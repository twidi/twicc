# Step 7f — settings content crossfade (design)

Status: draft for review. Parent: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §6m (7f), values
§8.11 / §9 "Step 7". Decisions by the user (2026-09-30): exit then enter (no overlap), the detail
panel scrolls back to the top on a section change, the fade is desktop only (the mobile slide stays
alone), and the Tips and Help entries are always in the menu.

## 1. Goal

When the user picks another section in the settings popover, the content of the right panel does not
change at once. The old section fades out fast, then the new one fades in with a small rise. The
section indicator already glides in the left menu (step 4c); only the content is missing.

Also in this step (user decision): the **Tips** and **Help** menu entries stop being conditional.

Out of scope: the gliding indicator itself, the mobile panel slide, the content of any section, the
popover's own open / close animation.

## 2. Where it applies (verified in code)

All in `frontend/src/components/app/SettingsPopover.vue`:

- The content is `div.settings-detail` (the scroller) > `div.settings-sections` > a flat list of
  `v-if="activeSection === '…'"` siblings (`general`, `providers`, one `ProviderSettingsSection` per
  registered provider via a `v-for` over `providerSections` (only the nav filters on enabled), `notifications`, `mcp`, `sharing`, `peers`,
  `sessions`, `layouts`, `title`, `editor`, `terminal`, `tips`, `help`, `usage`, `shortcuts`).
  Some are plain `<section>`, some are components (`NotificationSettings`, `McpSettings`,
  `TipsSettings`, `HelpSettings`, `ProviderSettingsSection`). There is no common root, no transition.
- `selectSection(id)` sets `activeSection` and `mobileShowContent`. `goToPublicBaseUrl()` (called by the
  Notifications callout) calls `selectSection('general')` then focuses the External address input in a
  `nextTick`.
- `hasTips` / `hasHelp` (computed from `availableTips` / `availableHelp`, themselves from
  `tipsStore.getAvailableTips` / `helpStore.getAvailableHelp`, which filter on platform, OS and enabled
  providers) gate the two menu entries (`v-if`, template lines ~1309 and ~1317), feed two `watch`es that
  bounce the user to General, and are listed in the glide `sources`.
- `TipsSettings.vue` (`tips-empty`) and `HelpSettings.vue` (`help-empty`) already render an empty state.
  The "Tips enabled" switch lives in `TipsSettings`: with the entry hidden (empty list) it is unreachable.
- Mobile (`@media (width < 640px)`): `.settings-layout-inner` is 200% wide and slides
  (`transition: transform 0.25s ease`, class `showing-content`) from the section list to the content.

## 2b. Tips and Help always in the menu

- Remove `v-if="hasTips"` and `v-if="hasHelp"` from the two nav buttons.
- Remove the two bounce `watch`es, `hasTips`, `hasHelp`, and `hasTips, hasHelp` from the `useGlideInk`
  `sources` (`[activeSection, sections]` remains). Also remove `availableTips` / `availableHelp`, the
  `tipsStore` / `helpStore` instances and their imports (verified: they are read only by these
  computeds, lines ~60-61, 70 and 79), and the three comment blocks that describe the gating (lines
  ~65-69, ~77-78 and ~149-152).
- The empty states of the two section components are the behaviour when a list is empty: unchanged.
- Consequence: the Tips and Help sections can no longer vanish under the user. (A provider section can
  still lose its nav entry when that provider is disabled; that case is unchanged and out of scope.)
  The active section changes by a click in the nav, by `goToPublicBaseUrl`, or by the in-content link
  of the Usage section (`selectSection('notifications')`, line ~2069). On an in-content link the
  focused link becomes inert and focus drops to `body`: same as today, where the `v-if` removes it.

## 3. Behaviour

