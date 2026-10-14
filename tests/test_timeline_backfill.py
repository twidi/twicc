from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_backfill import (
    BackfillStatus, backfill_session_sync, candidate_ids, run_timeline_backfill,
)
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
    assert row.precision == "derived" and row.actor == "system" and row.project_id == project.id
    assert row.dedupe_key == "backfill:session.created"
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}


def test_creation_row_carries_the_spawner_when_the_column_is_set(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), spawned_by_id="boss", selected_model="opus")
    run_all()
    data = TimelineEvent.objects.get(session_id="kid", kind="session.created").data
    assert data["spawned_by"] == "boss" and data["settings"] is None


def test_flag_rows_use_last_updated_at_then_created_at(project):
    updated = timezone.now() - timedelta(days=1)
    created = updated - timedelta(days=5)
    make(project, "a", created_at=created, last_updated_at=updated, archived=True)
    make(project, "b", created_at=created, hidden=True)
    make(project, "c", created_at=created, mute_on_user_turn=True)
    run_all()
    assert TimelineEvent.objects.get(session_id="a", kind="archived.started").occurred_at == updated
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").occurred_at == created
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").precision == "estimated"
    assert TimelineEvent.objects.get(session_id="c", kind="muted.started").dedupe_key == "backfill:muted.started"


def test_pinned_row_carries_the_scope(project):
    make(project, created_at=timezone.now(), pinned="workspace")
    run_all()
    assert TimelineEvent.objects.get(session_id="s1", kind="pinned.started").data == {"scope": "workspace"}


def test_no_process_rows_are_ever_written(project):
    now = timezone.now()
    make(project, created_at=now, last_started_at=now, last_stopped_at=now)
    run_all()
    assert kinds("s1") == ["session.created"]


