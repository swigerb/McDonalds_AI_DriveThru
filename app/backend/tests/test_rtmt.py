"""Tests for the real-time middle tier (rtmt.py), session manager (session_manager.py),
and audio pipeline (audio_pipeline.py).

Covers WebSocket lifecycle, session management, message routing, echo suppression,
and error recovery — all with mocked external services (no real OpenAI/Azure calls).
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from order_state import order_state_singleton

# ── Imports under test ──
from session_manager import SessionManager, ContextMonitor
from audio_pipeline import (
    EchoSuppressor,
    ECHO_COOLDOWN_SEC,
    TYPE_RE,
    RESPONSE_CREATE_MSG,
    INPUT_AUDIO_CLEAR_MSG,
    _PASSTHROUGH_SERVER_TYPES,
    _PASSTHROUGH_CLIENT_TYPES,
)
from rtmt import (
    RTMiddleTier,
    Tool,
    ToolResult,
    ToolResultDirection,
    RTToolCall,
)


# ── Helpers ──

def _make_mock_ws():
    """Create a mock WebSocket response with required attributes."""
    ws = MagicMock(spec=web.WebSocketResponse)
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION MANAGER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class SessionManagerCreationTests(unittest.TestCase):
    """Test session creation, tracking, and ID uniqueness."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    def test_create_session_returns_uuid(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIsInstance(sid, str)
        self.assertGreater(len(sid), 10)

    def test_create_session_maps_ws_to_session(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertEqual(self.sm.get_session_id(ws), sid)

    def test_session_id_uniqueness(self):
        ids = set()
        for _ in range(20):
            ws = _make_mock_ws()
            sid = self.sm.create_session(ws)
            ids.add(sid)
        self.assertEqual(len(ids), 20)

    def test_active_session_count_tracks_correctly(self):
        self.assertEqual(self.sm.active_session_count, 0)
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        self.sm.create_session(ws1)
        self.assertEqual(self.sm.active_session_count, 1)
        self.sm.create_session(ws2)
        self.assertEqual(self.sm.active_session_count, 2)

    def test_get_session_id_for_unknown_ws_returns_none(self):
        ws = _make_mock_ws()
        self.assertIsNone(self.sm.get_session_id(ws))

    def test_create_session_creates_context_monitor(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        cm = self.sm.get_context_monitor(sid)
        self.assertIsNotNone(cm)
        self.assertIsInstance(cm, ContextMonitor)

    def test_create_session_initializes_activity_timestamp(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIn(sid, self.sm._last_activity)
        self.assertGreater(self.sm._last_activity[sid], 0)

    def test_create_session_registers_in_order_state(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIn(sid, order_state_singleton.sessions)


class SessionManagerCleanupTests(unittest.TestCase):
    """Test cleanup frees all resources."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    def test_cleanup_removes_session(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.sm.cleanup_session(ws, sid)
        self.assertEqual(self.sm.active_session_count, 0)
        self.assertIsNone(self.sm.get_session_id(ws))

    def test_cleanup_removes_order_state(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIn(sid, order_state_singleton.sessions)
        self.sm.cleanup_session(ws, sid)
        self.assertNotIn(sid, order_state_singleton.sessions)

    def test_cleanup_clears_greeting_state(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.sm.mark_greeting_sent(sid)
        self.assertTrue(self.sm.has_sent_greeting(sid))
        self.sm.cleanup_session(ws, sid)
        self.assertFalse(self.sm.has_sent_greeting(sid))

    def test_cleanup_clears_context_monitor(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIsNotNone(self.sm.get_context_monitor(sid))
        self.sm.cleanup_session(ws, sid)
        self.assertIsNone(self.sm.get_context_monitor(sid))

    def test_cleanup_with_none_session_id_is_safe(self):
        ws = _make_mock_ws()
        self.sm.cleanup_session(ws, None)  # should not raise

    def test_cleanup_removes_activity_timestamp(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertIn(sid, self.sm._last_activity)
        self.sm.cleanup_session(ws, sid)
        self.assertNotIn(sid, self.sm._last_activity)

    def test_cleanup_idempotent(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.sm.cleanup_session(ws, sid)
        self.sm.cleanup_session(ws, sid)  # second call should not raise
        self.assertEqual(self.sm.active_session_count, 0)

    def test_cleanup_one_session_keeps_others(self):
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        sid1 = self.sm.create_session(ws1)
        sid2 = self.sm.create_session(ws2)
        self.sm.cleanup_session(ws1, sid1)
        self.assertEqual(self.sm.active_session_count, 1)
        self.assertEqual(self.sm.get_session_id(ws2), sid2)


class SessionManagerConcurrencyTests(unittest.TestCase):
    """Test concurrent session limits."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 3)
    def test_can_accept_session_within_limit(self):
        sm = SessionManager()
        for _ in range(3):
            ws = _make_mock_ws()
            sm.create_session(ws)
        self.assertFalse(sm.can_accept_session())

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 3)
    def test_can_accept_session_after_cleanup(self):
        sm = SessionManager()
        sessions = []
        for _ in range(3):
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            sessions.append((ws, sid))
        self.assertFalse(sm.can_accept_session())
        sm.cleanup_session(*sessions[0])
        self.assertTrue(sm.can_accept_session())

    def test_multiple_concurrent_connections_independent(self):
        ws1, ws2 = _make_mock_ws(), _make_mock_ws()
        sid1 = self.sm.create_session(ws1)
        sid2 = self.sm.create_session(ws2)
        self.assertNotEqual(sid1, sid2)
        self.assertEqual(self.sm.get_session_id(ws1), sid1)
        self.assertEqual(self.sm.get_session_id(ws2), sid2)

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 1)
    def test_single_session_limit(self):
        sm = SessionManager()
        ws = _make_mock_ws()
        sm.create_session(ws)
        self.assertFalse(sm.can_accept_session())

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 3)
    def test_can_accept_initially_true(self):
        sm = SessionManager()
        self.assertTrue(sm.can_accept_session())

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 2)
    def test_accept_after_full_cleanup_cycle(self):
        sm = SessionManager()
        pairs = []
        for _ in range(2):
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            pairs.append((ws, sid))
        self.assertFalse(sm.can_accept_session())
        for ws, sid in pairs:
            sm.cleanup_session(ws, sid)
        self.assertTrue(sm.can_accept_session())
        self.assertEqual(sm.active_session_count, 0)


