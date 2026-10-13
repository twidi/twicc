# Deferred Compute Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repeated background metadata aggregate repairs while preserving exact final aggregates and durable restart recovery.

**Architecture:** Store unresolved activity keys and direct-parent IDs in a small transactional journal. Background metadata chunks write source and obligations together; successful final publication repairs and settles them together. Writer-lifetime discovery repairs eligible abandoned obligations through ordinary background grants.

**Tech Stack:** Python 3.13+, Django 6, SQLite WAL, asyncio, existing dedicated writer/application workers, pytest and pytest-django.

**Spec:** `docs/plans/2026-10-13-deferred-compute-activity-design.md`

## Global Constraints

- Temporary incorrect caches and shared activity amounts are permitted during compute.
- The reference is the existing `Session.recalculate_costs()` and `PeriodicActivity.recalculate()` behavior.
- Retain its provider partition, UTC dates, daily dates, and Monday weekly dates.
- Retain project and global scopes.
- Raw SQLite cost sums must match the existing exact-repair reference, including fractional legacy values.
- The journal inventory has no correctness cap.
- The existing 4,096-key limit applies only to optional preparation proof.
- Foreground priority applies at grants; it does not preempt an admitted transaction.
- Recovery uses the ordinary writer worker only.
- No transaction, iterator, or read snapshot survives a recovery step.
- Excluded: SQL `GROUP BY` overhaul, huge-table indexes, new dependencies, content/cold-storage changes, scratch tools, API changes, and changelog changes.
- No installation, server restart, production migration, or merge belongs to this design task.
- Use the existing `/home/twidi/dev/twicc-poc/.worktrees/content-writer-priority` worktree and `fix/content-writer-priority` branch.
- Prefix shell commands with `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority &&`.
- Set `TWICC_DATA_DIR=$PWD` for every Python, Django, or pytest command.
- Use `uv run`, never `--active` or `uv pip`.
- Preserve other changes and use English for written artifacts.

## Review Focus

1. A restart after removal of the last historical timestamp must still repair the absent old bucket. Task 3 tests persisted adoption.
2. A session ID reused after deletion must retain its new caches and journal during old-incarnation recovery. Task 2 tests identity isolation.
3. A failing first recovery row must not starve eligible later rows or prevent shutdown. Task 6 tests bounded sweeps and retry closure.
4. A pending application can appear after recovery discovery; admission must exclude its target through terminal cleanup. Task 6 tests this race.
5. A final journal merge must not invalidate its own valid preparation proof; real source mutations must invalidate it. Task 4 tests both cases.

---

## Baseline and execution gates

Baseline product commit: `b072478b6239e3ba1f5cefda42a12ea868ab2c2c`.
The reviewed design commit follows that baseline. No product change accompanies this plan.
The user approves the design and requests reviewed implementation through subagents.
The written plan receives independent adversarial review and user review before execution.

Use one fresh implementer per task. Review spec compliance and code quality independently before advancing.
Review corrections on their actual diff. Run a whole-branch review after all tasks and validation.
Inherit model and effort for every subagent. Do not recreate the completed writer-priority task workspace.
Use `update_plan` throughout execution. Do not combine unrelated task commits.

Every task below includes RED, implementation, GREEN, review, and commit steps.
Commit subjects use Conventional Commits. Include a descriptive body and the runtime model trailer.
The examples use `Co-Authored-By: Codex GPT-6.1 Sol <codex@openai.com>`; verify the runtime model before committing.
Use a temporary body file for multiline commit messages. Do not interpolate unescaped shell text.

## File map

| File | Responsibility |
|---|---|
| `src/twicc/core/models.py` | New small `ComputeAggregateRepair` model only |
| `src/twicc/core/migrations/0157_compute_aggregate_repair.py` | Create journal table, unique identity; no history migration |
| `src/twicc/providers/compute_repair_journal.py` (new) | Typed keys, transaction-owned merge/load/conditional settlement and bounded discovery reads |
| `src/twicc/providers/compute_repair.py` (new) | Exact journal repair, retained-parent repair, supported-mutation preservation |
| `src/twicc/providers/compute_repair_recovery.py` (new) | Worker-independent scheduler state, finite sweep, deduplication, backoff |
| `src/twicc/providers/live_aggregates.py` | Existing exact reference; delete absent-project scoped buckets safely |
| `src/twicc/providers/compute_base.py` | Explicit deferred chunk mode, final settlement, live preservation |
| `src/twicc/core/content_storage/extraction.py` | Operation journal adoption, producer for deferred mode |
| `src/twicc/providers/activity_preparation.py` | Additional durable journal fences; existing physical/version proof retained |
| `src/twicc/providers/db_writer.py` | Recovery lifetime/admission and raw ingestion/deletion preservation |
| `src/twicc/core/services/session_visibility.py` | Before/after visibility preservation |
| `src/twicc/providers/codex/rollout_migration.py` | Before-deletion replacement evidence and generation invalidation |
| New `tests/test_compute_repair_*.py` | Journal, exact recovery, mutations, and scheduler contracts |
| Existing activity/continuation/live tests | Compatibility and final correctness regressions |
| `tests/test_deferred_compute_activity_benchmark.py` (new) | Opt-in isolated comparison assertions |
| `scripts/benchmark_deferred_compute_activity.py` (new) | Shared isolated baseline/final workload and measurement entrypoint |

Do not restructure the large existing writer/compute modules beyond the integration hooks.
The migration number is `0157` against inspected leaf `0156_content_storage_schema`.
Check the leaf again immediately before creating it; adjust only if another approved migration appears.

## Shared typed vocabulary

Define these new interfaces in Task 1; later tasks consume the same names.
`ActivityKey` preserves existing runtime tuples: `(type[DailyActivity] | type[WeeklyActivity], str | None, str, date)`.
`SerializedActivityKey` is the JSON list `["day" | "week", str | None, str, ISO-date-string]`.
`RepairObservation` is an immutable `NamedTuple(journal_id: int, session_id: str, incarnation: str, generation: int)`.
`RepairSnapshot` is an immutable `NamedTuple(observation: RepairObservation, activity_keys: frozenset[ActivityKey], parent_ids: frozenset[str])`.
All DB helpers are synchronous. Call them on the existing appropriate worker.
Never move ORM objects, QuerySets, iterators, transactions, or connection proof across workers.

