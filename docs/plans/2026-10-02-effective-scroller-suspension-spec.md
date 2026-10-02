# Make scroller suspension effective

## Status and goal

This specification covers priority 3 in `2026-10-02-streaming-performance-analysis.md`.
Stop full-list geometry and height-cache maintenance while `useVirtualScroll.suspended` is true.
Restore the latest list and the saved anchor when the container becomes visible.

This document authorizes no implementation. The user requests a reviewed specification and a reviewed implementation plan, then a stop.
Priority 2 exists in the same worktree. Its browser acceptance remains unexecuted.
This change does not establish the cause of the reported application freeze.

## Source findings

- `frontend/src/composables/useVirtualScroll.js` computes `positions` by mapping every item and reading its cached height.
- Its render-range effect reads `positions` before checking `suspended`. An empty list also resets the range before that check.
- `totalHeight`, `visibleRange`, both spacers, and `getScrollAnchor()` can force the same calculation independently.
- Its post-flush items watcher builds a full key set and visits the height cache on every array replacement.
- `waitForHeightStability()` visits every cached height. Pending `scrollToKey()` operations also search the full item list.
- `VirtualScroller.vue` consumes the ranges and spacers. Its retained rendered window reads current items independently.
- Suspension saves up to five anchor candidates. Resume retries browser-clamped writes for up to eight frames.
- A zero-height container defers resume. Positive height completes an authorized deferred or automatic resume.

The initial analysis reports 120,000 key extractions across 60 suspended replacements of a 2,000-row list.
That probe does not separate geometry mapping from cache cleanup. New tests must distinguish both sources.

## Scope and boundaries

Modify frontend scroller suspension, geometry publication, deferred cache cleanup, and scroller-owned asynchronous work.
Keep the existing public API shape. Do not add dependencies or persistent state.
Preserve active scrolling, hysteresis, estimated heights, smooth reveals, and native bottom-following behavior.

Do not change visibility detection or the priority 2 publication registry.
Do not implement incremental geometry, publication throttling, Markdown changes, backend batching, or subscriptions.
Do not freeze canonical session data or forbid valid `seedItemHeight()` calls while suspended.
Do not claim zero hidden Vue work. Retained components and the bounded rendered-window mapping remain outside this contract.
An initially zero-height scroller that has not entered suspension remains outside this flag-based contract.

## Design decision

A guard in the range effect is necessary but insufficient. Other consumers can still request live positions.
Separate lazy live geometry from the published geometry used by consumers.
Retain the last active geometry snapshot throughout suspension. Reuse its array; do not clone the full list.

Capture positions, total height, render range, visible range, spacers, scrollTop, and viewportHeight at suspension entry.
Capture a coherent active snapshot before setting the suspended flag.
Synchronously reconcile range hysteresis against the captured positions, scrollTop, and viewportHeight.
Derive captured visible range and spacers from that same generation, even if the range effect has not flushed.
One necessary live calculation at this transition is allowed if active geometry is dirty.
An already-suspended call must not recapture geometry or replace saved anchors.

While suspended, public geometry reads return that snapshot. Object and array references remain stable.
The range effect checks suspension before reading positions or handling an empty list.
Source item replacements, in-place list changes, and valid height-cache updates remain available for the next resume.
No source update may cause a full positions calculation during suspension.

## Deferred height-cache maintenance

The items replacement watcher checks suspension before extracting keys or visiting the cache.
While suspended, it marks cleanup pending without retaining each intermediate array.
Track replacement identity synchronously in constant time, separately from the post-flush cleanup pass.
Resume must recognize a replacement even before its watcher flushes.
Record the array identity already cleaned. A queued watcher must not repeat that cleanup after resume.
On actual resume, clean the cache against the latest items once, before computing live geometry.
An active replacement continues to perform the existing cleanup.

Preserve cached heights for surviving keys, including valid heights seeded while hidden.
Remove cached heights for absent keys at actual resume.
`invalidateZeroHeights()` records a pending request while suspended; it does not iterate the cache.
Actual resume processes that request before live geometry. Preserve its existing active behavior.
This includes `VirtualScroller.vue` requesting invalidation before its positive-height resume notification.
Replacing items with an empty list while hidden preserves published geometry until resume.
Resuming that empty list publishes zero ranges, spacers, and total height. It clears obsolete anchors.
An in-place mutation does not expand the existing cache-cleanup policy beyond array replacement.

## Scroll commands and asynchronous ownership

Suspend invalidates scroller-owned asynchronous operations. Use a monotonically increasing lifecycle generation.
Cancel pending scroll RAF, anchor retry RAF, smooth-reveal completion, and height-stability wait ownership.
Stop height-stability watchers and timers immediately. Resolve their internal waits so callers can terminate.
Pending `scrollToKey()` and `scrollToEdge()` return `false` after invalidation, including a suspend/resume before their continuation runs.
Check ownership before every post-await list scan, geometry read, callback, or scroll write.

Commands started while suspended do not queue an intent or write to the container.
`scrollToKey()` and `scrollToEdge()` resolve `false` without a list scan or settle watcher.
`scrollToIndex()` returns `null` while suspended. Its active `Promise<void> | null | undefined` contract remains unchanged.
The other synchronous scroll commands retain their existing no-result return behavior and perform no work.
The other commands are `scrollToTop`, `scrollToBottom`, `scrollToAnchor`, and `setScrollTop`.
`syncScrollPosition()` must not overwrite the snapshot with a hidden container's zero values.

