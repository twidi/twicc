"""Centralized JSON output (and error) for the CLI.

Every structured command prints its result through :func:`emit_json` and
reports failures through :func:`emit_error`. These two functions are the
single output choke points for the whole CLI, which keeps the on-the-wire
shape uniform (indented UTF-8 JSON on stdout; plain text on stderr).

They are also the seam the in-process RPC layer hooks into: when an API
caller sets the :data:`_capture` ContextVar to a :class:`_Sink`, the result
and the error message are captured into the sink instead of being written
to stdout/stderr. ContextVars are isolated per-thread and per-asyncio-task
and propagate across ``asyncio.to_thread``, so concurrent /rpc/ invocations
never clash on a global stream. When the ContextVar is unset (normal CLI
use), behaviour is exactly as before.

Human-only commands (``password``, the ``claude`` / ``codex`` passthroughs,
``run``) print their own text and never call these.
"""

from __future__ import annotations

import contextvars
import logging
import sys
from datetime import datetime

import orjson
import typer


class _Sink:
    """Capture target for one in-process invocation."""

    __slots__ = ("error", "result")

    def __init__(self) -> None:
        self.result = None
        self.error: str | None = None


_capture: contextvars.ContextVar[_Sink | None] = contextvars.ContextVar(
    "emit_capture", default=None
)


def in_api_mode() -> bool:
    """True when running under the in-process RPC invoker (a capture sink is set).

    Used to reject CWD-relative inputs over the API, where the caller has no
    working directory the server could resolve a relative path against.
    """
    return _capture.get() is not None


def emit_json(payload) -> None:
    """Emit ``payload`` as the command's structured result.

    Normal CLI use: indented UTF-8 JSON to stdout with a trailing newline.
    API mode (capture set): stored on the sink, nothing written.
    """
    sink = _capture.get()
    if sink is not None:
        sink.result = payload
        return
    sys.stdout.buffer.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def emit_error(message: str, *, code: int = 1) -> None:
    """Emit a command failure and stop the command. Always raises ``typer.Exit``.

    Normal CLI use: ``message`` to stderr, then exit with ``code``.
    API mode (capture set): ``message`` stored on the sink, then ``typer.Exit``
    so the invoker can read both the message and the code.
    """
    sink = _capture.get()
    if sink is not None:
        sink.error = message
        raise typer.Exit(code)
    typer.echo(message, err=True)
    raise typer.Exit(code)


#: Page size ``--paginated`` falls back to when the caller passes no ``--limit``.
#: The flag promises a page and a "does another follow?" answer, which only means
#: something if the page is actually bounded — so it supplies its own bound rather
#: than inheriting a per-command default (or, on ``session messages`` / ``content``,
#: no default at all).
PAGINATED_DEFAULT_LIMIT = 50


def resolve_limit(limit: int | None, *, paginated: bool, default: int | None) -> int | None:
    """Page size to apply: the caller's, else the mode's.

    ``limit`` is ``None`` when the option was not passed — every paginated
    command declares it that way so an explicit ``--limit 20`` stays
    distinguishable from the default. ``default`` is what the command uses
    without ``--paginated`` (``None`` on the two commands that return
    everything by default).
    """
    if limit is not None:
        return limit
    return PAGINATED_DEFAULT_LIMIT if paginated else default


#: Local wall-clock instant at which the bare listing shape and the full session
#: payload stop being the defaults. Naive on purpose: "midnight on the 1st"
#: means midnight where the instance runs, not in UTC — the announcement and the
#: code then say the same thing on every host. Changing the date is a one-line
#: edit here; nothing else in the codebase encodes it. Design:
#: docs/plans/2026-09-08-pagination-cutover-design.md and
#: docs/plans/2026-09-23-session-listing-slim-cutover-design.md
LISTING_CUTOVER = datetime(2026, 10, 1)  # noqa: DTZ001 — local time, as announced


