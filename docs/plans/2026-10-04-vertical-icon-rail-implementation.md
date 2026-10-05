# Vertical Icon Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one shared icon bar to the project view, with a local visibility setting and a floating reopen button.

**Architecture:** Keep navigation and sidebar state in `ProjectView.vue`. A pure item resolver supplies `SidebarRail.vue`. CSS controls visibility from the existing checkbox; Vue derives effective state for labels, focus, clearance, and Settings anchoring.

**Tech Stack:** Vue 3 Composition API, Pinia, VueUse, Web Awesome 3, CSS container queries, `node:test`, `@vue/compiler-sfc`.

**Spec:** [Vertical icon rail — design](2026-10-04-vertical-icon-rail-design.md). Read it with this plan. Treat its agreed decisions as the implementation contract. Its draft review status does not require another spec review round.

## Global Constraints

- One `SidebarRail.vue`, rendered once in `ProjectView.vue`. "Rail" is the technical name; "icon bar" is the user-facing name.
- Same rail on mobile. No rail on `HomeView`. No concrete contextual item ships.
- `sidebarRailVisibleWhenClosed`: default `true`, local-only. The open sidebar always has a visible rail.
- Desktop breakpoint: `width >= 640px`. Mobile breakpoint: `width < 640px`. Preserve the checkbox's inverted meanings.
- Preserve `twicc-sidebar-state` (`{open, width}`), width persistence, drag/snap behavior, route memory, and existing shortcuts.
- Native rail buttons, except the existing Settings `wa-button`. Keep `#settings-trigger` and its programmatic callers.
- Floating card uses `.panel-card`. No `glass-*` classes. No rail visibility animation or button hover transition.
- Keep mobile drawer/backdrop animations and existing reduced-motion behavior. Keep the Settings gear rotation.
- Inbox badge stays on the inbox button. Remove the Settings trigger badge. The floating reopen button carries the badge when visible.
- All code, tests, comments, UI copy, and documents use English. Add no dependencies or backend changes.
- Preserve user-owned changes and the design document. Do not create another branch or worktree.
- No CHANGELOG entry: the spec excludes it unless the user requests it.

## Review Focus

Each condition below has a test or manual check in its owning task.

1. Fractional viewport widths and crossing 640px: CSS and Vue agree, with exactly one available reopen control. Task 6.
2. Hidden Settings trigger and changing anchors during an open popover: commands still open Settings; edits retain focus and scroll. Task 3.
3. Pending tooltip timer when a control hides: the tooltip closes and does not reopen after 250 ms. Tasks 4 and 6.
4. Short viewports and 12–32px root fonts: all buttons remain reachable; drawer and backdrop fit the viewport. Tasks 5 and 6.
5. Peer configuration changes while Settings is open: keyed items preserve the Settings instance. Tasks 5 and 6.

## Working Context and Verification

Use the existing checkout: `/home/twidi/dev/twicc-poc/.worktrees/vertical-icons-bar` (branch `vertical-icons-bar`).
Prefix shell commands with `cd /home/twidi/dev/twicc-poc/.worktrees/vertical-icons-bar &&`.
Commands below use paths relative to that checkout. Frontend test commands then enter `frontend`.
The checkout currently has no `frontend/node_modules`. Do not install packages on your own initiative.
Run dependency-free helper tests with `node --test` first. Run SFC tests and the suite when dependencies are available.
If the user requests server startup, use `uv run ./devctl.py start`; devctl installs frontend dependencies itself.
Do not start or restart servers merely to execute this plan. Record any verification blocked by missing dependencies or servers.

Each task has one bounded test cycle. Fix failures caused by the change; do not restart spec review.
Commit only the task's files. Use a Conventional Commit subject, descriptive body, and the environment's exact Codex model trailer.
The commit subjects below are suggestions. Do not commit this plan or the user-owned spec without authorization.

## File Map

All paths below are repository-relative. Existing files retain their current responsibilities.

