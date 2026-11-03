# Timeline Message Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read new user messages and provider-final assistant messages directly in the existing timelines.

**Architecture:** Save complete text once in `TimelineEvent.data`, under `user_message.sent` or `assistant_message.sent`.
Turn events retain stable source references to these message events, including references whose text arrives later.
Use the existing ingestion, timeline recording, feed, and Markdown components.

**Tech Stack:** Django, SQLite, Python `NamedTuple`, Vue 3, existing Markdown renderer, pytest, Node test runner.

**Spec:** `docs/plans/2026-11-03-timeline-message-snapshots-spec.md` (approved).

## Global Constraints

- Record new messages only. Do not backfill existing history, initial imports, or recomputations.
- Use the existing `TimelineEvent` table and its JSON `data` payload.
- Add no table, message model, retention ledger, or separate journal.
- Keep the existing confirmed-turn behavior. A displayed final message does not complete the logical turn.
- Store text snapshots, not thinking, tool calls, tool results, or attachment binaries.
- Keep the full text in this event once. Do not duplicate it in turn events.
- The event kind identifies the role. Do not duplicate it in a JSON `role` field.
- Never attach an earlier or later turn's message by choosing the latest transcript line or closest timestamp.
- Closed journal intervals expire after 48 hours; open intervals expire after seven days. Do not change retention.
- Keep the checkout on `main`. Preserve unrelated changes. Do not modify `CHANGELOG.md`.
- Do not run migrations, restart servers, install packages, or launch paid provider sessions without a separate request.

## Review Focus

1. Several finals and a user send during one logical turn: retain all messages without new completion boundaries (Tasks 1–2).
2. A Claude message spans live slices or has no marker until native completion proof arrives: save one complete message (Tasks 1–2).
3. A turn publishes before indexing, including a queued older closure: retain the exact source owner (Task 2).
4. Historical imports, reconnect, restart, and journal cleanup: no replay, duplicates, lost text, or lost links (Tasks 1–2).
5. Long Markdown, recycled rows, and hidden sessions: expand the correct saved text without changing completion state (Task 3).

## Files and Responsibilities

| Files | Responsibility |
| --- | --- |
| `src/twicc/core/models.py`, `src/twicc/core/timeline.py` | Two new kinds and typed JSON payloads. |
| `src/twicc/core/migrations/0168_timeline_message_kinds.py` | Choices-only `AlterField` for the two new kinds; no new table or column. |
| `src/twicc/core/services/timeline_messages.py` (new) | Source identity, complete text selection, idempotent recording, and targeted recovery. |
| `src/twicc/providers/helpers.py`, `src/twicc/providers/{claude_code,codex}/helpers.py` | Provider-specific text, identities, and final classification. |
| `src/twicc/providers/compute_base.py`, `src/twicc/providers/sessions_watcher.py` | New candidates from live ingestion only; targeted recovery after content publication. |
| `src/twicc/agent/base_agent.py`, `src/twicc/agent/base_manager.py`, `src/twicc/agent/timeline_hooks.py` | Turn-local message references and explicit publication context. |
| `src/twicc/providers/claude_code/agent/agent.py`, `src/twicc/providers/claude_code/agent/hybrid/agent.py` | Claude SDK and hybrid source observations and send identities. |
| `src/twicc/providers/codex/agent/agent.py` | Codex source observations and existing client-message identities. |
| `src/twicc/core/services/timeline_filters.py`, `frontend/src/utils/timeline/filters.js` | Matching `messages` family. |
| `frontend/src/utils/timeline/describeEvent.js`, `frontend/src/utils/timeline/messageSnapshot.js` (new) | Role labels and bounded excerpt view-model. |
| `frontend/src/components/timeline/TimelineEventRow.vue`, `TimelineMessage.vue` (new, same directory) | Expand saved Markdown within an existing timeline row. |
| Tests named below | Provider fixtures, writer transactions, lifecycle regressions, and display contracts. |

