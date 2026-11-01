"""Atomic Claude decisions and real publication integration."""
import asyncio
from unittest.mock import Mock

import pytest

from tests.db_write_helpers import run_with_direct_writes
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, TimelineEvent
from twicc.providers.claude_code import title_publication
from twicc.providers.claude_code.title_sync import SourceCursor, TitleSyncCoordinator

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def row(monkeypatch):
    monkeypatch.setattr(title_publication, '_coordinator', TitleSyncCoordinator())
    monkeypatch.setattr(title_publication, '_worker', None)
    project = Project.objects.create(id='decisions', directory='/missing')
    return Session.objects.create(id='decision-session', project=project, provider=Provider.CLAUDE_CODE,
                                  file_path='decisions/decision-session.jsonl', title='Old', title_origin='auto')


def test_acceptance_normalizes_and_same_text_confirmation_has_new_token(row, monkeypatch):
    from twicc.core.services.session_titles import accept_explicit_title
    notify = Mock()
    monkeypatch.setattr(title_publication, 'request_publication', notify)

    async def scenario():
        first = await accept_explicit_title(row.id, '  New\n title  ')
        second = await accept_explicit_title(row.id, first.title)
        assert first.title == 'New title'
        assert first.changed and not second.changed
        assert first.decision.token != second.decision.token
        assert notify.call_count == 2
    run_with_direct_writes(scenario)
    row.refresh_from_db()
    assert (row.title, row.title_origin) == ('New title', 'user')
    assert TimelineEvent.objects.filter(session_id=row.id, kind='title.changed').count() == 1


def test_failed_event_rolls_back_sql_and_decision(row, monkeypatch):
    from twicc.core.services import session_titles
    monkeypatch.setattr(session_titles, 'record_title_change', Mock(side_effect=RuntimeError('rollback')))
    with pytest.raises(RuntimeError, match='rollback'):
        run_with_direct_writes(lambda: session_titles.accept_explicit_title(row.id, 'New'))
    row.refresh_from_db()
    assert row.title == 'Old'
    assert title_publication.get_title_coordinator().current_decision(row.id) is None


def test_inactive_completion_is_decision_checked():
    c = TitleSyncCoordinator()
    cursor = SourceCursor('s', 'i', 0, 3)
    with c.staged_state() as stage:
        c.protect_acceptance_boundary(cursor)
        old = c.accept_decision('s', 'Old')
        current = c.accept_decision('s', 'New')
        assert not c.complete_inactive(old, cursor)
        assert c.current_decision('s') == current
        assert c.complete_inactive(current, cursor)
        stage.commit()
    assert c.current_decision('s') is None
    assert not c.manages('s')


def test_sdk_ready_dispatches_before_initial_transcript(row, monkeypatch):
    from types import SimpleNamespace
    from twicc.agent import AgentState
    from twicc.core.services.session_titles import accept_explicit_title
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    sent = []

    async def rename(client, sid, title, **kwargs):
        sent.append(title)
    monkeypatch.setattr(title_publication, 'rename_running_session', rename)

    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, state=AgentState.STARTING,
                                _client=object(), _title_process_token='p', _title_ready=False)
        manager._agents[row.id] = agent
        await manager._attach_title_process(agent)
        await accept_explicit_title(row.id, 'Chosen')
        assert sent == []
        await manager._title_process_ready(agent)
        assert sent == ['Chosen']
        assert worker.coordinator.current_decision(row.id).title == 'Chosen'
        await worker.close_publications()
    run_with_direct_writes(scenario)


@pytest.mark.parametrize('length', [150, 200])
def test_hybrid_launch_keeps_full_title(row, length):
    from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
    from twicc.pending_titles import set_pending_title, pop_pending_title
    set_pending_title(row.id, 'T' * length)
    try:
        from types import SimpleNamespace
        assert asyncio.run(HybridClaudeAgent._resolve_temp_title(SimpleNamespace(session_id=row.id), 'prompt')) == 'T' * length
    finally:
        pop_pending_title(row.id)


