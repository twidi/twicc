# Home motion — design (visual refresh, step 7b)

## 1. Context

Step 7 of the "Signature" visual refresh animates the secondary screens, split into 7a–7f
(roadmap §6m). 7a (project stats) is done (`docs/plans/2026-09-30-stats-motion-design.md`,
roadmap §6n). **7b (this document)** is the **home** page: the cards arrive in a cascade,
the activity sparklines reveal themselves, and a hovered card lifts and glows in its own
colour. Read first: `docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding:
**Firefox parity**, **reduced motion is reduced, not none**), §5 (process), §6m, §6n
(7a lessons), §8.11 and §9 "Step 7".

What the user sees today (http://localhost:5174, route `/`, `views/HomeView.vue`):

- a header: animated logo, "Welcome to TwiCC", the global activity sparkline
  (`ActivitySparkline`, `span#home-global-sparkline.global-sparkline`), two buttons;
- **Workspaces** (`components/workspace/WorkspaceList.vue`): one `WorkspaceCard` per
  workspace, in `div.workspace-cards`;
- **Projects** (`components/project/ProjectList.vue`): "Named projects" as `ProjectCard`s
  in `div.project-cards`, then "Other projects" as a recursive tree
  (`ProjectTreeNode.vue`): a folder header or a `ProjectCard`, children below when open;
- each card (`wa-card appearance="outlined"`) ends with its activity sparkline
  (`ActivitySparkline`, weekly message turns); on hover it lifts
  (`transform: translateY(-2px)`) and takes the level-2 shadow (`box-shadow: var(--depth-2)`,
  step 2); a disabled workspace card (no visible project) does not react.

`HomeView` is a plain route component (`router.js` `{ path: '/', component: HomeView }`,
`App.vue` `<router-view />` without `KeepAlive`): it mounts each time the home shows, and
its cards mount once the projects list has loaded (`v-else-if="isLoading"` spinner before).

The same `ActivitySparkline` also shows in the project page header
(`ProjectDetailHeader.vue`, `span.detail-sparkline`); 7a left its reveal to this step.

Mock reference (`mock.css` / `app.js`, roadmap §3):

```css
@keyframes fade-up { from { opacity: 0; transform: translateY(.375rem); } }
.mock.fx-enter .stagger > * { animation: fade-up 420ms var(--ease-out) both; animation-delay: calc(var(--i, 0) * 60ms); }
.mock.fx-micro .wcard:hover { transform: translateY(-.125rem); }
.mock.fx-depth.fx-micro .wcard:hover { box-shadow: var(--sh-2); }
.mock.fx-glow .wcard:hover { border-color: color-mix(in oklab, var(--wc, var(--b-60)) 55%, transparent);
    box-shadow: var(--sh-2, none), 0 8px 28px -12px color-mix(in oklab, var(--wc, var(--b-60)) 70%, transparent); }
```

`--wc` is the workspace colour on a workspace card and the project colour on a project
card.

## 2. User decisions (2026-09-30)

- **Order:** one cascade, top to bottom in page order (workspaces, then named projects,
  then the tree). Only the cards on screen cascade; the others are already in place when
  the user scrolls to them.
- **Cards that appear later** ("Show archived", a folder opened in the tree, a new
  project, a card created by a data update): they enter with the same animation,
  cascading among themselves when several arrive together.
- **Glow colour:** the workspace colour or the project's dot colour (with its worktree →
  main-repo fallback); a card without a colour glows in the accent.
- **Sparklines:** the same left-to-right reveal as the 7a curves, shorter (800ms), at the
  same time as their card's entrance. Also the home header sparkline and the project page
  header sparkline.

## 3. Card entrance

### 3.1 Trigger and batching

A new composable `composables/useHomeCardCascade.js`:

- `provideHomeCardCascade()` — called once in `HomeView.vue` `<script setup>`; provides a
  coordinator (Vue `provide`, an exported `Symbol` key).
