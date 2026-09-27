"""Server-side content filtering for shared sessions (design §6.2). The display
ceiling and frozen line are enforced in SQL so nothing above them ever reaches
the viewer's network tab."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

# ItemDisplayLevel: ALWAYS=1, COLLAPSIBLE=2, DEBUG_ONLY=3.
# A max_display_mode caps which levels are visible. Only "debug" exposes level 3.
_CEILING = {
    "conversation": 2,
    "simplified": 2,
    "normal": 2,
    "debug": 3,
}


def display_ceiling(max_display_mode: str) -> int:
    return _CEILING.get(max_display_mode, 2)


def filtered_items_qs(session, *, max_display_mode: str, max_line: int | None, extra: Q | None = None):
    """Base queryset for a shared session's items, ceiling- and frozen-line-filtered.
    ``display_level`` NULL rows (uncomputed) are excluded except in debug (they'd
    only be visible there anyway)."""
    ceiling = display_ceiling(max_display_mode)
    qs = session.items.all()
    if ceiling < 3:
        qs = qs.filter(display_level__isnull=False, display_level__lte=ceiling)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    if extra is not None:
        qs = qs.filter(extra)
    return qs


def visible_call_lines(pairs: Iterable[tuple[str, int]], ceiling: int) -> set[tuple[str, int]]:
    """The ``(session id, line)`` pairs whose item is under the display ceiling.

    Same rule as :func:`filtered_items_qs`: below ``3`` an item needs a
    non-null ``display_level`` at or under the ceiling (a missing item is not
    visible). One query for all pairs; the share snapshot and the live
    ``share_agent_interaction`` relay both check a control card's call item
    with it, so a live viewer and a reloaded one agree.
    """
    from twicc.core.models import SessionItem

    pairs = set(pairs)
    if ceiling >= 3 or not pairs:
        return pairs
    rows = SessionItem.objects.filter(
        session_id__in={session_id for session_id, _ in pairs},
        line_num__in={line for _, line in pairs},
        display_level__isnull=False,
        display_level__lte=ceiling,
    ).values_list("session_id", "line_num")
    return {row for row in rows if row in pairs}


async def is_descendant_of(candidate, root, *, max_hops: int = 16) -> bool:
    """Whether ``candidate`` is a subagent descendant of ``root`` — walk
    ``parent_session`` up to ``max_hops`` (with a ``spawn_root`` shortcut)."""
    from asgiref.sync import sync_to_async

    if candidate.id == root.id:
        return False
    if candidate.spawn_root_id and candidate.spawn_root_id == root.id:
        return True
    node = candidate
    for _ in range(max_hops):
        parent_id = node.parent_session_id
        if parent_id is None:
            return False
        if parent_id == root.id:
            return True
        node = await sync_to_async(lambda pid=parent_id: type(root).objects.filter(id=pid).first())()
        if node is None:
            return False
    return False
