# Chat Session Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every selected session event at its chronological position in the chat, independently of display mode and tool expansion.

**Architecture:** A session-owned controller collects complete event snapshots through the existing Timeline API. Pure projection helpers place notices and split displayed tool groups before normal display filtering. The existing chat scroller measures notices and preserves the reading position during structural changes.

**Tech Stack:** Vue 3 Composition API, Pinia, existing REST and WebSocket transports, JavaScript, node:test, existing VirtualScroller.

**Spec:** [Session events specification](2026-10-27-chat-session-events-spec.md). Read the entire specification before executing this plan.

**Status:** Adversarial implementation-plan review complete. Product implementation is not authorized by this document.

## Global Constraints

- Show the events that target the displayed session, except turn events.
- Present individual events as centered chat notices.
- Use a dedicated presentation, without the Timeline's session cards, headers, grouping, or right-hand time column.
- Use `GET /api/timeline/` with `session=<displayed session>` and `view=without_turns`.
- Request up to the existing maximum of 100 events per page.
- No new endpoint, response field, hidden-session exception, or server-side visibility rule is required.
- The segmentation changes the display projection, never stored provider or grouping metadata.
- Events never become candidates for loading JSONL line ranges.
- Public share viewers, event search, event exports, or new event visibility settings remain outside scope.
- Keep event loading and failures separate from the availability of messages and the composer.
- Preserve original transcript lines, tool/result associations, comments, logical conversation blocks, and existing timestamp settings.
- Write code, tests, comments, and UI strings in English. Add no dependencies or backend changes.
- Stay on the current branch. Preserve unrelated changes. Do not update CHANGELOG.

## Review Focus

These five interaction risks need explicit tests in the owning tasks.

1. An old occurrence time arrives during pagination after its historical page was read. Task 1 catches it by ID watermark.
2. An event splits an ALWAYS item's connected suffix after its internal toggle becomes external. Task 3 preserves expansion ownership.
3. An event update and a collapse share one Vue flush. Task 6 keeps notices outside tool animations.
4. A structure change replaces rows above the anchor without changing its index. Task 7 compares geometry and keys, not index alone.
5. Recovery fails after tool segments acquire different states. Tasks 3 and 8 preserve the snapshot and segment states together.

## Execution boundaries

This plan is one feature. Retrieval, projection, and scrolling share one observable contract.
Execute Tasks 1–7 in order. Task 8 verifies their integration.
Each task produces a testable unit. Do not ship a partial unit as the finished feature.

Use the existing node:test conventions. Store and SFC tests currently extract selected functions with injected dependencies.
Extend those fixtures when needed. Prefer executing behavior over assertions that search source text.
For rendered notice tests, reuse `frontend/tests/helpers/scrollerComponentHarness.js` (`compileComponent`, `makeRenderer`, `descendants`, `flush`).
This harness already supports TimelineEventRow tests. Browser checks own actual keyboard behavior and visual wrapping.
Do not introduce a new test framework or install packages.

Every command below starts from the repository root explicitly.
During implementation, each task commit must include a descriptive body and the required current-model Co-Authored-By trailer.
Resolve the current model at execution time. Stage only the task's files.
No commit command below authorizes unrelated changes or a CHANGELOG update.

## File and interface map

| File | Responsibility |
|---|---|
| `frontend/src/utils/chatEvents/history.js` | Pure complete-history controller, staging, cursor, retries, generation guards |
| `frontend/src/utils/chatEvents/placement.js` | Timestamp normalization, source boundaries, deterministic placement |
| `frontend/src/utils/chatEvents/segments.js` | Projected group metadata and inherited segment expansion |
| `frontend/src/utils/chatEvents/rows.js` | Typed event rows, message-only flags, event-aware calendar projection |
| `frontend/src/utils/chatEvents/scroll.js` | Pure structural-scroll capture and restore decision |
| `frontend/src/composables/useSessionEventHistory.js` | Chat eligibility and reactivation calls into the data store |
| `frontend/src/components/session/detail/SessionEventNotice.vue` | One centered notice, text details, accessible time |
| `frontend/src/stores/data.js` | Loaded-session ownership, publication, projection integration, disposal |
| `frontend/src/utils/visualItems.js` | Existing display projection; preserve its event-free behavior |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Notice branch, event status, loading guards, animation membership, scroll transaction |
| `frontend/src/components/session/detail/SessionItem.vue` | Optional projected segment range passed to provider rendering |
| `frontend/src/components/session/detail/items/claude_code/Message.vue` | Forward segment comment range to ContentList |
| `frontend/src/components/session/detail/items/claude_code/ContentList.vue` | Segment-local prefix/suffix controls and comment range |
| `frontend/src/utils/scrollerLoadWindow.js` | Exclude event rows from JSONL loading |
| `frontend/src/composables/useGroupReveal.js` | Optional membership predicate; existing callers keep their default |
| `frontend/src/composables/useChatNavigation.js` | Resolve logical targets against the actual rendered scroller keys |

Create adjacent `*.test.js` files for new pure modules.
Other exact test paths appear in their tasks.
Do not import TimelinePanel, TimelineEventRow, or Timeline session-card components.
Reuse `timeline/feed.js` URL/cursor/merge helpers, `eventTime.js`, `describeEvent.js`, and wording parts where useful.
The dedicated controller avoids changing the Timeline feed's progressive publication and silent live-error policy.

### Shared types

These are JavaScript JSDoc contracts. They do not require TypeScript.

