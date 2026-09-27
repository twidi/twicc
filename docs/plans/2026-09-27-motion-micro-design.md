# Motion tokens + micro-interactions — design (visual refresh, step 4a)

## 1. Context

Step 4 of the "Signature" visual refresh is split in three sub-steps, each with its own
spec, review and commit:

- **4a (this document):** motion tokens, global motion rules, micro-interactions, and the
  check "pop" of a completed task.
- **4b:** open/close motion of every `wa-details` (tool cards, thinking…). Own spec.
- **4c:** gliding indicators (tabs, settings nav, palette, keyboard lists) and a
  `SegmentedControl` component. Own spec.

4b and 4c consume the tokens defined here. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` — §4 (binding user decisions, including
the **Firefox parity** rule), §8.5 and §8.6 (target look), §9 "Step 4" (mock values).

Today there are no shared motion tokens: about 150 hardcoded `transition` / `animation`
declarations (`0.1s`–`0.25s`, `ease`) in 61 files, Web Awesome theme defaults for
`--wa-transition-fast/normal/slow` (default theme 75/150/300 ms `ease`; shoelace
50/150/250 ms `ease`; awesome 75/150/300 ms `ease-in`), and 4 local
`prefers-reduced-motion` rules (`BrandLogo.vue`, `robot-working.css`,
`WorkflowStateBadge.vue`, `WorkflowRunDetail.vue`), each only setting `animation: none`.

### 1.1 User decisions (2026-09-27) — do not reopen

- **Intensity:** the mock's values (durations 120/200/380 ms, the mock's spring curve).
- **Reduced motion:** the OS `prefers-reduced-motion` setting only. No TwiCC setting.
  **Reduced, not none** (user: "reduced motion, ça veut dire reduced motion, ça veut pas
  dire no motion"): movement is removed (translations, scales, rotations, the spring
  overshoot); short color / background / opacity fades and status indicators stay.
  Status indicators whose animation is movement (the working robot's hop, the workflow
  "pending" hourglass hop) switch to a gentle opacity pulse instead of stopping (user,
  2026-09-27; §5.9). Opacity pulses and spinners keep playing as they are.
- **Button effects are global** (all `wa-button`), with exclusions (§5.1). Native
  `<button>` elements get no global rule.
- **Every Web Awesome theme** follows the tokens (default, awesome, shoelace), except the
  awesome theme's own button behaviour (§4.2).
- **No migration of the ~150 existing hardcoded transitions** in this step. They are
  aligned only where this step already edits the rule.
- **Firefox parity:** Firefox (the user's only browser) must get the same result as
  Chrome. Everything in this step is supported by Firefox 156 (`linear()`, the individual
  `translate` / `scale` / `rotate` properties, CSS animations); no JavaScript fallback is
  needed. Browser checks are done in Firefox first.
- The sidebar collapse is **not** animated (user decision); it is out of step 4.

## 2. Goals

1. One set of motion tokens used by this step and by 4b / 4c.
2. Web Awesome components follow the same timing without per-component edits.
3. With the OS reduced-motion setting, every movement this step adds is gone; fades and
   status indicators stay (§4.3).
4. Small feedback effects on buttons, snippet chips, the settings gear,
   "go" arrows, the Send icon, and a completed task's check.

## 3. Out of scope

- Show/hide animations of dialogs, drawers, popovers, dropdowns, tooltips, selects
  (`--show-duration` / `--hide-duration`): step 5 (entrances).
- `wa-details` open/close: step 4b. Gliding indicators and segmented controls: step 4c.
- Sidebar collapse animation: rejected by the user.
- Migrating existing hardcoded transitions (§1.1).
- Hover lift of stats/home cards (mock `.wcard` / `.stat`): step 7.

## 4. Tokens and global rules — new `frontend/src/styles/motion.css`

### 4.1 Tokens

On `:root`, unlayered:

| Token | Value |
|---|---|
| `--motion-dur-1` | `120ms` |
| `--motion-dur-2` | `200ms` |
| `--motion-dur-3` | `380ms` |
| `--motion-dur-press` | `60ms` |
| `--motion-ease` | `cubic-bezier(.2, .8, .2, 1)` |
| `--motion-ease-out` | `cubic-bezier(.22, 1, .36, 1)` |
| `--motion-ease-spring` | `linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 25.3%, 0.849 31.5%, 0.937 38.1%, 0.968 41.8%, 0.991 45.7%, 1.006 50.1%, 1.015 55%, 1.017 63.9%, 1.001 85.9%, 1)` |
| `--motion-amount` | `1` (unitless) |

Usage rules:

- `--motion-ease-spring` is only for things that move or scale (translate, scale,
  rotate). Colors, backgrounds, borders, shadows and opacity use `--motion-ease`. Heights
  never use the spring (4b uses `--motion-ease-out`).
- **Every movement distance is multiplied by `--motion-amount`**: a translation is
  `calc(<distance> * var(--motion-amount))`, a rotation `calc(<angle> *
  var(--motion-amount))`, a scale `calc(1 ± <delta> * var(--motion-amount))`. Reduced
  motion sets the amount to `0` (§4.3), which turns every effect into "no movement" with
  one declaration, while the transitions and animations keep running with nothing to
  move (so `animationend` still fires).

### 4.2 Web Awesome tokens

A separate rule on `:root, .wa-invert`:

```css
:root,
.wa-invert {
    --wa-transition-fast: var(--motion-dur-1);
    --wa-transition-normal: var(--motion-dur-2);
    --wa-transition-slow: var(--motion-dur-3);
    --wa-transition-easing: var(--motion-ease);
}
```

Why it wins, and why `.wa-invert`: the three theme files declare these tokens inside
`@layer wa-theme`, on the theme root (`:where(:root), .wa-theme-default` for default;
`.wa-theme-awesome` / `.wa-theme-shoelace` for the others, the theme class being on
`<html>`) **and** on descendant `.wa-light`, `.wa-dark`, `.wa-invert` elements. An
unlayered declaration beats any layered one on the same element, so the `:root` rule wins
at the root. A descendant `.wa-invert` element re-declares the layered values on itself,
which beats the value inherited from `:root`, so the mapping is repeated on `.wa-invert`
(same pattern and same reason as `depth.css`'s `:root, .wa-invert` rule). The app renders
`.wa-invert` in `AppTooltip.vue`; no app element carries `.wa-light` / `.wa-dark` (the
scheme classes sit on `<html>`). The mapping references `--motion-*` tokens, which only
`:root` declares and `.wa-invert` inherits.

The awesome theme sets the three durations to `0` on `wa-button`, `button` and `input`
buttons (`@layer wa-theme-dimension`, `.wa-theme-awesome wa-button { … }`). That
declaration is on the button element itself, so it wins over the inherited `:root` value.
This is intended: the awesome buttons keep their instant hard-shadow press.

That awesome value is a **unitless** `0`, which is not a valid `<time>`. On its own it is
harmless (Web Awesome's list is then invalid and falls back to `0s`, the intended
"instant"). But §5.1 mixes these tokens with `--motion-*` durations in one list: on an
awesome button the list would resolve to `0, 0, 0, 0, 0, 200ms`, invalid at computed-value
time, so the whole `transition-duration` would fall back to `0s` and the press of awesome
plain buttons would snap instead of springing back. So `motion.css` restates the same
"instant" with units, unlayered (it beats the layered theme rule on the same element):

```css
:where(.wa-theme-awesome wa-button) {
    --wa-transition-fast: 0s;
    --wa-transition-normal: 0s;
    --wa-transition-slow: 0s;
}
```

Awesome button colors stay instant, as the theme intends, and the §5.1 lists stay valid.

Effect: every Web Awesome component that transitions with these tokens (button colors,
inputs, selects, switches, checkboxes, radios, the `wa-details` chevron, progress bar…)
takes the new timing.

### 4.3 Reduced motion

Principle (§1.1): **reduced, not none.** Movement goes; fades and status indicators stay.

This block comes **after** the §4.1 token block in `motion.css`: both select `:root` with
the same specificity, so source order decides.

```css
@media (prefers-reduced-motion: reduce) {
    :root {
        --motion-amount: 0;
        --motion-ease-spring: var(--motion-ease);
    }
}
```

- `--motion-amount: 0` removes every movement of this step: each translation, scale and
  rotation is written as a multiple of the amount (§4.1), so all of them resolve to "no
  movement" (`0`, `1`, `0deg`). Nothing else has to be overridden per effect.
- `--motion-ease-spring` becomes the plain ease: any movement left by a later step (4b,
  4c) has no overshoot. 4b and 4c state their own reduced-motion behaviour on top of this.
- Durations do not change: color / background / border / shadow / opacity fades keep
  their short timing (120–380 ms), in the document and inside Web Awesome components.
- No global `*` rule: existing animations are left alone. Opacity pulses (the
  `pending-pulse` family) and spinners (`wa-spinner`, the rotating refresh icons) keep
  running.
- The 4 existing local rules stop a movement under reduced motion. The 3 that stop a
  status indicator (`robot-working.css`, `WorkflowStateBadge.vue`,
  `WorkflowRunDetail.vue`) switch it to an opacity pulse instead (§5.9). The
  `BrandLogo.vue` rule stays for the decorative logo (home, login) and gains a pulse for
  the logo used as a busy indicator (§5.9).
- Existing movement that this step does not create (smooth scrolls, the mobile sidebar
  drawer slide, the mobile settings panel slide, a few local hover transforms) is not
  changed here (§9).

### 4.4 Imports

`motion.css` is imported right after `glass.css` in:

- `frontend/src/main.js`
- `frontend/src/share-session/main.js`
- `frontend/src/artifact-shell/main.js`

Same reason as `depth.css` / `glass.css`: the share viewer and the artifact shell render
the same Web Awesome controls and must match the SPA.

## 5. Micro-interactions

All rules below live in `motion.css` unless a component file is named. Hover effects are
wrapped in `@media (hover: hover)`: on touch screens a hover state sticks after the tap
(the reason `MessageSnippetsBar.vue` already does this).

Individual transform properties (`translate`, `scale`, `rotate`) are used instead of
`transform`, so an effect never overwrites another component's `transform`, and two
effects on one element combine. Every distance, angle and scale delta is multiplied by
`--motion-amount` (§4.1), which is what reduced motion switches off.

### 5.1 Button press (global)

What the user sees: every button sinks slightly when pressed (scale 0.96 in 60 ms), then
springs back on release.

```css
:where(wa-button)::part(base) {
    transition-property: background, border, box-shadow, color, opacity, scale;
    transition-duration: var(--wa-transition-fast), var(--wa-transition-fast),
        var(--wa-transition-fast), var(--wa-transition-fast), var(--wa-transition-fast),
        var(--motion-dur-2);
    transition-timing-function: var(--wa-transition-easing), var(--wa-transition-easing),
        var(--wa-transition-easing), var(--wa-transition-easing),
        var(--wa-transition-easing), var(--motion-ease-spring);
}
:where(
    :root:not(.wa-theme-awesome) wa-button:not([disabled], [loading], wa-button-group wa-button, .session-item, .bookmark-item):active,
    :root.wa-theme-awesome wa-button[appearance~='plain']:not([disabled], [loading], wa-button-group wa-button, .session-item, .bookmark-item):active
)::part(base) {
    scale: calc(1 - 0.04 * var(--motion-amount));
    transition-duration: var(--wa-transition-fast), var(--wa-transition-fast),
        var(--wa-transition-fast), var(--wa-transition-fast), var(--wa-transition-fast),
        var(--motion-dur-press);
}
```

Each `wa-button:not(…):active` compound stays on one line: a line break between `)` and
`:not(` or `:active` would become a descendant combinator (the trap `depth.css` documents
above its button rule).

- The first rule repeats Web Awesome's own list on `.button` (chunk `4FOHUBBS`:
  `transition-property: background, border, box-shadow, color, opacity`, fast, easing) and
  adds `scale`. A `::part()` rule from the document beats the component's inner styles, so
  the list must be complete.
- `:active` matches the `wa-button` host while its inner `<button>` is pressed (the host is
  a flat-tree ancestor of the active element), with the mouse, touch and Space. Enter
  fires the click on keydown without an `:active` state, in both browsers: no press then.
- `:where()` keeps specificity at zero, so any component rule that sets `transition` or
  `scale` on a button part wins. None exists today (no app rule sets `transition`,
  `transform` or `scale` on `wa-button::part(base)`).
- Exclusions, and why:
  - the awesome theme's framed buttons (every appearance except `plain`): their own press
    (translate + shadow collapse) would fight the scale. Awesome **plain** buttons have no
    press of their own (the theme's press rule is `:not([appearance~='plain'])`), so they
    get this one;
  - `[disabled]`, `[loading]`: no press feedback on an inert button;
  - every button inside a `wa-button-group`, whatever its appearance: a segment scaling
    alone breaks the group's joined edges (for example the sidebar's "New session" split
    button in single-project mode);
  - `.session-item`, `.bookmark-item`: the sidebar rows are full-width buttons; a
    shrinking row reads as a glitch. Session rows get no movement at all (§5.4); artifact
    bookmark rows get no effect in this step.

**Buttons inside Web Awesome components.** Some components render a `wa-button` in their
own shadow root and export its base part. The rules above do not reach them, so they get
the same pair, through the exported part names:

| Component | Exported part | Button | Used in the app |
|---|---|---|---|
| `wa-dialog` | `close-button__base` | the "×" | every dialog |
| `wa-drawer` | `close-button__base` | the "×" | not imported today; kept so a future drawer matches |
| `wa-tab-group` | `scroll-button__base` | the start/end scroll arrows | every `TabBar` that overflows |
| `wa-tag` | `remove-button__base` | the "×" of a removable tag | `BulkArchiveConfirmDialog.vue` |

All four are `appearance="plain"` inside Web Awesome, so they get the press in every
theme, the awesome theme included (it has no press for plain buttons).

```css
:where(wa-dialog, wa-drawer)::part(close-button__base),
:where(wa-tab-group)::part(scroll-button__base),
:where(wa-tag)::part(remove-button__base) {
    /* same transition-property / -duration / -timing-function lists as :where(wa-button)::part(base) */
}
:where(wa-dialog, wa-drawer)::part(close-button__base):active,
:where(wa-tab-group)::part(scroll-button__base):active,
:where(wa-tag)::part(remove-button__base):active {
    scale: calc(1 - 0.04 * var(--motion-amount));
    /* same press duration list as above */
}
```

A user-action pseudo-class after `::part()` is valid in Chrome and Firefox. The icons of
these buttons are not exported, so the §5.2 icon grow does not apply to them (§9).

### 5.2 Icon-only plain buttons (global)

What the user sees: the small icon buttons without a background (toolbar icons, the "⋮"
of a session row…) grow their icon slightly on hover (×1.12, spring).

```css
:where(wa-button[appearance='plain']) > wa-icon:only-child:not([slot]) {
    transition: scale var(--motion-dur-2) var(--motion-ease-spring);
}
@media (hover: hover) {
    :where(wa-button[appearance='plain']:not([disabled]):hover) > wa-icon:only-child:not([slot]) {
        scale: calc(1 + 0.12 * var(--motion-amount));
    }
}
```

- Scope measured on 2026-09-27: 86 static `appearance="plain"` buttons plus 2 with a
  dynamic `:appearance` that resolves to plain (`TerminalPanel.vue`, `SessionView.vue`)
  hold a single unslotted `wa-icon` and nothing else.
- `:only-child` ignores text nodes: a plain button with one icon followed by **bare text**
  would match. A label must therefore be an element. One case exists today:
  `JsonHumanView.vue`'s `.jhv-diff-toggle`, whose `{{ … 'Diff mode' : 'Old/new mode' }}`
  follows the icon as bare text. **Change in this step:** wrap that interpolation in a
  `<span>`. A test guards the rule (§7).
- The icon is a light-DOM child of the button, so the document rule reaches it.

### 5.3 Snippet chips — `MessageSnippetsBar.vue`

What the user sees: a chip rises by 1px on hover; the existing press (`scale(0.95)`)
stays.

- The existing press moves from `transform` to the individual property, scaled by the
  amount: `.snippet-btn:active { scale: calc(1 - 0.05 * var(--motion-amount));
  transform: none; }` (was `transform: scale(0.95)`). The `transform: none` stays, with a
  comment: it cancels the awesome theme's press on native buttons
  (`.wa-theme-awesome button:not([appearance~='plain']):…:active { transform:
  translate(…) }`, `@layer wa-theme-dimension`, a 4px downward jump), which the old
  unlayered `transform: scale(0.95)` also overrode. Chips are native `<button>`s, and a
  "disabled" chip only has the `.snippet-disabled` class, so both need it.
- `.snippet-btn.snippet-disabled:active` keeps `transform: none` and adds `scale: none`;
  `.snippet-btn.snippet-disabled:hover` replaces `transform: none` with `scale: none` (a
  hover has no awesome translate to cancel).
- `.snippet-btn`'s transition becomes `background-color 0.1s, border-color 0.1s,
  scale var(--motion-dur-1) var(--motion-ease), translate var(--motion-dur-2)
  var(--motion-ease-spring)` (the two color entries keep their existing timing, §1.1).
- In the existing `@media (hover: hover)` block:
  `.snippet-btn:hover { translate: 0 calc(-1px * var(--motion-amount)); }`; the existing
  `.snippet-btn.snippet-disabled:hover` rule adds `translate: none`.
- Narrow composer (`@container message-input (width < 40rem)`): the bar is
  `overflow-x: auto`, which also clips vertically. Add `padding-block-start: 1px;
  margin-block-start: -1px;` to `.message-snippets-bar` in that container query, so the
  lifted chip is not cut and the layout does not move.

### 5.4 Session rows — `SessionListItem.vue`

**Removed after the user's browser review (2026-09-27).** The first version shifted a
hovered row 3px to the right and slid the "⋮" into place from a 4px offset. In use the
"⋮" drifted left of its place and the effect did not please the user: "on retire ça
complètement". Session rows get no hover movement; `SessionListItem.vue` is unchanged by
this step. A test guards that its styles declare no `translate` (§7). The row still
gets no press (§5.1), and the "⋮" icon keeps the global icon grow of §5.2 like every
icon-only plain button.

### 5.5 Send icon — `MessageInput.vue`

What the user sees: when the Send button shows its icon (composer narrower than 25rem;
wider, the label replaces it), the paper plane rises by 2px on hover.

In the `@container message-input (width < 25rem)` query, directly inside its
`.message-input-actions { … }` block — **not** inside the nested
`.cancel-button, .reset-button, .send-button { … }` group, where `.send-button > wa-icon`
would resolve to a `.send-button` inside a `.send-button` and never match:

```css
.send-button > wa-icon {
    transition: translate var(--motion-dur-2) var(--motion-ease-spring);
}
@media (hover: hover) {
    .send-button:not([disabled]):hover > wa-icon {
        translate: 0 calc(-0.125rem * var(--motion-amount));
    }
}
```

The "Apply settings" state shows `arrows-rotate` in the same slot; it gets the same rise
(acceptable, same button).

### 5.6 "Go" arrows (global)

What the user sees: a button whose trailing icon is a right arrow or chevron moves that
arrow 3px to the right on hover. Today: "All N sessions" and "Artifacts" on the home
screen, "Next tip" in the tip toast, "Next" in the changelog dialog. Future buttons with
the same markup get the effect.

```css
:where(wa-button) > wa-icon[slot='end']:is([name='arrow-right'], [name='chevron-right']) {
    transition: translate var(--motion-dur-2) var(--motion-ease-spring);
}
@media (hover: hover) {
    :where(wa-button:not([disabled]):hover) > wa-icon[slot='end']:is([name='arrow-right'], [name='chevron-right']) {
        translate: calc(0.1875rem * var(--motion-amount)) 0;
    }
}
```

Checked on 2026-09-27: these 4 are the only `wa-icon slot="end"` with those names.

### 5.7 Settings gear — `SettingsPopover.vue`

What the user sees: hovering the Settings button turns its gear a quarter turn (600 ms,
ease-out); leaving turns it back.

```css
#settings-trigger > wa-icon[name='gear'] {
    transition: rotate 600ms var(--motion-ease-out);
}
@media (hover: hover) {
    #settings-trigger:hover > wa-icon[name='gear'] {
        rotate: calc(90deg * var(--motion-amount));
    }
}
```

In `SettingsPopover.vue`'s scoped style; no `:deep()` is needed, because the icon is in the
component's own template. 600 ms is the mock's value and deliberately longer than the
tokens: a slow turn reads as a flourish.

### 5.8 Completed-task check pop — `TodoContent.vue` + `utils/todoList.js`

What the user sees: when a task **becomes** completed while the list is on screen (Tasks
tab during a turn), its check appears with a small pop (scale from 0 with a slight
rotation, then settles). Nothing animates when the list first appears: opening the tab,
coming back to the session, or a timeline block scrolled back into view.

- New pure function in `utils/todoList.js`:
  `findNewlyCompleted(previousTodos, nextTodos) → Set<number>` — the indices `i` where
  `nextTodos[i].status === 'completed'`, `previousTodos[i]` exists, its status is not
  `'completed'`, and both items have the same identity text (`content` when present on
  both, else `activeForm`, else the index alone). The identity check stops a false pop
  when an item is inserted before others and statuses shift by index. `previousTodos`
  `null`/`undefined` → empty set.
- **Visibility gate.** `TaskPane` stays mounted when hidden: `SessionView.vue` shows it
  with `v-show="layout.isToolPanelVisible('tasks')"`, and a background session stays
  mounted under KeepAlive. Watchers keep running there, but a CSS animation does not run
  in a `display: none` or detached subtree: it would start later, when the tab or the
  session is shown again — exactly the case that must not pop. So:
  - `SessionView.vue` passes `:active="isActive && isToolTabShown('tasks')"` to
    `TaskPane` (the same expression it already passes to the other tool panes);
  - `TaskPane.vue` declares an `active` Boolean prop (default `false`) and passes it to
    `TodoContent` as `:animate="active"`;
  - `TodoContent.vue` declares an `animate` Boolean prop (default `false`). The timeline's
    TodoWrite / TaskList blocks do not pass it, so they never pop (they hold a fixed
    snapshot anyway).
- **Set of popping indices.** `TodoContent.vue` keeps a local `ref(new Set())`. A `watch`
  on `() => props.todos` (not `immediate`):
  - when `animate` is `false`, adds nothing;
  - otherwise **adds** every index of `findNewlyCompleted(oldValue, newValue)` to the set
    (union, never replace): Pinia's `$patch` in `updateSession` replaces `tasks.items`
    with a new array on every `session_updated` broadcast, even with no change, and a
    replacement would cut a running pop;
  - in both cases, **deletes** every index whose item in `newValue` is missing or not
    `'completed'`: when an item leaves `completed` during its pop, `v-if` removes the
    icon and neither `animationend` nor `animationcancel` is guaranteed, so the index
    would otherwise stay and pop falsely later.
- A `watch` on `animate` clears the set when it becomes `false`.
- The completed icon gets the class `todo-item-icon--pop` while its index is in the set.
  On `animationend` **or** `animationcancel` of that icon, the index is removed.
- `TaskPane.vue` passes `:key="sessionId"` to `TodoContent`, so a pane that ever
  receives another session's tasks re-mounts instead of comparing two sessions' lists.
- The `watch` never runs on mount, so a first render never pops.
- CSS (scoped):

```css
.todo-item-icon--pop {
    animation: todo-check-pop 420ms var(--motion-ease-spring) both;
}
@keyframes todo-check-pop {
    from {
        scale: calc(1 - var(--motion-amount));
        rotate: calc(-30deg * var(--motion-amount));
    }
}
```

- Reduced motion: the amount is `0`, so the keyframe resolves to `scale: 1; rotate: 0deg`:
  the animation still runs its 420 ms with nothing to move, and `animationend` fires, so
  the set is cleaned. `var()` inside `@keyframes` is resolved against the animated
  element, in Chrome and Firefox.
- A task completed while its list is hidden is never popped later: by design (§9).

### 5.9 Moving status indicators under reduced motion

What the user sees with reduced motion on: every working-agent robot (process
indicators in the sidebar and headers, orchestration and agent trees, the Agent tool card
in the transcript, workflow run agent rows), the hourglass of a pending workflow, and the
animated TwiCC logo of the two waiting screens ("Connecting to server…" and the
"TwiCC has been updated, reloading…" dialog) no longer move; they pulse softly in
opacity, so the "busy" state stays visible. The decorative animated logo (home, login)
stays still, as today. Without reduced motion nothing changes.

- `motion.css` declares one shared keyframe (global, not scoped):

```css
@keyframes motion-status-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.45; }
}
```

- The 3 local reduced-motion rules change from `animation: none` to this pulse, each
  keeping its indicator's own period:
  - `styles/robot-working.css`: `.robot-working { animation: motion-status-pulse 1.4s
    ease-in-out infinite; }`;
  - `WorkflowStateBadge.vue`: `.wf-state-pending { animation: motion-status-pulse 1s
    ease-in-out infinite; }`;
  - `WorkflowRunDetail.vue`: `.wf-row .wf-status-pending { animation:
    motion-status-pulse 1s ease-in-out infinite; }`.
- **Logo as a busy indicator.** `BrandLogo.vue` gets a `busy` Boolean prop (default
  `false`) that adds the class `brand-logo--busy` on the full-robot root element (the one
  that already carries `brand-logo--animated`). `App.vue` passes `busy` to the two
  `<BrandLogo :size="56" animated />` of the version-mismatch dialog and the connecting
  overlay. In `BrandLogo.vue`'s existing reduced-motion block, next to the kept
  `animation: none` rule for the parts: `.brand-logo--busy { animation:
  motion-status-pulse 1.4s ease-in-out infinite; }`. HomeView and LoginView do not pass
  `busy`: their logo stays still under reduced motion. The connecting overlay is
  unreachable today (the app mounts only after `authStore.checkAuth()` resolves, and
  `isConnecting` needs `checking`, which only that startup call sets); it gets `busy` for
  consistency, so it is right if it becomes reachable.
- The two workflow rules and the `BrandLogo.vue` rule are in scoped styles. Vue rewrites an animation name only when
  the same scoped block declares that keyframe; `motion-status-pulse` is declared in
  `motion.css`, so the name stays global and resolves. `motion.css` is loaded by the three
  entry files (§4.4); `robot-working.css` and the workflow components are SPA-only.
- The hop keyframes keep their `transform`: they are existing animations, only replaced
  under reduced motion (§6 applies to the new micro-interactions).

## 6. Invariants

- No rule in this step changes layout (sizes, margins that move content, `display`).
  The snippet bar's `padding` / negative `margin` pair (§5.3) is net zero.
- Every moving effect uses individual transform properties, never `transform`. The only
  `transform` in the new or changed rules is `transform: none` on the snippet chips'
  press (§5.3), which cancels the awesome theme's native-button translate.
- Every translation, scale delta and rotation is multiplied by `--motion-amount`, so
  reduced motion removes it (§4.3).
- Every hover effect is inside `@media (hover: hover)`.
- Every new duration or easing uses a `--motion-*` token, except the gear's 600 ms and the
  check pop's 420 ms (mock values, stated in the rules), and the §5.9 reduced-motion
  pulses (`1.4s` / `1s` `ease-in-out`, each keeping its indicator's own period).

## 7. Tests (node:test, `npm test`)

- **`frontend/src/styles/motion.test.js`** (same approach as `glass.test.js`: stylesheets
  and component files are read as text):
  - the 8 tokens exist on `:root` with the §4.1 values, and the reduced-motion block
    comes after that token block in the file;
  - the unlayered `:where(.wa-theme-awesome wa-button)` rule restates the three
    `--wa-transition-*` durations as `0s` (with a unit);
  - the 4 `--wa-transition-*` mappings reference the tokens, on the selector
    `:root, .wa-invert`;
  - the reduced-motion block sets exactly `--motion-amount: 0` and
    `--motion-ease-spring: var(--motion-ease)`, and `motion.css` has no `*` selector and
    no `!important` (reduced, not none);
  - `motion.css` declares `@keyframes motion-status-pulse` with opacity only; the
    reduced-motion rules of `robot-working.css`, `WorkflowStateBadge.vue` and
    `WorkflowRunDetail.vue` use `motion-status-pulse` (no `animation: none` left there);
    `BrandLogo.vue` has the `busy` prop, the `brand-logo--busy` class and its
    reduced-motion pulse rule; `App.vue` passes `busy` to exactly the two waiting-screen
    logos;
  - the `wa-button` base transition list starts with Web Awesome's five properties, in
    order, and adds `scale`; the rule for the three exported internal button parts
    (`close-button__base` on `wa-dialog` / `wa-drawer`, `scroll-button__base`,
    `remove-button__base`) has the same lists;
  - the `wa-button` press rule: its non-awesome branch excludes `[disabled]`, `[loading]`,
    group buttons, `.session-item`, `.bookmark-item`; its awesome branch requires
    `[appearance~='plain']` with the same exclusions; the internal-parts press rule
    covers the three part names;
  - the plain icon rule (§5.2) targets `wa-icon:only-child:not([slot])` and its hover
    excludes `[disabled]`;
  - no `.vue` template holds a `wa-button` with a static `appearance="plain"` whose
    content is one unslotted `wa-icon` plus text outside any element — a `{{ … }}`
    interpolation counts as text (guards §5.2);
  - the go-arrow rule (§5.6) targets `slot='end'` with `arrow-right` and `chevron-right`;
  - invariants (§6), over `motion.css` and over the new or changed component rules
    named below — selected by their selector, not file-wide (those files hold older
    `transform` / `translate` rules this step does not touch): every hover rule
    that sets `translate`, `scale` or `rotate` sits inside `@media (hover: hover)`; every
    `translate` / `scale` / `rotate` value except `none` contains `var(--motion-amount)`;
    no `transform:` declaration in the new or changed rules other than `transform: none`;
  - component rules: `MessageSnippetsBar.vue` (hover `translate` inside
    `@media (hover: hover)`, the press as `scale`, the only `transform` left being
    `transform: none` on `.snippet-btn:active` and `.snippet-btn.snippet-disabled:active`,
    the `padding-block-start: 1px` / `margin-block-start: -1px` pair in the
    `width < 40rem` container query), `SessionListItem.vue` (no `translate` declared:
    §5.4 was removed),
    `MessageInput.vue` (Send icon rule inside the `width < 25rem` container query,
    directly in `.message-input-actions`, not inside the three-button group),
    `SettingsPopover.vue` (gear `rotate` on hover), `TodoContent.vue` (the
    `todo-item-icon--pop` animation; both `animationend` / `animationcancel` listeners;
    the `todos` watcher **adds** to the existing set — `.add(` — and never assigns a new
    set, and deletes indices no longer completed — `.delete(`; a watcher on `animate`
    that clears the set), `TaskPane.vue` / `SessionView.vue` (the `active` → `animate`
    chain, and `:key="sessionId"`), `JsonHumanView.vue` (the diff toggle's label in a
    `<span>`);
  - `motion.css` is imported right after `glass.css` in the three entry files.
- **`frontend/src/utils/todoList.test.js`** (new): `findNewlyCompleted` — a status change
  to completed; an already-completed item; an inserted item that shifts indices (no pop);
  items with only `activeForm`; `previousTodos` null; a shorter previous list.

## 8. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome)

0. `cd frontend && npm run build` (the share viewer and artifact shell bundles are not
   hot-reloaded), then restart the worktree backend (safe sequence: stop, wait for the
   process to exit, start) so it serves the new files on 3501.
1. Press a few buttons (a dialog button, a toolbar icon, a dialog's "×", the scroll arrow
   of an overflowing tab bar, the "×" of the title-filter tag in the bulk-archive dialog,
   the sidebar "New session" in all-projects mode): sink and
   spring back. Session rows and the single-project "New session" split button do not
   sink.
2. Hover plain icon buttons: the icon grows. A disabled one does not. The JSON view's
   "Diff mode" toggle (icon + label) does not grow its icon.
3. Snippet chips: rise on hover, press still shrinks; in a narrow composer the lifted chip
   is not cut.
4. Sidebar: hovering a session row moves neither the row nor its "⋮" (§5.4).
5. Narrow composer: the Send icon rises on hover.
6. Home "All N sessions" / "Artifacts", tip toast "Next tip", changelog "Next": the arrow
   moves right.
7. Settings button: the gear turns a quarter.
8. Tasks tab visible during a turn: a task turning completed pops its check. Then:
   complete a task while the Tasks tab is hidden (or the session in the background), show
   it → no pop. Hide the tab during a pop, show it again → no replay.
9. Themes default, awesome, shoelace: WA transitions take the new timing; awesome framed
   buttons keep their instant hard-shadow press; in awesome a snippet chip shrinks on press
   with no downward jump; awesome plain buttons sink and spring
   back. In DevTools,
   the computed `--wa-transition-fast` on an open tooltip's `.wa-invert` element is
   `120ms`.
10. Reduced motion emulated (Chrome DevTools rendering panel; Firefox
    `ui.prefersReducedMotion = 1` in `about:config`): none of the §5 effects moves
    (press, icon grow, snippet lift, Send, go arrows, gear, check
    pop); hover color fades and status pulses/spinners still play; a working agent's
    robot and a pending workflow's hourglass pulse in opacity instead of hopping; the
    logo of the version-mismatch dialog pulses (show it by adding the `open` attribute
    to its `wa-dialog` in the DevTools inspector: its content is always rendered), the
    home logo stays still.
11. Share viewer (a share link on the share host) and artifact shell (the consent prompt
    of an HTML artifact): a button press sinks.
12. Touch: with touch emulation that makes `(hover: hover)` false (Chrome device mode
    with a touch device, Firefox responsive design mode with touch simulation) or on the
    Android phone: after a tap, no icon stays grown, no chip stays lifted.

## 9. Limitations

- Existing hardcoded transitions keep their own timing until a later edit touches them
  (§1.1).
- A check pop is not played for a task completed while its list was hidden or not
  mounted: by design.
- Reduced motion only switches off the movement this step creates. Existing movement
  (JS smooth scrolls, the mobile sidebar drawer slide, the mobile settings panel slide,
  local hover transforms in a few components) and Web Awesome show/hide animations
  (step 5) are unchanged. Web Awesome's internal movement transitions driven by the
  `--wa-transition-*` tokens (the switch thumb slide, the `wa-details` chevron rotation)
  keep moving under reduced motion: the tokens are durations, shared with fades.
- The icons of the Web Awesome internal buttons (dialog "×", tab-bar scroll arrows, tag
  "×") are not exported parts: no icon grow. The buttons still get the press (§5.1).
- Artifact bookmark rows get no hover effect in this step.

## 10. Delivery

One commit on branch `enhanced-ui`, after the user's browser check and explicit "commit".
