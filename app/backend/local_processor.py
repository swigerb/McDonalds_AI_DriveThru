"""Local Phi-4-mini ONNX processor for offline drive-thru mode.

Implements the full offline AI pipeline: incoming audio → Whisper STT →
Phi-4-mini text inference → tool execution → Piper TTS → audio response.
Audio format contract: frontend sends/expects 24 kHz PCM int16 mono
base64.  Whisper and Phi-4-mini expect 16 kHz — resampling is handled internally.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
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
from audio_pipeline import (
    vlog as _vlog,
    vlogger,
    create_verbose_file_handler as _create_verbose_file_handler,
    remove_verbose_file_handler as _remove_verbose_file_handler,
    _VERBOSE_GLOBAL,
)

logger = logging.getLogger("mcdonalds-drive-thru.local")
pipeline_logger = logging.getLogger("local-pipeline")

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
_WhisperSTTEngine = None

_cfg = get_config()
_vad_cfg = _cfg.get("vad", {})

# ── WebSocket message protocol constants ────────────────────────────────────

_MSG_SESSION_CREATED = "session.created"
_MSG_SESSION_UPDATED = "session.updated"
_MSG_RESPONSE_CREATED = "response.created"
_MSG_RESPONSE_DONE = "response.done"
_MSG_AUDIO_DELTA = "response.audio.delta"
_MSG_AUDIO_DONE = "response.audio.done"
_MSG_TRANSCRIPT_DELTA = "response.audio_transcript.delta"
_MSG_TRANSCRIPT_DONE = "response.audio_transcript.done"
_MSG_TOOL_RESPONSE = "extension.middle_tier_tool_response"
_MSG_SESSION_METADATA = "extension.session_metadata"
_MSG_ROUND_TRIP_TOKEN = "extension.round_trip_token"

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
        self.max_tokens: int | None = self._config.get("max_length", 2048)
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
        self._stt: Any = None    # WhisperSTTEngine

        # Per-connection state is managed inside handle_websocket;
        # these are processor-level flags.
        self._model_loaded: bool = False
        self._loading: bool = False
        self._current_task: asyncio.Task | None = None
        self._generating: bool = False  # Half-duplex: True while Phi-4 is generating

        # Model load error tracking — surfaces via diagnostics and frontend
        self._model_load_error: str | None = None
        self._phi4_load_error: str | None = None
        self._tts_load_error: str | None = None
        self._stt_load_error: str | None = None

        # Conversation history: list of (role, text) tuples, max 3 turns
        self._conversation_history: list[tuple[str, str]] = []
        self._max_history_turns: int = 3

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
        pipeline_logger.info("[%s] Local processor handling WebSocket connection", session_id)
        pipeline_logger.info(
            "[%s] Pipeline status: model_loaded=%s, tts=%s, stt=%s",
            session_id,
            self._model_loaded,
            "loaded" if (self._tts and self._tts.is_loaded) else "not loaded",
            "loaded" if (self._stt and self._stt.is_loaded) else "not loaded",
        )

        # Lazy-load models on first connection if not already loaded
        model_load_failed = False
        model_load_error_detail = ""
        if not self._model_loaded and not self._loading:
            try:
                pipeline_logger.info("[%s] Lazy-loading local models (first connection)...", session_id)
                await self._ensure_models_loaded()
                pipeline_logger.info("[%s] Local models loaded successfully", session_id)
            except Exception as exc:
                model_load_error_detail = self._format_load_error_detail()
                pipeline_logger.error("[%s] Failed to load local models: %s", session_id, exc, exc_info=True)
                print(f"\n{'='*70}")
                print(f"  LOCAL MODEL LOAD FAILED — session {session_id}")
                print(f"  Error: {exc}")
                if self._phi4_load_error:
                    print(f"  Phi-4: {self._phi4_load_error}")
                if self._tts_load_error:
                    print(f"  Piper TTS: {self._tts_load_error}")
                if self._stt_load_error:
                    print(f"  Whisper STT: {self._stt_load_error}")
                print(f"{'='*70}\n")
                model_load_failed = True
                await ws.send_json({
                    "type": _MSG_SESSION_CREATED,
                    "session": {"id": session_id, "model": "phi-4-onnx", "voice": "unavailable"},
                })
                # Send detailed error as transcript so it appears in Guest Conversation panel
                await self._send_text_response(
                    ws,
                    f"⚠️ Local model loading failed.\n{model_load_error_detail}\nPlease switch to cloud mode in settings.",
                )
                # DON'T return — keep the WebSocket alive in degraded mode.
                # Returning here would close the socket, triggering auto-reconnect,
                # which creates a rapid connect→fail→close cycle that prevents
                # readyState from ever being OPEN when the user clicks the mic.

        # Send session.created (skip if already sent during model load failure)
        if not model_load_failed:
            tool_names = list(self.tools.keys())
            pipeline_logger.info(
                "[%s] Sending session.created (model=%s, voice=%s, tools=%s)",
                session_id,
                f"phi-4-onnx ({self._model.device_name})" if self._model else "phi-4-onnx",
                self.voice_choice or "local",
                tool_names,
            )
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
        greeting_sent = False
        audio_frame_count = 0

        # ── Session token tracking ──
        session_token = str(uuid.uuid4())
        session_state = {
            "round_trip_index": 0,
            "session_token": session_token,
        }

        # ── Verbose logging per-connection state ──
        verbose = _VERBOSE_GLOBAL
        session_file_handler: logging.FileHandler | None = None

        # Emit initial session token metadata (same format as cloud mode)
        await ws.send_json({
            "type": _MSG_SESSION_METADATA,
            "sessionToken": session_token,
            "roundTripIndex": 0,
            "roundTripToken": f"{session_token}-0000",
        })
        _vlog(verbose,
              "─── [SESSION TOKEN] ───\n"
              "Token: %s\n"
              "Round Trip: #%d (token: %s)\n"
              "───────────────────────",
              session_token, 0, f"{session_token}-0000")

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

                    # Half-duplex: drop incoming audio while Phi-4 is generating
                    if self._generating:
                        pipeline_logger.debug("[%s] Audio frame dropped (half-duplex: generating)", session_id)
                        continue

                    chunk = base64.b64decode(audio_b64)
                    audio_buffer.extend(chunk)
                    audio_frame_count += 1
                    if audio_frame_count % 50 == 0:
                        _vlog(verbose, "─── [Client → Server] Audio frame #%d ───", audio_frame_count)

                    # Simple energy-based VAD
                    chunk_energy = _compute_energy(chunk)
                    if chunk_energy > self._silence_threshold * 1000:
                        if not is_speaking:
                            pipeline_logger.info("[%s] VAD: speech detected (energy=%.1f)", session_id, chunk_energy)
                            _vlog(verbose, "─── [VAD] Speech detected (energy=%.1f, threshold=%.1f) ───",
                                  chunk_energy, self._silence_threshold * 1000)
                        is_speaking = True
                        silence_samples = 0
                    else:
                        silence_samples += len(chunk) // _BYTES_PER_SAMPLE

                    # Minimum utterance duration: 0.5s at 24kHz stereo = 24000 bytes
                    _MIN_UTTERANCE_BYTES = int(_FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE * 0.5)

                    # Silence exceeded threshold after speech → process utterance
                    if (
                        is_speaking
                        and silence_samples >= silence_sample_threshold
                        and len(audio_buffer) > _FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE  # At least 1s of audio
                    ):
                        duration_s = len(audio_buffer) / (_FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE)
                        pipeline_logger.info(
                            "[%s] VAD: silence detected after speech — processing %.1fs utterance (%d bytes)",
                            session_id, duration_s, len(audio_buffer),
                        )
                        _vlog(verbose,
                              "─── [VAD] Silence after speech — processing %.1fs utterance (%d bytes, %d frames) ───",
                              duration_s, len(audio_buffer), audio_frame_count)
                        utterance = bytes(audio_buffer)
                        audio_buffer.clear()
                        silence_samples = 0
                        is_speaking = False
                        cancel_event.clear()

                        # Skip very short utterances (< 0.5s) — likely noise
                        if len(utterance) < _MIN_UTTERANCE_BYTES:
                            utterance_dur = len(utterance) / (_FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE)
                            pipeline_logger.info(
                                "[%s] Utterance too short (%.2fs) — likely noise, skipping",
                                session_id, utterance_dur,
                            )
                            continue

                        if model_load_failed:
                            _vlog(verbose,
                                  "─── [DROPPED] Utterance dropped — model_load_failed=True ───\n"
                                  "  %s", model_load_error_detail or "Models not loaded")
                            pipeline_logger.warning(
                                "[%s] Utterance dropped (model_load_failed=True): %s",
                                session_id, model_load_error_detail or "Models not loaded",
                            )
                            await self._send_text_response(
                                ws,
                                "Local models are not loaded. Please switch to cloud mode in settings.",
                            )
                        elif not processing_lock.locked():
                            asyncio.ensure_future(
                                self._process_utterance_safe(
                                    utterance, ws, session_id, cancel_event, processing_lock,
                                    session_state, verbose,
                                )
                            )

                elif msg_type == "input_audio_buffer.speech_started":
                    # Frontend-initiated barge-in
                    is_speaking = True
                    silence_samples = 0
                    cancel_event.set()
                    _vlog(verbose, "─── [Client → Server] input_audio_buffer.speech_started (barge-in) ───")

                elif msg_type == "input_audio_buffer.commit":
                    # Explicit commit — process whatever we have
                    _vlog(verbose, "─── [Client → Server] input_audio_buffer.commit (%d bytes buffered) ───",
                          len(audio_buffer))
                    if len(audio_buffer) > 0:
                        utterance = bytes(audio_buffer)
                        audio_buffer.clear()
                        silence_samples = 0
                        is_speaking = False
                        cancel_event.clear()

                        # Skip very short committed utterances (< 0.5s) — likely noise
                        if len(utterance) < _MIN_UTTERANCE_BYTES:
                            utterance_dur = len(utterance) / (_FRONTEND_SAMPLE_RATE * _BYTES_PER_SAMPLE)
                            pipeline_logger.info(
                                "[%s] Committed utterance too short (%.2fs) — likely noise, skipping",
                                session_id, utterance_dur,
                            )
                        elif model_load_failed:
                            _vlog(verbose,
                                  "─── [DROPPED] Committed utterance dropped — model_load_failed=True ───\n"
                                  "  %s", model_load_error_detail or "Models not loaded")
                            pipeline_logger.warning(
                                "[%s] Committed utterance dropped (model_load_failed=True): %s",
                                session_id, model_load_error_detail or "Models not loaded",
                            )
                            await self._send_text_response(
                                ws,
                                "Local models are not loaded. Please switch to cloud mode in settings.",
                            )
                        elif not processing_lock.locked():
                            asyncio.ensure_future(
                                self._process_utterance_safe(
                                    utterance, ws, session_id, cancel_event, processing_lock,
                                    session_state, verbose,
                                )
                            )

                elif msg_type == "session.update":
                    session_cfg = message.get("session", {})
                    if "instructions" in session_cfg:
                        self.system_message = session_cfg["instructions"]
                    _vlog(verbose,
                          "─── [Client → Server] session.update ───\n"
                          "  Instructions: %d chars, tools configured",
                          len(session_cfg.get("instructions", "")))
                    await ws.send_json({
                        "type": _MSG_SESSION_UPDATED,
                        "session": session_cfg,
                    })
                    _vlog(verbose, "─── [Server → Client] session.updated ───")

                    # Generate greeting (mirrors cloud mode's greeting after session.update)
                    if not greeting_sent:
                        greeting_sent = True
                        greeting_text = "Welcome to McDonald's! What can I get started for you today?"
                        pipeline_logger.info("[%s] Generating greeting after session.update", session_id)
                        _vlog(verbose,
                              "─── [Lifecycle] Greeting trigger=session.update ───\n"
                              "  Text: %s", greeting_text)
                        self._generating = True
                        try:
                            await self._send_text_response(ws, greeting_text)
                        finally:
                            self._generating = False
                            pipeline_logger.debug("[%s] Greeting TTS complete — half-duplex unlocked", session_id)

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

                elif msg_type == "extension.set_verbose_logging":
                    verbose = bool(message.get("enabled", False))
                    if verbose and not _VERBOSE_GLOBAL:
                        vlogger.setLevel(logging.DEBUG)
                        if not vlogger.handlers:
                            _h = logging.StreamHandler()
                            _h.setFormatter(logging.Formatter("%(message)s"))
                            vlogger.addHandler(_h)
                    logger.info("Verbose logging %s for session %s",
                                "ENABLED" if verbose else "DISABLED", session_id)
                    _vlog(verbose,
                          "\n╔══════════════════════════════════════╗\n"
                          "║  VERBOSE LOGGING: %-8s           ║\n"
                          "╚══════════════════════════════════════╝",
                          "ENABLED" if verbose else "DISABLED")

                elif msg_type == "extension.set_log_to_file":
                    enabled = bool(message.get("enabled", False))
                    if enabled and session_file_handler is None:
                        vlogger.setLevel(logging.DEBUG)
                        if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in vlogger.handlers):
                            _h = logging.StreamHandler()
                            _h.setFormatter(logging.Formatter("%(message)s"))
                            vlogger.addHandler(_h)
                        session_file_handler = _create_verbose_file_handler()
                        vlogger.addHandler(session_file_handler)
                    elif not enabled and session_file_handler is not None:
                        _remove_verbose_file_handler(session_file_handler)
                        session_file_handler = None
                    logger.info("Verbose log-to-file %s for session %s",
                                "ENABLED" if enabled else "DISABLED", session_id)
                    _vlog(verbose or enabled,
                          "\n╔══════════════════════════════════════╗\n"
                          "║  LOG TO FILE: %-8s              ║\n"
                          "╚══════════════════════════════════════╝",
                          "ENABLED" if enabled else "DISABLED")

                elif msg_type.startswith("extension."):
                    logger.debug("Extension message: %s", msg_type)

            elif msg.type in (
                aiohttp.WSMsgType.ERROR,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
            ):
                break

        cancel_event.set()
        if session_file_handler is not None:
            _remove_verbose_file_handler(session_file_handler)
        pipeline_logger.info("[%s] Local processor WebSocket closed", session_id)

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
        if self._stt:
            await self._stt.unload()
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

    def _format_load_error_detail(self) -> str:
        """Build a human-readable summary of which models failed and why."""
        parts: list[str] = []
        if self._phi4_load_error:
            parts.append(f"• Phi-4 LLM: {self._phi4_load_error}")
        if self._tts_load_error:
            parts.append(f"• Piper TTS: {self._tts_load_error}")
        if self._stt_load_error:
            parts.append(f"• Whisper STT: {self._stt_load_error}")
        if self._model_load_error and not parts:
            parts.append(f"• General: {self._model_load_error}")
        return "\n".join(parts) if parts else "Unknown error"

    # ── Model lifecycle ─────────────────────────────────────────────────────

    async def _ensure_models_loaded(self) -> None:
        """Load Phi-4 and Piper models if not already loaded."""
        if self._model_loaded or self._loading:
            return

        self._loading = True
        self._model_load_error = None
        self._phi4_load_error = None
        self._tts_load_error = None
        self._stt_load_error = None
        try:
            pipeline_logger.info("Loading Phi-4 ONNX model...")
            print("[LOCAL] Loading Phi-4 ONNX model...")
            await self._load_phi4()
            pipeline_logger.info("Phi-4 model loaded (device=%s)", self._model.device_name if self._model else "unknown")
            print(f"[LOCAL] Phi-4 loaded (device={self._model.device_name if self._model else 'unknown'})")

            pipeline_logger.info("Loading Piper TTS engine...")
            print("[LOCAL] Loading Piper TTS engine...")
            await self._load_tts()
            tts_status = "loaded" if (self._tts and self._tts.is_loaded) else "unavailable"
            pipeline_logger.info("TTS status: %s", tts_status)
            print(f"[LOCAL] TTS status: {tts_status}")

            # Always load Whisper STT — Phi-4-mini is text-only and needs STT
            pipeline_logger.info("Loading Whisper-tiny STT engine...")
            print("[LOCAL] Loading Whisper-tiny STT engine...")
            await self._load_stt()
            stt_status = "loaded" if (self._stt and self._stt.is_loaded) else "unavailable"
            pipeline_logger.info("STT status: %s", stt_status)
            print(f"[LOCAL] STT status: {stt_status}")

            self._model_loaded = True
            pipeline_logger.info("All local models loaded successfully")
            print("[LOCAL] All models loaded successfully")
        except Exception as exc:
            self._model_load_error = str(exc)
            print(f"[LOCAL] MODEL LOAD FAILED: {exc}")
            raise
        finally:
            self._loading = False

    async def _load_phi4(self) -> None:
        """Initialize and load the Phi-4 model manager."""
        global _Phi4ModelManager
        try:
            if _Phi4ModelManager is None:
                from phi4_model import Phi4ModelManager as _Phi4ModelManager
        except ImportError as exc:
            err = f"phi4_model module not importable: {exc}"
            self._phi4_load_error = err
            pipeline_logger.error("Phi-4 LOAD FAILED: %s", err)
            print(f"[LOCAL] Phi-4 LOAD FAILED: {err}")
            raise RuntimeError(err) from exc

        try:
            self._model = _Phi4ModelManager(
                model_path=self._model_path or "./models/phi4-mini/gpu/gpu-int4-rtn-block-32",
                device=self._device,
                max_length=self.max_tokens or 2048,
                temperature=self.temperature or 0.6,
            )
            await self._model.load()
        except Exception as exc:
            err = f"Phi-4 model failed to load: {exc}"
            self._phi4_load_error = err
            pipeline_logger.error("Phi-4 LOAD FAILED: %s", err)
            print(f"[LOCAL] Phi-4 LOAD FAILED: {err}")
            raise

    async def _load_tts(self) -> None:
        """Initialize and load the Piper TTS engine with multi-voice support."""
        global _PiperTTSEngine
        try:
            if _PiperTTSEngine is None:
                from piper_tts import PiperTTSEngine as _PiperTTSEngine
        except ImportError as exc:
            err = f"piper_tts module not importable: {exc}"
            self._tts_load_error = err
            pipeline_logger.warning("Piper TTS LOAD FAILED: %s — text-only mode", err)
            print(f"[LOCAL] Piper TTS LOAD FAILED: {err}")
            return  # TTS is optional — don't crash the pipeline

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
            err = f"Piper TTS failed to load: {exc}"
            self._tts_load_error = err
            pipeline_logger.warning("Piper TTS LOAD FAILED: %s — text-only mode", err)
            print(f"[LOCAL] Piper TTS LOAD FAILED: {err}")
            self._tts = None

    async def _load_stt(self) -> None:
        """Initialize and load the Faster-Whisper STT engine."""
        global _WhisperSTTEngine
        if _WhisperSTTEngine is None:
            try:
                from whisper_stt import WhisperSTTEngine as _WhisperSTTEngine, WHISPER_AVAILABLE
            except ImportError as exc:
                err = f"whisper_stt module not importable: {exc}"
                self._stt_load_error = err
                pipeline_logger.warning("Whisper STT LOAD FAILED: %s", err)
                print(f"[LOCAL] Whisper STT LOAD FAILED: {err}")
                return

            if not WHISPER_AVAILABLE:
                err = "faster-whisper package not installed (pip install faster-whisper)"
                self._stt_load_error = err
                pipeline_logger.warning("Whisper STT unavailable: %s", err)
                print(f"[LOCAL] Whisper STT unavailable: {err}")
                return

        stt_model = self._config.get("stt_model", "small")
        stt_device = self._config.get("stt_device", "auto")
        stt_compute = self._config.get("stt_compute_type", "int8")

        self._stt = _WhisperSTTEngine(
            model_size=stt_model,
            device=stt_device,
            compute_type=stt_compute,
        )
        try:
            await self._stt.load()
        except Exception as exc:
            err = f"Faster-Whisper failed to load: {exc}"
            self._stt_load_error = err
            pipeline_logger.warning("Whisper STT LOAD FAILED: %s — customer transcription unavailable", err)
            print(f"[LOCAL] Whisper STT LOAD FAILED: {err}")
            self._stt = None

    # ── Processing pipeline ─────────────────────────────────────────────────

    async def _process_utterance_safe(
        self,
        audio: bytes,
        ws: web.WebSocketResponse,
        session_id: str,
        cancel_event: asyncio.Event,
        lock: asyncio.Lock,
        session_state: dict[str, Any] | None = None,
        verbose: bool = False,
    ) -> None:
        """Wrapper with error handling and lock management."""
        if session_state is None:
            session_state = {"round_trip_index": 0, "session_token": str(uuid.uuid4())}
        async with lock:
            try:
                await self._process_utterance(audio, ws, session_id, cancel_event, session_state, verbose)
            except Exception as exc:
                pipeline_logger.error("[%s] Utterance processing error: %s", session_id, exc, exc_info=True)
                _vlog(verbose,
                      "─── [ERROR] _process_utterance_safe CRASHED ───\n"
                      "  Session: %s\n"
                      "  Error: %s\n"
                      "  Type: %s\n"
                      "───────────────────────────────────────────────",
                      session_id, exc, type(exc).__name__)
                print(f"[LOCAL PROCESSOR ERROR] session={session_id}: {type(exc).__name__}: {exc}")
                try:
                    if not ws.closed:
                        await ws.send_json({
                            "type": "error",
                            "error": {"message": f"Processing error: {exc}"},
                        })
                        await self._send_text_response(
                            ws,
                            "I'm sorry, I had trouble processing that. Could you repeat your order?",
                        )
                except Exception as send_exc:
                    pipeline_logger.error("[%s] Failed to send error response to client: %s", session_id, send_exc)
                    _vlog(verbose, "─── [ERROR] Failed to send error to client: %s ───", send_exc)

    async def _process_utterance(
        self,
        audio: bytes,
        ws: web.WebSocketResponse,
        session_id: str,
        cancel_event: asyncio.Event,
        session_state: dict[str, Any] | None = None,
        verbose: bool = False,
    ) -> None:
        """Full pipeline: audio → Phi-4 → tools → Piper TTS → audio deltas."""
        if session_state is None:
            session_state = {"round_trip_index": 0, "session_token": str(uuid.uuid4())}
        response_id = f"resp-{uuid.uuid4().hex[:8]}"
        pipeline_logger.info("[%s] Processing utterance (response=%s, %d bytes audio)", session_id, response_id, len(audio))
        _vlog(verbose, "─── [UTTERANCE] session=%s response=%s audio=%d bytes ───",
              session_id, response_id, len(audio))

        # Downsample 24 kHz → 16 kHz for Whisper STT and Phi-4-mini
        audio_16k = _downsample_24k_to_16k(audio)
        pipeline_logger.debug("[%s] Downsampled 24kHz→16kHz: %d→%d bytes", session_id, len(audio), len(audio_16k))
        _vlog(verbose, "[%s] Audio received: %d bytes (24kHz) → %d bytes (16kHz)", session_id, len(audio), len(audio_16k))

        # 1. STT: always run Whisper first, then pass text to Phi-4-mini
        customer_text: str | None = None
        if self._stt:
            pipeline_logger.info("[%s] Starting Whisper STT transcription", session_id)
            _vlog(verbose, "[%s] Whisper STT: starting transcription", session_id)
            try:
                customer_text = await self._stt.transcribe(audio_16k)
                if customer_text:
                    pipeline_logger.info("[%s] Whisper STT transcription: '%s'", session_id, customer_text[:100])
                    _vlog(verbose, "[%s] Whisper STT result: '%s'", session_id, customer_text[:100])
                    await ws.send_json({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": customer_text,
                    })
                else:
                    pipeline_logger.debug("[%s] Whisper STT returned empty transcription", session_id)
            except Exception as exc:
                pipeline_logger.warning("[%s] Customer transcription failed: %s", session_id, exc)
        else:
            pipeline_logger.warning("[%s] No STT engine loaded — cannot transcribe audio", session_id)

        # 2. Guard: skip inference if transcription is empty
        if not customer_text:
            pipeline_logger.warning("[%s] Whisper returned empty transcription — asking customer to repeat", session_id)
            await self._send_text_response(
                ws,
                "I didn't catch that, could you say that again?",
            )
            return

        # 3. Send response.created
        await ws.send_json({
            "type": _MSG_RESPONSE_CREATED,
            "response": {"id": response_id},
        })

        if not self._model or not self._model.is_loaded:
            pipeline_logger.error("[%s] Model not loaded — cannot process utterance", session_id)
            await self._send_transcript_and_done(ws, response_id,
                "Local model is not loaded. Please wait for initialization to complete.")
            return

        # 3. Run Phi-4-mini inference — half-duplex: mute audio input during generation
        system_prompt = self._get_local_system_prompt()
        tool_schemas = None

        pipeline_logger.info("[%s] Starting Phi-4-mini inference [text-only] (user_message=%s)...",
                             session_id,
                             repr(customer_text[:80]) if customer_text else "None")
        _vlog(verbose, "[%s] Phi-4-mini inference: START [text-only] (system_prompt=%d chars, user_message=%s, history=%d turns)",
              session_id, len(system_prompt),
              repr(customer_text[:80]) if customer_text else "None",
              len(self._conversation_history))
        t0 = time.monotonic()
        full_text = ""
        token_count = 0

        self._generating = True  # Half-duplex: mute VAD while generating
        try:
            async for token in self._model.process_audio(
                audio_16k, system_prompt, tool_schemas,
                user_message=customer_text,
                conversation_history=self._conversation_history[-self._max_history_turns * 2:] if self._conversation_history else None,
            ):
                if cancel_event.is_set():
                    pipeline_logger.info("[%s] Response cancelled (response=%s)", session_id, response_id)
                    break
                full_text += token
                token_count += 1
                await ws.send_json({
                    "type": _MSG_TRANSCRIPT_DELTA,
                    "delta": token,
                })
        finally:
            self._generating = False  # Half-duplex: re-enable VAD

        inference_ms = (time.monotonic() - t0) * 1000
        pipeline_logger.info("[%s] Phi-4-mini inference completed in %.0fms (%d chars)", session_id, inference_ms, len(full_text))
        _vlog(verbose, "[%s] Phi-4-mini inference: DONE in %.0fms — %d tokens, %d chars",
              session_id, inference_ms, token_count, len(full_text))

        if cancel_event.is_set():
            await ws.send_json({
                "type": _MSG_RESPONSE_DONE,
                "response": {"id": response_id, "status": "cancelled"},
            })
            return

        # 4. Check for tool calls in the response
        from phi4_model import Phi4ModelManager
        tool_calls = Phi4ModelManager.parse_tool_calls(full_text)

        speech_text = full_text
        if tool_calls:
            pipeline_logger.info(
                "[%s] Tool calls detected: %s", session_id, [c.get("name") for c in tool_calls]
            )
            _vlog(verbose, "[%s] Tool execution: %s", session_id, [c.get("name") for c in tool_calls])
            tool_result_text = await self._execute_tool_calls(
                tool_calls, ws, session_id
            )
            if tool_result_text:
                speech_text = tool_result_text

        # Strip tool_call tags from speech text
        speech_text = re.sub(r"<tool_call>.*?</tool_call>", "", speech_text, flags=re.DOTALL).strip()

        # 5. Send transcript done
        await ws.send_json({
            "type": _MSG_TRANSCRIPT_DONE,
            "transcript": speech_text,
        })

        # 6. Synthesize speech from text (if TTS available)
        if self._tts and self._tts.is_loaded and speech_text:
            pipeline_logger.info("[%s] Starting Piper TTS synthesis (%d chars)", session_id, len(speech_text))
            _vlog(verbose, "[%s] Piper TTS: START (%d chars)", session_id, len(speech_text))
            tts_t0 = time.monotonic()
            chunk_count = 0
            async for audio_chunk in self._tts.synthesize_streaming(speech_text):
                if cancel_event.is_set():
                    pipeline_logger.info("[%s] TTS synthesis cancelled", session_id)
                    break
                chunk_count += 1
                await ws.send_json({
                    "type": _MSG_AUDIO_DELTA,
                    "delta": base64.b64encode(audio_chunk).decode(),
                })
            tts_ms = (time.monotonic() - tts_t0) * 1000
            pipeline_logger.info("[%s] Piper TTS completed in %.0fms (%d chunks)", session_id, tts_ms, chunk_count)
            _vlog(verbose, "[%s] Piper TTS: DONE in %.0fms — %d audio chunks", session_id, tts_ms, chunk_count)
            # Signal audio stream complete (mirrors OpenAI Realtime API event
            # forwarded by rtmt.py — frontend may rely on this for playback state)
            await ws.send_json({"type": _MSG_AUDIO_DONE})
        elif speech_text:
            pipeline_logger.warning("[%s] TTS unavailable — text-only response", session_id)

        # 7. Send response.done
        pipeline_logger.info("[%s] Response complete (response=%s)", session_id, response_id)
        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {
                "id": response_id,
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "transcript": speech_text}],
                }],
                "status": "completed",
                "usage": {
                    "total_tokens": token_count,
                    "output_tokens": token_count,
                },
            },
        })

        # 8. Store conversation history for multi-turn context
        if customer_text:
            self._conversation_history.append(("user", customer_text))
        if speech_text:
            self._conversation_history.append(("assistant", speech_text))
        # Keep only the last N turns (each turn = user + assistant = 2 entries)
        max_entries = self._max_history_turns * 2
        if len(self._conversation_history) > max_entries:
            self._conversation_history = self._conversation_history[-max_entries:]

        # 9. Advance round trip and emit token to frontend
        session_state["round_trip_index"] += 1
        rt_idx = session_state["round_trip_index"]
        st = session_state["session_token"]
        rt_token = f"{st}-{rt_idx:04d}"
        await ws.send_json({
            "type": _MSG_ROUND_TRIP_TOKEN,
            "sessionToken": st,
            "roundTripIndex": rt_idx,
            "roundTripToken": rt_token,
        })
        _vlog(verbose,
              "─── [ROUND TRIP] #%d ───\n"
              "Token: %s\n"
              "Inference: %.0fms, %d tokens\n"
              "────────────────────────",
              rt_idx, rt_token, inference_ms, token_count)

    # ── Tool execution ──────────────────────────────────────────────────────

    def _get_tool_schemas(self) -> list[dict] | None:
        """Build tool schema list from registered tools."""
        if not self.tools:
            return None
        return [tool.schema for tool in self.tools.values() if tool.schema]

    def _get_local_system_prompt(self) -> str:
        """Return the short local-mode system prompt for INT4 inference.

        Tries prompt_loader's local prompt first, then falls back to
        the session system_message, then a minimal hardcoded default.
        """
        try:
            from prompt_loader import PromptLoader
            loader = PromptLoader(brand="mcdonalds")
            prompt = loader.get_local_system_prompt()
            if prompt:
                return prompt
        except Exception as exc:
            logger.debug("Could not load local system prompt via PromptLoader: %s", exc)

        if self.system_message:
            # Truncate full cloud prompt if it's too long for local inference
            if len(self.system_message) > 2000:
                logger.warning(
                    "system_message is %d chars — too large for local INT4; using fallback",
                    len(self.system_message),
                )
                return (
                    "You are a friendly McDonald's drive-thru crew member. "
                    "Take orders quickly, confirm each item, and suggest meals "
                    "when someone orders just a sandwich. Keep responses to one "
                    "or two short sentences. Be warm and upbeat."
                )
            return self.system_message

        return (
            "You are a friendly McDonald's drive-thru crew member. "
            "Take orders quickly, confirm each item, and suggest meals "
            "when someone orders just a sandwich. Keep responses to one "
            "or two short sentences. Be warm and upbeat."
        )

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
                pipeline_logger.info(
                    "[%s] Tool '%s' executed in %.1fms (direction=%s)",
                    session_id, name, elapsed * 1000, result.destination,
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
        response.created → transcript delta(s) → audio deltas → audio.done → response.done
        """
        response_id = f"local-{uuid.uuid4().hex[:8]}"
        await ws.send_json({"type": _MSG_RESPONSE_CREATED, "response": {"id": response_id}})
        await ws.send_json({"type": _MSG_TRANSCRIPT_DELTA, "delta": text})
        await ws.send_json({"type": _MSG_TRANSCRIPT_DONE, "transcript": text})

        # Synthesize audio if TTS is available
        if self._tts and self._tts.is_loaded:
            async for audio_chunk in self._tts.synthesize_streaming(text):
                await ws.send_json({
                    "type": _MSG_AUDIO_DELTA,
                    "delta": base64.b64encode(audio_chunk).decode(),
                })
            await ws.send_json({"type": _MSG_AUDIO_DONE})

        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {
                "id": response_id,
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "transcript": text}],
                }],
            },
        })

    async def _send_transcript_and_done(
        self, ws: web.WebSocketResponse, response_id: str, text: str
    ) -> None:
        """Send transcript delta + response.done for a simple text message."""
        await ws.send_json({"type": _MSG_TRANSCRIPT_DELTA, "delta": text})
        await ws.send_json({
            "type": _MSG_RESPONSE_DONE,
            "response": {
                "id": response_id,
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "transcript": text}],
                }],
            },
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
