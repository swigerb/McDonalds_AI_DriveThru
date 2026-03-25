# Mac Tonight — History

## Sessions

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
