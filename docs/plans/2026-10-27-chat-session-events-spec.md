# Session events in the chat — specification

Date: 2026-10-27
Status: Approved for implementation planning on 2026-10-27; product implementation is not yet authorized

## 1. Purpose and document level

Show a session's recorded events inside its chat, at their place in the conversation.
Let the reader understand what happens to the session without opening a separate Timeline tab.

This document specifies observable behavior and required constraints.
It does not prescribe functions, store fields, component props, CSS selectors, algorithms, or implementation tasks.

## 2. Decisions and scope

### User requirements

- Show the events that target the displayed session, except turn events.
- Keep each event visible in every display mode, including inside a collapsed sequence of tools.
- Keep events at their chronological position when tools appear or disappear.
- Present individual events as centered chat notices.
- Use a dedicated presentation, without the Timeline's session cards, headers, grouping, or right-hand time column.
- Reuse the existing Timeline REST API and its pagination.
- Keep the existing exclusion of hidden sessions. This feature does not make their chats accessible.
- Preserve virtual scrolling, measured heights, navigation, and the current assistant presentation on the page background.

### Design choices made for this specification

The user delegates the integration details. The following choices make the behavior concrete for review:

- Split a displayed tool group at each event boundary.
- Let the resulting tool segments open independently.
- Preserve an existing segment's open state when a new event splits it.
- Retrieve the session's complete recorded event history through successive existing API pages.
- Keep event loading and failures separate from the availability of messages and the composer.
- Show a short time with each event notice, independently of the message-timestamp setting.

The user approves implementation planning after the adversarial specification review.
This approval does not authorize product implementation.

### Out of scope

- New event kinds, new recording hooks, or reconstruction of unrecorded history.
- Changes to the Timeline tab, its filters, its REST contract, or hidden-session selection.
- A new API, a database migration, or changes to recorded transcript and grouping metadata.
- Events targeting other sessions, including descendants and sessions acted on by the displayed session.
- Instance events without a target session, including `backend.*`.
- Public share viewers, event search, event exports, or new event visibility settings.
- Replacing the virtual scroller or redesigning the chat's message presentation.
- An implementation plan or product implementation.

## 3. Event selection

Use the existing exact-session filter and the `without_turns` view.
Do not inherit the filters selected on a project, workspace, or global Timeline page.

The selected event's `session_id` equals the displayed session ID.
The event's actor can be another session. This does not change the target selection.

| Recorded family | Chat behavior |
|---|---|
| `session.created`, `title.changed` | Show |
| `settings.changed` | Show |
| `process.started`, `process.ended` | Show, including their recorded reasons |
| `awaiting_input.started`, `awaiting_input.ended` | Show |
| `archived.*`, `hidden.*`, `muted.*`, `pinned.*` | Show when returned by the existing selection |
| `turn.started`, `turn.ended` | Exclude |
| `backend.*` | Exclude because they do not target the session |

Show each recorded event once. Do not combine a started/ended pair into one inferred interval.
For example, a pin-scope change retains its unpin and pin events.

Do not invent settings, rename, process, or organization history for older sessions.
A session with only a reconstructed creation event shows only that recorded history.

Apply the same selection to initial loading, older pages, live updates, and reconnect recovery.
A future event returned by this selection remains visible through a generic description when specialized wording is unavailable.

The rule follows the displayed session, including any supported subagent chat with its own recorded events.
It does not create events for subagents that have none.
A draft without a persisted session has no recorded history to request.

## 4. Individual notice presentation

Each event is one centered notice within the chat's scrollable content.
It reads as session history, distinct from user messages, assistant messages, and collapsible tool controls.

The notice contains a concise event description and its local time.
An icon and restrained severity styling can support the description.
The notice has no session header, project badge, enclosing session card, or separate right-aligned time column.
Optional rules around the centered content can follow the date separators' visual language.

Preserve the information needed to understand the event:

- Old and new values for a rename or settings change.
- A meaningful stop reason or error for a process event.
- The request type or tool for an input-wait event, when recorded.
- The pin scope and useful actor/source information, when recorded.

Existing event-description helpers can supply wording and interpretation.
Their reuse does not impose the Timeline row's visual structure or all its decorative metadata.
Long values and error details remain accessible through wrapping, an expansion, or an appropriate detail affordance.
An event must not become an unreadable single-line ellipsis or force horizontal chat scrolling.

Event text remains selectable. Data remains plain text, never executable HTML.
Keyboard users can reach any detail control. An icon or color is never the only expression of meaning.

