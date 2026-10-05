# Codex Async Questions in the Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show optional Codex async questions above the composer textarea and send their answers with the user's message.

**Architecture:** A provider-specific reducer merges SDK and rollout evidence into durable question state. The Codex manager serializes answer validation and retirement with ordinary sends. The composer uses shared question controls and extends existing draft and send recovery.

**Tech Stack:** Django 6, Python >= 3.13, SQLite, orjson, Channels, Vue 3, Pinia, IndexedDB, Web Awesome, pytest, node:test.

**Spec:** [2026-10-05-codex-async-questions-composer-spec.md](2026-10-05-codex-async-questions-composer-spec.md), committed as `e8901d26`.

**Status:** Author self-review and two-round adversarial review complete. Awaiting user review and execution-method selection.

## Global Constraints

- Use the existing question widget style inside the composer, directly above the textarea.
- Provide `Other...` for free text. Make every question optional. Use one Send action.
- Use actual turn lifecycle evidence. Do not derive readiness from message phase or display state.
- Do not convert async questions into blocking pending requests or add an SDK response RPC.
- Preserve source transcript messages, normal send locks, commands, attachments, and send recovery.
- Generated headings are English. Source questions, source options, and user text retain their original language.
- Keep source identities stable across SDK ingestion, JSONL ingestion, reload, and recompute.
- Preserve draft text and answers after failure or external resolution. Never send automatically.
- Do not implement until the user reviews this plan and chooses the execution method.
- Stay on the current branch. Preserve unrelated changes. Do not change CHANGELOG.md.
- Add no dependencies. Do not run migrations, package installation, or dev server restart without the user's request.
- Use project DB-writer serialization for mutations. CPU compute workers must not write database state.
- Use NamedTuple for immutable Python records and orjson for backend JSON.
- Use getParsedContent() for frontend session-item access. Avoid static import cycles.

## Review Focus

1. A steering message before readiness must not remove questions after historical rebuild. Tasks 1–3 test this timeline.
2. A question first ingested after its relevant human reply must stay resolved. Tasks 1–3 test reversed arrival order.
3. A second browser resolving a question must preserve local Other text, even without a send attempt. Tasks 6 and 8 test recovery.
4. A lost acknowledgement must not duplicate answers or recover the sender's own accepted answers as external changes. Tasks 4 and 8 test both event orders.
5. An answer-only send must not become a settings-only action or alter slash-command dispatch. Tasks 4 and 7 test content classification.

## File and Responsibility Map

| File | Responsibility |
|---|---|
| New `src/twicc/providers/codex/async_questions.py` | Django-free normalization, chronology reducer, validation, answer formatting |
| New `src/twicc/core/services/async_questions.py` | Durable merge, snapshot, send preparation/acceptance, dismissal |
| `src/twicc/core/models.py` and new migration | One question-state record per Codex session |
| `src/twicc/providers/compute_base.py` | Transport extracted question evidence through existing compute apply paths |
| `src/twicc/providers/codex/compute.py` | Extract canonical question and turn/user facts; preserve source messages |
| `src/twicc/providers/codex/agent/agent.py` | Record SDK questions and true control-return boundaries |
| `src/twicc/providers/codex/agent/manager.py` | Validate and format answers inside the existing per-session send gate |
| `src/twicc/providers/codex/sdk_wrappers.py`, `agent/goal_continuation.py` | Forward native client user-message IDs through start, steer, and fallback |
| `src/twicc/asgi.py`, shared send service, CLI send commands | Carry send intent and existing acceptance/error replies |
| `src/twicc/views.py`, `src/twicc/urls.py` | Authenticated session question snapshot endpoint |
| New `frontend/src/utils/asyncQuestions.js` | Pure answer formatting and local recovery transforms |
| New `frontend/src/components/message/QuestionFields.vue` | Shared cards, Other input, keyboard interactions |
| Existing `RequestUserInputBody.vue` | Blocking host for shared controls; preserve current contract |
| New `frontend/src/components/message/AsyncQuestions.vue` | Optional composer question host; no Submit action |
| `frontend/src/stores/data.js`, `useWebSocket.js` | Snapshot state, revision ordering, outgoing send recovery |
| `frontend/src/utils/draftStorage.js`, `inflightStorage.js` | Transactional local choices and combined-send snapshots |
| `MessageInput.vue`, `SessionItemsList.vue`, `FailedSendBanner.vue` | Composer integration, single send, existing footer and failed-message behavior |

New test paths appear in their owning tasks. Migration numbering follows the actual migration graph at implementation time.
Do not assume the current `0150_session_automatic_titles` leaf remains the latest leaf.

## Shared Contracts

### Source facts and durable state

Use plain JSON-compatible values between compute processes. Python types below are NamedTuple records at public pure-function boundaries.

```python
class AsyncQuestion(NamedTuple):
    index: int
    title: str
    options: list[str]

class QuestionFact(NamedTuple):
    key: str
    kind: str
    at: str  # UTC ISO-8601, source occurrence time
    turn_id: str | None
    item_id: str | None
    line: int | None
    data: dict

class PreparedQuestionSend(NamedTuple):
    text: str
    submission: dict | None
```

