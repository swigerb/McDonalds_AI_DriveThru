# Project Context

- **Owner:** Brian Swiger
- **Project:** Dunkin Voice Chat Assistant — AI-powered voice ordering experience using Azure OpenAI GPT-4o Realtime, Azure AI Search, and Azure Container Apps
- **Stack:** Python backend (aiohttp, WebSockets, Azure OpenAI Realtime, Azure AI Search, Azure Speech SDK), React/TypeScript frontend (Vite, Tailwind CSS, shadcn/ui), Bicep IaC, Docker, azd CLI
- **Created:** 2026-03-19

## Learnings

- **Sonic Rebrand Scope (2026-03-20)**: Identified ~100+ Dunkin-specific references across frontend UI, backend prompts, menu data, docs, and team context. Critical changes needed in system prompts (`app.py`/`rtmt.py`), frontend components (`App.tsx`/`order-summary.tsx`), menu data files, and logo asset. No changes required in infrastructure, tests (logic remains), or upstream attribution. Scope documented in `.squad/decisions/inbox/rick-sonic-rebrand-scope.md`. Recommended 2–4 dev-days with team parallelization.
- **Team Orchestration (2026-03-19T04-06)**: Morty completed frontend rebrand (13 tests pass), Summer completed backend rebrand (69 tests pass), Birdperson created verification tests (12 tests pass). All decisions merged to decisions.md.
- **Performance Audit (2026-03-21)**: Full end-to-end latency audit of voice pipeline (mic → WS → middleware → Azure OpenAI Realtime → AI Search → response → audio playback). Key changes: (1) Regex-based type extraction in rtmt.py to skip json.loads on ~95% of hot-path messages (audio deltas, input_audio_buffer.append), (2) Set max_response_output_tokens=150 to force concise voice responses, (3) Reduced AI Search KNN from 50→15 and top from 5→3 to cut search latency, (4) Trimmed system prompt from ~170 words to ~100 words reducing per-turn token processing, (5) Cached order summary JSON in order_state to avoid repeated Pydantic serialization, (6) Pre-serialized static WS messages (greeting, response.create), (7) Tightened frontend VAD (threshold 0.7→0.6, silence 500→400ms, prefix padding 300→200ms) and greeting timing (drain wait 2500→2000ms, safety timeout 5s→3.5s, removed 150ms post-drain delay). All 100 backend + 13 frontend tests pass. Ruff clean.
