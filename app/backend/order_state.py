import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from config_loader import get_config
from models import OrderItem, OrderSummary

__all__ = ["OrderState", "SessionIdentifiers", "order_state_singleton", "is_happy_hour"]

logger = logging.getLogger("order_state")

_cfg = get_config()
_business_cfg = _cfg.get("business_rules", {})
_STORE_TZ = ZoneInfo(os.environ.get("STORE_TIMEZONE", "America/Chicago"))
_HAPPY_HOUR_START = _business_cfg.get("happy_hour_start", 14)
_HAPPY_HOUR_END = _business_cfg.get("happy_hour_end", 16)
_HAPPY_HOUR_DISCOUNT = _business_cfg.get("happy_hour_discount", 0.5)
_TAX_RATE = _business_cfg.get("tax_rate", 0.08)


def is_happy_hour() -> bool:
    """Check if the current time is within happy hour (store timezone)."""
    now = datetime.now(_STORE_TZ)
    return _HAPPY_HOUR_START <= now.hour < _HAPPY_HOUR_END


_BREAKFAST_KEYWORDS = ("mcmuffin", "biscuit", "mcgriddle", "hotcake", "big breakfast")
_SIZE_PREFIXES = ("Small ", "Medium ", "Large ")


def _infer_combo_component(item_name: str) -> str:
    """Lightweight category check for combo component validation (sides vs drinks)."""
    n = item_name.lower()
    if "tot" in n or "fries" in n or "onion rings" in n or "hash brown" in n:
        return "sides"
    if any(kw in n for kw in ("slush", "limeade", "ocean water", "drink", "tea", "lemonade", "shake", "blast", "malt", "coke", "coca", "sprite", "pepper", "root beer", "coffee", "mcflurry", "hi-c", "fanta", "mccaf")):
        return "drinks"
    return ""


def _is_meal_or_combo(item_name: str) -> bool:
    """Detect meal/combo items (both McDonald's 'Meal' and legacy 'Combo')."""
    n = item_name.lower()
    return "meal" in n or "combo" in n


def _is_meal(item_name: str) -> bool:
    """Specifically a McDonald's Meal or Combo (includes fries automatically).

    Customers use 'combo' and 'meal' interchangeably — both get
    the same component breakdown (entree + fries auto-populated).
    """
    n = item_name.lower()
    return "meal" in n or "combo" in n


def _extract_meal_entree(meal_name: str) -> str:
    """Extract entree name: 'Big Mac Meal (No Pickles)' → 'Big Mac (No Pickles)'."""
    base = meal_name.strip()
    mods = ""
    if "(" in base:
        idx = base.find("(")
        mods = " " + base[idx:]
        base = base[:idx].strip()
    for suffix in (" Extra Value Meal", " Meal", " Combo"):
        if base.lower().endswith(suffix.lower()):
            base = base[:len(base) - len(suffix)].strip()
            break
    return f"{base}{mods}".strip()


def _is_breakfast_meal(meal_name: str) -> bool:
    return any(kw in meal_name.lower() for kw in _BREAKFAST_KEYWORDS)


def _get_default_side(meal_name: str, size: str) -> str:
    """Returns the default side for a meal (e.g., 'Large Fries' or 'Hash Browns')."""
    if _is_breakfast_meal(meal_name):
        return "Hash Browns"
    if not size or size.lower() in ("standard", "n/a", "na", "none", ""):
        size = "Medium"
    return f"{size} Fries"


def _update_component_size(component: str, new_size: str) -> str:
    """Update the size prefix on a meal component (fries/drink).

    Entrees and unsized items (Hash Browns) are returned unchanged.
    """
    comp_type = _infer_combo_component(component)
    if comp_type not in ("sides", "drinks"):
        return component
    if "hash brown" in component.lower():
        return component
    # Strip any existing size prefix
    base = component
    for prefix in _SIZE_PREFIXES:
        if component.startswith(prefix):
            base = component[len(prefix):]
            break
    new_prefix = f"{new_size.capitalize()} " if new_size and new_size.lower() not in ("", "standard", "n/a", "na", "none") else ""
    return f"{new_prefix}{base}".strip()


@dataclass
class SessionIdentifiers:
    session_token: str
    round_trip_index: int
    round_trip_token: str


