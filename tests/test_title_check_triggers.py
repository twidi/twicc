"""Title requests follow indexed live updates and deliberate closing events."""

import asyncio
import importlib
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from django.test import RequestFactory
from django.utils import timezone
from watchfiles import Change

from twicc import search, title_auto_task
from twicc.agent.base_agent import BaseAgent
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, Session, SessionItem, SessionType
from twicc.core.services.session_update import apply_session_archived_change
from twicc.providers import db_writer
from twicc.providers.helpers import AgentSettings
from twicc.providers.sessions_watcher import IndexingRequest, ParsedSessionFile, SessionChangeResult

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def requests(monkeypatch):
    recorded = []

    def record(session_id, *, closing=False):
        assert not db_writer._db_write_lock.locked()
        recorded.append((session_id, closing))

    monkeypatch.setattr(title_auto_task, "request_title_check", record)
    monkeypatch.setattr(search, "is_initialized", lambda: False)
    return recorded


def run_with_writer(scenario):
    async def run():
        db_writer.start_db_writer()
        try:
            return await scenario()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(run())


def session_file(provider_home, provider, session_id="title-trigger"):
    if provider == Provider.CLAUDE_CODE:
        path = provider_home.claude / "projects" / "-tmp-title-project" / f"{session_id}.jsonl"
        records = [{"type": "user", "uuid": "message-one", "cwd": "/tmp/title-project",
                    "timestamp": "2026-10-04T12:00:00Z",
                    "message": {"role": "user", "content": "Implement automatic session titles"}}]
    else:
        path = provider_home.codex / "sessions" / "2026" / "10" / "04" / f"rollout-{session_id}.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/title-project", "source": "cli",
                                                  "history_mode": "paginated"}},
            {"type": "event_msg", "timestamp": "2026-10-04T12:00:00Z", "payload": {
                "type": "item_completed", "turn_id": "turn-one", "item": {
                    "type": "UserMessage", "id": "message-one",
                    "content": [{"type": "text", "text": "Implement automatic session titles", "text_elements": []}],
                },
            }},
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(orjson.dumps(record) + b"\n" for record in records))
    return path


def watcher_for(provider):
    module = importlib.import_module(f"twicc.providers.{provider.value}.sessions_watcher")
    name = "ClaudeCodeSessionsWatcher" if provider == Provider.CLAUDE_CODE else "CodexSessionsWatcher"
    return getattr(module, name)()


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("search_failure", [False, True])
def test_live_indexed_lines_request_outside_lock_without_search_dependency(
    provider_home, monkeypatch, requests, provider, search_failure,
):
    path = session_file(provider_home, provider)
    watcher = watcher_for(provider)
    project = Project.objects.create(id="title-project", directory="/tmp/title-project")
    session = Session.objects.create(id="title-trigger", project=project, provider=provider,
                                     compute_version=watcher.get_compute().compute_version,
                                     title="Existing title", title_origin="user")
    if provider == Provider.CODEX:
        metadata = path.read_bytes().splitlines(keepends=True)[0]
        SessionItem.objects.create(session=session, line_num=1, content=metadata.decode().strip())
        session.last_offset = len(metadata)
        session.last_line = 1
        session.save(update_fields=["last_offset", "last_line"])
    parsed = ParsedSessionFile(project.id, session.id, SessionType.SESSION, str(path))
    if search_failure:
        async def failed_index(*args):
            raise OSError("search unavailable")
        monkeypatch.setattr(watcher, "_index_new_items_for_search", failed_index)

    async def scenario():
        channel = SimpleNamespace(group_send=AsyncMock())
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, channel)
        assert result.indexing is not None, result
        assert result.indexing.new_line_nums
        assert await SessionItem.objects.filter(session_id=session.id).acount() > 0

    run_with_writer(scenario)
    assert requests == [(session.id, False)]


