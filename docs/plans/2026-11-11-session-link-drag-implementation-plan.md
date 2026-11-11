# Session link drag and drop — Implementation Plan

> **Superseded (2026-11-11).** The owner chose a simpler implementation, modelled on the layout tab drag:
> pointer events for mouse and touch, a long press then a sideways move on touch (rows wear
> `touch-action: pan-y`, so a vertical move stays a native scroll), the existing chat drop overlay, and
> the row menu entry. No HTML5 drag, no cross-window support, no feasibility spike. The code
> (`frontend/src/composables/useSessionLinkDrag.js`, `frontend/src/utils/sessionLink.js`) is the reference.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drag a sidebar session (desktop: native drag; touch: long-press then move) or use a row menu entry to insert a canonical Markdown link to that session into the composer of the open session.

**Architecture:** Pure helpers (`utils/sessionLink.js`) build the link, the private drag types and the spacing. One module-level store (`composables/useSessionLinkDrag.js`) holds the composer registry, the drag state (with a per-drag nonce), the drawer bridge, the touch/pointer input tracking and the stale-state cleanup. Desktop rides the existing HTML5 drop zones (`SessionItemsList`, `useDragHover`, `pendingDropData`) with extra private drag types. Touch uses a small controller (`utils/sessionLinkTouch.js`) started by the row at arming time; it freezes the list scroll, draws a top-layer ghost and hit-tests the chat by geometry.

**Tech Stack:** Vue 3 `<script setup>`, Pinia, Web Awesome 3, Vue Router, vue-i18n. Tests: `node --test` (`cd frontend && npm test`), `node:assert/strict`, real `vue` reactivity in Node, and the SFC-extraction harnesses already used in the repo.

**Spec:** [`docs/plans/2026-11-11-session-link-drag-design.md`](2026-11-11-session-link-drag-design.md). Read it before any task. Section names quoted below refer to it.

## Global Constraints

- Work on the current branch. No branch, no worktree, unless the owner asks.
- Commit steps run only if the owner authorized commits for this execution. Stage files one by one (`git add <file> …`), never a directory, `-A` or `.`; check `git diff --cached --name-only` before committing. Message: Conventional Commit, lowercase summary, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`.
- Never run `npm install` / `npm ci`, `migrate`, or a dev-server restart.
- Do not edit any file in `docs/plans/` during execution.
- Every interface string uses i18n in the five locales (`en`, `de`, `es`, `fr`, `pt-BR`), flat keys. Registers: French `vous`, German `du`, Spanish `tú`, pt-BR `você`. French guillemets use U+00A0 inside (`« … »`).
- Web Awesome: icon slot in `wa-dropdown-item` is `slot="icon"`. Custom `wa-*` events bubble: guard with `event.target === <the element listened on>`.
- Firefox is the reference browser; manual checks run in Firefox first. Mobile checks: Firefox Android, then both Android app flavors (WebView, Gecko).
- Reduced motion: no scale/translate; color and opacity changes only.
- Link format, verbatim: `[<label>](/project/<project_id>/session/<session_id>)`; path from `router.resolve({ name: 'session', params: { projectId, sessionId } }).href`.
- Label escaping: line breaks → one space; backslash-escape `` \ [ ] ` * < > ~ & `` always; `_` only when it is not between two letters or digits.
- Thresholds, verbatim: long-press 500 ms; scroll slop 10 px before arming; drag start > 10 px from the position at arming time; drop readiness ≥ 10 px of finger travel inside the chat, counted from the drag start; ghost bottom edge 24 px above the finger; touch-owned window: from `touchstart` until 1000 ms after `touchend`/`touchcancel`; recent pointer input window 1000 ms.
- Row touch listeners stay passive (`touchstart`, `touchmove`). No `preventDefault()` on any `touchmove` in this feature. New document-level touch listeners are passive.

## Review Focus

1. A refused session-link drag (self-link, no composer) followed by a file or text drag: the overlay must appear for the second drag. Test in Task 6.
2. The source row is recycled (`session.id` changes) or unmounted while armed or dragging: the gesture continues, ends normally, and the lift never shows on another session. Test in Task 9.
3. A press during which the list already scrolled: no armed phase, multi-select at 500 ms exactly as today. Test in Task 9.
4. A title with Markdown characters, line breaks and `snake_case`: the rendered link text equals the title. Test in Task 2.
5. A lost `dragend` (source row unmounted during a desktop drag), or a stale state from an earlier drag: the state clears on the next pointer activity, a drawer closed by the drag reopens, and a later drag from another window is neither refused nor mislabelled. Tests in Tasks 3 and 6.

---

### Task 1: Feasibility gate (throwaway spike, owner-run)

Spec section "Feasibility gate". Nothing from this task is committed.

**Files:**
- Create (throwaway, never committed): `frontend/public/spike-session-link.html`

**Interfaces:**
- Consumes: nothing.
- Produces: the owner's result per check. Later tasks start only after it.

- [ ] **Step 1: Write the spike page.** One self-contained HTML file, no framework. It reproduces:
  - a fixed drawer (`position: absolute`, `transform: translateX(...)` toggled by a button, width `min(300px, 80vw)`) holding a scroll container with `overflow-y: auto; overscroll-behavior: contain` and 60 rows; each row is a custom element with an open shadow root containing `<a part="base" href="#rowN">`, with passive `touchstart`/`touchmove` listeners on the host;
  - toggles: "short list" (5 rows, not scrollable), "wide layout" (drawer static beside the chat), "busy main thread" (`setInterval` blocking 40 ms every 50 ms), "reorder during drag" (remove and re-insert the source row 300 ms after the drag starts);
  - a chat area with an `<iframe srcdoc>` inside;
  - the gesture: 500 ms long-press with 10 px slop; arming adds `overflow-y: hidden` on the scroll container and `overscroll-behavior: none` on `html` and `body`; a move > 10 px closes the drawer and shows a top-layer ghost (`popover="manual"`, `showPopover()`, `pointer-events: none`) 24 px above the finger; gesture listeners on `event.composedPath()[0]`; a passive document `touchstart` listener that logs a second finger;
  - native-drag suppression for touch: a host class set at `touchstart` that applies `-webkit-user-drag: none` through `::part(base)`, and a `dragstart` listener that calls `preventDefault()` while a touch is in progress or ended less than 1000 ms ago;
  - desktop: `dragstart` adds `application/x-twicc-session-link-<hex of location.origin>` (value `'x'`) and `application/x-twicc-session-link-nonce-<random>` (value `'1'`) types, and calls `setDragImage` with a plain DOM card appended at `top:-1000px` and removed on the next frame; a "close drawer at drag start" toggle (`setTimeout 0`); a drop zone that logs `[...dataTransfer.types]` on `dragover`, to open in a second window; a log of `mousemove`/`pointerdown` seen during the drag;
  - an on-page log: `touchcancel`, `dragstart`, list `scrollTop`, `window.scrollY`, `visualViewport.offsetTop`.
