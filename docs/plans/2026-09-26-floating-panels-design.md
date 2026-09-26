# Floating panels on a lit canvas — design (visual refresh, step 1)

## 1. Context

The UI reads flat: one white (or one dark) surface everywhere, hairline dividers between
zones, almost no depth. An exploration (interactive mock in the session artifacts,
`ui-directions/index.html`, direction "C · Signature") settled a direction. It is split
into independent steps, each one visible and reversible on its own:

| Step | Content |
|---|---|
| **1 (this document)** | Canvas background + floating panels |
| 2 | Depth: layered soft shadows on cards, buttons, inputs |
| 3 | Accent-tinted glass on overlays (dialogs, menus, toasts, palette, settings) |
| 4 | Motion tokens, sliding tab ink, animated expand, micro-interactions |
| 5 | Entrances (messages, overlays, skeletons) — virtual-scroll aware |
| 6 | Accent glow + live states |
| 7 | Secondary screens (stats, home, tasks, question widget) |

Only step 1 is specified here. Steps 2–7 get their own design documents.

## 2. Goal of step 1

- The page background becomes a **canvas**: a color slightly tinted by the accent, with two
  faint accent-hued radial auras.
- The **sidebar sits directly on the canvas** (no opaque background, no divider line).
- The working zones become **cards** floating on the canvas: rounded corners, a small gap
  around them, a soft shadow, a full border.
- In a session, **each dock region is its own card**: the center, the right-top dock, the
  right-bottom dock, the bottom docks, the left docks — all separated by the gap.

Delivered in **two commits**, each one usable on its own:

1. **Commit 1 — canvas + one card.** Canvas, transparent sidebar, and the whole content area
   (`.main-content`) as a single card. Dock regions keep today's dividers inside it.
2. **Commit 2 — one card per region.** `.main-content` becomes transparent; the session
   header sits on the canvas; each layout region becomes a card; the gap separates them.

## 3. Non-goals

- No change to `utils/layoutResolver.js`: geometry, thresholds, fractions, splitter
  positions and drop zones stay as they are.
- No change to the panels' own surface: panels keep `--wa-color-surface-default`, so the
  terminal (`useTerminal.js` via `getSurfaceColor()`), CodeMirror, sticky headers and every
  element that paints `surface-default` to match its pane keep matching.
- No shadow/depth work beyond the panel cards themselves (step 2).
- Out of scope bundles: the share viewer (`share-session/`, `share-recent/`) and the artifact
  shell (`artifact-shell/`). They do not load `App.vue` and keep their current look.
- No change to the theme / accent settings. Everything derives from Web Awesome tokens, so it
  works with every theme (`default`, `awesome`, `shoelace`) and every accent.
- No change to modal overlays (command palette, search overlay, dialogs, settings popover).

## 4. Tokens — `frontend/src/styles/surfaces.css`

A new global stylesheet, imported in `frontend/src/main.js` right after
`./styles/transcript-tokens.css`. It is SPA-only on purpose (the share bundle imports
`transcript-tokens.css`, not this file).

Tokens, declared on `:root` and overridden under `.wa-dark`:

| Token | Light | Dark |
|---|---|---|
| `--canvas-color` | `color-mix(in oklab, var(--wa-color-brand-95) 30%, var(--wa-color-neutral-95))` | `color-mix(in oklab, color-mix(in oklab, var(--wa-color-surface-default), black 30%) 92%, var(--wa-color-brand-20))` |
| `--canvas-aura-start` | `color-mix(in oklab, var(--wa-color-brand-90) 60%, transparent)` | `color-mix(in oklab, var(--wa-color-brand-30) 35%, transparent)` |
| `--canvas-aura-end` | `oklch(from var(--wa-color-brand-90) l c calc(h + 70) / 0.45)` | `oklch(from var(--wa-color-brand-30) l c calc(h + 70) / 0.28)` |
| `--panel-gap` | `var(--wa-space-xs)` (0.5rem) | same |
| `--panel-half-gap` | `calc(var(--panel-gap) / 2)` | same |
| `--panel-radius` | `var(--wa-border-radius-l)` (0.75rem in the default theme) | same |
| `--panel-border` | `var(--divider-size) solid var(--wa-color-surface-border)` | same token, dark value |
| `--panel-shadow` | `0 1px 2px oklch(0.25 0.02 275 / .06), 0 6px 10px -6px oklch(0.25 0.02 275 / .14)` | `0 1px 2px oklch(0 0 0 / .4), 0 6px 10px -6px oklch(0 0 0 / .55)` |

Notes:

- `--divider-size` keeps the theme's divider thickness (1px, or 4px under
  `[data-theme="awesome"]`, see `App.vue`), so the card border follows the theme.
