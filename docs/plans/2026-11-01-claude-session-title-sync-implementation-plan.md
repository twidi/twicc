# Claude Session Title Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep managed Claude titles synchronized without stale reversions or rejection of voluntary renames.

**Architecture:** Publish through the active CLI, using the private SDK control request or hybrid `/rename`.
A process-local coordinator orders publications and fences transcript observations using existing storage identities and source positions.
All title writers establish that protection under the existing database write lock, before provider publication.

**Tech Stack:** Python 3.13+, Django, asyncio, NamedTuple, orjson, Claude Agent SDK 0.2.175, bundled Claude Code 2.1.314, tmux.

**Spec:** [Claude session title synchronization](2026-11-01-claude-session-title-sync-spec.md), revision 3.

**Status:** Implementation completed. Tasks 1–7 and final review corrections are complete. Final review approved; no remaining findings.
**Revision:** 4.

## Accepted limitation: hybrid delivery attribution

**The user accepts the strict hybrid limitation. Hybrid mode is currently disabled and may never be enabled.**
Current CLI records correlate command results with command messages. They do not correlate a command with TwiCC's paste token.
The paste lock excludes other TwiCC pastes. It does not exclude raw terminal input or external tmux clients.
A matching `/rename` after the captured boundary can therefore come from the user.
Message ancestry, an input-generation counter, screen text, and a quiet interval cannot prove attribution.

Keep the spec's strict single-outstanding-publication rule, as accepted by the user.
After any potentially delivered hybrid paste, keep an outstanding-delivery hazard until certain completion or confirmed process death.
Observed convergence can update observation ordering. It does not prove that TwiCC's own paste cannot execute later.
Do not send a successor while that hazard remains. Current interfaces may leave it unresolved until process death.
Successful explicit command outcomes remain admissible throughout that interval, whether or not their sender is attributable.
Apply the CLI's effective title from that outcome, retire older pending decisions, and retain the independent own-paste hazard.
A later actual outcome from the outstanding paste follows the same CLI ordering rule. Do not manufacture an additional corrective command.

**The user resolves this product choice after round 3 of the plan review.**
The accepted delay can last until process death. Do not weaken attribution to semantic convergence.
This decision neither activates hybrid mode nor authorizes product implementation.
A live hybrid diagnostic verifies the strict contract, including uncertain delivery and blocked successor publication.
It must not require an impossible attribution proof to pass or report semantic convergence as own-delivery confirmation.

## Global Constraints

- "The user accepts a dependency on the private Python SDK API."
- "Every Claude Agent SDK update must verify this dependency against the real bundled CLI."
- "Do not append a title directly to the transcript while a managed SDK process is active."
- "Do not append directly to the transcript as a fallback while the hybrid process remains active."
- "Do not hold the shared database write lock across provider I/O or a terminal wait."
- "Do not reject a title because it appears in history, whether immediately preceding or older."
- "Do not change Codex behavior through a shared compute refactor."
- "No new persistent revision field is required by this specification."
- Preserve automatic-title cadence, eligibility, origin, timeline attribution, and stale-session restrictions.
- Use English in files. Stay on the current branch. Preserve unrelated user changes.
- No dependency installation, server restart, migration, CHANGELOG entry, or historical title repair.
- Commands run from `/home/twidi/dev/twicc-poc`; use the actual checkout path if the user selects another checkout.
- Do not add `TWICC_DATA_DIR` to pytest commands. Use `twicc.settings_test` through the existing pytest configuration.
- Commit each task with a Conventional Commit subject, an explanatory body, and the executing model's correct co-author trailer.
- Do not copy a model trailer from this plan. Determine the executing model when committing.

## Review Focus

1. A matching historical title is not publication confirmation: Task 2 tests pre-dispatch duplicates and Task 3 tests command-specific evidence.
2. A manual `/rename` can share a batch with an own echo: Tasks 2 and 4 retain ordered observations and replay the later command.
3. A worker result can outlive a title decision or a storage incarnation: Tasks 2 and 4 test both identities independently.
4. A timeout can follow a successful provider write: Tasks 3 and 6 distinguish uncertain delivery from a safe retry.
5. A pending create-session title can reappear after a newer rename: Tasks 5 and 6 invalidate old retries without changing Codex.

---

## File and responsibility map

| File | Responsibility |
|---|---|
| Create `src/twicc/providers/claude_code/title_control.py` | One private SDK compatibility boundary and canonical title validation. |
| Create `src/twicc/providers/claude_code/title_sync.py` | Thread-safe synchronous decision/observation state; no asyncio, ORM, or filesystem work. |
| Create `src/twicc/providers/claude_code/title_publication.py` | Async per-session publication worker, route selection, retries, and lifecycle completion. |
| Create `src/twicc/core/services/session_titles.py` | Fresh locked title acceptance, DB persistence, and existing timeline recording. |
| Modify `src/twicc/providers/claude_code/helpers.py` | Canonical validation and delegation to the new publication route. |
| Modify `src/twicc/providers/claude_code/titles.py` | Keep inactive-file mutation; remove the lifetime protection/corrective-append mechanism after integration. |
| Modify `src/twicc/providers/compute_base.py` | Carry ordered title observations, preserve source identity, and dispatch shared provider acceptance. |
| Modify `src/twicc/providers/claude_code/compute.py` | Claude observation extraction and application through the coordinator. |
| Modify `src/twicc/providers/claude_code/agent/{agent.py,manager.py}` | SDK readiness, process identity, startup, and stop notifications. |
| Modify `src/twicc/providers/claude_code/agent/hybrid/{agent.py,signals.py,launch.py}` | Awaitable rename result, ordered local-command evidence, and full-length launch titles. |
| Modify `src/twicc/providers/claude_code/sessions_watcher.py` | Deliver title command evidence without corrupting existing hybrid turn signals. |
| Modify `src/twicc/{views.py,agent/base_manager.py}` | Route REST and deferred title writes through locked acceptance. |
| Modify `src/twicc/core/services/{session_update.py,title_automation.py}` | Route CLI/MCP and automatic writes; remove the unsafe corrective push. |
| Create `scripts/diagnose_claude_title_sync.py` | Isolated real-CLI upgrade diagnostic. |
| Create `docs/claude-sdk-updates.md`; modify `AGENTS.md` and `CLAUDE.md` | Maintain the private API compatibility gate alongside existing hermetic checks. |

