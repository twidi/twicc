# Visual refresh ("Signature") — roadmap, decisions and handoff

Living reference for the whole redesign. Read this first before working on any step: it
records what was explored, what the user decided (and rejected), how we work, what step 1
did and learned, and what steps 2–7 are meant to contain. Per-step specs and plans are
written separately (`docs/plans/<date>-<topic>-design.md` / `-plan.md`).

## 1. Status

| Step | Topic | Status |
|---|---|---|
| 1 | Canvas + floating panels | **Done** — commits `ddc43644`, `8dbdfd83` on branch `enhanced-ui` |
| 2 | Depth (layered shadows) + typography | **Done** — commits `85ce211f`, `ad7942b1`, `e2046d92` on branch `enhanced-ui` (see §6b) |
| 3 | Accent-tinted glass overlays | **Done** — commit `709f9cf9` on branch `enhanced-ui` (see §6c) |
| 4 | Motion tokens + micro-interactions | **4a done** — commit `46b4926c` (§6d); **4b done** — commit `d11ea446` (§6e); 4c (gliding indicators) to specify |
| 5 | Entrances (virtual-scroll aware) + skeletons | To specify |
| 6 | Accent glow + live states | To specify |
| 7 | Secondary screens | To specify |

**No merge into `main` and no pull request until the whole redesign (steps 1–7) is done.**
We keep iterating on branch `enhanced-ui`.

## 2. Origin

The user found the UI "sad": flat surfaces, almost no animation, nothing that makes you go
"oh, that's nice". Goal: make it more appealing **without overdoing it**, acting on shared
components and tokens (there are too many screens to treat one by one).

Constraints given at the start:
- light and dark schemes, and a user-chosen accent color, must both keep working;
- the user **may later remove** the theme choice and the accent-color choice (keep
  everything derived from tokens so either outcome works);
- sizes are `rem`-based and follow the font-size setting.

Findings on the existing UI (2026-09-26):
- only 2 Vue `<Transition>` in ~240 components; transitions are Web Awesome defaults
  (`150ms ease`, buttons only);
- surfaces are nearly all the same color; shadows are a hard 2px offset;
- a session shows a blank white screen for seconds while loading.

## 3. The interactive mock (reference)

Location (outside the repo, in the TwiCC artifacts of session
`2992a814-2ce5-4622-a145-219d5e2fd203`):
`~/.twicc/artifacts/2992a814-2ce5-4622-a145-219d5e2fd203/ui-directions/`
(`index.html` + `mock.css` + `app.js`; the user's saved pick in `data/pick.json`).
Open it from TwiCC's Artifacts tab of that session, or serve the folder locally
(`python3 -m http.server`) to test in Chrome.

It mocks the session screen, project stats, home, settings popover, command palette,
question widget, tasks tab, dialog, menu and toasts. A control bar switches scheme, accent,
font size (14/16/18), direction and each **ingredient** on/off, and has "Try" actions
(simulate a turn, question, reload/skeleton, palette, settings, dialog, toast, reduced
motion).

Directions: `Current`, `A · Polish` (motion, micro, enter, type), `B · Depth` (A + depth,
glow, live), `C · Signature` (B + floating panels, glass, ambient). **The user chose
C · Signature.**

Each ingredient is one block in `mock.css`, scoped under `.mock.fx-<name>`:
`fx-motion`, `fx-micro`, `fx-enter`, `fx-depth`, `fx-glow`, `fx-live`, `fx-type`,
`fx-float`, `fx-glass`, `fx-ambient`. **Use those blocks as the visual reference for the
matching step** (values are copied in §9 below in case the artifact is lost).

## 4. User decisions and feedback (binding)

From the mock (round 1 pick, round 2 review) and from step 1 reviews:

- **Selected session:** no accent bar on its left. Keep a distinct, **more evident** fill
  (stronger lit fill, thin accent ring, soft accent shadow, bolder title).
- **Toasts:** top center (where Notivue shows them), dropping in from the top.
- **Glass overlays:** transparent + blur is liked, but **tinted with the accent** (like the
  canvas), never a neutral grey. Applies to dialogs, menus, toasts, palette, settings.
  Toast glass over the flat header was checked and accepted as is.
- **Right dock:** right-top and right-bottom are **two separate cards**.
- **Working badge:** no expanding halo/ripple around the hopping icon; the icon animation is
  enough.
- **Dark canvas:** the accent must be visible across the whole canvas, not only in one
  corner — tuned by the user in several rounds (see §6.3). **The light canvas was right from
  the start and must not change.**
- **Bottom-right aura color:** the accent hue rotated by +70° (a neighbouring hue) — kept.
- **Mobile drawer:** paints the full canvas (auras included), still opaque.
- **Touch resize grips:** the old ×3-scaled `grip-lines-vertical` icons were heavy and the
  sidebar one misplaced; replaced by a small pill centered in the gap — approved on mobile.
- **Floating panels:** approved; done as step 1.
- The whole "Signature" direction is liked; details beyond these points were accepted as
  shown in the mock.
- **Firefox parity (2026-09-27, applies to steps 4–7):** the user, TwiCC's main user, uses
  only Firefox. Firefox must never get a degraded result. Use modern standard CSS where the
  browser supports it; where Firefox lacks a feature, ship a JavaScript fallback with the
  same visible result. Gate on feature detection (`CSS.supports()` / `@supports`), never on
  the user agent, so the CSS path takes over by itself when Firefox ships the feature.
  Browser checks are done in Firefox first. Measured in Firefox 156 (headless probe): it
  has `@starting-style` entries, `linear()`, `@property`, anchor positioning, same-document
  view transitions + `view-transition-class`, `sibling-index()`; it lacks exit transitions
  to `display: none` (`transition-behavior: allow-discrete` snaps), `overlay`,
  `interpolate-size` / `calc-size()`, scroll-driven animations and `if()`.
- **Reduced motion is reduced, not none (2026-09-27, applies to steps 4–7):** under
  `prefers-reduced-motion: reduce`, remove movement (translations, scales, rotations,
  spring overshoot, gliding, height animations → final state at once); keep short
  color / background / opacity fades and status indicators (opacity pulses, spinners);
  a status indicator whose animation is movement (the working robot's hop, the workflow
  pending hop) switches to an opacity pulse. No global "kill every animation" rule. Step 4a's `--motion-amount` token
  (`1`, `0` under reduced motion) multiplies every movement distance.

## 5. How we work (process rules)

- Spec per step, with an **independent adversarial review loop** (fresh subagent, verifies
  every claim against the code) until it passes; then an implementation plan with the same
  review loop; then execution (the user chose **native** execution: the main session
  implements, one fresh whole-branch reviewer at the end).
- Everything is checked in the **worktree's own dev instance** (http://localhost:5174,
  backend 3501; started with `uv run ./devctl.py start` from the worktree). The user
  reviews each commit in the browser (also on mobile) **before** saying "commit".
