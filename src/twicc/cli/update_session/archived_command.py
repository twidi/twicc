"""``twicc update-session <ID> archive | unarchive`` sub-commands.

Two commands sharing the same plumbing — they only differ by the boolean
they put in the ``kind="session:update_archived"`` payload. Both apply the same
behaviour the UI does:

- ``archive``  → ``archived=True``: server marks the session archived,
  kills the live agent (``reason="archived"``), tears down any tmux
  terminal attached to the session, and — when ``autoUnpinOnArchive``
  is set in the synced settings AND the session is currently pinned —
  also unpins.
- ``unarchive`` → ``archived=False``: server simply flips the flag back.
  No agent restart is implied (the session stays cold until the user
  resumes it).

There are no options beyond the standard ``--timeout`` control; auto-unpin
is honoured server-side from the synced setting, no CLI override.
"""

from __future__ import annotations

import typer


def _run_archived_update(
    session_id: str,
    *,
    archived: bool,
    timeout: int,
) -> None:
    """Drop a ``kind="session:update_archived"`` payload and wait for the status."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError, lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Local pre-check: session must exist, not be a subagent, not be stale,
    # and have a project directory. The watcher-side service re-validates
    # the same conditions in case the DB state changed.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "archived": archived,
    }

    sub = transport.submit(payload, kind="session:update_archived")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout


def update_archive_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the archive may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Archive the session.

    Same effect as the UI's archive action: stops any live agent attached to
    the session, closes the session's terminals, and — when the synced
    setting ``autoUnpinOnArchive`` is enabled and the session is currently
    pinned — also unpins.
    """
    _run_archived_update(
        ctx.obj,
        archived=True,
        timeout=timeout,
    )


def update_unarchive_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the unarchive may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Unarchive the session.

    Flips ``archived`` back to ``False``. Does not resume the agent — the
    session stays cold until you explicitly send a message or update its
    settings.
    """
    _run_archived_update(
        ctx.obj,
        archived=False,
        timeout=timeout,
    )
