# Refresh-independent streaming publication rate

## Status and intent

This specification covers priority 4 in `2026-10-02-streaming-performance-analysis.md`.
Priorities 1–3 already exist on `bugfix/stable-streaming-rows`.
The user observes less interface blocking when a large session resumes.
That observation supports continued work. It does not establish a measured performance result for this change.

Reduce visible streaming publications without losing text or extending display lag indefinitely.
Preserve invisible-stream ownership, stable rows, and scroller suspension.
This document authorizes no implementation. Implementation requires the user's next instruction.

## Current behavior and evidence

`frontend/src/utils/streamingBuffer.js` owns one buffer per session and block index.
The registry supplies an immutable message/block generation identity.
The buffer retains `fullText` while inactive. Inactive feeds schedule no RAF.
Activation publishes one complete snapshot. Explicit snapshots also support first-observation bootstrap.

Active buffers estimate characters per millisecond from five recent delta arrivals.
The first arrival uses 200 characters per second.
Each RAF clamps elapsed time to 100 ms, advances at least one UTF-16 code unit, and publishes a prefix.
Consequently, display refresh controls publication count and the minimum progress rate.
The existing behavior can publish approximately 60 or 120 times per second with a persistent backlog.

`data.js::_onBufferDrain` updates parsed content on the stable streaming row.
Each changed prefix can cause Markdown processing and height measurement.
`streamBlockStop` intentionally leaves the buffer draining. Retirement calls `flushBuffer`.
The stop event is therefore not an immediate flush boundary.

Source inspection establishes the scheduling mechanism. No new browser benchmark exists yet.

## Scope

Change the buffer's regular publication policy and its tests.
Keep existing public buffer signatures and registry ownership semantics.
Test the store boundary and production conversation rendering where needed.

Do not change backend events, WebSocket batching, Markdown algorithms, scroller geometry, or arrival animation.
Do not introduce a user setting, dependency, global scheduler, or load-adaptive rate.
A cap applies separately to each block. Multiple visible blocks can exceed 30 total publications per second.
This change limits callbacks and their downstream work. It does not remove refresh-rate RAF wakeups.

## Alternatives and decision

| Approach | Benefit | Cost or limitation |
|---|---|---|
| Per-buffer RAF gate at 30 Hz | Small change; preserves RAF alignment and ownership | RAF still wakes each display frame |
| Timer followed by RAF | Fewer RAF wakeups | Adds timer cancellation, throttling, and clock interactions |
| Shared or load-adaptive scheduler | Can coordinate concurrent blocks | Adds fairness and lifecycle policy beyond this priority |

Choose the per-buffer RAF gate.
Use fixed constants: `PUBLICATION_INTERVAL_MS = 1000 / 30` and `MAX_DISPLAY_LAG_MS = 250`.
The initial 30 Hz target comes from the analysis. The 250 ms limit is a design choice for evaluation.
Browser acceptance must assess progression and catch-up jumps before calling this an improvement.

## Publication contract

### Regular publications

A regular publication comes from active-buffer RAF draining, including deadline catch-up.
For one buffer, consecutive regular publications have at least `PUBLICATION_INTERVAL_MS` between them.
A preceding immediate snapshot also starts a new interval for subsequent regular publication.
The first publication has no artificial startup delay unless a previous publication still owns that interval.
The buffer publishes at the first eligible RAF with undisplayed text.

Use `performance.now()` as the monotonic clock for arrival, interval, and lag decisions.
Do not mix RAF timestamps with a separately sampled arrival clock.
Fractional millisecond rounding may produce a lower rate. Never compensate by emitting multiple callbacks in one RAF.
No catch-up callback burst follows a long frame gap.

A skipped RAF performs constant-time checks only.
It must not advance displayed length, consume fractional credit, slice text, or call `onDrain`.
At an eligible RAF, integrate elapsed smoothing time since the previous eligible advance or the start of pending text.
Clamp this elapsed time to 100 ms, as the current buffer does.
Retain the five-arrival rate estimate, default rate, fractional credit, and minimum-one-code-unit progress.
Apply that minimum only to eligible publications.
Clamp progress to available text. Emit only a changed prefix.

The cap applies to text and thinking blocks through the same buffer.
It does not cap structural recomputes or final JSONL item rendering.

### Bounded display lag

An undisplayed episode starts when an active buffer receives text while fully caught up.
Record that arrival time as `pendingSince`.
Additional deltas and partial publications do not reset it.
Clear it when displayed text reaches all text received at that moment.

At the first eligible RAF where the episode age reaches 250 ms, publish the full current text.
This catch-up is a regular publication and obeys the same interval gate.
It clears the pending episode and smoothing credit/history before calling `onDrain`.
The next delta starts a fresh estimate and episode.
Do not wait for another delta to discover the deadline.

With RAF opportunities spaced at most F ms apart, an episode catches up by 250 ms + interval + F.
This is a scheduling bound, not a wall-clock guarantee during browser throttling or a blocked event loop.
Following a long gap, the next eligible RAF publishes the latest complete text once.
A large burst must not trickle at the default rate for several seconds.
Continuous arrivals repeatedly start new episodes after catch-up; backlog age cannot reset on every delta.

### Immediate snapshots and terminal flush

Keep `setActive(true)` and `snapshotBuffer` synchronous and complete.
Changed activation, bootstrap, and explicit snapshots are exceptions to the regular cap.
`flushBuffer` publishes complete text immediately when active, then destroys the buffer.
Terminal flush publishes text captured before its callback and ends that generation.
Reentrant feeds into that terminating generation are outside the retained-text guarantee.
Reentrant replacement remains supported and owns later feeds. Preserve current callback and return-value semantics.
Do not recursively flush callback-fed suffixes.
These exceptions preserve current ownership and retirement behavior.
They must not publish an identical prefix twice.

