# Codex Compressed Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read compressed Codex rollouts and preserve existing sessions across compression and restoration.

**Architecture:** Adapt compressed sources to validated temporary JSONL files. Reuse existing ingestion, compute, migration, and replacement code. Restrict new coordination to compressed reads, representation transitions, and source-association repair.

**Tech Stack:** Python >= 3.13, Django 6, existing `zstandard~=0.25.0`, `orjson`, asyncio, watchfiles, pytest.

**Spec:** [Codex compressed sessions specification](2026-11-08-codex-compressed-sessions-spec.md).

**Status:** Revised after the user's performance review; both focused adversarial reviewers return READY. Implementation is not authorized.

## Global Constraints

- `last_offset` always counts bytes in the decompressed JSONL content.
- `Session.file_path` names the selected physical source, relative to the configured provider root.
- Plain JSONL takes precedence when both representations exist.
- A pure representation change preserves checkpoints, history, compute/search validity, storage generations, and user state.
- Compression must not make a session disappear or become stale.
- Validate compressed input before publishing new items or advancing checkpoints.
- Accept concatenated frames and frames without a declared content size.
- Never permanently decompress or modify Codex source files for read-only access.
- Keep ordinary JSONL ingestion incremental. Keep Claude on its current path.
- No new dependency, schema migration, compute-version bump, SDK update, or CHANGELOG edit.
- No archives discovery, arbitrary rollout relocation, or new duplicate-rollout policy.
- Paginated revert retains existing generic handling and its current limitations. No special revert support is part of this feature.
- Read provider roots at call time. Use `NamedTuple` for immutable records and `orjson` for JSON.
- Preserve unrelated files. No branch, worktree, commit, package installation, migration, or server restart without applicable authorization.
- Write source and documentation in English. Use existing i18n conventions for any new interface text.

## Review Focus

1. A truncated frame can return complete records without raising: publish nothing from that unvalidated input. Task 1.
2. An old compressed payload arrives after restoration and append: reject obsolete source/checkpoint evidence. Task 2.
3. A deleted sibling event arrives first: resolve the other representation before invalidating history or publishing stale. Task 3.
4. Codex registration restores JSONL before migration retry: resolve the physical path again. Task 4.
5. A large compressed preparation runs beside normal JSONL ingestion: the normal session keeps its existing scheduling and reads. Tasks 3 and 5.

## Scope correction and verified rewrite cases

The previous plan introduced a general history-verification and ingestion redesign.
Those changes are removed. Compression preserves JSONL bytes; it does not justify comparing all persisted records again.

Checked Codex commit: `748ab55ca7de0b647d9a7a9d9a8e70e00f28666d`.

| Operation | Behavior | Local Codex source |
|---|---|---|
| Normal recording | Opens JSONL with `append(true)`. | `codex-rs/rollout/src/recorder.rs:1752` |
| Legacy rollout migration | Atomically replaces the existing JSONL or ZST. TwiCC already handles this rebuild. | `codex-rs/thread-store/src/local/rollout_migration.rs:856` |
| Legacy rollback markers | Affect reconstructed history; do not truncate source bytes. | `codex-rs/core/src/thread_rollout_truncation.rs:40` |
| Paginated revert | Creates a new rollout. Keeps the old file intact. Changes the SQLite path pointer. | `codex-rs/thread-store/src/local/revert_thread.rs:15` |
| Compression/restoration | Changes representation while preserving JSONL bytes. Resume then appends to JSONL. | `codex-rs/rollout/src/compression.rs`, `recorder.rs` |

No additional production same-logical-path rewrite is identified in the inspected rollout/thread-store code.
This is a bounded source inspection, not a claim about every future Codex version.
Preserve existing migration, truncation, and bounded replacement detection. Do not build another replacement system.

Paginated revert is explicitly outside this plan, as requested by the user.
Do not add revert detection, current-rollout lookup in Codex SQLite, or cross-rollout history reconciliation.
Do not add a special rejection or exclusion rule for reverts either. Existing generic handling remains in place.
Only the exact `.jsonl` / `.jsonl.zst` sibling transition receives the new representation handling.

