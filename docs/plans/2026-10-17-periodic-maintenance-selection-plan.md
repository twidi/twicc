# Periodic Maintenance Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce periodic maintenance reads through SQL hot-root selection and an indexed plaintext-content traversal.

**Architecture:** Keep the existing maintenance dispatcher, writer coordinator, session cache, and candidate publication paths. Separate periodic root discovery from startup discovery. Retain failed restoration targets with timed, nonblocking retries and use a partial content index for compression selection.

**Tech Stack:** Python, Django ORM, SQLite WAL, asyncio, pytest/pytest-django, existing Zstd codec workers.

**Spec:** [Periodic content maintenance: root selection and plaintext indexing](2026-10-17-periodic-maintenance-selection-spec.md).

**Status:** Self-reviewed and adversarially reviewed by two independent reviewers. No remaining blocker or important defect.

## Global Constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
- Prefix shell commands with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression &&`.
- Content cold thresholds remain 15 days for hidden/archived sessions and 30 days otherwise.
- The inactivity reference is the latest non-null value of the four existing lifecycle timestamps.
- Periodic discovery selects roots only; owner eligibility in compression remains session-local at every depth.
- Preserve 100-row and 8 MiB preparation limits, including the single-oversized-row rule.
- Preserve 30-minute initial scheduling, compute/application gating, and the 24-hour interval after pass completion.
- Retain pass-scoped invalidatable metadata, candidate-only payload reads, and foreground precedence.
- Run tests with `uv run pytest`; test settings provide isolation. Do not add `TWICC_DATA_DIR` to pytest commands.
- For other Django invocations in this worktree, set `TWICC_DATA_DIR=$PWD` and verify the resolved DB path first.
- Run migrations only on disposable test/benchmark DBs. A running-instance reset or restart requires a separate user instruction.
- Write code, comments, tests, and documents in English. Commit each reviewed task with a descriptive Conventional Commit body and the actual model trailer.

## Review Focus

1. Active root below an advanced selection cursor: live targeted restoration still completes without waiting 24 hours. Tasks 1 and 2.
2. Failed subagent restoration with no later event: retry retains the target, releases demand, and permits unrelated compression. Task 1.
3. Runtime activity or a new hot owner during preparation: stale compression cannot overwrite required plaintext. Tasks 2 and 3.
4. Sparse plaintext IDs and changing index membership: cursor advances and terminates without duplicate prepared publication. Task 3.
5. ORM parameterization or schema-state drift: real production SQL uses the declared partial index after migration. Task 3.

## File Map and Dependencies

| File | Responsibility |
|---|---|
| `src/twicc/content_maintenance_task.py` | Periodic root selection, retry scheduling, content metadata query, orchestration |
| `src/twicc/core/content_storage/policy.py` | Shared hot SQL predicate beside the scalar cold predicate |
| `src/twicc/core/content_storage/maintenance_metrics.py` | Stage identity and stage-specific progress |
| `src/twicc/core/models.py` | Partial index declaration on SessionItemContent |
| `src/twicc/core/migrations/0162_content_plain_partial_index.py` | Schema index operation after 0161 |
| `tests/test_content_maintenance_selection.py` (new) | Policy equivalence, root selection, indexed query and cursor tests |
| `tests/test_content_maintenance_retry.py` (new) | Retry timing, retained targets, priority and cancellation |
| Existing maintenance tests | Cache, payload, admission, compute gating, integration and metrics regressions |
| `tests/test_content_storage_schema.py` | Index shape, migration ordering and legacy-table safety |
| `scripts/benchmark_content_maintenance.py` | Isolated before/after selection and complete-pass measurements |
| `tests/test_content_maintenance_benchmark.py` (new) | Benchmark source/destination safety and selection-mode smoke checks |
| `docs/plans/2026-10-17-periodic-maintenance-selection-validation.md` (new) | Actual commands, measurements, review outcomes and release gate |

Task 1 precedes Task 2 because narrower discovery removes a recovery fallback.
Task 3 may follow Task 1 independently of Task 2; execute sequentially to avoid shared-file edits.
Task 4 follows Tasks 1–3. Task 5 validates the combined result.

## Task 1: Retain failed restoration targets without blocking maintenance

**Files:** Modify `src/twicc/content_maintenance_task.py` (`_PromotionTarget`, `enqueue`, `wait`, `eligible`, `promote`, periodic cleanup).
Create `tests/test_content_maintenance_retry.py`.
Update `tests/test_content_maintenance.py` and `tests/test_content_maintenance_compute_gate.py` where they assert failure recovery.

**Interfaces:**
- Extend `_PromotionTarget` with `retry_at: float | None` and `retry_attempts: int`.
- Add `_PromotionTarget.schedule_retry(now: float) -> None` and `_PromotionTarget.clear_retry() -> None`.
- `reset()` continues to reset proof/cursor/prepared state without silently erasing the retry schedule.
- Add dispatcher-local `retry_delay() -> float | None`: next runnable retry delay, considering compute gates and deferrals.
- Existing `enqueue(session_id, priority)` clears backoff for a fresh explicit request.
- Keep `request_root_promotion` and public lifecycle entry points unchanged.

- [ ] **Step 1: Add failing retry state tests.** Use a monotonic fake clock and existing writer/coordinator fixtures.
  Define `test_retry_retains_target_and_releases_foreground_demand` and `test_retry_backoff_is_capped`.
  After a failed target at clock 100, assert the same target remains queued, `retry_at == 101`, and its foreground demand is released.
  Assert subsequent waits are 2, 4, 8, 16, 32 and 60 seconds, then remain capped at 60.
  Delay bounds apply between attempts; the obligation remains pending until completion or shutdown.

```python
def test_retry_backoff_is_capped():
    target = maintenance._PromotionTarget(recovery=False, priority=WritePriority.BACKGROUND)
    try:
        delays = []
        for _ in range(8):
            target.schedule_retry(100.0)
            delays.append(target.retry_at - 100.0)
        assert delays == [1, 2, 4, 8, 16, 32, 60, 60]
    finally:
        target.finish('cancelled')