- `useHomeCardEntrance(elRef)` — called in `WorkspaceCard.vue` and `ProjectCard.vue` with
  a **new** template ref on their root `wa-card` (`ref="cardRef"`, `const cardRef =
  ref(null)`; neither card has a template ref today). It reads the coordinator with
  `inject(KEY, null)` (the `null` default avoids Vue's missing-injection warning); without
  a coordinator it does nothing, so a card rendered outside the home has no entrance.

On `onMounted` the card calls `coordinator.enter(el)`. The coordinator collects the
elements of one render in a batch and flushes it once, in a `nextTick` callback queued by
the first `enter` of the batch (all the `onMounted` hooks of one Vue flush run before it,
and it runs before the browser paints). Flush:

1. drop elements no longer connected (`!el.isConnected`);
2. sort by document order (`a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING`
   → `a` first);
3. read each element's `getBoundingClientRect()` and the view rect
   `{ top: 0, bottom: window.innerHeight }` (the home scrolls the page, not an inner box);
4. `planHomeCardCascade(rects, viewRect)` (§3.3) → an index or `null` per element;
5. for each indexed element: `el.style.setProperty('--home-card-index', index)` and
   `el.classList.add('home-card-entering')`; a timer removes the class after
   `homeCardEndMs(index)` (§3.3). Elements with `null` get nothing.

Timers are cleared when the coordinator's owner (`HomeView`) unmounts
(`onBeforeUnmount`), and a card that unmounts before its timer fires is simply dropped
(the timer then touches a detached element: harmless). The first load and every later
batch (archived toggle, tree folder, new project) go through the same path, which gives
the §2 behaviour for both.

The class is removed after the entrance because Vue moves a card's DOM node when the list
reorders (named projects are sorted by last activity, so a working project jumps to the
top): a moved node restarts its CSS animations, and a card that keeps the class would
replay its entrance (and its sparkline reveal, §4) on every move. `--home-card-index`
stays set: it is inert without the class.

The coordinator writes `el.classList` / `el.style` directly, beside Vue's own bindings on
the same element:

- the cards' `:style` binding (§5) is always an object, so Vue patches only its own
  property and never drops the `style` attribute (a binding going to `null` would call
  `removeAttribute('style')` and wipe `--home-card-index`);
- a Vue `:class` patch rewrites `className`: if `WorkspaceCard`'s `disabled` class flips
  during the ~1s entrance, the entrance is cut (the card shows at once). Accepted: it takes
  a workspace losing or gaining its last visible project within that second.

Only cards animate. The section headers, the "Named projects" / "Other projects"
subheaders, the tree's folder headers and the hint callouts appear at once, also inside a
tree folder the user just opened.

Known replays that are accepted: the "Other projects" tree compresses folder paths and
keys its nodes by segment (`ProjectList.vue`, `ProjectTreeNode.vue`). A new unnamed
project or an archived toggle can change a compressed segment; the subtree then remounts
and its cards enter again (and its folders reset to open, as today). A WebSocket
reconnect reloads the projects list (`useReconciliation.js` → `store.loadProjects()`,
which sets the list's loading state): the home shows its spinner, every card remounts and
the full cascade replays.

### 3.2 CSS (`styles/motion.css`, pinned whole)

Global, next to the sidebar list cascade (step 5c) whose shape it follows:

```css
/* Home card cascade (step 7b). The class and --home-card-index are set by
   useHomeCardCascade on the card; the timings match utils/homeCardCascade.js.
   Movement × --motion-amount: reduced motion keeps the fade only. */
@keyframes home-card-in {
    from {
        opacity: 0;
        translate: 0 calc(0.375rem * var(--motion-amount));
    }
}
.home-card-entering {
    animation: home-card-in 420ms var(--motion-ease-out) backwards;
    animation-delay: calc(var(--home-card-index, 0) * 60ms);
}
```

The easing and shape follow the 5c list cascade (`list-enter`): under reduced motion the
entrance is a pure fade with the same `--motion-ease-out`, as 5c's. The hover lift (§5)
also sets `translate`: during the entrance the animation owns it, and §5 removes the
`translate` transition while the card enters so the two do not fight.

### 3.3 Pure helpers (`utils/homeCardCascade.js`, tested with `node:test`)

```js
export const HOME_CARD_STAGGER_MS = 60
export const HOME_CARD_DURATION_MS = 420
export const HOME_SPARKLINE_REVEAL_MS = 800
export const HOME_CARD_MAX_INDEX = 10
```

- `planHomeCardCascade(rects, viewRect, maxIndex = HOME_CARD_MAX_INDEX)`: `rects` in
  document order (`top` / `bottom`). Uses `visibleIndexRange(rects, viewRect)` from
  `utils/listCascade.js` (reused, not copied); returns an array of the same length:
  `min(i - start, maxIndex)` for `start <= i < end`, `null` elsewhere. An empty or fully
  off-screen batch gives all `null`.
- `homeCardEndMs(index)`: `index * HOME_CARD_STAGGER_MS + max(HOME_CARD_DURATION_MS,
  HOME_SPARKLINE_REVEAL_MS) + 100` — the class stays until the later of the two animations
  (card, sparkline) has ended, with the same 100ms margin as `listCascadeEndMs`.

## 4. Sparkline reveal

### 4.1 Rules (`ActivitySparkline.vue`, scoped, pinned whole)

The existing `.activity-sparkline { display: block; }` rule stays. Added:

```css
.home-card-entering .activity-sparkline,
.activity-sparkline.activity-sparkline--reveal {
    animation: activity-sparkline-reveal 800ms var(--motion-ease-out) backwards;
    animation-delay: calc(var(--home-card-index, 0) * 60ms);
}
@keyframes activity-sparkline-reveal {
    from { clip-path: inset(0 100% 0 0); }
    to { clip-path: inset(0 0 0 0); }
}
@media (prefers-reduced-motion: reduce) {
    .home-card-entering .activity-sparkline,
    .activity-sparkline.activity-sparkline--reveal {
        animation: activity-sparkline-fade 300ms ease-in-out backwards;
        animation-delay: calc(var(--home-card-index, 0) * 60ms);
    }
}
@keyframes activity-sparkline-fade { from { opacity: 0; } }
```

- `.home-card-entering` is set on an ancestor (the card) outside this component. A plain
  descendant selector is right: Vue scopes only the last compound, so
  `.home-card-entering .activity-sparkline` compiles to
  `.home-card-entering .activity-sparkline[data-v-…]` (checked with `@vue/compiler-sfc`
  3.5.27 `compileStyle`). Do **not** wrap it in `:global()`: `:global(.a) .b` compiles to
  `.a` alone and would animate the card. The keyframes must stay in this component (scoped
  keyframes are renamed per component, so a card-side rule could not name them).
- Both keyframes of the reveal are written (`inset()` does not interpolate with `none`,
  7a lesson). The SVG box already clips its overflow; there is no halo here, so the insets
  are `0`.
- The reduced-motion block comes **after** the base rule (same specificity).

### 4.2 Where each sparkline reveals

| Sparkline | Reveal | Delay |
|---|---|---|
| Workspace / project card | while its card has `home-card-entering` | the card's `--home-card-index` (inherited) |
| Home header (`HomeView.vue`) | on mount: `<ActivitySparkline reveal …>` | none (`--home-card-index` unset) |
| Project page header (`ProjectDetailHeader.vue`) | `<ActivitySparkline reveal …>`: on mount and each time the project page shows again (the panel is `v-show`n and `KeepAlive`d, 7a §3) | none |

A new Boolean prop `reveal` (default `false`) on `ActivitySparkline` adds the
`activity-sparkline--reveal` class to its `svg`. A card's sparkline does not use the prop:
it reveals only through its card's entrance, so a card that does not cascade (off screen)
shows its sparkline at once.

On a window 900px tall or less, `ProjectDetailHeader.vue` turns the row holding its
sparkline (`.detail-collapsible-rows`) into an overlay hidden at rest (`opacity: 0;
visibility: hidden`, shown when the header is expanded): the reveal plays unseen and the
expanded row shows the finished curve. Accepted (visibility changes do not restart CSS
animations; replaying on expand would need a remount).

An `ActivitySparkline` renders its `svg` only when it has data (`v-if="data.length"`): a
header sparkline whose data arrives after mount reveals when it arrives (the `svg` mounts
then) — accepted, it is the moment it appears.

## 5. Hover: lift and glow

`WorkspaceCard.vue` and `ProjectCard.vue` scoped styles. The existing card and hover rules
become (pinned whole; `ProjectCard.vue` has the same rules with `.project-card` and no
`disabled` rule):

```css
.workspace-card {
    cursor: pointer;
    transition:
        translate var(--motion-dur-2) var(--motion-ease-spring),
        box-shadow var(--motion-dur-2) var(--motion-ease),
        border-color var(--motion-dur-2) var(--motion-ease);
    &::part(body) {
        position: relative;
    }
}

.workspace-card.home-card-entering {
    transition:
        box-shadow var(--motion-dur-2) var(--motion-ease),
        border-color var(--motion-dur-2) var(--motion-ease);
}

@media (hover: hover) {
    .workspace-card:hover {
        translate: 0 calc(-0.125rem * var(--motion-amount));
        border-color: color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 55%, transparent);
        box-shadow: var(--depth-2), 0 0.5rem 1.75rem -0.75rem color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 70%, transparent);
    }

    .workspace-card.disabled:hover {
        translate: none;
        border-color: var(--wa-color-surface-border);
        box-shadow: var(--wa-shadow-s);
    }
}
```

- `.workspace-card.home-card-entering` (0,3,0 once scoped, beats the card rule) drops
  `translate` from the transition list while the card enters. Without it, a hover during
  the entrance's active phase starts a `translate` transition from the base value, which
  sits above the animation in the cascade: the card snaps (≈5px, probed in Firefox 156).
  With it, only a small step remains (at most 0.125rem × the entrance progress, the
  animation's implicit end keyframe following the hovered value): accepted. Between the
  card animation's end (`index × 60 + 420`ms) and the class removal (`index × 60 + 900`ms,
  kept for the sparkline), no animation owns `translate` and its transition is still off:
  a hover there lifts by an instant 0.125rem step, without the spring — accepted. The
  class goes away with the timer (§3.1); the spring lift is back from then on.
- The disabled hover keeps the resting shadow (`--wa-shadow-s`, `wa-card`'s own), so a
  disabled card really does not react.
- These rules follow the step 4a motion invariants (`styles/motion.css` header, enforced
  by `motion.test.js` test 8): individual `translate`, never `transform`; distances ×
  `--motion-amount`; the spring only for movement, `--motion-ease` for colours and
  shadows; hover effects only under `@media (hover: hover)` (on touch, a tap on the card's
  ⋮ menu keeps the page on home and would leave a hover state — and now a coloured
  glow — stuck). The implementer adds both cards to test 8's `changed` list (selector
  pick: `.workspace-card` / `.project-card`).
- `-0.125rem` is the current `-2px` in rem (sizes follow the font-size setting), times
  `--motion-amount`: under reduced motion the card no longer moves; the glow and shadow
  (colour changes) stay. Touch devices lose the hover lift and the existing hover shadow
  (they only showed as a stuck state after a tap).
- The light-DOM `border-color` wins over `wa-card`'s `:host([appearance='outlined'])`
  `border-color` (outer styles beat `:host` rules).
- Colour: the card root binds `:style="{ '--card-glow-color': <colour> || null }"` — always
  an object (§3.1); a `null` value removes only that property, and the CSS fallback gives
  the accent:
  - `WorkspaceCard`: `workspace.color`;
  - `ProjectCard`: `dotColor` from `useProjectMark(computed(() => props.project.id))`
    (`useProjectMark` reads its argument with `unref`, so it takes a ref, not a getter; own colour,
    then the worktree's main-repo colour).
- The `.workspace-card.disabled` rule (opacity 0.5, `not-allowed`) is unchanged.

## 6. Reduced motion

| Effect | Normal | Reduced motion |
|---|---|---|
| Card entrance | 420ms fade + 0.375rem rise, 60ms stagger | fade only, same stagger (`--motion-amount: 0`) |
| Sparkline reveal | 800ms wipe | 300ms fade, same delay |
| Hover lift | 0.125rem up | none |
| Hover glow, border, shadow | `--motion-dur-2` (200ms) colour transition | kept |

## 7. Tests

`node:test`, pinning each new or changed rule whole (roadmap §6l.2), helpers copied from
`styles/stats-motion.test.js` as that file does:

- `utils/homeCardCascade.test.js`:
  - `planHomeCardCascade`: three cards on screen → `[0, 1, 2]`; first card above the view,
    next two inside, last below → `[null, 0, 1, null]`; 14 cards all inside →
    `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10]`; none inside → all `null`;
    `[]` → `[]`;
  - `homeCardEndMs(0) === 900`, `homeCardEndMs(3) === 1080`;
  - the four constants.
- `styles/home-motion.test.js`:
  - `styles/motion.css`: the `home-card-in` keyframes and the `.home-card-entering` rule;
  - `ActivitySparkline.vue`: the reveal rule, both reveal keyframes, the reduced-motion
    block (after the base rule) and `activity-sparkline-fade`; the `reveal` prop and the
    `activity-sparkline--reveal` class binding; **and a compile check**: run
    `@vue/compiler-sfc` `compileStyle` (scoped, any id) on the style block and assert the
    compiled selector lists of both reveal rules contain
    `.home-card-entering .activity-sparkline[data-v-<id>]` (a source pin cannot catch a
    selector Vue rewrites, see §4.1);
  - `WorkspaceCard.vue` / `ProjectCard.vue`: the card rule, the `.home-card-entering`
    transition rule and the `@media (hover: hover)` block with its two / one rules (§5); `ref="cardRef"` on the root `wa-card`; the
    `--card-glow-color` style binding (object form); `useHomeCardEntrance(cardRef)`;
  - `HomeView.vue`: `provideHomeCardCascade()` called; the header sparkline has `reveal`;
  - `ProjectDetailHeader.vue`: its sparkline has `reveal`.
- `styles/motion.test.js` (existing): both cards added to test 8's `changed` list
  (pickers `.workspace-card` / `.project-card`).
- The coordinator itself (batching, document order, timers) is covered by the browser
  review, as 7a's `AnimatedNumber` (no component harness in the repo). Its pure planning
  is the tested helper.

## 8. Browser review (user, http://localhost:5174)

Trigger conditions: home page with several workspaces and projects; light and dark;
the system "reduce motion" setting on and off; a narrow window.

1. Open the home (from a project, or reload) → the visible cards fade up one after the
   other, top to bottom (workspaces, then projects); their sparklines reveal left to right
   with them; the header sparkline reveals.
2. Scroll down → the cards below are already in place, sparklines drawn.
3. Toggle "Show archived" (projects or workspaces) → the new cards enter in a cascade.
   Close then open a folder of the "Other projects" tree → its cards enter.
4. Hover a workspace with a colour → lifts, border and glow in that colour; a project with
   a dot colour → same in its colour; a card without colour → accent glow; a disabled
   workspace card → nothing. Move the pointer across the cards while they cascade in →
   no visible jump (at most a small step).
5. With an agent working on a named project, the project moves to the top of the list:
   no entrance replays on the moved card (after the first entrance has ended).
6. Window taller than 900px: open a project page → the header sparkline reveals; open a
   session and come back → it reveals again. (At 900px or less the header's sparkline row
   is a hidden overlay until expanded: the reveal runs unseen and the expanded row shows
   the finished curve — accepted, §4.2.)
7. Reduced motion → cards fade in without rising; sparklines fade; hover does not lift,
   the glow stays.

## 9. Files

- New: `composables/useHomeCardCascade.js`, `utils/homeCardCascade.js`,
  `utils/homeCardCascade.test.js`, `styles/home-motion.test.js`.
- Changed: `styles/motion.css`, `styles/motion.test.js`, `views/HomeView.vue`,
  `components/activity/ActivitySparkline.vue`, `components/workspace/WorkspaceCard.vue`,
  `components/project/ProjectCard.vue`, `components/project/ProjectDetailHeader.vue`.
