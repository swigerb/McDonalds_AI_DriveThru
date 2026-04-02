# Decision: Offline Auto-Fallback and Diagnostics Pipeline

**Author:** Grimace (Backend Dev)
**Date:** 2026-07-19
**Status:** Implemented

## Problem

When Brian went offline with local mode toggled ON in the UI, clicking the microphone button produced complete silence — no logs, no errors, nothing. The backend was running on localhost:8000 and should have been reachable.

## Root Cause

Three-layer failure:
1. **Config mismatch:** `config.yaml` has `local_mode.enabled: false`. ProcessorRouter defaults to "cloud" mode regardless of UI preference.
2. **Late mode signaling:** Frontend sends `extension.set_local_mode` via WebSocket message AFTER connection, but routing happens AT connection time. The preference arrives too late.
3. **Silent hang:** RTMiddleTier._forward_messages() tries ws_connect to Azure OpenAI. When offline, DNS resolution hangs for 10-30s, then throws an exception — but no error message is sent back to the WebSocket client. The connection just dies silently.

## Solution

### 1. Auto-Fallback (processor_router.py)
When mode resolves to "cloud" but a local processor exists, the router now does a quick (3s timeout) connectivity check to the Azure endpoint. If unreachable, it automatically falls back to local mode with a logged warning. No config changes needed.

### 2. Runtime Mode Toggle (app.py)
New `POST /api/local-mode/toggle` endpoint accepts `{"mode": "local"|"cloud"|"auto"}`. Birdie can wire this to the frontend's local mode toggle so the backend knows the user's preference BEFORE the WebSocket connects.

### 3. Graceful Offline Startup (app.py)
Missing Azure env vars now log a warning (not `sys.exit(1)`) when local mode is available. Cloud RTMiddleTier creation is skipped entirely in this case — ProcessorRouter handles `cloud_processor=None`.

### 4. Diagnostics Endpoint (app.py)
New `GET /api/diagnostics` returns comprehensive system state: current mode, model status, GPU provider, TTS/STT status, last error, WebSocket connection counts.

### 5. Pipeline Logging (all local modules)
`local-pipeline` logger added across processor_router, local_processor, phi4_model, piper_tts, whisper_stt, local_search. Every pipeline step is logged with session IDs and timing.

## Impact

- **No regressions:** 632 tests, same baseline (1 pre-existing failure, 2 pre-existing errors)
- **Cloud mode unchanged:** All existing cloud behavior preserved
- **Birdie action needed:** Wire `POST /api/local-mode/toggle` to the frontend's local mode toggle switch. Also consider adding `?mode=local` to the WebSocket URL when local mode is active.

## Files Changed

- `app/backend/processor_router.py` — Auto-fallback, runtime mode toggle, connection tracking, diagnostics
- `app/backend/app.py` — Graceful offline startup, `/api/diagnostics`, `/api/local-mode/toggle`
- `app/backend/local_processor.py` — Comprehensive pipeline logging
- `app/backend/phi4_model.py` — Pipeline logging for model load/inference
- `app/backend/piper_tts.py` — Pipeline logging for TTS load/synthesis
- `app/backend/whisper_stt.py` — Pipeline logging for STT load/transcription
- `app/backend/local_search.py` — Pipeline logging for menu search
- `app/backend/tests/test_performance.py` — Updated env var test for local-only mode