- `--wa-border-radius-*` scale with the theme's `--wa-border-radius-scale` (default 1,
  awesome 1.5, shoelace 0.7), so the card radius follows the theme too. The rem values given
  in brackets in this document are the default-theme values.
- **Shadow budget.** `--panel-shadow` is deliberately "cast downward". Both layers have a
  zero x-offset; the large one also has a negative spread. A box-shadow's blur B is a
  Gaussian with σ = B/2, visible about B beyond the offset + spread edge, so:
  - **horizontal reach** is max(2, 10 − 6) = **4px**. On desktop the gap is ≥ 6px at every
    font size (0.5rem, font-size setting ≥ 12px), so every clip can stop at the gap (§6.1,
    §6.3) and no shadow spills sideways onto a neighbour card. On narrow viewports the gap
    is 0.25rem (3–4px at font sizes 12–16, wider above), equal to or below the reach: the
    cut residual is about 2% alpha at most (dark shadow, font size 12), and the same residual
    can reach a side neighbour when docks show side by side (layout wider than 520px on a
    narrow viewport) — accepted;
  - **downward reach** is 6 − 6 + 10 = **10px**, more than the gap. At the bottom of the
    layout the cut falls exactly on the viewport's bottom edge (`.session-view`'s edge plus
    the gap is `.main-content`'s padding edge, i.e. the viewport edge) — invisible. Between
    two stacked cards (center → bottom docks, `left-top` → `left-bottom`, `right-top` →
    `right-bottom`, columns → classic bottom), the lower card comes later in the DOM and
    paints over the upper card's spill;
  - **upward reach** is about 1px (small layer only: blur 2 − offset 1), negligible.
  Tuning (below) must keep the horizontal reach ≤ 4px; more depth belongs to step 2.
- The values above are the starting point. They are tuned by eye in the running app (light,
  dark, at least two accents, the three themes) before commit 1, within the shadow budget;
  the final values are recorded in the stylesheet, not in this document.
- Declaration order and places in `surfaces.css`: the light tokens on `:root`, then the dark
  overrides on `.wa-dark` (same specificity, later wins — both match `html`, where
  `utils/theme.js` puts `wa-dark`), then the narrow-viewport override
  `@media (width < 640px) { :root { --panel-gap: var(--wa-space-2xs); --panel-radius:
  var(--wa-border-radius-m); } }` (0.25rem and 0.375rem). The override **must be on `:root`**:
  the derived tokens (`--panel-half-gap`, `--panel-inner-radius`, `--panel-corner-inset`) are
  computed where they are declared (`:root`) and inherited as values, so an override placed
  lower in the tree would leave them at their desktop values. The cards stay on mobile, only
  tighter.

A shared utility class, `.panel-card`, in the same file:

```css
.panel-card {
    background: var(--wa-color-surface-default);
    border: var(--panel-border);
    border-radius: var(--panel-radius);
    box-shadow: var(--panel-shadow);
}
```

Every element that becomes a card (commit 1: `.main-content`; commit 2: the elements listed
in §6) carries the `.panel-card` class — never its own copy of the values. An element that
needs a different shadow (the layout overlay) keeps the class and overrides only
`box-shadow` locally. The class is also the marker the iframe corner clipping looks for
(§6.4), so **every card, without exception, carries it**.

### Painting the canvas

- `html` gets `background-color: var(--canvas-color)` (it replaces Web Awesome's
  `html { background-color: surface-default }` from `native.css`, which lives in
  `@layer wa-native` — an unlayered rule wins). This covers overscroll areas.
- The full canvas look (auras over the color) is one token, `--canvas-background`:

  ```css
  --canvas-background:
      radial-gradient(55rem 38rem at -5% -10%, var(--canvas-aura-start), transparent 65%),
      radial-gradient(50rem 36rem at 105% 110%, var(--canvas-aura-end), transparent 65%),
      var(--canvas-color);
  ```

  It is declared once, on `:root`. Its `var()` references resolve on `html`, where `.wa-dark`
  also applies, so it picks the dark aura tokens with no redeclaration.
- The auras are painted by a fixed pseudo-element, `body::before`: `content: ""; position:
  fixed; inset: 0; z-index: -1; pointer-events: none; background: var(--canvas-background)`.
  A fixed layer is used instead of `background-attachment: fixed`, which repaints on every
  scroll of the home page.
- `.app-container` (`App.vue`) loses its `background: var(--wa-color-surface-default)` and
  becomes transparent.
- Full-page backdrops that today paint `surface-default` — `.connecting-backdrop`
  (`App.vue`) and `.login-backdrop` (`LoginView.vue`) — are `position: fixed` above
  everything, so they would hide `body::before`. They paint `background:
  var(--canvas-background)` themselves: the full canvas, auras included, and still opaque
  (the last layer is the solid color).
