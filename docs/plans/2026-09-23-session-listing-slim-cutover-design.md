# Session listings — slim by default, one shared cutover

**Status:** implemented
**Date:** 2026-09-23
**Cutover instant:** `2026-10-01T00:00:00`, local time on the machine running TwiCC — shared with the pagination cutover, which moves to this date

Paths are relative to the repository root. Line numbers are those of `main` at
`50c3e618`.

## Problem

Four CLI commands return several sessions. They disagree on the default shape
and on the flag that changes it:

| Command | Default today | Flag today |
|---|---|---|
| `sessions` | full `serialize_session()` payload | `--slim` (`src/twicc/cli/__init__.py:228`) |
| `sessions get` | full | `--slim` (`src/twicc/cli/__init__.py:602`) |
| `session <id> agents` | full | `--slim` (`src/twicc/cli/__init__.py:933`) |
| `topology` | slim (`TOPOLOGY_SESSION_FIELDS`, `src/twicc/cli/topology.py:17`) | `--full-sessions/--no-full-sessions` (`src/twicc/cli/__init__.py:1334`) |

The main consumer is an agent over MCP, where every field costs tokens. A
default `sessions` page is 20 full payloads, of which the agent rarely needs
more than the listing projection (`SESSION_LISTING_FIELDS`,
`src/twicc/core/serializers.py:128`). The `has_*` flags in that projection
already say where to look next, and `session <id>` returns the full row.

The `process` block is inconsistent too: `--slim` reduces it to `{state}`
(`src/twicc/cli/_process_state.py:227`), but `topology` always emits the five
fields (`src/twicc/cli/topology.py:379-381`), even though its sessions are slim.

Separately, the pagination cutover (`PAGINATION_CUTOVER`,
`src/twicc/cli/_output.py:111`) was set to 2026-09-15 and has not been
released. The code on `main` has already flipped. It must move to a date after
the release.

## Decisions

| Point | Decision |
|---|---|
| Rule | a command that returns **several** sessions is slim by default; a command that returns **one** session is full |
| Commands that change | `sessions`, `sessions get`, `session <id> agents`, `topology` |
| Commands that do not change | `session <id>`, `whoami` (one session each); every command that returns no session payload |
| Trigger | the same dated local cutover as pagination, one constant for both |
| Cutover instant | `2026-10-01T00:00:00` local (the night of 30 September to 1 October) |
| Pagination | its cutover moves from 2026-09-15 to that same instant; nothing else about it changes |
| Flags | two independent booleans, `--slim` and `--full`, on all four commands, mutually exclusive |
| Before the cutover | output unchanged when neither flag is passed, plus a notice |
| After the cutover | slim is the default; `--slim` is an accepted no-op |
| Escape hatch after it | `--full`, permanent |
| MCP | never notified, same as pagination |
| `topology` | session projection unchanged (already slim); only its default `process` block changes, to `{state}` |
| `--full-sessions` | kept on `topology` as a visible, deprecated alias of `--full`, on every channel (CLI, `--remote`, RPC, MCP) |
| Slim projections | stay two: `SESSION_LISTING_FIELDS` and `TOPOLOGY_SESSION_FIELDS` |

Four of these need their rationale on the record.

**An escape hatch, unlike pagination.** Pagination has none
(`docs/plans/2026-09-08-pagination-cutover-design.md`, "No escape hatch"): the
bare array was the shape to kill. Here the full payload is not a shape to kill.
It is a legitimate need — a caller about to act on a batch of sessions reads
their agent settings, `cwd`, `tasks`. So `--full` stays forever.

**Two flags, not one `--slim/--full` pair.** A Click pair is one boolean. It
has no "not passed" state unless its default is `None`, and the MCP schema
would expose a single `slim` boolean whose `false` means "full"
(`src/twicc/rpc/schema.py:52`, `src/twicc/rpc/generator.py:110-112`). An agent
that wants the full payload would pass `slim: false`. Two booleans give the
schema a `full: true` that reads as what it does, and they keep `--slim` as the
exact analogue of `--paginated` after the date: an accepted no-op.

**Two slim projections, kept apart.** The reason is recorded at
`src/twicc/core/serializers.py:123-127`: a tree carries no visibility state, a
flat listing needs it, and merging them would make a 332-node topology 17%
heavier. "Slim" names a mode ("the reduced projection of this command"), not
one field set. Each command's help lists its own fields.

