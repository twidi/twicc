# Stop visual publications for invisible streams

## Status and intent

This specification covers priority 2 in `2026-10-02-streaming-performance-analysis.md`.
It follows the stable-row change. It does not implement the other priorities.
The user requests a specification and an internal adversarial review. Product implementation is not authorized by this document.

Keep receiving every streaming delta. Stop adaptive visual publications when no eligible consumer needs a block's body.
When a consumer becomes eligible, publish the latest received text once. Smooth only subsequent deltas.
Preserve visible streaming, indicators, final reconciliation, detail state, and scroll behavior.
This is a background-work optimization. It does not claim to resolve the reported global freeze.

## Source findings

- `frontend/src/utils/streamingBuffer.js`: `initBuffer` creates a buffer for each block. `pushDelta` schedules RAF without visibility input.
- `frontend/src/stores/data.js`: `streamBlockDelta` appends to canonical `block.text` and feeds the buffer. `_onBufferDrain` changes `block.displayedText` and the current cached row's parsed envelope.
- `computeVisualItems` currently uses `block.displayedText ?? block.text`. A hidden buffer must not leave that fallback permanently stale.
- `streamBlockStop`, inactivity timers, retirement, process cleanup, and reconnect have distinct lifecycle responsibilities. Publication suspension must not replace them.
- `SessionItemsList.vue` receives `sessionActive`. It keeps the scroller mounted with `v-show`. Its scroller has 5,000 px render and 10,000 px unload buffers.
- `SessionView.vue` hosts main, ephemeral, subagent, and docked views. Session selection alone does not establish Chat visibility.
- `VirtualScrollerItem.vue` wraps rendered rows. A mounted row can remain far outside the viewport.
- Claude `ThinkingContent.vue` and Codex `Reasoning.vue` render the body while details are open or closing. A closed body still exposes an activity indicator.
- The shared read-only viewer also uses `SessionItem`. It has no live buffer and must not create streaming consumers.

## Scope

Include frontend buffer scheduling, a per-client consumer registry, and visibility reporting from actual conversation views.
Include text and thinking for Claude and Codex, including streamed proposed-plan text.
Include main sessions, ephemeral sessions, nested subagents, concurrent views, dock regions, and KeepAlive.

Exclude backend broadcasts, subscriptions, session hiding, delta batching, publication-rate limits, Markdown parsing changes, and geometry algorithms.
Do not modify `Session.hidden`. Do not add dependencies or persist visibility state.
Existing reactive canonical-text updates remain. This change removes buffer RAF and parsed-envelope publication for hidden bodies.
Already-started Markdown tasks can finish after suspension. Cancelling those tasks belongs to a separate priority.

## Approach decision

A selected-session gate is too coarse. It misses visible subagents and independently visible dock panes.
A mounted-component gate is also too coarse. KeepAlive, inactive tabs, and large scroller buffers retain invisible components.

Use per-view, per-block consumer tokens. Combine explicit view activity, row intersection, and body openness.
The buffer runs if at least one eligible consumer exists and the browser document is visible.
Use browser observers and lifecycle events. Do not measure layout or scan visual lists on each delta.

## Identity and ownership

A block identity contains `sessionId`, `messageId`, and `blockIndex`.
A consumer has a unique opaque token. Several consumers can refer to one block identity.
A reused synthetic line number never identifies a previous message's block.

Keep registry and observer ownership outside persisted Pinia state. Provide a scoped composable for UI ownership.
Use explicit operations to acquire, update, and release a token. Release is idempotent.
A late release can remove only its own token. It cannot decrement another consumer's ownership.

A message replacement removes old buffer state and invalidates old-generation consumer eligibility.
Mounted owners refresh their identity when the current message changes. They cannot resume the new message through an old token.
Acquisition must resolve the current block before publishing. Missing, retired, or mismatched identities are harmless no-ops.

The registry must not statically import the data store. Connect publication through callbacks or explicit store calls.
Preserve the project's circular-import rules. Do not maintain another visual-row registry.

## Eligible consumer

A consumer is eligible only when all applicable conditions hold:

1. The owning conversation view is active, attached, and actually shown in its dock or tab.
2. The view is not hidden during initial loading or reveal.
3. The synthetic body exists in the rendered display mode and group state.
4. The row intersects its visible, clipped scroll area or a 200 px prefetch margin.
5. For thinking, the detail body is open or remains mounted during its closing animation.

The 200 px margin is deliberate preparation near the viewport. It is independent of the scroller's larger rendering buffers.
Rows outside that margin become ineligible after the observer reports their position.
Group-head content retained during a closing animation remains eligible until its retained body leaves.
Exit-only rows can retain pixels but cannot acquire ownership for a retired block.

