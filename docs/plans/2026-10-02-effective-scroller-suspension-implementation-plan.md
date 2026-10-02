# Effective Scroller Suspension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Execute each task's test cycle before the next task.

**Goal:** Stop full-list geometry and height-cache maintenance during scroller suspension, then restore the latest list and saved anchor.

**Architecture:** Keep lazy live positions separate from the published suspended snapshot. Track item replacement in constant time and defer cache passes. A lifecycle generation owns asynchronous work and anchor retries.

**Tech Stack:** Vue 3 Composition API, existing VirtualScroller, node:test, deterministic RAF/timers, existing Vite SessionView fixture. No new dependency.

**Spec:** `docs/plans/2026-10-02-effective-scroller-suspension-spec.md`, commit `e56858ce`.

## Global constraints

- Work only in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
- Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&`.
- Preserve unrelated files and index entries. Commit only owned paths, with a descriptive body and current model trailer.
- Keep artifacts in English. Add no dependency, persistence, backend change, or migration.
- Keep the existing public API shape. `scrollToIndex()` returns `null` while suspended; preserve its active `Promise<void> | null | undefined` contract.
- Preserve active scrolling, hysteresis, estimated heights, smooth reveals, and native bottom-following behavior.
- Preserve five anchor candidates and the eight-frame restore bound.
- Do not change visibility detection or priority 2's registry. Do not freeze canonical session data.
- Retained rendered-window work and parent-owned asynchronous work remain outside cancellation.
- Do not start servers, install packages, merge, or change the main instance without user authorization.
- This plan authorizes no implementation. Wait for the user's next go after committing this plan.
- Report browser acceptance separately from deterministic checks. Do not claim the global freeze is fixed.

## Review focus

1. Replacement followed by suspend or resume before watcher flush must produce one coherent geometry generation. Tasks 1–2.
2. Manual suspension must revoke an older deferred resume without replacing saved anchors. Task 2.
3. Old RAF and Promise continuations must remain invalid after a rapid suspend/resume. Task 3.
4. Hidden height seeds must survive cleanup when their keys remain in the latest list. Task 2.
5. Final reconciliation followed by immediate hide and return must preserve reading position and bottom-following. Task 4.

## File map

| File | Responsibility |
|---|---|
| Modify `frontend/src/composables/useVirtualScroll.js` | Snapshot publication, deferred maintenance, lifecycle ownership |
| Modify `frontend/src/composables/useVirtualScroll.test.js` | Existing active regression suite; shared harness cleanup where needed |
| Create `frontend/src/composables/useVirtualScrollSuspension.test.js` | Controlled suspension, geometry, maintenance, and lifecycle tests |
| Modify `frontend/tests/browser/invisibleStreaming.js` | Reuse actual SessionView fixture for priority 3 scenarios |
| Modify `frontend/src/utils/invisibleStreamingFixture.test.js` | Verify fixture wiring and seeded scenarios |
| Create `docs/plans/2026-10-02-effective-scroller-suspension-execution.md` during implementation | Commands, measured work, browser results, review closure, limitations |

`VirtualScroller.vue` remains a source-audit target. Its existing invalidation request works through the modified composable API.
Do not modify `SessionItemsList.vue` parent-owned stream-swap cancellation or `renderedItems` freezing for this priority.

## Verification setup

Use the worktree's existing dependencies if available. Do not install packages to make checks work.
If dependencies remain absent, reuse the existing read-only resolver from priority 2:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --loader ./.superpowers/sdd/2026-10-02-invisible-stream-publications-implementation-plan/dependency-loader.mjs --test frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js frontend/src/stores/streamingRows.test.js
```

Call this command **SUSPENSION_TESTS** below. With worktree dependencies present, omit `--loader`.
If the ignored resolver is unavailable, recreate its read-only bare-import fallback in this plan's scratch directory.
Resolve missing packages from `/home/twidi/dev/twicc-poc/frontend/node_modules`; never write or link into either node_modules tree.
Record this limitation. Do not describe resolver-based checks as a normal installed worktree build.

## Task 1: Publish a coherent frozen geometry snapshot

**Files:** Modify `useVirtualScroll.js`; create `useVirtualScrollSuspension.test.js`.

**Interfaces:** Public refs retain their names and shapes: `positions`, `totalHeight`, `renderRange`, `visibleRange`, both spacers, `scrollTop`, `viewportHeight`, `suspended`.
Add private `livePositions`, `suspendedSnapshot`, and `updateRenderRange(posArray)`.
`updateRenderRange(posArray) -> void` owns existing hysteresis calculation and `previousRange`; both the effect and suspension capture call it.
No consumer outside the composable reads `livePositions`.