**`session <id>` and `whoami` stay full.** They return one session. The reason
to call them is the detail. `whoami` also has redundant top-level keys
(`session_id`, `title`, `project_id`, `current_working_directory`) and a
nine-field `process` block (`src/twicc/cli/whoami.py:90-101`); that is out of
scope here.

## The shared cutover

### Constant and predicate

In `src/twicc/cli/_output.py`:

- `PAGINATION_CUTOVER` (`:111`) is renamed `LISTING_CUTOVER` and set to
  `datetime(2026, 10, 1)`. The `noqa: DTZ001` stays. Its comment (`:106-110`)
  changes in three places: "the bare listing shape stops being emitted"
  becomes "the bare listing shape and the full session payload stop being the
  defaults"; "midnight on the 15th" becomes "midnight on the 1st"; the
  `Design:` line cites this document next to the pagination one.
- The `_notices` comment (`:128`), "One notice per invocation", becomes "Up to
  one notice per migration per invocation": a flagless `sessions` call records
  two.
- `pagination_is_default()` (`:114`) is renamed `listing_cutover_passed()`.
  Body and naive-datetime guard unchanged; its error message names the new
  function. Its docstring (`:115`), "True once the envelope is the only shape
  and ``--paginated`` is a no-op", also says the reduced session projection is
  then the default and `--slim` a no-op.
- `_CUTOVER_DATE` (`:223`) and every `strftime` read the renamed constant.
- Two docstrings name the old symbols: `cutover_help` (`:211`,
  ``:data:`PAGINATION_CUTOVER` ``) names `LISTING_CUTOVER`; `_in_mcp_call`
  (`:140-141`, "Agents read ``paginated`` off the tool schema") names
  `slim`/`full` too.

The rename is deliberate: a constant named after pagination that also governs
the session projection would mislead the next reader. Every reference is in
`src/twicc/cli/_output.py` and the tests listed under "Tests".

### The pagination side after the move

Moving the date from 2026-09-15 to 2026-10-01 puts `main` back into the
pagination pre-cutover phase until the new date. Consequences, all intended:

- `CUTOVER_NOTICE`, `CUTOVER_NOTICE_OBJECT`, `PAGINATED_HELP` and
  `limit_help()` show their "before" texts again (`src/twicc/cli/_output.py:228-252`,
  `:255-267`).
- A listing called without `--paginated` returns the bare shape again, with the
  notice.
- `session content` and `session messages` without `--limit` return every item
  again, not a page of 50.
- The bare `session content` guard comes back: a call with no selector is
  refused again (`src/twicc/cli/session.py:145-154`,
  `test_before_the_cutover_a_bare_content_call_is_refused`).
- Tests that rely on the post-cutover shape without pinning the clock go red.
  They must pin it (see "Tests").

### The notice recorder

`pagination_notice()` (`:150-202`) builds its message, then records it on one
of three carriers. That second half is extracted into a private helper,
`_record_notice(message: str) -> None`: `_notices` list if set, else
`typer.echo(message, err=True)`, then the log record if `_NOTICE_LOGGER` has
handlers. `pagination_notice()` calls it. Behaviour unchanged.

The MCP guard (`_capture.get() is not None and _in_mcp_call()`) stays in each
public notice function, **before** the message is built, as today.

`_notices` keeps its name. Its `ContextVar` label changes from
`"pagination_notices"` to `"cutover_notices"`. `_NOTICE_LOGGER` becomes
`logging.getLogger("twicc.cli.cutover")`. No test reads either label; the one
test that patches the logger patches the `_NOTICE_LOGGER` attribute
(`tests/test_pagination_cutover.py:295`).

## The slim cutover

### Resolution

New function in `src/twicc/cli/_output.py`:

```python
def slim_notice(command: str, slim: bool, full: bool, *, kind: str = "listing") -> bool:
    """Resolve the slim mode, announcing the cutover while it is still ahead."""
```

It returns what `slim` means from here on:

| `slim` | `full` | Before the cutover | After the cutover |
|---|---|---|---|
| `False` | `True` | `False` | `False` |
| `True` | `False` | `True` | `True` |
| `False` | `False` | `False`, and a notice unless MCP | `True`, no notice |

`slim` and `full` both `True` never reaches it: the Typer wrappers reject that
first (see "Flags"). As a second line, `slim_notice` treats it as a programming
error and raises `ValueError`, so a direct Python caller cannot get an
arbitrary winner.

