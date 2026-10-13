# Content extraction performance analysis

Date: 2026-10-13. Branch: `feature/content-storage-compression`. Product baseline: `1384b082`.

## Scope and operational state

This document records analysis. It does not authorize implementation.

DevCTL stops gracefully after the final snapshot, around 21:37 local time.
Both frontend and backend remain stopped. The worktree database and search index remain intact.
The main checkout, main data directory, and provider files remain unchanged.

Worktree: `/home/twidi/dev/twicc-poc/.worktrees/feature-content-storage-compression`.

## Final progress

The run starts at 18:14:17.766. The final database snapshot reports:

| Provider | Ready root sessions | Remaining root sessions | Ready subagents | Remaining subagents |
| --- | ---: | ---: | ---: | ---: |
| Claude Code | 394 | 2,893 | 1,687 | 6,734 |
| Codex | 342 | 909 | 1,587 | 99 |
| Total | 736 | 3,802 | 3,274 | 6,833 |

Between the 21:07 snapshot and shutdown, 249 more roots and 559 more subagents become ready.
Large sessions cause long pauses in root progress. Progress accelerates after their publication completes.

## Production observations

The 19:13–21:07 interval contains 5,490 extraction publications and 1,878 final publications.

| Writer phase | P50 | P95 | P99 | Maximum | Cumulative duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Extraction | 363 ms | 991 ms | 1,124 ms | 1,615 ms | 2,596.6 s |
| Final publication | 315.5 ms | 467.5 ms | 665.8 ms | 7,231 ms | — |

These holds exclude other application writers. Foreground priority applies between holds, not during a hold.
Read requests can still proceed in WAL mode. Foreground writes must wait for the current publication.

One Codex session has 186,496 items, about 537 MB of legacy content, and 72,394 history facts.
Its application takes 1,121.476 s. Worker compute takes about 136.8 s.
Extraction publication takes 614.6 s across 731 slices. Reconciliation publication takes 150.2 s across 732 slices.
Preparation takes about 258 s. Final publication takes 7.23 s.
These phase totals describe this session. They are not a universal cost ratio.

Other large applications take 591.7 s for 84,909 items and 425.1 s for 65,320 items.
Completed worker results can wait behind these applications for many minutes.
Bounded worker submission prevents unlimited accumulation. It does not interleave application across sessions.

## Confirmed causes

### Repeated work inside extraction publication

Sources:

- `src/twicc/core/content_storage/extraction.py`: `_publish_extraction`.
- `src/twicc/core/content_storage/references.py`: `intern_contents`, `bind_persisted_items`.
- `src/twicc/core/content_storage/codec.py`: `_load_dictionary`, `decode_content`.

For each text, `intern_contents` encodes and hashes plaintext again.
It decodes the prepared candidate to check exact text equality.
It checks cold eligibility for each new compressed content.
It reloads stored rows and decodes them again to check stored text equality.

A 256-content cold publication therefore performs 512 dictionary loads and 256 repeated cold eligibility checks.
The profile records 530 Session SELECTs, including the remaining publication checks.
These repeated checks consume Python and ORM time while the writer lease remains held.

Candidate validation and stored-row validation protect different boundaries.
They cannot both disappear without replacement checks.
However, validated dictionary data and batch eligibility need not be reconstructed for every content.

### Django update construction

Item assignment and owner updates use `bulk_update`.
Django constructs and resolves CASE expressions for these updates.
The profile attributes substantial time to this Python work before SQLite executes the statements.

SQL statement count alone is misleading.
A single expensive ORM-generated UPDATE can cost more than many simple parameterized statements.
The TEMP reference-count statements are numerous, but they are not the main measured cost.

### Reconciliation processes unchanged representations

Source: `extraction.py`, `_prepare_reconciliation`, `_publish_reconciliation`.

Initial extraction already writes compressed content for eligible cold sessions.
It does not retain the complete compression attempt outcome on the inserted row.
Reconciliation then revisits the newly compressed content.

For a row compressed with the current dictionary, preparation still calls `transcode_content`.
That path decompresses, checks the digest, and validates UTF-8 before returning the same representation.
Publication reloads the full row and writes the full payload again.

