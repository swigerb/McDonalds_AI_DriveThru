"""Tests for PiperTTSEngine — local text-to-speech with multi-voice support.

Covers:
  - Initialisation and default config
  - PIPER_VOICES metadata dictionary
  - Available voice list
  - set_voice with valid / invalid voice ID
  - set_voice lazy loading (unloads previous, loads new)
  - synthesize_streaming returns async generator of bytes
  - synthesize returns complete buffer
  - length_scale configuration (0.9 default)
  - Sample rate resampling (22050 → 24000)
  - Sentence chunking in streaming mode
  - Error handling: missing voice model file

All tests mock piper.voice — no actual model files needed.
"""

import asyncio
import io
import sys
import struct
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from piper_tts import (
    PiperTTSEngine,
    PIPER_VOICES,
    _split_sentences,
    _resample_pcm,
    _PIPER_AVAILABLE,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_piper_voice(native_rate=22050):
    """Create a mock PiperVoice that writes PCM data on synthesize."""
    voice = MagicMock()
    config = MagicMock()
    config.sample_rate = native_rate
    voice.config = config

    def fake_synthesize(text, buffer, sentence_silence=0.1, length_scale=0.9):
        # Write 100 samples of silence as int16
        samples = np.zeros(100, dtype=np.int16)
        buffer.write(samples.tobytes())

    voice.synthesize = MagicMock(side_effect=fake_synthesize)
    return voice


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALISATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class PiperInitTests(unittest.TestCase):
    """Test PiperTTSEngine construction and default values."""

    def test_default_init(self):
        engine = PiperTTSEngine()
        self.assertEqual(engine._model_name, "en_US-amy-medium")
        self.assertEqual(engine._target_rate, 24000)
        self.assertAlmostEqual(engine._length_scale, 0.9)
        self.assertFalse(engine.is_loaded)

    def test_custom_init(self):
        engine = PiperTTSEngine(
            default_voice="en_GB-jenny_dioco-medium",
            model_path="./custom/path",
            sample_rate=16000,
            length_scale=1.1,
        )
        self.assertEqual(engine._model_name, "en_GB-jenny_dioco-medium")
        self.assertEqual(engine._target_rate, 16000)
        self.assertAlmostEqual(engine._length_scale, 1.1)

    def test_legacy_model_name_compat(self):
        """model_name= legacy parameter takes priority if set."""
        engine = PiperTTSEngine(model_name="legacy-voice")
        self.assertEqual(engine._model_name, "legacy-voice")

    def test_available_voices_default(self):
        engine = PiperTTSEngine()
        self.assertEqual(engine._available_voices, list(PIPER_VOICES.keys()))

    def test_available_voices_custom(self):
        voices = ["en_US-amy-medium", "en_US-lessac-medium"]
        engine = PiperTTSEngine(available_voices=voices)
        self.assertEqual(engine._available_voices, voices)

    def test_current_voice_property(self):
        engine = PiperTTSEngine(default_voice="en_US-lessac-medium")
        self.assertEqual(engine.current_voice, "en_US-lessac-medium")


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE METADATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class VoiceMetadataTests(unittest.TestCase):
    """Test PIPER_VOICES dictionary and get_voice_info()."""

    def test_piper_voices_has_expected_keys(self):
        self.assertIn("en_US-amy-medium", PIPER_VOICES)
        self.assertIn("en_GB-jenny_dioco-medium", PIPER_VOICES)
        self.assertIn("en_US-lessac-medium", PIPER_VOICES)
        self.assertIn("en_US-kristin-medium", PIPER_VOICES)

    def test_piper_voices_metadata_structure(self):
        for vid, meta in PIPER_VOICES.items():
            self.assertIn("name", meta, f"Missing 'name' for {vid}")
            self.assertIn("accent", meta, f"Missing 'accent' for {vid}")
            self.assertIn("personality", meta, f"Missing 'personality' for {vid}")

    def test_get_voice_info_returns_list(self):
        engine = PiperTTSEngine()
        info = engine.get_voice_info()
        self.assertIsInstance(info, list)
        self.assertEqual(len(info), len(PIPER_VOICES))

    def test_get_voice_info_structure(self):
        engine = PiperTTSEngine()
        info = engine.get_voice_info()
        for entry in info:
            self.assertIn("id", entry)
            self.assertIn("name", entry)
            self.assertIn("accent", entry)
            self.assertIn("personality", entry)

    def test_get_voice_info_custom_subset(self):
        engine = PiperTTSEngine(available_voices=["en_US-amy-medium"])
        info = engine.get_voice_info()
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["id"], "en_US-amy-medium")


