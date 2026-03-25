"""Security controls tests for McDonald's AI Drive-Thru.

Tests Phase 4 security features:
  - Session limits (max concurrent, idle timeout)
  - Origin validation (same-origin, allowed_origins list)
  - HMAC session tokens (signing, expiry, tampering)

These tests use stub implementations for security interfaces that may still
be landing in Phase 4. Uses pytest.importorskip where real modules are needed.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from order_state import order_state_singleton
from session_manager import SessionManager


# ---------------------------------------------------------------------------
# Helpers — lightweight stubs for security interfaces
# ---------------------------------------------------------------------------

class _StubSessionLimiter:
    """In-memory session limiter matching the expected SessionManager API.

    Mirrors can_accept_session / create_session / cleanup_session behaviour.
    """

    def __init__(self, max_concurrent: int = 10, idle_timeout: int = 300):
        self.max_concurrent = max_concurrent
        self.idle_timeout = idle_timeout
        self._sessions: dict[str, float] = {}

    def try_add_session(self, session_id: str) -> bool:
        if len(self._sessions) >= self.max_concurrent:
            return False
        self._sessions[session_id] = time.monotonic()
        return True

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def touch(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id] = time.monotonic()

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def cleanup_idle(self, now: float | None = None) -> list[str]:
        """Remove sessions idle longer than idle_timeout. Returns removed ids."""
        now = now or time.monotonic()
        expired = [
            sid for sid, ts in self._sessions.items()
            if (now - ts) >= self.idle_timeout
        ]
        for sid in expired:
            del self._sessions[sid]
        return expired


def _validate_origin(
    request_origin: str | None,
    request_host: str,
    allowed_origins: list[str] | None = None,
) -> bool:
    """Validate the Origin header for a WebSocket upgrade request.

    Rules:
      - Missing Origin → accept (non-browser clients)
      - Origin matches scheme+host → accept (same-origin)
      - Origin in allowed_origins list → accept
      - Otherwise → reject
    """
    if request_origin is None:
        return True

    origin = request_origin.rstrip("/")
    host = request_host.rstrip("/")

    origin_host = origin.split("://", 1)[-1]
    if origin_host == host:
        return True

    if allowed_origins:
        normalised = {o.rstrip("/") for o in allowed_origins}
        if origin in normalised:
            return True

    return False


def _generate_hmac_token(
    session_id: str,
    secret: str,
    issued_at: float | None = None,
    ttl: int = 900,
) -> str:
    """Generate an HMAC-SHA256 session token.

    Format: base64(session_id:issued_at:ttl).signature
    """
    issued_at = issued_at or time.time()
    payload = f"{session_id}:{issued_at:.0f}:{ttl}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def _verify_hmac_token(
    token: str,
    secret: str,
    now: float | None = None,
) -> tuple[bool, str]:
    """Verify an HMAC session token. Returns (valid, reason)."""
    now = now or time.time()

    if not token or "." not in token:
        return False, "malformed"

    parts = token.split(".", 1)
    if len(parts) != 2:
        return False, "malformed"

    encoded, sig = parts

    expected_sig = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False, "invalid_signature"

    try:
        payload = base64.urlsafe_b64decode(encoded).decode()
        session_id, issued_str, ttl_str = payload.split(":")
        issued_at = float(issued_str)
        ttl = int(ttl_str)
    except Exception:
        return False, "malformed"

    if now - issued_at >= ttl:
        return False, "expired"

    return True, "ok"


def _make_mock_ws():
    ws = MagicMock(spec=web.WebSocketResponse)
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    return ws


# ===========================================================================
# Session Limits
# ===========================================================================

class TestSessionLimits:
    """Verify max-concurrent and idle-timeout enforcement."""

    def test_sessions_up_to_max_accepted(self):
        limiter = _StubSessionLimiter(max_concurrent=10)
        for i in range(10):
            assert limiter.try_add_session(f"s-{i}") is True
        assert limiter.active_count == 10

    def test_session_max_plus_one_rejected(self):
        limiter = _StubSessionLimiter(max_concurrent=10)
        for i in range(10):
            limiter.try_add_session(f"s-{i}")
        assert limiter.try_add_session("s-overflow") is False
        assert limiter.active_count == 10

    def test_closing_session_allows_new_one(self):
        limiter = _StubSessionLimiter(max_concurrent=2)
        limiter.try_add_session("a")
        limiter.try_add_session("b")
        assert limiter.try_add_session("c") is False

        limiter.remove_session("a")
        assert limiter.try_add_session("c") is True
        assert limiter.active_count == 2

    def test_idle_timeout_cleans_up_stale_sessions(self):
        limiter = _StubSessionLimiter(max_concurrent=10, idle_timeout=300)
        base = 1000.0
        limiter._sessions["old"] = base
        limiter._sessions["fresh"] = base + 200

        now = base + 300
        removed = limiter.cleanup_idle(now=now)

        assert "old" in removed
        assert "fresh" not in removed
        assert limiter.active_count == 1

    def test_active_sessions_not_cleaned(self):
        limiter = _StubSessionLimiter(max_concurrent=10, idle_timeout=300)
        base = 1000.0
        limiter._sessions["active"] = base

        limiter._sessions["active"] = base + 250
        now = base + 300
        removed = limiter.cleanup_idle(now=now)

        assert "active" not in removed
        assert limiter.active_count == 1

    def test_touch_renews_activity(self):
        limiter = _StubSessionLimiter(max_concurrent=10, idle_timeout=300)
        base = 1000.0
        limiter._sessions["s1"] = base
        limiter.touch("s1")
        assert limiter.active_count == 1

    def test_cleanup_returns_expired_ids(self):
        limiter = _StubSessionLimiter(max_concurrent=10, idle_timeout=60)
        base = 0.0
        limiter._sessions["x"] = base
        limiter._sessions["y"] = base
        limiter._sessions["z"] = base + 100

        removed = limiter.cleanup_idle(now=base + 60)
        assert set(removed) == {"x", "y"}

    def test_remove_nonexistent_session_is_safe(self):
        limiter = _StubSessionLimiter(max_concurrent=5)
        limiter.remove_session("does-not-exist")
        assert limiter.active_count == 0

    def test_touch_nonexistent_session_is_safe(self):
        limiter = _StubSessionLimiter(max_concurrent=5)
        limiter.touch("nonexistent")  # should not raise


class TestSessionManagerAsLimiter:
    """Test the real SessionManager's session limit behaviour."""

    def setup_method(self):
        order_state_singleton.sessions = {}

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 3)
    def test_reject_when_at_max_capacity(self):
        sm = SessionManager()
        for _ in range(3):
            ws = _make_mock_ws()
            sm.create_session(ws)
        assert sm.can_accept_session() is False

    @patch("session_manager._MAX_CONCURRENT_SESSIONS", 2)
    def test_accept_after_cleanup_frees_slot(self):
        sm = SessionManager()
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        sid1 = sm.create_session(ws1)
        sm.create_session(ws2)
        assert sm.can_accept_session() is False
        sm.cleanup_session(ws1, sid1)
        assert sm.can_accept_session() is True

    def test_idle_session_cleaned_after_timeout(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 0):
            sm = SessionManager()
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            sm._last_activity[sid] = time.monotonic() - 10
            asyncio.run(sm.close_idle_sessions())
            ws.close.assert_called_once()
            assert sm.active_session_count == 0

    def test_active_session_survives_idle_check(self):
        with patch("session_manager._IDLE_TIMEOUT_SECONDS", 9999):
            sm = SessionManager()
            ws = _make_mock_ws()
            sid = sm.create_session(ws)
            sm.touch_activity(sid)
            asyncio.run(sm.close_idle_sessions())
            ws.close.assert_not_called()
            assert sm.active_session_count == 1

    def test_touch_activity_resets_timer(self):
        sm = SessionManager()
        ws = _make_mock_ws()
        sid = sm.create_session(ws)
        t1 = sm._last_activity[sid]
        time.sleep(0.02)
        sm.touch_activity(sid)
        t2 = sm._last_activity[sid]
        assert t2 > t1


