# Step 7e — question options interior (design)

Status: draft for review. Parent: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §6m (7e), values
§8.11 / §9 "Step 7". Decisions by the user (2026-09-30): scope = the two question bodies only;
selection shown by an indicator in the card (radio for single choice, check box for multiple choice)
plus an accent outline and glow on the card.

## 1. Goal

The option cards of a question show which options are chosen: a springy radio or check box inside the
card, and an accent outline and soft glow on the chosen card. Today a chosen card only changes its
border and background colour (`.option-card.selected`), and the cards have no radio or check box at all
(they are `wa-card` toggle buttons with `role="button"` and `aria-pressed`).

Out of scope: tool approvals (Approve / Deny, the mode `wa-select`, the permission `wa-switch`), MCP
elicitation forms, the footer motion (7d), the "Other…" link and its text area.

## 2. Where it applies (verified in code)

| Body | File | Cards |
|---|---|---|
| Claude `ask_user_question` | `components/session/detail/items/claude_code/PendingRequestBody.vue` (`ask_user_question` template, cards at `~1190-1215`) | single or multiple choice (`question.multiSelect`) |
| Codex `request_user_input` | `components/session/detail/items/codex/RequestUserInputBody.vue` (cards at `~262-286`) | single choice only |

Both bodies carry an identical copy of the card CSS in their `<style scoped>`: `.question-options`,
`.option-card` (+ `.selected`, `.disabled`, `:focus-visible`), `.option-card-content`, `.option-label`
(the Claude copy also has `.option-card.auto-focused:focus-within`; the Codex copy the same rule).
`.option-description` is NOT card-specific: other components define their own `.option-description`
(`AgentSettingsPopover.vue`, `SettingsPopover.vue`, …) and the Claude body also uses it in the mode
selector; it stays where it is.

## 3. Behaviour

| State | Card | Indicator |
|---|---|---|
| Rest | Neutral border, raised surface, `--depth-1` shadow | Empty circle (radio) or empty rounded square (check box), neutral border |
| Hover (pointer devices, not disabled), card NOT chosen | ONE THIRD of the way from "not chosen" to "chosen": border, background tint and halo (light and dark) | Still no mark; the ring border goes one third of the way to the accent; no ring glow |
| Hover (pointer devices, not disabled), card chosen | TWO THIRDS of the way from "not chosen" to "chosen" (a little less lit than the chosen card without the pointer) | Keeps its dot or tick; the ring stays fully accent with its glow (only the card colours change) |
| Chosen | Accent border, accent-tinted surface, soft accent glow | Accent border and a soft accent glow; the inside stays the surface colour. The accent dot (radio) or accent tick (check box) springs in |
| Un-chosen (multiple choice, or a single-choice switch to another option) | Back to rest, colours fade | The dot or tick fades and shrinks out |
| Disabled (`.disabled`, while responding) | Existing: opacity 0.6, `not-allowed` cursor | Unchanged |
| Focus | Existing focus halo, unchanged | — |

With a mouse a click always shows: unchosen → hover (1/3) → click → hover (2/3) → pointer leaves →
chosen (full); and back down for an un-choose (multiple choice): 2/3 → 1/3 → rest. On a single-choice
question a click on the chosen card does not un-choose it (accepted, §5b).

Radio for single choice (`question.multiSelect` false, and every Codex question), check box for
multiple choice (`question.multiSelect` true). Choosing "Other…" clears the chosen option of a
single-choice question and every chosen option of a multiple-choice one (`toggleOther`,
`PendingRequestBody.vue:814-826`): the indicators empty through the same un-chosen transition.

## 4. Mechanism (CSS only)

No new JS state: everything follows the existing `.selected` class. The indicator is a pure-CSS element
inside the card.

### 4.1 Markup (both bodies)

`.option-card-content` becomes a row: the indicator, then the text column.

```html
<div class="option-card-content">
    <span class="option-indicator" :class="multiSelect ? 'option-indicator--check' : 'option-indicator--radio'" aria-hidden="true"></span>
    <div class="option-card-text">
        <span class="option-label">{{ option.label }}</span>
        <span v-if="option.description" class="option-description">{{ option.description }}</span>
    </div>
</div>
```

The Claude body binds `multiSelect` to `question.multiSelect`; the Codex body is always
`option-indicator--radio` (a literal class). `aria-pressed` on the card stays the accessible state; the
indicator is `aria-hidden`.

### 4.2 Shared stylesheet