- [ ] **Step 1: Add RED geometry tests.** Use a shallow item ref and an itemKey counter inside a stopped-after-test Vue scope. Supply a fake container with independently controlled DOM height; reading scrollHeight must not construct geometry.

Required assertions:

```text
warm -> suspend -> reset key counter
60 times: replace one row in 2,000 rows -> nextTick -> read all geometry and getScrollAnchor
positions === snapshot.positions; visibleRange === snapshot.visibleRange
totalHeight, ranges, spacers, scrollTop, viewportHeight equal captured values
itemKey calls === 0  [after Task 2 completes; Task 1 separately detects geometry reads]
```

Separate geometry from cleanup in Task 1: test in-place mutations and height-cache seeds without array replacement, then use the full replacement assertion in Task 2.
Cover append, reorder, removal, empty list, and active replacement immediately followed by suspend.
Assert captured ranges/spacers derive from the exact captured positions and viewport values, before any queued range effect flush.
Cover active resize, scroll, and existing hysteresis behavior; suspension must not alter active buffer thresholds.

- [ ] **Step 2: Run SUSPENSION_TESTS.** Confirm failures reach geometry assertions, rather than missing imports or harness errors.
- [ ] **Step 3: Implement snapshot publication.** Rename the existing lazy mapper to `livePositions`; public `positions` selects snapshot or live geometry. Gate other public geometry fields before reading live dependencies.
- [ ] **Step 4: Extract `updateRenderRange(posArray)`.** Preserve the existing hysteresis algorithm. The range effect checks suspension before positions and empty handling. At suspension entry, resolve live positions once, reconcile the range synchronously, then capture every published geometry field and anchors before setting suspended.
- [ ] **Step 5: Preserve query semantics.** `getScrollAnchor()` reads snapshot positions and captured scrollTop while suspended. `syncScrollPosition()` returns without reading hidden DOM state. Keep `getScrollState()` raw DOM semantics.
- [ ] **Step 6: Run SUSPENSION_TESTS.** Geometry tests and all existing active tests pass. At this stage, replacement cleanup remains active; the total-key-count replacement test belongs to Task 2.
- [ ] **Step 7: Commit owned code and tests.** Suggested subject: `fix(scroller): freeze geometry during suspension`. Include a body explaining the snapshot and active behavior preserved.

## Task 2: Defer cache maintenance and preserve resume authorization

**Files:** Modify `useVirtualScroll.js` and `useVirtualScrollSuspension.test.js`.

**Interfaces:** Keep `invalidateZeroHeights()`, `suspend()`, `resume()`, and `updateViewportHeight(height)` signatures.
Add private `cleanupHeightCache(currentItems) -> void` and `flushDeferredMaintenance() -> void`.
Add private `enterSuspension(origin: 'manual' | 'automatic') -> void`.
Keep latest replacement identity, last-cleaned identity, and pending zero-height invalidation as constant-size state.

