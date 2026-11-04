"""Read side of the timeline: the feed behind ``GET /api/timeline/``.

Design: ``docs/plans/2026-10-15-timeline-display-design.md`` section 7. Everything here is sync and
read-only; the view runs it in a worker thread.

Two query shapes matter for speed (measured on SQLite, no ANALYZE as in production):

* ``before`` and the first page use a ROW-VALUE keyset ``(occurred_at, id) < (cursor)``. The expanded
  ``OR`` form turns a multi-project ``IN`` into a multi-index OR plus a sort of the whole scope.
* ``after_id`` reads a primary-key range. The scope columns carry a unary plus (``+project_id``) so the
  planner cannot pick a project or provider index, which would read every event of the scope for a
  handful of new rows.

Filters (design ``docs/plans/2026-10-26-timeline-filters-design.md`` sections 4.5, 6 and 7): the providers
and the session filter are scope columns, the view, families and authors are one predicate
(``timeline_filters.match_q``). A page query has ONE plain scope column, the most selective (the session,
else the projects, else the providers); the other scope columns carry a unary plus, because two plain ones
make SQLite pick the wrong index (225 ms against 0.1 ms for a session and a provider on a million rows).
Two exceptions, described at the end of this doc: the instance-family shape reads the instance rows only
(``session_id IS NULL``, no project or provider column); the external-author shape makes the plain ``actor``
its scope column.
``match_q`` is a plain predicate on top: every ``kind`` comparison in it carries a unary plus, so it never
drives an index and the query keeps walking its index in order, stopping at ``limit + 1`` matching rows.
A provider filter keeps every row without a provider: the instance rows, read by their own query as
before, and the session rows no source could name, read by a third query on the provider index
(``provider IS NULL``). The ``after_id`` fetch reads the scope range first (``truncated`` speaks of the
range), then applies ``match_q`` to the ids of that range.

Two filters select so few rows that walking the scope index in order would read the whole scope before
the page fills (measured by ``tests/test_timeline_filters_bench.py`` on a million rows):

* A family filter on instance families alone (``INSTANCE_FAMILIES``, ``twicc``): only instance rows carry
  those kinds, so the page is the instance query alone (session index, ``session_id IS NULL``).
* An author filter on external authors alone (``EXTERNAL_AUTHORS``): ``match_q`` keeps a plain
  ``actor = 'external'``, served by the partial index ``idx_timeline_external``. That comparison is then the
  plain scope column of the main query: the projects and the providers carry a unary plus.

Hidden sessions: every read leaves out the rows of a session hidden NOW (``Session.hidden`` at read time, not at
the time of the event). The page queries carry :func:`visible_sessions_q`, an uncorrelated subquery on the partial
index ``idx_session_hidden`` under a ``NOT`` and as the last ``WHERE`` term, so it never drives the index choice
and is only probed for the rows every other term kept. The ``after_id`` fetch drops those
rows from its range in Python and names the sessions hidden or shown again inside the range (``hidden_sessions``,
``shown_sessions``), so an open list drops or merges them in place. Instance rows always stay; a visible row that
only cites a hidden session (``actor_ref``, ``data.spawned_by``) stays too.

Muted sessions follow the same visibility mechanism unless ``include_muted=1``.
The existing visibility lists then also describe ``muted.started`` and ``muted.ended`` changes.
Session references include ``mute_on_user_turn`` for consumers such as automatic speech.

A filter that can never match (``timeline_filters.can_match``: the view and the checked families leave no kind) reads
no page: its queries would walk the whole scope for nothing. Its ``after_id`` fetch still reads the range, for
``truncated`` and the two session lists.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NamedTuple

from django.db import connection
from django.db.models import BooleanField, Q
from django.db.models.expressions import RawSQL
from django.db.models.lookups import In, IsNull

from twicc.core.enums import Provider
from twicc.core.models import Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services.timeline_filters import (
    AUTHORS,
    EXTERNAL_AUTHORS,
    FAMILIES,
    INSTANCE_FAMILIES,
    VIEWS,
    TimelineFilter,
    can_match,
    match_q,
    unindexed,
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
AFTER_ID_CAP = 500
#: The largest id SQLite can bind (a signed 64-bit integer): a bigger one is a 400, not a 500.
MAX_EVENT_ID = 2**63 - 1
#: Session ids per ``IN`` of the side-table lookup (an ``after_id`` page can cite more than 1000 ids).
SESSION_LOOKUP_CHUNK = 500

_INTEGER = re.compile(r"[+-]?\d+")
_MAX_INTEGER_TEXT = 24
_PROVIDERS = tuple(provider.value for provider in Provider)


class FeedError(ValueError):
    """A bad request parameter. The message goes into the ``{"error": ...}`` body of the 400."""


class FeedRequest(NamedTuple):
    project_ids: tuple[str, ...]  # empty = the global scope
    limit: int
    before: tuple[datetime, int] | None  # (occurred_at in UTC, id)
    after_id: int | None
    filter: TimelineFilter = TimelineFilter()
    include_muted: bool = False


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


def _items(params: Mapping[str, str], name: str) -> tuple[str, ...]:
    """A comma list: items stripped, empty ones dropped, duplicates collapsed, order kept."""
    return tuple(dict.fromkeys(item.strip() for item in (params.get(name) or "").split(",") if item.strip()))


def _keys(params: Mapping[str, str], name: str, allowed: tuple[str, ...]) -> tuple[str, ...]:
    """A comma list of keys of ``allowed``; every key of ``allowed`` is the same as none (``()``, design 4.5)."""
    keys = _items(params, name)
    if unknown := [key for key in keys if key not in allowed]:
        raise FeedError(f"Unknown {name}: {', '.join(repr(key[:40]) for key in unknown[:5])}.")
    if set(keys) >= set(allowed):
        return ()
    return keys


def _parse_filter(params: Mapping[str, str]) -> TimelineFilter:
    view = (params.get("view") or "").strip() or "all"
    if view not in VIEWS:
        raise FeedError(f"Unknown view: {view[:40]!r}.")
    return TimelineFilter(
        view=view,
        families=_keys(params, "families", tuple(FAMILIES)),
        authors=_keys(params, "authors", AUTHORS),
        providers=_keys(params, "provider", _PROVIDERS),
        session_id=(params.get("session") or "").strip() or None,
    )


def parse_request(params: Mapping[str, str]) -> FeedRequest:
    """Validate the query string. Raises :class:`FeedError` (HTTP 400)."""
    project_ids = _items(params, "project_ids")
    flt = _parse_filter(params)
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
    include_muted = params.get("include_muted", "0")
    if include_muted not in ("0", "1"):
        raise FeedError("include_muted must be 0 or 1.")
    return FeedRequest(project_ids, limit, before, after_id, flt, include_muted == "1")


def before_condition(before: tuple[datetime, int]) -> RawSQL:
    occurred_at, event_id = before
    return RawSQL(
        "(occurred_at, id) < (%s, %s)",
        [connection.ops.adapt_datetimefield_value(occurred_at), event_id],
        output_field=BooleanField(),
    )


def visible_sessions_q(*, include_muted: bool = False) -> Q:
    """Exclude hidden sessions and, by default, muted sessions by their current state.

    One uncorrelated subquery, read once through the partial index ``idx_session_hidden``. Django guards the
    nullable column (``NOT (session_id IN (...) AND session_id IS NOT NULL)``): an instance row (``session_id IS
    NULL``) survives, where a bare ``NOT IN`` would be NULL for it as soon as one session is hidden. The comparison
    sits under a ``NOT``, so it never drives an index: each page query keeps its own index choice.
    A second uncorrelated subquery excludes muted sessions unless explicitly included.
    """
    visible = ~Q(session_id__in=Session.objects.filter(hidden=True).values("id"))
    if include_muted:
        return visible
    # Keep hidden exclusion last so its indexed lookup follows the other page predicates.
    return ~Q(session_id__in=Session.objects.filter(mute_on_user_turn=True).values("id")) & visible


def _page_querysets(request: FeedRequest) -> list:
    """The queries of one page, merged by :func:`_fetch_page`, each without the rows of the hidden sessions.

    The exclusion is the LAST term of each ``WHERE``: SQLite tests the terms in their order, so the scope, the cursor
    and the filters reject a row before its subquery probe. As the first term, it added about half to the long scan
    of a filter that matches few rows; as the last one, nothing measurable.
    """
    return [queryset.filter(visible_sessions_q(include_muted=request.include_muted)) for queryset in _scoped_querysets(request)]


def _scoped_querysets(request: FeedRequest) -> list:
    """The queries of one page before the hidden-session exclusion (see the module doc).

    With a session filter: one query on the session index. With a family filter on instance families alone:
    the instance query alone. Otherwise the main query, the instance-events query when the scope or a
    provider filter excludes them from the main one, and the query of the session rows without a provider
    when a provider filter is set.
    """
    flt = request.filter
    ordered = TimelineEvent.objects.order_by("-occurred_at", "-id")
    common = [before_condition(request.before)] if request.before else []
    if (match := match_q(flt)) is not None:
        common.append(match)
    unindexed_projects = [Q(In(unindexed("project_id"), request.project_ids))] if request.project_ids else []
    if flt.session_id is not None:
        query = ordered.filter(*common, *unindexed_projects, session_id=flt.session_id)
        if flt.providers:
            provider = unindexed("provider")
            query = query.filter(Q(In(provider, flt.providers)) | Q(IsNull(provider, True)))
        return [query]
    if flt.families and set(flt.families) <= INSTANCE_FAMILIES:
        # Instance rows are in every scope and under every provider filter (design 4.5).
        return [ordered.filter(*common, session_id__isnull=True)]
    main = ordered.filter(*common)
    # The plain scope column: the external actor of ``match_q``, else the projects, else the providers.
    actor_driven = bool(flt.authors) and set(flt.authors) <= EXTERNAL_AUTHORS
    if request.project_ids:
        main = main.filter(*unindexed_projects) if actor_driven else main.filter(project_id__in=request.project_ids)
    if flt.providers:
        if request.project_ids or actor_driven:
            main = main.filter(In(unindexed("provider"), flt.providers))
        else:
            main = main.filter(provider__in=flt.providers)
    queries = [main]
    if request.project_ids or flt.providers:
        # Instance events have no project and no provider: the filters above exclude them.
        queries.append(ordered.filter(*common, session_id__isnull=True))
    if flt.providers:
        # Session rows no source could name a provider for: a provider filter keeps them (design 4.5).
        queries.append(ordered.filter(*common, *unindexed_projects, provider__isnull=True, session_id__isnull=False))
    return queries


def _fetch_page(request: FeedRequest) -> tuple[list[TimelineEvent], bool]:
    """Newest-first events of one page and ``has_more`` (one extra row is read, as the sessions page does).

    A filter that can never match (:func:`can_match`) reads nothing: its queries would walk the whole scope.
    """
    if not can_match(request.filter):
        return [], False
    wanted = request.limit + 1
    merged: dict[int, TimelineEvent] = {}
    for queryset in _page_querysets(request):
        for event in queryset[:wanted]:
            merged[event.id] = event
    rows = sorted(merged.values(), key=lambda event: (event.occurred_at, event.id), reverse=True)[:wanted]
    return rows[: request.limit], len(rows) > request.limit


def _placeholders(values: tuple[str, ...]) -> str:
    return ", ".join(["%s"] * len(values))


def _after_scope(request: FeedRequest) -> RawSQL | None:
    """Scope of an ``after_id`` fetch, with unary-plus columns (see the module doc).

    Instance events are included, unless a session filter is set (it hides them, design 4.5).
    """
    flt = request.filter
    clauses: list[str] = []
    params: list[str] = []
    if request.project_ids:
        clauses.append(f"+project_id IN ({_placeholders(request.project_ids)})")
        params.extend(request.project_ids)
    if flt.providers:
        clauses.append(f"(+provider IN ({_placeholders(flt.providers)}) OR +provider IS NULL)")
        params.extend(flt.providers)
    if flt.session_id is not None:
        clauses.append("+session_id = %s")
        params.append(flt.session_id)
        return RawSQL(f"({' AND '.join(clauses)})", params, output_field=BooleanField())
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


def excluded_session_ids(session_ids: set[str], *, include_muted: bool) -> set[str]:
    """Sessions excluded now: always hidden, and muted unless explicitly included."""
    hidden: set[str] = set()
    ordered = sorted(session_ids)
    for start in range(0, len(ordered), SESSION_LOOKUP_CHUNK):
        hidden.update(
            Session.objects.filter(
                Q(hidden=True) if include_muted else Q(hidden=True) | Q(mute_on_user_turn=True),
                id__in=ordered[start:start + SESSION_LOOKUP_CHUNK],
            )
            .order_by().values_list("id", flat=True)
        )
    return hidden


class AfterResult(NamedTuple):
    events: list[TimelineEvent]
    truncated: bool
    hidden_sessions: list[str]  # an excluded session with a visibility-start row in the range
    shown_sessions: list[str]  # an included session with a visibility-end row in the range


def _fetch_after(request: FeedRequest) -> AfterResult:
    """Events with an id above ``after_id``, id ascending, ``truncated`` and the sessions hidden or shown.

    The cap applies to the scope range, before the view, families and authors and before the hidden sessions:
    ``truncated`` says the range holds more than :data:`AFTER_ID_CAP` rows, even when fewer of them are returned,
    so the client reloads. The rows of a session hidden now are left out (as on every page), and the range names
    the sessions whose visibility changed inside it, by their state NOW: a session hidden then shown again in the
    same range is only shown. These two lists ignore the view, families and authors: a filtered list still holds
    the rows of such a session, which it must drop or merge in place.
    """
    match = match_q(request.filter)
    queryset = _after_queryset(request)
    # With a filter the range is only read for its ids (and what the visibility needs): the rows come after.
    rows = list(queryset if match is None else queryset.values_list("id", "session_id", "kind", named=True))
    truncated = len(rows) > AFTER_ID_CAP
    rows = rows[:AFTER_ID_CAP]
    hidden = excluded_session_ids({row.session_id for row in rows if row.session_id}, include_muted=request.include_muted)
    hide_kinds = {TimelineKind.HIDDEN_STARTED}
    show_kinds = {TimelineKind.HIDDEN_ENDED}
    if not request.include_muted:
        hide_kinds.add(TimelineKind.MUTED_STARTED)
        show_kinds.add(TimelineKind.MUTED_ENDED)
    # Reuse the feed's existing remove/backfill lists for mute visibility changes too.
    hidden_sessions = sorted({
        row.session_id for row in rows if row.kind in hide_kinds and row.session_id in hidden
    })
    shown_sessions = sorted({
        row.session_id for row in rows
        if row.kind in show_kinds and row.session_id and row.session_id not in hidden
    })
    rows = [row for row in rows if row.session_id not in hidden]
    if not can_match(request.filter):
        # The range is still read: ``truncated`` and the two session lists speak of it, whatever the filters.
        rows = []
    elif match is not None:
        rows = list(TimelineEvent.objects.filter(match, id__in=[row.id for row in rows]).order_by("id"))
    return AfterResult(rows, truncated, hidden_sessions, shown_sessions)


def cited_session_ids(events: list[TimelineEvent]) -> set[str]:
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
            "id", "title", "project_id", "provider", "hidden", "archived", "mute_on_user_turn",
        )
        for row in rows:
            table[row["id"]] = {
                "title": get_pending_title(row["id"]) or row["title"] or None,
                "project_id": row["project_id"],
                "provider": row["provider"],
                "hidden": bool(row["hidden"]),
                "archived": bool(row["archived"]),
                "mute_on_user_turn": bool(row["mute_on_user_turn"]),
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
    visibility = {}
    if request.after_id is not None:
        after = _fetch_after(request)
        events, has_more, truncated = after.events, False, after.truncated
        visibility = {"hidden_sessions": after.hidden_sessions, "shown_sessions": after.shown_sessions}
    else:
        events, has_more = _fetch_page(request)
        truncated = False
    return {
        "events": [serialize_event(event) for event in events],
        "has_more": has_more,
        "truncated": truncated,
        "latest_id": latest_id,
        "sessions": build_sessions_table(cited_session_ids(events)),
        **visibility,
    }
