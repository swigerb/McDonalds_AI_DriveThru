# Mac Tonight — History

## Sessions

### 2025-07-22 — Phase 4: Backend Security Features
- Added HMAC session token utilities to `rtmt.py` (`create_hmac_token`, `validate_hmac_token`) — SHA-256 signed, base64-encoded JSON payloads with 15-min expiry
- Added `app_secret` field to `RTMiddleTier.__init__`, set by `app.py` via `os.urandom(32)` at startup
- Added three security gates to `_websocket_handler`: origin validation, HMAC token validation, concurrency limit
- Added `_security_cfg` module-level config load in `rtmt.py`
- Added `GET /api/auth/session` endpoint in `app.py` — returns fresh HMAC token
- All features disabled by default for demo safety (`require_session_token: false`, `allowed_origins: []`)
- Confirmed Phase 3 artifacts already in place: token refresh, background tasks, idle checker, activity tracking
- All 202 existing tests pass; pre-existing `test_combo_logic` failure unrelated (missing pytest-asyncio)

### 2026-03-25 — Prompt Externalization (YAML)
- Created `app/backend/prompts/mcdonalds/` with 6 YAML files porting the prompt externalization architecture from the Sonic project
- **manifest.yaml**: Brand metadata, file registry, model config (gpt-4o-realtime-preview, coral voice, temp 0.6)
- **system_prompt.yaml**: Converted hardcoded system prompt (app.py:127-273) into 22 prioritized sections preserving exact instruction text
- **greeting.yaml**: Standardized greeting message as conversation.item.create event
- **tool_schemas.yaml**: 4 tool definitions (search, update_order, get_order, reset_order) with McDonald's-specific descriptions
- **error_messages.yaml**: 12 error templates with Jinja2 variables for quantity limits, mod validation, extras restrictions, and search failures
- **hints.yaml**: 6 category-specific upsell hints (combo, burger, drink, shake, side, generic), 3 system hints (combo_incomplete, out_of_stock, happy_hour_active), 2 delta templates with Jinja2 variables
- All YAML validated syntactically via PyYAML

## Learnings
- System prompt has 22 distinct behavioral sections — priority ordering matters for model attention allocation
- McDonald's extras policy differs from Sonic: extras apply to drinks, shakes, McCafé beverages, and combos (not sides or standalone items)
- Jinja2 templating in error_messages.yaml and hints.yaml enables runtime string formatting without hardcoded f-strings
- The prompt externalization pattern decouples brand voice from application logic, enabling multi-brand support
- Phase 3 refactor already included token refresh, background tasks, idle checker, activity tracking, and SessionManager concurrency — Phase 4 only needed HMAC utilities + WebSocket security gates + session token endpoint
- HMAC session tokens use stateless validation (no server-side session store) — ephemeral `app_secret` rotates on restart, which is acceptable for drive-thru sessions under 15 minutes
- Security features are disabled by default (`require_session_token: false`, `allowed_origins: []`) — safe for demos, enable in production via config.yaml
- Origin validation uses `endswith(host)` which is permissive for subdomains — tighten for production deployments