### Removed work

| Removed component | Reason |
|---|---|
| Full source-prefix comparison against DB originals | Adds history-sized reads to a representation transition. Existing detection does not require this. |
| New original-content iterator in content storage | Exists only for that comparison. |
| New startup batching and per-batch ACK protocol | Refactors ingestion independently of compressed-file support. |
| Item spools and new spool replacement jobs | Existing prepared-history lists and replacement jobs already work with decoded JSONL. |
| General prepared-slice receipts in shared compute | A validated temporary JSONL can use the existing bounded live reader. |
| Global source cache, cache leases, and cross-consumer admission scheduler | Operation ownership and watcher drain ownership suffice to reuse decoded files. |
| Per-waiter asynchronous backlog probe | Polling must stay cheap. Active Codex sessions restore JSONL. |

## Performance contract

“No impact” here means no new history-sized work or blocking dependency on an existing ordinary path.
A provider dispatch can add a constant-time suffix or state check. Do not claim literally zero extra instructions.

| Path | Required behavior and additional cost |
|---|---|
| Reading history already in DB | No source access or decompression. |
| Existing unchanged JSONL at startup | Keep existing matching and mtime/size skip. No new header read or SQL query. |
| Normal JSONL append | Existing bounded source observation, live slice, writer transaction, and compute. No sibling stat, temporary copy, new SQL query, or compressed-worker wait per append. |
| Claude ingestion | Existing behavior. Shared optional arguments retain direct defaults. No Codex import on this path. |
| JSONL response polling | Existing `getsize()` comparison. No decompression, preparation, or new wait. |
| Initial inventory | One traversal accepting both extensions. Select siblings in memory. No second traversal or sibling stat for every ordinary JSONL. |
| Representation transition | Resolve siblings, validate identity, compare bounded existing source samples when available, and persist the selected path. |
| Compressed content read | Decode once to a temporary JSONL, validate, then reuse it for the operation or watcher drain. |
| Compressed metadata-only read | Read only the first decoded record. It cannot certify full ingestion. |
| Real deletion | Check both representations before stale publication. |
| Migration/rebuild | Existing scans and item materialization, with compressed source adaptation where required. |

Decoded JSONL creation adds sequential source reads and temporary disk writes for compressed content.
Decoder memory stays bounded. Existing startup/rebuild item lists retain their present memory behavior.
This feature does not claim to solve that pre-existing list materialization cost.

Do not introduce a persistent decoded cache or decode on each live slice.
A watcher drain pins one temporary file until its backlog is drained or invalidated.
Startup and migration each reuse one prepared source within their existing operation.
Metadata-only readers do not leave decoded copies behind.

## File map

| File | Change |
|---|---|
| New `src/twicc/providers/codex/rollout_sources.py` | Sibling paths, bounded metadata reads, validated temporary JSONL, source fingerprints, backlog hints. No ORM. |
| New `src/twicc/providers/codex/source_binding.py` | Small Codex-only association/ingestion envelope and serialized association helper. No content-history comparisons. |
| `src/twicc/providers/codex/initial_sync.py` | Two-extension inventory, logical matching, selected-source reads and repair. |
| `src/twicc/providers/db_writer.py` | Route the Codex-only envelope. Keep existing Create/Update payloads unchanged. |
| `src/twicc/providers/sessions_watcher.py` | Narrow optional read-path forwarding and file enumeration hook. Preserve defaults. |
| `src/twicc/providers/codex/sessions_watcher.py` | Logical queue keys, transition handling, asynchronous compressed preparation, bounded observations. |
| `src/twicc/providers/codex/rollout_migration.py` | Read prepared JSONL and accept equivalent sibling report paths. |
| `src/twicc/providers/codex/background_compute.py` | Resolve compressed candidates and use logical sizes. Re-resolve after Codex operations. |
| `src/twicc/providers/codex/helpers.py` | Route the Codex association job through `CodexHelpers.try_handle_async_job`. |
| `src/twicc/providers/codex/orchestrator.py` | Drain new watcher preparation tasks during existing teardown. |
| `src/twicc/cli/_wait_reply.py` | Cheap compressed-source backlog handling. |

