import pytest

from twicc.core.models import Project, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services import session_timeline
from twicc.core.services.session_timeline import EventSpec, record_event_sync, record_events_bulk_sync
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-bulkrec", directory="/tmp/tl-bulkrec")


def test_a_batch_writes_every_valid_spec_with_the_single_event_semantics(project):
    count = record_events_bulk_sync([
        EventSpec(TimelineKind.PINNED_STARTED, session_id="a", project_id=project.id, actor=TimelineActor.USER,
                  data={"scope": "project"}),
        EventSpec(TimelineKind.TURN_STARTED, session_id="b", project_id=project.id, actor=TimelineActor.AGENT),
    ])
    assert count == 2
    row = TimelineEvent.objects.get(session_id="a")
    assert row.data == {"scope": "project"} and row.actor == "user" and row.project_id == project.id
    assert row.recorded_at is not None and row.occurred_at is not None
    assert kinds("b") == ["turn.started"]


def test_an_invalid_spec_is_skipped_and_the_others_are_kept(project):
    count = record_events_bulk_sync([
        EventSpec(TimelineKind.TURN_STARTED, session_id="a", actor=TimelineActor.AGENT, data={"bogus": 1}),
        EventSpec(TimelineKind.TURN_STARTED, session_id="b", actor=TimelineActor.AGENT),
    ])
    assert count == 1 and kinds("a") == [] and kinds("b") == ["turn.started"]


def test_an_ended_row_inherits_the_data_of_its_open_started_row(project):
    record_event_sync(TimelineKind.PINNED_STARTED, session_id="a", actor=TimelineActor.USER,
                      data={"scope": "workspace"})
    record_events_bulk_sync([EventSpec(TimelineKind.PINNED_ENDED, session_id="a", actor=TimelineActor.USER)])
    assert TimelineEvent.objects.get(session_id="a", kind="pinned.ended").data == {"scope": "workspace"}


def test_dedupe_keys_are_checked_against_the_table_and_inside_the_batch(project):
    data = {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}
    record_event_sync(TimelineKind.SESSION_CREATED, session_id="a", dedupe_key="k",
                      actor=TimelineActor.SYSTEM, data=data)
    created = {"kind": TimelineKind.SESSION_CREATED, "dedupe_key": "k", "actor": TimelineActor.SYSTEM, "data": data}
    count = record_events_bulk_sync([
        EventSpec(session_id="a", **created),
        EventSpec(session_id="b", **created),
        EventSpec(session_id="b", **created),
    ])
    assert count == 1
    assert TimelineEvent.objects.filter(kind="session.created").count() == 2


def test_a_failing_batch_falls_back_to_row_by_row_and_never_raises(project, monkeypatch):
    def boom(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", boom)
    count = record_events_bulk_sync([
        EventSpec(TimelineKind.TURN_STARTED, session_id="a", actor=TimelineActor.AGENT),
        EventSpec(TimelineKind.TURN_STARTED, session_id="b", actor=TimelineActor.AGENT),
    ])
    assert count == 2 and kinds("a") == kinds("b") == ["turn.started"]


def test_an_empty_batch_is_a_noop():
    assert record_events_bulk_sync([]) == 0
