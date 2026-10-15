# Compute Selection and Content Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove repeated history scans, content reads, and compute-inventory polling while preserving foreground application priority.

**Architecture:** Resolve known tool IDs through history facts and reuse committed cold metadata. Select one native root on demand, then retain only its active tree and bounded deferred state. A separate compute-work signal gates periodic content maintenance without blocking foreground plaintext requests.

**Tech Stack:** Python 3.13+, Django 6, SQLite WAL, asyncio, existing DB writer and compute processes, pytest.

**Spec:** [Compute selection and content work](2026-10-15-compute-selection-and-content-work-spec.md).

## Global Constraints

- Work in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
- Foreground application work retains priority over background compute and periodic compression.
- Native roots satisfy `type='session' AND parent_session_id IS NULL`.
- Root selection orders by `-mtime` only. The index contains `(provider, mtime DESC, compute_version)` only.
- The source stability delay remains 30 seconds. The worker pipeline retains its existing bounded capacity.
- The first periodic deadline remains startup + 1,800 seconds. Subsequent completed passes retain the 86,400-second interval.
- Preserve existing content row/byte budgets, oversized-row handling, and transaction-owned publication guards.
- Use the existing committed cold cache and its invalidation mechanism.
- Use exact tool-call equality, strict prior-line bounds, and existing provider matching rules.
- Compute completion concerns DB work. `initial_sync_succeeded` does not gate it.
- All writes use the existing writer. Read preparation and codec work stay outside the asyncio thread.
- Tests run with `TWICC_DATA_DIR=$PWD` from the worktree. Inspect the resolved DB path before ad-hoc data probes.
- Existing changes in `statistics_prepare.py` and its two test files predate this plan. Preserve them and stage task files explicitly.

## Review Focus

1. Parent evidence arrives after the child's last record: resolve the link without another child write. Task 1 tests this.
2. A session resumes between metadata preparation and publication: reject obsolete compression and retain foreground restoration. Tasks 2, 3, and 7 test this.
3. A recent descendant blocks a tree: yield after applications drain, then revisit without scanning the pending inventory. Tasks 4–6 test this.
4. A compute worker exits during recovery: periodic work remains blocked until authoritative DB work drains. Tasks 5–7 test this.
5. An event arrives around an idle transition: reconsider DB work without a missed wakeup or a zero-timeout loop. Tasks 5–7 test this.

## File and Interface Map

| Unit | Files | Responsibility |
|---|---|---|
| Launcher lookup | `providers/history_facts.py`, `providers/compute_base.py`, new `providers/launcher_lookup.py`, `providers/live_sync.py`, `providers/sessions_watcher.py` | Exact lookup, slice requests, cooperative fallback, parent-triggered recovery |
| Content preparation | `content_maintenance_task.py`, `core/content_storage/cold_cache.py` | Shared lifecycle decisions, batched readiness metadata, deferred payloads |
| Mutation handling | `core/content_storage/{state,lifecycle,references,ingestion}.py` | Separate observation invalidation from actual plaintext demand |
| Root selection | `providers/compute_selection.py`, `providers/compute_tree.py`, `core/models.py`, new migration | Indexed root selection and active-tree admission |
| Compute work state | New `providers/compute_work.py` | Event-loop-owned provider busy state and change notifications |
| Generic coordinator | `providers/background_compute_task.py`, `providers/claude_code/orchestrator.py` | Bounded selection, root progress, retries and terminal drain |
| Codex coordinator | `providers/codex/{background_compute,orchestrator}.py` | Same selection contract with rollout-specific preparation and recovery |
| Periodic scheduling | `content_maintenance_task.py`, `orchestrator.py`, `cli/run.py` | Gate periodic batches across enabled providers |

All source paths above are relative to `src/twicc/`.
Do not split the large existing compute module as a separate refactor.
Add focused helpers at existing responsibility boundaries.

## Execution and Review Rules

Execute Tasks 1–7 in order. Task 8 validates the integrated result.
Each task gets a spec-compliance review and a code-quality review before the next task starts.
Reviewers inspect behavior and query shape, not only passing test counts.

Commands below assume the worktree as cwd. Prefix each shell invocation with its absolute `cd` command.
For each commit, stage only that task's files. Add a descriptive body and the model's required co-author trailer.
Commit subjects below name the intended change; they are not substitutes for the required bodies.

### Task 1: Resolve Known Launcher IDs Without Launcher-Wide Scans

**Files:**
- Modify: `src/twicc/providers/history_facts.py` — `HistoryFactContext`, `iter_resolver_items`.
- Modify: `src/twicc/providers/compute_base.py` — `create_agent_link_from_meta`, `_recover_owned_agent_links`, full-compute publication and live-slice link recovery.
- Create: `src/twicc/providers/launcher_lookup.py` — cooperative raw fallback outside writer ownership.
- Modify: `src/twicc/providers/live_sync.py` — unresolved lookup requests in slice results.
- Modify: `src/twicc/providers/sessions_watcher.py` — enqueue fallback after commit, without awaiting it inside the watcher consumer.
- Test: `tests/test_history_fact_resolution.py`.
- Test: `tests/test_agent_link_background_upgrade.py`.
- Create: `tests/test_launcher_history_lookup.py`.

**Interfaces:**
- Reuse `HistoryFactContext.lookup_tool_call(call_id: str, *, before_line: int) -> tuple[dict, int] | None`.
- Reuse `iter_resolver_items(session_id, kind, key=None, *, before_line=...)` for exact persisted candidates and stale raw pages.
- Extend `create_agent_link_from_meta(launcher_session_id, agent_id, tool_use_id, *, history=None)` with an optional launcher context.
- The context must belong to `launcher_session_id`; never pass the child's context as its parent's history.
- Known launcher IDs preserve the first spawn in transcript order. Do not directly substitute the context's newest-call lookup for that policy.
- Add `LauncherLookupRequest(launcher_session_id: str, agent_id: str, tool_use_id: str)` in `launcher_lookup.py`.
- Add `LauncherLookupResolver(*, on_resolved)` in `launcher_lookup.py`, with `enqueue(requests) -> None` and `async run(stop_event) -> None`.
- `on_resolved(updates: tuple[AgentLinkUpdate, ...])` is an async callback using the watcher's existing link-update broadcast path.
- The watcher owns one tracked resolver task and its shutdown drain. Type imports must avoid a `compute_base` import cycle.
- Append `unresolved_launcher_lookups: tuple = ()` to `LiveSyncUpdates`; preserve it in `merge_live_updates`.
- Keep unsuccessful metadata lookup state local to one `sync_session_slice` operation.

- [ ] **Step 1: Add failing lookup and recovery tests.**

Use real `SessionHistoryFact` fixtures and record SQL with `CaptureQueriesContext`.
Assertions must establish:

```python
assert resolved.tool_use_id == requested_id
assert resolved.tool_use_line_num == expected_line
assert unrelated_source_payload_reads == 0
assert in_memory_lookup_queries == 0
assert equivalent_failed_lookup_calls_in_eleven_record_slice == 1
assert recovered_after_parent_commit_without_new_child_record
```

Cover strict `before_line`, repeated IDs, multiple call blocks on one source line, nested launchers, and authoritative current-fact absence.
For stale facts, put the matching call beyond the first 128-record page.
An interrupted or deliberately budget-limited search must remain unresolved rather than create a negative fact.
Register new same-batch evidence after an earlier miss and verify that it resolves.

