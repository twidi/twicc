"""``twicc update-session <ID> annotations <OPERATION>...`` sub-command."""

from __future__ import annotations

import typer


def update_annotations_cmd(
    ctx: typer.Context,
    operations: list[str] = typer.Argument(
        ...,
        metavar="OPERATION...",
        help=(
            "Ordered annotation operation: clear, replace-file:PATH, "
            "merge-file:PATH, set:KEY=VALUE, or unset:KEY."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the annotation update may still apply "
            "on the server side."
        ),
    ),
) -> None:
    """Apply ordered annotation operations to the session."""
    session_id: str = ctx.obj

    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.annotations import parse_annotation_update_operations
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

    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    parsed_operations, errors = parse_annotation_update_operations(operations)
    if errors:
        emit_validation_errors(errors)
        raise typer.Exit(1)

    payload = {
        "session_id": resolved.session_id,
        "operations": parsed_operations,
    }

    sub = transport.submit(payload, kind="session:update_annotations")
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
