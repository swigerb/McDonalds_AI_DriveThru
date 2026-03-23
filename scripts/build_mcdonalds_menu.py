#!/usr/bin/env python3
"""
Build a comprehensive McDonald's US menu JSON file for Azure AI Search ingestion.

Output: app/frontend/src/data/mcdonalds-menu-items.json
Schema matches menuItems.json (flat-category format).
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def item(
    name: str,
    sizes: list[dict],
    description: str,
    long_description: str,
    calories: int,
    allergens: str = "",
    popularity: str = "",
    image: str = "",
    origin: str = "McDonald's Menu",
) -> dict:
    return {
        "name": name,
        "sizes": sizes,
        "description": description,
        "longDescription": long_description,
        "origin": origin,
        "popularity": popularity,
        "image": image,
        "calories": calories,
        "allergens": allergens,
    }


def std(price: float) -> list[dict]:
    """Single standard-size price."""
    return [{"size": "Standard", "price": price}]


def sml(s: float, m: float, l: float) -> list[dict]:
    """Small / Medium / Large prices."""
    return [
        {"size": "Small", "price": s},
        {"size": "Medium", "price": m},
        {"size": "Large", "price": l},
    ]


def reg(price: float) -> list[dict]:
    """Single 'Regular' size."""
    return [{"size": "Regular", "price": price}]


def img(slug: str) -> str:
    return f"https://s7d1.scene7.com/is/image/mcdonalds/Header_{slug}_832x472"


# ---------------------------------------------------------------------------
# BURGERS  (15 items)
# ---------------------------------------------------------------------------
burgers = {
    "category": "Burgers",
    "items": [
        item(
            "Big Mac®",
            std(5.69),
            "Mouthwatering perfection starts with two 100% pure all beef patties and Big Mac® sauce sandwiched between a sesame seed bun.",
            "Mouthwatering perfection starts with two 100% pure all beef patties and Big Mac® sauce sandwiched between a sesame seed bun. It's topped off with pickles, crisp shredded lettuce, finely chopped onion, and a slice of melty American cheese. Two all-beef patties, special sauce, lettuce, cheese, pickles, onions — on a sesame seed bun.",
            550, "Wheat, Milk, Soy, Sesame", "Best Seller", img("BigMac"),
        ),
        item(
            "Quarter Pounder® with Cheese",
            std(5.99),
            "Each Quarter Pounder® with Cheese burger features a ¼ lb.* of 100% fresh beef that's hot, deliciously juicy and cooked when you order.",
            "Each Quarter Pounder® with Cheese burger features a ¼ lb.* of 100% fresh beef that's hot, deliciously juicy and cooked when you order. It's topped with slivered onions, tangy pickles, two slices of melty American cheese, ketchup, and mustard on a sesame seed bun. *Before cooking.",
            520, "Wheat, Milk, Soy, Sesame", "Best Seller", img("QuarterPounderCheese"),
        ),
        item(
            "Quarter Pounder® with Cheese Deluxe",
            std(6.49),
            "The Quarter Pounder® with Cheese Deluxe features a ¼ lb.* of 100% fresh beef topped with Roma tomatoes, crisp leaf lettuce, and mayo.",
            "The Quarter Pounder® with Cheese Deluxe features a ¼ lb.* of 100% fresh beef that's hot, deliciously juicy and cooked when you order, topped with slivered onions, Roma tomatoes, crisp leaf lettuce, mayo, two slices of melty American cheese, ketchup, mustard, and pickles on a sesame seed bun. *Before cooking.",
            630, "Wheat, Milk, Soy, Egg, Sesame", "Popular", img("QPCDeluxe"),
        ),
        item(
            "Double Quarter Pounder® with Cheese",
            std(7.49),
            "Each Double Quarter Pounder® with Cheese features two ¼ lb.* patties of 100% fresh beef cooked when you order.",
            "Each Double Quarter Pounder® with Cheese features two ¼ lb.* patties of 100% fresh beef that's hot, deliciously juicy and cooked when you order, topped with slivered onions, tangy pickles, two slices of melty American cheese, ketchup, and mustard on a sesame seed bun. *Before cooking. Weight before cooking 4 oz.",
            740, "Wheat, Milk, Soy, Sesame", "Popular", img("DoubleQPC"),
        ),
        item(
            "Bacon Quarter Pounder® with Cheese",
            std(7.29),
            "Thick-cut Applewood smoked bacon on a ¼ lb.* of 100% fresh beef with two slices of melty American cheese.",
            "Thick-cut Applewood smoked bacon tops a ¼ lb.* of 100% fresh beef that's hot, deliciously juicy and cooked when you order. It's further layered with slivered onions, tangy pickles, two slices of melty American cheese, ketchup, and mustard on a sesame seed bun. *Before cooking.",
            620, "Wheat, Milk, Soy, Sesame", "Popular", img("BaconQPC"),
        ),
        item(
            "Double Bacon Quarter Pounder® with Cheese",
            std(8.99),
            "Two ¼ lb.* patties of 100% fresh beef topped with thick-cut Applewood smoked bacon and two slices of American cheese.",
            "Two ¼ lb.* patties of 100% fresh beef cooked when you order, topped with thick-cut Applewood smoked bacon, slivered onions, tangy pickles, two slices of melty American cheese, ketchup, and mustard on a sesame seed bun. *Before cooking.",
            840, "Wheat, Milk, Soy, Sesame", "", img("DoubleBaconQPC"),
        ),
        item(
            "McDouble®",
            std(2.79),
            "A classic double featuring two 100% pure beef patties seasoned with a pinch of salt and pepper.",
            "The McDouble® features two 100% pure beef patties seasoned with just a pinch of salt and pepper, topped with tangy pickles, chopped onions, ketchup, mustard, and a slice of melty American cheese on a regular bun.",
            400, "Wheat, Milk", "Value Pick", img("McDouble"),
        ),
        item(
            "Bacon McDouble®",
            std(3.49),
            "Two 100% pure beef patties topped with thick-cut Applewood smoked bacon and a slice of melty American cheese.",
            "Two 100% pure beef patties topped with thick-cut Applewood smoked bacon, tangy pickles, chopped onions, ketchup, mustard, and a slice of melty American cheese on a regular bun.",
            460, "Wheat, Milk", "", img("BaconMcDouble"),
        ),
        item(
            "Cheeseburger",
            std(2.29),
            "A simple, satisfying classic—a 100% pure beef patty with a slice of melty American cheese on a regular bun.",
            "A simple, satisfying classic—a 100% pure beef patty seasoned with just a pinch of salt and pepper, topped with a tangy pickle, chopped onions, ketchup, mustard, and a slice of melty American cheese on a regular bun.",
            300, "Wheat, Milk", "Value Pick", img("Cheeseburger"),
        ),
        item(
            "Double Cheeseburger",
            std(3.39),
            "Two 100% pure beef patties with two slices of melty American cheese on a regular bun.",
            "The Double Cheeseburger features two 100% pure beef patties seasoned with just a pinch of salt and pepper, topped with tangy pickles, chopped onions, ketchup, mustard, and two slices of melty American cheese on a regular bun.",
            450, "Wheat, Milk", "Popular", img("DoubleCheese"),
        ),
        item(
            "Hamburger",
            std(1.89),
            "The original McDonald's hamburger—a 100% pure beef patty on a regular bun with pickles, onions, ketchup, and mustard.",
            "The original McDonald's burger starts with a 100% pure beef patty seasoned with just a pinch of salt and pepper, then topped with a tangy pickle, chopped onions, ketchup, and mustard on a regular bun.",
            250, "Wheat", "Value Pick", img("Hamburger"),
        ),
        item(
            "Triple Cheeseburger",
            std(4.29),
            "Three 100% pure beef patties with two slices of melty American cheese on a regular bun.",
            "Three 100% pure beef patties seasoned with just a pinch of salt and pepper, topped with tangy pickles, chopped onions, ketchup, mustard, and two slices of melty American cheese on a regular bun.",
            590, "Wheat, Milk", "", img("TripleCheese"),
        ),
        item(
            "Bacon Big Mac®",
            std(7.39),
            "The Bacon Big Mac® adds thick-cut Applewood smoked bacon to the iconic Big Mac®.",
            "The Bacon Big Mac® features two 100% pure beef patties, thick-cut Applewood smoked bacon, Big Mac® sauce, crisp shredded lettuce, finely chopped onion, tangy pickles, a slice of melty American cheese, all on a sesame seed bun.",
            610, "Wheat, Milk, Soy, Sesame", "", img("BaconBigMac"),
        ),
        item(
            "Grand Mac™",
            std(6.39),
            "The Grand Mac™ is a bigger Big Mac® with more of everything — two larger all-beef patties and more Big Mac sauce.",
            "The Grand Mac™ features two larger 100% pure beef patties, more Big Mac® sauce, crisp shredded lettuce, a larger sesame seed bun, two slices of American cheese, finely chopped onion, and tangy pickles.",
            740, "Wheat, Milk, Soy, Sesame", "", img("GrandMac"),
        ),
        item(
            "Mac Jr.™",
            std(3.49),
            "The Mac Jr.™ is a single-patty take on the Big Mac® with all the classic fixings.",
            "The Mac Jr.™ features a single 100% pure beef patty topped with Big Mac® sauce, crisp shredded lettuce, finely chopped onion, tangy pickles, a slice of melty American cheese on a sesame seed bun.",
            390, "Wheat, Milk, Soy, Sesame", "", img("MacJr"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# CHICKEN & FISH SANDWICHES  (10 items)
# ---------------------------------------------------------------------------
chicken_sandwiches = {
    "category": "Chicken & Fish Sandwiches",
    "items": [
        item(
            "McCrispy™",
            std(5.49),
            "The McCrispy™ features a Southern-style fried chicken filet on a toasted potato roll with crinkle-cut pickles.",
            "The McCrispy™ is a crispy, juicy, Southern-style fried chicken sandwich made with all-white meat chicken filet, crinkle-cut pickles, and butter on a toasted potato roll.",
            470, "Wheat, Milk, Soy", "Best Seller", img("McCrispy"),
        ),
        item(
            "Spicy McCrispy™",
            std(5.69),
            "A Southern-style fried chicken filet with a spicy pepper sauce on a toasted potato roll.",
            "The Spicy McCrispy™ is a crispy, juicy, Southern-style fried chicken sandwich with a spicy pepper sauce, crinkle-cut pickles on a toasted potato roll.",
            470, "Wheat, Milk, Soy", "Popular", img("SpicyMcCrispy"),
        ),
        item(
            "Deluxe McCrispy™",
            std(6.29),
            "A Southern-style fried chicken filet with shredded lettuce, Roma tomatoes, and mayo on a toasted potato roll.",
            "The Deluxe McCrispy™ features a crispy, juicy, Southern-style fried chicken filet topped with shredded lettuce, Roma tomatoes, and mayo on a toasted potato roll.",
            530, "Wheat, Milk, Soy, Egg", "Popular", img("DeluxeMcCrispy"),
        ),
        item(
            "Spicy Deluxe McCrispy™",
            std(6.49),
            "A spicy Southern-style fried chicken filet with shredded lettuce, Roma tomatoes, and Spicy pepper sauce.",
            "The Spicy Deluxe McCrispy™ features a crispy, juicy, Southern-style fried chicken filet with a spicy pepper sauce, shredded lettuce, Roma tomatoes, and mayo on a toasted potato roll.",
            540, "Wheat, Milk, Soy, Egg", "", img("SpicyDeluxeMcCrispy"),
        ),
        item(
            "McChicken®",
            std(2.49),
            "A crispy chicken patty topped with shredded lettuce and mayo on a regular bun.",
            "The McChicken® is a crispy chicken sandwich made with a seasoned chicken patty, shredded lettuce, and mayonnaise on a regular bun.",
            400, "Wheat, Soy, Egg", "Value Pick", img("McChicken"),
        ),
        item(
            "Hot 'N Spicy McChicken®",
            std(2.49),
            "A spicy, crispy chicken patty topped with shredded lettuce and mayo on a regular bun.",
            "The Hot 'N Spicy McChicken® is a crispy chicken sandwich made with a spicy seasoned chicken patty, shredded lettuce, and mayonnaise on a regular bun.",
            400, "Wheat, Soy, Egg", "", img("HotNSpicyMcChicken"),
        ),
        item(
            "Filet-O-Fish®",
            std(5.19),
            "Dive into our wild-caught Filet-O-Fish® — a fish sandwich with crispy panko breading and tartar sauce.",
            "Dive into the Filet-O-Fish®. This fish sandwich features a wild-caught Alaska Pollock filet with crispy panko breading, topped with creamy tartar sauce and a half slice of melty American cheese, all served on a soft, steamed bun.",
            390, "Wheat, Milk, Fish, Soy", "Popular", img("FiletOFish"),
        ),
        item(
            "Chicken McGriddle®",
            std(3.79),
            "A Southern-style fried chicken filet between two warm, sweet McGriddles® cakes with a hint of maple flavor.",
            "The Chicken McGriddle® features a Southern-style fried chicken filet between two warm, sweet McGriddles® cakes with a hint of maple flavor, for a perfectly sweet and savory bite.",
            450, "Wheat, Milk, Soy, Egg", "", img("ChickenMcGriddle"),
        ),
        item(
            "Hot 'N Spicy Chicken McGriddle®",
            std(3.79),
            "A spicy Southern-style fried chicken filet between two warm, sweet McGriddles® cakes with maple flavor.",
            "The Hot 'N Spicy Chicken McGriddle® features a spicy Southern-style fried chicken filet between two warm, sweet McGriddles® cakes with a hint of maple flavor.",
            460, "Wheat, Milk, Soy, Egg", "", img("HotNSpicyChickenMcGriddle"),
        ),
        item(
            "Double Filet-O-Fish®",
            std(7.29),
            "Two wild-caught Alaska Pollock filets with crispy panko breading, tartar sauce, and melty American cheese.",
            "The Double Filet-O-Fish® doubles down with two wild-caught Alaska Pollock filets in crispy panko breading, topped with creamy tartar sauce and a slice of melty American cheese on a soft, steamed bun.",
            560, "Wheat, Milk, Fish, Soy", "", img("DoubleFiletOFish"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# McNUGGETS & TENDERS  (5 items)
# ---------------------------------------------------------------------------
nuggets = {
    "category": "McNuggets® & Tenders",
    "items": [
        item(
            "Chicken McNuggets® (4 piece)",
            std(2.49),
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken and no artificial colors, flavors, or preservatives.",
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken and no artificial colors, flavors, or preservatives. Enjoy these in a 4-piece serving as a snack or pair with your favorite dipping sauce.",
            170, "Wheat", "", img("McNuggets4"),
        ),
        item(
            "Chicken McNuggets® (6 piece)",
            std(3.69),
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken. Enjoy 6 pieces with your favorite dipping sauce.",
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken and no artificial colors, flavors, or preservatives. Enjoy 6 pieces with your favorite dipping sauce for a satisfying snack.",
            250, "Wheat", "Value Pick", img("McNuggets6"),
        ),
        item(
            "Chicken McNuggets® (10 piece)",
            std(5.29),
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken. Enjoy 10 pieces as a meal.",
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken and no artificial colors, flavors, or preservatives. Enjoy 10 pieces perfect for a meal with your favorite dipping sauce.",
            410, "Wheat", "Best Seller", img("McNuggets10"),
        ),
        item(
            "Chicken McNuggets® (20 piece)",
            std(7.99),
            "Our tender, juicy Chicken McNuggets® — 20 pieces perfect for sharing with family and friends.",
            "Our tender, juicy Chicken McNuggets® are made with 100% white meat chicken and no artificial colors, flavors, or preservatives. 20 pieces perfect for sharing with family and friends.",
            830, "Wheat", "Popular", img("McNuggets20"),
        ),
        item(
            "Chicken McNuggets® (40 piece)",
            std(14.99),
            "Feed the whole crew with 40 pieces of our tender, juicy Chicken McNuggets® made with 100% white meat chicken.",
            "Feed the whole crew with 40 pieces of our tender, juicy Chicken McNuggets® made with 100% white meat chicken and no artificial colors, flavors, or preservatives. Perfect for parties and gatherings.",
            1660, "Wheat", "", img("McNuggets40"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# MEALS & COMBOS  (10 items)
# ---------------------------------------------------------------------------
meals = {
    "category": "Meals & Combos",
    "items": [
        item(
            "Big Mac® Meal",
            sml(8.99, 10.29, 11.29),
            "The Big Mac® Meal includes a Big Mac®, World Famous Fries®, and your choice of drink.",
            "The Big Mac® Meal includes a Big Mac® with two 100% pure beef patties and Big Mac® sauce on a sesame seed bun, World Famous Fries®, and your choice of a medium drink. Choose from Small, Medium, or Large.",
            1080, "Wheat, Milk, Soy, Sesame", "Best Seller", img("BigMacMeal"),
        ),
        item(
            "Quarter Pounder® with Cheese Meal",
            sml(9.29, 10.69, 11.69),
            "The Quarter Pounder® with Cheese Meal includes a Quarter Pounder®, World Famous Fries®, and your choice of drink.",
            "The Quarter Pounder® with Cheese Meal includes a Quarter Pounder® with Cheese made with ¼ lb.* of 100% fresh beef cooked when you order, World Famous Fries®, and your choice of drink. *Before cooking.",
            1050, "Wheat, Milk, Soy, Sesame", "Best Seller", img("QPCMeal"),
        ),
        item(
            "Quarter Pounder® with Cheese Deluxe Meal",
            sml(9.79, 11.19, 12.19),
            "The QPC Deluxe Meal features a Quarter Pounder® with Cheese Deluxe, World Famous Fries®, and a drink.",
            "The Quarter Pounder® with Cheese Deluxe Meal features a ¼ lb.* of 100% fresh beef topped with Roma tomatoes, lettuce, mayo, two slices of American cheese, World Famous Fries®, and your choice of drink. *Before cooking.",
            1160, "Wheat, Milk, Soy, Egg, Sesame", "Popular", img("QPCDeluxeMeal"),
        ),
        item(
            "Bacon Quarter Pounder® with Cheese Meal",
            sml(10.29, 11.69, 12.69),
            "The Bacon QPC Meal features a Bacon Quarter Pounder® with Cheese, World Famous Fries®, and a drink.",
            "The Bacon Quarter Pounder® with Cheese Meal features thick-cut Applewood smoked bacon on a ¼ lb.* of 100% fresh beef with two slices of melty American cheese, World Famous Fries®, and your choice of drink. *Before cooking.",
            1150, "Wheat, Milk, Soy, Sesame", "", img("BaconQPCMeal"),
        ),
        item(
            "10 piece Chicken McNuggets® Meal",
            sml(8.49, 9.89, 10.89),
            "The 10 piece Chicken McNuggets® Meal includes 10 McNuggets®, World Famous Fries®, and a drink.",
            "The 10 piece Chicken McNuggets® Meal includes 10 tender, juicy Chicken McNuggets® made with 100% white meat chicken, World Famous Fries®, your choice of dipping sauce, and your choice of drink.",
            940, "Wheat, Milk, Soy", "Best Seller", img("McNuggets10Meal"),
        ),
        item(
            "McCrispy™ Meal",
            sml(9.29, 10.69, 11.69),
            "The McCrispy™ Meal includes a McCrispy™ sandwich, World Famous Fries®, and your choice of drink.",
            "The McCrispy™ Meal features a Southern-style fried chicken filet on a toasted potato roll with crinkle-cut pickles, World Famous Fries®, and your choice of drink.",
            1000, "Wheat, Milk, Soy", "Popular", img("McCrispyMeal"),
        ),
        item(
            "Filet-O-Fish® Meal",
            sml(8.79, 10.19, 11.19),
            "The Filet-O-Fish® Meal includes a Filet-O-Fish® sandwich, World Famous Fries®, and your choice of drink.",
            "The Filet-O-Fish® Meal features a wild-caught Alaska Pollock filet with crispy panko breading, tartar sauce, and melty American cheese, plus World Famous Fries® and your choice of drink.",
            920, "Wheat, Milk, Fish, Soy", "", img("FiletOFishMeal"),
        ),
        item(
            "2 Cheeseburger Meal",
            sml(7.49, 8.89, 9.89),
            "The 2 Cheeseburger Meal includes two Cheeseburgers, World Famous Fries®, and your choice of drink.",
            "The 2 Cheeseburger Meal features two classic Cheeseburgers with 100% pure beef and melty American cheese, World Famous Fries®, and your choice of drink.",
            1030, "Wheat, Milk", "Value Pick", img("2CheeseMeal"),
        ),
        item(
            "Double Quarter Pounder® with Cheese Meal",
            sml(10.79, 12.19, 13.19),
            "The Double QPC Meal features a Double Quarter Pounder® with Cheese, World Famous Fries®, and a drink.",
            "The Double Quarter Pounder® with Cheese Meal features two ¼ lb.* patties of 100% fresh beef cooked when you order with two slices of melty American cheese, World Famous Fries®, and your choice of drink. *Before cooking.",
            1270, "Wheat, Milk, Soy, Sesame", "", img("DoubleQPCMeal"),
        ),
        item(
            "Spicy McCrispy™ Meal",
            sml(9.49, 10.89, 11.89),
            "The Spicy McCrispy™ Meal includes a Spicy McCrispy™ sandwich, World Famous Fries®, and a drink.",
            "The Spicy McCrispy™ Meal features a Southern-style fried chicken filet with a spicy pepper sauce and crinkle-cut pickles on a toasted potato roll, World Famous Fries®, and your choice of drink.",
            1000, "Wheat, Milk, Soy", "", img("SpicyMcCrispyMeal"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# BREAKFAST  (24 items)
# ---------------------------------------------------------------------------
breakfast = {
    "category": "Breakfast",
    "items": [
        item(
            "Egg McMuffin®",
            std(4.49),
            "A freshly cracked Grade A egg on a toasted English muffin with real butter, lean Canadian bacon, and melty American cheese.",
            "A freshly cracked Grade A egg on a toasted English muffin with real butter, lean Canadian bacon, and a slice of melty American cheese. An excellent source of protein and the breakfast sandwich that started it all.",
            300, "Wheat, Milk, Egg", "Best Seller", img("EggMcMuffin"),
        ),
        item(
            "Sausage McMuffin®",
            std(2.79),
            "A warm, savory sausage patty on a toasted English muffin with a slice of melty American cheese.",
            "The Sausage McMuffin® features a warm, savory sausage patty topped with a slice of melty American cheese on a toasted English muffin. A satisfying breakfast at a great value.",
            400, "Wheat, Milk", "Value Pick", img("SausageMcMuffin"),
        ),
        item(
            "Sausage McMuffin® with Egg",
            std(4.69),
            "A savory sausage patty and a freshly cracked Grade A egg on a toasted English muffin with melty American cheese.",
            "The Sausage McMuffin® with Egg features a warm, savory sausage patty, a freshly cracked Grade A egg, and a slice of melty American cheese on a toasted English muffin.",
            480, "Wheat, Milk, Egg", "Popular", img("SausageMcMuffinEgg"),
        ),
        item(
            "Bacon, Egg & Cheese Biscuit",
            std(4.79),
            "Thick-cut Applewood smoked bacon, a freshly cracked egg, and melty American cheese on a warm buttermilk biscuit.",
            "The Bacon, Egg & Cheese Biscuit features thick-cut Applewood smoked bacon, a freshly cracked Grade A egg, and a slice of melty American cheese on a warm, flaky buttermilk biscuit.",
            460, "Wheat, Milk, Egg, Soy", "Popular", img("BaconEggCheeseBiscuit"),
        ),
        item(
            "Sausage Biscuit",
            std(2.49),
            "A warm, savory sausage patty on a flaky, buttery biscuit.",
            "The Sausage Biscuit features a warm, savory sausage patty on a flaky, warm buttermilk biscuit. A hearty breakfast classic at a great value.",
            460, "Wheat, Milk, Soy", "Value Pick", img("SausageBiscuit"),
        ),
        item(
            "Sausage Biscuit with Egg",
            std(4.49),
            "A savory sausage patty and a freshly cracked Grade A egg on a warm buttermilk biscuit.",
            "The Sausage Biscuit with Egg features a warm, savory sausage patty, a freshly cracked Grade A egg on a flaky, warm buttermilk biscuit.",
            530, "Wheat, Milk, Egg, Soy", "", img("SausageBiscuitEgg"),
        ),
        item(
            "Sausage, Egg & Cheese Biscuit",
            std(4.99),
            "A savory sausage patty, freshly cracked egg, and melty American cheese on a flaky buttermilk biscuit.",
            "The Sausage, Egg & Cheese Biscuit features a warm, savory sausage patty, a freshly cracked Grade A egg, and a slice of melty American cheese on a warm, flaky buttermilk biscuit.",
            530, "Wheat, Milk, Egg, Soy", "", img("SausageEggCheeseBiscuit"),
        ),
        item(
            "Sausage, Egg & Cheese McGriddles®",
            std(5.29),
            "Sweet McGriddles® cakes with sausage, a freshly cracked egg, and melty American cheese.",
            "The Sausage, Egg & Cheese McGriddles® features a warm sausage patty, a freshly cracked Grade A egg, and a slice of melty American cheese between two sweet McGriddles® cakes with a hint of maple flavor.",
            550, "Wheat, Milk, Egg, Soy", "Popular", img("SausageEggCheeseGriddle"),
        ),
        item(
            "Bacon, Egg & Cheese McGriddles®",
            std(5.29),
            "Sweet McGriddles® cakes with thick-cut bacon, a freshly cracked egg, and melty American cheese.",
            "The Bacon, Egg & Cheese McGriddles® features thick-cut Applewood smoked bacon, a freshly cracked Grade A egg, and a slice of melty American cheese between two sweet McGriddles® cakes with a hint of maple flavor.",
            430, "Wheat, Milk, Egg, Soy", "Popular", img("BaconEggCheeseGriddle"),
        ),
        item(
            "Sausage McGriddles®",
            std(3.49),
            "Warm McGriddles® cakes with a hint of maple flavor and a savory sausage patty.",
            "The Sausage McGriddles® features a warm, savory sausage patty between two soft McGriddles® cakes with a hint of sweet maple flavor.",
            430, "Wheat, Milk, Soy", "", img("SausageMcGriddle"),
        ),
        item(
            "Hotcakes",
            std(3.99),
            "Three fluffy, golden brown hotcakes with a warm side of real butter and sweet maple flavored hotcake syrup.",
            "Three fluffy, golden brown hotcakes served with a warm side of real butter and sweet maple flavored hotcake syrup. A classic breakfast favorite.",
            580, "Wheat, Milk, Egg", "", img("Hotcakes"),
        ),
        item(
            "Hotcakes & Sausage",
            std(5.29),
            "Three fluffy hotcakes with real butter and maple syrup, served alongside a warm sausage patty.",
            "Three fluffy, golden brown hotcakes with a warm side of real butter and sweet maple flavored hotcake syrup, served with a savory sausage patty. A classic breakfast combo.",
            770, "Wheat, Milk, Egg", "", img("HotcakesSausage"),
        ),
        item(
            "Big Breakfast®",
            std(5.99),
            "A warm biscuit, scrambled eggs, a sausage patty, and crispy hash browns.",
            "The Big Breakfast® features a warm biscuit, fluffy scrambled eggs, a savory sausage patty, and crispy golden hash browns. It's a big, hearty breakfast.",
            740, "Wheat, Milk, Egg, Soy", "", img("BigBreakfast"),
        ),
        item(
            "Big Breakfast® with Hotcakes",
            std(7.49),
            "A warm biscuit, scrambled eggs, sausage, hash browns, and golden hotcakes with butter and syrup.",
            "The Big Breakfast® with Hotcakes features a warm biscuit, fluffy scrambled eggs, a savory sausage patty, crispy golden hash browns, and three fluffy hotcakes with butter and maple flavored syrup.",
            1340, "Wheat, Milk, Egg, Soy", "Popular", img("BigBreakfastHotcakes"),
        ),
        item(
            "Sausage Burrito",
            std(2.29),
            "A savory blend of fluffy scrambled egg, pork sausage, melty cheese, and peppers wrapped in a warm tortilla.",
            "The Sausage Burrito features a flour tortilla filled with fluffy scrambled egg, pork sausage, melty American cheese, and green chiles and onion. Served with picante sauce.",
            310, "Wheat, Milk, Egg, Soy", "Value Pick", img("SausageBurrito"),
        ),
        item(
            "Hash Browns",
            std(1.89),
            "Our crispy, golden Hash Browns are deliciously tasty and perfectly crispy.",
            "Our crispy, golden Hash Browns are made with shredded potatoes and cooked to delicious perfection for a crispy hash brown every time. A classic McDonald's breakfast side.",
            140, "Wheat, Milk, Soy", "", img("HashBrowns"),
        ),
        item(
            "Fruit & Maple Oatmeal",
            std(3.49),
            "Whole-grain oats with diced apples, cranberry raisin blend, and cream, topped with a light maple flavor.",
            "The Fruit & Maple Oatmeal features two whole grain oats, diced apples, a cranberry raisin blend, and light cream, all topped with a light brown sugar maple flavored syrup.",
            320, "Wheat, Milk", "", img("FruitMapleOatmeal"),
        ),
        item(
            "Bacon, Egg & Cheese Bagel",
            std(5.49),
            "Thick-cut Applewood smoked bacon, a freshly cracked egg, and two slices of American cheese on a toasted bagel.",
            "The Bacon, Egg & Cheese Bagel features thick-cut Applewood smoked bacon, a freshly cracked Grade A egg, and two slices of melty American cheese on a toasted bagel. Includes creamy mustard sauce.",
            560, "Wheat, Milk, Egg, Soy, Sesame", "", img("BaconEggCheeseBagel"),
        ),
        item(
            "Steak, Egg & Cheese Bagel",
            std(6.29),
            "Savory seasoned steak, a freshly cracked egg, and two slices of American cheese on a toasted bagel.",
            "The Steak, Egg & Cheese Bagel features savory seasoned steak, a freshly cracked Grade A egg, and two slices of melty American cheese on a toasted bagel.",
            680, "Wheat, Milk, Egg, Soy, Sesame", "", img("SteakEggCheeseBagel"),
        ),
        # Breakfast MEALS
        item(
            "Egg McMuffin® Meal",
            std(6.49),
            "The Egg McMuffin® Meal includes an Egg McMuffin®, a Hash Brown, and a small McCafé® Premium Roast Coffee.",
            "Start your morning right with the Egg McMuffin® Meal. It includes the classic Egg McMuffin® with Canadian bacon, a freshly cracked egg and American cheese on a toasted English muffin, a crispy Hash Brown, and a small McCafé® Premium Roast Coffee.",
            590, "Wheat, Milk, Egg", "Best Seller", img("EggMcMuffinMeal"),
        ),
        item(
            "Sausage McMuffin® with Egg Meal",
            std(6.69),
            "The Sausage McMuffin® with Egg Meal includes a Sausage McMuffin® with Egg, Hash Brown, and coffee.",
            "The Sausage McMuffin® with Egg Meal features a warm sausage patty, freshly cracked egg, and melty American cheese on a toasted English muffin, a crispy Hash Brown, and a small McCafé® Premium Roast Coffee.",
            770, "Wheat, Milk, Egg", "Popular", img("SausageMcMuffinEggMeal"),
        ),
        item(
            "Sausage McGriddles® Meal",
            std(5.79),
            "The Sausage McGriddles® Meal includes Sausage McGriddles®, a Hash Brown, and a small coffee.",
            "The Sausage McGriddles® Meal features a warm sausage patty between two sweet McGriddles® cakes, a crispy Hash Brown, and a small McCafé® Premium Roast Coffee.",
            720, "Wheat, Milk, Soy", "", img("SausageMcGriddleMeal"),
        ),
        item(
            "Bacon, Egg & Cheese Biscuit Meal",
            std(6.79),
            "The Bacon, Egg & Cheese Biscuit Meal includes a Bacon, Egg & Cheese Biscuit, Hash Brown, and coffee.",
            "The Bacon, Egg & Cheese Biscuit Meal features thick-cut Applewood smoked bacon, a freshly cracked egg, and melty American cheese on a buttermilk biscuit, a crispy Hash Brown, and a small McCafé® Premium Roast Coffee.",
            750, "Wheat, Milk, Egg, Soy", "", img("BaconEggCheeseBiscuitMeal"),
        ),
        item(
            "Sausage, Egg & Cheese McGriddles® Meal",
            std(7.29),
            "The Sausage, Egg & Cheese McGriddles® Meal includes the sandwich, a Hash Brown, and coffee.",
            "The Sausage, Egg & Cheese McGriddles® Meal features a warm sausage patty, freshly cracked egg, and melty American cheese between sweet McGriddles® cakes, a crispy Hash Brown, and a small McCafé® Premium Roast Coffee.",
            840, "Wheat, Milk, Egg, Soy", "", img("SECGriddleMeal"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# FRIES & SIDES  (5 items)
# ---------------------------------------------------------------------------
fries_sides = {
    "category": "Fries & Sides",
    "items": [
        item(
            "World Famous Fries®",
            sml(2.19, 3.79, 4.39),
            "Our World Famous Fries® are made with premium potatoes like the Russet Burbank and Shepody.",
            "Our World Famous Fries® are made with premium potatoes like the Russet Burbank and Shepody. Crispy and golden on the outside, fluffy on the inside, and perfectly salted. The iconic fries that make every meal complete.",
            320, "Wheat, Milk, Soy", "Best Seller", img("FrenchFries"),
        ),
        item(
            "Apple Slices",
            std(0.99),
            "Fresh, crisp apple slices. A wholesome snack or side that's a great complement to any meal.",
            "Fresh, crisp apple slices. A wholesome snack or side that's a great complement to any meal. Perfect for kids and adults alike.",
            15, "", "", img("AppleSlices"),
        ),
        item(
            "Side Salad",
            std(3.29),
            "A blend of chopped Romaine and Iceberg lettuce, grape tomatoes, shredded carrots, and cheddar cheese.",
            "The Side Salad is made with a blend of chopped Romaine and Iceberg lettuce, grape tomatoes, shredded carrots, and cheddar cheese. Pair it with your favorite dressing.",
            70, "Milk", "", img("SideSalad"),
        ),
        item(
            "Tangy BBQ Sauce",
            std(0.25),
            "A bold, tangy BBQ dipping sauce perfect for Chicken McNuggets® and fries.",
            "Our Tangy BBQ Sauce is a bold, tangy BBQ dipping sauce perfect for dipping Chicken McNuggets® or World Famous Fries®.",
            45, "", "", img("TangyBBQSauce"),
        ),
        item(
            "Sweet 'N Sour Sauce",
            std(0.25),
            "A sweet and tangy dipping sauce with a touch of apricot flavor.",
            "Our Sweet 'N Sour Sauce is a sweet and tangy dipping sauce with a touch of apricot flavor. Perfect for dipping Chicken McNuggets® or other favorites.",
            50, "", "", img("SweetNSourSauce"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# HAPPY MEAL  (4 items)
# ---------------------------------------------------------------------------
happy_meal = {
    "category": "Happy Meal®",
    "items": [
        item(
            "Hamburger Happy Meal®",
            std(4.99),
            "A Hamburger Happy Meal® with a kid-sized World Famous Fries®, Apple Slices, and a drink.",
            "The Hamburger Happy Meal® includes a classic Hamburger with a 100% pure beef patty, a kid-sized order of World Famous Fries®, Apple Slices, and your choice of a small drink. Plus a fun toy!",
            475, "Wheat", "", img("HamburgerHappyMeal"),
        ),
        item(
            "4 Piece Chicken McNuggets® Happy Meal®",
            std(5.29),
            "A 4 piece Chicken McNuggets® Happy Meal® with kid-sized fries, Apple Slices, and a drink.",
            "The 4 Piece Chicken McNuggets® Happy Meal® includes 4 tender, juicy Chicken McNuggets® made with 100% white meat chicken, a kid-sized order of World Famous Fries®, Apple Slices, and your choice of a small drink. Plus a fun toy!",
            395, "Wheat", "Popular", img("McNuggets4HappyMeal"),
        ),
        item(
            "6 Piece Chicken McNuggets® Happy Meal®",
            std(5.99),
            "A 6 piece Chicken McNuggets® Happy Meal® with kid-sized fries, Apple Slices, and a drink.",
            "The 6 Piece Chicken McNuggets® Happy Meal® includes 6 tender, juicy Chicken McNuggets® made with 100% white meat chicken, a kid-sized order of World Famous Fries®, Apple Slices, and your choice of a small drink. Plus a fun toy!",
            475, "Wheat", "", img("McNuggets6HappyMeal"),
        ),
        item(
            "Mighty Kids Meal® — 6 Piece McNuggets®",
            std(6.49),
            "A bigger meal for bigger appetites — 6 McNuggets® with small fries, Apple Slices, and a drink.",
            "The Mighty Kids Meal® with 6 Piece McNuggets® includes 6 tender Chicken McNuggets®, a small World Famous Fries®, Apple Slices, and a small drink. A bigger Happy Meal for bigger appetites.",
            530, "Wheat", "", img("MightyKidsMeal"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# McCAFÉ COFFEES  (19 items)
# ---------------------------------------------------------------------------
mccafe = {
    "category": "McCafé® Coffees",
    "items": [
        item(
            "McCafé® Premium Roast Coffee",
            sml(1.49, 1.79, 1.99),
            "A full-bodied, smooth blend made from 100% Arabica beans. Available in Small, Medium, or Large.",
            "McCafé® Premium Roast Coffee is a smooth, full-bodied coffee brewed fresh from 100% Arabica beans. A perfect cup to start your day.",
            0, "", "Best Seller", img("PremiumRoastCoffee"),
        ),
        item(
            "McCafé® Decaf Coffee",
            sml(1.49, 1.79, 1.99),
            "A full-bodied, smooth decaf coffee brewed from 100% Arabica beans.",
            "McCafé® Decaf Coffee is a smooth, rich decaffeinated coffee brewed fresh from 100% Arabica beans. Great taste without the caffeine.",
            0, "", "", img("DecafCoffee"),
        ),
        item(
            "McCafé® Iced Coffee",
            sml(2.49, 3.29, 3.79),
            "Refreshingly cool McCafé® Iced Coffee made with 100% Arabica beans and served over ice.",
            "McCafé® Iced Coffee is brewed from 100% Arabica beans, cooled, and served over ice with your choice of light cream and liquid sugar.",
            140, "Milk", "Popular", img("IcedCoffee"),
        ),
        item(
            "McCafé® Iced Caramel Coffee",
            sml(2.79, 3.59, 4.09),
            "Premium Roast coffee chilled and combined with caramel syrup, served over ice.",
            "McCafé® Iced Caramel Coffee features our Premium Roast coffee chilled and combined with rich caramel syrup, served over ice with light cream.",
            190, "Milk", "", img("IcedCaramelCoffee"),
        ),
        item(
            "McCafé® Iced French Vanilla Coffee",
            sml(2.79, 3.59, 4.09),
            "Premium Roast coffee chilled and combined with French vanilla syrup, served over ice.",
            "McCafé® Iced French Vanilla Coffee features our Premium Roast coffee chilled and combined with French vanilla syrup, served over ice with light cream.",
            190, "Milk", "", img("IcedFrenchVanillaCoffee"),
        ),
        item(
            "McCafé® Caramel Frappé",
            sml(4.29, 4.99, 5.69),
            "A sweet, creamy caramel frappé blended with ice and drizzled with caramel.",
            "The McCafé® Caramel Frappé features rich caramel flavor blended with ice and our creamy dairy base, then topped with whipped topping and a caramel drizzle.",
            510, "Milk, Soy", "Popular", img("CaramelFrappe"),
        ),
        item(
            "McCafé® Mocha Frappé",
            sml(4.29, 4.99, 5.69),
            "A chocolatey, creamy mocha frappé blended with ice and topped with whipped cream.",
            "The McCafé® Mocha Frappé features rich chocolate flavor blended with ice and our creamy dairy base, then topped with whipped topping and a chocolate drizzle.",
            500, "Milk, Soy", "Popular", img("MochaFrappe"),
        ),
        item(
            "McCafé® Latte",
            sml(3.49, 3.99, 4.49),
            "A classic espresso drink with steamed whole milk and a light layer of foam.",
            "The McCafé® Latte is made with espresso and steamed whole milk, then finished with a light layer of foam. Rich and smooth.",
            170, "Milk", "", img("Latte"),
        ),
        item(
            "McCafé® Caramel Latte",
            sml(3.99, 4.49, 4.99),
            "Espresso with steamed whole milk and rich caramel syrup, topped with a light foam.",
            "The McCafé® Caramel Latte features espresso combined with steamed whole milk and caramel syrup, then topped with a light layer of foam for a sweet, creamy coffee.",
            260, "Milk", "", img("CaramelLatte"),
        ),
        item(
            "McCafé® Iced Latte",
            sml(3.49, 3.99, 4.49),
            "Espresso combined with whole milk and served over ice for a cool, creamy coffee.",
            "The McCafé® Iced Latte is made with espresso and whole milk, served over ice for a cool, creamy, and refreshing coffee drink.",
            110, "Milk", "", img("IcedLatte"),
        ),
        item(
            "McCafé® Iced Caramel Latte",
            sml(3.99, 4.49, 4.99),
            "Espresso, whole milk, and rich caramel syrup served over ice.",
            "The McCafé® Iced Caramel Latte features espresso combined with whole milk and rich caramel syrup, served over ice for a cool, sweet, creamy coffee.",
            210, "Milk", "", img("IcedCaramelLatte"),
        ),
        item(
            "McCafé® Mocha",
            sml(3.99, 4.49, 4.99),
            "Espresso with steamed whole milk and rich chocolate syrup, topped with whipped cream.",
            "The McCafé® Mocha features espresso with steamed whole milk, rich chocolate syrup, and whipped light cream, finished with a chocolate drizzle.",
            340, "Milk, Soy", "", img("Mocha"),
        ),
        item(
            "McCafé® Iced Mocha",
            sml(3.99, 4.49, 4.99),
            "Espresso with whole milk and chocolate syrup, served over ice and topped with whipped cream.",
            "The McCafé® Iced Mocha features espresso combined with whole milk, rich chocolate syrup, and ice, finished with whipped light cream and a chocolate drizzle.",
            310, "Milk, Soy", "", img("IcedMocha"),
        ),
        item(
            "McCafé® Caramel Macchiato",
            sml(3.99, 4.49, 4.99),
            "Espresso layered with steamed whole milk and vanilla syrup, finished with a caramel drizzle.",
            "The McCafé® Caramel Macchiato features espresso layered with steamed whole milk and vanilla syrup, then topped with a caramel drizzle for a sweet, layered coffee experience.",
            240, "Milk", "", img("CaramelMacchiato"),
        ),
        item(
            "McCafé® Iced Caramel Macchiato",
            sml(3.99, 4.49, 4.99),
            "Espresso layered over whole milk and vanilla syrup on ice, finished with a caramel drizzle.",
            "The McCafé® Iced Caramel Macchiato features espresso layered over whole milk and vanilla syrup served over ice, finished with a caramel drizzle.",
            200, "Milk", "", img("IcedCaramelMacchiato"),
        ),
        item(
            "McCafé® Cappuccino",
            sml(3.49, 3.99, 4.49),
            "A classic cappuccino with espresso, steamed whole milk, and a thick layer of foam.",
            "The McCafé® Cappuccino is made with espresso and steamed whole milk, then topped with a thick layer of rich, creamy foam.",
            120, "Milk", "", img("Cappuccino"),
        ),
        item(
            "McCafé® Americano",
            sml(2.29, 2.79, 3.29),
            "Espresso diluted with hot water for a bold, smooth coffee experience.",
            "The McCafé® Americano is made with Rainforest Alliance Certified™ espresso, diluted with hot water to create a rich, bold, and smooth coffee.",
            0, "", "", img("Americano"),
        ),
        item(
            "McCafé® Hot Chocolate",
            sml(2.49, 2.99, 3.49),
            "Steamed whole milk with rich chocolate syrup, topped with whipped cream and a chocolate drizzle.",
            "McCafé® Hot Chocolate is made with steamed whole milk, rich chocolate syrup, and whipped light cream, finished with a chocolate drizzle. A warm, comforting treat.",
            370, "Milk, Soy", "", img("HotChocolate"),
        ),
        item(
            "McCafé® Hot Tea",
            sml(1.29, 1.59, 1.79),
            "A selection of hot teas to suit your taste — choose from a variety of flavors.",
            "McCafé® Hot Tea is available in a variety of flavors. Simply choose your favorite and enjoy it however you like — with cream, sugar, or just plain.",
            0, "", "", img("HotTea"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# BEVERAGES  (20 items)
# ---------------------------------------------------------------------------
beverages = {
    "category": "Beverages",
    "items": [
        item(
            "Coca-Cola®",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Coca-Cola® — the perfect refreshment to pair with your McDonald's favorites.",
            "Enjoy an ice-cold Coca-Cola® at McDonald's. A bubbly, refreshing classic that's the perfect complement to any meal.",
            200, "", "Best Seller", img("CocaCola"),
        ),
        item(
            "Diet Coke®",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Diet Coke® with zero calories and the same great refreshing taste.",
            "Enjoy a refreshing Diet Coke® at McDonald's. All the great taste of Coca-Cola® with zero calories.",
            0, "", "Popular", img("DietCoke"),
        ),
        item(
            "Sprite®",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Sprite® — a lemon-lime flavored soft drink that's caffeine free.",
            "Enjoy an ice-cold Sprite® at McDonald's. A crisp, clean, lemon-lime flavored soft drink that's naturally caffeine free.",
            200, "", "Popular", img("Sprite"),
        ),
        item(
            "Dr Pepper®",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Dr Pepper® — the unique blend of 23 flavors.",
            "Enjoy an ice-cold Dr Pepper® at McDonald's. The unique blend of 23 flavors makes Dr Pepper® a refreshing one-of-a-kind soft drink.",
            200, "", "", img("DrPepper"),
        ),
        item(
            "Fanta® Orange",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Fanta® Orange — a bright, bubbly orange-flavored soft drink.",
            "Enjoy an ice-cold Fanta® Orange at McDonald's. A bright, bubbly, and fun orange-flavored soft drink.",
            210, "", "", img("FantaOrange"),
        ),
        item(
            "Hi-C® Orange Lavaburst®",
            sml(1.39, 1.79, 2.19),
            "Hi-C® Orange Lavaburst® — a fruity, refreshing orange drink that's a McDonald's classic.",
            "Hi-C® Orange Lavaburst® is a fruity, refreshing orange drink and a beloved McDonald's classic. Caffeine free.",
            230, "", "Popular", img("HiCOrangeLavaburst"),
        ),
        item(
            "Barq's® Root Beer",
            sml(1.39, 1.79, 2.19),
            "Ice-cold Barq's® Root Beer — a bold, refreshing root beer with a bite.",
            "Enjoy a Barq's® Root Beer at McDonald's. It's the bold, refreshing root beer with a bite that root beer lovers enjoy.",
            220, "", "", img("BarqsRootBeer"),
        ),
        item(
            "Hawaiian Punch®",
            sml(1.39, 1.79, 2.19),
            "A sweet, fruity punch drink perfect for kids and adults alike.",
            "Hawaiian Punch® at McDonald's delivers a sweet, fruity taste that's refreshing and fun. A perfect choice for any meal.",
            210, "", "", img("HawaiianPunch"),
        ),
        item(
            "Sweet Iced Tea",
            sml(1.39, 1.79, 2.19),
            "Refreshing iced tea sweetened to perfection. A Southern classic at McDonald's.",
            "McDonald's Sweet Iced Tea is freshly brewed, sweetened to perfection, and served ice cold. A refreshing complement to any meal.",
            130, "", "Popular", img("SweetIcedTea"),
        ),
        item(
            "Unsweetened Iced Tea",
            sml(1.39, 1.79, 2.19),
            "Refreshing, freshly brewed iced tea with zero sugar, served ice cold.",
            "McDonald's Unsweetened Iced Tea is freshly brewed and served ice cold with no added sugar. A refreshing, zero-calorie beverage option.",
            0, "", "", img("UnsweetenedIcedTea"),
        ),
        item(
            "Frozen Coca-Cola®",
            sml(1.79, 2.29, 2.79),
            "An ice-cold frozen Coca-Cola® slushie that's extra refreshing on a hot day.",
            "The Frozen Coca-Cola® features the classic taste of Coca-Cola® in an icy, slushy form. Extra refreshing and perfect for hot days.",
            150, "", "", img("FrozenCoke"),
        ),
        item(
            "Frozen Fanta® Blue Raspberry",
            sml(1.79, 2.29, 2.79),
            "A cool, icy, blue raspberry-flavored frozen drink that's fun and refreshing.",
            "The Frozen Fanta® Blue Raspberry is a cool, icy slush bursting with blue raspberry flavor. A fun and refreshing frozen treat.",
            160, "", "", img("FrozenFantaBlueRaspberry"),
        ),
        item(
            "Frozen Fanta® Wild Cherry",
            sml(1.79, 2.29, 2.79),
            "A cool, icy, wild cherry-flavored frozen drink that's bold and refreshing.",
            "The Frozen Fanta® Wild Cherry is a cool, icy slush bursting with wild cherry flavor. Bold, sweet, and refreshing.",
            160, "", "", img("FrozenFantaWildCherry"),
        ),
        item(
            "Strawberry Banana Smoothie",
            sml(3.49, 4.29, 4.99),
            "A creamy smoothie made with strawberry and banana puree, low-fat yogurt, and ice.",
            "The Strawberry Banana Smoothie is a creamy blend of strawberry puree, banana puree, and low-fat yogurt, blended with ice for a cool, refreshing treat.",
            210, "Milk", "Popular", img("StrawberryBananaSmoothie"),
        ),
        item(
            "Mango Pineapple Smoothie",
            sml(3.49, 4.29, 4.99),
            "A creamy tropical smoothie made with mango and pineapple puree, low-fat yogurt, and ice.",
            "The Mango Pineapple Smoothie is a creamy blend of mango and pineapple puree and low-fat yogurt, blended with ice for a cool, tropical treat.",
            220, "Milk", "", img("MangoPineappleSmoothie"),
        ),
        item(
            "Minute Maid® Orange Juice",
            sml(2.19, 2.69, 3.29),
            "100% orange juice squeezed from Minute Maid® oranges for a refreshing citrus taste.",
            "Minute Maid® Orange Juice at McDonald's is 100% pure squeezed orange juice. A great source of Vitamin C to start your morning.",
            150, "", "", img("MinuteMaidOJ"),
        ),
        item(
            "Dasani® Bottled Water",
            std(1.49),
            "Purified, refreshing Dasani® Bottled Water.",
            "Stay hydrated with Dasani® Bottled Water. Purified water enhanced with minerals for a pure, fresh taste.",
            0, "", "", img("DasaniWater"),
        ),
        item(
            "1% Low Fat Milk Jug",
            std(1.69),
            "A cold, creamy jug of 1% low fat milk. A great source of calcium and Vitamin D.",
            "A cold, creamy jug of 1% low fat milk. A great source of calcium and Vitamin D for kids and adults.",
            100, "Milk", "", img("LowFatMilk"),
        ),
        item(
            "Reduced Sugar* Low Fat Chocolate Milk Jug",
            std(1.89),
            "A cold, creamy jug of reduced sugar low fat chocolate milk. A kid favorite.",
            "A cold, creamy jug of reduced sugar* low fat chocolate milk. Great taste with less sugar. *25% less sugar than our regular chocolate milk.",
            130, "Milk", "", img("ChocolateMilk"),
        ),
        item(
            "Honest Kids® Apple Juice",
            std(1.49),
            "Organic apple juice for kids — made with no artificial sweeteners.",
            "Honest Kids® Apple Juice is an organic juice drink made with no high fructose corn syrup or artificial sweeteners. A great drink option for kids.",
            35, "", "", img("HonestKidsAppleJuice"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# SWEETS & TREATS  (20 items)
# ---------------------------------------------------------------------------
sweets = {
    "category": "Sweets & Treats",
    "items": [
        item(
            "McFlurry® with OREO® Cookies",
            reg(4.69),
            "Creamy vanilla soft serve swirled with crunchy OREO® cookie pieces.",
            "The McFlurry® with OREO® Cookies is a creamy vanilla soft serve swirled with crunchy OREO® cookie pieces. A delicious frozen treat that's the perfect ending to any meal.",
            510, "Wheat, Milk, Soy", "Best Seller", img("McFlurryOreo"),
        ),
        item(
            "McFlurry® with M&M'S® Candies",
            reg(4.69),
            "Creamy vanilla soft serve mixed with colorful M&M'S® chocolate candies.",
            "The McFlurry® with M&M'S® Candies features creamy vanilla soft serve with colorful, crunchy M&M'S® chocolate candies mixed in. A fun, tasty treat.",
            640, "Wheat, Milk, Soy, Peanut", "Popular", img("McFlurryMMs"),
        ),
        item(
            "McFlurry® with OREO® Fudge",
            reg(4.99),
            "Creamy vanilla soft serve swirled with OREO® cookie pieces and rich hot fudge.",
            "The McFlurry® with OREO® Fudge features creamy vanilla soft serve swirled with crunchy OREO® cookie pieces and a rich hot fudge topping for the ultimate indulgence.",
            570, "Wheat, Milk, Soy", "", img("McFlurryOreoFudge"),
        ),
        item(
            "Chocolate Shake",
            sml(3.49, 4.29, 4.99),
            "A thick and creamy chocolate milkshake topped with whipped cream.",
            "The McDonald's Chocolate Shake is made with creamy vanilla soft serve and chocolate syrup, topped with whipped light cream. Thick, creamy, and chocolatey.",
            530, "Milk", "Popular", img("ChocolateShake"),
        ),
        item(
            "Vanilla Shake",
            sml(3.49, 4.29, 4.99),
            "A thick and creamy vanilla milkshake topped with whipped cream.",
            "The McDonald's Vanilla Shake is made with creamy vanilla soft serve, topped with whipped light cream. Classic, smooth, and satisfying.",
            490, "Milk", "Popular", img("VanillaShake"),
        ),
        item(
            "Strawberry Shake",
            sml(3.49, 4.29, 4.99),
            "A thick and creamy strawberry milkshake topped with whipped cream.",
            "The McDonald's Strawberry Shake is made with creamy vanilla soft serve and strawberry syrup, topped with whipped light cream. Sweet and fruity.",
            510, "Milk", "", img("StrawberryShake"),
        ),
        item(
            "Vanilla Cone",
            std(1.69),
            "A classic vanilla soft serve cone. Cool, creamy, and refreshing.",
            "The Vanilla Cone features our creamy vanilla soft serve in a crispy cone. Cool, creamy, and just the right size for a satisfying treat.",
            200, "Wheat, Milk", "Value Pick", img("VanillaCone"),
        ),
        item(
            "Hot Fudge Sundae",
            std(2.49),
            "Creamy vanilla soft serve topped with hot fudge sauce.",
            "The Hot Fudge Sundae features creamy McDonald's vanilla soft serve topped with hot, thick fudge sauce. A rich, indulgent treat.",
            330, "Milk, Soy", "Popular", img("HotFudgeSundae"),
        ),
        item(
            "Hot Caramel Sundae",
            std(2.49),
            "Creamy vanilla soft serve topped with warm, buttery caramel sauce.",
            "The Hot Caramel Sundae features creamy McDonald's vanilla soft serve topped with warm, buttery caramel sauce. Sweet and satisfying.",
            340, "Milk", "", img("HotCaramelSundae"),
        ),
        item(
            "Plain Sundae",
            std(1.99),
            "Creamy vanilla soft serve served in a cup — simple and delicious.",
            "The Plain Sundae is simple and delicious — creamy McDonald's vanilla soft serve served in a sundae cup. Perfect for those who love the soft serve on its own.",
            200, "Milk", "", img("PlainSundae"),
        ),
        item(
            "Baked Apple Pie",
            std(1.89),
            "A classic McDonald's dessert with a warm, flaky crust and sweet apple filling.",
            "The Baked Apple Pie features 100% American-grown apples, baked in a flaky, buttery crust seasoned with cinnamon. A classic McDonald's dessert.",
            230, "Wheat, Milk, Soy", "Popular", img("BakedApplePie"),
        ),
        item(
            "Cherry & Crème Pie",
            std(1.89),
            "A warm, flaky crust filled with sweet cherry filling and smooth crème.",
            "The Cherry & Crème Pie features a warm, flaky crust filled with sweet cherry filling and a smooth, creamy vanilla crème. A limited-time treat.",
            240, "Wheat, Milk, Soy, Egg", "", img("CherryPie"),
        ),
        item(
            "Blueberry & Crème Pie",
            std(1.89),
            "A warm, flaky crust filled with blueberry filling and smooth crème.",
            "The Blueberry & Crème Pie features a warm, flaky crust filled with real blueberry filling and a smooth, creamy vanilla crème.",
            240, "Wheat, Milk, Soy, Egg", "", img("BlueberryPie"),
        ),
        item(
            "Chocolate Chip Cookie",
            std(1.29),
            "A warm, soft, chewy chocolate chip cookie baked fresh in our restaurants.",
            "The Chocolate Chip Cookie is soft, chewy, and baked fresh right in our restaurants. Made with semi-sweet chocolate chips for chocolate lovers.",
            170, "Wheat, Milk, Egg, Soy", "", img("ChocolateChipCookie"),
        ),
        item(
            "3 Pack Cookies — Chocolate Chip",
            std(3.29),
            "Three warm, soft, chewy chocolate chip cookies baked fresh in our restaurants.",
            "Get three of our soft, chewy Chocolate Chip Cookies baked fresh right in our restaurants. Made with semi-sweet chocolate chips — perfect for sharing (or not).",
            510, "Wheat, Milk, Egg, Soy", "", img("3PackChocolateChipCookie"),
        ),
        item(
            "Oatmeal Raisin Cookie",
            std(1.29),
            "A warm, soft oatmeal raisin cookie baked fresh in our restaurants.",
            "The Oatmeal Raisin Cookie is soft, chewy, and baked fresh in our restaurants with hearty oats and sweet raisins.",
            150, "Wheat, Milk, Egg, Soy", "", img("OatmealRaisinCookie"),
        ),
        item(
            "Sugar Cookie",
            std(1.29),
            "A warm, soft sugar cookie baked fresh in our restaurants.",
            "The Sugar Cookie is soft, sweet, and baked fresh right in our restaurants. A simple, classic cookie treat.",
            160, "Wheat, Milk, Egg", "", img("SugarCookie"),
        ),
        item(
            "Blueberry Muffin",
            std(2.79),
            "A soft, fluffy blueberry muffin loaded with real blueberries.",
            "The Blueberry Muffin is a soft, fluffy muffin loaded with real blueberries and topped with a sweet streusel crumble. Perfect for breakfast or a snack.",
            460, "Wheat, Milk, Egg, Soy", "", img("BlueberryMuffin"),
        ),
        item(
            "Apple Fritter",
            std(2.79),
            "A warm, glazed apple fritter with chunks of real apple and cinnamon.",
            "The Apple Fritter is a warm, sweet, glazed fritter packed with chunks of real apple and a touch of cinnamon. Baked fresh in our restaurants.",
            510, "Wheat, Milk, Egg, Soy", "", img("AppleFritter"),
        ),
        item(
            "Cinnamon Roll",
            std(2.79),
            "A warm, gooey cinnamon roll topped with sweet cream cheese icing.",
            "The Cinnamon Roll is warm, gooey, and topped with sweet cream cheese icing. Baked fresh daily in our restaurants for a delicious treat any time.",
            480, "Wheat, Milk, Egg, Soy", "", img("CinnamonRoll"),
        ),
    ],
}

# ---------------------------------------------------------------------------
# Assemble all categories
# ---------------------------------------------------------------------------
ALL_CATEGORIES = [
    burgers,
    chicken_sandwiches,
    nuggets,
    meals,
    breakfast,
    fries_sides,
    happy_meal,
    mccafe,
    beverages,
    sweets,
]

menu = {"menuItems": ALL_CATEGORIES}

# ---------------------------------------------------------------------------
# Write JSON
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_PATH = os.path.join(REPO_ROOT, "app", "frontend", "src", "data", "mcdonalds-menu-items.json")

os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(menu, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*60}")
print(f"  McDonald's Menu JSON — Build Complete")
print(f"{'='*60}")
print(f"  Output : {OUTPUT_PATH}")
file_size_kb = os.path.getsize(OUTPUT_PATH) / 1024
print(f"  Size   : {file_size_kb:.1f} KB")
print()
total = 0
for cat in ALL_CATEGORIES:
    count = len(cat["items"])
    total += count
    print(f"  {cat['category']:<30s}  {count:>3d} items")
print(f"  {'─'*40}")
print(f"  {'TOTAL':<30s}  {total:>3d} items")
print(f"{'='*60}\n")

# Quick validation
errors = []
for cat in ALL_CATEGORIES:
    for itm in cat["items"]:
        for field in ("name", "sizes", "description", "longDescription", "origin", "calories", "allergens", "popularity", "image"):
            if field not in itm:
                errors.append(f"Missing '{field}' in {itm.get('name', '???')}")
        if not itm["sizes"]:
            errors.append(f"Empty sizes for {itm['name']}")

if errors:
    print("VALIDATION ERRORS:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("✓ All items validated — schema OK\n")
