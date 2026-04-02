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

### 2026-07-15 — Phase 2: Phi-4 ONNX Integration + Piper TTS
- Created `app/backend/phi4_model.py` — Phi4ModelManager with auto-GPU detection (CUDA → DirectML → CPU), async streaming inference via queue-based executor pattern, `<tool_call>` tag parsing for structured tool output
- Created `app/backend/piper_tts.py` — PiperTTSEngine with sentence-chunked streaming synthesis, linear-interpolation resampling (22050→24000 Hz), graceful fallback when Piper not installed
- Completed `app/backend/local_processor.py` — full pipeline: audio accumulation → energy-based VAD → 24kHz→16kHz downsample → Phi-4 inference → tool execution → Piper TTS → base64 audio deltas. Supports barge-in, response cancellation, lazy model loading, concurrent-safe processing lock
- Fixed duplicate `local_mode` section in `app/backend/config.yaml`
- All 423 existing tests pass; no regressions

### 2026-07-16 — Multi-Voice Piper TTS + Drive-Thru Energy
- Upgraded `app/backend/piper_tts.py` — multi-voice support with lazy one-at-a-time loading, `set_voice()` for runtime switching, `PIPER_VOICES` metadata dict (Amy/Jenny/Lessac/Kristin), `length_scale` parameter (default 0.9) for upbeat drive-thru delivery, `get_voice_info()` for API endpoint, backward-compatible `model_name` kwarg
- Updated `app/backend/local_processor.py` — `extension.set_piper_voice` WebSocket handler with validation against allowed voices list, error/confirmation response messages, backward-compat `tts_default_voice`/`tts_model` config fallback
- Updated `app/backend/config.yaml` — renamed `tts_model` → `tts_default_voice`, added `tts_length_scale: 0.9`, added `tts_available_voices` list (4 voices)
- Updated `app/backend/config_loader.py` — new defaults matching config.yaml schema
- Added `GET /api/local-mode/voices` endpoint in `app/backend/app.py` — returns voice list with metadata, current voice, and length_scale from live TTS engine state
- All 423 existing tests pass; no regressions

## Learnings
- System prompt has 22 distinct behavioral sections — priority ordering matters for model attention allocation
- McDonald's extras policy differs from Sonic: extras apply to drinks, shakes, McCafé beverages, and combos (not sides or standalone items)
- Jinja2 templating in error_messages.yaml and hints.yaml enables runtime string formatting without hardcoded f-strings
- The prompt externalization pattern decouples brand voice from application logic, enabling multi-brand support
- Phase 3 refactor already included token refresh, background tasks, idle checker, activity tracking, and SessionManager concurrency — Phase 4 only needed HMAC utilities + WebSocket security gates + session token endpoint
- HMAC session tokens use stateless validation (no server-side session store) — ephemeral `app_secret` rotates on restart, which is acceptable for drive-thru sessions under 15 minutes
- Security features are disabled by default (`require_session_token: false`, `allowed_origins: []`) — safe for demos, enable in production via config.yaml
- Origin validation uses `endswith(host)` which is permissive for subdomains — tighten for production deployments
- Phi-4 multimodal expects 16kHz PCM input; frontend sends 24kHz — downsample with numpy linear interpolation (no scipy dependency needed)
- onnxruntime_genai has 3 package variants (cuda, directml, base) — auto-detect in priority order at import time, not at class init
- Piper TTS native sample rate varies by voice (usually 22050 Hz) — always resample to 24kHz to match frontend AudioContext expectation
- Streaming token generation uses asyncio.Queue bridging sync executor thread → async generator — avoids blocking event loop while enabling real-time token delivery
- Energy-based VAD (RMS threshold on PCM chunks) is sufficient for structured drive-thru interactions; silero-vad would be better but adds ~200MB model weight
- Tool call extraction uses `<tool_call>` XML tags in Phi-4 output — compatible with the model's instruction-following format
- Processing lock prevents overlapping inference calls on the same connection — Phi-4 ONNX is not thread-safe for concurrent generation on same model instance
- Lazy model loading (`lazy_load: true`) defers ~2-4GB memory allocation until first WebSocket connection — critical for shared hosting environments
- Piper `synthesize()` accepts `length_scale` as a direct keyword argument — no need for SynthesisConfig object; fallback via TypeError catch for older piper-tts versions
- Only one Piper voice model (~60 MB each) loaded in memory at a time — `set_voice()` unloads previous before loading new to keep RAM bounded
- Config key renamed from `tts_model` → `tts_default_voice` with backward compat via `or self._config.get("tts_model")` fallback in local_processor.py

## Team Updates (2026-04-02T16:30Z)

### Offline Mode Phase Completion
- ✅ **Phase 2 (Mac Tonight):** Phi-4 pipeline complete — ONNX streaming inference, queue-based async architecture, tool calling via XML tags
- ✅ **Piper Voices (Mac Tonight):** Multi-voice TTS deployed — lazy one-at-a-time loading, 4 voices, configurable energy (length_scale), voice switching via WebSocket
- **Decisions Merged:** #34–#35 captured (Phi-4 pipeline, multi-voice TTS)
- **Tests:** All 423 passing, zero regressions
- **Next:** Voice models available via download script, ready for demo deployment
