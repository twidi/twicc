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
import sys

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


PAGINATED_HELP = (
    "Wrap the result in {items, pagination} with limit/offset/total/has_more, "
    "so a caller knows whether another page follows. Without an explicit --limit "
    f"the page size becomes {PAGINATED_DEFAULT_LIMIT}, so the answer always describes a "
    "real page. Off by default: the bare shape is unchanged."
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
