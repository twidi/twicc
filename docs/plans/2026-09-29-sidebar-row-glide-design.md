# Gliding open-row fill in the sidebar lists — design (visual refresh, step 6a-bis)

## 1. Context

Step 4c (`docs/plans/2026-09-28-gliding-indicators-design.md`) made the marker of the
current item glide from item to item in tab bars, the settings section menu, pickers and
segmented controls, through one mechanism (`utils/glideInk.js`, `composables/useGlideInk.js`,
the `.glide-ink` rules of `styles/motion.css`). It left out the sidebar's session and
artifact lists ("virtual-scroll rows, not in the user's selection", 4c §3). Step 6a
(`docs/plans/2026-09-29-accent-glow-design.md` §7, §17) lit the open row of both lists
from one shared stylesheet, `styles/sidebar-rows.css`.

Read first: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox
parity**, **reduced motion is reduced, not none**), §6f (4c lessons), §6j (6a lessons).

### 1.1 User decision — do not reopen (2026-09-29)

The sidebar list is where the user changes session most often. The open row's lit look
(fill, ring, shadow) must **glide** from the previously open row to the newly opened one,
like the chosen section's fill in the settings menu. Same in the artifacts list (the two
lists are one component for the user, 6a §17.1).

## 2. Today (checked)

- **The ink mechanism.** `createGlideInk({ container, target, flushTarget, flushPseudo,
  getActive, getItems, getResetKey, env })` (`utils/glideInk.js:84-95`) measures
  `getActive()`'s box relative to `container` (scroll offsets included, so the ink lives
  in content coordinates and scrolls with the rows), writes `--glide-x/y/w/h` on
  `target`, and sets `data-glide-ready` / `data-glide-instant` there. It **glides** when
  the active element changes and the ink is shown with the same reset key, **snaps** on a
  first placement, a reset-key change or the same element moving, and **hides** (fade,
  then a zero box) when nothing is active or visible (`placementMode`, `:54-59`; `update`,
  `:166-240`). It watches the active element, the container and `getItems()` with a
  `ResizeObserver`. `useGlideInk({ container, target, flushTarget, getActive, getItems,
  sources, resetKey })` (`composables/useGlideInk.js`) creates it when the container
  element appears and calls `update()` after Vue's patch (`flush: 'post'`) when `sources`
  change.
- **The ink element** (`styles/motion.css:154-176`): `.glide-ink` is absolutely placed at
  the container's padding-box origin, sized and translated by the four properties, with
  `background: var(--glide-ink-bg, transparent)`, `border-radius:
  var(--glide-ink-radius, 0)`, `opacity: 0` until `[data-glide-ready] > .glide-ink`
  (then `transition: var(--glide-transition)`: translate, width and height with the
  spring, 380ms), no transition under `[data-glide-instant]`. It must be the target's
  **direct child**, and the first child: the rows are positioned, so they paint above it
  in tree order. Reduced motion sets `--glide-transition: none` (`motion.css:66-73`).
- **Existing list ink, for reference:** the settings section menu (`SettingsPopover.vue`,
  `useGlideInk` at `:137-146`, ink `span.glide-ink.settings-nav-ink` at `:1279`,
  `--glide-ink-bg` a gradient since 6a).
- **Session list.** `SessionList.vue:639-670`: a `VirtualScroller` (`:key="projectId"`,
  `class="session-list"`, `padding-block`) whose default slot renders, per item, an
  optional `SidebarListSeparator` and a `SessionListItem` with `:active="session.id ===
  sessionId"`. The scroller root `div.virtual-scroller` (`VirtualScroller.vue`, template)
  is the scroll container, `position: relative`; its children are a spacer, the rendered
  items (`VirtualScrollerItem`, in normal flow), a spacer and an anchor sentinel. Only the
  rows near the viewport are mounted. `getVisibleRange()` returns the reactive
  `visibleRange` computed (`useVirtualScroll.js:353`).
- **Artifacts list.** `ArtifactBookmarkList.vue`: `div.bookmark-list` (`ref="listRef"`,
  scroll container, flex column, not positioned) holding one `.bookmark-entry` per
  bookmark (separator + row wrapper); every row is mounted.
- **The rows.** Both lists put `sidebar-row` / `sidebar-row--active` on the row
  `wa-button` and `sidebar-row-wrapper` (`position: relative`) on its wrapper. The lit look
  is drawn on the button's `base` part by `styles/sidebar-rows.css` (6a §17.3): fill
  (`background-image` gradient, `background-origin: border-box`), ring (the 1px
  `border-color`), shadow (`box-shadow`), text colour; dark variants; the open and
  multi-selected row adds a 1px inset ring and a solid `brand-60` border.
- **Section labels.** `SidebarListSeparator.vue`: not positioned.

## 3. Goals

1. Opening another session (or artifact) from its list makes the lit fill, ring and
   shadow glide from the previous open row to the new one (the spring of the other
   indicators), in both lists.
2. The row's text colour, the multi-select marks and the keyboard highlight stay on the
   rows.
3. No wrong glide: a first display, a project / scope change, a live reorder of the open
   row, or an open row that was off the rendered range all place the fill at once.
4. Same result in Firefox and Chrome; reduced motion places the fill at once (no glide),
   as for the other indicators.

## 4. Design

### 4.1 The ink in each list

- **`VirtualScroller.vue`**: a new named slot `before`, rendered as the **first child**
  of the scroller root, before the first spacer. No other change. (A slot, not a prop: the
  scroller stays generic.)
- **`SessionList.vue`**: `<template #before><span ref="inkRef" class="glide-ink"
  aria-hidden="true"></span></template>` in the `VirtualScroller`; the scroller root gets
  the class `sidebar-row-list` next to `session-list`.
- **`ArtifactBookmarkList.vue`**: `<span ref="inkRef" class="glide-ink"
  aria-hidden="true"></span>` as the first child of `div.bookmark-list`, which gets the
  class `sidebar-row-list`. `.bookmark-list` becomes `position: relative` (the ink's
  containing block). The ink is absolutely positioned: it is not a flex item and does not
  shift the entries. Its DOM queries (`scrollRowIntoView`, `visibleEntryRange`) select
  `.bookmark-entry` children only (6a §17.4): the extra child is ignored.

### 4.2 The controller — `useGlideInk` in both lists

```js
// SessionList.vue
const scrollerEl = computed(() => scrollerRef.value?.$el ?? null)
useGlideInk({
    container: scrollerEl,
    flushTarget: inkRef,
    getActive: () => activeRowBase(scrollerEl.value),
    getActiveOffset: () => entranceOffset(scrollerEl.value),
    getItems: () => [...(scrollerEl.value?.querySelectorAll('.sidebar-row') ?? [])],
    sources: [() => props.sessionId, sessions, () => props.compactView,
              () => scrollerRef.value?.getVisibleRange()],
    resetKey: () => props.projectId,
})
// ArtifactBookmarkList.vue
useGlideInk({
    container: listRef,
    flushTarget: inkRef,
    getActive: () => activeRowBase(listRef.value),
    getActiveOffset: () => entranceOffset(listRef.value),
    getItems: () => [...(listRef.value?.querySelectorAll(':scope > .bookmark-entry') ?? [])],
    sources: [() => props.activeBookmarkId, list, () => props.compactView],
    resetKey: () => (props.showAllArtifacts ? 'all' : (props.effectiveProjectId ?? '')),
})
```

- **`activeRowBase(root)`**, a small shared helper (new, in `utils/sidebarRows.js`):
  `root?.querySelector('.sidebar-row--active')?.shadowRoot?.querySelector('[part~="base"]') ?? null`.
  The ink takes the box of the button's `base` part, not of the host: the part carries a
  `margin-bottom` (`sidebar-rows.css`), so the host is taller than the lit box.
