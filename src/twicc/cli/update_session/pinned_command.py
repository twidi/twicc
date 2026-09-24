"""``twicc update-session <ID> pin <MODE> | unpin`` sub-commands.

Two boolean-ish flips sharing the same plumbing: ``pin`` takes a positional
``MODE`` argument (one of ``project`` / ``workspace`` / ``all``, mirroring
``PinMode``), ``unpin`` takes no argument. Both drop a
``kind="session:update_pinned"`` payload that the server resolves into a write on
``Session.pinned`` (NULL for unpin, the mode string otherwise) and a
``session_updated`` broadcast.

There are no options beyond the standard ``--timeout`` control; the
mode vocabulary is the same one the UI uses (no aliases, no shortcuts —
keeping every surface aligned).
"""

from __future__ import annotations

import typer


# Mirrors :class:`twicc.core.models.PinMode`. Kept here as a flat set so the
# CLI validates locally without importing Django (the lookup happens before
# ``django.setup()`` so the help / validation paths stay fast).
_VALID_PIN_MODES: tuple[str, ...] = ("project", "workspace", "all")


def _run_pinned_update(
    session_id: str,
    *,
    pinned: str | None,
    timeout: int,
) -> None:
    """Drop a ``kind="session:update_pinned"`` payload and wait for the status."""
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
        "pinned": pinned,
    }

    sub = transport.submit(payload, kind="session:update_pinned")
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


def update_pin_cmd(
    ctx: typer.Context,
    mode: str = typer.Argument(
        ...,
        metavar="MODE",
        help=(
            "Pin scope: 'project' (only the session's project), 'workspace' "
            "(every workspace the project belongs to), or 'all' (every "
            "project — global pin). Same vocabulary as the UI's pin menu."
        ),
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the pin may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Pin the session in one of three visibility scopes.

    Same effect as the UI's pin menu. A pinned session stays at the top of
    the sidebar in every matching context: ``project`` only in its own
    project; ``workspace`` in every workspace the project belongs to;
    ``all`` everywhere. Pinning an already-pinned session simply switches
    the scope. Use ``unpin`` to clear the pin.
    """
    # Local mode validation BEFORE Django setup so the user gets immediate
    # feedback for typos (e.g. ``pin workspce``).
    if mode not in _VALID_PIN_MODES:
        # Cheap fallback path: no Django, hand-roll the validation_error
        # envelope so the output shape matches every other CLI command.
        from twicc.cli._drop_request.output import emit_validation_errors
        from twicc.cli._drop_request.validation import ValidationError
        emit_validation_errors(
            [ValidationError(
                "MODE", "invalid_pin_mode",
                f"Unknown pin mode {mode!r}. Accepted: "
                f"{list(_VALID_PIN_MODES)}.",
            )],
        )
        raise typer.Exit(1)

    _run_pinned_update(
        ctx.obj,
        pinned=mode,
        timeout=timeout,
    )


def update_unpin_cmd(
    ctx: typer.Context,
    timeout: int = typer.Option(
        30,
        "--timeout",
        help=(
            "Seconds to wait for the server's final status before giving up. "
            "The request is not cancelled; the unpin may still apply on the "
            "server side."
        ),
    ),
) -> None:
    """Unpin the session (regardless of the current pin scope).

    Same effect as picking "Not pinned" in the UI's pin menu. Idempotent:
    unpinning an already-unpinned session succeeds.
    """
    _run_pinned_update(
        ctx.obj,
        pinned=None,
        timeout=timeout,
    )
