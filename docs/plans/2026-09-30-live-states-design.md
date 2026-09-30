# Live states — design (visual refresh, step 6b)

## 1. Context

Step 6 of the "Signature" visual refresh is split in two. 6a (accent glow) and 6a-bis
(gliding open-row fill) are done (`docs/plans/2026-09-29-accent-glow-design.md`,
`docs/plans/2026-09-29-sidebar-row-glide-design.md`). **6b (this document)** animates the
live states: the working line, the unread eye, the context ring and the pending request
panel. Read first: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding:
**Firefox parity**, **reduced motion is reduced, not none**, **no expanding halo around the
working badge**), §8.9 (what the user sees) and §9 "Step 6" (mock values).

Mock reference (`mock.css`, `fx-live` block, roadmap §3). The rules ported here:

```css
.mock.fx-live .working {
    border: 1px solid transparent;
    background:
        linear-gradient(var(--assistant-card), var(--assistant-card)) padding-box,
        conic-gradient(from var(--angle), transparent 0 55%, var(--b-60) 78%, oklch(0.82 0.13 calc(var(--h) + 60)) 90%, transparent 100%) border-box;
    animation: spin-angle 2.8s linear infinite;
    box-shadow: 0 0 1.5rem -0.5rem oklch(0.67 0.13 var(--h) / .45);
}
@keyframes spin-angle { to { --angle: 360deg; } }
.mock.fx-live .working .label {
    background: linear-gradient(90deg, var(--c-quiet) 0%, var(--c-quiet) 38%, var(--c-text) 50%, var(--c-quiet) 62%, var(--c-quiet) 100%);
    background-size: 250% 100%; background-clip: text; color: transparent;
    animation: text-shimmer 2.2s linear infinite;
}
@keyframes text-shimmer { from { background-position: 100% 0; } to { background-position: -150% 0; } }
.mock.fx-live .dots i { animation: dot 1.2s var(--ease-out) infinite; }   /* delays .15s, .3s */
@keyframes dot { 0%, 60%, 100% { transform: none; opacity: .35; } 30% { transform: translateY(-.25rem); opacity: 1; } }
.mock.fx-live .ring .val.live { animation: ring-pulse 2.4s ease-in-out infinite; }
@keyframes ring-pulse { 50% { opacity: .65; } }
.mock.fx-live .question {
    border-color: transparent;
    background:
        linear-gradient(var(--b-fill-quiet), var(--b-fill-quiet)) padding-box,
        linear-gradient(120deg, var(--b-60), oklch(0.8 0.13 calc(var(--h) + 60)), var(--b-60)) border-box;
}
```

The mock's unread dot (`.name::after` with a `breathe` box-shadow) is **not** ported: the
user keeps the eye icon (§1.1).

### 1.1 User decisions — do not reopen (2026-09-29 and 2026-09-30)

- The **working line becomes a pill** with a light running around its border (the comet),
  a shimmering label and three bouncing dots.
- The **unread eye stays** (no dot) and **breathes as an opacity fade** (no halo, no size
  change).
- The **context ring pulses** while the agent works.
- **Every** pending request panel gets the gradient accent border: question, tool
  approval, form (not only questions).
- While the agent **waits for the user** (a pending request), the pill stays but is
  **calm**: no comet, no shimmer, no bouncing dots.
- The hopping robot stays as it is; no expanding halo around it (roadmap §4).

## 2. Today (checked, working tree at `dbd5151d`)

### 2.1 Working line — `components/session/detail/items/WorkingAssistantMessage.vue`

- Rendered by `items/claude_code/Message.vue` (`isStartingAssistantMessage` →
  `label="starting" process-state="starting"`, `isWorkingAssistantMessage`) and
  `items/codex/Message.vue` (same two cases). The share viewer renders it too
  (`share-session/shims/dataStoreShim.js`, whose `getPendingRequests` returns `[]`).
- Root: `<div class="working-assistant-message text-content">` (`display: flex`, full
  width, `gap: var(--wa-space-s)`, italic, `--wa-font-size-m`). `TextContent.vue`'s scoped
  `.text-content` rule does not reach it, but the global rule of `SessionItem.vue`
  (unscoped `<style>`, "Two successive markdown blocks", lines 891-897) does: the
  synthetic starting and working items have `kind: 'assistant_message'`
  (`stores/data.js`, `share-session/shims/dataStoreShim.js`), and the root is the 2nd
  child of `.session-item` (the 1st is the toggles `<div>`). So when the row above ends
  with a text block, the working line gets `padding-top: var(--wa-space-xl)` today.
- Left: the amber `hand` icon (`.working-assistant-message__awaiting`, scoped
  `awaiting-pulse` 1.5s) when `isAwaiting` (pending requests of the session), else
  `ProcessIndicator` (a `wa-spinner` in `starting`, the animated robot in
  `assistant_turn`).