| Moment | Old content | New content |
|---|---|---|
| Click on another section (desktop) | Its components unmount at once (Vue); their frozen DOM fades out, 120ms (`--motion-dur-1`), `ease-in-out`, and is inert (no pointer, no Tab) | Not mounted yet |
| End of the exit | Removed; the panel scrolls to the top (same task, the new element is still at opacity 0) | Mounted, fades in 200ms (`--motion-dur-2`, `ease-in-out`) and rises 0.75rem → 0 over 380ms (`--motion-dur-3`, `--motion-ease-out`): the rise outlasts the fade so its end stays visible (browser review amendment) |
| Click on the section already chosen | Nothing | Nothing (no key change) |
| Second click during the exit | Exit finishes | Only the LAST chosen section enters (Vue's `out-in` renders the latest state after the exit) |
| Click during the enter | Vue cancels the enter: the half-risen content snaps to its place (the rise stops) and fades out from its current opacity | The next section enters after that exit |
| First display of the popover | — | No animation (no `appear`): the swap only runs on a section change |
| Mobile (< 640px) | Instant | Instant, synchronous: `css` is off and the mode is the default there (§4.1), the existing slide is the only transition |
| Reduced motion | Fade stays | Fade stays, the rise is 0 (`--motion-amount: 0`): "reduced motion ≠ no motion" |

Total: about 390ms from click to fully visible (each phase adds two animation frames, ~33ms, on top of
its duration).

## 4. Mechanism

### 4.1 Markup

One `<Transition>` inside `div.settings-sections`, wrapping ONE keyed element:

```html
<div class="settings-sections">
    <Transition
        name="settings-swap"
        :mode="isNarrow ? undefined : 'out-in'"
        :css="!isNarrow"
        @before-leave="onSectionLeaving"
        @enter="onSectionEnter"
        @after-leave="onSectionLeft"
    >
        <div :key="activeSection" class="settings-swap">
            …the existing v-if sections, untouched and NOT re-indented (minimal diff)…
        </div>
    </Transition>
</div>
```

- The wrapper is required: the sections are several roots (components, fragments); a Transition
  needs exactly one. The key is `activeSection`, so each provider section and each plain section is
  its own step.
- `.settings-swap` has no styles of its own (a block `div`): the descendant rules `.settings-sections
  .settings-section …` (unscoped block at the end of the file, and the comment in
  `ProviderSettingsSection.vue`) keep matching. The implementer greps `settings-sections >` (child
  combinators) in `frontend/src` first; any hit is a blocker to report.
- At the click Vue unmounts the old section's COMPONENTS at once (effects stopped, refs nulled,
  `onBeforeUnmount` run) and keeps only their DOM for the exit; the new subtree mounts after the exit.
  So the `v-if` lifecycle is unchanged, only the removal of the DOM and the mount of the new section
  are delayed.
- `isNarrow = useMediaQuery('(width < 640px)')`, with a new `import { useMediaQuery } from
  '@vueuse/core'` (the file has none today; there is no shared narrow flag to reuse; same breakpoint
  as the slide rules in the CSS). With `:css="false"` Vue runs no class or timer and the JS hooks
  still run, so on a phone the swap is instant and never shows an empty panel.
- **`mode` must drop to the default when `css` is off.** `out-in` with `css: false` and no `@leave`
  hook crashes Vue 3.5.27 (the leave completes synchronously and the nested `instance.update()` reads
  `parentNode` of a placeholder comment with no `el`; reproduced by the round-2 reviewer). In the
  default mode on a phone the old DOM goes at once and the new one enters at once (`after-leave` then
  runs before the new element is inserted: harmless, it only resets the scroll).
- `onSectionLeaving(el)` sets `el.inert = true`: the leaving DOM takes no click and no Tab, and its
  plain-`<section>` listeners (inputs bound in `SettingsPopover`) can no longer be reached.

### 4.2 CSS (in the component's scoped block, near the detail panel rules)

```css
.settings-swap-leave-active {
    transition: opacity var(--motion-dur-1) ease-in-out;
}
.settings-swap-leave-to {
    opacity: 0;
}
.settings-swap-enter-active {
    transition:
        opacity var(--motion-dur-2) ease-in-out,
        translate var(--motion-dur-2) var(--motion-ease-out);
}
.settings-swap-enter-from {
    opacity: 0;
    translate: 0 calc(0.375rem * var(--motion-amount));
}
```

There is no mobile media query: a CSS-only "off" is wrong because Vue still adds `enter-from`
(opacity 0) and waits two frames, which would show the old content for ~2 frames, then an empty panel
for ~2 frames, under the sliding panel. `:css="!isNarrow"` (§4.1) is the switch.

- Fades use `ease-in-out` (roadmap §6i: `--motion-ease` is front-loaded, a fade with it reads as a snap).
- The movement is multiplied by `--motion-amount`; `translate` is `none`/`0` at rest (no
  `transform`, no `rotate`, no bare `scale`).
