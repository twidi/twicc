# Content and Tree Aggregate Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant storage work and publish exact tree aggregates without blocking foreground writes with historical calculations.

**Architecture:** Keep the existing source compute pipeline, single application operation, writer coordinator, and durable repair journal. Add reusable storage caches and a precise contribution revision registry. Prepare aggregate values in read snapshots outside writer ownership, then publish bounded results after validating their observations.

**Tech Stack:** Python 3.13+, Django, SQLite WAL, asyncio, orjson, python-zstandard, pytest/pytest-django.

**Spec:** [Content and tree aggregate optimization](2026-10-13-content-and-tree-aggregate-optimization-spec.md).

## Global Constraints

- Keep Zstd level 3 and existing immutable provider dictionaries.
- Keep content cold rules: 15 inactive days for archived/hidden roots; 30 days otherwise. Keep scratch rules unchanged.
- Keep the 2,000-item metadata batches and existing metadata `executemany` unchanged.
- Keep item identity, exact logical strings, line numbers, current/original provenance, and external content payloads unchanged.
- Keep bounded per-provider outstanding compute and single-session application. Do not interleave source applications.
- Publish activity after each complete native root tree. Do not publish full activity after every subagent or every ten roots.
- Calculate weeks from seven UTC daily rows. Do not add a global day-readiness gate.
- Foreground work has priority. No runtime/recovery fallback performs historical sums under writer ownership.
- No schema migration, new dependency, dictionary generation, garbage collection, legacy-column removal, or vacuum is planned.
- No automatic server start/reset. Benchmarks use isolated SQLite backup copies with verified paths; they never write to the source DB.
- Run all worktree Python commands with `TWICC_DATA_DIR=$PWD`. Never use `--active` or `uv pip`.
- Preserve unrelated changes. All documentation, comments, and tests use English. Do not modify CHANGELOG.
- Each task receives independent review before its changes are applied to the target feature worktree.

## Review Focus

1. A matching dictionary PK in another database must not reuse the first database's dictionary; pin this in Task 1.
2. A backward clock change must revoke cached cold eligibility; pin this in Task 2.
3. A late child must not publish a prepared complete-tree total; pin this in Tasks 7–9.
4. Timestamp-null costs and another root's changes must invalidate relevant prepared totals; pin this in Tasks 6 and 8.
5. A crash after daily publication but before weekly publication must retain recoverable work; pin this in Task 9.

## Execution and review protocol

The current feature worktree is the analysis baseline, at documentation commit `ccb18ba7`.
Before execution, verify its actual HEAD and preserve later commits. Execution location is selected with the user.
Do not start implementation or create another worktree during this planning task.

For each task, an implementer prepares a concrete patch in an isolated checkout. A fresh reviewer checks the patch
against the spec and tests before integration into the target feature worktree. Correct findings and re-review first.
Keep commits independently testable. Modules introduced in Tasks 6–8 remain unused by production dispatch until Task 9.
Task 9 switches normal finalization and recovery together; no half-enabled aggregate runtime is accepted.

All test commands below run from the selected implementation worktree:
`cd <implementation-worktree> && TWICC_DATA_DIR=$PWD uv run pytest <listed-tests>`.
FAIL means the named behavioral assertion fails before implementation, not merely an unrelated import/configuration error.
Commit each reviewed task with a Conventional Commit subject, descriptive body, and current-model Co-Authored-By trailer.
The assertion fragments below belong to the named tests. Each test creates its described rows and spies locally.
Counters cover preparation and publication, including work outside the writer transaction.

## File and interface map

| File | Responsibility |
| --- | --- |
| `core/content_storage/dictionaries.py` (new) | Validated immutable dictionary registry |
| `core/content_storage/cold_cache.py` (new) | Lifecycle metadata and native-root mapping cache |
| `core/content_storage/writes.py` (new) | Fixed-column adapted parameterized updates |
| Existing `codec.py`, `references.py`, `extraction.py`, `ingestion.py`, `readers.py` | Digest-first preparation, guarded assignments, no-op representation handling |
| `providers/contribution_revisions.py` (new) | Process-local committed source/day/project/graph observations |
| `providers/tree_aggregate_state.py` (new) | Tree eligibility and durable journal grouping |
| `providers/tree_aggregate_prepare.py` (new) | Read-only source and daily aggregate preparation |
| `providers/tree_aggregate_runtime.py` (new) | Bounded source-independent aggregate continuation |
| Existing `db_writer.py`, `compute_base.py`, `compute_repair*.py`, `live_aggregates.py` | Contribution notifications and coordinated cutover |
| Existing `core/models.py`, `projects.py`, visibility/history lifecycle writers | Daily-derived weeks and coverage of contributing mutations |

Paths in this table are relative to `src/twicc/`.
Use NamedTuple for immutable records. Do not introduce an unrelated provider or writer abstraction.

---

### Task 1: Reuse validated dictionaries across the process

**Files:** Create `src/twicc/core/content_storage/dictionaries.py`; modify `codec.py`, `policy.py`, `src/twicc/content_maintenance_task.py`, provider writer startup/shutdown in `src/twicc/providers/db_writer.py`; test `tests/test_content_dictionary_cache.py`; update cache cleanup fixtures in `tests/content_storage_helpers.py`.

**Interfaces:**
- `DictionaryEntry(NamedTuple)`: `row: ContentDictionary`, `native: zstandard.ZstdCompressionDict`, `policy_version: str`.
- `get_dictionary(dictionary_id: int) -> DictionaryEntry`.
- `get_provider_dictionary(provider: str) -> DictionaryEntry | None`.
- `reset_dictionary_cache() -> None`; `preload_dictionaries() -> None`.
- Entries are scoped by configured database identity and writer lifetime. Compressors/decompressors remain operation-local.

- [ ] Write `test_dictionary_loads_and_validates_once`, with repeated encode/decode and one dictionary SELECT/hash validation after preload.
- [ ] Write `test_same_pk_in_another_database_is_isolated`, `test_missing_or_invalid_dictionary_fails`, and `test_parallel_decoders_share_only_immutable_dictionary`.

**Assertion anchor — repeated dictionary operations after preload:**
```python
assert dictionary_select_count == 0
assert dictionary_validation_count == 0
assert decoded_texts == original_texts
```

- [ ] Run `tests/test_content_dictionary_cache.py`; confirm the behavioral tests fail against repeated dictionary loading.
- [ ] Implement the registry. Populate from committed dictionary rows after startup migrations, before source compute starts. Handle a previously unseen immutable ID once. Do not globally cache a dictionary inserted in an uncommitted transaction; publish such entries after commit or use them locally.
- [ ] Route codec/policy lookups through the entry. Remove redundant validation of an already validated entry. Preserve decode integrity checks and fresh mutable codec objects.
- [ ] Route maintenance dictionary selection and representation publication through the same registry. Test zero additional dictionary SELECTs during repeated maintenance preparation/publication after preload.
- [ ] Reset on writer lifetime changes and between isolated test databases/rollback fixtures. Run cache, codec, dictionary assets, and initial-compression tests.
- [ ] Review and commit `perf(storage): reuse validated dictionary entries`.

### Task 2: Cache lifecycle decisions without coupling them to content epochs

**Files:** Create `src/twicc/core/content_storage/cold_cache.py`; modify `state.py`, `policy.py`, `lifecycle.py`, `ingestion.py`, `extraction.py`, `src/twicc/providers/db_writer.py`; test `tests/test_content_cold_cache.py`, existing lifecycle/root-state tests.

**Interfaces:**
- `cached_root_decisions(session_ids: Iterable[str], now: datetime, active_ids: frozenset[str]) -> dict[str, RootDecision]`.
- `invalidate_cold_metadata(session_ids: Iterable[str], *, mappings: bool = False) -> None`.
- `reset_cold_cache() -> None`.
- Consume committed lifecycle fields from existing helpers. Apply pending compute fields locally, not as global committed cache values.

- [ ] Write `test_repeated_contents_reuse_root_metadata`, `test_reference_epoch_does_not_evict_lifecycle`, and `test_subagent_uses_root_policy`.
- [ ] Write tests for 15/30-day boundaries, backwards clock crossing, active grandchild, reparenting, late parent creation, DB identity replacement, and rollback. Assert the new parent and old parent are both invalidated after reparenting.
- [ ] Write `test_invalidated_inflight_fill_cannot_install_old_metadata`: pause a fill after its read, commit a hot lifecycle change, invalidate, then resume. Assert the stale entry cannot authorize compression.

**Assertion anchor — an invalidated fill resumes after a hot commit:**
```python
assert cached_decision.is_cold is False
assert compression_publication_count == 0
```

