# Project stats motion — design (visual refresh, step 7a)

## 1. Context

Step 7 of the "Signature" visual refresh animates the secondary screens. The user split it
into 7a–7f (roadmap §6m). **7a (this document)** is the project **Stats** tab: numbers count
up, the heatmap fills in a wave, the graph curves draw themselves with a soft gradient under
them, plus the mock's two glow touches on this screen. Read first:
`docs/plans/2026-09-26-visual-refresh-roadmap.md` §4 (binding: **Firefox parity**,
**reduced motion is reduced, not none**), §5 (process), §6m, §8.11 and §9 "Step 7".

What the user sees today (worktree instance, http://localhost:5174, a project page with no
session selected, tab **Stats**):

- two "overview" rows of four cards (Daily / Weekly / Monthly / Total), for sessions then
  message turns (`components/activity/ActivityDashboard.vue`); each card shows one big
  number and up to two smaller numbers, with a trend pill beside each;
- a **Heatmap / Graph** switch (`components/activity/ContributionGraphs.vue`);
- Heatmap: three calendar heatmaps (sessions, message turns, cost when costs are shown),
  `components/activity/ContributionGraph.vue`, drawn by the `vue3-calendar-heatmap` library
  (2.0.5); below 600px of container width the heatmap turns vertical;
- Graph: `components/activity/ContributionSparklines.vue`, one curve per metric
  (**Separate**) or the three curves in one chart (**Combined**), lines only (the
  `gradientId` / `maskId` fields of each curve are declared but never used).

Already done by earlier steps, not in scope: the trend pills are soft filled pills (step 2,
`appearance="filled" pill`); the cards lift to depth level 2 on hover (step 2); tabular
digits on `body` (`styles/transcript-tokens.css`).

Mock reference (`mock.css` / `app.js`, roadmap §3):

```css
@keyframes cell-in { from { opacity: 0; transform: scale(.3); } }
@keyframes draw { from { stroke-dashoffset: 1; } }
.mock.fx-enter .heat.play i { animation: cell-in 360ms var(--ease-spring) both;
    animation-delay: calc(var(--c) * 14ms + var(--r) * 10ms); }
.mock.fx-enter .spark.play .line { stroke-dasharray: 1; stroke-dashoffset: 0;
    animation: draw 1100ms var(--ease-out) both; }
.mock.fx-glow .spark .area { fill: url(#spark-fill); }   /* stop .35 → 0 of the line colour */
.mock.fx-glow .spark .line { filter: drop-shadow(0 0 .1875rem color-mix(in oklab, var(--spark) 60%, transparent)); }
.mock.fx-glow .stat .big { background: linear-gradient(180deg, var(--c-text),
    color-mix(in oklab, var(--c-text) 70%, var(--b-60))); background-clip: text; color: transparent; }
```

`countUp` in `app.js`: 1000ms, ease-out cubic (`1 - (1 - k)^3`), from 0 to the value,
`requestAnimationFrame` driven, final value at once when animations are off.

## 2. User decisions (2026-09-30)

- **Replay:** the animations play **each time the Stats tab becomes visible**: opening
  the project page, coming back from Files / Git / Terminal, coming back from a session,
  switching back to a cached project.
- **Data change while visible** (provider filter, startup polling): the numbers count from
  the old value to the new one, shorter (600ms). The heatmap changes its colours without
  replaying the wave. The curves change shape without redrawing.
- **Gradient under the curves:** Separate mode only; Combined keeps three plain lines.
- **Glow:** both mock touches — the big numbers get a vertical text gradient, the curves a
  soft halo of their colour (both modes).
- **Heatmap orientation:** always horizontal, scaled to the container width; the vertical
  mode below 600px is removed (user, mid-spec).
- The small activity curve in the project header (`ActivitySparkline.vue`, next to the
  project name) is the component the home cards use: its "draw in" belongs to 7b.

## 3. Trigger model

Every visible "screen opens" case already goes through a **display change** that restarts
CSS animations: the tab panel is `display: none` while inactive (`wa-tab-panel` `:host`
rule), the project detail content is `v-show`n off while a session is selected
(`views/ProjectView.vue`), and `KeepAlive` detaches the cached project's DOM. A CSS
animation restarts whenever its element goes from not rendered to rendered, and starts
when the element mounts. So:

- the heatmap wave and the curve reveal are **plain CSS animations** on the elements;
- the count-up is JavaScript, **started by the `animationstart` event** of a CSS fade on
  the number (§4.2). One trigger model for all three effects, no visibility observer.