Facts use distinct stable keys: `question:<item-id>`, `end:<turn-id>`, `owner:<group-id>`, `decision:<turn-id>`, `return:<group-id>`, `user:<item-id>`, `send:<request-id>`, and `dismiss:<request-id>`.
SDK question upserts and JSONL question upserts share the same key. JSONL adds line metadata without resetting decisions.
Kinds are `question`, `turn_end`, `live_owner`, `settlement_decision`, `control_return`, `user_submission`, and `dismiss`.
`turn_end` means provider completion; `control_return` means no immediate automatic continuation owns the next turn.
An interrupt or terminal failure records `control_return` with `outcome: interrupted|failed` when normal composition is available.

`live_owner` records `{group_id, root_turn_id, state: pending}` before a TwiCC-owned turn starts.
The server creates group_id before the RPC. When the RPC returns its turn ID, link that ID to the pre-existing owner.
Keep the owner admission active across the RPC, so a watcher completion received before that link cannot release questions.
`settlement_decision` records `{group_id, turn_id, decision: pending|continuation|return, successor_turn_id: string|null}`.
Write `pending` for every owned physical turn before processing completion. A known live owner suppresses raw turn-end readiness.
Write `continuation` before starting its successor. Link successor when its ID becomes available; the group's owner stays pending throughout.
Write `return` and `control_return` together after the no-continuation decision, before the subagent hold.
An active goal owns its whole group, including physical turns announced by the goal route before the agent consumes their completion.

Historical import without live ownership derives control return from terminal source turns and known internal continuation/goal evidence.
Run historical reconstruction over the complete ordered evidence set; do not publish intermediate ready state during its rebuild.
For goals, active goal context suppresses intermediate completion until terminal goal state or human interruption.
For auto-review/internal continuation prompts, group the associated successor with its preceding completed turn.
Runtime ownership/settlement decisions override this historical fallback and survive recompute.
After restart, reconcile pending owners against the resumed/read-only provider thread and persisted goal state.
If no turn is running and no automatic continuation remains armed, record interrupted/failed control return.
If status cannot be established, keep collecting until normal composition is restored by explicit stop or successful resume reconciliation.

State schema version 1:

```text
{schema: 1, revision: int,
 facts: {stable_key: fact},
 batches: {item_id: {item_id, turn_id, at, line, questions,
                   status: collecting|ready|sent|dismissed,
                   resolved_request_id: string|null}}}
```

Store facts and the derived projection together. Retain resolution evidence for the session lifetime.
Facts include human-submission boundaries even when no question is currently known. This supports late question discovery.
Store provider source timestamps plus source lines when available. Do not order evidence by database insertion time.
When comparing source records, line order wins within the same canonical rollout; timestamps join SDK facts before source lines exist.
Never compare a runtime observation timestamp against a provider source timestamp when a linked source item supplies the ordering.

For a live send, durably prepare a boundary before awaiting provider delivery: source time, current settled turn IDs, known eligible batch IDs.
Question generation after that boundary remains unresolved. Older late-ingested questions use the recorded settled-turn boundary.
Known continuation groups share one control-return boundary. A raw intermediate `turn_end` cannot release their questions.

Submission data includes `{request_id, origin, boundary, status, client_message_id, source_item_id, target_turn_id, delivery_route}`.
Statuses are `prepared`, `accepted`, `rejected`, and `uncertain`. Only accepted or source-proven delivered submissions retire questions.
Persist prepared before the SDK call; preserve the same boundary and identity across rejected-steer fallback attempts.
Use the native protocol `clientUserMessageId`, set to the submission request ID for ordinary user inputs.
Existing generated `TurnStartParams` and `TurnSteerParams` already expose `client_user_message_id`.
Add forwarding in TwiCC's wrappers, without editing the vendored SDK or generated types.
SDK UserMessage `clientId` links to request ID; its server item ID links to the canonical JSONL UserMessage.
Persist that association as soon as SDK input evidence arrives. On restart, reconcile native client IDs through thread history.
Do not infer delivery solely from identical text or message insertion time.
If source history lacks the native association, keep the submission uncertain; do not classify a potentially matching unlinked UserMessage twice.
Exclude unmatched runtime source user records within uncertain submission routes from retirement until reconciliation establishes their origin.
Source user facts linked to a submission enrich it; they never create a second human-submission boundary.
Live origin and original submission boundary win over the later source UserMessage timestamp, including for steering.

### Server snapshot and send payload

```text
snapshot = {revision, batches: [collecting_or_ready_batch],
            resolutions: {item_id: {status: sent|dismissed, request_id: string|null}},
            widget_enabled: bool}

send_message.async_questions = {
  revision: int,
  batch_ids: [item_id],
  answers: [{item_id, index, kind: option|other, value: string}]
}
```

`batch_ids` includes all ready batches presented at the user's submission boundary, including unanswered batches.
Use batch membership and immutable question content to validate staleness. A newer revision alone does not invalidate a send.
Ignore new batches not in the submission. Reject resolved referenced batches with code `async_questions_stale`.
Reject invalid answer identities or option labels with code `async_questions_invalid`.
The response carries original `request_id` and `session_id`, using the existing error envelope.