The card rules move out of the two `<style scoped>` blocks into one new global sheet,
`frontend/src/styles/option-cards.css`, imported in `frontend/src/main.js` directly after
`import './styles/sidebar-rows.css'` (`main.js:23`), with the same "SPA only" comment as its
neighbours: the share viewer and the artifact shell render no question form, so their entry points
do not import it (the sheet is grouped with the other SPA-only sheets; it reads `--glow-accent`,
which resolves at computed-value time whatever the order). The two scoped copies of
`.question-options`, `.option-card*` and `.option-label` are deleted; `.option-description` and
everything else stay. The global rules are wrapped in `:where()` (specificity 0, like `depth.css` and
`glow.css`) so a future component rule wins.

`wa-card` does NOT read `--border-color` / `--background-color`: its shadow styles paint
`:host([appearance='outlined'])` with `border-color: var(--wa-color-surface-border)` and
`background-color: var(--wa-color-surface-default)`. The colours reach the card only through host
declarations, so the sheet declares `border-color: var(--border-color-base)` and
`background-color: var(--background-color-base)` on the host (the pre-7e scoped CSS did the same through
a second layer of derived variables, dropped here: no rule overrides it any more; a rule in the outer
tree beats `:host` whatever its specificity, so `:where()` is safe). Firefox 156 probe:
without them the chosen card keeps the unchosen colours and only the glow ring shows.

Rules (exact values are the contract; each block is pinned whole in the test):

```css
:where(.question-options) {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-2xs);
}
:where(.option-card) {
    flex: 1 1 0;
    min-width: min-content;
    max-width: 20rem;
    cursor: pointer;
    transition: border-color var(--motion-dur-2) var(--motion-ease), background-color var(--motion-dur-2) var(--motion-ease), box-shadow var(--motion-dur-2) var(--motion-ease);
    --spacing: var(--wa-space-m);
    --border-color-base: var(--wa-color-surface-border);
    --background-color-base: var(--wa-color-surface-raised);
    --option-border-on: var(--glow-accent);
    --option-bg-on: color-mix(in oklab, var(--glow-accent) 9%, var(--wa-color-surface-raised));
    border-color: var(--border-color-base);
    background-color: var(--background-color-base);
    box-shadow: var(--depth-1);
}
:where(.wa-dark .option-card) {
    --option-bg-on: color-mix(in oklab, var(--glow-accent) 16%, var(--wa-color-surface-raised));
}
:where(.option-card.selected) {
    --border-color-base: var(--option-border-on);
    --background-color-base: var(--option-bg-on);
    box-shadow: var(--depth-1), 0 0 0 1px var(--glow-accent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 55%, transparent);
}
/* Hover (pointer devices): the colours go ONE THIRD of the way from "not chosen" to "chosen" on an
   unchosen card, TWO THIRDS on a chosen one (a click always shows, the pointer leaving completes it).
   The chosen-hover rule comes after `.selected` and after the unchosen-hover rule: same specificity
   (0 under :where), source order decides, and it must win on a chosen card under the pointer. */
@media (hover: hover) {
    :where(.option-card:not(.disabled):hover) {
        --border-color-base: color-mix(in oklab, var(--wa-color-surface-border) 66.667%, var(--option-border-on));
        --background-color-base: color-mix(in oklab, var(--wa-color-surface-raised) 66.667%, var(--option-bg-on));
        box-shadow: var(--depth-1), 0 0 0 1px color-mix(in oklab, var(--glow-accent) 33%, transparent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 18%, transparent);
    }
    :where(.option-card.selected:not(.disabled):hover) {
        --border-color-base: color-mix(in oklab, var(--wa-color-surface-border) 33.333%, var(--option-border-on));
        --background-color-base: color-mix(in oklab, var(--wa-color-surface-raised) 33.333%, var(--option-bg-on));
        box-shadow: var(--depth-1), 0 0 0 1px color-mix(in oklab, var(--glow-accent) 67%, transparent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 37%, transparent);
    }
}
:where(.option-card.disabled) {
    opacity: 0.6;
    cursor: not-allowed;
}
:where(.option-card:focus-visible),
:where(.option-card.auto-focused:focus-within) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
:where(.option-card-content) {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-s);
}
:where(.option-card-text) {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    min-width: 0;
}
:where(.option-card .option-label) {
    font-weight: 600;
    color: var(--wa-color-text);
    line-height: 1.4;
}
```

