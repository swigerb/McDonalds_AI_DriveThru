"""ProcessorRouter — delegates WebSocket connections to cloud or local processor.

Sits at the ``/realtime`` WebSocket endpoint and transparently routes each
connection to either RTMiddleTier (cloud / Azure OpenAI) or
LocalPhi4Processor (offline / Phi-4 ONNX) based on configuration and
optional per-connection overrides.

Design invariants:
- If local mode is disabled or unavailable, behaviour is **identical** to
  the pre-router wiring (RTMiddleTier.attach_to_app directly).
- Security checks (origin validation, HMAC tokens, concurrency limits)
  remain in RTMiddleTier — the router does NOT duplicate them.
- Mid-session ``extension.set_local_mode`` messages are intercepted by the
  router and NOT forwarded to the active processor.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from config_loader import get_config
from processor_base import AbstractProcessor

if TYPE_CHECKING:
    from rtmt import RTMiddleTier

logger = logging.getLogger("mcdonalds-drive-thru.router")

_cfg = get_config()
_local_cfg = _cfg.get("local_mode", {})


class ProcessorRouter:
    """Routes WebSocket connections to the appropriate processor.

    Parameters
    ----------
    cloud_processor : RTMiddleTier
        The existing Azure OpenAI Realtime processor.  Always required.
    local_processor : AbstractProcessor | None
        The local Phi-4 ONNX processor.  ``None`` when the model or its
        dependencies are not installed.
    """

    def __init__(
        self,
        cloud_processor: RTMiddleTier,
        local_processor: AbstractProcessor | None = None,
    ) -> None:
        self._cloud = cloud_processor
        self._local = local_processor
        self._default_mode: str = (
            "local" if _local_cfg.get("enabled", False) and local_processor is not None
            else "cloud"
        )
        logger.info(
            "ProcessorRouter initialised — default_mode=%s, local_available=%s",
            self._default_mode,
            local_processor is not None,
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def attach_to_app(self, app: web.Application, path: str) -> None:
        """Register the WebSocket handler at *path*.

        This replaces the direct ``rtmt.attach_to_app()`` call in app.py.
        """
        app.router.add_get(path, self._websocket_handler)

    @property
    def cloud(self) -> RTMiddleTier:
        """Access the underlying cloud processor (for startup/shutdown hooks)."""
        return self._cloud

    @property
    def local(self) -> AbstractProcessor | None:
        """Access the underlying local processor (may be None)."""
        return self._local

    # ── Background tasks (delegate to both processors) ──────────────────────

    def start_background_tasks(self) -> None:
        """Start background tasks for all active processors."""
        self._cloud.start_background_tasks()
        if self._local is not None:
            import asyncio
            asyncio.ensure_future(self._local.start_background_tasks())

    def stop_background_tasks(self) -> None:
        """Stop background tasks for all active processors."""
        self._cloud.stop_background_tasks()
        if self._local is not None:
            import asyncio
            asyncio.ensure_future(self._local.stop_background_tasks())

    # ── Internal routing ────────────────────────────────────────────────────

    def _resolve_mode(self, request: web.Request) -> str:
        """Determine the processor mode for this connection.

        Priority:
        1. ``?mode=local`` or ``?mode=cloud`` query parameter
        2. Default mode from config (``local_mode.enabled``)
        """
        requested = request.query.get("mode", "").lower()
        if requested in ("cloud", "local"):
            return requested
        return self._default_mode

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Main WebSocket entry point — delegates to the active processor.

        When mode is ``cloud``, the request is forwarded **directly** to
        ``RTMiddleTier._websocket_handler`` so all security checks,
        session management, and message forwarding remain unchanged.

        When mode is ``local``, the router performs minimal WebSocket
        setup and hands off to ``LocalPhi4Processor.handle_websocket()``.
        """
        mode = self._resolve_mode(request)

        # ── Cloud mode — full pass-through to RTMiddleTier ──────────────
        if mode == "cloud" or self._local is None:
            if mode == "local" and self._local is None:
                logger.warning(
                    "Local mode requested but unavailable — falling back to cloud"
                )
            return await self._cloud._websocket_handler(request)

        # ── Local mode ──────────────────────────────────────────────────
        logger.info("Routing WebSocket to local processor")

        ws = web.WebSocketResponse(heartbeat=15.0, autoping=True, autoclose=True)
        await ws.prepare(request)

        await self._local.handle_websocket(ws, request)
        return ws
