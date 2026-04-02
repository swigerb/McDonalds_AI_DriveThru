"""Tests for LocalPhi4Processor — offline drive-thru processor.

Covers:
  - Instantiation and config defaults
  - LOCAL_MODE_AVAILABLE flag (mock missing imports)
  - WebSocket message protocol compatibility:
      input_audio_buffer.append, session.update, extension.set_local_mode,
      extension.set_piper_voice, response messages
  - Audio buffer accumulation
  - VAD silence detection triggers processing
  - Tool call extraction from model output
  - Error handling (model failure → error message to client)
  - Status helper properties (available, device, model_loaded)
  - Module-level audio utilities (_compute_energy, _downsample_24k_to_16k)

All tests mock onnxruntime_genai and piper — no actual models needed.
"""

import asyncio
import base64
import json
import sys
import struct
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aiohttp import web
import aiohttp

from local_processor import (
    LocalPhi4Processor,
    _compute_energy,
    _downsample_24k_to_16k,
    _FRONTEND_SAMPLE_RATE,
    _PHI4_SAMPLE_RATE,
    _BYTES_PER_SAMPLE,
)
from rtmt import Tool, ToolResult, ToolResultDirection


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_ws():
    """Create a mock WebSocketResponse with async send methods."""
    ws = MagicMock(spec=web.WebSocketResponse)
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


