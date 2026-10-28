import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_timeline import record_session_created
from twicc.providers.helpers import AgentSettings
from twicc.pending_session_attributes import pop_pending_session_attributes, set_pending_session_attributes
from twicc.providers.db_writer import CreateSessionPayload, _apply_create_session_payload
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


@pytest.fixture
def project(tmp_path):
    return Project.objects.create(id="-tl-created", directory=str(tmp_path))


def parsed(project, sid, type_=SessionType.SESSION):
    return ParsedSessionFile(project.id, sid, type_, f"{sid}.jsonl")


def test_a_discovered_session_is_external_and_records_no_settings(project):
    _Watcher().create_session_sync(
        parsed(project, "ext"), project, agent_settings=AgentSettings(selected_model="opus"),
    )
    row = TimelineEvent.objects.get(session_id="ext")
    assert row.kind == "session.created" and row.data["origin"] == "external"
    assert row.data["settings"] is None and row.data["spawned_by"] is None


def test_a_session_with_a_process_run_is_twicc_and_records_its_settings(project):
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id="mine", started_at=now, last_state_change_at=now)
    _Watcher().create_session_sync(
        parsed(project, "mine"), project, agent_settings=AgentSettings(selected_model="opus", effort="high"),
    )
    data = TimelineEvent.objects.get(session_id="mine").data
    assert data["origin"] == "twicc"
    assert data["settings"] == {"selected_model": "opus", "effort": "high"}


def test_the_spawning_session_is_recorded_from_the_row(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider=Provider.CODEX)
    set_pending_session_attributes("kid", spawned_by_id="boss")
    try:
        _Watcher().create_session_sync(parsed(project, "kid"), project)
    finally:
        pop_pending_session_attributes("kid")
    assert TimelineEvent.objects.get(session_id="kid", kind="session.created").data["spawned_by"] == "boss"


def test_pending_hidden_and_muted_flags_become_initial_intervals(project):
    set_pending_session_attributes("flagged", hidden=True, mute_on_user_turn=True)
    try:
        _Watcher().create_session_sync(parsed(project, "flagged"), project)
    finally:
        pop_pending_session_attributes("flagged")
    assert kinds("flagged") == ["session.created", "hidden.started", "muted.started"]


def test_created_detached_session_records_tree_detached_started(project):
    Session.objects.create(id="boss2", file_path="boss2.jsonl", project=project, provider=Provider.CODEX)
    session = Session.objects.create(id="det", file_path="det.jsonl", project=project, provider=Provider.CODEX,
                                     spawned_by_id="boss2", tree_detached=True)
    created = record_session_created(session, origin="twicc")
    assert kinds("det") == ["session.created", "tree_detached.started"]
    row = TimelineEvent.objects.get(session_id="det", kind="tree_detached.started")
    assert row.caused_by_id == created.id


def test_created_detached_session_without_spawner_records_event(project):
    session = Session.objects.create(id="orphan", file_path="orphan.jsonl", project=project,
                                     provider=Provider.CODEX, tree_detached=True)
    created = record_session_created(session, origin="twicc")
    assert created.data["spawned_by"] is None
    row = TimelineEvent.objects.get(session_id="orphan", kind="tree_detached.started")
    assert row.caused_by_id == created.id


def test_subagent_sessions_get_no_event(project):
    parent = _Watcher().create_session_sync(parsed(project, "parent"), project)
    _Watcher().create_session_sync(
        ParsedSessionFile(project.id, "child", SessionType.SUBAGENT, "child.jsonl", parent_session_id="parent"),
        project, parent_session=parent,
    )
    assert kinds("child") == []


def test_the_initial_sync_insert_writes_no_timeline_row(project):
    session = Session(id="synced", project_id=project.id, provider=Provider.CODEX, file_path="synced.jsonl",
                      type=SessionType.SESSION)
    payload = CreateSessionPayload(
        provider=Provider.CODEX, project_id=project.id, new_project_directory=None, new_project_stale=False,
        session=session, items=[], last_offset=0, last_line=0, mtime=0.0,
    )
    _apply_create_session_payload(payload)
    assert kinds("synced") == []


def test_deleting_sessions_purges_their_timeline_rows(project):
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload

    for sid in ("gone", "kept"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider=Provider.CODEX)
        record_session_created(Session.objects.get(id=sid), origin="sync")
    _apply_delete_sessions_payload(DeleteSessionsPayload(provider=Provider.CODEX, session_ids=["gone"]))
    assert kinds("gone") == [] and kinds("kept") == ["session.created"]


def test_a_failing_settings_read_never_blocks_session_creation(project, monkeypatch):
    monkeypatch.setattr("twicc.providers.helpers.AgentSettings.from_session",
                        classmethod(lambda cls, session: (_ for _ in ()).throw(RuntimeError("boom"))))
    session = Session.objects.create(id="boom", file_path="boom.jsonl", project=project, provider="codex")
    assert record_session_created(session, origin="twicc") is None
    assert Session.objects.filter(id="boom").exists()
    assert kinds("boom") == []
