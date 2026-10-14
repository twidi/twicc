import asyncio
import re

import orjson
import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import archive_sessions_bulk
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-bulk", directory="/tmp/tl-bulk")


def test_bulk_archive_flips_all_and_records_one_event_per_flipped_session(project):
    for sid in ("a", "b"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    flipped = archive_sessions_bulk({"a", "b"})
    assert sorted(flipped) == ["a", "b"]
    assert Session.objects.filter(archived=True).count() == 2
    assert kinds("a") == kinds("b") == ["archived.started"]


def test_a_session_archived_meanwhile_records_nothing(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex")
    Session.objects.create(id="b", file_path="b.jsonl", project=project, provider="codex", archived=True)
    assert archive_sessions_bulk({"a", "b"}) == ["a"]
    assert kinds("a") == ["archived.started"] and kinds("b") == []


def test_search_version_is_reset_for_every_requested_session(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", search_version=7)
    archive_sessions_bulk({"a"})
    assert Session.objects.get(id="a").search_version == 0


def test_search_version_is_reset_for_an_already_archived_session_with_a_null_version(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex",
                           archived=True, search_version=None)
    Session.objects.create(id="b", file_path="b.jsonl", project=project, provider="codex",
                           archived=True, search_version=5)
    assert archive_sessions_bulk({"a", "b"}) == []
    assert Session.objects.get(id="a").search_version == 0
    assert Session.objects.get(id="b").search_version == 0


def test_empty_set_is_a_noop():
    assert archive_sessions_bulk(set()) == []


def test_the_bulk_archive_endpoint_records_one_user_event_per_archived_session(project, monkeypatch):
    from twicc import views

    monkeypatch.setattr("twicc.title_auto_task.request_title_check", lambda *a, **k: None)
    other = Project.objects.create(id="-tl-bulk-other", directory="/tmp/tl-bulk-other")
    for sid, owner in (("a", project), ("b", project), ("c", other)):
        Session.objects.create(
            id=sid, file_path=f"{sid}.jsonl", project=owner, provider="codex",
            user_message_count=1, created_at="2026-01-01T00:00:00Z", mtime=1.0,
        )
    Session.objects.create(  # pinned: not eligible
        id="p", file_path="p.jsonl", project=project, provider="codex",
        user_message_count=1, created_at="2026-01-01T00:00:00Z", mtime=1.0, pinned="project",
    )

    async def call():
        body = {"older_than": "2026-06-01T00:00:00+00:00", "scope": "all"}
        response = await AsyncClient().post("/api/sessions/bulk-archive/", orjson.dumps(body),
                                            content_type="application/json")
        for task in list(views._DETACHED_TASKS):  # the detached post-archive work
            await asyncio.wait_for(task, 10)
        return response

    response = run_with_writer(call)
    assert response.status_code == 200 and orjson.loads(response.content) == {"count": 3}
    assert Session.objects.filter(archived=True).count() == 3
    got = {r.session_id: r for r in TimelineEvent.objects.filter(kind="archived.started")}
    assert sorted(got) == ["a", "b", "c"]
    assert {sid: r.project_id for sid, r in got.items()} == {"a": project.id, "b": project.id, "c": other.id}
    assert all(
        (r.actor, r.via, r.actor_ref, r.actor_name) == ("user", "ui", None, None) and r.reason == ""
        for r in got.values()
    )
    assert kinds("p") == []


# --- Race analysis: ``ids`` is computed before the lock, the write decides what flipped ----------


def test_events_match_the_rows_that_really_flipped_when_the_state_changes_after_the_read(project):
    for sid in ("a", "b", "c", "d"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    ids = {"a", "b", "c", "d", "gone"}  # what the read phase selected
    # Between the read and the write: b is archived elsewhere, d is deleted, "gone" never existed.
    Session.objects.filter(id="b").update(archived=True)
    Session.objects.filter(id="d").delete()
    assert sorted(archive_sessions_bulk(ids)) == ["a", "c"]
    assert Session.objects.filter(archived=True).count() == 3  # a, b, c
    assert kinds("a") == kinds("c") == ["archived.started"]
    assert kinds("b") == kinds("d") == kinds("gone") == []


def test_a_session_unarchived_after_the_read_is_flipped_and_recorded(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", archived=True)
    Session.objects.filter(id="a").update(archived=False)  # was archived at read time, not anymore
    assert archive_sessions_bulk({"a"}) == ["a"]
    assert Session.objects.get(id="a").archived is True
    assert kinds("a") == ["archived.started"]


def test_an_already_archived_session_keeps_its_state_gets_no_event_and_a_reset_search_version(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex",
                           archived=True, search_version=9)
    assert archive_sessions_bulk({"a"}) == []
    row = Session.objects.get(id="a")
    assert row.archived is True and row.search_version == 0
    assert kinds("a") == []


def test_only_the_requested_ids_are_touched(project):
    for sid in ("a", "pinned", "other"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex",
                               pinned="project" if sid == "pinned" else None)
    assert archive_sessions_bulk({"a"}) == ["a"]  # pinned sessions are excluded by the caller's read
    assert Session.objects.get(id="pinned").archived is False
    assert Session.objects.get(id="other").archived is False
    assert kinds("pinned") == kinds("other") == []


def _write_statements(captured):
    # Django logs an ``executemany`` as "<n> times: <sql>".
    sqls = [re.sub(r"^\d+ times: ", "", q["sql"]) for q in captured]
    return [sql for sql in sqls if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


@pytest.mark.parametrize("count", [3, 300])
def test_the_number_of_write_statements_does_not_grow_with_the_number_of_sessions(project, count):
    Session.objects.bulk_create([
        Session(id=f"s{i:04d}", file_path=f"s{i}.jsonl", project=project, provider="codex") for i in range(count)
    ])
    with CaptureQueriesContext(connection) as captured:
        flipped = archive_sessions_bulk({f"s{i:04d}" for i in range(count)})
    assert len(flipped) == count
    assert TimelineEvent.objects.filter(kind="archived.started").count() == count
    writes = _write_statements(captured.captured_queries)
    assert len(writes) == 3, writes  # flip UPDATE, search_version UPDATE, one batched INSERT
