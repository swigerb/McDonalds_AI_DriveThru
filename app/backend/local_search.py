"""Local in-memory menu search for offline mode.

Replaces Azure AI Search with keyword/fuzzy matching over the menu JSON.
Returns results in the EXACT same format as the cloud search tool so the
AI model (Phi-4 or GPT-4o) sees identical output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from config_loader import get_config
from order_state import order_state_singleton, is_happy_hour
from rtmt import Tool, ToolResult, ToolResultDirection
from tools import (
    MEAL_NUMBER_MAP,
    BREAKFAST_MEAL_NUMBER_MAP,
    MOCK_MACHINE_STATUS,
    _ICE_CREAM_MACHINE_KEYWORDS,
    _expand_meal_number_query,
    _format_size_human_readable,
    search_tool_schema,
    update_order_tool_schema,
    get_order_tool_schema,
    reset_order_tool_schema,
    update_order,
    get_order,
    reset_order,
    validate_customization,
)

logger = logging.getLogger("mcdonalds-drive-thru.local-search")
pipeline_logger = logging.getLogger("local-pipeline")

__all__ = ["local_search", "attach_local_tools"]

# Load centralized config
_cfg = get_config()
_cache_cfg = _cfg.get("cache", {})
_search_cfg = _cfg.get("search", {})

# ---------------------------------------------------------------------------
# Search result cache — mirrors tools.py _SearchCache exactly
# ---------------------------------------------------------------------------
_SEARCH_CACHE_TTL_SEC = _cache_cfg.get("search_ttl_seconds", 60.0)
_SEARCH_CACHE_MAX_SIZE = _cache_cfg.get("search_max_size", 128)


class _SearchCache:
    """Simple TTL cache for local search results."""
    __slots__ = ("_store", "_max_size")

    def __init__(self, max_size: int = _SEARCH_CACHE_MAX_SIZE):
        self._store: dict[str, tuple[float, ToolResult]] = {}
        self._max_size = max_size

    def get(self, key: str) -> ToolResult | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, result = entry
        if time.monotonic() - ts > _SEARCH_CACHE_TTL_SEC:
            del self._store[key]
            return None
        return result

    def put(self, key: str, result: ToolResult) -> None:
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest_key]
        self._store[key] = (time.monotonic(), result)

    def clear(self) -> None:
        self._store.clear()


_local_search_cache = _SearchCache()


# ---------------------------------------------------------------------------
# Menu data loader — prefers offline_menu.json (134 items from Azure AI Search
# export) and falls back to nested menuItems.json (71 items).
# ---------------------------------------------------------------------------

_local_cfg = _cfg.get("local_mode", {})
_OFFLINE_MENU_FILENAME = "offline_menu.json"
_LEGACY_MENU_FILENAME = "menuItems.json"


def _resolve_offline_menu_path() -> Path | None:
    """Find offline_menu.json (flat export from Azure AI Search)."""
    env_override = os.environ.get("MCDONALDS_OFFLINE_MENU_PATH")
    if env_override:
        p = Path(env_override)
        if p.exists():
            return p

    cfg_path = _local_cfg.get("offline_menu_path")
    if cfg_path:
        p = Path(cfg_path)
        if p.exists():
            return p

    candidates = [
        Path(__file__).resolve().parent / "data" / _OFFLINE_MENU_FILENAME,
        Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / _OFFLINE_MENU_FILENAME,
    ]
    return next((p for p in candidates if p.exists()), None)


def _resolve_legacy_menu_path() -> Path | None:
    """Find menuItems.json (nested legacy format) — same logic as tools.py."""
    env_override = (
        os.environ.get("MCDONALDS_MENU_ITEMS_PATH")
        or os.environ.get("MENU_ITEMS_PATH")
    )

    candidate_paths: list[Path] = []
    if env_override:
        candidate_paths.append(Path(env_override))

    candidate_paths.append(Path(__file__).resolve().parent / "data" / _LEGACY_MENU_FILENAME)
    candidate_paths.append(
        Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / _LEGACY_MENU_FILENAME
    )

    return next((p for p in candidate_paths if p.exists()), None)


def _load_flat_menu(path: Path) -> list[dict[str, str]]:
    """Load a flat JSON array (offline_menu.json format).

    Each object already has: id, name, category, description, sizes.
    Azure Search metadata fields (@search.*) are stripped.
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got {type(data).__name__}")

    items: list[dict[str, str]] = []
    for raw in data:
        entry: dict[str, str] = {
            "id": str(raw.get("id", "")),
            "name": raw.get("name", ""),
            "category": raw.get("category", ""),
            "description": raw.get("description", ""),
            "sizes": raw.get("sizes", "[]"),
        }
        meal_number = raw.get("mealNumber")
        if meal_number is not None:
            entry["mealNumber"] = str(meal_number)
        items.append(entry)

    return items


