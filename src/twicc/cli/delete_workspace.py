"""``twicc delete-workspace <ID>`` command.

Drops a ``kind="workspace:delete"`` payload in ``<data_dir>/drop-requests/``
so the live TwiCC server removes the workspace via
:func:`twicc.core.services.workspace_mutation.delete_workspace_from_payload`
— atomic remove under ``_workspaces_lock`` + ``workspaces_updated``
broadcast.

No confirmation prompt by design — the CLI is meant to be scripted.
Projects referenced by the workspace are not deleted; they stay in TwiCC
and in any other workspace that lists them.
"""

from __future__ import annotations

import typer


def delete_workspace_cmd(
    workspace_id: str = typer.Argument(
        ...,
        metavar="WORKSPACE_ID",
        help="Id of the workspace to delete.",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the delete may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Delete a workspace by id."""
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import emit_final, emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._output import emit_error
    from twicc.workspaces import read_workspaces

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(str(e), code=2)

    # Pre-flight: workspace existence (cheap, gives the user a clean error
    # without waiting on the server). Re-checked under the lock server-side.
    existing_workspaces = read_workspaces().get("workspaces", [])
    if not any(w.get("id") == workspace_id for w in existing_workspaces):
        emit_validation_errors(
            [ValidationError("WORKSPACE_ID", "workspace_not_found",
                              f"Workspace {workspace_id!r} not found.")],
        )
        raise typer.Exit(1)

    payload = {"workspace_id": workspace_id}

    sub = transport.submit(payload, kind="workspace:delete")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "deleted":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
