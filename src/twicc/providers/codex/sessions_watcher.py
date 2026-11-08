"""
Codex file watcher.

Adapt :class:`~twicc.providers.sessions_watcher.BaseSessionsWatcher` to the
Codex layout and plain/compressed rollout transitions. Logical JSONL paths
own queue state. Validated compressed snapshots stay pinned across live
slices. Shared ingestion, broadcasts, search indexing and polling stay in
the base.

Codex uses a date-based layout — ``<codex home>/sessions/YYYY/MM/DD/rollout-*.jsonl``
— with no per-project subfolder, so we cannot derive ``project_id`` from
the path itself. :meth:`CodexSessionsWatcher.parse_session_file` instead
reads the first JSONL line (the ``session_meta`` event) to recover the
session UUID and the working directory, then maps that ``cwd`` to the
canonical TwiCC project id via :func:`twicc.paths.path_to_project_id` —
matching exactly what :func:`twicc.providers.codex.initial_sync.sync_all`
does for brand-new files. The same first-line read also surfaces a
parent session id when ``payload.source.subagent.thread_spawn`` is set,
so we can mark the file as a :class:`SessionType.SUBAGENT` and let the
base watcher wire it under its parent. Provider-internal Guardian rollouts
(``payload.source.subagent.other == "guardian"``) are rejected by the same
metadata parser before the base watcher can create a TwiCC session.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from watchfiles import Change

from twicc.core.content_storage.readers import effective_current, iter_current_items
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers.helpers import AgentSettings
from twicc.paths import path_to_project_id
from twicc.provider_homes import codex_sessions_dir
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.db_writer import submit_async_job
from twicc.providers.session_change_queue import MigrationRelease, PathDrainTarget
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    ParsedSessionFile,
    SessionChangeResult,
    _read_source_snapshot,
    get_session_by_id,
)

from .canonical import ended_command_process_id
from .compute import announced_running_process_id, rollout_line_epoch
from .compute import get_compute as _get_compute
from .initial_sync import extract_session_meta, is_session_file
from .migration_gate import gate_for, is_migrating, request_rebuild
from .rollout_sources import (
    PreparedRollout, SourceFingerprint, is_rollout_path, logical_rollout_path, prepare_rollout,
    resolve_rollout, source_is_current,
)
from .source_binding import CodexSourcePayload, ReconcileCodexSourceJob, source_checkpoint
from .rollout_migration import (
    HistoryMode,
    MarkSessionRebuildJob,
    RolloutMigrationError,
    get_db_history_mode,
)

logger = logging.getLogger(__name__)


def _prepare_plain_rollout(path: Path) -> PreparedRollout:
    """Capture a transition fingerprint without ever decoding under a callback."""
    with path.open('rb') as source:
        stat = os.fstat(source.fileno())
        fingerprint = SourceFingerprint(path, stat.st_dev, stat.st_ino, stat.st_size,
                                        stat.st_mtime_ns, stat.st_ctime_ns)
    return PreparedRollout(path, path, stat.st_size, stat.st_mtime, fingerprint)


class _GateWakeTarget(NamedTuple):
    source_generation: object
    release_token: int


class _PreparationAttempt(NamedTuple):
    lifecycle: object
    generation: object
    event_token: int
    release_token: int
    stop: threading.Event


class CodexSessionsWatcher(BaseSessionsWatcher):
    """File watcher for Codex's ``<codex home>/sessions/`` layout.

    Recognized layout: ``YYYY/MM/DD/rollout-<uuid>.jsonl`` →
    :class:`SessionType.SESSION` or :class:`SessionType.SUBAGENT` when the
    first-line ``session_meta`` carries a
    ``payload.source.subagent.thread_spawn.parent_thread_id``. Subagents
    use the exact same on-disk layout as top-level sessions; they're
    discriminated solely by that field.
    """

    def __init__(self) -> None:
        super().__init__()
        # Resolved at instantiation (``get_watcher()`` creates the singleton
        # lazily), never at import: the home is configurable per instance.
        self.projects_dir = codex_sessions_dir()
        # Sessions whose stored history is known to be paginated: the
        # rewrite check (legacy in DB, paginated on disk) is skipped for
        # them, so a live paginated session costs no extra DB read per event.
        self._paginated_in_db: set[str] = set()
        self._gate_wakes: dict[str, asyncio.Task] = {}
        self._gate_wake_targets: dict[str, dict[Path, _GateWakeTarget]] = {}
        self._association_sessions: dict[Path, Session] = {}
        self._prepared: dict[Path, PreparedRollout] = {}
        self._active_prepared: set[Path] = set()
        self._preparations: dict[Path, asyncio.Task] = {}
        self._preparation_attempts: dict[Path, _PreparationAttempt] = {}
        self._preparation_waiting: dict[Path, None] = {}
        self._preparation_errors: dict[Path, OSError] = {}
        self._needs_resolution: set[Path] = set()
        self._physical_sources: dict[Path, Path] = {}
        self._target_waiters: dict[Path, list[asyncio.Future]] = {}
        self._preparation_executor: ThreadPoolExecutor | None = None

    def enumerate_session_files(self, projects_dir: Path) -> list[Path]:
        selected = {}
        for path in projects_dir.rglob('*'):
            if is_rollout_path(path) and path.is_file():
                logical = logical_rollout_path(path)
                if logical not in selected or path.suffix != '.zst':
                    selected[logical] = path
        return sorted(selected.values())

    def _enqueue(self, path: Path, change: Change) -> None:
        if not is_rollout_path(path):
            super()._enqueue(path, change)
            return
        logical = logical_rollout_path(path)
        self._preparation_errors.pop(logical, None)
        if path.suffix == '.zst' or change == Change.deleted:
            self._needs_resolution.add(logical)
            # A physical deletion is not yet a logical deletion. Keep samples,
            # parsed identity and admitted finite targets until sibling resolution.
            change = Change.modified
        super()._enqueue(logical, change)

    def _attempt_current(self, path: Path, attempt: _PreparationAttempt) -> bool:
        return (not self._queue.closed and attempt.lifecycle is self._queue
                and attempt.generation is self._queue.source_generation(path)
                and attempt.event_token == self._queue.event_token(path)
                and attempt.release_token == self._queue.release_token(path))

    def _start_preparations(self) -> None:
        while (self._preparation_waiting and not self._queue.closed
               and len(self._preparations) + len(self._prepared) < 2):
            path = next((path for path in self._preparation_waiting
                         if path not in self._preparations and path not in self._prepared), None)
            if path is None:
                break
            self._preparation_waiting.pop(path)
            attempt = _PreparationAttempt(
                self._queue, self._queue.source_generation(path), self._queue.event_token(path),
                self._queue.release_token(path), threading.Event(),
            )
            self._preparation_attempts[path] = attempt
            if self._preparation_executor is None:
                self._preparation_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='codex-rollout')
            self._preparations[path] = asyncio.create_task(
                self._prepare_source(path, attempt), name=f'codex-rollout-prepare-{path.name}',
            )

    async def _prepare_source(self, path: Path, attempt: _PreparationAttempt) -> None:
        prepared = None
        error = None
        fingerprint = None

        def prepare():
            nonlocal fingerprint
            # Failed decoding returns no PreparedRollout. Retain source evidence
            # so its error cannot terminate catch-up of a newer selection.
            selected = resolve_rollout(path)
            fingerprint = _prepare_plain_rollout(selected).fingerprint
            return prepare_rollout(path, stop=attempt.stop)

        try:
            try:
                prepared = await asyncio.get_running_loop().run_in_executor(
                    self._preparation_executor, prepare,
                )
                current = await asyncio.to_thread(source_is_current, prepared.fingerprint)
            except OSError as failure:
                error = failure
                try:
                    if fingerprint is not None:
                        current = await asyncio.to_thread(source_is_current, fingerprint)
                    elif isinstance(failure, FileNotFoundError):
                        # A missing open is obsolete absence evidence when a
                        # source exists now, including a same-path replacement.
                        await asyncio.to_thread(resolve_rollout, path)
                        current = False
                    else:
                        current = True
                except OSError:
                    # Unreadability does not prove that the source changed.
                    current = True
            if not self._attempt_current(path, attempt):
                # A newer admitted event can have deferred behind this attempt.
                # Resume that attempt without inventing a fresh filesystem event.
                if (not self._queue.closed and attempt.lifecycle is self._queue
                        and attempt.generation is self._queue.source_generation(path)
                        and attempt.release_token == self._queue.release_token(path)
                        and self._queue.has_pending_change(path)):
                    self._queue.resume_pending_change(path)
                    self._ensure_consumer()
                return
            if not current:
                self._preparation_waiting[path] = None
                return
            if error is not None:
                self._preparation_errors[path] = error
            elif prepared is not None:
                self._prepared[path] = prepared
                prepared = None
            self._queue.resume_pending_change(path)
            self._ensure_consumer()
        finally:
            if prepared is not None:
                prepared.close()
            self._preparations.pop(path, None)
            self._preparation_attempts.pop(path, None)
            self._start_preparations()

    def _release_prepared(self, path: Path) -> None:
        prepared = self._prepared.pop(path, None)
        if prepared is not None:
            prepared.close()
            self._start_preparations()

    def _settle_targets(self, path: Path, target: PathDrainTarget | None) -> None:
        for future in self._target_waiters.pop(path, []):
            if not future.done():
                future.set_result(target)

    async def _observe_source(self, path: Path) -> PathDrainTarget:
        prepared = self._prepared.get(path)
        if prepared is None:
            if self._physical_sources.get(path, path).suffix == '.zst':
                self._needs_resolution.add(path)
                return PathDrainTarget(self._queue.source_generation(path), 0)
            try:
                target = await super()._observe_source(path)
                self._physical_sources[path] = path
            except FileNotFoundError:
                self._needs_resolution.add(path)
                return PathDrainTarget(self._queue.source_generation(path), 0)
        else:
            async with self._source_lock:
                previous = self._sources.get(path)
                fingerprint = prepared.fingerprint
                identity = (fingerprint.device, fingerprint.inode)
                # An exact sibling transition can retain its byte coordinates.
                # Temporary inode values must never identify the physical source.
                if previous is not None and self._physical_sources.get(path) != prepared.physical_path:
                    previous = previous._replace(identity=identity)
                snapshot, replaced = await asyncio.to_thread(
                    _read_source_snapshot, prepared.read_path, previous, identity=identity,
                )
                if previous is None or replaced:
                    if previous is not None:
                        self._replaced_paths.add(path)
                    self._invalidate_parsed_path(path)
                    self._queue.observe_source(path, object())
                    self._line_limits.pop(path, None)
                self._sources[path] = snapshot
                self._physical_sources[path] = prepared.physical_path
                target = PathDrainTarget(self._queue.source_generation(path), snapshot.end_offset)
        self._settle_targets(path, target)
        return target

    async def _process_change(self, change_type: Change, path_str: str, channel_layer) -> SessionChangeResult:
        path = logical_rollout_path(Path(path_str))
        if not is_rollout_path(path):
            return await super()._process_change(change_type, str(path), channel_layer)
        special = (path in self._needs_resolution or path in self._prepared
                   or self._physical_sources.get(path, path).suffix == '.zst')
        if not special:
            result = await super()._process_change(change_type, str(path), channel_layer)
            if path not in self._needs_resolution:
                if result.disposition in ('drained', 'failed'):
                    self._release_prepared(path)
                return result
        try:
            selected = await asyncio.to_thread(resolve_rollout, path)
            if path in self._preparation_errors:
                error = self._preparation_errors.pop(path)
                logger.error('Error preparing Codex rollout %s: %s', path, error)
                self._settle_targets(path, None)
                return SessionChangeResult('failed')
            prepared = self._prepared.get(path)
            if prepared is not None and not await asyncio.to_thread(source_is_current, prepared.fingerprint):
                self._release_prepared(path)
                prepared = None
            if prepared is None:
                if selected.suffix == '.zst':
                    if path not in self._preparations:
                        self._preparation_waiting[path] = None
                        self._start_preparations()
                    return SessionChangeResult('deferred')
                if self._physical_sources.get(path, path).suffix != '.zst':
                    # A deletion/recreation of plain JSONL retains the existing
                    # callback path. Reparse before testing old migration state.
                    self._needs_resolution.discard(path)
                    self._invalidate_parsed_path(path)
                    return await super()._process_change(Change.modified, str(path), channel_layer)
                # Restoration never waits for compressed preparation admission.
                prepared = await asyncio.to_thread(_prepare_plain_rollout, selected)
                self._prepared[path] = prepared
            self._needs_resolution.discard(path)
            release_token = self._queue.release_token(path)
            self._active_prepared.add(path)
            try:
                result = await super()._process_change(Change.modified, str(path), channel_layer)
            finally:
                self._active_prepared.discard(path)
            if release_token != self._queue.release_token(path) or path in self._replaced_paths:
                self._release_prepared(path)
            if result.disposition in ('drained', 'failed'):
                self._release_prepared(path)
            return result
        except FileNotFoundError:
            # A selected source can disappear between resolution and open.
            # Confirm logical absence before publishing the stale disposition.
            try:
                await asyncio.to_thread(resolve_rollout, path)
            except FileNotFoundError:
                pass
            except OSError:
                self._release_prepared(path)
                self._settle_targets(path, None)
                return SessionChangeResult('failed')
            else:
                self._release_prepared(path)
                self._needs_resolution.add(path)
                return SessionChangeResult('ready')
            self._release_prepared(path)
            self._needs_resolution.discard(path)
            self._invalidate_parsed_path(path)
            self._sources.pop(path, None)
            self._physical_sources.pop(path, None)
            self._queue.observe_source(path, object())
            self._settle_targets(path, None)
            return await super()._process_change(Change.deleted, str(path), channel_layer)
        except OSError:
            logger.exception('Error preparing Codex rollout %s', path)
            self._release_prepared(path)
            self._settle_targets(path, None)
            return SessionChangeResult('failed')

    def notify_migration_released(self, release: MigrationRelease, *, event_token: int | None = None) -> None:
        if release.path is not None:
            release = release._replace(path=logical_rollout_path(release.path))
        previous = self._queue.release_token(release.path) if release.path is not None else -1
        super().notify_migration_released(release, event_token=event_token)
        if release.path is None or self._queue.release_token(release.path) <= previous:
            return
        if release.outcome != 'ready':
            self._preparation_waiting.pop(release.path, None)
            self._preparation_errors.pop(release.path, None)
        elif release.path in self._preparations:
            # The ready outcome owns a new guarded attempt. The old decoder
            # must terminate before another preparation of this path starts.
            self._preparation_waiting[release.path] = None
        if (release.outcome != 'ready' or release.replay) and release.path not in self._active_prepared:
            self._release_prepared(release.path)

    async def process_path(self, path: Path) -> None:
        logical = logical_rollout_path(path)
        if path.suffix != '.zst' and logical not in self._needs_resolution:
            target = await self._observe_source(logical)
            if logical not in self._needs_resolution:
                self._enqueue(logical, Change.modified)
                await self._queue.wait_drained(logical, target=target)
                return
        future = asyncio.get_running_loop().create_future()
        self._target_waiters.setdefault(logical, []).append(future)
        self._needs_resolution.add(logical)
        self._enqueue(logical, Change.modified)
        terminal = asyncio.create_task(self._queue.wait_drained(
            logical, target=PathDrainTarget(self._queue.source_generation(logical), 2**63),
        ))
        try:
            await asyncio.wait((future, terminal), return_when=asyncio.FIRST_COMPLETED)
            if future.done():
                target = future.result()
                if target is not None:
                    await self._queue.wait_drained(logical, target=target)
            else:
                await terminal
        finally:
            terminal.cancel()
            await asyncio.gather(terminal, return_exceptions=True)
            if not future.done():
                future.cancel()
            waiters = self._target_waiters.get(logical, [])
            if future in waiters:
                waiters.remove(future)
                if not waiters:
                    self._target_waiters.pop(logical, None)

    def stop_watcher(self) -> None:
        for attempt in self._preparation_attempts.values():
            attempt.stop.set()
        super().stop_watcher()

    async def _drain_changes(self) -> None:
        try:
            await super()._drain_changes()
        finally:
            # These tasks are not gate wakes. Await actual decoder termination
            # before dropping any file that its worker can still access.
            pending = asyncio.gather(*self._preparations.values(), return_exceptions=True)
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            pending.result()
            for path in list(self._prepared):
                self._release_prepared(path)
            self._preparation_waiting.clear()
            self._preparation_errors.clear()
            for path in list(self._target_waiters):
                self._settle_targets(path, None)
            if self._preparation_executor is not None:
                self._preparation_executor.shutdown(wait=True)
                self._preparation_executor = None

    def prepare_start(self) -> None:
        reopening = self._queue.closed
        if reopening and (self._preparations or self._prepared or self._preparation_executor is not None):
            raise RuntimeError('Cannot prepare watcher before compressed preparation drains')
        super().prepare_start()
        if reopening:
            self._association_sessions.clear()
            self._physical_sources.clear()
            self._needs_resolution.clear()

    def create_session_sync(
        self, parsed: ParsedSessionFile, project: Project, parent_session: Session | None = None,
        agent_settings: AgentSettings | None = None,
    ) -> Session:
        """Attach any SDK plan received before watcher discovery."""
        from .plan_snapshots import apply_pending_plan

        session = super().create_session_sync(parsed, project, parent_session, agent_settings)
        apply_pending_plan(session)
        return session

    async def defer_session_change(self, parsed: ParsedSessionFile) -> bool:
        # The coordinator is rewriting this session's history: skip the
        # event, it replays the file through ``process_path`` afterwards.
        return is_migrating(parsed.session_id)

    async def _process_parsed_session_change(
        self,
        path: Path,
        parsed: ParsedSessionFile,
        change_type: Change,
        channel_layer,
        *,
        read_path: Path | None = None,
    ) -> SessionChangeResult:
        if is_migrating(parsed.session_id):
            return SessionChangeResult('deferred')
        # Order: provider callback lock -> session gate -> DB writer lease.
        # The coordinator never acquires the callback lock while holding a gate.
        gate = gate_for(parsed.session_id)
        wake_target = _GateWakeTarget(self._queue.source_generation(path), self._queue.release_token(path))
        try:
            # Free acquisition does not suspend. A queued gate cannot stall
            # the consumer, even before its future migration owner marks it.
            async with asyncio.timeout(0):
                await gate.acquire()
        except TimeoutError:
            self._wake_after_gate(parsed.session_id, path, gate, wake_target)
            return SessionChangeResult('deferred')
        try:
            if is_migrating(parsed.session_id):
                return SessionChangeResult('deferred')
            prepared = self._prepared.get(path)
            if prepared is not None:
                read_path = prepared.read_path
            self._association_sessions.pop(path, None)
            if change_type != Change.deleted and await self._rewrite_detected(parsed, path, read_path=read_path):
                self._queue.observe_source(path, object())
                self._association_sessions.pop(path, None)
                return SessionChangeResult('deferred')
            session = self._association_sessions.pop(path, None)
            if session is not None:
                if prepared is None:
                    self._needs_resolution.add(path)
                    prepared = await asyncio.to_thread(_prepare_plain_rollout, path)
                    self._prepared[path] = prepared
                    read_path = prepared.read_path
                    meta = await asyncio.to_thread(extract_session_meta, prepared.physical_path,
                                                   read_path=prepared.read_path)
                    if (meta is None or meta.ignored or meta.session_id != parsed.session_id
                            or prepared.physical_path != self.projects_dir / parsed.file_path):
                        self._needs_resolution.add(path)
                        return SessionChangeResult('ready')
                # The rewrite check reuses its existing session lookup. Only
                # association repair submits this short additional writer job.
                if not await asyncio.to_thread(source_is_current, prepared.fingerprint):
                    self._needs_resolution.add(path)
                    return SessionChangeResult('ready')
                payload = CodexSourcePayload(
                    Provider.CODEX, session.id, parsed.file_path, prepared.fingerprint,
                    source_checkpoint(session), None,
                )
                future = asyncio.get_running_loop().create_future()
                if not await submit_async_job(ReconcileCodexSourceJob(Provider.CODEX, payload, future)):
                    self._needs_resolution.add(path)
                    return SessionChangeResult('ready')
                self._needs_resolution.discard(path)
            return await super()._process_parsed_session_change(
                path, parsed, change_type, channel_layer,
                **({'read_path': read_path} if read_path is not None else {}),
            )
        finally:
            gate.release()

    def _wake_after_gate(
        self, session_id: str, path: Path, gate: asyncio.Lock, target: _GateWakeTarget,
    ) -> None:
        if self._queue.closed:
            return
        targets = self._gate_wake_targets.setdefault(session_id, {})
        # Coalesce the task, but retain responsibility for each latest admitted
        # source. A replacement can defer while the original wake still waits.
        # The callback captured this before awaiting the gate. A terminal
        # outcome received during that await must still invalidate its wake.
        targets[path] = target
        if session_id in self._gate_wakes:
            return

        async def wake():
            try:
                async with gate:
                    pass
                # A coordinator outcome or a replacement wins over this older
                # gate wake. Never turn a failed migration into an implicit retry.
                for source, (generation, release_token) in targets.items():
                    if (not self._queue.closed
                            and generation is self._queue.source_generation(source)
                            and release_token == self._queue.release_token(source)):
                        self._enqueue(source, Change.modified)
            finally:
                self._gate_wakes.pop(session_id, None)
                self._gate_wake_targets.pop(session_id, None)

        task = asyncio.create_task(wake(), name=f'session-gate-wake-{session_id}')
        self._gate_wakes[session_id] = task
        self._wake_tasks.add(task)
        def forget(done):
            self._wake_tasks.discard(done)
            if self._gate_wakes.get(session_id) is done:
                self._gate_wakes.pop(session_id, None)
                self._gate_wake_targets.pop(session_id, None)

        task.add_done_callback(forget)

    async def _rewrite_detected(
        self, parsed: ParsedSessionFile, path: Path, *, read_path: Path | None = None,
    ) -> bool:
        """Whether the rollout was rewritten under TwiCC (not appended to).

        Two signals, both cheap on the hot path:

        - the file is shorter than the ``last_offset`` TwiCC already
          ingested — the incremental reader would seek past EOF or read a
          suffix under wrong line numbers;
        - the file's ``session_meta`` says ``paginated`` while the history
          TwiCC stored is legacy — a ``codex migrate-rollouts --apply`` (manual,
          or Codex's own background migration once enabled) rewrote a
          session TwiCC had not migrated itself. Checked once per session,
          then remembered.

        On detection the session is marked for rebuild (``compute_version``
        reset through the DB writer, which hides it until the coordinator
        replaces its history from byte zero) and the coordinator is woken.
        """

        session = await get_session_by_id(parsed.session_id)
        if session is None:
            return False
        if (getattr(session, 'file_path', parsed.file_path) != parsed.file_path
                or (path in self._prepared and (session.stale or session.unavailable_reason == 'rollout_missing'))):
            self._association_sessions[path] = session
        try:
            size = (read_path if read_path is not None else path).stat().st_size
        except OSError:
            return False
        rewritten = size < session.last_offset or path in self._replaced_paths
        if not rewritten and parsed.compute_ready_on_create and session.id not in self._paginated_in_db:
            try:
                database_mode = await get_db_history_mode(session.id)
            except RolloutMigrationError:
                # No stored session_meta yet (brand-new session): nothing to compare.
                return False
            if database_mode == HistoryMode.PAGINATED:
                self._paginated_in_db.add(session.id)
            else:
                rewritten = True
        if not rewritten:
            return False
        logger.info(
            "Codex rollout of session %s was rewritten (size=%d, last_offset=%d): scheduling a rebuild",
            session.id, size, session.last_offset,
        )
        self._paginated_in_db.discard(session.id)
        self._replaced_paths.add(path)
        future = asyncio.get_running_loop().create_future()
        await submit_async_job(MarkSessionRebuildJob(Provider.CODEX, session.id, future))
        request_rebuild(session.id)
        return True

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        logical = logical_rollout_path(path)
        prepared = self._prepared.get(logical)
        if prepared is None and logical in self._needs_resolution:
            return None
        if prepared is not None:
            path = prepared.physical_path
        if not is_session_file(path):
            return None

        try:
            relative = path.relative_to(self.projects_dir)
        except ValueError:
            return None

        # Codex stores files under YYYY/MM/DD/, never directly under the
        # sessions dir. Anything shallower is unrecognized.
        if len(relative.parts) < 2:
            return None

        # Extract session_id + cwd (+ optional parent_session_id) from
        # the first JSONL line. This is blocking I/O (open + readline +
        # orjson parse), so we offload it to a worker thread to keep
        # the watcher event loop snappy.
        meta = await asyncio.to_thread(
            extract_session_meta, path, **({'read_path': prepared.read_path} if prepared is not None else {}),
        )
        if meta is None or meta.ignored:
            # Empty file, unreadable, or missing session_meta — silently
            # skip. Guardian reviewer rollouts are also deliberately ignored;
            # sync_and_broadcast won't be called for any of these cases.
            return None

        project_id = path_to_project_id(meta.cwd)
        session_type = (
            SessionType.SUBAGENT if meta.parent_session_id else SessionType.SESSION
        )
        return ParsedSessionFile(
            project_id,
            meta.session_id,
            session_type,
            file_path=str(relative),
            parent_session_id=meta.parent_session_id,
            compute_ready_on_create=meta.history_mode == HistoryMode.PAGINATED,
        )

    async def _fetch_initial_title(self, parsed: ParsedSessionFile) -> str | None:
        # Top-level sessions only — subagents don't carry a user-facing name.
        if parsed.type != SessionType.SESSION:
            return None
        from .titles import read_title_from_codex
        return await read_title_from_codex(parsed.session_id)

    def get_compute(self) -> BaseSessionCompute:
        return _get_compute()

    async def _after_compaction_synced(self, session_id: str) -> None:
        # A COMPACT_SUMMARY line just landed in live sync. Tell the Codex
        # agent manager neutrally ("this session was compacted"). The manager
        # forwards it to a live agent if there is one; the agent itself
        # decides — via its own flag — whether it had a manual ``/compact``
        # in flight (and so should leave ASSISTANT_TURN) or whether this was
        # an auto-compaction it can ignore. No live manager (e.g. a
        # compute-only process) → nothing to notify.
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        await manager.notify_compacted(session_id)

    async def _after_agents_resumed(
        self, session_id: str, agents: list[tuple[str, str]],
    ) -> None:
        # The batch created run-opening interactions (a ``followup_task`` on
        # an idle child): the live stream cannot tell it from a
        # ``send_message``, so only the watcher knows the child runs again.
        # ``session_id`` is the tree root, the only session with a live
        # agent: the relay puts the children that still run back in its
        # running set. Fire-and-forget, like the stop relay below; the agent
        # re-reads the run state under its own lock, so the two relays may
        # run in any order.
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        asyncio.create_task(
            manager.notify_subagents_resumed(session_id, list(agents)),
            name=f"subagents-resumed-relay-{session_id}",
        )

    async def _after_agents_stopped(
        self, session_id: str, stopped_agent_ids: list[str],
    ) -> None:
        # The batch's stop step closed a run of these children and they no
        # longer run (``agent_run_states``), whatever the stamp and whatever
        # file carried the evidence — the root's rollout, or the child's own
        # turn end (rule 5). ``session_id`` is the tree root, the only
        # session with a live agent: the relay drops the children from its
        # running set and releases the subagent hold (or refreshes the
        # "waiting for N subagents" count). The root's stream sees a
        # ``completed`` item for most runs, but not every run end (a
        # ``FINAL_ANSWER`` only, rule 5, an owner abort), so this relay stays
        # the reliable end-of-child source. Fire-and-forget: the ingest path
        # never blocks on agent state settles.
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        asyncio.create_task(
            manager.notify_subagents_stopped(session_id, list(stopped_agent_ids)),
            name=f"subagents-stopped-relay-{session_id}",
        )

    async def _after_new_lines_synced(self, session, new_line_nums, tool_result_updates) -> None:
        # When a live agent is parked in a ``/goal`` continuation (a synthetic
        # ASSISTANT_TURN it does NOT drive), watch the freshly-synced lines for
        # the goal leaving ``active`` — through a status event or a successful
        # Goal-tool result — so the agent can settle back to USER_TURN. Cheap
        # gate first (no DB read unless such an agent is live); fire-and-forget
        # so the ingest path never blocks on the agent lock.
        if not new_line_nums:
            return
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        if not manager.has_goal_continuation(session.id):
            return
        asyncio.create_task(
            self._check_goal_continuation_end(manager, session.id, list(new_line_nums)),
            name=f"goal-continuation-check-{session.id}",
        )

    async def _after_any_new_lines_synced(self, session, new_line_nums) -> None:
        # Background shells the SDK stream cannot report, read off the
        # rollout of the thread that ran them — the session's own, or a
        # subagent's (it runs in its parent's app-server, and its items only
        # ever reach its own turn's stream):
        # - any thread: a completed ``CommandExecution`` item, the end of a
        #   process that may have outlived its turn (the stream has stopped
        #   listening by then) — gated on any agent tracking a live shell;
        # - a subagent's thread: the outputs announcing a process still
        #   running, the only start signal its parent can get — gated on the
        #   parent agent being live.
        # Fire-and-forget like the goal check.
        if not new_line_nums:
            return
        from .agent.manager import get_codex_agent_manager
        try:
            manager = get_codex_agent_manager()
        except KeyError:
            return
        parent_id = getattr(session, "parent_session_id", None)
        owner_id = parent_id if parent_id and manager.has_agent(parent_id) else None
        if owner_id is None and not manager.has_live_shells():
            return
        asyncio.create_task(
            self._relay_shell_events(manager, session.id, owner_id, list(new_line_nums)),
            name=f"shell-events-relay-{session.id}",
        )

    async def _relay_shell_events(
        self, manager, session_id: str, owner_id: str | None, new_line_nums: list[int],
    ) -> None:
        """Relay the processes these lines announce as running or as ended.

        Starts are read only when ``owner_id`` (the live parent of this
        subagent session) is given. Starts go out before ends: a process
        announced and ended within the same batch nets out, whatever order
        Codex wrote the two lines in.
        """
        def _events() -> tuple[dict[str, float], list[str]]:
            started: dict[str, float] = {}
            ended: list[str] = []
            rows = SessionItem.objects.filter(
                session_id=session_id, line_num__in=new_line_nums,
            ).order_by("line_num")
            for item in iter_current_items(rows, 128, 8 * 1024 * 1024):
                content = effective_current(item)
                if not content:
                    continue
                is_end = '"CommandExecution"' in content
                # Cheap pre-filter on the three shapes announcing a process.
                is_start = owner_id is not None and (
                    "session_id" in content or "SESSION_ID=" in content
                    or "Process running with session ID" in content
                )
                if not (is_end or is_start):
                    continue
                try:
                    parsed = orjson.loads(content)
                except orjson.JSONDecodeError:
                    continue
                if is_end and (process_id := ended_command_process_id(parsed)) is not None:
                    ended.append(process_id)
                    continue
                if not is_start:
                    continue
                announced = announced_running_process_id(parsed)
                if announced is None or str(announced) in started:
                    continue
                # When the announcement was written: the start came shortly
                # before, which bounds where the agent looks for its process.
                started[str(announced)] = rollout_line_epoch(parsed) or time.time()
            return started, ended

        try:
            started, ended = await sync_to_async(_events)()
            if started and owner_id is not None:
                await manager.notify_shells_started(owner_id, session_id, started)
            if ended:
                await manager.notify_shells_exited(session_id, ended)
        except Exception:
            logger.exception("Shell events relay failed for session %s", session_id)

    async def _check_goal_continuation_end(self, manager, session_id: str, new_line_nums: list[int]) -> None:
        """Settle a parked ``/goal`` continuation if any fresh line is a goal stop.

        Reads only the just-synced lines and applies the compute's
        :meth:`~twicc.providers.codex.compute.CodexSessionCompute.is_goal_continuation_stopped`
        predicate; on a hit, relays to the agent manager (the live agent's flag
        decides whether to act).
        """
        def _stopped() -> bool:
            compute = _get_compute()
            rows = SessionItem.objects.filter(
                session_id=session_id, line_num__in=new_line_nums,
            ).order_by("line_num")
            for item in iter_current_items(rows, 128, 8 * 1024 * 1024):
                content = effective_current(item)
                if not content:
                    continue
                try:
                    parsed = orjson.loads(content)
                except orjson.JSONDecodeError:
                    continue
                if compute.is_goal_continuation_stopped(parsed):
                    return True
            return False

        try:
            if await sync_to_async(_stopped)():
                await manager.notify_goal_continuation_stopped(session_id)
        except Exception:
            logger.exception(
                "Goal-continuation end check failed for session %s", session_id,
            )


# ---- Singleton accessor ----

_watcher: CodexSessionsWatcher | None = None


def get_watcher() -> CodexSessionsWatcher:
    """Return the process-local Codex watcher singleton.

    Callers (orchestrator) should go through this rather than
    instantiating their own watcher, so the stop/boost events and
    fast-poll deadline are shared.
    """
    global _watcher
    if _watcher is None:
        _watcher = CodexSessionsWatcher()
    return _watcher