- Text: `{{ providerLabel }} is {{ plainPhrase }}...` or the phrase groups (`<strong>`
  verbs when several, `<code>` targets) followed by a literal `...`.
- The row sits inside the assistant card (`SessionItem.vue`: a non-user
  `.virtual-scroller-item` paints the card; `.session-item` declares
  `--assistant-card-bg-color`).

### 2.2 Unread eye — six sites, all `<wa-icon name="eye">`, amber except the switcher

| Site | Class | Colour |
|---|---|---|
| `session/list/SessionListItem.vue` compact row | `.compact-unread-indicator` | `--wa-color-warning-60` |
| `SessionListItem.vue` project line (no process) | `.unread-indicator.standalone-unread-indicator` | warning-60 |
| `SessionListItem.vue` process line | `.unread-indicator` | warning-60 |
| `ui/AggregatedProcessIndicator.vue` (every use: `ProjectView.vue` workspace rows, `WorkspaceCard.vue`, `ProjectCard.vue`, `ProjectDetailNavList.vue`, `ProjectDetailHeader.vue`, `ProjectSelectorRow.vue`, `WorktreeSelectorRows.vue`, `ProjectSelectOptions.vue`) | `.unread-indicator` (a `<span>` around the icon) | warning-60 |
| `app/CommandPalette.vue` session rows | `.palette-unread-icon` | warning-60 |
| `app/SessionSwitcher.vue` rows | `.switcher-unread` | `--wa-color-brand-60` |

None is animated. Out of scope: the menu items' `eye` icons (mark as read/unread,
`SessionSelectionBar.vue`), the file viewer's `eye` buttons, the favicon.

### 2.3 Context ring — three components

`wa-progress-ring` with class `context-usage-ring` (`SessionHeader.vue`, twice: the full
header and the `compact-context-ring` duplicate) or `onode-context-ring`
(`OrchestrationNode.vue`, `AgentTreeNode.vue`). `--indicator-color` inline
(`contextUsageColor`: `var(--glow-context-ring)` below 50%, warning, danger). `glow.css`
adds a drop-shadow on `::part(base)` and the dark track. Parts: `base`, `label`, `track`,
`indicator`. Working signal per component:

- `SessionHeader.vue`: `processState` (`store.getProcessState(sessionId)`),
  `.state === PROCESS_STATE.ASSISTANT_TURN`; pending requests through
  `store.getPendingRequests(sessionId)` (a pending request keeps the backend in
  `ASSISTANT_TURN`, see `WorkingAssistantMessage.vue`).
- `OrchestrationNode.vue`: `nodeData.process.state`, a topology virtual state; the
  awaiting case is its own state `awaiting_user_input`.
- `AgentTreeNode.vue`: `isRunning` (`!!store.getProcessState(node.id)`).

### 2.4 Pending request panel — `components/message/PendingRequestForm.vue`

- Rendered in `SessionItemsList.vue`'s `.session-footer` (ephemeral path and main path,
  hybrid sessions included), a `<wa-divider>` then `.pending-request-form`:
  `padding: var(--wa-space-s)`, `background: var(--wa-color-surface-default)`, no border,
  no radius, `max-height: 50dvh`. `.maximized` = `position: absolute; inset: 0; z-index:
  2`. `.minimized` = only the `CollapsedBar` (padding 0, card chrome stripped so it reads
  like the composer's collapsed bar).
- Header: amber (`--wa-color-warning-60`), icon `circle-question` (questions) or
  `shield-halved`. The question icon rule `.question-icon { color:
  var(--wa-color-primary-60) }` uses an **undefined token**: the declaration is invalid at
  computed-value time, so the icon inherits the amber. The same undefined token colours the
  "Other" link of both question bodies
  (`components/session/detail/items/claude_code/PendingRequestBody.vue`
  `.other-toggle-link`, `components/session/detail/items/codex/RequestUserInputBody.vue`
  `.other-toggle-link`), which then
  inherit their parent's text colour.

### 2.5 Shared tokens and keyframes

- `glow.css` (three bundles: SPA, share viewer, artifact shell): `--glow-accent`
  (`brand-60`), `--glow-accent-shifted` (hue +50°, lighter).
- `motion.css` (three bundles): `@keyframes motion-status-pulse` (opacity 1 → 0.45 → 1),
  global, used by status indicators under reduced motion; `--motion-amount` (1, 0 under
  reduced motion); `@keyframes motion-spin` keeps turning under reduced motion (a status
  indicator, roadmap §4).

## 3. Goals

1. The working line is a pill in the assistant card: a comet runs around its border, the
   label shimmers, three dots bounce. Starting and working states animate; the awaiting
   state is calm.
2. The unread eye fades gently in and out everywhere it marks unread content.
3. The context ring's indicator pulses while its session or agent works (not while it
   waits for the user).