| Files | Responsibility |
|---|---|
| Create `frontend/src/utils/sidebarRail.js` and `.test.js` | Item definitions, resolution, labels, group order |
| Create `frontend/src/stores/sidebarRailSetting.test.js`; modify `frontend/src/stores/settings.js` | Local setting registration and persistence |
| Create `frontend/src/utils/settingsPopoverPlacement.js` and `.test.js` | Pure Settings placement decision and sizing constants |
| Modify `frontend/src/components/app/SettingsPopover.vue`; create sibling `settingsPopoverRail.test.js` | Icon-only trigger, local switch, Teleport, placement, anchor lifecycle |
| Modify `frontend/src/components/ui/AppTooltip.vue` and `frontend/src/styles/overlay-motion.test.js` | Shared mounted-tooltip registry and timer cancellation |
| Create `frontend/src/styles/rail-button.css` and `.test.js`; modify `frontend/src/main.js` | Shared button tokens and styles; sheet import |
| Create `frontend/src/components/sidebar/SidebarRail.vue` and `.test.js` | Presentation, groups, badge, scrolling, emitted actions |
| Modify `frontend/src/views/ProjectView.vue`; create sibling `ProjectViewRail.test.js` | Integration, effective state, visibility, floating toggle, responsive layout |
| Modify `frontend/src/App.vue`, `frontend/src/components/session/layout/SessionLayout.vue`, `frontend/src/components/message/CollapsedBar.vue`, `frontend/src/components/message/MessageInput.vue`, `frontend/src/components/terminal/TerminalExtraKeysBar.vue`; create `frontend/src/styles/sidebar-clearance.test.js` | Clearance only when the toggle floats |
| Modify `frontend/src/components/session/SessionsSidebarControls.vue`, `frontend/src/components/peer/PeerInboxButton.vue`; delete `frontend/src/components/sidebar/SidebarViewSwitch.vue`, `frontend/src/components/app/CommandPaletteButton.vue` | Remove replaced controls and dead styles |
| Modify `frontend/src/styles/main-content-height.test.js`, `frontend/src/styles/sidebar-rows.css`, `frontend/src/styles/sidebar-filter.test.js` | Preserve drawer motion checks; update stale descriptions |
| Modify tips/help and comments listed in Task 7 | User instructions and source descriptions match the rail |

## Task 1: Define the Rail Item Contract

**Files:** Create `frontend/src/utils/sidebarRail.js` and `frontend/src/utils/sidebarRail.test.js`.

**Interfaces:**
- Consumes: `ARTIFACT_ICON` from `./artifactBookmark.js`.
- Produces: `RAIL_ITEM_DEFINITIONS`, `OPEN_SIDEBAR_LABEL`, `CLOSE_SIDEBAR_LABEL`.
- Produces: `resolveRailItems({ mode, sidebarOpen, peerConfigured, inboxCount, isMac }, definitions = RAIL_ITEM_DEFINITIONS) -> RailItem[]`.
- `RailItem`: `{ id, icon, label, group, visibleWhen, active, disabled, badge? }`.
- Definitions accept string/function labels, optional `requires(state)` and `badge(state)`, and `visibleWhen` default `'always'`.

- [ ] **Step 1: Write resolver tests.** Include these assertions with `node:test` and `node:assert/strict`:

```js
const state = { mode: 'sessions', sidebarOpen: true, peerConfigured: true, inboxCount: 3, isMac: false }
const items = resolveRailItems(state)
assert.deepEqual(items.map(item => item.id), ['home', 'sessions', 'artifacts', 'search', 'palette', 'inbox', 'settings', 'toggle'])
assert.equal(items.find(item => item.id === 'sessions').active, true)
assert.equal(items.find(item => item.id === 'artifacts').active, false)
assert.ok(items.filter(item => !['sessions', 'artifacts'].includes(item.id)).every(item => item.active === undefined))
assert.equal(items.find(item => item.id === 'inbox').badge, 3)
assert.equal(items.at(-1).label, 'Close sidebar (Alt+Shift+B)')
assert.equal(resolveRailItems({ ...state, sidebarOpen: false }).at(-1).label, 'Open sidebar (Alt+Shift+B)')
assert.equal(resolveRailItems({ ...state, peerConfigured: false }).some(item => item.id === 'inbox'), false)
```

Also assert both mode active states in artifacts mode. Assert exact icons and labels from the spec's item table.
Assert search labels `Full-text search (⌘⇧F)` / `Full-text search (Ctrl+Shift+F)` and palette labels `Open command palette (⌘K)` / `Open command palette (Ctrl+K)`.
Use synthetic definitions to assert `'always'`, `'open'`, `'closed'`, `requires`, disabled pass-through, default false, and top-before-bottom ordering.
Place a synthetic bottom item after `toggle` in the definitions; assert `toggle` still resolves last.

- [ ] **Step 2: Run `node --test frontend/src/utils/sidebarRail.test.js`.** Expect failure because the module does not exist.
- [ ] **Step 3: Implement the exports.** Use `.js` import extensions. Set toggle icon to `angles-left` when open, `angles-right` when closed. Only mode items receive boolean `active`.
- [ ] **Step 4: Run the same command.** Expect all resolver tests to pass.
- [ ] **Step 5: Commit the two files.** Subject: `feat(sidebar): define icon rail items`.

## Task 2: Register the Local Visibility Setting