class OrderState:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.sessions = {}
        return cls._instance

    def _update_summary(self, session_id: str):
        session = self.sessions[session_id]
        order_items = session["order_state"]
        happy_hour = is_happy_hour()
        total = 0.0
        for item in order_items:
            item_total = item.price * item.quantity
            if happy_hour and _infer_combo_component(item.item) == "drinks":
                item_total *= _HAPPY_HOUR_DISCOUNT  # Happy Hour discount on drinks
            total += item_total
        tax = total * _TAX_RATE
        finalTotal = total + tax
        summary = OrderSummary(items=order_items, total=total, tax=tax, finalTotal=finalTotal)
        session["order_summary"] = summary
        # Cache the JSON representation to avoid repeated Pydantic serialization
        session["order_summary_json"] = summary.model_dump_json()
        logger.debug("Order summary updated for session %s (items=%d, total=%.2f)", session_id, len(order_items), finalTotal)

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        session_token = str(uuid.uuid4())
        empty_summary = OrderSummary(items=[], total=0.0, tax=0.0, finalTotal=0.0)
        self.sessions[session_id] = {
            "order_state": [],
            "order_summary": empty_summary,
            "order_summary_json": empty_summary.model_dump_json(),
            "session_token": session_token,
            "round_trip_index": 0,
            "round_trip_token": self._format_round_trip_token(session_token, 0),
            "absorbed_sides": 0,
            "absorbed_drinks": 0,
            "absorbed_side_display": "",
            "absorbed_drink_display": "",
        }
        logger.info("Session created: %s", session_id)
        return session_id

    def delete_session(self, session_id: str) -> None:
        if self.sessions.pop(session_id, None) is not None:
            logger.info("Session deleted: %s", session_id)

    def _format_round_trip_token(self, session_token: str, round_trip_index: int) -> str:
        return f"{session_token}-{round_trip_index:04d}"

    def handle_order_update(self, session_id: str, action: str, item_name: str, size: str, quantity: int, price: float) -> dict:
        session = self.sessions[session_id]
        order_state = session["order_state"]
        result_info = {}

        normalized_size = (size or "").strip().lower()
        if normalized_size in {"", "standard", "n/a", "na", "none", "n.a."}:
            formatted_size = ""
        elif normalized_size in {"mini", "small", "medium", "large", "regular"}:
            formatted_size = f"{normalized_size.capitalize()} "
        else:
            formatted_size = ""

        display = f"{formatted_size}{item_name}".strip()

        if action == "add":
            is_meal_combo = _is_meal_or_combo(item_name)
            is_meal_item = _is_meal(item_name)

            # ── Combo conversion: auto-remove matching standalone entree ──
            if is_meal_combo:
                entree_name = _extract_meal_entree(item_name)
                entree_base = entree_name.lower().replace("®", "").strip()
                if "(" in entree_base:
                    entree_base = entree_base[:entree_base.find("(")].strip()
                for i, existing in enumerate(order_state):
                    if _is_meal_or_combo(existing.item):
                        continue
                    existing_base = existing.item.split("(")[0].strip().lower().replace("®", "")
                    if existing_base == entree_base:
                        if "(" in existing.item and "(" not in item_name:
                            mods = existing.item[existing.item.find("("):]
                            item_name = f"{item_name} {mods}"
                            display = f"{formatted_size}{item_name}".strip()
                            result_info["mods_carried"] = mods
                        result_info["meal_converted_from"] = existing.item
                        if existing.quantity > 1:
                            existing.quantity -= 1
                        else:
                            order_state.pop(i)
                        logger.info("Meal conversion: removed standalone '%s' for meal '%s'", existing.item, item_name)
                        break

            # ── Post-meal absorption: side/drink fills an incomplete meal slot ──
            if not is_meal_combo:
                component = _infer_combo_component(item_name)
                if component in ("sides", "drinks"):
                    meal_count = sum(it.quantity for it in order_state if _is_meal_or_combo(it.item))
                    if meal_count > 0:
                        if component == "sides":
                            filled = sum(it.quantity for it in order_state if _infer_combo_component(it.item) == "sides")
                            filled += session.get("absorbed_sides", 0)
                        else:
                            filled = sum(it.quantity for it in order_state if _infer_combo_component(it.item) == "drinks")
                            filled += session.get("absorbed_drinks", 0)

                        slots_available = meal_count - filled
                        if slots_available > 0:
                            to_absorb = min(quantity, slots_available)
                            if component == "sides":
                                session["absorbed_sides"] += to_absorb
                            else:
                                session["absorbed_drinks"] += to_absorb
                            remaining = quantity - to_absorb
                            result_info["absorbed_into_meal"] = True
                            result_info["absorbed_component"] = component
                            result_info["absorbed_display"] = display

                            # Add to the first incomplete meal's components
                            for meal_item in order_state:
                                if not _is_meal_or_combo(meal_item.item):
                                    continue
                                if component == "drinks":
                                    has_comp = any(_infer_combo_component(c) == "drinks" for c in meal_item.components)
                                else:
                                    has_comp = any(_infer_combo_component(c) == "sides" for c in meal_item.components)
                                if not has_comp:
                                    meal_item.components.append(display)
                                    result_info["meal_name"] = meal_item.display
                                    break

                            logger.info("Post-meal absorption: '%s' absorbed as meal %s", display, component)
                            if remaining <= 0:
                                self._update_summary(session_id)
                                return result_info
                            else:
                                quantity = remaining

            # ── Regular add ──
            existing_item_index = next(
                (index for index, order_item in enumerate(order_state) if order_item.item == item_name and order_item.size == size),
                -1
            )

            if existing_item_index != -1:
                order_state[existing_item_index].quantity += quantity
                logger.debug("Updated quantity for %s in session %s", display, session_id)
            else:
                # Build components for McDonald's Meal items
                components = []
                if is_meal_item:
                    entree = _extract_meal_entree(item_name)
                    size_for_side = formatted_size.strip() if formatted_size.strip() else ""
                    default_side = _get_default_side(item_name, size_for_side)
                    components = [entree, default_side]
                    session["absorbed_sides"] += quantity

                order_state.append(OrderItem(
                    item=item_name, size=size, quantity=quantity,
                    price=price, display=display, components=components
                ))
                logger.debug("Added %s to session %s", display, session_id)

            # ── Combo pivot: absorb standalone sides/drinks into newly added meal/combo ──
            if is_meal_combo:
                absorbed_side = False
                absorbed_drink = False
                items_to_remove = []
                for i, existing in enumerate(order_state):
                    if existing.item == item_name and existing.size == size:
                        continue
                    comp = _infer_combo_component(existing.item)
                    if comp == "sides" and not absorbed_side:
                        if is_meal_item:
                            continue  # Meals auto-include fries — don't absorb extra sides
                        logger.info("Absorbing '%s' into new meal/combo '%s'", existing.display, item_name)
                        meal_items = [oi for oi in order_state if oi.item == item_name and oi.size == size]
                        if meal_items:
                            meal_items[0].components.append(existing.display)
                        if existing.quantity > 1:
                            existing.quantity -= 1
                        else:
                            items_to_remove.append(i)
                        absorbed_side = True
                    elif comp == "drinks" and not absorbed_drink:
                        logger.info("Absorbing '%s' into new meal/combo '%s'", existing.display, item_name)
                        meal_items = [oi for oi in order_state if oi.item == item_name and oi.size == size]
                        if meal_items:
                            meal_items[0].components.append(existing.display)
                        if existing.quantity > 1:
                            existing.quantity -= 1
                        else:
                            items_to_remove.append(i)
                        absorbed_drink = True
                for idx in reversed(items_to_remove):
                    order_state.pop(idx)
                if absorbed_side:
                    session["absorbed_sides"] += 1
                if absorbed_drink:
                    session["absorbed_drinks"] += 1

        elif action == "modify":
            # ── In-place size change — preserves all meal components ──
            existing_item_index = next(
                (index for index, order_item in enumerate(order_state) if order_item.item == item_name),
                -1
            )
            if existing_item_index != -1:
                item = order_state[existing_item_index]
                old_size = item.size
                item.size = size
                item.price = price
                item.display = display
                # Update size prefix on meal components (fries, drink)
                if _is_meal_or_combo(item_name) and item.components:
                    new_size_label = formatted_size.strip() if formatted_size.strip() else ""
                    item.components = [
                        _update_component_size(c, new_size_label) for c in item.components
                    ]
                result_info["size_changed_from"] = old_size
                result_info["size_changed_to"] = size
                logger.info("Modified '%s' size from '%s' to '%s' in session %s", item_name, old_size, size, session_id)
            else:
                logger.warning("Modify failed — item '%s' not found in session %s", item_name, session_id)

        elif action == "remove":
            existing_item_index = next((index for index, order_item in enumerate(order_state) if order_item.item == item_name and order_item.size == size), -1)
            if existing_item_index != -1:
                if order_state[existing_item_index].quantity > quantity:
                    order_state[existing_item_index].quantity -= quantity
                    logger.debug("Decreased quantity for %s in session %s", display, session_id)
                else:
                    order_state.pop(existing_item_index)
                    logger.debug("Removed %s from session %s", display, session_id)

        self._update_summary(session_id)
        return result_info

    def get_order_summary(self, session_id: str) -> OrderSummary:
        return self.sessions[session_id]["order_summary"]

    def get_order_items(self, session_id: str) -> list:
        """Return raw order item list — avoids Pydantic overhead for validation checks."""
        return self.sessions[session_id]["order_state"]

    def get_combo_requirements(self, session_id: str) -> dict:
        """Scans the order for meals/combos and returns missing components.
        Helps the AI know exactly what to ask for next."""
        session = self.sessions[session_id]
        order_items = session["order_state"]

        combo_count = sum(item.quantity for item in order_items if _is_meal_or_combo(item.item))
        side_count = sum(item.quantity for item in order_items if _infer_combo_component(item.item) == "sides")
        drink_count = sum(item.quantity for item in order_items if _infer_combo_component(item.item) in ("drinks",))

        # Include sides/drinks absorbed into meals during combo pivot or meal auto-population
        side_count += session.get("absorbed_sides", 0)
        drink_count += session.get("absorbed_drinks", 0)

        missing = []
        if side_count < combo_count:
            missing.append("a side (fries or another side)")
        if drink_count < combo_count:
            missing.append("a drink")

        return {
            "is_complete": len(missing) == 0,
            "missing_items": missing,
            "prompt_hint": f"Ask the guest for {', and '.join(missing)} to finish their meal." if missing else ""
        }

    def get_grouped_order_for_readback(self, session_id: str) -> str:
        """
        Groups items with the same display name for a natural voice read-back.
        Example: 'Two Medium Coca-Colas and one Big Mac.'
        """
        session = self.sessions[session_id]
        items = session["order_state"]
        if not items:
            return "Your order is currently empty."

        # Aggregate quantities by display name
        counts = {}
        components_map = {}
        for oi in items:
            clean_name = oi.display
            # Convert parenthesized mods to speech-friendly format
            if "(" in clean_name and ")" in clean_name:
                clean_name = clean_name.replace("(", "with ").replace(")", "")
            counts[clean_name] = counts.get(clean_name, 0) + oi.quantity
            # Track drink components for voice readback
            if oi.components:
                drink_parts = [c for c in oi.components if _infer_combo_component(c) == "drinks"]
                if drink_parts:
                    components_map[clean_name] = drink_parts

        # Build the natural language string
        parts = []
        for display, qty in counts.items():
            prefix = f"{qty} " if qty > 1 else "one "
            desc = f"{prefix}{display}"
            if display in components_map:
                drinks = components_map[display]
                if len(drinks) == 1:
                    desc += f" with a {drinks[0]}"
                else:
                    desc += f" with {', '.join(drinks[:-1])} and {drinks[-1]}"
            parts.append(desc)

        if len(parts) > 1:
            summary_str = ", ".join(parts[:-1]) + f", and {parts[-1]}"
        else:
            summary_str = parts[0]

        total = session["order_summary"].finalTotal
        return f"I have {summary_str}. Your total is {total:.2f}. "

    def reset_order(self, session_id: str):
        """Clears all items from the current session's order."""
        session = self.sessions[session_id]
        session["order_state"] = []
        session["absorbed_sides"] = 0
        session["absorbed_drinks"] = 0
        session["absorbed_side_display"] = ""
        session["absorbed_drink_display"] = ""
        self._update_summary(session_id)
        logger.info("Order fully reset for session %s", session_id)

    def get_order_summary_json(self, session_id: str) -> str:
        """Return cached JSON string — avoids repeated Pydantic serialization."""
        return self.sessions[session_id]["order_summary_json"]

    def get_session_identifiers(self, session_id: str) -> SessionIdentifiers:
        session = self.sessions[session_id]
        return SessionIdentifiers(
            session_token=session["session_token"],
            round_trip_index=session["round_trip_index"],
            round_trip_token=session["round_trip_token"],
        )

    def advance_round_trip(self, session_id: str) -> SessionIdentifiers:
        session = self.sessions[session_id]
        session["round_trip_index"] += 1
        session["round_trip_token"] = self._format_round_trip_token(
            session["session_token"], session["round_trip_index"]
        )
        logger.debug(
            "Round trip %s recorded for session %s", session["round_trip_index"], session_id
        )
        return self.get_session_identifiers(session_id)

# Create a singleton instance of OrderState
order_state_singleton = OrderState()