# Streaming performance: prioritized analysis

Date: 2026-10-02

Status: initial analysis of optimization options. This document is not a specification or an implementation plan.

## Main finding

Separate received text, displayed text, and conversation structure.

Current text updates can invalidate geometry for the entire conversation. Invisible streams also continue to publish visual updates.

The highest-impact options remove this work before changing the network protocol or the Markdown parser.

## Problem and evidence limits

The user reports intermittent UI freezes during active sessions. Symptoms include blocked session navigation and delayed composer input.

The investigation covers the source code, recent animation changes, Chrome observations, and synthetic Node measurements.

No browser Performance trace captures a reported freeze. The investigation confirms expensive mechanisms, not the exact cause of every freeze.

Node measurements use the actual composable or parser with synthetic data. They exclude browser layout, paint, and other application work.

An isolated composable benchmark produces a Vue lifecycle warning because it runs outside a component. Treat it as a mechanism benchmark.

## Priority overview

Priority follows expected impact, current evidence, and implementation risk. These options overlap; their benefits must not be added together.

| Rank | Option | Expected impact | Evidence | Main limitation |
|---|---|---|---|---|
| 1 | Keep streaming rows stable; avoid list-wide lookup | Very high for large conversations | Synthetic test removes geometry key reads | Verify Vue rendering and final-item replacement |
| 2 | Stop visual publications for invisible streams | Very high with background sessions | Buffer has no visibility gate | Visibility must cover every view and block |
| 3 | Make scroller suspension effective | High for cached inactive sessions | Suspended benchmark still reads all keys | Other geometry consumers also need inspection |
| 4 | Limit publication rate independently of screen refresh | High on fast-refresh devices | Buffer publishes once per simulated frame | Less granular text progression |
| 5 | Coalesce Markdown renders and stop obsolete work | High for costly messages | Obsolete renders finish before rejection | Cannot interrupt synchronous work already running |
| 6 | Reduce streaming Markdown processing | High for long code and diagrams | Full-source parsing remains on each update | Preserve Markdown interpretation and final rendering |
| 7 | Update geometry only where heights change | High for long conversations | Height changes rebuild all positions | Preserve exact scroll and group behavior |
| 8 | Avoid height animation for frequent automatic arrivals | Potentially high during tool activity | CSS height animation feeds ResizeObserver | Changes the arrival effect |
| 9 | Batch network deltas | Moderate; grows with concurrent sessions | Providers broadcast individual deltas | Preserve event order and final flush |
| 10 | Subscribe clients to relevant streams | Potentially high at scale | Updates reach all connected clients | Requires reliable resume snapshots |

Ranks 2 and 3 complement rank 1. Stable rows do not eliminate Markdown work or actual height changes.

## Current streaming path

1. A provider receives a text or thinking delta.
2. The backend broadcasts a `stream_block_delta` event to the `updates` group.
3. `useWebSocket.js` sends the event to `streamBlockDelta()`.
4. The store appends received text and feeds an adaptive buffer.
5. The buffer publishes a growing text prefix through `requestAnimationFrame()`.
6. `_onBufferDrain()` finds and replaces the synthetic visual row.
7. Content components update; Markdown renders the changed source.
8. The scroller reacts to row replacement and measured height changes.
9. A real JSONL item eventually replaces the synthetic streaming item.

The store does not recompute the full visual conversation for every character. It patches the streaming row.

Full recomputation still occurs at structural events, including block start, stop, inactivity transitions, and real-item arrival.

Source areas:

- `frontend/src/utils/streamingBuffer.js`
- `frontend/src/composables/useWebSocket.js`
- `frontend/src/stores/data.js`: `streamBlockStart`, `streamBlockDelta`, `_onBufferDrain`, `_retireStreamingBlocks`, `recomputeVisualItems`
- `frontend/src/components/ui/MarkdownContent.vue`
- `frontend/src/utils/markdown.js`
- `frontend/src/composables/useVirtualScroll.js`
- `frontend/src/components/virtual-scroller/VirtualScroller.vue`
- `src/twicc/agent/base_agent.py`: `_broadcast_stream_event`
- `src/twicc/providers/claude_code/agent/agent.py`
- `src/twicc/providers/codex/agent/agent.py`

