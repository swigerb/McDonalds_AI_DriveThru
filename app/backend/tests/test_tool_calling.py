"""Tests for the search → tool result → response pipeline (tools.py),
order management, upsell hints, and menu utilities.

Covers the core RAG flow: search query → Azure AI Search → formatted result → AI,
plus order CRUD, quantity limits, caching, and upsell logic.
"""

import asyncio
import json
import math
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from order_state import order_state_singleton
from rtmt import ToolResult, ToolResultDirection
from tools import (
    _SearchCache,
    _search_cache,
    _is_extra_item,
    _format_size_human_readable,
    _infer_category,
    validate_customization,
    search,
    update_order,
    get_order,
    reset_order,
    MAX_QUANTITY_PER_ITEM,
    MAX_TOTAL_ITEMS,
    MOCK_MACHINE_STATUS,
)


# ── Helpers ──

def _run(coro):
    return asyncio.run(coro)


def _make_mock_search_client(records):
    """Create a mock SearchClient that returns an async iterable of records."""
    client = AsyncMock()

    async def _fake_search(**kwargs):
        async def _async_iter():
            for r in records:
                yield r
        return _async_iter()

    client.search = _fake_search
    return client


def _make_session():
    """Create a fresh session and return its ID."""
    order_state_singleton.sessions = {}
    return order_state_singleton.create_session()


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH TOOL TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class SearchQueryFormattingTests(unittest.TestCase):
    """Test search query formatting and result parsing."""

    def setUp(self):
        _search_cache.clear()

    def test_formats_results_with_item_name_and_category(self):
        records = [
            {"id": "1", "name": "Big Mac", "category": "Burgers & Sandwiches",
             "description": "A classic McDonald's burger", "sizes": '[{"size":"Standard","price":"5.99"}]'},
        ]
        client = _make_mock_search_client(records)
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "big mac"}))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("Big Mac", result.text)
        self.assertIn("Burgers & Sandwiches", result.text)
        self.assertIn("[1]", result.text)

    def test_sizes_json_formatted_human_readable(self):
        records = [
            {"id": "1", "name": "Coca-Cola", "category": "Drinks",
             "sizes": json.dumps([{"size": "Medium", "price": "1.99"}, {"size": "Large", "price": "2.49"}])},
        ]
        client = _make_mock_search_client(records)
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "coke"}))
        self.assertIn("Medium ($1.99)", result.text)
        self.assertIn("Large ($2.49)", result.text)

    def test_non_json_sizes_displayed_as_is(self):
        records = [{"id": "1", "name": "Apple Pie", "category": "Desserts", "sizes": "One Size"}]
        client = _make_mock_search_client(records)
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "pie"}))
        self.assertIn("One Size", result.text)

    def test_results_separated_by_dashes(self):
        records = [
            {"id": "1", "name": "Big Mac", "category": "Burgers", "sizes": "N/A"},
            {"id": "2", "name": "Quarter Pounder", "category": "Burgers", "sizes": "N/A"},
        ]
        client = _make_mock_search_client(records)
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "burgers"}))
        self.assertIn("-----", result.text)


class SearchEmptyResultTests(unittest.TestCase):
    """Test empty search results handling."""

    def setUp(self):
        _search_cache.clear()

    def test_empty_results_returns_fallback(self):
        client = _make_mock_search_client([])
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "nonexistent"}))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("No matching menu entries found", result.text)


class SearchErrorHandlingTests(unittest.TestCase):
    """Test search error and timeout handling."""

    def setUp(self):
        _search_cache.clear()

    def test_http_error_returns_friendly_message(self):
        from azure.core.exceptions import HttpResponseError
        client = AsyncMock()
        client.search = AsyncMock(side_effect=HttpResponseError(message="Service unavailable"))
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "test"}))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertTrue("can't reach" in result.text.lower() or "sorry" in result.text.lower())

    def test_field_mismatch_retries_with_fallback(self):
        from azure.core.exceptions import HttpResponseError
        call_count = 0

        async def _search_fallback(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HttpResponseError(message="Could not find a property named 'sizes'")
            async def _iter():
                yield {"id": "1", "description": "fallback result"}
            return _iter()

        client = AsyncMock()
        client.search = _search_fallback
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "item"}))
        self.assertEqual(call_count, 2)
        self.assertIn("[1]", result.text)