Do not modify `core/content_storage/readers.py`, `compute_base.py`, `live_sync.py`, or startup queue semantics for this feature.
The existing `read_live_slice()` already accepts a regular seekable JSONL path.

## Shared decisions and interfaces

### Source adaptation

`rollout_sources.py` provides:

```python
class SourceFingerprint(NamedTuple):
    physical_path: Path
    device: int
    inode: int
    size: int                 # physical size; never compare with last_offset
    mtime_ns: int
    ctime_ns: int

class PreparedRollout:
    physical_path: Path
    read_path: Path            # original JSONL, or private validated temporary JSONL
    logical_size: int
    mtime: float
    fingerprint: SourceFingerprint
    def close(self) -> None: ...
    def __enter__(self) -> PreparedRollout: ...
    def __exit__(self, *args) -> None: ...

def logical_rollout_path(path: Path) -> Path: ...
def is_rollout_path(path: Path) -> bool: ...
def resolve_rollout(path: Path) -> Path: ...
def prepare_rollout(path: Path, *, stop: threading.Event | None = None) -> PreparedRollout: ...
def read_rollout_meta(path: Path) -> dict | None: ...
def source_is_current(fingerprint: SourceFingerprint) -> bool: ...
def compressed_backlog_hint(path: Path, offset: int) -> bool | None: ...
```

Ordinary JSONL callers continue using their current readers directly.
These helpers run only for compressed sources, missing paths, or an identified representation transition.
`logical_rollout_path()` removes only the final `.zst`; it leaves an ordinary JSONL path unchanged.

Resolution checks plain before compressed. If both lookups miss, recheck plain once to cover restoration between lookups.
Use no sleeps in resolution or publication. Non-absence errors remain errors, not proof of deletion.
Use `fstat()` on opened sources. Keep stored paths relative to the configured provider root; do not substitute resolved symlink targets.

Compressed preparation writes decoded bytes to a private temporary regular file.
Set its mtime to the source mtime so unchanged shared readers and compute use the correct value.
The temporary name never enters `Session.file_path`, migration CLI arguments, queue keys, or migration reports.
A plain prepared source owns no copy. It is an adapter for special operations, not the normal JSONL hot path.

Validate every compressed frame before returning the temporary file.
Check `decompressobj().eof`, process `unused_data` for concatenated frames, and reject incomplete frames and trailing garbage.
Use `ZstdDecompressor(max_window_size=64 * 1024 * 1024)` with the installed 0.25.0 backend.
The local backend expects this value in bytes. A real frame with a window above 64 KiB must pass.
Feed bounded compressed chunks and write each output immediately. Start with 256-byte input chunks to bound per-call expansion.
Benchmark that decoder choice before accepting it; larger input chunks require a demonstrated bounded-output alternative.
Check cancellation between chunks. Normalize `ZstdError` to an `OSError` subclass for existing failure isolation.
Reject a source whose fingerprint changes during decoding. Delete temporary files on failure, cancellation, and close.

`read_rollout_meta()` reads only the first decoded record and preserves existing identity/classification rules.
It does not assert full-frame integrity. Data ingestion always uses `prepare_rollout()` first.

### Source association and startup publication

Existing Create/Update payload classes and FIFO startup behavior remain unchanged.
For compressed sources and association repair only, use a Codex-specific envelope:

```python
class SourceCheckpoint(NamedTuple):
    last_offset: int
    last_line: int
    history_epoch: int
    storage_incarnation: str | None

class CodexSourcePayload(NamedTuple):
    provider: Provider
    session_id: str
    physical_path: str
    fingerprint: SourceFingerprint
    expected: SourceCheckpoint | None
    ingestion: CreateSessionPayload | UpdateSessionPayload | None

class ReconcileCodexSourceJob(NamedTuple):
    provider: Provider
    payload: CodexSourcePayload
    future: asyncio.Future

class MarkCodexSourcesMissingPayload(NamedTuple):
    provider: Provider
    sources: list[tuple[str, str]]  # session ID and expected stored physical path

def apply_codex_source_payload(payload: CodexSourcePayload) -> bool: ...
```

