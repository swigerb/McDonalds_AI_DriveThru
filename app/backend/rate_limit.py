"""Rate-limit recovery for the cloud realtime relay.

The three demos share one Azure OpenAI quota. When a model response is
rate-limited the service ends it with `response.done` status=failed (or an
`error` event) and produces no output, so without this the guest hears
silence. Per failed response, never per session:

1. retry 1: silent `response.create` after the service's hint (clamped) or
   `retry_delay_seconds`;
2. retry 2: tell the browser (`extension.rate_limited`, attempt 1 -> it plays a
   pre-recorded apology clip), then `response.create` after the hint (clamped)
   or `second_retry_delay_seconds`;
3. give up: `extension.rate_limited` attempt 2 + final -> "please say that
   again". No more retries; the next guest turn proceeds normally.

A pending retry is cancelled when the guest starts speaking or another
response starts (server VAD creates a fresh one; don't stack a stale one).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("mcdonalds-drive-thru")

RATE_LIMITED_EVENT = "extension.rate_limited"
ENV_ENABLED = "RATE_LIMIT_RECOVERY_ENABLED"

FIRST_RETRY_BOUNDS = (0.5, 5.0)
SECOND_RETRY_BOUNDS = (2.0, 8.0)

_HINT_RE = re.compile(
    r"(?:try\s+again|retry)\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?)\b",
    re.IGNORECASE)
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool = True
    retry_delay_seconds: float = 1.5
    second_retry_delay_seconds: float = 4.0
    max_retries: int = 2

    @classmethod
    def from_config(cls, section: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> RateLimitConfig:
        section = section or {}
        env = os.environ if environ is None else environ
        enabled = bool(section.get("enabled", True))
        override = (env.get(ENV_ENABLED) or "").strip().lower()
        if override in _TRUE:
            enabled = True
        elif override in _FALSE:
            enabled = False
        return cls(enabled=enabled,
                   retry_delay_seconds=float(section.get("retry_delay_seconds", cls.retry_delay_seconds)),
                   second_retry_delay_seconds=float(section.get("second_retry_delay_seconds",
                                                                cls.second_retry_delay_seconds)),
                   max_retries=max(0, int(section.get("max_retries", cls.max_retries))))


def rate_limit_error(err: Any) -> dict | None:
    """`err` if it is a rate-limit error object (code or type contains rate_limit), else None."""
    if not isinstance(err, dict):
        return None
    for key in ("code", "type"):
        if "rate_limit" in str(err.get(key) or "").lower():
            return err
    return None


def failed_response_rate_limit(message: dict) -> dict | None:
    """The rate-limit error of a `response.done` with status failed, else None."""
    response = message.get("response") or {}
    if response.get("status") != "failed":
        return None
    return rate_limit_error((response.get("status_details") or {}).get("error"))


def retry_hint_seconds(text: Any) -> float | None:
    """Seconds from a 'try again in X s' / 'X ms' / 'retry after X seconds' hint, else None."""
    match = _HINT_RE.search(str(text or ""))
    if match is None:
        return None
    value = float(match.group(1))
    return value / 1000.0 if match.group(2).lower().startswith("m") else value


def retry_delay(hint: float | None, default: float, bounds: tuple[float, float]) -> float:
    """The hint clamped to `bounds`, or `default` when the service gave none."""
    if hint is None:
        return default
    low, high = bounds
    return min(max(hint, low), high)


class RateLimitRecovery:
    """Recovery ladder for ONE upstream socket."""

    def __init__(self, config: RateLimitConfig,
                 send_upstream: Callable[[], Awaitable[None]],
                 send_client: Callable[[dict], Awaitable[None]],
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 session_id: str | None = None) -> None:
        self.config = config
        self._send_upstream = send_upstream
        self._send_client = send_client
        self._sleep = sleep
        self._session_id = session_id
        self.retries_sent = 0          # retries sent for the current failed response chain
        self._retry_in_flight = False  # our response.create was sent; its response.created not yet seen
        self._task: asyncio.Task | None = None

    @property
    def retry_pending(self) -> bool:
        return self._task is not None and not self._task.done()

    async def on_rate_limited(self, err: dict, source: str) -> None:
        """A response (or response.create) was rate-limited."""
        code = err.get("code") or err.get("type")
        hint = retry_hint_seconds(err.get("message"))
        if not self.config.enabled:
            logger.warning("Realtime response rate-limited (%s, code=%s, retry hint=%s); recovery disabled "
                           "(session=%s)", source, code, hint, self._session_id)
            return
        if self.retry_pending:
            # e.g. an `error` and the failed `response.done` for the same response.
            logger.info("Rate limit (%s) while a retry is already pending; not stacking another (session=%s)",
                        source, self._session_id)
            return
        self._retry_in_flight = False
        attempt = self.retries_sent
        if attempt >= self.config.max_retries:
            logger.warning("Realtime response rate-limited again (%s, code=%s, retry hint=%s) after %d retries; "
                           "giving up on this response -- asking the guest to repeat (session=%s)",
                           source, code, hint, attempt, self._session_id)
            self.retries_sent = 0
            await self._send_client({"type": RATE_LIMITED_EVENT, "attempt": attempt, "final": True})
            return
        if attempt == 0:
            delay = retry_delay(hint, self.config.retry_delay_seconds, FIRST_RETRY_BOUNDS)
        else:
            delay = retry_delay(hint, self.config.second_retry_delay_seconds, SECOND_RETRY_BOUNDS)
        logger.warning("Realtime response rate-limited (%s, code=%s, retry hint=%s); retry %d of %d in %.2fs%s "
                       "(session=%s)", source, code, hint, attempt + 1, self.config.max_retries, delay,
                       "" if attempt == 0 else " after the apology clip", self._session_id)
        if attempt > 0:
            await self._send_client({"type": RATE_LIMITED_EVENT, "attempt": attempt})
        self.retries_sent = attempt + 1
        self._task = asyncio.ensure_future(self._retry_after(delay))

    async def _retry_after(self, delay: float) -> None:
        await self._sleep(delay)
        self._retry_in_flight = True
        try:
            await self._send_upstream()
        except Exception as exc:  # the socket closed while we waited
            logger.info("Rate-limit retry not sent: %s (session=%s)", exc, self._session_id)

    def cancel(self, reason: str) -> None:
        """The guest spoke / another response started: drop any pending retry and start over."""
        if self.retry_pending:
            self._task.cancel()
            logger.info("Pending rate-limit retry cancelled: %s (session=%s)", reason, self._session_id)
        self._task = None
        self._retry_in_flight = False
        self.retries_sent = 0

    def on_response_created(self) -> None:
        """Our retry's response continues the ladder; any other response starts over."""
        if self._retry_in_flight:
            self._retry_in_flight = False  # our retry's response: the ladder continues
            return
        self.cancel("a new response started")

    def set_session_id(self, session_id: str | None) -> None:
        """Follow a resumed session (logging only)."""
        self._session_id = session_id

    def close(self) -> None:
        if self.retry_pending:
            self._task.cancel()
        self._task = None