4. The pending request panel is an inset card with a gradient accent border and a soft
   accent glow; the question icon and "Other" links get their intended accent.
5. Firefox gets the same result as Chrome. Reduced motion removes movement only.

## 4. Shared pieces — `styles/glow.css` (new "Live states" section at the end)

```css
/* Live states (step 6b, docs/plans/2026-09-30-live-states-design.md). */

/* The comet's angle: registered, so it interpolates in a keyframe. */
@property --glow-live-angle {
    syntax: '<angle>';
    inherits: false;
    initial-value: 0deg;
}
@keyframes glow-live-spin {
    to { --glow-live-angle: 360deg; }
}
@keyframes glow-live-shimmer {
    from { background-position: 100% 0; }
    /* One tile (250% size: offset travel = 2.5 × the width): a seamless loop, one pass.
       The mock's -150% travelled 1.5 tiles and cut the highlight at each restart. */
    to { background-position: -66.667% 0; }
}
/* Movement × --motion-amount: under reduced motion only the opacity pulses. */
@keyframes glow-live-dot {
    0%, 60%, 100% { translate: none; opacity: 0.35; }
    30% { translate: 0 calc(-0.25rem * var(--motion-amount)); opacity: 1; }
}
/* The context ring's soft pulse (the mock's ring-pulse): shallower than
   motion-status-pulse (0.45), as roadmap §8.9 asks for a soft pulse. */
@keyframes glow-live-ring-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.65; }
}
```

- `@property` and `@keyframes` are global by nature; a scoped Vue style would rename the
  keyframes, so they live here and the components reference them by name (same pattern as
  `motion-status-pulse`).
- The share viewer and the artifact shell import `glow.css` too: the working line in a
  shared session animates the same way.
- No new colour token: the comet and the border use `--glow-accent` and
  `--glow-accent-shifted`.

## 5. Working pill — `WorkingAssistantMessage.vue`

### 5.1 Template

- Root keeps `working-assistant-message` and gains `working-assistant-message--calm` when
  `isAwaiting`. `text-content` is removed: the global xl padding of §2.1 would otherwise
  land inside the pill. The gap it gave is kept outside the pill (§5.4).
- Each of the two existing text branches (`v-if` plain phrase, `v-else` phrase groups)
  keeps its own `<span>`, which gains `class="working-assistant-message__phrase"` and
  loses the trailing literal `...`.
- Inside each phrase span, right after the text (no whitespace before it, where the `...`
  was): `<span class="working-assistant-message__dots"
  aria-hidden="true"><i></i><i></i><i></i></span>`. Inside the phrase, the dots follow
  the text's last line when the phrase wraps (limitation §12: they can wrap alone). Screen readers lose
  the "..."; the phrase alone ("Claude is thinking") carries the meaning.
- The icon logic (hand / `ProcessIndicator`) is unchanged.

### 5.2 Styles (scoped)

```css
.working-assistant-message {
    /* flex + fit-content, not inline-flex: no anonymous line box (and strut) around it. */
    display: flex;
    width: fit-content;
    align-items: center;
    max-width: 100%;
    gap: var(--wa-space-s);
    padding: var(--wa-space-3xs) var(--wa-space-m);
    font-style: italic;
    font-size: var(--wa-font-size-m);
    /* 1em: a full pill on one line, a rounded box when a long phrase wraps. */
    border-radius: 1em;
    border: 1px solid transparent;
    --live-pill-fill: var(--assistant-card-bg-color, var(--wa-color-surface-default));
    --live-pill-rim: color-mix(in oklab, var(--glow-accent) 22%, transparent);
    background:
        linear-gradient(var(--live-pill-fill), var(--live-pill-fill)) padding-box,
        conic-gradient(from var(--glow-live-angle), transparent 0 55%, var(--glow-accent) 78%,
            var(--glow-accent-shifted) 90%, transparent 100%) border-box,
        linear-gradient(var(--live-pill-rim), var(--live-pill-rim)) border-box;
    box-shadow: 0 0 1.5rem -0.5rem color-mix(in oklab, var(--glow-accent) 45%, transparent);
    animation: glow-live-spin 2.8s linear infinite;
}
```

- The third layer is a faint static rim: the pill keeps its shape where the comet is
  transparent.
- `--assistant-card-bg-color` is inherited from `.session-item` (§2.1); the fallback
  covers a context without it.

Phrase shimmer and dots, after the root rule:

