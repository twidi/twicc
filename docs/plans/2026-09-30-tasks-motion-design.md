# Tasks tab motion — design (visual refresh, step 7c)

## 1. Context

Step 7 of the "Signature" visual refresh animates the secondary screens, split into 7a–7f
(roadmap §6m). 7a (stats) and 7b (home) are done (§6n, §6o). **7c (this document)** is the
**Tasks tab** of a session: tasks tick one by one — check pop, strike-through, progress bar
fill. Read first: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox
parity**, **reduced motion is reduced, not none**), §5, §6m, §6n and §6o (lessons), §8.11 and
§9 "Step 7".

What the user sees today (a session with tasks, tab **Tasks** in a dock, or the overlay):

- `components/tasks/TaskPane.vue` renders the session's latest task snapshot
  (`Session.tasks`, `store.getSessionTasks`) with `components/session/detail/items/TodoContent.vue`,
  the same component the conversation timeline uses for its todo blocks;
- an item is an icon plus its text: a check (green) for `completed`, an arrow (accent) for
  `in_progress`, a hollow circle for `pending`, a cross for `deleted`. `completed` text is
  grey; `in_progress` text is semibold; `deleted` text is grey **and struck through**;
- step 4a added the **check pop** (`TodoContent.vue`): when the pane is on screen and a task
  becomes completed, its check pops (420ms, spring). Logic: `findNewlyCompleted`
  (`utils/todoList.js`), a `popping` Set grown by union, cleaned on `animationend` /
  `animationcancel`; `TaskPane` passes `:animate="active"` (`active` = the pane is shown and
  its session active); a first render never pops; the timeline blocks never pop;
- there is no progress line and no strike-through on completed tasks.

Mock reference (`mock.css` / `app.js`, roadmap §3; lines gathered from several rules and
adapted, not a literal quote):

```css
.mock .task.done .tx { color: var(--c-quiet); text-decoration: line-through; }
.mock.fx-motion .task .tx { transition: color var(--dur-3) var(--ease); }
.mock.fx-motion .progress i { transition: width 600ms var(--ease-out); }
.mock .progress { height: .375rem; border-radius: 1rem; background: var(--c-lowered); overflow: hidden; }
.mock.fx-glow .progress i { background: linear-gradient(90deg, oklch(0.7 0.14 160), oklch(0.72 0.15 135));
    box-shadow: 0 0 .375rem oklch(0.72 0.15 150 / .5); }
/* label: "2 of 5 done", .8125rem quiet, above the bar. The mock clips the fill with
   overflow: hidden; the implementation does not, so the glow shows (§6). */
```

## 2. User decisions (2026-09-30)

- **Strike-through:** on the **Tasks tab only**. The timeline todo blocks keep their current
  look (grey text, no strike). It **draws** left to right when a task becomes completed.
- **Progress line:** added at the top of the Tasks tab: "N of M done" and a thin bar. The
  bar fills smoothly when a task completes. `deleted` tasks do not count.
- **Several tasks completed at once** (the agent rewrites its whole list): their ticks chain
  **one by one**, top to bottom, 150ms apart.
- **On open:** each time the Tasks tab is shown, the bar **fills from 0**. Tasks already
  completed are shown as they are (strike included, no check pop).
- Not in scope (unchanged): the `in_progress` arrow icon and the `pending` circle; the mock's
  spinning ring and tinted row for the current task were not chosen.

## 3. The tick of one task

When a task becomes completed while the pane is shown, four parts start together, after the
item's rank delay (`r × 150ms`, §4), and run in parallel:

| Part | Effect | Duration | Where |
|---|---|---|---|
| check | pops (existing `todo-check-pop`) | 420ms | `TodoContent.vue` |
| text colour | fades to grey | `--motion-dur-3` (380ms) | `TaskPane.vue` |
| strike-through | draws left to right | 300ms | `TaskPane.vue` |
| progress bar | width grows to the new fraction | 600ms | `TaskPane.vue` |

The bar does not wait for the ticks (its transition has no delay).

## 4. Ranks and the popping map (`TodoContent.vue`, `utils/todoList.js`)

`popping` becomes a `Map<index, rank>` (still grown by union, never replaced, first render
never pops, cleaned by `animationend` / `animationcancel` and when an item leaves
`completed`). The rank is the position among the items that become completed in the same
snapshot, in list order, capped.

New pure helper in `utils/todoList.js` (tested with `node:test`):

```js
export const TICK_MAX_RANK = 8

/** Rank per newly completed index for one snapshot change: Map<index, rank>. */
export function tickRanks(previousTodos, nextTodos, maxRank = TICK_MAX_RANK) {
    const ranks = new Map()
    let rank = 0
    for (const index of findNewlyCompleted(previousTodos, nextTodos)) {
        ranks.set(index, Math.min(rank, maxRank))
        rank += 1
    }
    return ranks
}
```

