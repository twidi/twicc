# Content Storage and Cold Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract SessionItem contents progressively, preserve exact external behavior, and compress only cold native session trees.

**Architecture:** Content rows retain immutable logical UTF-8 data with mutable plaintext/zstd representation and exact JSON ownership. Existing ingestion and newest-first compute migrate references; a bounded maintenance coordinator performs periodic compression and immediate recoverable hot promotion. Root generations protect plaintext readiness, while explicit loaders preserve mixed-storage reads and serializers.

**Tech Stack:** Existing Python ≥3.13, Django, SQLite WAL, Channels, orjson, uv; add Python zstandard compatible with the measured 0.25.0 API.

**Spec:** [consolidated specification](2026-10-11-session-item-content-extraction-spec.md).

**Research:** [impact audit](2026-10-11-session-item-content-extraction-audit.md): section 11 covers compression integration; section 11.13 preserves benchmark evidence and public dictionary validation.

**Dictionary inputs:** [reviewed public policy](../../scripts/content_dictionary_policy.json) and [offline generator](../../scripts/benchmark_public_schema_dictionaries.py).

## Global Constraints

- Existing contents move through the existing provider background compute system.
- No RunPython existing-content conversion, item-table rebuild, global content scan, or vacuum runs at startup.
- `current_content` and `original_content`: nullable real FKs, `db_index=False`, `on_delete=PROTECT`.
- Keep the legacy content column; new writes use `''` and progressive extraction clears old values.
- Keep exact logical strings, SHA256 of their UTF-8 bytes, line numbering, and consumer-specific payload shapes.
- One binary payload column; explicit plaintext/zstd codec; zstd level 3.
- One immutable public dictionary per provider, maximum 16 KiB; no live training or private dictionary literals.
- `Session.is_cold_for_content` uses fifteen days for archived/hidden roots and thirty days for all other roots.
- `Session.is_cold_for_scratch` requires archived OR hidden plus thirty days since the latest lifecycle date.
- Every native subagent inherits root cold status, at every depth; orchestration roots remain independent.
- Any actual hot owner through either FK requires persistent plaintext; active trees cannot be compressed.
- Public cold/warming substring operations fail explicitly; ordinary reads decode requested contents only.
- Periodic maintenance starts thirty minutes after startup, then runs every twenty-four hours.
- No content GC, automatic column removal, automatic vacuum, persistent cold-read cache, or new fork-resolution behavior.
- No always-joined manager or universal content loading; preserve explicit async database boundaries.
- Preserve current checkout/user files. Create no branch/worktree or restart servers without explicit user request.
- English code/docs. NamedTuple for immutable records; orjson for backend JSON; no changelog change.
- This document authorizes no implementation, package installation, production migration, or server restart.

## Review Focus

1. Pending STARTING registration versus cold publication: start admission cannot deadlock or publish stale eligibility. Task 1/11 tests cover this.
2. Very large shared originals and small transformed results: both references remain owned, exact, and bounded. Task 5/9/15 tests cover this.
3. Unresolved native ancestry, cycles, and late parent discovery: reads remain available; contains readiness is never guessed. Task 4/11 tests cover this.
4. Interrupted replacement/reset with reused session IDs: stale memberships and generations cannot authorize compression or search. Task 10/12 tests cover this.
5. Filtered candidates before a late matching image/attachment: decoding preserves prefilter limits and pagination. Task 7/13 tests cover this.

## Execution conventions

Run project commands from the chosen checkout. Main checkout commands below use `cd /home/twidi/dev/twicc-poc &&`.
For an explicitly requested worktree, replace that path and prefix Python/Django probes with `TWICC_DATA_DIR=$PWD`.
Pytest uses its isolated test database. Never point a test or migration probe at the live data directory.

Each code task follows its listed red/green cycle and ends with a separate reviewable commit.
Commit subjects below are suggestions. Every commit needs a descriptive body and the environment's exact Codex-model co-author trailer.
Stage only task-owned files; never use `git add .` or commit unrelated documents.
The plan-writing task creates no commits.

Do not release intermediate commits. Legacy reads make scaffolding testable; activation follows completion of all dependent paths.
At execution start, reread AGENTS.md/AGENTS.local.md and the spec. Recheck source anchors against current HEAD.
Current production compute versions are Claude 114 and Codex 55; resolve drift before choosing activation targets.

## File map and responsibility boundaries

| File | Responsibility |
|---|---|
| `src/twicc/core/models.py` | Content/dictionary models, item references, root storage generations, explicit content/scratch cold properties. |
| `src/twicc/core/content_storage/types.py` | Shared immutable records and explicit unloaded sentinel. |
| `src/twicc/core/content_storage/codec.py` | Exact UTF-8 encode/decode, dictionary loading, hash/length integrity. |
| `src/twicc/core/content_storage/policy.py` | Loaded native-root decisions, compression-policy identity, no-gain rules. |
| `src/twicc/core/content_storage/references.py` | Transactional hash reuse, persisted item reference assignment, exact membership updates. |
| `src/twicc/core/content_storage/readers.py` | Explicit effective-current loaders, projections, decoded candidate iteration. |
| `src/twicc/core/content_storage/state.py` | Durable root generations, invalidation, promotion cursor verification. |
| `src/twicc/content_maintenance_task.py` | Event-driven promotion and periodic bounded maintenance orchestration. |
| `src/twicc/providers/db_writer.py` | Existing writer ownership, admission fence, bounded publication dispatch, compute apply integration. |
| `src/twicc/providers/compute_base.py` | Capture originals, decoded compute inputs, progressive extraction, final-date guards. |
| `src/twicc/providers/*/initial_sync.py`, `sessions_watcher.py` | Existing producer contracts and creation/ancestry transitions; no producer content-table writes. |
| `src/twicc/providers/codex/rollout_migration.py` | Replacement begin/chunks/finalization and generation recovery. |
| `src/twicc/core/services/session_update.py`, `session_visibility.py`; `agent/base_manager.py`; `views.py` | Lifecycle invalidation within actual existing writes. |
| `src/twicc/session_dirs_cleanup_task.py`, `cli/run.py` | Preserve filesystem cleanup and replace timer ownership with combined maintenance lifecycle. |
| `src/twicc/core/serializers.py`, `session_queries.py`; provider helpers/history; CLI/search/share readers | Consume loaded effective strings, preserve all external shapes. |
| `scripts/benchmark_public_schema_dictionaries.py`, `scripts/content_dictionary_policy.json` | Offline public dictionary generation and reproducible manifest selection. |
| `src/twicc/core/content_storage/dictionaries/` | Fixed `.dict` release assets and manifest; no raw corpus. |
| `tests/content_storage_helpers.py`, `tests/test_content_*.py` | Mixed-storage fixtures and meaningful integrity/recovery/concurrency contracts. |

Create `src/twicc/core/content_storage/__init__.py` without eager imports. It must not create a models/agent/writer import cycle.
Migration filenames use the next number at execution; do not reserve a stale migration number now.

## Shared interfaces