```css
.working-assistant-message__phrase {
    background: linear-gradient(90deg, var(--wa-color-text-quiet) 0%, var(--wa-color-text-quiet) 38%,
        var(--wa-color-text-normal) 50%, var(--wa-color-text-quiet) 62%, var(--wa-color-text-quiet) 100%);
    background-size: 250% 100%;
    background-clip: text;
    color: transparent;
    /* The mock's sweep speed: 2.2s for 3.75 widths → 1.47s for 2.5 widths. */
    animation: glow-live-shimmer 1.47s linear infinite;
    /* A long unbreakable target (a URL in <code>) wraps inside the pill instead of
       spilling out: a flex item's min-width is its min-content width otherwise. */
    min-width: 0;
    overflow-wrap: anywhere;
}
.working-assistant-message__phrase code {
    color: var(--wa-color-text-normal);
}
/* Inside the phrase (color: transparent): an explicit colour. A text-less inline-flex box
   takes its bottom edge as baseline: the dots sit on the last line's baseline.
   Smaller than the mock's (0.375rem dots, 0.25rem gap, accent) and in the phrase's quiet
   colour: they replace the "..." and sit like periods in the italic phrase, next to an
   accent comet that already carries the colour. */
.working-assistant-message__dots {
    display: inline-flex;
    gap: 0.1875rem;
    margin-inline-start: 0.125rem;
    color: var(--wa-color-text-quiet);
}
.working-assistant-message__dots i {
    display: block;
    width: 0.25rem;
    height: 0.25rem;
    border-radius: 50%;
    background: currentColor;
    animation: glow-live-dot 1.2s var(--motion-ease-out) infinite;
    /* The delayed dots show the first keyframe (0.35) during their delay, not 1. */
    animation-fill-mode: backwards;
}
.working-assistant-message__dots i:nth-child(2) { animation-delay: 0.15s; }
.working-assistant-message__dots i:nth-child(3) { animation-delay: 0.3s; }
```

- `<strong>` inherits `color: transparent` and shows the shimmer. `<code>` paints its own
  background over the clipped text, so it gets an explicit colour (static).
- The existing `code` rule and the awaiting hand rule stay.

Calm, after all the rules above. Once scoped, the calm root rule has the same specificity
as the root rule, so source order decides; the calm phrase and calm dots rules are more
specific than their base rules:

```css
.working-assistant-message--calm {
    animation: none;
    background:
        linear-gradient(var(--live-pill-fill), var(--live-pill-fill)) padding-box,
        linear-gradient(var(--live-pill-rim), var(--live-pill-rim)) border-box;
    box-shadow: none;
}
.working-assistant-message--calm .working-assistant-message__phrase {
    animation: none;
    background: none;
    color: var(--wa-color-text-quiet);
}
.working-assistant-message--calm .working-assistant-message__dots i {
    animation: none;
    opacity: 1;
}
```

The calm dots, fully opaque in the quiet colour, read as "..." in the phrase's colour.

### 5.3 Reduced motion

The comet and the shimmer stop; the pill's status is carried by the robot's and the dots'
opacity pulses (roadmap §4: movement goes, status indicators stay as opacity pulses).

- Last in the style block (same specificity as the base rules, later in the source: they
  win):

  ```css
  @media (prefers-reduced-motion: reduce) {
      .working-assistant-message {
          animation: none;
          background:
              linear-gradient(var(--live-pill-fill), var(--live-pill-fill)) padding-box,
              linear-gradient(var(--live-pill-rim), var(--live-pill-rim)) border-box;
      }
      .working-assistant-message__phrase {
          animation: none;
          background: none;
          color: var(--wa-color-text-quiet);
      }
  }
  ```

  **No `box-shadow`** on the root: the static glow stays (not movement), so a working
  pill still differs from the calm one.
- Result: the comet stops (static rim, glow kept); the shimmer stops (plain quiet
  phrase). The dots rule is not repeated: they keep fading.
- The status stays visible: the robot of a working pill already switches to
  `motion-status-pulse` (`robot-working.css`); the spinner of a starting pill keeps
  turning (a status indicator, roadmap §4); the dots keep fading (`--motion-amount` is 0
  in `glow-live-dot`, so only their opacity changes).
- No exception to add to the `motion.css` header: nothing rotates or moves under reduced
  motion.

### 5.4 Gap above the pill — `SessionItem.vue` (unscoped `<style>`)

The "Two successive markdown blocks" rule (§2.1) gets a sibling rule, right after it,
inside the same `.session-items { … }` block:

```css
    .virtual-scroller-item:has( > .session-item[data-kind="assistant_message"] > .text-content:last-child)
    + .virtual-scroller-item > .session-item[data-kind="assistant_message"]:has(> .working-assistant-message:nth-child(2)) {
        --assistant-card-top-spacing: var(--wa-space-xl);
    }
```

- The gap goes on the `.session-item` itself, through the card's own top padding
  variable: the card rule's `padding` shorthand reads `--assistant-card-top-spacing` on
  that same element. The gap stays inside the painted card, the same size as today.
- Not a `margin-top` on the pill: the 1st child of `.session-item` has no in-flow
  content, and `.session-item` has no top padding or border there, so a pill margin would
  collapse out of the card and leave a see-through strip.