`CLAUDE.md` may share storage with `AGENTS.md`. Check their relationship before editing either file.
Update the shared instruction source once. Do not replace a symlink or duplicate divergent text.

## State, ordering, and interface decisions

These interfaces are shared across tasks. Keep their names and field meanings consistent.

### Data types in `title_sync.py`

Use immutable `NamedTuple` records:

```python
class SourceCursor(NamedTuple):
    source_session_id: str
    storage_incarnation: str
    history_epoch: int
    line_num: int

class TitleObservation(NamedTuple):
    cursor: SourceCursor
    target_session_id: str
    kind: Literal["title", "rename_command", "command_success", "command_error"]
    title: str | None
    message_id: str | None
    parent_message_id: str | None

class TitleDecision(NamedTuple):
    session_id: str
    token: str
    title: str
    process_token: str | None

class ObservationResult(NamedTuple):
    apply: tuple[TitleObservation, ...]
    confirmed: tuple[str, ...]  # publication tokens
```

Tokens are opaque process-local identities, not title text or new database columns.
Use a fresh UUID for every accepted decision, including unchanged text confirmed as a user choice.
Use a separate fresh UUID for every managed process incarnation.

### Observation boundaries

1. Source positions use existing `storage_incarnation`, `history_epoch`, and JSONL line order.
2. A lower line only precedes a higher line within the same source incarnation and epoch.
3. Full-compute payloads already contain `observed_incarnation`, `observed_epoch`, and `observed_upper_line`. Preserve their revision guard.
4. Claude extraction must retain the ordered sequence of title, `/rename`, and command-result observations. A last-title-only map loses confirmation evidence.
5. Capture a complete-record transcript boundary immediately before dispatch. Derive it from the current source identity and complete file suffix, not only `Session.last_line`.
6. Scan outside the DB lock, bounded by the file size captured at the start. Recheck process and decision identities before dispatch.
7. During transition, retain provider observations without replacing the accepted database title.
8. SDK success requires the control response and a post-dispatch title record matching the requested canonical title. An already equal no-op may use the acknowledged complete-record boundary instead.
9. Hybrid results use message ancestry to associate a successful result with its command. This identifies a command outcome, not the sender of that command. Keep convergence and own-delivery resolution separate, as specified by the accepted hybrid limitation.
10. Establish a title floor at the confirming title record, not at the end of its ingest batch. Replay later observations in order.
11. Keep that positional floor after releasing temporary protection. Historical replay at or below the floor cannot restore a title.
12. A later provider title above the floor remains admissible, even if its text equals an earlier title. It retires obsolete pending publication work without creating a new provider write.
13. Ignore foreign-source title directives for an actively managed target. They cannot confirm its canonical transcript publication. Keep existing unmanaged import behavior.
14. On source epoch/incarnation change, discard incomparable observations, fail the old confirmation, and reconcile against the replacement history. Never compare line numbers across epochs.

Do not publish from inside the observer. It returns database changes and completion notifications to its caller.
Keep at most 128 unresolved evidence records per session. Compact decided prefixes.
On overflow, retain the earliest unresolved `SourceCursor` and the committed upper cursor as a replay range; discard the in-memory evidence buffer.
Use existing stored raw records to replay this range in chunks of at most 128 rows and 8 MiB through existing storage readers.
Reduce each chunk into bounded state: current decision, delivery hazard, latest effective command outcome, positional floor, and unresolved range.
Do not retain a map of every command UUID. Re-read a command's parent record from storage when needed.
If ancestry or replaced storage prevents replay, retain uncertain delivery and defer own publication until safe process-stop reconciliation.
Later proven manual outcomes must still apply. No overflow branch may restore an old decision or claim confirmation.
If a confirmation becomes ambiguous, do not fabricate success or repeat an already pasted hybrid command.

### Lock and ownership rules

- The existing DB write lock serializes acceptance and observation application.
- A short `threading.RLock` protects coordinator maps across executor threads. Never await, access ORM, or perform file I/O while holding it.
- The async publication worker owns provider I/O. Exactly one worker exists per session.
- New decisions replace queued work. An in-flight request resolves before a successor is sent.
- Provider acknowledgements return to the DB lock before changing protection or applying buffered observations.
- Every coordinator mutation uses a staged overlay inside the root DB transaction. Publish the overlay only after successful commit.
- `TitleSyncStage` is a context-managed mutable staging object. Its records remain immutable NamedTuples.
- Reads within the transaction see its overlay. Other workers see only committed state.
- Register overlay publication and worker notifications with `transaction.on_commit`, in that order, while the write lane remains serialized.
- Nested transactions share the root stage; rollback to a savepoint restores that savepoint's overlay. Root rollback discards the stage.
- The existing `live_state_transaction` does not cover this new state. Integrate staging explicitly in both full and live application.
- Never advance an observation floor, clear a delivery hazard, or retire a decision before its associated DB changes commit.
- Do not wait for watcher ingestion while holding the DB lock. The watcher needs that lock to provide confirmation.
- Manager readiness and death notifications use process tokens; old process responses cannot mutate new process state.

