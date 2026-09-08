# Pagination cutover — making `--paginated` the only shape

**Status:** design, not implemented
**Date:** 2026-09-08
**Cutover instant:** `2026-09-15T00:00:00`, local time on the machine running TwiCC

Paths below are relative to the repository root. Note that two different modules
are called `search.py`: `src/twicc/search.py` (the Tantivy layer) and
`src/twicc/cli/search.py` (the command). Both are always written in full.

## Problem

`--paginated` (commits `ba63b8c7`, `0940526c`) gave eleven listings an envelope
that answers "is there another page?". It is opt-in, so the answer only reaches
callers who already knew to ask for it — which is nobody, since the flag is a
week old.

The bare array is the shape we want gone. Removing it outright breaks every
existing script silently: an array becomes an object, `jq '.[0]'` returns null
instead of failing loudly.

So: a dated cutover, announced in-band, with one week of overlap.

## Decisions

| Point | Decision |
|---|---|
| Trigger | a fixed local date in the code, not a version |
| Before it | the command result is unchanged without the flag, plus a notice |
| Notice channels | best effort on three carriers — stderr, the log, a `warnings` key — with the guarantee that at least one is emitted on every notified channel |
| MCP | never notified |
| After it | the envelope **and** the 50-item page size become the default |
| `--paginated` after it | kept, no-op |
| Escape hatch after it | none |
| Opt-out of the notice | none |
| `search` | same cutover, its own notice text (its shape is already an object) |

Two of these deserve their rationale on the record.

**No escape hatch.** A `--no-paginated` would keep the bare shape alive
indefinitely, which is the thing this cutover exists to end. The migration path
is one flag, and a week is enough for a handful of scripts.

**No opt-out of the notice.** The nuisance is bounded — it disappears at the
cutover. An env var would give callers a way to silence it and be surprised on
the day, which is exactly what the notice prevents. Anyone who finds it noisy
has the intended exit: pass `--paginated`.

## The eleven listings

`projects`, `workspaces`, `sessions`, `artifacts`, `share`, `processes`,
`search`, and the four `session` sub-commands: `content`, `messages`, `agents`,
`workflows`.

On every path that windows a result, each calls `resolve_limit`
(`src/twicc/cli/_output.py:90`) once and emits through `emit_list`
(`src/twicc/cli/_output.py:112`). Two exceptions, both already known and both
harmless here: `--tail` on `content` (`src/twicc/cli/session.py:133`) and on
`messages` (`src/twicc/cli/session.py:226`) sizes its own window and never calls
`resolve_limit`; and every `emit_error` exit leaves through neither helper. The
notice fires before any of those branch, so none of them is a gap.

**Batch-lookup commands are out of scope**, and stay bare arrays:
`sessions get`, `projects get`, `workspaces get`, `processes get`,
`processes stop`, `processes wait` (`src/twicc/cli/sessions_get.py:89`,
`projects_get.py:115`, `workspaces_get.py:61`, `processes_get.py:94`,
`processes_stop.py:262`, `processes_wait.py:221`). Their output is one entry per
input id, in input order — a mapping, not a page. There is nothing to paginate
and no `has_more` to answer. Post-cutover `twicc sessions` therefore returns an
object while `twicc sessions get a b` still returns an array; that asymmetry is
deliberate.

## The cutover instant

One module-level constant, in `src/twicc/cli/_output.py` next to
`PAGINATED_DEFAULT_LIMIT`:

```python
#: Local wall-clock instant at which the bare listing shape stops being emitted.
#: Naive on purpose: "midnight on the 15th" means midnight where the instance
#: runs, not in UTC. Changing the date is a one-line edit here; nothing else in
#: the codebase encodes it.
PAGINATION_CUTOVER = datetime(2026, 9, 15)  # noqa: DTZ001 — local time, as announced


def pagination_is_default(now: datetime | None = None) -> bool:
    """True once the envelope is the only shape. ``now`` is injectable for tests.

    ``now`` must be naive: it is compared against a naive constant, and mixing
    the two raises ``TypeError``.
    """
    return (now or datetime.now()) >= PAGINATION_CUTOVER  # noqa: DTZ005 — local time
```