- [ ] **Step 2: Hand the page to the owner and stop.** Give the URL (Vite dev server, `/spike-session-link.html`; for the Android app flavors, the same URL if the app can open it, otherwise say that Chrome Android stands for WebView and Firefox Android for Gecko). List the spec's "Feasibility gate" checks, one line each, plus "no `mousemove`/`pointerdown` is logged during a native drag" (Firefox, Chromium). Wait for the results.
- [ ] **Step 3: Record the outcome.** Everything passes: delete `frontend/public/spike-session-link.html`, continue with Task 2, and keep the CSS mechanism that passed for Task 4. Only the cross-window type check fails: delete the page and continue; no code changes (see "Self-review notes"), and Task 11 skips the cross-window acceptance lines. Any other failure: delete the page, stop, and ask the owner.

---

### Task 2: Pure link helpers

**Files:**
- Create: `frontend/src/utils/sessionLink.js`
- Test: `frontend/src/utils/sessionLink.test.js`

**Interfaces:**
- Consumes: `getSessionDisplayName(session)` from `frontend/src/utils/sessionTitle.js`; the test also uses `parseMarkdownTokens` from `frontend/src/utils/markdown.js`.
- Produces:
  - `SESSION_LINK_MIME_PREFIX = 'application/x-twicc-session-link-'`, `SESSION_LINK_NONCE_PREFIX = 'application/x-twicc-session-link-nonce-'`.
  - `sessionLinkMimeType(origin: string): string` — prefix + lowercase hex of the UTF-8 bytes of `origin`.
  - `sessionLinkNonceType(nonce: string): string` — nonce prefix + `nonce` (lowercase `[0-9a-f]`).
  - `classifySessionLinkTypes(types: Iterable<string>, origin: string): 'own' | 'foreign' | null` — ignores nonce types; `'own'` when the own type is present, `'foreign'` when another prefixed type is present, else `null`.
  - `escapeLinkLabel(title: string): string`
  - `buildSessionLinkPayload(session, router): { sessionId: string, projectId: string, title: string, markdown: string }` — `title` = `getSessionDisplayName(session)`, `markdown` = `[escapeLinkLabel(title)](<canonical path>)`.
  - `encodeSessionLinkPayload(payload): string` (JSON) / `decodeSessionLinkPayload(raw: string): payload | null` (null on bad JSON or when `sessionId`/`markdown` are not strings).
  - `padInsertion(before: string, text: string, after: string): string` — `' '` prepended when `before` is non-empty and its last char is not `/\s/`; `' '` appended when `after` is non-empty and its first char is not `/\s/`.
  - `classifyDragTypes(types: Iterable<string>, { origin, selfLinked: boolean, hasComposer: boolean }): 'files' | 'session' | 'text' | null` — `'files'` if `Files` present; else if the own type: `null` when `selfLinked` or `!hasComposer`, else `'session'`; else `'text'` if `text/plain` present; else `null`.

