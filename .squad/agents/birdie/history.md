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
