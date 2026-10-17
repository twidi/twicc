# Periodic maintenance selection validation

## Measurement boundaries

The `selection` scenario uses one disposable disk-backed source copy.
The source opens with SQLite `mode=ro`.
Only the copy receives index DDL.

The source copy receives no content publication.

The first stepped backup restarts during active source commits.
At 76 seconds, `/proc` reports 99,989,930,915 logical read bytes and 50,842,657,804 logical write bytes.
The incomplete destination contains only 1,702,887,424 bytes.

The benchmark process stops, and its incomplete database and journal are removed.
The replacement uses the existing `devctl.py` one-step `backup(pages=-1)` method.
Only one destination copy exists at a time.

A regression test proves snapshot consistency during a real concurrent WAL commit.

The free-space guard remains active.
Initial available space is 33,783,074,816 bytes.
The source occupies approximately 23 GiB.

No service stops, restarts, migrations, or source index changes occur.

Root measurements compare all-depth recovery discovery with periodic hot-root discovery.
These populations have different purposes.
Both variants use production selection and `recovery_promotion_targets()` with fresh `MaintenanceSessionCache` instances.

The old population includes native descendants.
The new population includes only extracted hot roots.
The isolated process has no active runtime agents; source selection uses persisted lifecycle dates.

Content measurements compare all-content metadata with plaintext metadata, before and after index creation.
Each variant uses the same production metadata projection and row budget.
The indexed variant uses the actual parameterized `_compression_metadata_query()` ORM path.

These trials do not publish representations or load payload values.
`Length(payload)` measures stored bytes without selecting payload values.

Each comparison closes its SQLite connection first.
Application caches are fresh, but the OS page cache remains uncontrolled.
Earlier variants can warm later variants.

Process IO deltas distinguish physical reads from logical reads.
SQLite can access overflow pages while selecting metadata after the payload column.
Zero Python payload loads does not imply zero payload-page IO.

No universal latency or full-pass percentage claim follows from these query measurements.
Existing instance activity continues during the benchmark.
Focused tests overlap the replacement backup, but finish before measured selection starts.

Other host activity remains uncontrolled.

Synthetic measurements use a separate disposable database and process.
Setup and dictionary loading remain outside dispatcher timing.
The production dispatcher handles compressible contents, a subsequent conformant pass, and restoration failures.

A clock adapter advances scheduling deadlines without waiting for production intervals.
The dispatcher has a 30-second trial timeout.
Expected injected exceptions are counted; their logs are suppressed.

A transient target fails once; a permanent target continues to fail.
A new unrelated cold candidate proves compression continues during retry waits.
The transient target must restore its actual references.

Synthetic contents include 10,000 current-policy compressed records and 20,000 real shared cold references.
Additional contents use 128-byte, 64-KiB, 1-MiB, and 16-MiB payloads.
They include repeated owners and shared owners.

Foreground transactions run concurrently through the production writer coordinator.
Foreground latency includes request-to-commit delay.
Per-stage foreground samples use the stage active when the request starts.

SQL stage counters use the stage active when SQL starts.
Metric event counters are cumulative within each operation; do not add progress events.
Restoration target metrics remain separate from parent compression metrics.

Index write trials run three repetitions per payload size, without and with the index.
They use production ingestion preparation, `insert_ingested_items()`, and representation preparation/publication.
Each trial measures insertion and zstd/plain transitions.

The final benchmark correction removes the index boolean from synthetic input construction.
All 12 paired samples now use identical bytes, verified by retained `input_sha256` values.
Both variants perform 24 successful transitions: 48 total, with every `changed` value equal to `true`.

Ingestion inserts cold prepared representations.
Restoration and compression add and remove partial-index entries.
Hot plaintext insertion is not timed separately.

Writer timings include transaction entry, production helper work, and commit.
They exclude coordinator queue time; these are isolated writer transactions.
Preparation timings include metadata reads and codec work outside the writer transaction.

