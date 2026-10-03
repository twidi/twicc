"""hide_session / unhide_session — orchestrate the hidden-flag flip.

Both entry points are async (they touch DB + broadcast + FTS), receive
the already-fetched ``Session`` row, and return a structured result the
caller turns into a status file or WS payload. They share private
helpers for the recompute side-effects.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


class SessionVisibilityError(NamedTuple):
    field: str
    code: str
    message: str


class SessionVisibilityResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SessionVisibilityError] | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def hide_session(session) -> SessionVisibilityResult:
    """Flip hidden False -> True.

    Pre-conditions: session is type=SESSION, permission_mode is in the
    hidden whitelist for its provider, question_widget is not True
    (Claude Code).
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if session.hidden:
        return _ok(session)  # no-op: already hidden
    errors = _check_hidden_invariants(session)
    if errors:
        return _fail(session, errors)

    await _apply_flip(session, new_hidden=True)
    await _broadcast_session_removed(session.id)
    await _broadcast_project_updated(session.project_id)
    return _ok(session)


async def unhide_session(session) -> SessionVisibilityResult:
    """Flip hidden True -> False.

    No invariant checks beyond type=SESSION: the session re-enters the
    user surface, the user can reconfigure permission_mode / question_widget
    freely afterwards.
    """
    errors = _check_type_session(session)
    if errors:
        return _fail(session, errors)
    if not session.hidden:
        return _ok(session)  # no-op: already visible

    await _apply_flip(session, new_hidden=False)
    await _broadcast_session_updated(session)
    await _broadcast_project_updated(session.project_id)
    await _broadcast_live_process_state(session.id)
    return _ok(session)


# ---------------------------------------------------------------------------
# Pre-conditions
# ---------------------------------------------------------------------------


def _check_type_session(session) -> list[SessionVisibilityError]:
    from twicc.core.models import SessionType
    if session.type != SessionType.SESSION:
        return [SessionVisibilityError(
            "type", "not_top_level",
            "Only top-level sessions (type=SESSION) can be hidden; "
            f"got type={session.type!r}.",
        )]
    return []


def _check_hidden_invariants(session) -> list[SessionVisibilityError]:
    """Run validate_hidden_constraints against the current Session row.

    We read the columns directly (the session is already saved, no preset to merge).
    """
    from twicc.cli._drop_request.validation import validate_hidden_constraints
    from twicc.providers.helpers import AgentSettings

    settings = AgentSettings.from_session(session)
    vlist = validate_hidden_constraints(
        session.provider, settings, hidden=True,
    )
    return [SessionVisibilityError(v.field, v.code, v.message) for v in vlist]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_visibility_flag(session_id: str, new_hidden: bool):
    """Commit eligibility and all affected activity buckets together."""
    from django.db import transaction
    from twicc.core.models import Session
    from twicc.providers.live_aggregates import apply_contribution_changes, session_contribution

    with transaction.atomic():
        session = Session.objects.get(id=session_id)
        before = session_contribution(session)
        session.hidden = new_hidden
        session.save(update_fields=['hidden'])
        apply_contribution_changes([], [], before_sessions=[before],
            after_sessions=[session_contribution(session)], repair=True)
        session.refresh_from_db()
    return session