- [ ] **Step 1: Add RED maintenance tests.** Complete the 2,000-row/60-replacement test. Assert zero itemKey calls while suspended, including all geometry consumers.
- [ ] **Step 2: Add RED resume tests.** Cover replacement then immediate resume without nextTick, active replacement then immediate suspend, no subsequent item update, latest empty list, hidden seeds for surviving/removed keys, and repeated invalidation requests.
Separate itemKey counts: one `2,000`-key cleanup pass plus one `2,000`-key live geometry pass at resume when both are necessary. No hidden pass and no second queued cleanup.
Use dedicated counted height-cache traversal in the test harness to distinguish zero-height invalidation from geometry lookup; do not add a product public diagnostic API.
Seed test keys with a unique `suspension-test:` prefix. Temporarily wrap Map prototype `keys`, `values`, `entries`, and `Symbol.iterator` with iterator-preserving counters scoped to maps containing those keys. Preserve empty-map iteration and restore methods in test cleanup. Run these instrumented tests serially; do not count Vue's unrelated internal maps.
Use saved native methods for cache ownership detection and iterator delegation. Exclude classifier work from production-pass counts; never recursively classify through the wrapped methods.
Cover missing container, zero-height pending resume, automatic resume, manual takeover, and `suspend -> resume at zero height -> suspend -> positive measurement`.
Assert no recapture on repeated suspend and no automatic resume after authorization revocation.
- [ ] **Step 3: Run SUSPENSION_TESTS.** Confirm failures for hidden maintenance or resume ordering.
- [ ] **Step 4: Track replacement synchronously.** Add a constant-time `flush:'sync'` identity watch. Keep full cleanup in the existing post-flush path while active. Compare the current array with last-cleaned identity, so resume can consume pending work before that watcher runs.
- [ ] **Step 5: Defer zero-height invalidation.** While suspended, set one pending flag. On actual resume, remove absent keys and requested zero heights before requesting live positions. Preserve surviving seeds. Do not add in-place array cleanup semantics.
- [ ] **Step 6: Implement resume transition.** Keep hidden/missing resume lazy. Public `suspend()` delegates to `enterSuspension('manual')`; both zero-height automatic paths (`handleScroll` and `updateViewportHeight`) use `enterSuspension('automatic')`.
Only manual entry revokes `pendingResume` and `autoSuspended`, including repeated calls. Automatic entry sets/preserves automatic authorization. Capture geometry only on the first transition. Complete authorized visible resume, publish latest geometry and viewport, restore anchors, then release obsolete snapshot references.
- [ ] **Step 7: Add and pass anchor tests.** Preserve key-first candidates with independent offsets, removed-primary fallback, all-key removal, shorter-list fallback, empty clearing, intended scrollTop under DOM clamp, eight-frame retry, and explicit-scroll override. Ensure current published geometry sizes the DOM on the next Vue flush.
- [ ] **Step 8: Run SUSPENSION_TESTS.** Assert one latest geometry generation before new measurement updates. No stale heights or repeated queued cleanup.
- [ ] **Step 9: Commit owned files.** Suggested subject: `fix(scroller): defer hidden cache maintenance until resume`.

## Task 3: Cancel suspended asynchronous work without stale continuations

**Files:** Modify `useVirtualScroll.js`, `useVirtualScrollSuspension.test.js`, and existing test harness cleanup if necessary.

**Interfaces:** Keep existing scroll method signatures. `scrollToKey()` and `scrollToEdge()` remain `Promise<boolean>`.
Add private `lifecycleGeneration`, `disposeOwnedWork() -> void`, and a registry of pending height-stability waits.
`waitForHeightStability(ms, onHeightChange) -> Promise<void>` keeps its internal shape; callers check captured generation after awaiting it.

- [ ] **Step 1: Add RED cancellation tests.** Start nearest/non-nearest key reveals, an edge reveal, and a smooth reveal. Suspend during settling or smooth completion, then resume before continuation. Each async reveal returns false; no later list scan, callback, or scroll write occurs.
Deliver cancelled scroll RAF and anchor retry callbacks after a new lifecycle has a new handle. Assert the old callback does not clear that handle, mutate anchors, or write.
Deliver an old programmatic-reset timer while a newer smooth command owns the hold. Assert the hold remains owned by the new command.
Call every scroll command while suspended. Assert zero list scans, DOM writes, scheduled timers, and height-settle watchers; `scrollToIndex()` returns null.
- [ ] **Step 2: Add RED disposal tests.** Mount the composable in a minimal Vue component harness using Vue's existing custom renderer, then unmount it. Assert listener removal, canceled RAF/timers, stopped settle watchers, and resolved waits. An effectScope alone is insufficient.
- [ ] **Step 3: Run SUSPENSION_TESTS.** Confirm failures correspond to actual stale ownership, not a fake scheduler that silently drops canceled callbacks.
- [ ] **Step 4: Implement lifecycle cancellation.** Increment generation on first suspension and unmount. Cancel all owned handles, end smooth ownership, stop/resolve every settle wait, and release stored snapshots on unmount.
- [ ] **Step 5: Guard callbacks and async loops.** Check generation before clearing owned handles and before each post-await operation. Check suspension at command entry before list search or timers. Preserve existing active intent sequences and nearest no-write behavior.
- [ ] **Step 6: Guard legacy programmatic reset timers.** Use a separate programmatic ownership token plus lifecycle generation, so older timers cannot release a newer hold within the same lifecycle either.
- [ ] **Step 7: Run SUSPENSION_TESTS.** All suspension tests and existing active nearest, smooth, stream-swap height, and scroll-intent tests pass. Check explicit-scroll override still defeats anchor retries.
- [ ] **Step 8: Commit owned files.** Suggested subject: `fix(scroller): invalidate suspended scroll operations`.

## Task 4: Validate production integration and record acceptance

**Files:** Modify `frontend/tests/browser/invisibleStreaming.js` and `frontend/src/utils/invisibleStreamingFixture.test.js`; create the execution record.

