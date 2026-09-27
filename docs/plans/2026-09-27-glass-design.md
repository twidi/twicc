# Accent-tinted glass overlays — design (visual refresh, step 3)

## 1. Context

Step 3 of the "Signature" visual refresh. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` (§4 binding user decisions, §6.2 and
§6b.2 lessons, §8.4 the target look, §9 "Step 3" mock values). Steps 1 (canvas + floating
panels, `frontend/src/styles/surfaces.css`) and 2 (depth + typography,
`frontend/src/styles/depth.css`) are done on branch `enhanced-ui`.

Today every floating layer is opaque: Web Awesome paints dialogs, menus, select lists and
popovers with `--wa-color-surface-raised` / `--wa-color-surface-default` (white in light);
our own panels (composer pickers, session switcher, selection comment, hover panel, chart
tooltips) copy those values; toasts use Notivue's `slateTheme` / `lightTheme`, inverted
against the page. No `backdrop-filter` exists anywhere in `frontend/src`.

### 1.1 User decisions for this step (2026-09-27)

- **Toasts** become glass like the menus: light glass in light mode, dark glass in dark
  mode. They are no longer inverted.
- **Every dialog** gets the glass, including the large content dialogs (changelog, help,
  usage graph, peer message review, media preview).
- **Every theme** gets the glass, the awesome theme included (glass is a surface, not a
  control; step 2's awesome exclusion was about controls).
- **The share viewer** gets the same look (its dialogs, popover, select lists, tooltips
  and the veil of its sub-agent drawer; the drawer panel itself holds a transcript and
  stays opaque, §6.2). The artifact shell also imports the tokens, so its consent
  dialog matches the SPA preview (same reason `depth.css` is imported there).
- Details delegated to the author (user, 2026-09-26: "Je te fais confiance, au pire on
  changera des trucs plus tard"); the user reviews in the browser before "commit" and plans
  a global fine-tuning pass at the end of the redesign.

## 2. Goal

What the user sees (roadmap §8.4):

- Menus, select lists, popovers, dialogs, the command palette, the search overlay, the
  settings panel, the session switcher, the composer pickers and toasts are
  **translucent and blurred**: what lies behind shows through, softened.
- Their tint is **a light wash of the accent color** (dark wash in dark mode), never grey,
  with a faint accent border and a thin light edge on top (light mode).
- The veil behind a dialog, the session switcher and the mobile drawer is **lighter,
  slightly tinted with the accent and blurred** instead of a flat dark veil.
- Tooltips stay dark in light mode (light in dark mode), **slightly translucent** and
  blurred.
- Rows highlighted in those surfaces (menu hover, keyboard-selected palette/search/picker
  row, switcher row) use a **translucent accent tint** instead of grey or white.
- Text fields inside glass surfaces are **70 % opaque**, so they read as fields without
  punching a white hole in the glass.

## 3. Non-goals

- **No blur on any ancestor of panes or pooled iframes** (roadmap §6.2): `.main-content`
  and its branches, `.session-layout`, `.center-slot`, `.dock-region`, `.layout-overlay`,
  `.frame-host`, `.file-pane-preview--fullscreen`, `.browser-pane--fullscreen`. They stay
  exactly as they are. The layout overlay keeps its opaque card and its current veil
  (`LayoutOverlay.vue` `.overlay-backdrop`, `rgba(0,0,0,.2)`): it is a panel, not a
  transient overlay.
- The headers' overflow panels (`SessionHeader.vue` `.session-collapsible-rows`,
  `ProjectDetailHeader.vue` `.detail-collapsible-rows`) stay opaque panels (step 2 gave
  them the panel shadow budget; they belong to the card family).
- The mobile sidebar drawer itself stays opaque canvas (roadmap §4 "Mobile drawer"); only
  its veil changes.
- The color picker popup (`wa-color-picker`, in the project and workspace dialogs) stays
  opaque: judging a color needs a neutral backdrop.
- The Git graph tooltips (`BranchTagTooltip.vue`, `CommitNodeTooltip.vue`) keep their
  per-commit colors.
- The activity heatmap tooltip (project detail → Stats tab, `ContributionGraph.vue:131`,
  `:tooltip="true"` on `vue3-calendar-heatmap`) keeps its own look: it is a third-party
  tippy.js tooltip styled by the library (`.tippy-box { background-color: #333 }`), outside
  the Web Awesome and app surfaces this step targets.
- Motion: no new entrance/exit animation (step 5). Existing Web Awesome and Notivue
  animations stay.
- Pre-existing issues seen during the inventory and unrelated to glass stay as they are:
  `SearchOverlay.vue`'s mobile `::part(panel)` rule (a part Web Awesome 3 does not have),
  the undefined `--wa-color-surface-alt` / `-base` / `-secondary` / `--wa-color-surface`
  references in several dialogs (they resolve to no background today). Exception: the two
  dead `::part(overlay)` rules are removed (§6.4), because they state a veil that
  contradicts this step.
- Shared components rendered inside a dialog but also used elsewhere (e.g.
  `SessionListItem`, `MediaThumbnailGroup`) keep their own hover/selection styles. One
  exception: the file tree rows, whose opaque resting background would fill the whole
  `@` file picker, directory picker and file move dialog (§6.5, contextual `--row-*`
  tokens).
- Chips, keys, code and thumbnails inside glass surfaces (`kbd`, `.picker-key`,
  `.modifier-btn`, `.tsc-quote`, `.snippet-role`, the bordered attachments state box
  `.pr-attachments-state` of the peer message review dialog) keep their own backgrounds:
  they are elements on the surface, not the surface. Rendered content keeps its fills too:
  markdown tables, code frames and quotes (`github-markdown.css` `--bgColor-*`,
  `MarkdownContent.vue` `.code-tools-rendered` and `--md-tint-fill*`) and the peer message
  bubbles (`PeerMessageReviewDialog.vue` `.pr-quote`, `PeerInboxRow.vue` `.pir__message`) —
  they frame content the user reads, shown the same way wherever it appears. The same holds for every Web Awesome
  control or status element that paints its own fill: callouts (`wa-callout`,
  `--wa-color-fill-quiet`, the largest such blocks: settings popover, peers manager, peer
  message review, most form dialogs), filled/solid/neutral buttons, tags, badges, progress
  and slider tracks, switches, checked checkboxes and radios. They carry meaning through
  their fill (status, state, action) and read as objects on the glass.

## 4. Tokens — new file `frontend/src/styles/glass.css`

Shared by the SPA, the share viewer and the artifact shell, like `depth.css`: nothing in
it may depend on another app stylesheet except `depth.css` (`--depth-3`) and the Web
Awesome theme tokens. Unlayered (beats the themes' `@layer wa-theme*` declarations).
Declaration order matters (all blocks match `<html>`): light tokens on `:root`, dark
overrides on `.wa-dark` (same specificity, later wins), then the reduced-transparency and
no-support overrides on `:root` (later again, so they win over `.wa-dark` too).

Tokens declared on `:root` resolve their `var()` references on `<html>`, where `.wa-dark`
also applies, and are inherited as values — so they carry the **page** scheme everywhere,
including inside `.wa-invert` boxes (tooltip content).

```css
:root {
    /* The tint: the accent's lightest step softened toward the raised surface. Derived by
       mixing palette steps, never by forcing a chroma on the accent hue: a gray accent
       (wa-brand-gray) has no hue and must stay gray. */
    --glass-tint: color-mix(in oklab, var(--wa-color-brand-95) 70%, var(--wa-color-surface-raised));
    --glass-bg: color-mix(in oklab, var(--glass-tint) 74%, transparent);
    --glass-border: color-mix(in oklab, var(--wa-color-brand-60) 22%, transparent);
    /* Thin light top edge, drawn by the border overlay (§5.1). */
    --glass-highlight: inset 0 1px 0 oklch(1 0 0 / 0.7);
    /* The surface's cast shadow: level 3, outer layers only (the top edge is the overlay's). */
    --glass-shadow: var(--depth-3);
    --glass-filter: blur(1.125rem) saturate(1.6);
    /* Sticky headers inside a glass surface (content scrolls under them). */
    --glass-sticky-bg: color-mix(in oklab, var(--glass-tint) 60%, transparent);
    /* Text fields inside a glass surface. */
    --glass-field-bg: color-mix(in oklab, var(--wa-color-surface-default) 70%, transparent);
    /* Highlighted row: menu/option hover, keyboard-selected row. */
    --glass-item-highlight: color-mix(in oklab, var(--wa-color-brand-fill-normal) 70%, transparent);
    /* Mouse hover on rows that also have a keyboard-selected state (lighter, so the
       selection stays distinct). */
    --glass-item-hover: color-mix(in oklab, var(--wa-color-brand-fill-normal) 45%, transparent);
    /* Resting card-like row inside a glass surface (settings Tips/Help rows). */
    --glass-item-rest: color-mix(in oklab, var(--wa-color-surface-default) 45%, transparent);
    /* The veil behind modal layers: a faint accent-tinted dim, slightly blurred. */
    --glass-veil: color-mix(in oklab, color-mix(in oklab, var(--wa-color-brand-20) 25%, var(--wa-color-neutral-20)) 22%, transparent);
    --glass-veil-filter: blur(0.375rem);
    /* Tooltips: the theme's tooltip color (text-normal), slightly translucent. */
    --glass-tooltip-bg: color-mix(in oklab, var(--wa-color-text-normal) 82%, transparent);
    --glass-tooltip-filter: blur(0.5rem);
}

.wa-dark {
    --glass-tint: color-mix(in oklab, var(--wa-color-brand-10) 40%, var(--wa-color-surface-raised));
    --glass-border: color-mix(in oklab, var(--wa-color-brand-60) 28%, transparent);
    --glass-highlight: inset 0 1px 0 oklch(1 0 0 / 0.07);
    /* Dark --depth-3 starts with an inset top line (depth.css): the border overlay draws
       the edge here, so the surface takes only the outer layers. Keep in sync with
       --depth-3 dark. */
    --glass-shadow:
        0 2px 6px oklch(0 0 0 / 0.45),
        0 24px 56px -12px oklch(0 0 0 / 0.65);
    --glass-veil: color-mix(in oklab, color-mix(in oklab, var(--wa-color-brand-05) 30%, black) 50%, transparent);
}
```

`--glass-bg` and `--glass-sticky-bg` are declared once on `:root`: their `var(--glass-tint)`
resolves on `<html>`, where `.wa-dark` also applies, so the dark tint is used in dark mode
(the same mechanism as `--canvas-background` in `surfaces.css`).

Mock reference (roadmap §9): light `oklch(0.975 0.028 h)` at 74 %, dark `oklch(0.24 0.04 h)`
at 74 %, border `brand-60` at 22 % / 28 %, highlight `.7` / `.07`, blur `1.125rem`,
saturate `1.6`, fields at 70 % surface, menu hover `b-fill-normal` 70 %, palette highlight
75 %, settings-nav hover 45 %, veil `oklch(0.3 0.03 h / .22)` / `oklch(0.08 0.02 h / .5)`
blurred `.375rem`, tooltip `text` 82 % blurred `.5rem`. The values above approximate those
from palette steps (blue accent: `brand-95` is `oklch(95.9% 0.020 250)`, `brand-10` is
`oklch(24.0% 0.102 261)`); exact tuning is the user's global pass.

### 4.1 Opaque fallbacks

Placed after the `.wa-dark` block:

```css
@media (prefers-reduced-transparency: reduce) {
    :root {
        --glass-bg: var(--glass-tint);
        --glass-sticky-bg: var(--glass-tint);
        --glass-field-bg: var(--wa-color-surface-default);
        --glass-tooltip-bg: var(--wa-color-text-normal);
        --glass-filter: none;
        --glass-veil-filter: none;
        --glass-tooltip-filter: none;
    }
}
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    :root { /* the same seven declarations */ }
}
```

The tint, the border, the item tokens (`--glass-item-*`) and the veil color stay: they are colors, not
transparency effects, and read correctly over an opaque surface. A translucent surface
without blur would hurt readability, hence the no-support fallback.

## 5. Web Awesome mapping (global rules in `glass.css`)

All global rules are wrapped in `:where()` (specificity 0 + `::part`), like step 2: an
unlayered component rule on the same part still wins; being unlayered they beat every
layered theme rule. Every `backdrop-filter` declaration is paired with the same
`-webkit-backdrop-filter` declaration (Safari before 18).

### 5.0 The blur lives on a layer, never on the surface

A non-`none` `backdrop-filter` makes its element the **containing block of every
`position: fixed` descendant** (like `transform` or `filter`). Verified in the browser on
5174 (2026-09-27): a `position: fixed; top: 0; left: 0` child of a `wa-dialog` sits at the
viewport origin; with `backdrop-filter` on `::part(dialog)` it moves to the dialog's
top-left corner. Web Awesome relies on fixed descendants that live inside our surfaces:

- the tooltip's hover bridge (`wa-popup` renders it at `chunk.KGZRQJER.js:325-331` and
  shows it only with `hover-bridge`, `:329`, which `wa-tooltip` sets,
  `chunk.2XOQF53T.js:280`; `.popup-hover-bridge { position: fixed; inset: 0; z-index: 899;
  clip-path: polygon(…viewport coordinates…) }` in `chunk.NUEKQX75.js`) sits **outside**
  the popup's top-layer element, so a tooltip
  anchored inside a blurred dialog, popover, menu, panel or toast would get a displaced,
  invisible bridge that catches the pointer;
- the dropdown submenu's safe triangle (`#submenu::before`, `position: fixed`, viewport
  coordinates, `pointer-events: auto`, `chunk.OZ74GS2I.js`) would be placed relative to
  the submenu if the submenu itself carried the blur (it is the triangle's own parent; a
  blurred parent menu would not matter, the submenu is in the top layer).