- `TimelineEvent`: existing serialized API event. `id` is numeric; `occurred_at` retains its exact wire string.
- `EventSnapshot`: `{ events: TimelineEvent[], sessions: Object }`. Events are ascending by occurrence time, then ID.
- `HistoryState`: `{ status: 'idle'|'loading'|'ready'|'recovering'|'error', snapshot: EventSnapshot|null, error: Error|null }`.
- `EventBoundary`: `{ beforeLine: number|null, events: TimelineEvent[] }`. `null` means after all source items.
- `Segment`: `{ key: string, originalHead: number, head: number, tail: number, members: number[], expanded: boolean }`.
- `SegmentState`: array of `Segment` records. Retain it across modes and history recovery. Clear it on session unload.
- `EventRow`: `{ lineNum: 'event:<id>', isSessionEvent: true, event: TimelineEvent, timestamp: string }`.
- `SegmentRange`: `{ head: number, tail: number }`, attached as `segmentRange` to a visual message only when applicable.
- `EventGroupControl`: `{ segmentKey: string, expanded: boolean, range: SegmentRange }`.
- `eventInternalControls`: mapping from an internal content group's `startIndex` to its `EventGroupControl`.
- Row keys remain `lineNum` for compatibility. Source rows keep numeric keys. Event and date keys use separate string namespaces.

## Task 1: Complete session event history controller

**Files:**
- Create: `frontend/src/utils/chatEvents/history.js`
- Create: `frontend/src/utils/chatEvents/history.test.js`
- Read: `frontend/src/utils/timeline/feed.js`, `eventTime.js`, `liveEvents.js`
- Read: `src/twicc/core/services/timeline_feed.py` for the existing response contract

**Interfaces:**
- Consumes: injected `fetchPage({ sessionId, before, afterId, signal }): Promise<Object>`.
- Produces: `createSessionEventHistory({ sessionId, fetchPage, state, onSnapshot }): HistoryController`.
- Produces: `createSessionEventHistoryState(): HistoryState`.
- Controller methods: `start()`, `notifyLatest(id)`, `refresh()`, `retry()`, `idle(): Promise<void>`, `dispose()`.
- `onSnapshot(snapshot)` runs only when a complete usable snapshot changes. State changes remain reactive without republishing messages.

- [x] **Step 1: Add deferred-response tests for complete publication and cursor races.**

Use fixtures with source session `s`, another target `other`, and 201 selected events.
Pin the following assertions in executable tests:

```js
assert.equal(state.snapshot, null) // first 100 and next 100 are staging only
assert.equal(state.status, 'loading')
assert.equal(published.length, 0)
// First response watermark = 400; older pages report 450 and 470.
assert.equal(catchupRequest.afterId, 400)
assert.equal(state.snapshot.events.length, 202) // includes late backdated event id 471
assert.equal(new Set(state.snapshot.events.map(e => e.id)).size, 202)
assert.deepEqual(state.snapshot.events, expectedAscendingEvents)
```

Also cover first/before/live failure; Retry; empty complete history; duplicate page overlap; truncated catch-up; unrelated signals;
matching hidden/shown session reports; dispose while a request is pending; and an older generation resolving last.
Recovery failure retains `strictEqual(state.snapshot, previousCompleteSnapshot)` and sets `status='error'`.
A changed session never receives another controller's result.
A pagination response that claims more rows without advancing its cursor must fail visibly instead of looping.

- [x] **Step 2: Run the controller tests and confirm failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/history.test.js`.
Expected: FAIL because the new controller does not exist.

- [x] **Step 3: Implement the controller and its serial request queue.**

Use one queue per controller. Coalesce live invalidations by the highest signaled ID.
For a full load, stage the first page, retain its `latest_id` as watermark H, then drain every `before` page.
Never replace H with an older page's watermark. Deduplicate by event ID.
After the last older page, always request `after_id=H`, even without a signal.
Merge every matching live row, including rows older than already-read pages.
Publish once after this catch-up succeeds. Later invalidations use the new live cursor.
An invalidation arriving during catch-up queues another pass when its ID exceeds the successful response watermark.

On `truncated=true`, discard only staging and restart a full traversal.
Keep the last published snapshot throughout recovery. Do not advance the cursor from a truncated response.
On failures, retain the last complete snapshot and expose a retryable error.
Retry replays a failed history traversal or live catch-up from its safe cursor; no automatic failure loop.
On matching `shown_sessions`, rebuild complete history because rows hidden earlier can precede the live cursor.
On matching `hidden_sessions`, remove the published target snapshot immediately and stop publishing until the session is eligible again.
The application retains ownership of hiding and closing the chat.

Reuse exact cursor and event comparison helpers. Do not apply the Timeline feed's loaded-window floor to live rows.
Guard every completion by generation and disposal. Abort the active request on disposal or supersession.
Preserve equal event objects and snapshot references when no event or cited session information changes.
`idle()` waits for all currently queued work, including work queued by pagination or catch-up.

- [x] **Step 4: Run controller and existing Timeline tests.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/history.test.js src/utils/timeline/feed.test.js src/utils/timeline/liveEvents.test.js`.
Expected: all PASS. Existing Timeline behavior remains unchanged.

- [x] **Step 5: Commit the tested controller.**

Subject: `feat(chat): collect complete session event snapshots`.
Body: explain staging, the first-page watermark, safe recovery, and the passing tests.

## Task 2: Precise event placement over source metadata

**Files:**
- Create: `frontend/src/utils/chatEvents/placement.js`
- Create: `frontend/src/utils/chatEvents/placement.test.js`
- Modify: `frontend/src/stores/data.js` only to extract the existing compact-order correction if needed
- Test: `frontend/src/stores/visualItemsStability.test.js` if extraction changes its dependencies

**Interfaces:**
- Consumes: all source items before display filtering, including metadata-only items and synthetic message rows.
- Produces: `normalizeChatTimestamp(value: string|null): bigint|null` in epoch microseconds.
- Produces: `placeSessionEvents(items: Object[], events: TimelineEvent[]): EventBoundary[]`.
- Produces: `orderCompactItems(items: Object[], compacted: boolean): Object[]` if extraction is necessary.
- Source `line_num` identities and parsed-content caches remain intact.