Send text remains the raw textarea text. Backend construction produces the final combined text.
The frontend builds the same text for the optimistic bubble and matching existing recovery snapshots.
Never feed already formatted snapshot text through the formatter a second time.

### Source provenance

Manager send intent is `human`, `agent`, or `internal`.
The authenticated browser supplies `human` through its trusted server entry point.
CLI send commands use `resolve_current_session()` and external MCP caller context: agent/MCP calls use `agent`; ordinary human CLI uses `human`.
Do not expose a caller-controlled CLI flag to override this intent.
Automatic Codex continuations use `internal` and never retire questions.
Carry provenance as private send bookkeeping, not model-visible text.
For historical rollouts, exclude known injected command/resume/goal/auto-review messages and sender-header/inter-agent records before classifying a UserMessage as human.
Persist live provenance when available; recompute must not override it with a generic historical classification.

## Task 1: Pure Question Protocol and Timeline Reducer

**Files:** Create `src/twicc/providers/codex/async_questions.py`; create `tests/test_codex_async_question_protocol.py`.

**Interfaces:**

- `normalize_questions(raw: object) -> list[AsyncQuestion]`.
- `question_fact(record: dict, *, source: str, at: str, line: int | None = None) -> QuestionFact | None`.
- `reduce_question_state(state: dict, facts: list[QuestionFact]) -> dict`.
- `format_question_answers(batches: list[dict], answers: list[dict], text: str) -> str`.
- `validate_question_answers(snapshot: dict, response: dict | None) -> list[dict]` raises `ValueError` with the two stable error codes above.

- [ ] Write table-driven tests for normalization and chronology, including the two observed protocol shapes.

```python
def test_steering_before_control_return_survives_replay():
    state = reduce_question_state({}, [question(), human_submit(), control_return()])
    assert state["batches"]["q1"]["status"] == "ready"

def test_late_question_after_human_reply_stays_sent():
    state = reduce_question_state({}, [control_return(), human_submit(), question()])
    assert state["batches"]["q1"]["status"] == "sent"
```

Fixture helpers supply explicit source timestamps and turn IDs. Both arrival orders must represent the same source timeline.
Cover repeated SDK/JSONL facts, duplicate option labels, invalid entries, preserved original indexes, no-options free text, and repeated wording with different IDs.
Cover interrupted turns, intermediate completion inside continuation groups, and a question generated after a submission boundary.
Verify formatter output exactly matches spec section 7, including English headings and untranslated source/user text.

- [ ] Run `uv run pytest tests/test_codex_async_question_protocol.py -q`; confirm new tests fail for missing functions.
- [ ] Implement the public interfaces and deterministic reducer. Preserve resolutions and live-provenance facts on repeated merge.
- [ ] Run the same command; require PASS for every fixture and both arrival orders.
- [ ] Commit only this task's files with subject `feat(codex): normalize async question evidence` and a descriptive body plus current model trailer.

## Task 2: Durable Question State and Service

**Files:** Modify `src/twicc/core/models.py`; create a migration under `src/twicc/core/migrations/`; create `src/twicc/core/services/async_questions.py`; create `tests/test_async_question_state.py`.

**Interfaces:**

- Model `AsyncQuestionState`: `session` OneToOne FK with CASCADE, `state` JSONField(default=dict).
- Synchronous DB service `merge_question_facts(session_id: str, facts: list[QuestionFact]) -> dict`.
- `read_question_snapshot(session_id: str) -> dict`.
- `prepare_question_send(session_id: str, text: str, response: dict | None, *, request_id: str, origin: str, at: str) -> PreparedQuestionSend`.
- `accept_question_send(session_id: str, submission: dict) -> dict`.
- `dismiss_question_batch(session_id: str, item_id: str, *, request_id: str) -> dict`.
Retain dismissal request ID in the resolved projection.

Call mutations only through the existing DB writer (`run_under_db_write_lock` / compute apply ownership).
Use short atomic transactions. Never keep a DB write transaction open while awaiting an SDK call.
Service functions validate Codex/main-session membership. Reads for other providers return an empty snapshot.
Empty snapshot: `{revision: 0, batches: [], resolutions: {}, widget_enabled: effective_setting}`.
Increment revision only for an observable state change. All resolved IDs remain represented for local recovery.

- [ ] Write service tests for SDK/JSONL deduplication, dismissal persistence, source metadata enrichment, FK cascade, and revision stability.

```python
def test_recompute_preserves_dismissal(db, session):
    merge_question_facts(session.id, [question(), control_return()])
    dismiss_question_batch(session.id, "q1", request_id="dismiss-1")
    snapshot = merge_question_facts(session.id, [question(), control_return()])
    assert snapshot["resolutions"]["q1"]["status"] == "dismissed"
    assert snapshot["batches"] == []
```

Test prepare writes a prepared submission but leaves questions ready; acceptance retires eligible questions; definite rejection leaves questions ready.
Mark ambiguous delivery uncertain. Do not discard its immutable provenance or boundary during rebuild.
Store each accepted submission's immutable boundary for late-ingested older questions, even if its ready list was initially empty.
Test a stale batch with a newer unrelated batch, and an unanswered ready batch in an accepted send.

