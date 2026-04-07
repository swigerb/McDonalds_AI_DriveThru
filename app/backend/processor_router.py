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
- When mode resolves to ``cloud`` but the Azure endpoint is unreachable
  and a local processor is available, the router auto-falls back to local
  mode so offline usage never silently hangs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from config_loader import get_config
from processor_base import AbstractProcessor

if TYPE_CHECKING:
    from rtmt import RTMiddleTier

logger = logging.getLogger("mcdonalds-drive-thru.router")
pipeline_logger = logging.getLogger("local-pipeline")

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

    # Cache TTL for cloud reachability probe (seconds)
    _CLOUD_CHECK_CACHE_TTL: float = 30.0
    # Short timeout for the cloud probe (seconds) — must be fast enough
    # that a WebSocket handshake doesn't visibly stall.
    _CLOUD_CHECK_TIMEOUT_TOTAL: float = 1.0
    _CLOUD_CHECK_TIMEOUT_CONNECT: float = 0.5

    def __init__(
        self,
        cloud_processor: RTMiddleTier | None,
        local_processor: AbstractProcessor | None = None,
    ) -> None:
        self._cloud = cloud_processor
        self._local = local_processor
        self._default_mode: str = (
            "local" if _local_cfg.get("enabled", False) and local_processor is not None
            else "cloud"
        )
        # When cloud processor is unavailable, force local if available
        if self._cloud is None and self._local is not None:
            self._default_mode = "local"
        # Runtime-togglable mode (updated via /api/local-mode/toggle)
        self._runtime_mode: str | None = None
        # Cloud reachability cache — avoids repeating network probes on
        # every WebSocket connection.  ``None`` means "not yet checked".
        self._cloud_reachable: bool | None = None
        self._last_cloud_check: float = 0.0
        # Diagnostics counters
        self._ws_connection_count: int = 0
        self._active_connections: int = 0
        self._last_error: str | None = None
        self._last_error_time: float | None = None
        logger.info(
            "ProcessorRouter initialised — default_mode=%s, local_available=%s, cloud_available=%s",
            self._default_mode,
            local_processor is not None,
            cloud_processor is not None,
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def attach_to_app(self, app: web.Application, path: str) -> None:
        """Register the WebSocket handler at *path*.

        This replaces the direct ``rtmt.attach_to_app()`` call in app.py.
        """
        app.router.add_get(path, self._websocket_handler)
        pipeline_logger.info(
            "WebSocket route registered at %s (cloud=%s, local=%s)",
            path,
            "yes" if self._cloud is not None else "no",
            "yes" if self._local is not None else "no",
        )

    @property
    def cloud(self) -> RTMiddleTier | None:
        """Access the underlying cloud processor (for startup/shutdown hooks)."""
        return self._cloud

    @property
    def local(self) -> AbstractProcessor | None:
        """Access the underlying local processor (may be None)."""
        return self._local

    @property
    def active_mode(self) -> str:
        """Current effective mode (respects runtime toggle)."""
        return self._runtime_mode or self._default_mode

    @property
    def ws_connection_count(self) -> int:
        return self._ws_connection_count

    @property
    def active_connections(self) -> int:
        return self._active_connections

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def set_runtime_mode(self, mode: str) -> None:
        """Dynamically toggle the default mode at runtime.

        Called by ``/api/local-mode/toggle`` when the user switches modes
        in the UI. Accepts ``"local"``, ``"cloud"``, or ``"auto"``.
        ``"auto"`` clears the runtime override and falls back to config.
        """
        if mode == "auto":
            self._runtime_mode = None
            logger.info("Runtime mode cleared — using config default '%s'", self._default_mode)
        elif mode in ("cloud", "local"):
            self._runtime_mode = mode
            logger.info("Runtime mode set to '%s'", mode)
        else:
            logger.warning("Invalid runtime mode '%s' — ignoring", mode)

    # ── Background tasks (delegate to both processors) ──────────────────────

    def start_background_tasks(self) -> None:
        """Start background tasks for all active processors."""
        if self._cloud is not None:
            self._cloud.start_background_tasks()
        if self._local is not None:
            import asyncio
            asyncio.ensure_future(self._local.start_background_tasks())

    def stop_background_tasks(self) -> None:
        """Stop background tasks for all active processors."""
        if self._cloud is not None:
            self._cloud.stop_background_tasks()
        if self._local is not None:
            import asyncio
            asyncio.ensure_future(self._local.stop_background_tasks())

    # ── Internal routing ────────────────────────────────────────────────────

    def _resolve_mode(self, request: web.Request) -> tuple[str, bool]:
        """Determine the processor mode for this connection.

        Returns (mode, explicit) where *explicit* is True when the mode
        came from the ``?mode=`` query parameter (user's deliberate choice).

        Priority:
        1. ``?mode=local`` or ``?mode=cloud`` query parameter
        2. Runtime mode (set via /api/local-mode/toggle)
        3. Default mode from config (``local_mode.enabled``)
        """
        requested = request.query.get("mode", "").lower()
        if requested in ("cloud", "local"):
            pipeline_logger.info(
                "[VOICE/ROUTING] Mode resolved: %s (query=%s, runtime=%s, default=%s)",
                requested, requested, self._runtime_mode, self._default_mode,
            )
            return requested, True
        if self._runtime_mode is not None:
            pipeline_logger.info(
                "[VOICE/ROUTING] Mode resolved: %s (query=<none>, runtime=%s, default=%s)",
                self._runtime_mode, self._runtime_mode, self._default_mode,
            )
            return self._runtime_mode, False
        pipeline_logger.info(
            "[VOICE/ROUTING] Mode resolved: %s (query=<none>, runtime=<none>, default=%s)",
            self._default_mode, self._default_mode,
        )
        return self._default_mode, False

    def invalidate_cloud_cache(self) -> None:
        """Mark the cached cloud reachability as stale.

        Called when a cloud connection actually fails so the next
        WebSocket connection immediately falls back to local.
        """
        self._cloud_reachable = False
        self._last_cloud_check = time.time()
        pipeline_logger.info("Cloud reachability cache invalidated → unreachable")

    async def _check_cloud_reachable(self) -> bool:
        """Cached connectivity probe to the Azure OpenAI endpoint.

        Uses a short timeout (1s total / 500ms connect) and caches the
        result for ``_CLOUD_CHECK_CACHE_TTL`` seconds so that subsequent
        WebSocket connections don't repeat the probe.  This means the
        *first* offline connection waits ≤1s instead of 3s, and every
        connection after that is instant.
        """
        now = time.time()
        if (
            self._cloud_reachable is not None
            and (now - self._last_cloud_check) < self._CLOUD_CHECK_CACHE_TTL
        ):
            pipeline_logger.debug(
                "Cloud reachability (cached, age=%.1fs): %s",
                now - self._last_cloud_check,
                self._cloud_reachable,
            )
            return self._cloud_reachable

        endpoint = getattr(self._cloud, "endpoint", None)
        if not endpoint:
            self._cloud_reachable = False
            self._last_cloud_check = now
            return False
        try:
            timeout = aiohttp.ClientTimeout(
                total=self._CLOUD_CHECK_TIMEOUT_TOTAL,
                connect=self._CLOUD_CHECK_TIMEOUT_CONNECT,
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(endpoint, ssl=True) as resp:
                    self._cloud_reachable = True
                    self._last_cloud_check = now
                    pipeline_logger.info("Cloud reachability probe: REACHABLE")
                    return True
        except Exception as exc:
            self._cloud_reachable = False
            self._last_cloud_check = now
            pipeline_logger.info("Cloud reachability probe: UNREACHABLE (%s)", exc)
            return False

    async def probe_cloud_at_startup(self) -> None:
        """Run one cloud reachability check so the first WS connection is instant."""
        if self._cloud is not None:
            reachable = await self._check_cloud_reachable()
            pipeline_logger.info(
                "Startup cloud probe complete — reachable=%s", reachable
            )
        else:
            self._cloud_reachable = False
            self._last_cloud_check = time.time()
            pipeline_logger.info("Startup cloud probe skipped — no cloud processor")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Main WebSocket entry point — delegates to the active processor.

        Routing priority:
        1. **Explicit local** — ``?mode=local`` or runtime toggle to local:
           accept WebSocket immediately, zero cloud dependency.
        2. **Cloud with auto-fallback** — default when ``local_mode.enabled``
           is false: uses a *cached* reachability probe (≤1s on first hit,
           instant thereafter) and falls back to local if offline.
        3. **Explicit cloud** — ``?mode=cloud`` with no local available:
           delegates to RTMiddleTier directly.

        When mode is ``cloud``, the request is forwarded **directly** to
        ``RTMiddleTier._websocket_handler`` so all security checks,
        session management, and message forwarding remain unchanged.

        When mode is ``local``, the router performs minimal WebSocket
        setup and hands off to ``LocalPhi4Processor.handle_websocket()``.
        """
        self._ws_connection_count += 1
        self._active_connections += 1
        conn_id = self._ws_connection_count

        mode, explicit = self._resolve_mode(request)
        pipeline_logger.info(
            "[conn-%d] WebSocket connection — resolved mode=%s explicit=%s (default=%s, runtime=%s, query=%s)",
            conn_id,
            mode,
            explicit,
            self._default_mode,
            self._runtime_mode,
            request.query.get("mode", "<none>"),
        )

        try:
            # ── FAST PATH: explicit local mode ─────────────────────────
            # When mode is "local" and a local processor is available,
            # accept the WebSocket immediately with ZERO cloud dependency.
            if mode == "local" and self._local is not None:
                pipeline_logger.info(
                    "[VOICE/ROUTING] [conn-%d] FAST PATH — LocalPhi4Processor handling connection (no cloud check, no auto-fallback)", conn_id
                )
                ws = web.WebSocketResponse(heartbeat=15.0, autoping=True, autoclose=True)
                await ws.prepare(request)
                try:
                    await self._local.handle_websocket(ws, request)
                except Exception as exc:
                    err_msg = f"Local processor error: {exc}"
                    pipeline_logger.error("[conn-%d] %s", conn_id, err_msg, exc_info=True)
                    self._last_error = err_msg
                    self._last_error_time = time.time()
                    if not ws.closed:
                        try:
                            await ws.send_json({
                                "type": "error",
                                "error": {"message": f"Local processing failed: {exc}"},
                            })
                        except Exception:
                            pass
                return ws

            # ── Auto-fallback: cloud → local when offline ───────────────
            # Only auto-fallback when mode was NOT explicitly requested by the
            # user (e.g. via ?mode=cloud).  When the user deliberately chose
            # cloud mode, respect that and let RTMiddleTier handle errors.
            if mode == "cloud" and self._local is not None and not explicit:
                if self._cloud is None:
                    pipeline_logger.warning(
                        "[VOICE/ROUTING] [conn-%d] Cloud processor not configured — auto-fallback to LocalPhi4Processor",
                        conn_id,
                    )
                    mode = "local"
                else:
                    cloud_ok = await self._check_cloud_reachable()
                    if not cloud_ok:
                        pipeline_logger.warning(
                            "[VOICE/ROUTING] [conn-%d] Cloud endpoint unreachable (cached probe) — auto-fallback to LocalPhi4Processor",
                            conn_id,
                        )
                        mode = "local"
                    else:
                        pipeline_logger.info(
                            "[VOICE/ROUTING] [conn-%d] Cloud endpoint reachable — RTMiddleTier handling connection (no auto-fallback)",
                            conn_id,
                        )

            # ── Cloud mode — full pass-through to RTMiddleTier ──────────
            if mode == "cloud" or self._local is None:
                if self._cloud is None:
                    err_msg = "Neither cloud nor local processor available"
                    pipeline_logger.error("[conn-%d] %s", conn_id, err_msg)
                    self._last_error = err_msg
                    self._last_error_time = time.time()
                    ws = web.WebSocketResponse()
                    await ws.prepare(request)
                    await ws.send_json({"type": "error", "error": {"message": err_msg}})
                    await ws.close()
                    return ws
                if mode == "local" and self._local is None:
                    err_msg = "Local mode requested but unavailable — falling back to cloud"
                    logger.warning(err_msg)
                    pipeline_logger.warning("[conn-%d] %s", conn_id, err_msg)
                    self._last_error = err_msg
                    self._last_error_time = time.time()
                pipeline_logger.info("[conn-%d] Routing to cloud processor (RTMiddleTier)", conn_id)
                return await self._cloud._websocket_handler(request)

            # ── Local mode (after auto-fallback or explicit) ────────────
            pipeline_logger.info(
                "[conn-%d] Routing to local processor (Phi-4 ONNX)", conn_id
            )

            ws = web.WebSocketResponse(heartbeat=15.0, autoping=True, autoclose=True)
            await ws.prepare(request)

            try:
                await self._local.handle_websocket(ws, request)
            except Exception as exc:
                err_msg = f"Local processor error: {exc}"
                pipeline_logger.error("[conn-%d] %s", conn_id, err_msg, exc_info=True)
                self._last_error = err_msg
                self._last_error_time = time.time()
                if not ws.closed:
                    try:
                        await ws.send_json({
                            "type": "error",
                            "error": {"message": f"Local processing failed: {exc}"},
                        })
                    except Exception:
                        pass
            return ws

        except Exception as exc:
            err_msg = f"WebSocket handler error: {exc}"
            pipeline_logger.error("[conn-%d] %s", conn_id, err_msg, exc_info=True)
            self._last_error = err_msg
            self._last_error_time = time.time()
            raise
        finally:
            self._active_connections -= 1
            pipeline_logger.info("[conn-%d] WebSocket connection closed", conn_id)