- Commit only on the user's explicit "commit". Conventional commits, descriptive body,
  `Co-Authored-By` with the running model's name. No CHANGELOG entry unless asked (propose
  one at the end of the redesign).
- On the main instance (http://localhost:5173) the user allowed changing the sidebar width
  (saved locally only). Do not change other state there.
- Explain choices **in UI terms** (what the user sees and where), never by class names
  alone — the user does not know every class.
- Never claim a pre-existing behaviour without proving it on 5173 (the user uses TwiCC all
  day and will notice).

## 6. Step 1 — canvas + floating panels (done)

Spec: `docs/plans/2026-09-26-floating-panels-design.md`.
Plan: `docs/plans/2026-09-26-floating-panels-plan.md`.
Reviews: spec 7 rounds (PASS), plan 2 rounds (PASS), final whole-branch review (0
critical/important in code; all minors fixed).

### 6.1 What it does

- `frontend/src/styles/surfaces.css` (imported in `main.js` after `transcript-tokens.css`,
  SPA only) holds every token and the shared classes:
  - canvas: `--canvas-color`, `--canvas-aura-start/-end`, `--canvas-aura-start-size`,
    `--canvas-aura-end-size`, `--canvas-aura-end-at`, `--canvas-aura-fade`,
    `--canvas-background` (auras + color, declared once on `:root`);
  - panels: `--panel-gap` (0.5rem; 0.25rem under 640px), `--panel-half-gap`,
    `--panel-radius` (`--wa-border-radius-l`; `-m` under 640px), `--panel-inner-radius`,
    `--panel-corner-inset`, `--panel-border`, `--panel-shadow`;
  - `.panel-card` (every card carries it — it is also the marker pooled iframes look for);
  - `.panel-grip` (touch pill, `.horizontal` variant);
  - `html` background + `body::before` fixed aura layer.
- Sidebar on the canvas; the sidebar divider **is** the gap (accent line on hover/drag via
  `useSplitDividerDragFlag`'s `dragging`, which now filters on the panel's own divider).
- Session: header on the canvas (its divider/compact border made invisible, space kept);
  every layout region is a card (center, docks, overlay) inset by half a gap on its inner
  edges via `utils/panelInsets.js` (`innerEdges`, `insetRectStyle`, computed once per render
  in `SessionLayout`); rails transparent; overlay backdrop rounded; top bars inset so corner
  controls are not clipped. The pure resolver (`utils/layoutResolver.js`) is untouched.
- Project detail, artifacts browser, session fallback views (ephemeral, not found, loading):
  one card each.
- Pooled iframes (Browser pane, HTML previews) get rounded corners where they sit in a
  card's corner (`PersistentFrame` resolves the containing `.panel-card`; `FrameHost` builds
  the clip-path with `frameFlushCorners` / `frameClipPath`).