**Check order**, which differs from `pagination_notice` (whose first line is
the cutover shortcut, `src/twicc/cli/_output.py:166-167`):

1. `slim and full` → `ValueError`;
2. `full` → return `False`;
3. `slim` → return `True`;
4. cutover passed → return `True`;
5. MCP call (`_capture.get() is not None and _in_mcp_call()`) → return `False`;
6. build the message, `_record_notice(message)`, return `False`.

Putting the cutover shortcut first, as `pagination_notice` does, would make
`--full` a no-op after the date.

`kind` selects the notice text, the same way `shape=` does for
`pagination_notice`:

`kind="listing"` — `sessions`, `sessions get`, `session agents`:

```
twicc: from {when}, `{command}` returns the reduced session projection by
default (what --slim returns today) instead of the full payload. Pass --full to
keep the full payload, or --slim to get the new shape today; after that date
--slim is accepted but does nothing.
```

`{when}` is the formatted date, `{command}` the CLI path passed in (`sessions`,
`sessions get`, `session agents`), as in `pagination_notice`
(`src/twicc/cli/_output.py:173-188`).

`kind="topology"`:

```
twicc: from {when}, `topology` reduces each node's `process` block to
{"state"} by default. Pass --full to get the full session and process block on
every node, or --slim to get the new shape today; after that date --slim is
accepted but does nothing.
```

The date is formatted from `LISTING_CUTOVER` **at call time**
(`LISTING_CUTOVER.strftime("%Y-%m-%d")` inside `slim_notice`, as
`pagination_notice` does at `src/twicc/cli/_output.py:173`), never from the
import-time `_CUTOVER_DATE`. A test that pins the constant then sees its pinned
date in the notice, like `test_the_date_comes_from_the_constant` for pagination.
`command` is the full CLI path (`"session agents"`, `"sessions get"`), as for
`pagination_notice`.

`topology`'s text says "--full" and not "keep the current shape": after the
cutover, no flag combination gives slim sessions with a five-field `process`
block. The four dropped fields (`id`, `started_at`, `last_state_change_at`,
`pid`) are available per session through `process <id>` or with `--full`.
Accepted.

`topology --no-processes` gets **no** notice: every node then has
`process: None` (`src/twicc/cli/topology.py:379-381`), and the session
projection does not change, so nothing that caller reads changes on the date.
`topology.main` therefore calls `slim_notice` only when `include_processes`
is true, and uses `slim_processes = False` otherwise (the value is unused on
that path). Same idea as `share` being exempt from the page-size clause.

The same argument holds when `--processes` is set but no backend runs:
`build_topology` then emits `process: None` on every node
(`src/twicc/cli/topology.py:152-162`), yet the notice fires, since
`topology.main` calls `slim_notice` before `build_topology` learns that no
backend is live. Accepted: moving the call after that check would push notice
logic into `build_topology`, which the REST view shares, for a notice that
only lives until the date.

### Where it is called

One line per command body, right after the existing `pagination_notice` line
where there is one, else right after `django.setup()`:

| Body | Line |
|---|---|
| `src/twicc/cli/sessions.py` `main` (`:196`; after `pagination_notice` at `:228`) | `slim = slim_notice("sessions", slim, full)` |
| `src/twicc/cli/sessions_get.py` `main` (`:50`; after `django.setup()` at `:59`) | `slim = slim_notice("sessions get", slim, full)` |
| `src/twicc/cli/session.py` `agents` (`:368`; after `pagination_notice` at `:374`) | `slim = slim_notice("session agents", slim, full)` |
| `src/twicc/cli/topology.py` `main` (`:33`; after `django.setup()` at `:44`) | `slim_processes = slim_notice("topology", slim, full, kind="topology") if include_processes else False` |

Each function gains a `full: bool = False` keyword next to `slim`. Every
downstream decision already reads `slim`, so the three listings need nothing
else.

Order on `sessions` and `session agents`: pagination notice first, slim notice
second. Both are recorded; a caller that passes neither flag gets two lines.

### `topology`

`topology.main` gains `slim: bool = False, full: bool = False`, and drops
`full_sessions` from its signature: the wrapper folds the alias into `full`
(see "Flags").