- [ ] **Step 1: Add ordering tests with exact identities and boundaries.**

```js
assert.equal(normalizeChatTimestamp('2026-10-27T00:00:00.000001Z') + 1n,
    normalizeChatTimestamp('2026-10-27T00:00:00.000002+00:00'))
assert.deepEqual(placeSessionEvents([source(10, t1), source(11, t2)], [event(7, t2)]),
    [{ beforeLine: 11, events: [event(7, t2)] }])
```

Test leading/trailing events; equal occurrence times ordered by ID; three- and six-digit fractions; timezone offsets;
hidden tools; unloaded content later hydrating; no timestamps; missing middle timestamps; regressing timestamps;
compact command/summary correction; synthetic-to-real replacement; and a source item with several internal blocks.
Assert source order and input objects remain unchanged.

- [ ] **Step 2: Run the placement tests and confirm failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/placement.test.js`.
Expected: FAIL because placement exports do not exist.

- [ ] **Step 3: Implement deterministic placement without sorting transcript rows.**

Normalize valid ISO timestamps to integer microseconds. Parse the whole-second instant with its timezone separately from the fraction.
Pad the fractional part to six digits. Do not order with `Date.parse()` milliseconds alone.
Invalid timestamps return `null`.
Build monotonic effective source times in transcript order: carry the preceding usable time and clamp regressions to that time.
Leading undated items use the first usable time. When every source time is absent, put the ordered events before the source rows.
Undated synthetic rows follow the same deterministic fallback until real timestamps arrive.
Walk sorted events and effective source times once. Place each event before the first source time greater than or equal to its time.
Remaining events get `beforeLine=null`. Keep multiple events at one boundary together.

Apply the existing compact correction to the placement-order copy before choosing boundaries. Reuse its current command detection and bounded scan.
Do not create a second correction with different rules or mutate the raw session array.
When no correction applies, return the original array reference.
No content hydration is needed to place ordinary timestamped rows. Compact correction retains its existing content-detection behavior.
Do not feed the reordered copy into conversation-block identification. Preserve the current filtering-then-compact-correction order for message rows.
Keep source items indivisible. Placement does not inspect inner Markdown or invent tool-block timestamps.

- [ ] **Step 4: Run placement and event-time tests.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/placement.test.js src/utils/timeline/eventTime.test.js src/stores/visualItemsStability.test.js`.
Expected: all PASS. Event-free compact ordering remains unchanged.

- [ ] **Step 5: Commit precise source-boundary placement.**

Subject: `feat(chat): place events against source item metadata`.
Body: document microsecond ties, transcript-order fallback, and compact-order compatibility.

## Task 3: Independent tool segments and expansion inheritance

**Files:**
- Create: `frontend/src/utils/chatEvents/segments.js`
- Create: `frontend/src/utils/chatEvents/segments.test.js`
- Modify: `frontend/src/stores/data.js` group state actions, internal/external suffix migration, streaming retirement and orphan cleanup
- Modify: `frontend/src/utils/visualItems.js` to forward projected group-control range metadata
- Test: `frontend/src/utils/visualItems.test.js`

**Interfaces:**
- Consumes: `EventBoundary[]` from Task 2, original source groups, original numeric expansion arrays, prior `SegmentState`.
- Produces: `projectEventSegments({ items, boundaries, previous, expandedGroups, internalExpandedGroups, retired = [] }): { items: Object[], segments: Segment[], expandedHeads: number[] }`.
- Produces: `setEventSegmentExpanded(segments: Segment[], head: number, expanded: boolean): Segment[]`.
- Produces store action: `toggleEventSegment(sessionId: string, segmentKey: string)` for projected internal controls.
- `retired` entries are `{ streamingLineNum: number, realLineNum: number, contentIndex: number|null, expanded: boolean }`, captured before removing a synthetic segment.
- Store group actions keep their existing signatures with numeric source heads.
- The projected `head` is the original source line of the first participating item in that segment.
- Segment `key` uses `tools:<originalHead>:<first-member-line>`; it is state identity, never a source line key.

- [ ] **Step 1: Test independent segments with real display projection.**

Use user line 1, twenty COLLAPSIBLE tools at lines 2–21, and assistant line 22.
Place an event before line 12.

```js
assert.deepEqual(segments.map(s => [s.head, s.tail, s.members.length]), [[2, 11, 10], [12, 21, 10]])
assert.deepEqual(computeVisualItems(projectedItems, 'simplified', []).map(r => r.lineNum), [1, 2, 12, 22])
assert.deepEqual(openOnlyFirst.map(s => s.expanded), [true, false])
```

Cover ALWAYS suffix → COLLAPSIBLE → ALWAYS prefix; cuts at either connection; prefix and suffix on one ALWAYS item;
consecutive events; event outside any group; all modes; internal suffix migration; streamed group extension;
unrelated segments; split inheritance from open/closed parents; and different states retained across recovery.
Test an expanded ALWAYS suffix that becomes internal after an event, with its prefix closed. Repeat with the sides reversed.
Toggle the orphan control, recompute twice, and assert its state and the opposite side's state stay independent.
Open a synthetic thinking segment, retire it through the existing line pair, and assert only its destination segment opens.
Use an ALWAYS destination with both prefix and suffix groups; the thinking content index must select only its containing side.
Also test an internal-only thinking group and a destination without any collapsible group. Every transfer must be consumed.
Drop an orphan synthetic block and reuse its negative line number; the new block must remain closed.
Freeze original source metadata and expansion inputs. Assert no mutation.
Test A25's second event splits the closed segment while its neighbor remains open.

