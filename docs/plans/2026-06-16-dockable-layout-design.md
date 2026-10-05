# Dockable Layout — Design & Handoff (2026-06-16)

PyCharm-like dockable panels for the session view. Brainstorm + prototype done; first
real implementation not started. This doc is the resume point after context compaction.

**To resume:** read this doc + the prototype `resolver.js` (path below). The resolver IS
the executable spec.

## Goal

Session view tabs (Chat / Files / Git / Terminal / Artifacts / Orchestration) become
dockable "tool windows" placeable in 6 docks around a fixed center. Tab mode stays the
default; docking is opt-in. Layout auto-derives from available space (no manual modes).

## Status

- Design validated via an interactive prototype (the artifact below).
- Pure resolver complete, **20/20 scenario tests pass**.
- Branch/worktree: `layout` (`.worktrees/layout`). Created by user; stay on it.
- Code of the real app: NOT touched yet. Next step = explore session-view code, then implement.

## The Artifact (prototype)

Dir: `~/.twicc/artifacts/54b42b89-290a-4324-bf86-f636a048d23d/layout-playground/`
(session_id `54b42b89-290a-4324-bf86-f636a048d23d`). Files:

- **`resolver.js`** — PURE layout resolver, no DOM, no side effects. **Port into the frontend
  as-is.** Single source of all rules + thresholds. `resolve(input) -> renderDescription`.
- `playground.js` — UI harness: resizable fake window, controls (assign tabs→docks, tune
  thresholds, scenario presets), renders resolver output, draggable splitters (prototype
  only), live `W×H` per region for debug.
- `tests.js` — 20 scenario assertions, data-in/out. `runChecks(resolve)`.
- `index.html`, `style.css`.

Open in UI: `/artifacts/54b42b89-290a-4324-bf86-f636a048d23d/layout-playground/index.html`
Run tests headless: copy `resolver.js`+`tests.js` to a scratch dir with
`package.json {"type":"module"}`, run a `.mjs` importing `runChecks`+`resolve`.

## Core model

- **6 docks**: `left-top, left-bottom, right-top, right-bottom, bottom-left, bottom-right`. Plus CENTER.
- **Center = the chat** (messages + input). Pinned, always present, NOT movable.
- Other tabs are dockable. A non-docked tab stays in the center tab strip (next to chat).
  **No tab is ever hidden**; not-docked = in center strip. Center always has ≥1 tab.
- Each dock is its own tab bar. A tab is a singleton in exactly one place.
- **Gutter** = thin rail on an edge holding icons of collapsed/hidden docks.

## Resolver contract

```
input = {
  tabs:        [{ id, label, icon, optional?, hasContent?, fixedCenter? }]
  assignment:  { [tabId]: dockId | 'center' }     // chat (fixedCenter) is always center
  viewport:    { w, h }                            // session content area px (measured live)
  activeSide:  'left' | 'right'                    // wins under mutual exclusion
  activeResize:'left' | 'right'                    // column with priority when resizing
  collapsed:   string[]                            // user-minimized docks
  config?:     partial DEFAULT_CONFIG
}
renderDescription = {
  mode: 'widescreen' | 'classic' | 'tabs',
  viewport: {w,h},
  regions:   [{ id, kind:'center'|'col-left'|'col-right'|'bottom', x,y,w,h, label,
                slots:[{dockId, tabs}], merged, mergedFrom? }],   // px rects, abs from top-left
  gutters:   [{ edge:'left'|'right'|'bottom', x,y,w,h,
                items:[{dockId, tabs, action:'swap'|'overlay'|'restore', anchor:'start'|'end'}] }],
  overlays:  [{ edge, rect:{x,y,w,h}, tabs }],     // 95% rect a peek would take
  splitters: [{ id, kind:'sib'|'dock', axis:'h'|'v', x,y,w,h, originX,originY, from:'start'|'end',
                extent, configKey }],              // resize handles (UI wiring deferred)
  decisions: [string]                              // human "why" log (shown in playground)
}
```

## Rules (all in resolver, all space-derived)

- **Mode is derived, not chosen.** widescreen = sides full height, bottom under center.
  classic = bottom full width, sides shorter above it. Only differs when a side AND bottom coexist.
- **Bottom flip (→ classic): WIDTH-ONLY.** Bottom sits under center until center-col width
  `< bottomComfortW`, then full width. Independent of sibling count and of column heights.
  (A single terminal goes full width once center is cramped — VS Code style.)
- **Sibling merge:** vertical siblings (l-top/l-bot, r-top/r-bot) merge when side-column height
  `< sideMergeBelowH`. Horizontal siblings (b-left/b-right) merge when bottom span width
  `< bottomMergeBelowW`. ONE threshold each (no `×2`). **Merge depends only on viewport extent,
  never on resize.**
- **Width cascade.** `capacity` = side columns that fit (`centerMinW + n×sideMinW <= W`).
  2 → both shown. 1 + both demand → **mutual exclusion** (shown = activeSide; other = SWAP
  gutter, click swaps which shows). 0 → overlay gutters. `W <= mobileMaxW` → pure tabs (all in center).
- **Mutual exclusion = SWAP, not overlay.** Overlay reserved for: no column fits at all /
  bottom no room / optional-empty / user-minimized.