Define these records in `types.py`; do not invent differently named variants in later tasks.

| Record | Fields |
|---|---|
| `EncodedContent` | `payload: bytes`, `codec: str`, `dictionary_id: int | None`, `raw_size: int`, `sha256: bytes` |
| `ContentBinding` | `item_id: int`, `session_id: str`, `current_id: int`, `original_id: int | None` |
| `RootDecision` | `root_id: str | None`, `is_cold: bool`, `resolved: bool`, `active: bool` |
| `StorageCursor` | `line_num: int`, `rows: int`, `bytes: int`, `done: bool` |
| `RootPromotionCursor` | `session_id: str | None`, `line_num: int`, `fk_slot: int`, `rows: int`, `bytes: int`, `done: bool` |
| `RootStorageSnapshot` | `root_id: str`, `incarnation: str`, `epoch: int` |
| `PreparedRepresentation` | `content_id: int`, `observed_codec: str`, `observed_dictionary_id: int | None`, `encoded: EncodedContent`, `policy_version: str`, `no_gain: bool` |

`UNLOADED = object()` distinguishes unloaded from empty strings.
Codec values are `plain` and `zstd`. All plain payloads are UTF-8 bytes with NULL dictionary.
Session storage columns are nullable `content_storage_version`, `content_state_epoch`, and `plaintext_verified_epoch`, and `storage_incarnation`, without database defaults or indexes.
Readiness uses verified equality at the resolved native root, plus current hot/active policy and required extraction checks.
Increment epochs transactionally; do not set a generic boolean ready after an old promotion result.
`storage_incarnation` is a fresh UUID string assigned transactionally on session creation, never reused after deletion.
Legacy NULL incarnations initialize per session before dispatching a read-only compute worker, not through a migration/global startup backfill.
Every compute/extraction observation includes that session incarnation; root promotion includes the root incarnation and epoch.
Task 8 initializes identities in new and touched existing session writer paths; Task 9 prepares untouched legacy identities before worker dispatch. Task 4 verification initializes or rejects unknown root identity under writer ownership, never guesses it from the session ID.
A new incarnation rejects old results even when IDs, offsets, epochs and line boundaries repeat.

Ownership deletion/replacement remains in its existing transaction in this release. Streaming bounds memory, not transaction duration.
Do not split removal across transactions or add a pending-reconciliation alternative implicitly.
If Task 1 measurements reject this choice, amend schema/recovery interfaces and review the plan before Task 2 proceeds.

`RootPromotionCursor` orders native sessions by ID, items by line_num, then FK slot 0=current and 1=original.
Task 4 provides bounded native-tree membership pages; root epoch/incarnation guard the traversal.
After a budget stop, store the last completed FK slot and resume strictly after that key.
A shared/already-plain row advances the cursor without another payload write.
A root epoch change restarts the traversal; a process restart may start from zero using the durable pending generation.
Progress cursors are operation-local, not new permanent cursor columns.

---

### Task 1: Validate publication admission and ownership cost before dependent implementation

**Files:** Modify the audit's section 11; create `tests/test_content_storage_admission.py`; modify `providers/db_writer.py` for minimal admission scaffolding; create a temporary isolated probe only during execution.

**Interfaces:** Consumes current `run_under_db_write_lock`, `_drain_one`, manager registration, compute executor. Produces the validated admission ordering and bounded-reference-removal strategy used in Tasks 5/9/11/12.

- [ ] **Step 1: Pin the admission invariant in failing deterministic tests.**

`test_pending_start_and_compression_never_invert_locks`: pause registration before DB persistence; enqueue compute/maintenance publication; release barriers; both operations complete; the admitted active tree is never compressed by stale work.
`test_cancelled_publication_keeps_ownership_until_worker_finishes`: cancel await while worker is paused; no second writer enters before the first worker finishes.
Use the existing writer/executor fixtures and controlled fake publication/start actions; no new content schema or codec is required yet. Task 11 repeats these scenarios against real registration/publication. Initial failure exposes missing admission behavior, not a missing zstandard install.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
# test_pending_start_and_compression_never_invert_locks
await asyncio.wait_for(asyncio.gather(start_task, publication_task), timeout=1)
assert admission_trace.index("fence_acquired") < admission_trace.index("writer_acquired")
assert publication_result is False  # start admission wins this controlled ordering
# test_cancelled_publication_keeps_ownership_until_worker_finishes
assert second_writer_entered.is_set() is False
worker_release.set()
await worker_finished.wait()
assert ownership_released.is_set()
```

- [ ] **Step 2: Run the red tests.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_storage_admission.py -v`.
Expected: invariant failure before storage admission exists.

- [ ] **Step 3: Validate this chosen fence design against actual call ordering.**

Add an asyncio storage-admission fence owned by `providers/db_writer.py`. Agent registration acquires it before installing registry state and before writer acquisition. Compression-capable compute results acquire it before `_run_under_db_write_lock`; maintenance publication does the same. No holder acquires provider manager locks.
Order: existing manager lock, when applicable → storage-admission fence → writer lease → DB transaction. Never acquire the fence from inside a writer-held apply. Snapshot active roots while holding the fence; pass immutable protection to synchronous publication.
Keep candidate CPU outside the maintenance fence. Existing initial compute result-wide lease/fence may span result application; measure its added wait and do not extend this to an entire compute run.
Integrate admission/cancellation with the existing drive wrapper. A background child that reacquires writer ownership must not be awaited under the parent's lease.

If a real registration path violates this order, correct the ordering proposal in the audit and plan before continuing. Do not ship a partially verified alternative. The minimal scaffold exports `storage_admission()` now; actual registration hooks activate in Task 11.

- [ ] **Step 4: Measure grouped membership removal on an isolated representative session fixture.**

Use both FK columns and the existing session index. Compare one scan per displaced-ID batch against per-ID scans; include repeated originals and at least 100,000 items. Record elapsed time, allocation and WAL. Select grouped scans and existing transactional whole-history removal for this release; record their measured costs. If repeated full-session scans dominate, define operation-scoped reference accounting before Task 5. If whole-history transactional ownership removal is unacceptable, stop before Task 2 and explicitly review the replacement durable schema/recovery contract.
No full production DB copy is needed for this algorithm probe. Any real-data copy must use devctl's `Connection.backup(target, pages=-1)` method and stay isolated.

- [ ] **Step 5: Record the measured design verdict and rerun the admission tests.**

Expected: PASS; no pending lock-order choice reaches Task 9 or Task 12.
Commit: `test(storage): pin storage admission and ownership design` with audit evidence and any minimal admission scaffolding needed for the test.

### Task 2: Add storage schema, public dictionary assets, and dependency declaration

**Files:** Modify `core/models.py`, `pyproject.toml`, `uv.lock`, dictionary generator; create dictionary assets/manifest and schema-only migrations; create `tests/content_storage_helpers.py` with the shared `content_row_factory` fixture; test `tests/test_content_storage_schema.py`, `tests/test_content_dictionary_assets.py`.

