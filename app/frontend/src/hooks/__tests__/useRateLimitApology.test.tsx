import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import useRateLimitApology from "../useRateLimitApology";
import { apologyClipUrl, apologyLanguage } from "@/lib/rate-limit-apology";

type FakeClip = {
    src: string;
    play: ReturnType<typeof vi.fn>;
    pause: ReturnType<typeof vi.fn>;
    onended: (() => void) | null;
    onerror: (() => void) | null;
};

function setup({ language = "en", playRejects = false } = {}) {
    const clips: FakeClip[] = [];
    const createAudio = vi.fn((src: string) => {
        const clip: FakeClip = {
            src,
            play: vi.fn(() => (playRejects ? Promise.reject(new Error("autoplay blocked")) : Promise.resolve())),
            pause: vi.fn(),
            onended: null,
            onerror: null
        };
        clips.push(clip);
        return clip as unknown as HTMLAudioElement;
    });
    const isSessionActiveRef = { current: true };
    const isAiSpeakingRef = { current: true };
    const mute = vi.fn();
    const unmute = vi.fn();
    const lang = { current: language };
    const hook = renderHook(() =>
        useRateLimitApology({ isSessionActiveRef, isAiSpeakingRef, mute, unmute, getLanguage: () => lang.current, createAudio })
    );
    return { hook, clips, createAudio, isSessionActiveRef, isAiSpeakingRef, mute, unmute, lang };
}

const busy = { type: "extension.rate_limited" as const, attempt: 1 };
const final = { type: "extension.rate_limited" as const, attempt: 2, final: true };

describe("apology clip language", () => {
    it("maps UI languages to a shipped clip, falling back to English", () => {
        expect(apologyLanguage("fr-CA")).toBe("fr");
        expect(apologyLanguage("ja")).toBe("ja");
        expect(apologyLanguage("es_MX")).toBe("es");
        expect(apologyLanguage("de-DE")).toBe("en");
        expect(apologyLanguage(undefined)).toBe("en");
        expect(apologyClipUrl("ES")).toBe("/audio/rate-limit-apology-es.wav");
    });
});

describe("useRateLimitApology", () => {
    it("plays the clip for the guest's language with the mic muted, then reopens the mic", () => {
        const s = setup({ language: "fr-CA" });
        act(() => s.hook.result.current.onRateLimited(busy));

        expect(s.createAudio).toHaveBeenCalledWith("/audio/rate-limit-apology-fr.wav");
        expect(s.clips[0].play).toHaveBeenCalled();
        expect(s.mute).toHaveBeenCalledTimes(1);
        expect(s.unmute).not.toHaveBeenCalled();
        expect(s.hook.result.current.notice).toBe("busy");
        expect(s.hook.result.current.isRecovering()).toBe(true);
        // The failed response never spoke: echo of the clip must not count as barge-in.
        expect(s.isAiSpeakingRef.current).toBe(false);

        act(() => s.clips[0].onended?.());
        expect(s.unmute).toHaveBeenCalledTimes(1);
        expect(s.hook.result.current.notice).toBe("busy");
    });

    it("leaves the mic to response.done when the retried reply is already speaking", () => {
        const s = setup();
        act(() => s.hook.result.current.onRateLimited(busy));
        s.isAiSpeakingRef.current = true;
        act(() => s.clips[0].onended?.());
        expect(s.unmute).not.toHaveBeenCalled();
    });

    it("reopens the mic if the clip cannot play", async () => {
        const s = setup({ playRejects: true });
        await act(async () => {
            s.hook.result.current.onRateLimited(busy);
            await Promise.resolve();
        });
        expect(s.unmute).toHaveBeenCalledTimes(1);
    });

    it("does not reopen the mic for a clip that was already dismissed", async () => {
        const s = setup({ playRejects: true });
        await act(async () => {
            s.hook.result.current.onRateLimited(busy);
            s.hook.result.current.dismiss();
            await Promise.resolve();
        });
        expect(s.unmute).not.toHaveBeenCalled();
    });

    it("gives up with the final notice, no clip, and an open mic", () => {
        const s = setup();
        act(() => s.hook.result.current.onRateLimited(final));
        expect(s.createAudio).not.toHaveBeenCalled();
        expect(s.mute).not.toHaveBeenCalled();
        expect(s.unmute).toHaveBeenCalledTimes(1);
        expect(s.isAiSpeakingRef.current).toBe(false);
        expect(s.hook.result.current.notice).toBe("final");
        expect(s.hook.result.current.isRecovering()).toBe(false);
    });

    it("stops a playing clip when the final notice arrives", () => {
        const s = setup();
        act(() => s.hook.result.current.onRateLimited(busy));
        act(() => s.hook.result.current.onRateLimited(final));
        expect(s.clips[0].pause).toHaveBeenCalled();
        expect(s.clips[0].onended).toBeNull();
    });

    it("dismiss() clears the notice and stops the clip without touching the mic", () => {
        const s = setup();
        act(() => s.hook.result.current.onRateLimited(busy));
        act(() => s.hook.result.current.dismiss());
        expect(s.hook.result.current.notice).toBeNull();
        expect(s.hook.result.current.isRecovering()).toBe(false);
        expect(s.clips[0].pause).toHaveBeenCalled();
        act(() => s.clips[0].onended?.());
        expect(s.unmute).not.toHaveBeenCalled();
    });

    it("ignores the event once the conversation has ended", () => {
        const s = setup();
        s.isSessionActiveRef.current = false;
        act(() => s.hook.result.current.onRateLimited(busy));
        expect(s.createAudio).not.toHaveBeenCalled();
        expect(s.mute).not.toHaveBeenCalled();
        expect(s.hook.result.current.notice).toBeNull();
    });

    it("does not reopen the mic after the conversation ended mid-clip", () => {
        const s = setup();
        act(() => s.hook.result.current.onRateLimited(busy));
        s.isSessionActiveRef.current = false;
        act(() => s.clips[0].onended?.());
        expect(s.unmute).not.toHaveBeenCalled();
    });
});