**Files:** Modify `frontend/src/stores/settings.js`; create `frontend/src/stores/sidebarRailSetting.test.js`.

**Interfaces:**
- Produces: state `sidebarRailVisibleWhenClosed: boolean`, default `true`.
- Produces: getter `isSidebarRailVisibleWhenClosed` and action `setSidebarRailVisibleWhenClosed(enabled: boolean) -> void`.
- Persists through `collectAllSyncedSettings` into localStorage. Does not join `SYNCED_SETTINGS_KEYS`.

- [ ] **Step 1: Write source-registration tests.** Follow `settingsAgentShares.test.js`; do not import `settings.js` under Node.

```js
assert.match(source, /sidebarRailVisibleWhenClosed: true,/)
assert.match(source, /sidebarRailVisibleWhenClosed: \(v\) => typeof v === 'boolean'/)
assert.match(source, /isSidebarRailVisibleWhenClosed: \(state\) => state\.sidebarRailVisibleWhenClosed/)
assert.match(source, /setSidebarRailVisibleWhenClosed\(enabled\)/)
assert.match(source, /SETTINGS_VALIDATORS\.sidebarRailVisibleWhenClosed\(enabled\)/)
assert.match(source, /sidebarRailVisibleWhenClosed: store\.sidebarRailVisibleWhenClosed,/)
assert.equal(SYNCED_SETTINGS_KEYS.has('sidebarRailVisibleWhenClosed'), false)
```

- [ ] **Step 2: Run `node --test frontend/src/stores/sidebarRailSetting.test.js`.** Expect registration assertions to fail.
- [ ] **Step 3: Add the schema entry, validator, getter, setter, and persistence entry.** Follow `compactSessionList`.
- [ ] **Step 4: Run the same command.** Expect all assertions to pass.
- [ ] **Step 5: Commit the store and test.** Subject: `feat(settings): persist icon bar visibility locally`.

## Task 3: Make Settings Work Inside and Outside the Rail

**Files:** Create `frontend/src/utils/settingsPopoverPlacement.js` and `.test.js`; modify `frontend/src/components/app/SettingsPopover.vue`; create `frontend/src/components/app/settingsPopoverRail.test.js`.

**Interfaces:**
- Consumes: Task 2 getter/setter and shared CSS variables introduced by Task 5.
- Produces: constants `SETTINGS_POPOVER_WIDTH_RATIO = 0.9`, `SETTINGS_POPOVER_MAX_WIDTH = 700`, `SETTINGS_POPOVER_MARGIN = 16`.
- Produces: `resolveSettingsPlacement({ preferred, anchorRight, innerWidth }) -> string`.
- Adds props: `triggerLabel: String = null`, `triggerIconOnly: Boolean = false`, `placement: String = 'top'`, `tooltipPlacement: String = 'top'`, `positionAnchor: Object = null`.
- Retains `triggerAppearance: String = 'outlined'`, `#settings-trigger`, existing show logic, and existing Home usage.
- Internal functions: synchronous `onPopoverShow()`, asynchronous `applyAnchor() -> Promise<void>`, `onPopoverAfterHide()`.

- [ ] **Step 1: Write placement tests and Settings SFC/source guards.** Use the existing SFC compiler pattern in `settings-motion.test.js`.

```js
assert.equal(resolveSettingsPlacement({ preferred: 'right-end', anchorRight: 50, innerWidth: 1200 }), 'right-end')
assert.equal(resolveSettingsPlacement({ preferred: 'right-end', anchorRight: 50, innerWidth: 360 }), 'top')
assert.equal(resolveSettingsPlacement({ preferred: 'top', anchorRight: 50, innerWidth: 360 }), 'top')
assert.equal(resolveSettingsPlacement({ preferred: 'right-start', anchorRight: 50, innerWidth: 660 }), 'right-start')
assert.equal(resolveSettingsPlacement({ preferred: 'right-start', anchorRight: 50, innerWidth: 659 }), 'top')
```

Pin one Teleport containing the popover and all four dialogs. Pin new prop defaults, accessible hidden label, tooltip placement, and no trigger badge/container rules.
Pin `@wa-after-hide.self`, synchronous `onPopoverShow`, retained `afterSwap = null`, awaited Lit readiness, latest-prop reads, and unmount guards.
Compile the template and scoped styles, including CSS `v-bind()`.

- [ ] **Step 2: Run `node --test frontend/src/utils/settingsPopoverPlacement.test.js`.** Expect a missing-module failure.
Run `cd frontend && node --test src/components/app/settingsPopoverRail.test.js src/styles/settings-motion.test.js` when dependencies exist. Expect the new contract assertions to fail.

