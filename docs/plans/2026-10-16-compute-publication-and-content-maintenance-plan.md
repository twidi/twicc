# Compute Publication and Content Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce repeated compute work and long background writer holds while preserving foreground writes and source readiness.

**Architecture:** Extend the existing retained compute application with lifecycle, facts, and validation phases. Use writer-lifetime notifications for dispatch and maintenance gating. Reuse authoritative maintenance metadata only within one admitted publication transaction.

**Tech Stack:** Python 3.13+, Django 6, SQLite WAL, asyncio, existing compute executors, orjson, pytest and pytest-django.

**Spec:** [Compute publication and content maintenance](2026-10-16-compute-publication-and-content-maintenance-spec.md).

**Status:** Historical implementation plan. Implementation is delivered on the feature branch.
The original planning checklist and review record remain as written; they do not represent current execution progress.
See the [validation report](2026-10-16-compute-publication-and-content-maintenance-validation.md) for completed checks and remaining limits.

## Global Constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`, baseline `307cd0ba`.
- Foreground work retains precedence at every background publication boundary.
- The writer retains ownership until its synchronous publication worker actually finishes, including after caller cancellation.
- A background preparation never reserves the writer while it waits or performs CPU work.
- Each publication obtains SQLite write ownership before authoritative reads that precede writes.
- Advance operation cursors, accepted epochs, and validation coverage only after successful commit.
- Progressive history-fact publication accepts outdated sessions only. Current compatibility calls retain atomic fact replacement.
- Use existing schemas, source boundaries, storage epochs, and compute-version readiness. Add no migrations or dependencies.
- Preserve existing metadata batches of 2,000 items and content budgets of 256 rows / 8 MiB.
- New lifecycle, facts, and validation budgets are explicit starting values below. Tune them using the required benchmarks.
- Target sub-100 ms normal publication slices on the reference machine. Investigate holds above 250 ms. A repeatable one-second hold in a changed phase fails acceptance.
- Report p50, p95, p99, maximum, and counts above 100, 250, 500, and 1,000 ms.
- Distinguish writer hold, transaction, admission wait, preparation, dispatch wait, and foreground latency.
- All source, tests, comments, and documents use English.
- Every shell invocation starts with the absolute worktree `cd`. Tests set `TWICC_DATA_DIR=$PWD`.
- Do not restart servers or replace their DB/index as part of unit tests or isolated benchmarks.
- A full worktree reset/start requires the user's explicit operational instruction.
- Preserve pre-existing documents and user changes. Stage only the files belonging to the current task.

## Review Focus

1. A current compatibility caller enters partial fact replacement: preserve complete readable facts. Task 5 tests this.
2. External root mutation precedes an operation-owned lifecycle update: reject stale evidence before adopting any new epoch. Tasks 1 and 6 test this.
3. A receiver enqueues during the idle transition or shutdown: lose no wakeup and drain without spinning. Task 3 tests this.
4. Provider pending flags clear before the last application drains: keep compression excluded, then wake maintenance. Task 4 tests this.
5. A stricter plaintext requirement appears after some validation pages: earlier cold coverage does not authorize plaintext readiness. Task 6 tests this.

## File and Interface Map

All `src/` paths below are relative to the repository root.

| Unit | Files | Responsibility |
|---|---|---|
| Lifecycle projection | New `src/twicc/providers/compute_lifecycle.py`; `core/content_storage/lifecycle.py` | Normalize stop evidence; publish guarded, grouped mutations |
| Application phases | `src/twicc/core/content_storage/extraction.py`, `types.py`; `providers/db_writer.py`, `compute_base.py` | Retain worker ownership and progress through bounded phases |
| Maintenance reuse | `src/twicc/content_maintenance_task.py` | One authoritative owner/content context per transaction |
| Writer notifications | New `src/twicc/providers/writer_signals.py`; `db_writer.py`, `write_coordinator.py` | Queue/task/resource notifications and event-driven drain |
| Compression exclusion | `src/twicc/providers/db_writer.py`; `content_maintenance_task.py` | Inspect existing accepted-source state and notify complete-gate changes |
| Fact publication | `src/twicc/providers/history_facts.py`; `extraction.py`, `compute_base.py`, `db_writer.py` | TEMP preparation, bounded durable deletion/insertion, version gate |
| Storage validation | New `src/twicc/core/content_storage/validation.py`; `extraction.py`, `state.py`, `types.py`, `compute_base.py` | Bounded metadata coverage and short private completion |

Keep changes in existing large modules limited to their orchestration boundaries.
Use `NamedTuple` for immutable result records. Keep mutable operation state on `ComputeStorageOperation`.
Imports of the operation type from focused helpers use `TYPE_CHECKING` or existing lazy imports to avoid cycles.

## Execution, Interfaces, and Commits

Execute Tasks 1–8 in order. Tasks that touch the phase machine are sequential.
An implementer reads this plan and the linked spec before starting a task.
Review each task for spec compliance, then code quality, before starting the next task.

Every task follows red → green → regression tests → review → commit.
If a new test already passes, verify the intended assertion observes the production path before changing code.
Commit subjects below require a descriptive body and the current model's prescribed co-author trailer.
Do not use `git add .`.

### Phase Contract Used by Tasks 1, 5, and 6

The final progressive phase order is:

```text
counts → metadata → overrides → extraction → stopped_agents → reconciliation
       → history_stage → history_delete → history_insert → validation → final
```

