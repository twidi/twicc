---
title: "CLI / RPC migration of October 1, 2026"
---

On **October 1, 2026**, these changes to the `twicc` CLI and the `/rpc/` API
take effect together. Scripts and integrations that read their output must be
updated. This page lists every change and its replacement, so that you, or an
agent you point at this page, can update a script.

**The switch happens at local midnight** (the night of September 30 to
October 1), in the process that runs the command: the `twicc` process itself
on the terminal (a script run with `TZ=UTC` switches at UTC midnight), the
TwiCC backend for `/rpc/` and the MCP tools. With `--remote`, the clock and
the version of the remote instance decide.

**You can migrate today.** Every dated change is available now through a
flag, and each flag keeps working after the date. A script that passes them
behaves the same on both sides of the switch. A few changes apply now,
without a flag: see "Changed now, without a notice".

**Agents on the MCP tools need nothing.** They read the new parameters and
descriptions from the tool schemas and adapt on their own. The behaviour
switches at midnight; the tool list and descriptions change at the first
TwiCC restart after it. Until that restart, the `process` / `processes`
tools stay listed and answer with the removal error.

**To have an agent update your scripts,** give it this page:
<https://github.com/twidi/twicc/blob/main/frontend/public/help/cli-rpc-migration-2026-10-01.md>.
It holds everything needed, and ends with a checklist to apply to each
script.

## How to find what is affected

Until the date, an affected call still returns what it always did — except
the changes listed in "Changed now, without a notice" (the `share` page size,
the `workflows` trace, `artifacts_dir`, the effective agent settings, the new
keys) — plus one notice
line per change it is affected by (a flagless `sessions`, `sessions get` or
`session agents` call gets two):

- on the terminal, on **stderr**, starting with `twicc:`;
- over `/rpc/`, in a **`warnings`** key of the response envelope.

Run your scripts once and look for these notices. They name the command, the
date, and what to pass instead. A call that prints none is not affected by a
dated change; the changes of "Changed now, without a notice" print no notice.

## 1. Listings return a page envelope

The commands that return a list stop returning a bare JSON array. They return
an object. Every listing pages at **20** by default.

| Command | Default page today | From October 1 |
|---|---|---|
| `projects`, `workspaces`, `sessions`, `artifacts` | 20 | 20 |
| `session <id> agents`, `session <id> workflows` | 20 | 20 |
| `search` | 20 | 20 |
| `share` | 20 (it was 50: changed now) | 20 |
| `session <id> content`, `session <id> messages` | **everything** | **20** |

Before:

```json
[ {"id": "…"}, {"id": "…"} ]
```

From October 1:

```json
{
  "items": [ {"id": "…"}, {"id": "…"} ],
  "pagination": {"limit": 20, "offset": 0, "total": 134, "has_more": true}
}
```

- **Migrate now:** pass `--paginated`. You get the envelope and the 20-item
  page today. From October 1 the flag is accepted and does nothing.
- **Read the items:** `jq '.[]'` becomes `jq '.items[]'`.
- **Read everything:** loop on `--offset` while `pagination.has_more` is
  `true`, or pass an explicit `--limit`.
- **`session content` and `session messages`** returned every item when no
  `--limit` was given. They now return 20. Pass `--limit` (or `--tail N`) if
  you read them whole. `session <id> content` with no selector used to be
  refused; it now returns the first page.
- **`search`** already returned an object. It renames `hits` to `items` and
  `total_hits` to `pagination.total`, and moves `limit` / `offset` under
  `pagination`. The other keys, `query` included, stay at the top level.
- **The batch lookups and `peers`** return `items` too, without
  `pagination`: see section 3.

## 2. Session commands return a reduced projection

The commands that return sessions stop returning every field of each
session. They return a reduced projection: every field except the verbose
ones and the ones of no use to a caller.

| Command | From October 1 |
|---|---|
| `sessions` | reduced projection |
| `sessions get <ids>` | reduced projection, placeholders included |
| `session <id> agents` | reduced projection |
| `session <id>` | reduced projection |
| `whoami` | the `session self` payload, reduced (see below) |
| `topology` | sessions already reduced; the `process` block of each node becomes `{"state": …}` |