- No overlap with the `.is-block-start` rule, the other writer of that variable: it
  applies only after a user row or a day separator, never after an assistant text row.

## 6. Breathing unread eye

Every site of §2.2 adds, to its existing class rule:

```css
animation: motion-status-pulse 2.4s ease-in-out infinite;
```

- Opacity only (1 → 0.45 → 1): allowed under reduced motion; no media query.
- The colours do not change (the switcher's brand eye stays brand).
- `AggregatedProcessIndicator.vue`: on `.unread-indicator` (the span).
- `SessionListItem.vue`: on `.unread-indicator` (covers the standalone and process-line
  eyes) and `.compact-unread-indicator`.
- `CommandPalette.vue`: `.palette-unread-icon`. `SessionSwitcher.vue`: `.switcher-unread`.

## 7. Pulsing context ring

### 7.1 Signal — `utils/liveStates.js` (new)

```js
/** Whether a session's context ring pulses: it works and waits for nobody. */
export function isContextRingLive(processState, pendingRequests) {
    return processState?.state === PROCESS_STATE.ASSISTANT_TURN && !(pendingRequests?.length > 0)
}
```

(`PROCESS_STATE` from `constants.js`.) Consumers:

- `SessionHeader.vue`: `contextRingLive = computed(() => isContextRingLive(processState.value,
  store.getPendingRequests(props.sessionId)))`; both rings bind `:class="{ 'is-live':
  contextRingLive }"`.
- `OrchestrationNode.vue`: `is-live` when `nodeData?.process?.state === 'assistant_turn'`
  in the template binding (`nodeData.value?.…` in script; `nodeData` can be `null`; the topology already separates
  `awaiting_user_input`); not through the helper.
- `AgentTreeNode.vue`: `is-live` when `isRunning` (limitation §12).

### 7.2 Style — `glow.css`

```css
:where(wa-progress-ring.is-live:is(.context-usage-ring, .onode-context-ring))::part(indicator) {
    animation: glow-live-ring-pulse 2.4s ease-in-out infinite;
}
```

- The indicator arc pulses; the track and the percentage label stay. The drop-shadow on
  `base` is drawn from everything `base` renders (arc, track, label): the arc's share of
  it fades with the arc.
- Opacity only: kept under reduced motion.
- 6a kept every rule off `::part(indicator)` because a `filter` drop-shadow there is
  clipped by the SVG (`glow.test.js` test 7 asserts no `::part(indicator)` at all). An
  opacity animation paints nothing outside the arc, so it is safe; test 7 narrows to "no
  `filter` on `::part(indicator)`" (§10).

## 8. Pending request panel — `PendingRequestForm.vue`

### 8.1 The card (not minimized)

```css
.pending-request-form:not(.minimized) {
    margin: var(--wa-space-xs);
    border: 1px solid transparent;
    border-radius: var(--wa-border-radius-l);
    background:
        linear-gradient(var(--wa-color-surface-default), var(--wa-color-surface-default)) padding-box,
        linear-gradient(120deg, var(--glow-accent), var(--glow-accent-shifted), var(--glow-accent)) border-box;
    box-shadow: 0 0 1rem -0.5rem color-mix(in oklab, var(--glow-accent) 45%, transparent);
}
```

- The `background` shorthand replaces the base rule's `background` for the not-minimized
  state; the minimized bar keeps today's look (§2.4).
- Maximized (`position: absolute; inset: 0` over `.session-items-list`, the transcript and
  the composer): a margin would leave a see-through 0.5rem frame showing the transcript
  and the composer. So, after the rule above (same specificity once scoped: source order
  decides):

  ```css
  .pending-request-form.maximized {
      margin: 0;
      border-radius: var(--pending-maximized-radius, 0);
      box-shadow: none;
  }
  ```

  The card fills the chat area edge to edge and keeps only the gradient border: the glow
  would fall outside `.session-items-list` (`overflow: hidden`), which clips it.
- The maximized card's corners follow the host card's clip. `.session-items-list` is
  always in a rounded, clipping card (`surfaces.css` `.panel-card`, `--panel-radius`;
  overflow clipping follows `--panel-inner-radius`):
  - main session and subagent tabs (`SessionView.vue`, the center `wa-tab-panel`s, and
    `SessionContent.vue`): the list sits in the center `.panel-card`
    (`SessionLayout.vue`, `overflow: hidden`), under the tab bar (main tab) or under the subagent's own header
    (subagent tabs), so it reaches
    the card's **bottom** corners only;
  - ephemeral session (`SessionView.vue`, `<SessionItemsList class="panel-card">`): the
    list is the card: **all four** corners.

  So `SessionItemsList.vue` (scoped) declares the radius the maximized card must take:

  ```css
  .session-items-list {
      --pending-maximized-radius: 0 0 var(--panel-inner-radius) var(--panel-inner-radius);
  }
  .session-items-list.panel-card {
      --pending-maximized-radius: var(--panel-inner-radius);
  }
  ```

  (The first declaration goes at the end of the existing `.session-items-list` rule; the
  `.session-items-list.panel-card` rule is new.) The custom property inherits into the child component's form. The card's outer
  edge then matches the clip arc, and its 1px gradient border runs unbroken along it.
  Square corners without this would be cut by the host's clip, breaking the border at
  each rounded corner; rounded corners where the host is square would show the
  transcript.
- Static: no animation, nothing for reduced motion.
- Applies to every request type and both providers (one shell).
- The full-width `<wa-divider>` above the form stays in every state: it separates the
  footer from the chat, and the minimized bar needs it.

### 8.2 Accent token fixes

- `components/message/PendingRequestForm.vue` `.question-icon`,
  `components/session/detail/items/claude_code/PendingRequestBody.vue` and
  `components/session/detail/items/codex/RequestUserInputBody.vue` `.other-toggle-link`: `var(--wa-color-primary-60)` →
  `var(--wa-color-brand-60)`. The question icon turns accent inside the amber header; the
  "Other" links turn accent.

## 9. Invariants

- No `@layer`, no `!important` in the new CSS.
- Every movement × `--motion-amount` or removed under reduced motion (comet, shimmer);
  opacity pulses (dots, robot, eye, ring) stay.
- No new animation on the robot; no halo around it.
- `glow.css` uses tokens of `depth.css` and `motion.css` (`--motion-amount` in
  `glow-live-dot`), which the three bundles import before it (`glow.test.js` test 1).

## 10. Tests (node:test, `npm test`)

In `styles/glow.test.js` (CSS read as text, as today), `styles/motion.test.js` (item 1's
`glow-live-dot` invariant) and a new `utils/liveStates.test.js`.

**Pinning rule:** every **new** rule this spec gives in full CSS (`glow.css` §4 keyframe
frames and `@property` descriptors, §5.2–§5.3, §5.4, §7.2, §8.1 and its new
`.session-items-list.panel-card` rule) is asserted whole: its ordered declaration list
equals the spec's block exactly. An **existing** rule that gains one declaration (§6
eyes, §8.2 colours, the `.session-items-list` radius) is asserted on that declaration.
Source text (template, script) is compared after whitespace collapse (`glow.test.js`
`collapse`), so a line break counts as one space: it matters only where the spec's text
has no whitespace (e.g. `}}<span`, or right after `(`). The items below name what each test adds:

