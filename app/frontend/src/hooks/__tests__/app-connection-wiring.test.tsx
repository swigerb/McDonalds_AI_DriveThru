import { describe, expect, it } from "vitest";

import appSource from "@/App.tsx?raw";

// App.tsx is too entangled (audio worklets, speech SDK, MSAL) to mount in jsdom,
// so pin the cloud-only connection-lost wiring at the source level.
describe("App connection-lost wiring", () => {
    it("leaves local mode and Azure Speech on their existing path", () => {
        expect(appSource).toMatch(/onConnectionLost:[\s\S]{0,300}if \(useAzureSpeechOn \|\| localMode\) return;/);
    });

    it("surfaces the idle notice and stops an active conversation", () => {
        expect(appSource).toMatch(/if \(wasActive\) void stopConversation\(\);/);
        expect(appSource).toContain('setConnectionNotice(idle ? "idle" : "lost")');
        expect(appSource).toContain("notice={connectionNotice}");
    });

    it("reopens a parked cloud socket on the next mic tap instead of toasting", () => {
        expect(appSource).toContain("const cloudRealtime = !localMode && !useAzureSpeechOn;");
        expect(appSource).toMatch(/if \(cloudRealtime && realtime\.needsReconnect\) \{[\s\S]{0,200}void realtime\.reconnect\(\);/);
    });

    it("starts a fresh order after the server session was lost", () => {
        expect(appSource).toMatch(/if \(cloudRealtime && serverSessionLostRef\.current\) \{[\s\S]{0,120}setOrder\(initialOrder\);/);
    });
});