The first pass may need only compression policy metadata.
A repeated pass can have identical payload, codec, dictionary, policy, and no-gain state.
The probe confirms that this repeated pass still selects and updates all 256 rows individually.

### Batch duration and session fairness

Extraction uses 256 items and an 8 MiB byte budget.
The row budget limits memory and work count. It does not impose a short writer deadline.
A single oversized content can exceed the byte budget to guarantee progress.

One session retains application ownership across its slices.
Foreground writers can run between slices. Other computed sessions cannot yet publish between those slices.
This explains long compute queues even when worker submission remains bounded.

## Isolated measurements

### Method and limitations

Temporary pytest probes run against Django's isolated test database, using an in-memory SQLite database.
They read 256 legacy texts from the stopped worktree database through a read-only SQLite connection.
The sample contains 1,114,385 UTF-8 bytes. Its largest content contains 44,702 bytes.
No source text appears in the output.

The probes execute real extraction and reconciliation functions.
Experimental monkeypatches exist only in the probe process. They do not modify product code.
Publication caches are scoped to one publication. Preparation and reconciliation retain their original behavior.

This sample does not represent the largest individual contents or the full production database.
It excludes production disk latency, WAL growth, foreground load, and the full background process lifecycle.
The results identify avoidable work. They do not predict a whole-run speedup.

### Publication caches: five repetitions per variant

| Variant | Median publication | Range |
| --- | ---: | ---: |
| Existing behavior | 464.14 ms | 460.27–509.51 ms |
| Dictionary cache | 324.02 ms | 319.05–380.90 ms |
| Eligibility cache | 280.56 ms | 273.64–292.44 ms |
| Both caches | 149.30 ms | 134.06–160.05 ms |

Both caches reduce measured publication time by about 68% in this fixture.
Session SELECTs fall from 530 to 18. Dictionary loads fall from 512 to one.
Cold extraction preparation still takes a median 101.29 ms.
Reconciliation preparation still takes 110.48 ms. Its first publication takes 124.11 ms.
Repeated reconciliation still takes roughly 119–129 ms despite identical state.

### Update primitives: five repetitions per size

The following probe compares update primitives only. It does not replace a guarded publisher.
Both paths use the same field values and transaction boundaries. Assertions verify persisted values.
JSON values use Django's field preparation before parameterized SQL updates.

| Rows | Item `bulk_update` | Item `executemany` | Owner `bulk_update` | Owner `executemany` |
| --- | ---: | ---: | ---: | ---: |
| 32 | 7.333 ms | 0.151 ms | 3.929 ms | 0.264 ms |
| 64 | 13.719 ms | 0.230 ms | 7.236 ms | 0.440 ms |
| 256 | 52.630 ms | 0.681 ms | 27.771 ms | 1.498 ms |

This result supports a narrow private update helper for these fixed columns.
It does not justify replacing Django throughout the application.

### Smaller publications: three repetitions per combination

| Row budget | Existing behavior median | Both publication caches median |
| --- | ---: | ---: |
| 32 | 79.57 ms | 35.62 ms |
| 64 | 155.89 ms | 46.15 ms |
| 128 | 270.15 ms | 90.96 ms |
| 256 | 540.80 ms | 144.37 ms |

These runs use progressively larger prefixes of the sample. Content sizes differ between prefixes.
They support testing a 64-row publication budget after removing repeated work.
More slices add scheduling, guard, and commit overhead. Whole-session throughput must also be measured.
No fixed row budget guarantees a latency limit for one very large content.

The isolated probes complete successfully: 2 profiling cases, 4 cache cases, 20 repeated cache cases,
3 update primitive cases, and 24 row-budget cases.
No product test suite is rerun because this investigation changes no product code.

## Proposed improvement order

1. Cache the validated dictionary within each publication. Evaluate insertion eligibility once for the guarded batch.
   Keep the cache lifetime inside the actual transaction and writer lease.
   Include provider, dictionary identity, and root decision where needed.
   Do not reuse eligibility across a yield, transaction, or owner mutation.
2. Carry validated preparation results and compression attempt metadata into initial publication.
   Avoid recomputing the same hashes and decoding the same prepared bytes under the writer.
   Retain exact-text checks for existing shared rows and conflicts unless an equivalent check replaces them.
