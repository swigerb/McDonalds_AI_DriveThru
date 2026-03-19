# Project Context

- **Owner:** Brian Swiger
- **Project:** Dunkin Voice Chat Assistant — AI-powered voice ordering experience using Azure OpenAI GPT-4o Realtime, Azure AI Search, and Azure Container Apps
- **Stack:** Python backend (aiohttp, WebSockets, Azure OpenAI Realtime, Azure AI Search, Azure Speech SDK), React/TypeScript frontend (Vite, Tailwind CSS, shadcn/ui), Bicep IaC (infra/), Docker, azd CLI
- **Created:** 2026-03-19

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

### 2026-03-19: Repository Creation
- GitHub repo created at `brswig_microsoft/SonicAIDriveThru` (private — EMU accounts cannot create public repos)
- Forked from `swigerb/dunkin-chat-voice-assistant` as starting codebase (208 files, 26K+ lines)
- Local `.squad/`, `.copilot/`, `.github/` configs preserved over dunkin originals; `.gitignore` and `.gitattributes` merged
- Remote: `https://github.com/brswig_microsoft/SonicAIDriveThru`
- Branch: `main`

### 2026-03-19: Production Performance Hardening
- **Dockerfile**: Reordered layers for optimal caching — npm deps cached separately from source, pip requirements cached before backend copy. Added `--no-cache-dir` and `npm cache clean`. Added HEALTHCHECK on `/health` endpoint. Configured gunicorn with 2 async workers, 120s timeout (WebSocket-friendly), 65s keep-alive (outlasts Azure LB 60s idle), graceful shutdown.
- **Container App Bicep**: Added full health probe suite (startup/liveness/readiness) via parameterized `healthProbePath`. Added HTTP-based auto-scaling rule (20 concurrent requests trigger). Enabled explicit WebSocket transport (`transport: 'http'`). Set max replicas to 5 with min 1 (always-warm).
- **Backend app.py**: Added `/health` JSON endpoint. Made log level configurable via `LOG_LEVEL` env var (defaults to INFO, not DEBUG).
- **Start scripts**: Both `start.ps1` and `start.sh` now accept `--production` / `-Production` flag to skip frontend rebuild and launch gunicorn with production settings.
- Vite outputs to `../backend/static` (resolves to `/backend/static` in Docker build stage) — confirmed path is correct in Dockerfile COPY.
- `.dockerignore` already filters `node_modules`, `__pycache__`, `.env`, and `static/` (rebuilt by multistage build).
- **Performance Audit Orchestration (2026-03-19T13-21)**: Team completed full-stack performance sprint with 5 agents. Rick lead: 8 fixes across JSON parsing, token cap, search params, system prompt, JSON caching, VAD timing, and response filtering. Summer: 10 fixes for race conditions, hot-path fast-returns, search caching, compression, gzip, logging, memory. Morty: 9 fixes for AudioContext reuse, zero-alloc buffers, memoization, lazy loading, vendor chunking. Squanchy: 6 infrastructure fixes for Gunicorn async, health probes, auto-scaling, Docker caching. Birdperson: 28 performance tests validating latency, memory, thread safety, production readiness. All decisions documented in decisions.md. Orchestration logs written per-agent.
