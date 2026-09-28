# Gliding indicators + `SegmentedControl` — design (visual refresh, step 4c)

## 1. Context

Step 4 of the "Signature" visual refresh is split in three sub-steps: 4a (motion tokens +
micro-interactions, done: `docs/plans/2026-09-27-motion-micro-design.md`, commit
`46b4926c`), 4b (`wa-details` open/close, done: `docs/plans/2026-09-27-details-motion-design.md`,
commit `d11ea446`), **4c (this document)**. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding user decisions: **Firefox
parity**, **reduced motion is reduced, not none**), §6d and §6e (4a and 4b lessons).

4c uses the 4a tokens from `frontend/src/styles/motion.css`: `--motion-dur-1` (120ms),
`--motion-dur-3` (380ms), `--motion-ease`, `--motion-ease-spring` (the mock's `linear()`).

Today every "current item" marker jumps: the active tab's underline, the command palette's
highlighted row, the keyboard-selected row of the other lists, the checked segment. In the
mock (`fx-motion` block of `mock.css`, roadmap §3), one shared shape, the **ink**, slides
from the old item to the new one (`transform` + `width` + `height`, `--dur-3`,
`--ease-spring`).

### 1.1 User decisions (2026-09-27) — do not reopen

- **Approach A:** JavaScript measures the active item and writes its box into CSS custom
  properties; a CSS transition moves the ink. Same code path in Firefox and Chrome.
  Rejected: CSS anchor positioning (measured: Firefox 156 does not transition an anchor
  change, it jumps, and no `CSS.supports()` detects it); View Transitions (a snapshot per
  change; held ↑/↓ in the palette would overlap and cancel them).
- **Scope:** the tab bars (every `TabBar`), the settings nav, the command palette, the
  session switcher, the search overlay, the `/` command picker and the message-history
  picker, plus a new **`SegmentedControl`** component used by the two existing segmented
  controls (Cost/Speed in the benchmark task, the orchestration panel's view switch). Any
  future segmented control that uses the component glides with no extra work.
- **First placement never glides:** when a list opens, the ink is already in place. It
  never starts from a corner.
- **No active item** (for example a filtered list with no match): the ink fades out.
- **Never an unmarked active item:** until the ink is placed, the active item keeps its own
  marker (today's background or border).
- **Scrolling:** the ink lives in the scrolled content, so it scrolls with the list or the
  tab strip. In the palette it passes **under** the sticky group headers.
- **Reduced motion (roadmap §4):** no gliding; the ink goes to its final place at once.
  The fade-out stays.
- **Glides on pointer hover too** where the hover already moves the active row (palette,
  switcher, search, pickers).
- **Sidebar collapse is not animated** (roadmap §6d) — unrelated to 4c, restated so it is
  not added here.

## 2. Goals

1. One shared mechanism (§4) used by every site: measure, decide glide or snap, write.
2. Each site in scope (§6, §7) shows a gliding ink with the same look as today's marker,
   except the settings nav (new light accent fill, §6.2) and the segmented control (mock
   look, §7).
3. No regression: the active item is always marked; nested tab bars do not see each
   other's ink; no glide on first display, on a return from a hidden panel, or when a list
   is rebuilt (§4.2).
4. Firefox parity: every CSS feature used is supported by Firefox 156 (§4.5).

## 3. Out of scope

- The sidebar session list and artifact bookmark list keyboard navigation
  (`SessionList.vue`, `ArtifactBookmarkList.vue`): virtual-scroll rows, not in the user's
  selection.
- Tree-based pickers (`FilePickerPopup.vue`, `DirectoryPickerPopup.vue`, both on
  `FileTree`): rows expand and collapse; not in the user's selection.
- Web Awesome menus and selects (`wa-dropdown`, `wa-select`): their highlight is internal
  to Web Awesome.
- The accent glow on the ink (mock `fx-glow`: gradient, glow shadow, ring): step 6.
- Tab bars with a `placement` other than `top`: none exists today (checked: no `TabBar`
  call site sets `placement`). They keep Web Awesome's border (§6.1).

## 4. Mechanism — new `frontend/src/utils/glideInk.js`

### 4.1 Terms

- **Container:** the element whose padding box is the ink's containing block. For a list,
  the scrolled list element; for a tab bar, Web Awesome's `[part~="tabs"]` element.
- **Target:** the element that receives the custom properties and the state attributes.
  For a list, the container itself; for a tab bar, the `wa-tab-group` host (the custom
  properties inherit into its shadow tree, down to `::part(tabs)::after`).
- **Active element:** the item to mark, returned by a site-provided `getActive()`
  (`null` = none).
- **Reset key:** an optional site-provided value. When it changes, the list was rebuilt:
  the next placement snaps.
- **Items:** an optional site-provided list of elements (`getItems()`) whose size changes
  can move the active element without resizing the container or the active element (tabs
  before the active one). They are watched for resize (§4.3 step 9).
- **Box:** `{ x, y, w, h }` in CSS pixels, the active element's border box relative to the
  container's padding box, in content coordinates (scroll-independent) and in **layout
  pixels** (unaffected by a `scale` on an ancestor: Web Awesome dialogs, popovers and
  dropdowns open with `scale: 0.8 → 1`).

### 4.2 Pure helpers (exported, unit-tested)

```js
// Box of `activeRect` inside the container, in content coordinates and layout pixels.
// The rects are visual (getBoundingClientRect, after transforms); the scroll and client
// offsets are layout values. The container's visual/layout ratio removes an ancestor's
// scale (a dialog's opening animation) before the layout offsets are added.
// offsetWidth/offsetHeight are rounded integers while the rect is fractional: a difference
// of 1px or less is rounding, not a scale, and gives a ratio of exactly 1 (a real opening
// scale of 0.8 differs by tens of px).
export function scaleRatio(visual, layout) {
    return layout > 0 && visual > 0 && Math.abs(visual - layout) > 1 ? visual / layout : 1
}

export function relativeBox(activeRect, containerRect,
                            { scrollLeft = 0, scrollTop = 0, clientLeft = 0, clientTop = 0,
                              offsetWidth = 0, offsetHeight = 0 } = {}) {
    // Web Awesome's opening scales are uniform: one ratio, taken on the container's longer
    // layout axis (where a real scale is furthest above the 1px rounding threshold).
    const s = offsetWidth >= offsetHeight
        ? scaleRatio(containerRect.width, offsetWidth)
        : scaleRatio(containerRect.height, offsetHeight)
    const sx = s
    const sy = s
    return {
        x: (activeRect.left - containerRect.left) / sx - clientLeft + scrollLeft,
        y: (activeRect.top - containerRect.top) / sy - clientTop + scrollTop,
        w: activeRect.width / sx,
        h: activeRect.height / sy,
    }
}

// What the next placement does.
//   state: { ready, active, resetKey }   (last placement)
//   next:  { visible, active, resetKey } (now)
export function placementMode(state, next) {
    if (!next.visible || !next.active) return 'hide'
    if (state.ready && state.active && state.active !== next.active
        && Object.is(state.resetKey, next.resetKey)) return 'glide'
    return 'snap'
}

// true when all four values differ by less than 0.01px (a or b may be null → false unless
// both null). The tolerance absorbs the float noise of the scale division, so a measure
// taken during an opening animation does not snap over a running glide.
export function sameBox(a, b)
```

`placementMode` table:

| Case | Mode |
|---|---|
| Container hidden (zero width or height, or disconnected) | `hide` |
| No active element | `hide` |
| First placement, or first after a `hide` | `snap` |
| Same active element moved or resized (resize, reorder, font size) | `snap` |
| Reset key changed (list rebuilt: new query, new results) | `snap` |
| Another active element, same reset key, ink shown | `glide` |

### 4.3 Controller

```js
export function createGlideInk({
    container,            // Element
    target = container,   // Element receiving --glide-* and data-glide-*
    flushTarget,          // Element whose computed style carries the ink's transition
    flushPseudo = null,   // '::after' for the tab bar ink, else null
    getActive,            // () => Element | null
    getItems = () => [],  // () => Element[] — extra elements watched for resize
    getResetKey = () => null,
    env = globalThis,     // reads env.ResizeObserver, env.getComputedStyle, env.setTimeout,
                          // env.clearTimeout, env.requestAnimationFrame,
                          // env.cancelAnimationFrame — injectable for tests. globalThis (as in
                          // detailsMotion.js), never a plain object holding the bare
                          // window functions: those throw "Illegal invocation" when
                          // called with another `this`.
}) → { update(), destroy() }
```

State: `{ ready: false, active: null, resetKey: null, box: null, collapseTimer: null,
settleFrame: null, settleRect: null }`.

`createGlideInk` writes `0px` to the four properties on the target (so a nested target,
such as a `TabBar` inside another `TabBar`'s panel, never inherits its ancestor's box),
creates the observer (step 9) and calls `update()` once before returning. Every caller
relies on this first call; none repeats it.

`update({ settle = false } = {})` (callers and observers call it without argument; only
the settle loop of step 10 passes `settle: true`):

1. `visible` = `container.isConnected` and its `getBoundingClientRect()` has non-zero width
   and height. `active` = `getActive()`; an active element with a zero-size rect counts as
   `null`. `resetKey` = `getResetKey()`. Clear a pending `collapseTimer`. **Sync the
   observed set (step 9) now, before any mode**, `hide` included: the container is always
   observed, so a controller created hidden (a closed popover) places its ink when the
   container gets a size.
2. `mode = placementMode(state, { visible, active, resetKey })`.
3. **`hide`:** remove `data-glide-ready` from the target; `state.ready = false`,
   `state.active = null`, `state.box = null`. The ink fades out (§5) at its last box (during a running glide: at the
   glide's destination, §11).
   Then **collapse** it: write `0px` to the four properties — at once when the container
   is not visible, else after the fade (`env.setTimeout`, delay = the target's computed
   `--motion-dur-1` read with `parseDurationMs` from `utils/detailsMotion.js`), stored in
   `collapseTimer`. A collapsed ink is a zero-size box at the container's origin: an
   invisible ink left at a far row would otherwise keep the list's scroll range (a list
   that just lost all its rows could stay scrolled past its "no match" line). A later
   placement cancels the timer (step 1) and snaps (its box is new: `state.box` is `null`).
   Stop.
4. Measure: `box = relativeBox(active.getBoundingClientRect(), containerRect,
   { scrollLeft, scrollTop, clientLeft, clientTop, offsetWidth, offsetHeight } of the
   container)`.
5. **`snap`:** set `data-glide-instant` on the target; write the four properties
   (`--glide-x`, `--glide-y`, `--glide-w`, `--glide-h`, values in `px`) with
   `target.style.setProperty`; set `data-glide-ready`; force a style flush with
   `env.getComputedStyle(flushTarget, flushPseudo).getPropertyValue('translate')`; remove
   `data-glide-instant`. The flush commits the new values with `transition: none`, so no
   transition starts; removing the attribute afterwards changes no value, so none starts
   either.
6. **`glide`:** write the four properties only (the target is already ready). The CSS
   transition runs; a new glide during a running one retargets from the current value
   (native CSS transition behaviour).
7. Skip the writes in 5 and 6 when `sameBox(box, state.box)` (nothing to do). After a
   `hide`, `state.box` is `null`, so the next snap always writes.
8. Store `ready = true`, `active`, `resetKey`, `box`.
9. **Observers:** one `ResizeObserver` watches the container, the active element and every
   element of `getItems()`. At each `update()` (step 1, every mode), the observed set is
   synced: elements no longer in {container, active, items} are unobserved, new ones
   observed (`active` and items only when non-null). Its callback
   calls `update()` (same active element → `snap`, or no-op when the box is unchanged).
   The callback runs before paint, so a resize never shows a stale ink. A `scale`
   animation on an ancestor does not fire it (layout sizes do not change) and does not
   need to: the box is in layout pixels (§4.2), to within the rounding of the integer
   `offsetWidth` (≤ 0.5px) — step 10 removes that residue.
10. **Settle after a scale.** When a measure (snap or glide) finds the container's visual
   size on its longer axis differing from its layout size by more than 0.5px (an ancestor
   is scaled, i.e. an opening animation runs), start a settle loop unless one runs. An
   update with `settle: true` never starts a loop; only the loop's own frame callback
   schedules its next frame or stops it.
   - **Each frame** (`env.requestAnimationFrame`) only **reads** the container's rect and
     layout size; it writes nothing (Firefox rounds positions to 1/60px, so a write per
     frame would restart a running glide every frame and slow it down).
   - **Stop conditions**, checked each frame:
     1. the scale ended: the visual size on the longer axis is within 0.5px of the layout
        size (the start condition, reversed) **and** the rect equals the previous frame's
        rect (within 0.01px) — the primary condition. "Within 0.5px" alone would fire one
        frame early: Web Awesome's `ease` curve reaches 0.9992 one frame before 1, and a
        measure there has a ratio forced to 1 (difference ≤ 1px) and stays ~0.3px short.
        Web Awesome's openings end at exactly `scale: 1` and then hold still, so the
        equal-rect frame is the first one at the final scale (cost: one frame);
     2. fallback for a scale that never ends: from the 3rd frame on, the rect equals the
        previous frame's rect (within 0.01px). The first frame compares with the rect of
        the measure that started the loop (`state.settleRect`), but condition 2 is never
        checked before the 3rd frame: Firefox starts a pending animation one tick after its
        first paint, so its first frame can still show the start scale;
     3. safety cap: at least 60 frames **and** at least 500ms, measured from the first
        frame's `timestamp` argument (the loop's start has no rAF timestamp;
        `performance.now()` is not used, it is not in `env`). Frame 1 counts as frame 1
        and as elapsed 0. A frame count alone would stop a 200ms opening early on a
        360Hz display.
   - **At the stop:** one `update({ settle: true })`, taken at the final scale, so the ink
     ends exact (a no-op when `sameBox` holds).
   - A `hide` or `destroy()` cancels the loop (`settleFrame`, cleared with
     `env.cancelAnimationFrame`).
   - A settle update never snaps: when its mode is `snap` for the **same** active element,
     it writes the four properties like a glide (no `data-glide-instant`, no flush). The
     correction is under half a pixel, so its transition is invisible, and a glide the
     user started during the opening (↓ right after opening the palette) is retargeted
     once, at the end, not cut. Any other mode (a new active element, a reset key change,
     `hide`) runs as usual.

`destroy()`: clear `collapseTimer` and `settleFrame`, disconnect the observer, remove both
attributes and the four properties from the target.

Writes go through `setProperty` / `setAttribute` only: Vue patches only the style keys and
attributes it manages, so it never removes them.

### 4.4 Composable — new `frontend/src/composables/useGlideInk.js`

```js
export function useGlideInk({
    container,       // Ref<Element | null>
    target,          // Ref<Element | null>, defaults to container
    flushTarget,     // Ref<Element | null> (the ink element for lists)
    flushPseudo,     // string | null
    getActive,       // () => Element | null
    getItems,        // () => Element[], optional, passed through
    sources,         // watch sources that move the active item (Vue refs/getters)
    resetKey,        // () => any, optional
    env,             // optional, passed through to createGlideInk (tests)
}) → { update }
```

- `watch(container, …, { immediate: true, flush: 'post' })`: when the element appears,
  create the controller (which places the ink, §4.3); when it changes or disappears,
  `destroy()` the old one first. This covers lists rendered under `v-if` (search results,
  switcher).
- `watch(sources, () => controller?.update(), { flush: 'post' })`: after Vue patched the
  DOM, so the new active element exists and has its classes.
- `onScopeDispose` destroys the controller. No `onMounted`: the composable runs in any
  effect scope, so it is testable with `effectScope()` in node.

### 4.5 Browser support (Firefox 156 and Chrome)

Used: custom properties, the `translate` property and its transition, `linear()` easing,
`ResizeObserver`, `::part()::after` (already in this branch, step 3), `:state()` (Web
Awesome's own styles rely on it), `:deep()` / `::part()` from Vue scoped styles. No
anchor positioning, no view transitions, no `transition-behavior`. Nothing needs a
fallback; no user-agent test.

## 5. Shared CSS — `frontend/src/styles/motion.css`

Add to the existing `:root` token block:

```css
/* Gliding indicators (step 4c): the ink moves with the spring, fades with --motion-ease. */
--glide-transition:
    translate var(--motion-dur-3) var(--motion-ease-spring),
    width var(--motion-dur-3) var(--motion-ease-spring),
    height var(--motion-dur-3) var(--motion-ease-spring);
--glide-fade: opacity var(--motion-dur-1) var(--motion-ease);
```

Under `prefers-reduced-motion: reduce`, in the existing `:root` block of that media query:
`--glide-transition: none;` (no gliding; `--glide-fade` stays).

The ink's `translate` is a **measured placement**, not a movement distance: it is never
multiplied by `--motion-amount` (at `0` every ink would sit at the container's origin over
a transparent active row). Reduced motion is handled by `--glide-transition: none`. This
is an exception to the `motion.css` header rule "every movement distance … is multiplied
by --motion-amount"; the header states it next to the `motion-spin` exception, and
`motion.test.js` is updated accordingly (§9).

Add a new section (after the micro-interactions, before the `wa-details` section):

```css
/* Gliding indicators (step 4c): the list ink. The owner (useGlideInk) writes --glide-x/y/w/h
   and data-glide-ready / data-glide-instant on the ink's parent. The ink is the first child,
   positioned and without z-index; the rows are positioned too, so they paint above it in
   tree order, and sticky headers (z-index) above both. */
.glide-ink {
    position: absolute;
    top: 0;
    left: 0;
    width: var(--glide-w, 0px);
    height: var(--glide-h, 0px);
    translate: var(--glide-x, 0px) var(--glide-y, 0px);
    border-radius: var(--glide-ink-radius, 0);
    background: var(--glide-ink-bg, transparent);
    pointer-events: none;
    opacity: 0;
    transition: var(--glide-fade);
}
[data-glide-ready] > .glide-ink {
    opacity: 1;
    transition: var(--glide-transition);
}
/* After the ready rule (same specificity): a snap commits its values without a transition. */
[data-glide-instant] > .glide-ink {
    transition: none;
}
```

Entering the ready state uses the ready rule's transition list, which has no `opacity`:
the ink appears at once, in the same frame the active item's own marker is removed. Leaving
it uses the base rule's list: the ink fades out.

The ink element in a template: `<span class="glide-ink" aria-hidden="true"></span>`, first
child of the container.

## 6. Sites

Each list site does four things: (1) make the container `position: relative` and put the
ink as its first child; (2) make the rows `position: relative`; (3) set `--glide-ink-bg`
and `--glide-ink-radius` on the container to today's active background and row radius;
(4) under `[data-glide-ready]`, make the active row's own background transparent. It calls
`useGlideInk` with the arguments of the table in §6.3.

### 6.1 Tab bars — `frontend/src/components/ui/TabBar.vue`

Covers every call site: `SessionView.vue`, `UsageGraphDialog.vue`, `WorktreeDialog.vue`,
`ProjectDetailPanel.vue`, `ProjectEditDialog.vue`, `LayoutOverlay.vue`,
`ProjectAgentDefaultsSection.vue`, `TerminalPanel.vue`, `WorkflowsPane.vue`,
`DockRegion.vue`. No call site is changed.

Today (Web Awesome 3.3.1, `tab-group.styles`): the marker is a border on the active tab,
`.tab-group-top ::slotted(wa-tab[active]) { border-block-end: solid var(--safe-track-width)
var(--indicator-color); margin-block-end: calc(-1 * var(--safe-track-width)) }`. The
`.tabs` part is `position: relative` and sits inside the `.nav` scroller. Several call
sites set `::part(tabs) { align-items: center }`, so a tab can be shorter than the strip:
the ink copies the active tab's own box and draws the same bottom border, so the line stays
exactly where it is today.

**Ink:** the `::after` of `::part(tabs)`, in TabBar's scoped style:

```css
.tab-bar::part(tabs)::after {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    box-sizing: border-box;
    width: var(--glide-w, 0px);
    height: var(--glide-h, 0px);
    translate: var(--glide-x, 0px) var(--glide-y, 0px);
    border-block-end: var(--safe-track-width) solid var(--indicator-color);
    pointer-events: none;
    opacity: 0;
    transition: var(--glide-fade);
}
.tab-bar[data-glide-ready]::part(tabs)::after {
    opacity: 1;
    transition: var(--glide-transition);
}
.tab-bar[data-glide-instant]::part(tabs)::after {
    transition: none;
}
/* The ink draws the line: the active tab keeps its border width (no layout shift) but not
   its color. Direct children only, so a nested TabBar keeps its own. */
.tab-bar[data-glide-ready] > :deep(wa-tab[active]) {
    border-block-end-color: transparent;
}
```

`--safe-track-width` and `--indicator-color` are declared on Web Awesome's `:host` and
inherit into the `.tabs` element and its pseudo-element. A nested `TabBar` has its own
host: `.tab-bar::part(tabs)` only matches the matched host's own parts, and its own
`--glide-*` properties (`0px` from its controller's creation, §4.3) shadow the inherited
ones; its ink stays hidden and zero-size until its own first placement.

**Controller (in TabBar's `onMounted`, after `updateComplete`, next to the existing
`navEl` lookup):** `createGlideInk` directly (the element comes from the shadow root, not
a template ref):

- `container` = `el.shadowRoot.querySelector('[part~="tabs"]')`; `target` = `el`;
  `flushTarget` = the same tabs element, `flushPseudo` = `'::after'`.
- `getActive()` = `el.placement === 'top'` ? the first `:scope > wa-tab[active]` of `el`
  : `null`.
- `getItems()` = every `:scope > wa-tab` of `el`. A tab before the active one can change
  width without any other trigger (the Chat tab's pending-request hand icon and
  code-comments indicator, a subagent tab's process indicator, a dock tab label, a
  terminal tab hidden by class): the observer sees it and the active tab's line snaps to
  its new place.
- No reset key: a newly active tab in the same bar glides, including a tab dropped into
  the bar from another dock.
- Triggers: the controller's `ResizeObserver`, plus a **second** `MutationObserver` on `el`
  with `{ attributes: true, attributeFilter: ['active'], subtree: true }`; its callback
  calls `update()` when a mutation's target is a `WA-TAB` whose `closest('wa-tab-group')`
  is `el` (same filter as Web Awesome's own observer). Web Awesome reflects `active` on
  `wa-tab`, and every path that changes the active tab (click, keyboard, the `active`
  property, the first-visibility pick) goes through `setActiveTab`, which sets it. A
  subtree `childList` observation is not used: it would fire on every change inside the
  tab panels (the chat).
- The existing `listObserver` (`childList`, no subtree, reorder → `ensureActiveVisible`)
  also calls `update()` in its callback (a reorder, an added or a removed tab moves the
  active one: same element → snap). That `update()` also syncs the observed tabs (§4.3
  step 9), so an added tab is watched from then on.
- Not `wa-tab-show`: a remounted `DockRegion` (KeepAlive return) does not replay it
  (memory note), and the first-visibility pick can run without events.
- `onBeforeUnmount`: set an `unmounted` flag, disconnect the new observer, `destroy()` the
  controller. `onMounted` awaits `updateComplete` before creating them: when the flag is
  set after the await, it creates nothing (an unmount during the await would otherwise
  leak both observers). The existing `navEl` / `listObserver` setup takes the same guard.
- If the tabs element is absent (Web Awesome internals changed), no controller: Web
  Awesome's border stays (same no-op rule as `navEl`).

Hidden bars (inactive dock panel, `ProjectEditDialog`'s `hide-nav`, a KeepAlive-detached
view) have a zero-size tabs element: `hide`, then `snap` when shown again.

### 6.2 Settings nav — `frontend/src/components/app/SettingsPopover.vue`

Today the chosen section only changes its text color (`.settings-nav-item.active`: brand
color, semibold). New: a light accent fill glides under it (`--glass-item-hover`, the
lighter of the two glass row tints; the popover is glass).

- Container: `nav.settings-nav` (it scrolls); add `ref="navRef"`, `position: relative`,
  and `<span ref="navInkRef" class="glide-ink settings-nav-ink" aria-hidden="true">` as its
  first child. `--glide-ink-bg: var(--glass-item-hover)`, `--glide-ink-radius:
  var(--wa-border-radius-m)` (the items' radius).
- `.settings-nav-item`: add `position: relative` **after** its `all: unset` (which would
  reset it otherwise, leaving the ink over the labels). The active item keeps its text color and
  weight; it has no background today, so nothing is made transparent.
- `getActive` = `navRef.value?.querySelector('.settings-nav-item.active')`; `getItems` =
  every `.settings-nav-item` of `navRef` (a badge appearing on an item above, e.g. the
  peers inbox count, can change its height); `sources` = `[activeSection, sections,
  hasTips, hasHelp]`; no reset key.
- The scroll shadows (`.settings-nav::before/::after`, sticky, `z-index: 2`) stay above
  the ink.
- Mobile (`width < 640px`, the nav is a drill-down list and `.active` is neutralised):
  `.settings-nav-ink { display: none }` inside the existing media query.
- The popover's hover on items (`--glass-item-hover`) is unchanged; on the active item it
  adds to the ink (a darker fill under the pointer).

### 6.3 Keyboard lists

| Site | Container (add `ref` if absent) | Rows (`position: relative`) | `getActive` | `sources` | `resetKey` | Ink bg / radius | Made transparent under `[data-glide-ready]` |
|---|---|---|---|---|---|---|---|
| `components/app/CommandPalette.vue` | `.palette-list` (`listRef`) | `.command-item` | `.command-item.active` | `[activeKey, visibleItems, navEpoch]` | `` `${parentCommand?.id ?? ''}\|${query}\|${navEpoch}` `` (see notes) | `--glass-item-highlight` / `--wa-border-radius-s` | `.command-item.active` |
| `components/app/SessionSwitcher.vue` | `.switcher-list` (`listRef`) | `.switcher-row` | `.switcher-row--active` | `[cursor, rows]` | `mode` | `--glass-item-highlight` / `--wa-border-radius-m` | `.switcher-row--active` (background only; its text color stays) |
| `components/app/SearchOverlay.vue` | `.search-results` (new `resultsRef`) | `.search-result-card` (already relative) | `.search-result-card.selected` | `[selectedIndex, results]` | `results.value` (array identity: a new search or an appended page replaces it) | `--glass-item-highlight` / `--wa-border-radius-s` | `.search-result-card.selected` |
| `components/message/CommandPickerPopup.vue` | `.picker-list` (`listRef`) | `.picker-item` | `.picker-item.active` | `[activeIndex, filteredCommands]` | `searchQuery` | `--glass-item-highlight` / `0` | `.picker-item.active` and `.picker-item:hover` |
| `components/message/MessageHistoryPickerPopup.vue` | `.picker-list` (`listRef`) | `.picker-item` | `.picker-item.active` | `[activeIndex, filteredMessages]` | `searchQuery` | `--glass-item-highlight` / `0` | `.picker-item.active` and `.picker-item:hover` |

Notes:

- `getActive` queries inside the container ref (never `document`).
- `getItems` = every row of the "Rows" column inside the container (and, for the settings
  nav, every `.settings-nav-item`): a row above the active one that changes height (a
  wrapped label, a late badge) moves it without resizing the container. Syncing a few
  hundred observed rows per update is a set difference, negligible next to the render.
- The pickers' `:hover` background is made transparent too: a `mouseenter` makes the
  hovered row active, so the ink already marks it; a separate hover fill would sit on top
  of the arriving ink.
- Palette: the sticky group headers (`.category-sticky`, `z-index: 1`) stay above the ink
  and the rows; `scroll-margin-top` and `scrollIntoView` are unchanged (the ink uses
  content coordinates, so the scroll does not move it relative to its row).
- Palette `navEpoch`: a new `ref(0)` in `CommandPalette.vue`. `goBack()` changes
  `parentCommand` and `query` (the post watcher snaps the ink to the first row), then in
  its `nextTick` sets `activeKey = parentId`. That second change must snap too ("back" is
  a new list, not a move): `goBack`'s `nextTick` callback increments `navEpoch` in the same
  tick as it sets `activeKey`, so both reach the same post-flush `update()` with a new
  reset key. `navEpoch` is incremented whether or not the parent is found, and is one of
  the palette's `sources`: when `activeKey` does not change (parent not found, or already
  the first row), the epoch change alone still runs `update()`, so the stored reset key is
  never stale and the next ↓ glides.
- Search: `.search-results` is rendered under `v-else-if` (§4.4 handles it). A visited
  card has `opacity: 0.5`; today its selected background is dimmed with it. The ink sits
  behind the card, so it is dimmed by a rule instead:
  `.search-results[data-glide-ready]:has(> .search-result-card.selected.visited) > .glide-ink
  { opacity: 0.5 }` (its specificity beats the ready rule; `:has()` is in Firefox 121+).
- Switcher: the panel is under `v-if="visible"`; each opening creates the controller and
  places the ink with a snap.
- A dialog or popup that closes hides its container (zero size): `hide`; the next opening
  snaps.

## 7. `SegmentedControl` — new `frontend/src/components/ui/SegmentedControl.vue`

A single-choice control drawn as a row of segments inside one bordered frame; the checked
segment's fill glides (mock `.seg`). It wraps a `wa-radio-group` with `appearance="button"`
radios, so keyboard (arrows), focus and form semantics stay Web Awesome's.

### 7.1 API

```js
props: {
    modelValue: { type: String, required: true },
    options: { type: Array, required: true },   // [{ value: String, label: String, icon?: String }]
    label: { type: String, required: true },    // accessible name of the radiogroup (not shown)
    size: { type: String, default: 'small' },
}
emits: ['update:modelValue']
```

### 7.2 Template

```html
<div ref="frameRef" class="segmented-control">
    <span ref="inkRef" class="glide-ink" aria-hidden="true"></span>
    <wa-radio-group
        :label="label"
        :size="size"
        orientation="horizontal"
        :value.prop="modelValue"
        @change="onChange"
    >
        <wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value">
            <wa-icon v-if="option.icon" :name="option.icon" class="segmented-icon"></wa-icon>
            {{ option.label }}
        </wa-radio>
    </wa-radio-group>
</div>
```

`onChange(event)`: ignore it unless `event.target === event.currentTarget`; then
`emit('update:modelValue', event.target.value)`.

`useGlideInk({ container: frameRef, flushTarget: inkRef, getActive, getItems, sources: [() =>
props.modelValue, () => props.options] })` with `getItems` = every `wa-radio` of
`frameRef`, and `getActive` = the `wa-radio` of
`frameRef` whose `value` **property** equals `props.modelValue` (Vue sets `:value` as a
property; Web Awesome reflects it to the attribute only in its own later update, after
Vue's post-flush watchers). The segment's box does not
depend on its checked state, so Web Awesome's asynchronous check update does not matter.

### 7.3 Style (scoped)

```css
.segmented-control {
    --segmented-pad: 0.1875rem;
    --glide-ink-bg: var(--wa-color-brand-fill-normal);
    --glide-ink-radius: var(--wa-border-radius-s);
    position: relative;
    display: inline-flex;
    padding: var(--segmented-pad);
    border: var(--wa-form-control-border-width) var(--wa-form-control-border-style) var(--wa-form-control-border-color);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-form-control-background-color);
}
wa-radio-group {
    position: relative; /* paints above the ink, tree order */
}
wa-radio-group::part(form-control-label) {
    /* the accessible name stays; the caller shows its own visible label */
    position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap;
}
wa-radio-group::part(form-control-input) {
    gap: var(--segmented-pad);
}
wa-radio[appearance='button'] {
    margin: 0;
    border-color: transparent;
    border-radius: var(--glide-ink-radius);
    background-color: transparent;
    /* the awesome theme's hard offset shadow, and its checked "pressed" shift */
    box-shadow: none;
    transform: none;
    color: var(--wa-color-text-quiet);
    /* the frame's padding and border are inside the control height */
    min-height: calc(var(--wa-form-control-height) - 2 * (var(--segmented-pad) + var(--wa-form-control-border-width)));
}
wa-radio[appearance='button']:state(checked) {
    color: var(--wa-color-brand-on-quiet);
}
.segmented-control:not([data-glide-ready]) wa-radio[appearance='button']:state(checked) {
    background-color: var(--glide-ink-bg);
}
@media (hover: hover) {
    wa-radio[appearance='button']:hover:not(:state(checked), :state(disabled)) {
        background-color: var(--glass-item-hover);
    }
}
.segmented-icon {
    margin-inline-end: var(--wa-space-2xs);
}
```

A document (here: scoped) rule on the `wa-radio` host beats Web Awesome's `:host(...)`
rules, so the checked fill, the joined-button radii and the overlap margins are all
replaced. The focus ring (`outline` on `:host(:focus-visible)`) is untouched.

The themes also style button radios, in cascade layers that any unlayered rule beats:
- **awesome** (`styles/themes/awesome.css`, `@layer wa-theme-dimension`): a hard offset
  `box-shadow` plus `margin-bottom`/`margin-right` on every button radio of a horizontal
  group, and on the checked one `box-shadow: initial; transform: translate(...)` (4px
  down). Replaced by `margin: 0`, `box-shadow: none`, `transform: none` above. Without
  them, the checked segment's box would move after Web Awesome's asynchronous check
  update and the ink would be measured 4px off (the trap of roadmap §6d.2: an exposed
  theme `transform`).
- **shoelace** (`styles/themes/shoelace.css`): checked `background-color` and
  `color: var(--wa-color-brand-on-loud)`, plus size-based font and height tokens.
  The first two are replaced by the rules above; the size tokens are kept (the
  `min-height` calc reads them).
- **default:** no radio rule.
With these overrides, a segment's box does not depend on its checked state in any theme.

### 7.4 Consumers

- **`components/message/AgentSettingsBenchmarkTask.vue`:** the `wa-radio-group.task-favor`
  becomes
  `<SegmentedControl class="task-control task-favor" label="Favor" :model-value="store.favor"
  :options="FAVOR_OPTIONS" @update:model-value="store.setFavor" />` with
  `FAVOR_OPTIONS = [{ value: 'cost', label: 'Cost' }, { value: 'speed', label: 'Speed' }]`.
  `onFavorChange` and the `.task-favor::part(form-control-label)` rule are removed (the
  component hides its own label). `.task-control` and `.task-favor` (`justify-self: start`)
  stay.
- **`components/orchestration/OrchestrationPanel.vue`:** the `wa-button-group.orch-view-switch`
  becomes `<SegmentedControl v-if="canSwitchView" class="orch-view-switch" label="Tree to
  show" :model-value="view" :options="VIEW_OPTIONS" @update:model-value="selectedView =
  $event" />` with `VIEW_OPTIONS = [{ value: 'sessions', label: 'Sessions', icon:
  'diagram-project' }, { value: 'agents', label: 'Subagents', icon: 'robot' }]`.
  `.orch-view-switch { flex-shrink: 0 }` stays.

Both files import the component. `wa-radio-group` and `wa-radio` are already imported in
`main.js` (used today by the benchmark task).

## 8. Invariants

1. The active item is always marked: its own marker until the target has
   `data-glide-ready`, the ink after (same frame, §5).
2. A glide only happens between two different active elements of the same list content,
   with the ink already shown; everything else snaps (§4.2).
3. The ink lives in the scrolled content (tab strip, list): scrolling never desynchronises
   it.
4. A `TabBar` never marks or hides another `TabBar`'s tabs (host-scoped `::part`, direct
   children only).
5. Under reduced motion, no movement: `--glide-transition: none`; the fade stays.
6. No user-agent test; no feature missing in Firefox 156.
7. `useGlideInk` and `createGlideInk` never read `document`: every query is scoped to the
   container or the host.
8. The box is in layout pixels: an ancestor's `scale` (opening animations) never leaves
   the ink distorted; during the animation it is within 0.5px, and the settle loop makes
   it exact at the end (§4.2, §4.3 step 10).
9. A hidden ink never keeps a list's scroll range: it collapses to a zero box after its
   fade (§4.3 step 3).

## 9. Tests (node:test, `npm test`)

- **`frontend/src/utils/glideInk.test.js`:**
  - `relativeBox`: plain; with `scrollTop`/`scrollLeft`; with `clientTop`/`clientLeft`;
    **scaled container** (visual rects at 0.8 of `offsetWidth`/`offsetHeight` → the same
    layout box as unscaled); `offsetWidth`/`offsetHeight` 0 → ratio 1; **rounding**:
    a rect of 812.4 × 31.5 against `offsetWidth`/`offsetHeight` 812 × 32 gives ratio 1 and
    the exact box (`scaleRatio(812.4, 812) === 1`, `scaleRatio(31.5, 32) === 1`,
    `scaleRatio(640, 800) === 0.8`).
  - `placementMode`: each row of the §4.2 table, plus a `resetKey` change with a new
    active element (`snap`), and `NaN`/object keys through `Object.is`.
  - `sameBox`: equal; a difference under 0.01px counts as equal
    (`24.000000000000004` vs `24`); one value 0.5px off; one side `null`; both `null`.
  - `relativeBox` uses one ratio for both axes: a container 400 × 32 at scale 0.98
    (rect 392 × 31.36) gives ratio 0.98 on both axes (the short axis alone would have
    given 1). Scaled-box assertions compare with a 1e-9 tolerance.
  - Controller with a fake env (fake elements with `getBoundingClientRect`, `style`
    recording `setProperty`, attribute set; a fake `ResizeObserver` that records observed
    elements and can be fired; a `getComputedStyle` spy):
    - `createGlideInk` itself runs the first `update()` (no explicit call in the test);
    - first `update()`: `data-glide-instant` set, the four properties written in `px`,
      `data-glide-ready` set, flush called **after** the writes and **before**
      `data-glide-instant` is removed; `flushPseudo` passed through;
    - new active element: properties rewritten, `data-glide-instant` never set, no flush;
    - same active element, new rect: snap sequence;
    - reset key changed with a new active element: snap sequence;
    - `getActive()` returns `null`: `data-glide-ready` removed at once, the four
      properties still at the old box until the fake timer fires with the `--motion-dur-1`
      delay, then `0px`; the next placement snaps;
    - a placement before the collapse timer fires cancels it (properties keep the new box);
    - zero-size container: `hide` and the collapse is immediate (no timer); zero-size
      active element: `hide`;
    - creation writes `0px` to the four properties before anything else;
    - **created hidden** (zero-size container): the container is observed anyway; firing
      the fake observer after giving it a size runs the snap sequence;
    - unchanged box: no write, no flush;
    - the observer follows the active element (old one unobserved, new one observed), also
      observes every `getItems()` element (an item dropped from the list is unobserved), and
      a fired resize calls `update()`;
    - a sibling item resized so that the active element's rect moves → snap sequence with
      the new box;
    - **settle** (fake `requestAnimationFrame`): a measure with a container rect of
      640 × 320 against `offsetWidth`/`offsetHeight` 800 × 400 starts one loop (a second
      measure does not start another, nor does a settle update while still scaled); the
      frames write nothing; a frame within 0.5px of the layout size that still differs
      from the previous frame does **not** stop the loop; the next frame with an equal
      rect does, then runs exactly one settle update, which for the same active element
      writes without `data-glide-instant` and without flush; a first frame with the same
      (still scaled) rect as the starting measure does **not** stop the loop, nor does a
      second; a rect that stays scaled and stable stops it at the 3rd frame (fallback);
      the cap (a rect that keeps changing) stops it at the first frame where both 60
      frames (frame 1 included) and 500ms since frame 1's timestamp have passed — 60 frames 2.8ms apart do not stop
      it, nor do 500ms in fewer than 60 frames; an unscaled container (812.4 against 812) starts no loop; `hide`
      and `destroy()` cancel the frame; a new active element during the loop still glides;
    - `destroy()`: timer cleared, observer disconnected, attributes and properties removed.
- **`frontend/src/composables/useGlideInk.test.js`** (in an `effectScope()`, with
  `ref`/`nextTick` from `vue` and the injectable env): the controller is created when the
  container ref gets an element, recreated when it changes, destroyed when it becomes
  `null` and when the scope stops; a change of a `sources` ref triggers `update()` after
  the flush; `env` reaches the controller.
- **`frontend/src/styles/motion.test.js`** (existing, updated): test 3 expects the
  reduced-motion `:root` block to hold exactly `--motion-amount: 0`,
  `--motion-ease-spring: var(--motion-ease)` and `--glide-transition: none`; test 8
  exempts the `.glide-ink` rule from the `--motion-amount` check (a measured placement,
  §5), asserts the header states that exception, and still applies every other invariant
  to it (no `transform`). No other exemption is added.
- **`frontend/src/utils/glideInk.test.js`** also asserts that `createGlideInk` without an
  `env` reads `globalThis` (source text: `env = globalThis`).
- **`frontend/src/styles/glide.test.js`** (text tests on the files, like
  `details-motion.test.js`):
  - `motion.css`: `--glide-transition` (translate, width, height; `--motion-dur-3`,
    `--motion-ease-spring`), `--glide-fade`, `--glide-transition: none` inside the
    reduced-motion `:root`, the three `.glide-ink` rules in the order base → ready →
    instant.
  - `TabBar.vue`: the four ink rules (`::part(tabs)::after`, ready, instant, the
    transparent `border-block-end-color` on `> :deep(wa-tab[active])`); the `active`
    attribute observer with `subtree: true` and `attributeFilter: ['active']`; no
    `childList` + `subtree` observer; `getActive` checks `placement === 'top'`;
    `getItems` returns the host's `:scope > wa-tab`.
  - `CommandPalette.vue`: `navEpoch` in the reset key and incremented inside `goBack`'s
    `nextTick` callback, next to `activeKey.value = parentId`.
  - `SearchOverlay.vue`: the visited `:has()` dimming rule.
  - Each §6.3 site and `SettingsPopover.vue`: a `glide-ink` element, a `useGlideInk(`
    call with a `getItems`, the rows' `position: relative` (for `.settings-nav-item`:
    declared after `all: unset` in the same rule), the transparent active-row rule under
    `[data-glide-ready]` (not for the settings nav), the pickers' `:hover` rule, the
    settings mobile `display: none`.
  - `SegmentedControl.vue`: `appearance="button"`, the ink, the not-ready checked fill, the
    `event.target === event.currentTarget` guard; `getActive` compares the radio's `value`
    property (no `getAttribute('value')`); the button-radio rule sets `box-shadow: none`
    and `transform: none`.
  - `TabBar.vue`: the `unmounted` guard after `await … updateComplete`.
  - `CommandPalette.vue`: `navEpoch` among the `useGlideInk` sources.
  - `AgentSettingsBenchmarkTask.vue` and `OrchestrationPanel.vue`: use `SegmentedControl`;
    no `wa-radio-group` / `wa-button-group` left for these controls.
  - Web Awesome guard (same resolution as `detailsMotion.test.js`: `require.resolve` of the
    package root, exact chunk paths): `@awesome.me/webawesome` is 3.3.1;
    `dist/chunks/chunk.R3LBB5FI.js` (tab-group styles) contains
    `::slotted(wa-tab[active])` with `border-block-end: solid var(--safe-track-width)
    var(--indicator-color)` and `.tabs` with `position: relative`;
    `dist/chunks/chunk.PVB6QGII.js` (tab-group component) contains `part="tabs"`; the
    `wa-radio-group` component chunk dispatches `change` on itself
    (`dist/chunks/chunk.PTCJO2KT.js`); the radio-group styles chunk
    (`dist/chunks/chunk.OJS3MSZI.js`) declares `[part~='form-control-input']` with
    `display: flex`; the radio styles chunk (`dist/chunks/chunk.BELHQIBT.js`) contains the
    `:host([appearance='button'])` margin, radius and `:state(checked)` rules. The
    implementer confirms each chunk name and the exact string asserted by reading the file
    (the names are the ones found at review time).
- Every new assertion is checked with a mutant that makes it fail (roadmap §5: prove the
  mutant landed).

## 10. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

1. **Session tabs** (center bar): click from the first to the last tab — the line glides
   with a slight spring; keyboard ←/→ on a focused tab glides too.
2. **Overflowing tab strip** (narrow window, many dock tabs): the line scrolls with the
   tabs, the chevrons still work, the wheel still scrolls.
3. **Dock tabs:** drag a tab to reorder: the line follows the active tab without gliding
   from a stale place; move the active tab to another dock: the target bar's line glides
   from its previous active tab to the dropped one. On the Chat tab bar, while another tab
   is active, trigger a pending question (the Chat tab gains its hand icon): the line
   stays under the active tab.
4b. **Dialogs and popovers opening** (palette, search, settings, agent settings with the
   benchmark, usage graph, worktree and project edit dialogs): during and after the
   opening animation, the ink follows the active item within half a pixel (not smaller,
   not shifted); after it, the ink sits exactly on it.
4. **Nested bar** (a terminal's own tabs inside a dock panel): each bar marks only its own
   active tab.
5. **Return to a session** (KeepAlive) and **reopen a closed dock**: no glide from a
   corner.
6. **Font size 12 / 18**, **awesome theme** (4px borders), **dark mode**: the line has the
   same thickness and place as today's border.
7. **Settings:** switch sections — the light fill glides; open the popover again — no
   glide on opening; scroll the nav — the fill stays on its item; mobile width — no fill.
8. **Palette:** hold ↓ — the highlight follows smoothly (retargets, no stutter); it passes
   under the sticky group headers; hover rows — it follows the pointer; type a filter — it
   jumps to the first match without gliding; drill into a command and back — no glide.
9. **Switcher** (the MRU switcher shortcut), **search overlay** (↑/↓, hover, a new search,
   a second page), **`/` picker** and **history picker** (↑/↓, hover, type).
10. **Empty filter** in the palette and a picker: arrow down far in a long list, then type
    a filter with no match — the ink fades out and the "No matching…" line is visible
    (the list is not left scrolled); clearing the filter brings the ink back without a
    glide.
11. **Segmented controls:** agent settings popover → benchmark → Cost/Speed (click, and
    arrow keys after a Tab into the group); orchestration panel → Sessions/Subagents. The
    fill glides; the frame has the same height as a small button; focus ring visible.
    Repeat under the **awesome** and **shoelace** themes: no offset shadow, the checked
    label does not move, the fill sits exactly on it.
12. **Reduced motion** (OS setting): every ink goes to its place at once; the palette
    fade-out on an empty filter still fades.
13. **Mobile (touch):** tab bars and settings drill-down unchanged except the gliding line.

## 11. Limitations

- A `hide` during a running glide (↓, then within 380ms a filter with no match) cancels
  the glide: removing `data-glide-ready` drops `translate`/`width`/`height` from the
  transition list, so the ink jumps to the glide's destination and fades there (120ms).
  Accepted: freezing it would need reading the in-flight computed values, for a rare,
  sub-second case.

- The ink's look in the tab bars is Web Awesome's border (no rounded ends): the mock's
  rounded, glowing ink is step 6.
- Tab bars with a non-`top` placement keep Web Awesome's jumping border (none exists).
- The out-of-scope lists (§3) keep their jumping highlight.

## 12. Delivery

One commit after the user's browser review and explicit "commit":
`feat(ui): gliding indicators for tabs, lists and a SegmentedControl`. Then the roadmap:
status row 4 (4c done), §6f (what it does, lessons), test count.