**Interfaces:** Produces `SessionItemContent`, `ContentDictionary`, the columns named above, and the dictionary manifest. Existing item text remains usable. The shared `content_row_factory(text: str, compressed: bool, provider: str = "codex") -> SessionItemContent` fixture supplies Task 3 tests; it does not depend on Task 5 reference services.

- [ ] **Step 1: Write schema and asset tests.**

`test_item_content_fks_are_nullable_protected_and_unindexed`: both references have those exact properties.
`test_logical_hash_is_unique_binary_32_bytes`: equal exact UTF-8 deduplicates; differently serialized JSON remains different.
`test_payload_is_single_binary_field`: no duplicate compressed/plain columns.
`test_session_state_additions_have_no_db_default_or_index`: includes the three marker columns and the nullable incarnation column.
`test_bundled_dictionaries_match_manifest`: providers `claude_code`/`codex`, raw format, size ≤16,384 bytes, exact SHA256/version identity.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
for name in ("current_content", "original_content"):
    field = SessionItem._meta.get_field(name)
    assert field.null is True and field.db_index is False
    assert field.remote_field.on_delete is PROTECT
for name in ("content_storage_version", "content_state_epoch", "plaintext_verified_epoch", "storage_incarnation"):
    field = Session._meta.get_field(name)
    assert field.null is True and field.db_index is False
    assert field.has_db_default() is False
assert len(dictionary.payload) <= 16384
assert hashlib.sha256(dictionary.payload).digest() == dictionary.sha256
```

- [ ] **Step 2: Run red schema/asset tests.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_storage_schema.py tests/test_content_dictionary_assets.py -v`.
Expected: missing new models/assets.

- [ ] **Step 3: Generate and freeze release dictionaries.**

Use the current reviewed generator/policy on the explicitly selected read-only local DB. Choose `templates_raw_16384` for each provider. Copy only those sanitized candidate bytes into the package; manifest records provider, version 1, raw format, SHA256, byte length, policy hash and seed. Remove temporary candidates; retain no sample text or IDs.
Add a generator option selecting/finalizing these assets without exporting private data. Keep historical no-dictionary/trained candidates out of the release directory.

- [ ] **Step 4: Define models and create nullable additive migrations.**

Content fields: numeric PK, unique 32-byte binary hash, binary payload, codec, protected nullable dictionary FK, raw size, JSON session IDs, stable first-insert `dictionary_provider`, attempted-policy string, no-gain bool.
Dictionary fields: numeric `id`, `provider`, `version`, `format`, unique binary `sha256`, immutable binary `payload`; enforce unique provider/version.
New-table fields may have normal defaults. Existing Session/SessionItem additions must use nullable/no-db-default/no-index operations. Add nullable `storage_incarnation` with no ORM default; initialize UUIDs in normal writer preparation/creation.
Seed fixed dictionary assets only; no content conversion RunPython. Update dependency declaration/lock for `zstandard~=0.25.0` when implementation is authorized; never use `uv pip` or `--active`.

- [ ] **Step 5: Run green schema/asset tests and migration effect checks.**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_storage_schema.py tests/test_content_dictionary_assets.py tests/test_sqlite_migration_schema.py tests/test_sqlite_migration_effects.py -v`.
Inspect generated SQL: no existing item rebuild, child FK index, or legacy-table-wide FK check, including dictionary seeds. Representative full-copy timing belongs to Task 15.
Commit: `feat(storage): add external content schema and public dictionaries`.

### Task 3: Implement lossless codec and representation integrity

**Files:** Create `content_storage/types.py`, `codec.py`, initial `policy.py`; test `tests/test_content_codec.py`.

**Interfaces:**
- `encode_plain(text: str) -> EncodedContent`
- `encode_cold(text: str, dictionary_id: int, policy_version: str) -> tuple[EncodedContent, bool]`
- `decode_content(row: SessionItemContent) -> str`
- `compression_policy_version(provider: str, dictionary_id: int) -> str`
- `ContentIntegrityError` for invalid/missing/corrupt content or dictionary state.

- [ ] **Step 1: Write round-trip and no-gain tests.**

```python
def test_exact_codec_roundtrip(content_row_factory):
    text = '  {"text":"é\\n","a":1}  '
    row = content_row_factory(text=text, compressed=True)
    assert decode_content(row) == text
    assert row.sha256 == hashlib.sha256(text.encode('utf-8')).digest()
```
Also cover empty/malformed JSON, embedded NUL, a multi-megabyte payload, raw-size/hash mismatch, missing dictionary, corrupt frame, old dictionary after a new version, and compression expansion. Plaintext always has NULL dictionary.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
# test_invalid_raw_size_and_missing_dictionary_are_integrity_errors
with pytest.raises(ContentIntegrityError):
    decode_content(row_with_incorrect_raw_size)
with pytest.raises(ContentIntegrityError):
    decode_content(row_with_missing_dictionary)
encoded, no_gain = encode_cold(incompressible_text, dictionary.id, policy_version)
assert no_gain is True
assert encoded.codec == "plain" and encoded.dictionary_id is None
```

- [ ] **Step 2: Run red codec tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_codec.py -v`.
Expected: missing codec implementation.

- [ ] **Step 3: Implement exact UTF-8 codecs and policy identity.**
Use zstd level 3 and stored raw dictionary bytes. Retain plain bytes when zstd output is not smaller; return no-gain separately. Include codec/level/dictionary digest/savings rule in stable policy identity.
Validate state, expected output size and exact digest. Bound decoder/dictionary caches by immutable identity; do not cache every decoded content globally.

- [ ] **Step 4: Run green codec tests and commit.**
Expected: PASS including large-row byte equality.
Commit: `feat(storage): add lossless content codecs`.

### Task 4: Implement loaded root policy and durable generations

**Files:** Create `content_storage/state.py`; extend `policy.py`, `models.py`; test `tests/test_content_cold_policy.py`, `tests/test_content_root_state.py`.

**Interfaces:**
- `cold_from_fields(fields: dict, now: datetime) -> bool` for content.
- `scratch_cold_from_fields(fields: dict, now: datetime) -> bool` for scratch.
- `resolve_scratch_root_decisions(session_ids, now, active_root_ids)` selects scratch eligibility explicitly.
- `resolve_root_decision(session_id: str, now: datetime, active_root_ids: frozenset[str]) -> RootDecision`
- `invalidate_roots(root_ids: Iterable[str]) -> None` inside caller's transaction.
- `ensure_storage_incarnation(session_id: str) -> str` inside caller's transaction.
- `iter_native_tree_session_pages(root_id: str, after_id: str | None, page_size: int) -> Iterator[list[str]]`: stable ID order, unresolved/cyclic ancestry fails conservatively.
- `snapshot_root_storage(root_id: str) -> RootStorageSnapshot`
- `verify_plaintext_root(snapshot: RootStorageSnapshot) -> bool` guarded verification/publication.
- `Session.is_cold_for_content` and `Session.is_cold_for_scratch` use cached native ancestry and distinct predicates; no stored cold column.

- [ ] **Step 1: Write exact boundary and generation tests.**
Content: archived/hidden roots qualify at exactly 15 days; other roots qualify at exactly 30 days. Scratch: archived/hidden roots qualify at exactly 30 days. Latest of four dates decides. Missing dates cannot authorize cold placement. Resolved dateless roots may publish readiness after complete plaintext/extraction verification. Missing parents, cycles, and inconsistent ancestry cannot authorize cold placement or readiness. Nested native subagents inherit root flags/dates; independent spawned sessions do not.
`test_stale_promotion_epoch_cannot_mark_ready`: snapshot epoch N, invalidate to N+1, verify old snapshot returns False.
`test_tree_change_invalidates_both_roots`: move a native descendant and verify old/new epochs change.
`test_same_id_new_incarnation_rejects_old_promotion`: delete/recreate with matching epochs; old snapshot cannot complete.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert cold_from_fields(old_archived_fields, now) is True  # exactly fifteen days
assert cold_from_fields(recent_archived_fields, now) is False  # fourteen days
assert resolve_root_decision(cyclic_child.id, now, frozenset()).resolved is False
snapshot = snapshot_root_storage(root.id)
invalidate_roots([root.id])
assert verify_plaintext_root(snapshot) is False
assert recreated_root.storage_incarnation != snapshot.incarnation
assert verify_plaintext_root(snapshot) is False
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_cold_policy.py tests/test_content_root_state.py -v`.