So no surface that has children carries the `backdrop-filter` (one opt-in exception: five
selects with two kinds of wide list box, below). The surface keeps its shadow and radius and clears its
background and border. Two childless pseudo-elements do the painting:

- a **glass layer** (`z-index: -1`, behind the content): glass background and blur;
- a **border overlay** (`z-index: 100`, above the content): the glass border and, just
  inside it, the light top edge.

Why:

- **No border on the surface**: several surfaces clip their children at the padding box
  (`overflow: hidden` on the picker and switcher panels, the UA's `overflow: auto` on the
  modal `<dialog>`). A layer pushed under a border would be clipped there and leave an
  unblurred ring. With no border, the padding box is the border box and pseudo-elements at
  `inset: 0` cover it whole.
- **The border above the content**: at `z-index: -1` a border would disappear under edge
  scrollbars (menu, dialog body, settings detail, search results), full-bleed highlighted
  rows (picker rows `CommandPickerPopup.vue:501-536`; file tree rows `.node-label`,
  `position: relative; min-width: 100%`, `FileTree.vue:449-463`, in the file and directory
  pickers whose `.tree-container` has no padding, `FileTreePanel.vue:1523-1530`), the
  palette's full-bleed sticky header (`CommandPalette.vue:854-861`, `position: sticky;
  z-index: 1`) and the media preview's full-width content (`MediaPreviewDialog.vue:319-328`,
  `position: relative`). An outline does not solve it: Chromium paints an element's outline
  before its positioned descendants (checked by the round-6 reviewer on a `showModal()`
  dialog: a sticky `z-index: 1` header and a `position: relative` row painted over the
  outline). A positioned overlay with `z-index: 100` in the host's stacking context paints
  after every in-surface child with a lower `z-index` (the highest found inside these
  surfaces is 10, the usage graph hover tooltip `UsageGraphDialog.vue:1422`; others: 5 at
  `UsageGraphDialog.vue:1281`, 3 for the settings sticky back row `SettingsPopover.vue:2474`,
  1 for the palette sticky header).
- **Exception, the submenu**: its `::before` is Web Awesome's safe triangle and its
  `::after` is the glass layer, so it keeps an inner outline (`outline:
  var(--wa-border-width-s) solid var(--glass-border); outline-offset: calc(-1 *
  var(--wa-border-width-s))`). Its items are positioned (`wa-dropdown-item` `:host {
  position: relative; isolation: isolate }`, `chunk.OZ74GS2I.js:6-12`) and would paint
  over the outline, but they are inset by the submenu's `0.25em` padding (`#submenu`), so
  only a keyboard-focused item's focus ring (`:host(:focus-visible) { z-index: 1; outline:
  var(--wa-focus-ring) }`, `:27-29`; 3px wide, `themes/default.css:262`) can reach the
  outline pixel — touching it at 16px text, overlapping it by about 0.5px at 14px. The
  focus ring is the indicator that matters there; accepted, checked at the gate (§10.2).
- **Geometry**: the glass border is `var(--wa-border-width-s)`: 1px in the default and
  shoelace themes, **2px in the awesome theme** (`themes/awesome.css:249-250`,
  `--wa-border-width-scale: 2`; `default.css:243`, `shoelace.css:248`: scale 1). Below,
  "b" is that width. Surfaces that had a border of their own — b for the Web Awesome
  surfaces (menu, submenu, list box, popover body: `--wa-border-width-s`) and the switcher
  (`SessionSwitcher.vue:165`, `var(--wa-border-width-s)`), 1px for the pickers
  (`CommandPickerPopup.vue:477`, …), the selection comment panel
  (`TextSelectionComment.vue:471`) and the hover panel (`HoverInfoPanel.vue:76`) — change in one of two ways. Boxes with an explicit or stretched
  width (the pickers, the switcher, the selection comment panel, the layered list box, the
  agent settings popover's body) keep their outer size and gain that width of content box
  per side (`border-box`, `native.css`; Web Awesome hosts, `chunk.EPHHWXK2.js:16-24`).
  Content-sized boxes (`max-content`: menu `chunk.RAKNY5VC.js:15`, submenu
  `chunk.OZ74GS2I.js:132`, default popover body `chunk.4GV5JLFP.js:83`; `fit-content`
  — the UA `[popover]` width — capped at `max-width: 20rem`: the hover panel,
  `HoverInfoPanel.vue:67-84`, `:74`) keep their
  content box and get twice that width narrower (the hover panel only while under its
  cap). Either way the border overlay covers the outermost b rows of the padding box (and,
  at the top, the light edge covers the next row). Text is inset by padding. Two
  things still reach those rows:
  - full-bleed fills (rows, sticky headers, scrollbars) — intended: the border reads on top;
  - **focus rings of controls close to the edge**. A `wa-input` ring reaches 4px outside
    the field (`&:focus-within { outline: var(--wa-focus-ring); outline-offset:
    var(--wa-focus-ring-offset) }`, `chunk.UYRNQWC7.js:38-41` — `:focus-within`, so it
    also shows after the programmatic focus on open; width 3px and offset 1px,
    `themes/default.css:262-264`). The filter fields of the command and history pickers
    (`.picker-search { padding: var(--wa-space-2xs) }` = 4px,
    `CommandPickerPopup.vue:490-491`, `MessageHistoryPickerPopup.vue:446-447`, focused on
    open, `CommandPickerPopup.vue:174`, `MessageHistoryPickerPopup.vue:147-152`) would lose
    their top ring rows under the border and the edge (headless Chrome replica by the
    round-10 reviewer: only 1 of 3 ring rows visible on top). The directory picker's header
    buttons ("navigate up", "hidden toggle", `DirectoryPickerPopup.vue:318-338`; a
    `wa-button` ring is also 4px out, `.button:focus-visible`, `chunk.4FOHUBBS.js:180-183`)
    sit 4px from the top edge (`.picker-header { padding: var(--wa-space-2xs)
    var(--wa-space-xs) }`, `:388-395`). The file tree panel's filter field (`.files-search`,
    same 4px, `FileTreePanel.vue:1504-1505`) renders inside glass only in the `@` file
    picker (it needs `search-fn` or `has-extra-tree`, `FileTreePanel.vue:1300`; among glass
    hosts only `FilePickerPopup.vue:357` passes `search-fn` — `FilesPanel.vue:1101` and
    `GitPanel.vue:1566` also pass it, outside glass — and `has-extra-tree` comes only from
    `FilesPanel.vue:1111`); there it never touches the top edge (the picker's
    `.picker-header` precedes it, `FilePickerPopup.vue:346`) but its ring's outer column
    falls under the side borders. Fix: a contextual gap that follows the border width,
    `--glass-edge-gap: calc(var(--wa-border-width-s) + 1px)` (b + 1: the border rows plus
    the edge row), set by the §5.1 token rule and added to those paddings (§6.9) — 6px
    inside glass in default and shoelace, 7px in awesome (where the ring is also 3px wide
    with a 1px offset, `awesome.css:268-270`, and `--wa-space-2xs` is 4px, `:233`);
    unchanged in the Files and Git panes.

  Accepted, checked at the gate (§10.2):
  - a keyboard-focused first/last item of a dropdown menu (the ring has no offset,
    `chunk.OZ74GS2I.js:27-29`, and the menu padding is `0.25em`, `chunk.RAKNY5VC.js:17`):
    at 16px text the ring's top row lies under the light edge (2 of 3 rows visible); at
    14px it also overlaps half the border row; in the awesome theme (2px border) only 1
    of 3 rows shows at 16px;
  - the same in the submenu (outline only, no separate edge row: the ring touches a 1px
    outline, and overlaps the 2px one of the awesome theme by one row);
  - the settings popover's logout button (`SettingsPopover.vue:2753-2756`,
    `position: absolute; right: 0`, template `:2319`): flush with the popover's right
    edge, so the border overlay crosses its rightmost column, its hover fill and its focus
    ring. A gap cannot fully clear it: its ring already extends outside the popover today.

Verified in the browser on 5174: `wa-dialog::part(dialog)::before` and
`wa-dropdown::part(menu)::before` accept `backdrop-filter` from the document and blur the
page; the fixed child stays at the viewport origin; the menu layer stays in place while
the menu scrolls (below).

Where each layer is anchored (`position: absolute` needs a containing block, `z-index: -1`
needs the host to be a stacking context so the layer stays behind the host's content but
inside it):

Every layer and every border overlay is `position: absolute; inset: 0`; a host's border
overlay (`::after`, or none for the submenu) shares the layer's containing block and
stacking context.

| Surface | Layer | Containing block of the layer | Stacking context |
|---|---|---|---|
| `wa-dialog::part(dialog)` | `::before` | the dialog (top layer, `position: fixed`) | top layer |
| `wa-dropdown::part(menu)` | `::before` | the dropdown's `wa-popup` `.popup` element: `#menu` is not positioned (verified: same box as the menu, padding 0). During the 50 ms show/hide animation (`scale` on `#menu`, `chunk.GTJS5N43.js:370,395`) `#menu` itself is the containing block (§10.3) | top layer (`popover="manual"`) |
| `wa-dropdown-item::part(submenu)` | `::after` (`::before` is the safe triangle) | the submenu (`position: absolute`, `[popover]`) | top layer, `z-index: 10` |
| `wa-select::part(listbox)` (every select except the five opt-in ones of §6.8) | `::before` | the select's `wa-popup` `.popup` element, after `position: static` on the list box (today `relative`; nothing inside Web Awesome's select depends on it) | top layer (`popover="manual"`) |
| `wa-popover::part(body)` | `::before` | the body, after `position: relative` (today not positioned) | `isolation: isolate` |
| `wa-tooltip::part(body)` | `::before` | the body, after `position: relative` | `isolation: isolate` |
| `.glass-surface` | `::before` | the panel (`position: relative` from the class unless the panel is already `absolute`/`fixed`) | `isolation: isolate` |
| `.Notivue__notification` | `::before` | the toast (Notivue sets `position: relative`) | `isolation: isolate` |
| `.glass-sticky` | `::before` | the sticky element (`position: sticky`) | `isolation: isolate` |
| `.glass-veil` | `::before` | the fixed host | the host's `z-index` |

