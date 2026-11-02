# Confirmed Turn Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confirm logical assistant completion consistently across public state, timeline, unread notifications, and CLI/MCP waiting.

**Architecture:** A shared agent controller distinguishes actual root activity, existing assistant holds, and one-second completion candidates. A durable logical-turn journal preserves completion and native answer identities independently of disposable `ProcessRun` rows. Provider adapters supply facts; waiting resolves the exact indexed answer for the selected logical interval.

**Tech Stack:** Python 3.13+, asyncio, Django 6, SQLite, existing provider SDKs, Channels, Vue 3, pytest, Node tests.

**Spec:** [Confirmed turn completion specification](2026-11-02-confirmed-turn-completion-spec.md). Read it before this plan.

## Global Constraints

- Confirmation uses **one second**, initially `COMPLETION_DELAY_SECONDS = 1.0`.
- Background shells do not create assistant holds or extend confirmation.
- Preserve existing native subagent, Monitor, wakeup, and goal policies.
- Actual SDK activity determines Codex `steer` versus fresh send.
- Pending user action and terminal outcomes bypass the normal confirmation delay.
- Preserve strict cursor boundaries and existing response outcome names.
- Preserve the explicit `--wait-background` exception across later logical intervals.
- No historical backfill, provider SDK update, new public process state, or general background-work redesign.
- Use `NamedTuple` for immutable values and `orjson` for serialization.
- All DB mutations use the existing writer admission/locking paths. Timeline writes use `record_event` / `record_event_sync`.
- Ephemeral runs remain memory-only. Never persist their prompts, answers, or completion journal rows.
- Stay on the current branch. Preserve existing uncommitted files. Do not install packages or edit CHANGELOG.
- The user runs production migrations and backend restarts. Do not run either during implementation without an explicit request.

## Review Focus

These five conditions receive explicit tests in the owning tasks:

1. The final answer indexes after an intermediate final and after another interval starts: return the associated final, never the intermediate. Tasks 5 and 8.
2. A request or input reaches the agent before confirmation while publication is blocked: cancel the candidate and suppress stale live snapshots. Task 2.
3. A process stops or the backend restarts with an unconfirmed final: preserve interruption and forbid transcript fallback to success. Tasks 1 and 8.
4. Multiple accepted inputs share a stale transcript cursor: associate explicit waits with the accepted input, not the cursor alone. Task 6.
5. Hybrid Claude, manual commands, and Codex pending turn opening use paths outside ordinary SDK results: preserve routing and terminal semantics. Tasks 3 and 4.

## File and dependency map

| Unit | Files | Responsibility |
| --- | --- | --- |
| Durable journal | `src/twicc/core/models.py`; new `src/twicc/core/services/logical_turns.py`; new migration | Logical intervals, accepted-input associations, immutable terminal outcomes. |
| Pure controller | new `src/twicc/agent/completion.py` | Root activity, holds, pending delivery, deadlines, candidate invalidation. |
| Publication bridge | `src/twicc/agent/base_agent.py`, `base_manager.py`, `process_run_cleanup.py` | Persist confirmation before public completion and preserve teardown semantics. |
| Provider adapters | Claude agent, hybrid agent/signals/manager/watcher; Codex agent/manager | Translate existing runtime events and actual SDK lifecycle into controller facts. |
| Answer resolver | new `src/twicc/core/services/logical_turn_answers.py`; provider helpers | Map native identities to current indexed root messages without copying answer text. |
| Waiting | `src/twicc/cli/_wait_reply.py`; CLI entry points; MCP event runtime | Select a logical interval, then its exact concluding answer. |
| Input tokens | session creation/send services, provider managers, drop output, CLI wait arguments | Bind an explicit send to its accepted logical interval. |
| Timeline and docs | `core/timeline.py`, `asgi.py`, plugin skills, documentation tests | Remove metadata-only boundaries and describe the new waiting contract. |

Execute Tasks 1–8 in order. They implement one dependent feature, not independent sub-projects.
Commit after each passing task. Readiness for release requires all tasks.
Use lowercase Conventional Commit subjects and an explanatory body.
Add the actual executing model's required co-author trailer. Never hardcode a model name from this plan.

## Shared decisions and interfaces

Test snippets specify fixture scenarios and required assertions. Fixture-local names are illustrative.
Production interfaces below are exact; tests reuse existing provider and manager fixtures where possible.

### Journal identity and lifetime

Create `LogicalTurn` with these fields:

- `id`: UUID primary key; `session_id`: plain indexed string; `provider`: provider key.
- `run_id`: UUID generated per registered agent; `process_run_id`: nullable numeric value, **not a foreign key**.
- `ordinal`: positive integer within that run; unique constraint `(run_id, ordinal)`.
- `position`: positive per-session journal order; unique constraint `(session_id, position)`. Allocate under writer admission at interval creation.
  Use this order across agent replacement; do not order intervals by UUID or wall-clock timestamps.
- `revision`: monotonically increasing snapshot revision within the interval.
- `storage_incarnation`, `history_epoch`: nullable until the session's first trustworthy source observation; immutable after binding.
- `source_floor`: last indexed line observed before opening the interval; a lower bound, **not proof of source membership**.
- `first_ref`: nullable native identity of the interval's first root conversation record.
- `final_refs`: ordered JSON list of native root finals observed at physical turn ends; no answer text.
- `opened_at`, `closed_at`: UTC timestamps; `outcome`: null while open, then `normal`, `ended`, or `provider_error`.

