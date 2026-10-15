"""Actual work gates and thread-safe source wakeups."""

import asyncio


def test_pending_changes_only_signal_real_transitions():
    from twicc.providers.compute_work import ComputeWorkState

    async def run():
        state = ComputeWorkState()
        assert not state.has_pending()
        state.set_pending('claude_code', True)
        assert state.has_pending() and state.changed.is_set()
        state.changed.clear()
        state.set_pending('claude_code', True)
        assert not state.changed.is_set()
        state.set_pending('codex', True)
        state.set_pending('claude_code', False)
        assert state.has_pending()
        state.set_pending('codex', False)
        assert not state.has_pending()
    asyncio.run(run())


def test_thread_notification_does_not_reach_removed_or_replaced_callback():
    from twicc.providers.compute_work import ComputeWorkState

    async def run():
        state = ComputeWorkState()
        received = []
        old = lambda sid: received.append(('old', sid))
        new = lambda sid: received.append(('new', sid))
        state.register_source_wakeup('claude_code', old)
        await asyncio.to_thread(state.notify_source_changed, 'claude_code', 'a')
        await asyncio.sleep(0)
        assert received == [('old', 'a')]
        state.notify_source_changed('claude_code', 'late')
        state.register_source_wakeup('claude_code', new)
        state.unregister_source_wakeup('claude_code', old)
        await asyncio.sleep(0)
        assert received == [('old', 'a')]
        state.notify_source_changed('claude_code', 'b')
        await asyncio.sleep(0)
        assert received[-1] == ('new', 'b')
        state.unregister_source_wakeup('claude_code', new)
        state.notify_source_changed('claude_code', 'ignored')
        await asyncio.sleep(0)
        assert len(received) == 2
    asyncio.run(run())


def test_provider_gate_starts_before_admission_and_ends_after_teardown(monkeypatch):
    from types import SimpleNamespace
    from twicc import orchestrator
    from twicc.core.enums import Provider
    from twicc.providers.compute_work import ComputeWorkState

    async def run():
        state = ComputeWorkState()
        entered, release = asyncio.Event(), asyncio.Event()
        async def shutdown():
            entered.set()
            await release.wait()
        async def set_provider_state(*args):
            pass
        monkeypatch.setattr(orchestrator, 'set_provider_state', set_provider_state)
        registry = orchestrator.OrchestratorRegistry.__new__(orchestrator.OrchestratorRegistry)
        registry.compute_work = state
        registry._shutdown_event = asyncio.Event()
        registry._search_index_ready = asyncio.Event()
        registry._orchestrators = {Provider.CLAUDE_CODE: SimpleNamespace(shutdown=shutdown)}
        await registry.begin_start(Provider.CLAUDE_CODE)
        assert state.has_pending()
        task = asyncio.create_task(registry.finish_shutdown(Provider.CLAUDE_CODE))
        await entered.wait()
        assert state.has_pending()
        release.set()
        await task
        assert not state.has_pending()
    asyncio.run(run())


def test_failed_start_gate_clears_only_after_successful_cleanup(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from twicc import orchestrator
    from twicc.core.enums import Provider
    from twicc.providers.compute_work import ComputeWorkState

    async def scenario(cleanup_fails):
        entered, release = asyncio.Event(), asyncio.Event()
        async def start(*args):
            raise ValueError('startup failure')
        async def shutdown():
            entered.set()
            await release.wait()
            if cleanup_fails:
                raise RuntimeError('drain failure')
        async def ignore(*args):
            pass
        monkeypatch.setattr(orchestrator, 'set_provider_state', ignore)
        monkeypatch.setattr(orchestrator, 'force_disable_after_failed_start', ignore)
        registry = orchestrator.OrchestratorRegistry.__new__(orchestrator.OrchestratorRegistry)
        registry.compute_work = ComputeWorkState()
        registry._shutdown_event = asyncio.Event()
        registry._search_index_ready = asyncio.Event()
        registry._orchestrators = {Provider.CLAUDE_CODE: SimpleNamespace(start=start, shutdown=shutdown)}
        await registry.begin_start(Provider.CLAUDE_CODE)
        task = asyncio.create_task(registry.finish_start(Provider.CLAUDE_CODE))
        try:
            await asyncio.wait_for(entered.wait(), .5)
            assert registry.compute_work.has_pending()
            release.set()
            with pytest.raises(ValueError, match='startup failure'):
                await task
            assert registry.compute_work.has_pending() is cleanup_fails
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    for cleanup_fails in (False, True):
        asyncio.run(scenario(cleanup_fails))


def test_unexpected_coordinator_exit_retains_actual_work_gate(monkeypatch):
    import pytest
    from twicc.core.enums import Provider
    from twicc.providers import background_compute_task as background
    from twicc.providers.compute_work import ComputeWorkState

    async def fail(*args):
        raise RuntimeError('unexpected startup failure')
    monkeypatch.setattr(background, '_run_background_compute', fail)

    async def run():
        state = ComputeWorkState()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'unused:factory')
        with pytest.raises(RuntimeError, match='unexpected startup failure'):
            await background.start_background_compute_task(ctx, work_state=state)
        assert state.has_pending()
        assert not state._source_wakeups
    asyncio.run(run())


def test_cancellation_releases_gate_only_after_owned_drain(monkeypatch):
    import pytest
    from twicc.core.enums import Provider
    from twicc.providers import background_compute_task as background
    from twicc.providers.compute_work import ComputeWorkState

    async def run():
        started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def coordinator(*args):
            started.set()
            await asyncio.Event().wait()
        async def retire(ctx):
            draining.set()
            await release.wait()
        monkeypatch.setattr(background, '_run_background_compute', coordinator)
        monkeypatch.setattr(background, 'retire_compute_run', retire)
        state = ComputeWorkState()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'unused:factory')
        task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=state))
        await started.wait()
        task.cancel()
        await draining.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert state.has_pending()
        assert state._source_wakeups
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not state.has_pending()
        assert not state._source_wakeups
    asyncio.run(run())
