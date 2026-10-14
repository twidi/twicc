# Root Compute and Unified Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Compute native trees in recent-root order and publish costs and daily/weekly activity through one durable, event-driven calculator.

**Architecture:** Provider coordinators prepare descendants before their native root. Source transactions append root IDs to a persistent queue. One consumer captures bounded queue batches, prepares derived values on a dedicated read thread, and publishes through the existing writer.

**Tech Stack:** Python 3.13+, Django 6, SQLite WAL, asyncio, existing provider compute processes, Channels, Vue 3, Pinia.

**Spec:** [Root compute and unified statistics calculator](2026-10-14-root-compute-and-unified-statistics-spec.md).

## Global constraints

- Queue IDs use Django `BigAutoField`, SQLite signed 64-bit integers, and monotonically increasing committed IDs.
- Queue collection uses a fixed two-second window. Arrivals do not extend that window.
- Background compute enqueues once per completed root tree. Watcher ingestion enqueues once per affected root per committed batch.
- The consumer accepts partial source compute. Final root publication queues a converging calculation.
- Own-cost cache uses `Session.last_updated_at` and nullable `items_cost_computed_through_at`.
- Equal or regressing source timestamps retain the explicitly accepted cache limitation.
- Historical reads execute outside writer ownership and outside the main asyncio thread.
- Foreground writer work takes priority between short background publications.
- Preserve persisted Decimal precision, NULL/zero conventions, UTC boundaries, and existing aggregate eligibility filters.
- Application startup migrations perform schema work, not historical data movement.
- Use English for repository artifacts. Keep all specifications and plans in `docs/plans`.

## Review focus

1. Interrupted tree compute leaves current descendants and an outdated root: restart skips completed members and finishes the root. Tests: tasks 2–3.
2. A descendant fails or changes during application: root completion must wait, while other roots progress. Tests: tasks 2–3.
3. A source changes after queue capture: acknowledgement must preserve its new entry, including the same root. Tests: tasks 1 and 7.
4. Background item recomputation overlaps an own-cost read: older preparation must not erase its invalidation. Tests: tasks 4 and 6.
5. Workflows include nested agents: their total and phase costs must count each contribution once. Tests: task 5.

## Working directory and verification commands

Work in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
Prefix every shell command with `cd` to this path. Preserve the checkout's existing changes.

Backend tests use `TWICC_DATA_DIR=$PWD uv run pytest <paths> -q`.
Frontend tests use `cd frontend && npm test` after the worktree prefix.
Run Python lint with `uvx ruff check <changed Python paths>`.
Each task follows a failing-test, implementation, passing-test, review, and commit cycle.
Each commit uses a Conventional Commit subject, a descriptive body, and the current model's `Co-Authored-By` trailer.

Do not start DevCTL for unit validation. A real-data reset/start requires the user's explicit instruction.

## File map and dependency order

| Unit | Files | Responsibility |
|---|---|---|
| Queue | `core/models.py`, new migration, new `providers/statistics_queue.py` | Append entries, capture boundaries, acknowledge committed work |
| Native tree selection | new `providers/compute_tree.py`, existing `compute_selection.py` | Root membership and ordered source selection |
| Provider integration | `background_compute_task.py`, `codex/background_compute.py`, `db_writer.py`, `compute_base.py` | Descendant applications, root completion, atomic enqueue |
| Preparation | new `providers/statistics_prepare.py` | Own-cost snapshot, hierarchy, affected days, project/workflow inputs |
| Publication | new `providers/statistics_publication.py` | Bounded replacement writes and notifications |
| Runtime | new `providers/statistics_runtime.py` | Single event-driven consumer and dedicated read executor |
| Source integration | `live_aggregates.py`, `sessions_watcher.py`, workflow paths, existing writer payload handlers | Collect one enqueue per committed source batch |
| Frontend refresh | `composables/useWebSocket.js`, `stores/data.js`, `components/activity/ContributionGraphs.vue` | Coalesced activity and displayed cost refresh |
| Retirement | existing `compute_repair*`, `tree_aggregate*`, `contribution_revisions.py`, corresponding tests | Remove superseded runtime and aggregate-only guards |

