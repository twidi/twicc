# Streaming publication rate execution

## Status

Tasks 1–3 implementation and deterministic checks pass.
Task reviews and whole-change independent review approve the implementation.
Browser checks provide partial evidence. Performance and complete visual acceptance remain inconclusive.
This record does not establish that the global freeze is fixed.

## Deterministic rate proof

Production uses a per-block `1000 / 30` ms publication interval and a 250 ms pending-episode deadline.
The deterministic harness controls `performance.now()` separately from RAF callback timestamps.
It covers 60, 90, 120, 144, and 240 Hz, skipped opportunities, idle arrivals, continuous backlog, and callback reentry.

Additional measurement replays the tested 10,000-code-unit first burst through public buffer APIs.
All complete text before terminal flush. Terminal flush emits no duplicate publication.
The measurement supplies `-999` as the RAF timestamp and advances only the monotonic clock.

| RAF rate | Regular publications | Minimum gap (ms) | Complete backlog at (ms) | Executed buffer RAF | Terminal exceptions | Final text equality |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 60 Hz | 7 | 33.33333333333334 | 266.6666666666667 | 16 | 0 | true |
| 90 Hz | 8 | 33.33333333333334 | 288.8888888888889 | 26 | 0 | true |
| 120 Hz | 7 | 33.33333333333334 | 250 | 30 | 0 | true |
| 144 Hz | 8 | 34.7222222222222 | 250 | 36 | 0 | true |
| 240 Hz | 8 | 33.333333333333336 | 258.3333333333333 | 62 | 0 | true |

Measurement log: `/tmp/task3-deterministic-metrics.log`.
The command uses `node --input-type=module` and imports `./frontend/src/utils/streamingBuffer.js`.
It initializes one active unmanaged buffer, feeds 10,000 code units at 0 ms, and executes pending callbacks once per frame.
It ends when no callback remains, then verifies the public `flushBuffer` return value.

The continuous-arrival regression keeps its episode unfinished through 270 ms and completes at 280 ms.
Store-action tests prove text and thinking complete by 288 ms after stop without retirement.
These results remain inside each trace's `250 + 1000/30 + F` scheduling bound.
Snapshot tests separately verify activation, explicit snapshot, and terminal exceptions inside the interval.
They also verify silence for identical snapshots, hidden feeds, and hidden flush.

## Store-action integration

Tests execute the production Pinia actions with a controlled monotonic clock and batched RAF callbacks.
They verify stable row/list/cache identity and parsed-envelope silence at skipped RAF opportunities.
They verify complete canonical text and complete displayed text for text and thinking.
They exercise Claude uuid and Codex stream_uuid retirement, thinking expansion transfer, and terminal replacement ownership.

These tests do not mount Vue. Browser component acceptance remains a separate requirement.

## Fixture preparation

The fixture mounts production `SessionView`, `SessionItemsList`, and `VirtualScroller` through real KeepAlive routing.
The `publicationRateBaseline=1` mode loads data and buffer adapters from `1f7d16ef` before creating the shared store.
It rejects simultaneous `baseline=1` and `publicationRateBaseline=1`.
Runtime variable imports let normal mode load without optional adapters.
The existing priority-2 preparation script and adapter behavior remain separate.

The rate preparation script requires the exact worktree root and the reviewed buffer import.
It reads pinned Git source, creates each file exclusively, and rolls back only files created by that invocation.
Removal validates every present file against expected contents before deleting any file.
Tests inject operations and use an isolated temporary directory. They never generate real repository adapters.
Generated adapters remain ignored and must never enter the index.
The controller prepares and removes them around its browser comparison tabs.

All fixture cleanup boundaries select the matching buffer map.
Repeated clears cancel baseline pending handles while preserving production handles in the independent-map regression.
No production module export is replaced.
Provider availability gates are seeded only in the fixture's local Pinia state.
No settings watcher or live WebSocket bootstrap runs. The fixture rejects all mutation fetches.

Visible controls run plain text, fenced code, and open thinking scenarios.
Each resets three sessions to 100 rows, restores the main route and dock, and disables the second consumer.
Each feeds 100 chunks of 200 code units on fixed 20 ms deadlines, then observes backlog for 400 ms.
The same source and schedule apply in both modes.
Reading-above and timed route-hide/return checkboxes add the corresponding retirement acceptance cases.
Thinking opens through production `wa-details.show()` before the feed.

The complete report appears in `#fixture-report`.
The latest report is also available as JSON text in `#publication-rate-report` for browser extraction.
Reports include source, actual deliveries, publication text/times/phase labels, regular gaps, exception counts, RAF executions,
parsed-envelope changes, backlog completion times, complete canonical/displayed/final equality, geometry, scroller anchors, and errors.
They record document visibility changes, route/session identity, view activity, buffer activity, row bounds, and thinking expansion.
Activation/bootstrap/explicit snapshot/terminal labels come from boundary instrumentation, not publication spacing.
Buffer RAF executions include skipped frames and use the same instrumentation in both modes.