Task 1 adds `stopped_agents`. Task 5 adds the three history phases. Task 6 adds `validation`.
Each task updates the same explicit transition map and its phase-order test.

`begin_compute_application(msg: dict, *, progressive_facts: bool = False) -> ComputeStorageOperation`
retains atomic facts for the synchronous compatibility wrapper by default.
Task 5 changes normal writer dispatch to pass `progressive_facts=True`.
Task 6 adds progressive-validation state only for the live retained-operation path.
The detached synchronous compatibility result retains its existing independent final storage checks.

`prepare_compute_batch(operation) -> PreparedComputeBatch` prepares exactly one phase input.
`publish_compute_batch(operation, batch, active_ids) -> ComputeStorageProgress` publishes exactly that input.
`final_compute_message(operation, active_ids) -> dict` exposes only completed operation state.
`close_compute_application(operation) -> None` closes all retained TEMP resources on their owning worker.

### Task 1: Group and Slice Historical Agent Stops

**Files:**
- Create: `src/twicc/providers/compute_lifecycle.py`.
- Modify: `src/twicc/core/content_storage/lifecycle.py` — optional deferred readiness inspection for the compute-specific grouped path.
- Modify: `src/twicc/core/content_storage/extraction.py` — stopped-agent phase and transition map.
- Modify: `src/twicc/providers/compute_base.py` — remove repeated progressive final stop loop.
- Create: `tests/test_compute_lifecycle_publication.py`.
- Modify: `tests/test_content_lifecycle.py`, `tests/test_content_extraction.py`.

**Interfaces:**
- Add `StoppedAgentUpdate(session_id: str, stopped_at: datetime)` in `compute_lifecycle.py`.
- Add `normalize_agent_stops(entries: Sequence[dict]) -> tuple[StoppedAgentUpdate, ...]`.
- Add `publish_agent_stops(msg: dict, updates: tuple[StoppedAgentUpdate, ...]) -> tuple[str, ...]`, returning changed IDs.
- Extend `lifecycle_change(session_ids, *, defer_plaintext_inspection: bool = False)` without changing default callers.
- Extend `update_lifecycle(queryset, *, defer_plaintext_inspection: bool = False, **updates)`.
- Forward that explicit keyword only to `lifecycle_change`; never pass it to `QuerySet.update`.
- Test the wrapper and assert generated UPDATE fields never contain the control keyword.
- Start the stopped-agent phase with 64 agents and 256 KiB of serialized ID/timestamp input per slice.
- Store normalized entries on the retained operation. Normalize on its preparation worker, not during writer-held initialization.
- Reuse `apply_compute_lifecycle` for the one slice-level epoch check and mutation accounting.
- During `publish_compute_batch`, pass the helper a message containing `_storage_final_root` bound to the current scoped placement dictionary.

- [ ] **Step 1: Add failing grouping and interruption tests.**

Create `test_repeated_stops_publish_latest_once`: one agent has three stops and no newer activity.
After publication, assert the latest timestamp is stored and only one agent UPDATE executes.

```python
assert child.last_stopped_at == latest_stop
assert child.last_updated_at == latest_stop
assert agent_update_statements == 1
```

Add `test_equal_stops_do_not_write_or_invalidate`, checking zero agent UPDATE, zero root epoch change, and no promotion request.
Add `test_live_resume_between_preparation_and_publication_wins`, with `last_updated_at > stopped_at`.
Add `test_external_epoch_is_checked_before_grouped_mutation` and force an external reference mutation before the slice.
Assert superseded outcome, unchanged child timestamps, and no adopted external epoch.

Add `test_stopped_agents_release_writer_between_slices` with more than 64 agents and a foreground write.
Add `test_restart_after_committed_child_stop_replays_root_only`: child stays current, root stays outdated, root replay completes.
Reuse real content fixtures to cover both current/original references and valid root-only certificate behavior.

- [ ] **Step 2: Run the new tests against the baseline.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_lifecycle_publication.py -q`.
Expected: new grouping, no-op, slicing, or stale-epoch assertions fail.

- [ ] **Step 3: Implement normalization and one grouped lifecycle publication.**

Retain the maximum timestamp per agent, preserving deterministic first-agent order.
Load candidate metadata in bounded groups and remove exact no-ops before entering lifecycle mutation handling.
Execute parameterized UPDATE statements through `executemany`, retaining the SQL condition `last_updated_at IS NULL OR last_updated_at <= stopped_at`.
Resolve roots and compare before/after lifecycle metadata for the slice, rather than for each event.
Invalidate each changed root once and retain committed cache invalidation and promotion obligations.

In the compute-specific deferred path, use existing valid preservation evidence where available.
Unknown readiness schedules the existing targeted bounded promotion/inspection path; it cannot authorize a certificate.
Do not synchronously scan each changed child's complete item history inside this lifecycle slice.
The default lifecycle path retains its current behavior.

Check expected epochs before own writes. Publish accepted operation state after successful transaction exit only.
The scoped placement dictionary absorbs only the slice's own epoch changes; the retained operation is unchanged on rollback.

- [ ] **Step 4: Wire the stopped-agent phase and final handoff.**

Prepare bounded slices from the normalized operation entries.
Advance the integer event cursor only after commit. An empty list completes normally.
Place the phase after extraction and before reconciliation.
The progressive final path skips stops only when its live operation has completed this phase.
The compatibility wrapper passes an empty stop list in its internally prepared final message after applying the phase.
Do not trust a caller-supplied Boolean to authorize skipped readiness checks.
Retain the historical event tables; deduplication changes only lifecycle projection.

- [ ] **Step 5: Run lifecycle and phase regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_lifecycle_publication.py tests/test_content_lifecycle.py tests/test_content_extraction.py tests/test_claude_subagent_lifecycle.py tests/test_nested_agent_compute.py -q`.
Expected: PASS. No final per-event loop executes in the progressive path.