Data updates keep the same DOM nodes (the heatmap re-renders `rect`s in place, keyed by
index; the polylines update `points`), so no CSS animation replays on a data change (§2).
Known replays that are accepted (new elements mount, so they play their entrance):

- the heatmap is keyed by its colours (`ContributionGraph.vue` `:key`): a light/dark
  switch remounts it and replays the wave;
- toggling Heatmap ↔ Graph or Separate ↔ Combined (the screen content is new);
- a provider filter with no data empties `dailyActivity`, which unmounts the overview
  cards (`ActivityDashboard.vue` `v-if="periods.length > 0"`) and the heatmaps
  (`ContributionGraph.vue` `v-if="heatmapValues.length > 0 …"`); switching back to a
  provider with data remounts them: the numbers count from 0 and the wave replays;
- turning the "show costs" setting on mounts the cost numbers, the cost heatmap and the
  Separate cost curve, which play their entrance; in Combined mode the cost polyline joins
  an SVG that is already revealed, so it shows at once.

## 4. Numbers count up

### 4.1 `AnimatedNumber.vue` (new, `components/ui/`)

Props: `value` (`Number`, may be `null`), `format` (`'integer' | 'average' | 'cost'`).
Renders one root `<span class="animated-number">`:

- `integer`: the rounded displayed value;
- `average`: `formatAvg` of `ActivityDashboard.vue` (one decimal, integer when whole,
  `-` for `null`) — moved to `utils/countUp.js` and imported by both;
- `cost`: `<CostDisplay :cost="displayed" />` (CostDisplay keeps its own `-` for `null`
  and its `< 0.01 → 0.00` rule).

Non-prop attributes (the `wa-heading-*` classes) fall through to the root span.

### 4.2 Start, retarget, stop

- Scoped style of `AnimatedNumber.vue` (pinned whole, §8):

  ```css
  .animated-number {
      animation: animated-number-in var(--motion-dur-2) ease-in-out backwards;
  }
  @keyframes animated-number-in { from { opacity: 0; } }
  ```

  The fade restarts on every "screen opens" case (§3) and paints the first frame
  transparent, so the final value never flashes before the count starts.
- `@animationstart` on the root, filtered to `event.target === event.currentTarget` and
  `event.animationName.startsWith('animated-number-in')` (Vue renames scoped keyframes to
  `animated-number-in-<id>`): if motion is allowed and `value` is a number, start a tween
  **from 0 to `value`, 1000ms**; else show `value`.
- `watch(value)`: once a count has started at least once in this component instance, and
  if motion is allowed and both the displayed and the new value are numbers, start a tween
  **from the currently displayed value to the new value, 600ms** (also when a tween is
  running: it restarts from where it is). Otherwise show the new value at once (`null` →
  `-`).
- Tween: `requestAnimationFrame`, ease-out cubic `1 - (1 - k)^3`, `k = min(1, elapsed /
  duration)`; the last frame sets exactly `value`. One tween handle per instance: every
  tween start cancels the running one first (a Files → Stats round trip in under 1s
  restarts the count from 0). `onDeactivated` and `onBeforeUnmount` cancel the tween and
  set `displayed = value`.
- Motion allowed = `!matchMedia('(prefers-reduced-motion: reduce)').matches`, read when a
  tween would start (no cached value).
- Fallback: until an `animationstart` arrives, `displayed === value`. A browser or a rule
  that never runs the fade shows the right numbers, only without the count.

Pure helpers in `utils/countUp.js` (tested with `node:test`): `easeOutCubic(k)`,
`tweenValue(from, to, k)`, `formatAvg(value)`.

### 4.3 Which numbers count

In `ActivityDashboard.vue`, exactly four `AnimatedNumber` elements replace the current
value elements (`:value` / `format` / `class`):

| Replaces | `AnimatedNumber` |
|---|---|
| `<span class="wa-heading-2xl">{{ period.mainValue }}</span>` | `:value="period.mainValue" format="integer" class="wa-heading-2xl stat-number"` |
| `<span class="wa-heading-l">{{ formatAvg(period.sub1Value) }}</span>` (sessions mode) | `:value="period.sub1Value" format="average" class="wa-heading-l stat-number"` |
| `<CostDisplay :cost="period.sub1Value" class="wa-heading-l" />` (messages mode) | `:value="period.sub1Value" format="cost" class="wa-heading-l stat-number"` |
| `<CostDisplay :cost="period.sub2Value" class="wa-heading-l" />` | `:value="period.sub2Value" format="cost" class="wa-heading-l stat-number"` |

The previous-period values (`prev-value`, still formatted with `formatAvg` imported from
`utils/countUp.js`), the trend pills and the "N/A" pills stay static.

