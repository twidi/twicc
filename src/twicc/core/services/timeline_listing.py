"""Cursor-paginated event reads for the CLI and its generated RPC/MCP surfaces."""

from datetime import datetime

from django.db.models import Q, QuerySet

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.timeline_feed import (
    before_condition,
    cited_session_ids,
    format_occurred_at,
    serialize_event,
)


def _reference_tables(
    events: list[TimelineEvent], *, known_projects: dict[str, Project] | None,
    known_sessions: dict[str, Session] | None, full: bool, process_rows: dict | None,
) -> tuple[dict, dict]:
    """Load page references in batches and reuse the existing public payloads."""
    from twicc.cli._process_state import attach_process_blocks, load_process_rows, resolve_listing_twicc_pid
    from twicc.cli._session_payload import cli_session_payloads
    from twicc.core.serializers import serialize_project, slim_session
    from twicc.project_icons import load_repo_icon_cache

    cited = cited_session_ids(events)
    sessions = {sid: row for sid, row in (known_sessions or {}).items() if sid in cited}
    sessions.update(Session.objects.in_bulk(cited - sessions.keys()))
    project_ids = {event.project_id for event in events if event.project_id}
    project_ids.update(row.project_id for row in sessions.values() if row.project_id)
    projects = {pid: row for pid, row in (known_projects or {}).items() if pid in project_ids}
    projects.update(Project.objects.in_bulk(project_ids - projects.keys()))

    payloads = cli_session_payloads(
        [sessions[sid] for sid in sorted(sessions)],
        project_directories={pid: row.directory for pid, row in projects.items()},
    )
    if not full:
        payloads = [slim_session(row) for row in payloads]
    if process_rows is None:
        roots = [row["id"] for row in payloads if row.get("parent_session_id") is None]
        process_rows = load_process_rows(roots, resolve_listing_twicc_pid()) if roots else {}
    attach_process_blocks(payloads, process_rows, slim=not full)
    if projects:
        load_repo_icon_cache()
    return (
        {row["id"]: row for row in payloads},
        {pid: serialize_project(projects[pid]) for pid in sorted(projects)},
    )


def fetch_timeline_page(
    *, project_ids: tuple[str, ...] | None, session_id: str | None,
    selected_sessions: QuerySet, require_session_metadata: bool,
    include_instance: bool, limit: int, before: tuple[datetime, int] | None,
    known_projects: dict[str, Project] | None = None, known_sessions: dict[str, Session] | None = None,
    workspace: dict | None = None, full: bool = False, process_rows: dict | None = None,
) -> dict:
    """Select eligible target events before applying the event page boundary.

    ``None`` project IDs mean global; an empty tuple means no events. Missing
    target metadata remains eligible only for baseline history reads.
    """
    events: list[TimelineEvent] = []
    if project_ids != ():
        ordered = TimelineEvent.objects.order_by("-occurred_at", "-id")
        if before is not None:
            ordered = ordered.filter(before_condition(before))
        targets = ordered.filter(session_id__isnull=False)
        if project_ids is not None:
            targets = targets.filter(project_id__in=project_ids)
        if session_id is not None:
            targets = targets.filter(session_id=session_id)

        eligible = Q(session_id__in=selected_sessions.order_by().values("id"))
        if not require_session_metadata:
            eligible |= ~Q(session_id__in=Session.objects.order_by().values("id"))
        queries = [targets.filter(eligible)]
        if include_instance:
            queries.append(ordered.filter(session_id__isnull=True))

        # Each branch reads at most one page plus lookahead, as the REST feed does.
        merged = {event.id: event for query in queries for event in query[:limit + 1]}
        events = sorted(merged.values(), key=lambda event: (event.occurred_at, event.id), reverse=True)[:limit + 1]

    has_more = len(events) > limit
    events = events[:limit]
    next_cursor = None
    if has_more:
        last = events[-1]
        next_cursor = f"{format_occurred_at(last.occurred_at)},{last.id}"
    sessions, projects = _reference_tables(
        events, known_projects=known_projects, known_sessions=known_sessions, full=full, process_rows=process_rows,
    )
    result = {
        "items": [serialize_event(event) for event in events],
        "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        "sessions": sessions,
        "projects": projects,
    }
    if workspace is not None:
        result["workspace"] = workspace
    return result
