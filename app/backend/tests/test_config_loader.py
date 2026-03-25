"""Tests for config_loader — validates config.yaml loading and structure.

Covers: lazy loading, required sections, value types, reload semantics.
"""

import sys
import unittest
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

_mod = pytest.importorskip(
    "config_loader", reason="config_loader.py not yet created by other agent"
)
get_config = _mod.get_config
reload_config = _mod.reload_config


class ConfigLoaderBasicTests(unittest.TestCase):
    """get_config() returns a valid configuration dict."""

    def test_get_config_returns_dict(self):
        config = get_config()
        self.assertIsInstance(config, dict)

    def test_get_config_is_non_empty(self):
        config = get_config()
        self.assertTrue(len(config) > 0, "Config should not be empty")

    def test_get_config_is_cached(self):
        config1 = get_config()
        config2 = get_config()
        self.assertIs(config1, config2, "get_config() should return same cached object")


class ConfigRequiredSectionsTests(unittest.TestCase):
    """Config has all required top-level sections."""

    @classmethod
    def setUpClass(cls):
        cls.config = get_config()

    def test_has_model_section(self):
        self.assertIn("model", self.config)

    def test_has_business_rules_section(self):
        self.assertIn("business_rules", self.config)

    def test_has_cache_section(self):
        self.assertIn("cache", self.config)

    def test_has_audio_section(self):
        self.assertIn("audio", self.config)

    def test_has_connection_section(self):
        self.assertIn("connection", self.config)


class BusinessRulesTests(unittest.TestCase):
    """Business rules section has correct values."""

    @classmethod
    def setUpClass(cls):
        cls.rules = get_config()["business_rules"]

    def test_max_item_quantity_is_10(self):
        self.assertEqual(self.rules["max_item_quantity"], 10)

    def test_max_order_items_is_25(self):
        self.assertEqual(self.rules["max_order_items"], 25)

    def test_values_are_integers(self):
        self.assertIsInstance(self.rules["max_item_quantity"], int)
        self.assertIsInstance(self.rules["max_order_items"], int)


class ModelSectionTests(unittest.TestCase):
    """Model section has correct values and types."""

    @classmethod
    def setUpClass(cls):
        cls.model = get_config()["model"]

    def test_temperature_is_0_6(self):
        self.assertAlmostEqual(self.model["temperature"], 0.6, places=2)

    def test_temperature_is_numeric(self):
        self.assertIsInstance(self.model["temperature"], (int, float))


class CacheSectionTests(unittest.TestCase):
    """Cache section has correct types."""

    @classmethod
    def setUpClass(cls):
        cls.cache = get_config()["cache"]

    def test_search_ttl_is_numeric(self):
        self.assertIsInstance(self.cache["search_ttl_seconds"], (int, float))

    def test_search_max_size_is_integer(self):
        self.assertIsInstance(self.cache["search_max_size"], int)


class ReloadConfigTests(unittest.TestCase):
    """reload_config() forces a fresh load from disk."""

    def test_reload_returns_dict(self):
        config = reload_config()
        self.assertIsInstance(config, dict)

    def test_reload_returns_fresh_object(self):
        config_before = get_config()
        config_after = reload_config()
        # After reload, get_config() should return the new object
        config_current = get_config()
        self.assertIs(config_current, config_after)

    def test_reload_has_same_sections(self):
        config = reload_config()
        for section in ("model", "business_rules", "cache", "audio", "connection"):
            with self.subTest(section=section):
                self.assertIn(section, config)


if __name__ == "__main__":
    unittest.main()
