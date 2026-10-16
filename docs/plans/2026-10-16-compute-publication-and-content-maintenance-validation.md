# Compute publication and content maintenance validation

Date: 2026-10-16. Final measured candidate: `6101e880074c8a63c34b293b3791e00b8cd9558c`. Baseline: `307cd0ba`.
Lifecycle and maintenance use `d3b3587e76e4dc93cc6c14cb33eb7f06d14bfc6e`.
Corrected validation uses `2e49f55ce2a52307450067ece039007eeffb8213`.
Corrected facts, mixed, and dispatch use `6101e880`.

This document records Task 8 of the [implementation plan](2026-10-16-compute-publication-and-content-maintenance-plan.md).
The [specification](2026-10-16-compute-publication-and-content-maintenance-spec.md) defines the acceptance criteria.

## Measurement status

The controller's scoped reviews approve structural corrections `2e49f55c` and `6101e880` without findings.
All six scenarios complete. Corrected validation, matched facts, bounded mixed, and dispatch results replace affected diagnostic evidence.
Completed lifecycle and maintenance cases remain valid.
Before-correction validation, facts, and candidate mixed cases remain diagnostic evidence only.

| Retained production-budget comparison | Baseline maximum, ms | Candidate maximum, ms | Ownership metric |
| --- | --- | --- | --- |
| Lifecycle, all inputs | 9,947.20 | 27.71 | SQLite span after first-write completion |
| Facts, all inputs | 718.56 | 48.65 | SQLite span after first-write completion |
| Validation, all inputs/caches | 203.84 | 27.14 | SQLite span after first-write completion |
| Maintenance, normal inputs | 427.13 | 46.77 | SQLite span after first-write completion |
| Mixed, all measured repetitions | 243.25 | 54.74 | Actual coordinator lease hold |

The oversized maintenance candidate reaches 275.76 ms. Its COMMIT investigation appears below.
The specification targets normal slices below 100 ms and requires investigation above 250 ms.
A repeatable one-second changed-phase hold fails acceptance. The retained candidate shows no such hold in these measurements.
The oversized maintenance exception above 250 ms remains an investigated limitation.
These are phase-specific synthetic measurements. They do not establish full-dataset end-to-end gains.

Full-dataset end-to-end gains remain **unmeasured**. No server reset, server start, migration, dependency change, or instance database access occurs.
An operational run requires the user's separate instruction.

## Isolation and environment

All commands start in `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.
The existing `.venv/bin/python` runs both source versions. No editable installation changes occur.
The baseline comes from `git archive 307cd0ba`, extracted under `/tmp/twicc-publication-bench-20261016/baseline`.

The driver sets `TWICC_DATA_DIR` before importing TwiCC or Django.
It inserts the selected source directory into `sys.path` and asserts the exact imported `twicc.__file__`.
It redirects the Django database before model queries and asserts containment under its temporary output directory.

The driver creates managed model tables and indexes with `schema_editor`. It runs no migrations.
Both variants have schema SHA-256 `72316503bc6a1e3b3d1298eaec53528ed3b85b9a5c7b80bddd97cc28ac6f9e92`.
All data and search paths stay under the temporary benchmark root.

| Property | Value |
| --- | --- |
| CPU | Intel Core i7-10510U, 1.80 GHz nominal, 4 cores, 8 logical CPUs |
| Operating system | Linux 7.0.0-31-generic x86_64, glibc 2.43 |
| Python | 3.13.14, Clang 22.1.3 |
| SQLite | 3.53.1 |
| Storage | `/tmp` on `/dev/nvme0n1p3`, ext4 family; approximately 59 GiB free during the run |
| Django DEBUG | False |
| Journal | WAL |
| Synchronous | FULL (`2`), including reopened connections and worker connections |
| Page size | 4,096 bytes |
| WAL autocheckpoint | 1,000 pages |
| SQLite cache size | 2,000 pages |
| mmap size | 134,217,728 bytes |
| Journal size limit | 27,103,364 bytes |
| Busy timeout | 30,000 ms; independent contention probe uses 120 seconds |
| Data seed | 20261016 |

The variants run serially on the same shared machine. CPU frequency and other machine activity remain uncontrolled.
No CPU affinity, governor change, OS cache flush, or disk isolation occurs.
These results characterize this synthetic CPU and I/O workload. They give no production latency guarantee.

Input identifiers, content generation, owner graphs, and row counts match across comparable cases.
Temporary project paths and runtime timestamps differ between variants.
WAL/FULL settings match across variants; FULL is stronger than the application's default NORMAL setting.

## Timing definitions

All durations use `time.perf_counter`. Distributions report milliseconds.
Dispatch CPU uses `time.process_time` for the whole benchmark process, including worker threads, heartbeat, and instrumentation.
It is not isolated writer CPU. Block wall/CPU end at operation completion, before producer-stop cleanup.
Percentiles use sorted sample index `floor((n - 1) * p)`. Small sample counts limit tail estimates.
Threshold counts use strict comparisons: `>100`, `>250`, `>500`, and `>1000` ms.

| Metric | Measurement boundary |
| --- | --- |
| SQLite writer span | Completion of the first successful write through completion of COMMIT |
| First write acquisition and guard | First write execution, including SQLite lock acquisition |
| Transaction | BEGIN execution start through completion of COMMIT |
| Owned transaction | Outer unit helper duration, including initial write acquisition |
| Actual coordinator lease hold | Existing `Compute grant:` diagnostic, from application lease acquisition through release |
| Preparation | Real preparation helper execution, outside the coordinator lease |
| Dispatch gap | Preparation worker completion through publication worker start |
| Executor queue | Worker submission through worker entry |
| Foreground SQLite probe | Independent connection's BEGIN IMMEDIATE acquisition or committed write completion |
| Queued foreground completion | Real writer foreground-job enqueue through completion |
| Watcher append to commit | Independent JSONL append completion through the real transaction's on-commit callback |
| Watcher append to notification | Append completion through the actual outgoing `session_items_added` application envelope |
| Event-loop lag | Excess delay of a 5 ms asyncio heartbeat |

The SQLite writer span excludes the first write's execution. It is a lower bound for SQLite write ownership.
It is **not** the application coordinator lease hold. The acquisition/guard metric reports the excluded write separately.
The runtime mixed and dispatch scenarios capture actual coordinator lease diagnostics separately.

The independent SQLite probe writes a dedicated table every 10 ms. It has no application queue priority.
It can wait across consecutive background slices. Its latency does not measure the writer coordinator's foreground priority.
Each repetition drains any active probe before the next repetition starts.
The probe sets `synchronous=FULL` and a 120-second timeout. Its other connection settings retain SQLite defaults.
Main and application worker connections use the explicit cache, mmap, and journal settings listed above.

SQL counts include guard writes. `executemany` counts each executed statement.
COMMIT uses Django's connection method, so its timing sample count supplies the commit count.
Instrumentation runs identically in each variant; its cost remains in the measured samples.

## Sampling and input design

Normal cases use one excluded warmup and five complete measured repetitions.
Baseline atomic phases and candidate sliced phases have different publication sample counts by design.
Per-publication distributions and complete-operation cumulative distributions remain separate.
Imports and dictionary initialization precede measured publication. First-use operation results remain separate.
The dispatch test uses five blocks of 2,000 preparations per load mode: **10,000 total**, not five independent 10,000-preparation runs.
Its 20-preparation warmup remains excluded for each load mode.
Dispatch idle means no foreground job arrivals while background preparations continue.
The reported `idle_wakes` counter counts writer drain calls that process no work. It does not count OS interrupts or timer events.
No completely idle, no-background-work interval is measured.
Both variants retain exact per-block gaps. The driver asserts successful completion and exactly 2,000 measured preparations per block.

| Scenario | Inputs |
| --- | --- |
| Lifecycle | 10/100/400/1,000 children; unchanged, changed, mixed; populated current/original references and shared owners |
| Facts | 1k/10k/40k/75k facts; 128/256/1,000-row candidates; 256 KiB byte cap; varied payload lengths |
| Maintenance | 100 contents per case; shared/overlapping/distinct owners; total 333,000 bytes, total 8 MiB target, one 12 MiB oversized record |
| Validation | 1k/10k/100k/200k items; current and original slots; 512 distinct contents per source; warm/reopened connection |
| Dispatch | Real writer loop; serial worker preparations with cyclic 1–10 ms sleeps; idle and queued foreground load |
| Mixed | 400 children, 10,000 root items, 40,000 facts, queued foreground writes, independent real watcher, scheduled maintenance |

Lifecycle calls the actual old lifecycle loop and the actual progressive candidate helper.
The isolated unit phases omit full compute source-fence bookkeeping in both variants.
The mixed scenario measures the full production compute publication path.
Its compute message contains precomputed facts and stopped-agent evidence, with no item metadata update payload.
It does not measure the provider compute subprocess or full transcript parsing.
The independent watcher does ingest actual valid provider JSONL through its real live parsing path.

The validation setup runs real operation begin, counts, metadata, extraction, and reconciliation before the timed validation phase.
Those existing setup phases remain **excluded**, including their CPU and I/O cost.
The baseline runs its actual history staging and final helper; no scan approximation replaces the baseline.

Reopened tests close and reopen the connection before setup. They issue `PRAGMA shrink_memory` after setup, before timing.
This releases unpinned SQLite cache pages. It does not establish an OS cold cache, particularly with mmap enabled.
Results labeled reopened must not be described as OS cold-cache results.

Mixed watcher samples append valid deterministic Claude JSONL to an independent temporary session source.
They call real `process_path`, watcher enqueue, live sync, writer transaction, and application notification code.
Explicit `process_path` admission excludes OS watchfiles detection latency.
Only external delivery is stubbed after recording the actual outgoing application envelope.
The samples do not measure network, browser, phone, or push delivery.

## Calibration corrections

Directories named `quick-*`, `run-*`, `final-*`, and `verified-*` contain discarded calibration attempts.
They do not contribute to the audited tables.
Corrections drain foreground probes at repetition boundaries, include COMMIT completion, restore identical payloads, and preserve FULL after reopening.
Foreground probe starvation in discarded attempts remains diagnostic but does not contaminate the audited repetition samples.

The first mixed warmup uses unbounded 20 ms foreground and 30 ms watcher producers.
The baseline commits 1,857 watcher items but no lifecycle update after approximately three minutes.
Its 50 ms idle polling and foreground priority prevent sustained background admission under this constructed flood.
This does not establish starvation under normal application load. The warmup is interrupted and discarded.
Its temporary database and log remain under `discarded-unbounded-mixed-baseline*`.

The accepted mixed workload has exactly 200 foreground jobs and 134 watcher appends per repetition.
Their target deadlines use 20 ms and 30 ms offsets from repetition start.
Each producer permits one outstanding operation, then catches up to its next deadline.
Both variants process all counts. Actual arrival drift, compute completion, and full workload completion remain separate.

Dispatch foreground load uses one-second cycles. Each cycle schedules ten jobs at 20 ms offsets, then an 800 ms quiet window.
Both variants use this schedule. Actual job counts and arrival drift remain visible.
Dispatch gap partitions use preparation completion within the target burst or quiet interval.
These partitions describe target schedule windows, not measured foreground queue occupancy.
Dispatch foreground jobs repeat the same title value through the real `_RetireSessionsJob` and SQL UPDATE path.
They measure queue admission and SQL demand. Repeated identical values may avoid physical SQLite page changes; this is not directly instrumented.
Mixed foreground jobs use different `foreground-{i}` values and exercise changing title writes.

The first candidate facts matrix accumulates three source sessions per size, versus one in the baseline.
That matrix remains an auxiliary budget stress test. Its total unrelated database population differs.
The corrected 128/256-row candidate replays supply matched-population comparisons on `6101e880`.

## Validation structural defect before correction

The authoritative selected-ID metadata query chooses the `(session_id, line_num)` ordering index with only a session constraint.
Its `id IN (...)` condition filters a complete source index scan for every page.
Preparation correctly constrains both the session and line range.

Actual candidate SQL plans appear in `/tmp/twicc-publication-bench-20261016/queryplans.json`.
The diagnostic runs against the completed baseline temporary database, opened read-only, with identical schema and deterministic source population.
It does not open the active candidate database or warm that database's cache.
Plans reproduce at 100k/200k items, with 256/1,000 selected IDs, on first/middle/last pages.

| Warm source | Page samples | SQLite page p50/p95/p99/max, ms | Cumulative validation p50/p95/p99/max, ms |
| --- | --- | --- | --- |
| 100,000 items | 1,960 | 16.30 / 20.33 / 22.60 / 31.79 | 8,169.18 / 8,492.08 / 8,492.08 / 8,569.06 |
| 200,000 items | 3,915 | 26.54 / 31.97 / 34.65 / 48.30 | 24,199.05 / 24,298.17 / 24,298.17 / 25,370.96 |

Each row covers five complete measured validations. Existing setup remains excluded in both variants.
Baseline cumulative p50 is 112.08 ms at 100k and 190.66 ms at 200k.
Candidate publication SELECT time grows from 12,950.69 ms to 49,581.03 ms across these five repetitions.
The 100k publication COMMIT sum is only 31.30 ms across 1,960 pages.
Durable COMMIT cost does not explain this validation slowdown.

The proposed 1,000-row validation alternative does not run. A larger page must not conceal this structural defect.
The before-correction validation measurements cannot establish the bounded-query contract, despite their sub-100 ms page spans.
The following corrected results replace these diagnostic results for acceptance. Corrected runtime mixed results appear below.

The corrected actual-helper EXPLAIN records `SEARCH item USING INTEGER PRIMARY KEY (rowid=?)` for selected-ID publication.
It uses a temporary B-tree to order the bounded selected rows.
Preparation retains its session/line range index search.
Corrected evidence is `/tmp/twicc-publication-bench-20261016/queryplans-corrected.json`; original plans remain unchanged.

### Corrected validation scaling

| Source and cache | Page samples | SQLite page p50/p95/p99/max, ms | Cumulative validation p50/p95/p99/max, ms |
| --- | --- | --- | --- |
| 100k warm | 1,960 | 7.51 / 7.91 / 10.03 / 22.28 | 6,431.05 / 6,618.00 / 6,618.00 / 6,637.93 |
| 100k reopened | 1,960 | 7.49 / 7.91 / 12.93 / 21.58 | 5,982.70 / 6,059.83 / 6,059.83 / 7,268.45 |
| 200k warm | 3,915 | 7.48 / 7.88 / 9.14 / 22.22 | 12,361.19 / 12,479.82 / 12,479.82 / 12,656.78 |
| 200k reopened | 3,915 | 7.52 / 7.91 / 10.73 / 27.14 | 12,337.69 / 12,951.94 / 12,951.94 / 13,079.54 |

Doubling the source from 100k to 200k increases warm cumulative median by 1.92 times.
Preparation sums increase from 5,927.40 to 11,829.92 ms. SQLite publication span sums increase from 14,491.28 to 28,674.94 ms.
Publication SELECT execution increases from 1,468.16 to 2,942.49 ms. COMMIT sums increase from 39.33 to 78.14 ms.
These retained measurements support linear corrected work for these inputs. They do not prove bounds for all production inputs.

### Corrected validation protocol cost

The retained 100k warm case contains 1,960 publications across five validations.
Each page executes two guard UPDATEs and fourteen SELECTs. Preparation executes six SELECTs per page.
Source inspection confirms the outer publication guard and the inner validation guard both run.

| Corrected 100k warm metric | Sum across five validations, ms |
| --- | --- |
| Publication helper | 25,175.60 |
| SELECT execution | 1,468.16 |
| UPDATE execution | 9,583.80 |
| First write acquisition and guard execution, included in UPDATE | 9,523.97 |
| Second UPDATE execution | 59.84 |
| BEGIN execution | 12.89 |
| COMMIT | 39.33 |
| Unattributed helper work after subtracting SQL execution and COMMIT | 14,071.42 |
| SQLite span after first-write completion | 14,491.28 |
| Preparation helper | 5,927.40 |
| Preparation SELECT execution | 724.25 |

The first write includes contention from the independent SQLite probe.
Unattributed work includes ORM construction, result fetching, Python, and context management. It is not measured Python CPU time.
Exact wall time for each guard function is not separately instrumented.
The corrected protocol still repeats guards and ancestry/root checks for every page.
The cumulative cost remains visible even when the authoritative selected-ID SQL is bounded.

## Facts access-path defects before correction

The matched 128/256-row facts runs expose repeated owner scans in preparation and authoritative DELETE publication.
The persistent fact table has only `sqlite_autoindex_core_sessionhistoryfact_1` on `(session_id, kind, key, line_num)`.
Preparation requests `WHERE session_id=? AND id>? ORDER BY id LIMIT ?`.
Its plan constrains only `session_id`, then builds a temporary B-tree for ordering.
The primary-key cursor does not bound the chosen scan. Each page scans remaining owner facts.

Publication requests `DELETE ... WHERE session_id=? AND id IN (...)`.
Its plan also constrains only `session_id` through the business-key index.
Thus the selected-ID list does not bound that chosen authoritative writer scan.

Evidence files are `facts-prepare-queryplans.json` and `facts-publish-queryplans.json` under the temporary benchmark root.
These source-equivalent EXPLAIN statements use the completed baseline temporary database, opened read-only.
They perform no timed data query and do not access the active candidate database.
The controller reopens Task 5 and approves correction `6101e880` after scoped review.
Task 8 makes no production change for this defect.
The existing before-correction facts and candidate mixed results remain diagnostic. Corrected measurements determine the budget decision below.

The 75k/128-row run has 2,930 DELETE pages across five repetitions.
Preparation helper sum is 14,718.13 ms. SELECT p50/p99 is 4.61/10.49 ms.
Its cumulative median is 15,837.62 ms. The 256-row cumulative median is 8,389.73 ms.
This tradeoff cannot justify a larger budget that conceals repeated source scans.

The corrected actual-helper EXPLAIN records ordered business-index preparation with no temporary ordering tree.
Publication records `SEARCH core_sessionhistoryfact USING INTEGER PRIMARY KEY (rowid=?)`.
Evidence is `facts-queryplans-corrected.json`, covering both budgets and all four source sizes.
The diagnostic imports actual helpers, captures their SQL, and runs EXPLAIN without executing the data statement.
Its database is the completed baseline temporary database, opened read-only.

Both matched runs also have normal-case durable COMMIT outliers.
The 128-row DELETE maximum is 337.60 ms, with COMMIT 335.39 ms and DELETE SQL maximum 17.58 ms.
Two of 2,930 DELETE spans exceed 100 ms; one exceeds 250 ms.
The 256-row INSERT maximum is 210.13 ms, with COMMIT 206.72 ms and INSERT SQL maximum 5.66 ms.
One of 1,465 INSERT spans exceeds 100 ms; none exceed 250 ms.
These outliers remain visible. The measurements do not identify a specific WAL checkpoint or storage cause.

## Oversized maintenance investigation

All normal candidate maintenance cases stay below 100 ms. Their maximum SQLite writer span is 46.77 ms.
The oversized case contains one 12,582,912-byte record and 99 records of 100 bytes: 12,592,812 bytes total.
Oversized-record progress deliberately permits one record beyond the normal byte cap.

The overlapping-owner oversized case records ten publications across five repetitions.
Its SQLite span is p50 50.19, p95 272.37, p99 272.37, maximum 275.76 ms.
Threshold counts are 5/4/0/0 above 100/250/500/1,000 ms.

COMMIT alone reaches 239.67 ms. Its cumulative cost is 1,061.11 ms of 1,382.84 ms cumulative SQLite span.
The real publication helper reaches 38.83 ms, excluding COMMIT.
Maximum UPDATE execution is 28.04 ms; maximum SELECT execution is 3.39 ms.

These measurements locate most cost in durable COMMIT, after publication SQL finishes.
WAL checkpoint or storage behavior could explain this cost, but neither cause has direct instrumentation here.
The result does not establish a specific checkpoint cause.

The baseline's corresponding oversized maximum is 356.61 ms.
The candidate still exceeds the 250 ms investigation threshold, and this result remains visible.
Changing lifecycle, facts, or validation budgets cannot split this single maintenance payload.
The existing maintenance/content budgets therefore remain unchanged.

## Budget decision and throughput tradeoff

All production budgets remain unchanged after the corrected paired comparison.
Lifecycle remains 64 rows / 256 KiB. Facts remain 128 rows / 256 KiB. Validation remains 256 rows / 256 KiB.
Existing metadata remains 2,000 items. Existing content remains 256 rows / 8 MiB; maintenance remains 100 rows / 8 MiB.
The history reader's separate `_FACT_PAGE_SIZE=128` remains unchanged.

| Facts | Baseline atomic cumulative p50, ms | Corrected 128 rows, ms | Corrected 256 rows, ms |
| --- | --- | --- | --- |
| 1,000 | 9.15 | 147.50 | 53.53 |
| 10,000 | 82.63 | 1,215.22 | 730.14 |
| 40,000 | 329.52 | 5,769.26 | 3,097.41 |
| 75,000 | 590.10 | 11,068.42 | 5,799.31 |

The larger fact budget approximately halves cumulative work at 75k.
Across five 75k repetitions, SQLite publication span sums are 2,992.95 ms baseline, 43,443.97 ms at 128, and 22,537.80 ms at 256.
Commit counts are 5, 5,860, and 2,930 respectively. Commit sums are 1,034.69, 35,883.24, and 17,987.95 ms.
Slicing increases cumulative writer occupancy and durability overhead substantially. It provides foreground scheduling boundaries between those publications.
No cumulative compute speedup is claimed for the retained 128-row facts budget.

| Mixed repetition | Foreground p99, 128 rows, ms | Foreground p99, 256 rows, ms | Notification p99, 128 rows, ms | Notification p99, 256 rows, ms |
| --- | --- | --- | --- | --- |
| 1 | 69.21 | 73.69 | 80.48 | 91.15 |
| 2 | 73.66 | 68.49 | 88.24 | 79.70 |
| 3 | 62.07 | 69.35 | 89.10 | 82.97 |
| 4 | 59.86 | 72.55 | 77.48 | 85.06 |
| 5 | 64.79 | 80.00 | 84.33 | 89.95 |

Each mixed row contains 200 actual foreground jobs and 134 independent watcher appends.
The median of foreground per-repetition p99 increases from 64.79 to 72.55 ms at 256 rows.
Four corresponding repetitions have a higher foreground p99 at 256 rows.
Foreground maximum improves from 108.78 to 97.54 ms. Watcher notification maximum improves from 174.21 to 116.49 ms.
Notification per-repetition p99 medians remain similar: 84.33 versus 85.06 ms.
Actual coordinator lease maximum improves from 54.74 to 45.12 ms.
Full mixed completion median improves from 18,734.64 to 15,608.92 ms.

The comparison prioritizes foreground tail latency, so the final decision retains 128 rows.
Runs are serial and nonrandomized. These observations do not establish deterministic causation for the small foreground p99 difference.
The single 111.36 ms corrected 256-row INSERT span is dominated by COMMIT and remains visible.
It is not the sole reason to retain 128, and it does not establish a repeatable budget defect.
The 1,000-row auxiliary cases run before the facts access-path correction. They remain diagnostic and do not establish corrected 1,000-row performance.
No extra alternative or repeated full matrix runs follow the structural corrections.

## Mixed publication and maintenance limits

The baseline final coordinator lease reaches 243.25 ms in the matched mixed workload.
The retained corrected candidate's largest coordinator lease is 54.74 ms, in stopped-agent publication.
No changed mixed phase has a one-second hold. The retained corrected facts unit cases also remain below 100 ms.
The mixed baseline's foreground maximum is 103.98 ms; the candidate maximum is 108.78 ms.
Foreground per-repetition p99 ranges are 71.18–78.90 ms baseline and 59.86–73.66 ms candidate.
These distributions show the measured workload tradeoff. They do not guarantee every individual request improves.

The real scheduled maintenance task starts with only its initial delay changed to zero.
Its normal periodic interval remains unchanged. Every measured mixed repetition records zero compression preparations and zero source overlaps.
This demonstrates observed work suppression while source compute is pending.
It does not measure simultaneous compression CPU load, post-compute promotion latency, or a complete maintenance cycle under sustained live load.
The dedicated maintenance matrix measures actual compression preparation and publication separately.

## Dispatch results and block drift

Each load mode contains 10,000 serial preparations, divided into five measured blocks of 2,000.
The candidate foreground dispatch gap p99 is 7.047 ms, versus 76.465 ms baseline.
Idle p99 is 1.692 ms candidate, versus 50.556 ms baseline.
All dispatch gaps remain below 100 ms. No one-second dispatch delay occurs.

Foreground completion p99 is 9.074 ms candidate, versus 57.489 ms baseline.
The same burst schedule produces 817 candidate jobs and 5,592 baseline jobs because operation durations differ.
These are matched arrival rates and deadlines, not matched total foreground counts.
Actual arrival drift and target burst/quiet gap distributions appear in the aggregate table.

| Variant | Load | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | idle | dispatch_gap | 10000 | 45.543 | 50.111 | 50.556 | 51.789 | 0/0/0/0 |
| baseline | idle | preparation | 10000 | 5.386 | 10.158 | 10.273 | 13.116 | 0/0/0/0 |
| baseline | idle | dispatch_to_admitted_factory | 10000 | 45.088 | 49.637 | 50.028 | 51.132 | 0/0/0/0 |
| baseline | idle | event_loop_lag | 96576 | 0.385 | 1.014 | 1.216 | 4.763 | 0/0/0/0 |
| baseline | foreground | dispatch_gap | 10000 | 46.617 | 71.015 | 76.465 | 86.250 | 0/0/0/0 |
| baseline | foreground | preparation | 10000 | 6.019 | 10.161 | 10.272 | 10.883 | 0/0/0/0 |
| baseline | foreground | dispatch_to_admitted_factory | 10000 | 46.169 | 70.595 | 76.068 | 86.038 | 0/0/0/0 |
| baseline | foreground | foreground | 5592 | 6.573 | 54.900 | 57.489 | 61.504 | 0/0/0/0 |
| baseline | foreground | foreground_arrival_drift | 5592 | 8.533 | 35.334 | 38.194 | 42.017 | 0/0/0/0 |
| baseline | foreground | dispatch_gap_target_burst | 1564 | 65.715 | 76.730 | 78.375 | 86.250 | 0/0/0/0 |
| baseline | foreground | dispatch_gap_target_quiet | 8436 | 45.836 | 50.607 | 66.186 | 81.762 | 0/0/0/0 |
| baseline | foreground | event_loop_lag | 101805 | 0.391 | 1.091 | 1.309 | 3.916 | 0/0/0/0 |
| candidate | idle | dispatch_gap | 10000 | 1.223 | 1.533 | 1.692 | 3.964 | 0/0/0/0 |
| candidate | idle | preparation | 10000 | 5.390 | 10.146 | 10.250 | 11.075 | 0/0/0/0 |
| candidate | idle | dispatch_to_admitted_factory | 10000 | 0.779 | 1.001 | 1.123 | 3.214 | 0/0/0/0 |
| candidate | idle | event_loop_lag | 14020 | 0.500 | 1.098 | 1.243 | 2.948 | 0/0/0/0 |
| candidate | foreground | dispatch_gap | 10000 | 1.251 | 1.861 | 7.047 | 11.021 | 0/0/0/0 |
| candidate | foreground | preparation | 10000 | 6.056 | 10.147 | 10.232 | 13.906 | 0/0/0/0 |
| candidate | foreground | dispatch_to_admitted_factory | 10000 | 0.795 | 1.420 | 6.628 | 10.612 | 0/0/0/0 |
| candidate | foreground | foreground | 817 | 6.856 | 8.006 | 9.074 | 11.240 | 0/0/0/0 |
| candidate | foreground | foreground_arrival_drift | 817 | 0.585 | 1.202 | 1.296 | 1.543 | 0/0/0/0 |
| candidate | foreground | dispatch_gap_target_burst | 1845 | 1.224 | 7.074 | 8.284 | 11.021 | 0/0/0/0 |
| candidate | foreground | dispatch_gap_target_quiet | 8155 | 1.253 | 1.539 | 1.670 | 8.458 | 0/0/0/0 |
| candidate | foreground | event_loop_lag | 14446 | 0.510 | 1.148 | 1.305 | 2.384 | 0/0/0/0 |

Block CPU includes heartbeat, instrumentation, and worker threads. It does not isolate scheduling CPU.
No-work drain calls replace the ambiguous `idle_wakes` label. They do not measure OS wake events.
Block wall/CPU end before foreground-producer cleanup. The complete measured block sequence is retained below.

| Variant | Load | Block | Gap p99, ms | CPU seconds | Wall seconds | No-work drain calls | Foreground jobs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | idle | 1 | 50.481 | 10.498 | 104.857 | 2001 | 0 |
| baseline | idle | 2 | 50.596 | 11.797 | 105.146 | 2001 | 0 |
| baseline | idle | 3 | 50.565 | 11.952 | 105.221 | 2001 | 0 |
| baseline | idle | 4 | 50.592 | 12.042 | 105.275 | 2001 | 0 |
| baseline | idle | 5 | 50.525 | 11.687 | 105.123 | 2001 | 0 |
| baseline | foreground | 1 | 75.516 | 18.459 | 111.344 | 2004 | 1120 |
| baseline | foreground | 2 | 76.937 | 18.787 | 111.486 | 2002 | 1120 |
| baseline | foreground | 3 | 76.339 | 18.792 | 111.525 | 2003 | 1120 |
| baseline | foreground | 4 | 76.417 | 18.805 | 111.342 | 2002 | 1120 |
| baseline | foreground | 5 | 76.925 | 18.459 | 111.028 | 1987 | 1112 |
| candidate | idle | 1 | 1.631 | 5.063 | 15.671 | 2001 | 0 |
| candidate | idle | 2 | 1.689 | 4.493 | 15.182 | 2001 | 0 |
| candidate | idle | 3 | 1.700 | 5.081 | 15.672 | 2001 | 0 |
| candidate | idle | 4 | 1.700 | 4.889 | 15.492 | 2001 | 0 |
| candidate | idle | 5 | 1.710 | 5.006 | 15.619 | 2001 | 0 |
| candidate | foreground | 1 | 7.336 | 6.357 | 16.343 | 2023 | 170 |
| candidate | foreground | 2 | 7.524 | 6.054 | 16.132 | 2030 | 167 |
| candidate | foreground | 3 | 6.882 | 5.887 | 15.977 | 2042 | 160 |
| candidate | foreground | 4 | 6.493 | 5.793 | 15.884 | 2029 | 160 |
| candidate | foreground | 5 | 7.047 | 5.901 | 15.977 | 2029 | 160 |

## Commands and retained evidence

All Python commands use the existing worktree `.venv/bin/python`. Source selection and temporary environment configuration occur inside the scripts.
Each script sets `TWICC_DATA_DIR` before TwiCC imports and asserts the selected source and database paths.
The temporary scripts and JSON outputs remain under `/tmp/twicc-publication-bench-20261016`.
The commands below describe completed executions. Fresh reruns need fresh output directories and the matching source revision.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && mkdir -p /tmp/twicc-publication-bench-20261016/baseline
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && git archive 307cd0ba | tar -x -C /tmp/twicc-publication-bench-20261016/baseline
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && bash /tmp/twicc-publication-bench-20261016/run-matrix.sh
```