`None` ingestion permits path and availability repair without new records.
`None` expected denotes an absent row; it must not authorize overwriting an existing conflicting session.
The writer checks current selection, logical path, provider, identity evidence, and the captured checkpoint before mutations.
Reject stale evidence rather than rolling a checkpoint or association backward.
Only an exact sibling of the recorded logical path qualifies as representation repair.
Source metadata must declare the same session ID. Do not compare persisted item contents.

Apply the existing ingestion handler and association update under existing serialized writer ownership.
Retain existing off-lease content preparation and post-commit project/session side effects when routing an enveloped Create/Update.
Persist `file_path` explicitly; mutating the producer's model instance alone does not persist it.
Clear applicable stale state and an obsolete `rollout_missing` diagnosis; retain unrelated diagnoses.
Retain normal aggregate updates, compute wakeups, and broadcasts when availability changes.
No decompression, full read, or new SQL scan occurs under the writer lease.

If the envelope becomes obsolete, use existing incomplete-inventory/error accounting and arrange normal selected-source catch-up.
There is no per-batch acknowledgment and no wait for a producer-side mutated model instance.
Ordinary JSONL Create/Update payloads do not acquire new guards or additional queries.

### Watcher adaptation

Use the logical JSONL path for Codex queue keys and migration release keys.
Keep the selected physical path in parsed session metadata.
Normal JSONL added/modified events use the existing callback path unless a transition is already pending or the file disappears.
Deleted events and ZST events resolve siblings before using existing deletion handling.
Do not let base `_enqueue()` discard source evidence for a deleted sibling whose representation still exists.

Forward an optional `read_path: Path | None = None` through the base watcher methods:
`_process_parsed_session_change()`, `sync_and_broadcast()`, and `_sync_live_session_items()`.
It affects filesystem content/size reads and the path passed to existing `compute.sync_session_slice()` only.
Queue state, adaptive line limits, parsed physical associations, and drain generations still use the logical path.
The default executes current code. No new compute data type or compute transaction protocol is needed.
Add a provider enumeration hook whose base implementation retains `rglob('*.jsonl')`.
Codex enumerates both extensions in one traversal for late-created provider roots.

Compressed preparation runs outside the callback lock, event loop, and DB writer lease.
Use a Codex watcher-owned executor with two workers and at most two active prepared drains.
Pending compressed paths hold no decoded file. Ordinary JSONL never waits for this admission.
Reuse the same prepared file across bounded slices. Release it on drain, replacement, failure, or shutdown.
Startup remains sequential per source. Background compute retains its existing bounded scheduling.
No global resource scheduler or idle decoded cache is required.

Capture lifecycle, source generation, event token, and migration release token before watcher preparation.
A completion can resume only its still-current deferred attempt. Do not enqueue a synthetic new event.
Use existing `resume_pending_change()` and explicitly `_ensure_consumer()` after a valid completion.
A preparation failure stores an error for that attempt and resumes it through the same guarded path.
The resumed callback reports the existing `failed` disposition, which settles admitted waiters without a new queue API.
A superseded or failed migration release must not become ready because old preparation finishes.
Await actual preparation termination during shutdown before deleting its temporary file.

Observe decoded bytes with the existing bounded sampling policy: 4096-byte prefix/suffix and 65536-byte tail blocks.
Snapshot identity comes from the physical source fingerprint, never from a newly created temporary file's inode.
For an exact sibling representation transition, suppress only the inode-change reason when size and samples remain compatible.
Compare the previous suffix at its original logical offset, so restored JSONL can already include new records.
Preserve inode replacement detection for the same representation, shrinkage detection, and legacy/paginated mode checks.
With no earlier sample, use existing checkpoint-size and history-mode checks; do not reconstruct a DB prefix.
This retains the existing inability to detect arbitrary interior edits outside sampled evidence.

## Task 1: Add the compressed-source adapter