- [ ] **Step 2: Run the new tests and confirm behavioral failures.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_launcher_history_lookup.py tests/test_history_fact_resolution.py -q`.
Expected: new scan-count/coalescing/retry assertions fail against the current implementation.

- [ ] **Step 3: Implement exact lookup and slice-local failed-lookup coalescing.**

Replace `_spawn_items(owner.id)` in the known-ID path with the exact fact/context reader.
Parse and validate the selected source block with existing provider predicates.
Preserve the existing source-order match policy when an ID has several candidate records.
Reuse the current source's context during compute and live ingestion.
Use persisted launcher facts for child metadata lookup.
For stale persisted launcher facts, return an unresolved request from the live writer slice.
Do not scan stale launcher history while holding the writer.
After that slice commits, the watcher calls `resolver.enqueue` and returns to its consumer.
It must not await fallback completion under `_change_lock` or within a single change-consumer turn.
The watcher starts one tracked `resolver.run(stop_event)` task and cancels/drains it before watcher shutdown completes.
The resolver deduplicates in-flight request keys and retains only their scan cursors, not a complete parent transcript.
Each read preparation loads at most 128 records in the existing read executor, outside writer ownership.
Rotate unfinished requests after each page, yield to asyncio, and check stop.
Continue until the existing source-order match is established or history is exhausted.
For first-in-transcript matching, page forward through prior-line-bounded history; preserve block order within each record.
A cancelled search remains unresolved. It creates no authoritative absence cache.
Publish a found result through `run_under_storage_write_lock`, rechecking child/launcher membership, the selected call record, and existing link identity.
This publication reads one selected record, not the launcher history. Use the existing update broadcast after publication.
Full compute may leave a stale-parent lookup unresolved; parent publication retries against newly current facts.
The resolver is process-local. A restart recovers unresolved links from existing child metadata and parent evidence.
It needs no durable lookup queue. Drain any submitted read/publication operation before releasing owned task resources.
Do not add a process-wide negative lookup cache.

Move the equivalent child metadata attempt outside the per-record repetition in a live slice.
An attempt that depends on newly registered same-source evidence must retry after that evidence changes.
Use existing parent-side recovery when parent call facts become available.
Pass the parent's in-memory task evidence on live ingestion, and current exact persisted facts on full-compute publication.
Read existing child sidecar metadata to discover unresolved children; recovery does not depend on another child record.
At full parent-compute publication, reconcile unresolved child metadata against the newly published parent facts.
Recover link writes through the existing writer and existing link-update broadcast path.

- [ ] **Step 4: Verify lookup behavior and provider link regressions.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_launcher_history_lookup.py tests/test_history_fact_resolution.py tests/test_history_fact_compute_apply.py tests/test_agent_link_background_upgrade.py tests/test_claude_agent_runs.py tests/test_codex_subagent_links.py -q`.
Expected: PASS. SQL contains exact fact-key predicates and no content substring scan for known IDs.
`test_stale_launcher_fallback_releases_writer_between_pages` queues a foreground write during a large missing-ID fallback.
Assert it commits before fallback exhaustion, and assert no fallback page query executes under writer ownership.
Add `test_other_source_ingests_before_launcher_fallback_exhausts` using the actual watcher change consumer.
Assert the second source commits while the first lookup still has unread pages.
Add shutdown coverage proving resolver cancellation drains its submitted read and leaves no owned task running.

- [ ] **Step 5: Commit this task.**

Subject: `perf(history): resolve launcher ids through exact history facts`.

### Task 2: Reuse Cold Metadata and Read Payloads Only When Needed

**Files:**
- Modify: `src/twicc/content_maintenance_task.py` — `_owners_cold`, `_metadata_proof`, `prepare_compression_batch`, `prepare_promotion_batch`.
- Modify if required: `src/twicc/core/content_storage/cold_cache.py` — expose existing batched metadata reuse without duplicating its cache.
- Test: `tests/test_content_cold_cache.py`.
- Test: `tests/test_content_maintenance.py`.
- Create: `tests/test_content_maintenance_reads.py`.

**Interfaces:**
- Consume existing `cached_root_decisions(session_ids, now, active_ids)`.
- Keep the existing compression/promotion prepared-batch return types and publication entry points.
- Introduce a batch-local owner-readiness mapping inside compression preparation, keyed by owner ID.
- Its values include the existing extraction/compute readiness and root publication metadata; payloads are excluded.

- [ ] **Step 1: Add failing query and payload-read tests.**

Build 100 contents sharing two owners and repeat preparation after warming the lifecycle cache.
Instrument selected SQL columns and content-reader calls, not only Python attribute access.
Assertions:

```python
assert readiness_owner_reads_do_not_scale_with_content_count
assert repeated_lifecycle_cache_reads == 0
assert payload_reads_for_conforming_zstd == 0
assert payload_reads_for_unchanged_no_gain == 0
assert payload_reads_for_conforming_plain_promotion == 0
assert selected_content_ids == set(selected_content_ids)
```

Test a committed resume, active-session protection, and crossing the cold age threshold during a long pass.
Test a row where current and original references are identical.
Retain tests for malformed representation metadata, missing references, and one oversized payload.

- [ ] **Step 2: Run the tests and confirm redundant-read failures.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_reads.py -q`.
Expected: FAIL on the current per-content owner reads or eager payload reads.

- [ ] **Step 3: Implement metadata-first preparation.**

Load the bounded content metadata page, then batch-load distinct owner readiness metadata.
Resolve lifecycle policy through the shared committed cold cache.
Evaluate time and current active-session protection for each preparation, using cached metadata rather than frozen decisions.
Reuse owner readiness only within the prepared batch; publication retains authoritative existing guards.

Decide representation changes before querying `payload`.
Load selected payload IDs in bounded groups under existing row/byte limits.
For promotion, omit conforming plaintext IDs from the payload fetch.
For compression, omit conforming compressed IDs and unchanged no-gain attempts.
Preserve detection of invalid plain/dictionary combinations and incomplete bindings.
Do not let deferred model-field access create one hidden payload query per skipped row.

- [ ] **Step 4: Verify content preparation and publication races.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_reads.py tests/test_content_maintenance.py tests/test_content_cold_cache.py tests/test_content_storage_admission.py -q`.
Expected: PASS. A lifecycle commit between prepare and publish prevents obsolete compression publication.

- [ ] **Step 5: Commit this task.**

Subject: `perf(content): reuse owner metadata and defer unused payloads`.

### Task 3: Separate Storage Epoch Changes From Plaintext Requests

**Files:**
- Modify: `src/twicc/core/content_storage/state.py` — `invalidate_roots`.
- Modify: `src/twicc/core/content_storage/lifecycle.py` — `lifecycle_change` and lifecycle callers.
- Modify: `src/twicc/core/content_storage/references.py` — `bind_persisted_items`.
- Modify: `src/twicc/core/content_storage/ingestion.py` — ingestion promotion integration.
- Test: `tests/test_content_root_state.py`, `tests/test_content_ingestion.py`, `tests/test_content_contains_gate.py`, `tests/test_content_history_lifecycle.py`.

**Interfaces:**
- `invalidate_roots(root_ids: Iterable[str], *, promotion_targets: Iterable[str] = ()) -> None` fences mutations and requests promotion only for explicitly supplied targets.
- Existing `request_root_promotion(session_id, *, priority=None)` remains the demand notification interface.
- Existing lifecycle cache invalidation still runs after commit.

- [ ] **Step 1: Add failing hot-append and real-demand tests.**