Notes: the pre-7e scoped hover lightened the border and background (+0.025 OKLCH lightness, invisible on
the white light-mode background) on EVERY card, including chosen and disabled ones and on touch (its
`&:hover` block sat inside `.option-card`). The rewrite (browser-review amendments, user decisions
2026-09-30) replaces it by the one-third / two-thirds states above, limited to `@media (hover: hover)`
(step 4a: hover effects only on pointer devices) and to non-disabled cards. The `--option-border-on` /
`--option-bg-on` variables carry the "chosen" colours so the `.selected` and the two hover rules share
them; the dark scheme only changes `--option-bg-on` (its override comes after `:where(.option-card)`,
which sets the default). The previous chosen colours were `--wa-color-border-normal` /
`--wa-color-fill-normal`; they are replaced by the accent values above. The focus rule is the union of the two existing focus rules
(`:focus-visible` and `.auto-focused:focus-within`), identical declarations.

### 4.3 The indicator

```css
:where(.option-indicator) {
    flex: none;
    position: relative;
    box-sizing: border-box;
    inline-size: 1.125rem;
    block-size: 1.125rem;
    margin-block-start: 0.125rem;
    border: 2px solid var(--wa-color-neutral-border-loud);
    background: var(--wa-color-surface-default);
    transition: border-color var(--motion-dur-2) var(--motion-ease), background-color var(--motion-dur-2) var(--motion-ease), box-shadow var(--motion-dur-2) var(--motion-ease);
}
:where(.option-indicator--radio) { border-radius: 50%; }
:where(.option-indicator--check) { border-radius: 0.3125rem; }
@media (hover: hover) {
    :where(.option-card:not(.selected):not(.disabled):hover .option-indicator) {
        border-color: color-mix(in oklab, var(--wa-color-neutral-border-loud) 66.667%, var(--glow-accent));
    }
}
:where(.option-card.selected .option-indicator) {
    border-color: var(--glow-accent);
    box-shadow: 0 0 0.5rem -0.125rem color-mix(in oklab, var(--glow-accent) 70%, transparent);
}
```

Mark (both kinds) is an `::after` that springs in with the individual `scale` property (never
`transform`, step 4a). Movement × `--motion-amount`: the un-chosen scale is
`calc(1 - 0.6 * var(--motion-amount))` (0.4 at rest, 1 when chosen); with `--motion-amount: 0` (reduced
motion) the scale is always 1 and only the opacity changes, so under reduced motion the mark FADES in
and out and does not move. Under reduced motion `--motion-ease-spring` is already replaced by
`--motion-ease` (`motion.css:74`), so there is no overshoot either.

```css
:where(.option-indicator)::after {
    content: '';
    position: absolute;
    opacity: 0;
    scale: calc(1 - 0.6 * var(--motion-amount));
    transition: opacity var(--motion-dur-1) var(--motion-ease), scale var(--motion-dur-3) var(--motion-ease-spring);
}
:where(.option-indicator--radio)::after {
    inset: 0.1875rem;
    border-radius: 50%;
    background: var(--glow-accent);
}
:where(.option-indicator--check)::after {
    inset: 0.1875rem;
    background: var(--glow-accent);
    clip-path: polygon(14% 44%, 0 65%, 50% 100%, 100% 16%, 80% 0, 43% 62%);
}
:where(.option-card.selected .option-indicator)::after {
    opacity: 1;
    scale: none;
}
```

The tick is a filled box clipped to a check-mark polygon (no rotation, so no movement declaration other
than the `scale` delta). The polygon is a starting value; the browser review tunes it. The dot and the
tick use the accent, so the mark stays visible on the accent-tinted card.

## 5. Invariants (do not regress)

- No `transform` anywhere in the new CSS: the individual `scale` property only. Its un-chosen value
  carries `var(--motion-amount)`; the chosen value is `none` (`assertMotionInvariants` accepts `none`
  and any value containing `var(--motion-amount)`; a bare `1` or `45deg` fails it).
- Reduced motion: no movement (scale is 1), fades stay (opacity, colours).
- Firefox parity: `scale`, `clip-path: polygon()`, `color-mix`, `:where()` are all supported in
  Firefox 156 (probed, or already used by `glow.css`).
- The card behaviour (click, keyboard, `aria-pressed`, focus target, auto-focus class, the "Other…"
  logic, submit) is unchanged; no JS changes except the markup of the two templates.
