# Dockable Layout — Implementation Step 1 Plan (2026-06-16)

First real-code step of the dockable layout. Reads on top of the design doc
[`2026-06-16-dockable-layout-design.md`](./2026-06-16-dockable-layout-design.md)
and the prototype `resolver.js` (artifact dir in the design doc). **The resolver IS
the executable spec** — port it, don't reinvent it.

**To resume after compaction:** read this plan + the design doc + the prototype
`resolver.js` and `playground.js` (the latter shows exactly how the resolver output is
consumed — it's the reference for the render layer).

## Scope of step 1

IN:
- Port the pure resolver into the frontend (rename TBD, e.g. `layoutResolver.js`).
- Per-tab **placement menu** (small accessible arrow → dropdown: center / each dock).
- Measure → resolve → render the session view as docks + gutters + overlays, using
  **native Web Awesome tabs** and **`<Teleport>`** so panels never re-mount on a move.
- **Minimize-to-gutter + restore** (the deliberate way to exercise gutters from the UI).
- Ephemeral per-session layout state (no persistence yet).

OUT (deferred — see design doc "Deferred"):
- Resize splitter UI (resolver emits `splitters`; wire later).
- Persistence + 3-tier resolution (mirror agent settings later).
- Maximize, keyboard nav, drag-and-drop, named layouts, animations, custom tab styling,
  per-device override, tab lifecycle hooks.

## Resolver contract (recap — source of truth is `resolver.js`)

`resolve(input) -> renderDescription`. Output shape (px rects, absolute from the measured
area's top-left):

- `mode`: `'widescreen' | 'classic' | 'tabs'`
- `regions`: `[{ id, kind:'center'|'col-left'|'col-right'|'bottom', x,y,w,h, label,
  slots:[{dockId, tabs}], merged, mergedFrom? }]`
- `gutters`: `[{ edge:'left'|'right'|'bottom', x,y,w,h,
  items:[{dockId, tabs, action:'swap'|'overlay'|'restore', anchor:'start'|'end'}] }]`
- `overlays`: `[{ edge, rect:{x,y,w,h}, tabs }]` (95% peek)
- `splitters`: `[...]` — **ignored in step 1** (no resize UI)
- `decisions`: `[string]` — debug only

`input = { tabs, assignment, viewport:{w,h}, activeSide, activeResize, collapsed, config? }`.
`tabs[]` = `{ id, label, icon, optional?, hasContent?, fixedCenter? }`.

### Consumption facts taken from `playground.js` (the render reference)

- **Per-region active tab memory**: `groupKeyOf(slots)` = `'center'` if any slot is center,
  else the region's dockIds sorted+joined. `activeTabOf(groupKey, tabs)` = explicit choice
  if still valid, else first content-bearing tab. In the real app the **route overrides**
  the active tab of the region it points into (see Routing below); other regions use this
  per-group memory.
- **Gutter**: one icon **per tab** (a 2-tab dock → 2 icons), anchored start/end per
  `item.anchor`. Click action = `swap` (show this side column) | `restore` (un-minimize) |
  `overlay` (peek 95%).
- **Overlay**: 95% panel with its own tab bar + close; backdrop closes; gutter stays visible.

## Architecture

Three layers, mirroring `computeVisualItems` (`utils/visualItems.js` → wiring in
`stores/data.js` → dumb render):

1. **Measure** — `useElementSize` (VueUse, already used in `ContributionGraph.vue`) on the
   session content container → `viewport {w,h}`.
2. **Resolve** — the ported pure resolver. Pure, no Vue, unit-testable (port `tests.js` too).
3. **Render** — dumb components, NO layout math, NO media queries. Regions are
   **absolutely-positioned** divs using the resolver's px directly.

### Teleport model (the crux — preserves panel state across moves)

- Each dockable panel component is mounted **exactly once** in a stable hidden host
  (per session). It is **never** rendered a second time and **never** moved by `v-if`.
- Each panel resolves a **teleport target**:
  1. dock shown as a region → that region's body element;
  2. dock in a gutter with its edge's overlay open and this tab is overlay-active → overlay body;
  3. otherwise → the hidden host (`display:none`, stays mounted).
- Within a region, only the active tab's panel is visible (`display`/`v-show`), others hidden
  — same property as today's `wa-tab-group` (`display:none` on inactive panels), so nothing
  re-inits on tab switch.
- Region/overlay body refs are collected into a reactive `Map` (keyed by region id / edge).
  `<Teleport :to="targetFor(tab)" :disabled="!targetFor(tab)">`; when a target isn't mounted
  yet, `disabled` keeps the panel in the host (hidden). Resolve the chicken-and-egg with a
  reactive target map updated on mount (watch / `nextTick` as needed).

**Session scoping (explicit risk):** teleport targets and the panel host are **per session
instance**. `SessionView` is kept alive per `route.params.sessionId` (in `ProjectView.vue`,
`<KeepAlive :key="sessionId">`). Never share a host/target Map across sessions; key
everything by the owning `SessionView` instance so session A's Files never teleports into
session B's region. Build targets from refs owned by this component instance only.

### Component tree (names provisional)

- `components/session/layout/SessionLayout.vue` — dumb render of `renderDescription`:
  region divs (abs-positioned) + gutters + overlays + the hidden panel host. Owns the target
  `Map`. Receives `renderDescription` + the panel slot content.
- `DockRegion.vue` — one region: native tab bar (`wa-tab-group`, one `wa-tab` per slot tab) +
  per-tab placement arrow + minimize button + body (teleport target). Emits active-tab and
  placement changes.
- `DockGutter.vue` — one edge gutter: icons per tab, anchored, dispatches swap/restore/overlay.
- `LayoutOverlay.vue` — 95% overlay panel + backdrop + close.
- `TabPlacementMenu.vue` — the small arrow + `wa-dropdown` (center / 6 docks). Reused by the
  center strip and every dock tab bar.
- `composables/useSessionLayout.js` — measure + read intention state + call resolver →
  reactive `renderDescription`; actions: `place(tabId, dest)`, `minimize(dockId)`,
  `restore(dockId)`, `swapSide(edge)`, `setActive(groupKey, tabId)`, `openOverlay(edge, tabId)`.
- `utils/layoutResolver.js` — the ported resolver (verbatim from the artifact).

`SessionView.vue` changes: mount the panel components once into the host; pass
`renderDescription` to `SessionLayout`; **gate** (see below); feed the content-container ref
to the measure.

### Gating (opt-in, low risk)

While **no tab is docked** (all `assignment` ⊆ center) → keep today's plain `wa-tab-group`
behavior untouched (and the existing height-based compact mode at `max-height:900px`). As
soon as ≥1 tab is docked → switch to the resolver-driven `SessionLayout`. Matches "tab mode
= default, docking = opt-in" and avoids touching the compact mode in step 1.

## Routing (agreed)

The route stays the single pointer to "the active/focused tab" — names and route→tabId
mapping are essentially unchanged. Only the **effect** of `activeTabId` generalizes:

- routed tab is in center → center strip activates it (exactly as today);
- routed tab is in a dock → that dock's bar activates/focuses it; if the dock is collapsed
  to a gutter, open it (going to a tab makes it visible, as today).

Per-dock active state is the memory for docks the route doesn't point at; the route overrides
only its target region. Net: routing layer near-intact, only the consumer of `activeTabId`
is generalized.

## State (ephemeral, step 1 — no persistence)

Add to `stores/data.js` `localState`, keyed by sessionId, mirroring `sessionOpenTabs`:

```
sessionLayout: { [sessionId]: {
  assignment: { [tabId]: dockId | 'center' },  // chat is always center (fixedCenter)
  collapsed:  string[],                        // user-minimized docks
  activeSide: 'left' | 'right',
  activeResize: 'left' | 'right',              // unused until resize UI lands
  activeByGroup: { [groupKey]: tabId },
  // config (splitter fractions) omitted in step 1; resolver defaults apply
} } }
```

Getters/actions follow the `sessionOpenTabs` pattern. Persistence (IndexedDB / synced
settings / 3-tier) is deferred — when it lands it mirrors agent settings
(`utils/projectAgentDefaults.js` → `_resolveDraftAgentSettings` → `useSessionAgentSettings`).

## Mapping real tabs → resolver input

Build `tabs[]` from what the session actually exposes:

| tab | id | in `tabs[]` when | flags |
|---|---|---|---|
| Chat | `main` | always | `fixedCenter:true` |
| Files | `files` | always | — |
| Terminal | `terminal` | always | — |
| Git | `git` | `hasGitRepo` | — |
| Artifacts | `artifacts` | `hasArtifacts` | — |
| Orchestration | `orchestration` | `hasSpawnRoot` | — |

Conditional tabs simply absent from `tabs[]` when their condition is false (don't rely on
`optional`/`hasContent` to hide them). Artifacts/Orchestration are still **dockable when
present**; only the resolver's "empty optional dock → defaults to a gutter" path is unused.
`optional`/`hasContent` are therefore **unused in step 1** (every present tab has content);
keep the fields for forward-compat.

> **Don't forget (deferred from step 1):** wire `optional`/`hasContent` so an empty optional
> dock degrades to a gutter with an overlay placeholder (resolver already supports it).

**Subagent tabs** (`agent-<id>`, dynamic + closeable): **stay in center, not dockable —
settled, won't change.** They render in the center strip as today.

## Step 1 interactions

- **Placement menu** per tab → `place(tabId, dest)` mutates `assignment` → re-resolve →
  panel teleports to the new region body (no re-mount).
- **Minimize**: a region-level button at the **far right of the dock's tab bar** (never on
  the center — it's pinned). Click → `minimize(dockId)` adds the dock to `collapsed`; the
  resolver drops the region and emits a `restore` gutter (one icon per tab, anchored per
  origin); freed space reflows; the panel teleports to the hidden host (stays mounted). For a
  **merged region** (two siblings in one bar) the button collapses **both siblings at once**
  → two restore icons in the gutter. **Restore** from a gutter icon → removes the dock from
  `collapsed`, sets that tab active; the region returns and the panel teleports back (no
  re-mount). One edge gutter merges responsive items (swap/overlay) and minimized items
  (restore) — `gutterItems(edge)` already handles this.
- **Responsive gutters/overlays/swaps** arise automatically from the resolver as the measured
  area shrinks (swap under mutual exclusion, overlay when no column fits, bottom→gutter when
  too short). Wire the gutter actions exactly as `playground.js` does.
- **No splitters rendered.** Default fractions from `DEFAULT_CONFIG` apply.

## WA / project traps to honor

- `wa-dropdown` (placement arrow) lives inside a `wa-tab-group`: WA custom events bubble —
  scope `wa-show`/`wa-select`/`wa-after-*` with `.stop` / target guards so the dropdown
  doesn't disturb the tab group (see CLAUDE.md "Bubbling custom events").
- The arrow must not trigger a tab switch: `@click.stop` on the trigger.
- Any new `wa-*` component must be imported in `frontend/src/main.js`.
- New code lands idiomatically: pure → `utils/`, composable → `composables/`,
  components → `components/session/layout/`.

## Suggested implementation order

1. Port `resolver.js` → `utils/layoutResolver.js` (+ port `tests.js`, run headless, expect
   20/20) — pure, no UI yet.
2. `useSessionLayout.js`: measure + ephemeral state + `renderDescription`. Unit-sanity via a
   throwaway harness if useful.
3. `SessionLayout` + `DockRegion`/`DockGutter`/`LayoutOverlay` rendering from a **static**
   `renderDescription` (no teleport yet) to validate geometry against the playground.
4. Wire **Teleport** of the real panels (Files/Git/Terminal/Artifacts/Orchestration + center
   chat + non-docked tools); verify no re-mount on move (Terminal keeps its PTY).
5. `TabPlacementMenu` + minimize/restore; gate in `SessionView`; verify gutters/overlays/swaps
   by shrinking the window.
6. Manual pass across the playground scenarios in the real app.

## Open / revisit

- Deep-link to a docked tab while collapsed: open its gutter + focus (per Routing) — confirm
  the exact UX when implementing step 5.
- Subagent tabs dockability (kept center-only in step 1).
- Center strip when chat + several non-docked tools coexist: native `wa-tab-group` ordering.