Fixture creation, owner reassignment, deletion, and index DDL remain outside writer samples.
The sequential variant order and warm OS cache limit small timing differences.

## Results

Measured values and query plans are retained in `2026-10-17-periodic-maintenance-selection-results.json`.
The tables below record the completed disk-backed run.

| Root discovery | Selected identities | Reference probes | SQL statements | Cache IDs loaded | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| All-depth recovery baseline | 15,018 | 2,693 | 5,741 | 15,018 | 186.118 s |
| Periodic hot roots | 159 | 159 | 325 | 159 | 90.513 s |

Both root variants find zero restoration targets.
The clone already stores those hot references as plaintext.
The selected populations differ intentionally; hot descendants remain outside periodic discovery.

| Content metadata variant | Rows | SQL statements | Represented stored bytes | Duration |
| --- | ---: | ---: | ---: | ---: |
| All-content baseline | 3,974,629 | 39,748 | 12,087,893,705 | 292.883 s |
| Plaintext without index | 1,134,047 | 11,342 | 7,670,201,158 | 260.179 s |
| Production plaintext with index | 1,134,047 | 11,342 | 7,670,201,158 | 160.752 s |

Each content variant uses a 100-row budget and loads zero Python payload bytes.
The indexed and unindexed plaintext populations and byte totals match exactly.
The baseline represents 19,786,446,823 raw bytes; plaintext represents 7,670,201,158 raw bytes.

Plaintext owner metadata occupies 39,599,009 bytes, versus 139,501,117 bytes across all contents.

The baseline and unindexed filtered plan use `INTEGER PRIMARY KEY (rowid>?)`.
The actual parameterized production query uses `USING INDEX idx_content_plain_id (id>?)`.
Physical-read deltas are 16,101,892,096 bytes, 15,950,651,392 bytes, and 8,583,843,840 bytes, respectively.

The populated-copy index build takes **246.404 s**.
The index occupies **13,615,104 bytes**.
This populated-copy measurement does not replace the pending empty-table release-squash check.

The indexed selection takes 45.1% less elapsed time than this all-content baseline.
It takes 38.2% less time than this unindexed filtered query.
Root discovery takes 51.4% less elapsed time than this all-depth baseline.

These are single-run selection comparisons under the stated cache and IO conditions.
They are **not full-pass improvement percentages**.

| Actual dispatcher trial | Wall time, including startup recovery | Useful compression changes | Payload bytes loaded | Compression writer hold, total/max | Foreground commits; p95/max |
| --- | ---: | ---: | ---: | ---: | ---: |
| Compressible | 756.545 ms | 21 | 106,303,488 | 68.121 / 12.306 ms | 151; 8.872 / 18.064 ms |
| Conformant | 63.946 ms | 0 | 0 | 0 / 0 ms | 9; 10.111 / 10.111 ms |
| Restoration failures, two periodic passes | 5,957.542 ms | 1 | 10,000 | 4.456 / 4.456 ms | 1,181; 4.986 / 110.530 ms |

Synthetic setup takes 3,335.746 ms, outside dispatcher timings.
The fixture contains 10,025 contents before the separate failure candidate is added.
The compressible pass prepares 21 contents, publishes 12 batches, and saves 106,292,970 bytes.

The conformant pass examines four hot-owned plaintext contents and publishes no batches.
Its 10,021 compressed contents never enter plaintext candidate pages.

| Stage evidence | Compressible | Conformant |
| --- | ---: | ---: |
| Startup recovery SQL; completion time | 214; 56.195 ms | 218; 48.745 ms |
| Root restoration SQL; stage transition time | 10; 10.140 ms | 10; 7.821 ms |
| Plaintext compression SQL | 391 | 6 |
| Periodic operation completion time | 696.125 ms | 12.480 ms |
| Foreground root-stage p95 | 3.031 ms | 2.708 ms |
| Foreground plaintext-stage p95 | 7.643 ms | 3.169 ms |

