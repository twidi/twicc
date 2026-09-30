# Step 7d — footer blocks motion (design)

Status: draft for review. Parent: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §6m.
Scope decided by the user (2026-09-30): height + fade; animate open/collapse, appear/disappear,
maximize/restore, and the fixed banners; the terminal refits once (§4.6).

## 1. Goal

The blocks pinned at the bottom of a main session share one motion. Today none has any: each
swaps its content in one frame (`CollapsedBar` ↔ header, `display: none`, `v-if`).

## 2. The blocks (all in `SessionItemsList.vue`, `.session-footer`, line ~2271)

| Block | Component | Root today | States that change its shape |
|---|---|---|---|
| Goal | `message/GoalBlock.vue` | `<template v-if="goal">` fragment (`wa-divider` + `.goal-block`) | `viewState` collapsed / open / maximized; mounted by `v-if="currentGoal"` |
| Question / approvals | `message/PendingRequestForm.vue` | fragment (`wa-divider` + `.pending-request-form`) | `viewState` normal / minimized / maximized; mounted by `v-if="hasAnswerablePendingRequest"` (ephemeral and main branches) |
| Hybrid terminal | `message/HybridTerminalBlock.vue` | fragment (`wa-divider v-if="isVisible"` + `.hybrid-terminal-block`) | `viewState` closed / normal / maximized, plus `calloutShown` (closed + attention); mounted by `v-if` |
| Composer | `message/MessageInput.vue` | `.message-input` | `collapsed` |
| Fixed banners | `.stale-banner`, `.provider-disabled-banner`, `.hybrid-disabled-notice` (inline in `SessionItemsList.vue`) | plain `div`s | appear only |

Out of scope: `EphemeralActionsBar`, the question widget interior (7e), the chat area itself.

The footer accordion (`openBlock`, `applyOpenBlock`) is unchanged. It reduces one block and opens
another in the same flush; both animate at once.

## 3. Behaviour

| Change | Motion |
|---|---|
| A block goes from its one-line bar to its open form (or back), or the terminal opens or closes | The wrapper's height animates from the old to the new measured height (0 is a valid end: a closed terminal with no callout has zero height). The new content fades in. |
| A block mounts (goal appears, request arrives, terminal block appears, banner appears) | Height 0 → natural, opacity 0 → 1. |
| A block unmounts (request answered, goal dismissed) | Height current → 0, opacity 1 → 0, then the node is removed. Banners have no leave motion. |
| The next queued request replaces the shown one (`hasAnswerablePendingRequest` stays true) | None when the form is at normal size (it re-renders in place). A minimized or maximized form resets to normal (`PendingRequestForm.vue:184-193`), which plays the shape change or the restore. |
| Maximize | The block grows from its place to fill the session area. |
| Restore | The block shrinks from the full area to its place while the footer already holds its final height. |

Two animations per height change, both with no fill, played together on the same element:

| Animation | Properties | Duration | Easing |
|---|---|---|---|
| Height | `height`, `overflowY: 'clip'` (both frames) | `--motion-dur-3` (380ms) | `--motion-ease-out-height` |
| Fade | `opacity` 0 → 1 (enter) or 1 → 0 (leave) | `--motion-dur-2` (200ms) | `ease-in-out` (roadmap §6i.2: `--motion-ease` is front-loaded for fades) |

`overflowY: 'clip'` on one axis only (roadmap §6e.2): a glow or focus ring may extend sideways. The
same values apply to inset animations (380ms, `--motion-ease-out-height`).

JS reads the tokens once with `getComputedStyle(document.documentElement).getPropertyValue(name)`
and parses the durations with the existing `parseDurationMs` (`utils/detailsMotion.js:26`, reused by
`utils/glideInk.js` and `composables/useChatEntrance.js`; it returns 0 for garbage). A value that is
empty or parses to a non-finite or non-positive number → the fallback literal 380 / 200 / the easing
literal `cubic-bezier(.25, .46, .45, .94)` (same empty-value pattern as `detailsMotion.js:87-88`), so the
CSS file stays the single source.

**Reduced motion** (`matchMedia('(prefers-reduced-motion: reduce)').matches`, read at each play):
height and inset do not animate (the change snaps); the fade (200ms, `ease-in-out`) stays.

## 4. Mechanism

Web Animations API on the block's wrapper. CSS transitions cannot animate `height` between `auto`
states in Firefox (`interpolate-size` is absent, measured in Firefox 156), and the content is swapped
by `v-if`, so the height is measured before and after the DOM change (FLIP on height).

### 4.1 New files

