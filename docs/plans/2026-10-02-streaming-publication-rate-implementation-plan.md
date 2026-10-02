# Refresh-independent Streaming Publication Rate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Execute each task's test cycle before the next task.

**Goal:** Limit regular visible publications to 30 Hz per block, with bounded catch-up and complete final text.

**Architecture:** Keep one RAF scheduler per buffer. Gate prefix publication with a monotonic interval and a pending-episode deadline. Preserve registry ownership and synchronous snapshots; guard callback reentry and same-slot replacement.

**Tech Stack:** Existing JavaScript buffer, Pinia store, Vue 3, node:test, and Vite full-conversation fixture. No new dependency.

**Spec:** `docs/plans/2026-10-02-streaming-publication-rate-spec.md`, commit `7a791255`.

**Status:** The user authorizes implementation with “let’s go”. Tasks 1–3 are implemented and reviewed.
The complete frontend suite passes 1,581 tests. Browser acceptance remains partial.
See `docs/plans/2026-10-02-streaming-publication-rate-execution.md` for evidence and limits.

## Global constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
- Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&`.
- Preserve user changes and unrelated index entries. Commit only task-owned paths.
- Write code, comments, tests, and documents in English.
- Set `PUBLICATION_INTERVAL_MS = 1000 / 30` and `MAX_DISPLAY_LAG_MS = 250`.
- Keep five-arrival estimation, 200 characters/second default, 100 ms smoothing clamp, and UTF-16 prefix semantics.
- Use `performance.now()` for arrival, interval, and lag decisions.
- Preserve public signatures, return values, exact generation ownership, and priorities 1–3.
- No dependency, user setting, backend event change, timer, shared scheduler, or adaptive publication rate.
- Keep immediate activation/bootstrap/explicit snapshots and active terminal flush as cap exceptions.
- Hidden feeds and hidden flush schedule no RAF and make no regular publication.
- Terminal flush excludes same-terminating-generation callback feeds; replacement-generation feeds remain supported.
- One pending RAF per buffer. Do not claim this policy reduces RAF wakeup count.
- Cap each block independently. Do not claim a global 30 Hz limit.
- Do not merge, restart servers, install packages, or change the main instance without user authorization.
- Existing worktree servers run on frontend 5175/backend 3502. Do not restart them for JS HMR changes.
- The user supplies the implementation go. Keep the completed implementation isolated in this worktree.

## Review focus

1. High-refresh skipped frames must not accumulate minimum-one-character progress. Task 1.
2. Continuous arrivals must not reset backlog age, and idle time must not create smoothing credit. Task 1.
3. A publication callback can suspend, replace, or feed a buffer; old scheduling must remain invalid. Tasks 1–2.
4. Immediate snapshots must preserve the next regular budget; extra consumers must not create extra budgets. Task 2.
5. Rate-only browser baseline must retain priorities 1–3 and use the same production conversation path. Task 3.

## File map

| File | Responsibility |
|---|---|
| Modify `frontend/src/utils/streamingBuffer.js` | Per-buffer cadence, lag deadline, scheduler ownership, terminal slot guard |
| Modify `frontend/src/utils/streamingBuffer.test.js` | Existing public lifecycle tests; adjust explicitly changed cadence expectations |
| Create `frontend/src/utils/streamingPublicationRate.test.js` | Deterministic cadence, backlog, and reentry tests |
| Modify `frontend/src/stores/streamingRows.test.js` | Actual store-action boundary tests for text, thinking, stop, retirement |
| Modify `frontend/tests/browser/invisibleStreaming.js` | Add rate-only selection, timed scenarios, and measurements |
| Create `frontend/tests/browser/preparePublicationRateBaseline.mjs` | Generate isolated temporary adapters from `1f7d16ef` |
| Modify `.gitignore` | Ignore only the two generated priority-4 adapter paths |
| Modify `frontend/src/utils/invisibleStreamingFixture.test.js` | Validate rate fixture selection, seed, and safe baseline preparation |
| Create `docs/plans/2026-10-02-streaming-publication-rate-execution.md` during implementation | Commands, review findings, test evidence, browser evidence, limitations |

`data.js` and `streamPublicationRegistry.js` are audit targets, not planned production modifications.
Do not change stop semantics, visibility eligibility, registry aggregation, or Markdown processing.
If an audit proves a necessary caller correction, record its trigger and obtain a scope decision before changing that caller.

## Verification setup

Worktree dependencies now exist because the user starts `devctl`.
Use direct Node commands. Do not use the previous read-only dependency resolver unless dependency resolution actually fails.

**RATE_TESTS:**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/utils/streamingBuffer.test.js frontend/src/utils/streamingPublicationRate.test.js
```