def test_running_twice_never_duplicates(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    run_all()
    first = TimelineEvent.objects.count()
    run_all()
    assert TimelineEvent.objects.count() == first == 2


def test_a_live_flag_row_without_session_created_suppresses_only_that_subject(project):
    make(project, created_at=timezone.now(), archived=True, hidden=True, last_updated_at=timezone.now())
    record_event_sync("archived.ended", session_id="s1", actor="user")
    run_all()
    assert kinds("s1") == ["archived.ended", "session.created", "hidden.started"]


def test_candidates_exclude_uncomputed_subagents_and_processed_sessions(project):
    now = timezone.now()
    make(project, "ok", created_at=now)
    make(project, "uncomputed")
    make(project, "kid", created_at=now, type=SessionType.SUBAGENT, parent_session=Session.objects.get(id="ok"))
    make(project, "done", created_at=now)
    record_event_sync("session.created", session_id="done", actor="system", data={"origin": "twicc"})
    assert candidate_ids() == ["ok"]


def test_candidates_are_ordered_by_mtime_descending_then_id_descending(project):
    now = timezone.now()
    for sid, mtime in (("s-a", 100.0), ("s-b", 300.0), ("s-c", 300.0), ("s-d", 0.0)):
        make(project, sid, created_at=now, mtime=mtime)
    assert candidate_ids() == ["s-c", "s-b", "s-a", "s-d"]


def test_a_session_processed_meanwhile_is_skipped_without_a_duplicate(project):
    make(project, created_at=timezone.now())
    record_event_sync("session.created", session_id="s1", actor="system", data={"origin": "twicc"})
    outcome = backfill_session_sync("s1")
    assert outcome.status == BackfillStatus.SKIPPED and outcome.rows == 0
    assert TimelineEvent.objects.filter(session_id="s1").count() == 1


def test_a_vanished_or_uncomputed_session_is_skipped(project):
    make(project, "uncomputed")
    assert backfill_session_sync("uncomputed").status == BackfillStatus.SKIPPED
    assert backfill_session_sync("nope").status == BackfillStatus.SKIPPED


def test_a_failure_on_a_later_row_rolls_the_whole_session_back(project, monkeypatch):
    make(project, created_at=timezone.now(), archived=True)

    def insert_one_then_fail(rows):
        TimelineEvent.objects.bulk_create(rows[:1])
        raise RuntimeError("boom")

    monkeypatch.setattr(timeline_backfill, "_insert_rows", insert_one_then_fail)
    with pytest.raises(RuntimeError):
        backfill_session_sync("s1")
    assert TimelineEvent.objects.count() == 0
    assert candidate_ids() == ["s1"]


def test_an_already_present_dedupe_key_counts_as_written_not_as_a_failure(project):
    make(project, created_at=timezone.now())
    TimelineEvent.objects.create(
        session_id="s1", kind="turn.started", occurred_at=timezone.now(), actor="system",
        dedupe_key="backfill:session.created",
    )
    outcome = backfill_session_sync("s1")
    assert outcome.status == BackfillStatus.WRITTEN and outcome.rows == 0


def test_the_statements_per_session_do_not_depend_on_its_timeline_size(project):
    now = timezone.now()
    make(project, "empty", created_at=now, archived=True)
    make(project, "busy", created_at=now, archived=True)
    TimelineEvent.objects.bulk_create([
        TimelineEvent(session_id="busy", kind="turn.started", occurred_at=now, actor="agent") for _ in range(300)
    ])
    with CaptureQueriesContext(connection) as empty:
        backfill_session_sync("empty")
    with CaptureQueriesContext(connection) as busy:
        backfill_session_sync("busy")
    assert len(busy) == len(empty) <= 12


@pytest.fixture
def no_pauses(monkeypatch):
    monkeypatch.setattr(timeline_backfill, "RETRY_PAUSES", (0.0, 0.0, 0.0))


def fail_inserts(monkeypatch, *, times, only_session=None):
    """Make ``_insert_rows`` raise ``times`` times (None = always), then work. Returns the call list."""
    calls = []
    real = timeline_backfill._insert_rows

    def flaky(rows):
        calls.append(rows[0].session_id)
        if (only_session is None or rows[0].session_id == only_session) and (times is None or len(calls) <= times):
            raise RuntimeError("database is locked")
        real(rows)

    monkeypatch.setattr(timeline_backfill, "_insert_rows", flaky)
    return calls


def test_a_transient_failure_is_retried_in_the_same_run(project, monkeypatch, no_pauses):
    make(project, created_at=timezone.now())
    calls = fail_inserts(monkeypatch, times=2)
    run_all()
    assert kinds("s1") == ["session.created"]
    assert len(calls) == 3


def test_a_permanent_failure_is_bounded_and_the_run_ends(project, monkeypatch, no_pauses):
    make(project, created_at=timezone.now())
    calls = fail_inserts(monkeypatch, times=None)
    run_all()
    assert kinds("s1") == []
    assert len(calls) == 1 + len(timeline_backfill.RETRY_PAUSES)
    assert candidate_ids() == ["s1"]


def test_a_failing_session_does_not_stop_the_others(project, monkeypatch, no_pauses):
    now = timezone.now()
    make(project, "bad", created_at=now, mtime=10.0)
    make(project, "good", created_at=now, mtime=5.0)
    fail_inserts(monkeypatch, times=None, only_session="bad")
    run_all()
    assert kinds("good") == ["session.created"] and kinds("bad") == []


def test_the_writer_stopping_ends_the_run_without_retries(project, monkeypatch, no_pauses):
    now = timezone.now()
    make(project, "a", created_at=now, mtime=2.0)
    make(project, "b", created_at=now, mtime=1.0)
    calls = []

    def stopping(session_id):
        calls.append(session_id)
        raise RuntimeError("DB writer is stopping")

    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", stopping)
    run_all()
    assert calls == ["a"]


def test_only_the_stopping_error_of_the_writer_is_recognised():
    assert timeline_backfill._writer_is_stopping(RuntimeError("DB writer is stopping"))
    assert not timeline_backfill._writer_is_stopping(RuntimeError("boom"))
    assert not timeline_backfill._writer_is_stopping(ValueError("DB writer is stopping"))


def test_a_second_run_has_no_candidate_and_takes_no_lock(project, monkeypatch):
    make(project, created_at=timezone.now())
    run_all()
    calls = []
    real = timeline_backfill.backfill_session_sync
    monkeypatch.setattr(timeline_backfill, "backfill_session_sync", lambda sid: calls.append(sid) or real(sid))
    run_all()
    assert calls == []


def _parity_rows(session_id, project, when=None):
    """Rows covering every kind of value: datetime, nested dict, empty reason, null cause, project."""
    when = when or timezone.now() - timedelta(days=3)
    return [
        TimelineEvent(
            session_id=session_id, project_id=project.id, kind="session.created", occurred_at=when,
            precision="derived", actor="system", dedupe_key="backfill:session.created",
            data={"origin": "sync", "settings": None, "nested": {"list": [1, "é", None], "flag": True}},
        ),
        TimelineEvent(
            session_id=session_id, project_id=project.id, kind="pinned.started", occurred_at=when,
            precision="estimated", actor="system", dedupe_key="backfill:pinned.started", data={"scope": "all"},
        ),
    ]


def test_raw_insert_stores_exactly_what_the_orm_stores(project):
    before = timezone.now()
    when = timezone.now() - timedelta(days=3)
    timeline_backfill._insert_rows(_parity_rows("raw", project, when))
    TimelineEvent.objects.bulk_create(_parity_rows("orm", project, when))
    fields = ("occurred_at", "precision", "actor", "reason", "caused_by_id", "data", "dedupe_key", "project_id", "kind")
    for kind in ("session.created", "pinned.started"):
        raw = TimelineEvent.objects.get(session_id="raw", kind=kind)
        orm = TimelineEvent.objects.get(session_id="orm", kind=kind)
        for name in fields:
            assert getattr(raw, name) == getattr(orm, name), name
        assert raw.reason == "" and raw.caused_by_id is None
        assert raw.recorded_at is not None
        assert before - timedelta(seconds=1) <= raw.recorded_at <= timezone.now() + timedelta(seconds=5)


def test_raw_insert_sql_is_built_from_the_model_metadata(project):
    columns = [f.column for f in TimelineEvent._meta.concrete_fields if not f.primary_key]
    with CaptureQueriesContext(connection) as captured:
        timeline_backfill._insert_rows(_parity_rows("meta", project))
    sql = next(q["sql"] for q in captured.captured_queries if "INSERT" in q["sql"])
    assert TimelineEvent._meta.db_table in sql
    for column in columns:
        assert f'"{column}"' in sql
    assert '"id"' not in sql


def test_raw_insert_propagates_a_duplicate(project):
    timeline_backfill._insert_rows(_parity_rows("dup", project))
    with pytest.raises(IntegrityError), transaction.atomic():
        timeline_backfill._insert_rows(_parity_rows("dup", project))
    assert TimelineEvent.objects.filter(session_id="dup").count() == 2