- [ ] **Step 3: Implement the placement helper.** Return the preferred placement unless it starts with `right` and cannot fit:
`innerWidth < anchorRight + Math.min(0.9 * innerWidth, 700) + 16`. In that case return `'top'`.

- [ ] **Step 4: Add the trigger props, hidden label, and local switch.** Label: `Keep the icon bar visible when the sidebar is closed`.
Place the switch next to Compact session list. Bind the Task 2 getter; use `onSidebarRailVisibleWhenClosedChange(event)` to call the setter with `event.target.checked`.
Keep Home's label `Settings`, tooltip `Toggle settings`, and default placement.
Remove the Settings badge and its dead styles/imports. Retain any peers-store usage needed elsewhere.
Add `.settings-trigger--icon-only` and scoped hidden-span rules exactly as specified under Removals.
Apply the spec's icon-only `::part(base)` resets and hover/pressed layers only to that class. Preserve gear motion.

- [ ] **Step 5: Teleport the popover and four dialogs to `body`.** Keep trigger and tooltip in place.
Read sizing constants through computed CSS strings and `v-bind()` for `--max-width` and `.settings-layout`.
On `wa-show`, set `popoverOpen = true`; calculate placement synchronously from the actual anchor's right edge and `window.innerWidth`.
Skip that calculation for preferred `top` or absent anchor. Preserve all existing reset/seeding statements.

- [ ] **Step 6: Implement the anchor lifecycle.** `onMounted` and the prop watcher route through `applyAnchor()`.
Await the current popover's `updateComplete`; return if it unmounts. Read the latest prop after the await.
Defer writes while `popoverOpen` is true. Otherwise use the non-null prop, or restore `document.getElementById('settings-trigger')`.
Write only a non-null, changed anchor. Do not change `for` or add reconnection recovery.
`onPopoverAfterHide()` preserves `resetTransientControls()`, clears the flag, and calls `applyAnchor()`.

- [ ] **Step 7: Run the helper and SFC tests above.** Expect all tests to pass.
Manual check after Task 6: command-open Settings with the rail hidden; switch the visibility setting while editing; verify retained focus/scroll.
Verify nested dropdown hide does not apply an anchor; rapid prop changes use the latest anchor; unmount causes no late write.
Verify Home's accent trigger, dialogs, and Settings opening still work. Placement recalculates on opening, not resize while open.

- [ ] **Step 8: Commit the helper, component, and tests.** Subject: `feat(settings): support icon rail trigger and floating anchor`.

## Task 4: Cancel Tooltips Before Their Anchors Hide

**Files:** Modify `frontend/src/components/ui/AppTooltip.vue` and `frontend/src/styles/overlay-motion.test.js`.

**Interfaces:**
- Produces: named SFC export `hideAllTooltips() -> void`.
- Module-local `mountedTooltips: Set<Element>` and `clearPendingTimer(el) -> void` live in a plain `<script>`.
- Existing per-instance interactive-tooltip behavior remains in `<script setup>`.

- [ ] **Step 1: Add registry and cancellation tests to `overlay-motion.test.js`.** Read the plain script separately from `scriptOf`.
Pin registration when listeners bind, removal in `stopListening`, and `clearPendingTimer(el)` before `el.hide()`.
Evaluate the plain script with fake tooltip elements and a numeric timer scheduler, using Node's built-in facilities.
The scheduler returns integer handles and removes cancelled callbacks before flushing them. Native Node timer objects are not browser timer handles.
Preserve the production numeric `hoverTimeout` check. Assert every registered element hides.
Assert a scheduled show callback never runs after cancellation. Assert an unregistered element is not visited.
Keep the existing 250ms show-delay and distance assertions in the setup-script checks.

- [ ] **Step 2: Run `cd frontend && node --test src/styles/overlay-motion.test.js`.** Expect new export/registry assertions to fail.
- [ ] **Step 3: Implement the plain-script registry and export.** Move `clearPendingTimer` into it. Add/remove elements at the existing listener lifecycle points.
Keep `openInteractiveTooltips`, `TOOLTIP_SHOW_DELAY_MS`, and `TOOLTIP_DISTANCE` in setup. Do not change touch handling.
- [ ] **Step 4: Run the same command.** Expect existing motion and new cancellation tests to pass.
Manual timer and focus checks belong to Task 6, where anchors actually hide.
- [ ] **Step 5: Commit both files.** Subject: `fix(tooltips): cancel pending shows when sidebar controls hide`.

## Task 5: Build the Rail Presentation and Shared Button Styles

**Files:** Create `frontend/src/components/sidebar/SidebarRail.vue` and `.test.js`; create `frontend/src/styles/rail-button.css` and `.test.js`; modify `frontend/src/main.js`.

