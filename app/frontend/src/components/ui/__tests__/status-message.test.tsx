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

    it("says one moment while a rate-limited reply is retried, keeping the live equalizer", () => {
        const { container } = renderWithProvider(<StatusMessage isRecording busyNotice="busy" />);
        expect(screen.getByText("status.rateLimited")).toBeInTheDocument();
        expect(screen.queryByText("status.conversationInProgress")).toBeNull();
        expect(container.querySelector(".listening-equalizer")).not.toBeNull();
    });

    it("asks the guest to repeat themselves once the retries are exhausted", () => {
        renderWithProvider(<StatusMessage isRecording busyNotice="final" />);
        expect(screen.getByText("status.rateLimitedFinal")).toBeInTheDocument();
    });

    it.each([
        ["reconnecting", "status.reconnecting"],
        ["resumed", "status.resumed"],
        ["tapToResume", "status.resumedTapToContinue"],
        ["resumeRejected", "status.resumeRejected"],
        ["superseded", "status.superseded"]
    ] as const)("renders the %s notice when the mic is off", (notice, key) => {
        renderWithProvider(<StatusMessage isRecording={false} notice={notice} />);
        expect(screen.getByText(key)).toBeInTheDocument();
    });

    it("shows the reconnected line while listening after a resume", () => {
        const { container } = renderWithProvider(<StatusMessage isRecording notice="resumed" />);
        expect(screen.getByText("status.resumed")).toBeInTheDocument();
        expect(screen.queryByText("status.conversationInProgress")).not.toBeInTheDocument();
        expect(container.querySelector(".listening-equalizer")).not.toBeNull();
    });

    it("other notices don't replace the listening label", () => {
        renderWithProvider(<StatusMessage isRecording notice="reconnecting" />);
        expect(screen.getByText("status.conversationInProgress")).toBeInTheDocument();
    });

    it("a rate-limit notice outranks the reconnected line", () => {
        renderWithProvider(<StatusMessage isRecording notice="resumed" busyNotice="busy" />);
        expect(screen.getByText("status.rateLimited")).toBeInTheDocument();
        expect(screen.queryByText("status.resumed")).not.toBeInTheDocument();
    });
});
