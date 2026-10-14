# Timeline Incremental Backfill — Design

**Status:** draft v1, reviewed by independent adversarial subagents until PASS (3 rounds). Written with the user 2026-10-14.
**Date:** 2026-10-14
**Scope:** how the timeline backfill selects and processes sessions. It replaces the batch-parcours implementation of `2026-10-13-session-timeline-design.md` §8 with a candidate-driven, one-session-per-lock loop. It changes nothing else.
**Baseline:** the system as implemented on branch `feature/session-timeline` at commit `cfa958c0`. That design and its plan (`2026-10-13-session-timeline-design.md`, `2026-10-13-session-timeline-plan.md`) are not amended: they describe the system at that moment. This document describes the next step.

---

## 1. Why change

The implemented backfill (`core/services/timeline_backfill.py`) runs at every boot. It walks **all** normal sessions by id in locked batches of 200. Under the DB write lock, each batch:

1. loads the full `Session` rows (every column, including the large JSON ones);
2. reads **every** timeline row of those sessions (no `DISTINCT`, no kind filter);
3. loops in Python and, usually, writes nothing;
4. inserts the missing rows one by one (a duplicate-check `SELECT`, a savepoint and an `INSERT` per row).

Problems:

- **Lock hold time.** All of it runs under the write lock, which blocks every other writer (live sync, hooks, user actions) for the whole batch.
- **Cost grows with usage.** Step 2 reads one row per timeline event of every session, at every boot, while the answer is almost always "nothing to do". The timeline grows with every turn, so the cost has no ceiling.
- **Later boots do the work of the first one.** The parcours is the same whether or not anything is missing.

## 2. Goals and non-goals

**Goals.**

- Keep each lock acquisition tiny, so that other writers are never held back noticeably.
- Make the first boot and the following boots run the **same code**; the following boots must cost almost nothing when nothing is missing.
- Process the most recently active sessions first, because their data is the most useful.
- Cost independent of the number of timeline events.

**Non-goals.**

- What the backfill writes does not change (section 7).
- No history is invented; no process rows (unchanged rule of the earlier design).
- No catch-up of what happened between two shutdowns: the boot reconciliation and the live hooks own that.

## 3. Decisions

| # | Decision |
|---|---|
| B1 | A session is "processed" when it has a `session.created` timeline row. No marker column on `Session`. |
| B2 | A candidate is a normal session (`type=SESSION`) with `created_at` set and no `session.created` row. |
| B3 | An index makes the candidate test cheap: a composite index on `TimelineEvent (session_id, kind)`. The test "does this session have a `session.created` row" becomes a direct seek, independent of the number of timeline events of the session. |
| B4 | The ordered list of candidate ids is read **once** per run, outside the lock, ordered by `Session.mtime` descending (ties: id descending), reading its ids once like the background compute does (the compute has no id tie-break; this one adds it for determinism). |
| B5 | Sessions are then handled **one at a time**: one lock acquisition per session. No batches, no batch size. |
| B6 | Everything about a session (reload, re-check, reads, row building, insertion) happens **inside** its lock acquisition. Nothing is prepared outside the lock except the id list. |
| B7 | The first run and the following runs are the same code path. |
| B8 | A session that was written or skipped is never attempted again in the same run. A session whose write failed is retried only by the end-of-run retry (section 5). |

## 4. Candidate selection (outside the lock)

One query, once per run:

- normal sessions (`type=SESSION`) with `created_at` not null;
- with no `TimelineEvent` row `kind = 'session.created'` for the same `session_id` (an anti-join);
- ordered by `mtime` descending, then `id` descending;
- only the ids are returned.

The anti-join must be served by the index of B3 (a seek on `session_id` and `kind`). The index is a plain composite index, not a partial one, on purpose: a SQLite partial index is only used when the planner can match the index condition at planning time, and an ORM filter binds the kind as a parameter, which defeats that. The query plan must be verified (section 10).

