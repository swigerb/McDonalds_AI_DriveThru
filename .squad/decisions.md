# Squad Decisions

## Active Decisions

### Performance Audit (2026-03-19)

#### 1. Optimize WebSocket Message Processing (Ronald — Lead)
- **Decision:** Skip JSON parsing on hot path using regex extraction for message type. Cache JSON serialization for order summaries. Pre-serialize static WebSocket messages at module import.
- **Impact:** Eliminates ~30 json.loads calls/sec per session on audio delta hot path (~95% of traffic). Removes redundant Pydantic serialization.
- **Trade-off:** Message routing depends on regex pattern validity (tested).

#### 2. Constrain Model Output Tokens (Ronald)
- **Decision:** Set `max_response_output_tokens = 150` in voice interactions.
- **Rationale:** Voice responses should be 1-2 sentences. Without a cap, model can generate long responses increasing latency and audio playback time.
- **Trade-off:** Very complex orders might be slightly truncated. Monitor and increase to 200 if needed.

#### 3. Reduce AI Search Over-Fetching (Ronald)
- **Decision:** Reduce KNN from 50→15, top results from 5→3 in Azure AI Search queries.
- **Rationale:** KNN=50 retrieves 50 matches for 5-result return (10x overfetch). For structured menu (~100 items), KNN=15 sufficient. Reduces token processing load.
- **Trade-off:** Edge cases with very ambiguous queries might miss a 4th/5th result. Acceptable for drive-thru context.

#### 4. Isolate Per-Connection WebSocket State (Grimace)
- **Decision:** Move `_tools_pending` from shared RTMiddleTier dict to local scope in `_forward_messages()`. Each connection gets its own tracking dict.
- **Rationale:** Concurrent WebSocket clients were interfering via shared state. Race condition eliminated.
- **Risk:** None identified.

#### 5. Fast-Path Audio Messages (Grimace)
- **Decision:** Define `_PASSTHROUGH_TYPES` frozenset (13 message types never modified). Return immediately after JSON parse, skip match/case logic.
- **Rationale:** During active speech, ~90% of messages are audio deltas. This optimization reduces per-message processing overhead.
- **Impact:** Single async task model still works; optimization is purely throughput.

#### 6. Implement Search Result Caching (Grimace)
- **Decision:** Add `_SearchCache` with 60s TTL, 128-entry max for Azure AI Search results.
- **Rationale:** Repeated menu queries are common in drive-thru (same item asked multiple times). Eliminates redundant Azure round-trips.
- **Risk:** Cache invalidation: if menu changes frequently, consider shorter TTL.

#### 7. Reduce Search Response Payload (Grimace)
- **Decision:** Cut `select_fields` from 11 to 5 fields in Azure Search queries.
- **Rationale:** Only 5 fields used in result formatting. Reduces network payload and Azure Search response time.
- **Trade-off:** None; filtering reduces bloat.

#### 8. Gzip Compression for HTTP Responses (Grimace)
- **Decision:** Add `_compression_middleware` for text-based responses (JSON, JS, HTML, SVG). Skip WebSocket and streaming.
- **Impact:** 60-70% reduction in HTTP payload for typical JSON responses.
- **Overhead:** Minimal (compression on-the-fly, cached for static assets).

#### 9. Reuse AudioContext Across Sessions (Birdie)
- **Decision:** Keep single AudioContext instance for player and recorder, reuse across recording/playback cycles.
- **Rationale:** AudioContext creation takes 50-100ms (OS-level audio device negotiation). Reuse eliminates this latency on every session start.
- **Risk:** Edge case where audio device is unplugged mid-session. Handled by graceful fallback (current error handling).

#### 10. Zero-Allocation Audio Capture Buffer (Birdie)
- **Decision:** Replace O(n²) buffer append pattern with pre-allocated doubling buffer and `copyWithin()`.
- **Rationale:** Old pattern created new Uint8Array on every chunk (~20-50x/sec), copying all accumulated data. Caused GC pressure and frame drops.
- **Impact:** Near-zero allocation during audio hot path.

#### 11. Memoize Leaf React Components (Birdie)
- **Decision:** Wrap `OrderSummary`, `TranscriptPanel`, `MenuPanel`, `StatusMessage`, `BrandHero`, `SessionTokenBanner` with React.memo.
- **Rationale:** These re-rendered on every parent state change even when props didn't change. MenuPanel and BrandHero are fully static.
- **Impact:** Transcript updates no longer trigger menu/hero re-renders. Surgical updates only.

