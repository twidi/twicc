from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

from twicc.core.models import SessionType
from twicc.providers.codex import background_compute
from twicc.providers.codex.background_compute import (
    CodexComputeCandidate,
    CodexComputeCoordinator,
    DeferredCandidate,
    FailedCandidate,
    PreparedCandidate,
)
from twicc.providers.codex.migration_gate import gate_for
from twicc.providers.codex.rollout_migration import (
    CodexMigrationOutcome,
    HistoryMode,
    RolloutPreflight,
)
from twicc.providers.db_writer import ComputeApplied


def _coordinator():
    ctx = SimpleNamespace(compute_version=2, stop_event=asyncio.Event())
    return CodexComputeCoordinator(ctx, asyncio.Event())


def test_normal_superseded_apply_is_successful():
    async def scenario():
        coordinator = _coordinator()
        await coordinator._handle_applied(ComputeApplied("session", "superseded"))
        assert not coordinator.failed_this_run

    asyncio.run(scenario())


def test_migrated_superseded_apply_is_an_invariant_failure():
    async def scenario():
        coordinator = _coordinator()
        lease = gate_for("session")
        await lease.acquire()
        coordinator.migration_leases["session"] = lease
        coordinator.migrated_history.add("session")

        await coordinator._handle_applied(ComputeApplied("session", "superseded"))

        assert coordinator.failures["session"].phase == "metadata apply"
        assert not lease.locked()

    asyncio.run(scenario())