Create `LogicalTurnInput` with UUID `id` as the opaque wait token, a `LogicalTurn` foreign key, `admission_cursor`, and `status`.
`admission_cursor` is the immutable indexed boundary captured before provider delivery, including deferred delivery.
Status is `pending`, `accepted`, `rejected`, or `uncertain`. Only accepted input returns an accepted-send token.
Index journal lookup by `(session_id, opened_at)` and input token by its primary key.
Do not cascade journal deletion from `ProcessRun`. That model is normally deleted on death.
Purge journal rows when their session is explicitly deleted, using the existing session-deletion writer.
Add nullable UUID `McpEventSubscription.last_logical_turn_id` for durable logical consumption.
It is a plain value, not a foreign key. Reset it with existing history-rebase cursor state.

Define these immutable types in new `agent/completion_types.py` during Task 1:

```python
class NativeMessageRef(NamedTuple):
    kind: str       # claude_uuid, claude_message, codex_item, unresolved
    native_id: str
    turn_id: str | None = None

class CompletionCandidate(NamedTuple):
    turn_id: str
    revision: int
    deadline: float

class CompletionSnapshot(NamedTuple):
    turn_id: str
    run_id: str
    ordinal: int
    revision: int
    first_ref: NativeMessageRef | None
    final_refs: tuple[NativeMessageRef, ...]
    outcome: str | None
```

Journal creation receives session, provider, source scope, process row, and timestamps separately.
Runtime snapshots do not copy transcript content or become public API shapes.
Terminal outcome and final identities become immutable at confirmation or exceptional termination.
Later indexing resolves references without changing those terminal facts.

### Source correlation

- Claude: prefer the root `AssistantMessage.uuid`; fall back to its API `message_id` for split records.
- Codex: use root thread/turn scope and the completed `AgentMessage` item's ID. Exclude `delivery="async"`.
- Capture the first root record as `first_ref` and each physical turn's concluding text reference as a final observation.
- Deduplicate repeated identities. Retain earlier finals when a later physical turn produces no answer.
- A missing or unindexed identity is unresolved. It is not permission to choose another final by text or timestamp.
- If text exists without a usable identity, retain an `unresolved` reference. Distinguish this from a known empty answer.
- Load content through existing content-storage readers and provider helpers. Do not depend on Tantivy indexing.
- Source scope includes `storage_incarnation` and `history_epoch`. Never reuse an answer across a changed source scope.

### Input association decision

Keep manager return types (`bool` for send, canonical session ID for create).
Add optional keywords `completion_token: str | None = None` and `completion_cursor: int | None = None` through manager and agent admission paths.
Tokenized calls pass both. Internal callers capture a missing cursor before their own provider delivery.
The public token identifies input acceptance; `completion_cursor` remains an internal admission parameter.
An explicit service call generates its token before admission and returns it only after accepted delivery is durably associated.
Internal/UI sends without a token generate one internally; settings-only calls do not create an input association.

Add an optional `wait_token` field to accepted create/send output.
Add optional `--wait-token` to singular `session wait-reply` for precise deferred waiting.
The token selects its accepted interval. An explicit `from_line` or `since` remains a strict answer boundary.
Without either explicit boundary, use that token’s stored `admission_cursor`, not the latest user message.
Cursor-only waits retain their current defaults. Invalid, rejected, or cross-session tokens fail without transcript fallback.
This additive option avoids guessing when several accepted inputs share the same stale `last_line`.
Plural waits accept an internal per-session token map for create/send batch callers; no new plural CLI syntax is required.

---

### Task 1: Persist logical interval outcomes independently of live processes

**Files:** Create `src/twicc/agent/completion_types.py`, `core/services/logical_turns.py`, `tests/test_logical_turns.py`, and the next migration after `0166_session_unavailable_index.py`. Modify `core/models.py`, `agent/process_run_cleanup.py`, and `providers/db_writer.py::_apply_delete_sessions_payload`. All abbreviated source paths use `src/twicc/`.

**Interfaces:**

```python
def open_logical_turn(*, turn_id: str, session_id: str, provider: str, run_id: str,
                  process_run_id: int | None, ordinal: int, source_floor: int,
                  storage_incarnation: str | None, history_epoch: int | None,
                  opened_at: datetime) -> None: ...
def store_turn_snapshot(snapshot: CompletionSnapshot, *, closed_at: datetime | None) -> bool: ...
def bind_turn_input(*, token: str, turn_id: str, admission_cursor: int, status: str) -> None: ...
def input_for_token(*, session_id: str, token: str) -> LogicalTurnInput | None: ...
def settle_abandoned_turns(*, at: datetime) -> int: ...
```

All functions are synchronous writer-side operations. `store_turn_snapshot` rejects stale revisions and changes to closed outcomes.
`settle_abandoned_turns` closes only open rows as `ended`; it never reclassifies a completed row.

- [ ] **Step 1: Add failing journal tests.** Include these assertions:

```python
def test_closed_journal_survives_process_deletion(journal_fixture):
    assert store_turn_snapshot(normal_snapshot, closed_at=now) is True
    assert store_turn_snapshot(older_open_snapshot, closed_at=None) is False
    process_run.delete()
    assert LogicalTurn.objects.get(pk=turn_id).outcome == "normal"

def test_input_token_keeps_session_and_cursor(journal_fixture):
    assert input_for_token(session_id=session.id, token=accepted_token).turn_id == turn_id
    assert input_for_token(session_id=session.id, token=accepted_token).admission_cursor == 0
    assert input_for_token(session_id="another-session", token=accepted_token) is None
```

Also test: no Session row at creation; duplicate token cannot move to another interval; boot cleanup closes only open rows.
Session deletion purges the journal. History/incarnation mismatch cannot rebind an existing closed record.
Test immutable admission cursors and increasing session positions across run replacement.
Add `last_logical_turn_id` to the migration; existing subscriptions default to null without historical backfill.

- [ ] **Step 2: Run** `uv run pytest -q tests/test_logical_turns.py`. Expect new-model/import failures before implementation.
- [ ] **Step 3: Implement the models, migration, and writer-side interfaces.** Use ordinary schema creation without a data migration.
  Register abandoned-turn settlement in the existing boot cleanup before old `ProcessRun` rows are deleted.
  Preserve timeline boot reconciliation. Add deletion alongside existing `TimelineEvent` deletion, including missing-session IDs.
  Bind initially missing source scope only after matching a native observation to that session; never bind a replacement incarnation silently.
- [ ] **Step 4: Run** `uv run pytest -q tests/test_logical_turns.py tests/test_timeline_process_hooks.py`. Expect all pass.
- [ ] **Step 5: Commit** `feat(agent): persist logical turn outcomes`. Stage only this task's files. Do not run production `migrate`.

### Task 2: Add the shared completion controller and safe publication bridge

**Files:** Create `agent/completion.py`, `tests/test_completion_controller.py`, `tests/test_completion_publication.py`. Modify `agent/base_agent.py` and `agent/base_manager.py`.

**Interfaces:**

```python
class CompletionController:
    def __init__(self, *, run_id: str, delay_seconds: float = 1.0) -> None: ...
    def activity(self, now: float, *, first_ref: NativeMessageRef | None = None) -> None: ...
    def provider_ended(self, now: float, *, final_ref: NativeMessageRef | None = None) -> None: ...
    def set_holds(self, holds: frozenset[str], now: float) -> None: ...
    def set_awaiting(self, awaiting: bool, now: float) -> None: ...
    def input_pending(self, token: str, now: float) -> None: ...
    def input_settled(self, token: str, status: str, now: float) -> None: ...
    def candidate(self) -> CompletionCandidate | None: ...
    def claim_due(self, candidate: CompletionCandidate, now: float) -> CompletionSnapshot | None: ...
    def terminate(self, outcome: str, now: float) -> CompletionSnapshot | None: ...
    def snapshot(self) -> CompletionSnapshot: ...
```

The controller generates a new UUID and increments its ordinal when actual work starts after a closed interval.
`BaseAgent` produces synchronous `_completion_activity(*, first_ref=None)`, `_completion_provider_ended(*, final_ref=None)`, `_completion_holds_changed(holds)`, and `_completion_terminate(outcome)` helpers.
They record facts before any await and schedule the owned persistence/confirmation work.
Before the first activity, `input_pending()` stages the token without creating a logical interval.
The first `activity()` opens the interval and binds staged input to that identity.
Await durable input acceptance outside manager locks; provider startup can return before actual input delivery.
Task 1 receives the controller's chosen interval ID; journal creation must not generate another identity.
The manager produces `_persist_completion(agent, snapshot) -> bool`, through the DB writer, before public completion publication.

- [ ] **Step 1: Add deterministic fake-clock tests.** No real sleeps:

```python
def test_full_second_is_required(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    assert ticket.deadline == 1.0
    assert controller.claim_due(ticket, 0.999) is None
    assert controller.claim_due(ticket, 1.0).outcome == "normal"

def test_requesting_invalidates_old_candidate(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    controller.activity(0.02)
    assert controller.claim_due(ticket, 1.0) is None
    controller.provider_ended(4.0)
    assert controller.claim_due(controller.candidate(), 5.0).outcome == "normal"
    assert controller.claim_due(ticket, 6.0) is None
```

The controller fixture opens a logical interval with `activity(0.0)` before these sequences.
Cover: hold release, pending input, rejection/uncertainty, pending action, no-answer completion, duplicate end, metadata noise, and new interval after closure.
Generate 1,000 deterministic sequences of 40 events; assert at most one terminal snapshot per interval.

- [ ] **Step 2: Add publication race tests.** Block writer admission, record activity, then release admission.
  A candidate invalidated before its claim must produce no USER_TURN write, end event, or finished notification.
  After a valid claim, later activity opens a new interval; the old answer remains valid for its waiters.
  No stale callback may overwrite newer live state or broadcast an old USER_TURN after that newer state.
  A rejected persistence operation must not publish successful completion.
  Add `test_completion_settings_restart_drains_dead_callback`: deferred startup settings cause teardown after confirmation.
  Assert that completion and nested DEAD persistence drain without a deadlock.
  Add `test_candidate_only_interrupt_ends_without_sdk_call` for both providers.
  Assert outcome `ended`, immediate USER_TURN, zero SDK interrupt calls, and no later normal confirmation.
