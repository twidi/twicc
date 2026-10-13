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

- [ ] Write deterministic contention tests: pause background preparation, perform a foreground watcher append, then resume. Assert foreground completion, rejected stale preparation, exact eventual values, and no duplicate contribution.
- [ ] Block an actual preparation executor query with a barrier. Assert foreground SQL completes through its separate executor while the historical read remains blocked.
- [ ] Write a publication-turn test with queued foreground demand. Assert foreground runs before the next background unit and that a stale retry does not invoke SUM under writer.

**Assertion anchor — blocked historical executor, foreground append:**
```python
assert foreground_completed_before_preparation_release is True
assert eventual_aggregate_values == independent_reference_values
assert duplicate_contribution_count == 0
```

- [ ] Run contention tests and verify they pass on the implementation. Use barriers/events rather than timing-sensitive sleeps for correctness assertions.
- [ ] Run the complete backend suite: `TWICC_DATA_DIR=$PWD uv run pytest`. Run frontend tests only if serializer/frontend code changed; they are not planned here. Run `uvx ruff check <changed-python-files>`.
- [ ] Audit every spec section against implemented tests and every contribution writer against the mutation coverage matrix. Check external CLI/front payload content remains at the existing field with no N+1 policy imposed globally.
- [ ] Use the proven devctl SQLite backup method (`source.backup(target, pages=-1)`, read-only source) to create a throwaway DB copy. Do not start DevCTL or apply branch migrations manually. Check source/target and resolved Django DB paths before any benchmark.
- [ ] Compare baseline and implementation from identical backup snapshots and dictionaries. Include hot/cold sessions, 32/64/256-item content batches, medium/large individual payloads, shared contents, no-gain rows, and the large 145,950/186,496-item sessions when available. Record dictionary queries, compression/decode calls, owner/payload writes, and preparation/publication phase times.
- [ ] Exercise foreground watcher/lifecycle writes during each benchmark. Record writer hold P50/P90/P95/P99/max, foreground wait distribution, event-loop heartbeat delay, source throughput, and whole-tree aggregate completion time. Separate first-read/cache effects from repeat samples.
- [ ] Keep 256/8 MiB as the initial content publication budgets. Evaluate 64 rows only after removing redundant work; select the smallest change justified by measured foreground holds and overall throughput. No new universal latency guarantee is inferred from a memory-only test.
- [ ] Treat any routinely observed background hold of one second or more as a failed result. Attribute oversized content, guard, SQL, and commit components; do not accept averages that conceal the failure. If a remaining representation write cannot meet foreground needs, stop for a revised bounded-publication design rather than silently changing schema or weakening integrity.
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