- Vue reads the durations from the computed style (`transition` type, auto-detected), so the CSS is the
  single source of the timings.
- The popover can be closed while the section changes (`display: none`, no `transitionend`): Vue's
  timeout fallback (computed duration + 1ms) completes the swap.
- The wrapper animates `opacity` on an ancestor of the section content (roadmap §6h lesson: an
  opacity animation on an ancestor cuts the blur of a glass layer inside it). The sections' glass
  parts are safe: `TipsSettings` / `HelpSettings` only use the flat `--glass-item-rest/-hover`
  colours, and the `wa-select` listboxes and tooltips (glass, `glass.css`) render in the top layer
  (`wa-popup`, `popover="manual"`), which an ancestor's opacity does not reach; `wa-dialog`s are
  modal. The glass header `.settings-detail-header` is outside the wrapper.

### 4.3 Scroll back to the top

`const detailRef = ref(null)` on `div.settings-detail`; `onSectionLeft()` sets
`detailRef.value.scrollTop = 0`. `after-leave` runs after the old element is removed AND the new one
is inserted (Vue's `out-in` updates synchronously in the leave callback), but all in the same task,
and the new element is still at `enter-from` (opacity 0): the jump is invisible. The explicit
assignment makes it deterministic (the browser would only clamp it). It runs on mobile too, also when
the change comes from inside the content (the Usage link, `goToPublicBaseUrl`): the new content is
then shown at its top, which is the wanted result.

### 4.4 Focus after a swap (`goToPublicBaseUrl`)

The External address input sits in the General section, which now mounts up to ~155ms after the click (exit + two frames):
the current `nextTick(() => publicBaseUrlInputRef.value?.focus())` finds a null ref. Replace it with a
one-slot callback run by the Transition's `enter` hook:

```js
let afterSwap = null
function onSectionEnter() {
    const run = afterSwap
    afterSwap = null
    if (run) nextTick(run)
}
function goToPublicBaseUrl() {
    const swaps = activeSection.value !== 'general'
    selectSection('general')
    const focusInput = () => publicBaseUrlInputRef.value?.focus()
    if (swaps) afterSwap = focusInput
    else nextTick(focusInput)
}
```

- `onSectionEnter` has ONE parameter or none: a second parameter would make Vue wait for a `done`
  callback that never comes (`hasExplicitCallback`).
- The slot is cleared in `onPopoverShow` so a swap that never fired (hook missed) cannot steal the
  focus later.
- The other post-click work in `selectSection` is unaffected: the `nextTick(() =>
  notificationSettingsRef.value?.sync())` is null-safe, and a freshly mounted `NotificationSettings`
  reads the permission state at setup (`sync()` only matters for an already mounted instance, i.e. a
  click on the section already shown, which swaps nothing).

## 5. Invariants (do not regress)

1. The wrapper is the ONLY child of the Transition and is keyed by `activeSection`.
2. Timings come from `--motion-dur-1` (exit), `--motion-dur-2` (enter fade) and `--motion-dur-3`
   (enter rise); no literal `ms`.
3. `translate` only, scaled by `--motion-amount`; no `transform`, `rotate`, `scale`.
4. Fades use `ease-in-out`, the rise `--motion-ease-out`.
5. The Transition is bound `:css="!isNarrow"` AND `:mode="isNarrow ? undefined : 'out-in'"` (never
   `out-in` with `css` off: crash), `isNarrow = useMediaQuery('(width < 640px)')` (the slide rules'
   breakpoint); no mobile media query on the swap classes.
6. The exit takes no input: `onSectionLeaving` sets `el.inert = true`.
7. No `appear`: opening the popover never fades.
8. `onSectionEnter` takes at most one parameter.
9. The Tips / Help nav buttons carry no `v-if`; `hasTips`, `hasHelp` no longer exist.
10. The sections' own markup and CSS are not modified.

## 6. Limitations (accepted)

- A section's local component state resets at each switch (already true with `v-if`; the exit delay
  does not change it).
- The old section's components unmount at the click, only their DOM fades. A component whose look
  depends on live JS state shows its teardown state during the exit: `SegmentedControl` (used in the
  provider sections) destroys its glide ink at the click, so its checked segment falls back to the
  flat fill while the section fades. Accepted (120ms, fading out).
- The exit delays the mount of the new section by one exit; a section with a slow setup shows its
  content that much later.