**Interfaces:**
- Consumes: Task 1 resolver; Task 3 Settings props; `PeerInboxBadge`; `AppTooltip`; `settingsStore.isMac`.
- Props: `mode: 'sessions' | 'artifacts'`, `sidebarOpen: boolean`, `peerConfigured: boolean`, `inboxCount: number`, `settingsAnchor: Object = null`.
- Emits: `home`, `select-mode(mode)`, `search`, `palette`, `inbox`, `toggle-sidebar`.
- Produces: root `.sidebar-rail`, toggle id `sidebar-rail-toggle`, shared `.rail-button`, and root `--rail-*` tokens.

- [ ] **Step 1: Write SFC and stylesheet contract tests.** Compile the SFC with the existing compiler-test pattern.
Pin native buttons, `aria-label`, `:aria-pressed="item.active"`, disabled binding, and no `aria-expanded` or `glass-*` class.
Pin two keyed `<template v-for>` groups around one spacer, tooltip after button, and stable Settings key.
Pin Settings props `plain`, icon-only, `right-end`, tooltip `right`, item label, and passed anchor.
Pin `.panel-card`, root size container `rail`, scrolling below `22rem`, spacer flexibility, and nonshrinking buttons/Settings wrapper.
Pin the global unlayered CSS values, hover media guard, active precedence, no transitions, and the stylesheet import.

- [ ] **Step 2: Run `cd frontend && node --test src/components/sidebar/SidebarRail.test.js src/styles/rail-button.test.js`.** Expect missing-file failures.

- [ ] **Step 3: Write the shared stylesheet and import it.** Use these exact values:

| Token | Value |
|---|---|
| `--rail-button-size` | `2.25rem` |
| `--rail-icon-size` | `1.125rem` |
| `--rail-button-color` | `var(--wa-color-text-quiet)` |
| `--rail-button-fill-hover` | `var(--wa-color-neutral-fill-quiet)` |
| `--rail-button-fill-active` | `var(--wa-color-brand-fill-quiet)` |
| `--rail-button-color-active` | `var(--wa-color-brand-on-quiet)` |
| `--rail-card-padding`, `--rail-gap` | `0.25rem` each |
| `--rail-card-width` | `calc(var(--rail-button-size) + 2 * var(--rail-card-padding) + 2 * var(--divider-size))` |
| `--rail-width` | `calc(var(--rail-card-width) + var(--panel-gap))` |

Implement the `.rail-button` declaration table in the spec's Visual style section, including zero icon margins and retained native focus/disabled styles.
Use background-image layers for hover, press, and active mode. Active mode wins over hover and press.
Import after `tool-cards.css`, before `scrollbars.css`. Preserve the adjacent surface/sidebar/options imports.

- [ ] **Step 4: Implement `SidebarRail.vue`.** Use `<nav aria-label="Main navigation" class="sidebar-rail">` and a direct-child card.
Resolve items reactively; split by group. Render the Settings fragment in a flex wrapper; make tooltip hosts absolute.
Use `sidebar-rail-<id>` anchors except Settings. Buttons emit their action; navigation stays in the parent.
Render `PeerInboxBadge` for the inbox item. Do not clip it in normal-height cards.
The slot has flex none, relative positioning, width `--rail-width`, size containment, and desktop z-index 3.
The card uses flex column, specified gaps/padding, block margins `--panel-gap`, and left margin `--panel-gap`.
Below 640px use z-index 101 and opaque canvas underlay with fixed background attachment.
Below container height `22rem`, use vertical scrolling, hidden horizontal overflow, and hidden scrollbars.

- [ ] **Step 5: Add the threshold calculation test to `rail-button.test.js`.** Read tokens from CSS, divider size from App, gaps from surfaces, and ring width/offset from glow.
Resolve WA spacing using a documented map: `--wa-space-xs = 0.5rem`, `--wa-space-2xs = 0.25rem`.
Get the maximum item count from the real resolver with peers enabled, over both sidebar states.
Assert button heights + item-count gaps + double padding/border/panel gaps fit below 22rem, for desktop/mobile and 12/16/32px roots.
Assert card padding is at least focus-ring width plus offset. Adding an item must invalidate an insufficient threshold.

- [ ] **Step 6: Run the same SFC/style command plus Task 1 tests.** Expect all to pass.
Manual check after Task 6: peer configuration changes keep an open Settings popover mounted; Tab order follows the visual order.
- [ ] **Step 7: Commit the component, stylesheet, import, and tests.** Subject: `feat(sidebar): render shared vertical icon rail`.