class SessionManagerGreetingTests(unittest.TestCase):
    """Test greeting state tracking per session."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    def test_greeting_not_sent_initially(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.assertFalse(self.sm.has_sent_greeting(sid))

    def test_mark_greeting_sent(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.sm.mark_greeting_sent(sid)
        self.assertTrue(self.sm.has_sent_greeting(sid))

    def test_greeting_state_per_session(self):
        ws1, ws2 = _make_mock_ws(), _make_mock_ws()
        sid1 = self.sm.create_session(ws1)
        sid2 = self.sm.create_session(ws2)
        self.sm.mark_greeting_sent(sid1)
        self.assertTrue(self.sm.has_sent_greeting(sid1))
        self.assertFalse(self.sm.has_sent_greeting(sid2))

    def test_greeting_msg_default(self):
        msg = json.loads(self.sm.greeting_msg)
        self.assertEqual(msg["type"], "conversation.item.create")

    def test_greeting_msg_from_prompt_loader(self):
        loader = MagicMock()
        loader.get_greeting_json_str.return_value = '{"type":"custom_greeting"}'
        sm = SessionManager(prompt_loader=loader)
        self.assertEqual(sm.greeting_msg, '{"type":"custom_greeting"}')

    def test_greeting_msg_default_contains_mcdoanlds(self):
        msg = json.loads(self.sm.greeting_msg)
        content = msg["item"]["content"][0]["text"]
        self.assertIn("McDonald", content)

    def test_mark_greeting_sent_idempotent(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        self.sm.mark_greeting_sent(sid)
        self.sm.mark_greeting_sent(sid)  # second call should not raise
        self.assertTrue(self.sm.has_sent_greeting(sid))


class SessionManagerIdleTimeoutTests(unittest.TestCase):
    """Test idle timeout detection with mocked time."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    def test_touch_activity_updates_timestamp(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        t1 = self.sm._last_activity[sid]
        time.sleep(0.05)
        self.sm.touch_activity(sid)
        t2 = self.sm._last_activity[sid]
        self.assertGreaterEqual(t2, t1)

    def test_idle_session_closed(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 0):
            sm = SessionManager()
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            sm._last_activity[sid] = time.monotonic() - 10
            asyncio.run(sm.close_idle_sessions())
            ws.close.assert_called_once()
            self.assertEqual(sm.active_session_count, 0)

    def test_active_session_not_cleaned(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 9999):
            sm = SessionManager()
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            sm.touch_activity(sid)
            asyncio.run(sm.close_idle_sessions())
            ws.close.assert_not_called()
            self.assertEqual(sm.active_session_count, 1)

    def test_multiple_idle_sessions_all_closed(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 0):
            sm = SessionManager()
            sessions = []
            for _ in range(3):
                ws = _make_mock_ws()
                sid = sm.create_session(ws)
                sm._last_activity[sid] = time.monotonic() - 10
                sessions.append(ws)
            asyncio.run(sm.close_idle_sessions())
            for ws in sessions:
                ws.close.assert_called_once()
            self.assertEqual(sm.active_session_count, 0)

    def test_mixed_idle_and_active_sessions(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 5):
            sm = SessionManager()
            ws_idle = _make_mock_ws()
            ws_active = _make_mock_ws()
            sid_idle = sm.create_session(ws_idle)
            sid_active = sm.create_session(ws_active)
            sm._last_activity[sid_idle] = time.monotonic() - 10
            sm.touch_activity(sid_active)
            asyncio.run(sm.close_idle_sessions())
            ws_idle.close.assert_called_once()
            ws_active.close.assert_not_called()
            self.assertEqual(sm.active_session_count, 1)


