"""Timeline orchestration with shared session selection and scope validation."""

from typing import NamedTuple

from twicc.cli._output import emit_error, emit_json, resolve_limit


class TimelineScope(NamedTuple):
    project_ids: tuple[str, ...] | None
    session_id: str | None


def resolve_timeline_scope(
    *, project: str | None, workspace: str | None, session: str | None,
) -> TimelineScope:
    """Resolve explicit scopes; distinguish global from an empty workspace."""
    from twicc.cli._session_selection import resolve_explicit_ids
    from twicc.core.models import Project, Session
    from twicc.projects import expand_project_ids_with_worktrees, project_scope_ids
    from twicc.workspaces import read_workspaces

    if project is not None:
        if not Project.objects.filter(pk=project).exists():
            emit_error(f"Error: project '{project}' not found.")
        return TimelineScope(tuple(project_scope_ids(project)), None)
    if workspace is not None:
        definition = next((w for w in read_workspaces().get("workspaces", []) if w.get("id") == workspace), None)
        if definition is None:
            emit_error(f"Error: workspace '{workspace}' not found.")
        return TimelineScope(tuple(expand_project_ids_with_worktrees(definition.get("projectIds", []))), None)
    if session is not None:
        session_id = resolve_explicit_ids([session])[0]
        if not Session.objects.filter(pk=session_id).exists():
            emit_error(f"Error: session '{session_id}' not found.")
        return TimelineScope(None, session_id)
    return TimelineScope(None, None)


def main(
    *, project: str | None = None, workspace: str | None = None, session: str | None = None,
    provider: str | None = None, annotation: list[str] | None = None,
    spawned_by: str | None = None, spawn_tree: str | None = None,
    descendants: str | None = None, siblings: str | None = None,
    state: list[str] | None = None, active: bool = False,
    include_hidden: bool = False, only_hidden: bool = False, include_archived: bool = False,
    limit: int | None = None, before: str | None = None,
) -> None:
    """Return a timeline page. Session filters use current metadata."""
    import django

    django.setup()

    from twicc.cli._session_selection import reject_conflicting_scopes
    from twicc.cli.sessions import build_filtered_queryset
    from twicc.core.services.timeline_feed import MAX_LIMIT, FeedError, parse_cursor
    from twicc.core.services.timeline_listing import fetch_timeline_page

    if sum(value is not None for value in (project, workspace, session)) > 1:
        emit_error("Error: --project, --workspace and --session are mutually exclusive.", code=2)
    reject_conflicting_scopes(spawned_by, spawn_tree, descendants, siblings)
    if include_hidden and only_hidden:
        emit_error("Error: --include-hidden and --only-hidden are mutually exclusive.", code=2)
    limit = resolve_limit(limit)
    if not 1 <= limit <= MAX_LIMIT:
        emit_error(f"Error: --limit must be between 1 and {MAX_LIMIT}.", code=2)
    try:
        boundary = parse_cursor(before) if before is not None else None
    except FeedError as exc:
        emit_error(f"Error: {exc}", code=2)

    filiation = any(value is not None for value in (spawned_by, spawn_tree, descendants, siblings))
    selected, _ = build_filtered_queryset(
        archived=True, require_indexed=False, root_only=filiation, filiation_includes_hidden=False,
        provider=provider, annotation=annotation, spawned_by=spawned_by, spawn_tree=spawn_tree,
        descendants=descendants, siblings=siblings, state=state, active=active,
        include_hidden=include_hidden, only_hidden=only_hidden,
    )
    scope = resolve_timeline_scope(project=project, workspace=workspace, session=session)
    if scope.project_ids is not None:
        selected = selected.filter(project_id__in=scope.project_ids)
    if scope.session_id is not None:
        selected = selected.filter(pk=scope.session_id)

    # Supplied filters matter even when the builder simplifies them (all states).
    require_metadata = session is not None or provider is not None or bool(
        annotation or filiation or state or active or only_hidden
    )
    emit_json(fetch_timeline_page(
        project_ids=scope.project_ids, session_id=scope.session_id, selected_sessions=selected,
        require_session_metadata=require_metadata,
        include_instance=not require_metadata and scope.project_ids != (), limit=limit, before=boundary,
    ))
