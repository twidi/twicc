"""Shared projection + serialization helpers for the ``process`` CLI family.

Centralizes the virtual-state vocabulary (``ProcessRun.state`` +
``ProcessRun.awaiting_user_input`` → one of 5 values) and the row serialization
shared by:

- ``processes.py`` (``twicc processes``)
- ``process.py`` (``twicc process <ID>``)
- ``process_wait.py`` (``twicc process <ID> wait <STATUS>...``)
- ``processes_get.py`` (``twicc processes get <SESSION_ID>...``)

The first two never observe a DEAD row (their queryset filters it out at the
SQL level). ``wait`` and ``get`` are the only callers that can: they have to
detect the "no live process" condition as a matchable / reportable state, so
they pull the most-recent row regardless of state and let the projection
collapse DEAD (and absence of a row) to the virtual value ``"dead"``.
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

# Subset accepted by ``processes`` and ``process`` (their --state filter must
# reject 'dead' to keep the "live processes only" guarantee).
LIVE_VIRTUAL_STATES = VALID_VIRTUAL_STATES - {DEAD_VIRTUAL_STATE}


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


def serialize_process_row(row, session) -> dict:
    """Serialize a (ProcessRun, Session) pair into the common output schema.

    ``row`` MUST be a real ``ProcessRun`` (use :func:`serialize_dead_process_row`
    for the "no row, project to dead" case). ``session`` may be ``None``
    when the watcher has not created the ``Session`` row yet — title and
    ``project_id`` fall back to ``None`` then.
    """
    return {
        "id": row.pk,
        "provider": row.provider,
        "session_id": row.session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": project_virtual_state(row),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_state_change_at": (
            row.last_state_change_at.isoformat()
            if row.last_state_change_at
            else None
        ),
        "pid": row.agent_pid,
    }


def serialize_dead_process_row(session, *, session_id: str) -> dict:
    """Serialize the "no live ProcessRun row" case into the common output schema.

    Returns the same shape as :func:`serialize_process_row` with ``state="dead"``
    and every row-bound field (``id``, ``started_at``, ``last_state_change_at``,
    ``pid``) set to ``None``. ``provider``, ``session_title`` and ``project_id``
    fall back to the ``Session`` row when one exists, else ``None``.
    """
    return {
        "id": None,
        "provider": session.provider if session is not None else None,
        "session_id": session_id,
        "session_title": session.title if session is not None else None,
        "project_id": session.project_id if session is not None else None,
        "state": DEAD_VIRTUAL_STATE,
        "started_at": None,
        "last_state_change_at": None,
        "pid": None,
    }


def serialize_wait_result(
    row,
    session,
    *,
    session_id: str,
    matched_state: str,
) -> dict:
    """Serialize the result of a ``wait`` match.

    Adds ``matched_state`` to the standard schema. Accepts ``row = None``
    for the "wait dead matched, no row" case — in that branch the row-bound
    fields (``id``, ``provider``, ``started_at``, ``last_state_change_at``,
    ``pid``) are ``null`` and ``state`` is forced to ``"dead"``. The
    ``session_id`` argument is the one the CLI received, used as fallback
    when no row exists to carry it.
    """
    if row is None:
        return {
            "id": None,
            "provider": session.provider if session is not None else None,
            "session_id": session_id,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "state": DEAD_VIRTUAL_STATE,
            "matched_state": matched_state,
            "started_at": None,
            "last_state_change_at": None,
            "pid": None,
        }
    data = serialize_process_row(row, session)
    data["matched_state"] = matched_state
    return data


def serialize_get_result(
    row,
    session,
    *,
    session_id: str,
    session_known: bool,
) -> dict:
    """Serialize one entry of a ``processes get`` lookup.

    Adds ``session_known`` to the standard schema. Accepts ``row = None``
    for session_ids whose live ``ProcessRun`` row is missing — in that
    branch the row-bound fields (``id``, ``started_at``,
    ``last_state_change_at``, ``pid``) are ``null``; ``provider``,
    ``session_title`` and ``project_id`` fall back to the ``Session`` row
    when one exists, else are also ``null``.

    ``session_known`` is the caller's claim that TwiCC has at least one
    trace of the session (a ``Session`` row OR any ``ProcessRun`` row,
    even DEAD). ``False`` flags typos, periodic cleanup, or a foreign
    session_id from a stale script — distinguishing them from a session
    that genuinely existed but whose live process is gone.
    """
    if row is None:
        return {
            "id": None,
            "provider": session.provider if session is not None else None,
            "session_id": session_id,
            "session_title": session.title if session is not None else None,
            "project_id": session.project_id if session is not None else None,
            "state": DEAD_VIRTUAL_STATE,
            "session_known": session_known,
            "started_at": None,
            "last_state_change_at": None,
            "pid": None,
        }
    data = serialize_process_row(row, session)
    data["session_known"] = session_known
    return data


def load_process_rows(session_ids, twicc_pid: int | None) -> dict:
    """Return the most recent ``ProcessRun`` per session id, keyed by id.

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

    rows_by_id: dict = {}
    for row in (
        ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id__in=session_ids)
        .order_by("session_id", "-started_at")
    ):
        if row.session_id not in rows_by_id:
            rows_by_id[row.session_id] = row
    return rows_by_id


def serialize_compact_process(row, *, slim: bool = False) -> dict:
    """Serialize one process row for a payload that already identifies the session.

    Five fields instead of :func:`serialize_process_row`'s nine: ``provider``,
    ``session_id``, ``session_title`` and ``project_id`` are dropped because
    the session payload this rides on already carries them, in both
    projections. ``slim`` narrows further to ``state`` alone.

    ``row=None`` means "the table was read and holds nothing for this session"
    → ``state="dead"`` with the row-bound fields null. It does NOT mean "could
    not read": that case is the caller's, and it emits no block at all.
    """
    state = project_virtual_state(row)
    if slim:
        return {"state": state}
    return {
        "id": row.pk if row is not None else None,
        "state": state,
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
