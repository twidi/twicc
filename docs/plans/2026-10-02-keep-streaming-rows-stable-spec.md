# Keep streaming rows stable

Date: 2026-10-02

Status: draft specification for review. Product implementation is not authorized by this document.

Related analysis: [Streaming performance: prioritized analysis](2026-10-02-streaming-performance-analysis.md).

## Outcome

A displayed-text update changes the content of its streaming row without replacing the row or its containing list.

The update locates that row through the existing visual-item cache. It does not scan the conversation.

Vue still updates the rendered text. Geometry updates remain available when the browser measures an actual height change.

This is the first optimization only. It does not claim to eliminate every reported freeze.

## Scope

Change the content-publication path in `frontend/src/stores/data.js`, specifically `_onBufferDrain()`.

Use the existing `localState.visualItemCache[sessionId]` and `setParsedContent()` helper.

Preserve the existing provider-independent parsed-content shape for text and thinking blocks.

Keep structural changes on the existing `recomputeVisualItems()` path.

### Outside this change

- Buffer rate, smoothing algorithm, RAF scheduling, and visibility gating.
- WebSocket messages, backend broadcasts, and provider behavior.
- Markdown scheduling, parsing, highlighting, and Mermaid processing.
- Scroller suspension, position algorithms, and range-emission behavior.
- Tool-arrival animation and document View Transitions.
- Inactivity timing, process indicators, and stream-retirement semantics.
- Real JSONL item storage and parsed-content API changes.
- General store refactoring or another row registry.

## Current behavior

`streamBlockDelta()` appends received text to a block and feeds its adaptive buffer.

The buffer calls `_onBufferDrain(sessionId, blockIndex, displayedText)` when it publishes a displayed prefix.

The action currently performs these operations:

1. Find the active block in `streamingBlocks[sessionId].blocks`.
2. Store `block.displayedText`.
3. Compute the synthetic line number from `SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - blockIndex`.
4. Scan `sessionVisualItems[sessionId]` with `findIndex()`.
5. Build a parsed-content envelope.
6. Copy the visual row and replace its array entry.
7. Set the replacement row in the visual-item cache.

The visual row's structural identity has not changed. Its replacement nevertheless invalidates `useVirtualScroll.positions`.

That computed value maps every visual row. Virtualization limits mounted DOM rows, not this calculation.

## Investigated contracts

### The existing cache indexes the current visual list

`recomputeVisualItems()` builds `stableItems` and `newCache` together.

For every item in the current store visual list, both contain the same row object. The map key is `vi.lineNum`.

The method then replaces the session's cache and visual list synchronously. A later buffer callback reads the current cache.

Rows filtered out by display mode or group state are absent from both the current list and its cache.

The scroller receives `displayItems` from `useListExit()`. That list can temporarily retain removed rows during an exit animation.

Those exit rows can remain mounted after leaving the store visual list. They are outside the current cache's ownership.

An empty visual-list rebuild creates an empty cache. Session-item cleanup removes both structures.

The cache is therefore the existing lookup index. No second map or retained per-buffer row reference is needed.

### Cached rows support reactive content updates

The cache lives inside Pinia state. Despite its current comment saying "Not reactive", its Map and row access use Vue proxies.

Reading a row from the map returns the same proxy as reading that row from the visual list.

`setParsedContent(row, parsed)` replaces the row's parsed-content property with a new `markRaw()` envelope.

This property replacement is reactive. Changing a nested field inside the raw envelope would not provide the same guarantee.

Templates use `getParsedContent(item)` for the `SessionItem` content prop. They can observe the new envelope without row replacement.

Correct the misleading cache comment as part of this change. Keep the cache internal to the store.

### Structural recomputation already forwards parsed content

The stabilizer compares visual-row properties through `visualItemEqual()`, which excludes parsed-content value comparison.

When it reuses a cached row, it forwards new parsed content through `setParsedContent()`.

The streaming-content update follows this existing property-update pattern. It does not redefine structural equality.

### Retirement flushes before removing the block

`_retireStreamingBlocks()` flushes the buffer before removing the matched block and rebuilding the visual list.

That flush can invoke `_onBufferDrain()`. The new action must still update the current synthetic row at that point.

