# Session-local content maintenance validation

Date: 2026-10-17. Worktree: `feature-content-storage-compression`.
Implementation baseline at Task 8 start: `6b1e76bb`.

## Validation boundary

The tests use isolated pytest databases. The benchmarks modify disposable SQLite databases only.
The actual-data source is explicitly `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression/db/data.sqlite`.
SQLite opens this source with `mode=ro` and creates one backup in `/tmp/twicc-task8-source/benchmark.sqlite`.
No service restart, current database mutation, manual migration, dependency installation, or full compute reset occurs.

The actual snapshot contains existing misplaced plaintext. Its workload differs from fresh compute output.
The controller accepts synthetic conformance and both-provider compute tests instead of a full fresh compute on the 24 GB copy.
The copy remains supplemental evidence for real metadata discovery, existing candidates, and selected publications.
No full-copy normalization or second source backup occurs.

## Implementation and integration corrections

- `ingestion.py` indexes prepared records by text once before processing insertion batches.
- The regression uses 151 records and four batches. Visits fall from 604 to 151.
- The two provider cases compute hot roots, cold children, and cold grandchildren using their own lifecycle.
- Shared hot-owner content stays plaintext. Cold tiny content records no gain. Cold exclusive content becomes zstd.
- Subsequent real periodic maintenance prepares zero candidates, loads zero payloads, and requests zero background writer grants.
- A changed owner rejects only its candidate. Foreground transactions commit between successful compression slices.
- Foreground arrival during ingestion preparation commits first, including cancellation of that preparation await.
- Real periodic recovery pauses for compute, accepts an explicit foreground upgrade, and retains admitted publication through cancellation.
- Cancellation summaries include completed writes. Caches, foreground observers, demands, requests, and leases are released afterwards.

The broad suite identifies compatibility failures from Tasks 2, 4, and 6. Task 8 corrects their tests:

| Tests | Failure | Correction |
|---|---|---|
| `test_compute_continuation.py`, 2 cases | Wrapper rejects the new `session_cache` keyword | Forward `**kwargs` |
| `test_codex_compute_recovery.py`, 2 cases | Monkeypatch targets removed `pending_root_page` | Use `pending_session_page` |
| `test_compute_publication_integration.py`, 6 cases | Same removed discovery function | Use `pending_session_page`; retain original compute-cleanup assertions |
| `test_content_cold_cache.py`, 1 case | Old placement fixture lacks `session_final_fields` | Exercise committed shared-owner lookup with current `active_session_ids`; retain first-read=1/repeated-read=0 |
| `test_mcp_tools.py`, 1 case | Description assertion requires a hot native root | Require the hot requested session |

Three `SIM117` lint findings in dictionary-cache and storage-write tests also receive equivalent combined-context syntax.
No production behavior changes accompany these test corrections.

## Tests and freshness

Every shell command runs from the worktree. Python/Django commands set `TWICC_DATA_DIR=$PWD`.
Complete command output remains in `/tmp/twicc-task8-logs/`.

### Broad suite

```bash
TWICC_DATA_DIR=$PWD uv run pytest -q
```

Collected: **11,033**. Result: **12 failed, 10,993 passed, 28 skipped, 3 warnings in 663.83 seconds**.
Raw output: `/tmp/twicc-task8-logs/full-pytest.txt`.
All 12 failures correspond to the compatibility corrections above. No unrelated failing assertion remains unresolved.

The suite starts before Task 8 adds its tests and changes ingestion indexing.
It collects the old wrappers and assertions before their correction.
This is broad evidence from the initial execution, supplemented by current affected-module checks.
It is not a claim that a second complete suite runs against the final tree.
The controller explicitly accepts focused verification for these changes instead of another 11,000-test run.

The three pytest warnings concern unawaited test coroutines:

- `tests/test_codex_sdk_wrappers.py:133`: `AsyncMockMixin._execute_mock_call`.
- `tests/test_codex_sdk_wrappers.py:152`: `AsyncMockMixin._execute_mock_call`.
- `tests/shell_notice_helpers.py:31`, from `test_turn_openings_restart_the_delay`: `BaseAgent._notify_state_change`.

These SDK/shell-notice test paths are outside this change. They remain separate from the expected uv environment warning.
The inherited main-repository `VIRTUAL_ENV` warning states that uv ignores it and uses this worktree's `.venv`.

