"""Persistent failures stop; source changes and busy sessions remain recoverable."""

import asyncio
from contextlib import closing
import sqlite3

import orjson
import pytest

from twicc.core.content_storage import extraction
from twicc.core.enums import Provider
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.codex import background_compute as codex
from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult
from twicc.providers.compute_work import ComputeWorkState
from tests.test_background_compute_recovery import versions

pytest_plugins = ["tests.test_codex_compute_recovery"]
pytestmark = pytest.mark.django_db(transaction=True)


def configure(path, provider, monkeypatch):
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET provider=?", (provider.value,))
        database.commit()
    monkeypatch.setattr(codex, "sync_to_async", background.sync_to_async)
    monkeypatch.setattr(codex, "load_project_directories", lambda: None)
    monkeypatch.setattr(codex, "load_project_git_roots", lambda: None)
    monkeypatch.setattr(codex, "broadcast_startup_progress", background.broadcast_startup_progress)


async def prepared(candidate):
    return codex.PreparedCandidate(candidate.session_id, None, False)


def start(provider, *, prepare=prepared):
    state = ComputeWorkState()
    factory = ("tests.test_codex_compute_recovery:CodexRecoveryCompute" if provider == Provider.CODEX
               else "tests.test_background_compute_recovery:RecoveryCompute")
    ctx = background.ComputeContext(provider, 123, factory)
    owner = None
    db_writer.start_db_writer()
    if provider == Provider.CODEX:
        owner = codex.CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
        owner._is_agent_active = lambda sid: False
        if prepare is not None:
            owner.prepare_candidate = prepare
        task = asyncio.create_task(owner.run())
    else:
        task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=state))
    return task, ctx, state, owner


async def stop(task, ctx):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await background.retire_compute_run(ctx)
    await db_writer.stop_db_writer()


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
@pytest.mark.parametrize("failure", ["initialization", "apply", "process"])
def test_persistent_failure_stops_after_five_attempts_and_source_change_recovers(
    recovery_runtime, monkeypatch, provider, failure,
):
    path, _ = recovery_runtime
    configure(path, provider, monkeypatch)
    attempts = []
    broken = True
    original = (extraction.initialize_compute_session if failure != "apply"
                else BaseSessionCompute._apply_session_complete_final)
    if failure == "process":
        monkeypatch.setenv("RECOVERY_TEST_CRASH", "faulty")

    def fail(value):
        sid = value if failure != "apply" else value["session_id"]
        if sid == "faulty" and failure == "process":
            attempts.append(sid)
            return original(value)
        if sid == "faulty" and (broken or len(attempts) < 7):
            attempts.append(sid)
            raise RuntimeError("persistent diagnostic failure")
        return original(value)

    if failure != "apply":
        monkeypatch.setattr(extraction, "initialize_compute_session", fail)
    else:
        monkeypatch.setattr(BaseSessionCompute, "_apply_session_complete_final", staticmethod(fail))

    async def scenario():
        nonlocal broken
        task, ctx, state, _ = start(provider)
        try:
            async with asyncio.timeout(10):
                while len(attempts) < 5 or versions(path)["healthy"] != 123:
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            await asyncio.sleep(.2)
            assert len(attempts) == 5
            assert versions(path)["faulty"] is None
            broken = False
            if failure == "process":
                monkeypatch.delenv("RECOVERY_TEST_CRASH")
            state.notify_source_changed(provider, "faulty")
            async with asyncio.timeout(5):
                while versions(path)["faulty"] != 123:
                    await asyncio.sleep(.01)
            if failure != "process":
                assert len(attempts) == 7
        finally:
            await stop(task, ctx)
    asyncio.run(scenario())


def test_missing_rollout_waits_for_source_change_without_starting_worker(
    codex_recovery_runtime, monkeypatch, provider_home,
):
    path, _ = codex_recovery_runtime
    configure(path, Provider.CODEX, monkeypatch)
    rollout = path.parent / "restored.jsonl"
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET file_path=? WHERE id='faulty'", (str(rollout),))
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.commit()
    monkeypatch.setattr(codex.search, "is_initialized", lambda: False)
    monkeypatch.setattr(codex, "_EXTERNAL_WRITER_RETRY_SECONDS", .02)

    async def scenario():
        task, ctx, state, owner = start(Provider.CODEX, prepare=None)
        attempts = []
        prepare = owner.prepare_candidate

        async def observe(candidate):
            attempts.append(candidate.session_id)
            return await prepare(candidate)

        owner.prepare_candidate = observe
        try:
            async with asyncio.timeout(5):
                while "faulty" not in owner.failures:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.15)
            assert attempts == ["faulty"]
            assert ctx.process is None
            rollout.write_bytes(orjson.dumps({"type": "session_meta", "payload": {
                "id": "faulty", "cwd": "/test", "history_mode": "paginated",
            }}) + b"\n")
            state.notify_source_changed(Provider.CODEX, "faulty")
            async with asyncio.timeout(5):
                while versions(path)["faulty"] != 123 or owner.failures:
                    await asyncio.sleep(.01)
            with closing(sqlite3.connect(path)) as database:
                assert database.execute("SELECT unavailable_reason FROM core_session WHERE id='faulty'").fetchone() == (None,)
        finally:
            await stop(task, ctx)
    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["active", "busy"])
def test_busy_sessions_keep_retrying_without_worker_until_prepared(
    codex_recovery_runtime, monkeypatch, reason,
):
    path, _ = codex_recovery_runtime
    configure(path, Provider.CODEX, monkeypatch)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.commit()
    monkeypatch.setattr(codex, "_EXTERNAL_WRITER_RETRY_SECONDS", .01)

    async def scenario():
        attempts = []
        premature_workers = []

        async def prepare(candidate):
            attempts.append(candidate.session_id)
            if ctx.process is not None:
                premature_workers.append(ctx.process)
            if len(attempts) <= 8:
                return codex.DeferredCandidate(candidate.session_id, reason)
            return await prepared(candidate)

        task, ctx, _, owner = start(Provider.CODEX, prepare=prepare)
        try:
            async with asyncio.timeout(8):
                while versions(path)["faulty"] != 123:
                    await asyncio.sleep(.01)
            assert len(attempts) == 9
            assert not premature_workers
            assert not owner.failures
        finally:
            await stop(task, ctx)
    asyncio.run(scenario())


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_superseded_results_do_not_exhaust_retries(recovery_runtime, monkeypatch, provider):
    path, _ = recovery_runtime
    configure(path, provider, monkeypatch)
    apply = BaseSessionCompute._apply_session_complete_final
    superseded = []

    def reject_changed_revision(message):
        if message["session_id"] == "faulty" and len(superseded) < 8:
            superseded.append(message["session_id"])
            return ComputeApplyResult("superseded")
        return apply(message)

    monkeypatch.setattr(BaseSessionCompute, "_apply_session_complete_final", staticmethod(reject_changed_revision))

    async def scenario():
        task, ctx, _, _ = start(provider)
        try:
            async with asyncio.timeout(8):
                while versions(path) != {"faulty": 123, "healthy": 123}:
                    await asyncio.sleep(.01)
            assert len(superseded) == 8
        finally:
            await stop(task, ctx)
    asyncio.run(scenario())
