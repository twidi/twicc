"""Cursor-paginated event reads for the CLI and its generated RPC/MCP surfaces."""

from datetime import datetime

from django.db.models import Q, QuerySet

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.timeline_feed import (
    before_condition,
    build_sessions_table,
    cited_session_ids,
    format_occurred_at,
    serialize_event,
)


def fetch_timeline_page(
    *, project_ids: tuple[str, ...] | None, session_id: str | None,
    selected_sessions: QuerySet, require_session_metadata: bool,
    include_instance: bool, limit: int, before: tuple[datetime, int] | None,
    known_projects: dict[str, dict] | None = None, known_sessions: dict[str, dict] | None = None,
    workspace: dict | None = None,
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
    sessions = build_sessions_table(cited_session_ids(events), known_sessions=known_sessions)
    project_ids_needed = {event.project_id for event in events if event.project_id}
    project_ids_needed.update(row["project_id"] for row in sessions.values() if row["project_id"])
    projects = dict(known_projects or {})
    missing = project_ids_needed - projects.keys()
    if missing:
        projects.update((row["id"], row) for row in Project.objects.filter(id__in=sorted(missing)).values(
            "id", "name", "directory",
        ))

    # Event projects are recorded history; cited sessions carry their current projects.
    items = []
    for event in events:
        project = projects.get(event.project_id, {})
        item = serialize_event(event)
        item.update(
            session_title=sessions.get(event.session_id, {}).get("title"),
            project_name=project.get("name") or None,
            project_directory=project.get("directory") or None,
        )
        items.append(item)
    for row in sessions.values():
        project = projects.get(row["project_id"], {})
        row.update(project_name=project.get("name") or None, project_directory=project.get("directory") or None)
    result = {
        "items": items,
        "pagination": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        "sessions": sessions,
    }
    if workspace is not None:
        result["workspace"] = workspace
    return result
