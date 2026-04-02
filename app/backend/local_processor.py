"""Local Phi-4 ONNX processor for offline drive-thru mode.

Implements the full offline AI pipeline: incoming audio → Phi-4
multimodal inference → tool execution → Piper TTS → audio response.
Audio format contract: frontend sends/expects 24 kHz PCM int16 mono
base64.  Phi-4 expects 16 kHz — resampling is handled internally.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import aiohttp
from aiohttp import web

from config_loader import get_config
from processor_base import AbstractProcessor
from rtmt import Tool, ToolResult, ToolResultDirection

logger = logging.getLogger("mcdonalds-drive-thru.local")

# ── Optional dependency guards ──────────────────────────────────────────────

LOCAL_MODE_AVAILABLE = False

try:
    # Check for any onnxruntime_genai variant
    try:
        import onnxruntime_genai_cuda  # type: ignore
        LOCAL_MODE_AVAILABLE = True
    except ImportError:
        try:
            import onnxruntime_genai_directml  # type: ignore
            LOCAL_MODE_AVAILABLE = True
        except ImportError:
            try:
                import onnxruntime_genai  # type: ignore
                LOCAL_MODE_AVAILABLE = True
            except ImportError:
                pass

    if LOCAL_MODE_AVAILABLE:
        logger.info("onnxruntime_genai available — local Phi-4 inference enabled")
    else:
        logger.info("onnxruntime_genai not installed — local mode unavailable")
except Exception:
    logger.info("onnxruntime_genai not installed — local mode unavailable")

# Lazy imports for heavy modules (loaded only when actually needed)
_Phi4ModelManager = None
_PiperTTSEngine = None

_cfg = get_config()
_vad_cfg = _cfg.get("vad", {})

# ── WebSocket message protocol constants ────────────────────────────────────

_MSG_SESSION_CREATED = "session.created"
_MSG_SESSION_UPDATED = "session.updated"
_MSG_RESPONSE_CREATED = "response.created"
_MSG_RESPONSE_DONE = "response.done"
_MSG_AUDIO_DELTA = "response.audio.delta"
_MSG_TRANSCRIPT_DELTA = "response.audio_transcript.delta"
_MSG_TRANSCRIPT_DONE = "response.audio_transcript.done"
_MSG_TOOL_RESPONSE = "extension.middle_tier_tool_response"

# ── Audio constants ─────────────────────────────────────────────────────────

_FRONTEND_SAMPLE_RATE = 24000   # Frontend sends/expects 24 kHz
_PHI4_SAMPLE_RATE = 16000       # Phi-4 expects 16 kHz
_BYTES_PER_SAMPLE = 2           # int16


class LocalPhi4Processor(AbstractProcessor):
    """Offline processor using Phi-4 ONNX + Piper TTS.

    Parameters
    ----------
    config : dict
        ``local_mode`` section from config.yaml.  Keys:
        - ``model_path``          – ONNX model directory
        - ``device``              – ``auto | cuda | cpu | directml``
        - ``tts_default_voice``   – Default Piper voice name
        - ``tts_available_voices``– Allowed Piper voice list
        - ``tts_length_scale``    – Speech tempo (< 1.0 = faster)
        - ``tts_model_path``      – Piper model directory
        - ``max_length``          – max generation tokens
        - ``temperature``         – generation temperature
        - ``tts_sample_rate``     – output sample rate (default 24000)
        - ``lazy_load``           – defer model loading until first request
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self.tools: dict[str, Tool] = {}
        self.system_message: str | None = None
        self.temperature: float | None = self._config.get("temperature", 0.6)
        self.max_tokens: int | None = self._config.get("max_length", 256)
        self.voice_choice: str | None = self._config.get("tts_default_voice") or self._config.get("tts_model")

        self._model_path: str | None = self._config.get("model_path")
        self._device: str = self._config.get("device", "auto")
        self._lazy_load: bool = self._config.get("lazy_load", True)

        # VAD configuration
        self._silence_duration_ms: int = _vad_cfg.get("silence_duration_ms", 500)
        self._silence_threshold: float = _vad_cfg.get("threshold", 0.5)

        # Sub-components (created on load)
        self._model: Any = None  # Phi4ModelManager
        self._tts: Any = None    # PiperTTSEngine

        # Per-connection state is managed inside handle_websocket;
        # these are processor-level flags.
        self._model_loaded: bool = False
        self._loading: bool = False
        self._current_task: asyncio.Task | None = None

    # ── AbstractProcessor interface ─────────────────────────────────────────

    async def handle_websocket(
        self, ws: web.WebSocketResponse, request: web.Request
    ) -> None:
        """Handle a WebSocket connection in local/offline mode.

        Implements the full pipeline: audio accumulation → VAD silence
        detection → Phi-4 inference → tool execution → Piper TTS →
        audio deltas back to client.
        """
        session_id = f"local-{uuid.uuid4().hex[:8]}"
        logger.info("Local processor handling WebSocket (session=%s)", session_id)

        # Lazy-load models on first connection if not already loaded
        if not self._model_loaded and not self._loading:
            try:
                await self._ensure_models_loaded()
            except Exception as exc:
                logger.error("Failed to load local models: %s", exc)
                await ws.send_json({
                    "type": _MSG_SESSION_CREATED,
                    "session": {"id": session_id, "model": "phi-4-onnx", "voice": "unavailable"},
                })
                await self._send_text_response(
                    ws,
                    f"Local mode model loading failed: {exc}. Please switch to cloud mode.",
                )
                return

        # Send session.created
        tool_names = list(self.tools.keys())
        await ws.send_json({
            "type": _MSG_SESSION_CREATED,
            "session": {
                "id": session_id,
                "model": f"phi-4-onnx ({self._model.device_name})" if self._model else "phi-4-onnx",
                "voice": self.voice_choice or "local",
                "instructions": "",
                "tools": tool_names,
                "tool_choice": "auto" if tool_names else "none",
                "max_response_output_tokens": self.max_tokens,
            },
        })

        # Per-connection state
        audio_buffer = bytearray()
        silence_samples = 0
        is_speaking = False
        cancel_event = asyncio.Event()
        processing_lock = asyncio.Lock()

        # Samples of silence needed to trigger utterance end
        silence_sample_threshold = int(
            (_FRONTEND_SAMPLE_RATE * self._silence_duration_ms) / 1000
        )

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    message = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue

                msg_type = message.get("type", "")

                if msg_type == "input_audio_buffer.append":
                    audio_b64 = message.get("audio", "")
                    if not audio_b64:
                        continue
                    chunk = base64.b64decode(audio_b64)
                    audio_buffer.extend(chunk)

                    # Simple energy-based VAD
                    chunk_energy = _compute_energy(chunk)
                    if chunk_energy > self._silence_threshold * 1000:
                        is_speaking = True
                        silence_samples = 0
                    else:
                        silence_samples += len(chunk) // _BYTES_PER_SAMPLE

                    # Silence exceeded threshold after speech → process utterance
                    if (
                        is_speaking
                        and silence_samples >= silence_sample_threshold
                        and len(audio_buffer) > _FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE  # At least 1s of audio
                    ):
                        utterance = bytes(audio_buffer)
                        audio_buffer.clear()
                        silence_samples = 0
                        is_speaking = False
                        cancel_event.clear()

                        if not processing_lock.locked():
                            asyncio.ensure_future(
                                self._process_utterance_safe(
                                    utterance, ws, session_id, cancel_event, processing_lock
                                )
                            )

                elif msg_type == "input_audio_buffer.speech_started":
                    # Frontend-initiated barge-in
                    is_speaking = True
                    silence_samples = 0
                    cancel_event.set()

                elif msg_type == "input_audio_buffer.commit":
                    # Explicit commit — process whatever we have
                    if len(audio_buffer) > 0:
                        utterance = bytes(audio_buffer)
                        audio_buffer.clear()
                        silence_samples = 0
                        is_speaking = False
                        cancel_event.clear()
                        if not processing_lock.locked():
                            asyncio.ensure_future(
                                self._process_utterance_safe(
                                    utterance, ws, session_id, cancel_event, processing_lock
                                )
                            )

                elif msg_type == "session.update":
                    session_cfg = message.get("session", {})
                    if "instructions" in session_cfg:
                        self.system_message = session_cfg["instructions"]
                    await ws.send_json({
                        "type": _MSG_SESSION_UPDATED,
                        "session": session_cfg,
                    })

                elif msg_type == "response.cancel":
                    logger.debug("Cancel requested (session=%s)", session_id)
                    cancel_event.set()

                elif msg_type == "extension.set_voice":
                    new_voice = message.get("voice")
                    if new_voice:
                        self.voice_choice = new_voice
                        logger.info("Voice changed to %s", new_voice)

                elif msg_type == "extension.set_piper_voice":
                    voice_id = message.get("voice") or message.get("voice_id", "")
                    available = self._config.get("tts_available_voices", [])
                    if not available:
                        from piper_tts import PIPER_VOICES
                        available = list(PIPER_VOICES.keys())
                    if voice_id not in available:
                        logger.warning("Rejected voice switch to '%s' — not in allowed list", voice_id)
                        await ws.send_json({
                            "type": "extension.piper_voice_error",
                            "error": f"Voice '{voice_id}' not available",
                        })
                    elif self._tts:
                        success = await self._tts.set_voice(voice_id)
                        if success:
                            self.voice_choice = voice_id
                            await ws.send_json({
                                "type": "extension.piper_voice_changed",
                                "voice": voice_id,
                            })
                        else:
                            await ws.send_json({
                                "type": "extension.piper_voice_error",
                                "error": f"Failed to load voice '{voice_id}'",
                            })

                elif msg_type.startswith("extension."):
                    logger.debug("Extension message: %s", msg_type)

            elif msg.type in (
                aiohttp.WSMsgType.ERROR,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
            ):
                break

        cancel_event.set()
        logger.info("Local processor WebSocket closed (session=%s)", session_id)

    async def start_background_tasks(self) -> None:
        """Pre-load models if lazy_load is disabled."""
        if not self._lazy_load and self._model_path:
            asyncio.ensure_future(self._ensure_models_loaded())

    async def stop_background_tasks(self) -> None:
        """Unload models and free memory."""
        if self._model:
            await self._model.unload()
        if self._tts:
            await self._tts.unload()
        self._model_loaded = False
        logger.info("Local processor background tasks stopped")

    # ── Status helpers (used by /api/local-mode/status) ─────────────────────

    @property
    def available(self) -> bool:
        """Whether the ONNX runtime is installed and a model path is set."""
        return LOCAL_MODE_AVAILABLE and self._model_path is not None

    @property
    def device(self) -> str | None:
        """Accelerator device string, or None if unavailable."""
        if self._model and self._model.is_loaded:
            return self._model.device_name
        return self._device if self.available else None

    @property
    def model_loaded(self) -> bool:
        """Whether the model is loaded into memory and ready for inference."""
        return self._model_loaded

    # ── Model lifecycle ─────────────────────────────────────────────────────

    async def _ensure_models_loaded(self) -> None:
        """Load Phi-4 and Piper models if not already loaded."""
        if self._model_loaded or self._loading:
            return

        self._loading = True
        try:
            await self._load_phi4()
            await self._load_tts()
            self._model_loaded = True
            logger.info("All local models loaded successfully")
        finally:
            self._loading = False

    async def _load_phi4(self) -> None:
        """Initialize and load the Phi-4 model manager."""
        global _Phi4ModelManager
        if _Phi4ModelManager is None:
            from phi4_model import Phi4ModelManager as _Phi4ModelManager

        self._model = _Phi4ModelManager(
            model_path=self._model_path or "./models/phi4-multimodal",
            device=self._device,
            max_length=self.max_tokens or 256,
            temperature=self.temperature or 0.6,
        )
        await self._model.load()

    async def _load_tts(self) -> None:
        """Initialize and load the Piper TTS engine with multi-voice support."""
        global _PiperTTSEngine
        if _PiperTTSEngine is None:
            from piper_tts import PiperTTSEngine as _PiperTTSEngine

        tts_voice = self._config.get("tts_default_voice") or self._config.get("tts_model", "en_US-amy-medium")
        tts_path = self._config.get("tts_model_path", "./models/piper")
        tts_rate = self._config.get("tts_sample_rate", _FRONTEND_SAMPLE_RATE)
        tts_length_scale = self._config.get("tts_length_scale", 0.9)
        tts_available = self._config.get("tts_available_voices")

        self._tts = _PiperTTSEngine(
            default_voice=tts_voice,
            available_voices=tts_available,
            model_path=tts_path,
            sample_rate=tts_rate,
            length_scale=tts_length_scale,
        )
        try:
            await self._tts.load()
        except Exception as exc:
            logger.warning("Piper TTS failed to load: %s — text-only mode", exc)
            self._tts = None

    # ── Processing pipeline ─────────────────────────────────────────────────

    async def _process_utterance_safe(
        self,
        audio: bytes,
        ws: web.WebSocketResponse,
        session_id: str,
        cancel_event: asyncio.Event,
        lock: asyncio.Lock,
    ) -> None:
        """Wrapper with error handling and lock management."""
        async with lock:
            try:
                await self._process_utterance(audio, ws, session_id, cancel_event)
            except Exception as exc:
                logger.error("Utterance processing error: %s", exc, exc_info=True)
                await self._send_text_response(
                    ws,
                    "I'm sorry, I had trouble processing that. Could you repeat your order?",
                )

    async def _process_utterance(
        self,
        audio: bytes,
        ws: web.WebSocketResponse,
        session_id: str,
        cancel_event: asyncio.Event,
    ) -> None:
        """Full pipeline: audio → Phi-4 → tools → Piper TTS → audio deltas."""
        response_id = f"resp-{uuid.uuid4().hex[:8]}"

        # Downsample 24 kHz → 16 kHz for Phi-4
        audio_16k = _downsample_24k_to_16k(audio)

        # 1. Send response.created
        await ws.send_json({
            "type": _MSG_RESPONSE_CREATED,
            "response": {"id": response_id},
        })

        if not self._model or not self._model.is_loaded:
            await self._send_transcript_and_done(ws, response_id,
                "Local model is not loaded. Please wait for initialization to complete.")
            return

        # 2. Run Phi-4 inference, stream text tokens
        system_prompt = self.system_message or "You are a helpful McDonald's drive-thru assistant."
        tool_schemas = self._get_tool_schemas()

        full_text = ""
        async for token in self._model.process_audio(audio_16k, system_prompt, tool_schemas):
            if cancel_event.is_set():
                logger.info("Response cancelled (response=%s)", response_id)
                break
            full_text += token
            await ws.send_json({
                "type": _MSG_TRANSCRIPT_DELTA,
                "delta": token,
            })

        if cancel_event.is_set():
            await ws.send_json({
                "type": _MSG_RESPONSE_DONE,
                "response": {"id": response_id, "status": "cancelled"},
            })
            return

        # 3. Check for tool calls in the response
        from phi4_model import Phi4ModelManager
        tool_calls = Phi4ModelManager.parse_tool_calls(full_text)

        speech_text = full_text
        if tool_calls:
            tool_result_text = await self._execute_tool_calls(
                tool_calls, ws, session_id
            )
            if tool_result_text:
                speech_text = tool_result_text

        # Strip tool_call tags from speech text
        import re
        speech_text = re.sub(r"<tool_call>.*?</tool_call>", "", speech_text, flags=re.DOTALL).strip()

        # 4. Send transcript done
        await ws.send_json({
            "type": _MSG_TRANSCRIPT_DONE,
            "transcript": speech_text,
        })

        # 5. Synthesize speech from text (if TTS available)
        if self._tts and self._tts.is_loaded and speech_text:
            async for audio_chunk in self._tts.synthesize_streaming(speech_text):
                if cancel_event.is_set():
                    break
                await ws.send_json({
                    "type": _MSG_AUDIO_DELTA,
                    "delta": base64.b64encode(audio_chunk).decode(),
                })

        # 6. Send response.done
        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {
                "id": response_id,
                "output": [{"type": "message", "role": "assistant"}],
                "status": "completed",
            },
        })

    # ── Tool execution ──────────────────────────────────────────────────────

    def _get_tool_schemas(self) -> list[dict] | None:
        """Build tool schema list from registered tools."""
        if not self.tools:
            return None
        return [tool.schema for tool in self.tools.values() if tool.schema]

    async def _execute_tool_calls(
        self,
        tool_calls: list[dict],
        ws: web.WebSocketResponse,
        session_id: str,
    ) -> str | None:
        """Execute parsed tool calls and return result text for speech.

        Returns the client-facing text from tool results, or None if
        no results should modify the speech output.
        """
        client_texts: list[str] = []

        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments", {})

            if name not in self.tools:
                logger.warning("Unknown tool requested: %s", name)
                continue

            tool = self.tools[name]
            try:
                t0 = time.monotonic()
                # Order tools need session_id
                if name in ("update_order", "get_order", "reset_order"):
                    result: ToolResult = await tool.target(args, session_id)
                else:
                    result: ToolResult = await tool.target(args)

                elapsed = time.monotonic() - t0
                logger.info(
                    "Tool '%s' executed in %.1fms (direction=%s)",
                    name, elapsed * 1000, result.destination,
                )

                # Send result to client if direction allows
                if result.destination in (ToolResultDirection.TO_CLIENT, ToolResultDirection.TO_BOTH):
                    await ws.send_json({
                        "type": _MSG_TOOL_RESPONSE,
                        "tool_name": name,
                        "tool_result": result.to_client_text(),
                    })
                    client_texts.append(result.to_client_text())

            except Exception as exc:
                logger.error("Tool '%s' failed: %s", name, exc)

        return "\n".join(client_texts) if client_texts else None

    # ── Internal helpers ────────────────────────────────────────────────────

    async def _send_text_response(self, ws: web.WebSocketResponse, text: str) -> None:
        """Send a complete text-only response through the WebSocket.

        Mimics the message sequence the frontend expects from cloud mode:
        response.created → transcript delta(s) → response.done
        """
        response_id = f"local-{uuid.uuid4().hex[:8]}"
        await ws.send_json({"type": _MSG_RESPONSE_CREATED, "response": {"id": response_id}})
        await ws.send_json({"type": _MSG_TRANSCRIPT_DELTA, "delta": text})

        # Synthesize audio if TTS is available
        if self._tts and self._tts.is_loaded:
            async for audio_chunk in self._tts.synthesize_streaming(text):
                await ws.send_json({
                    "type": _MSG_AUDIO_DELTA,
                    "delta": base64.b64encode(audio_chunk).decode(),
                })

        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {
                "id": response_id,
                "output": [{"type": "message", "role": "assistant"}],
            },
        })

    async def _send_transcript_and_done(
        self, ws: web.WebSocketResponse, response_id: str, text: str
    ) -> None:
        """Send transcript delta + response.done for a simple text message."""
        await ws.send_json({"type": _MSG_TRANSCRIPT_DELTA, "delta": text})
        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {"id": response_id, "output": [{"type": "message", "role": "assistant"}]},
        })


# ── Module-level audio utilities ────────────────────────────────────────────


def _compute_energy(pcm_bytes: bytes) -> float:
    """Compute RMS energy of a PCM int16 audio chunk."""
    if len(pcm_bytes) < 2:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2)))


def _downsample_24k_to_16k(pcm_bytes: bytes) -> bytes:
    """Downsample PCM int16 audio from 24 kHz to 16 kHz.

    Uses linear interpolation — lightweight and sufficient for speech.
    """
    if len(pcm_bytes) < 4:
        return pcm_bytes

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    ratio = _PHI4_SAMPLE_RATE / _FRONTEND_SAMPLE_RATE  # 16000/24000 = 2/3
    new_length = int(len(samples) * ratio)

    if new_length == 0:
        return b""

    x_old = np.linspace(0, 1, len(samples), endpoint=False)
    x_new = np.linspace(0, 1, new_length, endpoint=False)
    resampled = np.interp(x_new, x_old, samples).astype(np.int16)

    return resampled.tobytes()
