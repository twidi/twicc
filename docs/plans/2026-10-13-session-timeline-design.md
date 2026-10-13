# Session Timeline — Event Recording Design

**Status:** draft v1, not reviewed. Brainstormed with the user 2026-10-13.
**Scope:** how TwiCC *records* the important events of a session in a new, append-only table. **Out of scope:** any display of the timeline (UI, CLI, MCP), any change to existing tables, any removal of existing columns.

---

## 1. Goal and non-goals

**Goal.** Keep a durable, ordered, queryable history of what happened to each session: it started, its process changed state, it ended (cleanly or not), the user archived, hid or muted it, and so on. Today TwiCC keeps only the *latest* value of a few facts (`Session.last_started_at`, `last_stopped_at`, `last_updated_at`, ...) and the *current* state of the live process (`ProcessRun`, whose DEAD rows are deleted).

**Non-goals.**

- No display. Reading the timeline is a later project.
- **The `Session` table is not touched.** No column added, changed or removed. The `last_*` columns stay as they are; the timeline is added next to them.
- No events for subagent sessions (`Session.type = subagent`), nor for ephemeral agents (no `ProcessRun`).
- No heartbeat and no periodic writer.
- No attempt to rebuild exact historical flag-change dates (they are not recoverable).

## 2. Decisions taken

| # | Decision |
|---|---|
| D1 | `session_id` is a plain `CharField` (like `ProcessRun`), not a FK. `Session` rows can be created after the process starts (`base_manager` only runs `Session.objects.filter(id=...).update(...)` at registration). `project` is a real FK to `Project`. |
| D2 | No events for subagent sessions. |
| D3 | Every state transition is stored (about two `turn.*` rows per assistant turn). |
| D4 | One table. Instance events (`backend.*`) live in it with `session_id` and `project` empty. |
| D5 | External sessions (run outside TwiCC) never get an inferred `process.started` or `process.ended`. The backfill cannot know whether they ran under TwiCC or still run, so it writes no process rows at all (section 8). |
| D6 | Event names follow one rule: `subject.verb` (section 4). |

## 3. Data model

New model `TimelineEvent` (app `core`), append-only. Rows are never updated and never deleted by normal code.

| Field | Type | Notes |
|---|---|---|
| `id` | `BigAutoField` | Total insertion order. Tie-breaker for equal `occurred_at`. |
| `session_id` | `CharField(200)`, null, indexed | `NULL` for instance events (D1, D4). |
| `project` | FK `Project`, null, `PROTECT`, `related_name="timeline_events"` | The project the session had *when the event happened* (raw FK, not the worktree-aggregated one). `NULL` for instance events. Projects are archived, never deleted. |
| `kind` | `CharField(40)`, `choices` | Closed enum, section 4. |
| `occurred_at` | `DateTimeField` | When it happened. May be earlier than `recorded_at` (backfill, inference). |
| `recorded_at` | `DateTimeField(auto_now_add)` | When the row was written. |
| `precision` | `CharField(10)` | `exact` (observed live), `derived` (computed from a timestamped datum), `estimated` (inferred, see `data.upper_bound`). |
| `actor` | `CharField(10)` | `user`, `agent`, `system`, `external`. In v1 every human-initiated change uses `user`, whatever the entry surface (UI, CLI, MCP). The registry accepts a reserved `data.via` (`ui` / `cli` / `mcp`) on every kind, but v1 never populates it (section 10). |
| `reason` | `CharField(30)`, blank | Reuses the existing vocabulary: `manual`, `force`, `archived`, `shutdown`, `apply-settings`, `switch-hybrid`, `error`, `startup-failed`, ... plus the new `lost` (section 7). |
| `caused_by` | self-FK, null, `SET_NULL` | The event that triggered this one (archive → process end). |
| `data` | `JSONField`, default `{}` | Small, kind-specific payload (section 5). |
| `dedupe_key` | `CharField(100)`, null | Makes backfill and reconciliation replayable. |

Indexes and constraints:

- `(session_id, occurred_at, id)` — one session's timeline in order.
- `(project, occurred_at, id)` — one project's timeline.
- `(kind, occurred_at)` — cross-session queries by kind.
- `UniqueConstraint(session_id, dedupe_key)` with `condition=Q(dedupe_key__isnull=False)`.

The enum, the `data` shape of each kind and a schema version live in one module (`core/timeline.py`): an enum of kinds plus one `NamedTuple` per payload. Unknown kinds and invalid payloads are rejected at write time.