The menu and the list box are scroll containers (`overflow: auto`): a layer anchored to
them would scroll away with their content. Anchoring it to the non-scrolling popup element
keeps it in place (verified for the menu: after `scrollTop = 300` the layer still covers
the whole menu; the list box works the same way once it is `position: static`, but its
popup takes the field's width, see below). The native
modal dialog has the UA's `overflow: auto`, but Web Awesome caps its height and scrolls its
`body` part, so the dialog itself does not scroll.

Direct `backdrop-filter`, no layer:

- **The arrows** (`wa-popover::part(popup__arrow)`, `wa-tooltip::part(base__arrow)`) have
  no children (§5.2).
- **Five selects with a wide list box, opt-in class `glass-listbox-direct` on the `wa-select`**, carry
  the glass directly on `::part(listbox)` (§5.1). `wa-select` renders its popup with
  `sync="width"` (`chunk.63CHHVSP.js:678`; `wa-popup` writes the anchor's width inline,
  `chunk.KGZRQJER.js:199-205`), so the popup-anchored layer is only as wide as the field.
  Two list boxes are wider than their field: the search overlay filters
  (`SearchOverlay.vue:845-852`, `.filter-select::part(listbox) { width: max-content;
  min-width: 100% }`; the four `.filter-select`s at `:551`, `:592`, `:604`, `:622`) and the
  peer message review actions (`PeerMessageReviewDialog.vue:1942-1946`,
  `.pr-actions__select` at `:1478`). The select's popup part is not exported, so it cannot
  be widened from outside. Those five selects take the class. Their options are plain:
  text, icons, project marks (`ProjectSelectOptions` without `show-process-indicator`,
  `SearchOverlay.vue:584`), `<small>` labels and dividers — no tooltip, dropdown or popover,
  so no fixed-position descendant is displaced (checked 2026-09-27). Every other select
  keeps the layer: some option lists do hold tooltips — the peer message review project
  selects render `AggregatedProcessIndicator` → `AppTooltip` inside their options
  (`PeerMessageReviewDialog.vue:1623`, `:1710`; `ProjectSelectOptions.vue:107`, `:131`,
  `:161`) — and those list boxes are not wider than their field.

### 5.1 The surfaces

```css
/* Tokens inside every glass surface. Children inherit through the flat tree (slotted
   content inherits from the part that holds its slot): fields and dividers follow. */
:where(wa-dialog)::part(dialog),
:where(wa-dropdown)::part(menu),
:where(wa-dropdown-item)::part(submenu),
:where(wa-select)::part(listbox),
:where(wa-popover)::part(body),
:where(.glass-surface) {
    --wa-form-control-background-color: var(--glass-field-bg);
    --wa-color-surface-border: var(--glass-border);
    /* Contextual row tokens, read (with their current values as fallbacks) by list rows
       that also live outside glass surfaces — today the file tree (§6.5). */
    --row-bg: transparent;
    --row-hover-bg: var(--glass-item-hover);
    --row-active-bg: var(--glass-item-highlight);
    /* Extra inset for controls near the glass edge, so their focus ring clears the border
       overlay and its light edge (§5.0 Geometry, §6.9). */
    --glass-edge-gap: calc(var(--wa-border-width-s) + 1px);
}

/* Layered surfaces: the layer draws background and blur; the border overlay draws the
   glass border and the top edge above the content (§5.0). */
:where(wa-dialog)::part(dialog),
:where(wa-dropdown)::part(menu),
:where(wa-dropdown-item)::part(submenu),
:where(wa-select:not(.glass-listbox-direct))::part(listbox),
:where(wa-popover)::part(body),
:where(.glass-surface) {
    background-color: transparent;
    border: 0;
    box-shadow: var(--glass-shadow);
}
:where(wa-select:not(.glass-listbox-direct))::part(listbox) {
    position: static;
}

/* Layer anchors (§5.0 table). Toasts join here (§7). */
:where(wa-popover)::part(body),
:where(.glass-surface),
:where(.Notivue__notification) {
    position: relative;
    isolation: isolate;
}

/* The glass layer. Two rules with the same declarations: a browser that cannot parse a
   pseudo-element after ::part() drops a whole rule whose selector list contains one, and
   our own panels and toasts must keep their layer there (see below). */
:where(wa-dialog)::part(dialog)::before,
:where(wa-dropdown)::part(menu)::before,
:where(wa-dropdown-item)::part(submenu)::after,
:where(wa-select:not(.glass-listbox-direct))::part(listbox)::before,
:where(wa-popover)::part(body)::before {
    content: "";
    position: absolute;
    inset: 0;
    z-index: -1;
    border-radius: inherit;
    background-color: var(--glass-bg);
    backdrop-filter: var(--glass-filter);
    -webkit-backdrop-filter: var(--glass-filter);
    pointer-events: none;
}
:where(.glass-surface)::before,
:where(.Notivue__notification)::before {
    /* The same nine declarations as the rule above. */
}

/* The border overlay, above the content. Split the same way. */
:where(wa-dialog)::part(dialog)::after,
:where(wa-dropdown)::part(menu)::after,
:where(wa-select:not(.glass-listbox-direct))::part(listbox)::after,
:where(wa-popover)::part(body)::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 100;
    border: var(--wa-border-width-s) solid var(--glass-border);
    border-radius: inherit;
    /* The top edge: an inset shadow paints inside the border, on the row just below it. */
    box-shadow: var(--glass-highlight);
    pointer-events: none;
}
:where(.glass-surface)::after,
:where(.Notivue__notification)::after {
    /* The same eight declarations as the rule above. */
}
/* The submenu's two pseudo-elements are taken (safe triangle, layer): inner outline, and
   the top edge on its layer (where it shares its row with the outline). */
:where(wa-dropdown-item)::part(submenu) {
    outline: var(--wa-border-width-s) solid var(--glass-border);
    outline-offset: calc(-1 * var(--wa-border-width-s));
}
:where(wa-dropdown-item)::part(submenu)::after {
    box-shadow: var(--glass-highlight);
}

/* The five opt-in select lists (two kinds of wide list box, §5.0): glass carried directly. */
:where(wa-select.glass-listbox-direct)::part(listbox) {
    background-color: var(--glass-bg);
    border-color: var(--glass-border);
    box-shadow: var(--glass-shadow), var(--glass-highlight);
    backdrop-filter: var(--glass-filter);
    -webkit-backdrop-filter: var(--glass-filter);
}

```

**Fallback without `::part()::before`** — the **last** rule block of `glass.css`, after
everything in §5.2–§5.5: its declarations have the same specificity as the ones they
replace (`@supports` adds no precedence), so they win only by coming later.

```css
/* Browsers that cannot style a pseudo-element after ::part() would show the Web Awesome
   surfaces with no background and no border at all (and a tooltip with white text on
   nothing): give them opaque fills, a real border, and opaque arrows. Verified in Chrome
   and Firefox: selector() returns true for `x::part(y)::before`, false for an unknown
   pseudo-element after ::part(). Both spellings are tested: the production minifier
   rewrites the rule selectors to the legacy `:before` but leaves this condition as
   written (checked by the round-11 reviewer), so the condition must cover the form that
   ships. */
@supports not (selector(:where(wa-dialog)::part(dialog)::before) and selector(:where(wa-dialog)::part(dialog):before)) {
    :where(wa-dialog)::part(dialog),
    :where(wa-dropdown)::part(menu),
    :where(wa-dropdown-item)::part(submenu),
    :where(wa-select:not(.glass-listbox-direct))::part(listbox),
    :where(wa-popover)::part(body) {
        background-color: var(--glass-tint);
        /* No layer is left to hide an inset shadow, so the surface carries its top edge. */
        box-shadow: var(--glass-shadow), var(--glass-highlight);
    }
    /* Their ::after border overlay is missing too (the submenu keeps its outline). */
    :where(wa-dialog)::part(dialog),
    :where(wa-dropdown)::part(menu),
    :where(wa-select:not(.glass-listbox-direct))::part(listbox),
    :where(wa-popover)::part(body) {
        border: var(--wa-border-width-s) solid var(--glass-border);
    }
    :where(wa-tooltip) {
        --wa-tooltip-background-color: var(--wa-color-text-normal);
    }
    :where(wa-tooltip)::part(body) {
        background-color: var(--wa-color-text-normal);
    }
    :where(wa-popover)::part(popup__arrow) {
        background-color: var(--glass-tint);
    }
}
```

The top edge sits on the border overlay, not on the surface: the dialog, submenu, popover
body, panels and toasts are the root of their own stacking context, so their own
`box-shadow` (inset top line included) paints below their negative-z children and the
layer would hide it. On the overlay, the inset shadow paints inside the overlay's border,
so the glass border and the light edge are two distinct rows (border, then edge), above the
content like the border. The five direct list boxes (`glass-listbox-direct`) keep Web
Awesome's border width and style (only the color changes) and draw their edge themselves —
the same two rows: the border outside the padding box, the inset edge just inside it. One
difference, accepted: their edge belongs to the list box's own background, so their
content paints over it (the scrollbar at the top right, a highlighted option scrolled to
the very top), where the overlay's edge stays above the content. The submenu is the other
exception: its edge is on its layer and shares the top row with its outline.

`border-radius: inherit` takes the host's radius (the host has no border, so its outer and
inner radii are the same). For the menu and the layered list boxes the containing block is
the popup element, whose box equals the surface's box (the list box is not wider than its
field outside the five opt-in selects). `position: relative` on `.glass-surface` is wrapped in
`:where()`: a panel whose own scoped rule sets `position: absolute` or `fixed` keeps it
(both are containing blocks too).

Toasts: the border overlay draws their glass border; the toast theme sets
`--nv-border-width: 0` so Notivue draws no second border (§7).

Facts this relies on (Web Awesome 3.3.1, `node_modules/@awesome.me/webawesome/dist`):

| Component | Part | Today |
|---|---|---|
| `wa-dialog` | `dialog` (a native `<dialog>` opened with `showModal()`) | `background-color: var(--wa-color-surface-raised)`, `border: none`, `box-shadow: var(--wa-shadow-l)` |
| `wa-dropdown` | `menu` | `surface-raised`, `border … var(--wa-color-surface-border)`, `var(--wa-shadow-m)` |
| `wa-dropdown-item` | `submenu` | same as the menu |
| `wa-select` | `listbox` | `surface-raised`, `surface-border`, `var(--wa-shadow-m)` |
| `wa-popover` | `body` | `surface-default`, `var(--wa-panel-border-width) solid var(--wa-color-surface-border)`, `var(--wa-shadow-l)` |