- [ ] **Step 6: Review and commit.**

Subject: `perf(compute): group and slice stopped-agent lifecycle updates`.

### Task 2: Reuse Maintenance Metadata Within Publication

**Files:**
- Modify: `src/twicc/content_maintenance_task.py` — readiness helpers, proof matching, representation publication.
- Modify: `tests/test_content_maintenance_reads.py`, `tests/test_content_maintenance.py`.

**Interfaces:**
- Add `PublicationContext(rows: dict[int, SessionItemContent], readiness: dict[str, OwnerReadiness])` as an immutable wrapper.
- Add `_publication_context(entries: tuple[PreparedEntry, ...], active: frozenset[str]) -> PublicationContext`.
- Rename `_preparation_readiness` to `_owner_readiness` and use it for both preparation and publication with operation-local results.
- Extend `_proof_matches(entry, row, *, readiness=None) -> bool`.
- Extend `publish_representation(candidate, active_root_ids, *, context: PublicationContext | None = None) -> bool`.
- Preserve the existing no-context helper contract for direct callers and tests.

- [ ] **Step 1: Add publication query and invalidation tests.**

Add `test_publication_shared_owners_reuses_metadata_and_content_rows` with 100 candidates and two owners.
Record selected content IDs and owner/root query groups with `CaptureQueriesContext`.

```python
assert published == 100
assert full_content_fetches == 1
assert metadata_queries_for_100_shared_contents == metadata_queries_for_one_shared_group
```

Compare fixed-input query shapes rather than a brittle total SQL count including savepoints.
Add tests for distinct/overlapping owners, a newly hot owner, changed payload observations, missing ancestry, and rollback.
Add `test_invalid_candidate_does_not_discard_valid_peers` with one stale candidate among three.

- [ ] **Step 2: Run the tests and confirm repeated reads fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_reads.py -q`.
Expected: the new publication reuse assertions fail on per-content queries.

- [ ] **Step 3: Build and consume one authoritative transaction-local context.**

Before `_publication_context`, call `_acquire_publication_write_guard(entries)` inside the same `transaction.atomic()`.
Implement this small private helper with a parameterized no-op UPDATE of the content primary key, restricted to the first candidate ID.
Use `SET id = id WHERE id = %s`; it acquires SQLite write ownership without changing owners, payloads, or lifecycle epochs.
A disappeared candidate still permits the bounded statement and is rejected by the later authoritative read.
Return immediately for an empty batch. Keep table/column names backend-quoted.
Test the actual SQL ordering: the write-first statement precedes the first context SELECT.
`in_atomic_block` alone does not establish SQLite write ownership.

Fetch admitted candidate contents once. Collect unique owners and resolve batched readiness under the current content-admission activity snapshot.
Use `_metadata_proof(owners, readiness)` to preserve the existing deterministic proof-record format.
Memoize repeated proof digests by the exact owners tuple within this context if profiling confirms repeated serialization remains material.
Use the same loaded row for observation comparison and UPDATE preparation.
Retain owners, metadata, identity, codec, dictionary, payload observation, and encoded-output checks.

The context is private to `publish_prepared_batch`'s current transaction and is never retained by a prepared candidate.
Do not reuse its active decisions in a later transaction.
Skip individual failed candidates. Preserve byte admission and oversized-record progress.

- [ ] **Step 4: Run maintenance regressions and commit.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_reads.py tests/test_content_maintenance.py tests/test_content_promotion.py tests/test_content_cold_cache.py tests/test_content_storage_admission.py -q`.
Expected: PASS. Context-assisted publication does not reload a content row or reconstruct owner metadata per candidate.

Subject: `perf(content): reuse authoritative maintenance publication metadata`.

### Task 3: Wake the Writer on Queue, Task, and Resource Changes

**Files:**
- Create: `src/twicc/providers/writer_signals.py`.
- Modify: `src/twicc/providers/db_writer.py` — queue construction, pending tasks, startup, dispatch, drain, cleanup.
- Modify: `src/twicc/providers/write_coordinator.py` — optional progress callback.
- Create: `tests/test_writer_notifications.py`.
- Modify: `tests/test_write_coordinator.py`, `tests/test_compute_shutdown.py`, `tests/test_compute_ipc.py`.

**Interfaces:**
- Add `WriterSignals(loop: asyncio.AbstractEventLoop)` with `dispatch: asyncio.Event` and `sources: asyncio.Event`.
- Methods: `notify_dispatch() -> None`, `notify_sources() -> None`, `close() -> None`.
- Notification methods accept calls from any thread. `notify_sources` sets both events on the captured loop.
- Add `NotifyingQueue(maxsize: int, *, on_put: Callable[[], None])`, preserving `queue.Queue` APIs.
- Add `NotifyingAsyncQueue(*, on_put: Callable[[], None])`, preserving `asyncio.Queue` APIs.
- Extend `WriterCoordinator(*, on_progress: Callable[[], None] | None = None)`.
- Store one `_writer_signals: WriterSignals | None` for the current writer lifetime.
- Task 4 consumes the `sources` event; Task 3 produces it after compute-ingress enqueue and source-application release.