The original orchestrator completes lifecycle, auxiliary facts, maintenance, baseline validation, and seven diagnostic candidate validation cases.
It stops before the final diagnostic validation case completes. Its remaining commands do not run through that shell.
Each initial scenario command expands the following CLI with `SCENARIO=lifecycle|facts|maintenance|validation` and both variants:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/audited-SCENARIO-VARIANT --variant VARIANT --scenario SCENARIO
```

Corrected validation runs on `2e49f55c`:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/queryplans.py > /tmp/twicc-publication-bench-20261016/queryplans-corrected.log
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/audited-validation-candidate-corrected --variant candidate --scenario validation
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && bash /tmp/twicc-publication-bench-20261016/resume-bounded-matrix.sh
```

The bounded orchestrator completes baseline/candidate mixed and matched 128/256-row facts before the facts defect stops advancement.
These commands use `audited-mixed-VARIANT`, `matched-facts-candidate`, and `matched-facts-256-candidate` output directories.
Its initial unbounded mixed warmup remains discarded. It does not start mixed-256 or dispatch.

Corrected facts and mixed run on `6101e880`:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/facts-queryplans-corrected.py > /tmp/twicc-publication-bench-20261016/facts-queryplans-corrected.log
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/corrected-facts-128-candidate --variant candidate --scenario facts --facts-row-budget 128
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/corrected-mixed-128-candidate --variant candidate --scenario mixed --facts-row-budget 128
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/corrected-facts-256-candidate --variant candidate --scenario facts --facts-row-budget 256
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/corrected-mixed-256-candidate --variant candidate --scenario mixed --facts-row-budget 256
```

The mixed 128/256 comparisons override only the real preparation helpers' `row_budget` keyword in the temporary harness.
They retain production helper implementations, byte caps, coordinator, writer jobs, watcher, and notifications.
The final sequential orchestrator is `resume-facts-fixed.sh`. It executes the corrected commands above, then the following commands:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/audited-dispatch-baseline --variant baseline --scenario dispatch
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/bench.py --output-dir /tmp/twicc-publication-bench-20261016/audited-dispatch-candidate --variant candidate --scenario dispatch
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && PYTHONTRACEMALLOC=5 .venv/bin/python /tmp/twicc-publication-bench-20261016/warning-tests.py > /tmp/twicc-publication-bench-20261016/warning-tests.log 2>&1
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/import-timing.py baseline > /tmp/twicc-publication-bench-20261016/import-baseline.json
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/import-timing.py candidate > /tmp/twicc-publication-bench-20261016/import-candidate.json
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/footprints.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && .venv/bin/python /tmp/twicc-publication-bench-20261016/compact-tables.py
```

The warning command runs separately during the paused interval; the final orchestrator does not repeat it.
The three exact pytest node IDs are `tests/test_codex_sdk_wrappers.py::test_turn_forwards_auto_review`,
`tests/test_codex_sdk_wrappers.py::test_turn_forwards_fast_service_tier`, and `tests/test_shell_notice_codex.py::test_turn_openings_restart_the_delay`.
The wrapper calls pytest with baseline `-c pyproject.toml -q -W always` and absolute node paths.

Each measured output directory contains `benchmark.sqlite3` and `results.json`.
Dispatch directories also contain `dispatch-samples-LOAD-BLOCK.json`, preserving every individual gap for exact aggregate percentiles.
Mixed directories contain `watcher-events-REPETITION.json`, preserving append, acceptance, commit, notification, and target-deadline timestamps.
The root contains scenario logs, `footprints.json`, import timing JSON, and the focused baseline warning output.
Before-correction evidence remains present and separate from accepted tables.

## Baseline warning provenance

The three requested focused tests run against the isolated `307cd0ba` archive with `PYTHONTRACEMALLOC=5` and `-W always`.
All three tests pass. All three full-suite warnings reproduce: `3 passed, 3 warnings in 34.13s`.
The script asserts the baseline source path and temporary database path before pytest starts.
The complete allocation traces remain in `/tmp/twicc-publication-bench-20261016/warning-tests.log`.

| Test | Reproduced warning allocation |
| --- | --- |
| `test_turn_forwards_auto_review` | Unawaited `AsyncMockMixin._execute_mock_call`; `AsyncTurnHandle.__post_init__` calls `_subscribe_turn_notifications` |
| `test_turn_forwards_fast_service_tier` | Same baseline SDK mock allocation |
| `test_turn_openings_restart_the_delay` | Unawaited `BaseAgent._notify_state_change`; `shell_notice_helpers.py:32` manually advances the error/dead transition coroutine |

The relevant SDK, agent, shell-notice, and focused test files have no implementation diff from baseline to the initial candidate.
This confirms baseline provenance for these warnings. Task 8 changes no unrelated warning code.

## First-use measurements and excluded warmups

Import timings each have one observation. They do not establish an import performance difference.
The import timer includes TwiCC, Django setup, writer, history, maintenance, extraction, and codec imports.
Dictionary fixture creation and schema creation occur after that timer, before the first native encode timer.
The first native codec call uses prepared dictionary metadata and 3,200 raw bytes.
It is not a pristine OS-cache or process-start measurement.
The following 100 native encode calls have separate warm distributions.

| Variant | Imports, n=1, ms | First native encode, n=1, ms | Warm n | Warm p50 | Warm p95 | Warm p99 | Warm max | Warm >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 345.269 | 0.184 | 100 | 0.049 | 0.053 | 0.056 | 0.065 | 0/0/0/0 |
| candidate | 310.631 | 0.193 | 100 | 0.049 | 0.053 | 0.061 | 0.066 | 0/0/0/0 |

Each row below records the first complete excluded unit-phase warmup, including its normal publication work.
Warmups do not contribute to retained percentile or threshold counts.
These are operation warmups after imports, not import timings.

| Output | Input | Excluded warmup, ms |
| --- | --- | --- |
| audited-lifecycle-baseline | agents=10, mode=unchanged | 59.40 |
| audited-lifecycle-baseline | agents=10, mode=changed | 117.21 |
| audited-lifecycle-baseline | agents=10, mode=mixed | 69.43 |
| audited-lifecycle-baseline | agents=100, mode=unchanged | 409.89 |
| audited-lifecycle-baseline | agents=100, mode=changed | 721.69 |
| audited-lifecycle-baseline | agents=100, mode=mixed | 584.28 |
| audited-lifecycle-baseline | agents=400, mode=unchanged | 1533.72 |
| audited-lifecycle-baseline | agents=400, mode=changed | 2784.05 |
| audited-lifecycle-baseline | agents=400, mode=mixed | 2184.94 |
| audited-lifecycle-baseline | agents=1000, mode=unchanged | 3724.38 |
| audited-lifecycle-baseline | agents=1000, mode=changed | 6839.19 |
| audited-lifecycle-baseline | agents=1000, mode=mixed | 5574.55 |
| audited-lifecycle-candidate | agents=10, mode=unchanged | 1.69 |
| audited-lifecycle-candidate | agents=10, mode=changed | 15.69 |
| audited-lifecycle-candidate | agents=10, mode=mixed | 15.31 |
| audited-lifecycle-candidate | agents=100, mode=unchanged | 2.67 |
| audited-lifecycle-candidate | agents=100, mode=changed | 36.56 |
| audited-lifecycle-candidate | agents=100, mode=mixed | 31.59 |
| audited-lifecycle-candidate | agents=400, mode=unchanged | 6.53 |
| audited-lifecycle-candidate | agents=400, mode=changed | 140.65 |
| audited-lifecycle-candidate | agents=400, mode=mixed | 131.65 |
| audited-lifecycle-candidate | agents=1000, mode=unchanged | 14.84 |
| audited-lifecycle-candidate | agents=1000, mode=changed | 317.65 |
| audited-lifecycle-candidate | agents=1000, mode=mixed | 273.56 |
| audited-facts-baseline | facts=1000, row_budget=128 | 9.08 |
| audited-facts-baseline | facts=10000, row_budget=128 | 82.09 |
| audited-facts-baseline | facts=40000, row_budget=128 | 345.16 |
| audited-facts-baseline | facts=75000, row_budget=128 | 560.50 |
| corrected-facts-128-candidate | facts=1000, row_budget=128 | 128.60 |
| corrected-facts-128-candidate | facts=10000, row_budget=128 | 1241.06 |
| corrected-facts-128-candidate | facts=40000, row_budget=128 | 5001.53 |
| corrected-facts-128-candidate | facts=75000, row_budget=128 | 9619.58 |
| corrected-facts-256-candidate | facts=1000, row_budget=256 | 63.61 |
| corrected-facts-256-candidate | facts=10000, row_budget=256 | 673.22 |
| corrected-facts-256-candidate | facts=40000, row_budget=256 | 3125.76 |
| corrected-facts-256-candidate | facts=75000, row_budget=256 | 5899.55 |
| audited-maintenance-baseline | actual_raw_bytes=333000, owners=shared | 407.67 |
| audited-maintenance-baseline | actual_raw_bytes=333000, owners=overlap | 390.01 |
| audited-maintenance-baseline | actual_raw_bytes=333000, owners=distinct | 349.31 |
| audited-maintenance-baseline | actual_raw_bytes=8388600, owners=shared | 446.02 |
| audited-maintenance-baseline | actual_raw_bytes=8388600, owners=overlap | 432.01 |
| audited-maintenance-baseline | actual_raw_bytes=8388600, owners=distinct | 453.58 |
| audited-maintenance-baseline | actual_raw_bytes=12592812, owners=shared | 427.08 |
| audited-maintenance-baseline | actual_raw_bytes=12592812, owners=overlap | 439.96 |
| audited-maintenance-baseline | actual_raw_bytes=12592812, owners=distinct | 467.93 |
| audited-maintenance-candidate | actual_raw_bytes=333000, owners=shared | 78.36 |
| audited-maintenance-candidate | actual_raw_bytes=333000, owners=overlap | 77.56 |
| audited-maintenance-candidate | actual_raw_bytes=333000, owners=distinct | 80.19 |
| audited-maintenance-candidate | actual_raw_bytes=8388600, owners=shared | 113.26 |
| audited-maintenance-candidate | actual_raw_bytes=8388600, owners=overlap | 125.84 |
| audited-maintenance-candidate | actual_raw_bytes=8388600, owners=distinct | 141.53 |
| audited-maintenance-candidate | actual_raw_bytes=12592812, owners=shared | 270.55 |
| audited-maintenance-candidate | actual_raw_bytes=12592812, owners=overlap | 323.80 |
| audited-maintenance-candidate | actual_raw_bytes=12592812, owners=distinct | 306.60 |
| audited-validation-baseline | items=1000, connection_cache=warm | 779.54 |
| audited-validation-baseline | items=1000, connection_cache=reopened | 33.33 |
| audited-validation-baseline | items=10000, connection_cache=warm | 52.76 |
| audited-validation-baseline | items=10000, connection_cache=reopened | 52.50 |
| audited-validation-baseline | items=100000, connection_cache=warm | 115.13 |
| audited-validation-baseline | items=100000, connection_cache=reopened | 106.75 |
| audited-validation-baseline | items=200000, connection_cache=warm | 193.90 |
| audited-validation-baseline | items=200000, connection_cache=reopened | 187.01 |
| audited-validation-candidate-corrected | items=1000, connection_cache=warm | 683.80 |
| audited-validation-candidate-corrected | items=1000, connection_cache=reopened | 87.09 |
| audited-validation-candidate-corrected | items=10000, connection_cache=warm | 609.02 |
| audited-validation-candidate-corrected | items=10000, connection_cache=reopened | 591.70 |
| audited-validation-candidate-corrected | items=100000, connection_cache=warm | 5988.62 |
| audited-validation-candidate-corrected | items=100000, connection_cache=reopened | 6492.87 |
| audited-validation-candidate-corrected | items=200000, connection_cache=warm | 12338.11 |
| audited-validation-candidate-corrected | items=200000, connection_cache=reopened | 11816.28 |

Dispatch warmups contain 20 preparations. Mixed warmups contain the full 134-watcher-append bounded workload.

| Output | Warmup | Preparations or watcher appends | Excluded wall, ms |
| --- | --- | --- | --- |
| dispatch-baseline | idle | 20 | 1636.17 |
| dispatch-baseline | foreground | 20 | 1179.51 |
| dispatch-candidate | idle | 20 | 719.26 |
| dispatch-candidate | foreground | 20 | 178.03 |
| mixed-baseline | mixed | 134 | 15303.35 |
| mixed-corrected-128 | mixed | 134 | 18509.88 |
| mixed-corrected-256 | mixed | 134 | 15834.15 |

## Auxiliary facts budget stress test before correction

These requested 128/256/1,000-row cases precede `6101e880`. They do not validate the corrected implementation.
Each input uses five complete measured repetitions and the same 256 KiB byte limit.
The auxiliary database has three source sessions per size. Its population differs from the matched baseline.
The byte limit can bind before the nominal row limit. Larger nominal budgets do not guarantee larger actual pages.
The subsequent corrected matched 128/256 matrix controls the final budget decision.
No corrected 1,000-row acceptance claim follows from these diagnostic results.

| Facts | Row budget | SQLite span, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1000 | 128 | facts_delete:sqlite_writer_hold | 40 | 5.11 | 10.98 | 13.34 | 14.08 | 0/0/0/0 |
| 1000 | 128 | facts_insert:sqlite_writer_hold | 40 | 5.52 | 7.24 | 9.34 | 10.89 | 0/0/0/0 |
| 1000 | 256 | facts_delete:sqlite_writer_hold | 20 | 5.51 | 5.94 | 5.94 | 6.05 | 0/0/0/0 |
| 1000 | 256 | facts_insert:sqlite_writer_hold | 20 | 6.26 | 10.77 | 10.77 | 12.84 | 0/0/0/0 |
| 1000 | 1000 | facts_delete:sqlite_writer_hold | 10 | 6.05 | 9.60 | 9.60 | 9.98 | 0/0/0/0 |
| 1000 | 1000 | facts_insert:sqlite_writer_hold | 10 | 8.64 | 11.14 | 11.14 | 13.47 | 0/0/0/0 |
| 10000 | 128 | facts_delete:sqlite_writer_hold | 395 | 6.57 | 9.55 | 14.14 | 18.79 | 0/0/0/0 |
| 10000 | 128 | facts_insert:sqlite_writer_hold | 395 | 6.72 | 12.63 | 20.46 | 25.54 | 0/0/0/0 |
| 10000 | 256 | facts_delete:sqlite_writer_hold | 200 | 6.07 | 9.75 | 17.47 | 20.85 | 0/0/0/0 |
| 10000 | 256 | facts_insert:sqlite_writer_hold | 200 | 8.25 | 10.60 | 23.04 | 24.71 | 0/0/0/0 |
| 10000 | 1000 | facts_delete:sqlite_writer_hold | 70 | 6.85 | 8.74 | 9.68 | 10.47 | 0/0/0/0 |
| 10000 | 1000 | facts_insert:sqlite_writer_hold | 70 | 8.85 | 19.77 | 21.43 | 22.81 | 0/0/0/0 |
| 40000 | 128 | facts_delete:sqlite_writer_hold | 1565 | 7.55 | 12.65 | 17.87 | 34.37 | 0/0/0/0 |
| 40000 | 128 | facts_insert:sqlite_writer_hold | 1565 | 6.32 | 12.76 | 22.19 | 77.29 | 0/0/0/0 |
| 40000 | 256 | facts_delete:sqlite_writer_hold | 785 | 7.61 | 13.84 | 19.83 | 23.05 | 0/0/0/0 |
| 40000 | 256 | facts_insert:sqlite_writer_hold | 785 | 5.46 | 16.46 | 23.85 | 60.39 | 0/0/0/0 |
| 40000 | 1000 | facts_delete:sqlite_writer_hold | 290 | 8.66 | 16.80 | 21.01 | 26.24 | 0/0/0/0 |
| 40000 | 1000 | facts_insert:sqlite_writer_hold | 290 | 7.24 | 19.69 | 32.29 | 47.88 | 0/0/0/0 |
| 75000 | 128 | facts_delete:sqlite_writer_hold | 2930 | 8.79 | 14.55 | 17.15 | 30.80 | 0/0/0/0 |
| 75000 | 128 | facts_insert:sqlite_writer_hold | 2930 | 7.14 | 15.86 | 26.67 | 62.37 | 0/0/0/0 |
| 75000 | 256 | facts_delete:sqlite_writer_hold | 1465 | 10.17 | 17.41 | 24.49 | 59.23 | 0/0/0/0 |
| 75000 | 256 | facts_insert:sqlite_writer_hold | 1465 | 5.74 | 16.25 | 22.30 | 35.41 | 0/0/0/0 |
| 75000 | 1000 | facts_delete:sqlite_writer_hold | 550 | 12.12 | 23.16 | 29.62 | 45.65 | 0/0/0/0 |
| 75000 | 1000 | facts_insert:sqlite_writer_hold | 550 | 10.07 | 23.20 | 33.71 | 207.34 | 1/0/0/0 |

## Retained synthetic database and content sizes

Sizes describe post-run files and SQLite `dbstat` pages. They are not peak WAL, TEMP, resident-memory, or I/O volumes.
Whole databases accumulate all input sizes in their scenario. Source-level validation sizes appear separately.

File sizes and content sizes use bytes. SQLite page bytes include table/index storage; cell payload bytes exclude page overhead.

| Output | DB bytes | WAL bytes | Sessions | Items | Contents | Facts |
| --- | --- | --- | --- | --- | --- | --- |
| audited-dispatch-baseline | 634880 | 0 | 1 | 0 | 0 | 0 |
| audited-dispatch-candidate | 634880 | 0 | 1 | 0 | 0 | 0 |
| audited-facts-baseline | 67874816 | 0 | 4 | 0 | 0 | 126000 |
| audited-lifecycle-baseline | 4251648 | 0 | 4542 | 4542 | 12 | 0 |
| audited-lifecycle-candidate | 4268032 | 0 | 4542 | 4542 | 12 | 0 |
| audited-maintenance-baseline | 16015360 | 0 | 360 | 0 | 900 | 0 |
| audited-maintenance-candidate | 15912960 | 0 | 360 | 0 | 900 | 0 |
| audited-mixed-baseline | 12709888 | 0 | 403 | 11205 | 806 | 40000 |
| audited-validation-baseline | 46526464 | 0 | 4 | 311000 | 2048 | 0 |
| audited-validation-candidate-corrected | 46526464 | 0 | 4 | 311000 | 2048 | 0 |
| corrected-facts-128-candidate | 67878912 | 0 | 4 | 0 | 0 | 126000 |
| corrected-facts-256-candidate | 67878912 | 0 | 4 | 0 | 0 | 126000 |
| corrected-mixed-128-candidate | 12746752 | 0 | 403 | 11205 | 806 | 40000 |
| corrected-mixed-256-candidate | 12746752 | 0 | 403 | 11205 | 806 | 40000 |

| Output | Codec | Content rows | Stored content bytes | Raw content bytes |
| --- | --- | --- | --- | --- |
| audited-lifecycle-baseline | plain | 12 | 564 | 564 |
| audited-lifecycle-candidate | plain | 12 | 564 | 564 |
| audited-maintenance-baseline | zstd | 900 | 60844 | 63943236 |
| audited-maintenance-candidate | zstd | 900 | 60844 | 63943236 |
| audited-mixed-baseline | plain | 806 | 187550 | 187550 |
| audited-validation-baseline | plain | 2048 | 803912 | 803912 |
| audited-validation-candidate-corrected | plain | 2048 | 803912 | 803912 |
| corrected-mixed-128-candidate | plain | 806 | 187550 | 187550 |
| corrected-mixed-256-candidate | plain | 806 | 187550 | 187550 |

| Output | Validation source | Items | Distinct current/original contents | Stored bytes | Raw bytes |
| --- | --- | --- | --- | --- | --- |
| audited-validation-baseline | valid-1000 | 1000 | 512 | 200338 | 200338 |
| audited-validation-baseline | valid-10000 | 10000 | 512 | 200850 | 200850 |
| audited-validation-baseline | valid-100000 | 100000 | 512 | 201362 | 201362 |
| audited-validation-baseline | valid-200000 | 200000 | 512 | 201362 | 201362 |
| audited-validation-candidate-corrected | valid-1000 | 1000 | 512 | 200338 | 200338 |
| audited-validation-candidate-corrected | valid-10000 | 10000 | 512 | 200850 | 200850 |
| audited-validation-candidate-corrected | valid-100000 | 100000 | 512 | 201362 | 201362 |
| audited-validation-candidate-corrected | valid-200000 | 200000 | 512 | 201362 | 201362 |

### Table and index page footprint