def listing_cutover_passed(now: datetime | None = None) -> bool:
    """True once the envelope is the only shape and ``--paginated`` is a no-op,
    and the reduced session projection is the default and ``--slim`` a no-op.

    ``now`` is injectable for tests and **must be naive**: it is compared against
    a naive constant, and mixing the two raises ``TypeError``.
    """
    if now is not None and now.tzinfo is not None:
        raise ValueError(
            "listing_cutover_passed() compares local wall-clock times: pass a naive "
            "datetime (datetime.now(), not timezone.now())."
        )
    return (now or datetime.now()) >= LISTING_CUTOVER  # noqa: DTZ005 — local time


#: Up to one notice per migration per invocation (a flagless ``sessions`` call
#: records two), drained by the RPC invoker into its result. A ContextVar rather
#: than a field on ``_Sink``: the terminal path has no sink.
_notices: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "cutover_notices", default=None
)

_NOTICE_LOGGER = logging.getLogger("twicc.cli.cutover")


def _in_mcp_call() -> bool:
    """True when the running command was dispatched by the MCP server.

    Agents read ``paginated``, ``slim`` and ``full`` off the tool schema and get
    the new shape without being told, so they are the one caller that is never
    notified. The import is
    lazy and reached only in API mode: ``twicc.mcp.identity`` pulls in
    ``django.utils.crypto``, and this module is imported by every command.
    """
    from twicc.mcp.identity import mcp_call

    return mcp_call.get()


def pagination_notice(command: str, paginated: bool, *, default_limit: int | None,
                      shape: str = "array") -> bool:
    """Resolve the pagination mode, announcing the cutover while it is still ahead.

    Returns what ``paginated`` should be from here on: always ``True`` past the
    cutover, where the flag is accepted but means nothing. Before it, the
    caller's own value — and when the caller passed nothing, a notice is
    recorded so the migration does not arrive unannounced.

    ``command`` is the full CLI path (``"session content"``, not ``"content"``)
    so the message names something the reader can paste back. ``default_limit``
    is what the command already hands to :func:`resolve_limit`; ``50`` means its
    page size is not changing, which exempts ``share`` from that clause without
    a special case. ``shape`` is ``"object"`` for the one listing whose current
    output is not a bare array.
    """
    if listing_cutover_passed():
        return True
    if paginated:
        return paginated  # already migrated: nothing to say
    if _capture.get() is not None and _in_mcp_call():
        return paginated

    when = LISTING_CUTOVER.strftime("%Y-%m-%d")
    if shape == "object":
        change = (
            f"`{command}` renames `hits` to `items` and `total_hits` to "
            "`pagination.total`, moves `limit`/`offset` under `pagination`"
        )
    else:
        change = (
            f"`{command}` returns {{\"items\": [...], \"pagination\": {{...}}}} "
            "instead of a bare array"
        )
    if default_limit != PAGINATED_DEFAULT_LIMIT:
        change += f", and pages at {PAGINATED_DEFAULT_LIMIT} by default"
    message = (
        f"twicc: from {when}, {change}. Pass --paginated now to get that shape "
        "today; after that date the flag is accepted but does nothing."
    )
    _record_notice(message)
    return paginated


def slim_notice(command: str, slim: bool, full: bool, *, kind: str = "listing") -> bool:
    """Resolve the slim mode, announcing the cutover while it is still ahead.

    Returns what ``slim`` should be from here on: ``--full`` always wins, then
    ``--slim``; with neither, ``True`` past the cutover and ``False`` before it,
    where a notice is recorded so the migration does not arrive unannounced.

    The cutover shortcut comes after the two flags, unlike in
    :func:`pagination_notice`: placed first, it would turn ``--full`` into a
    no-op past the date. ``command`` is the full CLI path, as for :func:`pagination_notice`.
    ``kind`` is ``"topology"`` for the one command whose change is its
    ``process`` block, its sessions being reduced already.
    """
    if slim and full:
        raise ValueError("slim and full are mutually exclusive.")
    if full:
        return False
    if slim:
        return True
    if listing_cutover_passed():
        return True
    if _capture.get() is not None and _in_mcp_call():
        return False

    # Formatted here, not from _CUTOVER_DATE: a test that pins the constant
    # must see its own date in the notice.
    when = LISTING_CUTOVER.strftime("%Y-%m-%d")
    if kind == "topology":
        change = (
            f"`{command}` reduces each node's `process` block to {{\"state\"}} by "
            "default. Pass --full to get the full session and process block on "
            "every node"
        )
    else:
        change = (
            f"`{command}` returns the reduced session projection by default "
            "(what --slim returns today) instead of the full payload. Pass --full "
            "to keep the full payload"
        )
    message = (
        f"twicc: from {when}, {change}, or --slim to get the new shape today; "
        "after that date --slim is accepted but does nothing."
    )
    _record_notice(message)
    return False