- Clips: `.main-content`, `.session-view` use `overflow: clip` + `overflow-clip-margin` =
  gap; `.frame-host` clips; **shadow budget**: panel shadow horizontal reach ≤ 4px.

### 6.2 Lessons (do not regress)

- **The content area (`.main-content`) must stay opaque.** It is stacked above the sidebar
  and is what hides anything spilling out of it (the sidebar is only a grid column; nothing
  clips it). Making it transparent showed the collapsed sidebar's header buttons over the
  session title, and the peer button pushed out when the project selector widens. It is now
  painted with `--canvas-background` + `background-attachment: fixed` (pixel-identical to
  the canvas behind); the divider part is painted the same way. The project-selector
  trigger (`z-index: 11`) still shows over it — that overflow on hover/focus/open is
  **intended** (to read the full name and the menu).
- Collapsed sidebar: its header rows are hidden (`visibility: hidden` under
  `@container sidebar (width <= 50px)`); the footer/reopen toggle keep their own rules.
- Never add `transform`, `filter`, `backdrop-filter`, `contain`, `will-change` or
  `container-type` to `.main-content`'s branches, `.session-layout`, `.center-slot`,
  `.dock-region` or `.layout-overlay`: fullscreen file previews are `position: fixed` in
  place and would be trapped. (Step 3's glass must respect this: blur only on overlays that
  are not ancestors of panes.)
- Never move an iframe in the DOM (see CLAUDE.md "Persistent frames").

### 6.3 Final canvas values

Light (unchanged since the first proposal — do not touch):
`--canvas-color: color-mix(in oklab, brand-95 30%, neutral-95)`; auras `brand-90` at 60% /
`h+70` at 0.45; sizes `55rem 38rem` / `50rem 36rem`; end aura at `105% 110%`; fade 65%.

Dark (tuned by the user: "¾ between A and B", then bottom-right aura fixed):
`--canvas-color: color-mix(in oklab, color-mix(in oklab, surface-default, black 26%) 70%, brand-20)`;
auras `brand-50` at 49% / `h+70` at 0.55; sizes `96rem 70rem` / `88rem 65rem`; end aura at
`100% 100%` (centered on the corner; outside it, it fell off-screen); fade 80%.
History: A = 15% tint, 45%/0.34, 55×38/50×36rem, fade 65%; B = 35%, 50%/0.4,
110×80/100×75rem, 85%.

### 6.4 Accepted limitations

- Safari has no `overflow-clip-margin`: shadows of cards touching the layout's outer edges
  are clipped there.
- Tab-drag drop zones use raw resolver rects (off by up to half a gap).
- The tab track line stops at the corner inset inside a card.
- Pooled frames with a pane padding under 6px get a rounded corner too.
- FilesPanel / GitPanel internal splitters keep their old grip icons (inside cards, out of
  scope).

### 6.5 Not verified in a browser (to check when convenient)

Fullscreen preview from a dock; KeepAlive return with a docked Browser (corners); Firefox
sideways shift when reopening the Artifacts overlay (commit `63d0d7c5` case); Browser
responsive stage larger than its pane (no page scrollbar); side overlay opened while a rail
shows. Code-read OK by the final reviewer.

## 6b. Step 2 — depth + typography (done)

Spec: `docs/plans/2026-09-26-depth-design.md` (§14 = the user's review amendments and the
final-review follow-ups — binding). Plan: `docs/plans/2026-09-26-depth-plan.md`.
Reviews: spec 7 rounds (PASS), plan 7 rounds (PASS), final whole-branch review (0
critical, 2 important + 2 minor, all fixed in `e2046d92`), re-review Ready (one doc minor,
fixed in the spec).
Commits: `85ce211f` (shadows + review amendments), `ad7942b1` (typography), `e2046d92`
(final-review fixes). Tests: 455 (`frontend/src/styles/depth.test.js` pins the CSS
invariants).

### 6b.1 What it does

- `frontend/src/styles/depth.css` (imported by the SPA, the share viewer and the artifact
  shell): `--depth-1/2/3` (resting / standing out / floating), `--depth-card` (downward-only,
  chat and tool cards) + `--depth-card-reach`, `--depth-button`, `--depth-highlight`,
  `--depth-edge` (dark top line), `--depth-inset` (recessed fields); Web Awesome's
  `--wa-shadow-s/m/l` mapped to levels 1/3/3 on `:root, .wa-invert`; global `:where()`
  rules for raised buttons (outlined/filled + solid accent, with exclusions), recessed
  fields, split buttons without WA's see-through gap.
- Chat: soft downward shadows (last row of an assistant card, last card of a joined tool
  run), accent-tinted user cards (quotes inside them start on the plain surface), white
  assistant cards in light, dark top edge.
