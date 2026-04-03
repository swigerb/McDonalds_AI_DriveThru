import asyncio
import math
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from order_state import order_state_singleton
from rtmt import ToolResultDirection
from tools import update_order


class ExtrasRuleTests(unittest.TestCase):
    def setUp(self):
        order_state_singleton.sessions = {}

    def _add_item(self, session_id: str, name: str, size: str, qty: int, price: float):
        order_state_singleton.handle_order_update(session_id, "add", name, size, qty, price)

    def test_block_extra_when_only_drink(self):
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Coca-Cola", "medium", 1, 2.99)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Cheese",
                    "size": "standard",
                    "quantity": 1,
                    "price": 0.50,
                },
                session_id,
            )
        )

        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("extras", result.text.lower())

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 1)
        self.assertEqual(summary.items[0].item, "Coca-Cola")

    def test_allow_extra_when_burger_present(self):
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Big Mac", "standard", 1, 5.99)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )

        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 2)
        extras_item = summary.items[1]
        self.assertEqual(extras_item.item, "Extra Patty")
        self.assertEqual(extras_item.quantity, 1)
        expected_total = (1 * 5.99) + 1.50
        self.assertTrue(math.isclose(summary.total, expected_total, rel_tol=1e-9))

    def test_block_extra_when_only_sides(self):
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Fries", "medium", 1, 2.79)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )

        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("extras", result.text.lower())

        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 1)
        self.assertEqual(summary.items[0].item, "Fries")
        self.assertTrue(math.isclose(summary.total, 2.79, rel_tol=1e-9))

    # ── Additional extras scenarios ──

    def test_block_extra_when_only_shake(self):
        """Extras like extra patty should be blocked when only a shake is in the order."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Classic Vanilla Shake", "large", 1, 4.99)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("extras", result.text.lower())

    def test_allow_extra_when_mixed_order_has_burger(self):
        """Extras allowed when order has both fries AND a burger."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Fries", "medium", 1, 2.79)
        self._add_item(session_id, "Big Mac", "standard", 1, 5.99)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 3)

    def test_block_extra_with_only_multiple_sides(self):
        """Even multiple sides should not unlock extras."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Fries", "large", 3, 3.29)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Cheese",
                    "size": "standard",
                    "quantity": 1,
                    "price": 0.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("extras", result.text.lower())

    def test_allow_extra_with_burger_and_drink(self):
        """Extra patty should be allowed when a burger is in the order alongside drinks."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Big Mac", "standard", 1, 5.99)
        self._add_item(session_id, "Coca-Cola", "large", 1, 3.49)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 3)

    def test_block_extra_message_differs_with_blocked_base(self):
        """When order has only blocked-category items, the apology should mention them."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Coca-Cola", "medium", 1, 2.99)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_SERVER)
        self.assertIn("can't add them", result.text.lower())

    def test_non_extra_item_always_allowed(self):
        """Non-extra items should always be addable regardless of order contents."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Fries", "medium", 1, 2.79)

        result = asyncio.run(
            update_order(
                {
                    "action": "add",
                    "item_name": "Coca-Cola",
                    "size": "medium",
                    "quantity": 1,
                    "price": 2.99,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 2)

    def test_remove_action_bypasses_extra_check(self):
        """Removing an extra should work even without a qualifying burger."""
        session_id = order_state_singleton.create_session()
        self._add_item(session_id, "Big Mac", "standard", 1, 5.99)
        self._add_item(session_id, "Extra Patty", "standard", 1, 1.50)

        # Now remove the burger, leaving only the extra
        order_state_singleton.handle_order_update(session_id, "remove", "Big Mac", "standard", 1, 5.99)

        # Removing the extra should still work (remove is not gated)
        result = asyncio.run(
            update_order(
                {
                    "action": "remove",
                    "item_name": "Extra Patty",
                    "size": "standard",
                    "quantity": 1,
                    "price": 1.50,
                },
                session_id,
            )
        )
        self.assertEqual(result.destination, ToolResultDirection.TO_BOTH)
        summary = order_state_singleton.get_order_summary(session_id)
        self.assertEqual(len(summary.items), 0)


if __name__ == "__main__":
    unittest.main()
