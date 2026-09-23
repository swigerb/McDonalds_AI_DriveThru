"""Rate-limit recovery on the cloud realtime relay (Round 3 R1).

The demos share one Azure OpenAI quota. A rate-limited response ends with
`response.done` status=failed and no output; before this the middle tier relayed
it and the guest heard silence. These tests drive the real middle tier against a
fake GA upstream that rate-limits scripted responses. The retry sleep is
injected (`RTMiddleTier._rate_limit_sleep`), so nothing waits out real delays.
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
from azure.core.credentials import AzureKeyCredential

import rate_limit
from rate_limit import (
    RateLimitConfig,
    RateLimitRecovery,
    failed_response_rate_limit,
    rate_limit_error,
    retry_delay,
    retry_hint_seconds,
)
from rtmt import (
    RTMiddleTier,
    Tool,
    ToolResult,
    ToolResultDirection,
    _SessionUpdateGuard,
)

TOOL_NAMES = ["search", "update_order", "get_order", "reset_order"]
# What the shared deployment returned under load (2026-09-22 spot check), plus a hint.
RATE_LIMIT = {"type": "rate_limit_exceeded", "code": "inference_rate_limit_exceeded",
              "message": "Rate limit reached for this deployment."}


def _failed_done(error: dict) -> dict:
    return {"type": "response.done", "response": {"id": "resp", "status": "failed", "output": [],
                                                  "status_details": {"type": "failed", "error": error}}}


class RateLimitedGA:
    """Fake /openai/v1/realtime. Each response (server VAD turn or response.create)
    plays the next entry of `script`; an empty script answers normally."""

    def __init__(self):
        self.script: list[str | tuple[str, dict]] = []
        self.received: list[dict] = []
        self.ws: web.WebSocketResponse | None = None
        self.connected = asyncio.Event()

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/openai/v1/realtime", self.handler)
        return app

    def count(self, kind: str) -> int:
        return sum(1 for e in self.received if e.get("type") == kind)

    async def handler(self, request):
        ws = self.ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "session.created", "session": {"type": "realtime"}})
        self.connected.set()
        async for msg in ws:
            event = json.loads(msg.data)
            self.received.append(event)
            if event["type"] == "session.update":
                await self.on_session_update(ws, event)
            elif event["type"] == "response.create":
                await self.respond()
        return ws

    async def on_session_update(self, ws, event):
        await ws.send_json({"type": "session.updated", "session": event["session"]})

    async def vad_turn(self):
        """The guest speaks and server VAD creates a response."""
        await self.ws.send_json({"type": "input_audio_buffer.speech_started"})
        await self.ws.send_json({"type": "input_audio_buffer.speech_stopped"})
        await self.respond()

    async def respond(self):
        entry = self.script.pop(0) if self.script else "ok"
        kind, error = entry if isinstance(entry, tuple) else (entry, RATE_LIMIT)
        ws = self.ws
        if kind == "error_only":  # response.create rejected before a response exists
            await ws.send_json({"type": "error", "error": error})
            return
        await ws.send_json({"type": "response.created", "response": {"id": "resp"}})
        if kind == "ok":
            await ws.send_json({"type": "response.output_audio.delta", "delta": "AAAA"})
            await ws.send_json({"type": "response.output_audio.done"})
            await ws.send_json({"type": "response.done", "response": {"id": "resp", "status": "completed", "output": [
                {"type": "message", "content": [{"type": "audio", "transcript": "Anything else?"}]}]}})
        elif kind == "fail":
            await ws.send_json(_failed_done(error))
        elif kind == "error_and_fail":
            await ws.send_json({"type": "error", "error": error})
            await ws.send_json(_failed_done(error))
        elif kind == "tool":
            call = {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": "{\"query\": \"fries\"}"}
            await ws.send_json({"type": "response.output_item.added", "item": {**call, "arguments": ""}})
            await ws.send_json({"type": "response.output_item.done", "item": call})
            await ws.send_json({"type": "response.done", "response": {"id": "resp", "status": "completed",
                                                                      "output": [call]}})
        elif kind == "call_then_fail":  # rate-limited after a tool call was announced
            call = {"type": "function_call", "call_id": "call_2", "name": "search", "arguments": ""}
            await ws.send_json({"type": "response.output_item.added", "item": call})
            await ws.send_json(_failed_done(error))
        elif kind == "hang":  # a response that never finishes
            pass
        else:
            raise AssertionError(kind)


class _Harness(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.fake = RateLimitedGA()
        self.fake_server = TestServer(self.fake.app())
        await self.fake_server.start_server()
        self.rtmt = RTMiddleTier(str(self.fake_server.make_url("")), "gpt-realtime-2.1-dz",
                                 AzureKeyCredential("test-key"), voice_choice="marin")
        self.rtmt.system_message = "You are a McDonald's drive-thru crew member."
        self.rtmt.max_tokens = 4096
        self.search_calls = 0

        async def search(_args):
            self.search_calls += 1
            return ToolResult("Fries: small, medium, large.", ToolResultDirection.TO_SERVER)
        self.rtmt.tools["search"] = Tool(target=search, schema={"type": "function", "name": "search"})
        for name in TOOL_NAMES[1:]:
            self.rtmt.tools[name] = Tool(target=MagicMock(), schema={"type": "function", "name": name})
        self.rtmt.rate_limit_config = RateLimitConfig()
        self.delays: list[float] = []
        self.gate: asyncio.Event | None = None

        async def fake_sleep(delay):
            self.delays.append(delay)
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)
        self.rtmt._rate_limit_sleep = fake_sleep

        app = web.Application()
        self.rtmt.attach_to_app(app, "/realtime")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.browser_events: list[dict] = []

    async def asyncTearDown(self):
        if self.gate is not None:
            self.gate.set()
        await self.client.close()
        await self.fake_server.close()

    async def connect(self):
        self.browser = await self.client.ws_connect("/realtime")
        await asyncio.wait_for(self.fake.connected.wait(), 5)
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self):
        async for msg in self.browser:
            if msg.type == WSMsgType.TEXT:
                self.browser_events.append(json.loads(msg.data))

    async def until(self, predicate, timeout=5.0):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)

    async def settle(self, seconds=0.2):
        await asyncio.sleep(seconds)

    def events(self, kind):
        return [e for e in self.browser_events if e.get("type") == kind]

    def completed(self):
        return [e for e in self.events("response.done") if e["response"].get("status") == "completed"]


class RecoveryLadderTests(_Harness):

    async def test_rate_limited_response_gets_exactly_one_silent_retry(self):
        await self.connect()
        self.fake.script = ["fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 1)
        self.assertEqual(self.delays, [1.5])
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT), [], "retry 1 must be silent")

    async def test_second_failure_tells_the_browser_then_retries_once_more(self):
        await self.connect()
        self.fake.script = ["fail", "fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 2)
        self.assertEqual(self.delays, [1.5, 4.0])
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT),
                         [{"type": "extension.rate_limited", "attempt": 1}])
        notice = self.browser_events.index(self.events(rate_limit.RATE_LIMITED_EVENT)[0])
        self.assertLess(notice, self.browser_events.index(self.completed()[0]),
                        "the apology must play before retry 2's answer")

    async def test_third_failure_is_final_and_stops_retrying(self):
        await self.connect()
        self.fake.script = ["fail", "fail", "fail"]
        await self.fake.vad_turn()
        await self.until(lambda: any(e.get("final") for e in self.events(rate_limit.RATE_LIMITED_EVENT)))
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 2, "no retry after the final notice")
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT),
                         [{"type": "extension.rate_limited", "attempt": 1},
                          {"type": "extension.rate_limited", "attempt": 2, "final": True}])
        self.assertFalse(self.browser.closed, "the session stays alive")

        # The next guest turn proceeds normally -- and its own rate limit starts a fresh ladder.
        self.delays.clear()
        self.fake.script = ["fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 3)
        self.assertEqual(self.delays, [1.5], "ladder is per failed response, not per session")
        self.assertEqual(len(self.events(rate_limit.RATE_LIMITED_EVENT)), 2)

    async def test_max_retries_is_configurable(self):
        self.rtmt.rate_limit_config = RateLimitConfig(max_retries=1)
        await self.connect()
        self.fake.script = ["fail", "fail"]
        await self.fake.vad_turn()
        await self.until(lambda: self.events(rate_limit.RATE_LIMITED_EVENT))
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 1)
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT),
                         [{"type": "extension.rate_limited", "attempt": 1, "final": True}])

    async def test_service_retry_hint_sets_the_delays(self):
        await self.connect()
        slow = {**RATE_LIMIT, "message": "Rate limit reached. Please try again in 20 seconds."}
        fast = {**RATE_LIMIT, "message": "Rate limit reached. Please try again in 250ms."}
        self.fake.script = [("fail", fast), ("fail", fast), "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        self.assertEqual(self.delays, [0.5, 2.0], "hints are clamped to [0.5, 5] and [2, 8]")
        self.delays.clear()
        self.fake.script = [("fail", slow), ("fail", slow), "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: len(self.completed()) == 2)
        self.assertEqual(self.delays, [5.0, 8.0])

    async def test_rate_limit_error_event_is_retried(self):
        await self.connect()
        self.fake.script = ["error_only", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 1)
        self.assertEqual(self.delays, [1.5])

    async def test_error_and_failed_done_for_one_response_retry_once(self):
        await self.connect()
        self.gate = asyncio.Event()
        self.fake.script = ["error_and_fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.events("response.done"))
        await self.settle()
        self.gate.set()
        await self.until(lambda: self.completed())
        await self.settle()
        self.assertEqual(self.fake.count("response.create"), 1)
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT), [])


class CancellationTests(_Harness):

    async def _pending_retry(self):
        await self.connect()
        self.gate = asyncio.Event()
        self.fake.script = ["fail"]
        await self.fake.vad_turn()
        await self.until(lambda: self.delays)

    async def test_guest_speech_cancels_a_pending_retry(self):
        await self._pending_retry()
        await self.fake.ws.send_json({"type": "input_audio_buffer.speech_started"})
        await self.until(lambda: self.events("input_audio_buffer.speech_started")[1:])
        self.gate.set()
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 0, "a stale retry was stacked on the guest's new turn")

        # The guest's new turn starts its own ladder: a rate limit there is retry 1 again (silent).
        self.fake.script = ["fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        self.assertEqual(self.delays, [1.5, 1.5])
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT), [])

    async def test_a_new_response_cancels_a_pending_retry(self):
        await self._pending_retry()
        self.fake.script = ["hang"]
        await self.fake.respond()  # e.g. server VAD started a fresh response
        await self.until(lambda: len(self.events("response.created")) == 2)
        self.gate.set()
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 0)

    async def test_the_retrys_own_response_does_not_cancel_the_ladder(self):
        await self.connect()
        self.fake.script = ["fail", "fail", "fail"]
        await self.fake.vad_turn()
        await self.until(lambda: any(e.get("final") for e in self.events(rate_limit.RATE_LIMITED_EVENT)))
        self.assertEqual(self.delays, [1.5, 4.0])


class UntouchedTests(_Harness):

    async def test_other_failures_are_relayed_as_before(self):
        await self.connect()
        self.fake.script = [("fail", {"type": "server_error", "code": "server_error", "message": "try again in 1s"}),
                            ("fail", {"type": "invalid_request_error", "code": "content_filter"})]
        await self.fake.vad_turn()
        await self.fake.vad_turn()
        await self.until(lambda: len(self.events("response.done")) == 2)
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 0)
        self.assertEqual(self.delays, [])
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT), [])

    async def test_disabled_recovery_relays_the_failure(self):
        self.rtmt.rate_limit_config = RateLimitConfig(enabled=False)
        await self.connect()
        self.fake.script = ["fail"]
        await self.fake.vad_turn()
        await self.until(lambda: self.events("response.done"))
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 0)
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT), [])

    async def test_session_update_rejection_goes_to_the_fallback_not_here(self):
        """A rate-limit error that names our bootstrap's event_id is the session.update fallback's."""
        async def reject_bootstrap(ws, event):
            if str(event.get("event_id", "")).startswith("mcd_bootstrap"):
                await ws.send_json({"type": "error", "error": {**RATE_LIMIT, "event_id": event["event_id"]}})
            else:
                await ws.send_json({"type": "session.updated", "session": event["session"]})
        self.fake.on_session_update = reject_bootstrap
        await self.connect()
        await self.until(lambda: self.fake.count("session.update") == 2)
        await self.settle(0.3)
        fallback = self.fake.received[-1]
        self.assertTrue(fallback["event_id"].startswith("mcd_fallback"))
        self.assertEqual(self.fake.count("response.create"), 0)
        self.assertEqual(self.delays, [])


class ToolFollowUpTests(_Harness):

    async def test_retried_tool_follow_up_does_not_rerun_the_tool(self):
        await self.connect()
        self.fake.script = ["tool", "fail", "fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed()[1:])  # the tool call's done + the follow-up's answer
        await self.settle()
        self.assertEqual(self.search_calls, 1)
        outputs = [e for e in self.fake.received if e.get("type") == "conversation.item.create"
                   and e["item"]["type"] == "function_call_output"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(self.fake.count("response.create"), 3, "follow-up + 2 retries")
        self.assertEqual(self.delays, [1.5, 4.0])
        self.assertEqual(self.events(rate_limit.RATE_LIMITED_EVENT),
                         [{"type": "extension.rate_limited", "attempt": 1}])


    async def test_failed_response_with_a_pending_tool_call_is_not_double_retried(self):
        """The tool follow-up response.create already regenerates it; don't stack a retry on top."""
        await self.connect()
        self.fake.script = ["call_then_fail", "ok"]
        await self.fake.vad_turn()
        await self.until(lambda: self.completed())
        await self.settle(0.3)
        self.assertEqual(self.fake.count("response.create"), 1)
        self.assertEqual(self.delays, [])
        self.assertEqual(self.search_calls, 0)