The reduced projection keeps: `id`, `project_id`, `provider`, `title`,
`annotations`, `parent_session_id`, `spawned_by`, `spawn_root`, `created_at`,
`last_new_content_at`, `context_usage`, `context_max`, `total_cost`,
`user_message_count`, `model`, `git_branch`, `archived`, `hidden`, `pinned`,
`stale`, `unavailable_reason`, `mute_on_user_turn`, `has_artifacts`,
`has_plan`, `has_workflows`, `has_tasks`, `has_goals`, `last_line`, `cwd`,
`git_directory`, `project_directory`, `artifacts_dir`, `scratch_dir`,
`orchestration_scratch_dir`, `compacted`, `hybrid`, the agent settings
(`permission_mode`, `selected_model`, `effort`, `thinking_enabled`,
`claude_in_chrome`, `fast_mode`, `question_widget`), and a `process` block
reduced to `{"state": …}` (`null` on a subagent row, which has no process of
its own).

It drops: `mtime`, `last_started_at`, `last_updated_at`, `last_stopped_at`,
`last_viewed_at`, `slug`, `compute_version_up_to_date`, `self_cost`,
`subagents_cost`, `layout`, `browser_url`, `tasks`, `plan_paths`, `goals`,
and the `id`, `started_at`, `last_state_change_at`, `pid` of the `process`
block.

- **Keep the full payload:** pass `--full`. It works today and stays after the
  date.
- **Get the new shape today:** pass `--slim`. From October 1 it is the default
  and the flag does nothing.
- `--slim` and `--full` together exit `2`.
- **`topology`:** `--full` gives every node its full session and its
  five-field `process` block. `--full-sessions` still works, as an alias of
  `--full`.
- **One session:** `twicc session <id>` takes `--slim` and `--full` too,
  before or after the id (`session <id> --full` or `session --full <id>`),
  never before a subcommand (`session <id> --full agents` exits `2`; write
  `session <id> agents --full`). It also accepts `self` (your own session)
  and `parent` (the session that spawned you).
- **`whoami`** takes `--slim` and `--full` today. With a flag, and from
  October 1 without one, it returns exactly what `session self` returns with
  the same flag: the session row with its `process` block inside. Until then
  a flagless `whoami` keeps its current object. **Pass `--slim` or `--full`
  now**, so a script reads the same keys on both sides of the date. The
  removed keys are read from:

  - `session_id` → `id`
  - `title`, `project_id`, `project_directory`, `artifacts_dir`,
    `scratch_dir` → same names
  - `orchestration_scratch_dir` → same name, `null` instead of absent outside
    an orchestration
  - `current_working_directory` → `git_directory`
  - `agent_settings.<field>` → `<field>` (effective values)
  - `session.<field>` → `<field>` (with `--full` for the fields the reduced
    projection drops)
  - `process` (nine fields) → `process`: `{"state": …}`, or five fields
    (`id`, `state`, `started_at`, `last_state_change_at`, `pid`) with
    `--full`; `provider`, `session_id`, `session_title`, `project_id` are the
    row's own `provider`, `id`, `title`, `project_id`

  So `whoami | jq .process.pid` needs `--full`.

## 3. Batch lookups and `peers` return `items`

The commands that read several objects by id, and `peers`, return their
entries under `items`, like the listings — but with **no `pagination`**: a
lookup returns exactly one entry per id you named, `peers` every approved
peer.

| Command | Today | From October 1 |
|---|---|---|
| `sessions get <ids>` | `[ {…}, … ]` | `{"items": [ {…}, … ]}` |
| `projects get <ids>` | `[ … ]` | `{"items": [ … ]}` |
| `workspaces get <ids>` | `[ … ]` | `{"items": [ … ]}` |
| `peers` | `{"peers": [ … ]}` | `{"items": [ … ]}` |

- **Migrate now:** pass `--paginated`. You get `{"items": […]}` today. From
  October 1 the flag is accepted and does nothing.
- **Read the entries:** `jq '.[]'` becomes `jq '.items[]'`; for `peers`,
  `jq '.peers[]'` becomes `jq '.items[]'`.

## 4. `process` and `processes` stop working

The seven commands below are deprecated until the date. From October 1 they
**exit `64`** with an error naming their replacement. Over `/rpc/`, their
routes (`process`, `process/*`, `processes`, `processes/*`) answer
`{"exit_code": 64, "result": null, "error": "…"}`.

