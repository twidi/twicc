# src/twicc/core/services/timeline_feed.py
"""Read side of the timeline: the feed behind ``GET /api/timeline/``.

Design: ``docs/plans/2026-10-15-timeline-display-design.md`` section 7. Everything here is sync and
read-only; the view runs it in a worker thread.

Two query shapes matter for speed (measured on SQLite, no ANALYZE as in production):

* ``before`` and the first page use a ROW-VALUE keyset ``(occurred_at, id) < (cursor)``. The expanded
  ``OR`` form turns a multi-project ``IN`` into a multi-index OR plus a sort of the whole scope.
* ``after_id`` reads a primary-key range. The scope columns carry a unary plus (``+project_id``) so the
  planner cannot pick a project or provider index, which would read every event of the scope for a
  handful of new rows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NamedTuple

from django.db import connection
from django.db.models import BooleanField
from django.db.models.expressions import RawSQL

from twicc.core.enums import Provider
from twicc.core.models import Session, TimelineActor, TimelineEvent, TimelineKind

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
AFTER_ID_CAP = 500
#: The largest id SQLite can bind (a signed 64-bit integer): a bigger one is a 400, not a 500.
MAX_EVENT_ID = 2**63 - 1
#: Session ids per ``IN`` of the side-table lookup (an ``after_id`` page can cite more than 1000 ids).
SESSION_LOOKUP_CHUNK = 500

_INTEGER = re.compile(r"[+-]?\d+")
_MAX_INTEGER_TEXT = 24


class FeedError(ValueError):
    """A bad request parameter. The message goes into the ``{"error": ...}`` body of the 400."""


class FeedRequest(NamedTuple):
    project_ids: tuple[str, ...]  # empty = the global scope
    provider: str | None
    limit: int
    before: tuple[datetime, int] | None  # (occurred_at in UTC, id)
    after_id: int | None


def format_occurred_at(value: datetime) -> str:
    """UTC, always six fractional digits, always ``Z``: ``2026-10-14T10:00:00.123456Z``.

    One fixed format: the string orders like the time, and the cursor round-trips without loss.
    """
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _integer(name: str, text: str) -> int:
    text = text.strip()
    # Longer than any id (2**63 - 1 has 19 digits): also keeps ``int()`` away from Python's limit on huge
    # digit strings, which raises a plain ValueError and would turn a 400 into a 500.
    if len(text) > _MAX_INTEGER_TEXT or not _INTEGER.fullmatch(text):
        raise FeedError(f"{name} must be an integer, got {text[:40]!r}.")
    return int(text)


def parse_cursor(text: str) -> tuple[datetime, int]:
    """Parse ``<occurred_at>,<id>`` (the timestamp is the exact string the server returned)."""
    timestamp, separator, raw_id = text.rpartition(",")
    if not separator:
        raise FeedError("before must be '<occurred_at>,<id>'.")
    try:
        occurred_at = datetime.fromisoformat(timestamp)
    except ValueError:
        raise FeedError(f"before has an unreadable timestamp: {timestamp!r}.") from None
    if occurred_at.tzinfo is None:
        raise FeedError("before must carry a UTC offset or Z.")
    try:
        occurred_at = occurred_at.astimezone(UTC)
    except OverflowError:
        raise FeedError(f"before has a timestamp out of range: {timestamp!r}.") from None
    event_id = _integer("before id", raw_id)
    if not 0 <= event_id <= MAX_EVENT_ID:
        raise FeedError("before id is out of range.")
    return occurred_at, event_id


def parse_request(params: Mapping[str, str]) -> FeedRequest:
    """Validate the query string. Raises :class:`FeedError` (HTTP 400)."""
    project_ids = tuple(dict.fromkeys(
        item.strip() for item in (params.get("project_ids") or "").split(",") if item.strip()
    ))
    provider = None
    if params.get("provider"):
        try:
            provider = Provider(params["provider"]).value
        except ValueError:
            raise FeedError(f"Unknown provider: {params['provider']!r}.") from None
    limit = DEFAULT_LIMIT
    if "limit" in params:
        limit = _integer("limit", params["limit"])
        if limit < 1:
            raise FeedError("limit must be at least 1.")
        limit = min(limit, MAX_LIMIT)
    if "before" in params and "after_id" in params:
        raise FeedError("before and after_id are mutually exclusive.")
    before = parse_cursor(params["before"]) if "before" in params else None
    after_id = None
    if "after_id" in params:
        after_id = _integer("after_id", params["after_id"])
        if not 0 <= after_id <= MAX_EVENT_ID:
            raise FeedError("after_id must be between 0 and 2**63 - 1.")
    return FeedRequest(project_ids, provider, limit, before, after_id)


def _keyset(before: tuple[datetime, int]) -> RawSQL:
    occurred_at, event_id = before
    return RawSQL(
        "(occurred_at, id) < (%s, %s)",
        [connection.ops.adapt_datetimefield_value(occurred_at), event_id],
        output_field=BooleanField(),
    )


def _page_querysets(request: FeedRequest) -> list:
    """The main query and, when the main one cannot return them, the instance-events query."""
    ordered = TimelineEvent.objects.order_by("-occurred_at", "-id")
    keyset = [_keyset(request.before)] if request.before else []
    main = ordered.filter(*keyset)
    if request.project_ids:
        main = main.filter(project_id__in=request.project_ids)
    if request.provider:
        main = main.filter(provider=request.provider)
    queries = [main]
    if request.project_ids or request.provider:
        # Instance events have no project and no provider: the filters above exclude them.
        queries.append(ordered.filter(*keyset, session_id__isnull=True))
    return queries


def _fetch_page(request: FeedRequest) -> tuple[list[TimelineEvent], bool]:
    """Newest-first events of one page and ``has_more`` (one extra row is read, as the sessions page does)."""
    wanted = request.limit + 1
    merged: dict[int, TimelineEvent] = {}
    for queryset in _page_querysets(request):
        for event in queryset[:wanted]:
            merged[event.id] = event
    rows = sorted(merged.values(), key=lambda event: (event.occurred_at, event.id), reverse=True)[:wanted]
    return rows[: request.limit], len(rows) > request.limit


def _after_scope(request: FeedRequest) -> RawSQL | None:
    """Scope of an ``after_id`` fetch, instance events included, with unary-plus columns (see the module doc)."""
    clauses: list[str] = []
    params: list[str] = []
    if request.project_ids:
        clauses.append(f"+project_id IN ({', '.join(['%s'] * len(request.project_ids))})")
        params.extend(request.project_ids)
    if request.provider:
        clauses.append("+provider = %s")
        params.append(request.provider)
    if not clauses:
        return None
    return RawSQL(f"(({' AND '.join(clauses)}) OR +session_id IS NULL)", params, output_field=BooleanField())


def _after_queryset(request: FeedRequest):
    """The ``after_id`` query: one extra row is read to know whether the cap was exceeded."""
    queryset = TimelineEvent.objects.filter(id__gt=request.after_id)
    scope = _after_scope(request)
    if scope is not None:
        queryset = queryset.filter(scope)
    return queryset.order_by("id")[: AFTER_ID_CAP + 1]


def _fetch_after(request: FeedRequest) -> tuple[list[TimelineEvent], bool]:
    """Events with an id above ``after_id``, id ascending, at most :data:`AFTER_ID_CAP`, and ``truncated``."""
    rows = list(_after_queryset(request))
    return rows[:AFTER_ID_CAP], len(rows) > AFTER_ID_CAP


def _cited_session_ids(events: list[TimelineEvent]) -> set[str]:
    cited: set[str] = set()
    for event in events:
        if event.session_id:
            cited.add(event.session_id)
        if event.actor == TimelineActor.AGENT and event.actor_ref:
            cited.add(event.actor_ref)
        if event.kind == TimelineKind.SESSION_CREATED and isinstance(event.data, dict):
            spawned_by = event.data.get("spawned_by")
            if isinstance(spawned_by, str) and spawned_by:
                cited.add(spawned_by)
    return cited


def build_sessions_table(session_ids: set[str]) -> dict[str, dict]:
    """The side table: one slim entry per session that has a row. An id with no row gets no entry."""
    from twicc.pending_titles import get_pending_title

    table: dict[str, dict] = {}
    ordered = sorted(session_ids)
    for start in range(0, len(ordered), SESSION_LOOKUP_CHUNK):
        rows = Session.objects.filter(id__in=ordered[start:start + SESSION_LOOKUP_CHUNK]).values(
            "id", "title", "project_id", "provider", "hidden", "archived",
        )
        for row in rows:
            table[row["id"]] = {
                "title": get_pending_title(row["id"]) or row["title"] or None,
                "project_id": row["project_id"],
                "provider": row["provider"],
                "hidden": bool(row["hidden"]),
                "archived": bool(row["archived"]),
            }
    return table


def serialize_event(event: TimelineEvent) -> dict:
    """The public shape of an event. ``recorded_at``, ``dedupe_key`` and ``caused_by`` are not sent."""
    return {
        "id": event.id,
        "kind": event.kind,
        "occurred_at": format_occurred_at(event.occurred_at),
        "precision": event.precision,
        "actor": event.actor,
        "via": event.via,
        "actor_ref": event.actor_ref,
        "actor_name": event.actor_name,
        "reason": event.reason,
        "data": event.data,
        "session_id": event.session_id,
        "project_id": event.project_id,
        "provider": event.provider,
    }


def latest_event_id() -> int | None:
    """The highest id of the whole table, whatever the scope."""
    return TimelineEvent.objects.order_by("-id").values_list("id", flat=True).first()


def fetch_feed(request: FeedRequest) -> dict:
    """The response body of ``GET /api/timeline/``.

    ``latest_id`` is read FIRST (display design 7.2): a row committed afterwards may appear in
    ``events`` with a higher id, which is harmless; a row can never be below ``latest_id`` and missing
    from a result that claims it.
    """
    latest_id = latest_event_id()
    if request.after_id is not None:
        events, truncated = _fetch_after(request)
        has_more = False
    else:
        events, has_more = _fetch_page(request)
        truncated = False
    return {
        "events": [serialize_event(event) for event in events],
        "has_more": has_more,
        "truncated": truncated,
        "latest_id": latest_id,
        "sessions": build_sessions_table(_cited_session_ids(events)),
    }
