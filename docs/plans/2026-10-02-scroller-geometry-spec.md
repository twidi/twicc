# Limit Scroller Geometry Work Specification

## Status and intent

Priority 7 of `2026-10-02-streaming-performance-analysis.md`.
The user authorizes this specification, adversarial review, commit, implementation plan, review, and a separate plan commit.
Implementation requires a later user go.
Priority 6 is not approved. Progressive Markdown text, code highlighting, and diagrams remain unchanged.

The approved scope contains three changes:

1. Recompute position entries only from the earliest affected row.
2. Reuse render and visible range objects when their indices remain equal.
3. Emit scroller `update` only for meaningful range notifications, with explicit loading recovery outside geometry churn.

Work stays in `.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
No main change, merge, dependency installation, backend change, or server restart belongs to this scope.

## Existing contracts and source evidence

| Source | Current behavior |
| --- | --- |
| `composables/useVirtualScroll.js`, `livePositions` | Maps every item into `{ index, key, top, height }` after a relevant height change |
| `updateRenderRange` and `visibleRange` | Allocate new objects even when start/end indices remain identical |
| `batchUpdateItemHeights` | Captures an anchor, writes heights, then synchronously reads new positions for correction |
| Height remove/clear/seed/zero cleanup | Change effective geometry outside the measured-height batch path |
| `suspend` / `performResume` | Freeze published geometry while hidden; flush maintenance before computing resumed positions |
| `components/virtual-scroller/VirtualScroller.vue` | Watches both range objects and emits an initial post-measurement update |
| `SessionItemsList.onScrollerUpdate` | Scans the visible window plus `LOAD_BUFFER` for missing content; schedules a 150 ms debounce |
| `SessionList.onScrollerUpdate` | Requests another page when fewer than ten rows remain |
| `stores/data.js`, `loadSessionItemsRanges` | Returns false on fetch failure; current caller does not schedule explicit recovery |

Paths in this table are relative to `frontend/src`.
`useChatNavigation` also consumes exposed position arrays. Preserve their shape and reactive access contract.

A read-only probe executes the current composable with 1,000 rows and changes zero-based index 799.
It observes 1,000 item-key reads, no reused prefix entry, and new render/visible objects with unchanged indices.
This proves unnecessary construction. It does not measure a complete application freeze.

## Approach decision

| Approach | Benefit | Limit |
| --- | --- | --- |
| Stabilize ranges only | Small change; avoids downstream events | Still reconstructs all positions |
| Cached positions with explicit dirty tracking | Reuses valid prefix entries and supports existing APIs | Requires all height writes and structural changes to participate |
| Tree or persistent/chunked geometry index | Can reduce reference-copy and lookup cost further | Changes representations and adds complexity |

Use cached positions with explicit invalidation, plus stable ranges and separate loading triggers.
Do not introduce a Fenwick tree, global scheduler, or new geometry representation.

## Position correctness

For each row, preserve the existing entry shape and exact cumulative-height equations:

- `index` is the zero-based current index.
- `key` comes from the existing item-key function.
- `height` is the cached height, or the current instance's `minItemHeight` estimate.
- The first `top` is zero. Every later `top` equals the previous `top + height`.
- `totalHeight` equals the final `top + height`, or zero for an empty list.

Retain all entries before the earliest affected index by reference.
Reconstruct the affected suffix from the unchanged prefix's final bottom.
For a 1,000-row list changing index 799, retain entries 0–798 and reconstruct entries 799–999.
A batch uses the minimum affected index across all accepted effective height changes.
Multiple writes before one geometry read accumulate invalidation. A synchronous read must include every preceding write.

Never mutate an already published position array or entry.
Suspended snapshots and retained old arrays must remain valid after later height or topology changes.
Copying unchanged entry references into a new array is permitted.
That copy is O(N); this scope does not promise an O(suffix) total runtime or eliminate every linear operation.
Height-only updates must avoid full key extraction, full height scanning, and prefix position arithmetic/allocation.
Record reference-copy cost separately from rebuilt entries in performance evidence.

## Invalidation coverage

Track effective geometry, not merely raw Map writes.
A first measurement equal to the estimate can preserve positions while retaining the existing measurement event behavior.
Keep the exact measurement epsilon and zero-height rejection. Do not round accepted heights.
Do not derive validity from a height sum: opposite height changes can have the same sum.

Every cache mutation path participates:

- Measured single/batched writes, including no-container and programmatic-scroll branches.
- Removal reverting a current key to its estimate.
- Clear reverting current measured rows to estimates.
- Seeds for current rows and future canonical replacement keys.
- Zero-height invalidation and removed-key cleanup.
- Deferred maintenance before resume.

An O(1) current key lookup may use an internal key-to-index map.
Unknown/future keys retain their valid cached heights; they do not invalidate an unrelated current row.
When that key enters the items, its cached seed must determine its first position.
Update the map when topology changes. Never use a stale index to skip affected geometry.
Duplicate-key input must retain existing position equations through a conservative fallback; do not silently choose an incorrect occurrence.

Preserve reactive invalidation for later prefix changes after a suffix-only recomputation.
Do not lose dependencies by reading only the last dirty suffix from a reactive Map.
An explicit geometry revision controlled by every writer can provide this ownership.

## Structural changes

Compare ordered keys and effective heights. Array identity or length alone is insufficient.
Support append, prepend, insert, removal, truncation, reorder, same-length replacement, grouping, filtering, and streaming retirement.
Retain the common valid prefix; recompute from the first mismatch or earlier height invalidation.
A conservative full rebuild is permitted when topology cannot be safely reconciled.
A same-key content replacement can preserve geometry but must still update rendered content and loading eligibility.
Support existing in-place mutations, including the `shallowRef` plus `triggerRef` contract tested by suspension tests.
Changes to reactive fields read by the item-key function must remain observable.
Structural key reconciliation may be O(N). Do not move that scan into the height-only path.

## Suspension and scroll preservation

Preserve priority 3's effective suspension.
Hidden item replacements and seeds cause no geometry construction or key extraction.
Retain the newest hidden state and dirty records without changing the published snapshot.
Resume flushes deferred maintenance, then reconciles current topology and heights before restoring the anchor.
Immediate resume before the post watcher runs must work.
Suspension immediately after an active replacement must capture that replacement.

Preserve the current anchor capture/correction order and synchronous old/new geometry reads.
Preserve surviving-key candidates, index fallback, bounded restore frames, and explicit-scroll cancellation.
Preserve native bottom-sentinel anchoring. Do not redesign bottom-following or introduce a second scroll correction.
Keep shared ResizeObserver batching, threshold handling, height floors, and legitimate later shrink measurements.
`waitForHeightStability` retains its current contract; optimizing its full-cache scan is outside this scope.

## Stable range objects

Preserve existing binary-search boundaries, hysteresis, buffers, and exclusive end indices.
Reuse each range object when its own `start` and `end` are unchanged.
Changing render indices must not force a new visible range object when visible indices remain equal, or conversely.
Empty ranges retain `{ start: 0, end: 0 }` semantics.
Continue reading all inputs necessary for reactive tracking before choosing to reuse an object.
Range identity stabilization must not freeze later height, viewport, scroll, or topology changes.
Suspended range objects remain frozen. Resume computes correct ranges before exposing active geometry.

## Update event meaning

The public payload remains `{ startIndex, endIndex, visibleStartIndex, visibleEndIndex }`.
Indices retain their existing meanings and exclusive-end conventions.
The normal event reports a change in any of these four indices.
Do not emit normal updates for equal tuples caused only by geometry recalculation or sub-index scroll movement.
Keep one initial event after mount and viewport measurement.
Allow one explicit recovery notification per real inactive-to-active scroller recovery, even when the tuple is unchanged.
Do not duplicate initial/recovery notifications through the normal watcher.
Do not emit new loading notifications or expose item content in this public payload.

`update` does not guarantee window membership, content availability, or pagination settlement notifications.
Both existing consumers must receive explicit loading opportunities independent of range-object allocation.
Audit all `@update` consumers before changing emission behavior.

## Loading preservation

### Conversation content

Keep the current visible load window, `LOAD_BUFFER`, debounce duration, range conversion, and `hasContent()` access.
Preserve the current conservative inclusive loading loop; do not silently change the exclusive-end interpretation in this optimization.
Do not scan the entire conversation for each update.

Reevaluate the bounded load window on:

- A meaningful range update or initial/recovery notification.
- Window membership changes at equal indices.
- Relevant content becoming missing or arriving partially, without a range change.
- View activation/recovery.
- A subsequent scroll opportunity after a settled failed or no-progress load, even if indices remain equal.

Do not reschedule the debounce for unchanged missing-line candidates on ordinary geometry churn.
Keep failed or partially fulfilled gaps eligible for later retries.
Serialize/coalesce this component's pending load decisions; do not create duplicate concurrent requests for the same gap.
After successful partial progress, reconcile remaining candidates.
If a request fails or makes no progress, do not create a self-rescheduling retry loop.
Retry after a later external opportunity listed above; preserve current error reporting.
This change adds no periodic polling or new automatic network retry policy.
Hidden/unmounted views must not start delayed loads or receive obsolete post-await scroll correction.

### Session pagination

Preserve the near-end threshold, loading guard, `hasMore`, and existing retry button/error state.
Reevaluate after page loading settles and list membership changes, even when range indices remain equal.
A short page must not leave the viewport underfilled while more rows are available.
Pagination decisions require a mounted, active, measured scroller and ownership of the current project/filter scope.
Before initial measurement, defer range-based chaining. On recovery, reevaluate once against current measured bounds.
Capture scope ownership before awaiting a page. Retired completions cannot chain against another scope or hidden view.
In-flight requests may still settle into their existing store scope. Do not discard valid store data.
Define progress through existing pagination state, not filtered visible length alone.
Progress means the requested scope gains previously unseen session IDs or its existing pagination cursor advances.
A change to `hasMore=false` ends chaining. An unchanged cursor with no new IDs means no progress.
Filtered-out pages can advance the cursor and permit continued loading while the active viewport remains underfilled.
If a successful page makes no progress, stop automatic chaining.
A later range/membership change, actual scroll opportunity, scope reset, or activation/recovery permits a new attempt.
A failed page must retain the error UI and explicit retry action. Do not introduce a tight request loop.

## Acceptance and evidence

| Requirement | Required proof |
| --- | --- |
| Correct suffix entries | Exact comparison against a simple full-recompute oracle |
| Reduced construction | Count key reads and rebuilt entries for tail, middle, and head updates |
| Immutable publication | Retain old arrays and suspension snapshots across later writes |
| Complete invalidation | Every writer, future seeds, deletion/clear, offsetting heights, and later prefix changes |
| Structural correctness | Ordered-key changes, same-length replacement, in-place mutation, retirement, groups |
| Stable reactive ranges | Equal indices preserve identities; later genuine changes publish normally |
| Meaningful events | Initial/recovery once; tuple changes once; equal geometry tuples suppressed |
| Loading without starvation | Same-index placeholder, partial response, failure then recovery, hidden pending debounce |
| Pagination without starvation | Short/filtered/empty pages, cursor progress, unchanged tuple, failure and explicit retry |
| Pagination ownership | Hide/unmount/project-filter change during await; no stale chaining; activation recovery |
| Scroll preservation | Existing reveal, anchor, suspension, and stream-retirement tests; real conversation checks |

Use production functions, real Vue reactivity, and controlled geometry in deterministic tests.
Do not claim a faster phone from synthetic operation counts.
Record reference-copy and full-cache stability costs as remaining limits.
Browser checks use existing servers and the existing guarded conversation fixture where practical.
If no browser evidence is available, report it as pending. Never substitute a fake bottom-following conclusion.
Keep progressive text/code/Mermaid rendering, grouping, content loading, and initial reveal behavior intact.

## Adversarial review record

Round 1 identifies missing pagination lifecycle ownership and ambiguous pagination progress.
The correction defines active measured scope ownership, stale-completion rejection, existing-cursor progress, and external recovery triggers.
Scoped re-review marks both findings addressed and finds no new issue. The specification review is closed.