- `frontend/src/utils/footerMotion.js` — pure helpers, no DOM, unit-tested:
  - `FOOTER_MOTION = { heightMs: 380, fadeMs: 200, heightEasing: 'cubic-bezier(.25, .46, .45, .94)', fadeEasing: 'ease-in-out' }`.
  - `tokenMs(value, fallback)` — wraps `parseDurationMs` with the fallback rule of §3.
  - `heightKeyframes(fromPx, toPx)` → `[{ height: 'Apx', overflowY: 'clip' }, { height: 'Bpx', overflowY: 'clip' }]`.
  - `fadeKeyframes(from, to)` → `[{ opacity: from }, { opacity: to }]`.
  - `insetKeyframes(from, to)` (`{ top, bottom }` px offsets inside the container) →
    `[{ inset: 'Tpx 0 Bpx 0' }, { inset: 'T2px 0 B2px 0' }]`; the restore variant is produced by
    `restoreKeyframes(from, target)` (both `{ top, bottom }`), adding the discrete `position: 'absolute', zIndex: 2, height: 'auto', maxHeight: 'none'`
    to BOTH frames.
  - `shouldAnimate({ enabled, visible, fromPx, toPx })` → false when `!enabled`, when `!visible`
    (`getClientRects().length === 0`: an ancestor is `display: none`, e.g. KeepAlive or a hidden tab), or
    when `|to − from| < 1`. A 0 height is legal.
- `frontend/src/composables/useFooterMotion.js`:
  - `createFooterMotion({ env, enabled, pinEnabled, getScrollEl, isAtBottom })` — the controller. `env` is
    `{ getComputedStyle, matchMedia, ResizeObserver, requestAnimationFrame }` (a test seam, same idea as
    `useGlideInk`'s `env`). `enabled` and `pinEnabled` are `Ref<boolean>`; `getScrollEl` returns the chat
    scroller root (`scrollerRef.value?.$el`, accessor at `SessionItemsList.vue:1490`); `isAtBottom` returns
    `scrollerRef.value?.isAtBottom()`. Returns `{ animating, attachBlock, capturePin, footerEnter, footerLeave, vFooterEnter }`;
    the controller's `animating` is a `Ref<boolean>` (true while any footer animation runs, all blocks).
  - `createSwitchGate({ env } = {})` — the session-switch gate, `{ open: Ref<boolean>, arm() }` (§4.3).
    `env` defaults to the real one (`{ getComputedStyle, matchMedia, ResizeObserver,
    requestAnimationFrame }` of `window`), so `SessionItemsList.vue` calls `createSwitchGate()`.
  - `provideFooterMotion(options)` — builds the controller with the real env, provides it under
    `FOOTER_MOTION_KEY`, and returns `{ capturePin, footerEnter, footerLeave, vFooterEnter }` (closures
    over the controller; Transition hooks and a directive cannot inject; `SessionItemsList.vue` calls
    `capturePin` from its own watcher, §4.5).
  - `useFooterBlockMotion({ wrapperRef, blockRef, shape, maximized, motion })` — called by each block.
    `motion` defaults to `inject(FOOTER_MOTION_KEY, null)`; null → the block does nothing and
    `animating` is `ref(false)` (defensive: `SessionItemsList.vue` is the only user of the four
    components today, and the seam lets tests pass a controller directly). It calls
    `motion.attachBlock(...)`, which registers the watchers below in the caller's scope, and returns
    `{ animating }` (per block, see §4.7). Optional `beforeMeasure` (§4.2) and `onSettled` (§4.7) callbacks are passed the same way.
    - `wrapperRef` — the element whose height animates (the block's single root, §4.2).
    - `blockRef` — the maximizable element (`.pending-request-form`, `.goal-block`,
      `.hybrid-terminal-block`); null for the composer.
    - `shape` — a `computed` string that changes exactly when the height must animate; the four
      values are in the table of §4.2. NOT derived from `maximized`.
    - `maximized` — `computed<boolean>`; a constant `false` for the composer.

### 4.2 State change (open ↔ collapsed)

Two `watch([shape, maximized], ([s, m], [oldS, oldM]) => …)` on the same sources, so they run once per
change of either, never per re-render (typing in the composer must not read layout). A `pre` watcher
runs after the state mutation and before the DOM patch: the DOM is still old, but the reactive state is
already new, so the old values come from the watcher's `oldS` / `oldM` arguments (never from
`maximized.value`). A pure maximize or restore changes only `maximized`.

`shape` per block (a computed string; NOT derived from `maximized`):

| Block | `shape` |
|---|---|
| Goal | `isOpen ? 'open' : 'collapsed'` |
| Pending | `isMinimized ? 'minimized' : 'normal'` |
| Terminal | `isClosed ? (calloutShown ? 'callout' : 'closed') : 'open'` |
| Composer | `collapsed ? 'collapsed' : 'open'` |

1. `flush: 'pre'`: `cancelAll()` is NOT called here (a running animation's current height is the right
   start). `prevPx = wrapper.getBoundingClientRect().height`; for a maximize, or a restore while a
   footer inset animation runs on the block, also capture the block's offsets (§4.4). Call `capturePin()` (§4.5). Keep `oldM` and `shapeChanged = s !== oldS`.
2. `flush: 'post'`: first `cancelAll(block)` (below), THEN run the composer's `beforeMeasure` hook
   (the composer calls `adjustTextareaHeight()` so the textarea has its final height; without it the
   `nextTick` re-measure runs after this step and the animation ends short), THEN measure `nextPx`
   (the natural height, with no animation applied), then if `shouldAnimate` play the height and fade
   animations `prevPx → nextPx`.

`cancelAll(block)` is synchronous and idempotent: it cancels only the footer animations the
controller created (it keeps them in a per-element `Set`; `el.getAnimations()` also returns CSS
animations and transitions, and cancelling a `CSSAnimation` stops it for good) on the wrapper AND on
the block (the restore animation runs on the block), then runs `finalizeRestore()` (remove
`data-footer-restoring`, clear the inline `height`). Cleanup never waits for `finished`: a handler
attached after `cancel()` never fires (Firefox 156 probe) and a promise handler is a microtask anyway.
`cancelAll` also runs in `footerLeave`, after its measure (§4.3), so a leave during a restore leaves no
absolute block behind (its start height is the measured one); a shape change during a restore reads
the natural height. The controller keeps its per-block state (restore
state, its own animation `Set`) in a `WeakMap` keyed on the wrapper element. Vue disposes a
component's effect scope BEFORE the Transition leave hook runs (`unmountComponent` calls
`scope.stop()`, then `remove` → leave hook, runtime-core 3.5.27), so scope disposal only stops the
watchers: it never cancels animations and never drops the state, or `footerLeave` would lose them.