**STREAM_TESTS:**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/utils/streamingBuffer.test.js frontend/src/utils/streamingPublicationRate.test.js frontend/src/utils/streamPublicationRegistry.test.js frontend/src/stores/streamingRows.test.js frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js frontend/src/components/session/detail/SessionItemsList.resume.test.js frontend/src/utils/invisibleStreamingFixture.test.js
```

**FULL_TESTS:**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && npm --prefix frontend test
```

Run the relevant command after each implementation task. Run FULL_TESTS once after all task changes pass.
Record actual counts and failures. Do not reuse older counts as current results.
No Python or backend check is needed for this frontend-only change.

## Task 1: Gate regular publications and bound display lag

**Files:** Modify `streamingBuffer.js`; create `streamingPublicationRate.test.js`; adjust `streamingBuffer.test.js` only for intentional cadence changes.

**Consumes:** Existing `initBuffer`, `feedDelta`, `setBufferActive`, `snapshotBuffer`, and destroy helpers.
Use `{ visibilityManaged: false, active: true, messageId: 'message' }` for standalone buffer tests.

**Produces:** No new public API. Private state: `lastPublicationTime`, `lastAdvanceTime`, `pendingSince`, and `lifecycleGeneration`.
Keep existing request token ownership. Private `_onFrame() -> void` samples `performance.now()`; do not use the RAF timestamp.
`cancel() -> void` revokes lifecycle ownership as well as the existing RAF token.
`resetRate() -> void` clears arrival history, fractional credit, advance time, and first-delta state; it does not clear publication time.

- [x] **Step 1: Create the deterministic harness.** Mock `performance.now`, RAF, and cancelRAF. Capture callback execution times, callback registrations, and publication times/text.

Provide these test-local interfaces:

```text
feed(text, atMs) -> void              // set now, feed through public API
frame(atMs) -> void                  // execute the handles pending before this frame exactly once
advanceFrames(fromMs, throughMs, hz) -> void
invokeCanceled(handle, atMs) -> void  // adversarial callback delivery
publications -> [{ time, text }]
```

`frame` must batch all pending handles before invocation. Handles registered during a callback wait until the next frame.
Do not drain callbacks repeatedly at the same timestamp.
`advanceFrames` uses a fixed time origin and evenly spaced opportunities; it never moves monotonic time backwards.
Retain canceled callbacks independently from pending handles.
Restore globals and destroy all buffers in finally blocks. Tests changing globals run serially.

- [x] **Step 2: Add RED cadence tests.** Parameterize 60, 90, 120, 144, and 240 Hz.

Feed a persistent large backlog, then continue large arrivals so the buffer does not become idle during the count interval.
Measure regular publications in `[0, 1000)` and assert:

```text
every adjacent publication gap >= 1000 / 30 - assertionTolerance
count <= ceil(1000 / (1000 / 30))
each callback text strictly extends its previous prefix
final flush returns the exact concatenation of feeds
```

Use assertion tolerance `1e-7` ms only for comparisons. Do not add a production early-release tolerance.
Classify final flush separately, outside the regular count interval.
Test first publication at the next RAF when no preceding publication owns a budget.
After a publication at t, callbacks at t+10 and t+20 must not publish or advance text.
At t+34, progression uses elapsed time since the last eligible advance, not just that frame's delta.
Compare traces with added skipped RAF opportunities at the same feed and eligible timestamps.
The published texts must be identical. This proves skipped frames do not apply minimum progress or consume credit.

- [x] **Step 3: Add RED lag and idle tests.** Feed 10,000 code units once, without stop or flush.
For each simulated Hz, the complete prefix appears by `250 + 1000 / 30 + 1000 / hz` ms.
The catch-up callback itself satisfies the interval assertion.
Feed small deltas every 10 ms during an unfinished episode; the first full catch-up includes all text received at that instant.
Assert catch-up still occurs within the episode bound despite continuous arrivals.
After a 2,000 ms frame gap, the next eligible frame emits one full prefix and schedules no replay of old frames.
After caught-up idle, feed a small burst; its first prefix reflects new arrival time, not idle duration.
Test low arrival rate, multiple fractional-credit publications, and a fully empty delta without scheduling.