- [ ] Run `tests/test_content_cold_cache.py`; confirm repeated SQL and stale cached decisions fail the new assertions.
- [ ] Implement cached metadata/mappings. Use current wall time to validate threshold expiry in either clock direction. Keep unresolved ancestry conservative. Overlay the current active set once per publication under admission.
- [ ] Fence read/install with per-key invalidation generations under a cache lock. A fill installs only when its captured generation still matches. Retry invalidated fills outside writer; preserve tombstone generations until all in-flight fills finish.
- [ ] Invalidate after successful lifecycle/creation/deletion commits. Keep cache notification separate from `content_state_epoch`; reference-only updates do not evict lifecycle metadata.
- [ ] Replace repeated `cold_placement` callback work with one batch eligibility decision under the existing guards. Run cold policy/cache, lifecycle, root state, storage admission, and promotion tests.
- [ ] Review and commit `perf(storage): cache native root lifecycle decisions`.

### Task 3: Adapt fixed-column content writes and skip unchanged owners

**Files:** Create `src/twicc/core/content_storage/writes.py`; modify `references.py`, `extraction.py`; test `tests/test_content_storage_writes.py`, `tests/test_content_references.py`.

**Interfaces:**
- `write_item_content_assignments(items: Sequence[SessionItem]) -> None`: updates `current_content_id`, `original_content_id`, and legacy `content` in one statement per changed item.
- `write_content_owners(rows: Sequence[SessionItemContent]) -> None`: adapted JSON values, changed rows only.
- Both require the caller's transaction. They use quoted fixed identifiers and Django field adaptation with `cursor.executemany`.

- [ ] Write `test_assignment_and_legacy_clear_share_one_update`, `test_owner_present_produces_no_update`, `test_owner_addition_preserves_other_owners`, and `test_assignment_rollback_restores_counts_and_owners`.

**Assertion anchor — an owner already exists:**
```python
assert owner_update_count == 0
assert stored_owner_ids == initial_owner_ids
```

- [ ] Run these tests and existing references tests; verify unchanged owner SQL currently violates the assertion.
- [ ] Implement fixed-column updates. Deduplicate item IDs before writing; reject conflicting assignments rather than allowing iteration order to choose a winner. Preserve untouched original IDs and null semantics.
- [ ] Replace only the content/ownership CASE paths. Preserve assignment validation and reference-count capture around writes. Continue checking both FK slots before owner removal.
- [ ] Run storage writes, references, extraction, history lifecycle, and initial ingestion tests. Assert JSON byte adaptation matches the existing Django field preparation.
- [ ] Review and commit `perf(storage): use parameterized content updates`.

### Task 4: Look up logical identities before optional compression

**Files:** Modify `types.py`, `references.py`, `extraction.py`, `ingestion.py`, `readers.py`; test `tests/test_content_digest_preparation.py`, existing initial compression/ingestion/extraction/consumers tests.

**Interfaces:**
- `PreparedInternContent(NamedTuple)`: `text: str`, `sha256: bytes`, `raw_size: int`, `existing_id: int | None`, `encoded: EncodedContent | None`, `policy_version: str | None`, `no_gain: bool`.
- `prepare_intern_contents(texts: Sequence[str], provider: str, *, cold: bool) -> dict[bytes, PreparedInternContent]`: retains identity records for every unique string; prepares representation bytes only for missing rows. Existing rows have `encoded=None`.
- Retain `intern_contents(texts, provider, prepared=None) -> dict[str, int]` for callers; change prepared value type to `PreparedInternContent`.
- Add optional `initial_owners: Sequence[str] = ()` to `intern_contents`; only the guarded assignment path supplies owners.

- [ ] Write `test_existing_hash_skips_compression_and_decode`, `test_new_cold_content_compresses_once_without_validation_decode`, and `test_no_gain_metadata_survives_initial_insert`.
- [ ] Write `test_prepare_and_publish_hash_each_unique_string_once`, covering both reused and missing rows. Publication consumes their retained text/digest associations.
- [ ] Write `test_row_inserted_between_lookup_and_publication_is_reused`, `test_known_batch_digest_conflict_fails`, and `test_prepared_metadata_mismatch_fails`.

**Assertion anchor — one existing and one missing cold string:**
```python
assert digest_call_count == 2
assert compression_call_count == 1
assert validation_decode_call_count == 0
assert resolved_existing_id == initial_existing_id
```

- [ ] Run digest-preparation tests; require spies to show the existing extra compression/decode calls before changing the code.
- [ ] Prepare digests once per unique exact string, using unchanged UTF-8 semantics. Existing lookups load identity/representation metadata, not payloads for equality decoding.
- [ ] Publish prepared missing rows without round-trip validation or repeated digest computation. Validate metadata association, provider/dictionary choice, size, codec, and guarded source. Resolve insertion conflicts through unique digest identity.
- [ ] Seed new owners on insert, then validate actual assignments. Concurrently encountered rows still receive missing owners; ignored item insertions must not create false owners. Plain foreground fallback computes each unique digest once without optional compression under writer.
- [ ] Run digest preparation, codec, references, ingestion, extraction, overrides/readers, and external consumers tests. Update old tests requiring routine collision decoding to test the new documented trust boundary.
- [ ] Review and commit `perf(storage): prepare only missing logical content`.

### Task 5: Make reconciliation change-driven

**Files:** Modify `extraction.py`, `types.py`, `src/twicc/content_maintenance_task.py` where shared no-op classification applies; test `tests/test_content_reconciliation_changes.py`, existing extraction/maintenance/promotion tests.

**Interfaces:**
- `ContentMetadataObservation(NamedTuple)`: content ID, codec, dictionary ID, logical digest/size, owner IDs, provider, attempt state, and no-gain state. It contains no payload bytes.
- `ReconciliationAction(NamedTuple)`: `content_id: int`, `observed: ContentMetadataObservation`, `encoded: EncodedContent | None`, `policy_version: str | None`, `no_gain: bool`.
- `encoded=None` means metadata-only change; omit the action entirely when all relevant state is unchanged.
- Maintain operation-local visited content IDs across reconciliation pages; revalidate owner/root changes before skipping or publishing. Release this state with the operation.

- [ ] Write `test_new_conforming_contents_skip_reconciliation`, `test_metadata_only_action_does_not_read_or_write_payload`, `test_repeated_shared_ids_are_not_transcoded_again`, and `test_current_no_gain_is_not_retried`.
- [ ] Write `test_hot_owner_added_after_preparation_rejects_compression` and `test_promotion_between_pages_is_not_hidden_by_visited_ids`.

**Assertion anchor — metadata-only reconciliation:**
```python
assert payload_select_count == 0
assert payload_update_count == 0
assert decode_call_count == 0
```

- [ ] Run new tests; confirm current same-dictionary transcoding and identical payload updates fail the assertions.
- [ ] Classify using codec, known provider policy, attempt state, owner metadata, and current cached root eligibility. Read/decode payload only when an actual representation change is needed.
- [ ] Preserve representation freshness and membership checks without blanket decoding or payload equality validation for no-op rows. A changed relevant observation invalidates that skip/action and remains eligible for re-evaluation.
- [ ] Validate metadata-only actions against current metadata and ownership/root observations. Actual transcodes additionally retain the existing representation observation when reading payloads. Do not require full-payload observations for no-op classification.
- [ ] Run reconciliation, extraction, maintenance, promotion, cold cache, and session-promotion tests. Keep existing cooperative yielding during actual transcodes.
- [ ] Review and commit `perf(storage): skip unchanged representation reconciliation`.

### Task 6: Observe every committed contribution mutation precisely

**Files:** Create `src/twicc/providers/contribution_revisions.py`; modify `live_aggregates.py`, `compute_repair.py`, `compute_base.py`, `db_writer.py`, `core/services/session_visibility.py`, and source history/type/project mutation callers found by the final audit; test `tests/test_contribution_revisions.py` and existing repair mutation tests.

**Interfaces:**
- `DailyKey(NamedTuple)`: `project_id: str | None`, `provider: str`, `day: date`.
- `SourceKey(NamedTuple)`: `session_id: str`, `incarnation: str`.
- `RevisionObservation(NamedTuple)`: tuples of `(key, revision)` for source contributions, native root membership, daily inputs, derived node costs, and project aggregate inputs.
- `observe_revisions(*, sources: Iterable[SourceKey] = (), roots: Iterable[SourceKey] = (), days: Iterable[DailyKey] = (), costs: Iterable[SourceKey] = (), projects: Iterable[str] = ()) -> RevisionObservation`.
- `revisions_match(observation: RevisionObservation) -> bool`.
- `record_contribution_commit` accepts the same typed keyword collections and returns `None`: register after-commit increments; discard callbacks on rollback.
- `reset_contribution_revisions() -> None`.

