# Retiring the `process` / `processes` commands

**Status:** implemented
**Date:** 2026-09-23
**Cutover instant:** `LISTING_CUTOVER` — `2026-10-01T00:00:00`, local time on the machine running TwiCC, shared with the pagination and slim-listing cutovers

Paths are relative to the repository root. Line numbers are those of the
tree at `2b424f87`, which carries the slim-listing change
(`docs/plans/2026-09-23-session-listing-slim-cutover-design.md`). This design depends on that change: it reuses its constant,
its notice recorder (`_record_notice`) and its help-text pattern
(`cutover_help`).

## Problem

The `process` and `processes` families predate the session commands that now
do the same jobs. Every session row carries a `process` block, `sessions`
filters on it (`--state`, `--active`), `sessions stop` / `session <id> stop`
stop agents, and `sessions wait-reply` / `session <id> wait-reply` wait for
them. Two families for one job cost tokens in every agent's tool list and
teach agents two vocabularies.

## Decisions

| Point | Decision |
|---|---|
| Commands retired | the seven: `processes`, `processes get`, `processes stop`, `processes wait`, `process <id>`, `process <id> stop`, `process <id> wait` |
| Before the cutover | each works as today, plus a notice naming its replacement |
| After the cutover | each **refuses to run**: exit `64`, an error naming the replacement |
| Code | stays; deleting it is a later cleanup, like the pre-cutover paths of the two other migrations |
| Trigger | `LISTING_CUTOVER`, the same constant, the same instant |
| MCP before the cutover | never notified at run time, like the two other migrations; the tool descriptions carry the notice |
| MCP after the cutover | the seven tools leave the tool list; a single call to one of their names still gets the removal message (a `batch` / `batch_read` child gets the generic `unknown_tool` diagnostic) |
| RPC after the cutover | the seven routes stay and answer with the error |
| `twicc --help` after the cutover | the two groups are hidden |
| Lifecycle waits | **dropped, not replaced** (see below) |
| Session family | three gaps closed first (see "Prerequisites") |

### What replaces what

| Retired | Replacement | Equivalent? |
|---|---|---|
| `processes [filters]` | `sessions --active [filters]` (or `--state …`) | **partly**: same filters, but a session the watcher has not indexed yet (no `Session` row, or no user message) is not listed (`require_indexed`, `src/twicc/cli/sessions.py:48-54`); from the cutover the row's `process` block is `{state}` unless `--full` |
| `processes get <ids>` | `sessions get <ids>` | **yes**, with three differences: `known` means "a `Session` row exists" (a row with no user message yet is `known: true`), where `processes get`'s `session_known` meant "a `Session` row **or** any `ProcessRun` row exists" (`src/twicc/cli/processes_get.py:16-21`); the process fields are nested under `process.*` instead of flat on the entry; and from the cutover the block is `{state}` unless `--full` |
| `process <id>` | `session <id>` for an indexed session, `sessions get <id>` otherwise | **partly**: `session <id>` exits `1` on a session not indexed yet (`src/twicc/cli/session.py:14-27`); `process <id>` exits `1` when nothing runs (`src/twicc/cli/process.py:38-42`), where `session <id>` exits `0` with `process.state: "dead"` |
| `processes stop [ids] [filters] [--force] [--timeout]` | `sessions stop [ids] [filters] [--force] [--timeout]` | **partly**: same batch code (`src/twicc/cli/_stop_batch.py`), but different guardrails — see below |
| `process <id> stop [--force] [--timeout]` | `session <id> stop [--force] [--timeout]` | **yes**: `session stop` already calls `process_stop.stop_cmd` (`src/twicc/cli/__init__.py:823-825`) |
| `processes wait <ids/states>` | `sessions wait-reply` | **no**: waits for an answer, not a state |
| `process <id> wait <states>` | `session <id> wait-reply` | **no**: same |

`--force` (SIGKILL without the grace window) exists on both stop commands of
the session family, so the hard stop is covered.

**`sessions stop` guardrails differ from `processes stop`.** No code change:
`sessions stop` is designed to be broad, and the skills carry the guardrails
in prose (see "Rewrite rules").

- A bare `processes stop` exits `1` (`src/twicc/cli/processes_stop.py:88-94`);
  a bare `sessions stop` stops every running session, the caller included
  (`src/twicc/cli/sessions_stop.py:99-110`).
- `processes stop` refuses `parent`, `--spawn-tree` and `--siblings`;
  `sessions stop` accepts them, so `sessions stop --spawn-tree self` stops the
  whole tree, the caller included.
- `processes stop` refuses `--annotation` without a filiation scope
  (`src/twicc/cli/processes_stop.py:80-85`); `sessions stop --annotation …`
  selects across every tree.
- Filter-selected ids are narrowed to live sessions, so the result has one
  row per running target, not one per selected session.
- Explicit ids are **unioned** with the filters
  (`src/twicc/cli/sessions_stop.py:87-123`,
  `tests/test_cli_sessions_stop.py:155-165`). Three texts say the opposite
  ("naming ids bypasses the filters") and are corrected (see "Prerequisites").

### Lifecycle waits are dropped

Waiting for a **state** (`starting`, `assistant_turn`, `user_turn`,
`awaiting_user_input`, `dead`) and `--transition` have no replacement. By
decision of the owner, only the answer-wait matters.