### Failure budgets

- SDK request timeout: 10 seconds. Total active SDK publication budget: 30 seconds.
- At most three SDK attempts. Before retry after uncertain delivery, ingest available confirmation evidence and check the current token.
- Hybrid composer wait: retain the existing 300-second budget and 3-second poll interval.
- After a successful paste, wait at most 10 seconds for command confirmation. Never blindly paste the same uncertain command again.
- STARTING waits for readiness without hot polling. Retry only on readiness or terminal process-stop notification.
- After budget exhaustion, stop active retries. Keep the latest pending decision for lifecycle reconciliation within the current backend run.
- A later successful explicit provider command retires an older failed publication. No lifetime blanket title rejection.
- On normal process exit, first drain and apply the complete final transcript suffix, then revalidate the pending decision before inactive reconciliation.
- Hard backend termination can lose pending memory. Normal subsequent import remains authoritative; no blind pre-crash replay.

---

### Task 1: Add the SDK compatibility adapter and canonical validation

**Files:**
- Create: `src/twicc/providers/claude_code/title_control.py`
- Modify: `src/twicc/providers/claude_code/helpers.py` (`validate_title` override)
- Test: `tests/test_claude_title_control.py`

**Interfaces:**
- Produces `TitleControlError(RuntimeError)` with `.reason: str` from `unavailable`, `timeout`, `rejected`.
- Produces `async rename_running_session(client: ClaudeSDKClient, session_id: str, title: str, *, timeout: float = 10.0) -> None`.
- Produces `canonicalize_claude_title(title: str | None) -> TitleValidationResult`.
- Consumes existing `TitleValidationResult` and the existing 200-character maximum.

- [ ] **Step 1: Write failing adapter and validation tests.**

```python
# Mock Query._send_control_request; do not start a CLI here.
assert sent == {"subtype": "rename_session", "title": "New", "source": "host", "session_id": sid}
assert timeout_argument == 10.0
assert canonicalize_claude_title("  État\t du\nchat  ").title == "État du chat"
assert canonicalize_claude_title("x" * 200).title == "x" * 200
assert canonicalize_claude_title("x" * 201).error
assert canonicalize_claude_title("\t \n").error
```

Add named cases `test_missing_query_is_unavailable`, `test_timeout_is_uncertain_failure`, `test_rejection_is_not_success`, and `test_cancellation_propagates`.
Reject non-whitespace control/invisible characters that the CLI would silently remove. Preserve printable Unicode punctuation and accents.
Assert no adapter error calls the file mutation helper. Assert Codex whitespace validation stays unchanged.

- [ ] **Step 2: Run the new tests and confirm failure.**

Run: `uv run pytest tests/test_claude_title_control.py -q`
Expected: missing adapter/override, then behavioral assertion failures. No live model call.

- [ ] **Step 3: Implement the adapter and Claude-only validation override.**

Call the private Query method with its `timeout` argument. Map expected connection/protocol failures into `TitleControlError`.
Propagate cancellation. Reject success-shaped assumptions: only normal completion of the SDK request is acknowledgement.
Use `" ".join(title.split())` before length validation. Reject unsupported invisible controls before database acceptance.

- [ ] **Step 4: Run adapter and existing origin-writer tests.**

Run: `uv run pytest tests/test_claude_title_control.py tests/test_title_origin_writers.py -q`
Expected: all pass; existing ordinary-title behavior remains unchanged.

- [ ] **Step 5: Commit the adapter and tests.**

Subject: `feat(titles): add active claude rename control adapter`

### Task 2: Build the pure transition and observation coordinator

**Files:**
- Create: `src/twicc/providers/claude_code/title_sync.py`
- Test: `tests/test_claude_title_sync.py`

**Interfaces:**
- Consumes Task 1's canonical title as input; does not normalize or access ORM.
- Produces `attach_process(session_id: str, process_token: str, *, hybrid: bool) -> None`.
- Produces `accept_decision(session_id: str, title: str) -> TitleDecision`.
- Produces `current_decision(session_id: str) -> TitleDecision | None`.
- Produces `start_dispatch(decision: TitleDecision, boundary: SourceCursor | None) -> bool`.
- `boundary=None` is valid only for SDK creation when no transcript file exists. It stores an unbound dispatch identified by process and decision tokens.
- Produces `bind_initial_source(decision: TitleDecision, first_cursor: SourceCursor) -> bool`. Call during the first committed ingestion of that process's canonical transcript; bind once, reject replaced tokens and foreign sources.
- For an unbound dispatch, retain its ACK and wait for a matching title in that first history. Do not confirm a no-op from an absent boundary.
- If a transcript already exists but DB storage identity is missing, ingest it before dispatch; `None` must not bypass existing history.
- Produces `observe(records: tuple[TitleObservation, ...]) -> ObservationResult`.
- Produces `acknowledge(decision: TitleDecision, *, success: bool, boundary: SourceCursor | None) -> ObservationResult`.
- Produces `retire_process(session_id: str, process_token: str) -> TitleDecision | None`.
- Produces `staged_state() -> ContextManager[TitleSyncStage]`; all mutators above operate on the active stage.
- Produces `TitleSyncStage.commit() -> None`, called only by the transaction coordinator after commit; uncommitted stages are discarded.
- Produces `TitleSyncStage.checkpoint() -> object` and `restore(checkpoint: object) -> None` for nested savepoint rollback.
- Produces `ReplayRange(NamedTuple)` with `start: SourceCursor` and `upper: SourceCursor`.
- Extend `ObservationResult` with `replay: tuple[ReplayRange, ...] = ()`; range endpoints use the same source identity.

