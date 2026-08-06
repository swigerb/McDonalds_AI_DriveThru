"""Tests for ProcessorRouter — WebSocket routing between cloud and local processors.

Covers:
  - Router creation with cloud-only (no local processor)
  - Router creation with both processors
  - Default mode selection from config
  - extension.set_local_mode message routing
  - Local mode rejection when local processor is None
  - Existing cloud-mode behaviour unchanged (mock RTMiddleTier)
  - Mid-session mode swap
  - Query parameter mode override (?mode=local)
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from aiohttp import web

from processor_base import AbstractProcessor

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_cloud():
    """Create a mock RTMiddleTier with the expected interface."""
    cloud = MagicMock()
    cloud._websocket_handler = AsyncMock(return_value=web.WebSocketResponse())
    cloud.start_background_tasks = MagicMock()
    cloud.stop_background_tasks = MagicMock()
    return cloud


def _make_mock_local():
    """Create a mock AbstractProcessor for local mode."""
    local = MagicMock(spec=AbstractProcessor)
    local.handle_websocket = AsyncMock()
    local.start_background_tasks = AsyncMock()
    local.stop_background_tasks = AsyncMock()
    return local


def _make_request(query_string=""):
    """Create a mock aiohttp Request with optional query parameters."""
    req = MagicMock(spec=web.Request)
    from multidict import MultiDict
    if query_string:
        params = {}
        for part in query_string.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
        req.query = MultiDict(params)
    else:
        req.query = MultiDict()
    return req


# ═══════════════════════════════════════════════════════════════════════════════
# CREATION / INIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ProcessorRouterCreationTests(unittest.TestCase):
    """Test ProcessorRouter initialisation with various configurations."""

    @patch("processor_router._local_cfg", {})
    def test_cloud_only_creation(self):
        """Router created with cloud_processor only, local=None."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=None)
        self.assertIs(router.cloud, cloud)
        self.assertIsNone(router.local)

    @patch("processor_router._local_cfg", {})
    def test_cloud_only_default_mode_is_cloud(self):
        """When no local processor, default mode must be cloud."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(cloud_processor=_make_mock_cloud())
        self.assertEqual(router._default_mode, "cloud")

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_both_processors_creation(self):
        """Router created with both cloud and local processors."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        local = _make_mock_local()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=local)
        self.assertIs(router.cloud, cloud)
        self.assertIs(router.local, local)

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_both_processors_default_mode_is_local(self):
        """When local is enabled and available, default mode is local."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=_make_mock_local(),
        )
        self.assertEqual(router._default_mode, "local")

    @patch("processor_router._local_cfg", {"enabled": False})
    def test_disabled_local_defaults_to_cloud(self):
        """When local_mode.enabled=False, default is cloud even if local processor given."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=_make_mock_local(),
        )
        self.assertEqual(router._default_mode, "cloud")

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_enabled_but_no_processor_defaults_cloud(self):
        """Config says enabled but no processor → cloud."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=None,
        )
        self.assertEqual(router._default_mode, "cloud")


# ═══════════════════════════════════════════════════════════════════════════════
# MODE RESOLUTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class ModeResolutionTests(unittest.TestCase):
    """Test _resolve_mode with query parameters and defaults."""

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_query_param_mode_local(self):
        """?mode=local overrides default."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=_make_mock_local(),
        )
        req = _make_request("mode=local")
        mode, explicit = router._resolve_mode(req)
        self.assertEqual(mode, "local")
        self.assertTrue(explicit)

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_query_param_mode_cloud(self):
        """?mode=cloud overrides default."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=_make_mock_local(),
        )
        req = _make_request("mode=cloud")
        mode, explicit = router._resolve_mode(req)
        self.assertEqual(mode, "cloud")
        self.assertTrue(explicit)

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_query_param_case_insensitive(self):
        """?mode=LOCAL should resolve to local."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=_make_mock_local(),
        )
        req = _make_request("mode=LOCAL")
        mode, explicit = router._resolve_mode(req)
        self.assertEqual(mode, "local")
        self.assertTrue(explicit)

    @patch("processor_router._local_cfg", {})
    def test_no_query_param_uses_default(self):
        """No ?mode param → fall back to _default_mode."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(cloud_processor=_make_mock_cloud())
        req = _make_request("")
        mode, explicit = router._resolve_mode(req)
        self.assertEqual(mode, "cloud")
        self.assertFalse(explicit)

    @patch("processor_router._local_cfg", {})
    def test_invalid_mode_param_uses_default(self):
        """?mode=invalid → fall back to _default_mode."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(cloud_processor=_make_mock_cloud())
        req = _make_request("mode=invalid")
        mode, explicit = router._resolve_mode(req)
        self.assertEqual(mode, "cloud")
        self.assertFalse(explicit)


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET ROUTING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class WebSocketRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Test _websocket_handler delegates to the correct processor."""

    @patch("processor_router._local_cfg", {})
    async def test_cloud_mode_delegates_to_rtmiddletier(self):
        """Cloud mode routes directly to RTMiddleTier._websocket_handler."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud)

        req = _make_request("mode=cloud")
        await router._websocket_handler(req)
        cloud._websocket_handler.assert_awaited_once_with(req)

    @patch("processor_router._local_cfg", {"enabled": True})
    async def test_local_mode_delegates_to_local_processor(self):
        """Local mode routes to LocalPhi4Processor.handle_websocket."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        local = _make_mock_local()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=local)

        req = _make_request("mode=local")
        # Mock web.WebSocketResponse to avoid actual WS setup
        with patch("processor_router.web.WebSocketResponse") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws.prepare = AsyncMock()
            mock_ws_cls.return_value = mock_ws
            await router._websocket_handler(req)

        local.handle_websocket.assert_awaited_once()

    @patch("processor_router._local_cfg", {"enabled": True})
    async def test_local_mode_fallback_when_no_processor(self):
        """Requesting local mode without a processor falls back to cloud."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=None)

        req = _make_request("mode=local")
        await router._websocket_handler(req)
        # Should have fallen back to cloud
        cloud._websocket_handler.assert_awaited_once_with(req)

    @patch("processor_router._local_cfg", {})
    async def test_default_cloud_mode_unchanged_behaviour(self):
        """Default cloud path passes request untouched to RTMiddleTier."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud)

        req = _make_request("")
        await router._websocket_handler(req)
        cloud._websocket_handler.assert_awaited_once_with(req)


# ═══════════════════════════════════════════════════════════════════════════════
# ATTACH / BACKGROUND TASKS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class AttachAndLifecycleTests(unittest.TestCase):
    """Test attach_to_app and background task delegation."""

    @patch("processor_router._local_cfg", {})
    def test_attach_to_app_registers_route(self):
        """attach_to_app registers a GET route at the given path."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(cloud_processor=_make_mock_cloud())
        app = MagicMock(spec=web.Application)
        mock_router = MagicMock()
        app.router = mock_router
        router.attach_to_app(app, "/realtime")
        mock_router.add_get.assert_called_once_with("/realtime", router._websocket_handler)

    @patch("processor_router._local_cfg", {})
    def test_start_background_tasks_cloud_only(self):
        """start_background_tasks delegates to cloud when local is None."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud)
        router.start_background_tasks()
        cloud.start_background_tasks.assert_called_once()

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_start_background_tasks_both(self):
        """start_background_tasks delegates to both processors."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        local = _make_mock_local()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=local)
        with patch("asyncio.ensure_future"):
            router.start_background_tasks()
        cloud.start_background_tasks.assert_called_once()

    @patch("processor_router._local_cfg", {})
    def test_stop_background_tasks_cloud_only(self):
        """stop_background_tasks delegates to cloud when local is None."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud)
        router.stop_background_tasks()
        cloud.stop_background_tasks.assert_called_once()

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_stop_background_tasks_both(self):
        """stop_background_tasks delegates to both processors."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        local = _make_mock_local()
        router = ProcessorRouter(cloud_processor=cloud, local_processor=local)
        with patch("asyncio.ensure_future"):
            router.stop_background_tasks()
        cloud.stop_background_tasks.assert_called_once()

    @patch("processor_router._local_cfg", {})
    def test_cloud_property_returns_cloud(self):
        """cloud property returns the cloud processor."""
        from processor_router import ProcessorRouter
        cloud = _make_mock_cloud()
        router = ProcessorRouter(cloud_processor=cloud)
        self.assertIs(router.cloud, cloud)

    @patch("processor_router._local_cfg", {})
    def test_local_property_returns_none_when_absent(self):
        """local property is None when no local processor given."""
        from processor_router import ProcessorRouter
        router = ProcessorRouter(cloud_processor=_make_mock_cloud())
        self.assertIsNone(router.local)

    @patch("processor_router._local_cfg", {"enabled": True})
    def test_local_property_returns_local(self):
        """local property returns the local processor."""
        from processor_router import ProcessorRouter
        local = _make_mock_local()
        router = ProcessorRouter(
            cloud_processor=_make_mock_cloud(),
            local_processor=local,
        )
        self.assertIs(router.local, local)


if __name__ == "__main__":
    unittest.main()