- [ ] **Step 3: Run** `uv run pytest -q tests/test_completion_controller.py tests/test_completion_publication.py`. Expect failures before controller implementation.
- [ ] **Step 4: Implement the controller and bridge.** Use `loop.time()` and one cancellable timer task per agent.
  `claim_due` is the synchronous decision point after all already-recorded ingress facts. Check interval, revision, eligibility, and deadline there.
  Serialize only persistence and live publication, not the entire provider `_on_state_change` callback.
  Run lifecycle hooks, including Claude `_apply_pending_settings`, outside this ordered section.
  A hook can await teardown and its nested DEAD callback; that callback must enter the ordered section independently.
  Preserve current-agent and revision guards before each state-dependent lifecycle hook.
  Recheck candidate validity after waiting for writer admission and before claiming; do not hold an agent publication lock across an SDK call.
  Persist the claimed closed journal snapshot and corresponding `ProcessRun` transition under one writer transaction.
  Factor an already-admitted persistence implementation from `_persist_process_run_transition`; do not recursively acquire the writer lock.
  Record timeline through its existing hook. Publish only after commit; suppress a live snapshot superseded during that await.
  Before provider-specific soft-interrupt early returns or SDK calls, handle a candidate with no actual root work.
  Terminate its logical interval as `ended`, cancel the timer, and publish USER_TURN immediately.
  No SDK interrupt or future result is required for this path. Preserve normal SDK interrupt when actual work exists.
  Clear candidate tasks on terminal failure, stop, death, and replacement.
  Pending request registration/clear calls `set_awaiting`; delivery settlement cannot manufacture a provider result.
  Ephemeral agents use the controller without journal writes and retain their existing one-shot result behavior.
- [ ] **Step 5: Run** `uv run pytest -q tests/test_completion_controller.py tests/test_completion_publication.py tests/test_ephemeral_lifecycle.py`. Expect all pass.
- [ ] **Step 6: Commit** `feat(agent): confirm stable logical turn completion`.

### Task 3: Adapt Claude SDK and hybrid lifecycle signals

**Files:** Modify `providers/claude_code/agent/agent.py`, `providers/claude_code/agent/manager.py`,
`providers/claude_code/agent/hybrid/agent.py`, `providers/claude_code/agent/hybrid/signals.py`, and `providers/claude_code/sessions_watcher.py`. Extend `tests/test_claude_turn_activity.py`, `tests/test_claude_subagent_lifecycle.py`. Create `tests/test_claude_completion.py` and `tests/test_hybrid_completion.py`.

**Interfaces:** Consume Task 2 fact helpers. Carry the shared `completion_token` and `completion_cursor` admission keywords without changing return types.
Add `ClaudeCodeAgent._completion_assistant_holds() -> frozenset[str]` from native agents, Monitors, and pending wakeup only.
Hybrid signals retain event order and distinguish normal turn end from interruption. Do not collapse opposite events into unordered booleans.

- [ ] **Step 1: Add failing provider tests.** Feed real-shaped SDK messages and collect public transitions:

```python
def test_requesting_bridges_late_first_tokens(claude_completion_fixture):
    assert transitions_after(final_at_0, requesting_at_0_02, stream_at_3, result_at_4) == [
        "assistant_turn", "user_turn",
    ]
    assert confirmed_at == 5.0
    assert intermediate_final_released_waiter is False
```

Test `requesting` while the public state is already assistant, not only after USER_TURN.
Keep commands_changed, settings acks, hooks, and child-scoped output non-root.
Test a child completed before the parent's ResultMessage, last-child release without another result, and Monitors/wakeups.
Hybrid cases: ordered end/start in one ingest batch; final plus delayed root progress; local command ack during a real turn; interruption bypass.
Add `test_hybrid_start_before_first_paste_stages_input`: startup returns before its first paste.
Assert no premature interval or accepted token; first activity binds the staged token to exactly one interval.
The service then observes persisted acceptance without holding the manager lock.

- [ ] **Step 2: Run** `uv run pytest -q tests/test_claude_completion.py tests/test_hybrid_completion.py tests/test_claude_turn_activity.py`. Expect the requesting and timing tests to fail initially.
- [ ] **Step 3: Route root activity before the message loop's existing stream/output branches.** Capture final references even in branches that already consume AssistantMessage.
  Recognize only `status=requesting` as the added early system status. Keep existing compaction handling.
  On normal ResultMessage, mark actual root activity inactive and update holds; do not directly publish USER_TURN.
  On native task/Monitor/wakeup changes, recompute holds even if public state does not change.
  Releasing the last hold considers completion without requiring a new parent result.
  Keep soft interrupt and terminal errors immediate, with an `ended` or provider-error journal outcome.
- [ ] **Step 4: Adapt hybrid sources.** Translate already indexed root records and ordered lifecycle markers into the same controller.
  Use the hybrid first/last native transcript identities for correlation; requesting is unavailable on this transport.
  The watcher never waits on agent locks while holding ingest ownership; relay ordered observations after commit.
  Preserve pending hook ownership, command acknowledgments, title handling, and first USER_TURN startup signaling.
