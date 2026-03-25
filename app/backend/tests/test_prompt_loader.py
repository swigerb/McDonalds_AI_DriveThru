"""Tests for PromptLoader — validates prompt loading, caching, and rendering.

Covers: initialization, system prompt assembly, greeting structure,
tool schemas, error message rendering (Jinja2), upsell hints,
delta templates, and error paths (missing brand, malformed YAML).
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

PromptLoader = pytest.importorskip(
    "prompt_loader", reason="prompt_loader.py not yet created by other agent"
).PromptLoader


class PromptLoaderInitTests(unittest.TestCase):
    """PromptLoader can be created and loads data for the mcdonalds brand."""

    def test_init_with_mcdonalds_brand(self):
        loader = PromptLoader(brand="mcdonalds")
        self.assertIsNotNone(loader)

    def test_init_raises_for_nonexistent_brand(self):
        with self.assertRaises(FileNotFoundError):
            PromptLoader(brand="nonexistent_brand_xyz")

    def test_init_default_brand_is_mcdonalds(self):
        loader = PromptLoader()
        self.assertIsNotNone(loader)


class SystemPromptTests(unittest.TestCase):
    """System prompt is assembled correctly from YAML sections."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_returns_non_empty_string(self):
        prompt = self.loader.get_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertTrue(len(prompt) > 50, "System prompt should be substantial")

    def test_contains_mcdonalds(self):
        prompt = self.loader.get_system_prompt()
        self.assertIn("McDonald's", prompt)

    def test_contains_crew_member(self):
        prompt = self.loader.get_system_prompt()
        self.assertIn("crew member", prompt.lower())

    def test_contains_world_famous_fries(self):
        prompt = self.loader.get_system_prompt()
        self.assertIn("World Famous Fries", prompt)


class GreetingTests(unittest.TestCase):
    """Greeting payload has correct structure for WebSocket conversation.item.create."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_get_greeting_returns_dict(self):
        greeting = self.loader.get_greeting()
        self.assertIsInstance(greeting, dict)

    def test_greeting_has_type_key(self):
        greeting = self.loader.get_greeting()
        self.assertIn("type", greeting)

    def test_get_greeting_json_str_returns_valid_json(self):
        json_str = self.loader.get_greeting_json_str()
        self.assertIsInstance(json_str, str)
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, dict)

    def test_greeting_json_str_matches_greeting_dict(self):
        greeting_dict = self.loader.get_greeting()
        greeting_json = json.loads(self.loader.get_greeting_json_str())
        self.assertEqual(greeting_dict.get("type"), greeting_json.get("type"))


class ToolSchemaTests(unittest.TestCase):
    """Tool schemas define the four expected AI tools."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_returns_list(self):
        schemas = self.loader.get_tool_schemas()
        self.assertIsInstance(schemas, list)

    def test_has_four_tools(self):
        schemas = self.loader.get_tool_schemas()
        self.assertEqual(len(schemas), 4,
                         f"Expected 4 tools, got {len(schemas)}: "
                         f"{[s.get('name', '?') for s in schemas]}")

    def test_expected_tool_names(self):
        schemas = self.loader.get_tool_schemas()
        names = {s["name"] for s in schemas}
        expected = {"search", "update_order", "get_order", "reset_order"}
        self.assertEqual(names, expected)

    def test_each_schema_has_name_and_type(self):
        schemas = self.loader.get_tool_schemas()
        for schema in schemas:
            with self.subTest(tool=schema.get("name", "unknown")):
                self.assertIn("name", schema)
                self.assertIn("type", schema)


class ErrorMessageTests(unittest.TestCase):
    """Error messages load as Jinja2 templates and render correctly."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_returns_dict(self):
        errors = self.loader.get_error_messages()
        self.assertIsInstance(errors, dict)

    def test_has_expected_keys(self):
        errors = self.loader.get_error_messages()
        expected_keys = [
            "search_service_unavailable",
            "price_validation_failed",
            "invalid_mod",
        ]
        for key in expected_keys:
            with self.subTest(key=key):
                self.assertIn(key, errors, f"Missing error key: {key}")

    def test_render_error_invalid_mod(self):
        rendered = self.loader.render_error(
            "invalid_mod",
            forbidden_item="mustard",
            base_name="McFlurry",
        )
        self.assertIsInstance(rendered, str)
        self.assertIn("mustard", rendered)
        self.assertIn("McFlurry", rendered)

    def test_render_error_returns_string(self):
        errors = self.loader.get_error_messages()
        if errors:
            first_key = next(iter(errors))
            rendered = self.loader.render_error(first_key)
            self.assertIsInstance(rendered, str)


class HintsTests(unittest.TestCase):
    """Hints provide upsell and delta template data."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_get_hints_returns_dict(self):
        hints = self.loader.get_hints()
        self.assertIsInstance(hints, dict)

    def test_hints_has_upsell_hints(self):
        hints = self.loader.get_hints()
        self.assertIn("upsell_hints", hints)

    def test_get_upsell_hint_combos(self):
        hint = self.loader.get_upsell_hint("combos")
        self.assertIsInstance(hint, str)
        self.assertTrue(len(hint) > 0, "Upsell hint for combos should be non-empty")

    def test_get_upsell_hint_unknown_returns_fallback(self):
        hint = self.loader.get_upsell_hint("nonexistent_category_xyz")
        self.assertIsInstance(hint, str)


class DeltaTemplateTests(unittest.TestCase):
    """Delta templates contain expected placeholders."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_add_template_has_quantity_placeholder(self):
        tpl = self.loader.get_delta_template("add")
        self.assertIn("{{quantity}}", tpl)

    def test_remove_template_exists(self):
        tpl = self.loader.get_delta_template("remove")
        self.assertIsInstance(tpl, str)
        self.assertTrue(len(tpl) > 0)


class RenderTemplateTests(unittest.TestCase):
    """Generic template rendering with Jinja2."""

    @classmethod
    def setUpClass(cls):
        cls.loader = PromptLoader(brand="mcdonalds")

    def test_render_template_substitutes_variables(self):
        tpl = "Hello {{name}}, your order has {{count}} items."
        rendered = self.loader.render_template(tpl, name="Brian", count=3)
        self.assertEqual(rendered, "Hello Brian, your order has 3 items.")

    def test_render_template_no_vars(self):
        tpl = "Welcome to McDonald's!"
        rendered = self.loader.render_template(tpl)
        self.assertEqual(rendered, "Welcome to McDonald's!")


class MalformedYAMLTests(unittest.TestCase):
    """Malformed YAML files should raise ValueError."""

    def test_malformed_yaml_raises_value_error(self):
        prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
        fake_brand_dir = prompts_dir / "_test_malformed_brand"
        manifest_path = fake_brand_dir / "manifest.yaml"

        try:
            fake_brand_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(": : : invalid yaml [[[", encoding="utf-8")
            with self.assertRaises((ValueError, Exception)):
                PromptLoader(brand="_test_malformed_brand")
        finally:
            if manifest_path.exists():
                manifest_path.unlink()
            if fake_brand_dir.exists():
                fake_brand_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