### Task 1: Transactional journal and identity fences

**Files:**
- Modify: `src/twicc/core/models.py`
- Create: `src/twicc/core/migrations/0157_compute_aggregate_repair.py`
- Create: `src/twicc/providers/compute_repair_journal.py`
- Create: `tests/test_compute_repair_journal.py`

**Interfaces:**
- Produces the shared typed vocabulary above.
- Produces `load_compute_repair(session_id: str, incarnation: str) -> RepairSnapshot | None`.
- Produces `merge_compute_repair(session_id: str, incarnation: str, *, activity_keys: Iterable[ActivityKey] = (), parent_ids: Iterable[str] = (), source_changed: bool, create: bool = True) -> RepairSnapshot | None`.
- Produces `require_compute_repair(observation: RepairObservation) -> RepairSnapshot`; raise new `RepairObservationChanged(RuntimeError)` on identity/generation mismatch.
- Produces `settle_compute_repair(observation: RepairObservation) -> None`; conditional delete, raise `RepairObservationChanged` if it deletes no matching row.
- Produces `load_session_repairs(session_ids: Iterable[str]) -> tuple[RepairSnapshot, ...]`, including old incarnations.
- Produces `read_compute_repair_page(after_id: int, upper_id: int, *, limit: int = 64) -> tuple[RepairSnapshot, ...]` and `compute_repair_upper_id() -> int`.
- All merge/require/settle helpers require an active caller-owned transaction. Load/page helpers fully materialize their results.

- [ ] **Step 1: Write failing journal tests.**

```python
# Use real sessions only when needed; journal identities have no FKs.
def test_merge_retains_keys_and_bumps_generation_for_same_day_source_change():
    assert second.activity_keys == first.activity_keys
    assert second.observation.generation == first.observation.generation + 1

def test_noop_final_merge_keeps_observation():
    assert merged.observation == original.observation

def test_conditional_clear_cannot_delete_new_generation_or_recreated_row():
    with pytest.raises(RepairObservationChanged):
        settle_compute_repair(old.observation)
    assert load_compute_repair(session_id, incarnation) == newer

def test_rollback_keeps_source_and_journal_unchanged():
    assert stored_source == before_source
    assert stored_snapshot == before_snapshot

def test_journal_has_no_correctness_cap_or_session_foreign_key():
    assert len(snapshot.activity_keys) > 4096
    assert load_compute_repair(missing_session_id, incarnation) is not None
```

Add serialization round-trip assertions for null scope, Monday week, ISO date, both providers, duplicate keys, and deterministic ordering.
Reject malformed kind/date shape before writing. Empty obligations still retain a source-changed generation.
Assert same session ID with different incarnation produces separate rows.
Inspect migration SQL in an isolated migration test: one new table, no `core_sessionitem` rebuild or history query.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_journal.py -q`
Expected: FAIL because the new model/helpers do not exist.

- [ ] **Step 3: Implement the journal and schema.**

Model fields: `id` BigAutoField, `session_id` CharField(max_length=255), `incarnation` CharField(max_length=36),
`generation` PositiveBigIntegerField(default=0), `activity_keys` JSONField(default=list), `parent_ids` JSONField(default=list).
UniqueConstraint `(session_id, incarnation)`, name `compute_repair_source_identity`.
No FK and no SessionItem changes. The unique index supports presence/adoption; the primary key supports sweeps.
Merge deduplicated unions inside the writer transaction. Increment on every `source_changed=True`, or any newly added obligation.
A no-op with `source_changed=False` retains identity/generation. A source-changed new row starts generation 1.
Creation of an empty final inventory is permitted; do not treat no keys as no source evidence.

- [ ] **Step 4: Run GREEN and schema checks.**

Run the Task 1 command. Expected: PASS.
Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations --check --dry-run --settings=twicc.settings`
Expected: no model changes detected. Do not manually migrate any running instance.

- [ ] **Step 5: Review Task 1, correct findings, and commit.**

Subject: `feat(activity): add durable compute repair journal`.
Body: Explain source/journal atomicity, incarnation/generation fencing, small-table-only migration, and tests.
Stage only the four Task 1 files. Add the verified runtime model trailer.

### Task 2: Exact repair and orphan settlement

**Files:**
- Create: `src/twicc/providers/compute_repair.py`
- Modify: `src/twicc/providers/live_aggregates.py:107` exact activity branch
- Create: `tests/test_compute_repair_exact.py`

**Interfaces:**
- Consumes Task 1 snapshots and conditional settlement.
- Produces `repair_retained_parents(parent_ids: Iterable[str]) -> None`; exact raw-item costs, no message recount for parents.
- Produces `repair_activity_key(key: ActivityKey) -> None`; delegate to existing model `recalculate`, or delete obsolete scoped rows when the project no longer exists.
- Produces `repair_compute_observation(observation: RepairObservation) -> str`, returning `"repaired"`, `"stale_target"`, or `"changed"`.
- Produces `settle_deleted_repairs(snapshots: Sequence[RepairSnapshot]) -> None` for caller-owned deletion transactions.
- `repair_compute_observation` owns one transaction. Other helpers require the caller's active transaction.

- [ ] **Step 1: Write failing exact repair tests.**

```python
def test_current_target_recovery_matches_independent_reference():
    assert actual_session_costs == reference_raw_session_costs
    assert actual_bucket_rows == reference_bucket_rows
    assert load_compute_repair(session_id, incarnation) is None

def test_old_incarnation_recovery_never_changes_replacement_caches():
    assert replacement_values_after == replacement_values_before
    assert load_compute_repair(session_id, new_incarnation) == new_snapshot
    assert load_compute_repair(session_id, old_incarnation) is None

def test_orphan_repairs_global_and_retained_parent_without_project_fk():
    assert obsolete_scoped_bucket_does_not_exist
    assert global_rows == reference_global_rows
    assert retained_parent.total_cost == reference_parent_raw_sum

def test_stale_target_waits_for_metadata_compute():
    assert result == 'stale_target'
    assert journal_after == journal_before
```

