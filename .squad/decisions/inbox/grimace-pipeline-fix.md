# Decision: Sequential STT→LLM Pipeline for Text-Only Local Mode

**Author:** Grimace (Backend Dev)
**Date:** 2026-07-23
**Status:** Implemented

## Context

In local mode with the INT4 Phi-4 ONNX model, `MultiModalProcessor` is unavailable — the model runs in text-only mode. The pipeline was running Whisper STT and Phi-4 inference in **parallel**, meaning the LLM generated responses without ever seeing the customer's words.

## Decision

1. **Sequential pipeline** — Whisper STT runs first and is awaited. The transcribed text is then included in the Phi-4 prompt as a `<|user|>` turn. This adds ~2-5 seconds of STT latency before inference starts, but the model actually knows what the customer said.

2. **Proper chat template** — `_build_prompt()` now produces `<|system|>...<|end|><|user|>...<|end|><|assistant|>` format instead of dumping raw text. This matches Phi-4's expected chat format.

3. **Conversation history (3 turns)** — Stored on the processor instance as `(role, text)` tuples. Essential for drive-thru flow where customers add to orders incrementally ("I'll also have a Coke").

4. **max_length 8192→2048** — Prompt is ~400 tokens, response ~200. 2048 cuts KV cache overhead with no functional loss.

## Trade-offs

- STT-first adds latency vs. parallel execution. But parallel was broken — the model couldn't respond to what it never heard.
- Conversation history is per-processor-instance, not per-session. If multiple WebSocket sessions share a processor, history could bleed. Current architecture is 1:1 so this is fine for now.
- If `MultiModalProcessor` becomes available in the future, the parallel path with native audio embeddings would be faster and should be revisited.