- [ ] Write tests proving cost changes with null timestamps advance source/project observations; old/new date and scope changes advance both daily keys; creation/deletion/reparenting advances old/new root membership; rollback advances nothing.
- [ ] Write `test_other_root_same_day_invalidates_preparation` and `test_auth_or_title_only_commit_does_not_invalidate_contributions`.
- [ ] Write `test_other_root_cost_publication_invalidates_project_preparation`, `test_derived_publication_preserves_tree_source_observation`, and `test_project_output_does_not_advance_project_input_revision`.

**Assertion anchor — another root publishes its derived cost:**
```python
assert revisions_match(project_input_observation) is False
assert revisions_match(tree_source_observation) is True
```

- [ ] Run revision tests; verify the new observation contract is absent or fails without hooks.
- [ ] Implement an in-process lock-protected monotonic revision registry. All keys include the configured runtime/database identity. Obtain observations atomically relative to commit notifications; callbacks finish before releasing writer ownership.
- [ ] Audit every `apply_contribution_changes`, `preserve_pending_repair`, metadata item write, lifecycle scope change, deletion/history replacement, and project-total writer. Connect source mutation hooks even when no journal exists. Representation-only changes do not advance cost/day revisions.
- [ ] Separate input domains. Source edits advance source/day inputs; membership edits advance graph inputs. Publishing derived node costs advances cost revisions and eligible root project inputs, but not source/day/graph revisions. Writing `Project.total_cost` does not invalidate its own inputs.
- [ ] Historical scope movement uses preserved old/new affected dates. Prepare expensive historical key enumeration outside writer, with source observation validation before mutation; do not add a new whole-history scan under writer.
- [ ] Run revision, repair mutation/journal, visibility, live aggregate, and history replacement tests. Record the concrete mutation coverage matrix in the test module comments.
- [ ] Review and commit `refactor(compute): track scoped contribution revisions`.

### Task 7: Group durable obligations and observe complete native trees

**Files:** Create `src/twicc/providers/tree_aggregate_state.py`; modify `src/twicc/providers/compute_repair_journal.py`; test `tests/test_tree_aggregate_state.py`, `tests/test_compute_repair_journal.py`.

**Interfaces:**
- `TreeObservation(NamedTuple)`: optional root `SourceKey`, immutable member source identities and compute versions, source/membership-only `RevisionObservation`, and tuple of `RepairSnapshot`.
- `RepairSnapshot` gains `project_ids: frozenset[str]`. `merge_compute_repair` gains keyword `project_ids: Iterable[str] = ()`; generation advances when this retained union changes.
- Persist project obligations in the existing `activity_keys` JSON list as `["project", project_id]` tags. Keep existing four-element day/week tags unchanged. Decode and validate the two tag shapes separately; reject malformed IDs/tags. Older rows decode with an empty project set. No schema migration is needed.
- `TreeAggregateWork(NamedTuple)`: `observation: TreeObservation`, `days: frozenset[DailyKey]`, `weeks: frozenset[DailyKey]`, `former_parents: frozenset[str]`, `project_ids: frozenset[str]`.
- `observe_tree_work(session_id: str, pending_source_ids: frozenset[str]) -> TreeAggregateWork | None`.
- `tree_work_matches(work: TreeAggregateWork, pending_source_ids: frozenset[str]) -> bool`.
- `observe_orphan_work(snapshot: RepairSnapshot) -> TreeAggregateWork`: root may be absent; no absent-source compute requirement.

- [ ] Write readiness tests for flattened deep launchers, valid unflattened chains, independent spawned sessions, cycles/missing parents, and a failed/deferred child. Assert an applied root alone does not make unfinished descendants eligible.
- [ ] Write `test_late_child_invalidates_observed_tree`, `test_reparent_preserves_former_tree_dates`, and `test_old_incarnation_obligation_survives_source_replacement`.
- [ ] Write `test_ready_new_tree_does_not_settle_incomplete_former_tree` and its orphan variant. New-tree publication proceeds; the shared journal survives until former-tree costs also finish.
- [ ] Write journal round-trip/union/generation tests for mixed project/day/week tags and legacy rows. Project tags do not enter daily/week query helpers. Keep production project-tag emission disabled until Task 9's atomic cutover.

**Assertion anchor — new tree ready, former tree incomplete:**
```python
assert new_tree_work is not None
assert former_tree_work is None
assert shared_journal_exists is True
```

- [ ] Run state tests before implementation.
- [ ] Resolve normal flat roots through indexed parent metadata. Traverse exceptional unflattened ancestry through parent links with cycle detection. Never recursively search all `type='subagent'` rows per node.
- [ ] Group existing journal rows without deleting them. Union old/new day scopes and parent obligations. Derive week keys from dates; for a legacy weekly-only obligation, repair its seven daily keys before deriving that week.
- [ ] Carry retained project IDs independently of day keys and source existence. Add project scopes inferred from legacy daily keys or surviving sources. Do not claim that absent historical project scopes can be reconstructed after deletion.
- [ ] Resolve every surviving former parent into a separate observed native-tree work dependency. Apply the same completion barrier to that tree. Missing former parents leave only surviving bucket/project obligations. Keep a shared snapshot until all associated tree obligations finish; do not block eligible tree publication on another deferred tree.
- [ ] Accept only current source versions and no conflicting pending application. Revalidate member identities, membership/source revisions, and journal generations. Use provider inventory completion/success from orchestrators, not their compute classification event.
- [ ] Run state, journal, nested-agent, initial sync, and source readiness tests. Verify query plans on synthetic 414-node trees without product DB access.
- [ ] Review and commit `feat(compute): observe complete native tree obligations`.

### Task 8: Prepare exact aggregate values outside writer and derive weeks from days

**Files:** Create `src/twicc/providers/tree_aggregate_prepare.py`; modify `core/models.py` weekly helper; test `tests/test_tree_aggregate_prepare.py`, `tests/test_weekly_from_daily.py`.

**Interfaces:**
- `AggregateValues(NamedTuple)`: `cost: Decimal | None`, `message_count: int | None`, `session_count: int | None`, `self_cost: Decimal | None`, `subagents_cost: Decimal | None`. Unused fields are `None`; preserve existing model zero/null semantics.
- `PreparedAggregate(NamedTuple)`: `kind: Literal["session", "day", "project"]`, `key: SourceKey | DailyKey | str`, `values: AggregateValues`, `observation: RevisionObservation`. Kind selects the fixed destination fields; no arbitrary SQL identifiers.
- `prepare_session_values(work: TreeAggregateWork, session_id: str) -> PreparedAggregate`.
- `prepare_daily_values(work: TreeAggregateWork, key: DailyKey) -> PreparedAggregate`.
- `prepare_project_values(work: TreeAggregateWork, project_id: str) -> PreparedAggregate`: observes eligible persisted root contributions and project inputs.
- `publish_aggregate(work: TreeAggregateWork, prepared: PreparedAggregate, pending_source_ids: frozenset[str]) -> str`: returns `published`, `changed`, or `deferred`; caller holds the short writer transaction.
- `WeeklyActivity.recalculate_from_daily(project_id: str | None, monday: date, provider: Provider) -> None`: caller-owned writer transaction; SUM of seven daily rows, zero-row deletion preserved.

- [ ] Write `test_preparation_has_no_writer_lease_or_source_write`, `test_changed_observation_rejects_publication`, `test_null_timestamp_cost_is_included`, and `test_root_total_counts_each_member_once`.
- [ ] Write week equivalence tests for UTC boundaries, missing/zero daily rows, Decimal rounding, hidden user-message exclusions, creation-date counts, providers, and project/global scopes. Seed the daily baseline before comparing weeks to the independent item reference.

**Assertion anchor — daily-derived week and stale preparation:**
```python
assert published_week_values == independent_item_week_values
assert weekly_session_item_query_count == 0
assert stale_publication_result == "changed"
```