`build_topology` (`src/twicc/cli/topology.py:74`) keeps its `full_sessions`
parameter and gains `slim_processes: bool = False`. It threads it to
`_serialize_topology_node` (`:359`), which passes it to
`serialize_compact_process(process_row, slim=slim_processes)` (`:380`).

`topology.main` calls `build_topology(..., full_sessions=full, slim_processes=slim_processes)`.

The keyword defaults of `build_topology` keep today's output. The REST view
(`src/twicc/views.py:1568-1573`, `full_sessions=True`, no `slim_processes`)
therefore keeps full sessions and five-field process blocks: the UI's
Orchestration tab does not change. So do the direct `build_topology` tests in
`tests/test_cli_topology.py`.

`processes: False` and "no backend" paths keep `process: None`
(`src/twicc/cli/topology.py:379-381`): `slim_processes` only changes what a
read row projects to.

## Flags

### The three listings

In `src/twicc/cli/__init__.py`, on `_sessions_default` (`:228`),
`_sessions_get` (`:602`) and `agents` (`:933`):

```python
slim: bool = typer.Option(False, "--slim", help=SLIM_HELP),
full: bool = typer.Option(False, "--full", help=FULL_HELP),
```

Each wrapper rejects both together, before calling the body, like the existing
`--include-hidden`/`--only-hidden` check (`:328-329`):

```python
if slim and full:
    emit_error("Error: --slim and --full are mutually exclusive.", code=2)
```

Each wrapper then passes `full=full` next to `slim=slim` in its call to the
body: `sessions_main(...)` (`:360`), `sessions_get_main(...)` (`:613`),
`session_agents(...)` (`:940`).

On `_sessions_default` the check sits with the other mutual-exclusion checks,
after the `ctx.invoked_subcommand` early return (`:325-326`). `_sessions_get`
and `agents` have no such block; the check is their first statement.

### `topology`

On `topology` (`src/twicc/cli/__init__.py:1318`):

```python
slim: bool = typer.Option(False, "--slim", help=TOPOLOGY_SLIM_HELP),
full: bool = typer.Option(False, "--full", help=TOPOLOGY_FULL_HELP),
full_sessions: bool = typer.Option(
    False, "--full-sessions/--no-full-sessions",
    help="Deprecated alias of --full, kept for existing callers.",
),
```

The wrapper computes `full = full or full_sessions`, then applies the same
mutual-exclusion check, then calls `topology_main(..., slim=slim, full=full)`.
Its message names both spellings, since the user may have typed the alias:
`"Error: --slim and --full (or --full-sessions) are mutually exclusive."`
`--no-full-sessions` never cancels `--full`: `--full --no-full-sessions` (or
`{"full": true, "full_sessions": false}` over MCP/RPC) gives the full payload.
Alone, `--no-full-sessions` does nothing, the same as passing neither flag.

**The alias stays visible, not hidden.** `--full-sessions` is released (it is
in every tag since `v1.7.0`), and its main callers are agents over MCP. A
`hidden=True` option is skipped by `param_spec` (`src/twicc/rpc/schema.py:37`),
so it would vanish from the tool schema; with `additionalProperties: false`
(`src/twicc/rpc/schema.py:92`), an MCP call or a JSON-body `/rpc/topology`
call (`src/twicc/rpc/views.py:36-44`) passing `full_sessions` would then be
refused, with no date and no notice. Visible, it keeps working on every
channel: terminal, `--remote` (raw `argv`, `src/twicc/cli/_remote.py:763`),
MCP and RPC. The cost is one extra boolean in the `topology` schema, whose
description names the replacement.

### Help texts

All through `cutover_help(before, after)`, so they flip on the date without a
second edit. Evaluated at import, like the existing ones. Every new constant
below lives in `src/twicc/cli/_output.py`, after `_CUTOVER_DATE` (`:223`, which
their `{date}` needs) and next to `SLIM_HELP` (`:270`), and is added to the
import block at `src/twicc/cli/__init__.py:24-27`.