Also test target message count, direct-child cache repair, stale sibling cache ignored for parent raw sums,
missing parent, hidden/subagent cost inclusion, raw fractional SQLite values, and rollback after a bucket failure.
Oracle: capture actual state, run independent existing full `recalculate_costs`/`PeriodicActivity.recalculate`, compare raw SQL and ORM totals.
Do not use the new repair helper as its own oracle.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_exact.py -q`
Expected: FAIL for absent new exact helpers.

- [ ] **Step 3: Implement exact transaction settlement.**

First SQL in recovery is a conditional no-op journal UPDATE matching observation identity/generation, to acquire write ownership.
Then reload/fence the snapshot. A changed observation returns `"changed"` without settlement.
Current same-incarnation target must have its provider's current compute version; otherwise return `"stale_target"`.
Use existing `apply_contribution_changes(..., repair=True, extra_activity_keys=complete_union)` for current target exact caches/history.
Repair retained old parents explicitly with `Session.recalculate_costs()` and existing nullable/positive-total saves.
Orphans/different incarnations repair journal buckets and retained existing parents only; never update replacement caches.
Delete the matching journal only after exact repair. Any conditional-settlement failure rolls back all repair writes.
Use `repair_activity_key` in the existing helper's exact branch too, so final/deletion fallback handles missing old projects.
Import that helper lazily inside the exact branch: `compute_repair.py` imports the existing aggregate helper, so a new module-level reverse import creates a cycle.
Preserve existing current-version delta behavior and raw SUM arithmetic. Do not reconstruct costs from quantized contributions.

- [ ] **Step 4: Run GREEN and existing exact tests.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_journal.py tests/test_compute_repair_exact.py tests/test_live_sync_aggregates.py -q`
Expected: PASS; existing live delta and fractional precision behavior remain.

- [ ] **Step 5: Review Task 2, correct findings, and commit.**

Subject: `feat(activity): repair durable aggregate obligations exactly`.
Body: Explain orphan identity isolation, retained-parent repair, missing-project cleanup, atomic settlement, and reference tests.

### Task 3: Deferred background chunks and atomic final adoption

**Files:**
- Modify: `src/twicc/providers/compute_base.py:3698,3760,4069`
- Modify: `src/twicc/core/content_storage/extraction.py:444,523,624,669`
- Modify: `tests/test_compute_activity_obligations.py`
- Modify: `tests/test_live_sync_aggregates.py`
- Create: `tests/test_compute_repair_publication.py`

**Interfaces:**
- Extend `BaseSessionCompute.apply_session_items_chunk` with keyword-only `defer_aggregate_repair: bool = False`, `prospective_session: SessionContribution | None = None`, and `extra_activity_keys: Iterable[ActivityKey] = ()`; keep all existing positional parameters and return values.
- Produces `record_compute_repair(session: Session, before_items: Sequence[ItemContribution], after_items: Sequence[ItemContribution], *, before_session: SessionContribution, prospective_session: SessionContribution | None, extra_activity_keys: Iterable[ActivityKey], source_changed: bool) -> RepairSnapshot` in `compute_repair.py`.
- Extend `ComputeStorageOperation` with `repair_snapshot: RepairSnapshot | None = None`.
- Runtime producer: `publish_compute_batch` explicitly passes deferred mode, operation-prospective contribution, and worker/creation keys.
- Compatibility producers retain default exact chunk behavior. Direct standalone callers do not silently change semantics.
- Final consumer: `_apply_session_complete_final` adopts SQL journal independently of any operation/private snapshot.

- [ ] **Step 1: Write failing publication tests.**

```python
def test_runtime_metadata_chunks_do_not_repair_aggregates(monkeypatch):
    assert aggregate_recalculate_calls_during_metadata == []
    assert full_history_contribution_reads_during_metadata == []
    assert target.compute_version == old_version
    assert journal_snapshot.activity_keys >= removed_and_new_keys

def test_restart_after_removed_timestamp_adopts_durable_union():
    assert removed_old_buckets_match_reference_after_new_final
    assert current_buckets_match_reference
    assert no_target_journal_remains

def test_direct_chunk_default_preserves_existing_exact_behavior():
    assert parent_and_shared_bucket_are_exact_before_failed_final

def test_final_failure_rolls_back_version_caches_and_journal_settlement():
    assert version_after == version_before
    assert journal_after == committed_chunk_snapshot

def test_direct_final_uninitialized_target_uses_valid_persisted_incarnation():
    assert result.outcome == 'applied'
    assert target.storage_incarnation not in (None, '', 'None')
    assert final_state_matches_reference
    assert no_target_journal_remains
```

Add tests for zero-item final, prospective/old creation scopes, >4096 keys, failed source write, supersession after append,
shared unfinished peer source, direct parent and child final caches, and conditional settlement mismatch.
Retain the existing direct-chunk old invariant test; add a distinct deferred-runtime assertion.
Replace metadata rollback injection through `DailyActivity.recalculate` with an injected source/journal failure in runtime tests.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_publication.py -q`
Expected: FAIL because runtime chunks still perform aggregate repair and have no durable adoption.

- [ ] **Step 3: Implement guarded runtime deferral and final settlement.**

Retain the existing write-first source revision guard, metadata/content budgets, transaction nesting, and postcommit private mirrors.
Inside deferred chunks record before/after bounded contributions and seed worker/prospective keys before commit; skip `apply_contribution_changes` entirely.
Keep the `compute_metadata_aggregates` diagnostic phase with no recalculation in deferred mode; direct mode retains its existing phase.
Journal generation advances even if only cost/kind changes occur inside an existing day.
Content representation-only storage phases do not change aggregate generation.

At final, capture the original session contribution before applying session fields, including parent/project/provider/type/hidden/creation.
Merge before/current/prospective/worker keys in the same transaction; enumerate full historical old/new scopes once through the exact helper.
Use `source_changed=False` for obligation-only final merges. Actual final item/source overrides use source-changed generation before proof admission.
Capture/fence the final observation before known own cached-count writes; repair target/children/current parent and retained old parents.
Feed the complete SQL union to exact activity repair, then conditionally settle the captured journal in the final transaction.
Existing lifecycle/facts/link/readiness/version writes remain atomic, even if their code order saves version earlier inside that transaction.
No source-complete success/ACK occurs before that transaction commits.
Until Task 4 installs durable proof fences, Task 3 deliberately passes `repaired_activity_buckets=None` for journal-backed final repair.
This keeps Task 3 independently correct; Task 4 restores optional shortcut admission.
Standalone full compute adapters also adopt existing journals, even without an operation-owned snapshot.
When a direct final caller has no operation, observed incarnation, or stored incarnation, resolve/initialize the target
inside its existing guarded transaction with `initialize_compute_session`, then use the persisted valid incarnation.
Never create a journal with a blank incarnation or `str(None)`. Do not weaken source revision guards.
Add a direct-final regression for an uninitialized session; retain exact standalone behavior when no journal exists.

- [ ] **Step 4: Run GREEN and continuation compatibility.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_publication.py tests/test_compute_activity_obligations.py tests/test_live_sync_aggregates.py tests/test_compute_continuation.py -q`
Expected: PASS; provider terminal and revision guard behavior remain unchanged.

