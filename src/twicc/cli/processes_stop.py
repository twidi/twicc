"""``twicc processes stop <SESSION_ID>...`` sub-command.

Batch-stops live agent processes by dropping one ``kind="process:stop"``
request per ``session_id`` in ``<data_dir>/drop-requests/``. Server-side,
each request is dispatched to
:func:`twicc.core.services.process_kill.kill_session_process_from_payload`
exactly like the singular ``process <ID> stop`` (with ``reason="manual"``),
so the operation is fully idempotent — asking to stop a session whose
process is already gone still produces ``status="stopped"``.

Output is a JSON array, one entry per input session_id, in input order
(duplicates collapsed, first occurrence wins). Each entry carries:

- ``status``: ``"stopped"`` / ``"rejected"`` / ``"failed"`` / ``"timeout"``
  for entries that reached the server, or ``"skipped_*"`` for entries
  rejected by the local pre-check (no drop emitted)
- ``session_known``: ``true`` if TwiCC has at least one trace of the
  session (``Session`` row OR any ``ProcessRun`` for this TwiCC)
- ``request_uuid``: the uuid of the drop file, or ``null`` for skipped
- ``provider`` / ``session_title`` / ``project_id``: from the ``Session``
  row when one exists, else ``null``
- ``error``: failure reason for ``rejected`` / ``failed`` / ``skipped_*``
  / ``timeout``; ``null`` for ``stopped``

Exit codes:

- 0 — command ran to completion (inspect each entry's ``status`` for the
  per-id outcome)
- 1 — local CLI validation error (``--timeout`` <= 0, etc.)
- 2 — TwiCC server is not running
- 64 — bad CLI usage (handled by Typer)

A single ``--timeout`` (default 30 s) bounds the entire batch: all drops
are submitted upfront, then we poll every status file in a single loop
until each has a final response or the deadline elapses. Drops are
processed in parallel by the server-side watcher, so a 30 s batch
timeout typically covers N drops without compounding.
"""

from __future__ import annotations

from twicc.cli._output import emit_error, emit_json


def stop_cmd(
    session_ids: list[str],
    *,
    timeout: int,
    force: bool = False,
    spawned_by: str | None = None,
    descendants: str | None = None,
    annotation: list[str] | None = None,
) -> None:
    """Batch-stop live agent processes for one or more sessions."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._twicc_info import resolve_live_twicc

    # --- Argument validation ---------------------------------------------

    if timeout <= 0:
        emit_error(
            f"Error: --timeout must be > 0 (got {timeout}).",
            code=1,
        )

    if spawned_by == "parent" or descendants == "parent":
        emit_error(
            "Error: processes stop does not support parent-scoped filters. "
            "Use 'self' or an explicit session_id.",
            code=1,
        )

    filiation_scope = any((spawned_by, descendants))
    if annotation and not filiation_scope:
        emit_error(
            "Error: --annotation on processes stop requires --spawned-by "
            "or --descendants.",
            code=1,
        )

    has_scope = filiation_scope or bool(annotation)
    if not session_ids and not has_scope:
        emit_error(
            "Error: no session_ids or filters given. Pass at least one "
            "session_id, or select sessions with --spawned-by or "
            "--descendants. Use --annotation only to narrow that scope.",
            code=1,
        )

    # --- Server-up check (exit 2 mirrors process stop) -------------------

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    info = resolve_live_twicc()
    if info is None:
        emit_error(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            code=2,
        )

    # --- Resolve explicit ids + optional scope filters -------------------

    try:
        from twicc.cli._session_scope import merge_session_scope_ids

        unique_ids = merge_session_scope_ids(
            session_ids,
            spawned_by=spawned_by,
            descendants=descendants,
            annotation=annotation,
        )
    except RuntimeError as e:
        emit_error(f"Error: {e}", code=1)
    except ValueError as e:
        emit_error(f"Error: {e}", code=2)

    if not unique_ids:
        emit_json([])
        return

    # --- Stop them ------------------------------------------------------
    # The mechanism is shared with ``sessions stop``; only the selection above
    # is this command's own.
    from twicc.cli._stop_batch import stop_session_ids

    results = stop_session_ids(
        unique_ids, timeout=timeout, force=force, twicc_pid=info.pid,
    )
    emit_json(results)