#### 12. Remove Polling Timer in TranscriptPanel (Birdie)
- **Decision:** Remove `setInterval` that called `setCurrentTime(new Date())` every second.
- **Rationale:** This caused entire transcript panel to re-render every second, even when idle. Timestamp comparison now uses adjacent transcript entries.
- **Impact:** Eliminated ~1 re-render/second.

#### 13. Lazy-Load Settings Component (Birdie)
- **Decision:** Use `React.lazy()` + `Suspense` for Settings panel.
- **Rationale:** Settings rarely opened, includes Dialog/Sheet/Switch (~7.4 kB gzipped). No reason to load on initial page render.
- **Impact:** Faster initial page load.

#### 14. Strategic Vendor Chunking (Birdie)
- **Decision:** Replace per-package `manualChunks` with explicit groups: `react-vendor`, `ui-vendor`, `i18n`, `motion`.
- **Rationale:** Old pattern created hundreds of tiny files. Strategic grouping produces fewer, larger chunks with better caching and fewer HTTP requests.
- **Impact:** Improved cache hit rate, reduced network requests in production.

#### 15. Disable Sourcemaps in Production (Birdie)
- **Decision:** Set `sourcemap: false` in Vite build config for production builds.
- **Rationale:** Sourcemaps expose source code and increase artifact size. Not needed in production.
- **Impact:** Smaller deploy artifacts.

#### 16. Exponential Backoff for WebSocket Reconnection (Birdie)
- **Decision:** Implement exponential backoff (1s base, 30s cap) with random jitter for reconnection attempts.
- **Rationale:** Default instant-retry can overwhelm server during outages (thundering herd). Backoff with jitter distributes reconnection load.
- **Impact:** More resilient connection recovery, server-friendly.

#### 17. Gunicorn Configuration for WebSocket (Mayor McCheese)
- **Decision:** 2 async workers, 120s timeout, 65s keep-alive.
- **Rationale:** Async handles many concurrent connections. 120s timeout protects long-lived WebSocket sessions. 65s keep-alive matches Azure LB 60s idle timeout.
- **Risk:** Worker count should be monitored against memory usage (Azure SDK overhead).

#### 18. Container Apps Auto-Scaling (Mayor McCheese)
- **Decision:** HTTP scaling at 20 concurrent requests per replica, max 5 replicas, min 1.
- **Rationale:** Each replica runs 2 async workers. 20 concurrent/replica is conservative starting point. Min 1 prevents cold-start.
- **Risk:** Threshold should be validated with real WebSocket load testing.

#### 19. Health Probes: Startup + Liveness + Readiness (Mayor McCheese)
- **Decision:** Dedicated `/health` endpoint with generous startup probe (50s budget), liveness every 30s, readiness every 10s.
- **Rationale:** Startup probe allows gunicorn + pip deps to initialize. Liveness detects hung workers. Readiness gates traffic routing.
- **Trade-off:** 50s startup budget is conservative but acceptable for one-time initialization.

#### 20. Docker Layer Caching (Mayor McCheese)
- **Decision:** Copy dependency files (package.json, requirements.txt) first, install, then copy source code.
- **Impact:** Dependency layer cached across code-only changes (saves 60-90s per rebuild).
- **Trade-off:** None; pure efficiency.

#### 21. Configurable Log Level (Mayor McCheese)
- **Decision:** `LOG_LEVEL` env var controls logging (defaults to INFO). Can be set to DEBUG without code redeploy.
- **Rationale:** Enables troubleshooting by restarting container with new env var, avoiding full redeployment.
- **Impact:** Faster debugging cycle.

#### 22. Performance Test Harness (Hamburglar)
- **Decision:** Implement 28 tests covering latency (<5ms order_state ops, <10ms search, <2ms JSON), memory (<1MB delta, <2MB peak), thread safety, and production readiness.
- **Rationale:** Quantifiable baseline for future optimization. Thresholds have ~10× headroom for operational margin. All Azure calls mocked (zero external dependencies).
- **Impact:** Regression protection, team confidence in production readiness.
- **Trade-off:** Thresholds can be tightened post-optimization if needed.