class SessionManagerEmitIdentifiersTests(unittest.IsolatedAsyncioTestCase):
    """Test session identifier emission."""

    def setUp(self):
        order_state_singleton.sessions = {}
        self.sm = SessionManager()

    async def test_emit_session_identifiers(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        identifiers = order_state_singleton.get_session_identifiers(sid)
        await self.sm.emit_session_identifiers(ws, "extension.session_metadata", identifiers)
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        self.assertEqual(payload["type"], "extension.session_metadata")
        self.assertIn("sessionToken", payload)

    async def test_emit_none_identifiers_is_noop(self):
        ws = _make_mock_ws()
        await self.sm.emit_session_identifiers(ws, "test", None)
        ws.send_json.assert_not_called()

    async def test_emit_identifiers_contains_round_trip(self):
        ws = _make_mock_ws()
        sid = self.sm.create_session(ws)
        identifiers = order_state_singleton.get_session_identifiers(sid)
        await self.sm.emit_session_identifiers(ws, "test", identifiers)
        payload = ws.send_json.call_args[0][0]
        self.assertIn("roundTripIndex", payload)
        self.assertIn("roundTripToken", payload)
        self.assertEqual(payload["roundTripIndex"], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT MONITOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ContextMonitorTests(unittest.TestCase):
    """Test context window token estimation and threshold warnings."""

    def test_initial_state(self):
        cm = ContextMonitor("test-session")
        self.assertEqual(cm.estimated_tokens, 0)
        self.assertAlmostEqual(cm.usage_pct, 0.0)

    def test_add_content_increases_token_estimate(self):
        cm = ContextMonitor("test-session")
        cm.add_content("a" * 400)  # ~100 tokens
        self.assertEqual(cm.estimated_tokens, 100)

    def test_add_empty_content_is_safe(self):
        cm = ContextMonitor("test-session")
        cm.add_content("")
        cm.add_content(None)
        self.assertEqual(cm.estimated_tokens, 0)

    def test_warning_threshold_logged(self):
        with patch("session_manager._CTX_MAX_TOKENS", 100):
            cm = ContextMonitor("test-session")
            with self.assertLogs("mcdonalds-drive-thru", level="WARNING") as log:
                cm.add_content("a" * 328)  # 82 tokens = 82% of 100 max
            self.assertTrue(any("WARNING" in m for m in log.output))

    def test_critical_threshold_logged(self):
        with patch("session_manager._CTX_MAX_TOKENS", 100):
            cm = ContextMonitor("test-session")
            with self.assertLogs("mcdonalds-drive-thru", level="WARNING") as log:
                cm.add_content("a" * 400)  # 100 tokens = 100% of 100 max
            self.assertTrue(any("CRITICAL" in m for m in log.output))

    def test_multiple_add_content_accumulates(self):
        cm = ContextMonitor("test-session")
        cm.add_content("a" * 100)  # 25 tokens
        cm.add_content("b" * 100)  # 25 tokens
        self.assertEqual(cm.estimated_tokens, 50)

    def test_usage_pct_calculation(self):
        with patch("session_manager._CTX_MAX_TOKENS", 1000):
            cm = ContextMonitor("test-session")
            cm.add_content("a" * 2000)  # 500 tokens
            self.assertAlmostEqual(cm.usage_pct, 50.0)

    def test_warning_only_fires_once(self):
        with patch("session_manager._CTX_MAX_TOKENS", 100):
            cm = ContextMonitor("test-session")
            with self.assertLogs("mcdonalds-drive-thru", level="WARNING"):
                cm.add_content("a" * 328)  # triggers warning
            # Second add shouldn't re-trigger
            cm.add_content("a" * 4)
            self.assertTrue(cm._warned_warning)

    def test_critical_also_sets_warning_flag(self):
        with patch("session_manager._CTX_MAX_TOKENS", 100):
            cm = ContextMonitor("test-session")
            with self.assertLogs("mcdonalds-drive-thru", level="WARNING"):
                cm.add_content("a" * 400)
            self.assertTrue(cm._warned_warning)
            self.assertTrue(cm._warned_critical)

    def test_zero_max_tokens_returns_zero_pct(self):
        with patch("session_manager._CTX_MAX_TOKENS", 0):
            cm = ContextMonitor("test-session")
            cm.add_content("a" * 100)
            self.assertEqual(cm.usage_pct, 0.0)

    def test_session_id_stored(self):
        cm = ContextMonitor("my-session-123")
        self.assertEqual(cm.session_id, "my-session-123")

    def test_get_context_monitor_returns_none_for_none_session(self):
        sm = SessionManager()
        self.assertIsNone(sm.get_context_monitor(None))


# ═══════════════════════════════════════════════════════════════════════════════
# ECHO SUPPRESSOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class EchoSuppressorTests(unittest.TestCase):
    """Test the echo suppression state machine."""

    def test_initial_state_not_suppressing(self):
        echo = EchoSuppressor()
        self.assertFalse(echo.ai_speaking)
        self.assertFalse(echo.greeting_in_progress)
        self.assertFalse(echo.should_suppress_audio(0.0))

    def test_audio_delta_activates_suppression(self):
        echo = EchoSuppressor()
        echo.on_audio_delta()
        self.assertTrue(echo.ai_speaking)
        self.assertTrue(echo.should_suppress_audio(0.0))

    def test_audio_done_deactivates_speaking_starts_cooldown(self):
        async def _run():
            echo = EchoSuppressor()
            echo.on_audio_delta()
            loop = asyncio.get_running_loop()
            target_ws = MagicMock()
            target_ws.closed = False
            target_ws.send_str = AsyncMock()
            t = loop.time()
            echo.on_audio_done(loop, target_ws)
            self.assertFalse(echo.ai_speaking)
            self.assertAlmostEqual(echo.cooldown_end, t + ECHO_COOLDOWN_SEC, delta=0.1)
        asyncio.run(_run())

    def test_cooldown_suppresses_audio(self):
        echo = EchoSuppressor()
        echo.cooldown_end = 200.0
        self.assertTrue(echo.should_suppress_audio(199.0))
        self.assertFalse(echo.should_suppress_audio(201.0))

    def test_cooldown_exact_boundary_not_suppressing(self):
        echo = EchoSuppressor()
        echo.cooldown_end = 200.0
        # At exactly the boundary, loop_time == cooldown_end → not suppressed
        self.assertFalse(echo.should_suppress_audio(200.0))

    def test_speech_started_resets_suppression(self):
        echo = EchoSuppressor()
        echo.on_audio_delta()
        self.assertTrue(echo.ai_speaking)
        ignored = echo.on_speech_started()
        self.assertFalse(ignored)
        self.assertFalse(echo.ai_speaking)
        self.assertEqual(echo.cooldown_end, 0.0)

    def test_speech_started_during_greeting_is_ignored(self):
        echo = EchoSuppressor()
        echo.start_greeting_suppression()
        self.assertTrue(echo.greeting_in_progress)
        ignored = echo.on_speech_started()
        self.assertTrue(ignored)

    def test_barge_in_resets_all_suppression(self):
        echo = EchoSuppressor()
        echo.on_audio_delta()
        echo.cooldown_end = 999.0
        echo.on_barge_in()
        self.assertFalse(echo.ai_speaking)
        self.assertEqual(echo.cooldown_end, 0.0)

    def test_greeting_suppression_doubles_cooldown(self):
        async def _run():
            echo = EchoSuppressor()
            echo.start_greeting_suppression()
            loop = asyncio.get_running_loop()
            target_ws = MagicMock()
            target_ws.closed = False
            target_ws.send_str = AsyncMock()
            t = loop.time()
            echo.on_audio_done(loop, target_ws)
            expected_cooldown = ECHO_COOLDOWN_SEC * 2
            self.assertAlmostEqual(echo.cooldown_end, t + expected_cooldown, delta=0.1)
            self.assertFalse(echo.greeting_in_progress)  # reset after done
        asyncio.run(_run())

    def test_start_greeting_suppression_sets_state(self):
        echo = EchoSuppressor()
        echo.start_greeting_suppression()
        self.assertTrue(echo.ai_speaking)
        self.assertTrue(echo.greeting_in_progress)

    def test_audio_done_sends_clear_to_openai(self):
        async def _run():
            echo = EchoSuppressor()
            loop = asyncio.get_running_loop()
            target_ws = MagicMock()
            target_ws.closed = False
            target_ws.send_str = AsyncMock()
            echo.on_audio_done(loop, target_ws)
            await asyncio.sleep(0)
            target_ws.send_str.assert_called()
        asyncio.run(_run())

    def test_multiple_audio_deltas_stay_suppressing(self):
        echo = EchoSuppressor()
        echo.on_audio_delta()
        echo.on_audio_delta()
        echo.on_audio_delta()
        self.assertTrue(echo.ai_speaking)

    def test_barge_in_after_greeting_resets(self):
        echo = EchoSuppressor()
        echo.start_greeting_suppression()
        echo.on_barge_in()
        self.assertFalse(echo.ai_speaking)
        self.assertEqual(echo.cooldown_end, 0.0)

    def test_should_suppress_while_speaking_regardless_of_cooldown(self):
        echo = EchoSuppressor()
        echo.ai_speaking = True
        echo.cooldown_end = 0.0
        self.assertTrue(echo.should_suppress_audio(999.0))


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIO PIPELINE UTILITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TypeRegexTests(unittest.TestCase):
    """Test the TYPE_RE regex used for fast message routing."""

    def test_extracts_type_from_json(self):
        data = '{"type": "response.audio.delta", "data": "..."}'
        m = TYPE_RE.search(data)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "response.audio.delta")

    def test_passthrough_server_types_recognized(self):
        for msg_type in _PASSTHROUGH_SERVER_TYPES:
            data = json.dumps({"type": msg_type})
            m = TYPE_RE.search(data)
            self.assertIsNotNone(m, f"TYPE_RE should match {msg_type}")
            self.assertEqual(m.group(1), msg_type)

    def test_passthrough_client_types_recognized(self):
        for msg_type in _PASSTHROUGH_CLIENT_TYPES:
            data = json.dumps({"type": msg_type})
            m = TYPE_RE.search(data)
            self.assertIsNotNone(m, f"TYPE_RE should match {msg_type}")
            self.assertEqual(m.group(1), msg_type)

    def test_no_type_field_returns_none(self):
        data = '{"data": "no type here"}'
        m = TYPE_RE.search(data)
        self.assertIsNone(m)

    def test_type_with_extra_whitespace(self):
        data = '{"type"  :  "session.update"}'
        m = TYPE_RE.search(data)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "session.update")


