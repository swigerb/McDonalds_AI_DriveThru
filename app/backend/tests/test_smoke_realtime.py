"""scripts/smoke_realtime.py: the post-deploy proof that the live model accepts our session.

The smoke check is only worth anything if (a) it sends the payloads the app really
sends and (b) it fails when the model would end up without its tools. Both are
exercised here against a fake GA realtime endpoint; the azd hook must never be
able to fail a deployment.
"""

import asyncio
import base64
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.append(str(Path(__file__).resolve().parents[1]))

import smoke_realtime  # noqa: E402

import tools  # noqa: E402
from prompt_loader import PromptLoader  # noqa: E402

TOOLS = sorted(smoke_realtime.EXPECTED_TOOLS)
# Beta-era keys GA rejects wholesale if they reach the top level of `session`.
BETA_KEYS = {"voice", "turn_detection", "input_audio_transcription", "input_audio_format",
             "output_audio_format", "modalities", "temperature", "max_response_output_tokens"}


class EchoGA:
    """Fake /openai/v1/realtime: merges accepted session.updates and echoes them back."""

    def __init__(self):
        self.session = {"type": "realtime", "tools": [], "tool_choice": "auto", "instructions": ""}
        self.reject_keys = set(BETA_KEYS)
        self.ignore_tools = False     # accept the update but silently keep no tools
        self.force_tool_choice = None
        self.received = []

    def app(self):
        app = web.Application()
        app.router.add_get("/openai/v1/realtime", self.handler)
        return app

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            event = json.loads(msg.data)
            self.received.append(event)
            if event.get("type") != "session.update":
                continue
            session = event["session"]
            bad = sorted(k for k in session if k in self.reject_keys)
            if bad:
                await ws.send_json({"type": "error", "error": {
                    "type": "invalid_request_error", "code": "unknown_parameter",
                    "param": f"session.{bad[0]}", "event_id": event.get("event_id"), "message": "nope"}})
                continue
            for key, value in session.items():
                if key == "tools" and self.ignore_tools:
                    continue
                self.session[key] = value
            if self.force_tool_choice:
                self.session["tool_choice"] = self.force_tool_choice
            await ws.send_json({"type": "session.updated", "session": self.session})
        return ws


def _isolate_tools_global(test):
    """attach_tools_rtmt sets the module-global tools._prompt_loader; restore it so
    the smoke tests don't change upsell hints for tests that run later."""
    patcher = patch.object(tools, "_prompt_loader", tools._prompt_loader)
    patcher.start()
    test.addCleanup(patcher.stop)


class SmokeAgainstFakeRealtimeTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        _isolate_tools_global(self)
        self.fake = EchoGA()
        self.server = TestServer(self.fake.app())
        await self.server.start_server()
        self.url = str(self.server.make_url("/openai/v1/realtime?model=gpt-realtime-2.1")).replace("http", "ws", 1)
        self.rtmt = smoke_realtime.build_middle_tier("https://example.openai.azure.com", "gpt-realtime-2.1",
                                                     environ={})

    async def asyncTearDown(self):
        await self.server.close()

    async def _check(self):
        return await smoke_realtime.check_session_updates(self.rtmt, self.url, {}, timeout=3)

    async def test_passes_when_every_payload_registers_the_tools(self):
        failures, report = await self._check()

        self.assertEqual(failures, [])
        self.assertEqual([line.split(":")[0] for line in report],
                         ["PASS  bootstrap", "PASS  relayed browser session.update", "PASS  minimal fallback"])
        sent = [e["session"] for e in self.fake.received]
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0], json.loads(self.rtmt.build_bootstrap_session_update())["session"])
        self.assertEqual(set(sent[2]), {"type", "instructions", "tools", "tool_choice"})
        self.assertEqual(sent[0]["reasoning"], {"effort": "low"}, "shipped config.yaml: reasoning low on 2.1")

    async def test_fails_when_the_model_rejects_a_payload(self):
        self.fake.reject_keys |= {"reasoning"}      # e.g. 2.1 swapped for 1.5 behind the same name
        failures, _ = await self._check()

        self.assertEqual(len(failures), 2, failures)   # bootstrap + relayed; fallback has no reasoning
        self.assertTrue(all("REJECTED" in f and "session.reasoning" in f for f in failures))

    async def test_fails_when_tools_do_not_register(self):
        self.fake.ignore_tools = True
        failures, _ = await self._check()

        self.assertEqual(len(failures), 3)
        self.assertTrue(all("tools registered []" in f for f in failures), failures)

    async def test_fails_when_tool_choice_is_not_auto(self):
        self.fake.force_tool_choice = "none"
        failures, _ = await self._check()

        self.assertEqual(len(failures), 3)
        self.assertTrue(all("tool_choice='none'" in f for f in failures), failures)

    async def test_run_exit_codes(self):
        with patch.object(smoke_realtime, "realtime_url", return_value=self.url), \
                patch.object(smoke_realtime, "get_auth_headers", return_value={}), \
                patch.dict("os.environ", {"AZURE_OPENAI_REALTIME_VOICE_CHOICE": "marin"}), \
                redirect_stdout(io.StringIO()) as out:
            ok = await smoke_realtime.run("https://x", "gpt-realtime-2.1", voice=None, timeout=3,
                                          skip_transcription=True)
            self.fake.session["tools"], self.fake.ignore_tools = [], True   # a fresh deployment
            bad = await smoke_realtime.run("https://x", "gpt-realtime-2.1", voice=None, timeout=3,
                                           skip_transcription=True)
        self.assertEqual((ok, bad), (0, 1))
        self.assertIn("SMOKE CHECK PASSED", out.getvalue())
        self.assertIn("SMOKE CHECK FAILED", out.getvalue())


class SmokePayloadTests(unittest.TestCase):

    def setUp(self):
        _isolate_tools_global(self)

    def test_middle_tier_uses_the_real_prompt_and_tools(self):
        rtmt = smoke_realtime.build_middle_tier("https://example.openai.azure.com", "gpt-realtime-2.1", environ={})
        session = json.loads(rtmt.build_bootstrap_session_update())["session"]

        self.assertEqual(session["instructions"], PromptLoader().get_system_prompt())
        self.assertIn("McDonald", session["instructions"])
        self.assertEqual(sorted(t["name"] for t in session["tools"]), TOOLS)
        expected = {s["name"]: s for s in PromptLoader().get_tool_schemas()}
        for tool in session["tools"]:
            self.assertEqual(tool["parameters"], expected[tool["name"]]["parameters"])
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["audio"]["output"]["voice"], "marin")

    def test_env_overrides_reach_the_payload(self):
        rtmt = smoke_realtime.build_middle_tier(
            "https://e", "gpt-realtime-2.1",
            environ={"AZURE_OPENAI_REALTIME_REASONING_EFFORT": "medium", "AZURE_OPENAI_REALTIME_VOICE_CHOICE": "cedar"})
        session = json.loads(rtmt.build_bootstrap_session_update())["session"]
        self.assertEqual(session["reasoning"], {"effort": "medium"})
        self.assertEqual(session["audio"]["output"]["voice"], "cedar")

        rollback = smoke_realtime.build_middle_tier("https://e", "gpt-realtime-1.5", environ={})
        self.assertNotIn("reasoning", json.loads(rollback.build_bootstrap_session_update())["session"])

    def test_check_session_reports_each_problem(self):
        good = {"tools": [{"name": n} for n in TOOLS], "tool_choice": "auto", "instructions": "x",
                "audio": {"output": {"voice": "marin"}}, "reasoning": {"effort": "low"}}
        sent = {"audio": {"output": {"voice": "marin"}}}
        check = smoke_realtime.check_session
        self.assertEqual(check("b", sent, good, None, {"effort": "low"}), [])
        cases = {
            "tools registered": {**good, "tools": good["tools"][:3]},
            "tool_choice": {**good, "tool_choice": "required"},
            "instructions": {**good, "instructions": ""},
            "voice": {**good, "audio": {"output": {"voice": "alloy"}}},
            "reasoning": {**good, "reasoning": None},
        }
        for needle, echoed in cases.items():
            with self.subTest(needle):
                problems = check("b", sent, echoed, None, {"effort": "low"})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(needle, problems[0])
        rejected = check("b", sent, None, {"error": {"code": "invalid_value", "param": "session.x"}}, None)
        self.assertEqual(len(rejected), 1)
        self.assertIn("REJECTED", rejected[0])

    def test_realtime_url(self):
        self.assertEqual(smoke_realtime.realtime_url("https://cog-x.openai.azure.com/", "gpt-realtime-2.1"),
                         "wss://cog-x.openai.azure.com/openai/v1/realtime?model=gpt-realtime-2.1")

    def test_missing_settings_exit_2_without_touching_the_network(self):
        with patch.object(smoke_realtime, "_azd_env_values", return_value={}), \
                patch.dict("os.environ", {}, clear=True), \
                patch.object(smoke_realtime, "run") as run, redirect_stderr(io.StringIO()):
            self.assertEqual(smoke_realtime.main([]), 2)
        run.assert_not_called()

    def test_could_not_run_is_exit_2(self):
        async def boom(*args, **kwargs):
            raise smoke_realtime.SmokeError("no token")
        with patch.object(smoke_realtime, "run", boom), redirect_stderr(io.StringIO()) as err:
            code = smoke_realtime.main(["--endpoint", "https://e", "--deployment", "d"])
        self.assertEqual(code, 2)
        self.assertIn("no token", err.getvalue())


