# Content Writer Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Implement task-by-task, with independent review after each task.

**Goal:** Give normal application writes precedence over bounded compute and content-maintenance publication without weakening data correctness.

**Architecture:** One local coordinator manages admission and writer resources. One pending compute application prepares bounded batches on a pinned worker connection, then publishes through short coordinated leases. Final publication remains atomic.

**Tech Stack:** Python 3.13+, Django, Channels, asyncio, SQLite WAL, existing Zstd dependency, pytest.

**Spec:** `docs/plans/2026-10-12-content-writer-priority-design.md`.

## Global Constraints

- Work only in `/home/twidi/dev/twicc-poc/.worktrees/content-writer-priority`, branch `fix/content-writer-priority`, baseline `442d07cc`.
- The original feature worktree is running. Do not edit its product files or operate its servers/database.
- Prefix shell commands with `cd /home/twidi/dev/twicc-poc/.worktrees/content-writer-priority &&`.
- Set `TWICC_DATA_DIR=$PWD` for every Python invocation. Tests use the existing test settings and isolated databases.
- No schema, dependency, index, provider-home, cold-policy, scratch-policy, API, or changelog changes.
- Foreground wins at resource-grant boundaries; already admitted work drains before release.
- Two priority classes: foreground and background. FIFO among eligible requests within a class; no priority aging.
- Default existing public/unclassified jobs to foreground. Compute, bulk ingestion, recovery, and periodic compression are background.
- Explicit target promotion serves foreground. Preserve recovery provenance and root-only startup recovery.
- One pending compute result and one prepared batch; no successor result or done marker before terminal handling.
- Keep metadata budget 2,000 rows and storage budgets 256 rows / 8 MiB; one oversized content may progress.
- Preserve codec cooperative yields, exact two-slot ownership, original-NULL provenance, digest integrity, and all-owner compression rules.
- Keep final facts/links/metadata/versions/aggregate/readiness publication atomic.
- Use test-first development. Report the failing test, covering command/results, and commit range per task.
- Review design and plan before product edits. Review every task and the complete correction before integration/restart.
- No restart or merge is part of this plan. Present reviewed commits and remaining latency limitations to the user.

## Review Focus

1. Admission dependency: an admission holder's foreground writer request must pass a blocked admission waiter.
2. Cancellation after resource grant: release exactly once, after actual worker commit/rollback, including repeated cancellation.
3. Foreign references: an added second reference cannot disappear from ownership because a pending TEMP count becomes stale.
4. Shutdown/abandonment: pending preparation and application drain or terminate without double acknowledgement or hanging completion.
5. Aggregate consistency: first-bucket repair, hidden/project changes, removed days, and cache eviction cannot yield incorrect activity counts.

## File Map

- Create `src/twicc/providers/write_coordinator.py`: event-loop-owned priority resources and compatible foreground lock facade.
- Modify `src/twicc/providers/db_writer.py`: wrapper integration, pending-result scheduling, queue order, terminal handling, shutdown.
- Modify `src/twicc/providers/compute_executor.py`: independent pinned application worker lifecycle.
- Modify `src/twicc/core/content_storage/references.py`: explicit operation scope, connection identity, paginated TEMP initialization.
- Modify `src/twicc/core/content_storage/extraction.py`: explicit resumable preparation/publication and final fencing.
- Modify `src/twicc/core/content_storage/types.py`: immutable bounded transfer records if needed.
- Modify `src/twicc/providers/compute_base.py`: reserved-field protection, final operation validation, phase diagnostics.
- Modify `src/twicc/content_maintenance_task.py`: foreground-demand versus background-recovery/compression grants.
- Modify `src/twicc/providers/live_aggregates.py` only in Task 6 after its audit/review gate.
- Tests live in existing focused files and new `tests/test_write_coordinator.py`, `tests/test_compute_continuation.py`.

### Task 1: Priority resources and foreground demand

**Files:** Create `write_coordinator.py`, `tests/test_write_coordinator.py`; modify `db_writer.py`; extend `test_content_storage_admission.py` and writer lock tests.

