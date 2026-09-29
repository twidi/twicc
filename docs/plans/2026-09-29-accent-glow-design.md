# Accent glow — design (visual refresh, step 6a)

## 1. Context

Step 6 of the "Signature" visual refresh is split in two sub-steps: **6a (this document)**,
the accent glow on static controls, and 6b, the live states (working line, unread eye,
pulsing context ring, pending question border), specified later. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox parity**,
**reduced motion is reduced, not none**), §8.8 (what the user sees) and §9 "Step 6" (mock
values).

Mock reference (`mock.css`, `fx-glow` block, roadmap §3). The rules ported here:

```css
.mock.fx-glow .btn.primary {
    background: linear-gradient(180deg, oklch(0.63 0.115 var(--h)), oklch(0.52 0.105 var(--h)));
    box-shadow: inset 0 1px 0 oklch(1 0 0 / .28), 0 1px 2px oklch(0.3 0.08 var(--h) / .3),
        0 6px 18px -6px oklch(0.55 0.13 var(--h) / .6); }
.mock.fx-glow .btn.primary:hover { filter: brightness(1.07) saturate(1.1); box-shadow: …
        0 10px 26px -6px oklch(0.6 0.15 var(--h) / .7); }
.mock.fx-glow .btn.primary::after { background: linear-gradient(105deg, transparent 35%,
        oklch(1 0 0 / .35) 50%, transparent 65%); transform: translateX(-120%); }
.mock.fx-glow .btn.primary:hover::after { transform: translateX(120%);
        transition: transform 700ms var(--ease-out); }
.mock.fx-glow :is(.input, .composer .box):focus-within { border-color: var(--b-60);
    box-shadow: 0 0 0 .25rem oklch(0.67 0.12 var(--h) / .2), 0 0 1.5rem -0.25rem oklch(0.67 0.13 var(--h) / .35); }
.mock.fx-glow .session-item.selected {
    background: linear-gradient(100deg, var(--b-fill-normal), color-mix(in oklab, var(--b-fill-quiet) 70%, transparent));
    box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--b-60) 45%, transparent), 0 2px 10px -4px oklch(0.6 0.13 var(--h) / .45);
    color: var(--b-on-quiet); }
.mock.fx-glow[data-scheme="dark"] .session-item.selected {
    background: linear-gradient(100deg, oklch(0.32 0.07 var(--h)), oklch(0.25 0.05 var(--h) / .6));
    box-shadow: inset 0 0 0 1px oklch(0.6 0.11 var(--h) / .45), 0 2px 12px -4px oklch(0.6 0.13 var(--h) / .5);
    color: oklch(0.88 0.08 var(--h)); }
.mock.fx-glow .ink { background: linear-gradient(90deg, var(--b-60), oklch(0.75 0.13 calc(var(--h) + 50)));
    box-shadow: 0 0 .625rem oklch(0.67 0.13 var(--h) / .7); height: .1875rem; }
.mock.fx-glow .ring svg { filter: drop-shadow(0 0 .25rem color-mix(in oklab, var(--c-danger) 60%, transparent)); }
.mock.fx-glow :is(.usage .bar > i, .progress i) { background: linear-gradient(90deg, oklch(0.7 0.14 160), oklch(0.72 0.15 135));
    box-shadow: 0 0 .375rem oklch(0.72 0.15 150 / .5); }
.mock.fx-glow .switch.on .track { background: linear-gradient(90deg, oklch(0.6 0.11 var(--h)), oklch(0.68 0.13 calc(var(--h) + 30)));
    box-shadow: 0 0 .75rem -0.125rem oklch(0.67 0.13 var(--h) / .7); }
.mock.fx-glow .seg .seg-ink { box-shadow: 0 0 0 1px var(--b-border-quiet), 0 2px 8px -2px oklch(0.6 0.12 var(--h) / .45); }
.mock.fx-glow .snav .snav-ink { background: linear-gradient(100deg, var(--b-fill-normal), var(--b-fill-quiet));
    box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--b-60) 35%, transparent); }
```

The mock has a hue variable `--h`. The app has none: the accent is Web Awesome's
`wa-brand-cyan` class (`index.html:2`). Every mock `oklch(… var(--h))` is rewritten from the
brand tokens: `color-mix()` for alphas, CSS relative colors (`oklch(from var(--wa-color-brand-60) l c calc(h + 50))`)
for hue shifts. Relative colors already ship in `styles/surfaces.css:15` and `:57`, so
Firefox supports them.

### 1.1 User decisions — do not reopen (2026-09-29)

- **Split:** 6a = accent glow, 6b = live states. Two specs, two commits.
- **Focus halo everywhere, buttons included.** The soft halo replaces the hard 3px outline
  on every control that shows a focus ring. If it is too much in use, the user removes it
  from some controls later.
- For 6b (recorded here, not built here): the unread **eye icon stays** and breathes; the
  "working" line becomes a small pill with a light running around its border, a
  shimmering label and three bouncing dots.

## 2. Today (checked)

### 2.1 Solid brand buttons

- About 76 `wa-button`s are solid brand buttons (`variant="brand"` with the default
  `appearance="accent"`). 19 buttons bind `:variant`; one of them can be a brand solid
  button besides the excluded window buttons (`.layout-winbtn`): the per-block detail
  toggle at the corner of a transcript card (`components/session/detail/SessionItem.vue:168-176`,
  `.detail-toggle`, `:variant="isBlockDetailed ? 'brand' : 'neutral'"`, default
  appearance), a small floating control (`scale: 0.6` at `:361-373`, `opacity: 0.5` at `:374-383`), also
  rendered by the share viewer. Two static brand buttons bind `:appearance` and are solid
  on the Home page: the Inbox button (`PeerInboxButton.vue:37`, `HomeView.vue:160`) and the
  Settings button (`SettingsPopover.vue:1267`, `trigger-appearance="accent"` at
  `HomeView.vue:161`), the pair of floating Home buttons. Examples of solid brand buttons:
  the composer's
  Send button (`components/message/MessageInput.vue:2193`), New session
  (`views/ProjectView.vue:2117-2135`, a `wa-button-group` with its menu arrow; all-projects
  variant `:2263-2273`), the Create / Save button of about 25 dialogs
  (`components/project/ProjectEditDialog.vue:1215`, `SessionRenameDialog.vue:371`, …).
- No rule targets the brand variant. Every solid button gets
  `box-shadow: var(--depth-button), inset 0 1px 0 oklch(1 0 0 / 0.2)`
  (`styles/depth.css:106-110`, wrapped in `:where()`). Solid buttons outside a
  `wa-button-group` also get the press scale (`styles/motion.css:88-104`; group segments,
  such as the single-project New session and its arrow, are excluded at `:98`).
