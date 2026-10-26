"""Event selection must happen before cursor pagination."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_timeline_api import T0, make_event, make_instance_event
from twicc.core.models import Project, Session
from twicc.core.services.timeline_feed import parse_cursor
from twicc.core.services.timeline_listing import fetch_timeline_page
from twicc.pending_titles import pop_pending_title, set_pending_title

pytestmark = pytest.mark.django_db


@pytest.fixture
def corpus():
    project = Project.objects.create(id="-timeline-listing", directory="/tmp/timeline-listing")
    for sid, fields in (
        ("visible", {}), ("hidden", {"hidden": True}),
        ("native", {"type": "subagent", "parent_session_id": "visible"}),
    ):
        Session.objects.create(id=sid, project=project, provider="codex", file_path=f"{sid}.jsonl", **fields)
    return project


def page(**kwargs):
    options = {
        "project_ids": None, "session_id": None, "selected_sessions": Session.objects.filter(hidden=False),
        "require_session_metadata": False, "include_instance": True, "limit": 20, "before": None,
    }
    options.update(kwargs)
    return fetch_timeline_page(**options)


def ids(body):
    return [row["id"] for row in body["items"]]


def test_empty_project_scope_never_means_global(corpus):
    make_instance_event()
    make_event(session_id="visible", project=corpus)
    assert page(project_ids=()) == {
        "items": [], "pagination": {"limit": 20, "has_more": False, "next_cursor": None}, "sessions": {},
    }


def test_keyset_ties_microseconds_and_insertions(corpus):
    rows = [make_event(session_id="visible", project=corpus, at=(i // 4) * 0.0001) for i in range(12)]
    first = page(limit=3)
    seen = ids(first)
    recent = make_event(session_id="visible", project=corpus, at=100)
    cursor = first["pagination"]["next_cursor"]
    for _ in range(5):
        body = page(limit=3, before=parse_cursor(cursor))
        seen.extend(ids(body))
        cursor = body["pagination"]["next_cursor"]
        if cursor is None:
            break
    assert seen == [r.id for r in reversed(rows)]
    assert recent.id not in seen
    assert len(set(seen)) == 12


def test_sparse_matches_fill_pages_before_lookahead(corpus):
    wanted = []
    for i in range(12):
        row = make_event(session_id="visible" if i % 3 == 0 else "hidden", project=corpus, at=i)
        if i % 3 == 0:
            wanted.append(row.id)
    first = page(limit=3)
    assert ids(first) == list(reversed(wanted))[:3]
    assert first["pagination"]["has_more"] is True
    second = page(limit=3, before=parse_cursor(first["pagination"]["next_cursor"]))
    assert ids(second) == wanted[:1]
    assert second["pagination"]["next_cursor"] is None


def test_instance_rows_merge_once_at_their_time_position(corpus):
    rows = [
        make_event(session_id="visible", project=corpus, at=0), make_instance_event(at=1),
        make_event(session_id="visible", project=corpus, at=2), make_instance_event(at=3),
    ]
    first = page(project_ids=(corpus.id,), limit=2)
    second = page(project_ids=(corpus.id,), limit=2, before=parse_cursor(first["pagination"]["next_cursor"]))
    assert ids(first) + ids(second) == [r.id for r in reversed(rows)]


def test_instance_rows_survive_without_eligible_session_history(corpus):
    make_event(session_id="hidden", project=corpus)
    instance = make_instance_event()
    assert ids(page(project_ids=(corpus.id,))) == [instance.id]


def test_missing_target_metadata_is_preserved_only_without_metadata_filters(corpus):
    orphan = make_event(session_id="missing", project=corpus)
    make_event(session_id="hidden", project=corpus)
    assert ids(page(project_ids=(corpus.id,))) == [orphan.id]
    assert ids(page(require_session_metadata=True, include_instance=False)) == []


def test_subagent_events_are_not_implicitly_dropped(corpus):
    row = make_event(session_id="native", project=corpus)
    assert ids(page()) == [row.id]


def test_target_visibility_does_not_filter_actor_references(corpus):
    row = make_event(session_id="visible", project=corpus, actor="agent", actor_ref="hidden")
    body = page()
    assert ids(body) == [row.id]
    assert body["sessions"]["hidden"]["hidden"] is True


def test_event_fields_and_side_table_match_existing_serialization(corpus):
    row = make_event(
        "session.created", session_id="visible", project=corpus, at=0.123456,
        data={"spawned_by": "hidden"},
    )
    set_pending_title("visible", "Pending timeline title")
    try:
        body = page()
        assert set(body["items"][0]) == {
            "id", "kind", "occurred_at", "precision", "actor", "via", "actor_ref", "actor_name", "reason",
            "data", "session_id", "session_title", "project_id", "project_name", "project_directory", "provider",
        }
        assert body["items"][0]["occurred_at"] == "2026-10-14T10:00:00.123456Z"
        assert body["items"][0]["id"] == row.id
        assert set(body["sessions"]) == {"visible", "hidden"}
        assert body["sessions"]["visible"]["title"] == "Pending timeline title"
    finally:
        pop_pending_title("visible")


def test_terminal_and_empty_pages_have_null_cursor(corpus):
    make_event(session_id="visible", project=corpus)
    assert page()["pagination"] == {"limit": 20, "has_more": False, "next_cursor": None}
    assert page(before=(T0 - timedelta(seconds=1), 0))["pagination"]["next_cursor"] is None


def test_cursor_survives_deleted_boundary_event(corpus):
    old = make_event(session_id="visible", project=corpus, at=0)
    newer = make_event(session_id="visible", project=corpus, at=1)
    cursor = page(limit=1)["pagination"]["next_cursor"]
    newer.delete()
    assert ids(page(before=parse_cursor(cursor))) == [old.id]


def test_listing_keeps_row_value_keyset(corpus, record_property):
    other = Project.objects.create(id="-timeline-other", directory="/tmp/timeline-other")
    make_event(session_id="visible", project=corpus)
    make_instance_event()
    with CaptureQueriesContext(connection) as captured:
        page(project_ids=(corpus.id, other.id), before=(T0 + timedelta(seconds=1), 100), limit=2)
    event_queries = [q["sql"] for q in captured if 'FROM "core_timelineevent"' in q["sql"]]
    assert len(event_queries) == 2
    for sql in event_queries:
        assert "(occurred_at, id) <" in sql
        assert "LIMIT 3" in sql
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN QUERY PLAN " + sql)
            record_property("listing_query_plan", str(cursor.fetchall()))