**Interfaces:**
- Produce `WritePriority.FOREGROUND` and `WritePriority.BACKGROUND`.
- Produce `WriteResources.ADMISSION` and `WriteResources.WRITER` flags.
- `WriterCoordinator.acquire(resources: WriteResources, priority: WritePriority) -> ResourceLease` is async.
- `WriterCoordinator.try_background_lease() -> ResourceLease | None` atomically reserves both resources or returns None.
- `ResourceLease.release() -> None` releases once; `WriterCoordinator.drain() -> None` is async and waits for all registered requests/holders.
- A coordinator foreground lock facade supports `acquire`, `release`, `locked`, and async context management for `get_db_write_lock()` compatibility.
- `run_under_storage_write_lock(factory, *, priority: WritePriority, content_activity: bool = False, internal: bool = False)` acquires both resources atomically and reuses the existing cancellation-safe drive.
- Preserve current `run_under_db_write_lock` and `storage_admission` signatures/default behavior; foreground is the default.
- Produce a scoped write-priority ContextVar and idempotent foreground-demand token API. Registered demand blocks new background grants before a foreground operation requests resources. Resource-wait dependencies still permit foreground grants.

- [ ] Write barrier tests for foreground overtaking queued background work, FIFO within eligible classes, background resuming after foreground completion, and no background admission reservation while writer is busy.
- [ ] Add a dependency test: hold admission; enqueue another admission request; request writer from the holder; assert it completes and later admission proceeds.
- [ ] Add grant/cancellation race tests, repeated cancellation during worker publication, raw-lock facade compatibility, reentrance, child-task rejection, and drain covering already-granted requests.
- [ ] Run new tests and confirm expected failures before production edits.
- [ ] Test registered queued foreground demand blocking both compute and maintenance grants; cancelled/failed/settled demand releases pressure exactly once.
- [ ] Implement the coordinator with public state; do not inspect asyncio private waiter internals. Maintain existing manager/admission/writer/transaction order.
- [ ] Integrate wrappers while preserving `_LockLease` task membership, eager-task-factory behavior, stop guards, and cancellation drain.
- [ ] Run coordinator, writer-lock, admission, maintenance, and executor tests. Update all changed annotation/FIFO contracts.
- [ ] Commit with a descriptive Conventional Commit body and current model co-author trailer. Review before Task 2.

### Task 2: Slice-level maintenance demand scheduling

**Files:** Modify `content_maintenance_task.py`, `core/content_storage/lifecycle.py`, `core/content_storage/state.py`, and priority-origin scope in `db_writer.py` if needed; extend `tests/test_content_maintenance.py` and admission tests.

**Interfaces:**
- Consume coordinator combined leases, foreground-demand tokens, and scoped WritePriority origin from Task 1.
- Extend `request_root_promotion(session_id: str, *, priority: WritePriority | None = None) -> None`, retaining one-argument callers and recovery provenance. Carry resolved scheduling priority separately through the thread-safe enqueue callback.
- Explicit foreground origin produces foreground demand; background compute binding notifications remain background with recovery=False.
- Preserve one maintenance codec worker and bounded batches. Store interrupted target snapshot/cursor/verification state explicitly rather than keeping a whole target in an awaited local loop.
- Keep FIFO among eligible foreground targets; duplicate target requests coalesce, foreground upgrades background, and explicit demand overrides recovery eligibility.
- Produce an event-loop-owned idempotent background-promotion deferral token keyed by guarded root identity and incarnation. Task 5 owns its compute lifetime. Existing and new background targets retain exact target/provenance while deferred; foreground demand bypasses deferral. Unresolved ancestry receives no guessed root scope.