- [ ] **Step 1: Write failing state-machine tests with exact cursor order.**

```python
# Each c(n) has the same source session/incarnation/epoch and line_num=n.
d1 = accept_decision(sid, "B")
assert observe((title(c(11), "A"),)).apply == ()
assert start_dispatch(d1, c(20))
assert observe((title(c(19), "B"),)).confirmed == ()
# A matching pre-dispatch value cannot confirm B.
d2 = accept_decision(sid, "A")
assert d2.token != d1.token
assert acknowledge(d1, success=True, boundary=c(21)).confirmed == ()
assert current_decision(sid) == d2
```

Run mutators inside a test staging context and commit explicitly. Assert committed readers cannot see staged changes.
Add `test_sdk_initial_dispatch_without_cursor`: call `start_dispatch(decision, None)`, ACK it, bind the first actual ingested cursor, then observe its matching title.
Assert no confirmation before the matching record, no synthetic cursor, and no binding from another process or source.
Add cases for SDK echo-before-ACK, ACK-before-echo, no-op ACK, same-batch own B followed by manual A, historical full replay, foreign source, epoch replacement, old process ACK, and rollback.
Assert the manual A above the floor is returned for application. Assert lower historical A remains ignored after temporary protection ends.
Feed split batches and one combined batch; assert identical final title and confirmation results.
Add `test_observation_rollback_replays_once`, `test_ack_rollback_preserves_hazard`, and `test_savepoint_rollback_restores_overlay`.
Assert rollback restores decisions, source floors, replay ranges, process tokens, and confirmation state.
Add `test_distinct_command_overflow_is_bounded`: feed 10,000 unique command/result records and assert the 128-record cap.
Replay committed ranges in chunks and assert the same final manual title and event sequence as unbounded input.

- [ ] **Step 2: Run and confirm failures.**

Run: `uv run pytest tests/test_claude_title_sync.py -q`
Expected: missing coordinator module, then failed ordering assertions.

- [ ] **Step 3: Implement the coordinator and the boundary algorithm above.**

Use UUID identities, an `RLock`, and per-session state. Keep the highest accepted source position per source epoch.
Model `queued`, `dispatched`, `acknowledged`, `confirmed`, `failed`, and `superseded` distinctly.
Store only relevant ordered observations. Do not collapse a batch before confirmation and manual-rename reconciliation.
Return accepted observations in order; ensure consuming a result twice cannot create another change.

- [ ] **Step 4: Run all pure coordinator and adapter tests.**

Run: `uv run pytest tests/test_claude_title_sync.py tests/test_claude_title_control.py -q`
Expected: all pass, without database or real CLI dependence for coordinator cases.

- [ ] **Step 5: Commit the coordinator and tests.**

Subject: `feat(titles): coordinate claude title transitions by source position`

### Task 3: Implement SDK and hybrid publication with bounded confirmation