`--wa-shadow-m/l` already resolve to `--depth-3` (step 2); the explicit `box-shadow` is
`--glass-shadow`, the outer layers of level 3 (the top edge is the border overlay's).
`--wa-color-surface-border` inside the glass tints the dividers
(`wa-divider`'s default color) and every in-surface border that uses the token (search
header, picker search row, switcher header): they read as the glass border, as in the mock
(`.palette .pin-row, .pfoot, .snav` → `--glass-border`). It does **not** reach the popover
arrow: in `wa-popup` the arrow is a sibling of the slot that holds the popover body
(`dist/chunks/chunk.KGZRQJER.js:333-345`), so the arrow gets its border color explicitly
(§5.2).

**Nested popups.** An element with a `backdrop-filter` is a backdrop root for its
descendants. With the blur on a childless layer, no surface is an ancestor of another
surface's layer. Web Awesome's popups are also in the top layer: `wa-popup` renders
`popover="manual"` (`chunk.KGZRQJER.js:334`) and the dropdown submenu too
(`chunk.R6LWH55I.js:208`). So a select list inside a glass dialog or popover, or a submenu
inside a glass menu, blurs the page behind it. To check in the browser (§10.2).

### 5.2 The popover arrow and the tooltip

```css
:where(wa-popover)::part(popup__arrow) {
    background-color: var(--glass-bg);
    /* Web Awesome draws the arrow's bottom/right borders with --wa-color-surface-border,
       resolved outside the glass body (see §5.1): match the body's glass border. */
    border-color: var(--glass-border);
    backdrop-filter: var(--glass-filter);
    -webkit-backdrop-filter: var(--glass-filter);
}

:where(wa-tooltip) {
    /* Read by the arrow (--arrow-color). */
    --wa-tooltip-background-color: var(--glass-tooltip-bg);
    /* No border: the body's border is the background color today (the theme declares
       --wa-tooltip-border-color as var(--wa-tooltip-background-color), resolved on :root to
       the opaque color); a 0px width drops it and keeps the arrow geometry consistent (the
       arrow's --popup-border-width reads this token). 0px, never a unitless 0: the value
       feeds calc() next to lengths, where a bare number makes the arrow invalid. */
    --wa-tooltip-border-width: 0px;
}
/* The body: its color moves to the layer (§5.0). */
:where(wa-tooltip)::part(body) {
    background-color: transparent;
    position: relative;
    isolation: isolate;
}
:where(wa-tooltip)::part(body)::before {
    content: "";
    position: absolute;
    inset: 0;
    z-index: -1;
    border-radius: inherit;
    background-color: var(--glass-tooltip-bg);
    backdrop-filter: var(--glass-tooltip-filter);
    -webkit-backdrop-filter: var(--glass-tooltip-filter);
    pointer-events: none;
}
:where(wa-tooltip)::part(base__arrow) {
    backdrop-filter: var(--glass-tooltip-filter);
    -webkit-backdrop-filter: var(--glass-tooltip-filter);
}
```

Every theme sets `--wa-tooltip-border-width` to `var(--wa-border-width-s)`
(`themes/default.css:352`, `awesome.css:358`, `shoelace.css:358`). Web Awesome reads it for
the body border, the arrow's two borders and the popup's `--popup-border-width` (arrow
offset) (`chunk.TKL7YZKI.js:52`, `:59`, `:62-63`), so the host-level `0px` changes all three
together; the tooltip keeps no visible border, as today (its border was the background
color).

Verified in the browser on 5174 (2026-09-27): a `backdrop-filter` on
`wa-tooltip::part(base__arrow)` applies and is clipped by the arrow's own `clip-path`. The
tooltip's `--arrow-color` is `var(--wa-tooltip-background-color)` resolved on the tooltip
host, so it follows the override.

**The arrow joint.** Web Awesome's arrow clip keeps a strip past the arrow's base line
(`calc(var(--arrow-base-offset) - 2px)`, `chunk.NUEKQX75.js:69-77`), and the arrow
(`position: absolute; z-index: 3` in the `.popup` stacking context) paints over the body.
With two translucent fills, that strip reads as a denser line along the joint: about 1.4px
deep with a 0 border width, more with the popover's b (`--popup-border-width:
var(--wa-panel-border-width)`, `chunk.4GV5JLFP.js`, `.popover`; `--wa-panel-border-width`
is `var(--wa-border-width-s)` in all three themes, `default.css:342`, `awesome.css:348`,
`shoelace.css:348`: 1px, 2px in awesome). Measured by the round-8
reviewer on a headless Chrome replica of the Web Awesome arrow with 50 % fills: 2 rows at
double density with a 0 border width, 3 rows with 1px, 0 rows with a clip at the base
diagonal. So both arrows are clipped at their base diagonal, pushed 1px into the body, and
the popover's arrow offset is set to 0px like the tooltip's (the body has no border of its
own any more). Why the 1px: clipped exactly on the diagonal, Firefox antialiases the
arrow's first row to half coverage and whatever is behind shows through as a line (user
report, 2026-09-27; measured in headless Firefox through Selenium on 5174: first arrow row
202 against 229 inside the arrow). The 1px push (about 0.7px along the diagonal's normal)
covers that row (228 against 229) without a denser strip on the body side (245 as the rest
of the body), and Chrome stays seamless (checked on 5174):

```css
:where(wa-tooltip)::part(base__arrow),
:where(wa-popover)::part(popup__arrow) {
    clip-path: polygon(calc(0% - 1px) 100%, 100% calc(0% - 1px), 100% 100%);
}
:where(wa-popover)::part(popup) {
    --popup-border-width: 0px;
}
```

**Units.** Both zero widths are written `0px`, never a unitless `0`. `wa-popup` feeds
`--popup-border-width` into `calc()` next to lengths (`--arrow-base-offset`,
`--arrow-size-diagonal`, `chunk.NUEKQX75.js:26-30`, and the inline static-side offset,
`chunk.KGZRQJER.js:314`); a bare number there makes every dependent arrow property invalid
at computed-value time. Checked by the round-9 reviewer in headless Chrome with Web Awesome
3.3.1: with `0` the tooltip arrow computes to `width: 0px` and no clip, the popover arrow to
1×1px; with `0px` both render (8.48px). Web Awesome itself writes `0px`
(`chunk.NUEKQX75.js:9`).

**The border cut under the popover arrow.** With the offset at 0px, the arrow's base sits
on the body's outer edge, and the body's border overlay (and its light edge on the top
side) runs along that edge: seen through the translucent arrow, it would draw a b-wide line
closing the arrow's base (user review, 2026-09-27: rejected — the arrow must merge with the
body, and the border must stay everywhere else). The overlay is therefore masked under the
arrow's base:

- `frontend/src/utils/glassArrowGap.js`: one document listener on Web Awesome's
  `wa-reposition` event (dispatched by `wa-popup` after each placement,
  `chunk.KGZRQJER.js`, end of `reposition()`; the event bubbles and is composed,
  `chunk.ZWQCGLB5.js:6`). For a `wa-popover` host (the event's first composed-path node
  is the `wa-popup`, its root's host the popover), it measures the body part and the arrow
  part (`getBoundingClientRect`), divides by the body's on-screen scale (screen width /
  `offsetWidth`, below 1 during the show animation) and publishes on the host
  `data-glass-arrow="top|bottom|left|right"` (the body side the arrow sits on:
  placement `bottom*` → `top`, `top*` → `bottom`, `left*` → `right`, `right*` → `left`)
  and `--glass-arrow-gap-start` / `--glass-arrow-gap-end` (the span of the arrow's
  bounding box along that edge — the rotated square's bounding box spans the triangle's
  base — in body pixels, clamped to the body). The pure computation is
  `computeArrowGap(placement, bodyRect, arrowRect, scale)`, tested by
  `frontend/src/utils/glassArrowGap.test.js`. `installGlassArrowGap()` is called once from
  `frontend/src/main.js` and `frontend/src/share-session/main.js` (the artifact shell has no
  popover).
- `glass.css`: `:where(wa-popover[data-glass-arrow])::part(body)::after` gets a two-layer
  mask — a band of `calc(var(--wa-border-width-s) + 1px)` (the border plus the light-edge
  row) along the arrow's side, transparent between the two variables, opaque elsewhere,
  plus an opaque layer for the rest of the box. One rule per side sets the gradient
  direction, size and position. The rules hold only `::part(…)::after` selectors
  (homogeneous lists, §5.1).

Verified on 5174 (2026-09-27, settings popover opened by its trigger): the host gets
`data-glass-arrow="bottom"` and the arrow's span, and the overlay's computed mask matches.
A popover opened programmatically without its anchor never repositions, so it keeps no gap
(no arrow is shown there either). In the no-`::part()::before` fallback (§5.1) the overlay
does not exist and the real border stays under the arrow: accepted for that fallback. The
tooltip has no border, so no line crosses its arrow.

The clip is in the arrow's own coordinates, so it follows Web Awesome's per-placement
rotation. `wa-popover` exposes its `wa-popup` as the `popup` part
(`chunk.QJBP6HBR.js:214-218`); a document rule beats the shadow rule `.popover {
--popup-border-width: … }`. (Not `--wa-panel-border-width: 0` on the host: it would
inherit into slotted callouts, `wa-details` and cards, which read that token for their
own borders.) The joint is still checked at the gate for a hairline gap (§10.2).

### 5.3 The modal veil

```css
:root {
    --wa-color-overlay-modal: var(--glass-veil);
}
:where(wa-dialog)::part(dialog)::backdrop {
    backdrop-filter: var(--glass-veil-filter);
    -webkit-backdrop-filter: var(--glass-veil-filter);
}
```

`--wa-color-overlay-modal` is what Web Awesome's `.dialog::backdrop` and the native
`dialog::backdrop` (`dist/styles/native.css`) paint; the themes declare it in
`@layer wa-theme` on `:root` and `.wa-dark` — the unlayered `:root` declaration wins on
`<html>`. Verified in the browser on 5174: `wa-dialog::part(dialog)::backdrop` accepts
`backdrop-filter` from the document (computed `blur(6px)` read back, blur visible).
Placed in the file after the `.wa-dark` block.

### 5.4 Highlighted rows in Web Awesome menus and lists

Today `wa-dropdown-item` hover/focus-visible and `wa-option` hover use
`--wa-color-neutral-fill-normal` (grey). The current `wa-option` keeps its solid accent
(`brand-fill-loud`); danger items keep their danger fill.

```css
@media (hover: hover) {
    :where(wa-dropdown-item:not([variant='danger'], [disabled], :state(disabled)):hover),
    :where(wa-option:not([disabled], :state(current)):is(:hover, :state(hover))) {
        background-color: var(--glass-item-highlight);
    }
}
:where(wa-dropdown-item:not([variant='danger']):focus-visible) {
    background-color: var(--glass-item-highlight);
}
```

A document rule on the host beats the component's own `:host(...)` rule for normal
declarations (the outer context wins), so no `!important` is needed. `:state()` matches
custom states from the document. The option's hover text color
(`--wa-color-neutral-on-normal`) is kept.

### 5.5 Collapsible sections inside glass

`wa-details` defaults to `appearance="outlined"`, whose `base` part paints
`background-color: var(--wa-color-surface-default)` (white in light): an opaque card in the
glass. It appears inside glass surfaces through `HybridModeExplainer.vue:27` (hybrid mode
announcement dialog `HybridAnnouncementDialog.vue:78`, the composer's hybrid dialog
`MessageInput.vue:2246`, the settings popover via `ProviderSettingsSection.vue:173`) and
`MarkdownContent.vue:762` (table of contents, in markdown shown by glass dialogs and the
commit popover). Inside glass it takes the resting-row token:

```css
:where(wa-dialog, wa-popover, .glass-surface) :where(wa-details:not([appearance='filled'], [appearance='filled-outlined'], [appearance='plain']))::part(base) {
    background-color: var(--glass-item-rest);
}
```

The ancestors are matched in the light DOM (content slotted into the dialog or popover),
so the chat's tool cards (`wa-details.item-details`, not inside those ancestors) are not
affected. `wa-card` is not rendered inside any glass surface today (its users are home,
stats, login and the question widget).

## 6. Our own overlays

### 6.1 The shared classes (in `glass.css`)

- **`.glass-surface`** — listed in §5.1: the full glass surface for our own panels. The
  class takes part in five §5.1 rules: the token rule (field, border and row tokens), the
  layered-surface rule, the layer anchor, the `::before` layer and the `::after` border
  overlay.
- **`.glass-sticky`** — a sticky header inside a glass surface. The component keeps its own
  `position: sticky`; the class adds the stacking context and the layer:
  ```css
  :where(.glass-sticky) {
      isolation: isolate;
  }
  :where(.glass-sticky)::before {
      content: "";
      position: absolute;
      inset: 0;
      z-index: -1;
      background-color: var(--glass-sticky-bg);
      backdrop-filter: var(--glass-filter);
      -webkit-backdrop-filter: var(--glass-filter);
      pointer-events: none;
  }
  ```
  The layer blurs what is painted under the header: the rows scrolling under it and the
  surface's own glass layer.
- **`.glass-veil`** — a veil painted by a `::before` layer, for a fixed full-screen
  element:
  ```css
  :where(.glass-veil)::before {
      content: "";
      position: absolute;
      inset: 0;
      z-index: -1;
      background-color: var(--glass-veil);
      backdrop-filter: var(--glass-veil-filter);
      -webkit-backdrop-filter: var(--glass-veil-filter);
      opacity: var(--glass-veil-opacity, 1);
      pointer-events: none;
  }
  ```
  Why a pseudo-element, besides §5.0:
  - An element with a `backdrop-filter` is a backdrop root for its descendants. If a veil
    element whose children include a glass panel carried the blur itself (the session
    switcher overlay), the panel would blur only the veil, never the page. The `::before`
    is not an ancestor of the panel.
  - For the mobile drawer veil (an empty `<label>`, no child panel) and the share
    sub-agent drawer, the class keeps the blur in `glass.css` (invariant 1) and gives the
    mobile open/close fade through the `::before`'s `opacity` (`--glass-veil-opacity`,
    §6.3).

  The host must be positioned and form a stacking context (all three hosts in §6.3 are
  `position: fixed` with a `z-index`), so `z-index: -1` paints the layer above the host's
  own background and below its children.

**Only `glass.css` declares `backdrop-filter`** (pinned by a test, §10), and only on
childless boxes — pseudo-elements (`::before`, `::after`, `::backdrop`) and the two arrow
parts — plus the opt-in `glass-listbox-direct` select lists (§5.0). Every component uses one of these classes or the global rules, and every
declaration uses a `--glass-*-filter` token. This keeps the reduced-transparency switch
(§4.1) effective everywhere, keeps fixed descendants anchored to the viewport (§5.0) and
keeps the forbidden containers (§3) out of reach.

The test is a plain text search (§10.1 test 3), comments included: comments written in
component files for this step (e.g. the mobile drawer veil in `ProjectView.vue`, the
Notivue list clip in `App.vue`) say "blur layer" or "blur", never the property name
`backdrop-filter`.

### 6.2 Panels that take `.glass-surface`

Add the class in the template and **delete** the panel's own `background`, `border` and
`box-shadow` declarations (a scoped `.x[data-v-…]` rule beats `:where(.glass-surface)`).
Keep size, radius, padding, layout and overflow declarations.

| Surface (what the user sees) | Element | Today |
|---|---|---|
| Composer slash-command picker | `CommandPickerPopup.vue` `.picker-panel` (template `:412`, CSS `:471-481`) | `surface-default`, `1px surface-border`, `--wa-shadow-l` |
| Composer message-history picker | `MessageHistoryPickerPopup.vue` `.picker-panel` (`:373`, CSS `:427-437`) | same |
| Composer `@` file picker | `FilePickerPopup.vue` `.picker-panel` (`:344`, CSS `:411-421`) | same |
| Directory picker (project, worktree, workspace dialogs) | `DirectoryPickerPopup.vue` `.picker-panel` (`:316`, CSS `:371-381`) | same |
| Session switcher (Ctrl+Tab) | `SessionSwitcher.vue` `.switcher-panel` (`:92`, CSS `:159-169`) | `surface-default`, `surface-border`, `--wa-shadow-l` |
| Comment on a text selection | `TextSelectionComment.vue` `.tsc-panel` (`:389`, CSS `:466-479`) | `surface-default`, `1px surface-border`, `--wa-shadow-l` |
| Agent settings hover panel | `HoverInfoPanel.vue` `.hover-info-panel` (`:58`, CSS `:67-84`) | `surface-raised`, `surface-border`, `--wa-shadow-l` |
| Usage graph hover tooltip | `UsageGraphDialog.vue` `.usage-chart-tooltip` (`:1004`, `:1111`, CSS `:1417-1430`) | `surface-raised`, `--depth-2` |
| Project stats sparkline tooltip (project detail → Stats tab; `ContributionSparklines` is rendered only by `ContributionGraphs.vue:291`, itself in `ProjectDetailPanel.vue:463`) | `ContributionSparklines.vue` `.sparkline-tooltip` (`:556`, `:622`, CSS `:725-738`) | `surface-raised`, `--depth-2` |

The two chart tooltips move from level 2 to level 3 (`--depth-3`): they are floating
layers now, like every other glass surface. The usage-graph tooltip sits inside a glass
dialog: its layer blurs the chart under it (and the dialog's glass behind the chart).

**The share viewer's sub-agent drawer panel stays opaque**
(`share-session/SharedSubagentView.vue` `.subagent-panel`, `:21`, CSS `:40-42`). It is a
full-height transcript container, not a transient overlay: the glass surface rule would
restyle its transcript (the §5.1 token overrides and the §5.5 `wa-details` rule reach the
tool, thinking and result cards rendered by `ShareItemsList` → `SessionItem`), so the same
transcript would look different in the drawer and on the share page. Only its veil becomes
glass (§6.3). The same reasoning keeps the SPA's panels opaque (§3).

### 6.3 Veils that take `.glass-veil`

| Veil | Element | Change |
|---|---|---|
| Session switcher | `SessionSwitcher.vue` `.switcher-overlay` (`:91`, CSS `:148-157`) | Add `glass-veil`; delete `background: rgba(0,0,0,.4)` |
| Share sub-agent drawer | `SharedSubagentView.vue` `.subagent-drawer` (the fixed root, `z-index: 20`) | Add `glass-veil` on the drawer root; delete `.subagent-backdrop`'s `background` (the element stays, as the click target) |
| Mobile sidebar drawer | `ProjectView.vue` `.sidebar-backdrop` (`:2667`, mobile CSS `:3785-3797`, open state `:3815-3818`) | Add `glass-veil`. The label is `display: block` on mobile even while the drawer is closed, and an `opacity: 0` box with a `backdrop-filter` is still composited (checked by the round-9 reviewer: CDP `LayerTree` keeps a `BackdropFilter` layer at opacity 0; `visibility: hidden` removes it), so the closed state also hides the layer. Closed (base mobile rule): `--glass-veil-opacity: 0` and `.sidebar-backdrop::before { visibility: hidden; transition: opacity var(--transition-duration, .3s) ease, visibility 0s linear var(--transition-duration, .3s); }` (the layer stays visible during the fade-out, then disappears). Open (the `:has(:checked)` rule): `--glass-veil-opacity: 1` instead of `background: rgba(0,0,0,.5)`, and `.sidebar-backdrop::before { visibility: visible; transition-delay: 0s, 0s; }`. The base `background: transparent` stays; its `background` transition goes |

The desktop `.sidebar-backdrop` rule (`ProjectView.vue:3677`) is `display: none` today and
is not changed; the `::before` does not render while its host is not displayed.

### 6.4 Command palette and search overlay

Both are `wa-dialog`s: the global rule gives them the glass. Their own opaque backgrounds go:

- `CommandPalette.vue`: delete `wa-dialog { background: … }` (`:772-775`, keep `--width`),
  the `::part(body)` background (`:778`, keep `padding: 0`), and the dead
  `wa-dialog::part(overlay)` rule (`:781-783`, Web Awesome 3 has no `overlay` part: the veil
  is §5.3).
- `CommandPalette.vue` sticky group header: `.category-sticky` (`:524`, `:623`, CSS
  `:854-861`) takes `.glass-sticky` and loses its `background`; `.category-label` (CSS
  `:862-872`) loses its `background` (it sits inside the sticky wrapper). The stuck-state
  shadow (`@container scroll-state(stuck: top)`) stays.
- `SearchOverlay.vue`: delete the `::part(body)` background (`:763`, keep `padding: 0`) and
  the dead `::part(overlay)` rule (`:767-769`).

### 6.5 Rows highlighted inside glass surfaces

| Row | Today | New |
|---|---|---|
| Palette keyboard-selected command `.command-item.active` (`CommandPalette.vue:919-921`) | `surface-lowered` | `var(--glass-item-highlight)` |
| Palette breadcrumb back `.breadcrumb-back:hover` (`:828-830`) | `surface-lowered` | `var(--glass-item-hover)` |
| Palette breadcrumb back pressed `.breadcrumb-back:active` (`:832-834`) | `surface-border` (inside the glass it would resolve to `--glass-border`, fainter than the new hover) | `var(--glass-item-highlight)` |
| Search keyboard-selected result `.search-result-card.selected` (`SearchOverlay.vue:887-889`) | `surface-lowered` | `var(--glass-item-highlight)` |
| Switcher active row `.switcher-row--active` (`SessionSwitcher.vue:223-226`) | `brand-fill-quiet` | `var(--glass-item-highlight)`, text color kept |
| Picker rows, `CommandPickerPopup.vue` and `MessageHistoryPickerPopup.vue`: `.picker-item:hover` (`:532`, `:485`) | `surface-raised` (white in light) | `var(--glass-item-hover)` |
| Same files, `.picker-item.active` (`:536`, `:489`) | `surface-lowered` | `var(--glass-item-highlight)` |
| Peer inbox clickable row `.pi-row--clickable:hover` (`PeerInboxDialog.vue:372`) | `surface-raised` | `var(--glass-item-hover)` |
| Settings nav `.settings-nav-item:hover` (`SettingsPopover.vue:2397-2399`) | `var(--wa-color-surface)` (undefined: no hover today) | `var(--glass-item-hover)` (the mock's settings-nav hover) |
| File tree rows `.node-label` (`FileTree.vue:461-462` resting, `:465-467` hover, `:473-479` selected-hover and keyboard-focused) — shown in the `@` file picker, the directory picker and the file move dialog, all glass | `--node-bg-color`: `surface-default` resting (opaque), `surface-raised` hover, `surface-lowered` focused | `--node-bg-color: var(--row-bg, var(--wa-color-surface-default))`, hover `var(--row-hover-bg, var(--wa-color-surface-raised))`, selected-hover and focused `var(--row-active-bg, var(--wa-color-surface-lowered))`. The `--row-*` tokens are set only inside glass surfaces (§5.1), so the Files and Git panes keep today's values |
| Peer inbox message rows `.pir:hover` (`PeerInboxRow.vue:267`; the component is used only in `PeerInboxDialog.vue:281`, `:292`) | `surface-raised` | `var(--glass-item-hover)` |
| Settings → Tips rows `.tips-row` (`TipsSettings.vue:164` resting, `:167-171` hover and focus-visible) and Settings → Help rows `.help-row` (`HelpSettings.vue:96` resting, `:99-103` hover and focus-visible); both components are rendered only by `SettingsPopover.vue` (`:2078`, `:2080`; imported at `:34-35`) | resting `surface-lowered` (opaque grey card), hover `surface-default` (white) | resting `var(--glass-item-rest)`, hover and focus-visible `var(--glass-item-hover)` |

The file tree's sticky git flag (`FileTree.vue:523-531`, `background-color:
var(--node-bg-color)`) only renders in git mode (Files/Git panes, never inside a glass
surface), so a transparent `--row-bg` never reaches it.

### 6.6 Settings popover, mobile sticky back row

`SettingsPopover.vue`: on narrow screens (`@media (width < 640px)` from `:2432`), the detail
panel's back row `.settings-detail-header` (template `:1336`, mobile CSS `:2461-2477`) is
sticky with `background: var(--wa-color-surface-default)`. On wider screens the row is not
displayed at all (desktop CSS `:2426-2428`, `display: none`). The element takes
`.glass-sticky` in the template and the mobile rule loses its `background` declaration. Its
comment (`:2466-2471`) is rewritten: the row now blurs what scrolls under it instead of
masking it.

That comment also relies on the opaque row to hide the detail panel's **top scroll
shadow**: `.settings-detail::before` (`:2510-2531`, sticky `top: 0`, `z-index: 2`, a
`--_shadow-color` gradient) sits under the stuck row (`z-index: 3`) and is at opacity 1
whenever the panel is scrolled from the top (`@container scroll-state(scrollable: top)`,
`:2550-2555`). Through the glass row it would show as a dark band. On narrow screens the
stuck back row already marks that the panel is scrolled, so the mobile block of the scroll
shadows (`@media (width < 640px)` inside `@supports (container-type: scroll-state)`,
`:2564-2569`) adds `.settings-detail::before { visibility: hidden; }` — not `display: none`:
the pseudo-element is an in-flow 16px sticky box (`display: block; height: 16px`, in a
non-flex `.settings-detail`), and removing it would move the back row up 16px at rest
(checked in headless Chrome by the round-8 reviewer). The bottom shadow and
the nav shadows are unchanged.

### 6.7 Native text fields inside glass

Web Awesome fields follow `--glass-field-bg` through `--wa-form-control-background-color`
(§5.1). One native `<input>` inside a glass dialog paints its own opaque background: the
key capture field of the terminal combos dialog, `TerminalCombosDialog.vue` `.key-capture-input`
(template `:377`, CSS rule from `:677`, `background: var(--wa-color-surface-raised)` at
`:679`). A sweep of every `.vue` file that renders a native `<input>`, `<textarea>` or
`<select>` found no other rule whose selector names an input, textarea, select, field or
editor and paints an opaque `--wa-color-surface-*` / `neutral-*` background. It becomes
`background: var(--wa-form-control-background-color)`, which resolves to `--glass-field-bg`
inside the dialog (and to the theme's field color anywhere else).

### 6.8 Opt-in direct list boxes

The class `glass-listbox-direct` is added to the five `wa-select`s whose list box is wider
than their field (§5.0): the four `.filter-select`s of `SearchOverlay.vue` (`:551`, `:592`,
`:604`, `:622`) and `.pr-actions__select` in `PeerMessageReviewDialog.vue` (`:1478`).
Nothing else changes in those components.

### 6.9 Filter fields near the glass edge

Four rules read the contextual `--glass-edge-gap` (§5.0 Geometry, §5.1 token rule):

| Rule | Today | New |
|---|---|---|
| `CommandPickerPopup.vue` `.picker-search` (`:490-491`) | `padding: var(--wa-space-2xs)` | `padding: calc(var(--wa-space-2xs) + var(--glass-edge-gap, 0px))` |
| `MessageHistoryPickerPopup.vue` `.picker-search` (`:446-447`) | same | same change |
| `FileTreePanel.vue` `.files-search` (`:1504-1505`) | same | same change (6px — 7px in the awesome theme — in the `@` file picker — the only glass surface where the filter row renders, since among glass hosts only `FilePickerPopup.vue:357` passes `search-fn`; 4px in the Files and Git panes, which are not glass) |
| `DirectoryPickerPopup.vue` `.picker-header` (`:388-395`) | `padding: var(--wa-space-2xs) var(--wa-space-xs)` | `padding: calc(var(--wa-space-2xs) + var(--glass-edge-gap, 0px)) var(--wa-space-xs)` (the 8px sides already clear the ring) |

`FilePickerPopup.vue`'s `.picker-header` (`:428-435`) holds only the path text, no
focusable control: unchanged.

## 7. Toasts — `App.vue`

1. The `<div class="toast-invert wa-invert">` wrapper inside `<Notivue v-slot>`
   (`App.vue:868-873`) is removed: toasts take the page scheme. `.toast-invert` CSS
   (`:878-880`, with its comment at `:877`) and the template comment above `<Notivue>` go with it.
1b. `<Notivue v-slot="item">` (`App.vue:868`) gets `:styles="{ list: { clipPath: 'none' } }"`.
   Notivue puts an inline `clip-path: inset(…)` on its fixed `<ol>`
   (`notivue/dist/index.js:848-860`, applied at `:986`, where `props.styles.list` is merged
   after it). A `clip-path` on an ancestor makes it a backdrop root, so every toast's glass
   layer would blur only the empty list, not the page (checked in headless Chrome by the
   round-7 reviewer: a `backdrop-filter` layer under a fixed `ol` with that clip shows no
   blur; without the clip it blurs). The clip only cuts what overflows below the viewport
   bottom (top-aligned list: negative insets on three sides, `0px` at the bottom), which is
   off-screen anyway, so removing it changes nothing visible.
2. `toastTheme` (`App.vue:766-778`) becomes:
   ```js
   const toastTheme = computed(() => {
       const isDark = settingsStore.getEffectiveColorScheme === COLOR_SCHEME.DARK
       return {
           // Type accents (success, error, …) readable on the scheme's own glass.
           ...(isDark ? slateTheme : lightTheme),
           '--nv-width': '100%',
           '--nv-min-width': '30rem',
           // The glass is painted by the layer below (§5.0), not by the toast itself.
           '--nv-global-bg': 'transparent',
           '--nv-global-fg': 'var(--wa-color-text-normal)',
           // The border overlay draws the glass border (§5.1); slateTheme's 1px border goes.
           '--nv-border-width': '0',
           // Outer shadow only: the border overlay draws the top edge (§5.1).
           '--nv-shadow': 'var(--glass-shadow)',
       }
   })
   ```
   Notivue paints `background-color: var(--nv-bg, var(--nv-global-bg))`, the border with
   `--nv-border-width` / `--nv-global-border`, and `box-shadow: var(--nv-shadow), inset
   <tip> var(--nv-accent)`; the theme object is applied inline on `.Notivue__notification`
   (both `Notification` and `CustomNotification`), so the `var()` references resolve there,
   with the page scheme. No theme in use sets per-type `--nv-*-bg`, so every type is
   transparent and shows the layer.
3. In `glass.css`, `:where(.Notivue__notification)` joins the §5.1 **layer anchor** rule
   (`position: relative` — Notivue already sets it — and `isolation: isolate`) and
   `:where(.Notivue__notification)::before` joins the §5.1 **layer** rule (background,
   blur) and `:where(.Notivue__notification)::after` joins the §5.1 **border overlay** rule
   (glass border, top edge). It does **not** join the §5.1 layered-surface rule: the toast's shadow,
   its absent border and its transparent background come from the theme object above.
4. Content components (`ProviderAuthToastContent`, `ProviderStatusToastContent`,
   `TipToast`, `McpToast`, `McpSecurityToast` — pushed by `McpManager.vue:165`, no style
   block —, `PeerToastContent`, `SessionToastContent`) use semantic tokens
   only (checked: `ProviderAuthToastContent.vue:122-123` warning fill/on-normal,
   `PeerToastContent.vue:155` `brand-on-quiet`, `ProviderStatusToastContent.vue:71`
   `inherit`): they follow the page scheme without change.
5. Consequences in `depth.css`: the button exclusion `.wa-invert wa-button` (toasts had an
   inverted box) now only concerns tooltip content; buttons inside toasts become raised like
   every other button. The comments that name toasts as an inverted box (the
   `--wa-shadow-*` mapping comment and the button-rule comment) are updated; the selectors
   stay (tooltips remain inverted).

## 8. Imports

`glass.css` is imported right after `depth.css` in:
- `frontend/src/main.js` (after `import './styles/depth.css'`, before `surfaces.css`);
- `frontend/src/share-session/main.js` (after `import '../styles/depth.css'`);
- `frontend/src/artifact-shell/main.js` (after `import '../styles/depth.css'`).

`installGlassArrowGap()` (`utils/glassArrowGap.js`, §5.2) is imported and called once in
`frontend/src/main.js` and `frontend/src/share-session/main.js`.

The share and artifact-shell bundles are not HMR'd: `cd frontend && npm run build` after the
change (CLAUDE.md).

## 9. Invariants

1. `backdrop-filter` / `-webkit-backdrop-filter` (and `backdropFilter` /
   `WebkitBackdropFilter` in inline styles) appear in `glass.css` only; each value is a
   `var(--glass-…-filter)` token (so §4.1 turns every blur off); and they sit only on
   childless boxes — pseudo-elements (`::before`, `::after`, `::backdrop`) and the two
   arrow parts — plus the list box of the `wa-select`s that carry the opt-in class
   `glass-listbox-direct` (five selects whose options hold no tooltip, dropdown or popover,
   §5.0); never on another element with descendants.
2. `glass.css` never names `.main-content`, `.session-layout`, `.center-slot`,
   `.dock-region`, `.layout-overlay`, `.frame-host`, `.file-pane-preview--fullscreen` or
   `.browser-pane--fullscreen`, and no template adds `glass-surface`, `glass-sticky` or
   `glass-veil` to those elements or to an ancestor of them.
3. The tint is derived from palette steps by `color-mix`, never by an `oklch(…)` with a fixed
   chroma on the accent hue (gray accent stays gray).
4. `--glass-bg`, `--glass-sticky-bg`, `--glass-field-bg` and `--glass-tooltip-bg` are
   translucent in normal conditions and opaque under `prefers-reduced-transparency: reduce`
   and without `backdrop-filter` support; every `--glass-*-filter` is `none` there. The item
   tokens and the veil color stay translucent on purpose (§4.1).
5. Toasts are not inside a `.wa-invert` box.
6. The light canvas, the panel cards and every step-1/step-2 token are untouched.

## 10. Testing

### 10.1 Node test — new `frontend/src/styles/glass.test.js`

Same style as `depth.test.js` (parses the CSS as text). `depth.test.js` exports nothing and
its `parseTopLevelBlocks` skips at-rules, so its helpers are copied and completed with an
at-rule-aware reader (the `@media` / `@supports` blocks of test 2 are read with their inner
`:root` block). The declarations of "`:root`" mean the merge of every top-level block whose
selector is exactly `:root` (the tokens block of §4 and the `--wa-color-overlay-modal`
block of §5.3), not the first one only. It pins:

1. **Tokens**: `:root` declares every `--glass-*` token of §4; `.wa-dark` overrides
   `--glass-tint`, `--glass-border`, `--glass-highlight`, `--glass-shadow`, `--glass-veil`; `--glass-bg`,
   `--glass-sticky-bg`, `--glass-field-bg`, `--glass-item-*`, `--glass-veil`,
   `--glass-tooltip-bg` each contain `transparent` (translucent); `--glass-tint` (both
   schemes) contains `color-mix(` and `--wa-color-brand-` and no `oklch(`.
2. **Fallbacks**: the `@media (prefers-reduced-transparency: reduce)` block and the
   `@supports not (…backdrop-filter…)` block each set `--glass-bg` and `--glass-sticky-bg`
   to `var(--glass-tint)`, `--glass-field-bg` to `var(--wa-color-surface-default)`,
   `--glass-tooltip-bg` to `var(--wa-color-text-normal)`, and the three `--glass-*-filter`
   tokens to `none`; both blocks come after the `.wa-dark` block.
3. **Blur only here, only through tokens, only on childless boxes**: scanning every `.css`,
   `.vue`, `.js` and `.ts` file under `frontend/src` (excluding `*.test.js`), the
   case-insensitive patterns `backdrop-filter` and `backdropfilter` (the latter catches
   `backdropFilter` and `WebkitBackdropFilter` style keys) occur in `styles/glass.css`
   only (a plain text search over the other files). In `glass.css`, every
   `backdrop-filter` and `-webkit-backdrop-filter` declaration value is
   `var(--glass-filter)`, `var(--glass-veil-filter)` or `var(--glass-tooltip-filter)`; the
   file has as many `-webkit-backdrop-filter:` as unprefixed `backdrop-filter:`
   declarations; and every comma-separated selector of a rule that declares one ends with
   `::before`, `::after`, `::backdrop`, `::part(popup__arrow)`, `::part(base__arrow)`, or
   is exactly `:where(wa-select.glass-listbox-direct)::part(listbox)`.
   These checks read declarations inside rule blocks only: the `@supports` prelude of §4.1
   (`backdrop-filter: blur(1px)`) is a condition, not a declaration, and is skipped.
4. **Forbidden containers**: `glass.css` contains none of the class names of invariant 2;
   the `glass-surface`, `glass-sticky` and `glass-veil` classes appear, across all `.vue`
   files, only in the files listed in §6 (`CommandPickerPopup`, `MessageHistoryPickerPopup`,
   `FilePickerPopup`, `DirectoryPickerPopup`, `SessionSwitcher`, `TextSelectionComment`,
   `HoverInfoPanel`, `UsageGraphDialog`, `ContributionSparklines`, `SharedSubagentView`,
   `ProjectView`, `CommandPalette`, `SettingsPopover`); the `glass-listbox-direct` class
   appears only on `wa-select` elements, in `SearchOverlay.vue` (four) and
   `PeerMessageReviewDialog.vue` (one, `.pr-actions__select`), so a new direct list box
   forces a look at its options (they must hold no tooltip, dropdown or popover, §5.0). An allow-list is used because a
   text test cannot tell whether a class lands on an ancestor of a pane; adding a glass
   class to a new file then fails the test and forces a look at that file.
   `ProjectView.vue` hosts `.main-content`: there the test also checks that the class
   token `glass-veil` appears exactly once, on the `<label … class="sidebar-backdrop …">`
   element, and that the class tokens `glass-surface` and `glass-sticky` appear zero
   times. Class tokens are read from the templates' `class="…"` and `:class` attribute
   values only, split on whitespace (or taken as quoted string literals in `:class`), so CSS
   custom properties such as `--glass-veil-opacity` in the `<style>` block are never
   counted.
5. **Mapping**: the §5.1 token selector list (six surfaces); the layered-surface list
   (dialog, menu, submenu, `wa-select:not(.glass-listbox-direct)` list box, popover body,
   `.glass-surface`) with `background-color: transparent`, `border: 0` and `box-shadow:
   var(--glass-shadow)`; the layered list box's `position: static`; the layer selectors
   (each layered surface has its layer, plus `.Notivue__notification`), in two rules —
   the Web Awesome `::part(…)::before/::after` ones, and `.glass-surface::before` with
   `.Notivue__notification::before` — both setting `background-color: var(--glass-bg)`,
   `z-index: -1`, no `border` and no `box-shadow`; the border-overlay selectors (the
   layered surfaces except the submenu, plus `.Notivue__notification`, all `::after`),
   split the same way, both setting `z-index: 100`, `border: … var(--glass-border)` and
   `box-shadow: var(--glass-highlight)`; the submenu layer's own `box-shadow:
   var(--glass-highlight)`; and, over the whole file, **every selector list is homogeneous**: either
   only selectors without a pseudo-element after `::part(…)`, or only
   `::part(…)::before` / `::part(…)::after` selectors, and a `::part(…)::backdrop` (or any
   other pseudo-element after `::part(…)`) selector always stands alone in its rule (one
   unsupported selector drops the whole rule, §5.1; §11 expects browsers without
   `::part()::backdrop`); the submenu's outline rule; the §5.2 arrow rule
   (`clip-path: polygon(calc(0% - 1px) 100%, 100% calc(0% - 1px), 100% 100%)` on both
   arrow parts) and
   `:where(wa-popover)::part(popup) { --popup-border-width: 0px }` and the tooltip's
   `--wa-tooltip-border-width: 0px` (both with the `px` unit: a unitless `0` fails the
   test, §5.2); the
   `@supports not (selector(…::part(dialog)::before) and selector(…::part(dialog):before))`
   block (its condition names both spellings) — the last top-level block of
   the file, after the §5.2 tooltip and popover-arrow rules — giving the five Web Awesome
   layered surfaces `background-color: var(--glass-tint)` and `box-shadow:
   var(--glass-shadow), var(--glass-highlight)`, the dialog, menu, layered list
   box and popover body a `border` with `var(--glass-border)`, the tooltip
   `--wa-tooltip-background-color: var(--wa-color-text-normal)` and body
   `background-color: var(--wa-color-text-normal)`, and the popover arrow
   `background-color: var(--glass-tint)`; the direct list-box rule
   (`wa-select.glass-listbox-direct`) with `var(--glass-bg)`, the glass filter and
   `var(--glass-shadow), var(--glass-highlight)`; `--wa-color-overlay-modal:
   var(--glass-veil)` on `:root`; the `::part(dialog)::backdrop` rule; the §5.5 `wa-details`
   rule; the layer-anchor rule (`position: relative` and `isolation: isolate` on the
   popover body, `.glass-surface` and `.Notivue__notification`); every declaration of the
   §5.1 token rule with its value (`--wa-form-control-background-color:
   var(--glass-field-bg)`, `--wa-color-surface-border: var(--glass-border)`, `--row-bg:
   transparent`, `--row-hover-bg: var(--glass-item-hover)`, `--row-active-bg:
   var(--glass-item-highlight)`, `--glass-edge-gap: calc(var(--wa-border-width-s) + 1px)`); the §5.4 rules — inside
   `@media (hover: hover)` the hover rule excludes `[variant='danger']`, `[disabled]` and
   `:state(disabled)` on `wa-dropdown-item`, `[disabled]` and `:state(current)` on
   `wa-option`, and sets `var(--glass-item-highlight)`; the `:focus-visible` rule excludes
   danger and sets the same token; both arrow rules declaring their filter pair
   (`var(--glass-filter)` on the popover arrow, `var(--glass-tooltip-filter)` on the
   tooltip arrow); the tooltip rules of §5.2 — host `--wa-tooltip-background-color:
   var(--glass-tooltip-bg)`, body `background-color: transparent`, `position: relative`,
   `isolation: isolate`, and the body layer with `background-color:
   var(--glass-tooltip-bg)`, `z-index: -1` and the tooltip filter; the popover arrow's
   `background-color: var(--glass-bg)` and `border-color: var(--glass-border)`. The
   pseudo-element boxes themselves: each layer rule and each border-overlay rule (both
   halves of each split, the tooltip body layer, the `.glass-sticky::before` and
   `.glass-veil::before` rules) declares `content: ""`, `position: absolute`, `inset: 0`
   and `pointer-events: none` (without it a `z-index: 100` overlay swallows every click on
   the surface); the layer, overlay and tooltip-layer rules also declare `border-radius:
   inherit`; each layer rule declares its `backdrop-filter` and `-webkit-backdrop-filter`
   (test 3 checks their values, this checks their presence); `.glass-sticky` declares
   `isolation: isolate` and its `::before` `z-index: -1` and `var(--glass-sticky-bg)`;
   `.glass-veil::before` declares `z-index: -1`, `var(--glass-veil)` and `opacity:
   var(--glass-veil-opacity, 1)`. Also: the dark `--glass-shadow` equals the dark `--depth-3` of `depth.css` without
   its first (inset) layer, so the two stay in sync.
6. **Imports**: `glass.css` is imported right after `depth.css` in the three entry files.
7. **Toasts**: `App.vue` contains no `wa-invert` inside the `<Notivue` element; the
   `<Notivue` opening tag binds `styles` with `clipPath: 'none'` for `list`; and
   `toastTheme` sets `--nv-global-bg` to `transparent`.

### 10.2 Browser checks (worktree instance http://localhost:5174, before the user gate)

Scope probes to the visible elements (KeepAlive keeps other sessions' DOM). Read computed
styles with transitions disabled if the MCP tab is backgrounded (roadmap §6b.2).

- Light and dark: a dropdown menu, a select list, the settings popover (desktop and
  < 640px), the agent settings popover and its hover panel, the attachments popover, a
  dialog (project edit: fields at 70 %), the terminal combos dialog's key-capture field
  (70 %, like the Web Awesome fields, §6.7), the command palette (sticky header over scrolled
  rows, keyboard highlight), the search overlay, the session switcher, the three composer
  pickers and the directory picker (file tree rows transparent, tinted hover/focus), the
  file move dialog tree, the peer inbox rows, the settings Tips/Help rows, the selection
  comment panel, a tooltip (body and arrow both visible, no denser line at the joint), a
  popover arrow (visible, border matches the body, no denser line at the joint, the arrow
  still points at the anchor after `--popup-border-width: 0px`; no border line across the
  arrow's base — the body border is cut under it, §5.2 — in each placement: settings
  popover, agent settings popover, attachments popover, commit popover; also in the awesome
  theme), the
  usage graph tooltip, the project stats sparkline tooltip (project detail → Stats tab),
  each toast type reachable (tip toast from
  settings, a session toast), the mobile drawer veil (open/close fade; with the drawer
  closed, the `::before` computes `visibility: hidden`, so no blur layer stays composited
  over the page).
- Top layer (§5.1): a select list inside the project edit dialog and a dropdown submenu
  blur the page behind, not only their parent.
- Accent: cyan, orange, gray (gray: the tint is neutral, no pink/red cast).
- Theme: default, awesome and shoelace (each has its own palette, so its own tint).
- Reduced transparency: everything opaque, no blur.
  - Agent pre-check: headless Chrome launched from Bash with `--remote-debugging-port`,
    CDP `Emulation.setEmulatedMedia({features: [{name: 'prefers-reduced-transparency',
    value: 'reduce'}]})`, on 5174 (the worktree backend lets loopback requests in without
    a login); read the computed `backdrop-filter: none` on a surface layer, on
    `.glass-veil::before` and on the tooltip layer, and an opaque layer `background-color`
    (`--glass-tint`). (The Chrome extension tools used for the other checks have no media
    emulation.)
  - User check at the gate: Chrome DevTools → Rendering → "Emulate CSS media feature
    prefers-reduced-transparency: reduce".
- Nested modals: Shared links → edit a link (`ShareManagerDialog.vue:62` opens
  `ShareDialog` over itself; also `ShareTargetDialog.vue:82`, `ArtifactBookmarkDialog.vue:519`):
  two `::backdrop` veils stack over a blurred parent dialog — check that the double veil
  reads well and scrolls/animates smoothly. Deepest stack: Shared links → edit an
  **artifact** link → "Manage network access…" (`ShareDialog.vue:300`, `:35-38`) opens
  `ArtifactBookmarkDialog` inside `ShareDialog` (`:334`): three modals, three veils — check
  it the same way, on desktop and on mobile (< 640px, drawer open, then its command palette
  button → "Manage shared links" → the same chain: four veils with the drawer's under
  them).
- Share viewer: the worktree instance cannot serve it (shares are served only on the share
  host, `origin_gate.py`; the worktree's `shareBaseUrl` is the main instance's public host;
  the same limit as step 2, roadmap §6b.3). Checked the step-2 way instead: after
  `cd frontend && npm run build`, the built
  `src/twicc/static/share-session/share-session.css` contains the `glass.css` rules
  (`--glass-bg`, `wa-dialog)::part(dialog):before`, `wa-popover)::part(body):before`,
  `.glass-veil):before` — the check matches `::?before`: the build's minifier keeps the
  `:where()` wrappers but writes `::before`/`::after` as the legacy `:before`/`:after`,
  e.g. the current bundle's `share-header:before{`, while the `@supports` condition keeps
  `::before`), and
  `SharedSubagentView.vue` carries `glass-veil` on its drawer root. The visual check (popover
  menu, the select inside it — top layer —, a tooltip, the sub-agent drawer veil with the
  drawer panel and transcript unchanged) is recorded in the roadmap's "not verified in a
  browser" list for the user, on the main instance once the redesign is merged.
- Layers (§5.0): a long select list scrolled to its end (the layer stays); the opt-in wide
  list boxes (search overlay filters, `SearchOverlay.vue:845-852`; peer message review
  actions, `PeerMessageReviewDialog.vue:1942-1946`) show the glass across their whole
  width (their top edge may be covered by the scrollbar or a highlighted option scrolled
  to the top — accepted, §5.1); a long dropdown menu scrolled (the layer stays); the light top edge (light mode)
  on a dialog, a popover, a picker, a toast, a menu and a select list; the "Archive
  sessions older than…" submenu (`SessionsSidebarControls.vue:78-89`, in the sidebar
  options menu) reached by a diagonal mouse move from its parent item; a tooltip anchored
  inside a glass dialog (its hover bridge follows the pointer path; clicks elsewhere in
  the dialog are not swallowed while it is open); the process indicator tooltip inside the
  peer message review project select (`PeerMessageReviewDialog.vue:1623`), hovered, then
  the pointer moved to another option (the option takes the hover); every glass surface
  shows no unblurred ring along its border.
- Artifact shell: the consent dialog over an artifact, after `cd frontend && npm run build`
  (the shell bundle is served built even through 5174, `vite.config.js:85`, `:100`; its
  files are read on each request, so no restart is needed).
- Firefox (by the user, at the gate, in a real window — the agent's browser tools drive
  Chrome only and headless Firefox never blurs): a dropdown menu, a dialog, a tooltip and a
  toast show the tinted glass and the blur.
- The Firefox check is run twice: on the dev server (5174) and on the built bundle served
  by the worktree backend (http://localhost:3501 on the machine that runs the worktree),
  because only the build has the minified `::part(…):before` form (§11). The agent also
  opens the built bundle on 3501 in Chrome. Order: `cd frontend && npm run build`, then
  the agent restarts the worktree backend (worktree exception to the no-restart rule), with
  the safe sequence — a plain `restart back` has left a zombie `./run.py` holding the
  data-dir claim in worktrees before: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui
  && uv run ./devctl.py stop back`, wait until `pgrep -f
  "/home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/.venv/bin/python3 ./run.py"` finds
  nothing (kill any residue), `uv run ./devctl.py start back`, then poll
  `http://localhost:3501/api/projects/` until it answers
  — the backend's static server builds its file map once at startup (`asgi.py:2362-2363`,
  BlackNoise), while `index.html` is read fresh on each request (`views.py:4186-4196`) and
  the build replaces the hashed assets (`vite.config.js:62-64`, `emptyOutDir`), so without
  the restart the new page's CSS and JS answer 404.
