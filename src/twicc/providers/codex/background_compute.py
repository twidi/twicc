"""Long-lived Codex compute and rollout-migration coordinator.

The generic background compute (:mod:`twicc.providers.background_compute_task`)
selects age-eligible stale sources until all sessions are current. Codex
also coordinates runtime rollout migration, because since 0.151 its rollouts come in two on-disk formats
(legacy events vs. paginated canonical items — see :mod:`.canonical`) and a
legacy session must be **migrated by Codex itself** (``codex migrate-rollouts
--apply``, one thread at a time) before TwiCC reads it. That rewrites the
JSONL — line numbers, offsets, everything TwiCC keys on — so each migration is
a small, serialised transaction:

1. classify the session from its source ``history_mode`` and the one TwiCC
   stored (:func:`.rollout_migration.migration_preparation`);
2. take the per-session gate (:mod:`.migration_gate`) and flag the session
   *migrating*: the agent manager waits before sending to it, the watcher
   skips its file events (never queues behind the gate — one slow migration
   must not freeze every other session's live updates);
3. run Codex's migration when the source is legacy. Codex refuses a thread
   its own state DB does not know (``missing_sqlite_metadata``): every rollout
   older than that machine's backfill. A registration-only ``thread/resume``
   (no turn, no tokens, MCP servers disabled) fixes that, then the migration
   runs again;
4. replace TwiCC's raw history from byte zero through the DB writer, then
   dispatch the read-only CPU worker; the final metadata apply remaps
   snapshot shares and advances ``compute_version`` in one transaction;
5. release the gate and replay the file through the watcher, so lines
   appended while the session was flagged are ingested.

Busy sessions (a live TwiCC agent, or Codex's ``skipped_busy`` because
another process holds the writer lock) are deferred and retried on agent
``DEAD`` / a timer. A rollout Codex cannot read or convert, or one that is
gone from disk, is flagged ``Session.unavailable_reason`` so the UI explains
it. Bounded periodic retries re-read its source and database state.

The coordinator stays alive for the whole provider lifetime — idle without a
worker process between runs — because a rewrite can also happen at runtime
(a manual ``codex migrate-rollouts --apply``, or Codex's own background
migration once OpenAI enables it by default): the watcher detects it,
resets ``compute_version`` and files a rebuild request; the coordinator
starts a new run on the spot.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import queue
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import suppress
from itertools import count
from pathlib import Path
from typing import Literal, NamedTuple

from asgiref.sync import sync_to_async
from django.db.models import Q

from twicc import search
from twicc.agent import AgentState
from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType, Share
from twicc.projects import load_project_directories, load_project_git_roots
from twicc.provider_homes import codex_sessions_dir
from twicc.providers.background_compute_task import (
    ComputeContext,
    _wait_for_retry_delay,
    read_compute_state,
    retire_compute_run,
    start_compute_process,
    stop_background_task,
    stop_compute_worker,
)
from twicc.providers.compute_selection import (
    COMPUTE_MIN_SOURCE_AGE_SECONDS, COMPUTE_STATUS_POLL_SECONDS,
)
from twicc.providers.db_writer import (
    ComputeApplied,
    InitializeComputeStorageJob,
    arm_compute_completion,
    submit_async_job,
)
from twicc.providers.session_change_queue import MigrationRelease
from twicc.startup_progress import broadcast_startup_progress

from .agent import get_codex_agent_manager
from .initial_sync import extract_session_meta
from .migration_gate import (
    gate_for,
    mark_migrating,
    take_rebuild_requests,
    unmark_migrating,
    wait_for_migration_wake,
)
from .rollout_migration import (
    MISSING_SQLITE_METADATA,
    ROLLOUT_UNREADABLE_REASONS,
    SNAPSHOT_ANCHOR_KEY,
    UNAVAILABLE_ROLLOUT_MISSING,
    CaptureSnapshotAnchorsJob,
    ClearSnapshotAnchorsJob,
    CodexMigrationRunner,
    HistoryMode,
    MarkSessionUnavailableJob,
    MigrationPreparation,
    ReplaceCodexHistoryJob,
    RolloutMigrationError,
    get_db_history_mode,
    migration_preparation,
    preflight_rollout,
    prepare_full_history,
    register_thread_with_codex,
)

logger = logging.getLogger(__name__)

# Bound initialized sessions until both worker status and writer acknowledgment arrive.
CODEX_MAX_OUTSTANDING = 2

_EXTERNAL_WRITER_RETRY_SECONDS = 30.0
_UNAVAILABLE_RECOVERY_PAGE_SIZE = 200
# A progress summary line is logged every N outcomes or every N seconds,
# whichever comes first, while sessions are being processed.
# Final outcomes that count as "done" in the user-facing progress line.
_GOOD_OUTCOMES = frozenset({"migrated", "replaced", "compute"})
_SUMMARY_EVERY_OUTCOMES = 50
_SUMMARY_EVERY_SECONDS = 30.0

# Called after a session's gate is released, with its rollout path, so the
# owner (the orchestrator) can replay the file through the watcher.
SessionReleasedCallback = Callable[[str, Path], Awaitable[None]]
MigrationReleasedCallback = Callable[[MigrationRelease], None]
_release_tokens = count(1)


class CodexComputeCandidate(NamedTuple):
    session_id: str
    file_path: Path
    session_type: str
    last_offset: int = 0
    unavailable_reason: str | None = None
    mtime: float = 0.0


class PreparedCandidate(NamedTuple):
    session_id: str
    migration_lease: asyncio.Lock | None
    migrated_history: bool
    # How the history was prepared, for the logs and the UI tally:
    # ``migrated`` (Codex converted a legacy rollout), ``replaced`` (already
    # paginated on disk, TwiCC re-read it from byte zero), ``compute``
    # (metadata only). ``registered`` marks a ``thread/resume`` registration
    # before the migration.
    kind: str = "compute"
    registered: bool = False


class DeferredCandidate(NamedTuple):
    session_id: str
    reason: Literal["active", "skipped_busy"]


class FailedCandidate(NamedTuple):
    session_id: str
    phase: str
    error: str
    # Set when the failure condemns the rollout (Codex cannot read or convert
    # it, or it is gone): persisted on ``Session.unavailable_reason``.
    unavailable_reason: str | None = None


PreparationResult = PreparedCandidate | DeferredCandidate | FailedCandidate


class _RolloutMissing(RolloutMigrationError):
    """The session's rollout is gone from disk."""


