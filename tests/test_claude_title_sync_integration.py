"""Deterministic publication races through acceptance, storage and lifecycle."""
import asyncio
from types import SimpleNamespace

import orjson
import pytest
from django.db import transaction

from tests.db_write_helpers import run_with_writer
from tests.timeline_helpers import mark_known
from twicc.core.services.session_timeline import record_title_change
from twicc.agent import AgentState, BaseAgentManager
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, TimelineEvent
from twicc.core.services.session_titles import accept_explicit_title, accept_title_locked, with_title_boundary
from twicc.providers.claude_code import sessions_watcher, title_publication as publication, titles
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
from twicc.providers.claude_code.title_sync import SourceCursor, TitleObservation, TitleSyncCoordinator

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(publication, '_coordinator', TitleSyncCoordinator())
    monkeypatch.setattr(publication, '_worker', None)
    monkeypatch.setattr('twicc.provider_homes.claude_projects_dir', lambda: tmp_path)
    project = Project.objects.create(id='integration', directory=str(tmp_path))
    row = Session.objects.create(id='s', project=project, provider=Provider.CLAUDE_CODE,
        file_path='integration/s.jsonl', title='B', title_origin='auto')
    mark_known(row.id, project.id)
    record_title_change(row.id, "B", source="user", old="Initial", old_origin="user")
    path = tmp_path / row.file_path
    path.parent.mkdir()
    watcher = ClaudeCodeSessionsWatcher()
    watcher.projects_dir = tmp_path
    monkeypatch.setattr(sessions_watcher, 'get_watcher', lambda: watcher)
    return row, path, watcher


def append(path, *records):
    with path.open('ab') as stream:
        stream.write(b''.join(orjson.dumps(record) + b'\n' for record in records))


def title(value, sid='s'):
    return {'type': 'custom-title', 'sessionId': sid, 'customTitle': value}


def rename(value, token='manual'):
    return (
        {'type': 'user', 'uuid': token, 'message': {'role': 'user', 'content':
            f'<command-name>/rename</command-name><command-args>{value}</command-args>'}},
        {'type': 'user', 'uuid': f'{token}-result', 'parentUuid': token, 'message': {'role': 'user', 'content':
            f'<local-command-stdout>Session renamed to: {value}</local-command-stdout>'}},
        title(value),
    )


def agent(sid='s', token='p'):
    return SimpleNamespace(session_id=sid, _title_process_token=token, _title_ready=False,
        state=AgentState.STARTING, _client=object(), provider=Provider.CLAUDE_CODE, ephemeral=False)


async def close(worker, watcher):
    await worker.close_publications()
    await watcher._drain_changes()


async def events(manager, sid='s'):
    return await manager._title_lane(lambda: list(TimelineEvent.objects.filter(
        session_id=sid, kind='title.changed').exclude(data__new='B').order_by('id').values_list('data', flat=True)))