- [x] **Step 4: Run RATE_TESTS.** Confirm RED failures concern excessive publication, reset pending age, or unbounded backlog. Fix harness failures before product changes.
- [x] **Step 5: Implement interval and episode state.** Reject empty feeds before history or scheduling work.
On transition from fully caught up to pending active text, set pendingSince and lastAdvanceTime to arrival time.
Preserve pendingSince during further feeds and partial drains.
At each owned RAF, consume its pending handle and perform the interval gate before prefix computation.
If too early, schedule one successor RAF and return without progress or fractional changes.
At eligibility, apply full catch-up if pending age reaches 250 ms. Otherwise integrate smoothing elapsed, clamped to 100 ms.
Set displayed length and publication time before callback. Clear pendingSince on full progress.
Deadline catch-up also resets estimation before callback. Record actual callback time for every publication.
If more text remains, schedule one successor only under the same lifecycle and only if reentry has not already scheduled it.
- [x] **Step 6: Add and pass normal-callback reentry tests.** From onDrain, separately suspend, destroy, replace the same slot, snapshot, and feed more text.
Deliver old canceled callbacks after a replacement or resume; they must not clear the new pending handle or publish old text.
For reentrant feed, verify concatenated text survives, one handle remains, pending age starts correctly after full progress, and later flush completes it.
Use lifecycle ownership, not only a token cleared before callback, to validate post-callback continuation.
- [x] **Step 7: Run RATE_TESTS.** Existing lifecycle assertions still pass.
Update the old resume test that expects progress 16 ms after an immediate snapshot: require silence before the gate, then the correct growing prefix after eligibility.
Do not weaken complete-text or canceled-callback assertions.
- [x] **Step 8: Commit owned buffer and test files.** Suggested subject: `perf(streaming): cap regular publications and bound display lag`.
Use a descriptive body and the exact current model trailer.

## Task 2: Preserve immediate snapshots, replacement ownership, and store behavior

**Files:** Modify `streamingBuffer.js`, `streamingPublicationRate.test.js`, `streamingBuffer.test.js`, and `streamingRows.test.js`.

**Consumes:** Task 1's interval, pending-episode, and lifecycle state.

**Produces:** Existing `snapshot(publish = true)`, `setActive(active)`, `flush()`, and public `flushBuffer(sessionId, blockIndex)` contracts.
No new public function or parameter. The registry still supplies the sole managed active state.

- [x] **Step 1: Add RED immediate-boundary tests.** Inside an unexpired interval, activation, explicit snapshot, and visible terminal flush immediately publish complete text once.
For activation, first suspend, feed hidden text, then resume. Hidden feeds and hidden flush publish nothing and leave no RAF.
An immediate snapshot at t makes regular RAF at t+16 silent; the first eligible RAF can progress new text.
An already-complete snapshot publishes nothing. All snapshots cancel old handles and reset pending episode/rate state.
Canceled callbacks remain invalid after snapshot and reactivation.
- [x] **Step 2: Add RED ownership and replacement tests.** A second eligible registry consumer shares the existing buffer budget and does not force a snapshot.
Distinct blocks in the same session publish independently; one block's snapshot never resets the other's budget.
During the flush callback, initialize a replacement at the same session/block slot, acquire its exact generation owner, and feed it.
After old flush cleanup, the new buffer remains discoverable and publishes its own text. Its full flush returns only its own generation's text.
Cover reentrant activation-snapshot and explicit-snapshot callbacks that feed, suspend, or replace.
Do not promise preservation of feeds into the generation already undergoing terminal flush.
- [x] **Step 3: Run RATE_TESTS.** The same-slot flush replacement test must fail against unconditional map deletion.
- [x] **Step 4: Finish snapshot state ownership.** Cancel/revoke before resetting state. Commit displayed length and timing before calling onDrain.
Keep lastPublicationTime across rate reset; set it only for an actual publication.
Preserve silent hidden flush and synchronous complete activation/bootstrap.
After callback, do not overwrite state initialized by reentrant feed or replacement.
- [x] **Step 5: Guard terminal map cleanup.** Capture the old buffer in `flushBuffer`.
Flush and destroy that captured buffer. Delete the slot only when `buffers.get(k) === buf`.
Old destroy must release only the old registry binding; it must not deactivate or cancel replacement ownership.
Preserve current flush return semantics and missing-buffer null return.
- [x] **Step 6: Migrate the store harness and add boundary tests.** Update existing `streamingRows.test.js::withFrames` before adding rate assertions.
Mock, advance, and restore `globalThis.performance` alongside RAF/cancelRAF. Its `now()` returns the controlled monotonic time.
Each drain opportunity advances time once, then executes the handles captured before that opportunity as a batch.
New handles wait until the next opportunity. Retain the bounded drain guard.
Run the existing reused-message replacement test without weakening its assertions; it must complete under the controlled clock.
Restore globals and destroy buffers in finally, including failed assertions.
Extend the actual source-extracted Pinia actions; do not copy actions into a fake implementation.
Use a managed registry owner and fake time/RAF. Cover both text and thinking.
Assert canonical block.text contains every feed even when prefixes are capped.
Assert row/list/cache identity stays stable across content-only publications.
Call streamBlockStop without retirement; pending text completes within the scheduling bound.
Exercise Claude uuid retirement and Codex stream_uuid retirement inside the publication interval.
Assert visible final parsed text is complete, old buffer is destroyed, and existing thinking expansion transfer remains correct.
Cover hidden retirement: retained visual row stays unchanged and canonical item supplies complete text.
Use the existing explicit fixture structural replacement; do not claim it proves full Vue reconciliation.
- [x] **Step 7: Run STREAM_TESTS.** All cadence, ownership, stable-row, and scroller regression tests pass.
- [x] **Step 8: Commit owned files.** Suggested subject: `fix(streaming): preserve snapshot and replacement lifecycle under rate caps`.