- [ ] **Step 3: Implement pure date predicate, explicit root resolution, and transactional epochs.**
Use metadata projections and visited IDs; unresolved ancestry returns `resolved=False`. Root generation advances once per mutation group. Initialize session incarnation once; it never changes during that session row's lifetime. Snapshot verification must compare incarnation before epoch. Verification checks tree membership, root incarnation, current generation, both FK columns, payload codec and extraction requirements; inline legacy strings count as plaintext while incomplete extraction remains separately tracked.
Use bounded root/tree/item pages. Do not hold an unbounded set of content PKs for a whole tree.

- [ ] **Step 4: Run green tests and commit.**
Commit: `feat(storage): track native root plaintext readiness`.

### Task 5: Centralize hash reuse and exact persisted ownership

**Files:** Create `content_storage/references.py`; test `tests/test_content_references.py`; extend `tests/content_storage_helpers.py`.

**Interfaces:**
- `intern_contents(texts: Sequence[str], provider: str, prepared: Mapping[bytes, EncodedContent] | None = None) -> dict[str, int]` bounded input, caller-owned transaction. Keys of prepared are exact logical digests; encoded bytes are fully verified against the corresponding text.
- Supplying prepared compressed inserts requires the caller's guarded owner/root/final-date eligibility proof. An existing hash is reused as-is; insertion does not silently overwrite shared representation.
- `bind_persisted_items(bindings: Sequence[ContentBinding]) -> None` validates actual persisted item identity/reference assignments.
- `reconcile_session_memberships(session_id: str, displaced_ids: Sequence[int]) -> None`
- `remove_session_memberships(session_id: str, content_ids: Iterable[int]) -> None`

- [ ] **Step 1: Write reference mutation tests.**
Equal hashes reuse compressed rows without changing strings. A mocked digest collision raises integrity failure. Repeated items and original=current add one session ID. Displacing current preserves membership when original/another item remains. Ignored insert conflicts do not add phantom owners. Hot compressed reuse invalidates root readiness in the same transaction. Rollback restores FK/list/legacy state together.
`test_shared_large_original_and_small_result_keep_both_owners`: assert ownership and exact bytes for both rows.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert set(shared_source.session_ids) == {root.id, fork.id}
assert set(transformed.session_ids) == {root.id}
reconcile_session_memberships(root.id, [shared_source.id])
shared_source.refresh_from_db()
assert root.id in shared_source.session_ids  # original or another item survives
assert unused_conflict_content.session_ids == []
assert hot_root.plaintext_verified_epoch != hot_root.content_state_epoch
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_references.py -v`.

- [ ] **Step 3: Implement the Task 1 selected grouped mutation algorithm.**
Hash exact UTF-8; conflict-tolerant insert then fetch PKs and verify equality. New rows use verified prepared bytes when supplied; otherwise plain bytes. Binding and membership commit with the insert, so a new eligible cold row never has a persisted plain intermediate. Use actual persisted assignments after ignore-conflicts; never overwrite enriched current refs with raw duplicates. Keep provider affinity fixed at first insert.
Reconcile displaced IDs once per bounded batch using both FK columns and session scope. Lists are unique and deterministically serialized. No reverse FK index, signal-only ownership, or global scan per item.
For large multi-batch overrides, use the Task 1 selected operation-scoped reference accounting. Activate it when estimated item scans reach 1,000,000 across at least two mutation batches (session item count × expected batch count). This provisional engineering threshold is validated/tuned in Task 15.
Build one connection-local temporary SQLite content-reference count table from both FK slots through the session index. Hold the existing writer lease across initialization and all mutation deltas. Update counts from actual persisted old/new assignments only; ignored conflicts and superseded guards contribute no delta. Count deltas share the item/list transaction, so rollback restores all three. Remove membership only at zero; original=current counts twice but owns one membership.
Expose an operation-scoped context/facility to bounded override callers without changing the existing public binding/reconciliation signatures. Keep grouped reconciliation for small isolated batches and streamed whole-history ownership removal in its existing transaction. Drop temporary counts in `finally`; retries rebuild from the authoritative graph, including earlier committed chunks. Use SQLite temporary storage instead of an unbounded Python map. No durable schema, split ownership-removal transaction, or pending-reconciliation recovery state is added.
Add tests for multiple batches, both FK slots, duplicate originals, ignored conflicts, rollback, and retry after a partial commit. Task 1 measures 54.630 ms count initialization plus 19.997 ms for fifty lookups, versus 3,617.830 ms for fifty grouped rescans on 100,000 items; this excludes assignment-delta write cost.

- [ ] **Step 4: Run green tests and commit.**
Commit: `feat(storage): maintain shared content references and ownership`.

### Task 6: Add explicit effective-current loaders and mixed projections

**Files:** Create `content_storage/readers.py`; test `tests/test_content_readers.py`.

**Interfaces:**
- `effective_current(item: SessionItem) -> str`: pure accessor, no SQL; unsaved current override wins.
- `load_current(items: Sequence[SessionItem]) -> None`: synchronous bounded loader attaching decoded values.
- `aload_current(items: Sequence[SessionItem]) -> None`: explicit async boundary.
- `iter_current_items(queryset: QuerySet, row_budget: int, byte_budget: int) -> Iterator[SessionItem]`
- `iter_matching_items(queryset: QuerySet, terms: Sequence[str], limit: int | None = None) -> Iterator[SessionItem]`: existing textual semantics, ordered decoded candidates.
- `effective_plaintext_expression() -> Expression`: current-FK-selected CAST text/legacy CASE for ready-hot predicates.

- [ ] **Step 1: Write mixed-state tests.**
Legacy, plain, zstd, empty string, unloaded, in-memory override, missing current row, and cleared legacy. Accessor executes zero SQL; missing current never returns stale legacy. A page with repeated large PKs loads/decodes each distinct PK once. Async serialization triggers no synchronous query. Updating current reference invalidates loaded/parsed values.
`test_decoded_prefilter_matches_sqlite_text_semantics`: ASCII case, non-ASCII case, escaped `_`/`%`/backslash, embedded NUL and empty terms.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
with django_assert_num_queries(0):
    assert effective_current(loaded_empty_item) == ""
with pytest.raises(ContentIntegrityError):
    load_current([missing_current_item])
load_current(repeated_pk_page)
assert decode_spy.call_count == 1
assert effective_current(repeated_pk_page[0]) == original_text
assert list(matched_line_numbers) == list(sqlite_text_matched_line_numbers)
```

