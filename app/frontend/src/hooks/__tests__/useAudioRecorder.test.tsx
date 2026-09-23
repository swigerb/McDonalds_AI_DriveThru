import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import useAudioRecorder from "../useAudioRecorder";

const recorder = vi.hoisted(() => ({ start: vi.fn(async (_stream: MediaStream) => true), stop: vi.fn(async () => {}) }));

vi.mock("@/components/audio/recorder", () => ({
    Recorder: vi.fn(function (this: any) {
        this.start = recorder.start;
        this.stop = recorder.stop;
    })
}));

beforeEach(() => {
    recorder.start.mockReset();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [] })) } });
});

// The resume path relies on this to fall back to "tap to continue" (docs/order_resume.md).
describe("useAudioRecorder.start", () => {
    it.each([true, false])("reports whether capture started (%s)", async started => {
        recorder.start.mockResolvedValue(started);
        const { result } = renderHook(() => useAudioRecorder({ onAudioRecorded: () => {} }));
        await expect(result.current.start()).resolves.toBe(started);
    });

    it("lets a getUserMedia refusal propagate", async () => {
        vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => Promise.reject(new DOMException("denied", "NotAllowedError"))) } });
        const { result } = renderHook(() => useAudioRecorder({ onAudioRecorded: () => {} }));
        await expect(result.current.start()).rejects.toThrow("denied");
    });
});
