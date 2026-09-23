import { MutableRefObject, useCallback, useEffect, useRef, useState } from "react";

import { apologyClipUrl } from "@/lib/rate-limit-apology";
import { ExtensionRateLimited } from "@/types";

export type RateLimitNotice = "busy" | "final" | null;

type Options = {
    isSessionActiveRef: MutableRefObject<boolean>;
    isAiSpeakingRef: MutableRefObject<boolean>;
    mute: () => void;
    unmute: () => void;
    getLanguage: () => string | undefined;
    createAudio?: (src: string) => HTMLAudioElement;
};

const defaultCreateAudio = (src: string) => new Audio(src);

// Handles extension.rate_limited from the middle tier (see app/backend/rate_limit.py):
// {attempt} -> play the local apology clip with the mic muted while the server
// retries; {final: true} -> stop, show "please say that again" and reopen the mic.
export default function useRateLimitApology(options: Options) {
    const [notice, setNotice] = useState<RateLimitNotice>(null);
    const optionsRef = useRef(options);
    optionsRef.current = options;
    const clipRef = useRef<HTMLAudioElement | null>(null);
    const recoveringRef = useRef(false);

    const stopClip = useCallback(() => {
        const clip = clipRef.current;
        clipRef.current = null;
        if (clip) {
            clip.onended = null;
            clip.onerror = null;
            clip.pause();
        }
    }, []);

    const onRateLimited = useCallback(
        (message: ExtensionRateLimited) => {
            const opts = optionsRef.current;
            if (!opts.isSessionActiveRef.current) return;
            stopClip();
            // The failed response never produced audio. Clear the speaking flag so the
            // clip's own echo can't read as barge-in and cancel the server's retry.
            opts.isAiSpeakingRef.current = false;

            if (message.final) {
                recoveringRef.current = false;
                setNotice("final");
                opts.unmute();
                return;
            }

            recoveringRef.current = true;
            setNotice("busy");
            // Muted, not just quiet: server VAD hearing the clip would count as the
            // guest speaking and cancel the retry.
            opts.mute();
            const clip = (opts.createAudio ?? defaultCreateAudio)(apologyClipUrl(opts.getLanguage()));
            clipRef.current = clip;
            const finished = () => {
                if (clipRef.current !== clip) return;
                clipRef.current = null;
                const current = optionsRef.current;
                // If the retried response already started, its response.done unmutes.
                if (current.isSessionActiveRef.current && !current.isAiSpeakingRef.current) current.unmute();
            };
            clip.onended = finished;
            clip.onerror = finished;
            const played = clip.play();
            if (played && typeof played.catch === "function") played.catch(finished);
        },
        [stopClip]
    );

    // The crew member is talking again (or the guest is): drop the notice and any clip.
    const dismiss = useCallback(() => {
        recoveringRef.current = false;
        stopClip();
        setNotice(null);
    }, [stopClip]);

    const isRecovering = useCallback(() => recoveringRef.current, []);

    useEffect(() => stopClip, [stopClip]);

    return { notice, onRateLimited, dismiss, isRecovering };
}
