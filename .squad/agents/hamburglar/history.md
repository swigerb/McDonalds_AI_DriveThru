# Hamburglar — History

## Sessions

### 2026-03-22 — Prompt Externalization Test Suite

Created comprehensive test coverage for the new prompt externalization architecture:

- **test_prompt_loader.py** (14 tests): PromptLoader init, system prompt content (McDonald's, crew member, World Famous Fries), greeting dict/JSON, 4 tool schemas, error message rendering with Jinja2, upsell hints, delta templates with {{quantity}}, render_template(), FileNotFoundError for bad brand, ValueError for malformed YAML.
- **test_config_loader.py** (11 tests): get_config() returns dict, required sections (model, business_rules, cache, audio, connection), business_rules values (max_item_quantity=10, max_order_items=25), model temperature=0.6, type validation, reload_config() fresh load semantics.
- **test_menu_utils.py** (13 tests): normalize_size() mappings (small→Small, m→Medium, l/lg→Large, empty→empty, standard→empty, n/a→empty, whitespace), infer_category() for combos, desserts, sides, drinks, burgers, case insensitivity.
- **test_app.py** (9 new tests): PromptLoaderIntegrationTests (5) and ConfigLoaderIntegrationTests (4) — verify prompt/config integration in app context.

Used `pytest.importorskip` for graceful degradation when source modules are still being created by other agents. All pre-existing tests continue to pass.

Reference: Studied Sonic project architecture at `C:\Users\brswig\source\repos\SonicAIDriveThru` for test patterns and API signatures.

## Learnings

- PromptLoader API follows manifest-driven YAML loading: manifest.yaml → references to system_prompt.yaml, greeting.yaml, tool_schemas.yaml, error_messages.yaml, hints.yaml
- `pytest.importorskip` is essential for parallel agent workflows where test files may be created before source files
- Config loader uses module-level `_cache` with lazy loading (get_config) and force-reload (reload_config) pattern
- menu_utils uses SIZE_MAP + SIZE_ALIASES two-tier lookup; `_NO_DISPLAY_SIZES` frozenset hides standard/n/a sizes
- Error messages are Jinja2 templates requiring `render_error(key, **kwargs)` — test with actual variable substitution
- Test files should use `sys.path.append(str(Path(__file__).resolve().parents[1]))` to match existing project convention