### Current affected modules

```bash
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_end_to_end.py tests/test_content_ingestion.py tests/test_writer_notifications.py tests/test_compute_continuation.py tests/test_compute_selection_wakeups.py tests/test_content_maintenance_compute_gate.py -q
```

Result: **144 passed in 32.99 seconds**. Output: `focused-final-green.txt`.
The pre-correction run records **2 failed, 142 passed** with the incompatible wrapper.

```bash
TWICC_DATA_DIR=$PWD uv run pytest tests/test_codex_compute_recovery.py tests/test_compute_publication_integration.py tests/test_mcp_tools.py -q
```

Result: **77 passed in 68.81 seconds**. Output: `full-failures-green.txt`.
The pre-correction run records **9 failed, 68 passed in 63.45 seconds**.

```bash
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_cold_cache.py -q
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_dictionary_cache.py tests/test_content_storage_writes.py -q
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_end_to_end.py -q
```

Results: **21 passed in 5.47 seconds**, **19 passed in 5.49 seconds**, and **9 passed in 5.48 seconds**.
The last command strengthens fresh-compute assertions to require completion for every session.
It also checks source-backup immutability and unsafe destination rejection.

### RED/GREEN evidence

The prepared-map regression fails before its production correction: `604 != 151`.
The first new end-to-end run also rejects two fixture assumptions about a tiny value shared with a hot owner.
Those values correctly have no cold no-gain attempt. Distinct tiny values correct the fixtures.
The accepted compute-to-zero-work tests then pass without changing the zero-work requirement.
Output files: `end-to-end-red.txt`, `end-to-end-green.txt`, and `end-to-end-final.txt`.

### Static checks

```bash
TWICC_DATA_DIR=$PWD uvx ruff check src/twicc/core/content_storage src/twicc/content_maintenance_task.py tests/test_content_*.py scripts/benchmark_content_maintenance.py tests/test_compute_continuation.py
```

The requested paths pass. All 42 changed Python paths also receive an explicit check.
The exact additional file list is `/tmp/twicc-task8-logs/ruff-files.txt`; full output is `ruff-all-changed.txt`.
`git diff --check` passes.


## Task 8 review correction: output safety

Review identifies an important CLI defect: the output write can replace an existing database or an alias.
The correction validates output before backup, fixture construction, or Django configuration.
It rejects source/destination aliases, protected instance paths, and every existing output file.
Existing-file rejection covers hard links. Resolved-path checks cover symbolic links and directory aliases.
The final write uses exclusive `xb` creation on the validated canonical path.
A file created after validation therefore remains unchanged. A new output inside the disposable work directory still works.

```bash
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_end_to_end.py -q -k 'output_'
TWICC_DATA_DIR=$PWD uv run pytest tests/test_content_maintenance_end_to_end.py -q
uvx ruff check scripts/benchmark_content_maintenance.py tests/test_content_maintenance_end_to_end.py
```

RED: **6 failed, 9 deselected in 5.29 seconds**, before the guard exists.
GREEN: **16 passed in 6.28 seconds**, including seven new output tests and the nine original cases.
Tests cover source, destination, protected database, symlink, hardlink, concurrent file creation, and valid new output.
Alias rejection preserves original bytes and precedes execution.
Raw outputs: `output-safety-red.txt`, `output-safety-green.txt`, and `output-safety-final-green.txt` in the same log directory.
Ruff and `git diff --check` pass.

The earlier JSON `recovery.writer_grants=0` is a structural assertion, not an instrumented grant counter.
Recovery discovery calls no writer acquisition API. The updated script states this as `writer_grants_structural_assertion`.
The live periodic end-to-end tests independently instrument coordinator grants for their zero-work assertions.
The metadata-inspection zero-grant statement is also structural; no writer acquisition occurs in that function.

These corrections do not change the measured maintenance interval. Recorded benchmark trials remain applicable.
The controller starts another broad suite from `46dfb490` before this correction.
That run does not collect these seven new tests. Current focused checks cover the correction.
The three earlier warnings remain attributed to the unchanged SDK and shell-notice test paths above.

## Benchmark method

`scripts/benchmark_content_maintenance.py` requires an empty work directory.
It refuses instance data directories and source/destination overlap, including resolved aliases.
It configures Django explicitly and creates synthetic model schemas without migration commands.
It never imports the running instance's settings.

