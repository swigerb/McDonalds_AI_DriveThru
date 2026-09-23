# Hamburglar — History

## Sessions

### 2026-08-06 — Route 44 Regression Tests

**Added:**
- `test_order_state.py::test_route44_not_recognized_as_mcdonalds_size` — asserts all Sonic size aliases (rt44, rt 44, route 44, 44, 44oz) are silently dropped, no "Route 44" prefix appears
- `test_tool_calling.py::test_route44_not_valid_mcdonalds_size` — asserts `_format_size_human_readable` has no explicit Route 44 mapping
- Updated existing `test_formatted_display_labels_handle_special_sizes` to assert rejection (was asserting acceptance)
- Updated `test_display_formatting_for_various_sizes` cases to expect "" for Route 44 variants

**Validation:** 729 passed, 0 failed; ruff clean; npm build ✓; npm test 15 passed.

### 2026-03-22 — Prompt Externalization Test Suite

Created comprehensive test coverage for the new prompt externalization architecture:

- **test_prompt_loader.py** (14 tests): PromptLoader init, system prompt content (McDonald's, crew member, World Famous Fries), greeting dict/JSON, 4 tool schemas, error message rendering with Jinja2, upsell hints, delta templates with {{quantity}}, render_template(), FileNotFoundError for bad brand, ValueError for malformed YAML.
- **test_config_loader.py** (11 tests): get_config() returns dict, required sections (model, business_rules, cache, audio, connection), business_rules values (max_item_quantity=10, max_order_items=25), model temperature=0.6, type validation, reload_config() fresh load semantics.
- **test_menu_utils.py** (13 tests): normalize_size() mappings (small→Small, m→Medium, l/lg→Large, empty→empty, standard→empty, n/a→empty, whitespace), infer_category() for combos, desserts, sides, drinks, burgers, case insensitivity.
- **test_app.py** (9 new tests): PromptLoaderIntegrationTests (5) and ConfigLoaderIntegrationTests (4) — verify prompt/config integration in app context.

Used `pytest.importorskip` for graceful degradation when source modules are still being created by other agents. All pre-existing tests continue to pass.

Reference: Studied Sonic project architecture at `C:\Users\brswig\source\repos\SonicAIDriveThru` for test patterns and API signatures.

### 2026-03-22 — Phase 5: RTMT Lifecycle, Security & Tool Calling Tests

Created 221 new tests across 3 files covering Phase 3/4 modules:

- **test_rtmt.py** (102 tests): SessionManager creation (8), cleanup (8), concurrency (6), greeting (7), idle timeout (4), emit identifiers (3), ContextMonitor (12), EchoSuppressor (13), TypeRegex (5), PreSerialized (2), ToolResult (5), ToolResultDirection (3), Tool (1), RTToolCall (3), RTMiddleTier init (12), Passthrough sets (8).
- **test_security.py** (40 tests): StubSessionLimiter (9), SessionManager-as-limiter (5), Origin validation (10), HMAC tokens (11), Security config (5).
- **test_tool_calling.py** (79 tests): Search formatting (4), empty results (1), error handling (2), cache (6), OOS annotations (3), update_order add (5), remove (2), quantity limits (5), customization validation (6), get_order (4), reset_order (3), tax calculation (2), upsell hints (5), combo validation (2), happy hour (3), format_size (5), is_extra (3), infer_category (8), extras validation (3), edge cases (7).

All 423 total tests pass (221 new + 202 existing).

### 2026-03-22 — Phase 6: Offline Mode Component Tests

Created 137 new tests across 4 files covering the offline/local mode architecture:

- **test_processor_router.py** (23 tests): Router creation cloud-only (2), both processors (2), default mode logic (2), mode resolution via query params (5), WebSocket routing delegation (4), attach_to_app (1), background task lifecycle (4), property accessors (3).
- **test_phi4_model.py** (31 tests): Init/defaults (4), auto-device detection CUDA→DirectML→CPU→none (4), model loading/unloading (7), inference async generator (3), PCM conversion (2), prompt building (4), tool call parsing (7).
- **test_piper_tts.py** (42 tests): Init/defaults (6), voice metadata dict (5), length_scale clamping (4), voice switching valid/invalid/lazy (5), loading/unloading (5), synthesis streaming/complete (7), sentence chunking (5), PCM resampling (5).
- **test_local_processor.py** (41 tests): Init/config (6), LOCAL_MODE_AVAILABLE flag (4), status properties (5), WebSocket protocol messages (10), model loading errors (1), background tasks (4), tool schemas (3), _compute_energy (4), _downsample_24k_to_16k (5).

All 560 total tests pass (137 new + 423 existing), zero regressions.

## Learnings

- PromptLoader API follows manifest-driven YAML loading: manifest.yaml → references to system_prompt.yaml, greeting.yaml, tool_schemas.yaml, error_messages.yaml, hints.yaml
- `pytest.importorskip` is essential for parallel agent workflows where test files may be created before source files
- Config loader uses module-level `_cache` with lazy loading (get_config) and force-reload (reload_config) pattern
- menu_utils uses SIZE_MAP + SIZE_ALIASES two-tier lookup; `_NO_DISPLAY_SIZES` frozenset hides standard/n/a sizes
- Error messages are Jinja2 templates requiring `render_error(key, **kwargs)` — test with actual variable substitution
- Test files should use `sys.path.append(str(Path(__file__).resolve().parents[1]))` to match existing project convention
- MENU_CATEGORY_MAP from menuItems.json overrides keyword-based `_infer_category()` — tests for customization validation and category inference must use item names NOT in the map (or use exact map keys like "Big Mac®") to exercise specific paths
- `is_happy_hour` is imported by tools.py at module level — patch `tools.is_happy_hour` not `order_state.is_happy_hour` for tool tests
- `_ICE_CREAM_MACHINE_KEYWORDS` only covers ("shake", "blast", "sundae", "ice cream") — "mcflurry" is NOT in this list, so McFlurry items aren't flagged OOS when ice cream machine is down
- Security test stubs (_StubSessionLimiter, _validate_origin, _generate_hmac_token) allow tests to pass before Phase 4 source modules land; swap for real imports once available
- EchoSuppressor cooldown boundary: `loop_time < cooldown_end` means at exactly the boundary, suppression is OFF
- For `async for msg in ws` WebSocket iteration in tests, use a custom `_AsyncIter` adapter wrapping a list — `MagicMock(return_value=iter(...))` on `__aiter__` does NOT work with `async for`
- ProcessorRouter imports `asyncio` inside method bodies (lazy import) — patch `asyncio.ensure_future` directly, not `processor_router.asyncio.ensure_future`
- PiperTTSEngine.set_voice() calls unload→load internally — must patch both `Path.exists` and `_PIPER_AVAILABLE` + `_PiperVoice.load` for the full chain to succeed in tests
- Phi4ModelManager._load_onnxruntime_genai() tries CUDA→DirectML→CPU import priority — mock with `builtins.__import__` side_effect that selectively raises ImportError
- LocalPhi4Processor uses module-level `LOCAL_MODE_AVAILABLE` flag set at import time — patch `local_processor.LOCAL_MODE_AVAILABLE` at test time, not the import chain
- WhisperSTTEngine._detect_device() tries torch→ctranslate2→CPU priority — mock with `builtins.__import__` side_effect that selectively raises ImportError (same pattern as Phi4)
- WhisperSTTEngine.transcribe() runs _sync_transcribe via run_in_executor — mock model.transcribe return as `(iter([segments]), info)` tuple
- LocalPhi4Processor._process_utterance() creates transcription_task via asyncio.create_task for parallel STT+Phi4 — test parallelism by gating Phi-4 with Events
- To test code inside `_forward_messages()` nested functions (like voice change handler), mock the full aiohttp.ClientSession→ws_connect→target_ws chain with async context managers (MagicMock + AsyncMock for __aenter__/__aexit__), use `_AsyncIter` for WebSocket iteration, and mock `rtmt._sessions` to skip greeting/session lifecycle noise
- The `continue` in the voice handler is at the `if ext_msg.get("type")` level, not inside the valid-voice check — so ALL extension.set_voice messages are consumed (valid or not), never forwarded to OpenAI

## Team Updates (2026-04-02T16:30Z)

### Offline Mode Phase Completion
- ✅ **Phase 6 (Hamburglar):** Test coverage complete — 137 new tests across 4 modules (processor router, phi4, piper, local processor)
- **Decisions Merged:** #39 captured (offline mode test patterns)
- **Tests:** 560 passing (137 new + 423 existing), zero regressions
- **Key Patterns:** Custom `_AsyncIter` for WebSocket mocking, module-boundary patching, lazy import handling, state initialization guard
- **Next:** Full test coverage validates offline mode architecture before demo

### Whisper STT Test Coverage
- ✅ **test_whisper_stt.py** (37 tests): WHISPER_AVAILABLE flag (3), init/config (4), device detection CUDA→ctranslate2→CPU (5), model loading/idempotent/auto-detect (4), unloading (2), properties (2), transcription PCM→float32/segments/executor/vad/language/beam (7), short audio guard (4), error handling/lazy-load/empty-segments (4), constants (2)
- ✅ **test_local_processor.py** (6 new tests): Transcription message sent to WS, parallel execution verification, graceful skip without STT, STT unloaded on stop, STT failure doesn't crash pipeline
- **Tests:** 671+ passing (43 new + existing), zero regressions from Whisper changes

## Sonic parity — item 1 mutation checks (2026-09-22)
- 7/7 mutants killed on rtmt.py bootstrap/voice-lock/greeting logic (no bootstrap frame, voice_locked ignored, picker not deferred, lock never set, bootstrap ack greets, greeting skips wait, bootstrap lacks transcription).
- Strengthened greeting-wait test with a delayed session.updated ack + timeline so the ordering is actually observable.

## Sonic parity — item 2 mutation check (2026-09-22)
- 13 mutants on rtmt.py / app.py / config.yaml / main.bicep, 13 killed. 2i ("off" not treated as a disabled value) first SURVIVED — it was only visible as a spurious warning — so added `test_off_is_a_documented_value_not_a_typo` (assertNoLogs) and it was killed.

## Sonic parity — item 3 mutation check (2026-09-22)
- 14 mutants (voices.ts ×6 incl. BE-side parity checks, settings.tsx, App.tsx, rtmt ×3, config.yaml, main.parameters.json, app.py), 14 killed. App.tsx seed covered by an `?raw` source assertion in voice-picker.test.tsx (rendering App is too heavy for a unit test).
- Runner fix: subprocess output decoded as utf-8 (vitest prints ✓/×; cp1252 crashed the runner).

## Sonic parity — item 4 mutation check (2026-09-22)
- 15 mutants (guard/fallback/tool-error seams in rtmt.py), 15 killed. 4j (bootstrap builder without its own event_id) and 4l (voice update untracked) first SURVIVED — `guard.track` stamps anyway, and no test rejected a voice update. Added `test_builders_stamp_their_own_event_ids` and `test_rejected_voice_change_is_recovered_and_tools_kept`; both killed.
- Tool-error tests (`test_tool_errors.py`) fail 4/5 against pre-fix rtmt (verified via stash).

## Sonic parity — item 5 mutation check (2026-09-22)
- 16 mutants (smoke_realtime.py ×13, azure.yaml continueOnError, ps1/sh exit code), 16 killed. Tests drive the real smoke functions against an in-process fake GA endpoint (`EchoGA`) that rejects beta keys like GA does — so sending the raw browser session (mutant 5g) is caught.

## Sonic parity — item 6 mutation check (2026-09-22)
- 23 mutants (rtmt ×4, config.yaml, session_manager ×2, useRealtime ×11, status-message ×2, App.tsx ×3), 23 killed. 6a (`ws_compression` default flipped to True) first SURVIVED because config.yaml always supplies the key; added `test_compression_stays_off_when_config_omits_the_key` (loads a fresh rtmt copy with an empty connection config) and it was killed.
- Runner needs PYTHONIOENCODING=utf-8 when printing vitest's ❯ glyph.

## Sonic parity — item 7 mutation check (2026-09-22)
- 9 mutants (main.parameters.json ×3, main.bicep ×5, azure.yaml ×1), 9 killed — incl. un-conditioning the openAi module, flipping the reuse default, and adding a non-role declaration scoped to the shared OpenAI RG. New tests fail 4/6 against the pre-fix parameters file (stash check).

## Round 3 — L1 mutation check (2026-09-23)
- 10 mutants (processor_router ×6, app.py ×2, rtmt ×2), 10 killed. The behavioural test alone (AST scan deselected) kills `compress=True` on the local fast path — it reproduces the real 1002, not just the kwarg. The scan asserts it found >= 6 constructions so an empty scan can't pass.

## Round 3 — R3 mutation check (2026-09-23)
- 6 mutants (es/fr/ja/en locale values, fr key removal, a Contoso string in status-message.tsx), 6 killed. Runner needs `encoding=utf-8` on subprocess output (vitest glyphs crash cp1252).

## Round 3 — R2 mutation check (2026-09-23)
- 19 mutants on `scripts/smoke_realtime.py` (similarity gate, threshold 0.5/0.95, phrase back in a user turn / dropped from instructions, empty check, case/order normalisation, check_transcription bypassing the judge, credential order/pinning/fallback/continue-on-failure/first-line errors, CLI>env>azd precedence, azd skipped with explicit endpoint, identity not passed through run/main), 19 killed. The canned answered transcript is the one Sonic's keyword check passed on.
