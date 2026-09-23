"""scripts/smoke_realtime.py: the post-deploy proof that the live model accepts our session.

The smoke check is only worth anything if (a) it sends the payloads the app really
sends and (b) it fails when the model would end up without its tools. Both are
exercised here against a fake GA realtime endpoint; the azd hook must never be
able to fail a deployment.
"""

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
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