```

- [ ] **Step 2: Run the new module and confirm failure on missing retry behavior.**
  Run `uv run pytest tests/test_content_maintenance_retry.py -q`.
  Expected: named new tests fail before the feature exists; repair fixture/setup errors before proceeding.

- [ ] **Step 3: Implement retry state on `_PromotionTarget`.**
  Reset prepared/snapshot state after failure. Retain the target and restoration intent.
  Release foreground demand and retry at background priority; a later explicit foreground enqueue upgrades it again.
  If the target borrows a periodic cache, detach into a new owned MaintenanceSessionCache before the pass cache closes.
  Update the metrics cache reference. Keep `periodic` compute-gating semantics for targets originating from the periodic pass.
  Close an owned cache exactly once when the target finally settles or shuts down.

- [ ] **Step 4: Integrate deadlines into the dispatcher.**
  Replace failure `settle(...)` with error logging, retry-state update and target retention.
  `eligible()` excludes targets whose retry time has not arrived.
  `wait(timeout)` uses the minimum of its caller timeout and `retry_delay()`, retaining existing event handling.
  A retry blocked by compute or a deferral must not cause zero-timeout polling after its deadline expires.
  Rotate failed targets so another ready target can run.
  A fresh explicit enqueue clears backoff and restores requested priority without duplicating the target.
  Reset the failure backoff only after a successful committed publication advances the cursor, or a fresh explicit request arrives.
  Snapshot acquisition and preparation alone do not reset it; repeated publication failures retain increasing delays.

- [ ] **Step 5: Add dispatcher regression tests with controlled failures.**
  `test_subagent_retry_succeeds_without_new_event`: fail one subagent preparation once; advance the clock; verify its compressed content becomes plain.
  `test_failed_periodic_target_does_not_block_compression`: a perpetually failing root cannot hold the periodic compression stage behind its backoff.
  `test_retry_survives_periodic_cache_close`: the detached cache stays usable after the pass cache closes.
  `test_new_foreground_request_upgrades_retry`: explicit enqueue runs before an older backoff deadline.
  `test_retry_wait_releases_writer_and_foreground_demand`: commit an unrelated foreground write during backoff.
  `test_retry_compute_gate_and_deferral_do_not_spin`: no repeated zero-timeout wait while blocked; resume on existing signals.
  `test_publication_failures_keep_increasing_backoff`: snapshots and preparation succeed before each publication failure; dispatcher waits still increase.
  `test_retry_cancel_drains_admitted_work_and_closes_cache`: retain existing publication drain semantics and cleanup once.

- [ ] **Step 6: Adjust the periodic drain boundary.**
  The pre-compression drain waits for ready periodic restoration work, not sleeping failed targets.
  Continue compression while failed targets await retry.
  Periodic finalization must not settle a retained failed target or close its owned retry cache.
  Retry wakeups must remain effective during compression and between periodic passes.
  Cover the same behavior when startup recovery creates the failed target.

- [ ] **Step 7: Run retry and coordinator regression tests.**
  Run `uv run pytest tests/test_content_maintenance_retry.py tests/test_content_maintenance.py tests/test_content_maintenance_compute_gate.py tests/test_content_storage_admission.py -q`.
  Expected: all pass; no busy-spin or lost target in fake-clock tests.

- [ ] **Step 8: Review and commit.**
  Subject: `fix(storage): retain failed restoration targets for targeted retries`.
  Commit only this task's files after independent review and corrections.

## Task 2: Select periodic hot roots directly in SQL

**Files:** Modify `src/twicc/core/content_storage/policy.py`, `src/twicc/content_maintenance_task.py`.
Create `tests/test_content_maintenance_selection.py`.
Update `tests/test_content_maintenance_reads.py`, `tests/test_content_maintenance_metrics.py`, and affected recovery mocks.

**Interfaces:**
- `content_hot_q(now: datetime, active_ids: frozenset[str]) -> Q` in `policy.py`.
- `periodic_hot_root_page(after_id: str | None, now: datetime) -> list[str]` in `content_maintenance_task.py`.
  Read active IDs from the existing `active_session_ids` context populated by `prepare()`.
- Keep `pending_session_page(after_id)` as startup all-depth discovery.
- Keep `recovery_promotion_targets(session_ids, session_cache)` and its targeted metadata probes.

- [ ] **Step 1: Add failing SQL/scalar equivalence tests.**
  Define `test_sql_hot_matches_scalar_policy`, parameterized across all archived/hidden combinations.
  Generate lifecycle inputs with all-null dates, mixed dates, each field as the latest, future dates, and just before/equal/after both thresholds.
  Compare selected IDs with `not cold_from_fields(row, now)`, plus the active-ID override.
  Include an active old root and an active child; the root page must include only the former.

- [ ] **Step 2: Add selection population tests.**
  `test_periodic_page_filters_before_limit`: create over 128 cold roots and interleaved hot roots/subagents; selected pages contain all eligible roots exactly once.
  `test_periodic_page_requires_extracted_identity`: missing storage identity or wrong content storage version is excluded.
  `test_startup_discovery_keeps_hot_nested_subagents`: preserve startup behavior while periodic excludes the same descendants.
  Run `uv run pytest tests/test_content_maintenance_selection.py -q`; expect the new selection behavior to fail.

- [ ] **Step 3: Implement `content_hot_q` and `periodic_hot_root_page`.**
  Build mutually exclusive hidden/archived and ordinary branches using each branch's threshold.
  A branch is hot if any lifecycle field exceeds the threshold, or all four dates are null.
  OR the active-ID predicate, then combine it with root type and existing extraction predicates.
  Use strict `>` so equality stays cold; avoid nullable SQLite Greatest/MAX behavior.
  Apply `pk > after_id`, ascending ID and TREE_PAGE_SIZE after filtering.
  Project IDs only: selection does not materialize lifecycle dictionaries that would later be discarded.

- [ ] **Step 4: Wire periodic and startup discovery separately.**
  Capture one `timezone.now()` when the periodic root traversal begins.
  Pass this selection time through every periodic page, including compute pauses.
  Startup continues calling `pending_session_page`; periodic calls `periodic_hot_root_page` through the existing preparation worker.
  The following `recovery_promotion_targets` call loads selected roots' lifecycle fields into the pass cache once.
  Reuse these entries in root snapshot/preparation, publication and stage 2; retain existing targeted invalidation.
  ID-only selection avoids a second transfer of already-fetched lifecycle dictionaries and requires no cache-prefill bypass.

- [ ] **Step 5: Add integration and cache tests.**
  `test_periodic_restores_hot_root_without_descendants`: root content becomes plain; compressed cold and hot children remain untouched by periodic discovery.
  `test_root_selection_metadata_is_loaded_once_per_pass`: query capture shows ID-only selection and one grouped lifecycle load; unchanged publication does not reload it.
  `test_root_activated_below_cursor_uses_targeted_restoration`: trigger the existing lifecycle hook after cursor advancement; restore without a second discovery pass.
  `test_periodic_root_selection_pauses_without_resetting_cursor`: compute resumes between pages; resume the same selection timestamp and cursor.
  Retain shared-content restoration and current-activity publication tests.

- [ ] **Step 6: Run selection, cache and integration regressions.**
  Run `uv run pytest tests/test_content_maintenance_selection.py tests/test_content_maintenance_reads.py tests/test_content_maintenance_cache.py tests/test_content_maintenance_compute_gate.py tests/test_content_maintenance_end_to_end.py -q`.
  Expected: pass, including startup all-depth recovery and periodic root-only selection.

- [ ] **Step 7: Review and commit.**
  Subject: `perf(storage): select hot roots before periodic discovery`.

## Task 3: Add the partial index and filter plaintext before pagination

**Files:** Modify `src/twicc/core/models.py`, `src/twicc/content_maintenance_task.py`.
Create `src/twicc/core/migrations/0162_content_plain_partial_index.py`.
Update `tests/test_content_storage_schema.py`, `tests/test_content_maintenance_selection.py`, `tests/test_content_maintenance.py`, `tests/test_content_maintenance_reads.py`.

**Interfaces:**
- Model index: `models.Index(fields=['id'], name='idx_content_plain_id', condition=Q(codec='plain'))`.
- `_compression_metadata_query(after_id: int, row_budget: int) -> QuerySet` returns the existing annotated metadata projection, filtered by codec and cursor, ordered and sliced.
- `prepare_compression_batch` uses this query and retains its CompressionPage interface, owner checks, codec preparation and candidate publication.

- [ ] **Step 1: Add failing schema and production-query tests.**
  `test_plain_partial_index_definition`: assert index fields, predicate and name in model state and database DDL.
  `test_production_metadata_query_uses_plain_index`: use a representative mixed fixture and the actual queryset's `sql_with_params()` with EXPLAIN QUERY PLAN.
  Assert the plan names `idx_content_plain_id`, contains a range search, and needs no temporary sort.
  `test_compressed_rows_never_enter_metadata_page`: interleave zstd rows and plain rows; only plain IDs consume row slots.
  Run `uv run pytest tests/test_content_storage_schema.py tests/test_content_maintenance_selection.py -q`; expect new assertions to fail.

- [ ] **Step 2: Declare the model index and create the schema migration.**
  Add SessionItemContent.Meta.indexes with the stated partial index.
  Add the AddIndex migration depending on `0161_session_compute_root_index`.
  Use normal schema state and operations; do not move content data in a migration.
  Do not apply this migration to the live worktree DB during implementation.

- [ ] **Step 3: Implement `_compression_metadata_query` and use it in preparation.**
  Keep the existing metadata projection, Length(payload), owner-list size accounting, row budget and byte budget.
  Add codec='plain' before values/limit; retain ID keyset ordering.
  Replace the final unfiltered existence probe with `filter(codec='plain', pk__gt=last).exists()`.
  An empty plaintext page returns `done=True` immediately, even when compressed rows remain above its cursor.
  Keep matching-policy no-gain skipping in preparation and permit different or null attempt versions.
  Already-zstd rows are absent regardless of dictionary version.

- [ ] **Step 4: Update incompatible recompression expectations and add cursor tests.**
  Replace `test_recompression_budget_includes_decoded_input_for_large_compressed_rows` with a test that zstd rows using an older dictionary are excluded without payload reads.
  Preserve oversized-budget coverage using large plaintext candidates.
  `test_all_rejected_plain_page_advances_cursor`: use no-gain and hot-owner rows before a later eligible content.
  `test_trailing_compressed_rows_finish_plain_scan`: plaintext processing terminates despite higher compressed IDs.
  `test_all_compressed_table_finishes_in_one_empty_page`: return no entries and `done=True`, with no payload or writer work.
  `test_remaining_plain_rows_compressed_concurrently_finish_scan`: remove all remaining index members between pages; the next empty page finishes.
  `test_index_membership_changes_preserve_cursor`: compress rows between pages, restore one below cursor, insert a new row above cursor; verify defined current/next-pass behavior.
  Test same-policy no-gain, old-policy no-gain and null attempt version under both providers.

- [ ] **Step 5: Verify empty-table migration ordering and index maintenance.**
  Extend the disposable DatabaseWrapper/MigrationExecutor pattern in `test_content_storage_schema.py`.
  Apply the branch schema chain after the 0155 state with legacy SessionItem content present.
  Assert the new content table has zero rows when CREATE INDEX executes, and legacy tables/data are preserved.
  Assert the AddIndex step performs no global FK check and does not rebuild SessionItem.
  Check index membership through forced-index reads after insert, plain-to-zstd and zstd-to-plain transitions.
  Verify the index is removed on backward application and restored on forward application in the disposable DB.
  Record that this additive migration must be included in the planned release squash; inspect the consolidated graph before release.
  The release path must still create the index before any compute or initial-sync content ingestion.

- [ ] **Step 6: Run relevant schema, selection and publication tests.**
  Run `uv run pytest tests/test_content_storage_schema.py tests/test_content_maintenance_selection.py tests/test_content_maintenance.py tests/test_content_maintenance_reads.py tests/test_content_maintenance_cache.py tests/test_content_storage_writes.py -q`.
  Expected: pass; zero payload reads for compressed, no-gain or hot-owner-only pages.
  Existing tests retain changed-owner, active-transition, coherent-representation and candidate-only publication coverage.

- [ ] **Step 7: Review and commit.**
  Subject: `perf(storage): index plaintext content maintenance selection`.

## Task 4: Report progress by maintenance stage

**Files:** Modify `src/twicc/core/content_storage/maintenance_metrics.py`, `src/twicc/content_maintenance_task.py`.
Update `tests/test_content_maintenance_metrics.py` and `tests/test_content_maintenance_end_to_end.py`.

**Interfaces:**
- Add `MaintenanceMetrics.set_stage(stage: str) -> None` for `root_restoration` and `plaintext_compression` in periodic operations.
- Add `MaintenanceMetrics.record_root_selection(selected: int, inspected: int, duration_ms: float) -> None`.
- Preserve startup/targeted operation kinds; stage fields augment periodic metrics rather than conflating separate operation totals.

- [ ] **Step 1: Add failing metrics tests.**
  `test_periodic_stages_have_distinct_populations`: start, root progress, stage transition, plaintext progress and complete events identify their stage.
  Assert selected/inspected root counts and stage timings remain separate from plaintext `scanned`.
  `test_stage_metrics_do_not_count_compressed_rows_with_sql`: capture queries and assert logging emits no count/scan queries.
  `test_retry_diagnostics_survive_pass_completion`: retained retry target remains a separate restoration operation after periodic completion.
  Run `uv run pytest tests/test_content_maintenance_metrics.py -q`; expect new metrics assertions to fail.

- [ ] **Step 2: Implement bounded stage accounting.**
  Track stage elapsed time with the existing monotonic clock and include stage identity in progress and terminal summaries.
  Emit a stage transition immediately, even within the normal ten-second progress throttle.
  Keep selected/inspected root counters and plaintext counts distinct.
  Retain target restoration counters for candidate/payload/change/hold details; correlate periodic-origin target logs with the periodic operation ID.
  Do not add overlapping target and parent writer totals as if independent.
  Keep `already_compressed` as a legacy zero field or remove it from new periodic summaries; label plaintext selection explicitly.

- [ ] **Step 3: Add progress and interruption tests.**
  An entirely conformant pass emits read-only progress with zero payload/publication counters.
  Cancellation or failure during either stage logs the correct stage and closes only its owned pass cache.
  A retry target with a detached cache remains usable after periodic summary emission.
  Run `uv run pytest tests/test_content_maintenance_metrics.py tests/test_content_maintenance_retry.py tests/test_content_maintenance_end_to_end.py -q`; expect pass.

- [ ] **Step 4: Review and commit.**
  Subject: `feat(storage): report periodic maintenance stages separately`.

## Task 5: Validate selection gains, full maintenance and index write overhead

**Files:** Modify `scripts/benchmark_content_maintenance.py`.
Create `tests/test_content_maintenance_benchmark.py` and `docs/plans/2026-10-17-periodic-maintenance-selection-validation.md`.

**Interfaces:**
- Add benchmark scenario `selection` and `run_selection_benchmark() -> dict`.
- Reuse existing destination/output validation and read-only source backup; all schema changes stay inside its new destination.
- Add measured result groups `root_selection`, `content_selection`, `index_build`, `index_write_overhead`, and `complete_pass`.

- [ ] **Step 1: Add failing benchmark isolation tests.**
  Use subprocess tests with tiny fixtures.
  Verify a source path or hard-link alias cannot become the write destination/output.
  The source schema and file contents remain unchanged after the selection benchmark.
  Existing protected-directory and exclusive-output checks remain active.
  Run `uv run pytest tests/test_content_maintenance_benchmark.py -q`; expect missing-scenario tests to fail.

- [ ] **Step 2: Implement selection comparisons on the disposable destination.**
  Measure all-depth pending_session_page/recovery_promotion_targets against periodic_hot_root_page/recovery_promotion_targets with fresh caches.
  Measure the previous unfiltered metadata query, filtered query without index, and production filtered query with partial index.
  Use the same metadata projection and page budgets, not an ID-only surrogate.
  Read benchmark baselines without publishing representations so subsequent variants see the same rows.
  Drop/recreate only the benchmark destination's partial index when measuring index effects.
  Record plans, query counts, durations, row populations, payload bytes, cache conditions and index size/build time.
  Clearly distinguish all-depth recovery population from root-only discovery and all-content from plaintext-only scans.

- [ ] **Step 3: Measure index write overhead and end-to-end behavior.**
  On matched synthetic disposable fixtures with and without the index, time insertions and plain/zstd transitions using production write helpers.
  Include small, medium, large and oversized payloads and repeated/shared owners; separate fixture setup and codec time from writer hold/commit time.
  Run the actual dispatcher on conformant and genuinely compressible datasets, plus foreground writes during maintenance.
  Include a transient restoration failure and a permanently failing target to prove unaffected work continues.
  Report useful changes, SQL/cache/payload costs, writer holds and foreground commit latency per stage.
  Query-only speedups do not become a claimed percentage improvement for the full pass.

- [ ] **Step 4: Run focused tests and the broader maintenance suite.**
  Run `uv run pytest tests/test_content_maintenance_selection.py tests/test_content_maintenance_retry.py tests/test_content_maintenance_benchmark.py tests/test_content_maintenance.py tests/test_content_maintenance_cache.py tests/test_content_maintenance_compute_gate.py tests/test_content_maintenance_reads.py tests/test_content_maintenance_metrics.py tests/test_content_maintenance_end_to_end.py tests/test_content_storage_schema.py tests/test_content_storage_admission.py tests/test_content_storage_writes.py -q`.
  Expected: all pass. Run further relevant root-state/lifecycle tests if those production helpers change.
  Run `uvx ruff check` with the explicit changed Python file list; expect no violations.
  Run `git diff --check`; expect no whitespace errors.

- [ ] **Step 5: Run disk-backed benchmarks safely.**
  Use the worktree DB only as an explicit read-only source for an isolated backup.
  Check available disk space; use the script's existing free-space guard and safe new destination.
  Example: `uv run python scripts/benchmark_content_maintenance.py --scenario selection --source-db "$PWD/db/data.sqlite" --work-dir /tmp/twicc-maint-selection-<unique> --output /tmp/twicc-maint-selection-<unique>.json`.
  This script configures its own disposable settings; do not import application settings before its safety/configuration steps.
  Retain concise result JSON and the validation document. Remove only benchmark DBs created by this run after closing connections.

- [ ] **Step 6: Record acceptance and release readiness.**
  Document actual selected roots, plaintext candidates, query plans, before/after timings, index write overhead and complete-pass measurements.
  Record cache/IO limitations and any background workload during measurement.
  Map every spec acceptance scenario to tests or benchmark evidence.
  The release checklist must verify that the eventual squashed migration creates the index on the empty content table.
  Report any unperformed release-squash check as pending; do not silently claim it is validated.
  Obtain independent whole-change review and resolve important defects before declaring implementation complete.

- [ ] **Step 7: Commit validation changes.**
  Subject: `test(storage): validate indexed periodic maintenance selection`.

## Execution Handoff

Self-review checks spec coverage, step precision, interface consistency, failure-mode tests and plan proportion.
The first independent review finds an omitted completion-query change that could make plaintext traversal loop indefinitely.
Task 3 now filters the completion probe and explicitly finishes empty plaintext pages.
Its tests cover trailing compressed rows, all-compressed tables and concurrent removal of remaining plaintext candidates.
The same reviewer confirms the correction in a second review.
The second independent reviewer finds no blocker in retry scheduling, cache ownership, foreground precedence or stage metrics.
Its suggested dispatcher test for repeated publication failures is included in Task 1.
Both reviewers report no remaining blocker or important defect in their review scope.
The eventual release-squash verification remains an explicit implementation/release gate, not a completed measurement.

No production code or migration is implemented by writing this document.
The detailed implementation begins only after the user requests execution.
