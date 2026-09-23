"""Generate the rate-limit apology clips the browser plays (Round 3 R1).

When a realtime response is rate-limited twice the middle tier sends
`extension.rate_limited` and the browser plays a short pre-recorded apology
("Sorry, give me just a second.") while the model retries. The clip must be
local audio: the model is the thing that is rate-limited.

This script asks the live realtime model (voice marin by default) to say each
locale's phrase exactly, in a warm crew-member tone, and writes 24 kHz mono
PCM16 WAVs to app/frontend/public/audio/rate-limit-apology-<lang>.wav. Each clip
is then transcribed (whisper-1) as a sanity check. Re-run it if the default
voice changes:

    python scripts/generate_apology_clips.py                     # azd env endpoint / deployment
    python scripts/generate_apology_clips.py --deployment gpt-realtime-2.1-dz --voice marin
    python scripts/generate_apology_clips.py --lang ja          # one locale only

Auth and endpoint resolution are the smoke check's (scripts/smoke_realtime.py).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiohttp  # noqa: E402
import smoke_realtime as smoke  # noqa: E402

OUT_DIR = smoke.REPO_ROOT / "app" / "frontend" / "public" / "audio"
SAMPLE_RATE = 24000

# One per UI locale (app/frontend/src/i18n/config.ts). Keep in sync with
# app/frontend/src/lib/rate-limit-apology.ts.
PHRASES = {
    "en": ("English", "Sorry, give me just a second."),
    "es": ("Spanish", "Perdón, dame un segundito."),
    "fr": ("French", "Pardon, une petite seconde."),
    "ja": ("Japanese", "すみません、少々お待ちください。"),
}


def clip_path(lang: str) -> Path:
    return OUT_DIR / f"rate-limit-apology-{lang}.wav"


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)


async def synthesize(url: str, headers: dict, language: str, phrase: str, voice: str, timeout: float) -> bytes:
    pcm = bytearray()
    async with aiohttp.ClientSession() as http, http.ws_connect(url, headers=headers) as ws:
        await ws.send_json({"type": "session.update", "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": ("You are the voice of a friendly McDonald's drive-thru crew member. "
                             "Say only what you are told to say, warmly and naturally, like a quick "
                             "apology to a guest at the speaker."),
            "audio": {"input": {"turn_detection": None},
                      "output": {"voice": voice, "format": {"type": "audio/pcm", "rate": SAMPLE_RATE}}}}})
        # Text-only instructions, no user turn: the model recites instead of answering.
        await ws.send_json({"type": "response.create", "response": {
            "instructions": f"Say exactly this {language} sentence, word for word, and nothing else: \"{phrase}\""}})
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            event = await smoke._next_event(ws, remaining)
            if event is None:
                continue
            if event["type"] == "response.output_audio.delta":
                pcm += base64.b64decode(event["delta"])
            elif event["type"] == "response.done":
                status = (event.get("response") or {}).get("status")
                if status != "completed":
                    raise smoke.SmokeError(f"{language}: response {status}: {event['response'].get('status_details')}")
                break
            elif event["type"] == "error":
                raise smoke.SmokeError(f"{language}: {event.get('error')}")
    return bytes(pcm)


async def transcribe(url: str, headers: dict, pcm: bytes, lang: str, timeout: float) -> str:
    async with aiohttp.ClientSession() as http, http.ws_connect(url, headers=headers) as ws:
        await smoke.send_session_update(ws, json.dumps({"type": "session.update", "session": {
            "type": "realtime", "audio": {"input": {
                "turn_detection": None, "transcription": {"model": "whisper-1", "language": lang}}}}}), timeout)
        for i in range(0, len(pcm), 4800):
            await ws.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + 4800]).decode()})
        await ws.send_json({"type": "input_audio_buffer.commit"})
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            event = await smoke._next_event(ws, remaining)
            if event and event.get("type") == "conversation.item.input_audio_transcription.completed":
                return event.get("transcript") or ""
            if event and event.get("type") in ("conversation.item.input_audio_transcription.failed", "error"):
                return f"<{event.get('error')}>"
    return "<no transcript>"


async def generate(endpoint: str, deployment: str, voice: str, langs: list[str], timeout: float,
                   tenant_id: str | None, subscription_id: str | None) -> int:
    headers = smoke.get_auth_headers(tenant_id, subscription_id)
    url = smoke.realtime_url(endpoint, deployment)
    for lang in langs:
        language, phrase = PHRASES[lang]
        pcm = await synthesize(url, headers, language, phrase, voice, timeout)
        if not pcm:
            raise smoke.SmokeError(f"{lang}: no audio returned")
        write_wav(clip_path(lang), pcm)
        heard = await transcribe(url, headers, pcm, lang, timeout)
        print(f"{lang}: {len(pcm) / 2 / SAMPLE_RATE:.2f}s  {clip_path(lang).relative_to(smoke.REPO_ROOT)}")
        print(f"    asked: {phrase}")
        print(f"    heard: {heard}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint")
    parser.add_argument("--deployment")
    parser.add_argument("--voice", default="marin")
    parser.add_argument("--lang", action="append", choices=sorted(PHRASES), help="repeatable; default all")
    parser.add_argument("--tenant")
    parser.add_argument("--subscription")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    azd = smoke._azd_env_values()
    endpoint = smoke.resolve_setting("AZURE_OPENAI_EASTUS2_ENDPOINT", args.endpoint, azd)
    deployment = smoke.resolve_setting("AZURE_OPENAI_REALTIME_DEPLOYMENT", args.deployment, azd)
    if not endpoint or not deployment:
        print("need --endpoint and --deployment (or the azd env)", file=sys.stderr)
        return 2
    tenant = args.tenant or os.environ.get("AZURE_TENANT_ID") or azd.get("AZURE_TENANT_ID")
    subscription = args.subscription or os.environ.get("AZURE_SUBSCRIPTION_ID") or azd.get("AZURE_SUBSCRIPTION_ID")
    try:
        return asyncio.run(generate(endpoint, deployment, args.voice, args.lang or list(PHRASES), args.timeout,
                                    tenant, subscription))
    except smoke.SmokeError as exc:
        print(f"could not generate clips: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
