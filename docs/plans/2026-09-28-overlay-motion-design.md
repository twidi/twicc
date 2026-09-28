# Overlay entrances and exits — design (visual refresh, step 5b)

## 1. Context

Step 5 of the "Signature" visual refresh is split in three sub-steps: 5a (live chat
entrances + chat skeleton, done: `docs/plans/2026-09-28-chat-entrances-skeleton-design.md`,
commit `f8cee943`), **5b (this document)**: how overlays open and close, 5c (session-list
cascade, tab-panel crossfade, light/dark reveal). Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox parity**,
**reduced motion is reduced, not none**), §6c.2 (glass lessons), §6d–§6g.

Tokens (`frontend/src/styles/motion.css`): `--motion-dur-1/2/3` (120/200/380ms),
`--motion-ease`, `--motion-ease-out`, `--motion-ease-spring` (`linear()`; reduced motion
turns it into `--motion-ease`), `--motion-amount` (1; 0 under reduced motion).

Mock reference (`fx-enter` block of `mock.css`, roadmap §3 and §9 "Step 5"):

```css
@keyframes pop-in { from { opacity: 0; transform: scale(.94) translateY(-.25rem); } }      /* menus, 180ms ease-out */
@keyframes pop-out { to { opacity: 0; transform: scale(.96) translateY(-.125rem); } }     /* 120ms ease-in */
@keyframes pop-up { from { opacity: 0; transform: scale(.96) translateY(.5rem); } }       /* settings, 260ms spring */
@keyframes pop-down { to { opacity: 0; transform: scale(.97) translateY(.375rem); } }     /* 150ms ease-in */
@keyframes dialog-in { from { opacity: 0; transform: translateY(.75rem) scale(.96); } }   /* 320ms spring */
@keyframes dialog-out { to { opacity: 0; transform: translateY(.375rem) scale(.98); } }   /* 160ms ease-in */
@keyframes palette-in { from { opacity: 0; transform: translateY(-.5rem) scale(.97); } }  /* 260ms spring */
@keyframes backdrop-in { from { background-color: transparent; backdrop-filter: blur(0); } } /* 240ms ease-out; out 180ms */
@keyframes toast-in { from { opacity: 0; transform: translateY(-130%) scale(.96); } }     /* 520ms spring */
@keyframes toast-out { to { opacity: 0; transform: translateY(-60%) scale(.96); } }       /* 220ms ease-in */
@keyframes tip-in { from { opacity: 0; translate: -50% -.25rem; } }                      /* 160ms ease-out, after 250ms */
```

### 1.1 User decisions — do not reopen

- Target (roadmap §8.7): menus pop open from where they were clicked; dialogs spring in; the
  command palette drops in; the settings panel grows from its button; closing is a quick
  fade; toasts drop in from the top center with a small bounce and fade up when leaving.