## Verification commands

All commands run from `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`.

Initial fixture RED:

```bash
node --test frontend/src/utils/invisibleStreamingFixture.test.js
```

Result: 12 tests, 8 pass, 4 expected failures. Log: `/tmp/task3-red.log`.
Provider-gate RED adds one expected failure. Visibility diagnostic RED adds one expected failure.
Final fixture result: 16 tests, 16 pass. Log: `/tmp/task3-fixture-final.log`.

Final STREAM_TESTS:

```bash
node --test frontend/src/utils/streamingBuffer.test.js frontend/src/utils/streamingPublicationRate.test.js frontend/src/utils/streamPublicationRegistry.test.js frontend/src/stores/streamingRows.test.js frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js frontend/src/components/session/detail/SessionItemsList.resume.test.js frontend/src/utils/invisibleStreamingFixture.test.js
```

Result: 155 tests, 155 pass, 0 fail. Log: `/tmp/task3-stream-final.log`.

Final FULL_TESTS:

```bash
npm --prefix frontend test
```

Result: 1,577 tests, 1,577 pass, 0 fail. Log: `/tmp/task3-full-final.log`.
An earlier run passes 1,575 tests before controller-requested provider and visibility changes.
The final run includes those changes. No package installation runs.

Syntax and whitespace checks:

```bash
node --check frontend/tests/browser/preparePublicationRateBaseline.mjs
node --check frontend/tests/browser/invisibleStreaming.js
git diff --check
```

All pass.

## Browser observations and pending acceptance

The controller runs Chrome against the existing worktree server on port 5175.
Desktop CSS viewport: 1527 × 909. Mobile CSS viewport: 355 × 767.
The baseline uses the pinned pre-rate buffer and store from `1f7d16ef`.
All stable traces below run after the startup and final-render evidence corrections.

| Provider / viewport | Mode / source | Regular publications / buffer RAF | Minimum gap (ms) | Display complete before retirement | Final visual/component source checks |
| --- | --- | ---: | ---: | --- | --- |
| Claude / desktop | Implementation / plain | 8 / 10 | 37.1 | false | true |
| Claude / desktop | Baseline / plain | 2 / 2 | 1014.6 | false | true |
| Claude / desktop | Baseline / fenced code | 2 / 2 | 1105.9 | false | true |
| Claude / desktop | Baseline / open thinking | 6 / 6 | 42.3 | false | true |
| Claude / desktop | Implementation / fenced code | 3 / 3 | 487.0 | true | true |
| Claude / desktop | Implementation / open thinking | 4 / 4 | 99.7 | false | true |
| Codex / mobile | Implementation / plain, after `f2b4d362` | 3 / 3 | 97.6 | true | true |

Canonical and raw final source comparisons pass in these runs.
Actual composer typing succeeds during the baseline plain stream and implementation fenced-code stream.
The corrected Codex mobile plain run ends with a 1.46 px bottom gap.
Its canonical, displayed, parsed displayed, final visual, and final component source comparisons all pass.
Two ResizeObserver notifications remain recorded in that run.
Several Claude runs also record ResizeObserver notifications.
The fixture does not silently suppress them.

RAF opportunities are sparse in both modes, despite recorded visible document and active view/buffer state.
The cause is not established.
A fixed 400 ms observation cannot prove completion when a frame opportunity arrives approximately one second later.
The scheduling contract includes the next frame opportunity, rather than a wall-clock guarantee under blocked or throttled rendering.
These traces cannot establish comparative publication savings at normal or high refresh rates.
Deterministic high-refresh tests provide the cadence proof.

Desktop fixture bottom gaps also occur in the pre-rate baseline.
They do not prove a new product bottom-following regression.
The user reports that actual main-instance bottom following generally works.

The controller also repeats mobile Codex fenced code with reading-above and route hide/return in both modes.
Both report zero regular publications and zero buffer RAF executions while the block remains outside the viewport.
Both preserve history anchor `2` and its -32.06 px offset through retirement.
Both pass canonical, raw final, and exact production visual-item source checks.
Both fail the subsequent final component rendering probe with the same diagnostic.

That probe combines a missing component and incorrect component content into one assertion.
It ignores the `scrollToKey()` return value and uses a fixed wait.
Production retirement restores the reading anchor for eight RAF opportunities.
The fixture waits two RAF opportunities plus 700 ms before probing another row.
Under sparse RAF delivery, the restore can move the probe row outside the rendered window.
Source inspection supports this explanation; the trace does not prove the exact race or production text loss.
This final component probe remains inconclusive in both modes.
A future fixture refinement must wait for restoration and distinguish absent rendering from incorrect content.

Exclude earlier HMR/startup-race runs and pre-`f2b4d362` Codex text runs from acceptance.
Those Codex runs use malformed synthetic item content.

Pending: full provider/mode/viewport/source matrix, normal-frame-rate performance comparison, and complete visual progression acceptance.
Pending: final component rendering after reading-above retirement and route hide/return.
Current browser observations do not establish that the global freeze is fixed.
The controller resets the temporary viewport, closes its fixture tab, and removes generated adapters with the guarded script.

