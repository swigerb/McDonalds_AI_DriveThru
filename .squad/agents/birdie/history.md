# Birdie — History

## Sessions

_No sessions yet._

## Learnings

### 2026-03-23: Breakfast/Lunch Menu Mode Toggle
- **What:** Created `MenuModeContext` with localStorage persistence, a ☀️/🍔 segmented toggle in Settings, and `menuPeriod`-based filtering in `menu-panel.tsx`.
- **Why:** Extra Value Meals had duplicate meal numbers (breakfast #1-5 and lunch #1-10 shown together). The toggle lets operators switch menus cleanly.
- **Pattern:** Followed `dummy-data-context.tsx` exactly for context shape — `createContext`, `Provider` with `localStorage`, hook with error boundary.
- **Filtering approach:** `useMemo` keyed on `menuMode` inside the `React.memo` component — context change triggers re-render and `useMemo` recomputes the filtered list. Items without `menuPeriod` treated as "allDay" for backward compatibility while Grimace adds the field to `menuItems.json`.
- **Settings placement:** Menu Mode toggle placed FIRST (before Dark Mode) as requested — most operationally relevant for demo.
- **Styling:** Used McDonald's yellow `#FFBC0D` for active segment state, dark brown `#27251F` for text, consistent with existing branding.
- ✅ Merged to decisions.md.

### 2026-07-17: Session Token Support for WebSocket
- **What:** Added `fetchSessionToken()` and token-aware WebSocket URL construction to `useRealtime.tsx`. Ported from Sonic project's equivalent hook.
- **Changes:** (1) `fetchSessionToken()` calls `/api/auth/session` on mount, (2) appends `?token=...` to `/realtime` WS endpoint when token exists, (3) auto-refreshes token on WebSocket close with code 4001 or "expired" reason.
- **Backward compatible:** If `/api/auth/session` doesn't exist (404, network error), token is null and WS connects without it — identical to previous behavior.
- **Pattern:** `useState<string | null>` for token, `useEffect` on mount for fetch, `buildWsEndpoint()` helper replaces inline ternary. Matches Sonic implementation exactly.
- **Key detail:** `encodeURIComponent` on token in query string to handle special characters safely.
- **Build:** TypeScript clean — `tsc --noEmit` passes with zero errors.

### 2025-07-20: Local Mode Toggle UI (Phase 3)
- **What:** Added full "Local Mode" toggle to Settings panel with context provider, WebSocket messaging, and visual indicators.
- **Files created:** `app/frontend/src/context/local-mode-context.tsx`
- **Files modified:** `settings.tsx` (new toggle + disabled AI Voice when local), `useRealtime.tsx` (`sendLocalModeToggle`), `App.tsx` (provider + wiring), `status-message.tsx` (🔌 Local / ☁️ Cloud badge)
- **Pattern:** Followed `menu-mode-context.tsx` exactly — `createContext`, `Provider` with `localStorage`, hook with error boundary. Default `false`.
- **Settings placement:** Local Mode toggle sits AFTER Dark Mode, BEFORE AI Voice selector.
- **Status indicator:** Toggle shows loading spinner, green "● Ready", or amber "⚠ Model not available" based on `/api/local-mode/status` fetch.
- **AI Voice:** Disabled when local mode active since Piper handles voice. Shows "Piper (Local)" hint text.
- **Mic button:** Yellow dot badge (absolute positioned, top-right) when local mode active.
- **Status bar:** Inline pill badge showing "🔌 Local" (yellow) or "☁️ Cloud" (blue) next to status text.
- **WebSocket:** Sends `{ type: "extension.set_local_mode", enabled }` via `sendLocalModeToggle`.
- ✅ TypeScript compiles cleanly (`tsc --noEmit` — zero errors).

### 2025-07-22: Piper Voice Selection Dropdown (Local Mode)
- **What:** Added a "Local Voice" dropdown that appears ONLY when local mode is active, replacing the disabled Azure voice selector.
- **Files modified:** `settings.tsx` (conditional Azure/Piper voice selectors), `useRealtime.tsx` (`sendPiperVoiceChoice`), `App.tsx` (`piperVoice` state + localStorage + wiring)
- **Voice options:** Amy (US, default), Jenny (UK), Lessac (US), Kristin (US) — all `*-medium` Piper models.
- **Pattern:** Followed exact same `voiceChoice` pattern — localStorage persistence, prop drilling through Settings, WebSocket message via `sendPiperVoiceChoice`.
- **WebSocket message:** `{ type: "extension.set_piper_voice", voice: "en_US-amy-medium" }` — consistent with other `extension.*` message types.
- **UI:** Conditional render (`{!localMode && ...}` / `{localMode && ...}`) with CSS transitions for smooth show/hide. Label uses 🎙️ icon, description says "Upbeat drive-thru voices — runs locally".
- **localStorage key:** `piperVoice` (separate from `voiceChoice` to avoid conflicts).
- ✅ TypeScript compiles cleanly (`tsc --noEmit` — zero errors).
