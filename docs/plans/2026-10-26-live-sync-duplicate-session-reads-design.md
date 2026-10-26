# Live sync: remove duplicate session reads in content storage

Date: 2026-10-26. Status: design.

## Problem

The live sync (`BaseSessionCompute.sync_session_slice`, `src/twicc/providers/compute_base.py:4282`) runs one
writer transaction per slice. A slice that syncs one JSONL line runs **62.6 queries** on average (measured on
`tests/test_codex_agent_runs_live.py::test_batch_live_parity[stale_history-t20_sequence-per_line]`: 22 slices,
one root session and one subagent).

**24** of them read `core_session`. Most come from content storage (`src/twicc/core/content_storage/`), where
each helper loads the session metadata it needs on its own, even when a previous helper of the same transaction
has just loaded the same rows:

| Reader (per slice) | Queries |
|---|---|
| `_resolve_root_decisions` (`state.py:40`), reached 6 times per slice for the same session (see below) | 7.5 |
| `lifecycle_change` (`lifecycle.py:58`): `_metadata` before and after, `resolve_content_decisions` before and after, the certificate fields of `capture_plaintext_certificate`, `session_plaintext_metadata`, and `resolve_content_decisions` inside `invalidate_roots` | 8.0 |
| `ensure_storage_incarnation` (`state.py:93`), called twice by `initialize_ingestion_session` (`ingestion.py:103` and `:106`) | 2 SELECT + 2 UPDATE |
| Certificate fields read by `capture_plaintext_certificate` from `ingestion_plaintext_change` (`ingestion.py:48`) | 0.9 |
| Policy read of `insert_ingested_items` (`ingestion.py:158`), once per batch | 1 |
| Other readers (slice start `compute_base.py:4288`, `refresh_from_db` at `compute_base.py:4921`, `promote_ingested_items` policy read, agent runs, history facts, async questions) | ~5 |

In the measured scenario, a slice resolves the same ancestry 6 times: `ingestion_plaintext_change` → `capture_plaintext_certificate`;
`initialize_ingestion_session`; `bind_persisted_items` (`references.py:195`); `lifecycle_change` → `_root_map`
before the write, `capture_plaintext_certificate`, `_root_map` after the write. A subagent slice adds one:
`bind_persisted_items` captures the root's certificate (`references.py:201`). A slice whose stop step closes a run
adds two or three per stopped agent: the stop step's `update_lifecycle` (`agent_runs.py:561`) runs one more
`lifecycle_change` for each agent it stops. A slice that inserts no new line skips the `bind_persisted_items` resolution, and a
`lifecycle_change` that changes no lifecycle column skips its after-write resolution. A resolution costs one query per session of the chain it walks: one for a root, two for a
subagent, one when it starts from the subagent's root. `carry_plaintext_certificate` (`state.py:150`) resolves the ancestry once more
each time a certificate is carried (`lifecycle.py:95`, `references.py:204`, `ingestion.py:64`). The measured
replay carries none.

The production watcher pays this cost on every synced line of every session. So does every test that replays a
transcript through the live sync.

## Why these reads are duplicates

Every reader listed above runs inside the slice's writer transaction: `run_under_db_write_lock`
(`sessions_watcher.py:1255`) around `transaction.atomic(durable=True)` (`compute_base.py:993`). In WAL mode, the
transaction's read snapshot starts at its first read (`compute_base.py:4288`). A commit by another connection
after that point cannot change what this transaction reads: it makes this transaction's first write fail instead.
So a second read of the same columns of the same row returns the first value, **unless this transaction wrote
those columns in between**.

The slice also relies on an early write: today its first write, the conditional `UPDATE` of
`ensure_storage_incarnation`, runs right after the slice-start reads. In SQLite it takes the write lock even when
it matches no row. That shortens the window in which a writer outside the coordinator (the DB-backed Django
session, `settings.py:213-227`) can commit and fail the slice. The codebase uses the same reservation on purpose
(`extraction.py:465`). This design keeps it.

Two classes of `core_session` columns matter here, and they follow different rules:

- **Ancestry and cold-policy columns** (`type`, `parent_session_id`, and `COLD_FIELDS`: `archived`, `hidden` and
  the activity dates). In a live slice, no code writes them before the lifecycle write (`save_lifecycle` at
  `compute_base.py:4905`). The only later writer is the stop step's `update_lifecycle` (`agent_runs.py:561`), which
  runs after it. The slice writes other `core_session` columns before that point (titles, storage state), never
  these. These columns are reused from the slice start up to the lifecycle write. After it, they come from a fresh
  read (C3), which is reused until `lifecycle_change` exits. A later `lifecycle_change` (the stop step's) reads
  them again.
- **Storage-state columns** (`storage_incarnation`, `content_storage_version`, `content_state_epoch`,
  `plaintext_verified_epoch`). Content storage writes them itself, at many points of a slice: incarnation
  initialization, every `invalidate_roots`, every certificate carry, once per batch of bindings. A value of these
  columns is reused only while no write has touched the same row since it was read. A write to another row does
  not end the reuse. When this transaction writes such a value itself (the incarnation of C1), the shared row takes
  the written value: that is not a reuse of an older read.

This design removes only the reads that meet those rules. Each reader still computes its decision itself. It
reuses the rows it reads, not a decision computed by someone else. Time-based policy (`cold_from_fields`) is
still evaluated with the current time at each use.

## Goal

- Remove the certain duplicate `core_session` reads and writes listed below, with no change to any decision,
  certificate, epoch, or promotion that content storage makes.
- Keep the existing helpers usable as they are today: a caller that passes no preloaded rows gets the current
  behaviour.
- Lock the result with query-count tests on the live sync.

## Non-goals

- The probable duplicates found by the same measurement: the agent-run evidence loaded twice by `run_stop_step`
  (`agent_runs.py:533` and `:534`) and once more by the Codex compute; the two epoch bumps of one slice
  (`bind_persisted_items` → `invalidate_roots` inside `insert_ingested_items`, then `lifecycle_change` →
  `invalidate_roots`); the reads that only
  recover ids after an insert. Each needs its own analysis.
- Reads after the lifecycle write: the policy read of `promote_ingested_items` and the carry at the end of
  `ingestion_plaintext_change` (`ingestion.py:64`). The C3 after-write read holds the same columns, but C3 keeps
  its rows inside `lifecycle_change`, and the stop step's `update_lifecycle` (`agent_runs.py:561`) can write the
  synced session's cold-policy columns between that read and these ones. A slice whose records are all blank skips the lifecycle write
  (`compute_base.py:4321`). Its final carry could reuse the slice-start rows, but that slice syncs no line, so it is
  left as is.
- The slice-start `Session.objects.get` and the `refresh_from_db` after the cost aggregates: both read state that
  no earlier read of the slice holds.
- The before-write read of `lifecycle_change` in the live slice. Its ancestry and cold-policy columns are still
  those of the slice-start rows, so it is a duplicate too. C3 keeps `lifecycle_change` self-contained instead: it
  serves many callers, and passing slice rows into it would tie its correctness to each caller's reuse window.
- Any change to the content storage model, its invariants, or its public behaviour.

## Changes

### C1. Storage incarnation

`ensure_storage_incarnation` sets an incarnation once in the life of a session, then never changes it (the only
other write is a no-op `F()` update, `extraction.py:465`). It runs a conditional `UPDATE` and a `SELECT` on each
call.

- `initialize_ingestion_session` takes the session's incarnation from the session row the slice has just read.
  When that row has none (a legacy row, or one created by a path that skips initialization: the watcher
  initializes the sessions it creates, `sessions_watcher.py:679-691` and `:727-728`), it takes the value the
  conditional `UPDATE` has just written. In both cases the in-memory session and the shared row hold the current
  value, and nothing reads it back.
- The slice keeps the conditional `UPDATE` of the session as its first write, so the write-lock reservation
  described above is unchanged. It is a no-op once the incarnation exists.
- The native root is not ensured a second time when it is the session itself. This skip applies to every caller
  of `initialize_ingestion_session`: the second call is a no-op `UPDATE` and `SELECT` issued after the first `UPDATE`
  already took SQLite's database-wide write lock, so it changes no value and no concurrency behaviour. When the caller passes the
  ancestry rows (the live sync, C2), a distinct root is ensured only when its row has no incarnation. Other
  callers ensure a distinct root as today. When it is ensured, the shared root row takes
  the ensured value.

### C2. One ancestry read at slice start

`ingestion_plaintext_change` (via `capture_plaintext_certificate`) and `initialize_ingestion_session` run back to
back, right after the slice reads its session.

- The session row of the slice seeds the ancestry resolution. A root session needs no further read. A subagent
  needs its ancestors only.
- The certificate capture of `ingestion_plaintext_change` runs before any write of the slice. It takes the
  storage-state columns of the root from the same ancestry rows, so it costs no extra read.
- `initialize_ingestion_session` reuses the same rows for the ancestry. The incarnation write of C1 is the only
  write between the two, and C1 keeps the shared row up to date.

### C3. `lifecycle_change` reads each state once

Before the write, `lifecycle_change` reads the rows of `ids`, resolves their native roots, evaluates their
content decisions and captures the root certificates. After the write, it reads the rows again, resolves the
roots of the changed rows, evaluates their decisions, checks plaintext metadata, invalidates the roots and carries
the certificates.

- **Before the write:** one read of the `ids` rows and their ancestors, including the storage-state columns.
  No write happens between this read and the yield. The old decisions, the old roots and the certificates all
  come from those rows.
- **After the write:** one read of the `ids` rows, including the columns `session_plaintext_metadata` needs.
  When at least one row changed (otherwise `lifecycle_change` stops there, as today), the ancestors of those rows
  are read again too: the new state is never taken from a read made before the write, so the design needs no assumption about what the body of `lifecycle_change` writes. (The old roots,
  decisions and certificates still describe the state before the write, as today.) The new roots, the new
  decisions, the plaintext metadata check and the promotion targets of `invalidate_roots` all come from that read. `session_plaintext_metadata` keeps its scoped SQL probe on the
  items. Only the session row read is shared.
- The carry that ends `lifecycle_change` reuses the ancestry and cold-policy columns of that after-write read:
  the epoch `UPDATE` of `invalidate_roots` writes neither, and earlier carries write only
  `plaintext_verified_epoch`. It still reads the epochs it compares. A carried root
  that the after-write read does not hold (an old root that is no longer an ancestor of a changed row) is loaded,
  as today.

### C4. Bindings reuse the slice ancestry

`bind_persisted_items` resolves the native root of each binding's session. It then captures a certificate for
each root that is not a binding owner, invalidates the roots and carries the certificates. It runs once per batch
of 50 lines (`INSERT_BATCH_SIZE`), up to 10 times in a slice of 500 lines (`LiveSyncLimits.max_lines`).

- During a live slice, the bindings run before the lifecycle write. So the ancestry and cold-policy columns of the
  session and its ancestors are still those read at slice start (C2). The slice passes them, for the root
  resolution, the capture and the carry of every batch.
- The storage-state columns are not passed: each batch's `invalidate_roots` and carry change them. Every capture
  and every carry reads them fresh.
- Other callers keep today's behaviour.

### C5. Per-batch policy of `insert_ingested_items`

`insert_ingested_items` evaluates the session's content decision once per batch (`ingestion.py:158`), from the
cold-policy columns. Before the lifecycle write, those columns are still those of the slice-start row. The slice
passes them as read at slice start, not from the in-memory session object, which the slice modifies as it goes.
The decision itself is still evaluated at each batch, with the current time and the active set.

### Interface principle

Each changed helper accepts already-loaded rows as an optional input. When the input is absent, the helper
loads what it needs, as today. Only the live sync path and `lifecycle_change` pass rows. Each passing site
documents why the rows are still current under the rules above.

## Expected result

Measured baseline: 62.6 queries per slice on the test above. Estimated removal per slice:

| Change | Queries removed |
|---|---|
| C1 | ~3 |
| C2 | ~3 |
| C3 | ~9.5 |
| C4 | ~1.1 |
| C5 | ~1 |
| **Total** | **~16–18** (root slice ~16, subagent slice ~18) |

The per-change figures are rough and add up to the upper bound (a subagent slice).

Target: about **62 → 45–47 queries per one-line slice**, a quarter of the live sync's database work. The exact
figure is measured after the change and becomes the budget of the tests below.

## Verification

- **Query budget tests:** one-line slices for a root session and for a subagent, Claude and Codex, with a fixed
  number of queries. A later change that adds a read fails them. The budgets are the measured counts after the
  change.
- **Behaviour:** the existing content storage, live sync and parity suites pass unchanged. In particular:
  `test_codex_agent_runs_live.py`, `test_claude_agent_runs.py`, the `test_content_*` files,
  `test_compute_lifecycle_publication.py`, `test_inline_artifact_compute.py`.
- **Equivalence check:** before and after the change, run the same live replays and compare the persisted
  content-storage state (`storage_incarnation`, `content_state_epoch`, `plaintext_verified_epoch`,
  `content_storage_version`, item and content rows), plus the effects that are not persisted: the targets of the
  promotion wakeups queued after commit and the cold-cache invalidations. Ids and generated incarnations
  excepted, they are identical. The replays cover:
  - one-line slices of a root session and of a subagent;
  - a session row without an incarnation (C1 writes it, nothing reads it back), and a subagent whose root has no
    incarnation yet. The watcher initializes the sessions it creates, so the replay builds these rows on purpose;
  - a root session whose root holds a certificate (capture from the seeded row, C2);
  - a multi-batch slice (more than 50 lines) of a subagent whose root holds a certificate: per-batch epoch
    changes (C4), and the certificate carry that ends `lifecycle_change` (C3);
  - a root slice that replays lines already stored (non-blank, so the slice reaches the lifecycle write) and changes
    a lifecycle column (for example a newer activity date), while its root holds a certificate: the other path to
    that carry (C3);
  - a replay with a non-empty active session set, since C2 and C3 stop loading the active ids' ancestry;
  - a slice whose stop step closes several runs, including the synced subagent itself, so the stop step's
    `lifecycle_change` calls (C3) are compared too;
  - a cold session that becomes hot, so the lifecycle write produces a promotion target (C3).
- **Timing:** full backend suite before and after, to report the gain.

## Risks

- **A reused value is stale.** That can happen only if this transaction writes a reused column between the read
  and the use. Mitigation: the two column classes above, each with its own reuse rule. A storage-state value is
  never reused across a write to its own row. The equivalence check, with its multi-batch slice, and the
  existing suites cover it.
- **A caller outside the live sync changes behaviour.** C1, C2, C4 and C5: preloaded rows are optional, and
  without them every helper behaves as today, except the root-is-session skip of C1, which saves a no-op for
  every caller. C3 is different: it changes the reads inside `lifecycle_change` for
  every caller (`session_update.py:186`, `compute_lifecycle.py:60`, `agent_runs.py:561`, `save_lifecycle`,
  `update_lifecycle`). Its argument does not depend on the caller: no write happens between the before-write read
  and the body, and the after-write read is fresh. The existing suites cover the non-live callers, in particular
  `test_content_lifecycle.py` (reparenting, root to subagent), `test_timeline_bulk_archive.py` and
  `test_compute_lifecycle_publication.py`.
- **Active sessions.** The native root and the `resolved` flag of a session depend only on its own ancestry chain.
  The active set adds `is_active` and changes `is_cold`. The consumers that share rows fall in three groups. The
  active-set root resolutions (`initialize_ingestion_session`, `_root_map`) use only the root and the flag. The
  certificate captures and carries (C2, C3, C4) resolve with an empty active set (`state.py:129`, `:150`).
  The content decisions (`resolve_content_decisions`, including the promotion targets of `invalidate_roots`) use
  the active ids as a membership test, with no row read. So the shared rows never need the rows of the active
  ids. A consumer that needs `is_active` or `is_cold` from an active-set root resolution keeps loading the active
  ancestry, as today.