Final runs use production SQLite settings: **WAL**, **synchronous=NORMAL**, a 128 MiB mmap limit, and normal checkpoints.
Preparation and recovery use a dedicated codec executor. Publication uses the existing compute/writer executor.
Actual `prepare_compression_batch`, `_publish`, `_apply`, `WriterCoordinator`, and `MaintenanceMetrics` paths run.
Synthetic fixtures use the bundled public Codex dictionary, verified against its manifest digest and length.
Its SHA-256 is `e09a9e649bbf941e0b7a5a19dd1aeeefa8bd497ffa26dc35f7e28d5f86011fdd`.

The corrected runner executes each synthetic command serially:

```bash
TWICC_DATA_DIR=$PWD uv run python scripts/benchmark_content_maintenance.py --scenario conformant --work-dir /tmp/twicc-task8-corrected-conformant --output /tmp/twicc-task8-logs/corrected-conformant.json
TWICC_DATA_DIR=$PWD uv run python scripts/benchmark_content_maintenance.py --scenario compress --work-dir /tmp/twicc-task8-corrected-compress --output /tmp/twicc-task8-logs/corrected-compress.json
TWICC_DATA_DIR=$PWD uv run python scripts/benchmark_content_maintenance.py --scenario foreground --work-dir /tmp/twicc-task8-corrected-foreground --output /tmp/twicc-task8-logs/corrected-foreground.json
TWICC_DATA_DIR=$PWD uv run python scripts/benchmark_content_maintenance.py --scenario acquisition --work-dir /tmp/twicc-task8-corrected-acquisition --output /tmp/twicc-task8-logs/corrected-acquisition.json
```

These directories must not be reused for a new CLI invocation.
The runner is `/tmp/twicc-task8-logs/run-corrected-benchmarks.py`.
For actual-copy measurement, it imports the script, calls `configure()` on the existing single backup, then calls `run_pass(source=True)`.
This avoids a second 24 GB backup.

Each synthetic scenario has three disk-backed trials.
The first measured pass can already have warm fixture pages. Subsequent trials explicitly reuse the same database.
Compression fixtures restore eligible plaintext outside measured intervals before each repeat.
No operating-system cache flush or isolated-machine claim is made.

Foreground jobs change a project name on every transaction. Each sample starts before requesting writer resources and ends after commit.
These are real writer transactions. HTTP read latency is not used as a substitute.
CPU and `/proc/self/io` values are interval deltas around each measured pass, excluding fixture construction.
SQL counters separate profiling statements. Hold timings include query-plan instrumentation overhead.
Registry initialization runs before each measured interval and has a separate duration.
This matches `cli/run.py:191` importing ASGI before maintenance starts at line 331.
ASGI imports the registry and provider managers at module line 32.
Percentiles for maintenance holds use the bounded production histogram's upper bounds.
Foreground percentiles use individual request-to-commit samples.

The acquisition scenario retains both actual `SessionItem` content FKs without indexes.
It compares 100,000 and 1,000,000 unrelated children, with three publications at each scale.
`EXPLAIN` checks publication opcodes and table root pages; `EXPLAIN QUERY PLAN` checks candidate lookup.
This catches FK child work that `EXPLAIN QUERY PLAN` alone can omit.

## Measured results

Final synthetic output: `/tmp/twicc-task8-logs/corrected-{scenario}.json`.
All trials use WAL/NORMAL. Trial 1 includes the first measured pass after application registry initialization.
Trials 2 and 3 reuse warm databases. Registry initialization takes about 1.4 seconds in the first process.