| Constant | Before the date | After the date |
|---|---|---|
| `SLIM_HELP` | today's text (`src/twicc/cli/_output.py:270-272`) + "Off by default until {date}, when it becomes the default and this flag turns into an accepted no-op." | "Accepted and ignored: the reduced projection is the default. Kept so scripts that migrated during the deprecation window keep working untouched." |
| `FULL_HELP` (new) | "Return the full session payload — the same fields as `session <id>`. It is the default until {date}; pass --full now to keep it after that date. Mutually exclusive with --slim." | "Return the full session payload — the same fields as `session <id>` — instead of the default reduced projection: identity, state, cost, and the has_* flags telling you what else is there. Mutually exclusive with --slim." |
| `TOPOLOGY_SLIM_HELP` (new) | "Reduce each node's `process` block to `{state}`. Each node's `session` is already the reduced subset. Off by default until {date}, when it becomes the default and this flag turns into an accepted no-op." | "Accepted and ignored: each node's `process` block is reduced to `{state}` by default." |
| `TOPOLOGY_FULL_HELP` (new) | "Emit the full session serialization for every node — the fields `session <id>` returns, minus its `process` block, which sits at `nodes[].process` — and that full `process` block. Disabled by default: each node's `session` carries a reduced subset (id, project_id, provider, title, annotations, spawned_by, spawn_root, created_at, last_new_content_at, context_usage, context_max, total_cost, directory). Its `process` block keeps its five fields until {date}; from that date it is `{state}` alone. Mutually exclusive with --slim." | "Emit the full session serialization for every node — the fields `session <id>` returns, minus its `process` block, which sits at `nodes[].process` — and that full `process` block. Disabled by default: each node's `session` carries a reduced subset (id, project_id, provider, title, annotations, spawned_by, spawn_root, created_at, last_new_content_at, context_usage, context_max, total_cost, directory), and its `process` block is `{state}` alone. Mutually exclusive with --slim." |

One argument help also becomes false after the date: the `session_ids` help
of `sessions get` (`src/twicc/cli/__init__.py:589-598`), which `param_spec`
copies into the `sessions_get` MCP schema (`src/twicc/rpc/schema.py:72`,
`:86-87`). It says "Each entry is either the full session metadata or a
placeholder …". It becomes `SESSIONS_GET_IDS_HELP = cutover_help(before, after)`:

- before: today's text, unchanged;
- after: today's text with "Each entry is either the full session metadata or
  a placeholder" replaced by "Each entry is either the session's reduced
  projection (its full metadata with --full) or a placeholder".

Command-level prefixes, emptied after the date like `CUTOVER_NOTICE`:

- `SLIM_CUTOVER_NOTICE` = "DEPRECATION: from {date} this returns the reduced session projection by default (what --slim returns today). Pass --full to keep the full payload. "
- `TOPOLOGY_CUTOVER_NOTICE` = "DEPRECATION: from {date} each node's `process` block is reduced to `{state}` by default. Pass --full to get the full session and process block on every node. "

Where they go — the command help is also the MCP tool description
(`src/twicc/mcp/tools.py:75-77`):

| Command | Help today | Help after this change |
|---|---|---|
| `sessions` | `sessions_app` help (`src/twicc/cli/__init__.py:210`) | `CUTOVER_NOTICE + SLIM_CUTOVER_NOTICE + "List sessions, …"` |
| `sessions get` | the docstring of `_sessions_get` (no `help=`) | `help=SLIM_CUTOVER_NOTICE + <the current docstring text>` on the decorator (`:587`) |
| `session agents` | `CUTOVER_NOTICE + "List subagents …"` (`:929`) | `CUTOVER_NOTICE + SLIM_CUTOVER_NOTICE + "List subagents …"` |
| `topology` | the docstring (`@app.command()`, `:1317`) | `help=TOPOLOGY_CUTOVER_NOTICE + "Show the spawned-session tree containing a session as JSON."` |

A `help=` on the decorator replaces the docstring as the Click help, so the
`sessions get` decorator must carry the whole current docstring text, not
only its first line.

## Channels

Unchanged from the pagination design: stderr on the terminal, the `warnings`
key on RPC, nothing on MCP (including `batch` / `batch_read`). The same
`_record_notice` does it.

`--remote`: the body runs on the remote, so the remote's clock and version
decide both the default and the notice, as for pagination.

## Tests

### Clock pins

Every test whose assertion depends on either default must pin the clock
explicitly, with a naive datetime, by monkeypatching `LISTING_CUTOVER`.

- The four files that pin today (`tests/test_cli_pagination_envelope.py:37`,
  `tests/test_cli_resolve_filters.py:34`, `tests/test_artifact_bookmarks.py:27`,
  `tests/test_cli_processes_listing.py:28`) and `tests/test_pagination_cutover.py`
  (`:33`, `:38`, `:103`) patch `"PAGINATION_CUTOVER"`. `monkeypatch.setattr`
  fails loudly on the old name, so the rename cannot be missed; each becomes
  `"LISTING_CUTOVER"`. Their docstrings that say "red on 2026-09-15" say
  "red on 2026-10-01".
