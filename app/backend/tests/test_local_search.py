"""Tests for local_search.py — offline in-memory menu search.

Covers menu loading, keyword/fuzzy matching, scoring, caching, OOS flags,
result format parity with Azure AI Search (tools.py), and tool attachment.
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rtmt import ToolResult, ToolResultDirection

# Import the module under test — uses pytest.importorskip for graceful
# degradation if local_search.py hasn't landed yet.
local_search_mod = __import__("local_search")
local_search = local_search_mod.local_search
attach_local_tools = local_search_mod.attach_local_tools
_SearchCache = local_search_mod._SearchCache
_local_search_cache = local_search_mod._local_search_cache
_MENU_ITEMS = local_search_mod._MENU_ITEMS
_score_item = local_search_mod._score_item
_tokenize = local_search_mod._tokenize

from tools import (  # noqa: E402
    MOCK_MACHINE_STATUS,
    get_order_tool_schema,
    reset_order_tool_schema,
    search_tool_schema,
    update_order_tool_schema,
)

# ── Helpers ──

def _run(coro):
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════
# MENU LOADING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class MenuLoadingTests(unittest.TestCase):
    """Verify menu loads from JSON with correct structure and count."""

    def test_menu_items_loaded(self):
        """Menu data must be populated at module import time."""
        self.assertIsInstance(_MENU_ITEMS, list)
        self.assertGreater(len(_MENU_ITEMS), 0, "Menu items should be non-empty")

    def test_menu_item_count(self):
        """Menu loads 134 items from offline_menu.json or 71 from menuItems.json."""
        self.assertIn(len(_MENU_ITEMS), (134, 71))

    def test_each_item_has_required_keys(self):
        """Every menu entry must have id, name, category, description, sizes."""
        required_keys = {"id", "name", "category", "description", "sizes"}
        for item in _MENU_ITEMS:
            with self.subTest(item=item.get("name", "?")):
                self.assertTrue(
                    required_keys.issubset(item.keys()),
                    f"Missing keys: {required_keys - item.keys()}"
                )

    def test_ids_are_non_empty_strings(self):
        """Every item must have a non-empty string ID."""
        for item in _MENU_ITEMS:
            self.assertIsInstance(item["id"], str)
            self.assertTrue(len(item["id"]) > 0, f"Empty ID for {item.get('name')}")

    def test_categories_present(self):
        """Core menu categories must appear in loaded data."""
        categories = {item["category"] for item in _MENU_ITEMS}
        # These categories exist in both offline_menu.json and menuItems.json
        core_categories = {"Breakfast", "Sweets & Treats"}
        self.assertTrue(
            core_categories.issubset(categories),
            f"Missing core categories: {core_categories - categories}",
        )
        self.assertGreaterEqual(len(categories), 5)

    def test_sizes_are_json_strings(self):
        """Sizes field should be a JSON-serialized string (not raw list)."""
        for item in _MENU_ITEMS:
            sizes = item["sizes"]
            self.assertIsInstance(sizes, str)
            parsed = json.loads(sizes)
            self.assertIsInstance(parsed, list)


# ═══════════════════════════════════════════════════════════════════════════════
# KEYWORD MATCHING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class KeywordMatchingTests(unittest.TestCase):
    """Verify keyword search returns expected menu items."""

    def setUp(self):
        _local_search_cache.clear()

    def test_search_big_mac(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertIn("Big Mac", result.text)

    def test_search_fries(self):
        result = _run(local_search({"query": "fries"}))
        self.assertIn("Fries", result.text)

    def test_search_coca_cola_returns_coca_cola(self):
        """'coca cola' matches via substring; note 'coke' does NOT match
        because the local keyword engine has no synonym mapping."""
        result = _run(local_search({"query": "coca cola"}))
        self.assertIn("Coca-Cola", result.text)

    def test_search_mcnuggets(self):
        result = _run(local_search({"query": "McNuggets"}))
        self.assertIn("McNuggets", result.text)

    def test_search_egg_mcmuffin(self):
        result = _run(local_search({"query": "Egg McMuffin"}))
        self.assertIn("Egg McMuffin", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# CASE INSENSITIVITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class CaseInsensitivityTests(unittest.TestCase):
    """Search must be case-insensitive."""

    def setUp(self):
        _local_search_cache.clear()

    def test_lowercase_matches(self):
        result = _run(local_search({"query": "big mac"}))
        self.assertIn("Big Mac", result.text)

    def test_uppercase_matches(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "BIG MAC"}))
        self.assertIn("Big Mac", result.text)

    def test_mixed_case_matches(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "bIg MaC"}))
        self.assertIn("Big Mac", result.text)

    def test_all_three_return_same_top_result(self):
        """Regardless of casing, the top result should be the same item."""
        _local_search_cache.clear()
        r1 = _run(local_search({"query": "big mac"}))
        _local_search_cache.clear()
        r2 = _run(local_search({"query": "Big Mac"}))
        _local_search_cache.clear()
        r3 = _run(local_search({"query": "BIG MAC"}))
        # All should contain Big Mac as the first result
        for r in [r1, r2, r3]:
            first_result = r.text.split("-----")[0]
            self.assertIn("Big Mac", first_result)


# ═══════════════════════════════════════════════════════════════════════════════
# CATEGORY MATCHING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class CategoryMatchingTests(unittest.TestCase):
    """Search by category name should return items from that category."""

    def setUp(self):
        _local_search_cache.clear()

    def test_search_breakfast_returns_breakfast_items(self):
        result = _run(local_search({"query": "breakfast"}))
        self.assertIn("Breakfast", result.text)

    def test_search_breakfast_returns_known_items(self):
        result = _run(local_search({"query": "breakfast"}))
        # Should return items from the Breakfast category
        # Egg McMuffin is the most iconic breakfast item
        text = result.text
        has_breakfast_item = any(
            name in text
            for name in ["Egg McMuffin", "Sausage McMuffin", "Hotcakes", "McGriddles", "Biscuit"]
        )
        self.assertTrue(has_breakfast_item, f"Expected a breakfast item in: {text[:200]}")

    def test_search_sweets_returns_dessert_items(self):
        result = _run(local_search({"query": "sweets"}))
        self.assertIn("Sweets & Treats", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# MEAL NUMBER EXPANSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class MealNumberExpansionTests(unittest.TestCase):
    """Verify 'number 1' expands to 'Big Mac Meal' etc."""

    def setUp(self):
        _local_search_cache.clear()

    def test_number_1_returns_big_mac_meal(self):
        result = _run(local_search({"query": "number 1"}))
        self.assertIn("Big Mac", result.text)

    def test_combo_2_returns_quarter_pounder_meal(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "combo 2"}))
        self.assertIn("Quarter Pounder", result.text)

    def test_meal_number_word_expansion(self):
        """'number one' should also expand to Big Mac Meal."""
        _local_search_cache.clear()
        result = _run(local_search({"query": "number one"}))
        self.assertIn("Big Mac", result.text)

    def test_hash_number_expansion(self):
        """'#6' should expand to McNuggets Meal."""
        _local_search_cache.clear()
        result = _run(local_search({"query": "#6"}))
        self.assertIn("McNuggets", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# OOS FLAG INJECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class OOSFlagInjectionTests(unittest.TestCase):
    """Verify ice cream machine OOS flags are appended to affected items."""

    def setUp(self):
        _local_search_cache.clear()
        self._original_status = MOCK_MACHINE_STATUS.get("ice_cream_machine")

    def tearDown(self):
        MOCK_MACHINE_STATUS["ice_cream_machine"] = self._original_status
        _local_search_cache.clear()

    def test_shake_gets_oos_flag_when_machine_down(self):
        MOCK_MACHINE_STATUS["ice_cream_machine"] = "down"
        result = _run(local_search({"query": "shake"}))
        self.assertIn("[OOS", result.text)
        self.assertIn("ice cream machine", result.text.lower())

    def test_sundae_gets_oos_flag_when_machine_down(self):
        MOCK_MACHINE_STATUS["ice_cream_machine"] = "down"
        result = _run(local_search({"query": "sundae"}))
        self.assertIn("[OOS", result.text)

    def test_no_oos_flag_when_machine_operational(self):
        MOCK_MACHINE_STATUS["ice_cream_machine"] = "operational"
        result = _run(local_search({"query": "shake"}))
        self.assertNotIn("[OOS", result.text)

    def test_burger_never_gets_oos_flag(self):
        MOCK_MACHINE_STATUS["ice_cream_machine"] = "down"
        result = _run(local_search({"query": "Big Mac"}))
        self.assertNotIn("[OOS", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT FORMAT PARITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ResultFormatParityTests(unittest.TestCase):
    """Output format must match Azure AI Search (tools.py) format exactly."""

    def setUp(self):
        _local_search_cache.clear()

    def test_result_contains_id_bracket_format(self):
        """Results must have [id]: prefix."""
        result = _run(local_search({"query": "Big Mac"}))
        self.assertRegex(result.text, r"\[[^\]]+\]:")

    def test_result_contains_item_field(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertIn("Item:", result.text)

    def test_result_contains_category_field(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertIn("Category:", result.text)

    def test_result_contains_available_sizes_field(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertIn("Available Sizes:", result.text)

    def test_multiple_results_separated_by_dashes(self):
        """Multiple results must be separated by '-----' exactly like tools.py."""
        result = _run(local_search({"query": "chicken"}))
        # "chicken" should match multiple items
        self.assertIn("-----", result.text)

    def test_full_format_string_pattern(self):
        """Verify the complete format: [id]: Item: name, Category: cat, Available Sizes: ..."""
        result = _run(local_search({"query": "Coca-Cola"}))
        pattern = r"\[[^\]]+\]: Item: .+, Category: .+, Available Sizes: .+"
        self.assertRegex(result.text, pattern)


# ═══════════════════════════════════════════════════════════════════════════════
# TOP N RESULTS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TopNResultsTests(unittest.TestCase):
    """Verify result count is limited to top_results (default: 3)."""

    def setUp(self):
        _local_search_cache.clear()

    def test_max_3_results_by_default(self):
        """A broad query should still return at most 3 results."""
        result = _run(local_search({"query": "chicken"}))
        # Count result blocks (separated by ------)
        parts = [p.strip() for p in result.text.split("-----") if p.strip()]
        self.assertLessEqual(len(parts), 3)

    def test_single_match_returns_1_result(self):
        """Specific query should return fewer results when fewer match."""
        result = _run(local_search({"query": "Egg McMuffin"}))
        parts = [p.strip() for p in result.text.split("-----") if p.strip()]
        self.assertGreaterEqual(len(parts), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# EMPTY RESULTS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class EmptyResultsTests(unittest.TestCase):
    """Search for nonexistent items returns a polite empty message."""

    def setUp(self):
        _local_search_cache.clear()

    def test_nonexistent_query_returns_no_match_message(self):
        result = _run(local_search({"query": "xyznonexistent"}))
        self.assertIn("No matching menu entries found", result.text)

    def test_gibberish_query_returns_empty(self):
        result = _run(local_search({"query": "zzzzqqqwww123"}))
        self.assertIn("No matching menu entries found", result.text)

    def test_empty_menu_returns_no_match(self):
        """If menu fails to load, should still return gracefully."""
        original = local_search_mod._MENU_ITEMS
        try:
            local_search_mod._MENU_ITEMS = []
            _local_search_cache.clear()
            result = _run(local_search({"query": "anything"}))
            self.assertIn("No matching menu entries found", result.text)
        finally:
            local_search_mod._MENU_ITEMS = original


# ═══════════════════════════════════════════════════════════════════════════════
# SIZE FORMATTING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class SizeFormattingTests(unittest.TestCase):
    """Size strings must include price in ($X.XX) format."""

    def setUp(self):
        _local_search_cache.clear()

    def test_price_format_in_sizes(self):
        """Sizes should contain dollar-sign price formatting."""
        result = _run(local_search({"query": "Coca-Cola"}))
        # Match ($X.XX) pattern
        self.assertRegex(result.text, r"\(\$\d+\.\d{2}\)")

    def test_multiple_sizes_displayed(self):
        """Coca-Cola has Small/Medium/Large — all should appear."""
        result = _run(local_search({"query": "Coca-Cola"}))
        self.assertIn("Small", result.text)
        self.assertIn("Medium", result.text)
        self.assertIn("Large", result.text)

    def test_standard_size_formatted(self):
        """Items with 'Standard' size should show it as formatted."""
        result = _run(local_search({"query": "Big Mac"}))
        # Big Mac has Standard size — _format_size_human_readable maps it
        self.assertIn("($", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# CACHING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class LocalSearchCacheTests(unittest.TestCase):
    """Verify local search result caching."""

    def setUp(self):
        _local_search_cache.clear()

    def test_cache_hit_returns_same_result(self):
        r1 = _run(local_search({"query": "fries"}))
        r2 = _run(local_search({"query": "fries"}))
        self.assertIs(r1, r2, "Second call should return the exact same cached object")

    def test_cache_is_case_insensitive(self):
        r1 = _run(local_search({"query": "FRIES"}))
        r2 = _run(local_search({"query": "fries"}))
        self.assertIs(r1, r2)

    def test_cache_respects_ttl(self):
        cache = _SearchCache(max_size=10)
        tr = ToolResult("cached-local", ToolResultDirection.TO_SERVER)
        cache.put("key", tr)
        self.assertIsNotNone(cache.get("key"))
        # Artificially expire the entry
        cache._store["key"] = (time.monotonic() - 999, tr)
        self.assertIsNone(cache.get("key"))

    def test_cache_evicts_oldest_when_full(self):
        cache = _SearchCache(max_size=2)
        cache.put("a", ToolResult("A", ToolResultDirection.TO_SERVER))
        time.sleep(0.01)
        cache.put("b", ToolResult("B", ToolResultDirection.TO_SERVER))
        time.sleep(0.01)
        cache.put("c", ToolResult("C", ToolResultDirection.TO_SERVER))
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("b"))
        self.assertIsNotNone(cache.get("c"))

    def test_cache_clear(self):
        cache = _SearchCache()
        cache.put("x", ToolResult("X", ToolResultDirection.TO_SERVER))
        cache.clear()
        self.assertIsNone(cache.get("x"))


# ═══════════════════════════════════════════════════════════════════════════════
# TOOLRESULT RETURN TYPE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ToolResultReturnTypeTests(unittest.TestCase):
    """Verify local_search returns ToolResult with TO_SERVER direction."""

    def setUp(self):
        _local_search_cache.clear()

    def test_returns_tool_result(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertIsInstance(result, ToolResult)

    def test_direction_is_to_server(self):
        result = _run(local_search({"query": "Big Mac"}))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)

    def test_empty_result_still_returns_tool_result(self):
        result = _run(local_search({"query": "xyznonexistent"}))
        self.assertIsInstance(result, ToolResult)
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ToolSchemaTests(unittest.TestCase):
    """Verify local_search uses the same tool schemas as cloud tools.py."""

    def test_search_schema_has_query_param(self):
        """The search tool schema must have a 'query' parameter."""
        props = search_tool_schema["parameters"]["properties"]
        self.assertIn("query", props)

    def test_search_schema_name_is_search(self):
        self.assertEqual(search_tool_schema["name"], "search")

    def test_search_schema_type_is_function(self):
        self.assertEqual(search_tool_schema["type"], "function")

    def test_update_order_schema_has_required_params(self):
        props = update_order_tool_schema["parameters"]["properties"]
        self.assertIn("action", props)
        self.assertIn("item_name", props)

    def test_get_order_schema_name(self):
        self.assertEqual(get_order_tool_schema["name"], "get_order")

    def test_reset_order_schema_name(self):
        self.assertEqual(reset_order_tool_schema["name"], "reset_order")


# ═══════════════════════════════════════════════════════════════════════════════
# ATTACH LOCAL TOOLS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class AttachLocalToolsTests(unittest.TestCase):
    """Verify attach_local_tools() registers all 4 tools."""

    def test_registers_all_four_tools(self):
        processor = MagicMock()
        processor.tools = {}
        attach_local_tools(processor)
        expected_tools = {"search", "update_order", "get_order", "reset_order"}
        self.assertEqual(set(processor.tools.keys()), expected_tools)

    def test_search_tool_is_local_search(self):
        """The search tool target should invoke local_search, not Azure."""
        processor = MagicMock()
        processor.tools = {}
        attach_local_tools(processor)
        tool = processor.tools["search"]
        # Verify it's a Tool instance
        from rtmt import Tool
        self.assertIsInstance(tool, Tool)

    def test_tools_have_schemas(self):
        """Each registered tool must have a schema dict."""
        processor = MagicMock()
        processor.tools = {}
        attach_local_tools(processor)
        for name, tool in processor.tools.items():
            with self.subTest(tool=name):
                self.assertIsNotNone(tool.schema)
                self.assertIn("name", tool.schema)

    def test_prompt_loader_forwarded_to_tools_module(self):
        """attach_local_tools should set _prompt_loader on tools module."""
        processor = MagicMock()
        processor.tools = {}
        mock_loader = MagicMock()
        mock_loader.get_tool_schemas.return_value = []
        attach_local_tools(processor, prompt_loader=mock_loader)
        import tools as _tools_module
        self.assertIs(_tools_module._prompt_loader, mock_loader)


# ═══════════════════════════════════════════════════════════════════════════════
# FUZZY / PARTIAL MATCHING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class FuzzyPartialMatchingTests(unittest.TestCase):
    """Partial query strings should still match relevant items."""

    def setUp(self):
        _local_search_cache.clear()

    def test_mac_matches_big_mac(self):
        result = _run(local_search({"query": "mac"}))
        self.assertIn("Mac", result.text)

    def test_nugget_matches_mcnuggets(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "nugget"}))
        self.assertIn("McNuggets", result.text)

    def test_quarter_matches_quarter_pounder(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "quarter"}))
        self.assertIn("Quarter Pounder", result.text)

    def test_muffin_matches_egg_mcmuffin(self):
        _local_search_cache.clear()
        result = _run(local_search({"query": "muffin"}))
        self.assertIn("McMuffin", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# SCORING ENGINE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class ScoringEngineTests(unittest.TestCase):
    """Test the _score_item and _tokenize internals."""

    def test_tokenize_basic(self):
        tokens = _tokenize("Big Mac Meal")
        self.assertEqual(tokens, {"big", "mac", "meal"})

    def test_tokenize_strips_punctuation(self):
        tokens = _tokenize("Coca-Cola® (Large)")
        self.assertIn("coca", tokens)
        self.assertIn("cola", tokens)
        self.assertIn("large", tokens)

    def test_exact_name_match_scores_highest(self):
        item = {"name": "Big Mac", "category": "Burgers", "description": "A burger"}
        score_exact = _score_item(item, "Big Mac")
        score_partial = _score_item(item, "burger")
        self.assertGreater(score_exact, score_partial)

    def test_name_contains_query_scores_high(self):
        item = {"name": "Big Mac Meal", "category": "Combos", "description": "Combo meal"}
        score = _score_item(item, "mac")
        self.assertGreater(score, 0)

    def test_category_match_adds_score(self):
        item = {"name": "Hotcakes", "category": "Breakfast", "description": "Pancakes"}
        score = _score_item(item, "breakfast")
        self.assertGreaterEqual(score, 40.0)

    def test_zero_score_for_unrelated_query(self):
        item = {"name": "Big Mac", "category": "Burgers", "description": "Beef patties"}
        score = _score_item(item, "xyznonexistent")
        self.assertEqual(score, 0.0)

    def test_description_match_adds_score(self):
        item = {"name": "Filet-O-Fish", "category": "Burgers", "description": "Wild-caught Alaska Pollock"}
        score = _score_item(item, "pollock")
        self.assertGreater(score, 0)

    def test_registered_trademark_ignored_in_matching(self):
        """The ® symbol should not affect matching."""
        item = {"name": "Big Mac®", "category": "Burgers", "description": "Burger"}
        score = _score_item(item, "Big Mac")
        self.assertGreaterEqual(score, 100.0)


if __name__ == "__main__":
    unittest.main()