Both `noqa` markers are load-bearing: `src/twicc` is ruff-clean today, and
`DTZ001` / `DTZ005` are not in the `ignore` list at `pyproject.toml:117`. The
inline-marker-plus-rationale form is the codebase's existing convention for a
deliberate naive datetime — `src/twicc/log_retention.py:86`,
`src/twicc/quota_wakeup_task.py:186`, `tests/test_log_retention.py:18`.

**Local time, deliberately naive.** The announcement says "from the 15th", and
the operator reads that on their own wall clock. A UTC instant would flip at
02:00 local for a CEST instance and at 17:00 on the 14th for a US-Pacific one —
the second being *early*, which is the direction that surprises people. Comparing
two naive datetimes makes the rule say exactly what the notice promises, on every
host, with no zone to reason about.

The cost is that instances in different zones flip at different absolute
instants. That matters nowhere: each instance is self-contained, and the one
cross-machine path (`--remote`) already resolves everything on the remote host,
its own clock included.

This is one of the rare places where a naive `datetime` is the correct type
rather than an oversight — worth a comment on the constant so nobody
"fixes" it into `timezone.now()` later.

**Changing the date** before release means editing that one line. The date
rendered in the notice is formatted from the constant, never written twice.

**The clock is trusted, without a floor.** A host skewed into 2027 flips today,
with no notice period; a host stuck in 2020 never flips. Both are accepted: a
self-hosted tool whose clock is wrong has larger problems (share expiry, cron
scheduling, session timestamps all break first), and a sanity floor would need a
second trusted source we do not have.

**The predicate is evaluated per invocation.** A loop started at 23:59 on the
14th changes output shape mid-run. Accepted; latching the answer at process
start would make a long-lived backend keep the old shape for days after the
date.

**Testing** monkeypatches `PAGINATION_CUTOVER`. Every pinned value and every
`now=` **must be naive** — `datetime(...)` or `datetime.now()`, never
`django.utils.timezone.now()`. Comparing an aware datetime against the naive
constant raises `TypeError: can't compare offset-naive and offset-aware
datetimes`, and the trap is live: `tests/test_cli_pagination_envelope.py:16`
already imports `django.utils.timezone` and `:34` builds its fixtures with
`timezone.now()`, so the obvious pin is the wrong one. A dedicated test feeds the
predicate an aware `now=` and asserts the failure is a clear one, not a
`TypeError` surfacing from inside a command body.

The `now=` parameter exists for
unit-testing the predicate itself; it is deliberately **not** threaded through
the eleven command signatures, which would turn "one line each" into an
eleven-signature change and still leave the RPC and MCP paths untestable that
way. Monkeypatching the constant covers every path uniformly.

## Which caller is speaking

Three channels reach the same command bodies. Only two of them have a human at
the end.

| Channel | Signature at emission time | Carriers that fire | Guaranteed |
|---|---|---|---|
| Terminal CLI | `_capture` unset (`src/twicc/cli/_output.py:39`) | stderr, and the log when configured | stderr |
| RPC over HTTP | `_capture` set, no MCP marker | the `warnings` key, and the log | the key |
| MCP | `_capture` set, MCP marker set | none, by decision | — |

**The MCP marker does not exist yet and must be added.** Two existing
ContextVars look like they would do the job and neither does:

- `transport.backend_loop` is set by *both* paths — `src/twicc/rpc/views.py:123`
  for RPC and `src/twicc/mcp/server.py:90` for MCP.
- `forced_session_id` (`src/twicc/mcp/server.py:89`) is set only on the MCP
  path, but it would **fail open**: its value comes from
  `_session_id_from_request` (`src/twicc/mcp/server.py:133`), which returns
  `None` when the request context is absent or the token does not resolve.
  Reading "is it set?" would then classify those MCP callers as RPC and warn
  them.

