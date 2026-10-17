# Session-local content eligibility and bounded maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compress each session according to its own lifecycle and make maintenance bounded, observable, and inexpensive for unchanged data.

**Architecture:** Separate session-local content decisions from structural native-root lookup. Use a pass-owned metadata cache with committed invalidation, prepare only necessary representations outside the writer, and publish candidate metadata in explicit SQLite write transactions. Extend targeted restoration and recovery to all session depths.

**Tech Stack:** Python 3.13+, Django 6, SQLite WAL, asyncio, existing writer/admission coordinator, python-zstandard, pytest-django.

**Spec:** [Session-local content eligibility and bounded maintenance](2026-10-17-session-content-cold-policy-and-maintenance-spec.md).

**Status:** Historical implementation plan. Implementation is delivered on the feature branch.
The original planning checklist and review record remain as written; they do not represent current execution progress.
See the [validation report](2026-10-17-session-content-cold-policy-and-maintenance-validation.md) for completed checks and remaining limits.

## Global Constraints

- Cold thresholds: 15 days for archived/hidden sessions and 30 days otherwise.
- Use the maximum non-null value of `created_at`, `last_started_at`, `last_updated_at`, and `last_stopped_at`.
- A currently active session is hot; unknown lifecycle information cannot authorize compression.
- Each root/subagent uses its own lifecycle and activity, at every depth.
- Every owner must be cold before compressing shared content.
- Maintenance budgets remain 100 rows and 8 MiB, with one oversized content admitted alone.
- Preparation owns no writer lease; foreground work wins every new background grant.
- No artificial primary-key UPDATE, no added SessionItem FK index, and no payload reread for publication validation.
- Session metadata load once per pass unless invalidated; invalidation follows committed changes before writer release.
- First periodic deadline: 30 minutes after startup, gated by compute/source application completion.
- Next deadline: 24 hours after pass completion. Compute pauses retain the pass cursor.
- A reproducible writer hold >= 1 second blocks acceptance.
- All new documents, comments, names, and logs use English. All planning files live in `docs/plans/`.
- Existing uncommitted documents are preserved. This plan authorizes no service stop/start, data reset, or migration execution.

## Review Focus

1. A cold child under a hot/outdated root must compress and pass final validation: Tasks 2 and 5.
2. A committed child cold-to-hot change followed by process loss must recover under a cold root: Task 6.
3. A cache fill racing with a committed lifecycle change must not reinstall the old value: Task 3.
4. One changed owner in three candidates must not discard the other two or reread their payloads: Task 4.
5. Small candidate publication against millions of unindexed SessionItem FK rows must not scan those rows: Tasks 1 and 8.

## Execution and test conventions