Existing UUID matching, detail-state transfer, height bridging, and final-item replacement stay unchanged.

## Options considered

| Option | Benefit | Cost or risk | Decision |
|---|---|---|---|
| Update the current cached row in place | Removes list scan and content-only row replacement | Requires reactive property replacement | Selected |
| Retain a row reference inside each buffer | Direct access | Reference can become stale after structural changes | Rejected |
| Add a separate streaming-row index | Direct access | Duplicates cache ownership and cleanup | Rejected |
| Redesign the scroller to ignore content-only array replacement | Broader separation of geometry and content | Changes a shared component and leaves the list scan | Deferred |

## Required behavior

### Publication algorithm

`_onBufferDrain()` keeps its current arguments and block lookup.

1. Return if the session has no streaming state or the block no longer exists.
2. Assign `block.displayedText = displayedText`, including when no visual row exists.
3. Compute the existing synthetic line number.
4. Read the current session cache and call `cache.get(lineNum)`.
5. Return if no cached row exists.
6. Return if the row does not have `syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind`.
7. Build the same text or thinking envelope as today.
8. Call `setParsedContent()` on the cached reactive row.

The kind guard prevents this path from patching another synthetic or real row accidentally.

The action does not mutate the cache membership. The row is already the object indexed by the cache.

### Parsed-content shape

Keep the outer envelope:

```js
{
    type: 'assistant',
    syntheticKind: SYNTHETIC_ITEM.STREAMING_BLOCK.kind,
    message: { role: 'assistant', content: [contentBlock] },
}
```

For text, keep `{ type: 'text', text: displayedText }`.

For thinking, keep `{ type: 'thinking', thinking: displayedText, streaming: !block.stopped }`.

Create a new envelope for each publication. Do not mutate the previous raw envelope.

Do not parse or write `item.content`. Use the parsed-content helpers exclusively.

### Identity and performance guarantees

For a content-only publication:

- The visual-list reference stays unchanged.
- The target visual-row reference stays unchanged.
- Every other visual-row reference stays unchanged.
- Cache membership and cached row references stay unchanged.
- The target receives a new parsed-content envelope.
- The action does not call `recomputeVisualItems()`.
- The action does not scan, copy, or replace the visual list.
- Lookup uses `Map.get()` and does not grow with conversation length.

Finding the active block may still scan the small block array. That independent optimization is outside this scope.

The complete callback is not declared O(1). Text-prefix allocation and downstream rendering can depend on message length.

### Missing or filtered rows

When the cache or target row is absent, keep `block.displayedText` current and skip the visual patch.

Do not rebuild the list, create a hidden row, or fall back to a list scan.

When a later structural recomputation includes the block, it builds content from the latest `block.displayedText`.

This preserves Conversation-mode filtering and collapsed-group behavior.

A removed row retained for exit animation receives no further publications once it is absent from the cache.

Its exit can show the previous displayed prefix. Reopening builds or reuses the current store row with the latest displayed text.

Do not patch exit-only row references or extend their lifetime to keep streaming.

### Structural changes

Block start, stop, inactivity transitions, group toggles, mode changes, and real-item arrivals keep their existing rebuild behavior.

Those operations may legitimately replace the list or row. Stability applies only to content-only buffer publications.

Each publication looks up the current cache. It must not update a row captured before a structural rebuild.

Synthetic line numbers can be reused by later messages. Preserve existing buffer destruction and message lifecycle checks.

The kind guard does not replace message identity checks. Do not expand this change into stream-lifecycle redesign.

### Rendering and scroll

The rendered content must show the latest published text for both Claude and Codex paths.

While the row remains mounted, content-only publication must preserve its `VirtualScrollerItem`, `SessionItem`, and provider message-component instances.

Existing content-dependent child transitions remain valid. For example, streamed `<proposed_plan>` text changes Codex's inner content subtree.

This change must not freeze those branches or require every Markdown/TextContent child to retain its instance.

Structural filtering, virtual-range removal, and exit completion may still unmount a row through their existing behavior.

The content update itself must not invalidate scroller positions when keys and measured heights remain unchanged.

A real height change must still reach the shared ResizeObserver and existing scroll-anchor handling.