Wrappers: the three fragment-root components get ONE root, `div.footer-block`, holding the existing
`wa-divider` and the block. Its only CSS is `display: flow-root`: with `display: block` the pending
card's bottom margin (`margin: var(--wa-space-xs)`) collapses out of the wrapper, and the moment an
animation sets an explicit height the margin enters the wrapper and is clipped (Firefox 156 probe:
natural height 59 without the margin, 67 with `flow-root`; a same-height animation then moved the
composer by 8px). Roadmap §6l.2 records the same trap. The rule
`.footer-block { display: flow-root; }` lives in the `<style scoped>` of each of `GoalBlock.vue`,
`PendingRequestForm.vue` and `HybridTerminalBlock.vue` and is pinned whole in all three. For the goal, the existing `v-if="goal"`
moves onto that root. The composer's root `.message-input` is its own wrapper. Existing selectors that
name `.pending-request-form`, `.hybrid-terminal-block`, `.goal-block` are unchanged;
`.session-footer:has(… .maximized)` keeps working.

**Combined change.** The accordion often takes a maximized block straight to its bar (`applyOpenBlock`
calls `minimize()`/`collapse()`/`close()` from `'maximized'`), so `shape` and `maximized` can change in
one flush. Rule, evaluated in `post`:

| `wasMaximized` | `maximized` now | `shape` changed | Motion |
|---|---|---|---|
| false | true | any | Maximize (§4.4) only. No height animation. |
| true | false | false | Restore (§4.4). |
| true | false | true | Fade only (200ms) on the block (see below). No height animation, no restore: the block was out of the flow, so its old height is not meaningful. |
| false | false | true | Height + fade (above). |

**Fade target when maximized.** A running `opacity` animation on the wrapper makes it a stacking
context, so the block's `z-index: 2` only counts inside it and the composer (`position: relative`, later
in tree order, `MessageInput.vue:2285`) paints over the block (Firefox 156 probe: `elementFromPoint` in
the composer area returned the composer). Whenever the block is or was maximized, every fade (this row,
the maximized leave, reduced-motion maximize/restore) runs on `blockRef`, not on the wrapper. This path
is not gated on a height delta.

Unmounting a block that is maximized (answering a maximized question): `footerLeave` sees a
`.maximized` descendant and fades that descendant only.

### 4.3 Mount and unmount

`SessionItemsList.vue` wraps `GoalBlock`, both `PendingRequestForm` instances (ephemeral and main) and
`HybridTerminalBlock` in `<Transition :css="false" @enter="footerEnter" @leave="footerLeave">` (the
closures returned by `provideFooterMotion`). This needs their single root (§4.2). `v-if` and `ref` stay
on the component. The `HybridTerminalBlock` `v-if` is followed today by a `v-else-if`
(`.hybrid-disabled-notice`, `SessionItemsList.vue:2357`), which a wrapping `Transition` would orphan
(roadmap §6p.2). The notice becomes its own
`v-if="isHybridSession && !parentSessionId && !settingsStore.isClaudeHybridEnabled"` (same condition,
made explicit) and carries `v-footer-enter`.

- `footerEnter(el, done)`: `natural = el.getBoundingClientRect().height`; plays height `0 → natural`
  and fade `0 → 1`; `done` after both finish.