- [ ] Write failing barriers: demand for B arrives during large recovery A; B publishes before A's next background slice.
- [ ] Test same-target demand upgrading recovery, demand arriving while a background resource request waits, FIFO foreground targets, and no deadlock from registered pressure.
- [ ] Test background compute binding wakeups: no foreground token, no full promotion scan per extraction slice, unchanged eligibility provenance.
- [ ] Test synthetic operation deferral across changing epochs, queued recovery before registration, exact target retention, foreground bypass, release wakeup, late callbacks, successor tokens, and shutdown. Task 5 proves activation through the real compute path; Task 2 does not claim production integration yet.
- [ ] Test cancellation/shutdown during codec preparation and admitted publication, including token cleanup and retained periodic deadline/scratch rules.
- [ ] Confirm failures before restructuring maintenance dispatch.
- [ ] Advance recovery and demanded targets one bounded preparation/publication step at a time. A waiting background grant must not suspend the demand dispatcher.
- [ ] Preserve completed codec work or drain it before changing target; never pretend cancelling a future stops its thread. Interruption occurs before a grant or after an admitted publication drains.
- [ ] Defer further background preparation/publication for the registered root, including already queued recovery. A cheap guarded snapshot may resolve queued target scope. Preserve immediate epoch invalidation, exact membership, ordinary decoding, and readiness checks. Deferral neither certifies plaintext nor creates tree-wide requests.
- [ ] Register foreground pressure on enqueue and retain it through target settlement or safe transfer to an executing foreground request. Never let that pressure block its own foreground dispatch.
- [ ] Capture priority origin as an immutable callback argument when registering `transaction.on_commit`, not only when the callback later executes. Pass it explicitly through `call_soon_threadsafe`; do not infer priority solely from recovery=False. Scope/reset origin per publication, including pinned-worker calls; later background invalidation cannot downgrade foreground demand.
- [ ] Run maintenance, lifecycle, admission and coordinator tests. Preserve root-only recovery and target-only promotion.
- [ ] Commit and independently review before Task 3.

### Task 3: Pinned worker and resumable reference accounting

**Files:** Modify `compute_executor.py`, `references.py`; extend `test_compute_executor.py`, `test_content_references.py`.

**Interfaces:**
- Produce `start_compute_application_executor() -> None`, async `run_compute_application_sync(function, /, *args, **kwargs)`, async `stop_compute_application_executor() -> None`.
- The single application worker owns its Django connection. It uses raw executor calls with explicit copied/scoped ContextVars, without per-call Channels connection cleanup.
- Produce worker-side `close_compute_application_connection() -> None`; cleanup runs on that worker after each terminal operation and before shutdown.
- Extend `SessionReferenceOperation` with explicit `activate()`, `validate_connection()`, and idempotent `close()`; retain the synchronous `session_reference_operation` compatibility wrapper.
- Produce paginated TEMP count initialization, consuming line-ordered current/original FK metadata with a row budget and a guarded observation callback.
- Preserve transactional `capture_mutation` and existing optimized `_referenced_ids` behavior.

- [ ] Write failing tests proving TEMP persistence across application calls with `CONN_MAX_AGE=0`, and unrelated writer jobs progressing while application preparation blocks.
- [ ] Test physical connection replacement before TEMP use, terminal cleanup after a broken transaction, and executor shutdown draining an uncancellable thread.
- [ ] Test paginated counts for equal FK slots, ignored insertion conflicts, mutation rollback, no ambient ContextVar leakage, and no full-session rebuild per publication.
- [ ] Run the tests and capture expected failures.
- [ ] Implement one lifetime application worker, distinct from both the original writer executor and maintenance codec worker.
- [ ] Refactor reference scope without leaving context managers suspended across unrelated calls. Store and validate physical connection identity.
- [ ] Build TEMP counts through bounded read/TEMP-only transactions outside the main writer lease. Never use the main no-op UPDATE during count preparation.
- [ ] Scope connection lifetime explicitly. On replacement or unusable cleanup connection, abort and close; never continue with missing/stale counts.
- [ ] Run reference and executor tests, including existing transaction/savepoint behavior.
- [ ] Commit and review before Task 4.

### Task 4: Bounded storage preparation and publication

**Files:** Modify `extraction.py`, `types.py`, `compute_base.py`; extend `test_content_extraction.py`, `test_content_initial_compression.py`, `test_content_references.py`.