#### 23. Audio Feedback Loop Prevention (Birdie)
- **Decision:** Multi-layered approach: VAD threshold `0.6` → `0.8`, silence duration `400ms` → `500ms`, auto gain control disabled, recorder worklet isolation via gain node, mic muting during AI playback.
- **Rationale:** AI speech output was being captured by microphone, creating feedback loop. Higher VAD threshold + longer silence buffer reject echo artifacts. AGC disable prevents amplification of speaker output. Gain node isolates recorder while preserving echo cancellation. Active muting while AI speaks blocks feedback path entirely.
- **Files:** `useRealtime.tsx`, `useAudioRecorder.tsx`, `recorder.ts`, `App.tsx`
- **Constraints:** Barge-in capability preserved (user can still interrupt with loud speech via server VAD). No permission re-prompts (mic stream kept alive, muted via gain node). Server-side VAD maintained.
- **Impact:** Eliminates infinite self-response loop. Maintains natural conversation flow.

#### 24. Echo Suppression Code Review (Ronald — Reviewer, 2026-03-20)
- **Decision:** APPROVE — Defense-in-depth echo suppression is correct and well-coordinated
- **Architecture:** Two independent layers (frontend gain-node muting + backend gating via ai_speaking flag + 300ms cooldown + buffer clear) complement each other. If one layer fails silently, the other still works.
- **Key Validations:**
  - Double `input_audio_buffer.clear` (frontend on `response.created`, backend on `response.audio.done`) is idempotent — clearing an empty buffer is a no-op
  - Per-connection state isolation correct — `ai_speaking` and `cooldown_end` are local variables, no shared mutable state
  - 300ms cooldown appropriate for typical 50-200ms speaker-to-mic latency with headroom
  - Early muting at `response.created` prevents echo path before first audio delta arrives
  - Barge-in preserved — both frontend and backend reset suppression immediately on `speech_started`
  - No race conditions — asyncio single-threaded, all state checks atomic within event loop tick
- **Code Quality:** Grimace's backend clean and well-commented; Birdie's frontend integration clean; substring-marker approach (`_MARKER_*`) is faster than JSON parsing on hot path
- **Minor Observation:** `response.created` mutes briefly for tool-call-only responses (harmless, not worth complexity to distinguish)
- **Impact:** Eliminates phantom transcriptions from audio feedback loop. Barge-in ~300ms latency acceptable for drive-thru UX.

#### 25. Demo Readiness Audit (Mac Tonight — Auditor, 2026-03-21)
- **Decision:** System Prompt Refactor + VAD Tuning
- **Changes Made:**
  1. **System Prompt Format** (app.py:127-157): Dense paragraph → bulleted structure with named sections (VOICE STYLE, MENU & PRICING, ORDERING, CLOSING, BOUNDARIES). Added ALL CAPS emphasis on critical instructions (NEVER, ALWAYS, ONLY, CORRECT, FULL, TOTAL). Implemented phrase variety rules — "NEVER use the same phrase twice in a row" with examples: "Awesome choice!", "You got it!", "Great pick!", "Nice!", "Coming right up!". Added explicit grounding: "ONLY recommend items found in search results — do NOT invent menu items"
  2. **VAD Threshold** (useRealtime.tsx:165): 0.8 → 0.7. Rationale: Multi-layered echo suppression (server-side in rtmt.py + client-side mic muting) now handles echo properly. 0.7 is more forgiving for natural speech while still rejecting ambient noise.
  3. **Prefix Padding** (useRealtime.tsx:166): 200ms → 300ms. Rationale: 200ms risks clipping word starts (plosives like "burger", "please", "fries"). In demo context, clipped words cause AI to ask for repetition — embarrassing.