Paths in the map are relative to `src/twicc`, except frontend paths under `frontend/src`.
Tasks 1–3 establish producer contracts. Tasks 4–6 establish calculation contracts.
Task 7 joins them. Task 8 routes live producers. Task 9 retires old writers and enables the replacement.
Task 10 validates the complete system. Intermediate commits are development checkpoints, not independently releasable versions.

## Task 1: Durable root queue and cache schema

**Files:**
- Modify: `src/twicc/core/models.py`.
- Create: `src/twicc/core/migrations/0158_statistics_queue.py` (use the next free number if the branch advances).
- Create: `src/twicc/providers/statistics_queue.py`.
- Create: `tests/test_statistics_queue.py`.

**Interfaces:**
- Model: `StatisticsQueueEntry(id: BigAutoField, root_session_id: CharField(max_length=255))`.
- Model field: `Session.items_cost_computed_through_at: DateTimeField(null=True, blank=True)`.
- `append_roots(root_ids: Iterable[str]) -> None`: requires the source transaction; deduplicates this call only.
- `capture_queue() -> QueueBatch | None`; `QueueBatch(max_id: int, root_ids: tuple[str, ...])` is a `NamedTuple`.
- `acknowledge_queue(max_id: int) -> None`: writer transaction only; deletes IDs at or below the boundary.
- `install_queue_wakeup(callback: Callable[[], None] | None) -> None`: main-process lifecycle binding.

- [x] Write tests `test_append_rolls_back_with_source`, `test_repeated_root_entries_survive`, `test_acknowledge_preserves_later_same_root`, and `test_ids_above_32_bits_are_not_reused`.
  Assert rollback leaves zero rows and sends zero wakeups. Assert wakeup follows commit.
  Seed SQLite's sequence above `2**31`, delete acknowledged rows, and assert the next committed ID exceeds the previous ID.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_queue.py -q`; confirm failures identify the missing queue contract.
- [x] Implement the schema and queue functions. Use the primary-key index for range capture/delete; add no root uniqueness constraint.
  Register wakeups with `transaction.on_commit`. The callback must use the owning loop's `call_soon_threadsafe`, because source writes run on threads.
  Read `MAX(id)` and distinct root IDs within one read snapshot. Split large insertion parameter lists within the same source transaction.
  Inspect generated SQLite schema SQL. Add the nullable marker without a default, index, table copy, or historical backfill.
  Use the project's custom SQLite schema backend if migration state needs a separate database operation.
- [x] Run queue tests and `uvx ruff check src/twicc/providers/statistics_queue.py tests/test_statistics_queue.py`.
- [x] Review and commit: `feat(stats): add durable root calculation queue`.

## Task 2: Shared native-tree discovery and source ordering

**Files:**
- Create: `src/twicc/providers/compute_tree.py`.
- Modify: `src/twicc/providers/compute_selection.py`.
- Create: `tests/test_compute_tree.py`.
- Modify: `tests/test_compute_age_selection.py`.

**Interfaces:**
- `NativeTree(root_id: str, member_ids: tuple[str, ...], ordered_source_ids: tuple[str, ...])`.
- `resolve_native_root(session_id: str) -> str`: use existing native membership, not `spawned_by`.
- `load_native_tree(root_id: str) -> NativeTree`: include the root and database-native descendants.
- `select_outdated_roots(provider: str, compute_version: int) -> tuple[str, ...]`: roots ordered by descending root `mtime`, then root ID.
- Source eligibility still uses `compute_age_delay`, the existing 30-second threshold, and provider-specific forced rebuild state.