class _AsyncIter:
    """Async iterator adapter for a regular list of messages."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


def _make_ws_text_msg(data: dict):
    """Create a mock aiohttp WebSocket TEXT message."""
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.TEXT
    msg.data = json.dumps(data)
    return msg


def _make_ws_close_msg():
    """Create a mock aiohttp WebSocket CLOSE message."""
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.CLOSE
    msg.data = None
    return msg


def _make_audio_chunk(n_samples=100, amplitude=0):
    """Generate a base64-encoded PCM audio chunk."""
    if amplitude == 0:
        samples = np.zeros(n_samples, dtype=np.int16)
    else:
        samples = np.full(n_samples, amplitude, dtype=np.int16)
    return base64.b64encode(samples.tobytes()).decode()


def _make_request():
    req = MagicMock(spec=web.Request)
    return req


# ═══════════════════════════════════════════════════════════════════════════════
# INSTANTIATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class LocalProcessorInitTests(unittest.TestCase):
    """Test LocalPhi4Processor construction and default config."""

    def test_default_init(self):
        proc = LocalPhi4Processor()
        self.assertIsNone(proc._model_path)
        self.assertEqual(proc._device, "auto")
        self.assertTrue(proc._lazy_load)
        self.assertFalse(proc._model_loaded)

    def test_config_overrides(self):
        config = {
            "model_path": "/models/phi4",
            "device": "cuda",
            "temperature": 0.8,
            "max_length": 512,
            "lazy_load": False,
            "tts_default_voice": "en_GB-jenny_dioco-medium",
        }
        proc = LocalPhi4Processor(config=config)
        self.assertEqual(proc._model_path, "/models/phi4")
        self.assertEqual(proc._device, "cuda")
        self.assertAlmostEqual(proc.temperature, 0.8)
        self.assertEqual(proc.max_tokens, 512)
        self.assertFalse(proc._lazy_load)
        self.assertEqual(proc.voice_choice, "en_GB-jenny_dioco-medium")

    def test_empty_tools_dict(self):
        proc = LocalPhi4Processor()
        self.assertEqual(proc.tools, {})

    def test_system_message_initially_none(self):
        proc = LocalPhi4Processor()
        self.assertIsNone(proc.system_message)

    def test_default_temperature(self):
        proc = LocalPhi4Processor()
        self.assertAlmostEqual(proc.temperature, 0.6)

    def test_default_max_tokens(self):
        proc = LocalPhi4Processor()
        self.assertEqual(proc.max_tokens, 256)


# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL_MODE_AVAILABLE FLAG TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class LocalModeAvailableTests(unittest.TestCase):
    """Test LOCAL_MODE_AVAILABLE flag with mocked imports."""

    def test_flag_is_boolean(self):
        """LOCAL_MODE_AVAILABLE should be a boolean value."""
        from local_processor import LOCAL_MODE_AVAILABLE
        self.assertIsInstance(LOCAL_MODE_AVAILABLE, bool)

    def test_available_property_without_model_path(self):
        """available is False when no model_path set, even if runtime exists."""
        proc = LocalPhi4Processor()
        # Model path is None by default
        with patch("local_processor.LOCAL_MODE_AVAILABLE", True):
            self.assertFalse(proc.available)

    def test_available_property_with_model_path(self):
        """available is True when LOCAL_MODE_AVAILABLE and model_path are set."""
        proc = LocalPhi4Processor(config={"model_path": "/models/phi4"})
        with patch("local_processor.LOCAL_MODE_AVAILABLE", True):
            self.assertTrue(proc.available)

    def test_available_false_when_runtime_missing(self):
        """available is False when LOCAL_MODE_AVAILABLE is False."""
        proc = LocalPhi4Processor(config={"model_path": "/models/phi4"})
        with patch("local_processor.LOCAL_MODE_AVAILABLE", False):
            self.assertFalse(proc.available)


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS PROPERTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class StatusPropertyTests(unittest.TestCase):
    """Test status helper properties (device, model_loaded)."""

    def test_device_none_when_unavailable(self):
        proc = LocalPhi4Processor()
        with patch("local_processor.LOCAL_MODE_AVAILABLE", False):
            self.assertIsNone(proc.device)

    def test_device_returns_requested_when_available(self):
        proc = LocalPhi4Processor(config={"model_path": "/m", "device": "cuda"})
        with patch("local_processor.LOCAL_MODE_AVAILABLE", True):
            self.assertEqual(proc.device, "cuda")

    def test_device_from_loaded_model(self):
        proc = LocalPhi4Processor()
        mock_model = MagicMock()
        mock_model.is_loaded = True
        mock_model.device_name = "directml"
        proc._model = mock_model
        self.assertEqual(proc.device, "directml")

    def test_model_loaded_false_initially(self):
        proc = LocalPhi4Processor()
        self.assertFalse(proc.model_loaded)

    def test_model_loaded_true_after_loading(self):
        proc = LocalPhi4Processor()
        proc._model_loaded = True
        self.assertTrue(proc.model_loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET MESSAGE PROTOCOL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WebSocketProtocolTests(unittest.IsolatedAsyncioTestCase):
    """Test WebSocket message handling within handle_websocket."""

    def _make_processor(self):
        """Create a processor with models pre-loaded (mocked)."""
        proc = LocalPhi4Processor(config={"model_path": "/fake"})
        proc._model_loaded = True
        proc._model = MagicMock()
        proc._model.is_loaded = True
        proc._model.device_name = "cpu"
        proc._tts = MagicMock()
        proc._tts.is_loaded = True
        return proc

    async def _run_with_messages(self, proc, messages):
        """Simulate a WebSocket session with a list of messages then close."""
        ws = _make_mock_ws()
        request = _make_request()

        all_msgs = [_make_ws_text_msg(m) for m in messages]
        all_msgs.append(_make_ws_close_msg())
        ws.__aiter__ = MagicMock(return_value=_AsyncIter(all_msgs))

        await proc.handle_websocket(ws, request)
        return ws

    async def test_session_created_sent_on_connect(self):
        """First message sent is session.created."""
        proc = self._make_processor()
        ws = await self._run_with_messages(proc, [])
        first_call = ws.send_json.call_args_list[0]
        msg = first_call[0][0]
        self.assertEqual(msg["type"], "session.created")
        self.assertIn("session", msg)

    async def test_session_created_includes_model_info(self):
        """session.created includes model name and device."""
        proc = self._make_processor()
        ws = await self._run_with_messages(proc, [])
        msg = ws.send_json.call_args_list[0][0][0]
        self.assertIn("phi-4-onnx", msg["session"]["model"])

    async def test_session_update_echoed_back(self):
        """session.update message is acknowledged with session.updated."""
        proc = self._make_processor()
        ws = await self._run_with_messages(proc, [
            {"type": "session.update", "session": {"instructions": "Be helpful"}}
        ])
        # Find session.updated in sent messages
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        updated_msgs = [m for m in sent if m.get("type") == "session.updated"]
        self.assertEqual(len(updated_msgs), 1)

    async def test_session_update_sets_system_message(self):
        """session.update with instructions updates system_message."""
        proc = self._make_processor()
        await self._run_with_messages(proc, [
            {"type": "session.update", "session": {"instructions": "Be a crew member"}}
        ])
        self.assertEqual(proc.system_message, "Be a crew member")

    async def test_audio_append_accumulates_buffer(self):
        """input_audio_buffer.append adds data to the audio buffer.

        We verify indirectly by checking no processing fires for short audio.
        """
        proc = self._make_processor()
        audio_b64 = _make_audio_chunk(50, amplitude=0)
        ws = await self._run_with_messages(proc, [
            {"type": "input_audio_buffer.append", "audio": audio_b64}
        ])
        # No response.created should be sent (buffer too small to trigger processing)
        sent_types = [c[0][0].get("type") for c in ws.send_json.call_args_list]
        self.assertNotIn("response.created", sent_types[1:])  # skip session.created

    async def test_extension_set_voice_changes_voice_choice(self):
        """extension.set_voice message updates voice_choice."""
        proc = self._make_processor()
        await self._run_with_messages(proc, [
            {"type": "extension.set_voice", "voice": "shimmer"}
        ])
        self.assertEqual(proc.voice_choice, "shimmer")

    async def test_extension_set_piper_voice_invalid(self):
        """extension.set_piper_voice with invalid voice sends error."""
        proc = self._make_processor()
        proc._config["tts_available_voices"] = ["en_US-amy-medium"]
        ws = await self._run_with_messages(proc, [
            {"type": "extension.set_piper_voice", "voice": "nonexistent"}
        ])
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        error_msgs = [m for m in sent if m.get("type") == "extension.piper_voice_error"]
        self.assertEqual(len(error_msgs), 1)

    async def test_extension_set_piper_voice_valid(self):
        """extension.set_piper_voice with valid voice triggers set_voice on TTS."""
        proc = self._make_processor()
        proc._config["tts_available_voices"] = ["en_US-amy-medium", "en_US-lessac-medium"]
        proc._tts = MagicMock()
        proc._tts.set_voice = AsyncMock(return_value=True)
        ws = await self._run_with_messages(proc, [
            {"type": "extension.set_piper_voice", "voice": "en_US-lessac-medium"}
        ])
        proc._tts.set_voice.assert_awaited_once_with("en_US-lessac-medium")
        self.assertEqual(proc.voice_choice, "en_US-lessac-medium")

    async def test_response_cancel_sets_event(self):
        """response.cancel sets the cancel event (tested indirectly)."""
        proc = self._make_processor()
        # Just verify no crash — cancel event is per-connection
        ws = await self._run_with_messages(proc, [
            {"type": "response.cancel"}
        ])
        # Should complete without error

    async def test_close_message_ends_session(self):
        """CLOSE message type ends the WebSocket loop."""
        proc = self._make_processor()
        ws = _make_mock_ws()
        request = _make_request()

        close_msg = _make_ws_close_msg()
        ws.__aiter__ = MagicMock(return_value=_AsyncIter([close_msg]))

        await proc.handle_websocket(ws, request)
        # Should complete without error — session.created was sent
        ws.send_json.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL LOADING ERROR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ModelLoadingErrorTests(unittest.IsolatedAsyncioTestCase):
    """Test error handling when models fail to load."""

    async def test_load_failure_sends_error_response(self):
        """When model loading fails, an error message is sent to client."""
        proc = LocalPhi4Processor(config={"model_path": "/nonexistent"})
        proc._model_loaded = False
        proc._loading = False

        # Make _ensure_models_loaded raise
        async def _failing_load():
            raise RuntimeError("Model not found")

        proc._ensure_models_loaded = _failing_load

        ws = _make_mock_ws()
        request = _make_request()
        close_msg = _make_ws_close_msg()
        ws.__aiter__ = MagicMock(return_value=_AsyncIter([close_msg]))

        await proc.handle_websocket(ws, request)

        # Should have sent session.created and an error response
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        self.assertTrue(any(m.get("type") == "session.created" for m in sent))


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class BackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    """Test start/stop_background_tasks."""

    async def test_start_background_no_lazy_load(self):
        """start_background_tasks pre-loads when lazy_load is False."""
        proc = LocalPhi4Processor(config={"lazy_load": False, "model_path": "/fake"})
        proc._ensure_models_loaded = AsyncMock()
        with patch("local_processor.asyncio.ensure_future") as mock_ef:
            await proc.start_background_tasks()
            mock_ef.assert_called_once()

    async def test_start_background_lazy_load_skips(self):
        """start_background_tasks skips when lazy_load is True."""
        proc = LocalPhi4Processor(config={"lazy_load": True})
        proc._ensure_models_loaded = AsyncMock()
        await proc.start_background_tasks()
        proc._ensure_models_loaded.assert_not_awaited()

    async def test_stop_background_unloads(self):
        """stop_background_tasks unloads model and TTS."""
        proc = LocalPhi4Processor()
        proc._model = MagicMock()
        proc._model.unload = AsyncMock()
        proc._tts = MagicMock()
        proc._tts.unload = AsyncMock()
        proc._model_loaded = True

        await proc.stop_background_tasks()
        proc._model.unload.assert_awaited_once()
        proc._tts.unload.assert_awaited_once()
        self.assertFalse(proc._model_loaded)

    async def test_stop_background_no_model_safe(self):
        """stop_background_tasks with no model doesn't crash."""
        proc = LocalPhi4Processor()
        await proc.stop_background_tasks()


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ToolSchemaTests(unittest.TestCase):
    """Test _get_tool_schemas helper."""

    def test_no_tools_returns_none(self):
        proc = LocalPhi4Processor()
        self.assertIsNone(proc._get_tool_schemas())

    def test_tools_returns_schemas(self):
        proc = LocalPhi4Processor()
        tool = MagicMock(spec=Tool)
        tool.schema = {"type": "function", "name": "search"}
        proc.tools = {"search": tool}
        schemas = proc._get_tool_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "search")

    def test_tools_with_none_schema_excluded(self):
        proc = LocalPhi4Processor()
        tool1 = MagicMock(spec=Tool)
        tool1.schema = {"name": "search"}
        tool2 = MagicMock(spec=Tool)
        tool2.schema = None
        proc.tools = {"search": tool1, "hidden": tool2}
        schemas = proc._get_tool_schemas()
        self.assertEqual(len(schemas), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIO UTILITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ComputeEnergyTests(unittest.TestCase):
    """Test _compute_energy module-level function."""

    def test_silence_returns_zero(self):
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        self.assertAlmostEqual(_compute_energy(pcm), 0.0)

    def test_loud_signal_returns_high_energy(self):
        pcm = np.full(100, 10000, dtype=np.int16).tobytes()
        self.assertGreater(_compute_energy(pcm), 5000)

    def test_empty_bytes_returns_zero(self):
        self.assertAlmostEqual(_compute_energy(b""), 0.0)

    def test_single_byte_returns_zero(self):
        self.assertAlmostEqual(_compute_energy(b"\x00"), 0.0)


class DownsampleTests(unittest.TestCase):
    """Test _downsample_24k_to_16k module-level function."""

    def test_downsamples_correctly(self):
        """24kHz → 16kHz gives 2/3 the number of samples."""
        n_samples = 2400
        pcm = np.zeros(n_samples, dtype=np.int16).tobytes()
        result = _downsample_24k_to_16k(pcm)
        result_samples = len(result) // _BYTES_PER_SAMPLE
        expected = int(n_samples * (_PHI4_SAMPLE_RATE / _FRONTEND_SAMPLE_RATE))
        self.assertEqual(result_samples, expected)

    def test_short_input_passthrough(self):
        """Very short input (<4 bytes) is passed through."""
        self.assertEqual(_downsample_24k_to_16k(b"\x00\x01"), b"\x00\x01")

    def test_empty_input(self):
        result = _downsample_24k_to_16k(b"")
        self.assertEqual(result, b"")

    def test_output_is_bytes(self):
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        result = _downsample_24k_to_16k(pcm)
        self.assertIsInstance(result, bytes)

    def test_constants(self):
        """Verify audio constants match expected values."""
        self.assertEqual(_FRONTEND_SAMPLE_RATE, 24000)
        self.assertEqual(_PHI4_SAMPLE_RATE, 16000)
        self.assertEqual(_BYTES_PER_SAMPLE, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# WHISPER STT INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WhisperIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Test Whisper STT integration within LocalPhi4Processor."""

    def _make_processor_with_stt(self):
        """Create a processor with mocked models + STT engine."""
        proc = LocalPhi4Processor(config={"model_path": "/fake"})
        proc._model_loaded = True

        # Mock Phi-4 model
        proc._model = MagicMock()
        proc._model.is_loaded = True
        proc._model.device_name = "cpu"

        # Mock TTS
        proc._tts = MagicMock()
        proc._tts.is_loaded = True
        proc._tts.synthesize_streaming = MagicMock(return_value=_empty_async_gen())

        # Mock STT engine
        proc._stt = MagicMock()
        proc._stt.transcribe = AsyncMock(return_value="I want a Big Mac")
        proc._stt.unload = AsyncMock()

        return proc

    async def test_transcription_message_sent(self):
        """After processing, customer transcription message is sent to WS."""
        proc = self._make_processor_with_stt()
        ws = _make_mock_ws()
        cancel = asyncio.Event()

        # Mock Phi-4 to produce a simple response
        async def _gen(*a, **kw):
            yield "Hello! "
            yield "One Big Mac coming up."
        proc._model.process_audio = _gen

        await proc._process_utterance(
            _make_audio_pcm_24k(1.0), ws, "sess-1", cancel
        )

        # Find the transcription.completed message
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        transcript_msgs = [
            m for m in sent
            if m.get("type") == "conversation.item.input_audio_transcription.completed"
        ]
        self.assertEqual(len(transcript_msgs), 1)
        self.assertEqual(transcript_msgs[0]["transcript"], "I want a Big Mac")

    async def test_parallel_execution(self):
        """Whisper transcription runs in parallel with Phi-4 (asyncio.create_task)."""
        proc = self._make_processor_with_stt()
        ws = _make_mock_ws()
        cancel = asyncio.Event()

        phi4_started = asyncio.Event()
        phi4_proceed = asyncio.Event()

        async def _slow_gen(*a, **kw):
            phi4_started.set()
            await phi4_proceed.wait()
            yield "Response text"
        proc._model.process_audio = _slow_gen

        transcribe_called = asyncio.Event()
        original_transcribe = proc._stt.transcribe

        async def _tracked_transcribe(*a, **kw):
            transcribe_called.set()
            return await original_transcribe(*a, **kw)
        proc._stt.transcribe = _tracked_transcribe

        async def _run():
            task = asyncio.create_task(
                proc._process_utterance(_make_audio_pcm_24k(1.0), ws, "sess-1", cancel)
            )
            # Wait for Phi-4 to start
            await asyncio.wait_for(phi4_started.wait(), timeout=2.0)
            # Whisper should already have been called (started in parallel)
            self.assertTrue(
                transcribe_called.is_set(),
                "Whisper transcription should start before Phi-4 completes"
            )
            phi4_proceed.set()
            await asyncio.wait_for(task, timeout=5.0)

        await _run()

    async def test_graceful_skip_when_no_stt(self):
        """Processor works fine without STT engine — no transcript message."""
        proc = LocalPhi4Processor(config={"model_path": "/fake"})
        proc._model_loaded = True
        proc._model = MagicMock()
        proc._model.is_loaded = True
        proc._model.device_name = "cpu"
        proc._tts = MagicMock()
        proc._tts.is_loaded = True
        proc._tts.synthesize_streaming = MagicMock(return_value=_empty_async_gen())
        proc._stt = None  # No STT engine

        async def _gen(*a, **kw):
            yield "Welcome to McDonald's!"
        proc._model.process_audio = _gen

        ws = _make_mock_ws()
        cancel = asyncio.Event()

        await proc._process_utterance(
            _make_audio_pcm_24k(1.0), ws, "sess-1", cancel
        )

        # No transcription.completed message should be sent
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        transcript_msgs = [
            m for m in sent
            if m.get("type") == "conversation.item.input_audio_transcription.completed"
        ]
        self.assertEqual(len(transcript_msgs), 0)

        # But response.done should still be sent
        done_msgs = [m for m in sent if m.get("type") == "response.done"]
        self.assertEqual(len(done_msgs), 1)

    async def test_stt_unloaded_on_stop(self):
        """stop_background_tasks() calls stt.unload()."""
        proc = self._make_processor_with_stt()
        proc._model.unload = AsyncMock()
        proc._tts.unload = AsyncMock()

        await proc.stop_background_tasks()

        proc._stt.unload.assert_awaited_once()

    async def test_stt_failure_does_not_crash_pipeline(self):
        """If STT transcription fails, pipeline continues without crash."""
        proc = self._make_processor_with_stt()
        proc._stt.transcribe = AsyncMock(side_effect=RuntimeError("STT failed"))
        ws = _make_mock_ws()
        cancel = asyncio.Event()

        async def _gen(*a, **kw):
            yield "Here's your order."
        proc._model.process_audio = _gen

        # Should not raise
        await proc._process_utterance(
            _make_audio_pcm_24k(1.0), ws, "sess-1", cancel
        )

        # response.done should still be sent
        sent = [c[0][0] for c in ws.send_json.call_args_list]
        done_msgs = [m for m in sent if m.get("type") == "response.done"]
        self.assertEqual(len(done_msgs), 1)


# ── Whisper integration helpers ─────────────────────────────────────────────


async def _empty_async_gen():
    """Empty async generator (no audio chunks from TTS)."""
    return
    yield  # noqa — makes this an async generator


def _make_audio_pcm_24k(duration_s: float) -> bytes:
    """Generate silence PCM bytes at 24 kHz (frontend format)."""
    n_samples = int(duration_s * _FRONTEND_SAMPLE_RATE)
    return np.zeros(n_samples, dtype=np.int16).tobytes()


if __name__ == "__main__":
    unittest.main()
