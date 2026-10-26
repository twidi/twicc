"""Timeline filter definitions: families, views, problems and the author partition (filters design section 4)."""

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection

from twicc.core.models import TimelineEvent, TimelineKind
from twicc.core.services.timeline_filters import (
    AUTHORS,
    FAMILIES,
    PROBLEM_REASONS,
    VIEWS,
    TimelineFilter,
    author_of,
    author_q,
    authors_q,
    families_q,
    kind_in,
    match_q,
    problem_q,
    view_q,
)

T0 = datetime(2026, 10, 26, 10, 0, 0, tzinfo=UTC)

ACTORS = ("user", "agent", "system", "external", "ghost")
VIAS = (None, "ui", "cli", "rpc", "mcp_internal", "mcp_external")
PRECISIONS = ("exact", "derived", "estimated")
TITLE_DATA = ({"source": "auto"}, {"source": "agent"}, {"source": None}, {})

#: The reasons of ``PROCESS_ENDED_REASONS`` in ``frontend/src/utils/timeline/describeEvent.js``.
FRONTEND_REASONS = (
    "manual", "force", "archived", "shutdown", "apply-settings", "switch-hybrid", "timeout_user_turn", "cli-exit",
    "timeout_starting", "timeout_assistant_turn", "cron_restart_timeout", "lost", "auth_required",
    "startup-failed", "error",
)


def build(kind, *, actor="system", via=None, precision="exact", reason="", data=None):
    """An unsaved row; ``data`` defaults to ``{}``."""
    return TimelineEvent(
        kind=kind, session_id="s1", occurred_at=T0, provider="codex", actor=actor, via=via, precision=precision,
        reason=reason, data={} if data is None else data,
    )


def save(*rows):
    """Insert the rows with distinct times; returns them with their ids."""
    for offset, row in enumerate(rows):
        row.occurred_at = T0 + timedelta(seconds=offset)
    return TimelineEvent.objects.bulk_create(rows)


def matching_ids(q):
    return set(TimelineEvent.objects.filter(q).values_list("id", flat=True))


def as_mapping(row):
    return {"kind": row.kind, "precision": row.precision, "actor": row.actor, "via": row.via, "data": row.data}


def test_the_keys_and_their_order():
    assert VIEWS == ("all", "without_turns", "waits_problems")
    assert tuple(FAMILIES) == ("sessions", "processes", "turns", "waits", "settings", "organization", "twicc")
    assert AUTHORS == ("unknown", "automatic", "you", "agent_cli_mcp", "external_mcp", "session_agent")


def test_every_kind_belongs_to_exactly_one_family():
    kinds = [kind for family in FAMILIES.values() for kind in family]
    assert set(kinds) == set(TimelineKind.values)
    assert len(kinds) == len(TimelineKind)


@pytest.mark.django_db
def test_the_author_partition_is_total_and_disjoint():
    rows = [
        build(kind, actor=actor, via=via, precision=precision)
        for kind, actor, via, precision in itertools.product(TimelineKind.values, ACTORS, VIAS, PRECISIONS)
    ]
    rows += [
        build(TimelineKind.TITLE_CHANGED, actor=actor, via=via, data=data)
        for actor, via, data in itertools.product(ACTORS, VIAS, TITLE_DATA)
    ]
    rows = save(*rows)
    sets = {author: matching_ids(author_q(author)) for author in AUTHORS}

    for first, second in itertools.combinations(AUTHORS, 2):
        assert sets[first].isdisjoint(sets[second]), (first, second)
    assert set().union(*sets.values()) == {row.id for row in rows}
    author_by_id = {row_id: author for author, row_ids in sets.items() for row_id in row_ids}
    mismatches = [
        (as_mapping(row), author_by_id[row.id]) for row in rows if author_by_id[row.id] != author_of(as_mapping(row))
    ]
    assert mismatches == []
    # The matrix reaches every author (a rule that matches nothing would pass the checks above).
    assert all(sets.values())