#: Retired command → its replacement. The single source for the wrappers, the
#: help texts, and the RPC / MCP refusals. Design:
#: docs/plans/2026-09-23-process-commands-removal-design.md
RETIRED_COMMANDS = {
    "processes": "sessions --active",
    "processes get": "sessions get",
    "processes stop": "sessions stop",
    "processes wait": "sessions wait-reply",
    "process": "session <id>",
    "process stop": "session <id> stop",
    "process wait": "session <id> wait-reply",
}


def removal_message(command: str) -> str:
    """The error a retired command answers with past the cutover.

    One builder for every channel — the terminal, the RPC view and the MCP
    pre-check — so the three never drift. The date is read at call time.
    """
    when = LISTING_CUTOVER.strftime("%Y-%m-%d")
    return (
        f"Error: `twicc {command}` was removed on {when}. "
        f"Use `twicc {RETIRED_COMMANDS[command]}` instead."
    )


def removed_command(command: str) -> None:
    """Announce a retired command before the cutover; refuse it after.

    Exit 64 (bad usage), not 1: ``process <id>`` already exits 1 for "no
    running process", and a script testing that code must not read a retired
    command as "not running".
    """
    if listing_cutover_passed():
        emit_error(removal_message(command), code=64)
    if _capture.get() is not None and _in_mcp_call():
        return
    when = LISTING_CUTOVER.strftime("%Y-%m-%d")
    _record_notice(
        f"twicc: `twicc {command}` stops working on {when}. "
        f"Use `twicc {RETIRED_COMMANDS[command]}` instead."
    )


def refuse_if_removed(group: str, subcommand: str | None) -> None:
    """Past the cutover, refuse ``group`` or ``group subcommand``; else do nothing.

    Called first in the group callback, which Click runs before it parses the
    subcommand's own arguments: the refusal then wins over a missing argument.
    """
    if listing_cutover_passed():
        removed_command(group if subcommand is None else f"{group} {subcommand}")


def _record_notice(message: str) -> None:
    """Carry one notice to the caller, on the channel native to it."""
    recorded = _notices.get()
    if recorded is not None:
        # In-process (RPC): the caller reads it off the envelope.
        recorded.append(message)
    else:
        # Terminal: typer.echo, never print(file=sys.stderr) — a closed fd 2
        # makes sys.stderr None, and print() would then fall back to stdout and
        # corrupt the JSON payload. click.echo drops the write instead.
        typer.echo(message, err=True)
    if _NOTICE_LOGGER.hasHandlers():
        _NOTICE_LOGGER.warning("%s", message)


def cutover_help(before: str, after: str) -> str:
    """Pick the help text that is true right now.

    Every help string here is also data: Click's help becomes the JSON-Schema
    ``description`` (``twicc/rpc/schema.py``) and then the MCP tool schema, so a
    string that outlives its behaviour actively misinforms agents. Deriving it
    from :data:`LISTING_CUTOVER` means it flips itself on the date — no
    second edit, and no release to cut on or after the cutover just to correct a
    sentence.

    Evaluated at import: a CLI process is short-lived, so it always reads true;
    the backend builds its MCP schema at startup and carries the old wording
    until the first restart past the date. A description one restart stale, with
    no effect on behaviour.
    """
    return after if listing_cutover_passed() else before


