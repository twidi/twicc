"""Real title lifecycle hooks keep final provider history authoritative."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from tests.db_write_helpers import run_with_direct_writes, run_with_writer
from twicc.agent import AgentState
from twicc.core.enums import Provider
from twicc.core.models import Project, Session
from twicc.core.services.session_titles import accept_explicit_title
from twicc.providers.claude_code import title_publication, titles
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.claude_code.title_sync import SourceCursor, TitleObservation, TitleSyncCoordinator

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.setattr(title_publication, '_coordinator', TitleSyncCoordinator())
    monkeypatch.setattr(title_publication, '_worker', None)
    monkeypatch.setattr('twicc.provider_homes.claude_projects_dir', lambda: tmp_path)
    project = Project.objects.create(id='lifecycle', directory=str(tmp_path))
    row = Session.objects.create(id='lifecycle-session', project=project, provider=Provider.CLAUDE_CODE,
                                 file_path='lifecycle/lifecycle-session.jsonl', title='Old', title_origin='user',
                                 storage_incarnation='storage')
    path = tmp_path / row.file_path
    path.parent.mkdir()
    return row, path


def record(sid, line, title, kind='title'):
    return TitleObservation(SourceCursor(sid, 'storage', 0, line), sid, kind, title, None, None)


def test_failed_inactive_write_abandons_exact_decision_and_allows_manual_import(context, monkeypatch):
    row, _ = context
    writes = []
    def fail(sid, title):
        writes.append(title)
        raise OSError('unavailable transcript')
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', fail)

    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        await accept_explicit_title(row.id, 'Chosen')
        assert await worker.flush_publications(row.id, timeout=2)
        assert writes == ['Chosen']
        assert worker.coordinator.current_decision(row.id) is None
        await manager._title_lane(lambda: title_publication.apply_claude_observations_locked((record(row.id, 1, 'Manual'),)))
        assert (await Session.objects.aget(pk=row.id)).title == 'Manual'
        await worker.close_publications()
    run_with_direct_writes(scenario)


def test_final_manual_outcome_drains_before_process_retirement(context, monkeypatch):
    row, _ = context
    writes = []
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: writes.append(args))

    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, _title_process_token='process', _title_ready=False,
                                state=AgentState.STARTING)
        await manager._attach_title_process(agent)
        await accept_explicit_title(row.id, 'Chosen')
        async def drain(sid):
            assert worker.coordinator.process_token(sid) == 'process'
            await manager._title_lane(lambda: title_publication.apply_claude_observations_locked((
                record(sid, 1, 'Chosen'), record(sid, 2, 'Old', 'command_success'),
            )))
            await Session.objects.filter(pk=sid).aupdate(last_line=2)
            return SourceCursor(sid, 'storage', 0, 2)
        monkeypatch.setattr(manager, '_ingest_title_available', drain)
        # Hooks retain their original bound methods; the finish hook delegates
        # its final drain through the current method above.
        await worker.finish_process(row.id, 'process')
        assert worker.coordinator.process_token(row.id) is None
        assert worker.coordinator.current_decision(row.id) is None
        assert manager._resolve_title_process(row.id) is None
        assert (await Session.objects.aget(pk=row.id)).title == 'Old'
        assert writes == []
        await worker.close_publications()
    run_with_direct_writes(scenario)


def test_inactive_write_reservation_blocks_process_attachment(context, monkeypatch):
    row, _ = context
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, release, acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        def write(sid, title):
            # Use a real executor barrier; the loop remains free for launch.
            worker._loop.call_soon_threadsafe(entered.set)
            future = asyncio.run_coroutine_threadsafe(release.wait(), worker._loop)
            future.result(timeout=3)
        monkeypatch.setattr(titles, 'rename_session_in_jsonl', write)
        async def launch():
            async with worker.process_publication_reservation(row.id):
                acquired.set()
        await accept_explicit_title(row.id, 'Chosen')
        await entered.wait()
        task = asyncio.create_task(launch())
        await asyncio.sleep(0)
        assert not acquired.is_set()
        release.set()
        await task
        assert acquired.is_set()
        await worker.flush_publications(row.id, timeout=2)
        await worker.close_publications()
    run_with_direct_writes(scenario)


def test_final_drain_failure_retains_process_and_never_appends(context, monkeypatch):
    row, _ = context
    writes = []
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: writes.append(args))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, _title_process_token='p', _title_ready=False,
                                state=AgentState.STARTING)
        await manager._attach_title_process(agent)
        await accept_explicit_title(row.id, 'Chosen')
        monkeypatch.setattr(manager, '_ingest_title_available', AsyncMock(side_effect=OSError('final drain failed')))
        with pytest.raises(OSError):
            await worker.finish_process(row.id, 'p')
        assert worker.coordinator.process_token(row.id) == 'p'
        assert manager._resolve_title_process(row.id).agent is agent
        assert writes == []
        await worker.close_publications()
    run_with_direct_writes(scenario)


def test_process_finish_waits_for_transport_termination_before_final_drain(context, monkeypatch):
    row, _ = context
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, terminated = asyncio.Event(), asyncio.Event()
        async def rename(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                terminated.set()
        monkeypatch.setattr(title_publication, 'rename_running_session', rename)
        agent = SimpleNamespace(session_id=row.id, _title_process_token='p', _title_ready=False,
                                state=AgentState.STARTING, _client=object())
        await manager._attach_title_process(agent)
        await accept_explicit_title(row.id, 'Chosen')
        ready = asyncio.create_task(manager._title_process_ready(agent))
        await entered.wait()
        async def final_drain(sid):
            assert terminated.is_set()
        monkeypatch.setattr(manager, '_ingest_title_available', final_drain)
        await worker.finish_process(row.id, 'p')
        await asyncio.gather(ready, return_exceptions=True)
        assert terminated.is_set()
        await worker.close_publications()
    run_with_direct_writes(scenario)


def test_real_watcher_drains_external_history_before_attachment_and_final_manual_suffix(context, monkeypatch):
    row, path = context
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.claude_code import sessions_watcher
    watcher = ClaudeCodeSessionsWatcher()
    watcher.projects_dir = path.parent.parent
    monkeypatch.setattr(sessions_watcher, 'get_watcher', lambda: watcher)
    Session.objects.filter(pk=row.id).update(storage_incarnation=None)
    initial = {'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'External'}
    path.write_bytes(orjson.dumps(initial) + b'\n')
    sent = []
    async def rename(client, sid, title, **kwargs):
        sent.append(title)
    monkeypatch.setattr(title_publication, 'rename_running_session', rename)

    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, _title_process_token='p', _title_ready=False,
                                state=AgentState.STARTING, _client=object())
        await manager._attach_title_process(agent)
        assert (await Session.objects.aget(pk=row.id)).title == 'External'
        await manager._title_process_ready(agent)
        assert sent == ['External']
        # A real, freshly ingested provider baseline permits ACK no-op confirmation.
        assert worker.coordinator.current_decision(row.id) is None
        agent._title_ready = False
        await accept_explicit_title(row.id, 'Pending')
        suffix = [
            {'type': 'user', 'uuid': 'manual', 'message': {'role': 'user', 'content':
                '<command-name>/rename</command-name><command-args>Old</command-args>'}},
            {'type': 'user', 'uuid': 'success', 'parentUuid': 'manual', 'message': {'role': 'user', 'content':
                '<local-command-stdout>Session renamed to: Old</local-command-stdout>'}},
            {'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'Old'},
        ]
        with path.open('ab') as stream:
            stream.write(b''.join(orjson.dumps(line) + b'\n' for line in suffix))
        await worker.finish_process(row.id, 'p')
        assert (await Session.objects.aget(pk=row.id)).title == 'Old'
        assert worker.coordinator.current_decision(row.id) is None
        assert worker.coordinator.process_token(row.id) is None
        assert path.read_bytes().count(b'custom-title') == 2
        await worker.close_publications()
        await watcher._drain_changes()
    run_with_writer(scenario)


def test_inactive_write_uses_runtime_provider_home(context, monkeypatch):
    existing, old_path = context
    sid = '00000000-0000-4000-8000-000000000099'
    row = Session.objects.create(id=sid, project=existing.project, provider=Provider.CLAUDE_CODE,
                                 file_path=f'lifecycle/{sid}.jsonl')
    home = old_path.parent.parent / 'configured-home'
    path = home / 'projects' / 'lifecycle' / f'{sid}.jsonl'
    path.parent.mkdir(parents=True)
    monkeypatch.setattr('twicc.provider_homes.provider_env_overlay', lambda: {'CLAUDE_CONFIG_DIR': str(home)})
    path.write_bytes(b'{"type":"system"}\n')
    titles.rename_session_in_jsonl(row.id, 'Chosen')
    records = [orjson.loads(line) for line in path.read_bytes().splitlines()]
    assert records[-1] == {'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'Chosen'}


def test_cancelled_inactive_worker_keeps_reservation_until_file_io_ends(context, monkeypatch):
    row, _ = context
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, release, acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        def write(sid, title):
            worker._loop.call_soon_threadsafe(entered.set)
            asyncio.run_coroutine_threadsafe(release.wait(), worker._loop).result(timeout=3)
        monkeypatch.setattr(titles, 'rename_session_in_jsonl', write)
        async def launch():
            async with worker.process_publication_reservation(row.id):
                acquired.set()
        await accept_explicit_title(row.id, 'Chosen')
        await entered.wait()
        task = worker._tasks[row.id]
        task.cancel()
        launch_task = asyncio.create_task(launch())
        try:
            await asyncio.sleep(0)
            assert not acquired.is_set()
            # A second loop turn lets cancellation reach the file-I/O await.
            await asyncio.sleep(0)
            assert not acquired.is_set()
        finally:
            release.set()
            await asyncio.gather(task, launch_task, return_exceptions=True)
            await worker.close_publications()
    run_with_direct_writes(scenario)


def test_old_process_death_cannot_cancel_successor_publication(context, monkeypatch):
    row, _ = context
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def rename(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(title_publication, 'rename_running_session', rename)
        agent = SimpleNamespace(session_id=row.id, _title_process_token='successor', _title_ready=False,
                                state=AgentState.STARTING, _client=object())
        await manager._attach_title_process(agent)
        await accept_explicit_title(row.id, 'Chosen')
        ready = asyncio.create_task(manager._title_process_ready(agent))
        await entered.wait()
        try:
            await worker.finish_process(row.id, 'old-process')
            assert not cancelled.is_set()
            assert worker.coordinator.process_token(row.id) == 'successor'
        finally:
            await worker.close_publications()
            await asyncio.gather(ready, return_exceptions=True)
    run_with_direct_writes(scenario)