- Floating layers at level 3 (menus, selects, dialogs, popovers, toasts, pickers); the
  layout overlay and the headers' overflow panels keep the step-1 ≤ 4px side budget
  (`--panel-overlay-shadow` in `surfaces.css`).
- Sidebar chrome: its own buttons brand outlined; project selector and filter inputs painted
  with the canvas (mobile drawer: `--canvas-drawer-top` approximation); accent-based
  separators (`--sidebar-divider-color`); toggle opaque only while it floats over content.
  Home: Inbox/Settings solid accent.
- Typography: tabular digits everywhere except `.markdown-body`; titles 650 / tighter;
  uppercase sidebar section labels; `text-wrap: pretty` in message paragraphs.

### 6b.2 Lessons (do not regress)

- **Shadow layer order matters for transitions**: a `box-shadow` transition pairs layers by
  position and snaps on an inset/outer pair — dark tokens put their inset layer first.
- **`:where()` protects against component rules, not against layered theme rules**: an
  unlayered global rule always beats `@layer wa-theme-*`; the awesome theme's hard controls
  are kept by excluding `.wa-theme-awesome`.
- **Whitespace is a combinator**: each `wa-button:is(…):not(…)` compound stays on one line.
- **`background-attachment: fixed` is `scroll` under a transform** (the mobile drawer) and
  in iOS Safari: canvas-painted elements need an approximation there.
- **An outlined (transparent) button that floats over content needs an opaque fill** in its
  floating states (sidebar toggle; home Inbox/Settings are solid).
- KeepAlive keeps hidden chat lists of other sessions in the DOM: browser probes must scope
  to the visible `.session-items`. A backgrounded MCP tab freezes transitions (read target
  values with the transition disabled).

### 6b.3 Not verified in a browser

Layout overlay shadow, question-widget options, chart tooltips (exact token swaps, code-read
by the reviewers); the pinned inbox badge ring (count 0 on the worktree instance); the dark
hover *fade* (frozen in the background MCP tab; interpolation pinned by the node test). The
public share viewer could not be opened on the worktree (checked through its built CSS).

### 6b.4 Left for the global fine-tuning pass (user, 2026-09-27)

The user plans a pass over every detail once all steps are done. Open items noted so far:
neutral `filled-outlined` buttons left in the Git/Files panes; the tinted-block style
(quotes, `:::` blocks) may be restyled later; the mock's message radius (`0.875rem`) was
not taken.

## 6c. Step 3 — accent-tinted glass overlays (done)

Spec: `docs/plans/2026-09-27-glass-design.md` (no separate plan: the spec carries the exact
CSS and every component change). Reviews: spec 17 rounds (from round 8 on, two parallel
reviewers split by zone: CSS mechanics and tests / scope and components) until PASS;
implementation by a subagent, code review by another (PASS); user fixes after that.
Commit: `709f9cf9`. Tests: 468 (`frontend/src/styles/glass.test.js`,
`frontend/src/utils/glassArrowGap.test.js`).

### 6c.1 What it does

- `frontend/src/styles/glass.css` (SPA, share viewer, artifact shell): `--glass-*` tokens
  (tint from palette steps, 74 % bg, accent border, top edge, blur, veil, tooltip, item
  highlights, field bg, row tokens, edge gap); glass on `wa-dialog`, `wa-dropdown` menu and
  submenu, `wa-select` list box, `wa-popover` body, `wa-tooltip`, Notivue toasts, and our
  panels via `.glass-surface` / `.glass-sticky` / `.glass-veil`; lighter blurred modal
  veil; accent-tinted highlighted rows; fields at 70 %; `wa-details` tinted inside glass.
- Toasts are no longer inverted (glass like the menus); Notivue's list `clip-path` removed
  (it stopped the blur).
- Popover arrows merge with the body: `frontend/src/utils/glassArrowGap.js` reads the
  arrow's position on each `wa-reposition` and the border overlay is masked under its base.
- Settings popover, mobile: the sticky back row covers the panel padding edge to edge.

### 6c.2 Lessons (do not regress)

- **Never put `backdrop-filter` on an element with children**: it becomes the containing
  block of `position: fixed` descendants (Web Awesome's tooltip hover bridge, the submenu
  safe triangle). The blur lives on a childless pseudo-element layer (or an arrow part).
