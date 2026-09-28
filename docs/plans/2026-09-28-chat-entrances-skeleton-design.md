# Live chat entrances + chat skeleton — design (visual refresh, step 5a)

## 1. Context

Step 5 of the "Signature" visual refresh (entrances + skeletons) is split in three sub-steps:
**5a (this document)**: live chat entrances and the chat skeleton; 5b: overlay entrances and
exits (dialogs, menus, popovers, palette, switcher, pickers, toasts, tooltip delay); 5c:
session-list cascade, tab-panel crossfade, light/dark reveal. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding user decisions: **Firefox
parity**, **reduced motion is reduced, not none**), §6d–§6f (step 4 lessons), §8.7 and §9
"Step 5" (the target).

5a uses the step 4a tokens of `frontend/src/styles/motion.css`: `--motion-dur-2` (200ms),
`--motion-dur-3` (380ms), `--motion-ease`, `--motion-ease-out`, `--motion-amount` (1; 0
under reduced motion), and the global `@keyframes motion-status-pulse`.

Mock reference (`fx-enter` block of `mock.css`, roadmap §3):

```css
@keyframes msg-in { from { opacity: 0; transform: translateY(.75rem) scale(.985); filter: blur(.125rem); } }
@keyframes msg-in-user { from { opacity: 0; transform: translateX(1rem) scale(.985); } }
.msg.is-new.assistant, .working.is-new { animation: msg-in var(--dur-3) var(--ease-out) both; animation-delay: calc(var(--i, 0) * 70ms); }
.msg.is-new.user { animation: msg-in-user var(--dur-3) var(--ease-out) both; animation-delay: calc(var(--i, 0) * 70ms); }
.sk { background: linear-gradient(90deg, lowered 0%, color-mix(in oklab, lowered, text 6%) 50%, lowered 100%);
      background-size: 200% 100%; animation: shimmer-bg 1.4s linear infinite; }
@keyframes shimmer-bg { to { background-position: -200% 0; } }
```

Mock skeleton: four shapes, gap 1.5rem — user 55% × 4.5rem (right), assistant 85% × 12rem,
user 40% × 3.5rem (right), assistant 75% × 8rem. In the mock, each message is one card; in
TwiCC, one assistant turn is **one card drawn over several scroller rows** (§4.1).

### 1.1 User decisions (2026-09-28) — do not reopen

- **What enters:** every new row of the chat, tool cards included (a turn is mostly tool
  cards), with the 70ms stagger when several rows arrive together.
- **The slight blur of the mock's entrance is kept** for now; the user judges it in the
  browser during the global fine-tuning pass.
- Split 5a / 5b / 5c as above.

## 2. Goals

1. A chat row that **arrives live** enters: it fades in (`--motion-dur-3`,
   `--motion-ease-out`). A row that **is a whole card by itself** also slides up `0.75rem`,
   grows from `scale(.985)` and clears a `0.125rem` blur; a user message slides in from the
   right (`1rem`), without blur. A row that is **one slice of a larger assistant card** only
   fades: a moving or scaled slice would open a gap in the card, inset its side borders
   against the slices around it, or leave the card's top open while it moves (§4.1). In a
   turn, that means the "starting"/"working" message slides in at the start of the turn,
   and the rows added to the turn's card afterwards fade in. Rows arriving in the same
   update enter one after the other, 70ms apart.
2. **Nothing else plays it:** scrolling back, a KeepAlive return or re-mount, the first load,
   a lazy content fill, a view change (display mode, debug override, timestamps, a group or
   detail-block toggle), a catch-up after a reconnect, and the swap of a synthetic row
   (optimistic message, failed send, streamed block, "starting" message) for its successor
   never animate.
3. While a session's items load and the chat positions itself, a **skeleton** (four card
   shapes with a moving shimmer) shows instead of the spinner, the blank phase and the brief
   view of the transcript top. It then crossfades into the chat. A quick opening shows no
   skeleton at all.
4. Same result in Firefox and Chrome. Reduced motion: no slide, scale or blur, only the
   opacity fade; the skeleton pulses in opacity instead of the moving shimmer.

## 3. Out of scope

- Overlays, toasts, tooltips (5b); session-list cascade, tab crossfade, theme reveal (5c).
- The share viewer's **live** entrances (§12). The share viewer gets the skeleton only.
- The session view's own fallback "Loading session..." (`views/SessionView.vue`, shown
  while the session object is not in the store): unchanged.

## 4. What "arrives live" means — new `frontend/src/utils/chatEntrance.js`

A pure module (no Vue, no DOM), unit-tested.

### 4.1 Facts it relies on

- The chat's virtual scroller keys each rendered row by `item.lineNum`
  (`SessionItemsList.vue`, `:item-key="item => item.lineNum"`). A row that leaves the
  render range is unmounted and re-created later (`VirtualScroller.vue` renders
  `props.items.slice(start, end)`): **mounting is not arrival**.
- Real rows have a 1-based `lineNum`. Day separators have the string key `daysep-<lineNum>`
  (`utils/visualItems.js`). Synthetic rows have negative keys (`constants.js`
  `SYNTHETIC_ITEM`): failed sends `-3000 - seq`, optimistic user message `-2000`, starting
  message `-1500`, streamed blocks `-1000 - blockIndex`, working message `-500`, ephemeral
  result `-400`.
- **Live marking.** `applySessionItemsAdded` (`composables/wsSessionItems.js`) calls the
  store action `markItemsLive(sessionId, lineNums)` for every `session_items_added`
  WebSocket message, **then** `addSessionItems` (which recomputes the visual items
  synchronously). `markNewTailItemsLive` (reconnect, `useReconciliation.js`) also calls
  `markItemsLive`, and is directly followed by `await ensureSessionItemsCoverage`, whose fill
  is the next recompute: those marks must not count (§5.2). First loads, lazy range loads
  and coverage heals never call `markItemsLive`. Pinia's `$onAction` fires for nested
  `this.markItemsLive(…)` calls, after the outer action's listener.