async def _load_stale_candidates(
    compute_version: int, *, forced_ids: frozenset[str] = frozenset(), source_ids=None,
) -> list[CodexComputeCandidate]:
    """Read startup metadata once, or refresh only an admitted source."""
    def load():
        sources = Session.objects.filter(provider=Provider.CODEX)
        if source_ids is None:
            sources = sources.filter(
                Q(compute_version__isnull=True) | Q(compute_version__lt=compute_version)
                | Q(id__in=forced_ids) | Q(unavailable_reason__isnull=False),
            )
        else:
            sources = sources.filter(pk__in=source_ids)
        sessions_dir = codex_sessions_dir()
        return [
            CodexComputeCandidate(sid, sessions_dir / path, kind, offset, unavailable, mtime)
            for sid, path, kind, offset, unavailable, mtime in sources.order_by('-mtime').values_list(
                'id', 'file_path', 'type', 'last_offset', 'unavailable_reason', 'mtime',
            ).iterator(chunk_size=200)
        ]
    return await sync_to_async(load)()


async def _has_snapshot_anchor(session_id: str) -> bool:
    def check() -> bool:
        return any(
            SNAPSHOT_ANCHOR_KEY in (options or {})
            for options in Share.objects.filter(session_id=session_id).values_list("options", flat=True)
        )

    return await sync_to_async(check)()


def _file_size(path: Path) -> int | None:
    try:
        return Path(path).stat().st_size
    except (OSError, TypeError):
        return None