- [ ] **Step 5: Review Task 3, correct findings, and commit.**

Subject: `perf(activity): defer background chunk aggregate repairs`.
Body: Explain runtime-only deferral, durable old-key adoption, atomic final reference restoration, and compatibility tests.

### Task 4: Fence optional preparation with durable observations

**Files:**
- Modify: `src/twicc/providers/activity_preparation.py:24,107,189,264`
- Modify: `src/twicc/providers/compute_base.py:4069` final policy handoff
- Modify: `tests/test_activity_preparation.py`
- Modify: `tests/test_activity_final_admission.py`

**Interfaces:**
- Extend `ActivityPreparation` with `repair_observation: RepairObservation | None`.
- Extend `activity_final_policy` with keyword-only `repair_snapshot: RepairSnapshot | None = None`.
- `begin_activity_preparation` adopts the durable union and stores the observation in state and operation.
- `publish_activity_key` calls `require_compute_repair` after its existing write-first source guard.
- Final producer passes Task 3's pre-own-final-write snapshot to `activity_final_policy`.

- [ ] **Step 1: Write failing preparation tests.**

```python
def test_noop_final_journal_merge_preserves_prepared_noncreation_hits():
    assert admitted_keys == prepared_keys - old_and_new_creation_keys

def test_same_day_source_generation_change_disables_preparation():
    assert admitted_keys is None
    assert final_bucket_rows == reference_rows

def test_enlarged_final_union_forces_complete_exact_fallback():
    assert admitted_keys is None
    assert every_durable_key_is_repaired
```

Extend existing foreign-commit, connection replacement, cancellation, callback failure, capacity and missing-membership cases.
Assert partial bucket preparation never deletes any journal obligation.
Assert own final cached count/lifecycle writes do not falsely look like a foreign commit.
Creation buckets still use final cached message count and raw fractional cost.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_activity_preparation.py tests/test_activity_final_admission.py -q`
Expected: new durable generation tests FAIL.

- [ ] **Step 3: Implement journal adoption and proof fences.**

Keep the same pinned physical connection, fixed data_version, 256-row timestamp pages and 4096 optional proof limit.
Add required durable keys without truncation; capacity disables proof, never journal retention.
Validate journal identity/generation under each bucket's guard and before final own source/cache writes.
An actual final source update or enlarged union rejects proof; an identical obligation merge does not bump generation.
Retain metadata stability and creation exclusions. Foreign commits still invalidate the entire proof.
Exact fallback always receives the SQL union and `repaired_activity_buckets=None`.

- [ ] **Step 4: Run GREEN.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_activity_preparation.py tests/test_activity_final_admission.py tests/test_compute_repair_publication.py tests/test_compute_activity_obligations.py -q`
Expected: PASS, including all old proof invalidation cases.

- [ ] **Step 5: Review Task 4, correct findings, and commit.**

Subject: `fix(activity): fence prepared repairs with journal generations`.
Body: Explain no-op merge admission, actual-source invalidation, creation exclusions, fixed connection/version contract, and tests.

### Task 5: Preserve obligations at supported foreground mutation boundaries

**Files:**
- Modify: `src/twicc/providers/compute_repair.py`
- Modify: `src/twicc/providers/compute_base.py:4166,4752`
- Modify: `src/twicc/providers/db_writer.py:2818,2877,2955`
- Modify: `src/twicc/core/services/session_visibility.py:114`
- Modify: `src/twicc/providers/codex/rollout_migration.py:353,398,419`
- Create: `tests/test_compute_repair_mutations.py`

**Interfaces:**
- Produces `preserve_pending_repair(session_id: str, incarnation: str, *, before_sessions: Sequence[SessionContribution], after_sessions: Sequence[SessionContribution], before_items: Sequence[ItemContribution] = (), after_items: Sequence[ItemContribution] = (), historical_timestamps: Iterable[datetime | None] = (), source_changed: bool = True) -> RepairSnapshot | None`.
- It uses Task 1 `merge_compute_repair(create=False)`. With no matching journal it performs one indexed presence probe and no history scan or journal creation.
- Historical timestamps come from the mutation caller's existing old-history scan or a timestamp-only scan only when historical scopes change and a pending journal exists.
- Deletion consumes `load_session_repairs` and `settle_deleted_repairs` from Tasks 1–2.

- [ ] **Step 1: Write failing mutation regressions.**

```python
def test_live_append_invalidates_pending_journal_generation():
    assert generation_after > generation_before
    assert old_final_outcome == 'superseded'
    assert subsequent_final_matches_reference

def test_visibility_and_scope_changes_keep_old_and_new_historical_keys():
    assert pending_keys >= old_scope_keys | new_scope_keys
    assert final_rows == reference_rows

def test_replacement_retains_previously_removed_timestamp_keys():
    assert previously_removed_keys <= replacement_journal.activity_keys
    assert replacement_final_matches_reference

def test_delete_descendants_settles_every_deleted_incarnation_atomically():
    assert deleted_target_journals == ()
    assert retained_parent_and_global_buckets_match_reference
```

