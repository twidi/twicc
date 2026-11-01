"""Provider shutdown drains owned retirement before closing worker resources."""

import asyncio
import importlib
import logging
import multiprocessing
import queue
import threading
from types import SimpleNamespace

import pytest

from twicc.core.enums import Provider
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.compute_ipc import ComputeResultSender
from twicc.providers.codex.background_compute import CodexComputeCoordinator
from twicc.providers.codex.migration_gate import gate_for


@pytest.fixture(params=['claude_code', 'codex'])
def provider_shutdown(request, monkeypatch):
    name = request.param
    module = importlib.import_module(f'twicc.providers.{name}.orchestrator')
    cls = module.ClaudeCodeOrchestrator if name == 'claude_code' else module.CodexOrchestrator
    owner = cls()

    async def shutdown(**kwargs):
        pass

    manager_name = 'get_claude_code_agent_manager' if name == 'claude_code' else 'get_codex_agent_manager'
    monkeypatch.setattr(module, manager_name, lambda: SimpleNamespace(shutdown=shutdown))
    if name == 'codex':
        monkeypatch.setattr(module, 'get_watcher', lambda: SimpleNamespace(stop_watcher=lambda: None,
            _drain_changes=shutdown))
    return owner


def test_compute_stop_precedes_first_provider_shutdown_await(provider_shutdown, monkeypatch):
    async def run():
        owner = provider_shutdown
        ctx = background.ComputeContext(owner.provider, 1, 'unused:factory')
        owner._compute_ctx = ctx
        entered, release = asyncio.Event(), asyncio.Event()
        resume_recovery = asyncio.Event()
        replacements = []
        monkeypatch.setattr(background, 'start_compute_process', lambda context: replacements.append(context))

        async def recovery():
            await resume_recovery.wait()
            if not ctx.stop_event.is_set():
                background.start_compute_process(ctx)

        owner._compute_task = asyncio.create_task(recovery())

        async def dependency():
            try:
                await asyncio.Future()
            finally:
                entered.set()
                await release.wait()

        owner._orch_task = asyncio.create_task(dependency())
        await asyncio.sleep(0)
        task = asyncio.create_task(owner.shutdown())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert ctx.stop_event.is_set()
            resume_recovery.set()
            await owner._compute_task
            assert not replacements
        finally:
            resume_recovery.set()
            release.set()
            await task
            ctx.command_queue.close()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_provider_shutdown_serializes_join_blocked_retirement(provider_shutdown, monkeypatch):
    async def run():
        owner = provider_shutdown
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(owner.provider, set(), 0)
        ctx = background.ComputeContext(owner.provider, 1, 'unused:factory', run_id=run_id)
        owner._compute_ctx = ctx
        stream = db_writer.create_compute_result_stream(owner.provider, run_id)
        ctx.result_stream = stream
        closed = []
        close = stream.close

        async def close_stream():
            closed.append(run_id)
            await close()

        monkeypatch.setattr(stream, 'close', close_stream)
        entered, duplicate = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        class JoiningProcess:
            pid = 123
            alive = True
            joins = 0
            closures = 0

            def is_alive(self):
                assert self.closures == 0, 'process accessed after closure'
                return self.alive

            def join(self, timeout):
                self.joins += 1
                loop.call_soon_threadsafe(entered.set)
                if self.joins > 1:
                    loop.call_soon_threadsafe(duplicate.set)
                assert release.wait(5), 'test did not release worker join'
                self.alive = False

            def close(self):
                self.closures += 1

        process = JoiningProcess()
        ctx.process = process
        lease = gate_for('owned')
        coordinator = CodexComputeCoordinator(ctx, asyncio.Event()) if owner.provider == Provider.CODEX else None
        if coordinator is not None:
            await lease.acquire()
            coordinator._done_future = completion
            coordinator.run_active = True
            coordinator.migration_leases['owned'] = lease
        replacements = []

        async def producer():
            if coordinator is None:
                await background.retire_compute_run(ctx, completion)
            else:
                await coordinator._retire_run()
            if not ctx.stop_event.is_set():
                replacements.append('replacement')
                background.start_compute_process(ctx)

        monkeypatch.setattr(background, 'start_compute_process', lambda context: replacements.append('spawn'))
        owner._compute_task = asyncio.create_task(producer())
        shutdown = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if coordinator is not None:
                assert lease.locked()
            shutdown = asyncio.create_task(owner.shutdown())
            async with asyncio.timeout(2):
                while not owner._compute_task.cancelling() and not duplicate.is_set() and not shutdown.done():
                    await asyncio.sleep(0)
            if coordinator is not None:
                assert lease.locked()
            owner._compute_task.cancel()
            await asyncio.sleep(0)
            assert not owner._compute_task.done()
            release.set()
            await asyncio.wait_for(shutdown, 2)
            assert process.joins == 1
            assert process.closures == 1
            assert closed == [run_id]
            assert stream.reader.closed and stream.sender.connection.closed
            assert ctx.process is None and ctx.result_stream is None
            assert owner._compute_task.done() and completion.done()
            assert not replacements
            if coordinator is not None:
                assert not lease.locked()
                assert not coordinator.migration_leases
        finally:
            release.set()
            if shutdown is not None:
                await asyncio.gather(shutdown, return_exceptions=True)
            owner._compute_task.cancel()
            await asyncio.gather(owner._compute_task, return_exceptions=True)
            await background.stop_compute_worker(ctx)
            await db_writer.stop_db_writer()
            if lease.locked():
                lease.release()
    asyncio.run(run())