| Output | Table or index | Page bytes | Cell payload bytes |
| --- | --- | --- | --- |
| audited-dispatch-baseline | core_session | 4096 | 215 |
| audited-dispatch-baseline | core_session_parent_session_id_d545c4ea | 4096 | 3 |
| audited-dispatch-baseline | core_session_project_id_ce0e2ba5 | 4096 | 8 |
| audited-dispatch-baseline | core_session_provider_82180212 | 4096 | 8 |
| audited-dispatch-baseline | core_session_spawn_root_id_42098ff9 | 4096 | 3 |
| audited-dispatch-baseline | core_session_spawned_by_id_7aaec49f | 4096 | 3 |
| audited-dispatch-baseline | core_session_type_4a798408 | 4096 | 10 |
| audited-dispatch-baseline | core_sessioncron | 4096 | 0 |
| audited-dispatch-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-dispatch-baseline | core_sessionhistoryfact | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitem | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitemcontent | 4096 | 0 |
| audited-dispatch-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 0 |
| audited-dispatch-baseline | sqlite_autoindex_core_session_1 | 4096 | 16 |
| audited-dispatch-baseline | sqlite_autoindex_core_session_2 | 4096 | 87 |
| audited-dispatch-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-dispatch-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-dispatch-baseline | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| audited-dispatch-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 0 |
| audited-dispatch-candidate | core_session | 4096 | 216 |
| audited-dispatch-candidate | core_session_parent_session_id_d545c4ea | 4096 | 3 |
| audited-dispatch-candidate | core_session_project_id_ce0e2ba5 | 4096 | 8 |
| audited-dispatch-candidate | core_session_provider_82180212 | 4096 | 8 |
| audited-dispatch-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 3 |
| audited-dispatch-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 3 |
| audited-dispatch-candidate | core_session_type_4a798408 | 4096 | 10 |
| audited-dispatch-candidate | core_sessioncron | 4096 | 0 |
| audited-dispatch-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-dispatch-candidate | core_sessionhistoryfact | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitem | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitemcontent | 4096 | 0 |
| audited-dispatch-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 0 |
| audited-dispatch-candidate | sqlite_autoindex_core_session_1 | 4096 | 16 |
| audited-dispatch-candidate | sqlite_autoindex_core_session_2 | 4096 | 88 |
| audited-dispatch-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-dispatch-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-dispatch-candidate | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| audited-dispatch-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 0 |
| audited-facts-baseline | core_session | 4096 | 829 |
| audited-facts-baseline | core_session_parent_session_id_d545c4ea | 4096 | 15 |
| audited-facts-baseline | core_session_project_id_ce0e2ba5 | 4096 | 35 |
| audited-facts-baseline | core_session_provider_82180212 | 4096 | 35 |
| audited-facts-baseline | core_session_spawn_root_id_42098ff9 | 4096 | 15 |
| audited-facts-baseline | core_session_spawned_by_id_7aaec49f | 4096 | 15 |
| audited-facts-baseline | core_session_type_4a798408 | 4096 | 43 |
| audited-facts-baseline | core_sessioncron | 4096 | 0 |
| audited-facts-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-facts-baseline | core_sessionhistoryfact | 60858368 | 52488517 |
| audited-facts-baseline | core_sessionitem | 4096 | 0 |
| audited-facts-baseline | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| audited-facts-baseline | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| audited-facts-baseline | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| audited-facts-baseline | core_sessionitemcontent | 4096 | 0 |
| audited-facts-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 0 |
| audited-facts-baseline | sqlite_autoindex_core_session_1 | 4096 | 74 |
| audited-facts-baseline | sqlite_autoindex_core_session_2 | 4096 | 346 |
| audited-facts-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-facts-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 6389760 | 5429529 |
| audited-facts-baseline | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| audited-facts-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 0 |
| audited-lifecycle-baseline | core_session | 1552384 | 1352218 |
| audited-lifecycle-baseline | core_session_parent_session_id_d545c4ea | 139264 | 98032 |
| audited-lifecycle-baseline | core_session_project_id_ce0e2ba5 | 69632 | 45292 |
| audited-lifecycle-baseline | core_session_provider_82180212 | 69632 | 45292 |
| audited-lifecycle-baseline | core_session_spawn_root_id_42098ff9 | 45056 | 22582 |
| audited-lifecycle-baseline | core_session_spawned_by_id_7aaec49f | 45056 | 22582 |
| audited-lifecycle-baseline | core_session_type_4a798408 | 86016 | 58906 |
| audited-lifecycle-baseline | core_sessioncron | 4096 | 0 |
| audited-lifecycle-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-lifecycle-baseline | core_sessionhistoryfact | 4096 | 0 |
| audited-lifecycle-baseline | core_sessionitem | 208896 | 178988 |
| audited-lifecycle-baseline | core_sessionitem_group_head_6ab2d437 | 45056 | 22582 |
| audited-lifecycle-baseline | core_sessionitem_message_id_0d319e61 | 45056 | 22582 |
| audited-lifecycle-baseline | core_sessionitem_session_id_5bd4bb18 | 151552 | 115294 |
| audited-lifecycle-baseline | core_sessionitemcontent | 122880 | 112110 |
| audited-lifecycle-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 47 |
| audited-lifecycle-baseline | sqlite_autoindex_core_session_1 | 151552 | 115294 |
| audited-lifecycle-baseline | sqlite_autoindex_core_session_2 | 520192 | 442318 |
| audited-lifecycle-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-lifecycle-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-lifecycle-baseline | sqlite_autoindex_core_sessionitem_1 | 151552 | 119836 |
| audited-lifecycle-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 431 |
| audited-lifecycle-candidate | core_session | 1564672 | 1356748 |
| audited-lifecycle-candidate | core_session_parent_session_id_d545c4ea | 139264 | 98032 |
| audited-lifecycle-candidate | core_session_project_id_ce0e2ba5 | 69632 | 45292 |
| audited-lifecycle-candidate | core_session_provider_82180212 | 69632 | 45292 |
| audited-lifecycle-candidate | core_session_spawn_root_id_42098ff9 | 45056 | 22582 |
| audited-lifecycle-candidate | core_session_spawned_by_id_7aaec49f | 45056 | 22582 |
| audited-lifecycle-candidate | core_session_type_4a798408 | 86016 | 58906 |
| audited-lifecycle-candidate | core_sessioncron | 4096 | 0 |
| audited-lifecycle-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-lifecycle-candidate | core_sessionhistoryfact | 4096 | 0 |
| audited-lifecycle-candidate | core_sessionitem | 208896 | 178988 |
| audited-lifecycle-candidate | core_sessionitem_group_head_6ab2d437 | 45056 | 22582 |
| audited-lifecycle-candidate | core_sessionitem_message_id_0d319e61 | 45056 | 22582 |
| audited-lifecycle-candidate | core_sessionitem_session_id_5bd4bb18 | 151552 | 115294 |
| audited-lifecycle-candidate | core_sessionitemcontent | 122880 | 112110 |
| audited-lifecycle-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 47 |
| audited-lifecycle-candidate | sqlite_autoindex_core_session_1 | 151552 | 115294 |
| audited-lifecycle-candidate | sqlite_autoindex_core_session_2 | 524288 | 446860 |
| audited-lifecycle-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-lifecycle-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-lifecycle-candidate | sqlite_autoindex_core_sessionitem_1 | 151552 | 119836 |
| audited-lifecycle-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 431 |
| audited-maintenance-baseline | core_session | 102400 | 93780 |
| audited-maintenance-baseline | core_session_parent_session_id_d545c4ea | 4096 | 1672 |
| audited-maintenance-baseline | core_session_project_id_ce0e2ba5 | 12288 | 3472 |
| audited-maintenance-baseline | core_session_provider_82180212 | 12288 | 3472 |
| audited-maintenance-baseline | core_session_spawn_root_id_42098ff9 | 4096 | 1672 |
| audited-maintenance-baseline | core_session_spawned_by_id_7aaec49f | 4096 | 1672 |
| audited-maintenance-baseline | core_session_type_4a798408 | 12288 | 4192 |
| audited-maintenance-baseline | core_sessioncron | 4096 | 0 |
| audited-maintenance-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-maintenance-baseline | core_sessionhistoryfact | 4096 | 0 |
| audited-maintenance-baseline | core_sessionitem | 4096 | 0 |
| audited-maintenance-baseline | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| audited-maintenance-baseline | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| audited-maintenance-baseline | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| audited-maintenance-baseline | core_sessionitemcontent | 2543616 | 266023 |
| audited-maintenance-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 12288 | 4372 |
| audited-maintenance-baseline | sqlite_autoindex_core_session_1 | 16384 | 9412 |
| audited-maintenance-baseline | sqlite_autoindex_core_session_2 | 53248 | 36052 |
| audited-maintenance-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-maintenance-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-maintenance-baseline | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| audited-maintenance-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 40960 | 33172 |
| audited-maintenance-candidate | core_session | 102400 | 94140 |
| audited-maintenance-candidate | core_session_parent_session_id_d545c4ea | 4096 | 1672 |
| audited-maintenance-candidate | core_session_project_id_ce0e2ba5 | 12288 | 3472 |
| audited-maintenance-candidate | core_session_provider_82180212 | 12288 | 3472 |
| audited-maintenance-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 1672 |
| audited-maintenance-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 1672 |
| audited-maintenance-candidate | core_session_type_4a798408 | 12288 | 4192 |
| audited-maintenance-candidate | core_sessioncron | 4096 | 0 |
| audited-maintenance-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-maintenance-candidate | core_sessionhistoryfact | 4096 | 0 |
| audited-maintenance-candidate | core_sessionitem | 4096 | 0 |
| audited-maintenance-candidate | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| audited-maintenance-candidate | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| audited-maintenance-candidate | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| audited-maintenance-candidate | core_sessionitemcontent | 2441216 | 266023 |
| audited-maintenance-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 12288 | 4372 |
| audited-maintenance-candidate | sqlite_autoindex_core_session_1 | 16384 | 9412 |
| audited-maintenance-candidate | sqlite_autoindex_core_session_2 | 53248 | 36412 |
| audited-maintenance-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-maintenance-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-maintenance-candidate | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| audited-maintenance-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 40960 | 33172 |
| audited-mixed-baseline | core_session | 98304 | 87946 |
| audited-mixed-baseline | core_session_parent_session_id_d545c4ea | 12288 | 5887 |
| audited-mixed-baseline | core_session_project_id_ce0e2ba5 | 12288 | 3902 |
| audited-mixed-baseline | core_session_provider_82180212 | 12288 | 3908 |
| audited-mixed-baseline | core_session_spawn_root_id_42098ff9 | 4096 | 1887 |
| audited-mixed-baseline | core_session_spawned_by_id_7aaec49f | 4096 | 1887 |
| audited-mixed-baseline | core_session_type_4a798408 | 12288 | 5108 |
| audited-mixed-baseline | core_sessioncron | 4096 | 0 |
| audited-mixed-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-mixed-baseline | core_sessionhistoryfact | 8101888 | 7702727 |
| audited-mixed-baseline | core_sessionitem | 421888 | 359296 |
| audited-mixed-baseline | core_sessionitem_group_head_6ab2d437 | 106496 | 55897 |
| audited-mixed-baseline | core_sessionitem_message_id_0d319e61 | 106496 | 55897 |
| audited-mixed-baseline | core_sessionitem_session_id_5bd4bb18 | 270336 | 190741 |
| audited-mixed-baseline | core_sessionitemcontent | 290816 | 276385 |
| audited-mixed-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 12288 | 3902 |
| audited-mixed-baseline | sqlite_autoindex_core_session_1 | 16384 | 7833 |
| audited-mixed-baseline | sqlite_autoindex_core_session_2 | 45056 | 35181 |
| audited-mixed-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-mixed-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 1896448 | 1595999 |
| audited-mixed-baseline | sqlite_autoindex_core_sessionitem_1 | 303104 | 223298 |
| audited-mixed-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 36864 | 29694 |
| audited-validation-baseline | core_session | 4096 | 817 |
| audited-validation-baseline | core_session_parent_session_id_d545c4ea | 4096 | 15 |
| audited-validation-baseline | core_session_project_id_ce0e2ba5 | 4096 | 35 |
| audited-validation-baseline | core_session_provider_82180212 | 4096 | 35 |
| audited-validation-baseline | core_session_spawn_root_id_42098ff9 | 4096 | 15 |
| audited-validation-baseline | core_session_spawned_by_id_7aaec49f | 4096 | 15 |
| audited-validation-baseline | core_session_type_4a798408 | 4096 | 43 |
| audited-validation-baseline | core_sessioncron | 4096 | 0 |
| audited-validation-baseline | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-validation-baseline | core_sessionhistoryfact | 4096 | 0 |
| audited-validation-baseline | core_sessionitem | 13074432 | 11106468 |
| audited-validation-baseline | core_sessionitem_group_head_6ab2d437 | 3170304 | 1833105 |
| audited-validation-baseline | core_sessionitem_message_id_0d319e61 | 3170304 | 1833105 |
| audited-validation-baseline | core_sessionitem_session_id_5bd4bb18 | 7454720 | 5553105 |
| audited-validation-baseline | core_sessionitemcontent | 1044480 | 949524 |
| audited-validation-baseline | core_sessionitemcontent_dictionary_id_cee40c89 | 24576 | 10112 |
| audited-validation-baseline | sqlite_autoindex_core_session_1 | 4096 | 60 |
| audited-validation-baseline | sqlite_autoindex_core_session_2 | 4096 | 352 |
| audited-validation-baseline | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-validation-baseline | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-validation-baseline | sqlite_autoindex_core_sessionitem_1 | 8785920 | 6720059 |
| audited-validation-baseline | sqlite_autoindex_core_sessionitemcontent_1 | 90112 | 75648 |
| audited-validation-candidate-corrected | core_session | 4096 | 861 |
| audited-validation-candidate-corrected | core_session_parent_session_id_d545c4ea | 4096 | 15 |
| audited-validation-candidate-corrected | core_session_project_id_ce0e2ba5 | 4096 | 35 |
| audited-validation-candidate-corrected | core_session_provider_82180212 | 4096 | 35 |
| audited-validation-candidate-corrected | core_session_spawn_root_id_42098ff9 | 4096 | 15 |
| audited-validation-candidate-corrected | core_session_spawned_by_id_7aaec49f | 4096 | 15 |
| audited-validation-candidate-corrected | core_session_type_4a798408 | 4096 | 43 |
| audited-validation-candidate-corrected | core_sessioncron | 4096 | 0 |
| audited-validation-candidate-corrected | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| audited-validation-candidate-corrected | core_sessionhistoryfact | 4096 | 0 |
| audited-validation-candidate-corrected | core_sessionitem | 13074432 | 11106468 |
| audited-validation-candidate-corrected | core_sessionitem_group_head_6ab2d437 | 3170304 | 1833105 |
| audited-validation-candidate-corrected | core_sessionitem_message_id_0d319e61 | 3170304 | 1833105 |
| audited-validation-candidate-corrected | core_sessionitem_session_id_5bd4bb18 | 7454720 | 5553105 |
| audited-validation-candidate-corrected | core_sessionitemcontent | 1044480 | 949524 |
| audited-validation-candidate-corrected | core_sessionitemcontent_dictionary_id_cee40c89 | 24576 | 10112 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_session_1 | 4096 | 60 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_session_2 | 4096 | 396 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_sessionhistoryfact_1 | 4096 | 0 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_sessionitem_1 | 8785920 | 6720059 |
| audited-validation-candidate-corrected | sqlite_autoindex_core_sessionitemcontent_1 | 90112 | 75648 |
| corrected-facts-128-candidate | core_session | 4096 | 857 |
| corrected-facts-128-candidate | core_session_parent_session_id_d545c4ea | 4096 | 15 |
| corrected-facts-128-candidate | core_session_project_id_ce0e2ba5 | 4096 | 35 |
| corrected-facts-128-candidate | core_session_provider_82180212 | 4096 | 35 |
| corrected-facts-128-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 15 |
| corrected-facts-128-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 15 |
| corrected-facts-128-candidate | core_session_type_4a798408 | 4096 | 43 |
| corrected-facts-128-candidate | core_sessioncron | 4096 | 0 |
| corrected-facts-128-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionhistoryfact | 60862464 | 52488517 |
| corrected-facts-128-candidate | core_sessionitem | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionitemcontent | 4096 | 0 |
| corrected-facts-128-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 0 |
| corrected-facts-128-candidate | sqlite_autoindex_core_session_1 | 4096 | 74 |
| corrected-facts-128-candidate | sqlite_autoindex_core_session_2 | 4096 | 374 |
| corrected-facts-128-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| corrected-facts-128-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 6389760 | 5429529 |
| corrected-facts-128-candidate | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| corrected-facts-128-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 0 |
| corrected-facts-256-candidate | core_session | 4096 | 857 |
| corrected-facts-256-candidate | core_session_parent_session_id_d545c4ea | 4096 | 15 |
| corrected-facts-256-candidate | core_session_project_id_ce0e2ba5 | 4096 | 35 |
| corrected-facts-256-candidate | core_session_provider_82180212 | 4096 | 35 |
| corrected-facts-256-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 15 |
| corrected-facts-256-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 15 |
| corrected-facts-256-candidate | core_session_type_4a798408 | 4096 | 43 |
| corrected-facts-256-candidate | core_sessioncron | 4096 | 0 |
| corrected-facts-256-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionhistoryfact | 60862464 | 52488517 |
| corrected-facts-256-candidate | core_sessionitem | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionitem_group_head_6ab2d437 | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionitem_message_id_0d319e61 | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionitem_session_id_5bd4bb18 | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionitemcontent | 4096 | 0 |
| corrected-facts-256-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 4096 | 0 |
| corrected-facts-256-candidate | sqlite_autoindex_core_session_1 | 4096 | 74 |
| corrected-facts-256-candidate | sqlite_autoindex_core_session_2 | 4096 | 374 |
| corrected-facts-256-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| corrected-facts-256-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 6389760 | 5429529 |
| corrected-facts-256-candidate | sqlite_autoindex_core_sessionitem_1 | 4096 | 0 |
| corrected-facts-256-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 4096 | 0 |
| corrected-mixed-128-candidate | core_session | 131072 | 111562 |
| corrected-mixed-128-candidate | core_session_parent_session_id_d545c4ea | 12288 | 5887 |
| corrected-mixed-128-candidate | core_session_project_id_ce0e2ba5 | 12288 | 3902 |
| corrected-mixed-128-candidate | core_session_provider_82180212 | 12288 | 3908 |
| corrected-mixed-128-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 1887 |
| corrected-mixed-128-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 1887 |
| corrected-mixed-128-candidate | core_session_type_4a798408 | 12288 | 5108 |
| corrected-mixed-128-candidate | core_sessioncron | 4096 | 0 |
| corrected-mixed-128-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| corrected-mixed-128-candidate | core_sessionhistoryfact | 8101888 | 7702727 |
| corrected-mixed-128-candidate | core_sessionitem | 421888 | 359296 |
| corrected-mixed-128-candidate | core_sessionitem_group_head_6ab2d437 | 106496 | 55897 |
| corrected-mixed-128-candidate | core_sessionitem_message_id_0d319e61 | 106496 | 55897 |
| corrected-mixed-128-candidate | core_sessionitem_session_id_5bd4bb18 | 270336 | 190741 |
| corrected-mixed-128-candidate | core_sessionitemcontent | 290816 | 276385 |
| corrected-mixed-128-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 12288 | 3902 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_session_1 | 16384 | 7833 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_session_2 | 49152 | 37995 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 1896448 | 1595999 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_sessionitem_1 | 303104 | 223298 |
| corrected-mixed-128-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 36864 | 29694 |
| corrected-mixed-256-candidate | core_session | 131072 | 111562 |
| corrected-mixed-256-candidate | core_session_parent_session_id_d545c4ea | 12288 | 5887 |
| corrected-mixed-256-candidate | core_session_project_id_ce0e2ba5 | 12288 | 3902 |
| corrected-mixed-256-candidate | core_session_provider_82180212 | 12288 | 3908 |
| corrected-mixed-256-candidate | core_session_spawn_root_id_42098ff9 | 4096 | 1887 |
| corrected-mixed-256-candidate | core_session_spawned_by_id_7aaec49f | 4096 | 1887 |
| corrected-mixed-256-candidate | core_session_type_4a798408 | 12288 | 5108 |
| corrected-mixed-256-candidate | core_sessioncron | 4096 | 0 |
| corrected-mixed-256-candidate | core_sessioncron_process_run_id_71b11740 | 4096 | 0 |
| corrected-mixed-256-candidate | core_sessionhistoryfact | 8101888 | 7702727 |
| corrected-mixed-256-candidate | core_sessionitem | 421888 | 359296 |
| corrected-mixed-256-candidate | core_sessionitem_group_head_6ab2d437 | 106496 | 55897 |
| corrected-mixed-256-candidate | core_sessionitem_message_id_0d319e61 | 106496 | 55897 |
| corrected-mixed-256-candidate | core_sessionitem_session_id_5bd4bb18 | 270336 | 190741 |
| corrected-mixed-256-candidate | core_sessionitemcontent | 290816 | 276385 |
| corrected-mixed-256-candidate | core_sessionitemcontent_dictionary_id_cee40c89 | 12288 | 3902 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_session_1 | 16384 | 7833 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_session_2 | 49152 | 37995 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_sessioncron_1 | 4096 | 0 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_sessionhistoryfact_1 | 1896448 | 1595999 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_sessionitem_1 | 303104 | 223298 |
| corrected-mixed-256-candidate | sqlite_autoindex_core_sessionitemcontent_1 | 36864 | 29694 |

## Complete retained metric tables

The following tables use only accepted matched sources and corrected affected cases.
Facts 256 and mixed 256 tables record the alternative budget; production retains 128.
Cumulative durations include only the named phase, except explicitly labeled mixed whole-operation metrics.
Existing validation setup stays excluded and has no separate timing measurement.

## Lifecycle retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 10 unchanged | stopped_agents:sqlite_writer_hold | 5 | 45.27 | 45.60 | 45.60 | 45.68 | 0/0/0/0 |
| baseline | 10 changed | stopped_agents:sqlite_writer_hold | 5 | 87.01 | 88.17 | 88.17 | 88.51 | 0/0/0/0 |
| baseline | 10 mixed | stopped_agents:sqlite_writer_hold | 5 | 70.02 | 70.04 | 70.04 | 70.29 | 0/0/0/0 |
| baseline | 100 unchanged | stopped_agents:sqlite_writer_hold | 5 | 412.63 | 423.94 | 423.94 | 433.49 | 5/5/0/0 |
| baseline | 100 changed | stopped_agents:sqlite_writer_hold | 5 | 761.82 | 769.16 | 769.16 | 809.04 | 5/5/5/0 |
| baseline | 100 mixed | stopped_agents:sqlite_writer_hold | 5 | 580.74 | 582.83 | 582.83 | 633.74 | 5/5/5/0 |
| baseline | 400 unchanged | stopped_agents:sqlite_writer_hold | 5 | 1517.10 | 1540.69 | 1540.69 | 1566.83 | 5/5/5/5 |
| baseline | 400 changed | stopped_agents:sqlite_writer_hold | 5 | 2737.33 | 2808.29 | 2808.29 | 2846.20 | 5/5/5/5 |
| baseline | 400 mixed | stopped_agents:sqlite_writer_hold | 5 | 2131.76 | 2142.66 | 2142.66 | 2153.29 | 5/5/5/5 |
| baseline | 1000 unchanged | stopped_agents:sqlite_writer_hold | 5 | 3759.31 | 3769.88 | 3769.88 | 3853.45 | 5/5/5/5 |
| baseline | 1000 changed | stopped_agents:sqlite_writer_hold | 5 | 9342.57 | 9637.88 | 9637.88 | 9947.20 | 5/5/5/5 |
| baseline | 1000 mixed | stopped_agents:sqlite_writer_hold | 5 | 7301.40 | 7334.39 | 7334.39 | 7477.57 | 5/5/5/5 |
| candidate | 10 unchanged | stopped_agents:sqlite_writer_hold | 5 | 0.86 | 0.88 | 0.88 | 0.92 | 0/0/0/0 |
| candidate | 10 changed | stopped_agents:sqlite_writer_hold | 5 | 14.89 | 15.38 | 15.38 | 15.39 | 0/0/0/0 |
| candidate | 10 mixed | stopped_agents:sqlite_writer_hold | 5 | 14.64 | 14.89 | 14.89 | 15.36 | 0/0/0/0 |
| candidate | 100 unchanged | stopped_agents:sqlite_writer_hold | 10 | 0.90 | 0.98 | 0.98 | 1.33 | 0/0/0/0 |
| candidate | 100 changed | stopped_agents:sqlite_writer_hold | 10 | 18.52 | 20.28 | 20.28 | 23.78 | 0/0/0/0 |
| candidate | 100 mixed | stopped_agents:sqlite_writer_hold | 10 | 15.13 | 16.29 | 16.29 | 16.57 | 0/0/0/0 |
| candidate | 400 unchanged | stopped_agents:sqlite_writer_hold | 35 | 0.90 | 1.28 | 1.40 | 1.40 | 0/0/0/0 |
| candidate | 400 changed | stopped_agents:sqlite_writer_hold | 35 | 18.48 | 21.47 | 21.55 | 21.76 | 0/0/0/0 |
| candidate | 400 mixed | stopped_agents:sqlite_writer_hold | 35 | 18.43 | 22.90 | 23.06 | 23.12 | 0/0/0/0 |
| candidate | 1000 unchanged | stopped_agents:sqlite_writer_hold | 80 | 0.84 | 1.03 | 1.10 | 1.38 | 0/0/0/0 |
| candidate | 1000 changed | stopped_agents:sqlite_writer_hold | 80 | 19.09 | 22.36 | 25.29 | 25.91 | 0/0/0/0 |
| candidate | 1000 mixed | stopped_agents:sqlite_writer_hold | 80 | 17.06 | 21.87 | 27.45 | 27.71 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 10 unchanged | stopped_agents:cumulative | 5 | 45.40 | 45.72 | 45.72 | 45.82 | 0/0/0/0 |
| baseline | 10 unchanged | foreground:completion | 5 | 58.27 | 58.46 | 58.46 | 58.57 | 0/0/0/0 |
| baseline | 10 changed | stopped_agents:cumulative | 5 | 87.21 | 88.42 | 88.42 | 88.71 | 0/0/0/0 |
| baseline | 10 changed | foreground:completion | 5 | 108.60 | 108.76 | 108.76 | 108.79 | 5/0/0/0 |
| baseline | 10 mixed | stopped_agents:cumulative | 5 | 70.23 | 70.25 | 70.25 | 70.48 | 0/0/0/0 |
| baseline | 10 mixed | foreground:completion | 5 | 83.75 | 83.83 | 83.83 | 85.36 | 0/0/0/0 |
| baseline | 100 unchanged | stopped_agents:cumulative | 5 | 413.00 | 424.12 | 424.12 | 433.60 | 5/5/0/0 |
| baseline | 100 unchanged | foreground:completion | 5 | 441.62 | 441.98 | 441.98 | 442.10 | 5/5/0/0 |
| baseline | 100 changed | stopped_agents:cumulative | 5 | 769.65 | 770.62 | 770.62 | 818.33 | 5/5/5/0 |
| baseline | 100 changed | foreground:completion | 8 | 734.19 | 834.47 | 834.47 | 839.52 | 5/5/5/0 |
| baseline | 100 mixed | stopped_agents:cumulative | 5 | 581.34 | 583.39 | 583.39 | 642.28 | 5/5/5/0 |
| baseline | 100 mixed | foreground:completion | 8 | 633.61 | 634.42 | 634.42 | 634.93 | 5/5/5/0 |
| baseline | 400 unchanged | stopped_agents:cumulative | 5 | 1517.48 | 1540.79 | 1540.79 | 1575.74 | 5/5/5/5 |
| baseline | 400 unchanged | foreground:completion | 6 | 1542.55 | 1642.62 | 1642.62 | 1642.79 | 5/5/5/5 |
| baseline | 400 changed | stopped_agents:cumulative | 5 | 2738.40 | 2817.93 | 2817.93 | 2847.31 | 5/5/5/5 |
| baseline | 400 changed | foreground:completion | 6 | 2736.18 | 2837.22 | 2837.22 | 2940.21 | 5/5/5/5 |
| baseline | 400 mixed | stopped_agents:cumulative | 5 | 2132.55 | 2143.46 | 2143.46 | 2162.54 | 5/5/5/5 |
| baseline | 400 mixed | foreground:completion | 6 | 2139.87 | 2235.92 | 2235.92 | 2236.06 | 5/5/5/5 |
| baseline | 1000 unchanged | stopped_agents:cumulative | 5 | 3759.41 | 3770.17 | 3770.17 | 3853.74 | 5/5/5/5 |
| baseline | 1000 unchanged | foreground:completion | 5 | 3845.05 | 3845.71 | 3845.71 | 3944.57 | 5/5/5/5 |
| baseline | 1000 changed | stopped_agents:cumulative | 5 | 9344.78 | 9640.07 | 9640.07 | 9949.71 | 5/5/5/5 |
| baseline | 1000 changed | foreground:completion | 5 | 9442.75 | 9740.61 | 9740.61 | 10042.73 | 5/5/5/5 |
| baseline | 1000 mixed | stopped_agents:cumulative | 5 | 7302.57 | 7335.62 | 7335.62 | 7479.19 | 5/5/5/5 |
| baseline | 1000 mixed | foreground:completion | 6 | 6839.43 | 7339.97 | 7339.97 | 7539.44 | 5/5/5/5 |
| candidate | 10 unchanged | stopped_agents:cumulative | 5 | 1.01 | 1.05 | 1.05 | 1.08 | 0/0/0/0 |
| candidate | 10 unchanged | foreground:completion | 2 | 5.46 | 5.46 | 5.46 | 5.97 | 0/0/0/0 |
| candidate | 10 changed | stopped_agents:cumulative | 5 | 15.03 | 15.54 | 15.54 | 15.58 | 0/0/0/0 |
| candidate | 10 changed | foreground:completion | 5 | 22.71 | 22.74 | 22.74 | 22.99 | 0/0/0/0 |
| candidate | 10 mixed | stopped_agents:cumulative | 5 | 14.81 | 15.05 | 15.05 | 23.87 | 0/0/0/0 |
| candidate | 10 mixed | foreground:completion | 6 | 23.18 | 23.36 | 23.36 | 23.40 | 0/0/0/0 |
| candidate | 100 unchanged | stopped_agents:cumulative | 5 | 1.78 | 1.86 | 1.86 | 2.63 | 0/0/0/0 |
| candidate | 100 unchanged | foreground:completion | 2 | 5.41 | 5.41 | 5.41 | 7.72 | 0/0/0/0 |
| candidate | 100 changed | stopped_agents:cumulative | 5 | 36.23 | 36.63 | 36.63 | 44.48 | 0/0/0/0 |
| candidate | 100 changed | foreground:completion | 5 | 58.22 | 58.37 | 58.37 | 58.39 | 0/0/0/0 |
| candidate | 100 mixed | stopped_agents:cumulative | 5 | 30.68 | 31.80 | 31.80 | 32.07 | 0/0/0/0 |
| candidate | 100 mixed | foreground:completion | 5 | 37.57 | 37.60 | 37.60 | 37.60 | 0/0/0/0 |
| candidate | 400 unchanged | stopped_agents:cumulative | 5 | 15.79 | 15.84 | 15.84 | 16.07 | 0/0/0/0 |
| candidate | 400 unchanged | foreground:completion | 5 | 8.30 | 13.40 | 13.40 | 13.42 | 0/0/0/0 |
| candidate | 400 changed | stopped_agents:cumulative | 5 | 129.24 | 131.92 | 131.92 | 132.36 | 5/0/0/0 |
| candidate | 400 changed | foreground:completion | 5 | 182.89 | 183.22 | 183.22 | 183.71 | 5/0/0/0 |
| candidate | 400 mixed | stopped_agents:cumulative | 5 | 143.00 | 148.24 | 148.24 | 165.06 | 5/0/0/0 |
| candidate | 400 mixed | foreground:completion | 9 | 83.66 | 134.80 | 134.80 | 186.68 | 4/0/0/0 |
| candidate | 1000 unchanged | stopped_agents:cumulative | 5 | 15.50 | 15.68 | 15.68 | 24.38 | 0/0/0/0 |
| candidate | 1000 unchanged | foreground:completion | 6 | 22.78 | 23.34 | 23.34 | 28.06 | 0/0/0/0 |
| candidate | 1000 changed | stopped_agents:cumulative | 5 | 313.23 | 318.23 | 318.23 | 322.94 | 5/5/0/0 |
| candidate | 1000 changed | foreground:completion | 6 | 333.64 | 334.05 | 334.05 | 334.95 | 5/5/0/0 |
| candidate | 1000 mixed | stopped_agents:cumulative | 5 | 285.92 | 289.40 | 289.40 | 292.09 | 5/5/0/0 |
| candidate | 1000 mixed | foreground:completion | 5 | 333.68 | 333.85 | 333.85 | 333.94 | 5/5/0/0 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | 10 unchanged | UPDATE=55, metadata_select=450, SELECT=450 | 5 | 0.08 |
| baseline | 10 changed | UPDATE=155, metadata_select=850, SELECT=850 | 5 | 24.63 |
| baseline | 10 mixed | UPDATE=105, metadata_select=650, SELECT=650 | 5 | 25.70 |
| baseline | 100 unchanged | UPDATE=505, metadata_select=4500, SELECT=4500 | 5 | 0.08 |
| baseline | 100 changed | UPDATE=1505, metadata_select=8500, SELECT=8500 | 5 | 66.52 |
| baseline | 100 mixed | UPDATE=1005, metadata_select=6500, SELECT=6500 | 5 | 54.80 |
| baseline | 400 unchanged | UPDATE=2005, metadata_select=18000, SELECT=18000 | 5 | 0.08 |
| baseline | 400 changed | UPDATE=6005, metadata_select=34000, SELECT=34000 | 5 | 62.48 |
| baseline | 400 mixed | UPDATE=4005, metadata_select=26000, SELECT=26000 | 5 | 66.82 |
| baseline | 1000 unchanged | UPDATE=5005, metadata_select=45000, SELECT=45000 | 5 | 0.08 |
| baseline | 1000 changed | UPDATE=15005, metadata_select=85000, SELECT=85000 | 5 | 35.33 |
| baseline | 1000 mixed | UPDATE=10005, metadata_select=65000, SELECT=65000 | 5 | 55.82 |
| candidate | 10 unchanged | UPDATE=5, metadata_select=5, SELECT=5 | 5 | 0.10 |
| candidate | 10 changed | UPDATE=65, metadata_select=85, SELECT=85 | 5 | 22.02 |
| candidate | 10 mixed | UPDATE=40, metadata_select=85, SELECT=85 | 5 | 24.75 |
| candidate | 100 unchanged | UPDATE=10, metadata_select=10, SELECT=10 | 10 | 0.14 |
| candidate | 100 changed | UPDATE=530, metadata_select=170, SELECT=170 | 10 | 50.87 |
| candidate | 100 mixed | UPDATE=280, metadata_select=170, SELECT=170 | 10 | 42.42 |
| candidate | 400 unchanged | UPDATE=35, metadata_select=35, SELECT=35 | 35 | 0.48 |
| candidate | 400 changed | UPDATE=2105, metadata_select=595, SELECT=595 | 35 | 157.20 |
| candidate | 400 mixed | UPDATE=1105, metadata_select=595, SELECT=595 | 35 | 223.69 |
| candidate | 1000 unchanged | UPDATE=80, metadata_select=80, SELECT=80 | 80 | 1.03 |
| candidate | 1000 changed | UPDATE=5240, metadata_select=1360, SELECT=1360 | 80 | 386.87 |
| candidate | 1000 mixed | UPDATE=2740, metadata_select=1360, SELECT=1360 | 80 | 366.15 |