@pytest.mark.parametrize("case", ["deleted", "no-lines", "subagent", "missing-indexed", "not-ready", "no-indexing"])
def test_live_request_requires_indexed_top_level_ready_session(tmp_path, monkeypatch, requests, case):
    watcher = watcher_for(Provider.CLAUDE_CODE)
    session = Session.objects.create(
        id="gated", project=Project.objects.create(id="gates"), provider=Provider.CLAUDE_CODE,
        type=SessionType.SUBAGENT if case == "subagent" else SessionType.SESSION,
        compute_version=0 if case == "not-ready" else watcher.get_compute().compute_version,
    )
    path = tmp_path / "gated.jsonl"
    path.write_text("{}\n")
    parsed = ParsedSessionFile(session.project_id, session.id, session.type, str(path), title="Known")
    indexing = None if case in ("deleted", "no-indexing") else IndexingRequest(
        "absent" if case == "missing-indexed" else session.id,
        [] if case == "no-lines" else [1], False,
    )
    monkeypatch.setattr(watcher, "sync_and_broadcast", AsyncMock(
        return_value=SessionChangeResult("drained", indexing=indexing),
    ))
    monkeypatch.setattr(watcher, "_index_new_items_for_search", AsyncMock())

    async def scenario():
        await watcher._process_parsed_session_change(
            path, parsed, Change.deleted if case == "deleted" else Change.modified, None,
        )

    run_with_writer(scenario)
    assert requests == []


def manager_agent(provider, session, *, ephemeral=False):
    module = importlib.import_module(f"twicc.providers.{provider.value}.agent.manager")
    name = "ClaudeCodeAgentManager" if provider == Provider.CLAUDE_CODE else "CodexAgentManager"
    manager = getattr(module, name)()
    agent_class = type("TriggerAgent", (BaseAgent,), {"provider": provider})
    agent = agent_class(session.id, session.project_id, "/tmp/title-project", AgentSettings(), ephemeral=ephemeral)
    agent._old_runs_purged = True
    agent._first_user_turn_reached = True
    manager._agents[session.id] = agent
    return manager, agent


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("reason", [
    "manual", "force", "archived", "shutdown", "startup-failed", "apply-settings", "switch-hybrid",
    "cron_restart_timeout", "timeout_starting", "timeout_user_turn", "timeout_assistant_turn", "cli-exit", None,
])
def test_dead_requests_only_manual_or_force_after_real_manager_cleanup(monkeypatch, requests, provider, reason):
    session = Session.objects.create(id="closing", project=Project.objects.create(id="close-project"), provider=provider)
    manager, agent = manager_agent(provider, session)
    now = timezone.now()
    agent.process_run = ProcessRun.objects.create(
        session_id=session.id, provider=provider, started_at=now, last_state_change_at=now,
        state=AgentState.USER_TURN.value,
    )
    agent._set_state(AgentState.DEAD)
    agent.kill_reason = reason
    recorded = []

    def record(session_id, *, closing=False):
        assert not db_writer._db_write_lock.locked()
        assert session_id not in manager._agents
        assert agent.process_run is None
        recorded.append((session_id, closing))

    monkeypatch.setattr(title_auto_task, "request_title_check", record)
    run_with_writer(lambda: manager._on_state_change(agent))
    session.refresh_from_db()
    assert session.last_stopped_at is not None
    assert not ProcessRun.objects.filter(session_id=session.id).exists()
    assert recorded == ([(session.id, True)] if reason in {"manual", "force"} else [])


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("reason", ["manual", "force"])
def test_ephemeral_dead_never_requests_title(requests, provider, reason):
    session = SimpleNamespace(id="ephemeral", project_id="ephemeral-project")
    manager, agent = manager_agent(provider, session, ephemeral=True)
    agent._set_state(AgentState.DEAD)
    agent.kill_reason = reason
    run_with_writer(lambda: manager._on_state_change(agent))
    assert requests == []
    assert not Session.objects.filter(id=session.id).exists()


@pytest.mark.parametrize("provider", list(Provider))
def test_user_turn_finished_does_not_request_title(monkeypatch, requests, provider):
    session = Session.objects.create(id="finished", project=Project.objects.create(id="finish-project"), provider=provider)
    manager, agent = manager_agent(provider, session)
    agent._set_state(AgentState.USER_TURN)
    if provider == Provider.CLAUDE_CODE:
        monkeypatch.setattr(manager, "_apply_pending_settings", AsyncMock())
    run_with_writer(lambda: manager._on_state_change(agent))
    assert requests == []
    assert session.id in manager._agents