## Shared Decisions

**Payloads.** Add `TimelineKind.USER_MESSAGE_SENT = "user_message.sent"` and `TimelineKind.ASSISTANT_MESSAGE_SENT = "assistant_message.sent"`.
Register both kinds with the same `MessageSentData(text: str, source_refs: list, source_line: int)` payload.
The kind identifies the role; the JSON payload has no `role` field.
Each source reference has the existing `NativeMessageRef` shape: `kind`, `native_id`, optional `turn_id`.
Use `claude_message` as the assistant group identity, `claude_uuid` for users, and `codex_item` for canonical Codex messages.
Retain all native aliases of a Claude group. Include a timeline-only `codex_client` alias when a user item has `clientId`.
Never add that alias to the logical journal's completion protocol.

Add `TurnStartedData(message_refs: list | None = None)` and the same optional field to `TurnEndedData`.
An end's references cover the user messages and retained assistant messages of that interval.
A start references its triggering input when known; an automatic start has no user reference.
References match a message event by session and an exact `source_refs` alias.
They do not require a database event ID before the message exists.
There is no reference-resolution API or event-update protocol in this change.

**Deduplication.** Compute `msg:` plus a SHA-256 digest of provider, event kind, and canonical native identity.
Keep the key below the existing 100-character limit. Do not hash the message text, line number, or mutable storage epoch.
Write only through `record_event_sync`. Keep existing best-effort timeline failure behavior.

**Capture boundary.** Give each watcher run a UTC `timeline_since` captured before live watching starts.
Pass it explicitly to live slices; default `None` disables message recording for other callers.
Only newly inserted source groups with native occurrence time at or after that boundary qualify as new capture candidates.
Previously saved turn references retain eligibility across restart; the new boundary must not discard their delayed text.
Batch sync never creates new candidates. It may resolve only exact references already saved by live turns.
Recomputation selects no messages. Existing pending references may resolve when their current content becomes readable.
Historical first-discovery files do not qualify by insertion alone.
Preserve the first source timestamp as `occurred_at`, and source line as metadata.
Within one capture batch, native source order breaks equal-time ties.
Across separate capture batches with strictly equal native timestamps, preserve the existing event-ID arrival tie-break.
Capture each complete message immediately, even when another group's earlier source prefix is incomplete.
This bounded exception keeps native timestamps and the existing feed, cursor, and merge ordering.

**Complete text.** Read current content through the existing content-storage readers.
Use the existing user-message classification after ingestion removes internal context and attachment manifests.
Keep Markdown verbatim, including indentation and trailing spaces. Never use the Conversation selector or a “last message” fallback.
Do not reuse Claude's indexable-text extractor: it trims text blocks and rewrites command arguments.
Group Claude records by their API message ID, using indexed `SessionItem.message_id` to collect split text blocks.
A positive closing marker proves completion of the group.
A verified final UUID can also prove it once its exact terminal record and preceding group text are indexed.
A `claude_message` group ID alone does not prove that its last record is indexed; keep waiting for terminal evidence.
An explicit nonfinal verdict or Codex `delivery=async` prevents final capture.

## Task 1: Record Complete New Message Snapshots

**Files:** Models, migration, payload registry, new `timeline_messages.py`, provider helpers, live ingestion/watcher, and both family registries above.
**Tests:** Create `tests/test_timeline_messages.py`; extend `tests/test_timeline_enums_sync.py`, `tests/test_timeline_filters_sync.py`, `tests/test_timeline_filters.py`, and `tests/test_watcher_catch_up.py`.