- [x] Write tests for an old descendant under a recent root, three nested launcher levels, unlinked native members, interrupted trees with current descendants, and independent spawned sessions.
  Assert known descendants sort by descending depth, then descending `mtime`, with root last.
  Assert unrelated old subagents never become independently ranked candidates.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_tree.py tests/test_compute_age_selection.py -q`; confirm the ordering contract fails.
- [x] Implement membership from `Session.parent_session_id`; use `AgentLink` for launcher depth.
  Use deterministic ID ties. Skip current members when building required source work.
  Missing launcher links leave native members eligible for preparation. Re-read membership after applications can discover links.
  Detect malformed cycles or conflicting launcher parents; report a retryable tree failure instead of hanging or counting twice.
- [x] Run the task tests. Keep root grouping separate from Codex source migration eligibility.
- [x] Review and commit: `feat(compute): select and order native root trees`.

## Task 3: Apply complete trees and enqueue root completion

**Files:**
- Modify: `src/twicc/providers/background_compute_task.py`.
- Modify: `src/twicc/providers/codex/background_compute.py`.
- Modify: `src/twicc/providers/db_writer.py`.
- Modify: `src/twicc/providers/compute_base.py`.
- Modify: `tests/test_background_compute_retries.py`, `tests/test_codex_compute_recovery.py`, `tests/test_compute_apply_signals.py`.
- Create: `tests/test_root_compute_completion.py`.

**Interfaces:**
- Provider coordinators consume task 2's `NativeTree` and existing `ComputeApplied` acknowledgements.
- The root retains its outdated compute version throughout descendant processing.
- Root final metadata publication validates required database members, advances the root version, and appends the root queue entry in one writer transaction.
- Completion validates descendants against the current target version before advancing the root's version.

- [x] Write controlled-acknowledgement tests for both providers.
  Assert descendants apply before root admission, worker status alone never completes a tree, and successful completion creates exactly one queue row.
  Assert a failed/superseded descendant prevents completion, retries automatically, and permits another root to progress.
  Assert restart skips current descendants and finishes the remaining root.
  Assert a new native member discovered before completion joins required work.
  Insert an outdated member after coordinator validation but before root-final writer publication; assert no false completion commits.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_root_compute_completion.py tests/test_background_compute_retries.py tests/test_codex_compute_recovery.py tests/test_compute_apply_signals.py -q`; confirm missing root grouping fails.
- [x] Replace per-source global selection with one active native tree per provider.
  Keep existing bounded result admission and run retirement. Within a known depth, allow existing bounded compute/application overlap.
  Wait for required descendant writer acknowledgements before admitting the root.
  Re-query member versions and membership inside root final completion publication.
  A changed tree retains an outdated root and re-enters selection automatically; it cannot commit a completion queue entry or current root version.
  Preserve Codex rollout preparation, migration leases, forced rebuilds, unavailable diagnostics, and replay callbacks.
  Queue completion depends on successful source transactions, not notification delivery.
  Member item-cost metadata writes set their own marker to NULL in the same transaction; final member publication also resets it.
  Ensure stale model saves do not overwrite the cache marker later.
  Remove background per-slice/per-member queue production. Retain startup root progress and source reindex requests.
- [x] Run the task tests plus `tests/test_compute_shutdown.py`, `tests/test_compute_ipc.py`, and `tests/test_search_compute_readiness.py`.
- [x] Review and commit: `feat(compute): complete native trees before statistics enqueue`.

## Task 4: Snapshot own costs and compute hierarchical totals

**Files:**
- Create: `src/twicc/providers/statistics_prepare.py`.
- Modify: `src/twicc/providers/contribution_revisions.py` for a narrowly retained background invalidation guard.
- Create: `tests/test_statistics_costs.py`.

**Interfaces:**
- `PreparedOwnCost(session_id: str, observed_at: datetime | None, self_cost: Decimal | None, invalidation_observation: RevisionObservation)`.
- `SessionCosts(session_id: str, self_cost: Decimal | None, subagents_cost: Decimal | None, total_cost: Decimal | None)`.
- `prepare_tree_costs(root_ids: tuple[str, ...]) -> PreparedTreeCosts`.
- `PreparedTreeCosts(own_updates: tuple[PreparedOwnCost, ...], session_costs: tuple[SessionCosts, ...], project_ids: tuple[str, ...], workflow_ids: tuple[int, ...])`.
- Reuse the existing `RevisionObservation` type for the narrow guard. Only background own-cost invalidation observations participate.

