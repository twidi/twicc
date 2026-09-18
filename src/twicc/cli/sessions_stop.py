"""``twicc sessions stop`` — stop the agents behind selected sessions.

Selection is the whole ``sessions`` filter family, narrowed to what is
actually running: a session with no process has nothing to stop, so the
targets are always a subset of ``sessions --active``. That is what makes a
bare ``sessions stop`` safe to allow where ``processes stop`` refuses one —
the blast radius is bounded by the agents alive at that instant, not by the
thousands of rows the listing holds.

Hidden sessions are included. ``sessions`` hides them when *listing*, but
"stop everything running" that silently skipped the orchestration workers —
which are hidden by convention — would be a lie. Archived ones need no
thought: archiving already kills the agent, so none of them is ever running.

The stopping itself is :mod:`twicc.cli._stop_batch`, shared with
``processes stop``.
"""

from __future__ import annotations

from twicc.cli._output import emit_error, emit_json


def main(
    session_ids: list[str],
    *,
    timeout: int,
    force: bool = False,
    project: str | None = None,
    workspace: str | None = None,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
    provider: str | None = None,
    state: list[str] | None = None,
) -> None:
    """Stop every selected session that currently has a process."""
    import django

    django.setup()

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._process_state import (
        DEAD_VIRTUAL_STATE,
        live_session_ids,
        load_process_rows,
        resolve_listing_twicc_pid,
    )
    from twicc.cli._stop_batch import stop_session_ids
    from twicc.cli.sessions import build_filtered_queryset

    if timeout <= 0:
        emit_error(f"Error: --timeout must be > 0 (got {timeout}).", code=1)

    # Refused rather than silently emptied: asking to stop what is already
    # stopped is a mistake worth naming, not a no-op to honour.
    if state and DEAD_VIRTUAL_STATE in state:
        emit_error(
            "Error: --state dead selects sessions with no process, which "
            "sessions stop has nothing to do with.",
            code=1,
        )

    try:
        transport.ensure_server_available()
    except ServerDownError as e:
        emit_error(f"Error: {e}", code=2)

    twicc_pid = resolve_listing_twicc_pid()
    if twicc_pid is None:
        emit_error(
            "Error: TwiCC server is unresponsive "
            "(twicc.info.json missing or recorded PID is dead).",
            code=2,
        )

    # Explicit ids bypass the filters entirely, mirroring `sessions get` and
    # `processes stop`: naming an id is the caller saying they know which one.
    if session_ids:
        seen: set = set()
        targets = [sid for sid in session_ids if not (sid in seen or seen.add(sid))]
    else:
        qs, _ = build_filtered_queryset(
            project=project, workspace=workspace,
            # A running session is never archived (archiving kills the agent)
            # and may well be hidden, so neither default belongs here.
            archived=True, include_hidden=True,
            spawned_by=spawned_by, spawn_tree=spawn_tree,
            descendants=descendants, siblings=siblings,
            annotation=annotation, provider=provider,
            state=state, active=not state,
        )
        targets = list(qs.values_list("id", flat=True))

    if not targets:
        emit_json([])
        return

    # Explicit ids are not filtered by the queryset, so the live check happens
    # here for both paths: stopping a session that has no process is harmless
    # (the underlying service is idempotent) but reporting it as "stopped"
    # when nothing was running is noise.
    live = live_session_ids(load_process_rows(targets, twicc_pid))
    targets = [sid for sid in targets if sid in live]
    if not targets:
        emit_json([])
        return

    emit_json(stop_session_ids(
        targets, timeout=timeout, force=force, twicc_pid=twicc_pid,
    ))
