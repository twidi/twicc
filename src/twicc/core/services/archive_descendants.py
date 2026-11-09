"""Archive the eligible hidden descendants of a session.

A hidden session never appears in the archive dialog, so the frontend cannot
decide for it: it has no live state for hidden sessions, and the session
``PATCH`` route refuses them. The backend applies the rules itself, when the
target is archived. See ``docs/plans/2026-11-09-archive-with-descendants-spec.md``.

Nothing is broadcast: hidden sessions never surface in the frontend.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import NamedTuple

from asgiref.sync import sync_to_async

from twicc.agent.states import AgentState
from twicc.cli._process_state import AWAITING_VIRTUAL_STATE, load_process_rows, project_virtual_state
from twicc.core.models import Session
from twicc.core.services.session_crons import active_cron_counts
from twicc.core.services.session_update import apply_session_archived_change

logger = logging.getLogger(__name__)

_BUSY_STATES = frozenset({
    AgentState.STARTING.value,
    AgentState.ASSISTANT_TURN.value,
    AWAITING_VIRTUAL_STATE,
})


class _Descendant(NamedTuple):
    id: str
    spawned_by_id: str
    tree_detached: bool
    archived: bool
    hidden: bool
    pinned: str | None


def _load_children(target_id: str) -> dict[str, list[_Descendant]]:
    """Return the children of each walked session, with one query per level.

    The children of a detached session are not loaded: the walk never reaches
    them. ``seen`` ends the walk on a ``spawned_by`` cycle.
    """
    children: dict[str, list[_Descendant]] = {}
    seen = {target_id}
    level = [target_id]
    while level:
        rows = (
            Session.objects
            .filter(spawned_by_id__in=level)
            .order_by("created_at", "id")
            .values_list(*_Descendant._fields)
        )
        level = []
        for descendant in map(_Descendant._make, rows):
            if descendant.id in seen:
                continue
            seen.add(descendant.id)
            children.setdefault(descendant.spawned_by_id, []).append(descendant)
            if not descendant.tree_detached:
                level.append(descendant.id)
    return children


def _is_barrier(descendant: _Descendant, process_row, cron_count: int) -> bool:
    """Tell whether the descendant must not be archived automatically."""
    return (
        project_virtual_state(process_row) in _BUSY_STATES
        # A DEAD row always holds ``None`` here: the state transition clears it.
        or (process_row is not None and process_row.background_work_in_progress is not None)
        or cron_count > 0
        or bool(descendant.pinned)
    )


def select_hidden_descendants(target_id: str, archived_ids: set[str]) -> list[str]:
    """Return the hidden descendants of the target to archive, parents first.

    ``archived_ids`` holds the listed sessions that the confirmation archives.
    The first matching case applies to each child. ``eligible`` tells whether
    a hidden session at this place of the walk can be selected:

    ========================  ==================  ===================
    Child                     Effect              Its children
    ========================  ==================  ===================
    detached                  skip its subtree    not walked
    in ``archived_ids``       none                eligible
    already archived          none                ``eligible`` as is
    hidden and barrier        none                not eligible
    hidden, not barrier       select if eligible  ``eligible`` as is
    any other (listed)        none                not eligible
    ========================  ==================  ===================

    The barrier state is read now, at the call: the frontend has no live state
    for hidden sessions.
    """
    children = _load_children(target_id)

    candidate_ids = [
        descendant.id
        for siblings in children.values()
        for descendant in siblings
        if descendant.hidden and not descendant.archived and descendant.id not in archived_ids
    ]
    process_rows = load_process_rows(candidate_ids, os.getpid())
    cron_counts = active_cron_counts(candidate_ids)

    selected: list[str] = []
    queue = deque([(target_id, True)])
    while queue:
        parent_id, eligible = queue.popleft()
        for child in children.get(parent_id, ()):
            if child.tree_detached:
                continue
            if child.id in archived_ids:
                child_eligible = True
            elif child.archived:
                child_eligible = eligible
            elif not child.hidden or _is_barrier(child, process_rows.get(child.id), cron_counts[child.id]):
                child_eligible = False
            else:
                if eligible:
                    selected.append(child.id)
                child_eligible = eligible
            queue.append((child.id, child_eligible))
    return selected


async def archive_hidden_descendants(target_id: str, archived_ids: set[str]) -> tuple[list[str], list[str]]:
    """Archive the eligible hidden descendants of the target.

    Each session succeeds or fails alone: a failure is logged, reported in the
    second list, and does not stop the others. Return ``(archived, failed)``.
    """
    archived: list[str] = []
    failed: list[str] = []
    for session_id in await sync_to_async(select_hidden_descendants)(target_id, archived_ids):
        try:
            session = await Session.objects.aget(id=session_id)
            await apply_session_archived_change(session, True)
        except Exception:
            logger.exception("[archive_hidden_descendants] failed to archive %s", session_id)
            failed.append(session_id)
        else:
            archived.append(session_id)
    return archived, failed