- [ ] **Step 2: Run segment tests and confirm failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/segments.test.js`.
Expected: FAIL because segment exports do not exist.

- [ ] **Step 3: Project segment-local grouping and reconcile state.**

Cut original source groups at event boundaries. Clone only affected item metadata and forward the parsed-content cache.
Reassign projected `group_head`/`group_tail` within each segment. Do not change source `line_num` or stored grouping.
An ALWAYS prefix/suffix retains its connection only to source items on its side of the event.
A severed prefix/suffix uses the existing internal-content grouping layout when it has no external members.
Its expansion ownership remains in SegmentState, even when no external COLLAPSIBLE item remains.
Never split an ALWAYS item's internal content. Its own prefix and suffix can belong to different original groups.

Match previous segments by original group and member overlap.
An unchanged segment keeps its state. Each child of a newly split segment inherits that parent's state.
For a previously unseen segment, use its original group's expansion state, including migrated internal suffix state.
A source-group extension inherits the overlapping segment's state.
Keep segment records in every display mode. Recovery staging must not reconcile them against a partial snapshot.
Initialize `localState.sessionEventSegments` in this task because its group actions already use it.

Route toggle/expand/collapse/collapse-all and search-driven expansion through segment state when projected groups exist.
Keep legacy numeric expansion arrays as the no-event fallback. A toggle of one projected head changes only that segment.
Adapt `_migrateInternalSuffixToExternal` so an already-open internal suffix seeds the corresponding segment once.
Do not reset independently toggled siblings on later recomputes.
For an orphan prefix/suffix, use the original connection metadata and parsed-content boundaries to map its internal `startIndex` to `eventInternalControls`.
Forward this mapping to ContentList. Its visibility and toggle state use the mapped segment's `expanded` value.
Its toggle calls `toggleEventSegment(sessionId, segmentKey)` instead of `toggleInternalExpandedGroup`.
Unmapped internal groups keep the existing internal expansion store. Do not duplicate ownership between the two stores.
Metadata-only items retain segment state; derive the content-index mapping when their content hydrates.
The mapped orphan controls seed later external reconnection from SegmentState, rather than an obsolete internal entry.

Before `_retireStreamingBlocks` removes a fake group, capture its expansion with the existing `{streamingLineNum, realLineNum}` retirement pair.
For Claude, capture the persisted thinking content index used by the existing wa-details transfer. For Codex reasoning, use `contentIndex=null` for the whole source row.
Keep these transfers until the next projection contains each real destination line. Pass them as `retired` and then consume them.
After ordinary overlap matching, resolve one destination control. A COLLAPSIBLE source row uses its containing segment.
For an ALWAYS row, classify `contentIndex` with its original prefix/suffix boundaries and `getInternalCollapsibleGroups`.
A prefix targets the segment for the original `group_head`; a suffix targets the segment for original head `realLineNum`.
Require the destination line and this original-side identity. Membership in the source line alone is insufficient.
An orphan side uses its mapped `eventInternalControls` segment. An unmapped middle/internal group seeds `sessionInternalExpandedGroups[realLineNum]` at its `startIndex`.
Apply an open transfer only to that control. If no collapsible destination exists, consume the transfer without opening another control.
Do not open another segment across an event. A closed synthetic source does not close an already-open destination.
Preserve existing wa-details migration separately. Remove retired synthetic state and transfer entries after application.
`_dropOrphanedStreamingBlocks` removes its synthetic segment and pending transfers before a negative line number can be reused.
Projected visual items carry a numeric `segmentRange` for their connected control's comment scope.
For separate prefix and suffix controls on one ALWAYS item, carry `prefixSegmentRange` and `suffixSegmentRange` separately.
Forward those optional fields and `eventInternalControls` explicitly in `computeVisualItems`'s `makeVisualItem`; its current constructor does not copy arbitrary source fields.

- [ ] **Step 4: Run segment and existing grouping tests.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/segments.test.js src/utils/visualItems.test.js src/stores/visualItemsStability.test.js`.
Expected: all PASS, including event-free group actions and internal suffix migration.

- [ ] **Step 5: Commit projected tool segmentation.**

Subject: `feat(chat): split displayed tool groups at event boundaries`.
Body: explain independent state, inheritance, prefix/suffix treatment, and source identity preservation.

## Task 4: Store ownership and event-aware visual rows

**Files:**
- Create: `frontend/src/utils/chatEvents/rows.js`
- Create: `frontend/src/utils/chatEvents/rows.test.js`
- Modify: `frontend/src/stores/data.js`
- Modify: `frontend/src/stores/visualItemsStability.test.js`
- Create: `frontend/src/stores/sessionEventHistory.test.js`
- Read: `frontend/src/utils/visualItems.js`

**Interfaces:**
- Produces: `isSessionEvent(item): boolean` and `isTranscriptLoadCandidate(item): boolean`.
- Produces: `mergeSessionEventRows({ sourceItems, messageRows, boundaries }): Object[]`.
- Produces: `insertEventDaySeparators(rows: Object[]): Object[]`.
- Store state: `localState.sessionEventHistory[sessionId]: HistoryState` and `localState.sessionEventSegments[sessionId]: SegmentState`.
- Store actions: `ensureSessionEventHistory(sessionId)`, `refreshSessionEventHistory(sessionId)`, `retrySessionEventHistory(sessionId)`, `clearSessionEventHistory(sessionId)`.
- A module-level WeakMap keyed by store instance holds each session's controller and window-event cleanup. Controllers never enter reactive state.

- [ ] **Step 1: Add projection and ownership tests.**

Compare message-only projections with and without event insertion across four modes.