Test adopted initial-sync creation and raw append bump generation despite zero timestamp/kind/cost metadata.
Test stale-flag-only unchanged behavior, parent relation old/new retention, null creation transitions,
root/child/tree deletion, missing target with journal, provider mismatch with an existing target,
old incarnation beside new target, deletion rollback, and Codex raw replacement chunk/final tracking generation changes.
Assert current live append without a journal retains no full-history SUM/COUNT behavior.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_mutations.py -q`
Expected: missing preservation hooks FAIL.

- [ ] **Step 3: Implement narrow before/after preservation hooks.**

Call preservation inside existing source transactions before evidence disappears.
Live sync uses its already captured `before_session` and bounded inserted rows before existing `apply_contribution_changes`.
Initial-sync create adoption and update preserve existing matching journals when items/tracking source changes.
New sessions with no journal retain current exact behavior; do not implement the optional initial-sync skip.
Visibility retains before/after historical scopes, then performs existing exact foreground repair; it does not settle the whole journal.
Compute final handles project/provider/type/parent/creation changes through Task 3; scope changes need old history timestamp keys.
Codex `_begin_replace_codex_history` captures removed contributions and pending unions before deletion; keep journals through replacement.
Its raw insert chunks and finish tracking write invalidate existing observations without creating fresh journals unnecessarily.

Delete gathers matching existing roots and descendants, then all their journals before deleting source.
Union journal keys into existing deletion repair and repair retained parents, then atomically settle the complete deleted inventory.
If requested IDs have no source row but have journals, repair those orphan journals rather than returning solely on empty source IDs.
An existing source with a different provider stays untouched. For missing source, orphan repair touches only retained keys/parents,
never a replacement target; provider-scoped deletion does not infer journal ownership from one serialized key.
Retain normal return value as actual deleted IDs only. Do not report orphan repair as a session deletion.
Recheck source absence/identity in the transaction before journal-only settlement.

- [ ] **Step 4: Run GREEN and relevant existing mutation suites.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_mutations.py tests/test_live_sync_aggregates.py tests/test_compute_repair_exact.py -q`
Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_lifecycle.py tests/test_content_ingestion.py tests/test_content_initial_compression.py tests/test_codex_rollout_migration.py tests/test_live_sync_rollback.py -q`
Expected: PASS; normal live deltas and supported deletion/provider restrictions remain.

- [ ] **Step 5: Review Task 5, correct findings, and commit.**

Subject: `fix(activity): preserve repair obligations across source mutations`.
Body: Explain bounded presence probes, old evidence preservation, replacement/deletion behavior, and compatibility coverage.

### Task 6: Fair writer-lifetime recovery and finite shutdown

**Files:**
- Create: `src/twicc/providers/compute_repair_recovery.py`
- Modify: `src/twicc/providers/db_writer.py:657,709,1800,2309,2355`
- Create: `tests/test_compute_repair_recovery.py`
- Modify: `tests/test_compute_continuation.py`

**Interfaces:**
- Produces `RepairRecoveryScheduler` with `__init__(clock: Callable[[], float] = monotonic)`,
  `begin_sweep(upper_id: int) -> None`,
  `page_request() -> tuple[int, int, int] | None` (after ID, fixed upper ID, read limit),
  `observe_page(rows: tuple[RepairSnapshot, ...], *, excluded_session_ids: frozenset[str]) -> None`,
  `next_candidate(excluded_session_ids: frozenset[str]) -> RepairObservation | None`,
  `defer_unadmitted(observation: RepairObservation) -> None`,
  `mark_admitted(observation: RepairObservation) -> None`,
  `settle_attempt(observation: RepairObservation, outcome: str, *, failed: bool = False) -> None`, and `close() -> None`.
- Scheduler `page_request` returns `None` when full/closed/between sweeps. Its read limit is `min(64, 16 - queued_count)`.
- `next_candidate` removes excluded queued observations through `defer_unadmitted`, then peeks an eligible candidate without consuming.
- `defer_unadmitted` removes only private unadmitted state, retains SQL evidence, and allows later-sweep rediscovery.
- `mark_admitted` consumes only after a grant and pending-target recheck. A denied grant retains its candidate.
- `begin_sweep` fixes upper ID and resets cursor to 0. `observe_page` advances to the last observed row ID; a short page ends that sweep.
- Scheduler owns only plain observations/state. Constants: `RECOVERY_PAGE_ROWS = 64`, `RECOVERY_QUEUE_LIMIT = 16`,
  `RECOVERY_POLL_SECONDS = 1.0`, `RECOVERY_RETRY_BASE_SECONDS = 1.0`, `RECOVERY_RETRY_MAX_SECONDS = 60.0`.
- Produces writer helpers `_discover_compute_repairs() -> None` and `_advance_compute_repair() -> bool`.
- Discovery reads via `run_compute_sync`; publication uses `_background_turn` and `run_compute_sync(repair_compute_observation, observation)`.
- Only `db_writer` reads `_pending_compute` for exclusion; scheduler receives excluded IDs and never imports writer state.

- [ ] **Step 1: Write failing scheduler and writer integration tests.**

```python
def test_failed_first_row_does_not_starve_later_eligible_row():
    assert later_row_is_repaired
    assert failed_row_journal_survives
    assert failed_retry_time >= clock_now + 1.0

def test_pending_target_admission_recheck_skips_discovered_recovery():
    assert recovery_sql_calls_for_pending_target == []
    assert pending_target_journal_survives

def test_sixteen_excluded_incarnations_release_capacity_for_row_seventeen():
    assert eligible_row_seventeen_is_repaired_while_target_remains_pending
    assert excluded_old_incarnation_journal_count == 16
    assert maximum_private_queue_size <= 16

def test_many_failed_earlier_identities_do_not_block_later_distinct_row():
    assert earlier_failed_identity_count >= 4096
    assert later_distinct_row_is_repaired_while_earlier_retries_remain_pending
    assert no_failed_identity_retries_before_its_deadline
    assert private_queue_size <= 16

def test_recovery_runs_with_zero_stale_targets_and_after_provider_completion():
    assert orphan_journal_is_settled
    assert metadata_compute_calls == []

def test_shutdown_closes_discovery_before_final_drain():
    assert discovery_reads_after_close == []
    assert retry_enqueues_after_close == []
    assert admitted_attempt_count <= 1
    assert unadmitted_journals_survive
```

Use a fake monotonic clock for scheduler tests and barriers for writer races, not timing sleeps.
For the 16/17 test, create 16 old-incarnation journals for one reused session ID, followed by one eligible orphan.
Pause after the first page fills the private queue, then admit that ID as the pending target through a barrier.
Keep that application pending until row 17 repairs; assert all 16 excluded SQL journals survive.
Also test a page entirely excluded at observation: its original last-row cursor and short-page evidence still advance.
Test a denied grant separately: candidate stays queued and its cursor/SQL identity does not disappear.
Test every pending application stage, current-version pending target, different target while an application exists,
finite maximum-ID sweep with concurrent insertion, 64-row reads/16-job queue cap, newer generation supersession,
positive empty polling, 60-second retry cap, cancellation, same-process writer restart, and foreground grant ordering.
Assert recovery uses ordinary worker identity and foreign commit invalidates app preparation.
Assert provider ACK/terminal readiness is not delayed by recovery discovery.

- [ ] **Step 2: Run RED.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_recovery.py -q`
Expected: new scheduler/lifetime integration tests FAIL.

