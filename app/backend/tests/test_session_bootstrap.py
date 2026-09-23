"""Regression tests for the "no tools after reconnect" failure (ported from the
Sonic reference repo's $0.00 ticket incident, 2026-09-22).

A browser socket auto-reconnected by react-use-websocket while the mic is live
opens a fresh upstream realtime session that runs on service defaults (no tools,
generic instructions, server VAD auto-responding). If the model speaks before the
browser's session.update arrives, GA rejects any later session.update whose voice
differs from the current one with `cannot_update_voice` -- and it rejects the
whole event, so tools/tool_choice/instructions are never registered and the crew
member goes quiet / never touches the order.

These tests drive the real middle tier end to end against a fake GA realtime
server that enforces the two service behaviours involved:
  * server VAD auto-creates a response as soon as mic audio arrives;
  * a session.update carrying a *different* voice after assistant audio is
    rejected wholesale with `cannot_update_voice` (same voice / no voice is OK).
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

from rtmt import RTMiddleTier, Tool, _to_ga_session

SYSTEM_PROMPT = "You are a McDonald's drive-thru crew member."
TOOL_NAMES = ["search", "update_order", "get_order", "reset_order"]

# Exactly what app/frontend/src/hooks/useRealtime.tsx startSession() sends
# (App.tsx passes enableInputAudioTranscription: true).
BROWSER_SESSION_UPDATE = {
    "type": "session.update",
    "session": {
        "turn_detection": {"type": "server_vad", "threshold": 0.7, "prefix_padding_ms": 300, "silence_duration_ms": 500},
        "input_audio_transcription": {"model": "whisper-1"},
    },
}
MIC_FRAME = {"type": "input_audio_buffer.append", "audio": "AAAA"}


class FakeGARealtime:
    """Minimal stand-in for /openai/v1/realtime with the GA rules that matter here."""

    def __init__(self):
        self.session = {"tools": [], "tool_choice": "auto", "instructions": "default assistant", "voice": "alloy"}
        self.assistant_audio = False
        self.vad_fired = False
        self.received: list[dict] = []
        self.errors: list[dict] = []
        self.response_sessions: list[dict] = []
        # GA rejects a session.update wholesale if ANY field is unsupported.
        # reject_keys: top-level GA session keys that get an update rejected.
        # echo_event_id=False mimics gpt-realtime-1.5 rejecting `reasoning`
        # (no error.event_id, no param).
        self.reject_keys: set[str] = set()
        self.reject_every_update = False
        self.echo_event_id = True
        # Delay (seconds) before acknowledging an accepted session.update, and a
        # timeline of ("recv", type) / ("sent", type) to observe ordering.
        self.ack_delay = 0.0
        self.timeline: list[tuple[str, str]] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/openai/v1/realtime", self.handler)
        return app

    def _snapshot(self) -> dict:
        return {**self.session, "tools": [t.get("name") for t in self.session["tools"]]}

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "session.created", "session": {"type": "realtime", **self._snapshot()}})
        async for msg in ws:
            event = json.loads(msg.data)
            self.received.append(event)
            kind = event.get("type")
            self.timeline.append(("recv", kind))
            if kind == "session.update":
                await self._session_update(ws, event["session"], event.get("event_id"))
            elif kind == "input_audio_buffer.append" and not self.vad_fired:
                self.vad_fired = True  # server VAD: speech detected -> auto response
                await self._respond(ws)
            elif kind == "response.create":
                await self._respond(ws)
            elif kind == "conversation.item.delete":
                # Unrelated client event rejected, with its event_id echoed.
                await self._error(ws, "item_not_found", "item_id", event.get("event_id"), "No such item.")
            elif kind == "input_audio_buffer.commit":
                # Unrelated rejection with no event_id and no param.
                await self._error(ws, "input_audio_buffer_commit_empty", None, None, "Buffer too small.")
        return ws

    async def _error(self, ws, code, param, event_id, text) -> None:
        error = {"type": "error", "event_id": "event_srv", "error": {
            "type": "invalid_request_error", "code": code, "message": text, "param": param,
            "event_id": event_id}}
        self.errors.append(error)
        await ws.send_json(error)

    async def _session_update(self, ws, session: dict, event_id: str | None = None) -> None:
        rejected = sorted(k for k in session if k in self.reject_keys)
        if self.reject_every_update or rejected:
            key = rejected[0] if rejected else "instructions"
            await self._error(ws, "invalid_value",
                              f"session.{key}" if self.echo_event_id else None,
                              event_id if self.echo_event_id else None,
                              "Unsupported option for this model.")
            return
        voice = (session.get("audio") or {}).get("output", {}).get("voice")
        if voice is not None and self.assistant_audio and voice != self.session["voice"]:
            error = {"type": "error", "error": {
                "type": "invalid_request_error", "code": "cannot_update_voice",
                "message": "Cannot update a conversation's voice if assistant audio is present."}}
            self.errors.append(error)
            await ws.send_json(error)
            return
        for key in ("tools", "tool_choice", "instructions"):
            if key in session:
                self.session[key] = session[key]
        if voice is not None:
            self.session["voice"] = voice
        ack = {"type": "session.updated", "session": {"type": "realtime", **self._snapshot()}}
        if self.ack_delay:
            async def later():
                await asyncio.sleep(self.ack_delay)
                self.timeline.append(("sent", "session.updated"))
                await ws.send_json(ack)
            asyncio.get_running_loop().create_task(later())
            return
        self.timeline.append(("sent", "session.updated"))
        await ws.send_json(ack)

    async def _respond(self, ws) -> None:
        self.response_sessions.append(self._snapshot())
        await ws.send_json({"type": "response.created", "response": {"id": "resp"}})
        await ws.send_json({"type": "response.output_audio.delta", "delta": "AAAA"})
        self.assistant_audio = True
        await ws.send_json({"type": "response.output_audio.done"})
        await ws.send_json({"type": "response.done", "response": {"id": "resp", "output": [
            {"type": "message", "content": [{"type": "audio", "transcript": "hi"}]}]}})


class _RealtimeHarness(unittest.IsolatedAsyncioTestCase):
    """Real middle tier <-> FakeGARealtime, driven through a fake browser socket."""

    async def asyncSetUp(self):
        self.fake = FakeGARealtime()
        self.fake_server = TestServer(self.fake.app())
        await self.fake_server.start_server()

        self.rtmt = RTMiddleTier(
            endpoint=str(self.fake_server.make_url("")),
            deployment="gpt-realtime-test",
            credentials=AzureKeyCredential("test-key"),
            voice_choice="shimmer",
        )
        self.rtmt.system_message = SYSTEM_PROMPT
        self.rtmt.max_tokens = 4096
        for name in TOOL_NAMES:
            self.rtmt.tools[name] = Tool(target=MagicMock(), schema={"type": "function", "name": name})

        app = web.Application()
        self.rtmt.attach_to_app(app, "/realtime")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.fake_server.close()

    async def _until(self, predicate, timeout=5.0):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)

    async def _response_done(self, browser, timeout=5.0):
        async def recv():
            while True:
                msg = await browser.receive()
                if json.loads(msg.data).get("type") == "response.done":
                    return
        await asyncio.wait_for(recv(), timeout)

    def _session_updates(self):
        return [e for e in self.fake.received if e["type"] == "session.update"]

    async def _browser_events(self, browser, duration=0.3):
        events = []
        try:
            while True:
                msg = await asyncio.wait_for(browser.receive(), duration)
                if msg.type != WSMsgType.TEXT:
                    return events
                events.append(json.loads(msg.data))
        except TimeoutError:
            return events


class SessionBootstrapTests(_RealtimeHarness):

    async def test_reconnected_socket_with_live_mic_still_registers_tools(self):
        """The production failure: mic audio reaches the upstream before the
        browser's session.update. The model must still have our tools."""
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json(MIC_FRAME)
        await self._response_done(browser)          # VAD auto-response
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)          # greeting

        first = self.fake.response_sessions[0]
        self.assertEqual(first["tools"], TOOL_NAMES,
                         "the model answered the guest with no tools registered")
        self.assertEqual(first["tool_choice"], "auto")
        self.assertEqual(first["instructions"], SYSTEM_PROMPT)
        self.assertEqual(self.fake.errors, [], "a session.update was rejected")
        self.assertEqual(self.fake.session["tools"], [{"type": "function", "name": n} for n in TOOL_NAMES])
        self.assertEqual(self.fake.session["tool_choice"], "auto")
        await browser.close()

    async def test_first_upstream_frame_is_the_server_session_config(self):
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json(MIC_FRAME)
        await self._until(lambda: len(self.fake.received) >= 2)

        first = self.fake.received[0]
        self.assertEqual(first["type"], "session.update")
        session = first["session"]
        self.assertEqual([t["name"] for t in session["tools"]], TOOL_NAMES)
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["instructions"], SYSTEM_PROMPT)
        self.assertEqual(session["audio"]["output"]["voice"], "shimmer")
        self.assertEqual(session["audio"]["input"]["turn_detection"], BROWSER_SESSION_UPDATE["session"]["turn_detection"])
        self.assertEqual(session["audio"]["input"]["transcription"], {"model": "whisper-1"})
        self.assertEqual(session["type"], "realtime")
        await browser.close()

    async def test_mic_press_sequence_voice_then_session_update(self):
        """What App.tsx onToggleListening sends: extension.set_voice, then
        session.update. First press greets once with tools; a second press after
        the greeting (voice locked) must not produce any rejection."""
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json({"type": "extension.set_voice", "voice": "coral"})
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)          # greeting
        self.assertEqual(self.fake.session["voice"], "coral")
        self.assertEqual(self.fake.response_sessions[0]["tools"], TOOL_NAMES)

        await browser.send_json({"type": "extension.set_voice", "voice": "ash"})
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._until(lambda: sum(e.get("type") == "session.update" for e in self.fake.received) >= 4)
        await asyncio.sleep(0.1)

        self.assertEqual(self.fake.errors, [])
        self.assertEqual(sum(e["type"] == "conversation.item.create" for e in self.fake.received), 1,
                         "greeting must fire exactly once")
        self.assertEqual(self.fake.session["voice"], "coral", "voice is locked once assistant audio exists")
        self.assertEqual(self.fake.session["tools"], [{"type": "function", "name": n} for n in TOOL_NAMES])
        await browser.close()

    async def test_session_update_after_assistant_audio_is_not_rejected_for_voice(self):
        """voice_choice is process-wide, so another tab can change it mid-call.
        A re-sent session.update (mic re-toggle) must not carry a new voice."""
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)          # greeting -> assistant audio present
        self.rtmt.voice_choice = "coral"
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._until(lambda: len(self._session_updates()) >= 3)
        await asyncio.sleep(0.1)

        self.assertEqual(self.fake.errors, [])
        last = self._session_updates()[-1]["session"]
        self.assertNotIn("voice", (last.get("audio") or {}).get("output", {}))
        self.assertEqual([t["name"] for t in last["tools"]], TOOL_NAMES)
        await browser.close()

    async def test_voice_picker_after_assistant_audio_is_deferred(self):
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)
        before = len(self._session_updates())
        await browser.send_json({"type": "extension.set_voice", "voice": "coral"})
        await asyncio.sleep(0.2)

        self.assertEqual(len(self._session_updates()), before, "voice change was sent and would be rejected")
        self.assertEqual(self.fake.errors, [])
        self.assertEqual(self.rtmt.voice_choice, "coral")
        await browser.close()

    async def test_voice_picker_before_assistant_audio_is_applied(self):
        browser = await self.client.ws_connect("/realtime")
        await self._until(lambda: len(self._session_updates()) >= 1)
        await browser.send_json({"type": "extension.set_voice", "voice": "coral"})
        await self._until(lambda: len(self._session_updates()) >= 2)
        await asyncio.sleep(0.1)

        self.assertEqual(self.fake.session["voice"], "coral")
        self.assertEqual(self.fake.errors, [])
        await browser.close()

    async def test_bootstrap_does_not_trigger_an_unprompted_greeting(self):
        """Greeting belongs to the browser's session.update (mic pressed), not to
        the bootstrap session.updated that arrives as soon as the page connects."""
        browser = await self.client.ws_connect("/realtime")
        await self._until(lambda: len(self._session_updates()) >= 1)
        await asyncio.sleep(0.3)
        self.assertFalse(any(e["type"] == "conversation.item.create" for e in self.fake.received))
        self.assertEqual(self.fake.response_sessions, [])

        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)
        self.assertEqual(sum(e["type"] == "conversation.item.create" for e in self.fake.received), 1)
        self.assertEqual(self.fake.response_sessions[0]["tools"], TOOL_NAMES)
        await browser.close()

    async def test_greeting_waits_for_session_updated(self):
        """The greeting's response.create must not reach the upstream before the
        server has confirmed a session config (no tools-less greeting)."""
        self.fake.ack_delay = 0.4
        browser = await self.client.ws_connect("/realtime")
        await browser.send_json(BROWSER_SESSION_UPDATE)
        await self._response_done(browser)
        timeline = self.fake.timeline
        self.assertLess(timeline.index(("sent", "session.updated")), timeline.index(("recv", "response.create")),
                        timeline)
        self.assertEqual(self.fake.response_sessions[0]["tools"], TOOL_NAMES)
        await browser.close()


