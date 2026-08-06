"""Tests for GA session translation (_to_ga_session) and semantic ranker gating."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rtmt import _to_ga_session  # noqa: E402
from tools import search  # noqa: E402


class GASessionTranslationTests(unittest.TestCase):
    """Tests for _to_ga_session() — ensures legacy session configs are translated to GA shape."""

    def test_adds_type_realtime(self):
        """GA session must always include type='realtime'."""
        result = _to_ga_session({})
        self.assertEqual(result["type"], "realtime")

    def test_voice_moved_to_audio_output(self):
        """voice → audio.output.voice"""
        result = _to_ga_session({"voice": "ash"})
        self.assertEqual(result["audio"]["output"]["voice"], "ash")
        self.assertNotIn("voice", result)

    def test_turn_detection_moved_to_audio_input(self):
        """turn_detection → audio.input.turn_detection"""
        td = {"type": "server_vad", "threshold": 0.5}
        result = _to_ga_session({"turn_detection": td})
        self.assertEqual(result["audio"]["input"]["turn_detection"], td)
        self.assertNotIn("turn_detection", result)

    def test_input_audio_transcription_moved(self):
        """input_audio_transcription → audio.input.transcription"""
        trans = {"model": "whisper-1"}
        result = _to_ga_session({"input_audio_transcription": trans})
        self.assertEqual(result["audio"]["input"]["transcription"], trans)
        self.assertNotIn("input_audio_transcription", result)

    def test_input_audio_format_string_to_object(self):
        """input_audio_format (string like 'pcm16') → audio.input.format (object)."""
        result = _to_ga_session({"input_audio_format": "pcm16"})
        fmt = result["audio"]["input"]["format"]
        self.assertEqual(fmt["type"], "audio/pcm")
        self.assertEqual(fmt["rate"], 24000)
        self.assertNotIn("input_audio_format", result)

    def test_output_audio_format_string_to_object(self):
        """output_audio_format → audio.output.format (object)."""
        result = _to_ga_session({"output_audio_format": "pcm16"})
        fmt = result["audio"]["output"]["format"]
        self.assertEqual(fmt["type"], "audio/pcm")
        self.assertEqual(fmt["rate"], 24000)
        self.assertNotIn("output_audio_format", result)

    def test_max_response_output_tokens_renamed(self):
        """max_response_output_tokens → max_output_tokens"""
        result = _to_ga_session({"max_response_output_tokens": 4096})
        self.assertEqual(result["max_output_tokens"], 4096)
        self.assertNotIn("max_response_output_tokens", result)

    def test_modalities_renamed(self):
        """modalities → output_modalities"""
        result = _to_ga_session({"modalities": ["text", "audio"]})
        self.assertEqual(result["output_modalities"], ["text", "audio"])
        self.assertNotIn("modalities", result)

    def test_temperature_dropped(self):
        """temperature is not a valid GA session key — must be dropped."""
        result = _to_ga_session({"temperature": 0.8})
        self.assertNotIn("temperature", result)

    def test_disable_audio_dropped(self):
        """disable_audio is not a valid GA key — must be dropped."""
        result = _to_ga_session({"disable_audio": True})
        self.assertNotIn("disable_audio", result)

    def test_tools_preserved(self):
        """tools list is preserved at the top level."""
        tools = [{"type": "function", "name": "search"}]
        result = _to_ga_session({"tools": tools})
        self.assertEqual(result["tools"], tools)

    def test_instructions_preserved(self):
        """instructions are preserved at top level."""
        result = _to_ga_session({"instructions": "You are a helpful assistant."})
        self.assertEqual(result["instructions"], "You are a helpful assistant.")

    def test_combined_session(self):
        """A full session with multiple fields translates correctly."""
        legacy = {
            "voice": "coral",
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "modalities": ["text", "audio"],
            "max_response_output_tokens": 2048,
            "temperature": 0.7,
            "instructions": "Test",
            "tools": [{"type": "function", "name": "t"}],
        }
        result = _to_ga_session(legacy)
        self.assertEqual(result["type"], "realtime")
        self.assertEqual(result["audio"]["output"]["voice"], "coral")
        self.assertEqual(result["audio"]["input"]["turn_detection"], {"type": "server_vad"})
        self.assertEqual(result["output_modalities"], ["text", "audio"])
        self.assertEqual(result["max_output_tokens"], 2048)
        self.assertEqual(result["instructions"], "Test")
        self.assertNotIn("temperature", result)
        self.assertNotIn("voice", result)
        self.assertNotIn("modalities", result)


class SemanticRankerGatingTests(unittest.TestCase):
    """Tests for semantic ranker gating in search()."""

    def _make_mock_client(self, results=None):
        """Create a mock SearchClient that returns async results."""
        if results is None:
            results = []

        class AsyncResultIter:
            def __init__(self, items):
                self._items = items
                self._index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._index >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._index]
                self._index += 1
                return item

        search_calls: list[dict] = []

        class FakeClient:
            _search_calls = search_calls

            async def search(self, **kwargs):
                search_calls.append(kwargs)
                return AsyncResultIter(results)

        return FakeClient()

    def test_semantic_ranker_enabled_uses_semantic_query_type(self):
        """When use_semantic_ranker=True, query_type should be 'semantic'."""
        client = self._make_mock_client()
        asyncio.run(search(client, "cfg", "id", "description", "embedding", False, {"query": "semantic enabled test"}, use_semantic_ranker=True))
        call_kwargs = client._search_calls[0]
        self.assertEqual(call_kwargs["query_type"], "semantic")
        self.assertEqual(call_kwargs["semantic_configuration_name"], "cfg")

    def test_semantic_ranker_disabled_uses_simple_query_type(self):
        """When use_semantic_ranker=False, query_type should be 'simple'."""
        client = self._make_mock_client()
        asyncio.run(search(client, "cfg", "id", "description", "embedding", False, {"query": "semantic disabled test"}, use_semantic_ranker=False))
        call_kwargs = client._search_calls[0]
        self.assertEqual(call_kwargs["query_type"], "simple")
        self.assertIsNone(call_kwargs["semantic_configuration_name"])

    def test_default_uses_semantic(self):
        """Default (no use_semantic_ranker kwarg) defaults to semantic."""
        client = self._make_mock_client()
        asyncio.run(search(client, "cfg", "id", "description", "embedding", False, {"query": "default semantic test"}))
        call_kwargs = client._search_calls[0]
        self.assertEqual(call_kwargs["query_type"], "semantic")


if __name__ == "__main__":
    unittest.main()
