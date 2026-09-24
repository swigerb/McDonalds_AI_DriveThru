import asyncio
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from audio_pipeline import (
    _GA_TO_LEGACY_EVENTS,
    _PASSTHROUGH_CLIENT_TYPES,
    _PASSTHROUGH_SERVER_TYPES,
    _VERBOSE_GLOBAL,
    _VERBOSE_LOG_FILE_GLOBAL,
    _VERBOSE_RESULT_TRUNCATE,
    INPUT_AUDIO_CLEAR_MSG as _INPUT_AUDIO_CLEAR_MSG,
    MARKER_AUDIO_APPEND as _MARKER_AUDIO_APPEND,
    MARKER_AUDIO_DELTA as _MARKER_AUDIO_DELTA,
    MARKER_AUDIO_DELTA_LEGACY as _MARKER_AUDIO_DELTA_LEGACY,
    MARKER_AUDIO_DONE as _MARKER_AUDIO_DONE,
    MARKER_AUDIO_DONE_LEGACY as _MARKER_AUDIO_DONE_LEGACY,
    MARKER_END_SESSION as _MARKER_END_SESSION,
    MARKER_LOG_TO_FILE as _MARKER_LOG_TO_FILE,
    MARKER_RESPONSE_CANCEL as _MARKER_RESPONSE_CANCEL,
    MARKER_RESPONSE_CREATE as _MARKER_RESPONSE_CREATE,
    MARKER_RESUME as _MARKER_RESUME,
    MARKER_SESSION_UPDATE as _MARKER_SESSION_UPDATE,
    MARKER_SESSION_UPDATED as _MARKER_SESSION_UPDATED,
    MARKER_SET_VOICE as _MARKER_SET_VOICE,
    MARKER_SPEECH_STARTED as _MARKER_SPEECH_STARTED,
    MARKER_TRANSCRIPTION_COMPLETED as _MARKER_TRANSCRIPTION_COMPLETED,
    MARKER_VERBOSE_LOGGING as _MARKER_VERBOSE_LOGGING,
    RESPONSE_CREATE_MSG as _RESPONSE_CREATE_MSG,
    TYPE_RE as _TYPE_RE,
    EchoSuppressor,
    create_verbose_file_handler as _create_verbose_file_handler,
    remove_verbose_file_handler as _remove_verbose_file_handler,
    vlog as _vlog,
    vlogger,
)
from config_loader import get_config
from order_state import order_state_singleton
from rate_limit import (
    RateLimitConfig,
    RateLimitRecovery,
    failed_response_rate_limit,
    rate_limit_error,
)
from session_manager import (
    SESSION_ENDED_CLOSE_CODE,
    SESSION_ENDED_CLOSE_REASON,
    SUPERSEDED_CLOSE_CODE,
    SUPERSEDED_CLOSE_REASON,
    SessionManager,
    resume_id_fingerprint,
)

logger = logging.getLogger("mcdonalds-drive-thru")

# Load centralized config
_cfg = get_config()
_conn_cfg = _cfg.get("connection", {})
_security_cfg = _cfg.get("security", {})
_rate_limit_cfg = (_cfg.get("resilience") or {}).get("rate_limit") or {}

__all__ = ["RTMiddleTier", "RTToolCall", "Tool", "ToolResult", "ToolResultDirection",
           "configure_realtime_model", "create_hmac_token", "deployment_supports_reasoning",
           "normalize_reasoning_effort", "parse_reasoning_model", "validate_hmac_token"]

# Connection tuning constants
_WS_HEARTBEAT_SEC = _conn_cfg.get("ws_heartbeat_seconds", 15.0)
# permessage-deflate on the browser socket. Off by default: aiohttp 3.14.2/3.14.3
# reject the first compressed frame after an initial PONG (aio-libs/aiohttp#13274),
# which is exactly what a browser sends on a socket idle past one heartbeat.
_WS_COMPRESS = bool(_conn_cfg.get("ws_compression", False))
_WS_CONNECT_TIMEOUT = aiohttp.ClientTimeout(
    total=_conn_cfg.get("ws_connect_timeout_total", 30),
    connect=_conn_cfg.get("ws_connect_timeout_connect", 10),
)
_MARKER_RESPONSE_CREATED = '"response.created"'


# ── HMAC Session Token Utilities ──

def create_hmac_token(secret: bytes, expiry_seconds: int = 900) -> str:
    """Create an HMAC-signed session token with expiry."""
    payload = {"exp": int(time.time()) + expiry_seconds}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def validate_hmac_token(token: str, secret: bytes) -> bool:
    """Validate an HMAC session token (signature + expiry)."""
    if not token or "." not in token:
        return False
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("exp", 0) > time.time()
    except Exception:
        return False


class ToolResultDirection(Enum):
    TO_SERVER = 1
    TO_CLIENT = 2
    TO_BOTH = 3

class ToolResult:
    __slots__ = ("text", "destination", "_client_text")

    def __init__(self, text: str, destination: ToolResultDirection, client_text: str | None = None):
        self.text = text
        self.destination = destination
        self._client_text = client_text

    def to_text(self) -> str:
        if self.text is None:
            return ""
        return self.text if isinstance(self.text, str) else json.dumps(self.text)

    def to_client_text(self) -> str:
        """Text for client display. Falls back to to_text() if no separate client payload."""
        if self._client_text is not None:
            return self._client_text
        return self.to_text()

class Tool:
    __slots__ = ("target", "schema")

    def __init__(self, target: Any, schema: Any):
        self.target = target
        self.schema = schema

class RTToolCall:
    __slots__ = ("tool_call_id", "previous_id")

    def __init__(self, tool_call_id: str, previous_id: str):
        self.tool_call_id = tool_call_id
        self.previous_id = previous_id


# Session keys the GA realtime API accepts at the top level. Anything else that
# the legacy (2024-10-01-preview) clients send is dropped, because GA rejects
# unknown parameters outright instead of ignoring them.
# `reasoning` ({effort}) and `parallel_tool_calls` exist only for reasoning
# realtime models (gpt-realtime-2 / 2.1). gpt-realtime-1.5 rejects the whole
# session.update if they are present, so RTMiddleTier only sets them when the
# deployment is a reasoning model (see `RTMiddleTier._reasoning_model`).
_GA_SESSION_TOP_LEVEL = frozenset({
    "type", "model", "instructions", "tools", "tool_choice",
    "max_output_tokens", "output_modalities", "audio", "tracing",
    "include", "prompt", "truncation",
    "reasoning", "parallel_tool_calls",
})

# Legacy audio formats were bare strings ("pcm16"); GA expects an object.
_GA_AUDIO_FORMATS = {
    "pcm16": {"type": "audio/pcm", "rate": 24000},
    "g711_ulaw": {"type": "audio/pcmu"},
    "g711_alaw": {"type": "audio/pcma"},
}


def _ga_audio_format(value: Any) -> Any:
    if isinstance(value, str):
        return _GA_AUDIO_FORMATS.get(value, {"type": "audio/pcm", "rate": 24000})
    return value


def _to_ga_session(session: dict) -> dict:
    """Translate a legacy realtime `session` object into the GA shape.

    The browser client speaks the 2024-10-01-preview dialect. The GA endpoint
    moved most audio settings under `audio.input` / `audio.output`, renamed a
    couple of fields, requires a `type` discriminator, and errors on unknown
    parameters rather than ignoring them. Doing the translation here keeps the
    client contract stable and keeps the failure modes in one place.
    """
    ga: dict = dict(session)
    audio: dict = dict(ga.get("audio") or {})
    audio_in: dict = dict(audio.get("input") or {})
    audio_out: dict = dict(audio.get("output") or {})

    # input side
    if (turn_detection := ga.pop("turn_detection", None)) is not None:
        audio_in["turn_detection"] = turn_detection
    if (transcription := ga.pop("input_audio_transcription", None)) is not None:
        audio_in["transcription"] = transcription
    if (in_fmt := ga.pop("input_audio_format", None)) is not None:
        audio_in["format"] = _ga_audio_format(in_fmt)
    if (noise := ga.pop("input_audio_noise_reduction", None)) is not None:
        audio_in["noise_reduction"] = noise

    # output side
    if (voice := ga.pop("voice", None)) is not None:
        audio_out["voice"] = voice
    if (out_fmt := ga.pop("output_audio_format", None)) is not None:
        audio_out["format"] = _ga_audio_format(out_fmt)
    if (speed := ga.pop("speed", None)) is not None:
        audio_out["speed"] = speed

    # renamed top-level fields
    if (max_tokens := ga.pop("max_response_output_tokens", None)) is not None:
        ga["max_output_tokens"] = max_tokens
    if (modalities := ga.pop("modalities", None)) is not None:
        ga["output_modalities"] = modalities

    if audio_in:
        audio["input"] = audio_in
    if audio_out:
        audio["output"] = audio_out
    if audio:
        ga["audio"] = audio

    ga["type"] = "realtime"

    # `temperature` and `disable_audio` are not part of the GA session object.
    dropped = [k for k in ga if k not in _GA_SESSION_TOP_LEVEL]
    for key in dropped:
        ga.pop(key)
    if dropped:
        logger.debug("session.update: dropped non-GA keys %s", dropped)

    return ga