- [ ] Run these tests; verify old raw weekly queries and missing freshness guards fail new assertions.
- [ ] Execute exact read queries in a consistent read-only snapshot on a dedicated preparation connection. Never carry its transaction/cursor into publication. Preserve SQLite/Django cost conversion and existing null results; do not sum provider floating-point usage calculations.
- [ ] Use a scheduler-owned single-worker preparation executor, separate from `run_compute_sync()` and foreground SQL. Submit at most one outstanding preparation; close its thread-local connection on shutdown. Drain cancelled work before closing the executor. Historical reads never occupy the foreground execution queue.
- [ ] Check relevant observations before and after read preparation. Compute each node's own values once for the tree observation. Derive root subagent totals from member own costs; do not sum overlapping intermediate totals. Include timestamp-null costs.
- [ ] Publish only small values after checking work eligibility and prepared freshness. Project totals use their own observed root contributions. Retry stale values outside writer; do not run source SUMs in `publish_aggregate`.
- [ ] Observe unit inputs separately: own costs depend on source revisions; root totals depend on finalized member cost revisions; days depend on daily input revisions; project totals depend on root cost/project input revisions. Derived publication does not invalidate the tree's source/membership observation.
- [ ] Add daily-derived weekly helper. Keep old raw weekly calculation available only as an independent test reference until runtime callers switch in Task 9.
- [ ] Run prepare/week tests, exact repair references, and live aggregate tests. Assert a weekly publication issues no SessionItem query.
- [ ] Review and commit `feat(compute): prepare tree aggregates outside writer`.

### Task 9: Switch normal finalization and recovery to one bounded aggregate continuation

**Files:** Create `src/twicc/providers/tree_aggregate_runtime.py`; modify `db_writer.py`, `compute_base.py`, `activity_preparation.py`, `compute_repair.py`, `compute_repair_recovery.py`, `live_aggregates.py`, `projects.py`, and visibility/deletion/history callers as required; test `tests/test_tree_aggregate_runtime.py`, `tests/test_tree_aggregate_recovery.py`, existing continuation/repair/live tests.

**Interfaces:**
- `TreeAggregateScheduler`: `note_source_applied(session_id: str) -> None`, `discover(snapshots: tuple[RepairSnapshot, ...]) -> None`, `async advance(pending_sources: Callable[[], frozenset[str]]) -> bool`, `async close() -> None`.
- `advance` performs at most one preparation transition or one bounded publication unit per turn, using existing background coordinator admission. Its owned preparation future is drained cancellation-safely.
- A unit is one session cost update, one daily bucket, one weekly bucket, or one project-total update. Settlement is a separate guarded journal step.

- [ ] Write `test_source_ack_does_not_wait_for_tree`, `test_incomplete_child_blocks_both_normal_and_recovery_activity`, and `test_other_root_and_foreground_write_continue`.
- [ ] Write late-child/source-change tests at preparation and publication; verify stale job work remains durable. Write `test_partial_days_then_restart_finishes_weeks`, `test_generation_change_prevents_settlement`, and orphan/former-parent recovery tests.
- [ ] Write `test_pending_application_appearing_during_admission_defers_publication`. Queue admission, add pending source work without committing, then grant admission. Assert no stale publication.
- [ ] Write restart tests for roots with `created_at=None` and only timestamp-null costs: move P to Q, or delete the root. Commit source mutation, reset process state before aggregate publication, then recover. Assert both retained project totals become exact before settlement.

**Assertion anchor — pending application appears before admission:**
```python
assert publication_result == "deferred"
assert aggregate_write_count == 0
assert repair_journal_exists is True
```

- [ ] Run runtime/recovery tests before integration.
- [ ] Switch final source publication to retain durable obligations and publish source readiness/acknowledgments without whole-history aggregate repair. Notify the tree scheduler after commit. Do not change outstanding-source bounds or startup source progress.
- [ ] Switch recovery discovery/admission simultaneously. Defer incomplete trees, schedule ready trees/orphans through the same continuation, and remove whole-journal writer repair dispatch. Do not leave `activity_final_policy` fallback or an alternate source completion route able to run historical SUMs under writer.
- [ ] Enable durable project-tag emission in this same cutover. Retain old/new effective project scopes on cost, project/worktree scope, type, and deletion changes, even without dates or parents. Preserve existing project/worktree roll-up rules. Record tags atomically with source mutation; no process-local revision substitutes for this journal obligation.
- [ ] Order each job: finalized member values and root costs, affected days, affected weeks, project totals, journal settlement. Current-day values remain progressive across other trees. Reobserve/reprepare only affected stale work; every settlement fences all captured generations.
- [ ] Obtain pending application IDs from the writer's authoritative registry after admission, immediately before validation/publication. The callback never performs historical SQL. Apply the same fresh admission check to recovery and settlement.
- [ ] Track shared-journal dependencies across current/former trees. Settlement requires all associated work to finish against captured generations. After restart, rediscover every dependency from the retained journal; no in-memory completion flag is durable evidence.
- [ ] Include every retained project in publication and settlement dependencies, including orphan projects and timestamp-null contributions. A missing project is discharged only after confirming deletion; a surviving project requires exact replacement.
- [ ] Keep live source updates immediate. An incomplete tree records dates without full historical repair; established live baselines retain deltas. Live weekly updates and background derived-week replacement stay serialized, with consistent zero/null behavior.
- [ ] Handle hide/project/type changes, source deletion/history replacement, and retained parent journals through bounded aggregate jobs instead of calling `settle_deleted_repairs` with unbounded SUMs in the foreground deletion transaction. Source mutations themselves keep their existing atomic guards.
- [ ] On restart, recover from journals without trusting memory revisions. On graceful shutdown, drain actual preparation workers but retain unfinished journal work; do not insist on finishing all sums before exit. Retain retry backoff and finite discovery sweeps.
- [ ] Remove obsolete runtime full-repair paths. Move independent raw aggregate reference code into test helpers if removing old production helpers affects oracle tests. Adapt standalone private test callers to drain the new continuation explicitly.
- [ ] Run runtime/recovery, compute continuation/shutdown/signals, repair mutation/journal/exact, activity obligation, live sync aggregate, project notification, visibility, and history lifecycle tests.
- [ ] Review the complete cutover before integration; commit `refactor(compute): publish aggregates after native trees complete`.

### Task 10: Verify compatibility, contention, and real-copy performance

**Files:** Create `tests/test_tree_aggregate_contention.py`; extend existing content/executor/consumer tests; update this plan with measured results. Throwaway benchmark scripts/results remain outside tracked product files unless needed to reproduce a regression.

**Interfaces:** Consume the final storage and tree continuation APIs. Do not introduce another runtime API or change the 2,000-item metadata batch size.

- [x] Write deterministic contention tests: pause background preparation, perform a foreground watcher append, then resume. Assert foreground completion, rejected stale preparation, exact eventual values, and no duplicate contribution.
- [x] Block an actual preparation executor query with a barrier. Assert foreground SQL completes through its separate executor while the historical read remains blocked.
- [x] Write a publication-turn test with queued foreground demand. Assert foreground runs before the next background unit and that a stale retry does not invoke SUM under writer.

**Assertion anchor — blocked historical executor, foreground append:**
```python
assert foreground_completed_before_preparation_release is True
assert eventual_aggregate_values == independent_reference_values
assert duplicate_contribution_count == 0
```

- [x] Run contention tests and verify they pass on the implementation. Use barriers/events rather than timing-sensitive sleeps for correctness assertions.
- [ ] Run the complete backend suite: `TWICC_DATA_DIR=$PWD uv run pytest`. Run frontend tests only if serializer/frontend code changed; they are not planned here. Run `uvx ruff check <changed-python-files>`.
- [x] Audit every spec section against implemented tests and every contribution writer against the mutation coverage matrix. Check external CLI/front payload content remains at the existing field with no N+1 policy imposed globally.
- [x] Use the proven devctl SQLite backup method (`source.backup(target, pages=-1)`, read-only source) to create a throwaway DB copy. Do not start DevCTL or apply branch migrations manually. Check source/target and resolved Django DB paths before any benchmark.
- [x] Compare baseline and implementation from identical backup snapshots and dictionaries. Include hot/cold sessions, 32/64/256-item content batches, medium/large individual payloads, shared contents, no-gain rows, and the large 145,950/186,496-item sessions when available. Record dictionary queries, compression/decode calls, owner/payload writes, and preparation/publication phase times.
- [x] Exercise foreground watcher/lifecycle writes during each benchmark. Record writer hold P50/P90/P95/P99/max, foreground wait distribution, event-loop heartbeat delay, source throughput, and whole-tree aggregate completion time. Separate first-read/cache effects from repeat samples.
- [x] Keep 256/8 MiB as the initial content publication budgets. Evaluate 64 rows only after removing redundant work; select the smallest change justified by measured foreground holds and overall throughput. No new universal latency guarantee is inferred from a memory-only test.
- [x] Treat any routinely observed background hold of one second or more as a failed result. Attribute oversized content, guard, SQL, and commit components; do not accept averages that conceal the failure. If a remaining representation write cannot meet foreground needs, stop for a revised bounded-publication design rather than silently changing schema or weakening integrity.
- [ ] Review the whole branch independently, correct findings, and rerun affected checks. Commit the contention coverage and final evidence with `test(compute): verify bounded tree aggregate publication`.