- [ ] **Step 1: Add deterministic wakeup and drain tests.**

Add `test_enqueue_during_idle_check_is_not_lost` using events to stop the dispatcher between clearing and waiting.
Exercise direct `put` and `put_nowait` on thread and compute queues, including a real receiver-thread enqueue.
Exercise foreground and initialization async queues through their normal exposed APIs.

```python
assert accepted_jobs == completed_jobs
assert progress_before_any_poll_timeout
assert idle_dispatch_calls_remain_constant_without_notifications
```

Add tests for preparation success/failure, terminal notification completion, refused background admission, and foreground arrival.
Add `test_stop_with_blocked_worker_waits_without_spinning` and `test_old_lifetime_callback_cannot_wake_restarted_writer`.
Instrument scheduler waits and callback counts; use timeouts only as deadlock guards.

- [ ] **Step 2: Run tests and establish failures.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_writer_notifications.py tests/test_write_coordinator.py -q`.
Expected: notification APIs or event-driven behavior are absent on the baseline.

- [ ] **Step 3: Implement writer-lifetime signals and notifying queues.**

Notify only after `Queue.put` makes an item visible and releases its mutex.
Keep `ComputeIngressQueue`'s existing `_put` accepted-source accounting; subclass the notifying queue without double notification.
Async `put` delegates to the overridden `put_nowait`, so each successful insertion notifies once.
Preserve `_ForegroundJobQueue`'s demand token and initialization routing.
Capture signals in callbacks; never resolve a late callback through a replacement lifetime's module global.
`close` disables pending old-lifetime callbacks after final drain and coordinator quiescence.

Wake on preparation and terminal-notification task completion, whether successful or exceptional.
Coordinator release/cancellation transitions notify after scheduling updates that can permit progress.
Do not repurpose its drain-idle event as a general availability event.

- [ ] **Step 4: Replace normal idle polling and shutdown drain polling.**

Use this order on the owning event loop:

```text
clear dispatch event
attempt one authoritative drain turn
if progress: cooperative yield
else: wait for dispatch event
```

After stop, use the same clear/check/wait protocol until all accepted work and owned workers drain.
Do not wait repeatedly on the already-set stop event.
Retain the distinct error backoff and queue-capacity/receiver stop checks.
Stop requests explicitly notify dispatch.

- [ ] **Step 5: Run dispatch and ownership regressions; commit.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_writer_notifications.py tests/test_write_coordinator.py tests/test_compute_shutdown.py tests/test_compute_ipc.py tests/test_content_storage_admission.py tests/test_compute_executor.py -q`.
Expected: PASS, with foreground work admitted between background turns and no idle polling loop.

Subject: `perf(writer): dispatch accepted work through lifecycle-bound notifications`.

### Task 4: Enforce and Notify the Complete Compression Gate

**Files:**
- Modify: `src/twicc/providers/db_writer.py` — accepted-source inspection and release notifications.
- Modify: `src/twicc/content_maintenance_task.py` — complete periodic gate, waits, publication admission.
- Modify: `tests/test_content_maintenance_compute_gate.py`, `tests/test_writer_notifications.py`.

**Interfaces:**
- Add `has_pending_source_applications() -> bool` in `db_writer.py`, called on the owning event loop.
- Add `source_application_changes() -> asyncio.Event`, returning the current writer's `WriterSignals.sources` event.
- Use existing `ComputeIngressQueue.pending_sources` under its queue mutex and `_pending_compute` cleanup state.
- The gate is `compute_work.has_pending() or has_pending_source_applications()`.
- No additional accepted-source counter or durable gate table is introduced.

- [ ] **Step 1: Add gate completion and admission-race tests.**

Add `test_last_application_release_wakes_overdue_maintenance_without_provider_change`.
Set provider flags false, retain one accepted source, wait, release it, and assert periodic work starts.
Parameterize terminal outcomes: applied, superseded, failed, and abandoned.

```python
assert periodic_publications_while_application_pending == 0
assert maintenance_resumes_after_final_release
assert new_provider_state_notifications == 0
```

Add tests for queued-not-yet-applied work, cancellation with a blocked worker, and a prepared candidate crossing the gate.
Assert no-gain periodic metadata publication also waits.
Assert foreground plaintext demand still completes while the gate is closed.

- [ ] **Step 2: Run the new gate tests.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_compute_gate.py -q`.
Expected: provider-only gate fails the accepted-source and final-release cases.

- [ ] **Step 3: Integrate authoritative gate inspection and notifications.**

Expose pending-source inspection without scanning sessions or copying a complete pending inventory.
Keep exclusion active through actual worker drain and TEMP cleanup.
Define the pending predicate to ignore a `terminal` application only after its workers drain and TEMP cleanup completes.
After accepted-source release, blocked WebSocket/terminal notifications alone do not keep the compression gate closed.
`_collect_pending_notifications` still wakes dispatch when it removes that terminal application.
Test notifications blocked after cleanup: source release reopens maintenance without any provider state change.
Then complete and collect notifications; confirm no application count leaks or extra periodic publication.
Release accepted-source accounting exactly once on every terminal/drop path and notify `sources` through the captured writer signals.
Do not clear accounting while a preparation or publication worker still runs.

Maintenance clears its relevant events before reading the complete gate.
Wait on maintenance wake, provider changes, source-application changes, deadline, and stop as appropriate.
Check the complete gate again immediately after obtaining a periodic publication lease and before dispatching its worker.
If closed, release the lease and wait without changing cursor or candidate observations.
A previously admitted slice may finish; compute cannot validate concurrently under its lease.
Revalidate prepared candidates through Task 2's publication context when periodic publication resumes.

- [ ] **Step 4: Run gate and lifecycle regressions; commit.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_compute_gate.py tests/test_compute_work.py tests/test_compute_shutdown.py tests/test_content_storage_admission.py tests/test_writer_notifications.py -q`.
Expected: PASS; overdue waits are event-driven and no foreground restoration is held by the periodic gate.

