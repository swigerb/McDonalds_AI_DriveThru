import { render, screen } from "@testing-library/react";
import LoadingSpinner from "../loading-spinner";

describe("LoadingSpinner", () => {
    it("renders the default loading message", () => {
        render(<LoadingSpinner />);
        expect(screen.getByText("Loading...")).toBeInTheDocument();
    });

    it("renders a custom message when provided", () => {
        render(<LoadingSpinner message="Please wait..." />);
        expect(screen.getByText("Please wait...")).toBeInTheDocument();
    });

    it("renders the spinner element", () => {
        const { container } = render(<LoadingSpinner />);
        const spinner = container.querySelector(".animate-spin");
        expect(spinner).not.toBeNull();
    });
});