- **Gutters take REAL space** (inset the layout). Extent mirrors the replaced region per mode:
  widescreen bottom gutter = under center only; classic side gutter = column-band height only.
  Icons anchored start/end mirroring origin (top/bottom on sides, left/right on bottom).
  **ONE icon per TAB** (dock with 2 tabs → 2 icons). Click action = swap | overlay | restore.
- **Overlay** opens at 95% (`overlayCoverage`), 5% escape strip; click-outside / Esc closes;
  gutter stays visible.
- **Optional tabs** (Artifacts/Orchestration): empty → collapsed to gutter by default, click =
  overlay placeholder. With content → real region. (Files/Git assumed always present for now.)
  > **Amended 2026-06-18 — this rule was reversed.** An optional tab with **no content** is now
  > treated as **absent**: no tab, no dock, no gutter, no overlay — exactly as if it were never in
  > `tabs` (the app just omits a not-yet-present tool). It does **not** collapse to a gutter, and
  > there is no overlay placeholder. Its dock **assignment** is remembered, so it appears at the
  > right dock once content arrives (a present sibling in the same dock shows alone until then).
  > The optional set is also wider than this line says: **only Files and Terminal are always
  > present**; Git, Artifacts, Orchestration (and future tabs) are optional; Chat + subagents are
  > center-only (not dockable). The resolver now filters `optional && !hasContent` up front
  > (`isPresent`), so the old "empty-optional → gutter/overlay" path — also referenced above
  > ("Overlay reserved for: … optional-empty …") and below ("Coexist edge case … optional-empty
  > bottom dock gutter") — is gone. Rationale + the remaining work (an optional-tab registry, and
  > persistence so a default layout can pre-assign a not-yet-present tab) live in the
  > 2026-06-17 impl handoff (2026-06-18 entries).
- **Resize siblings:** bounded ONLY by `siblingMaxFrac` (one ≤ 80%, other ≥ 20%). NOT clamped to
  any px min. Logic in resolver; **UI wiring deferred**.
- **Resize docks:** column width vs center, bottom height vs center. Clamps: center `≥
  centerResizeMinW` wide / `≥ centerResizeMinH` high; side col `≥ sideResizeMinW`; bottom `≥
  bottomResizeMinH`. No max except the neighbor's min. The actively-dragged column wins
  (computed first, pushes the other to its min, restores on release) via `activeResize`.
  Logic in resolver; **UI wiring deferred**.

## Storage / persistence (decided, not yet built)

- Persist the **intention** (assignment, collapsed, activeSide, activeResize, splitter
  fractions), NOT the resolved geometry (ephemeral, recomputed each render/resize).
- **3 tiers** like agent settings: global default → project override → session, resolved at
  creation only (later global/project changes don't touch an existing session).
- Synced settings (user config + project + session DB), same shape across devices.
- **Multi-device:** one canonical layout + responsive degradation; mobile = tabs. Per-device
  local override (localStorage) is a possible v2 escape hatch, deferred.

## Config (DEFAULT_CONFIG) — single tuning point

Structural (decide STRUCTURE): `centerMinW 460, centerMinH 220, sideMinW 280,
sideMergeBelowH 300, bottomMinH 150, bottomMergeBelowW 560, bottomComfortW 560, mobileMaxW 480`.
Resize clamps (how far you can DRAG): `centerResizeMinW 300, centerResizeMinH 150,
sideResizeMinW 150, bottomResizeMinH 150, siblingMaxFrac 0.8`.
Default ratios (user-draggable): `leftColFrac 0.22, rightColFrac 0.22, bottomFrac 0.30,
leftSplitFrac/rightSplitFrac/bottomSplitFrac 0.5`.
Misc: `overlayCoverage 0.95`. `RAIL = 26` (gutter thickness, const in resolver).
Note: structural mins (e.g. 460/220) intentionally differ from resize mins (300/150).

## Architecture stance (for the port)

- Single PURE resolver module (port `resolver.js`). One place for rules/thresholds.
- 3 layers: **measure** (ResizeObserver on session content area) → **resolve** (pure) →
  **render** (dumb Vue, NO layout logic, NO CSS media queries).
- Same philosophy as `computeVisualItems()` (raw → pure transform → render, stabilized).

## NEXT STEP — agreed scope for first real implementation

1. Per-tab **small accessible arrow** → dropdown "place to → dock / center". **Menu only**, no drag-drop.
2. Render session view via the resolver: docks + gutters + responsive. **Native Web Awesome
   tabs** first (`wa-tab-group`); custom button-style tabs later.
3. **NO resize splitters in the UI** (resolver has the logic; wire later).
4. Goal: get docks + gutters correct, testable through the placement menus.
5. FIRST sub-task: **explore** existing session-view + tab-bar code (was about to start when
   paused). Then propose a concrete plan (files/components) before editing.

## Deferred (explicitly, do NOT do now)

keyboard nav between docks/tabs; persistence + 3-tier wiring; per-device localStorage override;
tab **lifecycle** (run work on "became visible/focused" instead of "tab activated"); drag-and-drop
placement; named layouts / presets; animations; custom WA tab styling (use native first); resize
UI wiring (siblings + docks); reset-to-project/default/tabbed; `wa-split-panel` nesting / `wa-reposition`
event traps (port concern for resize).

## Open / to revisit

- Thresholds are starting values — tune live in the playground.
- Coexist edge case (bottom region shown + an optional-empty bottom dock gutter): handled
  "good enough" (rail stacks below the region); revisit if it looks off.
- Structural vs resize min split could confuse tuning; revisit naming if needed.