## Task 3: Add a rate-only browser comparison and record acceptance

**Files:** Modify `invisibleStreaming.js`, `invisibleStreamingFixture.test.js`, and `.gitignore`; create `preparePublicationRateBaseline.mjs` and the execution report.

**Consumes:** The existing actual SessionView fixture; completed Tasks 1–2 buffer policy.

**Produces:** Query option `publicationRateBaseline=1`, mutually exclusive with existing `baseline=1`.
Select `destroyFixtureSessionBuffers(sessionId) -> void` from `publicationRateBaselineBuffer.js` in rate-baseline mode; otherwise use the existing production cleanup.
Route every fixture cleanup call through this selected function, including hidden scenarios, reconciliation, and exposed clear().
Do not leave a baseline map or RAF alive after its store state is cleared.
Expose `window.invisibleStreamingFixture.runPublicationRateScenario({ blockType, sourceKind }) -> Promise<Report>`.
`blockType` is `text` or `thinking`. `sourceKind` is `plain` or `code`.
Report records feed source, complete canonical/displayed text equality, publication times, exception labels, executed buffer RAF count, parsed-envelope count, geometry, and errors.

- [x] **Step 1: Add RED adapter and fixture tests.** Verify rate baseline selects a data adapter before creating the shared Pinia store.
Reject both baseline query options together with an explicit fixture error.
Test mode-specific cleanup with independent production/baseline buffer stubs: all rate-baseline cleanup reaches the baseline map, clears its pending handles, and preserves production buffers.
Verify all existing fixture cleanup call sites use the selected function; repeated rate scenarios must start with no old pending buffer work.
Validate baseline source pin `1f7d16ef`, exact guarded worktree root, and the expected data.js buffer import replacement.
Tests must exercise adapter preparation in an isolated temporary directory with injected file/git operations, rather than write real adapter files.
Assert exclusive creation refuses existing files and rollback removes only files successfully created by that invocation.
Assert --remove refuses mismatched contents. This prevents fixture cleanup from deleting another developer's file.
Do not replace production buffer exports globally.
- [x] **Step 2: Implement isolated adapter preparation.** Generate these ignored files:

```text
frontend/src/stores/publicationRateBaselineData.js
frontend/src/utils/publicationRateBaselineBuffer.js
```