Subject: `fix(content): gate periodic publication until accepted compute drains`.

### Task 5: Publish History Facts in Bounded Durable Slices

**Files:**
- Modify: `src/twicc/providers/history_facts.py` — staged page preparation/publication and readiness gate.
- Modify: `src/twicc/core/content_storage/extraction.py` — fact mode and history phases.
- Modify: `src/twicc/providers/db_writer.py` — progressive mode and removal of final-only stage preparation.
- Modify: `src/twicc/providers/compute_base.py` — completed progressive facts versus atomic compatibility finalization.
- Modify: `tests/test_history_fact_compute_apply.py`, `tests/test_history_facts.py`, `tests/test_content_extraction.py`.
- Create: `tests/test_history_fact_publication_slices.py`.

**Interfaces:**
- Extend `begin_compute_application` with the phase contract's `progressive_facts=False` keyword.
- Add operation fields `progressive_facts: bool` and `history_facts_published: bool = False`.
- Add `FactPage(cursor: int, positions: tuple[int, ...], bytes: int, done: bool)` in `history_facts.py`.
- Add `prepare_fact_delete_page(stage, after_id: int, *, row_budget=128, byte_budget=262144) -> FactPage`.
- Add `prepare_fact_insert_page(stage, after_order: int, *, row_budget=128, byte_budget=262144) -> FactPage`.
- Add `publish_fact_delete_page(stage, page: FactPage) -> None` and `publish_fact_insert_page(stage, page: FactPage) -> None`.
- Stage helpers validate owning connection/thread. Publication requires an existing guarded writer transaction.
- Delete cursor starts at 0. TEMP `source_order` insertion cursor starts at -1.

- [ ] **Step 1: Add partial-publication and compatibility tests.**

Add tests with more than two fact pages and read through `iter_resolver_items` between every durable slice.
For outdated sessions, assert raw fallback and unchanged outdated version until completion.

```python
assert not history_facts_are_current(session)
assert resolver_lines_during_partial_publication == expected_raw_source_lines
assert source_version_after_completed_final_commit == target_version
```

Add `test_current_compatibility_facts_remain_atomic`, inspecting readers before and after the compatibility transaction.
Add `test_progressive_facts_reject_current_before_first_delete` and version-gate checks before later slices.
Add tests for empty facts, duplicate tuples, missing input, both providers, rollback, cancellation, and TEMP affinity.
Add a foreground write between pages and a source append that makes the application superseded.

- [ ] **Step 2: Run and confirm missing phase behavior.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_history_fact_publication_slices.py tests/test_history_fact_compute_apply.py -q`.
Expected: bounded-page or current-session rejection assertions fail before implementation.

- [ ] **Step 3: Implement TEMP-backed delete and insert pages.**

During off-writer TEMP stage creation, define `source_order INTEGER PRIMARY KEY`.
Keep the existing business UNIQUE constraint and first-valid-duplicate behavior.
Explicit zero-based source positions remain valid; gaps from ignored duplicates remain valid.
Assert `EXPLAIN QUERY PLAN` for the actual published INSERT/SELECT interval uses indexed source-order range access.
Assert it does not scan the complete stage or build a temporary ORDER BY sort on every page.
Include 75,000 facts in this query-plan regression; row admission alone does not bound an unindexed scan.

Use source-session-scoped primary-key pages for deletion.
Prepare only positions and `LENGTH(CAST(data AS BLOB))` for byte admission, rather than decoding JSON.
Delete precisely the selected IDs with both session and ID predicates.
Read stage source-order positions and serialized sizes for insertion admission.
Insert the selected stage interval with parameterized `INSERT OR IGNORE ... SELECT`, preserving source order.
Use the last selected position to advance despite duplicates or gaps.
Allow one oversized fact to make progress, without retaining a second complete Python fact list.

Before each durable mutation, verify the source observation and outdated readiness in the current writer transaction.
Keep `publish_compute_history_facts` and `replace_history_facts` for atomic compatibility callers.
Keep the original unique tuple and first-valid-duplicate semantics.

- [ ] **Step 4: Integrate history phases and short final publication.**

In progressive mode, place `history_stage → history_delete → history_insert` after reconciliation.
Build the stage on the retained preparation worker before the first deletion.
Relax `prepare_compute_history_stage`'s final-only precondition to the explicit history-stage phase.
The first phase publication checks the observation; it does not move TEMP preparation under writer ownership.
Mark `history_facts_published` only after the last insertion slice commits, including an empty insertion.

Remove `_advance_pending_compute_body`'s old special final-stage preparation branch.
It now uses generic preparation/publication for all pre-final phases.
Keep done callbacks from Task 3 on every preparation and notification task.

In `_apply_session_complete_final`, skip durable fact replacement only for a live progressive operation with completed history phases.
Keep atomic replacement for detached synchronous compatibility messages.
Reject missing facts before any partial mutation, and never clear a current version to enter progressive mode.
On supersession or interruption, close TEMP state after worker drain without a monolithic durable cleanup.

- [ ] **Step 5: Verify readers, recovery, and completion signals; commit.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_history_fact_publication_slices.py tests/test_history_fact_compute_apply.py tests/test_history_facts.py tests/test_history_fact_resolution.py tests/test_compute_apply_signals.py tests/test_root_compute_completion.py tests/test_content_extraction.py -q`.
Expected: PASS. Progressive facts may survive a later failure, but current readiness and root completion may not.
Keep existing atomic rollback assertions on the compatibility path.
Audit all production `SessionHistoryFact` reads; raw fact iterators remain internal to readiness-aware readers.

