"""Phi-4-multimodal-instruct ONNX model manager for local inference.

Manages the full lifecycle of a Phi-4 ONNX model: loading with
auto-detected GPU support, streaming token generation from audio input,
and clean unloading.  All synchronous ONNX calls run in an asyncio
executor to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np

logger = logging.getLogger("mcdonalds-drive-thru.phi4")
pipeline_logger = logging.getLogger("local-pipeline")

# ── Optional runtime imports (GPU variants tried in priority order) ─────────
_og: Any = None
_OG_PROVIDER: str = "none"


def _load_onnxruntime_genai() -> tuple[Any, str]:
    """Try importing onnxruntime-genai and detect best available provider.
    
    All onnxruntime-genai variants (CPU, CUDA, DirectML) install as the same
    Python module ``onnxruntime_genai``.  We detect the actual provider by
    checking for DirectML / CUDA support via onnxruntime's provider list.
    """
    try:
        mod = __import__("onnxruntime_genai")
    except ImportError:
        return None, "none"

    # Determine which execution provider is actually available
    provider = "cpu"
    try:
        import onnxruntime as _ort
        eps = _ort.get_available_providers()
        if "DmlExecutionProvider" in eps:
            provider = "directml"
        elif "CUDAExecutionProvider" in eps:
            provider = "cuda"
    except Exception:
        pass  # onnxruntime not importable or no provider info — assume CPU

    logger.info("Loaded onnxruntime_genai — provider: %s", provider)
    return mod, provider


class Phi4ModelManager:
    """Manages the Phi-4 ONNX model lifecycle — loading, inference, unloading."""

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        max_length: int = 256,
        temperature: float = 0.6,
    ) -> None:
        self._model_path = model_path
        self._requested_device = device
        self._max_length = max_length
        self._temperature = temperature

        self._model: Any = None
        self._processor: Any = None
        self._tokenizer: Any = None
        self._device_name: str = "none"
        self._loaded = False

    # ── Public properties ───────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def device_name(self) -> str:
        return self._device_name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def load(self) -> None:
        """Load the model into memory.  Auto-detects GPU (CUDA → DirectML → CPU)."""
        if self._loaded:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_load)

    def _sync_load(self) -> None:
        global _og, _OG_PROVIDER
        _og, _OG_PROVIDER = _load_onnxruntime_genai()
        if _og is None:
            raise RuntimeError(
                "onnxruntime_genai is not installed. "
                "Install with: pip install onnxruntime-genai"
            )

        device = self._requested_device
        if device == "auto":
            device = _OG_PROVIDER

        logger.info("Loading Phi-4 model from %s (device=%s)", self._model_path, device)
        pipeline_logger.info("Phi-4 ONNX model loading from %s (device=%s)...", self._model_path, device)
        self._model = _og.Model(self._model_path)

        # MultiModalProcessor may not be available for all model formats.
        # Fall back to Tokenizer-only mode (text inference without audio embeddings).
        try:
            self._processor = _og.MultiModalProcessor(self._model)
            self._tokenizer = self._processor.tokenizer if hasattr(self._processor, "tokenizer") else _og.Tokenizer(self._model)
            logger.info("MultiModalProcessor loaded (multimodal mode)")
        except Exception as proc_exc:
            logger.info("MultiModalProcessor unavailable (%s) — using text-only Tokenizer", proc_exc)
            self._processor = None
            self._tokenizer = _og.Tokenizer(self._model)

        self._device_name = device
        self._loaded = True
        logger.info("Phi-4 model loaded successfully (device=%s)", device)
        pipeline_logger.info("Phi-4 model loaded successfully (device=%s)", device)

    async def unload(self) -> None:
        """Unload the model from memory."""
        if not self._loaded:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_unload)

    def _sync_unload(self) -> None:
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._loaded = False
        self._device_name = "none"
        logger.info("Phi-4 model unloaded")

    # ── Inference ───────────────────────────────────────────────────────────

    async def process_audio(
        self,
        audio_pcm: bytes,
        system_prompt: str,
        tool_schemas: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Process audio input through Phi-4 multimodal and stream text tokens.

        Args:
            audio_pcm: Raw PCM audio bytes (16kHz, mono, int16)
            system_prompt: System message for the AI
            tool_schemas: Optional tool definitions for structured output

        Yields:
            Text tokens as they're generated
        """
        if not self._loaded:
            raise RuntimeError("Model not loaded — call load() first")

        prompt = self._build_prompt(system_prompt, tool_schemas)
        audio_array = self._pcm_bytes_to_numpy(audio_pcm)

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _run_inference() -> None:
            try:
                params = _og.GeneratorParams(self._model)
                params.set_search_options(
                    max_length=self._max_length,
                )
                if self._temperature > 0:
                    params.set_search_options(
                        max_length=self._max_length,
                        temperature=self._temperature,
                        do_sample=True,
                    )

                generator = _og.Generator(self._model, params)

                if self._processor is not None:
                    # Multimodal path: audio + text
                    inputs = self._processor(prompt, audios=audio_array)
                    if hasattr(inputs, "input_ids"):
                        generator.append_tokens(inputs.input_ids)
                    else:
                        generator.append_tokens(inputs)
                else:
                    # Text-only path: tokenize prompt
                    tokens = self._tokenizer.encode(prompt)
                    generator.append_tokens(tokens)
                while not generator.is_done():
                    generator.generate_next_token()
                    new_token_ids = generator.get_next_tokens()
                    token_text = self._tokenizer.decode(new_token_ids)
                    if token_text:
                        loop.call_soon_threadsafe(queue.put_nowait, token_text)
            except Exception as exc:
                logger.error("Phi-4 inference error: %s", exc)
                pipeline_logger.error("Phi-4 inference error: %s", exc)
                loop.call_soon_threadsafe(
                    queue.put_nowait, f"[inference error: {exc}]"
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.get_event_loop().run_in_executor(None, _run_inference)

        while True:
            token = await queue.get()
            if token is None:
                break
            yield token

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _pcm_bytes_to_numpy(pcm: bytes) -> "np.ndarray":
        """Convert raw PCM int16 bytes to float32 numpy array normalised to [-1, 1]."""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    @staticmethod
    def _build_prompt(
        system_prompt: str,
        tool_schemas: list[dict] | None = None,
    ) -> str:
        """Build the chat-style prompt string for Phi-4 multimodal.

        Includes tool schemas as a structured JSON block in the system
        message when tools are provided.
        """
        parts: list[str] = [system_prompt]

        if tool_schemas:
            tools_block = (
                "\n\nYou have access to the following tools. To call a tool, "
                "output a JSON block wrapped in <tool_call> tags:\n"
                "<tool_call>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_call>\n\n"
                "Available tools:\n"
                + json.dumps(tool_schemas, indent=2)
            )
            parts.append(tools_block)

        return "\n".join(parts)

    @staticmethod
    def parse_tool_calls(text: str) -> list[dict]:
        """Extract tool calls from model output.

        Looks for ``<tool_call>{"name": ..., "arguments": ...}</tool_call>``
        patterns in the generated text.

        Returns:
            List of dicts with ``name`` and ``arguments`` keys.
        """
        pattern = r"<tool_call>\s*(\{.*?\})\s*</tool_call>"
        matches = re.findall(pattern, text, re.DOTALL)
        calls: list[dict] = []
        for match in matches:
            try:
                parsed = json.loads(match)
                if "name" in parsed:
                    calls.append(parsed)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool call JSON: %s", match[:200])
        return calls
