import gzip
import logging
import os
import sys
from pathlib import Path

from aiohttp import web
from azure.core.credentials import AzureKeyCredential
from azure.identity import AzureDeveloperCliCredential, DefaultAzureCredential
from dotenv import load_dotenv

from config_loader import get_config
from prompt_loader import PromptLoader
from rtmt import RTMiddleTier
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


async def _health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})


async def create_app() -> web.Application:
    """Configure and return the aiohttp application for realtime ordering."""

    if not _get_bool_env("RUNNING_IN_PRODUCTION", False):
        logger.info("Running in development mode; loading values from .env")
        load_dotenv()

    llm_endpoint = os.environ.get("AZURE_OPENAI_EASTUS2_ENDPOINT")
    llm_deployment = os.environ.get("AZURE_OPENAI_REALTIME_DEPLOYMENT")
    if not llm_endpoint or not llm_deployment:
        raise RuntimeError("Azure OpenAI realtime endpoint and deployment must be configured.")

    llm_key = os.environ.get("AZURE_OPENAI_EASTUS2_API_KEY")
    search_key = os.environ.get("AZURE_SEARCH_API_KEY")

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

    app = web.Application(
        middlewares=[_compression_middleware],
        client_max_size=_cfg["connection"].get("client_max_size_bytes", 4 * 1024 * 1024),
    )

    _model_cfg = _cfg.get("model", {})
    rtmt = RTMiddleTier(
        credentials=llm_credential,
        endpoint=llm_endpoint,
        deployment=llm_deployment,
        voice_choice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE_CHOICE") or _model_cfg.get("default_voice", "shimmer"),
        prompt_loader=prompt_loader,
    )
    if api_version := os.environ.get("AZURE_OPENAI_REALTIME_API_VERSION"):
        rtmt.api_version = api_version
    rtmt.temperature = _model_cfg.get("temperature", 0.6)
    rtmt.max_tokens = _model_cfg.get("max_response_output_tokens", 4096)

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
        # Defaults aligned with the menu ingestion index schema; override via env vars as needed.
        semantic_configuration=os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIGURATION") or "menuSemanticConfig",
        identifier_field=os.environ.get("AZURE_SEARCH_IDENTIFIER_FIELD") or "id",
        content_field=os.environ.get("AZURE_SEARCH_CONTENT_FIELD") or "description",
        embedding_field=os.environ.get("AZURE_SEARCH_EMBEDDING_FIELD") or "embedding",
        title_field=os.environ.get("AZURE_SEARCH_TITLE_FIELD") or "name",
        use_vector_query=_get_bool_env("AZURE_SEARCH_USE_VECTOR_QUERY", True),
        prompt_loader=prompt_loader,
    )

    rtmt.attach_to_app(app, "/realtime")

    current_directory = Path(__file__).parent
    app.add_routes([
        web.get('/', _index_handler),
        web.get('/health', _health_handler),
    ])
    app.router.add_static(
        '/',
        path=current_directory / 'static',
        name='static',
        append_version=True,
    )

    async def _on_shutdown(app: web.Application):
        logger.info("Graceful shutdown initiated — cleaning up active sessions")

    app.on_shutdown.append(_on_shutdown)

    return app


if __name__ == "__main__":
    host = os.environ.get("HOST", "localhost")
    port = int(os.environ.get("PORT", 8000))
    _conn_cfg = _cfg.get("connection", {})
    web.run_app(
        create_app(),
        host=host,
        port=port,
        shutdown_timeout=_conn_cfg.get("shutdown_timeout", 10.0),
        keepalive_timeout=_conn_cfg.get("keepalive_timeout", 75.0),
    )