- Global rules are `:where()`-wrapped and mention only `.question-options`, `.option-card*`,
  `.option-indicator*`, `.option-label` (always as `.option-card .option-label`), plus `.wa-dark` and
  the state classes `.selected`, `.disabled`, `.auto-focused` (compounded on `.option-card`). Only the
  two bodies use these names (grep), so the global sheet leaks nothing.

## 5b. Limitations (accepted)

- The cards look like radio buttons and check boxes but keep the semantics of toggle buttons
  (`role="button"`, `aria-pressed`); there is no `radiogroup`, and arrow keys move focus without
  selecting (`handleOptionKeydown`, Claude `:757-788`, Codex `:117-148`), unlike native radios. Step 7e
  is visual; changing the semantics is out of scope.
- On a single-choice question a click on the already chosen card does not un-choose it, while its
  hover (two thirds) looks slightly dimmer than the full chosen state. Accepted by the user.
- The unfocused hover halo (a 1px ring plus a glow) sits under the 4px focus halo of `glow.css` on a
  focused card: the focus halo hides the ring. Already the case for the chosen card.

## 6. Tests (`node:test`, source pins like `home-motion.test.js` / `tasks-motion.test.js`)

`styles/question-options-motion.test.js`:
- Each rule of §4.2 and §4.3 is pinned whole (selector + every declaration, whitespace-normalised);
  the reduced-motion behaviour is pinned through the `scale: calc(1 - 0.6 * var(--motion-amount))`
  declaration; the `@media (hover: hover)` wrappers are pinned; the ORDER of the sheet is pinned: the dark
  override after `:where(.option-card)` (both set `--option-bg-on` at specificity 0) and the
  chosen-hover rule after `.selected` and after the unchosen-hover rule (or the chosen card would
  ignore the two-thirds colours). The full head order is pinned, so the unchosen-hover rule's own
  position (before `.selected` would render the same, probed) is fixed by convention.
- `main.js` imports `./styles/option-cards.css` directly after `./styles/sidebar-rows.css`, and the
  only importer of the sheet in `src` is `main.js` (like `sidebar-rows.test.js:79-83`: the share
  viewer, artifact shell and companion entry points do NOT import it).
- The host declarations `border-color: var(--border-color-base)` and
  `background-color: var(--background-color-base)` are part of the pinned `.option-card` block (the chosen card must show the accent in a browser).
- `glow.test.js` test 9 (`:278-297`) currently pins `.option-card:focus-visible` and
  `.option-card.auto-focused:focus-within` in the scoped styles of both bodies; the rules move to
  `option-cards.css`. `glow.test.js:91-93` `rule()` matches an exact selector list, and the focus rule
  is ONE rule with a two-selector list, so test 9 parses `read('option-cards.css')` and asserts one
  rule found with
  `rule(rules, [':where(.option-card:focus-visible)', ':where(.option-card.auto-focused:focus-within)'], topLevel)`;
  it keeps the `outline` / `outline-offset` equality checks and the repo-wide `outline: 2px solid` scan.
- Stale comments in the ALREADY-IMPLEMENTED sheet (the 7e code is in the tree before this amendment):
  delete `option-cards.css:27-29` (the "Two-layer variable indirection … lightens the derived layer"
  comment) and `:45` ("+0.025 OKLCH lightness"); the sheet's hover comment is the one in §4.2. The
  test that pins these comments (`question-options-motion.test.js` tests 9 and 10) changes: test 10
  asserts the §4.2 hover comment sits directly above the first `@media (hover: hover)`, test 9
  asserts the sheet contains no `Two-layer`, `derived layer` or `0.025`. The same test file also
  changes elsewhere: test 3's `SPEC` array and head-order list follow the §4.2 block (the dark override
  right after `:where(.option-card)`, `.selected`, then ONE `@media (hover: hover)` holding the two
  hover rules, then `.disabled` …); test 4 asserts the host declarations
  `var(--border-color-base)` / `var(--background-color-base)`, the selector
  `:where(.wa-dark .option-card)`, and that the dark override comes after `:where(.option-card)` (no
  longer after `.selected`).