- [x] Write `test_only_changed_member_sums_items`, `test_subtree_costs_include_nested_agents_once`, `test_unlinked_member_counts_once`, and `test_accepted_equal_timestamp_cache_hit`.
  Fixture: root own cost 1, child own cost 2, grandchild own cost 3. Assert child total 5 and root total 6.
  For 400 members with one advanced timestamp, assert the own-cost query only contains that member ID.
  Assert marker NULL forces a complete own sum. Assert NULL source timestamps cannot become valid cache hits.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_costs.py -q`; confirm missing selective calculations fail.
- [x] Implement read preparation with a dedicated thread-owned Django connection and a consistent read transaction.
  Group sums only for members with NULL markers or advancing timestamps. Quantize through existing `persisted_cost` conventions.
  Read cached own values for the other members. Compute launcher subtree sums bottom-up using `Decimal`.
  Root totals include every native member once. Resolve unlinked components without adding their costs twice.
  Capture background invalidation observations before the snapshot and check them under writer admission during publication.
  Reuse only the existing source observation mechanism needed to detect background item-cost publication; ordinary live changes queue a later pass.
  This guard must detect NULL-to-NULL invalidations. Marker equality alone is insufficient.
  Add no database revision field, line-number marker, daily/project certificate, or readiness gate.
- [x] Run task tests and add an interleaving test: read old costs, commit background invalidation, then reject the prepared marker publication.
- [x] Review and commit: `feat(stats): prepare selective hierarchical costs`.

## Task 5: Prepare daily, weekly, project, and workflow outputs

**Files:**
- Modify: `src/twicc/providers/statistics_prepare.py`.
- Modify: `src/twicc/core/models.py` (`Workflow.compute_costs` calculation semantics and activity helpers).
- Create: `tests/test_statistics_activity.py`, `tests/test_statistics_workflows.py`.

**Interfaces:**
- `DailyKey(project_id: str | None, provider: str, day: date)`; retain or move the existing `NamedTuple` consistently.
- `ActivityValues(user_message_count: int, session_count: int, cost: Decimal)`.
- `prepare_affected_days(root_ids: tuple[str, ...]) -> tuple[DailyKey, ...]`.
- `prepare_daily(key: DailyKey) -> ActivityValues`.
- `prepare_weekly(key: DailyKey) -> ActivityValues`: key day is UTC Monday; reads persisted daily rows.
- `prepare_project_cost(project_id: str) -> Decimal | None`.
- `prepare_workflow_cost(workflow_id: int) -> tuple[Decimal, dict[str, float]]`.

- [x] Write tests for two trees sharing a day, different providers/projects, hidden roots, cost-free user messages, root creation-day counts, and a UTC Sunday/Monday boundary.
  Assert affected keys deduplicate. Assert a daily sum includes an unrelated tree's contributions on that day.
  Assert weekly preparation reads daily rows and performs zero `SessionItem` queries.
  Write nested workflow fixtures with repeated envelope agent entries; assert each attributable contribution counts once.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_activity.py tests/test_statistics_workflows.py -q`; confirm missing preparation contracts fail.
- [x] Discover distinct surviving contribution and root creation dates, including dates affected by visibility changes.
  Query per-day indexed timestamp ranges. Apply the filters in spec section 9 and include project-specific plus global/provider keys.
  Prepare project sums after session cost publication. Sum native root `total_cost`, deduplicating project IDs.
  Prepare weeks after daily publication. Sum all seven days, treating absent rows as zero using current zero-row conventions.
  Prepare workflow costs after session costs. Map workflow agents to disjoint owned subtrees; exclude nested contributions already included by an ancestor entry.
  Preserve established phase attribution for workflow agent entries. Use `raw_json` as input; publish only derived cost columns.
- [x] Run task tests plus existing workflow discovery and metadata tests.
- [x] Review and commit: `feat(stats): prepare activity and workflow aggregates`.

## Task 6: Publish derived replacements with short writer ownership

**Files:**
- Create: `src/twicc/providers/statistics_publication.py`.
- Modify: `src/twicc/providers/db_writer.py` for publication integration.
- Create: `tests/test_statistics_publication.py`.

**Interfaces:**
- `async publish_tree_costs(prepared: PreparedTreeCosts) -> None`.
- `async publish_daily(key: DailyKey, values: ActivityValues) -> None`.
- `async publish_weekly(key: DailyKey, values: ActivityValues) -> None`.
- `async publish_project_cost(project_id: str, value: Decimal | None) -> None`.
- `async publish_workflow_cost(workflow_id: int, cost: Decimal, phases_cost: dict[str, float]) -> None`.
- `BackgroundInvalidated` exception rejects an obsolete own-cost publication; consumer retains the batch.

