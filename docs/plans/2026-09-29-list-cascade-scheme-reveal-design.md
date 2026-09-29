# Session-list cascade and color-scheme reveal — design (visual refresh, step 5c)

## 1. Context

Step 5 of the "Signature" visual refresh is split in three sub-steps: 5a (live chat
entrances + chat skeleton, done: `docs/plans/2026-09-28-chat-entrances-skeleton-design.md`),
5b (overlay entrances and exits, done: `docs/plans/2026-09-28-overlay-motion-design.md`),
**5c (this document)**: the session-list cascade, the light/dark reveal (§1–§11) and the
tab-panel crossfade with the overlay slide (§12, written after a browser probe). Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox parity**,
**reduced motion is reduced, not none**), §6g and §6h (lessons of 5a and 5b).

Tokens (`frontend/src/styles/motion.css:19-40`): `--motion-dur-1/2/3` (120/200/380ms),
`--motion-ease`, `--motion-ease-out` (`cubic-bezier(.22, 1, .36, 1)`), `--motion-amount`
(1; 0 under reduced motion, `motion.css:66-73`).

Mock reference (`mock.css`, roadmap §3 and §9 "Step 5"; the list rule is in the
`fx-enter` block, the view-transition rules at the top of the file):

```css
@keyframes fade-up { from { opacity: 0; transform: translateY(.375rem); } }
.mock.fx-enter .session-item.is-new { animation: fade-up 320ms var(--ease-out) both;
    animation-delay: calc(var(--i, 0) * 22ms); }
::view-transition-old(root), ::view-transition-new(root) { animation: none; mix-blend-mode: normal; }
::view-transition-new(root) { animation: vt-reveal 650ms cubic-bezier(.22,1,.36,1); }
@keyframes vt-reveal { from { clip-path: circle(0 at var(--vt-x, 50%) var(--vt-y, 50%)); }
    to { clip-path: circle(150vmax at var(--vt-x, 50%) var(--vt-y, 50%)); } }
```

The mock's `setScheme` (`app.js`) sets `--vt-x` / `--vt-y` from the pointer position of the
click event (`clientX` / `clientY`), else the viewport center, then calls
`document.startViewTransition(run)`.

### 1.1 User decisions — do not reopen (2026-09-29)

- **Cascade triggers:** the session list cascades when a list arrives: app load, project
  switch, workspace switch (roadmap §8.7 and user, 2026-09-28). Only the rows on screen
  cascade, with a cap. Scrolling, the next page, the search filter and live reorders play
  nothing.
- **Live entrance:** a new session that appears in the list live (created in this tab or
  received from the server) arrives with the same entrance, alone.
- **Reveal origin:** from the settings panel, the circle grows from the center of the
  "Color scheme" select; from the command palette, from the center of the viewport. A
  change that comes from elsewhere (settings synced from another device or tab, the OS
  scheme under "System") is a plain fade of the page, no circle.
- **Reduced motion:** the circle becomes the plain fade (a growing wipe is movement; a fade
  stays). The cascade keeps its fades and loses its rise (`--motion-amount`).

## 2. Today (checked)

### 2.1 Session list

- `components/session/list/SessionList.vue` renders the list in a `VirtualScroller`
  (`:613-645`) with `:key="projectId"`, `:items="sessions"`, `:item-key="session =>
  session.id"`; no `item-class` / `item-style` today. A scope switch (project, workspace,
  all projects: `projectId` is the effective scope id, `views/ProjectView.vue:640-644`)
  mounts a new scroller instance.
- `sessions` (`:131-141`) is `allSessions` (`:119-127`: `[extra?, ...crossFilterPinned,
  ...crossFilterActive, ...natural]`) filtered by the search query. Section separators are
  not items: each renders inside the row of its first session (`:628-631`). The empty
  states (`:603-610`) replace the scroller when `allSessions` or `sessions` is empty and
  no load is running; while a load runs, the scroller renders with zero rows. The
  search query survives a `SessionList` remount within one scope (`ProjectView.vue:819-821`
  clears it only on a scope change).
- Loading: `ProjectView.vue:2069-2098` shows a spinner instead of `SessionList` while
  `isInitialLoading` (`:737-740`: loading and never fetched); `SessionList` mounts once the
  first page is in the store. A scope fetched before keeps `SessionList` mounted and swaps
  the scroller at once with the store's rows (background refetch merges later).
- Pagination: `onScrollerUpdate` → `loadMore()` near the end (`:249-254`, `:225-237`).
- Scroll on arrival: a watcher on `props.sessionId` with `{ flush: 'post', immediate: true }`
  (`:284-288`) calls `scrollToSession(id)` (`:311-336`). With `immediate`, Vue runs the
  first call synchronously inside `watch()` (during setup; `@vue/reactivity`
  `reactivity.cjs.js:1926-1928`), later calls post-flush. `scrollToSession` retries 5 ×
  50ms while the session or the scroller is missing (at setup `scrollerRef` is still
  null), then calls `scrollerRef.value.scrollToKey(id, { align: 'center' })` (returns
  `Promise<boolean>`, `VirtualScroller.vue:544-546`), which waits at least 150ms for the
  heights to settle, even for a row already on screen (`useVirtualScroll.js:1233-1250`).
  `scrollToSession` returns nothing today. A `projectId` watcher (`:257-263`) calls
  `scrollToTop()` on the scroller.
- Live rows: the WebSocket `session_updated` handler calls `store.addSession(msg.session)`
  for an unknown session of a fetched scope (`composables/useWebSocket.js:1152-1163`);
  `createDraftSession(projectId, …)` (`stores/data.js:1720`) adds the user's new session
  and returns its id. A Codex draft is rekeyed on its first send. `session_bound` goes to
  every tab, before the watcher's `session_updated` (`src/twicc/agent/base_manager.py:526-531`); in a tab
  without the canonical session it stores `pendingDraftBindings[draftId] = sessionId`
  (`useWebSocket.js:1113-1116`; also in tabs that never held the draft, and also `id → id`
  for Claude). Then `session_updated` for the canonical id goes through `addSession` →
  `tryFinalizePendingBinding` (`data.js:1571-1574`) → the async `bindDraftSession(draftId, sessionId)`
  (`data.js:1917`), which forwards an ephemeral draft to `bindEphemeralSession`
  (`:1918-1919`), returns early when `draftId === sessionId`, and otherwise removes the
  draft, after a `router.replace` when the route is on the draft (`data.js:2007-2019`). When
  `session_updated` arrives first, `session_bound` finds the canonical session and calls
  `bindDraftSession` at once (`useWebSocket.js:1113`). A Claude draft keeps its id
  (`updateSession`); an ephemeral session is rekeyed in place
  (`utils/ephemeralSessions.js:114`). `addSession` is also called for a loaded subagent
  session (`SessionItemsList.vue:677`); subagents never show in the sidebar list
  (`utils/sidebarSessions.js:112`, `:142`, `:163`).
- `VirtualScroller` props `itemClass` / `itemStyle` are `(item) => value`, called in its
  render and bound on each `VirtualScrollerItem` wrapper (`VirtualScroller.vue:104-113`,
  `:714-715`). Exposed: `getVisibleRange()` → `{ start, end }` (end exclusive,
  `useVirtualScroll.js:353-369`; `{0, 0}` with no positions), computed from `scrollTop` and
  the measured viewport height.
- No list motion exists (no `TransitionGroup` in `src`).

### 2.2 Color scheme

- `utils/theme.js`: `setColorScheme(mode)` (`:76-80`) sets the module's scheme, toggles
  `html.wa-dark` and `html[data-color-scheme]` (`applyColorScheme`, `:13-22`) and recomputes
  the cached surface / selection colors synchronously (`:42-67`). `initTheme()` (`:86-95`,
  called from `main.js:5`) applies the scheme stored in localStorage (`:11`) and adds a
  `prefers-color-scheme` `change` listener that re-applies and recomputes.