Do not suppress measurement, freeze heights, or change automatic bottom following.

## Analysis probes

### Geometry invalidation probe

With 2,000 synthetic rows and 60 content updates, row replacement causes 120,000 geometry key reads.

Stable-row updates through `setParsedContent()` cause zero geometry key reads when heights do not change.

This uses the actual `useVirtualScroll` composable, not a replacement position algorithm.

### Reactive cache and scoped-slot probe

A second probe uses Pinia state, Vue's custom renderer, scoped slots, and the actual scroller composable.

It mirrors the cache/list ownership and `getParsedContent()` prop flow. It does not mount the application's actual SFCs.

Observed results:

- The cache lookup and visual-array access return the same reactive row.
- After 60 publications, the content component's final text matches the latest publication.
- Geometry key reads remain zero after initial setup.
- The content component mounts once.
- After replacing both cache and list structurally, the next publication reaches the new row.

These probes support the design. Browser layout, actual provider SFCs, and retirement remain implementation validation requirements.

## Acceptance criteria

| Case | Required result |
|---|---|
| Repeated text publications | Latest text renders; row, list, and cache references remain stable |
| Repeated thinking publications | Latest thinking renders; `streaming` reflects the current stopped flag |
| Missing streaming state or block | No content mutation and no exception |
| Missing cache or target row | Displayed text remains current; no list rebuild or fallback scan |
| Cache entry with another synthetic kind | No visual patch |
| Structural rebuild during an active stream | Next publication updates the current row, not the previous row |
| Filtered stream becomes visible | Rebuilt row uses the latest displayed text |
| Streaming group collapses and reopens | Exit-only row is not patched; reopened row shows the latest displayed text |
| Codex stream introduces a proposed plan | Outer row and message instances stay mounted; existing inner plan transition renders correctly |
| Buffer flush during retirement | Complete text remains correct; final item appears once |
| New message reuses a block index | Current text updates; previous buffer does not regain ownership |
| No measured height change | Content update causes zero full-list geometry key reads |
| Text wraps and height changes | Measurement, bottom following, and reader scroll preservation still work |

## Validation strategy

Use deterministic identity and key-read assertions rather than elapsed-time thresholds.

Exercise production publication logic. Tests must not copy its implementation into a separate mock function.

If Node cannot import the full store graph, isolate the smallest publication helper and call it from the action.

Any such helper must remain provider-independent and preserve the algorithm above. Do not create another row registry for testability.

Use Vue and Pinia in reactive tests. A plain-object mutation test cannot establish template notification.

Mount the actual provider rendering path for text and thinking during product validation. Test Claude and Codex.

Cover repeated updates, missing rows, cache replacement, display-mode changes, final-item retirement, and synthetic-key reuse.

Also cover collapse/reopen during streaming and content-dependent transitions such as a Codex proposed plan.

Retain existing stream-swap tests and behavior. Run frontend tests with `cd frontend && npm test` after implementation.

Browser validation must check changed text, stable component lifetime, real height measurement, and scroll behavior.

A mobile Performance trace is not a prerequisite for this step. The deterministic geometry regression supplies the performance evidence.

## Expected benefit and remaining limits

Remove the conversation-length lookup and row-replacement geometry invalidation from each buffer publication.

This benefit applies even when a cached session continues receiving text. It does not stop that session's buffers or Markdown renders.

Actual height changes still recalculate positions through the existing algorithm. Long Markdown and high publication rates remain separate work.

No user-visible settings, network protocol, backend changes, or dependencies are required by this design.

## Adversarial review record

An internal subagent reviews this specification against the source on 2026-10-02. The review completes in two rounds.

Round 1 finds two actionable ambiguities, with no blocking design issue:

- Component stability must apply to outer row and provider message instances, while allowing existing content-dependent child transitions.
- Cache ownership covers the current store visual list, excluding removed rows temporarily retained for exit animation.

Both findings are verified against the source and corrected. Acceptance criteria cover proposed-plan transitions and streaming group collapse/reopen.

The probe description also narrows its claim to the final rendered value after 60 publications.

Round 2 reports no remaining blocking or actionable findings.

This is a specification and source review. Actual SFC notification, browser geometry, scroll behavior, and lifecycle validation remain required during implementation.
