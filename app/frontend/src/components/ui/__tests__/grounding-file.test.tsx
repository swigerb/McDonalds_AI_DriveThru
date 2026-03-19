import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GroundingFile from "../grounding-file";

describe("GroundingFile", () => {
    const file = { id: "1", name: "menu-data.pdf", content: "sample content" };

    it("renders the file name", () => {
        render(<GroundingFile value={file} onClick={() => {}} />);
        expect(screen.getByText("menu-data.pdf")).toBeInTheDocument();
    });

    it("calls onClick when the button is clicked", async () => {
        const handleClick = vi.fn();
        render(<GroundingFile value={file} onClick={handleClick} />);
        await userEvent.click(screen.getByRole("button"));
        expect(handleClick).toHaveBeenCalledOnce();
    });
});