# ═══════════════════════════════════════════════════════════════════════════════
# LENGTH SCALE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class LengthScaleTests(unittest.TestCase):
    """Test length_scale property and clamping."""

    def test_default_length_scale(self):
        engine = PiperTTSEngine()
        self.assertAlmostEqual(engine.length_scale, 0.9)

    def test_set_length_scale(self):
        engine = PiperTTSEngine()
        engine.length_scale = 1.2
        self.assertAlmostEqual(engine.length_scale, 1.2)

    def test_length_scale_clamp_min(self):
        engine = PiperTTSEngine()
        engine.length_scale = 0.1
        self.assertAlmostEqual(engine.length_scale, 0.5)

    def test_length_scale_clamp_max(self):
        engine = PiperTTSEngine()
        engine.length_scale = 3.0
        self.assertAlmostEqual(engine.length_scale, 2.0)


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE SWITCHING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class VoiceSwitchingTests(unittest.IsolatedAsyncioTestCase):
    """Test set_voice with valid / invalid voices and lazy loading."""

    async def test_set_voice_invalid_returns_false(self):
        """Invalid voice ID returns False, keeps current voice."""
        engine = PiperTTSEngine()
        result = await engine.set_voice("nonexistent-voice")
        self.assertFalse(result)
        self.assertEqual(engine.current_voice, "en_US-amy-medium")

    async def test_set_voice_same_voice_loaded_is_noop(self):
        """Switching to already-active voice is a no-op that returns True."""
        engine = PiperTTSEngine()
        engine._loaded = True
        engine._model_name = "en_US-amy-medium"
        result = await engine.set_voice("en_US-amy-medium")
        self.assertTrue(result)

    async def test_set_voice_missing_model_file_returns_false(self):
        """Voice in allowed list but model file missing → False."""
        engine = PiperTTSEngine(model_path="./nonexistent_dir")
        result = await engine.set_voice("en_US-lessac-medium")
        self.assertFalse(result)
        self.assertEqual(engine.current_voice, "en_US-amy-medium")

    async def test_set_voice_unloads_previous(self):
        """Switching voices unloads the previous model before loading new one."""
        engine = PiperTTSEngine()
        engine._loaded = True
        engine._voice = MagicMock()
        engine._model_name = "en_US-amy-medium"

        mock_voice = _make_mock_piper_voice()

        with patch.object(Path, "exists", return_value=True), \
             patch("piper_tts._PIPER_AVAILABLE", True), \
             patch("piper_tts._PiperVoice") as mock_pv:
            mock_pv.load.return_value = mock_voice
            result = await engine.set_voice("en_US-lessac-medium")

        self.assertTrue(result)
        self.assertEqual(engine.current_voice, "en_US-lessac-medium")

    async def test_set_voice_load_failure_returns_false(self):
        """If loading new voice fails, set_voice returns False."""
        engine = PiperTTSEngine()
        engine._loaded = True
        engine._model_name = "en_US-amy-medium"

        with patch.object(Path, "exists", return_value=True), \
             patch("piper_tts._PiperVoice") as mock_pv:
            mock_pv.load.side_effect = Exception("Model corrupt")
            result = await engine.set_voice("en_US-lessac-medium")

        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════════════════