**Files:** Create `src/twicc/providers/codex/rollout_sources.py` and `tests/test_codex_rollout_sources.py`.

**Interfaces:** Produce `PreparedRollout`, source fingerprints, sibling helpers, metadata reader, and backlog hint defined above.
No ORM, cache manager, SDK, or ingestion code belongs here.

- [ ] **Step 1: Add failing tests with real `zstandard` frames.**

`test_prepared_rollout_preserves_bytes_size_and_mtime` asserts exact bytes, logical size, source mtime, and source-file preservation.
`test_truncated_frame_never_returns_prepared_rollout` includes a frame that returns complete JSONL records before physical EOF.
Parameterize unknown size, concatenated frames, checksum errors, trailing garbage, empty input, and invalid dictionaries.
`test_codex_level3_window_above_64kib` verifies backend window units with an actual frame.
Test plain precedence, restoration between missing lookups, unreadable plain source, fingerprint changes, cancellation, and cleanup.
Test metadata reads do not decode the whole file or certify its integrity.

- [ ] **Step 2: Run `uv run pytest tests/test_codex_rollout_sources.py -q`.** Confirm failures identify absent interfaces.
- [ ] **Step 3: Implement the adapter and strict completion checks.** Keep decoder memory independent of total decoded size.
- [ ] **Step 4: Run the same tests.** Expect PASS, unchanged source files, and no leaked temporary files.
- [ ] **Step 5: Review this task.** Commit only if requested. Suggested subject: `feat(codex): read compressed rollout sources`.

## Task 2: Reconcile startup identity and source association

**Files:** Create `src/twicc/providers/codex/source_binding.py` and `tests/test_codex_compressed_initial_sync.py`.
Modify `src/twicc/providers/codex/initial_sync.py`, `src/twicc/providers/db_writer.py`, and `src/twicc/providers/codex/helpers.py`.

**Interfaces:** Consume Task 1. Produce `CodexSourcePayload`, `ReconcileCodexSourceJob`, `MarkCodexSourcesMissingPayload`, and `apply_codex_source_payload()` above.
Extend `extract_session_meta(path: Path, *, read_path: Path | None = None)` without changing its default JSONL behavior.

- [ ] **Step 1: Add failing startup and writer tests.**

`test_compression_while_stopped_repairs_existing_session` checks the same session, new physical path, unchanged item IDs/checkpoints/epochs/user state.
`test_unchanged_source_repairs_stale_without_new_items` checks durable repair even when mtime and logical offset match.
`test_partial_compressed_import_appends_only_missing_records` checks existing line numbers and no duplicate prefix.
`test_obsolete_compressed_payload_cannot_reverse_restoration` advances the DB checkpoint before writer application and checks rejection.
Test new compressed sessions/subagents, plain precedence, ignored guardians, source-ID mismatch, unrelated duplicate paths, and related-only diagnosis repair.
`test_unchanged_jsonl_startup_keeps_existing_io_and_query_counts` compares the direct ordinary path before and after adaptation.

- [ ] **Step 2: Run `uv run pytest tests/test_codex_compressed_initial_sync.py -q`.** Confirm failure before implementation.
- [ ] **Step 3: Extend the existing inventory in one traversal.** Match exact physical paths first; use a logical-path map for sibling transitions.

Keep existing plain entries on current matching and read paths. Read metadata again only for new sources or changed associations.
Select plain in memory when inventory contains both siblings. Resolve races only for the affected source.
For compressed content reads, pass the prepared temporary JSONL to existing list-based import helpers.
Preserve source mtime and startup handling of nonblank unterminated final records.
Keep the prepared file until that source's parsing finishes; the queued payload owns its existing item list, not the temporary path.

- [ ] **Step 4: Add the Codex envelope route and association update.** Use existing Create/Update ingestion; no new batching or ACKs.

Compute stale candidates by logical source identity, not absent physical extension.
Use `MarkCodexSourcesMissingPayload` for these Codex candidates; keep the shared stale payload unchanged.
Before applying it, check the expected association and both representations under writer ownership.
A present or unreadable sibling blocks a missing-source classification. Never sleep or decode under writer ownership.
Reuse existing stale/aggregate publication with only confirmed absent IDs; no filesystem checks on ordinary append payloads.