- Web Awesome paints the solid button with `background-color` on `.button` (part `base`):
  `var(--wa-color-fill-loud)`, mixed with `--wa-color-mix-hover` (black 10%) on hover and
  `--wa-color-mix-active` on press (`webawesome/dist/chunks/chunk.4FOHUBBS.js`, block
  `:host([appearance='accent'])`). `.button` has no `position`. `variant` and `appearance`
  are reflected attributes.
- A pseudo-element after `::part()` works in Firefox: `components/ui/TabBar.vue` already
  draws the gliding tab line with `.tab-bar::part(tabs)::after` (step 4c, checked in
  Firefox).

### 2.2 Focus ring

- The app does not redefine the focus ring. Web Awesome's theme
  (`webawesome/dist/styles/themes/default.css:175-264`, `@layer wa-theme`, on
  `:where(:root), .wa-theme-default, .wa-light, .wa-dark, .wa-invert`) declares
  `--wa-focus-ring-style: solid`, `--wa-focus-ring-width: 0.1875rem`,
  `--wa-focus-ring: var(--wa-focus-ring-style) var(--wa-focus-ring-width) var(--wa-color-focus)`,
  `--wa-focus-ring-offset: 0.0625rem`. `--wa-color-focus` is `var(--wa-color-brand-60)`.
- Every Web Awesome control draws `outline: var(--wa-focus-ring)` +
  `outline-offset: var(--wa-focus-ring-offset)` on focus (buttons on `.button:focus-visible`,
  fields on `:focus-within`, the switch on its thumb; icon-only buttons use `outline-offset: 2px`, `.button.is-icon-button`). 12 app files reuse the token
  (`grep -rln "outline:.*var(--wa-focus-ring)"`), for example the keyboard-highlighted
  session row (`SessionListItem.vue:722-725`) and `styles/codemirror-search.css:108/165`.
- Two layouts reserve room for the ring from the tokens:
  `MessageInput.vue:2281` (`padding-top: calc(var(--wa-focus-ring-width) + var(--wa-focus-ring-offset))`)
  and `components/session/detail/ChatNavToolbar.vue:102`.
- Four app rules draw their own hard outline instead of the token:
  `components/session/detail/items/claude_code/PendingRequestBody.vue:1418-1421` and
  `:1521-1524`, `components/session/detail/items/codex/RequestUserInputBody.vue:485-488`
  and `:495-498` — all `outline: 2px solid var(--wa-color-brand-fill-loud); outline-offset: 2px`.
- Fields are recessed: `:where(:is(wa-input, wa-textarea))::part(base), :where(wa-select)::part(combobox)`
  get `box-shadow: var(--depth-inset)` (`depth.css:124-127`). The composer overrides it
  with `.message-input wa-textarea::part(base) { box-shadow: var(--depth-2); }`
  (`MessageInput.vue:2344-2346`); its footer is a scroll container that cuts anything
  more than 4px above the box.

### 2.3 Selected session row

- `components/session/list/SessionListItem.vue:443-455`: each row is a
  `wa-button.session-item`; the active row is `appearance="outlined" variant="brand"` with
  the class `session-item--active`. No custom CSS for the active row: it is Web Awesome's
  outlined brand button. The title `.session-name` is already `font-weight: 700`
  (`:771-780`). No dark-mode rule.
- The multi-select selected row (`.session-item-wrapper--selected`, `:728-731`) uses
  `brand-fill-quiet` + an inset `brand-border-quiet` ring. It must stay distinct from the
  active row.