- `index.html`'s inline `html.loading` background (shown before the CSS loads) is left
  unchanged.

## 5. Commit 1 — canvas + one card

### 5.1 Sidebar (`views/ProjectView.vue`)

- `.sidebar`: `background: transparent` on desktop.
- Narrow drawer (`@media (width < 640px)`): the drawer overlays the content, so it must stay
  opaque. It gets `background: var(--canvas-color)` (solid, no aura) instead of
  `surface-default`. Its shadow and border stay as today.
- The project selector trigger keeps its explicit `surface-default` background: it reads as
  a control on the canvas.
- `.sidebar-header` / `.sidebar-footer-buttons` already use `--main-header-footer-bg-color`
  (transparent), so they show the canvas with no change.

### 5.2 Sidebar / content divider (`wa-split-panel.project-view`)

- The divider column becomes the gap: on `.project-view`, `--divider-width: var(--panel-gap)
  !important` (overrides the global `wa-split-panel { --divider-width: var(--divider-size)
  !important }` in `App.vue`; a class selector beats the type selector, both being
  `!important`).
- The existing scoped rule `wa-split-panel::part(divider) { background-color:
  var(--wa-color-surface-border); width: var(--divider-size); }` (`ProjectView.vue`) is
  **replaced**: the part keeps the full column width and draws its line as a centered
  background image, not as a fill:

  ```css
  .project-view::part(divider) {
      --line: transparent;
      background: linear-gradient(var(--line), var(--line)) center / var(--divider-size) 100% no-repeat;
  }
  .project-view::part(divider):hover { --line: color-mix(in oklab, var(--wa-color-brand-fill-loud) 50%, transparent); }
  .project-view.sidebar-resizing::part(divider) { --line: var(--wa-color-brand-fill-loud); }
  ```

  The line must not use `::after` on the part: the split panel's shadow CSS already uses
  `.divider::after` as its hit area. Opacity is carried by the color, not by `opacity`, so
  the slotted touch grip (`.divider-handle`, `pointer: coarse`) stays fully visible.
- Drag state: `wa-split-panel` exposes none. `ProjectView.vue`'s existing
  `handleSplitPanelPointerDown` (capture listener, already filters on the divider through
  `composedPath()`) adds the `sidebar-resizing` class to the split panel; a one-shot
  `pointerup` / `pointercancel` listener on `window` removes it.
- The collapsed-sidebar rule (`grid-template-columns: 0 var(--divider-width) auto`) keeps
  working: the gap stays on the left of the card when the sidebar is closed.

### 5.3 The content card (`.main-content`)

- `.main-content` gets the card look (`.panel-card` tokens) and an inset from the viewport:
  `margin-block: var(--panel-gap)`, `margin-inline-end: var(--panel-gap)`,
  `height: calc(100% - 2 * var(--panel-gap))`. The left side needs no margin: the divider
  column is the gap.
- It keeps `overflow: hidden`. With the radius, this clips everything inside to the rounded
  shape — including the pooled iframes, which are positioned in `FrameHost` inside
  `.main-content`. No iframe corner work is needed in commit 1.
- It keeps `position: relative`, `z-index: 1`, `container-type: inline-size` and the
  `--preview-expanded` rule. The card adds only `border`, `border-radius`, `box-shadow` and
  margins — none of which creates a containing block for `position: fixed`, so the
  fullscreen previews still escape.
- Below 640px the drawer layout makes `.project-view` a block, and the split panel's slots
  are `display: contents`: `.main-content` becomes the host's first in-flow child, so a top
  margin would collapse through `.project-view`, `.project-view-wrapper` and
  `.app-container`, push the `100dvh` wrapper down and make the page scroll. So on mobile the
  inset is **not** a margin: `.project-view` gets `padding: var(--panel-gap)` (with
  `box-sizing: border-box`, its `height: 100%` unchanged), and `.main-content` gets
  `margin: 0; height: 100%`. The narrow token values apply.

### 5.3.1 The sidebar reopen toggle

When the sidebar is closed, `label.sidebar-toggle` floats at the bottom-left of the
viewport — which would put it across the card's rounded bottom-left corner and border. Its
offset has two variants today (`ProjectView.vue`): `var(--wa-space-s)` by default, and
`var(--wa-space-xs)` under `@container sidebar (width <= 19rem)` when the footer carries the
peer inbox (`.sidebar-footer-buttons--with-inbox .sidebar-toggle`) — a rule that matches
whenever the sidebar is closed, since the container is then 0 wide.

