"""Dedicated workers for compute application and ordinary synchronous writer work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import get_ident
from time import perf_counter

from channels.db import database_sync_to_async
from django.db import connections

from twicc.sync_diagnostics import log_slow

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

    submitted = perf_counter()

    def measured():
        started = perf_counter()
        log_slow('executor_queue', (started - submitted) * 1000)
        try:
            return function(*args, **kwargs)
        finally:
            log_slow('executor_run', (perf_counter() - started) * 1000)

    return await database_sync_to_async(measured, thread_sensitive=False, executor=executor)()


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


_application_executor: ThreadPoolExecutor | None = None
_application_shutdown_task: asyncio.Task | None = None
_application_thread: int | None = None


def _initialize_compute_application_worker() -> None:
    global _application_thread
    _application_thread = get_ident()


def start_compute_application_executor() -> None:
    """Start the pinned application worker, separate from ordinary writer work."""
    global _application_executor
    if _application_executor is not None or _application_shutdown_task is not None:
        raise RuntimeError('Compute application executor already started')
    _application_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='twicc-compute-application',
        initializer=_initialize_compute_application_worker,
    )


async def run_compute_application_sync[T](function: Callable[..., T], /, *args, **kwargs) -> T:
    """Copy caller context for one call without Channels connection cleanup.

    Cancellation cancels the asyncio wrapper, not an executing thread. Keep
    preparation shielded and retain its completion future. A cancelled wrapper
    does not prove completion. Drain actual work before cleanup or successor
    admission; shutdown does this through a FIFO worker cleanup call.
    """
    executor = _application_executor
    if executor is None:
        raise RuntimeError('Compute application executor not started')
    context = copy_context()
    return await asyncio.get_running_loop().run_in_executor(executor, context.run, lambda: function(*args, **kwargs))


def close_compute_application_connection() -> None:
    """Close the worker's default connection after terminal work has drained.

    Call on the application worker only, after atomic blocks have unwound.
    Operation TEMP cleanup is separate and never closes a caller connection.
    """
    if _application_thread != get_ident():
        raise RuntimeError('Compute application connection cleanup requires its worker')
    connections['default'].close()


async def stop_compute_application_executor() -> None:
    """Drain uncancellable calls, clean up on their thread, and join once."""
    global _application_executor, _application_shutdown_task, _application_thread
    if _application_shutdown_task is None:
        executor = _application_executor
        if executor is None:
            return
        _application_executor = None

        async def close_and_join() -> None:
            try:
                await asyncio.get_running_loop().run_in_executor(executor, close_compute_application_connection)
            finally:
                await asyncio.to_thread(executor.shutdown, wait=True)

        _application_shutdown_task = asyncio.create_task(close_and_join())

    shutdown = _application_shutdown_task
    cancelled = False
    while not shutdown.done():
        try:
            await asyncio.shield(shutdown)
        except asyncio.CancelledError:
            cancelled = True
    try:
        shutdown.result()
    finally:
        if _application_shutdown_task is shutdown:
            _application_shutdown_task = None
            _application_thread = None
    if cancelled:
        raise asyncio.CancelledError()