- `depth.css:90-102` excludes `.session-item` from the button depth ("the selected row is
  step 6"); `motion.css` excludes it from the press scale.

### 2.4 Tab line

`components/ui/TabBar.vue:199-224`: `.tab-bar::part(tabs)::after` is sized and placed on
the active tab by `--glide-w/h/x/y` and draws
`border-block-end: var(--safe-track-width) solid var(--indicator-color)` (Web Awesome's
`--indicator-color: var(--wa-color-brand-fill-loud)`). `.tab-bar` sets
`--track-width: var(--divider-size)`.

### 2.5 Context ring

- `wa-progress-ring.context-usage-ring` in `components/session/detail/SessionHeader.vue:719-727`
  (compact duplicate) and `:818-826`; `.onode-context-ring` in
  `components/orchestration/AgentTreeNode.vue:176-184` and `OrchestrationNode.vue:275-283`.
  Parts: `base`, `track`, `indicator`, `label`.
- The indicator colour is set inline as `--indicator-color` from `contextUsageColor`
  (`SessionHeader.vue:143-150`, `AgentTreeNode.vue:101-107`, `OrchestrationNode.vue:194-200`):
  danger above 70%, warning above 50%, else `'var(--wa-color-primary)'`.
- **Bug, measured (Chrome, worktree instance, 2026-09-29):** `--wa-color-primary` does not
  exist in Web Awesome 3. At 50% or less the indicator's computed `stroke` is `none`: the
  ring shows only its track. A ring without the inline colour computes
  `rgb(7, 128, 152)` (`--wa-color-brand-fill-loud`).

### 2.6 Usage bars

`views/ProjectView.vue:2416-2520`: the quota bars in the sidebar footer are divs.
`.usage-lane-fill` gets its colour inline as `background: <color>` at `:2419`, `:2467`
and `:2515` (5h, 7d, extra usage); `.usage-lane-time` (`:2422`, `:2470`) is the neutral
time lane. The colour strings come from `getUsageRingColor` (`utils/usage.js:40-47`) and
`quotaExtraUsageRingColor` (`ProjectView.vue:272-285`): `var(--wa-color-neutral)`,
`var(--wa-color-success)`, `var(--wa-color-warning)` or `var(--wa-color-danger)`. CSS at
`:3398-3500`.

### 2.7 Switches

73 `wa-switch`es, no global styling. Web Awesome paints the track (part `control`) with
`--wa-form-control-activated-color` when checked (`.checked .switch`). The checked state
is the custom state `:state(checked)` (`chunk.RIOPXPRS.js:111`); the `checked` attribute is
only the default value.

### 2.8 Gliding inks

- `components/ui/SegmentedControl.vue:67-127`: `span.glide-ink` with
  `--glide-ink-bg: var(--wa-color-brand-fill-normal)`.
- `components/app/SettingsPopover.vue:2346-2387`: `span.glide-ink.settings-nav-ink` with
  `--glide-ink-bg: var(--glass-item-hover)`; the active item text is `--wa-color-brand`.
- `.glide-ink` itself is in `styles/motion.css:154-176`.

## 3. Goals

1. Solid brand buttons look lit: a light-to-dark vertical shading, a coloured soft
   shadow, a brighter look and a light sheen sweeping across on hover.
2. Focus shows as a soft accent halo everywhere, with no layout shift.
3. The selected session row is clearly lit: accent-tinted fill fading to the right, thin
   accent ring, soft accent shadow. No left bar.
4. The active-tab line is a thicker accent gradient that glows.
5. The context ring shows at every percentage and glows in its colour.
6. The quota bars, the checked switches and the two gliding inks get gradients and glows.
7. Same result in Firefox and Chrome; reduced motion removes only the sheen sweep.

Out of scope (step 6b): working line, unread eye, ring pulse, pending question border.
Out of scope (step 7): question option cards, sparklines, stats numbers, home cards, logo.
Not ported: the mock's `.icon-btn.brand` (no matching control in the app) and the range
thumb halo (covered by the focus halo).

## 4. File and tokens — `styles/glow.css` (new)

- New stylesheet `frontend/src/styles/glow.css`, unlayered (beats `@layer wa-theme`), no
  `!important`. Header comment in the style of `depth.css` / `motion.css`: step, design
  path, shared by the three bundles.
- Imported **right after `motion.css`** in the three entry files: `main.js`,
  `share-session/main.js`, `artifact-shell/main.js`, with a one-line comment like its
  neighbours (`main.js`: "Accent glow tokens and rules (also imported by the share bundle
  and the artifact shell)."). Nothing in it may depend on `surfaces.css` (SPA only).
- Global rules are wrapped in `:where()` (specificity 0 + `::part`), like `depth.css`, so
  a component rule on the same part wins.

Tokens, on `:root, .wa-invert` (the theme re-declares brand colours on `.wa-invert`):

```css
:root,
.wa-invert {
    /* Accent at mid lightness, for glows (both schemes). */
    --glow-accent: var(--wa-color-brand-60);
    /* Second colour of accent gradients: the accent hue turned by +50°, a touch lighter. */
    --glow-accent-shifted: oklch(from var(--wa-color-brand-60) calc(l + 0.08) c calc(h + 50));
    /* Coloured shadow of a solid brand button, at rest and on hover. */
    --glow-button: 0 6px 18px -6px color-mix(in oklab, var(--glow-accent) 60%, transparent);
    --glow-button-hover: 0 10px 26px -6px color-mix(in oklab, var(--glow-accent) 70%, transparent);
    /* Light-to-dark shading laid over a solid button's own colour. */
    --glow-button-shading: linear-gradient(180deg, oklch(1 0 0 / 0.14), oklch(0 0 0 / 0.1));
}
```

`--glow-accent-shifted` gives the tab line the mock's second colour (`+50°`); the switch
uses its own `+30°` shift inline (§10). Values are starting points: the user tunes them
in the browser review.

## 5. Solid brand buttons — `styles/glow.css`

Target: `wa-button[variant='brand'][appearance='accent']`, not inside `.wa-invert`, not
`.layout-winbtn` (same exclusions as `depth.css:106-110`), not `.dock-winbtn` (the dock's
"Restore" window button when a dock is maximized, `components/session/layout/DockRegion.vue:131-141`,
`variant="brand" appearance="accent"`: the same kind of window control as `.layout-winbtn`,
in a `.dock-topbar` with `overflow: hidden` that would cut its glow), not `.detail-toggle` (the
transcript's small floating corner toggle, §2.1: a glow reaching 18px over the card and a
sheen do not suit it; it keeps the step-2 look), and **not `[disabled]` nor
`.fake-disabled`**: a disabled button (Send while the composer is empty,
`MessageInput.vue:2197`) and the "fake disabled" New session button
(`ProjectView.vue:3118-3137`: dimmed, `pointer-events: none` on its part only, so its
host still matches `:hover`) keep the step-2 look, with no glow, no hover and no sheen.
Hover and sheen also skip `[loading]`. One-line compounds (a line break before `:not(` is
a descendant combinator — `depth.css` comment).

Web Awesome darkens a solid button on hover and press:
`background-color: color-mix(in oklab, var(--wa-color-fill-loud), var(--wa-color-mix-hover))`
(`chunk.4FOHUBBS.js`, `:host([appearance='accent'])` block; `--wa-color-mix-hover` is
`black 10%` in light, `black 8%` in dark, `default.css:36` / `:120`). A `brightness(1.07)`
filter over a 10% darker fill is still darker than the rest state. So the hover rule puts
the rest colour back (`var(--wa-color-fill-loud)`, set on the host by
`:host([variant='brand'])`, `chunk.XNTP7DEQ.js:20-22`, and inherited by the part), and a
press rule restores the press mix, which the outer hover rule would otherwise hide.

```css
/* Lit: shading over Web Awesome's own background-color, drawn from the border edge (the
   1px transparent border would otherwise repeat the gradient's far end in it), the depth
   shadow plus a brighter top light and a coloured glow. */
:where(wa-button[variant='brand'][appearance='accent']:not(.wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled))::part(base) {
    position: relative;
    isolation: isolate;
    background-image: var(--glow-button-shading);
    background-origin: border-box;
    box-shadow: var(--depth-button), inset 0 1px 0 oklch(1 0 0 / 0.28), var(--glow-button);
}
/* Hover: the rest colour (not Web Awesome's darker mix), brightened, a larger glow. */
@media (hover: hover) {
    :where(wa-button[variant='brand'][appearance='accent']:not(.wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled, [loading]):hover)::part(base) {
        background-color: var(--wa-color-fill-loud);
        filter: brightness(1.07) saturate(1.1);
        box-shadow: var(--depth-button), inset 0 1px 0 oklch(1 0 0 / 0.3), var(--glow-button-hover);
    }
}
/* Press: Web Awesome's press mix again, without the brightening. After the hover block
   (same specificity), so it wins while both match. */
:where(wa-button[variant='brand'][appearance='accent']:not(.wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled, [loading]):active)::part(base) {
    background-color: color-mix(in oklab, var(--wa-color-fill-loud), var(--wa-color-mix-active));
    filter: none;
}
```

- `background-origin` is set as a longhand: the `background` shorthand would reset Web
  Awesome's `background-color`.
- `position: relative` is the containing block of the sheen (below); `isolation: isolate`
  makes `.button` a stacking context, so the sheen's `z-index: -1` paints it above the
  button's own background and below its content (label, icons, badge). A badge absolutely
  positioned in a lit button then positions against `.button`'s padding box instead of
  the host: it moves 1px inward (the border). The one lit button with a badge is the
  Home page's Inbox button (`HomeView.vue:160`, `<PeerInboxButton appearance="accent" />`,
  badge in `PeerInboxBadge.vue`, `translate: 30% -30%` from the corner): the 1px shift is
  accepted; the badge stays above the sheen; the hover `filter` brightens the badge with
  the button (accepted, browser check 1). The Home Settings button next to it is lit too
  (the pair stays alike; its gear keeps its hover rotation above the sheen); it holds a
  `PeerInboxBadge` too, hidden on Home (`.settings-trigger-badge` shows only under
  `@container sidebar (width <= 9rem)`, `SettingsPopover.vue:3121-3128`).
