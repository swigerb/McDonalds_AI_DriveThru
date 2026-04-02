import math
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from order_state import SessionIdentifiers, order_state_singleton


class OrderStateTests(unittest.TestCase):
    def setUp(self):
        order_state_singleton.sessions = {}

    def test_create_session_initializes_empty_summary(self):
        session_id = order_state_singleton.create_session()
        summary = order_state_singleton.get_order_summary(session_id)

        self.assertEqual(len(summary.items), 0)
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.tax, 0)
        self.assertEqual(summary.finalTotal, 0)

    def test_handle_order_update_adds_and_updates_totals(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Caramel Craze Latte", "medium", 2, 4.99)
        order_state_singleton.handle_order_update(session_id, "add", "Glazed Donut", "standard", 1, 1.49)

        summary = order_state_singleton.get_order_summary(session_id)

        self.assertEqual(len(summary.items), 2)
        self.assertEqual(summary.items[0].quantity, 2)

        expected_total = (2 * 4.99) + 1.49
        expected_tax = expected_total * 0.08
        expected_final = expected_total + expected_tax

        self.assertTrue(math.isclose(summary.total, expected_total, rel_tol=1e-9))
        self.assertTrue(math.isclose(summary.tax, expected_tax, rel_tol=1e-9))
        self.assertTrue(math.isclose(summary.finalTotal, expected_final, rel_tol=1e-9))

    def test_formatted_display_labels_handle_special_sizes(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "rt44", 1, 3.99)

        summary = order_state_singleton.get_order_summary(session_id)

        self.assertEqual(summary.items[0].display, "Route 44 Coca-Cola")

    def test_n_a_size_is_hidden_in_display(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Glazed Donut", "n/a", 1, 1.49)

        summary = order_state_singleton.get_order_summary(session_id)

        self.assertEqual(summary.items[0].display, "Glazed Donut")

    def test_session_identifiers_increment_with_round_trips(self):
        session_id = order_state_singleton.create_session()
        identifiers = order_state_singleton.get_session_identifiers(session_id)

        self.assertIsInstance(identifiers, SessionIdentifiers)
        self.assertEqual(identifiers.round_trip_index, 0)
        self.assertTrue(identifiers.round_trip_token.endswith("-0000"))

        first_round = order_state_singleton.advance_round_trip(session_id)
        self.assertEqual(first_round.round_trip_index, 1)
        self.assertTrue(first_round.round_trip_token.endswith("-0001"))
        self.assertEqual(first_round.session_token, identifiers.session_token)

    def test_session_tokens_are_unique_per_session(self):
        session_one = order_state_singleton.create_session()
        session_two = order_state_singleton.create_session()

        identifiers_one = order_state_singleton.get_session_identifiers(session_one)
        identifiers_two = order_state_singleton.get_session_identifiers(session_two)

        self.assertNotEqual(identifiers_one.session_token, identifiers_two.session_token)

    # ── Edge cases added below ──

    def test_delete_session_removes_state(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Glazed Donut", "standard", 1, 1.49)
        order_state_singleton.delete_session(session_id)
        self.assertNotIn(session_id, order_state_singleton.sessions)

    def test_delete_nonexistent_session_is_safe(self):
        order_state_singleton.delete_session("nonexistent-id")

    def test_concurrent_sessions_are_independent(self):
        s1 = order_state_singleton.create_session()
        s2 = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(s1, "add", "Glazed Donut", "standard", 2, 1.49)
        order_state_singleton.handle_order_update(s2, "add", "Cold Brew", "large", 1, 3.99)

        summary1 = order_state_singleton.get_order_summary(s1)
        summary2 = order_state_singleton.get_order_summary(s2)

        self.assertEqual(len(summary1.items), 1)
        self.assertEqual(summary1.items[0].item, "Glazed Donut")
        self.assertEqual(len(summary2.items), 1)
        self.assertEqual(summary2.items[0].item, "Cold Brew")

    def test_round_trip_token_format(self):
        session_id = order_state_singleton.create_session()
        ids = order_state_singleton.get_session_identifiers(session_id)
        self.assertRegex(ids.round_trip_token, r"^.+-0000$")

        for i in range(1, 4):
            ids = order_state_singleton.advance_round_trip(session_id)
            self.assertEqual(ids.round_trip_index, i)
            self.assertTrue(ids.round_trip_token.endswith(f"-{i:04d}"))

    def test_multiple_round_trip_advances_maintain_session_token(self):
        session_id = order_state_singleton.create_session()
        ids_initial = order_state_singleton.get_session_identifiers(session_id)
        for _ in range(5):
            ids = order_state_singleton.advance_round_trip(session_id)
        self.assertEqual(ids.session_token, ids_initial.session_token)
        self.assertEqual(ids.round_trip_index, 5)

    def test_remove_item_decreases_quantity(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Glazed Donut", "standard", 3, 1.49)
        order_state_singleton.handle_order_update(session_id, "remove", "Glazed Donut", "standard", 1, 1.49)

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 1)
        self.assertEqual(summary.items[0].quantity, 2)

    def test_remove_item_fully_removes_when_quantity_matches(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Glazed Donut", "standard", 2, 1.49)
        order_state_singleton.handle_order_update(session_id, "remove", "Glazed Donut", "standard", 2, 1.49)

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 0)
        self.assertAlmostEqual(summary.total, 0.0)

    def test_remove_nonexistent_item_is_noop(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "remove", "Phantom Item", "large", 1, 9.99)
        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 0)

    def test_add_duplicate_item_increments_quantity(self):
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Cold Brew", "large", 1, 3.99)
        order_state_singleton.handle_order_update(session_id, "add", "Cold Brew", "large", 2, 3.99)

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 1)
        self.assertEqual(summary.items[0].quantity, 3)

    def test_display_formatting_for_various_sizes(self):
        _session_id = order_state_singleton.create_session()
        cases = [
            ("Latte", "small", "Small Latte"),
            ("Latte", "medium", "Medium Latte"),
            ("Latte", "large", "Large Latte"),
            ("Coca-Cola", "mini", "Mini Coca-Cola"),
            ("Coca-Cola", "rt44", "Route 44 Coca-Cola"),
            ("Coca-Cola", "rt 44", "Route 44 Coca-Cola"),
            ("Coca-Cola", "route 44", "Route 44 Coca-Cola"),
            ("Donut", "standard", "Donut"),
            ("Donut", "n/a", "Donut"),
            ("Donut", "na", "Donut"),
            ("Donut", "none", "Donut"),
            ("Donut", "", "Donut"),
            ("Donut", "n.a.", "Donut"),
            ("Cold Brew", "pot", "Cold Brew"),
            ("Cold Brew", "kannchen", "Cold Brew"),
        ]
        for item, size, expected_display in cases:
            order_state_singleton.sessions = {}
            sid = order_state_singleton.create_session()
            order_state_singleton.handle_order_update(sid, "add", item, size, 1, 1.0)
            summary = order_state_singleton.get_order_summary(sid)
            self.assertEqual(summary.items[0].display, expected_display,
                             f"Failed for size='{size}': expected '{expected_display}'")

    # ── Combo requirements tests ──

    def test_combo_requirements_no_combo_is_complete(self):
        """No combos in order means requirements are complete."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["missing_items"], [])
        self.assertEqual(result["prompt_hint"], "")

    def test_combo_requirements_combo_without_side_or_drink(self):
        """Combo auto-includes fries (like a Meal), so only drink is missing."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])
        self.assertEqual(len(result["missing_items"]), 1)
        self.assertIn("drink", result["missing_items"][0])

    def test_combo_requirements_combo_with_side_missing_drink(self):
        """Combo with side but no drink should report drink missing."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])
        self.assertEqual(len(result["missing_items"]), 1)
        self.assertIn("drink", result["missing_items"][0])

    def test_combo_requirements_combo_with_drink_missing_side(self):
        """Combo auto-includes fries, so with drink added it's fully complete."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "medium", 1, 2.99)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(result["is_complete"])

    def test_combo_requirements_combo_fully_complete(self):
        """Combo with both side and drink is complete."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "medium", 1, 2.99)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["missing_items"], [])

    def test_combo_requirements_two_combos_one_side_one_drink(self):
        """Two combos auto-include fries each. With one extra side absorbed + one drink, still need one drink."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 2, 8.49)
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "medium", 1, 2.99)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])
        self.assertEqual(len(result["missing_items"]), 1)
        self.assertIn("drink", result["missing_items"][0])

    def test_combo_requirements_empty_order(self):
        """Empty order should be complete (no combos to satisfy)."""
        session_id = order_state_singleton.create_session()
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(result["is_complete"])

    # ── Combo pivot / absorption tests ──

    def test_combo_absorbs_existing_side_and_drink(self):
        """Fish Sandwich + Fries + Diet Coke → 'make it a combo' converts entree.
        Combo auto-includes fries so standalone Fries stay; drink is absorbed."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fish Sandwich", "standard", 1, 5.49)
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "medium", 1, 1.99)
        # Guest says "make that a combo"
        order_state_singleton.handle_order_update(session_id, "add", "Fish Sandwich Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        item_names = [i.item for i in items]
        # Standalone entree is converted to combo
        self.assertNotIn("Fish Sandwich", item_names)
        self.assertIn("Fish Sandwich Combo", item_names)
        # Combo auto-includes fries, so standalone Fries remain as extra
        self.assertIn("Fries", item_names)
        # Drink is absorbed into the combo
        self.assertNotIn("Diet Coke", item_names)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(result["is_complete"])

    def test_combo_absorbs_only_one_side(self):
        """Two standalone sides + combo: combo auto-includes fries so neither side is absorbed."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Onion Rings", "medium", 1, 3.29)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        side_items = [i for i in items if i.item in ("Fries", "Onion Rings")]
        self.assertEqual(len(side_items), 2, "Combo auto-includes fries, standalone sides remain")

    def test_combo_absorbs_only_one_drink(self):
        """Two standalone drinks, combo absorbs only one."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "medium", 1, 2.99)
        order_state_singleton.handle_order_update(session_id, "add", "Sprite", "medium", 1, 2.99)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        drink_items = [i for i in items if i.item in ("Coca-Cola", "Sprite")]
        self.assertEqual(len(drink_items), 1, "Only one drink should remain after absorption")

    def test_combo_absorbs_decrements_quantity_when_multiple(self):
        """Standalone side qty=2 + combo: combo auto-includes fries so standalone stays at qty=2."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 2, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        fries = next(i for i in items if i.item == "Fries")
        self.assertEqual(fries.quantity, 2, "Combo auto-includes fries, standalone fries untouched")

    def test_combo_no_absorption_when_no_sides_or_drinks(self):
        """Adding a combo with no standalone sides/drinks absorbs nothing."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fish Sandwich", "standard", 1, 5.49)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 2)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])

    def test_non_combo_add_does_not_absorb(self):
        """Adding a regular item doesn't trigger absorption."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Fish Sandwich", "standard", 1, 5.49)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].item, "Fries")

    def test_combo_absorbs_side_only_when_no_drink_present(self):
        """Side exists but no drink — combo auto-includes fries so side stays; combo needs drink."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Fries", "medium", 1, 2.79)
        order_state_singleton.handle_order_update(session_id, "add", "Fish Sandwich Combo", "standard", 1, 8.49)
        items = order_state_singleton.get_order_items(session_id)
        item_names = [i.item for i in items]
        # Combo auto-includes fries, standalone Fries remain as extra
        self.assertIn("Fries", item_names)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])
        self.assertEqual(len(result["missing_items"]), 1)
        self.assertIn("drink", result["missing_items"][0])

    # ── Meal component tests (McDonald's "Meal" items) ──

    def test_meal_auto_populates_entree_and_fries(self):
        """Big Mac Meal should auto-populate components with entree and fries."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        meal = items[0]
        self.assertEqual(meal.display, "Large Big Mac Meal")
        self.assertIn("Big Mac", meal.components)
        self.assertIn("Large Fries", meal.components)
        self.assertEqual(len(meal.components), 2)

    def test_meal_only_needs_drink(self):
        """Meal should only require a drink (fries are auto-included)."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "medium", 1, 9.99)
        result = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(result["is_complete"])
        self.assertEqual(len(result["missing_items"]), 1)
        self.assertIn("drink", result["missing_items"][0])

    def test_meal_drink_absorption(self):
        """Drink added after meal should be absorbed into meal components."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        result = order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        # Drink should be absorbed
        self.assertTrue(result.get("absorbed_into_meal"))
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)  # Only the meal, no separate drink
        meal = items[0]
        self.assertIn("Large Diet Coke", meal.components)
        self.assertEqual(len(meal.components), 3)  # entree + fries + drink
        # Meal should now be complete
        req = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(req["is_complete"])

    def test_meal_drink_not_double_charged(self):
        """Absorbed drink should not add to the order total."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        summary = order_state_singleton.get_order_summary(session_id)
        # Only the meal price should be in the total (drink absorbed, not charged separately)
        expected_total = 11.29
        self.assertAlmostEqual(summary.total, expected_total, places=2)

    def test_meal_with_existing_drink_absorbs_on_pivot(self):
        """Drink ordered first, then meal added — drink should be absorbed into meal."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "medium", 1, 1.99)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "medium", 1, 9.99)
        items = order_state_singleton.get_order_items(session_id)
        item_names = [i.item for i in items]
        self.assertNotIn("Diet Coke", item_names)  # Absorbed by combo pivot
        self.assertIn("Big Mac Meal", item_names)
        meal = next(i for i in items if i.item == "Big Mac Meal")
        self.assertIn("Medium Diet Coke", meal.components)

    def test_meal_components_shown_in_readback(self):
        """Voice readback should mention the drink for a meal."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        readback = order_state_singleton.get_grouped_order_for_readback(session_id)
        self.assertIn("Diet Coke", readback)

    def test_meal_conversion_removes_standalone_entree(self):
        """Ordering Big Mac then Big Mac Meal should remove the standalone Big Mac."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac", "standard", 1, 6.49)
        result = order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "medium", 1, 9.99)
        self.assertEqual(result.get("meal_converted_from"), "Big Mac")
        items = order_state_singleton.get_order_items(session_id)
        item_names = [i.item for i in items]
        self.assertNotIn("Big Mac", item_names)
        self.assertIn("Big Mac Meal", item_names)

    def test_meal_conversion_carries_mods(self):
        """Mods on standalone entree should carry over to the meal."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac (No Pickles)", "standard", 1, 6.49)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertIn("(No Pickles)", meal.item)

    def test_breakfast_meal_uses_hash_browns(self):
        """Breakfast meals should auto-populate Hash Browns instead of Fries."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Egg McMuffin Meal", "standard", 1, 5.99)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertIn("Egg McMuffin", meal.components)
        self.assertIn("Hash Browns", meal.components)
        self.assertFalse(any("Fries" in c for c in meal.components))

    def test_standalone_drink_when_no_meal(self):
        """Drink without a meal should be a standalone item, not absorbed."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item, "Diet Coke")

    def test_second_drink_not_absorbed_when_meal_complete(self):
        """A second drink should not be absorbed when the meal already has one."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        # Order a second drink — should be standalone
        order_state_singleton.handle_order_update(session_id, "add", "Sprite", "large", 1, 1.99)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 2)  # Meal + standalone Sprite
        sprite = next(i for i in items if i.item == "Sprite")
        self.assertEqual(sprite.item, "Sprite")

    def test_components_serialized_in_json(self):
        """Components should appear in the JSON order summary."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "large", 1, 1.99)
        import json
        json_str = order_state_singleton.get_order_summary_json(session_id)
        data = json.loads(json_str)
        meal_item = data["items"][0]
        self.assertIn("components", meal_item)
        self.assertIn("Big Mac", meal_item["components"])
        self.assertIn("Large Fries", meal_item["components"])
        self.assertIn("Large Diet Coke", meal_item["components"])

    # ── Combo synonym tests ──

    def test_combo_synonym_auto_populates_components(self):
        """'Big Mac Combo' should get the same entree + fries breakdown as 'Big Mac Meal'."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item, "Big Mac Combo")
        self.assertIn("Big Mac", items[0].components)
        self.assertIn("Large Fries", items[0].components)

    def test_combo_synonym_only_needs_drink(self):
        """A combo (like a meal) should auto-include fries, leaving only drink missing."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "medium", 1, 9.99)
        req = order_state_singleton.get_combo_requirements(session_id)
        self.assertFalse(req["is_complete"])
        self.assertEqual(len(req["missing_items"]), 1)
        self.assertIn("drink", req["missing_items"][0])

    def test_combo_synonym_drink_absorption(self):
        """Drink added after a combo should absorb into the combo's components."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "large", 1, 11.29)
        result = order_state_singleton.handle_order_update(session_id, "add", "Sprite", "large", 1, 2.19)
        self.assertTrue(result.get("absorbed_into_meal"))
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertIn("Large Sprite", items[0].components)

    def test_combo_synonym_meal_conversion(self):
        """Standalone entree should be removed when combo version is ordered."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac", "", 1, 5.99)
        self.assertEqual(len(order_state_singleton.get_order_items(session_id)), 1)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item, "Big Mac Combo")

    def test_combo_synonym_breakfast_uses_hash_browns(self):
        """Breakfast combos should use Hash Browns instead of fries."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Egg McMuffin Combo", "", 1, 6.49)
        items = order_state_singleton.get_order_items(session_id)
        self.assertIn("Hash Browns", items[0].components)
        self.assertNotIn("Fries", " ".join(items[0].components))

    def test_combo_synonym_complete_order_flow(self):
        """Full flow: 'Large #1 Combo with a Sprite' should work identically to meal version."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Sprite", "large", 1, 2.19)
        req = order_state_singleton.get_combo_requirements(session_id)
        self.assertTrue(req["is_complete"])
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertIn("Big Mac", items[0].components)
        self.assertIn("Large Fries", items[0].components)
        self.assertIn("Large Sprite", items[0].components)

    def test_combo_synonym_pivot_absorbs_existing_drink(self):
        """Existing standalone drink should be absorbed when combo is added after."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "medium", 1, 1.79)
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "medium", 1, 9.99)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertIn("Medium Diet Coke", items[0].components)

    # ── Meal size upgrade tests ──

    def test_meal_size_upgrade_preserves_all_components(self):
        """Upgrading a meal from Medium to Large must keep entree, fries, AND drink."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "McCrispy Meal", "medium", 1, 9.99)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "medium", 1, 1.99)
        # Verify drink absorbed into meal
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0].components), 3)
        # Now upgrade to Large
        result = order_state_singleton.handle_order_update(session_id, "modify", "McCrispy Meal", "large", 1, 11.69)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)  # Still one item
        meal = items[0]
        self.assertEqual(meal.size, "large")
        self.assertEqual(meal.price, 11.69)
        self.assertEqual(meal.display, "Large McCrispy Meal")
        # All three components preserved with updated sizes
        self.assertIn("McCrispy", meal.components)
        self.assertIn("Large Fries", meal.components)
        self.assertIn("Large Diet Coke", meal.components)
        self.assertEqual(len(meal.components), 3)
        self.assertEqual(result.get("size_changed_from"), "medium")
        self.assertEqual(result.get("size_changed_to"), "large")

    def test_meal_size_upgrade_no_separate_drink_charge(self):
        """After size upgrade, drink stays inside meal — no extra line item."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "McCrispy Meal", "medium", 1, 9.99)
        order_state_singleton.handle_order_update(session_id, "add", "Diet Coke", "medium", 1, 1.99)
        order_state_singleton.handle_order_update(session_id, "modify", "McCrispy Meal", "large", 1, 11.69)
        summary = order_state_singleton.get_order_summary(session_id)
        # Only the meal price — no separate drink charge
        self.assertAlmostEqual(summary.total, 11.69, places=2)
        items = order_state_singleton.get_order_items(session_id)
        self.assertEqual(len(items), 1)  # No ejected drink

    def test_meal_size_upgrade_updates_fries_and_drink_sizes(self):
        """Fries and drink size prefixes must update to match the new meal size."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "medium", 1, 9.99)
        order_state_singleton.handle_order_update(session_id, "add", "Sprite", "medium", 1, 1.79)
        # Upgrade to Large
        order_state_singleton.handle_order_update(session_id, "modify", "Big Mac Meal", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertIn("Large Fries", meal.components)
        self.assertIn("Large Sprite", meal.components)
        self.assertNotIn("Medium Fries", meal.components)
        self.assertNotIn("Medium Sprite", meal.components)

    def test_meal_size_downgrade_updates_components(self):
        """Downgrading from Large to Medium also updates component sizes."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Meal", "large", 1, 11.29)
        order_state_singleton.handle_order_update(session_id, "add", "Coca-Cola", "large", 1, 2.19)
        # Downgrade to Medium
        order_state_singleton.handle_order_update(session_id, "modify", "Big Mac Meal", "medium", 1, 9.99)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertEqual(meal.display, "Medium Big Mac Meal")
        self.assertIn("Medium Fries", meal.components)
        self.assertIn("Medium Coca-Cola", meal.components)

    def test_meal_size_upgrade_breakfast_preserves_hash_browns(self):
        """Breakfast meal size upgrade should keep Hash Browns unchanged (no size prefix)."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Egg McMuffin Meal", "standard", 1, 5.99)
        order_state_singleton.handle_order_update(session_id, "add", "Coffee", "medium", 1, 1.49)
        items = order_state_singleton.get_order_items(session_id)
        self.assertIn("Hash Browns", items[0].components)
        # Upgrade to large
        order_state_singleton.handle_order_update(session_id, "modify", "Egg McMuffin Meal", "large", 1, 7.49)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertIn("Hash Browns", meal.components)
        self.assertIn("Large Coffee", meal.components)

    def test_meal_size_upgrade_combo_synonym(self):
        """Size upgrade should also work on items using 'Combo' terminology."""
        session_id = order_state_singleton.create_session()
        order_state_singleton.handle_order_update(session_id, "add", "Big Mac Combo", "medium", 1, 9.99)
        order_state_singleton.handle_order_update(session_id, "add", "Sprite", "medium", 1, 1.79)
        order_state_singleton.handle_order_update(session_id, "modify", "Big Mac Combo", "large", 1, 11.29)
        items = order_state_singleton.get_order_items(session_id)
        meal = items[0]
        self.assertEqual(meal.display, "Large Big Mac Combo")
        self.assertIn("Large Fries", meal.components)
        self.assertIn("Large Sprite", meal.components)


if __name__ == "__main__":
    unittest.main()