- **View changes add and remove rows, synthetic ones included.** The data-store actions
  `toggleExpandedGroup` (groups and suffixes), `toggleBlockDetailedMode`,
  `ensureBlockDetailed`, `toggleSessionDebug` and `recomputeAllVisualItems` (called by the
  settings store for the display mode and the timestamps setting) recompute synchronously.
  During a turn they can add a streamed block `-1000 - i` (a thinking block in a collapsed
  group; a text stream hidden by conversation mode) or the working message `-500`.
- Streamed-block rows carry `syntheticKind === 'streaming-block'`; the starting message
  `-1500` lies inside the numeric range of streamed keys, so keys alone do not identify
  them.
- A day separator has no `kind`; its key is `daysep-<lineNum of the row after it>`.
- A line can be live and not visible: hidden in a collapsed group, by the display mode, or
  by conversation mode during the turn (`utils/visualItems.js`). A view change later shows
  it.
- When a real row replaces a streamed block, the store action `_retireStreamingBlocks`
  returns the `(streamingLineNum, realLineNum)` pairs; it runs before the caller's
  `recomputeVisualItems`. A real row can also arrive **before** `stream_block_end`
  (`streamBlockEnd`, `data.js`): the block is then retired later, and the real row is added
  while its streamed twin is still in the list.
- A real `user_message` arriving deletes the optimistic message (`addSessionItems`) and
  resolves a matching failed-send bubble (`resolveInflightSends`), in the same recompute
  batch. A failed send turns the optimistic message into a failed-send row, and a retry
  turns a failed-send row back into an optimistic message; each is two store calls, both
  before the next pre-flush watcher run. Leaving the "starting" state removes `-1500` and
  adds `-500`.
- Visual items carry `isBlockStart` / `isBlockEnd` (`recomputeVisualItems`): a block is a
  run of consecutive `user_message` rows or of consecutive non-user rows. One assistant turn
  is one card drawn across its rows (`SessionItem.vue`: first row top border and radius,
  inner rows sides only, last row bottom and shadow).

### 4.2 API

```js
export const CHAT_ENTRANCE_STAGGER_MS = 70
export const CHAT_ENTRANCE_MAX_BATCH = 6
/** Real item kinds a streamed block can turn into (see _retireStreamingBlocks). Claude also
 *  files tool_use-only and tool_result lines as content_items (backend compute), hence the
 *  hasToolBlock check of rule 4. */
export const STREAMED_KINDS = new Set(['assistant_message', 'content_items', 'reasoning'])

/** Number carried by a row key: the key itself, N for 'daysep-N', else NaN. */
export function keyLineNum(key)

/** True when a process state adds or removes rows (the starting or working message). */
export function isInTurnState(state)   // ASSISTANT_TURN or STARTING

/**
 * Whether a suppressing action (§5.2) must mark a view change.
 * @param {{ listBefore, listAfter, inTurnBefore?: boolean, inTurnAfter?: boolean }} p
 * @returns {boolean} listBefore !== listAfter, or inTurnBefore !== inTurnAfter when both
 *                    are given
 */
export function shouldNoteViewChange(p)

/**
 * Decide which rows of a new visual-item list enter.
 * @param {object} p
 * @param {Set<string|number>} p.previousKeys  keys of the previous list
 * @param {Array} p.items                      the new list (visual items, in order)
 * @param {(item) => string|number} p.getKey
 * @param {Set<number>} p.liveLineNums         lines marked live since the previous call
 * @param {Set<number>} p.retiredRealLineNums  real lines that replaced a streamed block
 *                                             since the previous call
 * @param {Set<string|number>} p.previousStreamingKeys  keys of the previous list's rows with
 *                                             syntheticKind 'streaming-block'
 * @param {boolean} p.revealed                 false = record the baseline only
 * @param {boolean} p.viewChanged              true = a view change caused this update
 * @param {(item) => boolean} p.hasToolBlock   true when the item's parsed content holds a
 *                                             tool_use or tool_result block
 * @returns {{ entering: Array<{ key, index: number, variant: 'user'|'card'|'slice' }>,
 *             keys: Set<string|number>, streamingKeys: Set<string|number> }}
 */
export function planChatEntrances(p)
```

### 4.3 Rules (in this order)

1. `keys` = the set of keys of `items`; `streamingKeys` = the keys of the rows of `items`
   whose `syntheticKind` is `'streaming-block'`. `added` = keys of `items` not in
   `previousKeys`, in list order. `removed` = `previousKeys` not in `keys`.
2. If `!revealed` or `viewChanged` → `entering = []`.
3. A key of `added` is a **candidate** when:
   - its `keyLineNum` is positive, it is in `liveLineNums`, and it is not in
     `retiredRealLineNums` (this covers `daysep-N`: a separator is a candidate with its
     live line);
   - or its `keyLineNum` is negative (synthetic).
   A key whose `keyLineNum` is NaN is never a candidate.
4. **Swaps** remove candidates:
   - `-2000` in `removed` → drop the real candidates of kind `user_message` and the
     failed-send candidates (`keyLineNum <= -3000`);
   - a failed-send key in `removed` → drop the `-2000` candidate and the real candidates of
     kind `user_message`;
   - `-1500` in `removed` → drop the `-500` candidate;
   - `-500` or `-1500` in `removed` → drop the candidates whose `syntheticKind` is
     `'streaming-block'` (the working bubble turning into the streamed text);
   - a key of `previousStreamingKeys` still in `keys` (a streamed block still on screen)
     → drop the real candidates whose `kind` is in `STREAMED_KINDS` and for which
     `hasToolBlock(item)` is false (a real text, thinking or reasoning row arriving before
     `stream_block_end`, the twin of the block; §12 notes the cost);
   - then drop every `daysep-N` candidate whose row `N` was dropped by this rule.