- [ ] **Step 2: Run red reader tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_readers.py -v`.

- [ ] **Step 3: Implement explicit loaders and textual matching.**
Use the task-scoped distinct-PK map, stored dictionary identity, and codec API. Read row and byte budgets incrementally; permit one oversized row. Metadata-only queries fetch no payload. No always-joined manager.
Reproduce SQLite LIKE prefilter behavior with a tested helper; preserve escaped literal terms and NUL behavior. Validate CASE/CAST through actual Django SQLite queries. Use the existing reader's synchronous executor boundary, not the single writer executor for every read.

- [ ] **Step 4: Run green reader tests and commit.**
Commit: `feat(storage): load mixed content explicitly`.

### Task 7: Convert consumers and compression-safe internal predicates

**Files:** Modify all audit section 3/4 consumers: `core/serializers.py`, `core/session_queries.py`, `providers/history_facts.py`, `providers/compute_base.py`, both provider `compute.py`, both provider `helpers.py`, watcher projections, `providers/claude_code/workflow_synthesis.py`, `views.py`, `search.py`, title consumers, `share/session_views.py`, `share/display.py` and transitive message/wait consumers identified by the audit. Test `tests/test_content_consumers.py` plus existing regressions.

**Interfaces:** Existing serializers/helper payloads remain unchanged. Consumers use Task 6 loaders/accessors. Change `image_candidate_queryset` and its slicing caller together to ordered decoded candidate selection.

- [ ] **Step 1: Write consumer regressions using mixed codec fixtures.**
`test_raw_serializer_and_cli_shapes_stay_unchanged`: raw JSON string on API/WS/share, parsed JSON on CLI.
`test_metadata_serializer_never_loads_payload`: no dictionary/payload queries.
`test_late_image_after_nonmatching_rows_survives_prefilter_limit`: decoded candidate selection precedes old row limit; same-batch images retain precedence.
`test_attachment_first_five_matching_rows_within_500_lines`: preserve the textual-match slice before structural validation.
`test_stale_history_facts_decode_raw_fallback`: task/spawn evidence remains complete after compute-version bump.
`test_cold_workflow_links_and_synthesis_survive_provider_file_removal`: persisted decoded fallback preserves launch/run links.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert serialize_session_item(loaded_item)["content"] == raw_json_string
assert cli_payload["content"] == orjson.loads(raw_json_string)
assert "content" not in serialize_session_item_metadata(metadata_item)
assert [hit.line_num for hit in late_image_hits] == expected_prefiltered_lines
assert attachment_result == expected_media_and_bytes
assert cold_workflow_links == plain_workflow_links
```

- [ ] **Step 2: Run red consumer tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_consumers.py -v`.

- [ ] **Step 3: Convert accessor calls, projections and internal filters.**
Retain tuple aliases and source-line ceilings. Replace content projection SQL separately from object access. Keep `.defer('content')` on metadata paths while legacy text remains large. Load/decode before leaving async DB boundaries.
For internal filters, preserve SQLite textual prefilter, match order and prefilter limits; parse only after selecting the same candidates. Current facts/links may narrow only where their absence is authoritative; otherwise decoded keyset pages provide complete fallback.
Do not change frontend wire structure or `parsedContent.js` conventions.

- [ ] **Step 4: Run green tests and affected existing regressions.**
Run new tests plus `tests/test_codex_recompute_persistence.py`, `tests/test_nested_agent_compute.py`, `tests/test_cli_session_command.py`, `tests/test_insert_screenshot.py`, `tests/test_claude_task_dedup.py`, `tests/test_codex_subagent_links.py`, `tests/test_hybrid_attachment_image_view.py`, `tests/test_workflow_discovery.py`, `tests/test_workflow_meta_extract.py`, `tests/test_share_cli_reads.py`, `tests/test_share_updates_consumer.py`, `tests/test_share_nested_subagents.py`, `tests/test_wait_reply.py` and `tests/test_wait_background.py`.
Commit: `refactor(storage): use effective content across readers`.

### Task 8: Switch initial and live ingestion to external references

**Files:** Modify `providers/db_writer.py:_apply_create_session_payload`, `_apply_update_session_payload`, `providers/compute_base.py:compute_live`, shared watcher creation/ancestry paths; test `tests/test_content_ingestion.py`, existing live-sync rollback/slice tests.

**Interfaces:** Producer `(line_num, string)` messages remain unchanged. Capture explicit in-memory source/current strings before transforms; persistence uses Task 5. New item legacy `content=''`.

- [ ] **Step 1: Write ingestion tests.**
Initial create/append uses both original=current source refs; transformed live items preserve distinct original/current. Duplicate initial append cannot replace enriched current. Tracking, membership and rows roll back together. Unsaved same-batch lookup sees transformed in-memory current. Appending to an old mixed session cannot mark all storage complete.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
item.refresh_from_db()
assert item.content == ""
assert item.original_content_id == source.id
assert item.current_content_id == transformed.id
assert source.id != transformed.id
assert decode_content(item.original_content) == source_text
assert duplicate_after.current_content_id == enriched_before.current_content_id
assert tracking_after_rollback == tracking_before
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_ingestion.py tests/test_live_sync_rollback.py tests/test_live_sync_slices.py -v`.

- [ ] **Step 3: Implement writer-side content reuse and live source capture.**
Keep producer threads read-only. Initial inserts intern source and assign both refs in the existing transaction. Live capture occurs before generic/provider/enrichment transforms; commit original/current, facts, offsets, cache ownership and readiness invalidation together.
Schedule any required hot promotion after durable invalidation, without changing the existing transformation order or retry caches.

- [ ] **Step 4: Run green tests and commit.**
Commit: `feat(storage): persist ingestion through shared contents`.

### Task 9: Add progressive extraction and direct initial cold compression

**Files:** Modify `providers/compute_base.py` worker/result/chunk/final apply, `providers/db_writer.py` compute branch, `providers/background_compute_task.py`, both provider background compute paths as required; test `tests/test_content_extraction.py`, `tests/test_content_initial_compression.py`.

