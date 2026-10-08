"""The title runner owns two workers and retains requests through each check."""

import asyncio
from collections import Counter
from importlib import import_module
from types import SimpleNamespace

import pytest

from tests.test_title_automation import OLD, env, session  # noqa: F401
from twicc.core.models import Session


@pytest.fixture
def runner(monkeypatch):
    module = import_module("twicc.title_auto_task")
    # Keep error-log assertions independent of earlier CLI setup calls.
    monkeypatch.setattr(module.logger, "disabled", False)
    yield module
    # Failed state-reset assertions must not contaminate the next test.
    module._wake = None
    module._queued.clear()
    module._pending.clear()
    module._running.clear()


async def wait(event):
    await asyncio.wait_for(event.wait(), timeout=5)


async def turn():
    """Pass one loop boundary without a real sleep."""
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


async def stop(task, shutdown=None):
    if shutdown is None:
        task.cancel()
    else:
        shutdown.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.CancelledError:
        if shutdown is not None:
            raise


def test_queued_requests_coalesce_and_survive_prestart(runner, monkeypatch):
    calls = []
    for _ in range(100):
        runner.request_title_check("first")
    runner.request_title_check("first", closing=True)
    runner.request_title_check("second")

    async def scenario():
        complete = asyncio.Event()

        async def check(session_id, *, closing):
            calls.append((session_id, closing))
            if session_id == "second":
                complete.set()

        monkeypatch.setattr(runner, "check_session_title", check)
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        try:
            await wait(complete)
            assert calls == [("first", True), ("second", False)]
        finally:
            await stop(task, shutdown)

    asyncio.run(scenario())


def test_running_requests_merge_into_one_later_closing_check(runner, monkeypatch):
    async def scenario():
        entered, release, later = (asyncio.Event() for _ in range(3))
        calls = []

        async def check(session_id, *, closing):
            calls.append((session_id, closing))
            if len(calls) == 1:
                entered.set()
                await release.wait()
            else:
                later.set()

        monkeypatch.setattr(runner, "check_session_title", check)
        task = asyncio.create_task(runner.start_title_auto_task(asyncio.Event()))
        runner.request_title_check("same")
        try:
            await wait(entered)
            owned_tasks = asyncio.all_tasks()
            for _ in range(100):
                runner.request_title_check("same")
            runner.request_title_check("same", closing=True)
            runner.request_title_check("same")
            assert asyncio.all_tasks() == owned_tasks
            await turn()
            assert calls == [("same", False)]
            release.set()
            await wait(later)
            await turn()
            assert calls == [("same", False), ("same", True)]
        finally:
            await stop(task)

    asyncio.run(scenario())


def test_two_workers_limit_global_and_per_session_concurrency(runner, monkeypatch):
    async def scenario():
        two_started, release, done = (asyncio.Event() for _ in range(3))
        active = Counter()
        peak = Counter()
        calls = []
        global_peak = 0

        async def check(session_id, *, closing):
            nonlocal global_peak
            calls.append(session_id)
            active[session_id] += 1
            peak[session_id] = max(peak[session_id], active[session_id])
            global_peak = max(global_peak, sum(active.values()))
            if len(calls) == 2:
                two_started.set()
            try:
                await release.wait()
            finally:
                active[session_id] -= 1
            if len(calls) == 5:
                done.set()

        monkeypatch.setattr(runner, "check_session_title", check)
        for session_id in ("a", "b", "c", "d"):
            runner.request_title_check(session_id)
        task = asyncio.create_task(runner.start_title_auto_task(asyncio.Event()))
        try:
            await wait(two_started)
            runner.request_title_check("a", closing=True)
            await turn()
            assert calls == ["a", "b"]
            release.set()
            await wait(done)
            assert calls == ["a", "b", "c", "d", "a"]
            assert global_peak == 2
            assert dict(peak) == {"a": 1, "b": 1, "c": 1, "d": 1}
        finally:
            await stop(task)

    asyncio.run(scenario())