`Session.mtime` (the JSONL file's modification time) is always set and follows any activity, including activity outside TwiCC. `last_updated_at` is nullable (it is empty for sessions not yet computed), so it is not used for ordering.

The snapshot is taken once. Sessions created after it are not in the list; they get their rows from the live hooks (the watcher's `create_session_sync` and the initial-sync create apply). A session whose `created_at` is still empty at snapshot time (not yet computed by the background compute) is picked up at the next boot.

## 5. Per-session procedure (inside one lock acquisition)

For each id of the list, in order, one `run_under_db_write_lock` acquisition:

1. **Reload** the session row (fresh data). Skip it if it no longer exists, is no longer a normal session, or has an empty `created_at`.
2. **Re-check** that it still has no `session.created` row. If one exists now (a live hook wrote it meanwhile), skip it.
3. **Read** the timeline rows that matter for the four flag subjects (`archived.*`, `hidden.*`, `muted.*`, `pinned.*`): only those kinds, one distinct row per kind is enough. The read is bounded by a handful of rows, whatever the session's activity.
4. **Build** the rows (section 7), skipping a flag subject that already has a row.
5. **Insert** them in the same transaction and release the lock.

Between two sessions the loop yields to the event loop, so other writers waiting on the lock can take it (the write lock is fair: waiters are served in arrival order). The session's rows are inserted all-or-nothing: if any row cannot be written, the whole session transaction is rolled back, the failure is logged, and the session is skipped. It then still has no `session.created` row. It is set aside and retried once the whole list has been processed (see the end-of-run retry below). A row skipped because the same `dedupe_key` already exists counts as written, not as a failure.

**End-of-run retry.** When the list is exhausted, the sessions set aside are attempted again, at most 3 more times each, with a short pause that grows between rounds, in the order of seconds and under about 10 seconds in total (the pause is not under the lock). Only a failure to write a session's rows sends it to the retry set; the refusal of the lock itself because the writer is stopping is not a session failure: it ends the run (the shutdown path cancels the task before that can happen). A session that succeeds leaves the set. A session still failing after the last round is logged at error level with its id and the cause, and is left for the next boot. This retry is meant for transient failures (a busy or locked database, a transient I/O error). It does not cure a permanent failure (a bug or an unexpected legacy value): that one is made visible by the error log and is fixed by a code change.

The time spent under the lock for one session is a few statements and a handful of rows. No hold depends on the number of sessions.

## 6. Why one session per lock, and not batches

- A batch makes other writers wait for the whole batch. The smaller the unit, the sooner they get the lock.
- The cost of one lock acquisition (a thread hop and a transaction commit) is small next to the benefit. The total first-run duration is longer with one session per lock, but it runs in the background and blocks nobody for long.
- With a unit this small, preparing data outside the lock and re-reading it inside would duplicate the work for no gain; everything is done inside (B6).

## 7. What is written (unchanged from the earlier design)

For a candidate session:

- `session.created`, dated `created_at`, `precision=derived`, `origin=sync`, `provider`, `spawned_by` from the column, `settings` null, `dedupe_key="backfill:session.created"`.
- For each of `archived`, `hidden`, `mute_on_user_turn` (true) and `pinned` (not null): the matching `<flag>.started`, dated `last_updated_at` (fallback `created_at`), `precision=estimated`, `dedupe_key="backfill:<kind>"`, with `scope` for `pinned`. Skipped when the session already has any row of that subject, or when both dates are empty.
- Never any process, turn or backend row.

The insertion method (one `INSERT` per row, a multi-row `bulk_create`, or `executemany`) is chosen by measurement (section 10). Whatever the method, the rows keep their payload validation and the `(session_id, dedupe_key)` uniqueness as a safety net, and a session's rows are inserted in one transaction.

The backfill uses its own non-swallowing insert path, not `record_event_sync`. That function catches every error and returns the same result for a duplicate and for a failure, and it isolates each row in its own savepoint, so reusing it would write partial sessions and could not tell "already present" from "failed" (section 5). The backfill's path shares the payload validation and the duplicate check with it, lets errors reach the per-session transaction, and distinguishes an already-present row from a failure. The "never raises" rule of the earlier design applies to the live hooks and to `record_event[_sync]`, not to this internal path; the backfill loop itself still never raises out of the run.

## 8. First run and following runs

| | First run after the rollout | Following runs |
|---|---|---|
| Candidates | Every normal session with `created_at` set | Almost none: sessions computed since the last run, and sessions whose live creation hook never wrote its row |
| Work per boot | One lock acquisition per historical session, newest first | One index seek per normal session to build the candidate list, which comes back (almost) empty; no lock is taken when it is empty |
| Cost vs timeline size | Independent | Independent |

The backfill does not detect what changed since the last shutdown, and it does not compare the current state of a session with its timeline. A session that has a `session.created` row is never looked at again by the backfill.

## 9. Relationship with the other mechanisms

- **Boot reconciliation** (crash detection, `process.ended(lost)`, `backend.*`) is untouched and still runs before the backfill is created.
- **Live hooks** are untouched. They remain the only source of events for sessions created while TwiCC runs.
- **Start time** is untouched: the backfill task is created in `cli/run.py` right after the automatic-title task, before the orchestrators start, and runs concurrently with the initial sync and the background compute. The per-session re-check (step 2) makes that concurrency safe.

## 10. Measurement

A volume test builds a synthetic database of several thousand sessions and reports, per variant:

- the duration of one lock acquisition (distribution and maximum);
- the total duration of a first run;
- the duration of a following run with no candidates.

The insertion variants to compare: per-row `record_event_sync`-style inserts, `bulk_create`, and `executemany`. SQLite's cost is dominated by the transaction commit, not by the number of statements inside it, and Django's `bulk_create` emits a plain multi-row `INSERT` (it has none of the `CASE WHEN` cost of `bulk_update`); the measurement decides.

Target to validate (not a contract): no single lock acquisition above about 50 ms on the development machine.

Acceptance check: `EXPLAIN QUERY PLAN` of the candidate query shows that the anti-join is served by the `(session_id, kind)` index and that the cost of the check does not depend on the number of timeline events of a session.

## 11. Decisions that were considered and dropped

| Idea | Why dropped |
|---|---|
| A boolean on `Session` meaning "processed by the timeline" | The `session.created` row is the marker; no schema change on `Session` needed |
| A "last end recorded" date on `Session`, compared with `last_updated_at` | Crash detection already comes from the boot reconciliation of `ProcessRun` rows; `last_updated_at` moves on any activity, so the comparison would flag almost every session and would invent ends for sessions TwiCC does not run |
| Batches of several sessions per lock | See section 6 |
| An index on `Session.mtime` and re-sorting at every step | One ordered id list per run is enough, like the background compute |
| Reading outside the lock and re-reading inside | Redundant at this granularity (B6) |
| A second pass once the background compute has finished | Unneeded complexity for a rare, harmless case; the next boot handles it |

## 12. Known limits

- A session still without `created_at` when the list is read is processed at the next boot.
- A `process.ended` whose write was lost (logged failure) is never rewritten (unchanged limit of the earlier design).
- The id list is a snapshot: a candidate that becomes processed during the run is skipped by the in-lock re-check.
- A session whose rows can never be written (a permanent failure: a bug, or an unexpected legacy value) is retried at the end of every run and at every boot, and an error is logged each time. The cost is bounded (a few lock acquisitions per boot) and accepted: the error log is the signal that a fix is needed. If the cause is global (for example the database is unavailable), every session fails: the run then logs one error per session at the end of the retry rounds, and stays bounded; that volume is accepted and the log is not rate-limited.

## 13. Open points for the plan

1. **Migration placement.** The `(session_id, kind)` index goes with the timeline table's migration. If `0156_timeline_event` was never applied anywhere, the index is added to it; otherwise it is a new migration. The plan must check.
2. **Insertion method.** Chosen by the measurement of section 10.
3. **Log output.** A single summary line per run (sessions processed, rows written, duration); the level of per-session detail is left to the plan.