- **Validations:**
  - Temperature 0.6: ✅ Optimal for menu ordering (deterministic tool calling, natural variance)
  - Max tokens 250: ✅ Sufficient for full order recap + closing phrase (tested; perf Decision #2's 150 was too aggressive)
  - Voice "coral": ✅ Warm, friendly female — excellent McDonald's crew member persona
  - Echo suppression: ✅ Well-architected, defense-in-depth validated
- **Demo Risk Assessment:**
  1. Risk: AI invents menu items (HIGH severity) — Mitigation: Added explicit grounding rule. Recommend pre-demo test with 5-10 orders covering all categories.
  2. Risk: Response truncation (MEDIUM severity) — Mitigation: max_tokens 250 + explicit "complete your full sentence" instruction. Recommend testing 4-5 item order.
  3. Risk: WebSocket disconnect (MEDIUM severity) — Mitigation: Exponential backoff with jitter (Decision #16). Recommend testing on exact demo network, pre-warming connection, backup browser tab.
- **Impact:** Ensures flawless voice ordering experience for McDonald's executive demo. All critical voice path components audited and optimized.

#### 26. ToolResultDirection.TO_BOTH for Order Updates (Grimace — Backend Dev, 2026-03-21)
- **Decision:** Changed successful `update_order` tool results from `TO_CLIENT` to `TO_BOTH` routing.
- **Problem:** `TO_CLIENT` sent order summary to frontend UI but empty string to OpenAI model. AI had no confirmation order succeeded, causing dead silence after valid orders (including exactly 10 items at per-item limit).
- **Solution:** Added `ToolResultDirection.TO_BOTH = 3` to enum. Now sends order summary JSON to both:
  - **OpenAI server** — AI knows item was added, continues with "anything else?"
  - **Frontend client** — UI updates with current order
- **Implementation:**
  - `rtmt.py`: New enum value + updated routing conditions
  - `tools.py`: Success path `TO_CLIENT` → `TO_BOTH`
  - Error/limit responses remain `TO_SERVER` (AI relays the message)
- **Test Coverage:** All existing tests updated, 14 new quantity-limit tests added (111 total, all passing)
- **Impact:** Eliminates conversation-killing bug. Multi-item orders now flow naturally through all quantities up to limit (10 per item, 25 total).

#### 27. System Prompt Upgrade — Upselling & ACV for McDonald's Demo (Mac Tonight — AI Expert, 2026-03-22)
- **Decision:** Added 4 new system prompt sections and updated 2 existing to drive revenue through suggestive selling while maintaining authentic McDonald's brand voice.
- **New Sections:**
  1. **CONVERSATIONAL FLOW** — No filler words at response start (reduces perceived latency); immediate pivot on barge-in interrupts
  2. **BRAND IDENTITY** — Fries mentioned FIRST whenever sides offered (McDonald's signature item)
  3. **SUGGESTIVE SELLING** — Three-tier upsell strategy:
     - Combo conversion: burger/sandwich alone → combo with Fries + drink
     - Upsize: Small/Medium → Large with price difference
     - McDonald's McFlurry suggestion when order has no dessert
  4. **TECHNICAL GUARDRAILS** — Currency spoken naturally ("six forty-nine", never "6.49"); long orders grouped not enumerated
- **Updated Sections:**
  - **ORDERING** — Added combo-check directive before moving to next item
  - **CLOSING AN ORDER** — Added item grouping rule for long orders
- **Rationale:** User directive for demo to show revenue-driving AI; McDonald's execs evaluate ACV impact; fries-first branding signals deep brand knowledge; no-filler-words cuts perceived latency by ~200-300ms per response
- **Trade-offs:** Prompt length increased ~40% (still optimal range for gpt-realtime-1.5); upselling adds one extra turn/item (mitigated by "ONE suggestion at a time, NEVER pushy" guardrail)
- **Risks:** Over-aggressive upselling → robotic feel (mitigated by variety rules + "NATURAL" emphasis); price mismatch on upsize (model should skip gracefully if search misses)
- **Validation Recommended:** Test 3-4 complete orders for combo triggers; confirm fries-first when "side"/"fries" mentioned; verify natural currency speech; test 5+ item order for grouping in recap
- **Impact:** Demo-ready system prompt ensuring revenue impact while maintaining conversational authenticity.

#### 28. User Directive: Demo Requirements (Brian Swiger via Copilot, 2026-03-21)
- **Request:** System prompt must drive higher Average Check Value (ACV) with suggestive selling (combo conversion, fries-first branding, treat suggestions), handle barge-in gracefully, avoid filler words, format currency as spoken words, and group long orders.
- **Context:** Executive demo requirements for McDonald's presentation. Critical for successful pitch.
- **Implementation:** Addressed by Decisions #26 (TO_BOTH routing for conversation flow) and #27 (suggestive selling + technical guardrails).
- **Impact:** Enables demo to showcase both conversational quality (no dead silence) and revenue impact (ACV-driving prompts).

#### 29. Prompt Externalization to YAML (Mac Tonight — AI Expert, 2026-03-25)
- **Decision:** Externalize all system prompt content and voice AI configuration from hardcoded Python into 6 structured YAML files under `app/backend/prompts/mcdonalds/`.
- **Files Created:**
  1. `manifest.yaml` — Brand metadata, file registry, model configuration
  2. `system_prompt.yaml` — 22 prioritized behavioral sections (exact content from app.py lines 127-273)
  3. `greeting.yaml` — Standardized greeting event configuration
  4. `tool_schemas.yaml` — 4 tool definitions with McDonald's-specific descriptions
  5. `error_messages.yaml` — 12 error templates with Jinja2 variable support
  6. `hints.yaml` — Upsell hints, system hints, delta templates
- **Rationale:** Hardcoded prompts blocked brand customization, prevented multi-brand support, and mixed brand voice with application logic.
- **Architecture:** Matches proven Sonic AI Drive-Thru reference implementation.
- **Impact:** Enables brand switching without code changes. Prompt tuning becomes a YAML edit, not a code deploy.
- **Trade-off:** Requires loader infrastructure (implemented by Grimace) to parse and apply YAML at runtime.

#### 30. Prompt Loader & Config Architecture (Grimace — Backend Dev, 2026-03-25)
- **Decision:** Implement brand-parameterized YAML loaders and centralized config management. Wire integration across all backend modules.
- **New Components:**
  1. `prompt_loader.py` — Discovers prompts from `prompts/{brand}/`, loads and validates YAML. DEV_MODE hot-reload for rapid iteration.
  2. `config_loader.py` — Singleton that reads `config.yaml` once, caches, validates required sections.
  3. `config.yaml` — Centralized configuration covering: model settings, VAD thresholds, business rules (tax, discounts), cache TTL, audio processing, search parameters, connection timeouts, compression, logging levels.
  4. `menu_utils.py` — Canonical size mappings (Small, Medium, Large) and category inference keywords for McDonald's menu.
- **Integration Points:**
  - `app.py` — Uses config for compression/connection settings. Loads PromptLoader with graceful fallback.
  - `rtmt.py` — Config-driven echo cooldown, WebSocket heartbeat, verbose truncation. Greeting loaded from YAML.
  - `tools.py` — Config-driven cache TTL, search KNN/top values, quantity limits. Error messages and upsell hints from loader.
  - `order_state.py` — Fixed timezone bug (`datetime.now()` → `datetime.now(ZoneInfo(...))`). Config-driven tax rate, happy hour window, discount factor.
- **Dependencies:** Added `PyYAML>=6,<7` and `Jinja2>=3,<4`.
- **Key Design:** Fallback everywhere. Every config/prompt access has hardcoded default so app runs without YAML (critical for tests/minimal deploys).
- **Impact:** Complete prompt/config infrastructure deployed. Ready for multi-brand expansion.
- **Files Changed:** 22 total (+1623/-201 lines)

#### 31. Prompt Externalization Test Strategy (Hamburglar — Tester, 2026-03-25)
- **Decision:** Create comprehensive test suite for prompt externalization covering PromptLoader, ConfigLoader, menu_utils, and app integration before modules land in production.
- **Test Coverage:** 47 new tests across 4 files:
  1. `test_prompt_loader.py` — 14 tests (init, system prompt content, greeting, tools, errors, hints, templates, error paths)
  2. `test_config_loader.py` — 11 tests (loading, sections, values, types, reload)
  3. `test_menu_utils.py` — 13 tests (size normalization, category inference, edge cases)
  4. `test_app.py` — 9 new integration tests
- **Key Design Choices:**
  - `pytest.importorskip` — Tests skip gracefully when modules don't exist yet, enabling parallel development.
  - Content assertions — System prompt tests verify McDonald's brand terms (not just "non-empty"), catching branding regressions.
  - Jinja2 render tests — Error templates tested with actual variable substitution (e.g., `render_error("invalid_mod", forbidden_item="mustard", base_name="McFlurry")`).
  - Malformed YAML test — Creates/cleans up temporary brand dir to test error path without polluting prompts/.
- **Results:** 202 total tests (47 new + 155 existing), all passing. Zero regressions.
- **Impact:** Quality gate fully operational. Matches Sonic reference architecture.

#### 32. Processor Abstraction Layer for Offline Mode (Grimace — Backend Dev, 2026-04-02)
- **Decision:** Create abstract processor interface supporting both cloud (Azure OpenAI) and local (Phi-4) inference.
- **Components:**
  - `processor_base.py` — ProcessorBase abstract class defining unified interface for `process_audio()`, `get_status()`, etc.
  - `local_processor.py` — LocalProcessor stub implementing interface for offline pipeline
  - `processor_router.py` — Router selects cloud or local based on config
  - `app.py` — Integrated ProcessorRouter into FastAPI pipeline
- **Key Design:** Cloud path unchanged; local mode is additive. Router toggles path without touching core inference logic.
- **Impact:** Clean separation enabling independent cloud/local optimization. Foundation for multi-processor strategy.

#### 33. Local Mode Config Integration Pattern (Grimace — Backend Dev, 2026-04-02)
- **Decision:** Environment variables override config.yaml in three-tier priority chain: `ENV VARS > config.yaml > built-in defaults`.
- **Three Env Vars:**
  - `LOCAL_MODE_ENABLED` → boolean override for `local_mode.enabled`
  - `LOCAL_MODE_MODEL_PATH` → string override for `local_mode.model_path`
  - `LOCAL_MODE_DEVICE` → string override for `local_mode.device`
- **Rationale:** Operators need to toggle local mode without editing files (Docker/Azure deployments). Three-tier fallback ensures graceful startup even with no `local_mode` section in config.yaml.
- **Validation:** `/health` endpoint returns `local_mode.available` and `local_mode.device`. Startup logs clearly report model path/size, TTS readiness, GPU detection.
- **Impact:** Operational flexibility while maintaining cloud-first default behavior.

#### 34. Phi-4 Local Pipeline Architecture (Mac Tonight — AI Expert, 2026-04-02)
- **Decision:** Full offline AI inference pipeline: ONNX Phi-4 multimodal → queue-based async streaming → Piper TTS synthesis.
- **Key Choices:**
  1. **Queue-based async streaming** — Bridge sync ONNX to async via executor thread + asyncio.Queue for true streaming
  2. **Energy-based VAD** — RMS thresholding instead of silero-vad (avoids ~200MB model)
  3. **Linear interpolation resampling** — numpy `np.interp` instead of scipy (avoids ~150MB dependency)
  4. **Lazy model loading** — Load on first WebSocket connection, not startup (saves RAM for cloud-only deploys)
  5. **XML-tagged tool calls** — `<tool_call>` tags instead of JSON (more robust for Phi-4)
- **Files:** `phi4_model.py`, `piper_tts.py`, rewritten `local_processor.py`
- **Rationale:** Minimize dependencies, enable CPU-based inference, stream tokens for real-time feel
- **Impact:** 560 tests passing (137 new), zero regressions. Full offline capability ready.

#### 35. Multi-Voice Piper TTS with One-at-a-Time Loading (Mac Tonight — AI Expert, 2026-04-02)
- **Decision:** Lazy one-at-a-time voice loading — only active voice in memory (~60MB per voice, not ~240MB for all 4).
- **Four Voices:**
  - `en_US-amy-medium` (default) — Friendly & Conversational
  - `en_GB-jenny_dioco-medium` — Expressive & Upbeat
  - `en_US-lessac-medium` — Warm & Professional
  - `en_US-kristin-medium` — Neutral & Clear
- **Key Features:**
  - `length_scale=0.9` default for drive-thru energy
  - `set_voice()` unloads previous before loading new
  - Pre-flight file check prevents broken state
  - `extension.set_piper_voice` WebSocket message for runtime switching
  - `/api/local-mode/voices` endpoint returns live voice metadata
- **Backward Compatibility:** `model_name` kwarg still accepted; config loader falls back from `tts_default_voice` to `tts_model`
- **Impact:** Users can select voices in UI; memory footprint scales with active voice only.

#### 36. Local Mode UI Architecture (Birdie — Frontend Dev, 2026-04-02)
- **Decision:** React Context Provider for offline mode state, following existing `menu-mode-context.tsx` pattern.
- **Components:**
  - `LocalModeProvider` with localStorage key `localMode` (default: `false`)
  - Settings toggle with status check (`/api/local-mode/status` endpoint)
  - Visual indicators: yellow dot on mic button, "🔌 Local" / "☁️ Cloud" pill badge
  - WebSocket message: `{ type: "extension.set_local_mode", enabled: boolean }`
- **Status Display:** Loading → Ready → Unavailable states based on `/api/local-mode/status` response
- **Impact:** Clean toggle for users; backend completely decoupled from frontend UI state.

#### 37. Piper Voice Selection UI (Birdie — Frontend Dev, 2026-04-02)
- **Decision:** Conditional Piper voice dropdown when local mode is active (not disabled/grayed-out).
- **Key Design:**
  - Separate localStorage key `piperVoice` (independent from cloud `voiceChoice`)
  - Conditional render: `{!localMode && AzureVoiceSelector} {localMode && PiperVoiceSelector}`
  - Same `<select>` styling as Azure selector for visual consistency
  - WebSocket message: `{ type: "extension.set_piper_voice", voice: string }`
- **Voice Labels:** Amy, Jenny, Lessac, Kristin with descriptions
- **Impact:** Users seamlessly switch between cloud/local voice systems with independent state.

#### 38. Local Model Asset Management Strategy (Mayor McCheese — DevOps, 2026-04-02)
- **Decision:** On-demand model downloading via scripts (not Git LFS or baked into Docker).
- **Components:**
  - `scripts/download_local_models.py` — Python downloader with skip-if-exists, progress tracking
  - `scripts/download_local_models.ps1` — PowerShell wrapper for Windows dev
  - `docker-compose.local.yml` — dev-only with NVIDIA GPU reservation
  - `.gitignore` — added `models/` directory
  - `requirements.txt` — added `onnxruntime-genai`, `piper-tts`
  - `config.yaml` — added `local_mode` section (disabled by default)
  - `.env-sample` — added `LOCAL_MODE_*` variables
- **Key Choices:**
  1. Download script over Git LFS (model size + infrequent changes)
  2. Config-driven local mode (disabled by default)
  3. GPU opt-in via separate CUDA/DirectML install
  4. Separate docker-compose (dev-only, not production Dockerfile)
  5. No production Dockerfile changes (cloud deployment zero-impact)
- **Piper Voices:** `--voices` flag downloads all 4 Piper voice models
- **Impact:** Cloud deployment unaffected. Dev setup automated with download script.

#### 39. Offline Mode Test Patterns (Hamburglar — Tester, 2026-04-02)
- **Decision:** Comprehensive test suite for offline modules without requiring ONNX Runtime or Piper TTS installed.
- **Test Files Created:**
  - `test_processor_router.py` — 23 tests for route selection, cloud/local toggling
  - `test_phi4_model.py` — 31 tests for token streaming, memory management
  - `test_piper_tts.py` — 42 tests for voice switching, lazy loading, RAM management
  - `test_local_processor.py` — 41 tests for full pipeline, WebSocket integration
- **Key Decisions:**
  1. Custom `_AsyncIter` adapter for WebSocket async iteration (standard MagicMock.__aiter__ doesn't work)
  2. Mock at module boundaries (patch `local_processor.LOCAL_MODE_AVAILABLE` directly)
  3. No conftest.py (consistent with existing self-contained test structure)
- **Results:** 137 new tests, 560 total passing, zero regressions, zero external dependencies in CI
- **Impact:** Full test coverage for offline mode without blocking on GPU/model availability.

#### 40. Offline Mode Documentation (Ronald — Documentation, 2026-04-02)
- **Decision:** Add comprehensive offline mode section to README covering what, setup, features, config, troubleshooting.
- **Content:** 80+ lines across 6 sections:
  1. "What is Local Mode?" — Phi-4 ONNX + Piper TTS
  2. "Prerequisites" — CPU baseline, GPU recommended
  3. "Setup" — Model download, env vars, docker-compose local
  4. "Features" — Offline inference, voice selection, no Azure deps
  5. "Configuration" — Config.yaml overrides, env var precedence, device selection
  6. "Troubleshooting" — Missing models, OOM, device detection
- **Links:** Piper voices (huggingface.co/rhasspy/piper-voices)
- **Context:** Azure Local compatibility for uninterrupted AI drive-thru
- **Impact:** Clear documentation enabling users to understand and deploy offline mode.

#### 41. User Directives: Offline Mode & Piper Voices (Brian Swiger via Copilot, 2026-04-02)
- **Directive 1 (2026-04-02T15:45:00Z):** 4 Piper TTS voices with dropdown in settings panel. Voice models from huggingface.co/rhasspy/piper-voices. `length_scale 0.9-0.95` for drive-thru energy.
- **Directive 2 (2026-04-02T16:00:00Z):** README documentation for offline mode: ONNX Runtime + Phi-4-multimodal, Piper TTS, online/offline toggle, CPU support with GPU/NPU recommended, Azure Local compatibility.
- **Implementation:** All directives addressed by Decisions #34–40. Piper voices deployed with configurable energy tuning; README expanded with offline mode content.
- **Impact:** User requirements captured for offline mode delivery and documentation.

#### 42. Faster-Whisper STT Engine for Offline Mode (Mac Tonight — AI Expert, 2026-04-02)
- **Decision:** Added `whisper_stt.py` with `WhisperSTTEngine` using Faster-Whisper (CTranslate2) for local speech-to-text. Integrated into `local_processor.py` to run **in parallel** with Phi-4 inference.
- **Key Design Choices:**
  1. **CUDA-only GPU** — Faster-Whisper uses CTranslate2 which supports CUDA but not DirectML. Falls back to CPU with int8 quantization.
  2. **Parallel execution** — `asyncio.create_task` launches Whisper transcription before Phi-4 starts; both use `run_in_executor` on separate threads. Zero added latency to the response pipeline.
  3. **`small` model default** (244 MB) — best accuracy/speed tradeoff for drive-thru. Configurable via `stt_model` in config.yaml.
  4. **0.5s minimum audio guard** — prevents Whisper hallucination on silence/noise fragments.
  5. **Graceful degradation** — if Faster-Whisper isn't installed, AI responses still work; only customer transcription is missing.
- **Files Changed:** `app/backend/whisper_stt.py` (new), `app/backend/local_processor.py` (integration), `app/backend/config.yaml` (keys), `app/backend/config_loader.py` (defaults)
- **Risk:** Whisper `small` on CPU may add ~1-2s latency per utterance; acceptable since it runs in parallel with Phi-4 and doesn't block the response.
- **Impact:** Guest Conversation panel displays customer speech in offline mode. 629+ tests passing.

#### 43. Whisper Dependencies & Environment Config (Mayor McCheese — Build, 2026-04-02)
- **Decision:** Updated requirements.txt with `faster-whisper` and `pydub` dependencies. Updated config.yaml with `stt_model`, `stt_device`, `stt_compute_type` keys. Updated .env-sample with examples. Created model download utility in `download_local_models.py`.
- **Configuration Keys:**
  - `stt_model: small` — model size/variant
  - `stt_device: auto` — GPU/CPU detection
  - `stt_compute_type: auto` — int8 fallback for CPU
- **Impact:** Operators can configure STT behavior without code changes. Models pre-downloadable for air-gapped deployments.

#### 44. Offline Menu Search Implementation (Grimace — Backend Dev, 2026-04-02)
- **Decision:** Created in-memory keyword search engine (`local_search.py`) for 134-item offline McDonald's menu. Replaces Azure AI Search dependency for local mode.
- **Key Features:**
  1. Full parity with Azure dataset (134 items, all fields)
  2. Keyword matching (item name, description, category)
  3. Quantity and price tracking
  4. Zero external dependencies (no Azure credentials required)
- **Files:** `app/backend/local_search.py`, `models/offline_menu.json`
- **Impact:** Full menu search works without Azure; enables truly offline operation. 70 new tests added.

#### 45. Whisper STT Test Suite (Hamburglar — QA, 2026-04-02)
- **Decision:** Created comprehensive test suite for Whisper STT engine. Test file: `tests/test_whisper_stt.py` (37 tests). Extended `test_local_processor.py` with 6 integration tests.
- **Coverage:** Audio processing, model loading, error handling, config integration, parallel execution, graceful degradation.
- **Results:** 671+ total tests passing, zero regressions.
- **Impact:** Quality gate ensures STT reliability in offline mode.

#### 46. Offline Mode Documentation — Whisper & Search (Ronald — Documentation, 2026-04-02)
- **Decision:** Updated README.md with Faster-Whisper documentation and offline search capabilities.
- **Content:** STT feature guide, model configuration, device selection, performance notes, offline search explanation.
- **Impact:** Clear documentation for users implementing offline speech transcription and menu search.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