# What the browser's useRealtime.startSession() sends (App.tsx enables input
# transcription). The middle tier applies the same values itself the moment the
# upstream socket opens, so a socket the browser never configures (e.g.
# react-use-websocket auto-reconnected while the mic was live) behaves exactly
# like one it did.
_BOOTSTRAP_CLIENT_SESSION: dict = {
    "turn_detection": {
        "type": "server_vad",
        "threshold": 0.7,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 500,
    },
    "input_audio_transcription": {"model": "whisper-1"},
}

# How long the greeting waits for the server to confirm the session config.
_SESSION_CONFIGURED_TIMEOUT_SEC = 5.0

# Fire-and-forget tasks (e.g. closing a superseded socket) kept alive until done.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def _close_superseded(stale_ws: web.WebSocketResponse) -> None:
    try:
        await stale_ws.close(code=SUPERSEDED_CLOSE_CODE, message=SUPERSEDED_CLOSE_REASON.encode())
    except Exception:
        pass


def _extension_type(data: str, marker: str) -> str | None:
    """The `type` of a client frame that contains `marker`, else None (cheap for audio frames)."""
    if marker not in data:
        return None
    try:
        message = json.loads(data)
    except ValueError:
        return None
    return message.get("type") if isinstance(message, dict) else None


def _strip_output_voice(ga_session: dict) -> bool:
    """Remove `audio.output.voice` from a GA session in place. Returns True if removed."""
    audio = ga_session.get("audio")
    if not isinstance(audio, dict):
        return False
    output = audio.get("output")
    if not isinstance(output, dict) or "voice" not in output:
        return False
    output.pop("voice")
    if not output:
        audio.pop("output")
    if not audio:
        ga_session.pop("audio")
    return True


# Every built-in voice gpt-realtime-2.1 (and 1.5) accepts for audio.output.voice;
# the service lists exactly these ten when it rejects anything else (fable, onyx,
# nova...). Keep in sync with app/frontend/src/lib/voices.ts.
GA_REALTIME_VOICES = ("marin", "cedar", "shimmer", "ash", "ballad", "coral", "sage", "verse", "alloy", "echo")
# OpenAI's recommended voice; keep in sync with config.yaml model.default_voice.
DEFAULT_VOICE = "marin"

# Values accepted by gpt-realtime-2.1 for `reasoning.effort` (probed live 2026-09-22).
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
# Config values that mean "do not send `reasoning` at all".
_REASONING_DISABLED_VALUES = frozenset({"", "off", "disabled", "false", "null"})

# Realtime model families that are NOT reasoning models. gpt-realtime-1.5 answers
# `reasoning` (any effort, even "none") and `parallel_tool_calls: true` with
# `invalid_value` "Unsupported option for this model" -- and drops the whole
# session.update, tools included. The dated `gpt-realtime-2025-08-28` snapshot is
# the original non-reasoning gpt-realtime, not gpt-realtime-2.
_NON_REASONING_DEPLOYMENT_RE = re.compile(
    r"^(gpt-4o.*|gpt-realtime(-mini.*|-1(\.\d+)?(-.*)?|-\d{4}-\d{2}-\d{2})?)$",
    re.IGNORECASE,
)


def deployment_supports_reasoning(deployment: str | None) -> bool:
    """Best-effort check from the deployment name, used only when
    `model.reasoning_model` is "auto". azd names deployments after the model, so
    a rollback to `gpt-realtime-1.5` is recognised. Unrecognised custom names
    are assumed to support reasoning; if they don't, the rejected session.update
    is caught by the fallback in RTMiddleTier and reasoning is switched off for
    the rest of the process."""
    if not isinstance(deployment, str) or not deployment.strip():
        return True
    return _NON_REASONING_DEPLOYMENT_RE.match(deployment.strip()) is None


def parse_reasoning_model(value: Any) -> bool | None:
    """`model.reasoning_model` / AZURE_OPENAI_REALTIME_REASONING_MODEL:
    True / False force it; None ("auto", empty, unknown) infers it from the
    deployment name."""
    if isinstance(value, bool):
        return value
    text = "" if value is None else str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    if text not in ("", "auto", "null", "none"):
        logger.warning("Ignoring unknown reasoning_model %r (expected auto|true|false)", value)
    return None


def normalize_reasoning_effort(value: Any) -> str | None:
    """Map a configured effort to the wire value, or None to omit `reasoning`.

    Empty / "off" / "disabled" omit the field. "none" is a real effort level on
    gpt-realtime-2.1 (no reasoning tokens) and is sent as-is.
    """
    if value is None:
        return None
    effort = str(value).strip().lower()
    if effort in _REASONING_DISABLED_VALUES:
        return None
    if effort not in REASONING_EFFORTS:
        logger.warning("Ignoring unknown reasoning effort %r (expected one of %s)", value, sorted(REASONING_EFFORTS))
        return None
    return effort


def _tool_error_result(tool_name: str) -> ToolResult:
    """What the model gets back when a tool call cannot be executed."""
    return ToolResult(
        f"The {tool_name} tool failed and did not run. Apologise briefly, ask the guest to repeat "
        "that part of the order, and do not claim it was done.",
        ToolResultDirection.TO_SERVER,
    )


def _new_event_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


# The fallback carries only what the conversation cannot work without. No voice
# (cannot_update_voice), no audio config, no reasoning -- the usual suspects when
# GA rejects an update.
_FALLBACK_SESSION_KEYS = ("type", "instructions", "tools", "tool_choice")


class _SessionUpdateGuard:
    """Tracks the session.updates sent on ONE upstream socket so a rejection can
    be correlated back to them.

    GA rejects an invalid session.update wholesale and reports it only as an
    `error` event. Most rejections echo our `event_id` in `error.event_id`, but
    some do not (gpt-realtime-1.5 rejecting `reasoning` returns no event_id and
    no param), so an uncorrelated invalid_request_error that arrives while one
    of our updates is still unacknowledged is attributed to the oldest one --
    the service processes client events in order.
    """

    _MAX_TRACKED = 64

    def __init__(self) -> None:
        # event_id -> event_id of the original if this is a fallback, else None
        self._sent: OrderedDict[str, str | None] = OrderedDict()
        self._payloads: dict[str, dict] = {}
        self._in_flight: deque[str] = deque()
        self._fallback_sent_for: set[str] = set()

    def stamp(self, message: dict, fallback_of: str | None = None) -> dict:
        """Ensure `message` carries an event_id and start tracking it."""
        event_id = message.get("event_id") or _new_event_id("mcd_fallback" if fallback_of else "mcd_su")
        message["event_id"] = event_id
        self._sent[event_id] = fallback_of
        self._payloads[event_id] = message.get("session") or {}
        self._in_flight.append(event_id)
        while len(self._sent) > self._MAX_TRACKED:
            old, _ = self._sent.popitem(last=False)
            self._payloads.pop(old, None)
        return message

    def track(self, payload: str, fallback_of: str | None = None) -> str:
        """`stamp` for an already-serialised session.update."""
        message = json.loads(payload)
        had_id = bool(message.get("event_id"))
        self.stamp(message, fallback_of)
        return payload if had_id else json.dumps(message)

    def on_session_updated(self) -> None:
        if self._in_flight:
            self._in_flight.popleft()

    def correlate(self, error_event: dict) -> str | None:
        """Return the event_id of our session.update this error rejects, or None."""
        err = error_event.get("error") or {}
        event_id = err.get("event_id")
        if event_id:
            if event_id not in self._sent:
                return None
            try:
                self._in_flight.remove(event_id)
            except ValueError:
                pass
            return event_id
        param = err.get("param") or ""
        # A rate limit is not a verdict on our payload: only an explicit event_id
        # ties one to a session.update (see rate_limit.py for the rest).
        if rate_limit_error(err) is not None:
            return None
        if (self._in_flight and err.get("type") == "invalid_request_error"
                and (not param or param.startswith("session"))):
            return self._in_flight.popleft()
        return None

    def original_of(self, event_id: str) -> str | None:
        return self._sent.get(event_id)

    def payload_of(self, event_id: str) -> dict:
        return self._payloads.get(event_id, {})

    def claim_fallback(self, event_id: str) -> bool:
        """True exactly once per original session.update."""
        if event_id in self._fallback_sent_for:
            return False
        self._fallback_sent_for.add(event_id)
        return True