- **Tooltip delay: 250ms** (today Web Awesome's 150ms), set once in `AppTooltip`
  (2026-09-28).

## 2. Today (checked, Web Awesome 3.3.1)

- Web Awesome animates an overlay by adding a class to one element of its shadow DOM; the
  class sets a CSS `animation` whose `@keyframes` live in the component's shadow styles
  (`animateWithClass`, `dist/chunks/chunk.L6CIKOFQ.js`). The promise resolves on the
  first `animationend` / `animationcancel` event reaching that element, or after one frame
  when `el.getAnimations()` is empty. A `::backdrop` animation fires its `animationend` on
  its originating `<dialog>` too (measured in Chrome and Firefox 156): on a dialog,
  whichever of the element and its backdrop ends first resolves the promise, removes the
  class (cutting the other animation) and lets the dialog continue (`wa-after-show`, or the
  close). Web Awesome gives both the same duration. The hide animation always completes before the component
  hides the element, so exits already run in Firefox.

| Component | Animated element (its shadow) | Classes | WA keyframes | WA duration |
|---|---|---|---|---|
| `wa-dialog` | `.dialog` (`part="dialog"`) and its `::backdrop` | `show` / `hide` (+ `pulse`) | opacity + `scale: .8` / backdrop opacity | 200ms |
| `wa-dropdown` | `#menu` | `show` / `hide` | opacity + `scale: .9` | 50ms |
| `wa-dropdown-item` submenu | `#submenu` (`part="submenu"`) | `show` / `hide` | `submenu-show` | 50ms |
| `wa-popover` | the inner `wa-popup`'s `.popup`; the `wa-popup` host has class `popover` | `show-with-scale` / `hide-with-scale` | opacity + `scale: .8` | 100ms |
| `wa-tooltip` | same, `wa-popup` host class `tooltip` | `show-with-scale` / `hide-with-scale` | opacity + `scale: .8` | 100ms |
| `wa-select` | same, `wa-popup` host class `select` | `show` / `hide` | opacity | 100ms |

  The `wa-popup` styles hold `.show`, `.hide`, `.show-with-scale`, `.hide-with-scale` and
  their keyframes (`chunk.NUEKQX75.js`). Hide = the same keyframes with `reverse`, same
  `ease`. Transform origins follow the placement (dropdown: the current, flipped
  placement, and the select: `.select[data-current-placement…]::part(popup)` on its inner
  `wa-popup`; popover and tooltip: the requested one). None of these components
  honours reduced motion: the scale runs anyway.
- Web Awesome components are Lit elements; `createRenderRoot()` adopts
  `this.constructor.elementStyles` into each new shadow root, and a `CSSStyleSheet` entry is
  accepted as is (`@lit/reactive-element`). `elementStyles` is finalized when the element
  is defined, before any instance exists.
- ES module imports are evaluated before the body of `main.js`, so code in that body runs
  after every `wa-*` element is defined and before the app mounts (step 4b's
  `installDetailsMotion()` already relies on this).
- The `/` command picker, the message-history picker, the file picker and the directory
  picker are bare `<wa-popup :active="isOpen">` (no animation: `active=false` is
  `display: none` at once). The session switcher is a teleported `v-if` overlay. The
  command palette and the search overlay are `wa-dialog`s.
- Toasts: Notivue 2.4.5 with its default `animations.css` (`transform` keyframes on the
  notification container); under reduced motion Notivue skips the enter/leave classes
  entirely (instant). `AppTooltip` never sets `show-delay`.

## 3. Goals

1. Every overlay opens and closes with the mock's motion, adapted per kind (§5), in
   Firefox and Chrome alike. Exception: the pickers get the entrance only and still close
   at once (§6).
2. Reduced motion: the same fades, no movement (translate, scale, spring overshoot).
3. No change to how overlays open, close, focus or dispatch their events: Web Awesome
   keeps its lifecycle and event order, TwiCC only restyles the animation it already runs.
   The events that follow an animation (`wa-after-show`, `wa-after-hide`, Web Awesome's
   own focus steps) move with the new durations (§5.6). A close during an opening behaves
   without cancelling the running animation; unlike today, it may jump or end at once
   (§5, §12).
4. Tooltips show after 250ms.

## 4. Mechanism — TwiCC's styles inside Web Awesome's shadow roots

New `frontend/src/utils/waMotionStyles.js`:

```js
/** One entry per Web Awesome element whose show/hide animation TwiCC restyles. */
export const WA_MOTION_STYLES = { 'wa-dialog': `…`, 'wa-dropdown': `…`,
    'wa-dropdown-item': `…`, 'wa-popover': `…`, 'wa-popup': `…` }

/**
 * Append a constructed stylesheet to each element's Lit `elementStyles`, so every shadow
 * root created afterwards adopts it after Web Awesome's own styles.
 * Skips an element that is not defined (the share viewer and the artifact shell register
 * a subset) or whose `elementStyles` is not an array. Idempotent (a Symbol marker on the
 * constructor).
 * @param {{ registry?: CustomElementRegistry, createSheet?: (css: string) => CSSStyleSheet }} deps
 * @returns {string[]} the tags actually patched
 */
export function installWaMotionStyles({ registry = globalThis.customElements,
    createSheet = css => { const s = new CSSStyleSheet(); s.replaceSync(css); return s } } = {})
```

- Called in the body of `frontend/src/main.js` and `frontend/src/share-session/main.js`
  right **after** `installDetailsMotion()` (`details-motion.test.js` requires
  `installDetailsMotion()` to follow `installGlassArrowGap()` directly), and in
  `frontend/src/artifact-shell/main.js` after its imports, before `createApp`.
- **Why it works:** our sheet comes last in each shadow root's `adoptedStyleSheets`; its
  rules repeat Web Awesome's selectors (same or higher specificity), so its `animation`
  declarations win. The class names, the element and the lifecycle stay Web Awesome's:
  `animateWithClass` still sees a CSS animation start on the class it added and still gets
  its `animationend`. Keyframes declared in the same sheet are found in the shadow scope.
  `--motion-*` custom properties inherit into shadow roots.
- Every keyframe uses individual properties (`translate`, `scale`, `opacity`), multiplies
  each distance and each scale delta by `var(--motion-amount)`, and every movement easing
  is `var(--motion-ease-spring)` or `var(--motion-ease-out)`; exits play as `ease-in`
  (declared `ease-out` with `reverse`, §5).
  `motion.css`'s rule "the spring easing is only for movement; opacity uses
  `--motion-ease`" holds (§5.1). Reduced motion therefore keeps only the fades, through the
  existing tokens, with no media query. Exception: the dialog `pulse` (a feedback nudge,
  not an entrance or exit) uses `--motion-ease`, and a reduced-motion media query swaps it
  for an opacity dip (§5.1).
- Glass (step 3): the animated elements are ancestors of the glass `::before` blur layers;
  during an opacity animation the blur is not drawn (roadmap §6c.2, accepted since step 3).

## 5. Motion per overlay kind (the CSS of `WA_MOTION_STYLES`)

Keyframe names are prefixed `twicc-` (no clash with Web Awesome's `show`, `show-dialog`…).
`A` stands for `var(--motion-amount)`.

**One keyframe per kind, played forward to open and in reverse to close — Web Awesome's
own model.** A close can start while the opening still runs: Web Awesome adds the hide
class next to the show class (the dropdown swaps them). With the same animation name, the
hide rule only changes the running animation's duration, easing, direction and fill: it is
updated in place, never cancelled. (Distinct names would cancel the opening animation;
the `animationcancel` event resolves the pending hide at once, so the overlay would vanish
with no exit, and `wa-after-show` would fire during the close, whatever the timing.) The
exit is therefore the entrance played backwards and shorter; the mock's smaller exit
distances are not kept. **Reversed easing:** `animation-direction: reverse` also reverses
the timing function (MDN: "an ease-in easing function becomes ease-out"), so the hide rules
declare `ease-out` to get the mock's `ease-in` exit (slow start, fast end).

What a close during an opening looks like: the updated animation keeps its start time and
re-reads the elapsed time `t` against the exit's timing. Web Awesome's own show and hide
last the same, so today it always turns around smoothly. Here every exit is shorter than
its entrance (§5.6): a close with `t` shorter than the exit duration jumps to the reversed
position (`1 - t/exit`) and fades from there — **up** toward full visibility when the close
is very early (before about `entrance × exit / (entrance + exit)`: ~40ms for menus, ~70ms
for popovers, ~100ms for dialogs), down otherwise; a close with `t` at least the exit duration ends
at once (the animation is already past its end). Accepted (§12): the "quick close"
the mock asks for is worth more than this rare case (Escape on the palette in its first
quarter-second, a tooltip left within 160ms of appearing).

Order and specificity: in each sheet, every hide rule comes **after** the show rule it
refines, with at least its specificity, so it wins while both classes are present.

### 5.1 Dialogs — `wa-dialog`

```css
.dialog.show { animation: twicc-dialog 320ms var(--motion-ease-spring), twicc-fade 320ms var(--motion-ease); }
.dialog.show::backdrop { animation: twicc-fade 320ms var(--motion-ease-out); }
.dialog.hide { animation: twicc-dialog 160ms ease-out reverse forwards, twicc-fade 160ms ease-out reverse forwards; }
.dialog.hide::backdrop { animation: twicc-fade 160ms ease-out reverse forwards; }

:host(.motion-drop) .dialog.show { animation: twicc-drop 260ms var(--motion-ease-spring), twicc-fade 260ms var(--motion-ease); }
:host(.motion-drop) .dialog.show::backdrop { animation: twicc-fade 260ms var(--motion-ease-out); }
:host(.motion-drop) .dialog.hide { animation: twicc-drop 160ms ease-out reverse forwards, twicc-fade 160ms ease-out reverse forwards; }
:host(.motion-drop) .dialog.hide::backdrop { animation: twicc-fade 160ms ease-out reverse forwards; }

.dialog.pulse { animation: twicc-pulse 250ms var(--motion-ease); }
@media (prefers-reduced-motion: reduce) {
    .dialog.pulse { animation: twicc-pulse-dim 250ms var(--motion-ease); }
}

@keyframes twicc-dialog { from { translate: 0 calc(0.75rem * A); scale: calc(1 - 0.04 * A); } }
@keyframes twicc-drop { from { translate: 0 calc(-0.5rem * A); scale: calc(1 - 0.03 * A); } }
@keyframes twicc-fade { from { opacity: 0; } }
@keyframes twicc-pulse { 50% { scale: calc(1 + 0.02 * A); } }
@keyframes twicc-pulse-dim { 50% { opacity: 0.85; } }
```

- `motion.css`'s rule "the spring easing is only for movement; opacity uses
  `--motion-ease`": a spring entrance is two animations of the same duration — the movement
  keyframes (no opacity) with the spring, and `twicc-fade` with `--motion-ease`.
  `animateWithClass` resolves on the first `animationend`; equal durations end together.
- `forwards` on exits: the element stays at its end state (the `from` keyframe, since the
  animation runs in reverse) until Web Awesome closes it on `animationend`.
- The backdrop fade carries the glass veil and its blur (`glass.css` paints the veil on
  `::backdrop`). **Its duration equals the dialog's in each state** (320ms, 260ms for
  drop-in dialogs, 160ms on exit): a shorter backdrop would end the dialog's animation
  early (§2), a longer one would be cut when the dialog closes. (The mock's 240/180ms
  backdrop is not kept for that reason.)
- Every rule declares the full `animation` shorthand (no partial refinement). The
  `motion-drop` hide rules are needed: `:host(.motion-drop) .dialog.show` is more specific
  than `.dialog.hide` and would otherwise keep its 260ms during a close.
- `pulse` (click outside a dialog without light dismiss): scale only, as Web Awesome's
  (no opacity animation: it would drop the glass blur); under reduced motion, an opacity
  dip instead.
- **Drop-in dialogs:** the command palette (`CommandPalette.vue`) and the search overlay
  (`SearchOverlay.vue`) get the host class `motion-drop` on their `wa-dialog` (the palette
  adds `class="motion-drop"`; the search overlay appends it to its existing class). They
  drop from above, like the mock's palette.

### 5.2 Menus — `wa-dropdown`, submenus, `wa-select`

```css
/* wa-dropdown */
#menu.show { animation: twicc-pop 180ms var(--motion-ease-out); }
#menu.hide { animation: twicc-pop 50ms ease-out reverse forwards; }
/* wa-dropdown-item: opacity only (see below) */
#submenu.show { animation: twicc-fade 180ms var(--motion-ease-out); }
#submenu.hide { animation: twicc-fade 50ms ease-out reverse forwards; }
/* wa-popup (select list box) */
:host(.select) .popup.show { animation: twicc-pop 180ms var(--motion-ease-out); }
:host(.select) .popup.hide { animation: twicc-pop 100ms ease-out reverse forwards; }
/* wa-popup (color picker panel: host class color-popup) */
:host(.color-popup) .popup.show-with-scale { animation: twicc-pop 180ms var(--motion-ease-out); }
:host(.color-popup) .popup.hide-with-scale { animation: twicc-pop 100ms ease-out reverse forwards; }
:host(.color-popup[data-current-placement='bottom-start']) .popup { transform-origin: left top; }
:host(.color-popup[data-current-placement='bottom-end']) .popup { transform-origin: right top; }
:host(.color-popup[data-current-placement='top-start']) .popup { transform-origin: left bottom; }
:host(.color-popup[data-current-placement='top-end']) .popup { transform-origin: right bottom; }

@keyframes twicc-pop { from { opacity: 0; scale: calc(1 - 0.06 * A); } }
@keyframes twicc-fade { from { opacity: 0; } }   /* in the wa-dropdown-item sheet */
```

- **Submenus fade without scaling.** Web Awesome's submenu "safe triangle" (the zone that
  keeps a submenu open while the pointer travels diagonally to it) is `#submenu::before`,
  `position: fixed` with a viewport `clip-path`. A `scale` animation makes `#submenu` the
  containing block of that fixed layer, which then covers only the submenu: the triangle
  stops working during the entrance, the very moment the pointer travels. Opacity creates
  no containing block.

