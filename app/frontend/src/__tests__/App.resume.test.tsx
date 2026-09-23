import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RootApp from "../App";

const rt = vi.hoisted(() => ({
    params: null as any,
    api: {
        startSession: vi.fn(),
        addUserAudio: vi.fn(),
        inputAudioBufferClear: vi.fn(),
        cancelResponse: vi.fn(),
        sendVerboseLogging: vi.fn(),
        sendLogToFile: vi.fn(),
        sendVoiceChoice: vi.fn(),
        sendLocalModeToggle: vi.fn(),
        sendPiperVoiceChoice: vi.fn(),
        endSession: vi.fn(),
        reconnect: vi.fn(async () => {}),
        readyState: 1,
        wsEndpoint: "/realtime?mode=cloud",
        retryCount: 0,
        maxRetries: 10,
        isConnected: true,
        needsReconnect: false
    }
}));
const rec = vi.hoisted(() => ({ start: vi.fn(async () => true), stop: vi.fn(async () => {}), mute: vi.fn(), unmute: vi.fn() }));
const player = vi.hoisted(() => ({ reset: vi.fn(async () => {}), play: vi.fn(), stop: vi.fn(), waitForDrain: vi.fn(async () => true) }));
const toasts = vi.hoisted(() => ({ toast: vi.fn(), dismissAllToasts: vi.fn() }));

vi.mock("@/hooks/useRealtime", () => ({
    default: (params: any) => {
        rt.params = params;
        return rt.api;
    }
}));
vi.mock("darkreader", () => ({ enable: vi.fn(), disable: vi.fn(), auto: vi.fn(), setFetchMethod: vi.fn() }));
vi.mock("@/hooks/useAudioRecorder", () => ({ default: () => rec }));
vi.mock("@/hooks/useAudioPlayer", () => ({ default: () => player }));
vi.mock("@/components/ui/use-toast", async importOriginal => ({ ...(await importOriginal<object>()), ...toasts }));

const BIG_MAC = { item: "Big Mac", size: "", quantity: 1, price: 5.99, display: "Big Mac (no pickles)" };
const FRIES = { item: "French Fries", size: "Large", quantity: 1, price: 3.79, display: "Large Fries (extra salt)" };
const orderOf = (...items: (typeof BIG_MAC)[]) => {
    const total = items.reduce((s, i) => s + i.price * i.quantity, 0);
    return { items, total, tax: total * 0.08, finalTotal: total * 1.08 };
};
const resumedMsg = (order = orderOf(BIG_MAC, FRIES)) => ({
    type: "extension.session_resumed" as const,
    order_summary: order,
    session_token: "SESSION-1",
    round_trip_index: 3,
    round_trip_token: "RT-3",
    resume_id: "RID-NEW"
});
const transportDrop = { code: 1011, reason: "", idle: false, kind: "transport", resuming: true };

const tapMic = async () => {
    await act(async () => {
        fireEvent.click(screen.getByLabelText(/app\.(start|stop)Recording/));
    });
};

const addBigMac = () =>
    act(() => rt.params.onReceivedExtensionMiddleTierToolResponse({ tool_name: "update_order", tool_result: JSON.stringify(orderOf(BIG_MAC)), previous_item_id: "x" }));

async function startConversationWithBigMac() {
    render(<RootApp />);
    await tapMic();
    addBigMac();
    expect(screen.getByText("Big Mac (no pickles)")).toBeInTheDocument();
}

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    rec.start.mockImplementation(async () => true);
    rt.api.isConnected = true;
    rt.api.needsReconnect = false;
    rt.api.readyState = 1;
});