```js
assert.deepEqual(withEvents.filter(r => !r.isSessionEvent && !r.isDaySeparator).map(blockIdentity), baseline.map(blockIdentity))
assert.equal(rows.filter(r => r.lineNum === 'event:7').length, 1)
assert.equal(isTranscriptLoadCandidate(rows.find(r => r.lineNum === 'event:7')), false)
assert.strictEqual(secondRecomputeUnchangedEvent, firstEventRow)
```

`blockIdentity` includes source line, detailToggleFor, isBlockStart, isBlockEnd, and live timestamp anchor.
The baseline uses the same segment projection with events omitted, rather than rejoining the original tool group.
Test retained conversation response, empty-assistant policy, background status, timestamps off, and events-only sessions.
Test midnight within one response and local calendar dates using a fixed test timezone.
Test per-store controller isolation, repeated ensure calls, unload/dispose, hidden removal, and drafts producing no request.

- [ ] **Step 2: Run the new tests and confirm failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/rows.test.js src/stores/sessionEventHistory.test.js`.
Expected: FAIL because the new rows and store actions do not exist.

- [ ] **Step 3: Add store-owned history lifecycle and bounded network requests.**

`ensureSessionEventHistory` starts once for an existing, persisted, non-hidden session, including `last_line=0`.
It must not depend on `loadSessionHistory` succeeding or on its early return for empty transcripts.
Use `apiFetch(buildTimelineUrl({ query: {session: sessionId, view: 'without_turns'}, limit: 100, before, afterId }), {signal})`.
Apply a 30,000 ms AbortController deadline to each request, including response body reading. Clear timeout in `finally`.
Propagate controller cancellation and HTTP/body failures to Task 1's error handling.
Bind `bindTimelineEvents(window, controller)` once per loaded session. Dispose it in `clearSessionEventHistory` and `unloadSession`.
Guard snapshot publication against the current state object and generation. Recompute only this session when its snapshot changes.
React to the existing hidden-session state path by clearing its event state. Do not create a new hiding policy.

- [ ] **Step 4: Integrate the projection passes in `recomputeVisualItems`.**

Keep existing synthetic assembly. Use a compact-corrected copy only to choose placement boundaries.
Reconcile segments against those boundaries while retaining original transcript order as the filtering input.
Pass projected items/expandedHeads to `computeVisualItems` once for the whole transcript, then apply the existing compact correction to its output.
Do not run conversation mode independently per event segment.
Propagate syntheticKind and compute existing block flags and message timestamp anchors on message rows only.
Then merge independent EventRows using source boundaries. Hidden source items map to the next displayed source row.
Tool segments never cover both sides of an inserted notice.
Append the existing background-work status after event and date projection, as today.

Remove the empty-source early return when a complete event snapshot has rows.
Recompute all sessions from the union of loaded source sessions and event-history sessions, including events-only sessions.
Preserve stable row references through the existing cache. Reuse event objects and stable segment-range objects or scalar signatures.
Do not create a fresh nested object on every no-op recompute and defeat `visualItemEqual`.

With no events, call the current `insertDaySeparators` unchanged.
With events, split the visible calendar traversal at event rows without changing message block flags.
Within each message portion, use the first usable visible source date, then insert a separator at a later visible source day change.
Use event occurrence dates for notices. Undated rows inherit current context without inventing a date.
Preserve the no-leading-heading convention and suppress duplicate adjacent same-day headings.
Date keys use `daysep-<following-row-key>`, including event keys.
This avoids using the response's final next-day timestamp for tools displayed before an earlier event.
When the timestamp setting is off, insert no day separators; retain every EventRow.

- [ ] **Step 5: Run store and row tests.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/*.test.js src/stores/sessionEventHistory.test.js src/stores/visualItemsStability.test.js src/utils/visualItems.test.js`.
Expected: all PASS. Event-free row references and arrays remain stable.

- [ ] **Step 6: Commit store and projection integration.**

Subject: `feat(chat): merge session events into stable visual rows`.
Body: explain loaded-session ownership, complete publication, logical-block isolation, and calendar behavior.

## Task 5: Dedicated notice and independent event-loading UI

**Files:**
- Create: `frontend/src/components/session/detail/SessionEventNotice.vue`
- Create: `frontend/src/components/session/detail/SessionEventNotice.test.js`
- Create: `frontend/src/composables/useSessionEventHistory.js`
- Create: `frontend/src/composables/useSessionEventHistory.test.js`
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue`
- Modify: `frontend/src/components/session/detail/SessionItemsList.loading.test.js`
- Read: `frontend/src/utils/timeline/describeEvent.js`, `parts.js`, `eventTime.js`, `providerLabels.js`

**Interfaces:**
- Notice props: `event: TimelineEvent`, `context: Object` using the existing `describeEvent` context.
- `context` is one stable `shallowReactive({ sessions, labelsFor, isSubagent })` object per chat component. Replace its `sessions` property when the complete snapshot's cited-session table changes.
- Composable: `useSessionEventHistory({ store, sessionId, eligible, active })` returns `{ state, retry }` as computed refs/action.
- `eligible` means an existing persisted non-hidden session. `active` follows the existing chat/view activation state.
- The composable ensures history when eligible, and requests `refreshSessionEventHistory` on reactivation.
- Reactivation first ensures a missing controller, including after reconciliation unloads a cached chat. Refresh an existing controller; do not duplicate a new controller's initial traversal.
- Component destruction does not discard a loaded-session cache. `unloadSession` owns final disposal.

- [ ] **Step 1: Add notice, loading, and activation tests.**

Cover every selected current family, another actor, unknown kind, missing optional data, estimated timestamp, and long values.
Assert the short time remains visible when message timestamps are disabled.
Assert full rename/settings values and error text are reachable with a keyboard detail control.
Assert markup-like strings render as text. Assert no Timeline header, card, grouping, or separate time column is mounted.
Test loading/error with messages, without messages, and during recovery with a previous snapshot.
Assert opening the same chat twice does not restart traversal, a draft sends no request, and activation requests catch-up once.
Mount the notice through the real memoized VirtualScroller harness. Keep event and EventRow references unchanged, then replace the cited-session table.
Assert actor title/provider/link eligibility update and the mounted notice and open Details element retain their identity and state.

- [ ] **Step 2: Run notice and loading tests and confirm failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/detail/SessionEventNotice.test.js src/composables/useSessionEventHistory.test.js src/components/session/detail/SessionItemsList.loading.test.js`.
Expected: new tests FAIL because the notice and lifecycle composable do not exist.

