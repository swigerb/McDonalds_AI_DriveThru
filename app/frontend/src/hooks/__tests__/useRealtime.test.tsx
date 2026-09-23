import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import useRealTime, { RESUME_STORAGE_KEY, WS_CLOSE_IDLE_TIMEOUT, WS_CLOSE_SUPERSEDED } from "../useRealtime";

const ws = vi.hoisted(() => ({
    calls: [] as Array<{ url: string | null; options: any; connect: boolean }>,
    readyState: 1,
    send: vi.fn()
}));

vi.mock("react-use-websocket", () => ({
    default: (url: string | null, options: any, connect: boolean) => {
        ws.calls.push({ url, options, connect });
        return { sendJsonMessage: ws.send, readyState: ws.readyState };
    },
    ReadyState: { UNINSTANTIATED: -1, CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 }
}));

const last = () => ws.calls[ws.calls.length - 1];
const closeEvent = (code: number, reason = "") => ({ code, reason, wasClean: true }) as CloseEvent;
const sent = () => ws.send.mock.calls.map(([msg]) => msg);
const sentTypes = () => sent().map(msg => msg.type);
const serverSays = (message: object) => last().options.onMessage({ data: JSON.stringify(message) } as MessageEvent);
const storedId = () => sessionStorage.getItem(RESUME_STORAGE_KEY);
const url = (n: number) => `/realtime?token=tok${n}&mode=cloud`;
const metadata = (resumeId: string) => ({ type: "extension.session_metadata", sessionToken: "S", roundTripIndex: 0, roundTripToken: "T", resumeId });

let tokenCounter = 0;

beforeEach(() => {
    ws.calls = [];
    ws.readyState = 1;
    ws.send.mockReset();
    sessionStorage.clear();
    tokenCounter = 0;
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubGlobal(
        "fetch",
        vi.fn(async () => ({ ok: true, json: async () => ({ token: `tok${++tokenCounter}` }) }))
    );
});

async function renderConnected(extra: Record<string, any> = {}) {
    const onConnectionLost = extra.onConnectionLost ?? vi.fn();
    const hook = renderHook(() => useRealTime({ enableInputAudioTranscription: true, ...extra, onConnectionLost }));
    await waitFor(() => expect(last().url).toBe(url(1)));
    return { ...hook, onConnectionLost };
}

const open = () => act(() => last().options.onOpen(new Event("open")));

describe("useRealTime connection lifecycle (cloud)", () => {
    it("does not open a socket until the session token fetch settles", async () => {
        await renderConnected();
        expect(ws.calls[0].url).toBeNull();
        expect(ws.calls.filter(c => c.url !== null).every(c => c.url === url(1))).toBe(true);
    });

    it("stays disconnected after the server's idle close instead of auto-reconnecting", async () => {
        const { result, onConnectionLost } = await renderConnected();
        expect(last().options.shouldReconnect(closeEvent(WS_CLOSE_IDLE_TIMEOUT, "idle_timeout"))).toBe(false);

        act(() => last().options.onClose(closeEvent(WS_CLOSE_IDLE_TIMEOUT, "idle_timeout")));

        expect(last().connect).toBe(false);
        expect(result.current.needsReconnect).toBe(true);
        expect(onConnectionLost).toHaveBeenCalledWith({ code: 4000, reason: "idle_timeout", idle: true, kind: "idle", resuming: false });
    });

    it("reconnects with a fresh token only when the guest asks to", async () => {
        const { result } = await renderConnected();
        act(() => last().options.onClose(closeEvent(WS_CLOSE_IDLE_TIMEOUT, "idle_timeout")));
        ws.readyState = 3;

        await act(async () => {
            await result.current.reconnect();
        });

        expect(last().connect).toBe(true);
        expect(last().url).toBe(url(2));
        expect(result.current.needsReconnect).toBe(false);
    });

    it("treats other closes as connection loss and keeps background reconnect", async () => {
        const { onConnectionLost } = await renderConnected();
        expect(last().options.shouldReconnect(closeEvent(1006))).toBe(true);
        expect(last().options.shouldReconnect(closeEvent(1002))).toBe(true);

        act(() => last().options.onClose(closeEvent(1006)));

        expect(last().connect).toBe(true);
        expect(onConnectionLost).toHaveBeenCalledWith({ code: 1006, reason: "", idle: false, kind: "transport", resuming: false });
    });

    it("stops connecting once retries are exhausted so a tap can restart it", async () => {
        const onReconnectGaveUp = vi.fn();
        const { result } = await renderConnected({ onReconnectGaveUp });
        act(() => last().options.onReconnectStop(10));
        expect(last().connect).toBe(false);
        expect(result.current.needsReconnect).toBe(true);
        expect(onReconnectGaveUp).toHaveBeenCalledTimes(1);
    });

    it("never queues realtime audio or cancels for a future socket", async () => {
        const { result } = await renderConnected();
        result.current.addUserAudio("AAAA");
        result.current.inputAudioBufferClear();
        result.current.cancelResponse();
        result.current.startSession();
        result.current.sendVoiceChoice("marin");
        expect(ws.send).not.toHaveBeenCalled();

        open();
        // session.update and the voice pick are the messages that must survive into the next socket.
        expect(sentTypes()).toEqual(["session.update", "extension.set_voice"]);
    });

    it("keeps the 4001 / expired token-refresh path", async () => {
        await renderConnected();
        expect(last().options.shouldReconnect(closeEvent(4001, "token expired"))).toBe(true);
        act(() => last().options.onClose(closeEvent(4001, "token expired")));
        await waitFor(() => expect(last().url).toBe(url(2)));
        expect(last().connect).toBe(true);
    });

    it("does not queue the echo-flush clear sent on response.created", async () => {
        await renderConnected();
        act(() => serverSays({ type: "response.created" }));
        expect(ws.send).toHaveBeenCalledWith({ type: "input_audio_buffer.clear" }, false);
    });

    it("routes extension.rate_limited to its callback", async () => {
        const onReceivedExtensionRateLimited = vi.fn();
        const onReceivedError = vi.fn();
        await renderConnected({ onReceivedExtensionRateLimited, onReceivedError });
        const event = { type: "extension.rate_limited", attempt: 2, final: true };
        act(() => serverSays(event));
        expect(onReceivedExtensionRateLimited).toHaveBeenCalledWith(event);
        expect(onReceivedError).not.toHaveBeenCalled();
    });
});

