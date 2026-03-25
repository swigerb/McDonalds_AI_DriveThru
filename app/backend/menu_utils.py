"""Shared menu utilities — canonical size mappings and category inference.

Both ``tools.py`` and ``order_state.py`` need size normalisation and category
inference.  Keeping a single source of truth here avoids silent drift.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

__all__ = [
    "SIZE_MAP",
    "SIZE_ALIASES",
    "normalize_size",
    "infer_category",
    "MENU_CATEGORY_MAP",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical size map  (display-ready values)
# ---------------------------------------------------------------------------
SIZE_MAP: Dict[str, str] = {
    "small": "Small",
    "medium": "Medium",
    "large": "Large",
    "standard": "Standard",
}

# Aliases that normalise to a canonical key above
SIZE_ALIASES: Dict[str, str] = {
    "s": "small",
    "m": "medium",
    "l": "large",
    "lg": "large",
}

# Sizes that should be hidden in display strings (no prefix)
_NO_DISPLAY_SIZES = frozenset({"", "standard", "n/a", "na", "none", "n.a."})


def normalize_size(size: str) -> str:
    """Return a human-readable size string, or ``""`` for hidden/standard sizes.

    >>> normalize_size("lg")
    'Large'
    >>> normalize_size("m")
    'Medium'
    >>> normalize_size("n/a")
    ''
    """
    key = (size or "").strip().lower()
    if key in _NO_DISPLAY_SIZES:
        return ""
    # Resolve aliases first
    canonical = SIZE_ALIASES.get(key, key)
    return SIZE_MAP.get(canonical, "")


# ---------------------------------------------------------------------------
# Menu category map (loaded once from menuItems.json)
# ---------------------------------------------------------------------------
def _load_menu_category_map() -> Dict[str, str]:
    env_override = (
        os.environ.get("MCDONALDS_MENU_ITEMS_PATH")
        or os.environ.get("MENU_ITEMS_PATH")
    )

    candidate_paths: list[Path] = []
    if env_override:
        candidate_paths.append(Path(env_override))

    candidate_paths.append(Path(__file__).resolve().parent / "data" / "menuItems.json")
    candidate_paths.append(Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / "menuItems.json")

    menu_path = next((path for path in candidate_paths if path.exists()), None)
    if menu_path is None:
        return {}
    try:
        with menu_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        mapping: Dict[str, str] = {}
        for category_entry in data.get("menuItems", []):
            category = category_entry.get("category", "").strip().lower()
            for item in category_entry.get("items", []):
                name = item.get("name")
                if name:
                    mapping[name.lower()] = category
        return mapping
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to load menu items; falling back to keyword inference: %s", exc)
        return {}


MENU_CATEGORY_MAP: Dict[str, str] = _load_menu_category_map()


def infer_category(item_name: str) -> str:
    """Return the menu category for *item_name* (keyword fallback if not in the JSON map)."""
    normalized = item_name.lower()
    if normalized in MENU_CATEGORY_MAP:
        return MENU_CATEGORY_MAP[normalized]
    if "mcflurry" in normalized or "shake" in normalized or "sundae" in normalized:
        return "desserts"
    if "big mac" in normalized or "quarter pounder" in normalized or "mcchicken" in normalized or "filet-o-fish" in normalized or "combo" in normalized or "meal" in normalized:
        return "combos"
    if "fries" in normalized or "hash browns" in normalized:
        return "sides"
    if "coke" in normalized or "coca" in normalized or "sprite" in normalized or "fanta" in normalized or "hi-c" in normalized or "coffee" in normalized or "mccaf" in normalized or "dr pepper" in normalized or "root beer" in normalized or "lemonade" in normalized or "tea" in normalized:
        return "drinks"
    if "mcnuggets" in normalized or "nuggets" in normalized:
        return "chicken"
    return ""
