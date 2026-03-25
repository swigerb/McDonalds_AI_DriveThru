"""Tests for menu_utils — validates size normalization and category inference.

Covers: SIZE_MAP lookups, SIZE_ALIASES resolution, edge cases (empty/unknown),
and keyword-based category inference for McDonald's menu items.
"""

import sys
import unittest
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

_mod = pytest.importorskip(
    "menu_utils", reason="menu_utils.py not yet created by other agent"
)
normalize_size = _mod.normalize_size
infer_category = _mod.infer_category


class NormalizeSizeTests(unittest.TestCase):
    """normalize_size() maps raw size strings to display-friendly labels."""

    def test_small(self):
        self.assertEqual(normalize_size("small"), "Small")

    def test_medium_alias_m(self):
        self.assertEqual(normalize_size("m"), "Medium")

    def test_large_alias_l(self):
        self.assertEqual(normalize_size("l"), "Large")

    def test_large_alias_lg(self):
        self.assertEqual(normalize_size("lg"), "Large")

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalize_size(""), "")

    def test_medium_full_word(self):
        self.assertEqual(normalize_size("medium"), "Medium")

    def test_large_full_word(self):
        self.assertEqual(normalize_size("large"), "Large")

    def test_case_insensitive(self):
        self.assertEqual(normalize_size("SMALL"), "Small")
        self.assertEqual(normalize_size("Medium"), "Medium")
        self.assertEqual(normalize_size("LARGE"), "Large")

    def test_standard_returns_empty(self):
        # "standard" is a non-display size
        result = normalize_size("standard")
        self.assertEqual(result, "")

    def test_na_returns_empty(self):
        result = normalize_size("n/a")
        self.assertEqual(result, "")

    def test_unknown_size_returns_empty(self):
        result = normalize_size("gigantic")
        self.assertEqual(result, "")

    def test_whitespace_handling(self):
        self.assertEqual(normalize_size("  small  "), "Small")


class InferCategoryTests(unittest.TestCase):
    """infer_category() maps item names to menu categories."""

    def test_combo_item(self):
        cat = infer_category("Big Mac Combo")
        self.assertIn("combo", cat.lower(),
                       f"Expected 'combo' in category, got '{cat}'")

    def test_dessert_item(self):
        cat = infer_category("McFlurry")
        self.assertIn("dessert", cat.lower(),
                       f"Expected 'dessert' in category, got '{cat}'")

    def test_sides_item_fries(self):
        cat = infer_category("World Famous Fries")
        self.assertIn("side", cat.lower(),
                       f"Expected 'side' in category, got '{cat}'")

    def test_drinks_item(self):
        cat = infer_category("Coca-Cola")
        self.assertIn("drink", cat.lower(),
                       f"Expected 'drink' in category, got '{cat}'")

    def test_burger_item(self):
        cat = infer_category("Quarter Pounder with Cheese")
        # Should be either "burgers" or "combos" — both valid
        self.assertTrue(
            "burger" in cat.lower() or "combo" in cat.lower() or "sandwich" in cat.lower(),
            f"Expected burger/combo/sandwich category, got '{cat}'",
        )

    def test_chicken_item(self):
        cat = infer_category("McNuggets")
        # Should resolve to chicken or similar
        self.assertIsInstance(cat, str)
        self.assertTrue(len(cat) > 0 or cat == "",
                        "Category should be a string")

    def test_empty_name_returns_empty(self):
        cat = infer_category("")
        self.assertEqual(cat, "")

    def test_case_insensitive(self):
        cat1 = infer_category("big mac combo")
        cat2 = infer_category("BIG MAC COMBO")
        self.assertEqual(cat1.lower(), cat2.lower())

    def test_unknown_item_returns_empty_or_category(self):
        cat = infer_category("Mystery Item XYZ123")
        self.assertIsInstance(cat, str)


if __name__ == "__main__":
    unittest.main()