class DetectionAndHintTests(unittest.TestCase):

    def test_rate_limit_error_matches_code_or_type(self):
        self.assertIsNotNone(rate_limit_error({"code": "rate_limit_exceeded"}))
        self.assertIsNotNone(rate_limit_error({"type": "rate_limit_error", "code": None}))
        self.assertIsNotNone(rate_limit_error({"code": "inference_rate_limit_exceeded"}))
        self.assertIsNone(rate_limit_error({"type": "server_error", "code": "server_error",
                                            "message": "rate_limit"}), "the message alone is not a signal")
        self.assertIsNone(rate_limit_error(None))

    def test_only_failed_responses_count(self):
        self.assertEqual(failed_response_rate_limit(_failed_done(RATE_LIMIT)), RATE_LIMIT)
        cancelled = _failed_done(RATE_LIMIT)
        cancelled["response"]["status"] = "cancelled"
        self.assertIsNone(failed_response_rate_limit(cancelled))
        self.assertIsNone(failed_response_rate_limit({"type": "response.done", "response": {"status": "completed"}}))
        self.assertIsNone(failed_response_rate_limit(_failed_done({"code": "server_error"})))

    def test_retry_hint_parsing(self):
        cases = {
            "Rate limit reached. Please try again in 2 seconds.": 2.0,
            "Please try again in 1.5s.": 1.5,
            "please TRY AGAIN IN 350ms": 0.35,
            "Retry after 7 seconds.": 7.0,
            "try again in 20 sec": 20.0,
            "Rate limit reached.": None,
            "try again later": None,
            None: None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(retry_hint_seconds(text), expected)

    def test_retry_delay_clamps_the_hint_and_defaults_without_one(self):
        self.assertEqual(retry_delay(None, 1.5, rate_limit.FIRST_RETRY_BOUNDS), 1.5)
        self.assertEqual(retry_delay(0.1, 1.5, rate_limit.FIRST_RETRY_BOUNDS), 0.5)
        self.assertEqual(retry_delay(3.0, 1.5, rate_limit.FIRST_RETRY_BOUNDS), 3.0)
        self.assertEqual(retry_delay(60, 1.5, rate_limit.FIRST_RETRY_BOUNDS), 5.0)
        self.assertEqual(retry_delay(None, 4.0, rate_limit.SECOND_RETRY_BOUNDS), 4.0)
        self.assertEqual(retry_delay(1.0, 4.0, rate_limit.SECOND_RETRY_BOUNDS), 2.0)
        self.assertEqual(retry_delay(30, 4.0, rate_limit.SECOND_RETRY_BOUNDS), 8.0)

    def test_guard_never_infers_a_rate_limit_is_a_session_update_rejection(self):
        guard = _SessionUpdateGuard()
        guard.stamp({"type": "session.update", "event_id": "su1", "session": {}})
        uncorrelated = {"type": "error", "error": {**RATE_LIMIT, "type": "invalid_request_error"}}
        self.assertIsNone(guard.correlate(uncorrelated))
        self.assertEqual(guard.correlate({"type": "error", "error": {**RATE_LIMIT, "event_id": "su1"}}), "su1",
                         "an echoed event_id still correlates")


class ConfigTests(unittest.TestCase):

    def test_config_yaml_block(self):
        from config_loader import get_config
        cfg = RateLimitConfig.from_config(get_config()["resilience"]["rate_limit"], environ={})
        self.assertEqual(cfg, RateLimitConfig(enabled=True, retry_delay_seconds=1.5,
                                              second_retry_delay_seconds=4.0, max_retries=2))

    def test_env_override_for_enabled(self):
        on = {"enabled": True}
        self.assertFalse(RateLimitConfig.from_config(on, {"RATE_LIMIT_RECOVERY_ENABLED": "false"}).enabled)
        self.assertFalse(RateLimitConfig.from_config(on, {"RATE_LIMIT_RECOVERY_ENABLED": "0"}).enabled)
        self.assertTrue(RateLimitConfig.from_config({"enabled": False}, {"RATE_LIMIT_RECOVERY_ENABLED": "true"}).enabled)
        self.assertTrue(RateLimitConfig.from_config(on, {"RATE_LIMIT_RECOVERY_ENABLED": ""}).enabled)
        self.assertTrue(RateLimitConfig.from_config(None, {}).enabled)

    def test_middle_tier_reads_the_config(self):
        rtmt = RTMiddleTier("https://fake.openai.azure.com", "gpt-realtime-2.1-dz", AzureKeyCredential("k"))
        self.assertTrue(rtmt.rate_limit_config.enabled)
        self.assertEqual(rtmt.rate_limit_config.max_retries, 2)


class RecoveryUnitTests(unittest.IsolatedAsyncioTestCase):

    async def _recovery(self):
        self.upstream, self.client_events = [], []

        async def upstream():
            self.upstream.append("response.create")

        async def client(event):
            self.client_events.append(event)

        async def no_sleep(_):
            return None
        return RateLimitRecovery(RateLimitConfig(), upstream, client, sleep=no_sleep)

    async def test_a_later_response_starts_a_fresh_ladder(self):
        recovery = await self._recovery()
        await recovery.on_rate_limited(RATE_LIMIT, "test")
        await asyncio.sleep(0.01)
        recovery.on_response_created()   # the retry's own response (it succeeds)
        recovery.on_response_created()   # a later response, e.g. a tool follow-up
        await recovery.on_rate_limited(RATE_LIMIT, "test")
        await asyncio.sleep(0.01)
        self.assertEqual(self.upstream, ["response.create", "response.create"])
        self.assertEqual(self.client_events, [], "a fresh failure after a success starts at the silent retry")


    async def test_send_failure_after_the_socket_closed_is_swallowed(self):
        async def closed():
            raise ConnectionResetError("closed")
        sent = []

        async def client(event):
            sent.append(event)

        async def no_sleep(_):
            return None
        recovery = RateLimitRecovery(RateLimitConfig(), closed, client, sleep=no_sleep)
        await recovery.on_rate_limited(RATE_LIMIT, "test")
        task = recovery._task
        await asyncio.sleep(0.01)
        self.assertTrue(task.done())
        self.assertIsNone(task.exception(), "a closed socket must not leave an unretrieved task exception")
        self.assertFalse(recovery.retry_pending)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