- Those pins are to 2200: they now also keep the full payload as the default.
  That is what their assertions need.
- `tests/test_pagination_cutover.py:70` reads `_output.PAGINATION_CUTOVER`
  directly, and `:71-72`, `:79` and `:385` call `pagination_is_default`; they
  read `LISTING_CUTOVER` and call `listing_cutover_passed`.
- Stale names in prose, updated with the rename: the docstrings naming the
  constant (`tests/test_pagination_cutover.py:4`,
  `tests/test_cli_pagination_envelope.py:28`, `tests/test_cli_resolve_filters.py:25`,
  `tests/test_artifact_bookmarks.py:18`, `tests/test_cli_processes_listing.py:19`),
  the logger name in the `_isolate_logger` docstring
  (`tests/test_pagination_cutover.py:281`), the comment at
  `tests/test_final_assistant_message.py:319`, and the comment at
  `src/twicc/cli/topology.py:11-16` ("opt into … with `--full-sessions`").
- `tests/test_final_assistant_message.py:321` reads `["items"]` with no pin
  (the comment above it, `:319`, says why), so it passes today only because
  2026-09-15 is past. It pins the clock past the cutover.
- **Every other clock-dependent test** is found by running the whole suite
  twice, with the constant forced to 2000 and then to 2200 (a pytest plugin
  passed with `-p` that monkeypatches the constant in an autouse fixture).
  A test red under one of the two runs and not pinned is pinned. One known
  false positive: `tests/test_pagination_cutover.py`
  (`test_the_mcp_descriptions_match_the_side_of_the_cutover_we_are_on`) goes
  red under such a plugin because the help texts are evaluated at import,
  before the fixture patches the constant; it is correct against the real
  constant.
- That method was run on `main` for the pagination side alone (constant
  patched to `datetime(2026, 10, 1)`, today 2026-09-23): 4096 passed, 3 failed —
  the two parameters of `test_final_assistant_message.py::test_cli_messages_exposes_final_on_every_entry`
  and the false positive above. The slim side can only be measured once
  implemented. The known candidates:
  - `tests/test_cli_session_process_state.py` has no pin and calls the
    listings without `slim` at `:125`, `:141`, `:306`, `:309`, `:357` and
    `:702` (`test_it_is_the_same_block_the_listing_builds`, which compares
    `sessions get` with the five-field block of `session <id>`). These
    tests pass `full=True` instead of relying on the default, so
    they read as what they test. The CLI-runner tests from `:565` all pass
    `--slim` or exit on an error path, so they do not depend on the default.
  - `tests/test_cli_topology.py:197` (`test_build_topology_adds_compact_process_state`)
    calls `build_topology` directly, whose defaults do not change: it stays
    green unmodified.

### Pinned tests that the slim notice breaks

Five tests in `tests/test_pagination_cutover.py` are already pinned `before`
and call `sessions` with neither `--slim` nor `--full`. With this design each
such call records a **second** notice, so their counts break. Pinning does not
catch them: they are pinned already.

| Test | Assertion that breaks |
|---|---|
| `test_a_migrated_caller_is_left_alone` (`:109`) | `err == ""` |
| `test_the_rpc_path_carries_the_notice_in_the_envelope` (`:224`) | `len(result.warnings) == 1` |
| `test_notices_do_not_accumulate_across_invocations` (`:240`) | `== 1` |
| `test_the_log_carrier_fires_when_a_handler_exists` (`:299`) | `len(records) == 1` |
| `test_the_log_carrier_is_skipped_without_a_handler` (`:313`) | `err.count("twicc: from ") == 1` |

Each passes `full=True` (or `--full` on the `invoke` argv). They test the
pagination notice and its carriers; `--full` isolates that notice without
changing what they assert. The combined case (two notices) is covered by a new
test below.