- [ ] **Step 3: Implement the dedicated notice.**

Use `describeEvent` for semantic wording and time. Reuse small wording parts, not the full Timeline row.
Pass the stable reactive context from this task's interface. Update `context.sessions` even when no EventRow changes.
The notice's computed description reads that reactive property directly; a parent `v-memo` skip must not freeze cited-session information.
Render a centered wrapping text flow with an optional decorative icon and restrained severity color.
Use inline short time, an estimated mark, and accessible full local time.
Use a native `<details><summary>Details</summary>…</details>` for full descriptions and long errors when needed.
Include the helper's full tooltip information there as text; hover alone cannot expose required values to keyboard users.
Keep source/actor information when supplied. An unknown kind exposes its literal kind and time.
Use normal text interpolation, selectable content, `min-width: 0`, wrapping, and `overflow-wrap: anywhere`.
No fixed notice height. ResizeObserver measures expanded details through the existing row wrapper.

- [ ] **Step 4: Integrate loading state and the event rendering branch.**

Insert the event branch after DaySeparator and before the `!hasContent(item)` placeholder branch.
Do not send EventRows to SessionItem or provider-specific rendering.
Add an independent compact status region outside the message error/loading conditional chain.
Use `Loading session events…`, `Updating session events…`, and `Failed to load session events` with `Retry`.
Keep the existing composer and message controls usable throughout event loading/error.
For events-only sessions, show the scroller when event rows exist. Suppress `Nothing to show yet` until event loading completes.
An event-history failure never sets message `itemsLoadingError` or blocks message navigation.
Preserve existing compute/unavailable behavior; event state must not turn an inaccessible session into a visible chat.

- [ ] **Step 5: Run notice/lifecycle/loading tests.**

Run the Step 2 command. Expected: all PASS.

- [ ] **Step 6: Commit the dedicated notice and event status UI.**

Subject: `feat(chat): render centered session event notices`.
Body: explain independent loading, accessible detail text, and events-only display.

## Task 6: Loading, navigation, comments, and animation boundaries

**Files:**
- Modify: `frontend/src/utils/scrollerLoadWindow.js` and `scrollerLoadWindow.test.js`
- Modify: `frontend/src/composables/useGroupReveal.js`
- Modify: `frontend/src/utils/groupReveal.js` and `groupReveal.test.js` if its planner receives the predicate
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue`
- Modify: `frontend/src/components/session/detail/SessionItem.vue`
- Modify: `frontend/src/components/session/detail/items/claude_code/Message.vue`
- Modify: `frontend/src/components/session/detail/items/claude_code/ContentList.vue`
- Modify: `frontend/src/composables/useChatNavigation.js`
- Test: `frontend/src/utils/chatBlocks.test.js`, `frontend/src/utils/listExit.test.js`
- Create: `frontend/src/components/session/detail/SessionItemsList.events.test.js`

**Interfaces:**
- Consumes: Task 4 row predicates and Task 3 prefix/suffix segment ranges.
- Extends: `useGroupReveal({ ..., isMember = () => true })` with a per-item membership predicate.
- Existing `useListExit.isEligible(item)` receives `!isSessionEvent(item) && groupReveal.isCollapsing()` in the chat.
- Adds optional numeric-range props to SessionItem → Claude Message → ContentList: `prefixSegmentRange`, `suffixSegmentRange`.
- Adds optional `eventInternalControls` prop through the same chain, defaulting to an empty mapping.
- Extends `useChatNavigation` with optional `displayItems=visualItems`, preserving existing share-viewer callers.

- [ ] **Step 1: Add behavior tests for non-message rows and concurrent updates.**

```js
assert.deepEqual(collectMissingScrollerLines([unloaded(12), eventRow(7), dayRow(), unloaded(13)], range, 0), [12, 13])
assert.equal(toolAnimationKeys.includes('event:7'), false)
assert.equal(exitKeys.includes('event:7'), false)
assert.deepEqual(messageBlockTargetsAfter, messageBlockTargetsBefore)
```

Test an event inserted in the same flush as expand/collapse/detail toggle; segment-local comment counts; mapped orphan prefix/suffix toggles;
source-line search expanding only the containing segment; day separator/event string targets skipping content preload;
leading/trailing event edge navigation; and block navigation while exit rows remain mounted.
Test a shared navigation caller without events or the optional argument.

- [ ] **Step 2: Run the targeted tests and confirm new failures.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/scrollerLoadWindow.test.js src/utils/groupReveal.test.js src/utils/listExit.test.js src/utils/chatBlocks.test.js src/components/session/detail/SessionItemsList.events.test.js`.
Expected: new event-specific cases FAIL; existing cases remain diagnostic baselines.

- [ ] **Step 3: Exclude events from loading and animations.**

Use `isTranscriptLoadCandidate` in `collectMissingScrollerLines` and the preload loop inside `scrollToLineNum`.
It accepts only real positive numeric source lines lacking content. Date/event/status rows are not candidates.
Direct event/date scroll targets use their stable key without JSONL loading or source-line arithmetic.
Keep metadata-to-content hydration keyed by original numeric source lines.

