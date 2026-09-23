import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Settings from "../settings";
import { DummyDataProvider } from "@/context/dummy-data-context";
import { AzureSpeechProvider } from "@/context/azure-speech-context";
import { MenuModeProvider } from "@/context/menu-mode-context";
import { LocalModeProvider } from "@/context/local-mode-context";
import { DEFAULT_VOICE, VOICE_OPTIONS, resolveVoice } from "@/lib/voices";
import appSource from "@/App.tsx?raw";

// Every voice gpt-realtime-2.1 accepts (the service's own list when it rejects
// anything else, probed live 2026-09-22 in the Sonic reference repo).
const GA_REALTIME_VOICES = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"];

function renderSettings(voiceChoice: string) {
    return render(
        <AzureSpeechProvider>
            <DummyDataProvider>
                <MenuModeProvider>
                    <LocalModeProvider>
                        <Settings
                            isMobile={false}
                            showSessionTokens={false}
                            onShowSessionTokensChange={() => {}}
                            verboseLogging={false}
                            onVerboseLoggingChange={() => {}}
                            logToFile={false}
                            onLogToFileChange={() => {}}
                            voiceChoice={voiceChoice}
                            onVoiceChoiceChange={() => {}}
                            piperVoice="en_US-amy-medium"
                            onPiperVoiceChange={() => {}}
                            onLocalModeChange={() => {}}
                        />
                    </LocalModeProvider>
                </MenuModeProvider>
            </DummyDataProvider>
        </AzureSpeechProvider>
    );
}

async function openVoicePicker() {
    await userEvent.click(screen.getByRole("button", { name: /open settings/i }));
    return (await screen.findByLabelText("AI Voice")) as HTMLSelectElement;
}

describe("Crew voice picker", () => {
    beforeEach(() => localStorage.clear());

    it("offers every gpt-realtime-2.1 voice and defaults to marin", async () => {
        renderSettings(resolveVoice(localStorage.getItem("voiceChoice")));
        const picker = await openVoicePicker();
        const offered = within(picker)
            .getAllByRole("option")
            .map(o => (o as HTMLOptionElement).value);

        expect([...offered].sort()).toEqual(GA_REALTIME_VOICES);
        expect(new Set(offered).size).toBe(offered.length);
        expect(picker.value).toBe("marin");
        expect(screen.getByText("Default: Marin")).toBeInTheDocument();
    });

    it("marks the OpenAI-recommended voices", async () => {
        renderSettings(DEFAULT_VOICE);
        const picker = await openVoicePicker();
        const recommended = within(picker)
            .getAllByRole("option")
            .filter(o => /recommended/i.test(o.textContent ?? ""))
            .map(o => (o as HTMLOptionElement).value);
        expect(recommended.sort()).toEqual(["cedar", "marin"]);
        expect(VOICE_OPTIONS.filter(v => v.recommended).map(v => v.value).sort()).toEqual(["cedar", "marin"]);
    });

    it("keeps a stored valid voice and replaces an unknown one with the default", () => {
        expect(DEFAULT_VOICE).toBe("marin");
        expect(resolveVoice("shimmer")).toBe("shimmer");
        expect(resolveVoice("cedar")).toBe("cedar");
        expect(resolveVoice("nova")).toBe("marin");
        expect(resolveVoice(null)).toBe("marin");
        expect(resolveVoice("")).toBe("marin");
    });

    it("App seeds the stored voice through resolveVoice (so a stale or empty choice becomes marin)", () => {
        expect(appSource).toContain('resolveVoice(localStorage.getItem("voiceChoice"))');
        expect(appSource).not.toMatch(/getItem\("voiceChoice"\)\s*\|\|/);
    });
});