## Limits and review status

RAF wakeups remain. The cap reduces publications, not frame opportunities.
Concurrent visible blocks multiply the aggregate rate.
A large deadline catch-up can trigger a costly Markdown render.
30 Hz progression and the 250 ms deadline remain product choices that require browser evaluation.

Tasks 1–2 independent review closes before Task 3 dispatch, according to the controller's task brief.
Task 3 review closes all startup, rendering-evidence, and error-attribution findings.
Whole-change review approves `70ee160a..d1820fb6` without a significant code finding.
Scoped final review approves `d1820fb6..f2b4d362` and closes the Codex fixture finding.
No implementation finding remains open. Browser acceptance remains partial as stated above.
No server restart, dependency installation, migration, merge, or main-checkout write runs for this task.

## Task 3 review correction round 1

Independent review finds one startup race, one final-render evidence gap, and one error-attribution issue.
The fixture keeps rate buttons and options disabled until the real startup preflight succeeds.
`#fixture-status` displays pending, ready, or failed startup state.
Startup failure records an explicit `fixtureReady: false` report and keeps rate controls disabled.
The exposed scenario also rejects calls before readiness.

Final retirement now checks the production visual item and its exact parsed text.
It checks the mounted production `SessionItem` content against the complete source.
It verifies that component owns the connected replacement DOM element and renders nonempty visible text.
These checks supplement canonical/displayed checks and the final raw item comparison.
Reading-above runs capture retirement geometry before an explicit scroll-to-replacement rendering probe.
`renderProbeMovesViewport` identifies this probe. The recorded retirement anchor excludes the probe's navigation.

Each scenario records the initial global error-array length and includes only later errors.
The overall fixture export retains all global errors.

RED command:

```bash
node --test frontend/src/utils/invisibleStreamingFixture.test.js
```

Result: 19 tests, 16 pass, 3 expected failures. Log: `/tmp/task3-round1-red.log`.
The regressions exercise deferred startup, failed startup, stale/missing visual content, stale component content, and disconnected rendering.

GREEN uses the same command: 19 tests, 19 pass, 0 fail. Log: `/tmp/task3-round1-green.log`.
Both `node --check` commands and `git diff --check` pass again.
The earlier 155 targeted and 1,577 full results describe the pre-correction commit.
No full-suite rerun follows this small fixture-only correction, as the controller instructs.
Production code and adapter generation stay unchanged.
Independent re-review closes all three findings. Browser revalidation appears above.

## Task 3 browser correction round 2

The controller observes an empty Codex mobile final component while canonical and raw final text comparisons pass.
The fixture emits lowercase `text` inside Codex `AgentMessage.content`.
The production `agentMessageText` parser accepts capitalized `Text` for that item type.
The fixture now uses `Text` for Codex history and final text replacements.
Reasoning keeps the existing correct `summary_text` type. Production parsers stay unchanged.

The regression executes source-extracted fixture `finalContent` through the actual production `agentMessageText` function.
It verifies complete history, final plain text, and fenced code reach the canonical rendering input.
It also checks reasoning retains its original representation.

```bash
node --test frontend/src/utils/invisibleStreamingFixture.test.js
node --check frontend/tests/browser/invisibleStreaming.js
node --check frontend/tests/browser/preparePublicationRateBaseline.mjs
git diff --check
```

RED: 20 tests, 19 pass, 1 expected failure. Log: `/tmp/task3-round2-red.log`.
GREEN: 20 tests, 20 pass, 0 fail. Log: `/tmp/task3-round2-green.log`.
Syntax and whitespace checks pass. No full-suite rerun follows this fixture-only correction.
The controller repeats affected Codex browser scenarios after the stable correction commit, as recorded above.
Earlier affected Codex history and replacement rendering observations use invalid fixture data and do not establish acceptance.

## Controller final verification

The controller reruns the complete frontend suite after both fixture correction commits:

```bash
npm --prefix frontend test
```

Result: **1,581 tests, 1,581 pass, 0 fail**, exit 0.
Log: `.superpowers/sdd/2026-10-02-streaming-publication-rate-implementation-plan/final-full-tests.log`.
An independent targeted buffer/rate/registry/store/fixture run also passes 85 tests before the last fixture correction.
The complete final suite supersedes earlier suite counts.

The implementation stays on `bugfix/stable-streaming-rows` in the existing worktree.
Main stays unchanged. Existing worktree servers stay running; no restart runs.

## Controller rulings

1. Use runtime variable paths for optional fixture adapters and test loading without adapters.
   Vite resolves missing literal imports even with `@vite-ignore`.
   Cost if wrong: baseline fixture loading needs rework. Production application code stays unchanged.
2. Correct fixture Codex `AgentMessage.content` blocks to canonical `Text` and test the actual parser.
   Malformed synthetic data invalidates rendering acceptance.
   Cost if wrong: test-source compatibility needs rework. Production parsers stay unchanged.