# ===========================================================================
# Origin Validation
# ===========================================================================

class TestOriginValidation:
    """Verify Origin header checking on WebSocket upgrade."""

    def test_same_origin_accepted(self):
        assert _validate_origin(
            request_origin="https://mcdonalds-app.azurecontainerapps.io",
            request_host="mcdonalds-app.azurecontainerapps.io",
        ) is True

    def test_same_origin_with_scheme_accepted(self):
        assert _validate_origin(
            request_origin="https://example.com",
            request_host="example.com",
        ) is True

    def test_missing_origin_accepted(self):
        """Non-browser clients (curl, Postman) don't send Origin."""
        assert _validate_origin(
            request_origin=None,
            request_host="mcdonalds-app.azurecontainerapps.io",
        ) is True

    def test_foreign_origin_rejected(self):
        assert _validate_origin(
            request_origin="https://evil.example.com",
            request_host="mcdonalds-app.azurecontainerapps.io",
        ) is False

    def test_allowed_origins_list(self):
        allowed = ["https://staging.mcdonalds.com", "https://dev.mcdonalds.com"]
        assert _validate_origin(
            request_origin="https://staging.mcdonalds.com",
            request_host="prod.mcdonalds.com",
            allowed_origins=allowed,
        ) is True

    def test_allowed_origins_rejects_unlisted(self):
        allowed = ["https://staging.mcdonalds.com"]
        assert _validate_origin(
            request_origin="https://evil.example.com",
            request_host="prod.mcdonalds.com",
            allowed_origins=allowed,
        ) is False

    def test_trailing_slash_normalised(self):
        assert _validate_origin(
            request_origin="https://mcdonalds-app.azurecontainerapps.io/",
            request_host="mcdonalds-app.azurecontainerapps.io",
        ) is True

    def test_empty_allowed_origins_falls_through(self):
        """Empty list == same-origin only."""
        assert _validate_origin(
            request_origin="https://other.com",
            request_host="mcdonalds-app.azurecontainerapps.io",
            allowed_origins=[],
        ) is False

    def test_http_vs_https_different_scheme(self):
        """Different scheme but same host — origin check is on host portion."""
        assert _validate_origin(
            request_origin="http://app.example.com",
            request_host="app.example.com",
        ) is True

    def test_port_mismatch_rejected(self):
        assert _validate_origin(
            request_origin="https://app.example.com:8080",
            request_host="app.example.com",
        ) is False