Report row intersection with a shared IntersectionObserver per scroller. Use its scroll container as root and vertical `rootMargin: '200px 0px'`.
Nested clipping must still apply. Explicit view eligibility also covers maximization, hidden regions, inactive tabs, and KeepAlive.
Do not infer visibility from the selected session, `Session.hidden`, or the existence of a cached row.

Provide explicit view and row context through the production conversation hierarchy.
The main and ephemeral paths need context. Each nested subagent view needs its own context.
Audit all SessionItemsList mounts and dock visibility paths. `sessionActive` alone is insufficient.

The current main and subagent Chat tabs remain anchored in the center region.
For those views, combine SessionView's `isActive`, the actual `centerActiveTab`, and SessionLayout's `centerVisible`.
Expose the center visibility predicate through reactive context or a prop. It must match the `v-show` used by SessionLayout.
A tool dock owning the route does not hide the center's selected Chat tab.
A maximized tool dock does hide it. Do not use `activeTabId === 'main'` as the complete predicate.
The ephemeral direct Chat path uses active-session eligibility without a center-tab requirement.
Forward each subagent tab's eligibility through SessionContent instead of inheriting the main Chat tab's eligibility.
`layout.isToolPanelVisible` alone is insufficient: it deliberately returns true for center panels, including inactive tabs.

Acquire text-body ownership at the synthetic row owner. Do not wait for nonempty Markdown or proposed-plan parsing.
Acquire thinking-body ownership at its open/closing boundary in both providers. Forward the block identity from the synthetic row context.
A closed thinking body has no body consumer. Its spinner and stopped state continue through existing structural lifecycle updates.
Missing context defaults to no live ownership. The read-only viewer and real JSONL rows remain unaffected.

### First observation and cold mounting

An eligible view can bootstrap a newly mounted synthetic row before its first intersection observation.
Allow one synchronous snapshot only when the block has no eligible consumer and no current bootstrap reservation.
The browser document must be visible. Text-body ownership or open/closing thinking-body ownership must already be established.
An already-active block exposes its current displayed prefix to the new view, without full-text catch-up.
Keep a block-local bootstrap reservation until its first observation or owner disposal. Concurrent unknown mounts reuse that snapshot.
The one snapshot can occur even if the row is later reported offscreen.
Do not start continuous RAF during this unknown-intersection state.
After the first observation, only the normal eligibility predicate applies.
This exception prevents an empty text row from needing rendered text before visibility can be established.
A hidden or detached view cannot bootstrap a snapshot.

## Buffer state contract

| State | Incoming delta | Visual action |
|---|---|---|
| No eligible consumer | Retain full text and required lifecycle state | No scheduled drain, no content publication |
| Eligible consumer, document visible | Retain text and update arrival history | Existing adaptive drain |
| Document hidden | Retain text and lifecycle state | Cancel scheduled drain; no content publication |
| First eligible consumer appears | Retain text | Publish current full text once, then smooth future deltas |
| Additional consumer appears | Retain text | Use the shared currently displayed prefix; do not force another catch-up |
| Last consumer disappears | Retain text | Cancel scheduled drain immediately |
| Block retires | Preserve existing authoritative final-item handling | No stale body publication after removal |

Initialize each new buffer as suspended. Registration order must not require the block to exist before its row can mount.
On suspension, cancel pending RAF and invalidate its scheduler generation or expected-request token.
Every callback checks that token, current eligibility, and block identity before touching any frame state.
A stale callback returns before changing displayed length, fractional characters, frame time, RAF ownership, or publication state.
Invalidate scheduler ownership on flush, destroy, and buffer replacement too.
Eligibility and message identity alone are insufficient after a suspend/resume of the same block.
No paused `pushDelta` operation schedules RAF.

On resume, catch up once through the stable-row publication path. Reset displayed length to the full-text length.
Reset fractional characters, frame time, and rate-estimation history. Start a new arrival window for later deltas.
Do not replay hidden text, use the hidden interval as a frame duration, or recursively recompute the list.
An unchanged catch-up needs no parsed-envelope replacement.

Maintain `block.text` as the authoritative received content. Keep the buffer's retained text consistent with it.
`block.displayedText` denotes the last published prefix and may remain stale while suspended.
When building a synthetic row for a suspended block, use canonical `block.text`. This structural snapshot does not resume its buffer.
This covers display-mode changes, group reopen, and cache replacement before consumer acquisition.
On acquisition, resolve the current visual cache row. Never publish through a saved row reference.

If no row exists, resume still updates the publication cursor and `block.displayedText` once.
A subsequent structural build must show current content. A missing row must not cause repeated catch-up publications.
Keep outer rows stable for content-only changes, as required by priority 1.