So: a dedicated `ContextVar` in `src/twicc/mcp/identity.py`, set in
`execute_prepared` alongside the two it already sets, and read by the notice
recorder. Explicit, and it cannot fail open.

**Batch MCP tools are covered by that one marker.** `src/twicc/mcp/batch.py:154`
dispatches each child through `self.execute`, which
`src/twicc/mcp/server.py:157` wires to a closure over `execute_prepared`, so
every batched child runs inside the same context. No separate handling.

Agents need no notice: the tool schema carries `paginated` and its description
(see the help-text change in Phase 2), and after the cutover the tool simply
returns the new shape.

## Phase 1 — before the cutover

The command result does not change. A caller that omits `--paginated` gets the
same bytes it gets today. (The RPC *envelope* around that result does gain a
`warnings` key — additive, see Code changes.)

What is added is one notice per invocation, on the listing commands only. Two
texts, because the two migrations differ:

**The ten array-returning listings:**

```
twicc: from 2026-09-15, `sessions` returns {"items": [...], "pagination": {...}}
instead of a bare array, and pages at 50 by default. Pass --paginated now to get
that shape today; after the date the flag is accepted but does nothing.
```

The page-size clause is rendered only when it is true for that command: `share`
already defaults to 50 (`src/twicc/cli/share.py:61`), so its notice announces
the shape change alone.

**`search`**, whose current shape is already an object
(`src/twicc/search.py:1009`):

```
twicc: from 2026-09-15, `search` renames `hits` to `items` and `total_hits` to
`pagination.total`, moves `limit`/`offset` under `pagination`, and pages at 50
by default.
Pass --paginated now to get that shape today; after the date the flag is
accepted but does nothing.
```

**Where it fires.** Once per command invocation, at the top of the command body,
before any query runs. The notice is about *how the command was called*, so it
does not depend on whether the command later succeeds: an `emit_error` raised
**inside the command body** still carries it, and on the RPC path the envelope
is then `{"exit_code": 1, "error": …, "warnings": […]}`. That is intended.

Validation that happens in the Typer wrapper runs *before* the body and
therefore before the notice — `--include-hidden` with `--only-hidden`
(`src/twicc/cli/__init__.py:296`), `--project` with `--workspace` (`:309`), and
their equivalents for `artifacts` (`:548`), `processes` (`:956`) and `search`
(`:1412`). Those exits carry no `warnings`. Acceptable: the call was malformed,
and the caller has a more urgent message to read.

Placement has three per-command constraints:

- In `session content` the line must sit **above** the selector guard
  (`src/twicc/cli/session.py:118`) — see Phase 2, item 4.
- In `src/twicc/cli/search.py` it must sit **above** line 39, before the
  `if …: django.setup()` block that ends at `:50`. Inside the block it would
  only fire for DB-backed filters; below it, a filtered search would write a log
  line while an unfiltered one would not.
- Everywhere else it goes at the top of the body. Nine of the eleven open with
  `django.setup()`, so there the line lands right after it — the position that
  makes the log channel work, below. `workspaces` has no such call and `search`'s
  is conditional, so in those two the line is simply first.

**stderr, not stdout.** `twicc sessions | jq` keeps working. The known casualty
is `twicc sessions 2>&1 | jq`, which would feed the notice into the JSON parser.
That pattern is fragile by construction — `src/twicc/cli/__init__.py:1647`
already prints `Warning: …` to stderr on any local invocation whose `.env`
dropped an inherited provider-home variable, and
`src/twicc/cli/_drop_request/aliases.py:169` prints a `note:` on some successful
commands. Adding one more diagnostic does not create the hazard; it makes an
existing one more likely to be hit.