Subject: `perf(history): publish rebuilt facts through guarded durable slices`.

### Task 6: Validate Storage Progressively and Shorten Final Readiness

**Files:**
- Create: `src/twicc/core/content_storage/validation.py`.
- Modify: `src/twicc/core/content_storage/types.py`, `extraction.py`, `state.py`.
- Modify: `src/twicc/providers/compute_base.py` — final guards and private operation completion.
- Create: `tests/test_content_validation_slices.py`.
- Modify: `tests/test_content_extraction.py`, `tests/test_content_reconciliation_changes.py`.

**Interfaces:**
- Add `StorageValidationState(upper_line: int, cursor: int, requires_plaintext: bool, complete: bool)` in `types.py`.
- Add `operation.validation: StorageValidationState | None`, initially None; initial validation cursor is -1.
- Add `prepare_validation_page(operation, *, row_budget=256, byte_budget=262144) -> PreparedComputeBatch` in `validation.py`.
- Add `publish_validation_page(operation, batch: PreparedComputeBatch) -> StorageValidationState` in `validation.py`.
- Add `complete_validated_storage(operation, final_msg: dict) -> None` in `validation.py`, called only inside the guarded final writer transaction.
- `publish_validation_page` returns proposed state. `publish_compute_batch` adopts it only after commit.
- Keep public `verify_plaintext_root` and detached compatibility completion independently authoritative.

- [ ] **Step 1: Add page coverage and no-final-scan tests.**

Add `test_progressive_finalization_does_not_scan_session_items` with more than two validation pages.
Capture SQL only during the final transaction, after page validation commits.

```python
assert session_wide_item_probes_during_final == 0
assert operation.validation.complete
assert operation.validation.cursor == observed_upper_line
assert certified_session_ids == {validated_root_id}
```

For an empty session, assert explicit complete zero-row coverage instead of requiring a physical last line.
Add parameterized tests for current/original slots, missing rows, NULL codec, invalid dictionaries, nonempty legacy content, and compressed hot references.
Add external-epoch-before-own-lifecycle, root/child certificate separation, stricter plaintext requirement, and final rollback tests.
Test that an arbitrary message or unrelated operation cannot authorize private completion.

After one validation page commits, inject each mutation before the next page:
reference assignment, owner binding, resume, ancestry change, cold-threshold crossing, and foreground plaintext promotion.
Assert source/ancestry/epoch incompatibility supersedes; a stricter compatible policy restarts reconciliation and coverage.
A compatible plaintext promotion can preserve coverage only when every recorded guard still matches.
A weaker policy cannot authorize an unvalidated stronger certificate.
Assert no stale prefix authorizes completion or a plaintext certificate in any case.

Add a whole-final-transaction SQL regression with a hot root, current storage version, invalid plaintext certificate, and changed timestamps.
Populate more than two validation pages and invalidate the certificate through an earlier child lifecycle mutation.
Include the final `update_lifecycle` call in the capture, rather than testing only `complete_validated_storage`.