class PreSerializedMessagesTests(unittest.TestCase):
    """Test pre-serialized static messages are valid JSON."""

    def test_response_create_msg(self):
        parsed = json.loads(RESPONSE_CREATE_MSG)
        self.assertEqual(parsed["type"], "response.create")

    def test_input_audio_clear_msg(self):
        parsed = json.loads(INPUT_AUDIO_CLEAR_MSG)
        self.assertEqual(parsed["type"], "input_audio_buffer.clear")


# ═══════════════════════════════════════════════════════════════════════════════
# RTMT CORE CLASSES TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ToolResultTests(unittest.TestCase):
    """Test ToolResult value object."""

    def test_to_text_returns_string(self):
        tr = ToolResult("hello", ToolResultDirection.TO_SERVER)
        self.assertEqual(tr.to_text(), "hello")

    def test_to_text_none_returns_empty(self):
        tr = ToolResult(None, ToolResultDirection.TO_SERVER)
        self.assertEqual(tr.to_text(), "")

    def test_to_client_text_falls_back_to_text(self):
        tr = ToolResult("server text", ToolResultDirection.TO_BOTH)
        self.assertEqual(tr.to_client_text(), "server text")

    def test_to_client_text_uses_separate_payload(self):
        tr = ToolResult("server text", ToolResultDirection.TO_BOTH, client_text='{"order": []}')
        self.assertEqual(tr.to_client_text(), '{"order": []}')

    def test_direction_to_both(self):
        tr = ToolResult("x", ToolResultDirection.TO_BOTH)
        self.assertEqual(tr.destination, ToolResultDirection.TO_BOTH)
        self.assertEqual(tr.destination.value, 3)