**Three carriers, each best effort, one guarantee.** No carrier is mandatory on
its own. The rule is that **at least one is emitted on every notified channel**,
via the carrier native to that channel: stderr for a terminal, the envelope key
for an RPC reply. The log is a bonus everywhere and a requirement nowhere.

*Emitted*, not *received* — the caller can always throw it away, and two cases
are accepted rather than defended against:

- `twicc workspaces 2>/dev/null` discards the notice, and those two Django-free
  commands have no log record to fall back on (below).
- An unhandled exception inside a command returns a 500 from the
  `except Exception` block at `src/twicc/rpc/views.py:126` (the return is at
  `:135`) **before** the envelope at `:138`, so the notice is dropped. Same reasoning as the wrapper-level validation exits: the caller
  has a more urgent message to read.

**The stderr write must go through `typer.echo(msg, err=True)`, never
`print(msg, file=sys.stderr)`.** Closing fd 2 (`twicc sessions 2>&-`) sets
`sys.stderr` to `None`, and `print(file=None)` falls back to **stdout** — which
would inject the notice into the JSON payload, the exact failure the previous
section exists to prevent, and without any `2>&1` in sight. Click's `echo`
guards on `file is None` and drops the write instead. The two precedents cited
above (`src/twicc/cli/__init__.py:1647`,
`src/twicc/cli/_drop_request/aliases.py:169`) both use bare `print` and are
therefore **not** the model to copy here.

That is what makes the log's irregularity harmless rather than a gap to close:

- The record is emitted at `warning` level. Before `django.setup()` the logging
  tree is unconfigured and `warning` is exactly the level Python's `lastResort`
  handler passes, so the notice would print to stderr a second time. The
  recorder therefore guards the log call on
  `logging.getLogger("twicc").hasHandlers()` — no handler, no log line, no
  duplicate.
- Two commands are Django-free on the terminal path and so write no log line
  there: `src/twicc/cli/workspaces.py` never calls `django.setup()` at all (it
  reads `workspaces.json` from disk), and `src/twicc/cli/search.py` places the
  notice above its conditional `django.setup()` (`:39`–`:50`), so the logger is
  never configured *at the moment the notice is recorded* — filtered or not.
  Both still print to stderr, so both are covered.
  Forcing `django.setup()` in either just to log a deprecation would cost them
  the bootstrap they were written to avoid.
- Over `/rpc/` that irregularity disappears anyway: the command runs inside the
  backend, where `src/twicc/settings.py:374` has already wired the `twicc`
  logger, so `hasHandlers()` is true and both do write a record — on top of the
  envelope key that was already guaranteed.

**Log volume.** `src/twicc/settings.py:370` sets `"delay": True` on the file
handler, with the stated intent that "a CLI command that never logs should not
touch the file at all". Phase 1 makes the listings write one record per invocation for a week, on every
channel whose logger is configured. Agent traffic through the MCP tools is *not*
in that count — MCP records nothing at all — so only agents shelling out to
`twicc` contribute. Accepted: one line per call for seven
days, against the value of the notice surviving a shell nobody is watching. If
that proves noisy, dropping the log channel for the terminal path is a one-line
change and loses nothing on the RPC path.

## Phase 2 — after the cutover

`pagination_is_default()` returns True, and inside each command `paginated`
becomes True regardless of what the caller passed. Because every downstream
decision already reads that one flag, the flip propagates on its own — but it
reaches **five** sites, not three, and two of them need saying out loud:

1. `resolve_limit` applies the 50-item default where the caller gave no
   `--limit`.
2. `total` is counted.
3. `emit_list` emits the envelope.
4. **`session content`'s bare-call guard self-disables.** The guard
   (`src/twicc/cli/session.py:118`) rejects a call with no selector, and
   `--paginated` is one of the selectors it accepts — because a bounded page
   cannot dump a session. With the flag always true, the guard can never fire:
   `twicc session <id> content` stops being an error and returns the first 50
   items. That is coherent (the payload is bounded, which is all the guard ever
   protected against) and it is the reason the flip must sit **above** the
   guard. It is not silent, though: the `emit_error` at
   `src/twicc/cli/session.py:122` becomes unreachable, and
   `test_content_still_refuses_a_bare_call` must pin a pre-cutover clock.