(`findNewlyCompleted` returns indices in ascending order.) The cap keeps a long list from
ticking for seconds: from the ninth item on, ticks share the last delay.

Changes in `TodoContent.vue`:

- the `todos` watcher does `for (const [index, rank] of tickRanks(oldValue, newValue))
  popping.value.set(index, rank)` instead of the `add` loop; the clean-up loop iterates
  `[...popping.value.keys()]` and `delete`s the entries that left `completed`;
- `onPopEnd(index)` and the `animate` watcher (`clear()`) work on the Map unchanged;
- the `li` gets the class `todo-item-ticking` while `popping.has(i)` and a custom property
  `--tick-rank`: `:class="[`todo-item-${todo.status}`, { 'todo-item-ticking': popping.has(i) }]"`
  and `:style="{ '--tick-rank': popping.get(i) ?? null }"` (always an object: a `null`
  value removes only that property, 7b lesson);
- the text is wrapped: `<span class="todo-item-text"><span class="todo-item-strike">{{
  getDetail(todo) }}</span></span>` (§5 needs an inline box);
- the pop rule (`.todo-item-icon--pop`) gains the delay (whole rule, pinned):

  ```css
  .todo-item-icon--pop {
      animation: todo-check-pop 420ms var(--motion-ease-spring) both;
      animation-delay: calc(var(--tick-rank, 0) * 150ms);
  }
  ```

  `both` keeps the icon at its `from` frame (scale `1 - --motion-amount`, so 0 in normal
  motion) during the delay: a chained check is invisible until its turn.

The timeline blocks are untouched: they never set `popping`, so they get no
`todo-item-ticking` class, no `--tick-rank` and no delay.

## 5. Strike-through and text colour (`TaskPane.vue`, scoped `:deep`)

The strike is a line drawn with a background on the inner inline `span.todo-item-strike`.
A background on an inline box wraps across its line fragments as if they were joined
(`box-decoration-break: slice`), so animating `background-size` from `0%` draws the line
along the first line, then the second, and so on. Probed in Firefox 156 (§9). It needs the
**inline** span: `.todo-item-text` is a flex item (blockified), a background on it would
draw one line at mid height only.

Rules in the scoped block of `TaskPane.vue`, after the existing `.task-scroll :deep(...)`
rules (pinned whole):

```css
/* Completed tasks are struck through, here only (the timeline blocks keep grey text). The
   line is a background so it can draw; it follows the text across wrapped lines. */
.task-scroll :deep(.todo-item-completed .todo-item-strike) {
    background-image: linear-gradient(currentColor, currentColor);
    background-repeat: no-repeat;
    background-position: 0 62%;
    background-size: 100% 1px;
}
.task-scroll :deep(.todo-item-ticking .todo-item-text) {
    transition: color var(--motion-dur-3) var(--motion-ease);
    transition-delay: calc(var(--tick-rank, 0) * 150ms);
}
.task-scroll :deep(.todo-item-ticking .todo-item-strike) {
    animation: task-strike-draw 300ms var(--motion-ease-out) backwards;
    animation-delay: calc(var(--tick-rank, 0) * 150ms);
}
@keyframes task-strike-draw {
    from { background-size: 0% 1px; }
}
```

- `currentColor` is the text colour (grey for completed): the line matches the text.
- `background-position-y: 62%` starts as an estimate of the line's mid height; the browser
  review tunes it (the probe showed 55% a little high). The keyframe has only `from`: the
  implicit end is the static rule's `100% 1px`.
- The text turns grey through a `color` transition delayed by the task's rank, declared
  **only on a ticking item**. The class lands in the same Vue patch as
  `todo-item-completed`, so the transition is in the after-change style and runs. It is
  scoped to ticking items on purpose: a page-wide `transition: color` would also fade the
  Tasks text during a light/dark switch (the circle reveal of step 5c) and lag behind the
  rest of the page. A task going back to `pending` / `deleted`, or a scheme change, changes
  colour at once.
- `.todo-item-ticking` lasts until the check's `animationend` (`420ms + rank × 150ms`). That
  outlasts the strike (`300ms + rank × 150ms`) and the colour fade (`380ms + rank × 150ms`),
  so removing the class (and `--tick-rank`) never cuts a running transition or animation
  (probed in Firefox 156 on Vue 3.5.27); the static rule holds the final look.
- A task that is completed at open (or whose snapshot arrives while the pane is hidden)
  shows the strike from the static rule at once, no animation.

## 6. Progress line (`TaskPane.vue`)

