"""CLI implementation for the ``twicc processes`` subcommand."""

from twicc.cli._output import emit_error, emit_list, pagination_notice, resolve_limit


def main(
    *,
    provider: str | None = None,
    state: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
    paginated: bool = False,
) -> None:
    """List currently running processes (live ProcessRuns) of the running TwiCC.

    Scopes to ``twicc_pid`` equal to the PID recorded in ``twicc.info.json``
    and excludes ``state=DEAD`` rows — a DEAD row never represents a
    running process (Claude Code keeps DEAD rows around solely so the boot
    cron restart can reclaim them, never to advertise a live worker).

    The CLI projects the persisted ``state`` + ``awaiting_user_input``
    columns onto a single 4-value vocabulary for output and filtering:

    - ``starting`` (state=STARTING)
    - ``assistant_turn`` (state=ASSISTANT_TURN AND awaiting_user_input=False)
    - ``awaiting_user_input`` (awaiting_user_input=True; always
      implies state=ASSISTANT_TURN by construction)
    - ``user_turn`` (state=USER_TURN; awaiting is always False here)

    Optional filters narrow the result further:

    - ``provider`` restricts to one backend (``claude_code``, ``codex``).
    - ``state`` restricts to one of the four virtual values above; ``dead``
      is rejected to keep the "live processes only" guarantee.
    """
    import django

    django.setup()
    paginated = pagination_notice("processes", paginated, default_limit=20)

    filiation_scope = any((spawned_by, spawn_tree, descendants, siblings))
    if annotation and not filiation_scope:
        emit_error(
            "Error: --annotation on processes listing requires --spawned-by, "
            "--spawn-tree, --descendants, or --siblings.",
            code=1,
        )

    has_scope = filiation_scope or bool(annotation)
    scoped_session_ids: list[str] | None = None
    if has_scope:
        try:
            from twicc.cli._session_scope import merge_session_scope_ids

            scoped_session_ids = merge_session_scope_ids(
                spawned_by=spawned_by,
                spawn_tree=spawn_tree,
                descendants=descendants,
                siblings=siblings,
                annotation=annotation,
            )
        except RuntimeError as e:
            emit_error(str(e), code=1)
        except ValueError as e:
            emit_error(f"Error: {e}", code=2)

    from twicc.agent.states import AgentState
    from twicc.cli._process_state import (
        AWAITING_VIRTUAL_STATE,
        LIVE_VIRTUAL_STATES,
        serialize_process_row,
    )
    from twicc.cli._twicc_info import resolve_live_twicc_or_exit
    from twicc.core.models import ProcessRun, Session

    info = resolve_live_twicc_or_exit()

    qs = (
        ProcessRun.objects
        .filter(twicc_pid=info.pid)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
    )

    if state is not None:
        if state not in LIVE_VIRTUAL_STATES:
            emit_error(
                f"Error: invalid --state '{state}'. Use one of: "
                f"{', '.join(sorted(LIVE_VIRTUAL_STATES))}.",
                code=1,
            )
        if state == AWAITING_VIRTUAL_STATE:
            # awaiting_user_input rows are necessarily in ASSISTANT_TURN; the
            # flag is the canonical filter so we don't combine with state.
            qs = qs.filter(awaiting_user_input=True)
        else:
            # Real-state filters must also exclude awaiting rows so the four
            # virtual buckets stay disjoint — otherwise --state assistant_turn
            # would also return rows the CLI projects as awaiting_user_input.
            qs = qs.filter(state=state, awaiting_user_input=False)

    if provider is not None:
        qs = qs.filter(provider=provider)

    # Resolved before the empty-scope exit below, so that early return reports the
    # same window as every other path rather than a bare ``limit: null``.
    limit = resolve_limit(limit, paginated=paginated, default=20)

    if scoped_session_ids is not None:
        if not scoped_session_ids:
            emit_list([], paginated=paginated, limit=limit, offset=offset, total=0)
            return
        qs = qs.filter(session_id__in=scoped_session_ids)

    hidden_session_ids = Session.objects.filter(hidden=True).values_list("id", flat=True)
    if only_hidden:
        qs = qs.filter(session_id__in=hidden_session_ids)
    elif not include_hidden and not filiation_scope:
        qs = qs.exclude(session_id__in=hidden_session_ids)

    total = qs.count() if paginated else None
    rows = list(qs[offset : offset + limit])

    # Enrich with the matching Session's title and project_id when the session
    # row has already been created by the watcher. Brand-new sessions that
    # haven't reached their first JSONL line yet have no Session row, so those
    # fields fall back to ``None``.
    session_ids = [r.session_id for r in rows]
    sessions_by_id = {
        s.id: s
        for s in Session.objects.filter(id__in=session_ids).only(
            "id", "title", "project_id"
        )
    }

    data = [
        serialize_process_row(row, sessions_by_id.get(row.session_id))
        for row in rows
    ]

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)
