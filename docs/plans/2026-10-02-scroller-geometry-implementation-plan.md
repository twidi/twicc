# Limit Scroller Geometry Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for delegated execution, or superpowers:executing-plans for inline execution. Execute each task's test cycle before the next task.

**Goal:** Reuse valid position prefixes and stable ranges without losing loading or scroll behavior.

**Architecture:** A local geometry cache reconciles lazy ordered keys and explicit dirty height records. Stable range publication and deduplicated notifications separate geometry from consumer loading triggers.

**Tech stack:** Existing Vue 3, node:test, custom VirtualScroller, and Vite. No dependency change.

**Spec:** `docs/plans/2026-10-02-scroller-geometry-spec.md`, commit `56a1600a`.

**Status:** Planning only. The user authorizes review and commit of this plan. Stop afterward; implementation requires a later go.

## Global constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
- Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&`.
- Preserve unrelated changes. Commit task-owned files only, with descriptive Conventional Commit bodies and the current model trailer.
- English artifacts. No merge, main edit, install, backend change, or server restart without separate authorization.
- Priority 6 is not approved. Preserve progressive Markdown, code highlighting, and diagrams.
- Preserve `{ index, key, top, height }` entries and exact cumulative heights.
- Never mutate published position arrays or entries. Retain valid prefix entries by reference.
- Height-only changes avoid full key extraction, full height scans, and prefix entry construction.
- Array reference copying and structural reconciliation may remain O(N). Do not promise total O(suffix) runtime.
- All height writers participate in invalidation. Preserve seeds for future replacement keys.
- Hidden changes cause no geometry construction or key extraction. Preserve frozen snapshots and resume ordering.
- Preserve binary-search boundaries, exclusive range ends, buffers, hysteresis, native anchoring, and explicit-scroll ownership.
- Public update payload remains the same four indices. Keep initial/recovery events; suppress equal normal tuples.
- Preserve loading through independent bounded-window, membership, availability, settlement, and recovery triggers.
- Do not create duplicate concurrent gap requests, hidden delayed loads, stale scroll correction, or retry loops.
- Pagination uses active measured scope ownership and existing cursor/canonical-ID progress, not filtered length alone.
- Use `hasContent()` for content availability. Do not read raw item content or parsed-content internals.
- No global scheduler, tree index, new registry, new polling, or new automatic retry policy.

## Review focus

1. Prefix dependencies lost after a suffix rebuild: a later head resize must still update all later positions. Tasks 1–2.
2. Same-index placeholder replacement after stable range suppression: content must still load. Task 3.
3. Future-key seed followed by immediate suspend/resume before post cleanup: preserve height and frozen snapshots. Task 2.
4. In-flight load completes after hiding or changing project/filter: store data survives, obsolete view work stops. Task 3.
5. Filtered pages advance a cursor without increasing visible length: continue only with current viewport ownership and real progress. Task 3.

## File map

| Path | Responsibility |
| --- | --- |
| `frontend/src/utils/virtualScrollGeometry.js` (new) | Pure dirty-index position cache and structural reconciliation |
| `frontend/src/utils/virtualScrollGeometry.test.js` (new) | Oracle, identity, counts, duplicate keys, and deferred invalidation |
| `frontend/src/composables/useVirtualScroll.js` | Lazy ordered keys, complete height-write ownership, and stable ranges |
| `frontend/src/composables/useVirtualScrollGeometry.test.js` (new) | Real Vue composable integration and height-writer proofs |
| `frontend/src/composables/useVirtualScroll.test.js` | Preserve and extend existing reveal/anchor tests where relevant |
| `frontend/src/composables/useVirtualScrollSuspension.test.js` | Hidden work, seeds, immediate resume, and frozen snapshots |
| `frontend/src/utils/virtualScrollRangeUpdates.js` (new) | Four-index event admission and initial/recovery deduplication |
| `frontend/src/utils/virtualScrollRangeUpdates.test.js` (new) | Event ownership and tuple traces |
| `frontend/src/utils/scrollerLoadWindow.js` (new) | Missing-line collection, candidate identity, and pagination progress comparison |
| `frontend/src/utils/scrollerLoadWindow.test.js` (new) | Bounded windows, separators, partial progress, and cursor progress |
| `frontend/src/components/virtual-scroller/VirtualScroller.vue` | Admit current measured range notifications and recovery |
| `frontend/src/components/virtual-scroller/VirtualScroller.update.test.js` (new) | Execute actual event wiring with real Vue reactivity |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Independent bounded gap loading and async ownership |
| `frontend/src/components/session/detail/SessionItemsList.loading.test.js` (new) | Execute actual consumer loading functions/watch wiring |
| `frontend/src/components/session/list/SessionList.vue` | Current-scope pagination settlement and progress guards |
| `frontend/src/components/session/list/SessionList.loading.test.js` (new) | Filtered pages, active scope, and explicit retry |
| `frontend/tests/browser/scrollerGeometry.html` and `.js` (new) | Isolated real composable comparison and operation evidence |
| `frontend/tests/browser/scrollerGeometryConversation.html` and `.js` (new) | Reuse guarded production conversation fixture for geometry acceptance |
| `frontend/tests/browser/prepareScrollerGeometryBaseline.mjs` and `.test.js` (new) | Exclusive pinned baseline copy and guarded cleanup |
| `frontend/tests/browser/scrollerGeometryFixture.test.js` (new) | Actual source schedule, readiness, and independent output assertions |
| `.gitignore` | Ignore only the generated baseline composable and its ownership manifest |
| `docs/plans/2026-10-02-scroller-geometry-execution.md` (new) | Tests, reviews, browser observations, and remaining limits |

No store or transport change is planned. Existing store scope/pagination state supplies the required observations.

## Internal interfaces

### Geometry cache

`createVirtualScrollGeometry({ minItemHeight })` returns:

- `invalidateKey(key)`: retain the earliest affected current index; unknown keys do not affect unrelated geometry.
- `reconcile(keys, readHeight)`: return an immutable published position array; consume accumulated invalidation.

`keys` is an immutable ordered-key snapshot. `readHeight(key)` returns cached height or undefined.
The same ordered-key snapshot permits the height-only path without a key scan.
A new snapshot reconciles the first key mismatch and rebuilds the key-index map.
Equal effective geometry may return the previous position array.
Duplicate keys use conservative full reconstruction; they must preserve all existing equations.
Reconciliation reads heights only for the reconstructed suffix on ordinary unique-key height updates.
Clear/delete writers identify affected effective keys before discarding cache values.
The cache does not own Vue, timers, height measurements, or scroll effects.

The composable owns a lazy ordered-key computed and an explicit geometry revision.
The ordered-key computed tracks array topology, `triggerRef`, and reactive fields used by `itemKey`.
Compare ordered values before publishing a new key snapshot. Same-key content replacement may retain it.
Do not eagerly read this computed during hidden replacement handling.
Each accepted effective-height mutation records dirty ownership before invalidating geometry consumers.
Raw Map mutation still preserves existing height-stability watchers and measurement events.

### Event admission

`createVirtualScrollRangeUpdates()` returns `admit(payload, { reason, epoch }) -> boolean`.
`reason` is `normal`, `initial`, or `recovery`; `epoch` identifies the mounted/active lifecycle generation.
Normal admission compares the exact four-index tuple against the last published tuple.
Initial/recovery admission permits one event for its current measured epoch, including an unchanged tuple.
Each real initial/recovery epoch starts with one pending lifecycle obligation after measurement.
The first valid publication in that epoch fulfills this obligation, regardless of its reason.
A normal watcher therefore admits an equal tuple when this obligation remains pending.
That publication records the tuple and consumes the obligation together.
A later lifecycle callback cannot force another event; equal tuples are rejected.
If the lifecycle callback runs first, the following equal normal watcher is rejected instead.
After fulfillment, genuinely changed tuples still emit through normal tuple comparison.
Mount activation belongs to the initial epoch; it does not create an additional recovery obligation.
The component owns lifecycle checks and emits the original payload only after admission.
A normal watcher and deferred lifecycle callback cannot both publish the same transition.
No event helper reads item content or requests network data.

### Loading decisions

- `collectMissingScrollerLines(visualItems, { start, end }, buffer) -> lineNums`: preserve the existing conservative inclusive loop and separator exclusion.
- `sameScrollerLoadCandidates(left, right) -> boolean`: compare ordered candidate lines, without serializing source content.
- `hasScrollerPaginationProgress(before, after) -> boolean`: compare existing cursor and canonical scope ID sets; filtered display length is not progress.

Use `store.localState.projects[scope].oldestSessionMtime` as the existing pagination cursor.
Snapshot canonical session IDs for the existing project/workspace/all-project scope, before display filters.

Consumers own mounted/active/measured state, current scope generation, latest range, pending debounce, and in-flight ownership.
Use existing `scrollerRef.suspended`, measured scroll state, component lifecycle, and current view props.
Do not introduce a generic global loading coordinator.

SessionList keeps a measurable VirtualScroller mounted while its active scope has unfinished pagination, including zero displayed rows.
Use a stable viewport wrapper and an empty-state sibling/overlay; preserve viewport dimensions and scroll ownership.
Do not let the current empty-state `v-if` branches remove this viewport between filtered pages.
After exhaustion, preserve the existing final empty-state UI.
Error/no-progress stops chaining and preserves the relevant empty-state/retry UI without an automatic retry.
Hidden/unmounted ownership still blocks every next-page decision, even if the viewport remains mounted.

## Verification commands

Use the existing installed dependency tree. Do not install dependencies to run this plan.

| Name | Command after the required worktree prefix |
| --- | --- |
| GEOMETRY | `node --test frontend/src/utils/virtualScrollGeometry.test.js frontend/src/composables/useVirtualScrollGeometry.test.js` |
| SCROLL | `node --test frontend/src/composables/useVirtualScroll.test.js frontend/src/composables/useVirtualScrollSuspension.test.js` |
| EVENTS_LOADING | `node --test frontend/src/utils/virtualScrollRangeUpdates.test.js frontend/src/utils/scrollerLoadWindow.test.js frontend/src/components/virtual-scroller/VirtualScroller.update.test.js frontend/src/components/session/detail/SessionItemsList.loading.test.js frontend/src/components/session/list/SessionList.loading.test.js` |
| FIXTURE | `node --test frontend/tests/browser/scrollerGeometryFixture.test.js frontend/tests/browser/prepareScrollerGeometryBaseline.test.js` |
| FULL | `npm --prefix frontend test` |
| BUILD | `npm --prefix frontend run build` |
| WHITESPACE | `git diff --check` |

## Task 1: Prove and implement immutable suffix geometry

**Files:** geometry utility and its tests.
**Produces:** `createVirtualScrollGeometry` interface above. No composable integration yet.

- [ ] Step 1: Add deferred dirty-key and full-oracle tests before implementation.
  Cover empty/initial rows, append/prepend/insert/delete/truncate/reorder/same-length replacement, duplicate keys, and identical key snapshots.
  Compare every entry and total against a simple full-recompute oracle after mixed structural/height traces.
- [ ] Step 2: Add count/identity tests for 1,000 unique keys with index 799 changed.
  Rebuild exactly 201 suffix entries; preserve 799 prefix entries by identity; read 201 heights and no prefix heights.
  Retain the old array and every old entry unchanged. Opposite height changes cannot cancel invalidation through equal sums.
- [ ] Step 3: Add first measurement equal to estimate, unknown future seed, clear/delete-effective change, batched minimum, and later-prefix invalidation cases.
  Unknown invalidation does not rebuild unrelated current geometry. A later inserted future key reads its preserved seed.
- [ ] Step 4: Run the utility test file and record meaningful RED failures.
- [ ] Step 5: Implement the geometry interface with current key lookup, first structural mismatch, and immutable suffix publication.
  Reference copying is permitted and reported; avoid hashing entire geometry or scanning all keys on the height-only path.
- [ ] Step 6: Run the utility tests and record GREEN. Self-review all mutation/publication ownership.
- [ ] Step 7: Obtain scoped review and commit owned files: `perf(scroller): reuse unchanged position prefixes`.

## Task 2: Integrate all writers and stable reactive ranges

**Files:** composable, new integration test, existing scroll/suspension tests.
**Consumes:** Task 1 geometry interface.
**Produces:** existing composable API with complete invalidation and stable range identity.

- [ ] Step 1: Build tests around the actual composable with real Vue refs/effect scopes and existing controlled containers.
  Count `itemKey` calls: after initial reconciliation, a unique-key height-only update reads zero item keys.
  Compare entries/total/spacers with the oracle and retained snapshot identities.
- [ ] Step 2: Cover every `heightCache.set/delete/clear` branch found in the source audit.
  Include no container, programmatic scrolling, unknown future seeds, equal estimates, zero cleanup, deferred cleanup, and legitimate shrink after a height floor.
- [ ] Step 3: Cover a suffix update followed by a head update, multiple writes before read, and immediate anchor correction.
  Prove prefix invalidation remains reactive and synchronous post-batch reads contain all accepted heights.
- [ ] Step 4: Cover equal render/visible indices retaining separate object identities, then actual scroll/resize/topology changes publishing correct ranges.
  Preserve empty ranges, exclusive ends, search boundaries, and hysteresis.
- [ ] Step 5: Cover hidden replacements/seeds with zero key extraction and zero geometry construction.
  Include same-reference mutation plus `triggerRef`, reactive key changes, immediate resume before post cleanup, and suspend immediately after active replacement.
  Retained arrays/snapshots cannot change after resumed reconciliation.
- [ ] Step 6: Run GEOMETRY and SCROLL; record RED before integrating.
- [ ] Step 7: Integrate lazy ordered keys and explicit height revision. Route every writer through local invalidation ownership.
  Keep active cleanup and resume ordering. Reuse equal range objects only after reading required reactive inputs.
- [ ] Step 8: Run GEOMETRY and SCROLL; record GREEN. Inspect all remaining cache mutation calls.
- [ ] Step 9: Obtain scoped review and commit: `perf(scroller): stabilize geometry and range publication`.

## Task 3: Deduplicate events without starving either consumer

**Files:** event/loading utilities, VirtualScroller, both consumers, and actual-function/watcher tests.
**Consumes:** stable ranges and existing exposed scroller API from Task 2.
**Produces:** unchanged public payload and independent lifecycle-owned loading opportunities.

- [ ] Step 1: Add event admission tests and actual VirtualScroller wiring tests.
  Initial measured notification occurs once; real recovery occurs once with equal bounds; normal duplicate tuples never emit.
  Changed visible-only/render-only tuples emit. Deferred old-epoch callbacks cannot notify a new epoch.
  Trace both normal-before-lifecycle and lifecycle-before-normal orders for initial, equal recovery, and changed recovery tuples.
  Test mount activation without a second initial event, then a later changed tuple in the same epoch.
- [ ] Step 2: Add bounded missing-line tests using `hasContent`, real visual item shapes, separators, and existing inclusive load boundaries.
  Execute actual SessionItemsList loading functions/watch wiring; do not duplicate its algorithm in the test.
  Equal-index placeholder replacement and content removal trigger loading. Geometry-only churn does not scan or rearm its debounce.
- [ ] Step 3: Add deferred conversation fetch tests.
  Partial progress reconciles remaining candidates. Same gap coalesces while a request runs.
  Failure/no-progress does not self-loop; later real scroll, membership, or activation can retry with unchanged bounds.
  Hide/unmount before debounce prevents start. Hide/scope change during await prevents obsolete scroll correction or follow-up.
- [ ] Step 4: Add actual SessionList tests for short pages, filtered pages with cursor advance, empty/no-progress pages, failures, and explicit retry.
  Capture existing cursor and unfiltered canonical scope IDs, using current store scope membership rules.
  Defer before initial measurement. Hide/unmount/project-filter changes revoke chaining; current activation permits reevaluation.
  Execute the actual empty-state template/component branch with zero displayed rows and hasMore true.
  An advancing filtered first page must retain a measurable viewport, then load a later matching page.
  Cover allSessions-empty initial loading, exhaustion, error/no-progress UI, and hidden/unmounted filtered-page settlement.
  Do not prove these cases with a permanently injected fake scroller reference.
  Cover sessionsLoading becoming false before the store removes its finishing in-flight request.
  Reconcile after the current request promise settles; joining that finishing request cannot latch false no-progress.
- [ ] Step 5: Run EVENTS_LOADING and record RED.
- [ ] Step 6: Implement admission plus both consumer trigger paths in the same task.
  Retain latest measured range. Derive bounded missing candidates from range, visual item membership, and relevant content availability.
  Schedule the existing debounce only for changed candidates or an explicit permitted retry opportunity.
  Preserve in-flight ownership across async work; current final settlement reconciles once without a no-progress loop.
  Pagination watches current scoped membership/loading settlement and checks progress before automatic chaining.
  Change the empty-state template ownership with this loading task, not as a later fixture-only fix.
- [ ] Step 7: Run EVENTS_LOADING, GEOMETRY, and SCROLL; record GREEN.
  Audit all VirtualScroller update consumers and prohibit normal geometry events as a loading fallback.
- [ ] Step 8: Obtain scoped review and commit: `perf(scroller): suppress redundant updates and preserve loading`.

## Task 4: Validate work counts and real scroll/loading behavior

**Files:** browser fixtures/preparation, exact ignore entries, execution report.
**Consumes:** completed production implementation. No production debug hooks.

- [x] Step 1: Add preparation/readiness/report RED tests before fixture implementation.
  Baseline is pinned to `56a1600a:frontend/src/composables/useVirtualScroll.js`.
  Generate only `frontend/src/composables/useVirtualScrollGeometryBaseline.js` and `frontend/tests/browser/.scroller-geometry-generation.json`.
  Use exclusive writes, saved digest/path ownership, rollback of owned files only, and guarded removal after tab closure.
  Refuse existing/modified/unowned files and preserve ownership after failed cleanup.
  Manifest schema: `{ version: 1, generationId, baselineCommit, sourcePath, createdPaths, digest }`.
  Use a UUID generationId, pinned baselineCommit `56a1600a`, the source path above, and a SHA-256 adapter digest.
  createdPaths contains only the baseline adapter path exclusively created by this invocation.
  Create the adapter with `wx`, then create its ownership manifest with `wx`.
  If manifest creation fails, rollback only the adapter this invocation created, after validating its digest.
  If rollback fails, exclusively save a recovery manifest for that owned adapter before returning the combined failures.
  Never overwrite a racing manifest or claim a pre-existing adapter.
  If recovery manifest creation also fails, report all failures and preserve the remaining files.
  Removal validates schema, pinned source, allowed paths, and actual digest; delete the manifest last.
  Test failed manifest creation plus failed adapter rollback, recovery-write failure, racing files, modified adapters, and cleanup retry.
- [x] Step 2: Implement an isolated real-composable fixture with one implementation per page and explicit ready/failed controls.
  The current imports production directly; the optional baseline copy keeps same-directory imports and unchanged executable source.
  Use the same rows/measurement sequence/container conditions in both modes.
  Wrap the existing itemKey input to count actual key extraction. Compare old/new entry references outside the measured operation.
- [ ] Step 3: Run 60 accepted height changes with 500, 2,000, and 10,000 rows, at head/middle/tail positions.
  Record exact source sizes, indices, key reads, rebuilt entries, total/spacers, range identities, and actual elapsed times.
  Validate final positions against an independent full oracle. Exclude oracle/reference-inspection work from the timed interval.
  Record reference-copy and height-stability scans as remaining costs. Never equate allocation counts with total runtime complexity.
- [ ] Step 4: Reuse `invisibleStreaming.js` unchanged through the new conversation entry.
  Add bounded controls for real SessionView/SessionItemsList, initial reveal, near-bottom growth, reading-above anchor, group expand/collapse, and streaming retirement.
  Check actual successful scrollToKey, row presence, and component content separately.
  Exercise same-index missing content and failure/recovery using fixture-only read substitutions; all mutation fetches remain blocked.
  Add session-pagination validation through the actual SessionList component with controlled read responses, including filtered short pages.
  Reject inherited baseline query flags in the production conversation entry; it always imports current production components.
- [x] Step 5: Run FIXTURE, GEOMETRY, SCROLL, EVENTS_LOADING, FULL, BUILD, and WHITESPACE.
  Record exact commands/counts/exits. The controller independently runs final full/build checks after all fixes.
- [ ] Step 6: Use existing browser tooling and servers for desktop and mobile viewport comparisons and Claude/Codex conversation checks.
  Apply viewport overrides after navigation; record actual DOM dimensions, feed conditions, module warm state, and all failures.
  Real bottom-following remains browser evidence. Sparse RAF or unavailable native behavior stays pending/inconclusive.
- [ ] Step 7: Close owned tabs, reset viewport, and remove only verified generated baseline/manifest files.
  Do not restart servers or install packages. Preserve unrelated user tabs and files.
- [ ] Step 8: Run whole-change adversarial review, correct significant findings, and obtain scoped re-review.
  Record every deferred acceptance limit and controller decision in the execution report.
- [ ] Step 9: Commit artifacts: `test(scroller): verify geometry reuse and loading preservation`.
  Update plan checkboxes truthfully. Retain explicit missing browser acceptance.

## Plan self-review

The three approved optimizations share geometry and loading contracts; event and consumer changes form one atomic task.
Every height mutation path and both update consumers have assigned tests.
Published snapshots, hidden work suppression, progressive rendering, and current payload conventions remain binding.
No task installs packages, restarts servers, implements priority 6, or changes the backend.
The specification permits prefix reference copying and structural scans; performance evidence retains those limits.

## Adversarial review record

Independent review closes after one correction pass and scoped re-review.

- Empty filtered pagination now retains a measurable viewport. Actual-template tests cover later matching pages and lifecycle ownership.
- Initial/recovery publication consumes one lifecycle obligation. Tests cover both callback orders and later tuple changes.
- Baseline preparation defines exclusive ownership, rollback recovery, guarded removal, and injected-failure tests.
- The plan names the existing cursor, covers finishing-request settlement order, and rejects production-entry baseline query flags.

Final reviewer verdict: **Ready to commit.** All Important findings and Minor recommendations are addressed.
Reports: `.superpowers/reviews/2026-10-02-scroller-geometry/plan-review-1.md` and `plan-rereview-1.md`.
No production implementation or runtime validation occurs during this documentation stage.