The short time remains visible when message timestamps are disabled.
The full local date and time remain available in the event's accessible details.
Preserve the existing distinction between exact/derived times and estimated times, including the estimated-time mark.

An unknown kind still exposes its kind and time with a safe generic description.
Missing optional data must not remove the notice or break the chat.

## 5. Chronological placement

The chat reads from older content to newer content.
Order events by `occurred_at` ascending, then event ID ascending.
Use occurrence time, not arrival time, recording time, or event ID alone, for their historical placement.

Determine event placement from the full available source-item metadata, before display-mode filtering hides tools or messages.
Loading a message's content later must not provide a different chronological position than its existing metadata.

Preserve transcript order and existing deliberate presentation corrections, including the `/compact` ordering correction.
Do not globally sort messages and events together in a way that reorders the transcript.

For a normally timestamped transcript:

- An event between two source items appears between their displayed portions.
- An event earlier than all items appears before the conversation.
- An event later than all items appears after the recorded conversation.
- At an identical timestamp, events precede the matching source item. Equal-time events retain their ID order.
- Several events at one boundary remain consecutive individual notices.

Placement has the precision of the recorded source items.
A source item containing several content blocks remains indivisible when those blocks have no separate timestamps.
The feature does not invent a position inside such an item or split a Markdown message.

Missing or contradictory provider timestamps do not justify dropping events or reordering source messages.
Use a deterministic fallback consistent with transcript order and the surrounding usable timestamps.
The exact fallback algorithm is an implementation choice.

Rows without a final provider timestamp, including streaming and optimistic rows, remain compatible with this ordering.
An event remains present when those rows acquire their real transcript counterparts.
Reconcile its placement against the new source metadata without duplicating it or moving the reader unexpectedly.

## 6. Display modes and tool groups

### 6.1 Required example

Consider 20 consecutive tool items, with an event between items 10 and 11.

| Display state | Required result |
|---|---|
| `normal` or `debug` | Items 1–10, event, items 11–20 |
| `simplified`, closed | First tool-segment toggle, event, second tool-segment toggle |
| `simplified`, opened | The opened tools remain on their own side of the event |
| `conversation`, closed details | User message, event, retained assistant response; tools stay hidden |
| `conversation`, opened details | Visible tool items appear around the same event boundary |

In this example, each closed segment represents 10 of the original tool items.
Use the existing group's counting convention for real transcript content.
The event never contributes to a tool count or a message count.

The required visibility concerns filtering and collapse behavior.
Normal virtualization can unmount an event outside the rendered scroll range.

### 6.2 Segment behavior

An event is a boundary for displayed tool groups.
This applies to closed groups, open groups, and groups connected to message-content prefixes or suffixes.
No toggle claims items from both sides of an event.

Each non-empty tool segment has the appropriate existing control in simplified mode.
Opening or closing one segment does not open or close another segment across the event.
Consecutive events do not create empty tool toggles.

When an event splits an existing open segment, both resulting segments initially remain open.
When it splits a closed segment, both initially remain closed.
Unchanged segments keep their state across event updates and temporary display-mode changes.

Counts and comment indicators concern the segment they describe.
Tool/result associations, comment targets, and source line numbers keep their original identity.
The segmentation changes the display projection, never stored provider or grouping metadata.

### 6.3 Conversation semantics

An event does not start a new user turn or a new conversation-detail block.
It does not make an otherwise hidden assistant message become the retained response.
The existing conversation-detail toggle continues to control the same logical assistant response.

Events remain visible before, during, and after a detail toggle.
They do not participate in tool-opening or tool-closing animations as hidden group members.

### 6.4 Existing block behavior

Preserve the distinction between a logical conversation block and its visible tool segments.
The existing `isBlockStart`/`isBlockEnd` behavior must not accidentally treat an event as an assistant message.

Keep the current empty-assistant-message policy.
An event boundary alone must not create or remove a "had nothing to add here" notice.
The message-only projection still decides whether the assistant message is alone or accompanied by visible tools or text.

Keep the existing placement of message timestamps and live status rows.
An event does not consume a message's timestamp or turn a process-status row into historical conversation content.

## 7. Dates

Keep the existing setting that controls message timestamps and chat day separators.
Disabling it never hides event notices or their short times.

When day separators are enabled, they describe the dates of the displayed conversation portions and event notices.
An event can introduce a date boundary, including between tools in one logical assistant response.
This calendar boundary does not create a new conversation-detail block.

Avoid duplicate adjacent separators for one date.
Do not place a next-day separator before an earlier-day event solely because the assistant response ends the next day.
A response crossing midnight must retain correct date context on both sides of an intervening event.