- The two rules stop setting `bottom` / `left` directly. They set a variable instead,
  `--sidebar-toggle-offset` (`s` by default, `xs` in the inbox variant), and one rule places
  the toggle: `bottom` / `left: calc(var(--sidebar-toggle-offset) +
  var(--sidebar-toggle-shift, 0px))`.
- A new rule, **inside `@media (width >= 640px)`**, sets `--sidebar-toggle-shift:
  var(--panel-gap)` on the toggle, keyed on **the same fact that collapses the grid**:
  `.project-view-wrapper:has(.sidebar-toggle-checkbox:checked) .sidebar-toggle` (desktop:
  checked = closed, see the existing collapsed-grid rule). It must not key on
  `body.sidebar-closed`: that class is toggled on desktop and mobile alike, with inverted
  checkbox meanings, and only updated on toggle / navigation / drag — so it goes stale when
  the window crosses the 640px breakpoint.
- Result: in both variants the closed toggle keeps its current distance from the card's
  edges, as it had from the viewport's. The clearance variables
  (`--sidebar-toggle-clearance-*`, `App.vue` / `SessionLayout.vue`) are relative to the
  content, which moved by the same gap, so they are unchanged. The sidebar-open position and
  the mobile drawer position are unchanged.

### 5.4 Home and other screens

- Home (`HomeView.vue`) sits on `.app-container`, which is now transparent: its `wa-card`s
  float on the canvas with no change to the view.
- The artifacts browser and the project detail panel live inside `.main-content`, so they are
  inside the card.

### 5.5 What commit 1 does not touch

The session layout: regions, gutters, overlay and splitters keep their current borders and
backgrounds. The whole session (header included) is inside the one card.

## 6. Commit 2 — one card per region

### 6.1 The content area becomes transparent

- `.main-content` drops the card look (background, border, radius, shadow and the
  `.panel-card` class) and becomes transparent. The outer inset moves from **margins to
  padding**: `padding-block: var(--panel-gap)`, `padding-inline-end: var(--panel-gap)`,
  `height: 100%` again.
- `.main-content` changes from `overflow: hidden` to `overflow: clip` with
  `overflow-clip-margin: var(--panel-gap)`. Reasons:
  - the cards flush with `.main-content`'s left edge (left column, center without a left
    dock, classic bottom, project detail, artifacts browser, fallback card) sit exactly on
    that edge, next to the divider column. The clip margin lets their left shadow paint
    into the divider column (the gap), as the commit 1 card's does. The shadow budget (§4)
    keeps that shadow within the gap, so it ends before the sidebar with no visible edge;
  - `clip` is not a scroll container, unlike `hidden`: nothing can scroll `.main-content`
    sideways (the "clip, not hidden" rule `.session-layout`, `DockRegion` and `LayoutOverlay`
    already follow, see their comments).
- **One clip margin everywhere: the gap.** `.main-content`, `.session-view` (§6.2) and
  `.session-layout` (§6.3) all use `overflow-clip-margin: var(--panel-gap)`. The innermost
  one decides for the session cards; since all equal the gap and the horizontal shadow reach
  fits in it (§4, shadow budget), the side shadows are whole (on desktop; narrow-viewport
  residual per §4); the bottom shadow's cut lands
  on the viewport edge. Bounding the clip to the gap also
  bounds the session's real content (e.g. the gutters' invisible measurement mirrors that
  overflow the layout's right edge) within `.main-content`'s padding box.
- Without `overflow-clip-margin` support (Safari), the shadows of cards flush with
  `.main-content`'s left edge are cut there — accepted degradation (§6.3).
- Mobile (`width < 640px`): the commit 1 `.project-view` padding is removed and
  `.main-content` gets `padding: var(--panel-gap)` on all four sides instead (there is no
  divider column on mobile), for the same shadow reason.
- The three branches get their card individually:
  - `.project-detail-content` → one card (`.panel-card`, `overflow: clip`).
  - `.artifacts-browser-content` → one card (`.panel-card`, `overflow: clip`).
  - `.session-content` → no card itself; see below.
  `clip`, not `hidden`, for the same reason as above; with both axes clipped, the clip
  follows the rounded corners.
- `FrameHost` stays in `.main-content` (`position: absolute; inset: 0` resolves against the
  padding box; frames are placed by measured rects, so padding is absorbed).
- `.frame-host` gets `overflow: clip` (`FrameHost.vue`). Reason: a frame cell is sized to
  its placeholder and trimmed only visually by `clip-path`; in a Browser pane's responsive
  mode the stage has an exact size and the cell can extend far past the viewport. Today
  `.main-content`'s `overflow: hidden` (a scroll container) swallows that; with
  `overflow: clip` plus a clip margin (above), the part inside the margin could become page
  scrollable overflow. `.frame-host` is `inset: 0`, so it clips at `.main-content`'s padding
  box. The fullscreen cells (`position: fixed`) escape it: it is not their containing block.

