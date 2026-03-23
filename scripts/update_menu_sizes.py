"""
Update menuItems.json to include all size variants from production data.
Reads the comprehensive mcdonalds-menu-items.json and syncs size/price info
into the UI-facing menuItems.json for items that have size variants.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "frontend" / "src" / "data"
_POS_CANDIDATES = ["mcdonalds-menu-items.json", "sonic-menu-items.json"]
PRODUCTION_FILE = next(
    (DATA_DIR / f for f in _POS_CANDIDATES if (DATA_DIR / f).exists()),
    DATA_DIR / _POS_CANDIDATES[0],
)
MENU_FILE = DATA_DIR / "menuItems.json"

SIZE_ORDER = ["small", "medium", "large"]


def load_production_items():
    """Load the comprehensive menu JSON (flat menuItems format)."""
    with open(PRODUCTION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = {}
    for category in data.get("menuItems", []):
        for item in category.get("items", []):
            items[item["name"]] = item
    return items


def update_menu():
    prod_items = load_production_items()

    with open(MENU_FILE, "r", encoding="utf-8") as f:
        menu_data = json.load(f)

    updated_count = 0

    for category in menu_data["menuItems"]:
        for item in category["items"]:
            name = item["name"]
            prod = prod_items.get(name)
            if not prod:
                continue

            prod_sizes = prod.get("sizes", [])
            if len(prod_sizes) <= 1:
                continue

            # Normalize size names for comparison
            old_keys = [(s["size"].lower(), s["price"]) for s in item.get("sizes", [])]
            new_keys = [(s["size"].lower(), s["price"]) for s in prod_sizes]

            if old_keys != new_keys:
                item["sizes"] = prod_sizes
                updated_count += 1
                print(f"  UPDATED {name}: {len(old_keys)} -> {len(prod_sizes)} sizes")

    with open(MENU_FILE, "w", encoding="utf-8") as f:
        json.dump(menu_data, f, indent=4, ensure_ascii=False)
        f.write("\n")

    print(f"\nUpdated {updated_count} items in menuItems.json")


if __name__ == "__main__":
    update_menu()