Read both originals from `1f7d16ef` with `execFileSync('git', ['show', ...])`.
Change only the generated data module's `../utils/streamingBuffer` import to `../utils/publicationRateBaselineBuffer`.
Leave its registry import and all store actions unchanged. The old buffer still uses the current equivalent registry.
The generated data store retains the production Pinia ID. Select it before any component obtains that store.
At the same selection boundary, load its matching buffer cleanup function and bind `destroyFixtureSessionBuffers`.
Replace every fixture session-buffer cleanup call with that selected function; do not mutate module exports.
Require exact worktree root. Use exclusive writes and transactional cleanup of newly created files if preparation fails.
Add these exact root `.gitignore` entries; never commit generated modules:
`/frontend/src/stores/publicationRateBaselineData.js` and `/frontend/src/utils/publicationRateBaselineBuffer.js`.
For --remove, validate exact expected generated contents before deletion; preserve unknown files and explain the conflict.
Keep existing `prepareInvisibleStreamingBaseline.mjs` and priority-2 adapters unchanged.
- [x] **Step 3: Add timed scenario controls.** Use a fixed source string and timed feed schedule, identical between modes.
Suggested trace: feed 200 characters every 20 ms for 2 seconds; observe remaining backlog for 400 ms; then retire.
No timestamp rebasing or faster feed is allowed in one mode. Record actual delivery times and actual RAF opportunities.
Use the same registry ownership, active session, viewport, source, and visible consumers.
Open thinking explicitly through the existing production UI before feed.
Measure changed displayed-prefix publications before terminal retirement separately from immediate snapshots.
Wrap the fixture store's _onBufferDrain to capture actual publication times and displayed text.
Classify activation/bootstrap/explicit snapshot/terminal boundaries through scenario phase markers, not an assumed 30 Hz threshold.
Count executed buffer RAF callbacks; the current fixture's registration counter alone is not an execution counter.
Recognize both production `streamingBuffer.js` and generated `publicationRateBaselineBuffer.js` callback registrations.
Wrap the registered callback to count execution, including skipped frames; use identical instrumentation in both modes.
Count parsed-envelope changes with the existing instrumentation and keep all prior suspension scenarios intact.
- [x] **Step 4: Add fixture regressions and run STREAM_TESTS.** Validate query routing, source schedule, diagnostics, and full production component wiring.
These are fixture preparation tests, not executed browser acceptance.
Run `node --check` on the adapter script and browser fixture, then `git diff --check`.
- [ ] **Step 5: Run browser acceptance on the existing worktree server when browser tooling is available.** Partial execution remains inconclusive; see the execution record. Prepare adapters with:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node frontend/tests/browser/preparePublicationRateBaseline.mjs
```

Use `/tests/browser/invisibleStreaming.html?provider=claude_code` and the same URL with `&publicationRateBaseline=1`.
Repeat with `provider=codex`.
The local origin is `http://localhost:5175`. No server restart is required.
Compare plain text, fenced code, and open thinking in desktop and mobile viewports.
Run route hide/return and real-item retirement while reading above the bottom, then while following the bottom.
Verify composer typing, session switch responsiveness, complete final text, reading anchor, and bottom-following.
Record screenshots or logs and timing/count reports. A 60 Hz browser is adequate for product checks; deterministic tests cover high refresh.
If browser tooling is unavailable, record exact pending scenarios and do not call them passed.
- [x] **Step 6: Remove generated adapters safely.** Run the same preparation script with `--remove` after closing comparison tabs.
Confirm no generated adapter enters the index. Preserve unknown files if contents differ.
- [x] **Step 7: Run FULL_TESTS and whole-change adversarial review.** Resolve all significant findings, rerun affected tests, then record final counts and review closure.
Use an independent reviewer. Give the reviewer the spec, plan, implementation diff, and actual verification evidence.
Broaden tests only when changed behavior or a finding requires it.
- [x] **Step 8: Commit fixture and execution report.** Suggested subject: `test(streaming): validate refresh-independent publication cadence`.
Include current browser status. Do not claim the global freeze is fixed or all browser checks pass if any remain pending.

## Completion evidence

The execution report must distinguish deterministic rate proof, store-action integration, fixture preparation, and browser observations.
Record per-block publication gaps, backlog completion times, exception counts, final text equality, and exact commands.
Report the expected limits: RAF wakeups remain, concurrent blocks multiply the aggregate rate, and large catch-up can cause a costly render.
The initial 30 Hz/250 ms values remain product choices requiring browser evaluation.

## Plan self-review

Spec coverage maps regular cadence, lag, idle, and normal reentry to Task 1.
Immediate snapshots, terminal boundaries, consumers, store lifecycle, and final text map to Task 2.
Rate-only baseline, fixture measurement, browser acceptance, and reporting map to Task 3.
All five review-focus conditions have corresponding tests.
Public interfaces remain unchanged. Generated files remain separate from production files.

## Adversarial review record

Independent adversarial review identifies two Important verification/integration gaps.
Task 2 now migrates the existing store harness to the same controlled monotonic clock and batched RAF model.
Task 3 now selects matching baseline-buffer cleanup at every fixture cleanup boundary.
Scoped re-review approves both corrections. No significant finding remains open.
This verdict covers the plan only. Implementation and product validation have not started.