3. Add reconciliation fast paths for unchanged representations and unchanged policy state.
   Update policy columns alone when the payload does not change.
   Deduplicate repeated content references where safe, including current and original references.
   Keep hot-owner promotion, dictionary upgrades, no-gain policy changes, and interrupted-run recovery.
4. Replace the two fixed-column CASE update paths with bounded parameterized updates.
   Keep assignment validation, ownership maintenance, reference counting, and root invalidation in the same transaction.
   Use Django field adaptation. Preserve duplicate-row and missing-row behavior explicitly.
5. Benchmark smaller publication batches on a production database copy.
   Start with 64 rows and the existing byte budget after the previous changes.
   Measure foreground write latency and asyncio availability before optimizing whole-run throughput.
6. Investigate remaining final aggregate holds separately.
   Earlier final holds reach 67 s on aggregate fallback. Later holds still reach 7.23 s.
   Global `data_version` invalidation can trigger fallback after unrelated external commits.
   Narrower invalidation requires independent correctness analysis. Do not simply ignore external commits.

Skip unnecessary reference-count initialization for entirely legacy sessions only after higher-priority work.
The current full-session scan costs preparation time, but the confirmed writer hot paths dominate this investigation.

Do not begin with session application interleaving.
Pinned connection state, TEMP reference counts, history staging, cursors, and ownership make that change broader.
First shorten each publication and remove redundant reconciliation.
Reconsider interleaving only if session readiness remains unacceptable after those changes.

## Required correctness boundaries

Preserve storage incarnation, history epoch, source position, ancestry, and reference guards.
Check actual owner eligibility before storing compressed shared content.
Preserve exact logical content, current/original references, and collision handling.
Advance cursors only after successful publication. Interrupted work must remain recoverable.
Foreground work retains priority between bounded publications.
Do not remove cooperative yielding merely to improve throughput.

Each product change needs independent review before application, as requested by the user.
Validation must include hot-owner reuse, owner changes between preparation and publication, dictionary changes,
no-gain state, superseded results, interruption, subagents, shared references, and actual foreground write contention.

## Attribution limit

The large 145,950-item session takes longer than its earlier baseline despite faster final history publication.
Its extraction and reconciliation costs also increase substantially.
However, `references.py` and `codec.py` are unchanged between `e72d53c9` and `1384b082`.
The measured redundant work therefore predates the latest finalization changes.
These probes do not establish the complete cause of the cross-run slowdown.
Cache state, database state, external writes, and system load require controlled full-session comparisons.

The confirmed conclusion is narrower: substantial avoidable ORM and validation work occurs inside writer holds.
Initial reconciliation also repeats work on content that extraction already compresses correctly.

## Follow-up: simplification and aggregate query measurements

### Dictionary lifetime

`ContentDictionary` is the persisted dictionary row. `ZstdCompressionDict` is its native library representation.
These are two representations of the same dictionary, not two dictionary collections.
The current cache retains the native representation but still reloads and validates the persisted row.
An immutable, validated dictionary can stay available for the process lifetime.
The cache must belong to the actual database identity. New dictionary versions can load once when introduced.
There is no need to reload, rehash, or reconstruct the dictionary for every content.

### Cold state lifetime

The root's cold state can remain cached while its lifecycle remains unchanged.
Subagents need a root mapping, not an independently calculated cold state.
Session resume, new source activity, active-agent transitions, and lifecycle changes can invalidate that state.
Hot roots can also cross the age threshold as time passes without any database write.
A process-lifetime boolean without invalidation would therefore be incorrect.
None of these transitions requires recalculating cold state per content.
Content-binding epoch updates must not automatically force a complete cold-state recalculation.

### Deduplication and owners

The digest identifies exact uncompressed UTF-8 bytes. It does not identify the compressed representation.
The current preparation compresses before publication checks whether the digest already exists.
The preferred order is hash, bounded digest lookup, then preparation only for missing content.
Publication must handle an existing row appearing after that lookup.
Existing content can be reused directly; owner policy determines whether its representation needs a later change.

