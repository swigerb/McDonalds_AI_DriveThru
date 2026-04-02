# Mayor McCheese — History

## Sessions

### 2026-03-22: Squad Framework Upgrade v0.8.25 → v0.9.1

**Task:** Upgrade Squad agent framework and push to GitHub

**Actions:**
- Ran `npx @bradygaster/squad-cli upgrade` to upgrade from v0.8.25 to v0.9.1
- Verified upgrade with `npx @bradygaster/squad-cli doctor` (9 passed, 3 warnings)
- Staged all changes including coordinator's casting registry fixes (McDonald's universe)
- Committed 79 files: 7,677 insertions, 299 deletions
- Pushed to GitHub main branch

**Key Changes:**
- Updated `.github/agents/squad.agent.md` version comment to 0.9.1
- Migrated 5 project skills to `.copilot/skills/` (echo-suppression, gpt-realtime-expert, mcdonalds-menu-parsing, project-conventions, squad-conventions)
- Added 28 Squad skill definitions to `.copilot/skills/`
- Created 7 new GitHub workflow files (squad-ci, squad-docs, squad-insider-release, squad-label-enforce, squad-preview, squad-promote, squad-release)
- Updated Squad templates (casting-policy, mcp-config, routing, scribe-charter, squad.agent)
- Created new Squad documentation files (charter, constraint-tracking, copilot-instructions, history, issue-lifecycle, mcp-config, multi-agent-format, orchestration-log, plugin-marketplace, raw-agent-output, roster, run-output, scribe-charter, skill)

**Commit:** 3d78595

## Learnings

### Squad Upgrade Process
- **Primary command:** `npx @bradygaster/squad-cli upgrade` works reliably
- **Fallback options:** If npx fails, try `npx @bradygaster/squad-cli@latest upgrade` or global install with `npm install -g @bradygaster/squad-cli@latest`
- **Validation:** Always run `npx @bradygaster/squad-cli doctor` post-upgrade to verify installation integrity
- **Preserved state:** Upgrade respects user state (team.md, decisions/, agents/*/history.md) — no manual backups needed
- **Skill migration:** v0.9.x automatically migrates project skills from `.squad/skills/` to `.copilot/skills/` as part of Copilot standardization
- **Version tracking:** Version stored in HTML comment at top of `.github/agents/squad.agent.md` (e.g., `<!-- version: 0.9.1 -->`)
- **Git lock handling:** On Windows, if commit fails with "index.lock exists", remove the lock file with `Remove-Item -Path ".git\index.lock" -Force` and retry

### Phase 4: Local Model Asset Management (2025-07)
- **Model download script:** `scripts/download_local_models.py` — uses `huggingface_hub.snapshot_download()` for Phi-4 ONNX, `urllib` for Piper TTS. Includes `--cpu-only` and `--model-dir` flags. PowerShell wrapper at `scripts/download_local_models.ps1`.
- **Config approach:** `local_mode` section added to `app/backend/config.yaml` with `enabled: false` default — cloud mode unchanged, no existing behavior disrupted.
- **Dependency isolation:** Local AI deps (`onnxruntime-genai`, `piper-tts`, `soundfile`, `huggingface-hub`) added to `requirements.txt`. GPU variants (`onnxruntime-genai-cuda`, `onnxruntime-genai-directml`) are opt-in via comment instructions.
- **Docker strategy:** `docker-compose.local.yml` is dev-only with NVIDIA GPU pass-through. Production Dockerfile untouched — no local mode deps in production image.
- **Key paths:** `models/phi4-multimodal/` (Phi-4 ONNX), `models/piper/` (Piper TTS). Both gitignored.
- **Constraint:** azure.yaml, infra/main.bicep, and app/Dockerfile were NOT modified — cloud deployment is unchanged.