- [ ] Run `uv run pytest tests/test_async_question_state.py -q`; confirm meaningful failures.
- [ ] Implement the model and service. Generate a migration using the project migration graph, without applying it to the running instance.
Run migration generation in an isolated scratch data directory, with TWICC_DATA_DIR pointing there, so checks cannot use the production DB.
- [ ] Run the same tests; require PASS and confirm no change to production data outside pytest's test database.
- [ ] Commit task files with subject `feat(codex): persist async question lifecycle` and the required body/trailer.

## Task 3: SDK, JSONL, Recompute, and Actual Turn Completion

**Files:** Modify `src/twicc/providers/codex/agent/agent.py`, `src/twicc/providers/codex/compute.py`, `src/twicc/providers/compute_base.py`; modify the actual Codex compute-version declaration; create `tests/test_codex_async_question_lifecycle.py`; extend `tests/test_codex_recompute_persistence.py`.

**Interfaces:**

- Pure extractor `extract_async_question_facts(record: dict, *, line: int) -> list[QuestionFact]` in the protocol module.
- Base compute hook `extract_async_question_facts(...)` defaults to no facts for other providers.
- Compute result key `async_question_facts`: complete canonical evidence from the CPU worker.
- Live sync batches call the same durable merge inside their existing commit.
- SDK helper `_record_async_question_fact(fact: QuestionFact) -> None` performs DB-writer merge and post-commit publication.
- Main-turn helper `_settle_async_questions(turn_id: str, *, outcome: str) -> None` records actual control return.

Do not mutate DB from full-compute CPU workers. Apply evidence only after the current apply-session fingerprint/version guard succeeds.
Append facts within live compute transactions; merge authoritative rebuild facts without erasing runtime submissions or dismissals.
Use provider message provenance and existing canonical helpers. Do not double-count legacy response-item message copies.

- [ ] Write lifecycle tests using stubbed SDK events and canonical JSONL fixtures.

```python
def test_final_answer_phase_does_not_release_question():
    state = replay([async_agent_message(phase="final_answer"), tool_call()])
    assert state["batches"]["q1"]["status"] == "collecting"

def test_parent_completion_releases_question_during_subagent_hold():
    state = finish_parent_turn(with_live_child=True)
    assert state["batches"]["q1"]["status"] == "ready"
    assert state["process_state"] == "assistant_turn"
```

Cover auto-review retry, goal continuation, Plan implementation prompt, stopped/failed turn, and abrupt backend restart with no completion.
Test watcher task_complete before the live settlement decision, and restart in that exact window.
Assert pending owner suppresses readiness, then return releases or continuation keeps collecting.
Test historical replay without a manager using explicit goal-active/terminal and internal successor fixtures.
Persist automatic continuation links before starting the successor; publish control return only after the whole continuation group settles.
Test JSONL recovery separately from SDK mocks. A middle turn_end must not bypass an existing live continuation decision.
Test historical steering and late question ingestion in both full recompute and live incremental batches.
Test source-line remapping during rollout replacement: semantic item IDs preserve dispositions.

- [ ] Run `uv run pytest tests/test_codex_async_question_lifecycle.py tests/test_codex_recompute_persistence.py tests/test_codex_subagent_hold.py -q`; confirm failures for missing hooks.
- [ ] Add SDK completed-message detection and control-return hooks at actual settlement paths, before subagent holds and after immediate continuation decisions.
- [ ] Add extraction and guarded compute apply. Increment the current Codex compute version once.
- [ ] Run the same tests; require PASS with unchanged existing hold behavior.
- [ ] Commit with subject `feat(codex): reconcile async questions across turn lifecycles` and the required body/trailer.

## Task 4: Combined Send, Provenance, and Acceptance

**Files:** Modify `src/twicc/providers/codex/agent/manager.py`, `src/twicc/providers/codex/agent/agent.py`, `src/twicc/providers/codex/agent/goal_continuation.py`, `src/twicc/providers/codex/sdk_wrappers.py`, `src/twicc/asgi.py`, `src/twicc/core/services/send_message.py`, `src/twicc/cli/send_message/command.py`, `src/twicc/cli/send_messages.py`; create `tests/test_codex_async_question_send.py`; extend `tests/test_codex_sdk_wrappers.py`.

**Interfaces:**

- Add optional keyword args to Codex `send_to_session`: `async_questions: dict | None = None`, `request_id: str | None = None`, `send_origin: str = "internal"`.
- Carry those arguments through `_send_to_session_under_gate` without changing its existing settings/images/documents contract.
- Ordinary human CLI generates a server-traceable send ID if no browser request ID exists. Carry origin in private CLI transport payload.
- Browser handler forwards `async_questions`, original request ID, and trusted `human` origin only for existing Codex sessions.
- Reject async-question fields on draft creation or other providers; leave their ordinary sends unchanged.
- Extend TwiCC `turn_with_policy(..., client_user_message_id: str | None = None)` and forward the existing native parameter.
- Add TwiCC wrapper `steer_with_message_id(handle: AsyncTurnHandle, input: RunInput, *, client_user_message_id: str) -> TurnSteerResponse` using the existing generic client request and generated TurnSteerParams.
- Extend `GoalContinuation.steer(..., client_user_message_id: str | None = None)` through the same native steer parameter.
- Carry submission identity through agent.send, turn scheduling, _open_turn, rejected-steer fallback, and source user-message association.