class SynthesizeTests(unittest.IsolatedAsyncioTestCase):
    """The test audio must be the phrase read aloud, not the model's reply to it (Dunkin c4249de)."""

    async def test_phrase_is_sent_as_response_instructions_not_a_user_turn(self):
        received = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                event = json.loads(msg.data)
                received.append(event)
                if event["type"] == "response.create":
                    await ws.send_json({"type": "response.output_audio.delta", "delta": "AAAA"})
                    await ws.send_json({"type": "response.done"})
            return ws

        app = web.Application()
        app.router.add_get("/openai/v1/realtime", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            url = str(server.make_url("/openai/v1/realtime?model=gpt-realtime-2.1-dz")).replace("http", "ws", 1)
            pcm = await smoke_realtime._synthesize(url, {}, smoke_realtime.TRANSCRIPTION_PHRASE, 5)
        finally:
            await server.close()

        self.assertEqual(pcm, b"\x00\x00\x00")
        kinds = [e["type"] for e in received]
        self.assertNotIn("conversation.item.create", kinds)
        create = next(e for e in received if e["type"] == "response.create")
        self.assertIn(f'"{smoke_realtime.TRANSCRIPTION_PHRASE}"', create["response"]["instructions"])
        self.assertIn("word for word", create["response"]["instructions"])
        self.assertNotIn(smoke_realtime.TRANSCRIPTION_PHRASE, received[0]["session"]["instructions"])
        self.assertIsNone(received[0]["session"]["audio"]["input"]["turn_detection"])


# What Sonic's check passed on (2026-09-23): gpt-realtime-2.1 answered the phrase instead of reciting it.
ANSWERED_TRANSCRIPT = "Sure, I can't place the order for you, but it sounds tasty! Anything else?"


class TranscriptVerbatimTests(unittest.TestCase):
    """The transcription step passes only on (essentially) the phrase word for word."""

    def test_answered_instead_of_recited_fails(self):
        for transcript in (ANSWERED_TRANSCRIPT,
                           "Sure! A Big Mac meal with a large Coke. Anything else for you today?",
                           "Hi, can I get a Big Mac meal, please?"):
            with self.subTest(transcript=transcript):
                failures, report = smoke_realtime.judge_transcript("whisper-1", transcript)
                self.assertEqual(len(failures), 1, failures)
                self.assertIn("is not the test phrase", failures[0])
                self.assertEqual(report, [])

    def test_verbatim_passes_whatever_the_case_and_punctuation(self):
        for transcript in (smoke_realtime.TRANSCRIPTION_PHRASE,
                           "hi can i get a big mac meal with a large coke please",
                           "Hi! Can I get a Big Mac meal with a large Coke please.",
                           "Hey, can I get a Big Mac meal with a large Coke, please?"):   # one whisper slip
            with self.subTest(transcript=transcript):
                failures, report = smoke_realtime.judge_transcript("whisper-1", transcript)
                self.assertEqual(failures, [])
                self.assertTrue(report[0].startswith("PASS  transcription (whisper-1)"), report)

    def test_empty_transcript_fails(self):
        failures, _ = smoke_realtime.judge_transcript("whisper-1", "  ")
        self.assertIn("empty transcript", failures[0])

    def test_similarity_normalises_but_keeps_word_order(self):
        sim = smoke_realtime.transcript_similarity
        self.assertEqual(sim("Big Mac, please!", "big mac please"), 1.0)
        self.assertEqual(sim("Café au lait", "cafe au lait"), 1.0)
        self.assertLess(sim("big mac please", "please mac big"), smoke_realtime.TRANSCRIPTION_MIN_SIMILARITY)
        self.assertEqual(sim("x", ""), 0.0)


class TranscribeGA:
    """Fake /openai/v1/realtime for check_transcription: synthesises 'audio', then 'transcribes' it as `transcript`."""

    def __init__(self, transcript):
        self.transcript = transcript

    def app(self):
        app = web.Application()
        app.router.add_get("/openai/v1/realtime", self.handler)
        return app

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            event = json.loads(msg.data)
            if event["type"] == "session.update":
                await ws.send_json({"type": "session.updated", "session": event["session"]})
            elif event["type"] == "response.create":
                await ws.send_json({"type": "response.output_audio.delta",
                                    "delta": base64.b64encode(b"\x00" * 9600).decode()})
                await ws.send_json({"type": "response.done"})
            elif event["type"] == "input_audio_buffer.commit":
                await ws.send_json({"type": "conversation.item.input_audio_transcription.completed",
                                    "transcript": self.transcript})
        return ws


class CheckTranscriptionTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        _isolate_tools_global(self)
        self.rtmt = smoke_realtime.build_middle_tier("https://e", "gpt-realtime-2.1-dz", environ={})

    async def _run(self, transcript):
        server = TestServer(TranscribeGA(transcript).app())
        await server.start_server()
        try:
            url = str(server.make_url("/openai/v1/realtime?model=gpt-realtime-2.1-dz")).replace("http", "ws", 1)
            return await smoke_realtime.check_transcription(self.rtmt, url, {}, 5)
        finally:
            await server.close()

    async def test_answered_transcript_fails_the_check(self):
        failures, report = await self._run(ANSWERED_TRANSCRIPT)
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("whisper-1", failures[0])
        self.assertEqual(report, [])

    async def test_verbatim_transcript_passes_the_check(self):
        failures, report = await self._run("Hi, can I get a Big Mac meal with a large Coke, please?")
        self.assertEqual(failures, [])
        self.assertIn("similarity 1.00", report[0])

    async def test_run_exits_1_on_an_answered_transcript(self):
        server = TestServer(TranscribeGA(ANSWERED_TRANSCRIPT).app())
        await server.start_server()
        try:
            url = str(server.make_url("/openai/v1/realtime?model=gpt-realtime-2.1-dz")).replace("http", "ws", 1)
            with patch.object(smoke_realtime, "check_session_updates", return_value=([], [])), \
                    patch.object(smoke_realtime, "realtime_url", return_value=url), \
                    redirect_stdout(io.StringIO()) as out:
                code = await smoke_realtime.run("https://x", "gpt-realtime-2.1-dz", voice=None, timeout=5,
                                                skip_transcription=False, headers={})
        finally:
            await server.close()
        self.assertEqual(code, 1)
        self.assertIn("SMOKE CHECK FAILED", out.getvalue())


class TenantTests(unittest.TestCase):
    """The token must come from the resource's tenant, not the active `az` or `azd` default (Dunkin c4249de)."""

    NAMES = ["AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID", "AZURE_OPENAI_EASTUS2_ENDPOINT",
             "AZURE_OPENAI_REALTIME_DEPLOYMENT"]
    AZD = {"AZURE_OPENAI_EASTUS2_ENDPOINT": "https://x", "AZURE_OPENAI_REALTIME_DEPLOYMENT": "d",
           "AZURE_TENANT_ID": "azd-tenant", "AZURE_SUBSCRIPTION_ID": "azd-sub"}
    EXPLICIT = ["--endpoint", "https://x", "--deployment", "d"]

    def _main_identity(self, argv, env, azd):
        seen = {}

        async def fake_run(*_a, **kwargs):
            seen["identity"] = (kwargs.get("tenant_id"), kwargs.get("subscription_id"))
            return 0
        saved = {n: os.environ.pop(n, None) for n in self.NAMES}
        try:
            os.environ.update(env)
            with patch.object(smoke_realtime, "_azd_env_values", return_value=azd), \
                    patch.object(smoke_realtime, "run", fake_run):
                self.assertEqual(smoke_realtime.main(argv), 0)
        finally:
            for n in self.NAMES:
                os.environ.pop(n, None)
                if saved[n] is not None:
                    os.environ[n] = saved[n]
        return seen["identity"]

    def test_azd_env_identity_is_used(self):
        self.assertEqual(self._main_identity([], {}, self.AZD), ("azd-tenant", "azd-sub"))

    def test_azd_env_identity_is_used_even_with_explicit_endpoint_and_deployment(self):
        self.assertEqual(self._main_identity(self.EXPLICIT, {}, self.AZD), ("azd-tenant", "azd-sub"))

    def test_env_then_cli_override_azd(self):
        env = {"AZURE_TENANT_ID": "env-tenant", "AZURE_SUBSCRIPTION_ID": "env-sub"}
        self.assertEqual(self._main_identity([], env, self.AZD), ("env-tenant", "env-sub"))
        self.assertEqual(self._main_identity(["--tenant", "cli-tenant", "--subscription", "cli-sub"], env, self.AZD),
                         ("cli-tenant", "cli-sub"))

    def test_each_value_falls_back_to_azd_independently(self):
        self.assertEqual(self._main_identity(["--tenant", "cli-tenant"], {}, self.AZD), ("cli-tenant", "azd-sub"))
        self.assertEqual(self._main_identity([], {"AZURE_SUBSCRIPTION_ID": "env-sub"}, self.AZD),
                         ("azd-tenant", "env-sub"))

    def test_nothing_anywhere_is_none(self):
        self.assertEqual(self._main_identity(self.EXPLICIT, {}, {}), (None, None))

    def test_run_passes_identity_to_auth(self):
        seen = {}

        def fake_auth(*args):
            seen["args"] = args
            raise smoke_realtime.SmokeError("stop here")
        with patch.object(smoke_realtime, "get_auth_headers", fake_auth), \
                self.assertRaises(smoke_realtime.SmokeError), redirect_stdout(io.StringIO()):
            asyncio.run(smoke_realtime.run("https://x", "d", voice=None, timeout=1, skip_transcription=True,
                                           tenant_id="t", subscription_id="s"))
        self.assertEqual(seen["args"], ("t", "s"))

    def _auth(self, tenant_id, subscription_id, fail=()):
        """Returns (headers or SmokeError, credentials built, credentials asked for a token)."""
        built, asked = [], []

        class FakeCred:
            def __init__(self, kind, **kwargs):
                self.kind = kind
                built.append((kind, kwargs.get("tenant_id") or kwargs.get("subscription")))

            def get_token(self, *scopes, **_kw):
                asked.append(self.kind)
                if self.kind in fail:
                    raise RuntimeError(f"{self.kind} said no\nsecond line")
                return SimpleNamespace(token=f"tok-{self.kind}", expires_on=0)

        def az(**k):
            return FakeCred("az-sub" if k.get("subscription") else "az", **k)

        import azure.identity as identity
        with patch.dict(os.environ, {"AZURE_OPENAI_EASTUS2_API_KEY": ""}), \
                patch.object(identity, "AzureDeveloperCliCredential", lambda **k: FakeCred("azd", **k)), \
                patch.object(identity, "AzureCliCredential", az), \
                patch.object(identity, "DefaultAzureCredential", lambda **k: FakeCred("default", **k)):
            try:
                result = smoke_realtime.get_auth_headers(tenant_id, subscription_id)
            except smoke_realtime.SmokeError as exc:
                result = exc
        return result, built, asked

    def test_subscription_first_then_tenant_pinned_clis(self):
        headers, built, asked = self._auth("tenant-x", "sub-y")
        self.assertEqual(built, [("az-sub", "sub-y"), ("azd", "tenant-x"), ("az", "tenant-x")])
        self.assertEqual(asked, ["az-sub"])
        self.assertEqual(headers, {"Authorization": "Bearer " + "tok-az-sub"})

    def test_hard_failure_moves_on_to_the_next_credential(self):
        headers, _, asked = self._auth("tenant-x", "sub-y", fail=("az-sub", "azd"))
        self.assertEqual(asked, ["az-sub", "azd", "az"])
        self.assertEqual(headers, {"Authorization": "Bearer " + "tok-az"})

    def test_all_failing_reports_every_credential(self):
        err, _, asked = self._auth("tenant-x", "sub-y", fail=("az-sub", "azd", "az"))
        self.assertIsInstance(err, smoke_realtime.SmokeError)
        self.assertEqual(asked, ["az-sub", "azd", "az"])
        self.assertIn("az-sub said no", str(err))
        self.assertIn("azd said no", str(err))
        self.assertNotIn("second line", str(err))

    def test_tenant_only_pins_both_clis(self):
        _, built, _ = self._auth("tenant-x", None)
        self.assertEqual(built, [("azd", "tenant-x"), ("az", "tenant-x")])

    def test_subscription_only(self):
        _, built, _ = self._auth(None, "sub-y")
        self.assertEqual(built, [("az-sub", "sub-y")])

    def test_nothing_falls_back_to_default_credential(self):
        headers, built, _ = self._auth(None, None)
        self.assertEqual(built, [("default", None)])
        self.assertEqual(headers, {"Authorization": "Bearer " + "tok-default"})

    def test_api_key_short_circuits_entra(self):
        with patch.dict(os.environ, {"AZURE_OPENAI_EASTUS2_API_KEY": "k"}):
            self.assertEqual(smoke_realtime.get_auth_headers("t", "s"), {"api-key": "k"})


class PostdeployHookTests(unittest.TestCase):
    """The hook must never fail an `azd up` (anonymous external users run this template)."""

    def test_azure_yaml_postdeploy_hook(self):
        import yaml
        hook = yaml.safe_load((REPO_ROOT / "azure.yaml").read_text(encoding="utf-8"))["hooks"]["postdeploy"]
        for platform, script in (("windows", "./scripts/smoke_realtime.ps1"), ("posix", "./scripts/smoke_realtime.sh")):
            with self.subTest(platform):
                self.assertEqual(hook[platform]["run"], script)
                self.assertIs(hook[platform]["continueOnError"], True)
                self.assertIs(hook[platform]["interactive"], False)
                self.assertTrue((REPO_ROOT / script).is_file())

    def test_wrappers_always_exit_zero(self):
        for name in ("smoke_realtime.ps1", "smoke_realtime.sh"):
            with self.subTest(name):
                text = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
                exits = re.findall(r"^\s*exit\b.*$", text, flags=re.MULTILINE)
                self.assertTrue(exits)
                self.assertEqual({e.strip() for e in exits}, {"exit 0"})
                self.assertNotIn("set -e", text)
                self.assertIn("MCD_SKIP_REALTIME_SMOKE", text)
                self.assertIn("smoke_realtime.py", text)


if __name__ == "__main__":
    unittest.main()
