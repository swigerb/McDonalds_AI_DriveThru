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
