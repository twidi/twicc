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
| `actor` | `CharField(10)` | `user`, `agent`, `system`, `external`. UI, CLI and MCP all map to `user`; the entry surface goes in `data.via` (`ui` / `cli` / `mcp`) when known. |
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
| Background work | `background_work.started` | `background_work.ended` |
| Archived | `archived.started` | `archived.ended` |
| Hidden | `hidden.started` | `hidden.ended` |
| Muted | `muted.started` | `muted.ended` |
| Pinned | `pinned.started` | `pinned.ended` |
| Backend instance | `backend.started` | `backend.ended` |

Point facts (v1): `session.created`, `title.changed`, `settings.changed`, `pinned.changed` (scope change while pinned).

A session's intervals are *derived* by pairing begin and end rows in order. An unmatched begin is an open interval (the end is unknown or still to come). Pairing is a display concern and is out of scope.

## 5. Event catalogue and payloads

### Tier 1 — recorded in v1

| Kind | Fires when | `data` |
|---|---|---|
| `session.created` | The `Session` row is first created (TwiCC, watcher or initial sync). `occurred_at` is the row-creation time (`Session.created_at` is nullable and set later by compute, so it is not used live). | `origin`: `twicc` / `external` / `sync`; `provider`. |
| *(initial flags)* | The row is created already `hidden` or `muted` (creation payload, or `pending_session_attributes` applied when the row appears; `pinned` is not part of that flow and is always unset at creation). | Not a kind: creation also emits the matching `hidden.started` / `muted.started` rows, same `occurred_at`, `caused_by=<session.created>`. |
| `title.changed` | The title changes. | `source`: `auto` / `user` / `agent`; `new`. |
| `settings.changed` | Any field of the closed agent-settings bundle changes. | `changes`: `{field: [old, new]}`. |
| `process.started` | A `ProcessRun` is created. | `provider`; `cause`: `user_message` / `resume` / `cron_restart` / `peer` / ...; `twicc_pid`. |
| `process.ended` | The process is `DEAD`. | `reason` (column); `error` text when any. |
| `turn.started` / `turn.ended` | State enters / leaves `ASSISTANT_TURN`. | — |
| `awaiting_input.started` / `.ended` | `awaiting_user_input` flips. | `request_type` (tool approval, question, ...). |
| `background_work.started` / `.ended` | `background_work_in_progress` flips between `None` and non-`None`. | `kinds` of work at start. |
| `archived.*`, `hidden.*`, `muted.*` | The flag flips. | — |
| `pinned.started` / `.ended` / `.changed` | `pinned` flips or changes scope. | `scope`: `project` / `workspace` / `all`. |
| `backend.started` | TwiCC boots. | `pid`, `boot_id`, `version`; `machine_restarted` (boot id differs from the previous `backend.started`). |
| `backend.ended` | Clean shutdown, or inferred at next boot. | `clean`: bool. |

### Tier 2 — candidates, decided later

API error (529) and quota hit, send failure and resend, `/compact`, `/clear`, context saturation, auto-denied request, subagent spawned or ended on the parent, workflow started or ended, cron created or fired, Codex goal set or reached, plan approved, share created or revoked, artifact bookmarked, peer message sent or received, worktree created for the session.

### Tier 3 — derived, never stored

Individual user messages, costs and token usage (already in `SessionItem`, `SessionHistoryFact` and `Session` columns), "first viewed since the last turn".

## 6. Recording architecture

One entry point: `record_event(kind, *, session_id=None, project_id=None, occurred_at=None, precision="exact", actor, reason="", caused_by=None, data=None, dedupe_key=None)` in `core/services/session_timeline.py`.

- **Single-writer path.** Writes go through the DB writer (`providers/db_writer.py`), like other lifecycle writes. Every write, live or backfill, runs under `run_under_db_write_lock`, which serializes all of them.
- **Atomicity depends on the site.** Some sites already run inside `transaction.atomic` (DB-writer jobs, the boot cleanup): there the event is written in the same transaction, so change and event commit or roll back together, and the event insert runs in a nested savepoint so that a failed insert rolls back only the event row. Other sites only take the write lock and issue autocommit writes (`_register_and_start`, `_persist_process_run_transition`, `_settle_dead`, `apply_session_archived_change`): there the event is a separate write in the same lock acquire, and a crash between the change and the event can lose the event. That loss is accepted. In every case a failed timeline write is logged and never blocks or undoes the user action it describes.
- **Broadcasts stay outside any lock.** The timeline has no broadcast in v1.
- **Explicit calls, not Django signals.** The code base mutates rows with `queryset.update()` (e.g. `core/agent_runs.py`, `base_manager._update_session_stopped_at`); signals would miss them.
- **`project` resolution.** Call sites that have a `Session` use `session.project_id`. Process events resolve the project from the `Session` row when it exists; if it does not exist yet, the write is retried once the row exists, or the project is taken from the agent's launch context (to settle at plan time).