class ReasoningAndTranscriptionConfigTests(unittest.TestCase):
    """gpt-realtime-2.1 reasoning / transcription wiring (ported from Sonic).

    A session.update the service rejects drops the tools with it, so anything
    only a reasoning model accepts must never reach a non-reasoning deployment.
    """

    def _rtmt(self, deployment="gpt-realtime-2.1", **attrs):
        rtmt = RTMiddleTier("https://fake.openai.azure.com", deployment, AzureKeyCredential("k"), voice_choice="marin")
        rtmt.system_message = SYSTEM_PROMPT
        rtmt.tools["update_order"] = Tool(target=MagicMock(), schema={"type": "function", "name": "update_order"})
        for key, value in attrs.items():
            setattr(rtmt, key, value)
        return rtmt

    def _bootstrap(self, rtmt):
        return json.loads(rtmt.build_bootstrap_session_update())["session"]

    def test_reasoning_not_sent_when_unconfigured(self):
        session = self._bootstrap(self._rtmt())
        self.assertNotIn("reasoning", session)
        self.assertNotIn("parallel_tool_calls", session)

    def test_reasoning_sent_on_a_reasoning_deployment(self):
        session = self._bootstrap(self._rtmt(reasoning_effort="low", parallel_tool_calls=False))
        self.assertEqual(session["reasoning"], {"effort": "low"})
        self.assertIs(session["parallel_tool_calls"], False)
        self.assertEqual(self._bootstrap(self._rtmt(reasoning_effort="none"))["reasoning"], {"effort": "none"})

    def test_reasoning_reaches_every_session_update_path(self):
        rtmt = self._rtmt(reasoning_effort="low")
        payloads = [self._bootstrap(rtmt), rtmt._build_session({}), rtmt._build_session({}, voice_locked=True)]
        for session in payloads:
            self.assertEqual(session["reasoning"], {"effort": "low"})
        # The picker's voice-only update is a partial merge; it must not re-send reasoning.
        self.assertEqual(set(json.loads(rtmt.build_voice_update("marin"))["session"]), {"type", "audio"})

    def test_rollback_to_1_5_never_sends_reasoning(self):
        """1.5 rejects `reasoning` (any effort) and parallel_tool_calls=true -- with the tools."""
        for deployment in ("gpt-realtime-1.5", "gpt-realtime", "gpt-realtime-2025-08-28", "gpt-realtime-mini",
                           "gpt-4o-realtime-preview"):
            with self.subTest(deployment=deployment):
                rtmt = self._rtmt(deployment, reasoning_effort="high", parallel_tool_calls=True)
                session = self._bootstrap(rtmt)
                self.assertNotIn("reasoning", session)
                self.assertNotIn("parallel_tool_calls", session)
                self.assertEqual(session["tools"][0]["name"], "update_order")
                self.assertFalse(rtmt.reasoning_enabled())

    def test_deployment_name_check(self):
        from rtmt import deployment_supports_reasoning
        for name in ("gpt-realtime-2", "gpt-realtime-2.1", "GPT-Realtime-2.1", "mcd-crew-prod", "", None):
            self.assertTrue(deployment_supports_reasoning(name), name)
        for name in ("gpt-realtime-1.5", "gpt-realtime-1", "gpt-realtime", "gpt-realtime-2025-08-28",
                     "gpt-realtime-mini-2025-10-06", "gpt-4o-realtime-preview"):
            self.assertFalse(deployment_supports_reasoning(name), name)

    def test_client_cannot_inject_reasoning(self):
        rtmt = self._rtmt("gpt-realtime-1.5")
        session = rtmt._build_session({"reasoning": {"effort": "high"}, "parallel_tool_calls": True})
        self.assertNotIn("reasoning", session)
        self.assertNotIn("parallel_tool_calls", session)
        rtmt = self._rtmt("gpt-realtime-2.1", reasoning_effort="low")
        session = rtmt._build_session({"reasoning": {"effort": "xhigh"}, "parallel_tool_calls": True})
        self.assertEqual(session["reasoning"], {"effort": "low"})
        self.assertNotIn("parallel_tool_calls", session)

    def test_runtime_rejection_stops_reasoning(self):
        rtmt = self._rtmt(reasoning_effort="low")
        rtmt._reasoning_rejected = True
        self.assertNotIn("reasoning", self._bootstrap(rtmt))
        rtmt.reasoning_model = True                     # a live rejection beats the explicit switch
        self.assertNotIn("reasoning", self._bootstrap(rtmt))

    def test_explicit_reasoning_model_switch_beats_the_name_check(self):
        from rtmt import configure_realtime_model
        cases = [
            # (deployment, config reasoning_model, env switch, reasoning sent?)
            ("gpt-realtime-2.1", "auto", None, True),
            ("gpt-realtime-1.5", "auto", None, False),
            ("gpt-realtime-2.1", False, None, False),          # YAML `false`
            ("gpt-realtime-2.1", "auto", "false", False),
            ("mcd-crew-prod", "auto", None, True),             # unknown name: assumed reasoning (fallback guards it)
            ("mcd-crew-prod", "auto", "false", False),
            ("gpt-4o-crew", "auto", None, False),
            ("gpt-4o-crew", "false", "true", True),           # env wins over config
            ("gpt-realtime-1.5", True, "", True),              # empty env = use config
            ("gpt-realtime-1.5", "bogus", None, False),        # unknown value = auto
        ]
        for deployment, cfg_switch, env_switch, sent in cases:
            with self.subTest(deployment=deployment, cfg=cfg_switch, env=env_switch):
                env = {} if env_switch is None else {"AZURE_OPENAI_REALTIME_REASONING_MODEL": env_switch}
                rtmt = configure_realtime_model(
                    self._rtmt(deployment), {"reasoning_effort": "low", "parallel_tool_calls": False,
                                             "reasoning_model": cfg_switch}, environ=env)
                session = self._bootstrap(rtmt)
                self.assertEqual("reasoning" in session, sent)
                self.assertEqual("parallel_tool_calls" in session, sent)
                self.assertEqual(rtmt.reasoning_enabled(), sent)
                self.assertEqual(session["tools"][0]["name"], "update_order")

    def test_effort_off_or_empty_never_sends_reasoning_even_when_forced(self):
        from rtmt import configure_realtime_model
        for effort_cfg, effort_env in (("", None), ("off", None), ("low", "off"), (None, None)):
            with self.subTest(cfg=effort_cfg, env=effort_env):
                env = {"AZURE_OPENAI_REALTIME_REASONING_MODEL": "true"}
                if effort_env is not None:
                    env["AZURE_OPENAI_REALTIME_REASONING_EFFORT"] = effort_env
                rtmt = configure_realtime_model(self._rtmt(), {"reasoning_effort": effort_cfg}, environ=env)
                self.assertNotIn("reasoning", self._bootstrap(rtmt))
                self.assertFalse(rtmt.reasoning_enabled())

    def test_configure_realtime_model(self):
        from rtmt import configure_realtime_model
        cases = [
            # (config, env, expected effort, expected transcription model)
            ({}, {}, None, "whisper-1"),
            ({"reasoning_effort": "low", "transcription_model": "gpt-4o-transcribe"}, {}, "low", "gpt-4o-transcribe"),
            ({"reasoning_effort": "low"}, {"AZURE_OPENAI_REALTIME_REASONING_EFFORT": "medium"}, "medium", "whisper-1"),
            ({"reasoning_effort": "low"}, {"AZURE_OPENAI_REALTIME_REASONING_EFFORT": "off"}, None, "whisper-1"),
            ({"reasoning_effort": "low"}, {"AZURE_OPENAI_REALTIME_REASONING_EFFORT": ""}, "low", "whisper-1"),
            ({"reasoning_effort": ""}, {}, None, "whisper-1"),
            ({"reasoning_effort": False}, {}, None, "whisper-1"),      # YAML `off` parses to False
            ({"reasoning_effort": "turbo"}, {}, None, "whisper-1"),
            ({"transcription_model": "whisper-1"},
             {"AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL": "my-transcribe-deployment"}, None, "my-transcribe-deployment"),
        ]
        for cfg, env, effort, transcription in cases:
            with self.subTest(cfg=cfg, env=env):
                rtmt = configure_realtime_model(self._rtmt(), cfg, environ=env)
                self.assertEqual(rtmt.reasoning_effort, effort)
                self.assertEqual(rtmt.transcription_model, transcription)
                session = self._bootstrap(rtmt)
                self.assertEqual(session["audio"]["input"]["transcription"], {"model": transcription})
                self.assertEqual(session.get("reasoning"), None if effort is None else {"effort": effort})

    def test_off_is_a_documented_value_not_a_typo(self):
        from rtmt import normalize_reasoning_effort
        for value in ("off", "OFF", "disabled", "false", False, "null", ""):
            with self.subTest(value=value), self.assertNoLogs("mcdonalds-drive-thru", level="WARNING"):
                self.assertIsNone(normalize_reasoning_effort(value))
        with self.assertLogs("mcdonalds-drive-thru", level="WARNING"):
            self.assertIsNone(normalize_reasoning_effort("turbo"))

    def test_client_transcription_model_is_overridden(self):
        # The browser always asks for whisper-1; the server-side choice wins.
        rtmt = self._rtmt(transcription_model="my-transcribe-deployment")
        session = rtmt._build_session({"input_audio_transcription": {"model": "whisper-1"}})
        self.assertEqual(session["audio"]["input"]["transcription"], {"model": "my-transcribe-deployment"})

    def test_shipped_config_is_rollback_safe_and_uses_whisper(self):
        import yaml

        from rtmt import configure_realtime_model
        cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))
        model_cfg = cfg["model"]
        self.assertEqual(model_cfg["transcription_model"], "whisper-1")
        self.assertEqual(model_cfg["reasoning_model"], "auto")
        self.assertEqual(model_cfg["reasoning_effort"], "low")
        self.assertIsNone(model_cfg["parallel_tool_calls"])
        rtmt = configure_realtime_model(self._rtmt("gpt-realtime-2.1"), model_cfg, environ={})
        self.assertEqual(self._bootstrap(rtmt)["reasoning"], {"effort": "low"})
        rtmt = configure_realtime_model(self._rtmt("gpt-realtime-1.5"), model_cfg, environ={})
        session = self._bootstrap(rtmt)
        self.assertNotIn("reasoning", session)
        self.assertNotIn("parallel_tool_calls", session)

    def test_reasoning_model_fields_survive_ga_translation(self):
        session = _to_ga_session({"reasoning": {"effort": "low"}, "parallel_tool_calls": False, "temperature": 0.6})
        self.assertEqual(session["reasoning"], {"effort": "low"})
        self.assertIs(session["parallel_tool_calls"], False)
        self.assertNotIn("temperature", session)

    def test_app_wires_configure_realtime_model(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn("configure_realtime_model(rtmt, model_cfg)", source)

    def test_template_deploys_gpt_realtime_2_1(self):
        bicep = (Path(__file__).resolve().parents[3] / "infra" / "main.bicep").read_text(encoding="utf-8")
        self.assertIn("name: 'gpt-realtime-2.1'", bicep)
        self.assertIn("version: '2026-07-07'", bicep)
        self.assertNotIn("gpt-realtime-1.5", bicep)


class BuildSessionTests(unittest.TestCase):

    def _rtmt(self):
        rtmt = RTMiddleTier("https://fake.openai.azure.com", "gpt-realtime-test",
                            AzureKeyCredential("k"), voice_choice="shimmer")
        rtmt.system_message = SYSTEM_PROMPT
        rtmt.tools["update_order"] = Tool(target=MagicMock(), schema={"type": "function", "name": "update_order"})
        return rtmt

    def test_voice_locked_omits_voice_but_keeps_tools(self):
        session = self._rtmt()._build_session({}, voice_locked=True)
        self.assertNotIn("audio", session)
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["tools"][0]["name"], "update_order")
        self.assertEqual(session["instructions"], SYSTEM_PROMPT)

    def test_voice_locked_keeps_input_audio_settings(self):
        session = self._rtmt()._build_session(dict(BROWSER_SESSION_UPDATE["session"]), voice_locked=True)
        self.assertNotIn("output", session["audio"])
        self.assertIn("turn_detection", session["audio"]["input"])

    def test_unlocked_sets_voice(self):
        session = self._rtmt()._build_session({})
        self.assertEqual(session["audio"]["output"]["voice"], "shimmer")

    def test_bootstrap_payload_is_ga_shaped(self):
        payload = json.loads(self._rtmt().build_bootstrap_session_update())
        self.assertEqual(payload["type"], "session.update")
        session = payload["session"]
        self.assertEqual(session["type"], "realtime")
        for legacy in ("voice", "turn_detection", "input_audio_transcription", "temperature",
                       "max_response_output_tokens", "modalities"):
            self.assertNotIn(legacy, session)


if __name__ == "__main__":
    unittest.main()