## Task 6: Integrate State, Responsive Layout, and Floating Clearance

**Files:** Modify `frontend/src/views/ProjectView.vue`, `frontend/src/App.vue`, `frontend/src/components/session/layout/SessionLayout.vue`, `frontend/src/components/message/CollapsedBar.vue`, `frontend/src/components/message/MessageInput.vue`, `frontend/src/components/terminal/TerminalExtraKeysBar.vue`, `frontend/src/components/session/SessionsSidebarControls.vue`, `frontend/src/components/peer/PeerInboxButton.vue`, `frontend/src/styles/main-content-height.test.js`, `frontend/src/styles/overlay-motion.test.js`; create `frontend/src/views/ProjectViewRail.test.js` and `frontend/src/styles/sidebar-clearance.test.js`; delete `SidebarViewSwitch.vue` and `CommandPaletteButton.vue` at their File Map paths.

**Interfaces:**
- Consumes Tasks 1–5 exports, component props, and events.
- Internal `checked = ref(initialSidebarChecked.value)`, `sidebarOpen = computed(() => isNarrowViewport.value ? checked.value : !checked.value)`.
- Internal `railCollapsed = computed(() => !sidebarOpen.value && !settingsStore.isSidebarRailVisibleWhenClosed)`.
- Internal `floatingToggleEl = shallowRef(null)`, `syncSidebarState() -> void`, `handleRailToggle() -> Promise<void>`, `handleFloatingToggle() -> Promise<void>`, `handleRailSelectMode(mode) -> void`.
- Produces body class `sidebar-toggle-floating`, wrapper attribute `data-rail-when-closed="visible" | "hidden"`, and wrapper peer class.

- [ ] **Step 1: Write integration source/SFC tests.** Pin checkbox-based CSS selectors for both breakpoints and complementary floating-toggle visibility.
Pin `--rail-width: 0px` on the wrapper, hidden rail overflow/visibility, and unconditional rendered floating button.
Pin effective state, both `< 640px` matchMedia strings, all synchronization callers, post-flush initial-checkbox watcher, and immediate body watcher with unmount removal.
Pin tooltip watcher `watch([sidebarOpen, railCollapsed]` calling `hideAllTooltips()` without `immediate`.
Pin nextTick focus transfers, Settings anchor conditional, mode no-op, action wiring, footer gating, and removed old controls.
Add a truth-table assertion over viewport side, checkbox state, and setting:

```js
const sidebarOpen = narrow ? checked : !checked
const railCollapsed = !sidebarOpen && !keepVisible
assert.equal(railVisible, !railCollapsed)
assert.equal(floatingVisible, railCollapsed)
assert.equal(railVisible || floatingVisible, true)
assert.equal(railVisible && floatingVisible, false)
```

The test must inspect the actual source expressions/selectors, not only repeat this table. Runtime browser checks below verify the DOM behavior.
In `sidebar-clearance.test.js`, pin all five consumers to `sidebar-toggle-floating`, including the three formerly unconditional mobile paddings.
Update `main-content-height.test.js` to remove only the obsolete floating-toggle transition assertion. Preserve drawer reduced-motion invariants.

- [ ] **Step 2: Run `cd frontend && node --test src/views/ProjectViewRail.test.js src/styles/sidebar-clearance.test.js src/styles/main-content-height.test.js src/styles/overlay-motion.test.js`.** Expect new integration assertions to fail.

- [ ] **Step 3: Replace the stale sidebar state with the effective state contract.** `syncSidebarState()` reads the real checkbox; absent checkbox retains `checked`.
Update every old funnel caller: session-change close, width reset, mount, both split-reposition branches, and toggle handling.
Add `watch(initialSidebarChecked, syncSidebarState, { flush: 'post' })`. Both viewport-query helpers use `(width < ${MOBILE_BREAKPOINT}px)`.
Width reset reads the real checkbox; it does not invent a new open state. Keep drag suppression, snaps, width persistence, and `toggleSidebar()` change-event behavior.
Watch `railCollapsed` immediately to set the body class; remove it on `onBeforeUnmount`. Remove the old state ref/class writer.
Watch `[sidebarOpen, railCollapsed]` to hide all tooltips.