### 6.2 Session view (`views/SessionView.vue`)

- `SessionHeader` sits on the canvas. It already paints `--main-header-footer-bg-color`
  (transparent); its compact overlays keep their own `surface-default` background.
- The header's own separators would draw a hairline on the canvas just above the cards; the
  gap now does that job, so both are made **invisible without removing the space they
  take** (the trailing divider carries the header's only bottom spacing in normal mode —
  the flex `gap` before it; `display: none` would glue the last row to the cards):
  - the trailing `<wa-divider>` (`SessionHeader.vue`, end of the header template):
    `visibility: hidden`;
  - the `border-bottom` of `.session-header.compact-collapsed` (`@media (max-height: 900px)`
    block of `SessionHeader.vue`): `border-bottom-color: transparent`.
  Both stay as they are when `SessionHeader` renders elsewhere (it has a `mode` prop; only
  `mode="session"` inside `SessionView` is concerned). The implementation scopes the rule to
  `.session-view > .session-header` from `SessionView.vue` (`:deep` as needed) rather than
  changing `SessionHeader.vue` for every mode.
- `.session-view` itself changes from `overflow: hidden` to `overflow: clip` with
  `overflow-clip-margin: var(--panel-gap)`: its right and bottom edges coincide with the
  layout's, and `overflow: hidden` would clip the outer cards' shadows in every browser. No
  code scrolls `.session-view` (it is a flex column whose children own their scrolling), so
  losing the scroll-container status is intended — the same reason `.session-layout` already
  uses `clip` (see its comment).
- The content of the session view:
  - with a layout (`SessionLayout`): cards per region (§6.3);
  - without a layout — a launched ephemeral session (`SessionItemsList` rendered directly
    under the header), or the "not found" / "failed" / "loading" states (rendered without
    any header: `SessionHeader` is `v-if="session"`) — that element gets the `.panel-card`
    class and fills the height left in `.session-view` (`flex: 1; min-height: 0`).
    - `.empty-state` also gets `overflow: clip`; `flex: 1` replaces its fixed
      `height: 200px`; its content stays centered.
    - `SessionItemsList`'s root keeps its existing `overflow: hidden`
      (`SessionItemsList.vue`, root rule): it is the list's pre-existing behaviour, not
      introduced here, and it clips to the rounded corners as well.

### 6.3 Region cards (`components/session/layout/*`)

**Which elements become cards**

| Element | File | Today | Commit 2 |
|---|---|---|---|
| `.center-slot` | `SessionLayout.vue` | no background (shows `.main-content`) | `.panel-card` |
| `.dock-region` | `DockRegion.vue` | `surface-default` + inner-edge borders only | `.panel-card` (full border replaces the inner-edge borders) |
| `.layout-overlay` | `LayoutOverlay.vue` | `surface-default`, inner-edge border, `--wa-shadow-l` | `.panel-card`, with a local `box-shadow: var(--wa-shadow-l)` override (keeps its stronger shadow) |
| `.dock-gutter` (rails) | `DockGutter.vue` | `surface-default` + center-facing border | transparent, no border: the rail icons sit on the canvas like the sidebar |

A maximized region (center or dock) and the tabs mode (layout ≤ 520px wide) are one card
filling the whole layout area — they have no inner edges, so no gap applies.

**The gap between regions — render-time inset, no resolver change**

The resolver returns edge-to-edge rects. Each card is inset by half the gap **on its inner
edges only** (edges that touch another region or a rail), so two neighbours are separated by
one full gap and the outer edges stay flush with the layout box.

- A new pure helper, `frontend/src/utils/panelInsets.js`:
  `innerEdges(rect, viewport) → { left, top, right, bottom }` (booleans). An edge is inner
  when it is not on the layout boundary: `x > ε`, `y > ε`, `x + w < W − ε`, `y + h < H − ε`
  (ε = 0.5px, the resolver works in fractional px).
- `SessionLayout.vue` computes the flags for the center rect, passes them to `DockRegion`
  (new prop, e.g. `insets`) for each dock rect, and to `LayoutOverlay` for the overlay rect
  (`overlay.rect`). The overlay rect starts at `railLeft` and stops at the top of the bottom
  rail (`layoutResolver.js`, `overlayRect`), so with a rail shown it would otherwise sit flush
  against the rail's icons; with the flags it gets the same half-gap as any region. All flags
  are computed against the same `render.viewport`.