class RTMiddleTier:
    endpoint: str
    deployment: str
    key: str | None = None
    
    # Tools are server-side only for now, though the case could be made for client-side tools
    # in addition to server-side tools that are invisible to the client
    tools: dict[str, Tool]

    # Server-enforced configuration, if set, these will override the client's configuration
    # Typically at least the model name and system message will be set by the server
    model: str | None = None
    system_message: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    disable_audio: bool | None = None
    voice_choice: str | None = None
    # audio.input.transcription.model. whisper-1 works on Azure without its own
    # deployment; gpt-4o(-mini)-transcribe are ACCEPTED by session.update but
    # then fail every turn with DeploymentNotFound unless deployed separately.
    transcription_model: str | None = None
    # reasoning.effort for reasoning realtime models; None omits the field.
    reasoning_effort: str | None = None
    parallel_tool_calls: bool | None = None
    # Whether the deployment is a reasoning model (accepts `reasoning` and
    # `parallel_tool_calls`). None = infer from the deployment name.
    reasoning_model: bool | None = None
    _reasoning_rejected: bool = False

    def __init__(self, endpoint: str, deployment: str, credentials: AzureKeyCredential | DefaultAzureCredential, voice_choice: str | None = None, prompt_loader=None):
        self.endpoint = endpoint
        self.deployment = deployment
        self.voice_choice = voice_choice
        self.tools = {}
        self._token_provider = None
        self._cached_token: str | None = None
        self._token_refresh_task: asyncio.Task | None = None
        self.app_secret: bytes = b""
        self._prompt_loader = prompt_loader
        self._sessions = SessionManager(prompt_loader=prompt_loader)
        # Flipped if the deployment rejects `reasoning` at runtime despite the
        # name check, so later sessions stop sending it.
        self._reasoning_rejected = False
        # Rate-limit recovery (config.yaml resilience.rate_limit); the sleep is
        # swappable so tests don't wait out real retry delays.
        self.rate_limit_config = RateLimitConfig.from_config(_rate_limit_cfg)
        self._rate_limit_sleep = asyncio.sleep
        if voice_choice is not None:
            logger.info("Realtime voice choice set to %s", voice_choice)
        if isinstance(credentials, AzureKeyCredential):
            self.key = credentials.key
        else:
            self._token_provider = get_bearer_token_provider(credentials, "https://cognitiveservices.azure.com/.default")
            try:
                self._token_provider()  # Warm up — cache a token for the first request
            except Exception as exc:
                logger.warning("Token warmup failed (offline?): %s — will retry on first request", exc)

    def build_voice_update(self, voice: str, event_id: str | None = None) -> str:
        """Serialise a session.update that switches the assistant voice.

        The voice is expressed in the legacy shape and then translated, so it
        lands at `audio.output.voice` where the GA endpoint expects it.
        """
        ga_session = _to_ga_session({"voice": voice})
        return json.dumps({"type": "session.update", "event_id": event_id or _new_event_id("mcd_voice"),
                           "session": ga_session})

    def _reasoning_model(self) -> bool:
        """Whether reasoning-model-only fields may be sent upstream at all.

        A runtime rejection always wins; then the explicit `reasoning_model`
        switch; the deployment-name check is only the default."""
        if self._reasoning_rejected:
            return False
        if self.reasoning_model is not None:
            return self.reasoning_model
        return deployment_supports_reasoning(getattr(self, "deployment", None))

    def reasoning_enabled(self) -> bool:
        """Whether `reasoning` will be sent upstream."""
        return normalize_reasoning_effort(self.reasoning_effort) is not None and self._reasoning_model()

    def _build_session(self, session: dict, voice_locked: bool = False) -> dict:
        """Overlay the server-owned configuration onto a legacy-shaped session
        and translate it to the GA shape.

        `voice_locked` must be True once the upstream conversation contains
        assistant audio. From then on GA rejects any session.update whose voice
        differs from the current one with `cannot_update_voice` -- and it
        rejects the WHOLE event, so tools, tool_choice and instructions are
        silently lost along with the voice.
        """
        if self.system_message is not None:
            session["instructions"] = self.system_message
        if self.temperature is not None:
            session["temperature"] = self.temperature
        if self.max_tokens is not None:
            session["max_response_output_tokens"] = self.max_tokens
        if self.disable_audio is not None:
            session["disable_audio"] = self.disable_audio
        if self.voice_choice is not None:
            session["voice"] = self.voice_choice
        session["tool_choice"] = "auto" if len(self.tools) > 0 else "none"
        session["tools"] = [tool.schema for tool in self.tools.values()]
        if self.transcription_model:
            transcription = session.get("input_audio_transcription")
            session["input_audio_transcription"] = {
                **(transcription if isinstance(transcription, dict) else {}),
                "model": self.transcription_model,
            }
        # Server-owned: never trust a client-supplied value for these, since
        # an unsupported one takes the tools down with it.
        session.pop("reasoning", None)
        session.pop("parallel_tool_calls", None)
        if self._reasoning_model():
            if (effort := normalize_reasoning_effort(self.reasoning_effort)) is not None:
                session["reasoning"] = {"effort": effort}
            if self.parallel_tool_calls is not None:
                session["parallel_tool_calls"] = bool(self.parallel_tool_calls)
        # Clients speak the legacy (2024-10-01-preview) session shape.
        # Translate to the GA shape here so the browser contract is
        # unchanged, and so unsupported legacy keys are dropped rather
        # than rejected outright by the server.
        ga_session = _to_ga_session(session)
        if voice_locked and _strip_output_voice(ga_session):
            logger.info("session.update: assistant audio already present — omitting voice so the update is not rejected")
        return ga_session

    def build_bootstrap_session_update(self, event_id: str | None = None) -> str:
        """Serialise the session.update the middle tier sends as the very first
        frame on every upstream socket, before any browser traffic is relayed.

        Without it the upstream session runs on the service defaults (no tools,
        generic instructions, server VAD auto-responding) until the browser's
        own session.update arrives -- and if the model speaks in that window the
        voice locks and every later session.update carrying our voice is
        rejected, so tools are never registered for that conversation.
        """
        session = self._build_session(copy.deepcopy(_BOOTSTRAP_CLIENT_SESSION))
        return json.dumps({"type": "session.update", "event_id": event_id or _new_event_id("mcd_bootstrap"),
                           "session": session})

    def build_fallback_session_update(self, event_id: str | None = None) -> str:
        """Serialise the minimal session.update sent when GA rejects one of ours.

        Only `type`, `instructions`, `tools` and `tool_choice` -- whatever field
        got the original rejected, the crew member keeps its tools and persona.
        """
        full = self._build_session({}, voice_locked=True)
        session = {key: full[key] for key in _FALLBACK_SESSION_KEYS if key in full}
        return json.dumps({"type": "session.update", "event_id": event_id or _new_event_id("mcd_fallback"),
                           "session": session})

    async def _recover_rejected_session_update(self, message: dict, server_ws, guard: "_SessionUpdateGuard | None",
                                               session_id: str | None) -> bool:
        """Handle an upstream `error` that rejects one of our session.updates.

        Returns True if the error was consumed (a fallback was sent), False if
        it should reach the browser: unrelated errors, and a rejected fallback.
        """
        if guard is None:
            return False
        event_id = guard.correlate(message)
        if event_id is None:
            return False
        err = message.get("error") or {}
        code, param, text = err.get("code"), err.get("param"), err.get("message")
        original = guard.original_of(event_id)
        if original is not None or not guard.claim_fallback(event_id):
            logger.error(
                "Fallback session.update %s (for %s) was ALSO rejected: code=%s param=%s message=%s -- "
                "tools may NOT be registered for this conversation (session=%s)",
                event_id, original, code, param, text, session_id)
            return False
        logger.error(
            "Upstream REJECTED session.update %s: code=%s param=%s message=%s -- resending a minimal "
            "session.update (instructions + tools only) so the tools survive (session=%s)",
            event_id, code, param, text, session_id)
        rejected = guard.payload_of(event_id)
        if (("reasoning" in rejected or "parallel_tool_calls" in rejected)
                and (not param or param.startswith(("session.reasoning", "session.parallel_tool_calls")))):
            self._reasoning_rejected = True
            logger.error("Deployment %s rejected reasoning-model options; no longer sending `reasoning` / "
                         "`parallel_tool_calls` from this process. Set model.reasoning_effort to \"off\" for this "
                         "deployment.", getattr(self, "deployment", "?"))
        fallback = guard.track(self.build_fallback_session_update(), fallback_of=event_id)
        await server_ws.send_str(fallback)
        return True

    def _get_auth_token(self) -> str:
        """Return the cached token, falling back to a synchronous call if needed."""
        if self._cached_token is not None:
            return self._cached_token
        if self._token_provider is not None:
            return self._token_provider()
        return ""

    async def _refresh_token_loop(self) -> None:
        """Background task: proactively refresh the Azure AD token every 5 minutes."""
        while True:
            try:
                loop = asyncio.get_event_loop()
                token = await loop.run_in_executor(None, self._token_provider)
                self._cached_token = token
                logger.debug("Azure AD token refreshed successfully")
            except Exception as e:
                logger.warning("Token refresh failed: %s", e)
            await asyncio.sleep(300)  # 5 minutes

    def start_background_tasks(self) -> None:
        """Start background tasks (token refresh, idle checker). Called once at app startup."""
        if self._token_provider is not None:
            self._token_refresh_task = asyncio.ensure_future(self._refresh_token_loop())
        self._sessions.start_idle_checker()

    def stop_background_tasks(self) -> None:
        """Cancel background tasks. Called on app shutdown."""
        if self._token_refresh_task and not self._token_refresh_task.done():
            self._token_refresh_task.cancel()
        self._sessions.stop_idle_checker()

    def _scrub_session_for_client(self, session: dict) -> None:
        """Strip the system prompt, tool schemas and max-token cap from a
        `session` object before it is relayed to the browser.

        Every GA server event that echoes the full session object (currently
        `session.created` and `session.updated` -- see the `case` blocks in
        `_process_message_to_client`) must route through this one helper. If
        we ever allow client-side tools, this will need updating.
        """
        session["instructions"] = ""
        session["tools"] = []
        session["voice"] = self.voice_choice
        session["tool_choice"] = "none"
        session["max_response_output_tokens"] = None

    async def _process_message_to_client(self, msg: str, client_ws: web.WebSocketResponse, server_ws: web.WebSocketResponse, tools_pending: dict[str, RTToolCall], verbose: bool = False, guard: "_SessionUpdateGuard | None" = None,
                                         recovery: RateLimitRecovery | None = None,
                                         on_session_created: Callable[[], Awaitable[None]] | None = None) -> str | None:
        data = msg.data

        # FAST PATH: extract type via regex without full JSON parse.
        # Audio deltas are ~95% of server messages — avoid json.loads entirely.
        m = _TYPE_RE.search(data)
        if m is not None and m.group(1) in _PASSTHROUGH_SERVER_TYPES:
            # Translate GA event names to legacy names for client compatibility
            event_type = m.group(1)
            legacy_name = _GA_TO_LEGACY_EVENTS.get(event_type)
            if legacy_name is not None:
                data = data.replace(f'"{event_type}"', f'"{legacy_name}"', 1)
                event_type = legacy_name
            # Verbose: log passthrough types (skip audio delta data to avoid flooding)
            if verbose or _VERBOSE_GLOBAL:
                if event_type == "response.audio.delta":
                    _vlog(verbose, "─── [Server → Client] response.audio.delta (audio data) ───")
                elif event_type == "response.audio_transcript.delta":
                    # Extract transcript snippet from raw JSON
                    td_match = re.search(r'"delta"\s*:\s*"([^"]{0,120})', data)
                    snippet = td_match.group(1) if td_match else ""
                    _vlog(verbose, '─── [AI → Client] response.audio_transcript.delta ───\n"%s"', snippet)
                elif event_type == "response.audio_transcript.done":
                    td_match = re.search(r'"transcript"\s*:\s*"([^"]{0,200})', data)
                    snippet = td_match.group(1) if td_match else ""
                    _vlog(verbose, '─── [AI → Client] response.audio_transcript.done ───\n"%s"', snippet)
                elif event_type == "input_audio_buffer.speech_started":
                    _vlog(verbose, "─── [Server] input_audio_buffer.speech_started ───")
                elif event_type == "input_audio_buffer.speech_stopped":
                    _vlog(verbose, "─── [Server] input_audio_buffer.speech_stopped ───")
                else:
                    _vlog(verbose, "─── [Server → Client] %s ───", event_type)
            return data

        message = json.loads(data)
        msg_type = message.get("type", "")

        updated_message = data
        session_id = self._sessions.get_session_id(client_ws)
        if message is not None:
            _vlog(verbose, "─── [Server → Client] %s ───", msg_type)
            match msg_type:
                case "error":
                    # A rejected session.update of ours is recovered here (minimal
                    # fallback) instead of surfacing as a user-facing failure.
                    if await self._recover_rejected_session_update(message, server_ws, guard, session_id):
                        _vlog(verbose, "  ⚠ session.update rejected — fallback sent: %s", json.dumps(message, default=str)[:500])
                        return None
                    rate_limited = rate_limit_error(message.get("error"))
                    if rate_limited is not None and recovery is not None:
                        await recovery.on_rate_limited(rate_limited, "error event")
                        return updated_message
                    # Surface OpenAI errors (e.g. rejected session.update, malformed tool schemas)
                    # so they don't silently vanish into the client.
                    logger.error("OpenAI Realtime API error: %s", json.dumps(message, default=str)[:1000])
                    _vlog(verbose, "  ⚠ ERROR: %s", json.dumps(message, default=str)[:500])

                case "conversation.item.input_audio_transcription.failed":
                    # e.g. DeploymentNotFound when the configured transcription
                    # model has no Azure deployment: the session.update was
                    # accepted, but no guest speech is ever transcribed.
                    logger.error("Input audio transcription failed (model=%s): %s", self.transcription_model,
                                 json.dumps(message.get("error"), default=str)[:500])

                case "session.created":
                    session = message["session"]
                    _vlog(verbose, "  Session ID: %s", session.get("id", "?"))
                    self._scrub_session_for_client(session)
                    updated_message = json.dumps(message)
                    if on_session_created is not None:
                        # The forwarder announces the session (metadata or resume)
                        # once it knows whether this socket is resuming.
                        await on_session_created()
                    elif session_id is not None:
                        identifiers = order_state_singleton.get_session_identifiers(session_id)
                        await self._sessions.emit_session_identifiers(client_ws, "extension.session_metadata", identifiers)
                        _vlog(verbose, "─── [SESSION TOKEN] ───\n"
                                       "Token: %s\n"
                                       "Round Trip: #%d (token: %s)\n"
                                       "───────────────────────",
                              identifiers.session_token,
                              identifiers.round_trip_index,
                              identifiers.round_trip_token)

                case "session.updated":
                    # Same leak surface as session.created: this event fires on
                    # every accepted session.update (bootstrap, voice change,
                    # browser handshake, rejection fallback...) and echoes the
                    # full session object right back, instructions/tools included.
                    session = message.get("session")
                    if session is not None:
                        self._scrub_session_for_client(session)
                        updated_message = json.dumps(message)

                case "response.output_item.added":
                    if "item" in message and message["item"]["type"] == "function_call":
                        # Fallback registration — ensures tools_pending is populated even
                        # if conversation.item.created fires late or is skipped by newer
                        # API versions.  conversation.item.created overwrites with the
                        # correct previous_item_id when it arrives.
                        item = message["item"]
                        call_id = item.get("call_id")
                        if call_id and call_id not in tools_pending:
                            logger.info("Tool call received: name=%s, call_id=%s", item.get("name"), call_id)
                            tools_pending[call_id] = RTToolCall(call_id, "")
                        _vlog(verbose, "  Tool call registered: %s (call_id=%s)", item.get("name"), item.get("call_id"))
                        updated_message = None

                case "conversation.item.created" | "conversation.item.added":
                    if "item" in message and message["item"]["type"] == "function_call":
                        item = message["item"]
                        # Always overwrite — may upgrade fallback from output_item.added
                        # with the correct previous_item_id
                        tools_pending[item["call_id"]] = RTToolCall(item["call_id"], message.get("previous_item_id", ""))
                        _vlog(verbose, "  Tool pending confirmed: call_id=%s, prev=%s", item["call_id"], message.get("previous_item_id", ""))
                        updated_message = None
                    elif "item" in message and message["item"]["type"] == "function_call_output":
                        updated_message = None
                    elif "item" in message and message["item"].get("role") == "assistant":
                        # Log AI conversation items (non-tool)
                        _vlog(verbose, "  AI conversation item created")

                case "response.function_call_arguments.delta":
                    updated_message = None
                
                case "response.function_call_arguments.done":
                    _vlog(verbose, "  Tool args complete: %s", message.get("arguments", "")[:200])
                    updated_message = None

                case "response.output_item.done":
                    if "item" in message and message["item"]["type"] == "function_call":
                        item = message["item"]
                        tool_call = tools_pending.get(item["call_id"])
                        if tool_call is None:
                            logger.warning("Tool call %s not found in pending tools", item["call_id"])
                            updated_message = None
                        else:
                            tool = self.tools.get(item["name"])
                            if tool is None:
                                logger.error("Unknown tool requested: %s", item["name"])
                            t0 = time.monotonic()
                            args: Any = item.get("arguments")
                            try:
                                if tool is None:
                                    raise LookupError(f"unknown tool {item['name']!r}")
                                args = json.loads(item["arguments"])
                                logger.info("Executing tool '%s' with args %s (session=%s)", item["name"], args, session_id)
                                if item["name"] in ("update_order", "get_order", "reset_order"):
                                    result = await tool.target(args, session_id)
                                else:
                                    result = await tool.target(args)
                            except Exception:
                                # Without a function_call_output the model is left waiting on
                                # its own call, and an exception escaping here would tear down
                                # the whole conversation: answer the call with an error instead.
                                logger.exception("Tool '%s' failed (args=%s, session=%s) — returning an error result "
                                                 "so the conversation continues", item["name"], args, session_id)
                                result = _tool_error_result(item["name"])
                            elapsed_ms = (time.monotonic() - t0) * 1000
                            logger.info("Tool '%s' result direction=%s", item["name"], result.destination)

                            # ── Verbose: full tool call lifecycle ──
                            result_text = result.to_text()[:_VERBOSE_RESULT_TRUNCATE]
                            _vlog(verbose,
                                  "\n═══ [TOOL CALL] %s ═══\n"
                                  "Args: %s\n"
                                  "Result: %s\n"
                                  "Direction: %s\n"
                                  "Time: %.1fms\n"
                                  "═══════════════════════════",
                                  item["name"],
                                  json.dumps(args, indent=2),
                                  result_text,
                                  result.destination.name,
                                  elapsed_ms)

                            await server_ws.send_json({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": item["call_id"],
                                    "output": result.to_text() if result.destination in (ToolResultDirection.TO_SERVER, ToolResultDirection.TO_BOTH) else ""
                                }
                            })
                            if result.destination in (ToolResultDirection.TO_CLIENT, ToolResultDirection.TO_BOTH):
                                await client_ws.send_json({
                                    "type": "extension.middle_tier_tool_response",
                                    "previous_item_id": tool_call.previous_id,
                                    "tool_name": item["name"],
                                    "tool_result": result.to_client_text()
                                })
                            updated_message = None

                case "response.done":
                    fn_calls = []
                    follow_up_sent = bool(tools_pending)
                    if tools_pending:
                        tools_pending.clear()
                        await server_ws.send_str(_RESPONSE_CREATE_MSG)
                    rate_limited = failed_response_rate_limit(message) if recovery is not None else None
                    if rate_limited is not None:
                        if follow_up_sent:
                            logger.warning("Rate-limited response had tool calls pending; the tool follow-up "
                                           "response.create already regenerates it (session=%s)", session_id)
                        else:
                            # Retrying re-runs only the model: tools run on output_item.done,
                            # which a failed response never produced.
                            await recovery.on_rate_limited(rate_limited, "response.done")
                    if "response" in message:
                        output = message["response"]["output"]
                        fn_calls = [o for o in output if o.get("type") == "function_call"]
                        if fn_calls:
                            logger.info("Response contained %d tool call(s): %s",
                                        len(fn_calls), [o.get("name", "?") for o in fn_calls])
                        else:
                            out_types = [o.get("type", "?") for o in output]
                            logger.info("Response completed with NO tool calls (output types: %s)", out_types)
                        _vlog(verbose, "  Response done — output types: %s",
                              [o.get("type", "?") for o in output])
                        filtered = [o for o in output if o.get("type") != "function_call"]
                        if len(filtered) != len(output):
                            message["response"]["output"] = filtered
                            updated_message = json.dumps(message)
                        # Remember what the crew member said, for rehydrating a resumed session.
                        if session_id is not None:
                            spoken = " ".join(
                                (content.get("transcript") or content.get("text") or "").strip()
                                for out_item in output
                                if out_item.get("type") == "message"
                                for content in out_item.get("content", []))
                            self._sessions.record_turn(session_id, "crew", spoken)
                    if session_id is not None and not fn_calls:
                        identifiers = order_state_singleton.advance_round_trip(session_id)
                        await self._sessions.emit_session_identifiers(client_ws, "extension.round_trip_token", identifiers)
                        _vlog(verbose, "─── [ROUND TRIP] #%d ───\n"
                                       "Token: %s\n"
                                       "────────────────────────",
                              identifiers.round_trip_index,
                              identifiers.round_trip_token)

        return updated_message

    async def _process_message_to_server(self, msg: str, ws: web.WebSocketResponse, verbose: bool = False, voice_locked: bool = False, guard: "_SessionUpdateGuard | None" = None) -> str | None:
        data = msg.data

        # FAST PATH: input_audio_buffer.append is the most frequent client message
        # (~10 per second). Skip JSON parse entirely — it never needs modification.
        m = _TYPE_RE.search(data)
        if m is not None and m.group(1) in _PASSTHROUGH_CLIENT_TYPES:
            return data

        message = json.loads(data)
        msg_type = message.get("type", "")
        updated_message = data
        if message is not None:
            _vlog(verbose, "─── [Client → Server] %s ───", msg_type)
            match msg_type:
                case "session.update":
                    session = self._build_session(message["session"], voice_locked=voice_locked)
                    tool_names = [t.get("name", "?") for t in session["tools"]]
                    message["session"] = session
                    # Every session.update carries an event_id so a rejection can
                    # be correlated and recovered (see _recover_rejected_session_update).
                    if guard is not None:
                        guard.stamp(message)
                    else:
                        message.setdefault("event_id", _new_event_id("mcd_su"))
                    logger.info(
                        "session.update: injected %d tools %s, tool_choice=%s, max_tokens=%s, reasoning=%s",
                        len(session["tools"]), tool_names, session["tool_choice"],
                        session.get("max_output_tokens"), session.get("reasoning"),
                    )
                    _vlog(verbose, "  Injected %d tools: %s, tool_choice=%s",
                          len(session["tools"]), tool_names, session["tool_choice"])
                    updated_message = json.dumps(message)

        return updated_message

    async def _forward_messages(self, ws: web.WebSocketResponse):
        # Per-connection tool tracking — prevents cross-connection interference
        tools_pending: dict[str, RTToolCall] = {}

        # Per-connection verbose logging toggle (set by frontend extension message)
        verbose = _VERBOSE_GLOBAL
        audio_frame_count = 0  # Counter for verbose audio frame logging
        # Per-connection file handler for verbose log-to-file (set by frontend or env var)
        session_file_handler: logging.FileHandler | None = None

        # Auto-create session log file if verbose log-to-file is enabled via env config
        if _VERBOSE_LOG_FILE_GLOBAL:
            verbose = True
            vlogger.setLevel(logging.DEBUG)
            if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in vlogger.handlers):
                _h = logging.StreamHandler()
                _h.setFormatter(logging.Formatter("%(message)s"))
                vlogger.addHandler(_h)
            session_file_handler = _create_verbose_file_handler()
            vlogger.addHandler(session_file_handler)
            logger.info("Verbose log-to-file auto-enabled (config/env)")
            _vlog(verbose,
                  "\n╔══════════════════════════════════════╗\n"
                  "║  LOG TO FILE: %-8s              ║\n"
                  "╚══════════════════════════════════════╝",
                  "ENABLED")

        # Echo suppression — delegates to EchoSuppressor
        echo = EchoSuppressor()

        async with aiohttp.ClientSession(
            base_url=self.endpoint,
            timeout=_WS_CONNECT_TIMEOUT,
        ) as session:
            params = {"model": self.deployment}
            headers = {}
            if "x-ms-client-request-id" in ws.headers:
                headers["x-ms-client-request-id"] = ws.headers["x-ms-client-request-id"]
            if self.key is not None:
                headers = { "api-key": self.key }
            else:
                headers = { "Authorization": f"Bearer {self._get_auth_token()}" }
            async with session.ws_connect(
                "/openai/v1/realtime",
                headers=headers,
                params=params,
                heartbeat=_WS_HEARTBEAT_SEC,
                compress=0,  # Azure OpenAI declines deflate anyway; don't offer it.
            ) as target_ws:
                loop = asyncio.get_running_loop()
                session_id = self._sessions.get_session_id(ws)
                greeting_sent = self._sessions.has_sent_greeting(session_id) if session_id else False
                # Set once the model has produced audio on this upstream socket;
                # from then on GA refuses voice changes (see _build_session).
                assistant_audio_seen = False
                session_configured = asyncio.Event()
                guard = _SessionUpdateGuard()

                async def _send_to_client(event: dict) -> None:
                    if not ws.closed:
                        await ws.send_json(event)

                recovery = RateLimitRecovery(self.rate_limit_config,
                                             send_upstream=lambda: target_ws.send_str(_RESPONSE_CREATE_MSG),
                                             send_client=_send_to_client, sleep=self._rate_limit_sleep,
                                             session_id=session_id)

                # ── Resume handshake state (one decision per socket) ──
                # A resume is honoured only as the first client frame. Until that
                # decision is made (first frame, or first_frame_timeout), the
                # session announcement is held back so a socket gets exactly one
                # of extension.session_metadata / extension.session_resumed.
                first_frame_pending = True
                resume_decided = asyncio.Event()
                upstream_created = False
                announced = False
                # Silent-guest nudge after a resume (once per resume).
                nudge_task: asyncio.Task | None = None

                _vlog(verbose, "\n═══ [SESSION] Connected ═══\n"
                               "Session ID: %s\n"
                               "═══════════════════════════", session_id or "?")

                # Configure the upstream session BEFORE relaying a single browser
                # frame. Events are processed in order, so nothing the browser
                # sends (mic audio included) can reach an unconfigured session.
                await target_ws.send_str(guard.track(self.build_bootstrap_session_update()))
                logger.info("Upstream session bootstrapped with %d tools before relaying client traffic "
                            "(reasoning=%s, session=%s)", len(self.tools),
                            normalize_reasoning_effort(self.reasoning_effort) if self.reasoning_enabled() else "off",
                            session_id)

                async def send_greeting_once(trigger: str = "unknown"):
                    nonlocal greeting_sent
                    if greeting_sent:
                        return
                    # Don't greet until the server has confirmed the session
                    # configuration (tools + instructions) -- or give up waiting.
                    try:
                        await asyncio.wait_for(session_configured.wait(), timeout=_SESSION_CONFIGURED_TIMEOUT_SEC)
                    except TimeoutError:
                        logger.warning("No session.updated within %.0fs; sending greeting anyway (session=%s)",
                                       _SESSION_CONFIGURED_TIMEOUT_SEC, session_id)
                    if greeting_sent:
                        return
                    greeting_sent = True
                    logger.info("Greeting firing via trigger=%s (session=%s)", trigger, session_id)
                    _vlog(verbose, "─── [Lifecycle] Greeting trigger=%s ───", trigger)
                    echo.start_greeting_suppression(verbose)
                    # Flush any stale audio that arrived before session was configured
                    await target_ws.send_str(_INPUT_AUDIO_CLEAR_MSG)
                    await target_ws.send_str(self._sessions.greeting_msg)
                    await target_ws.send_str(_RESPONSE_CREATE_MSG)
                    if session_id is not None:
                        self._sessions.mark_greeting_sent(session_id)

                async def announce_fresh():
                    """Send extension.session_metadata (with a resume id) once the resume
                    decision is made and the upstream session exists."""
                    nonlocal announced
                    if announced or not resume_decided.is_set() or not upstream_created or session_id is None:
                        return
                    announced = True
                    identifiers = order_state_singleton.get_session_identifiers(session_id)
                    resume_id = self._sessions.issue_resume_id(session_id)
                    await self._sessions.emit_session_identifiers(
                        ws, "extension.session_metadata", identifiers,
                        extra={"resumeId": resume_id} if resume_id else None)
                    _vlog(verbose, "─── [SESSION TOKEN] ───\n"
                                   "Token: %s\n"
                                   "Round Trip: #%d (token: %s)\n"
                                   "───────────────────────",
                          identifiers.session_token, identifiers.round_trip_index, identifiers.round_trip_token)

                async def on_session_created():
                    nonlocal upstream_created
                    upstream_created = True
                    await announce_fresh()

                async def first_frame_deadline():
                    await asyncio.sleep(self._sessions.first_frame_timeout_seconds)
                    if not resume_decided.is_set():
                        resume_decided.set()
                        await announce_fresh()

                async def nudge_after_silence():
                    """If the guest says nothing for nudge_after_seconds after a resume, have
                    the crew member ask once whether they need anything else. Goes through the
                    same session.updated gate as the greeting so voice/tools are confirmed."""
                    await asyncio.sleep(self._sessions.nudge_after_seconds)
                    await session_configured.wait()
                    if recovery.retry_pending:
                        # A rate-limit retry is about to make the model speak; a nudge
                        # on top would stack a second response.
                        logger.info("Resume nudge skipped: a rate-limit retry is pending (session=%s)", session_id)
                        return
                    logger.info("Guest silent %.0fs after resume; crew member nudges (session=%s)",
                                self._sessions.nudge_after_seconds, session_id)
                    await target_ws.send_str(self._sessions.build_nudge_item())
                    await target_ws.send_str(_RESPONSE_CREATE_MSG)

                def cancel_nudge(reason: str) -> None:
                    nonlocal nudge_task
                    if nudge_task is not None and not nudge_task.done():
                        nudge_task.cancel()
                        logger.info("Resume nudge cancelled: %s (session=%s)", reason, session_id)
                    nudge_task = None

                async def handle_resume(data: str):
                    nonlocal session_id, announced, greeting_sent, nudge_task
                    try:
                        presented = json.loads(data).get("resume_id")
                    except (ValueError, AttributeError):
                        presented = None
                    outcome = self._sessions.resume(ws, presented)
                    resume_decided.set()
                    if not outcome.accepted:
                        logger.info("Resume rejected (reason=%s, resume id %s); starting fresh session %s",
                                    outcome.reason, resume_id_fingerprint(presented), session_id)
                        await ws.send_json({"type": "extension.resume_rejected", "reason": outcome.reason})
                        await announce_fresh()
                        return
                    session_id = outcome.session_id
                    recovery.set_session_id(session_id)
                    if outcome.stale_ws is not None:
                        _spawn(_close_superseded(outcome.stale_ws))
                    identifiers = order_state_singleton.get_session_identifiers(session_id)
                    announced = True
                    await ws.send_json({
                        "type": "extension.session_resumed",
                        "order_summary": json.loads(order_state_singleton.get_order_summary_json(session_id)),
                        "session_token": identifiers.session_token,
                        "round_trip_index": identifiers.round_trip_index,
                        "round_trip_token": identifiers.round_trip_token,
                        "resume_id": outcome.resume_id,
                    })
                    if not outcome.conversation_started:
                        return                  # never greeted: the normal greeting still runs
                    # Mid-conversation: no greeting, no "welcome back". Brief the new
                    # upstream (after the bootstrap session.update, before any
                    # response.create) and stay silent until the guest speaks.
                    greeting_sent = True
                    await target_ws.send_str(self._sessions.build_rehydration_item(session_id))
                    logger.info("Resumed session %s rehydrated (%d recent turns); greeting suppressed",
                                session_id, len(self._sessions.recent_turns(session_id)))
                    if self._sessions.nudge_after_seconds > 0:
                        nudge_task = asyncio.ensure_future(nudge_after_silence())

                async def reject_late_resume(data: str):
                    nonlocal announced
                    try:
                        presented = json.loads(data).get("resume_id")
                    except (ValueError, AttributeError):
                        presented = None
                    logger.info("Resume rejected (reason=not_first_frame, resume id %s); session %s continues",
                                resume_id_fingerprint(presented), session_id)
                    await ws.send_json({"type": "extension.resume_rejected", "reason": "not_first_frame"})
                    # The browser drops its stored id on any rejection, so re-announce
                    # this socket's own session (with a rotated id) if already announced.
                    if announced:
                        announced = False
                        await announce_fresh()

                async def from_client_to_server():
                    nonlocal verbose, audio_frame_count, session_file_handler, first_frame_pending
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            # Resume handshake: only the very first client frame may resume.
                            if first_frame_pending:
                                first_frame_pending = False
                                if not resume_decided.is_set() and _extension_type(msg.data, _MARKER_RESUME) == "extension.resume":
                                    await handle_resume(msg.data)
                                    continue
                                if not resume_decided.is_set():
                                    resume_decided.set()
                                    await announce_fresh()
                            if _extension_type(msg.data, _MARKER_RESUME) == "extension.resume":
                                await reject_late_resume(msg.data)
                                continue
                            if _extension_type(msg.data, _MARKER_END_SESSION) == "extension.end_session":
                                logger.info("Guest ended session %s", session_id)
                                self._sessions.end_session(session_id, "guest ended the session")
                                await ws.close(code=SESSION_ENDED_CLOSE_CODE, message=SESSION_ENDED_CLOSE_REASON.encode())
                                break
                            # Guest activity drives the idle clock. Mic frames stream
                            # constantly (silence included), so they don't count; the
                            # guest actually speaking does (speech_started/transcripts
                            # from upstream).
                            if session_id and _MARKER_AUDIO_APPEND not in msg.data:
                                self._sessions.touch_activity(session_id)
                            # Intercept extension messages — don't forward to OpenAI
                            if _MARKER_VERBOSE_LOGGING in msg.data:
                                try:
                                    ext_msg = json.loads(msg.data)
                                    if ext_msg.get("type") == "extension.set_verbose_logging":
                                        verbose = bool(ext_msg.get("enabled", False))
                                        if verbose and not _VERBOSE_GLOBAL:
                                            # Enable the verbose logger for this session
                                            vlogger.setLevel(logging.DEBUG)
                                            if not vlogger.handlers:
                                                _h = logging.StreamHandler()
                                                _h.setFormatter(logging.Formatter("%(message)s"))
                                                vlogger.addHandler(_h)
                                        logger.info("Verbose logging %s for session %s",
                                                    "ENABLED" if verbose else "DISABLED", session_id)
                                        _vlog(verbose,
                                              "\n╔══════════════════════════════════════╗\n"
                                              "║  VERBOSE LOGGING: %-8s           ║\n"
                                              "╚══════════════════════════════════════╝",
                                              "ENABLED" if verbose else "DISABLED")
                                        continue  # Don't forward to OpenAI
                                except (json.JSONDecodeError, KeyError):
                                    pass

                            if _MARKER_LOG_TO_FILE in msg.data:
                                try:
                                    ext_msg = json.loads(msg.data)
                                    if ext_msg.get("type") == "extension.set_log_to_file":
                                        enabled = bool(ext_msg.get("enabled", False))
                                        if enabled and session_file_handler is None:
                                            # Ensure verbose logger is active
                                            vlogger.setLevel(logging.DEBUG)
                                            if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in vlogger.handlers):
                                                _h = logging.StreamHandler()
                                                _h.setFormatter(logging.Formatter("%(message)s"))
                                                vlogger.addHandler(_h)
                                            session_file_handler = _create_verbose_file_handler()
                                            vlogger.addHandler(session_file_handler)
                                        elif not enabled and session_file_handler is not None:
                                            _remove_verbose_file_handler(session_file_handler)
                                            session_file_handler = None
                                        logger.info("Verbose log-to-file %s for session %s",
                                                    "ENABLED" if enabled else "DISABLED", session_id)
                                        _vlog(verbose or enabled,
                                              "\n╔══════════════════════════════════════╗\n"
                                              "║  LOG TO FILE: %-8s              ║\n"
                                              "╚══════════════════════════════════════╝",
                                              "ENABLED" if enabled else "DISABLED")
                                        continue  # Don't forward to OpenAI
                                except (json.JSONDecodeError, KeyError):
                                    pass

                            if _MARKER_SET_VOICE in msg.data:
                                try:
                                    ext_msg = json.loads(msg.data)
                                    if ext_msg.get("type") == "extension.set_voice":
                                        new_voice = ext_msg.get("voice", DEFAULT_VOICE)
                                        if new_voice in GA_REALTIME_VOICES:
                                            previous_voice = self.voice_choice
                                            self.voice_choice = new_voice
                                            logger.info("[VOICE] Voice change request: %s → %s (session %s)", previous_voice, new_voice, session_id)
                                            if assistant_audio_seen:
                                                # GA would reject this with cannot_update_voice.
                                                logger.info("[VOICE] Assistant audio already present — voice %s applies from the next conversation (session %s)", new_voice, session_id)
                                                _vlog(verbose, "─── [Voice Change] Deferred voice=%s (voice locked) ───", new_voice)
                                            else:
                                                # The bootstrap already configured the session, so the
                                                # voice-only update cannot race ahead of the tools.
                                                session_update = guard.track(self.build_voice_update(new_voice))
                                                await target_ws.send_str(session_update)
                                                logger.info("[VOICE] Sent session.update to OpenAI (session %s): %s", session_id, session_update)
                                                _vlog(verbose, "─── [Voice Change] Sent session.update voice=%s to OpenAI ───", new_voice)
                                        continue  # Don't forward to OpenAI
                                except (json.JSONDecodeError, KeyError):
                                    pass

                            # Echo suppression: drop mic audio while AI is speaking or cooling down.
                            if _MARKER_AUDIO_APPEND in msg.data:
                                if echo.should_suppress_audio(loop.time()):
                                    continue
                                audio_frame_count += 1
                                if (verbose or _VERBOSE_GLOBAL) and audio_frame_count % 50 == 0:
                                    _vlog(verbose, "─── [Client → Server] Audio frame #%d ───", audio_frame_count)
                            # Barge-in: client sent response.cancel — user wants to speak.
                            if _MARKER_RESPONSE_CANCEL in msg.data:
                                echo.on_barge_in(verbose)
                            if nudge_task is not None and _MARKER_RESPONSE_CREATE in msg.data:
                                cancel_nudge("guest-initiated response")
                            # Forward client message to OpenAI.
                            new_msg = await self._process_message_to_server(msg, ws, verbose, voice_locked=assistant_audio_seen, guard=guard)
                            if new_msg is not None:
                                await target_ws.send_str(new_msg)
                            # The browser's session.update marks the start of a conversation.
                            if not greeting_sent and _MARKER_SESSION_UPDATE in msg.data and _MARKER_SESSION_UPDATED not in msg.data:
                                logger.info("Client session.update forwarded — sending greeting")
                                await send_greeting_once(trigger="client-session.update")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error("Client WebSocket error: %s", ws.exception())
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break
                    
                    if target_ws and not target_ws.closed:
                        logger.info("Closing OpenAI's realtime socket connection.")
                        _vlog(verbose, "─── [Lifecycle] Disconnect — closing OpenAI socket ───")
                        await target_ws.close()
                        
                async def from_server_to_client():
                    nonlocal assistant_audio_seen
                    async for msg in target_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = msg.data
                            if _MARKER_AUDIO_DELTA in data or _MARKER_AUDIO_DELTA_LEGACY in data:
                                assistant_audio_seen = True
                                echo.on_audio_delta(verbose)
                            elif _MARKER_AUDIO_DONE in data or _MARKER_AUDIO_DONE_LEGACY in data:
                                echo.on_audio_done(loop, target_ws, verbose)
                            elif _MARKER_SPEECH_STARTED in data:
                                echo.on_speech_started(verbose)
                                recovery.cancel("the guest started speaking")
                                if session_id:
                                    self._sessions.touch_activity(session_id)
                                cancel_nudge("guest speech")
                            elif _MARKER_TRANSCRIPTION_COMPLETED in data and session_id:
                                self._sessions.touch_activity(session_id)
                                cancel_nudge("guest transcript")
                                try:
                                    self._sessions.record_turn(session_id, "guest", json.loads(data).get("transcript"))
                                except (ValueError, AttributeError):
                                    pass
                            elif _MARKER_RESPONSE_CREATED in data:
                                recovery.on_response_created()

                            # The bootstrap session.updated arrives as soon as the socket
                            # opens, so it must NOT trigger the greeting -- the browser's
                            # session.update (conversation start) does that.
                            if _MARKER_SESSION_UPDATED in data:
                                guard.on_session_updated()
                                if not session_configured.is_set():
                                    logger.info("session.updated received — tools are configured (session=%s)", session_id)
                                    _vlog(verbose, "─── [Lifecycle] session.updated — session configured ───")
                                    session_configured.set()

                            # Verbose: log conversation transcription events
                            if (verbose or _VERBOSE_GLOBAL):
                                if '"conversation.item.input_audio_transcription.completed"' in data:
                                    try:
                                        _tr_msg = json.loads(data)
                                        _tr_text = _tr_msg.get("transcript", "")[:200]
                                        _vlog(verbose, '\n─── [User] transcription.completed ───\n"%s"', _tr_text)
                                    except (json.JSONDecodeError, KeyError):
                                        pass

                            new_msg = await self._process_message_to_client(msg, ws, target_ws, tools_pending, verbose, guard=guard,
                                                              recovery=recovery, on_session_created=on_session_created)
                            if new_msg is not None:
                                await ws.send_str(new_msg)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error("Server WebSocket error: %s", target_ws.exception())
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break

                deadline_task = asyncio.ensure_future(first_frame_deadline())
                try:
                    await asyncio.gather(from_client_to_server(), from_server_to_client())
                except ConnectionResetError:
                    pass
                except Exception:
                    logger.exception("Unexpected error in WebSocket forwarding")
                finally:
                    deadline_task.cancel()
                    cancel_nudge("socket closed")
                    recovery.close()
                    _vlog(verbose, "\n═══ [SESSION] Disconnected ═══\n"
                                   "Session ID: %s\n"
                                   "══════════════════════════════", session_id or "?")
                    if session_file_handler is not None:
                        _remove_verbose_file_handler(session_file_handler)
                        session_file_handler = None
                    # A transport close holds the order for the resume grace period
                    # (an idle close already ended the session, so this is a no-op then).
                    # recovery.close() above has cancelled any pending rate-limit retry.
                    self._sessions.detach_session(ws, session_id, reason=f"client close code={ws.close_code}")

    async def _websocket_handler(self, request: web.Request):
        # ── Origin validation ──
        origin = request.headers.get("Origin", "")
        allowed_origins = _security_cfg.get("allowed_origins", [])
        host = request.headers.get("Host", "")
        if origin and not origin.endswith(host) and origin not in allowed_origins:
            logger.warning("Rejected WebSocket from disallowed origin: %s", origin)
            return web.Response(status=403, text="Origin not allowed")

        # ── HMAC session token validation ──
        if _security_cfg.get("require_session_token", False):
            token = request.query.get("token", "")
            if not validate_hmac_token(token, self.app_secret):
                logger.warning("Rejected WebSocket with invalid/expired session token")
                return web.Response(status=401, text="Invalid or expired token")

        # ── Concurrency limit ──
        if not self._sessions.can_accept_session():
            logger.warning("Rejected WebSocket — session limit reached (%d)", self._sessions.active_session_count)
            ws = web.WebSocketResponse(compress=_WS_COMPRESS)
            await ws.prepare(request)
            await ws.send_json({"type": "error", "message": "Server is busy — please try again in a moment."})
            await ws.close()
            return ws

        ws = web.WebSocketResponse(
            heartbeat=_WS_HEARTBEAT_SEC,
            autoping=True,
            autoclose=True,
            compress=_WS_COMPRESS,
        )
        await ws.prepare(request)

        self._sessions.create_session(ws)

        try:
            await self._forward_messages(ws)
        except (TimeoutError, aiohttp.ClientConnectorError, aiohttp.WSServerHandshakeError, OSError) as exc:
            logger.error("Failed to connect to Azure OpenAI Realtime: %s", exc)
            if not ws.closed:
                try:
                    await ws.send_json({
                        "type": "error",
                        "error": {
                            "message": (
                                "Cloud service unreachable — the backend cannot reach Azure OpenAI. "
                                "Switch to local mode or check your network connection."
                            ),
                            "code": "cloud_unreachable",
                        },
                    })
                except Exception:
                    pass
        except Exception as exc:
            logger.exception("Unexpected error in cloud WebSocket handler: %s", exc)
            if not ws.closed:
                try:
                    await ws.send_json({
                        "type": "error",
                        "error": {"message": f"Unexpected backend error: {exc}"},
                    })
                except Exception:
                    pass
        finally:
            # Covers an upstream connect failure, which never reaches the
            # forwarder's own finally. A no-op if that already ran.
            self._sessions.detach_session(ws, self._sessions.get_session_id(ws),
                                          reason=f"handler exit code={ws.close_code}")
        return ws
    
    def attach_to_app(self, app: web.Application, path: str) -> None:
        app.router.add_get(path, self._websocket_handler)