Add `test_hot_append_restart_does_not_recover_plaintext_scan` and `test_interrupted_resume_still_recovers_after_restart`.
Assert an ordinary hot append increments the required epoch but sends no whole-session promotion request.
Restart the maintenance scheduler after that append and assert recovery still schedules no traversal.
Assert a hot-to-hot activity timestamp update sends no such request.
Assert reuse of an already compressed reference becomes plaintext through ingestion without a redundant traversal.
Assert a real cold-to-hot transition restores the resumed session, without eager subagent restoration.
Assert an interrupted real request remains discoverable through existing recovery metadata.
Assert the scoped substring gate accepts a fully plaintext hot session after an epoch change.

- [ ] **Step 2: Confirm the new demand-count tests fail.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_root_state.py tests/test_content_ingestion.py tests/test_content_history_lifecycle.py -q`.
Expected: new zero-redundant-demand assertions fail before the change.

- [ ] **Step 3: Make demand explicit at each mutation boundary.**

Remove implicit `promotion_targets=None -> roots` behavior.
Audit every `invalidate_roots` caller and classify its actual plaintext obligation.
Binding new references fences observations without scheduling a whole-session traversal when ingestion satisfies those references.
Lifecycle handling compares policy before and after mutation to detect a genuine cold-to-hot transition.
Retain explicit demand for unresolved legacy storage and interrupted restoration.
Preserve on-commit wakeups and transactional epoch updates.

Carry a valid root plaintext certificate forward within the ingestion/lifecycle transaction when the mutation preserves plaintext.
Capture whether `plaintext_verified_epoch == content_state_epoch` before the first mutation.
When ingestion has established plaintext for every new/replaced required reference, advance the previously valid certificate to the final epoch.
Hot-to-hot lifecycle-only changes can carry the same certificate forward.
A descendant binding does not invalidate an otherwise unchanged root-only plaintext certificate.
Do not carry an already stale certificate or certify a cold-to-hot restoration before it finishes.
The final epoch assignment stays conditional on the transaction's observed root identity and generation.
`pending_root_page` therefore retains genuine interrupted demands without rediscovering each certified hot append as demand.
For an already stale but fully plaintext root, `recovery_promotion_targets` first uses `session_plaintext_metadata`.
It excludes that root from payload traversal when the scoped metadata query proves plaintext; it does not silently certify missing or legacy references.
Do not replace publication guards with a cached policy result.

- [ ] **Step 4: Verify demand, search availability, and recovery.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_root_state.py tests/test_content_ingestion.py tests/test_content_contains_gate.py tests/test_content_history_lifecycle.py tests/test_content_maintenance.py -q`.
Expected: PASS. Repeated hot appends do not invoke the full-session promotion scanner.

- [ ] **Step 5: Commit this task.**

Subject: `perf(content): request plaintext scans only for actual demand`.

### Task 4: Select One Indexed Root and Retain Active-Tree Admission

**Files:**
- Modify: `src/twicc/core/models.py` — `Session.Meta.indexes`.
- Create: `src/twicc/core/migrations/0161_session_compute_root_index.py` (verify the next migration number at execution).
- Modify: `src/twicc/providers/compute_tree.py` — replace whole-root candidate loading.
- Modify: `src/twicc/providers/compute_selection.py` — separate root selection from active-tree admission.
- Test: `tests/test_compute_tree.py`, `tests/test_compute_age_selection.py`.
- Create: `tests/test_compute_root_selection.py`.

**Interfaces:**
- `select_outdated_root(provider: str, compute_version: int, *, cutoff: float, excluded_root_ids=()) -> str | None` performs one indexed `LIMIT 1` root query.
- Add `TreeSelection(source_id: str | None, required_pending: bool, next_eligible_at: float | None)` as a `NamedTuple` in `compute_selection.py`.
- `select_tree_source(root_id: str, compute_version: int, *, cutoff: float, outstanding=(), excluded_source_ids=(), forced_source_ids=()) -> TreeSelection` refreshes one admitted tree.
- `next_root_eligibility(provider: str, compute_version: int, *, cutoff: float, excluded_root_ids=()) -> float | None` returns the earliest recent-root `mtime + 30`, or None.
- This deadline probe reads one mtime ordered ascending among outdated recent native roots; it runs only when no eligible root is available.
- `TreeSelection.required_pending` distinguishes a blocked tree from a finished tree. `next_eligible_at` reports the blocked level's earliest source-age deadline.
- Retry deadlines remain coordinator-owned monotonic values; source-age deadlines use Unix wall time.
- Preserve `tree_completion_context(...)` and its successful application/completion contract.
- Coordinators own the current root ID and separate root deferrals from member exclusions.

- [ ] **Step 1: Add failing candidate-query and tree-admission tests.**

Construct roots, subagents, NULL/current/old versions, equal mtimes, recent members, and small exclusions.
Assert native roots only, NULL-or-lower version filtering, `ORDER BY mtime DESC`, and `LIMIT 1`.
Do not assert deterministic ID order for equal mtimes.
Assert the selected root's other members remain eligible after root admission.
Add `test_only_recent_root_wakes_at_deadline_without_notification` and `test_recent_descendant_reports_deadline_not_completion`.
Assert deepest-first, descending member mtime, root-last, and descendant application acknowledgements.
Assert a blocked tree yields only after outstanding applications drain.
Refresh membership before subsequent admission and cover a newly discovered descendant.

- [ ] **Step 2: Confirm the new bounded-query tests fail.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_root_selection.py tests/test_compute_age_selection.py -q`.
Expected: FAIL on the missing single-root interface or old whole-inventory query shape.

- [ ] **Step 3: Add the index and bounded selector interfaces.**

Use index name `idx_session_compute_root` with fields `['provider', '-mtime', 'compute_version']`.
Use condition `Q(type='session', parent_session__isnull=True)`.
The migration adds only this Session index.
Preserve the existing custom migration backend's targeted constraint-check behavior.

Use a positive root predicate, provider equality, NULL-or-lower compute version, age cutoff, and small root exclusions.
Do not add ID to ordering or index keys.
Keep tree loading and per-level application fences inside `select_tree_source`.
Do not resolve thousands of unrelated source IDs to roots on every admission.
For an admitted tree, inspect its members only.

- [ ] **Step 4: Verify the actual ORM SQL and migration behavior.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_root_selection.py tests/test_compute_tree.py tests/test_compute_age_selection.py -q`.
Expected: PASS.
In the query test, execute `EXPLAIN QUERY PLAN` on Django's parameterized SQL.
Assert use of `idx_session_compute_root` and absence of a temporary ORDER BY B-tree.
Exercise most-old, few-old, and no-old root distributions, with small ID exclusions.
Use an isolated test DB for applying the migration; inspect SQL for Session-only index creation and no global FK check.

- [ ] **Step 5: Commit this task.**

Subject: `perf(compute): select native roots through a partial index`.

### Task 5: Remove Generic Inventory Polling and Expose Actual Compute Work

**Files:**
- Create: `src/twicc/providers/compute_work.py`.
- Modify: `src/twicc/providers/background_compute_task.py`.
- Modify: `src/twicc/providers/claude_code/orchestrator.py`.
- Modify: `src/twicc/orchestrator.py` — provider start/stop integration.
- Modify: `src/twicc/providers/sessions_watcher.py` — committed source notifications.
- Modify: provider initial-sync/compute-invalidation callers discovered by tracing compute-version writes; notify after commit when they can run concurrently with compute.
- Test: `tests/test_background_compute_recovery.py`, `tests/test_background_compute_retries.py`.
- Create: `tests/test_compute_work.py`, `tests/test_compute_selection_wakeups.py`.

