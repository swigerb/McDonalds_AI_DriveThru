"""Every browser-facing websocket declines permessage-deflate (L1, round 3).

aiohttp 3.14.2/3.14.3 close a socket with 1002 "Received frame with non-zero
reserved bits" when the first inbound frame is a PONG and the next one is a
compressed data frame (aio-libs/aiohttp#13274) -- i.e. any browser socket that
sat idle past one heartbeat. The cloud socket in rtmt was fixed in the Sonic
parity round; the local-mode sockets in processor_router and /api/ws-test still
used aiohttp's default (compress=True). These tests keep all of them honest.
"""

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

import processor_router as processor_router_module
from processor_router import ProcessorRouter

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _app_sources() -> list[Path]:
    skip = {"tests", ".venv", "__pycache__", "node_modules", "static"}
    return sorted(p for p in BACKEND_DIR.rglob("*.py") if not skip.intersection(p.relative_to(BACKEND_DIR).parts))


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if callee == name:
                found.append(node)
    return found


def _compress_problem(call: ast.Call) -> str | None:
    kw = next((k for k in call.keywords if k.arg == "compress"), None)
    if kw is None:
        return "no compress= (aiohttp defaults to deflate ON)"
    if isinstance(kw.value, ast.Constant) and kw.value.value not in (False, 0):
        return f"compress={kw.value.value!r} enables deflate"
    return None


class CompressionSettingScanTests(unittest.TestCase):
    """Static guard: no WebSocketResponse / ws_connect in app code may use aiohttp's deflate default."""

    def _offenders(self, name: str) -> tuple[list[str], int]:
        offenders, seen = [], 0
        for path in _app_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for call in _calls_named(tree, name):
                seen += 1
                if (problem := _compress_problem(call)) is not None:
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{call.lineno} {name}(...) -- {problem}")
        return offenders, seen

    def test_every_websocket_response_sets_compression(self):
        offenders, seen = self._offenders("WebSocketResponse")
        # rtmt x2, processor_router x3, app.py ws-test x1 -- guards against a scan that finds nothing.
        self.assertGreaterEqual(seen, 6, "scan found fewer WebSocketResponse constructions than exist")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_client_ws_connect_sets_compression(self):
        offenders, seen = self._offenders("ws_connect")
        self.assertGreaterEqual(seen, 1)
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_scanner_flags_a_default_construction(self):
        """The scanner itself must catch the bug shape it exists for."""
        bad = ast.parse("web.WebSocketResponse(heartbeat=15.0)\nWebSocketResponse(compress=True)\n"
                        "web.WebSocketResponse(compress=WS_COMPRESS)\n")
        problems = [_compress_problem(c) for c in _calls_named(bad, "WebSocketResponse")]
        self.assertIsNotNone(problems[0])
        self.assertIsNotNone(problems[1])
        self.assertIsNone(problems[2])

    def test_local_switch_follows_config_and_defaults_off(self):
        from config_loader import get_config
        self.assertIs(processor_router_module.WS_COMPRESS, bool(get_config()["connection"]["ws_compression"]))
        self.assertIs(processor_router_module.WS_COMPRESS, False)


class _EchoLocalProcessor:
    """Stands in for LocalPhi4Processor: echoes every text frame back."""

    async def handle_websocket(self, ws, request):
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await ws.send_str("echo:" + msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                break


class LocalModeSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    """Production framing on the local-mode socket: server PING -> browser PONG -> deflated data frame."""

    async def asyncSetUp(self):
        router = ProcessorRouter(cloud_processor=None, local_processor=_EchoLocalProcessor())
        app = web.Application()
        router.attach_to_app(app, "/realtime")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_first_data_frame_after_heartbeat_pong_survives(self):
        real = web.WebSocketResponse

        def fast_heartbeat(*args, **kwargs):
            return real(*args, **{**kwargs, "heartbeat": 0.2})

        with patch.object(processor_router_module.web, "WebSocketResponse", fast_heartbeat):
            # compress=15 is what Chromium offers on every handshake.
            browser = await self.client.ws_connect("/realtime?mode=local", compress=15, autoping=False)
            self.assertEqual(browser.compress, 0, "local socket negotiated permessage-deflate")
            ping = await asyncio.wait_for(browser.receive(), 5)
            self.assertIs(ping.type, WSMsgType.PING)
            await browser.pong(ping.data)
            await browser.send_str('{"type":"session.update","session":{}}')
            reply = await asyncio.wait_for(browser.receive(), 5)
            while reply.type is WSMsgType.PING:
                await browser.pong(reply.data)
                reply = await asyncio.wait_for(browser.receive(), 5)
            await browser.close()
        self.assertIs(reply.type, WSMsgType.TEXT, f"server killed the socket: {reply.data} {reply.extra}")
        self.assertEqual(reply.data, 'echo:{"type":"session.update","session":{}}')


if __name__ == "__main__":
    unittest.main()