## Spec coverage and completion handoff

| Spec sections | Tasks |
| --- | --- |
| Dictionaries | 1 |
| Cold/mapping caches | 2 |
| Identity, preparation, ownership, writes | 3–4 |
| Change-driven reconciliation | 5 |
| Precise source/live observations | 6 |
| Native tree completion | 7, 9 |
| Costs, days, derived weeks | 8–9 |
| Durable obligations, normal/recovery parity | 7, 9 |
| Foreground responsiveness and acceptance | 9–10 |

Plan execution requires user review and a preserved execution-method choice.
This planning task neither starts implementation nor starts the dev servers.
After implementation, report tests, measured foreground/hold results, limitations, and remaining user-operated restart steps.

## Implementation-plan review result

Two independent internal reviewers examine storage/caches and aggregates/live/recovery over three rounds.
Round one identifies eight material gaps: concurrent cache fills, metadata-only observations, reused digest retention,
maintenance dictionary access, derived input revisions, former-tree dependencies, admission freshness, and executor ownership.
Round two identifies one additional gap: durable project scopes for timestamp-null root deletion or project movement.
The corrected plan addresses all nine. Both reviewers pass the final round with no remaining material findings.
Self-review checks spec coverage, task dependencies, interface consistency, assertion anchors, and document/test paths.
These reviews validate the plan, not product correctness or performance. Implementation and measurements remain pending.


## Task 10 implementation and permanent validation evidence

Affected verification: **1,186 passed in 198.65 seconds** after all production changes and the WAL replay fixture correction.
Scoped Ruff reports `All checks passed!`; `git diff --check` passes.
The complete backend suite and independent branch review remain controller-owned and pending at this commit.
The controller's earlier integration run on `9dd4df48` has 54 failed, 10,610 passed, 28 skipped, 37 errors, and 3 baseline warnings.
The clean baseline has 10,688 passed, 28 skipped, and those same 3 warnings.

The fixes retain immediate bounded live user-message deltas when own-cost baselines await exact repair.
Creation-day revisions fence zero-crossing eligibility, including timestamp-null messages.
Completed tree proofs reuse validated date inventories. Exact journal and content metadata guards use batched reads.
Daily cost sums restore the existing partial cost index; null-cost rows remain in message and session counts.
WAL barriers prove foreground commit, stale preparation rejection, exact eventual cost, and no duplicate contribution.
Real watcher tests preserve immediate new-session costs, callback success, and exact replay checkpoints.
The shared-memory SQLite failure is an unsupported test topology; production queue/retry behavior does not change.
External RPC/CLI/serializer tests preserve decoded content fields and shapes. No frontend or global N+1 policy changes.
The report audits every specification section and contribution writer family against concrete regression suites.

Nonblocking residuals: private malformed prepared-field types can raise ordinary Python errors; mapping eviction stays conservative;
Django callback-tuple inspection remains an installed-version dependency; retained journal/proof memory scales with backlog.
No new eviction policy or weaker publication guard is introduced. The benchmark limitations below remain review concerns.

## Benchmark safety and reproducibility

Canonical immutable input: `/tmp/twicc-content-optimization-bench-20261014/snapshot.sqlite` (24,781,836,288 bytes).
The controller creates it with read-only SQLite `source.backup(target, pages=-1)` in 129.466 seconds.
No whole-database integrity pass is claimed: the earlier read-only `quick_check` is explicitly stopped.
Only `/tmp/twicc-content-optimization-bench-20261014/working/db/data.sqlite` receives benchmark writes.
One full working copy exists at a time. Restore deletes only this owned disposable copy before copying the canonical snapshot.
Every benchmark asserts `twicc.__file__` and the resolved Django database path before data writes.
No benchmark opens provider homes or starts servers.

Scripts and JSONL output stay outside tracked product files under `/tmp/twicc-content-optimization-bench-20261014/`.
Scripts log aggregate metrics and source IDs, never user content.
Baseline source comes from `git archive ccb18ba7`; it runs through the existing staging Python environment with explicit `PYTHONPATH`.
Both revisions use the identical persisted dictionaries: Claude dictionary 1 has 16,384 bytes; Codex dictionary 2 has 12,496 bytes.
No dictionary training or schema migration occurs. The source inventory contains 4,538 roots and 10,107 subagents.
Operating-system file caches are not forcibly cleared; "first" means first operation in a fresh process/database restore,
not a claim of physically cold storage. Repeat cases and canonical restore scope are labeled separately.
The synthetic extraction cases use the full database copy and real production preparation/publication functions.
Whole-source passes exercise already-stored source reconciliation. The synthetic matrix exercises missing-content extraction.
Whole-source elapsed times cover the continuation loop after `begin_compute_application`, through the final-ready boundary.
They exclude provider JSONL parsing, metadata recomputation, operation creation, and the separate aggregate scheduler.
Synthetic case elapsed times include fixture creation and operation creation as well as the continuation loop.
The final current run adds preparation samples and total counter output without changing benchmark operations.
The last restore takes 140.091 seconds and copies exactly 24,781,836,288 bytes.

### M1 date enumeration

Read-only canonical query covers root `01a08171-a93c-7b21-9689-b34ffc2f73fc` and 413 native children.
It returns 447,032 distinct session/timestamp pairs.
SQL enumeration: first 7.975 seconds; repeats 0.758, 0.795, 0.755 seconds.
These measurements exclude Python UTC conversion and scope expansion, so they understate `_work` cost.
They establish that per-journal enumeration is material. Proof reuse is validated by the SQL-count regression.
Log: `readonly-observation.jsonl`; script: `scripts/readonly_observation.py`.

### M2 blocked-backlog memory

`tracemalloc` measurements with inventory explicitly blocked:

| Journal count | Separate day keys per journal | Waiting retained bytes | Completed shared proof bytes | Half-settled bytes |
| --- | --- | ---: | ---: | ---: |
| 10,000 | 2 | 11,300,162 | 13,836,970 | 13,281,258 |
| 100 | 4,100 | 55,842,650 | 56,429,490 | 56,420,738 |

The generator creates separate date/key objects for each journal. These figures do not assume globally shared key tuples.
Snapshots retain IDs, dates, scopes, generations, and proof references. They retain no item payloads.
Memory grows with retained obligations; finite discovery pages are not a global memory bound.
The canonical database has zero existing repair journals, so these are synthetic backlog limits, not production backlog measurements.
No eviction is added without an observed workload need. A much larger simultaneous 4,100-key backlog remains a documented scaling risk.
The synthetic completed proof shares the actual snapshot objects. Half settlement retains them through the remaining shared proof.
This explains why half settlement does not free half the retained memory. Final runtime drain clears the completed/settled collections.
The largest measured peaks are 14,532,634 and 56,432,754 bytes respectively.
These synthetic proof measurements omit database member objects and executor cost caches; they isolate journal retention.
Log: `memory-final.jsonl`; earlier waiting-only log: `memory.jsonl`; script: `scripts/memory.py`.

### Legacy aggregate comparison

The real 414-member giant root repair holds the old writer for 10.216 seconds, then 2.987 seconds on repeat.
It executes 872 historical SUM queries per repair. Foreground waits are 10.218 and 2.985 seconds respectively.
Heartbeat maximum is 56.001 ms. Commit maximum is 4.616 ms, so historical work causes the long holds.
This measures one legacy root repair, including its affected activity and direct project total.
It does not claim the old per-child full compute pass completes in this time, or imply a direct end-to-end speed ratio.
Log: `baseline-aggregate.jsonl`; script: `scripts/baseline_aggregate.py`.

### Final giant aggregate result

The final scheduler completes the 414-member tree and settles all 414 journals in 105.911 seconds.
This includes 414 own/root publications, 36 daily publications, derived weekly/project units, shared observation, and bounded settlement.
Its independent raw SQL root-cost error is exactly 0.0. No journal remains for the tree.
The measured runtime executes one own-tree historical SUM and 36 daily historical SUMs. Weekly work uses persisted daily rows.
The scheduler performs one timestamp inventory scan; four explicit completed-proof observations perform zero additional date scans.

| Runtime measurement, ms | P50 | P90 | P95 | P99 | Maximum | Samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Background writer hold | 63.05 | 72.88 | 89.15 | 145.35 | 161.23 | 873 |
| Foreground wait plus service | 23.53 | 71.84 | 77.41 | 141.85 | 528.68 | 2,775 |
| Heartbeat delay | 1.18 | 5.23 | 5.73 | 12.37 | 69.13 | 14,377 |
| Individual tree guard | 33.79 | 50.85 | 57.46 | 115.77 | 135.40 | 2,188 |