**Interfaces:**
- `extract_storage_slice(session_id: str, observed_incarnation: str, observed_epoch: int, upper_line: int, cursor: int, row_budget: int, byte_budget: int) -> StorageCursor` in references/state shared storage service.
- Worker result carries observed session incarnation, history epoch and input identity for transformed overrides; no unchanged strings added to IPC.
- Both result-size branches use the same extraction/final-placement ordering.

- [ ] **Step 1: Write extraction and final-date tests.**
Legacy empty/malformed strings survive; unavailable original stays NULL; known original survives later transforms. Interrupt after one chunk, resume, preserve distinct items and refs. Offset match with changed history epoch rejects stale results. Small-result shortcut extracts unchanged rows too.
`test_initial_compute_uses_final_root_dates_before_compression`: root initially looks cold, computed activity makes it hot; payload remains/promotes plain and readiness never falsely publishes.
`test_initial_cold_rows_compress_before_periodic_maintenance`: eligible independent rows compress during first compute; no timer wait.
`test_new_cold_row_is_inserted_compressed_without_plain_intermediate`: observe insertion before commit; new payload has zstd codec on its first persisted representation.
`test_old_compute_rejected_after_same_id_recreation`: recreate equal offsets/epochs and reject the old incarnation.
`test_recent_root_compute_does_not_wait_for_old_descendant_metadata`: preserve newest-first availability and separate tree readiness.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert inserted_codec_trace[new_cold_content.id] == ["zstd"]
assert hot_after_final_dates.codec == "plain"
assert old_incarnation_compute_result.outcome == "superseded"
assert old_history_epoch_result.outcome == "superseded"
assert moved_empty_item.original_content_id is None
assert effective_current(moved_empty_item) == ""
assert "unchanged_contents" not in worker_result
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_extraction.py tests/test_content_initial_compression.py -v`.

- [ ] **Step 3: Implement bounded apply phases with guarded final eligibility.**
Initialize legacy session incarnation through a bounded writer job before worker dispatch. Under the existing result lease/admission fence: validate run/session incarnation/history generation; apply metadata chunks and changed overrides; sweep legacy rows using ascending keyset/row+byte limits; reconcile placement using authoritative pending final lifecycle values; validate final root/state; apply final metadata/links and publish compute/storage versions.
Changed overrides verify the old current ID or legacy digest before updating; originals remain untouched. Compression preparation occurs outside write transactions. Feed prepared new cold bytes to Task 5 insertion within the guarded assignment transaction; reconcile reused rows separately. No worker content-row allocation and no writer replay of provider transforms.
Include pending final root lifecycle values in the compression decision, then verify the applied final values before readiness publication. If uncertain, retain plaintext; optional compression must not block extraction.
Keep apply lease behavior for one result. Maintenance fairness differs; do not rewrite the compute transport.

- [ ] **Step 4: Run green tests and existing apply/retry tests.**
Run: new tests plus `tests/test_background_compute_retries.py`, `tests/test_compute_apply_signals.py`, `tests/test_history_fact_compute_apply.py`, `tests/test_title_compute_apply.py`.
Expected: PASS; no unchanged-source IPC amplification; stale results never advance completion.
Commit: `feat(storage): extract and compress during background compute`.

### Task 10: Cover replacement, deletion, Guardian reconciliation and reset

**Files:** Modify `providers/codex/rollout_migration.py`, `providers/db_writer.py:_apply_delete_sessions_payload`, `core/management/commands/sync.py`; test `tests/test_content_history_lifecycle.py`, `tests/test_codex_rollout_migration.py`.

**Interfaces:** Reuse Task 5 membership removal and Task 4 invalidation; retained content and dictionaries are not deleted.

- [ ] **Step 1: Write destructive-path tests.**
Replacement begin removes old session memberships, preserves other owners, resets storage/readiness and increments history epoch. Replacement chunks assign known originals/current refs and memberships. Interrupt at begin/middle and resume through offset-zero marker.
Guardian deletion covers nested native descendants. Project reset removes ownership and old epochs before same session IDs reappear; recreate a different incarnation and reject old promotion/compute observations. Surviving independent orchestration sessions retain independent state.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
retained_content.refresh_from_db()
assert deleted_session_id not in retained_content.session_ids
assert surviving_session_id in retained_content.session_ids
assert SessionItemContent.objects.filter(pk=retained_content.pk).exists()
assert unfinished_replacement.last_offset == 0
assert recreated.storage_incarnation != old_snapshot.incarnation
assert verify_plaintext_root(old_snapshot) is False
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_history_lifecycle.py tests/test_codex_rollout_migration.py -v`.

- [ ] **Step 3: Integrate streaming old-reference removal with actual destructive boundaries.**
Before entire session/history deletion, stream both old FK IDs and remove session memberships idempotently, with no payload fetch or per-ID reverse scan. Couple deletion and ownership in existing transactions; do not split removal or introduce an undefined pending-state mechanism.
Reset explicitly handles Project cascade; signals do not cover this contract. Retain offset-zero and share-anchor replacement behavior. No guesses reconnect old enrichment.

- [ ] **Step 4: Run green tests and commit.**
Commit: `fix(storage): reconcile ownership across history removal`.

### Task 11: Integrate lifecycle invalidation and admission fencing

**Files:** Modify `agent/base_manager.py`, `core/services/session_update.py`, `session_visibility.py`, `views.py` bulk archive, native creation/discovery paths, provider live/full timestamp writes, `providers/db_writer.py` fence entry points; test `tests/test_content_lifecycle.py`, Task 1 admission tests.

**Interfaces:**
- `storage_admission()` async context manager in writer module, acquired only before writer lease.
- `request_root_promotion(root_id: str) -> None`: post-commit wakeup consumed by Task 12; durable generation is the recovery record.
- Invalidation is synchronous transactional work; wakeups must not be awaited under an incompatible parent lease.