The number grows from one digit to its final width during the count, so the trend pill
beside it moves right during the first part of the count (digits are tabular; the width
changes only when a digit is added, early in an ease-out count). Accepted: the mock has
the same layout and behaviour.

### 4.4 Text gradient on the counted numbers

The gradient goes on every counted number: the big `wa-heading-2xl` number **and** the
`wa-heading-l` sub-metric numbers (the mock had it on its big and its sub numbers alike;
the browser review judges both). `ActivityDashboard.vue` passes `class="stat-number"`
(next to the `wa-heading-*` class) to each `AnimatedNumber`. A scoped rule of the parent reaches a child component's root
element, so in `ActivityDashboard.vue`:

```css
.stat-number {
    background: linear-gradient(180deg, var(--wa-color-text-normal),
        color-mix(in oklab, var(--wa-color-text-normal) 70%, var(--wa-color-brand-60)));
    background-clip: text;
    color: transparent;
}
.stat-number :deep(.cost-icon) {
    color: var(--wa-color-text-normal);
}
```

The second rule exists because the dollar icon of `CostDisplay` paints with
`currentColor`: under `color: transparent` it would vanish. The test pins both rules whole
(§8).

## 5. Heatmap

### 5.1 Always horizontal

`ContributionGraph.vue`: remove `isVertical`, `useElementSize` (and its import), the
`graphContainer` ref (`ref="graphContainer"` and `const graphContainer = ref(null)`; `ref`
leaves the `vue` import if nothing else uses it), the `vertical` prop on
`CalendarHeatmap`, the `vertical` class, the `.contribution-graph.vertical` rules, and
`isVertical` from the heatmap `:key`. The test asserts that none of `vertical`,
`isVertical`, `useElementSize`, `graphContainer` is left. The SVG keeps `width: 100%; height: auto` and the
`.vch__container` `max-width: 80rem`: narrow containers get smaller cells.

### 5.2 The wave

The library renders one `g.vch__month__wrapper` per week (children of
`g.vch__year__wrapper`, in week order) holding one `rect.vch__day__square` per day (day
order; future days render no element, only a comment). `sibling-index()` therefore gives
the week (on the `g`) and the day (on the `rect`). The rect's own SVG `transform`
attribute positions it: a CSS `transform` would replace that attribute (the cell jumps to
the origin), and `scale` would compose with it around the `0 0` origin (the cell moves).
**The wave is an opacity fade only**.

```css
@property --heat-week-delay { syntax: '<time>'; inherits: true; initial-value: 0s; }

.contribution-graph :deep(.vch__day__square) {
    animation: heatmap-cell-in 360ms ease-in-out backwards;
}
@supports (animation-delay: calc(sibling-index() * 1ms)) {
    .contribution-graph :deep(.vch__month__wrapper) {
        --heat-week-delay: calc(sibling-index() * 14ms);
    }
    .contribution-graph :deep(.vch__day__square) {
        animation-delay: calc(var(--heat-week-delay) + sibling-index() * 10ms);
    }
}
@keyframes heatmap-cell-in { from { opacity: 0; } }
```

The registered `<time>` property computes `sibling-index()` on the week `g` and inherits
the resulting time to its cells (an unregistered custom property would be substituted on
the rect and read the day index). Probed in Firefox 156 (§9). A 53-week year finishes in
about 53 × 14 + 7 × 10 + 360 ≈ 1.2s. Without `sibling-index()` support every cell fades
together.