- [ ] **Step 4: Wire the rail and floating toggle.** Add `openPalette` to `useCommandRegistry()` destructuring.
Wire home to `handleBackHome`, search to existing `openAdvancedSearch`, palette to `openPalette`, and inbox to `twicc:open-peer-inbox`.
`handleRailSelectMode` calls existing `toggleSidebarView()` only for another mode. Preserve route location memory.
Render rail after the hidden checkbox, then floating toggle + tooltip, then split panel. Pass floating ref as Settings anchor only when collapsed.
Pass mode as `isArtifactsMode ? 'artifacts' : 'sessions'`, peer configuration as `peerSystemConfigured`, and count as `peersStore.inboxCount`.
Use that same count for the floating badge condition.
Floating button: `id="sidebar-toggle-button"`, classes `sidebar-toggle rail-button`, static `angles-right`, exported open label, next-sibling tooltip `top`.
Render badge when `inboxCount > 0`, without a peer-configured gate. Both click handlers call `toggleSidebar()` then await `nextTick()`.
Floating click focuses the rail toggle; rail click focuses the floating toggle only if now collapsed.

- [ ] **Step 5: Implement responsive CSS.** Wrapper: relative flex row, full viewport height. Split panel: flex 1, min-width 0.
Use the spec's Visibility selectors in each breakpoint to zero width, hide the rail, and show the floating toggle together.
Floating toggle: absolute, normally visibility hidden, z-index 5, opaque base surface plus panel border/radius/shadow. No transitions.
Offset `--wa-space-s`, or `--wa-space-xs` with `.project-view-wrapper--peer`; desktop adds `--panel-gap` to left and bottom offsets.
Mobile drawer: `left: var(--rail-width)`, `--sidebar-width: min(300px, calc(80vw - var(--rail-width)))`, closed transform `translateX(calc(-100% - var(--rail-width)))`.
Backdrop: `left: var(--rail-width)` after `inset: 0`. Keep existing drawer/backdrop animation rules and z-indices.
Re-key all desktop clearance rules to `body.sidebar-toggle-floating`; re-key the three unconditional mobile rules too. Keep clearance values and desktop width guards.

- [ ] **Step 6: Remove replaced controls and fix the remaining footer.** Remove back button/tooltip, mode switch, advanced-search button/emit/listener, footer action row, placeholder, and in-footer divider.
Delete the two unused components. Keep Home's `PeerInboxButton`, removing its dead container rules.
Remove old toggle icon swaps, translation/motion rules, shift/offset definitions, footer-inset indirection, and old Settings/palette container rules.
Retain `.sidebar-header { visibility: hidden }` in the <=50px container rule. Change project-selector max-width to `50rem`.
Define boolean `hasSidebarFooter = computed(() => !!((quotaHasUsage.value && quotaComputed.value) || unauthenticatedProviders.value.length))`.
Gate sidebar-level footer divider and footer wrapper with it. Add `sidebar--no-footer` and bottom padding `--panel-gap` only when false.
Split the shared divider selector list: keep the sidebar-level usage selector; remove the in-footer selector.
Keep project selector, filter controls, lists, usage card, auth callouts, and new-session actions.

- [ ] **Step 7: Run the Task 6 tests and all new tests.** Expect all to pass.
The whole-source obsolete-name guard is added in Task 7 after comment cleanup.

- [ ] **Step 8: Verify behavior in a running checkout when available.** Test this matrix; record results rather than adding dependencies:

| Conditions | Required result |
|---|---|
| Desktop/mobile; open; setting on/off | Rail visible; floating toggle absent |
| Desktop/mobile; closed; setting on | Rail visible; no floating-toggle clearance |
| Desktop/mobile; closed; setting off | Rail hidden; floating toggle and necessary composer/terminal/dock clearance visible |
| 639px, 639.5px, 640px; resize in both directions | CSS, labels, effective state, and body class agree; checkbox meaning flips |
| Toggle click, Alt+Shift+B, palette command, backdrop, session change, selector-focus command, drag collapse, reset | No stale state; persistence and width remain functional |
| Rail toggle with setting off; floating toggle click | Focus reaches the other toggle after update |
| Click before 250ms; hover any rail button then keyboard-toggle | Tooltip closes; delayed timer never reopens it; hidden floating anchor retains its rect during hide |
| Sessions/Artifacts; active-mode click; session/non-session routes | Correct active state; active click does nothing; remembered destinations and existing mobile route behavior remain |
| Both modes; search/palette/home/inbox | All actions work; search available in artifacts mode |
| Settings open, peer setup changes, visibility setting changes | Stable Settings instance; deferred anchor swap preserves editing state |
| Settings opened by command or telemetry while rail hidden | Popover anchors to floating toggle and fits; all four dialogs have opaque surfaces |
| 360px viewport with large root; 320px viewport; 12/16/32px roots; short landscape height | Drawer stays within viewport; backdrop has tappable strip; card scrolls; focus ring remains usable; narrow-sidebar rules remain valid |
| Light/dark; reduced motion; mouse/touch; Tab/Shift+Tab | Correct surfaces, no new motion, no sticky touch hover, accessible labels and expected tab order |
| Usage/auth present or absent | Footer/divider shown only with content; empty sidebar retains bottom inset |
| Home, expanded content preview, ConnectionIndicator | Home controls work; expanded preview covers desktop rail; accepted indicator overlap remains |