- Safari: not checked — no Safari is available (the user works on Linux and Android). The
  §5.1 fallback covers it (§11).
- Glass border (§5.0 border overlay; submenu outline): unbroken along an edge scrollbar (a
  long menu, a scrolling dialog body, the settings detail panel), a highlighted full-bleed
  picker row, a hovered file tree row in the `@` file picker and the directory picker, the
  palette's sticky header, the media preview dialog's full-width content, and a
  keyboard-focused item of the "Archive sessions older than…" submenu (its focus ring may
  touch the outline, §5.0); the focused filter field of the command picker, the history
  picker and the `@` file picker, and the keyboard-focused "navigate up" / "hidden toggle"
  buttons of the directory picker, show their whole focus ring (§6.9; checked in the
  default theme and in the awesome theme, light mode, where the border is 2px); the
  keyboard-focused
  first item of a dropdown menu (at 16px its top ring row lies under the light edge, 2 of 3
  rows visible; at 14px it also overlaps half the border row — accepted, §5.0); the
  keyboard-focused or hovered logout button of the settings popover (the right border
  crosses it — accepted, §5.0).
- Toasts (Chrome): a toast blurs the page behind it (§7 step 1b, Notivue list clip
  removed).
- The layout overlay and the Files/Browser fullscreen previews: unchanged (no blur).