Read-only queries remain available. `getScrollAnchor()` uses the suspended snapshot without resolving the latest list.
`getScrollState()` retains its existing raw DOM semantics. It does not construct geometry.
Native browser animation cancellation is not required. Late events must not update suspended state or newer ownership.
Parent-owned asynchronous work remains outside lifecycle cancellation, including SessionItemsList's stream-swap restoration loop.
Its calls are blocked during suspension but can run after resume. Validate this interaction in browser acceptance.
Guard legacy programmatic-scroll reset timers against an older lifecycle or a newer programmatic operation.
Unmount performs the same ownership cleanup without leaving unresolved scroller waits.

## Resume and anchor preservation

Keep manual suspension, automatic suspension, and pending resume as distinct states.
A manual suspend after automatic suspension still prevents automatic resume until `resume()` requests it.
A manual suspend also revokes an earlier pending resume request, including when suspension is already active.
For `suspend -> resume at zero height -> suspend -> positive measurement`, suspension must remain active.
`resume()` with a missing container keeps suspension and anchors. It does not compute geometry.
`resume()` with zero clientHeight sets pending resume and keeps the snapshot.
A positive observer measurement completes resume only under the existing authorization rules.

Actual resume processes deferred cleanup, exposes latest geometry, updates the viewport, and restores saved anchors.
Many hidden replacements require one latest-list geometry build at resume, before new measured heights arrive.
Subsequent active measurements can legitimately cause additional builds.
Release the snapshot after active publication. Do not retain old list generations after resume or unmount.

Preserve key-first candidate selection, each candidate's own offset, index fallback, and final-item fallback.
Preserve intended reactive scrollTop when the browser clamps an initial write.
Keep the eight-frame retry bound. A later explicit scroll overrides retry restoration.
Generation-check retries before clearing a handle or touching anchors. A stale callback cannot cancel a newer retry.
Resume must work without a subsequent item update. Vue must publish coherent ranges and spacers on its next flush.

## Acceptance and validation

### Deterministic composable tests

- Warm a 2,000-row scroller, suspend, then replace one row 60 times with a Vue flush after each replacement.
- After each replacement, read every public geometry output and `getScrollAnchor()`. Assert zero item-key extractions after suspension entry.
- Track height-cache cleanup independently. Assert no full cache iteration or key-set construction while suspended.
- Repeat with hidden append, reorder, deletion, empty list, in-place mutation, height seed, and cache invalidation.
- Assert snapshot values and references remain stable. Resume against the latest list and valid seeded heights.
- Assert one live geometry build on resume before new measurements. Separate this count from the one cleanup key pass.
- Replace items and resume immediately, without a Vue flush. Also replace while active and immediately suspend.
- In the latter case, assert ranges and spacers agree with the captured positions, scrollTop, and viewportHeight.
- Cover missing container, zero-height deferred resume, automatic resume, repeated suspend, and manual takeover of automatic suspension.
- Cover revocation of pending resume and deferred zero-height invalidation during manual suspension.
- Cover anchor candidate survival, primary removal, all-key removal, shorter list, empty list, DOM-clamped writes, and explicit-scroll override.
- Deliver cancelled RAF callbacks deliberately after a new lifecycle starts. Assert no stale writes or ownership changes.
- Suspend pending nearest reveal, non-nearest reveal, edge reveal, and smooth reveal. Assert prompt wait cleanup and `false` completion.
- Call every scroll command while suspended. Assert no DOM writes, list searches, timers, or settle watchers.
- Retain existing active reveal, smooth-scroll, and streaming-row tests.

Use isolated Vue effect scopes and deterministic RAF/timer control. Stop every scope after each test.
Also exercise real unmount cleanup. Stopping an effect scope does not invoke `onUnmounted()`.
Do not infer browser bottom-following behavior from a fake container.

### Browser acceptance

Use the actual SessionView fixture from priority 2. Extend it only for suspension scenarios and geometry counters.
Count actual geometry builds and deferred cleanup passes at their implementation boundaries.
Do not treat all `itemKey()` calls as geometry builds: the rendered window also extracts keys.
Compare the same seeded large history before and after the change.
Exercise a visible stream, KeepAlive switching, a hidden Chat tab, reveal then immediate hide, and repeated hide/show cycles.
Include final JSONL reconciliation followed by immediate hide and rapid return, while stream-swap restoration remains pending.
Confirm latest text and rows on return, saved reading position, bottom-following, and scroll-up behavior.
Check desktop and mobile viewport sizes. Record results and limitations; do not claim the global freeze is fixed.

Server startup requires separate user authorization. Do not start servers or install packages to prepare these documents.
Implementation completion must distinguish deterministic checks from any browser acceptance still pending authorization.

## Adversarial review

Internal reviewer: `effective_suspension_spec_review`. Two passes examine the document against source and callers.

Resolved findings:

1. A repeated manual suspend must revoke an earlier zero-height pending resume request.
2. Zero-height invalidation must defer its cache scan while manually suspended.
3. Suspended `scrollToIndex()` returns `null`; its active smooth Promise contract remains unchanged.
4. Replacement tracking must precede post-flush cleanup, so immediate resume cannot use obsolete cached heights.
5. Snapshot capture must reconcile pending range work against one geometry generation.

The reviewer confirms the first four corrections. The closure pass identifies the fifth requirement and two wording/test-harness clarifications.
The document incorporates all three closure clarifications. The final closure check reports no remaining blocker.
Parent-owned stream-swap work remains explicitly excluded. Browser acceptance covers its rapid hide/return interaction.