AUTHOR_EXAMPLES = [
    ("derived creation by the system", {"kind": "session.created", "precision": "derived"}, "unknown"),
    ("estimated lost process end", {"kind": "process.ended", "precision": "estimated", "reason": "lost"}, "unknown"),
    ("exact creation by the system", {"kind": "session.created"}, "unknown"),
    ("user through the API", {"kind": "archived.started", "actor": "user", "via": "rpc"}, "you"),
    ("user through the UI", {"kind": "archived.started", "actor": "user", "via": "ui"}, "you"),
    ("agent through the CLI", {"kind": "archived.started", "actor": "agent", "via": "cli"}, "agent_cli_mcp"),
    ("the session's agent turn", {"kind": "turn.started", "actor": "agent"}, "session_agent"),
    ("generated title", {"kind": "title.changed", "actor": "agent", "data": {"source": "auto"}}, "automatic"),
    ("provider title", {"kind": "title.changed", "actor": "agent", "data": {"source": "agent"}}, "session_agent"),
    ("shutdown process end", {"kind": "process.ended", "reason": "shutdown"}, "automatic"),
    ("external MCP", {"kind": "archived.started", "actor": "external", "via": "mcp_external"}, "external_mcp"),
    ("external through the CLI", {"kind": "archived.started", "actor": "external", "via": "cli"}, "unknown"),
    ("user through the external MCP", {"kind": "archived.started", "actor": "user", "via": "mcp_external"}, "unknown"),
    ("unknown actor", {"kind": "archived.started", "actor": "ghost"}, "unknown"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("fields", "author"), [(fields, author) for _, fields, author in AUTHOR_EXAMPLES],
                         ids=[label for label, _, _ in AUTHOR_EXAMPLES])
def test_author_examples_from_the_spec(fields, author):
    (saved,) = save(build(**fields))
    assert author_of(as_mapping(saved)) == author
    assert matching_ids(author_q(author)) == {saved.id}


def test_the_problem_reasons_are_the_warning_and_danger_reasons():
    assert PROBLEM_REASONS == frozenset({
        "timeout_starting", "timeout_assistant_turn", "cron_restart_timeout", "lost", "auth_required",
        "startup-failed", "error",
    })
    assert PROBLEM_REASONS < set(FRONTEND_REASONS)


@pytest.mark.django_db
def test_problems_are_the_warning_and_danger_ends_and_the_unclean_stop():
    process_ends = save(*[build("process.ended", reason=reason) for reason in FRONTEND_REASONS])
    unclean, clean, unsaid, wait = save(
        build("backend.ended", data={"clean": False}),
        build("backend.ended", data={"clean": True}),
        build("backend.ended"),
        build("awaiting_input.started", actor="agent"),
    )
    expected = {row.id for row in process_ends if row.reason in PROBLEM_REASONS} | {unclean.id}
    assert len(expected) == len(PROBLEM_REASONS) + 1
    assert matching_ids(problem_q()) == expected


@pytest.mark.django_db
def test_views():
    rows = save(*[build(kind, actor="agent") for kind in TimelineKind.values])
    by_kind = {row.kind: row.id for row in rows}
    problem, unclean = save(build("process.ended", reason="error"), build("backend.ended", data={"clean": False}))
    every_id = set(by_kind.values()) | {problem.id, unclean.id}

    assert view_q("all") is None
    assert every_id - matching_ids(view_q("without_turns")) == {by_kind["turn.started"], by_kind["turn.ended"]}
    assert matching_ids(view_q("waits_problems")) == {by_kind["awaiting_input.started"], problem.id, unclean.id}
    with pytest.raises(ValueError):
        view_q("pending")


@pytest.mark.django_db
def test_families_select_their_kinds():
    rows = save(*[build(kind, actor="agent") for kind in TimelineKind.values])
    by_kind = {row.kind: row.id for row in rows}

    assert families_q(()) is None
    assert families_q(tuple(FAMILIES)) is None
    assert matching_ids(families_q(("organization",))) == {by_kind[kind] for kind in FAMILIES["organization"]}
    assert matching_ids(families_q(("turns", "twicc", "turns"))) == {
        by_kind[kind] for kind in FAMILIES["turns"] + FAMILIES["twicc"]
    }
    with pytest.raises(ValueError):
        families_q(("turns", "chats"))


@pytest.mark.django_db
def test_authors_or_their_values():
    you, agent, system = save(
        build("archived.started", actor="user", via="ui"),
        build("turn.started", actor="agent"),
        build("process.ended", reason="shutdown"),
    )

    assert authors_q(()) is None
    assert authors_q(AUTHORS) is None
    assert matching_ids(authors_q(("you", "automatic"))) == {you.id, system.id}
    assert agent.id not in matching_ids(authors_q(("you", "automatic")))
    with pytest.raises(ValueError):
        author_q("nobody")
    with pytest.raises(ValueError):
        authors_q(("you", "nobody"))
    with pytest.raises(ValueError):
        authors_q(("nobody",))
    with pytest.raises(ValueError):
        match_q(TimelineFilter(authors=("nobody",)))


@pytest.mark.django_db
def test_match_q_is_none_without_filters_and_ands_the_groups():
    assert match_q(TimelineFilter()) is None
    assert match_q(TimelineFilter(providers=("codex",), session_id="s1")) is None

    shutdown, user_stop, system_turn_end = save(
        build("process.ended", reason="shutdown"),
        build("process.ended", actor="user", via="ui", reason="manual"),
        build("turn.ended"),
    )
    flt = TimelineFilter(families=("processes",), authors=("automatic",))
    assert matching_ids(match_q(flt)) == {shutdown.id}
    assert user_stop.id not in matching_ids(match_q(flt)) and system_turn_end.id not in matching_ids(match_q(flt))

    view_only = TimelineFilter(view="without_turns", authors=("automatic",))
    assert matching_ids(match_q(view_only)) == {shutdown.id}


def test_kind_in_needs_a_kind():
    with pytest.raises(ValueError):
        kind_in()


def test_kind_in_qualifies_the_column_and_keeps_the_unary_plus():
    sql = str(TimelineEvent.objects.filter(kind_in("turn.started")).query)
    assert '+"core_timelineevent"."kind" IN (' in sql


@pytest.mark.django_db
def test_kind_in_inside_a_same_model_subquery_reads_the_inner_row():
    started, ended, other_session_end = save(
        build("turn.started", actor="agent"),
        build("turn.ended", actor="agent"),
        build("turn.ended", actor="agent"),
    )
    TimelineEvent.objects.filter(id=other_session_end.id).update(session_id="s2")
    sessions_with_a_start = TimelineEvent.objects.filter(kind_in("turn.started")).values("session_id")
    queryset = TimelineEvent.objects.filter(session_id__in=sessions_with_a_start)

    assert '+U0."kind" IN (' in str(queryset.query).replace('"U0"', "U0")
    # Read against the outer row, the subquery would keep only the start and drop the end of the same session.
    assert set(queryset.values_list("id", flat=True)) == {started.id, ended.id}


def plan_of(queryset):
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        return " | ".join(str(row[-1]) for row in cursor.fetchall())


@pytest.mark.django_db
def test_a_families_filtered_page_query_walks_the_time_index_in_order():
    save(*[build(kind, actor="agent") for kind in TimelineKind.values for _ in range(5)])
    page = TimelineEvent.objects.order_by("-occurred_at", "-id")

    plan = plan_of(page.filter(families_q(("turns", "waits")))[:51])
    assert "idx_timeline_occurred" in plan
    assert "idx_timeline_kind" not in plan and "USE TEMP B-TREE" not in plan
    # Control: without the unary plus the planner takes the kind index and sorts (what ``kind_in`` avoids).
    control = plan_of(page.filter(kind__in=FAMILIES["turns"] + FAMILIES["waits"])[:51])
    assert "idx_timeline_kind" in control and "USE TEMP B-TREE" in control