## Facts retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1000 facts / atomic | facts_atomic:sqlite_writer_hold | 5 | 9.03 | 13.56 | 13.56 | 15.28 | 0/0/0/0 |
| baseline | 10000 facts / atomic | facts_atomic:sqlite_writer_hold | 5 | 82.53 | 82.84 | 82.84 | 83.54 | 0/0/0/0 |
| baseline | 40000 facts / atomic | facts_atomic:sqlite_writer_hold | 5 | 329.42 | 331.53 | 331.53 | 340.84 | 5/5/0/0 |
| baseline | 75000 facts / atomic | facts_atomic:sqlite_writer_hold | 5 | 589.97 | 590.49 | 590.49 | 718.56 | 5/5/5/0 |
| candidate | 1000 facts / 128 rows | facts_delete:sqlite_writer_hold | 40 | 5.31 | 6.01 | 6.12 | 6.35 | 0/0/0/0 |
| candidate | 1000 facts / 128 rows | facts_insert:sqlite_writer_hold | 40 | 6.39 | 14.46 | 15.74 | 19.81 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_delete:sqlite_writer_hold | 395 | 5.72 | 7.08 | 12.59 | 17.30 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_insert:sqlite_writer_hold | 395 | 6.67 | 10.33 | 19.41 | 22.94 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts_delete:sqlite_writer_hold | 1565 | 6.64 | 12.23 | 18.74 | 24.79 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts_insert:sqlite_writer_hold | 1565 | 7.03 | 13.15 | 22.05 | 43.86 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts_delete:sqlite_writer_hold | 2930 | 6.36 | 12.67 | 19.31 | 48.65 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts_insert:sqlite_writer_hold | 2930 | 7.12 | 15.29 | 25.16 | 35.20 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1000 facts / atomic | facts_atomic:sql:DELETE | 5 | 1.10 | 1.10 | 1.10 | 1.10 | 0/0/0/0 |
| baseline | 1000 facts / atomic | facts_atomic:sql:INSERT | 5 | 2.38 | 2.38 | 2.38 | 2.42 | 0/0/0/0 |
| baseline | 1000 facts / atomic | facts_atomic:commit | 5 | 5.48 | 10.07 | 10.07 | 11.75 | 0/0/0/0 |
| baseline | 1000 facts / atomic | facts:cumulative | 5 | 9.15 | 13.66 | 13.66 | 15.39 | 0/0/0/0 |
| baseline | 1000 facts / atomic | foreground:completion | 5 | 23.00 | 26.93 | 26.93 | 27.16 | 0/0/0/0 |
| baseline | 10000 facts / atomic | facts_atomic:sql:DELETE | 5 | 10.04 | 10.06 | 10.06 | 10.20 | 0/0/0/0 |
| baseline | 10000 facts / atomic | facts_atomic:sql:INSERT | 5 | 27.51 | 27.82 | 27.82 | 29.35 | 0/0/0/0 |
| baseline | 10000 facts / atomic | facts_atomic:commit | 5 | 44.72 | 44.86 | 44.86 | 45.89 | 0/0/0/0 |
| baseline | 10000 facts / atomic | facts:cumulative | 5 | 82.63 | 82.94 | 82.94 | 83.63 | 0/0/0/0 |
| baseline | 10000 facts / atomic | foreground:completion | 5 | 91.14 | 92.21 | 92.21 | 96.43 | 0/0/0/0 |
| baseline | 40000 facts / atomic | facts_atomic:sql:DELETE | 5 | 70.03 | 74.44 | 74.44 | 76.01 | 0/0/0/0 |
| baseline | 40000 facts / atomic | facts_atomic:sql:INSERT | 5 | 153.52 | 154.79 | 154.79 | 155.26 | 5/0/0/0 |
| baseline | 40000 facts / atomic | facts_atomic:commit | 5 | 106.87 | 107.53 | 107.53 | 111.04 | 5/0/0/0 |
| baseline | 40000 facts / atomic | facts:cumulative | 5 | 329.52 | 331.63 | 331.63 | 340.98 | 5/5/0/0 |
| baseline | 40000 facts / atomic | foreground:completion | 5 | 338.17 | 338.67 | 338.67 | 340.65 | 5/5/0/0 |
| baseline | 75000 facts / atomic | facts_atomic:sql:DELETE | 5 | 91.35 | 91.44 | 91.44 | 94.14 | 0/0/0/0 |
| baseline | 75000 facts / atomic | facts_atomic:sql:INSERT | 5 | 296.00 | 309.23 | 309.23 | 311.15 | 5/5/0/0 |
| baseline | 75000 facts / atomic | facts_atomic:commit | 5 | 185.09 | 202.51 | 202.51 | 335.46 | 5/1/0/0 |
| baseline | 75000 facts / atomic | facts:cumulative | 5 | 590.10 | 590.61 | 590.61 | 718.70 | 5/5/5/0 |
| baseline | 75000 facts / atomic | foreground:completion | 5 | 587.38 | 588.28 | 588.28 | 724.54 | 5/5/5/0 |
| candidate | 1000 facts / 128 rows | facts_delete:sql:DELETE | 40 | 0.27 | 0.96 | 0.97 | 1.03 | 0/0/0/0 |
| candidate | 1000 facts / 128 rows | facts_delete:commit | 40 | 4.95 | 5.36 | 5.44 | 5.59 | 0/0/0/0 |
| candidate | 1000 facts / 128 rows | facts_insert:sql:INSERT | 40 | 0.88 | 1.32 | 1.32 | 2.06 | 0/0/0/0 |
| candidate | 1000 facts / 128 rows | facts_insert:commit | 40 | 5.30 | 13.28 | 14.56 | 18.59 | 0/0/0/0 |
| candidate | 1000 facts / 128 rows | facts:cumulative | 5 | 147.50 | 180.99 | 180.99 | 203.49 | 5/0/0/0 |
| candidate | 1000 facts / 128 rows | foreground:completion | 25 | 6.45 | 84.61 | 108.49 | 114.68 | 2/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_delete:sql:DELETE | 395 | 0.56 | 1.21 | 1.45 | 1.74 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_delete:commit | 395 | 4.83 | 5.67 | 12.12 | 16.34 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_insert:sql:INSERT | 395 | 1.20 | 1.48 | 1.78 | 2.04 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts_insert:commit | 395 | 5.21 | 9.74 | 18.61 | 21.52 | 0/0/0/0 |
| candidate | 10000 facts / 128 rows | facts:cumulative | 5 | 1215.22 | 1247.17 | 1247.17 | 1282.44 | 5/5/5/5 |
| candidate | 10000 facts / 128 rows | foreground:completion | 42 | 23.87 | 535.74 | 744.41 | 836.27 | 13/8/6/0 |
| candidate | 40000 facts / 128 rows | facts_delete:sql:DELETE | 1565 | 1.08 | 1.52 | 1.60 | 2.22 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts_delete:commit | 1565 | 5.28 | 10.86 | 17.56 | 23.61 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts_insert:sql:INSERT | 1565 | 1.45 | 1.75 | 1.91 | 2.65 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts_insert:commit | 1565 | 5.30 | 11.15 | 20.93 | 42.35 | 0/0/0/0 |
| candidate | 40000 facts / 128 rows | facts:cumulative | 5 | 5769.26 | 6001.31 | 6001.31 | 6411.03 | 5/5/5/5 |
| candidate | 40000 facts / 128 rows | foreground:completion | 419 | 11.78 | 334.23 | 835.93 | 1838.71 | 39/22/14/4 |
| candidate | 75000 facts / 128 rows | facts_delete:sql:DELETE | 2930 | 1.03 | 1.50 | 1.66 | 2.22 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts_delete:commit | 2930 | 5.12 | 11.34 | 17.62 | 47.43 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts_insert:sql:INSERT | 2930 | 1.46 | 1.83 | 2.12 | 3.03 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts_insert:commit | 2930 | 5.35 | 13.16 | 23.03 | 33.18 | 0/0/0/0 |
| candidate | 75000 facts / 128 rows | facts:cumulative | 5 | 11068.42 | 11565.67 | 11565.67 | 11575.87 | 5/5/5/5 |
| candidate | 75000 facts / 128 rows | foreground:completion | 771 | 12.67 | 334.42 | 935.08 | 2638.67 | 85/42/23/8 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | 1000 facts / atomic | UPDATE=5, DELETE=5, INSERT=5 | 5 | 37.33 |
| baseline | 10000 facts / atomic | UPDATE=5, DELETE=5, INSERT=5 | 5 | 223.69 |
| baseline | 40000 facts / atomic | UPDATE=5, DELETE=5, INSERT=5 | 5 | 526.06 |
| baseline | 75000 facts / atomic | UPDATE=5, DELETE=5, INSERT=5 | 5 | 1034.69 |
| candidate | 1000 facts / 128 rows | metadata_select=80, SELECT=80, UPDATE=80, DELETE=40, INSERT=40 | 80 | 463.27 |
| candidate | 10000 facts / 128 rows | metadata_select=790, SELECT=790, UPDATE=790, DELETE=395, INSERT=395 | 790 | 4244.18 |
| candidate | 40000 facts / 128 rows | metadata_select=3130, SELECT=3130, UPDATE=3130, DELETE=1565, INSERT=1565 | 3130 | 18592.94 |
| candidate | 75000 facts / 128 rows | metadata_select=5860, SELECT=5860, UPDATE=5860, DELETE=2930, INSERT=2930 | 5860 | 35883.24 |


## Maintenance retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 333000 B / shared | maintenance_publish:sqlite_writer_hold | 5 | 353.84 | 359.14 | 359.14 | 427.13 | 5/5/0/0 |
| baseline | 333000 B / overlap | maintenance_publish:sqlite_writer_hold | 5 | 351.56 | 359.44 | 359.44 | 367.34 | 5/5/0/0 |
| baseline | 333000 B / distinct | maintenance_publish:sqlite_writer_hold | 5 | 339.88 | 340.33 | 340.33 | 346.36 | 5/5/0/0 |
| baseline | 8388600 B / shared | maintenance_publish:sqlite_writer_hold | 15 | 179.18 | 183.94 | 183.94 | 185.24 | 10/0/0/0 |
| baseline | 8388600 B / overlap | maintenance_publish:sqlite_writer_hold | 15 | 178.94 | 196.73 | 196.73 | 201.96 | 10/0/0/0 |
| baseline | 8388600 B / distinct | maintenance_publish:sqlite_writer_hold | 15 | 173.27 | 189.81 | 189.81 | 192.31 | 10/0/0/0 |
| baseline | 12592812 B / shared | maintenance_publish:sqlite_writer_hold | 10 | 26.57 | 339.56 | 339.56 | 358.67 | 5/5/0/0 |
| baseline | 12592812 B / overlap | maintenance_publish:sqlite_writer_hold | 10 | 26.71 | 346.91 | 346.91 | 356.61 | 5/5/0/0 |
| baseline | 12592812 B / distinct | maintenance_publish:sqlite_writer_hold | 10 | 26.57 | 345.54 | 345.54 | 353.82 | 5/5/0/0 |
| candidate | 333000 B / shared | maintenance_publish:sqlite_writer_hold | 5 | 33.60 | 43.97 | 43.97 | 44.46 | 0/0/0/0 |
| candidate | 333000 B / overlap | maintenance_publish:sqlite_writer_hold | 5 | 44.73 | 45.86 | 45.86 | 46.77 | 0/0/0/0 |
| candidate | 333000 B / distinct | maintenance_publish:sqlite_writer_hold | 5 | 33.63 | 35.27 | 35.27 | 35.66 | 0/0/0/0 |
| candidate | 8388600 B / shared | maintenance_publish:sqlite_writer_hold | 15 | 23.49 | 32.77 | 32.77 | 43.15 | 0/0/0/0 |
| candidate | 8388600 B / overlap | maintenance_publish:sqlite_writer_hold | 15 | 22.47 | 26.57 | 26.57 | 41.83 | 0/0/0/0 |
| candidate | 8388600 B / distinct | maintenance_publish:sqlite_writer_hold | 15 | 22.31 | 26.04 | 26.04 | 42.92 | 0/0/0/0 |
| candidate | 12592812 B / shared | maintenance_publish:sqlite_writer_hold | 10 | 57.37 | 186.24 | 186.24 | 187.77 | 5/0/0/0 |
| candidate | 12592812 B / overlap | maintenance_publish:sqlite_writer_hold | 10 | 50.19 | 272.37 | 272.37 | 275.76 | 5/4/0/0 |
| candidate | 12592812 B / distinct | maintenance_publish:sqlite_writer_hold | 10 | 96.00 | 150.11 | 150.11 | 197.05 | 5/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 333000 B / shared | maintenance_publish:commit | 5 | 12.64 | 12.65 | 12.65 | 13.03 | 0/0/0/0 |
| baseline | 333000 B / shared | maintenance:cumulative | 5 | 387.81 | 397.99 | 397.99 | 448.92 | 5/5/0/0 |
| baseline | 333000 B / shared | foreground:completion | 16 | 4.75 | 435.19 | 435.19 | 438.63 | 5/5/0/0 |
| baseline | 333000 B / overlap | maintenance_publish:commit | 5 | 12.72 | 12.80 | 12.80 | 13.15 | 0/0/0/0 |
| baseline | 333000 B / overlap | maintenance:cumulative | 5 | 385.78 | 397.92 | 397.92 | 410.02 | 5/5/0/0 |
| baseline | 333000 B / overlap | foreground:completion | 19 | 4.84 | 434.93 | 434.93 | 435.11 | 5/5/0/0 |
| baseline | 333000 B / distinct | maintenance_publish:commit | 5 | 12.85 | 12.86 | 12.86 | 12.98 | 0/0/0/0 |
| baseline | 333000 B / distinct | maintenance:cumulative | 5 | 371.07 | 371.98 | 371.98 | 379.93 | 5/5/0/0 |
| baseline | 333000 B / distinct | foreground:completion | 16 | 6.19 | 434.28 | 434.28 | 434.39 | 5/5/0/0 |
| baseline | 8388600 B / shared | maintenance_publish:commit | 15 | 4.79 | 12.39 | 12.39 | 12.45 | 0/0/0/0 |
| baseline | 8388600 B / shared | maintenance:cumulative | 5 | 455.80 | 464.68 | 464.68 | 466.28 | 5/5/0/0 |
| baseline | 8388600 B / shared | foreground:completion | 32 | 4.65 | 233.53 | 233.89 | 234.04 | 10/0/0/0 |
| baseline | 8388600 B / overlap | maintenance_publish:commit | 15 | 4.87 | 5.20 | 5.20 | 7.11 | 0/0/0/0 |
| baseline | 8388600 B / overlap | maintenance:cumulative | 5 | 462.51 | 469.35 | 469.35 | 483.72 | 5/5/0/0 |
| baseline | 8388600 B / overlap | foreground:completion | 26 | 4.67 | 234.10 | 433.56 | 434.03 | 8/2/0/0 |
| baseline | 8388600 B / distinct | maintenance_publish:commit | 15 | 4.85 | 9.60 | 9.60 | 23.27 | 0/0/0/0 |
| baseline | 8388600 B / distinct | maintenance:cumulative | 5 | 461.49 | 467.86 | 467.86 | 471.92 | 5/5/0/0 |
| baseline | 8388600 B / distinct | foreground:completion | 27 | 4.96 | 234.05 | 434.30 | 533.85 | 8/2/1/0 |
| baseline | 12592812 B / shared | maintenance_publish:commit | 10 | 4.65 | 5.20 | 5.20 | 12.55 | 0/0/0/0 |
| baseline | 12592812 B / shared | maintenance:cumulative | 5 | 467.50 | 473.77 | 473.77 | 484.82 | 5/5/0/0 |
| baseline | 12592812 B / shared | foreground:completion | 39 | 4.57 | 333.72 | 334.28 | 434.84 | 5/5/0/0 |
| baseline | 12592812 B / overlap | maintenance_publish:commit | 10 | 4.95 | 12.65 | 12.65 | 12.66 | 0/0/0/0 |
| baseline | 12592812 B / overlap | maintenance:cumulative | 5 | 472.58 | 475.60 | 475.60 | 484.18 | 5/5/0/0 |
| baseline | 12592812 B / overlap | foreground:completion | 37 | 4.99 | 434.59 | 434.61 | 434.82 | 5/5/0/0 |
| baseline | 12592812 B / distinct | maintenance_publish:commit | 10 | 4.87 | 12.53 | 12.53 | 12.60 | 0/0/0/0 |
| baseline | 12592812 B / distinct | maintenance:cumulative | 5 | 460.45 | 467.88 | 467.88 | 480.99 | 5/5/0/0 |
| baseline | 12592812 B / distinct | foreground:completion | 36 | 4.58 | 334.06 | 434.38 | 434.72 | 5/5/0/0 |
| candidate | 333000 B / shared | maintenance_publish:commit | 5 | 5.06 | 5.22 | 5.22 | 5.26 | 0/0/0/0 |
| candidate | 333000 B / shared | maintenance:cumulative | 5 | 86.44 | 88.56 | 88.56 | 88.72 | 0/0/0/0 |
| candidate | 333000 B / shared | foreground:completion | 19 | 8.94 | 38.04 | 38.04 | 38.52 | 0/0/0/0 |
| candidate | 333000 B / overlap | maintenance_publish:commit | 5 | 5.57 | 5.76 | 5.76 | 11.07 | 0/0/0/0 |
| candidate | 333000 B / overlap | maintenance:cumulative | 5 | 89.84 | 90.46 | 90.46 | 94.39 | 0/0/0/0 |
| candidate | 333000 B / overlap | foreground:completion | 20 | 5.06 | 42.98 | 42.98 | 58.41 | 0/0/0/0 |
| candidate | 333000 B / distinct | maintenance_publish:commit | 5 | 5.44 | 7.47 | 7.47 | 7.49 | 0/0/0/0 |
| candidate | 333000 B / distinct | maintenance:cumulative | 5 | 80.29 | 82.59 | 82.59 | 83.66 | 0/0/0/0 |
| candidate | 333000 B / distinct | foreground:completion | 19 | 4.80 | 38.11 | 38.11 | 38.34 | 0/0/0/0 |
| candidate | 8388600 B / shared | maintenance_publish:commit | 15 | 5.57 | 10.01 | 10.01 | 26.51 | 0/0/0/0 |
| candidate | 8388600 B / shared | maintenance:cumulative | 5 | 144.91 | 144.96 | 144.96 | 176.02 | 5/0/0/0 |
| candidate | 8388600 B / shared | foreground:completion | 32 | 5.54 | 39.09 | 41.00 | 60.54 | 0/0/0/0 |
| candidate | 8388600 B / overlap | maintenance_publish:commit | 15 | 4.88 | 9.77 | 9.77 | 24.38 | 0/0/0/0 |
| candidate | 8388600 B / overlap | maintenance:cumulative | 5 | 133.87 | 135.35 | 135.35 | 135.48 | 5/0/0/0 |
| candidate | 8388600 B / overlap | foreground:completion | 30 | 5.18 | 38.37 | 38.57 | 44.73 | 0/0/0/0 |
| candidate | 8388600 B / distinct | maintenance_publish:commit | 15 | 5.03 | 7.63 | 7.63 | 24.63 | 0/0/0/0 |
| candidate | 8388600 B / distinct | maintenance:cumulative | 5 | 135.81 | 144.55 | 144.55 | 151.76 | 5/0/0/0 |
| candidate | 8388600 B / distinct | foreground:completion | 30 | 5.25 | 38.25 | 39.17 | 50.24 | 0/0/0/0 |
| candidate | 12592812 B / shared | maintenance_publish:commit | 10 | 31.56 | 149.17 | 149.17 | 151.64 | 5/0/0/0 |
| candidate | 12592812 B / shared | maintenance:cumulative | 5 | 312.25 | 342.56 | 342.56 | 348.54 | 5/5/0/0 |
| candidate | 12592812 B / shared | foreground:completion | 39 | 5.74 | 134.16 | 175.36 | 184.85 | 5/0/0/0 |
| candidate | 12592812 B / overlap | maintenance_publish:commit | 10 | 24.17 | 235.39 | 235.39 | 239.67 | 4/0/0/0 |
| candidate | 12592812 B / overlap | maintenance:cumulative | 5 | 416.04 | 416.59 | 416.59 | 449.33 | 5/5/0/0 |
| candidate | 12592812 B / overlap | foreground:completion | 42 | 4.56 | 261.14 | 272.86 | 282.58 | 5/4/0/0 |
| candidate | 12592812 B / distinct | maintenance_publish:commit | 10 | 69.19 | 113.31 | 113.31 | 161.50 | 3/0/0/0 |
| candidate | 12592812 B / distinct | maintenance:cumulative | 5 | 308.88 | 353.66 | 353.66 | 356.79 | 5/5/0/0 |
| candidate | 12592812 B / distinct | foreground:completion | 40 | 5.64 | 133.77 | 134.29 | 172.33 | 5/0/0/0 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | 333000 B / shared | metadata_select=3020, SELECT=4025, payload_select=1005, UPDATE=505 | 5 | 61.74 |
| baseline | 333000 B / overlap | metadata_select=3020, SELECT=4025, payload_select=1005, UPDATE=505 | 5 | 63.87 |
| baseline | 333000 B / distinct | metadata_select=3020, SELECT=4025, payload_select=1005, UPDATE=505 | 5 | 63.82 |
| baseline | 8388600 B / shared | metadata_select=3060, SELECT=4075, payload_select=1015, UPDATE=515 | 15 | 100.16 |
| baseline | 8388600 B / overlap | metadata_select=3060, SELECT=4075, payload_select=1015, UPDATE=515 | 15 | 74.39 |
| baseline | 8388600 B / distinct | metadata_select=3060, SELECT=4075, payload_select=1015, UPDATE=515 | 15 | 102.90 |
| baseline | 12592812 B / shared | metadata_select=3040, SELECT=4050, payload_select=1010, UPDATE=510 | 10 | 54.61 |
| baseline | 12592812 B / overlap | metadata_select=3040, SELECT=4050, payload_select=1010, UPDATE=510 | 10 | 84.82 |
| baseline | 12592812 B / distinct | metadata_select=3040, SELECT=4050, payload_select=1010, UPDATE=510 | 10 | 83.76 |
| candidate | 333000 B / shared | metadata_select=25, SELECT=35, payload_select=10, UPDATE=510 | 5 | 25.08 |
| candidate | 333000 B / overlap | metadata_select=25, SELECT=35, payload_select=10, UPDATE=510 | 5 | 32.37 |
| candidate | 333000 B / distinct | metadata_select=25, SELECT=35, payload_select=10, UPDATE=510 | 5 | 30.98 |
| candidate | 8388600 B / shared | metadata_select=75, SELECT=105, payload_select=30, UPDATE=530 | 15 | 117.01 |
| candidate | 8388600 B / overlap | metadata_select=75, SELECT=105, payload_select=30, UPDATE=530 | 15 | 99.96 |
| candidate | 8388600 B / distinct | metadata_select=75, SELECT=105, payload_select=30, UPDATE=530 | 15 | 98.95 |
| candidate | 12592812 B / shared | metadata_select=50, SELECT=70, payload_select=20, UPDATE=520 | 10 | 727.73 |
| candidate | 12592812 B / overlap | metadata_select=50, SELECT=70, payload_select=20, UPDATE=520 | 10 | 1061.11 |
| candidate | 12592812 B / distinct | metadata_select=50, SELECT=70, payload_select=20, UPDATE=520 | 10 | 724.16 |