- [ ] **Step 5: Run** `uv run pytest -q tests/test_claude_completion.py tests/test_hybrid_completion.py tests/test_claude_turn_activity.py tests/test_claude_subagent_lifecycle.py tests/test_claude_monitor_liveness.py tests/test_hybrid_pending_tool_input.py`. Expect all pass.
- [ ] **Step 6: Commit** `fix(claude): preserve work across automatic continuations`.

### Task 4: Adapt Codex completion without steering an ended turn

**Files:** Modify `providers/codex/agent/agent.py` and `providers/codex/agent/manager.py`. Extend `tests/test_codex_subagent_hold.py`. Create `tests/test_codex_completion.py`.

**Interfaces:** Consume Task 2 helpers and Task 1 input association. Add `CodexAgent._completion_assistant_holds() -> frozenset[str]` from existing native/goal continuation facts.
Add `CodexAgent._has_actual_turn_work() -> bool` for an opening RPC, active stream, scheduled replacement, or owned goal turn.
It must distinguish an opening turn from completion-only public activity; `_current_turn is None` alone is insufficient.

- [ ] **Step 1: Add failing event/routing tests.** Use existing SDK fakes:

```python
async def test_send_during_candidate_opens_fresh_sdk_turn(codex_completion_fixture):
    assert await send_during_completion_interval() is True
    assert fresh_turn_calls == 1
    assert steer_calls == 0
    assert public_turn_events == ["turn.started"]
```

Also test active-turn steer, send during opening RPC, rejected-steer replacement, last-child stop without another final, and child resume before deadline.
Cover goal continuation, plan implementation question, manual compact completion, failed/interrupted turn, and ephemeral final delivery.
Add `test_cold_plan_command_settles_without_logical_interval`, `test_command_failure_settles_without_interval`,
and `test_goal_clear_without_continuation_settles`. Assert USER_TURN, no new logical interval, and no synthetic turn boundaries.
A command while actual root/goal work remains must preserve assistant ownership.
An `AgentMessage` with `phase="final_answer", delivery="async"` must not become the interval's concluding answer.

- [ ] **Step 2: Run** `uv run pytest -q tests/test_codex_completion.py tests/test_codex_subagent_hold.py`. Expect missing delay and routing assertions to fail.
- [ ] **Step 3: Integrate root start and terminal facts.** Capture completed root AgentMessage references before stream branches return.
  Preserve `_current_turn`, `_current_turn_ready`, `_turn_stream_done`, `_send_delivery_done`, and replacement-task guards.
  Set actual-root inactivity only after the owned stream is drained and replacement/continuation decisions are known.
  Replace normal work-completion USER_TURN settles with controller eligibility at `_run_turn`, `_apply_subagents_stopped`, and goal completion.
  Keep `_settle_after_command` as a control-state path: without an open logical interval, settle immediately.
  This also clears STARTING for a cold bare `/plan` or failed command. Do not create a logical interval for metadata commands.
  With an open interval, update controller eligibility; preserve active root/goal ownership.
  Keep explicit interruption, failure, and plan/question outcomes immediate as required.
- [ ] **Step 4: Route sends using actual work.** A completion candidate with no actual turn uses fresh send, even though public state is assistant.
  Preserve existing opening-turn handshake, goal steer, and explicit-rejection fallback. Never replay uncertain delivery.
  Hold helpers must not acquire `_subagent_set_lock` recursively through the publication bridge.
- [ ] **Step 5: Run** `uv run pytest -q tests/test_codex_completion.py tests/test_codex_subagent_hold.py tests/test_codex_subagent_wait_label.py tests/test_codex_async_question_lifecycle.py tests/test_ephemeral_lifecycle.py`. Expect all pass.
- [ ] **Step 6: Commit** `fix(codex): confirm completion without stale turn steering`.

### Task 5: Resolve the exact indexed answer and enforce logical waiting

**Files:** Create `core/services/logical_turn_answers.py`, `tests/test_logical_turn_answers.py`. Modify provider `helpers.py` files and `cli/_wait_reply.py`. Extend `tests/test_wait_reply.py`, `tests/test_wait_background.py`, and `tests/test_wait_default_cursor.py`.

**Interfaces:**

```python
class LogicalAnswer(NamedTuple):
    status: str  # pending, replied, ended, provider_error, invalidated
    message: IndexableMessage | None

def resolve_logical_answer(turn: LogicalTurn, *, cursor: int) -> LogicalAnswer: ...
class WaitProgress(NamedTuple):
    consumed_up_to: int
    last_logical_turn_id: str | None

def select_logical_turn(*, session_id: str, cursor: int, wait_token: str | None,
                    after_turn_id: str | None = None) -> LogicalTurn | None: ...
```

Add `BaseProviderHelpers.completion_record_refs(parsed: dict) -> tuple[NativeMessageRef, ...]` with provider overrides.
Extend `_SessionWait` and `wait_for_reply[_or_degrade]` with optional `wait_token: str | None = None`.
`_SessionWait` additionally accepts `after_turn_id: str | None = None` and exposes `progress() -> WaitProgress`.
`scanned_up_to` is a read watermark. It is never proof that all earlier logical intervals are consumed.
Extend `wait_for_replies` with optional `wait_tokens: dict[str, str] | None = None`.

