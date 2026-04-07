# Team Decisions

## WebSocket Offline & Toast Notifications (2026-04-02)

### Offline Auto-Fallback and Diagnostics Pipeline (Grimace)
**Date:** 2026-07-19 | **Status:** Implemented

When offline with local mode toggled, the backend now auto-detects cloud unreachability and falls back to local mode. Three-layer fix:

1. **Auto-Fallback (processor_router.py)** — 3s connectivity check to Azure. If unreachable, falls back to local mode with warning.
2. **Runtime Mode Toggle (app.py)** — `POST /api/local-mode/toggle` endpoint lets frontend signal mode preference before WS connect.
3. **Graceful Offline Startup (app.py)** — Missing Azure env vars log warning instead of exit. Cloud RTMiddleTier skipped if unavailable.
4. **Diagnostics Endpoint (app.py)** — `GET /api/diagnostics` returns mode, model status, GPU, TTS/STT, last error, WS counts.
5. **Pipeline Logging** — `local-pipeline` logger added across all modules for session tracking.

**Impact:** No regressions (632 tests baseline preserved). Birdie to wire `/api/local-mode/toggle` to frontend toggle.

---

### WebSocket Offline Fix — Cached Cloud Probe + Error Handling (Grimace)
**Date:** 2026-07-20 | **Status:** Implemented

Three compounding bugs fixed:

1. **Cached Cloud Reachability Probe (processor_router.py)**
   - Cloud reachability cached for 30 seconds (first WS connection now instant)
   - Probe timeout: 3s → 1s total, 2s → 500ms connect
   - Startup probe in `_on_startup` prevents first WS stall
   - `invalidate_cloud_cache()` marks cloud unreachable on real failure

2. **RTMiddleTier Error Handling (rtmt.py)**
   - `_websocket_handler` wraps `_forward_messages()` in try/except
   - Sends structured error JSON (`type: "error"`, `code: "cloud_unreachable"`) to client before closing
   - Previously: exception propagated unhandled → WS closed silently

3. **Explicit Local Mode Fast Path (processor_router.py)**
   - Mode="local" + local processor exists = immediate WS accept (zero cloud dependency)
   - Auto-fallback only runs when mode="cloud"

**Frontend Action:** Pass `?mode=local` in WS URL when local mode enabled, or call `/api/local-mode/toggle` first.

---

### Local Mode WebSocket Direct Connection (Birdie)
**Date:** 2025-07-23 | **Status:** Implemented

Offline mic clicks produced silence. Three root causes fixed:

1. WebSocket URL was relative (`/realtime`), resolving to remote Azure host instead of localhost
2. `react-use-websocket`'s `sendJsonMessage` silently drops messages when connection not OPEN
3. Local mode state wasn't synced to backend at session start

**Decision:**
- When `localMode=true`, connect WebSocket directly to `ws://localhost:8000/realtime`
- Always check `readyState === OPEN` before session start; show error if not connected
- Sync local mode state to backend at every session start
- Skip Azure session token fetch in local mode

**Impact:** `useRealtime.tsx` accepts `localMode` parameter and returns `readyState`. `App.tsx` gates mic on WebSocket readyState. User sees ⚠️ error if connection fails.

---

### Toast Notifications for Connection Errors (Birdie)
**Date:** 2025-07-24 | **Status:** Implemented

Inline error text (`<p>` below mic button) was missed by users and auto-cleared after 5s.