- `filter` joins the part's transitions, in `motion.css`:
  - the rest rule `:where(wa-button)::part(base)` (`motion.css:88-96`) appends `filter` to
    `transition-property`, `var(--wa-transition-fast)` to `transition-duration` and
    `var(--wa-transition-easing)` to `transition-timing-function` (7 entries each);
  - the press rule (`:98-104`) declares only `transition-duration`: it appends a 7th
    duration, `var(--wa-transition-fast)` (a duration list shorter than the property list
    would be reused cyclically and give `filter` the first entry by accident — it is the
    same value today, the explicit entry keeps it deliberate).
  - The internal parts rules (`:108-128`, dialog "×", tab scroll arrows, tag "×") do not
    change.
  - The comment above the rest rule (`motion.css:83-84`, "repeats Web Awesome's own list
    … and adds scale") becomes "… and adds scale, and filter for the lit brand buttons of
    glow.css".
- The depth rule for solid buttons (`depth.css:106-110`) stays for every other solid
  button, disabled brand buttons included. Both rules have specificity 0 + the part;
  `glow.css` is imported after `depth.css`, so source order makes the glow rule win for
  the lit buttons.
- Split buttons: the menu arrow of New session is also a solid brand button; it gets the
  same look. `background-origin: border-box` puts the shading under its translucent left
  separator (`depth.css:119-121`) too.

### 5.1 Sheen

```css
/* A light band parked off the left edge; on hover it sweeps to the right in 700ms. It
   moves inside the button's own box (background-position), so nothing is clipped. Back
   off hover, it returns at once (no reverse sweep). */
:where(wa-button[variant='brand'][appearance='accent']:not(.wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled, [loading]))::part(base)::after {
    content: '';
    position: absolute;
    inset: 0;
    z-index: -1;
    border-radius: inherit;
    pointer-events: none;
    background: linear-gradient(105deg, transparent 35%, oklch(1 0 0 / 0.3) 50%, transparent 65%)
        no-repeat 100% 0 / 400% 100%;
}
@media (hover: hover) {
    :where(wa-button[variant='brand'][appearance='accent']:not(.wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled, [loading]):hover)::part(base)::after {
        background-position: 0% 0;
        transition: background-position 700ms var(--motion-ease-out);
    }
}
@media (prefers-reduced-motion: reduce) {
    :where(wa-button[variant='brand'][appearance='accent'])::part(base)::after {
        display: none;
    }
}
```

- `z-index: -1` needs the stacking context of `isolation: isolate` on the part (rest
  rule): without it, the band would go behind the button's background, out of sight.
- Web Awesome's `.button` has no `::before` / `::after` of its own (only
  `::-moz-focus-inner`), so the pseudo-element is free.
- Geometry, for a button W wide and H high: with `background-size: 400% 100%` the
  gradient box is 4W wide; position 100% puts its center (the band's center) at −W,
  position 0% at +2W. The band is tilted (105°): along the x axis its half-width is about
  0.15 × 4W + 0.04H, and the tilt brings one corner 0.13H nearer. At rest its right edge
  is at about −W + 0.6W + 0.17H = −0.4W + 0.17H, left of the button for any W ≥ 0.44H;
  the square menu arrow of New session (W = H, `is-icon-button`) is covered. On hover it
  ends symmetrically past the right edge. (With 300% the band showed a faint corner on
  buttons narrower than about 3.5H.) `inset: 0` is measured from the padding box (inside
  the 1px border): the band does not cross the border, invisible on a transparent
  border.
- The band passes under the label and icons (stacking above), unlike the mock where it
  passes over them: the text stays crisp. `pointer-events: none` keeps clicks on the
  button.
- Reduced motion: the sweep is movement (roadmap §4); it is removed. The brightening and
  the glow are colour changes; they stay.

## 6. Focus halo — `styles/glow.css`

```css
/* Soft halo instead of the hard 3px ring. Same total thickness (width + offset = 4px), so
   layouts that reserve room for the ring (MessageInput, ChatNavToolbar) keep their
   measures. Declared on every element where the theme declares the ring: the value is a
   var() chain resolved where it is declared. */
:root,
.wa-theme-default,
.wa-light,
.wa-dark,
.wa-invert {
    --glow-focus-halo: color-mix(in oklab, var(--wa-color-focus) 45%, transparent);
    --wa-focus-ring-width: 0.25rem;
    /* A length, never a bare 0: calc() consumers add or subtract it from lengths. */
    --wa-focus-ring-offset: 0rem;
    --wa-focus-ring: var(--wa-focus-ring-style) var(--wa-focus-ring-width) var(--glow-focus-halo);
}
/* Tabs draw their ring inside the tab (negative offset minus this token): keep the old
   1px there, so the wider ring still ends at the tab's edge (−4px to 0) instead of 1px
   outside it, where the tab strip's scroller would cut it. */
:where(wa-tab) {
    --wa-focus-ring-offset: 0.0625rem;
}
```

- This block goes in its own rule, after the token block of §4. Its selector list is the
  theme's (`:where(:root), .wa-theme-default, .wa-light, .wa-dark, .wa-invert`,
  `default.css:175-179`): the theme declares the ring on each of them, and a declaration
  on an element beats inheritance. Unlayered, it beats the theme's `@layer wa-theme`
  declarations on the same elements whatever their specificity.
- `--wa-focus-ring-offset` must carry a unit: in `calc()`, a bare `0` is a number, and
  `calc(<length> + 0)` is invalid at computed-value time (the property falls back to its
  initial value). Its `calc()` consumers:
  - `MessageInput.vue:2281`, `padding-top: calc(width + offset)`: 0.25rem + 0rem = 4px as
    before;
  - `ChatNavToolbar.vue:101-103`, `bottom: max(0px, calc(var(--wa-space-s) - width - offset))`:
    same 4px subtracted as before;
  - `wa-tab` (`chunk.R2GHHEHL.js:44`), `outline-offset: calc(-1 * var(--wa-border-width-l) - offset)`:
    with the `wa-tab` override, −3px − 1px = −4px as before; the 4px ring covers −4…0px
    (before: 3px over −4…−1px);
  - `wa-details` (`chunk.WRKKMHO2.js:82`) and native `details` (`native.css:439`),
    `outline-offset: calc(var(--wa-panel-border-width) + offset)`: 1px less than before;
    the ring is 1px wider, so its outer edge stays where it was.
