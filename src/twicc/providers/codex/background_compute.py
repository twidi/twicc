"""Long-lived Codex compute and rollout-migration coordinator."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import queue
from contextlib import suppress
from pathlib import Path
from typing import Literal, NamedTuple

from asgiref.sync import sync_to_async

from twicc import search
from twicc.agent import AgentState
from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType, Share
from twicc.projects import load_project_directories, load_project_git_roots
from twicc.providers.background_compute_task import (
    ComputeContext,
    start_compute_process,
    stop_background_task,
)
from twicc.providers.db_writer import (
    ComputeApplied,
    arm_compute_completion,
    submit_async_job,
)
from twicc.startup_progress import broadcast_startup_progress

from .agent import get_codex_agent_manager
from .helpers import CodexHelpers
from .initial_sync import extract_session_meta
from .migration_gate import gate_for, wait_for_migration_wake
from .rollout_migration import (
    SNAPSHOT_ANCHOR_KEY,
    CaptureSnapshotAnchorsJob,
    ClearSnapshotAnchorsJob,
    CodexMigrationRunner,
    HistoryMode,
    MigrationPreparation,
    ReplaceCodexHistoryJob,
    RolloutMigrationError,
    get_db_history_mode,
    migration_preparation,
    preflight_rollout,
    prepare_full_history,
)

logger = logging.getLogger(__name__)

_EXTERNAL_WRITER_RETRY_SECONDS = 30.0
_STATUS_QUEUE_POLL_SECONDS = 0.05


class CodexComputeCandidate(NamedTuple):
    session_id: str
    file_path: Path
    session_type: str


class PreparedCandidate(NamedTuple):
    session_id: str
    migration_lease: asyncio.Lock | None
    migrated_history: bool


class DeferredCandidate(NamedTuple):
    session_id: str
    reason: Literal["active", "skipped_busy"]


class FailedCandidate(NamedTuple):
    session_id: str
    phase: str
    error: str


PreparationResult = PreparedCandidate | DeferredCandidate | FailedCandidate


async def _load_stale_candidates(compute_version: int) -> list[CodexComputeCandidate]:
    rows = await sync_to_async(
        lambda: list(
            Session.objects.filter(provider=Provider.CODEX)
            .exclude(compute_version=compute_version)
            .order_by("-mtime")
            .values_list("id", "file_path", "type")
        )
    )()
    return [
        CodexComputeCandidate(session_id, CodexHelpers.SESSIONS_DIR / file_path, session_type)
        for session_id, file_path, session_type in rows
    ]


async def _has_snapshot_anchor(session_id: str) -> bool:
    def check() -> bool:
        return any(
            SNAPSHOT_ANCHOR_KEY in (options or {})
            for options in Share.objects.filter(session_id=session_id).values_list("options", flat=True)
        )

    return await sync_to_async(check)()


class CodexComputeCoordinator:
    """Schedule migration-aware Codex metadata computation."""

    def __init__(self, ctx: ComputeContext, initial_done: asyncio.Event) -> None:
        self.ctx = ctx
        self.initial_done = initial_done
        self.runner = CodexMigrationRunner()
        self.applied_queue: asyncio.Queue[ComputeApplied] = asyncio.Queue()
        self.events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self.submitted: set[str] = set()
        self.computed_not_applied: set[str] = set()
        self.applied_before_computed: set[str] = set()
        self.deferred: set[str] = set()
        self.failed_this_run: set[str] = set()
        self.failures: dict[str, FailedCandidate] = {}
        self.migration_leases: dict[str, asyncio.Lock] = {}
        self.migrated_history: set[str] = set()
        self.worker_errors: dict[str, str] = {}
        self.in_flight: str | None = None
        self.initial_ids: set[str] = set()
        self.initial_classified: set[str] = set()
        self._initial_total = 0
        self._pumps: list[asyncio.Task] = []

    def _is_agent_active(self, session_id: str) -> bool:
        info = get_codex_agent_manager().get_agent_info(session_id)
        return info is not None and info.state != AgentState.DEAD

    async def _source_mode(self, path: Path, session_id: str) -> HistoryMode:
        meta = await asyncio.to_thread(extract_session_meta, path)
        if meta is None or meta.session_id != session_id:
            raise RolloutMigrationError(f"Cannot read matching session_meta from {path}")
        return meta.history_mode

    async def _submit_job(self, job_type, *args):
        future = asyncio.get_running_loop().create_future()
        try:
            return await submit_async_job(job_type(Provider.CODEX, *args, future))
        except asyncio.CancelledError:
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                future.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Codex migration DB job failed during cancellation")
            raise

    async def prepare_candidate(self, candidate: CodexComputeCandidate) -> PreparationResult:
        session_id = candidate.session_id
        phase = "classify"
        lease: asyncio.Lock | None = None
        anchors_existed = False
        try:
            source_mode = await self._source_mode(candidate.file_path, session_id)
            database_mode = await get_db_history_mode(session_id)
            preparation = migration_preparation(source_mode, database_mode)
            anchors_existed = await _has_snapshot_anchor(session_id)

            needs_gate = preparation != MigrationPreparation.COMPUTE_ONLY or anchors_existed
            if not needs_gate:
                return PreparedCandidate(session_id, None, False)
            if self._is_agent_active(session_id):
                return DeferredCandidate(session_id, "active")

            lease = gate_for(session_id)
            await lease.acquire()

            source_mode = await self._source_mode(candidate.file_path, session_id)
            database_mode = await get_db_history_mode(session_id)
            preparation = migration_preparation(source_mode, database_mode)
            anchors_existed = await _has_snapshot_anchor(session_id)
            if self._is_agent_active(session_id):
                lease.release()
                return DeferredCandidate(session_id, "active")
            if preparation == MigrationPreparation.INCONSISTENT:
                raise RolloutMigrationError("legacy source conflicts with paginated database history")
            if preparation == MigrationPreparation.COMPUTE_ONLY and not anchors_existed:
                lease.release()
                return PreparedCandidate(session_id, None, False)

            if source_mode == HistoryMode.LEGACY:
                phase = "preflight"
                preflight = await asyncio.to_thread(preflight_rollout, candidate.file_path)
                if (
                    preflight.malformed_lines
                    or preflight.blank_lines
                    or preflight.retired_lines
                    or preflight.partial_trailing_line
                ):
                    logger.info(
                        "Codex rollout preflight: session=%s complete=%d malformed=%d "
                        "blank=%d retired=%d partial_trailing=%s",
                        session_id,
                        preflight.complete_lines,
                        preflight.malformed_lines,
                        preflight.blank_lines,
                        preflight.retired_lines,
                        preflight.partial_trailing_line,
                    )
                if preflight.oversized_line is not None:
                    raise RolloutMigrationError(
                        f"legacy rollout {candidate.file_path} line "
                        f"{preflight.oversized_line} is {preflight.oversized_bytes} bytes"
                    )

            phase = "snapshot anchors"
            await self._submit_job(CaptureSnapshotAnchorsJob, session_id)

            if source_mode == HistoryMode.LEGACY:
                phase = "Codex migration"
                outcome = await self.runner.run(session_id, candidate.file_path)
                if outcome.status == "skipped_busy":
                    if not anchors_existed:
                        await self._submit_job(ClearSnapshotAnchorsJob, session_id)
                    lease.release()
                    return DeferredCandidate(session_id, "skipped_busy")
                if outcome.status not in {"migrated", "already_paginated"}:
                    raise RolloutMigrationError(outcome.message or f"migration returned {outcome.status}")

            if preparation != MigrationPreparation.COMPUTE_ONLY:
                phase = "history read"
                history = await asyncio.to_thread(prepare_full_history, candidate.file_path)
                phase = "history replacement"
                await self._submit_job(
                    ReplaceCodexHistoryJob,
                    session_id,
                    history.items,
                    history.last_offset,
                    history.last_line,
                    history.mtime,
                )
                if search.is_initialized():
                    phase = "search invalidation"
                    await asyncio.to_thread(search.delete_session_documents, session_id)
                    await asyncio.to_thread(search.commit)

            return PreparedCandidate(session_id, lease, True)
        except asyncio.CancelledError:
            await self.runner.stop()
            if lease is not None and lease.locked():
                lease.release()
            raise
        except Exception as error:  # noqa: BLE001 - one session failure must not stop the coordinator
            if lease is not None and lease.locked():
                lease.release()
            return FailedCandidate(session_id, phase, str(error))

    async def _status_pump(self) -> None:
        status_queue = self.ctx.status_queue
        if status_queue is None:
            raise RuntimeError("Codex compute status channel is not configured")
        while not self.ctx.stop_event.is_set():
            try:
                status = status_queue.get_nowait()
            except queue.Empty:
                try:
                    await asyncio.wait_for(
                        self.ctx.stop_event.wait(),
                        timeout=_STATUS_QUEUE_POLL_SECONDS,
                    )
                except TimeoutError:
                    pass
                continue
            await self.events.put(("worker", status))

    async def _applied_pump(self) -> None:
        while not self.ctx.stop_event.is_set():
            signal = await self.applied_queue.get()
            await self.events.put(("applied", signal))

    async def _wake_pump(self) -> None:
        while not self.ctx.stop_event.is_set():
            await wait_for_migration_wake(
                self.ctx.stop_event,
                _EXTERNAL_WRITER_RETRY_SECONDS,
            )
            await self.events.put(("wake", None))

    async def _classify_initial(self, session_id: str) -> None:
        if session_id not in self.initial_ids or session_id in self.initial_classified:
            return
        self.initial_classified.add(session_id)
        current = len(self.initial_classified)
        completed = current == self._initial_total
        await broadcast_startup_progress(
            "background_compute",
            current,
            self._initial_total,
            provider=Provider.CODEX.value,
            completed=completed,
        )
        if completed:
            self.initial_done.set()

    def _release_lease(self, session_id: str) -> None:
        lease = self.migration_leases.pop(session_id, None)
        if lease is not None and lease.locked():
            lease.release()
        self.migrated_history.discard(session_id)

    async def _record_failure(self, failure: FailedCandidate) -> None:
        if failure.session_id in self.failed_this_run:
            return
        self.failed_this_run.add(failure.session_id)
        self.failures[failure.session_id] = failure
        self.deferred.discard(failure.session_id)
        self._release_lease(failure.session_id)
        await self._classify_initial(failure.session_id)
        logger.error(
            "Codex background compute failed: session=%s phase=%s error=%s",
            failure.session_id,
            failure.phase,
            failure.error,
        )

    async def _handle_worker_status(self, status: object) -> None:
        if not isinstance(status, dict):
            return
        session_id = status.get("session_id")
        if not isinstance(session_id, str):
            return
        if self.in_flight == session_id:
            self.in_flight = None
        self.submitted.discard(session_id)
        if status.get("type") == "failed":
            self.worker_errors[session_id] = str(status.get("error") or "compute worker failed")
        if session_id in self.applied_before_computed:
            self.applied_before_computed.discard(session_id)
        else:
            self.computed_not_applied.add(session_id)

    async def _handle_applied(self, signal: ComputeApplied) -> None:
        session_id = signal.session_id
        arrived_before_computed = session_id in self.submitted
        if arrived_before_computed:
            self.applied_before_computed.add(session_id)
        self.computed_not_applied.discard(session_id)

        migrated = session_id in self.migrated_history
        if signal.outcome == "failed":
            error = signal.error or self.worker_errors.get(session_id) or "metadata apply failed"
            await self._record_failure(FailedCandidate(session_id, "metadata apply", error))
            return
        if migrated and signal.outcome != "applied":
            await self._record_failure(FailedCandidate(
                session_id,
                "metadata apply",
                f"migration invariant rejected final apply as {signal.outcome}",
            ))
            return

        self.deferred.discard(session_id)
        self._release_lease(session_id)
        if signal.outcome in {"applied", "superseded"}:
            from twicc.search_indexing_task import request_session_reindex

            request_session_reindex(session_id)
        await self._classify_initial(session_id)

    async def _initialize_progress(self, candidates: list[CodexComputeCandidate]) -> None:
        self.initial_ids = {
            candidate.session_id
            for candidate in candidates
            if candidate.session_type == SessionType.SESSION
        }
        self._initial_total = len(self.initial_ids)
        await broadcast_startup_progress(
            "background_compute",
            0,
            self._initial_total,
            provider=Provider.CODEX.value,
            completed=self._initial_total == 0,
        )
        if self._initial_total == 0:
            self.initial_done.set()

    async def run(self) -> None:
        initial_candidates = await _load_stale_candidates(self.ctx.compute_version)
        await self._initialize_progress(initial_candidates)
        if not initial_candidates:
            logger.info("Codex background compute: no sessions to process")
            return

        await sync_to_async(load_project_directories)()
        await sync_to_async(load_project_git_roots)()
        run_id, done_future = arm_compute_completion(
            Provider.CODEX,
            display_session_ids=set(),
            total_display=0,
            applied_queue=self.applied_queue,
        )
        self.ctx.run_id = run_id
        if self.ctx.status_queue is None:
            self.ctx.status_queue = multiprocessing.get_context("spawn").Queue()
        start_compute_process(self.ctx)
        self._pumps = [
            asyncio.create_task(self._status_pump()),
            asyncio.create_task(self._applied_pump()),
            asyncio.create_task(self._wake_pump()),
        ]

        try:
            while not self.ctx.stop_event.is_set():
                candidates = await _load_stale_candidates(self.ctx.compute_version)
                stale_ids = {candidate.session_id for candidate in candidates}
                for session_id in self.initial_ids - stale_ids:
                    await self._classify_initial(session_id)

                candidate = next((
                    item
                    for item in candidates
                    if item.session_id not in self.submitted
                    and item.session_id not in self.computed_not_applied
                    and item.session_id not in self.failed_this_run
                    and item.session_id not in self.deferred
                ), None)

                if self.in_flight is None and candidate is not None:
                    prepared = await self.prepare_candidate(candidate)
                    if isinstance(prepared, DeferredCandidate):
                        self.deferred.add(prepared.session_id)
                        await self._classify_initial(prepared.session_id)
                        continue
                    if isinstance(prepared, FailedCandidate):
                        await self._record_failure(prepared)
                        continue

                    if prepared.migration_lease is not None:
                        self.migration_leases[prepared.session_id] = prepared.migration_lease
                    if prepared.migrated_history:
                        self.migrated_history.add(prepared.session_id)
                    self.submitted.add(prepared.session_id)
                    self.in_flight = prepared.session_id
                    self.ctx.command_queue.put_nowait({"session_id": prepared.session_id})
                    continue

                remaining = stale_ids - self.failed_this_run
                pending = bool(self.submitted or self.computed_not_applied)
                if not remaining and not pending and self.in_flight is None:
                    self.ctx.command_queue.put_nowait(None)
                    failed_count = await done_future
                    if failed_count and not self.failures:
                        logger.error("Codex DB writer reported %d failed compute result(s)", failed_count)
                    break

                event_type, payload = await self.events.get()
                if event_type == "worker":
                    await self._handle_worker_status(payload)
                elif event_type == "applied" and isinstance(payload, ComputeApplied):
                    await self._handle_applied(payload)
                elif event_type == "wake":
                    self.deferred.clear()
        finally:
            await self.runner.stop()
            for task in self._pumps:
                task.cancel()
            for task in self._pumps:
                with suppress(Exception, asyncio.CancelledError):
                    await task
            for session_id in list(self.migration_leases):
                self._release_lease(session_id)
            await stop_background_task(self.ctx)
            if not self.initial_done.is_set():
                self.initial_done.set()
            if self.failures:
                for failure in self.failures.values():
                    logger.error(
                        "Codex migration failure summary: session=%s phase=%s error=%s",
                        failure.session_id,
                        failure.phase,
                        failure.error,
                    )


async def start_codex_background_compute_task(
    ctx: ComputeContext,
    initial_done: asyncio.Event,
) -> None:
    """Run the migration-aware Codex coordinator."""

    await CodexComputeCoordinator(ctx, initial_done).run()
