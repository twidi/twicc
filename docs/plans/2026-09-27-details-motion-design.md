# `wa-details` open/close motion — design (visual refresh, step 4b)

## 1. Context

Step 4 of the "Signature" visual refresh is split in three sub-steps: 4a (motion tokens +
micro-interactions, done: `docs/plans/2026-09-27-motion-micro-design.md`, commit
`46b4926c`), **4b (this document)**, 4c (gliding indicators). Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding user decisions: **Firefox
parity**, **reduced motion is reduced, not none**) and §6d (4a lessons).

4b uses the 4a tokens from `frontend/src/styles/motion.css`: `--motion-dur-2` (200ms),
`--motion-dur-3` (380ms), `--motion-ease-out`, `--motion-ease-spring`, `--motion-amount`
(`1`, `0` under `prefers-reduced-motion: reduce`).

### 1.1 How `wa-details` animates today (Web Awesome 3.3.1)

`WaDetails` (chunk `EERPZXW4`, registered as `wa-details` when
`@awesome.me/webawesome/dist/components/details/details.js` is imported) decorates
`handleOpenChange` with `@watch('open', {waitUntilFirstUpdate: true})`. The `watch`
decorator (chunk `PZAN6FPN`) wraps `update()` and calls **`this[decoratedFnName](old,
new)` by name** at each change, so a replacement of `WaDetails.prototype.handleOpenChange`
is the method that runs.

`handleOpenChange()` today:

- **Open:** sets the inner `<details>` open, dispatches `WaShowEvent` synchronously (if
  prevented: `open = false`, inner closed, return), `closeOthersWithSameName()`,
  `isAnimating = true`, reads `this.body.scrollHeight`, awaits
  `animate(body, [{height: 0, opacity: 0}, {height: <scrollHeight>px, opacity: 1}],
  {duration: --show-duration, easing: 'linear'})`, then `body.style.height = 'auto'`,
  `isAnimating = false`, dispatches `WaAfterShowEvent`.
- **Close:** dispatches `WaHideEvent` (if prevented: inner and `open` set back to open,
  return), `isAnimating = true`, awaits the reverse animation with `--hide-duration`,
  then `height = 'auto'`, `isAnimating = false`, `this.details.open = false`, dispatches
  `WaAfterHideEvent`.
- The durations are read with `parseDuration(getComputedStyle(this.body)
  .getPropertyValue('--show-duration' | '--hide-duration'))` (`L6CIKOFQ`).

Other relevant parts:

- `firstUpdated()` sets `body.style.height = this.open ? 'auto' : '0'`, opens the inner
  `<details>` if needed, and starts `detailsObserver`, a `MutationObserver` on the inner
  `<details>` that calls `show()` / `hide()` when its `open` attribute changes.
- `updated()` mirrors `isAnimating` into the custom state `:state(animating)`;
  `.body.animating { overflow: hidden }` in the styles (`WRKKMHO2`).
- `--show-duration` / `--hide-duration` default to `200ms` on `:host`.
- The body is `<div class="body">` (`this.body`, a `@query`), the content is
  `<slot part="content" class="content">` (`display: block`, padded by `--spacing`).
- The event classes are importable from `@awesome.me/webawesome/dist/events/*.js`
  (`show.js`, `after-show.js`, `hide.js`, `after-hide.js`; `package.json` exports
  `./dist/events/*`). The events bubble and are composed.

### 1.2 The defect, measured

Most transcript cards render their content **lazily**: `v-if` on an open flag that the
component sets in its `wa-show` handler (list in §6). Vue renders it in a microtask, after
Web Awesome has already measured `scrollHeight`. Measured in headless Firefox 156 on a
Bash tool card of the worktree instance (2026-09-27), body height per frame:

- **Open:** 0 → 4 → 8 → 13px over 200ms (it animates toward the height of the **empty**
  body), then a jump to 244px.
- **Close:** the content is gone from the first frame (Vue removes it in the microtask
  after `wa-hide`); an **empty** frame shrinks 244 → 20px.

Content that arrives later (a fetched result, a diff widget mounting, polling output)
also changes the height in jumps.

### 1.3 User decisions (2026-09-27) — do not reopen

- Open/close: **380ms, `ease-out`**, no overshoot on heights.
- **The height follows the content**: the card opens at once toward what is rendered,
  then every later height change of an open card animates, **including content that
  arrives during the opening** (the most important case for the user).
- **Closing keeps the content visible** until the fold ends.
- **Interrupted gestures are smooth** (option A): closing during the opening folds from
  where the card is; reopening during the fold grows back from where it is and the card
  stays open.