- Stale comments (the implementer moves each comment with the rule it explains and fixes what the
  move makes false):
  - `PendingRequestBody.vue:1398-1409`: the `.option-card` sentence ("For .option-card (a plain
    tabindex element), :focus-within is also true …") moves to the focus rule in `option-cards.css`;
    the scoped comment keeps only its `wa-button` / `wa-textarea` part (rule at 1410-1414).
    `PendingRequestBody.vue:1416-1417` goes with its rule.
  - `RequestUserInputBody.vue:490-494` explains two rules: the option-card part moves to the focus rule
    in `option-cards.css`; the "lone text input for an options-less question" part stays as a comment
    above the scoped `wa-textarea` / `wa-input` `.auto-focused:focus-within` rule (`:500-504`).
  - `RequestUserInputBody.vue:448-451` (the two-layer `-base` / derived colour variables comment) is
    DELETED: the derived layer no longer exists in the sheet. The Claude body has no copy of this
    comment (`PendingRequestBody.vue:1488` has none).
  - `PendingRequestBody.vue:1505` and `RequestUserInputBody.vue:469` (the "10% lighter" comment inside
    the `&:hover` block) are deleted with the scoped rule: it is false. The hover comment of the
    sheet is the one in §4.2 (one third / two thirds, source order).
- Both bodies: the card markup (`option-indicator` with `aria-hidden="true"`, the `--radio` / `--check`
  class rule: Claude binds it to `question.multiSelect`, Codex is a literal `--radio`),
  `option-card-text` wrapping label and description; the two scoped copies of `.question-options`,
  `.option-card*`, `.option-card-content`, `.option-label` are gone; `.option-description` is still
  defined where it was.
- Movement invariants on the new sheet: `motion.test.js`'s `changed` array holds `.vue` paths only
  (read through `componentTree`/`styleOf`, a `<style>` regex), so a `.css` path cannot go there. Test 8
  of `motion.test.js` gains one call, following its `motion.css` lines (`:417`):
  `assertMotionInvariants(flatten(parseBlocks(stripComments(read('option-cards.css')))), 'option-cards.css')`
  (`flatten` takes the node list from `parseBlocks`; probed: it passes on the spec CSS). The rules have only `scale` (with
  `--motion-amount` or `none`), so the assertion passes; a bare `scale: 1` or a `rotate: 45deg`
  would fail it.
- Mutation testing by the code reviewer (with proof the mutant landed): drop `:where`; drop the
  `--motion-amount` factor; use `transform` for the mark; drop the `hover: hover` wrapper; swap the
  radio/check class binding; drop `aria-hidden`; import the sheet from another entry point; drop the
  dark override; drop the selected glow; drop the host `border-color` / `background-color`
  declarations; use `scale: 1` on the chosen mark; move the unchosen-hover rule before the `.selected` rule (equivalent in rendering, killed by the order
  pin only); drop `:not(.disabled)` from a hover rule;
  move the chosen-hover rule before the unchosen-hover rule; swap the 66.667% / 33.333% shares; drop the
  halo from a hover rule; place the dark override before `:where(.option-card)`; the indicator ring at
  full accent on hover; drop `--option-bg-on` from a hover mix; re-add a derived `--border-color` layer.

## 7. Browser review checklist (user)

1. Claude question, single choice: pick an option (the dot springs in, the card lights up), pick
   another (the first empties, the second springs in); the cards keep their size.
2. Claude question with multiple choice: check several, un-check one (tick springs in and fades out).
3. Codex `request_user_input` question: same as 1.
4. "Other…": on a single-choice question the chosen option's indicator empties; on a multiple-choice
   question every tick empties.
5. Dark and light scheme: the chosen card is readable, the glow is soft, not loud.
6. Keyboard: Tab and arrow keys still move focus; the focus halo shows on the focused card.
7. Reduced motion: no springing, the mark and the colours fade.
8. Hover on a phone: no stuck lit card after a tap.
9. Hover on desktop, light and dark: an unchosen card goes one third of the way to "chosen" (border,
   tint, halo, ring; no mark); click: it goes to two thirds and the mark springs in; move the pointer
   away: it reaches the full chosen look. A chosen card under the pointer shows two thirds and keeps its
   mark. A disabled card does not react. On a multiple-choice question, un-choosing goes 2/3 → 1/3 →
   rest.

## 8. Risks

- The tick's clip-path polygon inside a 1.125rem box may look off-centre or too thin in one browser:
  the review tunes it.
- The chosen background tint (9% light, 16% dark) may read too strong or too weak: the review tunes it.
- The spring is small: with the project's `--motion-ease-spring` the mark overshoots to about 1.01
  (about 0.08px on an 8px mark, probed in Firefox 156), so it reads as a fast ease-out, like
  `todo-check-pop`. The review judges "springy" knowingly; a livelier rebound needs a dedicated easing
  token, not a change of the shared one.