- [ ] **Step 1: Write the failing tests** in `sessionLink.test.js` (`import test from 'node:test'`, `assert/strict`). Cases:
  - `escapeLinkLabel('Fix [bug] *now*\nand `x` <b> ~a~ & c\\d')` → `'Fix \\[bug\\] \\*now\\* and \\`x\\` \\<b\\> \\~a\\~ \\& c\\\\d'`.
  - `escapeLinkLabel('snake_case and _lead trail_')` → `'snake_case and \\_lead trail\\_'`; `escapeLinkLabel('a\r\nb')` → `'a b'`.
  - Review Focus 4: for each title in `['Fix [bug] *now*', 'a_b and _c_ snake_case', 'x `y` <z> ~w~ & \\']`, `parseMarkdownTokens(buildSessionLinkPayload({ id: 's', project_id: 'p', title }, router).markdown)` (from `utils/markdown.js`; `renderMarkdown` cannot run in Node, DOMPurify needs a DOM) gives one inline token whose children are exactly `link_open` (`href` = the path), one `text` with `content === title`, `link_close`.
  - `buildSessionLinkPayload({ id: 's1', project_id: '-home-p', title: 'T' }, router).markdown` → `'[T](/project/-home-p/session/s1)'`, with a stub router whose `resolve(loc)` asserts `loc.name === 'session'` and returns `{ href: '/project/' + loc.params.projectId + '/session/' + loc.params.sessionId }`.
  - `sessionLinkMimeType('http://a:1')` starts with the prefix, is lowercase, differs from `sessionLinkMimeType('http://a:2')`.
  - `classifySessionLinkTypes([own, sessionLinkNonceType('ab'), 'text/plain'], o)` → `'own'`; another origin's type → `'foreign'`; `[sessionLinkNonceType('ab'), 'text/plain']` → `null`.
  - `classifyDragTypes`: `['Files', own]` → `'files'`; `[own, 'text/plain']` → `'session'`; with `selfLinked: true` → `null`; with `hasComposer: false` → `null`; `[foreign, 'text/plain']` → `'text'`; `[]` → `null`.
  - `padInsertion('', 'L', '')` → `'L'`; `('a', 'L', 'b')` → `' L '`; `('a ', 'L', '\nb')` → `'L'`; `('a\n', 'L', '')` → `'L'`.
  - `decodeSessionLinkPayload(encodeSessionLinkPayload(p))` deep-equals `p`; `'{'` → `null`; `'{"sessionId":1}'` → `null`.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/sessionLink.test.js`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the exports. Hex: `Array.from(new TextEncoder().encode(origin), b => b.toString(16).padStart(2, '0')).join('')`. `_` rule: escape unless both neighbours match `/[\p{L}\p{N}]/u`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `frontend/src/utils/sessionLink.js`, `frontend/src/utils/sessionLink.test.js` — `feat(sessions): add session link helpers`.

---

### Task 3: Shared session-link drag store

**Files:**
- Create: `frontend/src/composables/useSessionLinkDrag.js`
- Test: `frontend/src/composables/useSessionLinkDrag.test.js`

**Interfaces:**
- Consumes: `vue` (`reactive`, `computed`), `vue-router` (`useRoute`, default argument of `useSessionLinkTarget` only), Task 2 (`sessionLinkNonceType`).
- Produces (named exports; module-level singletons):
  - `sessionLinkDrag`: `reactive({ session: null | Snapshot, source: null | 'mouse' | 'touch', nonce: null | string, touchDropSessionId: null | string })`, `Snapshot = { id, projectId, title, projectName, color, width, payload }`.
  - `registerSessionLinkComposer(sessionId, entry: { el: () => HTMLElement | null, isChatVisible: () => boolean, insert: (markdown, { linkedSessionId, input: 'mouse' | 'touch' | null }) => Promise<boolean> }): () => void` — returns the unregister function; a newer registration for the same id replaces the older one, and the older unregister function does not remove the newer entry. The registry is a `reactive(new Map())`.
  - `getSessionLinkComposer(sessionId: string | null)`: entry or `null`.
  - `isComposerZoneVisible(entry): boolean` — `!!entry && entry.isChatVisible() && !!el && el.isConnected && el.getClientRects().length > 0` (`el = entry.el()`).
  - `useSessionLinkTarget(route = useRoute()): { targetSessionId: ComputedRef<string | null>, isTargetChatVisible: () => boolean }` — `targetSessionId` = `route.params.sessionId` when the registry has it, else `null`; `isTargetChatVisible()` = `isComposerZoneVisible(getSessionLinkComposer(targetSessionId.value))`.
  - `startSessionLinkDrag(snapshot, source: 'mouse' | 'touch'): string` — ends any previous drag first, resets every flag (inserted, drawer closed), sets the state with a fresh nonce (16 random hex chars) and returns it. For `'mouse'`: installs document capture `dragend` and `drop` listeners at once (each schedules `endSessionLinkDrag()` with `setTimeout 0`, so a zone `drop` handler and Vue microtasks run first), and installs document capture `pointerdown` and `mousemove` listeners in a `setTimeout(…, 0)` (each calls `endSessionLinkDrag()`).
  - `isCurrentLocalDrag(types: Iterable<string>): boolean` — true when a drag is active and `types` contains `sessionLinkNonceType(sessionLinkDrag.nonce)`.
  - `markSessionLinkInserted(): void` — no-op when no drag is active.
  - `endSessionLinkDrag(): void` — idempotent; removes the listeners; if this drag closed the drawer and nothing was inserted, calls the bridge `reopen()`; resets the state.
  - `registerSessionLinkDrawer(bridge: { closeIfNarrowOpen: () => boolean, reopen: () => void }): () => void` — same rule as the composer registry: a newer bridge replaces the older one, and the older unregister function does not remove the newer bridge.
  - `closeDrawerForSessionLinkDrag(): void` — calls `bridge.closeIfNarrowOpen()` and remembers a `true` result for `endSessionLinkDrag`. No-op without an active drag.
  - `noteRowTouchStart(): void`, `noteRowTouchEnd(now = Date.now()): void`, `isRowTouchOwned(now = Date.now()): boolean` — true while a row touch is in progress, and until 1000 ms after its end. The in-progress flag also ends on a document `touchend`/`touchcancel` with no touch left (when the events still reach `document`). A `pointerdown` with `pointerType === 'mouse'` clears the whole state at once, with no 1000 ms tail, so a mouse drag right after a touch is never blocked and a flag stuck by a detached target is cleared by the next mouse use.
  - `rowTouchState`: `reactive({ inProgress: false })`, kept in step by `noteRowTouchStart` / `noteRowTouchEnd` / the mouse clear; rows bind their `--touching` class to it.
  - `installPointerInputTracking(doc = document): () => void` — passive capture listeners: `pointerdown` records `pointerType` and time (and clears the row touch state at once when `pointerType === 'mouse'`); `touchend`/`touchcancel` with `event.touches.length === 0` call `noteRowTouchEnd()`; `recentPointerInput(now = Date.now()): 'touch' | 'mouse' | null` — `'touch'` for `pointerType === 'touch'`, `'mouse'` for `'mouse'`/`'pen'`, `null` when the last `pointerdown` is older than 1000 ms or none.

- [ ] **Step 1: Write the failing tests** (Node has `EventTarget`; per test set `globalThis.document = new EventTarget()` and restore it; `t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })`). Cases:
  - registry: register → `getSessionLinkComposer('a')` is the entry; unregister → `null`; a stale unregister keeps a newer entry.
  - `isComposerZoneVisible`: false for `null`, `isChatVisible()` false, `el()` null, `isConnected` false, no client rects; true otherwise.
  - `useSessionLinkTarget({ params: { sessionId: 'a' } })`: `targetSessionId.value` null before registering `'a'`, `'a'` after, null after unregistering.
  - mouse drag: `startSessionLinkDrag(snap, 'mouse')` returns a 16-hex nonce; `isCurrentLocalDrag([sessionLinkNonceType(nonce)])` true; `isCurrentLocalDrag([sessionLinkNonceType('other')])` false; dispatch `drop` on `document`, tick 0 → state cleared.
  - Review Focus 5: bridge `closeIfNarrowOpen` returns true; start a mouse drag, `closeDrawerForSessionLinkDrag()`, tick 0, dispatch only `pointerdown` → state cleared, `reopen` called once.
  - inserted: same, with `markSessionLinkInserted()` before the drop → `reopen` not called.
  - `markSessionLinkInserted()` with no drag, then a new drag that closes the drawer and ends without insertion → `reopen` called (no carried-over flag).
  - `endSessionLinkDrag()` twice → `reopen` once; a later `pointerdown` does nothing.
  - drawer bridge: register A, register B, call A's unregister → `closeDrawerForSessionLinkDrag()` during a drag calls B.
  - `isRowTouchOwned`: true after `noteRowTouchStart()` at any later time until `noteRowTouchEnd()`; true at +999 ms after the end; false at +1000 ms.
  - with `installPointerInputTracking(document)`: `noteRowTouchStart()`, then a document `touchend` with `touches: []` → ends the touch (false at +1000 ms); `noteRowTouchStart()`, then a `pointerdown` with `pointerType: 'mouse'` → `isRowTouchOwned()` false at once and `rowTouchState.inProgress` false; `noteRowTouchEnd()` then a mouse `pointerdown` 10 ms later → false at once (no tail); a document `touchend` with one touch left → still in progress.
  - `recentPointerInput`: after a `pointerdown` with `pointerType: 'touch'` → `'touch'`; `'pen'` → `'mouse'`; +1000 ms → `null`.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/composables/useSessionLinkDrag.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the exports in `useSessionLinkDrag.js`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** both files — `feat(sessions): add the shared session link drag store`.

---

### Task 4: Ghost, drag image and drag CSS

**Files:**
- Create: `frontend/src/utils/sessionLinkGhost.js`
- Create: `frontend/src/styles/session-link-drag.css`; import it in `frontend/src/main.js` after the other `./styles/*.css` imports.

**Interfaces:**
- Consumes: the `Snapshot` shape (Task 3).
- Produces:
  - `createSessionLinkGhost(snapshot, doc = document): HTMLElement` — `div.session-link-ghost` with `aria-hidden="true"`, children `span.session-link-ghost-dot` (inline background `snapshot.color`, or no inline color when null), `span.session-link-ghost-title` (`textContent = snapshot.title`), `span.session-link-ghost-project` (`textContent = snapshot.projectName`); inline width `Math.min(snapshot.width, 320)px`. Plain DOM only.
  - `setSessionLinkDragImage(dataTransfer, snapshot, offsetX: number, offsetY: number): void` — appends a ghost with inline `position: fixed; top: -1000px; left: -1000px`, calls `dataTransfer.setDragImage(ghost, offsetX, offsetY)`, removes it in the next `requestAnimationFrame`.
  - `showTouchGhost(snapshot): { move(x: number, y: number): void, remove(): void }` — ghost with `popover="manual"`, appended to `document.body`, `showPopover()`, inline `position: fixed; margin: 0; inset: auto; pointer-events: none`; `move` coalesces to one `requestAnimationFrame` and sets `left = x - width / 2`, `top = y - height - 24`.
- CSS (global, the classes are set from outside scoped components):
  - `.session-link-ghost` and children: raised row look (surface background, `var(--wa-shadow-l)`, border radius, one-line ellipsis title, smaller project line, the default neutral dot color when no inline color).
  - The freeze classes kept from Task 1. Default: `.session-link-scroll-frozen { overflow-y: hidden !important; }` and `html.session-link-drag-active, html.session-link-drag-active body { overscroll-behavior: none; }`.
  - `.session-item-wrapper--touching .session-item::part(base) { -webkit-user-drag: none; }` (wa-button exposes `part="base"`).
  - `.session-item-wrapper--lifted .session-item { transform: scale(1.02); box-shadow: var(--wa-shadow-m); }`; under `@media (prefers-reduced-motion: reduce)`: no transform, a background change only.

- [ ] **Step 1: Implement** the three functions and the CSS. No unit test: this is DOM glue, checked manually in Task 11.
- [ ] **Step 2: Verify** `cd /home/twidi/dev/twicc-poc/frontend && npm run build`. Expected: exit 0.
- [ ] **Step 3: Commit** `frontend/src/utils/sessionLinkGhost.js`, `frontend/src/styles/session-link-drag.css`, `frontend/src/main.js` — `feat(sessions): add the session link drag ghost`.

---

### Task 5: Touch drag controller

**Files:**
- Create: `frontend/src/utils/sessionLinkTouch.js`
- Test: `frontend/src/utils/sessionLinkTouch.test.js`

**Interfaces:**
- Consumes (through an injectable `deps` object whose defaults are the real functions): Task 3 (`sessionLinkDrag`, `startSessionLinkDrag`, `endSessionLinkDrag`, `markSessionLinkInserted`, `closeDrawerForSessionLinkDrag`), Task 4 (`showTouchGhost`).
- Produces: `createSessionLinkTouchDrag({ target: EventTarget, armPoint: { x, y }, snapshot, scrollEl: HTMLElement, getZone: () => { sessionId: string, rect: DOMRect, insert: Function } | null, onArmedRelease: () => void, onEnd: () => void, deps? }): { phase: () => 'armed' | 'dragging' | 'ended', cancel: () => void }`.
  - Creation (armed): adds `session-link-scroll-frozen` to `scrollEl`, `session-link-drag-active` to `document.documentElement`; listens on `target` to `touchmove` (`{ passive: true }`), `touchend`, `touchcancel`; listens on `document` to `touchstart` (`{ passive: true, capture: true }`) — any new touch cancels.
  - Armed: `touchend` → `onArmedRelease()`, then end. `touchmove` with more than one touch → cancel. A move > 10 px from `armPoint` → dragging: `startSessionLinkDrag(snapshot, 'touch')`, `closeDrawerForSessionLinkDrag()`, show the ghost at the point; `travel = 0` and the previous point is this transition point.
  - Dragging, each `touchmove`: ghost `move`; `zone = getZone()`; no zone → cancel; point inside `zone.rect` → `travel += distance(previous, point)`, else `travel = 0`; previous = point; `sessionLinkDrag.touchDropSessionId = travel >= 10 ? zone.sessionId : null`; more than one touch → cancel.
  - Dragging, `touchend`: `zone = getZone()` again; if `touchDropSessionId` is set, `zone?.sessionId === touchDropSessionId` and the release point (`changedTouches[0]`) is inside `zone.rect` → `inserted = await zone.insert(snapshot.payload.markdown, { linkedSessionId: snapshot.id, input: 'touch' })`, `markSessionLinkInserted()` when true, then end; otherwise cancel. `touchcancel` → cancel.
  - End/cancel (idempotent): remove all listeners, remove both classes, remove the ghost, `sessionLinkDrag.touchDropSessionId = null`, `endSessionLinkDrag()` (it reopens the drawer when the drag closed it and nothing was inserted), `onEnd()`.

- [ ] **Step 1: Write the failing tests** with `target = new EventTarget()`, `globalThis.document = Object.assign(new EventTarget(), { documentElement: { classList: fakeClassList() } })`, `scrollEl = { classList: fakeClassList() }`, events `Object.assign(new Event('touchmove'), { touches: [{ clientX, clientY }], changedTouches: [...] })`, stub deps recording calls. Cases:
  - armed + `touchend` → `onArmedRelease` once, `onEnd` once, both classes removed, `startSessionLinkDrag` not called.
  - armed + move of 10 px → still armed; 11 px → dragging, drawer close requested, ghost shown.
  - drag start inside the zone, then release without another move → cancel, `insert` not called (the transition move does not count).
  - 9 px of travel inside the zone → `touchDropSessionId` null; release → cancel, `endSessionLinkDrag` called.
  - 10 px inside → `touchDropSessionId === 'target'`; release inside → `insert('MD', { linkedSessionId: 'src', input: 'touch' })`, `markSessionLinkInserted` called.
  - 8 px inside, 1 move outside, 8 px inside → still null.
  - ready, then `getZone()` returns a zone for another session at release → cancel, no insert.
  - `getZone()` null during dragging → cancel.
  - a `touchstart` dispatched on `document` while armed, and while dragging → cancel.
  - two touches in a `touchmove` → cancel; `touchcancel` → cancel.
  - after end, events on `target` and `document` do nothing.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/sessionLinkTouch.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** `createSessionLinkTouchDrag`.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** both files — `feat(sessions): add the touch session link drag controller`.

---

### Task 6: Composer side: insertion, registry, drop zone, i18n

**Files:**
- Modify: `frontend/src/components/message/MessageInput.vue` (`insertTextAtCursor` ~1890; `wa-textarea` `@focus` ~2159)
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue` (props ~65; drag handlers ~1786-1871; `handleForwardedDrop` ~2295; root div ~2385; overlay ~2814)
- Modify: `frontend/src/views/SessionView.vue` (main `SessionItemsList` ~2509; `pendingDropData` watch ~1666)
- Modify: `frontend/src/i18n/locales/{en,de,es,fr,pt-BR}.json`
- Test: `frontend/src/components/session/detail/SessionItemsList.sessionLink.test.js`, `frontend/src/components/message/MessageInput.insertText.test.js`

**Interfaces:**
- Consumes: Task 2 (`classifyDragTypes`, `sessionLinkMimeType`, `decodeSessionLinkPayload`, `padInsertion`), Task 3 (`sessionLinkDrag`, `registerSessionLinkComposer`, `isCurrentLocalDrag`, `markSessionLinkInserted`, `endSessionLinkDrag`).
- Produces:
  - `MessageInput.insertTextAtCursor(text, { focus = true, pad = false } = {})`. With `pad`: inserted text = `padInsertion(before, text, after)`; on the collapsed path `before` = the whole draft, `after` = `''`; when `caretSeen` is false the position is the end of the draft. `caretSeen` (plain `let`, false) becomes true in a new `onTextareaFocus()` that also calls `adjustTextareaHeight()`. `caretSeen` and `padInsertion` are read only inside the `pad` branches, so `MessageInput.selectionSend.test.js` (which evaluates `insertTextAtCursor` without them) keeps passing. Without `pad`: unchanged.
  - `SessionItemsList` prop `chatVisible: Boolean` (default `false`).
  - `SessionItemsList.insertSessionLink(markdown, { linkedSessionId, input }): Promise<boolean>` — false when `linkedSessionId === props.sessionId` or no `messageInputRef.value`; `focus = input === 'touch' ? false : input === 'mouse' ? true : !settingsStore.isTouchDevice`; `openBlock === 'message-input'` → `insertTextAtCursor(markdown, { focus, pad: true })`; `openBlock === 'none'` → `openBlock.value = 'message-input'`, `messageInputRef.value.expand()`, `await nextTick()`, same call; other `openBlock` → `insertTextAtCursor(markdown, { focus: false, pad: true })`. Returns true when inserted.
  - `handleForwardedDrop({ files, text, sessionLink, input })` — with `sessionLink`: `insertSessionLink(sessionLink.markdown, { linkedSessionId: sessionLink.sessionId, input: input ?? null })`, `markSessionLinkInserted()` on true; without: unchanged.
  - The overlay label logic is a plain function `sessionLinkOverlayLabel(): string` and the types seen at enter are kept in `let dragEnterTypes = []`; both sit inside the `// Drag and Drop Handlers` section, so the test slice includes them.
  - `insertSessionLink` and `handleForwardedDrop` sit between the comment markers `// === Session link insertion ===` and `// === End session link insertion ===`.
  - i18n keys `session.drop.sessionLink`, `session.drop.sessionLinkGeneric`, `session.list.insertLink` (the last one used in Task 10).

- [ ] **Step 1: Write the failing tests.**
  - `SessionItemsList.sessionLink.test.js`, source-slice pattern of `SessionItemsList.events.test.js`: read `SessionItemsList.vue`, slice from `// Drag and Drop Handlers` to `async function processDroppedFile(`, and the block between the session-link markers; evaluate with `new Function`, declaring `let dragCounter = 0` and `const dragOverType = ref(null)` in the body before the slices (pattern: `let scrollToLineNumGeneration = 0` in `SessionItemsList.events.test.js`) and returning the handlers plus a `getCounter: () => dragCounter` getter, with stubs: `ref`/`nextTick` from `vue`, `document` as an `EventTarget`, `globalThis.location = { origin: 'http://t' }`, `props`, `messageInputRef`, `openBlock`, `settingsStore`, `sessionLinkDrag` and `isCurrentLocalDrag` (real, from Task 3), the Task 2 helpers (real), `markSessionLinkInserted`/`endSessionLinkDrag` spies, `processDroppedFile` stub. Events carry `dataTransfer: { types, getData(type), dropEffect }` and a `preventDefault` spy. Cases:
    - Review Focus 1: a current local self-link drag (state session id = `props.sessionId`, types include its nonce): enter, over (`dropEffect === 'none'`, `preventDefault` not called), leave, drop → `dragOverType` null, nothing inserted; then a `['text/plain']` enter → `dragOverType === 'text'`.
    - own type, other session: enter → `'session'`; drop → `insertTextAtCursor(payload.markdown, { focus: true, pad: true })`, `markSessionLinkInserted` and `endSessionLinkDrag` called.
    - Review Focus 5: stale state (state session id = `props.sessionId`, nonce `'aa'`) and a drag whose types hold the own type with nonce `'bb'` → enter gives `'session'` (not refused), the overlay label key is `session.drop.sessionLinkGeneric`; drop with a payload for session `'other'` inserts it.
    - `['Files', own]` → `'files'`; foreign type + `text/plain` → `'text'`, drop inserts `getData('text/plain')` as today.
    - own type, payload `getData` returns `'{'` → nothing inserted, `dragCounter` and `dragOverType` reset.
    - enter counted, then `messageInputRef.value = null`, then leave → `dragCounter === 0`, `dragOverType` null.
    - `insertSessionLink`: `openBlock 'none'` → `'message-input'`, `expand` called, insertion after `nextTick`; `openBlock 'terminal'` → `{ focus: false, pad: true }`, `openBlock` unchanged; `input: 'touch'` → `focus: false`; `input: null` + `isTouchDevice` true → `focus: false`; `input: null` + false → `focus: true`.
    - `handleForwardedDrop({ files: [], text: 'url', sessionLink: { sessionId: props.sessionId, markdown: 'MD' } })` → nothing inserted.
    - the template source contains `class="drop-overlay"` together with `aria-hidden="true"` (regex on the SFC source).
    - touch overlay: `sessionLinkDrag.touchDropSessionId = props.sessionId`, `sessionLinkDrag.session = { id: 'other', title: 'T' }` → `sessionLinkOverlayLabel()` calls `t('session.drop.sessionLink', { title: 'T' })` (stub `t` records its arguments).
  - `MessageInput.insertText.test.js`, `babelParse` selection technique of `MessageInput.selectionSend.test.js`, selecting `insertTextAtCursor`, `onTextareaFocus` and the `caretSeen` declaration; stubs: `messageText` ref, `collapsed` ref, `textareaRef` with an inner `{ selectionStart, selectionEnd, setSelectionRange() }`, `updateTextareaContent` writing `messageText`, `nextTick`, `adjustTextareaHeight`, `padInsertion` (real). Cases: draft `'abc'`, `selectionStart 0`, `pad`, no focus yet → `'abc L'`; after `onTextareaFocus()` with caret 1 → `'a L bc'`; collapsed + `pad` → `'abc L'`; no `pad`, caret 0 → `'Labc'` (unchanged); selection 1..2 + `pad` → `'a L c'`.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/detail/SessionItemsList.sessionLink.test.js src/components/message/MessageInput.insertText.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement in `SessionItemsList.vue`:**
  - the `chatVisible` prop; `ref="rootRef"` on the root div;
  - `classifyDrag(event)` = `classifyDragTypes(types, { origin: location.origin, selfLinked: isCurrentLocalDrag(types) && sessionLinkDrag.session.id === props.sessionId, hasComposer: !!messageInputRef.value })`;
  - `onDragEnter`/`onDragOver`: return at once on `null` (`onDragOver` sets `dropEffect = 'none'` first when the own type is present); `onDragLeave`: decrement while `dragCounter > 0` whatever the classification, reset at 0; `onDrop`: always reset `dragCounter`, `dragOverType` and the `dragend` listener first, then classify; for `'session'` decode the own-type payload, and on a valid payload `insertSessionLink(payload.markdown, { linkedSessionId: payload.sessionId, input: 'mouse' })` + `markSessionLinkInserted()` on true; then `endSessionLinkDrag()`; on `null` insert nothing;
  - overlay: shown when `dragOverType` is set or `sessionLinkDrag.touchDropSessionId === props.sessionId`; `aria-hidden="true"`; session variant icon `link`; label `t('session.drop.sessionLink', { title })` when the overlay comes from the touch drop state, or from a mouse drag with `isCurrentLocalDrag(<types seen at enter>)`; otherwise `t('session.drop.sessionLinkGeneric')`; root keeps the `drag-over` class in both cases;
  - registry: when `!props.parentSessionId`, `watch([messageInputRef, () => props.sessionId], …, { immediate: true })` registers `{ el: () => rootRef.value, isChatVisible: () => props.chatVisible, insert: insertSessionLink }` while the ref is set and unregisters otherwise and in `onBeforeUnmount`;
  - move `handleForwardedDrop` between the session-link markers, together with `insertSessionLink`.
- [ ] **Step 4: Implement in `MessageInput.vue`:** the `pad` option, `caretSeen`, `onTextareaFocus()`; the `wa-textarea` `@focus` calls `onTextareaFocus`.
- [ ] **Step 5: Implement in `SessionView.vue`:** `:chat-visible="isActive && layout.centerVisible.value && centerActiveTab === 'main' && !layout.openOverlayEdge.value"` on the main `SessionItemsList`; in the `pendingDropData` watch, when `data.sessionLink` is set and `layout.maximizedRegion.value && !layout.centerVisible.value`, call `layout.restoreMaximized()` before forwarding. The existing `switchToTab('main')` closes an overlay dock (the overlay follows the route).
- [ ] **Step 6: Add the i18n keys** next to `session.drop.text` and `session.list.share` (French: U+00A0 inside the guillemets):

  | key | en | fr | de | es | pt-BR |
  |---|---|---|---|---|---|
  | `session.drop.sessionLink` | `Drop to insert a link to “{title}”` | `Déposez pour insérer un lien vers « {title} »` | `Ablegen, um einen Link zu „{title}“ einzufügen` | `Suelta para insertar un enlace a «{title}»` | `Solte para inserir um link para “{title}”` |
  | `session.drop.sessionLinkGeneric` | `Drop to insert a session link` | `Déposez pour insérer un lien de session` | `Ablegen, um einen Sitzungslink einzufügen` | `Suelta para insertar un enlace de sesión` | `Solte para inserir um link de sessão` |
  | `session.list.insertLink` | `Insert link in message` | `Insérer un lien dans le message` | `Link in Nachricht einfügen` | `Insertar enlace en el mensaje` | `Inserir link na mensagem` |

- [ ] **Step 7: Run** the two new tests, then `cd /home/twidi/dev/twicc-poc/frontend && npm test`. Expected: PASS, `MessageInput.selectionSend.test.js` and the locale tests included.
- [ ] **Step 8: Commit** the modified and new files, listed one by one — `feat(sessions): accept session links in the chat drop zone`.

---

### Task 7: Desktop hand-offs and the drawer bridge

**Files:**
- Modify: `frontend/src/composables/useDragHover.js` (`onDrop`)
- Modify: `frontend/src/views/ProjectView.vue` (`handleDropOnSession` ~1078; near `syncSidebarState` ~1669; `onMounted`/`onBeforeUnmount`)
- Test: `frontend/src/composables/useDragHover.sessionLink.test.js`

**Interfaces:**
- Consumes: Task 2 (`classifySessionLinkTypes`, `sessionLinkMimeType`, `decodeSessionLinkPayload`), Task 3 (`registerSessionLinkDrawer`, `installPointerInputTracking`).
- Produces:
  - `useDragHover` `onDropData` receives `{ files, text, sessionLink, input: 'mouse' }`; `sessionLink` = decoded payload of the own type (`location.origin`), or `null`.
  - `ProjectView.handleDropOnSession({ session, files, text, sessionLink, input })` → `pendingDropData = { sessionId: session.id, files, text, sessionLink, input }`.
  - `ProjectView.closeDrawerIfNarrowOpen(): boolean` — `false` unless `isMobile()` and `#sidebar-toggle-state` is checked; then unchecks it, `syncSidebarState()`, returns `true`.
  - `ProjectView.reopenDrawerIfNarrow(): void` — if `isMobile()`: checks it, `syncSidebarState()`.
  - On mount, `ProjectView` calls `registerSessionLinkDrawer({ closeIfNarrowOpen: closeDrawerIfNarrowOpen, reopen: reopenDrawerIfNarrow })` and `installPointerInputTracking()`; it calls both returned cleanups on unmount.

- [ ] **Step 1: Write the failing test** `useDragHover.sessionLink.test.js`: import `useDragHover` directly (it imports `../i18n/index.js`, which other Node tests already import). Stubs: `globalThis.document = { createElement: () => ({ style: { setProperty() {} }, remove() {} }), body: { appendChild() {} }, addEventListener() {}, removeEventListener() {} }`, `globalThis.requestAnimationFrame = () => 0`, `globalThis.cancelAnimationFrame = () => {}`, `globalThis.location = { origin: 'http://t' }`; `t.mock.timers` for `setTimeout`. Cases: enter with types `[own, 'text/plain']`, tick 1000 ms, drop with `getData(own) = encodeSessionLinkPayload(p)` and `getData('text/plain') = 'url'` → `onDropData` called once with `{ files: [], text: 'url', sessionLink: p, input: 'mouse' }`; a `['text/plain']` drag → `sessionLink: null`; a drop before 1000 ms → `onDropData` not called.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/composables/useDragHover.sessionLink.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement** the `useDragHover.onDrop` change and the `ProjectView` changes.
- [ ] **Step 4: Run** the test, then `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** the three files — `feat(sessions): carry session links through drop hand-offs`.

---

### Task 8: Sidebar row: desktop drag

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue` (props ~49; `useDragHover` call ~155; template wrapper ~605)
- Modify: `frontend/src/components/session/list/SessionList.vue` (`SessionListItem` in the `VirtualScroller` ~990)
- Modify: `frontend/src/components/session/list/SessionListItem.touch.test.js`, `frontend/src/components/session/list/SessionListItem.rail.test.js` (harness defaults)
- Test: `frontend/src/components/session/list/SessionListItem.sessionLink.test.js`

**Interfaces:**
- Consumes: Task 2 (`buildSessionLinkPayload`, `sessionLinkMimeType`, `sessionLinkNonceType`, `encodeSessionLinkPayload`), Task 3 (`sessionLinkDrag`, `useSessionLinkTarget`, `startSessionLinkDrag`, `closeDrawerForSessionLinkDrag`, `isRowTouchOwned`), Task 4 (`setSessionLinkDragImage`).
- Produces:
  - `SessionListItem` prop `linkSource: Boolean` (default `false`); `SessionList` passes `link-source` on its virtual-scroller rows only.
  - Row internals used by Tasks 9 and 10: `isLinkable` (computed: `!session.draft && !session.ephemeral`), `targetSessionId` / `isTargetChatVisible` (from `useSessionLinkTarget()`), `wrapperRef` (ref on the wrapper div), `buildSnapshot(): Snapshot`, `handleDragStart(event)`.

- [ ] **Step 1: Update the harnesses.** In `SessionListItem.touch.test.js` and in both setups of `SessionListItem.rail.test.js`, after the auto-stubs, set these defaults so every existing test stays green: `ref: v => ({ value: v })`, `useSessionLinkTarget: () => ({ targetSessionId: { value: null }, isTargetChatVisible: () => false })`, `sessionLinkDrag: { session: null, touchDropSessionId: null }`, `isRowTouchOwned: () => false`, `noteRowTouchStart() {}`, `noteRowTouchEnd() {}`. Let `fixture(t, overrides, contextOverrides = {})` in the touch harness merge `contextOverrides` last. Run `cd /home/twidi/dev/twicc-poc/frontend && node --test 'src/components/session/list/*.test.js'`. Expected: PASS (no behaviour change yet).
- [ ] **Step 2: Write the failing tests** in `SessionListItem.sessionLink.test.js` (same harness as the touch test, copied into the file; context adds `location: { origin: 'http://t' }`, `useDataStore: () => ({ getProject: () => null, getProjectDisplayName: () => 'P' })`, a spy `setSessionLinkDragImage`, a spy `startSessionLinkDrag` returning `'aa'`, a spy `closeDrawerForSessionLinkDrag`, and real Task 2 helpers). The drag event: `{ dataTransfer: { setData: spy, clearData: spy, setDragImage() {} }, preventDefault: spy, clientX, clientY, currentTarget: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 280 }) } }`. Cases:
  - `linkSource: false` → no `setData`, no `preventDefault`.
  - `isRowTouchOwned` true → `preventDefault` called, no `setData`; `event.sourceCapabilities = { firesTouchEvents: true }` → same.
  - multi-select active → no `setData`, no `preventDefault`; draft session → no `setData`.
  - normal → `setData(sessionLinkMimeType('http://t'), <encoded payload>)` and `setData(sessionLinkNonceType('aa'), '1')`; `startSessionLinkDrag(snapshot, 'mouse')` called before the nonce `setData`; `clearData` never called; with target `'open'` ≠ this session, `closeDrawerForSessionLinkDrag` called after a `setTimeout 0`; with no target, not called.
  - the `shouldActivate` passed to `useDragHover` returns false when `sessionLinkDrag.session = { id: 'first' }` and the row is not active.
- [ ] **Step 3: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/list/SessionListItem.sessionLink.test.js`. Expected: FAIL.
- [ ] **Step 4: Implement.** `buildSnapshot()` = `{ id, projectId: session.project_id, title: getSessionDisplayName(session), projectName: store.getProjectDisplayName(session.project_id), color: projectDotColor.value, width: wrapperRef.value?.getBoundingClientRect().width ?? 300, payload: buildSessionLinkPayload(session, router) }`. `handleDragStart(event)` on the wrapper (`@dragstart`), in this order: not `linkSource` → return; touch-owned (`isRowTouchOwned()` or `event.sourceCapabilities?.firesTouchEvents === true`) → `preventDefault()`, return; multi-select active or not linkable → return; else `nonce = startSessionLinkDrag(snapshot, 'mouse')`, `setData(sessionLinkMimeType(location.origin), encodeSessionLinkPayload(snapshot.payload))`, `setData(sessionLinkNonceType(nonce), '1')`, `setSessionLinkDragImage(event.dataTransfer, snapshot, event.clientX - rect.left, event.clientY - rect.top)` (`rect` of `event.currentTarget`), and when `targetSessionId.value && targetSessionId.value !== session.id`: `setTimeout(closeDrawerForSessionLinkDrag, 0)`. `useDragHover` `shouldActivate`: add `&& sessionLinkDrag.session?.id !== props.session.id`. `SessionList.vue`: `link-source` on the virtual-scroller `SessionListItem`.
- [ ] **Step 5: Run** the new test, then `npm test`. Expected: PASS.
- [ ] **Step 6: Commit** the modified and new files, listed one by one — `feat(sessions): drag sidebar sessions into the chat on desktop`.

---

### Task 9: Sidebar row: touch gesture

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue` (touch handlers ~402-466; template wrapper ~605)
- Test: `frontend/src/components/session/list/SessionListItem.sessionLink.test.js` (extend)
- Modify: `frontend/src/components/session/list/SessionListItem.touch.test.js` (the wiring test expects `touchcancel` → `'handleTouchCancel'`; change it to `'onTouchCancelEvent'`)

**Interfaces:**
- Consumes: Task 3 (`getSessionLinkComposer`, `isComposerZoneVisible`, `noteRowTouchStart`, `noteRowTouchEnd`, `rowTouchState`), Task 5 (`createSessionLinkTouchDrag`), Task 8 (`isLinkable`, `targetSessionId`, `isTargetChatVisible`, `wrapperRef`, `buildSnapshot`).
- Produces (row internals): `linkGesture` (`null` or `{ controller, sessionId }`; set at arming, cleared at the next `touchstart`), `liftedSessionId` ref (`null` or a session id), `touching` ref (Boolean).

- [ ] **Step 1: Write the failing tests** (extend `SessionListItem.sessionLink.test.js`; context adds a spy `createSessionLinkTouchDrag` returning `{ phase: () => 'armed', cancel: spy }` and capturing its options; `wrapperRef` resolves to `{ closest: () => scroller, getBoundingClientRect: () => ({ width: 280 }) }` with `scroller = { scrollTop: 0 }`; `useSessionLinkTarget` returns target `'open'`, visible; touch events gain `composedPath: () => [innerTarget]`). Cases:
  - no link target, or `linkSource: false` → multi-select at 500 ms, no controller (regression guard).
  - link target → at 500 ms the controller is created once with `armPoint` = the touch point, `target === innerTarget`, `scrollEl === scroller`; `liftedSessionId.value === 'first'`; `store.active === false`.
  - armed release, real order: call the captured `onArmedRelease()` then `onEnd()`, then `row.handleTouchEnd(touch())` → selection `['first']`, `preventDefault` called, the next `row.handleClick(click())` is suppressed, no `select` event.
  - Review Focus 3: `scroller.scrollTop` set to 5 between `touchstart` and 500 ms → no controller, multi-select at 500 ms.
  - `wrapperRef.value.closest` returns null → no controller, multi-select at 500 ms.
  - target equals this session (`session.id === 'open'`), or `isTargetChatVisible()` false → no controller.
  - multi-select active → no controller; a tap toggles as today.
  - Review Focus 2: with the controller created, run every collected `watchers` callback and every `cleanup` callback → controller `cancel` not called; then set `props.session = { id: 'second' }` → `liftedSessionId.value === 'first'` (the class binding `liftedSessionId === session.id` is false for `'second'`); `onArmedRelease()` selects `['first']` (the snapshot id), not `'second'`.
  - `handleContextMenu({ pointerType: 'touch', preventDefault: spy })` while `linkGesture` is set → `preventDefault` called.
  - `noteRowTouchStart` called at `touchstart` and `touching.value === true` after `handleTouchStart` (the initial reset inside `handleTouchStart` must not undo it); `noteRowTouchEnd` called once at the `touchend` event and once at the `touchcancel` event (`onTouchCancelEvent`).
  - a move > 10 px before 500 ms (scroll path) → `noteRowTouchEnd` not called, `touching.value` still true.
  - while armed, run every `watchers` and `cleanup` callback → `noteRowTouchEnd` not called.
  - `handleContextMenu({ pointerType: 'mouse', preventDefault: spy })` after a finished gesture (`linkGesture` still set) → `preventDefault` not called.
  - `handleDragStart` while `linkGesture` is set with `phase()` `'armed'` or `'dragging'`, and `isRowTouchOwned` false → `preventDefault` called; with `phase()` `'ended'` (finished gesture) and a mouse `dragstart` → `preventDefault` not called, `setData` called.
  - armed release in real order (`onArmedRelease()`, `onEnd()`, then `handleTouchEnd`) → `noteRowTouchEnd` called once, `touching.value === false`.
  - the wrapper `--touching` class binding is `touching && rowTouchState.inProgress`: with `touching.value === true` and `rowTouchState.inProgress` set to false (document fallback or mouse clear), the binding is false.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/list/SessionListItem.sessionLink.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `handleTouchStart`: keep its existing first line (the internal reset `handleTouchCancel()`), then `linkGesture = null`; `noteRowTouchStart()`; `touching.value = true`; keep `touchTarget = event.composedPath?.()[0] ?? event.target`; `scrollEl = wrapperRef.value?.closest('.virtual-scroller') ?? null` (light DOM, never from `touchTarget`); record `scrollEl?.scrollTop`.
  - 500 ms timer: `canArm` = `linkSource && !selectionStore.active && isLinkable && targetSessionId.value && targetSessionId.value !== session.id && isTargetChatVisible() && scrollEl && scrollEl.scrollTop === <recorded>`. If `canArm`: `suppressTouchClick = true`; `const snapshot = buildSnapshot()`; `liftedSessionId.value = snapshot.id`; `try { navigator.vibrate?.(10) } catch {}`; `linkGesture = { sessionId: snapshot.id, controller: createSessionLinkTouchDrag({ target: touchTarget, armPoint, snapshot, scrollEl, getZone, onArmedRelease: () => { selectionStore.enter(snapshot.id); selectionStore.setSelection([snapshot.id], { anchor: snapshot.id }) }, onEnd: () => { liftedSessionId.value = null } }) }` (`onEnd` never changes `touching`; the row's real `touchend`/`touchcancel` does). `getZone()` reads `entry = getSessionLinkComposer(targetSessionId.value)` and returns `{ sessionId: targetSessionId.value, rect: entry.el().getBoundingClientRect(), insert: entry.insert }` when `isComposerZoneVisible(entry)`, else `null`. Otherwise: the existing path, unchanged.
  - `handleTouchCancel` stays the internal reset (timer, `touchOrigin`); it never calls `noteRowTouchEnd()`, never changes `touching`, never touches `linkGesture`. The `session.id` watch, `onBeforeUnmount` and the scroll-slop path keep calling it.
  - New `onTouchCancelEvent()`, bound to `@touchcancel` in place of `handleTouchCancel`: `if (touching.value) { noteRowTouchEnd(); touching.value = false }`, then `handleTouchCancel()`.
  - `handleTouchMove`: return when `linkGesture` is set. `handleTouchEnd`: `if (touching.value) { noteRowTouchEnd(); touching.value = false }`; when `linkGesture` is set: `preventDefault()`, `suppressTouchClick = true`, `touchOrigin = null`, return.
  - `handleDragStart` (Task 8): the touch-owned test also includes a live gesture: `linkGesture && linkGesture.controller.phase() !== 'ended'`.
  - `handleContextMenu`: also `preventDefault()` when `linkGesture` is set and `event.pointerType !== 'mouse'`.
  - Template: wrapper classes `session-item-wrapper--lifted` when `liftedSessionId === session.id`, `session-item-wrapper--touching` when `touching && rowTouchState.inProgress`.
- [ ] **Step 4: Run** the test file, then `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `frontend/src/components/session/list/SessionListItem.vue`, `frontend/src/components/session/list/SessionListItem.sessionLink.test.js`, `frontend/src/components/session/list/SessionListItem.touch.test.js` — `feat(sessions): drag sidebar sessions into the chat by touch`.

---

### Task 10: Row menu entry

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue` (`handleMenuSelect` ~530; menu template ~827; emits ~123)
- Modify: `frontend/src/components/session/list/SessionList.vue` (emits ~690; `SessionListItem` listeners ~1006)
- Modify: `frontend/src/views/ProjectView.vue` (`SessionList` listeners ~2058; new `handleInsertSessionLink`)
- Test: `frontend/src/components/session/list/SessionListItem.sessionLink.test.js` (extend)

**Interfaces:**
- Consumes: Task 2 (`buildSessionLinkPayload`), Task 3 (`useSessionLinkTarget`, `recentPointerInput`), Task 7 (`closeDrawerIfNarrowOpen`), Task 8 (`isLinkable`, `targetSessionId`).
- Produces: `SessionListItem` emits `insert-link` with `{ session, input: 'touch' | 'mouse' | null }`; `SessionList` re-emits `insert-link`; `ProjectView.handleInsertSessionLink({ session, input })` sets `pendingDropData = { sessionId: targetSessionId.value, files: [], text: null, sessionLink: buildSessionLinkPayload(session, router), input }` and calls `closeDrawerIfNarrowOpen()` (no reopen). It returns at once, without touching `pendingDropData` or the drawer, when `targetSessionId.value` is null.

- [ ] **Step 1: Write the failing tests** (extend the same file). Cases:
  - the template source has, right after the "Share" item, `<wa-dropdown-item v-if="showInsertLinkEntry" value="insert-link">` with `<wa-icon slot="icon" name="link">` and `$t('session.list.insertLink')`.
  - `showInsertLinkEntry`: false with `linkSource: false`, a draft, an ephemeral session, no target, or target = this session; true otherwise, including with multi-select active.
  - the target disappears before `wa-after-hide` (`targetSessionId.value = null`) → no emit.
  - `handleMenuSelect` with `value: 'insert-link'` and `event.target` = a fake dropdown `EventTarget`: no emit before `wa-after-hide`; dispatching `wa-after-hide` on the dropdown → one `insert-link` emit with `{ session, input: <recentPointerInput stub value> }`; a `wa-after-hide` dispatched from a child (`target !== dropdown`) does not emit; with no `wa-after-hide`, the 600 ms fallback emits once; never twice.
- [ ] **Step 2: Run** `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/list/SessionListItem.sessionLink.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement.** `showInsertLinkEntry` = `linkSource && isLinkable && !!targetSessionId.value && targetSessionId.value !== session.id`. Menu item after "Share". `handleMenuSelect`: put the `insert-link` branch first (before `edit-project`), so the `rename` → `detach-tree` sequence checked by `SessionListItem.tree.test.js` stays intact; capture `input = recentPointerInput()` at selection time; emit once on the first of `wa-after-hide` from the dropdown itself or the 600 ms fallback, and only if `targetSessionId.value` is still set and is not this session. `SessionList.vue`: `insert-link` in `defineEmits`, forwarded. `ProjectView.vue`: `@insert-link="handleInsertSessionLink"`, `targetSessionId` from `useSessionLinkTarget()` called in its setup.
- [ ] **Step 4: Run** the test file, then `npm test`. Expected: PASS (`SessionListItem.tree.test.js` included).
- [ ] **Step 5: Commit** `SessionListItem.vue`, `SessionList.vue`, `ProjectView.vue`, `SessionListItem.sessionLink.test.js` (full paths) — `feat(sessions): add the insert session link menu entry`.

---

### Task 11: Full verification and manual acceptance

**Files:** none.

- [ ] **Step 1: Run** `cd /home/twidi/dev/twicc-poc/frontend && npm test` and `cd /home/twidi/dev/twicc-poc/frontend && npm run build`. Expected: all tests pass; build exits 0.
- [ ] **Step 2: Hand the checks to the owner and wait.** The dev servers are the owner's; no restart by the agent. Give, in this order, Firefox first:
  - the spec's "Acceptance checks" (desktop, mobile, menu entry, link), minus the cross-window lines if Task 1 dropped them;
  - desktop, Firefox and Chromium: during a native drag, the drag state is not cleared early (the overlay label keeps the title over the chat); in a narrow window, the drawer does not reopen during the drag; drag a row, make it leave the list (archive it from another window) before dropping outside → the drawer reopens on the next mouse move;
  - menu entry chosen by touch on a touch screen with hover (if available): the composer does not take the focus.
  Report each failure as it is, with the check that failed.

---

## Self-review notes

- Spec coverage: link and escaping (Task 2); insertion and accordion (Task 6); desktop drop zone, overlay, refusals, other origin, stale state (Tasks 2, 3, 6); spring-load and hand-offs (Task 7); narrow pointer drawer (Tasks 3, 7, 8); touch gesture, freeze, ghost, drop readiness, cancel causes, context menu (Tasks 4, 5, 9); menu entry, focus by input (Tasks 3, 6, 10); multi-select (Tasks 8, 9); i18n (Task 6); accessibility (Tasks 4, 6, 10); feasibility gate (Task 1); acceptance (Task 11).
- Cross-window fallback: if the browser does not expose the private types to another window during the hover, `classifyDragTypes` sees only `text/plain` there and returns `'text'`: the drop is today's text drop, which is the spec's fallback. No code branch depends on the Task 1 result.