# LOADING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class LoadingTests(unittest.IsolatedAsyncioTestCase):
    """Test model loading and error paths."""

    async def test_load_when_not_available_raises(self):
        """Loading when piper is not installed raises RuntimeError."""
        engine = PiperTTSEngine()
        with patch("piper_tts._PIPER_AVAILABLE", False):
            with self.assertRaises(RuntimeError) as ctx:
                await engine.load()
            self.assertIn("not installed", str(ctx.exception))

    async def test_load_missing_onnx_file_raises(self):
        """Loading when .onnx file missing raises FileNotFoundError."""
        engine = PiperTTSEngine(model_path="./nonexistent_dir")
        with patch("piper_tts._PIPER_AVAILABLE", True):
            with self.assertRaises(FileNotFoundError):
                await engine.load()

    async def test_load_idempotent(self):
        """Loading twice does not re-load the model."""
        engine = PiperTTSEngine()
        engine._loaded = True
        await engine.load()  # Should be a no-op

    async def test_unload_clears_state(self):
        """unload() resets loaded state."""
        engine = PiperTTSEngine()
        engine._loaded = True
        engine._voice = MagicMock()
        engine._native_rate = 22050
        await engine.unload()
        self.assertFalse(engine.is_loaded)
        self.assertIsNone(engine._voice)
        self.assertIsNone(engine._native_rate)

    async def test_unload_when_not_loaded_is_noop(self):
        """Unloading when not loaded does nothing."""
        engine = PiperTTSEngine()
        await engine.unload()
        self.assertFalse(engine.is_loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# SYNTHESIS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class SynthesisTests(unittest.IsolatedAsyncioTestCase):
    """Test synthesize and synthesize_streaming with mocked voice."""

    def _loaded_engine(self, native_rate=22050):
        """Create a loaded engine with a mock voice."""
        engine = PiperTTSEngine(sample_rate=24000)
        engine._loaded = True
        engine._voice = _make_mock_piper_voice(native_rate)
        engine._native_rate = native_rate
        return engine

    async def test_synthesize_streaming_yields_bytes(self):
        """synthesize_streaming yields byte chunks."""
        engine = self._loaded_engine()
        chunks = []
        async for chunk in engine.synthesize_streaming("Hello. World."):
            chunks.append(chunk)
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            self.assertIsInstance(chunk, bytes)

    async def test_synthesize_returns_complete_buffer(self):
        """synthesize returns a single complete byte buffer."""
        engine = self._loaded_engine()
        result = await engine.synthesize("Hello World.")
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    async def test_synthesize_not_loaded_raises(self):
        """synthesize_streaming raises when TTS not loaded."""
        engine = PiperTTSEngine()
        with self.assertRaises(RuntimeError):
            async for _ in engine.synthesize_streaming("test"):
                pass

    async def test_synthesize_empty_text(self):
        """Synthesizing empty text yields nothing."""
        engine = self._loaded_engine()
        chunks = []
        async for chunk in engine.synthesize_streaming(""):
            chunks.append(chunk)
        self.assertEqual(len(chunks), 0)

    async def test_synthesize_resamples_if_rates_differ(self):
        """When native_rate != target_rate, output is resampled."""
        engine = self._loaded_engine(native_rate=22050)
        # The engine target is 24000 — should trigger resampling
        result = await engine.synthesize("Test sentence.")
        self.assertIsInstance(result, bytes)

    async def test_synthesize_no_resample_when_rates_match(self):
        """When native_rate == target_rate, no resampling occurs."""
        engine = self._loaded_engine(native_rate=24000)
        engine._target_rate = 24000
        result = await engine.synthesize("Test.")
        self.assertIsInstance(result, bytes)


# ═══════════════════════════════════════════════════════════════════════════════
# SENTENCE CHUNKING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class SentenceChunkingTests(unittest.TestCase):
    """Test _split_sentences helper function."""

    def test_single_sentence(self):
        result = _split_sentences("Hello world.")
        self.assertEqual(result, ["Hello world."])

    def test_multiple_sentences(self):
        result = _split_sentences("Hello. World! How are you?")
        self.assertEqual(len(result), 3)

    def test_empty_string(self):
        result = _split_sentences("")
        self.assertEqual(result, [])

    def test_no_punctuation(self):
        result = _split_sentences("Hello world")
        self.assertEqual(result, ["Hello world"])

    def test_whitespace_only(self):
        result = _split_sentences("   ")
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════════════
# RESAMPLING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ResamplingTests(unittest.TestCase):
    """Test _resample_pcm helper function."""

    def test_same_rate_passthrough(self):
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        result = _resample_pcm(pcm, 24000, 24000)
        self.assertEqual(result, pcm)

    def test_upsample_22050_to_24000(self):
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        result = _resample_pcm(pcm, 22050, 24000)
        expected_len = int(100 * (24000 / 22050))
        self.assertEqual(len(result) // 2, expected_len)

    def test_downsample_48000_to_24000(self):
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        result = _resample_pcm(pcm, 48000, 24000)
        expected_len = int(100 * (24000 / 48000))
        self.assertEqual(len(result) // 2, expected_len)

    def test_empty_input(self):
        result = _resample_pcm(b"", 22050, 24000)
        self.assertEqual(result, b"")

    def test_output_is_bytes(self):
        pcm = np.ones(50, dtype=np.int16).tobytes()
        result = _resample_pcm(pcm, 22050, 24000)
        self.assertIsInstance(result, bytes)


if __name__ == "__main__":
    unittest.main()