class ToolResultDirectionTests(unittest.TestCase):
    """Test the ToolResultDirection enum values."""

    def test_to_server_value(self):
        self.assertEqual(ToolResultDirection.TO_SERVER.value, 1)

    def test_to_client_value(self):
        self.assertEqual(ToolResultDirection.TO_CLIENT.value, 2)

    def test_to_both_value(self):
        self.assertEqual(ToolResultDirection.TO_BOTH.value, 3)


class ToolTests(unittest.TestCase):
    """Test the Tool wrapper."""

    def test_tool_stores_schema_and_target(self):
        schema = {"name": "test_tool"}
        target = MagicMock()
        tool = Tool(target=target, schema=schema)
        self.assertEqual(tool.schema, schema)
        self.assertEqual(tool.target, target)


class RTToolCallTests(unittest.TestCase):
    """Test RTToolCall tracking object."""

    def test_stores_ids(self):
        tc = RTToolCall("call-123", "prev-456")
        self.assertEqual(tc.tool_call_id, "call-123")
        self.assertEqual(tc.previous_id, "prev-456")

    def test_different_ids(self):
        tc1 = RTToolCall("c1", "p1")
        tc2 = RTToolCall("c2", "p2")
        self.assertNotEqual(tc1.tool_call_id, tc2.tool_call_id)

    def test_empty_previous_id(self):
        tc = RTToolCall("call-abc", "")
        self.assertEqual(tc.previous_id, "")


