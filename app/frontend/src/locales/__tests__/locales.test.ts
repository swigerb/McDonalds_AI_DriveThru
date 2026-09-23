import en from "../en/translation.json";
import es from "../es/translation.json";
import fr from "../fr/translation.json";
import ja from "../ja/translation.json";

// Leftovers from the VoiceRAG / sibling-brand templates this app was forked from.
const TEMPLATE_LEFTOVERS: RegExp[] = [
    /contoso/i,
    /mercer/i,
    /voice ?rag/i,
    /talk to your data/i,
    /habla con tus datos/i,
    /parlez à vos données/i,
    /データと話す/
];

const LOCALES: Record<string, unknown> = { en, es, fr, ja };

function flatten(value: unknown, prefix = ""): Record<string, string> {
    if (typeof value === "string") return { [prefix]: value };
    const out: Record<string, string> = {};
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        Object.assign(out, flatten(child, prefix ? `${prefix}.${key}` : key));
    }
    return out;
}

const SOURCES = import.meta.glob("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
>;

describe("locale files", () => {
    it.each(Object.keys(LOCALES))("%s has no Contoso / template wording", lng => {
        const offenders = Object.entries(flatten(LOCALES[lng])).filter(([, text]) =>
            TEMPLATE_LEFTOVERS.some(pattern => pattern.test(text))
        );
        expect(offenders).toEqual([]);
    });

    it.each(["es", "fr", "ja"])("%s translates every English key", lng => {
        expect(Object.keys(flatten(LOCALES[lng])).sort()).toEqual(Object.keys(flatten(en)).sort());
    });

    it("the leftover check itself catches the known template strings", () => {
        for (const text of ["Contoso Coffee", "assistant for Contoso", "Habla con tus datos"]) {
            expect(TEMPLATE_LEFTOVERS.some(pattern => pattern.test(text))).toBe(true);
        }
        expect(TEMPLATE_LEFTOVERS.some(pattern => pattern.test("Welcome to McDonald's Drive-Thru!"))).toBe(false);
    });
});

describe("user-visible source strings", () => {
    it("no component or hook mentions Contoso", () => {
        const files = Object.entries(SOURCES).filter(([path]) => !path.includes("__tests__"));
        expect(files.length).toBeGreaterThan(10);
        expect(files.filter(([, text]) => /contoso/i.test(text)).map(([path]) => path)).toEqual([]);
    });
});