- [ ] **Step 5: Run the new startup tests and existing Codex migration job tests.**

Run: `uv run pytest tests/test_codex_compressed_initial_sync.py tests/test_codex_migration_jobs.py -q`.
Expect PASS, including explicit physical-path persistence and no false stale update.

- [ ] **Step 6: Review this task.** Commit only if requested. Suggested subject: `fix(codex): preserve sessions across rollout compression`.

## Task 3: Adapt the Codex watcher without changing ordinary ingestion

**Files:** Modify `src/twicc/providers/codex/sessions_watcher.py`, `src/twicc/providers/sessions_watcher.py`, and `src/twicc/providers/codex/orchestrator.py`.
Create `tests/test_codex_compressed_watcher.py`; extend existing watcher regression tests where appropriate.

**Interfaces:** Consume Tasks 1–2. Add the optional base-watcher `read_path` forwarding and enumeration hook above.
Extend Codex `_rewrite_detected(parsed, path, *, read_path: Path | None = None)` with logical state keys and selected read-path size.
Keep `BaseSessionCompute.sync_session_slice()`, `RawLiveSlice`, `LiveSyncLimits`, and `SessionChangeQueue` unchanged.

- [ ] **Step 1: Add failing transition, concurrency, and fast-path tests.**

`test_deleted_plain_with_compressed_sibling_never_broadcasts_stale` checks every emitted update, not just final state.
`test_restoration_and_immediate_append_keep_history_and_generation` checks logical targets, preserved item IDs, and exactly-once suffix import.
`test_same_representation_replacement_still_requests_rebuild` preserves existing inode/truncation/mode behavior.
`test_compressed_drain_decodes_once_across_slices` asserts one decode and existing per-slice budgets.
`test_distinct_temp_files_for_unchanged_zst_preserve_generation` performs two separate drains and asserts no rebuild despite different temporary inodes.
`test_plain_append_bypasses_compressed_preparation_and_sibling_stats` asserts no new source reads, queries, or admission waits.
Test reversed/duplicate events, both siblings, genuine deletion, late root creation, and finite `process_path()` targets.
Test stale preparation after failed migration release, newer event, shutdown, and lifecycle restart.
`test_preparation_completion_restarts_idle_consumer` covers a queue containing only deferred paths.
`test_preparation_failure_settles_admitted_waiter` checks the resumed failed disposition and absence of a hanging drain.
`test_compressed_replacement_reaches_existing_rebuild_handler` checks logical state and decoded-size forwarding into `_rewrite_detected()`.

- [ ] **Step 2: Run `uv run pytest tests/test_codex_compressed_watcher.py -q`.** Confirm failures identify the missing transition behavior.
- [ ] **Step 3: Implement logical admission and sibling-aware deletion.** Do not weaken same-representation replacement detection.
- [ ] **Step 4: Add optional read-path forwarding and asynchronous preparation.** Preserve existing normal JSONL and Claude defaults.

Recheck selected-source freshness outside publication; publish compressed association through the short Codex job under normal gate ownership.
Pass the selected read path into Codex `_rewrite_detected()` before base truncation checks; retain logical keys for `_replaced_paths`.
For a compressed selection, parse metadata through its prepared read path while retaining its physical path in `ParsedSessionFile`.
Run association repair only when needed; ordinary JSONL callbacks do not submit another writer job.
If selected representation changes while a snapshot is prepared, discard that attempt and process the current source.
Keep normal source-generation and migration-gate checks effective through bounded publication.
A failed compressed read preserves stored history and does not describe an existing file as deleted.
The read path remains pinned through actual compute completion; never remove it while the worker still reads it.

- [ ] **Step 5: Run watcher and shared-ingestion regressions.**

Run: `uv run pytest tests/test_codex_compressed_watcher.py tests/test_watcher_source_identity.py tests/test_watcher_catch_up.py tests/test_watcher_slice_fairness.py tests/test_session_change_queue.py tests/test_codex_migration_gate.py tests/test_live_sync_slices.py -q`.
Expect PASS. Validate both providers and actual consumer restart after deferred preparation.