- Rect styles become `calc()` expressions using a CSS variable, so the gap stays in `rem` and
  follows the font-size setting with no JS measurement:

  ```js
  left:   `calc(${x}px + var(--panel-half-gap) * ${l})`,
  top:    `calc(${y}px + var(--panel-half-gap) * ${t})`,
  width:  `calc(${w}px - var(--panel-half-gap) * ${l + r})`,
  height: `calc(${h}px - var(--panel-half-gap) * ${t + b})`,
  ```

  with `--panel-half-gap: calc(var(--panel-gap) / 2)` in `surfaces.css` and `l`, `t`, `r`,
  `b` = 0 or 1.
- Splitters (`.layout-splitter`) keep their resolver positions: they sit exactly on the
  shared boundary, which is now the middle of the gap. Their hover/drag line appears in the
  gap. No change to their code.
- The resolver's `railW` (30px) and the gutter rects are unchanged; the neighbouring card
  insets itself by half a gap away from a rail like from any other region.
- Tab-drag drop zones (`layoutDropZones`) keep using the raw resolver rects. They are off by
  at most half a gap from the visible cards — accepted (the zones are large).

**Shadows at the layout's outer edges**

Two ancestors clip at the layout's outer boundary: `.session-layout` (`overflow: clip`) and
`.session-view` (changed to `overflow: clip` in §6.2). Both get
`overflow-clip-margin: var(--panel-gap)`, like `.main-content` (§6.1, "one clip margin
everywhere"). The outer cards' shadows paint into the gap around the layout — into
`.main-content`'s padding on the right and bottom, into the divider column on the left (the
top edge faces the session header, and the shadow's upward reach is negligible, about 1px).
The shadow budget
(§4) keeps the side shadows within the gap (on desktop; narrow-viewport residual per §4); the bottom cut lands on the viewport edge. The
margin is deliberately only the
gap: it also bounds the session's real content (e.g. the gutters' invisible measurement
mirrors that overflow the layout's right edge) inside `.main-content`'s padding box.
Browsers without `overflow-clip-margin` (Safari at the time of writing) clip those outer
shadows — accepted degradation, the cards and gaps still show.

**The overlay backdrop**

`.overlay-backdrop` (`LayoutOverlay.vue`) is a square `inset: 0` dimming layer over the whole
layout. In commit 2 it also darkens the gaps and the rails: the rails (`z-index: 12`, above
the backdrop's 8) used to hide it behind their opaque `surface-default`; now transparent,
they let it show through, so the rail area is dimmed while its chips stay crisp. Intended:
everything in the layout but the overlay is dimmed, the rails included (the session header
and the canvas outside the layout stay undimmed, as today). It gets `border-radius: var(--panel-radius)`
so its outer corners follow the outer cards' corners instead of showing square dark corners
on the canvas. The overlay's inner-edge border rules (`.layout-overlay.left`, `.right`,
`.bottom`) are removed, like `DockRegion`'s: `.panel-card` draws the full border.

**Borders and inner chrome**

- `DockRegion`'s inner-edge border rules (`.col-left`, `.col-right`, `.bottom`,
  `[data-rid="left-bottom"]`, `[data-rid="right-bottom"]`, `[data-rid="bottom-right"]`) are
  removed: every card has a full border.
- `overflow: clip` on `.dock-region` (and `overflow: hidden` on `.center-slot`) clips at the
  padding box, rounded with the inner radius (`--panel-radius` minus the border width), so
  the tab bar and body follow the corners.
- Controls that sit flush in a top corner get their corner clipped by that radius: the
  first tab of each bar (top-left), `.layout-nav-cluster` (`top: 0; inset-inline-end: 0` in
  `.center-slot`) and the last `.dock-controls` button (top-right). Their hover fill and
  focus ring must stay whole. Rule: a new token `--panel-corner-inset:
  calc(var(--panel-radius) / 3)` (in `surfaces.css`) insets the top bars inline:
  - `.dock-topbar` (`DockRegion.vue`) and `.overlay-topbar` (`LayoutOverlay.vue`):
    `padding-inline: var(--panel-corner-inset)`;
  - center (`SessionView.vue`): `.session-tabs::part(nav)` gets
    `margin-inline-start: var(--panel-corner-inset)` — a margin, not a padding: when the tabs
    overflow, Web Awesome sets its own `padding: 0 1.5em` on that element (the `nav` part is
    its `.nav-container`) and places the start chevron at `inset-inline-start: 0` inside it;
    an outer padding would override the 1.5em and put the chevron over the first tab. A
    margin moves the whole container, chevron included (same approach as the existing end
    reservation). `.layout-nav-cluster` gets
    `inset-inline-end: var(--panel-corner-inset)`; the existing reservation
    `.session-tabs::part(nav) { margin-inline-end: var(--layout-nav-cluster-w, 0px) }`
    becomes `calc(var(--layout-nav-cluster-w, 0px) + var(--panel-corner-inset))`, because
    `measureCenterNav()` measures the cluster's own width only.
  Side effect, accepted: the tab track line (the tab group's track + the `border-bottom` of
  `.dock-controls` / `.overlay-controls` / `.layout-nav-cluster`) now stops at the inset on
  both ends instead of touching the card's border. Checked manually per §8; the divisor is
  tuned if a corner is still cut.