Pass membership filtering into useGroupReveal before computing growing rows and group counts.
An event added during a pending toggle never gets a group animation class or contributes to animation count.
Pass the per-item exclusion into useListExit. Event removal on hiding never retains an exiting notice.
Keep the predicate default unchanged for sidebar and other existing callers.

- [ ] **Step 4: Forward comment scopes and resolve navigation by rendered keys.**

Use each segment's head/tail for the GroupToggle comment count.
For ALWAYS controls, forward independent prefix/suffix ranges through SessionItem and Claude Message to ContentList.
Use those ranges only for their matching control. Keep original line and tool-result lookups unchanged.
Forward `eventInternalControls` too. Mapped internal groups use their segment state, toggle action, and comment range from Task 3.
An internal-only control falls back to its own source line, not the original cross-event range.
Keep the whole conversation-detail comment count attached to its original logical user block.

Block targets still come from message flags. Events add no `isBlockStart` and no detailToggleFor.
Map each logical target's key into `displayItems` and then the scroller positions before reading its offset.
Never use a visualItems index directly against a list containing extra exit rows.
Top/bottom use the complete displayed list, including event-only content.
Search/source targets keep numeric lines and expand the projected segment containing the target when needed.

- [ ] **Step 5: Run targeted tests and the existing chat loading tests.**

Run the Step 2 command plus `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/detail/SessionItemsList.loading.test.js`.
Expected: all PASS. Existing no-event navigation behavior remains unchanged.

- [ ] **Step 6: Commit integration boundaries.**

Subject: `fix(chat): keep event rows outside transcript controls`.
Body: explain loading exclusions, tool animation membership, segment comments, and keyed navigation.

## Task 7: Preserve reading position through structural event changes

**Files:**
- Create: `frontend/src/utils/chatEvents/scroll.js`
- Create: `frontend/src/utils/chatEvents/scroll.test.js`
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue`
- Modify: `frontend/src/components/session/detail/SessionItemsList.scroll.test.js`
- Modify: `frontend/src/components/session/detail/SessionItemsList.resume.test.js`
- Read: `frontend/src/utils/timeline/scrollKeeper.js`
- Read: `frontend/src/components/virtual-scroller/VirtualScroller.vue`, `frontend/src/composables/useVirtualScroll.js`

**Interfaces:**
- Produces: `captureEventScroll({ scroller, rows, intent }): Object|null`.
- Produces: `planEventScrollRestore({ snapshot, rows, positions, intent, revision }): { action: 'none'|'anchor'|'bottom', anchor?: Object }`.
- `intent` is a SessionItemsList counter advanced by user scrolling and explicit search/block/edge navigation.
- `revision` is existing `scroller.getScrollRevision()`, not a new scroller API.
- Reuse `chooseAnchor`/`survivingAnchor` math where compatible. Do not reuse Timeline's special no-correction-at-top rule.

- [ ] **Step 1: Add structural-scroll tests.**

Test initial complete publication, backdated live insertion, truncated recovery replacement, and tool-group splitting above the viewport.
Pin these cases:

```js
assert.equal(decisionForHistoricalInsertion.action, 'anchor')
assert.equal(restoredAnchor.key, previouslyVisibleMessageKey)
assert.equal(restoredAnchor.offset, previousOffset)
assert.equal(decisionAfterUserScroll.action, 'none')
assert.equal(decisionAfterExplicitNavigation.action, 'none')
assert.equal(decisionAtBottom.action, 'bottom')
```

Also test: same anchor index but changed preceding row keys/heights; anchor deletion with surviving next row;
no surviving row; a viewport at scrollTop=0 reading a source row; hidden pane/KeepAlive resume; and a detail expansion changing row height.
Events-only leading/trailing lists must remain valid anchors.

- [ ] **Step 2: Run scroll tests and confirm new failures.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/chatEvents/scroll.test.js src/components/session/detail/SessionItemsList.scroll.test.js src/components/session/detail/SessionItemsList.resume.test.js`.
Expected: new structural-event cases FAIL because the transaction does not exist.

- [ ] **Step 3: Implement one chat-owned structural-scroll transaction.**

Watch the actual scroller `displayItems` in pre-flush. Capture old positions, anchor, bottom state, intent, and scroll revision before geometry updates.
Compare ordered row keys and segment-control state to detect event-related structural changes, including equal-length replacements.
Choose a stable message/event anchor; skip day separators when a surviving content row follows them.
After nextTick, restore the same key/offset, or the next surviving row with its old screen offset.
A same-index anchor still needs restoration when its old and new top differ.
If nothing survives, leave ordinary initial positioning in control rather than forcing Timeline's top behavior.

At bottom, delegate to existing `scrollToBottomUntilStable` only when captured bottom state and current intent still agree.
A newer user scroll or explicit navigation cancels a pending restore before any scroll call.
Use existing revision checks to avoid fighting streaming swaps or another settled scroll operation.
Coalesce multiple structural publications in one frame into one transaction.
Do not add a second height-change correction; the existing ResizeObserver/useVirtualScroll path owns measured height deltas.

Coordinate the existing visualItems-length auto-bottom watcher with the transaction. It must not issue a competing bottom operation.
When suspended or inactive, defer restoration to the existing saved-anchor/resume flow.
Event catch-up on activation follows the same captured near-bottom rule and user-intent guard.
Preserve the VirtualScroller instance, its key-based measurement cache, stream-swap floors, and initial reveal behavior.

- [ ] **Step 4: Run scroll and existing scroller tests.**

Run the Step 2 command.
Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
Expected: all PASS. Inspect any failure before broadening the implementation.

- [ ] **Step 5: Commit structural-scroll preservation.**