- **We own the animation** (user's idea): TwiCC replaces `wa-details`'s
  `handleOpenChange` with its own, **keeping the `wa-details` tag** (no template or CSS
  renaming). Everything else of the component (render, styles, parts, events, keyboard,
  accessibility) stays Web Awesome's.
- **Empty body → a generic loading line** (option B): a card opened by the user whose
  body is still empty after ~150ms shows it until content appears. It looks like the
  existing "Loading result..." line of tool results (spinner + quiet text), reading
  "Loading...".
- Instant restores stay instant (session re-activation, virtual-scroller re-mount,
  auto-opened live diffs).
- The chevron turns with the spring.
- Reduced motion: heights change at once; the content fades (short); the chevron turns
  without animation; the loading spinner keeps turning (a status indicator).
- **Firefox parity** (roadmap §4). Web Animations, `ResizeObserver`,
  `requestAnimationFrame`, `:has()`, `::part()` and `::part()::before/::after` (already
  used in the branch) are all supported by Firefox 156. No fallback needed.

## 2. Goals

1. A card opened by the user grows smoothly to the real height of its rendered content.
2. While it stays open (after a user open), each height change of its content animates,
   from the first frame of the opening on.
3. A card closed by the user folds smoothly with its content visible.
4. An opened card with nothing to show shows the loading line.
5. Instant opens stay instant; nothing animates on restore or scroll.
6. Interrupted gestures stay smooth and end in the state the user asked for.
7. Reduced motion: no height movement.

## 3. Out of scope

- Other show/hide animations (dialogs, popovers, dropdowns…): step 5.
- Gliding indicators: step 4c.
- Skeletons instead of the loading line: step 5 may replace it.

## 4. Mechanism — new `frontend/src/utils/detailsMotion.js`

### 4.0 Principles

1. **One owner.** `installDetailsMotion(target = WaDetails)` replaces
   `target.prototype.handleOpenChange`
   (and nothing else) with the module's `handleOpenChange`. `WaDetails` is the default
   export of `@awesome.me/webawesome/dist/components/details/details.js`, the module both
   entry files already import. The install runs at module evaluation of the entry file,
   before the Vue app mounts, so no `wa-details` has changed `open` yet. It is idempotent
   and throws a clear error if `handleOpenChange` is not a function (a future Web Awesome
   that renamed it).
2. **One gesture at a time.** Each details keeps a gesture token in its state (§4.2). Every call of `handleOpenChange` increments it, cancels the details' running
   animation, and every `await` in the method is followed by a token check: a superseded
   gesture returns at once, without touching the element. So an interrupted gesture never
   finishes later (no stale `details.open = false`, no stale `after-*` event).
3. **Start from what is on screen.** A new gesture starts from the body's current rendered
   height and opacity when an animation was running, else from the settled state (0 for an
   open, the full height for a close).
4. **Height follows the content while followed.** From a user open to the next close, the
   body height is **pinned** inline (`height: <px>`, `overflow: clip`). `clip`, not
   `hidden`: it creates no scroll container, so a focus or `scrollIntoView` inside cannot
   scroll the body (Firefox 156 and Chrome support it). No `overflow-clip-margin`: during
   a motion it would let the cut content paint past the card's bottom edge over the next
   card of a joined run (§10 lists the small cost: edge focus rings are clipped). A shared
   `ResizeObserver` on the content slot reports content changes. The slot's own size does
   not depend on the body height, so there is no feedback loop. Height writes happen in a
   `requestAnimationFrame` or synchronously in `handleOpenChange`, never inside the
   observer callback (resizing the virtual-scroller item there would trigger the
   "ResizeObserver loop" error).
5. **Instant stays instant.** A duration of `0` (`--show-duration: 0ms` inline, set by the
   components' `instantOpen` on restore and auto-open) opens or closes at once, not
   followed, body `height: auto` — the same result as today.

### 4.1 Tokens, sizes, pure helpers

Motion values are read from `getComputedStyle(document.documentElement)` at each gesture:
`--motion-dur-3`, `--motion-dur-2`, `--motion-ease-out`, `--motion-amount`
(`reduced = amount === 0`). The instant test keeps Web Awesome's own reading:
`--show-duration` / `--hide-duration` of the body, `0` → instant.

**Sizes.** The **content height** is the content slot's `offsetHeight` (`slot` =
`[part~="content"]`). It is a layout size, unaffected by ancestor transforms, and it does
not depend on the body's height: the body has no padding or border, and `motion.css` makes
the content part a formatting root, `wa-details::part(content) { display: flow-root; }`
(no visual change), so the slot contains its children's margins and its height is exactly
the height the body needs. (`body.scrollHeight` is **not** usable: it never goes below the
body's own height, so a pinned body would never see its content shrink.) The **on-screen
height** of the body is `body.offsetHeight` (a running height animation affects layout, so
this is the animated value).

Exported pure functions (unit-tested, §8):

- `parseDurationMs(value)` — `380ms` → 380, `0.38s` → 380, empty → 0.
- `startState({kind, interrupting, renderedHeight, renderedOpacity, settledHeight})` →
  `{height, opacity}`: interrupting → the rendered values; else open → `{0, 0}`, close →
  `{settledHeight, 1}`.
- `openKeyframes(start, to, reduced)` → normal `[{height: start.height px, opacity:
  start.opacity}, {height: to px, opacity: 1}]`; reduced `[{opacity: start.opacity},
  {opacity: 1}]`.
- `followKeyframes(from, to, fromOpacity)` → `[{height: from px, opacity: fromOpacity},
  {height: to px, opacity: 1}]` (`fromOpacity` is the body's computed opacity: below 1 when
  the follow re-targets an opening that was still fading in, 1 otherwise), so a re-target
  never pops the opacity.
- `closeKeyframes(start, reduced)` → normal `[{height: start.height px, opacity:
  start.opacity}, {height: '0px', opacity: 0}]`; reduced `[{height: start.height px,
  opacity: start.opacity}, {height: start.height px, opacity: 0}]`.
- `followStep({running, renderedHeight, lastHeight, target, descendantAnimating,
  unpinned})`, in this order:
  - `descendantAnimating` → `unpinned ? none : unpin` (§4.5);
  - `unpinned` → `set` (re-pin at `target`, no animation: the card was `auto` and already
    shows `target`);
  - `|target − lastHeight| < 0.5` → `none` (running or not: `lastHeight` is always the
    running animation's target);
  - else `animate` from `running ? renderedHeight : lastHeight` to `target`.

### 4.2 Per-details state

A `WeakMap` keyed by the element: `{token, lastKind, animation, pending, followed,
reduced, lastHeight, rafId, loadingTimer, loadingExpiry, unpinned}`. `lastKind` is `'open'`
or `'close'`, set at every gesture start (read by `wasOpening`). **Cancelling never clears
`state.animation`**: it keeps the cancelled animation until a newer one replaces it, so
the "still the current animation" guard of the animating `Set` (§4.4) and the open loop's
"not running" test (§4.3 step 8) both see a cancelled, non-running animation. `reduced` is captured at each
open: it decides whether the card is pinned and followed for heights. A card followed
before the OS switched to reduced motion stays pinned, but its follow frame reads the
live value and changes its height at once instead of animating (§4.4). `animation` is the module's current
animation on the body (at most one); `pending` is true between open steps 4 and 6 (the
start state is held inline, no animation yet). The entry is created on first use and
**never deleted** (the `WeakMap` frees it with the element), so a superseded continuation
always finds it and fails its token check instead of throwing.

**Stop following** (used by every gesture start, when the details leaves the document,
and when its content has no layout, §4.4): unobserve the slot, cancel `rafId`, clear both
timers, remove the `data-motion-loading` attribute, **clear the pin** (`height`, `opacity`
and `overflow` inline cleared), `followed = false`, `unpinned = false`. ("Clear the pin" is
this cleanup of the card's own inline styles; it is not the §4.5 "unpin" of an ancestor.)

**Pin** (open step 6, the follow `set` / `animate` branches, the prevented-hide restore):
`height: <px>` inline, `unpinned = false`.

The shared `ResizeObserver` is created on first use (not at import), so the module can be
imported in node tests before they install a fake.

**Every `await animation.finished`** and every handler attached to `finished` catches the
rejection of a cancelled animation (`handleOpenChange` is never awaited by its caller, so
an uncaught `AbortError` would be an unhandled rejection).

### 4.3 The replacement `handleOpenChange`

Same contract as Web Awesome's (§1.1): same events, same prevention handling, same
`closeOthersWithSameName()`, `isAnimating` true while an open or a fold runs (so
`:state(animating)` keeps working; follow animations of a settled card do not set it), inner `<details>` open during the whole open and the
whole fold.

**Common start** (both directions):

1. `token++`; `interrupting = animation?.playState === 'running' || pending`; snapshot
   `wasFollowed = followed` and `wasOpening = isAnimating && lastKind === 'open'` (used
   only by the prevented-hide restore); then `lastKind` = this gesture's kind;
2. read, **before** changing anything: `renderedHeight = body.offsetHeight`,
   `renderedOpacity = getComputedStyle(body).opacity`;
3. cancel `animation`; `pending = false`; stop following (which also clears any inline
   `opacity` a superseded open held); set `overflow: clip` inline (every inline
   `overflow` of this module is `clip`).

**Open:**

1. `this.details.open = true`; dispatch `WaShowEvent`; if prevented → `this.open = false`,
   `this.details.open = false`, clear the inline `overflow`, `isAnimating = false`,
   return.
2. `this.closeOthersWithSameName()`.
3. Instant (`--show-duration` `0`) → `body.style.height = 'auto'`, clear `overflow`,
   `isAnimating = false`, dispatch `WaAfterShowEvent`, return. Not followed.
4. `isAnimating = true`; `reduced` captured into the state; `start = startState({kind:
   'open', interrupting, renderedHeight, renderedOpacity})`. Hold the start state on screen at once: `body.style.height =
   start.height px`, `body.style.opacity = start.opacity`, `pending = true` (so the frame
   painted before step 5, if any, shows the start, not the full content).
5. `await` the next `requestAnimationFrame` (Vue's render of the lazy content and the Lit
   renders it triggered — a nested `wa-details`, a `wa-callout` — are done by then, so the
   content has its real height). Token check.
6. `pending = false`. `H` = the content height (§4.1). Clear the inline `opacity`; pin
   `height: H px`. Start `animation = body.animate(openKeyframes(start, H, reduced),
   {duration: normal --motion-dur-3 / reduced --motion-dur-2, easing:
   --motion-ease-out})`. Reduced: `body.style.height = 'auto'` instead of the pin.
7. `lastHeight = H`; `followed = !reduced`; observe the content slot (§4.4) — **now**, so
   content arriving during the opening is followed from the first frame; arm the loading
   line (§4.7); unpin the followed ancestors (§4.5).
8. Loop: `await state.animation.finished` (catch); token changed → return; if
   `state.animation` is now another running animation (a follow re-targeted the opening,
   §4.4) → await it too. Settled = `state.animation` is not running (finished, or
   cancelled by a nested card's unpin, §4.5).
9. `isAnimating = false`; dispatch `WaAfterShowEvent` (once, when the opening motion has
   ended). The card stays followed.

**Close:**

1. Dispatch `WaHideEvent`; if prevented → `this.details.open = true`, `this.open = true`,
   and restore the open state the common start broke: if `wasFollowed || wasOpening` →
   `followed = !reduced`, re-observe the slot, and pin at the content height with
   `lastHeight` set (normal) or `height: auto` (reduced); then clear `isAnimating` and, if
   `wasOpening`, dispatch `WaAfterShowEvent` (so it is not left half-open). Else clear the
   inline `overflow` and `isAnimating`. Return.
2. Instant (`--hide-duration` `0`) → `body.style.height = 'auto'`, clear `overflow`,
   `isAnimating = false`, `this.details.open = false`, dispatch `WaAfterHideEvent`,
   return.
3. `isAnimating = true`; `start = startState({kind: 'close', interrupting,
   renderedHeight, renderedOpacity, settledHeight: renderedHeight})`;
   `body.style.height = 'auto'`; `animation = body.animate(closeKeyframes(start,
   reduced), …)` with `reduced` read **live** at this gesture (§4.1; a card opened
   instantly has no stored value — the stored `reduced` serves only the follow frames and
   the prevented-hide restore), `{duration: normal --motion-dur-3 / reduced --motion-dur-2, easing:
   --motion-ease-out})`; unpin the followed ancestors (§4.5).
4. `await animation.finished` (catch). Token check (a reopen during the fold returns here:
   the card is open again and nothing closes it).
5. `body.style.height = 'auto'`, clear `overflow`, `isAnimating = false`,
   `this.details.open = false`, dispatch `WaAfterHideEvent`.

Close during the opening: the close's common start cancels the opening (its loop returns
on the token check) and folds from the on-screen height and opacity, including during the
step-5 frame wait (`pending`). Reopen during the fold: the open's common start cancels
the fold (its `await` returns on the token check; the inner `<details>` was never closed)
and grows back from the on-screen height and opacity.

**After-events of abandoned gestures.** A superseded gesture dispatches no `wa-after-*`
event. Web Awesome's `show()` / `hide()` return `waitForEvent(this, 'wa-after-show' |
'wa-after-hide')`: a caller awaiting them resolves at the next completed gesture of the
same direction, or never. No code in the repo awaits them (`detailsObserver` does not;
`UnknownEntry.vue`'s `show()` call is removed, §6). Listed in §10.

**Other callers of the method.** `detailsObserver` calls `show()` / `hide()`, which set
`open` and go through the same method. When the inner `<details>` is closed only at the
end of a fold, the observer's `hide()` is a no-op (the host is already closed).

### 4.4 Follow

One shared `ResizeObserver` observes the content slot of every followed details. Its
callback only schedules one `requestAnimationFrame` per details (coalesced in `rafId`,
capturing the token). The initial notification of `observe()` is harmless:
`followStep` returns `none` when the target equals `lastHeight`.

In that frame, token unchanged:

- details not connected → stop following (the state entry stays, §4.2);
- content without layout (`slot.offsetHeight === 0` and `body.offsetParent === null`:
  hidden tab, `display: none` ancestor) → stop following (the card becomes `auto`, like a
  re-mounted card; a pane unmounted while hidden would otherwise stay observed forever,
  since no further size change is reported);
- the loading-line check (§4.7);
- the state's `reduced` (captured at open) → nothing more (height is `auto`);
- `target` = the content height (§4.1); `running = animation?.playState === 'running'`;
  `descendantAnimating` = one of the module's currently animating details has this card as
  a composed ancestor (same walk as §4.5, crossing shadow roots). The module keeps a `Set`
  of details: added when an animation starts; in that animation's `finished` handler
  (with catch), removed **only if `state.animation` is still that animation** — a
  cancelled animation replaced by a newer one (a follow re-target, a close interrupting
  an opening) must not remove the element while the newer one runs;
- read `renderedHeight = body.offsetHeight` and `fromOpacity =
  getComputedStyle(body).opacity` **before** `followStep` and before any cancel (after a
  cancel they would give the pinned values and a re-target would jump);
- `followStep(...)`: `none` → nothing; `animate` → if `--motion-amount` read **live** is
  `0` (the OS switched to reduced motion while the card was open), do a `set` instead (pin
  at `target`, no animation); else cancel `animation`, pin `height: target px`,
  `animation = body.animate(followKeyframes(from, target, fromOpacity), {duration:
  --motion-dur-2, easing: --motion-ease-out})`, `lastHeight = target`, unpin the followed
  ancestors (§4.5); `unpin` → §4.5; `set` → pin `height: target px`, `lastHeight =
  target`, `unpinned = false`, no animation.

The open's step 8 loop awaits a follow that re-targeted the opening, so `wa-after-show`
fires once, when the motion has ended.

### 4.5 Nested cards

Each card manages its own pin; no card re-pins another.

**Unpin.** When the module starts an animation on a details (open step 7, follow, close
step 3), it walks up **all** ancestor details (`parentElement?.closest('wa-details')`,
crossing shadow roots through `getRootNode().host`), **skipping** the ones that are not
followed (a card restored open in between is `auto` already) and continuing past them, and
**unpins** each followed one at once, in the same task, before the next paint: its own `animation` is cancelled (an open loop
waiting on it sees it settled, §4.3 step 8), its inline height and opacity are cleared
(`auto`, 1), `unpinned = true`. Ancestors then grow and shrink with the inner motion in
the same frame, with no lag and no clipping.

**Re-pin.** Only in the ancestor's own follow frame (§4.4): the inner motion changes the
ancestor's content size every frame, so its follow frame keeps running. `followStep`
returns `none` while a descendant is animating (the card stays `auto`), and `set` when the
card is `unpinned` and no descendant is animating any more: pin at the content height,
measured after the inner card has fully settled (for a fold, after its inner `<details>`
closed, since that closing is itself a content change that triggers one more frame). To
make sure that frame always comes, every module animation, when it ends or is cancelled
(a `finished` handler with a catch), schedules a follow frame for each followed ancestor
(the same coalesced `rafId`), whether or not a size change is reported.

An ancestor still in its own motion (opening or follow) when any nested card starts
moving (open, fold, follow) jumps to its full height and opacity at once (its animation is
cancelled); while unpinned, its own content changes also show at once, not animated.
Accepted (§10): both need two cards moving within the same few hundred milliseconds.

### 4.6 Installation

`installDetailsMotion()` is called right after `installGlassArrowGap()` in
`frontend/src/main.js` and `frontend/src/share-session/main.js` (the share viewer renders
the same transcript cards). It imports `WaDetails` from
`@awesome.me/webawesome/dist/components/details/details.js` and the four event classes
from `@awesome.me/webawesome/dist/events/`. The artifact shell has no `wa-details` and does
not install it.

### 4.7 Loading line

After a user open (open step 7), `setTimeout(150ms)` capturing the token: if unchanged,
the host open and the slot has **no assigned element** (`slot.assignedElements()` empty),
set `data-motion-loading` on the `wa-details` and arm a 10s expiry that removes it (a card
whose content is legitimately empty must not show it forever). The follow frame (and,
under reduced motion, the observer frame) removes it as soon as the slot has an assigned
element. Stop following removes it too.

So that the line never shows next to real content, even for one frame, the CSS hides it
as soon as a content element exists:

```css
wa-details[data-motion-loading]:not(:has(> :not([slot])))::part(content)::before {
    /* A CSS stand-in for <wa-spinner>, like the "Loading result..." line. */
    content: '';
    display: inline-block;
    box-sizing: border-box; /* document `*` rules do not reach a shadow pseudo-element */
    vertical-align: middle;
    inline-size: 1em;
    block-size: 1em;
    margin-inline-end: var(--wa-space-s);
    border-radius: 50%;
    border: 0.1em solid <track color>; /* wa-spinner draws a stroke of 5 in a 50-unit viewBox; its --track-width is unused */
    border-block-start-color: <indicator color>;
    animation: motion-spin <rotation duration> linear infinite;
}
wa-details[data-motion-loading]:not(:has(> :not([slot])))::part(content)::after {
    content: 'Loading...';
    vertical-align: middle;
    color: var(--wa-color-text-quiet);
}
@keyframes motion-spin {
    to { rotate: 1turn; }
}
```

**Look.** `ToolUseContent.vue` renders the result loading line as
`<div class="tool-result-loading"><wa-spinner></wa-spinner><span>Loading result...</span></div>`
(`display: flex; align-items: center; gap: var(--wa-space-s); color:
var(--wa-color-text-quiet)`). A `wa-spinner` cannot be created from CSS, and inserting a
node into a Vue-managed `wa-details` from outside Vue would fight Vue's patching, so the
spinner is a CSS **approximation**: a ring with a fixed-length arc turning at
`wa-spinner`'s speed, with its track width, track color and indicator color (the
implementer reads them in `wa-spinner`'s styles in the Web Awesome dist and fills the
placeholders). `wa-spinner` also grows and shrinks its arc (a second animation); the
stand-in does not. The spinner keeps turning under reduced motion (status indicator). The
line adds height: the follow animates it in, and animates the change when the real
content replaces it.

### 4.8 Chevron

In `motion.css`:

```css
:where(wa-details)::part(icon) {
    transition: rotate var(--motion-dur-2) var(--motion-ease-spring);
}
@media (prefers-reduced-motion: reduce) {
    :where(wa-details)::part(icon) {
        transition-duration: 0s;
    }
}
```

A `::part()` rule from the document beats the component's `[part~='icon']` rule. The
reduced-motion block comes after the first rule.

## 5. Keeping the content during the fold — `frontend/src/composables/useDetailsClosing.js`

```js
const { isClosing, markClosing, clearClosing } = useDetailsClosing()
// isClosing(key = 'default') → boolean (reactive); markClosing(key) / clearClosing(key)
```

Backed by a `reactive(new Set())`. Each lazy component (§6):

- in its existing `wa-hide` handler (the one that sets its open flag to `false`), also
  calls `markClosing(key)`;
- adds a `wa-after-hide` handler with the **same event guard** as its `wa-hide` handler
  (`.self`, or the same `event.target !== event.currentTarget` check) that calls
  `clearClosing(key)`;
- in its existing `wa-show` handler, calls `clearClosing(key)` (reopen during the fold:
  no `wa-after-hide` comes for the abandoned fold, §4.3);
- renders the content with `v-if="<open flag> || isClosing(key)"`;
- **every other write of the open flag to `false`** (outside the `wa-hide` handler) calls
  `markClosing(key)` first, in the same tick. Otherwise Vue removes the content in the same
  flush that closes the card, before the fold starts (the §1.2 defect). The one case in the
  §6 components (grep, 2026-09-27): `ToolUseContent.vue`'s watcher that closes an
  auto-opened live Edit/Write diff when its tool errors (`isOpen.value = false`).

The open flag, the persisted state (`dataStore.setDetailOpen`) and the handlers' side
effects (polling stop, fetches) are unchanged. A component unmounted during the fold
(virtual scroller) loses the state with it.

## 6. Components changed

Lazy blocks (content under `v-if` on a flag set in `wa-show`; verified 2026-09-27):

| File | Details | Flag / key |
|---|---|---|
| `session/detail/items/ToolUseContent.vue` | tool card | `isOpen` |
| `session/detail/items/claude_code/ThinkingContent.vue` | thinking | `isOpen` |
| `session/detail/items/codex/Reasoning.vue` | reasoning | `isOpen` |
| `session/detail/items/codex/AssistantMessage.vue` | proposed plan | `isOpen` |
| `session/detail/items/codex/ImageGeneration.vue` | prompt | `isPromptOpen` |
| `session/detail/items/CompactSummary.vue` | compact summary | `isOpen` |
| `session/detail/items/UnknownEntry.vue` | unknown entry | `isOpen` |
| `workflows/WorkflowRunDetail.vue` | args; run result; phases, agents, agent prompt/result | `argsOpen`; `resultOpen`; the `open` Set keys (`phase:…`, `agent:…`, `prompt:…`, `result:…`) |

The nested "Result" of a tool card is **not** lazy (`.tool-result-content` is always
rendered inside the inner details): no `isClosing` for it.

Two more changes:

- **`ToolUseContent.vue`** — `onToolUseClose` sets `isResultOpen = false` today, so the
  inner "Result" folds on its own inside the folding outer card. Changes:
  - the assignment `isResultOpen.value = false` moves to the outer card's `wa-after-hide`
    handler, guarded by `if (!isOpen.value)`;
  - the store write (`setDetailOpen(…, 'result:…', false)`) stays in `onToolUseClose`;
  - `onToolUseOpen` rewrites `setDetailOpen(…, 'result:…', true)` when `isResultOpen` is
    still `true` (reopen during the fold), so the store matches the screen.
- **`UnknownEntry.vue`** restores its open state by calling `detailsRef.value?.show()` in
  `onMounted` → an **animated** open on every virtual-scroller re-mount and session switch.
  It moves to the `:open="isOpen"` + `instantOpen` pattern of `CompactSummary.vue` (inline
  `--show-duration: 0ms` / `--hide-duration: 0ms` on the first render, cleared on
  `nextTick` after mount).

The implementer reads each component and applies §5 to every lazy block, including any
not listed if the pattern is found (then reports it). Non-lazy `wa-details` (goal
objectives, hybrid-mode explainer, permission suggestions, markdown table of contents)
need no change.

## 7. Invariants

- Only `WaDetails.prototype.handleOpenChange` is replaced; the tag, render, styles,
  parts and events stay Web Awesome's.
- Every `await` of the replacement is followed by a token check; a superseded gesture
  touches nothing.
- Instant opens and closes (duration `0`) behave as today and are never followed.
- Only non-instant opens are followed (a click, the keyboard, a prop-driven `:open`
  change, `detailsObserver`); following stops at close, when the details leaves the
  document, or when its content has no layout (then the pin is cleared).
- The body height is never written inside the `ResizeObserver` callback (only in
  `handleOpenChange` and its continuations, or in a follow `requestAnimationFrame`).
- No animation of the module overshoots (no spring on heights).
- The loading line never stays more than 10s and never shows next to content.
- Reduced motion: no height animation; opacity fades only.

## 8. Tests (node:test, `npm test`)

- **`frontend/src/utils/detailsMotion.test.js`**:
  - `parseDurationMs` (`380ms`, `0.38s`, `0s`, empty);
  - `startState`: open not interrupting → `{0, 0}` even with a large rendered height;
    close not interrupting → `{settledHeight, 1}`; interrupting → the rendered values;
  - the keyframe builders, normal and reduced, zero and non-zero starts and opacities;
  - `followStep`: descendant animating → `unpin`, then `none` once unpinned; unpinned and
    no descendant animating → `set`; same target (within 0.5px) with a running animation
    → `none`; no running animation → `from = lastHeight`; running → `from =
    renderedHeight`; a shrink (target below `lastHeight`) → `animate` downward;
  - `installDetailsMotion(target = WaDetails)` replaces `target.prototype
    .handleOpenChange` once (idempotent) and throws when the method is missing (tested
    on a fake class);
  - **the WA contract guard**: import `WaDetails` from
    `@awesome.me/webawesome/dist/components/details/details.js` (it loads in node): its
    prototype has a `handleOpenChange` function, `closeOthersWithSameName`, and `body` /
    `details` / `header` accessors; `WaDetails.elementProperties.get('isAnimating').state`
    is `true`; the `watch` chunk text contains
    `this[decoratedFnName](`; the version in `@awesome.me/webawesome/package.json` (the
    package root) is `3.3.1`. A Web Awesome upgrade fails the test on purpose and forces a
    re-read of §1.1.
- **Gesture sequences** with fakes. The fakes provide: an element with `open`, `details`,
  `body`, a shadow root returning the slot for `[part~="content"]`,
  `closeOthersWithSameName`, `dispatchEvent` capture; a `body.animate()` returning
  controllable animations (`playState`, `cancel()`, a `finished` promise the test resolves
  or rejects); `offsetHeight` on body (the running animation's current height when its keyframes carry
  one, else the
  inline height, or the slot's `offsetHeight` when the inline height is `auto` or empty)
  and slot, `offsetParent` on the body; `isConnected`,
  `parentElement` / `closest('wa-details')` / `getRootNode()` (with `.host`) for the
  ancestor walk; `slot.assignedElements()`; `setAttribute` / `removeAttribute` /
  `hasAttribute`; a stubbed `document.documentElement` and `getComputedStyle` (durations,
  opacity, motion tokens); fake `requestAnimationFrame`, `setTimeout` and
  `ResizeObserver` the test drives. Cases:
  - user open → followed, pinned, `body.style.overflow === 'clip'`, `wa-show` then
    exactly one `wa-after-show`; the inline `overflow` is empty after a completed fold,
    after the instant paths, after a prevented `wa-show`, and after a prevented `wa-hide`
    on a card that was neither followed nor opening;
  - content arriving during the opening (a follow re-targets it) → still exactly one
    `wa-after-show`, dispatched after the replacement animation ends, and the follow's
    first keyframe is the **rendered** height (see the `offsetHeight` fake above);
  - OS switched to reduced motion while a card is followed → the next content change is
    a `set`, no animation;
  - content shrinking on a settled followed card → an animation downward to the new
    height;
  - close during the opening (also during the frame wait) → folds from the rendered
    height and opacity, one `wa-after-hide`, no `wa-after-show`;
  - reopen during the fold → no `wa-after-hide`, inner details never closed, ends
    followed;
  - open-close-open-close → ends closed, one `wa-after-hide`;
  - instant open → not followed, height `auto`; prevented `wa-show` / `wa-hide`
    (including a prevented hide during an opening: ends pinned, `isAnimating` false, one
    `wa-after-show`);
  - nested: an inner animation unpins a followed outer at once; the outer is re-pinned by
    its own follow frame once the inner animation has ended (the end schedules the frame
    even with no size report);
  - nested, outer still opening: a nested animation unpins an outer card during its own
    opening → the outer's open loop ends (its cancelled animation is not running) with
    `isAnimating = false` and exactly one `wa-after-show`;
  - nested, three levels: followed grand-parent, non-followed parent (restored open),
    animating child → the grand-parent is unpinned, and its `descendantAnimating` is true
    through the middle card;
  - live `reduced` on close: a card opened instantly, then closed under reduced motion →
    opacity-only close keyframes with the height held; a card opened under normal motion,
    then closed after the setting switched to reduced → same;
  - nested re-target: an inner opening re-targeted by a follow (A1 cancelled, A2 started)
    keeps the followed outer unpinned (`followStep` → `none`) until A2 ends, then `set`;
    same for a close that interrupts an inner opening;
  - loading line: set after 150ms when the slot has no assigned element, not set when it
    has one, removed when content appears, removed after 10s, never set after a token
    change;
  - disconnect while followed → following stops, the state entry remains, the pending
    open loop completes without throwing (`isAnimating` false, one `wa-after-show`);
  - close while unpinned by a nested card, reopen, content changes before the first
    follow frame → an animation, not a `set` (the flag was reset);
  - content without layout while followed → following stops, height `auto`;
  - reduced motion: open → height `auto`, not followed but observed, opacity-only
    animation; the observer frame removes the loading line; close → the height is held
    during the fade, then the card closes.
- **Updates to `frontend/src/styles/motion.test.js`** (4a's tests), which the new
  `motion.css` rules would otherwise break:
  - test 1 (`assert.equal(reducedMedia.length, 1, …)`) breaks: it accepts a second
    top-level reduced-motion block; test 3 (which reads `reducedMedia[0]`, the `:root`
    block) is extended to check that the second block holds exactly the
    `:where(wa-details)::part(icon)` rule with `transition-duration: 0s`;
  - test 8 (every `rotate` / `translate` / `scale` contains `var(--motion-amount)`)
    exempts `@keyframes motion-spin`: a spinner is a status indicator and keeps turning
    under reduced motion (roadmap §4);
  - the `motion.css` header comment states the same exception.
- **`frontend/src/composables/useDetailsClosing.test.js`**: mark / clear / isClosing per
  key; reactivity (a `computed` over `isClosing` updates).
- **Text tests** (new `frontend/src/styles/details-motion.test.js`, same approach as
  `glass.test.js`): in `motion.css`, the `flow-root` content rule, the loading-line rules
  (with the `:not(:has(> :not([slot])))` guard) and `@keyframes motion-spin`, the chevron
  rules (reduced block after the base rule); `installDetailsMotion()` called in `main.js`
  and `share-session/main.js`; each §6 lazy block renders with `|| isClosing(`, each
  component has a `wa-after-hide` handler calling `clearClosing(` with the same guard as
  its `wa-hide` handler and its `wa-show` handler calls `clearClosing(`;
  `ToolUseContent.vue`'s error watcher calls `markClosing(` before setting `isOpen` to
  `false`; its `onToolUseClose` no longer assigns `isResultOpen`, its
  `wa-after-hide` handler assigns it under `!isOpen.value`, `onToolUseOpen` rewrites the
  result store key; `UnknownEntry.vue` has no `.show()` call and binds `:open` with the
  `instantOpen` style.

## 9. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

1. Open a Bash tool card: one smooth growth to its full height. Measure it like §1.2
   (body height per frame): monotonic growth to the final height, no jump. Close and open
   it twice more: same growth each time. Same with a tool "Result" and a non-lazy details
   (goal objectives).
2. Close it: it folds with its content visible until the end.
3. Close a card during its opening: it folds from where it is.
4. Reopen a card during its fold: it grows back from where it is and stays open; then open
   its Result: the change animates, no jump.
5. Open, close, open, close quickly: the card ends closed, smoothly; open, close, open:
   it ends open and followed.
6. Open an Edit card with a diff: smooth opening, and the diff mounting during or after
   the opening extends it smoothly.
7. Open a tool card, then its "Result" for the first time (its data is fetched on open:
   "Loading result..." then the data): the inner part grows smoothly and the outer card
   follows without lag or clipping, including when the data arrives during the Result's
   opening. Close the outer card with the Result open: one fold. Close it
   and reopen it during the fold: the Result is still open.
8. Loading line look: in the inspector, add `data-motion-loading` to an open card and
   remove its content elements: the line looks like the "Loading result..." line
   (spinner, quiet text) and the ring turns, in Firefox and in Chrome (the first
   document `@keyframes` driven through a `::part()` rule in the branch). Put a content
   element back: the line disappears.
9. Open a card whose result arrives later, switch session and back before it arrives,
   wait: the result shows in full (not stuck at the old height).
10. Scroll a long transcript up and down with cards open: no height animation on
    re-mount; switch sessions and back: restored cards are open at once (including an
    unhandled event card). A live Edit diff that auto-opens appears at once.
11. Thinking, Codex reasoning, compaction summary, workflow run rows (args, run result,
    phase, agent, prompt, result): same open/close behaviour.
12. Chevron: turns with a slight spring. Keyboard (Enter, Space, arrows on the summary)
    opens and closes the same way.
13. Reduced motion (`ui.prefersReducedMotion = 1` in Firefox's `about:config`): cards open
    and close at once in height, their content fades; the chevron turns at once.
14. Long session in Firefox, pinned to the bottom: open and close cards near the bottom;
    the view stays pinned and does not jitter.
15. Firefox console: no "ResizeObserver loop" error and no unhandled rejection during
    checks 1–9.
16. Share viewer: the worktree instance cannot serve it (share host gate, same limit as
    step 3). After `npm run build`, check the built `share-session` assets contain string
    literals that survive minification: the install error message and
    `data-motion-loading` in its JS chunk (the module sets the attribute with
    `setAttribute('data-motion-loading', '')`, never through `dataset`), and
    `motion-spin` in its CSS asset.
17. An open card whose content gets shorter (make the window wider so text reflows, or
    collapse a node of a JSON result): it animates down to the content, with no blank gap
    left once the animation ends.
18. A live Edit diff that auto-opened and whose tool then errors: the card folds with the
    diff visible until the end (no snap, no re-mount of the diff during the fold).

## 10. Limitations

- A card with legitimately empty content shows the loading line for up to 10s.
- The loading spinner approximates `wa-spinner` (no growing/shrinking arc).
- A followed card stops being followed after a re-mount (virtual scroller, session
  switch): its later height changes jump, like any restored card.
- Height changes of a card opened instantly (restore, auto-open) are not animated.
- A parent card still in its own motion jumps to full height and full opacity when a
  nested card starts moving (open, fold or follow), and its own content changes are not
  animated while a nested card moves (§4.5).
- A card opened by the user clips what overflows its body while it is open (`overflow:
  clip`), e.g. the focus ring of a link on the first line of a thinking block (the
  transcript cards have no top padding on their content part). A card restored open is
  not clipped. A clip margin was rejected: it paints the cut content over the next card
  during a motion.
- A prevented `wa-hide` snaps the card back to its content height (§4.3 close step 1). No
  handler in the repo prevents `wa-hide` on a `wa-details`.
- A prevented `wa-show` during a fold leaves the card closed at once (the fold was already
  cancelled) and its component's closing mark set until the next open. No handler in the
  repo prevents `wa-show` on a `wa-details`.
- A superseded gesture dispatches no `wa-after-*` event: a caller awaiting Web Awesome's
  `show()` / `hide()` resolves only at the next completed gesture of the same direction,
  or never (no caller in the repo, §4.3).
- A Web Awesome upgrade needs a re-read of §1.1 (the §8 guard test fails on purpose).

## 11. Delivery

One commit on branch `enhanced-ui`, after the user's browser check and explicit "commit".
