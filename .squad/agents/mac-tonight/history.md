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

### 2026-07-17 — Faster-Whisper STT for Offline Customer Transcription
- Created `app/backend/whisper_stt.py` — WhisperSTTEngine with CUDA→CPU auto-detection, lazy loading, executor-wrapped sync transcription, PCM int16→float32 normalization, 0.5s minimum audio guard
- Updated `app/backend/local_processor.py` — integrated STT as `_stt` component alongside Phi-4 and Piper; Whisper transcription runs in parallel with Phi-4 via `asyncio.create_task`; sends `conversation.item.input_audio_transcription.completed` WebSocket message for Guest Conversation panel; lifecycle managed in `_ensure_models_loaded` / `stop_background_tasks`
- Updated `app/backend/config.yaml` — added `stt_model`, `stt_device`, `stt_compute_type` to `local_mode` section
- Updated `app/backend/config_loader.py` — added STT defaults to `_LOCAL_MODE_DEFAULTS`
- All 629 existing tests pass; 1 pre-existing failure (unrelated `test_unknown_category_gets_generic_upsell`); zero regressions

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
- Faster-Whisper uses CTranslate2 which only supports CUDA (not DirectML) — GPU auto-detection tries CUDA first via torch or ctranslate2, falls back to CPU with int8 quantization
- Whisper transcription runs in parallel with Phi-4 inference via `asyncio.create_task` — neither blocks the other since both use `run_in_executor` on separate threads
- Audio shorter than 0.5s is skipped before Whisper transcription to prevent hallucination on silence/noise fragments
- Whisper `small` model (244 MB) is the default — best accuracy/speed tradeoff for drive-thru; `tiny` faster but less accurate, `medium`/`large-v3` too slow for real-time
- The `vad_filter=True` parameter in Whisper transcription uses Silero VAD internally to skip non-speech segments — separate from the energy-based VAD in local_processor

## Team Updates (2026-04-02T16:30Z)

### Offline Mode Phase Completion
- ✅ **Phase 2 (Mac Tonight):** Phi-4 pipeline complete — ONNX streaming inference, queue-based async architecture, tool calling via XML tags
- ✅ **Piper Voices (Mac Tonight):** Multi-voice TTS deployed — lazy one-at-a-time loading, 4 voices, configurable energy (length_scale), voice switching via WebSocket
- **Decisions Merged:** #34–#35 captured (Phi-4 pipeline, multi-voice TTS)
- **Tests:** All 423 passing, zero regressions
- **Next:** Voice models available via download script, ready for demo deployment

