"""Filters of the timeline feed: views, event families, problems and the "Done by" partition.

Design: ``docs/plans/2026-10-26-timeline-filters-design.md`` sections 4.1 (views), 4.2 (families),
4.3 (authors), 4.4 (problems) and 4.5 (combining). This module only builds Django ``Q`` objects; the
feed (``timeline_feed``) applies them. Providers and the session filter are not here: the feed puts them
in the scope part of each query, where they drive an index.

Every comparison on ``kind`` carries a unary plus (``+"core_timelineevent"."kind" IN (...)``, through
:func:`kind_in`; the column keeps the alias of its query, ``U0`` in a subquery). Without it, SQLite (no
ANALYZE, as in production) picks ``idx_timeline_kind`` for a ``kind IN (...)`` and sorts every matching row
before it can return the first page (``USE TEMP B-TREE FOR ORDER BY``). With the plus, the planner
cannot use that index: the page query keeps walking ``idx_timeline_occurred`` or the scope index in
order and stops at ``limit + 1`` rows. Same technique as the ``after_id`` scope of the feed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from django.db.models import CharField, F, Func, Q, TextField, Value
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django.db.models.lookups import Exact, In

from twicc.core.models import TimelineKind, TimelinePrecision
from twicc.request_origin import (
    ACTOR_AGENT,
    ACTOR_EXTERNAL,
    ACTOR_SYSTEM,
    ACTOR_USER,
    VIA_CLI,
    VIA_MCP_EXTERNAL,
    VIA_MCP_INTERNAL,
    VIA_RPC,
    VIA_UI,
)

#: The views of section 4.1, in display order.
VIEWS: tuple[str, ...] = ("all", "without_turns", "waits_problems")

#: Family key -> its kinds (section 4.2). Every ``TimelineKind`` is in exactly one family (a test pins it).
FAMILIES: dict[str, tuple[str, ...]] = {
    "sessions": (TimelineKind.SESSION_CREATED, TimelineKind.TITLE_CHANGED),
    "processes": (TimelineKind.PROCESS_STARTED, TimelineKind.PROCESS_ENDED),
    "turns": (TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED),
    "waits": (TimelineKind.AWAITING_INPUT_STARTED, TimelineKind.AWAITING_INPUT_ENDED),
    "settings": (TimelineKind.SETTINGS_CHANGED,),
    "organization": (
        TimelineKind.ARCHIVED_STARTED, TimelineKind.ARCHIVED_ENDED,
        TimelineKind.HIDDEN_STARTED, TimelineKind.HIDDEN_ENDED,
        TimelineKind.MUTED_STARTED, TimelineKind.MUTED_ENDED,
        TimelineKind.PINNED_STARTED, TimelineKind.PINNED_ENDED,
    ),
    "twicc": (TimelineKind.BACKEND_STARTED, TimelineKind.BACKEND_ENDED),
}

#: The "Done by" values of section 4.3, in rule order: the first rule that fits a row wins.
AUTHORS: tuple[str, ...] = ("unknown", "automatic", "you", "agent_cli_mcp", "external_mcp", "session_agent")

#: The ``process.ended`` reasons whose severity is warning or danger in the row wording
#: (``PROCESS_ENDED_REASONS`` in ``frontend/src/utils/timeline/describeEvent.js``), section 4.4.
PROBLEM_REASONS: frozenset[str] = frozenset({
    "timeout_starting", "timeout_assistant_turn", "cron_restart_timeout", "lost", "auth_required",
    "startup-failed", "error",
})

#: The ``(actor, via)`` pairs TwiCC writes (section 4.3). Any other pair is "Unknown".
KNOWN_ORIGIN_PAIRS: frozenset[tuple[str, str | None]] = frozenset({
    (ACTOR_USER, VIA_UI), (ACTOR_USER, VIA_CLI), (ACTOR_USER, VIA_RPC),
    (ACTOR_USER, VIA_MCP_INTERNAL), (ACTOR_USER, None),
    (ACTOR_AGENT, VIA_CLI), (ACTOR_AGENT, VIA_MCP_INTERNAL), (ACTOR_AGENT, None),
    (ACTOR_EXTERNAL, VIA_MCP_EXTERNAL),
    (ACTOR_SYSTEM, None),
})

_AFTERWARDS_PRECISIONS = (TimelinePrecision.DERIVED, TimelinePrecision.ESTIMATED)
_AGENT_TOOL_VIAS = (VIA_CLI, VIA_MCP_INTERNAL)
_TITLE_SOURCE_AUTO = "auto"


class TimelineFilter(NamedTuple):
    """The filters of one feed query. An empty tuple means no filter on that group."""

    view: str = "all"
    families: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    session_id: str | None = None


def kind_in(*kinds: str) -> Q:
    """``+kind IN (...)``: a kind comparison that never drives an index (see the module doc).

    Built from Django expressions, not raw SQL: the column compiles through the alias machinery, so it
    names the right table in a join or in a same-model subquery (``U0``).
    """
    if not kinds:
        raise ValueError("kind_in needs at least one kind")
    unindexed_kind = Func(F("kind"), template="+%(expressions)s", output_field=CharField())
    return Q(In(unindexed_kind, [str(kind) for kind in kinds]))


def _generated_title_q() -> Q:
    """A title TwiCC generated. Never NULL: a title row without ``source`` compares ``''`` to ``'auto'``.

    ``data`` is not nullable, so Django adds no ``IS NOT NULL`` guard when this is negated; without the
    ``Coalesce``, ``NOT (source = 'auto')`` would be NULL for such a row and the row would match no author.
    """
    source = Coalesce(KT("data__source"), Value("", output_field=TextField()))
    return kind_in(TimelineKind.TITLE_CHANGED) & Q(Exact(source, _TITLE_SOURCE_AUTO))


def known_pair_q() -> Q:
    """The rows whose ``(actor, via)`` is one of :data:`KNOWN_ORIGIN_PAIRS`."""
    q = Q()
    for actor, via in sorted(KNOWN_ORIGIN_PAIRS, key=lambda pair: (pair[0], pair[1] or "")):
        q |= Q(actor=actor, via__isnull=True) if via is None else Q(actor=actor, via=via)
    return q


def _unknown_q() -> Q:
    return (
        Q(precision__in=_AFTERWARDS_PRECISIONS)
        | (kind_in(TimelineKind.SESSION_CREATED) & Q(actor=ACTOR_SYSTEM))
        | ~known_pair_q()
    )


def _automatic_base_q() -> Q:
    return _generated_title_q() | Q(actor=ACTOR_SYSTEM)


def author_q(author: str) -> Q:
    """The rows of one "Done by" value: its own condition, minus every earlier rule (section 4.3)."""
    if author not in AUTHORS:
        raise ValueError(f"unknown author: {author!r}")
    unknown = _unknown_q()
    if author == "unknown":
        return unknown
    automatic = _automatic_base_q()
    if author == "automatic":
        return ~unknown & automatic
    own = {
        "you": Q(actor=ACTOR_USER),
        "agent_cli_mcp": Q(actor=ACTOR_AGENT, via__in=_AGENT_TOOL_VIAS),
        "external_mcp": Q(actor=ACTOR_EXTERNAL, via=VIA_MCP_EXTERNAL),
        "session_agent": Q(actor=ACTOR_AGENT, via__isnull=True),
    }[author]
    return ~unknown & ~automatic & own


def authors_q(authors: tuple[str, ...]) -> Q | None:
    """OR of the checked authors; ``None`` when none or all are checked (every row has one author)."""
    checked = set(authors)
    if unknown := checked - set(AUTHORS):
        raise ValueError(f"unknown authors: {sorted(unknown)!r}")
    if not checked or checked >= set(AUTHORS):
        return None
    q = Q()
    for author in AUTHORS:
        if author in checked:
            q |= author_q(author)
    return q


def families_q(families: tuple[str, ...]) -> Q | None:
    """The kinds of the checked families; ``None`` when none or all are checked (every kind has one family)."""
    checked = set(families)
    if unknown := checked - set(FAMILIES):
        raise ValueError(f"unknown families: {sorted(unknown)!r}")
    if not checked or checked >= set(FAMILIES):
        return None
    return kind_in(*(kind for family, kinds in FAMILIES.items() if family in checked for kind in kinds))


def problem_q() -> Q:
    """The rows that say something went wrong (section 4.4)."""
    return (
        (kind_in(TimelineKind.PROCESS_ENDED) & Q(reason__in=PROBLEM_REASONS))
        | (kind_in(TimelineKind.BACKEND_ENDED) & Q(data__clean=False))
    )


def view_q(view: str) -> Q | None:
    """The base rows of a view (section 4.1); ``None`` for ``all``."""
    if view == "all":
        return None
    if view == "without_turns":
        return ~kind_in(TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED)
    if view == "waits_problems":
        return kind_in(TimelineKind.AWAITING_INPUT_STARTED) | problem_q()
    raise ValueError(f"unknown view: {view!r}")


def match_q(flt: TimelineFilter) -> Q | None:
    """AND of the view, the families and the authors; ``None`` when none of them filters (section 4.5)."""
    parts = [q for q in (view_q(flt.view), families_q(flt.families), authors_q(flt.authors)) if q is not None]
    if not parts:
        return None
    q = parts[0]
    for part in parts[1:]:
        q &= part
    return q


def author_of(row: Mapping) -> str:
    """The "Done by" value of a row given as a mapping (``kind``, ``precision``, ``actor``, ``via``, ``data``).

    Pure-Python mirror of :func:`author_q`, same rules in the same order; a test pins that both agree.
    """
    kind, actor, via = row["kind"], row["actor"], row.get("via")
    data = row.get("data")
    if (
        row["precision"] in _AFTERWARDS_PRECISIONS
        or (kind == TimelineKind.SESSION_CREATED and actor == ACTOR_SYSTEM)
        or (actor, via) not in KNOWN_ORIGIN_PAIRS
    ):
        return "unknown"
    generated_title = (
        kind == TimelineKind.TITLE_CHANGED and isinstance(data, Mapping) and data.get("source") == _TITLE_SOURCE_AUTO
    )
    if generated_title or actor == ACTOR_SYSTEM:
        return "automatic"
    if actor == ACTOR_USER:
        return "you"
    if actor == ACTOR_AGENT and via in _AGENT_TOOL_VIAS:
        return "agent_cli_mcp"
    if actor == ACTOR_EXTERNAL and via == VIA_MCP_EXTERNAL:
        return "external_mcp"
    if actor == ACTOR_AGENT and via is None:
        return "session_agent"
    raise AssertionError(f"the known pairs leave no author for {(actor, via)!r}")
