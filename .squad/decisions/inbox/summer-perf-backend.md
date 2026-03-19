# Backend Production Performance Hardening

**Author:** Summer (Backend Dev)
**Date:** 2026-03-19
**Status:** Implemented — 100 tests passing

## Decisions Made

### 1. Per-connection tool call tracking (rtmt.py)
`_tools_pending` was a shared dict on the RTMiddleTier instance. If two WebSocket clients were connected simultaneously, their tool calls would interfere. Moved to a local dict inside `_forward_messages()` — each connection gets its own isolated tracking.

### 2. Audio hot-path fast return (rtmt.py)
Added `_PASSTHROUGH_TYPES` frozenset containing 13 high-frequency message types that are never modified by the middleware (e.g. `response.audio.delta`). These now return immediately after JSON parse, skipping the match/case block entirely. During active speech, this is ~90% of messages.

### 3. WebSocket keepalive (rtmt.py)
Both client-facing and Azure OpenAI WebSockets now use `heartbeat=15s` with autoping. This prevents Azure load balancers and proxies from dropping idle connections during long pauses in conversation.

### 4. Search result caching (tools.py)
Added `_SearchCache` — a simple TTL dict cache (60s TTL, 128 entry max) for Azure AI Search results. Repeated questions about the same menu item (common in drive-thru ordering) skip the round-trip to Azure AI Search entirely.

### 5. Reduced search payload (tools.py)
Cut `select_fields` from 11 fields to 5 — only the fields actually used in result formatting. Reduces network payload and Azure Search response time.

### 6. Gzip compression middleware (app.py)
Added `_compression_middleware` for text-based HTTP responses (JSON, JS, HTML, SVG). Skips WebSocket upgrades, FileResponse (streaming), and responses smaller than 256 bytes.

### 7. Static file cache headers (app.py)
- `index.html`: `Cache-Control: no-cache` (always revalidate)
- Static assets: `append_version=True` on aiohttp static router for cache-busting

### 8. Production server tuning (app.py)
- `client_max_size=4MB` — prevents oversized request abuse
- `shutdown_timeout=10s` — graceful drain on SIGTERM
- `keepalive_timeout=75s` — matches common reverse proxy defaults

### 9. Logging demotion (order_state.py)
All per-item and per-round-trip log statements demoted from INFO to DEBUG. At INFO level, the `get_order_summary` call was serializing the entire order to a string on every invocation — pure waste in production.

### 10. Memory optimization (rtmt.py)
Added `__slots__` to `ToolResult`, `Tool`, and `RTToolCall` — reduces per-object memory and slightly speeds attribute access for these high-frequency objects.

## No New Dependencies
All changes use Python stdlib and existing aiohttp features. No new pip packages required.
