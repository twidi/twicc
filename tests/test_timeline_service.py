# tests/test_timeline_service.py
import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.models import (
    Project, Session, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.session_timeline import (
    latest_event_id, project_id_of, record_event, record_event_sync, record_session_created,
)
from tests.timeline_helpers import kinds, mark_known, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-service", directory="/tmp/tl-service")


def test_record_event_sync_writes_one_row_with_defaults(project):
    before = timezone.now()
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", project_id=project.id,
                              actor=TimelineActor.AGENT)
    assert event is not None
    event.refresh_from_db()
    assert event.project_id == project.id and event.precision == TimelinePrecision.EXACT
    assert event.occurred_at >= before and event.data == {}


def test_invalid_payload_returns_none_and_writes_nothing(project):
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                             data={"bogus": 1}) is None
    assert TimelineEvent.objects.count() == 0


def test_dedupe_key_makes_a_second_write_a_noop(project):
    kwargs = {"session_id": "s1", "actor": TimelineActor.SYSTEM, "dedupe_key": "k1", "data": {"origin": "sync"}}
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is not None
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is None
    assert TimelineEvent.objects.count() == 1


def test_a_failing_insert_does_not_poison_the_callers_transaction(project):
    with transaction.atomic():
        Project.objects.create(id="-tl-a", directory="/tmp/a")
        # precision=None violates NOT NULL: the INSERT fails at SQL level.
        assert record_event_sync(
            TimelineKind.TURN_STARTED, session_id="s1", actor="system", precision=None,
        ) is None
        Project.objects.create(id="-tl-b", directory="/tmp/b")
    assert Project.objects.filter(id__in=["-tl-a", "-tl-b"]).count() == 2


def test_caused_by_accepts_an_event_or_an_id(project):
    cause = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    by_event = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.USER,
                                 caused_by=cause)
    by_id = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.USER,
                              caused_by=cause.id)
    assert by_event.caused_by_id == by_id.caused_by_id == cause.id


def test_latest_event_id_and_project_id_of(project):
    Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex")
    first = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    second = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    assert latest_event_id("s1", TimelineKind.ARCHIVED_STARTED) == second.id != first.id
    assert latest_event_id("s1", TimelineKind.HIDDEN_STARTED) is None
    assert project_id_of("s1") == project.id and project_id_of("nope") is None


def test_an_ended_row_inherits_the_data_of_the_open_started_row_of_its_session(project):
    started = {"session_id": "s1", "actor": TimelineActor.USER}
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "claude_code", "twicc_pid": 1}, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED,
                      data={"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True}, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="other", actor=TimelineActor.USER,
                      data={"provider": "claude_code"})
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM,
                              data={"error": "boom"})
    assert ended.data == {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True,
                          "error": "boom", "upper_bound": None}


def test_a_closed_interval_is_never_inherited(project):
    started = {"session_id": "s1", "actor": TimelineActor.USER}
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    first = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert first.data["provider"] == "codex"
    # No new ``started`` row (lost write, or a second stale row at boot): nothing to inherit.
    second = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert second.data == {"provider": None, "cause": None, "twicc_pid": None, "hybrid": None,
                           "error": None, "upper_bound": None}


def test_a_lost_start_does_not_fall_back_to_an_older_closed_interval(project):
    started = {"session_id": "s1", "actor": TimelineActor.USER}
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    # The second ``process.started`` write was lost: only its ``ended`` row exists.
    assert record_event_sync(TimelineKind.PROCESS_ENDED, **started).data["provider"] is None


def test_a_reopened_interval_inherits_its_own_data(project):
    started = {"session_id": "s1", "actor": TimelineActor.USER}
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "claude_code", "twicc_pid": 8}, **started)
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert ended.data["provider"] == "claude_code" and ended.data["twicc_pid"] == 8


def test_a_pin_scope_change_inherits_the_scope_of_each_interval(project):
    pin = {"session_id": "s1", "actor": TimelineActor.USER}
    record_event_sync(TimelineKind.PINNED_STARTED, data={"scope": "project"}, **pin)
    assert record_event_sync(TimelineKind.PINNED_ENDED, **pin).data == {"scope": "project"}
    record_event_sync(TimelineKind.PINNED_STARTED, data={"scope": "all"}, **pin)
    assert record_event_sync(TimelineKind.PINNED_ENDED, **pin).data == {"scope": "all"}


def test_a_field_provided_by_the_caller_wins_over_the_started_row(project):
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="s1", actor=TimelineActor.USER,
                      data={"provider": "codex", "twicc_pid": 7})
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM,
                              data={"provider": "claude_code", "twicc_pid": None})
    assert ended.data["provider"] == "claude_code" and ended.data["twicc_pid"] is None
    assert ended.data["cause"] == "new"  # still inherited


def test_an_ended_row_without_a_started_row_keeps_its_fields_null(project):
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM)
    assert ended.data == {"provider": None, "cause": None, "twicc_pid": None, "hybrid": None,
                          "error": None, "upper_bound": None}
    pinned = record_event_sync(TimelineKind.PINNED_ENDED, session_id="s1", actor=TimelineActor.USER)
    assert pinned.data == {"scope": None}