Within `gate_for(session_id)`, validate, commit prepared submission/boundary, format text, call the existing send implementation, then accept only if it returns True.
Dismissal takes the same migration/send gate so it cannot invalidate a validated send during SDK delivery.
Capture plain human text/attachment submission boundaries too. Agent/internal messages do not resolve questions.
Do not hold the global DB-writer lock while awaiting the manager or SDK. Do not reacquire the per-session gate recursively.

Parse hardcoded commands from raw textarea text before formatting. Reject selected answers plus a hardcoded command with `async_questions_command`.
Dispatch commands and settings-only actions without recording a human response boundary.
Treat selected answers as content before settings-only/empty-text checks, including the answer-only case.
Use `SendDeliveryError` for typed validation failures, preserving existing request-correlated error frames.

- [ ] Write manager and WS tests for one combined SDK send, untranslated text, partial answers, attachment-only sends, and answer-only sends.

```python
def test_answer_only_is_a_real_message(send_harness):
    send_harness.send(text="", answers=[option_answer("q1", 0, "Yes")])
    assert send_harness.sdk_text == "Answers to your questions:\n\nQuestion: Open the session?\nAnswer: Yes"
    assert send_harness.accepted_batches == ["q1"]
```

Test rejection leaves questions/drafts intact; newly generated batches during delivery remain ready; SDK returns False does not retire anything.
Test browser/human CLI retire and agent/MCP/internal/commands do not retire.
Test selected answers plus `/compact`, `/plan`, and `/goal` follow the explicit command rule.
Test crash after provider delivery and before acceptance; native client ID evidence recovers acceptance without issuing another SDK send.
Test late rollout steering UserMessage after readiness: its linked prepared boundary remains before readiness and must not retire collecting questions.
Test start, steer, goal steer, and rejected-steer fallback use the same client message ID for the same submission.
Test agent input without a sender header retains private agent origin after full recompute.
If a provider exception does not prove rejection, retain uncertain status and expose correlated `send_uncertain`; existing recovery must prohibit Retry until resolved.
Do not treat provider delivery plus failed bookkeeping as a definite non-delivery error that invites retry.
If manager acceptance only schedules a background start, carry its prepared record into that start and associate the later provider user item.
Reconcile startup failure using the existing send-failure path, without converting its original boundary into a later human reply.

- [ ] Run `uv run pytest tests/test_codex_async_question_send.py tests/test_codex_send_fallback.py tests/test_cli_session_command.py -q`; confirm new failures.
- [ ] Implement the argument plumbing, trusted origin classification, and manager-gated send preparation/acceptance.
- [ ] Run the same tests and `uv run pytest tests/test_codex_sdk_wrappers.py -q`; require PASS, including existing fallback and command tests.
- [ ] Commit with subject `feat(codex): send async question answers with messages` and the required body/trailer.

## Task 5: Snapshot Hydration, Updates, and Dismissal

**Files:** Modify `src/twicc/views.py`, `src/twicc/urls.py`, `src/twicc/providers/codex/ws.py`, `src/twicc/core/services/session_update.py`; extend the durable service; create `tests/test_async_question_api.py`.

**Interfaces:**

- GET `/api/projects/<project_id>/sessions/<session_id>/async-questions/` returns the shared snapshot.
- Use `_resolve_session_or_404` and existing authentication. Do not create a share endpoint.
- WS update `{type: "async_questions_updated", session_id, snapshot}` publishes after commit.
- WS action `{type: "codex_dismiss_async_question", session_id, item_id, request_id}`.
- Dismiss acknowledgement `{type: "async_question_dismissed", session_id, item_id, request_id, snapshot}`.
- Idempotent dismiss of an already resolved known batch returns its current snapshot. Unknown batch produces a correlated error.

Record request ID before broadcasting dismissal. The same ID appears in resolution and acknowledgement.

Publish from every SDK merge, live/recompute apply, accepted send, and dismiss that changes the visible projection.
Use the ordinary hidden-session broadcast policy. Read the current effective `question_widget` in snapshots.
Avoid attaching DB queries to every session-list serializer; hydrate through this session endpoint independently of message pagination.
After update_session_settings_from_payload changes question_widget, publish a refreshed snapshot so other browsers hide/show controls immediately.

- [ ] Write authenticated API tests for empty state, unresolved plus resolutions, hidden sessions, disabled widgets, other providers, and subagent rejection.

```python
def test_snapshot_does_not_depend_on_loaded_message_range(client, ready_session):
    result = client.get(question_url(ready_session)).json()
    assert [batch["item_id"] for batch in result["batches"]] == ["q1"]
```

Test dismissal preserves source conversation text, never starts a turn, and broadcasts only after transaction success.

