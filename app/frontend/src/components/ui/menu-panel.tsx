import menuItemsData from "@/data/menuItems.json";
import { memo, useCallback, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { useMenuModeContext } from "@/context/menu-mode-context";

interface Size {
    size: string;
    price: number;
}

interface MenuItem {
    name: string;
    sizes: Size[];
    description: string;
    mealNumber?: number | null;
    calories?: number;
    menuPeriod?: "breakfast" | "lunch" | "allDay";
}

interface MenuCategory {
    category: string;
    items: MenuItem[];
}

const categoryIcons: Record<string, string> = {
    "Extra Value Meals": "🏷️",
    "Burgers & Sandwiches": "🍔",
    "Chicken & McNuggets®": "🍗",
    "Breakfast": "🥞",
    "Fries, Sides & Drinks": "🍟",
    "Sweets & Treats": "🍦",
    "McCafé® Coffees": "☕",
    "Beverages": "🥤",
    "Happy Meal®": "😊",
    "Meals & Combos": "🍟",
    "McNuggets® & Tenders": "🍗",
};

function getCategoryDisplay(category: string, menuMode: "breakfast" | "lunch"): { displayName: string; icon: string } {
    if (category === "Fries, Sides & Drinks") {
        return menuMode === "breakfast"
            ? { displayName: "Sides & Drinks", icon: "☕" }
            : { displayName: "Fries, Sides & Drinks", icon: "🍟" };
    }
    return { displayName: category, icon: categoryIcons[category] ?? "🍽️" };
}

const allCategories = menuItemsData.menuItems as MenuCategory[];

function isItemVisible(item: MenuItem, menuMode: "breakfast" | "lunch"): boolean {
    if (!item.menuPeriod || item.menuPeriod === "allDay") return true;
    return item.menuPeriod === menuMode;
}

export default memo(function MenuPanel() {
    const { menuMode } = useMenuModeContext();

    const menuItems = useMemo(() => {
        // Build filtered Extra Value Meals from items with mealNumber matching current mode
        const valueMealItems: MenuItem[] = [];
        for (const cat of allCategories) {
            for (const item of cat.items) {
                if ((item as MenuItem).mealNumber && isItemVisible(item as MenuItem, menuMode)) {
                    valueMealItems.push(item as MenuItem);
                }
            }
        }
        valueMealItems.sort((a, b) => (a.mealNumber ?? 99) - (b.mealNumber ?? 99));

        const valueMealsCategory: MenuCategory = {
            category: "Extra Value Meals",
            items: valueMealItems,
        };

        // Filter regular categories
        const filteredCategories: MenuCategory[] = allCategories
            .map(cat => ({
                ...cat,
                items: (cat.items as MenuItem[]).filter(item => isItemVisible(item, menuMode) && !(item as MenuItem).mealNumber),
            }))
            .filter(cat => cat.items.length > 0);

        return valueMealItems.length > 0
            ? [valueMealsCategory, ...filteredCategories]
            : filteredCategories;
    }, [menuMode]);

    const [expanded, setExpanded] = useState<Set<string>>(() => new Set(menuItems.map(c => c.category)));

    const toggle = useCallback((category: string) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(category)) {
                next.delete(category);
            } else {
                next.add(category);
            }
            return next;
        });
    }, []);

    return (
        <div className="space-y-4">
            {menuItems.map(category => {
                const isOpen = expanded.has(category.category);
                const isValueMeals = category.category === "Extra Value Meals";
                const { displayName, icon } = getCategoryDisplay(category.category, menuMode);
                return (
                    <div
                        key={category.category}
                        className={`rounded-3xl border shadow-[0_15px_35px_rgba(39,37,31,0.08)] dark:shadow-[0_25px_55px_rgba(0,0,0,0.65)] ${
                            isValueMeals
                                ? "border-[#FFBC0D]/40 bg-gradient-to-br from-[#FFBC0D]/10 to-white/80 dark:from-[#FFBC0D]/10 dark:to-[#1a1812]/95 dark:border-[#FFBC0D]/30"
                                : "border-primary/10 bg-white/80 dark:border-white/10 dark:bg-[#1a1812]/95"
                        }`}
                    >
                        <button
                            type="button"
                            onClick={() => toggle(category.category)}
                            className="flex w-full cursor-pointer items-center justify-between gap-3 p-4"
                            aria-expanded={isOpen}
                        >
                            <div className="flex items-center gap-2 sm:gap-3">
                                <span className="text-2xl" aria-hidden>
                                    {icon}
                                </span>
                                <h3 className={`break-keep text-left font-semibold uppercase tracking-wide ${
                                    isValueMeals ? "text-[#DB0007] dark:text-[#FFBC0D]" : "text-primary dark:text-primary"
                                }`}>
                                    {displayName}
                                </h3>
                            </div>
                            <div className="flex items-center gap-2">
                                <span className="whitespace-nowrap rounded-full bg-[#27251F]/10 px-3 py-1 text-xs font-bold text-[#27251F] dark:bg-[#211f18] dark:text-[#FFBC0D]">
                                    {category.items.length} items
                                </span>
                                <motion.span
                                    animate={{ rotate: isOpen ? 180 : 0 }}
                                    transition={{ duration: 0.2 }}
                                    className="text-primary/60 dark:text-white/50"
                                >
                                    <ChevronDown size={18} />
                                </motion.span>
                            </div>
                        </button>

                        <AnimatePresence initial={false}>
                            {isOpen && (
                                <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: "auto", opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    transition={{ duration: 0.25, ease: "easeInOut" }}
                                    className="overflow-hidden"
                                >
                                    <div className="space-y-4 px-4 pb-4">
                                        {category.items.map(item => (
                                            <div
                                                key={`${category.category}-${item.name}`}
                                                className={`rounded-2xl border border-dashed p-3 transition-colors ${
                                                    (item as MenuItem).mealNumber && isValueMeals
                                                        ? "border-[#FFBC0D]/30 bg-[#FFBC0D]/5 dark:border-[#FFBC0D]/20 dark:bg-[#FFBC0D]/5"
                                                        : "border-primary/20 bg-white/70 dark:border-white/10 dark:bg-white/5"
                                                }`}
                                            >
                                                <div className="flex flex-wrap items-baseline justify-between gap-2">
                                                    <div className="pr-1">
                                                        <div className="flex items-center gap-2">
                                                            {(item as MenuItem).mealNumber && (
                                                                <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[#DB0007] text-xs font-bold text-white">
                                                                    {(item as MenuItem).mealNumber}
                                                                </span>
                                                            )}
                                                            <span className="font-semibold text-foreground dark:text-white">{item.name}</span>
                                                        </div>
                                                        <p className="text-sm text-muted-foreground">{item.description}</p>
                                                        {(item as MenuItem).calories ? (
                                                            <p className="mt-0.5 text-xs text-muted-foreground/70">{(item as MenuItem).calories} Cal</p>
                                                        ) : null}
                                                    </div>
                                                    <div className="text-right">
                                                        {item.sizes.map(({ size, price }) => (
                                                            <div key={size} className="font-mono text-sm text-foreground/80 dark:text-white/80">
                                                                {size !== "standard" && size !== "Standard" ? <span className="capitalize">{`${size}: `}</span> : null}
                                                                <span>${price.toFixed(2)}</span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                );
            })}
        </div>
    );
});
