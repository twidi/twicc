"""Coalesce automatic title requests through two owned backend workers."""

import asyncio
import logging
from collections import deque
from typing import NamedTuple

from twicc.core.services.title_automation import check_session_title
from twicc.title_ownership import (
    TitleCheckRun, capture_title_run, release_final_title_check, title_check_scope,
)

logger = logging.getLogger(__name__)


class TitleCheckRequest(NamedTuple):
    closing: bool
    run: TitleCheckRun | None


# Plain containers retain requests submitted before the backend loop starts.
_queued: deque[str] = deque()
_pending: dict[str, TitleCheckRequest] = {}
_running: set[str] = set()
_wake: asyncio.Event | None = None


def request_title_check(session_id: str, *, closing: bool = False, run: TitleCheckRun | None = None) -> None:
    """Enqueue on the backend loop, merging closing flags without spawning tasks."""
    already_pending = session_id in _pending
    run = run if run is not None else capture_title_run(session_id)
    previous = _pending.get(session_id)
    if previous is not None and previous.run is not None and previous.run.final and run is None:
        return  # A watcher/archive request cannot replace an authorized final check.
    same_agent = previous is not None and (
        previous.run is run or (previous.run is not None and run is not None and previous.run.agent is run.agent)
    )
    _pending[session_id] = TitleCheckRequest(closing or (same_agent and previous.closing), run)
    if already_pending or session_id in _running:
        return
    _queued.append(session_id)
    if _wake is not None:
        _wake.set()


async def _worker(wake: asyncio.Event) -> None:
    """Hold the session slot through generation, apply, and all provider pushes."""
    while True:
        if not _queued:
            wake.clear()
            await wake.wait()
            continue
        session_id = _queued.popleft()
        request = _pending.pop(session_id)
        _running.add(session_id)
        try:
            with title_check_scope(request.run):
                await check_session_title(session_id, closing=request.closing)
        except Exception:
            logger.exception("Automatic title check failed for %s", session_id)
        finally:
            release_final_title_check(request.run)
            _running.remove(session_id)
            # Requests received during this check live in a separate pending
            # entry. Do not erase their closing flag when this check finishes.
            if session_id in _pending:
                _queued.append(session_id)
                wake.set()


async def start_title_auto_task(shutdown_event: asyncio.Event) -> None:
    """Own both workers until shutdown; release all loop state after cancellation."""
    global _wake
    wake = asyncio.Event()
    _wake = wake
    if _queued:
        wake.set()
    workers = [asyncio.create_task(_worker(wake), name=f"title-auto-worker-{index}") for index in range(2)]
    try:
        await shutdown_event.wait()
    finally:
        for worker in workers:
            worker.cancel()
        try:
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            # Server cancellation can overlap cooperative worker cleanup.
            # Release loop state even when gathering propagates cancellation.
            _wake = None
            _queued.clear()
            for request in _pending.values():
                release_final_title_check(request.run)
            _pending.clear()
            _running.clear()
