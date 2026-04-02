"""Piper TTS engine for local text-to-speech synthesis.

Wraps the Piper ONNX voice library to produce PCM audio from text.
All synchronous Piper calls run in an asyncio executor so the event
loop is never blocked.  Output is resampled to 24 kHz to match the
frontend audio player expectation.

Supports multiple voice models with lazy loading — only one voice is
held in memory at a time.  Switching voices unloads the previous model
before loading the new one.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("mcdonalds-drive-thru.tts")
pipeline_logger = logging.getLogger("local-pipeline")

# ── Optional dependency guard ───────────────────────────────────────────────
_PiperVoice: Any = None
_PIPER_AVAILABLE = False

try:
    from piper.voice import PiperVoice as _PiperVoice  # type: ignore[no-redef]
    _PIPER_AVAILABLE = True
    logger.info("Piper TTS available")
except ImportError:
    logger.info("Piper TTS not installed — local TTS unavailable")

# Sentence boundary regex for chunked synthesis
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# ── Voice metadata ──────────────────────────────────────────────────────────

PIPER_VOICES: dict[str, dict[str, str]] = {
    "en_US-amy-medium": {"name": "Amy", "accent": "US", "personality": "Friendly & Conversational"},
    "en_GB-jenny_dioco-medium": {"name": "Jenny", "accent": "UK", "personality": "Expressive & Upbeat"},
    "en_US-lessac-medium": {"name": "Lessac", "accent": "US", "personality": "Warm & Professional"},
    "en_US-kristin-medium": {"name": "Kristin", "accent": "US", "personality": "Neutral & Clear"},
}


class PiperTTSEngine:
    """Local text-to-speech using Piper ONNX voices.

    Supports multiple voices with lazy loading — only the active voice
    is held in memory (~60 MB each).  Call ``set_voice()`` to switch at
    runtime.

    Parameters
    ----------
    default_voice : str
        Initial Piper voice model name (e.g. ``en_US-amy-medium``).
    available_voices : list[str] | None
        Allowed voice model names.  Defaults to all keys in
        :data:`PIPER_VOICES`.
    model_path : str
        Directory containing Piper ``.onnx`` and ``.json`` voice files.
    sample_rate : int
        Target output sample rate.  Piper's native rate varies by voice
        (commonly 22050 Hz); output is resampled to this rate.
    length_scale : float
        Speech tempo multiplier.  ``< 1.0`` = faster (more upbeat),
        ``> 1.0`` = slower.  Default ``0.9`` gives drive-thru energy.
    """

    def __init__(
        self,
        default_voice: str = "en_US-amy-medium",
        available_voices: list[str] | None = None,
        model_path: str = "./models/piper",
        sample_rate: int = 24000,
        length_scale: float = 0.9,
        # Legacy compat — old callers may pass model_name=
        model_name: str | None = None,
    ) -> None:
        self._model_name = model_name or default_voice
        self._available_voices = available_voices or list(PIPER_VOICES.keys())
        self._model_path = Path(model_path)
        self._target_rate = sample_rate
        self._length_scale = length_scale
        self._voice: Any = None
        self._native_rate: int | None = None
        self._loaded = False

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def available(self) -> bool:
        return _PIPER_AVAILABLE

    @property
    def current_voice(self) -> str:
        return self._model_name

    @property
    def length_scale(self) -> float:
        return self._length_scale

    @length_scale.setter
    def length_scale(self, value: float) -> None:
        self._length_scale = max(0.5, min(value, 2.0))

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def load(self) -> None:
        """Load the Piper voice model."""
        if self._loaded:
            return
        if not _PIPER_AVAILABLE:
            raise RuntimeError(
                "Piper TTS is not installed. Install with: pip install piper-tts"
            )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_load)

    def _sync_load(self) -> None:
        onnx_path = self._model_path / f"{self._model_name}.onnx"
        json_path = self._model_path / f"{self._model_name}.onnx.json"

        if not onnx_path.exists():
            raise FileNotFoundError(
                f"Piper voice model not found: {onnx_path}. "
                f"Download with: piper --download-dir {self._model_path} --model {self._model_name}"
            )

        config_path = str(json_path) if json_path.exists() else None
        self._voice = _PiperVoice.load(str(onnx_path), config_path=config_path)

        # Determine the native sample rate from voice config
        if hasattr(self._voice, "config") and hasattr(self._voice.config, "sample_rate"):
            self._native_rate = self._voice.config.sample_rate
        else:
            self._native_rate = 22050  # Piper default for most voices

        self._loaded = True
        logger.info(
            "Piper TTS loaded: voice=%s, native_rate=%d, target_rate=%d, length_scale=%.2f",
            self._model_name,
            self._native_rate,
            self._target_rate,
            self._length_scale,
        )
        pipeline_logger.info(
            "Piper TTS loaded: voice=%s, rate=%d→%d, length_scale=%.2f",
            self._model_name, self._native_rate, self._target_rate, self._length_scale,
        )

    async def unload(self) -> None:
        """Unload the voice model and free memory."""
        prev = self._model_name if self._loaded else None
        self._voice = None
        self._native_rate = None
        self._loaded = False
        if prev:
            logger.info("Piper TTS unloaded: voice=%s", prev)

    async def set_voice(self, voice_id: str) -> bool:
        """Switch to a different voice model at runtime.

        Unloads the current voice and loads the requested one.
        Returns True on success, False if the voice is invalid or
        the model file is missing.
        """
        if voice_id not in self._available_voices:
            logger.warning("Voice '%s' not in available voices list — ignoring", voice_id)
            return False

        if voice_id == self._model_name and self._loaded:
            logger.debug("Voice '%s' already active — no-op", voice_id)
            return True

        # Check model file exists before unloading current voice
        onnx_path = self._model_path / f"{voice_id}.onnx"
        if not onnx_path.exists():
            logger.warning(
                "Voice model file not found: %s — keeping current voice '%s'",
                onnx_path, self._model_name,
            )
            return False

        await self.unload()
        self._model_name = voice_id
        try:
            await self.load()
            logger.info("Voice switched to '%s'", voice_id)
            return True
        except Exception as exc:
            logger.error("Failed to load voice '%s': %s — TTS unavailable until next switch", voice_id, exc)
            return False

    def get_voice_info(self) -> list[dict[str, str]]:
        """Return metadata for all available voices."""
        voices = []
        for vid in self._available_voices:
            meta = PIPER_VOICES.get(vid, {})
            voices.append({
                "id": vid,
                "name": meta.get("name", vid),
                "accent": meta.get("accent", ""),
                "personality": meta.get("personality", ""),
            })
        return voices

    # ── Synthesis ───────────────────────────────────────────────────────────

    async def synthesize_streaming(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesize text to audio, yielding PCM chunks per sentence.

        Splits text at sentence boundaries for lower latency — each
        sentence is synthesized and yielded independently.

        Yields:
            Raw PCM audio bytes (target sample rate, mono, int16).
        """
        if not self._loaded:
            raise RuntimeError("TTS not loaded — call load() first")

        sentences = _split_sentences(text)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            chunk = await self._synthesize_sentence(sentence)
            if chunk:
                yield chunk

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to a complete audio buffer."""
        chunks: list[bytes] = []
        async for chunk in self.synthesize_streaming(text):
            chunks.append(chunk)
        return b"".join(chunks)

    # ── Internal ────────────────────────────────────────────────────────────

    async def _synthesize_sentence(self, sentence: str) -> bytes:
        """Synthesize a single sentence in an executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_synthesize, sentence)

    def _sync_synthesize(self, text: str) -> bytes:
        """Synchronous Piper synthesis + optional resampling."""
        audio_buffer = io.BytesIO()
        try:
            self._voice.synthesize(
                text,
                audio_buffer,
                sentence_silence=0.1,
                length_scale=self._length_scale,
            )
        except TypeError:
            # Older piper-tts versions may not accept length_scale kwarg
            try:
                self._voice.synthesize(text, audio_buffer, sentence_silence=0.1)
            except Exception as exc:
                logger.error("Piper synthesis failed: %s", exc)
                return b""
        except Exception as exc:
            logger.error("Piper synthesis failed: %s", exc)
            pipeline_logger.error("Piper TTS synthesis failed: %s", exc)
            return b""

        raw_pcm = audio_buffer.getvalue()
        if not raw_pcm:
            return b""

        # Resample if native rate differs from target
        if self._native_rate and self._native_rate != self._target_rate:
            raw_pcm = _resample_pcm(raw_pcm, self._native_rate, self._target_rate)

        return raw_pcm


# ── Module-level helpers ────────────────────────────────────────────────────


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence boundaries for lower-latency streaming."""
    parts = _SENTENCE_RE.split(text)
    return [p for p in parts if p.strip()]


def _resample_pcm(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample PCM int16 mono audio using linear interpolation.

    This is a lightweight resampler suitable for speech.  For
    production quality, consider ``scipy.signal.resample`` or
    ``soxr``, but those add heavy dependencies.
    """
    if src_rate == dst_rate:
        return pcm_bytes

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    ratio = dst_rate / src_rate
    new_length = int(len(samples) * ratio)

    if new_length == 0:
        return b""

    x_old = np.linspace(0, 1, len(samples), endpoint=False)
    x_new = np.linspace(0, 1, new_length, endpoint=False)
    resampled = np.interp(x_new, x_old, samples).astype(np.int16)

    return resampled.tobytes()
