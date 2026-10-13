# Compute Backpressure and Finalization Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Execute tasks sequentially with independent spec and quality review.

**Goal:** Bound cross-provider result backlog and reduce writer holds without weakening foreground priority or final readiness correctness.

**Architecture:** Apply the existing bounded Claude pipeline to Codex admission. Keep one active compute application and its pinned TEMP connection. Remove preliminary activity repairs that final policy never reuses, use SQL metadata validation, and privately stage history facts before atomic publication.

**Tech Stack:** Python, asyncio, Django, SQLite WAL, existing compute IPC and pinned application executor.

**Spec:** Approved behavior below implements the recommendations explicitly authorized by the user on 2026-10-13.

## Approved Behavior

- Codex admits at most two outstanding sessions, including computed results awaiting writer application. Preserve overlapping calculation and application.
- Avoid preliminary repairs of creation activity buckets that final policy always excludes from reuse. Preserve their complete inventory and exact final repair.
- Reduce readiness validation overhead through SQL-equivalent metadata checks and avoid duplicate root validation within the same final transaction.
- Prepare, validate, and serialize history facts outside main writer ownership. Publish complete replacement atomically with final metadata and readiness.
- Measure final subphases and compare focused behavior/performance against the current implementation. Do not promise a wall-clock gain from summed lock holds.
- Do not replace raw parent cost sums with unchecked cached costs or introduce batch interleaving across sessions.

## Global Constraints

- Work only in `.worktrees/feature-content-storage-compression` on `feature/content-storage-compression`.
- DevCTL is stopped by explicit user request. Leave it stopped. Do not delete/reset or mutate its actual database or index.
- No dependency installation, model/schema migration, main merge, push, or provider-file mutation.
- Foreground writes and asyncio responsiveness retain priority over compute and maintenance.
- Keep a single main database writer and one active application. Keep preparation outside writer ownership and on the pinned application worker.
- Preserve source revision, incarnation/epoch, ancestry, activity data_version and repair-journal fences, cancellation-safe drain, migration leases, retry cooldowns, and startup progress/search behavior.
- Readiness and aggregates remain correct at final publication. A failed or superseded operation must not publish partial current history facts.
- Maintain the current synchronous compatibility APIs and JSON/duplicate/constraint behavior.
- Tests and benchmarks use isolated SQLite databases/provider fixtures. No overlapping pytest suites. Run the full suite once code and fixtures settle after reviews.
- English artifacts. Conventional commits with a descriptive body and current model trailer. Do not modify CHANGELOG.

## Review Focus

- Applied acknowledgment can arrive before worker status: outstanding admission must remain bounded.
- Creation dates can change: skipped preparation must never become a reusable proof accidentally.
- SQL NULL handling must reject dangling/missing current content and invalid optional original content exactly as existing metadata validation.
- Forced rebuild can target a current session: readers must never observe a partial replacement fact set.
- Preparation, publication, and cleanup can fail or receive repeated cancellation: retain worker resources until actual work drains.

### Task 1: Bound Codex Outstanding Admission

**Files:**
- Modify: `src/twicc/providers/codex/background_compute.py`
- Test: `tests/test_compute_age_selection.py`
- Test/adapt: `tests/test_codex_compute_recovery.py`

**Interfaces:** Keep coordinator APIs. Add `CODEX_MAX_OUTSTANDING = 2` near coordinator constants. Admission requires `in_flight is None` and `len(submitted | computed_not_applied) < CODEX_MAX_OUTSTANDING` before `prepare_candidate`.

