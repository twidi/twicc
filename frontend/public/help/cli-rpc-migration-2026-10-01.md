---
title: "CLI / RPC migration of October 1, 2026"
---

On **October 1, 2026**, three changes to the `twicc` CLI and the `/rpc/` API
take effect together. Scripts and integrations that read their output must be
updated. This page lists every change and its replacement, so that you, or an
agent you point at this page, can update a script.

**The switch happens at local midnight** (the night of September 30 to
October 1), in the process that runs the command: the `twicc` process itself
on the terminal (a script run with `TZ=UTC` switches at UTC midnight), the
TwiCC backend for `/rpc/` and the MCP tools. With `--remote`, the clock and
the version of the remote instance decide.

**You can migrate today.** Every new behaviour is available now through a
flag, and each flag keeps working after the date. A script that passes them
behaves the same on both sides of the switch.

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

Until the date, an affected call still returns what it always did, plus one
notice line per change it is affected by (a bare `sessions` call gets two):

- on the terminal, on **stderr**, starting with `twicc:`;
- over `/rpc/`, in a **`warnings`** key of the response envelope.

Run your scripts once and look for these notices. They name the command, the
date, and what to pass instead. A call that prints none is not affected.

## 1. Listings return a page envelope

The commands that return a list stop returning a bare JSON array. They return
an object, and they page at **50** by default.

| Command | Default page today | From October 1 |
|---|---|---|
| `projects`, `workspaces`, `sessions`, `artifacts` | 20 | 50 |
| `session <id> agents`, `session <id> workflows` | 20 | 50 |
| `search` | 20 | 50 |
| `share` | 50 | 50 |
| `session <id> content`, `session <id> messages` | **everything** | **50** |

Before:

```json
[ {"id": "…"}, {"id": "…"} ]
```

From October 1:

```json
{
  "items": [ {"id": "…"}, {"id": "…"} ],
  "pagination": {"limit": 50, "offset": 0, "total": 134, "has_more": true}
}
```

- **Migrate now:** pass `--paginated`. You get the envelope and the 50-item
  page today. From October 1 the flag is accepted and does nothing.
- **Read the items:** `jq '.[]'` becomes `jq '.items[]'`.
- **Read everything:** loop on `--offset` while `pagination.has_more` is
  `true`, or pass an explicit `--limit`.
- **`session content` and `session messages`** returned every item when no
  `--limit` was given. They now return 50. Pass `--limit` (or `--tail N`) if
  you read them whole. `session <id> content` with no selector used to be
  refused; it now returns the first page.
- **`search`** already returned an object. It renames `hits` to `items` and
  `total_hits` to `pagination.total`, and moves `limit` / `offset` under
  `pagination`. The other keys, `query` included, stay at the top level.
- **Unchanged:** the batch lookups keep returning a bare array, one entry per
  id you named — `sessions get`, `projects get`, `workspaces get`.

## 2. Session listings return a reduced projection

The commands that return **several sessions** stop returning every field of
each session. They return a reduced projection, about 60% lighter. The
commands that return **one** session (`session <id>`, `whoami`) are
unchanged.

| Command | From October 1 |
|---|---|
| `sessions` | reduced projection |
| `sessions get <ids>` | reduced projection, placeholders included |
| `session <id> agents` | reduced projection |
| `topology` | sessions already reduced; the `process` block of each node becomes `{"state": …}` |

The reduced projection keeps: `id`, `project_id`, `provider`, `title`,
`annotations`, `parent_session_id`, `spawned_by`, `spawn_root`, `created_at`,
`last_new_content_at`, `context_usage`, `context_max`, `total_cost`,
`user_message_count`, `model`, `git_branch`, `archived`, `hidden`, `pinned`,
`stale`, `unavailable_reason`, `mute_on_user_turn`, `has_artifacts`,
`has_plan`, `has_workflows`, `has_tasks`, `has_goals`, and a `process` block
reduced to `{"state": …}` (`null` on a subagent row, which has no process of
its own).