5. **`processes`' empty-scope early exit** emitted before `resolve_limit` ran,
   so it reported `limit: null` while every other path reported the resolved
   value. Fixed ahead of this design by moving `resolve_limit` above the exit
   (`src/twicc/cli/processes.py:113`), with a regression test; noted here
   because the cutover would otherwise have made the inconsistency permanent.

`--paginated` stays declared on all eleven commands and stays in the MCP schema.
Passing it changes nothing; not passing it changes nothing either. Scripts
migrated during phase 1 keep working untouched, which is the whole point of not
deleting the flag.

No notice fires after the cutover: there is nothing left to announce.

**Only the flip is automatic.** It needs no release — the date does it. Every
other item in this section is a static edit that would be *false* during the
overlap week, so the help strings below, and the phase-2 docs pass in Not in
scope, must ship in a release cut on or after 2026-09-15.

**The help texts become false and must change with the behaviour.** They are not
documentation — Click help becomes the JSON-Schema `description`
(`src/twicc/rpc/schema.py:88`) and then the MCP `Tool.input_schema`
(`src/twicc/mcp/tools.py:89`), so a stale string actively misinforms every
agent. Eleven strings in two places:

- `PAGINATED_HELP` (`src/twicc/cli/_output.py:104`), which currently ends "Off
  by default: the bare shape is unchanged".
- **Ten** `--limit` strings in `src/twicc/cli/__init__.py`, at lines 81, 154,
  224, 394, 420, 435, 463, 539, 886 and 1345 (1345 is `search`, not `share`).
  Eight read "(default: 20; 50 with --paginated)"; the two `session` ones at 394
  and 420 read "(default: no limit; 50 with --paginated)".

`share`'s `--limit` (`src/twicc/cli/__init__.py:661`) is deliberately **not**
touched: it reads "(default: 50)", never mentions the flag, and stays true
because `src/twicc/cli/share.py:61` already passes `default=50`.

**`content` and `messages` lose data, not just shape.** Seven of the ten array
listings widen at the cutover, 20 → 50; `share` already pages at 50 and does not
move. These two go the other way: they pass
`default=None` (`src/twicc/cli/session.py:134`, `:229`), so an unfiltered call
returns *everything* today and at most 50 items afterwards. `twicc session X
messages | jq length` silently drops the tail rather than failing on a shape
change — the only place in the eleven where that happens, and the reason the
notice's "pages at 50 by default" clause matters as much as the shape sentence.

**Cost.** Post-cutover every unpaginated `content` call and every `messages`
call without `--contains` pays a `COUNT(*)`
(`src/twicc/cli/session.py:148`, `:261`) where it pays none today.
`messages --contains` is unaffected — it counts in memory
(`src/twicc/cli/session.py:256`). Order of magnitude on the instance measured
for `ba63b8c7`: the heaviest session holds ~186 000 raw items and the most
talkative ~8 000 messages, so the count is the same order as the page fetch
itself. Worth re-measuring on real sessions before the date; it is a consequence
of the decision, not a reason to revisit it.

## Code changes

**`src/twicc/cli/_output.py`** — the constant, `pagination_is_default()`, the
notice recorder, and the per-command entry point.

That entry point is an assignment, since a helper cannot rebind a caller's
local:

```python
paginated = pagination_notice("sessions", paginated, default_limit=20)
```

It returns `True` past the cutover and its argument otherwise, and records the
notice when the argument was falsey and the cutover has not passed.

Three inputs shape the message:

- the **command name**, quoted in the text. Sub-commands pass their full CLI
  path — `"session content"`, not `"content"` — so the notice names something
  the reader can paste back;
