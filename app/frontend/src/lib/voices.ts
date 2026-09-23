export interface VoiceOption {
    value: string;
    label: string;
    recommended?: boolean;
}

// Every built-in voice gpt-realtime-2.1 accepts (the service lists exactly these
// ten when it rejects anything else, e.g. fable/onyx/nova). OpenAI recommends
// marin and cedar for best quality. Keep in sync with GA_REALTIME_VOICES in
// app/backend/rtmt.py.
export const VOICE_OPTIONS: readonly VoiceOption[] = [
    { value: "marin", label: "Marin — Warm & Natural (recommended)", recommended: true },
    { value: "cedar", label: "Cedar — Gentle & Natural (recommended)", recommended: true },
    { value: "shimmer", label: "Shimmer — Cheerful & Bright" },
    { value: "ash", label: "Ash — Warm & Friendly" },
    { value: "ballad", label: "Ballad — Caring & Soft" },
    { value: "coral", label: "Coral — Confident & Clear" },
    { value: "sage", label: "Sage — Calm & Thoughtful" },
    { value: "verse", label: "Verse — Natural & Adaptable" },
    { value: "alloy", label: "Alloy — Neutral & Crisp" },
    { value: "echo", label: "Echo — Deep & Resonant" }
];

// Keep in sync with model.default_voice in app/backend/config.yaml.
export const DEFAULT_VOICE = "marin";

export function resolveVoice(stored: string | null | undefined): string {
    return stored && VOICE_OPTIONS.some(v => v.value === stored) ? stored : DEFAULT_VOICE;
}

export function voiceLabel(value: string): string {
    return VOICE_OPTIONS.find(v => v.value === value)?.label ?? value;
}
