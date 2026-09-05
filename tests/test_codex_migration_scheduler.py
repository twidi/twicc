from __future__ import annotations

import asyncio
import contextlib
import queue
from pathlib import Path
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
from twicc.providers.codex.migration_gate import gate_for, is_migrating, request_rebuild
from twicc.providers.codex.rollout_migration import (
    CodexMigrationOutcome,
    HistoryMode,
    RolloutMigrationError,
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

        async def stop_background_task(ctx):
            # Mirrors the real one: it sets ``ctx.stop_event``, the
            # coordinator's own exit condition. ``_finish_run`` must not use it.
            ctx.stop_event.set()

        monkeypatch.setattr(background_compute, "stop_background_task", stop_background_task)
        monkeypatch.setattr(background_compute, "stop_compute_worker", lambda _ctx: _async_value(None))
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
        # Nothing left: the worker is stopped (``None``), the coordinator stays
        # alive for runtime rebuild requests.
        await _wait_until(lambda: command_queue.commands[-1] is None)
        await _wait_until(lambda: coordinator.run_active is False)
        assert not task.done()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def _legacy_setup(coordinator, monkeypatch, *, source=HistoryMode.LEGACY, database=HistoryMode.LEGACY):
    monkeypatch.setattr(coordinator, "_source_mode", lambda *_args: _async_value(source))
    monkeypatch.setattr(background_compute, "get_db_history_mode", lambda *_args: _async_value(database))
    monkeypatch.setattr(background_compute, "_has_snapshot_anchor", lambda *_args: _async_value(False))
    monkeypatch.setattr(coordinator, "_is_agent_active", lambda _session_id: False)
    monkeypatch.setattr(background_compute, "preflight_rollout", lambda _path: _clean_preflight())
    monkeypatch.setattr(background_compute, "prepare_full_history", lambda _path: SimpleNamespace(
        items=[(1, "{}")], last_offset=2, last_line=1, mtime=1.0,
    ))
    monkeypatch.setattr(background_compute.search, "is_initialized", lambda: False)
    jobs = []

    async def submit(job_type, *args):
        jobs.append((job_type.__name__, args))

    coordinator._submit_job = submit
    return jobs


def test_missing_sqlite_metadata_registers_the_thread_then_retries(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("unknown", SimpleNamespace(), SessionType.SESSION)
        jobs = _legacy_setup(coordinator, monkeypatch)
        outcomes = [
            CodexMigrationOutcome("failed", 0, "thread unknown is missing its SQLite metadata", "missing_sqlite_metadata"),
            CodexMigrationOutcome("migrated", 10, None),
        ]
        runs, registered = [], []

        async def run(session_id, path):
            runs.append(session_id)
            return outcomes.pop(0)

        async def register(session_id, path):
            registered.append(session_id)

        coordinator.runner = SimpleNamespace(run=run, stop=lambda: _async_value(None))
        monkeypatch.setattr(background_compute, "register_thread_with_codex", register)

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, PreparedCandidate)
        assert result.migrated_history is True
        assert runs == ["unknown", "unknown"]
        assert registered == ["unknown"]
        assert [name for name, _ in jobs] == ["CaptureSnapshotAnchorsJob", "ReplaceCodexHistoryJob"]
        assert gate_for("unknown").locked()
        assert is_migrating("unknown")
        result.migration_lease.release()
        coordinator._release_lease("unknown", replay=False)
        assert not is_migrating("unknown")

    asyncio.run(scenario())


def test_unreadable_rollout_is_flagged_unavailable(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("corrupt", SimpleNamespace(), SessionType.SESSION)
        jobs = _legacy_setup(coordinator, monkeypatch)
        coordinator.runner = SimpleNamespace(
            run=lambda *_args: _async_value(
                CodexMigrationOutcome("failed", 0, "rollout metadata invalid", "invalid_session_metadata"),
            ),
            stop=lambda: _async_value(None),
        )

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, FailedCandidate)
        assert result.unavailable_reason == "codex_migration_failed:invalid_session_metadata"
        assert ("MarkSessionUnavailableJob", ("corrupt", "codex_migration_failed:invalid_session_metadata")) in jobs
        assert not gate_for("corrupt").locked()
        assert not is_migrating("corrupt")

    asyncio.run(scenario())


def test_environmental_codex_failure_is_not_flagged_unavailable(monkeypatch):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("busy-disk", SimpleNamespace(), SessionType.SESSION)
        jobs = _legacy_setup(coordinator, monkeypatch)
        coordinator.runner = SimpleNamespace(
            run=lambda *_args: _async_value(
                CodexMigrationOutcome("failed", 0, "publish failed", "rollout_publish_failed"),
            ),
            stop=lambda: _async_value(None),
        )

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, FailedCandidate)
        assert result.unavailable_reason is None
        assert all(name != "MarkSessionUnavailableJob" for name, _ in jobs)

    asyncio.run(scenario())


