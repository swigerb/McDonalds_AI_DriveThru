# Ronald — History

## Sessions

### 2026-08-06 — Remove Route 44 Sonic Branding

**Task:** Remove Sonic Drive-In "Route 44" sizing from McDonald's demo.
**Status:** ✅ Complete — all validation green.
**Files changed:** `order_state.py`, `tools.py`, `scripts/extract_production_items.py`, `test_order_state.py`, `test_tool_calling.py`
**Decision:** Unknown sizes (including Route 44) are silently dropped to no-size — consistent with existing behavior for other unrecognized sizes like "pot" or "kannchen". The AI's system prompt already instructs it not to accept invalid sizes, so the assistant will naturally ask for clarification.

## Learnings

### 2026-03-22 — Added "Built with Squad" section to README.md
- **What:** Added a `## 🍔 Built with Squad` section to the README after "Deploying to Azure" and before "License". Includes a shoutout to Brady Gaster (Squad creator), the full team roster in a table, and plays up the irony of McDonald's characters building a McDonald's AI app. Updated the Table of Contents with the new section link.
- **Why:** Brian wanted to publicly credit Squad and the AI team, highlight the serendipitous casting, and keep the tone fun-but-authentic for developer audiences. Good README sections like this make open-source projects memorable.

### 2026-03-23 — Comprehensive Offline Mode Documentation Added to README.md
- **What:** Updated README.md with full offline mode feature documentation across 6 key areas:
  1. Opening paragraph: Added sentence about offline mode with Phi-4-multimodal-instruct via ONNX Runtime
  2. Table of Contents: Added "Offline Mode (Local AI)" section with 4 subsections
  3. Features section: New "Offline Mode (Local AI)" subsection highlighting 6 key capabilities (Phi-4 ONNX, Piper TTS voices, one-toggle switch, CPU/GPU/NPU support, Azure Local compatibility, graceful degradation)
  4. Technical Stack: New "Offline AI (Local Mode)" block with model specs and audio pipeline details
  5. Architecture Diagram: Added technical note about ProcessorRouter and LocalPhi4Processor swap
  6. New full section "Offline Mode (Local AI)" after "Deploying to Azure" with comprehensive subsections covering: How It Works (comparison table), Setting Up Offline Mode (4-step guide with model downloads and GPU setup), Piper TTS Voice Selection (4-voice comparison table), and Azure Local Compatibility (enterprise benefits and Docker deployment)
- **Why:** Brian requested comprehensive documentation for the new offline mode feature to help developers and stakeholders understand how local AI inference works, setup requirements, voice options, and edge deployment capabilities. Maintains existing README tone (enthusiastic, technically precise, McDonald's-branded) while ensuring all content is additive (no existing content removed or modified).
- **Standards Maintained:** Preserved all existing content, matched heading hierarchy and Markdown conventions, kept Table of Contents accurate, used same McDonald's-branded tone and style, included technical details and practical code examples.

## Team Updates (2026-03-23T12:18Z)

### From Scribe Orchestration
- ✅ Orchestration log written for Ronald background task (README section)
- ✅ Session log created for menu expansion & README work
- ✅ Task outcome verified: SUCCESS
- **Pending:** Decision merge, git commit, history summarization check

## Team Updates (2026-04-02T16:30Z)

### Offline Mode Phase Completion
- ✅ **Documentation (Ronald):** README updated with 80+ lines of offline mode content across 6 sections
- **Sections Added:** What is Local Mode, Prerequisites, Setup, Features, Configuration, Troubleshooting
- **Decisions Merged:** #40–#41 captured (offline mode documentation, user directives)
- **Links:** Piper voices reference (huggingface.co/rhasspy/piper-voices), Azure Local compatibility callout
- **Next:** Documentation complete, ready for user guidance and deployment guides

## Sonic parity review — feat/sonic-parity (2026-09-22)
- Items 1–7 landed as separate commits on the cloud realtime path only; local mode (Phi-4/Piper) untouched except for the shared browser hook (keep=false plus 4000 handling, harmless for local).
- Silent-model flags (raised by the brief): (1) FIXED — a tool exception used to escape `response.output_item.done` and kill the session with no function_call_output; it now returns an apology result. (2) OPEN — `tools.search`: only `search_client.search()` is guarded, so pager iteration and the semantic retry can raise (now caught by the seam with a generic apology). (3) OPEN — `response.done status=failed` (e.g. rate limit on the shared deployment) is relayed silently, with no retry or apology. (4) OPEN — local/test sockets (processor_router L306/L357/L376, app.py ws_test_handler) keep the aiohttp default compress=True (aiohttp#13274).
- Shared OpenAI: provision with reuse=true touches rg-sonic-demo only through two already-existing role assignments; it declares no deployments.

## Round 3 review — feat/round3 (2026-09-23)
- One commit per item: L1 (local-mode socket compression + AST guard), dz reasoning-name test, R3 (i18n template sweep + guard), R2 (verbatim smoke transcription + tenant-pinned auth) + two follow-up fixes, R1 backend / frontend / clips, docs. Nothing pushed, merged or deployed.
- Reviewed R1 for talk-over risk: every retry path is cancelled by guest speech; the clip is played with the mic muted so it can't cancel the retry itself; `final` always reopens the mic. Local mode (Phi-4/Piper) doesn't emit `extension.rate_limited` and is otherwise untouched apart from L1.
- Needs a deploy to confirm: real rate-limit event shapes and hint text on the shared deployment, clip playback + mute in a real browser, and the azd env — `mcd-demo` still has `AZURE_OPENAI_REALTIME_DEPLOYMENT=gpt-realtime-2.1`, not the `-dz` deployment McDonald's is meant to use (Brian's call).
- Still open from the Sonic-parity review: `tools.search` guards only `search_client.search()` (generic apology via the tool-error seam covers the rest). The docs heading "Customizing the VoiceRAG deployment" is a template leftover outside R3's i18n scope.
- **Order resume port — review (2026-09-23, feat/order-resume):**
  - Scope held to the cloud realtime path. Local (Phi-4/Piper) and Azure Speech modes are unchanged: their tests pass and they show no resume UI.
  - Deltas from Sonic are documented in `docs/order_resume.md` ("How it maps onto McDonald's"): the router seam, R1 composition, the voice re-send, the held tap, no ctx_monitor feeding, and `resumeEnabled`.
  - Only a deploy can confirm:
    - the sticky affinity cookie on the ws upgrade;
    - the session secret surviving provision;
    - the real-browser mic auto-restart on the deployed origin.
  - No push/merge/deploy.
  - Closed: the 'Customizing the VoiceRAG deployment' template heading (and the other template-branded doc headings) was fixed in 9f28e0e, with a heading guard in test_rebrand_verification.