- `--motion-ease-out` is not the spring: movement and opacity share it (motion.css only
  reserves the spring for movement).
- "From where they were clicked": the scale grows from the placement-based transform
  origin, set by Web Awesome: the dropdown's by `wa-popup[data-current-placement…] #menu`
  (the edge touching the trigger, or its corner for `left/right-start/end` menus), the
  select's by `.select[data-current-placement…]::part(popup)` in the select's shadow (the
  inner `wa-popup` has class `select` and carries `data-current-placement`; an outer
  `::part()` rule beats the popup's own styles). Unused today (the select's animation is
  opacity only), it now orients the scale. The color picker's panel has no Web Awesome
  origin (it would scale from its center): the four `:host(.color-popup[…]) .popup` rules
  set it from the placement its `wa-popup` reaches (`bottom-start` by default; `flip` with
  its fallback can land on `bottom-end`, `top-start`, `top-end`). The placement is set when
  `computePosition` resolves, in microtasks, before the first frame; `stop()` removes it
  only after the hide animation.
- The mock's small extra `translateY` is dropped: its sign would have to follow the
  flipped placement, and the origin already gives the direction.
- Web Awesome's `#submenu.show/.hide` rules sit in a nested block of `#submenu`; ours use
  the flat `#submenu.show` (same specificity, later sheet). TwiCC's one submenu today:
  "Archive sessions older than…" (`SessionsSidebarControls.vue`).

