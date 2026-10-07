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


#: The page size of every listing: what a call without ``--limit`` returns.
PAGINATED_DEFAULT_LIMIT = 20


def resolve_limit(limit: int | None) -> int:
    """Page size to apply: the caller's, else :data:`PAGINATED_DEFAULT_LIMIT`.

    ``limit`` is ``None`` when the option was not passed — every paginated
    command declares it that way so an explicit ``--limit 20`` stays
    distinguishable from the default.
    """
    return PAGINATED_DEFAULT_LIMIT if limit is None else limit




def limit_help(noun: str, *, suffix: str = "") -> str:
    """Help for a listing's ``--limit``."""
    return f"Max number of {noun} to return (default: {PAGINATED_DEFAULT_LIMIT})." + suffix



FULL_HELP = (
    "Return the full session payload — every field of the session payload — "
    "instead of the default reduced projection, which drops the per-session "
    "payloads, the redundant timestamps, the cost breakdown, slug, browser_url "
    "and compute_version_up_to_date."
)


_TOPOLOGY_FULL_LEAD = (
    "Emit the full serializer payload for every node — agent settings as stored, "
    "`artifacts_dir` as the serializer reports it (always the session's artifacts "
    "folder, even while empty), none of the CLI-added "
    "keys (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`, "
    "`question_widget`) — minus its `process` block, which sits at "
    "`nodes[].process` — and that full `process` block. Disabled by default: each "
    "node's `session` carries a reduced subset (id, project_id, provider, title, "
    "annotations, spawned_by, spawn_root, created_at, last_new_content_at, "
    "context_usage, context_max, total_cost, directory)"
)

TOPOLOGY_FULL_HELP = (
    f"{_TOPOLOGY_FULL_LEAD}, and its `process` block is "
    "`{state, background_work_in_progress}` alone."
)

SESSIONS_GET_IDS_HELP = (
    "One or more session IDs to look up. The output mirrors the input "
    "order (duplicates collapsed, first occurrence wins). Each entry "
    "is either the session's reduced projection (its full metadata with --full) "
    "or a placeholder with `known: false` when no Session row exists for that id. "
    "Subagents, archived and hidden sessions are returned just like "
    "regular ones — the listing filters don't apply when you name "
    "explicit ids."
)



WHOAMI_HELP = (
    "Print the calling session (found by walking the PID ancestry; over MCP, "
    "from the connection): the `session self` payload — the session row, reduced by default "
    "(--full for every field), with its `process` block inside. From a plain "
    "terminal, this command exits 1."
)


WHOAMI_FULL_HELP = "Return the `session self` payload in full — every field of the session payload."


def emit_list(
    items: list,
    *,
    limit: int | None = None,
    offset: int = 0,
    total: int | None = None,
    has_more: bool | None = None,
    extra: dict | None = None,
) -> None:
    """Emit a listing wrapped in the shared pagination envelope.

    The payload is ``{"items": [...], "pagination": {...}}``. ``pagination``
    always carries the same four keys, on every command, so a caller never
    has to test for their presence:

    - ``limit`` / ``offset`` — the window that was applied.
    - ``total`` — the number of rows the filters match, or ``None`` when it
      cannot be known without an unreasonable amount of work.
    - ``has_more`` — whether another page follows.

    ``has_more`` is derived from ``offset + limit < total`` unless the caller
    passes it explicitly, which the windowing modes that do not read forward
    (``--tail``) must do. ``extra`` carries command-specific top-level keys
    (``search``'s ``query``, ...) that are not pagination facts.
    """
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