**An `ended` row repeats the data of its `started` row, so that each line reads alone.** This applies to `awaiting_input.ended`, `process.ended`, `pinned.ended` and `backend.ended`. Their payload has the same fields as their `started` kind, plus their own fields. All these fields are optional (default null), because an interval can begin before the rollout, with no `started` row. The rule lives in one place, the recording service (section 6): for these four kinds, it fills every payload field the caller did not provide from the data of the **open** `started` row. The open `started` row is the latest row of the matching `started` kind with the same `session_id` (instance events: `session_id` NULL), provided no row of the matching `ended` kind with the same `session_id` is newer than it. Everywhere in this design, "newer", "later" and "after" between two rows mean a higher `id`, never a later `occurred_at`: lost-end rows carry back-dated times (section 7). A field the caller provides wins. When no open `started` row exists, the fields stay null. An `ended` row therefore never inherits from an interval that an earlier `ended` row already closed: this covers a lost `started` write (accepted at non-atomic sites, section 6) and several stale rows of one session at boot (section 7). The other interval kinds (`turn.*`, `archived.*`, `hidden.*`, `muted.*`) have no `started` payload, so nothing is copied.

## 4. Naming rule

`subject.verb`, lowercase.

- **Interval** (has a begin and an end): `<subject>.started` and `<subject>.ended`.
- **Point fact**: `<subject>.<past participle>` (`session.created`, `title.changed`).

Filtering all begins is `kind LIKE '%.started'`; one subject is `kind LIKE 'turn.%'`.

State subjects use the *state* noun, not the action noun: `archived`, `hidden`, `muted`, `pinned`.

| Subject | Begin | End |
|---|---|---|
| Process | `process.started` | `process.ended` |
| Assistant turn | `turn.started` | `turn.ended` |
| Waiting for the user | `awaiting_input.started` | `awaiting_input.ended` |
| Archived | `archived.started` | `archived.ended` |
| Hidden | `hidden.started` | `hidden.ended` |
| Muted | `muted.started` | `muted.ended` |
| Pinned | `pinned.started` | `pinned.ended` |
| Backend instance | `backend.started` | `backend.ended` |

Point facts (v1): `session.created`, `title.changed`, `settings.changed`.

There is no separate interval for background work: `turn.ended` carries it (section 5). A scope change while pinned is `pinned.ended` (old scope) followed by `pinned.started` (new scope).

A session's intervals are *derived* by pairing begin and end rows in order. An unmatched begin is an open interval (the end is unknown or still to come). Pairing is a display concern and is out of scope.

## 5. Event catalogue and payloads

### Tier 1 — recorded in v1

"Inherited" means the field is copied from the matching `started` row (section 3).

| Kind | Fires when | `data` |
|---|---|---|
| `session.created` | The `Session` row is first created (watcher or initial sync). `occurred_at` is the row-creation time (`Session.created_at` is nullable and set later by compute, so it is not used live). | `origin`: `twicc` = a `ProcessRun` already exists for the session id when the watcher creates the row (TwiCC always creates it before the JSONL file appears), `external` = the watcher creates the row and no `ProcessRun` exists, `sync` = rows created by the initial sync and by the backfill (a TwiCC-started session found by the initial sync is `sync`: its `settings` is null); `provider`; `settings`: the non-null fields of the closed agent-settings bundle (all eight), read from the created `Session` row, so the stored (clamped) value and not the request, and only when `origin` is `twicc` (otherwise null); `spawned_by`: id of the session that created this one (`Session.spawned_by_id`), when set. |
| *(initial flags)* | The row is created already `hidden` or `muted` (creation payload, or `pending_session_attributes` applied when the row appears; `pinned` is not part of that flow and is always unset at creation). | Not a kind: creation also emits the matching `hidden.started` / `muted.started` rows, same `occurred_at`, `caused_by=<session.created>`. |
| `title.changed` | The title changes. | `source`: `auto` / `user` / `agent`; `old` (previous title, null when none or unknown); `new`. |
| `settings.changed` | Any field of the closed agent-settings bundle changes. | `changes`: `{field: [old, new]}`. |
| `process.started` | A `ProcessRun` is created. | `provider`; `cause`: `new` / `resume` (v1: the manager only knows whether it resumes); `twicc_pid`; `hybrid`: the agent runs in hybrid CLI mode. |
| `process.ended` | The process is `DEAD`. | `reason` (column); `error` text when any; `upper_bound` (section 7); inherited: `provider`, `cause`, `twicc_pid`, `hybrid`. |
| `turn.started` | State enters `ASSISTANT_TURN`. | — |
| `turn.ended` | State leaves `ASSISTANT_TURN`; also re-emitted while the session stays in `USER_TURN` and its background work changes (below). | `background_work`: the `ProcessRun.background_work_in_progress` snapshot (`{subagents, shells, monitors, scheduled_wakeup_at, goal}`, counts) or null when nothing runs. |
| `awaiting_input.started` / `.ended` | `awaiting_user_input` flips. | `request_type` (tool approval, question, ...) and `tool_name`, both of the request that **opened** the interval (the oldest pending request). Requests added or removed while the interval is open write nothing: one interval per 0-to-n / n-to-0 flip of `bool(agent.pending_requests)`. Both inherited on `.ended`. |
| `archived.*`, `hidden.*`, `muted.*` | The flag flips. | — |
| `pinned.started` / `.ended` | `pinned` flips or changes scope. A scope change writes `.ended` (old scope) then `.started` (new scope), same `occurred_at`, the `.started` row `caused_by` the `.ended` row. | `scope`: `project` / `workspace` / `all`, on both rows (inherited on `.ended`). |
| `backend.started` | TwiCC boots. | `pid`, `boot_id`, `version`; `machine_restarted` (boot id differs from the previous `backend.started`). |
| `backend.ended` | Clean shutdown, or inferred at next boot. | `clean`: bool; inherited: `pid`, `boot_id`, `version`, `machine_restarted`. |

