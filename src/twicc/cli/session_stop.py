"""``twicc session <ID> stop`` sub-command.

Drops a ``kind="process:stop"`` payload in ``<data_dir>/drop-requests/`` so
the live TwiCC server asks the agent manager to kill the agent attached
to the session via
:func:`twicc.core.services.process_kill.kill_session_process_from_payload`,
exactly as the UI's *Stop process* button does (with ``reason="manual"``).

Idempotent — if no live agent is attached when the request lands, the
status is still ``"stopped"``. The CLI exits 0 either way.
"""

from __future__ import annotations

import typer


def stop_cmd(
    session_id: str,
    *,
    timeout: int,
    force: bool = False,
) -> None:
    """Drop a ``kind="process:stop"`` request and wait for the status."""
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
    # and have a project directory. Mirrors the guards every other CLI
    # session-targeted command runs.
    try:
        resolved = lookup_session(session_id)
    except SessionLookupError as e:
        emit_validation_errors([ValidationError("SESSION_ID", e.code, e.message)])
        raise typer.Exit(1)

    payload = {"session_id": resolved.session_id}
    if force:
        payload["force"] = True

    sub = transport.submit(payload, kind="process:stop")
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    emit_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)

    if outcome.status == "stopped":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