**Files:**
- Create: `src/twicc/providers/claude_code/title_publication.py`
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py` (rename and auto-paste handling)
- Modify: `src/twicc/providers/claude_code/agent/hybrid/signals.py`
- Test: `tests/test_claude_title_publication.py`
- Test: `tests/test_hybrid_title_confirmation.py`

**Interfaces:**
- Consumes Task 1 adapter and Task 2 identities/state.
- Produces `request_publication(decision: TitleDecision) -> None`, which schedules work without waiting for a composer.
- Produces `async flush_publications(session_id: str, *, timeout: float) -> bool` for shutdown/testing only.
- Produces `notify_process_ready(session_id: str, process_token: str) -> None`.
- Produces `async finish_process(session_id: str, process_token: str) -> None`.
- Produces `async close_publications() -> None` for bounded manager cleanup.
- Produces `process_publication_reservation(session_id: str) -> AsyncContextManager[None]`, a per-session async lock shared by launch/adoption and inactive publication.
- Lock order: acquire the reservation, then enter the short DB write lane to revalidate; release the DB lane before provider I/O.
- Never wait for this reservation while holding the DB write lock. Live compute and ACK processing do not acquire it.
- Changes hybrid `async rename(self, title: str, *, token: str) -> None` to own the paste-and-confirm operation; the caller is a background publication worker.
- Produces `HybridRenameEvidence = TitleObservation` as an alias for ordered command evidence; keep its stream separate from existing boolean turn signals.

- [ ] **Step 1: Write failing publisher and hybrid tests using controlled events.**

```python
# No sleeps: a fake transport exposes entered/release events.
request_publication(d1)
await transport.entered.wait()
request_publication(d2)
assert transport.sent == [d1.title]
transport.release.set()
await flush_publications(sid, timeout=1)
assert transport.sent == [d1.title, d2.title]
assert transport.max_concurrent == 1
```

Hybrid cases: busy composer; queued title superseded; successful paste without a result; old matching title; unrelated `/model` ACK; command error; own success then manual old title in one batch.
Assert a successful explicit outcome updates the DB even while an independent paste hazard remains; the next raw title echo cannot revive the retired pending decision.
Require command identity/ancestry scope for command outcomes, not a generic `local_command_ack` flag or matching screen text. Do not infer paste ownership from ancestry.
Include equivalent same-title manual commands before/after a paste boundary, including input queued before its JSONL echo.
Assert matching ancestry proves convergence only. It must not clear an outstanding own-paste hazard without attribution.
Assert successor publication stays queued while that hazard remains; confirmed process death clears it.
Test collision/yielded results with suffixed actual names and superseded-command messages. Neither confirms the requested title.
Assert the existing permission-prompt and real-assistant-turn state remain unchanged by rename acknowledgements.

- [ ] **Step 2: Run and confirm failures.**

Run: `uv run pytest tests/test_claude_title_publication.py tests/test_hybrid_title_confirmation.py -q`
Expected: missing publication API, then missing confirmation behavior.

- [ ] **Step 3: Implement the publication worker and the user-approved hybrid contract.**

Apply the accepted strict hybrid contract. Keep hybrid activation unchanged.
Resolve the current manager entry at dispatch, not at enqueue time. Verify both process token and decision token.
SDK route awaits Task 1 and records confirmation evidence through the DB application lane.
Hybrid route uses `_checked_paste` and its existing paste lock; disable the old independent `rename` auto-paste task.
Keep settings auto-paste behavior unchanged. Wake pending title work on readiness instead of falling back to JSONL.
Use the defined budgets. A pasted-but-unconfirmed command remains uncertain; do not resend it blindly.
Treat malformed or title-mismatching command results as failures, not confirmation.

- [ ] **Step 4: Run focused tests.**

Run: `uv run pytest tests/test_claude_title_publication.py tests/test_hybrid_title_confirmation.py tests/test_claude_title_sync.py -q`
Expected: all pass; no file write occurs on either active route, including failure branches.

- [ ] **Step 5: Commit publication and confirmation.**

Subject: `feat(titles): publish through active claude processes with confirmation`

### Task 4: Integrate ordered observations into live and full compute

**Files:**
- Modify: `src/twicc/providers/compute_base.py` (title extraction maps, live application, `compute_final_titles`)
- Modify: `src/twicc/providers/claude_code/compute.py` (`extract_custom_title`, `apply_session_title`, command extraction helpers)
- Modify: `src/twicc/providers/claude_code/sessions_watcher.py` (hybrid JSONL bridge)
- Test: `tests/test_title_compute_apply.py`
- Test: `tests/test_claude_title_observations.py`

**Interfaces:**
- Consumes Task 2 `TitleObservation`, `SourceCursor`, and `ObservationResult`.
- Produces Claude `title_observations` payloads as ordered serialized NamedTuple records; restore types at the main-process boundary.
- Introduces base hook `apply_provider_title_observations(self, records: tuple[TitleObservation, ...]) -> tuple[str, ...]` for provider-specific application.
- Base/default path retains existing provider maps and Codex semantics. Claude overrides observation handling.
- Produces `apply_claude_observations_locked(records: tuple[TitleObservation, ...]) -> tuple[str, ...]` in `title_publication.py` for both compute paths and acknowledgement replay.

- [ ] **Step 1: Add failing end-to-end compute application tests.**

```python
@pytest.mark.parametrize("mode", ["live", "full"])
def test_predecision_observation_cannot_restore_title(mode):
    # Prepare A before accepting B; apply its result after publication confirms.
    assert stored_title == "B"
    assert title_changed_events == [("A", "B", "auto")]

@pytest.mark.parametrize("mode", ["live", "full"])
def test_manual_old_title_after_own_echo_is_preserved(mode):
    # Ordered records: own B echo, own command success, manual /rename A and its title.
    assert stored_title == "A"
    assert title_origin == original_origin
    assert no_corrective_file_appends
```

Add `test_full_apply_rollback_replays_manual_title_once` and `test_live_apply_rollback_replays_manual_title_once`.
Inject an SQL failure after observation consumption and title update. Roll back, replay the same records, and assert the title and one timeline event commit.
Repeat with an ACK that would release protection; rollback must preserve the prior protection.
Also cover split batches, a foreign target directive, replacement `history_epoch`, absent observation payload on Codex, and a first-title placeholder.
Assert stale compute still returns its existing stale outcome without title side effects.

- [ ] **Step 2: Run and confirm failures.**

Run: `uv run pytest tests/test_title_compute_apply.py tests/test_claude_title_observations.py -q`
Expected: failures for lost ordering, bypassed protection, and absent source identities.

- [ ] **Step 3: Implement shared Claude acceptance without provider I/O.**

Extract observations from the raw parsed records before display classification can hide slash-command evidence.
Use `extract_command`, `get_message_content`, and existing text extraction; do not implement a strict XML parser.
For stored rows, use the existing content-storage readers, including `iter_current_items` and `effective_current`.
Pass the ordered payload through full compute with its existing incarnation/epoch revision guard.
Keep title application inside the existing DB write lane and transaction.
Use the root transactional stage for `observe`, ACK replay, and process-state mutations.
After `observe`, use `_write_provider_title` only for accepted title observations and record changed session IDs normally.
Schedule completion wakeups after commit. No coroutine waits or provider mutations occur inside synchronous compute.
Extend the hybrid bridge with ordered rename evidence; keep boolean turn-end and pending-permission behavior unchanged.

- [ ] **Step 4: Run compute, timeline, and storage regression tests.**

Run: `uv run pytest tests/test_title_compute_apply.py tests/test_claude_title_observations.py tests/test_timeline_title_settings.py tests/test_title_origin_writers.py -q`
Expected: all pass; no duplicate timeline events, no origin inference, no Codex behavior changes.

- [ ] **Step 5: Commit the read-side integration.**

Subject: `fix(titles): reject stale claude observations in live and full compute`

### Task 5: Integrate every decision writer and managed lifecycle

**Files:**
- Create: `src/twicc/core/services/session_titles.py`
- Modify: `src/twicc/views.py` (`session_detail` title branch)
- Modify: `src/twicc/core/services/session_update.py` (`update_session_title_from_payload`)
- Modify: `src/twicc/core/services/title_automation.py` (`_apply_check`, `_publish_change`)
- Modify: `src/twicc/agent/base_manager.py` (pending title flush/retry, Claude branch only)
- Modify: `src/twicc/providers/claude_code/{helpers.py,titles.py}`
- Modify: `src/twicc/providers/claude_code/agent/{agent.py,manager.py}`
- Modify: `src/twicc/providers/claude_code/agent/hybrid/{agent.py,launch.py}`
- Test: `tests/test_title_origin_writers.py`
- Test: `tests/test_title_automation.py`
- Test: `tests/test_title_protection_lifecycle.py`
- Test: `tests/test_claude_title_decisions.py`

**Interfaces:**
- Consumes Tasks 1–4.
- Produces `AcceptedTitle(NamedTuple)` with `title: str`, `changed: bool`, `decision: TitleDecision | None`.
- Produces `async accept_title_locked(session_id: str, title: str, *, source: Literal["auto", "user"], origin: RequestOrigin | None = None, extra_updates: dict | None = None) -> AcceptedTitle`.
- Caller must own the existing DB write lock. `extra_updates` accepts only `title_check_count` and `title_checked_at` for automatic checks.
- Produces `async accept_explicit_title(session_id: str, title: str, *, origin: RequestOrigin | None = None) -> AcceptedTitle`, which acquires the lock and delegates.
- Result is the accepted DB decision, not a claim of completed provider synchronization.

- [ ] **Step 1: Add failing writer and lifecycle tests.**

```python
@pytest.mark.parametrize("writer", ["rest", "cli", "pending", "automatic"])
def test_protection_precedes_database_visibility(writer):
    assert late_old_observation_during_publish_cannot_replace_decision