The cell rule, the `@supports` block and the keyframes go in the scoped style of
`ContributionGraph.vue`. `@property` is global whatever block holds it; put it in
`styles/motion.css` next to the motion tokens (a scoped block does not rename it, but a global file makes the
registration's scope obvious).

## 6. Curves (Graph view)

### 6.1 Reveal

Each curve SVG (`svg.contribution-sparkline`, both modes) reveals left to right. The
existing rule of `ContributionSparklines.vue` becomes (pinned whole, §8):

```css
.contribution-sparkline {
    display: block;
    width: 100%;
    height: 150px;
    overflow: visible;
    animation: sparkline-reveal 1100ms var(--motion-ease-out) backwards;
}
@keyframes sparkline-reveal {
    from { clip-path: inset(-1rem 100% -1rem -1rem); }
    to { clip-path: inset(-1rem -1rem -1rem -1rem); }
}
```

- Both keyframes are written: `inset()` does not interpolate with `none`, so a `from`-only
  keyframe would jump. After the animation the rule has no `clip-path` (fill `backwards`).
- `overflow: visible` and the `-1rem` insets let the halo (§6.3) show above the peaks
  (drawn at y≈1–3 of the 150 viewBox), below the baseline (y≈147) and past the left edge
  during the reveal, instead of being cut by the SVG box; the `-1rem` right inset at the
  end keeps the right-edge halo from popping in when the animation ends.
- A wipe instead of the mock's `stroke-dashoffset` draw: it reveals the line and the area
  under it together (a dash draw would leave the area to appear on its own), and it
  progresses along x (time), not along the path length, where a dash draw slows on every
  spike.
- `clip-path` also clips hit-testing: during the 1.1s reveal, the hover cursor and tooltip
  respond over the revealed part only. Accepted.

### 6.2 Gradient area (Separate mode)

In Separate mode each curve SVG gets, in a `<defs>` before its `g`:

```html
<linearGradient :id="`${uid}-${curve.key}-area`" x1="0" x2="0" y1="1" y2="0">
    <stop offset="0" :stop-color="colorVars(curve.colorPrefix).stroke" stop-opacity="0.35"></stop>
    <stop offset="1" :stop-color="colorVars(curve.colorPrefix).stroke" stop-opacity="0"></stop>
</linearGradient>
```

and, inside the `g`, before the polyline:

```html
<polygon
    class="sparkline-area"
    :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
    :points="areaPoints(curve.points, MIN_Y)"
    :fill="`url(#${uid}-${curve.key}-area)`"
></polygon>
```

- `const uid = useId()` (Vue 3.5): one id per component instance, suffixed by the curve
  key, so the three gradients of one instance never share an id and two cached project
  panels never collide either. The unused `gradientId` / `maskId` fields of both curve
  lists are removed.
- `areaPoints(points, baseY)` is a pure helper in a new `utils/sparklineArea.js`: it
  returns the points string with `<last x>,<baseY> <first x>,<baseY>` appended, and `''`
  for an empty string. In the flipped `scale(1,-1)` frame `MIN_Y` is the bottom, so the
  polygon closes along the baseline.
- The gradient maps onto the polygon's bounding box in its local frame, before the flip,
  so `y1="1"` is the local maximum: the opaque stop sits at the peaks, drawn at the top
  (same attributes as `ActivitySparkline.vue`).
- Colours are passed as attributes holding `var(...)`, as the existing polylines and
  `ActivitySparkline.vue` stops already do.
- CSS (pinned whole): `.sparkline-area { stroke: none; }`.
- Combined mode renders no `<defs>` and no area.

### 6.3 Halo

Every polyline (both modes) gets `class="sparkline-line"` and
`:style="{ '--curve-color': colorVars(curve.colorPrefix).stroke }"`. CSS (pinned whole):

```css
.sparkline-line {
    filter: drop-shadow(0 0 0.1875rem color-mix(in oklab, var(--curve-color) 60%, transparent));
}
```

The gradient stops of §6.2 use `colorVars(...)` directly, not `--curve-color` (a property
set on the polyline does not reach the `<defs>`).

## 7. Reduced motion

Per roadmap §4 (movement removed, fades and status indicators kept):

| Effect | Normal | Reduced motion |
|---|---|---|
| Number fade (`animated-number-in`) | 200ms fade | kept (a fade) |
| Count-up | 1000ms / 600ms | **none**: the number is shown at its value |
| Heatmap wave | staggered fade | kept (a fade; no movement in it) |
| Curve reveal | 1100ms wipe | **replaced by a 300ms opacity fade** |
| Text gradient, halo, area | static | static |

The count-up gate is in JavaScript (§4.2). The only reduced-motion CSS, in
`ContributionSparklines.vue` (pinned whole), placed **after** the `.contribution-sparkline`
rule (same specificity: the later rule wins; the test asserts the order):

```css
@media (prefers-reduced-motion: reduce) {
    .contribution-sparkline {
        animation: sparkline-fade 300ms ease-in-out backwards;
    }
}
@keyframes sparkline-fade { from { opacity: 0; } }
```

## 8. Tests

`node:test`, same style as `styles/glow.test.js` (parse the SFC style block, pin each new
rule **whole**, ordered declaration list — roadmap §6l.2):

- `utils/countUp.test.js`: `easeOutCubic` (0 → 0, 1 → 1, 0.5 → 0.875), `tweenValue`
  (ends exactly on `to`, works downward), `formatAvg` (`null` → `-`, `12` → `12`,
  `1.25` → `1.3`, `0.04` → `0`: the current `Math.round(v * 10) / 10` behaviour).
- `utils/sparklineArea.test.js`: `areaPoints('0,1 10,5 20,3', 1)` →
  `'0,1 10,5 20,3 20,1 0,1'`; `areaPoints('5,3', 1)` → `'5,3 5,1 5,1'`; `''` → `''`.
- A new `styles/stats-motion.test.js`, pinning every rule written in this spec whole
  (selector + ordered declarations; keyframes by their frames):
  - `styles/motion.css`: the `@property --heat-week-delay` block (§5.2);
  - `ContributionGraph.vue`: the cell rule, the `@supports` block with its two rules, the
    `heatmap-cell-in` keyframes (§5.2); no occurrence of `vertical` / `isVertical` /
    `useElementSize` / `graphContainer` left (§5.1);
  - `ContributionSparklines.vue`: the `.contribution-sparkline` rule, both keyframes of
    `sparkline-reveal`, the reduced-motion block and `sparkline-fade` (§6.1, §7), the
    `.sparkline-area` and `.sparkline-line` rules (§6.2, §6.3); template: exactly one
    `<linearGradient` with `x1="0" x2="0" y1="1" y2="0"` and the id pattern
    `` `${uid}-${curve.key}-area` ``, its two stops each with its `offset` (`"0"`, `"1"`),
    `:stop-color="colorVars(curve.colorPrefix).stroke"` and `stop-opacity` (`"0.35"`,
    `"0"`), the polygon's `class`, `:transform`, `:points="areaPoints(curve.points, MIN_Y)"`
    and `fill`; the reduced-motion block placed after the `.contribution-sparkline` rule; the
    `sparkline-line` class and `--curve-color` binding on both polylines; no
    `gradientId` / `maskId` left;
  - `AnimatedNumber.vue`: the `.animated-number` rule and `animated-number-in` keyframes
    (§4.2), `@animationstart` on the root;
  - `ActivityDashboard.vue`: the `.stat-number` and `.stat-number :deep(.cost-icon)` rules
    (§4.4); template: exactly the four `<AnimatedNumber` elements of the §4.3 table, each
    with its `:value`, `format` and `class`; no `{{ period.mainValue }}`, no
    `wa-heading-l">{{ formatAvg(period.sub1Value)`, no `<CostDisplay :cost="period.sub1Value" class="wa-heading-l"`
    or `<CostDisplay :cost="period.sub2Value" class="wa-heading-l"` left.
- `AnimatedNumber.vue` logic that is not pure (the `animationstart` filter, retarget,
  unmount) is covered by the browser review, not by a DOM test (the repo has no component
  test harness).

## 9. Probe (done while writing this spec)

Headless Firefox 156 (Selenium, `data:` page), the §5.2 rules on two `g` weeks of three
and two `rect`s, both with SVG `transform` attributes as the library renders them:
`CSS.supports('animation-delay', 'calc(sibling-index() * 1ms)')` is `true`; computed
`animation-delay` = 24 / 34 / 44ms (week 1) and 38 / 48ms (week 2) — the registered
`<time>` carries the week index, `sibling-index()` on the rect gives the day. By the CSS
Values 5 definition `sibling-index()` counts element siblings only, so the library's
comment nodes (future days) do not shift it (not probed).

## 10. Browser review (user, http://localhost:5174)

Trigger conditions: a project with activity; tab Stats; light and dark; a window narrower
than 600px for the heatmap; the system "reduce motion" setting on and off.

1. Open a project page → the numbers fade in and count up; the three heatmaps fill in a
   left-to-right wave.
2. Open a session, come back → both replay. Files, Git or Terminal tab, then Stats → both
   replay. Switch to another project, then back (cached page) → both replay.
3. Change the provider filter to one with data → numbers count from the old to the new
   values; heatmaps recolour without a wave. (A provider with no data, then back:
   everything replays from 0, accepted in §3.)
4. Switch to Graph → Separate curves wipe in left to right with their gradient area and
   halo; Combined → three lines with halo, no area. Move the sliders → curves reshape, no
   replay. Hover during the wipe → cursor and tooltip only over the revealed part.
5. Narrow window → the heatmap stays horizontal and fits the width.
6. Reduced motion → numbers appear at their value (fade only), curves fade in.

## 11. Files

- New: `components/ui/AnimatedNumber.vue`, `utils/countUp.js`, `utils/countUp.test.js`,
  `utils/sparklineArea.js`, `utils/sparklineArea.test.js`, `styles/stats-motion.test.js`.
- Changed: `components/activity/ActivityDashboard.vue`,
  `components/activity/ContributionGraph.vue`,
  `components/activity/ContributionSparklines.vue`, `styles/motion.css` (`@property`).