@pytest.mark.parametrize("already_dead", [False, True])
def test_archive_transition_requests_after_all_teardown(monkeypatch, requests, already_dead):
    from twicc.agent import registry
    from twicc import terminal

    session = Session.objects.create(id="archive", project=Project.objects.create(id="archive-project"))
    events = []

    async def kill(session_id, *, reason):
        assert session_id == session.id and reason == "archived"
        assert requests == []
        events.append("kill")
        return not already_dead

    def kill_tmux(scope):
        assert scope == f"s:{session.id}"
        assert requests == []
        assert not ProcessRun.objects.filter(session_id=session.id).exists()
        events.append("tmux")

    now = timezone.now()
    ProcessRun.objects.create(session_id=session.id, provider=session.provider, started_at=now,
                              last_state_change_at=now, state=AgentState.DEAD.value)
    monkeypatch.setattr(registry, "get_agent_manager_registry", lambda: SimpleNamespace(kill_agent=kill))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", kill_tmux)
    run_with_writer(lambda: apply_session_archived_change(session, True))
    session.refresh_from_db()
    assert session.archived is True
    assert events == ["kill", "tmux"]
    assert requests == [(session.id, True)]


@pytest.mark.parametrize("before,after", [(True, True), (True, False), (False, False)])
def test_archive_without_false_to_true_transition_requests_nothing(monkeypatch, requests, before, after):
    from twicc.agent import registry
    from twicc import terminal

    session = Session.objects.create(id="archive-repeat", project=Project.objects.create(id="archive-repeat-project"),
                                     archived=before)
    monkeypatch.setattr(registry, "get_agent_manager_registry", lambda: SimpleNamespace(kill_agent=AsyncMock()))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)
    run_with_writer(lambda: apply_session_archived_change(session, after))
    assert requests == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_bulk_archive_requests_only_final_selected_ids(monkeypatch, requests, dry_run):
    from twicc import views

    project = Project.objects.create(id="bulk-project")
    for session_id, options in [
        ("selected-one", {}), ("selected-two", {}), ("active", {}), ("became-active", {}),
        ("archived", {"archived": True}), ("pinned", {"pinned": "project"}),
        ("hidden", {"hidden": True}), ("subagent", {"type": SessionType.SUBAGENT}),
        ("empty", {"user_message_count": 0}), ("recent", {"mtime": 10**12}),
    ]:
        values = {"user_message_count": 1, "created_at": timezone.now(), "mtime": 0, **options}
        Session.objects.create(id=session_id, project=project, file_path=f"{session_id}.jsonl", **values)
    snapshots = iter([[SimpleNamespace(session_id="active")],
                      [SimpleNamespace(session_id="active"), SimpleNamespace(session_id="became-active")]])
    registry = SimpleNamespace(get_active_agents=lambda: next(snapshots))
    monkeypatch.setattr(views, "get_agent_manager_registry", lambda: registry)
    monkeypatch.setattr(views, "kill_all_tmux_terminals", lambda scope: None)
    request = RequestFactory().post("/api/sessions/bulk-archive/", data=orjson.dumps({
        "older_than": "2026-10-04T12:00:00Z", "scope": "project", "project_id": project.id,
        "dry_run": dry_run,
    }), content_type="application/json")

    async def scenario():
        response = await views.bulk_archive_sessions(request)
        if views._DETACHED_TASKS:
            await asyncio.gather(*views._DETACHED_TASKS)
        return orjson.loads(response.content)

    result = run_with_writer(scenario)
    assert result["count"] == (3 if dry_run else 2)
    assert set(requests) == (set() if dry_run else {("selected-one", True), ("selected-two", True)})
    assert Session.objects.get(id="selected-one").archived is (not dry_run)
    assert not Session.objects.get(id="active").archived
    assert not Session.objects.get(id="became-active").archived


@pytest.mark.parametrize("provider", list(Provider))
def test_startup_initial_sync_does_not_request_title(provider_home, requests, provider):
    session_file(provider_home, provider, "startup-title")
    initial_sync = importlib.import_module(f"twicc.providers.{provider.value}.initial_sync")
    output = queue.Queue()
    initial_sync.sync_all(output)
    payloads = [payload for payload in output.queue if isinstance(payload, db_writer.CreateSessionPayload)]
    assert len(payloads) == 1

    async def scenario():
        for payload in payloads:
            await db_writer.run_under_db_write_lock(lambda: db_writer._process_thread_message(payload))

    run_with_writer(scenario)
    assert Session.objects.filter(id="startup-title").exists()
    assert SessionItem.objects.filter(session_id="startup-title").exists()
    assert requests == []