| Scenario/trial | Pass ms | Prepare ms | Resource wait ms | Transaction ms | Max hold ms | Hold p50/p95/p99 upper ms | CPU s | Read/write bytes |
|---|---:|---:|---:|---:|---:|---|---:|---|
| conformant/1 | 2680.8 | 2573.9 | 0.00 | 0.00 | 0.00 | 0/0/0 | 2.641 | 29614080/0 |
| conformant/2 | 2902.8 | 2808.4 | 0.00 | 0.00 | 0.00 | 0/0/0 | 2.888 | 442368/0 |
| conformant/3 | 3203.9 | 3103.0 | 0.00 | 0.00 | 0.00 | 0/0/0 | 3.195 | 0/0 |
| compress/1 | 612.6 | 483.6 | 0.13 | 60.34 | 10.92 | 5/25/25 | 0.606 | 16384/299008 |
| compress/2 | 671.2 | 498.8 | 0.12 | 74.85 | 17.18 | 10/25/25 | 0.657 | 0/344064 |
| compress/3 | 668.8 | 512.1 | 0.15 | 78.20 | 17.09 | 10/25/25 | 0.659 | 0/335872 |
| foreground/1 | 676.8 | 525.8 | 10.92 | 60.29 | 10.82 | 5/25/25 | 0.878 | 0/1462272 |
| foreground/2 | 730.5 | 520.8 | 58.20 | 61.09 | 11.03 | 5/25/25 | 0.969 | 0/1466368 |
| foreground/3 | 687.6 | 523.1 | 20.96 | 54.93 | 7.42 | 5/10/10 | 0.902 | 0/1490944 |

The conformant fixture contains 100,001 contents: 50,000 zstd, 50,000 current-policy no-gain, and one hot shared plaintext.
Each pass prepares and changes zero entries, loads zero payload bytes, and grants zero background writer leases.
Compression selection executes 2,004/2,003/2,003 SQL statements. It does not select payloads.
The pass cache loads 109 session IDs in one grouped query. It records 101 hits and 101 recovery reference probes.
Recovery separately takes 88.20/73.73/77.58 ms with 206 SQL statements each.
Its zero writer-grant value in saved JSON is the structural assertion clarified above.
Recovery identifies zero targets, reads zero payloads, and takes zero writer grants.
The large hot owner has 100,000 items. Another 100 hot children already have plaintext references.

Each compress/foreground trial scans 25 contents and prepares 21 eligible entries.
Four entries have ineligible owners. Each trial publishes 21 changes through 12 bounded publication batches.
The fixture includes 1 KiB, 64 KiB, 1 MiB, and 16 MiB contents with shared and mixed owners.
Synthetic strings repeat short phrases and compress unusually well. These results do not predict real-data compression ratios.
Payload loads total 106,303,488 bytes; stored candidates total 10,518 bytes; saved bytes total 106,292,970.
SQL counts are 97/96/96 with 64/63/63 SELECTs and 12 payload SELECTs; 43 profiling statements remain separate.
The pass cache loads 109 IDs once, records 173 hits, and needs no invalidation reload.

| Foreground trial | Commits | Request→commit p50/p95/p99/max ms | Coordinator wait total/max ms |
|---|---:|---|---|
| 1 | 141 | 1.49/8.06/12.02/12.51 | 50.26/8.68 |
| 2 | 136 | 1.48/8.42/39.84/52.81 | 52.60/9.54 |
| 3 | 140 | 1.37/8.21/10.25/32.96 | 44.20/6.09 |

### Unindexed-FK acquisition

| Unrelated children/trial | Pass ms | Prepare ms | Resource wait ms | Transaction ms | Max hold ms | CPU s | Read/write bytes |
|---|---:|---:|---:|---:|---:|---:|---|
| 100000/1 | 104.97 | 6.02 | 0.02 | 8.96 | 9.81 | 0.100 | 0/16384 |
| 100000/2 | 137.63 | 4.80 | 0.02 | 3.33 | 4.00 | 0.137 | 0/8192 |
| 100000/3 | 87.04 | 3.90 | 0.01 | 2.81 | 3.42 | 0.087 | 0/8192 |
| 1000000/1 | 557.78 | 5.90 | 0.01 | 5.55 | 6.56 | 0.530 | 9699328/8192 |
| 1000000/2 | 585.46 | 4.90 | 0.01 | 3.70 | 4.32 | 0.584 | 4096/8192 |
| 1000000/3 | 538.39 | 4.72 | 0.01 | 3.41 | 4.04 | 0.537 | 0/8192 |

Each trial prepares and changes three contents. Publication takes one batch and loads 384 payload bytes.
Publication SQL counts are 10/9/9 at 100,000 children and 9/9/9 at 1,000,000 children.
The extra first SQL initializes cached dictionary metadata. Each publication has seven separate profiling statements.
All candidate lookups use `SEARCH core_sessionitemcontent USING INTEGER PRIMARY KEY (rowid=?)`.
Every publication plan reports `publication_opens_child_table=false`.
`FkCounter` remains present for the changed `dictionary_id` parent constraint. It does not open the SessionItem child table.
Both actual content FK columns remain unindexed. The 10× child count does not increase publication hold.
Total pass time increases because the separately measured hot recovery discovery probes the large owner.