This reverses the direction of `docs/plans/2026-09-18-wait-commands-evolution.md`
("keep `process wait` / `processes wait` for lifecycle states"). That document
is a historical record and is not edited.

## Prerequisites: three gaps in the session family

The replacements must work before the retired commands stop. Three do not
today.

### P1 — waiting on a session that is not indexed yet

A session exists as a `ProcessRun` before the watcher writes its `Session`
row (`src/twicc/cli/sessions_get.py:16-18`). `process <id> wait` and
`processes wait <id>` handle it; the answer-waits do not:

- `session <id> wait-reply` exits `1` through `_get_session`
  (`src/twicc/cli/session.py:618`), which also refuses a row with no user
  message yet;
- `sessions wait-reply <id>` reports `outcome: unknown_session` and never
  waits (`src/twicc/cli/sessions_wait_reply.py:131-141`).

The wait loop already handles a session with no row
(`src/twicc/cli/_wait_reply.py:424-433`, `SESSION_ROW_GRACE_SECONDS`), and
`create-session --wait-reply` already waits on a brand-new session with cursor
`0`. So:

- **`sessions wait-reply`**: an explicit id with no `Session` row but a
  `ProcessRun` whose virtual state is not `dead` on this instance
  (`load_process_rows` + `project_virtual_state`,
  `src/twicc/cli/_process_state.py`) is waited on with cursor `0`. `--since`
  does not apply to it (no transcript to place an instant in); for an instant
  earlier than the spawn — what the rules below recommend — cursor `0` is
  what it would give anyway. (With watcher lag a later instant could land
  inside lines already written; `0` then re-reads them, which can only report
  an answer, never miss one.)
  An id with neither stays `unknown_session`.
- **`session <id> wait-reply`**: when `_get_session` would refuse the id, the
  same live-`ProcessRun` test decides. Live: wait with cursor `0` (or `--from`
  if given; `--since` gives `0` as above) and emit `{"session_id", "reply"}` as
  usual. Not live: the current `not found` error, exit `1`.

Filters (`--spawned-by self`, `--annotation` …) still select from `Session`
rows only: the spawn link and the annotations are written on that row
(held as pending attributes until the row exists, `src/twicc/pending_session_attributes.py:1-21`). A child
that is not written yet matches no filter. A caller who just spawned children
therefore names their ids (from each `create-session` result). Ids are
**unioned** with the filters, never narrowed by them: a barrier on a subset
(`--spawned-by self --annotation phase=audit`) names **only the ids of that
subset** — the ones spawned for that phase — or drops the filter and passes
exactly those ids.