- [ ] **Step 2: Run the new tests and confirm the full final scans fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_validation_slices.py tests/test_content_extraction.py -q`.
Expected: progressive no-final-scan and coverage assertions fail.

- [ ] **Step 3: Centralize bounded metadata validation.**

Define one metadata predicate for complete placement and the additional plaintext requirements.
Prepare a bounded page of item IDs and source lines, plus metadata sizes needed for the byte budget.
Do not select legacy content strings or payload bytes; use their needed metadata/length only.
During publication, re-read the selected items and joined content metadata after the source/root guard.
Scope the authoritative query to selected IDs so a LIMIT on invalid rows cannot conceal a full-session scan.
Verify the actual SQLite plan uses primary-key access for those IDs; a session predicate plus ORDER BY can otherwise choose a whole-session ordering-index scan.
Constrain publication access where necessary without adding a persistent index. Preserve authoritative session membership and selected-line comparisons.
Add an actual EXPLAIN regression at 100,000 and 200,000 source rows, for first, middle, and last selected-ID pages.
Preparation retains its existing session/line range index. Sort only the bounded selected rows when needed.
Validate both occupied reference slots, missing content rows, codec/dictionary shape, and empty legacy storage.
Never decompress or rehash payloads for metadata validation.

Advance coverage through the last selected item line; a separately guarded exhausted page marks completion through `upper_line`.
Allow gaps in line numbers. Completion does not require an item at exactly `upper_line`.
Cache only the existing operation-local coverage state, not all rows already examined.

- [ ] **Step 4: Integrate validation and enforce requirement changes.**

Add validation after progressive history insertion, before final.
Compatibility operations validate through their existing final path and do not acquire reusable operation evidence after their handle closes.

Compute the required representation from proposed lifecycle fields and current admitted activity.
On a stricter requirement with an otherwise compatible observation, discard weak coverage and reset its validation cursor.
Run bounded plaintext reconciliation before repeating validation; reuse the operation's existing reconciliation phase.
Do not discard unrelated completed metadata or fact phases.
Clear reconciliation's visited-representation cache when its previous eligibility no longer applies.
Track the return-to-validation transition explicitly so repeated reconciliation does not rerun historical stop or fact publication phases.
If source, ancestry, or epoch is incompatible, use the existing superseded result instead.

Every page verifies expected state before mutation. Own lifecycle changes settle expected state after commit.
Task 4's gate excludes every existing-content compressor until the retained application drains.
Foreground plaintext promotions remain admitted and preserve already accepted placement/plaintext properties.

- [ ] **Step 5: Add private guarded completion and remove progressive full scans.**

Require the exact live owning operation, its connection identity, complete phases, no pending batch, and compatible coverage.
Retain source write-first, ancestry, expected root identity/epoch, activity, and proposed/final policy checks.
Handle the final transaction's own lifecycle update through its guarded expected-epoch accounting.
For this progressive final path only, call `update_lifecycle` with `defer_plaintext_inspection=True`.
This defers its hidden `session_plaintext_metadata` scan to `complete_validated_storage`, which consumes the exact operation's paged coverage.
Retain lifecycle invalidation and promotion obligations; final plaintext readiness requires strong coverage for the proposed lifecycle state.
If this requirement is stronger than accepted coverage, return to bounded reconciliation/validation before entering final publication.
Keep default lifecycle inspection for unrelated callers and detached compatibility completion.
The deferred final call and private completion commit together; rollback publishes neither readiness nor completion.

Do not adopt an external epoch to preserve a previously validated prefix.

Publish `content_storage_version` and only the validated native root's own plaintext certificate.
Use bounded Session metadata queries and conditional updates; no complete SessionItem/Content probe occurs here.
Route progressive finalization to this private helper instead of `finish_storage_result`'s full scans.
Keep independent public verification and detached compatibility behavior intact.

- [ ] **Step 6: Run storage, concurrency, and search regressions; commit.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_validation_slices.py tests/test_content_extraction.py tests/test_content_reconciliation_changes.py tests/test_content_root_state.py tests/test_content_session_promotion.py tests/test_content_contains_gate.py tests/test_content_storage_admission.py -q`.
Expected: PASS; strict/public readers remain authoritative and progressive finalization has no session-wide probe.

Subject: `perf(content): finish compute from guarded paged storage validation`.

### Task 7: Verify Integrated Foreground Progress and Interrupted Recovery

**Files:**
- Create: `tests/test_compute_publication_integration.py`.
- Modify: `tests/test_content_maintenance_compute_gate.py`, `tests/test_compute_shutdown.py` only for shared integrated fixtures if needed.

**Interfaces:**
- Exercise the real writer queue, application executor, source message, and maintenance task from Tasks 1–6.
- Use existing provider compute version, `ComputeApplied`, and root completion contracts.
- Add no production API for testing.

- [ ] **Step 1: Add a deterministic end-to-end interleaving test.**

Construct an outdated root and two-depth children, over two lifecycle/fact/validation slices, and a shared content owned by another root.
Prepare a periodic compression candidate before compute arrives.
Use task barriers around preparation, publication, and terminal cleanup; let the actual writer perform the transitions.

```python
assert foreground_commit_before_background_next_slice
assert compressed_publications_during_compute == 0
assert partial_facts_never_used_as_complete_history
assert root_completion_and_statistics_enqueue_share_commit
assert maintenance_resumes_after_worker_cleanup
```

Parameterize both providers and applied/superseded outcomes.
Assert the gate stays closed after caller cancellation until the synchronous worker finishes.
Assert asyncio advances an unrelated heartbeat while a preparation is paused.

- [ ] **Step 2: Add restart and final-rollback scenarios.**

Interrupt after committed child stops, after fact deletion, after partial insertion, and after a validated page.
Close the first writer lifetime without deleting DB rows; start a new isolated test lifetime on the same test database.
Rebuild the outdated root using existing source fixtures; do not reset current children.
Verify exact final fact tuples, root/child costs readiness handoff, reference ownership, storage version, and root-only certificates.
Inject final metadata failure and verify it leaves source outdated and enqueues no completed-root statistics work.

- [ ] **Step 3: Run focused integrated tests, then the full backend suite.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_publication_integration.py tests/test_root_compute_completion.py tests/test_compute_apply_signals.py tests/test_compute_shutdown.py tests/test_content_maintenance_compute_gate.py -q`.
Expected: PASS without error logs, stranded accepted-source accounting, unresolved futures, or retained TEMP resources.

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q`.
Expected: PASS, with documented existing environment/integration skips only.
Do not reinterpret intentionally atomic compatibility tests as permission to expose partial current facts.

- [ ] **Step 4: Review and commit.**

Subject: `test(compute): cover sliced publication recovery and foreground precedence`.

### Task 8: Benchmark On-Disk Holds and Tune the New Budgets

**Files:**
- Temporary benchmark driver: `/tmp/twicc-publication-bench-<run-id>/bench.py`.
- Temporary SQLite files and JSON results: the same temporary directory.
- Modify only the new phase budget constants if measurement requires it.
- Create: `docs/plans/2026-10-16-compute-publication-and-content-maintenance-validation.md` for retained results.