Owner addition reads the existing JSON list and takes its union with the new session IDs.
It does not scan all sessions to rebuild the list.
However, `bind_persisted_items` writes the list even when this union leaves it unchanged.
New rows can carry their known initial owners when inserted, subject to transactional assignment validation.
Existing rows need an update only when the owner set changes.
Removal retains the separate current/original-reference checks before discarding ownership.

The item reference assignment and legacy text clearing already happen in the same update.
There is no separate second update just to clear `content`.

### Individual updates versus CASE updates

A second isolated probe compares five repetitions of each method in the same transaction.
Individual SQL updates use one cursor. They do not commit after every row.
`executemany` executes those same parameterized updates through one Python call.

| 256 rows | Django `bulk_update` | Individual cursor updates | `executemany` |
| --- | ---: | ---: | ---: |
| Item references and legacy clearing | 46.798 ms | 1.780 ms | 0.581 ms |
| Owner JSON lists | 25.618 ms | 2.386 ms | 1.316 ms |

All three isolated test cases pass. Persisted-value assertions cover both item assignments and owner JSON.
This is an in-memory write-primitive benchmark. It does not measure full production publication or disk commits.
It supports parameterized updates for these paths, rather than merely reducing the CASE batch size.

### Actual database aggregate queries

The worktree database remains stopped and read-only throughout these measurements.
The probe executes equivalent SQL for the existing cost SUM and user-message COUNT queries.
It samples two dates and their containing weeks, for project and global scopes.
It uses three executions per query. It does not reset the operating-system cache.

An initial SUM-plus-count probe records first global cost executions of 693.60 ms and 245.80 ms for days,
and 2,145.97 ms and 1,017.28 ms for weeks. Later executions are much faster.
These are first executions on this probe connection, not certified cold-cache measurements.

A subsequent exact SUM-only probe reports these medians:

| Global provider cost range | Existing planner | Forced timestamp index |
| --- | ---: | ---: |
| Codex, 2026-09-09 | 14.64 ms | 14.01 ms |
| Codex, containing week | 48.05 ms | 75.31 ms |
| Claude Code, 2026-08-18 | 30.56 ms | 8.05 ms |
| Claude Code, containing week | 48.57 ms | 74.64 ms |

`EXPLAIN QUERY PLAN` shows that the existing planner starts from provider sessions,
then probes the partial `(session_id, timestamp)` cost index for each session.
The forced alternative starts from the partial timestamp index and joins each matching item to its session.
It helps one sampled day but hurts both sampled weeks. Forcing that index globally is not justified.
Neither path reads or decompresses content. Both read stored `cost` and `timestamp` columns.
The partial cost indexes do not contain the cost value, so SUM also accesses the item table.

Each activity bucket calculates cost, user-message count, and session count, then persists the result.
For D distinct days and W distinct weeks, project/global scopes produce up to `2D + 2W` bucket keys.
The current implementation deduplicates these keys. It does not calculate a week's key once per day.
Nevertheless, multiple completing sessions can recalculate the same project/global ranges.
The cost depends on all matching database history, not only the newly computed session.

This follow-up does not establish a regression relative to main or explain every long aggregate hold.
It does confirm that a simple indexed SUM can have materially different latency between executions.
The first-execution behavior and repeated overlapping repairs matter when the writer remains held.
No index, model, migration, server, or product code changes accompany these probes.

## User decisions after follow-up

- Keep the existing 2,000-item metadata batches unchanged. Main already uses `executemany` for these updates.
- Replace the costly CASE update paths introduced by content storage with bounded parameterized updates.
- Keep per-root activity publication. Do not introduce a ten-root aggregation interval.
- Publish activity after the root and all its native descendants finish compute/application, at every depth.
  Individual subagent completion must not independently trigger the full activity recalculation.
  Retain the union of affected dates from the complete tree, including previous or removed contributions.
- Daily buckets continue to reflect currently persisted item costs and timestamps during progressive compute.
  They do not wait for all other sessions contributing to the same date to finish compute.
- After recalculating affected days, calculate weekly values from the existing daily buckets for the same scope/provider.
  Later completed trees update the days and weeks again. No new global readiness condition is required.

These decisions define intended behavior. Tree completion, retained repair obligations, live writes,
and recovery still require a reviewed design before product changes.