# ═══════════════════════════════════════════════════════════════════════════════
# RTMIDDLETIER INITIALIZATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class RTMiddleTierInitTests(unittest.TestCase):
    """Test RTMiddleTier construction and configuration."""

    def _make_rtmt(self, **kwargs):
        from azure.core.credentials import AzureKeyCredential
        cred = AzureKeyCredential("test-key")
        return RTMiddleTier(
            endpoint="https://fake.openai.azure.com",
            deployment="gpt-4o-realtime",
            credentials=cred,
            **kwargs,
        )

    def test_init_with_api_key(self):
        rtmt = self._make_rtmt()
        self.assertEqual(rtmt.key, "test-key")
        self.assertIsNone(rtmt._token_provider)

    def test_init_sets_voice_choice(self):
        rtmt = self._make_rtmt(voice_choice="coral")
        self.assertEqual(rtmt.voice_choice, "coral")

    def test_tools_empty_by_default(self):
        rtmt = self._make_rtmt()
        self.assertEqual(len(rtmt.tools), 0)

    def test_attach_to_app(self):
        rtmt = self._make_rtmt()
        app = web.Application()
        rtmt.attach_to_app(app, "/rt")
        routes = [r.resource.canonical for r in app.router.routes()]
        self.assertIn("/rt", routes)

    def test_system_message_settable(self):
        rtmt = self._make_rtmt()
        rtmt.system_message = "You are a crew member."
        self.assertEqual(rtmt.system_message, "You are a crew member.")

    def test_default_voice_choice_is_none(self):
        rtmt = self._make_rtmt()
        self.assertIsNone(rtmt.voice_choice)

    def test_temperature_settable(self):
        rtmt = self._make_rtmt()
        rtmt.temperature = 0.6
        self.assertEqual(rtmt.temperature, 0.6)

    def test_max_tokens_settable(self):
        rtmt = self._make_rtmt()
        rtmt.max_tokens = 150
        self.assertEqual(rtmt.max_tokens, 150)

    def test_endpoint_stored(self):
        rtmt = self._make_rtmt()
        self.assertEqual(rtmt.endpoint, "https://fake.openai.azure.com")

    def test_deployment_stored(self):
        rtmt = self._make_rtmt()
        self.assertEqual(rtmt.deployment, "gpt-4o-realtime")

    def test_session_manager_created(self):
        rtmt = self._make_rtmt()
        self.assertIsNotNone(rtmt._sessions)
        self.assertIsInstance(rtmt._sessions, SessionManager)

    def test_prompt_loader_passed_to_session_manager(self):
        loader = MagicMock()
        loader.get_greeting_json_str.return_value = '{"type":"test"}'
        rtmt = self._make_rtmt(prompt_loader=loader)
        self.assertEqual(rtmt._sessions.greeting_msg, '{"type":"test"}')


