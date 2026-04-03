# Decision: Multimodal Audio-In & VRAM Optimization

**Date:** 2026-07-23
**Author:** Grimace (Backend Dev)
**Requested by:** Brian Swiger

## Context

Brian's RTX 4060 has 8GB VRAM. The local pipeline loaded three models:
- Phi-4 INT4: ~3.5GB
- Faster-Whisper STT (small): ~1.5GB
- Piper TTS: ~0.1GB
- Total: ~5.1GB + OS (1GB) + KV cache → pushing 8GB limit

DirectML was paging to system RAM, causing very slow inference.

## Decision

**Option A (Multimodal audio-in) was implemented.** Phi-4's built-in speech
encoder processes customer audio directly, eliminating Whisper STT entirely.

### What changed

1. **phi4_model.py** — New multimodal API: `model.create_multimodal_processor()`,
   PCM→WAV conversion via `_pcm_to_wav_bytes()`, `og.Audios.open_bytes()` for
   audio loading, `generator.set_inputs()` + `proc.create_stream()` for inference.

2. **local_processor.py** — Whisper loading skipped when `model.multimodal_available`
   is True. Half-duplex mode: `self._generating` flag mutes VAD during inference.

3. **config.yaml** — `stt_model: "tiny"` (fallback only), `max_length: 1024`.

### VRAM savings

| Before | After | Savings |
|--------|-------|---------|
| Phi-4 ~3.5GB | Phi-4 ~3.5GB | — |
| Whisper ~1.5GB | Skipped | **~1.5GB** |
| Piper ~0.1GB | Piper ~0.1GB | — |
| KV cache (2048 tokens) | KV cache (1024 tokens) | ~200MB |
| **Total: ~5.1GB** | **Total: ~3.6GB** | **~1.7GB** |

New headroom: ~3.4GB free on 8GB GPU (vs ~1.9GB before).

### API discoveries (onnxruntime-genai)

- `og.MultiModalProcessor(model)` constructor removed; use `model.create_multimodal_processor()`
- `og.Audios.open_bytes()` requires a single WAV-formatted bytes object (not a list, not raw PCM)
- Token decoding: `proc.create_stream()` → `stream.decode(token_id)` (not tokenizer)
- `model.create_tokenizer()` doesn't exist; get tokenizer from processor

### Trade-offs

- Phi-4's speech recognition is slightly less accurate than Whisper-small for noisy
  drive-thru audio, but acceptable for order-taking
- Customer transcription in the Guest Conversation panel now shows the AI's
  interpretation rather than Whisper's verbatim transcript
- Half-duplex means the customer can't interrupt mid-response (barge-in still
  works via cancel event, but audio isn't buffered during generation)

## Status

✅ Implemented and tested. Integration test passed on DirectML with RTX 4060.
