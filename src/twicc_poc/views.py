"""API views and SPA catch-all for serving the frontend."""

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse

import orjson

from twicc_poc.compute import get_message_content_list
from twicc_poc.core.models import Project, Session, SessionItem, SessionItemLink
from twicc_poc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)


def project_list(request):
    """GET /api/projects/ - List all projects."""
    projects = Project.objects.all()
    data = [serialize_project(p) for p in projects]
    return JsonResponse(data, safe=False)


def project_detail(request, project_id):
    """GET /api/projects/<id>/ - Detail of a project."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")
    return JsonResponse(serialize_project(project))


def project_sessions(request, project_id):
    """GET /api/projects/<id>/sessions/ - Sessions of a project."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")
    sessions = project.sessions.all()
    data = [serialize_session(s) for s in sessions]
    return JsonResponse(data, safe=False)


def session_detail(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/ - Detail of a session."""
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")
    return JsonResponse(serialize_session(session))


def session_items(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/items/ - Items of a session.

    Query params (optional):
        range: one or more ranges (can be repeated)
            Formats:
            - "N" : exact line N
            - "min:max" : lines min to max (inclusive)
            - "min:" : all lines from min onwards
            - ":max" : all lines up to max

    Examples:
        ?range=5                -> line 5 only
        ?range=0:15             -> lines 0 to 15 inclusive
        ?range=10:              -> all lines from 10 onwards
        ?range=:10              -> all lines up to 10
        ?range=0:10&range=20:30&range=50:  -> multiple ranges combined
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    items = session.items.all()

    # Filter by line_num ranges if provided
    ranges = request.GET.getlist("range")
    if ranges:
        from django.db.models import Q

        q_filter = Q()
        for r in ranges:
            try:
                if ":" not in r:
                    # Single number = exact line
                    line_val = int(r)
                    q_filter |= Q(line_num=line_val)
                else:
                    min_str, max_str = r.split(":", 1)
                    min_val = int(min_str) if min_str else None
                    max_val = int(max_str) if max_str else None

                    if min_val is not None and max_val is not None:
                        q_filter |= Q(line_num__gte=min_val, line_num__lte=max_val)
                    elif min_val is not None:
                        q_filter |= Q(line_num__gte=min_val)
                    elif max_val is not None:
                        q_filter |= Q(line_num__lte=max_val)
                    # Both empty = invalid, skip
            except ValueError:
                continue  # Skip invalid ranges

        if q_filter:
            items = items.filter(q_filter)

    data = [serialize_session_item(item) for item in items]
    return JsonResponse(data, safe=False)


def session_items_metadata(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/items/metadata/ - Metadata of all items.

    Returns all items with metadata fields but WITHOUT content.
    Used for initial session load to build the visual items list.
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    items = session.items.all().defer('content')  # Already ordered by line_num (see Meta.ordering)
    data = [serialize_session_item_metadata(item) for item in items]
    return JsonResponse(data, safe=False)


def tool_results(request, project_id, session_id, line_num, tool_id):
    """GET /api/projects/<id>/sessions/<session_id>/items/<line_num>/tool-results/<tool_id>/

    Returns the tool_result content(s) for a specific tool_use.
    Uses SessionItemLink to find related tool_result items.
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Find links for this tool_use
    links = SessionItemLink.objects.filter(
        session=session,
        source_line_num=line_num,
        link_type='tool_result',
        reference=tool_id,
    ).values_list('target_line_num', flat=True)

    if not links:
        return JsonResponse({"results": []})

    # Fetch the target items, ordered by line_num (chronological)
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=links,
    ).order_by('line_num')

    # Extract tool_result entries from each item's content
    results = []
    for item in items:
        try:
            parsed = orjson.loads(item.content)
        except orjson.JSONDecodeError:
            continue

        content_list = get_message_content_list(parsed, 'user')
        if not content_list:
            continue
        for entry in content_list:
            if entry.get('type') == 'tool_result' and entry.get('tool_use_id') == tool_id:
                results.append(entry)

    return JsonResponse({"results": results})


def spa_index(request):
    """Catch-all for Vue Router - serves index.html."""
    index_path = settings.BASE_DIR / "frontend" / "dist" / "index.html"
    if not index_path.exists():
        raise Http404("Frontend not built. Run 'npm run build' in frontend/")
    return FileResponse(open(index_path, "rb"), content_type="text/html")