**Background work and `turn.ended`.** The session can wait for the user while work still runs behind the agent (a subagent, a shell, a monitor, a scheduled wake-up, a goal). A `turn.ended` whose `background_work` is non-null means "waiting for the user, but work still runs". Each later `turn.ended` re-emission updates that state. A `turn.ended` with `background_work` null means "really idle". Work that starts and ends inside a turn produces no row: the `turn.ended` at the end of the turn carries the state at that moment. No separate background-work interval exists, on purpose: it would add noise for work that starts and ends during a turn.

### Tier 2 — future scope (not in v1)

API error (529) and quota hit, send failure and resend, `/compact`, `/clear`, context saturation, auto-denied request, subagent spawned or ended on the parent, workflow started or ended, cron created or fired, Codex goal set or reached, plan approved, share created or revoked, artifact bookmarked, peer message sent or received, worktree created for the session. The `process.started` causes `cron_restart` and `peer` belong to the same future scope.

### Tier 3 — derived, never stored

Individual user messages, costs and token usage (already in `SessionItem`, `SessionHistoryFact` and `Session` columns), "first viewed since the last turn".

## 6. Recording architecture

One entry point: `record_event(kind, *, session_id=None, project_id=None, occurred_at=None, precision="exact", actor, reason="", caused_by=None, data=None, dedupe_key=None)` in `core/services/session_timeline.py`. For the four `ended` kinds with a `started` counterpart (section 3), it also fills the payload fields the caller left out from the open `started` row (section 3); this is the only place that rule is implemented.

- **Single-writer path.** Writes go through the DB writer (`providers/db_writer.py`), like other lifecycle writes. Every write, live or backfill, runs under `run_under_db_write_lock`, which serializes all of them.
- **Atomicity depends on the site.** Some sites already run inside `transaction.atomic` (DB-writer jobs, the boot cleanup): there the event is written in the same transaction, so change and event commit or roll back together, and the event insert runs in a nested savepoint so that a failed insert rolls back only the event row. Other sites only take the write lock and issue autocommit writes (`_register_and_start`, `_persist_process_run_transition`, `_settle_dead`, `apply_session_archived_change`): there the event is a separate write in the same lock acquire, and a crash between the change and the event can lose the event. That loss is accepted. In every case a failed timeline write is logged and never blocks or undoes the user action it describes.
- **Broadcasts stay outside any lock.** The timeline has no broadcast in v1.
- **Explicit calls, not Django signals.** The code base mutates rows with `queryset.update()` (e.g. `core/agent_runs.py`, `base_manager._update_session_stopped_at`); signals would miss them.
- **`project` resolution.** Call sites that have a `Session` use `session.project_id`. Process events take the project from the agent (`agent.project_id`), which is known before any `Session` row exists.

### Hook sites