| Retired | Replacement |
|---|---|
| `processes [filters]` | `sessions --active [filters]` (or `--state …`) |
| `processes get <ids>` | `sessions get <ids>` |
| `processes stop …` | `sessions stop …` |
| `processes wait …` | `sessions wait-reply …` |
| `process <id>` | `session <id>` |
| `process <id> stop` | `session <id> stop` |
| `process <id> wait …` | `session <id> wait-reply` |

The live state of a session is in the `process` block of its session row:
`process.state` is `starting`, `assistant_turn`, `awaiting_user_input`,
`user_turn` or `dead`. The block is `null` on a subagent, which runs inside
its parent's process.

### Reading state

- `process <id>` exited `1` when nothing ran. `session <id>` exits `0` and
  reports `"process": {"state": "dead"}` (from October 1 without `--full`;
  `--full` adds `id`, the timestamps and `pid`). Test `process.state`, not
  the exit code.
- A session spawned seconds ago may not be in the database yet (no session
  row), or may have a row with no user message yet. `session <id>` exits `1`
  when there is no row (a row with no user message is returned), and
  `sessions --active` lists neither. Use `sessions get <id>`: with no row it
  returns a `known: false` placeholder that still carries the live `process`
  block.
- **The keys change.** `processes`, `processes get` and `process <id>`
  returned a process row. The session commands return a session row with a
  `process` block:

  - `session_id` → `id`
  - `session_title` → `title`
  - `provider`, `project_id` → unchanged, top level (`null` on a
    `known: false` placeholder)
  - `state` → `process.state`
  - `id` (the process run id) → `process.id`
  - `started_at`, `last_state_change_at`, `pid` → the same names under
    `process` (from October 1, `process.id` and these three need `--full`
    on `sessions`, `sessions get` and `session <id>`)
  - `session_known` (`processes get`) → `known` (`sessions get`)

  **The top-level `id` changes meaning:** it was the process run id, it is
  now the session id. **`known` is narrower than `session_known`:** it is
  `true` only when a session row exists, where `session_known` was also
  `true` for a session that had only a process run. On a subagent,
  `process` is `null` instead of a `dead` block.

### Stopping

`sessions stop` and `session <id> stop` take the same `--force` (immediate
SIGKILL) and `--timeout` as before. `sessions stop` selects more widely than
`processes stop`:

- **A bare `sessions stop` is refused** (exit `1`), as `processes stop`
  refused one: pass at least one id or one filter. "Stop everything running"
  is written on purpose: `sessions stop --state starting --state
  assistant_turn --state awaiting_user_input --state user_turn`.
- **`sessions stop` never stops the caller.** Named (`self` included) or
  selected by a filter, the calling session is reported with the status
  `skipped_self`; `session self stop` stops it.
- It accepts `--spawned-by parent`, `--descendants parent`, `--spawn-tree`
  and `--siblings`, which `processes stop` refused, and `--annotation`
  without a filiation scope. Pair `--annotation` with `--spawned-by` or
  `--descendants`. It also takes `--project`, `--workspace`, `--provider`
  and `--state`.
- Filters select only **running** sessions: `results` has one entry per
  running session, not one per session in the scope (named ids are always
  reported). `processes stop --descendants X` listed every session of the
  subtree.
- **The output is `{summary, results}`**, where `processes stop` returned an
  array: `summary` holds `total`, `succeeded` (`status: stopped`), `failed`
  (every other status) and `all_succeeded`; `results` is an object keyed by
  session id, each value the entry `processes stop` gave for that id.
- Named ids are **added** to what the filters select:
  `sessions stop <MANAGER_ID> --descendants <MANAGER_ID>` stops a manager and
  its subtree.

### Waiting

`process wait` and `processes wait` waited for a **state**. There is no
replacement for that: waiting on `starting`, `assistant_turn`, `user_turn` or
`dead`, and `--transition`, are gone. `session <id> wait-reply` and
`sessions wait-reply` wait for an **answer**: a message that closes a turn,
or a pending request (a tool approval or a question) that only a human can
clear.

