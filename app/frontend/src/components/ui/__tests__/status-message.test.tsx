import { render, screen } from "@testing-library/react";
import StatusMessage from "../status-message";
import { LocalModeProvider } from "@/context/local-mode-context";

const renderWithProvider = (ui: React.ReactElement) =>
    render(<LocalModeProvider>{ui}</LocalModeProvider>);

describe("StatusMessage", () => {
    it("renders the idle helper when recording is disabled", () => {
        renderWithProvider(<StatusMessage isRecording={false} />);
        expect(screen.getByText("status.notRecordingMessage")).toBeInTheDocument();
    });

    it("renders the live equalizer label while recording", () => {
        const { container } = renderWithProvider(<StatusMessage isRecording />);
        expect(screen.getByText("status.conversationInProgress")).toBeInTheDocument();
        expect(container.querySelector(".listening-equalizer")).not.toBeNull();
    });

    it("tells the guest the session ended after inactivity", () => {
        renderWithProvider(<StatusMessage isRecording={false} notice="idle" />);
        expect(screen.getByText("status.sessionEndedIdle")).toBeInTheDocument();
    });

    it("tells the guest the connection dropped", () => {
        renderWithProvider(<StatusMessage isRecording={false} notice="lost" />);
        expect(screen.getByText("status.connectionLost")).toBeInTheDocument();
    });
});