- Exceptions to "same total thickness" (rings that do not read the offset token, or add
  their own offset), each 1px larger outward:
  - icon-only buttons, `outline-offset: 2px` (`.button.is-icon-button`,
    `chunk.4FOHUBBS.js:202-203`): 3 + 2 = 5px becomes 4 + 2 = 6px;
  - `wa-dropdown-item` (`chunk.OZ74GS2I.js:27-30`, no offset): 3px becomes 4px; the menu
    has `padding: 0.25em` and `overflow: auto` (`chunk.RAKNY5VC.js:17,25`), about 3.5px
    at 14px text (less at smaller sizes); items stretch to the menu's content width, so
    the ring of a focused item is cut by about half a pixel on its left and right edges,
    and also on the top edge of the first item and the bottom edge of the last
    (accepted, §14; `glass.css:402` also gives the focused item a highlight fill);
  - the `wa-split-panel` divider (`chunk.ZZ6XGOYX.js:35-37`), `wa-slider` thumbs
    (`chunk.A3FLBFGD.js:47-51`, `:153-157`) and the `wa-color-picker` grid and slider
    handles (`chunk.XZOAK3IQ.js:57-60`, `:94-97`; the project and workspace dialogs),
    all with no offset: 3px becomes 4px.
  Nothing reserves room for them; they are drawn outside the box.
- An outline follows the border radius in Firefox and Chrome: the halo is rounded.
- Every Web Awesome control and the app rules that use the token (12 files) get the halo with
  no further change.

### 6.1 Outer glow on fields

```css
/* Focused fields add a soft outer glow to their recessed look. */
:where(:is(wa-input, wa-textarea):focus-within)::part(base),
:where(wa-select:focus-within)::part(combobox) {
    box-shadow: var(--depth-inset), 0 0 0.75rem -0.25rem color-mix(in oklab, var(--glow-accent) 45%, transparent);
}
```

The composer keeps level 2 and gets a glow that fits the 4px its footer leaves above it
(`MessageInput.vue`, next to the `::part(base)` rule at `:2344-2346`):

```css
.message-input wa-textarea:focus-within::part(base) {
    box-shadow: var(--depth-2), 0 0 0.5rem -0.25rem color-mix(in oklab, var(--glow-accent) 45%, transparent);
}
```

The glow appears at once: Web Awesome's `wa-input` transitions only `background-color,
border, outline` (`chunk.UYRNQWC7.js:29-32`), `wa-select` a similar list
(`chunk.CTPJMD46.js:82-86`), and repeating those lists here to add `box-shadow` would tie
`glow.css` to Web Awesome internals. The halo (the outline) fades in on `wa-input` and
`wa-select`; `wa-textarea` has no transition at all (`chunk.GWWGP7ZL.js:10-28`), so on a
textarea — the composer included — the halo appears at once too, as the hard ring does
today.

Buttons, tabs, switches and the rest get the halo only (the outline): a
`:focus-visible` state on a shadow host is not reliable across browsers, and the halo is
the visible part the user asked for.

### 6.2 The four hard outlines

In `PendingRequestBody.vue` (`:1418-1421`, `:1521-1524`) and `RequestUserInputBody.vue`
(`:485-488`, `:495-498`), replace
`outline: 2px solid var(--wa-color-brand-fill-loud); outline-offset: 2px;` with
`outline: var(--wa-focus-ring); outline-offset: var(--wa-focus-ring-offset);`. The
comments above them stay true (Tab and forced focus still look identical).

## 7. Selected session row — `SessionListItem.vue` (scoped CSS)

After the multi-select rule (`:728-731`):

```css
/* The open session: a lit fill fading to the right, a thin accent ring, a soft accent
   shadow (visual refresh 6a). No bar on its left (user decision). The ring is the
   outlined button's own 1px border, recoloured, so it sits on the edge of the fill (an
   inset shadow would float 1px inside it). The second selector ties with the
   multi-select rule above and comes after it. */
.session-item--active::part(base),
.session-item-wrapper--selected .session-item--active::part(base) {
    border-color: color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent);
    background-origin: border-box;
    background-image: linear-gradient(100deg, var(--wa-color-brand-fill-normal), color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, transparent));
    box-shadow: 0 2px 10px -4px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent);
    color: var(--wa-color-brand-on-quiet);
}
html.wa-dark .session-item--active::part(base) {
    border-color: oklch(from var(--wa-color-brand-60) 0.6 0.11 h / 0.45);
    background-image: linear-gradient(100deg, oklch(from var(--wa-color-brand-60) 0.32 0.07 h), oklch(from var(--wa-color-brand-60) 0.25 0.05 h / 0.6));
    box-shadow: 0 2px 12px -4px oklch(from var(--wa-color-brand-60) 0.6 0.13 h / 0.5);
    color: oklch(from var(--wa-color-brand-60) 0.88 0.08 h);
}
/* Open and multi-selected: the selection has no other marker than its fill, so it shows
   as a thicker accent ring (the border plus 1px inside) on top of the lit look. */
.session-item-wrapper--selected .session-item--active::part(base) {
    border-color: var(--wa-color-brand-60);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-60),
        0 2px 10px -4px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent);
}
html.wa-dark .session-item-wrapper--selected .session-item--active::part(base) {
    border-color: var(--wa-color-brand-60);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-60),
        0 2px 12px -4px oklch(from var(--wa-color-brand-60) 0.6 0.13 h / 0.5);
}
```

- `background-image` sits over Web Awesome's `background-color`: the outlined hover mix
  still shows through the lighter right end. `background-origin: border-box` (a longhand,
  so `background-color` stays) starts the gradient under the translucent border, so the
  border shows the gradient's own colours (without it, the gradient repeats and the right
  border column shows its start colour).
- Specificity with Vue's scoped attribute: the multi-select rule
  `.session-item-wrapper--selected .session-item[data-v]::part(base)` is (0,3,1);
  `.session-item--active[data-v]::part(base)` is only (0,2,1), hence the second selector
  (0,3,1, later in the source). The dark rules (`html.wa-dark …`) are (0,3,2) and (0,4,2):
  they win over the light ones.
- The keyboard highlight (`outline`) keeps working. The multi-select fill keeps working on
  non-active rows; the open row, when selected, shows the thicker ring.
- The row has `margin-bottom: var(--wa-shadow-offset-y-s)` (0.125rem, `:715-719`); the
  shadow `0 2px 10px -4px` reaches about 8px below the row, over the next row. It shows
  because rows are transparent; a hovered or multi-selected next row covers its tail
  (§14). The title stays `font-weight: 700` (it already is).
- Scoped CSS: the list only exists in the SPA. `SessionListItem` also renders the
  session picker of the peer message review dialog (`PeerMessageReviewDialog.vue:1730-1737`,
  `:active="selectedSessionId === row.session.id"`): the chosen target session is lit the
  same way there, on the dialog's glass, and that is intended.

## 8. Tab line — `TabBar.vue`

Replace the border drawing of `.tab-bar::part(tabs)::after` (`:199-214`):

```css
    /* An accent gradient strip at the bottom of the active tab's box, glowing. */
    border: 0;
    background: linear-gradient(90deg, var(--glow-accent), var(--glow-accent-shifted))
        no-repeat left bottom / 100% var(--glow-ink-thickness);
    filter: drop-shadow(0 0 0.3125rem color-mix(in oklab, var(--glow-accent) 70%, transparent));