_CUTOVER_DATE = LISTING_CUTOVER.strftime("%Y-%m-%d")

#: Prepended to each listing's own help while the bare shape is still emitted.
#: Empty afterwards, so the notice disappears from ``--help`` and from the MCP
#: tool descriptions the day the migration is over.
CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns {{items, pagination}} instead "
    f"of a bare array, and pages at {PAGINATED_DEFAULT_LIMIT} by default. Pass "
    "--paginated now to get that shape today. ",
    "",
)

#: Same, for the one listing whose current shape is already an object.
CUTOVER_NOTICE_OBJECT = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this renames `hits` to `items` and "
    "`total_hits` to `pagination.total`, moves `limit`/`offset` under "
    f"`pagination`, and pages at {PAGINATED_DEFAULT_LIMIT} by default. Pass "
    "--paginated now to get that shape today. ",
    "",
)

PAGINATED_HELP = cutover_help(
    "Wrap the result in {items, pagination} with limit/offset/total/has_more, "
    "so a caller knows whether another page follows. Without an explicit --limit "
    f"the page size becomes {PAGINATED_DEFAULT_LIMIT}, so the answer always describes a "
    f"real page. Off by default until {_CUTOVER_DATE}, when the envelope becomes "
    "the only shape and this flag turns into an accepted no-op.",
    "Accepted and ignored: the envelope is the default. Kept so scripts that "
    "migrated during the deprecation window keep working untouched.",
)


def limit_help(noun: str, default: int | None, *, suffix: str = "") -> str:
    """Help for a listing's ``--limit``, true on both sides of the cutover.

    ``default`` is what the command passes to :func:`resolve_limit` — the page
    size without ``--paginated``. Past the cutover every listing pages at
    :data:`PAGINATED_DEFAULT_LIMIT`, so the second form drops the distinction.
    """
    shown = "no limit" if default is None else str(default)
    before = f"Max number of {noun} to return (default: {shown}"
    # `share` already pages at 50, so the flag changes nothing for it.
    before += ")." if default == PAGINATED_DEFAULT_LIMIT else f"; {PAGINATED_DEFAULT_LIMIT} with --paginated)."
    after = f"Max number of {noun} to return (default: {PAGINATED_DEFAULT_LIMIT})."
    return cutover_help(before + suffix, after + suffix)


SLIM_HELP = cutover_help(
    "Return a reduced projection of each session: identity, state, cost, and the "
    "has_* flags telling you what else is there. Drops the payloads you can fetch "
    "per session (tasks, plan, goals, layout), the redundant timestamps and paths, "
    "and the agent-settings bundle. About 60% lighter. Off by default until "
    f"{_CUTOVER_DATE}, when it becomes the default and this flag turns into an "
    "accepted no-op.",
    "Accepted and ignored: the reduced projection is the default. Kept so scripts "
    "that migrated during the deprecation window keep working untouched.",
)

FULL_HELP = cutover_help(
    "Return the full session payload — the same fields as `session <id>`. It is "
    f"the default until {_CUTOVER_DATE}; pass --full now to keep it after that "
    "date. Mutually exclusive with --slim.",
    "Return the full session payload — the same fields as `session <id>` — "
    "instead of the default reduced projection: identity, state, cost, and the "
    "has_* flags telling you what else is there. Mutually exclusive with --slim.",
)

TOPOLOGY_SLIM_HELP = cutover_help(
    "Reduce each node's `process` block to `{state}`. Each node's `session` is "
    f"already the reduced subset. Off by default until {_CUTOVER_DATE}, when it "
    "becomes the default and this flag turns into an accepted no-op.",
    "Accepted and ignored: each node's `process` block is reduced to `{state}` by "
    "default.",
)

_TOPOLOGY_FULL_LEAD = (
    "Emit the full session serialization for every node — the fields "
    "`session <id>` returns, minus its `process` block, which sits at "
    "`nodes[].process` — and that full `process` block. Disabled by default: each "
    "node's `session` carries a reduced subset (id, project_id, provider, title, "
    "annotations, spawned_by, spawn_root, created_at, last_new_content_at, "
    "context_usage, context_max, total_cost, directory)"
)