**Interfaces:**
- Produce mutable `ComputeStorageOperation` containing message, phase/cursors, exact target observation, expected root observation, count handle, and connection identity.
- `begin_compute_application(msg: dict) -> ComputeStorageOperation`: short guarded initialization under an admitted publication.
- `prepare_compute_batch(operation: ComputeStorageOperation) -> PreparedComputeBatch`: one count/metadata/override/extraction/reconciliation preparation step on the pinned worker, outside resources.
- `publish_compute_batch(operation: ComputeStorageOperation, batch: PreparedComputeBatch, active_ids: frozenset[str]) -> ComputeStorageProgress`: one guarded publication with committed cursor/epoch updates.
- `final_compute_message(operation: ComputeStorageOperation, active_ids: frozenset[str]) -> dict`: final trimmed message with operation observations and fresh active protection.
- `close_compute_application(operation: ComputeStorageOperation) -> None`: worker-owned idempotent cleanup.
- Immutable batch/progress records use NamedTuple. Do not expose live QuerySets or unbounded payload collections.
- Keep `prepare_compute_result` as a synchronous compatibility wrapper using the same phase primitives. The new async runtime must never call this whole-session wrapper.

- [ ] Add failing tests for interleaved same-session append after count initialization, offset-decreasing history replacement, equal-offset recreation, sibling/root mutation, and target reparenting.
- [ ] Add tests for shared-owner/representation changes, new active child between preparation/publication, cold threshold changes, existing compressed-row reuse by a hot target, and unchanged original-NULL provenance.
- [ ] Add tests for unresolved/cyclic ancestry: plaintext placement and exact persisted reconciliation; later resolution supersedes instead of adopting old counts.
- [ ] Add reserved metadata-field rejection tests for `content`, `current_content`, `original_content`, and both FK `_id` aliases. They must not bypass audited binding mutation paths.
- [ ] Confirm failures before implementing phases.
- [ ] Split existing prepare/publication bodies without duplicating storage logic. Preserve observed-current/digest identical-result retry and all collision checks.
- [ ] Keep the conditional target no-op UPDATE first in each main publication transaction. Validate exact operation offset/upper line, target type/parent, ancestry, root incarnation/epoch, and initial lifecycle fields before cached count use.
- [ ] Use audited binding/deletion/replacement fences for resolved targets. For unresolved targets, disable cached counts/compression and reconcile from current persisted assignments.
- [ ] Refresh active roots and eligibility time per publication; fresh owner checks apply even when the target proof is unchanged.
- [ ] Advance Python cursors/expected epoch only after successful atomic exit. Read resulting epochs; do not guess increments or refresh after foreign changes.
- [ ] Validate the operation in final atomic publication; preserve goals/plan-path/task concurrent folds and root-own readiness scope.
- [ ] Run storage extraction, references, initial compression, readers, lifecycle, and admission tests.
- [ ] Commit and review before Task 5.

### Task 5: Writer continuation and terminal handling

**Files:** Modify `db_writer.py`, `compute_executor.py`; create `tests/test_compute_continuation.py`; extend provider background-compute/writer and executor tests.

**Interfaces:**
- Consume Tasks 1–4 APIs.
- Produce one `PendingComputeApplication` owned by the writer loop, with run ID, receive/apply times, operation, at most one preparation future/batch, and terminal outcome.
- The pending result owns Task 2's root/incarnation deferral token, registered after guarded initialization before its lease releases. Release it on every terminal outcome after admitted work drains, in cancellation-safe cleanup even if operation cleanup raises. Release before acknowledgement or successor dispatch. Retained exact targets resume; late callbacks cannot revive or release another token.
- `_drain_one()` services foreground queued writer jobs before any background application or ingestion advancement.
- Queued foreground pressure registers before dispatch and remains continuous until resource grant/settlement. Separate foreground/background job queues preserve FIFO within each class; background initialization cannot hide foreground jobs behind it.
- Background advancement uses nonblocking combined acquisition. It never waits for background resources inside the permanent writer tick.
- Existing `ComputeApplied`, run futures, abandonment, missing/superseded/failed outcomes, progress and broadcasts remain compatible.

