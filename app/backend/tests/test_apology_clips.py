"""Rate-limit apology clips (Round 3 R1): one playable clip per UI locale.

The browser plays /audio/rate-limit-apology-<lang>.wav on extension.rate_limited
(app/frontend/src/lib/rate-limit-apology.ts). A locale without a clip would 404
and the guest would hear nothing, so every locale the UI ships must have one,
and the generator, the frontend list and the locales must agree.
"""

import array
import math
import re
import sys
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_apology_clips  # noqa: E402

FRONTEND = REPO_ROOT / "app" / "frontend"
LOCALES_DIR = FRONTEND / "src" / "locales"
CLIP_DIR = FRONTEND / "public" / "audio"
LIB = FRONTEND / "src" / "lib" / "rate-limit-apology.ts"


def ui_locales() -> set[str]:
    return {p.name for p in LOCALES_DIR.iterdir() if (p / "translation.json").is_file()}


class ApologyClipTests(unittest.TestCase):
    def test_every_ui_locale_has_a_valid_clip(self):
        locales = ui_locales()
        self.assertEqual(locales, {"en", "es", "fr", "ja"})
        for lang in sorted(locales):
            with self.subTest(lang=lang):
                path = CLIP_DIR / f"rate-limit-apology-{lang}.wav"
                self.assertTrue(path.is_file(), f"missing {path}")
                with wave.open(str(path), "rb") as clip:
                    self.assertEqual(clip.getnchannels(), 1)
                    self.assertEqual(clip.getsampwidth(), 2)
                    self.assertEqual(clip.getframerate(), 24000)
                    frames = clip.getnframes()
                    samples = array.array("h", clip.readframes(frames))
                duration = frames / 24000
                # "Sorry, give me just a second." -- long enough to be speech, short
                # enough to finish before the second retry (4s) lands.
                self.assertGreaterEqual(duration, 1.0)
                self.assertLessEqual(duration, 4.0)
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                self.assertGreater(rms, 300, "clip is (near) silent")

    def test_generator_and_frontend_cover_exactly_the_ui_locales(self):
        self.assertEqual(set(generate_apology_clips.PHRASES), ui_locales())
        declared = re.search(r"APOLOGY_LANGUAGES = \[([^\]]*)\]", LIB.read_text(encoding="utf-8"))
        self.assertIsNotNone(declared)
        self.assertEqual(set(re.findall(r'"([a-z]{2})"', declared.group(1))), ui_locales())
        for lang in ui_locales():
            self.assertEqual(generate_apology_clips.clip_path(lang), CLIP_DIR / f"rate-limit-apology-{lang}.wav")


if __name__ == "__main__":
    unittest.main()