- [ ] **Step 1: Write failing answer-resolution tests.** Use compressed current-content fixtures for both providers:

```python
def test_last_native_answer_survives_later_interval(answer_fixture):
    assert resolve(turn_A, cursor=0).status == "pending"  # final A2 not indexed
    index_answer(A2)
    assert resolve(turn_A, cursor=0).message.text == "A2"
    index_answer(B1)
    assert resolve(turn_A, cursor=0).message.text == "A2"
    assert resolve(turn_A, cursor=A2.line_num).status == "ended"
```

Cover Claude split blocks, repeated text with different IDs, child scope, async Codex delivery, source invalidation, and missing native identity.
Test mixed historical/unmanaged and managed intervals, including an unindexed old final at admission.

- [ ] **Step 2: Replace tests pinning the old first-final behavior.** An open logical interval cannot return an intermediate final.
  Test later parent final, no-answer normal completion (`ended`), last-child stop reusing the eligible earlier final, and terminal stop preserving non-success.
  A current pending request wins over the unconfirmed intermediate final; a later interval's pending request cannot invalidate an already completed older answer.
  Keep legacy transcript fallback tests for sessions/ranges without logical completion information.
- [ ] **Step 3: Run** `uv run pytest -q tests/test_logical_turn_answers.py tests/test_wait_reply.py tests/test_wait_background.py tests/test_wait_default_cursor.py`. Expect the new journal-aware assertions to fail.
- [ ] **Step 4: Implement native identity resolution.** Use `load_current` and provider parsing; select the last eligible final from the closed interval's ordered references.
  Resolve source scope before trusting any line; reject history/incarnation mismatch as `invalidated`, mapped to existing `wait_failed` behavior.
  An expected but unindexed native answer stays `pending` until timeout. A known absence of answer returns `ended`.
  A stopped process does not enable legacy fallback for a journaled interval.
  Cache observed source references per wait and scan newly indexed lines incrementally. Do not rescan whole transcripts every poll.
  Resolve `first_ref` for ordering legacy candidates before managed coverage. While unresolved, do not classify records newer than `source_floor` as legacy successes.
  A managed empty interval produces no owned final; after its existing watcher-flush barrier, earlier unowned historical candidates can still use fallback.
- [ ] **Step 5: Implement waiting selection.** A token selects its accepted input's interval; otherwise choose the earliest eligible interval after the cursor.
  Preserve older completed results while newer work runs. Keep the target interval once selected.
  Check journal outcome and associated pending action before legacy transcript success; never infer managed completion from ProcessRun disappearance.
  On a terminal result, advance logical progress only to the selected interval.
  `consumed_up_to` cannot exceed its resolved source boundary or skip a later interval’s indexed answer.
  For an empty interval, retain the safe prior line cursor and advance `last_logical_turn_id` instead.
  Compare `after_turn_id` using immutable per-session `position`. Keep consumed rows in managed-ownership checks.
  A final from a consumed managed interval never becomes an unowned legacy fallback.
  Preserve API errors, backend disappearance, missing-session grace, timeout output, and no-reply-text behavior.
  For `--wait-background`, retain ignored-final state and permit later intervals only after a background-based ignore.
  A later interval must confirm before success. Do not revive the ignored final when work ends without another answer.
- [ ] **Step 6: Run the command from Step 3.** Expect all pass, including the retained explicit background contract.
- [ ] **Step 7: Commit** `fix(cli): wait for the associated logical completion`.

### Task 6: Carry wait tokens through accepted inputs and every waiting entry point

**Files:** Modify `core/services/session_creation.py`, `core/services/send_message.py`, provider managers, `cli/_drop_request/output.py`, `cli/create_session/command.py`, `cli/send_message/command.py`, `cli/send_messages.py`, `cli/session.py`, `cli/sessions_wait_reply.py`, `mcp/events/runtime.py`, and `mcp/events/methods.py`. Extend their existing CLI/MCP tests. Create `tests/test_completion_wait_tokens.py`.

**Interfaces:** Accepted service results produce `status_extra["wait_token"]`; add `status_extra` to `SessionCreationResult` with an empty default.
`_OPTIONAL_FIELDS` passes `wait_token` through without inventing it for legacy or rejected results.
`session.wait_reply(..., wait_token: str | None = None)` forwards the token and cursor to Task 5.
CLI-generated MCP schemas expose the additive singular option through existing RPC generation.

- [ ] **Step 1: Write failing association tests.** Deliver two inputs with the same indexed cursor and finish different intervals:

```python
def test_equal_cursors_keep_distinct_accepted_inputs(two_sends_fixture):
    assert send_A["last_line"] == send_B["last_line"]
    assert send_A["wait_token"] != send_B["wait_token"]
    assert wait(wait_token=send_A["wait_token"], from_line=0).text == "A"
    assert wait(wait_token=send_B["wait_token"], from_line=0).text == "B"
```

Also test two inputs during the same completion interval sharing its eventual answer; canonical Codex session binding; deferred Claude restart delivery.
Rejected/uncertain sends must not publish an accepted token or bind to another send's answer.
Settings-only updates, shell notices, and commands must not manufacture accepted human input.
Add these regression tests, with singular and batch variants where applicable:

```python
def test_answer_indexed_before_send_returns_keeps_admission_cursor(fast_send):
    sent = fast_send(answer_before_return=True)
    assert sent["last_line"] == 0
    assert wait(wait_token=sent["wait_token"]).text == "A"

def test_token_without_explicit_cursor_survives_later_input(two_completed_inputs):
    token_A, token_B = two_completed_inputs
    assert wait(wait_token=token_A).text == "A"
    assert wait(wait_token=token_B).text == "B"

def test_monitor_consumes_empty_A_then_answer_B_after_reload(monitor_fixture):
    monitor_fixture.index_closed_empty_A_then_answer_B()
    assert monitor_fixture.tick().outcome == "ended"
    monitor_fixture.reload_from_persisted_subscription()
    assert monitor_fixture.tick().text == "B"
    assert monitor_fixture.next_delivery() is None
```

- [ ] **Step 2: Add entry-point tests.** Cover create `--wait-reply`, send `--wait-reply`, batch send, singular cursor wait, token wait, and plural waiting.
  Test MCP worker reconstruction after a poll and history rebase. It must use Task 5 semantics without an independent final scanner.
  Run token-default tests through singular CLI and generated MCP calls after a later user input.
  Repeat the empty-A/answer-B case with interrupted A, no source lines for A, and both answers already indexed.
  Assert no skipped B, duplicate A, or logical-progress reuse across a rebase.
- [ ] **Step 3: Run** `uv run pytest -q tests/test_completion_wait_tokens.py tests/test_send_messages_wait.py tests/test_cli_sessions_wait_reply.py tests/test_mcp_events_runtime.py tests/test_mcp_events_background.py`. Expect missing token/selection assertions to fail.
- [ ] **Step 4: Integrate admission.** Generate tokens in create/send services and propagate them to actual manager delivery, including parked restart sends.
  Within the existing serialized send/admission lane, capture the indexed cursor before provider delivery begins.
  Pass this immutable cursor with the token through agents, parked sends, and durable acceptance.
  Return this admission boundary as `last_line`; never replace it with a post-delivery `Session.last_line` read.
  New sessions use zero before their first provider delivery; existing sessions use their current indexed boundary.
  Attach pending input before the SDK can emit a result. Settle acceptance through existing delivery success/failure decisions.
  Return the token only after its accepted binding is committed; do not read a mutable "last admitted turn" after manager return.
  Do not change existing Codex async-question request identity or replay logic.
  Feed tokens automatically to in-command waits; permit explicit singular deferred waits through `--wait-token`.
  Keep `last_line` and existing cursor-only calls valid. A token cannot bypass cursor bounds or target another session.
  Without an explicit cursor, a token wait loads `admission_cursor` through `input_for_token` before legacy cursor defaults.
  MCP monitors use Task 5 `progress()`, not `scanned_up_to`, for terminal consumption.
  Carry `last_logical_turn_id` through `SubscriptionSnapshot`, monitor reconstruction, `_new_wait`, and `CursorWrite`.
  Commit line and logical progress together through the existing generation-bound emission writer.
  Preserve delivery-before-cursor ordering. Reset logical progress in `RebaseWrite` and the in-memory rebase path.
  Remove the first-poll cursor clamp when it would cross an unconsumed managed interval. Preserve historical-only behavior.
  MCP monitors keep existing epoch guards; their optional accepted-input workflow can additionally supply a token.
- [ ] **Step 5: Run the command from Step 3**, plus `uv run pytest -q tests/test_mcp_events_turns.py tests/test_mcp_events_rebase.py tests/test_cli_session_wait.py`. Expect all pass.
- [ ] **Step 6: Commit** `feat(cli): associate reply waits with accepted inputs`.

### Task 7: Remove metadata-only timeline boundaries and verify public behavior

**Files:** Modify `core/timeline.py`, `asgi.py`, and `tests/test_timeline_process_hooks.py`. Extend `tests/test_completion_publication.py` and existing frontend tests `frontend/src/utils/sessions.test.js`, `processStateNotifications.test.js` only where an end-to-end assertion needs them.

**Interfaces:** Keep `process_transition_events(...) -> list[EventSpec]`. USER_TURN to USER_TURN background changes produce no TURN_ENDED.
Keep all public state values and terminal-indicator fields unchanged.

- [ ] **Step 1: Replace** `test_background_work_changes_while_idle_re_emit_turn_ended` with a no-additional-end assertion.
  Test a shell starting, finishing, and updating after one real completion. Assert exactly one TURN_ENDED and current background metadata.
  Preserve awaiting-input intervals, process death, actor/origin data, and unchanged historical events.
- [ ] **Step 2: Add public-sequence tests.** Before confirmation, no USER_TURN frame or completion notification exists.
  After confirmation, one completion frame and one logical end exist. A late transcript line cannot cause an intermediate reply.
  Hidden/muted sessions and resync continue following existing notification preferences.
- [ ] **Step 3: Run** `uv run pytest -q tests/test_timeline_process_hooks.py tests/test_completion_publication.py`. Expect metadata-boundary assertions to fail before removal.
- [ ] **Step 4: Remove only the metadata-only TURN_ENDED branch.** Preserve background persistence and broadcasts.
  Remove the obsolete one-second broadcast-delay comment and commented sleep in `asgi.broadcast_process_state`.
  Do not add a second frontend or WebSocket debounce. Existing unread and notification consumers use confirmed public state.