### Existing-source snapshot

Accepted output: `/tmp/twicc-task8-logs/corrected-source.json`.
The one backup contains **3,963,395 content rows**. This is existing data with a large misplaced-plaintext backlog.
It is not a conformant full maintenance pass and receives no full fresh compute.

| Full metadata/eligibility inspection | Result |
|---|---:|
| Elapsed | 460,914.01 ms, about 7 minutes 41 seconds |
| CPU interval | 150.379 seconds |
| SQL / session metadata SQL | 68,817 / 7,783 |
| Cache IDs loaded / hits / grouped queries | 10,485 / 20,161 / 7,783 |
| Payload SELECTs / payload codec work / writer grants | 0 / 0 / 0 |
| Physical read / write bytes | 18,482,053,120 / 0 |
| Logical read bytes | 17,725,326,484 |
| Raw / stored content bytes | 19,736,962,653 / 14,026,843,762 |
| Eligible / ineligible owner rows | 869,710 / 1,101,036 |
| Already compressed / current no-gain rows | 1,992,648 / 1 |

Metadata-only application reads still cause **18.5 GB of physical I/O** on this layout.
This genuine limitation explains the long inspection. It does not hold writer resources.
The scan uses 100-row metadata pages and grouped missing-owner reads, not one session query per content.
No source-copy normalization, backlog compression, or second full inspection occurs.

The following real API work prepares only the first 20 eligible rows from that inspection.
It is a small, non-stratified sample: 42,095 raw bytes total, or 2,104.75 bytes per row on average.
Per-row size distribution is not retained. This sample does not establish large actual-payload hold behavior.
The synthetic 16 MiB cases remain separate evidence and use highly repetitive public data.

| Selected real candidates | Result |
|---|---:|
| Prepared / changed / publication batches | 20 / 20 / 20 |
| Payload bytes loaded / stored / saved | 42,095 / 20,084 / 22,011 |
| Preparation / resource wait / transaction total ms | 67.61 / 0.15 / 45.33 |
| Writer hold total / maximum ms | 53.32 / 9.27 |
| Hold p50 / p95 / p99 histogram upper ms | 5 / 5 / 10 |
| Holds at least one second | 0 |
| SQL / SELECT / payload SELECT / separate profiling SQL | 140 / 100 / 20 / 41 |
| Candidate-phase CPU interval | about 0.123 seconds |
| Candidate-phase physical read / write bytes | 102,400 / 331,776 |

Candidate-phase CPU and I/O subtract the inspection interval from the encompassing pass interval.
The full source interval takes 461,047.31 ms and 150.502 CPU seconds.
Registry initialization takes 2,099.79 ms separately. Startup also checkpoints the exploratory copy's old WAL outside this interval.
Every captured publication uses the content primary key and avoids the SessionItem child table.
No foreground jobs run against this source copy. The synthetic foreground scenario supplies request-to-commit evidence.

All accepted measured publication holds stay below one second.
The largest accepted synthetic hold is 17.18 ms; the largest selected actual-copy hold is 9.27 ms.
This evidence applies to the recorded workloads and does not prove bounds for every remaining actual candidate.

### Rejected exploratory measurements

Early exploratory runs overlap pytest or backup, use DELETE/FULL, or share codec and writer executors.
They show writer holds above one second and do not establish acceptance.
A later WAL/NORMAL run still shows first holds of 1,341.52 ms and 1,245.64 ms.
A fresh-process profile identifies the cause: the harness omits application startup initialization.
Its first activity wrapper takes 3,069 ms under profiling; registry imports take 3,060 ms.
SDK imports build Pydantic models synchronously and block the event loop before foreground requests can run.
This explains low coordinator waits during the uninitialized first hold.
Application startup imports the registry before it creates maintenance, as the call chain above establishes.
The corrected harness performs that same initialization before measurement and records its separate duration.
Rejected outputs remain in `compress.json`, `foreground.json`, `final-compress.json`, and `final-foreground.json`.
The profile remains in `profile-first-publication.txt`. No first measured publication is silently discarded.
The exploratory actual-copy recovery takes 231,050 ms without writer ownership.
The old exploratory source driver unnecessarily prepares its backlog after its publication limit.
The controller stops that diagnostic process and approves bounded selected-candidate measurement.