- [x] **Step 1: Write and run RED regressions.** Hold real writer application while worker status arrives; assert only two unique sessions initialize/dispatch and a third waits. Hold worker status after an early applied acknowledgment; assert its submitted slot remains occupied. Include failed results awaiting application, retired-run signals, and stop during capacity wait.
- [x] **Step 2: Implement the admission bound.** Do not clear tracking sets early, exclude failed pending results, or consume capacity for a deferred preparation. Preserve ordinary age ordering and event handling.
- [x] **Step 3: Validate focused pipelines.** Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_compute_age_selection.py tests/test_codex_compute_recovery.py tests/test_codex_migration_scheduler.py tests/test_compute_project_notifications.py -q`. Run scoped `uvx ruff check` and `git diff --check`.
- [x] **Step 4: Commit and obtain task review.** Report RED/GREEN and remaining limitations. The bound limits queue length, not maximum duration of a large application.

### Task 2: Skip Non-Reusable Preliminary Activity Repairs

**Files:**
- Modify: `src/twicc/providers/activity_preparation.py`
- Test: `tests/test_activity_preparation.py`
- Test/adapt: `tests/test_compute_continuation.py`

**Interfaces:** Add `ActivityPreparation.skipped_keys` as a private set. Preserve `required_keys` and `operation.activity_repair_keys` as complete inventories. `ordered_keys` holds only preliminarily repairable keys. Derive skipped creation keys from stored metadata and effective worker `created_at`, project and provider. Final reusable cache excludes both skipped keys and actual final creation keys.

- [x] **Step 1: Write and run RED regressions.** Four creation keys alone require zero preliminary grants but four exact final repairs. Mixed inventory repairs only reusable keys. Continue complete enumeration even when the first page contains only skipped keys. Cover changed created_at, external commit, journal changes, cancellation and superseded cleanup.
- [x] **Step 2: Separate inventory from reusable evidence.** Change page completion/publication/final policy checks consistently. Validate repaired evidence against `required_keys - skipped_keys`; never lose historical keys or relax source/connection/data_version/journal fences. All-skipped operations still validate external commits and perform exact final repair.
- [x] **Step 3: Validate exact aggregates.** Run `TWICC_DATA_DIR=$PWD uv run pytest tests/test_activity_preparation.py tests/test_compute_continuation.py tests/test_compute_activity_obligations.py tests/test_activity_final_admission.py -q`. Use existing exact-comparison fixtures, not implementation-mirroring tests. Run scoped Ruff/diff checks.
- [x] **Step 4: Commit and obtain task review.** Report actual preliminary grant reduction and final-result parity.

### Task 3: SQL Readiness Validation and Complete Final Timings

**Files:**
- Modify: `src/twicc/core/content_storage/state.py`
- Modify: `src/twicc/core/content_storage/extraction.py`
- Modify: `src/twicc/providers/compute_base.py`
- Test: `tests/test_content_root_state.py`
- Test/adapt: `tests/test_content_extraction.py`

**Interfaces:** Preserve `session_plaintext_metadata(session_id: str) -> bool` and `verify_plaintext_root` behavior for all callers. Replace Python metadata pages with a SQL invalid-row existence probe. If duplicate validation is removed, use a private transaction-scoped helper; do not expose a general caller-supplied trust flag. Keep root version, incarnation, epoch, native ancestry, hot policy and both content-reference checks.

- [x] **Step 1: Write and run RED performance/correctness regressions.** Verify bounded query count independent of session row count and unchanged answers for empty sessions, incomplete extraction, legacy content, missing/dangling current reference, compressed/dictionary-bearing current or original reference, missing incarnation, changed epoch and cold root. Capture SQL query count, not flaky timing thresholds.
- [x] **Step 2: Implement SQL-equivalent metadata validation.** Resolve NULL semantics explicitly. Final certification may reuse a check only in the same guarded writer transaction and for that exact root; ordinary public validation remains conservative.
- [x] **Step 3: Instrument remaining final subphases.** Add existing `timed_sync_phase` scopes for ancestor plan-doc fold, titles, project directory/git resolution, stopped-agent lifecycle and project metadata. Keep operations and exceptions unchanged.
- [x] **Step 4: Validate correctness and isolated performance.** Run existing content state, readiness, search, extraction and continuation tests. Benchmark metadata validation on small and large isolated sessions against the prior implementation; report both query count and duration with measurement limits. Run scoped Ruff/diff checks.
- [x] **Step 5: Commit and obtain task review.** No readiness weakening or aggregate publication changes.

### Task 4: Private History Fact Staging Before Final Publication

**Files:**
- Modify: `src/twicc/providers/history_facts.py`
- Modify: `src/twicc/core/content_storage/extraction.py`
- Modify: `src/twicc/providers/db_writer.py`
- Modify: `src/twicc/providers/compute_base.py`
- Test: existing history-fact tests and `tests/test_compute_continuation.py`

**Interfaces:** Introduce a private operation-owned `ComputeHistoryFactsStage` in `history_facts.py`, prepared and closed on the pinned application connection. A TEMP table stores validated, DB-adapted rows in original order. `prepare_compute_history_facts(session_id: str, facts: Sequence[HistoryFact]) -> ComputeHistoryFactsStage` builds it without main writer ownership. `publish_compute_history_facts(stage) -> None` runs only in the guarded final writer transaction. `stage.close()` is idempotent and validates connection ownership. Existing `replace_history_facts` and append/read APIs remain compatible.

- [x] **Step 1: Write and run RED regressions.** Verify complete old facts remain visible during preparation for a forced rebuild of a current session. Verify exact replacement, first duplicate wins, same JSON adaptation/constraints, empty replacement and rollback on failure. Hold preparation and cancel repeatedly; cleanup waits for actual worker completion. Verify superseded source and invalid input do not alter durable facts, and TEMP state disappears on success/error/stop.
- [x] **Step 2: Implement private staging.** Validate and adapt JSON through existing Django field/backend behavior, with bounded chunks and cooperative worker yields. Never build or serialize the whole fact list on asyncio. Quote internal TEMP names safely. TEMP rows include no main-table PKs; preserve original ordering for conflict handling. Clean up partially initialized staging if preparation fails.
- [x] **Step 3: Integrate the retained preparation phase.** Store the stage on `ComputeStorageOperation`; prepare before final publication outside main writer ownership using the existing retained future. Preserve activity initialization/version sampling order. Use staged DELETE plus INSERT SELECT only after final revision guards; retain synchronous compatibility fallback. Close stage before connection cleanup in all terminal paths.
- [x] **Step 4: Validate parity and benchmark.** Run history-fact, resolver, continuation, recovery, extraction and readiness tests sequentially. Compare old replacement with staging at small/large fact counts in isolated databases. Report total preparation plus final writer hold separately; benchmark is not a production duration guarantee. Run scoped Ruff/diff checks.
- [x] **Step 5: Commit and obtain task review.** Keep atomic final fact publication and all source/lifecycle fences.

## Final Validation

- [x] Obtain independent final review of `e72d53c9..HEAD`; address findings through one fix wave and scoped re-review.
- [x] Run `TWICC_DATA_DIR=$PWD uv run pytest` after all code and fixtures settle.
- [x] Record focused benchmarks, test results and limitations. Keep DevCTL stopped, database/index intact and branch/worktree preserved.

## Execution Evidence

Task reviews approve Tasks 1 through 3 without blocking findings.

| Task | Commit | Focused validation | Result |
| --- | --- | --- | --- |
| Codex admission | `a08bb317` | Age selection, recovery, migration scheduler, project notifications | 80 passed |
| Activity preparation | `07159b57` | Preparation, continuation, obligations, final admission | 250 passed |
| SQL readiness and timings | `51cb4c6f` | Root state, extraction, contains gate, readers, search readiness, continuation | 200 passed |

Activity fixtures remove preliminary grants from 4 to 0 and from 8 to 4.
Both retain four exact final repairs and raw aggregate parity.

SQL metadata benchmark uses seven alternating warmed samples in isolated in-memory SQLite.
Setup is excluded. Shared small payloads and metadata-only queries limit the result.

| Rows | State | Old/new queries | Old/new median milliseconds |
| ---: | --- | --- | --- |
| 128 | Valid | 3 / 2 | 3.067 / 0.600 |
| 50,000 | Valid | 198 / 2 | 334.676 / 20.348 |
| 128 | Invalid last row | 2 / 2 | 1.781 / 0.537 |
| 50,000 | Invalid last row | 197 / 2 | 319.586 / 18.679 |

These results do not measure production disk I/O or whole-final writer latency.
The SQL scan remains proportional to session rows.
Final hot-root certification uses one mandatory plaintext metadata probe after the final fix.
Public verification remains conservative and independent.

Task 4 review approves private staging and atomic publication without blocking findings.
Commit `ca64e9a2` passes 366 scoped tests across history facts, continuation,
recovery, extraction, activity preparation, content readers, search and MCP readiness.

The history benchmark uses isolated SQLite WAL with `synchronous=NORMAL` and five samples.
It verifies exact raw adapted-row parity after every sample.
Setup, cleanup and parity reads are excluded from measured windows.

| Facts | Old writer hold median ms | Preparation median ms | New final hold median ms | New total median ms |
| ---: | ---: | ---: | ---: | ---: |
| 64 | 3.040 | 2.028 | 0.362 | 2.405 |
| 100,000 | 3271.578 | 2446.078 | 439.130 | 2926.063 |

Preparation includes validation, Django JSON adaptation, TEMP construction and cooperative yields.
Final hold includes transaction entry, DELETE, ordered INSERT SELECT and commit.
Large final publication remains proportional to the fact count.
The input fact list remains allocated in the received compute message.
These measurements do not guarantee production durations or whole-compute speedup.

Final review approves the complete range without Critical or Important findings.
Its remaining Minor duplicate plaintext scan is fixed in `eeb5cf07`.
Scoped re-review approves that fix without new findings.
The fix passes 223 covering tests and 16 settled new regressions.
The separate incomplete-placement probe remains intact.
Inherited `VIRTUAL_ENV` warnings confirm that uv ignores the main checkout environment.
They require no product change and must never be suppressed with `--active`.

## Final Validation Result

The stable product tree at `eeb5cf07` passes the complete suite:

```bash
TWICC_DATA_DIR=$PWD uv run pytest
```

Result: **10,688 passed, 28 skipped, 3 warnings in 610.55 seconds**. Exit code: 0.
The three RuntimeWarnings concern existing AsyncMock/notification test fixtures in
`test_codex_sdk_wrappers.py` and `test_shell_notice_codex.py`.
They match the earlier validation baseline and do not fail the suite.

Scoped `uvx ruff check` passes all 14 changed Python files.
`git diff --check` passes.
Final review and scoped re-review report no unresolved findings.
No controller rulings or parked findings remain.

A final read-only `uv run ./devctl.py status` confirms stopped frontend and backend.
The worktree remains at `.worktrees/feature-content-storage-compression` on
`feature/content-storage-compression`. The actual database and index remain intact.
No restart, merge, push, dependency installation or migration occurs.
The next runtime start remains a separate user-requested operation.
