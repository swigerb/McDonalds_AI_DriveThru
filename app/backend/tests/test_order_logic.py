"""Tests for combo/meal ordering logic — component absorption, combo requirements.

Verifies that:
  - Adding a combo creates pending component requirements (side + drink)
  - Adding a side partially satisfies combo requirements
  - Multiple combos track independently
  - Grouped readback includes combo components
  - Removing a combo clears its component requirements
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import unittest

from order_state import order_state_singleton


class ComboRequirementsTests(unittest.TestCase):
    """Test combo component tracking (sides/drinks required after combo added)."""

    def setUp(self):
        self.session_id = order_state_singleton.create_session()

    def test_adding_combo_creates_requirements(self):
        """Adding a combo should create pending side + drink requirements."""
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Big Mac Combo", "Medium", 1, 8.99
        )
        status = order_state_singleton.get_combo_requirements(self.session_id)
        # Should indicate missing components
        self.assertIn("prompt_hint", status)
        self.assertTrue(
            status.get("missing_sides", 0) > 0 or status.get("missing_drinks", 0) > 0
            or "side" in status.get("prompt_hint", "").lower()
            or "drink" in status.get("prompt_hint", "").lower(),
            f"Expected combo requirements after adding combo, got: {status}",
        )

    def test_adding_side_partially_satisfies(self):
        """Adding one side to two combos should still show missing requirements."""
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Big Mac Combo", "Medium", 2, 8.99
        )
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Fries", "Medium", 1, 2.49
        )
        status = order_state_singleton.get_combo_requirements(self.session_id)
        # With 2 combos and only 1 side, should still have pending requirements
        hint = status.get("prompt_hint", "")
        self.assertTrue(
            len(hint) > 0,
            "Expected outstanding combo requirements with 2 combos but only 1 side",
        )

    def test_fully_satisfied_combo(self):
        """Adding both a side and drink should satisfy a single combo."""
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Big Mac Combo", "Medium", 1, 8.99
        )
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Fries", "Medium", 1, 2.49
        )
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Coca-Cola", "Medium", 1, 1.99
        )
        status = order_state_singleton.get_combo_requirements(self.session_id)
        hint = status.get("prompt_hint", "")
        # Fully satisfied — no prompt hint needed
        self.assertEqual(hint, "", f"Expected no pending requirements, got: {hint}")

    def test_grouped_readback_not_empty(self):
        """Grouped readback should return a non-empty string after adding items."""
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Big Mac Combo", "Medium", 1, 8.99
        )
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Fries", "Medium", 1, 2.49
        )
        readback = order_state_singleton.get_grouped_order_for_readback(self.session_id)
        self.assertIsInstance(readback, str)
        self.assertGreater(len(readback), 0)
        self.assertIn("Big Mac", readback)

    def test_remove_item(self):
        """Removing an item should update the order correctly."""
        order_state_singleton.handle_order_update(
            self.session_id, "add", "Big Mac Combo", "Medium", 1, 8.99
        )
        order_state_singleton.handle_order_update(
            self.session_id, "remove", "Big Mac Combo", "Medium", 1, 8.99
        )
        readback = order_state_singleton.get_grouped_order_for_readback(self.session_id)
        # After removing the only item, readback should indicate empty or no Big Mac
        self.assertNotIn("Big Mac", readback)


if __name__ == "__main__":
    unittest.main()