Foreground latency includes SQL service and thread scheduling, so its maximum can exceed the maximum background lease.
The broader run's foreground maximum is 571.67 ms during initial observation, and heartbeat maximum is 70.00 ms.
Fixture setup and the explicit diagnostic observations remain separately labeled in the raw output.
No one-second background lease occurs in this final aggregate run.
Settlement admits one journal per unit, but exact proof validation still scales with tree membership and retained repair snapshots.
The measured 414-member tree does not establish a universal bound for arbitrarily larger trees.
No membership, generation, source-revision, pending-source, or settled-snapshot guard is weakened to improve these timings.
Log: `current-aggregate-final.jsonl`. The 105.911 seconds excludes the separate diagnostic observation/guard probes before scheduler start.

### Measured fixes and incomplete diagnostic runs

- `current-aggregate.jsonl` is intentionally interrupted after measuring repeated 340–360 ms aggregate leases.
  About 340 ms is guard work; SQL execution is about 23 ms and commit time is negligible.
  It demonstrates per-journal ORM overhead. It is not a completed aggregate run.
- `current-verified.jsonl` completes a source repeat and byte-budget cases, then stops the unindexed aggregate diagnostic.
  The daily phase advances only a few units across 90–180 seconds. This run is not an aggregate completion claim.
- `day-index.jsonl` uses a read-only connection and EXPLAIN on the disposable database.
  Without the cost-only predicate, the plan uses `idx_session_kind_line` and the global query exceeds a 10-second cutoff.
  With the predicate, the plan uses `idx_item_cost_session` and completes in 92.73 ms.
  The project-scoped pair completes in 485.17 and 2.83 ms and returns equal cost within 0.000001.
  The censored global old query supplies no exact value comparison; the independent regression oracle supplies that coverage.
- The initial current giant-source maximum is 354.19 ms. That sample is explicitly unpaired and cannot receive an exact phase attribution.
  The subsequent paired repeat reproduces slow reconciliation: 246.76 ms for 256 items / 512 pointer slots / 854,553 bytes.
  SQL execution is 7.58 ms, storage guards 5.56 ms, and commit 0.033 ms.
  A later paired sample reaches 330.70 ms for 1,351,436 bytes, again with negligible commit and low SQL/guard time.
  Inspection identifies repeated per-content ORM metadata fetches; the metadata batch regression and final rerun assess their removal.

### Final stored-source comparison

| Source rows | Revision | Elapsed seconds | Units | Hold P50 | P90 | P95 | P99 | Maximum, ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 186,496 | Baseline original | 293.034 | 2192 | 38.51 | 157.60 | 162.37 | 172.73 | 202.77 |
| 186,496 | Current final | 166.706 | 2192 | 15.63 | 39.79 | 40.75 | 44.89 | 243.17 |
| 145,950 | Baseline original | 73.366 | 1715 | 10.05 | 35.72 | 36.56 | 38.82 | 51.28 |
| 145,950 | Current final | 77.229 | 1715 | 9.53 | 34.55 | 35.48 | 38.00 | 48.95 |

The baseline source samples retain their original run; they are not rerun with the final matrix.
Both source passes start from the canonical snapshot, but current foreground work also includes lifecycle updates.
The 186,496-row source has a maximum regression: **202.77 → 243.17 ms**, despite lower P95 and elapsed time.
The 145,950-row source takes **73.37 → 77.23 seconds**. No throughput improvement is claimed for that source.

| Current stored source | Dictionary queries | Native compression | Content decodes | Byte decodes | Payload inserts | Payload updates | Owner/metadata updates |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 186,496 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 145,950 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### Final matched storage matrix

Each cell lists **baseline / current**. Holds, waits, and heartbeat delays use milliseconds. Elapsed time uses seconds.
Foreground wait includes admission and SQL service. Both final runs include the same lifecycle writes.

| Case | Maximum writer hold, ms | Maximum foreground wait, ms | Maximum heartbeat delay, ms | Elapsed, s |
| --- | ---: | ---: | ---: | ---: |
| hot-32-0 | 38.64 / 18.69 | 84.51 / 4481.15 | 5.22 / 4.27 | 0.23 / 4.60 |
| hot-32-1 | 45.75 / 17.08 | 49.81 / 27.84 | 5.16 / 2.10 | 0.14 / 0.11 |
| cold-32-0 | 103.38 / 17.14 | 121.38 / 36.88 | 3.59 / 1.60 | 0.26 / 0.14 |
| cold-32-1 | 83.91 / 19.94 | 91.37 / 64.46 | 10.49 / 1.33 | 0.26 / 0.21 |
| shared-32-0 | 35.24 / 12.95 | 41.43 / 19.88 | 2.46 / 1.67 | 0.14 / 0.12 |
| shared-32-1 | 33.75 / 12.95 | 38.37 / 24.71 | 4.78 / 2.74 | 0.14 / 0.12 |
| no_gain-32-0 | 30.52 / 15.03 | 37.21 / 33.81 | 5.21 / 5.30 | 0.18 / 0.13 |
| no_gain-32-1 | 39.85 / 25.35 | 46.67 / 42.87 | 4.83 / 4.65 | 0.18 / 0.14 |
| medium-32-0 | 31.53 / 27.73 | 49.00 / 95.55 | 1.61 / 8.22 | 0.16 / 0.25 |
| medium-32-1 | 40.72 / 13.78 | 54.42 / 23.15 | 6.59 / 3.24 | 0.15 / 0.13 |
| large-32-0 | 42.25 / 17.10 | 51.74 / 28.89 | 2.76 / 2.25 | 0.17 / 0.13 |
| large-32-1 | 33.80 / 17.39 | 43.32 / 46.59 | 3.82 / 2.31 | 0.15 / 0.15 |
| oversized-32-0 | 452.31 / 228.75 | 464.60 / 274.00 | 96.63 / 100.20 | 1.09 / 0.73 |
| oversized-32-1 | 430.24 / 227.46 | 442.09 / 271.83 | 102.81 / 99.42 | 1.03 / 0.83 |
| bytecap-32-0 | 124.68 / 24.38 | 168.21 / 250.82 | 14.96 / 5.12 | 0.69 / 0.52 |
| bytecap-32-1 | 135.66 / 23.75 | 177.95 / 224.57 | 6.88 / 9.77 | 0.73 / 0.49 |
| hot-64-0 | 101.37 / 24.67 | 104.01 / 31.10 | 5.26 / 2.17 | 0.20 / 0.14 |
| hot-64-1 | 75.50 / 42.28 | 78.07 / 53.79 | 5.21 / 2.03 | 0.21 / 0.17 |
| cold-64-0 | 178.79 / 22.01 | 189.61 / 39.66 | 5.72 / 3.11 | 0.41 / 0.18 |
| cold-64-1 | 197.86 / 23.62 | 207.26 / 51.92 | 5.31 / 2.70 | 0.46 / 0.19 |
| shared-64-0 | 53.95 / 14.80 | 61.75 / 56.21 | 5.15 / 2.55 | 0.19 / 0.16 |
| shared-64-1 | 53.81 / 14.43 | 57.58 / 20.64 | 5.15 / 4.04 | 0.18 / 0.12 |
| no_gain-64-0 | 94.40 / 21.49 | 108.36 / 52.39 | 5.16 / 13.93 | 0.31 / 0.15 |
| no_gain-64-1 | 55.82 / 20.40 | 64.48 / 45.13 | 6.03 / 6.45 | 0.26 / 0.16 |
| medium-64-0 | 30.48 / 14.38 | 43.74 / 56.39 | 1.46 / 2.17 | 0.13 / 0.17 |
| medium-64-1 | 31.99 / 14.49 | 70.64 / 19.22 | 2.52 / 1.78 | 0.19 / 0.12 |
| large-64-0 | 40.44 / 16.32 | 44.51 / 23.75 | 5.31 / 5.08 | 0.14 / 0.13 |
| large-64-1 | 49.81 / 16.01 | 65.24 / 53.74 | 2.03 / 2.31 | 0.16 / 0.17 |
| oversized-64-0 | 438.56 / 203.36 | 451.53 / 230.19 | 75.69 / 88.98 | 1.05 / 0.74 |
| oversized-64-1 | 422.29 / 207.33 | 432.32 / 248.14 | 134.19 / 103.25 | 1.03 / 0.79 |
| bytecap-64-0 | 124.99 / 24.87 | 146.17 / 232.77 | 2.96 / 7.16 | 0.67 / 0.54 |
| bytecap-64-1 | 121.61 / 24.42 | 150.24 / 211.75 | 10.16 / 5.22 | 0.65 / 0.52 |
| hot-256-0 | 222.91 / 103.67 | 235.71 / 117.69 | 5.38 / 5.90 | 0.46 / 0.32 |
| hot-256-1 | 229.51 / 106.63 | 242.50 / 120.31 | 5.30 / 5.34 | 0.37 / 0.29 |
| cold-256-0 | 595.78 / 95.68 | 606.33 / 119.24 | 14.66 / 6.34 | 1.19 / 0.43 |
| cold-256-1 | 580.92 / 108.08 | 583.86 / 141.60 | 16.49 / 15.19 | 1.20 / 0.45 |
| shared-256-0 | 253.07 / 24.65 | 255.61 / 35.38 | 63.75 / 5.73 | 0.57 / 0.16 |
| shared-256-1 | 191.16 / 25.84 | 201.54 / 82.78 | 6.04 / 7.52 | 0.48 / 0.22 |
| no_gain-256-0 | 153.05 / 48.30 | 158.89 / 55.04 | 5.41 / 6.81 | 0.73 / 0.35 |
| no_gain-256-1 | 162.45 / 72.78 | 167.43 / 95.16 | 5.64 / 5.34 | 0.74 / 0.37 |
| medium-256-0 | 30.36 / 14.16 | 156.10 / 20.10 | 3.48 / 4.12 | 0.16 / 0.12 |
| medium-256-1 | 37.90 / 14.36 | 45.56 / 21.96 | 2.47 / 4.80 | 0.14 / 0.12 |
| large-256-0 | 40.25 / 16.23 | 44.42 / 48.71 | 7.10 / 2.38 | 0.17 / 0.16 |
| large-256-1 | 36.02 / 16.13 | 46.82 / 23.87 | 2.26 / 2.41 | 0.16 / 0.13 |
| oversized-256-0 | 406.07 / 180.75 | 419.99 / 195.15 | 90.43 / 89.03 | 1.09 / 0.63 |
| oversized-256-1 | 421.42 / 170.66 | 433.34 / 186.49 | 92.50 / 86.15 | 1.13 / 0.61 |
| bytecap-256-0 | 128.79 / 25.63 | 169.53 / 164.82 | 6.64 / 2.68 | 0.69 / 0.43 |
| bytecap-256-1 | 123.97 / 27.49 | 158.08 / 148.78 | 8.20 / 2.55 | 0.69 / 0.42 |

