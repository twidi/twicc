"""CLI implementation for the ``twicc sessions`` subcommand."""

from twicc.cli._output import emit_error, emit_list, pagination_notice, resolve_limit


def build_filtered_queryset(
    *,
    project=None, workspace=None, archived=False,
    include_hidden=False, only_hidden=False,
    spawned_by=None, spawn_tree=None, descendants=None, siblings=None,
    annotation=None, provider=None, state=None, active=False,
    require_indexed=True,
):
    """Resolve every ``sessions`` filter into a queryset, before any window.

    Returns ``(queryset, process_rows)``. ``process_rows`` is the live
    ``ProcessRun`` set when a state filter needed it, else ``None`` — the
    caller reuses it rather than querying twice.

    Shared with ``sessions stop``, which selects the same way and then acts
    instead of listing. Keeping one implementation is what stops the two from
    drifting into answering the same question differently.
    """
    import django

    django.setup()

    from twicc.cli._drop_request.whoami import (
        resolve_descendants_filter,
        resolve_siblings_filter,
        resolve_spawn_tree_filter,
        resolve_spawned_by_filter,
    )

    try:
        spawned_by_id = resolve_spawned_by_filter(spawned_by)
        spawn_root_id = resolve_spawn_tree_filter(spawn_tree)
        descendants_ids = resolve_descendants_filter(descendants)
        siblings_ids = resolve_siblings_filter(siblings)
    except RuntimeError as e:
        emit_error(str(e), code=1)

    from django.db.models import Q

    from twicc.core.models import Session

    qs = Session.objects.filter(type="session").order_by("-mtime")
    if require_indexed:
        # The listing's notion of a session worth showing: one whose transcript
        # has been read far enough to have a date and a first user message.
        # ``sessions stop`` turns it off — an agent that started two seconds
        # ago has neither yet, and sparing it would make "stop everything
        # running" false for exactly the session most likely to be running.
        qs = qs.filter(created_at__isnull=False, user_message_count__gt=0)

    if not archived:
        qs = qs.filter(archived=False)

    # When filtering by spawned_by / spawn_tree / descendants, the caller is
    # explicitly asking about filiation — show every matching session in the
    # tree whatever its visibility. The hidden=False default only applies to
    # unscoped listings, where it keeps the output aligned with what the
    # UI displays. --only-hidden still narrows further if requested.
    if only_hidden:
        qs = qs.filter(hidden=True)
    elif (
        not include_hidden
        and spawned_by_id is None
        and spawn_root_id is None
        and descendants_ids is None
        and siblings_ids is None
    ):
        qs = qs.filter(hidden=False)

    if spawned_by_id is not None:
        qs = qs.filter(spawned_by_id=spawned_by_id)

    if spawn_root_id is not None:
        # OR with Q(pk=spawn_root_id) to also include the root of a single-node
        # tree (a standalone session that has never spawned a child still has
        # spawn_root_id=NULL, so the plain equality filter would exclude it).
        # Same pattern as ``twicc topology`` (cf. ``cli/topology.py``).
        qs = qs.filter(Q(spawn_root_id=spawn_root_id) | Q(pk=spawn_root_id))

    if descendants_ids is not None:
        # An empty set means "the target has no descendants"; ``id__in=[]``
        # returns nothing without hitting the DB, which is exactly what we want.
        qs = qs.filter(id__in=descendants_ids)

    if siblings_ids is not None:
        # An empty set means "the target has no siblings" — same ``id__in=[]``
        # returns-nothing semantics as descendants above.
        qs = qs.filter(id__in=siblings_ids)

    if annotation:
        from twicc.cli._annotation_filters import apply_annotation_filters, parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            # 1, not 2: a malformed spec is the caller's typo, and a script
            # branching on 2 to wait for the backend would loop on it.
            emit_error(f"Error: {exc}", code=1)
        qs = apply_annotation_filters(qs, annotation_filters)

    if workspace is not None:
        from twicc.workspaces import read_workspaces

        ws = next(
            (w for w in read_workspaces().get("workspaces", []) if w.get("id") == workspace),
            None,
        )
        if ws is None:
            emit_error(f"Error: workspace '{workspace}' not found.", code=1)
        # A workspace's session scope = its members plus each member's git
        # worktrees, mirroring the UI (``getAllProjectIds``).
        from twicc.projects import expand_project_ids_with_worktrees

        qs = qs.filter(project_id__in=expand_project_ids_with_worktrees(ws.get("projectIds", [])))

    if project is not None:
        # A project's session scope = itself plus its own git worktrees,
        # mirroring the UI (``getProjectScopeIds``). Strictly downward: a normal
        # project folds in its worktrees; a worktree scopes to just itself.
        from twicc.projects import project_scope_ids

        qs = qs.filter(project_id__in=project_scope_ids(project))

    if provider is not None:
        from twicc.core.enums import Provider

        valid = [p.value for p in Provider]
        if provider not in valid:
            emit_error(
                f"Error: invalid --provider '{provider}'. Use one of: "
                f"{', '.join(sorted(valid))}.",
                code=1,
            )
        qs = qs.filter(provider=provider)

    # The live set is read BEFORE the window is applied. Filtering the page
    # afterwards would return fewer rows than --limit and make `total` and
    # `has_more` lie; and the set is small enough (one row per running agent)
    # that `id__in` costs nothing.
    from twicc.cli._process_state import (
        DEAD_VIRTUAL_STATE,
        VALID_VIRTUAL_STATES,
        live_session_ids,
        load_process_rows,
        resolve_listing_twicc_pid,
        session_ids_in_state,
    )

    if state and active:
        emit_error(
            "Error: --state and --active are mutually exclusive "
            "(--active means 'any state but dead').",
            code=1,
        )
    for token in state or ():
        if token not in VALID_VIRTUAL_STATES:
            emit_error(
                f"Error: invalid --state '{token}'. Use one of: "
                f"{', '.join(sorted(VALID_VIRTUAL_STATES))}.",
                code=1,
            )

    # Repeatable and OR-combined, like ``session messages --is-final``: a
    # session holds ONE state, so repeating the flag can only mean "any of
    # these". Naming all five selects everything, which is the unfiltered
    # listing — kept as a cheap branch rather than a union of every bucket.
    wanted = set(state or ())
    if wanted == set(VALID_VIRTUAL_STATES):
        wanted = set()

    process_rows = None
    if wanted or active:
        process_rows = load_process_rows(None, resolve_listing_twicc_pid())
        live = live_session_ids(process_rows)
        if active:
            qs = qs.filter(id__in=live)
        else:
            matched: set = set()
            for token in wanted - {DEAD_VIRTUAL_STATE}:
                matched |= session_ids_in_state(process_rows, token)
            if DEAD_VIRTUAL_STATE in wanted:
                # "dead" is an absence, so it cannot be enumerated — it is
                # everything the live set does not hold.
                from django.db.models import Q

                qs = qs.filter(Q(id__in=matched) | ~Q(id__in=live))
            else:
                qs = qs.filter(id__in=matched)
    return qs, process_rows


