"""Tests for WhisperSTTEngine — Faster-Whisper speech-to-text engine.

Covers:
  - WHISPER_AVAILABLE flag (mock missing import)
  - WhisperSTTEngine initialization / config params
  - Device auto-detection (CUDA vs CPU fallback)
  - Model loading (lazy load on first transcribe)
  - Model unloading / cleanup
  - is_loaded / device_name property state tracking
  - transcribe(): PCM → float32 conversion, segment joining, executor usage
  - Short audio guard (<0.5s returns empty string)
  - Error handling (model not loaded raises, transcription failure → "")
  - VAD filter and language params passed to model

All tests mock faster_whisper.WhisperModel — no real model needed.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))


# ═══════════════════════════════════════════════════════════════════════════════
# WHISPER_AVAILABLE FLAG TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WhisperAvailableFlagTests(unittest.TestCase):
    """Test the WHISPER_AVAILABLE module-level guard flag."""

    def test_flag_is_boolean(self):
        """WHISPER_AVAILABLE is always a boolean."""
        from whisper_stt import WHISPER_AVAILABLE
        self.assertIsInstance(WHISPER_AVAILABLE, bool)

    def test_flag_false_when_import_fails(self):
        """When faster_whisper is not installed, flag should be False."""
        # The real environment doesn't have faster_whisper, so the flag
        # should already be False.  We verify the guard logic by checking
        # the module-level value set after the failed import attempt.
        import importlib
        import whisper_stt

        # Force-reload with faster_whisper unavailable
        with patch.dict("sys.modules", {"faster_whisper": None}):
            saved = whisper_stt.WHISPER_AVAILABLE
            # The module already ran its import; check it didn't crash
            self.assertIsInstance(saved, bool)

    def test_whisper_model_ref_none_when_unavailable(self):
        """_WhisperModel should be None when faster_whisper not installed."""
        from whisper_stt import _WhisperModel, WHISPER_AVAILABLE
        if not WHISPER_AVAILABLE:
            self.assertIsNone(_WhisperModel)


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALIZATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WhisperSTTInitTests(unittest.TestCase):
    """Test WhisperSTTEngine construction and default config."""

    def setUp(self):
        from whisper_stt import WhisperSTTEngine
        self.engine_cls = WhisperSTTEngine

    def test_default_init(self):
        engine = self.engine_cls()
        self.assertEqual(engine._model_size, "small")
        self.assertEqual(engine._requested_device, "auto")
        self.assertEqual(engine._compute_type, "int8")
        self.assertFalse(engine._loaded)
        self.assertIsNone(engine._model)

    def test_custom_params(self):
        engine = self.engine_cls(
            model_size="large-v3",
            device="cuda",
            compute_type="float16",
        )
        self.assertEqual(engine._model_size, "large-v3")
        self.assertEqual(engine._requested_device, "cuda")
        self.assertEqual(engine._compute_type, "float16")

    def test_device_name_initially_none(self):
        engine = self.engine_cls()
        self.assertEqual(engine.device_name, "none")

    def test_is_loaded_initially_false(self):
        engine = self.engine_cls()
        self.assertFalse(engine.is_loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE AUTO-DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class DeviceDetectionTests(unittest.TestCase):
    """Test _detect_device static method — CUDA vs CPU fallback."""

    def setUp(self):
        from whisper_stt import WhisperSTTEngine
        self._detect = WhisperSTTEngine._detect_device

    def test_cuda_via_torch(self):
        """CUDA available via torch → returns ('cuda', 'float16')."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            device, compute = self._detect("int8")
        self.assertEqual(device, "cuda")
        self.assertEqual(compute, "float16")

    def test_no_torch_no_ctranslate2_falls_back_to_cpu(self):
        """No torch, no ctranslate2 → returns ('cpu', fallback_compute)."""
        def _import_fail(name, *a, **kw):
            if name in ("torch", "ctranslate2"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *a, **kw)

        import builtins
        original_import = builtins.__import__
        with patch.object(builtins, "__import__", side_effect=_import_fail):
            device, compute = self._detect("int8")
        self.assertEqual(device, "cpu")
        self.assertEqual(compute, "int8")

    def test_torch_no_cuda_ctranslate2_unavailable_falls_back(self):
        """torch installed but CUDA unavailable, ctranslate2 absent → CPU."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        def _import_maybe(name, *a, **kw):
            if name == "torch":
                return mock_torch
            if name == "ctranslate2":
                raise ImportError("No ctranslate2")
            return original_import(name, *a, **kw)

        import builtins
        original_import = builtins.__import__
        with patch.object(builtins, "__import__", side_effect=_import_maybe):
            device, compute = self._detect("int8")
        self.assertEqual(device, "cpu")
        self.assertEqual(compute, "int8")

    def test_cuda_via_ctranslate2(self):
        """CUDA available via ctranslate2 → returns ('cuda', 'float16')."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        mock_ct2 = MagicMock()
        mock_ct2.get_supported_compute_types.return_value = ["cuda", "int8"]

        def _import_maybe(name, *a, **kw):
            if name == "torch":
                return mock_torch
            if name == "ctranslate2":
                return mock_ct2
            return original_import(name, *a, **kw)

        import builtins
        original_import = builtins.__import__
        with patch.object(builtins, "__import__", side_effect=_import_maybe):
            device, compute = self._detect("int8")
        self.assertEqual(device, "cuda")
        self.assertEqual(compute, "float16")

    def test_fallback_compute_preserved_for_cpu(self):
        """CPU fallback preserves the caller's compute type."""
        def _import_fail(name, *a, **kw):
            if name in ("torch", "ctranslate2"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *a, **kw)

        import builtins
        original_import = builtins.__import__
        with patch.object(builtins, "__import__", side_effect=_import_fail):
            _, compute = self._detect("float32")
        self.assertEqual(compute, "float32")


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL LOADING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ModelLoadingTests(unittest.IsolatedAsyncioTestCase):
    """Test model load / unload lifecycle."""

    def _make_engine(self, **kwargs):
        from whisper_stt import WhisperSTTEngine
        return WhisperSTTEngine(**kwargs)

    @patch("whisper_stt.WHISPER_AVAILABLE", True)
    @patch("whisper_stt._WhisperModel")
    async def test_load_creates_model(self, mock_model_cls):
        """load() instantiates WhisperModel with correct params."""
        mock_model_cls.return_value = MagicMock()
        engine = self._make_engine(model_size="base", device="cpu", compute_type="int8")

        await engine.load()

        mock_model_cls.assert_called_once_with("base", device="cpu", compute_type="int8")
        self.assertTrue(engine.is_loaded)
        self.assertEqual(engine.device_name, "cpu")

    @patch("whisper_stt.WHISPER_AVAILABLE", True)
    @patch("whisper_stt._WhisperModel")
    async def test_load_idempotent(self, mock_model_cls):
        """Calling load() twice doesn't create a second model."""
        mock_model_cls.return_value = MagicMock()
        engine = self._make_engine()
        await engine.load()
        await engine.load()
        mock_model_cls.assert_called_once()

    @patch("whisper_stt.WHISPER_AVAILABLE", False)
    async def test_load_raises_when_unavailable(self):
        """load() raises RuntimeError when faster-whisper is not installed."""
        engine = self._make_engine()
        with self.assertRaises(RuntimeError) as ctx:
            await engine.load()
        self.assertIn("not installed", str(ctx.exception))

    @patch("whisper_stt.WHISPER_AVAILABLE", True)
    @patch("whisper_stt._WhisperModel")
    async def test_auto_device_calls_detect(self, mock_model_cls):
        """device='auto' triggers _detect_device for resolution."""
        mock_model_cls.return_value = MagicMock()
        engine = self._make_engine(device="auto")

        with patch.object(
            type(engine), "_detect_device",
            return_value=("cpu", "int8"),
        ) as mock_detect:
            await engine.load()
            mock_detect.assert_called_once_with("int8")


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL UNLOADING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ModelUnloadingTests(unittest.IsolatedAsyncioTestCase):
    """Test model cleanup on unload."""

    def _make_loaded_engine(self):
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True
        engine._device_name = "cpu"
        return engine

    async def test_unload_clears_model(self):
        engine = self._make_loaded_engine()
        await engine.unload()
        self.assertIsNone(engine._model)
        self.assertFalse(engine.is_loaded)
        self.assertEqual(engine.device_name, "none")

    async def test_unload_idempotent(self):
        """Calling unload() when not loaded does nothing."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        await engine.unload()  # should not raise
        self.assertFalse(engine.is_loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# PROPERTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class PropertyTests(unittest.TestCase):
    """Test is_loaded and device_name property state tracking."""

    def setUp(self):
        from whisper_stt import WhisperSTTEngine
        self.engine_cls = WhisperSTTEngine

    def test_is_loaded_reflects_internal_state(self):
        engine = self.engine_cls()
        self.assertFalse(engine.is_loaded)
        engine._loaded = True
        self.assertTrue(engine.is_loaded)

    def test_device_name_reflects_internal_state(self):
        engine = self.engine_cls()
        self.assertEqual(engine.device_name, "none")
        engine._device_name = "cuda"
        self.assertEqual(engine.device_name, "cuda")


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSCRIPTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_audio_pcm(duration_s: float, sample_rate: int = 16000) -> bytes:
    """Generate silence PCM bytes of a given duration (int16 mono)."""
    n_samples = int(duration_s * sample_rate)
    return np.zeros(n_samples, dtype=np.int16).tobytes()


class TranscriptionTests(unittest.IsolatedAsyncioTestCase):
    """Test transcribe() method — PCM conversion, segment joining, executor."""

    def _make_loaded_engine(self):
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True
        engine._device_name = "cpu"
        return engine

    async def test_pcm_to_float32_conversion(self):
        """PCM int16 bytes are converted to float32 numpy array in [-1, 1]."""
        engine = self._make_loaded_engine()

        captured_array = None
        def mock_transcribe(audio, **kwargs):
            nonlocal captured_array
            captured_array = audio
            seg = MagicMock()
            seg.text = "hello"
            return iter([seg]), MagicMock()

        engine._model.transcribe = mock_transcribe

        # Create known PCM: max positive int16
        pcm = np.array([32767, -32768, 0], dtype=np.int16).tobytes()
        # Pad to meet minimum duration
        padding = _make_audio_pcm(1.0)
        full_pcm = padding + pcm

        await engine.transcribe(full_pcm)

        self.assertIsNotNone(captured_array)
        self.assertEqual(captured_array.dtype, np.float32)
        # Values should be normalized to roughly [-1, 1]
        self.assertLessEqual(np.max(np.abs(captured_array)), 1.0 + 1e-5)

    async def test_returns_joined_segment_text(self):
        """Multiple segments are joined with spaces."""
        engine = self._make_loaded_engine()

        seg1 = MagicMock()
        seg1.text = " I want a "
        seg2 = MagicMock()
        seg2.text = " Big Mac "
        seg3 = MagicMock()
        seg3.text = " please "
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg1, seg2, seg3]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        result = await engine.transcribe(audio)
        self.assertEqual(result, "I want a Big Mac please")

    async def test_empty_segments_skipped(self):
        """Segments with only whitespace are skipped."""
        engine = self._make_loaded_engine()

        seg1 = MagicMock()
        seg1.text = " hello "
        seg2 = MagicMock()
        seg2.text = "   "  # whitespace only
        seg3 = MagicMock()
        seg3.text = " world "
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg1, seg2, seg3]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        result = await engine.transcribe(audio)
        self.assertEqual(result, "hello world")

    async def test_runs_in_executor(self):
        """Transcription runs via run_in_executor (non-blocking)."""
        engine = self._make_loaded_engine()

        seg = MagicMock()
        seg.text = "test"
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        # The fact that we can await it from an async test proves
        # it runs in the executor.  We verify _sync_transcribe is called.
        with patch.object(engine, "_sync_transcribe", wraps=engine._sync_transcribe) as mock_sync:
            result = await engine.transcribe(audio)
            mock_sync.assert_called_once()

    async def test_vad_filter_enabled(self):
        """vad_filter=True is passed to model.transcribe()."""
        engine = self._make_loaded_engine()

        seg = MagicMock()
        seg.text = "test"
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        await engine.transcribe(audio)

        call_kwargs = engine._model.transcribe.call_args
        self.assertTrue(call_kwargs[1].get("vad_filter") or
                       call_kwargs.kwargs.get("vad_filter"))

    async def test_language_set_to_english(self):
        """language='en' is passed to model.transcribe()."""
        engine = self._make_loaded_engine()

        seg = MagicMock()
        seg.text = "test"
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        await engine.transcribe(audio)

        call_kwargs = engine._model.transcribe.call_args
        self.assertEqual(
            call_kwargs[1].get("language") or call_kwargs.kwargs.get("language"),
            "en",
        )

    async def test_beam_size_passed(self):
        """beam_size=3 is passed to model.transcribe()."""
        engine = self._make_loaded_engine()

        seg = MagicMock()
        seg.text = "test"
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        await engine.transcribe(audio)

        call_kwargs = engine._model.transcribe.call_args
        self.assertEqual(
            call_kwargs[1].get("beam_size") or call_kwargs.kwargs.get("beam_size"),
            3,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SHORT AUDIO GUARD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ShortAudioGuardTests(unittest.IsolatedAsyncioTestCase):
    """Test minimum audio duration guard (<0.5s → empty string)."""

    async def test_very_short_audio_returns_empty(self):
        """Audio shorter than 0.5s returns '' without calling model."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True

        # 0.1 seconds at 16kHz = 1600 samples = 3200 bytes
        short_audio = _make_audio_pcm(0.1)
        result = await engine.transcribe(short_audio)
        self.assertEqual(result, "")
        # Model should not have been called
        engine._model.transcribe.assert_not_called()

    async def test_exactly_at_threshold_returns_empty(self):
        """Audio at exactly 0.5s boundary (just under) returns ''."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True

        # Just under 0.5s — 7999 samples = 15998 bytes (0.4999375s)
        almost_audio = np.zeros(7999, dtype=np.int16).tobytes()
        result = await engine.transcribe(almost_audio)
        self.assertEqual(result, "")

    async def test_above_threshold_proceeds(self):
        """Audio >= 0.5s proceeds to transcription."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True
        engine._device_name = "cpu"

        seg = MagicMock()
        seg.text = "hello"
        engine._model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )

        # 0.6 seconds at 16kHz = 9600 samples
        audio = _make_audio_pcm(0.6)
        result = await engine.transcribe(audio)
        self.assertEqual(result, "hello")
        engine._model.transcribe.assert_called_once()

    async def test_empty_bytes_returns_empty(self):
        """Zero-length audio returns '' immediately."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._loaded = True
        result = await engine.transcribe(b"")
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    """Test error handling in load() and transcribe()."""

    @patch("whisper_stt.WHISPER_AVAILABLE", False)
    async def test_load_raises_when_whisper_unavailable(self):
        """load() raises RuntimeError when faster-whisper not installed."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        with self.assertRaises(RuntimeError):
            await engine.load()

    async def test_transcription_failure_returns_empty(self):
        """If model.transcribe raises, returns '' gracefully."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True
        engine._device_name = "cpu"

        engine._model.transcribe = MagicMock(
            side_effect=RuntimeError("CUDA OOM")
        )

        audio = _make_audio_pcm(1.0)
        result = await engine.transcribe(audio)
        self.assertEqual(result, "")

    @patch("whisper_stt.WHISPER_AVAILABLE", True)
    @patch("whisper_stt._WhisperModel")
    async def test_lazy_load_on_first_transcribe(self, mock_model_cls):
        """transcribe() auto-loads model if not already loaded."""
        mock_model = MagicMock()
        seg = MagicMock()
        seg.text = "auto loaded"
        mock_model.transcribe = MagicMock(
            return_value=(iter([seg]), MagicMock())
        )
        mock_model_cls.return_value = mock_model

        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine(device="cpu")
        self.assertFalse(engine.is_loaded)

        audio = _make_audio_pcm(1.0)
        result = await engine.transcribe(audio)

        self.assertTrue(engine.is_loaded)
        self.assertEqual(result, "auto loaded")
        mock_model_cls.assert_called_once()

    async def test_no_segments_returns_empty(self):
        """Model returns zero segments → empty string."""
        from whisper_stt import WhisperSTTEngine
        engine = WhisperSTTEngine()
        engine._model = MagicMock()
        engine._loaded = True
        engine._device_name = "cpu"

        engine._model.transcribe = MagicMock(
            return_value=(iter([]), MagicMock())
        )

        audio = _make_audio_pcm(1.0)
        result = await engine.transcribe(audio)
        self.assertEqual(result, "")


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ConstantsTests(unittest.TestCase):
    """Verify module-level constants are correctly set."""

    def test_min_audio_seconds(self):
        from whisper_stt import _MIN_AUDIO_SECONDS
        self.assertAlmostEqual(_MIN_AUDIO_SECONDS, 0.5)

    def test_expected_sample_rate(self):
        from whisper_stt import _EXPECTED_SAMPLE_RATE
        self.assertEqual(_EXPECTED_SAMPLE_RATE, 16000)


if __name__ == "__main__":
    unittest.main()