The other `before` tests in that file that call `sessions` or `session agents`
without a slim flag (`:85`, `:93`, `:102`, `:170`, `:204`, `:212`, `:248`)
assert on content that stays true with a second notice (a substring, the first
line's prefix, the stdout shape, an empty `warnings` on MCP). They stay
unmodified.

### New tests

In `tests/test_pagination_cutover.py`, or a new `tests/test_slim_cutover.py`
using its `before` / `after` fixtures:

- For each of `sessions`, `sessions get`, `session agents` (on every line
  below, `process` is always `null` on `session agents`, and `sessions get`
  entries also carry `known`):
  - before, no flag: full payload, five-field `process`, one slim notice
    naming the command and the date;
  - before, `--slim`: `SESSION_LISTING_FIELDS` + `process: {state}`, no slim
    notice;
  - before, `--full`: full payload, no slim notice;
  - after, no flag: identical to "before, `--slim`", no notice;
  - after, `--slim`: identical to the line above;
  - after, `--full`: full payload.
- `topology`:
  - before, no flag: `TOPOLOGY_SESSION_FIELDS` + `directory`, five-field
    `process`, one notice with the topology text;
  - after, no flag: same sessions, `process: {"state": …}`;
  - `--full` on either side: full sessions, five-field `process`, and no
    slim notice before the date;
  - `--full-sessions` behaves exactly as `--full`, from the terminal and
    through `invoke()` on the argv that `render_argv`
    (`src/twicc/rpc/generator.py:119`) builds from a `{"full_sessions": true}`
    body, which is the MCP and RPC path;
  - `--no-processes`: `process: None` on every node on both sides, and no
    slim notice before the date.
- `--slim --full` together exits 2 with the mutual-exclusion message, on all
  four commands, from the CLI runner. On `topology`, also `--slim
  --full-sessions` from the CLI runner, and `{"slim": true, "full_sessions":
  true}` through `render_argv` + `invoke()`: both exit 2 with the alias
  message.
- `slim_notice(..., slim=True, full=True)` raises `ValueError`.
- MCP records no slim notice, including through `batch`. The MCP envelope
  never carries `warnings` (`execute_prepared` builds `exit_code`, `result`,
  `error` only), so the test calls `invoke()` with `mcp_call` set, as
  `tests/test_pagination_cutover.py:248-258` does, and asserts
  `result.warnings == ()` on `sessions get` and `topology` (no pagination
  notice there to mask it).
- On the RPC path, `sessions` without flags before the date carries **two**
  entries in `warnings`: pagination first, slim second.
- The MCP schema: the registry paths `sessions`, `sessions/get`,
  `session/agents`, `topology` each have boolean `slim` and `full` params;
  `topology` keeps its boolean `full_sessions`. Asserted on `build_registry()` (keyed by
  CLI path), like `tests/test_cli_session_process_state.py:367-385`.
- The help texts match the side of the cutover: before the date, the
  `sessions_get` and `topology` descriptions start with `DEPRECATION`, and the
  `sessions`, `sessions_get`, `session_agents` descriptions contain the fixed
  substring "reduced session projection by default"; after the date, none
  does. (Asserting `SLIM_CUTOVER_NOTICE in description` would pass vacuously
  after the date, when the constant is `""`.) The texts are evaluated at import,
  so fixtures that patch the constant cannot flip them: like
  `tests/test_pagination_cutover.py:363-389`, the test reads
  `listing_cutover_passed()` on the real constant and asserts the matching
  side. It shares that test's known false positive when the suite runs under a
  plugin that forces the constant. The same test covers the `sessions_get`
  `session_ids` parameter description (`SESSIONS_GET_IDS_HELP`): it contains
  "the full session metadata or a placeholder" before the date and "reduced
  projection" after.
- The slim notice carries the pinned date (the constant patched to
  `datetime(2199, 3, 4)` gives "2199-03-04"), like
  `test_the_date_comes_from_the_constant`. The test runs `sessions get` (which
  has no pagination notice), so the pinned date in `err` can only come from
  the slim notice. On `sessions` the pagination notice would already carry it,
  and a slim notice built from the import-time `_CUTOVER_DATE` would go
  unnoticed.
- The REST topology (`src/twicc/views.py:1568`) keeps five-field `process`
  blocks after the date.
- `tests/test_cli_session_process_state.py:269`
  (`test_the_three_listings_share_one_slim_key_set`) also holds with no flag
  after the date.

## Docs and skills

These are prose; they do not read the constant.

- Every "2026-09-15" in `SKILLS-AND-CLI.md:17` and in the `--paginated`
  paragraphs of the eight `SKILL.md` files (`twicc-projects`, `twicc-artifacts`,
  `twicc-processes`, `twicc-search`, `twicc-share`, `twicc-session` (`:144`,
  `:179`, `:182`), `twicc-workspaces`, `twicc-sessions`) becomes "2026-10-01".
  `twicc-session/SKILL.md:179` says "Since the 2026-09-15 cutover" — rewritten
  to the before/after form of its neighbours.
- `SKILLS-AND-CLI.md:18` ("Slim listings"), `:245`, `:246` (the `get`
  bullet), `:247`, `:255`, `:262`
  (`agents` options): the new default, `--full`, the cutover date, `topology`
  added to the list of commands that take the flags, and the notice — one line
  on stderr / in the RPC `warnings` key, never on MCP, none for
  `topology --no-processes`; a flagless `sessions` or `session agents` call
  prints two lines until the date (pagination and slim).
- `SKILLS-AND-CLI.md:378` (`topology` section): `--slim` and `--full` replace
  the `--full-sessions/--no-full-sessions` line, with the `process` block
  change and its date; `--full-sessions` is named once as a deprecated alias
  of `--full`.
- `twicc-sessions/SKILL.md:80-81`, `twicc-session/SKILL.md:19`, `:54`, `:364`:
  same as `SKILLS-AND-CLI.md:18`, notice included. The `agents` usage line
  (`twicc-session/SKILL.md:364`) replaces `[--slim]` with `[--slim | --full]`.
- `twicc-sessions/SKILL.md:103` (the `sessions get` usage line): add
  `[--slim | --full]`.
- `twicc-sessions/SKILL.md:108-183` ("Output format" › "Listing", "Key
  fields", "Batch lookup (`get`)"): the example shows the full payload as the
  default output. It becomes the default (slim) shape, with a sentence saying
  that `--full` returns the full payload and that the full payload stays the
  default until the date. In "Key fields" (`:160-171`), the fields absent from
  `SESSION_LISTING_FIELDS` (`slug`, `compacted`, `last_viewed_at`) are marked
  "`--full` only".
- `twicc-topology/SKILL.md:4`, `:44`, `:128`, `:150`: `--full` replaces
  `--full-sessions` in usage and examples, `--slim` added, the `process` block
  change and its date; `--full-sessions` is listed once as a deprecated alias
  of `--full`.
- `twicc-topology/SKILL.md:87`, `:110`, `:135`: the example's `process` blocks
  have five fields. The example keeps them (true until the date), and `:135`
  states that from the date the default block is `{"state": …}` and `--full`
  keeps the five fields.
- `twicc-topology/SKILL.md:138-142` ("Exit codes"): add `2` for `--slim`
  with `--full` / `--full-sessions`.
- `src/twicc/core/serializers.py:113` ("What a listing keeps under
  ``--slim``"): "the reduced projection a listing returns by default (or with
  ``--slim`` before the cutover)".
- Historical documents under `docs/superpowers/` and `docs/plans/` that mention
  `--slim` or `--full-sessions` are not edited.
- The three orchestration skills that recommend `sessions --spawned-by self --slim`
  (`twicc-orchestration/SKILL.md:160`, `twicc-orchestration-leader/SKILL.md:69`,
  `twicc-orchestration-manager/SKILL.md:52`) keep `--slim`: it is valid on both
  sides of the date.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: `0.100.0` →
  `0.101.0` (new flags).
- `docs/plans/2026-09-08-pagination-cutover-design.md` is not edited. It is a
  historical record; this document records the date move.

**CHANGELOG** — the user's call; to propose, not to write unasked.

- Two `[Unreleased]` entries are affected: "CLI and RPC listings"
  (`CHANGELOG.md:19`, names 2026-09-15 and "the 15th") and "Lighter CLI
  listings" (`:22`, presents `--slim` as a new opt-in).
- Two user-visible changes to the released `topology` command have no entry
  yet: its default `process` block becomes `{state}` from 2026-10-01, and
  `--full` / `--slim` arrive with `--full-sessions` kept as a deprecated alias.
  They need a new `[Unreleased]` entry, or a clause in the "CLI and RPC
  listings" one.

## Not in scope

- `whoami`'s redundant keys and nine-field `process` block.
- Merging the two slim projections.
- `session <id> workflows --full`: it concerns the workflow envelope. It already
  follows the rule (list reduced by default, `session workflow <id>` full).
- Removing the pre-cutover code paths after the date: a separate cleanup, as
  for pagination.