## Validation retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1000 / warm | validation_final:sqlite_writer_hold | 5 | 30.06 | 30.12 | 30.12 | 32.03 | 0/0/0/0 |
| baseline | 1000 / reopened | validation_final:sqlite_writer_hold | 5 | 23.52 | 25.05 | 25.05 | 25.14 | 0/0/0/0 |
| baseline | 10000 / warm | validation_final:sqlite_writer_hold | 5 | 35.98 | 36.09 | 36.09 | 38.08 | 0/0/0/0 |
| baseline | 10000 / reopened | validation_final:sqlite_writer_hold | 5 | 33.63 | 34.68 | 34.68 | 35.25 | 0/0/0/0 |
| baseline | 100000 / warm | validation_final:sqlite_writer_hold | 5 | 111.97 | 115.68 | 115.68 | 115.77 | 5/0/0/0 |
| baseline | 100000 / reopened | validation_final:sqlite_writer_hold | 5 | 121.17 | 124.73 | 124.73 | 125.46 | 5/0/0/0 |
| baseline | 200000 / warm | validation_final:sqlite_writer_hold | 5 | 190.54 | 190.77 | 190.77 | 203.84 | 5/0/0/0 |
| baseline | 200000 / reopened | validation_final:sqlite_writer_hold | 5 | 189.81 | 190.94 | 190.94 | 191.72 | 5/0/0/0 |
| candidate | 1000 / warm | validation_publish:sqlite_writer_hold | 25 | 7.17 | 7.64 | 7.82 | 18.36 | 0/0/0/0 |
| candidate | 1000 / warm | validation_final:sqlite_writer_hold | 5 | 22.00 | 22.14 | 22.14 | 22.16 | 0/0/0/0 |
| candidate | 1000 / reopened | validation_publish:sqlite_writer_hold | 25 | 7.31 | 7.51 | 7.68 | 15.49 | 0/0/0/0 |
| candidate | 1000 / reopened | validation_final:sqlite_writer_hold | 5 | 22.13 | 22.38 | 22.38 | 28.14 | 0/0/0/0 |
| candidate | 10000 / warm | validation_publish:sqlite_writer_hold | 205 | 7.19 | 7.72 | 8.05 | 10.90 | 0/0/0/0 |
| candidate | 10000 / warm | validation_final:sqlite_writer_hold | 5 | 22.20 | 22.81 | 22.81 | 24.14 | 0/0/0/0 |
| candidate | 10000 / reopened | validation_publish:sqlite_writer_hold | 205 | 6.92 | 7.83 | 14.19 | 18.01 | 0/0/0/0 |
| candidate | 10000 / reopened | validation_final:sqlite_writer_hold | 5 | 22.15 | 22.55 | 22.55 | 26.14 | 0/0/0/0 |
| candidate | 100000 / warm | validation_publish:sqlite_writer_hold | 1960 | 7.51 | 7.91 | 10.03 | 22.28 | 0/0/0/0 |
| candidate | 100000 / warm | validation_final:sqlite_writer_hold | 5 | 22.73 | 23.01 | 23.01 | 26.84 | 0/0/0/0 |
| candidate | 100000 / reopened | validation_publish:sqlite_writer_hold | 1960 | 7.49 | 7.91 | 12.93 | 21.58 | 0/0/0/0 |
| candidate | 100000 / reopened | validation_final:sqlite_writer_hold | 5 | 22.47 | 22.63 | 22.63 | 24.10 | 0/0/0/0 |
| candidate | 200000 / warm | validation_publish:sqlite_writer_hold | 3915 | 7.48 | 7.88 | 9.14 | 22.22 | 0/0/0/0 |
| candidate | 200000 / warm | validation_final:sqlite_writer_hold | 5 | 22.66 | 22.72 | 22.72 | 23.07 | 0/0/0/0 |
| candidate | 200000 / reopened | validation_publish:sqlite_writer_hold | 3915 | 7.52 | 7.91 | 10.73 | 27.14 | 0/0/0/0 |
| candidate | 200000 / reopened | validation_final:sqlite_writer_hold | 5 | 22.62 | 22.98 | 22.98 | 24.35 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1000 / warm | validation:cumulative | 5 | 30.22 | 32.14 | 32.14 | 44.17 | 0/0/0/0 |
| baseline | 1000 / warm | foreground:completion | 6 | 38.40 | 38.55 | 38.55 | 41.80 | 0/0/0/0 |
| baseline | 1000 / reopened | validation:cumulative | 5 | 23.63 | 33.63 | 33.63 | 33.72 | 0/0/0/0 |
| baseline | 1000 / reopened | foreground:completion | 7 | 23.58 | 39.06 | 39.06 | 39.10 | 0/0/0/0 |
| baseline | 10000 / warm | validation:cumulative | 5 | 36.21 | 38.19 | 38.19 | 54.53 | 0/0/0/0 |
| baseline | 10000 / warm | foreground:completion | 6 | 57.91 | 57.99 | 57.99 | 58.00 | 0/0/0/0 |
| baseline | 10000 / reopened | validation:cumulative | 5 | 41.71 | 52.42 | 52.42 | 52.52 | 0/0/0/0 |
| baseline | 10000 / reopened | foreground:completion | 8 | 37.92 | 57.96 | 57.96 | 58.00 | 0/0/0/0 |
| baseline | 100000 / warm | validation:cumulative | 5 | 112.08 | 115.79 | 115.79 | 115.88 | 5/0/0/0 |
| baseline | 100000 / warm | foreground:completion | 5 | 133.29 | 133.34 | 133.34 | 133.81 | 5/0/0/0 |
| baseline | 100000 / reopened | validation:cumulative | 5 | 140.43 | 144.13 | 144.13 | 145.29 | 5/0/0/0 |
| baseline | 100000 / reopened | foreground:completion | 9 | 133.25 | 133.36 | 133.36 | 133.40 | 5/0/0/0 |
| baseline | 200000 / warm | validation:cumulative | 5 | 190.66 | 203.96 | 203.96 | 210.72 | 5/0/0/0 |
| baseline | 200000 / warm | foreground:completion | 6 | 233.58 | 234.03 | 234.03 | 245.53 | 5/0/0/0 |
| baseline | 200000 / reopened | validation:cumulative | 5 | 189.91 | 191.04 | 191.04 | 191.82 | 5/0/0/0 |
| baseline | 200000 / reopened | foreground:completion | 5 | 233.86 | 233.89 | 233.89 | 234.02 | 5/0/0/0 |
| candidate | 1000 / warm | validation:cumulative | 5 | 126.93 | 128.12 | 128.12 | 135.37 | 4/0/0/0 |
| candidate | 1000 / warm | foreground:completion | 27 | 8.25 | 38.47 | 38.52 | 38.65 | 0/0/0/0 |
| candidate | 1000 / reopened | validation:cumulative | 5 | 117.95 | 117.99 | 117.99 | 123.28 | 5/0/0/0 |
| candidate | 1000 / reopened | foreground:completion | 28 | 7.98 | 38.54 | 38.54 | 38.62 | 0/0/0/0 |
| candidate | 10000 / warm | validation:cumulative | 5 | 643.72 | 691.53 | 691.53 | 753.85 | 5/5/5/0 |
| candidate | 10000 / warm | foreground:completion | 167 | 6.80 | 22.66 | 57.73 | 116.80 | 1/0/0/0 |
| candidate | 10000 / reopened | validation:cumulative | 5 | 647.33 | 658.30 | 658.30 | 703.53 | 5/5/4/0 |
| candidate | 10000 / reopened | foreground:completion | 130 | 7.46 | 37.68 | 38.79 | 434.23 | 2/1/0/0 |
| candidate | 100000 / warm | validation:cumulative | 5 | 6431.05 | 6618.00 | 6618.00 | 6637.93 | 5/5/5/5 |
| candidate | 100000 / warm | foreground:completion | 1637 | 7.32 | 10.55 | 38.70 | 642.81 | 6/1/1/0 |
| candidate | 100000 / reopened | validation:cumulative | 5 | 5982.70 | 6059.83 | 6059.83 | 7268.45 | 5/5/5/5 |
| candidate | 100000 / reopened | foreground:completion | 1682 | 4.90 | 12.60 | 38.08 | 133.14 | 6/0/0/0 |
| candidate | 200000 / warm | validation:cumulative | 5 | 12361.19 | 12479.82 | 12479.82 | 12656.78 | 5/5/5/5 |
| candidate | 200000 / warm | foreground:completion | 3239 | 7.18 | 9.90 | 38.86 | 842.42 | 7/1/1/0 |
| candidate | 200000 / reopened | validation:cumulative | 5 | 12337.69 | 12951.94 | 12951.94 | 13079.54 | 5/5/5/5 |
| candidate | 200000 / reopened | foreground:completion | 3348 | 7.33 | 9.87 | 37.67 | 442.54 | 6/2/0/0 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | 1000 / warm | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 55.52 |
| baseline | 1000 / reopened | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 24.60 |
| baseline | 10000 / warm | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 52.78 |
| baseline | 10000 / reopened | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 37.84 |
| baseline | 100000 / warm | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 60.55 |
| baseline | 100000 / reopened | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 61.81 |
| baseline | 200000 / warm | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 61.48 |
| baseline | 200000 / reopened | UPDATE=40, metadata_select=180, SELECT=180, DELETE=5, INSERT=10 | 5 | 62.62 |
| candidate | 1000 / warm | metadata_select=660, SELECT=660, UPDATE=85, INSERT=5 | 30 | 23.95 |
| candidate | 1000 / reopened | metadata_select=660, SELECT=660, UPDATE=85, INSERT=5 | 30 | 31.62 |
| candidate | 10000 / warm | metadata_select=4260, SELECT=4260, UPDATE=445, INSERT=5 | 210 | 27.79 |
| candidate | 10000 / reopened | metadata_select=4260, SELECT=4260, UPDATE=445, INSERT=5 | 210 | 34.00 |
| candidate | 100000 / warm | metadata_select=39360, SELECT=39360, UPDATE=3955, INSERT=5 | 1965 | 62.42 |
| candidate | 100000 / reopened | metadata_select=39360, SELECT=39360, UPDATE=3955, INSERT=5 | 1965 | 62.33 |
| candidate | 200000 / warm | metadata_select=78460, SELECT=78460, UPDATE=7865, INSERT=5 | 3920 | 101.67 |
| candidate | 200000 / reopened | metadata_select=78460, SELECT=78460, UPDATE=7865, INSERT=5 | 3920 | 103.60 |


## Mixed retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | rep 1 | actual_coordinator_lease_hold:begin | 1 | 10.18 | 10.18 | 10.18 | 10.18 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:counts | 1 | 3.62 | 3.62 | 3.62 | 3.62 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:metadata | 1 | 4.69 | 4.69 | 4.69 | 4.69 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:overrides | 1 | 8.39 | 8.39 | 8.39 | 8.39 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:extraction | 40 | 22.23 | 42.34 | 43.20 | 51.47 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:reconciliation | 40 | 31.34 | 37.00 | 37.38 | 39.90 | 0/0/0/0 |
| baseline | rep 1 | actual_coordinator_lease_hold:final | 1 | 235.91 | 235.91 | 235.91 | 235.91 | 1/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:begin | 1 | 9.24 | 9.24 | 9.24 | 9.24 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:counts | 1 | 4.76 | 4.76 | 4.76 | 4.76 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:metadata | 1 | 9.98 | 9.98 | 9.98 | 9.98 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:overrides | 1 | 8.18 | 8.18 | 8.18 | 8.18 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:extraction | 40 | 25.78 | 45.01 | 46.68 | 56.41 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:reconciliation | 40 | 30.94 | 36.60 | 37.59 | 37.77 | 0/0/0/0 |
| baseline | rep 2 | actual_coordinator_lease_hold:final | 1 | 243.25 | 243.25 | 243.25 | 243.25 | 1/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:begin | 1 | 10.45 | 10.45 | 10.45 | 10.45 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:counts | 1 | 4.76 | 4.76 | 4.76 | 4.76 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:metadata | 1 | 5.06 | 5.06 | 5.06 | 5.06 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:overrides | 1 | 8.32 | 8.32 | 8.32 | 8.32 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:extraction | 40 | 25.12 | 42.42 | 43.76 | 49.49 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:reconciliation | 40 | 30.96 | 35.38 | 36.04 | 36.09 | 0/0/0/0 |
| baseline | rep 3 | actual_coordinator_lease_hold:final | 1 | 243.32 | 243.32 | 243.32 | 243.32 | 1/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:begin | 1 | 9.78 | 9.78 | 9.78 | 9.78 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:counts | 1 | 4.71 | 4.71 | 4.71 | 4.71 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:metadata | 1 | 3.69 | 3.69 | 3.69 | 3.69 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:overrides | 1 | 31.61 | 31.61 | 31.61 | 31.61 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:extraction | 40 | 21.82 | 45.02 | 45.83 | 53.92 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:reconciliation | 40 | 31.15 | 35.51 | 35.66 | 36.20 | 0/0/0/0 |
| baseline | rep 4 | actual_coordinator_lease_hold:final | 1 | 241.72 | 241.72 | 241.72 | 241.72 | 1/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:begin | 1 | 9.16 | 9.16 | 9.16 | 9.16 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:counts | 1 | 17.71 | 17.71 | 17.71 | 17.71 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:metadata | 1 | 3.58 | 3.58 | 3.58 | 3.58 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:overrides | 1 | 11.81 | 11.81 | 11.81 | 11.81 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:extraction | 40 | 24.59 | 42.52 | 43.27 | 45.68 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:reconciliation | 40 | 31.64 | 35.73 | 36.08 | 36.48 | 0/0/0/0 |
| baseline | rep 5 | actual_coordinator_lease_hold:final | 1 | 243.07 | 243.07 | 243.07 | 243.07 | 1/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:begin | 1 | 11.46 | 11.46 | 11.46 | 11.46 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:counts | 1 | 6.39 | 6.39 | 6.39 | 6.39 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:metadata | 1 | 4.86 | 4.86 | 4.86 | 4.86 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:overrides | 1 | 9.04 | 9.04 | 9.04 | 9.04 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:extraction | 40 | 14.34 | 15.64 | 15.81 | 49.16 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:stopped_agents | 7 | 26.14 | 26.56 | 26.56 | 27.21 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:reconciliation | 40 | 10.08 | 11.82 | 12.63 | 14.18 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_stage | 1 | 3.72 | 3.72 | 3.72 | 3.72 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_delete | 313 | 9.53 | 14.40 | 19.24 | 22.26 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_insert | 313 | 10.37 | 15.99 | 23.43 | 41.03 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:validation | 41 | 6.84 | 7.99 | 8.15 | 10.33 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:final | 1 | 14.52 | 14.52 | 14.52 | 14.52 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:begin | 1 | 11.08 | 11.08 | 11.08 | 11.08 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:counts | 1 | 4.71 | 4.71 | 4.71 | 4.71 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:metadata | 1 | 4.84 | 4.84 | 4.84 | 4.84 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:overrides | 1 | 10.42 | 10.42 | 10.42 | 10.42 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:extraction | 40 | 14.35 | 15.74 | 15.93 | 16.56 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:stopped_agents | 7 | 27.48 | 30.59 | 30.59 | 54.74 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:reconciliation | 40 | 10.03 | 14.04 | 18.83 | 20.61 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_stage | 1 | 3.67 | 3.67 | 3.67 | 3.67 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_delete | 313 | 9.32 | 13.51 | 17.69 | 23.72 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_insert | 313 | 9.66 | 14.40 | 21.90 | 27.46 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:validation | 41 | 6.66 | 8.51 | 8.51 | 9.04 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:final | 1 | 14.80 | 14.80 | 14.80 | 14.80 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:begin | 1 | 11.58 | 11.58 | 11.58 | 11.58 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:counts | 1 | 6.36 | 6.36 | 6.36 | 6.36 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:metadata | 1 | 6.25 | 6.25 | 6.25 | 6.25 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:overrides | 1 | 13.50 | 13.50 | 13.50 | 13.50 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:extraction | 40 | 14.83 | 15.60 | 16.20 | 23.60 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:stopped_agents | 7 | 24.96 | 26.50 | 26.50 | 35.97 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:reconciliation | 40 | 10.12 | 12.50 | 17.34 | 38.75 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_stage | 1 | 3.84 | 3.84 | 3.84 | 3.84 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_delete | 313 | 9.57 | 14.99 | 18.87 | 21.38 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_insert | 313 | 10.20 | 14.53 | 20.38 | 24.17 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:validation | 41 | 6.78 | 7.58 | 7.59 | 8.28 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:final | 1 | 15.20 | 15.20 | 15.20 | 15.20 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:begin | 1 | 10.97 | 10.97 | 10.97 | 10.97 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:counts | 1 | 6.73 | 6.73 | 6.73 | 6.73 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:metadata | 1 | 4.48 | 4.48 | 4.48 | 4.48 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:overrides | 1 | 9.18 | 9.18 | 9.18 | 9.18 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:extraction | 40 | 14.91 | 15.84 | 16.86 | 17.77 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:stopped_agents | 7 | 25.63 | 27.00 | 27.00 | 27.06 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:reconciliation | 40 | 10.11 | 13.46 | 14.11 | 28.04 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_stage | 1 | 4.09 | 4.09 | 4.09 | 4.09 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_delete | 313 | 9.80 | 15.58 | 20.17 | 22.36 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_insert | 313 | 9.72 | 12.66 | 19.22 | 22.28 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:validation | 41 | 6.72 | 8.08 | 8.24 | 8.44 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:final | 1 | 14.67 | 14.67 | 14.67 | 14.67 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:begin | 1 | 41.49 | 41.49 | 41.49 | 41.49 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:counts | 1 | 4.46 | 4.46 | 4.46 | 4.46 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:metadata | 1 | 10.11 | 10.11 | 10.11 | 10.11 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:overrides | 1 | 10.29 | 10.29 | 10.29 | 10.29 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:extraction | 40 | 14.48 | 15.96 | 16.00 | 18.98 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:stopped_agents | 7 | 26.12 | 26.36 | 26.36 | 26.71 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:reconciliation | 40 | 10.09 | 13.69 | 14.74 | 16.27 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_stage | 1 | 3.93 | 3.93 | 3.93 | 3.93 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_delete | 313 | 9.71 | 11.47 | 17.87 | 21.07 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_insert | 313 | 9.50 | 13.32 | 20.44 | 21.79 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:validation | 41 | 6.77 | 7.56 | 7.73 | 9.72 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:final | 1 | 15.13 | 15.13 | 15.13 | 15.13 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | rep 1 | operation_total | 1 | 14164.81 | 14164.81 | 14164.81 | 14164.81 | 1/1/1/1 |
| baseline | rep 1 | mixed:cumulative | 1 | 14237.28 | 14237.28 | 14237.28 | 14237.28 | 1/1/1/1 |
| baseline | rep 1 | mixed_compute_completion | 1 | 14182.97 | 14182.97 | 14182.97 | 14182.97 | 1/1/1/1 |
| baseline | rep 1 | foreground_arrival_drift | 200 | 87.62 | 128.30 | 176.77 | 197.81 | 58/0/0/0 |
| baseline | rep 1 | watcher_arrival_drift | 134 | 2057.39 | 2898.78 | 2973.86 | 2996.55 | 129/124/117/101 |
| baseline | rep 1 | event_loop_lag | 2372 | 0.44 | 3.40 | 6.55 | 16.75 | 0/0/0/0 |
| baseline | rep 1 | foreground_queued_completion | 200 | 7.35 | 55.54 | 71.18 | 82.90 | 0/0/0/0 |
| baseline | rep 1 | watcher_append_to_accepted | 134 | 0.22 | 0.43 | 0.94 | 2.25 | 0/0/0/0 |
| baseline | rep 1 | watcher_append_to_commit | 134 | 39.18 | 50.91 | 81.99 | 84.92 | 0/0/0/0 |
| baseline | rep 1 | watcher_append_to_notification | 134 | 44.21 | 59.28 | 86.44 | 90.02 | 0/0/0/0 |
| baseline | rep 2 | operation_total | 1 | 10522.17 | 10522.17 | 10522.17 | 10522.17 | 1/1/1/1 |
| baseline | rep 2 | mixed:cumulative | 1 | 15020.70 | 15020.70 | 15020.70 | 15020.70 | 1/1/1/1 |
| baseline | rep 2 | mixed_compute_completion | 1 | 14966.74 | 14966.74 | 14966.74 | 14966.74 | 1/1/1/1 |
| baseline | rep 2 | foreground_arrival_drift | 200 | 215.64 | 422.41 | 431.57 | 442.86 | 141/92/0/0 |
| baseline | rep 2 | watcher_arrival_drift | 134 | 2404.04 | 3228.31 | 3271.00 | 3287.85 | 131/126/120/104 |
| baseline | rep 2 | event_loop_lag | 2476 | 0.46 | 3.62 | 7.93 | 16.22 | 0/0/0/0 |
| baseline | rep 2 | foreground_queued_completion | 200 | 8.05 | 57.81 | 73.86 | 79.22 | 0/0/0/0 |
| baseline | rep 2 | watcher_append_to_accepted | 134 | 0.23 | 0.53 | 0.99 | 4.83 | 0/0/0/0 |
| baseline | rep 2 | watcher_append_to_commit | 134 | 40.25 | 61.95 | 81.47 | 88.80 | 0/0/0/0 |
| baseline | rep 2 | watcher_append_to_notification | 134 | 45.96 | 67.65 | 86.60 | 94.06 | 0/0/0/0 |
| baseline | rep 3 | operation_total | 1 | 13448.02 | 13448.02 | 13448.02 | 13448.02 | 1/1/1/1 |
| baseline | rep 3 | mixed:cumulative | 1 | 14457.86 | 14457.86 | 14457.86 | 14457.86 | 1/1/1/1 |
| baseline | rep 3 | mixed_compute_completion | 1 | 14405.14 | 14405.14 | 14405.14 | 14405.14 | 1/1/1/1 |
| baseline | rep 3 | foreground_arrival_drift | 200 | 48.60 | 109.86 | 125.70 | 128.90 | 17/0/0/0 |
| baseline | rep 3 | watcher_arrival_drift | 134 | 2002.78 | 2869.80 | 2950.65 | 2969.10 | 131/126/116/99 |
| baseline | rep 3 | event_loop_lag | 2375 | 0.45 | 3.69 | 8.35 | 15.00 | 0/0/0/0 |
| baseline | rep 3 | foreground_queued_completion | 200 | 7.01 | 49.05 | 78.90 | 103.98 | 1/0/0/0 |
| baseline | rep 3 | watcher_append_to_accepted | 134 | 0.23 | 0.56 | 1.25 | 2.33 | 0/0/0/0 |
| baseline | rep 3 | watcher_append_to_commit | 134 | 38.85 | 47.89 | 82.57 | 85.35 | 0/0/0/0 |
| baseline | rep 3 | watcher_append_to_notification | 134 | 44.27 | 61.66 | 87.29 | 90.27 | 0/0/0/0 |
| baseline | rep 4 | operation_total | 1 | 10452.78 | 10452.78 | 10452.78 | 10452.78 | 1/1/1/1 |
| baseline | rep 4 | mixed:cumulative | 1 | 14877.52 | 14877.52 | 14877.52 | 14877.52 | 1/1/1/1 |
| baseline | rep 4 | mixed_compute_completion | 1 | 14825.34 | 14825.34 | 14825.34 | 14825.34 | 1/1/1/1 |
| baseline | rep 4 | foreground_arrival_drift | 200 | 153.58 | 314.09 | 325.15 | 330.99 | 135/58/0/0 |
| baseline | rep 4 | watcher_arrival_drift | 134 | 2288.01 | 3178.85 | 3223.65 | 3240.72 | 132/126/120/104 |
| baseline | rep 4 | event_loop_lag | 2451 | 0.45 | 3.62 | 7.68 | 19.47 | 0/0/0/0 |
| baseline | rep 4 | foreground_queued_completion | 200 | 7.86 | 57.90 | 75.26 | 87.87 | 0/0/0/0 |
| baseline | rep 4 | watcher_append_to_accepted | 134 | 0.23 | 0.41 | 0.82 | 1.32 | 0/0/0/0 |
| baseline | rep 4 | watcher_append_to_commit | 134 | 40.49 | 60.43 | 85.64 | 90.23 | 0/0/0/0 |
| baseline | rep 4 | watcher_append_to_notification | 134 | 45.75 | 64.84 | 90.94 | 101.28 | 1/0/0/0 |
| baseline | rep 5 | operation_total | 1 | 14392.62 | 14392.62 | 14392.62 | 14392.62 | 1/1/1/1 |
| baseline | rep 5 | mixed:cumulative | 1 | 14745.27 | 14745.27 | 14745.27 | 14745.27 | 1/1/1/1 |
| baseline | rep 5 | mixed_compute_completion | 1 | 14690.54 | 14690.54 | 14690.54 | 14690.54 | 1/1/1/1 |
| baseline | rep 5 | foreground_arrival_drift | 200 | 138.27 | 193.86 | 202.80 | 207.16 | 106/0/0/0 |
| baseline | rep 5 | watcher_arrival_drift | 134 | 1993.96 | 2889.35 | 2934.97 | 2966.67 | 130/123/115/100 |
| baseline | rep 5 | event_loop_lag | 2432 | 0.44 | 3.54 | 8.02 | 21.63 | 0/0/0/0 |
| baseline | rep 5 | foreground_queued_completion | 200 | 7.27 | 49.79 | 75.44 | 88.77 | 0/0/0/0 |
| baseline | rep 5 | watcher_append_to_accepted | 134 | 0.23 | 0.45 | 0.73 | 4.69 | 0/0/0/0 |
| baseline | rep 5 | watcher_append_to_commit | 134 | 39.26 | 49.04 | 69.43 | 92.54 | 0/0/0/0 |
| baseline | rep 5 | watcher_append_to_notification | 134 | 44.58 | 63.46 | 74.04 | 100.22 | 1/0/0/0 |
| candidate | rep 1 | operation_total | 1 | 19111.98 | 19111.98 | 19111.98 | 19111.98 | 1/1/1/1 |
| candidate | rep 1 | mixed:cumulative | 1 | 19136.44 | 19136.44 | 19136.44 | 19136.44 | 1/1/1/1 |
| candidate | rep 1 | mixed_compute_completion | 1 | 19128.07 | 19128.07 | 19128.07 | 19128.07 | 1/1/1/1 |
| candidate | rep 1 | foreground_arrival_drift | 200 | 44.27 | 183.51 | 196.07 | 196.59 | 41/0/0/0 |
| candidate | rep 1 | watcher_arrival_drift | 134 | 2157.21 | 4456.63 | 4618.02 | 4668.32 | 131/126/118/101 |
| candidate | rep 1 | event_loop_lag | 3193 | 0.53 | 3.38 | 5.22 | 13.67 | 0/0/0/0 |
| candidate | rep 1 | foreground_queued_completion | 200 | 7.79 | 50.62 | 69.21 | 76.56 | 0/0/0/0 |
| candidate | rep 1 | watcher_append_to_accepted | 134 | 0.27 | 4.70 | 8.39 | 9.08 | 0/0/0/0 |
| candidate | rep 1 | watcher_append_to_commit | 134 | 42.76 | 67.78 | 75.25 | 90.32 | 0/0/0/0 |
| candidate | rep 1 | watcher_append_to_notification | 134 | 48.06 | 73.17 | 80.48 | 95.68 | 0/0/0/0 |
| candidate | rep 2 | operation_total | 1 | 14497.43 | 14497.43 | 14497.43 | 14497.43 | 1/1/1/1 |
| candidate | rep 2 | mixed:cumulative | 1 | 18734.64 | 18734.64 | 18734.64 | 18734.64 | 1/1/1/1 |
| candidate | rep 2 | mixed_compute_completion | 1 | 18726.55 | 18726.55 | 18726.55 | 18726.55 | 1/1/1/1 |
| candidate | rep 2 | foreground_arrival_drift | 200 | 101.07 | 211.69 | 216.29 | 233.70 | 104/0/0/0 |
| candidate | rep 2 | watcher_arrival_drift | 134 | 2194.43 | 4620.41 | 4799.62 | 4855.32 | 131/127/119/103 |
| candidate | rep 2 | event_loop_lag | 3142 | 0.55 | 3.33 | 5.10 | 14.05 | 0/0/0/0 |
| candidate | rep 2 | foreground_queued_completion | 200 | 7.69 | 51.47 | 73.66 | 76.18 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_accepted | 134 | 0.25 | 4.45 | 6.11 | 10.92 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_commit | 134 | 42.89 | 71.13 | 83.01 | 87.17 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_notification | 134 | 48.29 | 75.47 | 88.24 | 91.91 | 0/0/0/0 |
| candidate | rep 3 | operation_total | 1 | 18926.15 | 18926.15 | 18926.15 | 18926.15 | 1/1/1/1 |
| candidate | rep 3 | mixed:cumulative | 1 | 18951.48 | 18951.48 | 18951.48 | 18951.48 | 1/1/1/1 |
| candidate | rep 3 | mixed_compute_completion | 1 | 18942.70 | 18942.70 | 18942.70 | 18942.70 | 1/1/1/1 |
| candidate | rep 3 | foreground_arrival_drift | 200 | 36.91 | 72.20 | 81.49 | 84.92 | 0/0/0/0 |
| candidate | rep 3 | watcher_arrival_drift | 134 | 1992.00 | 4634.64 | 4760.94 | 4817.87 | 130/125/118/101 |
| candidate | rep 3 | event_loop_lag | 3175 | 0.55 | 3.30 | 4.69 | 110.94 | 1/0/0/0 |
| candidate | rep 3 | foreground_queued_completion | 200 | 7.40 | 44.68 | 62.07 | 73.61 | 0/0/0/0 |
| candidate | rep 3 | watcher_append_to_accepted | 134 | 0.27 | 4.57 | 7.40 | 8.89 | 0/0/0/0 |
| candidate | rep 3 | watcher_append_to_commit | 134 | 42.73 | 67.91 | 84.50 | 168.26 | 1/0/0/0 |
| candidate | rep 3 | watcher_append_to_notification | 134 | 48.18 | 72.97 | 89.10 | 174.21 | 1/0/0/0 |
| candidate | rep 4 | operation_total | 1 | 18618.40 | 18618.40 | 18618.40 | 18618.40 | 1/1/1/1 |
| candidate | rep 4 | mixed:cumulative | 1 | 18643.45 | 18643.45 | 18643.45 | 18643.45 | 1/1/1/1 |
| candidate | rep 4 | mixed_compute_completion | 1 | 18634.90 | 18634.90 | 18634.90 | 18634.90 | 1/1/1/1 |
| candidate | rep 4 | foreground_arrival_drift | 200 | 87.19 | 155.12 | 167.76 | 171.55 | 80/0/0/0 |
| candidate | rep 4 | watcher_arrival_drift | 134 | 1997.68 | 4489.78 | 4656.91 | 4704.05 | 130/125/119/104 |
| candidate | rep 4 | event_loop_lag | 3129 | 0.50 | 3.47 | 5.23 | 17.57 | 0/0/0/0 |
| candidate | rep 4 | foreground_queued_completion | 200 | 7.43 | 45.88 | 59.86 | 73.96 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_accepted | 134 | 0.28 | 4.54 | 8.05 | 9.71 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_commit | 134 | 42.50 | 66.32 | 72.28 | 91.41 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_notification | 134 | 48.51 | 71.36 | 77.48 | 96.70 | 0/0/0/0 |
| candidate | rep 5 | operation_total | 1 | 18175.37 | 18175.37 | 18175.37 | 18175.37 | 1/1/1/1 |
| candidate | rep 5 | mixed:cumulative | 1 | 18602.48 | 18602.48 | 18602.48 | 18602.48 | 1/1/1/1 |
| candidate | rep 5 | mixed_compute_completion | 1 | 18594.21 | 18594.21 | 18594.21 | 18594.21 | 1/1/1/1 |
| candidate | rep 5 | foreground_arrival_drift | 200 | 45.49 | 117.26 | 131.12 | 131.81 | 30/0/0/0 |
| candidate | rep 5 | watcher_arrival_drift | 134 | 2049.12 | 4546.11 | 4720.63 | 4781.61 | 130/124/117/100 |
| candidate | rep 5 | event_loop_lag | 3141 | 0.48 | 3.29 | 4.71 | 8.24 | 0/0/0/0 |
| candidate | rep 5 | foreground_queued_completion | 200 | 7.36 | 47.14 | 64.79 | 108.78 | 1/0/0/0 |
| candidate | rep 5 | watcher_append_to_accepted | 134 | 0.26 | 6.94 | 8.45 | 9.48 | 0/0/0/0 |
| candidate | rep 5 | watcher_append_to_commit | 134 | 43.05 | 69.47 | 77.68 | 102.08 | 1/0/0/0 |
| candidate | rep 5 | watcher_append_to_notification | 134 | 48.20 | 74.40 | 84.33 | 107.36 | 1/0/0/0 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | rep 1 | SELECT=1384, UPDATE=381, DELETE=4, INSERT=40001, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 741 | 1634.22 |
| baseline | rep 2 | SELECT=1384, UPDATE=381, DELETE=4, INSERT=40001, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 741 | 1750.41 |
| baseline | rep 3 | SELECT=1384, UPDATE=381, DELETE=4, INSERT=40001, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 741 | 1573.90 |
| baseline | rep 4 | SELECT=1384, UPDATE=381, DELETE=4, INSERT=40001, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 741 | 1775.26 |
| baseline | rep 5 | SELECT=1384, UPDATE=381, DELETE=4, INSERT=40001, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 741 | 1668.50 |
| candidate | rep 1 | SELECT=7581, UPDATE=1515, DELETE=317, INSERT=40313, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1821 | 5155.24 |
| candidate | rep 2 | SELECT=7581, UPDATE=1515, DELETE=317, INSERT=40313, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1821 | 4778.08 |
| candidate | rep 3 | SELECT=7581, UPDATE=1515, DELETE=317, INSERT=40313, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1821 | 4803.67 |
| candidate | rep 4 | SELECT=7581, UPDATE=1515, DELETE=317, INSERT=40313, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1821 | 4867.73 |
| candidate | rep 5 | SELECT=7581, UPDATE=1515, DELETE=317, INSERT=40313, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1821 | 4766.15 |