- [ ] Run `uv run pytest tests/test_async_question_api.py -q`; confirm missing endpoint/action failures.
- [ ] Implement endpoint, update publication, and gated dismissal using the service contracts.
- [ ] Run the same tests; require PASS.
- [ ] Commit with subject `feat(codex): expose async question snapshots` and the required body/trailer.

## Task 6: Frontend State, Draft Persistence, and Pure Recovery

**Files:** Create `frontend/src/utils/asyncQuestions.js` and `.test.js`; modify `frontend/src/utils/draftStorage.js`, `frontend/src/stores/data.js`, `frontend/src/composables/useWebSocket.js`, `frontend/src/main.js`; create `frontend/src/utils/asyncQuestionStorage.test.js`.

**Interfaces:**

- Pure `formatAsyncQuestionMessage(batches, answers, text) -> string` matches the Python formatter fixture byte-for-byte.
- Pure `recoverResolvedAnswers({draft, choices, snapshot, pendingSends, pendingDismissals}) -> {draft, choices, recoveredIds, notice}`.
- Store getter `getAsyncQuestionSnapshot(sessionId)` and actions `loadAsyncQuestions(projectId, sessionId)`, `applyAsyncQuestionSnapshot(sessionId, snapshot)`.
- Store actions `setAsyncQuestionDraft(sessionId, draft)`, `dismissAsyncQuestion(projectId, sessionId, itemId)`, `hydrateAsyncQuestionDrafts()`.
- Storage functions `saveAsyncQuestionDraft(sessionId, draft)`, `getAllAsyncQuestionDrafts()`, `deleteAsyncQuestionDraft(sessionId)`.

Add one IndexedDB store `asyncQuestionDrafts`; advance DB_VERSION from the actual value at implementation time.
Draft record: `{choices: {item_id: {index: {kind, value}}}, sourceBatches: {item_id: batch}, recoveredIds: [item_id]}`.
Store source text with choices, so a removed server batch remains recoverable after reload.
Persist recovery text, removal of consumed choices, and recovered IDs in one IndexedDB transaction with the main draft message.
Resolve the helper only on tx.oncomplete; abort/error rejects it. Request.onsuccess is not a commit boundary.
Do not overwrite existing text added while hydration or network fetch is pending.

Snapshot updates ignore lower revisions. Compare statuses and request IDs before recovering resolved choices.
Do not recover a batch while a local send referencing it remains uncertain.
Persist local dismissal request IDs with the question draft before sending the dismissal frame.
Matching resolution IDs consume local choices without inserting recovered text or an external-resolution notice.
Unmatched dismissal IDs recover local answers through the normal external-resolution transform.
On dismissal error, retain choices and release local pending-dismissal bookkeeping.
Initialize per-session snapshot independently of the sessions list; refresh active sessions on WebSocket reconnect and metadata reload.
Widget-disabled snapshots keep choices safely stored but hide controls and omit structured answers from sends.

- [ ] Add node tests for formatter parity, external-resolution transforms, source-text retention, no-options answers, and original-index identity.

```javascript
it('recovers Other text once without changing existing draft text', () => {
    const first = recoverResolvedAnswers(externalResolutionFixture())
    assert.ok(first.draft.message.includes('Answer: My own answer'))
    assert.ok(first.draft.message.endsWith('Keep the original draft.'))
    const second = recoverResolvedAnswers(replayRecoveryFixture(first))
    assert.equal(second.draft.message, first.draft.message)
})
```

Use injectable storage adapters or a small transaction fake in storage tests. Add no IndexedDB test dependency.
Cover hydrate-after-user-typing, disconnected updates, snapshot fetch racing WS, and atomic recovery interrupted by reload.
Test local dismissal broadcast-before-ack and ack-before-broadcast, and external dismissal in both orders.

- [ ] Run `cd frontend && node --test src/utils/asyncQuestions.test.js src/utils/asyncQuestionStorage.test.js`; confirm failures.
- [ ] Implement pure transforms, storage, and store/WS actions. Hook hydration before app mount through the existing draft startup sequence.
- [ ] Run the same command; require PASS.
- [ ] Commit with subject `feat(composer): persist async question drafts` and the required body/trailer.

## Task 7: Shared Question Controls and Composer Integration

**Files:** Create `frontend/src/components/message/QuestionFields.vue` and `AsyncQuestions.vue`; modify `RequestUserInputBody.vue`, `MessageInput.vue`, `SessionItemsList.vue`; create `frontend/src/utils/asyncQuestionComposer.test.js`.

**Interfaces:**

- `QuestionFields.vue` props: `questions`, `modelValue`, `disabled`, `allowClear`, `autoFocus`, `allowOther`.
- Emits `update:modelValue`; no submit event or send shortcut. It owns cards, Other inputs, and their keyboard behavior.
- `AsyncQuestions.vue` props: `sessionId`, `snapshot`; emits `dismiss(itemId)`; binds local choices through the store.
- Blocking host maps its native wire questions onto shared fields and preserves its allAnswered check, Submit/Dismiss buttons, focus rules, secret-input protection, and existing pending-request draft contract.

