# Grimace — History

## Sessions

_No sessions yet._

## Learnings

- **Menu Period Tagging (2026-03-23):** Added `menuPeriod` field to all 61 items in `app/frontend/src/data/menuItems.json`. Used Python script to parse JSON, insert field right after `name` key in each item object, and write back with UTF-8 encoding preserved. Category mapping: Breakfast→"breakfast", Burgers & Sandwiches→"lunch", Chicken & McNuggets®→"lunch", Fries/Sides/Drinks→"allDay", Sweets & Treats→"allDay". This enables Birdie's Breakfast/Lunch toggle on the frontend. ✅ Merged to decisions.md.