Every snapshot cancels outstanding RAF and clears pending episode, smoothing credit, and arrival history.
If it publishes, record its actual publication time before invoking `onDrain`.
A hidden flush returns complete text without publication, as it does today.
Hidden explicit bootstrap is permitted only through existing registry authorization.
Do not broaden document visibility or bootstrap eligibility.
A stop event alone keeps draining normally, with bounded lag.

## State and lifecycle invariants

- `fullText` contains every received delta in order, including inactive periods, before terminal flush starts.
- `displayedLength` never decreases within one buffer generation.
- Regular publications are growing prefixes of `fullText`.
- At most one RAF is pending per live buffer.
- Inactive buffers schedule no RAF and make no regular publications.
- No timer is added for lag or publication deadlines.
- Destroy, replacement, suspension, flush, and snapshot invalidate existing RAF ownership.
- A canceled callback cannot clear or replace a newer generation's scheduler state.
- Registry identity checks and public return values remain unchanged.

Before calling `onDrain`, commit displayed length, timestamps, pending-episode state, and consumed RAF ownership.
The callback can suspend, snapshot, flush, destroy, replace, or feed this buffer synchronously.
After the callback, schedule more work only if the same lifecycle still owns the drain and remains active.
Do not cancel a RAF that callback reentry already schedules.
Newly fed text after a complete publication starts its own pending episode.
This contract applies to immediate snapshot callbacks as well as regular callbacks.
`flushBuffer` may remove its map slot only if that slot still holds the flushed buffer.
A callback can replace the same slot; destruction and deletion must preserve the replacement.
This narrow helper correction belongs to scope because the publication callback is an explicit reentry boundary.

When text resumes after an idle gap, initialize smoothing elapsed time at the new arrival.
Do not count idle time as smoothing credit or as pending age.
A recent publication still enforces the interval gate.
Empty deltas must not start a pending episode or schedule useless work.

## Verification and acceptance

### Deterministic buffer tests

Drive monotonic time and RAF separately. Retain canceled callbacks for adversarial delivery.
Observe publication timestamps, text, and pending handles through public APIs.

1. Persistent backlogs at 60, 90, 120, 144, and 240 Hz obey the interval gate.
2. For the same simulated elapsed time, skipped frames never multiply minimum-character progress.
3. Skipped RAF makes no publication; reaching the gate resumes progress.
4. A 10,000-code-unit first burst completes within the stated scheduling bound without a stop event.
5. Frequent small arrivals do not reset pending age. A long frame gap yields one catch-up, not replay.
6. Slow arrivals and a new burst after idle retain correct rate estimation and bounded lag.
7. Visible flush, activation, and explicit snapshot complete immediately inside the interval.
8. Hidden feeds and flush remain silent. Resume publishes complete text once.
9. Two consumers of one block share one budget. Distinct blocks keep independent budgets.
10. Callback reentry and canceled callbacks cannot revive obsolete work or lose text in live or replacement generations.
    A terminal callback can replace its slot and feed the replacement; cleanup preserves that new generation.
    Same-generation terminal callback feeds are excluded as specified above.
11. Empty deltas, already-complete snapshots, and complete buffers create no redundant publication.
12. Message/generation replacement never lets old text reach the new owner.

Count regular and exceptional publications separately.
For a half-open interval of length T, regular count is at most `ceil(T / PUBLICATION_INTERVAL_MS)`.
Assert adjacent timestamp separation as the stronger invariant.
Use a small numerical comparison tolerance only in assertions, not an early-publication production threshold.

### Store and product acceptance

Store tests verify stable row/list identity, complete canonical text, and complete visible parsed text after flush or retirement.
Cover text, thinking, stop without retirement, and real-item replacement.

Extend the full conversation fixture only with additive controls and counters.
It must mount the actual `SessionView`, `SessionItemsList`, and `VirtualScroller` path.
Compare before/after on the same browser, viewport, visible block count, and feed schedule.
Use a pre-change buffer module captured from commit `1f7d16ef` for a local fixture baseline.
Keep that baseline distinct from the existing priority-2 `baseline=1` behavior.
Do not compare stages 2–4 together and attribute that difference to this cap.

Record RAF callback executions, regular publications, exceptional publications, parsed-envelope changes, and final content equality.
Record actual refresh opportunities; synthetic RAF tests are the rate proof if a 120 Hz device is unavailable.
Include long plain text, fenced code, open thinking, route hide/return, and stream retirement.
Assess composer typing, session switching, bottom-following, and a stable reading anchor.
A smoother-feeling interface is supporting evidence, not proof that the global freeze is fixed.

Acceptance requires passing deterministic and store tests, plus recorded browser results or an explicit pending browser status.
Do not label unexecuted fixture scenarios as passing browser checks.

## Risks and rollback

30 Hz produces coarser text progression than 60 or 120 Hz.
Deadline catch-up can reveal a large burst at once and trigger expensive Markdown work.
This cap bounds frequency, not the cost of a single render or aggregate cost across blocks.
Browser acceptance must record these trade-offs.

Rollback changes only this priority's buffer policy and dedicated fixture additions.
Keep priorities 1–3 intact. Do not use a production feature flag for rollback.

## Review record

Independent adversarial review identifies two lifecycle ambiguities.
The specification explicitly requires same-buffer slot deletion after flush callback replacement.
It also defines terminal flush scope, excluding feeds into the terminating generation while preserving replacement generations.
Scoped re-review approves both corrections. No significant finding remains open.
This review covers the specification only; product and browser validation belong to implementation.