- [ ] **Step 6: Review this task.** Commit only if requested. Suggested subject: `fix(codex): follow rollout representation transitions`.

## Task 4: Adapt existing migration and rebuild readers

**Files:** Modify `src/twicc/providers/codex/rollout_migration.py` and `src/twicc/providers/codex/background_compute.py`.
Create `tests/test_codex_compressed_migration.py`; extend existing migration tests.

**Interfaces:** Consume Tasks 1–3. Extend existing preflight/history functions with optional `read_path: Path | None = None`.
Keep `PreparedCodexHistory.items` and `ReplaceCodexHistoryJob` list-based. Keep existing chunked writer jobs and recovery markers.

- [ ] **Step 1: Add failing compressed migration/recovery tests.**

`test_compressed_legacy_rollout_uses_existing_migration_and_rebuild` checks existing gates and replacement behavior.
`test_registration_resume_restores_before_migration_retry` requires new physical resolution before the second CLI call.
`test_sibling_migration_report_is_accepted` accepts only the same logical source and correct session ID.
Test compressed rewritten output, already-paginated input, interrupted offset-zero rebuild, missing-source recovery, and unrelated diagnoses.
`test_plain_migration_retains_existing_reader_and_job_protocol` rejects accidental spool or replacement-job changes.

- [ ] **Step 2: Run `uv run pytest tests/test_codex_compressed_migration.py -q`.** Confirm source/report failures before implementation.
- [ ] **Step 3: Adapt source classification, size checks, preflight, first-record reads, and history preparation.**

Use prepared logical size wherever offsets are compared. Never pass temporary paths to Codex.
Reuse the prepared file during one preparation operation; complete validation before invoking destructive history replacement.
Keep existing session gates, writer ownership, share anchors, replacement chunks, and interrupted-rebuild semantics.
Re-resolve after registration, migration, and any operation that can restore or replace a rollout.
Invalidate old preparation before reading results; do not reuse a pre-operation compressed snapshot.
Validate reports by session identity and normalized logical path, then persist the selected physical association.
Do not add a policy for unrelated rollout paths created by paginated revert.

- [ ] **Step 4: Run migration and recovery regressions.**

Run: `uv run pytest tests/test_codex_compressed_migration.py tests/test_codex_rollout_migration.py tests/test_codex_migration_jobs.py tests/test_codex_migration_scheduler.py tests/test_codex_compute_recovery.py -q`.
Expect PASS with the existing migration and replacement protocols.

- [ ] **Step 5: Review this task.** Commit only if requested. Suggested subject: `fix(codex): migrate compressed rollout sources`.

## Task 5: Preserve response polling and verify performance boundaries

**Files:** Modify `src/twicc/cli/_wait_reply.py`; extend `tests/test_wait_reply.py` and `tests/test_cli_sessions_wait_reply.py`.
Create `tests/test_codex_compressed_lifecycle.py` and `tests/test_codex_compressed_fast_paths.py`.

**Interfaces:** Consume `compressed_backlog_hint(path, offset)` from Task 1 and existing response-wait interfaces.
No per-waiter object, decoded cache, background preparation, or polling lifecycle is introduced.

- [ ] **Step 1: Add failing polling and end-to-end tests.**

`test_plain_wait_keeps_existing_size_check` verifies ordinary JSONL and Claude use their current checks.
`test_compressed_wait_never_decodes_or_blocks_other_waiters` checks bounded reads and timeout responsiveness.
Test stale stored ZST path after active resume: choose restored JSONL and detect its unimported bytes.
Test first-frame declared size above the offset: positive backlog. Unknown size or a non-positive hint retains current unknown-source fallback.
Test concatenated input whose first frame does not establish total size; never treat that header as validated full-stream EOF.
The lifecycle test imports plain, compresses, restarts inventory, restores, appends, and deletes both representations.
Assert durable identity, checkpoints, item IDs, path, history epochs, availability, and emitted session updates.