def _load_nested_menu(path: Path) -> list[dict[str, str]]:
    """Load nested menuItems.json and flatten into the standard format."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    items: list[dict[str, str]] = []
    item_counter = 0

    for category_entry in data.get("menuItems", []):
        category = category_entry.get("category", "Unknown")
        for item in category_entry.get("items", []):
            item_counter += 1
            name = item.get("name", "")
            description = item.get("description", "")
            sizes_raw = item.get("sizes", [])
            meal_number = item.get("mealNumber")

            sizes_json = json.dumps(sizes_raw) if sizes_raw else "[]"

            entry: dict[str, str] = {
                "id": str(item_counter),
                "name": name,
                "category": category,
                "description": description,
                "sizes": sizes_json,
            }
            if meal_number is not None:
                entry["mealNumber"] = str(meal_number)

            items.append(entry)

    return items


def _load_menu_items() -> list[dict[str, str]]:
    """Load menu data with priority: offline_menu.json → menuItems.json.

    Each dict has: id, name, category, description, sizes (JSON string).
    """
    # --- Priority 1: offline_menu.json (134 items, Azure AI Search export) ---
    offline_path = _resolve_offline_menu_path()
    if offline_path is not None:
        try:
            items = _load_flat_menu(offline_path)
            logger.info(
                "Loaded %d items from %s (Azure AI Search export)",
                len(items), offline_path.name,
            )
            return items
        except Exception as exc:
            logger.warning(
                "Failed to parse %s: %s — falling back to %s",
                _OFFLINE_MENU_FILENAME, exc, _LEGACY_MENU_FILENAME,
            )

    # --- Priority 2: menuItems.json (71 items, legacy nested format) ---
    legacy_path = _resolve_legacy_menu_path()
    if legacy_path is not None:
        try:
            items = _load_nested_menu(legacy_path)
            logger.info(
                "Loaded %d items from %s (%s not found)",
                len(items), legacy_path.name, _OFFLINE_MENU_FILENAME,
            )
            return items
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", _LEGACY_MENU_FILENAME, exc)

    logger.warning(
        "Menu JSON not found — local search will return empty results. "
        "Searched: %s, %s", _OFFLINE_MENU_FILENAME, _LEGACY_MENU_FILENAME,
    )
    return []


# Module-level menu data — loaded once at import time
_MENU_ITEMS: list[dict[str, str]] = _load_menu_items()


# ---------------------------------------------------------------------------
# Keyword scoring engine
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _score_item(item: dict[str, str], query: str) -> float:
    """Score a menu item against a search query.

    Scoring tiers:
      - Exact name match        → 100
      - Name contains query     → 80
      - Query contains name     → 70
      - Category match          → 40
      - Description match       → 20
      - Token overlap bonus     → +2 per shared token (tiebreaker)
    """
    query_lower = query.lower().strip()
    name_lower = item["name"].lower()
    category_lower = item["category"].lower()
    description_lower = item["description"].lower()

    score = 0.0

    # Exact name match (ignoring ® symbols and case)
    name_clean = name_lower.replace("®", "").strip()
    query_clean = query_lower.replace("®", "").strip()
    if name_clean == query_clean:
        score += 100.0
    elif query_clean in name_clean:
        score += 80.0
    elif name_clean in query_clean:
        score += 70.0

    # Category match
    if query_clean in category_lower:
        score += 40.0

    # Description contains query
    if query_clean in description_lower:
        score += 20.0

    # Token overlap tiebreaker
    query_tokens = _tokenize(query)
    name_tokens = _tokenize(item["name"])
    desc_tokens = _tokenize(item["description"])
    cat_tokens = _tokenize(item["category"])

    all_item_tokens = name_tokens | desc_tokens | cat_tokens
    overlap = query_tokens & all_item_tokens
    score += len(overlap) * 2.0

    return score


# ---------------------------------------------------------------------------
# Local search function — matches tools.py search() signature and output
# ---------------------------------------------------------------------------

async def local_search(args: Any) -> ToolResult:
    """Execute a local keyword search over the menu JSON.

    Drop-in replacement for the Azure AI Search ``search()`` tool.
    Returns results in the exact same text format.
    """
    query = args["query"]
    logger.info("Local search requested for query '%s'", query)
    pipeline_logger.info("Local search query: '%s'", query)

    # Expand meal number references (reuse tools.py logic exactly)
    expanded_query = _expand_meal_number_query(query)
    if expanded_query != query:
        logger.info("Expanded meal number query '%s' → '%s'", query, expanded_query)
        query = expanded_query

    # Check cache first
    cache_key = query.strip().lower()
    cached = _local_search_cache.get(cache_key)
    if cached is not None:
        logger.debug("Local search cache hit for '%s'", query)
        return cached

    # Handle empty menu gracefully
    if not _MENU_ITEMS:
        result = ToolResult(
            "No matching menu entries found.", ToolResultDirection.TO_SERVER
        )
        return result

    # Score every item and take top N
    top_n = _search_cfg.get("top_results", 3)
    scored = [(item, _score_item(item, query)) for item in _MENU_ITEMS]
    # Filter out zero-score items
    scored = [(item, s) for item, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_items = scored[:top_n]

    # Format results in the exact same format as tools.py search()
    results: list[str] = []
    for item, _score in top_items:
        identifier = item.get("id", "unknown")

        # Format sizes exactly like tools.py
        raw_sizes = item.get("sizes", "N/A")
        try:
            sizes_json = json.loads(raw_sizes)
            size_str = ", ".join(
                [
                    f"{_format_size_human_readable(s['size'])} (${s['price']})"
                    for s in sizes_json
                ]
            )
        except Exception:
            size_str = raw_sizes

        item_name = item.get("name", "N/A")
        summary = (
            f"[{identifier}]: "
            f"Item: {item_name}, Category: {item.get('category', 'N/A')}, "
            f"Available Sizes: {size_str}"
        )

        # OOS flag injection — same logic as tools.py
        if MOCK_MACHINE_STATUS.get("ice_cream_machine") == "down":
            if any(kw in item_name.lower() for kw in _ICE_CREAM_MACHINE_KEYWORDS):
                summary += " [OOS: Ice cream machine is being cleaned]"

        results.append(summary)

    joined_results = "\n-----\n".join(results)
    pipeline_logger.info("Local search returned %d results for '%s'", len(results), query)
    result = ToolResult(
        joined_results or "No matching menu entries found.",
        ToolResultDirection.TO_SERVER,
    )

    _local_search_cache.put(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Tool attachment — registers local search + order tools on a processor
# ---------------------------------------------------------------------------

def attach_local_tools(processor: Any, prompt_loader: Any = None) -> None:
    """Attach offline-capable tools to a local processor.

    Uses local JSON search instead of Azure AI Search.
    Order tools (update_order, get_order, reset_order) are already local —
    they only touch in-memory order_state_singleton.
    """
    # Set the prompt_loader on tools.py so order tools can use it
    import tools as _tools_module
    _tools_module._prompt_loader = prompt_loader

    # Use tool schemas from prompt loader if available
    schema_map: dict[str, Any] = {}
    if prompt_loader is not None:
        try:
            yaml_schemas = prompt_loader.get_tool_schemas()
            schema_map = {s["name"]: s for s in yaml_schemas}
        except Exception:
            logger.warning("Failed to load tool schemas from prompt loader — using hardcoded schemas")

    _search_schema = schema_map.get("search", search_tool_schema)
    _update_order_schema = schema_map.get("update_order", update_order_tool_schema)
    _get_order_schema = schema_map.get("get_order", get_order_tool_schema)
    _reset_order_schema = schema_map.get("reset_order", reset_order_tool_schema)

    processor.tools["search"] = Tool(
        schema=_search_schema,
        target=lambda args: local_search(args),
    )
    processor.tools["update_order"] = Tool(
        schema=_update_order_schema,
        target=lambda args, session_id: update_order(args, session_id),
    )
    processor.tools["get_order"] = Tool(
        schema=_get_order_schema,
        target=lambda args, session_id: get_order(args, session_id),
    )
    processor.tools["reset_order"] = Tool(
        schema=_reset_order_schema,
        target=lambda args, session_id: reset_order(args, session_id),
    )

    logger.info(
        "Local tools attached: search (local JSON), update_order, get_order, reset_order"
    )