def test_accepted_automatic_decision_survives_late_old_read_and_confirms_once(setup, monkeypatch):
    row, path, watcher = setup
    append(path, title('B'))
    sent, appended = [], []
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: appended.append(args))
    async def control(client, sid, value, **kwargs):
        sent.append(value)
        append(path, title(value))
    monkeypatch.setattr(publication, 'rename_running_session', control)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        try:
            await manager._attach_title_process(current)
            stored = await Session.objects.aget(pk=row.id)
            old_read = TitleObservation(SourceCursor(row.id, stored.storage_incarnation, stored.history_epoch, 1),
                row.id, 'title', 'B', None, None)
            async def accept():
                return await accept_title_locked(row.id, 'D', source='auto')
            from twicc.providers.db_writer import run_under_db_write_lock
            accepted = await with_title_boundary(row.id, lambda: run_under_db_write_lock(accept))
            assert accepted.changed
            await manager._title_lane(lambda: publication.apply_claude_observations_locked((old_read,)))
            assert (await Session.objects.aget(pk=row.id)).title == 'D'
            await manager._title_process_ready(current)
            assert worker.coordinator.current_decision(row.id) is None
            assert await events(manager) == [{'old': 'B', 'new': 'D', 'source': 'auto'}]
            assert sent == ['D']
            assert appended == []
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('failure', ['unavailable', 'rejected', 'timeout', 'written-timeout'])
def test_sdk_failures_use_bounded_control_retries_without_file_fallback(setup, monkeypatch, failure):
    row, path, watcher = setup
    append(path, title('B'))
    calls = []
    async def control(client, sid, value, **kwargs):
        from twicc.providers.claude_code.title_control import TitleControlError
        calls.append(value)
        if failure == 'written-timeout' or len(calls) == 3:
            append(path, title(value))
        if len(calls) < 3:
            raise TitleControlError('timeout' if failure == 'written-timeout' else failure, 'controlled failure')
    monkeypatch.setattr(publication, 'rename_running_session', control)
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: pytest.fail('active file fallback'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        try:
            await manager._attach_title_process(current)
            await accept_explicit_title(row.id, 'D')
            await manager._title_process_ready(current)
            assert calls == ['D'] * 3
            assert worker.coordinator.current_decision(row.id) is None
            assert (await Session.objects.aget(pk=row.id)).title == 'D'
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_new_decision_during_timeout_keeps_its_protection_and_dispatches_after_old_ack(setup, monkeypatch):
    row, path, watcher = setup
    append(path, title('B'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []
        async def control(client, sid, value, **kwargs):
            sent.append(value)
            if value == 'C':
                entered.set()
                await release.wait()
                append(path, title('C'))
                raise TimeoutError('write completed before timeout')
            append(path, title(value))
        monkeypatch.setattr(publication, 'rename_running_session', control)
        current = agent()
        ready = None
        try:
            await manager._attach_title_process(current)
            await accept_explicit_title(row.id, 'C')
            ready = asyncio.create_task(manager._title_process_ready(current))
            await asyncio.wait_for(entered.wait(), timeout=5)
            accepted = await accept_explicit_title(row.id, 'D')
            release.set()
            await ready
            assert sent == ['C', 'D']
            assert (await Session.objects.aget(pk=row.id)).title == 'D'
            assert worker.coordinator.current_decision(row.id) is None
            assert accepted.decision.title == 'D'
            assert [event['new'] for event in await events(manager)] == ['C', 'D']
        finally:
            release.set()
            await close(worker, watcher)
            if ready:
                await asyncio.gather(ready, return_exceptions=True)
    run_with_writer(scenario)


def test_pending_create_retry_does_not_reaccept_stale_text(setup):
    row, _, watcher = setup
    from twicc.pending_titles import set_pending_title, pop_pending_title
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        try:
            await manager._attach_title_process(current)
            set_pending_title(row.id, 'Draft')
            await accept_explicit_title(row.id, 'D')
            assert await manager._try_flush_pending_title(current, 'Draft')
            assert (await Session.objects.aget(pk=row.id)).title == 'D'
            assert worker.coordinator.current_decision(row.id).title == 'D'
            assert [event['new'] for event in await events(manager)] == ['D']
        finally:
            pop_pending_title(row.id)
            await close(worker, watcher)
    run_with_writer(scenario)


def test_failed_final_drain_recovers_on_next_normal_launch(setup, monkeypatch):
    row, path, watcher = setup
    append(path, title('B'))
    started = []
    async def start(self, current, *args, **kwargs):
        started.append(current)
    monkeypatch.setattr(BaseAgentManager, '_register_and_start', start)
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: pytest.fail('pending B append'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        old = agent()
        try:
            await manager._attach_title_process(old)
            await accept_explicit_title(row.id, 'Pending')
            append(path, *rename('A'))
            ingest = manager._ingest_title_available
            async def fail(sid):
                raise OSError('one failed final drain')
            monkeypatch.setattr(manager, '_ingest_title_available', fail)
            with pytest.raises(OSError):
                await worker.finish_process(row.id, 'p')
            assert manager._resolve_title_process(row.id).agent is old
            monkeypatch.setattr(manager, '_ingest_title_available', ingest)
            successor = agent(token='unused')
            await manager._register_and_start(successor, 'prompt', resume=True)
            assert started == [successor]
            assert manager._resolve_title_process(row.id).agent is successor
            assert (await Session.objects.aget(pk=row.id)).title == 'A'
            assert row.id not in worker._retiring
            assert path.read_bytes().count(b'custom-title') == 2
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_unmanaged_publication_then_external_old_and_manual_titles_remain_admissible(setup, monkeypatch):
    row, path, watcher = setup
    append(path, title('B'))
    writes = []
    def inactive(sid, value):
        writes.append(value)
        append(path, title(value))
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', inactive)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        try:
            await accept_explicit_title(row.id, 'D')
            assert await worker.flush_publications(row.id, timeout=3)
            append(path, title('A'))
            await manager._ingest_title_available(row.id)
            assert (await Session.objects.aget(pk=row.id)).title == 'A'
            append(path, *rename('C'))
            await manager._ingest_title_available(row.id)
            assert (await Session.objects.aget(pk=row.id)).title == 'C'
            assert writes == ['D']
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_two_sessions_have_independent_provider_io(setup, monkeypatch):
    row, path, watcher = setup
    other = Session.objects.create(id='other', project=row.project, provider=Provider.CLAUDE_CODE,
        file_path='integration/other.jsonl', title='B')
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def control(client, sid, value, **kwargs):
            if sid == row.id:
                entered.set()
                await release.wait()
            else:
                second.set()
        monkeypatch.setattr(publication, 'rename_running_session', control)
        first_ready = None
        try:
            first, next_agent = agent(), agent(other.id, 'p2')
            await manager._attach_title_process(first)
            await manager._attach_title_process(next_agent)
            await accept_explicit_title(row.id, 'D')
            await accept_explicit_title(other.id, 'E')
            first_ready = asyncio.create_task(manager._title_process_ready(first))
            await asyncio.wait_for(entered.wait(), timeout=5)
            await manager._title_process_ready(next_agent)
            assert second.is_set()
            assert not release.is_set()
        finally:
            release.set()
            if first_ready:
                await first_ready
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('snapshot', ['boundary', 'drain'])
def test_boundary_snapshot_waits_for_competing_ingestion_transaction(setup, monkeypatch, snapshot):
    """A publication snapshot cannot read an uncommitted watcher transaction."""
    import threading
    import traceback
    from django.db.backends.utils import CursorWrapper
    from twicc.core.services import session_titles
    from twicc.providers import db_writer

    row, path, watcher = setup
    Session.objects.filter(pk=row.id).update(storage_incarnation='disk')
    append(path, title('B'))
    release = threading.Event()
    original_execute = CursorWrapper.execute
    def diagnostic_execute(self, sql, params=None):
        try:
            return original_execute(self, sql, params)
        except Exception:
            print('FAILED SQL:', sql, params, flush=True)
            print('COMPETING SQL: UPDATE core_session SET title=B; held in title_state_transaction', flush=True)
            traceback.print_exc()
            raise
    monkeypatch.setattr(CursorWrapper, 'execute', diagnostic_execute)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        def ingestion():
            with publication.title_state_transaction():
                Session.objects.filter(pk=row.id).update(title='B')
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(timeout=5), 'test transaction release deadline'
        from twicc.providers.compute_executor import run_compute_sync
        holding = asyncio.create_task(db_writer.run_under_db_write_lock(lambda: run_compute_sync(ingestion)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        original_lane = db_writer.run_under_db_write_lock
        async def snapshot_lane(operation, **kwargs):
            # The snapshot has requested admission. Let the competing writer
            # commit before the snapshot's serialized SQL executes.
            release.set()
            return await original_lane(operation, **kwargs)
        monkeypatch.setattr(session_titles, 'run_under_db_write_lock', snapshot_lane)
        try:
            if snapshot == 'boundary':
                boundary = await session_titles.capture_title_boundary(row.id)
            else:
                title_lane = manager._title_lane
                async def drain_lane(operation):
                    release.set()
                    return await title_lane(operation)
                monkeypatch.setattr(manager, '_title_lane', drain_lane)
                boundary = await manager._ingest_title_available(row.id)
            assert boundary == SourceCursor(row.id, 'disk', 0, 1)
        finally:
            release.set()
            await holding
            await close(worker, watcher)
    run_with_writer(scenario)


def test_inactive_io_blocks_actual_launch_but_releases_database_lane(setup, monkeypatch):
    from contextlib import asynccontextmanager
    row, _, watcher = setup
    started = []
    async def start(self, current, *args, **kwargs):
        started.append(current)
    monkeypatch.setattr(BaseAgentManager, '_register_and_start', start)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        entered, release, requested = asyncio.Event(), asyncio.Event(), asyncio.Event()
        def write(*args):
            worker._loop.call_soon_threadsafe(entered.set)
            asyncio.run_coroutine_threadsafe(release.wait(), worker._loop).result(timeout=5)
        monkeypatch.setattr(titles, 'rename_session_in_jsonl', write)
        reserve = worker.process_publication_reservation
        @asynccontextmanager
        async def observed_reservation(sid):
            if entered.is_set():
                requested.set()
            async with reserve(sid):
                yield
        monkeypatch.setattr(worker, 'process_publication_reservation', observed_reservation)
        launching = None
        try:
            await accept_explicit_title(row.id, 'D')
            await asyncio.wait_for(entered.wait(), timeout=5)
            successor = agent()
            launching = asyncio.create_task(manager._register_and_start(successor, '', resume=True))
            await asyncio.wait_for(requested.wait(), timeout=5)
            assert started == []
            # This commits while transcript I/O remains paused.
            assert await manager._title_lane(lambda: Session.objects.filter(pk=row.id).update(title_check_count=17)) == 1
            assert not release.is_set()
            release.set()
            await launching
            assert started == [successor]
        finally:
            release.set()
            if launching:
                await asyncio.gather(launching, return_exceptions=True)
            await close(worker, watcher)
    run_with_writer(scenario)


def test_launch_before_inactive_reservation_uses_active_control_route(setup, monkeypatch):
    row, path, watcher = setup
    sent = []
    async def control(client, sid, value, **kwargs):
        sent.append(value)
    async def start(self, current, *args, **kwargs):
        pass
    monkeypatch.setattr(BaseAgentManager, '_register_and_start', start)
    monkeypatch.setattr(publication, 'rename_running_session', control)
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: pytest.fail('launch already owns route'))
    async def scenario():
        # A decision precedes manager configuration, so its inactive request
        # cannot acquire the launch reservation before this attachment.
        await accept_explicit_title(row.id, 'D')
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        try:
            current = agent()
            await manager._register_and_start(current, '', resume=True)
            await manager._title_process_ready(current)
            assert sent == ['D']
            assert not path.exists()
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_hybrid_batch_manual_old_title_survives_death_and_full_recompute(setup, monkeypatch):
    import queue
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.compute_executor import run_compute_sync
    row, path, watcher = setup
    append(path, title('B'))
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: pytest.fail('hybrid file fallback'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        current.is_hybrid = True
        current.on_title_evidence = lambda records: None
        pasted = []
        async def paste(value, *, token, before_paste):
            assert await before_paste()
            pasted.append('/rename ' + value)
            append(path, *rename(value, 'own'), *rename('B', 'manual'))
        current.rename = paste
        try:
            await manager._attach_title_process(current)
            await accept_explicit_title(row.id, 'D')
            await manager._title_process_ready(current)
            assert await worker.flush_publications(row.id, timeout=3)
            assert (await Session.objects.aget(pk=row.id)).title == 'B'
            assert worker.coordinator.current_decision(row.id) is None
            await worker.finish_process(row.id, 'p')
            results = queue.Queue()
            await run_compute_sync(get_compute().compute_session_metadata, row.id, results, run_id=0)
            message = next(orjson.loads(raw) for raw in list(results.queue)
                if orjson.loads(raw)['type'] == 'session_complete')
            await manager._title_lane(lambda: BaseSessionCompute.apply_session_complete(message))
            assert (await Session.objects.aget(pk=row.id)).title == 'B'
            assert pasted == ['/rename D']
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_restart_does_not_reconstruct_old_protection_or_publish_old_database_title(setup, monkeypatch):
    row, path, watcher = setup
    append(path, title('B'))
    sent = []
    async def control(client, sid, value, **kwargs):
        sent.append(value)
    monkeypatch.setattr(publication, 'rename_running_session', control)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        first = manager._ensure_title_publications()
        old = agent()
        await manager._attach_title_process(old)
        await accept_explicit_title(row.id, 'D')
        await manager._title_process_ready(old)
        assert first.coordinator.current_decision(row.id).title == 'D'
        await close(first, watcher)
        # Restart discards only process-local coordinator/manager ownership.
        monkeypatch.setattr(publication, '_coordinator', TitleSyncCoordinator())
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        append(path, title('A'))
        try:
            successor = agent(token='p2')
            await manager._attach_title_process(successor)
            await manager._title_process_ready(successor)
            assert (await Session.objects.aget(pk=row.id)).title == 'A'
            assert sent == ['D', 'A']
            assert worker.coordinator.current_decision(row.id) is None
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


def test_large_distinct_evidence_replays_stored_order_with_bounded_memory(setup):
    row, path, watcher = setup
    append(path, title('B'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        try:
            await manager._attach_title_process(current)
            accepted = await accept_explicit_title(row.id, 'D')
            stored = await Session.objects.aget(pk=row.id)
            cursor = SourceCursor(row.id, stored.storage_incarnation, stored.history_epoch, 1)
            def apply():
                assert worker.coordinator.start_dispatch(accepted.decision, cursor)
                repeated = tuple(TitleObservation(cursor._replace(line_num=n), row.id, 'title', 'B', None, None)
                    for n in range(2, 2002))
                commands = [rename(str(n), f'command-{n}')[0] for n in range(10000)]
                raw = [title('B')] * 2000 + commands + [rename('Final', 'command-9999')[1]]
                SessionItem.objects.bulk_create([SessionItem(session_id=row.id, line_num=n,
                    content=orjson.dumps(value).decode()) for n, value in enumerate(raw, 2)], batch_size=128)
                records = repeated + tuple(TitleObservation(cursor._replace(line_num=n), row.id,
                    'rename_command', str(n - 2002), f'command-{n - 2002}', None) for n in range(2002, 12002))
                publication.apply_claude_observations_locked(records)
                assert worker.coordinator.unresolved_evidence_count(row.id) <= 128
                final = TitleObservation(cursor._replace(line_num=12002), row.id,
                    'command_success', 'Final', 'command-9999-result', 'command-9999')
                publication.apply_claude_observations_locked((final,))
                assert worker.coordinator.unresolved_evidence_count(row.id) <= 128
            await manager._title_lane(apply)
            assert (await Session.objects.aget(pk=row.id)).title == 'Final'
            assert worker.coordinator.current_decision(row.id) is None
            assert [event['new'] for event in await events(manager)] == ['D', 'Final']
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('elapsed,expected_attempts', [(10, 3), (30, 1)])
def test_sdk_total_budget_and_request_limit_use_controlled_clock(setup, monkeypatch, elapsed, expected_attempts):
    row, _, watcher = setup
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = agent()
        clock, timeouts = [0.0], []
        real_loop = worker._loop
        class ClockLoop:
            def time(self):
                return clock[0]
            def __getattr__(self, name):
                return getattr(real_loop, name)
        async def control(client, sid, value, *, timeout):
            timeouts.append(timeout)
            clock[0] += elapsed
            raise TimeoutError('controlled deadline')
        monkeypatch.setattr(publication, 'rename_running_session', control)
        try:
            await manager._attach_title_process(current)
            current._title_ready = True
            monkeypatch.setattr(worker, '_loop', ClockLoop())
            def on_owner_loop():
                try:
                    return asyncio.get_running_loop() is real_loop
                except RuntimeError:
                    return False
            monkeypatch.setattr(worker, '_on_owner_loop', on_owner_loop)
            await accept_explicit_title(row.id, 'D')
            await worker._ingress_barrier()
            task = worker._tasks.get(row.id)
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            assert timeouts == [10.0] * expected_attempts
            assert worker.SDK_TOTAL_BUDGET == 30.0
            assert worker.coordinator.current_decision(row.id).title == 'D'
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize('composer_ready', [False, True])
async def test_hybrid_composer_and_confirmation_budgets_never_repaste(monkeypatch, composer_ready):
    from twicc.providers.claude_code.agent.hybrid import agent as hybrid
    from twicc.providers.claude_code.title_control import TitleControlError
    from twicc.providers.helpers import AgentSettings
    current = hybrid.HybridClaudeAgent('s', 'integration', '/tmp', AgentSettings())
    current.state = AgentState.USER_TURN
    clock, checked, waits = [0.0], [], []
    monkeypatch.setattr(hybrid, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    async def paste(command, **kwargs):
        checked.append(command)
        clock[0] += 300
        return composer_ready
    async def confirmation(future, *, timeout):
        waits.append(timeout)
        raise TimeoutError('controlled confirmation deadline')
    monkeypatch.setattr(current, '_checked_paste', paste)
    original_asyncio = hybrid.asyncio
    class AsyncioProxy:
        wait_for = staticmethod(confirmation)
        def __getattr__(self, name):
            return getattr(original_asyncio, name)
    monkeypatch.setattr(hybrid, 'asyncio', AsyncioProxy())
    with pytest.raises(TimeoutError if composer_ready else TitleControlError):
        await current.rename('D', token='decision')
    assert current.AUTO_PASTE_RETRY_TIMEOUT == 300.0
    assert checked == ['/rename D']
    assert waits == ([10.0] if composer_ready else [])


@pytest.mark.parametrize('replacement', ['epoch', 'incarnation'])
def test_replaced_source_rejects_old_ack_and_keeps_unrelated_metadata(setup, replacement):
    from twicc.providers.compute_base import BaseSessionCompute
    row, path, watcher = setup
    append(path, title('B'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        try:
            await manager._attach_title_process(agent())
            accepted = await accept_explicit_title(row.id, 'D')
            stored = await Session.objects.aget(pk=row.id)
            old = SourceCursor(row.id, stored.storage_incarnation, stored.history_epoch, 1)
            await manager._title_lane(lambda: worker.coordinator.start_dispatch(accepted.decision, old))
            def replace():
                updates = {'history_epoch': 1} if replacement == 'epoch' else {'storage_incarnation': 'replacement'}
                Session.objects.filter(pk=row.id).update(**updates)
                current = Session.objects.get(pk=row.id)
                fresh = TitleObservation(SourceCursor(row.id, current.storage_incarnation, current.history_epoch, 2),
                    row.id, 'title', 'E', None, None)
                publication.apply_claude_observations_locked((fresh,))
                publication.acknowledge_claude_title_locked(accepted.decision, True, old)
                BaseSessionCompute.apply_session_complete({'session_id': row.id, 'history_facts': [],
                    'session_fields': {'title_check_count': 23}})
            await manager._title_lane(replace)
            current = await Session.objects.aget(pk=row.id)
            assert (current.title, current.title_check_count) == ('E', 23)
            assert worker.coordinator.current_decision(row.id) is None
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('rollback_at', ['observation', 'ack'])
def test_sql_rollback_replays_manual_title_event_exactly_once(setup, rollback_at):
    row, path, watcher = setup
    append(path, title('B'))
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        try:
            await manager._attach_title_process(agent())
            accepted = await accept_explicit_title(row.id, 'D')
            stored = await Session.objects.aget(pk=row.id)
            cursor = SourceCursor(row.id, stored.storage_incarnation, stored.history_epoch, 1)
            await manager._title_lane(lambda: worker.coordinator.start_dispatch(accepted.decision, cursor))
            echo = TitleObservation(cursor._replace(line_num=2), row.id, 'title', 'D', None, None)
            manual = TitleObservation(cursor._replace(line_num=3), row.id, 'title', 'A', None, None)
            def apply():
                publication.apply_claude_observations_locked((echo, manual))
                publication.acknowledge_claude_title_locked(accepted.decision, True, manual.cursor)
            if rollback_at == 'ack':
                await manager._title_lane(lambda: publication.apply_claude_observations_locked((echo, manual)))
            def failing():
                if rollback_at == 'ack':
                    publication.acknowledge_claude_title_locked(accepted.decision, True, manual.cursor)
                else:
                    apply()
                transaction.set_rollback(True)
            await manager._title_lane(failing)
            assert (await Session.objects.aget(pk=row.id)).title == 'D'
            assert worker.coordinator.current_decision(row.id) == accepted.decision
            await manager._title_lane(apply)
            await manager._title_lane(apply)
            assert (await Session.objects.aget(pk=row.id)).title == 'A'
            assert [event['new'] for event in await events(manager)] == ['D', 'A']
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('active', [False, True])
def test_actual_orchestrator_shutdown_drains_final_history_and_reconciles_pending(setup, monkeypatch, manual, active):
    from unittest.mock import AsyncMock
    from twicc.providers.claude_code import orchestrator
    from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
    from twicc.providers.helpers import AgentSettings
    row, path, watcher = setup
    append(path, title('B'))
    persisted, dispatched = [], []
    write_entered, write_release = None, None
    def persist(sid, value):
        worker_loop.call_soon_threadsafe(write_entered.set)
        asyncio.run_coroutine_threadsafe(write_release.wait(), worker_loop).result(timeout=5)
        persisted.append(value)
        append(path, title(value))
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', persist)
    entered, cancelled, worker_loop = None, None, None
    async def control(*args, **kwargs):
        dispatched.append(args)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(publication, 'rename_running_session', control)
    async def scenario():
        nonlocal entered, cancelled, write_entered, write_release, worker_loop
        worker_loop = asyncio.get_running_loop()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        write_entered, write_release = asyncio.Event(), asyncio.Event()
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = ClaudeCodeAgent(row.id, row.project_id, str(path.parent), AgentSettings(),
            get_session_slug=AsyncMock(), on_cron_created=AsyncMock(), on_cron_deleted=AsyncMock())
        current._title_process_token = 'process'
        current._title_ready = active
        current._state_change_callback = manager._on_state_change
        current._shutdown_sdk_client = AsyncMock()
        current._client = object()
        current.state = AgentState.USER_TURN
        await manager._attach_title_process(current)
        manager._agents[row.id] = current
        await accept_explicit_title(row.id, 'D')
        if active:
            await asyncio.wait_for(entered.wait(), timeout=5)
        async def stop(reason):
            assert not worker._closed
            assert worker._quiescing
            assert not active or cancelled.is_set()
            if manual:
                append(path, *rename('Final manual'))
            current.kill_reason = reason
            await current._transition_to_dead()
        current.interrupt_or_kill = stop
        monkeypatch.setattr(orchestrator, 'get_claude_code_agent_manager', lambda: manager)
        monkeypatch.setattr(orchestrator, 'get_watcher', lambda: watcher)
        owner = orchestrator.ClaudeCodeOrchestrator()
        watching = asyncio.Event()
        async def watcher_lifetime():
            watching.set()
            await asyncio.Event().wait()
        owner._watcher_task = asyncio.create_task(watcher_lifetime())
        await watching.wait()
        stopping = asyncio.create_task(owner.shutdown())
        try:
            if not manual:
                await asyncio.wait_for(write_entered.wait(), timeout=5)
                assert not stopping.done()
                assert not worker._closed
                # The real ingestion lane stays available while the file write waits.
                await manager._ingest_title_available(row.id)
                write_release.set()
            await stopping
            expected = 'Final manual' if manual else 'D'
            assert persisted == ([] if manual else ['D'])
            assert len(dispatched) == int(active)
            assert (await Session.objects.aget(pk=row.id)).title == expected
            assert worker.coordinator.current_decision(row.id) is None
            assert worker._closed
            # Fresh import has no process-local protection to hide a lost write.
            monkeypatch.setattr(publication, '_coordinator', TitleSyncCoordinator())
            from twicc.providers.compute_base import BaseSessionCompute
            from twicc.providers.compute_executor import run_compute_sync
            import queue
            results = queue.Queue()
            await run_compute_sync(get_compute().compute_session_metadata, row.id, results, run_id=0)
            payload = next(orjson.loads(raw) for raw in list(results.queue)
                if orjson.loads(raw)['type'] == 'session_complete')
            await manager._title_lane(lambda: BaseSessionCompute.apply_session_complete(payload))
            assert (await Session.objects.aget(pk=row.id)).title == expected
        finally:
            write_release.set()
            await asyncio.gather(stopping, return_exceptions=True)
            await close(worker, watcher)
    run_with_writer(scenario)


@pytest.mark.parametrize('legacy', ['Legacy   title', 'L' * 201, 'L' * 250, ' Legacy title ', 'Legacy\ttitle'])
@pytest.mark.parametrize('origin', ['', 'auto', 'user'])
def test_resume_publishes_exact_existing_title_without_reclassification(setup, monkeypatch, legacy, origin):
    from unittest.mock import AsyncMock
    from twicc.providers.claude_code.agent import agent as sdk_agent
    from twicc.providers.helpers import AgentSettings
    from twicc.core.services import trust
    row, path, watcher = setup
    Session.objects.filter(pk=row.id).update(title=legacy, title_origin=origin)
    append(path, title(legacy))
    sent, prompts = [], []
    async def control(client, sid, value, **kwargs):
        sent.append(value)
    monkeypatch.setattr(publication, 'rename_running_session', control)
    async def query(prompt):
        prompts.append(prompt)
    client = SimpleNamespace(connect=AsyncMock(), query=query)
    monkeypatch.setattr(sdk_agent, 'ClaudeSDKClient', lambda **kwargs: client)
    monkeypatch.setattr(sdk_agent, 'patch_client_for_logging', lambda *args: None)
    monkeypatch.setattr(sdk_agent, 'attach_elicitation_handler', lambda *args: None)
    monkeypatch.setattr(trust, 'project_is_untrusted', lambda project: False)
    monkeypatch.setattr('twicc.mcp.mcp_enabled', lambda: False)
    async def scenario():
        manager = ClaudeCodeAgentManager()
        worker = manager._ensure_title_publications()
        current = sdk_agent.ClaudeCodeAgent(row.id, row.project_id, str(path.parent), AgentSettings(),
            AsyncMock(), AsyncMock(), AsyncMock())
        current._title_process_token = 'p'
        current._title_ready = False
        current._on_title_ready = manager._title_process_ready
        current._resolve_and_create_work_dirs = AsyncMock(return_value=[])
        current._build_query_prompt = AsyncMock(return_value='first prompt')
        current._run_message_loop = AsyncMock()
        try:
            await manager._attach_title_process(current)
            before = await manager._title_lane(lambda: TimelineEvent.objects.filter(session_id=row.id, kind='title.changed').count())
            await current.start('first prompt', AsyncMock(), resume=True)
            if current._message_loop_task is not None:
                await current._message_loop_task
            assert current.error is None
            stored = await Session.objects.aget(pk=row.id)
            assert (stored.title, stored.title_origin) == (legacy, origin)
            assert sent == ([legacy] if legacy == 'Legacy   title' else [])
            assert worker.coordinator.current_decision(row.id) is None
            assert prompts == ['first prompt']
            assert await manager._title_lane(lambda: TimelineEvent.objects.filter(session_id=row.id, kind='title.changed').count()) == before
        finally:
            await close(worker, watcher)
    run_with_writer(scenario)