- [ ] **Step 1: Write transition tests.**
Unarchive while hidden and old keeps cold; unhide while archived keeps cold. Clearing both flags requires promotion only when inactivity remains below thirty days. Start/resume/activity invalidates readiness for the full native tree. Late parent/cycle resolution rejects old readiness. Independent orchestration child changes do not invalidate an unrelated native tree.
Registration begun while compression is prepared either blocks publication or causes it to discard the stale result, without deadlock. Failed registration removes provisional active protection safely.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert hidden_after_unarchive.is_cold_for_content is True
assert promotion_requests == []  # old inactive root remains hidden
assert hot_root.plaintext_verified_epoch != hot_root.content_state_epoch
assert late_parent_snapshot.root_id == actual_native_root.id
assert orchestration_root.content_state_epoch == orchestration_epoch_before
assert publication_after_admitted_start is False
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_lifecycle.py tests/test_content_storage_admission.py -v`.

- [ ] **Step 3: Apply Task 1 verified fence ordering and transactional invalidation to every inventory path.**
Registration acquires the admission fence before in-memory installation and durable start writes. Publication acquires the same fence before writer ownership; no provider manager locks are acquired inside it. Preserve ephemeral-agent behavior and start failure cleanup.
Flags, dates, new hot refs and ancestry changes invalidate affected generations in their actual transactions; post-commit notification is best-effort because recovery uses generations. Bulk archive and live timestamp writes must not bypass integration.

- [ ] **Step 4: Run green lifecycle and admission tests and commit.**
Commit: `feat(storage): invalidate readiness on lifecycle changes`.

### Task 12: Implement bounded promotion and combined data maintenance

**Files:** Create `content_maintenance_task.py`; modify filesystem task, `cli/run.py`, writer publication integration, state helpers; test `tests/test_content_maintenance.py`, `tests/test_content_promotion.py`, `tests/test_session_dirs_cleanup.py`.

**Interfaces:**
- `start_content_maintenance_task(stop_event: asyncio.Event) -> None`
- `request_root_promotion(root_id: str) -> None`
- `prepare_representation(content_id: int, policy_version: str) -> PreparedRepresentation` synchronous CPU/read worker, no write lease.
- `publish_representation(candidate: PreparedRepresentation, active_root_ids: frozenset[str]) -> bool` synchronous guarded transaction after admission/writer acquisition.
- `promote_root_slice(snapshot: RootStorageSnapshot, cursor: RootPromotionCursor, row_budget: int, byte_budget: int) -> RootPromotionCursor`

- [ ] **Step 1: Write controlled-clock and interruption tests.**
First periodic pass at 1,800 seconds; later intervals 86,400 seconds. Event hot promotion starts without either wait. Filesystem-disable flag still permits promotion/recovery. Tree promotion covers both refs across depths and remains readable mid-pass. Epoch changes prevent false completion. Restart rediscovers pending roots from metadata only.
`test_promotion_cursor_between_descendants_with_repeated_lines`: stop after one descendant; resume the next without skipping its same-numbered lines.
`test_promotion_cursor_between_current_and_original`: stop after current slot on one item; resume original slot and complete both.
No-gain same policy skips, changed policy retries, successful promoted content recompresses. Empty ownership does not classify orphans cold. Cold shared row with one hot owner cannot publish zstd. Stop/cancel leaves valid bytes/state and drains admitted applies.
`test_interactive_write_runs_between_maintenance_slices`: barriers prove a full pass does not retain one writer lease.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert periodic_delays == [1800, 86400]
assert cursor_after_current.fk_slot == 0
assert cursor_after_original.fk_slot == 1
assert resumed_descendant_ids == expected_native_ids_in_order
assert verify_plaintext_root(old_epoch_snapshot) is False
assert interactive_apply_index < final_maintenance_slice_index
assert attempted_same_no_gain_policy.call_count == 0
```

- [ ] **Step 2: Run red maintenance tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_maintenance.py tests/test_content_promotion.py tests/test_session_dirs_cleanup.py -v`.

- [ ] **Step 3: Implement coordinator and bounded publication.**
Use immediate promotion wakeups plus recoverable root generations/incarnations and the exact RootPromotionCursor ordering above; periodic content-PK/keyset sweeps handle elapsed-time cold eligibility. Initial provisional budgets: 500 rows and 8 MiB encoded input per slice, one oversized row allowed. Validate and tune these in Task 15; these are plan choices, not spec requirements.
Coalesce roots and prioritize promotion before the next optional compression slice. Prepare codec CPU on a bounded worker separate from the single writer executor; one candidate batch in flight, no unbounded thread pool queue.
Recheck exact membership, root state, active snapshot, codec/dictionary identity and hash/length at publication. Replace payload/codec/dictionary/attempt metadata atomically. No cold-read DB cache/timestamps.
Use a content row's first provider affinity for policy selection. Retain old dictionary bytes.

- [ ] **Step 4: Integrate the existing directory cleanup without broadening its guard.**
Combined timer owns scheduling. Its filesystem branch retains its existing all-manager guard and disable setting. Scratch user policy calls its independent thirty-day native-root predicate; empty directories, orphan scratch and draft attachments retain distinct rules. Draft cleanup stays separately scheduled.
Shutdown stops maintenance before compute executor/writer teardown and waits for admitted work using existing cancellation-safe semantics.

- [ ] **Step 5: Run green maintenance/cleanup tests and commit.**
Commit: `feat(storage): maintain cold data and recover hot promotion`.

### Task 13: Gate public substring search with consistent hot readiness

**Files:** Modify `cli/session.py`, CLI option/help schemas, plugin session content/messages docs and MCP discovery generated surfaces where applicable; test `tests/test_content_contains_gate.py`, existing CLI listing/session/MCP tests.

**Interfaces:**
- `require_plaintext_search(session_id: str) -> RootStorageSnapshot` in state module; raises explicit cold, warming, or unresolved-ancestry errors.
- Execute guard, matching and pagination within a consistent reader transaction/snapshot. Revalidate live admission state through the backend service boundary where required; standalone CLI reads cannot invent an active-state proof from an old Session object.

- [ ] **Step 1: Write gate and consistency tests.**
Both commands reject cold roots even when requested lines are plain. Warming/unresolved roots reject explicitly. No-contains range/message reads continue decoding. Ready hot content uses exact CAST/legacy matching with AND terms; messages retains its Python extracted-text semantics. Count/tail/paging remain unchanged.
`test_contains_snapshot_cannot_mix_ready_epoch_and_new_hot_reference`: query either uses one verified snapshot or returns warming; it cannot silently omit new matching rows.
Global Tantivy search still returns cold matches; reading hit lines decodes only those rows.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert cold_contains_error.reason == "cold"
assert warming_contains_error.reason == "warming"
assert unresolved_contains_error.reason == "unresolved_ancestry"
assert contains_result["total"] == expected_complete_match_count
assert contains_result["items"] == expected_match_window
assert concurrent_query_epoch == verified_snapshot.epoch
assert tantivy_cold_hits == hits_before_compression
```

- [ ] **Step 2: Run red tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_contains_gate.py tests/test_cli_session_command.py -v`.

- [ ] **Step 3: Implement gate and update public contracts together.**
Use current root resolution/generation from DB; keep codec checks out of requested-line-range eligibility. Hold one SQLite read snapshot for DB root-state validation and count/selection, so concurrent commits cannot mix generations. Preserve existing CLI/MCP error transport and expose distinct cold/warming/unresolved reasons.
Document Tantivy and range-read alternatives without adding a public decode-and-scan flag. Update descriptions that still claim physical SessionItem.content storage.

- [ ] **Step 4: Run green CLI/MCP tests, including `tests/test_cli_listing_envelope.py` and `tests/test_mcp_tools.py`, and commit.**
Commit: `feat(storage): restrict substring search to ready hot roots`.

### Task 14: Activate compute/storage versions and complete packaging

**Files:** Modify `settings.py`, version-specific fixtures, `pyproject.toml` packaging only if required; dictionary generator query adaptation; test `tests/test_content_upgrade.py`, asset wheel tests.

**Interfaces:** First extracted storage version is 1. Activation compute targets are current Claude/Codex values plus one at execution (115/56 only if no intervening version change).

- [ ] **Step 1: Write activation/direct-upgrade tests.**
Old inline DB goes directly to combined release; partial conversion restarts; new storage-ready sessions still respect independent compute readiness. Dictionary assets exist in built wheel and fresh DB seed. Old dictionaries remain usable when a new version ships. Generator sampling after extraction consumes decoded effective current rows, not stale legacy text or unrelated originals.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert Session.objects.get(pk=upgraded_id).content_storage_version == 1
assert Session.objects.get(pk=interrupted_id).compute_version != current_compute_version
assert bundled_dictionary_paths <= wheel_member_paths
assert decode_content(old_dictionary_content) == exact_original_string
assert generator_current_samples == expected_effective_current_samples
```

