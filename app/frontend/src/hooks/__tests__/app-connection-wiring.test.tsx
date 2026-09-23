import { describe, expect, it } from "vitest";

import appSource from "@/App.tsx?raw";

// Source-level pins for the cloud-only connection-lost wiring. The behaviour
// (including order resume) is exercised on a mounted App in __tests__/App.resume.test.tsx.
describe("App connection-lost wiring", () => {
    it("leaves local mode and Azure Speech on their existing path", () => {
        expect(appSource).toMatch(/onConnectionLost:[\s\S]{0,300}if \(useAzureSpeechOn \|\| localMode\) return;/);
    });

    it("surfaces the idle notice and stops an active conversation", () => {
        expect(appSource).toMatch(/if \(wasActive\) void stopConversation\(\);/);
        expect(appSource).toContain('setConnectionNotice(idle ? "idle" : kind === "superseded" ? "superseded" : "lost")');
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