- **`default_limit`**, which is what the command already passes to
  `resolve_limit`. It decides whether the "pages at 50 by default" clause is
  rendered: `50` means the page size is not changing, which exempts `share`
  without a special case;
- **`shape=`**, defaulting to `"array"`. `src/twicc/cli/search.py` passes
  `shape="object"` and gets the second text — the one naming the key renames
  instead of "a bare array". It is a two-branch table, not a per-command one:
  `search` is the only listing whose historical shape is not an array, and the
  parameter says *why* it differs rather than hard-coding its name.

The recorder is a `ContextVar` holding a list, not a field on `_Sink`: the
terminal path has no sink at all (`_capture` is unset), so the sink cannot be
the carrier. Reading the MCP marker means importing `twicc.mcp.identity`, which
pulls in `django.utils.crypto` (`src/twicc/mcp/identity.py:28`);
`src/twicc/cli/_output.py` today imports only `contextvars`, `sys`, `orjson` and
`typer` and is imported by every command. This design adds a stdlib `datetime` to
that list — negligible, unlike `django.utils.crypto`. The import must therefore be lazy
**and** guarded by `_capture.get() is not None` — the marker can only ever be
set on an in-process call, so the terminal path must never reach the import at
all. `src/twicc/cli/__init__.py:1623` names keeping the local path fast as an
explicit value.

`emit_list` itself needs no cutover logic: by the time it runs, `paginated`
already carries the resolved value. Its docstring says "``items`` for the nine
array-returning commands" (`src/twicc/cli/_output.py:126`) — wrong when it was
written, since `ba63b8c7` added the docstring and `session content`'s
`emit_list` call in the same commit. Should read ten.

**The eleven command bodies** — one line each, at the top of the body, with the
three placement constraints listed in Phase 1: `projects.py`, `sessions.py`,
`workspaces.py`, `artifacts.py`, `processes.py`, `share.py`, `search.py`, and
the four in `session.py`.

**`src/twicc/rpc/invoker.py`** — `InvocationResult` gains a `warnings` field,
and `invoke()` owns its lifecycle, mirroring exactly what it already does with
`sink`:

```python
notices: list[str] = []
tok_notices = _notices.set(notices)
...
finally:
    _notices.reset(tok_notices)
return InvocationResult(..., warnings=tuple(notices))
```

The read must go through the **local** `notices` name, not `_notices.get()`:
the `return` at `src/twicc/rpc/invoker.py:62` runs *after* the `finally` at
`:60`, so a ContextVar read there would already see the outer value. This is why
`sink` is a local too.

The `reset` is symmetry with `_capture.reset(tok)` (`:61`), not a leak fix —
`invoke()` binds a fresh list every call, and both callers reach it through
`asyncio.to_thread`, which runs the callable in a *copied* context, so a
worker-side `set()` cannot escape to `src/twicc/rpc/views.py` in the first
place. That copy is also why the read-back has to happen inside `invoke()`
rather than in the caller.

`InvocationResult` is a `NamedTuple` constructed positionally in at least one
test (`tests/test_mcp_endpoint.py:253`, three arguments), so the field goes
**last** and defaults to `()` — a tuple, not a list, since NamedTuple defaults
are shared across instances. The three other construction sites
(`src/twicc/rpc/invoker.py:62`, `tests/test_mcp_server.py:90`,
`tests/test_rpc_auth.py:55`) are keyword-based and unaffected.

**`src/twicc/rpc/views.py:138`** — the envelope gains `warnings`, **omitted
when nothing was recorded** rather than sent as `[]`. Post-cutover no notice
ever fires, so the steady-state envelope goes back to exactly the three keys it
has today and the overlap leaves no permanent trace. Verified additive: `src/twicc/cli/_remote.py:763` only tests for the presence of
`exit_code`, `src/twicc/rpc/openapi.py:7` declares no
`additionalProperties: false`, and no test asserts an exact key set on this
envelope.