- `stores/settings.js`: the action `setColorScheme(mode)` (`:500-504`) only sets
  `colorScheme` after validation. `initSettings()` applies the synced settings fetched
  before mount (`:1178-1182`), which may change `colorScheme`; then registers the DOM
  watcher `watch(() => store.colorScheme, (mode) => { setColorSchemeOnDom(mode);
  store._updateEffectiveColorScheme() })` (`:1298-1301`, default pre-flush, no
  `immediate`); then computes `_effectiveColorScheme` once (`:1313`) and adds a second
  `prefers-color-scheme` listener that only calls `_updateEffectiveColorScheme()`
  (`:1314-1316`). `_updateEffectiveColorScheme` is at `:1085-1093`; `_effectiveColorScheme`
  starts `null` (`:121`). So today a synced scheme that differs from the localStorage one
  reaches the store and `_effectiveColorScheme`, but not the `<html>` class, until the next
  change.
- Consumers of `_effectiveColorScheme` react in the same flush (CodeMirror
  `useCodeMirror.js:502-510`, xterm `useTerminal.js:2026-2031`, the toast theme
  `App.vue:767-768`, `GitPanel.vue:800`) or asynchronously (Mermaid,
  `MermaidDiagram.vue:49`, `MarkdownContent.vue:552`). The only other reactive reader of
  `colorScheme` is the settings select's value (`SettingsPopover.vue:1374`).
- Entry points: the settings panel select (`components/app/SettingsPopover.vue:1373-1383`, handler
  `onColorSchemeChange` `:847-849`, a `change` event whose `target` is the `wa-select`);
  the palette "Change Color Scheme…" items (`commands/staticCommands.js:1108-1117`,
  `action: () => settings.setColorScheme(X)`), which run after the palette's close
  animation (`components/app/CommandPalette.vue` `onAfterHide`, `setTimeout`); synced settings
  (`useWebSocket.js:1698-1703` → `applySyncedSettings`, `settings.js:1042`); the OS scheme
  (the two listeners above). No keyboard shortcut.
- The share viewer aliases `stores/settings` to a shim (`vite.config.share.js:20`) and has
  its own `share-session/theme.js`: this step does not reach it.
- Nothing in `src` uses `startViewTransition` or view-transition CSS. Existing names to
  avoid: the registered property `--twicc-reveal` (`motion.css:46`) and the keyframes
  `twicc-reveal` / `twicc-reveal-out` (5b).

## 3. Goals

1. The session list cascades in when a list arrives: the rows on screen fade in and rise
   `.375rem`, one after the other, 22ms apart, 320ms each.
2. A session that appears live gets the same entrance, alone.
3. Changing the color scheme from the settings panel or the palette reveals the new scheme
   in a circle that grows from the origin of §1.1 and covers the viewport in 650ms. Other
   changes, and all changes under reduced motion, fade the page. A change that leaves the
   page's scheme the same (e.g. "System" while the OS already matches) plays nothing.
4. Firefox gets the same result as Chromium (same-document view transitions measured
   present in Firefox 156, roadmap §4). A browser without `document.startViewTransition`
   switches the scheme at once, as today.

## 4. Session-list cascade

### 4.1 Rules

- **Arrival.** A scope's list *arrives* the first time the **unfiltered** list
  (`allSessions`) holds at least one row after the `SessionList` mount or after a
  `projectId` change. The spinner path (first visit, app load) and the direct path (scope
  fetched before) both reach it: in both, the rows of the new scroller render for the
  first time. Nothing else is an arrival: the next page, a background refetch, a search
  (even one that turns an empty result into rows), an archive toggle, a reorder.
- **Pending.** From the arrival until the cascade starts, every rendered row carries
  `list-arriving` (`opacity: 0`).
