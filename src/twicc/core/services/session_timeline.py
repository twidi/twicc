"""Recording of timeline events (design §6).

Every hook calls :func:`record_event_sync` (from code already running in a
worker thread, inside or outside a transaction) or :func:`record_event` (from
async code). Neither ever raises: a failed timeline write is logged and must
never block or undo the user action it describes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import NamedTuple

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services import timeline_signal
from twicc.core.timeline import STARTED_OF, inherit_started_fields, validate_data
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.request_origin import (
    ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, VIA_VALUES, RequestOrigin,
    resolve_event_origin,
)

logger = logging.getLogger(__name__)

#: Width of ``TimelineEvent.provider``; a test pins it to the model.
PROVIDER_MAX_LENGTH = 50


def _truncate(value, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def origin_columns(resolved: RequestOrigin) -> dict:
    """The four origin columns of a row (design section 3).

    An invalid ``via`` becomes NULL and the other three values are kept (O35); ``actor_ref`` and
    ``actor_name`` are cut to their column width, never rejected (O24).
    """
    via = resolved.via
    if via is not None and (not isinstance(via, str) or via not in VIA_VALUES):
        logger.warning("Timeline event: invalid via %r stored as NULL", via)
        via = None
    return {
        "actor": resolved.actor,
        "via": via,
        "actor_ref": _truncate(resolved.actor_ref, ACTOR_REF_MAX_LENGTH),
        "actor_name": _truncate(resolved.actor_name, ACTOR_NAME_MAX_LENGTH),
    }


#: Provider of a session id, filled on demand. A session's provider never changes and ids are never
#: reused, so an entry never goes stale. Bounded (least recently used out) so a long-lived backend
#: that sees many sessions keeps a flat memory.
_PROVIDER_CACHE_MAX = 4096
_provider_cache: OrderedDict[str, str] = OrderedDict()
_provider_cache_lock = threading.Lock()


def clear_provider_cache() -> None:
    """Forget every cached provider (tests)."""
    with _provider_cache_lock:
        _provider_cache.clear()


def _provider_value(raw) -> str | None:
    """A ``Provider`` member or a string as a non-empty string cut to the column width, else ``None``."""
    value = getattr(raw, "value", raw)
    if not isinstance(value, str) or not value:
        return None
    return value[:PROVIDER_MAX_LENGTH]


def _remember_provider(session_id: str, provider: str) -> None:
    with _provider_cache_lock:
        _provider_cache[session_id] = provider
        _provider_cache.move_to_end(session_id)
        while len(_provider_cache) > _PROVIDER_CACHE_MAX:
            _provider_cache.popitem(last=False)


def resolve_provider(session_id: str | None, *, explicit=None, data: dict | None = None) -> str | None:
    """The ``provider`` column of an event (display design, section 8). Never raises.

    Order: an explicit value from the caller (free sources are preferred: the agent's provider, the
    loaded session, ``ProcessRun.provider``); then ``data["provider"]`` when the payload carries one
    (``session.created``, ``process.started``, and ``process.ended`` through ``STARTED_OF``); then the
    bounded cache; then ONE primary-key query on ``Session``. An instance event (no ``session_id``) and
    a session with no row and no hint give ``None``.
    """
    if session_id is None:
        return None
    try:
        for hint in (explicit, (data or {}).get("provider")):
            value = _provider_value(hint)
            if value is not None:
                _remember_provider(session_id, value)
                return value
        with _provider_cache_lock:
            cached = _provider_cache.get(session_id)
            if cached is not None:
                _provider_cache.move_to_end(session_id)
                return cached
        value = _provider_value(Session.objects.filter(id=session_id).values_list("provider", flat=True).first())
        if value is not None:
            _remember_provider(session_id, value)
        return value
    except Exception:
        logger.exception("Timeline provider could not be resolved for session %s", session_id)
        return None


def record_event_sync(
    kind: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    occurred_at=None,
    precision: str = TimelinePrecision.EXACT,
    actor: str | None = None,
    origin: RequestOrigin | None = None,
    provider=None,
    reason: str = "",
    caused_by: TimelineEvent | int | None = None,
    data: dict | None = None,
    dedupe_key: str | None = None,
    final_message_resolution: dict | None = None,
) -> TimelineEvent | None:
    """Write one event. Returns the row, or ``None`` on failure or dedupe hit.

    Who caused the event (design section 5, *Precedence*): a captured full ``origin`` keeps its four
    fields; else an explicit ``actor`` (system and non-request writers) gives NULL origin fields; else
    the write is request-caused and takes the carrier, falling back to the default ``user`` / ``ui``.
    A system writer MUST pass ``actor=`` explicitly.

    The insert runs in its own savepoint: when the caller already holds a
    transaction, a failed insert rolls back only this row. For an ``ended`` kind
    listed in ``STARTED_OF``, the payload fields the caller did not provide are
    copied from the open ``started`` row of the same session (design §3);
    the origin fields are never copied.

    ``provider`` is an optional hint (a free source the caller already holds); the ``provider`` column is
    resolved by :func:`resolve_provider` whatever the caller passes. After a successful insert (not on a
    dedupe hit, not on a failure) the timeline signal is requested for when the transaction commits.

    ``final_message_resolution`` never inserts. It resolves only the exact existing normal ending's
    pending final metadata. Success or identical replay returns that ending; failure returns ``None``.
    Only a changed resolution requests the timeline signal. Call under existing writer admission.
    """
    try:
        if final_message_resolution is not None:
            return _resolve_final_message(kind, session_id=session_id, project_id=project_id,
                provider=provider, occurred_at=occurred_at, dedupe_key=dedupe_key,
                resolution=final_message_resolution)
        resolved = resolve_event_origin(actor, origin)
        if kind in STARTED_OF:
            data = inherit_started_fields(kind, data, _open_started_data(session_id, kind))
        payload = validate_data(kind, data)
        caused_by_id = caused_by.pk if isinstance(caused_by, TimelineEvent) else caused_by
        resolved_provider = resolve_provider(session_id, explicit=provider, data=payload)
        with transaction.atomic():
            if (
                dedupe_key is not None
                and TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key).exists()
            ):
                return None
            event = TimelineEvent.objects.create(
                session_id=session_id,
                project_id=project_id,
                kind=kind,
                occurred_at=occurred_at or timezone.now(),
                precision=precision,
                provider=resolved_provider,
                reason=reason,
                caused_by_id=caused_by_id,
                data=payload,
                dedupe_key=dedupe_key,
                **origin_columns(resolved),
            )
        timeline_signal.poke_on_commit()
        return event
    except IntegrityError:
        if dedupe_key is not None:
            logger.debug("Timeline event %s deduplicated by the database for %s", kind, session_id)
        else:
            logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None
    except Exception:
        logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None


def _resolve_final_message(kind, *, session_id, project_id, provider, occurred_at, dedupe_key, resolution):
    """Resolve only a new ending's final proof; callers own writer admission."""
    if kind != TimelineKind.TURN_ENDED or session_id is None or dedupe_key is None:
        return None
    state = resolution.get('state')
    keys = {'state', 'storage_incarnation', 'history_epoch'} | ({'event_id'} if state == 'ready' else set())
    if state not in ('ready', 'none') or set(resolution) != keys:
        return None
    if state == 'ready' and (type(resolution['event_id']) is not int or resolution['event_id'] <= 0):
        return None
    with transaction.atomic():
        event = TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key,
            kind=kind, project_id=project_id, provider=provider, occurred_at=occurred_at, actor='agent').first()
        if event is None:
            return None
        previous = event.data.get('final_message')
        if not isinstance(previous, dict):
            return None
        if previous.get('state') != 'pending':
            return event if previous == resolution else None
        scope = {name: resolution[name] for name in ('storage_incarnation', 'history_epoch')}
        if (any(previous.get(name) not in (None, value) for name, value in scope.items())
                or not Session.objects.filter(pk=session_id, provider=provider, project_id=project_id, **scope).exists()):
            return None
        if state == 'ready':
            message = TimelineEvent.objects.filter(pk=resolution['event_id'], session_id=session_id,
                project_id=project_id, provider=provider, kind=TimelineKind.ASSISTANT_MESSAGE_SENT, actor='agent').first()
            if message is None or any(message.data.get(name) != value for name, value in scope.items()):
                return None
        event.data = {**event.data, 'final_message': resolution}
        TimelineEvent.objects.filter(pk=event.pk).update(data=event.data)
        timeline_signal.poke_on_commit()
        return event