**`src/twicc/rpc/openapi.py:7`** — `_ENVELOPE_SCHEMA` enumerates the three
properties and is published at `/rpc/openapi.json`. Adding the key without
declaring it there leaves the public contract stale.

**`src/twicc/cli/_remote.py:774`** — prints each warning to stderr, next to the
existing `error` handling, so `twicc --remote sessions` warns like a local run.
Through `typer.echo(..., err=True)`, not by copying the bare
`print(error, file=sys.stderr)` at `:776` — that neighbour has the closed-fd
problem described in Phase 1.

**`src/twicc/mcp/identity.py`** — the marker ContextVar.

**`src/twicc/mcp/server.py:89`** — set the marker alongside `forced_session_id`
and `backend_loop`, and reset it in the same `finally` they use (`:95`–`:97`). The envelope at `src/twicc/mcp/server.py:123` needs no
filter: nothing was recorded in the first place.
`tests/test_mcp_server.py:28` already asserts
`set(result) == {"exit_code", "result", "error"}` and keeps that honest.

**Existing tests — four files, not one.** Every test that calls a listing and
indexes the result as a list breaks once the envelope is the default. None of
them controls the clock.

In `tests/test_cli_pagination_envelope.py` (32 tests), nine go red:

| Line | Test | Why |
|---|---|---|
| 58 | `test_without_the_flag_the_payload_is_still_a_bare_array` | `assert isinstance(payload, list)` |
| 129 | `test_messages_without_a_limit_still_returns_everything_unpaginated` | `len()` of an envelope dict is 2 |
| 233 | `test_content_still_refuses_a_bare_call` | the guard no longer fires |
| 240 | `test_content_accepts_a_window_as_the_only_selector` | `len()` of an envelope dict is 2 |
| 247, 256 | `test_content_range_and_window_stack`, `test_content_window_ranks_matches_not_lines` | iterating a dict yields `str` keys |
| 266 | `test_content_no_match_is_an_empty_list_not_an_error` | `{...} != []` |
| 309 | `test_the_flag_overrides_a_command_s_own_default` | its premise ("20 without the flag") dies |
| 357 | `test_content_tail_returns_the_last_matches` | iterating a dict yields `str` keys |

And one goes **silently green while testing nothing**: line 403,
`test_content_tail_is_a_selector_on_its_own`, asserts `len(read(...)) == 2` —
post-cutover an envelope dict also has length 2. It must be rewritten, not just
pinned.

Five more break in three other files, each iterating a bare array that becomes a
dict:

| File | Line | Test |
|---|---|---|
| `tests/test_artifact_bookmarks.py` | 248 | `test_cli_artifacts_listing_and_scope_filter` |
| `tests/test_cli_processes_listing.py` | 74 | `test_processes_applies_spawned_by_scope_before_pagination` |
| `tests/test_cli_processes_listing.py` | 91 | `test_processes_applies_hidden_filter_before_pagination` |
| `tests/test_cli_processes_listing.py` | 115 | `test_processes_annotation_narrows_spawn_tree_scope` |
| `tests/test_cli_resolve_filters.py` | 84 | `test_sessions_cli_returns_standalone_session_filtered_by_its_own_spawn_tree` |

`tests/test_share_cli_reads.py` is **not** affected: its `_list()` helper
monkeypatches `emit_list` and captures `items` positionally (`:70`, and again
at `:270`), so it is shape-agnostic — the pattern the other four should adopt
where it fits.

Every pre-cutover assertion in all four files must pin the clock below the
constant.

## `--remote` crosses two versions

The command body runs on the **remote** host, so the shape is decided by the
remote's local clock and the remote's version, not the caller's. With a local
cutover that is not a complication but the reason the rule is coherent: one
instance, one wall clock, one answer.

A post-cutover client against a pre-cutover remote gets a bare array and no
local notice unless that remote is new enough to fill `warnings`. A pre-cutover
client against a post-cutover remote gets the envelope a week early. Neither is
a failure — `--remote` has always executed remote code — but it is the one place
where "one instant in the code" is not one instant.

