# Ronald — History

## Sessions

_No sessions yet._

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