describe("order resume in the app (cloud realtime)", () => {
    it("turns resume on for cloud realtime", () => {
        render(<RootApp />);
        expect(rt.params.resumeEnabled).toBe(true);
    });

    it("a mid-conversation drop pauses the mic and says it is reconnecting", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        expect(rec.stop).toHaveBeenCalled();
        expect(screen.getByText("status.reconnecting")).toBeInTheDocument();
        expect(screen.getByText("Big Mac (no pickles)")).toBeInTheDocument();
    });

    it("session_resumed restores the ticket, restarts the mic at once and resets nothing", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rec.start.mockClear();
        rt.api.startSession.mockClear();

        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));

        expect(screen.getByText("Big Mac (no pickles)")).toBeInTheDocument();
        expect(screen.getByText("Large Fries (extra salt)")).toBeInTheDocument();
        expect(rec.start).toHaveBeenCalledTimes(1);
        expect(rt.api.startSession).toHaveBeenCalledTimes(1);
        expect(rt.api.endSession).not.toHaveBeenCalled();
        expect(screen.getByText("status.resumed")).toBeInTheDocument();
        expect(screen.getByLabelText("app.stopRecording")).toBeInTheDocument();
    });

    it("re-sends the guest's voice ahead of session.update on the resumed socket", async () => {
        localStorage.setItem("voiceChoice", "cedar");
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rt.api.sendVoiceChoice.mockClear();
        rt.api.startSession.mockClear();

        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));

        expect(rt.api.sendVoiceChoice).toHaveBeenCalledWith("cedar");
        expect(rt.api.sendVoiceChoice.mock.invocationCallOrder[0]).toBeLessThan(rt.api.startSession.mock.invocationCallOrder[0]);
    });

    it("falls back to tap-to-continue when the browser won't restart the mic", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rec.start.mockImplementation(async () => false);

        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));

        expect(screen.getByText("status.resumedTapToContinue")).toBeInTheDocument();
        expect(screen.getByLabelText("app.startRecording")).toBeInTheDocument();
        expect(screen.getByText("Large Fries (extra salt)")).toBeInTheDocument();

        rec.start.mockImplementation(async () => true);
        rec.start.mockClear();
        await tapMic();
        // Resumed session: no greeting will come, so the mic starts without the 3.5 s wait.
        expect(rec.start).toHaveBeenCalledTimes(1);
        expect(screen.getByText("Large Fries (extra salt)")).toBeInTheDocument();
    });

    it("getUserMedia refusing after a resume also falls back to tap-to-continue", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rec.start.mockImplementation(async () => {
            throw new DOMException("denied", "NotAllowedError");
        });
        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));
        expect(screen.getByText("status.resumedTapToContinue")).toBeInTheDocument();
    });

    it("a resume while the guest was not talking restores the ticket without touching the mic", async () => {
        await startConversationWithBigMac();
        await tapMic(); // guest stops the conversation
        rec.start.mockClear();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));
        expect(rec.start).not.toHaveBeenCalled();
        expect(screen.getByText("Large Fries (extra salt)")).toBeInTheDocument();
        expect(screen.getByText("status.resumedTapToContinue")).toBeInTheDocument();
    });

    it("a tap while reconnecting is held (no toast) and the mic opens as soon as the order is back", async () => {
        await startConversationWithBigMac();
        await tapMic(); // guest stops
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rt.api.readyState = 0; // CONNECTING
        rec.start.mockClear();

        await tapMic();
        expect(toasts.toast).not.toHaveBeenCalled();
        expect(screen.getByLabelText("app.stopRecording")).toBeInTheDocument();
        expect(rec.start).not.toHaveBeenCalled(); // waiting: no greeting, but no socket yet either

        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));
        expect(rec.start).toHaveBeenCalledTimes(1);
        expect(screen.getByText("status.resumed")).toBeInTheDocument();
    });

    it("resume_rejected clears the ticket and asks for a fresh start", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rec.start.mockClear();
        await act(async () => rt.params.onReceivedResumeRejected({ type: "extension.resume_rejected", reason: "expired" }));
        expect(screen.queryByText("Big Mac (no pickles)")).not.toBeInTheDocument();
        expect(screen.getByText("status.resumeRejected")).toBeInTheDocument();
        expect(rec.start).not.toHaveBeenCalled();
    });

    it("idle close: no resume, ticket reset on the next tap", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost({ code: 4000, reason: "idle_timeout", idle: true, kind: "idle", resuming: false }));
        rt.api.needsReconnect = true;
        expect(screen.getByText("status.sessionEndedIdle")).toBeInTheDocument();
        await tapMic();
        expect(rt.api.reconnect).toHaveBeenCalled();
        expect(screen.queryByText("Big Mac (no pickles)")).not.toBeInTheDocument();
    });

    it("superseded: tells the guest the order moved to another window", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost({ code: 4002, reason: "superseded", idle: false, kind: "superseded", resuming: false }));
        expect(screen.getByText("status.superseded")).toBeInTheDocument();
    });

    it("a failed reconnect attempt in between still resumes the conversation the guest was in", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        await act(async () => rt.params.onConnectionLost({ ...transportDrop, code: 1006 })); // retry socket dropped too
        rec.start.mockClear();

        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));

        expect(rec.start).toHaveBeenCalledTimes(1);
        expect(screen.getByLabelText("app.stopRecording")).toBeInTheDocument();
    });

    it("retries giving up with no resume in flight says nothing on an idle page", async () => {
        render(<RootApp />);
        await act(async () => rt.params.onConnectionLost({ code: 1006, reason: "", idle: false, kind: "transport", resuming: false }));
        await act(async () => rt.params.onReconnectGaveUp());
        expect(screen.queryByText("status.connectionLost")).not.toBeInTheDocument();
        expect(screen.getByText("status.notRecordingMessage")).toBeInTheDocument();
    });

    it("retries exhausted during a resume: connection lost", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        await act(async () => rt.params.onReconnectGaveUp());
        expect(screen.getByText("status.connectionLost")).toBeInTheDocument();
    });

    it("start a new order: end_session and a clean ticket", async () => {
        await startConversationWithBigMac();
        await act(async () => {
            fireEvent.click(screen.getByText("app.newOrder"));
        });
        expect(rt.api.endSession).toHaveBeenCalledTimes(1);
        expect(screen.queryByText("Big Mac (no pickles)")).not.toBeInTheDocument();
        await act(async () => rt.params.onConnectionLost({ code: 1000, reason: "session_ended", idle: false, kind: "ended", resuming: false }));
        expect(screen.queryByText("status.connectionLost")).not.toBeInTheDocument();
    });

    it("a tap made before the old session finishes closing carries on into the fresh one", async () => {
        await startConversationWithBigMac();
        await tapMic(); // stop
        await act(async () => {
            fireEvent.click(screen.getByText("app.newOrder"));
        });
        await tapMic(); // start the next order at once
        rec.stop.mockClear();
        await act(async () => rt.params.onConnectionLost({ code: 1000, reason: "session_ended", idle: false, kind: "ended", resuming: false }));
        expect(rec.stop).not.toHaveBeenCalled();
        expect(screen.getByLabelText("app.stopRecording")).toBeInTheDocument();
        expect(screen.queryByText("status.connectionLost")).not.toBeInTheDocument();
    });

    it("a tap on a resumed session never waits for (or restarts the mic after) a greeting", async () => {
        await startConversationWithBigMac();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        rec.start.mockImplementation(async () => false);
        await act(async () => rt.params.onReceivedSessionResumed(resumedMsg()));
        rec.start.mockImplementation(async () => true);
        rec.start.mockClear();
        await tapMic();
        // The crew member's 30 s nudge finishing must not start a second capture.
        await act(async () =>
            rt.params.onReceivedResponseDone({ response: { output: [{ content: [{ transcript: "Anything else?" }] }] } })
        );
        expect(rec.start).toHaveBeenCalledTimes(1);
    });
});