Subject: `fix(chat): preserve reading position when event rows change`.
Body: explain anchor restoration, user-intent precedence, bottom behavior, and unchanged height measurement ownership.

## Task 8: Integrated acceptance and product validation

**Files:**
- Create: `frontend/src/stores/sessionEvents.integration.test.js`
- Extend: `frontend/src/components/session/detail/SessionItemsList.events.test.js`
- Update: this plan's execution checkboxes and validation record during implementation only

**Interfaces:**
- Consumes: real controller, placement, segments, projection/store actions, and existing scroller adapters from Tasks 1–7.
- Produces: an integrated deferred-network test fixture and a recorded acceptance result for A1–A25.
- The fixture injects network and scroller boundaries. It executes production projection and state transitions.

- [ ] **Step 1: Add integrated contract tests before changing any implementation in this task.**

Use both Claude-style connected content and Codex-style tool items.
Include 201 initial events, two independently toggled tool segments, a reconnect truncation, one failed recovery page, and a successful retry.
Insert one additional historical event into the closed segment.

```js
assert.strictEqual(snapshotDuringFailedRecovery, previousCompleteSnapshot)
assert.deepEqual(statesAfterRecovery, expectedIndependentAndInheritedStates)
assert.equal(countEventId(finalRows, insertedId), 1)
assert.equal(composerBlockedByEventLoading, false)
assert.equal(jsonlRequestedKeys.some(key => typeof key !== 'number'), false)
assert.deepEqual(retainedAssistantLines, baselineRetainedAssistantLines)
```

Also integrate: persisted events-only chat; actor targeting another session; hidden/unload response races;
metadata hydration; streaming/optimistic replacement; compact correction; midnight; timestamp toggle; and scroll cancellation.
Use earlier pure tests for exhaustive permutations. Do not duplicate every unit test here.

- [ ] **Step 2: Run integrated tests and investigate any failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/sessionEvents.integration.test.js src/components/session/detail/SessionItemsList.events.test.js`.
Expected: all PASS. A failure requires a regression test in the owning earlier task's module before correction.

- [ ] **Step 3: Run the frontend suite and production build.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`.
Expected: tests PASS and build exits 0. No package installation or backend commands are needed.
Check shared navigation and event-free visualItems tests because the public share viewer uses those helpers.
Do not add events to the share viewer or change its API.

- [ ] **Step 4: Validate rendered behavior in an available development instance.**

Use an already-running suitable instance. If none exists, request the user's explicit server-start instruction before starting one.
This constraint comes from repository operations rules; it does not block offline tests or the build.
Verify the twenty-tool example in four modes, each segment open/closed, long details in a narrow pane, and keyboard detail access.
Verify midnight context, timestamp setting off, search, block/edge navigation, event-only history, and visible event loading/error/Retry.
Verify anchor preservation above the viewport, follow-bottom then scroll-away, KeepAlive, hidden panes, resize, and streaming reconciliation.
Use fixtures or existing recorded events. Do not create noisy live session events solely for screenshots.
Record unavailable manual scenarios as unverified. Never report browser validation without running it.

- [ ] **Step 5: Record acceptance coverage and commit integration tests.**

Subject: `test(chat): verify session events across recovery and display modes`.
Body: identify the combined recovery/state scenario, tested providers, test/build results, and any unverified browser checks.

## Acceptance coverage map

| Spec scenarios | Owning tasks |
|---|---|
| A1–A3 selection, actors, other targets | 1, 4, 5, 8 |
| A4–A7 tools, connections, ties, inheritance | 2, 3, 4, 6, 8 |
| A8–A9 conversation semantics and empty assistant | 3, 4, 8 |
| A10–A11 dates and timestamp setting | 4, 5, 8 |
| A12–A15 pagination, races, failures, truncation | 1, 4, 5, 8 |
| A16–A17 reading anchor and bottom intent | 7, 8 |
| A18 lifecycle, stale responses, hidden sessions | 1, 4, 5, 8 |
| A19 metadata hydration and JSONL exclusion | 2, 4, 6, 8 |
| A20 streaming and optimistic replacement | 2, 4, 7, 8 |
| A21 events without messages | 4, 5, 8 |
| A22 timestamp fallback | 2 |
| A23 narrow layout and accessible details | 5, 8 |
| A24 KeepAlive and pane visibility | 5, 7, 8 |
| A25 mixed segment state and failed recovery | 1, 3, 8 |

## Plan review record

Self-review checks spec coverage, exact interfaces, task dependencies, test expectations, and plan size.
Three internal subagents review the plan. Corrections receive follow-up reviews until no material finding remains.
Reviewers evaluate implementation feasibility and missing regression coverage without reopening approved product decisions.

| Round | Focus | Result |
|---|---|---|
| 1 | Projection, grouping, dates, source identity | Two important findings: orphan control expansion and streaming state transfer |
| 1 | Complete history, lifecycle, scrolling, UI availability | PASS |
| 2 | Re-review of group corrections | Orphan controls resolved; streaming destination needs a prefix/suffix discriminator |
| 2 | Fresh whole-plan cross-review | One important finding: cited-session context can remain stale behind v-memo |
| 3 | Final projection re-review | PASS after side-specific retirement transfer and no-group consumption rules |
| 3 | Final whole-plan cross-review | PASS after stable reactive context and mounted regression coverage |

The corrected contracts appear in Tasks 3, 5, and 6. All three reviewers finish with PASS.
This is a document review. Product tests and browser validation are planned, not executed during planning.

## Execution handoff

Implementation requires a separate user instruction after plan review.
Recommended method: subagent-driven execution, with sequential task implementation and independent review gates.
The projection, group state, and scroll contracts need independent review because mistakes can hide events or move the reader.