@pytest.mark.parametrize('writer', ['rest', 'cli', 'pending', 'automatic'])
def test_every_writer_fences_complete_uningested_old_commands(row, monkeypatch, tmp_path, writer):
    from types import SimpleNamespace
    from django.test import RequestFactory
    from django.utils import timezone
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.claude_code.title_sync import TitleObservation
    from twicc.pending_titles import set_pending_title
    from twicc import views
    from twicc.core.services import session_update, title_automation
    from twicc.title_cadence import TitleCheckState

    monkeypatch.setattr('twicc.provider_homes.claude_projects_dir', lambda: tmp_path)
    monkeypatch.setattr(views, 'ensure_provider_running', lambda *args: None)
    monkeypatch.setattr(session_update, 'ensure_provider_running', lambda *args: None)
    path = tmp_path / row.file_path
    path.parent.mkdir()
    path.write_bytes(b'{"type":"custom-title"}\n\n{"type":"user"}\n{"incomplete":')

    async def scenario():
        manager = ClaudeCodeAgentManager()
        await manager._title_lane(lambda: title_publication.attach_claude_process_locked(row.id, 'p', hybrid=False))
        if writer == 'rest':
            response = await views.session_detail(RequestFactory().patch('/', data={'title': 'Chosen'},
                    content_type='application/json'), row.project_id, row.id)
            assert response.status_code == 200
        elif writer == 'cli':
            assert (await session_update.update_session_title_from_payload({'session_id': row.id, 'title': 'Chosen'})).success
        elif writer == 'pending':
            set_pending_title(row.id, 'Chosen')
            await manager._try_flush_pending_title(SimpleNamespace(session_id=row.id, provider=Provider.CLAUDE_CODE), 'Chosen')
        else:
            from twicc.agent.states import AgentState
            manager._agents[row.id] = SimpleNamespace(state=AgentState.USER_TURN, ephemeral=False)
            monkeypatch.setattr('twicc.agent.registry.get_agent_manager_registry',
                                lambda: SimpleNamespace(get=lambda provider: manager))
            snapshot = await Session.objects.aget(pk=row.id)
            result = await title_automation._apply_check(snapshot, TitleCheckState(1, timezone.now()), title='Chosen',
                                                      now=timezone.now(), closing=False, succeeded=True)
            assert result == 'changed'
        decision = title_publication.get_title_coordinator().current_decision(row.id)
        assert decision.title == 'Chosen'
        stored = await Session.objects.aget(pk=row.id)
        # Two nonempty complete records exist, although DB last_line is zero.
        late = TitleObservation(SourceCursor(row.id, stored.storage_incarnation, stored.history_epoch, 2),
                                row.id, 'command_success', 'Old', None, 'old-command')
        await manager._title_lane(lambda: title_publication.apply_claude_observations_locked((late,)))
        assert (await Session.objects.aget(pk=row.id)).title == 'Chosen'
        assert title_publication.get_title_coordinator().current_decision(row.id) == decision
    run_with_direct_writes(scenario)


@pytest.mark.parametrize('writer', ['rest', 'cli'])
def test_codex_rename_never_prepares_claude_state(row, monkeypatch, writer):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from django.test import RequestFactory
    from twicc import views
    from twicc.core.services import session_titles, session_update
    from twicc.providers.codex.helpers import CodexHelpers
    Session.objects.filter(pk=row.pk).update(provider=Provider.CODEX)
    ingest = AsyncMock(side_effect=AssertionError('Codex must not enter Claude ingestion'))
    monkeypatch.setattr(title_publication, '_worker', SimpleNamespace(_closed=False,
                        hooks=SimpleNamespace(ingest_available=ingest)))
    monkeypatch.setattr(session_titles, 'capture_title_boundary', AsyncMock(side_effect=AssertionError('Claude capture')))
    monkeypatch.setattr(views, 'ensure_provider_running', lambda *args: None)
    monkeypatch.setattr(session_update, 'ensure_provider_running', lambda *args: None)
    push = AsyncMock()
    monkeypatch.setattr(CodexHelpers, 'rename_session', push)
    async def scenario():
        if writer == 'rest':
            response = await views.session_detail(RequestFactory().patch('/', data={'title': 'Codex choice'},
                content_type='application/json'), row.project_id, row.id)
            assert response.status_code == 200
        else:
            assert (await session_update.update_session_title_from_payload({'session_id': row.id, 'title': 'Codex choice'})).success
    run_with_direct_writes(scenario)
    row.refresh_from_db()
    assert (row.title, row.title_origin, row.storage_incarnation) == ('Codex choice', 'user', None)
    ingest.assert_not_called()
    push.assert_awaited_once_with(row.id, 'Codex choice')


def test_boundary_scanner_uses_bounded_chunks_for_large_records(row, monkeypatch, tmp_path):
    from pathlib import Path
    from twicc.core.services.session_titles import capture_title_boundary
    Session.objects.filter(pk=row.pk).update(storage_incarnation='storage')
    monkeypatch.setattr('twicc.provider_homes.claude_projects_dir', lambda: tmp_path)
    path = tmp_path / row.file_path
    path.parent.mkdir()
    path.write_bytes(b' ' * 65537 + b'X' * 65537 + b'\n\t\n' + b'Z' * 150000)
    original_open = Path.open
    reads = []
    class BoundedStream:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def readline(self, *args):
            raise AssertionError('Record-sized readline is forbidden')
        def read(self, size):
            assert 0 < size <= 65536
            reads.append(size)
            return self.stream.read(size)
    def bounded_open(current, *args, **kwargs):
        stream = original_open(current, *args, **kwargs)
        return BoundedStream(stream) if current == path else stream
    monkeypatch.setattr(Path, 'open', bounded_open)
    boundary = run_with_direct_writes(lambda: capture_title_boundary(row.id))
    assert boundary.line_num == 1
    assert len(reads) > 1
