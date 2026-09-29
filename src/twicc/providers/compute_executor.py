"""One database worker for compute and other long synchronous writer work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from channels.db import database_sync_to_async
from django.db import connections

_executor: ThreadPoolExecutor | None = None
_shutdown_task: asyncio.Task | None = None


def start_compute_executor() -> None:
    """Start the dedicated worker for one DB writer lifetime."""
    global _executor
    if _executor is not None or _shutdown_task is not None:
        raise RuntimeError("Compute executor already started")
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="twicc-compute")


async def run_compute_sync[T](function: Callable[..., T], /, *args, **kwargs) -> T:
    """Run synchronous database work on the dedicated worker."""
    executor = _executor
    if executor is None:
        raise RuntimeError("Compute executor not started")

    return await database_sync_to_async(function, thread_sensitive=False, executor=executor)(*args, **kwargs)


async def stop_compute_executor() -> None:
    """Drain admitted work, close worker-owned connections, then join the worker."""
    global _executor, _shutdown_task

    if _shutdown_task is None:
        executor = _executor
        if executor is None:
            return
        _executor = None

        async def close_and_join() -> None:
            try:
                await asyncio.get_running_loop().run_in_executor(executor, connections.close_all)
            finally:
                await asyncio.to_thread(executor.shutdown, wait=True)

        _shutdown_task = asyncio.create_task(close_and_join())

    shutdown = _shutdown_task
    cancelled = False
    while not shutdown.done():
        try:
            await asyncio.shield(shutdown)
        except asyncio.CancelledError:
            cancelled = True
    try:
        shutdown.result()
    finally:
        if _shutdown_task is shutdown:
            _shutdown_task = None
    if cancelled:
        raise asyncio.CancelledError()