**Interfaces:**
- Produce `record_indexed_messages(session_id: str, *, new_line_nums: Sequence[int], timeline_since: datetime | None, verified_final_refs: Sequence[VerifiedMessageRef] | None = None) -> None` in `timeline_messages.py`.
- Produce `record_verified_messages(session_id: str, *, refs: Sequence[VerifiedMessageRef], timeline_since: datetime) -> None` for Task 2's already-admitted writer.
- Produce `recover_referenced_messages(session_id: str) -> None` for existing ingestion publication paths; resolve only saved live-turn references.
- Produce `BaseProviderHelpers.extract_timeline_text_from_parsed(parsed: dict) -> str`, implemented by both providers with verbatim text extraction.
- Extend `BaseSessionCompute.sync_session_slice(..., limits: LiveSyncLimits, timeline_since: datetime | None = None) -> LiveSyncResult`.
- Payload consumers receive the event kind, `data.text`, `data.source_refs`, and `data.source_line`. They derive the role from kind.

- [ ] **Write failing tests.** Use real persisted provider fixtures and production content readers; fixture helpers below describe test-local setup.

```python
def test_records_all_users_and_provider_finals_in_source_order(live_session):
    live_session.append(user("A"), user("B"), final("reply B"), final("reply A"))
    assert live_session.message_texts() == ["A", "B", "reply B", "reply A"]
    assert live_session.message_kinds() == [
        "user_message.sent", "user_message.sent",
        "assistant_message.sent", "assistant_message.sent",
    ]
    assert all("role" not in event.data for event in live_session.message_events())

def test_split_claude_message_waits_for_complete_group(live_session):
    live_session.append(claude_block("m1", "**First**", stop_reason=None))
    assert live_session.message_texts() == []
    live_session.append(claude_block("m1", "Second", stop_reason="end_turn"))
    assert live_session.message_texts() == ["**First**\nSecond"]

def test_replay_and_historical_import_do_not_record_messages(live_session):
    live_session.import_old_history()
    assert live_session.message_texts() == []
    live_session.append(user("new"), final("new reply"))
    live_session.recompute_and_restart_then_append_duplicate_source()
    assert live_session.message_texts() == ["new", "new reply"]
```

Also assert: `tool_use`, `pause_turn`, commentary, thinking, tools, tool results, synthetic local-command output, compaction prompts, and async questions create no message events.
Test a closing marker on a nontext Claude block, empty messages, malformed content, identical text with different native IDs, and duplicate native IDs.
Test proof arriving between two slices: a group ID alone must not publish the indexed prefix.
Pin indented code, trailing Markdown spaces, multiple text blocks, slash-command arguments, text-only attachments, All inclusion, and existing view semantics.

- [ ] **Run the new tests before implementation.** `uv run pytest tests/test_timeline_messages.py tests/test_watcher_catch_up.py -q`.
Expected: new snapshot assertions fail; existing watcher tests remain valid.
- [ ] **Implement recording and the live hook.** Add the interfaces above without adding runtime imports to spawned compute code.
Call capture inside the existing live-ingestion transaction, after inserting complete current records and before advancing the committed checkpoint.
The watcher supplies its capture boundary. Preserve it across bounded slices and retries.
Read only new candidate identities and their Claude groups; do not rescan the entire session for every slice.
Use the normal readers for freshly inserted content; do not invent a retry guarantee after checkpoint commit.
Keep timeline capture best-effort. Do not block ingestion, force storage restoration, or advance source-readiness state for this feature.
For verified references, resolve only the exact referenced native group. Use the capture boundary unless a saved live-turn reference already proves eligibility.
Set assistant actor to `agent`; use source evidence for user origin where available, otherwise use the existing unknown-origin convention.
Do not infer that every `user` role came from the human UI.
Add both new enum members and optional turn payloads; bump `SCHEMA_VERSION` for the changed payload shapes.
Generate only the choices migration. Add both kinds to the `messages` family in both registries.
Use UI label `Messages` and hint `user messages, assistant finals`.
- [ ] **Verify.** `uv run pytest tests/test_timeline_messages.py tests/test_watcher_catch_up.py tests/test_timeline_enums_sync.py tests/test_timeline_filters_sync.py tests/test_timeline_filters.py tests/test_timeline_service.py -q`.
Expected: all pass. `uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test` reports no model changes.
- [ ] **Commit scoped Task 1 files.** Subject: `feat(timeline): record new message snapshots`.
Add a body explaining live-only capture and native identity deduplication, plus the current model's required co-author trailer.