Async host uses `autoFocus=false`, `allowClear=true`, `allowOther=true` and stable batch/index IDs.
Questions without options use direct free text. No selection is the initial state.
Show `Questions pending` for collecting batches and a ready question count in the collapsed composer header.
Place ready controls immediately above the textarea inside the same composer root. Add no footer accordion panel.
When a blocking request owns the footer, hide async controls and preserve choices. Do not change the existing send lock.
Load snapshots when the main session view activates; exclude subagent and public-share contexts.
Constrain question area height with existing footer sizing and scrolling conventions; keep textarea and Send accessible.

- [ ] Extract pure composer classification `classifyAsyncQuestionSend({text, answers, attachments, settingsOnly, command})` into the utility module.
- [ ] Write node tests for answer-only content, whitespace-only Other, partial answers, commands with selections, and clearing selections.

```javascript
it('counts a selected answer as message content', () => {
    const result = classifyAsyncQuestionSend(answerOnlyFixture())
    assert.equal(result.settingsOnly, false)
    assert.equal(result.canSend, true)
})
```

- [ ] Run `cd frontend && node --test src/utils/asyncQuestionComposer.test.js`; confirm failures.
- [ ] Implement shared controls and hosts. Preserve existing card style and Enter/Space selection; never submit from an option-card keypress.
- [ ] Integrate composer content checks, pending indicator, clear/dismiss actions, and focus behavior.
- [ ] Run the targeted tests and `cd frontend && npm run build`; require PASS/build success.
- [ ] Commit with subject `feat(composer): integrate optional async question controls` and the required body/trailer.

## Task 8: Combined Outgoing Snapshots and Send-Failure Recovery

**Files:** Modify `frontend/src/components/message/MessageInput.vue`, `frontend/src/stores/data.js`, `frontend/src/utils/draftStorage.js`, `frontend/src/utils/inflightStorage.js`, `frontend/src/components/session/detail/items/FailedSendBanner.vue`; create `frontend/src/utils/asyncQuestionSendRecovery.test.js`.

**Interfaces:**

- Extend outgoing send registration with `rawText`, `asyncQuestions`, `sourceBatches`, and `questionDraft` alongside `text` (formatted final text).
- Extend failed-send entries and persisted snapshots with the same optional fields. Legacy snapshots without them keep current behavior.
- Composer payload sends rawText plus structured response; optimistic text uses `formatAsyncQuestionMessage` once.
- Accepted `send_ack` confirms the existing snapshot. Matching `resolutions[item_id].request_id` also proves that the same question-bearing send was accepted.
- Add `stageAsyncQuestionSend(requestId, snapshot, nextDraft, nextQuestionDraft) -> Promise<void>` to draftStorage.js.

For sends carrying questions, one IndexedDB readwrite transaction covers asyncQuestionDrafts, inflightSends, and draftMessages.
It stores the exact outgoing snapshot and removes only its selected choices/text from the active durable draft.
Await tx.oncomplete before clearing in-memory choices or issuing the socket frame. Abort keeps the draft and sends nothing.
Register the staged in-memory in-flight entry before socket dispatch; retain all raw/structured fields for recovery.
Persist outgoing status `staged`, then mark `dispatched` after socket dispatch; a crash between these states is uncertain, never auto-retried.
Socket dispatch failure restores staged drafts transactionally, then removes the staged snapshot.
Attachment medias stay in the existing attachment store until normal dispatch cleanup; retain the existing capped in-flight media copy.
Implement this question-send staging path explicitly. Existing registerInflightSend/saveInflightSend fire-and-forget is not sufficient for it.
Do not broaden the rewrite to ordinary sends without questions.

Stage outgoing question drafts in the existing in-flight snapshot before clearing visible choices.
Lock only the questions referenced by the pending local send, so a second local send cannot submit them twice.
Keep normal composer text entry available and allow unrelated new batches to remain editable.
Do not globally clear all async question drafts after one send.

On definite failure, preserve original textarea, attachments, and choices through the existing failed-message Edit/Retry flow.
Retry keeps rawText and structured answers when their batches are still ready, with a fresh request ID.
If batches become resolved externally, Edit recovers the formatted answer text into an ordinary draft and drops stale references.
Expose Edit for stale failures. Do not let Retry repeatedly send stale structured references.
If a user edits a failed formatted message as ordinary text, its structured answers must no longer be applied again.

In-flight acceptance resolution precedes external draft recovery, including when WS snapshot arrives before `send_ack`.
Preserve original draft content created after the send; recovery appends/prepends only the lost snapshot through existing merge conventions.
Use existing uncertain-delivery audit, attachment persistence size limits, and lost-ack handling.
Never auto-retry after reconnect or uncertainty.

- [ ] Write node tests for failure/restore, retry without double formatting, edit-to-ordinary-text, ack/snapshot event permutations, and old snapshot compatibility.

```javascript
it('does not recover its own accepted answers as an external resolution', () => {
    const result = reconcileSend(snapshotBeforeAckFixture())
    assert.equal(result.recoveredText, '')
    assert.equal(result.acceptedRequestId, 'send-1')
})
```