## Terminal and reconnect behavior

Distinguish finalization from visibility suspension. Stopped blocks can still have buffered visible text awaiting final reconciliation.
Do not destroy a stopped block merely because it has no consumer.
Retirement must flush canonical data and preserve the existing detail-state transfer before removal.
A hidden finalization can advance internal cursors without publishing a hidden parsed envelope.
A visible finalization retains its existing final-content behavior.

Process cleanup, session removal, new-message replacement, and reconnect cancel old RAF and clear matching buffer entries.
Reconnect retains mounted view owners but invalidates old block identities. New blocks require refreshed ownership.
Every observer, document event listener, and token has a cleanup owner. Observer retargeting cannot leave the previous row registered.
Document visibility affects only this browser client. A second client can independently continue displaying the same session.

## Performance acceptance

After eligibility settles, feed 1,000 deltas to a block with no consumer.
Observe zero scheduled buffer RAF callbacks, zero `_onBufferDrain` calls, and zero parsed-envelope replacements from those deltas.
Continue retaining every character and running the existing lifecycle timers.

One false-to-true aggregate eligibility transition produces at most one catch-up publication.
New consumers added to an already-active block do not jump an existing visible consumer to the full received text.
The per-delta gate uses block-local state. It must not scan consumers, historical items, or DOM nodes.
Use a registry-maintained eligible-token count or equivalent constant-time aggregate.
Counters distinguish bootstrap snapshots, structural rebuilds, normal drain publications, and finalization.
Do not claim zero total Vue work: canonical-text reactivity and other independent work remain.

## Verification requirements

### Deterministic buffer and ownership tests

Use controlled RAF and time. Cover inactive initialization, retained deltas, suspension between scheduling and callback, and immediate resume.
Cover duplicate release, two consumers, identity replacement, stale callback, retirement, reconnect, and document hide/show.
Schedule frame A, suspend, resume the same block, and feed a delta that schedules frame B.
Deliver stale A explicitly. It must not publish or change state. B remains the sole pending frame.
Cover initial mount before first delta and first observation after a bootstrap snapshot.
Cover adding a second consumer during a visible backlog without changing the first consumer's displayed prefix.
Verify canonical text, cursor consistency, rate reset, and no hidden backlog replay.

### Vue integration tests

Use actual buffer and store actions. Verify stable row identity and reactive content delivery.
Verify closed and closing thinking, both providers, display-mode filtering, group close/reopen, cache rebuilding, and repeated synthetic indices.
Verify inactive KeepAlive views, visible subagents, two views of one block, dock maximization, and listener disposal.
Verify activity indicators and final detail-state transfer independently from body publications.

### Full conversation browser validation

Mount the production SessionItemsList hierarchy, actual scroller, provider components, visibility context, and scroll providers.
Control frontend stream events without sending a real provider message or changing the user's main instance.
If a fixture needs data-loading substitutes, document each substitute. Do not substitute scroll or visibility behavior under test.
The former row-only harness is insufficient for bottom-following acceptance.

Run Claude text/thinking and Codex text/thinking/proposed-plan scenarios.
Validate visible bottom following, reading above, row offscreen suspension, catch-up on return, inactive Chat tabs, and session switches.
Validate center Chat while a tool dock owns the route, center suppression by dock maximization, and nested subagent views.
Validate browser document hide/show, final JSONL reconciliation, and subsequent messages.
Compare visible behavior with the pre-priority-2 worktree base. Use the same stream events and content.
Record publication counters and component identities. Record actual scroll outcomes, not just stored geometry.
Natural user-session evidence is supplementary. An unobserved lifecycle case remains incomplete.

No implementation acceptance requires reproducing the reported global freeze.
Acceptance requires the hidden-work reduction and preserved visible behavior above.

## Deliverable boundaries

The implementation plan must identify the concrete dock/tab activity signals and visibility-context plumbing before product edits.
Keep code, tests, and evidence in the isolated worktree. Do not restart the main instance.
This document does not authorize server startup, implementation, or merging into main.

## Adversarial review record

An internal subagent reviews the specification against frontend source on 2026-10-02.
The loop completes in two rounds.

The first round identifies two actionable conflicts:

- A new view's cold-mount snapshot can jump an existing view's shared displayed prefix.
- A canceled RAF callback can mutate a resumed buffer with the same block identity.

The revised specification limits bootstrap to inactive blocks, with a shared reservation and document/body eligibility checks.
It also requires scheduler-token invalidation before stale callbacks can mutate any state.
The deterministic tests explicitly cover both conflicts.

The second round reports no remaining actionable blockers in the reviewed specification and source.
This is a specification review. No product implementation or browser acceptance run occurs in this stage.