A local cutover adds a second, more likely skew: two instances on the *same*
version in different zones disagree about the shape for up to ~26 hours. Both
cases belong in the release note.

## Testing

Both phases are exercised by monkeypatching `PAGINATION_CUTOVER` around each
call.

- Pre-cutover, no flag: result byte-identical to today, one notice recorded.
- Pre-cutover, with the flag: envelope, no notice.
- Post-cutover, no flag: envelope, 50-item page, no notice.
- Post-cutover, with the flag: identical to the line above.
- The notice names the command and the date, and the date comes from the
  constant: change the constant, the message follows.
- `search` gets its own text, naming the four key renames rather than "a bare
  array"; `share`'s omits the page-size clause.
- Channel routing: terminal CLI writes to stderr; RPC fills `warnings` in the
  envelope; MCP records nothing at all, including through `batch` / `batch_read`.
- A command that exits through `emit_error` still carries its notice into the
  RPC envelope alongside a non-zero `exit_code`.
- `invoke()` resets the recorder between invocations: two successive calls in
  the same context do not accumulate.
- **At least one carrier is emitted on every notified channel**: stderr on the
  terminal path, the `warnings` key on the RPC path, asserted per command.
- The log carrier is asserted against an **explicitly controlled** handler set,
  never inferred from whether the command called `django.setup()`. The suite
  cannot infer it: `src/twicc/settings_test.py:54` empties `LOGGING`, so the
  `twicc` logger keeps `propagate=True` (production sets `propagate: False`,
  `src/twicc/settings.py:377`) and inherits the handlers pytest installs on the
  root logger — `hasHandlers()` is therefore `True` in every test, including for
  `twicc workspaces`. The test drives both branches itself — installing a `twicc` handler for the
  positive one, and for the negative one clearing the root handlers *and*
  setting `propagate=False`, since stripping `twicc.handlers` alone cannot reach
  the no-handler state while propagation is on. It also asserts that nothing
  duplicates onto stderr via `lastResort`.
- `typer.echo(..., err=True)` is used rather than `print(file=sys.stderr)`:
  asserted by running a command with `sys.stderr` monkeypatched to `None` and
  checking stdout stays pure JSON.
- The predicate compares naive local datetimes: a test that sets
  `PAGINATION_CUTOVER` one minute ahead of `datetime.now()` sees the old shape
  and one minute behind sees the new one, whatever the host's zone.
- `--tail` on `content` and `messages` still flips correctly, since it bypasses
  `resolve_limit`.
- Post-cutover, `twicc session <id> content` with no selector succeeds and
  returns at most 50 items.
- Post-cutover, `processes` with an empty filiation scope reports the same
  window as any other call.
- `/rpc/openapi.json` declares `warnings` on `RpcEnvelope` — no test reads that
  document today, so this is a new one.
- `twicc --remote <listing>` prints the remote's notice on the local stderr.
- `PAGINATED_HELP` and the ten `--limit` strings no longer claim the bare shape
  is the default; asserted against the generated MCP tool schema, which is where
  they actually land, rather than against the literals.

## Not in scope

**Removing the bare-shape code.** After the cutover, `emit_list`'s `plain=`
argument stops being *emitted* — but the dict it carries is built by
`raw_search` (`src/twicc/search.py:1009`), handed over at
`src/twicc/cli/search.py:102`, passed as `plain=` at `:126` **and** reused
through `extra=` at `:130`. Only the parameter is dead, not the value. Deleting it is a separate cleanup once the date has passed.

**Docs and skills.** `SKILLS-AND-CLI.md` and the eight `SKILL.md` files describe
the flag as opt-in with an unchanged default. They need a phase-1 pass
announcing the date and a phase-2 pass rewriting the default, each with a
`plugin.json` bump. Ordinary doc work, not design decisions — unlike the help
strings above, which are code.

**The CHANGELOG entry**, which is the user's call.