1. `glow.css` registers `--glow-live-angle` (`syntax: '<angle>'`, `inherits: false`,
   `initial-value: 0deg`) and declares `glow-live-spin` (to `--glow-live-angle: 360deg`),
   `glow-live-shimmer` (ending at `background-position: -66.667% 0`) and `glow-live-dot`
   (in `glow.test.js`). In `motion.test.js`, a
   new test builds `glowTree = parseBlocks(stripComments(read('glow.css')))` (comments
   stripped, as every existing caller does: a comment before `@keyframes` would otherwise
   turn the block into a plain rule), takes `keyframeEntries(glowTree, 'glow-live-dot')`,
   asserts it is not empty, and runs `assertMotionInvariants` on it (every `translate` is
   `none` or contains `var(--motion-amount)`); the test fails on `translate: 0 0`.
2. `WorkingAssistantMessage.vue` style: **every rule the spec gives in CSS (§5.2, §5.3) is
   pinned whole.** For each one (root, phrase, phrase `code`, dots, dots `i`, the two
   `:nth-child` delays, calm root, calm phrase, calm dots `i`, and the reduced-motion
   root and phrase), the test finds exactly one rule with that selector (in the
   `prefers-reduced-motion: reduce` block for the last two) and asserts its ordered
   declaration list (`[property, value]` pairs in source order, whitespace collapsed)
   equals the spec's block exactly. One mechanism covers every load-bearing declaration,
   its value and its position (a longhand after its shorthand, the fill as the top
   background layer, the transparent border, the dots' explicit colour, the calm
   phrase's colour). The reduced-motion root's list is `animation: none` plus the calm
   two-layer `background`, nothing else (no `box-shadow`); its phrase list is the calm
   phrase's three declarations. Source order between rules: the calm rules come after
   the base rules; the reduced-motion block comes after every other rule. The existing
   `code` rule and the awaiting hand rule are not pinned. The template has the dots span, with `aria-hidden="true"` and three `<i>`,
   inside each of the two phrase spans (plain and groups), the `--calm` class bound to `isAwaiting`, no `text-content`
   class, and no literal `...`; in the plain branch the text is followed directly by the
   dots span (`}}<span class="working-assistant-message__dots"`, no whitespace: a line
   break there would render a space before the dots).
3. The five eye rules of §6 (six sites) each declare exactly `animation:
   motion-status-pulse 2.4s ease-in-out infinite`:
   `AggregatedProcessIndicator.vue` `.unread-indicator`, `SessionListItem.vue`
   `.unread-indicator` and `.compact-unread-indicator`, `CommandPalette.vue`
   `.palette-unread-icon`, `SessionSwitcher.vue` `.switcher-unread`.
4. `SessionItem.vue`'s unscoped style has the §5.4 rule (selector ending in
   `:has(> .working-assistant-message:nth-child(2))`, declaring
   `--assistant-card-top-spacing: var(--wa-space-xl)`).