The documentation of the two commands says today that `unknown_session` /
exit `1` means "does not exist". It is updated with P1 (see "Files"), including `twicc-session/SKILL.md:14`
(the singular's exit-`1` clause, "an unknown session").

### P2 — `--remote` cuts the answer-waits at 30 s

`_WAIT_PATHS` (`src/twicc/cli/_remote.py:623`) holds only `process/wait` and
`processes/wait`. `_request_timeout` therefore gives `session/wait-reply` and
`sessions/wait-reply` the default 30 s read timeout, and a default 300 s wait
over `--remote` is cut by the client.

Fix: a second set, `_WAIT_REPLY_PATHS = {"session/wait-reply",
"sessions/wait-reply"}`. For those paths the read timeout is the bound
`wait_timeout` param (default `_DEFAULT_WAIT_TIMEOUT`, 300 s, when missing or
not positive) plus `_WAIT_TIMEOUT_MARGIN`. `_WAIT_PATHS` keeps its two
entries until the code cleanup.

### P3 — the "bypasses the filters" texts

Explicit ids are unioned with the filters on `sessions stop`. Three texts say
they bypass them, and become "are added to the filters' selection (unioned)":

- the `session_ids` argument help of `_sessions_stop`
  (`src/twicc/cli/__init__.py`, "Naming ids bypasses the filters, as in
  `sessions get`");
- `SKILLS-AND-CLI.md:250` ("Naming ids bypasses the filters");
- `twicc-sessions/SKILL.md:44` (same sentence).

## Mechanism

### One table, one helper

In `src/twicc/cli/_output.py`, next to `slim_notice`:

```python
#: Retired command → its replacement. The single source for the wrappers,
#: the help texts and the MCP unknown-tool message.
RETIRED_COMMANDS = {
    "processes": "sessions --active",
    "processes get": "sessions get",
    "processes stop": "sessions stop",
    "processes wait": "sessions wait-reply",
    "process": "session <id>",
    "process stop": "session <id> stop",
    "process wait": "session <id> wait-reply",
}


def removed_command(command: str) -> None:
    """Announce a retired command before the cutover; refuse it after."""
```

One builder produces the refusal text for every channel (terminal, RPC view,
MCP pre-check), so the three never drift:

```python
def removal_message(command: str) -> str:
    """The error a retired command answers with past the cutover."""
    # f"Error: `twicc {command}` was removed on {when}. Use `twicc {replacement}` instead."
```

`removed_command` reads the replacement from `RETIRED_COMMANDS[command]`, then:

1. cutover passed → `emit_error(removal_message(command), code=64)`;
2. MCP call (`_capture.get() is not None and _in_mcp_call()`) → return;
3. `_record_notice(f"twicc: `twicc {command}` stops working on {when}. Use `twicc {replacement}` instead.")`.

`when` is `LISTING_CUTOVER.strftime("%Y-%m-%d")` at call time, as in
`slim_notice`. The error path runs on MCP too.

**Exit `64`** (bad CLI usage, as `twicc-topology/SKILL.md` documents it). Not
`1`: `process <id>` exits `1` for "no running process", so a script that
tests the code (`[ $? -eq 1 ]`) would read a retired command as "not
running"; `64` keeps the two apart. A bare `if twicc process $ID; then` treats
any non-zero code as false and cannot be protected; the error on stderr is
what tells that caller. Not `2`: "backend down" on most commands.

### Where it is called

**After the cutover**, the refusal must come before Click parses the
subcommand's own arguments: `processes get` with no id, `processes wait`
without `--timeout`, `process X wait` without a state would otherwise exit on
a Click usage error instead. Click runs a group callback **before** it parses
the subcommand, so the refusal goes in the two group callbacks, **before**
their `ctx.invoked_subcommand` early return, and covers every subcommand:

A second helper in `src/twicc/cli/_output.py`:

```python
def refuse_if_removed(group: str, subcommand: str | None) -> None:
    """Past the cutover, refuse ``group`` or ``group subcommand``; else do nothing."""
    if listing_cutover_passed():
        removed_command(group if subcommand is None else f"{group} {subcommand}")
```

| Group callback (`src/twicc/cli/__init__.py`) | First statement, before the early return (`:1479` / `:1727`) |
|---|---|
| `_processes_default` (`:1401`) | `refuse_if_removed("processes", ctx.invoked_subcommand)` |
| `_process_default` (`:1721`) | `refuse_if_removed("process", ctx.invoked_subcommand)` |

**Before the cutover**, the notice is recorded once, by whichever command
actually runs:

| Wrapper | Placement | `command` |
|---|---|---|
| `_processes_default` | after the early return (`:1479`) | `processes` |
| `_processes_get` (`:1517`) | first statement | `processes get` |
| `_processes_stop` (`:1542`) | first statement | `processes stop` |
| `_processes_wait` (`:1621`) | first statement | `processes wait` |
| `_process_default` | after `ctx.obj = session_id` and the early return (`:1726-1728`) | `process` |
| `process_stop` (`:1736`) | first statement | `process stop` |
| `process_wait` (`:1774`) | first statement | `process wait` |

Before the cutover, the callbacks' early `listing_cutover_passed()` test is
false and does nothing, so a subcommand records exactly one notice (its own).
After it, the callback refuses first and the leaves never run; their own
`removed_command` call is then unreachable, which is harmless.

On `processes`, the module body also records the pagination notice
(`src/twicc/cli/processes.py`), which promises a new shape "from 2026-10-01".
That promise is moot for a command that stops on that date. The pagination
call is removed from `processes.main` (`src/twicc/cli/processes.py:46`):
`paginated = pagination_notice("processes", paginated, default_limit=20)`
becomes `paginated = paginated or listing_cutover_passed()`, keeping the
flag's meaning for direct callers of the module and dropping the notice. `CUTOVER_NOTICE` is dropped from the
`processes` help for the same reason.

### Help texts

A builder in `src/twicc/cli/_output.py`, `removal_help(command: str) -> str`,
reads the replacement from `RETIRED_COMMANDS` and returns, through
`cutover_help`:

- before: ``f"DEPRECATION: stops working on {_CUTOVER_DATE}; use `twicc {replacement}` instead. "``
- after: ``f"REMOVED on {_CUTOVER_DATE}: use `twicc {replacement}` instead. "``

| Command | Help today | Help after this change |
|---|---|---|
| `processes` | `processes_app` help (`:1394`), `CUTOVER_NOTICE + "List live TwiCC processes, …"` | `removal_help("processes") + "List live TwiCC processes, or look up specific session_ids."` (no `CUTOVER_NOTICE`) |
| `processes get`, `processes stop`, `processes wait` | their docstrings | `help=removal_help(…) + <the current docstring text>` on the decorators (`:1516`, `:1541`, `:1620`) |
| `process` | `process_app` help (`:1714`) | `removal_help("process") + "Inspect or control a session's live process."` |
| `process stop`, `process wait` | their docstrings | `help=removal_help(…) + <the current docstring text>` on the decorators (`:1735`, `:1773`) |

A `help=` on a decorator replaces the docstring as the Click help, so it
carries the whole docstring text.

### After the cutover: hidden, off the MCP list, still explained

- **`twicc --help`**: `processes_app` and `process_app` get
  `hidden=listing_cutover_passed()`, evaluated at import like the help texts.
  A hidden group still runs when named, so a direct call reaches the callback
  and gets the error.
- **MCP tool list**: `MCP_EXCLUDED_ROOTS` (`src/twicc/mcp/tools.py:29-31`) is
  evaluated at import; it gains `{"process", "processes"}` when
  `listing_cutover_passed()`. `build_mcp_registry` is cached at its first
  call. The tools therefore leave the list at the first backend start past
  the date; until then they stay listed and answer with the error.
- **MCP single calls after the cutover**: `_call_tool`
  (`src/twicc/mcp/server.py`) checks the tool name **first**, before
  `prepare_tool` (which raises `UnknownToolError` for a name missing from
  `tools_by_name()`, `src/twicc/mcp/dispatch.py:37-39`, and validates the
  arguments against the schema). The seven MCP names are a constant in
  `src/twicc/mcp/tools.py` (not in `_output.py`, which every command imports
  and which must not pull in the MCP layer): `RETIRED_MCP_TOOLS = {tool_name_for(cmd.replace(" ",
  "/")): cmd for cmd in RETIRED_COMMANDS}` — `processes`, `processes_get`,
  `processes_stop`, `processes_wait`, `process`, `process_stop`,
  `process_wait`. When `listing_cutover_passed()` and the name is one of them,
  `_call_tool` returns the **same envelope as any refused command**:
  `{"exit_code": 64, "result": None, "error": removal_message(RETIRED_MCP_TOOLS[name])}`, built and
  returned exactly as a normal command result is (text content plus
  `structured_content`, `is_error=False`, `:233-237` — "non-zero exit codes
  are data, not MCP errors"). This covers both windows: before the restart
  (tool still listed; a call missing a required argument would otherwise get
  "Input validation error") and after it (tool gone; a client connected
  before the restart would otherwise get `Unknown tool: <name>`, `:220-224`).
  The pre-check returns before `execute_prepared`, so a refused call writes no
  `McpOperation` row (`:100-130`). Accepted: nothing ran.
- **MCP `batch` / `batch_read`**: `validate_batch`
  (`src/twicc/mcp/batch_contract.py`) gains a `retired: frozenset[str]`
  parameter defaulting to `frozenset()` (so `tests/test_mcp_batch_contract.py:13-22`, which calls it without, keeps working), like the `registry=` / `read_only_paths=` it already takes (it
  cannot import `RETIRED_MCP_TOOLS`: `tools.py` imports `batch_contract` at
  module top, `tools.py:23`). `_call_batch` (`src/twicc/mcp/server.py:176-179`)
  passes `frozenset(RETIRED_MCP_TOOLS)` when `listing_cutover_passed()`, else
  an empty set. A child naming a retired tool gets the existing `unknown_tool`
  diagnostic ("Tool is not available.", `:85`) **before** the schema check
  (`:225-232`). As for every diagnostic, the batch is then rejected as a
  whole: no child runs. Without it, between the date and the restart a child with
  missing arguments would reject the whole batch at validation and a valid
  one would run and return the exit-`64` envelope; with it, the batch answers
  the same on both sides of the restart. The batch contract has no per-call
  free text; the single-call path and the tool descriptions carry the
  explanation.
- **RPC**: routes unchanged. The generator does not skip hidden commands
  (`src/twicc/rpc/generator.py:60-78`). The RPC view
  (`src/twicc/rpc/views.py`) checks the route's root in the **non-`argv`
  branch**, **after** the read-scope check (`:74-75`, so a cookie caller still
  gets its 403 on `processes/stop`) and after JSON parsing (a malformed body
  still gets its 400, `:88-89`), **before** `_validate_body` (`:110`). Past
  the cutover, a `process*` / `processes*` route there answers with the
  envelope `{"exit_code": 64, "result": null, "error":
  removal_message(command_path.rstrip("/").replace("/", " "))}` (the route
  `processes/get` maps to the key `processes get`, `process` to `process`), so a
  JSON body missing a required field gets the removal error rather than HTTP
  400. An `argv` body goes through the CLI and the group callback, which
  refuses the same way.
- **Telemetry**: `mcp_group_by_tool()` (`src/twicc/telemetry/snapshot.py:136-146`)
  is built from the registry, which loses the seven tools after the restart;
  `McpOperation` rows recorded under their names before the date would then
  count as `"other"` (`:312-318`), breaking the invariant stated at `:86-93`.
  It adds the seven `RETIRED_MCP_TOOLS` names, grouped by their root through
  `MCP_TOOL_GROUPS` like every other tool.

**Scope of "the refusal wins".** It wins over the subcommands' own argument
errors on the terminal, over schema validation on MCP single calls and on the
RPC JSON body. It does not win over errors Click raises on the **group's**
own input, before the callback: `processes --limit x` or a bare `process`
(missing `SESSION_ID`) still exit `2` with a usage error after the date.
`processes get --help` prints the refusal, not the help, since the callback
runs first. `process wait` also already used `64` for its own invalid
arguments (`src/twicc/cli/process_wait.py:58`, `:64`, `:75`); after the date
it never gets that far.

`src/twicc/rpc/permissions.py:69-73` (the `processes*` / `process*` entries)
stays: the routes exist until the code cleanup.

## What stays in use

Nothing in `src/` imports the seven command modules except
`src/twicc/cli/__init__.py`. Two pieces are shared and keep working:

- `process_stop.stop_cmd` — called by `session <id> stop`.
- `serialize_process_row` / `serialize_dead_process_row`
  (`src/twicc/cli/_process_state.py`) — `whoami` emits its nine-field
  `process` row with them (`src/twicc/cli/whoami.py:40-41`, `:77-79`).

Both are untouched: the refusal sits in the wrappers.

## Rewrite rules

Every place that teaches a retired command is rewritten **now**: the
replacements work on both sides of the date.

| Old | New | What changes for the caller |
|---|---|---|
| `processes --spawned-by self` (tracking children's states) | `sessions --spawned-by self --active --slim` (without `--active` to see finished ones too) | a child spawned seconds ago may not be listed yet: read it with `sessions get <id>` |
| `processes --spawned-by self` read for `last_state_change_at` / `pid` / `started_at` (hang detection: `patterns/supervisor.md:18-22`, `examples/long-running-watchdog.md:9-12`) | `sessions --spawned-by self --active --full`, or `sessions get <ids> --full` | the reduced projection carries `process: {state}` only; the three fields need `--full` |
| `processes get <ids>` | `sessions get <ids>` (`--full` for `pid` / timestamps) | `known` means "a `Session` row exists"; a live `process` block can ride on `known: false`; the process fields are under `process.*`, not flat |
| `processes stop <ids>` / `--spawned-by` / `--descendants` | `sessions stop` with the same arguments | never call it bare (it stops everything, the caller included); never with `parent`, `--spawn-tree` or `--siblings` unless that is the intent; `--annotation` only with a filiation scope |
| `processes stop <MANAGER_ID> --descendants <MANAGER_ID>` | same arguments on `sessions stop` | the id and the scope are unioned, so the manager and its subtree are stopped |
| `process <id>` | `session <id>` (or `sessions get <id>` right after a spawn) | exit `0` with `process.state: "dead"` when nothing runs, instead of exit `1` |
| `process <id> stop` | `session <id> stop` | none |
| a sentence describing **another** command's selection "as `processes stop`" (`SKILLS-AND-CLI.md:312`, `:397`, `twicc-update-sessions/SKILL.md:158`) | restate that command's own selection (`update-sessions`: explicit ids, `--spawned-by`, `--descendants`, `--annotation` with a scope; no `parent`, no `--spawn-tree`, no `--siblings`) | pointing at `sessions stop` would claim a wider selection than the command has |
| "`processes stop`, then a message" to restart an agent (`SKILLS-AND-CLI.md:302`, `twicc-update-session/SKILL.md:83`) | `session <id> stop`, then a message | none |
| the same for several sessions (`twicc-update-sessions/SKILL.md:103`) | `sessions stop <ids>`, then `send-messages <ids>` | none |
| `processes wait --spawned-by self user_turn dead` | `sessions wait-reply <child ids> --since <spawn instant>` (plus `--spawned-by self` only when every child is in the barrier; a barrier on an annotation subset names only that subset's ids, see P1) | each child's wait ends on an answer (`replied`), a pending request (`awaiting_user_input`), `ended`, `provider_error` or `timeout` (`concluded` in the summary counts only the first two); **exit `0` whatever the outcomes**: branch on `summary.all_replied` / each `outcome`, not on the exit code (the old command exited `5` on timeout) |
| `processes wait --first …` (incl. the annotation-scoped race, `control-cookbook.md:37`) | `sessions wait-reply --wait-first …`, naming only the ids of the racing subset (P1) | `--wait-first` also stops on `awaiting_user_input`; a race checks `outcome == "replied"` before declaring a winner. When the first conclusion is `awaiting_user_input`, either answer that session and wait again, or wait again **naming only the other ids** — the same call with the same `--since` would return the blocked session at once. A winner that finished before the call is only seen with `--since` |
| `processes wait … --timeout N` | `sessions wait-reply … --wait-timeout N`, with `N` ≤ 300 | one budget for the batch. A longer wait (the leader and manager skills use `--timeout 900`, `twicc-orchestration-leader/SKILL.md:48`, `twicc-orchestration-manager/SKILL.md:34`) becomes a loop with the same `--since <batch start instant>`, **repeated only while at least one `results[id].outcome` is `timeout`**, naming only those ids. `backend_gone` means retry once the backend is back. `wait_failed` means the wait itself broke and the sessions still run: retry the same call (same `--since`), naming those ids. `pending` only occurs with `--wait-first`. `replied` and `awaiting_user_input` are done; `ended`, `provider_error` and `unknown_session` are **final** — the same call returns them again at once — and the caller handles them (re-spawn, re-message, report). Never loop on `summary.concluded == summary.total`: `concluded` counts `replied` + `awaiting_user_input` only (`src/twicc/cli/sessions_wait_reply.py:180-186`), so a crashed child would make that loop spin forever. The MCP instructions ask for waits ≤ 300 s |
| `process <id> wait user_turn` right after `create-session` | `create-session --wait-reply`; otherwise `sessions wait-reply <id> --since <spawn instant>` | without `--since`, an answer that landed before the wait started is missed and the wait reports `ended` |
| `process <id> wait user_turn --transition` after `send-message` | `send-message --wait-reply`; otherwise `session <id> wait-reply --from <last_line of the send result>` | `send-message --wait-reply` exits `0` once sent, whatever the wait: branch on `reply.outcome`. `session <id> wait-reply` exits `0` on `replied` / `awaiting_user_input`, `5` on `timeout` / `ended` / `provider_error`, `2` on `backend_gone`, `1` on a refusal or `wait_failed` |
| `processes wait --spawned-by self user_turn dead --transition` after `send-messages` | `send-messages --wait-reply` | cursors are read server-side |
| waits on `awaiting_user_input` | the `awaiting_user_input` outcome of a `wait-reply` | — |
| waits on `dead` alone | removed; read `session <id>` / `sessions get <id>` (`process.state`) | — |

**Where the `--since` instant comes from.** `create-session` returns no
timestamp (`twicc-create-session/SKILL.md:219`), and an MCP-only caller has no
shell. So:

- children spawned for this barrier and **never messaged since**: any instant
  earlier than their spawn works, since `--since` older than a session's first
  line starts above that first stamped line (the `--since` help of `session
  <id> wait-reply`, `src/twicc/cli/__init__.py:735-736`; both commands place
  it with `_cursor_at`, `src/twicc/cli/session.py:704-722`). Use a fixed past date, **`2000-01-01`**. Never "today's
  date": a bare date is midnight **UTC** (`src/twicc/cli/session.py:667`), and
  the local date of a caller ahead of UTC can be tomorrow in UTC — an instant
  in the future puts the cursor on the last line (`:722`) and silently misses
  a child that already answered;
- a CLI caller can also capture `date -u +%Y-%m-%dT%H:%M:%SZ` before
  spawning;
- **later rounds** (children re-messaged with `send-messages` without
  `--wait-reply`): the instant of that send, captured before it, or better
  `send-messages --wait-reply`, which needs no instant at all;
- **resuming without an instant** (an MCP-only caller whose
  `send-messages --wait-reply` timed out): per id,
  `session <id> wait-reply --from <since_line_num>` from that id's `reply`
  block — the cursor the timed-out wait hands back for exactly this.

## Docs, skills and help texts

### Acceptance check

Two checks, both empty outside the listed exceptions.

**Prose** — every hit is rewritten with the rules above:

```bash
P='(twicc|\$TWICC) process(es)?\b|`processes([ `])|\bprocesses (get|stop|wait|--)|`process <|\bprocess <[A-Z_]+> (stop|wait)|\bprocess(es)?/(get|stop|wait)\b|mcp__twicc__process|twicc-process(es)?\b|\bprocess(es)? wait\b|\bprocess(es)? stop\b|\bprocesses get\b'
grep -rnE "$P" SKILLS-AND-CLI.md ORCHESTRATION.md RPC-API.md README.md src/twicc/agent/plugin
```

Exceptions: the two stub skills (`twicc-process/`, `twicc-processes/`), the
deprecation notes this design adds to `SKILLS-AND-CLI.md` (the reduced
`twicc processes` / `twicc process` sections) and `RPC-API.md` (a one-line
note under `:92`, see "Reference docs"),
and prose where "process stop" means an OS process stopping, not the command
(the pattern avoids most; each remaining hit is judged by reading it). On
`50c3e618` + the slim change this finds hits in 33 files, 31 of them outside
the two stub skills; all 31 are in the list below.

**Help texts** — the CLI help is the MCP tool description. No MCP tool
description, parameter description, or the MCP server instructions, other
than the seven retired tools' own, names a retired command. Checked by a test
over `iter_mcp_tools()` (see "Tests").

### Files

**Help texts and MCP instructions** (user-facing, part of the MCP schema):

- `src/twicc/cli/create_session/command.py:296-297`,
  `src/twicc/cli/send_message/command.py:125-126` and
  `src/twicc/cli/send_messages.py:196-197`: the whole clause "reach for `twicc
  process(es) … wait …` only to ask whether a session is still running, not
  what it said" is rewritten, not just the command name (a `wait-reply` there
  would say the opposite): "to check whether it is still running, read
  `process.state` from `session <id>` / `sessions get <ids>`".
- `src/twicc/cli/whoami.py:24-25`: "the nine-field shape ``processes`` uses" →
  describes the nine fields without naming the command.
- `src/twicc/mcp/server.py:68` (instructions): "Keep `*_wait` timeouts <= 300
  seconds" → names `wait_timeout` on the `*_wait_reply` tools and on
  `--wait-reply` calls.
- `src/twicc/cli/__init__.py:244-245` (`--state` help of the `sessions`
  listing, the `sessions.state` MCP parameter description): "Unlike
  `processes --state`, 'dead' is accepted…" → "'dead' is accepted and means no
  TwiCC-managed process…".
- `src/twicc/cli/__init__.py:555-558` (`sessions wait-reply` docstring, its MCP
  description) and the `session <id> wait-reply` help: `unknown_session` /
  exit `1` now means "no `Session` row and no live process" (P1); a live,
  not-yet-written session is waited on from line `0`, `--since` not applying
  to it.
- The `processes` `--paginated` and `--limit` options keep their shared help
  (`PAGINATED_HELP`, `limit_help("processes", 20)`), which promises the
  envelope as the only shape from the date. Accepted: the command-level
  `DEPRECATION` notice, first in the description, says the command stops that
  day, and the text is gone from the MCP list after the restart.

**Skills** (`src/twicc/agent/plugin/twicc/skills/`):

- `twicc-processes/SKILL.md`, `twicc-process/SKILL.md`: reduced to a stub. The
  frontmatter `description` reads "Retired on 2026-10-01 (deprecated until
  then). Use `sessions …` / `session <id> …` instead." — true on both sides of
  the date. The body is the "What replaces what" table, the lifecycle-wait
  note and the exit-code change (`64`). The folders are deleted in the code
  cleanup.
- The orchestration family, with the rules above: `twicc-orchestration/SKILL.md`
  (incl. `:65`, `:160` — the "`processes` sees an unindexed child"
  asymmetry becomes "name the child's id"), `control-cookbook.md` (incl.
  `:4`, `:29`, `:63`, `:88`), `examples/hard-bug-race.md`,
  `examples/large-migration.md`, `examples/long-running-watchdog.md`,
  `examples/parallel-feature-integration.md`, `examples/pr-review.md`,
  `patterns/composing.md`, `patterns/divide-and-conquer.md`,
  `patterns/phase-gated-fanout.md`, `patterns/pipeline.md`,
  `patterns/scatter-gather.md`, `patterns/single-writer-integration.md`,
  `patterns/speculative-race.md`, `patterns/supervisor.md`,
  `patterns/worker-pool.md`, `twicc-orchestration-leader/SKILL.md` (incl.
  `:69`), `twicc-orchestration-manager/SKILL.md` (incl. `:52`).
  `hard-bug-race.md` and `speculative-race.md` use `--wait-first` and check
  `outcome == "replied"`. `patterns/supervisor.md` and
  `examples/long-running-watchdog.md` read `last_state_change_at` and use
  `--full`. The annotation-scoped barriers (`patterns/divide-and-conquer.md:29`,
  `patterns/phase-gated-fanout.md:19`, `patterns/worker-pool.md:19`,
  `examples/large-migration.md:14`, `patterns/single-writer-integration.md:21`,
  `examples/parallel-feature-integration.md:15`, `control-cookbook.md:25-26`,
  the race at `control-cookbook.md:37`, `twicc-orchestration/SKILL.md:117`,
  `:161`, `patterns/composing.md:21-22`, `ORCHESTRATION.md:58`, `:89`) name only the subset's ids (P1). The
  prose grep finds every such barrier; this list is the ones known today, and
  any other annotation-narrowed wait the grep turns up follows the same rule.
- `twicc-create-session`, `twicc-send-message` (incl. `:157`),
  `twicc-send-messages` (incl. `:123`), `twicc-session`, `twicc-sessions`
  (incl. `:34` for P1, `:44` for P3 and `:82`, "Unlike `processes --state`"), `twicc-status`,
  `twicc-topology`, `twicc-update-session`, `twicc-update-sessions`,
  `twicc-whoami`: each hit rewritten.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: minor bump
  (`0.101.0` → `0.102.0`, or folded into `0.101.0` if both changes ship in the
  same commit).

**Reference docs:**

- `SKILLS-AND-CLI.md`: `:17` (the `--paginated` list names `processes`: drop
  it — the `processes` note says the flag works until the command stops),
  `:248` (`processes --state`, `processes --provider` comparisons), `:249`
  (P1: `unknown_session`), `:250` (P3), `:258` ("Same operation as `process
  <ID> stop`"), `:259` ("unlike `process wait --transition`", and the P1
  change to the singular's exit `1`), `:296`, `:302`, `:312`,
  `:385`, `:397` (the process-control paragraph: its guardrails move to the
  `sessions stop` section as prose), and the `twicc processes` (`:359`) /
  `twicc process` (`:367`) sections, reduced to a deprecation note with the
  replacement table and exit `64`.
- `ORCHESTRATION.md`: `:32`, `:34`, `:58`, `:86` ("Live-process commands are scoped operations, never global annotation searches" — false for `sessions stop` / `sessions wait-reply`, which accept `--annotation` alone: reworded as advice, "use them scoped"), `:88`, `:89-93`.
- `RPC-API.md`: `:92-102` ("Blocking waits can be cut short") is rewritten as
  a whole: the long-polls are `session/wait-reply` and `sessions/wait-reply`,
  their budget is `--wait-timeout` (not `--timeout`, four mentions), and the
  client read timeout follows it (P2); the `--wait-reply` forms of
  `create-session`, `send-message` and `send-messages` hold the connection the
  same way (`src/twicc/cli/_remote.py:677-688`) and are named too. After it, a one-line note: "The
  `process`, `process/*`, `processes` and `processes/*` routes stop working on
  2026-10-01 (exit `64`, error naming the replacement); use the `session` /
  `sessions` routes." Also `:19` (the example route `POST /rpc/process/wait`
  → a `wait-reply` route), and `:144-145` ("`wait` commands … the client read
  timeout is sized to the command `--timeout`" → sized to `--wait-timeout` on
  the `wait-reply` routes and on `--wait-reply`).

**CHANGELOG** — the owner's call, not written unasked.

## Tests

### Existing

- The module tests (`tests/test_cli_processes_listing.py`, the `processes`
  cases of `tests/test_cli_pagination_envelope.py`) call the module bodies
  directly and stay green. The `processes` pagination notice is gone
  (see "Where it is called"): any test asserting it on `processes` is updated.
  `tests/test_pagination_cutover.py` is checked for one.
- `tests/test_rpc_auth.py` uses `processes/stop` and `process/stop` as route
  names with a mocked `invoke`. Two tests POST `/rpc/processes/stop` and
  assert the mocked `invoke` ran: `test_token_allows_write_command` (`:146`,
  `:148`) and `test_unprotected_allows_everything` (`:222`, `:224`). Past the
  date the view's pre-check answers before `invoke`, so they go red on the
  day; they move to a write route that is not retired and not in the
  read-only allowlist, `sessions/stop`. `test_cookie_blocks_write_command`
  (`:181`) keeps passing: the pre-check sits after the read-scope 403.
  `:111-112` (the allowlist membership test) is unaffected.
- `tests/test_pagination_cutover.py:365-390`
  (`test_the_mcp_descriptions_match_the_side_of_the_cutover_we_are_on`) lists
  `processes` among the eleven listings. Before the date its description now
  starts with the removal notice (`DEPRECATION…`): the assertion holds. After
  the date `processes` is no longer an MCP tool: the expected set drops it on
  that side.
- `tests/test_mcp_tools.py:19-20` asserts that every RPC path is an MCP
  path (`rpc_paths <= paths`). After the date the RPC registry keeps the seven
  routes and the MCP registry drops them, so this goes red **on the day** —
  and the forced runs cannot catch it, since `MCP_EXCLUDED_ROOTS` is evaluated
  at import, before any fixture patches the constant. The test subtracts the
  `process` / `processes` roots from `rpc_paths` when
  `listing_cutover_passed()`.
- `tests/test_session_wait_documentation.py`
  (`test_every_mention_of_a_neighbour_s_flag_is_a_sanctioned_one`) pins
  `"SKILLS-AND-CLI.md": {"--transition": 1}`; the rewrite of
  `SKILLS-AND-CLI.md:259` removes that mention. The test asserts equality
  with `SANCTIONED` (`:260`), so the entry is kept and becomes
  `{"--wait-reply": 1, "--transition": 0}` (its `--wait-reply` mention stays). Any other count this file pins for a
  rewritten file is updated the same way.
- Any test that drives one of the seven through `invoke()` or a `CliRunner`
  without pinning the clock is found by the two forced runs of the slim design
  (constant forced to 2000, then 2200), and pinned.

### New

`tests/test_process_commands_removal.py`, `before` / `after` fixtures patching
`LISTING_CUTOVER`. Patching the date does not change what is evaluated at
import or cached: `MCP_EXCLUDED_ROOTS`, `build_mcp_registry` /
`tools_by_name` (`@cache`) and `mcp_group_by_tool`. The "after the restart"
tests therefore also monkeypatch `twicc.mcp.server.tools_by_name` (and
`build_mcp_registry`, with `mcp_group_by_tool.cache_clear()` around the
telemetry test) to a registry without the `process` / `processes` roots:

- For each of the seven, through `invoke()`:
  - before: the command's usual result and exit code, and **one** `warnings`
    entry naming `twicc <command>`, the date and the replacement;
  - after: exit `64`, `error` naming the command, the date and the
    replacement, and no side effect (for `processes stop` / `process stop`,
    the drop-request transport is never called; for the waits, no poll).
- After, the refusal wins over subcommand argument errors: `processes get`
  with no id, `processes wait user_turn` without `--timeout`, `process X wait`
  with no state all exit `64` with the removal error, on the terminal
  (`CliRunner`) and through `invoke()`.
- After, the refusal wins over schema validation: an MCP single call to
  `processes_wait` without `timeout`, and an RPC JSON-body `POST
  /rpc/processes/get` without `session_ids`, both return the removal error
  (exit `64`), not "Input validation error" / HTTP 400.
- Before, `processes` without `--paginated` carries only the removal notice.
- MCP before the date: no warning (`mcp_call` set, as
  `tests/test_pagination_cutover.py:250-260`).
- MCP after the date, before a restart: `invoke()` with `mcp_call` set returns
  the error.
- MCP after the restart: `_call_tool` with `processes_wait` (absent from
  `tools_by_name()`) returns the removal text, not `Unknown tool`; an
  unrelated unknown name still returns `Unknown tool`. Before the date,
  `_call_tool` with `processes_wait` runs normally (the pre-check is inert).
- The notice date is read at call time (constant patched to
  `datetime(2199, 3, 4)`).
- On the real side of the cutover, read on the real constant like
  `tests/test_pagination_cutover.py:365-390`: before, the seven MCP
  descriptions start with `DEPRECATION` and name their replacement; after,
  `tools_by_name()` has none of the seven and `twicc --help` lists neither
  group. Like that test, it is a known false positive under a plugin that
  forces the constant: the help texts, `hidden=` and `MCP_EXCLUDED_ROOTS` are
  evaluated at import.
- MCP `batch` after the date: a batch with a child naming `processes_wait` is
  rejected with a single `unknown_tool` diagnostic at that child's index,
  whether its arguments are valid or not; no other child gets a diagnostic,
  and none runs.
- RPC after the date: a cookie (read-scope) caller on `processes/stop` still
  gets 403; a malformed JSON body still gets 400; a valid token caller gets
  the exit-`64` envelope.
- Telemetry: `mcp_group_by_tool()` maps `processes_wait` to the `processes`
  root's group whatever the side of the cutover.
- Help-text acceptance: no description or parameter description of any other
  MCP tool, nor the MCP server instructions, matches the prose pattern above.
- `session <id> stop` still works after the date, and `whoami` still returns
  its nine-field `process` row.

For the prerequisites:

- P1: `sessions wait-reply <id>` and `session <id> wait-reply` on an id with a
  live `ProcessRun` and no `Session` row wait and report `replied` when a final
  message arrives; with a dead or missing `ProcessRun` they keep today's
  `unknown_session` / exit `1`. `session <id> wait-reply` on a row with no
  user message yet and a live `ProcessRun` waits too.
  `tests/test_sessions_wait_reply_documentation.py` and
  `tests/test_session_wait_documentation.py` check flags and cursor wording
  across the docs, not the `unknown_session` text; they are re-run after the
  doc edits and any count they pin for a rewritten file is updated.
- P2: `_request_timeout` on `session <id> wait-reply --wait-timeout 600` gives
  `read = 615`, and `300 + margin` without `--wait-timeout`; same on
  `sessions wait-reply`.
- P3: the `_sessions_stop` argument help no longer says "bypasses".

## Not in scope

- Deleting the seven command modules, their tests, their skill folders, their
  RPC permission and `_WAIT_PATHS` entries: the later code cleanup.
- Any new state-wait on the session family.
- Adding guardrails to `sessions stop` (bare call, `--spawn-tree`,
  `--annotation` without scope): documented, not changed.