It drops: `last_line`, `mtime`, `last_started_at`, `last_updated_at`,
`last_stopped_at`, `last_viewed_at`, `slug`, `compute_version_up_to_date`,
`self_cost`, `subagents_cost`, `cwd`, `git_directory`, the agent settings
(`permission_mode`, `selected_model`, `effort`, `thinking_enabled`,
`claude_in_chrome`, `fast_mode`), `compacted`, `layout`, `browser_url`,
`tasks`, `plan_paths`, `goals`, `hybrid`, `artifacts_dir`, and the `id`,
`started_at`, `last_state_change_at`, `pid` of the `process` block.

- **Keep the full payload:** pass `--full`. It works today and stays after the
  date.
- **Get the new shape today:** pass `--slim`. From October 1 it is the default
  and the flag does nothing.
- `--slim` and `--full` together exit `2`.
- **`topology`:** `--full` gives every node its full session and its
  five-field `process` block. `--full-sessions` still works, as an alias of
  `--full`.
- **One session in detail:** `twicc session <id>` still returns everything.

## 3. `process` and `processes` stop working

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
  reports `"process": {"state": "dead", …}`. Test `process.state`, not the
  exit code.
- A session spawned seconds ago may not be in the database yet (no session
  row), or may have a row with no user message yet. `session <id>` exits `1`
  on both, and `sessions --active` does not list them. Use
  `sessions get <id>`: with no row it returns a `known: false` placeholder
  that still carries the live `process` block.
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
    on `sessions` and `sessions get`; `session <id>` always carries them)
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

- **A bare `sessions stop` stops every running session, the caller
  included.** `processes stop` refused a bare call. Never run it with an
  empty id list by accident.
- It accepts `--spawned-by parent`, `--descendants parent`, `--spawn-tree`
  and `--siblings`, which `processes stop` refused, and `--annotation`
  without a filiation scope. Pair `--annotation` with `--spawned-by` or
  `--descendants`. It also takes `--project`, `--workspace`, `--provider`
  and `--state`.
- Filters select only **running** sessions: the result has one entry per
  running session, not one per session in the scope (named ids are always
  reported). `processes stop --descendants X` listed every session of the
  subtree.
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

Replacements:

- `processes wait <ids> user_turn dead --timeout N`, on sessions spawned for
  this wait → `sessions wait-reply <ids> --since 2000-01-01 --wait-timeout N`
  (see "The cursor" below before reusing it on older sessions).
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

- **The cursor.** Without `--since` (or `--from`), each session starts above
  its current last line: an answer written before the wait started is
  missed, and that session reports `ended` (or `timeout` if its turn is
  still running). For sessions spawned for this wait and not messaged since,
  pass **`--since 2000-01-01`**. On a session that already had turns, that
  instant returns its **first** answer ever: pass an instant just before the
  message you are waiting on, or `--from` a line number. Never pass today's
  date: a bare date means midnight UTC, and ahead of UTC it can be in the
  future. To resume one session, pass `--from <since_line_num>` with the
  cursor a previous wait returned.
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
  call with the same `--since`, naming only the ids still to wait on: those
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

1. Replace every `twicc process …` / `twicc processes …` call (section 3).
2. For each listing, pass `--paginated` now **and** read `.items`, looping on
   `pagination.has_more` or passing an explicit `--limit` (section 1).
3. For `session content` / `session messages`, also pass `--limit` if you
   need more than 50 items.
4. For `search`, pass `--paginated` and read `items` and `pagination.total`
   instead of `hits` and `total_hits`.
5. For `sessions`, `sessions get`, `session <id> agents` and `topology`, pass
   `--full` if you read a field the reduced projection drops, or the `pid`
   and timestamps of the `process` block; otherwise pass `--slim` (section 2).
6. Where a script read `process` / `processes` output, map the keys (section
   3, "Reading state"), and test `outcome` instead of the exit code of a
   wait.
7. Run the script before the date. No notice must remain: no stderr line
   starting with `twicc: from` or `` twicc: `twicc ``, and no `warnings` key
   in `/rpc/` responses.