## Sonic parity — item 1: server-owned bootstrap session.update (2026-09-22)
- McD HAD the defect: upstream session was only configured when the browser sent session.update (mic press). A react-use-websocket auto-reconnect with the mic live ran on service defaults (no tools); once the model spoke, our later session.update carrying a voice was rejected wholesale (cannot_update_voice) → no tools for the whole conversation.
- Ported Sonic fe91eb5: `build_bootstrap_session_update()` is the first upstream frame; `_build_session(session, voice_locked)` is the single seam that overlays server config + translates to GA; `_strip_output_voice` drops audio.output.voice once assistant audio was seen.
- Replaced McD's old `session_configured` voice deferral (pre-session) with the voice-lock deferral: voice picker sends immediately (bootstrap already registered tools) unless assistant audio exists, then defers to next conversation.
- Greeting: no longer fired by the (now bootstrap) session.updated; triggered by the browser's session.update and waits up to 5s for session.updated (reconciles decisions.md "session.updated not reliable" fallback).
- Tests: tests/test_session_bootstrap.py (fake GA upstream harness, incl. McD's mic-press sequence set_voice→session.update). 2 obsolete deferral tests in test_rtmt.py updated to the new contract.

## Sonic parity — item 2: gpt-realtime-2.1 + reasoning.effort (2026-09-22)
- Template deploys `gpt-realtime-2.1` / `2026-07-07` / GlobalStandard (was 1.5). 1.5 stays a rollback via `AZURE_OPENAI_REALTIME_DEPLOYMENT`.
- `reasoning`/`parallel_tool_calls` now pass `_to_ga_session`, but `_build_session` adds them ONLY when `_reasoning_model()` (runtime rejection > explicit `model.reasoning_model` > deployment-name regex). Client-supplied values are always stripped — 1.5 rejects the whole update (tools included) if they appear.
- New `configure_realtime_model(rtmt, model_cfg, environ)` is the one seam app.py (and the item-5 smoke script) use: temperature/max tokens + `AZURE_OPENAI_REALTIME_{REASONING_EFFORT,REASONING_MODEL,TRANSCRIPTION_MODEL}` overrides of config.yaml.
- config.yaml: `reasoning_effort: low` (Sonic's 174-trial benchmark), `reasoning_model: auto`, `parallel_tool_calls: null`, `transcription_model: whisper-1` (server-owned, overrides the browser's value; gpt-4o-transcribe needs a deployment).
- Bicep: 3 optional params → env via `union()` so unset = config.yaml wins; wired through main.parameters.json + azure.yaml pipeline vars.

## Sonic parity — item 3: backend voice whitelist (2026-09-22)
- `rtmt.GA_REALTIME_VOICES` (10 voices) + `DEFAULT_VOICE="marin"` replace the inline 8-voice tuple in the extension.set_voice handler — marin/cedar were silently ignored before. Missing voice key now defaults to marin.
- Default marin in config.yaml, app.py fallback, main.parameters.json, .env-sample. `VoiceParityTests` pins all four + voices.ts together.

## Sonic parity — item 4: session.update rejection fallback (2026-09-22)
- Ported Sonic `_SessionUpdateGuard`: every upstream session.update (bootstrap `mcd_bootstrap_*`, voice picker `mcd_voice_*`, browser `mcd_su_*`) carries an event_id and is tracked; a correlated `invalid_request_error` (echoed event_id, or no event_id + session param/none while one of ours is in flight) triggers exactly ONE minimal fallback (`type, instructions, tools, tool_choice` only) per original. A rejected fallback is surfaced to the browser once — never loops.
- Rejection with no event_id/param (1.5 rejecting `reasoning`) sets `_reasoning_rejected` so every later update drops reasoning.
- Seam: the `"error"` case in `_process_message_to_client` (errors are not in `_PASSTHROUGH_SERVER_TYPES`, so they reach the parsed path); `guard` threaded through both `_process_message_to_*`; `on_session_updated()` on every ack.
- McD addition: `test_rejected_bootstrap_still_greets_with_tools` (mic-press greeting after a recovered bootstrap still has the 4 tools) and `test_rejected_voice_change_is_recovered_and_tools_kept`.

## Sonic parity — item 5: realtime smoke check + postdeploy hook (2026-09-22)
- `scripts/smoke_realtime.{py,ps1,sh}` ported from Sonic. McD adaptation: `build_middle_tier` calls the REAL `tools.attach_tools_rtmt` (dummy search endpoint, never called) and passes `prompt_loader` like app.py, instead of Sonic's re-derived schema map — the smoke payload can't drift from the app's.
- azure.yaml `postdeploy` hook: `interactive: false`, `continueOnError: true`; wrappers always `exit 0` (skip: `MCD_SKIP_REALTIME_SMOKE=true`). Uses the root `.venv` created by the postprovision hook.
- Live (read-only, shared cog-axgpampkq3yfa): 2.1 → bootstrap / relayed / fallback all `session.updated` with 4 tools, tool_choice=auto, reasoning low; whisper-1 transcription PASS. 1.5 rollback → all PASS, reasoning not sent.

## Sonic parity — item 6 backend: browser socket without permessage-deflate (2026-09-22)
- aiohttp 3.14.3 (pinned here too) rejects the first compressed frame after an initial PONG (aio-libs/aiohttp#13274) — exactly a browser socket idle past one heartbeat. `config.yaml connection.ws_compression: false` → `rtmt._WS_COMPRESS`, applied to both cloud `WebSocketResponse`s (main + busy rejection); upstream `ws_connect(compress=0)` (AOAI declines deflate anyway).
- `session_manager.IDLE_CLOSE_CODE=4000` (unchanged) + `IDLE_CLOSE_REASON="idle_timeout"` (was a prose message) — the frontend keys off 4000 to stop auto-reconnect.
- NOT changed (local mode out of scope, flagged): processor_router local sockets (L306/L376), its no-processor error socket (L357), and app.py ws_test_handler (L396) still use aiohttp's default compress=True.
- Tests: `tests/test_ws_transport.py` (7) — drives the real middle tier with Chromium-style framing (PONG then compressed frame).

## Sonic parity — brand spot-check, gpt-realtime-2.1 live (2026-09-22)
- Harness (scratch, not committed): real McD system prompt + real tools (live Azure AI Search `mcdonalds-menu-items`, real order_state), bootstrap session.update from rtmt, text user turns, first-audio latency measured from response.create to the first output_audio.delta (includes tool round-trips).
- Correctness (2 reps × 6 scenarios): low 12/12, none 11/12 — at none, "small fries → actually make that a large" once ADDED a large next to the small (low replaced it both times). Menu question ("What comes on a Big Mac?") is search-grounded; in 2 of ~6 low attempts the model said the index doesn't list ingredients (index content, not a reasoning issue).
- First audio: low median 969 ms / max 2266; none median 1000 ms / max 5391. No regression from low; keep `reasoning_effort: low`.
- Rate limit: the shared deployment (capacity 10, also serving Sonic prod) returned `response.done status=failed inference_rate_limit_exceeded` under back-to-back test conversations. McD passes this through silently — the guest hears nothing, and there's no retry or apology. Flagged, not changed. The spot-check was throttled (20 s gaps) and rate-limited runs were re-run.

- **Round 3 — gpt-realtime-2.1-dz (2026-09-23):** McDonald's and Dunkin now share the DataZoneStandard deployment `gpt-realtime-2.1-dz` on the shared account (Sonic keeps `gpt-realtime-2.1`). `reasoning_model: auto` already treats it as a reasoning model (the non-reasoning regex only matches the 1.x / dated / mini / 4o families); pinned with `test_data_zone_2_1_deployment_is_a_reasoning_model` (also asserts `gpt-realtime-1.5-dz` stays non-reasoning). 4/4 mutants killed. Docs note the deployment name is configurable.

- **Round 3 R2 — smoke transcription is verbatim, auth follows the resource tenant (2026-09-23, port of Dunkin c4249de):** `scripts/smoke_realtime.py` passed the transcription step on a keyword, so Sonic's "Sure, I can't place the order for you…" (the model *answering* the phrase) counted as a pass. Now: the TTS step puts the phrase in `response.create.response.instructions` (no user turn; neutral TTS session instructions), and `judge_transcript` requires difflib word-sequence similarity >= 0.85 to the phrase after lowercasing/stripping punctuation and accents (tolerates "Hey" for "Hi"; the answered transcript scores 0.08). Auth: token for the azd env's `AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` (`--tenant`/`--subscription`, CLI > env > azd, azd read even with explicit `--endpoint`), trying `az --subscription`, then tenant-pinned `azd` and `az` in turn and reporting every failure. Live: PASS on `gpt-realtime-2.1` and `gpt-realtime-2.1-dz` (both transcripts verbatim, similarity 1.00). Note: the mcd-demo azd env still says `AZURE_OPENAI_REALTIME_DEPLOYMENT=gpt-realtime-2.1`, not -dz.

- **Round 3 R1 — rate-limit recovery (2026-09-23):** The silent `inference_rate_limit_exceeded` from the Sonic-parity spot-check is now handled in `app/backend/rate_limit.py` (`RateLimitRecovery`, one per connection in `_forward_messages`). Detection: an `error` whose code/type contains `rate_limit`, or a `response.done` with `status: failed` whose `status_details.error` does. Ladder: 1st hit → silent `response.create` after 1.5 s (or the "try again in N s" hint clamped 0.5-5 s); 2nd → `extension.rate_limited {attempt}` to the browser + retry after 4 s (hint clamped 2-8 s); after `max_retries` 2 → `{final: true}` and stop. `speech_started` or a `response.created` we did not send cancels and resets; a signal while a retry is pending does not stack; a failed response.done right after the tool follow-up `response.create` is not retried twice. `_SessionUpdateGuard.correlate` no longer claims uncorrelated rate-limit errors, so session.update rate limits keep the minimal-update fallback and turn rate limits reach the ladder. Config `resilience.rate_limit` + `RATE_LIMIT_RECOVERY_ENABLED`. Tests `tests/test_rate_limit_recovery.py` (25, scripted GA fake + injected sleep). `tools.search` try/except scope left as is (R1 didn't touch it).
- **Round 3 R1 clips (2026-09-23):** `scripts/generate_apology_clips.py` synthesises each locale's phrase on the live model (phrase in `response.create` instructions, voice marin, 24 kHz PCM16) and reads it back through whisper-1. Generated on `gpt-realtime-2.1-dz`: en 3.30 s / es 2.35 s / fr 2.40 s / ja 2.90 s, all four read back verbatim. Guard `tests/test_apology_clips.py` (clip per UI locale, format, 1-4 s, not silent, generator/frontend/locales agree).