5. `isContextRingLive`: true for `assistant_turn` with no pending request; false with one
   pending request; false for `starting`, `user_turn`, `null`; true with
   `pendingRequests` `undefined`.
6. The ring rule targets `.is-live` on both ring classes, `::part(indicator)`, with
   `glow-live-ring-pulse 2.4s`; `glow.css` declares `@keyframes glow-live-ring-pulse`
   with `opacity: 0.65` at 50%. Bindings, matched as source text: `SessionHeader.vue`
   contains `isContextRingLive(processState.value,
   store.getPendingRequests(props.sessionId))` and binds `'is-live': contextRingLive` on
   both rings; `OrchestrationNode.vue` binds `'is-live'` to `nodeData?.process?.state ===
   'assistant_turn'` (template form, auto-unwrapped); `AgentTreeNode.vue` binds
   `'is-live'` to `isRunning`. The existing `glow.test.js`
   test 7 assertion `!glowStripped.includes('::part(indicator)')` becomes: no rule on
   `::part(indicator)` declares `filter` (§7.2).
7. `PendingRequestForm.vue`: the not-minimized rule and the later top-level
   `.pending-request-form.maximized` rule are pinned whole, as in item 2 (ordered
   declaration list equal to the §8.1 blocks; the maximized rule after the
   not-minimized one); in `SessionItemsList.vue`, the existing `.session-items-list`
   rule declares `--pending-maximized-radius: 0 0 var(--panel-inner-radius)
   var(--panel-inner-radius)` (one added declaration), and the new
   `.session-items-list.panel-card` rule is pinned whole; no `--wa-color-primary` left in the three files of §8.2.

## 11. Browser checks (http://localhost:5174, Firefox first, then Chrome; light and dark)

1. Send a prompt to a new session, or to one with no live process (the "starting" line
   shows only while the process launches; a session whose process is alive goes straight
   to "thinking", unless a startup setting changed, which restarts the process): the "starting" then "thinking" line is a pill; the comet turns once
   per 2.8s; the label shimmers; the dots bounce and sit on the text baseline; no layout
   jump when the pill appears. After an assistant text block, the gap above the pill is
   the same as before the change (compare with http://localhost:5173). That state lasts
   only while the next tool's input streams (once the tool card exists, it is the row
   above the pill): ask the agent to write one sentence, then create a file of a few
   hundred lines; while the file content streams, the pill sits right under the
   sentence.
2. Targets show in a Claude session (Codex never passes tools), for example in the
   **Conversation** display mode (tool cards hidden) or with two tools running in
   parallel; in the default Normal mode a single running tool hides its target once its
   input has streamed. There: the phrase
   with `<code>` targets wraps into a rounded box, code readable. At a narrow width
   (≤ 25rem, mobile), a WebFetch with a long URL, same conditions: the URL breaks inside
   the pill, nothing crosses its border.
3. A question or a tool approval arrives: the pill turns calm (hand, no comet, static
   dots); the footer panel shows the gradient card under the divider; minimize → the
   collapsed bar has no card, the divider stays; maximize → the card fills the chat area
   edge to edge; its gradient border runs unbroken around it, rounded with the panel at
   the panel's bottom corners (all four in an ephemeral session: the ghost toggle in a new
   session's composer), square elsewhere; no
   transcript visible around it or in its corners. On a question (a Claude question, and
   a Codex user-input form), the header icon is accent inside the amber header and the
   "Other" link is accent. Codex shows the "Other" link only on a question with options
   that allows a free answer: ask the Codex agent to let you pick among a few options,
   with a free "other" answer allowed.
4. Unread eyes fade in the session list (compact and normal), the project selector, the
   sidebar's workspace rows, the workspace and project cards, the project detail header and nav list, the
   palette and the switcher. Optional: the peer message review dialog's delivery project
   pickers (needs an inbound peer message; same component as the project selector).
5. The header ring (and the compact header's ring: a window at most 900px tall, header
   collapsed, its default state there)
   pulses while the agent works, stops while it waits for an answer and
   after the turn. Orchestration tree: same. Agent tree and a subagent's own header: stop
   after the turn; while the parent waits on a subagent's approval they keep pulsing
   (§12).
