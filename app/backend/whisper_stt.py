"""Faster-Whisper STT engine for local speech-to-text transcription.

Transcribes customer audio in offline mode so the Guest Conversation
panel shows what the customer said (not just what the AI responded).
All synchronous CTranslate2 calls run in an asyncio executor so the
event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

logger = logging.getLogger("mcdonalds-drive-thru.stt")
pipeline_logger = logging.getLogger("local-pipeline")

# ── Optional dependency guard ───────────────────────────────────────────────
_WhisperModel: Any = None
WHISPER_AVAILABLE = False

try:
    from faster_whisper import WhisperModel as _WhisperModel  # type: ignore[no-redef]
    WHISPER_AVAILABLE = True
    logger.info("Faster-Whisper available")
except ImportError:
    logger.info("Faster-Whisper not installed — local STT unavailable")

# Minimum audio duration in seconds to avoid Whisper hallucination on silence
_MIN_AUDIO_SECONDS = 0.5
_EXPECTED_SAMPLE_RATE = 16000  # Whisper expects 16 kHz


class WhisperSTTEngine:
    """Local speech-to-text using Faster-Whisper (CTranslate2).

    Auto-detects CUDA for GPU acceleration; falls back to CPU.
    The model is lazy-loaded on first ``transcribe()`` call or via
    explicit ``load()``.

    Parameters
    ----------
    model_size : str
        Whisper model size (``tiny``, ``base``, ``small``, ``medium``,
        ``large-v3``).  Default ``small`` (244 MB) balances accuracy
        and speed for drive-thru use.
    device : str
        ``auto``, ``cuda``, or ``cpu``.  ``auto`` tries CUDA first.
    compute_type : str
        CTranslate2 quantization (``int8``, ``float16``, ``float32``).
        ``int8`` is fastest on CPU; ``float16`` preferred on CUDA.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._requested_device = device
        self._compute_type = compute_type

        self._model: Any = None
        self._device_name: str = "none"
        self._loaded = False

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def device_name(self) -> str:
        return self._device_name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def load(self) -> None:
        """Load the Whisper model.  Auto-detects GPU (CUDA → CPU)."""
        if self._loaded:
            return
        if not WHISPER_AVAILABLE:
            raise RuntimeError(
                "Faster-Whisper is not installed. "
                "Install with: pip install faster-whisper"
            )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_load)

    def _sync_load(self) -> None:
        device = self._requested_device
        compute_type = self._compute_type

        if device == "auto":
            device, compute_type = self._detect_device(compute_type)

        logger.info(
            "Loading Faster-Whisper model=%s device=%s compute_type=%s",
            self._model_size, device, compute_type,
        )
        self._model = _WhisperModel(
            self._model_size,
            device=device,
            compute_type=compute_type,
        )
        self._device_name = device
        self._loaded = True
        logger.info(
            "Faster-Whisper loaded: model=%s, device=%s",
            self._model_size, device,
        )
        pipeline_logger.info(
            "Whisper STT loaded: model=%s, device=%s",
            self._model_size, device,
        )

    @staticmethod
    def _detect_device(fallback_compute: str) -> tuple[str, str]:
        """Try CUDA first, fall back to CPU."""
        try:
            import torch
            if torch.cuda.is_available():
                logger.info("CUDA detected — using GPU for Whisper")
                return "cuda", "float16"
        except ImportError:
            pass

        # CTranslate2 can also probe CUDA without torch
        try:
            import ctranslate2
            if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
                logger.info("CUDA detected via CTranslate2 — using GPU for Whisper")
                return "cuda", "float16"
        except Exception:
            pass

        logger.info("No CUDA available — using CPU for Whisper")
        return "cpu", fallback_compute

    async def unload(self) -> None:
        """Unload the model from memory."""
        if not self._loaded:
            return
        self._model = None
        self._loaded = False
        self._device_name = "none"
        logger.info("Faster-Whisper model unloaded")

    # ── Transcription ───────────────────────────────────────────────────────

    async def transcribe(self, audio_pcm: bytes) -> str:
        """Transcribe audio to text.

        Args:
            audio_pcm: Raw PCM audio bytes (16 kHz, mono, int16)

        Returns:
            Transcribed text string, or empty string on failure /
            insufficient audio.
        """
        # Check minimum audio length to avoid hallucination on silence
        num_samples = len(audio_pcm) // 2  # int16 = 2 bytes per sample
        duration_s = num_samples / _EXPECTED_SAMPLE_RATE
        if duration_s < _MIN_AUDIO_SECONDS:
            logger.debug(
                "Audio too short (%.2fs < %.2fs) — skipping transcription",
                duration_s, _MIN_AUDIO_SECONDS,
            )
            return ""

        # Lazy-load on first transcription call
        if not self._loaded:
            await self.load()

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._sync_transcribe, audio_pcm)
        except Exception as exc:
            logger.warning("Whisper transcription failed: %s", exc)
            pipeline_logger.warning("Whisper STT transcription failed: %s", exc)
            return ""

    def _sync_transcribe(self, audio_pcm: bytes) -> str:
        """Synchronous CTranslate2 transcription."""
        # Convert PCM int16 bytes → float32 numpy array normalized to [-1, 1]
        audio_array = (
            np.frombuffer(audio_pcm, dtype=np.int16)
            .astype(np.float32) / 32768.0
        )

        segments, _info = self._model.transcribe(
            audio_array,
            language="en",
            beam_size=3,
            vad_filter=True,
        )

        # Collect all segment texts
        parts: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                parts.append(text)

        return " ".join(parts).strip()