### Hook sites

| Site | Events |
|---|---|
| `BaseAgentManager._register_and_start` (right after `ProcessRun.objects.create`) | `process.started` |
| `BaseAgentManager._persist_process_run_transition` (diff of the row's old and new `state`, `awaiting_user_input`, `background_work_in_progress`) | `turn.*`, `awaiting_input.*`, `background_work.*`, `process.ended` |
| `BaseAgentManager._persist_background_work` | `background_work.*` |
| `apply_session_archived_change`, `hide_session` / `unhide_session`, `apply_session_mute_on_user_turn_change`, `apply_session_pinned_change` (`core/services/`) | `archived.*`, `hidden.*`, `muted.*`, `pinned.*` |
| Bulk "archive old sessions" endpoint in `views.py` (it archives with `queryset.aupdate`, bypassing `apply_session_archived_change`) | One `archived.started` per affected session, emitted only for sessions whose `archived` is still false once the write lock is held (the endpoint computes its id list before taking the lock). Those sessions are inactive and unpinned, so no `process.ended` or `pinned.ended` follows. |
| Title and settings update services | `title.changed`, `settings.changed` |
| `session_creation.py`, watcher session creation, initial sync | `session.created` (+ initial `hidden.started` / `muted.started`) |
| `process_run_cleanup._cleanup_stale_process_runs` and the shutdown path | Reconciliation and `backend.*` (section 7) |

Rules:

- **Close inner intervals first.** When a process goes `DEAD`, the transition site also emits `turn.ended`, `awaiting_input.ended` and `background_work.ended` for any inner interval still open in the previous row, then `process.ended`, all with the same `occurred_at`.
- **Every writer of the four flags is hooked.** This is a design requirement, not a best effort: a missed writer leaves the timeline saying "not archived" for an archived session, unrecoverable later. The plan must grep every writer of `archived`, `hidden`, `pinned` and `mute_on_user_turn` (including `queryset.update`/`aupdate` paths) and confirm the hook-site list. The same requirement applies to `title.changed` and `settings.changed`, whose writers are scattered: auto and agent title writes in `providers/compute_base.py`, retirement-driven model upgrades in `providers/db_writer.py`, runtime writes through `persist_session_settings` in `core/services/session_update.py`, besides the update services themselves.
- **Flag changes record only real flips.** Setting a flag to its current value writes nothing.
- **Causality.** Archiving writes `archived.started`, then the kill it triggers writes `process.ended(reason="archived", caused_by=<archived.started>)`. The `DEAD` callback runs later in the agent's own task and receives no event id, so when `reason="archived"` the transition site resolves `caused_by` as the latest `archived.started` of that session.
- **Archive can unpin.** `apply_session_archived_change` can clear `pinned` itself (`also_unpin`), without calling `apply_session_pinned_change`. That hook therefore also emits `pinned.ended` with `caused_by=<archived.started>`.
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

1. For each `ProcessRun` row whose `state` is not `DEAD`: emit the inner `*.ended` events (from the row's `state`, `awaiting_user_input`, `background_work_in_progress`), then `process.ended(reason="lost", precision="estimated")`. An unmatched end is acceptable: a process that started before the rollout, or whose `process.started` write was lost (section 6), yields a `lost` end with no begin. Pairing is a display concern.
2. `occurred_at` is the **last evidence of life**: `max(ProcessRun.last_state_change_at, Session.last_updated_at, last SessionItem timestamp)`. Never the boot time, which would invent hours of activity. The boot time goes in `data.upper_bound`.
3. Find the latest `backend.started` with no later `backend.ended`. If there is one, the previous instance did not stop cleanly: emit `backend.ended(clean=false, precision="estimated")` at the greatest `occurred_at` among the non-backfill rows recorded after that `backend.started` (every step-1 row excluded, `lost` and inner `*.ended` alike, since their times are estimates; equivalently, `precision="estimated"` rows are ignored), or at the `backend.started` time when there is none. This attribution relies on the **single-instance guarantee per data dir** (`instance_lock.py`): events carry no instance id, because at most one instance writes at a time.
4. Emit `backend.started` for the current instance, with a `boot_id` when the platform provides one (Linux: `/proc/sys/kernel/random/boot_id`). `boot_id` is nullable. A different `boot_id` than the previous `backend.started` sets `machine_restarted=true`; same sets `false`; if either is missing (macOS, first run) it is `null` (unknown), never `false`.
5. If a provider then restores the session (Claude Code cron restart), the normal path emits `process.started(cause="cron_restart")`. The lost interval stays readable.

`ProcessRun` rows that are already `DEAD` at boot (kept for crons) emit nothing: their end was recorded when they died.

## 8. Backfill of existing data

Goals: give every existing session a believable starting point; never invent facts.

- **Mechanism.** A background job in the style of `compute_version` (idempotent, batched, resumable), not a heavy schema migration. The migration creates the empty table only. Backfill logic is written inline in the job and does not import application models' helper code into a migration.
- **Idempotency.** Every backfilled row carries `dedupe_key = "backfill:<kind>[:<detail>]"`. A second run inserts nothing.
- **Marker.** Backfilled rows have `recorded_at` far after `occurred_at`; `precision` says how much to trust them.

| Source | Row | `precision` |
|---|---|---|
| `Session.created_at` | `session.created` (`origin="sync"`); skipped when `created_at` is null | `derived` |
| `archived`, `hidden`, `mute_on_user_turn` true | `<flag>.started` at `Session.last_updated_at` | `estimated` |
| `pinned` not null | `pinned.started` (`scope` from the value) at `last_updated_at` | `estimated` |

`last_updated_at` is nullable. When it is null, the flag rows fall back to `created_at`; when both are null, the row is skipped.

What the backfill deliberately does **not** do:

- **No `process.started` and no `process.ended` at all.** `Session.last_started_at` and `last_stopped_at` cannot prove that TwiCC ran a process. Both are recomputed from the JSONL file by the full compute (`last_stopped_at` becomes the file mtime), and they are also stamped for subagents. Using them would invent a TwiCC process for an external session (D5) and give a file mtime the precision of a process end. Process history therefore starts at the rollout.
- **No historical turns, awaiting-input or background-work intervals.** `SessionHistoryFact` does not contain them: `PROCESS_START` is a process spawned *by a tool call*, not an agent process, and `TURN_START` exists for Codex only. Rebuilding turns from `SessionItem` is possible but lossy, and is left for a later decision.
- **No exact flag dates.** They were never stored.

## 9. Consequences and limits

- **No cascade.** `session_id` is not a FK. Rows survive a session purge. Sessions are never deleted by policy, so this is acceptable; a purge path, if one appears, must delete the timeline rows explicitly.
- **Volume.** About two `turn.*` rows per assistant turn plus occasional others; SQLite handles it. A retention policy for `turn.*` is a later option, not a v1 need.
- **Estimated times are bounded, not exact.** A `lost` end can be earlier than the true crash by the time between the last visible activity and the crash.
- **External sessions** (terminal `claude`, `codex`) appear in the timeline only through `session.created` and the user's flag changes. TwiCC sees only their JSONL files.
- **Duplicates between live and backfill.** Live recording starts at the deploy; the batched backfill may reach a session long after. One general rule: the backfill skips `session.created` when the session already has a live `session.created`, and skips a flag's `<flag>.started` when the session already has any live row (`dedupe_key` not starting with `backfill:`) for that flag subject, since the live rows are the truth. For each row, the check and the insert run together under `run_under_db_write_lock`, the same lock every live write takes (section 6), so no live row can slip in between.
- **No process history before the rollout.** Consequence of section 8: the timeline of an existing session shows its creation and its flag states, and its process events only from the deploy onward.

## 10. Open points for plan time

1. **Actor and `via` plumbing.** Confirm how each service learns whether the call came from UI, CLI, MCP or an agent, and add the parameter where missing.
2. **Project resolution for the first process event** of a brand-new session whose `Session` row does not exist yet (section 6).
3. **Pairing rule at display time** for open intervals whose end is unknown (out of scope here, but the data model must not block it).
4. **Whether `pinned.changed` is worth keeping** or whether a scope change should be `pinned.ended` + `pinned.started`.
5. **Tier 2 selection and order.**