describe("useRealTime order resume (cloud realtime)", () => {
    it("sends extension.resume as the literal first frame, ahead of anything queued", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { result } = await renderConnected();
        result.current.sendVoiceChoice("cedar");
        result.current.startSession();
        expect(ws.send).not.toHaveBeenCalled();

        open();

        expect(sent()[0]).toEqual({ type: "extension.resume", resume_id: "RID-1" });
        expect(sentTypes()).toEqual(["extension.resume", "extension.set_voice", "session.update"]);
    });

    it("never hands a frame to react-use-websocket's own queue", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { result } = await renderConnected();
        result.current.startSession();
        result.current.sendVerboseLogging(true);
        open();
        result.current.sendLogToFile(true);
        result.current.addUserAudio("AAAA");
        act(() => serverSays({ type: "response.created" }));

        expect(ws.send.mock.calls.length).toBe(6);
        expect(ws.send.mock.calls.every(([, keep]) => keep === false)).toBe(true);
    });

    it("sends no resume frame when the tab holds no id", async () => {
        const { result } = await renderConnected();
        result.current.startSession();
        open();
        expect(sentTypes()).toEqual(["session.update"]);
    });

    it("stores resumeId from session_metadata and presents it on the next open", async () => {
        const onReceivedSessionMetadata = vi.fn();
        await renderConnected({ onReceivedSessionMetadata });
        open();
        act(() => serverSays(metadata("RID-A")));
        expect(storedId()).toBe("RID-A");
        expect(onReceivedSessionMetadata).toHaveBeenCalledTimes(1);

        act(() => last().options.onClose(closeEvent(1011)));
        ws.send.mockReset();
        open();
        expect(sent()[0]).toEqual({ type: "extension.resume", resume_id: "RID-A" });
    });

    it("session_resumed stores the rotated id and hands the order to the app", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-OLD");
        const onReceivedSessionResumed = vi.fn();
        await renderConnected({ onReceivedSessionResumed });
        open();
        const resumed = {
            type: "extension.session_resumed",
            order_summary: { items: [{ item: "Big Mac", size: "", quantity: 1, price: 5.99, display: "Big Mac" }], total: 5.99, tax: 0.48, finalTotal: 6.47 },
            session_token: "S",
            round_trip_index: 2,
            round_trip_token: "T",
            resume_id: "RID-NEW"
        };
        act(() => serverSays(resumed));
        expect(storedId()).toBe("RID-NEW");
        expect(onReceivedSessionResumed).toHaveBeenCalledWith(resumed);
    });

    it("resume_rejected clears the stored id; the following metadata stores the new one", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-OLD");
        const onReceivedResumeRejected = vi.fn();
        await renderConnected({ onReceivedResumeRejected });
        open();
        act(() => serverSays({ type: "extension.resume_rejected", reason: "expired" }));
        expect(storedId()).toBeNull();
        expect(onReceivedResumeRejected).toHaveBeenCalledWith({ type: "extension.resume_rejected", reason: "expired" });

        act(() => serverSays(metadata("RID-FRESH")));
        expect(storedId()).toBe("RID-FRESH");
    });

    it.each([1001, 1002, 1006, 1011])("transport close %i reconnects and keeps the id to resume", async code => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { onConnectionLost } = await renderConnected();
        open();
        expect(last().options.shouldReconnect(closeEvent(code))).toBe(true);
        act(() => last().options.onClose(closeEvent(code)));
        expect(last().connect).toBe(true);
        expect(storedId()).toBe("RID-1");
        expect(onConnectionLost).toHaveBeenCalledWith(expect.objectContaining({ kind: "transport", resuming: true }));
    });

    it.each([
        ["idle 4000", WS_CLOSE_IDLE_TIMEOUT, "idle_timeout", "idle", null],
        ["superseded 4002", WS_CLOSE_SUPERSEDED, "superseded", "superseded", "RID-1"]
    ])("%s: no reconnect; id afterwards = %s", async (_label, code, reason, kind, idAfter) => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { onConnectionLost } = await renderConnected();
        open();
        expect(last().options.shouldReconnect(closeEvent(code as number, reason as string))).toBe(false);
        act(() => last().options.onClose(closeEvent(code as number, reason as string)));
        expect(last().connect).toBe(false);
        expect(storedId()).toBe(idAfter);
        expect(onConnectionLost).toHaveBeenCalledWith(expect.objectContaining({ kind, resuming: false }));
    });

    it("a plain 1000 close (not session_ended) still reconnects", async () => {
        await renderConnected();
        expect(last().options.shouldReconnect(closeEvent(1000, ""))).toBe(true);
    });

    it("frames queued for a session that then ends (idle) never reach the next one", async () => {
        const { result } = await renderConnected();
        open();
        act(() => last().options.onClose(closeEvent(1006)));
        result.current.sendVoiceChoice("cedar"); // queued while reconnecting
        act(() => last().options.onClose(closeEvent(WS_CLOSE_IDLE_TIMEOUT, "idle_timeout")));
        ws.readyState = 3;
        await act(async () => {
            await result.current.reconnect();
        });
        ws.send.mockReset();
        open();
        expect(ws.send).not.toHaveBeenCalled();
    });

    it("session_ended 1000: no background reconnect, id cleared, a fresh socket opens with a new token", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { onConnectionLost } = await renderConnected();
        open();
        expect(last().options.shouldReconnect(closeEvent(1000, "session_ended"))).toBe(false);
        act(() => last().options.onClose(closeEvent(1000, "session_ended")));
        expect(storedId()).toBeNull();
        expect(onConnectionLost).toHaveBeenCalledWith(expect.objectContaining({ kind: "ended", resuming: false }));
        await waitFor(() => expect(last().url).toBe(url(2)));
        expect(last().connect).toBe(true);
    });

    it("a tap right after endSession is held for the fresh session, never sent to the ending one", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { result } = await renderConnected();
        open();
        ws.send.mockReset();
        result.current.endSession();
        result.current.startSession();
        result.current.addUserAudio("AAAA");
        expect(sentTypes()).toEqual(["extension.end_session"]);

        act(() => last().options.onClose(closeEvent(1000, "session_ended")));
        await waitFor(() => expect(last().url).toBe(url(2)));
        ws.send.mockReset();
        open();
        expect(sentTypes()).toEqual(["session.update"]);
    });

    it("endSession on a socket that is not open sends nothing but still forgets the id", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { result } = await renderConnected();
        result.current.endSession();
        expect(ws.send).not.toHaveBeenCalled();
        expect(storedId()).toBeNull();
    });

    it("endSession sends extension.end_session and forgets the id", async () => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-1");
        const { result } = await renderConnected();
        open();
        ws.send.mockReset();
        result.current.endSession();
        expect(sent()).toEqual([{ type: "extension.end_session" }]);
        expect(storedId()).toBeNull();

        act(() => last().options.onClose(closeEvent(1000, "session_ended")));
        ws.send.mockReset();
        open();
        expect(sentTypes()).not.toContain("extension.resume");
    });
});

