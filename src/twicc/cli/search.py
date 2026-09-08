"""CLI implementation for the ``twicc search`` subcommand."""

from twicc.cli._output import emit_error, emit_list, pagination_notice, resolve_limit


def main(
    query: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    include_hidden: bool = False,
    only_hidden: bool = False,
    spawned_by: str | None = None,
    spawn_tree: str | None = None,
    descendants: str | None = None,
    siblings: str | None = None,
    project: str | None = None,
    workspace: str | None = None,
    annotation: list[str] | None = None,
    paginated: bool = False,
) -> None:
    """Execute a raw Tantivy search and print JSON results to stdout.

    ``spawned_by`` and ``descendants`` are raw CLI values (``None``, a
    session_id, or ``"self"`` / ``"parent"``). ``spawn_tree`` accepts
    ``None``, a session_id, or ``"self"``; ``siblings`` accepts ``None``, a
    session_id, or ``"self"``. ``project`` is an already-derived project id
    (the typer wrapper resolves a path/id input via ``derive_project_id``),
    expanded here to the project's session scope (itself plus its own
    worktrees); ``workspace`` is a workspace id, expanded to its members plus
    each member's worktrees. They are mutually exclusive (enforced by the typer
    wrapper). When any value needs DB access (a keyword on ``spawned_by``, any
    value on ``spawn_tree`` — the resolver always looks the id up to find the
    tree's root — any value on ``descendants`` / ``siblings`` which always walk
    the DB, or a ``project`` / ``workspace`` scope which expands worktrees), we
    ``django.setup()`` so an ordinary full-text query stays Django-free. The
    typer wrapper guarantees the filiation filters are mutually exclusive.
    """
    # Above the conditional django.setup() below: the notice must fire for every
    # search, not only the ones that touch the DB. shape="object" selects the
    # text naming the key renames — search is the one listing whose current
    # output is not a bare array.
    paginated = pagination_notice("search", paginated, default_limit=20, shape="object")

    if (
        spawned_by in ("self", "parent")
        or spawn_tree is not None
        or descendants is not None
        or siblings is not None
        or project is not None
        or workspace is not None
        or annotation
    ):
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

    limit = resolve_limit(limit, paginated=paginated, default=20)

    annotation_filters = None
    if annotation:
        from twicc.cli._annotation_filters import parse_annotation_filter
        try:
            annotation_filters = [parse_annotation_filter(spec) for spec in annotation]
        except ValueError as exc:
            emit_error(f"Error: {exc}", code=2)

    # Scope hits to a set of project ids, mirroring the UI. ``--project`` folds
    # in the project's own worktrees (a worktree scopes to just itself);
    # ``--workspace`` expands to its members plus each member's worktrees. The
    # two are mutually exclusive. ``None`` leaves the search unscoped; an empty
    # workspace yields ``[]``, which ``raw_search`` treats as "no candidates".
    project_ids = None
    if project is not None:
        from twicc.projects import project_scope_ids

        project_ids = project_scope_ids(project)
    elif workspace is not None:
        from twicc.projects import expand_project_ids_with_worktrees
        from twicc.workspaces import read_workspaces

        ws = next(
            (w for w in read_workspaces().get("workspaces", []) if w.get("id") == workspace),
            None,
        )
        if ws is None:
            emit_error(f"Error: workspace '{workspace}' not found.", code=1)
        project_ids = expand_project_ids_with_worktrees(ws.get("projectIds", []))

    from twicc.search import raw_search

    try:
        result = raw_search(
            query,
            limit=limit,
            offset=offset,
            to_json=False,
            include_hidden=include_hidden,
            only_hidden=only_hidden,
            spawned_by=spawned_by_id,
            spawn_tree=spawn_root_id,
            descendants=descendants_ids,
            siblings=siblings_ids,
            project_ids=project_ids,
            annotation_filters=annotation_filters,
        )
    except RuntimeError as exc:
        emit_error(f"Error: {exc}", code=1)

    # ``search`` is the one listing whose historical shape is already an object,
    # so the unpaginated branch emits it untouched; ``--paginated`` maps it onto
    # the envelope every other listing uses. Whatever is not a pagination fact
    # (the echoed query, the annotation flags, a parse error) stays top-level.
    emit_list(
        result["hits"],
        paginated=paginated,
        plain=result,  # untouched, key order included
        limit=limit,
        offset=offset,
        total=result["total_hits"],
        extra={k: v for k, v in result.items() if k not in ("hits", "total_hits", "limit", "offset")},
    )