def test_same_text_user_confirmation_supersedes_automatic_work():
    assert new_decision.token != automatic_decision.token
    assert title_origin == "user"
    assert no_duplicate_title_changed_event
```

Additional cases: stale automatic snapshot; fresh old-title event attribution; canceled DB transaction; late pending-create retry; unavailable transcript; STARTING readiness; process replacement; hybrid 150/200-character title on launch and resume.
Assert normalization occurs before DB/timeline publication. Do not shorten titles to 100 characters at either hybrid launch layer.

- [ ] **Step 2: Run and confirm failures.**

Run: `uv run pytest tests/test_claude_title_decisions.py tests/test_title_origin_writers.py tests/test_title_automation.py tests/test_title_protection_lifecycle.py -q`
Expected: new assertions expose late protection, unsafe corrections, and startup fallback.

- [ ] **Step 3: Implement atomic acceptance and route all Claude writers.**

Read the current title and origin inside the DB write lock. Do not use a stale request-loaded Session for event `old` values.
For Claude, stage `accept_decision` together with the DB write. Commit both through the serialized transaction lane; discard the stage on failure.
Persist text, origin, and allowed automatic counters in one transaction. Use only the existing `record_title_change` service.
After successful commit, enqueue the returned decision and retain current broadcast/search behavior.
Automatic eligibility and snapshot compare-and-set remain in `_matching_session`/`_apply_check`; do not consume stale results.
Delete the text-difference corrective push from `_publish_change` for Claude. Codex retains its existing behavior.
Keep non-Claude writer branches and pending verification behavior unchanged.

- [ ] **Step 4: Wire readiness and normal stop.**

Register a process token before it can produce imported title observations. Replace the token for each new SDK/PTY process.
SDK control becomes ready after `client.connect()`. Dispatch a pending title before the first prompt where the channel permits it.
Do not wait for JSONL confirmation before allowing the first prompt: initial transcript storage may not exist yet.
Retain the ACK until ingestion supplies the initial storage identity; do not fabricate a cursor or fall back to a file append.
Test first creation with no transcript and no `storage_incarnation`; the first prompt proceeds and subsequent ingestion confirms the title.
Hybrid publication becomes ready when the TUI accepts guarded paste. Preserve onboarding behavior.
On adoption after backend restart, import existing external changes before choosing a launch synchronization title.
Cancel obsolete pending-title retries by decision identity; never reaccept their old text after a later rename.
Both managed launch/adoption and inactive-file reconciliation must acquire `process_publication_reservation(session_id)`.
Hold this reservation through the inactive write; a successor process cannot start or adopt during that interval.
On confirmed process death, stop new dispatch and await the worker's provider-I/O termination.
Capture the final complete-record boundary after the process cannot write. Ingest and apply through that boundary using the normal ordered observation path.
Await DB commit without holding the write lock across ingestion. Then reacquire the lane, revalidate process/storage identities and the current decision token.
A successful final manual command retires any older pending decision before reconciliation.
Only then retire the process token and perform at most one inactive-file write for a still-current pending decision.
If final ingestion fails or history changes, defer reconciliation and log failure. Never append the pre-drain decision speculatively.
Remove the old DEAD rewrite of `get_protected_title` and the Claude lifetime protection dictionary.
Keep the generic automatic echo machinery for Codex. Do not let Claude's legacy text-only echo TTL reject a later manual old title.

- [ ] **Step 5: Run writer, publisher, compute, and lifecycle tests.**

Run: `uv run pytest tests/test_claude_title_decisions.py tests/test_title_origin_writers.py tests/test_title_automation.py tests/test_title_protection_lifecycle.py tests/test_title_compute_apply.py tests/test_claude_title_publication.py tests/test_hybrid_title_confirmation.py -q`
Expected: all pass, including unchanged Codex paths and existing automatic-title eligibility tests.

- [ ] **Step 6: Commit integrated decisions and lifecycle behavior.**

Subject: `fix(titles): synchronize all claude title decisions before stale imports`

### Task 6: Exercise production interleavings and recovery limits

**Files:**
- Test: `tests/test_claude_title_sync_integration.py`
- Modify tests: `tests/test_title_auto_task.py`, `tests/test_title_compute_apply.py`, `tests/test_title_protection_lifecycle.py`
- Modify source only where these tests expose a violation of Tasks 1–5 contracts.

**Interfaces:**
- Consumes all integrated APIs. Adds no second state machine or writer path.
- Test fixtures reset coordinator state and close workers; use existing isolated DB configuration.

- [ ] **Step 1: Add failing deterministic interleaving tests.**

Use `asyncio.Event`, fake clocks, and controlled executor completions. Do not prove ordering through arbitrary sleeps.
Test the production sequence: DB accepts D, old B read completes, publication confirms D, and no B correction occurs.
Assert `[(old, D, "auto")]` is the only new title event and that all managed writes use control or `/rename`.

Add tests for:

- SDK failure before send, rejection, timeout before write, and write-success-before-timeout.
- Three attempt/30-second limits, hybrid 300-second wait, 10-second confirmation limit, and no blind re-paste.
- A new explicit decision while an old attempt times out; late ACK must not clear the new protection.
- Same-batch hybrid confirmation then manual old title, followed by normal shutdown and full recompute.
- Pending-create retry after another writer already accepts a new title.
- Incarnation/epoch reset while confirmation waits; unrelated metadata still applies normally.
- Unmanaged file publication followed by external A and `/rename C`; both later imports remain admissible.
- Restart after an unconfirmed write: do not reconstruct an indefinite protection or blindly replay the old DB title.
- Thousands of repeated metadata echoes and 10,000 distinct commands/results: at most 128 buffered records; replay retains final ordering.
- Final manual `/rename A` exists on disk but is not ingested when the process dies: drain it, accept A, and never append pending B.
- Failed final drain: no inactive append; later normal ingestion can still accept A.
- Pause an inactive reconciliation write, request P2 launch, and assert P2 cannot start until the write completes. Assert no DB lock spans the paused I/O.
- Launch P2 before reconciliation acquires the reservation: revalidation selects the active route and performs no inactive append.
- SQL rollback after observation or ACK processing: replay commits the manual title and its event exactly once.
- Two different sessions publishing independently; no global provider I/O serialization.

- [ ] **Step 2: Run the new integration file.**

Run: `uv run pytest tests/test_claude_title_sync_integration.py -q`
Expected: a missing or incomplete behavior fails its named assertion. If already passing, keep the independent regression evidence.

- [ ] **Step 3: Correct only demonstrated contract violations.**

Do not broaden the spec to crash-atomic delivery, unmanaged process control, or historical repair.
Keep any source corrections in the responsible module from the file map.

- [ ] **Step 4: Run the complete title regression selection.**

Run: `uv run pytest tests/test_claude_title_control.py tests/test_claude_title_sync.py tests/test_claude_title_publication.py tests/test_hybrid_title_confirmation.py tests/test_claude_title_observations.py tests/test_claude_title_decisions.py tests/test_claude_title_sync_integration.py tests/test_title_automation.py tests/test_title_auto_task.py tests/test_title_compute_apply.py tests/test_title_origin_writers.py tests/test_title_protection_lifecycle.py tests/test_title_echo.py tests/test_timeline_title_settings.py -q`
Expected: all pass with no real provider calls.

- [ ] **Step 5: Commit the interleaving regressions and any required fixes.**

Subject: `test(titles): cover claude publication races and recovery boundaries`

### Task 7: Add the real-CLI upgrade gate and maintainer instructions

**Scoped execution ruling (2026-11-01):** Spec section 12 defines the required SDK compatibility gate.
The default `--live --yes` run checks SDK behavior and reports hybrid as NOT_RUN.
The user keeps hybrid disabled and accepts its strict limitation.
The explicit `--live --yes --hybrid` run is mandatory before future hybrid activation.
A blocked explicit TUI probe reports INCONCLUSIVE and exits 1.
Offline hybrid fixtures never substitute for real TUI evidence.
This ruling changes the Task 7 combined-gate expectation below; it does not activate hybrid mode.


**Files:**
- Create: `scripts/diagnose_claude_title_sync.py`
- Test: `tests/test_claude_title_diagnostic.py`
- Create: `docs/claude-sdk-updates.md`
- Modify shared instructions: `AGENTS.md`, `CLAUDE.md` only according to their existing shared-file relationship.

**Interfaces:**
- Diagnostic CLI: `uv run python scripts/diagnose_claude_title_sync.py [--live] [--yes] [--json]`.
- Offline mode performs no model calls; live mode requires `--yes` and isolates all generated sessions/configuration.
- Exit codes: 0 for PASS, 1 for FAIL/INCONCLUSIVE, 2 for setup failure.
- Include installed SDK and actual bundled CLI versions, per-check status, and total reported API cost.

- [ ] **Step 1: Write failing offline diagnostic tests.**

```python
assert offline_report.model_calls == 0
assert report_exit(["PASS"]) == 0
assert report_exit(["INCONCLUSIVE"]) == 1
assert report_exit(["FAIL"]) == 1
assert setup_failure_exit == 2
assert "CLAUDE_CODE_OAUTH_TOKEN" not in rendered_report
```

Add cleanup-on-error and invalid-session cases. Assert provider homes come from `provider_homes` and processes receive `provider_env_overlay()`.

- [ ] **Step 2: Run and confirm failures.**

Run: `uv run pytest tests/test_claude_title_diagnostic.py -q`
Expected: absent diagnostic/report interfaces, then missing isolation or exit-code behavior.

- [ ] **Step 3: Implement the diagnostic and add SDK update instructions.**

Follow `scripts/diagnose_hermetic_llm.py` report conventions, but keep the persistent-session probe separate from hermetic calls.
Use a temporary scratch directory, isolated Claude config/secure-storage homes, and an OAuth token read through existing credential accessors.
Apply provider environment configuration explicitly; override isolated homes intentionally. Never print tokens or copy the user's session/configuration files.
Disable tools, MCP, project settings, skills, and instruction files for SDK probes. Use `haiku` for minimal diagnostic responses.
Sanitize inherited Claude nesting variables as existing isolated calls require.
Live SDK checks cover file-append reproduction, idle control rename, streaming control rename, metadata re-append, resume, wrong-session rejection, and reuse of an old title.
Add a controlled hybrid fixture/probe for the exact current CLI command echo, result ancestry, and title record order.
If tmux/TUI onboarding prevents that probe, report INCONCLUSIVE; do not synthesize a live PASS from unit fixtures.
Retain no user transcripts. Stop only diagnostic processes, clean temporary sessions, and keep version/status output.
Document the required existing command `uv run python scripts/diagnose_hermetic_llm.py --provider all --live --yes` and the new title diagnostic.
An SDK update may not ship while either required diagnostic is FAIL or INCONCLUSIVE.

- [ ] **Step 4: Run offline tests and the isolated live diagnostic.**

Run: `uv run pytest tests/test_claude_title_diagnostic.py -q`
Expected: all pass.

Run: `uv run python scripts/diagnose_claude_title_sync.py --live --yes`
Expected: all required SDK checks PASS, including persistence after active SDK control; hybrid is NOT_RUN.
The explicit `--live --yes --hybrid` gate also requires real TUI evidence before future hybrid activation.
Record actual SDK/CLI versions. On FAIL/INCONCLUSIVE, diagnose and fix the affected contract before declaring implementation complete.
Run the existing hermetic live diagnostic if the SDK or its configuration changes; no SDK upgrade is required by this plan.

- [ ] **Step 5: Commit the diagnostic and update gate.**

Subject: `test(titles): verify private claude rename protocol on sdk updates`

## Coverage and final validation

| Spec acceptance | Owning tasks |
|---|---|
| A1–A3, A13 | 1, 3, 7 |
| A4–A6 | 2, 4, 6 |
| A7–A8 | 2, 5, 6 |
| A9–A12 | 2, 3, 4, 6 |
| A14–A15 | 2, 3, 5, 6 |
| A16 | 5, 7 |
| A17, A21 | 4, 5, 6 |
| A18–A20 | 4, 5, 6 |
| A22–A23 | 1, 5, 7 |

- [ ] Check every `helpers.rename_session(...)` call site. Claude must not bypass decision identity, while Codex remains unchanged.
- [ ] Run the Task 6 title selection plus the Task 7 offline diagnostic tests once after final source changes.
- [ ] Run `git diff --check`. Review only the implementation's changed files; preserve unrelated user changes.
- [ ] Run the live title diagnostic and retain its final versioned report.
- [ ] Report any remaining failures honestly. Do not claim confirmation from a queued hybrid task or a matching historical title.
- [ ] Tell the user backend changes require their dev-server restart. Do not restart without their explicit request.

## Self-review and adversarial review record

Author checks: all 23 acceptance criteria have owning tasks; interfaces and tokens are defined once; Codex and crash scope stay unchanged.
The plan uses seven independently testable deliverables. Source bodies remain the implementer's responsibility.

Round 1: two independent internal reviewers inspect ordering and integration.
Findings: hybrid paste attribution is unproved (P1); coordinator rollback, final-stop drain, and evidence-buffer bounds need explicit contracts (P2).
Revision 2 adds transactional staging, final transcript drain, a 128-record cap with stored replay, and an explicit hybrid approval gate.
The author also adds first-prompt creation coverage and collision/superseded-result cases.
Round 2: both reviewers identify the initial-dispatch cursor mismatch; ordering review also identifies a successor-process launch race during inactive reconciliation.
Revision 3 defines unbound initial SDK dispatch and a shared per-session launch/publication reservation, with regression cases.
Round 3: both reviewers approve the technical corrections and identify no new material P1/P2 defect.
After round 3, the user accepts the strict hybrid limitation because hybrid mode is disabled and may never be enabled.
Revision 4 records this decision in the plan and spec. The product gate is resolved; implementation still requires an explicit invitation.
Whitespace validation passes for this new document. No product code changes or implementation tests run during planning.


## Final implementation validation

Implementation and final correction reviews are approved with no remaining findings.
Runtime changes are committed through `3951fb16d`.

- Required title/diagnostic selection and shutdown tests: **468 PASS** in 20.34 seconds.
- Feature Python Ruff and `git diff --check`: **PASS**.
- Required real SDK compatibility checks: **PASS**, with SDK **0.2.175** and bundled CLI **2.1.314**.
- Explicit real hybrid probe: **INCONCLUSIVE**, blocked by the isolated trust prompt.
- Default SDK diagnostic reports hybrid **NOT_RUN**. Hybrid remains disabled.
- No backend restart occurs during implementation or validation. The user retains that operation.

Historical planning and authorization statements above retain their original review context.