- [x] Write tests for exact replacement on replay, background invalidation before publication, invalidation between publication slices, and foreground admission between slices.
  Assert stale preparation cannot set a valid cache marker or acknowledge its queue batch.
  Assert every historical read occurs without writer ownership.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_publication.py -q`; confirm missing publication contracts fail.
- [x] Route each bounded publication through `run_under_storage_write_lock` with the existing background priority.
  Execute synchronous transactions on the established writer connection/thread path; never write from the read executor.
  Use parameterized `executemany` for homogeneous session metadata replacements, not Django `bulk_update` expression construction.
  Begin with at most 256 session rows per publication. Publish daily, weekly, project, and workflow keys in separate short transactions.
  Validate own-cost invalidation observations inside each publication transaction before setting costs and covered timestamps.
  Yield to the event loop between publications. Release writer admission before the next read preparation.
  Use exact replacements throughout; failure after partial publication is safe to repeat.
- [x] Run task tests plus `tests/test_statistics_publication.py` and `tests/test_statistics_runtime.py` for the replacement lifecycle contract.
- [x] Review and commit: `feat(stats): publish derived values through bounded writer transactions`.

## Task 7: Event-driven consumer, recovery, and lifecycle

**Files:**
- Create: `src/twicc/providers/statistics_runtime.py`.
- Modify: `src/twicc/providers/db_writer.py` (`start_db_writer`, shutdown paths).
- Create: `tests/test_statistics_runtime.py`.

**Interfaces:**
- `StatisticsConsumer(wakeup: asyncio.Event | None = None)` owns one event, one task, and one single-worker read executor.
- `start() -> None`; `wake() -> None`; `async close() -> None`.
- `async run_batch(batch: QueueBatch) -> None` orchestrates tasks 4–6 and queue acknowledgement.
- Internal constants: `COLLECTION_SECONDS = 2.0`, retry delays start at 1 second and double to a 60-second ceiling.

- [x] Write fake-clock tests for startup backlog, idle zero reads, a fixed two-second window, same-root arrival during calculation, enqueue before idle wait, and transient failure without another signal.
  Assert one calculation batch runs across both providers. Assert partial trees are processed.
  Assert failures retain all captured entries. Assert shutdown waits for owned thread work before closing its Django connections.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_runtime.py -q`; confirm missing runtime behavior fails.
- [x] Implement the consumer with the following sequence:
  Clear wake event; inspect queue; if empty await wake; if work exists collect for two seconds; capture boundary.
  Deduplicate roots; prepare/publish tree costs; prepare/publish project and workflow costs; discover/deduplicate days; prepare/publish days; prepare/publish weeks.
  Complete notification delivery as task 9 defines, then acknowledge all queue rows through the captured boundary under background writer admission.
  Repeat queue inspection before waiting. Enqueues above the boundary survive even when the same root repeats.
  On calculation failure, retain rows and retry after bounded delay; cancellation retains unacknowledged rows.
  Own executor work across cancellation. Drain active reads off the main loop before closing their thread-owned connection.
  Bind wake callback only after the loop and writer are ready. Startup queue inspection recovers any enqueue before binding.
  Close the consumer before writer teardown; retain database work if shutdown interrupts publication.
- [x] Run task tests plus existing compute shutdown and writer lifecycle tests.
- [x] Review and commit: `feat(stats): run durable event-driven calculation batches`.

## Task 8: Route live source changes into one batch enqueue

**Files:**
- Modify: `src/twicc/providers/live_aggregates.py`, `src/twicc/providers/compute_base.py`, `src/twicc/providers/sessions_watcher.py`.
- Modify: `src/twicc/providers/db_writer.py` source payload handlers.
- Modify: `src/twicc/providers/claude_code/workflow_synthesis.py`.
- Modify: `src/twicc/projects.py`, `src/twicc/core/models.py` derived-cost callers.
- Modify: `tests/test_live_sync_aggregates.py`, `tests/test_workflow_discovery.py`.
- Create: `tests/test_statistics_producers.py`.

**Interfaces:**
- Replace cost/activity delta production with `changed_roots(before_items, after_items, before_sessions, after_sessions) -> frozenset[str]` in `live_aggregates.py`.
- Extract the existing bounded user-message count delta from `apply_contribution_changes` into `apply_message_count_changes(before_items, after_items) -> None`.
  This preserves a source operation currently coupled to the removed aggregate writes; it is not a new calculator output.
- Call task 1's `append_roots` once at each committed ingestion/source transaction boundary.
- Cost, relevant activity, visibility, launcher attribution, and workflow attribution changes feed the same queue.