- **An ancestor with `opacity < 1`, `filter`, `mask`, `clip-path`… stops the blur** of the
  glass inside it (Notivue's list clip; entrance fades show a short unblurred moment).
- **A border drawn on a `z-index: -1` layer disappears under content**, and Chromium paints
  an element's outline before its positioned descendants: the glass border is a
  `z-index: 100` overlay.
- **Custom properties fed into Web Awesome's `calc()` need units** (`0px`, never `0`).
- **Selector lists stay homogeneous**: one unsupported selector (a pseudo-element after
  `::part()`) drops the whole rule.
- **The production minifier writes `::before` as `:before`** but leaves `@supports selector()`
  conditions as written: test both spellings.
- **Theme widths vary**: `--wa-border-width-s` is 2px in the awesome theme.
- **Firefox antialiases a diagonal clip to a half-covered row**: the arrow clip overlaps the
  body by 1px.
- Headless Chrome with CDP `Emulation.setEmulatedMedia` checks `prefers-reduced-transparency`;
  headless Firefox driven by Selenium (`/snap/bin/geckodriver`) checks Firefox rendering
  (it never applies `backdrop-filter`).

### 6c.3 Not verified in a browser

- **Safari** (none available): whether WebKit renders a pseudo-element after `::part()` is
  unknown; the `@supports` fallback (opaque tinted surfaces, never none) is the guarantee.
- **Share viewer visual** (the worktree cannot serve shares): built CSS checked; to look at
  on the main instance after the merge (popover menu, its select, a tooltip, the sub-agent
  drawer veil).
- Arrow joint on other device pixel ratios (the user's Android phone).

### 6c.4 Left for the global fine-tuning pass

Accepted trade-offs routed to the user: the first keyboard-focused item of a menu and the
settings logout button have their focus ring touching the border; the palette's sticky
header reads slightly denser; nested modals stack veils (up to four on mobile).

## 6d. Step 4a — motion tokens + micro-interactions (done)

Spec: `docs/plans/2026-09-27-motion-micro-design.md` (commit `116b4f6e`, reviewed PASS in
7 rounds; §5.4 amended after the browser review). Code: commit `46b4926c`. Step 4 is split
in three sub-steps, each with its own spec, review and commit: **4a** (this one), **4b**
(`wa-details` open/close), **4c** (gliding indicators + `SegmentedControl`).

### 6d.1 What it does

- `frontend/src/styles/motion.css`: `--motion-dur-1/2/3` (120/200/380 ms),
  `--motion-dur-press`, `--motion-ease`, `--motion-ease-out`, `--motion-ease-spring`
  (the mock's `linear()`), and `--motion-amount` (1; 0 under reduced motion) that
  multiplies every movement. Web Awesome's `--wa-transition-*` map onto them on
  `:root, .wa-invert`.
- Micro-interactions: button press (incl. WA internal dialog "×", tab scroll arrows, tag
  "×" via exported parts), icon grow on icon-only plain buttons, snippet lift, Send icon
  and "go" arrow nudges, gear quarter turn, completed-task check pop (Tasks tab visible
  only).
- Reduced motion: movement off, fades kept; the working robot, the pending workflow
  hourglass and the busy logo pulse in opacity (`motion-status-pulse`).
- **Removed after the browser review:** the session row nudge and the "⋮" slide ("on
  retire ça complètement").

### 6d.2 Lessons (do not regress)

- The awesome theme sets `--wa-transition-*` to a **unitless `0`** on buttons: mixed into
  a duration list with `ms` values, it invalidates the whole list. `motion.css` restates
  it as `0s`.
- Replacing a `transform` with an individual property can **expose a theme rule** it was
  overriding: the awesome theme translates native buttons 4px down on press; the snippet
  chips keep `transform: none`.
- Web Awesome re-declares its tokens on `.wa-invert` (tooltips): any token override goes
  on `:root, .wa-invert`.
- Vue scoped styles rename a keyframe only when the same scoped block declares it: a
  global keyframe (`motion-status-pulse`) is referenced as is.
- A CSS animation does not run in a `display: none` / detached subtree; it starts when
  shown. Gate one-shot animations on real visibility (the Tasks tab `active` prop).
- Sub-agents: use the Agent tool, not separate TwiCC sessions (user, 2026-09-27).

### 6d.3 Not verified

Chrome pass, share viewer and artifact shell visuals, touch (Firefox checked by the user;
computed styles probed in headless Firefox).

## 6e. Step 4b — `wa-details` open/close motion (done)

Spec: `docs/plans/2026-09-27-details-motion-design.md` (commit `bfb01178`, reviewed PASS
in 10 rounds; §11 = amendments after the browser review, binding). Code: commit `d11ea446`.

### 6e.1 What it does

- `frontend/src/utils/detailsMotion.js` replaces `WaDetails.prototype.handleOpenChange`
  (user's idea; same tag, styles, events): cards grow to the real content height, follow
  their content while open, fold with it visible (`composables/useDetailsClosing.js`),
  handle interrupted gestures, show a loading line when empty, keep instant restores.
- Opening and follow wait for an idle main thread (`requestIdleCallback`, 250ms cap);
  heights use `--motion-ease-out-height` (easeOutQuad).
- Dedicated tool results capped at 20rem (images opt out with `uncapped: true`);
  Thinking/Reasoning not capped.
- Side spacing of an open `wa-details` moved into its direct children
  (`--details-spacing`), so scrollbars touch the card edge.
- Markdown raw/copy toolbar removed from tool cards (bug on `main`); Codex Reasoning's
  toolbar placed outside like Thinking.

### 6e.2 Lessons (do not regress)

- Fighting a Web Awesome animation from outside (re-timing, cancelling its pending
  branches) races endlessly; owning the method (prototype replacement, `watch` calls it
  by name) converged. Guard test pins Web Awesome 3.3.1.
- A height animation runs on the main thread: start it when the thread is idle and with
  a gentle start, else a busy phone drops frames into big jumps. Measure on the phone
  (screen recordings analysed frame by frame; comparison artifacts in session
  2992a814-2ce5-4622-a145-219d5e2fd203).
- Firefox for Android animates cards holding very large highlighted bodies at ~15 fps;
  Chrome is fluid. `contain`, `will-change` and dropping the fade changed nothing.
- `overflow: clip` on one axis only (`overflow-y`): a sideways toolbar must stay visible.
- A global rule moving padding into children must be checked against every child's own
  padding rule (`var(--spacing, 0)` pattern).

## 7. Deferred / open topics

- **Project-selector widening** (on hover/focus/open it pushes the peer button out of the
  sidebar, which now reads as visibly truncated at the sidebar edge) — "to rethink later",
  user 2026-09-26.
- Possible removal of the theme and accent-color choices (user idea, undecided).
- A bundled variable font (Inter / Geist, self-hosted) — would add an npm dependency: the
  user's call.
- CHANGELOG entry for the redesign — propose at the end, do not write without asking.

## 8. What the user sees — visual description per topic (validated as a whole)

The user validated the **C · Signature** direction **as a whole** in the mock, not each
detail one by one. This section describes, in plain visual terms, what each topic looks and
feels like in that validated mock — the target for each step. Section 4 lists the points the
user explicitly changed; they override anything here. Section 9 gives the matching values.

### 8.1 Canvas and ambient light (step 1 — done)

- The page background is no longer a plain white/black: it is a **canvas slightly tinted
  with the accent color**.
- Two **soft glows** sit on it: one in the top-left corner in the accent color, one in the
  bottom-right corner in a neighbouring hue (the accent turned by 70°: green → cyan, cyan →
  blue-violet). They fade into the tinted canvas.
- In dark mode the tint and the glows are stronger, so the accent reads across the whole
  background (user-tuned).
- The sidebar has no background of its own: its sessions sit directly on the canvas.

### 8.2 Floating panels (step 1 — done)

- The working zones are **cards floating on the canvas**: rounded corners, a thin border, a
  soft shadow cast downward, and a small gap (0.5rem) of canvas around each one.
- In a session, the chat, each dock (right-top, right-bottom, bottom, left) and the overlay
  are **separate cards**; the session title bar sits on the canvas above them.
- Between the sidebar and the content there is no line anymore: the gap is the separator. A
  thin accent line appears in the gap when hovering it, full strength while dragging.
- On touch screens, a small rounded pill sits in the middle of every resizable gap.
- Iframes (Browser, HTML previews) follow the rounded corners of their card.

### 8.3 Depth (step 2)

- Everything that is "on top" of something gets a **soft, layered shadow** instead of the
  current hard 2px line under it: message cards lift slightly off the chat, buttons look
  gently raised (with a faint light edge on top), inputs look slightly recessed.
- Floating things (menus, toasts, dialogs, command palette, settings) get a deeper, wider
  shadow, clearly above the page.
- **User messages** get a very faint accent tint, so they read differently from the
  assistant's cards at a glance.
- In dark mode, where shadows barely show, raised things get a hair-thin lighter line on
  their top edge instead.
- Cards on stats and home lift a little more on hover. The "+x% / −x%" badges become soft
  filled pills instead of outlined boxes.

### 8.4 Glass overlays (step 3)

- Menus, dialogs, toasts, the command palette and the settings panel become **translucent
  and blurred**: the content behind shows through, softened.
- Their tint is **a light wash of the accent color** (never grey), with a faint accent border
  and a thin highlight on the top edge.
- The dimmed backdrop behind a dialog is lighter and blurred, slightly tinted, instead of a
  flat dark veil.
- Tooltips are slightly translucent too.

### 8.5 Motion (step 4)

- Every hover/press/state change uses the same **smooth timing**, with a subtle **spring**
  on things that move (slight overshoot, then settle).
- The **active-tab underline glides** from one tab to the next instead of jumping; same for
  the segmented controls, the settings section indicator, and the command-palette highlight
  following the arrow keys.
- Tool cards (Bash, Edit, Read…) **open and close smoothly** instead of snapping.
- Collapsing/expanding the sidebar is animated.

### 8.6 Micro-interactions (step 4)

- Buttons **press in** slightly when clicked; snippet chips lift by 1px on hover.
- Session rows **nudge right** a little on hover; their "⋮" menu slides in.
- The Send arrow nudges up on hover; "→" arrows nudge right; the settings gear turns a
  quarter; small icon buttons grow slightly on hover.
- In the Tasks tab, a completed task's check **pops** in.

### 8.7 Entrances (step 5)

- A new message **fades in while sliding up** a few pixels (user messages slide in from the
  right). Only messages that arrive live animate — scrolling back never replays them.
- Menus **pop open from where they were clicked**; dialogs spring in; the command palette
  drops in; the settings panel grows from its button; closing is a quick fade.
- Toasts **drop in from the top center** with a small bounce, and fade up when leaving.
- The session list appears in a quick cascade on load.
- Instead of a blank screen while a session loads, a **skeleton** (grey card shapes with a
  moving shimmer) shows where the messages will be.
- Switching light/dark reveals the new theme in a **growing circle** from the click point.

### 8.8 Accent glow (step 6)

- The main buttons (Send, New session, Create…) get a **subtle vertical gradient** in the
  accent color, a soft coloured shadow, and a **light sheen** that sweeps across on hover.
- Focused fields get a **soft accent halo** around them instead of the hard thick outline.
- The **selected session** in the sidebar is clearly highlighted: a fuller accent-tinted
  fill that fades to the right, a thin accent outline, a soft accent shadow, a bolder title.
  **No bar on its left.**
- The active-tab underline is a small accent gradient that glows slightly.
- The context-usage ring glows in its colour; usage bars and switches get gradients.

### 8.9 Live states (step 6)

- While the agent works, its "working" card has a **light running around its border** (a
  comet-like accent streak), the "working" label **shimmers**, and three dots bounce.
- Unread sessions show a small accent dot that **breathes** gently.
- The context ring pulses softly while the agent is working.
- A pending question from the agent shows with a **gradient accent border**.
- The hopping robot icon of a running session stays as it is — **no expanding halo**.

### 8.10 Typography (step 2 or 4)

- Numbers (times, costs, percentages) keep a **fixed width**, so they stop jittering when
  they change.
- Titles are slightly tighter and a bit bolder; sidebar section labels ("Pinned", "Last 24
  hours") become small uppercase labels with letter spacing.
- Long message paragraphs avoid lonely last words (`text-wrap: pretty`).

### 8.11 Secondary screens (step 7)

- **Project stats:** the big numbers **count up** when the screen opens; the activity
  heatmap fills in as a **wave**; the cost sparkline **draws itself** with a soft gradient
  under it.
- **Home:** workspace and project cards arrive in a **cascade**; their sparklines draw in;
  a hovered card **lifts and glows in its workspace's own color**.
- **Tasks tab:** during a turn, tasks **tick one by one** (check pop, strike-through, the
  progress bar fills).
- **Question widget:** slides up from the composer; options are cards with a springy radio;
  the selected option has an accent outline and glow.
- **Settings:** the selected section indicator **glides** in the left menu; the content
  crossfades between sections.

## 9. Steps 2–7 — values (from the mock)

All values below come from `mock.css`; they are starting points, to be adapted to Web
Awesome tokens and re-reviewed per step. Everything must honour `prefers-reduced-motion`
(the mock collapses all animations/transitions to 0.01ms).

### Step 2 — Depth

- Layered soft shadows instead of the hard 2px offset (light):
  - `--sh-1: 0 1px 1px oklch(0.25 0.02 275 / .04), 0 1px 3px oklch(0.25 0.02 275 / .07)`
  - `--sh-2: 0 1px 2px …/.05, 0 4px 12px -2px …/.08, 0 16px 32px -12px …/.12`
  - `--sh-3: 0 2px 4px oklch(0.2 0.02 275 / .06), 0 12px 28px -4px …/.16, 0 32px 64px -16px …/.24`
  - `--hl: inset 0 1px 0 oklch(1 0 0 / .6)` (top highlight on buttons)
- Dark: shadows vanish on dark, so add a 1px top highlight:
  `--sh-1: 0 1px 2px oklch(0 0 0 / .35), inset 0 1px 0 oklch(1 0 0 / .04)`, `--sh-2` /
  `--sh-3` similar with `.4/.5` and `.45/.65`, highlight `.05/.06`.
- Message cards: `--sh-1`; user card with a faint accent tint
  (`color-mix(brand-fill-quiet 70%, white)`; dark 55% over raised); assistant card on the
  raised surface; radius `0.875rem` in the mock.
- Buttons `--sh-1 + --hl`; inputs with a faint inset shadow; composer box `--sh-2`;
  menus/toasts/dialogs/palette/settings `--sh-3`; tool cards `--sh-1`.
- Stats/home cards `--sh-1`, `--sh-2` on hover; delta badges become soft filled pills.
- Watch the **panel shadow budget** (§6.1) for panel cards; message/button shadows are
  inside cards and not bound by it.
- Typography (can ride along here or in step 4): tabular numbers, titles
  `letter-spacing: -0.015/-0.02em`, weight ~650, section labels small caps
  (`.6875rem`, uppercase, `.07em`), `text-wrap: pretty` on message text.

### Step 3 — Accent-tinted glass overlays

- `--glass-bg` light `color-mix(oklch(0.975 0.028 h) 74%, transparent)`, dark
  `color-mix(oklch(0.24 0.04 h) 74%, transparent)`; `--glass-border` brand-60 at 22%/28%;
  top highlight; `backdrop-filter: blur(1.125rem) saturate(1.6)`.
- Inputs inside glass overlays at 70% surface; menu hover / palette highlight tinted
  `brand-fill-normal`; dialog backdrop `blur(.375rem)` + faint accent-tinted dim.
- Targets: `wa-dialog`, dropdown menus, Notivue toasts, command palette, search overlay,
  settings popover, tooltips (`--c-text` at 82% + blur).
- Must not add `backdrop-filter` to an ancestor of panes (§6.2).

### Step 4 — Motion + micro-interactions

- Tokens: `--dur-1: 120ms; --dur-2: 200ms; --dur-3: 380ms;`
  `--ease: cubic-bezier(.2,.8,.2,1); --ease-out: cubic-bezier(.22,1,.36,1);`
  `--ease-spring: linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 25.3%, 0.849 31.5%, 0.937 38.1%, 0.968 41.8%, 0.991 45.7%, 1.006 50.1%, 1.015 55%, 1.017 63.9%, 1.001 85.9%, 1)`;
  also override `--wa-transition-*`.
- Sliding tab ink (active tab indicator glides; spring), sliding segmented control and
  settings-nav indicator, palette highlight gliding with ↑/↓.
- Animated expand/collapse of tool cards (`grid-template-rows 0fr → 1fr`), sidebar width.
- Micro: press `scale(.95–.96)` (60ms), snippets lift 1px, session row nudge
  `translateX(.1875rem)` on hover, kebab slides in, send arrow nudges up, "go" arrows nudge
  right, settings gear rotates 90°, ghost icon buttons scale 1.12, task check pop.

### Step 5 — Entrances

- New messages: fade + `translateY(.75rem) scale(.985)` + slight blur (user messages from
  the right), `--dur-3`, staggered 70ms. **Only for messages arriving live — never items
  re-mounted by the virtual scroller.**
- Menus pop from their anchor (180ms), dialogs spring in (320ms), backdrop fades + blur,
  palette drops in, settings pops up from bottom-left, tooltips fade after 250ms, toasts
  slide down from the top (spring) and fade up on exit.
- Session list cascade on load (22ms stagger), tab-panel/screen crossfades.
- **Skeleton** with shimmer instead of the blank screen while a session loads.
- Theme switch: circular reveal from the click point (View Transitions API).

### Step 6 — Accent glow + live states

- Primary button: vertical accent gradient + inset highlight + coloured soft shadow; sheen
  sweep on hover; brighter on hover.
- Focus: soft halo (`0 0 0 .25rem brand/20%` + outer glow) instead of the hard 3px outline.
- Selected session: gradient fill `brand-fill-normal → brand-fill-quiet`, 1px accent ring,
  soft accent shadow, bold title (dark: `oklch(0.32 0.07 h) → oklch(0.25 0.05 h / .6)`).
  **No left bar.**
- Tab ink: accent gradient + glow; context ring glow; usage bars gradient; switches
  gradient + glow.
- Live: working card with a comet running around its border
  (`@property --angle` + conic-gradient, 2.8s), shimmering "working" label, bouncing dots;
  breathing unread dot; pulsing context ring while working; question widget with a gradient
  border. **No expanding halo around the working badge.**

### Step 7 — Secondary screens

- Project stats: numbers count up (1s, ease-out cubic), heatmap cells fill in a wave,
  sparklines draw themselves with a soft gradient fill, deltas as soft pills.
- Home: cards cascade in, sparklines draw, hovered card lifts and glows in its workspace
  color.
- Tasks tab: tasks tick one by one (check pop, strike-through, progress bar).
- Question widget (PendingRequestForm): slides up, springy radio, gradient border.
- Settings: gliding section indicator, content crossfade.

## 10. Environment

- Worktree: `/home/twidi/dev/twicc-poc/.worktrees/enhanced-ui`, branch `enhanced-ui`
  (from `main` at `43402928`).
- Dev instance: `uv run ./devctl.py start|stop|status` from the worktree →
  http://localhost:5174 (backend 3501). DB copied from `~/.twicc` on first setup.
- Tests: `cd frontend && node --test` (449 at the end of step 1, 455 at the end of step 2, 468 at the end of step 3, 487 at the end of step 4a, 537 at the end of step 4b).
