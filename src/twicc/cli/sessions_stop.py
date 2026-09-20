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
        resolve_listing_twicc_pid,
    )
    from twicc.cli._session_selection import (
        reject_conflicting_scopes,
        resolve_explicit_ids,
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

    reject_conflicting_scopes(
        spawned_by, spawn_tree, descendants, siblings,
        project=project, workspace=workspace,
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

    # Explicit ids are UNIONED with the filters, as in `processes stop`,
    # `processes wait`, `send-messages` and `update-sessions`. An earlier
    # version replaced them, which silently turned a migrated
    # `processes stop <id> --descendants self` into a one-agent stop.
    explicit = resolve_explicit_ids(session_ids)
    seen = set(explicit)

    has_filter = any((
        project, workspace, provider, state,
        spawned_by, spawn_tree, descendants, siblings, annotation,
    ))
    selected: list[str] = []
    if has_filter or not explicit:
        qs, _ = build_filtered_queryset(
            project=project, workspace=workspace,
            # A running session is never archived (archiving kills the agent)
            # and may well be hidden, so neither default belongs here. And its
            # transcript may not be indexed yet, which must not spare it.
            archived=True, include_hidden=True, require_indexed=False,
            spawned_by=spawned_by, spawn_tree=spawn_tree,
            descendants=descendants, siblings=siblings,
            annotation=annotation, provider=provider,
            state=state, active=not state,
        )
        # Narrowed to what is running, which is what bounds a bare call. The
        # narrowing happens in SQL (``active=``), not here: the alternative
        # reads identically and hands every session id to one ``IN`` clause.
        selected = [
            sid for sid in qs.values_list("id", flat=True)
            if sid not in seen
        ]

    # Named ids are NOT narrowed to the live set: naming one is the caller
    # saying "stop this", and the underlying service is idempotent. Dropping
    # them would also make the output unalignable with the input, where
    # `processes stop` reports `skipped_*` per id.
    targets = explicit + selected
    if not targets:
        emit_json([])
        return

    emit_json(stop_session_ids(
        targets, timeout=timeout, force=force, twicc_pid=twicc_pid,
    ))