**Interfaces:**
- `ComputeWorkState` is event-loop-owned, with `set_pending(provider: str, pending: bool) -> None`, `has_pending() -> bool`, and `changed: asyncio.Event`.
- `set_pending` sets `changed` only on a state change. There is no per-content counter or generation.
- Registry owns one `ComputeWorkState` and exposes it to both provider coordinators and content maintenance.
- Extend `start_background_compute_task(ctx, initial_done=None, *, work_state)` to consume this state.
- `ComputeWorkState.register_source_wakeup(provider: str, callback) -> None` installs one event-loop callback; `unregister_source_wakeup(provider: str, callback) -> None` removes that callback if still installed.
- `ComputeWorkState.notify_source_changed(provider: str, session_id: str) -> None` dispatches through its owner loop with `call_soon_threadsafe`; the callback sets the coordinator's local selection event.
- Registry supplies this shared object to watchers and coordinators. Register before the initial DB read; unregister after drain on exit.
- Watcher `sync_and_broadcast` notifies after a committed slice changes source state. Concurrent invalidation writers use `transaction.on_commit`.
- Boot initial sync precedes the initial compute read; a concurrent later initial-sync producer must notify its committed source additions.
- Consume Task 4's separate root and tree selectors.

- [ ] **Step 1: Add failing event-driven coordinator tests.**

Assert no repeated `id IN(initial_subagent_inventory)` queries while waiting.
Assert recent root waits until its 30-second deadline, while an older eligible root proceeds.
Assert failed/superseded work retries, worker recovery retains pending state, and successful completion waits for applications to drain.
Assert admitted sources never duplicate while worker status and writer outcomes arrive in either order.
Assert source notification wakes an otherwise idle selector, including a notification during the idle transition.
Assert progress counts roots only and confirms current versions from DB.
Add `test_initial_done_waits_for_completed_or_explicitly_blocked_roots`, with one eligible unfinished tree and one recent deferred tree.
Assert the metadata snapshot alone does not release the search barrier.
Add notification tests for registration before initial read, idle transition, and callback removal after teardown.

- [ ] **Step 2: Confirm failures against current polling behavior.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_work.py tests/test_compute_selection_wakeups.py -q`.
Expected: FAIL on missing state/notification interfaces or inventory-wide polling.

- [ ] **Step 3: Replace polling with on-demand admission and bounded waits.**

Keep one active native tree; fill the existing two-source pipeline within its deepest unfinished level.
When no active tree remains, query one root with Task 4's selector.
Apply root/member age checks before admission; retain only blocked roots/members and their actual retry deadlines.
Process writer outcomes and worker statuses before the next admission.
After a blocked tree drains, defer it and choose another root.
Refresh source state at admission instead of keeping a full pending source inventory.

Wait on outcomes, relevant source notifications, stop, or the earliest eligibility/retry deadline.
Retain the existing bounded worker-status transport bridge where it cannot provide an awaitable notification.
A short transport-status check must not trigger a full candidate/classification query.
Before declaring idle, query authoritative pending root state again after draining outcomes.
Use the same native-root and NULL-or-lower-version predicate for selection and normal completion checks.
Do not retain the old `exclude(compute_version=current)` predicate for completion; it also includes higher versions.
Newly discovered source work must wake selection; wire both source ingestion and compute-invalidating paths.

Keep startup progress separate from selection state.
A one-time root-only cohort snapshot may retain existing fixed-total UI semantics; do not retain all descendant IDs.
Confirm affected roots in bounded groups after outcomes and relevant external updates.
Use a count or bounded final confirmation for completion, not a once-per-second whole-cohort query.
Preserve the existing `initial_done` startup-classification/search barrier with a root-only initial classification cohort.
Classify a root after its tree completes, or after an explicit recent-source deferral, retry failure, or invalid-tree outcome.
Do not classify an eligible unfinished tree from metadata alone.
Set `initial_done` once every initial cohort root has one of these outcomes.
Track only root cohort membership; derive descendant progress from the active tree and its outcomes.
`initial_done` does not mean all compute work is finished.
During deferred waits, use Task 4's deadline probe and `TreeSelection`; no source notification is needed to reach an unchanged age deadline.

Set provider work pending before initial admission/classification starts.
Keep it pending through age waits, backoff, worker replacement, and application drain.
Set it idle only after the final authoritative no-work check.
Provider start marks its work pending before maintenance can run; disabling a provider removes its gate only after teardown drains.

- [ ] **Step 4: Verify retry, startup progress, cancellation, and event ordering.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_work.py tests/test_compute_selection_wakeups.py tests/test_background_compute_recovery.py tests/test_background_compute_retries.py tests/test_compute_age_selection.py -q`.
Expected: PASS. Recovery and source-age waiting make no inventory-wide periodic queries.

- [ ] **Step 5: Commit this task.**

Subject: `perf(compute): drive root admission from outcomes and deadlines`.

### Task 6: Apply Bounded Selection to the Codex Coordinator

**Files:**
- Modify: `src/twicc/providers/codex/background_compute.py` — `_load_stale_candidates`, `CodexComputeCoordinator.run`, progress/classification helpers.
- Modify: `src/twicc/providers/codex/orchestrator.py` — compute work state and source wakeups.
- Test: `tests/test_codex_compute_recovery.py`, `tests/test_codex_recompute_persistence.py`, `tests/test_compute_selection_wakeups.py`.

**Interfaces:**
- Codex receives the same registry-owned `work_state` and Task 4 selectors.
- Replace recurring `_load_stale_candidates(all sessions)` calls with candidate loading scoped to the selected native tree.
- Keep existing `PreparedCandidate`, `DeferredCandidate`, `FailedCandidate`, rollout migration leases, and applied-event interfaces.
- Register Codex's source-wakeup callback through Task 5's shared state; retain its existing migration-specific event pump.
- Forced rebuild and unavailable-source recovery continue to identify their affected roots through their existing requests.

- [ ] **Step 1: Add failing Codex bounded-selection and recovery tests.**

Assert each normal selection fetches one root, then only that tree's required candidates.
Assert migration/rebuild/deferred-source decisions still hold their existing source locks and retry automatically.
Assert forced rebuild of an otherwise current root remains eligible.
Assert an unavailable source can recover and newly available evidence triggers admission.
Assert pending state remains true during migration-lock waits, worker replacement, and writer drain.
Assert root progress and root-last enqueue happen only after required member applications finish.