def test_missing_rollout_is_flagged_unavailable(monkeypatch, tmp_path):
    async def scenario():
        coordinator = _coordinator()
        candidate = CodexComputeCandidate("gone", tmp_path / "missing.jsonl", SessionType.SESSION)
        jobs = []

        async def submit(job_type, *args):
            jobs.append((job_type.__name__, args))

        coordinator._submit_job = submit

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, FailedCandidate)
        assert result.unavailable_reason == "rollout_missing"
        assert jobs == [("MarkSessionUnavailableJob", ("gone", "rollout_missing"))]

    asyncio.run(scenario())


def test_truncated_rollout_forces_history_replacement(monkeypatch, tmp_path):
    """A file shorter than last_offset was rewritten: replace even when both modes agree."""

    async def scenario():
        coordinator = _coordinator()
        rollout = tmp_path / "rollout.jsonl"
        rollout.write_bytes(b"{}\n")
        candidate = CodexComputeCandidate("rewritten", rollout, SessionType.SESSION, last_offset=500)
        jobs = _legacy_setup(coordinator, monkeypatch, source=HistoryMode.PAGINATED, database=HistoryMode.PAGINATED)

        async def never_run(*_args):
            raise AssertionError("a paginated source must not be migrated again")

        coordinator.runner = SimpleNamespace(run=never_run, stop=lambda: _async_value(None))

        result = await coordinator.prepare_candidate(candidate)

        assert isinstance(result, PreparedCandidate)
        assert result.migrated_history is True
        assert [name for name, _ in jobs] == ["CaptureSnapshotAnchorsJob", "ReplaceCodexHistoryJob"]
        result.migration_lease.release()
        coordinator._release_lease("rewritten", replay=False)

    asyncio.run(scenario())


def test_rebuild_request_lifts_the_per_run_failure(monkeypatch):
    from twicc.providers.codex import migration_gate

    monkeypatch.setattr(migration_gate, "_rebuild_requests", set())
    coordinator = _coordinator()
    coordinator.failed_this_run.add("retry-me")
    coordinator.failures["retry-me"] = FailedCandidate("retry-me", "phase", "error")
    coordinator.deferred.add("retry-me")

    request_rebuild("retry-me")
    coordinator._absorb_rebuild_requests()

    assert "retry-me" not in coordinator.failed_this_run
    assert "retry-me" not in coordinator.failures
    assert "retry-me" not in coordinator.deferred


def test_released_session_is_replayed_through_the_callback():
    async def scenario():
        replayed = []

        async def on_released(session_id, path):
            replayed.append((session_id, path))

        ctx = SimpleNamespace(compute_version=2, stop_event=asyncio.Event())
        coordinator = CodexComputeCoordinator(ctx, asyncio.Event(), on_released)
        lease = gate_for("done")
        await lease.acquire()
        coordinator.migration_leases["done"] = lease
        coordinator.migration_paths["done"] = Path("/tmp/done.jsonl")

        coordinator._release_lease("done")
        await asyncio.sleep(0)

        assert replayed == [("done", Path("/tmp/done.jsonl"))]
        assert not lease.locked()

    asyncio.run(scenario())


async def _wait_until(predicate, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=timeout)


def test_outcome_tally_feeds_the_ui_detail_line(monkeypatch):
    coordinator = _coordinator()
    assert coordinator._detail() == "Codex rollouts: nothing to migrate"

    coordinator._prepared["a"] = PreparedCandidate("a", None, True, kind="migrated", registered=True)
    coordinator._prepared["b"] = PreparedCandidate("b", None, True, kind="replaced")
    coordinator.deferred.add("c")
    # A fake logger rather than caplog: other tests disable logging globally.
    messages: list[str] = []

    def record(message, *args, **_kwargs):
        messages.append(message % args if args else message)

    monkeypatch.setattr(
        background_compute, "logger",
        SimpleNamespace(info=record, error=record, warning=record, exception=record, debug=record),
    )
    asyncio.run(coordinator._handle_applied(ComputeApplied("a", "applied")))
    asyncio.run(coordinator._handle_applied(ComputeApplied("b", "applied")))
    asyncio.run(coordinator._record_failure(FailedCandidate("d", "Codex migration", "boom", "rollout_missing")))
    asyncio.run(coordinator._record_failure(FailedCandidate("e", "history read", "boom")))

    assert coordinator._detail() == (
        "Codex rollouts: 1 migrated, 1 registered with Codex first, 1 rebuilt after an external rewrite, "
        "1 waiting for a running agent, 1 failed, 1 unavailable"
    )
    assert any("session a migrated by Codex and rebuilt (registered with Codex first)" in m for m in messages)
    assert any("session b rebuilt from its rewritten rollout" in m for m in messages)