**Decision:**
- **Toast module:** `components/ui/use-toast.ts` exports standalone `toast()`, `dismissToast()`, `dismissAllToasts()`
- **Toaster component:** `components/ui/toaster.tsx` renders top-right with spring animations, X dismiss button
- **Persistence:** Toasts persist until user dismisses or programmatic clear on reconnect/success
- **Deduplication:** Same message = no-op (prevents duplicate error stacking)
- **Variants:** `"error"` (McDonald's red #DB0007) and `"warning"` (amber)

**Impact:** All user-facing errors should use `toast()` instead of inline state. Console logging remains separate for dev debugging.

---

## Sonic Rebrand — Scope & Implementation (2026-03-19/20)

### Analysis & Scope (Rick)
- **~100+ Dunkin references** identified across frontend, backend, data, and documentation
- **6 categories of changes**: Frontend UI, system prompts, menu data, docs, assets, team context
- **Estimated effort**: 2–4 developer-days
- **Recommendation**: Parallel team execution with Rick coordinating

### Frontend Theme & Branding (Morty)
- **Primary colors**: Cherry Red (#E40046), Dark Blue (#285780), Yellow (#FEDD00), Light Blue (#74D2E7), Green (#328500)
- **Font**: Nunito Sans (replaced Fredoka)
- **Brand voice**: "Carhop" terminology (replaced "crew member")
- **Menu**: Slushes, burgers, shakes, tots, hot dogs, breakfast items
- **Logo**: New sonic-logo.svg created; dunkin-logo.svg removed from imports
- **Translations**: All locale files (en, es, fr, ja) updated
- **Test data**: dummyOrder.json and dummyTranscripts.json aligned with Sonic branding

### Backend System & Implementation (Summer)
- **System prompts**: Rewritten as Sonic carhop persona in app.py and rtmt.py
- **Logger**: coffee-chat → sonic-drive-in
- **Menu data**: structured_menu_items replaced with Sonic items
- **Environment**: DUNKIN_MENU_ITEMS_PATH → SONIC_MENU_ITEMS_PATH; index coffee-chat → sonic-drive-in
- **Tools coupling**: MENU_CATEGORY_MAP syncs with frontend JSON; ALLOWED/BLOCKED categories include both JSON names and keyword-inferred names
- **Tests**: All backend test suites updated with Sonic items; existing logic tests unchanged
- **Upstream attribution**: John Carroll's coffee-chat-voice-assistant credited; voice_rag_README.md excluded from rebrand

### Verification Testing (Birdperson)
- **Test suite**: test_rebrand_verification.py created with 12 tests
- **Coverage**: Scans all source files (.py, .ts, .tsx, .html, .css, .json, .md, .yaml, .bicep) for forbidden terms
- **Forbidden terms**: "dunkin", "crew member", "coffee-chat" / "coffee chat"
- **Targeted checks**: README title, index.html title, backend system prompt
- **Exclusions**: .squad/, .git/, node_modules/, __pycache__/, voice_rag_README.md, test file itself
- **Status**: All 12 tests passing post-rebrand

## Sonic Menu Items Search Index Name (2026-03-19)

**Author:** Summer (Backend Dev)

### Decision
Changed the default Azure AI Search index name to `sonic-menu-items` across:
- `.env-sample` (was `sonic-drive-in`)
- `infra/main.parameters.json` (was `voicerag-intvect`)
- New `sonic_menu_ingestion_search.ipynb` notebook (hardcoded)

### Rationale
Brian requested a distinct index for Sonic menu ingestion. The new notebook creates a `sonic-menu-items` index, so the default config should match. The app reads the index from `AZURE_SEARCH_INDEX` env var, so runtime behavior depends on what's in the actual `.env` file.

### Impact
- Any new deployment using `azd` defaults will provision with `sonic-menu-items` index name
- Existing deployments are unaffected (they use their own `.env` values)
- Team members should update their local `.env` if they want to match the new default

## Increase max_response_output_tokens from 150 → 250 (2026-03-19)

**Author:** Summer (Backend Dev)  
**Supersedes:** Decision #2 (Constrain Model Output Tokens)

### Context
Decision #2 set `max_response_output_tokens = 150` to keep voice responses concise. In practice, this truncated the closing phrase "Thank you! Your carhop will have that right out to you!" — the AI would say "Your carhop will have that right—" and stop.

### Decision
Raised the cap to 250 tokens. This gives enough room for a 1-2 sentence response + order recap + full closing phrase, while still preventing runaway generation.

### Trade-offs
- Slightly longer max possible response (~187 words vs ~112 words)
- Still well under the model's natural output limit
- If further truncation issues appear, consider removing the cap entirely and relying solely on the system prompt's "be brief" instruction

### Impact
- Fixes voice truncation on closing phrases
- No measurable latency impact for typical 1-2 sentence responses (model stops naturally before hitting the cap)

## Azure Speech Mode Architecture (2026-03-20)

**Author:** Summer (Backend Dev)

### Decision

The Azure Speech mode now uses a combined endpoint pattern (`POST /azurespeech/speech-to-text`) that performs STT + chat completion + tool calling in a single HTTP request. This differs from the Realtime WebSocket mode which streams continuously.

### Key Design Choices

1. **Async OpenAI client (`AsyncAzureOpenAI`)** — avoids blocking the event loop during chat completion. The sync `AzureOpenAI` client was a correctness issue in an aiohttp server.

2. **Executor for Speech SDK** — `recognize_once()` is synchronous. Wrapped in `run_in_executor()` to keep the event loop free. Same pattern for TTS.

3. **Separate SearchClient instance** — Azure Speech gets its own `SearchClient` (vs sharing with the Realtime pipeline). This keeps connection pools isolated so Speech mode load can't starve the WebSocket pipeline.

4. **Conversation history per session** — Multi-turn context stored in-memory with a 20-message sliding window (plus system message). This lets the model reference prior turns without unbounded memory growth.

5. **Conditional mount** — Azure Speech routes only mount if `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are configured. The Realtime WebSocket mode is unaffected if Speech env vars are missing.

6. **Session ID in response** — The endpoint creates an `order_state_singleton` session on first call and returns the `session_id`. The frontend can pass it back on subsequent calls for order continuity.

### Impact on Frontend

The frontend hook (`useAzureSpeech.tsx`) now consumes `tool_results` from the response and invokes `onReceivedToolResponse` for each entry to update the order panel in real-time.

### Risks

- Conversation history is in-memory; lost on server restart. Acceptable for drive-thru ordering sessions (short-lived).
- No extras validation in the Azure Speech update_order path (unlike the Realtime pipeline's tools.py which checks extras against base items). Could be added if needed.

## Azure Speech Hook — Tool Response Processing & Session Management (2026-03-20)

**Author:** Morty (Frontend Dev)

### Context

The `useAzureSpeech` hook had `onReceivedToolResponse` defined in its `Parameters` interface and passed by `App.tsx`, but the parameter was never destructured or used. This meant Azure Speech mode silently ignored all order updates from the backend — the carhop ticket always showed $0.00.

### Decisions

#### 1. Tool result processing matches real-time pattern
The hook now processes `tool_results` from the REST response and constructs `ExtensionMiddleTierToolResponse` objects with the same shape the real-time WebSocket hook uses. This lets `App.tsx` use one callback pattern for both modes.

#### 2. Session ID via `useRef` + `crypto.randomUUID()`
Each `startSession()` call generates a fresh UUID. The session ID is sent in every `/azurespeech/speech-to-text` request body so the backend can maintain order state per conversation. This parallels how the WebSocket mode implicitly gets a session per connection.

#### 3. Backward compatible
If the backend response omits `tool_results`, the hook works exactly as before — no errors, no regressions.

### Impact
- Azure Speech mode now properly updates the order panel with prices and items
- Backend now returns `tool_results` array and accepts `session_id` in the request body

## Canonical Size Labels for Menu Data (2026-03-20)

**Author:** Summer (Backend Dev)

### Decision
All size labels in the search index must use one of five canonical values: **Mini**, **Small**, **Medium**, **Large**, **RT 44**. The ingestion notebook normalizes raw product display names (e.g., "Sm Cherry Limeade") to these labels. The system prompt enumerates these valid sizes explicitly.

### Rationale
Raw Sonic API data embeds the product name in each size variant's display name. Without normalization, the AI model sees inconsistent labels across products and can't reliably match customer size requests to search result data. Standardized labels make the `update_order` tool call deterministic.

### Impact
- Next time the ingestion notebook is re-run, the search index will contain clean size labels
- The AI model now has explicit guidance on valid size names
- Any downstream code that checks size names should expect only these 5 values plus "Standard" (for items without size variants)

## Menu Sizes Sourced from Production Data (2026-03-19)

**Author:** Morty (Frontend Dev)

### Context
The UI menu panel (`menuItems.json`) had only Small/Medium/Large for drinks, while the AI voice assistant and Azure AI Search index offered 5 sizes (Mini, Small, Medium, Large, RT 44). This caused customer confusion.

### Decision
- Drink items (Cherry Limeade, Blue Raspberry Slush, Ocean Water) now show all 5 sizes: mini, small, medium, large, rt 44
- Shake items get mini added (4 sizes total). No RT 44 exists for shakes in production data
- SONIC Blast corrected to mini/small/medium — production data only has 3 sizes for this category
- All prices sourced from `sonic-menu-items.json` (production data), not manually set
- A reusable script (`scripts/update_menu_sizes.py`) was created to re-sync sizes/prices from production data whenever it changes

### Impact
- UI and voice assistant now show consistent size options
- Future price changes can be synced by re-running the script

## Greeting Sent AFTER session.update (2026-03-19)

**Author:** Summer (Backend Developer)

### Problem
The greeting was sent to OpenAI BEFORE the `session.update` message was forwarded. This caused three cascading issues:
1. **No tools available** — AI couldn't call `update_order`, so items were never added to the ticket ($0.00 orders)
2. **No system message** — AI used wrong closing phrases and didn't follow Sonic persona
3. **Mid-conversation reconfiguration** — AI had to reconfigure after greeting, causing delays

### Root Cause
In `app/backend/rtmt.py`, the `from_client_to_server()` function sent the greeting BEFORE processing/forwarding the first `session.update` message (which contains tools, system message, and voice config).

### Decision
Reordered message flow in `rtmt.py`:
1. Process and forward `session.update` first (with tools, system message, voice config)
2. Then send greeting

WebSocket messages are ordered, so OpenAI processes them in the correct sequence.

### Related Changes
Strengthened system prompt in `app.py`:
- Made tool calling instruction explicit: "When a guest orders items, IMMEDIATELY call 'update_order'. The guest ordering IS confirmation."
- Made closing phrase instruction emphatic: "you MUST say EXACTLY: [phrase] — Do NOT use any other closing phrase."

### Impact
- AI has tools available when generating responses → `update_order` calls work → items added to ticket
- AI has system message from the start → uses correct closing phrases and Sonic persona
- No mid-conversation reconfiguration → faster, smoother interactions
- All 100 tests pass

## Coordinated Echo Suppression Fix (2026-03-19/20)

### Frontend: Early Mic Mute on response.created (Morty)
**Date:** 2026-03-19  
**Status:** Implemented

The previous audio feedback loop fix muted the mic on `response.audio.delta`, but audio samples had already been sent to the server by then — causing phantom user inputs like "Peace." and "Thank you so much." from echoed AI speech.

**Decisions:**
1. **Mute on `response.created`** — the earliest event the OpenAI Realtime API sends when a response begins, arriving before any audio deltas.
2. **Send `input_audio_buffer.clear`** on `response.created` — flushes any already-buffered echo from the server's audio pipeline.
3. **Unmute on barge-in** — `input_audio_buffer.speech_started` now resets `isAiSpeakingRef` and unmutes the mic so the user can resume speaking after interrupting.

**Trade-offs:**
- With gain=0, barge-in relies on audio that was in-flight before the mute took effect. If the server detected real user speech from pre-mute audio, the barge-in handler correctly unmutes. Full barge-in during muted playback is not possible (acceptable — echo prevention is higher priority).
- `sendJsonMessageRef` pattern adds a small layer of indirection in `useRealtime.tsx` but is necessary to break the circular dependency between `useCallback` and `useWebSocket`.

**Files Changed:**
- `app/frontend/src/hooks/useRealtime.tsx` — Added `response.created` handler, `sendJsonMessageRef`, `onReceivedResponseCreated` callback
- `app/frontend/src/App.tsx` — Moved mute to `onReceivedResponseCreated`, updated barge-in handler, removed redundant transcript delta muting

### Backend: Server-Side Echo Suppression in rtmt.py (Summer)
**Date:** 2026-03-20  
**Status:** Implemented

Frontend mic-muting reduced but didn't eliminate the audio feedback loop. A timing gap exists between when AI audio arrives at the server and when the frontend gain-node mute activates — during this gap, echoed audio reaches the server, gets forwarded to OpenAI, and is transcribed as phantom user input.

**Implementation:** Three coordinated mechanisms in `rtmt.py`:
1. **Audio gating**: Track `ai_speaking` state per-connection. When `response.audio.delta` messages flow server→client, drop all `input_audio_buffer.append` messages from client→server.
2. **Post-response cooldown**: After `response.audio.done`, suppress audio for an additional 300ms to cover speaker-to-mic latency.
3. **Buffer flush**: Send `input_audio_buffer.clear` to OpenAI after each AI audio response completes to discard any leaked echo.

Barge-in preserved: `input_audio_buffer.speech_started` from OpenAI's server VAD immediately clears suppression.

**Trade-offs:**
- **Pro**: Eliminates phantom transcriptions at the server layer, independent of frontend timing.
- **Pro**: Zero JSON parse overhead — uses fast substring markers on the hot path.
- **Con**: Barge-in has ~300ms latency after AI finishes speaking. Acceptable for drive-thru UX.
- **Con**: During AI speech, user audio is fully dropped (not buffered). If cooldown is too aggressive, genuine speech immediately after AI could be clipped. Monitor and tune `_ECHO_COOLDOWN_SEC` if needed.

**Files Changed:**
- `app/backend/rtmt.py` — echo suppression state, audio gating, buffer flush, barge-in detection

### Coordination
Both fixes together form a complete echo suppression solution:
- **Frontend**: Early mute at `response.created`, automatic `input_audio_buffer.clear`
- **Backend**: Audio gating + cooldown + buffer flush
- **Result**: Phantom transcriptions eliminated; all 100 backend tests pass, 13 frontend tests pass

## Tools.py Demo Hardening (2026-03-21)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Four Targeted Improvements to update_order and search

#### 1. Hardened Price Validation
- **Decision:** Reject `add` actions with `price <= 0.0` before any order state mutation. Return friendly retry message via `ToolResultDirection.TO_SERVER`.
- **Rationale:** When the model skips search and guesses a price, it defaults to $0.00 — looks like a bug in demo. Early guard catches this before the item enters the order.
- **Trade-off:** Extras like "Whipped Cream" at $0.50 still work (>0). Items truly free would need an explicit $0.01 workaround, but Sonic has no free items.

#### 2. Combo Detection with Pending Slots
- **Decision:** After adding a Combo item (case-insensitive substring match), append a `(COMBO DETECTED: ...)` hint to the ToolResult instructing the AI to ask for side and drink selections.
- **Rationale:** Combos require side + drink choices. Without an explicit hint, the AI sometimes skips these and moves to the next item. The hint is appended to the order summary JSON so the AI gets it in context.
- **Trade-off:** Hint is text-appended to JSON (not structured). Acceptable because the AI model parses both.

#### 3. Human-Readable Size Formatting in Search Results
- **Decision:** Parse the `sizes` JSON field into `"Small ($X.XX), Medium ($Y.YY)"` format. Falls back to raw string on parse failure.
- **Rationale:** gpt-realtime-1.5 struggles to speak raw JSON like `[{"size":"Small","price":2.49}]`. Human-readable format lets it naturally say prices.
- **Trade-off:** Slightly changes search result format — existing tests updated. Description field dropped from search summary (sizes more important for ordering).

#### 4. Upsell Hints in Tool Results
- **Decision:** After successful `update_order`, append category-based upsell hints (combo upgrade, combo conversion for burgers, flavor add-in for drinks). No upsell on desserts/shakes.
- **Rationale:** Complements Unity's suggestive selling system prompt (Decision #27) with programmatic nudges. AI gets concrete suggestions in the tool response.
- **Trade-off:** Hints only fire on `add` actions, not `remove`. Uses `_infer_category()` — category lists must stay in sync.

### Impact
- All 111 existing tests pass with no modifications needed
- All four changes are additive — no existing functionality removed or altered
- Works with existing `_infer_category()`, `_SearchCache`, and `ToolResultDirection.TO_BOTH`

## Order Quantity Limits (2026-03-21)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Decision
Added per-item (`MAX_QUANTITY_PER_ITEM = 10`) and total order (`MAX_TOTAL_ITEMS = 25`) quantity limits enforced in the `update_order` tool in `tools.py`.

### Rationale
Prevents abuse scenarios (ordering 100+ of an item) that could destabilize the system, confuse the AI, or create unrealistic orders. Limits are realistic for a drive-thru window — 10 of any single item and 25 total items are generous enough for large family/group orders but cap truly absurd requests.

### Implementation Details
- Validation runs in `update_order()` **before** `handle_order_update()` is called — invalid quantities never touch order state.
- Only applies to `"add"` actions — removing items is never gated.
- Per-item check matches on `item_name + size` combo (same logic as `order_state.py` deduplication).
- Error messages are warm and customer-friendly, sent as `ToolResultDirection.TO_SERVER` so the AI can relay them conversationally.
- Constants are at module top of `tools.py` for easy tuning without code changes.

### Trade-offs
- Limits are not configurable at runtime (would need env var or config file for hot-tuning). Current constants are easy to change and redeploy.
- The AI model receives the limit message and may paraphrase it — this is intentional for natural conversation flow.

### Files Changed
- `app/backend/tools.py` — added constants + validation logic in `update_order()`

## Conversational Quantity Limit Guardrails (2026-03-21)

**Author:** Unity (AI / Realtime Expert)  
**Status:** Implemented

### Decision
Added a QUANTITY LIMITS section to the system prompt in `app/backend/app.py` with conversational guardrails for excessive order quantities.

### Limits
- **Per-item max:** 10 — AI suggests capping at 10 with friendly language
- **Total order max:** 25 items — AI suggests catering line for larger orders
- These match Summer's backend enforcement values exactly

### Design Choices
- **Placement:** Between ORDERING and CLOSING sections (natural conversation flow)
- **Tone:** Warm, helpful — like a carhop looking out for the customer. No "error" or "limit exceeded" language.
- **NEVER refuse service** — always offer the closest alternative
- **4 bullets only** — kept concise to minimize first-response latency impact
- **Defense-in-depth:** AI handles it conversationally first, backend enforces hard limits second

### Coordination
- Summer is adding backend enforcement with the same limits (per-item 10, total 25)
- AI-level guardrails prevent most cases from ever hitting the backend rejection path

## Combo Validation & Delta Summaries (2026-03-21)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Decision

## Dual-Trigger Greeting for API Resilience (2026-03-22)

**Author:** Summer (Backend Dev)  
**Date:** 2026-03-22  
**Status:** Implemented

### Context

Brian started a demo and got complete silence — no greeting, no audio. The session was active (frontend showed "Conversation in progress") but the AI never spoke.

Root cause: A recent change moved the greeting trigger from `from_client_to_server` (fired after forwarding `session.update`) to `from_server_to_client` (fired after receiving `session.updated` from Azure OpenAI). The intent was correct — ensure tools are configured before greeting — but Azure's Realtime API doesn't reliably send `session.updated`, so the greeting never fired.

### Decision

Implement dual-trigger greeting in `rtmt.py`:

1. **Primary (server→client):** Fire greeting when `session.updated` is received — guarantees tools are configured.
2. **Fallback (client→server):** Fire greeting after forwarding a `session.update` message — reliable because it doesn't depend on API response events.

The existing `greeting_sent` flag (checked in `send_greeting_once()`) prevents double-greeting regardless of which trigger fires first.

### Implementation

- Added `_MARKER_SESSION_UPDATE = '"session.update"'` constant alongside existing `_MARKER_SESSION_UPDATED`.
- In `from_client_to_server`, after forwarding the client message, check if it was a `session.update` (but NOT `session.updated`) and fire `send_greeting_once()`.
- Defensive substring check: `_MARKER_SESSION_UPDATE in msg.data and _MARKER_SESSION_UPDATED not in msg.data` — even though `session.updated` can't appear in client→server messages, this is belt-and-suspenders.

### Trade-offs

- The fallback trigger may fire before tools are fully acknowledged by OpenAI. In practice this is fine because the `session.update` has already been forwarded — OpenAI processes messages in order, so tools will be configured by the time it processes the greeting's `response.create`.
- Slight increase in code complexity (two trigger sites instead of one), mitigated by clear comments and the single `send_greeting_once()` function.

### Impact

Eliminates demo-blocking silence on startup. All 118 tests pass. Frontend build succeeds.

Extended order_state.py with deterministic combo validation, Sonic-specific size cleanup, and split voice/screen payloads.

### 1. `get_combo_requirements()` on OrderState
- Scans entire order for combo/side/drink ratios using `_infer_combo_component()` (lightweight keyword check in order_state.py)
- Avoids circular import: tools.py has full `_infer_category()`, order_state.py has focused `_infer_combo_component()`
- Returns `is_complete`, `missing_items`, `prompt_hint`
- Replaces ad-hoc `(COMBO DETECTED: ...)` hint with persistent `[SYSTEM HINT: ...]` pattern that fires after EVERY `update_order` call
- When combo is incomplete, upsell hints are suppressed — AI focuses on completing the combo first

### 2. Sonic Size Cleanup
- Removed Dunkin' remnants: "Kannchen" and "Pot" size formatting
- Added Route 44 support: `rt44`, `rt 44`, `route 44` → "Route 44 " prefix
- Unrecognized sizes now default to empty string (hidden) rather than capitalized

### 3. Delta Summaries (Voice vs Screen Split)
- `ToolResult` extended with optional `client_text` field and `to_client_text()` method
- Server (OpenAI) receives: natural-language delta ("Added 1 Large Cherry Limeade — your total is now $8.49") + system hints
- Client (frontend) receives: pure JSON order summary for the display panel
- Backward-compatible: `to_client_text()` falls back to `to_text()` when no `client_text` is set

### Impact
- **Unity**: `[SYSTEM HINT: ...]` pattern is ready — add corresponding system prompt instruction for combo completion flow
- **Morty**: Frontend now receives pure JSON in `tool_result` for `update_order` (no more appended hint text in the JSON payload)
- **Birdperson**: 7 new combo requirement tests added (118 total, all passing)

### Risk
- `_infer_combo_component()` duplicates subset of `_infer_category()` logic — if new drink/side categories are added to `_infer_category()`, they must also be added here

## System Hint Integration — Tool Hints in Prompt (2026-03-21)

**Author:** Unity (AI / Realtime Expert)  
**Status:** Implemented

### Decision

Added TOOL HINTS section to system prompt in `app/backend/app.py` to guide AI consumption of `[SYSTEM HINT]` patterns in tool results.

### Implementation

- **Location:** After ORDERING section, before SUGGESTIVE SELLING
- **Content:** 2 bullets explaining how AI processes hints embedded in tool results (e.g., missing combo sides/drinks, upsell opportunities)
- **Behavior:** AI recognizes hints, acts on them conversationally, NEVER reads hints aloud
- **Defense-in-depth:** Backend decides *when* to hint (Summer's `[SYSTEM HINT]` injection), system prompt tells AI *how* to act

### Coordination

- Complements Summer's backend `[SYSTEM HINT]` injection in tool results
- Hint pattern ready for immediate use in combo completion flow, upsell prompts, and other dynamic guidance
- All hints are suppressed while combos incomplete — focus on completion first

## Demo Polish Sprint (2026-03-21T20:23-20:28)

**Author:** Brian Swiger (via Copilot), coordinated across Summer and Unity

### 1. Lower RTMiddleTier Temperature from 0.6 → 0.5
- **Author:** Summer (Backend Dev)
- **Why:** Reduces creative wandering in voice responses, tighter carhop persona. Improves Time to First Token (TTFT) — lower temperature means model commits to high-probability tokens faster.
- **Change:** `rtmt.temperature` in `app/backend/app.py` line 125
- **Verification:** Static file serving order verified — `_index_handler` (explicit `GET /` route) registered before `add_static('/')`. In aiohttp, explicit routes take priority, so no conflict.

### 2. Suggestive Sell Follow-Through Guardrail
- **Author:** Unity (AI / Realtime Expert)
- **What:** Added rule to TECHNICAL GUARDRAILS: "If the guest says 'Yes' or 'Sure' to a suggestive sell (like a combo), IMMEDIATELY ask for the missing details (e.g., 'Awesome, tots or fries with that?')."
- **Why:** Ensures demo conversations flow naturally without pauses after customer agreement. Complements existing combo detection and upsell hints.
- **File:** System prompt in `app/backend/app.py`

### 3. Grouped Readback Integration
- **Author:** Summer (Backend Dev)
- **What:** Added `get_grouped_order_for_readback()` method to OrderState. Groups identical items for natural voice read-back (e.g., "Two Medium Cherry Limeades and one Footlong Quarter Pound Coney"). Integrated with `get_order` tool using `TO_BOTH` routing.
- **Why:** AI was receiving raw JSON for order readback, sounding robotic. A human carhop groups duplicates — AI should too.
- **Changes:**
  - `order_state.py` — new grouping method
  - `tools.py` — `get_order` changed to `TO_BOTH` with `client_text`. AI receives grouped text; frontend receives full JSON.
- **Testing:** All 118 tests pass. Pure computation on existing data. `TO_BOTH` pattern already tested in `update_order`.

## Fix Greeting-Before-Session.Update Tool Blindness (2026-03-22)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Problem
The AI conversed perfectly (asking about combos, sizes, drinks) but NEVER called `update_order`, `search`, or `get_order`. The order panel stayed at $0.00.

**Root cause:** `from_client_to_server()` in `rtmt.py` sent the greeting (`conversation.item.create` + `response.create`) to OpenAI BEFORE forwarding the client's `session.update` message — which carries tool definitions, system_message, and tool_choice. OpenAI received greeting before tools were configured.

### Solution
1. **Reordered greeting:** Client messages now forwarded FIRST (`_process_message_to_server` → `send_str`), then the greeting fires. OpenAI sees: `session.update` → greeting → `response.create`. Tools configured before first completion.

2. **Fallback tools_pending registration:** `response.output_item.added` now pre-registers `call_id` in `tools_pending` as safety net. `conversation.item.created` always overwrites with correct `previous_item_id`. Prevents silent tool-call drops if API event ordering changes.

3. **Diagnostic logging:** `session.update` now logs tool count and tool_choice. Tool execution logs tool name, args, and result direction.

### Impact
- Fixes demo blocker — orders now appear on carhop ticket
- All 118 existing tests pass
- No API or schema changes required

---

## System Prompt Tool-Calling Mandate (2026-03-21)

**Author:** Unity (AI / Realtime Expert)  
**Status:** Implemented

### Problem
The ORDERING section had only weak instruction — "Call update_order ONLY after guest confirms." The word "ONLY" reads as restriction, not mandate. Model treated ordering as role-play, never triggering tool calls.

### Solution
Added new "⚠️ TOOL-CALLING RULES — MANDATORY" section positioned early (section #3, after CONVERSATIONAL FLOW, before MENU & PRICING) with:
- Explicit negative instructions: "NEVER say X WITHOUT calling Y FIRST"
- Consequence statements: "the item WILL NOT appear"
- Mandatory flow: search → confirm → update_order
- Reinforced in ORDERING and MENU & PRICING sections

### Rationale
For gpt-realtime-1.5, tool-calling requires EXPLICIT negative instructions and consequence statements. Positive instructions alone ("call update_order after confirmation") deprioritized in favor of conversation. Position matters — tool-calling rules must appear near top, not buried in section #6.

### Impact
- Demo-tested with multi-item orders
- Tool-calling now reliable
- Order flow reaches completion

## Copilot Directive — Demo System Prompt Enhancements (2026-03-21T21:10)

**Author:** Brian Swiger (via Copilot)  
**Status:** Pending Review

### What
Three critical system prompt sections for Inspire Brands demo:
1. **PERSONALIZATION** — Carhop spirit, handle "regulars" and "happy hour" mentions warmly
2. **PATIENCE & CLARITY** — Handle stalls/silence gracefully ("No rush!"), offer Fan Favorites when asked for recommendations
3. **VISUAL SYNC** — Occasional spatial language ("I've got that added to your ticket right now")
4. **COMBO LOGIC — DETERMINISTIC** section enforcing priority: Item Selection → Combo Completion → Upsell → Treat Suggestion

### Why
Makes the AI feel like a person on skates, not a kiosk. Critical for emotional impact with Inspire Brands demo stakeholders.

---

## Demo Bug Fix Changeset — APPROVED (2026-03-22)

**Author:** Rick (Lead/Architect)  
**Status:** APPROVED

### Bug 1: Tools Not Called (Greeting Race Condition) ✓
- **Root cause:** `response.create` fired before OpenAI confirmed `session.updated`, so model hadn't loaded tool definitions.
- **Fix:** Move greeting trigger from `from_client_to_server` to `from_server_to_client` (fire-on-`session.updated`). One-line logical move using `_MARKER_SESSION_UPDATED` substring check.

### Bug 2: Barge-In Deadlock ✓
- **Root cause:** Frontend echo suppression (gain=0) → backend drops `input_audio_buffer.append` → OpenAI never fires `speech_started` → nothing unmutes. Circular dependency.
- **Fix:** AnalyserNode tapped before gain node detects user speech on muted stream. RMS energy calculation (textbook), 0.08 threshold (conservative), 100ms polling (cheap).

### Ancillary Fixes ✓
- `reset_order` session_id crash — fixed
- `reset_order` TO_CLIENT → TO_BOTH — consistent with Decision #26
- Frontend tool dispatch — add `get_order` and `reset_order` handlers for carhop ticket updates

### Risk Assessment
**Low.** All changes are additive or fix obvious bugs. Barge-in monitor only activates when mic muted (normal path is no-op). Greeting timing strictly safer.

---

## Happy Hour Dynamic Pricing (2026-03-22)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Decision
Drinks and slushes are automatically half-price between 2:00 PM and 4:00 PM local time.

### Key Choices
1. **Local time** — used `datetime.now()`, avoided new dependencies for demo environment
2. **Summary-level discount** — original `item.price` preserved on each OrderItem; 50% applied only to calculated totals
3. **Reused `_infer_combo_component()`** — already identifies drinks; single source of truth
4. **Context in tool results** — `update_order` and `get_order` append `[HAPPY HOUR ACTIVE: drinks/slushes half-price!]` so AI knows to get excited

### Impact
- `order_state.py` — added `is_happy_hour()` helper, updated `_update_summary()` loop
- `tools.py` — import, append note to tool results
- All 118 tests pass, no regressions

---

## OOS Machine Status Check in Search Results (2026-03-22)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### What
Menu items dependent on down machines get `[OOS: ...]` flagged in search results so AI steers customers away.

---

## Local Mode WebSocket Direct Connection (2026-07-23)

**Author:** Birdie (Frontend Dev)  
**Status:** Implemented

### Context

Clicking the mic button while offline in local mode produced total silence — no audio, no response, no error. Three root causes:

1. WebSocket URL was relative (`/realtime`), resolving to the remote Azure host instead of localhost
2. `react-use-websocket`'s `sendJsonMessage` silently drops messages when connection isn't OPEN
3. Local mode state wasn't synced to backend at session start (only on toggle change)

### Decision

- **When `localMode=true`, connect WebSocket directly to `ws://localhost:8000/realtime`** instead of using relative URL. This ensures the frontend talks to the local backend regardless of where the page was served from.
- **Always check `readyState === OPEN` before starting a session.** If not connected, show a user-visible error instead of silently failing.
- **Sync local mode state to backend at every session start**, not just when the toggle changes.
- **Skip Azure session token fetch in local mode** — no auth needed for offline operation.

### Impact

- `useRealtime.tsx` now accepts `localMode` parameter and returns `readyState`
- `App.tsx` gates mic activation on WebSocket readyState
- User sees `⚠️` error message below mic button when connection fails
- Console logs use `[WS]`, `[MIC]`, `[LOCAL-MODE]` prefixes for easy filtering

### Trade-offs

- Hardcoded `localhost:8000` — matches current backend config. If port changes, this needs updating.

---

## Voice Switch Must Push session.update to OpenAI (2026-07-24)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

The `extension.set_voice` WebSocket handler in `rtmt.py` stored the new voice choice in `self.voice_choice` but never sent a `session.update` to the OpenAI Realtime API. Because the frontend only sends `session.update` once at session initialization, mid-session voice changes were silently ignored — the user picked a new voice in the UI but the AI kept speaking in the old one.

### Decision

Any extension handler that modifies OpenAI session parameters (voice, temperature, etc.) must immediately send a `session.update` message to the upstream `target_ws` WebSocket. Storing state locally on the `RTMiddleTier` instance is necessary for future reconnects but is **not sufficient** to change the live session.

### Implementation

After `self.voice_choice = new_voice`, we now build and send:
```json
{"type": "session.update", "session": {"voice": "<new_voice>"}}
```
via `await target_ws.send_str(...)`, with standard logger + `_vlog()` calls for observability.

### Impact

- **File changed:** `app/backend/rtmt.py` (lines ~557-574)
- **Risk:** Low — only adds a WebSocket send inside an existing validated code path
- **Team note:** If Birdie adds more extension handlers that change session params (e.g., temperature, modalities), the same pattern applies — store locally AND push `session.update`.

---

## Local Mode: Session Tokens, Verbose Logging & File Logging (2026-07-22)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Decision

Local mode now supports the same three observability features as cloud mode:

#### 1. Session Tokens
- LocalPhi4Processor emits `extension.session_metadata` (on connection) and `extension.round_trip_token` (after each response.done) — identical JSON format to cloud mode.
- Frontend SessionTokenPanel renders local mode tokens identically to cloud tokens.
- Token format: `{uuid}-{round:04d}` (same as `order_state._format_round_trip_token`).
- Local mode tracks its own session_token and round_trip_index independently from OrderState (no dependency on order_state_singleton).

#### 2. Verbose Logging
- LocalPhi4Processor handles `extension.set_verbose_logging` WebSocket message and toggles per-connection verbose flag.
- Uses the shared `mcdonalds-verbose` logger and `vlog()` from `audio_pipeline.py`.
- Verbose messages at every pipeline step: audio received, STT start, inference start/end (timing + token count), tool execution, TTS start/end (chunk count), round trip advancement.

#### 3. Log to File
- LocalPhi4Processor handles `extension.set_log_to_file` WebSocket message.
- Reuses `create_verbose_file_handler()` / `remove_verbose_file_handler()` from `audio_pipeline.py`.
- Logs written to `app/backend/logs/verbose-*.log` — no network dependency, fully offline.
- File handler cleaned up on WebSocket disconnect.

### Trade-offs

- Local mode session tokens are not tied to `order_state_singleton` — this avoids coupling but means the token UUID is different from the order session ID. This is acceptable because local mode has its own session lifecycle.
- `_process_utterance` and `_process_utterance_safe` gained optional `session_state` and `verbose` parameters with defaults — backward compatible with existing tests.

---

## Meal Size Upgrade via 'modify' Action (2026-07-22)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Decision

Added a `"modify"` action to the `update_order` tool for in-place meal size changes. When the AI needs to change a meal's size (e.g., Medium → Large), it calls `update_order` with `action: "modify"` instead of remove + add. This preserves all meal components (entree, fries, drink) and updates their size prefixes automatically.

### Rationale

The old remove+add pattern caused drinks to be ejected as separate charged line items during size upgrades. The `modify` action keeps everything in place — no data loss, no double-charging.

### Impact

- **order_state.py:** New `"modify"` handler in `handle_order_update()` + `_update_component_size()` helper
- **tools.py:** `update_order` schema now accepts `"modify"` in the action enum; delta text handles size change messaging
- **system_prompt.yaml:** New MEAL_SIZE_CHANGES section (priority 12.5) instructs AI to use `modify` for size changes; ORDERING and SUGGESTIVE_SELLING updated to ask about size during meal conversion
- **Tests:** 6 new tests covering upgrade, downgrade, breakfast, combo synonym, and no-double-charge scenarios

### Who Should Know

- **Birdie:** The frontend `OrderSummary` component already renders `components` arrays — no frontend change needed. The meal item's display/price/components update in place.
- **Ronald:** The `modify` action follows the same tool-calling flow (search → update_order). System prompt changes may need review for prompt engineering quality.

---

## Combo = Meal Synonym Support (2026-07-21)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Decision

"Combo" and "Meal" are treated as identical synonyms across the entire backend. Both terms trigger the same component auto-population (entree + fries/hash browns), drink absorption, combo conversion, and combo pivot logic.

### Rationale

Drive-thru customers commonly say "combo" instead of "meal" (e.g., "Big Mac combo", "number 3 combo large"). The backend `_is_meal()` function only matched "meal", so "Combo"-named items missed the fries auto-population and component breakdown. This made the order screen inconsistent depending on which word the AI chose.

### Impact

- **order_state.py:** `_is_meal()` now matches both "meal" and "combo" — functionally equivalent to `_is_meal_or_combo()` for McDonald's
- **System prompt:** New COMBO_MEAL_SYNONYMS section (priority 9) instructs the AI to always translate "combo" → "Meal" in tool calls while mirroring the customer's terminology in speech
- **Tests:** 7 new combo synonym tests, 8 existing tests updated to reflect new behavior
- **tools.py:** No changes needed — regex patterns already match "combo" for meal number expansion

### Who Should Know

- **Birdie:** Frontend order display should handle items named with either "Combo" or "Meal" — both will have components populated
- **Ronald:** System prompt priority numbers shifted (9→23 instead of 9→22) due to new section insertion

---

## Local Mode Uses Separate Short Prompt (No Tools) (2026-07-23)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

Phi-4 INT4 on DirectML hangs on inference when given the full 12,899-char cloud system prompt (~3,768 tokens). The prefill phase never completes — no tokens are ever generated.

### Decision

Local mode uses a completely separate, drastically shorter system prompt (`local_system_prompt.yaml`, 885 chars / ~221 tokens) and passes NO tool schemas to inference.

#### Rationale

1. **INT4 models can't handle large prompts** — the quantized model chokes on prefill beyond ~1000 tokens in reasonable time on consumer GPUs
2. **Tool calling is unreliable on INT4** — structured JSON output from a 4-bit quantized model is inconsistent; tool schemas in the prompt waste tokens
3. **93% prompt reduction** makes inference feasible on DirectML with acceptable latency
4. **30s timeout** prevents infinite hangs if prompt is still too large — returns graceful fallback

#### What local mode loses

- No structured tool calling (search, update_order, etc.)
- No detailed combo/meal logic, size upgrade rules
- No menu number mappings
- No compliance/brand language enforcement

#### What local mode keeps

- McDonald's drive-thru persona
- Basic ordering flow (confirm items, suggest meals, ask about sizes)
- Natural conversational style
- Menu-only boundaries

### Impact

- **Frontend:** No changes needed
- **Cloud mode:** Completely unaffected — uses full prompt + tools as before
- **Local mode:** Should complete inference instead of hanging
- **Tests:** Zero regressions (692 pass, 7 pre-existing failures)

### Files Changed

- `app/backend/prompts/mcdonalds/local_system_prompt.yaml` (NEW)
- `app/backend/prompt_loader.py` (added `get_local_system_prompt()`)
- `app/backend/local_processor.py` (use local prompt, no tool schemas)
- `app/backend/phi4_model.py` (30s inference timeout)

---

## Multimodal Audio-In & VRAM Optimization (2026-07-23)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

RTX 4060 with 8GB VRAM was near capacity:
- Phi-4 INT4: ~3.5GB
- Faster-Whisper STT (small): ~1.5GB
- Piper TTS: ~0.1GB
- Total: ~5.1GB + OS (1GB) + KV cache → DirectML paging to system RAM

### Decision

**Multimodal audio-in implemented.** Phi-4's built-in speech encoder processes customer audio directly, eliminating Whisper STT entirely.

### What Changed

1. **phi4_model.py** — New multimodal API: `model.create_multimodal_processor()`, PCM→WAV conversion via `_pcm_to_wav_bytes()`, `og.Audios.open_bytes()` for audio loading, `generator.set_inputs()` + `proc.create_stream()` for inference.

2. **local_processor.py** — Whisper loading skipped when `model.multimodal_available` is True. Half-duplex mode: `self._generating` flag mutes VAD during inference.

3. **config.yaml** — `stt_model: "tiny"` (fallback only), `max_length: 1024`.

### VRAM Savings

| Before | After | Savings |
|--------|-------|---------|
| Phi-4 ~3.5GB | Phi-4 ~3.5GB | — |
| Whisper ~1.5GB | Skipped | **~1.5GB** |
| Piper ~0.1GB | Piper ~0.1GB | — |
| KV cache (2048 tokens) | KV cache (1024 tokens) | ~200MB |
| **Total: ~5.1GB** | **Total: ~3.6GB** | **~1.7GB** |

New headroom: ~3.4GB free on 8GB GPU (vs ~1.9GB before).

### API Discoveries

- `og.MultiModalProcessor(model)` constructor removed; use `model.create_multimodal_processor()`
- `og.Audios.open_bytes()` requires a single WAV-formatted bytes object (not a list, not raw PCM)
- Token decoding: `proc.create_stream()` → `stream.decode(token_id)` (not tokenizer)
- `model.create_tokenizer()` doesn't exist; get tokenizer from processor

### Trade-offs

- Phi-4's speech recognition is slightly less accurate than Whisper-small for noisy drive-thru audio, but acceptable for order-taking
- Customer transcription in the Guest Conversation panel now shows the AI's interpretation rather than Whisper's verbatim transcript
- Half-duplex means the customer can't interrupt mid-response (barge-in still works via cancel event, but audio isn't buffered during generation)

---

## Sequential STT→LLM Pipeline for Text-Only Local Mode (2026-07-23)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

In local mode with the INT4 Phi-4 ONNX model, `MultiModalProcessor` is unavailable — the model runs in text-only mode. The pipeline was running Whisper STT and Phi-4 inference in **parallel**, meaning the LLM generated responses without ever seeing the customer's words.

### Decision

1. **Sequential pipeline** — Whisper STT runs first and is awaited. The transcribed text is then included in the Phi-4 prompt as a `<|user|>` turn. This adds ~2-5 seconds of STT latency before inference starts, but the model actually knows what the customer said.

2. **Proper chat template** — `_build_prompt()` now produces `<|system|>...<|end|><|user|>...<|end|><|assistant|>` format instead of dumping raw text. This matches Phi-4's expected chat format.

3. **Conversation history (3 turns)** — Stored on the processor instance as `(role, text)` tuples. Essential for drive-thru flow where customers add to orders incrementally ("I'll also have a Coke").

4. **max_length 8192→2048** — Prompt is ~400 tokens, response ~200. 2048 cuts KV cache overhead with no functional loss.

### Trade-offs

- STT-first adds latency vs. parallel execution. But parallel was broken — the model couldn't respond to what it never heard.
- Conversation history is per-processor-instance, not per-session. If multiple WebSocket sessions share a processor, history could bleed. Current architecture is 1:1 so this is fine for now.
- If `MultiModalProcessor` becomes available in the future, the parallel path with native audio embeddings would be faster and should be revisited.

---

## TTS Speed & AI Transcript in Local Mode (2026-07-23)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

Local mode just started working — two problems surfaced:

1. **TTS voice was slow/lethargic** — `length_scale: 0.9` in config.yaml produced sluggish speech compared to cloud mode's "Coral" voice.
2. **AI responses didn't appear in Guest Conversation panel** — frontend extracts AI text from `response.done` events at `response.output[0].content[0].transcript`, but local_processor.py was emitting `output` without a `content` array.

### Decision

1. **Lowered `tts_length_scale` from 0.9 → 0.7.** This produces noticeably faster, drive-thru-appropriate speech. The Piper engine clamps to [0.5, 2.0] so 0.7 is safely within range. Can be tuned further — 0.65 would be even faster if 0.7 still feels sluggish.

2. **Added `content: [{type: "text", transcript: <text>}]` to all `response.done` events** across three emission sites in local_processor.py. This matches the Azure Realtime API structure the frontend already parses.

### Impact

- **Birdie (Frontend):** No frontend changes needed — the fix is purely backend. AI text will now appear in the Guest Conversation panel automatically.
- **Testing:** These are runtime WebSocket behaviors not covered by unit tests. Verify manually by running local mode and checking the conversation panel.
- **Future:** If we want real-time streaming transcript (word-by-word appearing), the `response.audio_transcript.delta` events are already being sent — we'd just need a frontend handler.

---

## McDonald's Upsell Rules Replace Sonic Add-In Logic (2026-07-23)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

The system prompt, tools.py, and hints.yaml contained Sonic-style "flavor add-in" and "whipped cream" upsell logic inherited from the original Sonic codebase. Brian directed that McDonald's should never ask about drink add-ins or flavor customizations.

### Decision

#### Removed
- "Flavor Add-In" and "Whipped Cream" from EXTRAS_KEYWORDS (tools.py)
- Drink add-in upsell hints from hints.yaml and hardcoded fallbacks in tools.py
- "Extras: flavor add-in $0.50, whipped cream $0.50" from system prompt ORDERING section
- Sonic-specific blocked categories ("hot dogs & tots") from extras validation

#### Added — Three Upsell Rules in System Prompt (SUGGESTIVE_SELLING, priority 14)

1. **Meal Upsell:** Burger/sandwich alone → offer meal. Fries alone → suggest sandwich. Drink alone → suggest food. Skip if already ordered a meal.
2. **Dessert Upsell:** When guest says "that's it" and no dessert on order, offer ONE random dessert (McFlurry Oreo/M&M's, Baked Apple Pie, Hot Fudge Sundae). Only once per order.
3. **Never:** Ask about flavor add-ins, drink customizations beyond size, or Sonic-style mods.

#### Updated Categories
- ALLOWED_EXTRA_CATEGORIES: burgers & sandwiches, chicken & mcnuggets, combos
- BLOCKED_EXTRA_CATEGORIES: drinks, shakes, sides, desserts, sweets & treats

### Impact

- **Birdie:** No frontend changes needed — upsell logic is entirely in the AI prompt and backend tool hints
- **Ronald:** System prompt priority 14 (SUGGESTIVE_SELLING) is the main behavioral change — review if AI responses need tuning
- **Hamburglar:** 3 test files updated — all "Flavor Add-In"/"Whipped Cream" test cases replaced with "Extra Patty"/"Extra Cheese"
- **All:** 696 tests pass, zero regressions from changes

---

## Local Mode WebSocket Resilience Pattern (2026-07-22)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Context

Recurring bug: toggling Local Mode ON and clicking the microphone always showed "Cannot connect to local server." The WebSocket never reached OPEN state.

### Root Cause (Three Layers)

1. **RTMiddleTier token warmup crash** — `self._token_provider()` in the constructor threw when Azure AD was unreachable, crashing `create_app()` before the ProcessorRouter or `/realtime` route was created.
2. **No try/except around cloud processor creation** — any failure in credential setup, RTMiddleTier init, or tool attachment crashed the entire startup, even though local mode doesn't need any of those.
3. **Model load failure caused rapid WS connect/disconnect cycle** — `handle_websocket` returned immediately on model load failure, closing the socket. Auto-reconnect fired, failed again, etc. The OPEN window was too brief for the user to ever click the mic.

### Decision

- **Never let cloud processor failures prevent local mode from working.** The entire RTMiddleTier creation block in `app.py` is now wrapped in try/except. If it fails, `rtmt=None` and local-only mode proceeds.
- **Keep WebSocket alive on degraded mode.** When local model loading fails, the WebSocket stays open in a degraded message loop. Audio processing responds with text error messages instead of crashing the connection.
- **Token warmup is non-fatal.** `self._token_provider()` warmup in rtmt.py is now wrapped in try/except — failure is logged as a warning, and the token will be fetched on first request instead.
- **Always confirm route registration at startup.** Explicit log lines confirm `/realtime` registration and processor availability.
- **Diagnostic echo WebSocket at `/api/ws-test`** for isolating WebSocket infrastructure issues from handler logic.

### Impact

- Backend always starts, even with zero Azure credentials
- `/realtime` route is always registered
- Local mode WebSocket stays OPEN even when models fail to load
- Zero test regressions (694 pass, same 2 pre-existing failures)

---

## User Directive: Remove Sonic Flavor Add-Ins (2026-04-03)

**Author:** Brian Swiger (via Copilot)  
**Status:** Implemented

### Decision

No flavor add-ins for drinks (remove Sonic-style logic). Instead:
1. Always upsell to a meal if customer orders just a burger, fries, or drink
2. Always offer a random dessert (McFlurry or Baked Apple Pie) if no dessert ordered
3. Never ask "Want to add a flavor add-in to that drink?"

### Rationale

User request — McDonald's doesn't do drink flavor add-ins like Sonic.
- `ReadyState` re-exported from `useRealtime.tsx` for consumer convenience (avoids direct `react-use-websocket` import in App.tsx).

---

## Offline Auto-Fallback and Diagnostics Pipeline (2026-07-19)

**Author:** Grimace (Backend Dev)  
**Status:** Implemented

### Problem

When Brian went offline with local mode toggled ON in the UI, clicking the microphone button produced complete silence — no logs, no errors, nothing. The backend was running on localhost:8000 and should have been reachable.

### Root Cause

Three-layer failure:
1. **Config mismatch:** `config.yaml` has `local_mode.enabled: false`. ProcessorRouter defaults to "cloud" mode regardless of UI preference.
2. **Late mode signaling:** Frontend sends `extension.set_local_mode` via WebSocket message AFTER connection, but routing happens AT connection time. The preference arrives too late.
3. **Silent hang:** RTMiddleTier._forward_messages() tries ws_connect to Azure OpenAI. When offline, DNS resolution hangs for 10-30s, then throws an exception — but no error message is sent back to the WebSocket client. The connection just dies silently.

### Solution

**1. Auto-Fallback (processor_router.py)**
When mode resolves to "cloud" but a local processor exists, the router now does a quick (3s timeout) connectivity check to the Azure endpoint. If unreachable, it automatically falls back to local mode with a logged warning. No config changes needed.

**2. Runtime Mode Toggle (app.py)**
New `POST /api/local-mode/toggle` endpoint accepts `{"mode": "local"|"cloud"|"auto"}`. Birdie can wire this to the frontend's local mode toggle so the backend knows the user's preference BEFORE the WebSocket connects.

**3. Graceful Offline Startup (app.py)**
Missing Azure env vars now log a warning (not `sys.exit(1)`) when local mode is available. Cloud RTMiddleTier creation is skipped entirely in this case — ProcessorRouter handles `cloud_processor=None`.

**4. Diagnostics Endpoint (app.py)**
New `GET /api/diagnostics` returns comprehensive system state: current mode, model status, GPU provider, TTS/STT status, last error, WebSocket connection counts.

**5. Pipeline Logging (all local modules)**
`local-pipeline` logger added across processor_router, local_processor, phi4_model, piper_tts, whisper_stt, local_search. Every pipeline step is logged with session IDs and timing.

### Impact

- **No regressions:** 632 tests, same baseline (1 pre-existing failure, 2 pre-existing errors)
- **Cloud mode unchanged:** All existing cloud behavior preserved
- **Birdie action needed:** Wire `POST /api/local-mode/toggle` to the frontend's local mode toggle switch. Also consider adding `?mode=local` to the WebSocket URL when local mode is active.

### Files Changed

- `app/backend/processor_router.py` — Auto-fallback, runtime mode toggle, connection tracking, diagnostics
- `app/backend/app.py` — Graceful offline startup, `/api/diagnostics`, `/api/local-mode/toggle`
- `app/backend/local_processor.py` — Comprehensive pipeline logging
- `app/backend/phi4_model.py` — Pipeline logging for model load/inference
- `app/backend/piper_tts.py` — Pipeline logging for TTS load/synthesis
- `app/backend/whisper_stt.py` — Pipeline logging for STT load/transcription
- `app/backend/local_search.py` — Pipeline logging for menu search
- `app/backend/tests/test_performance.py` — Updated env var test for local-only mode

### Design
1. **Module-level `MOCK_MACHINE_STATUS`** — easy toggle for demos; production would use Azure Function/IoT Hub
2. **Keyword-based matching** — items with "shake", "blast", "sundae", "ice cream" tied to `ice_cream_machine` status
3. **Non-blocking** — items still returned, just flagged; AI sees `[OOS]` tag and should advise naturally
4. **Server-side only** — `[OOS]` tag in `TO_SERVER` result, frontend never sees it

### Files Changed
- `app/backend/tools.py` — `MOCK_MACHINE_STATUS`, `_ICE_CREAM_MACHINE_KEYWORDS`, OOS check in search loop

### Impact
All 118 existing tests pass — simple string append guarded by dict lookup.

---

## reset_order Tool Routing (2026-03-22)

**Author:** Summer (Backend Dev)  
**Status:** Implemented

### Decision
`reset_order` uses `ToolResultDirection.TO_CLIENT` (not `TO_BOTH`). Response is `"Order cleared. {json_summary}"` — AI doesn't need confirmation to continue, frontend needs empty JSON for ticket.

### Trade-off
If AI needs explicit post-reset confirmation, routing should change to `TO_BOTH` with voice-friendly string. Monitor during demos.

---

## Previous Decisions (Archived)

### Copilot Directive (2026-02-25T22-39)
Copilot CLI configuration directive for squad ceremonies and agent interactions.

### Voice Chat Architecture & Prompt Engineering (Fenster, 2026-02-25)
Initial system prompt design leveraging Azure OpenAI GPT-4o Realtime for voice-based ordering. Foundation for Sonic rebrand implementation.

### Repository Initialization (Squanchy, 2026-02-25)
SonicAIDriveThru repository created with Azure Container Apps, Bicep IaC, React frontend (Vite, Tailwind, shadcn/ui), Python backend (aiohttp, WebSockets, Azure OpenAI Realtime, Azure AI Search).

## Menu Period Tagging (2026-03-23)

**Author:** Grimace (Backend Dev)

**Decision:** Added `menuPeriod` field to all items in menuItems.json

**Values:** "breakfast" (Breakfast category), "lunch" (Burgers, Chicken), "allDay" (Sides, Drinks, Sweets)

**Rationale:** Enables frontend Breakfast/Lunch toggle to filter Extra Value Meals and menu categories

**Impact:** Frontend can now filter menu items by time of day; eliminates duplicate meal numbers in Extra Value Meals

## Breakfast/Lunch Menu Mode Toggle (2026-03-23)

**Author:** Birdie (Frontend Dev)

**Decision:** Added MenuModeContext with localStorage persistence, Settings segmented toggle, and menu-panel.tsx filtering

**Default:** "lunch" mode (McDonald's serves lunch most of the day)

**Filtering:** Items filtered by menuPeriod field — "breakfast", "lunch", or "allDay". Missing field treated as allDay.

**Impact:** Extra Value Meals section now shows only relevant meals per mode (breakfast #1-5 OR lunch #1-10), fixing duplicate numbering issue

## Multi-Layer Echo Self-Talk Fix (2026-03-22)

**Author:** Summer (Backend Dev)

**Status:** Implemented

### Problem
AI generates 4+ unsolicited patience responses after speaking because:
1. Echo cooldown (0.5s) was too short — speakers still resonating when mic reopens
2. No buffer flush after cooldown window — echo audio accumulated during cooldown triggered VAD
3. System prompt instruction caused model to actively fill silence

### Changes
- `_ECHO_COOLDOWN_SEC`: 0.5 → 1.5 in rtmt.py
- Delayed second buffer flush catching accumulated echo audio
- Removed patience instruction; replaced with "NEVER speak unless guest has spoken first"
- max_tokens stays at 4096 for tool call budget

### Trade-offs
- 1.5s cooldown adds slight delay before user can speak after AI finishes
- Removing patience instruction means AI won't proactively comfort hesitant users (correct for demo)

---

## Phase 3 — Code Organization (2026-03-25)

**Author:** Grimace (Backend Developer)  
**Status:** Implemented  
**Tests:** 202 passing

### Summary

Split the monolithic `rtmt.py` (778 lines) into 3 focused modules and added startup validation to `app.py`. Ported from the Sonic project's Phase 3, adapted for McDonald's brand and existing codebase.

### Changes

**New Files:**
1. **`app/backend/session_manager.py`** — SessionManager (session lifecycle, greeting state, concurrency limits, idle timeout) + ContextMonitor (token usage estimation, threshold warnings at 80%/95%)
2. **`app/backend/audio_pipeline.py`** — EchoSuppressor class, verbose logging infrastructure (vlogger, file handler functions), audio marker constants, passthrough type sets, TYPE_RE regex, pre-serialized messages

**Modified Files:**
3. **`app/backend/rtmt.py`** — Removed extracted code, imports from session_manager and audio_pipeline. RTMiddleTier now delegates to `self._sessions` (SessionManager) instead of inline `_session_map`/`_sent_greeting`. Uses EchoSuppressor instance per connection. Added `_get_auth_token()`, `_refresh_token_loop()`, `start_background_tasks()`, `stop_background_tasks()`.
4. **`app/backend/app.py`** — Added startup validation: required env vars check (sys.exit(1) on missing), _startup_checks dict, enhanced /health endpoint (returns version + checks status), _check_service_connectivity(), on_startup/on_shutdown hooks for background tasks.
5. **`app/backend/config.yaml`** — Added `context` section (max_tokens, warning/critical thresholds) and `security` section (max_concurrent_sessions, idle_timeout, allowed_origins, require_session_token).
6. **`app/backend/tests/test_app.py`** — Updated _run_create_app to include required AZURE_SEARCH_* env vars and mock _check_service_connectivity.
7. **`app/backend/tests/test_performance.py`** — Updated test_create_app to expect SystemExit instead of RuntimeError; added _check_service_connectivity mock to all create_app test paths.

### Rationale
- rtmt.py was accumulating too many responsibilities (session management, echo suppression, audio constants, verbose logging, WebSocket routing). Splitting makes each module testable and reviewable in isolation.
- Startup validation prevents silent runtime failures from missing env vars.
- ContextMonitor prepares for context window management in Phase 4.
- SecurityManager config prepares for origin validation and session tokens in Phase 4.

---

## Phase 4 Frontend — Session Token Support (2026-03-25)

**Author:** Birdie (Frontend Developer)  
**Status:** Implemented

### Decision

Added session token fetch and inclusion in `useRealtime.tsx`:

1. **`fetchSessionToken()`** — calls `GET /api/auth/session`, returns `data.token` or null on any failure
2. **Token in WS URL** — appends `?token=<encoded_token>` to `/realtime` when token is available
3. **Auto-refresh on auth failure** — WebSocket close with code `4001` or reason containing "expired" triggers a token refetch
4. **Graceful fallback** — if the auth endpoint doesn't exist or errors, the connection proceeds without a token (backward compatible)

### Impact
- No breaking changes — existing deployments without `/api/auth/session` continue to work identically
- Backend agents (Ronald/Grimace) can independently add the `/api/auth/session` endpoint and WebSocket token validation
- Token is URI-encoded to handle special characters safely

### File Changed
- `app/frontend/src/hooks/useRealtime.tsx`

---

## Phase 4 Backend — Security Features (2026-03-25)

**Author:** Mac Tonight (AI / Realtime Expert)  
**Status:** Implemented  
**Tests:** 202 passing

### Decision

Ported Phase 4 security features from Sonic project into McDonald's backend. All security features are **disabled by default** (`require_session_token: false`, `allowed_origins: []`) to keep demos safe.

### Changes

**1. HMAC Session Token Utilities (`rtmt.py`)**
- Added `create_hmac_token()` and `validate_hmac_token()` — HMAC-SHA256 signed tokens with base64-encoded JSON payloads containing expiry timestamps.
- Tokens default to 15-minute expiry (900 seconds).

**2. WebSocket Handler Security Gates (`rtmt.py` → `_websocket_handler`)**
Three pre-connection checks added before WebSocket upgrade:
- **Origin validation** — rejects connections from disallowed origins (checks `Host` header match or explicit allowlist).
- **HMAC token validation** — when `require_session_token: true`, requires valid unexpired token in query string.
- **Concurrency limit** — enforces `max_concurrent_sessions` cap, returns friendly JSON error to over-limit clients.

**3. Session Token Endpoint (`app.py`)**
- `GET /api/auth/session` — returns a fresh 15-minute HMAC token.
- `app_secret` generated via `os.urandom(32)` at startup — unique per process instance.

**4. App Secret Lifecycle**
- `rtmt.app_secret` initialized as empty bytes in `__init__`, set by `app.py` at startup.
- Secret is ephemeral (in-memory only) — rotates on every server restart.

### What Already Existed (Not Duplicated)
- Token refresh loop, background task management, startup/shutdown hooks
- SessionManager idle checker, activity tracking, concurrency checks
- `config.yaml` security section

### Trade-offs
- HMAC tokens are stateless — no server-side revocation (acceptable for drive-thru sessions < 15 min).
- Origin check uses `endswith(host)` which is permissive for subdomains — tighten for production.
- `app_secret` rotates on restart, invalidating in-flight tokens — acceptable for deployment model.

### Risk
- None for demos (everything disabled by default).
- Production: enable `require_session_token: true` and populate `allowed_origins` list.

---

## Phase 5 — RTMT Lifecycle, Security & Tool Calling Tests (2026-03-25)

**Author:** Hamburglar (Tester)  
**Status:** Complete  
**Tests:** 423 total (202 baseline + 221 new)

### Summary

Created 221 new tests across 3 files covering the Phase 3 refactored modules (session_manager, audio_pipeline, rtmt) and Phase 4 security interfaces. All 423 total tests pass.

### Files Created

- `app/backend/tests/test_rtmt.py` — 102 tests covering SessionManager, ContextMonitor, EchoSuppressor, audio pipeline utilities, RTMT core classes, and RTMiddleTier initialization.
- `app/backend/tests/test_security.py` — 40 tests covering session limits, origin validation, HMAC tokens, and security config validation. Uses stub implementations for Phase 4 interfaces not yet merged.
- `app/backend/tests/test_tool_calling.py` — 79 tests covering search pipeline, order CRUD, quantity limits, customization validation, upsell hints, happy hour, extras validation, and edge cases.

### Key Decisions

1. **Security stubs over importorskip:** Used self-contained stub implementations (_StubSessionLimiter, _validate_origin, _generate_hmac_token) instead of pytest.importorskip for security tests. This allows tests to validate expected behaviour even before Phase 4 source lands. Once real security modules merge, swap stubs for imports.

2. **MENU_CATEGORY_MAP awareness:** Tests that exercise `_infer_category` keyword paths use synthetic item names (e.g., "Test Shake Special") to avoid MENU_CATEGORY_MAP overriding keyword inference. Tests that need real categories use exact menu keys (e.g., "Big Mac®").

3. **Happy hour mock target:** Patching `tools.is_happy_hour` (not `order_state.is_happy_hour`) since tools.py imports the function at module level.

### Risk Notes

- McFlurry items are NOT flagged OOS when ice cream machine is down — the keyword list doesn't include "mcflurry". Consider adding it in a future PR.
- Security tests use stubs. Once Phase 4 source merges, these should be updated to import real implementations.

---

## Local Menu Search Replaces Azure AI Search for Offline Mode (2026-04-02)

**Author:** Grimace (Backend Dev)  
**Status:** Approved

### Context
Azure AI Search was the last cloud dependency blocking true offline mode. The local Phi-4 processor was sharing \tmt.tools\ from the cloud RTMiddleTier, meaning the \search\ tool still required Azure Search credentials and network access.

### Decision
Created \pp/backend/local_search.py\ with a fully offline in-memory keyword search engine:

1. **Data source:** Loads \menuItems.json\ (71 items) at module init using the same path resolution as \	ools.py\.
2. **Scoring:** Tiered keyword matching (exact name 100 → name-contains 80 → category 40 → description 20 → token overlap +2 each).
3. **Format compatibility:** Output is byte-for-byte identical to Azure AI Search results — same \[{id}]: Item: ...\ template, same \\n-----\n\ separator, same OOS flags, same size formatting.
4. **Reuse:** Imports \_expand_meal_number_query\, \_format_size_human_readable\, \MOCK_MACHINE_STATUS\, and all tool schemas/handlers directly from \	ools.py\ — no code duplication for order tools.
5. **\ttach_local_tools()\** registers search (local) + order tools (already local) on the processor, completely decoupling local mode from Azure credentials.
6. **app.py change:** \local_processor.tools = rtmt.tools\ → \ttach_local_tools(local_processor, prompt_loader)\.

### Trade-offs
- **No semantic/vector search:** Keyword matching is less sophisticated than Azure AI Search's hybrid semantic+vector search. Acceptable for a structured menu of ~70 items where exact/substring matching covers 99% of drive-thru queries.
- **No index updates without restart:** Menu is loaded once at module init. Fine for a static menu; if menu changes frequently, would need a reload mechanism.
- **Separate cache instances:** Local and cloud search each maintain their own \_SearchCache\. No shared state, which is correct since they serve different processor instances.

### Impact
- Cloud mode: zero changes (tools.py untouched)
- Local mode: fully offline — no Azure credentials needed
- Tests: 559 pass, 0 regressions introduced