- **Start.** Two animation frames after the arrival (the second frame runs after the
  scroller's first ResizeObserver measurement), the composable reads the scroller's visible
  range. If a **held target** (§4.3: the selected session the list is scrolling to) is set
  and outside that range, it waits for the target's scroll promise, at most until
  `LIST_CASCADE_SETTLE_CAP_MS` = 300ms after the arrival, then one more frame, and reads
  the range again. Otherwise it starts at once. So the list stays blank two frames when
  the selected session is on the first screen (the common case), up to 300ms plus a frame
  when the list must scroll far to reach it.
- **Plan.** From the range `{start, end}` read at the start, each row whose index `i` is in
  `[start, end)` enters with index `min(i - start, LIST_CASCADE_MAX_INDEX)`,
  `LIST_CASCADE_MAX_INDEX` = 20: the rows past the 20th on screen share the last delay
  (440ms) and arrive together. Every other row drops `list-arriving` and shows at once (it
  is off screen). An empty or invalid range (`end <= start`, no scroller) drops the
  cascade: all rows show at once.
- **Live entrance.** A session id noted live (§4.4) that is absent from the previous list
  and present in the new one enters with index 0, unless the cascade is pending (the
  cascade plan covers it then). Several live ids in one update all use index 0.
- **Rekey.** A Codex draft's canonical row replaces a row the user already sees: it must
  not enter. When `bindDraftSession(draftId, sessionId)` starts with `draftId !== sessionId`
  and the draft is in this tab's store, `sessionId` is dropped from the noted ids and from
  the live entries (§4.4). Usual order (`session_bound` first): the bind starts inside
  `addSession`, before the run, so nothing plays. Reverse order (`session_updated` first):
  the canonical row's entrance starts and is cut at the bind (the row shows at once).
- **End.** An entry ends on a timer, not on `animationend` (a row unmounted mid-animation
  must not keep it; 5a rule): the cascade ends `maxIndex * 22 + 320 + 100` ms after its
  start; a live entry `320 + 100` ms after it is planned. A row outside the plan first
  rendered after the start (scroll, next page) has no class.
- **Reset.** A `projectId` change cancels every timer and frame, clears the plan, the live
  entries, the held target and the pending state, then applies the arrival rule to the new
  list in the same run.

### 4.2 Pure logic — `utils/listCascade.js` (new)

```js
export const LIST_CASCADE_STAGGER_MS = 22
export const LIST_CASCADE_DURATION_MS = 320
export const LIST_CASCADE_MAX_INDEX = 20
export const LIST_CASCADE_SETTLE_CAP_MS = 300

/** Entrance index per key for the rows on screen: Map<key, index>. */
export function planListCascade({ keys, visibleStart, visibleEnd, maxIndex = LIST_CASCADE_MAX_INDEX })

/** Keys that enter live: noted live, absent before, present now (list order). */
export function pickLiveEntrances({ previousKeys, keys, liveKeys })

/** Milliseconds from the start until the last entrance of `maxIndexUsed` has ended. */
export function listCascadeEndMs(maxIndexUsed)
```

`keys` is the list's keys in order. `planListCascade` returns an empty map when
`visibleEnd <= visibleStart` or `visibleStart >= keys.length`; it clamps `visibleEnd` to
`keys.length`. `listCascadeEndMs(n)` = `n * 22 + 320 + 100`.

### 4.3 Composable — `composables/useListCascade.js` (new)

```js
export function useListCascade({ items, getKey, sourceSize, scopeKey, getVisibleRange, env = globalThis })
// → { itemClass, itemStyle, noteLive, dropLive, holdTarget }
```

- `items`: the rendered list (a ref / computed); `sourceSize()`: the unfiltered list's
  length (arrival test); `scopeKey()`: the scope; `getVisibleRange()`: `{start, end}` or
  `null`.
- One pre-flush watcher on `[scopeKey, items, sourceSize]` (`immediate: true`), so a row
  carries its class at its first render (5a lesson: a post-flush class shows the row one
  frame, then blinks). Per run: on a scope change, reset (§4.1); then, if not yet arrived
  and `sourceSize() > 0`, arrive (state `pending`, schedule the start); else pick the live
  entrances from the ids noted since the previous run; then store the keys as
  `previousKeys` and clear the noted ids.
- The immediate run at setup is an arrival like any other when the list is already filled
  (the scroller mounts with the component).
- `holdTarget(key, promise)`: while pending, sets the held target (a later call replaces
  it). The promise counts once settled (fulfilled or rejected; its value is ignored).
  Outside pending it does nothing. It is read at the start (two frames after the arrival),
  so a call made during setup or in the post-flush of the arrival's flush is always seen.
  A held key absent from the list at the start counts as no hold (the session is not
  loaded yet; its scroll may never come).
- The start, as §4.1. At the arrival, arm the cap timer (`env.setTimeout(…, 300)`, a
  promise resolved by it). Two `env.requestAnimationFrame` frames; read the range; with a
  held target in the list but outside the range, `Promise.race([target promise, cap
  promise])`, then one frame, read the range again. The cap timer is cancelled when the
  start plans (whichever path). Each arrival has a generation number; a reset and the
  dispose both bump it, and every step of a start checks it first and stops when it
  changed (no frame requested, no plan). Then `planListCascade`; state
  `playing`; one end timer; state `idle` at the end.
- `itemClass(item)`: `pending` → `'list-arriving'`; a key in the plan or in the live
  entries → `'list-entering'`; else `null`. `itemStyle(item)`: a key in the plan or the live
  entries → `{ '--list-enter-index': index }`; else `null`.
- State that `itemClass` / `itemStyle` read is reactive: they run in the scroller's render,
  so it re-renders the rows when it changes. Timers go through `env.setTimeout` /
  `env.clearTimeout`, frames through `env.requestAnimationFrame` /
  `env.cancelAnimationFrame` (fallback `env.setTimeout(cb, 16)` / `env.clearTimeout` when
  absent). `onScopeDispose` cancels every timer and frame.
- `noteLive(id)`: adds an id to the noted set, read at the next run. `dropLive(id)`: removes
  it from the noted set and from the live entries (its timer cancelled).

### 4.4 Wiring — `SessionList.vue`

- Placement: right after `scrollerRef` and the other refs, **before** the `sessionId`
  watchers (`:271`, `:284`). The `:284` watcher's first call is synchronous in setup and
  calls `cascade.holdTarget`: the composable must exist (a `const` declared later throws)
  and must already be pending (its own immediate watcher ran first).
  ```js
  const cascade = useListCascade({
      items: sessions,
      getKey: (s) => s.id,
      sourceSize: () => allSessions.value.length,
      scopeKey: () => props.projectId,
      getVisibleRange: () => scrollerRef.value?.getVisibleRange() ?? null,
  })
  ```
- `VirtualScroller`: `:item-class="cascade.itemClass"`, `:item-style="cascade.itemStyle"`.
- Live ids: `store.$onAction(({ name, args, after }) => …)`, called in setup (Pinia
  removes the subscription when the component scope is disposed, `pinia.mjs:1151-1153`):
  - `addSession`: `cascade.noteLive(args[0].id)`.
  - `createDraftSession`: `after((id) => cascade.noteLive(id))`.
  - `bindDraftSession`: with `[draftId, sessionId] = args`, when `draftId !== sessionId`
    and `store.sessions[draftId]` exists (read at call time, before the action runs),
    `cascade.dropLive(sessionId)`. In a tab that never held the draft, the condition fails
    and the new row keeps its entrance (a session started from the CLI, another tab or
    another device).
  An id that never shows in this list (another project, a subagent) is dropped at the next
  run.
- `scrollToSession(id, attempt)` returns a `Promise<void>`: it resolves when
  `scrollToKey`'s promise settles, or when the retries give up. The `:284` watcher calls
  `cascade.holdTarget(newSessionId, scrollToSession(newSessionId))`.

### 4.5 CSS — `styles/motion.css`, after the 5a block

```css
/* Session-list cascade (step 5c). Classes and --list-enter-index are set by useListCascade
   on the virtual scroller's row wrapper. Movement × --motion-amount: reduced motion keeps
   the fade only. The delays and 320ms match utils/listCascade.js. */
@keyframes list-enter {
    from {
        opacity: 0;
        translate: 0 calc(0.375rem * var(--motion-amount));
    }
}
.list-arriving {
    opacity: 0;
}
.list-entering {
    animation: list-enter 320ms var(--motion-ease-out) both;
    animation-delay: calc(var(--list-enter-index, 0) * 22ms);
}
```

The row wrapper holds no glass (a menu opened from a row renders in the top layer), so the
opacity animation cuts no backdrop blur (5b §14 lesson).

## 5. Color-scheme reveal

### 5.1 View-transition owner — `utils/viewTransition.js` (new)

Every TwiCC view transition goes through one helper, so two users (this step, and the tab
crossfade and overlay slide of §12) never leave each other's class or timing on `<html>`.

```js
/** Run `update` (synchronous DOM change) in a document view transition of `kind`. */
export function runViewTransition(update, { kind, properties = {}, env = globalThis } = {})
```

1. `const doc = env.document`; without `doc?.startViewTransition` → `update()`, return.
2. A module run token (`++currentRun`). On `<html>`: remove every class that starts with
   `twicc-vt-` and every property set by the previous run; add `twicc-vt-<kind>`; set
   `properties` (name → value strings).
3. `doc.startViewTransition(() => update())`. The callback is synchronous:
   `::view-transition-new(root)` is a live image of the page, so what reacts later
   (CodeMirror, xterm in the next flush; Mermaid asynchronously) shows in the new image as
   it lands. Only the old image must be free of the change, and the change runs in the
   callback.
4. Catch the rejections of `ready`, `updateCallbackDone` and `finished` (a skipped
   transition rejects `ready`: no unhandled rejection). On `finished` settled, if the token
   is still `currentRun`, remove the class and the properties.
5. If `startViewTransition` throws, run `update()` at once unless it already ran (a flag),
   and clean up.

A second transition during a first one: the browser skips the running one and still runs
its callback. Each user's callback reads its state at call time, so the last one wins.

### 5.2 Scheme helper — `utils/colorSchemeTransition.js` (new)

```js
export const SCHEME_REVEAL_MS = 650

/** Radius (px) of a circle centered on (x, y) that covers a width × height viewport,
    plus a 10% margin of the larger side (the root snapshot can exceed the layout viewport
    on mobile). */
export function revealRadius(x, y, width, height)

/** Center of an element's bounding rect, { x, y }, or null. */
export function originFromElement(el)

/** Center of the viewport, { x, y }. */
export function viewportCenter(env = globalThis)

/** 'dark' or 'light' for a stored scheme ('system' follows the OS). */
export function effectiveSchemeFor(mode, env = globalThis)

/** One-shot origin for the next scheme change (consumed by takeNextSchemeOrigin). */
export function setNextSchemeOrigin(origin, env = globalThis)
export function takeNextSchemeOrigin()

/** Apply a scheme change: circle from `origin`, else a fade; nothing animates when
    `animate` is false. */
export function runSchemeTransition(apply, { origin = null, animate = true, env = globalThis } = {})
```

- `revealRadius` = `Math.hypot(Math.max(x, width - x), Math.max(y, height - y)) + 0.1 *
  Math.max(width, height)`.
- `setNextSchemeOrigin` stores the origin and clears it in `env.setTimeout(…, 0)`: the store
  watcher that consumes it runs in the microtask flush of the same task, before the
  timeout; an origin whose change never happened (same value, invalid value) cannot leak
  into a later change.
- `runSchemeTransition`: `animate` false → `apply()`, return. Else `kind` = `'circle'` when
  `origin` and not `env.matchMedia?.('(prefers-reduced-motion: reduce)').matches`, else
  `'fade'`; for `circle`, properties `--twicc-scheme-x`, `--twicc-scheme-y`,
  `--twicc-scheme-r` (px; `revealRadius` from `innerWidth` / `innerHeight`); then
  `runViewTransition(apply, { kind, properties, env })`.

### 5.3 The store applies the scheme — `stores/settings.js` `initSettings()`

```js
// Synced settings may have changed the scheme since initTheme(): reach the page before
// mount, without a transition.
setColorSchemeOnDom(store.colorScheme)

watch(() => store.colorScheme, () => {
    runSchemeTransition(applyStoreColorScheme, {
        origin: takeNextSchemeOrigin(),
        animate: effectiveSchemeFor(store.colorScheme) !== store._effectiveColorScheme,
    })
})
```

with, in `initSettings()`, `const applyStoreColorScheme = () => {
setColorSchemeOnDom(store.colorScheme); store._updateEffectiveColorScheme() }`.

- The watcher no longer changes the DOM itself: the class toggle and
  `_effectiveColorScheme` (what CodeMirror, xterm and Mermaid follow) both move into the
  callback, so the old image holds the old scheme everywhere. Each callback reads
  `store.colorScheme` at call time.
- The one-time `store._updateEffectiveColorScheme()` at `:1313` stays (it initialises
  `_effectiveColorScheme`, `null` before). Only the listener `:1314-1316` goes.
- The OS scheme: `utils/theme.js` gains `setSystemSchemeChangeHandler(fn)`; its
  `prefers-color-scheme` listener calls the handler when one is set (`null` clears it),
  else keeps today's
  `applyColorScheme(); recomputeCachedColors()`. `initSettings()` sets the handler:
  ```js
  setSystemSchemeChangeHandler(() => {
      if (store.colorScheme !== COLOR_SCHEME.SYSTEM) return
      runSchemeTransition(applyStoreColorScheme)
  })
  ```
  Under "Light" or "Dark" an OS change changes nothing visible (today it re-applies the
  same class): the handler returns.
- Import direction: `stores/settings.js` → `utils/colorSchemeTransition.js` →
  `utils/viewTransition.js` (no store, no component import): no cycle.

### 5.4 Entry points

- `components/app/SettingsPopover.vue` `onColorSchemeChange(event)`: when `event.target.value !==
  store.colorScheme`, `setNextSchemeOrigin(originFromElement(event.target))` then
  `store.setColorScheme(event.target.value)`; else nothing.
- `commands/staticCommands.js` "Change Color Scheme…": each item's action calls a local
  `changeColorScheme(mode)` that, when `mode !== settings.colorScheme`, calls
  `setNextSchemeOrigin(viewportCenter())` then `settings.setColorScheme(mode)`. The palette
  is closed at that point (its action runs after `wa-after-hide`), so it is not in the old
  image.
- Synced settings and the OS scheme set no origin: fade.

### 5.5 CSS — `styles/motion.css`

```css
/* Color-scheme change (step 5c). runViewTransition puts one twicc-vt-* class on <html>
   for the duration of the view transition. Circle: the new scheme grows over the old one
   (no crossfade). Fade: the browser's default crossfade, on our timing. */
html.twicc-vt-circle::view-transition-old(root),
html.twicc-vt-circle::view-transition-new(root) {
    animation: none;
    mix-blend-mode: normal;
}
html.twicc-vt-circle::view-transition-new(root) {
    animation: twicc-scheme-circle 650ms var(--motion-ease-out);
}
@keyframes twicc-scheme-circle {
    from { clip-path: circle(0 at var(--twicc-scheme-x) var(--twicc-scheme-y)); }
    to { clip-path: circle(var(--twicc-scheme-r) at var(--twicc-scheme-x) var(--twicc-scheme-y)); }
}
html.twicc-vt-fade::view-transition-old(root),
html.twicc-vt-fade::view-transition-new(root) {
    animation-duration: var(--motion-dur-3);
    animation-timing-function: var(--motion-ease);
}
```

The view-transition pseudo-elements originate at `<html>` and inherit its custom
properties, so the tokens and `--twicc-scheme-*` resolve. The circle needs no
reduced-motion rule: `runSchemeTransition` picks `fade` then.

## 6. Invariants

- A cascade plays only on an arrival (§4.1); a live entrance only for an id noted live and
  new in the list. A Codex draft's rekey plays nothing in the usual message order; in the
  reverse order its entrance is cut at the bind (§4.1). Nothing replays on scroll, except a
  planned row scrolled out and back during the cascade (§10).
- No row stays hidden: `list-arriving` ends at the start (at most 300ms plus three frames
  after the arrival, frames counted while the tab is visible), or at a reset, or at unmount
  (the class belongs to the composable's state).
- Movement × `--motion-amount`; no `transform` (motion.css rules, `motion.test.js`).
- The scheme reaches the page in exactly one place per path (the settings watcher, the OS
  handler), always through `runSchemeTransition`; the `<html>` class and
  `_effectiveColorScheme` change together inside the callback. The only bare calls are the
  two startup ones in `initSettings()` (DOM apply before the watcher, effective scheme
  after).
- `<html>` never keeps a `twicc-vt-*` class or a property set by `runViewTransition`
  (`--twicc-scheme-*`, `--twicc-vt-slide-*`) after the last transition ends; every view
  transition goes through `runViewTransition`.
- Without `startViewTransition`, behavior is today's (synchronous change).

## 7. Tab-panel crossfade — see §12

The user wants a real crossfade (both panels visible during the fade) for the session
center tabs and the dock regions only. A view transition fits (the old panel is an image,
so no component ever sees two panels), but it captures the old state at the next frame, so
the switch must run inside its callback: Web Awesome switches the panel synchronously in
its own click handler (`setActiveTab`), and `SessionView` / `DockRegion` react to
`wa-tab-show` right away. An uncommitted probe (`TabBar.vue` `crossfade` prop wrapping the
host's `setActiveTab` for tab-strip gestures only; `SessionView.vue`, `DockRegion.vue`,
`motion.css`) was tested by the user in Firefox (mobile and desktop) and Chrome. The result
is §12; it goes through `runViewTransition` (§5.1).

## 8. Tests (node:test, `npm test`)

`utils/listCascade.test.js`:
1. `planListCascade`: keys 0–9, range `[3, 7)` → `{3:0, 4:1, 5:2, 6:3}`; other keys absent.
2. Cap: range `[0, 30)` → key 25 has index 20; key 19 has 19.
3. Empty / invalid range (`[5, 5)`, `[8, 3)`, start past the list) → empty map; `end` past
   the list is clamped.
4. `pickLiveEntrances`: a live key new in the list → kept; a live key present before → not
   kept; a live key absent from the list → not kept; a new key not live → not kept; order
   is list order.
5. `listCascadeEndMs(0)` = 420, `listCascadeEndMs(20)` = 860.

`composables/useListCascade.test.js` (`effectScope`, fake env with manual `setTimeout` and
`requestAnimationFrame` queues, `ref` items, `await nextTick()`; the style of
`useChatEntrance.test.js`, plus a frame queue):
6. Setup with a filled list → every row `list-arriving`; after two frames with range
   `[0, 3)` → rows 0–2 `list-entering` with indexes 0–2, the others `null`; after
   `listCascadeEndMs(2)` → all `null`.
7. Setup with an empty list, then filled → arrival at the fill. Setup with `sourceSize() >
   0` but an empty filtered list (the start plans nothing), then, after two frames, a
   filtered list with rows → no class.
8. `holdTarget`: target inside the range after two frames → starts without waiting; target
   outside → waits for the promise, then one frame, reads the range again (a second range
   value is used); a promise that never settles → starts one frame after the 300ms cap timer fires; a
   rejected promise also releases; a held key absent from the list → starts after two
   frames; a call outside pending does nothing.
9. Scope change mid-pending: the old start does nothing at any step (no plan from the old
   arrival); the new list is pending.
10. After the cascade: a noted live id new in the list → `list-entering`, index 0, cleared
    after 420ms; a noted id already present → nothing; a new id not noted (next page) →
    nothing; noted ids are cleared by the run (a later appearance of the same id plays
    nothing).
11. A live id during pending → no live entry (the plan decides).
12. `getVisibleRange` returns `null` → every row `null` at the start.
13. Dispose (effect scope stop) → pending timers and frames cancelled (queues empty); a held
    promise settling after the stop requests no frame.
13b. `dropLive`: a noted id dropped before the run → no entry; a live entry dropped
    mid-animation → `null` at once, its timer cancelled.

`components/session/list/SessionList.vue` wiring (file scan, in `useListCascade.test.js`):
14. `useListCascade(` appears before the first `watch(() => props.sessionId`; the
    `VirtualScroller` binds `:item-class="cascade.itemClass"` and
    `:item-style="cascade.itemStyle"`; the `:284` watcher calls
    `cascade.holdTarget(`; the `$onAction` handler notes `addSession` ids, uses `after(` for
    `createDraftSession`, and calls `cascade.dropLive(` for `bindDraftSession` under a
    condition that compares `draftId !== sessionId` and reads `store.sessions[draftId]`.

`utils/viewTransition.test.js` (fake `document` with a `startViewTransition` spy returning
controllable `ready` / `updateCallbackDone` / `finished` promises, `documentElement` with a
real-ish `classList` and `style.setProperty` / `removeProperty`):
15. No `startViewTransition` → `update` called synchronously once, no class.
16. With it → class `twicc-vt-<kind>` and the properties set before the call; `update` runs
    inside the callback (not before); after `finished` → class and properties removed.
17. Two runs: the second removes the first's class (another kind) at its start; the first
    `finished` settling after the second started does not remove the second's class.
18. `ready` rejected (skip) → no unhandled rejection; `startViewTransition` throwing →
    `update` called once.

`utils/colorSchemeTransition.test.js`:
19. `revealRadius(0, 0, 300, 400)` = 540; `revealRadius(150, 200, 300, 400)` = 290.
20. `originFromElement` → rect center; `null` for `null`. `viewportCenter` → half of
    `innerWidth` / `innerHeight`. `effectiveSchemeFor`: `'dark'` / `'light'` pass through;
    `'system'` follows the fake `matchMedia`.
21. `runSchemeTransition`: origin, no reduced motion → kind `circle` and the three
    `--twicc-scheme-*` properties; no origin, or reduced motion → `fade`, no properties;
    `animate: false` → `apply` synchronously, no `startViewTransition` call.
22. `setNextSchemeOrigin` then `takeNextSchemeOrigin` → the origin, then `null`; not taken
    → `null` after the timeout.
23. File scan of `stores/settings.js`, on the body of `export function initSettings()`
    (sliced from that line to the first following line that is exactly `}`). First cut the
    `const applyStoreColorScheme = …` definition out of the body (from
    `const applyStoreColorScheme =` to its matching closing `}`, by brace count) and assert it contains
    `setColorSchemeOnDom(store.colorScheme)` and `store._updateEffectiveColorScheme()`. In
    the remainder: the watcher of §5.3, checked on the slice from
    `watch(() => store.colorScheme` to the next `})`, contains
    `runSchemeTransition(applyStoreColorScheme`, `takeNextSchemeOrigin()` and
    `effectiveSchemeFor(store.colorScheme) !== store._effectiveColorScheme`; `setSystemSchemeChangeHandler(`;
    exactly one `setColorSchemeOnDom(` call in total, placed before
    `watch(() => store.colorScheme`; exactly one `store._updateEffectiveColorScheme()`,
    placed after that watcher; no `addEventListener('change'`.
24. File scan of `components/app/SettingsPopover.vue` `onColorSchemeChange` and of
    `commands/staticCommands.js` `changeColorScheme`: each compares with the current
    scheme, and `setNextSchemeOrigin(` comes before `setColorScheme(`.
25. `utils/theme.js` delegation, unit test in its own file `utils/theme.test.js` (its
    global stubs must not reach the `env = globalThis` defaults of the other tests;
    `theme.js` imports in node). Stubs:
    - `globalThis.window = { matchMedia }`, where `matchMedia()` returns `{ matches: false,
      addEventListener }` and `addEventListener` captures the `change` listener;
    - `globalThis.document`: `documentElement.classList` with `add`, `remove` and `toggle`;
      `documentElement.dataset` (a plain object); `body.appendChild`; `createElement(tag)`
      returning, for `'div'`, an object with `style` and `remove()`, and for `'canvas'`, an
      object whose `getContext()` has `clearRect`, `fillStyle`, `fillRect` and
      `getImageData()` returning `{ data: [0, 0, 0, 0] }`;
    - `globalThis.getComputedStyle` returning `{ color: '' }`.

    The test counts the calls to `classList.toggle` and `document.createElement`. Steps:
    call `initTheme()`; fire the listener → `toggle` is called again (today's fallback);
    `setSystemSchemeChangeHandler(handler)`, fire → `handler` called once, no new `toggle`
    and no new `createElement` call; `setSystemSchemeChangeHandler(null)`, fire → `toggle`
    is called again and `handler` is not.
26. No other view-transition user: a scan of `frontend/src` (excluding `*.test.js`) finds
    `startViewTransition` only in `utils/viewTransition.js`.

`styles/motion.test.js` (extend, same file-scan style as its existing tests):
27. `list-enter` moves with `translate` × `--motion-amount`, no `transform`; `.list-entering`
    uses 320ms and `22ms`, matching `utils/listCascade.js` constants.
28. The `twicc-scheme-circle` keyframe uses `--twicc-scheme-x`, `--twicc-scheme-y` and
    `--twicc-scheme-r` (full names), and the circle rule uses 650ms, matching
    `SCHEME_REVEAL_MS`.

## 9. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

1. App load on a project with many sessions, no session open: spinner, then the rows on
   screen cascade top to bottom; no row flashes before its turn; rows below the fold show
   at once when scrolled to.
2. Switch project, then workspace, then "all projects", then back to a visited project
   (no spinner): each switch cascades.
3. App load on a session URL whose session is on the first screen: the cascade starts
   without a visible wait. Open a session deep in the list from search results: the list
   is blank at most about a third of a second, then the rows around the selected session
   cascade from the top of the viewport.
4. Scroll, load the next page, type in the list search, toggle archived: no cascade.
5. Create a new session (button and palette); start a session from another tab or the CLI:
   the new row enters alone; the other rows jump down at once (no reorder motion). Send
   the first message of a new Codex session: no second entrance when the draft is rekeyed.
   Start a Codex session and a Claude session from the CLI: each enters in this tab.
6. Reduced motion (OS setting): the cascade fades without rising.
7. Settings → Color scheme → Dark / Light: the circle grows from the select and covers the
   whole window, sidebar and docks included, with no strip left at an edge; on mobile,
   check the circle's start point against the select (§10); the settings panel stays
   usable after.
8. Palette → "Change Color Scheme…": the circle grows from the screen center; the palette
   is not visible in the old image.
9. Change the scheme on another device (synced): the page fades. OS scheme change under
   "System": fades. Under "Light": nothing happens. Choose "System" while the OS already
   matches the current scheme: nothing plays, clicks are not blocked.
10. With a terminal, a Files editor (CodeMirror) and a Mermaid diagram visible: all take
    the new colors inside the circle, none flips after the circle ends.
11. Two quick changes (Dark then Light within the circle): the page ends in Light, no
    stuck class on `<html>` (devtools).
12. Reduced motion: every scheme change is the fade.
13. Set a different scheme on another device while this tab is closed, then load this tab:
    the page opens in the synced scheme, no transition.

## 10. Limitations

- The cascade reads the visible range at the start. If the scroll to the selected session
  lands after the 300ms cap (very slow measurement), the rows it brings into view show at
  once. `scrollToKey` centers the selected session: when it is on the first screen but not
  centered, a short scroll can land during the cascade; the rows it brings in show at once.
- A planned row scrolled out and back during the cascade (≤ 860ms) is re-created and plays
  its entrance again.
- During the pending phase (two frames, or up to 300ms plus a frame for a far selected
  session) the list area is blank. In a hidden tab the frames wait for the tab to show.
- A live row pushes the rows below it down at once (no FLIP; not asked).
- In the tab that held a Codex draft, the canonical row never enters, even in a list that
  did not show the draft row (another scope's "active" block, which excludes drafts; a
  search that hid the "New session" label but matches the final title). The row shows at
  once.
- The cascade plays even when the sidebar is collapsed or the mobile drawer is closed; the
  drawer opened later shows a static list.
- During a view transition the page does not take pointer input: the pseudo-elements cover
  the document. Bound: capture ≤ 150ms, callback start → `ready` ≤ 400ms, then the animation (650ms circle,
  380ms fade) + 150ms (§12.3 watchdogs).
- The settings select's option list is closing (5b, 50ms) when the old image is taken; for
  the first frames outside the circle it may show frozen mid-close. The circle starts on
  the select, so it covers the list almost at once. The select's own label shows the new
  value in the old image (its value is bound to `colorScheme`).
- Iframes (Browser tab, HTML previews) have a fixed white background: the circle does not
  change them.
- On a mobile browser with a dynamic toolbar, the origin (layout-viewport coordinates) and
  the root snapshot (snapshot containing block) can be offset by the toolbar height: the
  circle may start a little off the select. The radius margin still covers the screen.

## 11. Delivery

One commit for §4–§5 after the user's browser review (roadmap §5), tests included, the
probe files reverted first. The roadmap's status row and a §6i section are updated in a
follow-up docs commit, like 5a and 5b.

## 12. Amendment — tab-panel crossfade and overlay slide (probe results, 2026-09-29)

### 12.1 User decisions — do not reopen

- **Real crossfade** (both panels visible during the fade) when the user switches a tab in
  the session's **center** tab bar, in a **dock**, or inside a **peek overlay**. Not in other
  tab bars (project view, terminal tabs, dialogs, settings).
- **Timing:** 250ms, `ease-in-out` (350ms felt long; `--motion-ease`, front-loaded, looked
  like a snap: ~80% of the change in the first ~60ms).
- **Overlay open / close:** the overlay card **slides** from its edge (in) and back to it
  (out); its backdrop fades. A tab change inside an open overlay crossfades.
- Programmatic switches (back / forward, palette, keyboard shortcuts, links from the chat,
  gutter swap / restore, a drag) stay instant.

### 12.2 What the probe established (Firefox Android, Firefox desktop, Chrome desktop)

The probe (uncommitted, v1–v10; recordings and frame viewers in the session artifacts
`video12-tabs` … `video22-v10`) measured, with an on-screen debug line:

1. A view transition captures the old state at the next frame, after `requestAnimationFrame`
   callbacks. A tab click also runs `requestPaneFocus` (`SessionView.vue:972`, HEAD), whose
   rAF claim calls `switchToTab`: it switched the tab **before** the capture, so both images
   showed the new tab (invisible fade; old-state capture 120–590ms after the click). Cancelling
   the claim when the transition starts fixed it (capture 8–77ms after the click).
2. Web Awesome switches `wa-tab-panel`s synchronously in its own click / keydown handlers
   (`setActiveTab`): the switch must be deferred into the transition's update callback. A
   wrapper on the host's `setActiveTab`, armed by a capture-phase click / keydown on the bar,
   does it.
3. Every overlay tab change ends in `overlay-activate` → route (`SessionView.vue:1073`,
   HEAD): its bar's click (Web Awesome `setActiveTab` → `wa-tab-show` → LayoutOverlay
   `onShow` → `select` → SessionLayout `onOverlaySelect`, `SessionLayout.vue:124-127`) and the
   gutter chips. The panel shown in the overlay follows the route, so wrapping
   `onOverlayActivate` / `onOverlayDismiss` covers tab changes, opening and closing, with no
   interception on the overlay's bar (the probe's bar-level wrapper never fired there).
4. The browser's default crossfade (`plus-lighter`) collapsed to one washed-out frame on
   Firefox Android; our own keyframe (old image opaque, new image fading in over it) renders
   everywhere.
5. The update callback must wait for the route and Vue: `await` the navigation, `nextTick`,
   one macrotask, `nextTick` (then the new image is right; it is live afterwards anyway).
6. The overlay's pooled iframe (FrameHost, `overlay` tier) is outside the overlay card: it
   needs its own transition group, sliding by the same viewport distance, to stay aligned.
7. When the phone is busy (first render of a heavy tab, other work), a transition can last
   1–3s: the capture waits, or frames stall. During a transition the page takes no pointer
   input, so clicks were lost. Hence the watchdog of §12.3.

### 12.3 `runViewTransition` — additions (`utils/viewTransition.js`, §5.1)

```js
export function runViewTransition(update, {
    kind, properties = {}, settle = false,
    startTimeoutMs = 150, updateTimeoutMs = 400, overrunMs = 150, env = globalThis,
} = {})   // → undefined, in every path
export function isViewTransitionUpdating()
export function supportsViewTransitions(env = globalThis)   // typeof env.document?.startViewTransition === 'function'
```

- `supportsViewTransitions` is the only support test in the app: `utils/viewTransition.js`
  stays the only file that names `startViewTransition` (test 26).

- **Async update.** With `settle: true` the callback is `async`: `await update()` (a promise
  or a value), then `await nextTick()`, `await` one `env.setTimeout(…, 0)`, `await
  nextTick()`. With `settle: false` (the scheme, §5) it stays synchronous. `nextTick` comes
  from `vue` (a utility importing `vue` only: no cycle).
- **Nesting.** A module depth counter is incremented when a callback starts and decremented
  in its `finally` (a skipped transition's callback may still run late and overlap the next
  one: a counter, not a boolean). `isViewTransitionUpdating()` returns `depth > 0`.
  `runViewTransition` called while it is true runs `update()` directly (no second transition:
  a second `startViewTransition` would skip the first).
- **Start watchdog.** If the callback has not started `startTimeoutMs` after the call (the
  old-state capture is late: the page is busy), `transition.skipTransition()`. The browser
  still runs the callback: the switch happens, without animation.
- **Update watchdog.** If `ready` has not settled `updateTimeoutMs` after the callback
  started (a slow navigation, a heavy first render, or a stalled new-state capture),
  `transition.skipTransition()`: the callback keeps running, but the page is shown live
  again instead of the frozen old image. Not armed when `ready` has already settled at
  callback start (for example, the start watchdog or a later transition skipped it). Two
  flags detect it: `readySettled`, set in `ready`'s fulfil and reject handlers, and
  `skipped`, set synchronously wherever the helper itself calls `skipTransition()`. 400ms keeps every normal switch measured by the
  probe (update 50–250ms).
- **Update cap.** With `settle: true`, `await update()` is raced against a 3000ms timer
  (`env.setTimeout`, cleared when `update()` settles first): a navigation promise that never
  settles cannot keep the depth counter up nor leave the class on `<html>`.
- **Overrun watchdog.** When `ready` fulfils (not on rejection), read the view-transition pseudo animations
  (`doc.documentElement.getAnimations({ subtree: true })`, `effect.pseudoElement` starting
  with `::view-transition`); arm a timer at the largest `effect.getComputedTiming().endTime`
  + `overrunMs` (an end time of 1000ms when no such animation is found, or when
  `documentElement.getAnimations` is absent: optional-chained); if `finished` has not
  settled by then, `skipTransition()`. Cancelled when `finished` settles.
- The three watchdogs apply to every kind (the circle is skipped the same way on a busy
  page); each timer goes through `env.setTimeout` / `env.clearTimeout` and is cleared when
  its phase ends.
- The class / properties / token rules of §5.1 are unchanged.

### 12.4 Tab bars — `components/ui/TabBar.vue` + `utils/tabCrossfade.js` (new)

```js
// utils/tabCrossfade.js
export function installTabCrossfade(host, { onStart, run = runViewTransition, env = globalThis })   // → uninstall()
```

- **Arming** follows Web Awesome's own test (`handleClick` / `handleKeyDown`,
  `chunk.PVB6QGII.js`): only a gesture **on a tab of this bar** arms, never one inside a
  panel (the center host contains its `wa-tab-panel`s: a chat link, the composer, a shortcut
  pressed in a panel, a subagent tab's close icon must not arm).
  - capture-phase `click` on `host`: `tab = event.target.closest('wa-tab')`; arms when
    `tab?.closest('wa-tab-group') === host` and the target is inside neither the tab's close
    icon (`.tab-close-icon`) nor a `wa-dropdown` (a dock tab's `TabPlacementMenu`, whose
    `@click.stop` would skip the bubble disarm); records `armedTab = tab`.
  - capture-phase `keydown` on `host`: arms when `event.target` is a `wa-tab` whose closest
    group is `host` and `event.key` is one of `Enter`, ` `, `ArrowLeft`, `ArrowRight`,
    `ArrowUp`, `ArrowDown`, `Home`, `End`; records `armedTab = null` (the key picks another
    tab).
  - **Disarm:** a bubble-phase `click` / `keydown` listener on `host` (it runs after Web
    Awesome's shadow handler, same dispatch) clears the flag; `env.setTimeout(…, 0)` also
    clears it (fallback).
- Shadows `host.setActiveTab` with an own property; `original = host.setActiveTab.bind(host)`
  is taken first (the prototype method reads `this.activeTab`, `this.tabs`, `this.panels`).
  The wrapper calls the original at once
  when: not armed, no tab, the tab is `host.activeTab`, the tab is disabled, the tab belongs
  to another group, or `armedTab` is set and differs from the tab. Otherwise it disarms,
  calls `onStart()` synchronously, then `run(() => original(tab, options), { kind: 'tab',
  settle: true, env })`.
- `uninstall()` deletes the own property and removes the four listeners.
- `TabBar.vue`: a Boolean prop `crossfade` (default `false`) and an emit `crossfade-start`.
  On mount, when `crossfade` and `supportsViewTransitions()`,
  `installTabCrossfade(el.value, { onStart: () => emit('crossfade-start') })`; uninstall on
  unmount. The prop is read at mount (the bars that use it never toggle it).
- Users: the center bar (`SessionView.vue:2278` HEAD, `ref="sessionTabsRef"`) with
  `crossfade @crossfade-start="cancelPaneFocus"`; the dock bar (`DockRegion.vue:105` HEAD)
  with `crossfade @crossfade-start="emit('crossfade-start')"`; `DockRegion` declares
  `crossfade-start`; `SessionLayout` declares it and forwards it from **both** DockRegion
  instances (the maximized one, `SessionLayout.vue:681`, and the normal ones, `:701`, HEAD);
  `SessionView` binds
  `@crossfade-start="cancelPaneFocus"` on `SessionLayout`. The overlay's bar
  (`LayoutOverlay.vue`) does not take `crossfade` (§12.5 covers it).
- Why cancelling is safe: the transition's own switch reaches `onTabShow` →
  `cancelPaneFocus` + `switchToTab` (center, `SessionView.vue:1546` HEAD) or DockRegion
  `onShow` → `select` → `onLayoutSelectTab` → `cancelPaneFocus` + `switchToTab`: the same
  route claim, one frame later.

### 12.5 Overlay — `views/SessionView.vue`, `components/frames/FrameHost.vue`

- `onOverlayActivate(tabId)` and `onOverlayDismiss()` keep their bodies as
  `overlayActivateNow(tabId)` / `overlayDismissNow()`, which **return** the navigation promise
  (`switchToTab` already returns `router.push`'s; the `update` awaits it). The handlers
  become:
  ```js
  function onOverlayActivate(tabId) {
      // Re-activating the shown tab (a gutter chip's double click) navigates nowhere.
      if (tabId === activeTabId.value) return overlayActivateNow(tabId)
      const slideEdge = layout.openOverlayEdge.value ? null : layout.overlayEdgeForTab(tabId)
      runOverlayTransition(() => overlayActivateNow(tabId), slideEdge)
  }
  function onOverlayDismiss() {
      runOverlayTransition(() => overlayDismissNow(), layout.openOverlayEdge.value)
  }
  ```
  An overlay open on one edge replaced by an overlay on another edge (a chip of the other
  gutter) is a tab change: it crossfades (intended; a slide would need both cards named at
  once).
  `runOverlayTransition(update, slideEdge)`: `cancelPaneFocus()`, then
  `runViewTransition(update, { kind, properties, settle: true })` with `kind` = `'overlay'`
  and properties `--twicc-vt-slide-x` / `--twicc-vt-slide-y` from `SLIDE_OFFSETS[slideEdge]`
  when `slideEdge` is set and `window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches`
  is not true (the query that drives `--motion-amount`, `motion.css:66`); else `kind` = `'tab'` (a tab change in
  an open overlay; any open / close under reduced motion).
  `SLIDE_OFFSETS = { right: ['100vw', '0px'], left: ['-100vw', '0px'], bottom: ['0px',
  '100vh'] }`: the gutter edges the resolver produces (`utils/layoutResolver.js:388`,
  `for (const edge of ['left', 'right', 'bottom'])`); any other value → `'tab'`.
- `onLayoutTabDragStart` (`SessionView.vue:1020` HEAD) calls `overlayDismissNow()`: no
  transition during a drag (pointer input must keep flowing).
- `FrameHost.vue`: the cell gets `frame-cell--overlay` when `frame.zTier === 'overlay'`.

### 12.6 CSS — `styles/motion.css`

```css
/* Tab crossfade and overlay (step 5c §12). The old image stays opaque under the new one,
   which fades in (the UA crossfade's plus-lighter blend collapsed on Firefox Android). */
html.twicc-vt-tab::view-transition-old(root),
html.twicc-vt-overlay::view-transition-old(root) {
    animation: none;
    mix-blend-mode: normal;
}
html.twicc-vt-tab::view-transition-new(root),
html.twicc-vt-overlay::view-transition-new(root) {
    animation: twicc-vt-fade-in 250ms ease-in-out both;
    mix-blend-mode: normal;
}
@keyframes twicc-vt-fade-in {
    from { opacity: 0; }
}
/* Overlay open / close: the card and its pooled iframe cell get their own groups and slide
   by the same viewport distance (they stay aligned); the backdrop fades with the root. */
html.twicc-vt-overlay .layout-overlay {
    view-transition-name: twicc-overlay;
}
/* Visible cells only: a kept-alive session's hidden overlay cell (visibility: hidden, still
   captured) would duplicate the name and abort the transition. */
html.twicc-vt-overlay .frame-cell--overlay:not(.frame-cell--hidden) {
    view-transition-name: twicc-overlay-frame;
}
html.twicc-vt-overlay::view-transition-new(twicc-overlay),
html.twicc-vt-overlay::view-transition-new(twicc-overlay-frame) {
    animation: twicc-vt-slide-in 300ms var(--motion-ease-out) both;
    mix-blend-mode: normal;
}
html.twicc-vt-overlay::view-transition-old(twicc-overlay),
html.twicc-vt-overlay::view-transition-old(twicc-overlay-frame) {
    animation: twicc-vt-slide-out 220ms ease-in both;
    mix-blend-mode: normal;
}
@keyframes twicc-vt-slide-in {
    from { translate: calc(var(--twicc-vt-slide-x) * var(--motion-amount)) calc(var(--twicc-vt-slide-y) * var(--motion-amount)); }
}
@keyframes twicc-vt-slide-out {
    to { translate: calc(var(--twicc-vt-slide-x) * var(--motion-amount)) calc(var(--twicc-vt-slide-y) * var(--motion-amount)); }
}
```

- **§5.5 amended (code review, 2026-09-29):** the scheme fade uses the same pattern (old
  image `animation: none; mix-blend-mode: normal`; new image
  `twicc-vt-fade-in var(--motion-dur-3) ease-in-out both`), not the UA crossfade, which
  collapses on Firefox Android (§12.2 point 4); `--motion-ease` is also front-loaded for a
  fade (§12.1). Test 28 pins it.
- The names exist only while `twicc-vt-overlay` is on `<html>`: the scheme circle and the tab
  crossfade keep a single root group.
- The slide never runs under reduced motion (§12.5 picks `'tab'`); the `--motion-amount`
  factor keeps `motion.test.js` test 8 true regardless.

### 12.7 Invariants (amend §6)

- A tab crossfade starts only from a user click / key on the center or a dock tab bar, or
  from `overlay-activate` / `overlay-dismiss`; the old image never shows the new **panel**
  (the pane-focus claim is cancelled before the capture). The overlay's own tab strip is the
  known exception (§12.10).
- At most one transition at a time: a switch requested during an update callback runs inside
  it.
- A transition blocks input and rendering at most: capture ≤ 150ms, then callback start →
  `ready` ≤ 400ms (after which the live page shows again), then its longest animation +
  150ms (1000ms + 150ms if none is found).
- Every view transition still goes through `runViewTransition`, and only
  `utils/viewTransition.js` names `startViewTransition` (test 26).

### 12.8 Tests (amend §8)

`utils/viewTransition.test.js` (extend). The fake env of tests 15–18 gains `setTimeout` /
`clearTimeout` (a manual queue), `documentElement.getAnimations()` (returns a configurable
list) and a `startViewTransition` fake that captures the callback (invoked by the test),
settles `updateCallbackDone` from the callback's returned promise, and exposes
`skipTransition` as a spy that also rejects `ready` when it is still pending.
29. `settle: true`: `update` returning a promise → the callback resolves only after that
    promise and the fake env's 0ms timer (the `nextTick`s are not observable);
    `isViewTransitionUpdating()` is true inside `update`, false after, and false after an
    `update` that throws; two overlapping callbacks (call `runViewTransition` twice before
    invoking either captured callback, then invoke both) → still true until both ended. `runViewTransition` returns `undefined` in
    every path.
30. Nesting: `runViewTransition` called inside an update → `update` runs directly, no second
    `startViewTransition` call.
31. Start watchdog: the fake `startViewTransition` does not call its callback; after 150ms →
    `skipTransition` called once; a callback invoked before 150ms → no skip.
32. Overrun watchdog: `ready` resolves while `documentElement.getAnimations()` returns fake
    pseudo animations whose `getComputedTiming().endTime` is 250; `finished` unsettled at
    250 + 150ms → `skipTransition`; `finished` settled at 260ms → timer cleared, no skip. No
    pseudo animation, or `getAnimations` removed from the fake → the bound is 1000 + 150ms, no
    throw.
32b. Update watchdog: the callback started but `ready` does not settle → `skipTransition` at
    400ms after the callback start; `ready` settling at 300ms → no skip. Update cap: an
    `update` promise that never settles → after 3000ms (and the test flushing the following
    0ms timer) the chain continues, the depth counter returns to 0. A start-watchdog skip
    before the callback started: after the 150ms timer fires, flush microtasks, then invoke
    the callback → no 400ms timer queued (the 3000ms update-cap timer may be).
32c. `supportsViewTransitions`: true with a fake `document.startViewTransition` function,
    false without `document` or without the method.

`utils/tabCrossfade.test.js` (new; the fake `host` is a class instance with `setActiveTab`
on its prototype and `activeTab`; it records `addEventListener` / `removeEventListener` with
their capture flag, and the test dispatches by calling the recorded listeners in order:
capture, then a stand-in for Web Awesome's handler, then bubble; fake tabs and panel
elements with `closest()` and `disabled`):
33. A captured click on a tab of `host`, then `setActiveTab(thatTab)` in the same dispatch →
    `onStart` once, the injected `run` spy called with `kind: 'tab'`, `settle: true`, the
    original called only inside its update, with `this === host` (the fake prototype method
    asserts it; also for the pass-through calls of test 34).
34. Original at once, no `onStart`: no gesture (programmatic `setActiveTab`); same tab;
    disabled tab; tab of another group; a different tab than the clicked one; after the
    bubble-phase disarm; after the macrotask fallback.
35. Not armed: a click inside a panel of `host` followed by a microtask `setActiveTab(other)`
    (a chat link → route → `active` watch); a click on a tab's `.tab-close-icon`; a click inside a `wa-dropdown` in a tab; a keydown
    in a panel; a keydown on a tab with another key (`a`). Armed: a keydown `ArrowRight` on a
    tab of `host` → any tab of `host` is accepted.
36. `uninstall()` → `host.setActiveTab` is the prototype's again, the four listeners
    removed.

File scans:
37. `views/SessionView.vue`: the center `TabBar` has `crossfade` and
    `@crossfade-start="cancelPaneFocus"`; `SessionLayout` has
    `@crossfade-start="cancelPaneFocus"`; `onOverlayActivate` / `onOverlayDismiss` call
    `runOverlayTransition(`; `onLayoutTabDragStart` calls `overlayDismissNow()`.
38. `components/session/layout/DockRegion.vue` declares and emits `crossfade-start`;
    `SessionLayout.vue` declares it and both `<DockRegion` elements carry
    `@crossfade-start`; `LayoutOverlay.vue`'s `TabBar` has no `crossfade`; `TabBar.vue` and
    `tabCrossfade.js` do not contain `startViewTransition`.
39. `styles/motion.css`: the rules of §12.6 (`twicc-vt-fade-in` 250ms `ease-in-out`; slide
    offsets × `--motion-amount`; `view-transition-name` only under `html.twicc-vt-overlay`;
    the frame cell rule carries `:not(.frame-cell--hidden)`).

### 12.9 Browser checks (amend §9; done in the probe unless marked *new*)

14. Center and dock tab clicks (also in a maximized dock), keyboard on a tab strip: 250ms
    crossfade; no freeze longer than the new tab's own render (≤ 400ms, §12.3).
15. Tab change inside an overlay (its bar and the gutter chips): crossfade.
16. Overlay open / close: the card slides from / to its edge, the backdrop fades; with a
    Browser or HTML preview tab inside, the iframe slides with the card. *New:* bottom edge.
17. Double-click an inactive dock tab: crossfade then maximize (verified in the probe: the
    second click lands before the animation phase; one landing during the animation would
    be lost, §12.10).
18. Drag a tab to another dock: the drop works, no transition during the drag.
19. Terminal and Files tabs in and out: no mis-sized terminal, no editor jump.
20. Three quick clicks: the final tab is right.
21. *New:* the watchdog — on a busy phone (first visit of a heavy tab), the switch lands
    without a long freeze and a following click is not lost.
22. *New:* back / forward, the palette, a link from the chat, a shortcut typed in the
    composer, closing the active subagent tab: instant.
23. *New:* reduced motion: tabs crossfade; the overlay opens / closes with the crossfade, no
    slide.

### 12.10 Limitations (amend §10)

- During a crossfade (250ms) or a slide (300ms) the page takes no pointer input; a click then
  is lost (rare at these durations).
- Live content (streaming chat, a running terminal) outside the switched region shows as a
  still image for the transition's duration (the old image covers it, the new one fades in
  live).
- The ink under the tabs glides in the new image while the old image still shows it at the
  old tab: a faint ghost of the old line fades out.
- On a busy page the watchdogs cancel the animation: the switch is instant, as today. A
  slow update (> 400ms) shows the frozen old image up to 400ms, then the live page.
- The overlay's own tab strip (active tab and ink) already shows the new tab in the old
  image: Web Awesome switches it synchronously in its click handler, before the capture.
- During an overlay slide, the named groups paint above the whole page: the card passes
  over the gutters, and a left-edge card over the sidebar, until it lands.
- Moving from an overlay on one edge to an overlay on another edge crossfades (no slide).
- Programmatic switches (§12.1) are instant.

### 12.11 Delivery (amend §11)

Same commit as §4–§5. The probe files (`TabBar.vue`, `SessionView.vue`, `DockRegion.vue`,
`LayoutOverlay.vue`, `FrameHost.vue`, `motion.css`) are reverted before the implementation
starts; the implementation follows §12, not the probe code (the probe's debug line and
logs never ship).
