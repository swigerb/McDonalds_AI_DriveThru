import { calculateOrderSummary, OrderItem } from "../order-summary";

describe("calculateOrderSummary", () => {
    it("returns zeros for an empty item list", () => {
        const result = calculateOrderSummary([]);
        expect(result.items).toHaveLength(0);
        expect(result.total).toBe(0);
        expect(result.tax).toBe(0);
        expect(result.finalTotal).toBe(0);
    });

    it("computes correct totals for a single item", () => {
        const items: OrderItem[] = [
            { item: "Glazed Donut", size: "standard", quantity: 1, price: 1.49, display: "Glazed Donut" }
        ];
        const result = calculateOrderSummary(items);
        expect(result.total).toBeCloseTo(1.49);
        expect(result.tax).toBeCloseTo(1.49 * 0.08);
        expect(result.finalTotal).toBeCloseTo(1.49 * 1.08);
    });

    it("computes correct totals for multiple items with quantities", () => {
        const items: OrderItem[] = [
            { item: "Latte", size: "medium", quantity: 2, price: 4.99, display: "Medium Latte" },
            { item: "Bagel", size: "standard", quantity: 3, price: 2.49, display: "Bagel" }
        ];
        const expectedTotal = 2 * 4.99 + 3 * 2.49;
        const result = calculateOrderSummary(items);
        expect(result.total).toBeCloseTo(expectedTotal);
        expect(result.tax).toBeCloseTo(expectedTotal * 0.08);
        expect(result.finalTotal).toBeCloseTo(expectedTotal * 1.08);
    });

    it("handles zero-price items", () => {
        const items: OrderItem[] = [
            { item: "Free Sample", size: "small", quantity: 1, price: 0, display: "Small Free Sample" }
        ];
        const result = calculateOrderSummary(items);
        expect(result.total).toBe(0);
        expect(result.finalTotal).toBe(0);
    });
});