The existing template is `<div v-if="!tasks" class="task-state">…</div>` followed by
`<div v-else class="task-scroll">…</div>`. The `v-else` branch becomes a `template` holding
the header and the scroll (a `v-if` div between the two would break the `v-else` pairing):

```html
<div v-if="!tasks" class="task-state">…</div>
<template v-else>
    <div v-if="progress.total > 0" class="task-progress">
        <span class="task-progress-label">{{ progress.done }} of {{ progress.total }} done</span>
        <div
            class="task-progress-track"
            role="progressbar"
            :aria-valuemin="0"
            :aria-valuemax="progress.total"
            :aria-valuenow="progress.done"
            aria-label="Tasks done"
        >
            <div class="task-progress-fill" :style="{ width: progress.percent + '%' }"></div>
        </div>
    </div>
    <div class="task-scroll">
        <TodoContent … />   <!-- unchanged -->
    </div>
</template>
```

`const progress = computed(() => countTasks(tasks.value?.items))` — `tasks` is `null` in
the brief "No tasks" window (`getSessionTasks` returns `null` when the session has none), and
`countTasks` accepts `null`. The helper is pure, in `utils/todoList.js`, tested:

```js
/** Counts for the progress line: `deleted` tasks are out of both numbers. */
export function countTasks(todos) {
    let done = 0
    let total = 0
    for (const todo of todos ?? []) {
        if (todo.status === 'deleted') continue
        total += 1
        if (todo.status === 'completed') done += 1
    }
    return { done, total, percent: total ? (done / total) * 100 : 0 }
}
```

The header is outside `.task-scroll`, so it stays visible while the list scrolls. Rules
(pinned whole), in the scoped block of `TaskPane.vue`:

```css
.task-progress {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-s) var(--wa-space-s) 0;
}
.task-progress-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.task-progress-track {
    height: 6px;
    border-radius: var(--wa-border-radius-pill);
    background: var(--wa-color-neutral-fill-normal);
}
html.wa-dark .task-progress-track {
    background: var(--wa-color-neutral-border-normal);
}
.task-progress-fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, oklch(from var(--wa-color-success-60) calc(l + 0.08) c h), var(--wa-color-success-60));
    box-shadow: 0 0 0.25rem color-mix(in oklab, var(--wa-color-success-60) 30%, transparent);
    transition: width 600ms var(--motion-ease-out);
    animation: task-progress-fill 600ms var(--motion-ease-out) backwards;
}
@keyframes task-progress-fill {
    from { width: 0; }
}
```

- Track and fill copy the session footer's usage bars (`views/ProjectView.vue`
  `.usage-lane`, `.usage-lane-fill`: 6px pill, neutral track lifted in dark, gradient fill
  with a soft glow), with the success colour of the check icon.
- The track has no `overflow: hidden`: the fill's glow shows around the bar, as on the
  usage bars, and the fill's own radius (`border-radius: inherit`) shapes it.
- **Width transition** (`transition: width`): the fraction grows or shrinks smoothly when
  the counts change while the pane is shown. A first render sets the inline width with no
  transition.
- **Fill on open** (`animation: task-progress-fill`): the fill grows from 0 to its width
  each time the pane's `display` goes from `none` to visible (`layout-tool-wrap` is
  `v-show`n; a hidden tab or overlay is `display: none`) and when the component mounts.
  Data changes keep the same element, so they use the transition, not the animation. A
  change arriving during the 600ms open animation retargets the animation's implicit end
  keyframe: accepted. Moving the Tasks tab to another dock re-parents its DOM (`Teleport`),
  which also restarts the animation: the fill replays, accepted ("on open").
- `100%` is the bar's full width; `percent` may be `0` (an empty bar with the label).

## 7. Reduced motion

Roadmap §4: movement removed, fades kept; a size animation snaps like the height animations
of step 4b.

| Effect | Normal | Reduced motion |
|---|---|---|
| Check pop | 420ms spring pop | no movement (`--motion-amount: 0`), same delay |
| Text colour | 380ms fade (ticking items only) | kept |
| Strike-through | 300ms draw | **none**: the line shows at once |
| Progress bar | 600ms width transition and fill on open | **none**: the width is set at once |
| Ranks | 150ms chain | kept (delays only stagger the colour fades) |

Under reduced motion the strike shows at once for every rank while the colour fade keeps its
rank delay: a chained task can be struck while still dark for a few hundred milliseconds.
Accepted (the snap rule wins over the stagger; nothing moves).

Rules, in `TaskPane.vue` **after** the rules of §5 and §6 (same specificity: the later rule
wins; the test asserts the order):

```css
@media (prefers-reduced-motion: reduce) {
    .task-scroll :deep(.todo-item-ticking .todo-item-strike) {
        animation: none;
    }
    .task-progress-fill {
        transition: none;
        animation: none;
    }
}
```