## Specification coverage

| Spec section | Tasks | Recorded checks |
|---|---|---|
| 3.1: own dates, flags, 15/30-day thresholds, active and missing owners | 2, 5, 6 | `test_content_cold_policy.py`, `test_content_initial_compression.py`, `test_content_session_promotion.py` |
| 3.2: every shared owner; hot and unknown owners prevent compression | 2, 4, 5, 6 | Policy/read/publication tests; shared hot-owner fresh-compute end-to-end cases |
| 3.3: model, compute, lifecycle, contains, targeted resume, requested-content reads | 2, 5, 6 | `test_content_contains_gate.py`, `test_content_lifecycle.py`, promotion/readers tests; current MCP description assertion |
| 4: final own-session metadata, independent child completion, reconciliation, final validation, no-gain | 5 | Initial-compression/ingestion/extraction tests; both-provider completed-compute zero-work passes |
| 5.1: one pass cache, grouped missing IDs, dynamic time/activity, close | 3, 4, 7 | `test_content_maintenance_cache.py`, reads/metrics tests; conformant repeated-owner benchmark |
| 5.2: committed invalidation, rollback, in-flight fill fencing, specific active owners | 3, 5, 6 | Maintenance-cache/cold-cache/lifecycle/history tests; ingestion foreground-arrival and cancellation |
| 5.3: changed owner only, new hot owner, no redundant update | 4, 6 | Maintenance-read tests; changed-owner foreground-slice end-to-end case |
| 6.1: 100 rows/8 MiB, one oversized row, metadata-first, no conformant payload load | 4 | Maintenance-read/budget tests; 100,001-row conformant and 1 KiB–16 MiB candidate benchmarks |
| 6.2: candidate-only metadata validation, cache reuse, no payload reread, useful writes only | 4, 7 | Read/publication tests; actual `_publish` query plans and metrics |
| 6.3: scoped BEGIN IMMEDIATE, no PK self-update/FK scan, callbacks/nesting/cancel drain | 1, 6, 7 | `test_content_write_transaction.py`, storage admission tests; two-scale unindexed-FK benchmark; cancellation drain end-to-end case |
| 7: targeted hot child restoration at every depth, process-loss recovery, shared references | 6 | Session-promotion/restart tests; real periodic-recovery upgrade/cancel test; metadata-only hot discovery benchmark |
| 8: 30 minutes/24 hours, compute/source gates, cursor retention, progress/cache/volume/timing outcomes | 7 | Maintenance-compute-gate/metrics tests; periodic target pause/upgrade test; interval benchmark counters |
| 9.1–9.3: policy, concurrency, SQL and scheduling acceptance cases | 1–7 | Tests above plus current affected-module reruns |
| 9.4: fresh-compute zero work, metadata/candidate disk workloads, actual foreground writes | 8 | Nine end-to-end tests; serial synthetic and actual-copy measurements |

## Retained-cache invalidation inventory

The maintenance cache retains exactly `id` and `COLD_FIELDS`.
Those fields are `archived`, `hidden`, `created_at`, `last_started_at`, `last_updated_at`, and `last_stopped_at`.
It retains no hot/cold boolean, ancestry, root compute readiness, content payload, or representation proof.
Current time and specific runtime activity are evaluated for each decision.