**Interfaces:** Reuse the fixture's actual SessionView, RouterView/KeepAlive, dock tabs, controlled streaming events, and final JSONL reconciliation.
Add named scenario controls for large-history suspension, pending reveal then hide, and reconciliation then rapid hide/return.
Keep its fake sessions, read-only API isolation, and priority 2 checks. Never replace the fixture with the old row-only harness.

- [ ] **Step 1: Add RED fixture contract tests.** Assert production scroller access, actual KeepAlive/session switching, hidden Chat tab controls, final reconciliation, and mobile viewport scenario inputs. These tests check wiring; they do not prove browser scroll behavior.
- [ ] **Step 2: Extend the fixture controls.** Use seeded 2,000-row histories and 60 replacements. Keep one visible comparison scroller while the target remains suspended. Expose existing geometry readouts and command results through fixture diagnostics only.
- [ ] **Step 3: Run targeted tests.** Run SUSPENSION_TESTS plus `frontend/src/utils/invisibleStreamingFixture.test.js` using the same loader policy. Compile any changed SFC with the existing Vue compiler and run `git diff --check`.
- [ ] **Step 4: Run the full frontend suite once.** Use `npm --prefix frontend test` with available dependencies or the recorded read-only resolver. Compare failures with priority 2's known two missing-dependency file-read failures. Investigate every new failure.
- [ ] **Step 5: Review implementation independently.** Run an adversarial code review, correct concrete findings, and repeat closure review. Include spec coverage, hidden work, immediate transitions, ownership, and active behavior.
- [ ] **Step 6: Check server authorization.** Do not start servers while authorization is pending. If startup is authorized, use only `devctl.py start --fresh-providers` in this worktree. Record URLs and resolved provider homes. Do not install packages or run migrations manually.
- [ ] **Step 7: Execute browser acceptance when authorized.** Run identical seeded scenarios on baseline `e56858ce` and new code, with priority 2 held constant. The existing `?baseline=1` fixture targets priority 2 and is not this baseline.
Use a separate baseline checkout only after explicit user authorization for its creation and server startup. Copy the new fixture controls into that isolated checkout without modifying its baseline composable. Keep all seeded scenarios identical. If that authorization is unavailable, record the comparative browser checks as pending; do not substitute a partial module baseline or alter the main/worktree product sources temporarily.
For work counts, set temporary browser debugger logpoints once per pass, before iteration begins. In baseline `e56858ce`, use the `positions` mapper's `let top = 0`, the inline items watcher's `new Set(newItems.map(...))`, and `invalidateZeroHeights()` immediately before cache iteration.
In new code, use the corresponding `livePositions` entry, `cleanupHeightCache(currentItems)` before key-set creation, and the actual zero-height invalidation pass. Increment three separate console counters. Do not place a logpoint inside a per-item callback. Do not count rendered-window itemKey calls or use logpoint timings as latency measurements. Remove logpoints after counting.
Verify reading-anchor restoration, latest rows/text, bottom-following while near bottom, scroll-up stability, visible stream behavior, rapid reveal/hide, final reconciliation/hide/return, and repeated cycles at desktop and mobile widths.
Record geometry and cleanup passes during hidden updates and actual resume. Any browser failure remains open until corrected and rechecked.
- [ ] **Step 8: Record results and commit owned files.** Include exact commands, passed/failed counts, independent review closure, browser evidence, and remaining limitations. If startup lacks authorization, mark browser acceptance pending and implementation unvalidated in-browser; do not claim full acceptance.
Suggested subject: `test(scroller): cover suspension integration and acceptance`.

## Handoff boundary

The requested deliverables are this plan and its specification. Commit them after review, then stop.
Do not execute Task 1 or launch an implementation agent until the user gives the next go.

## Adversarial plan review

Internal reviewer: `effective_suspension_plan_review`. Initial review and closure examine the plan against the committed specification and actual source.

Resolved findings:

1. Separate manual and automatic entry explicitly; automatic callers must not revoke their own resume authorization.
2. Define executable private-cache instrumentation without adding a product diagnostic API.
3. Pin the priority 3 baseline to `e56858ce`; do not reuse priority 2's `?baseline=1` module selection.
4. Name baseline and implementation measurement boundaries separately, since the baseline cleanup is inline.

Closure reports no remaining blocker. The reviewer confirms API compatibility, resume ordering, cancellation ownership, and snapshot release.
No implementation, test execution, package installation, or server startup occurs during document preparation.