## 8. Tests

`node:test`, pinning each new or changed rule whole (roadmap §6l.2), helpers copied from
`styles/home-motion.test.js` as that file does (including its `compileStyle` check):

- `utils/todoList.test.js` (existing, extended):
  - `tickRanks`: two items completing together in a list of four (indices 1 and 3) →
    `Map {1 => 0, 3 => 1}`; a single item → `Map {2 => 0}`; nothing newly completed →
    empty; twelve items completing together → ranks `0…7` then `8, 8, 8, 8`
    (`TICK_MAX_RANK = 8`); an inserted item that shifts indices → empty (as
    `findNewlyCompleted`; the fixture items carry distinct `content`);
  - `countTasks`: `null` → `{ done: 0, total: 0, percent: 0 }`; `deleted` tasks excluded
    from both numbers; two of five done → `{ done: 2, total: 5, percent: 40 }`; all deleted
    → `total: 0`;
  - `TICK_MAX_RANK === 8`.
- `styles/tasks-motion.test.js` (new):
  - `TodoContent.vue`: the whole `.todo-item-icon--pop` rule with its delay; template:
    `todo-item-ticking` class binding, `--tick-rank` style binding (object form), the inner
    `span.todo-item-strike` inside `span.todo-item-text`;
  - `TaskPane.vue`: the three strike / colour rules (§5) and the `task-strike-draw` keyframes; the
    `.task-progress*` rules, `task-progress-fill` keyframes; the reduced-motion block placed
    after them; the template header (`v-if="progress.total > 0"`, `role="progressbar"` and
    its three `aria-value*`, the fill's `:style` width, the label text); the `v-else`
    branch is a `<template v-else>` (the header `v-if` sits inside it); the script has
    `computed(() => countTasks(tasks.value?.items))`; `TodoContent` still has
    `:animate="active"`;
  - a `compileStyle` check (scoped, `@vue/compiler-sfc`) of `TaskPane.vue`: `:deep()`
    compiles the strike rules to `.task-scroll[data-v-<id>] .todo-item-completed
    .todo-item-strike` (and the ticking one likewise); the compiled `animation`
    declarations reference the renamed keyframes (`task-strike-draw-<id>`,
    `task-progress-fill-<id>`), which exist in the compiled output;
  - `TodoContent.vue`: `popping = ref(new Map())` (its comment says "Indices … ranks"), and
    no `popping.value.add(` left.
- `styles/motion.test.js` test 11 (existing) is updated: the pop rule now has the delay
  declaration; the `todos` watcher check looks for `.set(` and `tickRanks(` instead of
  `.add(` and `findNewlyCompleted(`; the "never replaced" and "no immediate" checks stay.
- The pane's runtime (the map cleanup, the open animation restart, the transition) is
  covered by the browser review (no component harness in the repo).

## 9. Probe (done while writing this spec)

Headless Firefox 156, a 220px-wide list item whose text wraps onto three lines inside
`span.t > span.s`; `.s` has the §5 static background and an animation of `background-size`
from `0% 1px`; paused at 30%, 60%, 100%: the line draws along the first line, continues on
the second, then the third, and ends on all three; a one-line item shows the full strike.
With `background-position: 0 55%` the line sits a little above the text's middle: hence
62% as the starting value, to be tuned in the browser.

## 10. Browser review (user, http://localhost:5174)

Trigger conditions: a session with tasks and the Tasks tab open (dock or overlay); light and
dark; the system "reduce motion" setting on and off; a narrow dock (task text wraps).

1. Open the Tasks tab → the bar fills from 0 to its fraction; tasks already completed show
   grey and struck through, with no pop.
2. Switch to another tab and back → the bar fills again; nothing else moves.
3. Let the agent complete a task → its check pops, the text turns grey while the line draws
   left to right (across wrapped lines), the bar grows and the label goes "N+1 of M done".
4. Have the agent complete several tasks in one update → the ticks chain from top to bottom,
   150ms apart; the bar grows once.
5. A task going back to pending or in progress → its text and strike change at once, no
   animation; the bar shrinks smoothly.
6. The conversation's todo blocks: completed tasks stay grey, not struck.
7. Reduced motion → no pop movement, no draw, the bar snaps; the text still fades grey.
8. Strike position: the line sits at the middle of the text, in light and dark, on one and
   several lines.

## 11. Files

- New: `styles/tasks-motion.test.js`.
- Changed: `utils/todoList.js`, `utils/todoList.test.js`,
  `components/session/detail/items/TodoContent.vue`, `components/tasks/TaskPane.vue`,
  `styles/motion.test.js` (test 11).
