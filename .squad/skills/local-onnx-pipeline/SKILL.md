---
name: "local-onnx-pipeline"
description: "Patterns for building local ONNX inference pipelines with async streaming and TTS integration"
domain: "ai-inference"
confidence: "high"
source: "earned — Phase 2 Phi-4 ONNX + Piper TTS implementation"
---

## Context
When building local/offline AI inference pipelines using ONNX models with text-to-speech output, these patterns ensure non-blocking async operation, proper audio format handling, and graceful degradation.

## Patterns

### 1. Queue-Bridged Async Streaming
ONNX inference is synchronous. Bridge to async generators via `asyncio.Queue`:
```python
queue: asyncio.Queue[str | None] = asyncio.Queue()

def _run_inference():
    # Sync ONNX generation loop
    while not generator.is_done():
        generator.generate_next_token()
        token = tokenizer.decode(generator.get_last_tokens(1))
        loop.call_soon_threadsafe(queue.put_nowait, token)
    loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

asyncio.get_event_loop().run_in_executor(None, _run_inference)

while True:
    token = await queue.get()
    if token is None: break
    yield token
```

### 2. GPU Auto-Detection Priority
Try GPU-specific packages in order:
1. `onnxruntime_genai_cuda` (NVIDIA)
2. `onnxruntime_genai_directml` (Windows GPU)
3. `onnxruntime_genai` (CPU fallback)

### 3. Audio Sample Rate Contract
- Frontend: 24kHz PCM int16 mono (base64 in WebSocket)
- Phi-4 model: 16kHz PCM int16 mono
- Piper TTS: varies by voice (usually 22050Hz)
- Always resample at boundaries using numpy `np.interp`

### 4. Sentence-Chunked TTS Streaming
Split text at sentence boundaries before synthesis for lower first-audio latency.

### 5. Processing Lock per Connection
Use `asyncio.Lock` to prevent overlapping inference on the same model instance.

## Anti-Patterns
- **Don't** run ONNX inference directly in the event loop — blocks all other connections
- **Don't** accumulate all tokens before sending — defeats streaming latency benefit
- **Don't** assume Piper sample rate matches frontend — always resample
- **Don't** load models at import time — use lazy loading for shared environments