- [x] Write tests: fifteen lines with several costs enqueue once; two roots in one batch enqueue twice; debug-only lines enqueue zero; cost-free user messages enqueue once.
  Assert failed source transactions leave no work. Assert hierarchy-only and workflow-only changes enqueue their native root.
  Assert cutting cost/activity deltas retains the existing user-message count update in the source transaction, including partial background trees.
  For a first cost-free user message, assert the source count changes from 0 to 1 and the consumer publishes creation-day session count 1.
  Replay the same batch; assert both counts stay 1 and no duplicate contribution appears.
  Assert watcher changes during background application remain eligible for an immediate partial-tree consumer pass.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_producers.py tests/test_live_sync_aggregates.py tests/test_workflow_discovery.py -q`; confirm current dispersed aggregate writes violate the replacement contract.
- [x] Collect relevant changes for the complete bounded ingestion transaction. Resolve and deduplicate native roots once before enqueue.
  Route existing create/update/hide, AgentLink, and workflow mutation boundaries into the queue when included outputs can change.
  Remove cost/activity delta writes from these paths. Preserve each source handler's existing source operations.
  Remove runtime calls to `Session.recalculate_costs`, `Project.recalculate_total_cost`, `update_project_total_cost`, and activity recalculation outside the consumer.
  Keep pure calculation helpers only where the consumer uses them. Make workflow envelope writes enqueue recalculation rather than recompute derived costs inline.
  Audit whole-model saves for stale derived-cost values; restrict write field lists so source saves cannot overwrite consumer publication.
  Background metadata slices invalidate markers through task 3; they never use the live enqueue path.
- [x] Run producer tests plus source ingest, nested-agent, workflow synthesis, and project notification regressions.
- [x] Review and commit: `refactor(stats): route live aggregate changes through the queue`.

## Task 9: Notifications, retirement, and upgrade activation

**Files:**
- Modify: `src/twicc/providers/statistics_publication.py`, `src/twicc/providers/statistics_runtime.py`, `src/twicc/providers/db_writer.py`.
- Modify: `frontend/src/composables/useWebSocket.js`, `frontend/src/stores/data.js`, `frontend/src/components/activity/ContributionGraphs.vue`.
- Create: `frontend/src/composables/statisticsRefresh.test.js`, `tests/test_statistics_notifications.py`.
- Modify: `src/twicc/settings.py`.
- Modify: `src/twicc/providers/claude_code/sessions_watcher.py` (the real workflow-file cost writer).
- Delete or trim: `src/twicc/providers/compute_repair.py`, `compute_repair_journal.py`, `compute_repair_recovery.py`, `tree_aggregate_runtime.py`, `tree_aggregate_state.py`, `tree_aggregate_prepare.py`, `aggregate_removal.py`.
- Trim: `src/twicc/providers/contribution_revisions.py` to the required narrow background guard, with matching imports/tests.
- Modify: schema migration from task 1 or add the next migration to remove `ComputeAggregateRepair`.

**Interfaces:**
- `async notify_statistics_published(root_ids: tuple[str, ...], project_ids: tuple[str, ...], workflow_owner_ids: tuple[str, ...]) -> None`.
- Reuse session/project broadcasts and the existing `workflow_changed` event.
- New `activity_updated` WebSocket message contains no queue IDs. Frontend requests current activity/home data.
- New `statistics_updated` message carries affected native root IDs and project IDs for displayed subagent cost refresh.

- [x] Write notification tests for refreshed root/subagent costs, project costs, unchanged workflow envelopes, and activity-only batches.
  Write a frontend test proving concurrent activity signals coalesce refresh requests and a signal during fetch causes a trailing refresh.
  Assert startup consumes a preexisting queue even if one provider's initial sync failed.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_statistics_notifications.py -q` and the frontend test suite; confirm missing notifications fail.
