"""Bounded asynchronous Claude title publication.

The hooks are the integration boundary with the database application lane.
``prepare_dispatch`` scans/ingests the complete transcript outside that lane,
then revalidates both identities and stages ``start_dispatch`` inside it.
``acknowledge`` applies the coordinator result in that same transactional lane.
``cancel_dispatch_before_delivery`` releases a prepared dispatch in that lane
only when the worker prevents every transport attempt.
``finish_process`` drains the final transcript before staged process retirement.
None of these hooks may retain a database lock when they return.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from contextvars import Context
from threading import Lock
from typing import Any, NamedTuple

from .title_control import TitleControlError, rename_running_session
from .title_sync import ObservationResult, SourceCursor, TitleDecision, TitleObservation, TitleSyncCoordinator

logger = logging.getLogger(__name__)


class ActiveTitleProcess(NamedTuple):
    process_token: str
    agent: Any
    ready: bool
    hybrid: bool


class PublicationHooks(NamedTuple):
    resolve_process: Callable[[str], ActiveTitleProcess | None]
    prepare_dispatch: Callable[[TitleDecision, ActiveTitleProcess], Awaitable[bool]]
    ingest_available: Callable[[str], Awaitable[SourceCursor | None]]
    acknowledge: Callable[[TitleDecision, bool, SourceCursor | None], Awaitable[None]]
    cancel_dispatch_before_delivery: Callable[[TitleDecision], Awaitable[None]]
    finish_process: Callable[[str, str], Awaitable[None]]
    reconcile_inactive: Callable[[str], Awaitable[None]]


class _SdkBudget(NamedTuple):
    token: str
    deadline: float
    attempts: int = 0


class TitlePublicationWorker:
    """One I/O worker per session; all authoritative state stays in the coordinator.

    ``flush_publications`` means scheduled work has drained, not that the CLI
    confirmed a title. The coordinator alone records confirmation and hazards.
    Readiness wakes parked work. Budget exhaustion retains the coordinator's
    pending decision without scheduling a hot retry loop.
    """

    SDK_REQUEST_TIMEOUT = 10.0
    SDK_TOTAL_BUDGET = 30.0
    SDK_MAX_ATTEMPTS = 3
    CLOSE_TIMEOUT = 1.0

    def __init__(self, coordinator: TitleSyncCoordinator, hooks: PublicationHooks):
        self._loop = asyncio.get_running_loop()
        self.coordinator = coordinator
        self.hooks = hooks
        self._queued: dict[str, TitleDecision] = {}
        self._sdk_budgets: dict[str, _SdkBudget] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._wakes: dict[str, asyncio.Event] = {}
        self._reservations: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._quiescing = False
        self._retiring: set[str] = set()
        self._ingress_lock = Lock()
        self._ingress: deque[tuple[Callable, tuple]] = deque()

    def _enqueue_ingress(self, callback, *args):
        # Record receipt before scheduling its notification. Flush can consume
        # this queue even if that notification has not reached the owner loop.
        with self._ingress_lock:
            self._ingress.append((callback, args))
        self._loop.call_soon_threadsafe(self._drain_ingress)

    def _drain_ingress(self):
        with self._ingress_lock:
            pending = tuple(self._ingress)
            self._ingress.clear()
        for callback, args in pending:
            callback(*args)

    @staticmethod
    def _complete_ingress_barrier(future):
        if not future.done():
            future.set_result(None)

    async def _ingress_barrier(self):
        future = self._loop.create_future()
        with self._ingress_lock:
            self._ingress.append((self._complete_ingress_barrier, (future,)))
        self._loop.call_soon(self._drain_ingress)
        await future

    def _on_owner_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def request_publication(self, decision: TitleDecision) -> None:
        if not self._on_owner_loop():
            self._enqueue_ingress(self.request_publication, decision)
            return
        if self._closed or decision.session_id in self._retiring:
            return
        sid = decision.session_id
        if self._quiescing and self.hooks.resolve_process(sid) is not None:
            return
        if self.coordinator.current_decision(sid) != decision:
            return
        self._queued[sid] = decision
        self._wakes.setdefault(sid, asyncio.Event()).set()
        if sid not in self._tasks:
            self._tasks[sid] = asyncio.create_task(self._run(sid), name=f"claude-title-{sid}", context=Context())

    def notify_process_ready(self, session_id: str, process_token: str) -> None:
        if not self._on_owner_loop():
            self._enqueue_ingress(self.notify_process_ready, session_id, process_token)
            return
        process = self.hooks.resolve_process(session_id)
        if process is None or process.process_token != process_token:
            return
        self._wakes.setdefault(session_id, asyncio.Event()).set()
        decision = self.coordinator.current_decision(session_id)
        if decision is not None:
            self.request_publication(decision)

    @asynccontextmanager
    async def process_publication_reservation(self, session_id: str):
        # Launch/adoption and inactive mutation share this reservation. Never
        # acquire it from a transaction that already owns the DB write lane.
        async with self._reservations.setdefault(session_id, asyncio.Lock()):
            yield

    def _current(self, decision, process):
        current = self.hooks.resolve_process(decision.session_id)
        return (
            self.coordinator.current_decision(decision.session_id) == decision
            and current is not None
            and current.process_token == process.process_token == decision.process_token
        )

    async def _run(self, sid):
        try:
            while sid in self._queued and not self._closed:
                decision = self._queued[sid]
                process = self.hooks.resolve_process(sid)
                if process is not None and process.process_token == decision.process_token and not process.ready:
                    wake = self._wakes[sid]
                    wake.clear()
                    # Re-read after clearing so a synchronous ready notification
                    # cannot be lost between the predicate and wait.
                    process = self.hooks.resolve_process(sid)
                    if process is not None and not process.ready:
                        await wake.wait()
                    continue
                self._queued.pop(sid, None)
                if process is None:
                    try:
                        async with self.process_publication_reservation(sid):
                            if self.hooks.resolve_process(sid) is None:
                                await self.hooks.reconcile_inactive(sid)
                    except Exception:
                        logger.exception("Inactive Claude title publication failed for %s", sid)
                    finally:
                        # A duplicate notification cannot retry this write. A
                        # newer decision keeps its own queued publication.
                        if self._queued.get(sid) == decision:
                            self._queued.pop(sid, None)
                    continue
                if self._quiescing or not self._current(decision, process):
                    continue
                try:
                    await self._publish(decision, process)
                except Exception:
                    # An I/O failure belongs to this decision. A newer queued
                    # decision must still receive its own dispatch validation.
                    logger.exception("Claude title publication failed for %s", sid)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Leave pending coordinator state for lifecycle reconciliation.
            logger.exception("Claude title publication failed for %s", sid)
        finally:
            self._tasks.pop(sid, None)

    async def _publish(self, decision, process):
        sid = decision.session_id
        if process.hybrid:
            started = False
            paste_permitted = False

            async def before_paste():
                nonlocal started, paste_permitted
                if self._quiescing or not self._current(decision, process):
                    return False
                started = await self.hooks.prepare_dispatch(decision, process)
                paste_permitted = started and not self._quiescing and self._current(decision, process)
                return paste_permitted

            success = False
            try:
                await process.agent.rename(decision.title, token=decision.token, before_paste=before_paste)
                success = True
            except (TimeoutError, TitleControlError) as error:
                self._log_failure(decision, 'hybrid', error.reason if isinstance(error, TitleControlError) else 'timeout',
                                  1, 'uncertain_delivery' if paste_permitted else 'pending')
            finally:
                if started and not self._closed:
                    if not paste_permitted:
                        await self.hooks.cancel_dispatch_before_delivery(decision)
                    else:
                        boundary = await self.hooks.ingest_available(sid)
                        await self.hooks.acknowledge(decision, success, boundary)
            return

        budget = self._sdk_budgets.get(sid)
        if budget is None or budget.token != decision.token:
            budget = _SdkBudget(decision.token, self._loop.time() + self.SDK_TOTAL_BUDGET)
            self._sdk_budgets[sid] = budget
        while budget.attempts < self.SDK_MAX_ATTEMPTS:
            await self.hooks.ingest_available(sid)
            if self._quiescing or not self._current(decision, process):
                return
            remaining = budget.deadline - self._loop.time()
            if remaining <= 0:
                self._log_failure(decision, 'sdk', 'budget_exhausted', budget.attempts, 'pending')
                return
            if not await self.hooks.prepare_dispatch(decision, process):
                return
            success = False
            attempted = False
            try:
                remaining = budget.deadline - self._loop.time()
                if remaining <= 0:
                    self._log_failure(decision, 'sdk', 'budget_exhausted', budget.attempts, 'pending')
                    return
                if self._quiescing or not self._current(decision, process):
                    return
                budget = budget._replace(attempts=budget.attempts + 1)
                self._sdk_budgets[sid] = budget
                timeout = min(self.SDK_REQUEST_TIMEOUT, remaining)
                async with asyncio.timeout(timeout):
                    attempted = True
                    await rename_running_session(process.agent._client, sid, decision.title, timeout=timeout)
                success = True
            except (TitleControlError, TimeoutError) as error:
                self._log_failure(decision, 'sdk', error.reason if isinstance(error, TitleControlError) else 'timeout',
                                  budget.attempts, 'pending')
            finally:
                if not self._closed:
                    if not attempted:
                        await self.hooks.cancel_dispatch_before_delivery(decision)
                    else:
                        boundary = await self.hooks.ingest_available(sid)
                        await self.hooks.acknowledge(decision, success, boundary)
            if success:
                # A successful control ACK is never retried because ingestion
                # can lag. Task4 confirms only on admissible transcript evidence.
                return
        if self.coordinator.current_decision(sid) == decision:
            self._log_failure(decision, 'sdk', 'budget_exhausted', budget.attempts, 'pending')

    @staticmethod
    def _log_failure(decision, route, reason, attempt, state):
        # Error messages can include title text or credentials. Log stable reasons only.
        logger.warning('Claude title publication session=%s route=%s token=%s reason=%s attempt=%d state=%s',
                       decision.session_id, route, decision.token, reason, attempt, state)

    async def quiesce_active_publications(self) -> None:
        """Stop active dispatch while retaining final ingestion and inactive writes."""
        self._quiescing = True
        self._drain_ingress()
        tasks = tuple(task for sid, task in self._tasks.items() if self.hooks.resolve_process(sid) is not None)
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.CLOSE_TIMEOUT)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                logger.warning('Claude title quiescence deadline reached for %d workers', len(pending))

    async def flush_publications(self, session_id: str, *, timeout: float) -> bool:
        deadline = self._loop.time() + timeout
        with self._ingress_lock:
            pending_ingress = bool(self._ingress)
        if not pending_ingress and session_id not in self._tasks:
            return True
        try:
            # One deadline covers ingress and provider work. The FIFO marker
            # consumes every notification recorded before this flush barrier.
            async with asyncio.timeout_at(deadline):
                await self._ingress_barrier()
                task = self._tasks.get(session_id)
                if task is not None:
                    await asyncio.shield(task)
        except TimeoutError:
            return False
        return True

    async def finish_process(self, session_id: str, process_token: str) -> None:
        # The manager calls this only after confirmed process death. The hook
        # drains the complete suffix and retires this exact process token.
        if self.coordinator.process_token(session_id) != process_token:
            return
        self._retiring.add(session_id)
        self._queued.pop(session_id, None)
        task = self._tasks.get(session_id)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # The transport no longer executes provider I/O before the final drain.
        await self.hooks.finish_process(session_id, process_token)
        self._retiring.discard(session_id)
        self._wakes.setdefault(session_id, asyncio.Event()).set()
        decision = self.coordinator.current_decision(session_id)
        if decision is not None:
            self.request_publication(decision)

    async def close_publications(self) -> None:
        self._closed = True
        self._queued.clear()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.CLOSE_TIMEOUT)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                logger.warning("Claude title cleanup deadline reached for %d workers", len(pending))


_worker: TitlePublicationWorker | None = None


def configure_publications(coordinator: TitleSyncCoordinator, hooks: PublicationHooks) -> TitlePublicationWorker:
    """Configure once on the manager event loop, before any title acceptance."""
    global _worker
    if _worker is not None and not _worker._closed:
        raise RuntimeError("Claude title publications are already configured")
    previous = _worker
    _worker = TitlePublicationWorker(coordinator, hooks)
    if previous is not None and previous._loop is _worker._loop:
        # Hot provider restart cannot bypass an older inactive I/O reservation.
        _worker._reservations = previous._reservations
    return _worker


def get_publications() -> TitlePublicationWorker:
    if _worker is None:
        raise RuntimeError("Claude title publications are not configured")
    return _worker


def request_publication(decision: TitleDecision) -> None:
    get_publications().request_publication(decision)


def notify_process_ready(session_id: str, process_token: str) -> None:
    get_publications().notify_process_ready(session_id, process_token)


async def flush_publications(session_id: str, *, timeout: float) -> bool:
    return await get_publications().flush_publications(session_id, timeout=timeout)


async def finish_process(session_id: str, process_token: str) -> None:
    await get_publications().finish_process(session_id, process_token)


async def close_publications() -> None:
    if _worker is not None:
        await _worker.close_publications()


def process_publication_reservation(session_id: str):
    return get_publications().process_publication_reservation(session_id)


_coordinator = TitleSyncCoordinator()


def get_title_coordinator() -> TitleSyncCoordinator:
    """Shared main-process coordinator for compute, writers, and publication."""
    return _coordinator


@contextmanager
def title_state_transaction(*, durable=False):
    """Stage title state in the caller's serialized root DB transaction.

    Nested uses share the overlay and restore it on savepoint rollback.
    Register publication before notifications, including inherited contexts.
    """
    from django.db import transaction

    coordinator = get_title_coordinator()
    existing = coordinator._stage.get()
    root = existing is None or existing._closed
    with coordinator.staged_state() as stage:
        checkpoint = stage.checkpoint()
        with transaction.atomic(durable=durable):
            if root:
                transaction.on_commit(stage.commit)
            yield stage
            # Atomic can roll back without raising, including set_rollback(True).
            # Restore before its savepoint exits so the outer overlay stays valid.
            if transaction.get_rollback():
                stage.restore(checkpoint)
                if root:
                    stage._closed = True


def protect_claude_acceptance_boundary_locked(
    cursor: SourceCursor, *, baseline: TitleObservation | None = None,
) -> bool:
    """Validate an externally captured complete-record boundary before acceptance.

    Capture outside the DB lane. Call inside title_state_transaction, before
    accept_decision. A mismatched source must be rescanned, never accepted.
    """
    from twicc.core.models import Session

    if not Session.objects.filter(id=cursor.source_session_id,
                                  storage_incarnation=cursor.storage_incarnation,
                                  history_epoch=cursor.history_epoch).exists():
        return False
    get_title_coordinator().protect_acceptance_boundary(cursor, baseline=baseline)
    return True


def attach_claude_process_locked(
    session_id: str, process_token: str, *, hybrid: bool, baseline: TitleObservation | None = None,
    initial_transcript_absent: bool = False,
) -> None:
    """Attach only after draining the existing complete transcript, before acceptance."""
    from twicc.core.models import Session

    if initial_transcript_absent:
        get_title_coordinator().attach_process(session_id, process_token, hybrid=hybrid)
        return
    session = Session.objects.get(pk=session_id)
    if session.storage_incarnation is None:
        from twicc.core.content_storage.extraction import initialize_compute_session
        initialize_compute_session(session_id)
        session.refresh_from_db()
    coordinator = get_title_coordinator()
    if coordinator.process_token(session_id) != process_token:
        coordinator.protect_acceptance_boundary(SourceCursor(
            session.id, session.storage_incarnation, session.history_epoch, session.last_line,
        ), baseline=baseline)
    coordinator.attach_process(session_id, process_token, hybrid=hybrid)


def _restore_observations(records):
    return tuple(TitleObservation(SourceCursor(*record[0]), *record[1:]) for record in records)


def _current_observations(records):
    from twicc.core.models import Session
    identities = {
        row['id']: (row['storage_incarnation'], row['history_epoch'])
        for row in Session.objects.filter(id__in={r.cursor.source_session_id for r in records}).values(
            'id', 'storage_incarnation', 'history_epoch',
        )
    }
    return tuple(r for r in records if identities.get(r.cursor.source_session_id) == r.cursor[1:3])


def apply_claude_observations_locked(records: tuple[TitleObservation, ...]) -> tuple[str, ...]:
    """Apply validated ordered observations; never perform provider I/O.

    The caller owns the DB write lane and title_state_transaction.
    Unmanaged imports retain last-title-per-target semantics.
    """
    from twicc.providers.compute_base import BaseSessionCompute

    records = _current_observations(_restore_observations(records))
    coordinator = get_title_coordinator()
    managed = tuple(r for r in records if coordinator.tracks_observations(r.target_session_id) and r.cursor.source_session_id == r.target_session_id)
    unmanaged = {}
    for record in records:
        if record not in managed and not coordinator.manages(record.target_session_id) and record.kind == 'title':
            unmanaged[record.target_session_id] = record.title
    changed = []
    for sid, title in unmanaged.items():
        if BaseSessionCompute._write_provider_title(sid, title):
            changed.append(sid)
    for record in managed:
        decision = coordinator.current_decision(record.target_session_id)
        if decision is not None:
            coordinator.bind_initial_source(decision, record.cursor)
    fresh = tuple(r for r in managed if coordinator.fresh_observation(r))
    result = coordinator.observe(managed) if managed else ObservationResult()
    changed.extend(apply_claude_observation_result_locked(result))
    _notify_title_evidence_after_commit(fresh)
    return tuple(dict.fromkeys(changed))


def apply_claude_observation_result_locked(result: ObservationResult) -> tuple[str, ...]:
    """Apply observer/ACK output and bounded stored replay in the same transaction."""
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.core.content_storage.readers import effective_current, iter_current_items
    from twicc.core.models import Session, SessionItem
    from .compute import get_compute
    import orjson

    changed = {}
    pending = deque([result])
    replayed = {}
    def write_applied(current):
        for record in current.apply:
            if record.title is not None:
                from django.db import transaction
                from twicc.pending_titles import pop_pending_title
                transaction.on_commit(lambda sid=record.target_session_id: pop_pending_title(sid))
                if BaseSessionCompute._write_provider_title(record.target_session_id, record.title):
                    changed[record.target_session_id] = None
    while pending:
        current = pending.popleft()
        write_applied(current)
        for span in current.replay:
            source = span.start[:3]
            if replayed.get(source) == span:
                continue
            replayed[source] = span
            session = Session.objects.filter(id=span.start.source_session_id,
                storage_incarnation=span.start.storage_incarnation, history_epoch=span.start.history_epoch).first()
            if session is None:
                continue
            rows = SessionItem.objects.filter(session_id=session.id, line_num__gte=span.start.line_num,
                                              line_num__lte=span.upper.line_num).order_by('line_num')
            parents, batch, latest = {}, [], None
            for item in iter_current_items(rows, 128, 8 * 1024 * 1024):
                try:
                    parsed = orjson.loads(effective_current(item))
                except orjson.JSONDecodeError:
                    continue
                batch.extend(get_compute().extract_provider_title_observations(parsed, session, item.line_num, parents))
                if len(batch) >= 128:
                    latest = get_title_coordinator().observe(tuple(batch))
                    write_applied(latest)
                    batch.clear()
            if batch:
                latest = get_title_coordinator().observe(tuple(batch))
                write_applied(latest)
            if latest is not None and latest.replay:
                pending.append(ObservationResult(replay=latest.replay))
    return tuple(changed)


def acknowledge_claude_title_locked(decision, success: bool, boundary: SourceCursor | None) -> tuple[str, ...]:
    from twicc.core.models import Session
    if boundary is not None and not Session.objects.filter(
        id=decision.session_id, storage_incarnation=boundary.storage_incarnation,
        history_epoch=boundary.history_epoch,
    ).exists():
        return ()
    changed = apply_claude_observation_result_locked(get_title_coordinator().acknowledge(
        decision, success=success, boundary=boundary,
    ))
    _wake_title_process_after_commit(decision.session_id, decision.process_token)
    return changed


def cancel_claude_dispatch_locked(decision) -> tuple[str, ...]:
    changed = apply_claude_observation_result_locked(get_title_coordinator().cancel_dispatch_before_delivery(decision))
    _wake_title_process_after_commit(decision.session_id, decision.process_token)
    return changed


def _wake_title_process_after_commit(session_id, process_token):
    from django.db import transaction
    worker = _worker
    if worker is None:
        return
    def wake():
        process = worker.hooks.resolve_process(session_id)
        if worker._closed or process is None or process.process_token != process_token:
            return
        event = worker._wakes.get(session_id)
        if event is not None:
            event.set()
    transaction.on_commit(lambda: worker._loop.call_soon_threadsafe(wake))


def _notify_title_evidence_after_commit(records):
    """Deliver validated evidence on the configured manager loop after commit."""
    from django.db import transaction
    if not records or _worker is None or _worker._closed:
        return
    worker = _worker
    groups = {}
    for record in records:
        process = worker.hooks.resolve_process(record.target_session_id)
        if process is not None:
            groups.setdefault((record.target_session_id, process.process_token), []).append(record)
    def deliver():
        def on_loop():
            if worker._closed:
                return
            for (sid, token), evidence in groups.items():
                process = worker.hooks.resolve_process(sid)
                if process is not None and process.process_token == token:
                    wake = getattr(worker, '_wakes', {}).get(sid)
                    if wake is not None:
                        wake.set()
                    if process.hybrid:
                        process.agent.on_title_evidence(tuple(evidence))
        worker._loop.call_soon_threadsafe(on_loop)
    transaction.on_commit(deliver)
