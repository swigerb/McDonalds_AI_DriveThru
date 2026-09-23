import { describe, expect, it } from "vitest";

import appSource from "@/App.tsx?raw";

// App.tsx can't be mounted in jsdom (see app-connection-wiring.test.tsx), so pin
// the extension.rate_limited wiring at the source level. Behaviour lives in
// useRateLimitApology and is tested there.
describe("App rate-limit apology wiring", () => {
    it("hands extension.rate_limited to the apology hook with the real mic controls and UI language", () => {
        expect(appSource).toMatch(/onReceivedExtensionRateLimited: message => \{\s*rateLimitApology\.onRateLimited\(message\);/);
        expect(appSource).toMatch(/useRateLimitApology\(\{[\s\S]{0,200}mute: muteAudioRecording,\s*unmute: unmuteAudioRecording,/);
        expect(appSource).toContain("getLanguage: () => i18n.resolvedLanguage || i18n.language");
    });

    it("opens the mic when a rate-limited greeting is given up on", () => {
        expect(appSource).toMatch(/if \(message\.final\) startMicAfterGreeting\(\);/);
        expect(appSource).toMatch(/if \(rateLimitApology\.isRecovering\(\)\) return;/);
    });

    it("drops the notice when the crew member or the guest speaks, or the conversation stops", () => {
        expect(appSource).toMatch(/onReceivedResponseAudioDelta:[\s\S]{0,120}rateLimitApology\.dismiss\(\);/);
        expect(appSource).toMatch(/onReceivedInputAudioBufferSpeechStarted:[\s\S]{0,200}rateLimitApology\.dismiss\(\);/);
        expect(appSource).toMatch(/const stopConversation = async \(\) => \{[\s\S]{0,200}rateLimitApology\.dismiss\(\);/);
    });

    it("shows the notice in the status line", () => {
        expect(appSource).toContain("busyNotice={rateLimitApology.notice}");
    });
});