## Task 2: Attach Durable Source References to Existing Turn Events

**Files:** Agent base/manager/hooks and provider agent files above; `timeline_messages.py`.
**Tests:** Create `tests/test_timeline_message_associations.py`; extend `tests/test_timeline_process_hooks.py`, `tests/test_completion_publication.py`, `tests/test_claude_completion.py`, `tests/test_codex_completion.py`, and `tests/test_hybrid_completion.py`.

**Interfaces:**
- Consume Task 1's two recording functions and `NativeMessageRef` JSON shape.
- Add `BaseAgent._timeline_observe_message(ref: NativeMessageRef, *, role: str) -> None` for exact root observations. This internal role selects the kind; it is not a JSON field.
- Add `BaseAgent._timeline_reserve_input(token: str, ref: NativeMessageRef) -> None` for a known transport identity before sending.
- Extend `record_process_changes(..., message_refs: list | None = None) -> None` and `record_process_ended(..., message_refs: list | None = None) -> None`.
- Extend `_persist_process_run_transition_admitted(..., message_refs: list | None = None) -> None` to carry the explicitly selected interval's immutable references.

- [ ] **Write failing tests.** Extend the existing fake SDK and admitted-writer fixtures. Test both providers and hybrid mode.

```python
def test_end_before_indexing_keeps_exact_message_references(turns):
    old, current = turns.finish_two_intervals_without_indexing()
    turns.index(current.sources)
    turns.index(old.sources)
    assert turns.linked_texts(old.end_event) == old.expected_texts
    assert turns.linked_texts(current.end_event) == current.expected_texts

def test_one_interval_keeps_every_user_and_final(turns):
    turn = turns.exchange("A", "B", "reply B", "reply A")
    assert turns.linked_texts(turn.end_event) == ["A", "B", "reply B", "reply A"]
    assert turns.turn_end_count() == 1

def test_links_survive_journal_cleanup_and_restart(turns):
    turn = turns.completed_exchange()
    before = turns.read_saved_exchange(turn)
    turns.expire_journal_and_restart()
    assert turns.read_saved_exchange(turn) == before
```

Also assert: start references A; a send B during work joins the end references without changing the start.
A failed unconfirmed send has no message event; its unresolved reference never resolves to an earlier prompt.
Test Claude with no SDK user echoes: start references A, end references A/B, and only later indexing creates their two snapshots.
An automatic turn has no old user trigger. A verified final without a marker records only its exact complete source group.
Test source-first and publication-first ordering, rollback/retry, source replacement, boot-estimated ends, and a queued old closure publishing after new activity.
Test a markerless final indexed during a logical hold, and delayed text resolved by startup ingestion after restart.