**The simplest form is `--wait-reply` on the command that sends**
(`create-session`, `send-message`, `send-messages`): it reads its cursor
server-side and needs nothing from you. **A send without `--wait-reply` is
never followed by a wait without a cursor:** after `send-message`, pass
`session <id> wait-reply --from <last_line>` the `last_line` the send
returned; after `send-messages`, pass `sessions wait-reply --since` an
instant taken before the send, or one `session <id> wait-reply --from
<last_line>` per id. `--from` and `--since` exist only on the two wait
commands (`sessions wait-reply` takes `--since` only), not on
`create-session`, `send-message` or `send-messages`.

Replacements:

- `processes wait <ids> user_turn dead --timeout N`, on sessions spawned for
  this wait → `sessions wait-reply <ids> --wait-timeout N`.
- `processes wait … --first` → `sessions wait-reply … --wait-first`.
- `process <id> wait user_turn` right after `create-session` →
  `create-session --wait-reply`.
- `process <id> wait user_turn --transition` after `send-message` →
  `send-message --wait-reply`. When the wait must be a separate step:
  `session <id> wait-reply --from <last_line>`, with the `last_line` the
  `send-message` result returned.
- `processes wait … --transition` after `send-messages` →
  `send-messages --wait-reply`.

Details:

- **The cursor.** Without `--since` (or `--from`), each session starts
  **after its last user message**, so an answer written before the wait
  started is returned. While a session's compute is not current — e.g.
  right after a TwiCC restart, until the background compute reaches it —
  it starts at its current last line instead: an answer written before the
  wait is then missed (`ended`), so pass `--since` an instant before the
  spawn or the send. After a send without `--wait-reply`, pass `--since` an
  instant taken before the send, or `--from` the `last_line` the send
  returned. `--since` is an ISO 8601 instant (`2026-09-30T14:05:00+00:00`;
  no offset means UTC; a bare date means its midnight UTC). To resume one
  session, pass `--from <since_line_num>` with the cursor a previous wait
  returned.
- **The result.** `sessions wait-reply` returns `summary` and `results`,
  an **object keyed by session id** (the old `processes wait` returned an
  array). Each value is a reply block: `outcome`, `line_num`, `is_final`,
  `since_line_num`, `waited_seconds`, `text` (only when a message was
  found), `error` (on `wait_failed`). `outcome` is `replied`,
  `awaiting_user_input`, `ended`, `provider_error`, `timeout`,
  `backend_gone`, `wait_failed`, `pending` (with `--wait-first` only) or
  `unknown_session` (then the block holds only `outcome` and `session_id`).
  Read `outcome` where the old command gave `wait_status` /
  `matched_state`.
- **Exit codes.**
  - `sessions wait-reply` exits `0` whatever the outcomes (`1` on a refused
    call). Branch on `summary.all_replied` and on each `outcome`.
  - `session <id> wait-reply` exits `0` on `replied` or
    `awaiting_user_input`, `5` on `timeout`, `ended` or `provider_error`, `2`
    when TwiCC stopped, `1` on a refused call or `wait_failed`.
  - Both also exit `2` on a usage error, such as an unknown option: read
    `reply.outcome` to tell it from "TwiCC stopped".
  - `create-session --wait-reply`, `send-message --wait-reply` and
    `send-messages --wait-reply` exit `0` once the session is created or the
    message sent, **whatever the wait**. Branch on `reply.outcome` (per
    recipient for `send-messages`).
  - The old `process wait` and `processes wait` exited `5` on timeout: a
    script that tests `$?` must test `outcome` instead.
- **`--wait-first`** also stops on `awaiting_user_input`. In a race, check
  `outcome == "replied"` before you call a winner.
- **Longer than 300 s.** Keep `--wait-timeout` at 300 or less and repeat the
  call. Re-running resumes, except for a session whose compute is not
  current; `--since <the instant the batch started>` resumes in every case.
  Name only the ids still to wait on: those
  whose `outcome` is `timeout` or `wait_failed`, and those on `backend_gone`
  once TwiCC is back. `ended`, `provider_error` and `unknown_session` are
  final: the same call returns them again (`ended` after its ~5 s flush
  window). Never loop until `summary.concluded` equals `summary.total`:
  `concluded` counts only `replied` and `awaiting_user_input`.
- **Just spawned.** A named id whose process runs but which is not in the
  database yet (no session row) is waited on from its first line. Filters
  (`--spawned-by`, `--annotation`) only see sessions that have a row, so
  name the ids you just spawned. Named ids are added to what the filters
  select, never narrowed by them: to wait on a subset, name only that
  subset.