class SearchCacheTests(unittest.TestCase):
    """Test search result caching."""

    def setUp(self):
        _search_cache.clear()

    def test_cache_hit_returns_same_result(self):
        records = [{"id": "1", "name": "Big Mac", "category": "Burgers", "sizes": "N/A"}]
        client = _make_mock_search_client(records)
        r1 = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "big mac"}))
        r2 = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "big mac"}))
        self.assertEqual(r1.text, r2.text)

    def test_cache_case_insensitive(self):
        records = [{"id": "1", "name": "Fries", "category": "Sides", "sizes": "N/A"}]
        client = _make_mock_search_client(records)
        _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "FRIES"}))
        r2 = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "  fries  "}))
        self.assertIn("Fries", r2.text)

    def test_cache_respects_ttl(self):
        cache = _SearchCache(max_size=10)
        tr = ToolResult("cached", ToolResultDirection.TO_SERVER)
        cache.put("key", tr)
        self.assertIsNotNone(cache.get("key"))
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

    def test_cache_get_nonexistent_returns_none(self):
        cache = _SearchCache()
        self.assertIsNone(cache.get("does-not-exist"))


class SearchOOSAnnotationTests(unittest.TestCase):
    """Test out-of-stock annotations for ice cream machine items."""

    def setUp(self):
        _search_cache.clear()

    def test_shake_flagged_oos_when_machine_down(self):
        records = [{"id": "1", "name": "Vanilla Shake", "category": "Shakes", "sizes": "N/A"}]
        client = _make_mock_search_client(records)
        with patch.dict(MOCK_MACHINE_STATUS, {"ice_cream_machine": "down"}):
            result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "shake"}))
        self.assertIn("OOS", result.text)
        self.assertIn("Ice cream machine", result.text)

    def test_non_ice_cream_item_not_flagged(self):
        records = [{"id": "1", "name": "Big Mac", "category": "Burgers", "sizes": "N/A"}]
        client = _make_mock_search_client(records)
        result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "big mac"}))
        self.assertNotIn("OOS", result.text)

    def test_sundae_flagged_oos_when_machine_down(self):
        records = [{"id": "1", "name": "Hot Fudge Sundae", "category": "Desserts", "sizes": "N/A"}]
        client = _make_mock_search_client(records)
        with patch.dict(MOCK_MACHINE_STATUS, {"ice_cream_machine": "down"}):
            result = _run(search(client, "cfg", "id", "description", "embedding", False, {"query": "sundae"}))
        self.assertIn("OOS", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER MANAGEMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class UpdateOrderAddTests(unittest.TestCase):
    """Test update_order with add action."""

    def test_add_valid_item(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        self.assertIn("Big Mac", result.text)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 1)

    def test_add_multiple_quantity(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 3, "price": 3.79,
        }, sid))
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(summary.items[0].quantity, 3)

    def test_add_zero_price_rejected(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 1, "price": 0.0,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 0)

    def test_add_negative_price_rejected(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 1, "price": -1.0,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)

    def test_add_item_with_size_display(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Coca-Cola",
            "size": "large", "quantity": 1, "price": 2.49,
        }, sid))
        self.assertIn("Coca-Cola", result.text)


class UpdateOrderRemoveTests(unittest.TestCase):
    """Test update_order with remove action."""

    def test_remove_existing_item(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 2, "price": 5.99,
        }, sid))
        result = _run(update_order({
            "action": "remove", "item_name": "Big Mac",
            "size": "standard", "quantity": 1,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(summary.items[0].quantity, 1)

    def test_remove_all_clears_item(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 1, "price": 3.79,
        }, sid))
        _run(update_order({
            "action": "remove", "item_name": "Fries",
            "size": "medium", "quantity": 1,
        }, sid))
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 0)