## 1. Keep streaming rows stable

### Current unnecessary work

Each `_onBufferDrain()` call searches `sessionVisualItems` with `findIndex()`.

It creates parsed content, copies the visual row, replaces the array entry, and updates the visual-item cache.

The key and order remain unchanged. The replacement nevertheless invalidates the scroller's `positions` computed value.

`positions` maps every visual row, including rows outside the rendered DOM range.

### Candidate optimization

Keep the visual row object stable. Replace its parsed-content property through the existing `setParsedContent()` helper.

Keep a direct reference or indexed lookup for the active row. Refresh that reference when structural recomputation replaces the visual list.

Do not mutate text inside a `markRaw()` parsed object and expect Vue to observe that mutation.

### Measurement

The actual scroller composable processes 60 synthetic text updates:

| Visual rows | Row-replacement time | Average per update |
|---:|---:|---:|
| 500 | 36.98 ms | 0.62 ms |
| 2,000 | 95.24 ms | 1.59 ms |
| 10,000 | 1,033.68 ms | 17.23 ms |

A 60 FPS frame has 16.67 ms. The largest case exceeds that budget before browser rendering work.

A second test compares 60 updates across 2,000 rows:

| Update method | Geometry key reads |
|---|---:|
| Replace the visual row | 120,000 |
| Stable row with `setParsedContent()` | 0 |

This test confirms geometry invalidation removal. It does not validate component rendering or final-item reconciliation.

## 2. Stop visual publications for invisible streams

### Current unnecessary work

The buffer has no visibility input. Every started block receives a buffer, including streams without a visible consumer.

Invisible cases include another selected session, an inactive Chat tab, offscreen rows, closed thinking, and hidden streaming in Conversation mode.

`KeepAlive` preserves components and reactive effects. Detaching a component does not automatically stop all its calculations.

### Candidate optimization

Continue receiving and retaining every delta. Suspend visual publications when no visible consumer needs them.

When a consumer becomes visible, publish the latest received text directly. Resume smoothing from that state without replaying the hidden backlog.

Track visibility per view and block. Account for subagent views, dock layouts, render buffers, and closing details animations.

A closed thinking block still needs its activity indicator. It does not need repeated Markdown rendering of its hidden body.

Keep this separate from `Session.hidden`. That field controls session visibility and existing backend broadcast suppression.

## 3. Make scroller suspension effective

The render-range `watchEffect` reads `positions.value` before checking `suspended.value`.

This forces a full position calculation even when the remaining range calculation returns early.

An isolated test suspends a 2,000-row scroller, then performs 60 row replacements with Vue flushes.

Result: `suspended === true`, but the key extractor runs 120,000 times.

Candidate: check suspension before reading geometry. Inspect total height, visible range, and cached rendering for other geometry reads.

Preserve saved anchors and the existing deferred resume behavior. A guard change alone does not establish zero inactive work.

## 4. Limit publication rate

The adaptive buffer publishes on each animation frame while text remains to drain.

| Simulated display refresh | Drain callbacks in one second |
|---:|---:|
| 60 Hz | 60 |
| 120 Hz | 120 |

Candidate: retain RAF alignment but cap text publication independently of refresh rate.

An initial value to evaluate is 30 publications per second. Publish multiple characters together when necessary.

Consider a lower rate for expensive blocks or measured render overload. Bound smoothing delay so the display does not trail received text indefinitely.

Final delivery must publish complete text. Browser visibility recovery must not depend on replaying delayed frames.

The trade-off is less granular progression. The text itself remains complete.

## 5. Coalesce Markdown renders

`MarkdownContent.render()` runs on each source change. Async calls can overlap.

`renderSeq` prevents old results from committing. Its obsolete-result check occurs after the block-render loop.