### 10.3 Checks with an uncertain outcome (decide at the gate)

- **Sticky header density**: the palette's sticky header layer blurs the rows under it and
  the dialog's own glass layer, then adds `--glass-sticky-bg`. It may read slightly denser
  than the rest of the panel. Acceptable; if it reads as a visible band, lower
  `--glass-sticky-bg`'s share.
- **Arrow joint at sub-pixel scale**: the §5.2 clip (base diagonal pushed 1px into the
  body) was checked in Chrome (DPR 1.1) and headless Firefox (DPR 1). Other device pixel
  ratios (the user's Android phone) may still show a faint seam; if one shows, record it for
  the global fine-tuning pass.
- **Entrance fades**: an ancestor with `opacity < 1` is a backdrop root, so while it fades
  in, the glass layer inside it may show without blur, then snap to the blur at the end.
  Cases: Web Awesome's dialog (`.dialog.show`, `show-dialog`, 200 ms, on the dialog
  itself, which is the layer's parent), dropdown menu (`#menu.show`, 50 ms) and submenu
  (`#submenu.show`, `submenu-show`, 50 ms, `chunk.OZ74GS2I.js`); Notivue's duplicate pulse
  (`.Notivue__duplicate`, opacity down to .8 for 0.3 s on the toast itself,
  `notifications.css`); Notivue's toast entrance (`notivue/dist/core/animations.css`,
  `.Notivue__enter`, 0.35 s opacity 0 → 1 on the toast's wrapper), exit
  (`.Notivue__leave`, starts at opacity .7, so the blur drops at once), clear-all
  (`.Notivue__clearAll`, 0.5 s fade); a scrolled dropdown menu opening or closing (during the
  50 ms `scale` animation `#menu` is the layer's containing block, so the layer follows
  the menu's scroll offset); Web Awesome's popover and tooltip
  (`animateWithClass(this.popup.popup, "show-with-scale")`, `chunk.QJBP6HBR.js:144`,
  `chunk.2XOQF53T.js:188`) and select list (`"show"`, `chunk.63CHHVSP.js:582`), about
  100 ms on show and hide. Acceptable for step 3 (entrances are step 5); record what is
  seen.
- **Ancestors with opacity/filter**: a glass panel inside an element with `opacity < 1`,
  `filter`, `mask`, `clip-path` or `mix-blend-mode` blurs only that element's content.
  Check the sparkline tooltip on the project stats (project detail → Stats tab). (The
  selection comment panel is
  teleported to `<body>` — `BrowserPane.vue:1197-1210`, `TerminalPanel.vue:1713`,
  `FilePane.vue:2068`, `FilePane.vue:2083`,
  `SessionItemsList.vue:2270` — so it has no such ancestor.)

## 11. Risks

| Risk | Mitigation |
|---|---|
| Text over busy content reads worse | 74 % tint + 1.125rem blur leaves a soft wash; reduced-transparency fallback; user gate on the large dialogs |
| GPU cost of large blurs (dialog veil over the whole viewport, big dialogs) | Veils stack: one per open modal, plus the mobile drawer veil when a dialog opens from the drawer (opening a dialog does not close the drawer: its checkbox changes only on a session change, `ProjectView.vue:1504-1514`, or an explicit toggle, `:1720-1724`). Usually one. Known two-modal cases: `ShareDialog` over `ShareManagerDialog`, `ShareTargetDialog` or `ArtifactBookmarkDialog`; the attachment preview (`MediaThumbnailGroup.vue:130`) over the peer message review (`PeerMessageReviewDialog.vue:1342`). Worst known case: four — on mobile, drawer open → its command palette button (`ProjectView.vue:2616`) → "Manage shared links" → edit an artifact link → "Manage network access…" (`ArtifactBookmarkDialog` inside `ShareDialog`, `ShareDialog.vue:334`) — gate checks in §10.2. No veil layer is composited while hidden (the mobile drawer veil is `visibility: hidden` when closed, §6.3; the other veils exist only while open). Blur radius small on the veil (0.375rem); no blur on panes |
| A future component re-adds an opaque background inside a glass surface | Visible at once (white patch); the rows/fields tokens are documented in `glass.css` |
| Firefox without `::part()::backdrop` support | Only the veil blur is lost; the veil color still applies through `--wa-color-overlay-modal` |
| A browser that does not render `::part(…)::before` would show the Web Awesome surfaces with no background at all (the surface clears its own) | The §5.1 `@supports not (selector(…::part(dialog)::before) and selector(…::part(dialog):before))` block gives those surfaces the opaque tint. Checked in Firefox (`/usr/bin/firefox`, headless screenshot, 2026-09-27): a `::part(x)::before` layer with `position: absolute; inset: 0; z-index: -1` renders behind the part's content. Headless Firefox applies no `backdrop-filter` at all (a plain `div` is not blurred either), so the blur itself is checked in a real Firefox window at the gate (§10.2). **Safari is not checked**: no Safari is available (the user works on Linux and Android), and no source found states whether current WebKit renders a pseudo-element after `::part()`. The fallback is the guarantee there: whichever spelling WebKit rejects, the condition turns the fallback on, so the worst case is opaque tinted surfaces, never a surface without background. To be recorded as a known limitation in roadmap §6c (§12) |
| The production build writes `::part(x)::before` as `::part(x):before` (the minifier's legacy single colon), a form the dev server (Vite, unminified) never shows | Checked: Chrome accepts `x::part(p):before` (round-10 reviewer); Firefox too (headless screenshot, 2026-09-27: the single-colon layer renders, `CSS.supports('selector(x::part(p):before)')` is true). The minifier leaves the `@supports selector()` condition as written, so the fallback condition tests both spellings (§5.1). The gate's Firefox check runs on the built bundle as well (§10.2) |
| Toasts lose their inverted contrast | User decision (§1.1); the glass border, `--depth-3` and the type-colored icon keep them distinct |

## 12. Delivery

One commit on `enhanced-ui` after the user's browser review ("commit" only on the user's
word): `feat(ui): accent-tinted glass on floating overlays`. It also carries this spec and
its implementation plan, as step 2's feat commit carried its design and plan (`85ce211f`).
The roadmap (§1 status, a new §6c with what it does, lessons and unverified points — the
share viewer check of §10.2 and the unverified Safari support of `::part()::before` — the
opaque fallback being the guarantee, §11 — among them) is updated in a separate docs commit, like step 2.