- [ ] **Step 2: Confirm the repeated-candidate-load test fails.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_selection_wakeups.py tests/test_codex_compute_recovery.py -q`.
Expected: new bounded-selection assertions fail against the recurring full candidate load.

- [ ] **Step 3: Integrate the shared selection contract without bypassing Codex preparation.**

Retain one-time startup migration classification if required for existing progress details.
Page its metadata/filesystem work outside the asyncio thread; do not repeat it for every admission.
Use the native root's mtime for normal root ranking, not a descendant's mtime.
Use Task 4's per-member source-age checks and refresh the selected tree's source data before preparation.
Do not pass a complete `eligible_source_ids` inventory into the shared selector.
Keep outstanding commands, retry roots, forced rebuild roots, and externally unavailable recovery state scoped to actual work.

Keep the existing migration and applied wake pumps.
Use those events and real retry/stability deadlines instead of rereading every stale session each second.
Retain an explicit exceptional recovery query for unavailable sources if their authoritative existing state requires it.
That query selects bounded roots and does not become an all-source scan on each loop.
Normal outdated selection remains NULL-or-lower-version native roots.
A recoverable current-root request uses the explicit recovery path, not compute-version downgrading.

Drive `work_state` from pending DB/recovery work, not from worker presence or the early `initial_done` event.
An enabled provider in retry remains pending.
The lifetime Codex coordinator can remain alive with `work_state` idle after all work drains.

- [ ] **Step 4: Verify Codex scheduling and rollout integration boundaries.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_codex_compute_recovery.py tests/test_codex_recompute_persistence.py tests/test_compute_selection_wakeups.py tests/test_compute_age_selection.py -q`.
Expected: PASS. No repeated provider-wide candidate list in steady selection.
If rollout migration code changes, also run the existing real-binary integration suite when its runtime is available.
Use only its throwaway `CODEX_HOME`, with `TWICC_CODEX_INTEGRATION=1`.

- [ ] **Step 5: Commit this task.**

Subject: `perf(codex): select compute trees without stale inventory polling`.

### Task 7: Gate Periodic Maintenance on Unfinished Compute Work

**Files:**
- Modify: `src/twicc/content_maintenance_task.py` — `start_content_maintenance_task` and periodic scheduling.
- Modify: `src/twicc/cli/run.py` — pass registry-owned compute work state.
- Modify: `src/twicc/orchestrator.py` if necessary for enabled-provider state transitions.
- Create: `tests/test_content_maintenance_compute_gate.py`.
- Test: `tests/test_content_maintenance.py`.

**Interfaces:**
- `start_content_maintenance_task(stop_event: asyncio.Event, *, compute_work: ComputeWorkState) -> None`.
- Consume `has_pending()` and `changed` on the asyncio thread.
- Retain the existing foreground promotion wakeup and writer coordinator priorities.

- [ ] **Step 1: Add failing scheduling tests using a controlled clock.**

Assert these exact schedules:

| Situation | Expected behavior |
|---|---|
| Compute finishes at startup + 600 seconds | Periodic work starts no earlier than +1,800 seconds |
| Compute finishes at +2,400 seconds | Overdue periodic work can start at +2,400 seconds |
| One provider finishes; another waits for source age or retry | Periodic work remains blocked |
| Worker absent while recovery owns unfinished work | Periodic work remains blocked |
| New compute begins during compression | Finish the bounded current step; pause before the next batch |
| Foreground plaintext request arrives during deferral | Service the request through the existing foreground path |
| Periodic pass completes | Next deadline is completion + 86,400 seconds |

Also assert stop wakes every wait, provider enable/disable updates eligibility, and an overdue blocked deadline does not create a busy loop.
Place an event between state inspection and waiting to prove no lost wakeup.

- [ ] **Step 2: Confirm the current ungated scheduler fails.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_compute_gate.py -q`.
Expected: FAIL because periodic preparation can start while provider compute remains pending.

- [ ] **Step 3: Add the gate to the whole periodic pass.**

Check compute work before periodic recovery/compression and before every subsequent bounded batch/publication grant.
If compute restarts after preparation, retain or discard the prepared batch safely under existing guards, then pause publication/admission.
Resume the pass cursor after compute drains; do not silently count a paused pass as completed.
Apply the same gate to the shared periodic directory-cleanup entry point.
Keep its existing eligibility rules.

Keep explicit foreground plaintext demands serviceable while the periodic pass waits.
Startup recovery of genuine interrupted plaintext demand remains available.
Do not turn opportunistic periodic recovery into an ungated second heavy pass.
Retain the dedicated codec thread, existing writer leases, and cooperative `await asyncio.sleep(0)` boundaries.

For each idle wait: clear the relevant change event, recheck authoritative state/deadlines, then wait on change, foreground wakeup, or stop.
When the periodic deadline is overdue but compute remains pending, wait for a state change rather than an already expired timer.
Do not use `compute_done`, `initial_done`, or `initial_sync_succeeded` as the periodic gate.

- [ ] **Step 4: Verify both providers and foreground priority.**

Run: `TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_compute_gate.py tests/test_content_maintenance.py tests/test_compute_work.py tests/test_content_storage_admission.py -q`.
Expected: PASS. No periodic compression preparation runs during a pending compute interval; foreground restoration still runs.

- [ ] **Step 5: Commit this task.**

Subject: `perf(maintenance): defer periodic content work until compute drains`.

### Task 8: Validate Integrated Behavior and Performance

**Files:**
- Modify only if a regression test is needed: the focused tests from Tasks 1–7.
- Record execution evidence in this plan's validation notes, not in permanent benchmark scripts or review reports.

**Interfaces:**
- Consume the completed lookup, content preparation, selectors, provider coordinators, and periodic gate.
- Existing DB writer telemetry distinguishes lease hold duration from elapsed multi-slice processing time.

- [x] **Step 1: Run the combined regression suite.**

Run all focused commands from Tasks 1–7 as one pytest invocation, deduplicating paths.
Also run `tests/test_statistics_activity.py`, `tests/test_statistics_costs.py`, and `tests/test_nested_agent_compute.py`.
Expected: PASS. The existing statistics query fixes remain intact.
Run `uvx ruff check` against the changed Python files. Inspect `git diff --check`.
Fix only issues caused by this work; report unrelated baseline failures separately.

- [x] **Step 2: Compare read-only probes on isolated representative data.**

Use worktree data only after confirming its resolved path.
If a fresh DB copy is needed, use the established devctl SQLite backup method without starting servers.
Do not benchmark writes against the main data directory.
Measure known-ID lookup with many unrelated calls and an eleven-record failed-lookup slice.
Measure cold readiness and payload fetches for shared owners, conforming compressed rows, and conforming plain rows.
Run final ORM root queries for most-old, few-old, and no-old distributions with small exclusions.
Record SQL counts, payload bytes read, elapsed time, and query plans.
Compare against the spec's observed baseline; do not convert local timings into portable guarantees.

- [x] **Step 3: Exercise event-loop and writer behavior under mixed work.**

Use a disposable test database with one small tree and one large multi-level tree.
Run foreground synthetic writer jobs and an asyncio heartbeat while compute outcomes and content batches flow.
Assert foreground jobs complete before further background grants when foreground demand is pending.
Assert the heartbeat continues during preparation and codec work.
Report heartbeat delay percentiles and actual writer-hold p50/p90/p95/p99/max separately.
Inspect outliers; do not classify a session's complete application time as one writer hold.
Include the stale-history fallback path in hold measurements, not only the indexed fast path.
If it still produces multi-second holds, report the blocker and resolve the fallback placement before accepting the task.

- [ ] **Step 4: Run the whole-branch adversarial code review and resolve findings.**

Reviewers compare implementation with this plan and its spec.
Require evidence for both coordinators, exact lookup, zero skipped-row payload reads, genuine restoration, and no periodic overlap.
Re-run the affected tests after each correction, then obtain reapproval.
A fresh live devctl reset/start requires the user's instruction; it is not part of this task's automatic checks.

- [x] **Step 5: Commit integrated regression corrections and report results.**

Subject when changes exist: `test(compute): validate bounded selection and maintenance isolation`.
Report tested behavior, measured costs, unresolved risks, and any live validation still awaiting the user's requested run.

## Spec Coverage

| Spec section | Plan tasks |
|---|---|
| 2. Exact lookup and failed-search recovery | 1, 8 |
| 3. Shared lifecycle metadata and deferred payloads | 2, 8 |
| 4. Epoch fencing and actual plaintext demand | 3, 8 |
| 5. Indexed selection, tree ordering, wakeups, progress | 4, 5, 6, 8 |
| 6. Cross-provider periodic maintenance gate | 5, 6, 7, 8 |
| 7. Behavioral and performance validation | All tasks, integrated in 8 |

## Validation Notes

Status: approved after adversarial review by two independent reviewers.

The selection/lifecycle reviewer approved the second version.
The history/content reviewer approved the third version after watcher-starvation coverage was added.

Tasks 1–7 are implemented and reviewed. Integrated Task 8 evidence follows.
The final whole-plan review remains pending with the controller.

### Task 8 integrated validation — 2026-10-15

Execution base: `f924fbdf`. Integrated Tasks 1–7 base: `9738e1a1`.
Validation uses this worktree: `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
No server starts, resets, package installations, main database copies, or instance migrations occur.
The existing statistics changes remain intact.
The controller owns the final independent whole-plan review and its correction wave.

