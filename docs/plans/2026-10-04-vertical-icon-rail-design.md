# Vertical icon rail — design

Date: 2026-10-04 — Status: draft, in independent review (round 28).

## Goal

Add one vertical bar of icon buttons (the **rail**) on the left edge of the project view. It groups the
buttons that are scattered across the sidebar header and footer today, and gives the app a place for
future quick actions.

The rail is one component, used by both sidebar modes (sessions and artifacts). Its buttons have
states: normal, active, hidden, disabled.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| Component | One `SidebarRail.vue`, rendered once in `ProjectView.vue`. "Rail" is the technical name; "icon bar" is the name shown to users (setting label, tips, help pages). |
| Visual style | A floating card, like the content panel (`.panel-card` tokens: veil, border, radius, shadow). Plain icon buttons. The active mode button has a tinted brand fill. |
| Mode buttons | Sessions and Artifacts are both always visible. The button of the current mode is active. |
| Home icon | A house (`house`), not an arrow. |
| Inbox badge | Always shown on the inbox button. The badge on the Settings trigger is removed. |
| Mobile | Same rail. No reduced version for now. |
| Closed sidebar | A local setting decides whether the rail stays visible. Default: visible. |
| Reopen button | When the rail is hidden and the sidebar is closed, a floating toggle returns (a native button replacing today's). It carries the inbox badge in that case only. |
| Contextual buttons | The item model supports buttons shown only when the sidebar is closed (or only when open). No concrete item ships now. |

## Current state (facts verified in the repo)

- The sidebar shell is inline in `frontend/src/views/ProjectView.vue`
  (`<aside class="sidebar">` inside a `wa-split-panel.project-view`, itself inside
  `.project-view-wrapper`, which only sets `height: 100dvh`).
- Sidebar state is a hidden checkbox `#sidebar-toggle-state` plus `body.sidebar-closed` and the
  `sidebarClosed` ref. The checkbox semantics invert at the 640px breakpoint: desktop checked = closed,
  mobile checked = open. `body.sidebar-closed` is written only by `updateSidebarClosedClass()`, so it
  goes stale across the breakpoint (the existing CSS comment near the mobile rules says so; the CSS keys
  on the checkbox). Persistence: `localStorage['twicc-sidebar-state']` (`{open, width}`).
- Three code paths change `checkbox.checked` without a `change` event: the mobile close on session change,
  the drag-collapse in `handleSplitReposition`, and the Vue re-patch of
  `:checked="initialSidebarChecked"` (that computed depends on `sessionId` on mobile). A fourth caller, `resetSidebarToDefault`, does not change the checkbox: it only calls `updateSidebarClosedClass(false)`.
- Closed sidebar (desktop): `grid-template-columns: 0 var(--divider-width) auto`. Today the footer toggle
  (`label.sidebar-toggle`, a descendant of `.sidebar` through the footer rows, always rendered) stays visible and floats over the
  content. On mobile it sticks out of the closed drawer through its own `translateX(var(--sidebar-width))`.
- `body.sidebar-closed` has exactly these consumers: the clearance rules in `App.vue` (defining
  `--sidebar-toggle-clearance-left-x/-x/-y`), `SessionLayout.vue` (9 rules), `CollapsedBar.vue`,
  `MessageInput.vue`, `TerminalExtraKeysBar.vue`. Besides the `>= 640px` clearance, three unconditional
  mobile rules also assume the toggle always overlaps the composer: the `.message-input-toolbar`
  left padding in `MessageInput.vue`, the mobile rule in `CollapsedBar.vue`, and the mobile
  `padding-left` in `TerminalExtraKeysBar.vue`.
- The mode is route-based: `isArtifactsMode` in `ProjectView.vue`. `toggleSidebarView` flips between the
  two modes using the location memory in `utils/sidebarViewMemory.js` (shared with
  `commands/staticCommands.js`). Any route that is not the artifacts one counts as sessions mode.
  `components/sidebar/SidebarViewSwitch.vue` (imported only by `ProjectView.vue`) is the visual switch.
- The full-text search button is in `components/session/SessionsSidebarControls.vue` (hidden in artifacts
  mode). It emits `openAdvancedSearch`; `ProjectView` listens and dispatches `twicc:open-search`.
- Footer buttons: `components/app/CommandPaletteButton.vue` (no other consumer),
  `components/peer/PeerInboxButton.vue` (also used by `HomeView`), `components/app/SettingsPopover.vue`
  (also used by `HomeView`).
- `SettingsPopover` is a multi-root fragment: the trigger, its tooltip, a `wa-popover` (`for="settings-trigger"`,
  placement hardcoded `top`) and several dialogs (`LayoutManagerDialog`, `ShareManagerDialog`, …). It has
  one prop, `triggerAppearance`. `wa-popover` toggles itself on a click of the `for` element; its visible popup is a `wa-popup`
  with `popover="manual"` (browser top layer), inside a transparent fixed `dialog` part. The trigger id `#settings-trigger` is clicked
  programmatically by the `ui.settings` command (`commands/staticCommands.js`) and by
  `TelemetryNoticeDialog.vue`, and is referenced by tests.
- `.panel-card` sets `--wa-color-surface-default: transparent` for its content (only `wa-switch`,
  `wa-slider`, `wa-radio`, `wa-color-picker` and `wa-button` are reset), so a popover or dialog rendered
  inside a card would inherit a transparent surface.
- On desktop the sidebar open/close does not animate today (`--transition-duration` is declared on
  `.sidebar`, not on the grid that uses it). On mobile the drawer animates (`transform` transition, fading
  backdrop, reduced-motion fade-in); that stays unchanged. `.main-content` has `z-index: 1`; the divider part has `z-index: 2`.
- The Settings trigger always renders a text label and a count badge; container queries
  (`@container sidebar`) hide the label and show the badge when the sidebar footer is narrow.
- Local-only settings live in `SETTINGS_SCHEMA`, `SETTINGS_VALIDATORS`, a getter, a setter, and the
  persistence key list of `frontend/src/stores/settings.js` (examples: `reduceEffects`,
  `compactSessionList`).
- The sidebar drag, snap and persisted width logic (`wa-split-panel`, `primary="start"`) is independent
  of the container width, so adding the rail needs no change there. When the rail reappears as the
  sidebar opens (setting off), `wa-split-panel` emits one `wa-reposition`; it reaches `handleSplitReposition`'s
  normal branch and rewrites the same width, which is harmless.
- `styles/main-content-height.test.js` asserts the `.sidebar-toggle` and `.sidebar` mobile
  reduced-motion rules, and that `ProjectView.vue` has no `prefers-reduced-motion` (reduced motion goes
  through `:root.reduce-motion`).

## Design

### Component: `components/sidebar/SidebarRail.vue`

The root is a `<nav aria-label="Main navigation" class="sidebar-rail">`. It is the flex child of the
wrapper and the slot: it holds the width and the gap. Inside it, a `div.panel-card` holds
the buttons. The base slot styles (`display: flex`, `flex: none`, `position`, `container-type: size` with `container-name: rail`, `width`, both `z-index`
values, and the mobile opaque underlay, with their `(width < 640px)` media rule) live in `SidebarRail.vue`'s scoped style on that root. The collapse rules live in `ProjectView`'s CSS and target
`.sidebar-rail` and `.project-view-wrapper` (which holds `--rail-width`; the root of a child component
takes the parent's scoped styles), because they depend on the wrapper's `data-` attribute and the
checkbox. The card (`display: flex; flex-direction: column; gap: var(--rail-gap); padding: var(--rail-card-padding); flex: 1; margin-block: var(--panel-gap); margin-inline-start: var(--panel-gap)`, in the same scoped style) is a vertical flex column whose direct children are the top buttons, a flexible spacer
and the bottom buttons (no group wrappers). Each button's `AppTooltip` host is `position: absolute`: it takes no flex slot and no gap. It renders the items from `resolveRailItems`. It owns no navigation logic: `ProjectView` handles the events.

Props: `mode` (`'sessions' | 'artifacts'`), `sidebarOpen` (boolean, effective state, see Visibility),
`peerConfigured` (boolean), `inboxCount` (number), `settingsAnchor` (`type: Object`, `default: null`; an element, see *Settings popover and dialogs*). It reads `isMac` from the settings store itself.

Events: `home`, `select-mode` (payload: the mode), `search`, `palette`, `inbox`, `toggle-sidebar`.
Two loops, one per group (the `top` and `bottom` computeds), surround one `<div class="rail-spacer" aria-hidden="true">`. Each loop is a `<template v-for>` (each item renders several roots: button and tooltip, or the `SettingsPopover` fragment), so Vue requires the key on the `<template>` tag: `:key="item.id"`, `settings` included. Within it each `AppTooltip` comes after its button (`wa-tooltip` resolves `for` on connect and again on its first update; keeping each tooltip right after its button keeps that lookup trivially valid, also for the keyed inbox item and the Settings wrapper). The key matters: an unkeyed list would patch by index when the inbox item appears (the peer system becomes configured) and remount the `SettingsPopover`, closing an open popover. `toggle-sidebar` is handled by `handleRailToggle`, which calls the existing `toggleSidebar()` (it flips the checkbox and dispatches `change`; see *Floating reopen toggle*). `inbox` makes `ProjectView` dispatch `twicc:open-peer-inbox` (the logic of today's `PeerInboxButton.openInbox()`). `select-mode` calls the existing `toggleSidebarView()` (only two modes exist), and
does nothing if the requested mode is already active.

### Item model

| Field | Meaning |
|---|---|
| `id` | Stable id (test key). The DOM id, used as the `AppTooltip` anchor, is `sidebar-rail-<id>`, except `settings`, which keeps `#settings-trigger`. |
| `icon` | Web Awesome icon name. |
| `label` | Tooltip text (shortcut hint included) and `aria-label`. With a tooltip (non-touch), `wa-tooltip` sets `aria-labelledby`, which outranks `aria-label`; both carry the same text. The toggle's label states the action ("Close sidebar" / "Open sidebar"), so the rail has no `aria-expanded`. |
| `group` | `'top'` or `'bottom'`. |
| `visibleWhen` | `'always'` (default), `'open'` or `'closed'` (sidebar state). |
| `active` | Boolean for the two mode items, `undefined` for every other item. The template binds `:aria-pressed="item.active"`: Vue omits the attribute for `undefined`, and would render `false` as the string "false", which makes a button a toggle button for assistive technology. |
| `disabled` | Boolean, a definition field (default false), passed through. |
| `badge` | Optional count. |

`frontend/src/utils/sidebarRail.js` exports `RAIL_ITEM_DEFINITIONS` (the static items: `id`, `icon`, `group`, `visibleWhen`, `disabled`, `label` as a string or a function of the state, an optional `requires` predicate of the state, and an optional `badge` function of the state; `inbox` requires `peerConfigured`) and the pure function `resolveRailItems({ mode, sidebarOpen, peerConfigured, inboxCount, isMac }, definitions = RAIL_ITEM_DEFINITIONS)`, which returns the visible items with their labels, `active`, `disabled` and `badge`. The tests pass synthetic definitions to exercise `visibleWhen` and `disabled`, which no shipped item uses. `isMac` (from
`settingsStore.isMac`, as `CommandPaletteButton` does today) selects the palette hint (`⌘K` or `Ctrl+K`) and the search hint (`⌘⇧F` or `Ctrl+Shift+F`; `App.vue` accepts ctrl or meta).
No concrete `open` or `closed` item ships: only the mechanism and its tests. The toggle labels ("Close sidebar (Alt+Shift+B)", "Open sidebar (Alt+Shift+B)") are the exported constants `CLOSE_SIDEBAR_LABEL` and `OPEN_SIDEBAR_LABEL` of `utils/sidebarRail.js`; the floating toggle reuses the second. `resolveRailItems` returns the top group before the bottom group, with `toggle` last.

| Group | id | Icon | Label | Action |
|---|---|---|---|---|
| top | `home` | `house` | Back to projects list | `handleBackHome` |
| top | `sessions` | `comments` | Sessions | mode switch; active in sessions mode |
| top | `artifacts` | `ARTIFACT_ICON` (`shapes`) | Artifacts | mode switch; active in artifacts mode |
| top | `search` | `magnifying-glass` | Full-text search (⌘⇧F on macOS, Ctrl+Shift+F elsewhere) | `twicc:open-search`, both modes |
| bottom | `palette` | `bars-staggered` | Open command palette (⌘K on macOS, Ctrl+K elsewhere) | `openPalette()` (`ProjectView` adds it to its `useCommandRegistry()` destructure) |
| bottom | `inbox` | `envelope` | Peer inbox | `twicc:open-peer-inbox`; only if `peerConfigured`; badge |
| bottom | `settings` | `gear` | Settings | embedded `SettingsPopover` |
| bottom | `toggle` | `angles-left` / `angles-right` | Close sidebar (Alt+Shift+B) / Open sidebar (Alt+Shift+B) | `toggle-sidebar`; always last |

The rail's buttons are native `<button>` elements, not `wa-button`: `wa-button` does not forward host ARIA attributes to its inner button, and the rail needs `aria-label` and `aria-pressed` (mode buttons). Their style (`.rail-button`) is under *Visual style*.

The `settings` item is special-cased in the template: it renders `SettingsPopover` with `trigger-appearance="plain"`,
`trigger-icon-only`, `placement="right-end"`, `tooltip-placement="right"` and
`:trigger-label="item.label"` and `:position-anchor="settingsAnchor"` (the full prop list is under *Removals, cleanup and wiring*); `resolveRailItems` still returns it so its position and visibility
stay declarative.
The `search` item keeps the plain `magnifying-glass` icon (the sidebar filter field has no icon button
next to it any more); its tooltip names the function.

### Visual style

- The card is a `div.panel-card` (`styles/surfaces.css` bundles veil, border, radius, shadow in that class).
- Buttons: native (the Settings trigger is a `wa-button` styled to match), square, icon only, plain look (`wa-icon` inside), `AppTooltip` placed to the right.
- Active button: tinted brand fill (`--wa-color-brand-fill-quiet` range) and brand icon colour.
- Inbox badge: `PeerInboxBadge` at the button corner. The card must not clip it (no `overflow: hidden`
  while the rail is shown, except below the 22rem container threshold, see *Layout*), so the buttons are
  `position: relative`.
- The rail has no entrance or exit animation (the desktop sidebar has none either) and its native buttons
  have no hover transition. The Settings trigger loses the `wa-button` press scale and its button-level
  transitions (see *Settings trigger* below); only the gear rotation on its icon stays (pinned by `motion.test.js`,
  already under `:root.reduce-motion`). No new reduced-motion rule is needed.

**`.rail-button`** (the shared button class) is one global class in `styles/rail-button.css` (imported by `main.js`), used by
`SidebarRail.vue` and by the floating toggle. `native.css` styles every `button` (fill, hover and active
fills, `padding-inline`, `line-height`, border, `transition-property`); the class overrides it (unlayered,
so it wins over `@layer wa-native`):

| Rule | Value | Reason |
|---|---|---|
| `height`, `width` | `var(--rail-button-size)` | square button |
| `display` | `grid; place-items: center` | centre the icon |
| `padding`, `border`, `line-height` | `0`, `0`, `1` | undo native |
| `border-radius` | `var(--wa-form-control-border-radius)` | pin the native value |
| `margin` on `wa-icon` children | `0` | native.css adds `em` margins around an icon with a sibling, which would shift the inbox icon when its badge shows |
| `color` | `var(--rail-button-color)` | quiet icon |
| `transition` | `none` | no hover transition |
| `position` | `relative` | the pinned badge needs it (the floating toggle's own `position: absolute` wins by specificity) |
| `flex` | `none` | a button never squashes in the scrolling card |
| `font-size` | `var(--rail-icon-size)` (on the button itself, so the `wa-icon` inherits it) | icon size |
| `background-color` | `var(--rail-button-bg, transparent)` | base colour; the floating toggle sets an opaque one |
| hover | `background-image: linear-gradient(var(--rail-button-fill-hover), var(--rail-button-fill-hover))` under `@media (hover: hover)` and `:hover:not(:disabled)` | layered over the base colour; no sticky hover on touch |
| pressed (`:active:not(:disabled)`, no media query) | the hover layer | tap feedback on touch devices, where hover is off |
| active mode (`[aria-pressed="true"]`, also with `:hover:not(:disabled)` and `:active:not(:disabled)`, declared last) | same layering with `--rail-button-fill-active`, colour `--rail-button-color-active` | wins over hover and pressed |

The native `button:focus-visible` ring and the native `:disabled` look (half opacity, `not-allowed` cursor)
are kept. The six custom properties are declared once on `:root` in the same file:
`--rail-button-size: 2.25rem`, `--rail-icon-size: 1.125rem`, `--rail-button-color:
var(--wa-color-text-quiet)`, `--rail-button-fill-hover: var(--wa-color-neutral-fill-quiet)`,
`--rail-button-fill-active: var(--wa-color-brand-fill-quiet)`, `--rail-button-color-active:
var(--wa-color-brand-on-quiet)`.

**Settings trigger.** It stays a `wa-button` (`appearance="plain"`). A `::part(base)` rule in
`SettingsPopover.vue`'s own scoped style (keyed on `triggerIconOnly`, next to the existing
`#settings-trigger::part(label)` rule) sets exactly: `width` and `height` of `var(--rail-button-size)`, `padding: 0`, `border: 0`, `line-height: 1`, `font-size: var(--rail-icon-size)`, `color: var(--rail-button-color)`, `background-color` as for `.rail-button`, `background-image: none`, `scale: none` and `transition: none`. All these rules are keyed on the class the component adds when `triggerIconOnly` is set, so `HomeView`'s accent trigger is untouched: the base rule is `#settings-trigger.settings-trigger--icon-only::part(base)`, the hover layer is `#settings-trigger.settings-trigger--icon-only:hover::part(base)` inside `@media (hover: hover)`, and the pressed layer is `#settings-trigger.settings-trigger--icon-only:active::part(base)` (the two layers outrank the base rule by specificity, whatever the order). The wrapper that holds the component is `display: flex` and `flex: none`; the `wa-button` host is a flex item; the inline-flex `::part(base)` dominates its line box, so its height is `--rail-button-size`. The trigger has no sticky active state. The press feedback of `wa-button` is `:where()` rules (`motion.css:99-115` `scale` and transition,
`glow.css:130-132` press `background-color`), so the keyed base rule above resets `scale`, `transition` and the fills of every WA rule, and the hover and pressed layers repaint only `background-image`; `motion.test.js` accepts `scale: none`. The gear's hover rotation, pinned by `motion.test.js`, stays.

### Layout

`.project-view-wrapper` becomes `display: flex` (row). Children in order: the rail slot, the existing
`wa-split-panel` (`flex: 1; min-width: 0`). The hidden checkbox and the backdrop keep their
positions (absolute or fixed). An open `wa-dialog` is `display: block`, so the wrapper's dialogs are
zero-size flex items, which is harmless.

Dimensions (custom properties, declared on `:root` in `styles/rail-button.css`, `--rail-width` being overridden on the wrapper, next to the six
`--rail-*` button properties): `--rail-card-padding: 0.25rem`, `--rail-gap: 0.25rem` (between buttons),
`--rail-card-width: calc(var(--rail-button-size) + 2 * var(--rail-card-padding) + 2 * var(--divider-size))`
(it includes the card border) and `--rail-width: calc(var(--rail-card-width) + var(--panel-gap))` (a gap on
the left only: on the right, the sidebar content has its own inset, 0.75rem in the header and 4px for list
rows, and when the sidebar is closed the grid's divider column (desktop) or `.main-content`'s padding (mobile) already provides the gap). `--rail-width`
is overridden to `0px` by the collapse rule on the wrapper itself (`.project-view-wrapper[data-rail-when-closed="hidden"]:has(…)`; two rules per breakpoint, listed in *Visibility*), with the literal `0px` (no `var()`); the slot, the drawer and the backdrop all inherit it. It snaps (no transition), like the desktop sidebar: animating a flex
width would relayout the content area and make `wa-split-panel` emit `wa-reposition` on every frame.

The slot is `flex: none`, `display: flex` and `position: relative`; the card is `flex: 1` (its width is the slot's remaining
width), gets its height from `align-items: stretch` minus its `margin-block: var(--panel-gap)`, and has
`margin-inline-start: var(--panel-gap)`. `.project-view-wrapper` gets
`position: relative` (the floating toggle positions against it; the mobile drawer today resolves against
the initial containing block at `left: 0`, which is the same box). Collapsed state: the slot gets `overflow: hidden` and `visibility: hidden` (tab stops and focus order are right).

On desktop the slot has `z-index: 3` (above the divider at 2 and `.main-content` at
1, below `.main-content--preview-expanded` at 1000, which covers it by design).

Short viewports (for example a phone in landscape):

- **Owner:** the card, the spacer and the `@container rail` rule below live in
  `SidebarRail.vue`'s scoped style. The slot is a size container (`container-type: size`, named `rail`).
- **Rule:** below `@container rail (height < 22rem)` the card scrolls vertically (`overflow-x: hidden;
  overflow-y: auto; scrollbar-width: none` plus `::-webkit-scrollbar { display: none }`, because a visible
  scrollbar from `styles/scrollbars.css` would eat the card's exact inner width; the card still scrolls by
  wheel or touch). Only the spacer flexes. The inbox badge is clipped there; tooltips are top-layer popups
  and are not.
- **Why a container query:** a `@media` query resolves `rem` against the browser's 16px, not against the
  user's root font-size setting (12-32px), which scales the rail. `html.compact-height`
  (`utils/compactHeight.js`) is not reused: its threshold is page-wide (56rem) and the rail needs a local one.
- **Threshold:** the card is a flex column with `gap: var(--rail-gap)` between its children (4 top buttons,
  the spacer, 4 bottom buttons: 8 gaps; the absolutely positioned tooltip hosts are not counted). The
  threshold must stay above 8 × `--rail-button-size` + 8 × `--rail-gap` + 2 × `--rail-card-padding` + 2 ×
  `--divider-size` (card border) + 2 × `--panel-gap`, about 21.5rem plus 2px on desktop (`--panel-gap`
  0.5rem) and 21.0rem plus 2px below 640px. The test of *Testing* checks it.
- **Constraint:** `--rail-card-padding` must stay at or above the focus ring (`styles/glow.css` sets `--wa-focus-ring-width: 0.25rem` and `--wa-focus-ring-offset: 0rem`; the threshold test asserts the padding is at least their sum),
  or `overflow-x: hidden` would clip the ring.

### Visibility (CSS-driven, no stale state)

Visibility of the rail and of the floating toggle is decided in CSS, from the checkbox, in two media
blocks:

- `>= 640px` (desktop): sidebar closed = checkbox checked.
- `< 640px` (mobile): sidebar closed = checkbox unchecked.

The wrapper carries `data-rail-when-closed="visible" | "hidden"` (from the setting). Rail: collapsed when
the sidebar is closed and the attribute is `hidden`. Floating toggle: shown in the same condition, hidden otherwise, so the two are never both shown or both absent, whatever the breakpoint state.

The collapse rules, per breakpoint (`:checked` in the `>= 640px` block, `:not(:checked)` in the `< 640px` block), with `S` = `.project-view-wrapper[data-rail-when-closed="hidden"]:has(.sidebar-toggle-checkbox:checked)` (or its `:not(:checked)` form):

- `S` sets `--rail-width: 0px` (a literal, no `var()`);
- `S .sidebar-rail` sets `overflow: hidden` and `visibility: hidden`;
- `S .sidebar-toggle` sets `visibility: visible` (see *Floating reopen toggle*).

For JS needs (the toggle icon and label, the `sidebarOpen` prop, the body class below), `ProjectView`
derives one effective state:

- `sidebarOpen = isNarrowViewport ? checked : !checked`. `checked` is a ref initialised from
  `initialSidebarChecked.value`, then re-read from the real checkbox by one funnel function.
- The existing `updateSidebarClosedClass()` becomes that funnel, renamed `syncSidebarState()`: it no longer
  takes a `closed` argument and re-reads the checkbox instead (if the element is missing, it keeps the
  current `checked`). All its callers are updated: `ProjectView.vue` lines ~1507 (mobile close on session
  change), ~1527 (`resetSidebarToDefault`), ~1564 and ~1566 (`onMounted`, where the `if/else` becomes one `syncSidebarState()` call), ~1690 and ~1704 (both branches of
  `handleSplitReposition`), ~1730 and ~1736 (`handleSidebarToggle`).
- It is also called from a `watch` on `initialSidebarChecked` with `flush: 'post'` (the Vue re-patch must
  have reached the DOM before the re-read).
- The body class `sidebar-toggle-floating` (see *Clearance variables*) is set by a separate watcher, not by
  this function.
- The existing `(max-width: 639px)` queries (`isNarrowViewport`, `isMobile()`, built from
  `MOBILE_BREAKPOINT` with a `- 1`) differ from the CSS breakpoint between 639 and 640px; the rail makes
  this visible, so both become `(width < ${MOBILE_BREAKPOINT}px)` (the constant and its "must match CSS
  media query" comment stay).
- Crossing 640px keeps the checkbox state, so its meaning flips (desktop closed becomes mobile drawer open,
  and the reverse); `sidebarOpen`, the body class and the CSS follow.

### Setting

- Name: `sidebarRailVisibleWhenClosed`. Default `true`. Local-only.
- Added in `stores/settings.js`: `SETTINGS_SCHEMA`, `SETTINGS_VALIDATORS`, a getter, a setter, and the persistence list (the dictionary built by `collectAllSyncedSettings`, which feeds localStorage even for local-only settings).
- A `wa-switch` row in `SettingsPopover.vue`, next to "Compact session list", labelled "Keep the icon bar
  visible when the sidebar is closed". Store names: getter `isSidebarRailVisibleWhenClosed`, setter
  `setSidebarRailVisibleWhenClosed` (as `isReduceEffects` / `setReduceEffects`).
- It only affects the closed state. With the sidebar open, the rail is always visible.

### Floating reopen toggle

The footer `label.sidebar-toggle` is replaced by a native `<button class="sidebar-toggle rail-button">` that
is a direct child of `.project-view-wrapper`, so it no longer depends on the drawer transform. A `label`
would send a second, synthetic click to the hidden checkbox, outside the popover and the anchor, which
closes an open Settings popover; a button has no such click, is focusable, and the popover's Escape handler focuses its anchor (a visible control when the anchor was swapped before the popover opened; see the deferral rule).

- **Semantics:** `aria-label` and tooltip "Open sidebar (Alt+Shift+B)" (its `AppTooltip` `for` is `sidebar-toggle-button`; it is `sidebar-toggle-label` today). It calls `toggleSidebar()`. DOM id `sidebar-toggle-button` (kept). Its `<AppTooltip for="sidebar-toggle-button" placement="top">` is the next sibling of the button (today's default placement). Its DOM position is right after the rail slot and before the `wa-split-panel`, so Tab reaches it before the sidebar and the content (it is `visibility: hidden` unless it is the only reopen control).
- **Look:** `.rail-button` with `--rail-button-bg` set to an opaque `--wa-color-surface-default` (the hover
  and active layers paint over it), plus `--panel-border`, `--panel-radius` and `--panel-shadow`, at both
  breakpoints, since it floats over content. `z-index: 5` (above `.main-content` at 1 and the divider at 2,
  below the drawer at 100). It appears and disappears at once.
- **Position:** `position: absolute` in the wrapper, with the same offsets from the wrapper edges as
  today: `left` and `bottom` are `var(--sidebar-toggle-offset)`, and `calc(var(--sidebar-toggle-offset) + var(--panel-gap))` in the `>= 640px` block.
- **Offset variable:** `--sidebar-toggle-offset` is consumed only by the toggle, so it is declared in
  `ProjectView`'s scoped CSS on `.sidebar-toggle`: `var(--wa-space-s)`, and `var(--wa-space-xs)` under
  `.project-view-wrapper--peer .sidebar-toggle` (the wrapper gets the class `project-view-wrapper--peer`, a new binding on the existing `peerSystemConfigured` constant). This keeps today's rule (the old footer inset
  was `xs` when the inbox button was shown in a narrow footer, which is the case in this state at the default root font size; at a smaller root size on a wide mobile viewport the old offset was `s`, a 0.25rem difference that is accepted) and
  replaces the `.sidebar-footer-buttons--with-inbox` and container-query pair; `--sidebar-toggle-shift` is
  removed. The clearance consumers (`App.vue`, `SessionLayout`, ...) hardcode their own values and do not
  read it.
- **Focus:** `ProjectView` has two handlers, `handleRailToggle` (from the rail's `toggle-sidebar` event) and
  `handleFloatingToggle` (the floating button's click). Both call `toggleSidebar()`, then on `nextTick`:
  `handleFloatingToggle` focuses `#sidebar-rail-toggle`; `handleRailToggle` focuses the floating toggle
  only if `railCollapsed` (see *Clearance variables*) is then true.
- **Tooltips:** a hover-opened tooltip would stay open on a rail button that becomes `visibility: hidden`
  (`wa-tooltip` hides only on `mouseout`, blur and Escape), whichever rail button the pointer rests on (the
  Settings tooltip included). So `ProjectView` watches `[sidebarOpen, railCollapsed]` (default flush, not `immediate`) and, on every change (toggle click, Alt+Shift+B, palette, backdrop, session change, resize across 640px),
  calls `hideAllTooltips()`. `AppTooltip.vue` gets a plain `<script>` block next to its `<script setup>`
  (the setup block runs per instance) holding a module-level `Set`, `mountedTooltips`, of the mounted `wa-tooltip` elements
  (added where the element is bound, removed in `stopListening`), `clearPendingTimer` (moved here from the
  setup block) and the exported `hideAllTooltips()`: for each element, `clearPendingTimer(el); el.hide()`. `openInteractiveTooltips` (per instance today, unchanged), `TOOLTIP_SHOW_DELAY_MS` and `TOOLTIP_DISTANCE` stay in `<script setup>`, where `overlay-motion.test.js` reads them (its `scriptOf` sees only the setup block).
  Cancelling the pending show timer matters: `wa-tooltip.hide()` returns at once when closed, so a show
  timer still pending (250 ms delay) would open the tooltip later on the hidden anchor. With the setting
  on, clicking the rail toggle also closes its own tooltip, which stays closed until the next hover
  although its label changed (accepted). Focusing the other toggle shows that toggle's tooltip
  (`wa-tooltip` opens on focus); accepted, it names the control that now has focus. The floating toggle
  element is held in a shallow ref, `floatingToggleEl`.
- **Show/hide:** always rendered. A scoped `ProjectView` rule sets `visibility: hidden` (not a tab stop; it keeps the element's rect, so a fading tooltip does not jump to a 0x0 anchor, and it matches how the rail collapses; the button is `position: absolute`, so it takes no layout). The show rule sets `visibility: visible` and uses the same selectors as the rail collapse rule: `.project-view-wrapper[data-rail-when-closed="hidden"]:has(.sidebar-toggle-checkbox:checked)`
  in the `>= 640px` block and `…:has(.sidebar-toggle-checkbox:not(:checked))` in the `< 640px` block.
- **Badge:** `PeerInboxBadge` is pinned to the toggle's top-inline-end corner by its own CSS (the toggle is `position: absolute`, so it is the containing block); it renders only when `inboxCount > 0`, with no `peerConfigured` condition (the old code had none).

Removed with it: the footer row `.sidebar-footer-buttons` and its helpers (`--with-inbox`, the
placeholder button, the footer-inset container rule), the orphan `wa-divider` before the footer row, the
icon swap (the toggle markup keeps one static `angles-right` icon; the four rule sets that swap icons are
removed: the default `.icon-collapse`/`.icon-expand` rules, the `@container sidebar (width <= 50px)` swap, and
the mobile closed and open swaps), and the old toggle's CSS: the mobile `translateX` rules (closed and open), its
`transition: transform`, the `:root.reduce-motion .sidebar-toggle` rule, the `--sidebar-toggle-shift` machinery and
the old `--sidebar-toggle-offset` definitions (replaced by the definition above), the two opaque-background
media rules (merged into the one unconditional rule of the new button), and the `wa-button` inside the
label. Also removed: the `@container sidebar (width <= 17rem /
13rem)` blocks that target `#settings-trigger` and `.command-palette-button`. `ProjectView` stops
importing `SettingsPopover`, `CommandPaletteButton`, `PeerInboxButton` and `SidebarViewSwitch`.

### Clearance variables

`ProjectView` defines one computed, `railCollapsed = !sidebarOpen && !settingsStore.isSidebarRailVisibleWhenClosed`,
used by the body-class watcher, `settingsAnchor` and `handleRailToggle` (the CSS selectors in
*Visibility* express the same condition for styling). A new body class `sidebar-toggle-floating` is set
from a watcher on `railCollapsed` (`immediate: true`, removed in `onBeforeUnmount`).
**Every** consumer of `body.sidebar-closed` is re-keyed to it: `App.vue` (variable definitions),
`SessionLayout.vue` (9 rules), `CollapsedBar.vue`, `MessageInput.vue`, `TerminalExtraKeysBar.vue`. The
clearance values stay as they are: the new toggle sits at the same offsets and is `--rail-button-size`, no larger than the old small `wa-button` (about 2.375rem), so every existing clearance
(`--sidebar-toggle-clearance-*`, the per-dock values in `SessionLayout.vue`, the mobile paddings) still
clears it; they are re-keyed only. The three unconditional mobile rules (`MessageInput.vue` toolbar left padding, the `CollapsedBar.vue` mobile
rule, the `TerminalExtraKeysBar.vue` mobile `padding-left`) are re-keyed too: the toggle overlaps the
composer on mobile only while it floats. The `body.sidebar-closed` class and the `classList.toggle('sidebar-closed')` call in the funnel are removed (no consumer is left). The
`>= 640px` limits in `MessageInput` and `CollapsedBar` stay.

### Mobile (< 640px)

- The drawer is `position: absolute; left: 0` today. It becomes `left: var(--rail-width)`, and its width `--sidebar-width` becomes `min(300px, calc(80vw - var(--rail-width)))` (declared in the mobile block, used by the drawer width only once the old toggle's `translateX` is removed), so the drawer never ends beyond the viewport and the backdrop strip to tap keeps its 20vw. Its closed
  transform becomes `translateX(calc(-100% - var(--rail-width)))`, so the closed drawer is fully
  off-screen and never overlaps the rail. The rail slot has `z-index: 101` under `(width < 640px)` (declared next to the desktop `z-index: 3`, in `SidebarRail.vue`) (above the drawer at 100 and the
  backdrop at 99). The card is translucent (`--panel-veil`), so under `(width < 640px)` the slot also paints
  an opaque underlay, as `.main-content` does (`background: var(--canvas-background); background-attachment:
  fixed`): the sliding drawer and its border then never show through the rail (`--wa-shadow-xl`, which the drawer uses today, is not defined in the theme, so the drawer has no shadow).
- The backdrop (`position: fixed; inset: 0; z-index: 99`) becomes `left: var(--rail-width)` (declared after `inset: 0`; physical, like the drawer),
  so it covers the content, not the rail.
- The `ConnectionIndicator` dot (`position: fixed`, 4px from the top-left, 10px wide, `z-index: 1000`, with its own tooltip and no `pointer-events: none`) overlaps the rail at both breakpoints (numbers at the default 16px root; the dot is 10px while the offsets are in rem, so at a 12px root the desktop overlap is about 3px and at 32px it vanishes): on desktop it covers the card's top-left corner and about 1px of the first button (the card starts 8px from the edge); on mobile about 5px of the first button's corner, including that much of its click area (the card starts 4px from the edge). Accepted, listed in the manual check.
- The rail stays while the drawer is open (always visible when the sidebar is open). The drawer closes on
  session change as today; the toggle then shows `angles-right`.
- With the setting off and the drawer closed, the rail collapses and the floating toggle shows.
- Accepted trade-offs: the rail takes about 3rem of the width of a narrow viewport (the reduced mobile
  rail is future work); with the setting off, `--rail-width` flips with the drawer, so the drawer's offset and width snap while its transform slides (`--sidebar-width` and the `translateX` base depend on `--rail-width`), and the content reflows behind the veil. Both are small and covered by the manual check. The drawer is also narrower than before by the rail width, so the `@container sidebar (width <= 13rem)` rules (project-selector trigger padding, `ProjectView.vue` ~2835; new-session button label hidden, ~3164) now apply on viewports of about 328px or less, and earlier at larger root font sizes.

### Settings popover and dialogs

The rail's collapse uses `visibility: hidden`, and the `.panel-card` surface is transparent. Neither suits
a popover or dialogs rendered inside the card. So `SettingsPopover` renders its `wa-popover` and its four dialogs inside one `<Teleport to="body">`; only the trigger and its tooltip stay in place. `HomeView` is unaffected (the `for` lookup resolves from the document, where the trigger lives).

Anchoring when the rail is collapsed: the `for` binding stays on `#settings-trigger`, so a programmatic
`#settings-trigger.click()` (`ui.settings`, `TelemetryNoticeDialog`) still toggles the popover (`click()`
works on a `visibility: hidden` element). The popup was already in the browser top layer, so no z-index is involved. The Teleport is needed for `visibility` inheritance, for the surface, and for the popover's `dialog` part, which is `position: fixed` and not top-layer: inside the slot, `container-type: size` makes the slot its containing block, and the collapsed slot's `overflow: hidden` would clip it. The popover body is `min(90vw, 700px)` wide; `shift` only acts on the cross axis and `flip` (with `bestFit`) tries `right-start`, `left-end` and `left-start` after `right-end`, and never `top` `SettingsPopover` therefore decides the final
placement itself, on `wa-show`, through a pure function in a new module,
`utils/settingsPopoverPlacement.js`, which also exports the three constants
(`SETTINGS_POPOVER_WIDTH_RATIO` 0.9, `SETTINGS_POPOVER_MAX_WIDTH` 700, `SETTINGS_POPOVER_MARGIN` 16) and has a
unit test: `resolveSettingsPlacement({ preferred, anchorRight, innerWidth })` returns `preferred` unchanged unless it starts with `right`, and otherwise `'top'` when `innerWidth < anchorRight + min(RATIO * innerWidth, MAX_WIDTH) + MARGIN` (below 640px
`.settings-layout` is `width: auto`, so the ratio term is a safe upper bound there). The `onPopoverShow`
handler stays a synchronous top-level `function onPopoverShow(` (not `async`; the placement step is a nested `if` or a helper call, with no early `return` that would skip the existing statements: `resetTransientControls`, `mobileShowContent = false`, `afterSwap = null`, ...) that sets
`popoverRef.value.placement` imperatively: `wa-show` fires synchronously before the popup is positioned,
so a Vue-bound prop would arrive too late; it keeps the `    afterSwap = null` line at 4-space indent that
`settings-motion.test.js` pins. It skips the placement step when the preferred placement is already `top` (`HomeView`) or `popover.anchor` is null. The template binds `:placement` to the `placement` prop. The placement is computed once per opening: it is not recomputed on a window resize or rotation while the popover is open (accepted). The component's CSS (`--max-width`, today
`90vw`, and the `.settings-layout` base rule `width: min(90vw, 700px)`, which applies at 640px and above)
reads the ratio and the max width through computed strings with `v-bind()` (a first use of `v-bind()` in
the repo, accepted; it works under Teleport). `wa-popover` re-positions on a `placement` change.

`SettingsPopover` gets a prop `positionAnchor` (`type: Object`, `default: null`; an element), with this contract (in the Web Awesome source, `anchor` is internal state that overrides the
`for` element, only `handleForChange` restores it, `handleDocumentClick` exempts clicks inside the anchor,
and `connectedCallback` resets it):

- Every assignment to `popover.anchor` happens after `await popover.updateComplete`, in `onMounted` and in
  the `positionAnchor` watcher. Reason: the `for` click listener is attached by `handleForChange`, which
  runs in the popover's first Lit update. Assigning the trigger earlier makes it return early (the
  listener is never attached); assigning another element earlier gets the override replaced by the
  trigger. The listener stays on `#settings-trigger` and is never removed. The watcher
  and `onMounted` are the only writers, and both read the latest `props.positionAnchor` after the
  `await` (props are captured at first render, when the parent's template ref is still `null`, so the prop goes from
  `null` to the element after mount). No re-connect of the popover happens in this app (`ProjectView` is not
  in a `KeepAlive`, and a Teleport mounts once).
- First resolution: the Teleport connects the popover to `body` during the parent's patch, before the rail
  tree is attached; the `for` lookup works because Lit's first update runs in a later microtask. The
  design relies on this and adds no recovery code.
- Non-null: assign it to `popover.anchor`.
- Null: re-resolve `document.getElementById('settings-trigger')` and assign it only if the result is
  non-null and differs from the current `popover.anchor`. This only restores the anchor after a floating
  toggle override; it does not recover a failed first resolution (see above).
- A change of `positionAnchor` is applied at once while the popover is closed, and deferred while it is
  open. Re-anchoring an open `wa-popup` stops and restarts it (`hidePopover()`, one frame,
  `showPopover()`), which drops the focus and the scroll position of the control the user is working
  with, typically the icon-bar `wa-switch` whose flip causes the change. "Open" is a flag `popoverOpen`,
  set in `onPopoverShow` and cleared in the after-hide handler (`popover.open` turns false when the hide
  starts, about 100 ms before the popup stops, so it is not used); it is checked after the `await`. The
  existing `@wa-after-hide.self` handler (today `benchmarkTaskStore.resetTransientControls()`) becomes a
  function that keeps that call, clears the flag and applies the latest `props.positionAnchor` through one
  idempotent function, `applyAnchor()`, also used by the watcher; the `.self` modifier matters because
  `wa-after-hide` bubbles from the `wa-select` and `wa-dropdown` inside the popover. While a swap is
  pending, the open popover stays on its old anchor (possibly a now hidden trigger): Escape then returns
  no focus to a visible control, and a click on the floating toggle is an outside click that closes the
  popover while it toggles the sidebar (accepted).
- Both writers guard against an unmounted component: after the `await`, they return if the popover ref
  is `null`.

`ProjectView` passes `floatingToggleEl` (the ref above) to `SidebarRail`'s `settingsAnchor` prop, and from there to `SettingsPopover`'s `positionAnchor`, while `railCollapsed` is true, otherwise `null`. A real click on the floating toggle only toggles the sidebar; if the popover is open, the click closes it unless the floating toggle is already its anchor (then `handleDocumentClick` ignores it).

### Removals, cleanup and wiring

- Delete `components/sidebar/SidebarViewSwitch.vue`; keep the location memory where it is.
- Remove from the sidebar header: the back button and the view switch. Remove from
  `SessionsSidebarControls.vue`: the full-text search button with its `AppTooltip`, the
  `openAdvancedSearch` emit and the `.search-advanced-button` rules (including the "+" pseudo-element);
  in `ProjectView.vue`: the `#back-button` markup and its `AppTooltip`, and the `@open-advanced-search`
  binding. The `ProjectView` function
  `openAdvancedSearch()` (it dispatches `twicc:open-search`) stays and handles the rail's `search` event.
- Delete `CommandPaletteButton.vue` (no consumer left).
- `AppTooltip.vue`: add the plain `<script>` block with `hideAllTooltips()` (see *Floating reopen toggle*).
- `PeerInboxButton.vue` stays for `HomeView`; remove its dead `@container sidebar` rules. The rail renders
  its own inbox button with `PeerInboxBadge`.
- `SettingsPopover.vue` props, in one list: the existing `triggerAppearance` (the rail passes `plain`; `HomeView` passes `accent`; the `outlined` default has no caller left, so its comment is updated),
  the new `triggerLabel` (String, default `null`) and the new `triggerIconOnly` (Boolean; it adds a class `settings-trigger--icon-only` on the trigger, which the `::part(base)` rule targets; the text label stays in the DOM as a visually hidden span, so the trigger
  keeps its accessible name; when `triggerLabel` is set, the hidden label and the tooltip both use it; when unset, the label is
  "Settings" and the tooltip "Toggle settings", as on `HomeView` today; the hidden span, class `settings-trigger-label`, carries an explicit clip rule in the component's scope:
  `position: absolute; inline-size: 1px; block-size: 1px; overflow: hidden; clip-path: inset(50%);
  white-space: nowrap`), `placement` (popover;
  default `top`, the rail passes `right-end`), `tooltipPlacement` (trigger tooltip; `default: 'top'`, the `wa-tooltip` default, so `HomeView` keeps its placement and no `undefined` is bound; the rail passes `right`) and `positionAnchor` (see above). Also: Teleport of the popover and dialogs; remove the
  `PeerInboxBadge` element from the trigger (not just hide it), the base `.settings-trigger-badge { display: none }` rule, and the `@container sidebar` rules for the
  label collapse and the badge; keep the `#settings-trigger` id. `HomeView` usage is unchanged. The rail wraps the component in
  one `display: flex` element (no inline baseline gap under the `wa-button`); the tooltip is
  `position: absolute` and takes no flex slot.
- Remove the dead CSS tied to the removed footer row: the `+ wa-divider` selectors for the in-footer divider (`.sidebar-footer-usage + wa-divider`,
  `.sidebar-footer-provider-auth + wa-divider`; see the selector-list note below), and the
  `--sidebar-footer-inset` indirection (replace `var(--sidebar-footer-inset, X)` with `X` at `ProjectView.vue` ~3198 and ~3265). In the `@container sidebar (width <= 50px)` block, remove only the
  toggle icon swap; `.sidebar-header { visibility: hidden }` stays.
- Remove the `sidebarClosed` ref (its only use is the `PeerInboxBadge v-if` on the old toggle); the `checked`
  ref and the derived `sidebarOpen` replace it, and the floating-toggle badge uses `inboxCount > 0` only.
- Update user-facing tips (shipped content): `frontend/public/tips/full-text-search-mac.md` and `full-text-search-non-mac.md` line 11 ("the **search** button (a magnifying glass with a **+**) next to the sidebar session filter" becomes the Full-text search button of the icon bar); `frontend/public/tips/artifacts.md` lines 11-12 ("the icon at the top of the sidebar, to the right of the project selector" becomes the Artifacts button of the icon bar). Other tips that mention the sidebar stay true.
- Update user-facing help (live pages, not comments): `frontend/public/help/what-are-artifacts.md` lines
  ~36-37 ("open it from anywhere with the icon at the top of the sidebar, just to the right of the project
  selector" becomes the Artifacts button of the icon bar); re-read `frontend/public/help/peers.md` lines
  ~102-103 ("the inbox button next to **Settings**", still true in the rail).
- Update stale comments. Rule: grep every comment that names `sidebar-closed` (the string must disappear from comments too), the sidebar view switch, "sidebar
  toggle", the footer toggle, "sidebar inbox" the "+" search button, or the back and search buttons at the top of the sidebar. Known ones, by file:
  - `ProjectView.vue`: "Sidebar header view switch" (~985); the backdrop comment (mentions `.sidebar-toggle
    (transform)`); "footer keeps its own rules (the reopen toggle must stay visible)"; the mobile
    reduced-motion comment ("the toggle … lives inside it"); `updateSidebarClosedClass`/`sidebarClosed`
    (~1417-1420, ~1741-1747); the template comment on the toggle; ~1562 ("Set initial sidebar-closed class on body"); ~3685-3689, ~3746-3748 and ~3812 (comments inside the removed toggle blocks, which go with them; ~3746-3748 names `body.sidebar-closed`); ~756 ("switch matches the sidebar toggle"); ~969 ("sidebar toggle or command palette"); ~1708 ("identical to clicking the footer toggle
    button"); ~2804 on `.project-selector-trigger` ("stays opaque when it widens over the peer button":
    the neighbour is gone, so reword or delete) ~3719 ("when sidebar is collapsed (≤ 50px), show expand icon"; the block stays for `.sidebar-header`) and ~3036 on `.main-content` ("the button pushed out when
    the project selector widens", same family).
  - `styles/sidebar-rows.css` ~67-70 and `styles/sidebar-filter.test.js` ~16-17: the filter field "wears the brand look of its neighbours, the outlined brand buttons and the project selector"; only the options dropdown button remains beside it, so reword both.
  - `SettingsPopover.vue`: ~51 (`triggerAppearance`), ~678 ("whether the sidebar inbox button exists"),
    ~3060 (footer layout block).
  - `App.vue`: ~517 (Alt+Shift+B, "the keyboard equivalent of the sidebar footer" toggle) and the clearance
    block above its rules.
  - `SessionsSidebarControls.vue` header (search button, emit, `SidebarViewSwitch`);
    `ArtifactBookmarksSidebarControls.vue` header (line ~4 "No advanced (full-text) search button…",
    false once the rail shows search in artifacts mode; line ~13 "view switch is NOT here — see
    SidebarViewSwitch", stale once it is deleted).
  - `components/session/detail/SessionHeader.vue` ~1535-1536 ("like the back / selector / search buttons at the top of the sidebar").
  - `PeerInboxButton.vue` header, `appearance` comment and the trailing "SettingsPopover MIRRORS this threshold…" comment (it goes with the container rules); `PeerInboxBadge.vue` doc comment.
  - `useWebSocket.js` ~1388 ("persistent surface is the sidebar inbox badge");
    `usePeerSystemConfigured.js` ~8 ("sidebar inbox button").
  - `TelemetryNoticeDialog.vue` ~86 (it says `#settings-trigger` is mounted by HomeView and ProjectView;
    `SidebarRail` mounts it now).
  - `staticCommands.js` 616, 637 ("sidebar view switch") and ~1354 ("sidebar button, Settings → Peers actions"); `utils/sidebarViewMemory.js` 2;
    `components/app/SearchOverlay.vue` 7 ("'+' button in sidebar").
  - `CollapsedBar.vue` ~24, ~75 and ~83, `MessageInput.vue` ~2387, `SessionLayout.vue` ~56 and the block comment ~935-941, `TerminalExtraKeysBar.vue` ~354-357: the clearance and "sidebar-reopen toggle" comments.
- Update `styles/main-content-height.test.js`: the `.sidebar-toggle` mobile reduced-motion assertion, and
  its comment "The toggle lives inside the drawer". The test also asserts that the lazily matched mobile
  reduced-motion block contains no `visibility`, `translate` or `transform`, and that its `.sidebar` rule
  has no `opacity`: no rail rule goes in that block.
- The shortcuts help panel in `SettingsPopover.vue` and the skills/CLI docs are unaffected (no shortcut
  is added or changed).
- Two dividers are involved. The in-footer `wa-divider` before the buttons row is removed with the row.
  The sidebar-level `wa-divider` before the footer and the `.sidebar-footer` wrapper are unconditional
  today; they get a new `v-if` on one computed, `hasSidebarFooter` (`(quotaHasUsage && quotaComputed) || unauthenticatedProviders.length`), also used for the `sidebar--no-footer` class below,
  so no trailing divider remains when the footer is empty. The CSS rule that hides that divider next to
  the usage card shares a selector list with the in-footer `+ wa-divider` rule: split the list, keep the
  first selector, remove the second.
- The footer row was also the sidebar's bottom inset. When `hasSidebarFooter` is false, the `.sidebar` gets a class
  `sidebar--no-footer` with a bottom padding of `--panel-gap` (it has none today and is `box-sizing:
  border-box`), so the session list does not touch the viewport edge. With a footer, its own margins
  apply (no double spacing).
- `.project-selector { max-width: min(50rem, calc(100vw - 100px)) }` (`ProjectView.vue` ~2787): the `100px` reserved room for the back button and the view switch, which leave the row; it becomes `max-width: 50rem` (the selector still fills the row with `flex: 1`).
- Wiring: `main.js` imports `./styles/rail-button.css` between `tool-cards.css` and `scrollbars.css`, with a one-line comment like its neighbours (not between `surfaces.css`, `sidebar-rows.css` and `option-cards.css`, whose adjacency `sidebar-rows.test.js` and `question-options-motion.test.js` pin).
- Stays in the sidebar: project selector, filter controls, session list, usage card, provider-auth
  callouts, new-session button.

### Accepted behaviours

- `HomeView` has no rail (it keeps its floating inbox and Settings buttons). Going from home to a
  project shows the rail on mount.
- The "Keep the icon bar visible when the sidebar is closed" switch lives in `SettingsPopover`, which `HomeView` also renders.
  It has no effect there (Home has no rail).
- Any change of the open/closed state that is not a click on one of the two toggles (the Alt+Shift+B shortcut, the command palette, the mobile backdrop or session-change close, `ui.focus-project-selector` expanding the sidebar) drops the focus to the page when the focused button hides; the floating toggle is a native button, so Tab and Enter reach and operate it.
- The `ConnectionIndicator` dot overlaps the rail's top-left corner (see *Mobile*).
- With the card scrolling below the 22rem container threshold, no scroll affordance shows (the scrollbar is hidden to keep the card's inner width).
- Tab and Shift+Tab leaving the Settings popover land at the end of `body` (the popover is teleported), not back on the rail.
- Dropping `--sidebar-footer-inset` removes the `xs` side inset that the usage card and the provider-auth callouts got in a narrow sidebar when the peer system is set up (0.25rem more inset there).
- On touch devices `AppTooltip` is hidden, so rail icons are discoverable by their glyph and accessible
  name only.
- The rail's search icon is the same glyph as the sidebar filter field's start icon; the old search
  button's "+" decoration is dropped.
- Clicking a mode button on desktop with the sidebar closed (rail visible) only switches the mode: the main pane changes and the sidebar stays closed. On mobile, when the page loaded below 640px, the existing rule applies (`initialSidebarChecked` depends on `sessionId` and the re-patch follows it; after a resize from desktop that computed is not reactive and the drawer does not open): Artifacts (no session in the route) opens the drawer, and Sessions restoring a session location closes it.
- On a route that is neither sessions nor artifacts, the Sessions button shows as active, as the current
  switch does.

## Testing

- A source-text test for the setting, as `stores/settingsAgentShares.test.js` does (`settings.js` is not
  importable under `node --test`: extensionless imports; `SETTINGS_VALIDATORS` is not exported). It asserts
  the registration points: `sidebarRailVisibleWhenClosed: true,` in the schema, the validator, the getter,
  the setter and the key in the persistence list. `utils/sidebarRail.js` imports only with `.js` extensions, from modules that import nothing
  extensionless (`ARTIFACT_ICON` lives in `utils/artifactBookmark.js`, which has no imports), so its test
  imports it directly.
- New test files, and what each pins:
  - `utils/sidebarRail.test.js`: `resolveRailItems` with the shipped and with synthetic definitions
    (`visibleWhen`, `disabled`, `requires`, group order with `toggle` last, `active` only for the two
    mode items, labels per `isMac`, toggle label per `sidebarOpen`, badge).
  - `utils/settingsPopoverPlacement.test.js`: the placement function.
  - `stores/sidebarRailSetting.test.js`: the setting's registration points (source text).
  - `styles/rail-button.test.js`: `rail-button.css` (no `@layer`, the six `:root` properties, the rules of
    the table incl. hover inside `@media (hover: hover)`), the `main.js` import, and the threshold test below.
  - `styles/sidebar-clearance.test.js`: the re-keying guard below.
  - `components/sidebar/SidebarRail.test.js`: compiles the SFC (as `settings-motion.test.js` does) and pins
    the two `<template v-for :key="item.id">` loops around one spacer, each `AppTooltip` right after its
    button, `:aria-pressed="item.active"`, native `<button>` elements, no `glass-*` class, no
    `aria-expanded`.
  - `components/app/settingsPopoverRail.test.js`: the `SettingsPopover` source: one Teleport holding the
    popover and the four dialogs, the `positionAnchor` prop (`type: Object`, `default: null`),
    `tooltipPlacement` default `'top'`, the `.settings-trigger--icon-only` class and the
    `.settings-trigger-label` span, no `PeerInboxBadge` in the trigger, no `@container sidebar`, `v-bind(`
    in the style, `@wa-after-hide.self` calling the apply function, `await popover.updateComplete` before
    every anchor write, a synchronous `function onPopoverShow(`.
  - `styles/overlay-motion.test.js` (existing): a pin for `hideAllTooltips`, `mountedTooltips` and
    `clearPendingTimer` in the plain `<script>` block of `AppTooltip.vue` (its `scriptOf` matches only
    `<script setup>`, so the pin reads the plain block with its own regex), and for the
    `watch([sidebarOpen, railCollapsed]` that calls `hideAllTooltips()` in `ProjectView.vue`.
- A source-text test for the short-viewport threshold: it reads the `--rail-*` values from `styles/rail-button.css`, `--divider-size` from `App.vue` (`:root`, unscoped style), both `--panel-gap` declarations from `styles/surfaces.css` (desktop and below 640px) and the `22rem` threshold from the `@container rail` rule in `SidebarRail.vue`, resolves `--wa-space-xs` (0.5rem) and `--wa-space-2xs` (0.25rem) from a small map documented in the test (the worktree has no `node_modules` to read the WA theme from), and takes the item count, taken from `resolveRailItems` with every flag
  enabled (the maximum over both `sidebarOpen` values; gaps = items, since the spacer is one more
  child), and asserts it is below the container threshold. Adding a rail item then fails the test
  instead of silently breaking the threshold.
- A source-text test for the `SettingsPopover` contract: one `<Teleport to="body">` holding the popover and the four dialogs, and the `positionAnchor` prop (`type: Object`, `default: null`), in the style of `settings-motion.test.js`.
- A unit test for `utils/settingsPopoverPlacement.js` (preferred kept when it fits, `top` when it does
  not, `top` preferred unchanged, narrow and wide viewports).
- A source-text guard for the re-keying: no `.vue`, `.css` or `.js` file under `frontend/src` (tests excluded) contains `sidebar-closed`, `updateSidebarClosedClass`, `sidebarClosed`, `sidebar-toggle-label`, `search-advanced-button`, `SidebarViewSwitch` or `CommandPaletteButton` (the `visibleWhen` values are `'open'` / `'closed'` for that reason), and the five consumer files (`App.vue`, `SessionLayout.vue`,
  `CollapsedBar.vue`, `MessageInput.vue`, `TerminalExtraKeysBar.vue`) contain `sidebar-toggle-floating`
  (the existing `glass.test.js` and `reduce-effects.test.js` already walk the sources). The `main.js` import pin of `rail-button.css` lives in `styles/rail-button.test.js`, like the other sheets' pins (a missing import is invisible under Vite dev).
- Updated `styles/main-content-height.test.js`. The whole `npm test` suite runs. Tests that pin things this change touches (non-exhaustive: `settingsNavigation.test.js`, `publicOriginSettings.test.js`, `settingsAgentShares.test.js` and `colorSchemeTransition.test.js` also read `SettingsPopover` and stay green): `motion.test.js` (every rule whose selector contains `#settings-trigger` goes through `assertMotionInvariants`: `:hover` rules inside `@media (hover: hover)`, `scale: none` accepted; plus the gear rules), `settings-motion.test.js` (the `SettingsPopover` source: `onPopoverShow`, the template walks; `glide.test.js` and `glow.test.js` pin the settings-nav ink and hover rules, untouched) and
  `glass.test.js` (a whitelist of files that may use glass classes: `SidebarRail.vue` is not in it and
  must not use `glass-*` classes); those ids, rules and shapes must stay.
- Manual check in the worktree (desktop and mobile widths, light and dark): rail with the sidebar
  open; closed with the setting on; closed with the setting off (floating toggle, badge, content
  clearance in the composer, terminal keys bar and layout); resizing across 640px with the sidebar
  closed; mode switch; search in both modes; Settings popover placement on desktop and on mobile (it must fit the viewport), also opened from the command palette while the rail is hidden; tab
  order; reduced motion; tooltip and focus after a click that hides the clicked button (the rail toggle with the setting off, the floating toggle), including a click within 250 ms of entering it, and Alt+Shift+B while the pointer rests on any rail button: the tooltip closes and never reopens, and the focus lands on the other toggle; a mode switch from a session route on mobile; the `ConnectionIndicator` dot over the rail's top-left corner on both breakpoints; the drawer and backdrop at a large root font size on a 360px viewport, and the `ConnectionIndicator` overlap at 12px and 32px roots and the narrow-sidebar container rules (`@container sidebar (width <= 13rem)`) on a 320px viewport; no tooltip jump during the hide animation after clicking the floating toggle; the Settings host is exactly the button height.

## Out of scope

- A reduced rail on mobile.
- Concrete `visibleWhen` items.
- Rail on `HomeView`.
- The hidden checkbox `#sidebar-toggle-state` being a tab stop today (pre-existing).
- CHANGELOG entry (only on the user's request).