- [ ] **Step 2: Run red activation tests.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_content_upgrade.py tests/test_content_dictionary_assets.py -v`.

- [ ] **Step 3: Activate versions only after Tasks 1–13 pass.**
Bump both provider targets once. Keep historical migrations unchanged. Preserve recent-first scheduling, mixed readers and storage marker verification. Package raw dictionary files/manifest through the existing wheel package layout; do not change Codex vendoring/runtime.
Adapt generation's sampling loader for post-extraction runs while keeping sanitization/project-holdout evaluation unchanged.

- [ ] **Step 4: Run green upgrade/package tests and commit.**
Build wheel through existing project build flow or `uv build`; inspect contents and seed from installed package resources in an isolated test environment. Never install this build into another checkout's active environment.
Commit: `feat(storage): activate progressive content storage upgrade`.

### Task 15: Run isolated migration/performance and product acceptance gates

**Files:** Extend relevant storage tests and audit validation record. Keep performance probes temporary unless they become maintained diagnostics. No application startup against real data.

**Interfaces:** Consumes all tasks; produces measured acceptance evidence and adjusted bounded budgets, with no new product policy.

- [ ] **Step 1: Create a representative isolated DB with the proven backup method.**
Use devctl's `source.backup(target, pages=-1)` algorithm without starting devctl. Read source path explicitly; target an isolated temporary directory. Print and assert Django's resolved DB path is inside the target before any migration/apply. Never run bare Django using the main production data directory.

Assertion examples for the named scenarios above (fixture/setup variables come from that scenario):

```python
assert resolved_probe_db.is_relative_to(isolated_probe_root)
assert "core_sessionitem" not in rebuilt_tables
assert new_item_fk_indexes == []
assert "core_sessionitem" not in globally_checked_legacy_tables
assert full_pytest_exit_code == 0
assert observed_unchanged_ipc_payload_count == 0
assert interactive_writer_passed_between_slices is True
```

- [ ] **Step 2: Measure schema migration duration and SQL effects.**
Apply only to the isolated copy. Record duration, rebuilt tables, indexes and FK-check scope, including dictionary seeds and nullable root fields. Reject an item-table rebuild, child index creation or global existing-item FK scan. No existing-content conversion or vacuum in the migration.

- [ ] **Step 3: Exercise representative conversion/promotion/ownership workloads.**
Include recent-first availability, a huge session, nested descendants, fork-sharing, large original/current overlap, interrupted extraction and replacement, compressed hot reuse, no-gain policy upgrade, and multi-megabyte reads. Record per-slice writer/executor waits, root-ready latency, memory, content/list bytes and WAL growth.
Tune the provisional 500-row/8-MiB slice limits from measurements. Verify interactive jobs can proceed between maintenance slices and no worker accumulates all unchanged contents in IPC.
Do not claim file shrink: freed SQLite pages remain reusable; physical compaction is out of scope.

- [ ] **Step 4: Run the complete relevant test gates.**
Run: `cd /home/twidi/dev/twicc-poc && uv run pytest`.
Run: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/core/content_storage src/twicc/content_maintenance_task.py` plus all modified Python files.
Run the existing real-binary Codex rollout tests with `TWICC_CODEX_INTEGRATION=1` only when the bundled runtime already exists; use their throwaway CODEX_HOME. Record a skip reason rather than downloading/updating a runtime silently.
Frontend tests are required only if frontend code changes; wire-shape server tests remain mandatory.

- [ ] **Step 5: Repeat the audit searches and check scope.**
Every production `.content`, content projection/filter, raw UPDATE, direct lifecycle/parent mutation, insertion/deletion and serialization occurrence has a documented effective-storage path or explicit legacy-only exemption. Historical migrations and frontend wire-field names are valid exemptions; unconverted live paths are not.
No new changelog, content GC, column removal, vacuum, read-time persistent cache or fork-resolution change.

- [ ] **Step 6: Record evidence and commit final corrections.**
Commit: `test(storage): validate progressive compression upgrade`.
Delete task-owned temporary copies/candidates. Do not delete unrelated files or request a production restart.
At handoff, remind the user that their own instance needs migration via normal startup and restart via devctl; do not perform those operations automatically.

## Coverage and dependency map

| Spec sections | Tasks |
|---|---|
| 1–4 scope/schema/provenance/migration | 2, 3, 5, 14, 15 |
| 5 new ingestion | 5, 8 |
| 6–7 extraction/completion/concurrency | 1, 4, 9, 11, 15 |
| 8 replacement | 10 |
| 9–11 loaders/contracts/consumer inventory | 6, 7, 13, 15 |
| 12 retention/physical footprint | 10, 15 |
| 13–14 acceptance/implementation validation | All tasks, especially 15 |
| 15 root cold policy | 4, 11, 12 |
| 16 codecs/dictionaries/no gain | 2, 3, 12, 14 |
| 17 ownership | 1, 5, 8, 9, 10 |
| 18 initial conversion/maintenance | 1, 9, 11, 12 |
| 19 reads/warming/search | 4, 6, 7, 12, 13 |
| 20–22 extended acceptance/evidence/review | 15 and plan review below |

Dependency order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15.
Tasks may be reorganized only if their interfaces and readiness/writer activation dependencies remain satisfied.
This is one integrated release; intermediate commits are independently testable units, not independent production releases.

## Plan self-review

- [x] All 22 specification sections map to implementation or acceptance tasks.
- [x] Shared interface names/types remain consistent across producers and consumers.
- [x] Every task supplies test names/assertions, red/green command and a distinct commit boundary.
- [x] The five Review Focus cases have explicit owning tests.
- [x] Design validation precedes uncertain concurrency/performance implementation.
- [x] No product policy is reopened; no migration data conversion or unintended startup work is introduced.

## Adversarial plan review

Two independent internal reviewers examine implementability, dependencies, interfaces, coverage and evidence gates.
Implementation sequencing and technical decisions are in scope; full function bodies and unrelated redesign are not.

Round 1 requires incarnation fencing, a distinct tree-promotion cursor, direct compressed insertion, an explicit transactional deletion choice, and concrete test assertions.
All findings are incorporated in this document.
Both reviewers recheck the corrected plan and independently report no remaining material plan-level blocker.

The review outcome and resolved findings are retained here; the separate review report is removed.
The planned tests and design/performance gates still must run during implementation.
This document and its review do not claim those gates already pass.
