"""``twicc update-session <ID> mute | notify`` sub-commands.

Both commands update the per-session gate for finished-working notifications.
``mute`` suppresses those notifications. ``notify`` enables them, subject to
the existing global notification settings.
"""

from __future__ import annotations

import typer


def _run_mute_on_user_turn_update(
    session_id: str,
    *,
    mute_on_user_turn: bool,
    timeout: int,
) -> None:
    """Submit a mute update and wait for the final server status."""
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
        "mute_on_user_turn": mute_on_user_turn,
    }
    sub = transport.submit(
        payload,
        kind="session:update_mute_on_user_turn",
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


def update_mute_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the mute may still apply on the server side."
        ),
    ),
) -> None:
    """Suppress only this session's finished-working notifications."""
    _run_mute_on_user_turn_update(
        ctx.obj,
        mute_on_user_turn=True,
        timeout=timeout,
    )


def update_notify_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; notifications may still be enabled on "
            "the server side."
        ),
    ),
) -> None:
    """Enable finished-working notifications for this session.

    Existing global notification settings still apply.
    """
    _run_mute_on_user_turn_update(
        ctx.obj,
        mute_on_user_turn=False,
        timeout=timeout,
    )
