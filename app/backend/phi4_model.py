"""Phi-4-mini-instruct ONNX model manager for local inference.

Manages the full lifecycle of a Phi-4-mini ONNX model: loading with
auto-detected GPU support, streaming token generation from text input,
and clean unloading.  All synchronous ONNX calls run in an asyncio
executor to avoid blocking the event loop.

Text-only model: pre-transcribed text (from Whisper STT) is tokenized
and fed to the model for response generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any

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
    """Manages the Phi-4-mini ONNX model lifecycle — loading, inference, unloading."""

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        max_length: int = 2048,
        temperature: float = 0.6,
    ) -> None:
        self._model_path = model_path
        self._requested_device = device
        self._max_length = max_length
        self._temperature = temperature

        self._model: Any = None
        self._processor: Any = None
        self._tokenizer: Any = None
        self._tokenizer_stream: Any = None
        self._device_name: str = "none"
        self._loaded = False

    # ── Public properties ───────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def multimodal_available(self) -> bool:
        """Always False — Phi-4-mini is text-only."""
        return False

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

        logger.info("Loading Phi-4-mini model from %s (device=%s)", self._model_path, device)
        pipeline_logger.info("Phi-4-mini ONNX model loading from %s (device=%s)...", self._model_path, device)
        self._model = _og.Model(self._model_path)

        # Phi-4-mini is text-only — no multimodal processor needed
        self._processor = None
        self._tokenizer = _og.Tokenizer(self._model)
        self._tokenizer_stream = self._tokenizer.create_stream()

        self._device_name = device
        self._loaded = True
        logger.info("Phi-4-mini model loaded successfully (device=%s)", device)
        pipeline_logger.info("Phi-4-mini model loaded successfully (device=%s)", device)

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
        self._tokenizer_stream = None
        self._loaded = False
        self._device_name = "none"
        logger.info("Phi-4-mini model unloaded")

    # ── Inference ───────────────────────────────────────────────────────────

    async def process_audio(
        self,
        audio_pcm: bytes,
        system_prompt: str,
        tool_schemas: list[dict] | None = None,
        timeout: float = 30.0,
        user_message: str | None = None,
        conversation_history: list[tuple[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Process text input through Phi-4-mini and stream text tokens.

        Args:
            audio_pcm: Raw PCM audio bytes (kept for API compatibility, not used)
            system_prompt: System message for the AI
            tool_schemas: Optional tool definitions for structured output
            timeout: Maximum seconds to wait for inference (default 30s).
                     If exceeded, yields a fallback message instead of hanging.
            user_message: Transcribed user text from Whisper STT
            conversation_history: Previous (role, text) turns for context

        Yields:
            Text tokens as they're generated
        """
        if not self._loaded:
            raise RuntimeError("Model not loaded — call load() first")

        prompt = self._build_prompt(system_prompt, tool_schemas, user_message, conversation_history)

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

                # Text-only path: tokenize prompt and stream decode
                tokens = self._tokenizer.encode(prompt)
                generator.append_tokens(tokens)
                stream = self._tokenizer_stream
                while not generator.is_done():
                    generator.generate_next_token()
                    new_token = generator.get_next_tokens()
                    token_text = stream.decode(new_token[0])
                    if token_text:
                        loop.call_soon_threadsafe(queue.put_nowait, token_text)
            except Exception as exc:
                logger.error("Phi-4-mini inference error: %s", exc)
                pipeline_logger.error("Phi-4-mini inference error: %s", exc)
                loop.call_soon_threadsafe(
                    queue.put_nowait, f"[inference error: {exc}]"
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        asyncio.get_event_loop().run_in_executor(None, _run_inference)

        try:
            first_token = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                "Phi-4 inference timed out after %.0fs (prompt=%d chars). "
                "System prompt may be too large for local INT4 model.",
                timeout, len(prompt),
            )
            pipeline_logger.error(
                "Phi-4 inference TIMEOUT after %.0fs — prompt was %d chars",
                timeout, len(prompt),
            )
            yield "I'm sorry, could you repeat that?"
            return

        # First token arrived — stream the rest without a global timeout
        if first_token is not None:
            yield first_token

        while True:
            token = await queue.get()
            if token is None:
                break
            yield token

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(
        system_prompt: str,
        tool_schemas: list[dict] | None = None,
        user_message: str | None = None,
        conversation_history: list[tuple[str, str]] | None = None,
    ) -> str:
        """Build the chat-style prompt string for Phi-4.

        Produces a proper chat template with system, history, and user turns.
        Includes tool schemas as a structured JSON block in the system
        message when tools are provided.
        """
        system_content = system_prompt

        if tool_schemas:
            system_content += (
                "\n\nYou have access to the following tools. To call a tool, "
                "output a JSON block wrapped in <tool_call> tags:\n"
                "<tool_call>{\"name\": \"tool_name\", \"arguments\": {...}}</tool_call>\n\n"
                "Available tools:\n"
                + json.dumps(tool_schemas, indent=2)
            )

        parts: list[str] = [f"<|system|>\n{system_content}\n<|end|>"]

        # Append conversation history (previous turns for context)
        if conversation_history:
            for role, text in conversation_history:
                tag = "user" if role == "user" else "assistant"
                parts.append(f"<|{tag}|>\n{text}\n<|end|>")

        # Append current user message
        if user_message:
            parts.append(f"<|user|>\n{user_message}\n<|end|>")

        parts.append("<|assistant|>")

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
