"""``twicc update-session <ID> detach-tree | attach-tree`` sub-commands.

Both commands update the per-session ``tree_detached`` display flag of the
sidebar tree. ``detach-tree`` shows the session as a root, apart from its
spawner. ``attach-tree`` restores the nesting under its spawner.
"""

from __future__ import annotations

import typer


def _run_tree_detached_update(
    session_id: str,
    *,
    tree_detached: bool,
    timeout: int,
) -> None:
    """Submit a tree_detached update and wait for the final server status."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django

    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.session_lookup import (
        SessionLookupError,
        lookup_session,
    )
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "tree_detached": tree_detached,
    }
    sub = transport.submit(
        payload,
        kind="session:update_tree_detached",
    )
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "updated":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)


def update_detach_tree_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the change may still apply on the server side."
        ),
    ),
) -> None:
    """Show this session detached from its spawner in the sidebar tree.

    Only the sidebar tree display changes; filiation, orchestration and
    reporting are unchanged. Use detach-tree when the session has no topical
    link with its spawner (a coordinator spawning independent work in other
    projects or on other subjects). Use attach-tree to restore the nesting.
    """
    _run_tree_detached_update(
        ctx.obj,
        tree_detached=True,
        timeout=timeout,
    )


def update_attach_tree_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the change may still apply on the server side."
        ),
    ),
) -> None:
    """Restore this session's nesting under its spawner in the sidebar tree.

    Only the sidebar tree display changes; filiation, orchestration and
    reporting are unchanged. This undoes detach-tree, which fits a session
    with no topical link with its spawner (a coordinator spawning independent
    work).
    """
    _run_tree_detached_update(
        ctx.obj,
        tree_detached=False,
        timeout=timeout,
    )