```

with `--glow-ink-thickness: max(var(--safe-track-width), 0.1875rem)` declared on
`.tab-bar` (next to `--track-width`). The strip is thicker than the track (3px against
the `--divider-size` track): it grows upward, inside the tab's bottom padding, so the
layout does not move. The `transition` / `opacity` / placement lines stay unchanged; the
rule that makes the active tab's own border transparent stays. `filter` is not in
`--glide-transition`: it does not animate.

The glow shows upward only: `::part(tabs)` sits inside Web Awesome's `.nav`, which has
`overflow-x: auto` (`chunk.R3LBB5FI.js`, `.tab-group-top .nav`), so the overflow is
clipped on the y axis too; the strip is at the bottom edge of the nav (the active tab has
a negative bottom margin of one track) and the downward half of the drop-shadow is cut.
The upward half is the visible glow. It goes past the tab's padding: TabBar tabs have
4px of vertical padding (`TabBar.vue:183-186`); the 3px strip covers the 1px transparent
track border and 2px of that padding, and the drop-shadow (blur 0.3125rem, σ ≈ 2.5px)
fades out about 5px above the strip, so about 3px into the label's line box. The ink is
positioned and `wa-tab` is not (`chunk.R2GHHEHL.js:6-10`), so the ink paints over the
label: descenders get a faint accent tint (about 13% at the label's bottom edge, 4% two
pixels higher). Accepted as the mock's look (the mock's ink glow is larger); judged in
browser check 4, tunable through the blur.

The three bundles import `glow.css` (§4), so every `TabBar` has the two tokens. They have
no fallback on purpose: a missing import must show up in the browser check.

## 9. Context ring

- **Fix:** in `SessionHeader.vue`, `AgentTreeNode.vue` and `OrchestrationNode.vue`,
  `contextUsageColor` returns `'var(--wa-color-brand-fill-loud)'` instead of
  `'var(--wa-color-primary)'` (Web Awesome's own default indicator colour). The ring then
  shows its indicator at every percentage. No other `--wa-color-primary` user is touched
  (not in this step's subject).
- **Glow**, in `glow.css`:

```css
/* The context ring glows in its own colour (--indicator-color is set inline on the host
   and inherits into the shadow tree). On the base part, an HTML box: the SVG clips its own
   overflow and its indicator circle touches the SVG edge. */