- [ ] **Step 3: Implement bounded independent discovery and admission.**

Create scheduler/discovery at writer start, after ordinary worker starts; discovery is independent of provider tasks.
Each sweep captures max journal ID, reads bounded pages, and advances past all observed rows, including skipped/backed-off rows.
Do not retain all journals in memory. Stop discovery when its bounded queue has no capacity; resume after consumption.
Read no more rows than queue capacity; never read 64, queue 16, and advance past 48 discarded eligible rows.
Even ineligible/backed-off pages advance their observed cursor. Refill a limited next page only when capacity exists.
At a new sweep obtain `compute_repair_upper_id()` on the ordinary worker, then call `begin_sweep`;
use `page_request` for every DB page and `observe_page` to settle fully materialized results.
Pass the complete original page to `observe_page`; apply exclusion inside it only after capturing last observed ID and short-page status.
Do not prefilter the page in the writer, which would corrupt cursor progress and sweep completion evidence.
Deduplicate observations by row identity; newer generation replaces queued older observation.
After sweep end wait at least `RECOVERY_POLL_SECONDS` before the next sweep, including empty/stale-only sweeps.
Use exponential retry delay `min(60.0, 1.0 * 2**failure_count)` with bounded exponent; first failure waits at least one second.
Stale-target outcome remains pending for normal compute, without an immediate retry loop.
Store only `(journal_id, generation, failure_count, retry_deadline)` retry records, never snapshots.
Create retry records only for observed failed identities. Remove them after success, confirmed row disappearance,
or generation supersession. Stale, changed, and excluded outcomes need no failure retry record.
Preserve positive deadlines and repeated-failure escalation to the 60-second cap.
Retry metadata has no fixed count cap: memory is proportional to unresolved observed failures, not total journal payload.
Never gate a new distinct identity on retry-record capacity; many earlier failures cannot block a later eligible row.
Queue/pages remain bounded, and no complete journal key inventory resides in retry metadata.
Advance sweeps past backed-off rows. Shutdown clears private retry state only.

Give other-target recovery one background turn even when `_pending_compute` exists; move the current early return accordingly.
Exclude pending target during discovery and again inside the admitted async factory before any recovery SQL.
Remove excluded queued observations through `defer_unadmitted` before selecting another candidate.
If exclusion appears after grant acquisition, defer that unadmitted observation and release the grant without SQL.
Both transitions release private queue capacity; neither settles or deletes SQL journal evidence.
Pending exclusion lasts until `_pending_compute` clears after actual terminal cleanup/notification collection.
A denied grant does not dequeue the candidate. Foreground pressure wins through existing coordinator.
Execute one exact row transaction on ordinary worker; never use application executor for discovery or recovery.

Before normal final drain and in cancellation/error shutdown paths: close scheduler, stop and await discovery,
discard unadmitted candidates without DB deletion, prohibit callback/retry enqueue, then run existing finite drain.
Already admitted worker work finishes or rolls back under existing cancellation-safe worker drainage.
Do not wait for every journal/backoff timer. Reset lifecycle state on same-process restart.
Add payload-free diagnostics for discovery counts, attempts, outcomes, retry delay, key count, and grant/transaction hold.

- [ ] **Step 4: Run GREEN and writer lifetime regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_repair_recovery.py tests/test_compute_continuation.py tests/test_activity_preparation.py -q`
Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest tests/test_write_coordinator.py tests/test_compute_executor.py tests/test_content_storage_admission.py tests/test_watcher_slice_fairness.py -q`
Expected: PASS, no infinite drain or main-loop blocking SQL, no orphan lost on cancellation.

- [ ] **Step 5: Review Task 6, correct findings, and commit.**

Subject: `feat(activity): recover deferred repairs through background grants`.
Body: Explain fair finite sweeps, ordinary worker isolation, pending target race fence, foreground priority, and shutdown retention.

### Task 7: Isolated performance comparison and whole-branch validation

**Files:**
- Create: `tests/test_deferred_compute_activity_benchmark.py`
- Create: `scripts/benchmark_deferred_compute_activity.py` (shared revision adapter)
- Modify: `docs/plans/2026-10-13-deferred-compute-activity-plan.md` validation evidence only

**Interfaces:**
- No new product API.
- Benchmark opt-in environment: `TWICC_ACTIVITY_BENCHMARK=1`; default skip.
- Shared concrete entrypoint: `scripts/benchmark_deferred_compute_activity.py --checkout <absolute-checkout> --data-dir <absolute-disposable-dir> --output <absolute-report.json>`.
- The adapter launches the target checkout's `uv run` Python with explicit data dir and uses the same workload/measurement code for both revisions.
- Tests call the shared adapter; baseline uses optional journal detection rather than importing absent new modules.
- Use persisted synthetic fixtures and actual writer grants/worker connections, not mocked SQLite timings.

- [ ] **Step 1: Write characterization assertions and measurement harness.**

```python
def test_metadata_deferral_characterization():
    assert metadata_history_scan_count == 0
    assert metadata_bucket_repair_count == 0
    assert final_state_matches_independent_reference
    assert foreground_wins_next_background_grant
```

Workload A: one 12,000-row root, four 3,000-row direct children, 2,000-row metadata slices, 30 UTC days.
Workload B: 20 roots with 2,400 rows each sharing four project/global daily/weekly buckets.
Workload C: same-sized root with last timestamp removal, creation move, partial commit/restart, replacement and deletion.
Workload D: foreground append during metadata, bucket preparation, orphan recovery, and forced exact-final fallback.
Workload E: live child append while its stale parent processes many metadata chunks; record unchanged foreground repair amplification separately.
Measure current live append with and without journal presence; report indexed presence probe cost separately.
Use fixed deterministic seed and identical fractional-cost synthetic source for both revisions.

- [ ] **Step 2: Run characterization RED against baseline.**

