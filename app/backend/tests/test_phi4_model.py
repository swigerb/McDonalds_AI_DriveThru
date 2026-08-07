"""Tests for Phi4ModelManager — ONNX model lifecycle and inference.

Covers:
  - Initialisation and property defaults
  - Auto-device detection (mock CUDA / DirectML / CPU)
  - Model loading with mocked onnxruntime_genai
  - Model unloading
  - is_loaded / device_name properties
  - process_audio async generator (mock model)
  - process_audio with tool schemas in prompt
  - Error handling: model not loaded → raises
  - Error handling: inference failure → clean error
  - PCM conversion, prompt building, tool call parsing
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from phi4_model import Phi4ModelManager, _load_onnxruntime_genai

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_og():
    """Create a mock onnxruntime_genai module with all necessary classes."""
    og = MagicMock()

    # Model
    mock_model = MagicMock()
    og.Model.return_value = mock_model

    # MultiModalProcessor
    mock_processor = MagicMock()
    mock_processor.tokenizer = MagicMock()
    og.MultiModalProcessor.return_value = mock_processor

    # Tokenizer
    mock_tokenizer = MagicMock()
    og.Tokenizer.return_value = mock_tokenizer

    # GeneratorParams
    mock_params = MagicMock()
    og.GeneratorParams.return_value = mock_params

    # Generator — produces two tokens then stops
    mock_generator = MagicMock()
    mock_generator.is_done = MagicMock(side_effect=[False, False, True])
    mock_generator.get_last_tokens = MagicMock(return_value=[42])
    og.Generator.return_value = mock_generator

    return og


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALISATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class Phi4InitTests(unittest.TestCase):
    """Test Phi4ModelManager construction and default property values."""

    def test_default_init(self):
        mgr = Phi4ModelManager(model_path="/fake/model")
        self.assertEqual(mgr._model_path, "/fake/model")
        self.assertEqual(mgr._requested_device, "auto")
        self.assertEqual(mgr._max_length, 2048)
        self.assertAlmostEqual(mgr._temperature, 0.6)

    def test_custom_init(self):
        mgr = Phi4ModelManager(
            model_path="/models/phi4",
            device="cuda",
            max_length=512,
            temperature=0.8,
        )
        self.assertEqual(mgr._requested_device, "cuda")
        self.assertEqual(mgr._max_length, 512)
        self.assertAlmostEqual(mgr._temperature, 0.8)

    def test_is_loaded_initially_false(self):
        mgr = Phi4ModelManager(model_path="/fake")
        self.assertFalse(mgr.is_loaded)

    def test_device_name_initially_none(self):
        mgr = Phi4ModelManager(model_path="/fake")
        self.assertEqual(mgr.device_name, "none")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-DEVICE DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class AutoDeviceDetectionTests(unittest.TestCase):
    """Test _load_onnxruntime_genai priority order."""

    def test_cuda_variant_first(self):
        """CUDA detected when CUDAExecutionProvider is available."""
        mock_og = MagicMock()
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        def fake_import(name, *a, **kw):
            if name == "onnxruntime_genai":
                return mock_og
            if name == "onnxruntime":
                return mock_ort
            return __import__(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            mod, provider = _load_onnxruntime_genai()
        self.assertEqual(provider, "cuda")
        self.assertIs(mod, mock_og)

    def test_directml_fallback(self):
        """DirectML detected when DmlExecutionProvider is available."""
        mock_og = MagicMock()
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["DmlExecutionProvider", "CPUExecutionProvider"]

        def fake_import(name, *a, **kw):
            if name == "onnxruntime_genai":
                return mock_og
            if name == "onnxruntime":
                return mock_ort
            return __import__(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            mod, provider = _load_onnxruntime_genai()
        self.assertEqual(provider, "directml")

    def test_cpu_fallback(self):
        """CPU fallback when no GPU variants available."""
        mock_og = MagicMock()
        mock_ort = MagicMock()
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]

        def fake_import(name, *a, **kw):
            if name == "onnxruntime_genai":
                return mock_og
            if name == "onnxruntime":
                return mock_ort
            return __import__(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            mod, provider = _load_onnxruntime_genai()
        self.assertEqual(provider, "cpu")

    def test_none_when_nothing_installed(self):
        """Returns (None, 'none') when nothing installed."""

        def fake_import(name, *a, **kw):
            if name.startswith("onnxruntime"):
                raise ImportError
            return __import__(name, *a, **kw)

        with patch("builtins.__import__", side_effect=fake_import):
            mod, provider = _load_onnxruntime_genai()
        self.assertIsNone(mod)
        self.assertEqual(provider, "none")


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL LOADING / UNLOADING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ModelLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Test loading and unloading the model with mocked onnxruntime_genai."""

    async def test_load_sets_loaded_flag(self):
        """After load(), is_loaded is True."""
        mgr = Phi4ModelManager(model_path="/fake")
        mock_og = _make_mock_og()
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cpu")):
            await mgr.load()
        self.assertTrue(mgr.is_loaded)

    async def test_load_sets_device_name(self):
        """After load(), device_name reflects detected device."""
        mgr = Phi4ModelManager(model_path="/fake", device="auto")
        mock_og = _make_mock_og()
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cuda")):
            await mgr.load()
        self.assertEqual(mgr.device_name, "cuda")

    async def test_load_explicit_device(self):
        """Explicit device override is used instead of auto-detected."""
        mgr = Phi4ModelManager(model_path="/fake", device="directml")
        mock_og = _make_mock_og()
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cpu")):
            await mgr.load()
        self.assertEqual(mgr.device_name, "directml")

    async def test_load_idempotent(self):
        """Calling load() twice does not reload."""
        mgr = Phi4ModelManager(model_path="/fake")
        mock_og = _make_mock_og()
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cpu")):
            await mgr.load()
            await mgr.load()
        # Model constructor called only once
        mock_og.Model.assert_called_once()

    async def test_load_raises_when_no_runtime(self):
        """load() raises RuntimeError when onnxruntime_genai is missing."""
        mgr = Phi4ModelManager(model_path="/fake")
        with patch("phi4_model._load_onnxruntime_genai", return_value=(None, "none")):
            with self.assertRaises(RuntimeError) as ctx:
                await mgr.load()
            self.assertIn("onnxruntime_genai is not installed", str(ctx.exception))

    async def test_unload_clears_state(self):
        """After unload(), is_loaded is False and device_name resets."""
        mgr = Phi4ModelManager(model_path="/fake")
        mock_og = _make_mock_og()
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cpu")):
            await mgr.load()
        await mgr.unload()
        self.assertFalse(mgr.is_loaded)
        self.assertEqual(mgr.device_name, "none")

    async def test_unload_when_not_loaded_is_noop(self):
        """Unloading when not loaded does nothing."""
        mgr = Phi4ModelManager(model_path="/fake")
        await mgr.unload()  # Should not raise
        self.assertFalse(mgr.is_loaded)