TOPOLOGY_FULL_HELP = cutover_help(
    f"{_TOPOLOGY_FULL_LEAD}. Its `process` block keeps its five fields until "
    f"{_CUTOVER_DATE}; from that date it is `{{state}}` alone. Mutually exclusive "
    "with --slim.",
    f"{_TOPOLOGY_FULL_LEAD}, and its `process` block is `{{state}}` alone. "
    "Mutually exclusive with --slim.",
)

_SESSIONS_GET_IDS_HELP = (
    "One or more session IDs to look up. The output mirrors the input "
    "order (duplicates collapsed, first occurrence wins). Each entry "
    "is either {entry} or a placeholder with "
    "`known: false` when no Session row exists for that id. "
    "Subagents, archived and hidden sessions are returned just like "
    "regular ones — the listing filters don't apply when you name "
    "explicit ids."
)

SESSIONS_GET_IDS_HELP = cutover_help(
    _SESSIONS_GET_IDS_HELP.format(entry="the full session metadata"),
    _SESSIONS_GET_IDS_HELP.format(
        entry="the session's reduced projection (its full metadata with --full)",
    ),
)

#: Prepended to the help of the three session listings while the full payload is
#: still their default. Empty afterwards, like :data:`CUTOVER_NOTICE`.
SLIM_CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} this returns the reduced session projection "
    "by default (what --slim returns today). Pass --full to keep the full payload. ",
    "",
)

#: Same, for ``topology``, whose change is its ``process`` block.
TOPOLOGY_CUTOVER_NOTICE = cutover_help(
    f"DEPRECATION: from {_CUTOVER_DATE} each node's `process` block is reduced to "
    "`{state}` by default. Pass --full to get the full session and process block "
    "on every node. ",
    "",
)


def removal_help(command: str) -> str:
    """Help prefix of a retired command, true on both sides of the cutover."""
    replacement = RETIRED_COMMANDS[command]
    return cutover_help(
        f"DEPRECATION: stops working on {_CUTOVER_DATE}; use `twicc {replacement}` instead. ",
        f"REMOVED on {_CUTOVER_DATE}: use `twicc {replacement}` instead. ",
    )


def emit_list(
    items: list,
    *,
    paginated: bool,
    plain=None,
    limit: int | None = None,
    offset: int = 0,
    total: int | None = None,
    has_more: bool | None = None,
    extra: dict | None = None,
) -> None:
    """Emit a listing, optionally wrapped in the shared pagination envelope.

    Without ``paginated`` the payload is emitted exactly as it always was —
    ``items`` for the nine array-returning commands, or ``plain`` for the one
    (``search``) whose historical shape is already an object. That branch is
    the compatibility contract: existing scripts must keep parsing what they
    parse today.

    With ``paginated`` the payload becomes ``{"items": [...], "pagination":
    {...}}``. ``pagination`` always carries the same four keys, on every
    command, so a caller never has to test for their presence:

    - ``limit`` / ``offset`` — the window that was applied (``limit`` is
      ``None`` when the command has no default limit and none was asked for,
      meaning everything was returned).
    - ``total`` — the number of rows the filters match, or ``None`` when it
      cannot be known without an unreasonable amount of work.
    - ``has_more`` — whether another page follows.

    ``has_more`` is derived from ``offset + limit < total`` unless the caller
    passes it explicitly, which the windowing modes that do not read forward
    (``--tail``) must do. ``extra`` carries command-specific top-level keys
    (``search``'s ``query``, ...) that are not pagination facts.
    """
    if not paginated:
        emit_json(items if plain is None else plain)
        return

    if has_more is None:
        has_more = limit is not None and total is not None and offset + limit < total

    payload: dict = {
        "items": items,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": has_more,
        },
    }
    if extra:
        payload |= extra
    emit_json(payload)
