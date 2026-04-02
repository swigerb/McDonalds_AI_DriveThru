import gzip
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv

from config_loader import get_config, get_local_mode_config
from local_search import attach_local_tools
from prompt_loader import PromptLoader
from processor_router import ProcessorRouter
from rtmt import RTMiddleTier, create_hmac_token
from tools import attach_tools_rtmt

# Production: INFO; override with LOG_LEVEL env var for debugging
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _log_level, logging.INFO))
logger = logging.getLogger(__name__)

# Load centralized config
_cfg = get_config()
_compression = _cfg.get("compression", {})

# Minimum response size worth compressing (bytes)
_COMPRESS_MIN_SIZE = _compression.get("min_size_bytes", 256)
# Cache-Control for immutable hashed assets (JS/CSS bundles from Vite)
_STATIC_IMMUTABLE_MAX_AGE = _compression.get("static_immutable_max_age", 31_536_000)  # 1 year
# Cache-Control for mutable files (index.html, etc.)
_STATIC_DEFAULT_MAX_AGE = _compression.get("static_default_max_age", 3600)  # 1 hour
# Compressible content-type substrings
_COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "image/svg")

# App version — exposed via /health endpoint
_APP_VERSION = "1.0.0"

# Required environment variables for the app to function
_REQUIRED_ENV_VARS = [
    "AZURE_OPENAI_EASTUS2_ENDPOINT",
    "AZURE_OPENAI_REALTIME_DEPLOYMENT",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_INDEX",
]

# Startup validation state — read by /health endpoint
_startup_checks = {
    "prompts_loaded": False,
    "config_loaded": True,  # validated at module load by get_config()
    "env_vars": False,
}

# Load prompt loader — fail fast if prompt YAML files are missing
try:
    prompt_loader = PromptLoader()
except FileNotFoundError:
    logger.warning("Prompt YAML files not found — running without prompt loader (hardcoded fallback)")
    prompt_loader = None