#### Regression corrections

- A user/queue-only live slice scans 2,001 sidecars without launcher evidence: 31.50 ms and 35 SQL calls.
- Live metadata retry now requires a new launcher tool call in the same slice.
- The comparable no-launcher slice uses 31 SQL calls and 14.10 ms after the correction.
- Child-local sidecar lookup remains available. Full parent-publication retry remains unconditional.
- Existing late-sidecar, same-batch launcher, and stale-first-spawn regressions remain covered.
- The first combined run exposes Codex restart recovery: an outdated child has a current repaired root.
- Startup classification finds the child. Normal root selection then discards that evidence and selects nothing.
- Codex now retains only resolved root IDs from its existing startup child classification.
- The admitted-tree selector refreshes members. It removes the root hint after all required applications drain.
- The correction neither retains a provider-wide source inventory nor lowers a root's compute version.
- The existing forced-rebuild/discovery test covers both retained-coordinator and restarted-coordinator paths.
- It also checks final root statistics publication and exactly recomputed member IDs.
- The broad suite exposes an old scheduler test loader mock that rejects the new `source_ids` argument.
- Update its test-only signature/filter. All 23 scheduler tests pass in 6.28s with their writer-ack assertions intact.
- The broad suite exposes metadata retry before the agent-run hooks on the spawn line.
- Retry now runs after the existing second pass. Per-line hooks retain their established link ordering.
- Ruff reports 12 captured-loop-variable findings in the new coordinators and 8 fixture-import findings.
- Bind captured selector arguments and expose the imported fixture through its module. Changed-file Ruff is clean.

TDD evidence:

```text
pytest tests/test_launcher_history_lookup.py::test_live_slice_without_launcher_evidence_does_not_scan_tree_sidecars -q
RED: assert 1 == 0 (tree sidecar scan count).
GREEN: covered by the 78-test lookup/nested/history run and the later 237-test hook run.

pytest 'tests/test_root_compute_completion.py::test_codex_discovery_during_forced_rebuild_keeps_tree_eligible[True]' -q
RED: TimeoutError waiting for the late child's compute_version after restart; reproduced alone.
GREEN: both restart variants pass, 2 passed in 9.55s.

pytest tests/test_agent_run_plumbing.py::test_live_hook_runs_after_result_link_and_before_tool_use_link -q
RED: assert True is False for AgentLink visibility in the spawn-line hook; reproduced alone.
GREEN: 237 affected lookup/hook/nested/history tests pass in 43.34s.
```

The first root-hint attempt passes an argument through a zero-argument fixture reader and fails with TypeError.
Use the established closure-based reader contract. This fixture error supplies no additional defect evidence.
The intermediate 145-test command records 140 passes and 5 failures before this reader correction.
Four failures report the argument TypeError. The fifth times out after the same startup failure.
The final combined command covers these tests after the correction.
Task 6's original missing-API RED remains fixture evidence. These integrated regressions provide behavioral RED evidence.

#### Commands and suite results

All pytest commands below start with `cd <worktree> && TWICC_DATA_DIR=$PWD uv run`.
The inherited `VIRTUAL_ENV` warning is expected. The commands use the worktree environment.

Combined focused command, deduplicated from Task 1–7 reports and including statistics/nested regressions:

```sh
pytest tests/test_agent_link_background_upgrade.py tests/test_background_compute_recovery.py tests/test_background_compute_retries.py tests/test_claude_agent_runs.py tests/test_codex_compute_recovery.py tests/test_codex_recompute_persistence.py tests/test_codex_rollout_migration.py tests/test_codex_subagent_links.py tests/test_compute_age_selection.py tests/test_compute_continuation.py tests/test_compute_project_notifications.py tests/test_compute_root_selection.py tests/test_compute_selection_wakeups.py tests/test_compute_tree.py tests/test_compute_work.py tests/test_content_cold_cache.py tests/test_content_contains_gate.py tests/test_content_history_lifecycle.py tests/test_content_ingestion.py tests/test_content_maintenance.py tests/test_content_maintenance_compute_gate.py tests/test_content_maintenance_reads.py tests/test_content_root_state.py tests/test_content_session_promotion.py tests/test_content_storage_admission.py tests/test_history_fact_compute_apply.py tests/test_history_fact_resolution.py tests/test_launcher_history_lookup.py tests/test_nested_agent_compute.py tests/test_root_compute_completion.py tests/test_session_dirs_cleanup.py tests/test_statistics_activity.py tests/test_statistics_costs.py tests/test_watcher_slice_fairness.py -q
```

- First run: 856 passed, 1 failed in 197.38s. The failure is the Codex restart regression above.
- Final combined run: 858 passed in 214.00s. This run includes the Codex restart and sidecar corrections.
- Whole suite: `pytest -q`; 10,719 passed, 28 skipped, 4 failed, 3 warnings in 581.48s.
- The broad run starts before the hook-order and scheduler-fixture corrections. It imports their pre-correction code.
- The four failures and their final covering runs are listed below. Do not report a final-revision full-suite pass.
- No second full suite runs. The controller owns final review and any required further verification.
- Broad validation is justified by coordinator, orchestrator, CLI, watcher, and writer API changes.
- The broad run imports modules before the final hook correction. Affected tests rerun afterward.
- Scheduler fixture correction: `pytest tests/test_codex_migration_scheduler.py -q`; 23 passed in 6.28s.
- Final affected command: `pytest tests/test_agent_run_plumbing.py tests/test_launcher_history_lookup.py tests/test_nested_agent_compute.py tests/test_claude_agent_runs.py tests/test_history_fact_resolution.py -q`.
- Affected result: 237 passed in 43.34s.
- Changed-file lint before the final title fixture correction: all 46 changed Python paths pass.
- Final lint includes 47 Python paths. It reports five baseline findings in the newly touched title test module.
- `git show f924fbdf:tests/test_title_auto_task.py` piped to `uvx ruff check --stdin-filename tests/test_title_auto_task.py -` reproduces all five.
- Baseline findings: four F811 fixture bindings (`env`, `session`) and PIE807 for `lambda: []`. Preserve these unrelated lines.
- `git diff --check` passes.

#### Isolated read measurements

Temporary probe: `.superpowers/task8_probe.py`; ignored and not committed.
Temporary logs/measurements: `/tmp/twicc-task8-*.log` and `/tmp/twicc-task8-metrics.json`.
Persistent evidence resides here. No benchmark code enters the repository.

