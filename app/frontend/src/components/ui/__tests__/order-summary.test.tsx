import { render, screen } from "@testing-library/react";
import OrderSummary, { calculateOrderSummary, OrderItem, OrderSummaryProps } from "../order-summary";

describe("OrderSummary", () => {
    const sampleItems: OrderItem[] = [
        { item: "Big Mac", size: "standard", quantity: 2, price: 5.99, display: "Big Mac" },
        { item: "Large Fries", size: "standard", quantity: 1, price: 3.79, display: "Large Fries" }
    ];

    it("renders McDonald's items with the correct totals", () => {
        const summary = calculateOrderSummary(sampleItems);
        render(<OrderSummary order={summary} />);

        expect(screen.getByText("Your McDonald's Order")).toBeInTheDocument();
        expect(screen.getByText(/Big Mac/)).toBeInTheDocument();
        expect(screen.getByText(/Large Fries/)).toBeInTheDocument();
        expect(screen.getByText(`$${summary.total.toFixed(2)}`)).toBeInTheDocument();
        expect(screen.getByText(`$${summary.finalTotal.toFixed(2)}`)).toBeInTheDocument();
    });

    it("shows the empty-state helper when no items are present", () => {
        const emptySummary: OrderSummaryProps = { items: [], total: 0, tax: 0, finalTotal: 0 };
        render(<OrderSummary order={emptySummary} />);

        expect(screen.getByText(/Add a Big Mac, McNuggets, or McFlurry/i)).toBeInTheDocument();
    });
});
