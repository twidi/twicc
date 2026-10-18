# Timeline Creation Rows From the Background Compute — Design

**Status:** reviewed by independent adversarial subagents until PASS (4 rounds). Written with the user 2026-10-18.
**Date:** 2026-10-18
**Scope:** who writes `session.created` (and the initial flag rows) for the sessions that TwiCC did not see being created, and when. It replaces the boot backfill of `2026-10-14-timeline-incremental-backfill-design.md` with a step of the background compute. It also changes one rule of `title.changed` and one signal rule of `2026-10-15-timeline-display-design.md` (D14). It changes nothing else.
**Baseline:** branch `feature/session-timeline` at commit `a372072ea` (rebased onto `main`, compute versions 116 / 57). The earlier timeline designs and plans are not amended: they describe the system at their moment. This document describes the next step.

---

## 1. Why change

Today three writers produce the creation row of a session:

| Writer | When | Date written |
|---|---|---|
| Initial-sync hook (`providers/db_writer.py`, `_apply_create_session_payload`) | When the initial sync inserts a session row | The current time, precision `exact` |
| Watcher hook (`providers/sessions_watcher.py`, `create_session_sync`) | When the watcher creates a session row while TwiCC runs | The current time, precision `exact` |
| Boot backfill (`core/services/timeline_backfill.py`, task started in `cli/run.py`) | At every boot, concurrently with the initial sync and the compute | `Session.created_at`, precision `derived` |

Problems:

- **Wrong date for every session found by the initial sync.** The initial sync inserts the row before the compute has read the file. The hook writes the time of the discovery, not the time of the creation. The backfill then sees a `session.created` row and never corrects it. The wrong date is permanent.
- **The backfill reads data that is not ready.** It runs while the initial sync and the compute work. `Session.created_at` is written by the compute (first timestamp of the whole file). Before that, `created_at` is empty, or it holds a provisional value written by a live slice (the first timestamp of the appended lines only). The backfill can read that provisional value and write a wrong date.
- **Late entries.** A session not yet computed when the backfill lists its candidates waits for the next boot.

Only the compute has the correct data, at the moment it writes it.

## 2. Goals and non-goals

**Goals.**

- Every `session.created` row carries the correct date.
- Rows appear progressively while the compute runs, not at the end of a full compute pass.
- Each session is handled once, by the component that knows its data.
- One mechanism for the first rollout, for later version changes and for every session discovered later.

**Non-goals.**

- No correction of rows already written with a wrong date. No released instance has any (the branch is not released); only development databases that ran the branch can have them.
- No change to what a creation row contains (section 5.2), or to the boot reconciliation (`timeline_boot.py`). The only changes to the live hooks are the removal of the initial-sync hook and the title rule of section 6.
- No change to how the compute selects, orders or publishes sessions.

## 3. Decisions

| # | Decision |
|---|---|
| C1 | The background compute writes the creation row of a session in the **final transaction** of that session's full compute (`BaseSessionCompute._apply_session_complete_final`), with the `created_at` it has just written. |
| C2 | The compute versions are bumped: `CLAUDE_CODE_COMPUTE_VERSION` 116 → **117**, `CODEX_COMPUTE_VERSION` 57 → **58**. Every existing session is then selected again by the compute. It gets its row when its compute completes (C1), or through C9 when the Codex compute marks it or a member of its tree unavailable. A session whose compute never completes for another reason gets no row (section 11). |
| C3 | The initial-sync hook is removed. A session inserted by the initial sync always has an empty `compute_version`, so the compute always selects it. |
| C4 | The boot backfill task is removed, with its candidate list and its retry rounds. The rules that build the rows (section 5.2) are kept and used by the compute step. |
| C5 | The watcher hook is kept, unchanged. It is dated at the moment TwiCC sees the new file, precision `exact`. It is the only writer that knows whether TwiCC created the session and which request created it. |
| C6 | A session is "known to the timeline" when it has a `session.created` row (unchanged rule B1 of the backfill design). The compute step writes nothing for a session already known. |
| C7 | An automatic or agent `title.changed` is not written for a session that is not yet known to the timeline. A title that exists before the timeline knows the session is its initial title, not a change. A `user` title is always written. |
| C8 | The compute step requests the live signal after its transaction commits, through the normal throttled path. This replaces D14 of the display design ("silent per session, one signal at the end of a run"). |
| C9 | A Codex session that the compute marks unavailable never reaches the final transaction, and neither does the root of its tree. The step that marks a session unavailable writes the creation row of its root instead (the session itself when it is a root), with the same rules (section 5.4). |

