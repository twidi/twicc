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
order and stops at ``limit + 1`` rows. Same technique as the ``after_id`` scope of the feed. The helper
behind it, :func:`unindexed`, also serves the feed's secondary scope columns (``+project_id``,
``+provider``) in its page queries.
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
VIEWS: tuple[str, ...] = ("all", "messages", "without_turns", "waits_problems")

#: Family key -> its kinds (section 4.2). Every ``TimelineKind`` is in exactly one family (a test pins it).
FAMILIES: dict[str, tuple[str, ...]] = {
    "sessions": (TimelineKind.SESSION_CREATED, TimelineKind.TITLE_CHANGED),
    "processes": (TimelineKind.PROCESS_STARTED, TimelineKind.PROCESS_ENDED),
    "turns": (TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED),
    "messages": (TimelineKind.USER_MESSAGE_SENT, TimelineKind.ASSISTANT_MESSAGE_SENT),
    "waits": (TimelineKind.AWAITING_INPUT_STARTED, TimelineKind.AWAITING_INPUT_ENDED),
    "settings": (TimelineKind.SETTINGS_CHANGED,),
    "organization": (
        TimelineKind.ARCHIVED_STARTED, TimelineKind.ARCHIVED_ENDED,
        TimelineKind.HIDDEN_STARTED, TimelineKind.HIDDEN_ENDED,
        TimelineKind.MUTED_STARTED, TimelineKind.MUTED_ENDED,
        TimelineKind.TREE_DETACHED_STARTED, TimelineKind.TREE_DETACHED_ENDED,
        TimelineKind.PINNED_STARTED, TimelineKind.PINNED_ENDED,
    ),
    "twicc": (TimelineKind.BACKEND_STARTED, TimelineKind.BACKEND_ENDED),
}

#: The families only instance rows carry. Invariant: ``timeline_boot`` writes every ``backend.*`` row (boot start,
#: clean end, unclean end) without a session, a project or a provider; ``tests/test_timeline_boot.py``
#: (``test_every_backend_row_is_an_instance_row``) pins it. The feed reads a filter on these families alone from the
#: instance rows only.
INSTANCE_FAMILIES: frozenset[str] = frozenset({"twicc"})

#: The "Done by" values of section 4.3, in rule order: the first rule that fits a row wins.
AUTHORS: tuple[str, ...] = ("unknown", "automatic", "you", "agent_cli_mcp", "external_mcp", "session_agent")

#: The authors whose rows all have ``actor = 'external'``. Their condition keeps a plain ``actor`` comparison,
#: which the partial index ``idx_timeline_external`` serves: the feed walks that index for a filter on them alone.
EXTERNAL_AUTHORS: frozenset[str] = frozenset({"external_mcp"})

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


def unindexed(column: str) -> Func:
    """``+column``: the column, kept out of the planner's index choice.

    SQLite (no ANALYZE, as in production) picks an index from any plain comparison on an indexed column,
    which can be the wrong one: a ``kind IN (...)`` drives ``idx_timeline_kind`` and sorts every matching
    row, a second scope column steals the index of the first. With a unary plus the comparison still
    filters, but no index serves it. Built from Django expressions, not raw SQL: the column compiles
    through the alias machinery, so it names the right table in a join or in a same-model subquery (``U0``).
    """
    return Func(F(column), template="+%(expressions)s", output_field=CharField())


def kind_in(*kinds: str) -> Q:
    """``+kind IN (...)``: a kind comparison that never drives an index (see the module doc)."""
    if not kinds:
        raise ValueError("kind_in needs at least one kind")
    return Q(In(unindexed("kind"), [str(kind) for kind in kinds]))


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


#: The kind of each problem condition (section 4.4): ``problem_q`` and ``view_kinds`` both read it.
_PROBLEM_KINDS: tuple[str, ...] = (TimelineKind.PROCESS_ENDED, TimelineKind.BACKEND_ENDED)
#: Messages include turn boundaries. Activity keeps the existing ``without_turns`` API key.
_MESSAGES_VIEW_KINDS: tuple[str, ...] = FAMILIES["messages"] + FAMILIES["turns"]
_WITHOUT_TURNS_EXCLUDED: tuple[str, ...] = _MESSAGES_VIEW_KINDS
_WAITS_VIEW_KINDS: tuple[str, ...] = FAMILIES["waits"]


def problem_q() -> Q:
    """The rows that say something went wrong (section 4.4)."""
    process_ended, backend_ended = _PROBLEM_KINDS
    return (
        (kind_in(process_ended) & Q(reason__in=PROBLEM_REASONS))
        | (kind_in(backend_ended) & Q(data__clean=False))
    )


def view_q(view: str) -> Q | None:
    """The base rows of a view (section 4.1); ``None`` for ``all``."""
    if view == "all":
        return None
    if view == "messages":
        return kind_in(*_MESSAGES_VIEW_KINDS)
    if view == "without_turns":
        return ~kind_in(*_WITHOUT_TURNS_EXCLUDED)
    if view == "waits_problems":
        return kind_in(*_WAITS_VIEW_KINDS) | problem_q()
    raise ValueError(f"unknown view: {view!r}")


def view_kinds(view: str) -> frozenset[str]:
    """The kinds a view can show (built from the same definitions as :func:`view_q`)."""
    every = frozenset(TimelineKind.values)
    if view == "all":
        return every
    if view == "messages":
        return frozenset(_MESSAGES_VIEW_KINDS)
    if view == "without_turns":
        return every - set(_WITHOUT_TURNS_EXCLUDED)
    if view == "waits_problems":
        return frozenset(_WAITS_VIEW_KINDS) | frozenset(_PROBLEM_KINDS)
    raise ValueError(f"unknown view: {view!r}")


def possible_kinds(flt: TimelineFilter) -> frozenset[str]:
    """The kinds a row can have and still match the view and the checked families (section 4.5).

    The authors, the providers and the session filter do not narrow it: only the view and the families speak of
    kinds. A test pins that a matching row of every one of these kinds exists and no row of another kind matches.
    """
    kinds = view_kinds(flt.view)
    if flt.families:
        kinds &= {kind for family in flt.families for kind in FAMILIES[family]}
    return kinds


def can_match(flt: TimelineFilter) -> bool:
    """False when the view and the checked families leave no kind possible (``without_turns`` with Turns alone,
    ``waits_problems`` with none of Waiting for you, Processes and TwiCC): no row can ever match."""
    return bool(possible_kinds(flt))


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
