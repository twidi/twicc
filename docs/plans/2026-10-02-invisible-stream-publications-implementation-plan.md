# Invisible Stream Publications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development when the user selects delegation. Execute each task's test cycle before the next task.

**Goal:** Stop adaptive visual publications for invisible streaming bodies while preserving received text and visible conversation behavior.

**Architecture:** A client-local registry aggregates consumer tokens by session, message, and block. Buffer suspension follows that aggregate and document visibility. Conversation views, shared row observers, and detail-body owners supply eligibility.

**Tech Stack:** Vue 3, Pinia, existing adaptive RAF buffer, IntersectionObserver, node:test, Vite browser fixtures. No new dependency.

**Spec:** `docs/plans/2026-10-02-invisible-stream-publications-spec.md`, committed as `aeaf1838`.

## Global constraints

- Work only in `/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows`, branch `bugfix/stable-streaming-rows`.
- Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows &&`.
- Preserve unrelated files and index entries. Commit only owned paths, with a descriptive body and the current Codex model trailer.
- Keep every written artifact in English. Do not add dependencies, migrations, backend changes, or persisted visibility state.
- Keep all received deltas. Preserve indicators, detail-state transfer, final reconciliation, and existing scroll behavior.
- Keep priority 1's stable row/list/cache identity for content-only publications.
- Use a 200 px vertical prefetch margin, independent of the 5,000 px render and 10,000 px unload buffers.
- Do not use selected-session identity, route ownership, `Session.hidden`, or component mounting alone as visibility.
- Do not scan historical items, consumers, or DOM geometry on each delta.
- Do not start/restart the main instance. Worktree startup requires user authorization. Do not install packages manually.
- Do not claim that this change fixes the reported global freeze.
- This plan authorizes no implementation. Complete all tasks before treating intermediate commits as a usable feature.

## Review focus

1. A second visible view must not force an existing view's displayed prefix forward. Task 2 tests shared ownership and bootstrap.
2. A canceled RAF can arrive after the same block resumes. Task 1 tests request generation before any mutation.
3. A tool dock can own the route while Chat remains visible. Task 4 tests center visibility separately from route ownership.
4. A closed thinking block still has a visible spinner. Tasks 3 and 4 test lifecycle state without body publications.
5. Empty rows and delayed observer delivery can prevent first visibility. Tasks 2 and 4 test cold bootstrap and unknown observations.

## Source map and file responsibilities

| File | Responsibility |
|---|---|
| Modify `frontend/src/utils/streamingBuffer.js` | Buffer pause, catch-up, safe scheduler, registry binding and disposal |
| Create `frontend/src/utils/streamingBuffer.test.js` | Controlled RAF and buffer state tests |
| Create `frontend/src/utils/streamPublicationRegistry.js` | Block bindings, token ownership, document visibility, aggregate transitions |
| Create `frontend/src/utils/streamPublicationRegistry.test.js` | Pure ownership and bootstrap tests |
| Modify `frontend/src/stores/data.js` | Captured message guards, gated buffer initialization, suspended structural snapshots |
| Modify `frontend/src/stores/streamingRows.test.js` | Existing stable-row tests plus actual action-source lifecycle coverage |
| Create `frontend/src/composables/streamPublicationKeys.js` | Shared Vue injection symbols; no component imports |
| Create `frontend/src/composables/useStreamingPublication.js` | Scoped consumer and block-identity ownership |
| Create `frontend/src/composables/useStreamingPublication.test.js` | Vue scope, KeepAlive-equivalent activity, and identity refresh tests |
| Create `frontend/src/utils/rowVisibilityObserver.js` and `.test.js` | Shared observer registration, stale-target protection, teardown |
| Modify `frontend/src/components/virtual-scroller/virtualScrollerKeys.js` | Shared row-visibility observer key |
| Modify `frontend/src/components/virtual-scroller/VirtualScroller.vue` | Own one row IntersectionObserver per scroller; retain existing sentinel observer |
| Modify `frontend/src/components/virtual-scroller/VirtualScrollerItem.vue` | Register wrapper and provide row intersection state |
| Modify `frontend/src/composables/useSessionLayout.js` | Export the canonical center-visibility predicate |
| Modify `frontend/src/components/session/layout/SessionLayout.vue` | Use the same predicate for center `v-show` |
| Modify `frontend/src/views/SessionView.vue` | Pass actual main, ephemeral, and subagent view activity |
| Modify `frontend/src/components/session/detail/SessionContent.vue` | Forward subagent view activity |
| Modify `frontend/src/components/session/detail/SessionItemsList.vue` | Provide shown/reveal/active view context |
| Modify `frontend/src/components/session/detail/SessionItem.vue` | Own text or visible raw-JSON consumers; provide live block identity |
| Modify `frontend/src/components/session/detail/items/claude_code/ThinkingContent.vue` | Own open/closing formatted thinking body |
| Modify `frontend/src/components/session/detail/items/codex/Reasoning.vue` | Own open/closing formatted reasoning body |
| Create `frontend/tests/browser/invisibleStreaming.html` and `.js` | Full conversation fixture, controlled events, read-only diagnostics |
| Update this plan's execution record | Commands, browser results, review findings, and limitations |

Do not modify generic Markdown rendering, geometry algorithms, or backend broadcasts.
The old row-only browser fixture remains a priority-1 test. It is not the scroll acceptance fixture for this feature.

## Task 1: Add safe suspended buffer operations

**Interfaces produced in `streamingBuffer.js`:**

- `initBuffer(sessionId, blockIndex, onDrain, { messageId, publicationIdentity, active, visibilityManaged }) -> void`.
- `setBufferActive(sessionId, messageId, blockIndex, active) -> boolean`: false for a missing/mismatched buffer.
- `snapshotBuffer(sessionId, messageId, blockIndex) -> boolean`: publish a changed full snapshot without starting RAF.
- `isBufferActive(sessionId, messageId, blockIndex, publicationIdentity = null) -> boolean`: if supplied, the identity must match the current buffer generation.
- `flushBuffer(sessionId, blockIndex) -> string | null`: retain visible flush behavior; hidden flush advances the internal cursor without `onDrain`.
- Existing feed/destroy exports retain their call signatures.

During Tasks 1–2, default `active` to true and `visibilityManaged` to false for existing callers.
Unmanaged buffers preserve legacy behavior during this transition. Task 3 changes both defaults and every production call.
Managed buffers always initialize suspended. Their registry aggregate overrides `active`, including an explicit true value.
An unmanaged active buffer is permitted only as a legacy transition or an isolated buffer test, never as final production behavior.

- [ ] **Step 1: Write RED tests** in `streamingBuffer.test.js` with fake `performance.now`, RAF, and cancellation. Restore globals in test cleanup.

Required cases:

```js
// hidden block, 1,000 feeds
assert.equal(scheduledFrames, 0)
assert.equal(publications.length, 0)
setBufferActive('session', 'message', 0, true)
assert.deepEqual(publications, [allReceivedText])
assert.equal(pendingFrames.size, 0) // catch-up alone needs no frame
```

Also test visible adaptive draining; unchanged snapshot; hidden flush; visible flush; missing/mismatched identity; destruction; fractional/rate reset.
Test A/B explicitly: schedule A, suspend, resume, feed a later delta to schedule B, deliver canceled A manually.
Assert unchanged displayed cursor, frame time, fractional accumulator, publications, and B ownership. Deliver B and verify exactly one loop.

- [ ] **Step 2: Run RED command.** `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/utils/streamingBuffer.test.js`.
Expected: missing gated operations or hidden publication failures. Record actual failure names.

- [ ] **Step 3: Implement the interfaces.** Retain `fullText`, pause without discarding it, and reset arrival history on catch-up.
Every frame closes over an expected request token and validates it before touching state.
Suspension, snapshot cancellation, flush, destroy, and replacement invalidate scheduler ownership.
Use message identity checks for externally requested activation. Do not use a delayed callback's current eligibility as its only guard.

- [ ] **Step 4: Run GREEN and related tests.** Run the new file and `frontend/src/stores/streamingRows.test.js`.
Existing behavior tests remain green while production visibility wiring is absent.

- [ ] **Step 5: Commit owned files.** Suggested subject: `refactor(streaming): add safe buffer suspension and catch-up`.

## Task 2: Aggregate block-local consumers and document visibility

**Files:** Registry and registry tests; buffer binding changes and buffer tests from Task 1.

**Interfaces produced in `streamPublicationRegistry.js`:**

- `createStreamPublicationRegistry({ document }) -> registry` for deterministic tests.
- `streamPublicationRegistry`: the client-local singleton. No store, component, or buffer imports.
- `createStreamPublicationIdentity(sessionId, messageId, blockIndex) -> frozen identity`: allocate one new immutable generation per block lifetime.
- `registry.bindBlock(identity, { setActive, snapshot }) -> releaseBinding`.
- `registry.acquire(identity, state) -> token`.
- `registry.update(token, state) -> void`.
- `registry.release(token) -> void`.
- `registry.dispose() -> void` for test teardown/client shutdown.

`identity` is `{ sessionId, messageId, blockIndex, generation }`.
The pure factory allocates a client-local monotonic generation and freezes the identity object.
Registry records distinguish generation, including reconnects with unchanged message fields.
Reuse the exact same identity object across buffer bindings, canonical blocks, visual rows, and consumer tokens.
`state` is `{ viewActive: boolean, bodyActive: boolean, intersection: 'unknown' | 'inside' | 'outside' }`.
Aggregate active means at least one inside token with active view/body, plus a visible document.
Callbacks invoke Task 1's active/snapshot operations. They never recompute visual items.

- [ ] **Step 1: Write RED ownership tests.** Assert two eligible tokens activate once; releasing one keeps the buffer active; releasing the last suspends once.
Add duplicate and late release, pending acquisition before binding, identity mismatch, document hide/show, and disposal tests.

Test bootstrap: unknown eligible owners can snapshot once but cannot activate RAF.
Mount an empty block before any delta; deliver its first observation and verify activation.
Reserve a bootstrap while text is empty, feed hidden deltas, then observe inside. Verify exactly one current-text catch-up.
Rebind the same session/message/index with a new generation. Old tokens cannot activate that binding.
Two concurrent unknown owners share one reservation. An active block never snapshots for another owner.
Hidden document, hidden view, and closed body cannot bootstrap.
After the reserving owner is observed or disposed, reservation cleanup must not suppress a remaining owner's future inside activation.
Changing a token's state replaces its contribution atomically, without a transient remove/add catch-up.

- [ ] **Step 2: Run registry RED tests.** `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/utils/streamPublicationRegistry.test.js`.

- [ ] **Step 3: Implement registry records.** Maintain aggregate counts per block; update only the affected record for a token operation.
Document visibility events can visit bound records once. Delta delivery never visits token sets.
The first block binding attaches the document listener. The last binding removes it.
A released binding invalidates its tokens. Old tokens cannot acquire a later block binding, even with identical identity fields.
A newly created owner can acquire before its new buffer binds. Preserve that pending contribution.

- [ ] **Step 4: Bind registry lifecycle in `initBuffer` only when `visibilityManaged` is true.** Store each binding's disposer with its buffer.
Managed initialization requires a publicationIdentity from the pure factory; verify its session/message/block fields against the call.
Each binding callback verifies that the map still contains its exact buffer instance before acting.
Update managed test fixtures now: acquire eligible tokens instead of relying on `{ active: true }`.
Keep existing unmodified action fixtures unmanaged until Task 3. This preserves Task 2's required GREEN cycle.
Install the buffer in the map before binding so synchronous catch-up can resolve it.
Destroy and delete the old buffer/binding before replacing the same key.
Flush/destroy/session cleanup/all cleanup dispose bindings exactly once. They do not dispose the registry singleton permanently.

- [ ] **Step 5: Run registry, buffer, and existing streaming-row tests GREEN.** Assert zero retained bindings/listeners after each test.

- [ ] **Step 6: Commit owned files.** Suggested subject: `feat(streaming): aggregate visible block consumers`.

## Task 3: Integrate canonical text and generation-safe store publication

**Files:** `data.js`, `streamingRows.test.js`, and buffer default option.

**Interfaces consumed:** Task 1 buffer operations. Production `streamBlockStart` passes `{ messageId, publicationIdentity, active: false, visibilityManaged: true }`.
The store imports only the registry module's pure identity factory. The buffer owns singleton bindings.
No registry module imports the store or buffer.

- [ ] **Step 1: Extend actual action-source fixtures with RED cases.** Keep the existing extraction pattern, without importing the full extensionless store graph into Node.
Update dependencies only for helpers actually used by the actions.

Test no-consumer initialization, 1,000 hidden deltas, one activation snapshot, visible final flush, hidden retirement, next-message index reuse, and reconnect cleanup.
Test inactivity timers while suspended: stopped state changes structurally, with no body drain.
Test cache reconstruction after an empty bootstrap and hidden deltas. The rebuilt row contains canonical full text, without replaying an old prefix.
Assert full `block.text`, zero hidden `_onBufferDrain` calls, and stable visual rows.
Use real production recomputation in the later browser fixture; Node fixture rebuilds must remain labeled synthetic.

- [ ] **Step 2: Run targeted RED tests.** `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/stores/streamingRows.test.js`.

- [ ] **Step 3: Update store initialization and publication.** Set `active` default to false and `visibilityManaged` default to true.
Every production start creates and stores one publicationIdentity on its canonical block before initializing its managed buffer.
Capture that identity in the drain callback. Verify both message fields and the current block's generation before calling `_onBufferDrain`.
Propagate the exact identity object onto each synthetic visual row before stabilization, alongside syntheticKind.
`visualItemEqual` already compares every direct field except `_parsedContent`; no custom comparator exclusion is allowed for this identity.
A new block lifetime may replace the row structurally. Content-only drains keep the existing row identity.
Keep `_onBufferDrain`'s stable current-cache publication behavior. Skip parsed-envelope replacement when the prefix and content flags are unchanged.
Do not gate `block.text`, inactivity timers, stopped flags, or structural lifecycle updates on consumer presence.

In `computeVisualItems`, use `isBufferActive(sessionId, streaming.messageId, block.blockIndex, block.publicationIdentity)`.
Use `block.displayedText` while active; use `block.text` while suspended. Never treat an empty displayed string as missing content.
A resumed missing row still advances `displayedText` once; later recomputation materializes current text.
An extra visible consumer must never change the builder to canonical full text while another consumer is smoothing.

- [ ] **Step 4: Audit every flush/destroy call.** Cover `_retireStreamingBlocks`, `clearEndedStreamingBlocks`, `_dropOrphanedStreamingBlocks`, process cleanup, new-message replacement, session deletion, and process snapshots.
Canonical text already holds hidden deltas. Do not depend on an `onDrain` callback to retain final content.
Retain existing detail/group state transfers before deleting a block. Disposal prevents late old-generation publications.

- [ ] **Step 5: Run GREEN buffer/registry/store tests.** Update old visible-buffer fixtures to request active test buffers or acquire explicit eligible tokens.
Do not globally force production buffers active to preserve old test assumptions.
Audit all production initBuffer calls: no unmanaged option or legacy default remains.
Old unmanaged tests must explicitly opt out; actual store-action tests use managed buffers and eligible tokens.

- [ ] **Step 6: Commit owned files.** Suggested subject: `feat(streaming): retain hidden text without visual drains`.

## Task 4: Wire actual conversation, row, and body eligibility

**Files:** All Vue/composable/row-observer files in the source map.

**Interfaces produced:**

- `useSessionLayout` returns `centerVisible: ComputedRef<boolean>` from `!maximizedRegion.value || isCenterMaximized.value`.
- `SessionLayout` uses `layout.centerVisible.value` for its existing center `v-show`; remove the duplicate local predicate.
- `SessionItemsList` and `SessionContent` accept `viewActive: boolean`, default false. Audit every production mount before enabling publication.
- `STREAMING_VIEW_CONTEXT`, `STREAMING_ROW_CONTEXT`, `STREAMING_BLOCK_CONTEXT`: symbols in `streamPublicationKeys.js`.
- `ROW_VISIBILITY_OBSERVER_KEY`: symbol alongside the resize-observer key.
- `createRowVisibilityObserver({ root, IntersectionObserver }) -> { observe(element, onState), disconnect() }`.
  `observe` returns a disposer; callbacks produce `'inside'` or `'outside'`.
- `useStreamingPublication({ identity, bodyActive }) -> void`: consumes injected view/row context, owns a registry token and cleans it on scope disposal.

- [ ] **Step 1: Write RED row-observer and scoped composable tests.** Use controlled observer deliveries and a real Vue renderer/Pinia scope where required.
Assert one observer, rootMargin `200px 0px`, unknown initial state, separate rows, unobserve/disconnect, and ignored stale target delivery.
Test zero viewport height even when IO reports inside; no token is eligible.
Test suspension, resize recovery, retargeting, and old observation delivery after the new generation reports inside.
Assert no token for missing context, no body ownership for real JSONL items, one token per owner, and release on unmount/deactivation.
On view/reactive identity change, replace tokens without retaining the previous block. A reconnect replacing a block object renews ownership even if message fields match.
Retain an old exit-only row, start a new generation at the same index, and filter the new row. The retained old row must own no valid token.
Restore the new row and verify its identity, token, and canonical text independently.

- [ ] **Step 2: Implement shared row observation.** VirtualScroller provides registration independently of its existing ResizeObserver and bottom-sentinel observer.
VirtualScrollerItem provides a row ref initially `'unknown'`, observes on mount, and disposes on unmount.
Use root-local intersection plus ancestor clipping, not the renderRange or unloadBuffer as visibility.
A root replacement disconnects the old observer and resets affected states to unknown before retargeting.
Add `scrollerActive = !composableSuspended && measuredViewportHeight > 0` to row context.
Maintain measuredViewportHeight from the existing container ResizeObserver, including zero on initial measurement. Do not read layout on each delta.
Gate both bootstrap and inside eligibility with scrollerActive. An expanded rootMargin never overrides a zero-height viewport.
On suspension or retargeting invalidate the observation generation. On recovery, reobserve retained rows with state unknown.
Discard callbacks from previous observation registrations before updating row state.

If IntersectionObserver is unavailable, treat mounted rows as inside but retain view/body/document gates.
Document that degradation: offscreen savings are unavailable; visible output must not silently stop.
Do not add geometry polling as a fallback. Tests cover this degraded path separately from strict observer acceptance.

- [ ] **Step 3: Implement scoped ownership.** Use synchronous watches for loss of explicit view/body eligibility.
The view context combines explicit `viewActive`, existing `sessionActive`, the view's reveal state, and `showVirtualScroller`.
The composable also clears ownership through KeepAlive deactivation and scope disposal. Reactivation refreshes current block identity.
Identity resolution validates the row's immutable publicationIdentity against the current canonical streaming entry/block.
Never derive a new identity from lineNum and the current store alone. Never mutate an old exit-only row's stamp to the current generation.
A restored/replaced row supplies its new stamp through its prop, which renews scoped ownership.

- [ ] **Step 4: Wire exact view predicates.** In SessionView, pass `isActive && layout.centerVisible.value && centerActiveTab === 'main'` to the main center Chat.
Pass the same center predicate with `centerActiveTab === tab.id` to each subagent SessionContent.
The ephemeral direct Chat uses `isActive` without a center-tab condition.
SessionContent forwards `viewActive` to its SessionItemsList. SessionItemsList adds reveal/loading gating locally.
Do not gate center Chat on `activeTabId === 'main'`: a shown tool dock can own the route.
Test main, subagent, ephemeral, center maximize, tool-dock maximize/restore, and inactive center tool tabs.

- [ ] **Step 5: Wire body owners.** Pass `item.publicationIdentity || null` to SessionItem in both normal and expanded/closing group-head branches.
SessionItem accepts only a synthetic row stamp matching the current canonical block's publicationIdentity and message fields.
Provide this identity to descendants. Own text publication at the row regardless of empty text or proposed-plan rendering.
Claude ThinkingContent and Codex Reasoning own formatted thinking only while `isOpen || isClosing()`.
Guard WA events against nested bubbling while preserving existing detail-state writes.

Treat visible raw JSON as another visible block-body consumer. SessionItem owns it when `showJson` is true, for text or thinking.
Formatted thinking unmounts in raw mode, so it releases its token. Raw JSON has no open-detail requirement.
This preserves the existing visible debug view; it does not add Markdown work to closed thinking.

- [ ] **Step 6: Run GREEN integration and related tests.** Run all new files plus streamingRows, existing detail-motion, group-reveal, chat-entrance, layout, and virtual-scroll tests.
Browser SFC assertions in Task 5 cover prop wiring that Node cannot mount directly.
Keep read-only ShareItemsList outside live ownership. Do not import the data store from the registry or row observer.

- [ ] **Step 7: Commit owned paths.** Suggested subject: `feat(chat): report streaming body visibility`.

## Task 5: Validate the full production conversation and record evidence

**Files:** New full browser fixture; this plan's execution evidence. No new product API for test convenience.

- [ ] **Step 1: Create a fixture rooted in the actual SessionView, with SessionItemsList, SessionContent, SessionLayout, useSessionLayout, and provider SFCs.**
Use a memory router, Pinia, required WA imports, production CSS, and no `main.js` or live WebSocket.
Use synthetic session IDs and controlled store stream actions. Set fixture sessions fetched with seeded metadata/content to avoid first-load calls.
For remaining HTTP reads, install a fixture-local fetch responder that accepts only documented read routes and synthetic IDs.
Reject mutation requests and unexpected reads. Record every substitute. Do not replace scroller, detail, reveal, or visibility algorithms.
Use actual display-mode/group actions and actual `addSessionItems` reconciliation with provider-specific final IDs/UUIDs.

Register memory-router routes with the production projectId/sessionId/tab parameters and seed the fixture project/session records before mounting.
Supply center tab selection through real WA tabs and useSessionLayout's actual maximize/restore and route predicates.
Do not copy SessionView's visibility predicate into the fixture. Exercise its actual prop wiring through the mounted SessionView.
A small secondary two-view fixture can mount two SessionItemsList instances, but it cannot replace the primary SessionView scenario.

- [ ] **Step 2: Add diagnostics at actual boundaries.** Count registry aggregate transitions, buffer frame scheduling, drain calls, and parsed-envelope changes.
Instrument with fixture-local wrappers; do not count the independent scroller/animation RAF as buffer work.
Record target component UIDs, final rendered text, and physical scroll geometry.
Reset counters after observer/reveal settling and before feeding hidden deltas. Separate cold bootstrap and structural snapshots.
A no-consumer fixture run feeds 1,000 deltas and asserts zero buffer RAF, drains, and parsed-envelope replacements.

- [ ] **Step 3: Execute controlled browser scenarios for both providers.**

| Scenario | Required result |
|---|---|
| Visible text/thinking | Smooth updates and stable outer row instances |
| Codex proposed-plan text | Visible body updates through dispatch changes |
| Closed thinking | Zero body drains; spinner/stop state correct |
| Close/reopen during fold | Closing body stays current; reopening catches up once when needed |
| Actual group close/reopen | Retained closing content finishes normally; reopen shows canonical text |
| Display mode filter/restore | No hidden drains; restored row shows current content |
| Cache rebuild after hidden bootstrap | Canonical text replaces the stale prefix without restarting RAF |
| Empty mount before first delta | First observation and later delta start normal streaming |
| Zero-height scroller/recovery | No draining at zero height; positive resize resumes with fresh observation |
| Offscreen outside 200 px margin | Hidden delta counters stay zero after settling |
| Return to row | Current full text appears once; future deltas smooth |
| Another center tab | Hidden body has no drains; switching back catches up |
| Tool dock owns route | Shown center Chat continues draining |
| Tool dock maximized | Center Chat suspends; restore catches up |
| Subagent selected | Correct subagent drains; hidden main Chat suspends |
| KeepAlive session switch | Inactive consumers release; return resolves current generation |
| Two views of one block | One view can hide without stopping the other; new view does not jump shared prefix |
| Document hidden/shown | No hidden scheduling; one resume catch-up |
| Final real-item replacement | No duplicate, missing text, or stale spinner; detail state transfers |
| Next message uses same index | Old callbacks and tokens cannot alter the new row |
| Reconnect with identical message fields | Old generation stops; mounted owners acquire the replacement generation |
| Raw JSON view | Visible synthetic content updates; formatted-body ownership releases |

- [ ] **Step 4: Validate scroll with before/after comparison.** Use the same full fixture and stream sequence on base `aeaf1838` and the implementation head.
Prepare two temporary modules from `git show aeaf1838:frontend/src/stores/data.js` and `git show aeaf1838:frontend/src/utils/streamingBuffer.js`.
Place them beside their originals as `invisibleStreamingBaselineData.js` and `invisibleStreamingBaselineBuffer.js`.
Change only the copied baseline store's buffer import to the copied baseline buffer.
Select the baseline through a fixture query parameter, then create its `defineStore('data')` instance first in a fresh Pinia before mounting SessionView.
Other production components retrieve that same Pinia store ID. Confirm by asserting their store instance matches the baseline instance.
A fresh page navigation separates baseline and implementation runs. Never mix both data stores in one scenario.
Keep the actual SessionView/conversation/scroll hierarchy unchanged between runs. New publication owners cannot control the unbound base buffer.
Record these adapters and remove the temporary modules before commit.
Never revert product files in the main checkout to run a baseline.

Assert bottom gap stays within the production near-bottom threshold and reaches the end after rendering settles.
Assert reading-above preserves the visible anchor, including catch-up and final replacement. Record tolerances and actual measured movement.
Do not infer a product regression from a fixture failure without a working baseline.
If baseline bottom following fails, fix the fixture fidelity before accepting scroll results.

- [ ] **Step 5: Run final checks.**
`cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && node --test frontend/src/utils/streamingBuffer.test.js frontend/src/utils/streamPublicationRegistry.test.js frontend/src/utils/rowVisibilityObserver.test.js frontend/src/composables/useStreamingPublication.test.js frontend/src/stores/streamingRows.test.js`.
Then `cd /home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows && npm --prefix frontend test` and `git diff --check`.
If dependencies or a worktree browser server are unavailable, record the gap. Do not substitute the main instance or install/start without authorization.
No Python tests are required for frontend-only changes.

- [ ] **Step 6: Record evidence and obtain independent implementation review when authorized.** Include exact heads, commands, counts, provider scenarios, browser errors, scroll results, and unexecuted cases.
Review the whole implementation against the committed spec and this plan. Correct findings and repeat affected verification.
Do not mark Task 5 complete while required controlled lifecycle or scroll cases remain unexecuted.

- [ ] **Step 7: Commit fixture and evidence.** Suggested subject: `test(streaming): validate invisible streams in full conversations`.

## Plan self-review

- Buffer gating, catch-up, scheduler generations: Task 1.
- Per-view aggregation, bootstrap reservations, document visibility, disposal: Task 2.
- Canonical text, cache rebuilds, lifecycle timers, retirement, reconnect: Task 3.
- Actual center/dock, subagent, detail, row intersection and scoped ownership: Task 4.
- Performance counters, production scroll, final provider items, and honest acceptance evidence: Task 5.
- No execution dependency requires new packages, backend changes, or main-instance operations.
- Observer-unavailable and raw-JSON behavior are derived compatibility requirements made explicit in this plan.

## Adversarial review record

An internal subagent reviews this plan against the committed specification and frontend source on 2026-10-02.
The review loop completes in two rounds.

The first round reports four actionable findings:

1. An old exit-only row can acquire a new block through a reused synthetic line number.
2. Row intersection can permit publication while the scroller is suspended or has zero viewport height.
3. Task 2 cannot meet its required GREEN cycle without a defined transitional binding rule.
4. Required bootstrap, group, display-mode, cache-rebuild, inactivity, and same-field reconnect tests are insufficiently explicit.

The revised plan adds immutable generation stamps, positive-height scroller eligibility, observation generations, and explicit transitional binding defaults.
It also adds executable test cases for the missing lifecycle conditions.

The second round confirms all four findings are resolved. It reports no further concrete blocker in the reviewed plan and source.
This review does not validate product implementation or browser outcomes. No implementation is executed at this stage.

## Execution handoff

Native execution is recommended. These tasks share buffer, registry, and Vue ownership interfaces.
Keep a whole-change independent review before completion when the user authorizes it.
The user authorizes internal subagents for specification and plan reviews. Implementation delegation remains unselected for this priority.
This plan remains unexecuted until the user approves implementation.

## Implementation execution status

Tasks 1–4 have implementation commits on the isolated worktree branch.
Task 5 has a full SessionView fixture and baseline adapter helper. Browser acceptance remains incomplete.
See `docs/plans/2026-10-02-invisible-stream-publications-execution.md` for exact verification and remaining cases.
Independent implementation review belongs to the parent agent. No review acceptance is claimed here.