## 8. Existing API and history loading

Use `GET /api/timeline/` with `session=<displayed session>` and `view=without_turns`.
Request up to the existing maximum of 100 events per page.
Use the existing `before` cursor for earlier pages and `after_id` for live recovery.
No new endpoint, response field, hidden-session exception, or server-side visibility rule is required.

Retrieve all pages for the session's recorded event history.
Completion means the existing API reports no older page, not that one page happens to fill the viewport.
Retain the existing serialized event precision and deduplicate by event ID.

Messages and event history can load concurrently.
The chat can display messages before the first complete event snapshot is ready.
Expose event-loading status until that snapshot is ready; do not imply that a loading history contains no events.
Collect the first complete snapshot before publishing its events into the chat.

Event requests must not indefinitely prevent message reading, search navigation, or use of the composer.
Requests have a bounded failure outcome using the application's existing network conventions.
On event-history failure, show a specific error with a retry action.
Keep messages and any previous complete event snapshot available.
Do not report an empty or complete event history after a failed or partial traversal.

A session without messages can still display its recorded events.
An event-loading failure is distinct from a message-history failure or a genuinely empty event history.

Cache retrieved events for the loaded session.
Changing display mode, opening tools, or navigating within the same loaded chat does not restart the complete traversal.
Cache ownership follows the session's loaded-chat lifetime, not a global permanent history of every session.
Do not start history traversals for unopened sessions.

## 9. Live updates and lifecycle

Use the existing `timeline_changed` signal and live cursor to request new events.
Use the same selection as the initial history.
An unrelated session's signal must never cause unrelated events to enter the chat.

Events committed while initial pages load must survive the transition to live updates.
Deduplication covers overlap between pages, live responses, retries, and reconnect recovery.
Use the existing response watermark; an older-page response must not skip unseen live events.

If live recovery exceeds the existing response cap, recover a complete history again.
Keep the last complete snapshot usable while recovery runs.
A late-recorded event with an old occurrence time joins its historical position, not the bottom by default.

On reconnect or chat reactivation, catch up before treating the cached event history as current.
Late responses from an earlier session or an unloaded chat must not populate the newly displayed session.
Hidden sessions remain excluded by the existing application and API behavior.
This feature does not reopen their chats or retain their events as visible content after hiding.

Displaying an event does not create a provider message, send text to the model, change turn state, or mark a message unread.
Existing session-state changes retain their own application behavior.

## 10. Virtual scrolling and navigation

Events are independently measured rows in the existing virtual scroller.
Their identities remain stable across content hydration, toggles, mode changes, retries, and streaming reconciliation.
Event identity must not collide with transcript lines, temporary rows, or date separators.

Variable notice heights, text wrapping, and expanded details participate in the normal height-measurement behavior.
Keep height measurements for unchanged rows. Do not recreate the scroller to insert events.

When an event appears before the reader's current position, preserve the visible reading position.
Apply the same rule to history publication, reconnect recovery, late historical events, and tool-segment restructuring.
At the bottom, preserve the existing follow-new-content behavior.
Do not pull a reader back to the bottom after the reader scrolls away.
User navigation takes precedence over a pending automatic position correction.

Events never become candidates for loading JSONL line ranges.
Existing message search and source-line links retain their original targets.
The top and bottom controls cover the whole displayed chat, including event-only leading or trailing content.
Previous/next conversation-block navigation retains its message-block meaning; events do not create extra conversation blocks.

Keep the same behavior across KeepAlive activation, hidden chat panes, pane resizing, and streaming-to-real row replacement.

## 11. Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| Split displayed tool groups at event boundaries, with independent controls | Adds visible segment controls; preserves the before/after meaning and independent inspection | Selected design proposal |
| Split the visible portions but share one open state across the original group | Reduces state changes; opening one portion also opens tools across the event | Rejected for less direct local control |
| Keep the original closed group and place the event beside its toggle | Requires less group adaptation; the closed summary no longer identifies what precedes the event | Rejected because placement becomes misleading |

All viable approaches use the existing event API and independent event notices.
The user's chronological-visibility requirement rules out hiding notices inside tool groups.

## 12. Acceptance scenarios