# ═══════════════════════════════════════════════════════════════════════════════
# PASSTHROUGH FROZENSET TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class PassthroughTypeSetsTests(unittest.TestCase):
    """Verify passthrough type sets contain expected values."""

    def test_server_types_contains_audio_delta(self):
        self.assertIn("response.audio.delta", _PASSTHROUGH_SERVER_TYPES)

    def test_server_types_contains_audio_done(self):
        self.assertIn("response.audio.done", _PASSTHROUGH_SERVER_TYPES)

    def test_server_types_contains_transcript_delta(self):
        self.assertIn("response.audio_transcript.delta", _PASSTHROUGH_SERVER_TYPES)

    def test_server_types_contains_speech_started(self):
        self.assertIn("input_audio_buffer.speech_started", _PASSTHROUGH_SERVER_TYPES)

    def test_client_types_contains_audio_append(self):
        self.assertIn("input_audio_buffer.append", _PASSTHROUGH_CLIENT_TYPES)

    def test_client_types_contains_audio_clear(self):
        self.assertIn("input_audio_buffer.clear", _PASSTHROUGH_CLIENT_TYPES)

    def test_server_types_is_frozenset(self):
        self.assertIsInstance(_PASSTHROUGH_SERVER_TYPES, frozenset)

    def test_client_types_is_frozenset(self):
        self.assertIsInstance(_PASSTHROUGH_CLIENT_TYPES, frozenset)


if __name__ == "__main__":
    unittest.main()