All quantiles, preparation times, commits, storage guards, SQL categories, and throughput remain in the two JSONL logs.
Small cases have few lease samples; their maximum is more informative than an interpolated percentile.
Repeat cold/no-gain cases use new identities. Shared repeats reuse the same exact content identity.

### Final storage and source distributions

| Run / phase / metric | P50 | P90 | P95 | P99 | Maximum | Samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline matrix / hold | 9.76 | 123.97 | 178.79 | 438.56 | 595.78 | 252 |
| Baseline matrix / foreground_wait | 5.98 | 15.34 | 43.74 | 194.18 | 606.33 | 1700 |
| Baseline matrix / heartbeat | 0.36 | 1.19 | 2.46 | 5.83 | 134.19 | 5930 |
| Baseline matrix / guard | 3.49 | 3.87 | 4.33 | 6.02 | 15.89 | 456 |
| Baseline matrix / commit | 0.07 | 0.14 | 0.32 | 24.14 | 115.84 | 2002 |
| Current matrix and sources / hold | 14.13 | 38.04 | 40.00 | 44.02 | 243.17 | 4159 |
| Current matrix and sources / foreground_wait | 17.29 | 45.77 | 49.94 | 61.23 | 4481.15 | 9510 |
| Current matrix and sources / heartbeat | 0.54 | 2.06 | 3.15 | 5.39 | 228.19 | 46431 |
| Current matrix and sources / guard | 3.04 | 3.76 | 3.85 | 4.78 | 15.55 | 6970 |
| Current matrix and sources / commit | 0.07 | 0.40 | 0.53 | 9.53 | 118.16 | 15021 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / hold | 15.63 | 39.79 | 40.75 | 44.89 | 243.17 | 2192 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / foreground_wait | 24.56 | 48.57 | 52.19 | 62.55 | 256.09 | 4869 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / heartbeat | 0.64 | 2.47 | 3.69 | 6.13 | 228.19 | 27438 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / storage_guard | 2.90 | 3.63 | 3.74 | 4.32 | 11.18 | 3655 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / commit | 0.06 | 0.42 | 0.49 | 9.48 | 32.40 | 7791 |
| Current whole-source-01a0160e-e2cf-7060-8282-35f7a88ee295 / preparation | 14.84 | 139.46 | 146.21 | 158.01 | 302.04 | 2192 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / hold | 9.53 | 34.55 | 35.48 | 38.00 | 48.95 | 1715 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / foreground_wait | 11.86 | 42.85 | 45.37 | 51.76 | 77.35 | 3303 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / heartbeat | 0.48 | 1.59 | 2.41 | 4.75 | 58.91 | 13456 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / storage_guard | 3.14 | 3.82 | 3.88 | 4.62 | 15.55 | 2859 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / commit | 0.06 | 0.12 | 4.53 | 9.54 | 19.43 | 5590 |
| Current whole-source-01a08171-a93c-7b21-9689-b34ffc2f73fc / preparation | 15.87 | 27.91 | 50.06 | 302.51 | 650.07 | 1715 |

Combined matrix-plus-source distributions are not paired matrix comparisons. Per-case rows above provide paired comparisons.

### Representation work for the complete matrix

Dictionary and payload/owner counts measure SQL statements, including bulk statements; they do not count affected rows.
Compression and decode counts measure function calls. Each cell lists baseline/current.