Candidate: keep one active render and one pending latest source. Replace the pending source instead of queueing intermediate versions.

Check obsolescence between blocks and before expensive post-processing. Stop unnecessary work after component disposal or loss of visibility.

Deduplicate identical in-flight block renders. The current cache holds completed HTML, not in-flight Promises.

Do not cache failed or incomplete Mermaid output as a successful final result.

This controls concurrency and wasted work. It cannot interrupt an already-running synchronous parse or highlighting operation.

## 6. Reduce streaming Markdown processing

### Current cost

The existing per-block cache reuses completed blocks. Preserve that benefit.

`splitMarkdownBlocks()` still parses the entire source, splits its lines, extracts block sources, and hashes them on each publication.

The growing final block misses the completed-result cache. It receives rendering and post-processing again.

Synthetic parsing measurements use headings, paragraphs, bold text, and links:

| Source length | 60 calls to `splitMarkdownBlocks()` |
|---:|---:|
| 2,000 characters | 52.9 ms |
| 10,000 characters | 116.2 ms |
| 40,000 characters | 367.3 ms |

These figures exclude sanitization, Shiki, Mermaid, DOM updates, layout, and paint.

### Candidate options

| Option | Benefit | Trade-off or risk |
|---|---|---|
| Lower publication frequency | Fewer full-source parses | Less granular progression |
| Render an unfinished code fence without Shiki | Avoid repeated highlighting of growing code | Highlighting arrives after closure |
| Defer unfinished Mermaid diagrams | Avoid incomplete diagram rendering and retries | Diagram appears later |
| Reuse initial parsing tokens for block rendering | Avoid some secondary parsing | Requires renderer changes |
| Parse only the changed suffix | Lower long-message parsing cost | Markdown boundaries and references can affect earlier blocks |
| Render a lightweight streaming tail | Limit processing of unfinished content | Temporary formatting differs from final output |

Start with scheduling and expensive-feature deferral. Incremental parsing needs separate analysis before implementation.

Preserve reference-link resolution, lists, tables, colon containers, comments, and proposed-plan detection.

Treat the complete final source as authoritative. Keep full final rendering and current sanitization.

## 7. Limit geometry work after height changes

Stable rows do not eliminate real height changes. New text can wrap onto another line.

`batchUpdateItemHeights()` updates the reactive height cache. `positions` then rebuilds the full cumulative position array.

The synthetic benchmark applies 60 height updates:

| Visual rows | Total position recalculation time |
|---:|---:|
| 500 | 25.95 ms |
| 2,000 | 102.81 ms |
| 10,000 | 1,038.63 ms |

Candidate: retain positions before the earliest changed row. Update only the affected suffix.

For a streaming row near the end, most earlier positions remain valid.

Keep render-range and visible-range objects stable when their indices do not change. Emit scroller `update` only for meaningful range changes.

This also avoids repeated content-loading scans in `SessionItemsList.onScrollerUpdate()`.

A cumulative-height index, such as a Fenwick tree, could later support O(log N) updates and position lookup.

That option adds complexity. Verify group expansion, insertion, removal, estimated heights, and scroll-anchor restoration before adoption.

Preserve the shared ResizeObserver and its subpixel threshold. Do not replace them with observers per row.

## 8. Reduce automatic height animation

Commit `0e3f8a6a` applies group-reveal animation to live tool arrivals.

`frontend/src/styles/group-reveal.css` animates `grid-template-rows`, changing actual row height during the animation.

Each measured change can trigger geometry work and scroll correction.

Candidate: use opacity and transform for automatic arrivals while retaining final row height.

Manual group expansion can keep a separate height animation. Its frequency differs from automatic stream activity.

This changes the visual effect. Evaluate that trade-off separately from text-buffer changes.

## 9. Batch network deltas

Both provider paths broadcast individual deltas. Each message requires transport, JSON parsing, dispatch, and store handling.

Candidate: concatenate deltas for the same session, message, and block over a short window, initially 20–40 ms.

