"""CLI implementation for the ``twicc projects`` subcommand."""

from twicc.cli._output import PAGINATED_DEFAULT_LIMIT, emit_error, emit_list, pagination_notice, resolve_limit


def main(
    *,
    limit: int | None = None,
    offset: int = 0,
    archived: bool = False,
    workspace: str | None = None,
    paginated: bool = False,
) -> None:
    """List all projects as JSON to stdout."""
    import django

    django.setup()
    paginated = pagination_notice("projects", paginated, default_limit=PAGINATED_DEFAULT_LIMIT)

    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project
    from twicc.project_icons import load_repo_icon_cache
    from twicc.projects import worktree_children_by_main
    from twicc.workspaces import read_workspaces

    # Read workspaces once: used both for the optional --workspace filter
    # and for the per-project ``workspaces`` membership field below.
    all_workspaces = read_workspaces().get("workspaces", [])

    qs = Project.objects.order_by("-mtime")

    if not archived:
        qs = qs.filter(archived=False)

    if workspace is not None:
        ws = next((w for w in all_workspaces if w.get("id") == workspace), None)
        if ws is None:
            emit_error(f"Error: workspace '{workspace}' not found.", code=1)
        qs = qs.filter(id__in=ws.get("projectIds", []))

    limit = resolve_limit(limit, paginated=paginated, default=PAGINATED_DEFAULT_LIMIT)
    total = qs.count() if paginated else None
    projects = list(qs[offset : offset + limit])

    # Build project_id -> [workspace_id] index for the listing.
    workspaces_by_project: dict[str, list[str]] = {}
    for ws in all_workspaces:
        for pid in ws.get("projectIds", []):
            workspaces_by_project.setdefault(pid, []).append(ws["id"])

    # Reverse of ``worktree_of``: main-repo id -> [worktree child ids] for the
    # projects on this page (one query). Each main repo's entry then carries
    # the ids of its git worktrees, like ``workspaces`` carries memberships.
    worktrees_by_main = worktree_children_by_main([p.id for p in projects])

    # Warm the repo-icon cache once (a short-lived CLI process never ran startup
    # discovery) so each ``repo_icon_url`` brick is populated rather than ``None``.
    load_repo_icon_cache()

    data = []
    for p in projects:
        serialized = serialize_project(p)
        serialized["workspaces"] = workspaces_by_project.get(p.id, [])
        serialized["worktrees"] = worktrees_by_main.get(p.id, [])
        data.append(serialized)

    emit_list(data, paginated=paginated, limit=limit, offset=offset, total=total)
