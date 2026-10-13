# src/twicc/core/services/session_timeline.py
"""Recording of timeline events (design §6).

Every hook calls :func:`record_event_sync` (from code already running in a
worker thread, inside or outside a transaction) or :func:`record_event` (from
async code). Neither ever raises: a failed timeline write is logged and must
never block or undo the user action it describes.
"""

from __future__ import annotations

import asyncio
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.timeline import STARTED_OF, inherit_started_fields, validate_data
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)


def record_event_sync(
    kind: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    occurred_at=None,
    precision: str = TimelinePrecision.EXACT,
    actor: str,
    reason: str = "",
    caused_by: TimelineEvent | int | None = None,
    data: dict | None = None,
    dedupe_key: str | None = None,
) -> TimelineEvent | None:
    """Write one event. Returns the row, or ``None`` on failure or dedupe hit.

    The insert runs in its own savepoint: when the caller already holds a
    transaction, a failed insert rolls back only this row. For an ``ended`` kind
    listed in ``STARTED_OF``, the payload fields the caller did not provide are
    copied from the open ``started`` row of the same session (design §3).
    """
    try:
        if kind in STARTED_OF:
            data = inherit_started_fields(kind, data, _open_started_data(session_id, kind))
        payload = validate_data(kind, data)
        caused_by_id = caused_by.pk if isinstance(caused_by, TimelineEvent) else caused_by
        with transaction.atomic():
            if (
                dedupe_key is not None
                and TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key).exists()
            ):
                return None
            return TimelineEvent.objects.create(
                session_id=session_id,
                project_id=project_id,
                kind=kind,
                occurred_at=occurred_at or timezone.now(),
                precision=precision,
                actor=actor,
                reason=reason,
                caused_by_id=caused_by_id,
                data=payload,
                dedupe_key=dedupe_key,
            )
    except IntegrityError:
        if dedupe_key is not None:
            logger.debug("Timeline event %s deduplicated by the database for %s", kind, session_id)
        else:
            logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None
    except Exception:
        logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None


async def record_event(kind: str, **kwargs) -> TimelineEvent | None:
    """Async twin of :func:`record_event_sync`, under the DB write lock.

    The lock is reentrant for the task that already holds it, so this is safe
    to call from inside another ``run_under_db_write_lock`` callback.
    """
    return await run_under_db_write_lock(
        lambda: asyncio.to_thread(lambda: record_event_sync(kind, **kwargs))
    )


def latest_event_id(session_id: str, kind: str) -> int | None:
    """Id of the most recent (highest id) event of ``kind`` for ``session_id``, if any."""
    return (
        TimelineEvent.objects.filter(session_id=session_id, kind=kind)
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )


def _open_started_data(session_id: str | None, ended_kind: str) -> dict | None:
    """``data`` of the open ``started`` row matching ``ended_kind``, or ``None``.

    The open row is the latest ``started`` row of ``session_id`` (``None`` = instance
    events), by id, with no row of ``ended_kind`` for the same session and a greater
    id (ids, never ``occurred_at``: lost-end rows are back-dated): a closed interval
    is never inherited.
    """
    started = (
        TimelineEvent.objects.filter(session_id=session_id, kind=STARTED_OF[ended_kind])
        .order_by("-id")
        .values("id", "data")
        .first()
    )
    if started is None:
        return None
    if TimelineEvent.objects.filter(session_id=session_id, kind=ended_kind, id__gt=started["id"]).exists():
        return None
    return started["data"]


def project_id_of(session_id: str) -> str | None:
    """Project id of a session row, or ``None`` when the row does not exist."""
    return Session.objects.filter(id=session_id).values_list("project_id", flat=True).first()


def record_session_created(session: Session, *, origin: str) -> TimelineEvent | None:
    """Write ``session.created`` and the initial flag intervals of a new row.

    A session created already hidden or muted gets the matching ``*.started``
    rows, same ``occurred_at``, ``caused_by`` the creation event (design §5).
    ``pinned`` is never set at creation. Sync: call from worker-thread code.

    ``settings`` and ``spawned_by`` are read from the row just created (the
    stored, clamped values, not the request). ``settings`` is kept only for
    ``origin == "twicc"``; ``spawned_by`` is ``Session.spawned_by_id``.
    """
    from twicc.providers.helpers import AgentSettings

    now = timezone.now()
    common = dict(session_id=session.id, project_id=session.project_id, occurred_at=now)
    settings = None
    if origin == "twicc":
        settings = {
            name: value for name, value in AgentSettings.from_session(session)._asdict().items()
            if value is not None
        }
    created = record_event_sync(
        TimelineKind.SESSION_CREATED, actor=TimelineActor.SYSTEM,
        data={"origin": origin, "provider": session.provider, "settings": settings,
              "spawned_by": session.spawned_by_id}, **common,
    )
    if created is None:
        return None
    if session.hidden:
        record_event_sync(TimelineKind.HIDDEN_STARTED, actor=TimelineActor.USER, caused_by=created, **common)
    if session.mute_on_user_turn:
        record_event_sync(TimelineKind.MUTED_STARTED, actor=TimelineActor.USER, caused_by=created, **common)
    return created


def record_title_change(session_id: str, title: str, *, source: str, old: str | None = None) -> None:
    """Write ``title.changed``. ``source`` is ``"auto"``, ``"user"`` or ``"agent"``.

    ``old`` is the previous title when the caller knows it (``None`` otherwise).

    Subagent sessions get no event (decision D2): the compute fills titles for
    every session it processes, subagents included.
    """
    row = Session.objects.filter(id=session_id).values_list("project_id", "type").first()
    if row is None or row[1] == SessionType.SUBAGENT:
        return
    record_event_sync(
        TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0],
        actor=TimelineActor.USER if source == "user" else TimelineActor.AGENT,
        data={"source": source, "new": title, "old": old},
    )