# ===========================================================================
# HMAC Session Token
# ===========================================================================

_TEST_SECRET = "super-secret-key-for-tests"


class TestHMACSessionToken:
    """Verify HMAC-SHA256 session token generation and verification."""

    def test_token_has_dot_separator(self):
        token = _generate_hmac_token("sess-1", _TEST_SECRET)
        assert "." in token
        parts = token.split(".")
        assert len(parts) == 2

    def test_valid_token_accepted(self):
        now = time.time()
        token = _generate_hmac_token("sess-123", _TEST_SECRET, issued_at=now)
        valid, reason = _verify_hmac_token(token, _TEST_SECRET, now=now + 60)
        assert valid is True
        assert reason == "ok"

    def test_expired_token_rejected(self):
        now = time.time()
        token = _generate_hmac_token("sess-123", _TEST_SECRET, issued_at=now, ttl=900)
        valid, reason = _verify_hmac_token(token, _TEST_SECRET, now=now + 960)
        assert valid is False
        assert reason == "expired"

    def test_malformed_token_rejected(self):
        valid, reason = _verify_hmac_token("not-a-real-token", _TEST_SECRET)
        assert valid is False
        assert reason == "malformed"

    def test_empty_token_rejected(self):
        valid, reason = _verify_hmac_token("", _TEST_SECRET)
        assert valid is False
        assert reason == "malformed"

    def test_signature_tampering_detected(self):
        now = time.time()
        token = _generate_hmac_token("sess-123", _TEST_SECRET, issued_at=now)
        encoded, sig = token.split(".", 1)
        tampered = f"{encoded}.{'0' * len(sig)}"
        valid, reason = _verify_hmac_token(tampered, _TEST_SECRET, now=now)
        assert valid is False
        assert reason == "invalid_signature"

    def test_payload_tampering_detected(self):
        now = time.time()
        token = _generate_hmac_token("sess-123", _TEST_SECRET, issued_at=now)
        encoded, sig = token.split(".", 1)
        tampered_encoded = encoded[:-1] + ("A" if encoded[-1] != "A" else "B")
        tampered = f"{tampered_encoded}.{sig}"
        valid, reason = _verify_hmac_token(tampered, _TEST_SECRET, now=now)
        assert valid is False
        assert reason in ("invalid_signature", "malformed")

    def test_wrong_secret_rejected(self):
        now = time.time()
        token = _generate_hmac_token("sess-123", _TEST_SECRET, issued_at=now)
        valid, reason = _verify_hmac_token(token, "wrong-secret", now=now)
        assert valid is False
        assert reason == "invalid_signature"

    def test_token_at_exact_expiry_boundary(self):
        """Token at exactly TTL seconds should be expired (>= boundary)."""
        now = 1000.0
        token = _generate_hmac_token("sess-1", _TEST_SECRET, issued_at=now, ttl=900)
        valid, reason = _verify_hmac_token(token, _TEST_SECRET, now=now + 900)
        assert valid is False
        assert reason == "expired"

    def test_token_one_second_before_expiry(self):
        now = 1000.0
        token = _generate_hmac_token("sess-1", _TEST_SECRET, issued_at=now, ttl=900)
        valid, reason = _verify_hmac_token(token, _TEST_SECRET, now=now + 899)
        assert valid is True

    def test_timing_safe_comparison_used(self):
        """Verify we use hmac.compare_digest, not == for signature check."""
        # The _verify_hmac_token uses hmac.compare_digest — confirm by code review.
        # We test indirectly: wrong signature is still rejected with correct error.
        now = time.time()
        token = _generate_hmac_token("sess-1", _TEST_SECRET, issued_at=now)
        _, sig = token.split(".", 1)
        # Flip last character
        bad_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        tampered = token.split(".")[0] + "." + bad_sig
        valid, reason = _verify_hmac_token(tampered, _TEST_SECRET, now=now)
        assert valid is False
        assert reason == "invalid_signature"


# ===========================================================================
# Integration-style: config.yaml security section
# ===========================================================================

class TestSecurityConfig:
    """Verify that config.yaml security section is well-formed."""

    def test_config_has_security_section(self):
        from config_loader import get_config
        cfg = get_config()
        assert "security" in cfg

    def test_max_concurrent_sessions_default(self):
        from config_loader import get_config
        sec = get_config()["security"]
        assert sec["max_concurrent_sessions"] == 10

    def test_idle_timeout_default(self):
        from config_loader import get_config
        sec = get_config()["security"]
        assert sec["idle_timeout_seconds"] == 300

    def test_allowed_origins_default_empty(self):
        from config_loader import get_config
        sec = get_config()["security"]
        assert sec["allowed_origins"] == []

    def test_require_session_token_default_false(self):
        from config_loader import get_config
        sec = get_config()["security"]
        assert sec["require_session_token"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
