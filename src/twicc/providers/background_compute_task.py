"""
Background compute producer.

Processes existing sessions that need metadata computation at startup. After
the startup phase, it monitors stale sessions until they become stable enough
for a successful compute. New sessions created by the watcher get
compute_version set at creation time.

Architecture:
- A separate Process per provider handles CPU-intensive work (JSON parsing,
  metadata computation). The worker process only READS from the database
  (WAL mode supports multiple readers).
- Each worker sends results and optional status on its own Pipe. An owned
  receiver thread decodes complete frames into process-local bounded queues.
  The DB writer applies the results. This module is purely the *producer* side — it never
  writes to the database from the main process.

This module is provider-agnostic: each provider's orchestrator builds a
:class:`ComputeContext` carrying its ``Provider`` enum value, its
``compute_version`` setting, and a ``compute_factory`` dotted path pointing
at its own :class:`~twicc.providers.compute_base.BaseSessionCompute`
factory. The factory is stored as a string so it survives the spawn-worker
pickle without dragging the provider's compute module into the child
process before ``django.setup()`` has run.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import multiprocessing
import queue
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import orjson
from asgiref.sync import sync_to_async

from twicc.providers.compute_ipc import ComputeResultStream, ComputeStatusSender, ComputeStopFlag
from twicc.providers.compute_selection import (
    COMPUTE_MIN_SOURCE_AGE_SECONDS, COMPUTE_SELECTION_POLL_SECONDS, COMPUTE_STATUS_POLL_SECONDS,
)
from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.startup_progress import broadcast_startup_progress

if TYPE_CHECKING:
    from twicc.providers.compute_base import BaseSessionCompute

# NOTE: Django model imports (twicc.core.models, twicc.core.serializers, and
# any provider compute module that imports them) are intentionally NOT imported
# at module level. The "spawn" start method re-imports this module in the child
# process before django.setup() runs. Top-level model imports would trigger
# AppRegistryNotReady. All such imports are done inside functions instead.
# For the same reason, db_writer (which the main process needs here) is only
# imported lazily inside functions, never at module level.

logger = logging.getLogger(__name__)

# Use "spawn" start method to avoid fork-safety issues.
# The default "fork" method on Linux can deadlock when the parent process has
# multiple threads (event loop, sync_to_async thread pool, etc.) because the
# child inherits all locks in their current state but the threads that held them
# no longer exist. "spawn" starts a fresh Python interpreter, avoiding this entirely.
_mp_ctx = multiprocessing.get_context("spawn")

async def _wait_for_retry_delay(stop_event: asyncio.Event, delay: float) -> bool:
    """Wait for one retry window, returning false when shutdown interrupts it."""

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return True
    return False


async def read_compute_state[T](
    read: Callable[[list[str]], Awaitable[T]],
    session_ids: list[str],
    stop_event: asyncio.Event,
    wait_for_delay: Callable[[asyncio.Event, float], Awaitable[bool]] = _wait_for_retry_delay,
    stopped: T | None = None,
) -> T | None:
    """Retry state reads without holding writer admission; stop interrupts waits."""
    delay = 1.0
    while not stop_event.is_set():
        try:
            return await read(session_ids)
        except Exception:
            logger.exception("Background compute state read failed; retrying")
        if not await wait_for_delay(stop_event, delay):
            break
        delay = min(delay * 2, 60.0)
    return stopped


async def retire_compute_run(ctx: ComputeContext, completion: asyncio.Future | None = None) -> None:
    """Fence and drain one run without stopping its provider.

    Retain cleanup ownership through repeated cancellation. The caller retains
    the completion future because writer abandonment removes its registry entry.
    """
    from twicc.providers.db_writer import abandon_compute_run

    async def cleanup():
        if ctx.result_stream is not None:
            ctx.result_stream.stop_admission()
        try:
            await abandon_compute_run(ctx.run_id, ctx.provider)
        finally:
            if completion is not None:
                if not completion.done():
                    completion.cancel()
                elif not completion.cancelled():
                    completion.exception()
            await stop_compute_worker(ctx)

    owned = asyncio.create_task(cleanup())
    cancelled = False
    while not owned.done():
        try:
            await asyncio.shield(owned)
        except asyncio.CancelledError:
            cancelled = True
    owned.result()
    if cancelled:
        raise asyncio.CancelledError


@dataclass
class ComputeContext:
    """Mutable state for the background compute pipeline.

    Created once at startup by a provider's orchestrator and passed
    explicitly to all functions that need access to the compute
    infrastructure. The provider injects:

    - ``provider``: the :class:`~twicc.core.enums.Provider` enum value used
      both for ORM filters and (via ``.value``) as the wire key on
      startup-progress broadcasts so the frontend can aggregate per-phase
      totals across providers.
    - ``compute_version``: the current target compute version for this
      provider (sessions whose stored ``compute_version`` differs are the
      ones that need recomputation).
    - ``compute_factory``: a ``"module:attribute"`` dotted path to the
      provider's :class:`~twicc.providers.compute_base.BaseSessionCompute`
      factory (e.g. ``"twicc.providers.claude_code.compute:get_compute"``).
      Stored as a string — not a callable — so the spawn worker can carry
      this value through pickle without importing the provider's compute
      module before ``django.setup()`` has run in the child process. The
      module is imported lazily on first :meth:`get_compute` call.

    ``result_stream`` owns this run's result/status Pipe and receiver thread.
    ``command_queue`` has a parent-only producer and is replaced each run.
    The worker stop flag is advisory and has no process-owned lock.
    """

    provider: Provider
    compute_version: int
    compute_factory: str
    command_queue: _mp_ctx.Queue = field(default_factory=_mp_ctx.Queue)
    # Coordinators consume worker status separately from writer acknowledgments.
    status_queue: queue.Queue | None = None
    worker_stop_event: ComputeStopFlag = field(default_factory=lambda: ComputeStopFlag(_mp_ctx))
    result_stream: ComputeResultStream | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: _mp_ctx.Process | None = None
    # Set by start_background_compute_task before the worker is spawned. The
    # worker tags every result message with it so the DB writer can
    # isolate this run from stale messages of a cancelled one.
    run_id: int = 0

    def get_compute(self) -> BaseSessionCompute:
        """Resolve :attr:`compute_factory` and return a fresh compute instance.

        Imports the provider's compute module on demand. Caller must have
        already run ``django.setup()`` in its process, since the compute
        module imports Django models at module level.
        """
        return _resolve_factory(self.compute_factory)()


def _resolve_factory(dotted_path: str):
    """Resolve a ``"module:attribute"`` path into the referenced object."""
    module_name, attr = dotted_path.split(":")
    return getattr(importlib.import_module(module_name), attr)


async def stop_background_task(ctx: ComputeContext) -> None:
    """Signal the background compute task to stop and terminate worker process.

    Sets the context's asyncio ``stop_event`` (the provider's compute task
    exits on it) and then stops the worker process. A coordinator that
    outlives its worker (Codex, between runs) must call
    :func:`stop_compute_worker` instead, or it stops itself.
    """
    logger.info("stop_background_task: starting shutdown...")

    # Signal asyncio tasks to stop
    ctx.stop_event.set()
    logger.info("stop_background_task: asyncio stop_event set")

    await stop_compute_worker(ctx)


async def stop_compute_worker(ctx: ComputeContext) -> None:
    """Stop the worker process and close the context's queues, nothing else.

    Leaves ``ctx.stop_event`` alone so the caller keeps running.
    ``start_compute_process`` recreates the worker stop flag and queues for
    the next run.

    Async so the blocking ``process.join()`` calls can run off the event loop:
    joining on the loop thread would freeze the DB writer task,
    and a worker blocked on a full result queue only makes progress (drains,
    then exits) while that DB writer keeps running.
    """
    # Stop receiver admission first. Abandonment fencing belongs to the
    # orchestrator; it must precede provider shutdown.
    if ctx.result_stream is not None:
        ctx.result_stream.stop_admission()
    # This advisory flag never acquires a process-owned lock.
    ctx.worker_stop_event.set()
    logger.info("stop_compute_worker: worker_stop_event set")

    # Send stop signal to worker process via queue (backup)
    try:
        if ctx.process is not None and ctx.process.is_alive():
            ctx.command_queue.put_nowait(None)  # None = stop signal
        logger.info("stop_compute_worker: stop signal sent to queue")
    except Exception as e:
        logger.warning(f"stop_compute_worker: failed to send stop signal to queue: {e}")

    # Wait for worker process to exit gracefully, then terminate if needed.
    # join() runs off the event loop (asyncio.to_thread) so the DB writer
    # keeps draining while we wait — terminate()/kill() are non-blocking
    # signals and stay on the loop.
    if ctx.process is not None and ctx.process.is_alive():
        logger.info(f"stop_compute_worker: waiting for worker process (PID: {ctx.process.pid}) to exit...")
        await asyncio.to_thread(ctx.process.join, 2.0)
        if ctx.process.is_alive():
            logger.warning("stop_compute_worker: worker process still alive, terminating...")
            ctx.process.terminate()
            await asyncio.to_thread(ctx.process.join, 1.0)
            if ctx.process.is_alive():
                logger.error("stop_compute_worker: worker process did not terminate, killing...")
                ctx.process.kill()
                await asyncio.to_thread(ctx.process.join, 0.5)
        else:
            logger.info("stop_compute_worker: worker process exited gracefully")
    else:
        logger.info("stop_compute_worker: no worker process to stop")
    if ctx.process is not None:
        if ctx.process.is_alive():
            raise RuntimeError(f'Compute worker {ctx.process.pid} did not exit after kill')
        ctx.process.close()
        ctx.process = None

    # Do not wait on the command feeder. The unused parent reader is closed
    # after spawn, so worker exit also releases a feeder blocked in send.
    with suppress(Exception):
        ctx.command_queue.cancel_join_thread()
        ctx.command_queue.close()
    from twicc.providers.db_writer import close_compute_result_stream
    await close_compute_result_stream(ctx.run_id)
    ctx.result_stream = None

    logger.info("stop_compute_worker: shutdown complete")


# =============================================================================
# Worker Process Functions (run in separate process)
# =============================================================================


def _set_pdeathsig_linux() -> None:
    """Ask the kernel to send SIGTERM to this process when its parent dies (Linux only).

    Calls ``prctl(PR_SET_PDEATHSIG, SIGTERM)`` so a hard kill of the main
    TwiCC server doesn't leave this worker orphaned still holding the
    Tantivy writer lock — which is what blocked the next ``twicc`` start
    until a manual ``pkill``. Must be called inside the child process,
    after spawn/fork, so the setting applies to the child rather than
    the parent.

    No-op on macOS and Windows: there is no portable equivalent there,
    so a hard kill of the parent on those platforms still leaves the
    worker orphaned. That is a known limitation, not a regression
    introduced by this code.

    Best-effort: any failure is logged at DEBUG level and the worker
    keeps running with the same lifetime semantics it had before.
    """
    import sys
    if sys.platform != "linux":
        return
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        import signal
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            logger.debug(
                "prctl(PR_SET_PDEATHSIG) failed: errno=%d",
                ctypes.get_errno(),
            )
    except Exception as exc:
        logger.debug("Could not set PR_SET_PDEATHSIG: %s", exc)


def compute_worker_main(
    command_queue,
    result_queue,
    status_queue,
    stop_event,
    compute_factory: str,
    run_id: int,
) -> None:
    """
    Main function running in the compute worker process.

    Receives session_ids via command_queue, computes metadata (READ-ONLY DB access),
    sends update batches via the synchronous result_queue Pipe adapter.

    This function runs in a separate process and must initialize Django itself.
    ``compute_factory`` is the dotted ``"module:attribute"`` path of the
    provider's compute factory; it is resolved AFTER ``django.setup()`` so
    importing the provider's compute module (which pulls in Django models)
    does not crash the spawn worker before the app registry is ready.

    Every message put on ``result_queue`` carries a ``'provider'`` key (for
    routing/logging) and this run's ``run_id``, so the DB writer can
    isolate this run from a cancelled run's stale messages.
    """
    # Tie the worker's lifetime to the parent's on Linux: if the main TwiCC
    # process dies hard (SIGKILL, panic), the kernel sends us SIGTERM, which
    # default-handles to a clean exit. Without this, the orphaned worker would
    # keep holding the Tantivy writer lock and block the next ``twicc`` start.
    # Done before any other setup so the window where we could be orphaned is
    # as small as possible.
    _set_pdeathsig_linux()

    import signal

    # Ensure default signal handling — with "fork" mode the child would inherit
    # the parent's custom SIGTERM/SIGINT handlers that don't actually exit.
    # With "spawn" this is already the case, but we set it explicitly as a safeguard.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # This "spawn" worker inherits the server's os.environ, but the server
    # strips DJANGO_SETTINGS_MODULE from it (see twicc.cli.run) so the var does
    # not leak into agents working on other Django projects. Set it ourselves
    # before django.setup() so this worker still resolves twicc's settings.
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

    import django
    django.setup()

    import logging
    worker_logger = logging.getLogger(__name__)

    compute = _resolve_factory(compute_factory)()
    compute.enable_batch_queue_cache()
    provider_value = compute.provider.value
    # Tag every subsequent log line emitted by this worker process with
    # the provider this worker was spawned for. The worker process has a
    # fresh ContextVar default, so this set() is what makes log lines
    # show up under the right provider rather than ``"global"``.
    current_provider.set(provider_value)
    worker_logger.info("Compute worker process started")

    while True:
        try:
            # Check stop signal before getting command
            if stop_event.is_set():
                worker_logger.info("Compute worker received stop signal (event)")
                break

            # Blocking get with timeout (allows checking for stop signal)
            try:
                command = command_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Check stop event again after getting command
            if stop_event.is_set():
                worker_logger.info("Compute worker received stop signal (event)")
                break

            # None = stop signal (backup method)
            if command is None:
                worker_logger.info("Compute worker received stop signal (queue)")
                break

            session_id = command.get('session_id')
            if session_id:
                try:
                    # This function reads DB and sends batches via result_queue
                    compute._tree_completion = command.get("tree_completion")
                    compute.compute_session_metadata(session_id, result_queue, run_id)
                    if status_queue is not None:
                        status_queue.put({"type": "computed", "session_id": session_id, "_run_id": run_id})
                except Exception as e:
                    worker_logger.error(f"Error computing session {session_id}: {e}", exc_info=True)
                    # Flush the file handler so the error survives process termination
                    with suppress(Exception):
                        for handler in logging.getLogger('twicc').handlers:
                            handler.flush()
                    result_queue.put(orjson.dumps({
                        'type': 'error',
                        'provider': provider_value,
                        'run_id': run_id,
                        'session_id': session_id,
                        'error': str(e),
                    }))
                    if status_queue is not None:
                        status_queue.put({
                            "type": "failed",
                            "_run_id": run_id,
                            "session_id": session_id,
                            "error": str(e),
                        })

        except Exception as e:
            worker_logger.error(f"Unexpected error in compute worker: {e}", exc_info=True)
            with suppress(Exception):
                for handler in logging.getLogger('twicc').handlers:
                    handler.flush()

    # Signal the DB writer that the worker is done
    try:
        result_queue.put(orjson.dumps({'type': 'done', 'provider': provider_value, 'run_id': run_id}))
        worker_logger.info("Compute worker sent 'done' signal")
    except BrokenPipeError:
        # Retirement can close receiver admission before the terminal frame.
        pass
    finally:
        result_queue.close()

    worker_logger.info("Compute worker process stopped")


def start_compute_process(ctx: ComputeContext) -> None:
    """Start the compute worker process if not already running.

    The worker gets only its Pipe writer. The parent closes its writer after
    spawn, so worker exit produces EOF even during a partial frame. The
    DB writer remains lazy-imported to keep model imports out of spawn setup.
    """
    if ctx.process is None or not ctx.process.is_alive():
        from twicc.providers.db_writer import create_compute_result_stream

        # Parent-only producers never read this queue. Recreate it for each
        # run: a killed child can retain its reader lock.
        with suppress(Exception):
            ctx.command_queue.cancel_join_thread()
            ctx.command_queue.close()
        ctx.command_queue = _mp_ctx.Queue()
        # Private Queue switch: EPIPE means this run has no consumer. Stop the
        # feeder instead of logging one traceback per abandoned command.
        ctx.command_queue._ignore_epipe = True
        if ctx.status_queue is not None:
            ctx.status_queue = queue.Queue(maxsize=200)
        ctx.worker_stop_event = ComputeStopFlag(_mp_ctx)
        stream = create_compute_result_stream(ctx.provider, ctx.run_id, ctx.status_queue)
        ctx.result_stream = stream
        spawned = False
        try:
            ctx.process = _mp_ctx.Process(
                target=compute_worker_main,
                args=(
                    ctx.command_queue,
                    stream.sender,
                    ComputeStatusSender(stream.sender) if ctx.status_queue is not None else None,
                    ctx.worker_stop_event,
                    ctx.compute_factory,
                    ctx.run_id,
                ),
                daemon=True,
                name="compute-worker",
            )
            ctx.process.start()
            spawned = True
            # Private stdlib endpoint: parent never consumes commands. Closing
            # its reader ensures worker death wakes the parent's queue feeder
            # with BrokenPipe instead of leaving a blocked daemon thread.
            ctx.command_queue._reader.close()
            stream.start()
        except BaseException as error:
            stream.failed(f'compute transport spawn failed: {error}')
            stream.stop_admission()
            stream.reader.close()
            with suppress(Exception):
                ctx.command_queue.cancel_join_thread()
                ctx.command_queue.close()
            if not spawned:
                ctx.process = None
            raise
        finally:
            # A retained parent writer would prevent EOF on a partial frame.
            stream.sender.close()
        logger.info(f"Started compute worker process (PID: {ctx.process.pid})")


async def start_background_compute_task(ctx: ComputeContext, initial_done: asyncio.Event | None = None) -> None:
    """Compute one native tree at a time with bounded admission within each depth.

    Startup classification includes deferred sources. Ready progress uses a fixed
    initial top-level cohort and only advances after database confirmation.
    Source age uses wall time; failure deadlines use monotonic time.
    """
    from twicc.core.models import Session, SessionType
    from twicc.projects import load_project_directories, load_project_git_roots
    from twicc.providers.db_writer import InitializeComputeStorageJob, arm_compute_completion, submit_async_job

    async def query(read):
        async def load(_session_ids):
            return await sync_to_async(read)()
        return await read_compute_state(load, [], ctx.stop_event, stopped=[])

    def outdated():
        return Session.objects.filter(provider=ctx.provider).exclude(compute_version=ctx.compute_version)

    initial = await query(lambda: list(outdated().values_list("id", "type", "mtime")))
    if ctx.stop_event.is_set():
        return
    initial_ids = {sid for sid, _, _ in initial}
    def initial_groups():
        from twicc.providers.compute_tree import NativeTreeError, resolve_native_root

        groups = {}
        for sid in initial_ids:
            try:
                root = resolve_native_root(sid)
            except (NativeTreeError, Session.DoesNotExist):
                root = sid
            groups.setdefault(root, set()).add(sid)
        return {sid: group for group in groups.values() for sid in group}

    classification_groups = await query(initial_groups)
    display_ids = {sid for sid, kind, _ in initial if kind == SessionType.SESSION}
    total = len(display_ids)
    ready: set[str] = set()
    unclassified = initial_ids.copy()
    pending: set[str] = set()
    # Keep commands excluded until both their writer outcome and worker status
    # arrive. A fast writer must not admit a duplicate before the status frame.
    awaiting_status: dict[str, None] = {}
    retry_at: dict[str, float] = {}
    retry_delays: dict[str, float] = {}
    applied: asyncio.Queue = asyncio.Queue()
    completion: asyncio.Future | None = None
    next_classification_poll = 0.0
    next_selection_poll = 0.0
    completed_normally = False

    async def progress(candidate_ids=None, *, completed=False):
        check_ids = display_ids if candidate_ids is None else display_ids & candidate_ids
        confirmed = await query(lambda: set(Session.objects.filter(
            id__in=check_ids, provider=ctx.provider, compute_version=ctx.compute_version,
            unavailable_reason__isnull=True,
        ).values_list("id", flat=True)))
        ready.update(confirmed)
        await broadcast_startup_progress(
            "background_compute", len(ready), total, provider=ctx.provider.value,
            completed=completed and len(confirmed) == total,
        )

    def classify(sid, *, blocked=False):
        unclassified.difference_update(classification_groups.get(sid, {sid}) if blocked else {sid})
        if initial_done is not None and not unclassified:
            initial_done.set()

    def failed(sid):
        delay = min(retry_delays.get(sid, .5) * 2, 60.0)
        retry_delays[sid] = delay
        retry_at[sid] = time.monotonic() + delay
        classify(sid, blocked=True)

    async def retire():
        nonlocal completion
        async def cleanup():
            delay = 1.0
            while True:
                try:
                    await retire_compute_run(ctx, completion)
                    return
                except Exception:
                    logger.exception("Compute run retirement failed; retaining resources for retry")
                if not await _wait_for_retry_delay(ctx.stop_event, delay):
                    await retire_compute_run(ctx, completion)
                    return
                delay = min(delay * 2, 60.0)

        owned = asyncio.create_task(cleanup())
        cancelled = False
        while not owned.done():
            try:
                await asyncio.shield(owned)
            except asyncio.CancelledError:
                cancelled = True
        owned.result()
        completion = None
        pending.clear()
        awaiting_status.clear()
        if cancelled:
            raise asyncio.CancelledError

    if not initial:
        if initial_done is not None:
            initial_done.set()
        total = await query(Session.objects.filter(provider=ctx.provider, type=SessionType.SESSION).count)
        await broadcast_startup_progress("background_compute", total, total,
                                         provider=ctx.provider.value, completed=True)
        return
    await progress()
    await query(load_project_directories)
    await query(load_project_git_roots)
    process_delay = 1.0
    try:
        while not ctx.stop_event.is_set():
            try:
                if ctx.status_queue is not None:
                    while True:
                        try:
                            status = ctx.status_queue.get_nowait()
                        except queue.Empty:
                            break
                        if isinstance(status, dict):
                            # The queue is owned by this run and replaced on spawn.
                            if status.get("_run_id") != ctx.run_id:
                                continue
                            sid = status.get("session_id")
                            if sid in awaiting_status:
                                awaiting_status.pop(sid)
                                next_selection_poll = 0.0
                while not applied.empty():
                    signal = applied.get_nowait()
                    if signal.run_id != ctx.run_id or signal.session_id not in pending:
                        continue
                    sid = signal.session_id
                    pending.discard(sid)
                    next_selection_poll = 0.0
                    classify(sid)
                    rejected_outdated = signal.outcome != "applied" and await query(
                        lambda sid=sid: outdated().filter(pk=sid).exists()
                    )
                    if signal.outcome == "failed" or rejected_outdated:
                        failed(sid)
                    else:
                        retry_at.pop(sid, None)
                        retry_delays.pop(sid, None)
                        process_delay = 1.0
                        if signal.outcome == "applied":
                            from twicc.search_indexing_task import request_session_reindex

                            request_session_reindex(sid)
                    await progress({sid})
                if completion is not None and completion.done():
                    # An unexpected terminal frame or broken transport retires
                    # the whole run before any replacement can be armed.
                    await asyncio.shield(completion)
                    raise RuntimeError("Compute worker ended before selection completed")

                cutoff = time.time() - COMPUTE_MIN_SOURCE_AGE_SECONDS
                if unclassified and time.monotonic() >= next_classification_poll:
                    remaining_initial = unclassified.copy()
                    recent = await query(lambda cutoff=cutoff, ids=remaining_initial: list(
                        outdated().filter(mtime__gt=cutoff, id__in=ids).values_list("id", flat=True)
                    ))
                    for sid in recent:
                        classify(sid, blocked=True)
                    still_outdated = await query(lambda ids=remaining_initial: set(
                        outdated().filter(id__in=ids).values_list("id", flat=True)
                    ))
                    externally_ready = remaining_initial - still_outdated
                    for sid in externally_ready:
                        classify(sid)
                    if externally_ready:
                        await progress(externally_ready)
                    next_classification_poll = time.monotonic() + COMPUTE_SELECTION_POLL_SECONDS

                outstanding = pending | awaiting_status.keys()
                cooling = {sid for sid, deadline in retry_at.items() if deadline > time.monotonic()}
                if outstanding - retry_delays.keys():
                    cooling.update(retry_delays)
                candidates = []
                if len(outstanding) < 2 and time.monotonic() >= next_selection_poll:
                    from twicc.providers.compute_selection import select_native_tree_source

                    def select_source():
                        invalid_roots = set()
                        candidate = select_native_tree_source(
                            ctx.provider, ctx.compute_version, cutoff=cutoff,
                            outstanding=outstanding, excluded=cooling, invalid_roots=invalid_roots,
                        )
                        return candidate, invalid_roots

                    candidate, invalid_roots = await query(select_source) or (None, ())
                    for root_id in invalid_roots:
                        classify(root_id, blocked=True)
                    candidates = [candidate] if candidate else []
                    if not candidates:
                        next_selection_poll = time.monotonic() + COMPUTE_SELECTION_POLL_SECONDS
                if candidates:
                    if completion is None:
                        # Never replace retained transport resources after failed retirement.
                        if ctx.process is not None or ctx.result_stream is not None:
                            await retire()
                        ctx.status_queue = queue.Queue(maxsize=200)
                        ctx.run_id, completion = arm_compute_completion(
                            ctx.provider, display_session_ids=None, total_display=0, applied_queue=applied,
                            publish_progress=False,
                        )
                        start_compute_process(ctx)
                    # Re-query after each initialization/application rather than
                    # admitting a stale batch of source observations.
                    sid = candidates[0]
                    try:
                        await submit_async_job(InitializeComputeStorageJob(
                            ctx.provider, sid, asyncio.get_running_loop().create_future(),
                        ))
                    except Exception:
                        logger.exception("Compute storage initialization failed for %s", sid)
                        failed(sid)
                        continue
                    pending.add(sid)
                    awaiting_status[sid] = None
                    from twicc.providers.compute_selection import tree_completion_context

                    context = await query(lambda: tree_completion_context(sid, ctx.compute_version))
                    ctx.command_queue.put_nowait({"session_id": sid, "tree_completion": context})
                    next_selection_poll = 0.0
                    continue
                if not outstanding and not await query(outdated().exists):
                    if completion is not None:
                        # Acknowledgments precede notifications and project work.
                        # Only the terminal writer future confirms their full drain.
                        ctx.command_queue.put_nowait(None)
                        stopped = asyncio.create_task(ctx.stop_event.wait())
                        try:
                            await asyncio.wait((completion, stopped), return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            stopped.cancel()
                            with suppress(asyncio.CancelledError):
                                await stopped
                        if ctx.stop_event.is_set():
                            break
                        failed_count = completion.result()
                        if failed_count:
                            logger.error("DB writer reported %d failed compute result(s)", failed_count)
                        await stop_compute_worker(ctx)
                        completion = None
                    # Source observations can change while notifications drain.
                    # A closed successful run can safely give way to a fresh run.
                    if await query(outdated().exists):
                        next_selection_poll = 0.0
                        continue
                    await progress(completed=True)
                    completed_normally = True
                    break
                # Polling also detects mtime changes without requiring a watcher event.
                if outstanding:
                    try:
                        signal = await asyncio.wait_for(applied.get(), timeout=COMPUTE_STATUS_POLL_SECONDS)
                    except TimeoutError:
                        pass
                    else:
                        applied.put_nowait(signal)
                elif not await _wait_for_retry_delay(ctx.stop_event, COMPUTE_SELECTION_POLL_SECONDS):
                    break
            except Exception:
                logger.exception("Background compute run failed; retiring before recovery")
                faulty = next(iter(awaiting_status), next(iter(pending), None))
                await retire()
                next_selection_poll = 0.0
                if faulty is not None:
                    failed(faulty)
                    continue
                if not await _wait_for_retry_delay(ctx.stop_event, process_delay):
                    break
                process_delay = min(process_delay * 2, 60.0)
    finally:
        if not completed_normally:
            await retire()