The probe bootstraps `twicc.settings_test`, then invokes pytest with `-p tests.conftest`.
The initial direct plugin invocations fail before tests: missing Python module path, then Django app initialization order.
Bootstrap Django before loading the test plugin. These harness failures do not indicate product failures.

Probe command prefix:

```sh
cd <worktree> && TWICC_DATA_DIR=$PWD uv run python -c 'import os; os.environ["DJANGO_SETTINGS_MODULE"]="twicc.settings_test"; import django; django.setup(); import pytest; raise SystemExit(pytest.main(["-p","tests.conftest",".superpowers/task8_probe.py::<probe>","-q","-s"]))'
```

- `test_read_probes`: passes after the code correction; 1 passed in 10.26s.
- `test_read_only_exact_lookup_probe`: 1 passed in 6.95s.
- The resolved test DB is `file:memorydb_default?mode=memory&cache=shared`; the probe asserts this before data access.
- Fixture setup creates synthetic rows. Read probes then measure real ORM queries and real content preparation.
- Lookup-and-publication and live-slice probes write only to this disposable test DB.
- `HistoryFactContext.lookup_tool_call` is also measured separately; all its recorded statements are SELECTs.

| Operation | SQL calls | Source/content payload bytes returned | Elapsed ms |
|---|---:|---:|---:|
| Read-only exact ID, 6,000 unrelated calls | 3 | 185 | 2.955 |
| Registered in-memory exact ID | 0 | 0 | 0.006 |
| Exact ID plus real link publication | 10 | 185 | 6.086 |
| Eleven failed current-history child records | 111 | 0 | 53.422 |
| 300 rows, two shared owners, cold first access | 8 | 800,890 | 22.039 |
| Same shared-owner preparation, warm lifecycle cache | 5 | 800,890 | 21.367 |
| 100 conforming compressed rows | 4 | 0 | 3.650 |
| 100 conforming no-gain plain rows | 4 | 0 | 3.548 |
| 100 conforming plain promotion rows, both references | 17 | 0 | 6.179 |

The exact read-only lookup returns one 185-byte launcher source. It returns no unrelated sources.
The eleven-record slice reads zero launcher payloads. Its SQL count includes real ingestion and content writes.
Shared-owner preparation fetches only the 100 pending plain payloads, in one content-payload SELECT.
The other 200 rows require metadata only. The skipped-only probes verify zero payload SELECTs.
The shared-owner batch also sees one unrelated hot row from the live-slice fixture. It returns none of that row's payload.
Payload byte totals count source/content bytes returned to Python, excluding reusable dictionary assets and SQLite page reads.
The plain promotion probe selects metadata for 100 rows with identical current/original references, without returning payloads.

Spec §8 reports a 3.24-second launcher scan with 5,981 SQL calls, versus 11.7 ms/5 calls for exact lookup.
This synthetic exact read records 2.955 ms/3 calls. Data sizes and publication boundaries differ from those observations.
Spec §8 also records 82.7 ms/200 calls for 100 per-content eligibility checks.
This integrated preparation verifies 300 rows with batched owner readiness and returns payloads only when required.
It includes codecs, unlike the spec's isolated lifecycle-cache measurements. These timings are not directly comparable guarantees.

#### Final ORM root query plans

The probe creates 14,824 native roots and excludes two IDs in each final Django query.
It marks all unrelated fixture roots current before selecting. The few-old case retains one eligible root after exclusions.
The first exploratory fixture leaves an unrelated stale root eligible; discard those few-old/no-old measurements.
The corrected final fixture asserts and records the intended selected root or no root.

| Distribution | Outdated roots | SQL calls | Elapsed ms | Selected root |
|---|---:|---:|---:|---|
| most-old | 14,000 | 1 | 1.160 | `probe-13999` |
| few-old | 3 | 1 | 1.933 | `probe-0` |
| no-old | 0 | 1 | 1.863 | `None` |

All three parameterized query plans report:

```text
SEARCH core_session USING INDEX idx_session_compute_root (provider=? AND mtime<?)
```

No plan uses a temporary B-tree. All queries return zero source/content payload bytes.
The final SQL ends with `ORDER BY "core_session"."mtime" DESC LIMIT 1`.
It filters native roots, version NULL-or-less-than-current, stability cutoff, and small ID exclusions.

#### Mixed WAL writer and heartbeat measurements

- `test_mixed_probe` uses a SQLite backup of the synthetic test DB. It never copies the 24 GB worktree DB.
- The probe confirms `/tmp/pytest-of-twidi/pytest-6488/task8_probe/test_mixed_probe-0/mixed.sqlite3` before DB access.
- WAL mode is enabled on that disposable file. Every ORM worker uses its test-only database configuration.
- One small tree has 10 rows. One large tree has 9 sources, with nested AgentLink launcher levels.
- Compute applies 10 successful outcomes across 5,410 rows, using 86 preparation operations.
- Concurrent compression prepares 100 rows through real codecs and publishes four 25-row batches.
- Concurrent stale-history recovery reads 6,001 records over 47 pages, then publishes one exact selected launcher.
- The probe asserts every fallback page runs without a writer lease.
- The probe asserts every codec and compute preparation runs outside the asyncio thread.
- Forty foreground writer jobs complete during this mixed scenario.
- Every background grant asserts there is no foreground holder, queued foreground request, or foreground demand token.
- This admission assertion checks priority at every grant. Foreground work completes before another background grant under pressure.
- The 1 ms heartbeat continues while preparation and codecs run. It records 2,042 samples.
- Mixed elapsed time is 3,874.83 ms. It is not one continuous writer hold.

Instrumentation records each WRITER ResourceLease from actual grant until release.
A diagnostic correction labels awaited leases when `acquire()` returns.
Grant scheduling can occur in another task's release context; the original grant-context class labels are discarded.
Unawaited compute grants remain labeled by the writer task. The correction changes probe labels only.

| Measurement, ms | Samples | p50 | p90 | p95 | p99 | Max |
| All actual writer holds | 154 | 10.319 | 50.710 | 63.533 | 119.104 | 126.390 |
| compute writer holds | 109 | 11.145 | 50.710 | 53.801 | 64.882 | 103.181 |
| content writer holds | 4 | 119.104 | 122.877 | 122.877 | 122.877 | 126.390 |
| foreground writer holds | 40 | 5.167 | 7.881 | 9.660 | 12.693 | 113.268 |
| stale-history-fallback writer holds | 1 | 9.352 | 9.352 | 9.352 | 9.352 | 9.352 |
| Foreground completion latency | 40 | 12.738 | 112.965 | 120.607 | 125.044 | 130.619 |
| Heartbeat delay above requested 1 ms | 2042 | 0.435 | 2.164 | 3.588 | 5.266 | 104.625 |
| Stale-history page read elapsed, outside writer | 47 | 21.308 | 62.371 | 71.735 | 112.564 | 122.696 |

Outlier inspection identifies content publication as the slowest writer class: 25-row proof/readiness checks plus guarded updates.
These transaction-owned checks remain inside the writer. Codecs already complete outside the asyncio thread.
One foreground hold reaches 113.268 ms; its p99 is 12.693 ms. Compute holds reach 103.181 ms.
The fallback publishes in 9.352 ms; its historical pages never hold the writer.
No measured writer hold reaches one second. No multi-second writer hold remains in this scenario.
Heartbeat max is 104.625 ms, versus p99 5.266 ms.
Heartbeat samples lack event timestamps, so this sample cannot attribute that maximum to one execution phase.
Class-level outlier inspection does not prove a specific cause for the heartbeat maximum.
These measurements include local scheduling effects and concurrent validation processes. They give no future payload-size or hardware guarantee.

