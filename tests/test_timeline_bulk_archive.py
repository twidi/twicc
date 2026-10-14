import asyncio

import orjson
import pytest
from django.test import AsyncClient

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
    assert all(r.actor == "user" and r.reason == "" for r in got.values())
    assert kinds("p") == []