def test_worker_logs_failure_and_accepts_same_session_again(runner, monkeypatch, caplog):
    async def scenario():
        failed, next_done, retry_done = (asyncio.Event() for _ in range(3))
        calls = []

        async def check(session_id, *, closing):
            calls.append(session_id)
            if len(calls) == 1:
                failed.set()
                raise RuntimeError("check failed")
            (next_done if session_id == "next" else retry_done).set()

        monkeypatch.setattr(runner, "check_session_title", check)
        runner.request_title_check("bad")
        runner.request_title_check("next")
        task = asyncio.create_task(runner.start_title_auto_task(asyncio.Event()))
        try:
            await wait(failed)
            await wait(next_done)
            runner.request_title_check("bad")
            await wait(retry_done)
            assert calls == ["bad", "next", "bad"]
        finally:
            await stop(task)

    asyncio.run(scenario())
    assert "bad" in caplog.text
    assert "check failed" in caplog.text


def test_startup_checks_nothing_and_shutdown_leaves_no_workers(runner, monkeypatch):
    async def scenario():
        calls = []

        async def check(session_id, *, closing):
            calls.append(session_id)

        monkeypatch.setattr(runner, "check_session_title", check)
        before = asyncio.all_tasks()
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        await turn()
        await turn()
        assert calls == []
        await stop(task, shutdown)
        assert asyncio.all_tasks() == before

    # No Django access is allowed: startup must not scan sessions.
    asyncio.run(scenario())


def test_shutdown_overlap_resets_state_before_fresh_event_loop(runner, monkeypatch):
    """Explicit cancellation during cooperative worker cleanup cannot retain requests."""
    async def first():
        entered, cleaning = asyncio.Event(), asyncio.Event()
        calls, finished = [], []
        cleanup_count = 0
        before = asyncio.all_tasks()

        async def check(session_id, *, closing):
            nonlocal cleanup_count
            calls.append(session_id)
            if len(calls) == 2:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_count += 1
                if cleanup_count == 2:
                    cleaning.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.append(session_id)

        monkeypatch.setattr(runner, "check_session_title", check)
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        runner.request_title_check("again")
        runner.request_title_check("blocker")
        await wait(entered)
        runner.request_title_check("again", closing=True)
        runner.request_title_check("old-queued", closing=True)
        shutdown.set()
        await wait(cleaning)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert sorted(finished) == ["again", "blocker"]
        assert asyncio.all_tasks() == before
        assert runner._wake is None
        assert not runner._queued
        assert not runner._pending
        assert not runner._running

    asyncio.run(first())
    runner.request_title_check("again")

    async def second():
        complete = asyncio.Event()
        calls = []

        async def check(session_id, *, closing):
            calls.append((session_id, closing))
            complete.set()

        monkeypatch.setattr(runner, "check_session_title", check)
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        try:
            await wait(complete)
            await turn()
            assert calls == [("again", False)]
        finally:
            await stop(task, shutdown)

    asyncio.run(second())


def test_fresh_event_loop_discards_old_running_and_queued_state(runner, monkeypatch):
    async def first():
        entered = asyncio.Event()
        canceled = []
        calls = []

        async def check(session_id, *, closing):
            calls.append(session_id)
            if len(calls) == 2:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(session_id)

        monkeypatch.setattr(runner, "check_session_title", check)
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        runner.request_title_check("again", closing=True)
        runner.request_title_check("blocker")
        try:
            await wait(entered)
            runner.request_title_check("again", closing=True)
            runner.request_title_check("old-queued")
        finally:
            await stop(task, shutdown)
        assert canceled == ["again", "blocker"]

    asyncio.run(first())
    runner.request_title_check("again")

    async def second():
        complete = asyncio.Event()
        calls = []

        async def check(session_id, *, closing):
            calls.append((session_id, closing))
            complete.set()

        monkeypatch.setattr(runner, "check_session_title", check)
        shutdown = asyncio.Event()
        task = asyncio.create_task(runner.start_title_auto_task(shutdown))
        try:
            await wait(complete)
            assert calls == [("again", False)]
        finally:
            await stop(task, shutdown)

    asyncio.run(second())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("phase", ["generation", "push", "correction"])
