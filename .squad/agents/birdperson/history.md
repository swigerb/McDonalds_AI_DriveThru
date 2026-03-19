# Project Context

- **Owner:** Brian Swiger
- **Project:** Dunkin Voice Chat Assistant — AI-powered voice ordering experience using Azure OpenAI GPT-4o Realtime, Azure AI Search, and Azure Container Apps
- **Stack:** Python backend (aiohttp, WebSockets, Azure OpenAI Realtime, Azure AI Search, Azure Speech SDK), React/TypeScript frontend (Vite, Tailwind CSS, shadcn/ui), Bicep IaC, Docker, azd CLI
- **Created:** 2026-03-19

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->
- **Rebrand verification tests added** (`test_rebrand_verification.py`): 12 tests scan every source file for forbidden terms ("dunkin", "crew member", "coffee-chat"). Excludes `.squad/`, `.git/`, `node_modules/`, `__pycache__/`, `voice_rag_README.md` (attribution), and itself. Targeted checks verify README title, index.html `<title>`, and backend system prompt. Pre-rebrand run: 5 pass, 7 fail — exactly right. Tests report file + line number for every violation.
- Existing test files (`test_app.py`, `test_models.py`, `test_order_state.py`, `test_extras_rules.py`, `test_tools_search.py`) contain zero Dunkin/crew-member/coffee-chat references — no updates needed there.
- The backend system prompt in `app.py` was already rebranded to Sonic before these tests ran, so those 3 targeted prompt tests pass immediately.
- **Team Orchestration (2026-03-19T04-06)**: Rick provided scope analysis, Morty completed frontend rebrand (13 tests pass), Summer completed backend rebrand (69 tests pass), Birdperson created verification tests (12 tests pass).
- **Performance test harness added** (`test_performance.py`): 28 tests covering latency benchmarks (order_state <5ms, search formatting <10ms, JSON serialization <2ms), memory efficiency (add/remove cycle <1MB delta, 100-item order <2MB peak), thread safety (10 concurrent writers, 8 concurrent session lifecycles), session isolation, production readiness (app startup, static files, health endpoint, CORS wildcard check), and Pydantic model serialization speed. All tests use mocks — zero real Azure calls. Full suite now at 100 tests.
- OrderState is a singleton with no locking; thread safety tests pass because GIL serializes Python bytecode, but under true parallelism (e.g., multi-process) this would need a lock. Worth noting for future scaling.
- `aiohttp.test_utils.TestClient/TestServer` is the canonical way to test aiohttp endpoints without starting a real server — used for health and CORS checks.
- **Performance Audit Orchestration (2026-03-19T13-21)**: Team completed full-stack performance sprint with 5 agents. Rick lead: 8 fixes across JSON parsing, token cap, search params, system prompt, JSON caching, VAD timing, and response filtering. Summer: 10 fixes for race conditions, hot-path fast-returns, search caching, compression, gzip, logging, memory. Morty: 9 fixes for AudioContext reuse, zero-alloc buffers, memoization, lazy loading, vendor chunking. Squanchy: 6 infrastructure fixes for Gunicorn async, health probes, auto-scaling, Docker caching. Birdperson: 28 performance tests validating latency, memory, thread safety, production readiness. All decisions documented in decisions.md. Orchestration logs written per-agent.
