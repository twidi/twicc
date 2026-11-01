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
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

from .title_control import TitleControlError, rename_running_session
from .title_sync import SourceCursor, TitleDecision, TitleSyncCoordinator

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

    def _on_owner_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def request_publication(self, decision: TitleDecision) -> None:
        if not self._on_owner_loop():
            self._loop.call_soon_threadsafe(self.request_publication, decision)
            return
        if self._closed:
            return
        sid = decision.session_id
        if self.coordinator.current_decision(sid) != decision:
            return
        self._queued[sid] = decision
        self._wakes.setdefault(sid, asyncio.Event()).set()
        if sid not in self._tasks:
            self._tasks[sid] = asyncio.create_task(self._run(sid), name=f"claude-title-{sid}")

    def notify_process_ready(self, session_id: str, process_token: str) -> None:
        if not self._on_owner_loop():
            self._loop.call_soon_threadsafe(self.notify_process_ready, session_id, process_token)
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
                if not self._current(decision, process):
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
                if not self._current(decision, process):
                    return False
                started = await self.hooks.prepare_dispatch(decision, process)
                paste_permitted = started and self._current(decision, process)
                return paste_permitted

            success = False
            try:
                await process.agent.rename(decision.title, token=decision.token, before_paste=before_paste)
                success = True
            except (TimeoutError, TitleControlError):
                pass
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
            if not self._current(decision, process):
                return
            remaining = budget.deadline - self._loop.time()
            if remaining <= 0 or not await self.hooks.prepare_dispatch(decision, process):
                return
            success = False
            attempted = False
            try:
                remaining = budget.deadline - self._loop.time()
                if remaining <= 0 or not self._current(decision, process):
                    return
                budget = budget._replace(attempts=budget.attempts + 1)
                self._sdk_budgets[sid] = budget
                timeout = min(self.SDK_REQUEST_TIMEOUT, remaining)
                async with asyncio.timeout(timeout):
                    attempted = True
                    await rename_running_session(process.agent._client, sid, decision.title, timeout=timeout)
                success = True
            except (TitleControlError, TimeoutError):
                pass
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

    async def flush_publications(self, session_id: str, *, timeout: float) -> bool:
        task = self._tasks.get(session_id)
        if task is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def finish_process(self, session_id: str, process_token: str) -> None:
        # The manager calls this only after confirmed process death. The hook
        # drains the complete suffix and retires this exact process token.
        await self.hooks.finish_process(session_id, process_token)
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
    _worker = TitlePublicationWorker(coordinator, hooks)
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
