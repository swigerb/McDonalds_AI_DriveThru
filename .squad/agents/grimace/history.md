# Grimace — History

## Sessions

_No sessions yet._

## Learnings

- **Menu Period Tagging (2026-03-23):** Added `menuPeriod` field to all 61 items in `app/frontend/src/data/menuItems.json`. Used Python script to parse JSON, insert field right after `name` key in each item object, and write back with UTF-8 encoding preserved. Category mapping: Breakfast→"breakfast", Burgers & Sandwiches→"lunch", Chicken & McNuggets®→"lunch", Fries/Sides/Drinks→"allDay", Sweets & Treats→"allDay". This enables Birdie's Breakfast/Lunch toggle on the frontend. ✅ Merged to decisions.md.
- **Chicken & McNuggets Menu Expansion (2026-03-23):** Expanded "Chicken & McNuggets®" category from 6→11 individual items (plus 10 unchanged Extra Value Meals). Renamed multi-size "Chicken McNuggets®" to "10 Piece Chicken McNuggets®" with single Standard size ($4.89). Added 4pc ($3.79), 6pc ($5.29), 20pc ($9.99), 40pc ($14.99) as separate items, plus standalone McCrispy® ($5.99). All new items follow existing JSON structure with `menuPeriod: "lunch"`, no `mealNumber`, empty image strings, and realistic allergens. Scanned Burgers & Sandwiches (10 items) — Big Mac, QPC variants, McDouble, Cheeseburger, Double Cheeseburger, Hamburger, McCrispy, Filet-O-Fish all present; no obvious McDonald's staples missing. Total menu: 66 items across 5 categories. Used Python script for safe JSON manipulation with OrderedDict to preserve key ordering.