Accepted behavior: non-click hiding paths drop focus to the page. Settings anchor changes remain deferred until after-hide.
Tab leaving the teleported popover reaches body-end controls. Placement does not update during an open-window resize.
The short-height badge may clip; scrollbars stay hidden. With the setting off, rail width snaps while the mobile drawer slides.
Do not redesign these accepted trade-offs during implementation.

- [ ] **Step 9: Commit integration files, deletions, and tests.** Subject: `feat(sidebar): integrate icon rail and floating reopen control`.

## Task 7: Align User Help and Remove Obsolete References

**Files:** Modify `frontend/public/tips/full-text-search-mac.md`, `frontend/public/tips/full-text-search-non-mac.md`, `frontend/public/tips/artifacts.md`, `frontend/public/help/what-are-artifacts.md`; modify comments in the files below; extend `frontend/src/styles/sidebar-clearance.test.js`.

Comment files, in addition to previously modified files:
- `frontend/src/styles/sidebar-rows.css`, `frontend/src/styles/sidebar-filter.test.js`.
- `frontend/src/components/artifacts/ArtifactBookmarksSidebarControls.vue`.
- `frontend/src/components/session/detail/SessionHeader.vue`.
- `frontend/src/components/peer/PeerInboxBadge.vue`.
- `frontend/src/composables/useWebSocket.js`, `frontend/src/composables/usePeerSystemConfigured.js`.
- `frontend/src/components/app/TelemetryNoticeDialog.vue`, `frontend/src/components/app/SearchOverlay.vue`.
- `frontend/src/commands/staticCommands.js`, `frontend/src/utils/sidebarViewMemory.js`.

**Interfaces:** No new runtime interfaces. User descriptions name the icon bar and its buttons.

- [ ] **Step 1: Add the recursive obsolete-name guard.** Walk `frontend/src` using the `glass.test.js` source-walk pattern.
Exclude tests. Assert `.vue`, `.css`, `.js` sources have none of:
`sidebar-closed`, `updateSidebarClosedClass`, `sidebarClosed`, `sidebar-toggle-label`, `search-advanced-button`, `SidebarViewSwitch`, `CommandPaletteButton`.
Keep the assertion that all five clearance consumers contain `sidebar-toggle-floating`.
- [ ] **Step 2: Run `node --test frontend/src/styles/sidebar-clearance.test.js`.** Expect remaining stale comments to fail.
- [ ] **Step 3: Update tips/help.** Replace old sidebar-search/+ descriptions with `Full-text search button in the icon bar`.
Replace old artifact-switch location descriptions with `Artifacts button in the icon bar`.
Read `frontend/public/help/peers.md`; its inbox-next-to-Settings description remains valid. Change it only if other obsolete location text exists.
- [ ] **Step 4: Update source comments.** Use the spec's Removals comment inventory as the checklist.
Search comments for the removed view switch, footer toggle, sidebar inbox, decorated search, and former back/search locations.
Keep valid generic references to sidebar toggling. Describe clearance as conditional on the floating reopen button.
Do not change shortcuts help, skills, or CLI documentation: their behavior stays unchanged.
- [ ] **Step 5: Run the guard, then `cd frontend && npm test` when dependencies exist.** Expect zero failures.
Check existing motion, settings-motion, glass, glide, glow, sidebar-rows, question-options-motion, settingsNavigation, publicOriginSettings, settingsAgentShares, and colorSchemeTransition tests.
Do not weaken unrelated assertions to obtain a passing suite.
- [ ] **Step 6: Run `cd frontend && npm run build` when dependencies exist.** Expect all existing bundle builds to succeed.
No standalone-bundle source changes are required; this checks the integrated SFC/CSS compilation.
- [ ] **Step 7: Check `git diff --check` and the changed-file list.** Expect no whitespace errors, no backend/dependency changes, and no edits to the spec.
- [ ] **Step 8: Commit help, comment cleanup, and guard.** Subject: `docs(sidebar): align help with the icon bar`.

## Completion Evidence

The implementation handoff reports:
- Completed tasks and any remaining verification blocks.
- Results of helper tests, new SFC/source tests, full frontend suite, and build.
- Results of the Task 6 browser matrix, including hidden Settings anchoring and breakpoint transitions.
- Commit identifiers, if commits are authorized at execution time.

This planning task stops after saving and self-checking this document. Implementation requires a separate user instruction.