- The 0.75rem rise enlarges the scroller's scrollable overflow for ~380ms: on a section that nearly
  fills the panel a scrollbar or the bottom scroll shadow can flash.

## 7. Tests (`node:test`, source pins)

New `frontend/src/styles/settings-motion.test.js`, same conventions as `question-options-motion.test.js`
(compile the SFC style with `compileStyle`, pin each rule whole). `assertMotionInvariants` is local
to `motion.test.js` (not exported): extend the existing `SettingsPopover.vue` entry of the `changed`
list of its test 8 (`motion.test.js:431`, pick `(s) => s.includes('#settings-trigger')`) to
`(s) => s.includes('#settings-trigger') || s.includes('.settings-swap')`.

1. Template: one `<Transition name="settings-swap" :mode="isNarrow ? undefined : 'out-in'"
   :css="!isNarrow">` inside
   `.settings-sections`; its only child is a `div.settings-swap` with `:key="activeSection"`; no
   `appear`; hooks `@before-leave`, `@enter`, `@after-leave` bound to `onSectionLeaving` /
   `onSectionEnter` / `onSectionLeft`; `isNarrow` comes from `useMediaQuery('(width < 640px)')`.
2. The four transition rules pinned whole (declarations and order); no `@media` block that mentions
   `settings-swap`; no literal `ms` in the four `settings-swap` rules (the block has an unrelated
   `rotate 600ms` for the gear); `translate` uses `var(--motion-amount)`.
3. `onSectionLeft` assigns `scrollTop = 0` on `detailRef`; `div.settings-detail` carries
   `ref="detailRef"`; `onSectionLeaving` sets `el.inert = true`.
4. `goToPublicBaseUrl` uses the `afterSwap` slot; `onSectionEnter` has no second parameter and clears
   the slot before running it; `onPopoverShow` also clears the slot.
5. Tips / Help: `settingsNavigation.test.js` gains pins that the two buttons have no `v-if`, and that
   `hasTips`, `hasHelp`, `availableTips`, `availableHelp` do not appear in `SettingsPopover.vue`.
6. `glide.test.js` line 256: the expected `sources` string becomes `'sources: [activeSection, sections]'`.
7. Any other existing test that mentions the removed names is updated (the implementer runs the whole
   suite: `cd frontend && npm test`).

Mutation proof by the reviewer (sha1 of the file before and after each mutant): remove the `key`, the
`mode` binding (turn it into a plain `mode="out-in"`), the `:css` binding, the `inert` line, the `scrollTop` line, the `afterSwap` clear in
`onSectionEnter`, the `afterSwap` clear in `onPopoverShow`, re-add a `v-if` on a nav button, each must
turn a test red.

## 8. Browser review checklist (user)

- Desktop: click through the sections; the old content fades out fast, the new one rises in. No jump
  of the panel height or of the footer below.
- Scroll a long section (Providers), switch: the next section starts at its title, without a visible
  jump.
- Click two sections quickly: only the last one appears, no flicker of the middle one.
- Click a section, then another one while the first is still fading in: no flicker beyond the
  half-risen content snapping to its place, then it fades out from its current opacity.
- A long section nearly filling the panel: no scrollbar flash while the new content rises.
- During the exit, Tab and clicks do nothing in the fading content.
- Notifications → click the callout "External address": General opens and the input is focused.
- Tips and Help are always in the menu; with an empty list they show their empty message.
- Phone width (< 640px) — MANDATORY, no source pin catches a Vue crash here: tap a section; only the
  slide plays, content changes behind it, no empty panel, no console error; then widen the window
  past 640px and back (the mode and css switch live) and change section on each side.
- Reduced motion on: the fade stays, nothing rises.
- Close and reopen the popover: no fade on the first display.

## 9. Risks

| Risk | Mitigation |
|---|---|
| `out-in` rapid clicks show an intermediate section | Vue re-renders with the latest state after the exit; checked in the browser review |
| A `nextTick` that assumed the section is mounted | Audited above (`sync` is null-safe and redundant on mount, `goToPublicBaseUrl` uses the enter hook); the implementer greps `activeSection.value =` and every `Ref` read in a `nextTick` |
| Scoped styles do not reach the wrapper | The wrapper is in `SettingsPopover`'s own template, so `<style scoped>` applies |
| A child combinator on `.settings-sections` | Grep before the change (§4.1) |