def main(
    *,
    project: str | None = None,
    workspace: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    archived: bool = False,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    annotation: list[str] | None = None,
    paginated: bool = False,
    slim: bool = False,
    provider: str | None = None,
    state: list[str] | None = None,
    active: bool = False,
) -> None:
    """List sessions as JSON to stdout.

    ``spawned_by`` and ``descendants`` are raw CLI values (``None``, a
    session_id, or ``"self"`` / ``"parent"``). ``spawn_tree`` accepts
    ``None``, a session_id, or ``"self"``; ``siblings`` accepts ``None``, a
    session_id, or ``"self"``. They are resolved here, after
    ``django.setup()``, so callers don't need to bootstrap Django
    themselves. The typer wrapper guarantees they are mutually exclusive.
    """
    import django

    django.setup()
    paginated = pagination_notice("sessions", paginated, default_limit=20)


    qs, process_rows = build_filtered_queryset(
        project=project,
        workspace=workspace,
        archived=archived,
        include_hidden=include_hidden,
        only_hidden=only_hidden,
        spawned_by=spawned_by,
        spawn_tree=spawn_tree,
        descendants=descendants,
        siblings=siblings,
        annotation=annotation,
        provider=provider,
        state=state,
        active=active,
    )

    from twicc.cli._process_state import (
        attach_process_blocks,
        load_process_rows,
        resolve_listing_twicc_pid,
    )
    from twicc.core.serializers import serialize_session, slim_session

    limit = resolve_limit(limit, paginated=paginated, default=20)
    total = qs.count() if paginated else None
    sessions = qs[offset : offset + limit]
    data = [serialize_session(s) for s in sessions]
    if slim:
        data = [slim_session(row) for row in data]

    if process_rows is None:
        process_rows = load_process_rows(
            [row["id"] for row in data], resolve_listing_twicc_pid(),
        )
    attach_process_blocks(data, process_rows, slim=slim)

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)