## Facts 256 retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate | 1000 facts / 256 rows | facts_delete:sqlite_writer_hold | 20 | 5.20 | 6.48 | 6.48 | 6.63 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | facts_insert:sqlite_writer_hold | 20 | 5.27 | 6.81 | 6.81 | 6.95 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_delete:sqlite_writer_hold | 200 | 6.37 | 7.38 | 7.74 | 7.93 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_insert:sqlite_writer_hold | 200 | 7.86 | 16.70 | 22.78 | 50.85 | 0/0/0/0 |
| candidate | 40000 facts / 256 rows | facts_delete:sqlite_writer_hold | 785 | 6.99 | 11.96 | 20.77 | 26.48 | 0/0/0/0 |
| candidate | 40000 facts / 256 rows | facts_insert:sqlite_writer_hold | 785 | 7.57 | 18.06 | 25.92 | 111.36 | 1/0/0/0 |
| candidate | 75000 facts / 256 rows | facts_delete:sqlite_writer_hold | 1465 | 6.80 | 14.24 | 20.57 | 49.38 | 0/0/0/0 |
| candidate | 75000 facts / 256 rows | facts_insert:sqlite_writer_hold | 1465 | 6.86 | 15.89 | 23.49 | 32.59 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate | 1000 facts / 256 rows | facts_delete:sql:DELETE | 20 | 0.52 | 1.46 | 1.46 | 1.53 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | facts_delete:commit | 20 | 4.43 | 4.86 | 4.86 | 4.95 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | facts_insert:sql:INSERT | 20 | 0.54 | 1.54 | 1.54 | 1.62 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | facts_insert:commit | 20 | 4.68 | 5.09 | 5.09 | 5.29 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | facts:cumulative | 5 | 53.53 | 73.04 | 73.04 | 73.79 | 0/0/0/0 |
| candidate | 1000 facts / 256 rows | foreground:completion | 13 | 4.51 | 58.59 | 58.59 | 58.65 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_delete:sql:DELETE | 200 | 1.18 | 1.76 | 2.02 | 2.07 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_delete:commit | 200 | 5.02 | 5.60 | 5.82 | 6.12 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_insert:sql:INSERT | 200 | 1.95 | 2.80 | 3.02 | 3.17 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts_insert:commit | 200 | 5.57 | 14.27 | 20.26 | 47.88 | 0/0/0/0 |
| candidate | 10000 facts / 256 rows | facts:cumulative | 5 | 730.14 | 895.22 | 895.22 | 921.08 | 5/5/5/0 |
| candidate | 10000 facts / 256 rows | foreground:completion | 59 | 11.47 | 338.48 | 539.91 | 635.95 | 7/4/2/0 |
| candidate | 40000 facts / 256 rows | facts_delete:sql:DELETE | 785 | 1.66 | 2.25 | 2.43 | 3.58 | 0/0/0/0 |
| candidate | 40000 facts / 256 rows | facts_delete:commit | 785 | 5.06 | 9.98 | 18.74 | 24.18 | 0/0/0/0 |
| candidate | 40000 facts / 256 rows | facts_insert:sql:INSERT | 785 | 1.77 | 3.07 | 3.29 | 6.32 | 0/0/0/0 |
| candidate | 40000 facts / 256 rows | facts_insert:commit | 785 | 5.46 | 15.74 | 23.02 | 110.68 | 1/0/0/0 |
| candidate | 40000 facts / 256 rows | facts:cumulative | 5 | 3097.41 | 3261.71 | 3261.71 | 4458.96 | 5/5/5/5 |
| candidate | 40000 facts / 256 rows | foreground:completion | 269 | 10.43 | 137.14 | 1040.36 | 2138.30 | 24/9/7/4 |
| candidate | 75000 facts / 256 rows | facts_delete:sql:DELETE | 1465 | 1.41 | 2.23 | 2.41 | 5.01 | 0/0/0/0 |
| candidate | 75000 facts / 256 rows | facts_delete:commit | 1465 | 5.10 | 12.30 | 18.57 | 48.85 | 0/0/0/0 |
| candidate | 75000 facts / 256 rows | facts_insert:sql:INSERT | 1465 | 1.33 | 2.78 | 3.13 | 4.82 | 0/0/0/0 |
| candidate | 75000 facts / 256 rows | facts_insert:commit | 1465 | 5.16 | 13.19 | 21.52 | 31.23 | 0/0/0/0 |
| candidate | 75000 facts / 256 rows | facts:cumulative | 5 | 5799.31 | 6000.26 | 6000.26 | 6173.94 | 5/5/5/5 |
| candidate | 75000 facts / 256 rows | foreground:completion | 409 | 10.68 | 334.93 | 836.46 | 1735.40 | 45/24/11/4 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| candidate | 1000 facts / 256 rows | metadata_select=40, SELECT=40, UPDATE=40, DELETE=20, INSERT=20 | 40 | 184.24 |
| candidate | 10000 facts / 256 rows | metadata_select=400, SELECT=400, UPDATE=400, DELETE=200, INSERT=200 | 400 | 2429.14 |
| candidate | 40000 facts / 256 rows | metadata_select=1570, SELECT=1570, UPDATE=1570, DELETE=785, INSERT=785 | 1570 | 9890.99 |
| candidate | 75000 facts / 256 rows | metadata_select=2930, SELECT=2930, UPDATE=2930, DELETE=1465, INSERT=1465 | 2930 | 17987.95 |


## Mixed 256 retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate | rep 1 | actual_coordinator_lease_hold:begin | 1 | 11.40 | 11.40 | 11.40 | 11.40 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:counts | 1 | 5.31 | 5.31 | 5.31 | 5.31 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:metadata | 1 | 4.02 | 4.02 | 4.02 | 4.02 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:overrides | 1 | 10.04 | 10.04 | 10.04 | 10.04 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:extraction | 40 | 14.69 | 16.54 | 17.92 | 17.98 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:stopped_agents | 7 | 26.22 | 26.82 | 26.82 | 26.98 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:reconciliation | 40 | 10.12 | 11.94 | 15.62 | 17.15 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_stage | 1 | 4.12 | 4.12 | 4.12 | 4.12 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_delete | 157 | 9.20 | 13.30 | 17.62 | 20.08 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:history_insert | 157 | 9.70 | 14.45 | 19.43 | 20.82 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:validation | 41 | 6.64 | 8.19 | 8.22 | 9.06 | 0/0/0/0 |
| candidate | rep 1 | actual_coordinator_lease_hold:final | 1 | 14.29 | 14.29 | 14.29 | 14.29 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:begin | 1 | 11.36 | 11.36 | 11.36 | 11.36 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:counts | 1 | 4.99 | 4.99 | 4.99 | 4.99 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:metadata | 1 | 5.19 | 5.19 | 5.19 | 5.19 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:overrides | 1 | 9.99 | 9.99 | 9.99 | 9.99 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:extraction | 40 | 15.11 | 18.24 | 20.97 | 27.65 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:stopped_agents | 7 | 25.18 | 26.18 | 26.18 | 45.12 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:reconciliation | 40 | 9.84 | 11.59 | 12.32 | 16.60 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_stage | 1 | 3.77 | 3.77 | 3.77 | 3.77 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_delete | 157 | 9.92 | 14.34 | 18.39 | 19.49 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:history_insert | 157 | 9.57 | 13.45 | 18.39 | 24.43 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:validation | 41 | 6.76 | 7.83 | 7.83 | 8.73 | 0/0/0/0 |
| candidate | rep 2 | actual_coordinator_lease_hold:final | 1 | 14.54 | 14.54 | 14.54 | 14.54 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:begin | 1 | 11.13 | 11.13 | 11.13 | 11.13 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:counts | 1 | 6.32 | 6.32 | 6.32 | 6.32 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:metadata | 1 | 9.66 | 9.66 | 9.66 | 9.66 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:overrides | 1 | 10.11 | 10.11 | 10.11 | 10.11 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:extraction | 40 | 14.52 | 15.99 | 16.13 | 17.11 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:stopped_agents | 7 | 25.76 | 26.23 | 26.23 | 26.57 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:reconciliation | 40 | 10.00 | 11.18 | 11.37 | 19.49 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_stage | 1 | 3.89 | 3.89 | 3.89 | 3.89 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_delete | 157 | 10.17 | 16.45 | 19.50 | 23.20 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:history_insert | 157 | 9.84 | 14.40 | 21.58 | 23.89 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:validation | 41 | 7.54 | 10.23 | 10.50 | 10.63 | 0/0/0/0 |
| candidate | rep 3 | actual_coordinator_lease_hold:final | 1 | 16.59 | 16.59 | 16.59 | 16.59 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:begin | 1 | 10.97 | 10.97 | 10.97 | 10.97 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:counts | 1 | 5.72 | 5.72 | 5.72 | 5.72 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:metadata | 1 | 4.52 | 4.52 | 4.52 | 4.52 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:overrides | 1 | 9.74 | 9.74 | 9.74 | 9.74 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:extraction | 40 | 14.85 | 16.82 | 17.65 | 33.52 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:stopped_agents | 7 | 25.91 | 26.63 | 26.63 | 27.81 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:reconciliation | 40 | 10.05 | 12.66 | 15.82 | 35.18 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_stage | 1 | 4.15 | 4.15 | 4.15 | 4.15 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_delete | 157 | 10.24 | 15.21 | 19.26 | 20.31 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:history_insert | 157 | 10.40 | 14.13 | 21.86 | 22.89 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:validation | 41 | 6.73 | 8.28 | 8.46 | 8.47 | 0/0/0/0 |
| candidate | rep 4 | actual_coordinator_lease_hold:final | 1 | 14.75 | 14.75 | 14.75 | 14.75 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:begin | 1 | 11.17 | 11.17 | 11.17 | 11.17 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:counts | 1 | 5.29 | 5.29 | 5.29 | 5.29 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:metadata | 1 | 4.17 | 4.17 | 4.17 | 4.17 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:overrides | 1 | 12.12 | 12.12 | 12.12 | 12.12 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:extraction | 40 | 14.90 | 15.66 | 17.33 | 18.84 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:stopped_agents | 7 | 25.41 | 26.06 | 26.06 | 26.41 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:reconciliation | 40 | 9.89 | 10.67 | 10.87 | 11.44 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_stage | 1 | 3.83 | 3.83 | 3.83 | 3.83 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_delete | 157 | 9.65 | 14.10 | 18.38 | 19.00 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:history_insert | 157 | 10.39 | 15.18 | 22.29 | 29.97 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:validation | 41 | 6.63 | 7.48 | 7.69 | 10.17 | 0/0/0/0 |
| candidate | rep 5 | actual_coordinator_lease_hold:final | 1 | 14.83 | 14.83 | 14.83 | 14.83 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| candidate | rep 1 | operation_total | 1 | 15639.68 | 15639.68 | 15639.68 | 15639.68 | 1/1/1/1 |
| candidate | rep 1 | mixed:cumulative | 1 | 15665.76 | 15665.76 | 15665.76 | 15665.76 | 1/1/1/1 |
| candidate | rep 1 | mixed_compute_completion | 1 | 15657.81 | 15657.81 | 15657.81 | 15657.81 | 1/1/1/1 |
| candidate | rep 1 | foreground_arrival_drift | 200 | 83.23 | 307.14 | 319.51 | 321.20 | 99/42/0/0 |
| candidate | rep 1 | watcher_arrival_drift | 134 | 2280.64 | 4689.81 | 4892.65 | 4960.54 | 131/126/119/101 |
| candidate | rep 1 | event_loop_lag | 2636 | 0.58 | 3.26 | 4.49 | 8.59 | 0/0/0/0 |
| candidate | rep 1 | foreground_queued_completion | 200 | 7.72 | 54.01 | 73.69 | 97.54 | 0/0/0/0 |
| candidate | rep 1 | watcher_append_to_accepted | 134 | 0.25 | 4.74 | 7.35 | 9.55 | 0/0/0/0 |
| candidate | rep 1 | watcher_append_to_commit | 134 | 43.19 | 74.86 | 86.09 | 111.25 | 1/0/0/0 |
| candidate | rep 1 | watcher_append_to_notification | 134 | 48.67 | 80.37 | 91.15 | 116.49 | 2/0/0/0 |
| candidate | rep 2 | operation_total | 1 | 15446.64 | 15446.64 | 15446.64 | 15446.64 | 1/1/1/1 |
| candidate | rep 2 | mixed:cumulative | 1 | 15592.90 | 15592.90 | 15592.90 | 15592.90 | 1/1/1/1 |
| candidate | rep 2 | mixed_compute_completion | 1 | 15584.25 | 15584.25 | 15584.25 | 15584.25 | 1/1/1/1 |
| candidate | rep 2 | foreground_arrival_drift | 200 | 141.60 | 183.52 | 203.83 | 206.05 | 149/0/0/0 |
| candidate | rep 2 | watcher_arrival_drift | 134 | 2162.90 | 4698.61 | 4853.19 | 4915.74 | 130/127/120/105 |
| candidate | rep 2 | event_loop_lag | 2611 | 0.57 | 3.55 | 4.82 | 8.22 | 0/0/0/0 |
| candidate | rep 2 | foreground_queued_completion | 200 | 7.62 | 51.33 | 68.49 | 74.52 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_accepted | 134 | 0.26 | 3.74 | 4.78 | 10.15 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_commit | 134 | 43.60 | 67.33 | 75.12 | 85.99 | 0/0/0/0 |
| candidate | rep 2 | watcher_append_to_notification | 134 | 49.38 | 74.20 | 79.70 | 91.36 | 0/0/0/0 |
| candidate | rep 3 | operation_total | 1 | 15421.46 | 15421.46 | 15421.46 | 15421.46 | 1/1/1/1 |
| candidate | rep 3 | mixed:cumulative | 1 | 15608.92 | 15608.92 | 15608.92 | 15608.92 | 1/1/1/1 |
| candidate | rep 3 | mixed_compute_completion | 1 | 15600.26 | 15600.26 | 15600.26 | 15600.26 | 1/1/1/1 |
| candidate | rep 3 | foreground_arrival_drift | 200 | 48.82 | 167.73 | 193.90 | 194.57 | 38/0/0/0 |
| candidate | rep 3 | watcher_arrival_drift | 134 | 2114.77 | 4625.28 | 4813.84 | 4877.89 | 130/124/116/100 |
| candidate | rep 3 | event_loop_lag | 2578 | 0.61 | 3.55 | 5.16 | 11.71 | 0/0/0/0 |
| candidate | rep 3 | foreground_queued_completion | 200 | 7.31 | 52.30 | 69.35 | 74.85 | 0/0/0/0 |
| candidate | rep 3 | watcher_append_to_accepted | 134 | 0.27 | 3.72 | 6.96 | 8.82 | 0/0/0/0 |
| candidate | rep 3 | watcher_append_to_commit | 134 | 43.66 | 73.04 | 77.74 | 85.43 | 0/0/0/0 |
| candidate | rep 3 | watcher_append_to_notification | 134 | 49.08 | 78.29 | 82.97 | 90.74 | 0/0/0/0 |
| candidate | rep 4 | operation_total | 1 | 15511.66 | 15511.66 | 15511.66 | 15511.66 | 1/1/1/1 |
| candidate | rep 4 | mixed:cumulative | 1 | 15535.14 | 15535.14 | 15535.14 | 15535.14 | 1/1/1/1 |
| candidate | rep 4 | mixed_compute_completion | 1 | 15526.68 | 15526.68 | 15526.68 | 15526.68 | 1/1/1/1 |
| candidate | rep 4 | foreground_arrival_drift | 200 | 24.90 | 231.84 | 243.25 | 256.10 | 61/2/0/0 |
| candidate | rep 4 | watcher_arrival_drift | 134 | 2157.25 | 4637.00 | 4842.96 | 4907.74 | 130/124/116/99 |
| candidate | rep 4 | event_loop_lag | 2565 | 0.65 | 3.53 | 4.91 | 19.91 | 0/0/0/0 |
| candidate | rep 4 | foreground_queued_completion | 200 | 7.26 | 50.77 | 72.55 | 76.75 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_accepted | 134 | 0.27 | 4.27 | 6.86 | 8.32 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_commit | 134 | 43.53 | 72.02 | 80.33 | 89.12 | 0/0/0/0 |
| candidate | rep 4 | watcher_append_to_notification | 134 | 49.56 | 77.53 | 85.06 | 94.52 | 0/0/0/0 |
| candidate | rep 5 | operation_total | 1 | 15649.61 | 15649.61 | 15649.61 | 15649.61 | 1/1/1/1 |
| candidate | rep 5 | mixed:cumulative | 1 | 15674.96 | 15674.96 | 15674.96 | 15674.96 | 1/1/1/1 |
| candidate | rep 5 | mixed_compute_completion | 1 | 15666.59 | 15666.59 | 15666.59 | 15666.59 | 1/1/1/1 |
| candidate | rep 5 | foreground_arrival_drift | 200 | 233.76 | 500.45 | 516.94 | 519.64 | 139/82/11/0 |
| candidate | rep 5 | watcher_arrival_drift | 134 | 2427.97 | 4752.23 | 4943.77 | 5000.34 | 130/125/117/105 |
| candidate | rep 5 | event_loop_lag | 2612 | 0.62 | 3.47 | 4.55 | 8.59 | 0/0/0/0 |
| candidate | rep 5 | foreground_queued_completion | 200 | 8.36 | 70.83 | 80.00 | 87.53 | 0/0/0/0 |
| candidate | rep 5 | watcher_append_to_accepted | 134 | 0.26 | 4.86 | 7.71 | 9.29 | 0/0/0/0 |
| candidate | rep 5 | watcher_append_to_commit | 134 | 43.77 | 73.49 | 84.65 | 88.81 | 0/0/0/0 |
| candidate | rep 5 | watcher_append_to_notification | 134 | 49.86 | 78.30 | 89.95 | 94.09 | 0/0/0/0 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| candidate | rep 1 | SELECT=5085, UPDATE=1203, DELETE=161, INSERT=40157, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1509 | 3334.82 |
| candidate | rep 2 | SELECT=5085, UPDATE=1203, DELETE=161, INSERT=40157, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1509 | 3362.13 |
| candidate | rep 3 | SELECT=5085, UPDATE=1203, DELETE=161, INSERT=40157, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1509 | 3369.73 |
| candidate | rep 4 | SELECT=5085, UPDATE=1203, DELETE=161, INSERT=40157, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1509 | 3392.36 |
| candidate | rep 5 | SELECT=5085, UPDATE=1203, DELETE=161, INSERT=40157, maintenance_preparations=0, compression_during_pending_source=0, compression_spans_overlapping_source_application=0 | 1509 | 3389.71 |


## Dispatch retained distributions

