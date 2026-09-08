"""CLI implementation for the ``twicc artifacts`` listing subcommand.

Read-only: lists bookmarked artifacts straight from the DB (no live server
needed), mirroring ``twicc sessions``. ``--project`` / ``--workspace`` reuse
the exact same worktree-aware scope helpers as the session listing
(``project_scope_ids`` / ``expand_project_ids_with_worktrees``) so the two
never drift; ``--scope`` filters on each bookmark's own visibility scope
(project / workspace / all), e.g. ``--scope all`` for the "everywhere" ones.
"""

from twicc.cli._output import emit_error, emit_list, resolve_limit


def main(
    *,
    project: str | None = None,
    workspace: str | None = None,
    scope: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    paginated: bool = False,
) -> None:
    """List bookmarked artifacts as JSON to stdout (most recently updated first)."""
    import django

    django.setup()

    from twicc.core.models import ArtifactBookmark, PinMode
    from twicc.core.serializers import serialize_artifact_bookmark

    if scope is not None and scope not in PinMode.values:
        emit_error(
            f"Error: --scope must be one of {list(PinMode.values)} (got {scope!r}).",
            code=2,
        )

    qs = ArtifactBookmark.objects.all().order_by("-updated_at")

    if scope is not None:
        qs = qs.filter(scope=scope)

    if workspace is not None:
        from twicc.workspaces import read_workspaces

        ws = next(
            (w for w in read_workspaces().get("workspaces", []) if w.get("id") == workspace),
            None,
        )
        if ws is None:
            emit_error(f"Error: workspace '{workspace}' not found.", code=1)
        # A workspace's scope = its members plus each member's git worktrees,
        # mirroring the UI (``getAllProjectIds``) and ``twicc sessions``.
        from twicc.projects import expand_project_ids_with_worktrees

        qs = qs.filter(project_id__in=expand_project_ids_with_worktrees(ws.get("projectIds", [])))

    if project is not None:
        # A project's scope = itself plus its own git worktrees, mirroring the UI
        # (``getProjectScopeIds``) and ``twicc sessions``.
        from twicc.projects import project_scope_ids

        qs = qs.filter(project_id__in=project_scope_ids(project))

    limit = resolve_limit(limit, paginated=paginated, default=20)
    total = qs.count() if paginated else None
    rows = qs[offset : offset + limit]
    emit_list(
        [serialize_artifact_bookmark(b) for b in rows],
        paginated=paginated, limit=limit, offset=offset, total=total,
    )