class UpdateOrderQuantityLimitTests(unittest.TestCase):
    """Test per-item and total order quantity limits."""

    def test_per_item_limit_exceeded(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": MAX_QUANTITY_PER_ITEM + 1, "price": 5.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 0)

    def test_per_item_limit_exact_succeeds(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": MAX_QUANTITY_PER_ITEM, "price": 5.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)

    def test_incremental_add_over_limit_rejected(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": MAX_QUANTITY_PER_ITEM - 1, "price": 5.99,
        }, sid))
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 2, "price": 5.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(summary.items[0].quantity, MAX_QUANTITY_PER_ITEM - 1)

    def test_total_order_limit_exceeded(self):
        sid = _make_session()
        for i in range(MAX_TOTAL_ITEMS):
            order_state_singleton.handle_order_update(sid, "add", f"Item{i}", "standard", 1, 1.0)
        result = _run(update_order({
            "action": "add", "item_name": "One More",
            "size": "standard", "quantity": 1, "price": 1.0,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("big order", result.text.lower())

    def test_quantity_limit_message_mentions_max(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": MAX_QUANTITY_PER_ITEM + 5, "price": 5.99,
        }, sid))
        self.assertIn(str(MAX_QUANTITY_PER_ITEM), result.text)


class CustomizationValidationTests(unittest.TestCase):
    """Test customization mod validation."""

    def test_valid_burger_mod_accepted(self):
        # Use item name NOT in MENU_CATEGORY_MAP so keyword inference triggers "combos"
        result = validate_customization("Test Cheeseburger", "no pickles")
        self.assertIsNone(result)

    def test_mustard_on_shake_rejected(self):
        # Use name NOT in MENU_CATEGORY_MAP so keyword inference returns "shakes"
        result = validate_customization("Test Shake Special", "mustard")
        self.assertIsNotNone(result)

    def test_cheese_on_slush_rejected(self):
        # "Cherry Slush" triggers keyword "slush" → "slushes" category
        result = validate_customization("Cherry Slush Drink", "cheese")
        self.assertIsNotNone(result)

    def test_whipped_cream_on_burger_rejected(self):
        # Big Mac® maps to "burgers & sandwiches" via MENU_CATEGORY_MAP
        result = validate_customization("Big Mac®", "whipped cream")
        self.assertIsNotNone(result)

    def test_customization_via_update_order(self):
        sid = _make_session()
        # Use name NOT in MENU_CATEGORY_MAP so keyword inference finds "shakes"
        result = _run(update_order({
            "action": "add", "item_name": "Test Shake Special (mustard)",
            "size": "medium", "quantity": 1, "price": 3.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)

    def test_valid_customization_passes_through(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Test Burger Patty (No Pickles)",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)


class GetOrderTests(unittest.TestCase):
    """Test get_order tool."""

    def test_get_order_empty(self):
        sid = _make_session()
        result = _run(get_order({}, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        self.assertIn("empty", result.text.lower())

    def test_get_order_with_items(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 2, 5.99)
        result = _run(get_order({}, sid))
        self.assertIn("Big Mac", result.text)
        self.assertRegex(result.text, r"\d+\.\d{2}")

    def test_get_order_returns_json_summary_for_client(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        result = _run(get_order({}, sid))
        client_text = result.to_client_text()
        parsed = json.loads(client_text)
        self.assertIn("items", parsed)
        self.assertIn("finalTotal", parsed)

    def test_get_order_grouped_readback_format(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 1, 5.99)
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        result = _run(get_order({}, sid))
        self.assertIn("Big Mac", result.text)
        self.assertIn("Fries", result.text)


class ResetOrderTests(unittest.TestCase):
    """Test reset_order (clear_order) tool."""

    def test_reset_clears_all_items(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 2, 5.99)
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        result = _run(reset_order({}, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 0)
        self.assertAlmostEqual(summary.finalTotal, 0.0)

    def test_reset_empty_order_is_safe(self):
        sid = _make_session()
        result = _run(reset_order({}, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)

    def test_reset_returns_confirmation(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 1, 5.99)
        result = _run(reset_order({}, sid))
        self.assertIn("clear", result.text.lower())


class TaxCalculationTests(unittest.TestCase):
    """Test tax calculation accuracy."""

    def test_tax_rate_applied_correctly(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 10.00)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertTrue(math.isclose(summary.tax, 0.80, rel_tol=1e-9))
        self.assertTrue(math.isclose(summary.finalTotal, 10.80, rel_tol=1e-9))

    def test_tax_on_multiple_items(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 2, 5.99)
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        summary = order_state_singleton.get_order_summary(sid)
        expected_subtotal = (2 * 5.99) + 3.79
        expected_tax = expected_subtotal * 0.08
        self.assertTrue(math.isclose(summary.tax, expected_tax, rel_tol=1e-9))


# ═══════════════════════════════════════════════════════════════════════════════
# UPSELL HINT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class UpsellHintTests(unittest.TestCase):
    """Test category-based upsell hints in tool results."""

    def test_burger_triggers_combo_upsell(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        self.assertTrue("combo" in result.text.lower() or "upsell" in result.text.lower())

    def test_drink_triggers_addon_upsell(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Coca-Cola",
            "size": "medium", "quantity": 1, "price": 1.99,
        }, sid))
        self.assertTrue(
            "flavor" in result.text.lower()
            or "add-in" in result.text.lower()
            or "upsell" in result.text.lower()
            or "side" in result.text.lower()
        )

    def test_side_triggers_drink_upsell(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 1, "price": 3.79,
        }, sid))
        self.assertTrue(
            "drink" in result.text.lower()
            or "upsell" in result.text.lower()
        )

    def test_combo_triggers_upgrade_upsell(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        order_state_singleton.handle_order_update(sid, "add", "Coca-Cola", "medium", 1, 1.99)
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac Combo",
            "size": "standard", "quantity": 1, "price": 8.49,
        }, sid))
        self.assertTrue(
            "upgrade" in result.text.lower()
            or "large" in result.text.lower()
            or "upsell" in result.text.lower()
            or "mcflurry" in result.text.lower()
            or "dessert" in result.text.lower()
        )

    def test_unknown_category_gets_generic_upsell(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Mystery Item",
            "size": "standard", "quantity": 1, "price": 4.99,
        }, sid))
        self.assertIn("UPSELL", result.text)


class ComboValidationInToolsTests(unittest.TestCase):
    """Test combo validation hints in update_order results."""

    def test_incomplete_combo_triggers_system_hint(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac Combo",
            "size": "standard", "quantity": 1, "price": 8.49,
        }, sid))
        self.assertIn("SYSTEM HINT", result.text)
        self.assertIn("side", result.text.lower())
        self.assertIn("drink", result.text.lower())

    def test_complete_combo_no_hint(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        order_state_singleton.handle_order_update(sid, "add", "Coca-Cola", "medium", 1, 1.99)
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac Combo",
            "size": "standard", "quantity": 1, "price": 8.49,
        }, sid))
        self.assertNotIn("SYSTEM HINT", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# HAPPY HOUR TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class HappyHourTests(unittest.TestCase):
    """Test happy hour pricing behaviour."""

    @patch("tools.is_happy_hour", return_value=True)
    def test_happy_hour_note_in_update_order(self, mock_hh):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        self.assertIn("HAPPY HOUR", result.text)

    @patch("tools.is_happy_hour", return_value=False)
    def test_no_happy_hour_note_outside_hours(self, mock_hh):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        self.assertNotIn("HAPPY HOUR", result.text)

    @patch("tools.is_happy_hour", return_value=True)
    def test_happy_hour_note_in_get_order(self, mock_hh):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Big Mac", "standard", 1, 5.99)
        result = _run(get_order({}, sid))
        self.assertIn("HAPPY HOUR", result.text)


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class FormatSizeHumanReadableTests(unittest.TestCase):
    """Test _format_size_human_readable from tools.py."""

    def test_known_sizes(self):
        self.assertEqual(_format_size_human_readable("medium"), "Medium")
        self.assertEqual(_format_size_human_readable("large"), "Large")

    def test_unknown_size_capitalized(self):
        self.assertEqual(_format_size_human_readable("jumbo"), "Jumbo")

    def test_alias_resolved(self):
        self.assertEqual(_format_size_human_readable("rt44"), "Route 44")

    def test_standard_size(self):
        self.assertEqual(_format_size_human_readable("standard"), "Standard")

    def test_mini_size(self):
        self.assertEqual(_format_size_human_readable("mini"), "Mini")


class IsExtraItemTests(unittest.TestCase):
    """Test extra item detection."""

    def test_recognized_extras(self):
        self.assertTrue(_is_extra_item("Flavor Add-In"))
        self.assertTrue(_is_extra_item("Whipped Cream"))
        self.assertTrue(_is_extra_item("Extra Patty"))
        self.assertTrue(_is_extra_item("Extra Cheese"))
        self.assertTrue(_is_extra_item("Add Bacon"))

    def test_non_extras(self):
        self.assertFalse(_is_extra_item("Big Mac"))
        self.assertFalse(_is_extra_item("Fries"))
        self.assertFalse(_is_extra_item("Coca-Cola"))

    def test_case_insensitive(self):
        self.assertTrue(_is_extra_item("EXTRA PATTY"))
        self.assertTrue(_is_extra_item("whipped cream"))


class InferCategoryTests(unittest.TestCase):
    """Test category inference from item names."""

    def test_burger_keywords(self):
        cat = _infer_category("Big Mac")
        self.assertTrue("burger" in cat or "combo" in cat or len(cat) > 0)

    def test_combo_keywords(self):
        cat = _infer_category("Big Mac Combo")
        self.assertTrue("combo" in cat or "burger" in cat)

    def test_drink_keywords(self):
        cat = _infer_category("Sweet Tea")
        self.assertTrue("drink" in cat)

    def test_fries_is_sides(self):
        cat = _infer_category("Fries")
        self.assertTrue("side" in cat)

    def test_shake_keywords(self):
        # Use name NOT in MENU_CATEGORY_MAP so keyword inference triggers
        cat = _infer_category("Test Shake Special")
        self.assertTrue("shake" in cat)

    def test_mcflurry_is_shake_category(self):
        # "McFlurry" keyword triggers shake category via inference
        cat = _infer_category("Test McFlurry Treat")
        self.assertTrue("shake" in cat)

    def test_unknown_returns_empty(self):
        self.assertEqual(_infer_category("Mystery Item XYZ 999"), "")

    def test_case_insensitive(self):
        cat = _infer_category("BIG MAC")
        self.assertTrue(len(cat) > 0)


class ExtrasValidationTests(unittest.TestCase):
    """Test extras only allowed on appropriate base items."""

    def test_extra_blocked_without_base_item(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Flavor Add-In",
            "size": "standard", "quantity": 1, "price": 0.79,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)

    def test_extra_allowed_with_drink_in_order(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Coca-Cola", "medium", 1, 1.99)
        result = _run(update_order({
            "action": "add", "item_name": "Flavor Add-In",
            "size": "standard", "quantity": 1, "price": 0.79,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)

    def test_extra_blocked_with_only_side_in_order(self):
        sid = _make_session()
        order_state_singleton.handle_order_update(sid, "add", "Fries", "medium", 1, 3.79)
        result = _run(update_order({
            "action": "add", "item_name": "Flavor Add-In",
            "size": "standard", "quantity": 1, "price": 0.79,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class EdgeCaseTests(unittest.TestCase):
    """Test edge cases in the tool pipeline."""

    def test_special_characters_in_item_name(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "McDonald's® Big Mac™",
            "size": "standard", "quantity": 1, "price": 6.99,
        }, sid))
        self.assertIsNotNone(result)

    def test_empty_cart_get_order(self):
        sid = _make_session()
        result = _run(get_order({}, sid))
        self.assertIn("empty", result.text.lower())

    def test_duplicate_item_different_sizes(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Coca-Cola",
            "size": "medium", "quantity": 1, "price": 1.99,
        }, sid))
        _run(update_order({
            "action": "add", "item_name": "Coca-Cola",
            "size": "large", "quantity": 1, "price": 2.49,
        }, sid))
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 2)

    def test_same_item_same_size_increments_quantity(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 1, "price": 3.79,
        }, sid))
        _run(update_order({
            "action": "add", "item_name": "Fries",
            "size": "medium", "quantity": 2, "price": 3.79,
        }, sid))
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 1)
        self.assertEqual(summary.items[0].quantity, 3)

    def test_remove_from_empty_cart(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "remove", "item_name": "Phantom",
            "size": "medium", "quantity": 1,
        }, sid))
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)

    def test_remove_nonexistent_item_does_not_crash(self):
        sid = _make_session()
        _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        result = _run(update_order({
            "action": "remove", "item_name": "Quarter Pounder",
            "size": "standard", "quantity": 1,
        }, sid))
        self.assertIsNotNone(result)
        summary = order_state_singleton.get_order_summary(sid)
        self.assertEqual(len(summary.items), 1)

    def test_update_order_client_text_is_json(self):
        sid = _make_session()
        result = _run(update_order({
            "action": "add", "item_name": "Big Mac",
            "size": "standard", "quantity": 1, "price": 5.99,
        }, sid))
        client_text = result.to_client_text()
        parsed = json.loads(client_text)
        self.assertIn("items", parsed)


if __name__ == "__main__":
    unittest.main()