- `.dock-controls`, `.layout-nav-cluster` and the overlay controls keep their bottom border
  (it continues the tab track inside the card).
- The existing rules keyed on the region (`isolation: isolate`, the
  `:has(.file-pane-preview--fullscreen)` escape, the sidebar-toggle clearance variables in
  `SessionLayout.vue`) are unchanged.

### 6.4 Rounded corners on pooled iframes

Pooled iframes are not DOM descendants of the regions (they live in `FrameHost`), so a
card's radius does not clip them. When an iframe reaches a corner of its card (e.g. the
bottom corners of a Browser pane or of an HTML preview), its square corner would stick out
over the rounded edge.

- `PersistentFrame.vue` finds the card that contains its placeholder:
  `placeholder.closest('.panel-card')` (every card carries the class, §4 — center slot, dock
  regions, layout overlay, project detail, artifacts browser, session fallback card).
- The card element is **re-resolved**, not cached for the component's life: a pane moved to
  another dock keeps its `PersistentFrame` instance (its panel is Teleported, never
  remounted), but its placeholder now sits in a different card. The card lives in a
  `shallowRef`; `closest()` re-runs in `onMounted`, in `onActivated`, in the existing
  `geometryEpoch` watcher (`SessionView.vue` bumps the epoch on every layout render, overlay
  and maximize change), **and in the existing placeholder-rect watcher**
  (`[bounding.x, bounding.y, bounding.width, bounding.height]`). The last one is the
  reliable trigger: on a KeepAlive return the dock regions are recreated and the panel is
  Teleported into them only after `onActivated` (while docking is not rendered yet,
  `toolTarget()` resolves to the center targets), and the epoch bump may land before the
  Teleport — but the placeholder's rect always changes when it lands in a new card.
  `closest()` is cheap. After each re-resolution the card's bounding is updated
  (`useElementBounding` on the `shallowRef`, then `update()`).
- It stores only the card's **rect** in the pool entry (new field, e.g. `cardRect`; `null`
  when no card is found). No computed style is read in JS: the radius and border width vary
  with the theme, the breakpoint and the font size, and a JS copy would go stale.
- `FrameHost.vue` decides, per corner, whether the frame is flush with the card, and builds
  one `clip-path: inset(t r b l round tl tr br bl)` combining the existing `clipRect`
  insets with the corner radii. A flush corner gets `var(--panel-inner-radius)`, a new token
  in `surfaces.css` (`calc(var(--panel-radius) - var(--divider-size))`, the radius of the
  card's padding box); other corners get `0`. The value is a CSS expression in the inline
  style, resolved by the browser from the inherited tokens, so theme, breakpoint and
  font-size changes apply with no JS.
- "Flush" is decided on the frame's **visible** rect (the frame rect intersected with
  `clipRect` when there is one): a visible corner is flush when it lies within
  `FLUSH_TOLERANCE_PX = 6` of the card's matching corner on both axes — the largest card
  border (4px, awesome theme) plus rounding. A pane with its own padding under 6px would
  also get a rounded frame corner (with the card's inner radius, slightly larger than a
  truly concentric one would be); accepted.
- The decision is a pure function in `utils/panelInsets.js`
  (`frameFlushCorners(visibleRect, cardRect) → { tl, tr, br, bl }`), unit-tested. It re-runs
  whenever the frame rect, `clipRect` or `cardRect` change (all reactive).
- Fullscreen frames (`zTier === 'fullscreen'`) never get a card radius.
- In commit 1 there is nothing to do here: `.main-content` itself clips the frames.

## 7. Invariants (both commits)

- No `transform`, `filter`, `backdrop-filter`, `contain: paint|layout`, `will-change` or
  `container-type` is added to `.main-content`'s content branches, `.session-layout`,
  `.center-slot`, `.dock-region` or `.layout-overlay`: any of them would trap the
  `position: fixed` fullscreen previews (`FilePane.vue`, `BrowserPane.vue`).
- Commit 2 changes `.session-view` and `.main-content` to `overflow: clip`, adds
  `overflow-clip-margin` to them and to `.session-layout`, and adds `overflow: clip` to
  `.frame-host`, `.project-detail-content`, `.artifacts-browser-content` and `.empty-state`:
  none of these creates a containing
  block for fixed elements (a fixed preview escapes a `clip` ancestor exactly as it escapes a
  `hidden` one).