6. Reduced motion (OS setting): the comet stops (static rim, the glow stays); the robot
   pulses (the starting pill's spinner keeps turning: a session with no live process, as
   in check 1); the dots only fade; the label is plain; eyes and ring pulse. Once a question
   or an approval arrives, the same session's pill (now calm) has no glow.
7. Share viewer of a live session. Setup, all in the worktree instance:
   1. `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm run build`
      (the share bundle is built separately and has no hot reload; it compiles
      `WorkingAssistantMessage.vue` and `glow.css`).
   2. The share viewer is served only on the host of the **Share host** setting, which
      the worktree copied from the main instance (its public host). In the worktree app
      (http://localhost:5174), Settings → Sharing → **Share host**: note the current
      value (`https://twicc-dell-public.twidi.com` today), enter
      `http://127.0.0.2:3501` (a loopback hostname other than `localhost`, the app's,
      and other than `127.0.0.1`, where the instance's agents reach its MCP server: a
      request to the share hostname only reaches the share viewer; plain `http` is
      accepted for a local host), click **Apply**. The setting is in the worktree data
      dir: the main instance is not affected.
   3. Create a share link for a session from its menu, open
      `http://127.0.0.2:3501/share/<token>/` and hard-reload (the bundle URL has no hash).
   4. Send that session a prompt that takes a while, and watch the share page during the
      turn (the share viewer shows the working line only during an assistant turn).

   Check: the pill animates. Afterwards, set **Share host** back to the noted value and
   click **Apply**.

## 12. Limitations

- The comet repaints the pill each frame (a registered custom property in a gradient);
  one pill per visible session, accepted.
- In a KeepAlive'd hidden session view the animation does not paint (hidden element).
- In the share viewer, `getPendingRequests` always returns `[]`
  (`share-session/shims/dataStoreShim.js`): the pill of a session waiting for an answer
  keeps its comet there (never calm), as its phrase already never says "waiting".
- The share viewer does not import `robot-working.css` (SPA only): its robot is static
  today. There, under reduced motion, the dots alone carry the working status. Not
  changed here.
- The dots are an atomic inline: a line can break right before them (CSS Text 3), so on
  a phrase that just fills a line they can wrap alone onto the next line. The literal
  `...` could not. Accepted: rare, and still readable.
- A subagent's ring (agent tree, the subagent's own header) keeps pulsing, and its
  transcript's working pill keeps its comet (not calm), while a
  tool approval raised by that subagent waits on the parent session: its synthetic
  process state is always `assistant_turn` with no pending requests
  (`stores/data.js`). The parent's ring and pill are calm.

## 13. Delivery

One commit after the user's browser review and an explicit "commit", holding the code and
this spec (as step 6a-bis did):
`feat(ui): live states — working pill, breathing unread eye, pulsing ring, pending card`.
Roadmap update (status row 6, a §6l, and the test count in roadmap §10) in a separate
docs commit.

## 14. Amendments after the browser review (user, 2026-09-30) — binding

Made after `b5f625a5`, on the user's requests in the browser; they supersede §5 and §9–§11
where they differ.

- **Assistant block without card chrome** (`SessionItem.vue`): the assistant block sits
  straight on the session background. `background: transparent`, card border width `0`,
  card spacing `0` (top, bottom, left), no card shadow and no dark top edge. The right
  padding keeps `--card-spacing`. The card structure and its per-row variables stay (the
  §5.4 gap above the working line still works). User messages are unchanged.
- **Assistant markdown toolbars** (assistant message, Thinking / compaction summary, Codex
  reasoning): moved left by half the card spacing
  (`--assistant-markdown-toolbar-offset`). The user message's toolbar is unchanged.
- **Conversation mode "Show details" toggle:** inactive, it shows only while its item is
  hovered or the toggle has keyboard focus (same on touch, like the markdown toolbar);
  active, it stays visible.
- **Assistant timestamp:** half the gap above it (`--wa-space-s / 2`); it stays on the
  right.
- **Working line, no pill:** no background, border, padding, radius, comet or glow; it
  sits on the session background. `@property --glow-live-angle`, `glow-live-spin`, the
  calm root rule and the reduced-motion root rule are removed. The shimmer, the dots and
  the calm / reduced-motion phrase and dots rules stay. The user wants something nicer
  later.
- **Tool targets** (`<code>` in the phrase): no background, border, radius or padding; the
  code font alone sets them apart.
- **Dots:** about a space's width from the text (`margin-inline-start: 0.3em`), and the
  robot's hop duration (`1.4s`, `robot-working.css`). A phase alignment with the hop was
  tried and dropped by the user.

§10 tests follow: the pill rule, the calm and reduced-motion root rules and the comet
keyframes are no longer pinned; test 15 asserts no comet is left.