#### Scope and remaining validation

- Full-plan adversarial review and any resulting fixes remain controller-owned.
- No new Claude stale-child inventory is added. This correction targets reproduced Codex forced-rebuild restart recovery.
- No generic current-root/outdated-child reconciliation is introduced.
- No new committed-source recovery mechanism is added without a separate reproduced producer case.
- A live reset/start is not performed. It requires the user's explicit request.
- Backend changes require the user to restart their instance through `devctl.py` when they choose to validate live behavior.
- Existing migration 0161 belongs to Task 4. No new Task 8 migration is created or manually applied to an instance.

#### Completed broad-run follow-up

The broad run finishes after the code checkpoint `77cd1354`.
It records these failures from the code/test modules imported before their corrections:

| Broad-run failure | Cause | Final covering result |
|---|---|---|
| `test_agent_run_plumbing.py::test_live_hook_runs_after_result_link_and_before_tool_use_link` | Metadata retry precedes the per-line hook | 237 affected tests pass, 43.34s |
| `test_codex_migration_scheduler.py::test_worker_dispatches_next_root_only_after_writer_ack` | Test loader mock rejects `source_ids` | All 23 scheduler tests pass, 6.28s |
| `test_title_auto_task.py::test_server_owns_title_runner_before_provider_start_and_until_teardown[normal]` | Fake registry omits `compute_work` | All 15 title-auto tests pass, 4.59s |
| `test_title_auto_task.py::test_server_owns_title_runner_before_provider_start_and_until_teardown[adoption-cancel]` | Same missing field prevents the adoption phase | Same 15-test module run |

The final fixture correction adds a real `ComputeWorkState` to the fake registry.
It changes no production code. Startup, cancellation, and teardown assertions remain intact.

Final title command:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest tests/test_title_auto_task.py -q
```

The three broad-run RuntimeWarnings concern unawaited coroutines:

- `tests/test_codex_sdk_wrappers.py::test_turn_forwards_auto_review`: `AsyncMockMixin._execute_mock_call`.
- `tests/test_codex_sdk_wrappers.py::test_turn_forwards_fast_service_tier`: `AsyncMockMixin._execute_mock_call`.
- `tests/test_shell_notice_codex.py::test_turn_openings_restart_the_delay`: `BaseAgent._notify_state_change` from `tests/shell_notice_helpers.py:31`.

Those test files are unchanged since `f924fbdf`. No unrelated warning correction is included.
The five final lint findings are separately confirmed against baseline file contents, without checkout mutation.
Final `git diff --check` passes. Known broad-run failures have passing focused coverage after their corrections.
A complete full-suite run of the final revision is not claimed.


### Final review correction — Codex initialization recovery — 2026-10-15

Review base: `a5ac74e7`. The whole-plan review identifies one Important issue.
Codex cleanup clears the compute gate after an unexpected startup exit, although database roots remain outdated.

- Startup preparation now retries before worker resources start.
- Retry delay starts at one second and doubles to a maximum of 60 seconds, using the existing stop-aware helper.
- The provider remains pending throughout initialization and retry waits.
- Authoritative selection still publishes lifetime idle.
- Final cleanup clears pending only after explicit provider stop and proven teardown.
- Unexpected exit and failed teardown retain pending state.
- No compute version is lowered or cleared. Existing leases, rebuild recovery, application drain, and cancellation cleanup remain intact.

TDD command, before production changes:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_compute_recovery.py -k 'unexpected_exit or startup_failure_recovers'
```

RED: **3 failed, 1 passed, 44 deselected in 5.35s**.
Both injected startup errors escape. Successful cleanup incorrectly clears pending after unexpected exit.
GREEN, same command after the production correction: **4 passed, 44 deselected in 7.91s**.
The exit test then expands to cover explicit stop with successful or failed teardown.

The real-root regression covers both one-time progress and project-cache read failures.
It uses real worker pipes, writer application, and the periodic maintenance scheduler against isolated SQLite.
Periodic preparation remains blocked during the paused retry interval with both roots still outdated.
Both roots then reach version 123 before periodic work can prepare. The coordinator remains alive at idle.

Final focused command:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_compute_recovery.py tests/test_compute_selection_wakeups.py tests/test_content_maintenance_compute_gate.py tests/test_compute_work.py tests/test_root_compute_completion.py tests/test_codex_migration_scheduler.py tests/test_background_compute_recovery.py
```

Result: **138 passed in 82.10s (0:01:22)**, with no test warnings.
uv prints the expected inherited-environment mismatch warning and ignores that environment.
Changed-file Ruff passes for the Codex coordinator and recovery tests. `git diff --check` passes.
No new broad run occurs for this bounded correction. Earlier broad evidence retains its stated limits.
No server operation, instance migration, package installation, data reset, or main database access occurs.
The controller owns the scoped independent re-review.
Internal report: `.superpowers/sdd/2026-10-15-compute-selection-and-content-work-plan/final-fix-report.md`.


### Residual final-review correction — capture stop evidence before cleanup

The scoped review of `31065d3b` finds that I1 remains open after its first correction.
Real `_shutdown()` calls `stop_background_task()`, which sets `ctx.stop_event` during cleanup.
The previous mocked shutdown hides this side effect. Its passing result does not prove the real exit contract.

- Capture explicit-stop evidence before cleanup starts.
- Release pending during cleanup only with that capture and completed teardown.
- Keep authoritative idle publication unchanged. Add no state field or generation counter.
- Exercise actual `_shutdown()` and `stop_background_task()` after an injected unexpected exit.
- Verify real cleanup sets the stop event while unexpected exit retains pending.
- Verify explicit stop before cleanup still clears pending after teardown.
- Add separate actual task-cancellation coverage with real cleanup. Cancellation without explicit stop retains pending.

RED command:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_compute_recovery.py -k real_shutdown_preserves_unexpected_exit_pending_state
```

Result before the fix: **1 failed, 1 passed, 50 deselected in 1.93s**.
The unexpected-exit branch loses pending after real cleanup sets the stop event.
GREEN real-cleanup and four-way exit matrix: **6 passed, 46 deselected in 1.65s**.

The first combined run reports **1 failed, 70 passed in 44.58s**.
`test_codex_selection_drains_once_then_wakes_without_inventory_polling` cancels after the database version changes, before authoritative idle.
Its final assertion depends on cleanup clearing the gate.
The fixture now waits for the current version and authoritative idle before cancellation. It does not set the stop event.

Final focused command:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_compute_recovery.py tests/test_content_maintenance_compute_gate.py tests/test_compute_work.py
```

Result: **71 passed in 45.54s**.
A separate cancellation regression is added after collection of that run:

```sh
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_codex_compute_recovery.py -k real_shutdown_keeps_pending_after_cancellation_without_explicit_stop
```

Result: **1 passed, 52 deselected in 1.77s**.
These runs cover all 72 final tests from the requested three modules. No test warnings appear.
Changed-file Ruff and `git diff --check` pass.
No broad/full-suite rerun, server action, migration, data reset, package installation, or main database access occurs.
The internal final-fix report records the failed attempt, corrected fixture, real cleanup coverage, and final evidence.