@pytest.fixture
def worker_setup(monkeypatch):
    import django
    import signal
    monkeypatch.setattr(background, '_set_pdeathsig_linux', lambda: None)
    monkeypatch.setattr(signal, 'signal', lambda *args: None)
    monkeypatch.setattr(django, 'setup', lambda: None)
    compute = SimpleNamespace(provider=Provider.CLAUDE_CODE, enable_batch_queue_cache=lambda: None)
    monkeypatch.setattr(background, '_resolve_factory', lambda path: lambda: compute)
    return compute


def test_terminal_done_broken_pipe_closes_sender(worker_setup):
    reader, writer = multiprocessing.get_context('spawn').Pipe(duplex=False)
    reader.close()
    stop = threading.Event()
    stop.set()
    background.compute_worker_main(queue.Queue(), ComputeResultSender(writer), None, stop, 'unused:factory', 1)
    assert writer.closed


def test_unexpected_terminal_transport_error_propagates_and_closes_sender(worker_setup):
    class FailedSender:
        closed = False

        def put(self, message):
            raise OSError('unexpected terminal transport failure')

        def close(self):
            self.closed = True

    sender = FailedSender()
    stop = threading.Event()
    stop.set()
    with pytest.raises(OSError, match='unexpected terminal transport failure'):
        background.compute_worker_main(queue.Queue(), sender, None, stop, 'unused:factory', 1)
    assert sender.closed


def test_ordinary_result_broken_pipe_keeps_worker_error_diagnostic(worker_setup, caplog, monkeypatch):
    # ASGI setup can disable existing loggers. Capture the enabled worker
    # logger directly without depending on ancestor logging configuration.
    logger = logging.getLogger(background.__name__)
    monkeypatch.setattr(logger, 'disabled', False)
    monkeypatch.setattr(logger, 'handlers', [caplog.handler])
    caplog.set_level(logging.ERROR, logger=background.__name__)
    reader, writer = multiprocessing.get_context('spawn').Pipe(duplex=False)
    reader.close()
    stop = threading.Event()

    def compute(session_id, sender, run_id):
        try:
            sender.put(b'ordinary result')
        finally:
            stop.set()

    worker_setup.compute_session_metadata = compute
    commands = queue.Queue()
    commands.put({'session_id': 'ordinary'})
    background.compute_worker_main(commands, ComputeResultSender(writer), None, stop, 'unused:factory', 1)
    assert writer.closed
    assert any('Error computing session ordinary' in record.message for record in caplog.records)
    assert any('Unexpected error in compute worker' in record.message for record in caplog.records)


def test_claude_startup_cron_producer_is_cancelled_before_manager_shutdown(monkeypatch, settings):
    from unittest.mock import AsyncMock
    from twicc.agent import AgentState
    from twicc.providers.claude_code import cron_restart, orchestrator
    from twicc.providers.claude_code.agent import manager as manager_module
    settings.CRON_AUTO_RESTART = True

    async def scenario():
        owner = orchestrator.ClaudeCodeOrchestrator()
        preparing = asyncio.Event()
        release, collected = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        sent, boundary = [], []
        manager = SimpleNamespace(_agents={}, _session_is_hybrid=AsyncMock(return_value=False))
        def collect(sid):
            loop.call_soon_threadsafe(preparing.set)
            assert release.wait(timeout=5), 'controlled cron collection release'
            collected.set()
            return {'session_id': sid, 'project_id': 'project', 'cwd': '/isolated',
                    'restored_crons': [], 'crons_to_recreate': []}
        async def send(**kwargs):
            sent.append(kwargs['session_id'])
            ready = asyncio.Event()
            ready.set()
            manager._agents[kwargs['session_id']] = SimpleNamespace(state=AgentState.USER_TURN,
                _first_turn_done_event=ready, _first_user_turn_reached=True)
        async def shutdown():
            # This models the real manager snapshot and the final title drain.
            boundary.append((owner._cron_restart_task.done(), owner._cron_restart_task.cancelled()))
            manager._agents.clear()
            release.set()
            assert await asyncio.to_thread(collected.wait, 5)
            await asyncio.gather(owner._cron_restart_task, return_exceptions=True)
        manager.send_to_session = send
        manager.shutdown = shutdown
        monkeypatch.setattr(cron_restart, '_collect_restart_data', collect)
        monkeypatch.setattr(db_writer, 'submit_async_job', AsyncMock(return_value=['session']))
        monkeypatch.setattr(manager_module, 'get_claude_code_agent_manager', lambda: manager)
        monkeypatch.setattr(orchestrator, 'get_claude_code_agent_manager', lambda: manager)
        stop = asyncio.Event()
        owner._cron_restart_task = asyncio.create_task(cron_restart.restart_all_session_crons(stop))
        try:
            await asyncio.wait_for(preparing.wait(), timeout=5)
            stop.set()
            await owner.shutdown()
            assert boundary == [(True, True)]
            assert sent == []
            assert manager._agents == {}
        finally:
            release.set()
            owner._cron_restart_task.cancel()
            await asyncio.gather(owner._cron_restart_task, return_exceptions=True)
    asyncio.run(scenario())