class EventSpec(NamedTuple):
    """One event of a batch given to :func:`record_events_bulk_sync` (same fields as ``record_event_sync``).

    ``actor`` defaults to ``system`` (NULL origin fields). A request-caused batch passes ``origin=``
    (resolved once by the caller, so every row carries the same origin). ``provider`` is the optional
    hint of :func:`resolve_provider` (the bulk archive passes the provider it already read).
    """

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
    origin: RequestOrigin | None = None
    provider: str | None = None


def _conflict_clause() -> str:
    """``ON CONFLICT ... DO NOTHING`` matching the partial unique constraint on ``(session_id, dedupe_key)``.

    The target columns come from the model constraint; the ``WHERE dedupe_key IS NOT NULL`` part is
    hard-coded. Both must match the partial unique index exactly, otherwise SQLite rejects the
    statement (a test compares the clause with the real index).
    Only a conflict on that index is skipped: a NOT NULL / CHECK / FK violation still raises, and
    rows with a NULL ``dedupe_key`` never conflict.
    """
    quote = connection.ops.quote_name
    constraint = next(c for c in TimelineEvent._meta.constraints if c.name == "uniq_timeline_dedupe")
    columns = [TimelineEvent._meta.get_field(name).column for name in constraint.fields]
    key_column = TimelineEvent._meta.get_field("dedupe_key").column
    return f"ON CONFLICT ({', '.join(quote(c) for c in columns)}) WHERE {quote(key_column)} IS NOT NULL DO NOTHING"


