# Depth — layered soft shadows and typography — design (visual refresh, step 2)

## 1. Context

Step 2 of the "Signature" visual refresh. The whole redesign, its decisions and its
process are in `docs/plans/2026-09-26-visual-refresh-roadmap.md` (read it first). Step 1
(canvas + floating panels, `docs/plans/2026-09-26-floating-panels-design.md`) is done.

Visual target: roadmap §8.3 (depth) and §8.10 (typography); mock values: roadmap §9
"Step 2 — Depth" (from the `fx-depth` and `fx-type` blocks of the mock's `mock.css`).

What the UI does today (verified in the code):

- Web Awesome components cast their shadows from three theme tokens, `--wa-shadow-s`,
  `--wa-shadow-m`, `--wa-shadow-l`:
  - `wa-card` → `s`;
  - `wa-dropdown` panel, `wa-dropdown-item` submenu, `wa-select` listbox, `wa-color-picker`
    → `m`;
  - `wa-dialog`, `wa-drawer`, `wa-popover` → `l`.
  In the default theme they are small shadows with a 2/4/8px vertical offset (s/m/l); in
  the "awesome" theme they are hard offset shadows (4/8/16px) with no blur (`--wa-shadow-blur-scale: 0`); in the shoelace theme
  they have an almost zero vertical offset (`--wa-shadow-offset-y-scale: 0.0625`).
- Each theme declares these tokens in `@layer wa-theme`: `default.css` on
  `:where(:root), .wa-theme-default, .wa-light, .wa-dark, .wa-invert`; `awesome.css` and
  `shoelace.css` on `.wa-theme-<name>` plus descendant forms such as
  `.wa-theme-<name> .wa-invert`. Every form includes a `.wa-invert` box.
- The awesome theme also gives buttons (`wa-button::part(base)`) and text fields
  (`wa-input::part(base)`, `wa-textarea::part(base)`, `wa-select::part(combobox)`) hard
  shadows of their own, in `@layer wa-theme-dimension`.
- Chat messages have **no real shadow**: under each card, a 2px-offset line of the card's
  border color (`box-shadow: <offset-x-s> <offset-y-s> <blur-s> <spread-s>
  <border color>`; slightly blurred in the default theme, a hard 4px line in the awesome
  theme), in `components/session/detail/SessionItem.vue`.
- An assistant "card" is not one element: it is a run of rows (one per virtual-scroller
  item), styled so that the first row carries the top border/radius and the last row the
  bottom border/radius and the shadow (`--assistant-card-shadow`, `none` on every other
  row). Tool cards (`wa-details.item-details`) inside it are joined the same way when
  adjacent: the upper one loses its bottom border/radius.
- The spacing between chat cards reserves `--main-shadow-size` (`--wa-shadow-offset-y-s`,
  `styles/transcript-tokens.css`) for that line, and the last row of an assistant card
  gets `margin-bottom: calc(var(--main-shadow-size) + 1px)` so the scroll container does
  not clip it at the bottom of the list. `--main-shadow-size` is `0.125rem` in the
  default theme, `0.25rem` in awesome and `0.0078rem` in shoelace. The font-size setting
  (up to 32px) is applied on `<html>`, so every `rem` follows it.
- Options in the question widget (`.permission-suggestion-card` and `.option-card` in
  `items/claude_code/PendingRequestBody.vue`, `.option-card` in
  `items/codex/RequestUserInputBody.vue`) use a hard (unblurred) line of the same kind:
  `<offset-x-s> <offset-y-s> 0 0 <border color>`.
- The session layout's overlay (`session/layout/LayoutOverlay.vue`, a step-1 panel card)
  keeps a stronger shadow than the other cards: `var(--wa-shadow-l, …)`.
- Toasts and tooltips render inside a `.wa-invert` box (`App.vue`, `ui/AppTooltip.vue`),
  which re-applies the theme's token block with the opposite scheme.
- The public share viewer (`share-session/main.js`) renders the same transcript
  components with the default theme; it imports `styles/transcript-tokens.css` but not
  `styles/surfaces.css`.

## 2. Goal

- Everything that sits "on top" of something gets a **soft, layered shadow**, on three
  levels, instead of today's hard 2px line or small offset shadow.
- Floating layers (menus, selects, dialogs, drawers, popovers, toasts, pickers) are
  clearly above the page (level 3).
- **User messages** get a faint accent tint.
- Secondary buttons look gently raised; text fields look slightly recessed; the message
  composer stands out (level 2).
- In dark mode, where shadows barely show, raised things get a hair-thin lighter line on
  their top edge.
- Stats trend badges become soft filled pills; home cards lift to level 2 on hover.
- Typography: fixed-width digits across the UI, tighter/bolder titles, small uppercase
  sidebar section labels, no lonely last word in message paragraphs.

Delivered in **two commits**, each reviewed by the user in the browser before "commit":

1. **Commit 1 — depth** (sections 4 to 9).
2. **Commit 2 — typography** (section 10).

User decisions for this step (2026-09-26):

- Applies to **all three Web Awesome themes** (default, awesome, shoelace). Exception: the
  awesome theme's **controls** (buttons and text fields) keep their own hard shadows. Its
  button press effect (the button sliding into its shadow) depends on it, and its fields
  match its buttons.
- Typography rides along in this step.
- The **public share viewer** gets the same transcript look (message shadows, user tint,
  typography).
- Deliberately left out (user agreed): the mock's larger message line-height (changes
  chat density) and a bold active tab (widens the tab and shifts its neighbours).