Plaintext-stage durations derive from completion minus transition: approximately 685.985 ms and 4.659 ms.
SQL stage totals include concurrent foreground transactions.
The result JSON retains cumulative operation events, cache statistics, writer histograms, and foreground stage samples.

Do not sum cumulative progress snapshots or overlapping foreground observations.

The transient target succeeds on attempt two and restores its actual hot references.
A fresh unrelated cold content compresses while the permanently failing target remains pending.
The permanent target records 1,409 attempts under the accelerated benchmark clock.

This fault trial proves continuation and eventual transient restoration, not the production retry rate.
Its restoration operation holds the writer for 509.160 ms cumulatively; the maximum hold is 1.013 ms.
The successful transient restoration changes one content and holds the writer for 14.915 ms cumulatively.

Its maximum hold is 4.485 ms.
These restoration totals remain separate from the periodic compression totals above.

| Raw size | Insert without/with index | Restore without/with index | Compress without/with index |
| --- | ---: | ---: | ---: |
| 128 B | 5.803 / 5.387 ms | 1.248 / 1.225 ms | 1.297 / 1.360 ms |
| 64 KiB | 5.123 / 5.210 ms | 1.218 / 1.265 ms | 1.301 / 1.337 ms |
| 1 MiB | 5.370 / 5.627 ms | 2.411 / 2.443 ms | 1.597 / 1.612 ms |
| 16 MiB | 6.857 / 6.867 ms | 100.235 / 93.880 ms | 9.823 / 11.811 ms |

Each write value is the median of three isolated transaction/commit samples.
Ingestion preparation medians without/with index are 0.925/0.862, 1.363/1.013, 3.904/3.937, and 49.239/49.244 ms.
The JSON retains separate preparation timings for each restoration and compression sample.

The largest insert median increases by 0.010 ms with the index.
Other small differences change direction across operations and remain subject to cache and ordering noise.
No universal index-write penalty follows from three repetitions.

The benchmark exits successfully before cleanup.
Only its created 24,625,868,800-byte clone and 113,696,768-byte synthetic database are removed.
Its known SQLite sidecars and now-empty directories are removed after all processes close.

The retained JSON contains counters and plans, without source identities or content values.
The source comparison process starts before page-count heartbeat logging is added.
Its selection queries, projections, and populations remain unchanged.

The synthetic subprocess loads the final expanded fixture.

## Final synthetic correction provenance

The source-selection, root-selection, index-build, fixture, and complete-pass result groups remain byte-for-byte unchanged.
Only `result.index_write_overhead` receives the corrected synthetic measurements above.
The original dispatcher measurements above remain from the original disk-backed run.