The SQLite spans below exclude first-write execution. Runtime lease metrics include actual coordinator ownership.

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | idle block 1 | actual_coordinator_lease_hold:begin | 1 | 3.27 | 3.27 | 3.27 | 3.27 | 0/0/0/0 |
| baseline | idle block 1 | actual_coordinator_lease_hold:synthetic | 2000 | 1.17 | 1.48 | 1.74 | 3.37 | 0/0/0/0 |
| baseline | idle block 1 | actual_coordinator_lease_hold:final | 1 | 1.16 | 1.16 | 1.16 | 1.16 | 0/0/0/0 |
| baseline | idle block 2 | actual_coordinator_lease_hold:begin | 1 | 0.30 | 0.30 | 0.30 | 0.30 | 0/0/0/0 |
| baseline | idle block 2 | actual_coordinator_lease_hold:synthetic | 2000 | 1.30 | 1.53 | 1.65 | 2.32 | 0/0/0/0 |
| baseline | idle block 2 | actual_coordinator_lease_hold:final | 1 | 1.24 | 1.24 | 1.24 | 1.24 | 0/0/0/0 |
| baseline | idle block 3 | actual_coordinator_lease_hold:begin | 1 | 0.27 | 0.27 | 0.27 | 0.27 | 0/0/0/0 |
| baseline | idle block 3 | actual_coordinator_lease_hold:synthetic | 2000 | 1.31 | 1.53 | 1.71 | 2.32 | 0/0/0/0 |
| baseline | idle block 3 | actual_coordinator_lease_hold:final | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 0/0/0/0 |
| baseline | idle block 4 | actual_coordinator_lease_hold:begin | 1 | 0.29 | 0.29 | 0.29 | 0.29 | 0/0/0/0 |
| baseline | idle block 4 | actual_coordinator_lease_hold:synthetic | 2000 | 1.31 | 1.56 | 1.77 | 3.44 | 0/0/0/0 |
| baseline | idle block 4 | actual_coordinator_lease_hold:final | 1 | 1.04 | 1.04 | 1.04 | 1.04 | 0/0/0/0 |
| baseline | idle block 5 | actual_coordinator_lease_hold:begin | 1 | 0.27 | 0.27 | 0.27 | 0.27 | 0/0/0/0 |
| baseline | idle block 5 | actual_coordinator_lease_hold:synthetic | 2000 | 1.29 | 1.53 | 1.67 | 2.01 | 0/0/0/0 |
| baseline | idle block 5 | actual_coordinator_lease_hold:final | 1 | 1.20 | 1.20 | 1.20 | 1.20 | 0/0/0/0 |
| baseline | foreground block 1 | actual_coordinator_lease_hold:begin | 1 | 0.64 | 0.64 | 0.64 | 0.64 | 0/0/0/0 |
| baseline | foreground block 1 | actual_coordinator_lease_hold:synthetic | 2000 | 1.27 | 1.54 | 1.66 | 2.82 | 0/0/0/0 |
| baseline | foreground block 1 | actual_coordinator_lease_hold:final | 1 | 1.25 | 1.25 | 1.25 | 1.25 | 0/0/0/0 |
| baseline | foreground block 2 | actual_coordinator_lease_hold:begin | 1 | 0.26 | 0.26 | 0.26 | 0.26 | 0/0/0/0 |
| baseline | foreground block 2 | actual_coordinator_lease_hold:synthetic | 2000 | 1.28 | 1.53 | 1.68 | 2.66 | 0/0/0/0 |
| baseline | foreground block 2 | actual_coordinator_lease_hold:final | 1 | 1.25 | 1.25 | 1.25 | 1.25 | 0/0/0/0 |
| baseline | foreground block 3 | actual_coordinator_lease_hold:begin | 1 | 0.25 | 0.25 | 0.25 | 0.25 | 0/0/0/0 |
| baseline | foreground block 3 | actual_coordinator_lease_hold:synthetic | 2000 | 1.29 | 1.54 | 1.67 | 3.25 | 0/0/0/0 |
| baseline | foreground block 3 | actual_coordinator_lease_hold:final | 1 | 1.30 | 1.30 | 1.30 | 1.30 | 0/0/0/0 |
| baseline | foreground block 4 | actual_coordinator_lease_hold:begin | 1 | 0.26 | 0.26 | 0.26 | 0.26 | 0/0/0/0 |
| baseline | foreground block 4 | actual_coordinator_lease_hold:synthetic | 2000 | 1.30 | 1.56 | 1.70 | 2.37 | 0/0/0/0 |
| baseline | foreground block 4 | actual_coordinator_lease_hold:final | 1 | 1.11 | 1.11 | 1.11 | 1.11 | 0/0/0/0 |
| baseline | foreground block 5 | actual_coordinator_lease_hold:begin | 1 | 0.23 | 0.23 | 0.23 | 0.23 | 0/0/0/0 |
| baseline | foreground block 5 | actual_coordinator_lease_hold:synthetic | 2000 | 1.29 | 1.53 | 1.67 | 3.47 | 0/0/0/0 |
| baseline | foreground block 5 | actual_coordinator_lease_hold:final | 1 | 0.39 | 0.39 | 0.39 | 0.39 | 0/0/0/0 |
| candidate | idle block 1 | actual_coordinator_lease_hold:begin | 1 | 2.54 | 2.54 | 2.54 | 2.54 | 0/0/0/0 |
| candidate | idle block 1 | actual_coordinator_lease_hold:synthetic | 2000 | 1.05 | 1.27 | 1.40 | 1.81 | 0/0/0/0 |
| candidate | idle block 1 | actual_coordinator_lease_hold:final | 1 | 0.96 | 0.96 | 0.96 | 0.96 | 0/0/0/0 |
| candidate | idle block 2 | actual_coordinator_lease_hold:begin | 1 | 0.32 | 0.32 | 0.32 | 0.32 | 0/0/0/0 |
| candidate | idle block 2 | actual_coordinator_lease_hold:synthetic | 2000 | 1.00 | 1.28 | 1.40 | 1.85 | 0/0/0/0 |
| candidate | idle block 2 | actual_coordinator_lease_hold:final | 1 | 0.21 | 0.21 | 0.21 | 0.21 | 0/0/0/0 |
| candidate | idle block 3 | actual_coordinator_lease_hold:begin | 1 | 0.34 | 0.34 | 0.34 | 0.34 | 0/0/0/0 |
| candidate | idle block 3 | actual_coordinator_lease_hold:synthetic | 2000 | 1.04 | 1.28 | 1.42 | 2.01 | 0/0/0/0 |
| candidate | idle block 3 | actual_coordinator_lease_hold:final | 1 | 0.96 | 0.96 | 0.96 | 0.96 | 0/0/0/0 |
| candidate | idle block 4 | actual_coordinator_lease_hold:begin | 1 | 0.34 | 0.34 | 0.34 | 0.34 | 0/0/0/0 |
| candidate | idle block 4 | actual_coordinator_lease_hold:synthetic | 2000 | 1.03 | 1.28 | 1.43 | 1.64 | 0/0/0/0 |
| candidate | idle block 4 | actual_coordinator_lease_hold:final | 1 | 0.47 | 0.47 | 0.47 | 0.47 | 0/0/0/0 |
| candidate | idle block 5 | actual_coordinator_lease_hold:begin | 1 | 0.44 | 0.44 | 0.44 | 0.44 | 0/0/0/0 |
| candidate | idle block 5 | actual_coordinator_lease_hold:synthetic | 2000 | 1.03 | 1.28 | 1.42 | 2.08 | 0/0/0/0 |
| candidate | idle block 5 | actual_coordinator_lease_hold:final | 1 | 0.98 | 0.98 | 0.98 | 0.98 | 0/0/0/0 |
| candidate | foreground block 1 | actual_coordinator_lease_hold:begin | 1 | 0.97 | 0.97 | 0.97 | 0.97 | 0/0/0/0 |
| candidate | foreground block 1 | actual_coordinator_lease_hold:synthetic | 2000 | 1.03 | 1.28 | 1.40 | 3.73 | 0/0/0/0 |
| candidate | foreground block 1 | actual_coordinator_lease_hold:final | 1 | 0.83 | 0.83 | 0.83 | 0.83 | 0/0/0/0 |
| candidate | foreground block 2 | actual_coordinator_lease_hold:begin | 1 | 0.29 | 0.29 | 0.29 | 0.29 | 0/0/0/0 |
| candidate | foreground block 2 | actual_coordinator_lease_hold:synthetic | 2000 | 1.04 | 1.28 | 1.43 | 1.73 | 0/0/0/0 |
| candidate | foreground block 2 | actual_coordinator_lease_hold:final | 1 | 0.96 | 0.96 | 0.96 | 0.96 | 0/0/0/0 |
| candidate | foreground block 3 | actual_coordinator_lease_hold:begin | 1 | 0.30 | 0.30 | 0.30 | 0.30 | 0/0/0/0 |
| candidate | foreground block 3 | actual_coordinator_lease_hold:synthetic | 2000 | 1.03 | 1.28 | 1.37 | 2.13 | 0/0/0/0 |
| candidate | foreground block 3 | actual_coordinator_lease_hold:final | 1 | 0.96 | 0.96 | 0.96 | 0.96 | 0/0/0/0 |
| candidate | foreground block 4 | actual_coordinator_lease_hold:begin | 1 | 0.35 | 0.35 | 0.35 | 0.35 | 0/0/0/0 |
| candidate | foreground block 4 | actual_coordinator_lease_hold:synthetic | 2000 | 1.01 | 1.26 | 1.40 | 1.62 | 0/0/0/0 |
| candidate | foreground block 4 | actual_coordinator_lease_hold:final | 1 | 1.10 | 1.10 | 1.10 | 1.10 | 0/0/0/0 |
| candidate | foreground block 5 | actual_coordinator_lease_hold:begin | 1 | 0.33 | 0.33 | 0.33 | 0.33 | 0/0/0/0 |
| candidate | foreground block 5 | actual_coordinator_lease_hold:synthetic | 2000 | 1.02 | 1.27 | 1.39 | 2.51 | 0/0/0/0 |
| candidate | foreground block 5 | actual_coordinator_lease_hold:final | 1 | 0.30 | 0.30 | 0.30 | 0.30 | 0/0/0/0 |

| Variant | Input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | idle block 1 | operation_total | 1 | 104805.96 | 104805.96 | 104805.96 | 104805.96 | 1/1/1/1 |
| baseline | idle block 1 | dispatch_gap | 2000 | 45.57 | 49.86 | 50.48 | 51.79 | 0/0/0/0 |
| baseline | idle block 1 | dispatch_gap_target_burst | 393 | 45.52 | 49.88 | 50.40 | 51.79 | 0/0/0/0 |
| baseline | idle block 1 | dispatch_gap_target_quiet | 1607 | 45.59 | 49.82 | 50.47 | 50.82 | 0/0/0/0 |
| baseline | idle block 1 | event_loop_lag | 19396 | 0.36 | 0.94 | 1.20 | 4.53 | 0/0/0/0 |
| baseline | idle block 1 | block_wall | 1 | 104857.22 | 104857.22 | 104857.22 | 104857.22 | 1/1/1/1 |
| baseline | idle block 1 | block_cpu | 1 | 10498.39 | 10498.39 | 10498.39 | 10498.39 | 1/1/1/1 |
| baseline | idle block 2 | operation_total | 1 | 105144.73 | 105144.73 | 105144.73 | 105144.73 | 1/1/1/1 |
| baseline | idle block 2 | dispatch_gap | 2000 | 45.52 | 50.11 | 50.60 | 51.00 | 0/0/0/0 |
| baseline | idle block 2 | dispatch_gap_target_burst | 403 | 45.56 | 50.06 | 50.35 | 50.81 | 0/0/0/0 |
| baseline | idle block 2 | dispatch_gap_target_quiet | 1597 | 45.52 | 50.15 | 50.62 | 51.00 | 0/0/0/0 |
| baseline | idle block 2 | event_loop_lag | 19291 | 0.39 | 1.03 | 1.22 | 2.76 | 0/0/0/0 |
| baseline | idle block 2 | block_wall | 1 | 105145.71 | 105145.71 | 105145.71 | 105145.71 | 1/1/1/1 |
| baseline | idle block 2 | block_cpu | 1 | 11797.47 | 11797.47 | 11797.47 | 11797.47 | 1/1/1/1 |
| baseline | idle block 3 | operation_total | 1 | 105220.32 | 105220.32 | 105220.32 | 105220.32 | 1/1/1/1 |
| baseline | idle block 3 | dispatch_gap | 2000 | 45.53 | 50.18 | 50.57 | 51.08 | 0/0/0/0 |
| baseline | idle block 3 | dispatch_gap_target_burst | 419 | 45.57 | 50.16 | 50.49 | 50.90 | 0/0/0/0 |
| baseline | idle block 3 | dispatch_gap_target_quiet | 1581 | 45.51 | 50.18 | 50.57 | 51.08 | 0/0/0/0 |
| baseline | idle block 3 | event_loop_lag | 19297 | 0.39 | 1.02 | 1.21 | 2.76 | 0/0/0/0 |
| baseline | idle block 3 | block_wall | 1 | 105221.17 | 105221.17 | 105221.17 | 105221.17 | 1/1/1/1 |
| baseline | idle block 3 | block_cpu | 1 | 11951.86 | 11951.86 | 11951.86 | 11951.86 | 1/1/1/1 |
| baseline | idle block 4 | operation_total | 1 | 105273.82 | 105273.82 | 105273.82 | 105273.82 | 1/1/1/1 |
| baseline | idle block 4 | dispatch_gap | 2000 | 45.54 | 50.21 | 50.59 | 51.12 | 0/0/0/0 |
| baseline | idle block 4 | dispatch_gap_target_burst | 405 | 45.51 | 50.29 | 50.55 | 50.67 | 0/0/0/0 |
| baseline | idle block 4 | dispatch_gap_target_quiet | 1595 | 45.58 | 50.19 | 50.59 | 51.12 | 0/0/0/0 |
| baseline | idle block 4 | event_loop_lag | 19285 | 0.40 | 1.04 | 1.23 | 4.76 | 0/0/0/0 |
| baseline | idle block 4 | block_wall | 1 | 105274.66 | 105274.66 | 105274.66 | 105274.66 | 1/1/1/1 |
| baseline | idle block 4 | block_cpu | 1 | 12042.15 | 12042.15 | 12042.15 | 12042.15 | 1/1/1/1 |
| baseline | idle block 5 | operation_total | 1 | 105121.80 | 105121.80 | 105121.80 | 105121.80 | 1/1/1/1 |
| baseline | idle block 5 | dispatch_gap | 2000 | 45.50 | 50.11 | 50.53 | 51.00 | 0/0/0/0 |
| baseline | idle block 5 | dispatch_gap_target_burst | 403 | 45.67 | 50.11 | 50.47 | 50.64 | 0/0/0/0 |
| baseline | idle block 5 | dispatch_gap_target_quiet | 1597 | 45.49 | 50.10 | 50.52 | 51.00 | 0/0/0/0 |
| baseline | idle block 5 | event_loop_lag | 19307 | 0.39 | 1.02 | 1.21 | 2.66 | 0/0/0/0 |
| baseline | idle block 5 | block_wall | 1 | 105122.64 | 105122.64 | 105122.64 | 105122.64 | 1/1/1/1 |
| baseline | idle block 5 | block_cpu | 1 | 11686.83 | 11686.83 | 11686.83 | 11686.83 | 1/1/1/1 |
| baseline | foreground block 1 | operation_total | 1 | 111264.15 | 111264.15 | 111264.15 | 111264.15 | 1/1/1/1 |
| baseline | foreground block 1 | dispatch_gap | 2000 | 46.67 | 70.79 | 75.52 | 81.76 | 0/0/0/0 |
| baseline | foreground block 1 | foreground_queued_completion | 1120 | 6.55 | 55.76 | 57.71 | 59.37 | 0/0/0/0 |
| baseline | foreground block 1 | foreground_arrival_drift | 1120 | 9.56 | 36.08 | 38.42 | 39.83 | 0/0/0/0 |
| baseline | foreground block 1 | dispatch_gap_target_burst | 318 | 64.19 | 75.71 | 77.47 | 78.24 | 0/0/0/0 |
| baseline | foreground block 1 | dispatch_gap_target_quiet | 1682 | 45.74 | 50.55 | 65.45 | 81.76 | 0/0/0/0 |
| baseline | foreground block 1 | event_loop_lag | 20376 | 0.39 | 1.08 | 1.32 | 3.64 | 0/0/0/0 |
| baseline | foreground block 1 | block_wall | 1 | 111343.98 | 111343.98 | 111343.98 | 111343.98 | 1/1/1/1 |
| baseline | foreground block 1 | block_cpu | 1 | 18458.52 | 18458.52 | 18458.52 | 18458.52 | 1/1/1/1 |
| baseline | foreground block 2 | operation_total | 1 | 111482.68 | 111482.68 | 111482.68 | 111482.68 | 1/1/1/1 |
| baseline | foreground block 2 | dispatch_gap | 2000 | 46.66 | 71.46 | 76.94 | 86.25 | 0/0/0/0 |
| baseline | foreground block 2 | foreground_queued_completion | 1120 | 6.59 | 54.62 | 57.44 | 59.29 | 0/0/0/0 |
| baseline | foreground block 2 | foreground_arrival_drift | 1120 | 8.34 | 35.16 | 38.19 | 40.01 | 0/0/0/0 |
| baseline | foreground block 2 | dispatch_gap_target_burst | 311 | 66.86 | 77.17 | 78.49 | 86.25 | 0/0/0/0 |
| baseline | foreground block 2 | dispatch_gap_target_quiet | 1689 | 45.87 | 50.52 | 67.59 | 77.69 | 0/0/0/0 |
| baseline | foreground block 2 | event_loop_lag | 20384 | 0.39 | 1.09 | 1.33 | 3.20 | 0/0/0/0 |
| baseline | foreground block 2 | block_wall | 1 | 111485.53 | 111485.53 | 111485.53 | 111485.53 | 1/1/1/1 |
| baseline | foreground block 2 | block_cpu | 1 | 18786.93 | 18786.93 | 18786.93 | 18786.93 | 1/1/1/1 |
| baseline | foreground block 3 | operation_total | 1 | 111522.00 | 111522.00 | 111522.00 | 111522.00 | 1/1/1/1 |
| baseline | foreground block 3 | dispatch_gap | 2000 | 46.67 | 70.80 | 76.34 | 79.11 | 0/0/0/0 |
| baseline | foreground block 3 | foreground_queued_completion | 1120 | 6.58 | 54.59 | 56.68 | 59.72 | 0/0/0/0 |
| baseline | foreground block 3 | foreground_arrival_drift | 1120 | 8.37 | 35.12 | 37.19 | 39.85 | 0/0/0/0 |
| baseline | foreground block 3 | dispatch_gap_target_burst | 309 | 65.62 | 76.47 | 77.93 | 79.11 | 0/0/0/0 |
| baseline | foreground block 3 | dispatch_gap_target_quiet | 1691 | 45.86 | 50.67 | 67.69 | 79.01 | 0/0/0/0 |
| baseline | foreground block 3 | event_loop_lag | 20380 | 0.39 | 1.09 | 1.30 | 2.64 | 0/0/0/0 |
| baseline | foreground block 3 | block_wall | 1 | 111524.82 | 111524.82 | 111524.82 | 111524.82 | 1/1/1/1 |
| baseline | foreground block 3 | block_cpu | 1 | 18792.18 | 18792.18 | 18792.18 | 18792.18 | 1/1/1/1 |
| baseline | foreground block 4 | operation_total | 1 | 111339.19 | 111339.19 | 111339.19 | 111339.19 | 1/1/1/1 |
| baseline | foreground block 4 | dispatch_gap | 2000 | 46.63 | 71.27 | 76.42 | 82.53 | 0/0/0/0 |
| baseline | foreground block 4 | foreground_queued_completion | 1120 | 6.60 | 54.47 | 57.33 | 58.93 | 0/0/0/0 |
| baseline | foreground block 4 | foreground_arrival_drift | 1120 | 8.06 | 34.79 | 38.12 | 39.15 | 0/0/0/0 |
| baseline | foreground block 4 | dispatch_gap_target_burst | 309 | 66.25 | 76.53 | 78.38 | 82.53 | 0/0/0/0 |
| baseline | foreground block 4 | dispatch_gap_target_quiet | 1691 | 45.86 | 50.70 | 64.86 | 78.14 | 0/0/0/0 |
| baseline | foreground block 4 | event_loop_lag | 20351 | 0.39 | 1.10 | 1.30 | 2.45 | 0/0/0/0 |
| baseline | foreground block 4 | block_wall | 1 | 111341.93 | 111341.93 | 111341.93 | 111341.93 | 1/1/1/1 |
| baseline | foreground block 4 | block_cpu | 1 | 18805.47 | 18805.47 | 18805.47 | 18805.47 | 1/1/1/1 |
| baseline | foreground block 5 | operation_total | 1 | 111025.73 | 111025.73 | 111025.73 | 111025.73 | 1/1/1/1 |
| baseline | foreground block 5 | dispatch_gap | 2000 | 46.49 | 70.86 | 76.92 | 86.04 | 0/0/0/0 |
| baseline | foreground block 5 | foreground_queued_completion | 1112 | 6.56 | 54.43 | 57.42 | 61.50 | 0/0/0/0 |
| baseline | foreground block 5 | foreground_arrival_drift | 1112 | 8.29 | 35.08 | 38.16 | 42.02 | 0/0/0/0 |
| baseline | foreground block 5 | dispatch_gap_target_burst | 317 | 65.70 | 76.98 | 78.28 | 86.04 | 0/0/0/0 |
| baseline | foreground block 5 | dispatch_gap_target_quiet | 1683 | 45.82 | 50.59 | 65.03 | 77.72 | 0/0/0/0 |
| baseline | foreground block 5 | event_loop_lag | 20314 | 0.39 | 1.08 | 1.31 | 3.92 | 0/0/0/0 |
| baseline | foreground block 5 | block_wall | 1 | 111027.92 | 111027.92 | 111027.92 | 111027.92 | 1/1/1/1 |
| baseline | foreground block 5 | block_cpu | 1 | 18458.63 | 18458.63 | 18458.63 | 18458.63 | 1/1/1/1 |
| candidate | idle block 1 | operation_total | 1 | 15669.63 | 15669.63 | 15669.63 | 15669.63 | 1/1/1/1 |
| candidate | idle block 1 | dispatch_gap | 2000 | 1.23 | 1.52 | 1.63 | 2.05 | 0/0/0/0 |
| candidate | idle block 1 | dispatch_gap_target_burst | 423 | 1.08 | 1.49 | 1.58 | 1.92 | 0/0/0/0 |
| candidate | idle block 1 | dispatch_gap_target_quiet | 1577 | 1.25 | 1.52 | 1.63 | 2.05 | 0/0/0/0 |
| candidate | idle block 1 | event_loop_lag | 2832 | 0.49 | 1.11 | 1.25 | 1.37 | 0/0/0/0 |
| candidate | idle block 1 | block_wall | 1 | 15670.71 | 15670.71 | 15670.71 | 15670.71 | 1/1/1/1 |
| candidate | idle block 1 | block_cpu | 1 | 5062.57 | 5062.57 | 5062.57 | 5062.57 | 1/1/1/1 |
| candidate | idle block 2 | operation_total | 1 | 15181.50 | 15181.50 | 15181.50 | 15181.50 | 1/1/1/1 |
| candidate | idle block 2 | dispatch_gap | 2000 | 1.10 | 1.54 | 1.69 | 3.96 | 0/0/0/0 |
| candidate | idle block 2 | dispatch_gap_target_burst | 419 | 1.18 | 1.56 | 1.69 | 1.85 | 0/0/0/0 |
| candidate | idle block 2 | dispatch_gap_target_quiet | 1581 | 1.09 | 1.53 | 1.68 | 3.96 | 0/0/0/0 |
| candidate | idle block 2 | event_loop_lag | 2739 | 0.52 | 1.09 | 1.23 | 2.95 | 0/0/0/0 |
| candidate | idle block 2 | block_wall | 1 | 15181.79 | 15181.79 | 15181.79 | 15181.79 | 1/1/1/1 |
| candidate | idle block 2 | block_cpu | 1 | 4493.15 | 4493.15 | 4493.15 | 4493.15 | 1/1/1/1 |
| candidate | idle block 3 | operation_total | 1 | 15668.52 | 15668.52 | 15668.52 | 15668.52 | 1/1/1/1 |
| candidate | idle block 3 | dispatch_gap | 2000 | 1.25 | 1.53 | 1.70 | 1.93 | 0/0/0/0 |
| candidate | idle block 3 | dispatch_gap_target_burst | 415 | 1.23 | 1.53 | 1.66 | 1.78 | 0/0/0/0 |
| candidate | idle block 3 | dispatch_gap_target_quiet | 1585 | 1.25 | 1.54 | 1.70 | 1.93 | 0/0/0/0 |
| candidate | idle block 3 | event_loop_lag | 2829 | 0.49 | 1.09 | 1.25 | 1.43 | 0/0/0/0 |
| candidate | idle block 3 | block_wall | 1 | 15671.65 | 15671.65 | 15671.65 | 15671.65 | 1/1/1/1 |
| candidate | idle block 3 | block_cpu | 1 | 5081.33 | 5081.33 | 5081.33 | 5081.33 | 1/1/1/1 |
| candidate | idle block 4 | operation_total | 1 | 15491.69 | 15491.69 | 15491.69 | 15491.69 | 1/1/1/1 |
| candidate | idle block 4 | dispatch_gap | 2000 | 1.24 | 1.56 | 1.70 | 2.62 | 0/0/0/0 |
| candidate | idle block 4 | dispatch_gap_target_burst | 411 | 1.20 | 1.56 | 1.77 | 2.62 | 0/0/0/0 |
| candidate | idle block 4 | dispatch_gap_target_quiet | 1589 | 1.24 | 1.56 | 1.68 | 2.02 | 0/0/0/0 |
| candidate | idle block 4 | event_loop_lag | 2798 | 0.50 | 1.10 | 1.24 | 1.37 | 0/0/0/0 |
| candidate | idle block 4 | block_wall | 1 | 15492.18 | 15492.18 | 15492.18 | 15492.18 | 1/1/1/1 |
| candidate | idle block 4 | block_cpu | 1 | 4888.90 | 4888.90 | 4888.90 | 4888.90 | 1/1/1/1 |
| candidate | idle block 5 | operation_total | 1 | 15618.03 | 15618.03 | 15618.03 | 15618.03 | 1/1/1/1 |
| candidate | idle block 5 | dispatch_gap | 2000 | 1.24 | 1.52 | 1.71 | 2.44 | 0/0/0/0 |
| candidate | idle block 5 | dispatch_gap_target_burst | 404 | 1.19 | 1.54 | 1.67 | 2.44 | 0/0/0/0 |
| candidate | idle block 5 | dispatch_gap_target_quiet | 1596 | 1.25 | 1.52 | 1.71 | 1.90 | 0/0/0/0 |
| candidate | idle block 5 | event_loop_lag | 2822 | 0.50 | 1.10 | 1.24 | 1.36 | 0/0/0/0 |
| candidate | idle block 5 | block_wall | 1 | 15618.94 | 15618.94 | 15618.94 | 15618.94 | 1/1/1/1 |
| candidate | idle block 5 | block_cpu | 1 | 5006.05 | 5006.05 | 5006.05 | 5006.05 | 1/1/1/1 |
| candidate | foreground block 1 | operation_total | 1 | 16342.33 | 16342.33 | 16342.33 | 16342.33 | 1/1/1/1 |
| candidate | foreground block 1 | dispatch_gap | 2000 | 1.27 | 2.67 | 7.34 | 8.51 | 0/0/0/0 |
| candidate | foreground block 1 | foreground_queued_completion | 170 | 7.00 | 7.90 | 8.62 | 9.43 | 0/0/0/0 |
| candidate | foreground block 1 | foreground_arrival_drift | 170 | 0.60 | 1.15 | 1.25 | 1.33 | 0/0/0/0 |
| candidate | foreground block 1 | dispatch_gap_target_burst | 353 | 1.33 | 7.45 | 8.26 | 8.51 | 0/0/0/0 |
| candidate | foreground block 1 | dispatch_gap_target_quiet | 1647 | 1.26 | 1.55 | 1.73 | 8.46 | 0/0/0/0 |
| candidate | foreground block 1 | event_loop_lag | 2938 | 0.51 | 1.14 | 1.31 | 2.38 | 0/0/0/0 |
| candidate | foreground block 1 | block_wall | 1 | 16343.37 | 16343.37 | 16343.37 | 16343.37 | 1/1/1/1 |
| candidate | foreground block 1 | block_cpu | 1 | 6357.26 | 6357.26 | 6357.26 | 6357.26 | 1/1/1/1 |
| candidate | foreground block 2 | operation_total | 1 | 16130.73 | 16130.73 | 16130.73 | 16130.73 | 1/1/1/1 |
| candidate | foreground block 2 | dispatch_gap | 2000 | 1.26 | 1.78 | 7.52 | 11.02 | 0/0/0/0 |
| candidate | foreground block 2 | foreground_queued_completion | 167 | 6.85 | 8.43 | 9.97 | 11.24 | 0/0/0/0 |
| candidate | foreground block 2 | foreground_arrival_drift | 167 | 0.64 | 1.13 | 1.28 | 1.42 | 0/0/0/0 |
| candidate | foreground block 2 | dispatch_gap_target_burst | 373 | 1.20 | 7.52 | 8.34 | 11.02 | 0/0/0/0 |
| candidate | foreground block 2 | dispatch_gap_target_quiet | 1627 | 1.26 | 1.55 | 1.67 | 8.11 | 0/0/0/0 |
| candidate | foreground block 2 | event_loop_lag | 2897 | 0.52 | 1.15 | 1.29 | 2.11 | 0/0/0/0 |
| candidate | foreground block 2 | block_wall | 1 | 16131.68 | 16131.68 | 16131.68 | 16131.68 | 1/1/1/1 |
| candidate | foreground block 2 | block_cpu | 1 | 6053.53 | 6053.53 | 6053.53 | 6053.53 | 1/1/1/1 |
| candidate | foreground block 3 | operation_total | 1 | 15975.71 | 15975.71 | 15975.71 | 15975.71 | 1/1/1/1 |
| candidate | foreground block 3 | dispatch_gap | 2000 | 1.25 | 1.68 | 6.88 | 8.45 | 0/0/0/0 |
| candidate | foreground block 3 | foreground_queued_completion | 160 | 6.75 | 7.60 | 8.21 | 8.37 | 0/0/0/0 |
| candidate | foreground block 3 | foreground_arrival_drift | 160 | 0.57 | 1.24 | 1.30 | 1.45 | 0/0/0/0 |
| candidate | foreground block 3 | dispatch_gap_target_burst | 376 | 1.10 | 6.88 | 8.11 | 8.45 | 0/0/0/0 |
| candidate | foreground block 3 | dispatch_gap_target_quiet | 1624 | 1.25 | 1.53 | 1.67 | 7.12 | 0/0/0/0 |
| candidate | foreground block 3 | event_loop_lag | 2875 | 0.51 | 1.15 | 1.32 | 2.25 | 0/0/0/0 |
| candidate | foreground block 3 | block_wall | 1 | 15976.66 | 15976.66 | 15976.66 | 15976.66 | 1/1/1/1 |
| candidate | foreground block 3 | block_cpu | 1 | 5886.64 | 5886.64 | 5886.64 | 5886.64 | 1/1/1/1 |
| candidate | foreground block 4 | operation_total | 1 | 15882.93 | 15882.93 | 15882.93 | 15882.93 | 1/1/1/1 |
| candidate | foreground block 4 | dispatch_gap | 2000 | 1.22 | 1.69 | 6.49 | 8.51 | 0/0/0/0 |
| candidate | foreground block 4 | foreground_queued_completion | 160 | 6.72 | 7.95 | 8.53 | 9.93 | 0/0/0/0 |
| candidate | foreground block 4 | foreground_arrival_drift | 160 | 0.57 | 1.18 | 1.30 | 1.54 | 0/0/0/0 |
| candidate | foreground block 4 | dispatch_gap_target_burst | 378 | 1.12 | 6.49 | 8.18 | 8.51 | 0/0/0/0 |
| candidate | foreground block 4 | dispatch_gap_target_quiet | 1622 | 1.23 | 1.53 | 1.63 | 6.59 | 0/0/0/0 |
| candidate | foreground block 4 | event_loop_lag | 2857 | 0.51 | 1.15 | 1.30 | 2.35 | 0/0/0/0 |
| candidate | foreground block 4 | block_wall | 1 | 15883.84 | 15883.84 | 15883.84 | 15883.84 | 1/1/1/1 |
| candidate | foreground block 4 | block_cpu | 1 | 5792.66 | 5792.66 | 5792.66 | 5792.66 | 1/1/1/1 |
| candidate | foreground block 5 | operation_total | 1 | 15976.51 | 15976.51 | 15976.51 | 15976.51 | 1/1/1/1 |
| candidate | foreground block 5 | dispatch_gap | 2000 | 1.24 | 1.86 | 7.05 | 9.85 | 0/0/0/0 |
| candidate | foreground block 5 | foreground_queued_completion | 160 | 6.80 | 7.62 | 8.24 | 9.61 | 0/0/0/0 |
| candidate | foreground block 5 | foreground_arrival_drift | 160 | 0.54 | 1.24 | 1.27 | 1.33 | 0/0/0/0 |
| candidate | foreground block 5 | dispatch_gap_target_burst | 365 | 1.22 | 7.05 | 8.20 | 9.85 | 0/0/0/0 |
| candidate | foreground block 5 | dispatch_gap_target_quiet | 1635 | 1.25 | 1.53 | 1.64 | 7.01 | 0/0/0/0 |
| candidate | foreground block 5 | event_loop_lag | 2879 | 0.50 | 1.14 | 1.30 | 2.02 | 0/0/0/0 |
| candidate | foreground block 5 | block_wall | 1 | 15976.89 | 15976.89 | 15976.89 | 15976.89 | 1/1/1/1 |
| candidate | foreground block 5 | block_cpu | 1 | 5901.16 | 5901.16 | 5901.16 | 5901.16 | 1/1/1/1 |