Flush before stop, end, a block boundary, or lifecycle shutdown. Preserve exact text order and block identity.

Batching can also reduce inactivity-timer rearming. It adds bounded delivery latency.

Measure after frontend changes. A network protocol change has a wider compatibility surface.

## 10. Subscribe clients to relevant streams

The backend sends stream events through the common `updates` group. All connected clients receive visible-session streams.

Existing hidden-session suppression avoids broadcasts for `Session.hidden`. It does not express each client's current visible views.

Candidate: retain structural updates and subscribe each client only to relevant text streams.

Activation needs a complete stream snapshot. Reconnection needs ordering and reconciliation rules to prevent missing or duplicate text.

Multiple visible views and multiple clients can require different subscriptions simultaneously.

This is an architectural option, not a prerequisite for the initial frontend improvements.

## Secondary options

| Option | Purpose | Priority and caution |
|---|---|---|
| Shared scheduler for visible blocks | Coordinate multiple RAF loops and enforce an aggregate budget | Medium; do not starve one visible stream |
| Timestamp-based inactivity checks | Avoid clear/set timer work for every delta | Low; preserve indicator behavior during pauses |
| One source of truth for received text | Reduce duplicate buffer/store state | Medium; measure memory and preserve lifecycle access |
| Direct block lookup | Avoid repeated small-array searches | Low; block arrays are usually small |
| Coalesce structural recomputation | Avoid repeated same-session rebuilds in a burst | Medium; preserve action listeners and arrival ordering |
| Worker for parsing or highlighting | Move compatible CPU work away from input handling | Later; DOM work remains on the main thread |

JavaScript engines can share string representations. Duplicate string fields do not establish an equal duplicate allocation.

Measure retained memory before redesigning text storage. Check copying, snapshots, and retirement before removing any representation.

## Large-session context

The observed redesign session contains 41,907 JSONL items:

| Display level | Items |
|---|---:|
| ALWAYS | 2,893 |
| COLLAPSIBLE | 10,186 |
| DEBUG_ONLY | 28,828 |

These are stored item counts, not mounted DOM counts. Visual-row counts depend on display mode and expanded groups.

This session illustrates why full-history work matters. It does not establish which session or display mode causes every reported freeze.

## Related navigation concern

Session navigation uses a document View Transition. Its pseudo-elements cover the page during the transition.

The session crossfade lasts 250 ms. Watchdogs cover delayed capture and update, including a session-specific 800 ms update timeout.

Timers cannot run during a long synchronous main-thread task. Streaming overload can therefore delay these safeguards.

This is a separate mechanism to inspect if navigation remains blocked after streaming improvements.

## Behavior to preserve

- Every received character, in exact order.
- Block identity across start, delta, stop, and end events.
- Complete final text, including when RAF pauses in a background browser tab.
- JSONL replacement of synthetic rows without duplication or lost text.
- Existing handling when real items arrive before stream-end events.
- Thinking open state and group expansion state across replacement.
- Reader scroll position when reading earlier content.
- Native bottom following when the reader stays near the end.
- Visibility and resume behavior across sessions, tabs, dock layouts, and subagent views.
- Conversation-mode filtering and proposed-plan handling.
- Reconnection and process-state cleanup.
- Final Markdown semantics, sanitization, and successful diagram rendering.

## Measurements for a future implementation

Count visual publications, full visual-list recomputations, geometry key reads, range emissions, and Markdown renders.

Count obsolete renders and expensive feature calls. Separate visible consumers from inactive or hidden consumers.

Measure with short and long histories, long prose, large code fences, Mermaid, multiple sessions, and 60/120 Hz scheduling.

Record elapsed CPU time, retained memory, and display lag. Include active-to-hidden-to-active transitions and final-item retirement.

The target is zero full-history work for a content-only update, except geometry work required by an actual height change.

An invisible stream should retain received data without producing visual publications or Markdown renders.

These targets guide later design. They do not define a committed API, implementation scope, or acceptance specification.