The replacement command omits `--source-db` and creates only disposable synthetic databases:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression && uv run python scripts/benchmark_content_maintenance.py --scenario selection --work-dir /tmp/twicc-maint-selection-final-fix-20261017-a --output /tmp/twicc-maint-selection-final-fix-20261017-a.json
```

The CLI runs its small synthetic selection fixture and synthetic dispatcher before the write trials.
Those extra result groups are discarded; no source backup or whole-source selection runs.
The focused benchmark tests overlap this synthetic run; ambient host activity remains uncontrolled.

The correction passes `uv run pytest tests/test_content_maintenance_benchmark.py -q`: **7 passed in 19.38 seconds**.
The RED run fails on differing paired input hashes before the input correction.
The expanded assertions require both variants, four sizes repeated three times, and all 48 successful transitions.

Only this correction's created 36,487,168-byte and 113,696,768-byte databases and known sidecars are removed after process exit.
The final focused checks include explicit Ruff and `git diff --check`; no full repository test run occurs.

## Acceptance mapping

| Acceptance scenario | Evidence |
| --- | --- |
| Hidden/archived states, both thresholds, null/mixed/future dates, exact equality, active cold-dated roots | `test_sql_hot_matches_scalar_policy` in `tests/test_content_maintenance_selection.py` |
| Filter before pagination; require extracted identity | `test_periodic_page_filters_before_limit`, `test_periodic_page_requires_extracted_identity` |
| Exclude hot subagents from periodic discovery; retain startup all-depth discovery | `test_startup_discovery_keeps_hot_nested_subagents`; root population benchmark |
| Restore selected root without restoring descendants; preserve shared contents | `test_periodic_restores_hot_root_without_descendants` |
| Activate root after cursor passes | `test_root_activated_below_cursor_uses_targeted_restoration` |
| Transient subagent retry without another lifecycle event | `test_subagent_retry_succeeds_without_new_event` in `tests/test_content_maintenance_retry.py` |
| Failed targets permit unrelated restoration and compression | `test_failed_periodic_target_does_not_block_compression`; synthetic `restoration_failures` |
| Repeated publication failures retain growing backoff | `test_publication_failures_keep_increasing_backoff` |
| Retry retains independent cache after parent pass closure | `test_retry_survives_periodic_cache_close` |
| Repeated pages cache metadata; invalidate only committed affected entries | `tests/test_content_maintenance_cache.py`; `test_committed_invalidations_reload_only_changed_owner` |
| Actual parameterized query uses partial index; returns ordered plaintext rows | `test_production_metadata_query_uses_plain_index`; indexed metadata benchmark query plan |
| Matching no-gain policy skips payload; old/null policy reevaluates; separate provider dictionaries | `test_plain_no_gain_policy_controls_payload_selection` in `tests/test_content_maintenance_reads.py` |
| Rejected page advances; empty page finishes | `test_all_rejected_plain_page_advances_cursor`, `test_all_compressed_table_finishes_in_one_empty_page` |
| Compressed rows do not load or rewrite | `test_compressed_rows_never_enter_metadata_page`; `test_older_dictionary_compressed_rows_are_excluded_without_payload_reads` |
| Three candidates load only candidate payloads | `test_only_candidates_load_payloads_and_publish_metadata` |
| Oversized candidate preserves bounded progress | `test_stored_byte_budget_allows_one_oversized_row`; synthetic 16-MiB samples |
| Shared owners, mutations, activation and concurrent representation changes fence publication | `test_publication_rechecks_authoritative_observations`, `test_new_cold_owner_is_preserved_during_publication`, `test_payload_selection_uses_coherent_representation` |
| Invalidations during cache fill remain safe | `test_invalidated_inflight_fill_is_reloaded`, `test_controlled_concurrent_fill_retries_after_invalidation` |
| Index membership changes preserve traversal | `test_index_membership_changes_preserve_cursor`, `test_remaining_plain_rows_compressed_concurrently_finish_scan`, `test_trailing_compressed_rows_finish_plain_scan` |
| Foreground precedence and compute gates remain active | `tests/test_content_maintenance_compute_gate.py`; `test_foreground_arrival_during_ingestion_preparation_commits_first` |
| Progress separates root and plaintext stages; retries retain parent correlation | `test_periodic_stages_have_distinct_populations`, `test_retry_diagnostics_survive_pass_completion` |
| Safe destination/output aliases and exclusive output | `tests/test_content_maintenance_benchmark.py`; existing benchmark output tests in `tests/test_content_maintenance_end_to_end.py` |
| Source schema and file contents remain unchanged | `test_selection_source_schema_and_bytes_unchanged` |
| Empty-table index creation | `tests/test_content_storage_schema.py`; synthetic model-schema creation. Release squash check remains pending. |

## Release gates

- Confirm the eventual squashed migration creates `idx_content_plain_id` on the empty content table before compute.
- This release-squash check is **pending**. Model-schema creation does not validate the future squash.
- Obtain independent whole-change review and resolve important findings before declaring the implementation complete.
- No changelog change belongs to this task.