def test_pin_and_awaiting_input_ended_rows_inherit_too(project):
    record_event_sync(TimelineKind.PINNED_STARTED, session_id="s1", actor=TimelineActor.USER, data={"scope": "all"})
    record_event_sync(TimelineKind.AWAITING_INPUT_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                      data={"request_type": "tool_approval", "tool_name": "Bash"})
    assert record_event_sync(TimelineKind.PINNED_ENDED, session_id="s1", actor=TimelineActor.USER).data == {"scope": "all"}
    assert record_event_sync(TimelineKind.AWAITING_INPUT_ENDED, session_id="s1", actor=TimelineActor.AGENT).data == {
        "request_type": "tool_approval", "tool_name": "Bash"}


def test_instance_events_inherit_from_the_latest_instance_row(project):
    record_event_sync(TimelineKind.BACKEND_STARTED, actor=TimelineActor.SYSTEM,
                      data={"pid": 5, "version": "1.0", "boot_id": "b", "machine_restarted": False})
    ended = record_event_sync(TimelineKind.BACKEND_ENDED, actor=TimelineActor.SYSTEM, data={"clean": True})
    assert ended.data == {"clean": True, "pid": 5, "version": "1.0", "boot_id": "b", "machine_restarted": False}


def test_kinds_without_a_started_payload_copy_nothing(project):
    record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    assert record_event_sync(TimelineKind.ARCHIVED_ENDED, session_id="s1", actor=TimelineActor.USER).data == {}


def test_async_record_event_runs_under_the_write_lock(project):
    # Real writer: the test is about the async service under the real DB write lock.
    event = run_with_writer(lambda: record_event(
        TimelineKind.TURN_STARTED, session_id="s1", project_id=project.id, actor=TimelineActor.AGENT))
    assert event is not None and kinds("s1") == ["turn.started"]


def test_record_session_created_writes_the_initial_flags_caused_by_the_creation(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", hidden=True,
                                     mute_on_user_turn=True)
    record_session_created(session, origin="twicc")
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [r.kind for r in rows] == ["session.created", "hidden.started", "muted.started"]
    assert rows[0].data == {"origin": "twicc", "provider": "codex", "settings": {}, "spawned_by": None}
    assert rows[1].caused_by_id == rows[2].caused_by_id == rows[0].id
    assert rows[0].project_id == project.id


def test_record_session_created_reads_the_settings_and_the_spawner_from_the_row(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider="codex")
    session = Session.objects.create(
        id="s1", file_path="s1.jsonl", project=project, provider="codex", spawned_by_id="boss",
        selected_model="gpt-x", effort="high", thinking_enabled=False,
    )
    record_session_created(session, origin="twicc")
    # Only the non-null fields; ``False`` is a value, not an absence.
    assert TimelineEvent.objects.get(session_id="s1").data == {
        "origin": "twicc", "provider": "codex", "spawned_by": "boss",
        "settings": {"selected_model": "gpt-x", "effort": "high", "thinking_enabled": False},
    }


def test_record_session_created_keeps_the_settings_for_twicc_sessions_only(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider="codex")
    for origin in ("external", "sync"):
        session = Session.objects.create(
            id=f"s-{origin}", file_path=f"{origin}.jsonl", project=project, provider="codex",
            spawned_by_id="boss", selected_model="gpt-x",
        )
        record_session_created(session, origin=origin)
        data = TimelineEvent.objects.get(session_id=session.id).data
        assert data["settings"] is None and data["spawned_by"] == "boss"


def test_record_title_change_skips_subagents_and_unknown_sessions(project):
    from twicc.core.models import SessionType
    from twicc.core.services.session_timeline import record_title_change

    parent = Session.objects.create(id="p", file_path="p.jsonl", project=project, provider="codex")
    Session.objects.create(id="c", file_path="c.jsonl", project=project, provider="codex",
                           type=SessionType.SUBAGENT, parent_session=parent)
    mark_known("p", project.id)
    mark_known("c", project.id)  # known too, so only the subagent rule can drop its title
    record_title_change("c", "Child title", source="agent")
    record_title_change("nope", "Ghost", source="agent")
    record_title_change("p", "Parent title", source="agent", old="Before")
    assert kinds("c") == ["session.created"] and kinds("nope") == []
    assert kinds("p") == ["session.created", "title.changed"]
    assert TimelineEvent.objects.get(session_id="p", kind="title.changed").data == {
        "source": "agent", "new": "Parent title", "old": "Before",
    }
    record_title_change("p", "Next", source="user")
    assert TimelineEvent.objects.filter(session_id="p").order_by("-id").first().data["old"] is None


def test_record_session_created_without_flags_writes_one_row(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex")
    record_session_created(session, origin="sync")
    assert kinds("s1") == ["session.created"]


def test_async_record_event_never_raises_when_the_writer_is_stopped(project):
    import asyncio

    # No DB writer started: the write lock refuses the acquisition.
    event = asyncio.run(record_event(
        TimelineKind.TURN_STARTED, session_id="s1", project_id=project.id, actor=TimelineActor.AGENT))
    assert event is None and kinds("s1") == []