- [x] Send notifications after derived publication. Reuse `workflow_changed` dispatch so open workflow views refresh.
  Implement the activity handler using `loadHomeData`, which already returns project and global weekly activity.
  Dispatch `twicc:activity-updated`; mounted `ContributionGraphs.vue` instances refresh through `fetchDailyActivity` and remove their listener on unmount.
  Coalesce each refresh target with a pending trailing refresh. Cover both home sparklines and mounted daily contribution graphs.
  Existing `broadcast_session_updated` skips subagents. Use `statistics_updated` to refresh loaded native-root agent snapshots through `fetchSubagentsState`.
  Refresh separately loaded subagent cost payloads through `refreshSessionRecord(sessionId)`.
  `loadSessionMetadata` returns item metadata and cannot refresh Session costs.
  Do not route subagents into the top-level session list to deliver cost updates.
  Route the real workflow-file writer in Claude's `sessions_watcher.py` into task 8's queue contract too.
  Remove `_discover_compute_repairs`, `_advance_compute_repair`, `RepairRecoveryScheduler`, `TreeAggregateScheduler`, and their lifecycle wiring.
  Remove obsolete repair arguments from compute messages, provider state, and tests. Search all callers before deleting helper modules.
  Remove old readiness/inventory guards from aggregate calculation. Keep the consumer's startup recovery and retry behavior.
  Bump `CLAUDE_CODE_COMPUTE_VERSION` from 115 to 116 and `CODEX_COMPUTE_VERSION` from 56 to 57, or next values if the branch advances.
  Compute-version mismatch remains persistent after restart and triggers the new root traversal. Drop the experimental repair table through schema migration.
  Inspect generated migration SQL on an isolated test database. Verify adding the cache marker does not rebuild `core_session`.
- [x] Run notification/frontend tests. Replace old repair-scheduler behavior tests with equivalent queue/recovery tests rather than retaining obsolete readiness assumptions.
- [x] Review and commit: `refactor(stats): activate unified calculator and retire repair polling`.

## Task 10: Complete-system regression and real-data validation

**Files:**
- Create: `tests/test_root_statistics_integration.py`.
- Adapt: `tests/test_tree_aggregate_contention.py`, `tests/test_deferred_compute_activity_benchmark.py`, `tests/tree_aggregate_helpers.py`.
- Update: this plan's checkboxes and measured validation notes.

**Interfaces:**
- Tests exercise provider enqueue → queue capture → cost/activity preparation → writer publication → notification → acknowledgement.
- Benchmark fixtures use persisted cost metadata and explicit native/launcher membership as the reference oracle.

- [x] Write end-to-end tests for both providers, partial live trees, interruption after partial publication, equal timestamp acceptance, and restart with current descendants/outdated root.
  Assert final session/subtree/project/workflow costs and daily/weekly values match direct reference sums.
  Assert new queue work survives interrupted batches and resumes without an external wakeup.
- [x] Run targeted integration tests first, then `TWICC_DATA_DIR=$PWD uv run pytest -q`, frontend tests, and lint on changed Python files.
  Inspect each failure. Update tests only where the reviewed spec changes behavior.
- [x] Search all derived field writers and old module references. Confirm one runtime author for included output columns.
  Verify migration SQL and monotonic queue IDs in a disposable test database. Never run migrations against the main data directory.
- [x] Benchmark small, medium, large, and very large trees, including the previous 499/9,753/59,004/458,192-item classes where available.
  Compare full own-cost reads, repeated cached reads, one changed member among 400, and multiple roots sharing dates/projects.
  Measure queue discovery, preparation, publication, commit, acknowledgement, notification, and maximum asyncio scheduling gaps separately.
  Record writer hold p50/p90/p95/p99/max, foreground wait latency, query counts, queue rows produced, and first-pass versus warmed times.
  Investigate any writer hold at or above one second; acceptance requires removing that stall before delivery.
  Check that no historical query appears inside writer admission. If session publication slices dominate holds, adjust the new slice budget using these measurements.
User-controlled follow-up: reset the worktree database/search index and start DevCTL only on explicit instruction.
Confirm resolved paths inside this worktree, then inspect compute/queue progress and frontend availability.
This implementation does not claim full startup validation.
- [x] Run a final independent whole-change review. Correct findings, rerun affected tests, and commit: `test(stats): validate root compute and unified publication`.

## Plan review record

Two independent internal reviewers examined the plan and relevant code, then re-reviewed the corrections.
The race review clarified transactional member validation at completion.
The coverage review corrected coupled source-count handling, mounted daily charts, and workflow-file enqueue coverage.
Both reviewers approved the reviewed version. A subsequent user clarification removes the current-root reset and separate completion-job path.
The root already remains outdated until its final publication; no artificial invalidation is required.
Implementation tasks receive independent scoped reviews. The complete-system evidence appears below.