- [ ] **Run before implementation.** `uv run pytest tests/test_timeline_message_associations.py tests/test_timeline_process_hooks.py -q`.
Expected: new association assertions fail.
- [ ] **Implement turn-local references.** Keep ordered, deduplicated native references on the agent, keyed by the actual logical interval ID.
Capture their immutable copy before awaiting writer admission. Never read the controller's latest interval to enrich an older end.
Collect user references from confirmed root observations or exact accepted-input reservations; collect assistants from positive final evidence.
Retain every physical final within one logical interval. Only actual indexed source evidence creates a user message event.
Bind reservations through the existing token's `CompletionInput.turn_id`; keep unbound reservations until `_completion_activity` binds them.
Do not require SDK user echoes or enable `replay-user-messages`.
The registry holds identities only, not text, and drops an interval only after its `turn.ended` publication commits.
Publishing `turn.started` never clears that interval's user references.
Keep retry state until commit; rollback must not consume references.
For starts before source confirmation, reserve only an exact transport identity for the pending input.
For Claude SDK sends, thread the existing completion-token UUID into streamed user messages as `uuid`; the installed SDK documents this field.
For Codex, reuse a submission's existing `client_message_id`; otherwise pass the existing completion token as `client_user_message_id` on start and steer routes.
Do not change wait-token meaning or create a second submission ledger.
Hybrid user observations contain native UUIDs; use those when available before the existing start publication.
If a trigger identity is not yet known, leave start references empty; retain the later confirmed user reference on the end.
Do not delay a start, change a boundary, or rewrite an existing event to fill that gap.
Automatic activity carries no reserved user reference. Ignore internal wakeups, shell notifications, replayed history, and subagent traffic.
Pass the selected references explicitly into existing timeline hooks for normal, exceptional, and stop transitions.
Leave boot-estimated and pre-rollout turns without invented references.
Invoke Task 1's exact-reference recorder during native final publication so source-first missing-marker messages become eligible before logical completion.
Use the watcher's capture boundary for that session, not the agent's send time; expose it through the existing watcher instance.
For proof-first arrival during a logical hold, live capture also uses copied verified runtime identities.
Copy each identity with its existing opening provider, source floor, storage incarnation, and history epoch in `VerifiedMessageRef`.
The runtime copy survives the journal's seven-day cleanup without recreating a row or adding persistence.
Both live capture and final publication validate this opening scope against the current source.
Retained `LogicalTurn.final_refs` remain usable under their existing scope and finality rules.
A missing journal row must not suppress exact verified-message capture or change completion publication results.
After turn publication, saved final references in timeline data provide durable proof without the logical journal.
In each live `sync_session_slice`, use the new-line/group recorder to resolve matching pending references.
Do not run full `recover_referenced_messages` on each live slice.
Run explicit recovery after turn publication and from `_apply_session_complete_final` when current content becomes readable.
The latter hook covers initial-sync metadata publication. Recompute can only resolve already saved references, never select a new candidate.
Resolve only explicit saved live-turn references, deduplicate through the same event keys, and ignore absent sources.
This completes pending new captures; it does not scan unreferenced transcript messages or create new historical candidates.
Keep these operations within the existing admitted transaction; do not introduce a new queue, polling task, journal field, or event mutation.
- [ ] **Verify.** `uv run pytest tests/test_timeline_message_associations.py tests/test_timeline_process_hooks.py tests/test_completion_publication.py tests/test_claude_completion.py tests/test_codex_completion.py tests/test_hybrid_completion.py tests/test_mcp_events_logical_turns.py tests/test_logical_turn_cleanup_task.py -q`.
Expected: all pass. Counts and timing of completion, unread, and `session.concluded` remain unchanged.
- [ ] **Commit scoped Task 2 files.** Subject: `feat(timeline): link messages to confirmed turn events`.
Explain stable references, late indexing, and unchanged journal retention in the body. Include the required co-author trailer.

## Task 3: Show Expandable Saved Messages in the Existing Feed

**Files:** UI files above; extend `tests/test_timeline_api.py`, `tests/test_timeline_hidden_sessions.py`, and frontend timeline tests.
**Create tests:** `frontend/src/utils/timeline/messageSnapshot.test.js`, `frontend/src/components/timeline/TimelineMessage.test.js`.

**Interfaces:**
- Consume Task 1's `user_message.sent` and `assistant_message.sent` events through the existing feed serializer, which already returns `kind` and `data`.
- Produce `describeMessageSnapshot(kind: string, data: object) -> {role: string|null, text: string, excerpt: string, expandable: boolean, hasMoreLines: boolean}` in `messageSnapshot.js`.
- Produce `TimelineMessage.vue` with `eventId: Number`, `kind: String`, and `data: Object` props. It consumes that view-model and `MarkdownContent`'s `source` prop.
  Its `expanded` model connects the row's clickable label, collapsed preview, and final close control.
  Its `more-change` event tells the label when to show its chevron, including width changes while expanded.
  Optional `contentId` and `labelledBy` props connect the toggle controls to the complete-text region.