- **`getItems`** (roadmap §6f.2: observe all items). A row created in the patch that the
  post-flush update follows has no `base` part yet: Lit renders the button's shadow tree
  one microtask after `connectedCallback`, so `activeRowBase()` returns `null` and the
  update hides the ink (a new open row: "New session", a scope change that remounts every
  artifact row, a row mounted by the virtual scroller). The `ResizeObserver` then sees the
  row host (sessions: the mounted `.sidebar-row` hosts) or the entry (artifacts) grow when
  the button renders, and its callback re-places the ink. It also catches a row above the
  open one changing height (the artifacts list has no other source for it).
- **Entrances: measure the final box.** A row that enters (`.list-entering`, the 5c
  cascade and live entrance, `motion.css:281-293`) is translated by up to `0.375rem`
  while its animation runs (`translate` on the scroller item or the `.bookmark-entry`),
  and `getBoundingClientRect()` includes an ancestor's `translate`. Any update during it
  (a `sessions` change, a scroll, a resize, the route change of "New session") would
  measure the open row low and snap or glide there. So the controller subtracts the
  entrance offset before measuring:
  - `createGlideInk` and `useGlideInk` take a new optional option
    `getActiveOffset(active) → { x, y }` (layout px, default `{ x: 0, y: 0 }`); `update()`
    subtracts it from the box `relativeBox` returns (`box.x -= x; box.y -= y`: both in
    layout px, so a scaled container — `relativeBox`'s reason to exist — stays right);
  - **tolerance for the same element**: the corrected box of one element measured at two
    moments of its entrance differs by up to ~0.016px in Firefox (it rounds rects to
    1/60px; measured in Firefox and Chromium with a 0→6px animation), above `sameBox`'s
    0.01px (`glideInk.js:64-68`), and a same-element box change is a snap
    (`placementMode`) that would cut a running glide — and a second update during the
    entrance is certain (the `ResizeObserver` notifies a newly observed element once).
    So `update()` writes nothing when the active element is the one of the last placement
    and every value of the new box is within **0.05px** of `state.box` (a new helper
    `nearBox(a, b, 0.05)` next to `sameBox`). Placement in `update()`
    (`glideInk.js:167-238`), exactly:
    - `syncObserved(active)` (`:182`) runs first, as today (rows mounted by a scroll must
      still be observed);
    - the check sits after `mode` is computed, the settle override (`:216`) and the offset
      subtraction, and wraps **only the write block**:
      `if (!sameBox(box, state.box) && !(state.active === active && nearBox(box, state.box, 0.05))) { …snap / glide write…; state.box = box }`;
    - `state.ready`, `state.active` and `state.resetKey` are still updated every time (an
      early return would keep a stale reset key: a picker whose first item stays active
      across a query change would then snap its next move instead of gliding);
    - `state.box` is assigned **only when a write happened**: it is the last written box,
      so successive sub-0.05px measures compare against what is on screen and cannot
      creep away from it;
    - `startSettle` (`:237`) is unchanged.
    Effect on the other 4c consumers (tab bars, settings nav, pickers, segmented control,
    the settle loop's final correction): a same-element correction under 0.05px is dropped
    everywhere; the residual error is at most 0.05px, invisible. No existing
    `glideInk.test.js` expectation changes (its settle corrections are 0.3px or more, its
    same-element snap moves 5px);
  - both lists pass `getActiveOffset: () => entranceOffset(root)`, a second helper in
    `utils/sidebarRows.js`: the open row host's closest `.list-entering` ancestor
    (`root.querySelector('.sidebar-row--active')?.closest('.list-entering')`), its
    computed `translate` parsed as `{ x, y }` in px (`getComputedStyle(el).translate`:
    `none`, one or two lengths), or `{ x: 0, y: 0 }` when there is none.
  Every update then lands on the row's final box: no dip during an arrival, no cut glide
  during "New session". While the open row finishes its entrance, its text is up to
  0.375rem below the already-placed fill for the rest of the 320ms (§8). (During an
  arrival's `pending` phase, `.list-arriving` sets only `opacity: 0`, no translate.)
- The target is the container (default): `--glide-*` and `data-glide-ready` sit on the
  scroller root / `.bookmark-list`, the ink is its direct child.
- **Sources.** The open id (a click, a route change), the list (a live reorder moves the
  open row: same element, new box → snap), compact mode (row heights change; the
  `ResizeObserver` on the active part also catches it). For sessions, the visible range:
  the open row is unmounted when it leaves the rendered range (`getActive()` → `null` →
  hide) and mounted again when the user scrolls back (→ a new element: the ink is hidden,
  so the placement is a snap, not a glide). `getVisibleRange()` reads a computed, so the
  watch tracks it.
- **Reset key.** A project change remounts the scroller (`:key="projectId"`): the
  container changes and `useGlideInk` recreates the controller; the key also covers it.
  For artifacts, the scope key of the cascade (6a §17.4): a new list places the fill at
  once.
- **What the user sees**, from `placementMode`:
  - click on another visible row → glide (380ms spring);
  - the previously open row was unmounted by the virtual scroller (sessions: more than
    the unload buffer, `SCROLLER_BUFFER * 1.5` = 450px, past the viewport,
    `SessionList.vue:649`) → its element is gone, the ink was hidden → snap on the new
    row; the same when the new row is revealed by the list's own scroll-to-selected;
  - the previously open row is off screen but still mounted (sessions within the unload
    buffer; any artifact row, all mounted) → the ink glides in from outside the viewport,
    by design (the same element rule as any glide);
  - "New session": `ProjectView.vue:1153-1174` creates the draft first, then navigates;
    the row mounts (not yet open) and renders its button before the route resolves, so
    the ink glides from the previous row to the new one's final box (its entrance offset
    subtracted, above), and a later update during the entrance does not cut it. The "hide
    until rendered, then snap" path of `getItems` is the fallback for a row created and
    opened in the same patch;
  - first display, project / scope change → snap;
  - the open row moves (live reorder, a new row above it) → snap to its new place.

### 4.3 CSS — `styles/sidebar-rows.css` (after the lit-row rules)

```css
/* Lists with a gliding ink (step 6a-bis): the open row's fill, ring and shadow move to
   the ink, which glides from row to row (motion.css .glide-ink). */
.sidebar-row-list {
    --glide-ink-bg: linear-gradient(100deg, var(--wa-color-brand-fill-normal), color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, transparent));
    --glide-ink-radius: var(--wa-form-control-border-radius);
}
html.wa-dark .sidebar-row-list {
    --glide-ink-bg: linear-gradient(100deg, oklch(from var(--wa-color-brand-60) 0.32 0.07 h), oklch(from var(--wa-color-brand-60) 0.25 0.05 h / 0.6));
}
.sidebar-row-list > .glide-ink {
    box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent),
        0 2px 10px -4px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent);
}
html.wa-dark .sidebar-row-list > .glide-ink {
    box-shadow: inset 0 0 0 1px oklch(from var(--wa-color-brand-60) 0.6 0.11 h / 0.45),
        0 2px 12px -4px oklch(from var(--wa-color-brand-60) 0.6 0.13 h / 0.5);
}
/* Once the ink is placed, the open row draws no fill, ring or shadow of its own (its text
   colour stays). Its background stays transparent on hover too: Web Awesome's outlined
   hover mix would cover the ink. */
.sidebar-row-list[data-glide-ready] .sidebar-row--active::part(base) {
    background-color: transparent;
    background-image: none;
    border-color: transparent;
    box-shadow: none;
}
/* While an arrival keeps every row hidden (.list-arriving, 5c: two frames, up to 300ms
   when the list first scrolls to an off-screen open row), the ink hides too: it is not a
   row, and would show alone on a blank list. It shows at once when the cascade starts
   (the ready transition list has no opacity). */
.sidebar-row-list:has(> .list-arriving) > .glide-ink {
    opacity: 0;
}
/* Open and multi-selected: the lit row used to show its gradient over the multi-select
   fill (brand-fill-quiet); the row's background is now transparent, so the ink carries
   that base under its gradient. */
.sidebar-row-list:has(.sidebar-row-wrapper--selected .sidebar-row--active) > .glide-ink {
    background: var(--glide-ink-bg), var(--wa-color-brand-fill-quiet);
}
/* Open and multi-selected: the selection ring stays on the row, over the ink. */
.sidebar-row-list[data-glide-ready] .sidebar-row-wrapper--selected .sidebar-row--active::part(base) {
    border-color: var(--wa-color-brand-60);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-60);
}
```

- **Same look as today.** The ink's gradient, ring and shadow are the values of the lit
  row (6a §7, `sidebar-rows.css`), light and dark. The ring was the row's 1px border; on
  the ink it is a 1px inset shadow at the ink's edge, and the ink's box is the part's
  border box: the ring lands on the same pixels. The radius is the button's
  (`--wa-form-control-border-radius`, Web Awesome's `.button` radius).
- **No double marker on a hand-off.** Buttons transition `border` and `box-shadow` over
  120ms (`motion.css:89-96`, `:where(wa-button)::part(base)`): without a rule, the row's
  own ring and shadow would fade out while the ink is already fully shown (and back when
  the ink hides), breaking 4c's rule that the ink appears in the frame the item's own
  marker goes (`motion.css:168-169`). So, in `sidebar-rows.css`:
  ```css
  /* In a list with an ink, the open row hands its marker over at once. */
  .sidebar-row-list .sidebar-row--active::part(base) {
      transition-property: color;
  }
  ```
  ((0,2,1): above the `:where()` rule. Hover has no effect on the open row there.)
- **Before the ink is placed** (no `data-glide-ready`: the first frames, a hidden list,
  a list without an ink such as the peer message review dialog's picker, which renders
  `SessionListItem` outside a `.sidebar-row-list`), the row keeps its own lit look (the
  6a rules, unchanged). No frame without a marker.
- **Specificity.** `.sidebar-row-list[data-glide-ready] .sidebar-row--active::part(base)`
  is (0,3,1): above the lit rules (0,1,1), (0,2,1) and the dark one (0,2,2). The
  open-and-selected one is (0,4,1): above the dark open-and-selected (0,3,2). The text
  `color` of the lit rules is not overridden.
- **Section labels over the ink.** `SidebarListSeparator.vue`'s root gets `position:
  relative` (no other change): a glide that crosses a section label passes under it, like
  under the rows (a non-positioned label would paint below the positioned ink).
- The arrival rule: `.sidebar-row-list:has(> .list-arriving) > .glide-ink` is (0,3,0),
  above `motion.css`'s `[data-glide-ready] > .glide-ink` (0,2,0). The rows that carry
  `.list-arriving` are direct children of the container in both lists (the scroller's
  items, the `.bookmark-entry` elements, `useListCascade.js:171-174`).
- `:has()` ships in Firefox (121+) and Chrome. The `background` shorthand with a colour as
  its last layer beats `motion.css`'s `.glide-ink { background: var(--glide-ink-bg,
  transparent) }` ((0,3,0)+ against (0,1,0)).
- The `.glide-ink` rules of `motion.css` are reused unchanged (opacity, transitions,
  reduced motion).

## 5. Invariants

- The open row is always marked: by its own lit look until the ink is placed, by the
  ink afterwards.
- Glide only between two rows of the same list, both mounted, ink shown; every other case
  snaps or hides (4c's rules).
- The rows' text colour, the multi-select fill of other rows, the open-and-selected ring,
  the keyboard highlight and the row menu are unchanged.
- The peer message review dialog's picker keeps the 6a look (no ink there).
- Firefox parity: nothing new beyond 4c's mechanism and 6a's colours. Reduced motion: no
  glide (4c's `--glide-transition: none`).

## 6. Tests (node:test, `npm test`)

- `styles/sidebar-rows.test.js`: the `.sidebar-row-list` variables (light, dark), the ink
  `box-shadow` (light, dark) with the lit row's values, the two `[data-glide-ready]`
  hand-off rules with their exact declarations, placed after the lit rules, and the
  `.sidebar-row-list .sidebar-row--active::part(base) { transition-property: color }`
  rule, the `.sidebar-row-list:has(> .list-arriving) > .glide-ink { opacity: 0 }` rule,
  and the `:has(.sidebar-row-wrapper--selected .sidebar-row--active) > .glide-ink`
  rule with `background: var(--glide-ink-bg), var(--wa-color-brand-fill-quiet)`.
- `utils/glideInk.test.js`: with `getActiveOffset` returning `{ x: 0, y: 6 }`, the
  written box is 6px higher than the unoffset one; without the option, unchanged; the
  same element re-measured with a box 0.016px away writes nothing (no
  `data-glide-instant`, no property write), while 0.1px away still places it; `nearBox`
  unit cases;
  - key change: same element, same box, a new reset key → nothing written; then another
    element under that key glides (no `data-glide-instant`, no flush);
  - no creep: the same element measured at +0.03, +0.06 and +0.09px → the first writes
    nothing, the second (0.06 from the written box) snaps, the third (0.03 from the new
    written box) writes nothing.
  `useGlideInk` passes the option through (source scan or its test).
- New `utils/sidebarRows.test.js`: `activeRowBase` returns the `base` part of the
  `.sidebar-row--active` host under the root, `null` without a root, an active row or a
  shadow root; `entranceOffset` parses `translate` (`none` → 0, `0px 3.5px` → y 3.5,
  one value → x only) from the open row's `.list-entering` ancestor, `{0,0}` without one
  (fake elements and a fake `getComputedStyle`).
- Source scans (`styles/glide.test.js`, next to the existing site checks):
  - `VirtualScroller.vue` renders `<slot name="before" />` before the first spacer;
  - `SessionList.vue`: the `#before` template holds `span.glide-ink` with `ref="inkRef"`;
    the scroller has `sidebar-row-list`; `useGlideInk` with `getActive` through
    `activeRowBase`, `getItems` on the `.sidebar-row` hosts, the four sources and
    `resetKey: () => props.projectId`, `getActiveOffset` through `entranceOffset`;
  - `ArtifactBookmarkList.vue`: `span.glide-ink` first child of `.bookmark-list`
    (`sidebar-row-list`), `useGlideInk` with `getItems` on the `.bookmark-entry`
    children, its three sources, the scope reset key and `getActiveOffset` through
    `entranceOffset`; `.bookmark-list` is `position: relative`;
  - `SidebarListSeparator.vue`'s root rule sets `position: relative`.
- Existing suites stay green; an assertion that pins a changed value is updated and named
  in the report.

## 7. Browser checks (worktree instance http://localhost:5174, Firefox first, light and dark)

1. Session list: click another visible session → the lit fill glides to it (and resizes
   between a two-line and a one-line row); text colours are right during and after;
   the fill passes under a section label ("Pinned", "Last 24 hours") without hiding it.
2. Open a session far down, scroll the list well past it (more than ~450px beyond the
   screen, so the virtual scroller unmounts it), open one at the top → the fill appears
   on it without gliding. Scroll only just past it (row still mounted) → the fill glides
   in from the edge, by design.
2b. "New session" → the fill glides to the new draft row and ends aligned with it once
   its entrance ends (no offset left). During a project switch or reload arrival, the
   fill does not dip while the other rows end their entrance.
3. A live reorder (the open session gets activity and moves to the top) → the fill
   follows at once.
4. Project switch, workspace switch, reload → the fill is on the open row at once, with
   the list's cascade; no lit box alone on the blank list before the rows start to
   appear.
5. Multi-select mode: selected rows keep their fill; the open and selected row shows the
   thicker ring over the lit ink. Compact mode. Keyboard highlight.
6. Artifacts list: the same checks (1, 4, compact).
7. The peer message review dialog's picker: the chosen session is lit as before.
8. Reduced motion: the fill moves at once.

## 8. Limitations

- **Hover on the open row** no longer tints it (its background is kept transparent over
  the ink). Other rows keep Web Awesome's hover.
- **Entrances.** The fill is placed on the open row's final box and fully shown while the
  row itself enters: on an arrival (the rows on screen cascade in over about 320ms plus
  22ms per row) and on the open row's live entrance ("New session"), the fill shows
  before the row's text has faded in, and the text sits up to 0.375rem below the fill
  until its entrance ends.
- **Off-range previous row** (sessions): no glide from a row the virtual scroller has
  unmounted (more than 450px past the screen); the fill appears on the new row. A row
  off screen but still mounted glides in from the edge.
- **A row created and opened in the same patch** (no such path today in the lists' own
  flows; "New session" navigates after creating): the fill appears on it once its button
  has rendered, without gliding from the previous row.

## 9. Delivery

One commit after the user's browser review and explicit "commit":
`feat(ui): the open row's lit fill glides in the sidebar lists`, with this spec.

## 10. Amendment — reveal the open row only near an edge (user, 2026-09-29)

### 10.1 User decision — do not reopen

With the glide in place, the session list's scroll on every open-row change hides it:
today `SessionList.vue` centres the open session **every time** its id changes
(`scrollToSession` → `scrollerRef.scrollToKey(id, { align: 'center' })`, `:378-383`),
even when the row is in plain view. New rule, for both lists:

- **Compact mode:** no scroll when the open row lies entirely inside the useful zone: the
  visible area minus a top band of about two compact rows and a bottom band of about two
  compact rows **plus the part the floating "New session" button covers** (session list
  only). Otherwise scroll the **minimum**: the row's top comes to the top band's edge, or
  its bottom to the bottom band's edge.
- **Non-compact mode:** no scroll when the row is entirely visible and not under the
  "New session" button (bands: 0 at the top, the button's part at the bottom). Otherwise
  the same minimal scroll.
- **No centring anywhere**, an off-screen row included (opened from the palette, a link,
  a reload): it comes to the band edge on its side.
- The band sizes are **constants in `rem`** (they follow the font size), never measured
  at run time.
- Keyboard navigation (the highlight moved with the arrow keys) keeps its own rules.

### 10.2 Today (checked)

- Session list: the watcher at `SessionList.vue:332-336` (`flush: 'post'`, `immediate`)
  calls `cascade.holdTarget(id, scrollToSession(id))`; `scrollToSession` retries while
  the row or the scroller is missing, then `scrollToKey(id, { align: 'center' })`.
  `scrollToKey` (`useVirtualScroll.js:1233-1270`) loops: `scrollToIndex(index, { align,
  behavior: 'auto', offset })`, waits for height stability, checks the row overlaps the
  viewport. `scrollToIndex` (`useVirtualScroll.js:668-712`) knows `start`, `center` and
  `end`, from `positions[index]` (content coordinates) and `viewportHeight`, clamped.
  Keyboard navigation uses `scrollToIndex` / `scrollToIndexIfNeeded` (`SessionList.vue:564-619`), not
  `scrollToKey`.
- Artifacts list: `scrollRowIntoView(index)` → `.bookmark-entry[index].scrollIntoView({
  block: 'nearest' })` (`ArtifactBookmarkList.vue:269-275`), used by the two reveal
  watchers (6a §17.4) **and** by keyboard navigation.
- The floating button (`ProjectView.vue:3138-3143` `.new-session-split-button`, and
  `.new-session-dropdown` in all-projects mode): `position: absolute; bottom:
  var(--wa-space-s)`, over the session list. Measured (worktree, root font 15px): button
  35px high, its bottom 11.25px above the list's bottom → it covers the list's last
  **46.25px = 3.08rem**. A non-compact row measures 83.5px; compact rows are about
  2.4rem (`minSessionHeight` 35px, `SessionList.vue:215`).

### 10.3 Design

**Bands, in `styles/sidebar-rows.css`**, as **registered** custom properties read by the
lists' own code — never as `scroll-padding` / `scroll-margin`: those feed every native
scroll-into-view, and Chrome then **centres** a row that sits inside a band on any
programmatic `focus()` (measured with a 400px scroller, 75px / 121px padding: a focused
band row went to the snapport's centre). Web Awesome restores focus that way when a
dialog closes (`wa-dialog`, `setTimeout(() => trigger.focus())` without `preventScroll`,
`chunk.OUY4VDF2.js:94-96`), and the trigger of a dialog opened from a row menu is that
row's menu button: closing a Rename dialog would re-centre the list. With the bands out
of native scrolling, nothing but the reveal reads them; a focus scroll stays the browser's
plain minimal one.

```css
/* Registered, so getComputedStyle() returns them resolved to px (Firefox 128+, Chrome). */
@property --sidebar-row-reveal-top { syntax: '<length>'; inherits: true; initial-value: 0px; }
@property --sidebar-row-reveal-bottom { syntax: '<length>'; inherits: true; initial-value: 0px; }
@property --sidebar-row-reveal-cover { syntax: '<length>'; inherits: true; initial-value: 0px; }

/* Added to the existing `.sidebar-row-list` rule (the one declaring --glide-ink-bg and
   --glide-ink-radius; one rule per selector): how close to an edge the open row may sit
   before the list scrolls to reveal it (step 6a-bis §10). Non-compact: only a row not
   fully visible scrolls in. */
    --sidebar-row-reveal-top: 0rem;
    --sidebar-row-reveal-bottom: 0rem;
/* Compact: about two rows of margin at each edge. Right after the `.sidebar-row-list`
   rule (same specificity, same element: the later rule wins). */
.sidebar-row-list--compact {
    --sidebar-row-reveal-top: 5rem;
    --sidebar-row-reveal-bottom: 5rem;
}
```

(the three `@property` blocks at the top of the file, after its header comment) and in
`SessionList.vue`, **added to the existing `.session-list` rule** (`SessionList.vue:735-739`,
one rule per selector; the floating button covers only the session list):

```css
    /* The floating "New session" button covers the list's last ~3.08rem (measured: 35px +
       11.25px at a 15px root), rounded up to 3.125rem; a row under it is not visible. */
    --sidebar-row-reveal-cover: 3.125rem;
```

- `rem` values: the bands follow the font size; nothing is measured at run time. The
  browser resolves them to pixels (the properties are registered as `<length>`).
- A new class `sidebar-row-list--compact` is bound to `compactView` on both containers
  (the scroller root, `.bookmark-list`).
- **Reading them**, once per reveal, from the list container:
  `const style = getComputedStyle(el)`; `bandTop = parseFloat(style.getPropertyValue('--sidebar-row-reveal-top')) || 0`,
  `bandBottom = (parseFloat(style.getPropertyValue('--sidebar-row-reveal-bottom')) || 0) + (parseFloat(style.getPropertyValue('--sidebar-row-reveal-cover')) || 0)`.
  A small shared helper does it: `revealBands(el, env = globalThis) → { top, bottom }` in
  `utils/sidebarRows.js`, reading `env.getComputedStyle(el)` (like `entranceOffset`),
  `{ top: 0, bottom: 0 }` for a null element.
- **One nearest computation for both lists**: a pure helper `nearestScrollTop({ top,
  height, scrollTop, viewport, scrollMax, marginTop, marginBottom })` (new
  `utils/nearestScroll.js`) implements the four rules and the clamp below; the virtual
  scroller and the artifacts list both call it.

**Session list — a `nearest` alignment in the virtual scroller:**

- `useVirtualScroll.js` `scrollToIndex`: a new `align: 'nearest'` with options
  `marginTop` and `marginBottom` (px, default 0), computed by `nearestScrollTop` (also
  used by `scrollToKey`'s check below). **Its viewport is the
  container's `clientHeight`** (padding box), read at call time, not `viewportHeight`:
  that ref holds the content-box height after a resize (`VirtualScroller.vue:415-418`,
  `useVirtualScroll.js:901`) but `clientHeight` after a mount or a resume (`:1328`,
  `:1119`), so formulas on it would move by the list's padding with the resize history.
  With `vh = container.clientHeight`, `top = pos.top`, `bottom = pos.top + pos.height`,
  `viewTop = scrollTop.value + marginTop`, `viewBottom = scrollTop.value + vh -
  marginBottom`, in this order:
  1. `top >= viewTop && bottom <= viewBottom` → **no scroll**: return the current scroll
     (the caller returns without writing, and without touching `explicitScrollSeq` /
     `isProgrammaticScroll`). A row that does not fit can never pass this test;
  2. a row that does not fit (`pos.height > vh - marginTop - marginBottom`: a short
     viewport in compact mode, or a tall row) → bring its bottom to the bottom edge but
     never past its own top: `Math.min(bottom - vh + marginBottom, top)`;
  3. `top < viewTop` → `top - marginTop`;
  4. else (`bottom > viewBottom`) → `bottom - vh + marginBottom`;
  then clamp to the **real** scroll range, `[0, container.scrollHeight - vh]`, and write.
  `offset` is ignored for `nearest`. The other alignments keep `viewportHeight` and their
  clamp.
- `scrollToKey` passes `align`, `marginTop`, `marginBottom` through to `scrollToIndex`.
  **For `align: 'nearest'` its end-of-attempt check is stricter**, one rule: the attempt
  succeeds when the `nearest` target recomputed from the current positions (the same
  function as `scrollToIndex`, clamped) equals `scrollTop.value` within ±1px — this
  covers "inside the zone" (the target is the current scroll), a non-fitting row already
  placed, and both clamp limits in the right direction (a row below the zone while at 0
  gives a target above 0: not a success). Otherwise the next attempt runs and re-jumps.
  The `nearest` target computation is one shared helper used by both. Why: the first jump uses estimated heights
  (`minSessionHeight` 35 / 70, `SessionList.vue:215`; a measured non-compact row is
  83.5px); once measured, the anchor correction keeps the first visible row fixed and
  the target drifts down — a plain "overlaps the viewport" check would accept it half
  under the button. The other alignments keep the overlap check.
- **A user scroll ends the loop** (`nearest` only). `scrollToKey` waits for height
  stability between attempts (`waitForHeightStability`, restarted by every height
  change, so it lasts as long as a user scroll mounts and measures rows); a stricter
  check would otherwise pull the list back when the user scrolls the open row into a band
  during that window. The signal is the user's **input**, not scroll events: a scroll
  event is queued and fires after `isProgrammaticScroll` has been reset (it is set and
  cleared synchronously around the write, `useVirtualScroll.js:702-714`), the anchor
  correction writes with the flag off (`:471`, write at `:543-545`), and `writeAnchor` /
  `restoreSavedAnchor`, `scrollToAnchor`, `setScrollTop`, native pin-to-bottom
  (`.virtual-scroller.at-bottom`, `VirtualScroller.vue:777`) and the browser's own clamp
  all move the scroll without it — any of them would look like a user scroll.
  `useVirtualScroll.js` gains a counter `userScrollSeq`, bumped by listeners on the
  container for `wheel`, `touchstart`, `touchmove`, `pointerdown` (a scrollbar drag) and
  `keydown` of a scroll key (`PageUp`, `PageDown`, `Home`, `End`, `ArrowUp`, `ArrowDown`,
  `' '`). Attachment: a closure variable `listenedEl` holds the element the listeners are
  on; `watch(containerRef, (el) => { listenedEl?.removeEventListener(…); el?.addEventListener(…);
  listenedEl = el ?? null }, { immediate: true, flush: 'sync' })`, and the existing
  `onUnmounted` (`useVirtualScroll.js:1336`) removes them from `listenedEl` and sets it to
  `null` (at unmount Vue stops the watcher and clears the ref before `onUnmounted` runs,
  so `containerRef.value` is already `null` there); the same handler functions and
  `{ passive: true }` options for add and remove (the listeners also work when the
  composable runs outside a component, as in the tests).
  For `align: 'nearest'`, `scrollToKey` reads **two** counters right after each write
  (or no-write): `userScrollSeq`, and the existing `explicitScrollSeq` (bumped by
  `scrollToIndex`, `scrollToTop`, `scrollToBottom`, `scrollToAnchor`, `setScrollTop`;
  its own write bumps it before the read, so the loop does not end itself; anchor
  corrections and native clamps never bump it). After the settle it returns (resolving as
  today) when either changed, without checking or re-jumping. A third counter,
  `revealSeq`, covers a newer reveal that does not scroll: each `scrollToKey(…, { align:
  'nearest' })` call bumps it once at its start and keeps its own value; after each
  settle, a loop also returns when `revealSeq` is no longer its own (another session
  opened during the settle, its row already in the zone: no write, so the two other
  counters would not move, and the old loop would re-jump toward a row that is no longer
  open). `explicitScrollSeq` is not bumped for that: a no-write reveal would then cancel a
  pending `restoreSavedAnchor` retry (`useVirtualScroll.js:1089-1100`). Last, an optional
  predicate `isCurrent: () => boolean` (`nearest` only), checked after each settle with
  the counters: when it returns false the loop returns without checking or re-jumping.
  It covers an open row that changes without any newer `scrollToKey` call — the new
  session not listed yet (its `scrollToSession` retries, `SessionList.vue:369-372`, and
  may never call `scrollToKey`, e.g. hidden by the search filter) or no session open any
  more (the watcher does nothing for a null id). `scrollToSession` passes `isCurrent: ()
  => targetSessionId === props.sessionId`. So any newer intent ends the reveal: another
  open row (or none), a newer reveal, a user gesture on the list, or a newer explicit
  scroll such as keyboard navigation — from the list itself, or from the search field (`ProjectView.vue:836-848`
  forwards ArrowDown / Enter to `handleKeyNavigation`, whose `keydown` fires outside the
  container). The click that opens a row is harmless: its `pointerdown` / `touchstart`
  fires before the route change that starts the reveal, so it is in the first read's
  baseline.
- `VirtualScroller.vue`'s JSDoc for `scrollToIndex` / `scrollToKey` lists the new align
  value and options (`marginTop`, `marginBottom`, `isCurrent`, `allowSmooth` — §10.7).
- `SessionList.vue` `scrollToSession`: at the start of each attempt, `if (targetSessionId
  !== props.sessionId) return Promise.resolve()` — a retry (up to 5 × 50ms while the row
  or the scroller is missing, `:360-383`) for a session that is no longer open must not
  start a reveal: it would bump `revealSeq` and cancel the newer one. Then
  `scrollToKey(id, { align: 'nearest', marginTop,
  marginBottom })`, where the margins are read **once per call** from the scroller root:
  `const pad = parseFloat(getComputedStyle(scrollerEl.value).paddingTop) || 0`;
  `const { marginTop, marginBottom } = revealMargins(revealBands(scrollerEl.value), pad)`,
  a pure helper in `utils/sidebarRows.js`: `marginTop = bands.top - pad`, `marginBottom =
  bands.bottom + pad` (px). Why the padding: `.session-list` has `padding-block:
  var(--wa-space-2xs)` (`SessionList.vue:737`) and `positions[].top` starts at 0 without
  it, so a row's real top in the scroll content is `pos.top + pad`; shifting both margins
  by `pad` puts the zone edges on real pixels (a negative `marginTop` is valid in the
  formulas). With the viewport and the clamp taken from the container (above), the zone
  edges and the end of the list are exact.
- **What must fit is the whole list entry**: in both lists an entry holds the optional
  section label and the row (the scroller item; `.bookmark-entry`). A row whose label is
  cut at the top edge counts as not fully visible, so the label comes into view with its
  row.
- Keyboard navigation (`scrollToIndex` with `start` / `end`, `scrollToIndexIfNeeded`) is
  unchanged.

**Artifacts list:** the two reveal watchers (6a §17.4) call a new
`revealEntry(index)` instead of `scrollRowIntoView(index)`; keyboard navigation keeps
`scrollRowIntoView` (plain `scrollIntoView({ block: 'nearest' })`). `revealEntry` returns
the same `nextTick` promise shape (the cascade hold waits on it) and, inside it (guarding
a missing list or entry with `?.`, like `scrollRowIntoView`): the list `list =
listRef.value`, the entry `el = list.querySelectorAll(':scope > .bookmark-entry')[index]`;
`const target = entryReveal({ offsetTop: el.offsetTop, offsetHeight: el.offsetHeight,
scrollTop: list.scrollTop, clientHeight: list.clientHeight, scrollHeight:
list.scrollHeight }, revealBands(list))`; `if (target !== null) list.scrollTop = target`.
`entryReveal` (pure, `utils/sidebarRows.js`) calls `nearestScrollTop({ top: offsetTop,
height: offsetHeight, scrollTop, viewport: clientHeight, scrollMax: scrollHeight -
clientHeight, marginTop: bands.top, marginBottom: bands.bottom })` and returns `null`
when the target is within 1px of `scrollTop`. `offsetTop`, not the rect: `.bookmark-list`
is `position: relative` (§4.1), so it is the entry's `offsetParent` and `offsetTop` is a
content coordinate from its padding edge, unaffected by the scroll and by the entrance
`translate` of `.list-entering` on that same entry (a rect would read it up to 6px low
during a cascade); no padding shift is needed (the list's padding is part of the content
measured by `offsetTop`).

### 10.4 Tests

- `utils/nearestScroll.test.js` (new), `nearestScrollTop` (pure numbers): a row inside
  the zone → the current scroll; above → `top - marginTop`; below → `bottom - viewport +
  marginBottom`; a row that does not fit with `zone < h ≤ viewport - marginBottom` →
  `bottom - viewport + marginBottom`; `h > viewport - marginBottom` → `top`; a zero or
  negative zone (viewport 150px, margins 75px / 121.875px, a 36px row, so
  `h ≥ viewport - marginBottom`) → `top`; a negative `marginTop`; clamping at 0 and at
  `scrollMax`; margins default to 0 (plain nearest).
- `utils/sidebarRows.test.js`: `revealBands` sums bottom + cover, defaults each to 0,
  returns `{ top: 0, bottom: 0 }` for a null element (fake `env.getComputedStyle`);
  `revealMargins(bands, pad)` → `{ marginTop: bands.top - pad, marginBottom: bands.bottom
  + pad }`; `entryReveal(...)` → the target, or `null` within 1px of the current scroll.
- A new `composables/useVirtualScroll.test.js` (none exists today), driving
  `useVirtualScroll` (calling `onUnmounted` outside a component only warns) with a fake
  container: `clientHeight`, a `scrollHeight` computed from the current positions plus
  padding, a writable `scrollTop`:
  - `scrollToIndex` with `align: 'nearest'`: no write inside the zone, `offset` ignored,
    the clamp read from the container, the margins passed through;
  - `scrollToKey` with `align: 'nearest'`: re-jumps when the positions change between two
    attempts and the row left the zone (the second attempt re-aligns it); stops at the
    clamp limit; a row that does not fit lands with its bottom at the bottom edge, its top
    kept in view; at `scrollTop` 0 with a row inside the zone on estimated heights that
    grow between attempts so the row ends below the zone → the second attempt scrolls;
    a `wheel` (or `pointerdown`, or a scroll-key `keydown`) dispatched on the container
    during the settle → no second jump; a `scrollToIndex` call from outside the loop
    during the settle → no second jump; a first `nearest` `scrollToKey` on a far row,
    then during its settle a second one on a row inside the zone, then heights that make
    the first row's target drift → no write after the second call; a `nearest` call whose
    `isCurrent` turns false during the settle → no second jump; a `scrollToIndex` write followed by a dispatched
    `scroll` event, and an anchor correction during the settle, do **not** end the loop
    (the second attempt still re-aligns the row). The test stubs `requestAnimationFrame`
    / `cancelAnimationFrame` (Node has neither; `useGlideInk.test.js:14` shows the
    pattern) and gives the fake container `addEventListener` / `removeEventListener` /
    `dispatchEvent`.
- `styles/sidebar-rows.test.js`: the three `@property` blocks (`<length>`, inherits,
  `0px`); test 6 (`rule(rules, ['.sidebar-row-list'])`, one rule per selector,
  `deepEqual` on its declarations) is **updated**: the expected declarations gain
  `--sidebar-row-reveal-top: 0rem` and `--sidebar-row-reveal-bottom: 0rem`; the
  `.sidebar-row-list--compact` rule is asserted with `5rem` / `5rem` and to come after
  the `.sidebar-row-list` rule (`compact.order > base.order`).
- Source scans (`styles/glide.test.js` or a new test): `SessionList.vue`
  `scrollToSession` calls `scrollToKey` with `align: 'nearest'` and margins from
  `revealMargins(revealBands(…), pad)`, where `pad` is read from
  `getComputedStyle(scrollerEl.value).paddingTop`; it starts with the `targetSessionId !==
  props.sessionId` guard and passes `isCurrent: () => targetSessionId === props.sessionId`
  to `scrollToKey`; no `align: 'center'` left in it; the single
  `.session-list` rule sets `--sidebar-row-reveal-cover: 3.125rem`; **no
  `scroll-padding` / `scroll-margin`** in `SessionList.vue`, `ArtifactBookmarkList.vue`
  or `sidebar-rows.css`; `ArtifactBookmarkList.vue`'s two reveal watchers call
  `revealEntry`, which contains `return nextTick(` and `':scope > .bookmark-entry'`, passes
  `offsetTop: el.offsetTop` and `offsetHeight: el.offsetHeight` to `entryReveal`, and has
  no `getBoundingClientRect`; keyboard navigation still calls
  `scrollRowIntoView`; both containers bind `sidebar-row-list--compact` to
  `compactView`.

### 10.5 Browser checks

1. Compact mode, session list with a dozen rows visible, scrolled so rows exist above and
   below the visible area: click a row in the middle → the fill glides, the list does not
   move. Click the second row from the top (or the bottom) → the list scrolls a little,
   the row ends about two rows from the edge (above the "New session" button at the
   bottom). At the very top (or end) of the list, a click in the band does not scroll
   (nothing to scroll to).
2. Non-compact: click any fully visible row → no scroll; click a row (not the last one)
   half under the "New session" button → the list scrolls just enough to show it above
   the button.
3. Open a session from the command palette that is far off screen → it comes to the band
   edge on its side, not to the centre.
4. Reload on a session far down → it is revealed at the band edge; the cascade still
   waits for that scroll (6a §17 / 5c hold).
5. Artifacts list: the same checks without the button.
6. Keyboard navigation in both lists behaves as before.
7. Rename a session in a band (top or bottom two rows, compact mode) from its row menu,
   then close the dialog → the list does not jump.
8. Session list (Firefox first) — the gesture signal must be proven, since a gesture made
   after the loop has ended (≈200–400ms after the open) proves nothing: temporarily log
   in the `userScrollSeq` handler (a DevTools logpoint or a temporary `console.debug`)
   and confirm one entry for a wheel, for a scrollbar-thumb drag and for PageDown on the
   focused list; then temporarily pass `settleMs: 2000` in `scrollToSession`, open a
   session far off screen (command palette) and scroll the list away within those 2s by
   each gesture → the list stays where you put it, no second jump. Revert both
   afterwards.

### 10.6 Limitations

- The band sizes are fixed estimates in `rem` (5rem ≈ two compact rows; 3.125rem ≈ the
  button's covered part, measured at a 15px root): a different row density or button
  height is not tracked.
- **Short compact list**: with a list viewport shorter than about 15.5rem (the two bands
  plus the button part plus one compact row), no row fits the zone, so every open-row
  change scrolls, to the bottom band edge or the row's top (for example a phone in
  landscape).
- **Section labels count**: the unit that must fit is the entry (label + row), so a fully
  visible first row of a section whose label is cut at the top still scrolls a little.
- **The last rows cannot reach the zone**: the scroll stops at the list's end, and nothing
  adds room under the last row for the floating button (already the case today). In
  non-compact mode the last row stays partly under the button; in compact mode the last
  ~3 rows stay in the bottom band or under the button. The reveal goes as far as the
  scroll allows (the real end of the list).

### 10.7 Amendment — a smooth reveal (user, 2026-09-29)

The user's browser review: when the list scrolls to reveal the open row, the jump is
sudden. A near-edge reveal must scroll **smoothly**; the rest of §10 stands.

- **When smooth.** A pure helper `revealBehavior({ distance, viewport, reduced, allowed })
  → 'smooth' | 'auto'` in `utils/nearestScroll.js`: `smooth` when `allowed`, not
  `reduced`, and `0 < distance ≤ viewport`; else `auto`.
  - `distance = |target − current|`, where *current* is the destination of a smooth
    reveal still in flight if there is one (below), else the scroll position;
    `viewport` = the list's `clientHeight` for the artifacts list (every row is mounted
    and measured), and `Math.min(clientHeight, buffer)` for the session list (`buffer` =
    the composable's own `buffer` option, `useVirtualScroll.js:67`, here `SCROLLER_BUFFER`
    = 300px, `SessionList.vue:216`). A longer move (a far row from the palette) stays
    instant.
  - **Session list, one more condition (going up only)**: every item that a smooth
    scroll **up** will mount is already measured. From the real render code
    (`useVirtualScroll.js:324-330`, `findIndexAtPosition` `:251-273`, `renderRange`):
    every index in `[findIndexAtPosition(max(0, target − buffer)), renderRange.start)`
    must have a `heightCache` entry (key `itemKey(item)`); otherwise the reveal is
    `auto`. The current `renderRange` is exact: its hysteresis only widens it beyond the
    load zone (`:344-366`), and it stays right while a smooth scroll is in flight.
    Going down there is no condition: a row's position depends only on the items above
    it, and with `distance ≤ buffer` every item mounting on the way down starts at or
    below `current + viewportHeight + buffer ≥ target + viewportHeight`, below every
    viewport of the animation — its first measurement moves nothing visible. (The
    session list never seeds heights: the item just past `renderRange.end` is never
    measured, so a downward condition would make almost every bottom-edge reveal
    instant.) Why: an item mounting mid-scroll
    keeps its estimated height until measured (70 against 83.5px non-compact, a section
    label not counted), and with the anchor correction held (below) and
    `overflow-anchor: none` (`VirtualScroller.vue:763`) its correction would shift the
    visible rows in steps during the animation. `revealBehavior` gets this as part of
    `allowed` (the composable computes it; the artifacts list mounts every row and passes
    only the cascade condition).
  - `reduced` = `globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
    === true`, read at call time (as `utils/colorSchemeTransition.js:65`; `window` is
    not defined in the Node tests). Reduced motion removes movement (roadmap §4).
  - `allowed` = false while the list's arrival cascade is **pending** (a reload, a scope
    switch, a remount): the cascade holds its start for the reveal, capped at 300ms
    (`utils/listCascade.js:12`, `useListCascade.js:107-118`); a smooth scroll would
    outlast the cap and `start` would read a range still moving, leaving the rows that
    scroll in afterwards without an entrance; and once it plays, a smooth scroll over the
    cascading rows would move them mid-entrance. `useListCascade` exposes `isArriving()`
    (`phase.value !== 'idle'`: pending or playing); each list passes `allowed:
    !cascade.isArriving()`. (A reload whose open row is already inside the visible range
    retries its reveal 50ms later, when the cascade may already be playing: still
    instant.)
- **One smooth scroll at a time, per list, with its destination remembered.** Each list
  keeps `smoothTo = { target, token } | null` for a smooth reveal in flight:
  - set when the smooth `scrollTo` starts; cleared (only if its token is still the
    current one) on the container's at-target `scrollend` or after **600ms**, whichever
    comes first. The `scrollend` listener is a plain one (not `{ once: true }`), removed
    explicitly on the at-target end, on the timeout and on `cancel()`; the timer is
    cleared on the at-target end and on `cancel()`. A `scrollend` counts only when the container is at
    the target (`|scrollTop − target| ≤ 1`); an earlier one (a wheel animation still
    finishing, an instant write in the same frame) is ignored and the listener stays until
    the timer. `scrollend` fires on the scroll container in Firefox 109+ and Chrome 114+;
    the timeout covers a missing event or a no-op scroll;
  - **ended early** by one internal `endSmooth()` (per list): it invalidates the token,
    clears `smoothTo`, releases the programmatic hold (session list) and resolves the
    pending `done`. Called on a **scrolling** gesture that the browser performs itself —
    which cancels its animation, so the list must correct its anchor again at once:
    - a `wheel`;
    - a `pointercancel` with `pointerType === 'touch'` (the Pointer Events signal that
      the browser took the touch for panning, in Chrome and Firefox; a plain
      `touchmove` also fires for the jitter of a tap, which does not cancel anything);
    - a `pointerdown` whose `event.target` is the container itself (its scrollbar);
    - a scroll-key `keydown` **not handled by the list** (`!event.defaultPrevented`): both
      lists handle Arrow / Home / End / PageUp / PageDown in their own `keydown` and call
      `preventDefault()` (`SessionList.vue:650-658`, `ArtifactBookmarkList.vue:415-419`),
      often without scrolling at all; only a key the browser scrolls (Space) ends it. In
      the session list the template `@keydown` on `<VirtualScroller>` is attached when the
      element is created, before `containerRef` is set, so it runs first and the
      composable's listener sees `defaultPrevented`. **Not** on `touchstart` nor on a `pointerdown` from a child: a
    click or a tap on a row does not cancel the animation, and a second click during a
    smooth reveal must keep the destination and the hold (the session list's
    `userScrollSeq` still counts every input, as §10.3 says). And called **before every
    other programmatic write** of the list:
    the `auto` branch of `writeProgrammaticScroll`, `scrollToTop`, `scrollToBottom`,
    `setScrollTop`, `scrollToAnchor`, `writeAnchor` in `useVirtualScroll.js`; the `auto`
    write of `revealEntry` in the artifacts list, and its `scrollRowIntoView` **only when
    it actually moved the list** (compare `list.scrollTop` before and after the
    `scrollIntoView`; a no-op keyboard highlight must not drop a running reveal's
    destination). The artifacts list listens with **template listeners** on the
    `.bookmark-list` element (which is `v-else` and can be re-created,
    `ArtifactBookmarkList.vue:499`; Vue attaches and removes them with it):
    `@wheel.passive`, `@pointercancel` (touch only), `@pointerdown.self`, and in its
    existing `@keydown` handler a branch for keys it does not handle (Space, when not
    `defaultPrevented`), each calling `endSmooth`. So a remembered destination never outlives the scroll that was going
    there;
  - while set, the reveal computations use `smoothTo.target` as the current scroll (the
    `scrollTop` passed to `nearestScrollTop` / `entryReveal`, and the `current` of the
    distance): a second click during a smooth scroll is judged against where the list is
    going, not where it is mid-way. A no-write result then leaves the running smooth
    scroll to finish.
  The session list keeps it in `useVirtualScroll.js` (one per composable); the artifacts
  list in `ArtifactBookmarkList.vue`. One helper, `startSmoothScroll(container, target,
  env = globalThis)`, in `utils/nearestScroll.js`, does the `scrollTo`, the at-target
  `scrollend` / timeout race with its cleanup, and returns `{ done, cancel }` (`done`
  resolves at the end or on `cancel()`, which also cleans up); the lists keep the token
  and `smoothTo` around it.
- **Session list (`useVirtualScroll.js`).**
  - **Wiring of the endings** in `useVirtualScroll.js`: `noteUserScroll` also calls
    `endSmooth()` for a `wheel`, and for a `pointerdown` whose `event.target` is the
    container; `noteScrollKey` also calls it for a scroll key when
    `!event.defaultPrevented`; a new `pointercancel` handler (touch only) calls it and
    joins the same add / remove pair (`addUserScrollListeners` /
    `removeUserScrollListeners`), so it moves with the container and is removed from
    `listenedEl` at unmount. The test at `useVirtualScroll.test.js:330-350` (one listener
    per type) is **extended** to `pointercancel`.
  - `scrollToIndex` with `align: 'nearest'` gains an option `allowSmooth` (default
    false); it computes the target from the current scroll as defined above, then either
    writes at once (`auto`, today's `writeProgrammaticScroll`) or starts a smooth scroll.
    The `behavior` option is ignored for `nearest`. For `nearest` it **returns** the
    started smooth scroll's `done` promise, or `null` (an `auto` write or no write).
  - **The programmatic hold follows the smooth scroll**: `isProgrammaticScroll` stays
    true until that smooth scroll's end (its `done`), not a fixed 500ms — so the anchor
    correction (`useVirtualScroll.js:503`, write `:575-577`) cannot write mid-scroll and
    cancel the animation. A token guards the release: an older smooth scroll's end never
    clears a newer hold. (The existing 500ms smooth path of `writeProgrammaticScroll`,
    used by other callers, is unchanged.) Heights that change during the smooth scroll
    are then handled by the next check.
  - `scrollToKey` with `nearest` passes `allowSmooth`, and when its own `scrollToIndex`
    call returned a `done` promise **awaits it** before the height-stability wait and the
    check. When the attempt did not write while a smooth scroll is running (`smoothTo`
    set), it **awaits that smooth scroll's end** too (a plain wait on its `done`: it
    neither takes over its hold nor its token), so its check never compares against a
    position still in flight. After an `auto` write
    or no write, no extra wait. A re-jump follows the same rule. The counters and
    `isCurrent` are read right after the write, as today.
  - `SessionList.vue` `scrollToSession` passes `allowSmooth: !cascade.isArriving()`.
- **Artifacts list (`revealEntry`).** `const current = smoothTo ? smoothTo.target :
  list.scrollTop`; `entryReveal({ …, scrollTop: current, … }, bands)`; with `allowed =
  !cascade.isArriving()` and `distance = Math.abs(target − current)`: `auto` →
  `endSmooth()` then `list.scrollTop = target`; `smooth` → `startSmoothScroll(list,
  target)` (a running one is ended first).
- **Keyboard navigation and the other callers** (chat list, share viewer): unchanged (they
  never use `nearest`).
- **The glide** runs at the same time: the ink lives in the list's content coordinates, so
  it moves with the scroll while it glides.
- **Tests.**
  - `utils/nearestScroll.test.js`: `revealBehavior` (≤ viewport → smooth; above → auto;
    reduced → auto; not allowed → auto; distance 0 → auto); `startSmoothScroll` with a
    fake element: `scrollTo` called with `behavior: 'smooth'`; `done` resolves on a
    dispatched `scrollend` (the timer cleared) or after 600ms (the listener removed).
  - `useVirtualScroll.test.js`: its fake container gains a `scrollTo` that **records the
    call and moves `scrollTop` only halfway** (dispatching `scroll`); the test completes
    the move and dispatches `scrollend` itself; `globalThis.matchMedia` stubbed where
    needed. Cases: a near nearest write is smooth; a far one is instant; reduced motion →
    instant; `allowSmooth: false` → instant; after a smooth write, rows above the target
    grow before `scrollend` → no second write before it, the re-jump after it plus the
    settle; with no `scrollend`, the check runs after 600ms; an early `scrollend` away
    from the target is ignored, and a later at-target `scrollend` still resolves `done`
    before 600ms; a `pointerdown` dispatched with a child as target during the smooth
    scroll, then a second nearest call at halfway → no write, the destination and
    `isProgrammaticScroll` kept; a `keydown` ArrowDown with `defaultPrevented: true`
    during the smooth scroll → destination and hold kept; a `pointercancel` with
    `pointerType: 'touch'` → the smooth scroll ends (hold released); a `touchmove` alone
    → nothing ends; `isProgrammaticScroll` stays true until the end (an
    anchor correction mid-scroll does not write); a `wheel` during the smooth scroll ends
    it (a height change above the viewport then corrects the anchor); a second nearest
    call at the halfway position whose row is outside the zone there but inside at the
    destination → no write; an instant write (`setScrollTop`) during a smooth scroll
    clears the destination; a second no-write nearest call during a smooth scroll, then a
    height change above its row after the `scrollend` → the list is corrected after the
    end.
  - `useListCascade.test.js`: `isArriving()` true in the pending and playing phases,
    false when idle.
  - Existing assertions **updated**: `styles/glide.test.js:466` (the anchored
    `scrollToKey(targetSessionId, { align: 'nearest', marginTop, marginBottom, isCurrent:
    …, })` regex) gains `allowSmooth: !cascade.isArriving()` after `isCurrent`; `:505` and
    `:507` (the `VirtualScroller.vue` JSDoc scans of `scrollToIndex` and `scrollToKey`)
    both expect `allowSmooth` listed; a new scan checks the artifacts list's template
    listeners (`@wheel.passive`, `@pointercancel` touch-only, `@pointerdown.self` calling
    `endSmooth`, the Space branch of its `@keydown` guarded by `!event.defaultPrevented`,
    and `scrollRowIntoView` calling `endSmooth` only when `scrollTop` changed); the scan of
    `revealEntry` checks `const current = smoothTo ? smoothTo.target : list.scrollTop`,
    `scrollTop: current` passed to `entryReveal`, the distance from `current`, and
    `endSmooth()` before `list.scrollTop = target`;
    `useVirtualScroll.test.js`: a nearest target more than `buffer` away but within
    `clientHeight` → instant; an upward near reveal with unmeasured items in
    the upward interval → instant (measured → smooth); an unmeasured item that straddles
    `target − buffer`, every other item measured → instant; a downward near reveal with
    unmeasured items past `renderRange.end` → smooth; each other ending
    of a smooth scroll clears the destination and releases the hold — a `pointerdown`
    whose target is the container, a non-prevented Space `keydown`, and a
    `scrollToIndex(…, { align: 'start' })`, `scrollToTop`, `scrollToBottom`,
    `scrollToAnchor` and `writeAnchor` call during it;
    `styles/glide.test.js:481` (`if (target !== null)
    list.scrollTop = target`) becomes a scan for the `auto` write and
    `startSmoothScroll(list, …)` driven by `revealBehavior(…)` with `allowed:
    !cascade.isArriving()`; `SessionList.vue` passes `allowSmooth: !cascade.isArriving()`.
- **Limitation.** After a far jump (palette, reload, link) the rows above the render range
  were never measured: the next upward near reveals are instant until those rows have
  been rendered once.
- **Limitation.** "Measured" means "has a `heightCache` entry", and an unmounted row keeps
  its last height: after a compact toggle (the scroller is keyed by project only, the
  cache survives) or a section label appearing / disappearing on an off-screen row, a
  near smooth reveal soon after can mount a row whose height is stale and step once
  mid-animation (the next check corrects the position).
- **Browser checks.**
  1. Compact mode, list at mid-height: click the second row from the top or the bottom →
     the list scrolls smoothly while the fill glides.
  2. Click a bottom-edge row, then at once a top-edge row → the list turns back at once
     toward the second row and ends with it at the top band edge; no extra jump follows
     (the first reveal's check does not pull the list back).
  3. A far session from the palette → instant jump. Reduced motion → instant.
  4. Reload with the open row just below the fold → instant reveal, the cascade plays on
     the final rows (none appears without its entrance).
  5. Firefox: a smooth reveal is not interrupted mid-way by a jump.
  6. Gestures during a smooth reveal (Firefox first, both lists): start one (a palette
     open of a row a few rows beyond the edge), and at once — a wheel, a scrollbar-thumb
     drag, a touch pan (DevTools touch emulation), Space on the focused list → the list
     stays where you put it, no pull-back, no jump. A temporary logpoint in `endSmooth`
     proves each gesture triggers it (revert afterwards).
