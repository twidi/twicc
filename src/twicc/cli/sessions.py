"""CLI implementation for the ``twicc sessions`` subcommand."""

from twicc.cli._output import emit_error, emit_list, pagination_notice, resolve_limit


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
    from twicc.core.serializers import serialize_session, slim_session

    qs = Session.objects.filter(
        type="session",
        created_at__isnull=False,
        user_message_count__gt=0,
    ).order_by("-mtime")

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
            emit_error(f"Error: {exc}", code=2)
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

    limit = resolve_limit(limit, paginated=paginated, default=20)
    total = qs.count() if paginated else None
    sessions = qs[offset : offset + limit]
    data = [serialize_session(s) for s in sessions]
    if slim:
        data = [slim_session(row) for row in data]

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)