// Resume is cloud-realtime only. With it off the hook must behave exactly as before:
// frames go straight to react-use-websocket with its keep semantics, nothing is stored,
// nothing is presented, and a stored id left over from cloud mode is dropped.
describe("useRealTime with resume off", () => {
    it("local mode: connects immediately without a token and keeps the socket enabled after exhausted retries", () => {
        const onReconnectGaveUp = vi.fn();
        renderHook(() => useRealTime({ localMode: true, onReconnectGaveUp }));
        expect(ws.calls[0].url).toMatch(/\/realtime\?mode=local$/);
        expect(fetch).not.toHaveBeenCalled();

        act(() => last().options.onReconnectStop(10));
        expect(last().connect).toBe(true);
        expect(onReconnectGaveUp).not.toHaveBeenCalled();
    });

    it.each([
        ["local mode", { localMode: true }],
        ["Azure Speech (resumeEnabled=false)", { resumeEnabled: false }]
    ])("%s: never presents, stores or keeps a resume id", async (_label, params) => {
        sessionStorage.setItem(RESUME_STORAGE_KEY, "RID-LEFTOVER");
        renderHook(() => useRealTime({ enableInputAudioTranscription: true, ...params }));
        await waitFor(() => expect(last().url).not.toBeNull());
        expect(storedId()).toBeNull();

        open();
        act(() => serverSays(metadata("RID-A")));
        expect(storedId()).toBeNull();
        expect(sentTypes()).not.toContain("extension.resume");
    });

    it.each([
        ["local mode", { localMode: true }],
        ["Azure Speech (resumeEnabled=false)", { resumeEnabled: false }]
    ])("%s: frames use react-use-websocket's own keep semantics, as before", async (_label, params) => {
        const { result } = renderHook(() => useRealTime({ enableInputAudioTranscription: true, ...params }));
        await waitFor(() => expect(last().url).not.toBeNull());
        result.current.addUserAudio("AAAA");
        result.current.startSession();
        result.current.sendVoiceChoice("marin");
        result.current.sendLocalModeToggle(true);
        result.current.sendPiperVoiceChoice("en_US-amy-medium");

        const byType = (t: string) => ws.send.mock.calls.find(([msg]) => msg.type === t)!;
        expect(byType("input_audio_buffer.append")[1]).toBe(false);
        expect(byType("session.update")[1]).not.toBe(false);
        expect(byType("extension.set_voice")[1]).not.toBe(false);
        expect(byType("extension.set_local_mode")[1]).not.toBe(false);
        expect(byType("extension.set_piper_voice")[1]).not.toBe(false);
    });

    it.each([
        ["local mode", { localMode: true }],
        ["Azure Speech (resumeEnabled=false)", { resumeEnabled: false }]
    ])("%s: close handling is the pre-resume one (only 4000 is final; never resuming)", async (_label, params) => {
        const onConnectionLost = vi.fn();
        renderHook(() => useRealTime({ ...params, onConnectionLost }));
        await waitFor(() => expect(last().url).not.toBeNull());
        expect(last().options.shouldReconnect(closeEvent(WS_CLOSE_SUPERSEDED, "superseded"))).toBe(true);
        expect(last().options.shouldReconnect(closeEvent(1000, "session_ended"))).toBe(true);
        expect(last().options.shouldReconnect(closeEvent(WS_CLOSE_IDLE_TIMEOUT))).toBe(false);

        act(() => last().options.onClose(closeEvent(1006)));
        expect(last().connect).toBe(true);
        expect(onConnectionLost).toHaveBeenCalledWith(expect.objectContaining({ kind: "transport", resuming: false }));

        act(() => last().options.onClose(closeEvent(WS_CLOSE_IDLE_TIMEOUT, "idle_timeout")));
        expect(last().connect).toBe(false);
    });

    it("Azure Speech (resumeEnabled=false): endSession is a no-op", async () => {
        const { result } = renderHook(() => useRealTime({ resumeEnabled: false }));
        await waitFor(() => expect(last().url).not.toBeNull());
        open();
        ws.send.mockReset();
        result.current.endSession();
        expect(ws.send).not.toHaveBeenCalled();
    });

    it("switching a cloud tab into local mode drops its resume id", async () => {
        const { rerender } = renderHook(({ localMode }) => useRealTime({ localMode }), { initialProps: { localMode: false } });
        await waitFor(() => expect(last().url).toBe(url(1)));
        open();
        act(() => serverSays(metadata("RID-A")));
        expect(storedId()).toBe("RID-A");

        rerender({ localMode: true });
        expect(storedId()).toBeNull();
    });
});