**Interfaces:**
- Driver CLI: `bench.py --output-dir PATH --variant baseline|candidate --scenario lifecycle|dispatch|maintenance|facts|validation|mixed`.
- Driver creates synthetic on-disk databases from the relevant model table/index schemas and deterministic data.
- It redirects the benchmark process's Django connection before any model query and asserts that the resolved database is under `--output-dir`.
- It never opens a running instance's database for writes or changes its durability settings.
- Output JSON records environment, SQLite settings, data seed, budgets, input sizes, SQL counts, phase timings, writer holds, foreground latency, watcher ingestion latency, and event-loop lag.
- Baseline code comes from `git archive 307cd0ba` in a temporary directory; use the existing worktree environment to run it, without editable-install or dependency changes.

- [ ] **Step 1: Build and verify the isolated benchmark driver.**

Set `TWICC_DATA_DIR` to its temporary data directory before importing TwiCC/Django.
Assert the actual imported source path for baseline and candidate variants.
Use identical schemas, indexes, input data, WAL mode, synchronous mode, and page size for both variants.
Prepare baseline model schemas inside the isolated process rather than running migrations against any server.
Warm up imports/dictionaries separately and report first-use results separately.
Use `time.perf_counter` and existing publication diagnostics; do not infer holds from total wall time.

- [ ] **Step 2: Run the scenario matrix.**

| Scenario | Required inputs | Required observations |
|---|---|---|
| Lifecycle | 10/100/400/1,000 agents; unchanged, changed, mixed; populated references | UPDATE/SELECT counts, cumulative work, max hold, foreground wait |
| Dispatch | 10,000 preparations of 1–10 ms; idle and foreground load | Dispatch gap, idle wake count, CPU, foreground p99 |
| Maintenance | 100 contents; shared/overlapping/distinct owners; 333 KB/8 MB/one oversized payload | Metadata reads, payload reads, commit and hold duration |
| Facts | 1k/10k/40k/75k facts; 128/256/1,000 row candidates; 256 KiB byte limit | DELETE/INSERT/commit separately, max hold, foreground wait |
| Validation | 1k/10k/100k/200k items; current/original; cold/warm connection caches | Per-page reads, cumulative time, final hold, foreground wait |
| Mixed | Lifecycle + large facts + validation + queued foreground writes + real watcher ingestion on an independent source | App latency, watcher commit/notification latency, event-loop lag, no compression overlap, no one-second changed-phase holds |

For watcher ingestion, append deterministic valid provider lines to a temporary independent session source.
Use the real watcher-to-writer path, without modifying the source being computed.
Record append completion, watcher acceptance, writer commit, and outgoing update notification timestamps.
Report append-to-commit and append-to-notification distributions separately from synthetic foreground-job latency.
Stub external delivery only after recording the actual outgoing application notification; do not measure phone/WebSocket network behavior.

Run at least five measured repetitions after warmup for normal cases.
Use enough publication samples to report meaningful percentiles and include sample counts.
Report reopened-connection cache tests separately; do not label them OS cold-cache tests without actual evidence.
Keep output comparisons phase-specific; overlapping wall-time gaps are not recoverable-time guarantees.

- [ ] **Step 3: Tune only the new budgets and rerun affected checks.**

Starting values: lifecycle 64 rows / 256 KiB; facts 128 rows / 256 KiB; validation 256 rows / 256 KiB.
Reduce or increase them only from measured foreground latency and holds, preserving oversized-record progress.
Leave existing 2,000-item metadata and 256-row / 8 MiB content batches unchanged.
If a different unchanged phase dominates, report it separately rather than expanding this implementation.
Rerun the owning task's tests after each changed budget.

- [ ] **Step 4: Record measured results and limits.**

Write the validation document in English, linking the spec and plan.
Separate baseline measurements, candidate measurements, hypotheses, and unmeasured cases.
Include p50/p95/p99/max, threshold counts, data volumes, and exact commands/settings.
Record the tradeoff if cumulative compute slows while foreground latency improves.
Record any remaining acceptance failure as an unresolved result; do not hide it behind an average speedup.

A full run against a fresh worktree copy follows only after the user's operational instruction.
Use devctl for any authorized reset/start workflow; never migrate manually to start a server.
Until that run happens, label full-dataset end-to-end gains unmeasured.

- [ ] **Step 5: Review the integrated diff and commit benchmark-backed tuning.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && git diff --check`.
Expected: no whitespace errors.
Run the focused test groups affected by any final tuning, then repeat the full backend suite only if changes justify it.
Perform an independent whole-change review against the spec before handoff.

Subject: `perf(compute): validate and tune bounded publication budgets`.

## Plan Self-Review and Review Record

Before execution, verify each spec section maps to Tasks 1–8.
Verify every cross-task signature above matches its producer and consumer.
The exact slice budgets are measured decisions; the readiness, ownership, and foreground-priority contracts are fixed.
This document authorizes no implementation, server restart, or data reset by itself.

### Independent Adversarial Review Outcome

Three independent reviewers examined application phases, notifications/maintenance, and complete spec coverage.
The first round identified a hidden final lifecycle scan, an unindexed TEMP cursor, and missing validation details.
Corrections add the progressive final lifecycle deferral, indexed TEMP source-order access, write-first maintenance publication, and explicit terminal gate behavior.
Tests now cover mutations after accepted validation pages and real watcher ingestion latency.
The second round found a missing wrapper signature for the lifecycle control keyword.
The third targeted round verified that signature, its forwarding contract, and its regression test.
All reviewers report no remaining blocker in their examined scope after these corrections.

This is a document review, not implementation validation. No source code, migration, server, or database changes occur during this task.
Slice budgets and measured performance remain implementation-time benchmark decisions.