- The user delegated the remaining detail calls ("je te fais confiance… on changera plus
  tard si besoin", reviewed visually before each commit). Two mock values are therefore
  deliberately not taken, each easy to revisit at the visual review:
  - the mock's message card radius (`0.875rem`): cards keep the theme's
    `--wa-panel-border-radius`, so each theme keeps its own radius;
  - the mock's assistant card on the raised surface (white in light): assistant cards
    keep today's `--assistant-card-base-color`. With the white chat card of step 1, a
    white assistant card would rely on its border alone; the user reads the chat all day
    and did not ask for that change. To offer as a try at the visual review.

## 3. Non-goals

- Glass / translucency, backdrop blur, dialog backdrop (step 3).
- Any animation or transition change (steps 4–5). Existing `transition`s stay as they are.
- Accent glow on primary (accent) buttons, focus halo, selected session in the sidebar
  (step 6).
- The panel cards of step 1: `--panel-shadow` and the shadow budget (horizontal reach
  ≤ 4px) stay unchanged. The layout overlay's stronger shadow is pinned inside that
  budget (§5.1) instead of following `--wa-shadow-l` to level 3.
- `kbd` keycaps: they keep their keycap bottom edge (WA native style, and the settings
  shortcut list's own rule). A keycap edge is the right metaphor; deliberately excluded.
- Native `<button>`/`<input>` elements (not `wa-*`): unchanged.
- Existing ad-hoc shadows not listed below (the command palette's sticky category
  headers, the changelog dialog's images, the share viewer's subagent drawer, terminal key bars, layout drop zones, the
  mobile drawer's `--wa-shadow-xl` which no theme defines): unchanged.

## 4. Tokens — new file `frontend/src/styles/depth.css`

Shared by the SPA, the share viewer and the artifact shell:

- `frontend/src/main.js`: right after `./styles/transcript-tokens.css` (before
  `./styles/surfaces.css`);
- `frontend/src/share-session/main.js`: right after `../styles/transcript-tokens.css`;
- `frontend/src/artifact-shell/main.js` (the dedicated artifact page's minimal shell):
  right after the Web Awesome theme import (`themes/default.css`). It mounts the same
  `ArtifactBrokerPrompt.vue` `<wa-dialog>` as the SPA's artifact preview ("one shell,
  both run contexts"), so the consent prompt looks the same in both (level 3 dialog; its
  buttons are default `accent` and stay flat, §7.1). The shell imports no other app stylesheet; `depth.css` has
  no dependency on one.

Same declaration pattern as `surfaces.css`: light values on `:root`, dark overrides on
`.wa-dark` (same specificity, later wins; both match `<html>`). Unlayered, so they beat
the theme's `@layer wa-theme` declarations.

Shadow color: the same cool near-black as `--panel-shadow` (`oklch(0.25 0.02 275 / a)`,
`oklch(0.2 0.02 275 / a)` for level 3) in light; pure black in dark.

```css
:root {
    /* Level 1: resting on a card (buttons, stat/home cards, question options). */
    --depth-1:
        0 1px 1px oklch(0.25 0.02 275 / 0.04),
        0 1px 3px oklch(0.25 0.02 275 / 0.07);
    /* Level 2: stands out (message composer, hovered home card). */
    --depth-2:
        0 1px 2px oklch(0.25 0.02 275 / 0.05),
        0 4px 12px -2px oklch(0.25 0.02 275 / 0.08),
        0 16px 32px -12px oklch(0.25 0.02 275 / 0.12);
    /* Level 3: floats above the page (menus, dialogs, popovers, toasts, pickers). */
    --depth-3:
        0 2px 4px oklch(0.2 0.02 275 / 0.06),
        0 12px 28px -4px oklch(0.2 0.02 275 / 0.16),
        0 32px 64px -16px oklch(0.2 0.02 275 / 0.24);
    /* Downward level 1 for chat cards and tool cards: nothing above, a negligible
       0.0625rem on the sides (its faintest edge), 0.1875rem below (--depth-card-reach). */
    --depth-card:
        0 0.0625rem 0.125rem -0.0625rem oklch(0.25 0.02 275 / 0.10),
        0 0.125rem 0.1875rem -0.125rem oklch(0.25 0.02 275 / 0.12);
    /* How far --depth-card reaches below its box. Keep in sync with --depth-card (both
       schemes use the same geometry). */
    --depth-card-reach: 0.1875rem;
    /* Light top edge of a raised button. Dark: a fainter one, added to the edge line
       --depth-1 already carries there. */
    --depth-highlight: inset 0 1px 0 oklch(1 0 0 / 0.6);
    /* Hair-thin light top line on raised things in dark mode; nothing in light. */
    --depth-edge: 0 0 transparent;
    /* Recessed text field. */
    --depth-inset: inset 0 1px 2px oklch(0.25 0.02 275 / 0.06);
}

.wa-dark {
    /* Inset layer first: keeps level 1 → level 2 hover transitions interpolable. */
    --depth-1:
        inset 0 1px 0 oklch(1 0 0 / 0.04),
        0 1px 2px oklch(0 0 0 / 0.35);
    --depth-2:
        inset 0 1px 0 oklch(1 0 0 / 0.05),
        0 1px 2px oklch(0 0 0 / 0.4),
        0 8px 24px -6px oklch(0 0 0 / 0.5);
    --depth-3:
        inset 0 1px 0 oklch(1 0 0 / 0.06),
        0 2px 6px oklch(0 0 0 / 0.45),
        0 24px 56px -12px oklch(0 0 0 / 0.65);
    --depth-card:
        0 0.0625rem 0.125rem -0.0625rem oklch(0 0 0 / 0.45),
        0 0.125rem 0.1875rem -0.125rem oklch(0 0 0 / 0.35);
    --depth-highlight: inset 0 1px 0 oklch(1 0 0 / 0.06);
    --depth-edge: inset 0 1px 0 oklch(1 0 0 / 0.04);
    --depth-inset: inset 0 1px 2px oklch(0 0 0 / 0.3);
}
```

Every token is a valid `box-shadow` list on its own (never `none`), so tokens can be
combined with commas (`var(--depth-card), var(--depth-edge)`).

Layer order matters for transitions: a `box-shadow` transition interpolates layer by
layer (the shorter list padded at its end) and snaps when a pair mixes inset and outer.
The home cards transition `box-shadow` from level 1 (rest) to level 2 (hover), so the
dark tokens put their inset layer first: [inset, outer] → [inset, outer, outer] pairs
cleanly. Inset and outer layers never overlap, so the order does not change the render.

`--depth-card` reach, from `offset-y + spread + blur` per layer: below 0.1875rem, sides
0.0625rem, above 0. Today's reserve under the last row of a chat card
(`--main-shadow-size + 1px`) is too small for it in the shoelace theme (≈1.1px) and, in
the default theme, at font sizes above 16px. §6.2 widens that one reserve.

### 4.1 Web Awesome shadow tokens

```css
:root,
.wa-invert {
    --wa-shadow-s: var(--depth-1);
    --wa-shadow-m: var(--depth-3);
    --wa-shadow-l: var(--depth-3);
}
```

- `m` maps to level 3, not 2: every WA consumer of `m` (dropdown panel, submenu, select
  listbox, color picker) is a floating layer. The app's own `m` users are re-pointed
  explicitly (§5, §9).
- `.wa-invert` is listed because every theme re-declares its `--wa-shadow-*` there (toasts,
  tooltips); inside it, `var(--depth-*)` resolves to the **page** scheme's values
  (inherited from `<html>`, `.wa-invert` does not re-declare them) — right for a shadow
  cast on the page.
- Placed in `depth.css` after the `.wa-dark` block. `var(--depth-1)` on `:root` resolves
  on `<html>`, where `.wa-dark` also applies, so dark values are picked up.
- Consequence, all themes: `wa-card` (home project/workspace cards, stats cards, any
  card) rests at level 1; menus, selects, dialogs, drawers, popovers at level 3. In the
  awesome theme this replaces its hard offset shadows on those components (user
  decision). The awesome theme's buttons and fields do not read these tokens (their
  `wa-theme-dimension` rules use the offset tokens directly), so they keep their hard
  shadows.
- App components already using `--wa-shadow-l` follow automatically to level 3:
  `ui/HoverInfoPanel.vue`, `app/SessionSwitcher.vue`, `message/CommandPickerPopup.vue`,
  `files/DirectoryPickerPopup.vue`, `message/MessageHistoryPickerPopup.vue`,
  `files/FilePickerPopup.vue`, `session/detail/TextSelectionComment.vue`.
  `session/layout/LayoutOverlay.vue` is the exception: it is a panel card and gets its
  own shadow (§5.1).

### 4.2 Specificity and layers of the global rules below

- Every global rule in `depth.css` that targets elements (§7) wraps its whole selector in
  `:where(...)` (specificity 0 plus the `::part()` pseudo-element), so any existing
  **unlayered** component rule that sets its own `box-shadow` on the same part keeps
  winning.
- Global rules only style `::part()` of `wa-*` elements from the document, which beats
  the component's shadow-internal styles regardless of specificity.
- Being unlayered, they also beat every **layered** Web Awesome rule on the same part,
  whatever `:where()` does. The only such rules that set `box-shadow` on the targeted
  parts are the awesome theme's button and field rules (§1); §7 excludes the awesome
  theme for exactly that reason.

## 5. Floating layers (commit 1)

- Via §4.1: every WA dropdown, submenu, select listbox, dialog, drawer, popover; every
  app popup listed in §4.1.
- **Toasts** (`App.vue`, `toastTheme` computed): add `'--nv-shadow': 'var(--depth-3)'`
  to the returned object, after the spread theme. Of the two Notivue themes used, only
  `lightTheme` (dark page) defines `--nv-shadow`; `slateTheme` (light page) has none, so
  light-page toasts have no shadow today. The theme object is applied as an inline style
  on each `.Notivue__notification` (Notivue's `Notification` and the app's
  `CustomNotification.vue`), inside the `.wa-invert` box; `var(--depth-3)` resolves there
  with the page scheme (§4.1).
- Chart tooltips (`app/UsageGraphDialog.vue` `.usage-chart-tooltip`,
  `activity/ContributionSparklines.vue` `.sparkline-tooltip`): `var(--wa-shadow-s)` →
  `var(--depth-2)` (small floating labels: level 3 would be heavy for their size).
- Header overflow panels that drop over the content on short viewports
  (`@media (max-height: 900px)`; `session/detail/SessionHeader.vue`
  `.session-collapsible-rows` and `project/ProjectDetailHeader.vue`
  `.detail-collapsible-rows`, `box-shadow: var(--wa-shadow-s)` on the
  absolutely-positioned rows, `left: 0; right: 0`): → `var(--panel-overlay-shadow)`
  (§5.1). The session one spans the whole width of `.session-view`, which clips at
  `--panel-gap`: its shadow must keep the step-1 horizontal budget (≤ 4px), and
  `--depth-2` would reach 10–20px sideways and be cut flat in the gap. The project one
  sits inside the `.project-detail-content` card, which clips at its own edge (so its
  side shadow is hidden either way); it takes the same token so both headers look alike.
  Both components are SPA-only, where `surfaces.css` is loaded.
- Share viewer header (`share-session/ShareSessionApp.vue`, `.share-header::before`,
  `var(--wa-shadow-m)`, which §4.1 would turn into level 3): → `var(--depth-2)`.

### 5.1 The layout overlay stays inside the panel shadow budget

`.layout-overlay` is a step-1 panel card. `.session-layout` clips at
`overflow-clip-margin: var(--panel-gap)`, and every panel shadow keeps a horizontal reach
≤ 4px (below the gap). Following `--wa-shadow-l` to level 3 would give it a 48px side
reach, cut flat in the gap. So:

- new token in `styles/surfaces.css` (SPA only, where the other panel tokens live),
  next to `--panel-shadow`:
  ```css
  :root {
      /* The layout overlay floats above the other cards: deeper than --panel-shadow, same
         horizontal budget (≤ 4px per layer: blur + spread). */
      --panel-overlay-shadow:
          0 2px 4px oklch(0.2 0.02 275 / 0.08),
          0 14px 18px -14px oklch(0.2 0.02 275 / 0.28);
  }
  .wa-dark {
      --panel-overlay-shadow:
          0 2px 4px oklch(0 0 0 / 0.45),
          0 14px 18px -14px oklch(0 0 0 / 0.7);
  }
  ```
  Horizontal reach: 4px (first layer: blur 4, spread 0), 4px (second: blur 18, spread
  −14). Below: 6px and 18px — the bottom edge of an overlay touching the layout's bottom
  is clipped at the gap, as today with `--wa-shadow-l`.
- `LayoutOverlay.vue`: `box-shadow: var(--wa-shadow-l, 0 10px 40px rgba(0, 0, 0, 0.35));`
  → `box-shadow: var(--panel-overlay-shadow);` and its comment keeps saying the stronger
  shadow overrides the card's.
- The `.wa-dark` block goes in the existing `.wa-dark` block of `surfaces.css`, the
  `:root` one in the existing `:root` block (the file's declaration-order note applies).

## 6. Chat messages (commit 1) — `components/session/detail/SessionItem.vue`, `styles/transcript-tokens.css`

### 6.1 User message

- **Tint**: in `styles/transcript-tokens.css`, `--user-card-base-color` changes:
  - light (`:root`): `color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, white)`;
  - dark (`.wa-dark`): `color-mix(in oklab, var(--wa-color-brand-fill-quiet) 55%, var(--wa-color-surface-raised))`.
  (`--base-user-assistant-card-color` and `--assistant-card-base-color` are unchanged.)
  The card background and border are still derived from it in `SessionItem.vue`
  (`--user-card-bg-color`, `--user-card-border-color` = background at `l / 1.05`), so the
  border becomes a slightly darker tint of the same accent.
- **Shadow**: `box-shadow: var(--depth-card), var(--depth-edge);` replaces the
  border-colored line.
- Margins unchanged (they keep reserving `--main-shadow-size`).

### 6.2 Assistant card (run of rows)

- `--assistant-card-default-shadow: var(--depth-card);` replaces the border-colored line
  (still applied to the last row only, through `--assistant-card-shadow`).
- The default `--assistant-card-shadow` (inner rows) changes from `none` to
  `0 0 transparent`, because the row's `box-shadow` becomes a list:
  `box-shadow: var(--assistant-card-shadow), var(--assistant-card-edge);`
  (`none` is not allowed inside a list).
- New `--assistant-card-edge`: `0 0 transparent` by default, `var(--depth-edge)` on the
  first row (the `.is-block-start` rule that sets the top radius/border). Result: in dark
  mode, the hair-thin light line sits on the card's top edge only.
- The last row's reserve becomes
  `margin-bottom: calc(max(var(--main-shadow-size), var(--depth-card-reach)) + 1px);`
  so the whole shadow fits in every theme and at every font size (the scroll container
  clips the last card of the list otherwise). Effect on spacing: none in the awesome
  theme (0.25rem > 0.1875rem); +0.0625rem (1px at 16px) in the default theme; about
  +0.18rem (≈2.9px at 16px) in the shoelace theme, under the last row of each assistant
  card only.
- Why downward-only: a side reach would be cut flat at the top of the last row (the rows
  above carry no shadow), which reads as a notch.

## 7. Controls (commit 1) — global rules in `depth.css`

### 7.1 Secondary buttons

```css
:where(
    :root:not(.wa-theme-awesome) wa-button:is([appearance*='outlined'], [appearance*='filled']):not(wa-button-group wa-button, .wa-invert wa-button, .session-item, .bookmark-item, .filters-toggle, .autoattach-button)
)::part(base) {
    box-shadow: var(--depth-1), var(--depth-highlight);
}
```

The `wa-button:is(…):not(…)` compound is written on one line on purpose: whitespace
between `:is(…)` and `:not(…)` would be a descendant combinator and target what is
**inside** the buttons. Outside the `:not()` arguments, the only combinator is the space
after `:root:not(.wa-theme-awesome)`; inside them, `wa-button-group wa-button` and
`.wa-invert wa-button` keep their descendant combinator (they need it).

- Matches `appearance="outlined"`, `"filled"` and `"filled-outlined"` (the attribute is
  reflected by `wa-button`, also when bound as a property).
- Not matched:
  - `accent` (the default appearance, solid fill) — step 6;
  - `plain` (icon/ghost buttons);
  - buttons inside a `wa-button-group` (joined buttons: a shadow per segment breaks the
    group);
  - buttons inside a `.wa-invert` box (toasts): the page-scheme highlight would draw a
    bright line on a button of the opposite scheme;
  - the sidebar rows that are `wa-button`s — `.session-item`
    (`session/list/SessionListItem.vue`) and `.bookmark-item`
    (`artifacts/ArtifactBookmarkList.vue`), outlined when active — the selected row is
    step 6;
  - the two toggles that switch between `plain` (off) and a framed appearance (on):
    the search filters toggle `.filters-toggle` (`app/SearchOverlay.vue`) and the
    terminal auto-attach toggle `.autoattach-button` (`terminal/TerminalPanel.vue`).
    Raised only when on would read backwards (an "on" toggle looks pressed, not lifted);
  - every button in the awesome theme (keeps its hard shadow).
- Resulting look: level 1 plus, in light, a thin white line on the top edge; in dark,
  level 1 (which already contains the edge line) plus a faint highlight.

### 7.2 Text fields (recessed)

```css
:where(:root:not(.wa-theme-awesome) :is(wa-input, wa-textarea))::part(base),
:where(:root:not(.wa-theme-awesome) wa-select)::part(combobox) {
    box-shadow: var(--depth-inset);
}
```

- The awesome theme is excluded: its own hard inset field shadow stays (§2, §4.2).
- WA draws the focus ring with `outline`, so the inset shadow does not interfere.
- No background change in dark mode (WA's `--wa-form-control-background-color` also
  drives checkboxes, radios and switches).

### 7.3 Message composer

In `components/message/MessageInput.vue` (scoped), the existing rule
`.message-input wa-textarea::part(textarea)` is left alone and a new rule is added:

```css
:root:not(.wa-theme-awesome) .message-input wa-textarea::part(base) {
    box-shadow: var(--depth-2);
}
```

Its specificity beats §7.2. The awesome theme is excluded like every field (§7.2).
Collapsed composer: the textarea is `display: none`, nothing to do.

Clipping: the composer sits in `.session-footer` (`SessionItemsList.vue`,
`overflow-y: auto`, so it clips on all four sides). The level-2 shadow reaches 6px above
in light and 10px in dark, and up to 20px sideways for its faintest light layer. The
footer cuts it 4px above the textarea (`.message-input`'s top padding, the focus-ring
room) and at `--wa-space-s` on the sides. At those lines the cut layers are faint
(estimated ≤ 3% alpha in dark, less in light). Accepted as is, and checked in the browser
(§12.4). If a flat top edge shows, the fallback is to drop the composer to `--depth-1`
(top reach 2px), with no layout change.

## 8. Tool cards and question widget (commit 1)

### 8.1 Tool cards (`wa-details.item-details`, `SessionItem.vue`)

- In the existing `wa-details.item-details` rule, add
  `&::part(base) { box-shadow: var(--depth-card); }` (downward-only, like chat cards:
  the next card of a joined run touches its top edge).
- No dark-mode top edge (`--depth-edge`) on tool cards, unlike the mock's `--sh-1`: in a
  joined run each card's top edge is the join with the card above, so a light line there
  would draw a seam across the run. Tool cards sit inside the assistant card, which
  already carries the top edge (§6.2).
- A tool card whose **bottom** border/radius is removed because the next card is joined
  to it gets `box-shadow: 0 0 transparent` on its `::part(base)`, with a selector at
  least as specific as the new rule above (`wa-details.item-details::part(base)`,
  specificity 0,1,2):
  - same row: a new rule `wa-details.item-details:has(+ wa-details)::part(base)`
    (0,1,3), next to the generic `wa-details:has(+wa-details)` rule, which is only
    (0,0,3) and would lose;
  - cross row: inside the existing rule in `.session-items`
    (`.virtual-scroller-item:has(wa-details.item-details:last-child)` →
    `wa-details.item-details:last-child`), which is more specific already.
  Result: only the last card of a joined run casts a shadow.
- Other `wa-details` (not `.item-details`, e.g. in dialogs) are unchanged.

### 8.2 Question widget options

- `items/claude_code/PendingRequestBody.vue`: both `box-shadow: var(--wa-shadow-offset-x-s)
  var(--wa-shadow-offset-y-s) 0 0 var(--border-color);` (on `.permission-suggestion-card`
  and on `.option-card`) → `box-shadow: var(--depth-1);`
- `items/codex/RequestUserInputBody.vue`: same replacement on `.option-card`.
- The question block itself is not a card (it is a section of the footer under a
  divider): no shadow on it.

## 9. Home and stats (commit 1)

- `project/ProjectCard.vue` and `workspace/WorkspaceCard.vue`, `:hover`:
  `box-shadow: var(--wa-shadow-m)` → `var(--depth-2)` (the `translateY(-2px)` lift and
  the `transition` stay). At rest they are `wa-card`s: level 1 through §4.1.
- `activity/ActivityDashboard.vue`:
  - the six `<wa-tag … appearance="outlined">` (trend badges and "N/A" badges) →
    `appearance="filled"` plus the `pill` attribute (soft filled pill in the badge's
    variant color; `wa-tag` is only rounded with `pill`);
  - stats cards lift to level 2 on hover (roadmap §8.3; no translate, the cards are not
    clickable): in the top-level scoped `wa-card { min-width: 16rem; }` rule (not the
    two `wa-card` rules inside `@container project-detail` blocks), add
    `&:hover { box-shadow: var(--depth-2); }`. At rest they are level 1 (§4.1).

## 10. Typography (commit 2)

- **Fixed-width digits** everywhere except prose:
  - `styles/transcript-tokens.css` (shared by SPA and share viewer): `body {
    font-variant-numeric: tabular-nums; }`;
  - `components/ui/MarkdownContent.vue`, in its existing **global** `<style>` block (the
    file has no scoped block; a scoped rule would never reach the `v-html` content), on
    `.markdown-body`: `font-variant-numeric: normal;` (message text — user and
    assistant —, plans, previews stay proportional).
- **Titles** (slightly tighter and bolder; `650` renders as 650 with a variable font and
  as the nearest bold weight otherwise):
  - `session/detail/SessionHeader.vue` `.session-title h2` (today 600): `font-weight:
    650; letter-spacing: -0.015em;`
  - `project/ProjectDetailHeader.vue` `.detail-title`: same;
  - `views/HomeView.vue` `h1`: already `font-weight: 700` (kept); add
    `letter-spacing: -0.02em;` only.
- **Sidebar section labels** (`sidebar/SidebarListSeparator.vue` `.sls-label`):
  `font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.07em;` (weight
  already semibold). Covers every use of the component: the session and artifact lists
  ("Pinned", "Last 24 hours", "Older than …") and the section separators of the peer
  message review dialog (`peer/PeerMessageReviewDialog.vue`) — the same look everywhere.
- **No lonely last word** in message paragraphs: `MarkdownContent.vue`, same global
  block, `.markdown-body :is(p, li) { text-wrap: pretty; }`.

## 11. Invariants

- No `transform`, `filter`, `backdrop-filter`, `contain`, `will-change` or
  `container-type` added to `.main-content`'s branches, `.session-layout`,
  `.center-slot`, `.dock-region`, `.layout-overlay` (roadmap §6.2).
- `.main-content` stays opaque (roadmap §6.2).
- `--panel-shadow` and the step-1 shadow budget unchanged.
- Chat spacing unchanged except the reserve under the last row of an assistant card
  (§6.2): `--main-shadow-size` and every other margin derived from it keep their values.
- Light canvas unchanged.
- Everything derives from tokens: accent color, scheme, theme and font size keep
  working; sizes that must follow the font size are in `rem`.

## 12. Testing

No JavaScript logic changes. One `node:test` file (`frontend/src/styles/depth.test.js`)
reads the stylesheets as text and pins the token invariants: every token is a valid list
without `none`, the dark inset-first layer order, `--depth-card`'s reach against
`--depth-card-reach`, the ≤ 4px horizontal budget of the panel shadows, the WA mapping,
and the three imports. The whole suite stays green (`cd frontend && npm test`).

Browser checks on the worktree instance (http://localhost:5174), each in light and
dark, default theme, font size 16, then the listed extras. Where "unchanged" or a
spacing delta is claimed, the baseline (screenshots, measured gaps) is taken **on the
same worktree instance before the change**: the main instance (5173) runs `main`, which
lacks step 1, so its chrome differs.

1. Chat: user card tinted with the accent; no solid 2px line under any card; soft shadow
   under the user card and under the last row of assistant cards only; in dark, a light
   top line on user cards and on the first row of assistant cards; spacing between cards
   identical to the baseline except +1px under assistant cards (§6.2); the last card of the list
   keeps its whole shadow (scrolled to the bottom).
2. Tool cards: a joined run **inside one row** (two tool calls in one assistant item)
   and a joined run **across rows** (consecutive tool items) each cast one shadow, under
   their last card.
3. Buttons: an outlined button in a dialog footer is raised with a top highlight; icon
   buttons, the default (accent) buttons, the sidebar session and artifact rows (active
   one included), button groups, toast buttons, the search filters toggle and the
   terminal auto-attach toggle are flat.
4. Fields: inputs, textareas and selects recessed; focus ring unchanged; composer raised
   (level 2), with no visible flat edge where the footer cuts its shadow (above it and on
   its sides), in light and in dark.
5. Floating layers: a dropdown menu, a select listbox, a dialog, a toast (light and dark
   page), the session switcher, the file picker (`@` in the composer) — all at level 3.
6. Layout overlay (a tab opened as an overlay, e.g. Artifacts): deeper shadow than the
   docks, no shadow cut flat in the gaps at its left and right sides. Bottom edge: a side
   overlay with no bottom rail reaches the layout's bottom, and its deeper layer is cut
   at the gap — expected, as today (§5.1); a bottom overlay (or a side overlay while a
   bottom rail shows) ends above the rail, so no cut.
7. Question widget (a pending permission request): options at level 1.
8. Home: cards level 1 at rest, level 2 on hover, the shadow fading smoothly in and out
   (light and dark). Stats: cards level 2 on hover, trend and N/A badges filled pills.
9. Awesome theme: buttons keep their hard shadow and press effect, fields keep their
   hard inset look, the composer keeps the awesome field look; menus, cards and
   messages soft.
10. Shoelace theme, then default theme at font sizes 12, 24 and 32: the last message
    card of the list keeps its whole shadow at the bottom.
11. Share viewer: the worktree instance cannot serve it (shares are served only on the
    dedicated share host, which points to the main instance). Check instead that
    `cd frontend && npm run build` succeeds and that the built share bundle's CSS
    (`src/twicc/static/share-session/share-session.css`, per
    `frontend/vite.config.share.js`) contains: the `--depth-card` and
    `--depth-card-reach` declarations; a `--user-card-base-color` declaration that
    references `--wa-color-brand-fill-quiet` (match on the variable names, not on
    literal values the minifier may rewrite, e.g. `white` → `#fff`); a
    `share-header` rule whose `box-shadow` references `--depth-2` (the minifier writes
    `.share-header:before`, one colon: match on `share-header` + `--depth-2`). The viewer
    renders the same `SessionItem.vue`, so the chat look follows. Same build: the
    artifact shell's `src/twicc/static/artifact-shell/shell.css` (per
    `frontend/vite.config.shell.js`, git-ignored) contains the `--depth-3` declaration
    and the `--wa-shadow-l` mapping to it.
12. Chart tooltips (usage graph dialog, project activity sparklines): level 2.
13. Short viewport (browser window height ≤ 900px, so the header rows collapse into an
    overflow panel): open the session header's and the project header's overflow panel;
    deeper shadow below, no shadow cut flat in the gaps at their sides.
14. Dark mode, tool cards: a joined run shows no light seam at the joins; the run casts
    one shadow under its last card.
15. Peer message review dialog: its section separators show the new label style
    (commit 2).
16. Commit 2: numbers in the sidebar/header/stats do not change width while updating;
    dense numbers (sidebar session costs, header and stats badges) still fit — no new
    truncation, wrap or overflow compared with the baseline; message text keeps
    proportional digits; titles tighter; sidebar labels uppercase; long paragraphs avoid
    a single last word.

## 13. Risks

- **Specificity of global rules**: mitigated by `:where()` (§4.2); a component that sets
  its own `box-shadow` on a WA part keeps it. Layered theme rules always lose; only the
  awesome theme's controls are concerned and they are excluded.
- **Outlined buttons used as rows or toggles elsewhere** would be raised. Found and
  excluded: the two sidebar lists (`.session-item`, `.bookmark-item`) and the two
  plain↔framed toggles (`.filters-toggle`, `.autoattach-button`). Toggles that switch
  between `filled` and `outlined` outside a button group (the `FilePane` preview
  toggles) stay raised in both states, which is consistent; the `OrchestrationPanel`
  view switch is a `wa-button-group`, so it stays flat in both states.
- **Level 3 in the awesome theme** changes that theme's character on menus and dialogs —
  accepted by the user.
- **Tabular digits** slightly widen some numbers in dense places (sidebar costs,
  badges); checked in §12.16.

## 14. Amendments from the user's visual review (2026-09-27)

Decided by the user while reviewing commit 1 in the browser. They override the sections
above where they differ. Fine-tuning of every detail is planned for a global pass once all
steps are done.

- **Buttons (§7.1)**:
  - raised buttons use a dedicated `--depth-button` token (light `0 1px 2px /.08, 0 2px 4px
    -1px /.10`; dark inset edge first, then black layers) — level 1 barely showed on a
    small control;
  - solid `accent` buttons are raised too (they were left to step 6), including the solid
    split buttons of a `wa-button-group`; their top light is a translucent white line
    (`inset 0 1px 0 oklch(1 0 0 / 0.2)`), which reads as a lighter step of whatever color the
    button actually has; outlined/filled buttons keep `--depth-highlight`;
  - new exclusion: `.layout-winbtn` (the maximize toggle, plain ↔ accent);
  - split buttons (solid button + menu arrow: New session, Approve ×2, Submit answers,
    terminal snippet Send): Web Awesome's 1px see-through gap between solid segments is
    removed; the arrow carries a `1px oklch(1 0 0 / 0.55)` separator (replaces the New
    session-only rule of `ProjectView.vue`).
- **User messages (§6.1)**: tinted blocks (quotes, `:::` containers, `::` lines) inside a
  user message start their depth alternation on the plain surface, the next level taking
  the tint (`SessionItem.vue`, overriding `--md-tint-fill` / `--md-tint-fill-alt`).
- **Assistant cards (§2, §6.2)**: in light, they sit on `--wa-color-surface-raised` (white),
  border and soft shadow kept; dark unchanged.
- **Sidebar chrome** (new):
  - the sidebar's own buttons (view switch, session/artifact list options, advanced search,
    sidebar toggle, command palette, inbox, settings) switch from neutral `filled-outlined`
    to brand `outlined`, like the back-home button;
  - the project selector and the sidebar filter inputs are painted with the canvas
    (`--canvas-background` + `background-attachment: fixed`): tinted like the sidebar, still
    opaque when they widen over their neighbours;
  - sidebar separators (its own `wa-divider`s outside menus and quota tooltips, the quota
    rows' borders, the list section lines) use `--sidebar-divider-color`, a 50/50 mix of
    `--wa-color-brand-border-normal` and `--wa-color-brand-border-loud`;
    `SidebarListSeparator` falls back to the surface border outside the sidebar.
- **Home page**: the floating Inbox and Settings buttons are solid `accent` there (opaque
  over scrolling content) through new props (`PeerInboxButton` `appearance`,
  `SettingsPopover` `triggerAppearance`, both defaulting to `outlined`).
- Remaining neutral `filled-outlined` buttons (Git folder and refresh, file-tree options,
  inside the Files/Git cards) are left as they are for now.