Execute in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
Prefix each shell invocation with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression &&`.
Every Python/Django command uses `TWICC_DATA_DIR=$PWD`.
Check that the resolved database path is inside this worktree before a data-dependent diagnostic.
Tests use isolated pytest databases; mutating benchmarks use separate throwaway databases.

Below, `TEST <paths/options>` means this exact command after the cwd prefix:

```bash
TWICC_DATA_DIR=$PWD uv run pytest <paths/options>
```

Each task follows RED -> implementation -> GREEN -> focused review -> commit.
Run its new tests before editing production code and record the expected behavioral failure.
Use existing helpers from `tests/content_storage_helpers.py`, and local factories following the adjacent tests.
Test sketches name assertions; the implementer supplies fixture setup with the stated dates, ownership, and activity.
Do not weaken existing tests merely because their names mention roots: distinguish structural invariants from superseded content-policy assertions.
Commit with a Conventional Commit subject, descriptive body, and the current model's Co-Authored-By trailer.

## File and responsibility map

| Files | Responsibility |
|---|---|
| New `core/content_storage/transactions.py` | Scoped outer SQLite IMMEDIATE transaction using Django transaction management |
| `policy.py`, new `session_policy.py`, `types.py`, `models.py` | Own-session policy, grouped decisions, immutable decision type, model property |
| `cold_cache.py`, new `maintenance_cache.py`, `lifecycle.py` | Committed invalidation and pass-owned session metadata |
| `content_maintenance_task.py`, new `maintenance_metrics.py` | Candidate selection/publication, recovery, scheduling, progress and holds |
| `extraction.py`, `validation.py`, `ingestion.py`, `references.py` | Compute/ingestion placement and reference consistency |
| `state.py` | Structural ancestry, targeted plaintext readiness, restoration snapshots and search gate |
| `providers/compute_base.py`, `providers/db_writer.py` | Existing compute/lifecycle integration and worker publication boundaries |
| `agent/base_manager.py`, `providers/claude_code/agent/manager.py` | Existing runtime activity/start/stop hooks |
| `cli/__init__.py`, `cli/session.py` | Session-local contains behavior and descriptions |
| New `scripts/benchmark_content_maintenance.py` | Isolated synthetic and copied-DB measurements |

Paths in this table are relative to `src/twicc/`, except explicit `scripts/` paths.
Keep tree lookup available for structural/source consistency; do not implement own-session policy by changing scratch ancestry behavior.

## Task 1: Replace artificial write acquisition with a scoped SQLite transaction

**Files:** Create `src/twicc/core/content_storage/transactions.py` and `tests/test_content_write_transaction.py`.
Modify `src/twicc/content_maintenance_task.py` and `tests/test_content_maintenance_reads.py`.

**Interfaces:**
- Produce `immediate_content_transaction() -> ContextManager[None]`.
- Retain `publish_prepared_batch(entries, active) -> int` as a transaction-owned primitive.
- `_publish(entries, active) -> int` enters the new context before calling that primitive.

- [ ] **Write failing tests** for the real publication boundary, with FK enforcement enabled:

```python
assert first_transaction_statement == 'BEGIN IMMEDIATE'
assert no_primary_key_self_update_is_executed
assert no_sessionitem_scan_in_acquisition_plan
assert connection.transaction_mode == previous_mode_after_success_or_error
assert committed_callback_runs_once and rollback_callback_does_not_run
```

Also test exception rollback, absence of UPDATE for empty entries, and rejection of unsafe nested use.
Use `TransactionTestCase`/`transaction=True` for real BEGIN tests, rather than pytest's ambient transaction.

- [ ] **Run RED:** `TEST tests/test_content_write_transaction.py tests/test_content_maintenance_reads.py -q`.
Expect the old self-update/DEFERRED behavior to fail the new assertions.

- [ ] **Implement the scoped context.** Require autocommit and no outer atomic block at entry.
Ensure the connection is open, temporarily select its SQLite `transaction_mode='IMMEDIATE'`, and enter Django `transaction.atomic()`.
Restore the prior mode in `finally`, including BEGIN failure and rollback.
Reject nested entry with `TransactionManagementError` before validation or writes; do not fake IMMEDIATE with a savepoint.
Production `_publish` is the outer transaction boundary; inner existing atomic blocks remain ordinary savepoints.
Remove `_acquire_publication_write_guard` and its call.
Return immediately for empty entries before requesting this transaction.
Keep the existing cancellation-draining `_apply` resource boundary.

- [ ] **Run GREEN:** the Task 1 command, plus `TEST tests/test_content_storage_admission.py tests/test_content_maintenance.py -q`.
Assert that an independent connection can read during the write transaction and cannot interleave a competing write.
Use a disposable disk database for this connection test.

- [ ] **Commit:** `fix(storage): acquire maintenance write transactions without fk scans`.

## Task 2: Define own-session policy without changing structural ancestry

**Files:** Modify `src/twicc/core/models.py` and `policy.py`, `types.py`, `state.py`, `cold_cache.py` in `src/twicc/core/content_storage/`.
Create `src/twicc/core/content_storage/session_policy.py`.
Tests: `tests/test_content_cold_policy.py`, `tests/test_content_cold_cache.py`, `tests/test_content_root_state.py`.

**Interfaces:**
- Add `SessionContentDecision(session_id: str, is_cold: bool, resolved: bool, active: bool)` to `types.py`.
- Add `resolve_content_decisions(session_ids: Iterable[str], now: datetime, active_ids: frozenset[str]) -> dict[str, SessionContentDecision]` in `session_policy.py`.
- Add `content_decision_from_fields(session_id: str, fields: Mapping | None, now: datetime, active_ids: frozenset[str]) -> SessionContentDecision` in the same module.
- Keep `cold_from_fields()` as the common threshold calculation.
- Keep root lookup functions for structural snapshots and scratch behavior. Audit callers rather than globally replacing root IDs with session IDs.

- [ ] **Write failing tests:** root hot/child 31 days old; root cold/child active; depth-three child with independent dates.
Test 15-day and 30-day boundaries at minus/plus one microsecond, all-null dates, missing session, and each timestamp being the latest.

```python
assert content_decisions[old_child.id].is_cold
assert not content_decisions[active_child.id].is_cold
assert old_child.is_cold_for_content == content_decisions[old_child.id].is_cold
assert scratch_decision_before == scratch_decision_after
```

- [ ] **Run RED:** `TEST tests/test_content_cold_policy.py tests/test_content_cold_cache.py tests/test_content_root_state.py -q`.

- [ ] **Implement grouped session decisions** with `TREE_PAGE_SIZE=128` bounded metadata queries.
Load only each requested session's own `COLD_FIELDS`; activity uses exact session ID membership.
No ancestor lookup, parent readiness, or provider compute version participates in content eligibility.
Change the content model property to inspect its own explicitly loaded fields without implicit SQL.
Keep the scratch property using its existing ancestry policy.
Add a caller inventory to the task review: every existing `resolve_root_decision(s)` use is classified as structural or content eligibility.

- [ ] **Run GREEN:** the Task 2 command and `TEST tests/test_session_dirs_cleanup.py -q`.

- [ ] **Commit:** `refactor(storage): evaluate content cold state per session`.

## Task 3: Add a pass-owned invalidatable session cache

**Files:** Create `src/twicc/core/content_storage/maintenance_cache.py`, `tests/test_content_maintenance_cache.py`.
Modify `cold_cache.py`, `lifecycle.py`, and the existing lifecycle mutation integration in `ingestion.py`, `providers/compute_base.py`, and agent managers.

**Interfaces:**
- `MaintenanceSessionCache()` owns one pass's loaded session metadata and statistics.
- `cache.decisions(session_ids: Iterable[str], now: datetime, active_ids: frozenset[str]) -> dict[str, SessionContentDecision]`.
- `cache.close() -> None` unregisters/relinquishes pass state.
- Reuse `schedule_cold_invalidation(session_ids, mappings=False)` as the committed mutation notification seam.
- Cache values contain `id` and `COLD_FIELDS`; policy no longer needs root identity/version/epoch readiness reads.

- [ ] **Write failing tests** using captured SQL and controlled load barriers:

```python
assert metadata_ids_loaded == set(all_requested_owners)
assert metadata_queries_after_warm_repeated_pages == 0
assert reloaded_ids_after_one_change == {changed_session.id}
assert untouched_session_entry_is_retained
assert invalidated_inflight_value_is_not_installed
```

Add transaction commit/rollback tests, threshold passage with warm cache, grouped misses >128 IDs, and pass-close cleanup.
Test the same session through watcher lifecycle, archived/hidden update, compute lifecycle, and start/resume/stop entry points.

- [ ] **Run RED:** `TEST tests/test_content_maintenance_cache.py tests/test_content_lifecycle.py -q`.

- [ ] **Implement reuse of the existing invalidation synchronization**, including its protection for invalidation during a fill.
Do not add persisted revisions or counters to Session or the statistics queue.
Invalidate pass entries for affected session IDs after successful lifecycle commits, before writer release.
Root mapping invalidation can retain its existing structural cache behavior, but it must not evict unrelated own-session pass entries.
Pass entries do not cache absence indefinitely; missing owners cannot authorize compression and may appear later.
Runtime activity is checked from the existing admission-protected snapshot each decision/publication, independent of cached dates.
Compression representation updates and structural-root epoch changes do not invalidate these lifecycle-only entries.

- [ ] **Run GREEN:** Task 3 tests plus `TEST tests/test_content_cold_cache.py tests/test_content_history_lifecycle.py -q`.
Verify one load per unchanged ID across both preparation and publication contexts.

- [ ] **Commit:** `perf(storage): reuse session metadata across maintenance batches`.

## Task 4: Prepare and publish only useful content changes

**Files:** Modify `src/twicc/content_maintenance_task.py`, `src/twicc/core/content_storage/types.py` if shared records are needed.
Tests: `tests/test_content_maintenance_reads.py`, `tests/test_content_maintenance.py`, new cache tests.

**Interfaces:**
- Extend `prepare_compression_batch(after_id, row_budget=100, byte_budget=8*1024*1024, *, session_cache)`; return a named `CompressionPage`.
- `CompressionPage` carries `cursor: int`, `entries: tuple[PreparedEntry, ...]`, `done: bool`, and `counts: CompressionPageCounts`.
- `CompressionPageCounts` is an immutable record of integer `scanned`, `already_compressed`, `no_gain_skips`, `ineligible_owners`, `prepared`, `payload_bytes_loaded`, `candidate_raw_bytes`, and `candidate_stored_bytes`.
- Page counts describe only that page; Task 7 accumulates them once when preparation completes, including empty candidate pages.
- `publish_prepared_batch(entries, active, *, session_cache) -> PublicationResult`.
- `PublicationResult` carries `changed`, `no_gain_recorded`, `already_conformant`, `stale`, and `bytes_saved` integer counts.
- `PreparedEntry` retains candidate and metadata observation only; eliminate payload-bearing `RepresentationProof` and owner metadata digest from this path.

- [ ] **Write failing tests** for a 100-row page containing 97 conformant rows and three eligible plaintext candidates.

```python
assert payload_ids_read_during_preparation == candidate_ids
assert publication_metadata_ids == candidate_ids
assert publication_payload_selects == []
assert unchanged_owner_sql_reads_during_publication == []
assert publish_changed == 2  # Third candidate acquires a hot owner.
```

Also test no candidates -> no lease/BEGIN, same dictionary compressed skip, no-gain policy skip, one oversized row, and concurrent already-conformant publication -> no UPDATE.

- [ ] **Run RED:** `TEST tests/test_content_maintenance_reads.py tests/test_content_maintenance.py -q`.

- [ ] **Implement early metadata filtering before owner resolution.** Fetch only payloads selected after cache decisions.
Reuse process-cached dictionaries and existing codec worker scheduling/GIL yields.
Capture observation fields: content ID, sha256, raw_size, codec, dictionary ID, owner list, compression-attempt policy and no-gain state.
Publication uses `.values(...)`/explicit fields that omit payload, plus existing immutable encoded candidate bytes.
Check logical identity and representation metadata; skip stale or already-conformant candidates individually.
If owners change, resolve the new set through the cache and accept only when all current owners are cold.
For newly invalidated session entries, grouped reload is allowed under publication; unchanged entries trigger no owner SQL.
Keep owner data changes separate from candidate payload updates and never overwrite an owner list from a preparation snapshot.
Record newly determined no-gain state even when plaintext bytes stay unchanged.
Use Task 1's explicit transaction boundary and update only changed columns.

- [ ] **Run GREEN:** Task 4 tests plus `TEST tests/test_content_codec.py tests/test_content_dictionary_cache.py -q`.
Ensure two valid candidates complete when the third becomes hot; no global retry and no new decode-after-compress check.

- [ ] **Commit:** `perf(storage): publish only eligible maintenance candidates`.

## Task 5: Integrate session-local compute, ingestion, and plaintext readiness

**Files:** Modify `extraction.py`, `validation.py`, `ingestion.py`, `references.py`, `state.py`, and `lifecycle.py` in `src/twicc/core/content_storage/`.
Also modify `src/twicc/providers/compute_base.py`, `src/twicc/providers/db_writer.py`, and `src/twicc/cli/__init__.py`.
Tests: `test_content_initial_compression.py`, `test_content_extraction.py`, `test_content_validation_slices.py`, `test_content_ingestion.py`, `test_content_reconciliation_changes.py`, `test_content_contains_gate.py`, `test_content_session_promotion.py`.

**Interfaces:**
- `_cold_placement(session_id)` uses session-local pending final lifecycle fields for that result.
- `require_plaintext_search(session_id)` retains its public error contract but gates the requested session, not its root's cold state.
- Existing snapshot/reference guards keep structural source identity separate from `SessionContentDecision`.
- Keep root certificates scoped to their own references; child readiness uses `session_plaintext_metadata(session_id) -> bool`.
- Search returns the existing structural snapshot type, while eligibility and plaintext checks target the requested session.

- [ ] **Write failing integration tests**, parameterized for Claude and Codex:

```python
assert child.compute_version == current_child_version
assert root.compute_version != current_root_version
assert child_exclusive_content.codec == 'zstd'
assert child_final_validation_accepts_compressed_storage
assert hot_child_contains_works_under_cold_root
assert cold_child_contains_is_rejected_under_hot_root
assert complete_lifecycle_ingestion_content.codec == 'zstd'
assert incomplete_lifecycle_ingestion_content.codec == 'plain'
```

Test existing shared hot owner -> plaintext, no-gain retention, initial ingestion with complete/incomplete lifecycle, and a newly added hot owner during compute.
Test shared content owned by a cold child and its cold, not-yet-extracted/outdated root: storage readiness must not become a cold-policy condition.

- [ ] **Run RED:** `TEST tests/test_content_initial_compression.py tests/test_content_extraction.py tests/test_content_validation_slices.py tests/test_content_ingestion.py tests/test_content_reconciliation_changes.py tests/test_content_contains_gate.py tests/test_content_session_promotion.py -q`.

- [ ] **Implement placement and validation with own-session final fields.** Remove root-current eligibility gates.
Update initialization and ingestion's current shared-root decision calls to the new content decision helper.
Audit `_owners_cold()` explicitly: remove owner extraction-version/incarnation prerequisites that merely delay compression of otherwise known cold owners.
Keep missing owner/lifecycle conservative; retain identity guards only where they protect actual source/reference publication.
Retain structural observation/incarnation checks that protect source application, rather than treating them as cold-policy checks.
Keep the existing root certificate as a fast path for the root's own references only.
For a subagent, use `session_plaintext_metadata(session_id)` after its own-session policy check, in the same caller-owned read snapshot.
Do not repurpose the native-root `content_state_epoch` as a per-child epoch or create new readiness columns.
Targeted child metadata validation reads both content references and never scans a full tree's payloads.
Keep content ownership bookkeeping from both current and original FK assignments.
Audit certificate carry/invalidation on reference mutation so a shared representation change cannot leave a hot owner's search falsely ready.
Update CLI contains descriptions from "hot native root" to "hot requested session" and retain complete-plaintext verification.

- [ ] **Run GREEN:** Task 5 tests plus `TEST tests/test_content_references.py tests/test_content_consumers.py tests/test_content_readers.py -q`.
Use a mixed-depth tree with root hot, one cold child, one active child, and one cold grandchild.
Verify root finalization neither decompresses those cold descendants nor forces their recompute.

- [ ] **Commit:** `fix(storage): compress subagents independently during compute`.

## Task 6: Restore and recover individual hot sessions after interruption

**Files:** Modify `content_maintenance_task.py`, `core/content_storage/state.py`, `core/content_storage/lifecycle.py`, and targeted start/resume consumers if signatures change.
Tests: `tests/test_content_session_promotion.py`, `tests/test_content_lifecycle.py`, `tests/test_content_maintenance.py`.

**Interfaces:**
- Replace root-only `pending_root_page` with `pending_session_page(after_id: str | None = None) -> list[str]`, bounded to `TREE_PAGE_SIZE=128`.
- Page extracted sessions by ID at all depths, without a root-type or root-certificate predicate. Keep the durable extraction identity/storage-version criteria.
- Replace `recovery_promotion_targets` root restriction with session-local filtering using `MaintenanceSessionCache`.
- Keep `request_root_promotion(session_id, priority=...)` callable and update its semantics/docs to a targeted session. Retain the legacy name in this change.
- Existing `PromotionBatch`, cursor, and storage snapshots remain bounded; their eligibility uses the requested session.

- [ ] **Write failing interruption tests:** child archived with latest date 20 days ago, compressed exclusive/shared contents, cold root and cold sibling.
Commit unarchive, discard every in-memory wakeup/target, and start the actual recovery path.

```python
assert recovered_target_ids == {child.id}
assert exclusive_content.codec == shared_content.codec == 'plain'
assert cold_sibling_exclusive_content.codec == 'zstd'
```

Repeat at depth three and interrupt halfway through restoration; restarting must finish it.
Include an extracted hot child whose own epoch/certificate fields are NULL, under a cold root with a current root certificate.
Assert already-plaintext references trigger no payload reads or updates.

- [ ] **Run RED:** `TEST tests/test_content_session_promotion.py tests/test_content_lifecycle.py tests/test_content_maintenance.py -q`.

- [ ] **Implement durable discovery without a new queue.** Enumerate extracted sessions at all depths from their committed storage identity/version.
Do not require a child `content_state_epoch` or `plaintext_verified_epoch`: these fields retain their structural/root semantics.
Do not exclude children because a root certificate is current, absent, or cold.
Filter session-local hot candidates with grouped cached metadata, then use targeted metadata-only reference validation to identify required restoration.
Run recovery discovery and its reference probes in short read transactions outside writer ownership. Publication retains its own guarded validation.
A conformant hot session may require a scan of its own references; measure this metadata-only cost explicitly rather than claiming zero reference work.
Do not create a new dependence on successful initial sync or a root compute version.
Restore only that session's compressed references; shared content restoration is permitted and required for the hot owner.
Use targeted cache decisions and metadata-only publication validation, as in Task 4, without reloading payloads already prepared.
Reuse existing startup and periodic recovery, writer precedence, cursor continuation, and cancellation drain.
Initialize the existing extraction identity/storage version for every completed extracted session, including children.
Missing child root-only readiness fields do not prevent recovery. A committed lifecycle change plus compressed references reconstructs the obligation.
Use `immediate_content_transaction()` as the outermost transaction in `apply_promotion_batch()` before its snapshot and candidate reads.
Adapt callers so this boundary owns no outer atomic block; preserve inner savepoints only.
Add a disk-backed two-connection restoration test proving IMMEDIATE acquisition precedes authorization reads, with rollback and callback coverage.

- [ ] **Run GREEN:** Task 6 tests plus `TEST tests/test_content_storage_admission.py tests/test_content_maintenance_compute_gate.py -q`.
Verify a root resume does not enqueue its entire cold subtree.

- [ ] **Commit:** `fix(storage): recover hot subagent restoration after restart`.

## Task 7: Add pass observability and retain scheduling invariants

**Files:** Create `src/twicc/core/content_storage/maintenance_metrics.py`, `tests/test_content_maintenance_metrics.py`.
Modify `src/twicc/content_maintenance_task.py`, `src/twicc/providers/write_coordinator.py`, `tests/test_content_maintenance_compute_gate.py`, and `tests/test_write_coordinator.py`.

**Interfaces:**
- `MaintenanceMetrics(operation_id: str, operation_kind: str)` with `record_page(page)`, `record_publication(result, *, wait_ms: float, transaction_ms: float, hold_ms: float)`, and `emit(event: str) -> None`.
- Operation kinds: `periodic_compression` and `plaintext_restoration`.
- Cache statistics from Task 3 and page/publication records from Task 4 feed this object.
- Add `WriterCoordinator.observe_foreground_wait(callback: Callable[[float], None]) -> Callable[[], None]`; the returned function unsubscribes.
- Add `MaintenanceMetrics.record_foreground_wait(wait_ms: float) -> None`. Subscribe for the operation lifetime and unsubscribe in `finally`.
- Coordinator samples measure request insertion to grant for foreground requests containing WRITER. Count each grant once, including immediate zero-wait grants; exclude cancelled requests and nested lease reuse.
- Samples belong to every operation active at grant time. Overlapping summaries describe overlapping observation windows and must not be added together.
- Label this metric `foreground_coordinator_wait_ms`; it excludes earlier job-queue delay. Task 8 separately measures end-to-end request-to-commit latency.

- [ ] **Write failing tests** with fake monotonic time and captured logs:

```python
assert event_names == ['start', 'progress', 'complete']
assert summary['scanned'] == 100 and summary['prepared'] == 3
assert summary['changed'] == 2 and summary['stale'] == 1
assert next_deadline == completion_time + 86400
assert resumed_cursor == paused_cursor
```

Test read-only pages still report progress, pass failure/cancellation emits its outcome, and no exact percentage is invented.
Test separate resource wait vs writer hold and no logging-induced payload/table scan.
Test foreground grant counts, cancellation, nested reuse, overlapping observers, unsubscription, and observer failure isolation.
Test a failed periodic pass schedules its next attempt at failure time +86400, rather than spinning on an expired deadline.

- [ ] **Run RED:** `TEST tests/test_content_maintenance_metrics.py tests/test_content_maintenance_compute_gate.py tests/test_write_coordinator.py -q`.

- [ ] **Implement start/progress/outcome events.** Emit progress at most every 10 seconds and at completion, with batch/cursor and all Spec section 8 counters.
Create/close one cache for the whole periodic pass, including its recovery phase; keep it through compute pauses.
Standalone restoration operations own and release their cache when their work completes.
Keep bounded histogram counters for holds, with upper-bin percentile estimates explicitly labelled, plus exact maximum and counts >=100/250/500/1000 ms.
Measure resource wait, synchronous transaction time, preparation, and actual writer ownership independently.
Correlate long-hold warnings with operation ID and batch IDs/counts, not payload contents.
Before emitting a summary, finish all admitted publication work; report cancellation rather than completion for an interrupted pass.
Retain INITIAL_DELAY=1800 and INTERVAL=86400, computed after successful pass completion; gate each new periodic publication when compute resumes.
Retain the existing failure backoff of INTERVAL from failure time, with a failure event. Do not mark failure as completion or immediately retry the whole pass.
Cancellation during shutdown starts no new pass; the next process performs startup recovery.
The observer is synchronous, bounded, exception-isolated, and performs no SQL or per-grant logging; it does not change coordinator priority or grant order.

- [ ] **Run GREEN:** Task 7 tests plus `TEST tests/test_content_maintenance_reads.py tests/test_content_maintenance.py -q`.

- [ ] **Commit:** `feat(storage): expose maintenance progress and publication costs`.

## Task 8: End-to-end validation and disk-backed benchmark evidence

**Files:** Create `tests/test_content_maintenance_end_to_end.py`, `scripts/benchmark_content_maintenance.py`.
Create the execution report `docs/plans/2026-10-17-session-content-cold-policy-and-maintenance-validation.md` during implementation.
Reuse existing public dictionary fixtures; benchmark output must not include private content values.

**Interfaces:**
- Script CLI: `--scenario {conformant,compress,foreground,acquisition} --output PATH --work-dir PATH [--source-db PATH]`.
- `--source-db` is a read-only backup source; the script modifies only a new destination inside `--work-dir`.
- Require an empty/new work directory, reject source=destination and paths resolving to the running DB, and use SQLite backup rather than copying an open DB file.
- The script never imports a running instance's DB settings implicitly and never starts/stops devctl.

- [ ] **Write the failing end-to-end test** for compute followed by maintenance, both providers, hot root/cold descendants and shared/no-gain contents.

```python
assert pass_summary['prepared'] == 0
assert pass_summary['changed'] == 0
assert periodic_writer_grants == 0
assert content_payload_selects == 0
```

Use unchanged lifecycle/ownership and dictionaries, and finish targeted restoration before measuring the conformant compression pass.
Also add a compressible case with one changed owner and foreground writes admitted between publication slices.

- [ ] **Run RED/GREEN:** `TEST tests/test_content_maintenance_end_to_end.py -q`.
Resolve integration defects in their owning task; do not change the expected zero-work result to hide missing compute compression.

- [ ] **Build and run isolated benchmark scenarios**:

| Scenario | Required workload and comparisons |
|---|---|
| Conformant | At least 100,000 contents with repeated owners; mixed zstd and recorded no-gain plaintext; zero useful publication |
| Compress | Eligible plaintext at roughly 1 KiB, 64 KiB, 1 MiB, and 16 MiB; include mixed owners and shared rows |
| Foreground | Repeated small writer jobs during compression; measure request-to-commit latency as well as maintenance holds |
| Acquisition | Real two unindexed SessionItem FKs, 100,000 then 1,000,000 unrelated children; verify no FK child scan in the publication acquisition plan |

In the conformant scenario, report recovery separately from compression selection.
Include many hot subagents with already-plaintext references and one large hot session.
Measure recovery reference probes, time, payload reads, and writer grants; recovery discovery must use no writer grants or payload reads.

Run each synthetic scenario at least three times on disk and distinguish warm-cache runs from the first run.
Report total pass time, metadata/cache SQL counts, payload bytes loaded, useful writes, holds, foreground wait and commit latency.
Capture CPU time and process I/O deltas over the benchmark interval; do not report lifetime CPU/I/O as maintenance cost.
On an isolated copy of the worktree DB, measure a conformant full metadata pass and selected actual candidate cases.
Existing misplaced cold contents in that copy form a separate workload; they are not evidence for the fresh compute -> zero-work invariant.

- [ ] **Run complete checks:**

```bash
TWICC_DATA_DIR=$PWD uv run pytest -q
uvx ruff check src/twicc/core/content_storage src/twicc/content_maintenance_task.py tests/test_content_*.py scripts/benchmark_content_maintenance.py
git diff --check
```

Use an explicit list of additional changed Python files for ruff when outside these paths.
Record baseline-only failures separately; fix every introduced failure.
Do not run `migrate`, reset the current worktree DB, or restart devctl as part of these checks.

- [ ] **Write the validation report**, separating measurements, query-plan guarantees, remaining hypotheses, and unexplained outliers.
Any reproducible writer hold >=1 second blocks acceptance; smaller percentiles and foreground latency still require review.
Run an independent whole-change review against the spec and this plan before requesting a real-instance reset/run.

- [ ] **Commit:** `test(storage): validate session-local maintenance and writer latency`.

## Completion checklist

- [ ] Every Spec section 3–9 requirement maps to Tasks 1–8 and a recorded check.
- [ ] No content cold decision depends on parent activity or root compute readiness.
- [ ] Cache load/invalidation coverage is documented for every retained field and mutation entry point.
- [ ] Recovery works for hot children after committed transitions and process loss.
- [ ] The conformant pass performs no compression publication or payload reload.
- [ ] Publication acquisition has no SessionItem FK scan and every write remains under the existing writer.
- [ ] Disk-backed results include foreground writes, not just HTTP reads.
- [ ] Explain every outlier; reject reproducible >=1-second holds.
- [ ] Obtain the user's instruction before resetting/restarting their running worktree for a new full compute run.

## Planning review status

Reviewed in two rounds by two independent internal reviewers. Both approve; no unresolved blocker remains.

Review corrections specify restoration transaction ownership, foreground-wait observation, failure backoff, child readiness without new epochs, all-depth durable recovery, and extraction-owner eligibility.
Tests and benchmarks cover these corrections, including recovery of already-plaintext hot children.

The plan is ready for user review before implementation.
No implementation, service operation, database mutation, or commit is performed while writing this plan.