| Case | Preparation P95, ms B/C | Dictionary queries B/C | Native compression B/C | Decode calls B/C | Byte decodes B/C | Payload inserts B/C | Payload updates B/C | Owner/metadata updates B/C |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| hot-32-0 | 5.22 / 15.79 | 0 / 0 | 0 / 0 | 64 / 0 | 64 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| hot-32-1 | 4.33 / 15.47 | 0 / 0 | 0 / 0 | 64 / 0 | 64 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| cold-32-0 | 39.35 / 18.68 | 102 / 1 | 32 / 32 | 64 / 0 | 96 / 0 | 1 / 1 | 32 / 0 | 1 / 0 |
| cold-32-1 | 29.98 / 19.53 | 102 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 1 / 1 | 32 / 0 | 1 / 0 |
| shared-32-0 | 25.49 / 4.44 | 71 / 0 | 32 / 1 | 33 / 0 | 34 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| shared-32-1 | 29.53 / 4.84 | 70 / 0 | 32 / 0 | 33 / 0 | 34 / 1 | 0 / 0 | 1 / 1 | 1 / 1 |
| no_gain-32-0 | 33.36 / 17.37 | 38 / 0 | 64 / 32 | 64 / 0 | 96 / 0 | 1 / 1 | 32 / 0 | 1 / 0 |
| no_gain-32-1 | 30.30 / 20.51 | 38 / 0 | 64 / 32 | 64 / 0 | 96 / 0 | 1 / 1 | 32 / 0 | 1 / 0 |
| medium-32-0 | 11.77 / 14.31 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| medium-32-1 | 14.45 / 10.87 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-32-0 | 14.80 / 13.44 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-32-1 | 15.38 / 13.76 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-32-0 | 84.85 / 85.57 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-32-1 | 81.62 / 87.67 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| bytecap-32-0 | 49.92 / 45.31 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |
| bytecap-32-1 | 51.34 / 46.76 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |
| hot-64-0 | 6.86 / 20.21 | 0 / 0 | 0 / 0 | 128 / 0 | 128 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| hot-64-1 | 8.84 / 27.26 | 0 / 0 | 0 / 0 | 128 / 0 | 128 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| cold-64-0 | 58.58 / 45.79 | 198 / 0 | 64 / 64 | 128 / 0 | 192 / 0 | 1 / 1 | 64 / 0 | 1 / 0 |
| cold-64-1 | 53.25 / 44.87 | 198 / 0 | 64 / 64 | 128 / 0 | 192 / 0 | 1 / 1 | 64 / 0 | 1 / 0 |
| shared-64-0 | 46.61 / 8.96 | 132 / 0 | 64 / 0 | 65 / 0 | 65 / 0 | 0 / 0 | 0 / 0 | 1 / 1 |
| shared-64-1 | 45.41 / 6.32 | 132 / 0 | 64 / 0 | 65 / 0 | 65 / 0 | 0 / 0 | 0 / 0 | 1 / 1 |
| no_gain-64-0 | 56.94 / 37.73 | 70 / 0 | 128 / 64 | 128 / 0 | 192 / 0 | 1 / 1 | 64 / 0 | 1 / 0 |
| no_gain-64-1 | 55.45 / 37.45 | 70 / 0 | 128 / 64 | 128 / 0 | 192 / 0 | 1 / 1 | 64 / 0 | 1 / 0 |
| medium-64-0 | 12.26 / 9.47 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| medium-64-1 | 11.58 / 9.86 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-64-0 | 13.52 / 13.63 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-64-1 | 13.14 / 12.63 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-64-0 | 86.17 / 91.72 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-64-1 | 80.41 / 87.93 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| bytecap-64-0 | 48.11 / 51.15 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |
| bytecap-64-1 | 46.49 / 60.05 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |
| hot-256-0 | 14.36 / 56.40 | 0 / 0 | 0 / 0 | 512 / 0 | 512 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| hot-256-1 | 13.96 / 52.19 | 0 / 0 | 0 / 0 | 512 / 0 | 512 / 0 | 1 / 1 | 0 / 0 | 1 / 0 |
| cold-256-0 | 186.84 / 152.45 | 774 / 0 | 256 / 256 | 512 / 0 | 768 / 0 | 1 / 1 | 256 / 0 | 1 / 0 |
| cold-256-1 | 196.24 / 150.37 | 774 / 0 | 256 / 256 | 512 / 0 | 768 / 0 | 1 / 1 | 256 / 0 | 1 / 0 |
| shared-256-0 | 165.15 / 15.79 | 516 / 0 | 256 / 0 | 257 / 0 | 257 / 0 | 0 / 0 | 0 / 0 | 1 / 1 |
| shared-256-1 | 170.85 / 18.40 | 516 / 0 | 256 / 0 | 257 / 0 | 257 / 0 | 0 / 0 | 0 / 0 | 1 / 1 |
| no_gain-256-0 | 187.05 / 146.60 | 262 / 0 | 512 / 256 | 512 / 0 | 768 / 0 | 1 / 1 | 256 / 0 | 1 / 0 |
| no_gain-256-1 | 195.84 / 134.11 | 262 / 0 | 512 / 256 | 512 / 0 | 768 / 0 | 1 / 1 | 256 / 0 | 1 / 0 |
| medium-256-0 | 11.96 / 10.84 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| medium-256-1 | 14.06 / 8.71 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-256-0 | 15.74 / 14.35 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| large-256-1 | 16.95 / 13.61 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-256-0 | 103.00 / 94.80 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| oversized-256-1 | 101.34 / 88.72 | 9 / 0 | 1 / 1 | 2 / 0 | 3 / 0 | 1 / 1 | 1 / 0 | 1 / 0 |
| bytecap-256-0 | 51.02 / 51.44 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |
| bytecap-256-1 | 51.53 / 56.27 | 106 / 0 | 32 / 32 | 64 / 0 | 96 / 0 | 2 / 2 | 32 / 0 | 2 / 0 |

Necessary new-owner changes are distinct from unchanged payload writes. Raw logs retain all SQL category totals.

### Final outliers, acceptance, and limitations

All 48 matched current cases have lower maximum background holds than their baseline cases.
This does not imply every foreground sample improves. The complete table preserves each foreground maximum, including regressions.
The final storage/source run has no background hold of one second or more; its maximum is 243.17 ms.
The giant aggregate maximum is 161.23 ms. These are measured workload results, not universal latency guarantees.

- The final 186,496-row source has a paired reconciliation outlier of 243.17 ms.
  It covers 256 items, 512 pointer slots, and 527,708 accounted bytes.
  SQL execution takes 0.919 ms, the storage guard takes 5.505 ms, and commit takes 0.036 ms.
  The residual Python/thread-scheduling time is not further attributed. No GC explanation is claimed.
  This differs from the original unpaired 354.19 ms sample and from the earlier 246.76 ms metadata-read sample.
- The indivisible 16.8 MB synthetic payload has a maximum current hold of 228.75 ms.
  Its paired record attributes 118.16 ms to commit. The 8 MiB budget still permits one oversized indivisible item.
- The first hot case contains a **4,481.15 ms foreground wait-plus-service** sample.
  Its maximum background hold is **18.69 ms**. Application-worker SQL totals **4.423 seconds** for that case.
  That SQL total includes fixture creation, operation setup, preparation, publication, and cleanup.
  Five preparation units each take at most 15.79 ms; five background holds each take at most 18.69 ms.
  Thus most SQL time lies outside those measured continuation units. The fixture uses the foreground coordinator.
  The exact original slow SQL and its first-read cause remain unresolved. Foreground samples use their completion-phase label.
  The sample stays in all tables and totals; it is not removed from acceptance evidence.
- One bounded diagnostic uses the same owned working copy and only two hot 32-item cases.
  Its initial corpus read takes 2.770 seconds; fixture holds take 19.53 and 11.61 ms.
  Its first foreground admission is 0.033 ms, thread queue 1.614 ms, service 17.012 ms, and SQL 1.755 ms.
  Its maximum foreground sample is 47.75 ms. Warm non-reproduction is not proof of the original cause.
  Script: `scripts/foreground_diagnostic.py`; log: `foreground-diagnostic.jsonl`.
- The 256/8 MiB content budgets and 2,000-item metadata batch remain unchanged.
  Current first cold cases have maximum holds of 17.14, 22.01, and 95.68 ms at 32, 64, and 256 rows.
  Their complete case times are 0.140, 0.184, and 0.434 seconds respectively.
  The 15.36 MB byte-budget cases split 32 items into two extraction and two reconciliation units.
  No measured routine hold justifies another budget or architecture change.

The controller's independent review judges the unresolved foreground outlier and the source maximum regression.
The final comprehensive suite remains controller-owned. The baseline/source comparator limitations remain explicit above.


## Reproduction commands

Run each Python command from `/tmp/twicc-content-optimization-implementation-20261013`.
Runtime: Python 3.13.14 and Django 6.1.2 in the staging `.venv`.
The script asserts its imported `twicc.__file__` and exact disposable database path before writes.
The two final matrix commands use the same `BENCH_RUN_TAG` and separate restores of the same canonical snapshot.

```bash
cd /tmp/twicc-content-optimization-implementation-20261013 && TWICC_DATA_DIR=/tmp/twicc-content-optimization-bench-20261014/working BENCH_CODE_ROOT=/tmp/twicc-content-optimization-bench-20261014/baseline-code PYTHONPATH=/tmp/twicc-content-optimization-bench-20261014/baseline-code/src BENCH_RUN_TAG=final-matched- .venv/bin/python /tmp/twicc-content-optimization-bench-20261014/scripts/benchmark.py > /tmp/twicc-content-optimization-bench-20261014/baseline-final.jsonl 2>&1
cd /tmp/twicc-content-optimization-implementation-20261013 && TWICC_DATA_DIR=/tmp/twicc-content-optimization-bench-20261014/working BENCH_CODE_ROOT=$PWD PYTHONPATH=$PWD/src BENCH_RUN_TAG=final-matched- BENCH_LARGE_SESSIONS=1 .venv/bin/python /tmp/twicc-content-optimization-bench-20261014/scripts/benchmark.py > /tmp/twicc-content-optimization-bench-20261014/current-final.jsonl 2>&1
```

Before each command, close all benchmark workers, remove only the owned working database and its WAL/SHM files,
and restore `snapshot.sqlite` to `working/db/data.sqlite`. Never copy over an open SQLite database.
The final aggregate uses the same current command with `BENCH_SKIP_MATRIX=1 BENCH_AGGREGATES=1`, without `BENCH_LARGE_SESSIONS`.
The legacy aggregate uses the baseline environment with `scripts/baseline_aggregate.py` and default one-root scope.
The memory script uses `scripts/memory.py`; it constructs synthetic journal snapshots and does not write source data.

Affected verification:

```bash
cd /tmp/twicc-content-optimization-implementation-20261013 && bash /tmp/twicc-content-optimization-bench-20261014/scripts/run-affected.sh
cd /tmp/twicc-content-optimization-implementation-20261013 && uvx ruff check $(git diff --name-only -- '*.py') tests/test_tree_aggregate_contention.py
cd /tmp/twicc-content-optimization-implementation-20261013 && git diff --check
```

The controller runs the complete backend suite after independent review and integration.
No frontend files or serializer contracts change, so no frontend build or test run is required by this task.