## Task 10 measured validation

The real-data benchmark copies metadata from the 24 GB worktree database through a read-only SQLite connection.
It writes only a separate disposable database with the new schema. It does not copy content payloads.
The measured trees contain 499 / 9,753 / 59,004 / 458,192 items and 2 / 13 / 85 / 414 native members.
Root costs match independent persisted-cost sums. Daily and weekly rows match independent Python reference sums.

| Items | Own-cost preparation, uncached / cached | Complete batch, uncached / cached |
| --- | --- | --- |
| 499 | 4.66 / 3.32 ms | 728.83 / 57.45 ms |
| 9,753 | 6.13 / 3.51 ms | 78.82 / 75.25 ms |
| 59,004 | 17.56 / 5.57 ms | 1,292.56 / 1,254.24 ms |
| 458,192 | 100.53 / 14.74 ms | 6,649.96 / 6,530.88 ms |

The first notification includes 671 ms of cold serializer/provider initialization on a worker thread.
The benchmark exposed a prior 606 ms main-loop stall. Notification payload preparation now runs off-loop.
WebSocket sends remain on the main loop. The final measured maximum scheduling gap is 15.58 ms.
Actual background writer ownership p50 / p90 / p95 / p99 / max is 2.30 / 2.54 / 3.78 / 6.32 / 10.58 ms.
Foreground admission wait p99 / max is 0.83 / 8.73 ms. Commit maximum is 0.136 ms.
No historical item query runs under writer ownership. The 256-session publication limit remains suitable.

The 400-member fixture reads 400 own costs initially, zero when cached, and one after changing one member.
Five roots sharing a project and dates converge in one additional captured batch.
The full benchmark produces 15 queue rows and leaves zero rows after acknowledgement.
Detailed per-phase timings and execute/executemany call counts remain in the task report's disposable benchmark output.

Limits: these are metadata-only measurements on a warmed filesystem, with a synthetic foreground admission probe.
They are not full startup, browser, provider ingestion, or production-load measurements.
An initial fixture timestamp conversion error is corrected only in the disposable database and verified against read-only source samples.
No actual worktree database migration, reset, search-index operation, or DevCTL startup is performed.
The final independent review identifies two integration defects. Its single correction wave resolves both.
Absent queued roots no longer block surviving work. Workflow REST and CLI responses overlay published agent costs without modifying source envelopes.
The scoped correction review finds no new Critical or Important issue. The fresh whole-suite run at `d0a378b8` passes.


## Validation observations

The source-only test helpers preserve historical fallback and omit false tree-completion declarations.
Real provider/coordinator tests retain compute versions, source guards, acknowledgement ordering, and root-last readiness.

An earlier whole-suite run stalls in the executor test group. The stall is not reproduced in isolated, repeated, or ordered-prefix checks.
A subsequent full run at `9412df99` passes 10,587 tests with 28 skips.
The fresh final run at `d0a378b8` passes 10,591 tests with 28 skips in 499.87 seconds.
Both runs use temporary executor-only diagnostics and faulthandler. The original stall remains unreproduced.
The diagnostic plugin remains temporary and does not ship.

Known display limitation: the unchanged Unassigned workflow section sums subtree-inclusive agent totals.
Nested unassigned workflow agents can overlap in that displayed total. Published workflow and phase totals use disjoint regions.


## Final verification

- Backend: `TWICC_DATA_DIR=$PWD PYTHONPATH=/tmp:$PWD uv run pytest -q -p task10_full_diagnostics -o faulthandler_timeout=30`: 10,591 passed, 28 skipped, exit 0.
- Frontend, run without concurrent backend tests: `npm --prefix frontend test`: 3,083 passed, exit 0.
- Lint: `uvx ruff check` on all 64 surviving Python files changed by this implementation: all checks pass.
- Opt-in benchmark contract: two tests pass, including zero warmed own-cost reads and one changed member among 400.
- Backend emits three RuntimeWarnings in unchanged Codex SDK/shell-notice mock tests. They remain recorded validation noise.
- Every task receives an independent review. The final review has two Important findings; one correction wave resolves both, followed by an approved scoped review.
- DevCTL stays stopped. The worktree database and search index remain intact. No production database migration or real startup run occurs.