- [ ] **Step 2: Run the new tests and confirm failures before implementation.**

Run: `uv run pytest tests/test_codex_compressed_lifecycle.py tests/test_codex_compressed_fast_paths.py tests/test_wait_reply.py tests/test_cli_sessions_wait_reply.py -q`.

- [ ] **Step 3: Implement the bounded polling adaptation.**

For ordinary valid JSONL, keep the current `getsize()` path directly.
Only a compressed or missing Codex path needs sibling resolution; use no sleeps.
If compressed remains selected, inspect at most the first frame header through `zstandard` frame metadata.
A declared size greater than the offset proves backlog. Other results are unknown, not proof of complete ingestion.
Retain existing `False` fallback for unknown-source advisory checks. Do not change answer confirmation or managed-interval logic.
This limits compressed unmanaged-history backlog detection when size evidence is unavailable; document it in the helper.
Active resumed sessions use restored JSONL and retain their current complete size comparison.

- [ ] **Step 4: Verify the performance contract with deterministic instrumentation.**

Count reader calls, bytes read, decode calls, temporary copies, SQL queries, and compressed-worker admission on representative paths.
Ordinary unchanged JSONL and ordinary appends must add no source read, SQL query, copy, or compressed-worker wait.
Run a blocked compressed decode beside a small ordinary JSONL append; the ordinary drain must finish before releasing the decode barrier.
Check one decode per compressed drain, bounded output chunks, cleanup, and no source modification.
Measure decoder wall time and peak memory on realistic and highly compressible large fixtures.
Record results during implementation; no performance numbers exist at planning time.
Reject unrelated overhead rather than hiding it with loose timing thresholds or widening the architecture.

- [ ] **Step 5: Run focused feature and existing regression tests.**

Run the commands from Tasks 1–5 once after the final change.
Then run `uv run pytest tests/test_live_sync_session_reads.py tests/test_live_sync_aggregates.py tests/test_live_sync_rollback.py tests/test_nested_subagents_watcher.py -q`.
Do not install packages, invoke the real provider homes, or run real-binary integration tests without the applicable explicit request.

- [ ] **Step 6: Request final independent review and report evidence.**

Report changed behavior, measured path costs, unresolved limitations, and required user operations.
Do not claim runtime performance is unchanged from plan review alone.
No implementation or operational action follows until the user explicitly requests it.

## Spec coverage and validation limits

| Requirement | Tasks |
|---|---|
| Identity, physical association, stale recovery, startup matching | 2–3 |
| Logical offsets, integrity, cleanup, bounded decoder memory | 1 |
| Transition-safe watcher, fairness, drain targets, normal fast paths | 3, 5 |
| Existing migration, replacement, diagnosis recovery | 2, 4 |
| Cheap response polling with explicit unknown-size fallback | 1, 5 |
| Preservation, restart recovery, both-provider regression checks | 2–5 |

This plan preserves existing list-based startup/rebuild memory use and bounded source-sampling limits.
It does not promise arbitrary rewrite detection or full backlog knowledge from compressed frame headers.
Decoder throughput and peak memory still require implementation-time measurement.
No product source code, tests, runtime data, or server state changes in this planning phase.

## Review record

The earlier plan passes correctness review after three rounds, but its scope exceeds the user's performance constraints.
Its previous readiness does not endorse the removed architecture.
A new source review confirms migration as the identified same-path rewrite and recommends the removals above.
Two internal reviewers then assess the revised plan for necessary correctness and ordinary-path costs.
They receive explicit instructions to remain at plan level and avoid unrelated architecture expansion.

| Finding | Correction |
|---|---|
| Temporary JSONL inode can falsely identify a new source version | Use physical source identity and test separate temporary files across drains. |
| Detached preparation failure can leave a deferred waiter unsettled | Resume the current attempt with stored failure and use the existing failed disposition. |
| Codex rewrite detection can stat an absent logical JSONL instead of decoded content | Forward the read path for size while retaining logical state keys. |

Both reviewers return READY after these corrections. No material review finding remains open.
This validates plan feasibility and scope, not measured runtime performance.