- **Known limit (Claude Code), rare and accepted.** A message sent while a
  Claude session is busy is queued by Claude and recorded as a queued
  command, not as a user message. Every wait for an answer — `--wait-reply`
  included, with a cursor or the default — returns the first final message
  past its cursor, which is then the running turn's closing message.
  Usually that turn read the queued message and its closing message covers
  it; in the rare case where the queued message runs as a turn of its own
  afterwards, the wait returns an answer that does not cover it. Message a
  session once it has finished, not while it works.

## Changed now, without a notice

Nothing released breaks before October 1: a change that only adds (a new
key, a new flag, a new keyword, an error that becomes an answer) applies now,
and everything else waits for the date and is announced by a notice. Four
changes of released values are exceptions and apply now, without a notice:

- **`share` pages at 20** by default (it was 50). Pass `--limit 50` to keep
  the old page.
- **`session <id> workflows` leaves out each run's execution trace**
  (`workflowProgress`, `script`, `logs`, `args`) and its `result`. Pass
  `--result` to get the `result` back, or `--full` for each run's envelope
  verbatim; `session <id> workflow <run_id>` still returns one run in full.
- **`artifacts_dir` is always the session's folder path** on `sessions`,
  `sessions get`, `session <id>` and `session <id> agents` (it was `null`
  until an artifact existed). Over `/rpc/` and MCP `has_artifacts` says
  whether the folder holds anything; from a terminal `has_artifacts` is
  always `false`, so check the folder itself. A `sessions get` placeholder
  keeps `artifacts_dir: null`.
- **The agent settings are effective values** on the same commands: the
  stored value, else the current default (`question_widget` `true` when not
  chosen), where they were the stored value, often `null`. A subagent row
  keeps its stored values, mostly `null`: it runs inside its parent's
  process.

Additive changes, applied now:

- The keys `project_directory`, `scratch_dir`, `orchestration_scratch_dir`
  and `question_widget` on `sessions`, `sessions get` (`null` on a
  placeholder), `session <id>`, `session <id> agents` and `whoami` with a
  flag — not on `topology` nodes, nor in the `session` sub-object of a
  flagless `whoami`.
- `session self` and `session parent` (and their subcommands, e.g.
  `session self stop`).
- `session <id>` answers for a row with no user message yet (it exited `1`).
- `--slim` / `--full` on `session <id>` and `whoami`; `--paginated` on
  `sessions get`, `projects get`, `workspaces get` and `peers`.

## `/rpc/` specifics

- The response envelope loses its `warnings` key: nothing is left to
  announce.
- The long-polls are `session/wait-reply` and `sessions/wait-reply`, and the
  `--wait-reply` form of `create-session`, `send-message` and
  `send-messages`. They hold the response for up to their `--wait-timeout`
  (300 s by default): size your client read timeout above it, and mind the
  idle limits of any proxy on the way.
- The `argv` form (`{"argv": […]}`) runs the same checks and returns the
  same exit codes as the terminal. Its notices arrive in `warnings`, not on
  stderr.

## Checklist for a script

1. Replace every `twicc process …` / `twicc processes …` call (section 4).
2. For each listing, pass `--paginated` now **and** read `.items`, looping on
   `pagination.has_more` or passing an explicit `--limit` (section 1).
3. For `session content` / `session messages`, also pass `--limit` if you
   need more than 20 items.
4. For `search`, pass `--paginated` and read `items` and `pagination.total`
   instead of `hits` and `total_hits`.
5. For `sessions`, `sessions get`, `session <id>`, `session <id> agents` and
   `topology`, pass `--full` if you read a field the reduced projection
   drops, or the `pid` and timestamps of the `process` block; otherwise pass
   `--slim` (section 2).
6. For `sessions get`, `projects get`, `workspaces get` and `peers`, pass
   `--paginated` and read `.items` (section 3). For `whoami`, pass `--slim`
   or `--full` and read the new keys (`id`, `git_directory`, the agent
   settings at the top level; section 2).
7. Where a script read `process` / `processes` output, map the keys (section
   4, "Reading state"), and test `outcome` instead of the exit code of a
   wait.
8. Run the script before the date. No notice must remain: no stderr line
   starting with `twicc: from` or `` twicc: `twicc ``, and no `warnings` key
   in `/rpc/` responses.