async def _apply_flip(session, *, new_hidden: bool) -> None:
    """Commit visibility and counters, then refresh search and broadcasts."""
    from twicc.search import reindex_session
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.db_writer import run_under_db_write_lock

    @sync_to_async
    def _recompute():
        # Project metadata and search remain best-effort after the commit.
        try:
            # 2. Recompute sessions_count on the Project.
            #    update_project_metadata takes project_id (str), not a Project instance.
            from twicc.projects import update_project_metadata
            update_project_metadata(session.project_id)

            # Activity aggregates already committed with the hidden flag.
            reindex_session(session.id)
        except Exception:
            logger.exception(
                "session_visibility flip side-effects failed for session %s "
                "(hidden=%s). Project metadata/search may be stale "
                "until next refresh.",
                session.id, new_hidden,
            )

    saved = await run_under_db_write_lock(lambda: run_compute_sync(_apply_visibility_flag, session.id, new_hidden))
    session.hidden = saved.hidden
    session.user_message_count = saved.user_message_count
    session.self_cost = saved.self_cost
    session.subagents_cost = saved.subagents_cost
    session.total_cost = saved.total_cost

    # Silence (or restore) the live agent's own broadcasts the instant the flag
    # is durable — before the recompute below, which reindexes the whole session
    # and dominates the flip's few hundred milliseconds. A streaming agent emits
    # one frame per token into the same bounded per-client queue that must carry
    # ``session_removed``, so every millisecond of delay here is more frames
    # queued ahead of it.
    _push_hidden_to_live_agent(session.id, new_hidden)

    await _recompute()


def _push_hidden_to_live_agent(session_id: str, hidden: bool) -> None:
    """Hand the new ``hidden`` value to the live agent, if the session has one.

    The agent gates its live-update broadcasts on a cached copy of the flag
    (``BaseAgent._is_session_hidden``) so the streaming path never queries the
    DB. This push is what keeps that copy exact; ``hide_session`` /
    ``unhide_session`` are the only writers of the column on an existing row.

    Best-effort by design: a broken push must never fail the flip itself. The
    agent would keep broadcasting until it dies, which is the pre-existing
    behaviour, not a new failure.
    """
    # Local import: crosses the services -> agent layer, kept out of this
    # module's import graph (same convention as the sessions watcher).
    from twicc.agent.registry import get_agent_manager_registry

    try:
        get_agent_manager_registry().set_session_hidden(session_id, hidden)
    except Exception:
        logger.exception(
            "Failed to push hidden=%s to the live agent for session %s; its "
            "live broadcasts stay ungated until it stops.",
            hidden, session_id,
        )


# ---------------------------------------------------------------------------
# Broadcasts
# ---------------------------------------------------------------------------


async def _broadcast_session_removed(session_id: str) -> None:
    """Emit a session_removed WS event so connected clients drop the row."""
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "session_removed", "session_id": session_id},
    })


async def _broadcast_live_process_state(session_id: str) -> None:
    """Re-state the live agent's process state once the session is visible again.

    Hiding made clients drop the session's process state, and no state is
    broadcast while it is hidden — so a session unhidden mid-run would show no
    activity until its next transition. Sent after ``session_updated``, so the
    row is back first, as a ``resync`` (no notification: nothing happened).
    Best-effort: a failure must not fail the flip.
    """
    # Local imports: crosses the services -> agent / ASGI layers.
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.asgi import broadcast_process_state

    try:
        info = get_agent_manager_registry().get_agent_info(session_id)
        if info is not None:
            await broadcast_process_state(info, resync=True)
    except Exception:
        logger.exception("Failed to re-broadcast the process state of unhidden session %s", session_id)


async def _broadcast_session_updated(session) -> None:
    """Emit a session_updated WS event with the freshly visible session."""
    from twicc.core.serializers import serialize_session
    layer = get_channel_layer()
    if layer is None:
        return
    payload = await sync_to_async(serialize_session)(session)
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "session_updated", "session": payload},
    })


async def _broadcast_project_updated(project_id: str) -> None:
    """Emit a project_updated WS event (sessions_count + cost may have changed)."""
    from twicc.core.models import Project
    from twicc.core.serializers import serialize_project

    layer = get_channel_layer()
    if layer is None:
        return
    try:
        if project := await sync_to_async(Project.objects.filter(id=project_id).first)():
            await layer.group_send("updates", {
                "type": "broadcast",
                "data": {"type": "project_updated", "project": serialize_project(project)},
            })
    except Exception:
        logger.exception("Failed to broadcast project_updated for %s", project_id)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _ok(session) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=True,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=None,
    )


def _fail(session, errors) -> SessionVisibilityResult:
    return SessionVisibilityResult(
        success=False,
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        errors=errors,
    )
