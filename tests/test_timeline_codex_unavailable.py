"""Creation rows of Codex sessions marked unavailable (spec 2026-10-18, C9 and section 5.4)."""

import asyncio
from datetime import UTC, datetime

import pytest
from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill, timeline_signal
from twicc.providers.codex.rollout_migration import MarkSessionUnavailableJob, _apply_mark_session_unavailable_job
from twicc.providers.compute_tree import NativeTreeError
from tests.timeline_helpers import kinds, log_records_of, mark_known

pytestmark = pytest.mark.django_db(transaction=True)

CREATED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-unavailable", directory="/tmp/tl-unavailable")


@pytest.fixture
def pokes(monkeypatch):
    calls = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: calls.append(1))
    return calls


def make(project, sid, **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def make_computed_root(project, sid="r", **kw):
    return make(project, sid, created_at=CREATED, compute_version=57, **kw)


def mark(sid, reason="rollout_missing"):
    # The job needs a future; the apply function never awaits it. The loop is closed right after.
    loop = asyncio.new_event_loop()
    try:
        return _apply_mark_session_unavailable_job(
            MarkSessionUnavailableJob(Provider.CODEX, sid, reason, loop.create_future()),
        )
    finally:
        loop.close()


def test_a_computed_root_marked_unavailable_gets_its_creation_row_from_the_stored_created_at(project, pokes):
    make_computed_root(project, pinned="project")
    assert mark("r") == 1
    assert Session.objects.get(id="r").unavailable_reason == "rollout_missing"
    assert kinds("r") == ["session.created"]  # no flag row: the pin has no known date
    created = TimelineEvent.objects.get(session_id="r", kind="session.created")
    assert created.occurred_at == CREATED
    assert pokes == [1]


def test_a_subagent_marked_unavailable_gives_its_root_the_rows(project, pokes):
    make_computed_root(project)
    make(project, "c", type=SessionType.SUBAGENT, parent_session_id="r", created_at=CREATED, compute_version=57)
    assert mark("c") == 1
    assert Session.objects.get(id="c").unavailable_reason == "rollout_missing"
    assert kinds("r") == ["session.created"]
    assert kinds("c") == []
    assert pokes == [1]


def test_a_root_with_an_empty_compute_version_gets_nothing(project, pokes):
    make(project, "r", created_at=CREATED, compute_version=None)
    assert mark("r") == 1
    assert TimelineEvent.objects.count() == 0
    assert pokes == []


def test_a_known_root_gets_nothing(project, pokes):
    make_computed_root(project)
    mark_known("r", project.id)
    assert mark("r") == 1
    assert kinds("r") == ["session.created"]
    assert TimelineEvent.objects.filter(session_id="r", dedupe_key__isnull=False).count() == 0
    assert pokes == []


def test_a_second_marking_and_a_clearing_write_nothing(project, pokes):
    make_computed_root(project)
    assert mark("r") == 1
    first = list(TimelineEvent.objects.filter(session_id="r").values_list("id", "kind"))
    assert mark("r") == 1
    assert mark("r", reason=None) == 1
    assert list(TimelineEvent.objects.filter(session_id="r").values_list("id", "kind")) == first
    assert kinds("r") == ["session.created"]
    assert Session.objects.get(id="r").unavailable_reason is None
    assert pokes == [1]


def test_a_clearing_of_an_unknown_root_writes_nothing(project, pokes):
    make_computed_root(project, unavailable_reason="rollout_missing")
    assert mark("r", reason=None) == 1
    assert Session.objects.get(id="r").unavailable_reason is None
    assert TimelineEvent.objects.count() == 0
    assert pokes == []


def test_a_failure_of_the_rows_never_prevents_the_marking(project, pokes, monkeypatch):
    make_computed_root(project)

    def boom(rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(timeline_backfill, "insert_event_rows", boom)
    with log_records_of(timeline_backfill.logger) as records:
        assert mark("r") == 1
    assert Session.objects.get(id="r").unavailable_reason == "rollout_missing"
    assert TimelineEvent.objects.count() == 0
    assert any(
        record.exc_info and isinstance(record.exc_info[1], RuntimeError) and str(record.exc_info[1]) == "boom"
        for record in records
    )
    assert pokes == []


def test_an_unmatched_id_through_the_job_writes_nothing(pokes):
    with log_records_of(timeline_backfill.logger) as records:
        assert mark("ghost") == 0
    assert TimelineEvent.objects.count() == 0
    assert records == []
    assert pokes == []


def test_a_missing_session_through_the_function_never_raises(pokes):
    with log_records_of(timeline_backfill.logger) as records:
        assert timeline_backfill.write_unavailable_root_creation_row_sync("ghost") == 0
    assert TimelineEvent.objects.count() == 0
    assert any(record.exc_info and isinstance(record.exc_info[1], Session.DoesNotExist) for record in records)
    assert pokes == []


def test_a_cyclic_tree_never_raises(project, pokes):
    make(project, "a", created_at=CREATED, compute_version=57)
    make(project, "b", created_at=CREATED, compute_version=57)
    Session.objects.filter(id="a").update(parent_session_id="b")
    Session.objects.filter(id="b").update(parent_session_id="a")
    with log_records_of(timeline_backfill.logger) as records:
        assert mark("a") == 1
    assert Session.objects.get(id="a").unavailable_reason == "rollout_missing"
    assert TimelineEvent.objects.count() == 0
    assert any(record.exc_info and isinstance(record.exc_info[1], NativeTreeError) for record in records)
    assert pokes == []


def test_the_flag_and_the_rows_share_one_transaction(project, monkeypatch):
    make_computed_root(project)
    in_atomic = []
    original = timeline_backfill.write_unavailable_root_creation_row_sync

    def recording(session_id):
        in_atomic.append(transaction.get_connection().in_atomic_block)
        return original(session_id)

    # The job imports the function lazily at call time, so it reads this module attribute.
    monkeypatch.setattr(timeline_backfill, "write_unavailable_root_creation_row_sync", recording)
    assert mark("r") == 1
    assert in_atomic == [True]
    assert kinds("r") == ["session.created"]