def test_cancellation_owns_entire_real_check(runner, env, session, phase, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        canceled = asyncio.Event()
        before = asyncio.all_tasks()

        async def block():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        async def rename_hook(number):
            if phase == "correction" and number == 1:
                await Session.objects.filter(pk=session.pk).aupdate(title="User choice", title_origin="user")
            elif phase == "push" or number == 2:
                await block()

        if phase == "generation":
            env.generation_hook = block
        else:
            env.rename_hook = rename_hook
        monkeypatch.setattr(runner, "check_session_title", env.module.check_session_title)
        task = asyncio.create_task(runner.start_title_auto_task(asyncio.Event()))
        runner.request_title_check(session.id)
        try:
            await wait(entered)
            runner.request_title_check(session.id, closing=True)
            await stop(task)
            assert canceled.is_set()
            assert asyncio.all_tasks() == before
        finally:
            if not task.done():
                await stop(task)

    asyncio.run(scenario())
    session.refresh_from_db()
    if phase == "generation":
        assert session.title == "Current"
        assert session.title_check_count == 1
        assert session.title_checked_at == OLD
        assert env.pushes == []
    else:
        assert session.title_check_count == 7
        assert session.title_checked_at == env.clock
        assert session.title == ("User choice" if phase == "correction" else env.output)
        assert len(env.calls) == 1


@pytest.mark.django_db(transaction=True)
def test_same_session_waits_for_real_corrective_push(runner, env, session, monkeypatch):
    async def scenario():
        push_entered, push_release, correction_entered, correction_release, later = (
            asyncio.Event() for _ in range(5))
        requests = []
        original = env.module.check_session_title

        async def check(session_id, *, closing):
            requests.append(closing)
            if len(requests) == 2:
                later.set()
            await original(session_id, closing=closing)

        async def rename_hook(number):
            if number == 1:
                push_entered.set()
                await push_release.wait()
            else:
                correction_entered.set()
                await correction_release.wait()

        env.rename_hook = rename_hook
        monkeypatch.setattr(runner, "check_session_title", check)
        task = asyncio.create_task(runner.start_title_auto_task(asyncio.Event()))
        runner.request_title_check(session.id)
        try:
            await wait(push_entered)
            runner.request_title_check(session.id)
            runner.request_title_check(session.id, closing=True)
            await Session.objects.filter(pk=session.pk).aupdate(title="User choice", title_origin="user")
            await turn()
            assert requests == [False]
            push_release.set()
            await wait(correction_entered)
            await turn()
            assert requests == [False]
            correction_release.set()
            await wait(later)
            assert requests == [False, True]
        finally:
            await stop(task)

    asyncio.run(scenario())
    assert env.pushes == [(session.id, env.output), (session.id, "User choice")]


@pytest.mark.parametrize("startup_phase", ["normal", "provider-failure", "adoption-cancel"])
def test_server_owns_title_runner_before_provider_start_and_until_teardown(
    runner, monkeypatch, settings, startup_phase,
):
    """Execute run_server, while replacing socket, filesystem, and provider work."""
    run = import_module("twicc.cli.run")
    settings.CLAUDE_HYBRID_ENABLED = startup_phase == "adoption-cancel"
    effects = []

    async def scenario():
        entered = asyncio.Event()
        adopting = asyncio.Event()
        before = asyncio.all_tasks()

        async def noop(*args, **kwargs):
            return None

        async def check(session_id, *, closing):
            assert effects == ["db-start", "providers-start"]
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                effects.append("check-cancel")

        async def providers_start(shutdown, search_ready):
            effects.append("providers-start")
            runner.request_title_check("live-trigger")
            await wait(entered)
            if startup_phase == "provider-failure":
                raise RuntimeError("provider startup failed")

        async def adopt():
            assert entered.is_set()
            adopting.set()
            await asyncio.Event().wait()

        async def providers_stop():
            assert effects[-1] == "check-cancel"
            effects.append("providers-stop")

        async def db_stop():
            assert effects[-1] == "providers-stop"
            effects.append("db-stop")

        class Server:
            def __init__(self, config):
                self.should_exit = False

            async def serve(self):
                assert entered.is_set()

        monkeypatch.setattr(runner, "check_session_title", check)
        monkeypatch.setattr("uvicorn.Server", Server)
        monkeypatch.setattr("signal.signal", lambda *args: None)
        registry = SimpleNamespace(start_all=providers_start, shutdown_all=providers_stop,
                                   request_thread_stop_all=lambda: None)
        monkeypatch.setattr(run, "get_orchestrator_registry", lambda: registry)
        monkeypatch.setattr("twicc.providers.db_writer.start_db_writer", lambda: effects.append("db-start"))
        monkeypatch.setattr("twicc.providers.db_writer.stop_db_writer", db_stop)
        for name in (
            "sync_all_providers", "_orchestrate_global_search", "start_price_sync_task", "start_quota_wakeup_task",
            "start_session_dirs_cleanup_task", "start_peer_purge_task", "start_tmux_cleanup_task",
            "start_upload_cleanup_task", "start_composer_attachments_cleanup_task", "start_last_used_flush_task",
            "start_share_view_flush_task", "start_denial_flush_task", "start_telemetry_task", "start_version_check_task",
            "start_tips_watcher_task", "start_help_watcher_task",
        ):
            monkeypatch.setattr(run, name, noop)
        for name in ("init_manifest", "init_help_manifest", "stop_version_check_task", "stop_search_index_task",
                     "shutdown_search_index"):
            monkeypatch.setattr(run, name, lambda: None)
        monkeypatch.setattr(run, "get_active_indexing_tasks", lambda: [])
        for name in (
            "twicc.mcp.oauth.storage.enforce_password_requirement",
            "twicc.agent.process_run_cleanup.cleanup_stale_process_runs",
            "twicc.projects.refresh_all_project_directory_states", "twicc.core.services.trust.backfill_unimported_trust",
            "twicc.project_icons.discover_all_project_icons", "twicc.heartbeat.heartbeat_loop",
            "twicc.mcp.endpoint.start_mcp_task",
        ):
            monkeypatch.setattr(name, noop)
        monkeypatch.setattr("twicc.providers.state.apply_auto_enable_providers_bootstrap", lambda: None)
        monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(
            get=lambda provider: SimpleNamespace(adopt_running_hybrid_sessions=adopt)))
        monkeypatch.setattr("twicc.drop_requests_watcher.get_drop_requests_watcher", lambda: SimpleNamespace(start=noop))
        monkeypatch.setattr("twicc.artifacts_watcher.get_artifacts_watcher", lambda: SimpleNamespace(start=noop))
        monkeypatch.setattr("twicc.inline_artifacts.share_exports.get_inline_export_coordinator",
                            lambda: SimpleNamespace(start=noop, stop=noop))
        if startup_phase == "provider-failure":
            with pytest.raises(RuntimeError, match="provider startup failed"):
                await asyncio.wait_for(run.run_server(0), timeout=5)
        elif startup_phase == "adoption-cancel":
            server_task = asyncio.create_task(run.run_server(0))
            await wait(adopting)
            server_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(server_task, timeout=5)
        else:
            await asyncio.wait_for(run.run_server(0), timeout=5)
        assert asyncio.all_tasks() == before

    asyncio.run(scenario())
    expected = ["db-start", "providers-start", "check-cancel"]
    if startup_phase == "normal":
        expected += ["providers-stop", "db-stop"]
    assert effects == expected