Define `reconcileSend` as a pure helper in `asyncQuestions.js`; exact return shape includes `recoveredText`, `acceptedRequestId`, `pendingQuestionIds`.
Test Other text created before external dismissal, no-send local draft, two-browser send, and a new question arriving during acceptance.
Test transaction abort before dispatch preserves text/choices. Test reload before staging, after staging, after dispatch, and after acknowledgement.
Test an acknowledgement arriving before the dispatched-status write cannot recreate a deleted accepted snapshot.

- [ ] Run `cd frontend && node --test src/utils/asyncQuestionSendRecovery.test.js`; confirm meaningful failures.
- [ ] Implement outgoing/in-flight/failed snapshot fields and reconciliation using existing recovery entry points.
- [ ] Run the targeted tests and `cd frontend && npm test`; require PASS.
- [ ] Commit with subject `fix(composer): preserve async answers through send recovery` and the required body/trailer.

## Task 9: Integration Verification and Product Validation

**Files:** Create `tests/test_async_question_integration.py`; use the new protocol fixtures and existing runtime harnesses.

**Interfaces:** No new production interfaces. Exercise the contracts from Tasks 1–8 together.

- [ ] Add integration tests from canonical question ingestion through readiness, API hydration, combined send, and resolved snapshot.
- [ ] Add full-recompute parity tests for steering-before-ready, late question-after-reply, command/internal messages, and external resolutions.
- [ ] Run `uv run pytest tests/test_codex_async_question_protocol.py tests/test_async_question_state.py tests/test_codex_async_question_lifecycle.py tests/test_codex_async_question_send.py tests/test_async_question_api.py tests/test_async_question_integration.py tests/test_codex_recompute_persistence.py tests/test_codex_subagent_hold.py tests/test_codex_send_fallback.py tests/test_cli_session_command.py -q`.
- [ ] Run `cd frontend && npm test` and `cd frontend && npm run build`. Require PASS and successful build.
- [ ] Validate UI in the existing dev instance after the user applies migrations and restarts backend through devctl.py.

Product checks:

| Scenario | Required result |
|---|---|
| Async question followed by tools and a long final message | Question controls become visible in composer after real control return |
| Main turn ends while children still run | Questions ready despite displayed assistant_turn |
| Automatic continuation | Questions remain collecting until control returns |
| Partial answers plus textarea and attachment | One send with selected answers and normal content |
| No answers plus ordinary text | Send succeeds and eligible questions disappear |
| Other, Clear answer, Dismiss | Optional controls work without sending or blocking |
| Second browser sends/dismisses | Local answers move into editable textarea text once |
| Failed send and lost ack | Existing recovery works without duplication or data loss |
| Collapsed composer, mobile keyboard, long question list | Count visible; textarea and Send reachable; no stolen focus |
| Existing blocking question | Existing style, focus, shortcut, draft persistence, and send lock unchanged |
| question_widget=False | Async controls hidden; transcript text remains |

Do not run live LLM diagnostics: this feature does not change model selection, SDK version, or hermetic LLM configuration.
Use recorded fixtures and controlled SDK mocks for automated checks. Never write source fixtures into real provider homes.

- [ ] Commit integration tests with subject `test(codex): verify async question composer lifecycle` and the required body/trailer.
- [ ] Report validation evidence and remind the user about required migrations/backend restart if still pending.

## Self-Review and Execution Handoff

Map spec sections 4–5 to Tasks 1–3, sections 6–7 to Tasks 4 and 6–8, section 8 to Tasks 2–3 and 5–6, and section 9 to Tasks 4–9.
Use Task 9 for section 11 acceptance coverage. Keep spec section 7.1 external-resolution recovery in both Tasks 6 and 8.

Before handing off, check function signatures, wire keys, source ordering, acceptance order, and every Review Focus test.
Do not start implementation during plan review. Internal adversarial reviewers review this plan after the author's self-review.

For execution, recommend subagent-driven development: lifecycle, sending, and recovery tasks share contracts that need independent checks.
The user selects the execution method after reviewing the corrected plan.

## Adversarial Review Record

Two internal reviewers inspect this implementation plan on 2026-10-05.
One focuses on backend lifecycle, durable chronology, sends, and compute integration.
The other focuses on frontend state, controls, persistence, and send recovery.

| Round | Findings | Corrections |
|---|---|---|
| 1, backend | Submission boundary and source-message provenance lack durable correlation. | Prepared journal precedes delivery; native client message IDs link start/steer/fallback to source records. |
| 1, backend | Watcher completion can precede the live continuation decision. | Pre-RPC live owner suppresses raw completion; durable pending/continuation/return decisions define release. |
| 1, frontend | Local dismissal can look external when broadcast precedes acknowledgement. | Persist dismissal request IDs and test both event orders. |
| 1, frontend | Fire-and-forget in-flight persistence can lose choices at send. | Stage question sends transactionally; await tx.oncomplete before dispatch and test abort/reload windows. |
| 2 | Both reviewers recheck corrections against current code and the spec. | Both report no remaining blockers in their reviewed areas. |

Author self-review checks spec coverage, shared names/types, source ordering, Review Focus tests, and task size.
Documentation checks pass. No product code, migrations, server changes, or runtime tests occur during plan preparation.
