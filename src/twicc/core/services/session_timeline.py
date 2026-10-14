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

from typing import NamedTuple

from django.db import IntegrityError, connection, transaction
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


class EventSpec(NamedTuple):
    """One event of a batch given to :func:`record_events_bulk_sync` (same fields as ``record_event_sync``)."""

    kind: str
    session_id: str | None = None
    project_id: str | None = None
    occurred_at: object = None
    precision: str = TimelinePrecision.EXACT
    actor: str = TimelineActor.SYSTEM
    reason: str = ""
    caused_by_id: int | None = None
    data: dict | None = None
    dedupe_key: str | None = None


def insert_event_rows(rows: list[TimelineEvent]) -> None:
    """Insert unsaved ``rows`` with ONE ``executemany``. Errors propagate (no swallowing, no savepoint).

    A raw statement built from the model metadata (the project convention, see
    ``apply_item_updates``): each value goes through the field's ``get_db_prep_save``, the bytes
    ``Model.save()`` would write. Primary keys are not assigned back to the objects.
    """
    if not rows:
        return
    fields = [f for f in TimelineEvent._meta.concrete_fields if not f.primary_key]
    quote = connection.ops.quote_name
    sql = (
        f"INSERT INTO {quote(TimelineEvent._meta.db_table)} ({', '.join(quote(f.column) for f in fields)}) "
        f"VALUES ({', '.join(['%s'] * len(fields))})"
    )
    params = [
        [
            f.get_db_prep_save(
                getattr(row, f.attname) if f.is_relation else f.pre_save(row, True), connection=connection,
            )
            for f in fields
        ]
        for row in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(sql, params)


def record_events_bulk_sync(specs: list[EventSpec]) -> int:
    """Write many events with one batched insert. Returns the number of rows written. Never raises.

    Same rules as :func:`record_event_sync` for each spec: payload validation (an invalid spec is
    logged and skipped, the others are kept), ``STARTED_OF`` inheritance, ``dedupe_key`` duplicates
    (against the table and inside the batch) skipped. The valid rows go in ONE ``executemany``
    inside a savepoint. If that batch fails, each row is retried alone through
    :func:`record_event_sync`, so one bad row costs only itself. Call it under the DB write lock.
    """
    if not specs:
        return 0
    try:
        now = timezone.now()
        built: list[tuple[EventSpec, TimelineEvent]] = []
        for spec in specs:
            try:
                data = spec.data
                if spec.kind in STARTED_OF:
                    data = inherit_started_fields(spec.kind, data, _open_started_data(spec.session_id, spec.kind))
                built.append((spec, TimelineEvent(
                    session_id=spec.session_id, project_id=spec.project_id, kind=spec.kind,
                    occurred_at=spec.occurred_at or now, precision=spec.precision, actor=spec.actor,
                    reason=spec.reason, caused_by_id=spec.caused_by_id, data=validate_data(spec.kind, data),
                    dedupe_key=spec.dedupe_key,
                )))
            except Exception:
                logger.exception("Timeline event %s could not be built for session %s", spec.kind, spec.session_id)
        built = _drop_duplicates(built)
        if not built:
            return 0
        rows = [row for _, row in built]
        try:
            with transaction.atomic():
                insert_event_rows(rows)
            return len(rows)
        except Exception:
            logger.exception("Timeline batch insert failed (%s rows); retrying row by row", len(rows))
            return sum(
                1 for spec, _ in built
                if record_event_sync(
                    spec.kind, session_id=spec.session_id, project_id=spec.project_id,
                    occurred_at=spec.occurred_at or now, precision=spec.precision, actor=spec.actor,
                    reason=spec.reason, caused_by=spec.caused_by_id, data=spec.data, dedupe_key=spec.dedupe_key,
                ) is not None
            )
    except Exception:
        logger.exception("Timeline batch of %s events could not be written", len(specs))
        return 0


# Each lookup query binds two IN lists of at most this many values (SQLite's variable limit is 32766).
_DEDUPE_LOOKUP_CHUNK = 2000


def _drop_duplicates(built: list[tuple[EventSpec, TimelineEvent]]) -> list[tuple[EventSpec, TimelineEvent]]:
    """Drop the rows whose ``(session_id, dedupe_key)`` already exists in the table or earlier in the batch.

    The table lookup is chunked so a very large keyed batch stays under SQLite's variable limit.
    """
    keyed = [row for _, row in built if row.dedupe_key is not None]
    if not keyed:
        return built
    existing: set[tuple[str, str]] = set()
    for start in range(0, len(keyed), _DEDUPE_LOOKUP_CHUNK):
        chunk = keyed[start:start + _DEDUPE_LOOKUP_CHUNK]
        existing.update(
            TimelineEvent.objects.filter(
                session_id__in={row.session_id for row in chunk}, dedupe_key__in={row.dedupe_key for row in chunk},
            ).values_list("session_id", "dedupe_key")
        )
    kept = []
    for spec, row in built:
        if row.dedupe_key is not None:
            key = (row.session_id, row.dedupe_key)
            if key in existing:
                continue
            existing.add(key)
        kept.append((spec, row))
    return kept


async def record_event(kind: str, **kwargs) -> TimelineEvent | None:
    """Async twin of :func:`record_event_sync`, under the DB write lock.

    The lock is reentrant for the task that already holds it, so this is safe
    to call from inside another ``run_under_db_write_lock`` callback.
    """
    try:
        return await run_under_db_write_lock(
            lambda: asyncio.to_thread(lambda: record_event_sync(kind, **kwargs))
        )
    except Exception:
        # E.g. the DB writer is stopping. Never let a timeline write break the user action.
        logger.exception("Timeline event %s could not be written for session %s", kind, kwargs.get("session_id"))
        return None


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

    try:
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
    except Exception:
        logger.exception("Timeline creation events could not be written for session %s", session.id)
        return None


def record_title_change(session_id: str, title: str, *, source: str, old: str | None = None) -> None:
    """Write ``title.changed``. ``source`` is ``"auto"``, ``"user"`` or ``"agent"``.

    ``old`` is the previous title when the caller knows it (``None`` otherwise).

    Subagent sessions get no event (decision D2): the compute fills titles for
    every session it processes, subagents included.
    """
    try:
        row = Session.objects.filter(id=session_id).values_list("project_id", "type").first()
        if row is None or row[1] == SessionType.SUBAGENT:
            return
        record_event_sync(
            TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0],
            actor=TimelineActor.USER if source == "user" else TimelineActor.AGENT,
            data={"source": source, "new": title, "old": old},
        )
    except Exception:
        logger.exception("Timeline title change could not be written for session %s", session_id)