5. More than `CHAT_ENTRANCE_MAX_BATCH` candidates → `entering = []`.
6. Otherwise `entering` = the candidates in list order, `index` = 0, 1, 2…, and `variant`:
   - `'user'` when `item.kind === 'user_message'` (a user message is its own card);
   - `'card'` when `item.isBlockStart && item.isBlockEnd` (the row is a whole card);
   - `'slice'` otherwise (the row is one slice of a larger card: the first row of an answer
     landing above the working message, a tool row added inside the turn's card).
   A day separator takes the variant of the row after it.

Why `liveLineNums` instead of "line above the highest seen": a live line hidden by the view
is consumed (dropped) at the call where it arrived; showing it later through a view change
finds it absent from `liveLineNums`, so it never enters. First loads, lazy fills and heals
bring no live lines; the reconnect marks are never recorded (§5.2). Why `viewChanged`:
view changes also add synthetic rows (§4.1), which carry no line to check.

## 5. Wiring — new `frontend/src/composables/useChatEntrance.js`

### 5.1 API

```js
export function useChatEntrance({ items, getKey, isRevealed, env = globalThis })
// items: Ref<Array> (visual items); getKey: item => key; isRevealed: () => boolean
// returns { itemClass(item), itemStyle(item), noteLive(lineNums), noteRetired(pairs),
//           noteViewChange(), clear() }
```

- State: `previousKeys`, `previousStreamingKeys` (plain `Set`s), `pendingLive` and
  `pendingRetired` (plain `Set<number>`), `pendingViewChange` (boolean), `entering`
  (`reactive(new Map())`, key → `{ index, variant }`), `timers` (plain `Map`, key → timeout
  id).
- `run(initial)` calls `planChatEntrances` with `revealed: !initial && isRevealed()`,
  `viewChanged: pendingViewChange` and `hasToolBlock` (a function reading
  `getParsedContent(item)` from `utils/parsedContent.js`: true when `message.content` is an
  array holding a `tool_use` or `tool_result` block), stores `keys` and `streamingKeys` for
  the next call, empties `pendingLive` and `pendingRetired`, resets `pendingViewChange`,
  deletes the map entry and cancels the timer of every key of the previous list absent from
  the new one (a row that leaves and comes back never replays or gets cut), then records
  each entering row. It is called once at setup as `run(true)` and by `watch(items, () =>
  run(false), { flush: 'pre' })`. The setup call is always baseline-only: a list mounted
  with items already in the store (KeepAlive eviction then return, a draft re-keyed to its
  real id with its optimistic message carried over) replays nothing.
- `flush: 'pre'` is required: the class must be on the row at its first render, else the
  row shows one frame at full opacity and then blinks. (Checked: the parent's pre job runs
  before its render, and the scroller's render-range `watchEffect` flushes before the
  scroller re-renders.)
- Recording a row: cancel its pending timer if any, set the map entry, start a timer that
  deletes the entry after `index × CHAT_ENTRANCE_STAGGER_MS + dur + 100` ms. `dur` is
  `parseDurationMs` (from `utils/detailsMotion.js`) of `--motion-dur-3` read on
  `env.document.documentElement` through `env.getComputedStyle`, once per batch; when
  `env.document` is missing or the value parses to 0, `dur` is 380. The timer, not
  `animationend`, ends the entry: a row unmounted mid-animation must not keep it.
- `itemClass(item)` → `['chat-entering', 'is-' + variant]` when the item's key is in the
  map, else `null`. `itemStyle(item)` → `{ '--chat-enter-index': index }` when in the map,
  else `null`.
- `noteLive(lineNums)` adds each number of an array to `pendingLive`; `noteRetired(pairs)`
  adds each `realLineNum` of an array of pairs to `pendingRetired` (both ignore a
  non-array); `noteViewChange()` sets `pendingViewChange`.
- `clear()` empties the map, cancels every timer; `onScopeDispose` calls it.

### 5.2 In `SessionItemsList.vue`

- The call is `useChatEntrance({ items: visualItems, getKey: item => item.lineNum,
  isRevealed })` (`visualItems`, not the raw `items`), and it comes **after** the declarations of `isLoading`,
  `showVirtualScroller`, `sessionActive` and the reveal state of §7.3 (they are `const`s;
  an earlier reference throws in setup).
- `isRevealed` = `sessionActive.value && !isLoading.value && !reveal.hidden.value &&
  showVirtualScroller.value` (`reveal`: §7.3).
- New `store.$onAction` listeners, separate from the existing stream-swap one (that one
  returns early when the reader is at the bottom or the session is inactive); all are
  unsubscribed on unmount:
  - `markNewTailItemsLive` with `args[0] === props.sessionId`: set a local
    `suppressLive = true` at call time and `false` in both `after()` and `onError()`;
  - `markItemsLive` with `args[0] === props.sessionId` and `!suppressLive` →
    `entrance.noteLive(args[1])` at call time (the reconnect marks are thereby never
    recorded: their lines are a catch-up);
  - `_retireStreamingBlocks` with `args[0] === props.sessionId` →
    `after(pairs => entrance.noteRetired(pairs))`;
  - **suppressing actions** — `toggleExpandedGroup`, `toggleBlockDetailedMode`,
    `ensureBlockDetailed`, `toggleSessionDebug` (with `args[0] === props.sessionId`),
    `recomputeAllVisualItems` (view settings) and `setActiveProcesses` (reconnect: it clears
    streaming blocks and brings the working message back), the last two with no session
    argument: at call time, keep `store.localState.sessionVisualItems[props.sessionId]`; in
    `after()`, call `entrance.noteViewChange()` when `shouldNoteViewChange` says so: only if
    that reference changed (the action
    really recomputed this list; `ensureBlockDetailed` on an already detailed block, or a
    global recompute that skips this session, sets nothing). For `setActiveProcesses`, also
    pass `inTurnBefore` / `inTurnAfter` = `isInTurnState(…)` of `store.processStates[props.sessionId]?.state` at call time and
    after the call, and call `noteViewChange()` when the two results differ, even without a
    recompute: a turn that started or ended during a disconnect adds or removes its
    working/starting row at the next recompute (the catch-up), which must not animate
    either. Only these two states add or remove rows in `recomputeVisualItems`; any other
    change (`user_turn` ↔ no process, dead) sets nothing, so the flag never waits for an
    unrelated later update.
- `onDeactivated` calls `entrance.clear()`. While inactive `isRevealed` is false, so rows
  arriving meanwhile only update the baseline.
- `VirtualScroller` receives `:item-class="entrance.itemClass"` and
  `:item-style="entrance.itemStyle"`.
- The scroller root (`.session-items`, the scroll container) gets `overflow-x: hidden`.
  `overflow-y: auto` already makes `overflow-x` compute to `auto`; `hidden` removes the
  horizontal scrollbar that a user message translated `1rem` right would flash. Wide
  content (code blocks, tables) scrolls inside its own card. The implementer checks that no
  chat row is wider than the scroller today (search the chat styles for rows that can
  overflow the scroller); if one is, stop and report.
- The vertical slide needs nothing: a row moves down at most `0.75rem`, inside the
  scroller's `padding-bottom: var(--wa-space-2xl)` (2.5rem), so the scroll range does not
  change; transforms do not change the border-box the scroller's `ResizeObserver` reads;
  every row is `overflow-anchor: none`.

## 6. `VirtualScroller` — two new props

`frontend/src/components/virtual-scroller/VirtualScroller.vue`:

```js
/** Optional (item) => class value, bound on each row wrapper. */
itemClass: { type: Function, default: null },
/** Optional (item) => style object, bound on each row wrapper. */
itemStyle: { type: Function, default: null },
```

Bound on the `VirtualScrollerItem` of the `v-for` as `:class="itemClass ? itemClass(item) :
null"` and `:style="itemStyle ? itemStyle(item) : null"`. `VirtualScrollerItem` has a single
root and default `inheritAttrs`, so both fall through onto `.virtual-scroller-item` and merge
with its own `minHeight` style. The calls run inside the scroller's render, so a change of
the entering map re-renders the rows. Other users of `VirtualScroller` pass nothing.

## 7. The skeleton

### 7.1 Today (checked)

While the first load runs, the loading branch shows a spinner and "Loading...".
`loadSessionData` sets `itemsLoading = false` in its `finally`; the load watcher then awaits
`store.fetchToolStates` (a network call), during which the scroller is **visible at the top
of the transcript**; then `scrollToEdgeUntilStable({ isInitial: true })` hides it
(`.initial-scrolling { visibility: hidden }`) until the position is stable (100ms without
resize, capped at 1s). `handleRetry` and `onComputeCompleted` reload with a visible gap of
one `nextTick`. `scrollToEdgeUntilStable` first awaits a scroll already in flight, whose end
clears `isInitialScrolling` unconditionally; `scrollToBottomUntilStable` returns its
promise. `onDeactivated` clears the stability timers without resolving the wait, so a
scroll in flight at deactivation can stay pending until a later item resize restarts the
debounce.

### 7.2 Reveal controller — new `frontend/src/utils/chatReveal.js`

One small unit owns "is the chat hidden, and is the skeleton showing". It has no Vue and no
DOM; time and timers are injected; it is unit-tested.

```js
export const CHAT_SKELETON_DELAY_MS = 300        // nothing shows before this
export const CHAT_SKELETON_MIN_VISIBLE_MS = 300  // once shown, it stays at least this long

/**
 * @param {{ now: () => number, setTimeout, clearTimeout, onChange: (state) => void }} deps
 * @returns {{ setBusy(busy: boolean), restartClock(), dispose(),
 *             state: { hidden: boolean, skeletonShown: boolean, startedAt: number|null,
 *                      phaseId: number } }}
 */
export function createChatReveal(deps)
```

Initial state: `hidden: false`, `skeletonShown: false`, `startedAt: null`, `phaseId: 0`,
and an internal `busy: false`; no timer runs. Each timer kind (shown, settle, finish) has
one slot: starting a timer cancels the pending one of the same kind. Behaviour (`onChange(state)` is called after every change of `state`, and
only then):

- `setBusy(b)` returns at once when `b === busy`; else it records `busy = b`, then:
- `setBusy(true)`: cancel the settle and finish timers; `hidden = true`; if `startedAt` is
  `null`, start a phase: `startedAt = now()`, `phaseId += 1`, `skeletonShown = false`, and a
  "shown" timer that sets `skeletonShown = true` after `CHAT_SKELETON_DELAY_MS`.
- `setBusy(false)`: when `hidden` is false, do nothing. Else cancel the settle timer,
  start it with 0ms. Its callback returns if
  busy again; else, if `!skeletonShown`, **finish** at once; else start the finish timer
  for `max(0, startedAt + DELAY + MIN_VISIBLE - now())` ms.
- **finish**: cancel the "shown" timer; `hidden = false`, `skeletonShown = false`,
  `startedAt = null`.
- `restartClock()`: when `hidden`, cancel the finish timer, set `startedAt = now()`,
  `phaseId += 1`, `skeletonShown = false`, restart the "shown" timer, and, if not busy,
  start the settle timer (the skeleton becomes visible only now: used when its place was
  not visible before; its callers call it while busy).
- `dispose()`: cancel every timer.

Properties this gives by construction:

- The chat hides synchronously when work starts, and shows only in a later macrotask: a
  busy input that flips false then true within the same task (a scroll in flight clearing
  `isInitialScrolling` before the waiting scroll sets it again) never reveals the chat, and
  never restarts the phase.
- A new busy period during a hold cancels the hold and keeps the phase; its end finishes
  normally. No flag can stay stuck: `hidden` returns to `false` exactly once per phase,
  through **finish**.
- A quick phase (under 300ms) never shows the skeleton; a slow one shows it at least 300ms.

`frontend/src/composables/useChatReveal.js` wraps it for Vue:

```js
export function useChatReveal(isBusy, env = globalThis)
// isBusy: () => boolean. Returns { hidden, skeletonShown, startedAt, phaseId } (readonly
// refs mirroring state) and restartClock().
```

It creates the controller with bound wrappers — `now: () => env.performance.now()`,
`setTimeout: (fn, ms) => env.setTimeout(fn, ms)`, `clearTimeout: id =>
env.clearTimeout(id)` (a bare `env.performance.now` reference throws "Illegal invocation"
when called) — calls `setBusy(isBusy())` once at setup, watches `isBusy` with
`flush: 'sync'` into `setBusy`, and calls `dispose()` on scope dispose.

### 7.3 In `SessionItemsList.vue`

State, declared next to `isInitialScrolling` (before the immediate load watcher, which uses
it during setup):

- `revealFlows = ref(0)`; `beginRevealFlow()` increments it and returns an idempotent
  `release()` that decrements it once. A count, not an owner: each flow keeps the chat
  hidden until it ends, whatever the others do.
- `const reveal = useChatReveal(() => isLoading.value || isInitialScrolling.value ||
  revealFlows.value > 0)` — declared after `isLoading` (move `isLoading`'s declaration up
  if needed; no other change).

Flows (the load watcher's `isFirstLoad` branch, `handleRetry`, `onComputeCompleted`), for
parent and sub-agent sessions:

- `const release = beginRevealFlow()` **before** `await loadSessionData(…)`, and `try { …
  } finally { release() }` around the load and the rest of the flow.
- Inside the `try`, the flow **awaits** its `scrollToBottomUntilStable({ isInitial: true
  })` (it returns the scroll's promise), so the flow covers the whole scroll. The deferred
  branch (chat tab hidden) keeps setting `pendingScrollToBottom` and `isInitialScrolling`,
  then ends the flow; `isInitialScrolling` keeps the chat hidden until the tab shows.
- For a sub-agent, the phase ends when the flow returns (after `fetchToolStates` in the
  load watcher; after the load in `handleRetry` and `onComputeCompleted`).
- In `handleRetry`, after `loadSessionData` succeeds, call `reveal.restartClock()`: the
  error panel stayed on screen during the retry's load, so the skeleton starts counting
  only now.
- In `onScrollerBecameVisible`, when `pendingScrollToBottom` is set **and**
  `isInitialScrolling.value` is true, call `reveal.restartClock()` before running it: the
  skeleton was in a hidden tab. (Not `reveal.hidden`: `became-visible` also fires on a
  normal first load when the scroll area appears, while the flow still runs. And not
  "`pendingScrollToBottom.isInitial`": a later watcher run can overwrite it with `{
  isInitial: false }` while the chat is still hidden.)
- `onDeactivated` calls `resolveStability()` instead of clearing the two stability timers
  itself, so a scroll in flight ends; `scrollToEdgeUntilStable` skips its final `jump`
  when `!sessionActive.value` (the scroller is suspended). Its existing reset of
  `isInitialScrolling` stays.

Rendering — **one** skeleton instance per phase:

- A new wrapper `div.chat-stage` encloses the `v-if`/`v-else-if` chain that starts at the
  unavailable-history branch (`v-if="unavailableReason"`) and ends at the "Nothing to show
  yet" state, plus `.chat-scroll-area`. The ephemeral notice and the in-session search bar
  (`SessionSearchBar`), which come before that chain, stay outside it, above, exactly as
  today (the skeleton never covers the search bar); the `.session-footer` stays outside
  too. `.chat-stage { flex: 1; min-height: 0; display: flex;
  flex-direction: column; position: relative; }`: the branches keep a flex-column parent, as
  today. The implementer checks that no style targets these branches as direct children of
  `.session-items-list` (none found at spec time); container queries on
  `.session-items-list` still apply to descendants.
- The loading branch `<div v-else-if="isLoading" class="empty-state">` (spinner + text)
  becomes an empty spacer `<div v-else-if="isLoading" class="chat-skeleton-area"></div>`
  (`flex: 1; min-height: 0;`).
- Last child of `.chat-stage`: `<Transition name="chat-skeleton" type="transition"
  :key="reveal.phaseId.value"><ChatSkeleton v-if="showSkeleton"
  class="chat-skeleton-overlay" :visible="reveal.skeletonShown.value"
  :align="parentSessionId ? 'start' : 'end'" /></Transition>`, with `showSkeleton` =
  `reveal.hidden.value && !unavailableReason.value && !isComputePending.value &&
  !hasError.value && (isLoading.value ||
  visualItems.value.length > 0)` and `.chat-skeleton-overlay { position: absolute; inset: 0;
  }`. The same instance covers the load and the positioning: no swap, no jump.
- The key sits on the `Transition`, not on its child: a `restartClock()` (new `phaseId`)
  unmounts the old instance at once, without a leave; both callers run before the next
  paint (the flush that shows the overlay after a retry and the continuation of `await
  loadSessionData` are microtasks of the same task; `became-visible` comes from the
  scroller's `ResizeObserver`, whose callbacks run before paint), so the old instance is
  never seen. A normal phase end keeps `phaseId`, so the leave plays: the skeleton fades out
  while the chat fades in.
- The scroller's class binding becomes `{ 'initial-scrolling': reveal.hidden.value }`; the
  `ChatNavToolbar` `v-show` uses `!reveal.hidden.value` instead of `!isInitialScrolling`.
- `.session-items` gets `transition: opacity var(--motion-dur-2) var(--motion-ease)`;
  `.session-items.initial-scrolling` gets `opacity: 0` next to `visibility: hidden`. When the
  class goes, `visibility` switches at once and the opacity fades in.
- `.chat-stage` gets `:aria-busy="showSkeleton ? 'true' : null"` (not the list root, which
  also holds the composer); the skeleton is `aria-hidden="true"`.

### 7.4 New `frontend/src/components/session/detail/ChatSkeleton.vue`

- Props: `visible` (Boolean), `align` (`'end'` | `'start'`, default `'end'`: parent
  sessions open at the bottom, sub-agent tabs at the top).
- Markup: root `div.chat-skeleton` with `:class="{ 'is-visible': visible }"` and
  `aria-hidden="true"`; four `div.chat-skeleton-bar` in the mock's order and sizes (§1);
  user bars `align-self: flex-end`.
- Root CSS: `display: flex; flex-direction: column; gap: 1.5rem; padding:
  var(--wa-space-l); overflow: hidden; pointer-events: none; opacity: 0; transition:
  opacity var(--motion-dur-2) var(--motion-ease);` `justify-content: flex-end` for
  `align="end"`, `flex-start` for `'start'` (the overlay's `inset: 0` gives it the stage's
  size); `.chat-skeleton.is-visible { opacity: 1; }`, then
  `.chat-skeleton.chat-skeleton-leave-to { opacity: 0; }` (same specificity, declared
  after, so the leave wins; scoped is fine: Vue's `Transition` puts the class on the
  component's root, which carries the scope attribute).
- The instance mounts with `visible` false and fades in when the controller sets it. An
  instance that leaves before it was visible goes from 0 to 0: nothing flashes.
- Bars: `flex-shrink: 0; border-radius: var(--wa-panel-border-radius);` base colour
  `--chat-skeleton-base: color-mix(in oklab, var(--wa-color-surface-default),
  var(--wa-color-text-normal) 7%)` (a hair darker than the chat card in light, a hair
  lighter in dark, where the real cards are lighter than the panel) and highlight
  `--chat-skeleton-shine: color-mix(in oklab, var(--wa-color-surface-default),
  var(--wa-color-text-normal) 13%)`; `background: linear-gradient(90deg,
  var(--chat-skeleton-base) 0%, var(--chat-skeleton-shine) 50%, var(--chat-skeleton-base)
  100%); background-size: 200% 100%;` `animation: chat-skeleton-shimmer 1.4s linear
  infinite;` `@keyframes chat-skeleton-shimmer { from { background-position: 100% 0; } to {
  background-position: -100% 0; } }` (one full period; seamless; left to right).
- Reduced motion (`@media (prefers-reduced-motion: reduce)`): bars get `background:
  var(--chat-skeleton-base)` and `animation: motion-status-pulse 1.4s ease-in-out
  infinite`; the opacity fade stays.
- Keyframes in the component's scoped style (Vue renames them consistently);
  `motion-status-pulse` is global and referenced as is (roadmap §6d.2).
- The colours are a starting point for the user's review (light **and** dark, §11).

### 7.5 Share viewer — `frontend/src/share-session/ShareItemsList.vue`

- `initialLoading = ref(true)`; `loadInitial` sets it to `false` in a `finally` (it
  rethrows non-"not ready" errors; the `finally` still runs).
- `const reveal = useChatReveal(() => initialLoading.value)` (same delay and hold as the
  SPA).
- The `VirtualScroller` gets `:class="{ 'initial-scrolling': reveal.hidden.value }"` and
  the `ChatNavToolbar` `v-show` adds `&& !reveal.hidden.value`: the rows that fill during
  the load stay hidden under the skeleton.
- `.share-items-list` is already `position: relative` (`ShareSessionApp.vue`, unscoped
  style; also used by the sub-agent drawer through `SharedSubagentView.vue`). Inside it,
  after the scroller: `<Transition name="chat-skeleton" type="transition"
  :key="reveal.phaseId.value"><ChatSkeleton v-if="reveal.hidden.value &&
  !preparationPending" class="chat-skeleton-overlay" :visible="reveal.skeletonShown.value"
  :align="parentSessionId ? 'start' : 'end'" /></Transition>`, and
  `:aria-busy="reveal.hidden.value ? 'true' : null"` on `.share-items-list`.
- A new `<style scoped>` block in `ShareItemsList.vue` (it has none today) carries the
  overlay rule and the `.session-items` opacity transition / `.initial-scrolling` rules of
  §7.3.
- The share bundle is not HMR'd: `cd frontend && npm run build` after the change (CLAUDE.md).

## 8. Shared CSS — `frontend/src/styles/motion.css`

Appended at the end of the file:

```css
/* Live chat entrances (step 5a). The classes and --chat-enter-index are set by
   useChatEntrance on the virtual scroller's row wrapper. Movement × --motion-amount:
   reduced motion keeps the opacity fade only. A row that is one slice of a larger card
   only fades: moving a slice would open a gap in the card. */
@keyframes chat-enter {
    from {
        opacity: 0;
        translate: 0 calc(0.75rem * var(--motion-amount));
        scale: calc(1 - 0.015 * var(--motion-amount));
        filter: blur(calc(0.125rem * var(--motion-amount)));
    }
}
@keyframes chat-enter-user {
    from {
        opacity: 0;
        translate: calc(1rem * var(--motion-amount)) 0;
        scale: calc(1 - 0.015 * var(--motion-amount));
    }
}
@keyframes chat-enter-fade {
    from { opacity: 0; }
}
.chat-entering {
    animation: chat-enter var(--motion-dur-3) var(--motion-ease-out) both;
    animation-delay: calc(var(--chat-enter-index, 0) * 70ms);
}
.chat-entering.is-user {
    animation-name: chat-enter-user;
}
.chat-entering.is-slice {
    animation-name: chat-enter-fade;
}
```

- Individual properties (`translate`, `scale`), never `transform` (motion.css rule).
- `var()` inside keyframes resolves on the animated element, where `--motion-amount` is
  inherited from `:root`.
- The 70ms in the CSS and `CHAT_ENTRANCE_STAGGER_MS` are the same value; a test pins it.
- `motion.css` is shared with the share viewer and the artifact shell: the rules are inert
  there (no element carries `.chat-entering`).

## 9. Invariants

- An entrance never changes a row's border-box, margin or padding (only `opacity`,
  `translate`, `scale`, `filter`).
- A **real** row enters at most once per `SessionItemsList` instance: only when its line is
  in `liveLineNums` at the call where its key is added, and `liveLineNums` is emptied at
  every call. Synthetic keys (`-500`, `-2000`, `-1000 - i`) can enter again each time they
  come back live.
- No entrance at the setup call, after a view change, while loading, while
  `reveal.hidden`, while inactive, or when the scroller's `v-show` hides it.
- Only a row that is a whole card, or a user message, moves; a slice of a larger card only
  fades.
- `reveal.hidden` becomes false exactly once per phase, in a macrotask after the busy input
  last turned false, and never while a flow, the load or the initial scroll is running.
- One skeleton instance per phase covers both the load and the positioning.
- The skeleton never shows with the unavailable-history callout, the compute-pending
  callout, the error panel or an empty
  session (`showSkeleton` excludes them).

## 10. Tests (node:test, `npm test`)

- `frontend/src/utils/chatEntrance.test.js`:
  - `keyLineNum`: number, `daysep-12` → 12, other strings → NaN; a NaN key is never a
    candidate.
  - `revealed: false` → no entering; `viewChanged: true` with a new synthetic key and a
    new live line → no entering.
  - A new key whose line is live enters; a new key whose line is not live does not (first
    load, lazy fill, heal, view change revealing an older or a hidden live line); a key
    present before does not.
  - A live line not in `items` at its call, then added at a later call with an empty
    `liveLineNums` → does not enter.
  - Retired real line → does not enter; the other live lines of the same call do.
  - A streamed block still in `keys` + live real rows in the same call: an
    `assistant_message` → does not enter; a `content_items` row whose `hasToolBlock` is
    true (a Claude tool_use or tool_result line) → enters; a `content_items` row whose
    `hasToolBlock` is false (a Claude thinking-only line) → does not enter; a `reasoning`
    row → does not enter; a `tool_use` row (Codex) → enters.
  - A streamed block in `previousStreamingKeys` but gone from `keys` (retired) → the rule
    does not apply (only `retiredRealLineNums` does).
  - `-1500` in the previous list (not a streamed block) does not suppress a live
    `assistant_message`.
  - `-500` removed + streamed-block key added → nothing enters; `-1500` removed +
    streamed-block key + `-500` added → nothing enters.
  - Optimistic removed + live `user_message` added → does not enter, nor its new
    `daysep-N`; a live tool row added in the same call does.
  - Failed bubble removed + live `user_message` added → does not enter.
  - Optimistic removed + failed added → nothing enters; failed removed + optimistic added →
    nothing enters.
  - `-1500` removed + `-500` added → nothing enters; `-500` added alone → enters.
  - Variants: `user_message` → `user`; a row with `isBlockStart && isBlockEnd` → `card`; a
    new block-start row followed by an existing row of the same block → `slice`; an inner
    row → `slice`; a new `daysep-N` takes the variant of row N; indexes follow list order.
  - 7 candidates → none enter; 6 → all enter with indexes 0–5.
  - `streamingKeys` returned = the keys with `syntheticKind === 'streaming-block'`.
  - `isInTurnState`: true for `assistant_turn` and `starting`; false for `user_turn`,
    `dead`, `undefined`.
  - `shouldNoteViewChange`: same list and no turn values → false; new list → true; same
    list, `inTurnBefore` false and `inTurnAfter` true → true; `user_turn` → no process
    (both false) → false.
- `frontend/src/composables/useChatEntrance.test.js` (fake timers, `effectScope`, a `ref`
  list, a fake `env`):
  - the setup call never produces a class, even with `isRevealed()` true;
  - `noteLive` then a list update adding that line → class and style, removed after the
    timer; `dur` read from the env, and 380 when the env has no document or the token is
    empty;
  - `noteRetired` or `noteViewChange` then an update → no class; the next update does not
    reuse the stale state;
  - a key re-entering while its old timer runs keeps its class for the full new duration;
  - a key removed from the list while entering loses its entry and its timer; when it comes
    back without being live, it gets no class;
  - `hasToolBlock` is true for a Claude-shaped parsed content with a `tool_use` or
    `tool_result` block in `message.content`, false for a thinking-only one and for a
    Codex-shaped payload with no `message.content`;
  - `clear()` removes every class and cancels the timers; disposing the effect scope does
    the same;
  - `noteLive` / `noteRetired` with a non-array change nothing;
  - with `isRevealed()` false, a live line added in an update gets no class; when
    `isRevealed()` is true again, that row does not enter at the next update.
- `frontend/src/utils/chatReveal.test.js` (fake clock and timers):
  - busy true → `hidden` at once; busy false after 100ms → `hidden` false after the 0ms
    settle, `skeletonShown` never true;
  - busy for 500ms → `skeletonShown` true at 300ms; busy false at 500 → `hidden` stays true
    until 600, then false;
  - busy false then true in the same task → never reveals, `phaseId` and `startedAt`
    unchanged;
  - busy true again during a hold, then false at a time past the hold → reveals (no stuck
    `hidden`), `phaseId` unchanged;
  - `restartClock()` → `phaseId` + 1, `skeletonShown` false until 300ms later; ignored
    when not hidden;
  - a second busy period after **finish** starts a new phase (`phaseId` + 1, new
    `startedAt`, `skeletonShown` false until 300ms later);
  - `restartClock()` during a hold (not busy) → the hold is cancelled, `phaseId` + 1, and
    the settle finishes at once (nothing was shown in the new phase);
  - `onChange` is called on every state change, with the new state;
  - a fresh controller has the initial state, and `setBusy(false)` on it changes nothing
    (no timer, `onChange` not called);
  - `setBusy(false)` twice during a hold, then `setBusy(true)`: the chat does not reveal
    when the old hold would have ended (no untracked timer);
  - `restartClock()` during the 0ms settle window leaves one settle timer;
  - `dispose()` cancels every timer.
- `frontend/src/composables/useChatReveal.test.js` (`effectScope`, a `ref` busy source, a
  fake `env` whose `performance.now` and timers **throw when called with a wrong `this`**):
  the setup call applies the initial busy value (with a source false at setup, `hidden` is
  false synchronously after setup; with a source true, it is true); a change of the source reaches the
  controller synchronously; the refs mirror the state; scope dispose cancels the timers.
- `frontend/src/styles/motion.test.js` (extend): the three keyframes exist; they use
  `translate` / `scale`, never `transform`; every distance, the scale delta and the blur
  radius are multiplied by `var(--motion-amount)`; `chat-enter-fade` animates `opacity`
  only; `.chat-entering` uses `--motion-dur-3` and `--motion-ease-out`; `.is-user` and
  `.is-slice` map to their keyframes; the stagger literal in the CSS equals
  `CHAT_ENTRANCE_STAGGER_MS`.
- `frontend/src/styles/motion.test.js` test 4 (status pulses under reduced motion): its
  `localRules` list gets `['ChatSkeleton.vue',
  componentTree('../components/session/detail/ChatSkeleton.vue'), '.chat-skeleton-bar',
  'motion-status-pulse 1.4s ease-in-out infinite']`. The bar rule inside the component's
  reduced-motion block therefore uses exactly the selector `.chat-skeleton-bar` (the test's
  `findRule` matches the selector list exactly), and `ChatSkeleton.vue` has exactly one
  reduced-motion block (the test counts them).
- The existing suite stays green (586 tests before this step).

## 11. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

1. Send a message: the user message slides in from the right; the "starting"/"working"
   message slides up; the rows then added to the turn's card fade in; the streamed answer
   does not replay when its real row replaces it; the user message does not replay when its
   real row replaces the optimistic one.
2. Scroll up then down again: nothing replays. Switch sessions and come back: nothing
   replays. During a turn, toggle the display mode, expand a group, open a
   conversation-mode detail block: nothing enters.
3. Several rows in one update: 70ms stagger.
4. Open a large session not yet loaded: the skeleton shows (bottom-aligned) after 300ms,
   then crossfades into the chat already at the bottom; no view of the transcript top; no
   second fade-in of the skeleton. A sub-agent tab: top-aligned skeleton.
5. A quick opening (small session): no skeleton; the chat fades in.
6. Open a session directly on the Files tab, then switch to Chat: the skeleton appears only
   if the reveal takes more than 300ms, and stays at least 300ms.
7. Load error, then Retry: the skeleton follows the same timing from the retry's success.
8. Reduced motion (Firefox `ui.prefersReducedMotion = 1`): entrances are fades only; the
   skeleton pulses in opacity.
9. No horizontal scrollbar flashes when a user message enters; the view stays pinned to the
   bottom while rows enter.
10. Skeleton colours in light **and** dark.
11. Reconnect: during a turn, go offline in the browser devtools, then back online: the working message and the caught-up rows do not replay. Same when the turn
    started or ended during the outage.
12. Switch sessions while one is loading, and deactivate a session during its initial
    scroll (switch away at once): the chat is never left hidden.
13. Share viewer: the worktree cannot serve shares (roadmap §6c.3); check that the built
    bundle contains the skeleton rules, and look at it on the main instance after the merge.

## 12. Limitations

- **Containing block and blur cost:** during an entrance (`index × 70 + 380 + 100` ms at
  most), the row is a containing block for `position: fixed` descendants (`translate`,
  `scale`, `filter` not `none`), a stacking context, and glass inside it loses its backdrop
  blur. Accepted for review (§1.1); the blur can be removed in the global pass (one line in
  `chat-enter`).
- **Conversation mode:** the turn's rows are live when they arrive but hidden until the turn
  ends; when they show, they do not enter (they are no longer in `liveLineNums`). Same for a
  live row that joins a collapsed group.
- **Before `stream_block_end`:** while a streamed block is on screen, a Claude text or
  thinking row (`assistant_message`, or `content_items` without a tool block) or a Codex
  agent message or reasoning row does not enter, even when it is not the twin of the block
  (rare: the stream shows one message at a time).
- **Same-update coincidences:** a live row processed in the same watcher run as a view
  change does not enter; nor a live row processed in the first list update after a
  reconnect's `setActiveProcesses` that started or ended this session's turn.
- **Failed send found at opening** (intended): the opening audit (`auditInflightSends`,
  async) can add a failed-send bubble after the reveal; it slides in like a user message,
  which draws the eye to the failure.
- **Chat tab shown mid-load:** open a session on another tab (Files…) and switch to Chat
  after 300ms while it still loads: the skeleton was made visible in the hidden panel, so it
  appears at full opacity without its fade (a pop of the skeleton, never a flash of the
  chat); its minimum visible time counts from its first 300ms. The clock restarts only when
  the deferred initial scroll runs (§7.3).
- **Hidden chat tab:** a row arriving while the chat tab is hidden (session still active)
  keeps its entry for its timer; showing the chat within that window plays the rest of it.
- A row that arrives while the reader is far above (outside the render range) mounts later
  without entrance, unless it mounts within its timer window (a truncated entrance).
- The share viewer: skeleton only, no live entrances.

## 13. Delivery

- Implementation by a sub-agent, then a code review by another sub-agent, as for 4a–4c; no
  separate plan file.
- Update `docs/plans/2026-09-26-visual-refresh-roadmap.md`: status row 5 (5a done with its
  commit), a §6g summary with lessons, the test count in §10.
- One commit after the user's browser review and explicit "commit".