- `footerLeave(el, done)`: in this order: `capturePin()`; `current = el.getBoundingClientRect().height`
  (a running animation's height is the right start: a leave that interrupts an enter starts from the
  animated height); `cancelAll` for the block; then it sets `el.inert = true` and
  `data-footer-leaving` on `el` (the wrapper); then plays height `current → 0` and fade `1 → 0`; `done`
  after both finish. A leaving form stays in the DOM
  for up to 380ms; during that window document-wide lookups must not target it (`focusChatPrimary`
  retries for ~1.5s, `focusChat.js:99-122`, and would focus the dying form). If `el` has a
  `.maximized` descendant: fade that descendant only. Every document-wide `.pending-request-form`
  lookup uses the selector `.pending-request-form:not([data-footer-leaving] *)` (the attribute is on
  the wrapper, an ancestor of the form: `:not([data-footer-leaving])` on the form itself would match
  nothing, probed in Firefox 156): `composables/usePendingRequestSubmitShortcut.js:25`,
  `utils/focusChat.js:74`, `items/codex/PendingRequestBody.vue:228`, and in
  `items/claude_code/PendingRequestBody.vue` the lookups at `:251`, `:263`, `:282`, `:330`; the
  implementer greps for any other.
- **Same-type replacement.** Both `PendingRequestForm` instances are unkeyed. In a production build,
  when a form mounts while another of the same type is leaving, Vue removes the leaving node at once
  (`beforeEnter` force-removes it), so the old form disappears in one frame and the new one grows
  from 0. Accepted: it needs a request answered and a new one arriving within 380ms, and queued
  requests do not remount (`hasAnswerablePendingRequest` stays true, the form only re-renders: no
  mount motion; a minimized or maximized form resets to normal and plays its shape change or restore). In development the root-level template comments of `PendingRequestForm.vue:198-204` and
  `GoalBlock.vue:250-252` keep the root a fragment and hide this early removal; they move INSIDE
  `div.footer-block` so dev and prod behave the same (pinned in `footer-motion.test.js`).
- Both call `done()` at once when `!shouldAnimate` (disabled, hidden, or a height delta under 1px),
  and on any rejected `finished` (an interrupted animation rejects: never leave the node mounted).
  EXCEPTION: the `.maximized` fade-only branch of `footerLeave` tests only `enabled` and visibility,
  never the height delta. A maximized block is absolute, so the wrapper holds just the divider, whose
  height is under 1px on fractional device pixel ratios (Firefox 156 probe: 0.8px at DPR 1.25,
  0.667px at DPR 1.5); gating on the delta would drop the fade.
- `vFooterEnter` (banners): the same enter at `mounted`.
- `enabled` is false: while the session is not active (`inject('sessionActive')`), and from a session
  switch until the next animation frame after `nextTick` — a switch re-uses this component, so blocks
  mounting or reshaping because of the new session must not animate. The gate is
  `createSwitchGate()` (in `useFooterMotion.js`; the real `env` by default, tests pass a fake one) →
  `{ open: Ref<boolean>, arm() }`: `arm()` sets `open` false, then releases it after `nextTick` then a
  `requestAnimationFrame`. `SessionItemsList.vue` calls `arm()` from a
  `watch(() => props.sessionId, …, { immediate: true })` of its own, so it is also armed at the first
  mount (the existing `sessionId` watcher at `SessionItemsList.vue:354-357` is `immediate` and, on a
  session opened with a pending request, reshapes the composer through `applyOpenBlock`), and passes
  `enabled = computed(() => sessionActive.value && gate.open.value)`. Source-pinned in
  `footer-motion.test.js`.

### 4.4 Maximize and restore

The container is `.session-items-list` (`position: relative`), found with
`blockRef.value.closest('.session-items-list')`. Offsets are always measured against it, never with
`offsetParent`: in flow the block's `offsetParent` is the relative `.session-footer`
(`SessionItemsList.vue:2539`), but maximized (footer `position: static`, rule at `:2546`) the block's
containing block is the list.

- **Maximize** (`wasMaximized` false, `maximized` true): in `pre`, capture
  `{ top: rect.top − (c.top + container.clientTop), bottom: (c.bottom − (container.offsetHeight −
  container.clientTop − container.clientHeight)) − rect.bottom }` of the block, with `c` the container's
  `getBoundingClientRect()`: `inset` resolves against the padding box, and an ephemeral session's list
  carries `.panel-card` with a 1px border (`views/SessionView.vue:2287`, `styles/surfaces.css:134`),
  which would otherwise add a 1px jump. The same padding-box measure is used for every offset in this
  section. In `post`
  (the block is absolute, `inset: 0`), play `insetKeyframes(captured, { top: 0, bottom: 0 })`. The
  footer shrinks at once; the chat is covered by the growing block.
- **Restore** (`wasMaximized` true, `maximized` false, `shape` unchanged): in `post`, in one
  synchronous task:
  1. Set `data-footer-restoring` on the wrapper. A new CSS rule
     `.session-footer:has([data-footer-restoring]) { position: static }` (added next to the existing
     `:has(… .maximized)` rule) keeps the block's containing block the list while the block animates,
     and stops the footer's `overflow-y: auto` from clipping it. (An attribute, not a Vue class: Vue
     rewrites `class` bindings and would drop a manual class.)
  2. Measure `target = { top, bottom }` of the block (in flow) against the container, and the
     wrapper's natural height `H`.
  3. Set the wrapper's inline `height = H px` (the footer keeps its final height; the chat never
     squeezes and the scroller never sees a height of 0).
  4. Play `restoreKeyframes` on the block from `from` to `target`. `from` is `{ top: 0, bottom: 0 }`,
     unless a maximize inset animation was running when `pre` ran: then `pre` captured the block's
     current offsets against the container (`getBoundingClientRect` includes the running animation)
     and `from` is that capture (a restore clicked mid-maximize does not jump to full size).
  5. On finish or cancel: remove the attribute and clear the inline `height`.
