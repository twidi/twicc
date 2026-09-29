"""Computed history facts retain exact, prior-line evidence."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.models import Project, Session, SessionHistoryFact, SessionItem
from twicc.providers.history_facts import (
    HistoryFact,
    HistoryFactKind,
    append_history_facts,
    history_facts_are_current,
    iter_history_facts,
    iter_history_items,
    replace_history_facts,
)


@pytest.fixture
def session(db):
    project = Project.objects.create(id="-tmp-history-facts", directory="/tmp/history-facts")
    return Session.objects.create(id="history-session", project=project, provider="codex", file_path="history.jsonl")


def test_newest_fact_is_strictly_before_current_line(session):
    append_history_facts(session.id, [
        HistoryFact(2, HistoryFactKind.TOOL_CALL, "call-1", {"name": "old"}),
        HistoryFact(6, HistoryFactKind.TOOL_CALL, "call-1", {"name": "current"}),
        HistoryFact(9, HistoryFactKind.TOOL_CALL, "call-1", {"name": "future"}),
    ])

    assert list(iter_history_facts(session.id, HistoryFactKind.TOOL_CALL, "call-1", before_line=6)) == [
        HistoryFact(2, HistoryFactKind.TOOL_CALL, "call-1", {"name": "old"}),
    ]
    assert [fact.line_num for fact in iter_history_facts(
        session.id, HistoryFactKind.TOOL_CALL, "call-1", before_line=10,
    )] == [9, 6, 2]


def test_reused_keys_retain_both_occurrences(session):
    append_history_facts(session.id, [
        HistoryFact(2, HistoryFactKind.PROCESS_START, "pid-1", {"call_id": "first"}),
        HistoryFact(7, HistoryFactKind.PROCESS_START, "pid-1", {"call_id": "second"}),
    ])

    assert [fact.data["call_id"] for fact in iter_history_facts(
        session.id, HistoryFactKind.PROCESS_START, "pid-1", before_line=8,
    )] == ["second", "first"]


def test_session_delete_cascades(session):
    append_history_facts(session.id, [HistoryFact(1, HistoryFactKind.TURN_CONTEXT, "context", {"line": 1})])

    session.delete()

    assert SessionHistoryFact.objects.count() == 0


def test_duplicate_source_fact_is_idempotent(session):
    fact = HistoryFact(3, HistoryFactKind.CODE_EXEC_TARGET, "patch", {"target": "file.py"})
    append_history_facts(session.id, [fact])
    append_history_facts(session.id, [fact])

    assert list(iter_history_facts(session.id, fact.kind, fact.key, before_line=4)) == [fact]


def test_replace_history_facts_replaces_only_one_session(session):
    other = Session.objects.create(id="other-history", project=session.project, provider="codex", file_path="other.jsonl")
    old = HistoryFact(1, HistoryFactKind.PLAN_MARKER, "context", {"mode": "old"})
    new = HistoryFact(5, HistoryFactKind.PLAN_MARKER, "context", {"mode": "new"})
    append_history_facts(session.id, [old])
    append_history_facts(other.id, [old])

    replace_history_facts(session.id, [new])

    assert list(iter_history_facts(session.id, new.kind, new.key, before_line=6)) == [new]
    assert list(iter_history_facts(other.id, old.kind, old.key, before_line=6)) == [old]


def test_iter_history_items_pages_backward_across_gaps_and_malformed_content(session):
    for line_num in [1, 2, 5, 9, 10, 20]:
        content = "{malformed" if line_num == 9 else f'{{"line":{line_num}}}'
        SessionItem.objects.create(session=session, line_num=line_num, content=content)

    with CaptureQueriesContext(connection) as queries:
        rows = list(iter_history_items(session.id, before_line=20, page_size=2))

    assert rows == [
        (10, '{"line":10}'), (9, "{malformed"), (5, '{"line":5}'),
        (2, '{"line":2}'), (1, '{"line":1}'),
    ]
    selects = [query["sql"].upper() for query in queries if query["sql"].lstrip().upper().startswith("SELECT")]
    assert len(selects) == 3
    assert all(" OFFSET " not in sql and " LIKE " not in sql for sql in selects)


def test_fact_reader_pages_exact_key_without_offset(session):
    facts = [HistoryFact(line, HistoryFactKind.TOOL_CALL, "same", {"line": line}) for line in range(1, 132)]
    append_history_facts(session.id, facts)
    append_history_facts(session.id, [HistoryFact(150, HistoryFactKind.TOOL_CALL, "other", {})])

    with CaptureQueriesContext(connection) as queries:
        found = list(iter_history_facts(session.id, HistoryFactKind.TOOL_CALL, "same", before_line=132))

    assert [fact.line_num for fact in found] == list(range(131, 0, -1))
    selects = [query["sql"].upper() for query in queries if query["sql"].lstrip().upper().startswith("SELECT")]
    assert len(selects) >= 2
    assert all(" OFFSET " not in sql and " LIKE " not in sql for sql in selects)


def test_exact_key_prior_line_lookup_uses_unique_index(session):
    query = (
        SessionHistoryFact.objects.filter(
            session=session, kind=HistoryFactKind.TOOL_CALL, key="call-1", line_num__lt=50,
        ).order_by("-line_num").values_list("line_num", "data")[:1]
    )
    sql, params = query.query.sql_with_params()

    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        plan = [row[3] for row in cursor.fetchall()]

    assert any(
        "USING INDEX sqlite_autoindex_core_sessionhistoryfact_1" in step
        and "session_id=? AND kind=? AND key=? AND line_num<?" in step
        for step in plan
    ), plan
    assert all("USE TEMP B-TREE" not in step for step in plan)


def test_history_facts_are_current_uses_provider_compute_version(session):
    from twicc.providers.helpers import get_provider_helpers

    current = get_provider_helpers(session.provider).current_compute_version
    assert current is not None
    assert not history_facts_are_current(session)

    session.compute_version = current
    assert history_facts_are_current(session)

    session.compute_version = current - 1
    assert not history_facts_are_current(session)


def test_history_fact_kind_has_exact_supported_family():
    assert set(HistoryFactKind.values) == {
        "tool_call", "process_start", "code_cell", "agent_spawn", "turn_start", "code_exec_target",
        "turn_context", "plan_marker", "goal_context", "goal_update", "token_usage",
    }