Export baseline with `git archive b072478b` into a disposable temporary directory, not a Git worktree.
Do not copy `.env`, DB, index, artifacts, scratch, `.venv`, or provider homes.
Install/run only through that exported checkout's `uv run` project resolution.
Set `TWICC_DATA_DIR` to a separate disposable absolute data directory.
Before any benchmark DB work assert the resolved SQLite path lies inside that disposable directory,
and imported `twicc.__file__` lies inside the exported checkout.
Run the shared entrypoint with `--checkout` pointing to that disposable baseline export and `--data-dir` pointing to its separate disposable data directory.
Make the measurement adapter support baseline's absent journal without changing baseline product code.
Expected: baseline final state matches reference; characterization zero-repair assertions fail.

- [ ] **Step 3: Run comparison on final branch.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD TWICC_ACTIVITY_BENCHMARK=1 uv run pytest tests/test_deferred_compute_activity_benchmark.py -q -s`
Expected: exact-reference and admission assertions PASS.
Run `DEBUG=False` first and report separately from any `DEBUG=True` run.
Record total time, foreground completion, max grant hold, final transaction hold, source rows/chunks,
unique keys, journal serialized bytes/writes, retries, full-history scans, and bucket repair counts.
Retain measurements and methodology in the plan; do not commit disposable database or standalone benchmark reports.
Do not set machine-dependent speed assertions or claim strict latency bounds.
Report remaining atomic final/recovery holds and repeated shared-bucket final scans.

- [ ] **Step 4: Run final correctness suite and checks.**

Run all focused journal/publication/preparation/mutation/recovery/continuation/live suites first.
Run: `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest -q`
Expected: no new failures; report existing skips/warnings explicitly.
Run `uvx ruff check` on changed Python files and `git diff --check`; expected clean.
Re-run `makemigrations --check --dry-run` with worktree data dir.
No Codex binary/provider integration or frontend build is required unless implementation changes those surfaces.

- [ ] **Step 5: Obtain whole-branch adversarial review and settle findings.**

Review baseline-to-final product diff against both spec and plan.
Focus on durable old-key retention, source-first transaction fences, raw precision, identity reuse,
foreground priority, pending-target admission race, cancellation, and finite shutdown.
Validate any correction with its owning regression tests; scoped re-review must close Critical/Important findings.
Do not mark complete on benchmark timing alone.

- [ ] **Step 6: Commit benchmark coverage and validated evidence.**

Subject: `test(activity): validate deferred repair correctness and responsiveness`.
Body: Explain reference-based interruption/concurrency characterization, isolated baseline comparison, checks, and remaining holds.
Keep opt-in synthetic regression harness; do not commit scratch DBs, private reviewer reports, or temporary baseline export.
Final user report links spec/plan, states exact tests/results/limits, and reminds user to restart their instance through devctl.
Do not restart, migrate, merge, or push on the user's behalf.

## Spec coverage and self-review

| Spec requirement | Owning task |
|---|---|
| Small non-FK journal, unlimited key union, identity/generation | 1 |
| Raw exact costs/counts, retained parents, orphans/project deletion | 2 |
| No per-runtime-chunk repairs; durable source atomicity and final settlement | 3 |
| Fixed preparation proof plus journal fence and creation exclusions | 4 |
| Live/visibility/initial-sync/replacement/deletion preservation | 5 |
| Independent fair discovery, pending target exclusion, retry/shutdown | 6 |
| Foreground validation, baseline comparison, exact final oracle, remaining holds | 7 |

The plan deliberately retains standalone exact chunk semantics and all ordinary live aggregate behavior.
It does not require stable values while source compute remains incomplete.
The optional initial-sync creation skip, stale-parent live optimization, and grouped SQL aggregation remain excluded.
All new helper producers and consumers appear in their owning Interfaces blocks.
Each task has a concrete RED/GREEN cycle and a distinct independently reviewable deliverable.
The five Review Focus cases have named tests in their owning tasks.

## Review and validation evidence

Initial independent plan review identifies one Important queue-fairness gap.
The amendment adds explicit unadmitted deferral, original-page cursor evidence, and the deterministic 16-excluded/row-17 test.
It also specifies compact failed-identity retry metadata, the shared benchmark entrypoint, and the stale-parent/live-child workload.
Scoped re-review accepts the exclusion correction but identifies a new retry-cap fairness issue.
A second amendment removes the retry metadata count cap and adds a many-failures/later-row progress regression.
Second scoped re-review marks both findings addressed, with no new Critical or Important findings.
The user approves the design and plan. Product implementation and measured validation now accompany this branch.


## Implementation validation (2026-10-13)

The user approves the design and implementation plan. Tasks 1–7 now implement the approved design.
Whole-branch adversarial review remains pending. The controller owns that final gate.

### Isolated comparison

Baseline `b072478b` is exported with `git archive` into `/tmp/task7-baseline-export`.
The export is disposable and is not a Git worktree.
Both revisions run `scripts/benchmark_deferred_compute_activity.py` from the current branch.
The adapter launches each target through `uv run --project` with a separate disposable `TWICC_DATA_DIR`.
Before database work, it asserts the imported source path and SQLite path belong to the selected isolated paths.
It refuses existing databases and copies no DB, provider home, `.env`, index, scratch, artifacts, or `.venv`.
Only the asserted synthetic database receives schema setup. No server starts or running-instance migration occurs.

Both runs use seed `20261013`, SQLite WAL, and `DEBUG=False`. No `DEBUG=True` run occurs.
The same workload adapter uses actual application/ordinary workers and actual writer coordinator grants.
It preserves the runtime per-run repaired-bucket cache on both revisions.
The raw SQLite oracle checks costs, messages, creation counts, provider scopes, and daily/weekly buckets.
It retains unrounded fractional legacy costs in A’s four children.
All oracle checkpoints and foreground-versus-older-background grant assertions pass on both revisions.

- A: 12,000-row root; four 3,000-row children; 2,000-row metadata slices; 30 UTC dates.
- B: twenty 2,400-row roots share four project/global daily/weekly buckets.
- C: 12,000-row root; sole timestamp removal; creation move; two committed slices; application-worker restart; replacement; deletion.
- D: foreground append during metadata, blocked bucket preparation, orphan recovery, and forced exact-final fallback.
- E: three live child appends supersede the stale parent’s shared root placement observation; bounded retries then complete.

Final commands:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD TWICC_ACTIVITY_BENCHMARK=1 TWICC_ACTIVITY_BENCHMARK_CHECKOUT=/tmp/task7-baseline-export uv run pytest tests/test_deferred_compute_activity_benchmark.py -q -s
cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD TWICC_ACTIVITY_BENCHMARK=1 uv run pytest tests/test_deferred_compute_activity_benchmark.py -q -s
```

