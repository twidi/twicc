"""Creation rows written by the compute's final transaction (spec 2026-10-18, C1/C3/C8)."""

from contextlib import contextmanager

import pytest

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services import timeline_backfill, timeline_signal
from twicc.providers.compute_base import BaseSessionCompute
from tests.timeline_helpers import kinds, log_records_of, mark_known

pytestmark = pytest.mark.django_db(transaction=True)

CREATED = "2026-01-02T03:04:05+00:00"
UPDATED = "2026-03-04T05:06:07+00:00"
PROVISIONAL = "2026-02-01T00:00:00+00:00"


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-compute", directory="/tmp/tl-compute")


@pytest.fixture
def pokes(monkeypatch):
    calls = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: calls.append(1))
    return calls


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="claude_code", **kw)


def publish(sid, version=99, fields=None, **extra):
    """A minimal final publication (the shape of tests/test_root_compute_completion.py). ``fields``
    overrides session fields; ``extra`` adds message keys (``placeholder_titles``, ``provider_titles``)."""
    return BaseSessionCompute.apply_session_complete({
        "session_id": sid, "history_facts": [],
        "session_fields": {"compute_version": version, "created_at": CREATED, "last_updated_at": UPDATED,
                           **(fields or {})},
        **extra,
    })


def test_the_first_compute_writes_the_creation_row_at_the_computed_created_at(project, pokes):
    make(project, created_at=PROVISIONAL, hidden=True)
    assert publish("s1").outcome == "applied"
    created = TimelineEvent.objects.get(session_id="s1", kind="session.created")
    assert created.occurred_at.isoformat() == CREATED
    assert created.precision == "derived"
    assert created.data["origin"] == "sync"
    assert kinds("s1") == ["session.created"]  # no flag row: the hidden flag has no known date


def test_a_known_session_gets_nothing_and_no_signal(project, pokes):
    make(project)
    mark_known("s1", project.id)
    assert publish("s1").outcome == "applied"
    assert TimelineEvent.objects.filter(session_id="s1", kind="session.created").count() == 1
    assert kinds("s1") == ["session.created"]
    assert pokes == []


def test_only_the_root_of_a_tree_gets_rows(project, pokes):
    root = make(project, "r")
    make(project, "c", type=SessionType.SUBAGENT, parent_session=root)
    assert publish("r").outcome == "superseded"
    assert TimelineEvent.objects.count() == 0
    assert pokes == []
    assert publish("c").outcome == "applied"
    assert kinds("c") == [] and kinds("r") == []
    assert pokes == []
    assert publish("r").outcome == "applied"
    assert kinds("r") == ["session.created"]
    assert kinds("c") == []
    assert pokes == [1]


def test_an_empty_created_at_gets_nothing(project, pokes):
    make(project)
    assert publish("s1", fields={"created_at": None}).outcome == "applied"
    assert TimelineEvent.objects.count() == 0
    assert pokes == []


def test_a_failing_timeline_step_leaves_the_compute_applied(project, pokes, monkeypatch):
    def boom(rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(timeline_backfill, "insert_event_rows", boom)
    make(project)
    with log_records_of(timeline_backfill.logger) as records:
        assert publish("s1").outcome == "applied"
    assert Session.objects.get(id="s1").compute_version == 99
    assert TimelineEvent.objects.count() == 0
    assert pokes == []
    assert any(r.exc_info and str(r.exc_info[1]) == "boom" for r in records)


def test_a_rolled_back_publication_leaves_no_row_and_no_signal(project, pokes, monkeypatch):
    rows_before_failure = []

    def fail(msg):
        rows_before_failure.append(TimelineEvent.objects.filter(session_id="s1").count())
        raise RuntimeError("late failure")

    monkeypatch.setattr("twicc.core.content_storage.extraction.finish_storage_result", fail)
    make(project)
    with pytest.raises(RuntimeError, match="late failure"):
        publish("s1")
    # The rows were written inside the transaction, then discarded with its rollback.
    assert rows_before_failure == [1]
    assert TimelineEvent.objects.count() == 0
    assert pokes == []
    assert Session.objects.get(id="s1").compute_version is None


def test_the_signal_is_requested_once_per_published_root(project, pokes):
    make(project, "a")
    make(project, "b")
    assert publish("a").outcome == "applied"
    assert publish("b").outcome == "applied"
    assert kinds("a") == ["session.created"] and kinds("b") == ["session.created"]
    assert pokes == [1, 1]


def test_the_step_is_timed_in_the_hold_diagnostics(project, pokes, monkeypatch):
    phases = []

    @contextmanager
    def recorder(name, **kwargs):
        phases.append(name)
        yield

    monkeypatch.setattr("twicc.providers.compute_base.timed_sync_phase", recorder)
    make(project)
    assert publish("s1").outcome == "applied"
    assert "compute_final_timeline" in phases
    assert phases[phases.index("compute_final_timeline") - 1] == "compute_final_titles"


def test_the_first_compute_records_no_title_and_a_later_change_is_recorded(project):
    make(project)
    assert publish("s1", placeholder_titles={"s1": "First words"}).outcome == "applied"
    assert kinds("s1") == ["session.created"]
    assert publish("s1", version=100, provider_titles={"s1": "Renamed"}).outcome == "applied"
    assert kinds("s1") == ["session.created", "title.changed"]
    assert TimelineEvent.objects.get(kind="title.changed").data == {
        "source": "agent", "new": "Renamed", "old": "First words",
    }