def test_active_candidate_defers_without_holding_gate(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("active", SimpleNamespace(), SessionType.SESSION)
        monkeypatch.setattr(coordinator, "_source_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "get_db_history_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "_has_snapshot_anchor", lambda *_args: _async_value(False))
        monkeypatch.setattr(coordinator, "_is_agent_active", lambda _session_id: True)

        result = await coordinator.prepare_candidate(candidate)

        assert result == DeferredCandidate("active", "active")
        assert not gate_for("active").locked()

    asyncio.run(scenario())


def _async_value(value):
    async def result():
        return value

    return result()


def _clean_preflight() -> RolloutPreflight:
    return RolloutPreflight(1, 0, 0, 0, False, None, None)


def test_skipped_busy_clears_attempt_anchors(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("busy", SimpleNamespace(), SessionType.SESSION)
        jobs = []
        monkeypatch.setattr(coordinator, "_source_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "get_db_history_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "_has_snapshot_anchor", lambda *_args: _async_value(False))
        monkeypatch.setattr(coordinator, "_is_agent_active", lambda _session_id: False)
        monkeypatch.setattr(background_compute, "preflight_rollout", lambda _path: _clean_preflight())

        async def submit(job_type, *_args):
            jobs.append(job_type.__name__)

        coordinator._submit_job = submit
        coordinator.runner = SimpleNamespace(
            run=lambda *_args: _async_value(CodexMigrationOutcome("skipped_busy", 0, None)),
            stop=lambda: _async_value(None),
        )

        result = await coordinator.prepare_candidate(candidate)

        assert result == DeferredCandidate("busy", "skipped_busy")
        assert jobs == ["CaptureSnapshotAnchorsJob", "ClearSnapshotAnchorsJob"]
        assert not gate_for("busy").locked()

    asyncio.run(scenario())


def test_real_failure_after_anchor_does_not_clear_it(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("broken", SimpleNamespace(), SessionType.SESSION)
        jobs = []
        monkeypatch.setattr(coordinator, "_source_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "get_db_history_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "_has_snapshot_anchor", lambda *_args: _async_value(False))
        monkeypatch.setattr(coordinator, "_is_agent_active", lambda _session_id: False)
        monkeypatch.setattr(background_compute, "preflight_rollout", lambda _path: _clean_preflight())

        async def submit(job_type, *_args):
            jobs.append(job_type.__name__)

        async def fail(*_args):
            raise RuntimeError("migration failed")

        coordinator._submit_job = submit
        coordinator.runner = SimpleNamespace(run=fail, stop=lambda: _async_value(None))

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, FailedCandidate)
        assert jobs == ["CaptureSnapshotAnchorsJob"]
        assert not gate_for("broken").locked()

    asyncio.run(scenario())


def test_cancellation_reaps_child_before_releasing_gate(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("cancelled", SimpleNamespace(), SessionType.SESSION)
        run_entered = asyncio.Event()
        reaped = asyncio.Event()
        block = asyncio.Event()
        monkeypatch.setattr(coordinator, "_source_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "get_db_history_mode", lambda *_args: _async_value(HistoryMode.LEGACY))
        monkeypatch.setattr(background_compute, "_has_snapshot_anchor", lambda *_args: _async_value(False))
        monkeypatch.setattr(coordinator, "_is_agent_active", lambda _session_id: False)
        monkeypatch.setattr(background_compute, "preflight_rollout", lambda _path: _clean_preflight())
        coordinator._submit_job = lambda *_args: _async_value(None)

        async def run(*_args):
            run_entered.set()
            await block.wait()

        async def stop():
            reaped.set()

        coordinator.runner = SimpleNamespace(run=run, stop=stop)
        task = asyncio.create_task(coordinator.prepare_candidate(candidate))
        await run_entered.wait()
        assert gate_for("cancelled").locked()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert reaped.is_set()
        assert not gate_for("cancelled").locked()

    asyncio.run(scenario())


def test_cancelled_migration_job_waits_for_db_settlement(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        submitted = asyncio.Event()
        jobs = []

        async def submit(job):
            jobs.append(job)
            submitted.set()
            return await asyncio.shield(job.future)

        monkeypatch.setattr(background_compute, "submit_async_job", submit)

        task = asyncio.create_task(
            coordinator._submit_job(
                background_compute.CaptureSnapshotAnchorsJob,
                "cancelled-db-job",
            )
        )
        await submitted.wait()
        task.cancel()
        await asyncio.sleep(0)

        assert not task.done()

        jobs[0].future.set_result(1)
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation must propagate after the DB job settles")

    asyncio.run(scenario())


class _FakeCommandQueue:
    def __init__(self, done_future):
        self.commands = []
        self.done_future = done_future

    def put_nowait(self, command):
        self.commands.append(command)
        if command is None and not self.done_future.done():
            self.done_future.set_result(0)


def test_worker_dispatches_next_only_after_computed_not_applied(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        done_future = loop.create_future()
        status_queue = queue.Queue()
        command_queue = _FakeCommandQueue(done_future)
        ctx = SimpleNamespace(
            compute_version=2,
            stop_event=asyncio.Event(),
            status_queue=status_queue,
            command_queue=command_queue,
            run_id=0,
        )
        coordinator = CodexComputeCoordinator(ctx, asyncio.Event())
        candidates = [
            CodexComputeCandidate("newest", SimpleNamespace(), SessionType.SESSION),
            CodexComputeCandidate("older", SimpleNamespace(), SessionType.SESSION),
        ]
        stale = {candidate.session_id for candidate in candidates}

        async def load(_version):
            return [candidate for candidate in candidates if candidate.session_id in stale]

        async def prepare(candidate):
            return PreparedCandidate(candidate.session_id, None, False)

        original_handle = coordinator._handle_applied

        async def handle_applied(signal):
            stale.discard(signal.session_id)
            await original_handle(signal)

        monkeypatch.setattr(background_compute, "_load_stale_candidates", load)
        monkeypatch.setattr(background_compute, "broadcast_startup_progress", lambda *_a, **_k: _async_value(None))
        monkeypatch.setattr(background_compute, "load_project_directories", lambda: None)
        monkeypatch.setattr(background_compute, "load_project_git_roots", lambda: None)
        monkeypatch.setattr(background_compute, "arm_compute_completion", lambda *_a, **_k: (1, done_future))
        monkeypatch.setattr(background_compute, "start_compute_process", lambda _ctx: None)
        monkeypatch.setattr(background_compute, "stop_background_task", lambda _ctx: _async_value(None))
        coordinator.prepare_candidate = prepare
        coordinator._handle_applied = handle_applied

        task = asyncio.create_task(coordinator.run())
        await _wait_until(lambda: len(command_queue.commands) == 1)
        assert command_queue.commands == [{"session_id": "newest"}]

        status_queue.put({"type": "computed", "session_id": "newest"})
        await _wait_until(lambda: len(command_queue.commands) == 2)
        assert command_queue.commands[1] == {"session_id": "older"}
        assert "newest" in coordinator.computed_not_applied

        status_queue.put({"type": "computed", "session_id": "older"})
        await coordinator.applied_queue.put(ComputeApplied("newest", "applied"))
        await coordinator.applied_queue.put(ComputeApplied("older", "applied"))
        await asyncio.wait_for(task, timeout=2)
        assert command_queue.commands[-1] is None

    asyncio.run(scenario())


async def _wait_until(predicate, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=timeout)