def configure_realtime_model(rtmt: RTMiddleTier, model_cfg: dict, environ: Any = None) -> RTMiddleTier:
    """Apply `config.yaml` `model:` settings plus their env overrides to `rtmt`.

    Shared by app.py and scripts/smoke_realtime.py so the smoke check sends
    exactly the session the app sends.
    """
    env = os.environ if environ is None else environ
    rtmt.temperature = model_cfg.get("temperature", 0.6)
    rtmt.max_tokens = model_cfg.get("max_response_output_tokens", 4096)
    rtmt.transcription_model = (env.get("AZURE_OPENAI_REALTIME_TRANSCRIPTION_MODEL")
                                or model_cfg.get("transcription_model") or "whisper-1")
    effort = env.get("AZURE_OPENAI_REALTIME_REASONING_EFFORT")
    rtmt.reasoning_effort = normalize_reasoning_effort(effort if effort else model_cfg.get("reasoning_effort"))
    parallel = model_cfg.get("parallel_tool_calls")
    rtmt.parallel_tool_calls = None if parallel is None else bool(parallel)
    switch = env.get("AZURE_OPENAI_REALTIME_REASONING_MODEL")
    rtmt.reasoning_model = parse_reasoning_model(switch if switch else model_cfg.get("reasoning_model"))
    if rtmt.reasoning_effort is not None and not rtmt._reasoning_model():
        logger.info("Deployment %s is not treated as a reasoning model (reasoning_model=%s); `reasoning` "
                    "(effort=%s) will not be sent", rtmt.deployment,
                    "auto" if rtmt.reasoning_model is None else rtmt.reasoning_model, rtmt.reasoning_effort)
    return rtmt