### 5.3 Popovers — `wa-popup` hosts with class `popover`

```css
:host(.popover) .popup.show-with-scale { animation: twicc-grow 260ms var(--motion-ease-spring), twicc-fade 260ms var(--motion-ease); }
:host(.popover) .popup.hide-with-scale { animation: twicc-grow 100ms ease-out reverse forwards, twicc-fade 100ms ease-out reverse forwards; }

@keyframes twicc-grow { from { scale: calc(1 - 0.04 * A); } }
@keyframes twicc-fade { from { opacity: 0; } }
```

- Applies to every popover: settings (placement `top`: grows up from its button), agent
  settings, the `+` menu of the composer, the Git panel header, the share viewer's menu.
- The mock's `translateY` is dropped for the same reason as menus.
- **Origin: the arrow, i.e. the button.** Web Awesome's popover origin is the middle of the
  edge facing the anchor (`.popover[placement^='top']::part(popup) { transform-origin:
  bottom }`), but popovers are positioned with `shift`: a wide panel (settings,
  `--max-width: 90vw`, trigger in the sidebar footer) is pushed sideways, and that middle
  can be far from its button. Every TwiCC popover has an arrow pointing at its anchor, and
  step 3's `utils/glassArrowGap.js` already measures it on each `wa-reposition`, publishing
  on the popover host `data-glass-arrow` (the side the arrow sits on) and
  `--glass-arrow-gap-start/-end` (its base, in body pixels). `computeArrowGap` now also
  returns `center` (`(start + end) / 2`, rounded like the others), and the handler sets
  `--popover-arrow-center: <center>px` next to the gap properties. As today, the no-gap
  branch only removes `data-glass-arrow`; the origin rules below are gated on it, so a
  stale value is never used. A new `wa-popover` entry of `WA_MOTION_STYLES`:

  ```css
  :host([data-glass-arrow='bottom']) .popover::part(popup) { transform-origin: var(--popover-arrow-center, 50%) bottom; }
  :host([data-glass-arrow='top']) .popover::part(popup) { transform-origin: var(--popover-arrow-center, 50%) top; }
  :host([data-glass-arrow='right']) .popover::part(popup) { transform-origin: right var(--popover-arrow-center, 50%); }
  :host([data-glass-arrow='left']) .popover::part(popup) { transform-origin: left var(--popover-arrow-center, 50%); }
  ```

  (more specific than Web Awesome's rule; an outer `::part()` declaration beats the popup's
  own styles). Without a measured arrow, Web Awesome's origin applies. Timing: `wa-popup`
  dispatches `wa-reposition` synchronously at the end of `reposition()`, before its
  `computePosition` resolves and sets `data-current-placement` and the arrow; `stop()`
  removes the placement on each close. So the repositions of the opening task (from
  `autoUpdate`'s first call and from `wa-popup`'s own `updated()`) find no placement
  (`data-glass-arrow` is removed: Web Awesome's origin). The first one that reads the
  placement is floating-ui's `autoUpdate` initial `ResizeObserver` callback, in the first
  rendering frame, before paint: it sets the arrow and the origin before the first painted
  frame. Each `wa-reposition` reads the previous
  computation's placement, so a flip shows one reposition late.

### 5.4 Tooltips — `wa-popup` hosts with class `tooltip`

```css
:host(.tooltip) .popup.show-with-scale { animation: twicc-tip 160ms var(--motion-ease-out); }
:host(.tooltip) .popup.hide-with-scale { animation: twicc-tip 100ms ease-out reverse forwards; }

@keyframes twicc-tip { from { opacity: 0; scale: calc(1 - 0.04 * A); } }
```

(The `wa-popup` sheet holds the select, color-picker, popover and tooltip rules and
declares `twicc-pop`, `twicc-grow`, `twicc-fade` and `twicc-tip` once each. Each shadow root
sees only its own sheet's keyframes, so the `wa-dropdown` sheet declares its own
`twicc-pop`, the `wa-dropdown-item` sheet its own `twicc-fade`, and the dialog sheet its own
`twicc-dialog`, `twicc-drop`, `twicc-fade`, `twicc-pulse` and `twicc-pulse-dim`.)

- `AppTooltip.vue`: `<wa-tooltip … :show-delay="TOOLTIP_SHOW_DELAY_MS" v-bind="$attrs">` —
  declared before `v-bind="$attrs"`, so a caller's own `show-delay` still wins. A
  `TOOLTIP_SHOW_DELAY_MS = 250` constant next to `INTERACTIVE_HIDE_DELAY`.

### 5.5 Other `wa-popup` users

`wa-color-picker` (in the project edit and workspace dialogs) animates its panel with
`show-with-scale` / `hide-with-scale` on an inner `wa-popup` of host class `color-popup`:
it is treated as a menu (§5.2 rules above). `:host(.popover)`, `:host(.tooltip)`,
`:host(.select)` and `:host(.color-popup)` never match a bare `wa-popup` (the four
pickers, §6) nor the dropdown's own popup (the dropdown animates `#menu`, §5.2).

Scope of goal 1: the overlays of §5–§8. TwiCC's own hover panels
(`HoverInfoPanel.vue`, `TextSelectionComment.vue`) are not overlays of this step.

### 5.6 Timing of the events that follow an animation

| Overlay | Show: today → new | Hide: today → new |
|---|---|---|
| Dialog | 200 → 320ms | 200 → 160ms |
| Palette, search overlay (drop) | 200 → 260ms | 200 → 160ms |
| Dropdown | 50 → 180ms | 50 → 50ms |
| Submenu | 50 → 180ms | 50 → 50ms |
| Popover | 100 → 260ms | 100 → 100ms |
| Select | 100 → 180ms | 100 → 100ms |
| Color picker panel | 100 → 180ms | 100 → 100ms |
| Tooltip | 100 → 160ms | 100 → 100ms |

**Exits of menus, submenus, popovers, selects and the color picker keep Web Awesome's
current durations** (the mock's 120–150ms are not taken). A reopen during an exit leaves
these components in a broken state, today already: the hide rule wins while both classes
are present, and, e.g., `wa-dropdown`'s `showMenu()` returns early because its popup is
still active, then `hideMenu()` keeps it active — a menu left open without its
click-outside and Escape listeners; a submenu hidden while marked open. Longer exits would
widen that window; equal ones leave it as today (§12). Dialog exits (160ms) are shorter
than today's 200ms.

Consumers of these moments: the command palette and the search overlay focus their search
field on `wa-after-show`; the dialog forms (the pattern of `ProjectEditDialog.vue`, used by
about twenty dialogs: rename, create, worktree, share, snippets, workspace, bookmark…)
focus their first field on `wa-after-show`; a dropdown focuses its first item after its
show; a submenu focuses its first item after its show; the agent settings popover focuses
its first control on `wa-after-show`; `wa-select` scrolls its current option into view
after its show.

- **Palette and search:** keys typed right after the shortcut must reach the field. Their
  search field gets the `autofocus` **attribute**: Web Awesome's `show()` focuses the first
  `[autofocus]` element (`this.querySelector("[autofocus]")`) in the frame after
  `showModal()`, before the animation ends. `CommandPalette.vue`'s native `<input>` takes
  a plain `autofocus` (the property is a boolean, Vue reflects it). `SearchOverlay.vue`'s
  `<wa-input>` needs `:autofocus.attr="true"`: its `autofocus` is a non-reflected Lit
  Boolean property, so a plain `autofocus` would be set as a property and no attribute
  would exist for Web Awesome to find.
- **Palette highlight:** with the earlier focus, ↑/↓ pressed during the entrance move the
  highlight; `onAfterShow` must not reset it. `CommandPalette.vue`'s `onAfterShow` calls
  `selectFirstItem()` only when `activeKey` is null or no longer among `visibleItems`; it
  keeps its `focus()` call.
- **Search overlay selection:** its `wa-after-show` handler selects the field's text when
  it is not empty (the query is kept across opens, "easy replacement"). With the earlier
  focus, keys typed during the entrance would land next to the old query, then the late
  selection would be skipped or replace them. The selection moves to focus time: on its
  opening branch only (after the existing "already open — just re-focus" early return,
  before `dialogRef.value.open = true`), `open()` adds a handler declared at the top level
  of `<script setup>` (a stable reference: adding it twice does nothing) as a `focusin`
  listener on the search field with `{ once: true }`; the handler calls `select()` when the
  query is not empty; `handleAfterHide` removes the listener if it never fired. The
  autofocus fires it in the frame after `showModal()`, before a realistic keystroke, so
  typing replaces the kept query as today. `handleAfterShow` keeps only `focus()` when the
  focus is not already inside the field.
- Dialog forms, dropdowns, submenus and agent settings: their focus comes later by up to
  120ms (dialogs), 130ms (dropdown, submenu) or 160ms (popover); accepted (§12). Keys typed
  before the focus arrives go to the dialog, the menu or the popover, as today during their
  shorter 200ms / 50ms / 100ms window. A select whose current option is below the fold grows
  scrolled to the top, then jumps to the option at 180ms (100ms today); accepted (§12).

## 6. Bare popups — the pickers (entrance only)

The pickers' logic assumes an instant close: `close()` clears their data in the same tick
(`CommandPickerPopup.vue`, `MessageHistoryPickerPopup.vue` empty their lists and search;
`FilePickerPopup.vue`, `DirectoryPickerPopup.vue` their tree), and their key handlers never
check `isOpen` (the composer pickers rely on the textarea taking the focus back; the
directory picker moves no focus). An animated exit would show an emptied panel and keep a
focused, keyboard-reachable tree for its duration. **Decision: the pickers get the
entrance only; they still close at once, as today.** (They close on a selection, where an
instant close reads as a direct result.)

New `frontend/src/composables/usePopupMotion.js`:

```js
/**
 * Play a picker panel's entrance each time its wa-popup opens. The popup's `active` stays
 * bound to the component's own open state.
 * @param {Ref<boolean>} isOpen
 * @param {Ref<Element|null>} panel   the element that moves (the picker panel)
 * @param {Ref<Element|null>} popup   the wa-popup (for its data-current-placement)
 * @param {object} env   globalThis by default; its functions are called through bound
 *                       wrappers (roadmap §6g.2)
 */
export function usePopupMotion(isOpen, panel, popup, env = globalThis)
```

- `isOpen` turns true: after `nextTick` and one `requestAnimationFrame` (the popup has
  positioned itself: `wa-popup` sets `data-current-placement` on its host when
  `computePosition` resolves, in microtasks), set the panel's `transform-origin` from the
  placement — vertical from the side (`top*` → `bottom`, `bottom*` → `top`), horizontal from
  the alignment (`-start` → `left`, `-end` → `right`, none → `center`), e.g. `top-start` →
  `left bottom`, `bottom-end` → `right top`, `bottom` → `center top`; when
  `data-current-placement` is missing (`wa-popup` removes it on each close and sets it again
  only when positioned), the popup's `placement` attribute is used, and `center bottom` when
  both are missing. The pickers only use top and bottom placements (`flip` keeps the axis).
  Then run
  `panel.animate([{ opacity: 0, scale: 1 - 0.06 * A }, { opacity: 1, scale: 1 }], {
  duration: 180, easing })`.
- `isOpen` turns false, or the scope is disposed: cancel the pending frame
  (`cancelAnimationFrame`) and a running entrance, remove the inline `transform-origin`.
  The popup closes at once, as today. A cancelled animation's rejected `finished` is
  caught.
- `A` = `parseFloat(getComputedStyle(panel).getPropertyValue('--motion-amount'))`, 1 when
  not a number; `easing` = the computed `--motion-ease-out` of the panel, `.trim()`med,
  `'ease-out'` when empty.
- Sites: `CommandPickerPopup.vue`, `MessageHistoryPickerPopup.vue`, `FilePickerPopup.vue`,
  `DirectoryPickerPopup.vue`: each already has `popupRef` and `isOpen`; each gets a new
  `panelRef` on its `.picker-panel` and one `usePopupMotion(isOpen, panelRef, popupRef)`
  call. Their template binding of `active` does not change.
- The pickers' gliding ink (step 4c) measures through the container's visual/layout ratio,
  so the scale does not misplace it.

## 7. Session switcher — `SessionSwitcher.vue`

It opens on Ctrl+` (after its 150ms anti-flash delay) and closes on Ctrl release, a
commit or Escape (`useSessionSwitcher.js`): often and quickly. The `v-if` overlay is wrapped
in `<Transition name="switcher" :duration="{ enter: 260, leave: 160 }">` (inside the
`Teleport`). Explicit durations: the moving parts are the veil's `::before` and the panel,
which Vue cannot read on the root.

The overlay host itself never changes opacity (an ancestor with opacity < 1 stops the veil
blur and the panel's glass blur, roadmap §6c.2): the veil layer and the panel fade
separately.

```css
.switcher-enter-active.switcher-overlay::before { animation: twicc-switcher-fade 260ms var(--motion-ease-out); }
.switcher-leave-active.switcher-overlay::before { animation: twicc-switcher-fade 160ms ease-out reverse forwards; }
.switcher-enter-active .switcher-panel { animation: twicc-switcher-drop 260ms var(--motion-ease-spring), twicc-switcher-fade 260ms var(--motion-ease); }
.switcher-leave-active .switcher-panel { animation: twicc-switcher-drop 160ms ease-out reverse forwards, twicc-switcher-fade 160ms ease-out reverse forwards; }
.switcher-leave-active { pointer-events: none; }
@keyframes twicc-switcher-drop { from { translate: 0 calc(-0.5rem * var(--motion-amount)); scale: calc(1 - 0.03 * var(--motion-amount)); } }
@keyframes twicc-switcher-fade { from { opacity: 0; } }
```

- Same model as §5: one keyframe per moving part, forward to open, `reverse forwards` to
  close, in the same list order. The switcher often closes during its entrance (Ctrl
  released soon after it appears): Vue then cancels the enter and adds `leave-active`
  without a reflow, so the same-named animations are updated in place, with the §5
  behaviour (same model everywhere; distinct names would restart the exit from the fully
  visible state whatever the timing). The §5 caveat on a close during an
  opening applies; Vue ends the leave on its explicit 160ms timer.
- In the component's scoped style (the `::before` veil layer is `glass.css`'s
  `.glass-veil::before`; an animation on it does not conflict with its
  `opacity: var(--glass-veil-opacity, 1)` base value).
- `.switcher-leave-active { pointer-events: none }`: the leaving overlay (fixed, full
  screen) never takes the next gesture's clicks or hovers.
- Reopening within the 160ms leave removes the leaving overlay at once (Vue's
  `BaseTransition` ends a leaving element of the same key when a new one enters): the leave
  is cut, never doubled.

## 8. Toasts — Notivue

- `main.js`: `createNotivue({ …, animations: { enter: 'twicc-toast-enter', leave:
  'twicc-toast-leave', clearAll: 'twicc-toast-clear-all' } })`, and the import of
  `notivue/animations.css` is removed (its classes are no longer used).
- New `frontend/src/styles/toast-motion.css`, imported by the SPA `main.js` after the
  Notivue CSS:

```css
.twicc-toast-enter { animation: twicc-toast-in 520ms var(--motion-ease-spring), twicc-toast-fade-in 520ms var(--motion-ease); }
.twicc-toast-leave { animation: twicc-toast-out 220ms ease-in forwards; }
.twicc-toast-clear-all { animation: twicc-toast-fade-out 300ms ease-in forwards; }
@keyframes twicc-toast-in { from { translate: 0 calc(-130% * var(--motion-amount)); scale: calc(1 - 0.04 * var(--motion-amount)); } }
@keyframes twicc-toast-out { to { opacity: 0; translate: 0 calc(-60% * var(--motion-amount)); scale: calc(1 - 0.04 * var(--motion-amount)); } }
@keyframes twicc-toast-fade-out { to { opacity: 0; } }
/* Toasts are the one exception to §5's same-keyframe model: Notivue waits on the leave's
   own animationend (a cancelled enter resolves nothing), and a leave inside a toast's
   520ms entrance is rare (a dismissal right after it appears), where the toast snaps to
   visible and then leaves. */

/* Notivue skips its enter/leave classes under reduced motion: keep a fade-in on the
   notification itself (it plays when the element is created). */
@media (prefers-reduced-motion: reduce) {
    .Notivue__notification { animation: twicc-toast-fade-in 200ms var(--motion-ease); }
}
@keyframes twicc-toast-fade-in { from { opacity: 0; } }
```

- Notivue waits for `animationend` on its container before removing a toast; our keyframe
  names are distinct from Notivue's, so its logic is unchanged.
- Under reduced motion the leave stays instant (Notivue's own behaviour; §12).

## 9. Invariants

- Web Awesome's own lifecycle and event order do not change (stated exceptions: the
  palette and search fields take the focus earlier through `autofocus`, and focus steps
  that follow an animation move with it, §5.6, §12): the same class, on the same element, still yields a CSS animation and its
  `animationend`; on a dialog, the element and its `::backdrop` have the same duration in
  each state. Pickers keep their instant close.
- Every keyframe of this step uses `opacity`, `translate`, `scale` only (no `transform`,
  no `filter`); every distance and scale delta is multiplied by `--motion-amount` (the
  reduced-motion `twicc-pulse-dim` holds opacity only).
- Entrance movement uses `--motion-ease-spring` or `--motion-ease-out`; the fade paired
  with a spring uses `--motion-ease`; exits replay the same keyframes with `ease-out`,
  `reverse` and `forwards`, which plays as `ease-in` (toasts: distinct exit keyframes played
  forward with `ease-in`, §8).
- In the Web Awesome sheets and the session switcher, the show and hide rules of a moving
  part name the same keyframes (a close during an opening updates the animation, never
  cancels it); in the Web Awesome sheets each hide rule comes after its show rule with at
  least its specificity. Toasts are the stated exception (§8).
- Nothing is injected into an element class that is not defined; the install is idempotent.

## 10. Tests (node:test, `npm test`)

- `frontend/src/utils/waMotionStyles.test.js`:
  - install with a fake registry (constructors holding `elementStyles` arrays) and a fake
    `createSheet`: one sheet appended per defined tag, last in the array; a second install
    appends nothing; an undefined tag or a non-array `elementStyles` is skipped; the
    returned list names the patched tags.
  - CSS invariants over every string of `WA_MOTION_STYLES`: every `@keyframes` is named
    `twicc-*`; keyframe declarations are only `opacity`, `translate`, `scale`; each
    `translate` / `scale` value contains `var(--motion-amount)`; no `transform`, no
    `filter` declaration inside a `@keyframes` block, and no `transform` property anywhere
    (`transform-origin` is allowed); every animation rule declares the full `animation` shorthand; every `.hide` /
    `.hide-with-scale` rule uses `ease-out`, `reverse` and `forwards` (reversed, it plays as
    ease-in) and every `.show` /
    `.show-with-scale` rule uses `--motion-ease-spring` or `--motion-ease-out` for its
    movement (`.pulse` uses `--motion-ease`); a keyframe animated with
    `--motion-ease-spring` holds no `opacity`, and each spring animation is paired with
    `twicc-fade` of the same duration with `--motion-ease`; each hide rule names the same
    keyframes as its show rule, adds `reverse`, and comes after it in the sheet with at
    least its specificity (the `motion-drop` pair included); `twicc-pulse` animates `scale` only and the
    reduced-motion `.pulse` rule animates `opacity` only; the `wa-dropdown-item` sheet's
    `#submenu.show` / `#submenu.hide` rules animate only `twicc-fade`, and no keyframe in
    that sheet declares `scale` or `translate`; the entrance durations match §5.6 (dialogs 320ms, `motion-drop`
    260ms, dropdown, submenu, select and color picker 180ms, popover 260ms, tooltip 160ms)
    and so do the exit durations (dropdown and submenu 50ms; popover, select, color picker
    and tooltip 100ms; dialogs 160ms); the two `color-popup` animation
    rules and
    its four `transform-origin` rules exist; for each dialog state (show, hide,
    `motion-drop` show and `motion-drop` hide) the `::backdrop` duration equals the
    dialog's; each sheet declares
    every keyframe it references.
  - **Guard against Web Awesome changes** (reads `node_modules/@awesome.me/webawesome`):
    `package.json` version is `3.3.1`; the dist chunks still contain `animateWithClass`
    with `animationend`, `animationcancel` and `getAnimations`; the calls
    `animateWithClass(this.dialog, "show")` / `"hide"`, `animateWithClass(this.menu,
    "show")` / `"hide"`, `animateWithClass(submenu, "show")` / `"hide"`,
    `animateWithClass(this.popup.popup, "show-with-scale")` / `"hide-with-scale"` and
    `animateWithClass(this.popup.popup, "show")` / `"hide"`; the `wa-popup` host classes
    `popover: true`, `tooltip: true`, `select: true`, and the color picker's
    `class="color-popup"`; in both
    `@lit/reactive-element/reactive-element.js` and
    `@lit/reactive-element/development/reactive-element.js`, the body of
    `createRenderRoot(){…}` contains `this.constructor.elementStyles` (the production file
    is minified: no `adoptStyles` identifier). A failure names what moved,
    so an upgrade re-checks this step.
- `frontend/src/utils/glassArrowGap.test.js` (extend): `computeArrowGap` returns `center`
  = the rounded middle of `start` and `end` (and `null` as today when there is no gap);
  the existing `deepEqual` expectations gain `center` (e.g. `{ side: 'top', start: 80,
  end: 92, center: 86 }`);
  a source check that the handler sets `--popover-arrow-center` from it. `waMotionStyles.test.js`: the `wa-popover` sheet's four origin rules
  use `var(--popover-arrow-center, 50%)` on the side named by `data-glass-arrow`.
- Source test on `CommandPalette.vue`: `onAfterShow` guards `selectFirstItem()` with the
  null / not-visible check.
- Source test on `SearchOverlay.vue`: `open()` registers the `focusin` handler with
  `{ once: true }` after its already-open early return; `handleAfterHide` removes it; the
  handler selects a non-empty query; the `wa-after-show` handler no longer calls
  `select()` and calls `focus()` only behind a check that the active element is not
  already inside the field.
- `frontend/src/composables/usePopupMotion.test.js` (effectScope, fake panel/popup with an
  `animate` stub returning a controllable animation, fake env with rAF/cAF):
  - open → after the frame, the entrance is animated with `transform-origin` from
    `data-current-placement` (`top-start` → `left bottom`, `bottom-end` → `right top`,
    `bottom` → `center top`, `top` → `center bottom`);
  - close before the frame → the frame is cancelled, nothing animates;
  - close during the entrance → the animation is cancelled (its rejected `finished`
    raises nothing), the inline origin removed;
  - scope dispose → same as close;
  - the entrance runs with `duration: 180`;
  - no `data-current-placement` → origin from the `placement` attribute; neither →
    `center bottom`;
  - a missing or non-numeric `--motion-amount` → the scale delta is the one of 1;
  - `--motion-amount: 0` → the keyframes keep opacity and have `scale: 1`; an empty or
    space-padded `--motion-ease-out` → `'ease-out'` / the trimmed value;
  - env functions are called with the env as `this` (a fake that throws otherwise).
- Source tests on the wiring: `installWaMotionStyles()` is called in the body of
  `main.js`, `share-session/main.js` and `artifact-shell/main.js`, before `mount`;
  `CommandPalette.vue` and `SearchOverlay.vue` give their `wa-dialog` the class
  `motion-drop`; the palette's `<input>` has `autofocus` and the search overlay's
  `<wa-input>` has `:autofocus.attr="true"` (the attribute form); `main.js` imports
  `styles/toast-motion.css` after the Notivue CSS.
- Guard: `notivue`'s `package.json` version is `2.4.5` (§2 and §8 rely on its reduced-motion
  and `animationend` behaviour), next to the Web Awesome guard.
- Source test on the four picker sites: each calls `usePopupMotion`, and still binds its
  popup's `active` to its open state.
- Source test on `SessionSwitcher.vue`: the `Transition` has `:duration="{ enter: 260,
  leave: 160 }"`; the two keyframes (`twicc-switcher-drop`, `twicc-switcher-fade`) use only
  `opacity` / `translate` / `scale` with `var(--motion-amount)` on movement, the movement
  one without opacity; the panel's enter rule pairs the spring movement with
  `twicc-switcher-fade` of the same duration using `--motion-ease`, and the veil's enter uses
  `--motion-ease-out`; each leave rule names the same keyframes as its enter rule, in the
  same order, with `ease-out reverse forwards`; the panel and veil animations are 260ms (enter) and
  160ms (leave), equal to the durations; the leave rule sets `pointer-events: none`; no
  rule sets `opacity` on `.switcher-overlay` itself.
- `frontend/src/styles/toast-motion.test.js`: keyframes use individual properties with
  `--motion-amount` on movement; a keyframe animated with `--motion-ease-spring` holds no
  `opacity`, and the spring animation is paired with a fade of the same duration with
  `--motion-ease`; the leave and clear-all rules use `ease-in` and `forwards`; durations: enter 520ms,
  leave 220ms, clear-all 300ms, reduced-motion fade-in 200ms; the
  reduced-motion block has one rule, on
  `.Notivue__notification`, with a fade animation; `main.js` passes the three class names to `createNotivue` and no
  longer imports `notivue/animations.css`.
- `frontend/src/components/ui/AppTooltip.vue` source test (in the same file as another UI
  source test, or a new `appTooltip.test.js`): `:show-delay` bound to a 250ms constant,
  before `v-bind="$attrs"`.
- The existing suite stays green (635 tests before this step).

## 11. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

1. A dialog (for example the project edit dialog): springs up into place; its first field
   gets the focus at the end of the entrance; closes with a
   quick fade down; the veil fades with it. Click outside a dialog without light dismiss:
   the pulse.
2. Command palette and search overlay: drop in from above; close quickly. Press the
   shortcut and type at once: no key is lost, also when the search overlay reopens with a
   previous query (typing replaces it). Palette: shortcut, ↓, Enter at once runs the
   highlighted command.
3. A dropdown menu opening downward and one opening upward: each grows from the edge next
   to its trigger; a submenu fades in (no scale: its safe triangle keeps working); closing is a quick fade. Close a menu
   (or press Escape on the palette) during its opening: a very early close (first ~40ms for
   menus, ~100ms for dialogs) jumps up to near full visibility then fades; a later one
   jumps down then fades; one at or after the exit duration ends at once (§5, §12).
4. A `wa-select`: its list grows from the field; a long select whose value is below the
   fold jumps to it at the end of the entrance. The color picker of the project edit
   dialog: its panel grows like a menu.
5. Settings popover: grows from its button (the arrow), even when shifted sideways; other
   popovers (agent settings, composer `+`) likewise.
6. Tooltips: appear after 250ms, fade in.
7. `/` and history pickers, file and directory pickers: grow from the side next to their
   anchor; close at once, as today; keyboard use unchanged.
8. Session switcher (Ctrl+`): drops in with its veil fading in (blur visible during the
   fade); closes quickly; a click right after closing reaches the app.
9. Toasts: drop in from the top center with a small bounce; leave upward with a fade.
10. Reduced motion: every item above fades without moving (pickers: fade-in only); toasts
    fade in, leave at once.
11. Gliding inks inside palettes, pickers and the settings nav still land on their item.
12. Share viewer (its menu popover, select, tooltip) and the artifact shell's dialog: same
    motion (check through the built bundles; the worktree cannot serve shares).

## 12. Limitations

- Toast exits under reduced motion stay instant (Notivue drops its leave class then).
- A close during an opening (Web Awesome overlays, the switcher): with `t` the time since
  the opening started, a close before the exit duration jumps to the reversed position (up
  when very early, down otherwise) and fades from there; a later one ends at once. Today Web Awesome turns around smoothly
  (equal show and hide durations). Toasts dismissed during their entrance snap to visible,
  then leave (§8).
- Pickers close at once (no exit animation, §6).
- Dialog forms focus their first field up to 120ms later than today (320ms entrance instead
  of 200ms); dropdowns and submenus focus their first item up to 130ms later, and the agent
  settings popover its first control up to 160ms later (§5.6). A long select jumps to its
  current option at the end of its 180ms entrance (100ms today). Keys typed before the
  focus arrives go to the dialog, the menu or the popover, as today during their shorter
  200ms / 50ms / 100ms window.
- Reopening a menu, submenu, popover, select or the color picker during its exit leaves it
  in Web Awesome's existing broken state (open without listeners, or hidden while marked
  open); the exit durations are kept as today, so the window does not grow (§5.6).
- The blur of glass overlays is not drawn during their fade (step 3, accepted).
- Instances created before `installWaMotionStyles()` keep Web Awesome's animation; none
  exist at that point in the three entry files.
- A Web Awesome upgrade can rename classes or elements; the guard test fails and names the
  change.

## 13. Delivery

- Implementation by a sub-agent, then a code review by another sub-agent, as for 4a–5a; no
  separate plan file.
- Rebuild the share and artifact-shell bundles (`cd frontend && npm run build`).
- Update `docs/plans/2026-09-26-visual-refresh-roadmap.md`: status row 5 (5b), a §6h summary
  with lessons, the test count in §10.
- One commit after the user's browser review and explicit "commit".