class CodexComputeCoordinator:
    """Schedule migration-aware Codex metadata computation."""

    def __init__(
        self,
        ctx: ComputeContext,
        initial_done: asyncio.Event,
        on_session_released: SessionReleasedCallback | None = None,
        on_migration_released: MigrationReleasedCallback | None = None,
        *, work_state=None,
    ) -> None:
        self.ctx = ctx
        self.work_state = work_state
        self._source_generation = 0
        self._shutdown_drained = False
        self._active_root: str | None = None
        self._active_diagnoses: set[str] = set()
        self._blocked_roots: dict[str, float] = {}
        self._blocked_diagnoses: dict[str, set[str]] = {}
        self.initial_done = initial_done
        self.on_session_released = on_session_released
        self.on_migration_released = on_migration_released
        self._replays: set[asyncio.Task] = set()
        self.runner = CodexMigrationRunner()
        self.applied_queue: asyncio.Queue[ComputeApplied] = asyncio.Queue()
        self.events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self.submitted: set[str] = set()
        self.computed_not_applied: set[str] = set()
        self.applied_before_computed: set[str] = set()
        self.deferred: set[str] = set()
        self.failed_this_run: set[str] = set()
        self._retry_at: dict[str, float] = {}
        self._retry_delays: dict[str, float] = {}
        self._process_delay = 1.0
        self._retired_run_ids: set[int] = set()
        self.failures: dict[str, FailedCandidate] = {}
        self._unavailable_diagnoses: dict[str, str] = {}
        self.migration_leases: dict[str, asyncio.Lock] = {}
        self.migration_paths: dict[str, Path] = {}
        self._forced_rebuild: set[str] = set()
        # Startup can find stale children beneath a current repaired root.
        # Retain their roots, not a provider-wide source inventory.
        self._startup_child_roots: set[str] = set()
        self._rebuild_generation: dict[str, int] = {}
        self._prepared_rebuild_generation: dict[str, int] = {}
        self.migrated_history: set[str] = set()
        self.worker_errors: dict[str, str] = {}
        self.in_flight: str | None = None
        self.initial_ids: set[str] = set()
        self.initial_classified: set[str] = set()
        self.initial_ready: set[str] = set()
        # Initial top-level sessions currently deferred, failed or unavailable
        # (the UI line's "set aside"); a later success removes them again.
        self._set_aside: set[str] = set()
        # Initial sessions whose rollout was still legacy at startup (all
        # types); the user-facing line counts the top-level ones only.
        self._legacy_ids: set[str] = set()
        self._initial_total = 0
        self._pumps: list[asyncio.Task] = []
        self._status_pump: asyncio.Task | None = None
        self._done_future: asyncio.Future | None = None
        self.run_active = False
        # Observability: outcome tally (logs + UI detail line), per-session
        # timing, and what each in-flight session went through.
        self.stats: Counter[str] = Counter()
        self._started_at: dict[str, float] = {}
        self._prepared: dict[str, PreparedCandidate] = {}
        self._logged_deferred: set[str] = set()
        self._outcomes_since_summary = 0
        self._last_summary_at = time.monotonic()
        self._pass_started_at = time.monotonic()
        self._final_summary_pending = False
        # ``stop_compute_worker`` closes the context's queues at the end of
        # a run; the next run needs fresh ones.
        self._queues_closed = False

    # ------------------------------------------------------------------
    # Preparation of one candidate
    # ------------------------------------------------------------------

    def _is_agent_active(self, session_id: str) -> bool:
        info = get_codex_agent_manager().get_agent_info(session_id)
        return info is not None and info.state != AgentState.DEAD

    async def _source_mode(self, path: Path, session_id: str) -> HistoryMode:
        if not path.exists():
            raise _RolloutMissing(f"Rollout is gone from disk: {path}")
        meta = await asyncio.to_thread(extract_session_meta, path)
        if meta is None or meta.session_id != session_id:
            raise RolloutMigrationError(f"Cannot read matching session_meta from {path}")
        return meta.history_mode

    async def _database_mode(self, candidate: CodexComputeCandidate) -> HistoryMode:
        """The stored history mode; a session at offset 0 with no rows counts as legacy.

        Offset 0 without a stored ``session_meta`` is an interrupted history
        replacement (or a row never synced): there is nothing to compute
        from, the rollout must be read from byte zero, which the legacy
        answer produces through the decision matrix.
        """
        try:
            return await get_db_history_mode(candidate.session_id)
        except RolloutMigrationError:
            if candidate.last_offset == 0:
                return HistoryMode.LEGACY
            raise

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

    async def _run_codex_migration(self, session_id: str, path: Path):
        """Run Codex's migration, registering the thread first if Codex does not know it.

        Returns ``(outcome, registered)``.
        """

        outcome = await self.runner.run(session_id, path)
        if outcome.status == "failed" and outcome.failure_reason == MISSING_SQLITE_METADATA:
            logger.info(
                "Codex does not know thread %s (predates its state DB): registering it via thread/resume",
                session_id,
            )
            await register_thread_with_codex(session_id, path)
            return await self.runner.run(session_id, path), True
        return outcome, False

    async def prepare_candidate(self, candidate: CodexComputeCandidate) -> PreparationResult:
        session_id = candidate.session_id
        if candidate.unavailable_reason:
            self._unavailable_diagnoses[session_id] = candidate.unavailable_reason
        phase = "classify"
        lease: asyncio.Lock | None = None
        anchors_existed = False
        registered = False
        self._started_at.setdefault(session_id, time.monotonic())
        try:
            source_mode = await self._source_mode(candidate.file_path, session_id)
            database_mode = await self._database_mode(candidate)
            preparation = migration_preparation(source_mode, database_mode)
            anchors_existed = await _has_snapshot_anchor(session_id)
            # A rollout shorter than what TwiCC already ingested was rewritten
            # (a re-migration, a rollback...): its history must be replaced
            # whatever the modes say. So must a session at offset 0: an
            # interrupted history replacement (see
            # ``_begin_replace_codex_history``) leaves it there with no or
            # only part of its rows.
            size = _file_size(candidate.file_path)
            truncated = (size is not None and size < candidate.last_offset) or candidate.last_offset == 0
            self._absorb_rebuild_requests()
            if (truncated or session_id in self._forced_rebuild) and preparation == MigrationPreparation.COMPUTE_ONLY:
                preparation = MigrationPreparation.REPLACE_ONLY

            needs_gate = preparation != MigrationPreparation.COMPUTE_ONLY or anchors_existed
            if not needs_gate:
                return PreparedCandidate(session_id, None, False)
            if self._is_agent_active(session_id):
                return DeferredCandidate(session_id, "active")

            lease = gate_for(session_id)
            await lease.acquire()
            mark_migrating(session_id)
            self.migration_paths[session_id] = candidate.file_path

            source_mode = await self._source_mode(candidate.file_path, session_id)
            database_mode = await self._database_mode(candidate)
            preparation = migration_preparation(source_mode, database_mode)
            anchors_existed = await _has_snapshot_anchor(session_id)
            self._absorb_rebuild_requests()
            if (truncated or session_id in self._forced_rebuild) and preparation == MigrationPreparation.COMPUTE_ONLY:
                preparation = MigrationPreparation.REPLACE_ONLY
            if self._is_agent_active(session_id):
                self._release_lease(session_id, replay=False)
                return DeferredCandidate(session_id, "active")
            if preparation == MigrationPreparation.INCONSISTENT:
                raise RolloutMigrationError("legacy source conflicts with paginated database history")
            if preparation == MigrationPreparation.COMPUTE_ONLY and not anchors_existed:
                self._release_lease(session_id, replay=False)
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
                outcome, registered = await self._run_codex_migration(session_id, candidate.file_path)
                if outcome.status == "skipped_busy":
                    if not anchors_existed:
                        await self._submit_job(ClearSnapshotAnchorsJob, session_id)
                    self._release_lease(session_id, replay=False)
                    return DeferredCandidate(session_id, "skipped_busy")
                if outcome.status not in {"migrated", "already_paginated"}:
                    error = outcome.message or f"migration returned {outcome.status}"
                    if outcome.failure_reason in ROLLOUT_UNREADABLE_REASONS:
                        return await self._condemn(
                            session_id, phase, error, lease,
                            reason=f"codex_migration_failed:{outcome.failure_reason}", path=candidate.file_path,
                        )
                    raise RolloutMigrationError(error)

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
                # Retain reconstruction intent until final metadata application.
                # Partial storage commits remain safe inputs for another rebuild.
                self._forced_rebuild.add(session_id)
                if search.is_initialized():
                    phase = "search invalidation"
                    await asyncio.to_thread(search.delete_session_documents, session_id)
                    await asyncio.to_thread(search.commit)

            return PreparedCandidate(
                session_id, lease, True,
                kind="migrated" if source_mode == HistoryMode.LEGACY else "replaced",
                registered=registered,
            )
        except asyncio.CancelledError:
            try:
                await self.runner.stop()
            finally:
                self._release_lease(session_id, replay=False, outcome='cancelled', fallback_path=candidate.file_path)
            raise
        except _RolloutMissing as error:
            return await self._condemn(
                session_id, phase, str(error), lease, reason=UNAVAILABLE_ROLLOUT_MISSING, path=candidate.file_path,
            )
        except Exception as error:  # noqa: BLE001 - one session failure must not stop the coordinator
            self._release_lease(
                session_id, replay=False, outcome='failed', error=str(error), fallback_path=candidate.file_path,
            )
            return FailedCandidate(session_id, phase, str(error))

    async def _condemn(
        self, session_id: str, phase: str, error: str, lease: asyncio.Lock | None, *, reason: str,
        path: Path | None = None,
    ) -> FailedCandidate:
        """Fail a candidate whose rollout cannot be shown, recording why for the UI."""

        try:
            await self._submit_job(MarkSessionUnavailableJob, session_id, reason)
        except asyncio.CancelledError:
            self._release_lease(session_id, replay=False, outcome='cancelled', fallback_path=path)
            raise
        except Exception:
            logger.exception("Could not flag Codex session %s as unavailable", session_id)
        self._release_lease(session_id, replay=False, outcome='failed', error=error, fallback_path=path)
        return FailedCandidate(session_id, phase, error, unavailable_reason=reason)

    # ------------------------------------------------------------------
    # Event pumps
    # ------------------------------------------------------------------

    async def _status_pump_loop(self, status_queue, run_id) -> None:
        while not self.ctx.stop_event.is_set():
            try:
                status = status_queue.get_nowait()
            except queue.Empty:
                try:
                    await asyncio.wait_for(
                        self.ctx.stop_event.wait(),
                        timeout=COMPUTE_STATUS_POLL_SECONDS,
                    )
                except TimeoutError:
                    pass
                continue
            if isinstance(status, dict):
                status = {**status, "_run_id": run_id}
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

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _tally(self) -> str:
        """Developer-facing one-line tally for the log summaries."""

        parts = [
            (self.stats["migrated"], "migrated"),
            (self.stats["registered"], "registered with Codex first"),
            (self.stats["replaced"], "rebuilt after an external rewrite"),
            (self.stats["compute"], "recomputed"),
            (len(self.deferred), "waiting for a running agent"),
            (self.stats["failed"], "failed"),
            (self.stats["unavailable"], "unavailable"),
        ]
        shown = [f"{count} {label}" for count, label in parts if count]
        return "Codex rollouts: " + (", ".join(shown) if shown else "nothing to migrate")

    def _detail(self) -> str | None:
        """User-facing line under the startup progress bar.

        Speaks only about the sessions that actually need Codex's migration:
        the top-level ones whose rollout was still legacy at startup. A pass
        with none of them (a plain compute-version bump on an already
        migrated store) shows no line — the progress bar alone is the truth
        there. ``done`` is every legacy session that ended well (migrated,
        or rebuilt / recomputed if it had been migrated externally
        meanwhile), ``set aside`` the ones waiting for a running agent,
        failed or unavailable.
        """

        legacy = self._legacy_ids & self.initial_ids
        if not legacy:
            return None
        set_aside = self._set_aside & legacy
        done = len(self.initial_ready & legacy)
        line = f"Migrating Codex legacy sessions ({done} / {len(legacy)}"
        if set_aside:
            line += f", {len(set_aside)} set aside"
        return line + ")"

    def _log_summary(self, *, force: bool = False, label: str = "Codex rollout migration progress") -> None:
        now = time.monotonic()
        due = (
            force
            or self._outcomes_since_summary >= _SUMMARY_EVERY_OUTCOMES
            or (self._outcomes_since_summary and now - self._last_summary_at >= _SUMMARY_EVERY_SECONDS)
        )
        if not due:
            return
        remaining = self._initial_total - len(self.initial_classified)
        logger.info(
            "%s: %s; %d of %d initial session(s) remaining, %.0fs elapsed",
            label, self._tally(), max(remaining, 0), self._initial_total, now - self._pass_started_at,
        )
        self._outcomes_since_summary = 0
        self._last_summary_at = now

    def _record_outcome(self, session_id: str, outcome: str | None, *, log: str | None = None) -> None:
        """Count one final outcome (``None`` for a deferral, which is not final) and log its line."""

        if outcome is not None:
            self.stats[outcome] += 1
        if session_id in self.initial_ids:
            if outcome in _GOOD_OUTCOMES:
                self._set_aside.discard(session_id)
            else:
                self._set_aside.add(session_id)
        self._outcomes_since_summary += 1
        started = self._started_at.pop(session_id, None)
        elapsed = f" in {time.monotonic() - started:.1f}s" if started is not None else ""
        if log:
            logger.info("Codex rollout migration: session %s %s%s", session_id, log, elapsed)
        self._log_summary()
        self._flush_final_summary()

    async def _confirm_initial_ready(self, session_ids: set[str]) -> None:
        unchecked = session_ids & self.initial_ids - self.initial_ready
        unchecked -= self.failures.keys() | self.deferred | self._forced_rebuild | self._unavailable_diagnoses.keys()
        if not unchecked:
            return
        ready = await sync_to_async(lambda: list(
            Session.objects.filter(
                id__in=unchecked, provider=Provider.CODEX, type=SessionType.SESSION,
                compute_version=self.ctx.compute_version, unavailable_reason__isnull=True,
            ).values_list("id", flat=True),
        ))()
        self.initial_ready.update(ready)

    async def _classify_blocked_tree(self, session_id: str) -> None:
        """Release startup classification for a root blocked by a native member."""
        if not self.initial_ids:
            return
        if session_id in self.initial_ids:
            if session_id not in self.initial_classified:
                await self._classify_initial(session_id, confirm_ready=False)
            return
        if self.initial_done.is_set():
            return
        from twicc.providers.compute_tree import NativeTreeError, resolve_native_root

        try:
            root_id = await sync_to_async(lambda: resolve_native_root(session_id))()
        except (NativeTreeError, Session.DoesNotExist):
            return
        if root_id in self.initial_ids:
            await self._classify_initial(root_id, confirm_ready=False)

    async def _classify_initial(self, session_id: str, *, confirm_ready: bool = True) -> None:
        if session_id not in self.initial_ids and not self.initial_done.is_set():
            return
        if session_id in self.initial_ids:
            self.initial_classified.add(session_id)
            if confirm_ready:
                await self._confirm_initial_ready({session_id})
        current = len(self.initial_ready)
        classified = len(self.initial_classified) == self._initial_total
        completed = classified and current == self._initial_total and not (
            self.failures or self.deferred or self._prepared or self._forced_rebuild or self._unavailable_diagnoses
        )
        await broadcast_startup_progress(
            "background_compute", current, self._initial_total,
            provider=Provider.CODEX.value, completed=completed, detail=self._detail(),
        )
        if classified:
            # Classification releases global indexing even when repairs continue.
            self.initial_done.set()
        if completed:
            self._final_summary_pending = True
            self._flush_final_summary()

    def _flush_final_summary(self) -> None:
        if not self._final_summary_pending:
            return
        if self.in_flight is not None or self.submitted or self.computed_not_applied:
            return
        self._final_summary_pending = False
        self._log_summary(force=True, label="Codex rollout migration: initial pass complete")

    def _release_lease(
        self, session_id: str, *, replay: bool = True,
        outcome: Literal['ready', 'failed', 'cancelled'] = 'ready', error: str | None = None,
        fallback_path: Path | None = None,
    ) -> None:
        """Release a session's gate and migrating flag; replay its file when asked.

        ``replay`` schedules the watcher catch-up (through
        ``on_session_released``) so lines appended while the watcher skipped
        the session are ingested. Not needed when nothing was replaced.
        """

        path = self.migration_paths.pop(session_id, None)
        lease = self.migration_leases.pop(session_id, None)
        if path is None and lease is None and fallback_path is None:
            return
        gate = gate_for(session_id)
        if gate.locked() and (lease is gate or (lease is None and path is not None)):
            gate.release()
        unmark_migrating(session_id)
        self.migrated_history.discard(session_id)
        if self.on_migration_released is not None:
            self.on_migration_released(MigrationRelease(
                session_id, path or fallback_path, outcome, replay, error, next(_release_tokens),
            ))
        if outcome == 'ready' and replay and path is not None and self.on_session_released is not None:
            task = asyncio.get_running_loop().create_task(self._replay(session_id, path))
            self._replays.add(task)
            task.add_done_callback(self._replays.discard)

    async def _replay(self, session_id: str, path: Path) -> None:
        try:
            await self.on_session_released(session_id, path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex watcher replay failed for session %s", session_id)

    async def _record_failure(self, failure: FailedCandidate) -> None:
        self.failed_this_run.add(failure.session_id)
        if failure.unavailable_reason:
            self._unavailable_diagnoses[failure.session_id] = failure.unavailable_reason
        elif failure.session_id in self._unavailable_diagnoses:
            failure = failure._replace(unavailable_reason=self._unavailable_diagnoses[failure.session_id])
        self.failures[failure.session_id] = failure
        if failure.session_id in self.migrated_history:
            self._forced_rebuild.add(failure.session_id)
        self.deferred.discard(failure.session_id)
        self._release_lease(failure.session_id, replay=False, outcome='failed', error=failure.error)
        self._prepared.pop(failure.session_id, None)
        self._prepared_rebuild_generation.pop(failure.session_id, None)
        self._record_outcome(failure.session_id, "unavailable" if failure.unavailable_reason else "failed")
        delay = min(self._retry_delays.get(failure.session_id, 0.5) * 2, 60.0)
        self._retry_delays[failure.session_id] = delay
        self._retry_at[failure.session_id] = time.monotonic() + delay
        await self._classify_initial(failure.session_id)
        await self._classify_blocked_tree(failure.session_id)
        logger.error(
            "Codex background compute failed: session=%s phase=%s error=%s%s",
            failure.session_id,
            failure.phase,
            failure.error,
            f" (session flagged unavailable: {failure.unavailable_reason})" if failure.unavailable_reason else "",
        )

    async def _handle_worker_status(self, status: object) -> None:
        if not isinstance(status, dict):
            return
        run_id = status.get('_run_id', getattr(self.ctx, 'run_id', None))
        if run_id in self._retired_run_ids or run_id != getattr(self.ctx, 'run_id', None):
            return
        if status.get('type') == 'transport_failed':
            raise RuntimeError(status.get('error') or 'compute transport interrupted')
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
        if signal.run_id is not None and (
            signal.run_id in self._retired_run_ids or signal.run_id != getattr(self.ctx, "run_id", None)
        ):
            return
        if signal.completed_root_id is not None:
            self.ctx.command_queue.put_nowait({'completed_root_id': signal.completed_root_id})
        session_id = signal.session_id
        arrived_before_computed = session_id in self.submitted
        if arrived_before_computed:
            self.applied_before_computed.add(session_id)
        self.computed_not_applied.discard(session_id)

        if signal.outcome == "missing":
            exists = await sync_to_async(Session.objects.filter(id=session_id).exists)()
            if not exists:
                self._forget_deleted_recovery(session_id)
                await self._classify_initial(session_id)
                return

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

        if session_id in self._unavailable_diagnoses and signal.outcome != "applied":
            await self._record_failure(FailedCandidate(
                session_id, "metadata apply", f"diagnosed repair application returned {signal.outcome}",
            ))
            return
        if signal.outcome == "superseded":
            outdated = await sync_to_async(lambda: Session.objects.filter(pk=session_id).exclude(
                compute_version=self.ctx.compute_version,
            ).exists())()
            if outdated:
                await self._record_failure(FailedCandidate(
                    session_id, "metadata apply", "source revision superseded before publication",
                ))
                return

        if signal.outcome == "applied" and session_id in self._unavailable_diagnoses:
            await self._submit_job(MarkSessionUnavailableJob, session_id, None)
            self._unavailable_diagnoses.pop(session_id, None)
        self._active_diagnoses.discard(session_id)
        self.deferred.discard(session_id)
        self.failed_this_run.discard(session_id)
        self.failures.pop(session_id, None)
        self._retry_at.pop(session_id, None)
        self._retry_delays.pop(session_id, None)
        self._process_delay = 1.0
        if migrated and signal.outcome == "applied" and (
            self._prepared_rebuild_generation.get(session_id, 0) == self._rebuild_generation.get(session_id, 0)
        ):
            self._forced_rebuild.discard(session_id)
        self._prepared_rebuild_generation.pop(session_id, None)
        self._release_lease(session_id)
        prepared = self._prepared.pop(session_id, None)
        kind = prepared.kind if prepared is not None else "compute"
        if prepared is not None and prepared.registered:
            self.stats["registered"] += 1
        self._record_outcome(session_id, kind, log={
            "migrated": "migrated by Codex and rebuilt" + (" (registered with Codex first)" if prepared and prepared.registered else ""),
            "replaced": "rebuilt from its rewritten rollout",
            "compute": "metadata recomputed",
        }[kind] + (f", apply {signal.outcome}" if signal.outcome != "applied" else ""))
        if signal.outcome in {"applied", "superseded"}:
            from twicc.search_indexing_task import request_session_reindex

            request_session_reindex(session_id)
        await self._classify_initial(session_id)

    def _forget_deleted_recovery(self, session_id: str) -> None:
        """Drop repair intent only after database deletion is confirmed."""
        self._release_lease(session_id, replay=False, outcome='cancelled')
        self.migrated_history.discard(session_id)
        self._active_diagnoses.discard(session_id)
        self._unavailable_diagnoses.pop(session_id, None)
        self.failures.pop(session_id, None)
        self.failed_this_run.discard(session_id)
        self._retry_at.pop(session_id, None)
        self._retry_delays.pop(session_id, None)
        self._forced_rebuild.discard(session_id)
        self._prepared.pop(session_id, None)
        self._prepared_rebuild_generation.pop(session_id, None)
        self.deferred.discard(session_id)
        self._started_at.pop(session_id, None)

    async def _prune_deleted_recovery(self, candidate_ids: set[str]) -> None:
        tracked = set(self.failures) | set(self._unavailable_diagnoses) | self._forced_rebuild
        unchecked = tracked - candidate_ids - self.submitted - self.computed_not_applied
        if not unchecked:
            return
        existing = await sync_to_async(lambda: set(
            Session.objects.filter(id__in=unchecked).values_list("id", flat=True),
        ))()
        for session_id in unchecked - existing:
            self._forget_deleted_recovery(session_id)
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
            detail=self._detail(),
        )
        if self._initial_total == 0:
            self.initial_done.set()

    @staticmethod
    def _classify_sources(candidates: list[CodexComputeCandidate]) -> tuple[Counter[str], set[str]]:
        """Count the stale sessions by source format (first line of each rollout).

        Also returns the ids whose rollout is still legacy: they are the ones
        the user-facing progress line talks about (see :meth:`_detail`).
        """

        tally: Counter[str] = Counter()
        legacy_ids: set[str] = set()
        for candidate in candidates:
            try:
                path = Path(candidate.file_path)
                if not path.exists():
                    tally["missing"] += 1
                    continue
                meta = extract_session_meta(path)
            except (TypeError, OSError):
                meta = None
            if meta is None:
                tally["unreadable"] += 1
            else:
                tally[meta.history_mode.value] += 1
                if meta.history_mode == HistoryMode.LEGACY:
                    legacy_ids.add(candidate.session_id)
        return tally, legacy_ids

    def _absorb_rebuild_requests(self) -> None:
        """Retain forced reconstruction intent and lift per-run exclusions."""

        for session_id in take_rebuild_requests():
            self._forced_rebuild.add(session_id)
            self._rebuild_generation[session_id] = self._rebuild_generation.get(session_id, 0) + 1
            self.failed_this_run.discard(session_id)
            self.failures.pop(session_id, None)
            self._retry_at.pop(session_id, None)
            self.deferred.discard(session_id)

    # ------------------------------------------------------------------
    # Worker run lifecycle
    # ------------------------------------------------------------------

    def _start_run(self) -> None:
        """Arm a DB-writer run and spawn the CPU worker (idle between runs)."""

        run_id, done_future = arm_compute_completion(
            Provider.CODEX,
            display_session_ids=None,
            total_display=0,
            applied_queue=self.applied_queue,
            publish_progress=False,
        )
        self._retired_run_ids.clear()
        self.ctx.run_id = run_id
        self._done_future = done_future
        def run_finished(future):
            if not future.cancelled() and future.exception() is not None and not self.ctx.stop_event.is_set():
                self.events.put_nowait(('worker', {
                    'type': 'transport_failed', 'error': str(future.exception()), '_run_id': run_id,
                }))
        done_future.add_done_callback(run_finished)
        if self._queues_closed:
            self.ctx.command_queue, self.ctx.status_queue = self._new_queues()
            self._queues_closed = False
        elif self.ctx.status_queue is None:
            self.ctx.status_queue = self._new_queues()[1]
        self.run_active = True
        start_compute_process(self.ctx)
        self._status_pump = asyncio.create_task(self._status_pump_loop(self.ctx.status_queue, run_id))

    async def _finish_run(self) -> None:
        """Stop the worker once nothing is queued, keeping the coordinator alive."""

        if not self.run_active:
            return
        self.run_active = False
        self.ctx.command_queue.put_nowait(None)
        if self._done_future is not None:
            failed_count = await self._done_future
            if failed_count and not self.failures:
                logger.error("Codex DB writer reported %d failed compute result(s)", failed_count)
        self._retired_run_ids.add(self.ctx.run_id)
        self._done_future = None
        if self._status_pump is not None:
            self._status_pump.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._status_pump
            self._status_pump = None
        # Not ``stop_background_task``: that one sets ``ctx.stop_event``,
        # which is the coordinator's own exit condition.
        await stop_compute_worker(self.ctx)
        self._queues_closed = True

    async def _retire_run(self) -> None:
        """Drain owned cleanup even when cancellation repeats."""
        await self._drain_owned_cleanup(self._drain_run())

    @staticmethod
    async def _drain_owned_cleanup(cleanup: Awaitable[None]) -> None:
        owned = asyncio.create_task(cleanup)
        cancelled = False
        while not owned.done():
            try:
                await asyncio.shield(owned)
            except asyncio.CancelledError:
                cancelled = True
        owned.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _drain_run(self) -> None:
        """Fence and drain the old writer before releasing migration gates."""
        run_id = getattr(self.ctx, "run_id", None)
        if run_id is not None:
            self._retired_run_ids.add(run_id)
        self.run_active = False
        if self._status_pump is not None:
            self._status_pump.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._status_pump
            self._status_pump = None
        delay = 1.0
        while True:
            try:
                await retire_compute_run(self.ctx, self._done_future)
                break
            except Exception:
                logger.exception("Codex run retirement failed; retaining old resources for retry")
            if not await _wait_for_retry_delay(self.ctx.stop_event, delay):
                # Shutdown still drains owned resources, but never starts a new run.
                await retire_compute_run(self.ctx, self._done_future)
                break
            delay = min(delay * 2, 60.0)
        self._clear_retired_run()

    def _clear_retired_run(self) -> None:
        self._done_future = None
        self._queues_closed = True
        self._forced_rebuild.update(self.migrated_history)
        for session_id in set(self.migration_leases) | set(self.migration_paths):
            self._release_lease(session_id, replay=False, outcome='failed', error='compute run retired')
        self.submitted.clear()
        self.computed_not_applied.clear()
        self.applied_before_computed.clear()
        self.worker_errors.clear()
        self._prepared.clear()
        self._prepared_rebuild_generation.clear()
        self._started_at.clear()
        self.in_flight = None

    @staticmethod
    def _new_queues():
        spawn = multiprocessing.get_context("spawn")
        return spawn.Queue(), queue.Queue(maxsize=200)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _select_candidate(self):
        """Admit one root, then refresh only its deepest required source."""
        from twicc.providers.compute_tree import (
            NativeTreeError, load_native_tree, next_root_eligibility, resolve_native_roots, select_outdated_root,
        )
        from twicc.providers.compute_selection import select_tree_source

        now = time.monotonic()
        wall_now = time.time()
        cutoff = wall_now - COMPUTE_MIN_SOURCE_AGE_SECONDS
        self._blocked_roots = {root: deadline for root, deadline in self._blocked_roots.items() if deadline > now}
        self._blocked_diagnoses = {
            root: sources for root, sources in self._blocked_diagnoses.items() if root in self._blocked_roots
        }
        outstanding = self.submitted | self.computed_not_applied
        await self._prune_deleted_recovery(set())
        recovery_ids = (
            self._forced_rebuild | self.failures.keys() | self.deferred
            | self._unavailable_diagnoses.keys() | self._active_diagnoses
        )
        # Unavailable histories may have a current root. Probe one bounded page
        # outside normal version selection; retries retain their original version.
        def recovery_roots():
            blocked_sources = set().union(*self._blocked_diagnoses.values())
            diagnosed = set(Session.objects.filter(
                provider=Provider.CODEX, unavailable_reason__isnull=False,
            ).exclude(pk__in=recovery_ids | blocked_sources).order_by('-mtime').values_list(
                'id', flat=True,
            )[:_UNAVAILABLE_RECOVERY_PAGE_SIZE])
            roots = resolve_native_roots(recovery_ids | diagnosed | outstanding)
            ranked = list(Session.objects.filter(
                pk__in=set(roots.values()) | self._startup_child_roots,
                provider=Provider.CODEX, type=SessionType.SESSION, parent_session__isnull=True,
            ).order_by('-mtime').values_list('id', flat=True))
            self._startup_child_roots.intersection_update(ranked)
            return [root for root in ranked if root not in self._blocked_roots], diagnosed, roots

        # Read at most one exceptional page per selection. Blocked diagnoses
        # stay attached to their retry roots, so later pages can make progress.
        recovery_loaded = False
        recovery_roots_left = []
        diagnosed_roots = {}
        diagnosed = set()
        while True:
            if self._active_root is None:
                if not recovery_loaded:
                    recovery_roots_left, diagnosed, diagnosed_roots = await sync_to_async(recovery_roots)()
                    recovery_ids = recovery_ids | diagnosed
                    recovery_loaded = True
                self._active_root = recovery_roots_left.pop(0) if recovery_roots_left else None
                self._active_diagnoses = {
                    sid for sid in diagnosed if diagnosed_roots.get(sid) == self._active_root
                }
                if self._active_root is None:
                    self._active_root = await sync_to_async(lambda: select_outdated_root(
                        Provider.CODEX, self.ctx.compute_version, cutoff=cutoff,
                        excluded_root_ids=self._blocked_roots,
                    ))()
            if self._active_root is None:
                deadline = await sync_to_async(lambda: next_root_eligibility(
                    Provider.CODEX, self.ctx.compute_version, cutoff=cutoff,
                    excluded_root_ids=self._blocked_roots,
                ))()
                deadlines = list(self._blocked_roots.values())
                if deadline is not None:
                    deadlines.append(now + max(0, deadline - wall_now))
                pending = bool(deadlines or outstanding or recovery_ids)
                return None, recovery_ids, pending, min(deadlines, default=None)
            root = self._active_root
            excluded = self.deferred | {sid for sid, deadline in self._retry_at.items() if deadline > now}
            if outstanding:
                excluded |= self.failures.keys()
            def select_member(root=root, excluded=excluded, recovery_ids=recovery_ids):
                # A current diagnosed member remains required even when the
                # bounded recovery page discovered a different native root.
                tree = load_native_tree(root)
                diagnosed_members = set(Session.objects.filter(
                    pk__in=tree.member_ids, unavailable_reason__isnull=False,
                ).values_list('id', flat=True))
                selection = select_tree_source(
                    root, self.ctx.compute_version, cutoff=cutoff, outstanding=outstanding,
                    excluded_source_ids=excluded, forced_source_ids=recovery_ids | diagnosed_members,
                )
                return selection, diagnosed_members
            try:
                selection, diagnosed_members = await sync_to_async(select_member)()
                self._active_diagnoses.update(diagnosed_members)
                recovery_ids |= diagnosed_members
            except (NativeTreeError, Session.DoesNotExist):
                await self._classify_blocked_tree(root)
                self._blocked_roots[root] = now + 60
                self._blocked_diagnoses[root] = self._active_diagnoses | {
                    sid for sid in diagnosed if diagnosed_roots.get(sid) == root
                }
                self._active_root = None
                self._active_diagnoses.clear()
                continue
            if selection.source_id is not None:
                candidates = await _load_stale_candidates(
                    self.ctx.compute_version, source_ids={selection.source_id},
                    forced_ids=frozenset(recovery_ids),
                )
                candidate = next((item for item in candidates if item.session_id == selection.source_id), None)
                if candidate is not None:
                    return candidate, recovery_ids, True, None
                self._active_root = None
                continue
            if outstanding:
                return None, recovery_ids, True, None
            self._active_root = None
            if not selection.required_pending:
                self._startup_child_roots.discard(root)
                self._active_diagnoses.clear()
                await self._classify_initial(root)
                continue
            await self._classify_blocked_tree(root)
            # Source age and recovery backoff are independent deadlines.
            deadlines = [deadline for sid, deadline in self._retry_at.items() if deadline > now]
            if selection.next_eligible_at is not None:
                deadlines.append(now + max(0, selection.next_eligible_at - wall_now))
            self._blocked_roots[root] = min(deadlines, default=now + _EXTERNAL_WRITER_RETRY_SECONDS)
            self._blocked_diagnoses[root] = self._active_diagnoses | {
                sid for sid in diagnosed if diagnosed_roots.get(sid) == root
            }
            self._active_diagnoses.clear()

    async def run(self) -> None:
        """Keep actual work pending until selection and owned writer drain finish."""
        def wakeup(session_id):
            self._source_generation += 1
            if self.work_state is not None:
                self.work_state.set_pending(Provider.CODEX, True)
            self.events.put_nowait(('source', session_id))

        if self.work_state is not None:
            self.work_state.set_pending(Provider.CODEX, True)
            self.work_state.register_source_wakeup(Provider.CODEX, wakeup)
        try:
            await self._run()
        finally:
            try:
                await self._drain_owned_cleanup(self._shutdown())
            finally:
                if self.work_state is not None:
                    self.work_state.unregister_source_wakeup(Provider.CODEX, wakeup)
                    if self._shutdown_drained:
                        self.work_state.set_pending(Provider.CODEX, False)

    async def _run(self) -> None:
        self._absorb_rebuild_requests()
        async def load_initial(_ids):
            self._absorb_rebuild_requests()
            return await _load_stale_candidates(
                self.ctx.compute_version, forced_ids=frozenset(self._forced_rebuild),
            )
        initial_candidates = await read_compute_state(load_initial, [], self.ctx.stop_event)
        if initial_candidates is None or self.ctx.stop_event.is_set():
            self.initial_done.set()
            return
        from twicc.providers.compute_tree import resolve_native_roots

        child_ids = {candidate.session_id for candidate in initial_candidates
                     if candidate.session_type == SessionType.SUBAGENT}
        if child_ids:
            roots = await sync_to_async(lambda: resolve_native_roots(child_ids))()
            self._startup_child_roots.update(roots.values())
        await self._initialize_progress(initial_candidates)
        cutoff = time.time() - COMPUTE_MIN_SOURCE_AGE_SECONDS
        for candidate in initial_candidates:
            if candidate.mtime > cutoff:
                await self._classify_blocked_tree(candidate.session_id)
        if initial_candidates:
            sources, self._legacy_ids = await asyncio.to_thread(self._classify_sources, initial_candidates)
            logger.info(
                "Codex rollout migration: %d stale session(s) at startup — %d legacy (to migrate through Codex), "
                "%d paginated (rebuild or recompute), %d missing from disk, %d unreadable",
                len(initial_candidates), sources["legacy"], sources["paginated"],
                sources["missing"], sources["unreadable"],
            )
            await sync_to_async(load_project_directories)()
            await sync_to_async(load_project_git_roots)()
            if self._detail() is not None:
                # The first broadcast went out before the sources were known;
                # publish the migration line now that we know there is one.
                await broadcast_startup_progress(
                    "background_compute", 0, self._initial_total,
                    provider=Provider.CODEX.value, completed=False, detail=self._detail(),
                )
        else:
            logger.info("Codex background compute: no sessions to process at startup")

        self._pumps = [
            asyncio.create_task(self._applied_pump()),
            asyncio.create_task(self._wake_pump()),
        ]

        while not self.ctx.stop_event.is_set():
            try:
                generation = self._source_generation
                self._absorb_rebuild_requests()
                candidate, forced, work_pending, next_deadline = await self._select_candidate()
                if self.work_state is not None:
                    self.work_state.set_pending(
                        Provider.CODEX, work_pending or self.run_active or generation != self._source_generation,
                    )

                if (
                    self.in_flight is None
                    and len(self.submitted | self.computed_not_applied) < CODEX_MAX_OUTSTANDING
                    and candidate is not None
                ):
                    if not self.run_active:
                        self._start_run()
                    prepared = await self.prepare_candidate(candidate)
                    if isinstance(prepared, DeferredCandidate):
                        self.deferred.add(prepared.session_id)
                        self._retry_at[prepared.session_id] = time.monotonic() + _EXTERNAL_WRITER_RETRY_SECONDS
                        if prepared.session_id not in self._logged_deferred:
                            self._logged_deferred.add(prepared.session_id)
                            self._record_outcome(
                                prepared.session_id, None,
                                log="deferred: " + (
                                    "a TwiCC agent is running it" if prepared.reason == "active"
                                    else "another Codex process holds its writer lock"
                                ),
                            )
                        await self._classify_initial(prepared.session_id)
                        await self._classify_blocked_tree(prepared.session_id)
                        continue
                    if isinstance(prepared, FailedCandidate):
                        await self._record_failure(prepared)
                        continue

                    if prepared.migration_lease is not None:
                        self.migration_leases[prepared.session_id] = prepared.migration_lease
                    if prepared.migrated_history:
                        self.migrated_history.add(prepared.session_id)
                    self._prepared[prepared.session_id] = prepared
                    self._prepared_rebuild_generation[prepared.session_id] = self._rebuild_generation.get(
                        prepared.session_id, 0,
                    )
                    self.submitted.add(prepared.session_id)
                    self.in_flight = prepared.session_id
                    try:
                        await self._submit_job(InitializeComputeStorageJob, prepared.session_id)
                    except Exception as error:
                        self.submitted.discard(prepared.session_id)
                        self.in_flight = None
                        await self._record_failure(FailedCandidate(prepared.session_id, "initialize", str(error)))
                        continue
                    from twicc.providers.compute_selection import tree_completion_context

                    context = await sync_to_async(lambda prepared=prepared, forced=forced: tree_completion_context(
                        prepared.session_id, self.ctx.compute_version,
                        forced_source_ids=forced,
                        outstanding=(self.submitted | self.computed_not_applied) - {prepared.session_id},
                    ))()
                    self.ctx.command_queue.put_nowait({
                        "session_id": prepared.session_id, "tree_completion": context,
                    })
                    continue

                pending = bool(self.submitted or self.computed_not_applied)
                if (
                    self.run_active
                    and candidate is None
                    and not pending
                    and self.in_flight is None
                    and not self.deferred
                ):
                    # Nothing left to feed the worker and no busy session to
                    # retry shortly: stop it, stay alive. Deferred sessions
                    # keep the idle worker around rather than respawning it
                    # at every 30-second retry.
                    await self._finish_run()
                    if not self.initial_done.is_set():
                        # Every initial session was classified through the
                        # normal paths; this is a belt-and-braces guard.
                        self.initial_done.set()

                if self.work_state is not None:
                    self.work_state.set_pending(
                        Provider.CODEX, work_pending or self.run_active or generation != self._source_generation,
                    )
                timeout = None if next_deadline is None else max(0, next_deadline - time.monotonic())
                try:
                    event_type, payload = await asyncio.wait_for(self.events.get(), timeout=timeout)
                except TimeoutError:
                    self.deferred = {sid for sid in self.deferred if self._retry_at.get(sid, 0) > time.monotonic()}
                    continue
                if event_type == "worker":
                    await self._handle_worker_status(payload)
                elif event_type == "applied" and isinstance(payload, ComputeApplied):
                    await self._handle_applied(payload)
                elif event_type in {"wake", "source"}:
                    self.deferred.clear()
                    self._blocked_roots.clear()
                    if event_type == 'source':
                        self._retry_at.pop(payload, None)
            except Exception:
                logger.exception("Codex compute run failed; retiring before recovery")
                faulty = self.in_flight
                await self._retire_run()
                if faulty is not None:
                    await self._record_failure(FailedCandidate(faulty, "transport", "compute run interrupted"))
                    continue
                if not await _wait_for_retry_delay(self.ctx.stop_event, self._process_delay):
                    break
                self._process_delay = min(self._process_delay * 2, 60.0)

    async def _shutdown(self) -> None:
        """Drain provider cleanup before cancellation leaves the coordinator."""
        await self.runner.stop()
        for task in self._pumps:
            task.cancel()
        for task in self._pumps:
            with suppress(Exception, asyncio.CancelledError):
                await task
        if self._status_pump is not None:
            self._status_pump.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._status_pump
        if self._done_future is not None or self.run_active:
            await self._retire_run()
        for session_id in set(self.migration_leases) | set(self.migration_paths):
            self._release_lease(session_id, replay=False, outcome='cancelled')
        for task in self._replays:
            task.cancel()
        if self._replays:
            await asyncio.gather(*self._replays, return_exceptions=True)
        await stop_background_task(self.ctx)
        self._shutdown_drained = True
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
    on_session_released: SessionReleasedCallback | None = None,
    on_migration_released: MigrationReleasedCallback | None = None,
    *, work_state,
) -> None:
    """Run the migration-aware Codex coordinator for the provider's lifetime."""

    await CodexComputeCoordinator(
        ctx, initial_done, on_session_released, on_migration_released, work_state=work_state,
    ).run()