## 4. Who writes the creation row, per case

| Case | `compute_version` at row creation | Writer | Date |
|---|---|---|---|
| Session found by an initial sync (fresh install, file added while TwiCC was stopped, `sync --reset`, provider enabled while TwiCC runs) | Empty | Compute step | `created_at` from the full file |
| Session that grew while TwiCC was stopped | Reset to empty | Compute step, only if the session is not yet known | Unchanged when already known |
| Compute version change (users: from the last release to 117 / 58; the owner's instance: from 116 / 57) | Lower than the target | Compute step, session by session, only for the sessions not yet known | `created_at` from the full file |
| New file while TwiCC runs (created by TwiCC or outside) | The current version (Codex legacy rollouts: empty) | Watcher hook | The moment TwiCC sees the file |
| Codex legacy rollout created while TwiCC runs | Empty | Watcher hook. The later compute finds the row and writes nothing. | The moment TwiCC sees the file |
| Codex session marked unavailable, or Codex root whose tree has a member marked unavailable | Lower than the target, never reaches the target | The step that marks the session unavailable (C9), only if the root is not yet known | Stored `created_at` (section 5.4) |
| Session whose compute fails at every attempt without being marked unavailable (both providers), or root blocked by such a member | Lower than the target | Nobody (section 11) | — |

A session row is created by one of two creation paths: the initial sync's insert, or the watcher's creation function (the Codex agent also creates its rows through that function, so it gets the watcher hook). Whichever path creates the row is the only one that may write the hook row. The watcher starts after the initial sync of its provider. A session that TwiCC starts while the boot's initial sync runs can be inserted by the initial sync: it then gets a compute row (`origin=sync`, no settings), as it does today.

Order of appearance: the compute handles roots roughly newest `mtime` first, and the root of a tree last. The Codex compute first takes the roots under recovery (a member unavailable, failed, deferred or forced), then the other roots by `mtime`. Creation rows therefore appear roughly newest session first, one root at a time, from the first root computed after the initial sync.

## 5. The compute step

### 5.1 When

Inside the final transaction of a full compute, for a normal session (`type=session`), once the session fields are written. Subagent sessions get no creation row (unchanged rule).

Both compute publication paths (progressive and chunked) reach the final transaction, so both are covered.

The step must not change the outcome of the compute:

- It never raises. A failure is logged, and only the timeline rows are discarded. The compute publication continues and succeeds.
- A compute publication that is discarded or rolled back for its own reasons discards the timeline rows with it. The retry of the compute writes them again.
- It takes no extra lock acquisition. It runs inside the hold that the compute already has.
- Its cost is bounded by a few index reads and a handful of rows, whatever the size of the session or of the timeline. It is measured in the existing hold diagnostics of the final transaction.

### 5.2 What

Unchanged from the backfill (`2026-10-14-timeline-incremental-backfill-design.md` §7), only for a session not yet known to the timeline:

- `session.created`, dated `created_at`, `precision=derived`, `origin=sync`, `provider`, `spawned_by` from the column, `settings` null, actor `system`.
- For each of `archived`, `hidden`, `mute_on_user_turn` (true) and `pinned` (not null): the matching `<flag>.started`, dated `last_updated_at` (fallback `created_at`), `precision=estimated`, with `scope` for `pinned`. Skipped when the session already has a row of that subject.
- The deduplication keys stay `backfill:<kind>`. The boot reconciliation excludes rows with this prefix from the last evidence of life of a crashed instance; that rule must keep applying to these estimated rows.
- Never any process, turn or backend row.

A session whose `created_at` is still empty after its compute (a file without any timestamp) gets no row. Accepted and expected. This holds even if the file grows later and a live slice then sets `created_at`: the session is already at the current compute version, so no compute step runs for it until the next version change (section 11).

### 5.3 Concurrency

The compute's final transaction and the watcher hook both write under the DB write lock, so they never interleave. The step checks "known to the timeline" inside the transaction: this check is what prevents a compute row next to a watcher row. The watcher row has no deduplication key, so the partial unique constraint on `(session_id, dedupe_key)` with `ON CONFLICT DO NOTHING` only stops a duplicate between rows that carry the `backfill:<kind>` keys.

### 5.4 Codex sessions marked unavailable (C9)

The Codex compute checks the rollout file before computing. When the file is missing, or when the Codex migration fails for a reason classified as "rollout unreadable", the session is marked unavailable (`unavailable_reason` set). Its `compute_version` stays below the target. It is selected again at each pass and never reaches the final transaction. A tree with such a member keeps that member required, so its root never reaches the final transaction either. Such sessions stay visible in TwiCC. Other Codex failures are not marked unavailable: they are plain failures, retried (section 11).

When the compute marks a session unavailable (a reason is set, not cleared), it also writes the rows of section 5.2 for the root of that session's tree (the session itself when it is a root):

- The flag and the rows commit together, in one transaction. A failure of the rows never prevents the marking: it is logged, and only the rows are discarded ("never raises").
- Nothing is written when the root is already known.
- The rows use the root's stored `created_at`, and only when the root's `compute_version` is not empty. This is a conservative proxy for "the value comes from a computed file read from its first line": an empty version means the row was never computed, or its file changed since, so `created_at` can be empty or provisional. Such a root gets no row.
- Clearing the flag (the rollout became readable and its compute was applied) writes nothing. The normal compute then finds the row, or writes it in its final transaction.

## 6. Titles (C7)

Problem: on the first compute of a historical session, the compute writes the session's title for the first time (a placeholder or a provider title). Today this produces a `title.changed(source=auto, old=null)` dated at the moment of the compute, for every historical session. The Codex title import (`providers/codex/titles.py`) produces the same rows for Codex sessions. The timeline design already says the initial title is not recorded.

Rule: an `auto` or `agent` title written for a session not yet known to the timeline produces no `title.changed`. In the final transaction of a session's first compute, the title step must see the session as not yet known: the creation row is written after the title step, or the step ignores the row written by the same transaction.

The rule applies to every `auto` and `agent` title writer: the compute's placeholder and provider titles (full compute and live slices), the automatic title task, and the Codex title import.

Effects:

- First compute of a historical session: no `title.changed`. The creation row comes alone.
- Session created while TwiCC runs: the watcher row exists from the start, so every later title (live slice, automatic title, Codex import) is recorded, as today.
- Later recomputes of a known session: a title that really changes is recorded, as today.
- A rename by the user is always recorded, even before the session is known.

## 7. Live signal (C8)

The compute step and the unavailable-marking step (C9) request the signal after their transaction commits, only when they wrote at least one row. The existing throttle (about 3 signals per second, trailing) merges the requests of consecutive roots. The display already inserts back-dated rows at their sorted place (display design §9.2, insertion rules). A rolled-back transaction requests no signal.

Display design D14 ("silent per session, at most one signal at the end of a run") no longer applies: there is no backfill run any more.

## 8. Rollout

- **Users who update from the last release:** they go from their current compute versions to 117 / 58. They recompute once, as they would for the versions already on `main`. Creation rows appear root by root during that pass.
- **The owner's instance and development databases already at 116 / 57:** one more full recompute. Accepted by the owner.
- **No schema change.** The migration `0163_timeline_event` is unchanged.
- **Fresh install:** the initial sync runs first and writes no creation row. The compute then writes them, progressively.

## 9. Documentation

- `CLAUDE.md` and `AGENTS.md`: the `TimelineEvent` paragraph describes the backfill task and D14. Update it: creation rows of discovered sessions come from the compute's final transaction; the signal comes per committed transaction.
- The comment of the watcher hook: it is now the only live writer of a creation row.
- The comment next to the compute versions in `settings.py`: name the timeline creation rows as the reason for 117 / 58.

## 10. Tests (what they must show)

- The initial sync inserts a session and writes no timeline row.
- A full compute of a session not yet known writes `session.created` dated at the `created_at` computed from the whole file, not at a provisional value and not at the current time. It writes the flag rows of section 5.2.
- A full compute of a session already known (watcher row, or an earlier compute) writes nothing.
- A subagent's compute writes nothing. A session with an empty `created_at` after compute gets nothing.
- A failure of the timeline step leaves the compute applied, logs the error, and leaves no partial timeline row.
- A compute publication that is discarded or rolled back leaves no timeline row and requests no signal.
- The signal is requested after commit when rows were written, and not otherwise.
- A Codex root marked unavailable gets its rows from its stored `created_at` when its `compute_version` is not empty, and no row when it is empty. A Codex subagent marked unavailable gives its root the same treatment. A second marking writes nothing. Clearing the flag writes nothing. A failure of the rows leaves the session marked and logs the error. The signal is requested after commit when rows were written.
- The first compute of a historical session writes no `title.changed`. A later real title change of a known session is recorded. A user rename of a session not yet known is recorded. The automatic title task, a live slice title and the Codex title import write no `title.changed` for a session not yet known, and do write one for a known session.
- The boot backfill task no longer exists and nothing starts it.
- The compute versions are 117 and 58.

## 11. Known limits

- **No retry.** A timeline step that fails is logged and not retried until the next compute version change. A watcher hook that fails is never retried. Both failures are rare (a savepoint inside a transaction that otherwise succeeds). Accepted. Consequence: such a session is not known to the timeline, so its later `auto` and `agent` title changes are not recorded either (C7). Its user renames, flag changes and process rows are still recorded.
- **Compute that never completes.** A session whose compute fails at every attempt without being marked unavailable never reaches the final transaction. Examples: a compute bug (both providers), and the Codex failures that are not classified as "rollout unreadable" (an empty rollout, a migration failure of another class, an unreadable or mismatched session header, an oversized legacy line, an inconsistent history mode). The same holds for the root of a tree that has such a member. Such a root gets no row, and its `auto` and `agent` title changes are not recorded. Accepted: these failures already leave the session's metadata stale. A fix of the cause comes with a code change, usually with a compute version change, and the compute then writes the row.
- **Unavailable Codex root with an empty compute version.** A root marked unavailable (or blocked by an unavailable member) whose `compute_version` is empty gets no row, and its `auto` and `agent` title changes are not recorded. The version is empty when the root was never computed, and also when it was computed before but its version was reset since: its file grew while TwiCC was stopped, its history was replaced, or a rebuild was requested after an external rewrite. In those reset cases the stored `created_at` is still correct, but the proxy of section 5.4 does not use it. The root stays without a row as long as the member stays unavailable. Accepted.
- **Session without a timestamp at its compute.** It gets no row, even if a live slice sets its `created_at` later, until the next compute version change. Its `auto` and `agent` title changes are not recorded meanwhile. Accepted.
- **Watcher date for a file that carries older history** (a forked or copied session): the row is dated when TwiCC sees the file, while `Session.created_at` holds the first line's timestamp. Accepted.
- **Title changes of a session not yet known are lost.** An `auto` or `agent` title change that happens before the session's first compute (a session found by the initial sync that grows while it waits for the compute) is not recorded. Accepted: the session is not on the timeline yet.
- **A recompute that changes `created_at`** does not move an existing creation row (append-only rule). Accepted.
- **Flood at first rollout.** Every historical session gets its creation row during the recompute, all dated at their creation. They fill the global timeline progressively. Already accepted by the display design.

## 12. Decisions that were considered and dropped

| Idea | Why dropped |
|---|---|
| Keep the boot backfill as a safety net, filtered on computed sessions, and no version bump | Its only gains were avoiding one recompute on the owner's instance and retrying rare failures. The owner prefers one mechanism and accepts the recompute. |
| A dedicated timeline version or column on `Session` | Changes the compute's selection rule and its index, touches the `Session` table, and still costs a full compute per session. |
| A separate task triggered after each compute publication | One more lock acquisition per root and one more task, for what the final transaction gives for free; not atomic with the compute. |
| Date the watcher row at the file's first timestamp | Needs the creation origin carried across slices and restarts, for a rare case. |