def insert_event_rows(rows: list[TimelineEvent]) -> int:
    """Insert unsaved ``rows`` with ONE ``executemany``. Returns the number of rows really inserted.

    A raw statement built from the model metadata (the project convention, see
    ``apply_item_updates``): each value goes through the field's ``get_db_prep_save``, the bytes
    ``Model.save()`` would write. Primary keys are not assigned back to the objects.

    A row whose ``(session_id, dedupe_key)`` already exists (in the table or earlier in the batch)
    is skipped by the database (``ON CONFLICT ... DO NOTHING``) and not counted: ``rowcount`` of an
    ``executemany`` is the sum of the modified rows. Any other error propagates (no swallowing,
    no savepoint).

    Limit: a keyed event needs a non-NULL ``session_id``. SQLite treats NULLs as distinct in a
    unique index, so rows ``(NULL, key)`` are never deduplicated here.
    """
    if not rows:
        return 0
    fields = [f for f in TimelineEvent._meta.concrete_fields if not f.primary_key]
    quote = connection.ops.quote_name
    sql = (
        f"INSERT INTO {quote(TimelineEvent._meta.db_table)} ({', '.join(quote(f.column) for f in fields)}) "
        f"VALUES ({', '.join(['%s'] * len(fields))}) {_conflict_clause()}"
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
        return cursor.rowcount


def record_events_bulk_sync(specs: list[EventSpec]) -> int:
    """Write many events with one batched insert. Returns the number of rows written. Never raises.

    Same rules as :func:`record_event_sync` for each spec: payload validation (an invalid spec is
    logged and skipped, the others are kept), ``STARTED_OF`` inheritance, ``dedupe_key`` duplicates
    (against the table and inside the batch) skipped by the database itself. The valid rows go in
    ONE ``executemany`` inside a savepoint. If that batch fails, each row is retried alone through
    :func:`record_event_sync`, so one bad row costs only itself. Call it under the DB write lock.
    A non-empty batch requests the timeline signal once, for when the transaction commits.
    """
    if not specs:
        return 0
    try:
        now = timezone.now()
        built: list[tuple[EventSpec, TimelineEvent, RequestOrigin]] = []
        for spec in specs:
            try:
                resolved = resolve_event_origin(spec.actor, spec.origin)
                data = spec.data
                if spec.kind in STARTED_OF:
                    data = inherit_started_fields(spec.kind, data, _open_started_data(spec.session_id, spec.kind))
                payload = validate_data(spec.kind, data)
                built.append((spec, TimelineEvent(
                    session_id=spec.session_id, project_id=spec.project_id, kind=spec.kind,
                    occurred_at=spec.occurred_at or now, precision=spec.precision,
                    provider=resolve_provider(spec.session_id, explicit=spec.provider, data=payload),
                    reason=spec.reason, caused_by_id=spec.caused_by_id, data=payload,
                    dedupe_key=spec.dedupe_key, **origin_columns(resolved),
                ), resolved))
            except Exception:
                logger.exception("Timeline event %s could not be built for session %s", spec.kind, spec.session_id)
        if not built:
            return 0
        rows = [row for _, row, _ in built]
        try:
            with transaction.atomic():
                written = insert_event_rows(rows)
            if written:
                timeline_signal.poke_on_commit()
            return written
        except Exception:
            logger.exception("Timeline batch insert failed (%s rows); retrying row by row", len(rows))
            return sum(
                1 for spec, row, resolved in built
                if record_event_sync(
                    spec.kind, session_id=spec.session_id, project_id=spec.project_id,
                    occurred_at=spec.occurred_at or now, precision=spec.precision, origin=resolved,
                    provider=row.provider, reason=spec.reason, caused_by=spec.caused_by_id, data=spec.data,
                    dedupe_key=spec.dedupe_key,
                ) is not None
            )
    except Exception:
        logger.exception("Timeline batch of %s events could not be written", len(specs))
        return 0


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


def record_session_created(
    session: Session, *, origin: str, creator: RequestOrigin | None = None,
) -> TimelineEvent | None:
    """Write ``session.created`` and the initial flag intervals of a new row.

    Called only for the rows created by the watcher's creation function (a session born while TwiCC
    runs). The sessions found by an initial sync get their ``session.created`` row (and no flag row)
    from ``timeline_backfill.py``, never from here: written by the background compute in each root's
    final transaction, and by the Codex "mark unavailable" apply
    (``rollout_migration._apply_mark_session_unavailable_job``) for the root of the marked session.

    A session created already hidden, muted or tree-detached gets the matching ``*.started``
    rows, same ``occurred_at``, ``caused_by`` the creation event (design §5).
    ``pinned`` is never set at creation. Sync: call from worker-thread code.

    ``settings`` and ``spawned_by`` are read from the row just created (the
    stored, clamped values, not the request). ``settings`` is kept only for
    ``origin == "twicc"``; ``spawned_by`` is ``Session.spawned_by_id``.

    Who caused the rows (design 2026-10-14, section 6.6): for ``origin == "twicc"`` the creating
    request, captured with the pending creation attributes (``creator``), else the default ``user`` /
    ``ui``; for any other ``origin`` the explicit ``system`` actor with NULL origin fields. These rows are
    written long after the request, so the carrier is never read.
    """
    from twicc.providers.helpers import AgentSettings

    try:
        now = timezone.now()
        common = {
            "session_id": session.id, "project_id": session.project_id, "occurred_at": now,
            "provider": session.provider,
        }
        who = {"origin": creator or DEFAULT_ORIGIN} if origin == "twicc" else {"actor": TimelineActor.SYSTEM}
        settings = None
        if origin == "twicc":
            settings = {
                name: value for name, value in AgentSettings.from_session(session)._asdict().items()
                if value is not None
            }
        created = record_event_sync(
            TimelineKind.SESSION_CREATED,
            data={"origin": origin, "provider": session.provider, "settings": settings,
                  "spawned_by": session.spawned_by_id}, **who, **common,
        )
        if created is None:
            return None
        if session.hidden:
            record_event_sync(TimelineKind.HIDDEN_STARTED, caused_by=created, **who, **common)
        if session.mute_on_user_turn:
            record_event_sync(TimelineKind.MUTED_STARTED, caused_by=created, **who, **common)
        if session.tree_detached:
            record_event_sync(TimelineKind.TREE_DETACHED_STARTED, caused_by=created, **who, **common)
        return created
    except Exception:
        logger.exception("Timeline creation events could not be written for session %s", session.id)
        return None


def record_title_change(
    session_id: str, title: str, *, source: str, old: str | None = None, old_origin: str | None = None,
    origin: RequestOrigin | None = None,
) -> None:
    """Write ``title.changed``. ``source`` is ``"auto"``, ``"user"`` or ``"agent"``.

    ``old`` is the previous title when the caller knows it (``None`` otherwise), ``old_origin`` its
    ``title_origin`` (``"auto"``, ``"user"`` or ``""``) when the caller read it before the write.

    A ``user`` title is request-caused: it takes ``origin`` when the caller captured one (a deferred
    flush), else the carrier, else the default. A title from another source comes from compute, the
    watcher or a Codex job, not from a request: actor ``agent``, NULL origin fields.

    Subagent sessions get no event (decision D2): the compute fills titles for
    every session it processes, subagents included.

    An ``auto`` / ``agent`` title of a session not yet known to the timeline (no ``session.created``
    row) is its initial title, not a change: no event (spec 2026-10-18, C7). A ``user`` title is
    always written.

    The first title event of a session is written as its first title (``old`` is ``None``) when the
    title it replaces was never recorded and is automatic: the placeholder made from the first prompt,
    or an initial title of C7. A rename from it would show a title the timeline never had. This holds
    for every ``auto`` / ``agent`` title, and for a ``user`` title that replaces an ``auto`` one (a
    title given at creation, flushed after the placeholder). A ``user`` title that replaces a title
    of another origin keeps it.
    """
    try:
        row = Session.objects.filter(id=session_id).values_list("project_id", "type", "provider").first()
        if row is None or row[1] == SessionType.SUBAGENT:
            return
        if source != "user" and not TimelineEvent.objects.filter(
            session_id=session_id, kind=TimelineKind.SESSION_CREATED,
        ).exists():
            return
        if old is not None and (source != "user" or old_origin == "auto") and not TimelineEvent.objects.filter(
            session_id=session_id, kind=TimelineKind.TITLE_CHANGED,
        ).exists():
            old = None
        who = {"origin": origin} if source == "user" else {"actor": TimelineActor.AGENT}
        record_event_sync(
            TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0], provider=row[2],
            data={"source": source, "new": title, "old": old}, **who,
        )
    except Exception:
        logger.exception("Timeline title change could not be written for session %s", session_id)