| Site | Events |
|---|---|
| `BaseAgentManager._register_and_start` (right after `ProcessRun.objects.create`) | `process.started` |
| `BaseAgentManager._persist_process_run_transition` (diff of the row's old and new `state`, `awaiting_user_input`, `background_work_in_progress`) | `turn.*`, `awaiting_input.*`, `process.ended` |
| `BaseAgentManager._persist_background_work` (background work changes between transitions) | `turn.ended` re-emission while the session is idle |
| `apply_session_archived_change`, `hide_session` / `unhide_session`, `apply_session_mute_on_user_turn_change`, `apply_session_pinned_change` (`core/services/`) | `archived.*`, `hidden.*`, `muted.*`, `pinned.*` |
| Bulk "archive old sessions" endpoint in `views.py` (it archives with `queryset.aupdate`, bypassing `apply_session_archived_change`) | One `archived.started` per affected session, emitted only for sessions whose `archived` is still false once the write lock is held (the endpoint computes its id list before taking the lock). The candidate filter excludes active, pinned and hidden sessions when the id list is computed, but the write filters only by id; the timeline records exactly the sessions whose `archived` flipped under the lock, and writes no `process.ended` or `pinned.ended`. |
| Title and settings update services | `title.changed`, `settings.changed` |
| Watcher session creation (`create_session_sync`), DB-writer initial-sync create (`_apply_create_session_payload`); `session_creation.py` only stashes pending attributes and writes nothing | `session.created` (+ initial `hidden.started` / `muted.started`) |
| `process_run_cleanup._cleanup_stale_process_runs` and the shutdown path | Reconciliation and `backend.*` (section 7) |

Rules:

- **Close inner intervals first.** When a process goes `DEAD`, the transition site also emits `turn.ended` (with `background_work` null) and `awaiting_input.ended` for any inner interval still open in the previous row, then `process.ended`, all with the same `occurred_at`.
- **Idle with background work.** One pure function of the previous and new row values produces the process events:
  - Leaving `ASSISTANT_TURN` to a state other than `DEAD` (normally `USER_TURN`) emits `turn.ended` with the new `background_work` snapshot (or null).
  - Leaving `ASSISTANT_TURN` to `DEAD` emits `turn.ended` with `background_work` null; `process.ended` follows.
  - While the session stays in `USER_TURN` and the snapshot changes (exact dict equality; null against a dict is a change), `turn.ended` is emitted again with the new snapshot. No change, no row. The last re-emission has `background_work` null: "really idle now".
  - A background-work change during `ASSISTANT_TURN` or any other state emits nothing: the `turn.ended` at the end of the turn carries the state at that time.
  - Death from `USER_TURN`, even with work running, emits nothing from this function: `process.ended` alone closes everything. A later `turn.started` supersedes a pending "idle but busy" state.
  - `turn.started` has no payload. `awaiting_input.*` follows the `awaiting_user_input` flag.
- **Every writer of the four flags is hooked.** This is a design requirement, not a best effort: a missed writer leaves the timeline saying "not archived" for an archived session, unrecoverable later. The plan must grep every writer of `archived`, `hidden`, `pinned` and `mute_on_user_turn` (including `queryset.update`/`aupdate` paths) and confirm the hook-site list. The same requirement applies to `title.changed` and `settings.changed`, whose writers are scattered: auto and agent title writes in `providers/compute_base.py`, retirement-driven model upgrades in `providers/db_writer.py`, runtime writes through `persist_session_settings` in `core/services/session_update.py`, besides the update services themselves.
- **Flag changes record only real flips.** Setting a flag to its current value writes nothing.
- **Causality.** Archiving writes `archived.started`, then the kill it triggers writes `process.ended(reason="archived", caused_by=<archived.started>)`. The `DEAD` callback runs later in the agent's own task and receives no event id, so when `reason="archived"` the transition site resolves `caused_by` as the latest `archived.started` of that session.
- **Archive can unpin.** `apply_session_archived_change` can clear `pinned` itself (`also_unpin`), without calling `apply_session_pinned_change`. That hook therefore also emits `pinned.ended` (scope inherited) with `caused_by=<archived.started>`.
- **Pin scope change.** `apply_session_pinned_change` writes `pinned.ended` then `pinned.started` with the same `occurred_at`; the `started` row is `caused_by` the `ended` row.
- **Flags set at creation** are covered by the creation rule in section 5, not by the flag services.
- **Deliberate vs unsolicited.** `process.ended.reason` carries the existing kill reason. `twicc.agent.states.DELIBERATE_STOP_REASONS` already separates deliberate stops from the rest; the timeline reuses it and invents no second classification.
- **Ordering.** `(occurred_at, id)`. Simultaneous events differ by `id`; causality by `caused_by`.

## 7. Crash, restart and machine failure

| Case | Detection | Written |
|---|---|---|
| Agent subprocess dies, TwiCC lives | The manager observes `DEAD` | `process.ended(reason="error", precision="exact")` |
| Clean TwiCC shutdown | `BaseAgentManager.shutdown` kills agents with `reason="shutdown"` | `process.ended(reason="shutdown")` per agent, then one `backend.ended(clean=true)`. The instance-level event is written by the global shutdown sequence, **after all provider managers have shut down and before the DB writer stops**; no single manager owns it. |
| TwiCC crash or machine failure | Nothing is written at crash time. Detected **at the next boot** | Below |

Boot reconciliation (inside the DB-writer job that already runs `_cleanup_stale_process_runs`, **before** it bulk-updates the rows to `DEAD`, because it needs their previous `state`):

1. For each `ProcessRun` row whose `state` is not `DEAD`: emit the inner ends from the row's `state` and `awaiting_user_input` (`turn.ended` with `background_work` null when the row is in `ASSISTANT_TURN`; `awaiting_input.ended` when it awaits input), then `process.ended(reason="lost", precision="estimated")`. A process lost while in `USER_TURN`, even with background work, emits only `process.ended`. `process.ended` inherits the data of the open `process.started` row (section 3). The rows written for a lost process, inner ends included, have `precision="estimated"`, like the `lost` end. If several non-`DEAD` rows share one session (the cleanup dedups them only afterwards), the first one processed closes the open `process.started` and inherits; the others find no open `started` row and keep null fields. An unmatched end is acceptable: a process that started before the rollout, or whose `process.started` write was lost (section 6), yields a `lost` end with no begin. Pairing is a display concern.
2. `occurred_at` is the **last evidence of life**: `max(ProcessRun.last_state_change_at, Session.last_updated_at, last SessionItem timestamp)`. Never the boot time, which would invent hours of activity. The boot time goes in `data.upper_bound`.
3. Step 3 runs after step 1. Find the latest `backend.started` with no later `backend.ended`. If there is one, the previous instance did not stop cleanly: emit `backend.ended(clean=false, precision="estimated")` (inheriting the data of that open `backend.started`) at the greatest `occurred_at` among all rows written after that `backend.started`, except backfilled rows (`dedupe_key` starting with `backfill:`), or at the `backend.started` time when there is none. The rows written after that `backend.started` are the previous instance's live rows and this boot's step-1 rows (dated at the evidence of life of the processes the previous instance ran): all of them count, since the previous instance was alive then. **Clamp.** The `occurred_at` of `backend.ended(clean=false)` is clamped to the interval [previous `backend.started`.`occurred_at`, current-boot `backend.started`.`occurred_at`]. The lower bound keeps it from being dated before its own start (a back-dated lost-end row written after that start would otherwise pull it earlier). The upper bound keeps it from being dated after the next instance began (after a power loss the clock may be wrong until NTP syncs). The evidence rows keep their own dates. Ordering between rows is by id (section 3), so the clamps only keep the dates plausible. If the clock went backwards so that the previous `backend.started` is dated after the current boot's, the upper bound wins and the pair reads as a negative duration; the id order stays correct. This attribution relies on the **single-instance guarantee per data dir** (`instance_lock.py`): events carry no instance id, because at most one instance writes at a time.
4. Emit `backend.started` for the current instance, with a `boot_id` when the platform provides one (Linux: `/proc/sys/kernel/random/boot_id`). `boot_id` is nullable. A different `boot_id` than the previous `backend.started` sets `machine_restarted=true`; same sets `false`; if either is missing (macOS, first run) it is `null` (unknown), never `false`.
5. If a provider then restores the session (Claude Code cron restart), the normal path emits `process.started(cause="resume")`. The lost interval stays readable.

`ProcessRun` rows that are already `DEAD` at boot (kept for crons) emit nothing: their end was recorded when they died.

**Hybrid adoption.** `adopt_running_hybrid_sessions` (Claude Code manager) re-attaches a hybrid CLI that survived the restart through `_register_and_start(resume=True, adopt=True)`. The timeline records TwiCC's attachment, not the life of the CLI: it reads `process.ended` (`lost`, or `shutdown`) then `process.started(cause="resume", hybrid=true)`, even though the CLI itself never died. This is the intended v1 behaviour.

## 8. Backfill of existing data

Goals: give every existing session a believable starting point; never invent facts.

- **Mechanism.** A background job in the style of `compute_version` (idempotent, batched, resumable), not a heavy schema migration. The migration creates the empty table only. Backfill logic is written inline in the job and does not import application models' helper code into a migration.
- **Idempotency.** Every backfilled row carries `dedupe_key = "backfill:<kind>[:<detail>]"`. A second run inserts nothing.
- **Marker.** Backfilled rows have `recorded_at` far after `occurred_at`; `precision` says how much to trust them.

| Source | Row | `precision` |
|---|---|---|
| `Session.created_at` | `session.created` (`origin="sync"`, `spawned_by` from the column when set, `settings` null); skipped when `created_at` is null | `derived` |
| `archived`, `hidden`, `mute_on_user_turn` true | `<flag>.started` at `Session.last_updated_at` | `estimated` |
| `pinned` not null | `pinned.started` (`scope` from the value) at `last_updated_at` | `estimated` |

`last_updated_at` is nullable. When it is null, the flag rows fall back to `created_at`; when both are null, the row is skipped.

What the backfill deliberately does **not** do:

- **No `process.started` and no `process.ended` at all.** `Session.last_started_at` and `last_stopped_at` cannot prove that TwiCC ran a process. Both are recomputed from the JSONL file by the full compute (`last_stopped_at` becomes the file mtime), and they are also stamped for subagents. Using them would invent a TwiCC process for an external session (D5) and give a file mtime the precision of a process end. Process history therefore starts at the rollout.
- **No historical turns or awaiting-input intervals.** `SessionHistoryFact` does not contain them: `PROCESS_START` is a process spawned *by a tool call*, not an agent process, and `TURN_START` exists for Codex only. Rebuilding turns from `SessionItem` is possible but lossy, and is left for a later decision.
- **No exact flag dates.** They were never stored.

## 9. Consequences and limits

- **No cascade.** `session_id` is not a FK. Rows survive a session purge. Sessions are never deleted by policy, so this is acceptable; a purge path, if one appears, must delete the timeline rows explicitly.
- **Volume.** About two `turn.*` rows per assistant turn, one more `turn.ended` per change of background work while idle, plus occasional others; SQLite handles it. A retention policy for `turn.*` is a later option, not a v1 need.
- **Estimated times are bounded, not exact.** A `lost` end can be earlier than the true crash by the time between the last visible activity and the crash.
- **External sessions** (terminal `claude`, `codex`) appear in the timeline only through `session.created` and the user's flag changes. TwiCC sees only their JSONL files.
- **Duplicates between live and backfill.** Live recording starts at the deploy; the batched backfill may reach a session long after. One general rule: the backfill skips `session.created` when the session already has a live `session.created`, and skips a flag's `<flag>.started` when the session already has any live row (`dedupe_key` not starting with `backfill:`) for that flag subject, since the live rows are the truth. For each row, the check and the insert run together under `run_under_db_write_lock`, the same lock every live write takes (section 6), so no live row can slip in between.
- **Hybrid adoption** shows a `process.ended` / `process.started` pair for a CLI that never died (section 7).
- **Agent dying before its `Session` row exists.** A TwiCC-started agent that dies before the watcher creates the `Session` row (its non-kept `ProcessRun` already deleted) is later recorded with `origin="external"` and `settings` null.
- **Idle state after a resume.** A session entering `USER_TURN` directly from `STARTING` (for example a resume with a goal or a monitor still running) writes no `turn.ended`, so it reads as idle without background work until the first snapshot change.
- **No process history before the rollout.** Consequence of section 8: the timeline of an existing session shows its creation and its flag states, and its process events only from the deploy onward.

## 10. Open points and future scope

Open point:

1. **Pairing rule at display time** for open intervals whose end is unknown (out of scope here, but the data model must not block it).

Resolved in this design:

- **Project resolution** for the first process event of a new session: the agent carries `project_id` (section 6).
- **Pin scope change:** `pinned.ended` (old scope) then `pinned.started` (new scope), same `occurred_at` (sections 4 and 5).

Deferred to a future scope (not in v1, not in the plan):

- **`actor` precision and `data.via` plumbing.** Today every human-initiated flag, title and settings event uses actor `user`. `data.via` is accepted by the registry on every kind but never populated.
- **`awaiting_input.ended` `outcome`** (allowed / denied / auto-denied / cancelled).
- **All Tier 2 events** (section 5) and the `process.started` causes `cron_restart` and `peer`. v1 causes stay `new` and `resume`.