def test_final_summary_waits_for_the_in_flight_subagent(monkeypatch):
    """The last top-level session may be classified while a subagent is still applying."""

    coordinator = _coordinator()
    coordinator.initial_ids = {"parent"}
    coordinator._initial_total = 1
    coordinator._prepared["parent"] = PreparedCandidate("parent", None, True, kind="migrated")
    coordinator._prepared["child"] = PreparedCandidate("child", None, True, kind="migrated", registered=True)
    coordinator.computed_not_applied.add("child")
    messages: list[str] = []

    def record(message, *args, **_kwargs):
        messages.append(message % args if args else message)

    monkeypatch.setattr(
        background_compute, "logger",
        SimpleNamespace(info=record, error=record, warning=record, exception=record, debug=record),
    )
    monkeypatch.setattr(background_compute, "broadcast_startup_progress", lambda *_a, **_k: _async_value(None))
    monkeypatch.setattr("twicc.search_indexing_task.request_session_reindex", lambda _session_id: None)

    asyncio.run(coordinator._handle_applied(ComputeApplied("parent", "applied")))
    assert coordinator.initial_done.is_set()
    assert not any("initial pass complete" in m for m in messages)

    asyncio.run(coordinator._handle_applied(ComputeApplied("child", "applied")))
    [summary] = [m for m in messages if "initial pass complete" in m]
    assert "2 migrated, 1 registered with Codex first" in summary


def test_startup_progress_carries_the_detail_line():
    from twicc import startup_progress

    startup_progress._current_progress.clear()
    assert startup_progress.set_startup_progress(
        "background_compute", 3, 10, provider="codex", detail="Codex rollouts: 3 migrated",
    )
    [state] = startup_progress.get_startup_progress()
    assert state["detail"] == "Codex rollouts: 3 migrated"
    # Replaced, never accumulated; absent by default.
    assert startup_progress.set_startup_progress("background_compute", 4, 10, provider="codex")
    assert startup_progress.get_startup_progress()[0]["detail"] is None
    startup_progress._current_progress.clear()


def test_offset_zero_forces_a_history_replacement(monkeypatch, tmp_path):
    """An interrupted replacement (last_offset 0, rows missing or partial) is repaired, never computed."""

    async def scenario():
        coordinator = _coordinator()
        rollout = tmp_path / "rollout.jsonl"
        rollout.write_bytes(b"{}\n")
        candidate = CodexComputeCandidate("interrupted", rollout, SessionType.SESSION, last_offset=0)
        jobs = _legacy_setup(coordinator, monkeypatch, source=HistoryMode.PAGINATED, database=HistoryMode.PAGINATED)

        async def never_run(*_args):
            raise AssertionError("a paginated source must not be migrated again")

        coordinator.runner = SimpleNamespace(run=never_run, stop=lambda: _async_value(None))
        result = await coordinator.prepare_candidate(candidate)
        assert isinstance(result, PreparedCandidate)
        assert result.kind == "replaced"
        assert [name for name, _ in jobs] == ["CaptureSnapshotAnchorsJob", "ReplaceCodexHistoryJob"]
        result.migration_lease.release()
        coordinator._release_lease("interrupted", replay=False)

        # No stored row at all (crash right after the delete slice): same repair.
        async def no_first_item(*_args):
            raise RolloutMigrationError("no stored first item")

        monkeypatch.setattr(background_compute, "get_db_history_mode", no_first_item)
        jobs.clear()
        result = await coordinator.prepare_candidate(candidate)
        assert isinstance(result, PreparedCandidate)
        assert [name for name, _ in jobs] == ["CaptureSnapshotAnchorsJob", "ReplaceCodexHistoryJob"]
        result.migration_lease.release()
        coordinator._release_lease("interrupted", replay=False)

    asyncio.run(scenario())