def _get_bool_env(variable_name: str, default: bool = False) -> bool:
    """Parse boolean environment variables with predictable defaults."""
    value = os.environ.get(variable_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

@web.middleware
async def _compression_middleware(request: web.Request, handler):
    """Gzip-compress eligible responses when the client accepts it."""
    response = await handler(request)

    # Only compress regular Response objects (not FileResponse, StreamResponse, WebSocket)
    if not isinstance(response, web.Response) or isinstance(response, web.WebSocketResponse):
        return response
    if response.body is None or len(response.body) < _COMPRESS_MIN_SIZE:
        return response

    accept_encoding = request.headers.get("Accept-Encoding", "")
    if "gzip" not in accept_encoding:
        return response

    content_type = response.content_type or ""
    if not any(ct in content_type for ct in _COMPRESSIBLE_TYPES):
        return response

    compressed = gzip.compress(response.body, compresslevel=_compression.get("level", 6))
    if len(compressed) >= len(response.body):
        return response

    response.body = compressed
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    return response


# ---------------------------------------------------------------------------
# Static file helpers
# ---------------------------------------------------------------------------

async def _index_handler(_request: web.Request) -> web.FileResponse:
    current_directory = Path(__file__).parent
    resp = web.FileResponse(current_directory / "static" / "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# Startup state for local mode — populated during create_app()
_local_mode_info: dict = {"available": False, "device": None}


async def _health_handler(_request: web.Request) -> web.Response:
    all_ok = all(_startup_checks.values())
    return web.json_response(
        {
            "status": "healthy" if all_ok else "unhealthy",
            "version": _APP_VERSION,
            "checks": _startup_checks,
            "local_mode": _local_mode_info,
        },
        status=200 if all_ok else 503,
    )


async def _check_service_connectivity() -> None:
    """Verify Azure service endpoints are reachable. Non-blocking — logs warnings only."""
    endpoints = {
        "Azure OpenAI": os.environ.get("AZURE_OPENAI_EASTUS2_ENDPOINT"),
        "Azure Search": os.environ.get("AZURE_SEARCH_ENDPOINT"),
    }
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name, url in endpoints.items():
                if not url:
                    continue
                try:
                    async with session.get(url, ssl=True) as resp:
                        logger.info("✅ %s reachable (HTTP %d)", name, resp.status)
                except Exception as exc:
                    logger.warning("⚠️ %s unreachable at %s — %s (non-fatal)", name, url, exc)
    except Exception as exc:
        logger.warning("⚠️ Service connectivity check failed — %s (non-fatal)", exc)


async def create_app() -> web.Application:
    """Configure and return the aiohttp application for realtime ordering."""

    if not _get_bool_env("RUNNING_IN_PRODUCTION", False):
        logger.info("Running in development mode; loading values from .env")
        load_dotenv()

    # ── Startup Validation ────────────────────────────────────────────────

    # Check local mode availability FIRST (before requiring Azure env vars)
    local_config = get_local_mode_config()
    local_processor = None
    local_mode_available = False
    try:
        from local_processor import LocalPhi4Processor, LOCAL_MODE_AVAILABLE
        if LOCAL_MODE_AVAILABLE:
            local_processor = LocalPhi4Processor(config=local_config)
            attach_local_tools(local_processor, prompt_loader=prompt_loader)
            local_mode_available = True
            logger.info("Local Phi-4 processor created (device=%s)", local_processor.device)
        else:
            logger.info("Local mode: not available (onnxruntime-genai not installed)")
    except Exception as exc:
        logger.info("Local processor unavailable: %s — cloud-only mode", exc)

    # 1. Validate required environment variables
    #    Non-fatal when local mode is available — allows fully offline startup
    missing_vars = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing_vars:
        if local_mode_available:
            logger.warning(
                "Missing Azure environment variables: %s — cloud mode unavailable, running local-only",
                ", ".join(missing_vars),
            )
            _startup_checks["env_vars"] = False
        else:
            logger.critical(
                "FATAL: Missing required environment variables: %s", ", ".join(missing_vars)
            )
            sys.exit(1)
    else:
        _startup_checks["env_vars"] = True

    # 2. Mark prompts loaded if prompt_loader succeeded at module init
    if prompt_loader is not None:
        _startup_checks["prompts_loaded"] = True

    # 3. Optional: verify Azure service connectivity (non-blocking)
    if not missing_vars:
        await _check_service_connectivity()
    else:
        logger.info("Skipping Azure service connectivity check (env vars missing)")

    env_count = len(_REQUIRED_ENV_VARS)
    env_set = env_count - len(missing_vars)
    logger.info(
        "✅ Startup validation passed: config valid, %d/%d env vars set%s",
        env_set,
        env_count,
        " (local-only mode)" if missing_vars else "",
    )

    # ── App Configuration ─────────────────────────────────────────────────

    llm_endpoint = os.environ.get("AZURE_OPENAI_EASTUS2_ENDPOINT")
    llm_deployment = os.environ.get("AZURE_OPENAI_REALTIME_DEPLOYMENT")

    llm_key = os.environ.get("AZURE_OPENAI_EASTUS2_API_KEY")
    search_key = os.environ.get("AZURE_SEARCH_API_KEY")

    # Cloud processor setup — skip when running in local-only mode
    rtmt = None
    if not missing_vars:
        credential = None
        if not llm_key or not search_key:
            if tenant_id := os.environ.get("AZURE_TENANT_ID"):
                logger.info("Using AzureDeveloperCliCredential with tenant_id %s", tenant_id)
                credential = AzureDeveloperCliCredential(tenant_id=tenant_id, process_timeout=60)
            else:
                logger.info("Using DefaultAzureCredential")
                credential = DefaultAzureCredential()

        llm_credential = AzureKeyCredential(llm_key) if llm_key else credential
        search_credential = AzureKeyCredential(search_key) if search_key else credential

        conn_cfg = _cfg.get("connection", {})

        model_cfg = _cfg.get("model", {})
        rtmt = RTMiddleTier(
            credentials=llm_credential,
            endpoint=llm_endpoint,
            deployment=llm_deployment,
            voice_choice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or model_cfg.get("default_voice", "shimmer"),
            prompt_loader=prompt_loader,
        )
        # Generate a random secret for HMAC session tokens
        app_secret = os.urandom(32)
        rtmt.app_secret = app_secret
        if api_version := os.environ.get("AZURE_OPENAI_REALTIME_API_VERSION"):
            rtmt.api_version = api_version
        else:
            rtmt.api_version = model_cfg.get("api_version", "2024-10-01-preview")
        rtmt.temperature = model_cfg.get("temperature", 0.6)
        rtmt.max_tokens = model_cfg.get("max_response_output_tokens", 4096)

        # System message: prefer externalized YAML prompt, fall back to hardcoded
        if prompt_loader is not None:
            rtmt.system_message = prompt_loader.get_system_prompt()
        else:
            rtmt.system_message = (
                "You are a McDonald's crew member — friendly, efficient, and FAST. You take drive-thru orders at the world's most famous restaurant.\n\n"
                "GREETING:\n"
                "- Welcome to McDonald's! I'm your digital assistant. What can I get started for you today?\n\n"
                "VOICE STYLE:\n"
                "- You ARE the crew member — NEVER explain what you would say. Just SAY it directly.\n"
                "- Warm, upbeat, efficient — the 'I'm Lovin' It' energy\n"
                "- ONE or TWO short sentences max per response\n"
            )

        attach_tools_rtmt(
            rtmt,
            credentials=search_credential,
            search_endpoint=os.environ.get("AZURE_SEARCH_ENDPOINT"),
            search_index=os.environ.get("AZURE_SEARCH_INDEX"),
            semantic_configuration=os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIGURATION") or "menuSemanticConfig",
            identifier_field=os.environ.get("AZURE_SEARCH_IDENTIFIER_FIELD") or "id",
            content_field=os.environ.get("AZURE_SEARCH_CONTENT_FIELD") or "description",
            embedding_field=os.environ.get("AZURE_SEARCH_EMBEDDING_FIELD") or "embedding",
            title_field=os.environ.get("AZURE_SEARCH_TITLE_FIELD") or "name",
            use_vector_query=_get_bool_env("AZURE_SEARCH_USE_VECTOR_QUERY", True),
            prompt_loader=prompt_loader,
        )
    else:
        conn_cfg = _cfg.get("connection", {})
        model_cfg = _cfg.get("model", {})
        app_secret = os.urandom(32)
        logger.info("Cloud processor (RTMiddleTier) skipped — Azure env vars not set")

    app = web.Application(
        middlewares=[_compression_middleware],
        client_max_size=conn_cfg.get("client_max_size_bytes", 4 * 1024 * 1024),
    )

    # Set system message on local processor (from prompt loader or fallback)
    if local_processor is not None:
        if rtmt is not None:
            local_processor.system_message = rtmt.system_message
        elif prompt_loader is not None:
            local_processor.system_message = prompt_loader.get_system_prompt()
        else:
            local_processor.system_message = (
                "You are a McDonald's crew member — friendly, efficient, and FAST. "
                "You take drive-thru orders at the world's most famous restaurant."
            )

    # Check model files at startup — warn but don't block
    _repo_root = Path(__file__).resolve().parent.parent.parent
    model_path_raw = local_config.get("model_path", "./models/phi4-multimodal")
    tts_model_path_raw = local_config.get("tts_model_path", "./models/piper")
    model_path = Path(model_path_raw) if Path(model_path_raw).is_absolute() else _repo_root / model_path_raw.lstrip("./")
    tts_model_path = Path(tts_model_path_raw) if Path(tts_model_path_raw).is_absolute() else _repo_root / tts_model_path_raw.lstrip("./")
    model_exists = model_path.exists() and model_path.is_dir()
    tts_exists = tts_model_path.exists() and tts_model_path.is_dir()

    if local_mode_available:
        if not model_exists:
            logger.warning(
                "Local mode model not found at %s — Run: python scripts/download_local_models.py",
                model_path,
            )
        else:
            try:
                total_bytes = sum(f.stat().st_size for f in model_path.rglob("*") if f.is_file())
                size_gb = total_bytes / (1024 ** 3)
                logger.info("Local mode: model at %s (%.2f GB)", model_path, size_gb)
            except OSError:
                logger.info("Local mode: model at %s", model_path)

        if not tts_exists:
            logger.warning(
                "Local mode TTS model not found at %s — local TTS unavailable",
                tts_model_path,
            )
        else:
            logger.info("Local mode: TTS model %s ready", local_config.get("tts_default_voice", local_config.get("tts_model", "unknown")))

        _detected_device = local_config.get("device", "auto")
        if _detected_device == "auto":
            _detected_device = "cpu"
            try:
                import onnxruntime as _ort
                eps = _ort.get_available_providers()
                if "DmlExecutionProvider" in eps:
                    _detected_device = "directml"
                elif "CUDAExecutionProvider" in eps:
                    _detected_device = "cuda"
            except Exception:
                pass
        logger.info("Local mode: available (%s)", _detected_device.upper() if _detected_device != "cpu" else "CPU only")
    else:
        _detected_device = None
        logger.info("Local mode: not available (dependencies not installed)")

    # Populate health endpoint info
    _local_mode_info["available"] = local_mode_available
    _local_mode_info["device"] = local_processor.device if local_processor else None

    router = ProcessorRouter(cloud_processor=rtmt, local_processor=local_processor)
    router.attach_to_app(app, "/realtime")

    # ── HMAC Session Token Endpoint ──
    async def get_session_token(_request: web.Request) -> web.Response:
        token = create_hmac_token(app_secret, expiry_seconds=900)
        return web.json_response({"token": token})

    # ── Local Mode Status Endpoint ──
    async def local_mode_status(_request: web.Request) -> web.Response:
        if local_processor is not None:
            return web.json_response({
                "available": local_processor.available,
                "enabled": local_config.get("enabled", False),
                "device": local_processor.device,
                "model_loaded": local_processor.model_loaded,
                "model_path": str(local_config.get("model_path", "")),
                "model_exists": model_exists,
                "tts_model": local_config.get("tts_default_voice", local_config.get("tts_model", "")),
                "tts_available": tts_exists,
            })
        return web.json_response({
            "available": False,
            "enabled": False,
            "device": None,
            "model_loaded": False,
            "model_path": str(local_config.get("model_path", "")),
            "model_exists": model_exists,
            "tts_model": local_config.get("tts_default_voice", local_config.get("tts_model", "")),
            "tts_available": False,
        })

    # ── Local Mode Toggle Endpoint ──
    async def local_mode_toggle(request: web.Request) -> web.Response:
        """Toggle local/cloud mode at runtime.

        POST /api/local-mode/toggle  { "mode": "local" | "cloud" | "auto" }
        Called by the frontend when the user toggles local mode in the UI.
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        mode = body.get("mode", "").lower()
        if mode not in ("local", "cloud", "auto"):
            return web.json_response(
                {"error": "mode must be 'local', 'cloud', or 'auto'"}, status=400
            )

        if mode == "local" and not local_mode_available:
            return web.json_response(
                {"error": "Local mode not available (dependencies not installed)"}, status=400
            )

        router.set_runtime_mode(mode)
        return web.json_response({
            "mode": router.active_mode,
            "local_available": local_mode_available,
        })

    # ── Diagnostics Endpoint ──
    async def diagnostics_handler(_request: web.Request) -> web.Response:
        """Return comprehensive diagnostic info for debugging."""
        diag: dict = {
            "current_mode": router.active_mode,
            "config_default_mode": router._default_mode,
            "runtime_mode_override": router._runtime_mode,
            "cloud_reachable": router._cloud_reachable,
            "cloud_reachable_cache_age_s": (
                round(time.time() - router._last_cloud_check, 1)
                if router._last_cloud_check > 0 else None
            ),
            "local_model_status": "not available",
            "gpu_provider": _detected_device,
            "tts_engine_status": "not available",
            "stt_engine_status": "not available",
            "last_error": router.last_error,
            "websocket_total_connections": router.ws_connection_count,
            "websocket_active_connections": router.active_connections,
            "cloud_processor": "configured" if rtmt is not None else "not configured (Azure env vars missing)",
        }

        if local_processor is not None:
            if local_processor.model_loaded:
                diag["local_model_status"] = "loaded"
            elif local_processor._loading:
                diag["local_model_status"] = "loading"
            elif local_processor.available:
                diag["local_model_status"] = "not loaded (lazy load — waiting for first connection)"
            else:
                diag["local_model_status"] = "not available"

            if local_processor._tts:
                diag["tts_engine_status"] = "loaded" if local_processor._tts.is_loaded else "not loaded"
            elif local_mode_available:
                diag["tts_engine_status"] = "not initialized"

            if local_processor._stt:
                diag["stt_engine_status"] = "loaded" if local_processor._stt.is_loaded else "not loaded"
            elif local_mode_available:
                diag["stt_engine_status"] = "not initialized"

        return web.json_response(diag)

    # ── Local Mode Voices Endpoint ──
    async def local_mode_voices(_request: web.Request) -> web.Response:
        from piper_tts import PIPER_VOICES
        available_ids = local_config.get("tts_available_voices", list(PIPER_VOICES.keys()))
        current = local_config.get("tts_default_voice", local_config.get("tts_model", "en_US-amy-medium"))
        ls = local_config.get("tts_length_scale", 0.9)

        if local_processor and hasattr(local_processor, "_tts") and local_processor._tts:
            current = local_processor._tts.current_voice
            ls = local_processor._tts.length_scale

        voices = []
        for vid in available_ids:
            meta = PIPER_VOICES.get(vid, {})
            voices.append({
                "id": vid,
                "name": meta.get("name", vid),
                "accent": meta.get("accent", ""),
                "personality": meta.get("personality", ""),
            })

        return web.json_response({
            "voices": voices,
            "current": current,
            "length_scale": ls,
        })

    current_directory = Path(__file__).parent
    app.add_routes([
        web.get('/', _index_handler),
        web.get('/health', _health_handler),
        web.get('/api/auth/session', get_session_token),
        web.get('/api/local-mode/status', local_mode_status),
        web.get('/api/local-mode/voices', local_mode_voices),
        web.post('/api/local-mode/toggle', local_mode_toggle),
        web.get('/api/diagnostics', diagnostics_handler),
    ])
    app.router.add_static(
        '/',
        path=current_directory / 'static',
        name='static',
        append_version=True,
    )

    async def _on_startup(app: web.Application):
        router.start_background_tasks()
        # Pre-populate cloud reachability cache so the first WebSocket
        # connection doesn't stall on a network probe.
        await router.probe_cloud_at_startup()
        logger.info("Background tasks started (token refresh, idle checker)")

    async def _on_shutdown(app: web.Application):
        logger.info("Graceful shutdown initiated — cleaning up active sessions")
        router.stop_background_tasks()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    return app


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    _conn_cfg = _cfg.get("connection", {})
    web.run_app(
        create_app(),
        host=host,
        port=port,
        shutdown_timeout=_conn_cfg.get("shutdown_timeout", 10.0),
        keepalive_timeout=_conn_cfg.get("keepalive_timeout", 75.0),
    )