- [ ] **Write failing tests.** Follow existing pure-function and Vue source-contract test patterns.

```javascript
test('snapshot keeps full Markdown and previews its first text line', () => {
    const text = '**Reply**\n' + 'x'.repeat(10000)
    const vm = describeMessageSnapshot('assistant_message.sent', { text })
    assert.equal(vm.role, 'assistant')
    assert.equal(vm.text, text)
    assert.equal(vm.excerpt, 'Reply…')
    assert.equal(vm.hasMoreLines, true)
    assert.equal(vm.expandable, true)
})
```

Pin labels `User message` and `Assistant message`; derive role only from event kind, separately from event origin.
Test both kinds, unknown-kind and malformed-payload fallbacks, All inclusion, `messages` filtering, and saved text after source mutation.
Test project, workspace, global, and hidden-session feed behavior through existing API fixtures.
Pin row-local expansion reset when `eventId` changes. Verify keyboard expansion and no navigation or read-state action.

- [ ] **Run before implementation.** `cd frontend && node --test src/utils/timeline/messageSnapshot.test.js src/components/timeline/TimelineMessage.test.js`.
Expected: the new helper/component contract fails.
- [ ] **Implement the view-model and row body.** Preview the first nonempty plain-text line, without a character cutoff.
Keep one visual line with CSS ellipsis. For additional lines or measured overflow, place a chevron after the message label.
Use one native label button with `aria-expanded`: its chevron points down when collapsed and up when expanded.
Make the collapsed preview a native open button, including short messages. Move focus to the persistent label after opening from it.
Hide the preview while expanded. Put `Close message` at the end and return focus to the persistent label.
Keep an invisible, out-of-flow preview while expanded to measure width changes. Disable its button while hidden.
Release its observer on unmount.
Render the complete saved text only while expanded, through the existing `MarkdownContent` component.
Use `defineAsyncComponent` for that renderer to avoid store/component import cycles. Do not use raw `v-html`.
Keep full text out of row titles, tooltips, and session navigation. Preserve session, role, and event time.
Reset expansion when a recycled row receives another event ID; retain the existing dynamic-height scroller behavior.
No change to `useWebSocket`, unread logic, notifications, Conversation mode, feed cursors, or the merge protocol.
- [ ] **Verify.** `cd frontend && node --test src/utils/timeline/*.test.js src/components/timeline/*.test.js src/composables/useTimelineFeed.test.js`.
Run `uv run pytest tests/test_timeline_api.py tests/test_timeline_hidden_sessions.py tests/test_timeline_api_filters.py tests/test_timeline_transports.py tests/test_cli_timeline.py tests/test_timeline_message*.py -q` from the repository root.
Expected: all pass. Run `cd frontend && npm run build`; expect a successful build without new dependencies.
On an already available dev UI, inspect a fresh test fixture feed: multiple finals, long Markdown, expand/collapse, and session filters.
Do not start or restart servers to perform this check; report any unavailable browser verification.
- [ ] **Commit scoped Task 3 files.** Subject: `feat(timeline): show expandable message snapshots`.
Explain the existing feed integration and saved-text expansion in the body. Include the required co-author trailer.

## Completion Gate

- [ ] Run one independent final review against the approved spec and all three scoped commits.
- [ ] Fix material findings in their owning task; run the relevant checks again.
- [ ] Inspect `git diff --check`, the scoped commit list, and `git status --short`. Preserve unrelated files.
- [ ] Report tests actually run, unavailable checks, and the choices migration. Remind the user to apply migrations and restart the backend.

This plan does not authorize product implementation before the user's plan review.