- The non-maximized pending card has a margin (`var(--wa-space-xs)`, `PendingRequestForm.vue:335`)
  that the maximized state drops (`.maximized` sets `margin: 0`, `:346-350`). Three jumps of that margin
  result: at the start of a maximize (the frames use left/right 0), at the start of a restore (the
  frame is `{0, 0}` while the margin already applies), and at the end of a restore (`target` comes
  from the block's border box, then the margin applies again). The wrapper's own margin collapse is
  handled by `flow-root` (§4.2), so enter, leave and shape changes add no jump. Accepted (a few
  pixels); the browser review decides.
- Reduced motion: no inset animation; the fade only, on the block (see "Fade target when maximized").
- Timing: like the composer and every user-triggered change, the footer motion starts at once and
  does not wait for an idle thread (roadmap §6e.2 advises idle starts for height animations of large
  content). The footer content is small; the phone check (§7 item 8) decides.

### 4.5 Chat stays pinned

In Firefox 156 native scroll anchoring does NOT keep a bottom-pinned list pinned when the viewport
shrinks (probe, `anchor_probe.py` variants: footer 40 → 300px over 400ms; without the observer the gap
reaches 260px; with a `ResizeObserver` on the scroller assigning `scrollTop = scrollHeight` in its
callback the painted gap is 0, checked in a second `ResizeObserver` registered after the pin — the
callback runs before paint).

The gap must be read BEFORE any DOM change of the batch: the browser clamps `scrollTop` when the
list grows after a change (Firefox 156 probe, footer 300 → 40 with the chat at the bottom: gap 0
before the change, 0 after the natural-height measure, 260 once `animate()` starts; `MessageInput.vue:640-647`
documents the same clamp). The controller therefore has `capturePin()`: if `pinEnabled.value &&
isAtBottom()` (the scroller's own "near the bottom" zone, 150px, `VirtualScroller.vue` sentinel) it
stores `{ gap: scrollHeight − clientHeight − scrollTop }` in a slot that a `requestAnimationFrame`
clears (a capture is valid for the current task and the next frame; the first capture of a batch
wins). The stale and provider-disabled banners (`isStale`, `!isProviderEnabled`) replace the whole
`v-else` branch at once and are not pinned: accepted, they appear on a rare state change. It is called
from: every block's `pre` watcher (§4.2); a `flush: 'pre'` watcher in
`SessionItemsList.vue` on the mount sources (`hasAnswerablePendingRequest`, `!!currentGoal`, the
terminal `v-if` condition, and the hybrid-disabled notice condition `isHybridSession && !parentSessionId &&
!settingsStore.isClaudeHybridEnabled`, which can flip without the terminal condition changing); and the start of `footerLeave` (the node is still in the DOM there).

The controller keeps two counters: a global one (all footer animations) for the pin, and one per
block (keyed on the wrapper, in the `WeakMap` of §4.2) that backs the block's `animating` and
`onSettled`. Each animation's `finished` settle handler (fulfil or reject) decrements both, once. `cancelAll` does NOT decrement (a cancel then replay would drop the count
1 → 0 → 1 and re-capture in the middle of a change; the settle handler runs later, after the replay
already incremented). When the count goes 0 → 1 and a capture slot exists, it connects a
`ResizeObserver` on `getScrollEl()` whose callback sets `scrollTop = scrollHeight − clientHeight − gap`
(a reader 100px above the bottom stays 100px above; there is no snap). It disconnects on 1 → 0 and at
once on a user scroll gesture (`wheel`, `touchstart`, `keydown`, and `pointerdown` whose target is the
scroll element itself — a scrollbar drag, as in `useVirtualScroll.js:37`/`:701`; listeners added and
removed with the observer), so it never fights the user. A chat not at the bottom leaves no capture,
so nothing is pinned. `pinEnabled` is `!parentSessionId`: a subagent's list uses
`preventAutoScrollToBottom` (`SessionItemsList.vue:2167`) and is never pinned.

### 4.6 Terminal

No code in `HybridTerminalBlock.vue` beyond §4.2. `useTerminal.js` refits with a 150ms trailing debounce
on every container resize (`RESIZE_DEBOUNCE_MS`). On open, the block has a fixed `height: 40dvh`, and
the xterm container changes size once when its body stops being `display: none`: the refit lands ~150ms
after the start, while the wrapper's height is still clipped. On maximize and restore the container
resizes every frame, so the refit lands ~150ms after the last frame. On close nothing refits.

### 4.7 Composer

The composer's `isTall` observer (`MessageInput.vue:808-818`) reads `offsetHeight` on every resize;
while the root height animates it would flip the floating collapse button (`v-if="isTall && !collapsed"`,
`:1922`) on and off. The observer callback returns early while the block's `animating` (returned by
`useFooterBlockMotion`, exact form below) is true, and the
composer passes `onSettled: recomputeIsTall` to `useFooterBlockMotion`, so `isTall` is recomputed once
when the block's last footer animation settles. The guard goes in a NEW observer callback,
`new ResizeObserver(() => { if (animating.value) return; recomputeIsTall() })`, where `animating` is
the ref returned by `useFooterBlockMotion` — per block: true while THIS block has a running footer
animation (the controller's `animating` is global). `recomputeIsTall` itself and the window `resize`
listener (`:822`) stay unguarded, so the `onSettled` call is never blocked. `onSettled` runs after the
block's count is decremented, so `animating` is already false. The exact observer form is source-pinned.

## 5. Invariants (do not regress)

- No `transform` in the composable's or helper's keyframes; only `height`, `opacity`, `overflowY`,
  `inset`, `position`, `zIndex`, `maxHeight` (step 4a: `motion.test.js` test 8 scans CSS; keep the JS
  clean too).
- The opacity animation runs on the wrapper only during mount/unmount and shape changes. A glass
  descendant (tooltip, dropdown) opened during that window would lose its blur for the run (step 5b);
  the browser review checks it. No source test (nothing to pin: no block declares `backdrop-filter`).
- The footer accordion logic and every `defineExpose` of the four components are unchanged.
- Firefox parity: WAAPI, `overflowY: clip` and `inset` keyframes all work in Firefox 156 (probed:
  computed `position: absolute` and `overflow-y: clip` from WAAPI keyframes).

## 6. Tests (`node:test`; each pin whole, §6l.2 of the roadmap)

- `utils/footerMotion.test.js`: `FOOTER_MOTION` values; `tokenMs` (`ms`, `s`, empty, garbage, zero → fallback);
  `heightKeyframes` (both frames' `overflowY`); `fadeKeyframes`; `insetKeyframes` and `restoreKeyframes(from, target)`
  strings (the first frame carries `from`, the last `target`; the restore frames both carry the
  discrete properties); `shouldAnimate` (disabled,
  hidden, sub-pixel delta, 0 → N and N → 0 true).
- `composables/useFooterMotion.test.js`, run in an `effectScope` with a fake `env` and fake elements
  (`getBoundingClientRect`, `getClientRects`, `closest`, `animate` returning an object with a
  `finished` thenable and `cancel`, `getAnimations()` listing them, `style` with a writable `height`,
  `setAttribute`/`removeAttribute`/`hasAttribute`, `inert`, `classList`/`querySelector` for the
  `.maximized` descendant), `motion` passed directly: one measure per `shape`/`maximized` change and
  none for unrelated reactive changes; the `pre` watcher takes the old values from its arguments (a
  test where `maximized.value` is already new); a pure maximize and a pure restore (only `maximized`
  changes) both animate; an interrupted change reads `prev` with the running animation and `next`
  after `cancelAll`; a change during a restore reads the natural `next` and leaves neither the
  attribute nor the inline height behind (synchronously, without awaiting `finished`); the
  combined-change table (four rows); a fade on a maximized block targets the block, never the
  wrapper; maximize captures offsets against `closest('.session-items-list')`; restore sets the
  attribute, then the inline height, and clears both on finish and on cancel; the composer's
  `beforeMeasure` runs after `cancelAll` and before the measure; reduced motion drops height/inset and
  keeps the fade; the pin observer connects once for two overlapping animations, keeps the captured
  gap read BEFORE the change (a fake scroller whose `scrollTop` is clamped by the post measure must
  still keep the pre-change gap; `cancelAll` followed by a replay does not re-capture), not a snap to
  the bottom, disconnects after both end and on a `wheel`, `touchstart`, `keydown` or a `pointerdown`
  on the scroll element, is not created when the
  chat is not at the bottom or `pinEnabled` is false; `enabled` false plays nothing and `footerLeave`
  calls `done()` at once; a rejected `finished` still calls `done()`; a leave sets `inert` and
  `data-footer-leaving` and, for a maximized block, fades the descendant only (also with a 0.8px
  wrapper: the fade plays and `done()` comes after it); `footerLeave` runs
  `capturePin`, then measures, then `cancelAll` (a leave during an enter starts from the animated
  height; a leave during a restore leaves neither attribute nor inline height); `footerEnter` plays
  height `0 → natural` and the fade and calls `done` only after BOTH finish; `vFooterEnter` plays the
  enter at `mounted` and nothing when `enabled` is false; a restore interrupting a maximize starts from
  the captured offsets, not `{0, 0}`; offsets use the padding-box measure (a container with a 1px
  border gives the same `inset` as one without); the capture slot keeps the first capture of a batch
  and is cleared by the `requestAnimationFrame`; a capture then `footerEnter` connects the pin with the
  captured gap; `attachBlock` accepts an optional `onSettled` callback called once when the block's
  last footer animation has settled (finish, cancel or rejection), and a test pins that (the composer
  passes `recomputeIsTall`, source-pinned below: there is no component harness); `cancelAll` leaves a
  foreign animation on the wrapper alone (the controller cancels only its own); `footerEnter` calls
  `done()` at once when disabled or hidden; a `pointerdown` on a CHILD of the scroll element does not
  disconnect the pin, and the gesture listeners are removed with the observer; `createSwitchGate`:
  `arm()` closes the gate and releases it only after `nextTick` then a `requestAnimationFrame`, never
  earlier (fake `env`); the two leave tests (during an enter, during a restore) dispose the
  `effectScope` BEFORE calling `footerLeave` (state survives scope disposal); `onSettled` runs after the
  block's count is decremented.
- `styles/footer-motion.test.js` (source pins): the `.footer-block { display: flow-root; }` rule whole,
  in the `<style scoped>` of each of `GoalBlock.vue`, `PendingRequestForm.vue`,
  `HybridTerminalBlock.vue`; the `SessionItemsList.vue` pre watcher on the four mount sources (exact
  list: `hasAnswerablePendingRequest`, `!!currentGoal`, the terminal `v-if` condition, the
  hybrid-disabled notice condition; `flush: 'pre'`; calls `capturePin`); the `immediate` `sessionId`
  watcher calls `gate.arm()` and `enabled` reads `gate.open`; the three components have ONE root `div.footer-block`
  containing the divider (and the goal's `v-if="goal"` on it); each of the four calls
  `useFooterBlockMotion` with its `shape` (list the exact state names); `SessionItemsList.vue` wraps
  the four instances in `<Transition :css="false">` with `footerEnter`/`footerLeave`, the notice has
  its explicit `v-if` and `v-footer-enter`, the banners carry `v-footer-enter`; `provideFooterMotion`
  is called with `enabled` derived from `sessionActive` and the switch gate, and `pinEnabled` from
  `parentSessionId`; the new CSS rule `.session-footer:has([data-footer-restoring])` is pinned whole
  next to the existing static rule; the composer's `isTall` observer has the early return and passes
  `beforeMeasure` and `onSettled: recomputeIsTall`; the four shape computeds match §4.2's table; the seven pending-form lookups listed
  in §4.3 carry the exact selector `.pending-request-form:not([data-footer-leaving] *)`; no template
  comment remains at the root level of `PendingRequestForm.vue` or `GoalBlock.vue` (they sit inside
  `div.footer-block`); the composable and helper sources contain no
  `transform`.
- Mutation testing by the code reviewer (with proof the mutant landed): drop the `pre` capture; drop
  the cancel-before-measure; drop `overflowY: 'clip'`; flip `enabled`; drop the pin disconnect; drop
  `done()` on rejection; drop the `removeAttribute('data-footer-restoring')` at the end of restore;
  drop the inline `height` clear; measure with `offsetParent`; read `maximized.value` instead of `oldM`
  in `pre`; drop `cancelAll` from `footerLeave`; fade the wrapper instead of the block when maximized;
  drop `inert`; gate the maximized fade on `shouldAnimate`; snap the pin to `scrollHeight`; drop the `SessionItemsList` mount-source capture
  watcher; call `cancelAll` before the measure in `footerLeave`; restore always from `{0, 0}`;
  `display: flow-root` → `block`; offsets from the border box; arm the switch gate without
  `immediate`; decrement the pin counter inside `cancelAll`.

## 7. Browser review checklist (user, http://localhost:5174)

1. Composer: collapse and expand (button and bar); the chat stays pinned to the bottom; the floating
   collapse button does not flicker.
2. Question: trigger an `AskUserQuestion`; it grows in, minimize, restore, answer (it folds out).
   Answer while maximized. Maximize, restore, and restore mid-maximize (look at the margin jumps of
   §4.4). In an ephemeral session: maximize and restore, no 1px jump. Queued request: with two
   parallel tool approvals pending, maximize the first form in the UI and answer it there (the next
   one resets to normal); for the minimized case (a minimized form hides its controls) resolve the
   first from another surface while it is minimized: the hybrid TUI, or a second browser tab on the same
   session (`viewState` belongs to one component instance, so the first tab's form stays minimized).
   `twicc session <id> answer-questions` does NOT work here: it refuses tool approvals
   (`not_a_question` with `--request-id`, `no_pending_question` without).
3. Goal: `/goal` a text; the bar appears; open, maximize, restore, dismiss.
4. Terminal: enable hybrid mode in the worktree; open, close, maximize, restore; the xterm refits once
   (during the motion on open, after it on maximize and restore).
5. Accordion: the closing and opening blocks move together; maximized → another block opening.
6. A session switch and a tab switch do not animate the footer. Disable a provider or the hybrid
   flag: the banner or notice grows in.
7. Reduced motion: heights snap, fades stay.
8. Phone: the same, with the chat not stuttering. A tooltip or dropdown opened mid-motion keeps its glass.

## 9. Amendment after the first browser review (binding)

Recording of a question closing (frames 559-590, tracked image by image): the collapsed bar's title
travelled from y=284 down to y=521, then jumped up to y=503 (its real position). Cause: `nextPx` (and
`natural`, and the restore's `H`) is measured synchronously after the DOM patch, before the Web Awesome
custom elements have rendered. Firefox 156 probe on the running app: a bar holding a `wa-button`
measured 33.6px right after insertion, 46px after the button's `updateComplete`. The animation ended
12-18px short, and the real layout then took over in one frame.

Rule: every measure of a NEW natural height waits for the wrapper's Web Awesome elements to finish
rendering, in microtasks only (no timer, no animation frame, so nothing paints in between).

- `settleRender(el, env)` (in `useFooterMotion.js`, exported for tests): collects the elements under
  `el` whose `localName` starts with `wa-` and that have an `updateComplete` thenable, walking into
  open `shadowRoot`s too; `await Promise.all(...)` of their `updateComplete` (a rejection is swallowed);
  repeats while a round finds elements it has not awaited yet (nested elements created by a render),
  at most 4 rounds. `env` has no new member: it only uses the passed element.
- It runs in: the `post` watcher (§4.2 step 2, after `cancelAll` and the `beforeMeasure` hook, before the
  `nextPx` measure); `footerEnter` (before `natural`); the restore's measure of `target` and `H`
  (§4.4 step 2). `footerLeave` measures the CURRENT height (no settle) and animates to 0: unchanged.
- Consequences: the `post` watcher callback and `footerEnter` become async. To stay correct when another
  change lands while a settle is pending, each block keeps a generation counter incremented at every
  `pre`; after the `await`, a run whose generation is stale returns without animating (the newer run
  owns the change). `footerEnter`/the enter of `vFooterEnter` apply the same check against a per-element
  token. `footerLeave` invalidates a pending enter token so a leave that arrives first wins.
- While waiting, the element is already at its final size and content: no frame is painted before the
  animation starts (microtasks only), so there is no flash. If the element is hidden
  (`getClientRects().length === 0` after the await) nothing plays.
- Tests: `settleRender` awaits `updateComplete` of top-level and shadow-nested `wa-*` fakes, ignores
  non-`wa-` elements and thenables that reject, stops after 4 rounds; the post watcher measures AFTER
  the settle (a fake whose height changes when its `updateComplete` resolves must yield the settled
  height as `nextPx`); a stale generation plays nothing; a leave arriving before a pending enter's
  settle cancels the enter; restore measures `target`/`H` after the settle; hidden-after-await plays
  nothing. Mutants: drop the settle in the post watcher, in `footerEnter`, in the restore; measure
  before the settle; ignore the stale generation; skip the shadow-root walk.

### 9.1 Follow-ups from the code review of §9 (binding)

- **Start height of a change that lands during a settle.** Before its `await`, a pending post run stores
  `state.pendingFromPx = prevPx`. A `pre` that runs while it is set uses it as its own `prevPx` (the
  screen still shows the height from before the pending run, no frame was painted) instead of
  measuring; the run clears it when it plays, bails out or finishes. The value is owned by the run
  that stored it (`state.pendingRun`): a stale run's `finally` clears it only if it still owns it, so a
  resuming stale run never erases the value of a newer pending run; the disabled/hidden early return
  clears it unconditionally (its `pre` just consumed it, and every older run is stale). Test: two changes, the second
  starting inside the first's settle, animate from the FIRST `prevPx`.
- **Composer stale container query (Firefox 156).** The snippets bar's `@container message-input
  (width < 40rem)` rule keeps the answer evaluated in the collapsed state (the collapsed root has
  `padding: 0`), so the measured expanded height was 64px too tall (probe: keyframes 55 → 255px, final
  191px). The composer's `beforeMeasure` forces a re-evaluation before the measure:
  `root.style.containerType = 'normal'; root.offsetWidth; root.style.containerType = ''`. Source-pin the
  exact statement sequence in `footer-motion.test.js`.
- **Test gaps.** (a) A restore that interrupts a maximize keeps the pin: one observer, never
  disconnected (kills the mutant that drops the `await` before `restore(...)`). (b) A change made while
  `enabled` is false still runs `cancelAll` first: after a→b animating, `enabled = false`, b→a, nothing
  runs on the wrapper (kills the mutant that moves the early return before `cancelAll`).
- `vFooterEnter.mounted` returns the promise of `footerEnter` so a throw is not an unhandled rejection.

## 8. Risks

- The chat pin runs a scroll write per frame; on a phone a huge chat may drop frames (the same class of
  risk as step 4b's height animations). The browser review decides whether to shorten or drop the
  height animation on the composer.
- During a restore the block is absolute for 380ms; a focus ring on a child may clip against the
  list's overflow.
