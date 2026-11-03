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


def test_failed_real_watcher_drain_never_allows_inactive_append(context, monkeypatch):
    row, path = context
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.sessions_watcher import SessionChangeResult
    watcher = ClaudeCodeSessionsWatcher()
    watcher.projects_dir = path.parent.parent
    monkeypatch.setattr(sessions_watcher, 'get_watcher', lambda: watcher)
    path.write_bytes(orjson.dumps({'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'Manual'}) + b'\n')
    async def failed_change(*args):
        return SessionChangeResult('failed', watcher._queue.source_generation(path), 0)
    monkeypatch.setattr(watcher, '_process_change', failed_change)
    writes = []
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: writes.append(args))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        await manager._title_lane(lambda: worker.coordinator.accept_decision(row.id, 'Pending'))
        try:
            with pytest.raises(RuntimeError, match='incomplete'):
                async with worker.process_publication_reservation(row.id):
                    await manager._reconcile_inactive_title(row.id)
            assert writes == []
            stored = await Session.objects.aget(pk=row.id)
            assert (stored.last_line, stored.storage_incarnation) == (0, 'storage')
        finally:
            await worker.close_publications()
            await watcher._drain_changes()
    run_with_writer(scenario)


@pytest.mark.parametrize('possibly_running', [False, True])
def test_registration_failure_unwinds_only_proven_prelaunch_attachment(context, monkeypatch, possibly_running):
    row, _ = context
    from twicc.agent import BaseAgentManager
    from twicc.core.models import ProcessRun
    original_registration = BaseAgentManager._register_and_start
    def fail_process_run(**kwargs):
        raise RuntimeError('ProcessRun registration fails')
    monkeypatch.setattr(ProcessRun.objects, 'create', fail_process_run)
    calls = []
    async def base_registration(self, agent, *args, **kwargs):
        calls.append(agent)
        if len(calls) == 1:
            if not possibly_running:
                return await original_registration(self, agent, *args, **kwargs)
            self._agents[agent.session_id] = agent
            agent._title_launch_entered = True
            self._agents.pop(agent.session_id)
            raise RuntimeError('ProcessRun registration fails')
    monkeypatch.setattr(BaseAgentManager, '_register_and_start', base_registration)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        from tests.agent_helpers import InitializedAgent

        agent = InitializedAgent(row.id, row.project_id, provider=Provider.CLAUDE_CODE)
        try:
            with pytest.raises(RuntimeError, match='registration fails'):
                await manager._register_and_start(agent, 'prompt', resume=True)
            if possibly_running:
                assert manager._resolve_title_process(row.id).agent is agent
                assert worker.coordinator.process_token(row.id) == agent._title_process_token
            else:
                assert manager._resolve_title_process(row.id) is None
                assert worker.coordinator.process_token(row.id) is None
                successor = SimpleNamespace(session_id=row.id, ephemeral=False)
                await manager._register_and_start(successor, 'prompt', resume=True)
                assert calls == [agent, successor]
                assert manager._resolve_title_process(row.id).agent is successor
        finally:
            await worker.close_publications()
    run_with_direct_writes(scenario)


def test_active_acceptance_and_dispatch_allow_suffix_appended_after_finite_drain(context, monkeypatch):
    row, path = context
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    watcher = ClaudeCodeSessionsWatcher()
    watcher.projects_dir = path.parent.parent
    monkeypatch.setattr(sessions_watcher, 'get_watcher', lambda: watcher)
    old_title = orjson.dumps({'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'Old'}) + b'\n'
    path.write_bytes(old_title)
    original_process = watcher.process_path
    append_after_drain = False
    async def drain_then_append(current):
        await original_process(current)
        # An already-committed target can return before its enqueued no-op
        # consumer turn ends. Finish that turn before the controlled append.
        await watcher._drain_changes()
        if append_after_drain:
            # This complete record is later than the admitted finite target.
            # It exists before the caller's next capture, but does not belong
            # to the just-completed watcher drain.
            with path.open('ab') as stream:
                stream.write(old_title)
    monkeypatch.setattr(watcher, 'process_path', drain_then_append)
    async def scenario():
        nonlocal append_after_drain
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, _title_process_token='p', _title_ready=False,
                                state=AgentState.STARTING, _client=object())
        try:
            await manager._attach_title_process(agent)
            append_after_drain = True
            accepted = await accept_explicit_title(row.id, 'Chosen')
            stored = await Session.objects.aget(pk=row.id)
            assert (stored.title, stored.last_line) == ('Chosen', 1)
            assert path.read_bytes().count(b'custom-title') == 2
            # The worker remains parked until readiness. Exercise its actual
            # dispatch hook with a second controlled post-drain append.
            agent._title_ready = True
            assert await manager._prepare_title_dispatch(accepted.decision, manager._resolve_title_process(row.id))
            stored = await Session.objects.aget(pk=row.id)
            assert (stored.title, stored.last_line) == ('Chosen', 2)
            assert path.read_bytes().count(b'custom-title') == 3
            assert worker.coordinator.current_decision(row.id) == accepted.decision
            await manager._cancel_title_dispatch(accepted.decision)
        finally:
            await worker.close_publications()
            await watcher._drain_changes()
    run_with_writer(scenario)


def test_attachment_baseline_includes_newer_title_committed_during_finite_drain(context, monkeypatch):
    row, path = context
    from twicc.core.content_storage import readers
    from twicc.providers import db_writer
    original_items = readers.iter_current_items
    snapshots = []
    def admitted_items(*args, **kwargs):
        lease = db_writer._db_write_lock_lease.get()
        assert lease is not None and lease.active
        snapshots.append(True)
        yield from original_items(*args, **kwargs)
    monkeypatch.setattr(readers, 'iter_current_items', admitted_items)
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    watcher = ClaudeCodeSessionsWatcher()
    watcher.projects_dir = path.parent.parent
    monkeypatch.setattr(sessions_watcher, 'get_watcher', lambda: watcher)
    first = {'type': 'custom-title', 'sessionId': row.id, 'customTitle': 'Old'}
    imported = first | {'customTitle': 'Imported during drain'}
    path.write_bytes(orjson.dumps(first) + b'\n')
    original_process = watcher.process_path
    appended = False
    async def append_before_admission(current):
        nonlocal appended
        if not appended:
            appended = True
            # The caller captures one complete record. Watcher admission then
            # includes a newer real provider title and commits both records.
            with path.open('ab') as stream:
                stream.write(orjson.dumps(imported) + b'\n')
        await original_process(current)
    monkeypatch.setattr(watcher, 'process_path', append_before_admission)
    sent = []
    async def acknowledged_noop(client, sid, title, **kwargs):
        sent.append(title)
        # The CLI already has this value. A real no-op adds no title record.
    monkeypatch.setattr(title_publication, 'rename_running_session', acknowledged_noop)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        agent = SimpleNamespace(session_id=row.id, _title_process_token='p', _title_ready=False,
                                state=AgentState.STARTING, _client=object())
        try:
            await manager._attach_title_process(agent)
            stored = await Session.objects.aget(pk=row.id)
            assert (stored.last_line, stored.title) == (2, 'Imported during drain')
            assert snapshots
            await manager._title_process_ready(agent)
            assert sent == ['Imported during drain']
            assert worker.coordinator.current_decision(row.id) is None
            assert path.read_bytes().count(b'custom-title') == 2
        finally:
            await worker.close_publications()
            await watcher._drain_changes()
    run_with_writer(scenario)