- [ ] Write failing barrier tests: external watcher and queued async job both commit before a multi-batch result ends; they also progress while preparation blocks.
- [ ] Test that a foreground waiter arriving after preparation wins before the next compute or compression publication.
- [ ] Test compute FIFO/done ordering, abandoned-run cleanup, exactly-one terminal handling, shutdown during preparation/publication, and final rollback with valid partial extraction.
- [ ] Test no tight idle spin while preparation waits or foreground excludes background admission.
- [ ] Test real compute deferral across multiple binding slices, existing root recovery during child compute, explicit foreground bypass, compressed-row hot reuse, valid partial extraction after supersession, cleanup failure, cancellation, abandonment, shutdown, and late callbacks. No repeated full-target background scan occurs per extraction slice.
- [ ] Confirm failures, then introduce the pending state and explicit phase advancement.
- [ ] Service foreground async jobs first. If their backlog remains, skip compute, initialization and bulk ingestion for this tick.
- [ ] Register queued foreground demand before enqueue-to-dispatch can expose a background grant. Release or transfer tokens without a dequeue-to-grant gap. Settle all tokens on failure and shutdown.
- [ ] Classify InitializeComputeStorageJob as background. Keep FIFO within each class, with eligible background initialization and bulk ingestion using nonblocking dispatch. Do not await a low-priority grant in the permanent tick.
- [ ] Give eligible bulk ingestion, initialization, and compute bounded turns when foreground traffic stops; avoid starving provider initialization behind a pending result.
- [ ] Start preparation on the application worker and retain its future. Collect only completed preparation; do not await unfinished work in the permanent tick.
- [ ] Capture actual active IDs on the event loop under admission before publication and final application. Never freeze them for the whole operation.
- [ ] Recheck run abandonment before each publication. Drain already-admitted work before teardown; close operation state before another result uses the application worker.
- [ ] Refactor common terminal handling to preserve provider acknowledgements and completion policy exactly. Do not acknowledge slices or publish through a stale detached run state.
- [ ] Move safe notifications outside the compute lease. Worktree links, colors, and other post-commit database writes retain their own classified writer lease.
- [ ] Start/stop the extra worker with the writer lifetime, including startup failure cleanup and pending preparation during shutdown.
- [ ] Run queue, lock, compute, provider acknowledgement, and all content tests.
- [ ] Commit and review before Task 6.

### Task 6: Final hold diagnostics and gated exact bucket preparation

**Files:** Modify `compute_base.py`, `db_writer.py`, `sync_diagnostics.py` if needed; conditionally modify `live_aggregates.py`; extend aggregate and continuation tests.

**Interfaces:**
- Produce payload-free phase timings and rows/bytes, lock waits/holds, cursor progress, invalidation reason/retries, and final subphase duration.
- Preserve existing timing/log consumers; report maximum publication hold separately from total apply.
- Retain a complete repair-key inventory independently of shortcut state, including old timestamps removed by committed metadata chunks.
- Optional preparation begins after all metadata/override/extraction/reconciliation source writes finish, one exact bucket per writer turn.
- Use private per-result preparation state with the dedicated physical connection, fixed `PRAGMA main.data_version`, required keys, committed keys, cursor, and explicit fallback reason.
- During the proof interval, this connection serves only this operation. No unrelated writes, outer transaction, read snapshot, open iterator, or unfinished cursor crosses publication boundaries.
- Never import existing per-run cache entries as proof. Exact fallback passes `repaired_activity_buckets=None` and retains the complete repair inventory.
- Exclude all old/new creation day/week keys at project/global scope from final cache reuse, regardless of apparent count stability.
- Final admission validates the same physical connection, fixed data_version, completed source phases, stable metadata, and committed key membership after the first guarded write.