- No iframe is moved in the DOM (no new wrapper around `FrameHost` cells, no Teleport).
- Layout sizes (gap, radius, insets) are in `rem` or Web Awesome tokens, so they follow the
  font-size setting. Px remain only where they are already px or are sub-pixel tolerances:
  the resolver rects, `--divider-size`, the shadow offsets/blurs, the ε = 0.5px edge
  tolerance and the 6px flush-corner tolerance (`FLUSH_TOLERANCE_PX`, §6.4).
- `getSurfaceColor()` keeps returning `surface-default`: panel surfaces do not change.

## 8. Testing

- Unit tests (`node:test`, `frontend/src/utils/panelInsets.test.js`), feeding real
  `resolveLayout()` outputs where possible:
  - `innerEdges`: each region kind in widescreen and classic, split and merged siblings,
    rails on each edge, the overlay rect with and without rails, maximized, tabs mode;
  - `frameFlushCorners`: visible rect flush with 0, 1, 2 and 4 corners; inset from the card
    by less and by more than the tolerance; frame partly scrolled out via `clipRect` (the
    clipped corner is judged on the visible rect); card border of 1px and 4px.
- `frontend/src/utils/layoutResolver.test.js` must stay green (the resolver is not modified).
- Manual check in the worktree's own dev instance (`devctl.py start`, existing DB copied),
  after each commit, in light and dark, with two accents, font sizes 12 (the size the shadow
  budget is sized for), 14 and 18, and the three
  themes (`default`, `awesome` with its 4px dividers and larger radius, `shoelace`):
  - session view with left, right-top, right-bottom and bottom docks; a maximized dock; a
    maximized center; a side overlay, including one opened while a rail shows (the rail is
    dimmed with the rest, its chips stay clickable); rails on each
    edge; tabs mode (narrow window);
  - resize every splitter and the sidebar divider (line visible on hover and while dragging);
    collapse and reopen the sidebar; reopen toggle clear of the card's corner, with and
    without the peer inbox configured; close the drawer on a narrow window then widen it
    past 640px (and the reverse): the toggle is where it belongs;
  - top-corner controls (first tab, nav cluster, dock window buttons): hover fill and focus
    ring not cut;
  - Browser pane and an HTML artifact preview docked in a corner region (rounded corners);
    move that tab to a non-corner region and back (corners follow); switch to another
    session and back (KeepAlive return: corners still right); open it in a side overlay;
    then fullscreen preview (must cover the whole window);
  - Browser pane in responsive mode with a stage larger than the pane, in a right and in a
    bottom dock: no page scrollbar, also while another preview is fullscreen;
  - terminal docked (background still matches the card);
  - no sideways shift of the layout, in Firefox: open the Artifacts tab in a side overlay
    with its file list open and a file selected, close it, reopen it (the case fixed by
    commit `63d0d7c5`), and resize the
    window;
  - shadows (commit 2): every card's shadow whole on every side, including cards flush with
    the sidebar side; no shadow visibly spilling onto a neighbour card (right column next
    to the center, docks stacked vertically); the overlay backdrop's corners follow the
    cards';
  - session header on the canvas: no hairline above the cards, in normal and compact
    (`max-height: 900px`) modes; ephemeral session; session "not found" / loading states;
  - HTML artifact preview in the artifacts browser and in the project detail Files tab: the
    frame's corners follow the card (commit 2);
  - project detail (stats, files, git, terminal), artifacts browser, Home, login screen,
    "Connecting to server..." overlay (backend unreachable at load);
  - narrow viewport (< 640px): the page does not scroll, drawer opaque, tighter gaps, cards
    inset on all four sides, shadows not visibly cut.

## 9. Risks

| Risk | Mitigation |
|---|---|
| A panel element painted `surface-default` to blend into `.main-content`, and now sits on the canvas | Commit 1 keeps `.main-content` opaque, so nothing changes there. Commit 2: two things move onto the canvas — the session header (background already transparent, separators hidden, §6.2) and the dock rails (`.dock-gutter`, made transparent on purpose, §6.3). Manual pass per §8. |
| Canvas too close to the panel color in one theme/scheme | Values tuned by eye before commit 1 (§4), in the three themes. |
| Shadows clipped at outer edges on Safari | Accepted (§6.3). |
| Reopen toggle or clearance slightly off because content is inset by the gap | Toggle offset by the gap (§5.3.1); manual check. |
| Top-corner controls clipped by the radius | Corner inset token (§6.3); manual check. |