Baseline RED: `1 failed, 1 passed in 150.39s`; only the zero-metadata-scan characterization fails.
Current GREEN: `2 passed in 141.80s`.
Baseline records 550 metadata cost aggregate/history reads and 218 metadata bucket repairs.
Current records zero for both counters. Both runs inventory 74 distinct keys.
History counters include unbounded cost-enumeration reads and raw SUM queries; they exclude bounded ID/line reads and COUNT-only queries.
Activity repair counters wrap real repair calls.
The measured adapter totals are 145046.799 ms baseline and 136408.768 ms current.
Totals include fixture creation and oracle work, but exclude uv/schema startup.
These local samples establish correctness and work removal. They do not establish machine-independent speed or latency bounds.

| Workload | Source rows published | Metadata chunks | Baseline cost reads/repairs | Current cost reads/repairs | Baseline work ms | Current work ms |
|---|---:|---:|---:|---:|---:|---:|
| A | 24000 | 14 | 176/70 | 0/0 | 19698.977 | 17966.479 |
| B | 48000 | 40 | 124/4 | 0/0 | 46788.083 | 43609.212 |
| C | 16001 | 9 | 31/4 | 0/0 | 10136.608 | 10383.500 |
| D | 14001 | 8 | 94/70 | 0/0 | 16094.245 | 13707.686 |
| E | 21000 | 11 | 125/70 | 0/0 | 16935.327 | 15643.628 |

C published rows include replay after interruption and the one-row replacement.
D includes one foreground precondition row and one 2,000-row orphan slice.
E includes child metadata, three interrupted 2,000-row parent slices, and the complete parent publication.

| Workload | Current journal writes | Current serialized JSON bytes | Retries (both revisions) |
|---|---:|---:|---:|
| A | 14 | 50702 | 0 |
| B | 40 | 8400 | 0 |
| C | 9 | 5218 | 0 |
| D | 9 | 25529 | 0 |
| E | 11 | 39803 | 3 |

Journal writes count real INSERT/UPDATE statements, including generation fences.
Serialized bytes count JSON parameters sent by those statements.

### Remaining atomic holds and foreground work

Transaction timing starts at actual BEGIN and ends after actual backend commit/rollback.
Grant timing includes admitted worker work. Foreground timing starts when demand registers.

| Workload | Baseline max grant ms | Current max grant ms | Baseline max final transaction ms | Current max final transaction ms |
|---|---:|---:|---:|---:|
| A | 385.769 | 154.459 | 146.580 | 154.121 |
| B | 169.630 | 171.996 | 97.284 | 87.706 |
| C | 882.904 | 813.041 | 252.083 | 324.228 |
| D | 422.681 | 386.748 | 382.387 | 386.361 |
| E | 408.889 | 178.251 | 168.710 | 176.293 |

C’s maximum grant includes replacement/deletion work.
D’s orphan recovery transaction remains atomic: 270.177 ms baseline; 273.557 ms current.
Foreground wins the next grant, but it cannot preempt an admitted transaction.
Exact final fallback and orphan recovery retain atomic holds.
Shared buckets still receive repeated exact final scans as each root completes.
The baseline has no journal; its orphan comparator uses the same exact bucket reference.

| D foreground phase (execution order) | Baseline completion ms | Current completion ms |
|---|---:|---:|
| Metadata | 444.372 | 111.043 |
| Bucket preparation | 20.679 | 21.210 |
| Forced exact final | 401.380 | 406.794 |
| Orphan recovery | 294.753 | 298.561 |

E retains 225 foreground cost aggregate/history reads on each revision across three live child appends.
That foreground repair amplification remains unchanged.
E completion samples are 593.436/534.378/710.022 ms baseline and 425.984/437.398/466.759 ms current.
The adapter respects each root-placement supersession and records three retries.

| Current live append | Indexed journal probes | Probe SQL time ms | Total append ms |
|---|---:|---:|---:|
| No journal | 1 | 0.048585 | 22.524526 |
| Pending journal | 2 | 0.074446 | 22.131705 |

Neither live append scans historical source. The pending append writes one 210-byte journal JSON payload.
Probe SQL timing excludes materialization and ORM processing.
C restarts the actual application worker. Focused tests own durable process-lifetime recovery contracts.
Temporary exports, SQLite files, and standalone JSON reports remain outside Git.

### Correctness checks

Focused journal/publication/preparation/mutation/recovery/continuation/live suites: `232 passed in 50.08s`.
Authorized Ruff cleanup covering tests: `76 passed in 30.79s`.
Remaining changed final-admission/activity-obligation suites: `139 passed in 22.49s`.
One full-suite run completes: `10535 passed, 28 skipped, 3 warnings in 413.83s`.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority && TWICC_DATA_DIR=$PWD uv run pytest -q
```

Skipped: 18 opt-in Codex runtime tests, 7 opt-in Claude runtime tests, 2 opt-in benchmark tests, and 1 device-node test.
Runtime integration flags remain disabled. The device-node test requires host permission to create device nodes.
The benchmark tests pass separately with `TWICC_ACTIVITY_BENCHMARK=1`.

All three warnings are RuntimeWarnings in untouched test files:

- `test_codex_sdk_wrappers.py::test_turn_forwards_auto_review`: unawaited `AsyncMockMixin._execute_mock_call`, line 133.
- `test_codex_sdk_wrappers.py::test_turn_forwards_fast_service_tier`: the same unawaited mock coroutine, line 152.
- `test_shell_notice_codex.py::test_turn_openings_restart_the_delay`: unawaited `BaseAgent._notify_state_change`, helper line 31.

No warning names a changed-topic test. No full-suite rerun occurs.
Changed Python Ruff passes, including both new benchmark files.
`makemigrations --check --dry-run` reports `No changes detected` with the asserted worktree database path.
`git diff --check` passes.
No Codex binary/provider integration, live diagnostic, or frontend build is required for this implementation.
The baseline archive’s normal package build runs its own build hook during uv project resolution.
No dependency, changelog, server, running-instance migration, merge, or push changes occur.
The user must restart their instance through `devctl.py`; startup applies the journal migration.