- [ ] Write tests for phase diagnostics without payload/SQL leakage, and a diagnostic final-aggregate delay distinct from codec preparation.
- [ ] Audit all contribution mutation paths: live ingestion, metadata chunks, hide/project/provider/type changes, creation/date changes, replacement/deletion, parent costs, and cache eviction.
- [ ] Write the audit result in this plan's private ledger. Obtain an independent reviewer verdict before adding the optional bucket optimization.
- [ ] If the audit cannot prove exact committed baselines survive all supported writes, retain original atomic repair and record the reason. This is a deliberate safe outcome, not permission to skip correctness.
- [ ] The mutation audit rejects unrestricted pre-repair before normalization. The independent narrowed-design review approves the following three boundaries. Implement and review each boundary separately before enabling shortcut reuse.
- [ ] **6A: Exact obligations and diagnostics.** Preserve current, removed, prospective, creation, and worker affected keys. Add explicit extra repair keys or equivalent zero-delta enumeration to the final helper. Initially use no prepared cache. Test raw SQL aggregate cost and endpoint-style SUM, including legacy normalization and count-only precision counterexamples.
- [ ] **6B: Operation-owned preparation.** Prepare bounded key pages with before/after fixed-version validation. For each exact repair, start with the target guarded write, verify data_version and physical identity, and sample the expected version before commit. Record the key/cursor/version only on successful commit. Foreign commits invalidate all keys. Never sample a replacement baseline after commit while retaining evidence.
- [ ] **6C: Final admission and integration.** Within final write ownership, recheck all preparation gates and exclude actual old/effective-new creation keys. Pass the private filtered cache or None directly into the final helper in the same transaction. Retain all old-key obligations on either path. Discard private state on terminal handling.
- [ ] Add adversarial equivalence/interleaving tests for project/global day/week keys, null/old/new dates, removed timestamps, hidden sessions, native children, metadata changes, legacy cache entries, failed repair rollback, failed commit callbacks, stale snapshots, physical connection replacement with equal version numbers, unrelated foreign commits, and current-version replay mutations without aggregate deltas.
- [ ] Preserve exact no-cache final repair for unknown/missing keys, metadata changes, failed proof, connection loss, or more than 4,096 keys. Bound preparation retries. Performance fallback must never skip exact repair.
- [ ] Keep expensive repair transactions visible: one bucket may itself exceed the desired interactive wait. No strict latency guarantee or new index follows from this task.
- [ ] Run aggregate, continuation, lifecycle, replacement/deletion and content tests.
- [ ] Commit and independently review diagnostics and any approved optimization.

### Task 7: Whole-correction verification and isolated load evidence

**Files:** Extend tests when a missing regression is discovered; update this design/plan and the existing audit with final evidence only. Private harnesses remain in the plan workspace.

**Interfaces:** Consume reviewed Tasks 1–6; produce verification evidence and reviewed commit range, without automatic merge/restart.

- [ ] Check the plan/spec against the delivered paths, including both providers, foreground classification, cold/shared content, cancellation, retries and terminal ordering.
- [ ] Run `TWICC_DATA_DIR=$PWD uv run pytest` in this worktree. Record all failures/skips/warnings. Fix regressions through the implementing agent and review the fix.
- [ ] Run targeted `uvx ruff check` for changed Python files and `git diff --check`.
- [ ] Run a synthetic isolated workload with large hot/cold results, shared current/original contents, concurrent foreground writes, and compression publication. It must assert semantic equivalence, not only elapsed time.
- [ ] Measure foreground completion between batches and during blocked preparation, background resumption, total throughput, maximum resource hold, and final hold. Report DEBUG modes separately if measured; do not extrapolate synthetic timing to the live database.
- [ ] Dispatch an independent whole-correction review against `442d07cc`, carrying private ledger rulings and task review evidence. Do not repeat the original feature's unrelated whole-branch review scope.
- [ ] Resolve Critical/Important findings, rerun covering checks, and request scoped re-review before presenting the correction as ready.
- [ ] Update persistent audit with delivered changes, measurements and remaining atomic-hold/fallback limitations. Mark tasks from evidence, not anticipated success.
- [ ] Present paths, commits, reviews and checks. Keep both existing servers and main untouched. Integration or restart remains a separate user action.