// Resume is cloud realtime only. Local mode (Phi-4/Piper) and Azure Speech mode keep
// their pre-resume behaviour: no resume notices, no "Start a new order" button.
describe.each([
    ["local mode", "localMode"],
    ["Azure Speech mode", "useAzureSpeechOn"]
])("%s never shows resume UI", (_label, storageKey) => {
    beforeEach(() => localStorage.setItem(storageKey, "true"));

    it("has no Start a new order button, even with items on the ticket", async () => {
        render(<RootApp />);
        await tapMic();
        addBigMac();
        expect(screen.getByText("Big Mac (no pickles)")).toBeInTheDocument();
        expect(screen.queryByText("app.newOrder")).not.toBeInTheDocument();
    });

    it("shows no reconnecting / resumed / superseded notices and keeps the mic running", async () => {
        render(<RootApp />);
        await tapMic();
        addBigMac();
        rec.stop.mockClear();
        await act(async () => rt.params.onConnectionLost(transportDrop));
        await act(async () => rt.params.onConnectionLost({ code: 4002, reason: "superseded", idle: false, kind: "superseded", resuming: false }));
        await act(async () => rt.params.onReconnectGaveUp?.());
        for (const key of ["status.reconnecting", "status.resumed", "status.superseded", "status.connectionLost"]) {
            expect(screen.queryByText(key)).not.toBeInTheDocument();
        }
        expect(rec.stop).not.toHaveBeenCalled();
    });
});

describe("Azure Speech mode", () => {
    it("turns resume off in the hook", () => {
        localStorage.setItem("useAzureSpeechOn", "true");
        render(<RootApp />);
        expect(rt.params.resumeEnabled).toBe(false);
    });
});
