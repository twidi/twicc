from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_backfill import backfill_batch_sync, run_timeline_backfill
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-backfill", directory="/tmp/tl-backfill")


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def run_all():
    run_with_writer(run_timeline_backfill)


def test_creation_row_is_derived_from_created_at(project):
    created = timezone.now() - timedelta(days=10)
    make(project, created_at=created)
    run_all()
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "session.created" and row.occurred_at == created
    assert row.precision == "derived" and row.data["origin"] == "sync"
    assert row.dedupe_key == "backfill:session.created" and row.project_id == project.id
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}


def test_creation_row_carries_the_spawner_when_the_column_is_set(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), spawned_by_id="boss", selected_model="opus")
    run_all()
    data = TimelineEvent.objects.get(session_id="kid", kind="session.created").data
    assert data["spawned_by"] == "boss" and data["settings"] is None


def test_flag_rows_use_last_updated_at_then_created_at_else_are_skipped(project):
    updated = timezone.now() - timedelta(days=1)
    make(project, "a", created_at=updated - timedelta(days=5), last_updated_at=updated, archived=True)
    make(project, "b", created_at=updated - timedelta(days=5), hidden=True)
    make(project, "c", mute_on_user_turn=True)
    run_all()
    assert TimelineEvent.objects.get(session_id="a", kind="archived.started").occurred_at == updated
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").precision == "estimated"
    assert kinds("c") == []


def test_pinned_row_carries_the_scope(project):
    make(project, created_at=timezone.now(), pinned="workspace")
    run_all()
    assert TimelineEvent.objects.get(session_id="s1", kind="pinned.started").data == {"scope": "workspace"}


def test_no_process_rows_are_ever_written(project):
    now = timezone.now()
    make(project, created_at=now, last_started_at=now, last_stopped_at=now)
    run_all()
    assert kinds("s1") == ["session.created"]


def test_subagents_are_skipped(project):
    parent = make(project, "p", created_at=timezone.now())
    make(project, "child", type=SessionType.SUBAGENT, parent_session=parent, created_at=timezone.now())
    run_all()
    assert kinds("child") == []


def test_running_twice_never_duplicates(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    run_all()
    first = TimelineEvent.objects.count()
    run_all()
    assert TimelineEvent.objects.count() == first == 2


def test_an_existing_live_row_of_the_subject_suppresses_the_backfill_row(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    record_event_sync("session.created", session_id="s1", actor="system", data={"origin": "twicc"})
    record_event_sync("archived.ended", session_id="s1", actor="user")
    run_all()
    assert kinds("s1") == ["session.created", "archived.ended"]


def test_batches_cover_every_session(project):
    for i in range(7):
        make(project, f"s{i}", created_at=timezone.now())
    last, written = backfill_batch_sync("", batch_size=3)
    assert written == 3 and last == "s2"
    run_all()
    assert TimelineEvent.objects.filter(kind="session.created").count() == 7