Counts aggregate all measured repetitions for this row. Runtime rows contain one repetition each.

| Variant | Input | Statement and diagnostic counts | Commits | Commit sum, ms |
| --- | --- | --- | --- | --- |
| baseline | idle block 1 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| baseline | idle block 2 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| baseline | idle block 3 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| baseline | idle block 4 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| baseline | idle block 5 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| baseline | foreground block 1 | UPDATE=1120, foreground_jobs=1120, ticks=4741, idle_wakes=2004 | 1120 | 66.49 |
| baseline | foreground block 2 | UPDATE=1120, foreground_jobs=1120, ticks=4743, idle_wakes=2002 | 1120 | 69.81 |
| baseline | foreground block 3 | UPDATE=1120, foreground_jobs=1120, ticks=4742, idle_wakes=2003 | 1120 | 65.36 |
| baseline | foreground block 4 | UPDATE=1120, foreground_jobs=1120, ticks=4732, idle_wakes=2002 | 1120 | 65.64 |
| baseline | foreground block 5 | UPDATE=1112, foreground_jobs=1112, ticks=4719, idle_wakes=1987 | 1112 | 65.63 |
| candidate | idle block 1 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| candidate | idle block 2 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| candidate | idle block 3 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| candidate | idle block 4 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| candidate | idle block 5 | foreground_jobs=0, ticks=4005, idle_wakes=2001 | 0 | 0.00 |
| candidate | foreground block 1 | UPDATE=170, foreground_jobs=170, ticks=4066, idle_wakes=2023 | 170 | 12.04 |
| candidate | foreground block 2 | UPDATE=167, foreground_jobs=167, ticks=4091, idle_wakes=2030 | 167 | 9.75 |
| candidate | foreground block 3 | UPDATE=160, foreground_jobs=160, ticks=4109, idle_wakes=2042 | 160 | 9.00 |
| candidate | foreground block 4 | UPDATE=160, foreground_jobs=160, ticks=4086, idle_wakes=2029 | 160 | 7.90 |
| candidate | foreground block 5 | UPDATE=160, foreground_jobs=160, ticks=4083, idle_wakes=2029 | 160 | 8.80 |


## Publication boundary and preparation cross-check

Each row selects the input case with the largest maximum for that output and boundary type.
Its percentiles describe that selected input, not an aggregate across different sizes.
Transaction timing includes the initial write execution that the lower-bound SQLite span excludes.
Owned transaction timing also includes the surrounding driver context.
Neither unit metric establishes an application-wide lease; the mixed tables measure actual coordinator leases.
The raw SQLite probe can delay initial acquisition. Its contention differs from foreground-priority application admission.
The complete per-case JSON retains all boundaries, helper preparation timings, and executor queue observations.

| Output | Selected input | Metric, ms | n | p50 | p95 | p99 | max | >100/250/500/1000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| audited-lifecycle-baseline | 400, unchanged | stopped_agents:write_acquisition_and_guard | 5 | 0.15 | 0.16 | 0.16 | 8.72 | 0/0/0/0 |
| audited-lifecycle-baseline | 1000, changed | stopped_agents:transaction | 5 | 9342.65 | 9637.97 | 9637.97 | 9947.34 | 5/5/5/5 |
| audited-lifecycle-baseline | 1000, changed | stopped_agents:owned_transaction | 5 | 9344.76 | 9640.05 | 9640.05 | 9949.69 | 5/5/5/5 |
| audited-lifecycle-candidate | 400, mixed | stopped_agents:write_acquisition_and_guard | 35 | 0.01 | 8.24 | 18.53 | 18.58 | 0/0/0/0 |
| audited-lifecycle-candidate | 400, mixed | stopped_agents:transaction | 35 | 18.46 | 31.17 | 38.90 | 41.72 | 0/0/0/0 |
| audited-lifecycle-candidate | 400, mixed | stopped_agents:owned_transaction | 35 | 18.53 | 31.27 | 39.01 | 41.88 | 0/0/0/0 |
| audited-facts-baseline | 40000, 128 | facts_atomic:write_acquisition_and_guard | 5 | 0.05 | 0.05 | 0.05 | 0.05 | 0/0/0/0 |
| audited-facts-baseline | 75000, 128 | facts_atomic:transaction | 5 | 590.04 | 590.56 | 590.56 | 718.62 | 5/5/5/0 |
| audited-facts-baseline | 75000, 128 | facts_atomic:owned_transaction | 5 | 590.09 | 590.60 | 590.60 | 718.69 | 5/5/5/0 |
| corrected-facts-128-candidate | 75000, 128 | facts_delete:write_acquisition_and_guard | 2930 | 0.03 | 8.96 | 9.54 | 19.87 | 0/0/0/0 |
| corrected-facts-128-candidate | 75000, 128 | facts_delete:transaction | 2930 | 6.49 | 17.41 | 23.81 | 48.76 | 0/0/0/0 |
| corrected-facts-128-candidate | 75000, 128 | facts_delete:owned_transaction | 2930 | 6.62 | 17.57 | 23.99 | 48.92 | 0/0/0/0 |
| corrected-facts-128-candidate | 75000, 128 | facts_delete:prepare:call | 2930 | 0.96 | 1.22 | 1.46 | 3.36 | 0/0/0/0 |
| corrected-facts-256-candidate | 40000, 256 | facts_insert:write_acquisition_and_guard | 785 | 0.04 | 8.82 | 9.04 | 79.49 | 0/0/0/0 |
| corrected-facts-256-candidate | 40000, 256 | facts_insert:transaction | 785 | 7.78 | 22.63 | 30.79 | 111.39 | 1/0/0/0 |
| corrected-facts-256-candidate | 40000, 256 | facts_insert:owned_transaction | 785 | 7.89 | 22.76 | 30.94 | 111.46 | 1/0/0/0 |
| corrected-facts-256-candidate | 75000, 256 | facts_delete:prepare:call | 1465 | 1.04 | 1.53 | 1.79 | 3.06 | 0/0/0/0 |
| audited-maintenance-baseline | distinct, 8388600 | maintenance_publish:write_acquisition_and_guard | 15 | 0.01 | 8.46 | 8.46 | 8.90 | 0/0/0/0 |
| audited-maintenance-baseline | shared, 333000 | maintenance_publish:transaction | 5 | 355.58 | 359.17 | 359.17 | 430.34 | 5/5/0/0 |
| audited-maintenance-baseline | shared, 333000 | maintenance_publish:owned_transaction | 5 | 355.64 | 359.24 | 359.24 | 430.39 | 5/5/0/0 |
| audited-maintenance-candidate | shared, 8388600 | maintenance_publish:write_acquisition_and_guard | 15 | 0.02 | 8.59 | 8.59 | 9.65 | 0/0/0/0 |
| audited-maintenance-candidate | overlap, 12592812 | maintenance_publish:transaction | 10 | 50.23 | 272.40 | 272.40 | 275.80 | 5/4/0/0 |
| audited-maintenance-candidate | overlap, 12592812 | maintenance_publish:owned_transaction | 10 | 50.28 | 272.51 | 272.51 | 275.89 | 5/4/0/0 |
| audited-validation-baseline | 200000, warm | validation_final:write_acquisition_and_guard | 5 | 0.04 | 0.05 | 0.05 | 19.85 | 0/0/0/0 |
| audited-validation-baseline | 200000, warm | validation_final:transaction | 5 | 190.60 | 203.90 | 203.90 | 210.67 | 5/0/0/0 |
| audited-validation-baseline | 200000, warm | validation_final:owned_transaction | 5 | 190.65 | 203.95 | 203.95 | 210.71 | 5/0/0/0 |
| audited-validation-candidate-corrected | 200000, reopened | validation_publish:write_acquisition_and_guard | 3915 | 3.38 | 8.61 | 8.67 | 19.94 | 0/0/0/0 |
| audited-validation-candidate-corrected | 100000, reopened | validation_publish:transaction | 1960 | 11.34 | 16.88 | 20.68 | 40.19 | 0/0/0/0 |
| audited-validation-candidate-corrected | 10000, warm | validation_final:owned_transaction | 5 | 22.30 | 24.23 | 24.23 | 31.26 | 0/0/0/0 |

### Mixed whole-operation preparation and admission

Each observation covers one complete operation. Admission is cumulative across all of its grants.

| Output | Repetition | Metric, ms | n | p50 | p95 | p99 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| audited-mixed-baseline | 1 | operation_preparation | 1 | 1661.94 | 1661.94 | 1661.94 | 1661.94 |
| audited-mixed-baseline | 1 | operation_admission_wait | 1 | 6542.54 | 6542.54 | 6542.54 | 6542.54 |
| audited-mixed-baseline | 1 | mixed_compute_completion | 1 | 14182.97 | 14182.97 | 14182.97 | 14182.97 |
| audited-mixed-baseline | 1 | operation_total | 1 | 14164.81 | 14164.81 | 14164.81 | 14164.81 |
| audited-mixed-baseline | 2 | operation_preparation | 1 | 1713.03 | 1713.03 | 1713.03 | 1713.03 |
| audited-mixed-baseline | 2 | operation_admission_wait | 1 | 2658.23 | 2658.23 | 2658.23 | 2658.23 |
| audited-mixed-baseline | 2 | mixed_compute_completion | 1 | 14966.74 | 14966.74 | 14966.74 | 14966.74 |
| audited-mixed-baseline | 2 | operation_total | 1 | 10522.17 | 10522.17 | 10522.17 | 10522.17 |
| audited-mixed-baseline | 3 | operation_preparation | 1 | 1718.99 | 1718.99 | 1718.99 | 1718.99 |
| audited-mixed-baseline | 3 | operation_admission_wait | 1 | 5701.56 | 5701.56 | 5701.56 | 5701.56 |
| audited-mixed-baseline | 3 | mixed_compute_completion | 1 | 14405.14 | 14405.14 | 14405.14 | 14405.14 |
| audited-mixed-baseline | 3 | operation_total | 1 | 13448.02 | 13448.02 | 13448.02 | 13448.02 |
| audited-mixed-baseline | 4 | operation_preparation | 1 | 1718.09 | 1718.09 | 1718.09 | 1718.09 |
| audited-mixed-baseline | 4 | operation_admission_wait | 1 | 2593.21 | 2593.21 | 2593.21 | 2593.21 |
| audited-mixed-baseline | 4 | mixed_compute_completion | 1 | 14825.34 | 14825.34 | 14825.34 | 14825.34 |
| audited-mixed-baseline | 4 | operation_total | 1 | 10452.78 | 10452.78 | 10452.78 | 10452.78 |
| audited-mixed-baseline | 5 | operation_preparation | 1 | 1699.61 | 1699.61 | 1699.61 | 1699.61 |
| audited-mixed-baseline | 5 | operation_admission_wait | 1 | 6450.47 | 6450.47 | 6450.47 | 6450.47 |
| audited-mixed-baseline | 5 | mixed_compute_completion | 1 | 14690.54 | 14690.54 | 14690.54 | 14690.54 |
| audited-mixed-baseline | 5 | operation_total | 1 | 14392.62 | 14392.62 | 14392.62 | 14392.62 |
| corrected-mixed-128-candidate | 1 | operation_preparation | 1 | 3505.13 | 3505.13 | 3505.13 | 3505.13 |
| corrected-mixed-128-candidate | 1 | operation_admission_wait | 1 | 6971.13 | 6971.13 | 6971.13 | 6971.13 |
| corrected-mixed-128-candidate | 1 | mixed_compute_completion | 1 | 19128.07 | 19128.07 | 19128.07 | 19128.07 |
| corrected-mixed-128-candidate | 1 | operation_total | 1 | 19111.98 | 19111.98 | 19111.98 | 19111.98 |
| corrected-mixed-128-candidate | 2 | operation_preparation | 1 | 3385.29 | 3385.29 | 3385.29 | 3385.29 |
| corrected-mixed-128-candidate | 2 | operation_admission_wait | 1 | 2863.55 | 2863.55 | 2863.55 | 2863.55 |
| corrected-mixed-128-candidate | 2 | mixed_compute_completion | 1 | 18726.55 | 18726.55 | 18726.55 | 18726.55 |
| corrected-mixed-128-candidate | 2 | operation_total | 1 | 14497.43 | 14497.43 | 14497.43 | 14497.43 |
| corrected-mixed-128-candidate | 3 | operation_preparation | 1 | 3611.43 | 3611.43 | 3611.43 | 3611.43 |
| corrected-mixed-128-candidate | 3 | operation_admission_wait | 1 | 6826.48 | 6826.48 | 6826.48 | 6826.48 |
| corrected-mixed-128-candidate | 3 | mixed_compute_completion | 1 | 18942.70 | 18942.70 | 18942.70 | 18942.70 |
| corrected-mixed-128-candidate | 3 | operation_total | 1 | 18926.15 | 18926.15 | 18926.15 | 18926.15 |
| corrected-mixed-128-candidate | 4 | operation_preparation | 1 | 3440.05 | 3440.05 | 3440.05 | 3440.05 |
| corrected-mixed-128-candidate | 4 | operation_admission_wait | 1 | 6480.74 | 6480.74 | 6480.74 | 6480.74 |
| corrected-mixed-128-candidate | 4 | mixed_compute_completion | 1 | 18634.90 | 18634.90 | 18634.90 | 18634.90 |
| corrected-mixed-128-candidate | 4 | operation_total | 1 | 18618.40 | 18618.40 | 18618.40 | 18618.40 |
| corrected-mixed-128-candidate | 5 | operation_preparation | 1 | 3415.57 | 3415.57 | 3415.57 | 3415.57 |
| corrected-mixed-128-candidate | 5 | operation_admission_wait | 1 | 3112.03 | 3112.03 | 3112.03 | 3112.03 |
| corrected-mixed-128-candidate | 5 | mixed_compute_completion | 1 | 18594.21 | 18594.21 | 18594.21 | 18594.21 |
| corrected-mixed-128-candidate | 5 | operation_total | 1 | 18175.37 | 18175.37 | 18175.37 | 18175.37 |
| corrected-mixed-256-candidate | 1 | operation_preparation | 1 | 3412.87 | 3412.87 | 3412.87 | 3412.87 |
| corrected-mixed-256-candidate | 1 | operation_admission_wait | 1 | 7351.44 | 7351.44 | 7351.44 | 7351.44 |
| corrected-mixed-256-candidate | 1 | mixed_compute_completion | 1 | 15657.81 | 15657.81 | 15657.81 | 15657.81 |
| corrected-mixed-256-candidate | 1 | operation_total | 1 | 15639.68 | 15639.68 | 15639.68 | 15639.68 |
| corrected-mixed-256-candidate | 2 | operation_preparation | 1 | 3461.22 | 3461.22 | 3461.22 | 3461.22 |
| corrected-mixed-256-candidate | 2 | operation_admission_wait | 1 | 6988.47 | 6988.47 | 6988.47 | 6988.47 |
| corrected-mixed-256-candidate | 2 | mixed_compute_completion | 1 | 15584.25 | 15584.25 | 15584.25 | 15584.25 |
| corrected-mixed-256-candidate | 2 | operation_total | 1 | 15446.64 | 15446.64 | 15446.64 | 15446.64 |
| corrected-mixed-256-candidate | 3 | operation_preparation | 1 | 3338.68 | 3338.68 | 3338.68 | 3338.68 |
| corrected-mixed-256-candidate | 3 | operation_admission_wait | 1 | 6528.13 | 6528.13 | 6528.13 | 6528.13 |
| corrected-mixed-256-candidate | 3 | mixed_compute_completion | 1 | 15600.26 | 15600.26 | 15600.26 | 15600.26 |
| corrected-mixed-256-candidate | 3 | operation_total | 1 | 15421.46 | 15421.46 | 15421.46 | 15421.46 |
| corrected-mixed-256-candidate | 4 | operation_preparation | 1 | 3389.66 | 3389.66 | 3389.66 | 3389.66 |
| corrected-mixed-256-candidate | 4 | operation_admission_wait | 1 | 6052.98 | 6052.98 | 6052.98 | 6052.98 |
| corrected-mixed-256-candidate | 4 | mixed_compute_completion | 1 | 15526.68 | 15526.68 | 15526.68 | 15526.68 |
| corrected-mixed-256-candidate | 4 | operation_total | 1 | 15511.66 | 15511.66 | 15511.66 | 15511.66 |
| corrected-mixed-256-candidate | 5 | operation_preparation | 1 | 3317.46 | 3317.46 | 3317.46 | 3317.46 |
| corrected-mixed-256-candidate | 5 | operation_admission_wait | 1 | 7179.13 | 7179.13 | 7179.13 | 7179.13 |
| corrected-mixed-256-candidate | 5 | mixed_compute_completion | 1 | 15666.59 | 15666.59 | 15666.59 | 15666.59 |
| corrected-mixed-256-candidate | 5 | operation_total | 1 | 15649.61 | 15649.61 | 15649.61 | 15649.61 |

## Evidence identity and verification

Temporary evidence remains under `/tmp/twicc-publication-bench-20261016`; it is not a permanent artifact store.
The repository retains the tables and methodology. Fresh execution requires the temporary scripts or an equivalent audited driver.
These SHA-256 values identify the final driver and accepted result files.

| Relative temporary path | SHA-256 |
| --- | --- |
| bench.py | fa704afb2000ecfc5f895eb3223945045ead9310125470d9ef5835ad7df9e272 |
| runtime.py | cab6a0d03bdd3999b38e4bdb46c2ccdc162a3a90aeddc755b510deafc007cc4d |
| queryplans.py | c1ec8fdff1459190dc0a0a875fec408985f32db3e2ae4e944a4ac9a9ab4d6a67 |
| facts-queryplans-corrected.py | 9c1480bc5a79849625db34d3ba952e0cd3870d283fe76bd6d3ca657e7fa7be12 |
| warning-tests.py | 0df420f260b6c6960b3b4fc7a6a4b5a8e4654945cda6f7ff0fca207159f2e7fb |
| import-timing.py | 946f49a4dddbddcc7b9eba1e180fd801c45ccabef5c1c7be411489cbf493c423 |
| footprints.py | 5ddb6ca201fdd9c7ded2333103324b675600feca65baf9e4212cb8df1d7723a3 |
| audited-lifecycle-baseline/results.json | 037131ddce78769c533ac794a07faccbef70c08b2a7086e5c5dbf37d34f014dc |
| audited-lifecycle-candidate/results.json | a5cf189a99a7eeabbd6efcca8a59700340912f6079b20ace09c2191b8ab92256 |
| audited-facts-baseline/results.json | 9d5bf56c1f1084f5b2fb22daafeb4bef962feedf347365a262409eecdd0875ba |
| corrected-facts-128-candidate/results.json | fbd2ed46559b2f21f8b216e19b606669f326c49ea1fc420ed8b6e0ba0d62c7da |
| corrected-facts-256-candidate/results.json | c4076fc79d3322bddf37b23341710f50047db6067b0762c453591adf9f7c821d |
| audited-maintenance-baseline/results.json | c53f02bf30882ccaa1447de8ca0dd45e5a21d613b27aa74c5e5ffb0897fe640a |
| audited-maintenance-candidate/results.json | f67d6cd4ce9332a75095646b29a3a9ccf24934f3a6cb9cce4663ecc5f2f52f9d |
| audited-validation-baseline/results.json | 0ce7523dd3be55f4097245af4b40e491075fc5d20fbcd22051a374c0cff247d6 |
| audited-validation-candidate-corrected/results.json | e95b6f574ae35acbbd9b2f7a8238a8a353101a2ee1026bd98d4741415d6f411a |
| audited-mixed-baseline/results.json | b10a1e7027d489c5ad06c7eefb3c126ce0fddf30e95ef849cea71a8e932e7fb3 |
| corrected-mixed-128-candidate/results.json | ff6f05f09299af5cf224a3ae813224d5dea140daabfc2b168f18819224a95172 |
| corrected-mixed-256-candidate/results.json | c9041313d98dffe34e8f4328bd0384eeabf0f668811f5c0008f921ba55e329c2 |
| audited-dispatch-baseline/results.json | 1e2fa0c1b91fb58cac8f2db7608c0f726c356df279c257fceb1316a9afb9f6cb |
| audited-dispatch-candidate/results.json | b7e35f3f68f83cb62f5603fa22b7e9be4bc97faef895468217ff70d20e6acb78 |

No budget or test source changes follow the final comparison. No owning test rerun is required for a budget change.
The isolated baseline warning check records `3 passed, 3 warnings in 34.13s`.
The controller starts a final full backend suite on `6101e880` after all timed work ends.
Its result and independent Task 8 review belong to the controller's final integration record.
`git diff --check` and a result-count audit run before the documentation commit.
