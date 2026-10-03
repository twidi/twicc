"""Shared projection + serialization helpers for the live process state of sessions.

Centralizes the virtual-state vocabulary (``ProcessRun.state`` +
``ProcessRun.awaiting_user_input`` → one of 5 values) and the compact
``process`` block the session commands (``sessions``, ``sessions get``,
``session <id>``, ``topology``, ...) attach to their payloads. The projection
collapses DEAD (and the absence of a row) to the virtual value ``"dead"``.
"""

from __future__ import annotations

from twicc.agent.states import AgentState

DEAD_VIRTUAL_STATE = "dead"
AWAITING_VIRTUAL_STATE = "awaiting_user_input"

VALID_VIRTUAL_STATES = frozenset({
    AgentState.STARTING.value,
    AgentState.ASSISTANT_TURN.value,
    AgentState.USER_TURN.value,
    AWAITING_VIRTUAL_STATE,
    DEAD_VIRTUAL_STATE,
})


def project_virtual_state(row) -> str:
    """Project a ``ProcessRun`` row onto the 5-value virtual vocabulary.

    Rules (in order):

    - ``row is None`` or ``row.state == DEAD`` → ``"dead"``
    - ``row.awaiting_user_input`` → ``"awaiting_user_input"``
      (always implies ``state == ASSISTANT_TURN`` by construction)
    - otherwise → ``row.state`` (``"starting"`` / ``"assistant_turn"`` /
      ``"user_turn"``)
    """
    if row is None or row.state == AgentState.DEAD.value:
        return DEAD_VIRTUAL_STATE
    if row.awaiting_user_input:
        return AWAITING_VIRTUAL_STATE
    return row.state


def load_process_rows(session_ids, twicc_pid: int | None) -> dict:
    """Return the most recent ``ProcessRun`` per session id, keyed by id.

    ``session_ids=None`` loads every row of the live instance instead of a
    given page — what a state filter needs, since it must know the whole live
    set before the listing is paginated, not after.

    Scoped to ``twicc_pid`` because rows outlive the instance that wrote them:
    the boot cleanup only runs at the *next* startup, so after a crash the
    table still holds the previous backend's rows, frozen mid-turn. Without
    the filter they would read as live agents.

    ``twicc_pid=None`` (no live backend) returns nothing rather than querying.
    The column is nullable — "unknown for rows imported from older schemas" —
    so a ``None`` reaching the ORM would render ``twicc_pid IS NULL`` and match
    exactly those legacy rows. The guard lives here rather than at each call
    site because that failure is silent.

    DEAD rows are deliberately kept: :func:`project_virtual_state` collapses
    them onto ``"dead"``, which is the answer a listing wants.
    """
    if twicc_pid is None:
        return {}

    from twicc.core.models import ProcessRun

    qs = ProcessRun.objects.filter(twicc_pid=twicc_pid)
    if session_ids is not None:
        qs = qs.filter(session_id__in=session_ids)

    rows_by_id: dict = {}
    for row in qs.order_by("session_id", "-started_at"):
        if row.session_id not in rows_by_id:
            rows_by_id[row.session_id] = row
    return rows_by_id


def serialize_compact_process(row, *, slim: bool = False) -> dict:
    """Serialize one process row for a payload that already identifies the session.

    Six fields: ``id``, ``state``, ``background_work_in_progress``,
    ``started_at``, ``last_state_change_at`` and ``pid`` — the session identity
    (``session_id``, title, ``project_id``, ...) is already in the session
    payload this rides on. ``slim`` narrows it to ``state`` and
    ``background_work_in_progress``.

    ``background_work_in_progress`` is what still runs behind the agent,
    whatever ``state`` says (see
    :func:`twicc.agent.states.build_background_work`): a background shell
    keeps running in ``user_turn``. ``None`` when nothing does, and always
    ``None`` for ``state="dead"``. Always present, so the shape never depends
    on the value.

    ``row=None`` means "the table was read and holds nothing for this session"
    → ``state="dead"`` with the row-bound fields null. It does NOT mean "could
    not read": that case is the caller's, and it emits no block at all.
    """
    state = project_virtual_state(row)
    background_work = (
        row.background_work_in_progress
        if row is not None and state != DEAD_VIRTUAL_STATE
        else None
    ) or None
    if slim:
        return {"state": state, "background_work_in_progress": background_work}
    return {
        "id": row.pk if row is not None else None,
        "state": state,
        "background_work_in_progress": background_work,
        "started_at": (
            row.started_at.isoformat() if row is not None and row.started_at else None
        ),
        "last_state_change_at": (
            row.last_state_change_at.isoformat()
            if row is not None and row.last_state_change_at
            else None
        ),
        "pid": row.agent_pid if row is not None else None,
    }


def resolve_listing_twicc_pid() -> int | None:
    """Pid of the live backend, or ``None`` when none is running.

    Imported inside the function on purpose: the process-family tests patch
    ``twicc.cli._twicc_info.resolve_live_twicc``, and a module-level import
    would bind past the patch.
    """
    from twicc.cli._twicc_info import resolve_live_twicc

    info = resolve_live_twicc()
    return info.pid if info is not None else None


def attach_process_blocks(entries, rows_by_id, *, slim: bool) -> None:
    """Add the ``process`` key to already-serialized session entries, in place.

    ``None`` means one thing: a session that cannot own a process — a subagent,
    which runs inside its parent's and never has a ``ProcessRun`` row.

    Everything else gets a block, and a missing row is ``state="dead"``.
    **Including when no backend is running**: an agent does not outlive its
    TwiCC instance (``agent/process_run_cleanup.py`` forces the point at the
    next startup), so "TwiCC is down" and "TwiCC runs nothing for this session"
    are the same fact reached by two roads. An earlier version answered ``None``
    for the first, out of a caution that bought nothing — it only gave ``None``
    a second meaning.

    ``dead`` means "no TwiCC-managed process", not "this session is not
    running": TwiCC never started most of the sessions it indexes.
    """
    for entry in entries:
        if entry.get("parent_session_id") is not None:
            entry["process"] = None
            continue
        entry["process"] = serialize_compact_process(
            rows_by_id.get(entry["id"]), slim=slim,
        )


def live_session_ids(rows_by_id) -> set:
    """Ids whose row projects to anything but ``dead``.

    The loader keeps DEAD rows on purpose — :func:`project_virtual_state`
    collapses them — so "has a live process" is decided here rather than in
    the query.
    """
    return {
        session_id for session_id, row in rows_by_id.items()
        if project_virtual_state(row) != DEAD_VIRTUAL_STATE
    }


def session_ids_in_state(rows_by_id, state: str) -> set:
    """Ids whose row projects to exactly ``state`` (never ``dead``)."""
    return {
        session_id for session_id, row in rows_by_id.items()
        if project_virtual_state(row) == state
    }
