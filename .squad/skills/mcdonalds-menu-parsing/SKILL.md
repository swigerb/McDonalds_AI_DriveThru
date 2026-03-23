# Skill: McDonald's Menu Data Parsing

## When to Use
Whenever you need to parse the McDonald's menu data — for ingestion, analysis, or any backend/tool code that works with menu items. Menu data sourced from https://www.mcdonalds.com/us/en-us/full-menu.html and structured for AI Search indexing.

## Data Source
`app/frontend/src/data/mcdonalds-menu-items.json` (UTF-8 encoded)

## Expected Menu Categories (McDonald's US Full Menu)
- **Burgers:** Big Mac, Quarter Pounder with Cheese, McDouble, Hamburger, Cheeseburger, Double Cheeseburger, etc.
- **Chicken & Fish:** McNuggets (4/6/10/20/40pc), McChicken, Filet-O-Fish, Crispy Chicken Sandwich, Spicy Chicken Sandwich, Spicy Deluxe
- **Breakfast:** Egg McMuffin, Sausage McMuffin, Hotcakes, McGriddles, Biscuit Sandwiches, Fruit & Maple Oatmeal
- **Fries & Sides:** French Fries (S/M/L), Apple Slices, Hash Browns
- **McCafé:** Iced Coffee, Latte, Mocha, Caramel Macchiato, Hot Chocolate, Americano
- **Sweets & Treats:** McFlurry (Oreo, M&M's), Baked Apple Pie, Chocolate Chip Cookie, Soft Serve
- **Drinks:** Coca-Cola, Sprite, Hi-C Orange, Sweet Tea, Lemonade, various sizes
- **Happy Meal:** 4pc McNuggets Happy Meal, Hamburger Happy Meal, 6pc McNuggets Happy Meal
- **Value Menu / Bundles:** McPick, Mix & Match, Bundle deals

## Key Patterns

### 1. Size Variant Handling
McDonald's items often come in multiple sizes (Small, Medium, Large). Fries, drinks, and McCafé beverages all have size variants with different prices.

### 2. Combo/Value Meal Structure
Most entrees can be ordered as a meal (with fries + drink) or à la carte. The menu data should track:
- Base item price
- Meal/combo price (includes medium fries + medium drink)
- Size upgrade pricing for meal components

### 3. Customization & Extras
Common McDonald's customizations:
- Extra pickles, no onions, etc. (burger customization)
- Sauce choices for McNuggets (BBQ, Sweet & Sour, Honey Mustard, Hot Mustard, Ranch)
- Drink size upgrades

### 4. Breakfast vs All-Day Items
Some items are breakfast-only (served until 10:30 AM / 11:00 AM). Menu data should include a `daypart` or `availability` field.

## Notes
- Must open file with `encoding='utf-8'` on Windows (contains special chars like ® and ™)
- McDonald's menu varies by region — this data targets US nationwide menu items
- Prices are approximate and may vary by location