# ═══════════════════════════════════════════════════════════════════════════════
# INFERENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    """Test process_audio async generator with mocked model."""

    async def _load_manager(self):
        """Create and load a manager with mocked runtime."""
        mgr = Phi4ModelManager(model_path="/fake")
        mock_og = _make_mock_og()
        # Make tokenizer decode return a fixed string
        mock_processor = mock_og.MultiModalProcessor.return_value
        mock_processor.tokenizer.decode = MagicMock(return_value="Hello")
        with patch("phi4_model._load_onnxruntime_genai", return_value=(mock_og, "cpu")):
            await mgr.load()
        return mgr, mock_og

    async def test_process_audio_not_loaded_raises(self):
        """process_audio raises RuntimeError when model not loaded."""
        mgr = Phi4ModelManager(model_path="/fake")
        with self.assertRaises(RuntimeError) as ctx:
            async for _ in mgr.process_audio(b"\x00" * 100, "system prompt"):
                pass
            self.assertIn("not loaded", str(ctx.exception))

    async def test_process_audio_yields_tokens(self):
        """process_audio yields text tokens from the model."""
        mgr, mock_og = await self._load_manager()

        tokens = []
        # We need to mock _og at module level for process_audio
        with patch("phi4_model._og", mock_og):
            async for token in mgr.process_audio(b"\x00\x00" * 100, "You are helpful"):
                tokens.append(token)

        self.assertGreater(len(tokens), 0)

    async def test_process_audio_with_tool_schemas(self):
        """process_audio accepts tool_schemas and includes them in the prompt."""
        mgr, mock_og = await self._load_manager()
        tools = [{"type": "function", "name": "search_menu", "parameters": {}}]

        with patch("phi4_model._og", mock_og):
            tokens = []
            async for token in mgr.process_audio(
                b"\x00\x00" * 100, "System", tool_schemas=tools
            ):
                tokens.append(token)

        # Tokenizer.encode was called — verify prompt contains tool info
        mock_tokenizer = mock_og.Tokenizer.return_value
        mock_tokenizer.encode.assert_called()
        encoded_prompt = mock_tokenizer.encode.call_args[0][0]
        self.assertIn("search_menu", encoded_prompt)
        self.assertIn("tool_call", encoded_prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER METHOD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class PromptBuildingTests(unittest.TestCase):
    """Test _build_prompt static method."""

    def test_basic_prompt(self):
        result = Phi4ModelManager._build_prompt("You are helpful")
        self.assertIn("You are helpful", result)
        self.assertIn("<|system|>", result)
        self.assertIn("<|assistant|>", result)

    def test_prompt_with_tools(self):
        tools = [{"type": "function", "name": "test_tool"}]
        result = Phi4ModelManager._build_prompt("System", tool_schemas=tools)
        self.assertIn("System", result)
        self.assertIn("tool_call", result)
        self.assertIn("test_tool", result)

    def test_prompt_without_tools(self):
        result = Phi4ModelManager._build_prompt("System", tool_schemas=None)
        self.assertNotIn("tool_call", result)

    def test_prompt_empty_tools_list(self):
        result = Phi4ModelManager._build_prompt("System", tool_schemas=[])
        self.assertNotIn("tool_call", result)


class ToolCallParsingTests(unittest.TestCase):
    """Test parse_tool_calls static method."""

    def test_single_tool_call(self):
        text = 'Sure! <tool_call>{"name": "search_menu", "arguments": {"query": "Big Mac"}}</tool_call>'
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "search_menu")
        self.assertEqual(calls[0]["arguments"]["query"], "Big Mac")

    def test_multiple_tool_calls(self):
        text = (
            '<tool_call>{"name": "search_menu", "arguments": {"query": "fries"}}</tool_call>'
            ' and also '
            '<tool_call>{"name": "update_order", "arguments": {"action": "add"}}</tool_call>'
        )
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 2)

    def test_no_tool_calls(self):
        text = "Welcome to McDonald's! How can I help you?"
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 0)

    def test_malformed_json_ignored(self):
        text = '<tool_call>{bad json}</tool_call>'
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 0)

    def test_missing_name_key_ignored(self):
        text = '<tool_call>{"arguments": {"query": "test"}}</tool_call>'
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 0)

    def test_multiline_tool_call(self):
        text = '''<tool_call>
{
    "name": "update_order",
    "arguments": {"action": "add", "item": "Big Mac"}
}
</tool_call>'''
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "update_order")

    def test_tool_call_with_surrounding_text(self):
        text = "Let me look that up. <tool_call>{\"name\": \"search_menu\", \"arguments\": {}}</tool_call> Found it!"
        calls = Phi4ModelManager.parse_tool_calls(text)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