:where(wa-progress-ring:is(.context-usage-ring, .onode-context-ring))::part(base) {
    filter: drop-shadow(0 0 0.25rem color-mix(in oklab, var(--indicator-color) 60%, transparent));
}
```

- Why the base part and not `::part(indicator)`: the indicator is a `<circle>` inside
  `<svg class="image">` (`chunk.X2P6WNYQ.js:48-54`); its radius is
  `size / 2 − max(track, indicator) / 2` (`chunk.3HIXNYAW.js`), so the stroke touches the
  SVG edge and the UA rule `svg:not(:root) { overflow: hidden }` would cut the outer half
  of the glow. The mock filters the whole SVG too.
- A drop-shadow has one colour for everything the box paints: the track and the "NN%"
  label glow in the indicator colour too, faintly. Accepted (browser check 5).
- `.onode-context-ring` lives in the SPA only; the rule is harmless elsewhere.

## 10. Quota bars, switches, inks

### 10.1 Quota bars — `ProjectView.vue`

- The three severity fills (`:2419`, `:2467`, `:2515`) pass their colour as a custom
  property instead of `background`: `'--usage-fill': <color>` (the `width` stays). The
  balance dot (`:2518`) is not a bar; it keeps `background`.
- CSS, after `.usage-lane-fill`:

```css
/* Severity fills: a gradient from a lighter step of the colour, with a soft glow of it. */
.usage-lane-fill:not(.usage-lane-time) {
    background: linear-gradient(90deg, oklch(from var(--usage-fill) calc(l + 0.08) c h), var(--usage-fill));
    box-shadow: 0 0 0.375rem color-mix(in oklab, var(--usage-fill) 50%, transparent);
}
```

`--usage-fill` is always set on these three elements (the computed colours never return
null). The neutral time lane keeps its flat fill.

### 10.2 Switches — `glow.css`

```css
/* A checked switch: an accent gradient track with a soft glow. */
:where(wa-switch:state(checked):not([disabled]))::part(control) {
    border-color: transparent;
    background-origin: border-box;
    background-image: linear-gradient(90deg, var(--wa-form-control-activated-color), oklch(from var(--wa-form-control-activated-color) calc(l + 0.08) c calc(h + 30)));
    box-shadow: 0 0 0.75rem -0.125rem color-mix(in oklab, var(--wa-form-control-activated-color) 70%, transparent);
}
```

`:state()` ships in Firefox and Chrome (it is already used by `SegmentedControl.vue:110`).
The rule lights every checked switch: the settings ones and the transcript group toggles
(`GroupToggle.vue:65-83`, one per expanded group, also in the share viewer transcript;
that component sets the control part's opacity and replaces its transition list with
`opacity` (`:74-77`): the glow keeps the opacity and appears at once there, §14).
Web Awesome gives the checked track a 1px border in the base colour
(`chunk.M2KNDX3A.js:68-71`); it goes transparent, with the gradient drawn under it, so
the lighter right end is not framed by a darker rim.

### 10.3 Gliding inks

- `SegmentedControl.vue`: a new rule
  `.segmented-control > .glide-ink { box-shadow: 0 0 0 1px var(--wa-color-brand-border-quiet), 0 2px 8px -2px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent); }`.
  The pre-ready fallback (the checked segment carries the fill, `:114-117`) stays flat.
- `SettingsPopover.vue` `.settings-nav`: `--glide-ink-bg` becomes
  `linear-gradient(100deg, var(--wa-color-brand-fill-normal), var(--wa-color-brand-fill-quiet))`
  (`.glide-ink` uses the `background` shorthand, so a gradient works), and a rule
  `.settings-nav > .glide-ink { box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--wa-color-brand-60) 35%, transparent); }`.
  The hover rule `.settings-nav-item:hover` (`:2380-2382`) becomes
  `.settings-nav-item:not(.active):hover`: the item paints above the ink, and a flat
  hover tint would hide the gradient of the chosen section. The other items keep
  `--glass-item-hover`, now distinct from the chosen section. Below 640px the settings
  are a drill-down list with no chosen section (the ink is hidden,
  `SettingsPopover.vue:2474-2482`): inside that `(width < 640px)` block, a rule
  `.settings-nav-item:hover { background: var(--glass-item-hover); }` gives every item
  its hover tint back.

## 11. Invariants

- Firefox and Chrome show the same result. Every feature used here ships in Firefox 156
  (relative colors, `color-mix`, `:state()`, `::part()::after`, outline following the
  radius). No JavaScript.
- No layout shift: the focus ring keeps 4px in total (icon-only buttons, menu items,
  split-panel dividers, slider thumbs and colour-picker handles: 1px more, drawn outside,
  §6); the tab strip grows inside the tab; shadows and filters do not take space.
- Reduced motion: only the sheen sweep is removed. The button transitions of colours,
  shadows and `filter` stay (fades); the field glow appears at once, and so does the
  halo on a textarea (§6.1).
- `glow.css` is unlayered, has no `!important`, wraps its global selectors in `:where()`.
- The multi-select marker, the keyboard highlight and disabled buttons keep their
  meaning: disabled and fake-disabled brand buttons are not lit (§5); an open row that is
  multi-selected shows a thicker ring (§7).

## 12. Tests (node:test, `npm test`)

New `frontend/src/styles/glow.test.js`, in the style of `depth.test.js` / `motion.test.js`
(read the file, strip comments, parse blocks):

1. `glow.css` is imported right after `motion.css` in the three entry files (and test 12 of
   `motion.test.js` keeps passing: `motion.css` still follows `glass.css`).
2. `glow.css` has no `@layer` and no `!important`.
3. The focus block declares `--wa-focus-ring-width: 0.25rem`, `--wa-focus-ring-offset: 0rem`
   (a length with a unit: the test fails on a bare `0`) and a `--wa-focus-ring` using
   `--glow-focus-halo`, on the selector list `:root`, `.wa-theme-default`, `.wa-light`,
   `.wa-dark`, `.wa-invert`; width + offset equals the theme's 0.1875rem + 0.0625rem
   (4px). A `:where(wa-tab)` rule sets `--wa-focus-ring-offset: 0.0625rem`.
   The token block of §4 is on `:root, .wa-invert` and declares the five `--glow-*`
   tokens.
4. The brand-button rules target `[variant='brand'][appearance='accent']` and exclude
   `.wa-invert wa-button`, `.layout-winbtn`, `.dock-winbtn`, `.detail-toggle`, `[disabled]` and `.fake-disabled` (hover,
   press and sheen also `[loading]`); the rest rule sets `background-origin: border-box`
   as a longhand; the hover rule sets `background-color: var(--wa-color-fill-loud)`; the
   press rule (`:active`) comes after the hover block and restores
   `color-mix(in oklab, var(--wa-color-fill-loud), var(--wa-color-mix-active))`; each
   `:is(`/`:not(` compound is on one line (no line break before `:not(`). Exempt: the
   reduced-motion rule that hides the sheen targets every brand solid button, with no
   exclusion.
5. The sheen: the rest rule sets `position: relative` and `isolation: isolate`; a rule on
   `::part(base)::after` with `pointer-events: none`, `inset: 0`, `z-index: -1` and
   a `background` sized `400% 100%` at position `100% 0`;
   its hover rule sets `background-position` under `@media (hover: hover)`; a
   `prefers-reduced-motion: reduce` block hides it.
6. `motion.css`: the rest rule `:where(wa-button)::part(base)` has 7 entries in each of
   its three lists, the 7th being `filter` / `var(--wa-transition-fast)` /
   `var(--wa-transition-easing)`; the press rule declares only `transition-duration`, with
   7 entries, the 7th `var(--wa-transition-fast)`. The internal-parts rules keep 6.
7. No `var(--wa-color-primary)` left in `SessionHeader.vue`, `AgentTreeNode.vue`,
   `OrchestrationNode.vue` `contextUsageColor`; the ring glow rule targets
   `::part(base)`, not `::part(indicator)`.
8. `ProjectView.vue`: no `.usage-lane-fill` with an inline `background:`; the three
   severity fills set `'--usage-fill'`; a `.usage-lane-fill:not(.usage-lane-time)` rule
   sets a `background` gradient reading `var(--usage-fill)` and a `box-shadow`.
9. The four option-card focus rules use `var(--wa-focus-ring)`; no
   `outline: 2px solid var(--wa-color-brand-fill-loud)` left in `frontend/src`.
10. `TabBar.vue`: the `::part(tabs)::after` rule has no `border-block-end` and uses
    `--glow-ink-thickness`; `.tab-bar` declares
    `--glow-ink-thickness: max(var(--safe-track-width), 0.1875rem)`.
11. `SessionListItem.vue`: the active rule's selector list includes
    `.session-item-wrapper--selected .session-item--active::part(base)`, sets a
    `border-color` and `background-origin: border-box`; an `html.wa-dark
    .session-item--active::part(base)` rule exists; the open-and-selected rules (light and
    dark) set `border-color: var(--wa-color-brand-60)` and a 1px inset ring.
12. Fields: a `glow.css` rule on `wa-input` / `wa-textarea` `:focus-within` `::part(base)`
    and `wa-select:focus-within::part(combobox)` sets a `box-shadow` starting with
    `var(--depth-inset)`; `MessageInput.vue` has a
    `.message-input wa-textarea:focus-within::part(base)` rule starting with
    `var(--depth-2)`.
13. Switch: a `glow.css` rule on `wa-switch:state(checked):not([disabled])` `::part(control)`
    sets `border-color: transparent`, `background-origin: border-box`, a
    `background-image` and a `box-shadow`.
14. Inks: `SegmentedControl.vue` has a `.segmented-control > .glide-ink` rule with a
    `box-shadow`; `SettingsPopover.vue` has a `.settings-nav > .glide-ink` rule with a
    `box-shadow`, and its hover rule is `.settings-nav-item:not(.active):hover`, with a plain
    `.settings-nav-item:hover` rule inside the `(width < 640px)` block.

Existing assertions this spec changes (update them, nothing else):

- `motion.test.js` test 5: `transition-property` of `:where(wa-button)::part(base)` gains
  `filter`; its duration and timing lists, and the press rule's duration list, gain a 7th
  entry. `assertTransitionLists` (`:260-268`) is shared with `INTERNAL_PARTS`, which keep
  6 entries, and picks its branch by identity (`durations === BASE_DURATIONS`). Change
  its signature to take the expectations as parameters — for example
  `assertTransitionLists(rule, { durations, properties, timings }, label)`, where a rest
  rule passes all three and a press rule passes `properties: undefined` (asserting the
  rule has no property list) — and give the button rules 7-entry expectations while the
  internal parts keep the 6-entry ones.
- `glide.test.js:147`: the `border-block-end` assertion on `.tab-bar::part(tabs)::after`
  becomes `border: 0` plus a `background` using `--glow-ink-thickness` and a `filter`.
- `glide.test.js:250` (the `SITES` expectations, checked at `:256`): SettingsPopover's
  `--glide-ink-bg` becomes the gradient of §10.3.

Every other suite must stay green unchanged (`depth.test.js`, `glass.test.js`,
`overlay-motion.test.js`, …). If another assertion breaks, the report names it and says
why.

## 13. Browser checks (worktree instance http://localhost:5174, Firefox first, then Chrome; light and dark)

1. Send, New session (with its menu arrow), a dialog Create / Save, the Home Inbox button
   (with its badge): shading, coloured shadow (note whether the footer / sidebar bottom
   cuts it at rest, and the session footer cuts Send's glow below and on the right on
   hover, §14); an Approve button of a pending tool call (same footer cuts) and a dialog
   Save on hover (faint tail cut); the Home Settings button next to Inbox (lit, its gear
   still rotates, no badge shown); hover is brighter than rest and a sheen
   sweeps once left to right, under the label and the badge; leaving the hover resets it
   without a reverse sweep; press darkens (and sinks, except the split New session).
   The detail toggle at the corner of a detailed transcript block (app and share viewer)
   and the Restore button of a maximized dock stay unlit. Disabled Send
   (empty composer) and the dimmed New session button (a project where sessions cannot
   start): no glow, no hover change, no sheen. The hover glow under Send and under New
   session: note whether the footer / sidebar bottom cuts it on hover too (§14).
2. Tab with the keyboard through a dialog: every control (button, field, select, switch,
   tab, checkbox, a menu item reached with the arrow keys) shows the soft halo; fields
   also glow. A focused tab: the halo stays
   inside the tab, not cut at its top. An open `wa-details` summary: the halo sits where
   the ring was. The composer: halo + small glow,
   not cut at the top. No control moves when focus arrives.
3. Session list: the open session has the lit fill, the ring and the shadow, light and
   dark; hover over it; multi-select mode still shows selected rows distinctly, and the
   open row, when selected, shows the thicker ring; the keyboard-highlighted row shows
   the halo. The session picker of the peer message review dialog: the chosen session
   is lit the same way (light and dark).
4. Tab bars (session tabs, a dock): the line is a thicker gradient that glows upward,
   glides as before, and the tab text does not move; the faint tint on the label's
   descenders (§8) does not hurt reading.
5. Context ring (session header, and a node of the orchestration tree) below 50%: the
   indicator shows (it was invisible before) and glows; above 50% and 70%: warning /
   danger glow. The "NN%" label stays readable under the faint glow.
6. Sidebar footer quota bars: gradients + glow for each severity; the time lane stays flat.
7. Switches in the settings, and the transcript group toggles of a session with an
   expanded group: checked ones show the gradient and glow; unchecked unchanged; the
   gradient appears at once on check (§14); the settings switches' thumb still slides,
   the group toggles' thumb jumps as it does today (`GroupToggle.vue:74-76` replaces the
   control's transition list with `opacity`).
8. Segmented controls (the "Favor" control of the benchmark task in the agent settings
   panel, the "Tree to show" switch of the orchestration panel) and the settings section
   menu: inks glow; the chosen section's fill is a gradient.
9. Reduced motion (OS setting): no sheen sweep; everything else unchanged.
10. The share viewer page and an artifact page: buttons, focus and (share viewer) the
    transcript group toggles look the same as in the app (the three bundles import
    `glow.css`).

## 14. Limitations

- Only brand solid buttons are lit. Danger / neutral / success solid buttons keep the
  step-2 look. Widen later if the user wants it.
- Split buttons (New session + its arrow) glow per segment: two shadows side by side.
- The focus ring of a menu item is cut by about half a pixel on its left and right edges,
  and on the top / bottom edge of the first / last item (§6).
- The glow of the selected session row can be cut by the list's scroll edges (sidebar
  list and peer review picker) at the first and last visible row, and its tail (about 8px below the row) is hidden under the next
  row when that row is hovered or multi-selected.
- Buttons get the halo but not the extra outer glow of fields (§6.1). The field glow
  appears without a fade.
- The tab-line glow shows upward only, and its top fades over the label's descenders
  (§8).
- The glow of a lit button is cut by its container in several places (the cut part is
  the faint outer tail). It reaches about 18px below the button at rest (`0 6px 18px -6px`:
  6 − 6 + 18) and about 30px on hover (`0 10px 26px -6px`: 10 − 6 + 26), and about 20px
  to the sides on hover (26 − 6). Send sits inside `.message-input` (12px padding,
  `MessageInput.vue:2278-2286`), inside the session footer, a scroll container
  (`SessionItemsList.vue:2531-2533`): the footer cuts the glow below at rest and on hover,
  and on the right on hover. The pending-request action buttons (Approve, Submit, Done…,
  `claude_code/PendingRequestBody.vue`, `codex/PendingRequestBody.vue`,
  `McpToolCallApprovalBody.vue`, `PlanImplementationBody.vue`, `AutoReviewDenialBody.vue`,
  `RequestUserInputBody.vue`, `ElicitationFormBody.vue`, `ElicitationUrlBody.vue`) sit in
  the same footer, right-aligned, inside `.pending-request-form` (12px padding): same
  cuts. Under the floating New session button (`bottom: var(--wa-space-s)`) the lower part
  can be cut too. A modal dialog is a scroll container (`dialog:modal { overflow: auto }`)
  with a 24px footer padding (`--wa-space-l`): only the last ~6px of the hover glow under
  a dialog's Create / Save is cut.
- A switch's checked gradient appears at once when it is checked and disappears at once
  when it is unchecked: `background-image` does not interpolate (Web Awesome's 200ms
  `background` transition on `.switch`, `chunk.M2KNDX3A.js:38-40`, still fades the flat
  colour under it and the glow `box-shadow`). The thumb still slides. The transcript
  group toggles only transition their opacity already (`GroupToggle.vue:74-76`).
- The context-ring glow also tints the track and the label (§9).
- Forced colors (Windows high contrast): gradients and glows are dropped by the browser;
  the outline stays (system colour). Not specially handled.

## 15. Delivery

One commit (after the user's review in the browser and an explicit "commit"):
`feat(ui): accent glow on buttons, focus, selected session and indicators`. Roadmap
update (status row 6, a §6j) in a separate docs commit.