| ID | Scenario | Expected behavior |
|---|---|---|
| A1 | A visible session has every current event family | Show its selected events once; exclude turns and instance events |
| A2 | Another session acts on the displayed session | Show the target's event with its useful recorded actor information |
| A3 | The displayed session acts on another session | Do not import that other target's events |
| A4 | Twenty tool items contain a middle event | Meet section 6.1 in all modes, with either segment open or closed |
| A5 | Events meet a message's connected tool prefix or suffix | Keep each event outside the collapsed content and preserve source identities |
| A6 | Several events share a boundary or timestamp | Keep their individual notices and deterministic order; create no empty tool segment |
| A7 | A live event divides an already open or closed segment | Preserve the inherited open state and independent subsequent controls |
| A8 | Conversation details open and close around events | Events stay visible; retained-response and detail-block identities stay unchanged |
| A9 | An empty final assistant message follows tools and an event | Keep the existing empty-message notice policy for the active display mode |
| A10 | A response and an intervening event cross midnight | Correct calendar context; no duplicate or misleading day separator |
| A11 | Message timestamps are disabled | Events and their short times remain visible |
| A12 | A session has more than 100 selected events | Traverse older pages and show the complete recorded history |
| A13 | Initial event loading overlaps a live write or reconnect | No missing or duplicate events after completion |
| A14 | An event-history request fails, then retries | Messages remain usable; show the event error and recover without a false empty state |
| A15 | A live response exceeds the existing cap | Recover complete history while retaining the last complete snapshot |
| A16 | A late event appears above a reader inspecting old messages | Preserve the reading position and insert the event at its historical boundary |
| A17 | The reader is at the bottom, then scrolls away | Follow new content only according to existing chat behavior; respect the reader's new position |
| A18 | A session changes, unloads, or becomes hidden during a request | Do not leak stale results into another chat or make the hidden chat visible |
| A19 | A source row is metadata-only, then hydrates | Keep the event's position; never request its ID as a JSONL line |
| A20 | Streaming and optimistic rows become persisted rows | Keep each event once and preserve normal scroll behavior |
| A21 | A persisted session has events but no messages | Display its events without requiring a first message |
| A22 | Provider timestamps are absent or contradictory | Keep source order and every event through the deterministic fallback |
| A23 | A narrow pane contains long event values or expanded errors | Keep content readable, centered, selectable, and keyboard accessible |
| A24 | The chat resumes through KeepAlive or pane visibility changes | Recover current events and preserve the applicable reading position |
| A25 | Tool segments have different open states; recovery fails, then succeeds with another event | Retain the previous snapshot during failure; preserve unaffected states and inherit the split segment's state on recovery |

## 13. Implementation freedom and existing references

The implementation chooses module boundaries, state shape, projection passes, cache representation, and scroll-compensation mechanics.
It also chooses exact spacing, icons, detail affordances, and the timestamp fallback algorithm within this contract.
These are not unresolved product questions or reasons to expand this specification into a plan.

Useful existing references:

- `frontend/src/utils/visualItems.js`: display filtering, group visibility, date separators, and status-row construction.
- `frontend/src/stores/data.js`: transcript projection, stable row references, and session-history loading.
- `frontend/src/components/session/detail/SessionItemsList.vue`: chat rendering, navigation, streaming reconciliation, and lazy content loading.
- `frontend/src/utils/emptyMessage.js`: empty-assistant-message behavior.
- `frontend/src/utils/chatBlocks.js`: conversation-block navigation.
- `frontend/src/utils/timeline/describeEvent.js`: event descriptions independent of the Timeline layout.
- `frontend/src/utils/timeline/feed.js` and `liveEvents.js`: existing event pagination and live-recovery contracts.
- `frontend/src/utils/timeline/scrollKeeper.js`: existing insertion-position preservation.
- `src/twicc/core/services/timeline_feed.py`: existing REST selection, pagination, and serialization.
- `docs/plans/2026-10-15-timeline-display-design.md`: recorded-event interpretation and existing feed behavior.
- `docs/plans/2026-10-26-timeline-filters-design.md`: exact-session and `without_turns` selection.

## 14. Specification review record

Three internal subagents review this specification across two independent rounds.
Their mandate covers observable behavior, completeness, consistency, and scope.
It explicitly excludes function signatures, internal data structures, algorithms, CSS selectors, and implementation planning.

| Round | Reviewer focus | Result |
|---|---|---|
| 1 | Projection, tool groups, display modes, dates, logical blocks, and navigation | PASS; no material finding |
| 1 | Scope, API pagination, loading failures, live recovery, lifecycle, and reading position | PASS; no material finding |
| 2 | Fresh review of interactions across the full contract | PASS; no material finding |

The second round suggests one optional combined acceptance scenario.
Scenario A25 incorporates it without changing the specified product behavior.
No blocking or important objection remains from these reviews.
The user requests implementation planning after these reviews on 2026-10-27.
Product implementation remains a separate later step.