| Retained data / entry point | Invalidation or freshness path | Evidence |
|---|---|---|
| `archived`, `hidden` through UI/CLI/services | `lifecycle_change` → committed `schedule_cold_invalidation(..., lifecycle_session_ids=changed)` | Warm maintenance-cache and real flag-service tests |
| Four lifecycle timestamps through watcher, ingestion, and compute | Same lifecycle hook; actual changed session IDs invalidate independently from structural roots | Maintenance-cache entry-point and lifecycle/history tests |
| Start/resume/stop manager activity | Immediate specific active-ID snapshot; committed lifecycle invalidation for persisted dates | Actual registration/admission and hot-child restoration tests |
| Session creation/deletion/replacement and database identity | Existing explicit mapping invalidation, reset generation, and connection identity fencing | Watcher creation, deletion, identity replacement, and reset tests |
| Reference-only epochs and representation updates | Do not evict unchanged lifecycle fields | Reference-only ingestion/replacement cache tests |
| Transaction-local writes, rollback, deferred fills | Pending local invalidation is conservative; committed callbacks fence fills; rollback cannot install new metadata | Atomic/savepoint/in-flight/reset tests |
| Unrelated session invalidation | Per-owner generations retain unrelated loads and deferred fills | Task 3 executing/deferred-fill regression cases |
| Pass completion/failure/cancellation | `close()` unregisters and clears; observers unsubscribe after admitted publication drains | Metrics tests and real periodic upgrade/cancel test |

## Limitations and review handoff

- No real-instance reset or full fresh compute runs. The actual copy is existing data, including misplaced plaintext.
- The first measured cache state is not guaranteed cold. Warm repeats and physical I/O deltas remain explicit.
- Existing backend processes remain active. Final measurements avoid the full pytest process and backup/earlier benchmark jobs.
- The script measures selection and bounded publications directly. Scheduler lifecycle, restoration, compute pauses, and cancellation use real integration tests.
- The source copy performs one full metadata/eligibility inspection, then prepares and publishes 20 selected candidates.
- The actual sample is small and not stratified. Large actual candidate holds remain unmeasured.
- That inspection uses production metadata fields, policy/cache decisions, and owner eligibility. It performs no backlog codec work.
- Actual-copy recovery remains exploratory evidence only. Its 231,050 ms discovery time holds no writer resource.
- The one copy can contain up to 20 publications from the exploratory run before the final interval.
- Independent whole-change review belongs to the controller after this task. This report does not substitute for that review.
- The user controls any subsequent `devctl.py` restart or reset/full-compute request.

## Final combined review correction

The correction reads selected payloads and their representation metadata in the same SQL result.
Preparation skips candidates whose representation metadata changed after initial selection.
It keeps the original owner observation for publication validation and does not duplicate owner-list reads.
The unchanged peer still publishes when another candidate changes representation.
A payload that changes without matching representation metadata still raises `ContentIntegrityError`.
The metadata-first selection, row/byte budgets, and one-oversized-row behavior remain unchanged.
Publication does not reread payloads.

Recovery now reports discovery progress through the existing ten-second metrics throttle.
Startup uses the `plaintext_recovery_discovery` operation kind with start and terminal events.
Periodic discovery uses the existing periodic operation and keeps its compression cursor and counters.
Summaries expose `discovery_cursor`, `discovery_pages`, `discovery_sessions`, `recovery_reference_probes`, and `discovery_preparation_ms`.
Discovery preparation also contributes to `preparation_ms`.
These counters use existing page results, cache statistics, and clocks, without added SQL or writer requests.

Fresh correction evidence:

- RED: both plain-to-compressed and compressed-to-plain concurrent changes raise false integrity errors before the correction.
- RED: all three conformant-hot discovery cases fail because startup operation events are absent.
- GREEN: the six affected modules below pass: **175 passed in 11.50s**.
- The three new discovery cases use multiple one-session pages and eleven seconds of fake elapsed time per probe page.
- They cover startup, periodic discovery, and stop during each phase, with no payload reads or writer grants.
- Existing E2E cancellation coverage verifies admitted publication draining and cache/observer cleanup.
- Scoped ruff passes; `git diff --check` passes.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uv run pytest -q tests/test_content_maintenance_reads.py tests/test_content_maintenance_metrics.py tests/test_content_maintenance_compute_gate.py tests/test_content_maintenance.py tests/test_content_maintenance_end_to_end.py tests/test_content_session_promotion.py --tb=short
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && TWICC_DATA_DIR=$PWD uvx ruff check src/twicc/content_maintenance_task.py src/twicc/core/content_storage/maintenance_metrics.py tests/test_content_maintenance_reads.py tests/test_content_maintenance_metrics.py tests/test_content_maintenance_end_to_end.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && git diff --check
```

These are fresh scoped results for the correction.
Earlier full-suite and benchmark results precede this correction and do not validate its exact final tree.
The controller owns the running full suite and the final scoped re-review.
No service, current database, migration, dependency, or benchmark operation runs for this correction.