- [ ] **Step 5: Run** the Step 3 command and `npm test` from `frontend/`. Expect all pass.
- [ ] **Step 6: Commit** `fix(timeline): record only logical turn boundaries`.

### Task 8: Verify integrated acceptance cases and update published contracts

**Files:** Create `tests/test_completion_integration.py`. Modify CLI help/docstrings, plugin waiting documentation, plugin manifest, and relevant documentation tests.
Read `src/twicc/agent/plugin/README.md` before editing skills.
Cover `twicc-create-session`, `twicc-send-message`, `twicc-send-messages`, `twicc-session/wait-reply.md`, and `twicc-sessions/wait-reply.md` under `agent/plugin/twicc/skills/`.

**Interfaces:** Reuse the real controller, managers, journal service, resolver, and `_SessionWait` with fake provider transports.
Tests use `twicc.settings_test`; they do not access provider homes or the production database.

- [ ] **Step 1: Add integrated tests for the spec's acceptance table.** Use both providers where applicable.
  Use sanitized event-only fixtures for the three Claude sequences: result → requesting after 20–25 ms → stream after 0.67–3.19 seconds.
  Replace transcript text with synthetic `WAITING` and `DONE` values. Do not copy private prompts or logs into tests.
  Assert one interval, no early unread/notification, and the correct concluding answer.
  Include a last-child stop without parent resume, stale timer after replacement, and stop before delayed indexing.
  Test both in-backend registry waiting and shell/DB waiting after ProcessRun deletion.
  Test a real writer queue stall and watcher publication ordering without sleeps or live model calls.
- [ ] **Step 2: Run** `uv run pytest -q tests/test_completion_integration.py`. Expect any remaining cross-task integration gaps to fail; fix them in their owning units.
- [ ] **Step 3: Update user-facing contracts.** State confirmed logical completion, the initial one-second interval, pending-action priority, and last relevant answer.
  Document `wait_token` and singular `--wait-token`, the stored default admission cursor, explicit strict cursor bounds,
  and the preserved explicit background exception. Explain that accepted send `last_line` is captured before delivery.
  Remove the claim that the first final always wins. Keep timeout, ignored-final, and resumption instructions accurate.
  Bump the plugin minor version from its current value because this plan adds a flag. Do not hardcode a stale installed plugin version.
- [ ] **Step 4: Run targeted documentation checks:**

```bash
uv run pytest -q tests/test_session_wait_documentation.py tests/test_sessions_wait_reply_documentation.py tests/test_mcp_events_catalog.py
```

Expected: all pass, with singular help/schema exposing `--wait-token` and existing plural syntax unchanged.

- [ ] **Step 5: Run the final focused suite:**

```bash
uv run pytest -q tests/test_logical_turns.py tests/test_completion_controller.py tests/test_completion_publication.py tests/test_claude_completion.py tests/test_hybrid_completion.py tests/test_codex_completion.py tests/test_logical_turn_answers.py tests/test_completion_wait_tokens.py tests/test_completion_integration.py tests/test_wait_reply.py tests/test_wait_background.py tests/test_codex_subagent_hold.py tests/test_timeline_process_hooks.py tests/test_mcp_events_turns.py tests/test_mcp_events_rebase.py tests/test_ephemeral_lifecycle.py
```

Expected: zero failures. Run `npm test` from `frontend/` if Task 7 changes frontend assertions or production files.
Do not rerun live provider diagnostics for an unchanged SDK. Live sessions are optional and require no new production settings.

- [ ] **Step 6: Add diagnostic logging** for candidate creation/cancellation, interval ID, revision, confirmation duration, and later root restart cause.
  Log no transcript text. Separate input, shell, hold, and unannounced root activity.
  Verify cancellation and duplicate suppression with `uv run pytest -q tests/test_completion_integration.py` after adding logging.
- [ ] **Step 7: Commit** `test(agent): verify confirmed completion contracts`, including updated help and plugin version.
- [ ] **Step 8: Prepare the handoff.** Report implementation changes, focused checks, remaining limits, and the user-run migration/restart requirement.
  Do not run `migrate` or restart the backend. Do not claim a production fix from fake-transport tests alone.

## Self-review and spec coverage

| Specification area | Owning tasks |
| --- | --- |
| One-second interval and early activity | 2, 3, 4 |
| Existing holds and background shell exception | 2, 3, 4, 7 |
| Pending input and actual Codex routing | 2, 4, 6 |
| Pending user action and terminal outcomes | 1–5 |
| Answer association, strict cursors, delayed indexing | 1, 5, 6 |
| Explicit background waiting across later intervals | 5, 8 |
| Historical fallback, stop, restart, source invalidation | 1, 5, 8 |
| Timeline, unread, notifications | 2, 7, 8 |
| CLI, MCP, schemas, skills | 5, 6, 8 |
| Timing measurement and known limits | 8 |

Implementation order preserves the shared interfaces above.
This plan adds no new session agent setting and makes no provider SDK upgrade.
The new optional token is a waiting selector, not a process state or background hold.
