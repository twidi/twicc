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
    COMPUTE_MIN_SOURCE_AGE_SECONDS, COMPUTE_STATUS_POLL_SECONDS, compute_retry_deadline,
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

# A malformed member tree beneath a current root is retried this many times,
# then dropped until the next coordinator start so the provider can go idle.
MEMBER_ROOT_ATTEMPTS = 3

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


async def cancel_and_wait(task: asyncio.Task) -> None:
    """Cancel a child task and wait for its end, keeping the caller's own cancellation.

    A cancel aimed at the caller while it awaits the child is forwarded to the
    child, so both surface as the same ``CancelledError``. The caller's cancel
    count tells them apart: only the child's cancellation is absorbed. The
    caller's cancel is raised even when the child swallows it and returns
    normally. Other child exceptions propagate.
    """
    current = asyncio.current_task()
    cancelling = current.cancelling() if current is not None else 0
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if current is not None and current.cancelling() > cancelling:
            raise
        return
    if current is not None and current.cancelling() > cancelling:
        raise asyncio.CancelledError


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

            completed_root_id = command.get('completed_root_id')
            if completed_root_id is not None:
                getattr(compute, '_computed_cost_sessions', {}).pop(completed_root_id, None)
                continue

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


async def start_background_compute_task(
    ctx: ComputeContext, initial_done: asyncio.Event | None = None, *, work_state,
) -> None:
    """Own callback registration and the actual-work gate through retirement."""
    selection_changed = asyncio.Event()
    notified = set()

    def wakeup(session_id):
        notified.add(session_id)
        selection_changed.set()

    work_state.set_pending(ctx.provider, True)
    work_state.register_source_wakeup(ctx.provider, wakeup)
    idle_confirmed = False
    try:
        idle_confirmed = await _run_background_compute(ctx, initial_done, selection_changed, notified)
    except asyncio.CancelledError:
        # Cancellation can arrive before the coordinator installs its own
        # retirement guard. Prove teardown before releasing actual-work gating.
        cleanup = asyncio.create_task(retire_compute_run(ctx))
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass
        cleanup.result()
        idle_confirmed = True
        raise
    finally:
        work_state.unregister_source_wakeup(ctx.provider, wakeup)
        if idle_confirmed:
            work_state.set_pending(ctx.provider, False)


async def _run_background_compute(ctx, initial_done, selection_changed, notified) -> bool:
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

    from django.db.models import Q
    from twicc.providers.compute_tree import (
        NativeTreeError, next_root_eligibility, resolve_native_roots, select_outdated_member_roots,
        select_outdated_root,
    )
    from twicc.providers.compute_selection import select_tree_source, tree_completion_context

    def outdated():
        return Session.objects.filter(
            Q(compute_version__isnull=True) | Q(compute_version__lt=ctx.compute_version),
            provider=ctx.provider, type=SessionType.SESSION, parent_session__isnull=True,
        )

    initial = await query(lambda: list(outdated().values_list("id", "mtime")))
    if ctx.stop_event.is_set():
        return True
    # Offline lines can leave an outdated member beneath a current root. Its
    # root is admitted once here; members keep their own source-age gating.
    member_roots = await query(lambda: select_outdated_member_roots(ctx.provider, ctx.compute_version))
    if ctx.stop_event.is_set():
        return True
    display_ids = {sid for sid, _ in initial}
    baseline = 0
    if not initial:
        baseline = await query(Session.objects.filter(
            provider=ctx.provider, type=SessionType.SESSION, parent_session__isnull=True,
        ).count)
    if ctx.stop_event.is_set():
        return True
    total = baseline + len(display_ids)
    ready: set[str] = set()
    unclassified = display_ids.copy()
    pending: set[str] = set()
    awaiting_status: dict[str, None] = {}
    retry_at: dict[str, float] = {}
    retry_delays: dict[str, float] = {}
    # Only blocked roots persist outside the currently admitted tree.
    deferred: dict[str, float] = {}
    member_root_failures: dict[str, int] = {}
    # Dropped member roots return on a source change: discovery can repair them.
    dropped_member_roots: set[str] = set()
    active_root = None
    applied: asyncio.Queue = asyncio.Queue()
    completion: asyncio.Future | None = None
    completed_normally = False

    async def progress(candidate_ids=(), *, completed=False):
        check_ids = display_ids if completed else display_ids & set(candidate_ids)
        confirmed = set()
        ids = list(check_ids)
        for offset in range(0, len(ids), 200):
            batch = ids[offset:offset + 200]
            confirmed.update(await query(lambda batch=batch: set(Session.objects.filter(
                id__in=batch, provider=ctx.provider, compute_version=ctx.compute_version,
                unavailable_reason__isnull=True,
            ).values_list("id", flat=True))))
        ready.update(confirmed)
        await broadcast_startup_progress(
            "background_compute", baseline + len(ready), total, provider=ctx.provider.value,
            completed=completed and baseline + len(ready) == total,
        )

    def classify(root_id):
        unclassified.discard(root_id)
        if initial_done is not None and not unclassified:
            initial_done.set()

    def failed(sid):
        delay = min(retry_delays.get(sid, .5) * 2, 60.0)
        retry_delays[sid] = delay
        retry_at[sid] = compute_retry_deadline(delay)
        classify(active_root)

    for sid, mtime in initial:
        if mtime > time.time() - COMPUTE_MIN_SOURCE_AGE_SECONDS:
            classify(sid)
    if not unclassified and initial_done is not None:
        initial_done.set()

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

    startup_pending = True
    process_delay = 1.0
    selection_due = True
    next_deadline = None
    try:
        while not ctx.stop_event.is_set():
            try:
                if startup_pending:
                    await progress()
                    await query(load_project_directories)
                    await query(load_project_git_roots)
                    startup_pending = False
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
                                selection_due = True
                while not applied.empty():
                    signal = applied.get_nowait()
                    if signal.run_id != ctx.run_id or signal.session_id not in pending:
                        continue
                    if signal.completed_root_id is not None:
                        ctx.command_queue.put_nowait({'completed_root_id': signal.completed_root_id})
                    selection_due = True
                    sid = signal.session_id
                    pending.discard(sid)
                    rejected_outdated = signal.outcome != "applied" and await query(
                        lambda sid=sid: Session.objects.filter(
                            Q(compute_version__isnull=True) | Q(compute_version__lt=ctx.compute_version),
                            pk=sid, provider=ctx.provider,
                        ).exists()
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
                    await progress({active_root})
                if completion is not None and completion.done():
                    # An unexpected terminal frame or broken transport retires
                    # the whole run before any replacement can be armed.
                    await asyncio.shield(completion)
                    raise RuntimeError("Compute worker ended before selection completed")

                if selection_changed.is_set() or (next_deadline is not None and time.monotonic() >= next_deadline):
                    selection_due = True
                # Status transport checks do not read candidates without new evidence.
                if not selection_due:
                    await asyncio.sleep(COMPUTE_STATUS_POLL_SECONDS)
                    continue
                selection_due = False
                # Clear before DB reads. A commit racing a read sets this again.
                selection_changed.clear()
                if notified:
                    changed_ids = list(notified)
                    notified.clear()
                    roots = set()
                    for offset in range(0, len(changed_ids), 200):
                        batch = changed_ids[offset:offset + 200]
                        resolved = await query(lambda batch=batch: resolve_native_roots(batch))
                        roots.update(resolved.values())
                    for root in roots:
                        deferred.pop(root, None)
                        if root in dropped_member_roots:
                            dropped_member_roots.discard(root)
                            member_root_failures.pop(root, None)
                            member_roots.add(root)
                    current = {}
                    root_ids = list(roots)
                    for offset in range(0, len(root_ids), 200):
                        batch = root_ids[offset:offset + 200]
                        current.update(await query(lambda batch=batch: list(
                            outdated().filter(pk__in=batch).values_list('id', 'mtime'),
                        )))
                    for root, mtime in current.items():
                        if mtime > time.time() - COMPUTE_MIN_SOURCE_AGE_SECONDS:
                            classify(root)
                    for root in roots - current.keys():
                        classify(root)
                    await progress(roots)

                now = time.monotonic()
                deferred = {root: deadline for root, deadline in deferred.items() if deadline > now}
                cutoff = time.time() - COMPUTE_MIN_SOURCE_AGE_SECONDS
                outstanding = pending | awaiting_status.keys()
                candidates = []
                next_deadline = min(deferred.values(), default=None)
                if len(outstanding) < 2:
                    while True:
                        if active_root is None:
                            active_root = await query(lambda cutoff=cutoff, deferred=deferred: select_outdated_root(
                                ctx.provider, ctx.compute_version, cutoff=cutoff, excluded_root_ids=deferred,
                            ))
                            if active_root is None:
                                active_root = min(member_roots - deferred.keys(), default=None)
                            if active_root is None:
                                break
                        cooling = {sid for sid, deadline in retry_at.items() if deadline > now}
                        if outstanding - retry_delays.keys():
                            cooling.update(retry_delays)
                        def select_source(active_root=active_root, cutoff=cutoff,
                                          outstanding=outstanding, cooling=cooling):
                            try:
                                return select_tree_source(
                                    active_root, ctx.compute_version, cutoff=cutoff,
                                    outstanding=outstanding, excluded_source_ids=cooling,
                                )
                            except (NativeTreeError, Session.DoesNotExist) as error:
                                # Malformed membership is a blocked tree, not a
                                # transient DB read failure that retries forever.
                                return error

                        try:
                            selection = await query(select_source)
                            if isinstance(selection, (NativeTreeError, Session.DoesNotExist)):
                                raise selection
                        except (NativeTreeError, Session.DoesNotExist):
                            if outstanding:
                                raise
                            classify(active_root)
                            if active_root in member_roots:
                                member_root_failures[active_root] = member_root_failures.get(active_root, 0) + 1
                                if member_root_failures[active_root] >= MEMBER_ROOT_ATTEMPTS:
                                    logger.warning(
                                        "Dropping malformed native tree %s after %d attempts until the next start",
                                        active_root, MEMBER_ROOT_ATTEMPTS,
                                    )
                                    member_roots.discard(active_root)
                                    dropped_member_roots.add(active_root)
                            deferred[active_root] = now + 60
                            active_root = None
                            continue
                        if selection is None:
                            break
                        if selection.source_id is not None:
                            candidates = [selection.source_id]
                            break
                        if not selection.required_pending:
                            member_roots.discard(active_root)
                            classify(active_root)
                            await progress({active_root})
                            active_root = None
                            continue
                        if outstanding:
                            break
                        deadlines = [deadline for sid, deadline in retry_at.items() if sid in cooling]
                        if selection.next_eligible_at is not None:
                            deadlines.append(now + max(0, selection.next_eligible_at - time.time()))
                        # An explicit blocked outcome releases only this root's search barrier.
                        classify(active_root)
                        deferred[active_root] = min(deadlines, default=now + 60)
                        active_root = None
                    next_deadline = min(deferred.values(), default=None)
                    if not candidates and active_root is None:
                        root_deadline = await query(lambda cutoff=cutoff, deferred=deferred: next_root_eligibility(
                            ctx.provider, ctx.compute_version, cutoff=cutoff, excluded_root_ids=deferred,
                        ))
                        if root_deadline is not None:
                            deadline = time.monotonic() + max(0, root_deadline - time.time())
                            next_deadline = min(next_deadline, deadline) if next_deadline is not None else deadline
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
                        selection_due = True
                        continue
                    selection_due = True
                    pending.add(sid)
                    awaiting_status[sid] = None
                    context = await query(lambda sid=sid: tree_completion_context(sid, ctx.compute_version))
                    ctx.command_queue.put_nowait({"session_id": sid, "tree_completion": context})
                    continue
                if not outstanding and not member_roots and not await query(outdated().exists):
                    if completion is not None:
                        # Acknowledgments precede notifications and project work.
                        # Only the terminal writer future confirms their full drain.
                        ctx.command_queue.put_nowait(None)
                        stopped = asyncio.create_task(ctx.stop_event.wait())
                        try:
                            await asyncio.wait((completion, stopped), return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            await cancel_and_wait(stopped)
                        if ctx.stop_event.is_set():
                            break
                        failed_count = completion.result()
                        if failed_count:
                            logger.error("DB writer reported %d failed compute result(s)", failed_count)
                        await stop_compute_worker(ctx)
                        completion = None
                    # Source observations can change while notifications drain.
                    # A closed successful run can safely give way to a fresh run.
                    if await query(outdated().exists) or selection_changed.is_set():
                        selection_due = True
                        continue
                    for root in tuple(unclassified):
                        classify(root)
                    await progress(completed=True)
                    if selection_changed.is_set():
                        selection_due = True
                        continue
                    completed_normally = True
                    break
                # Worker status uses a bounded bridge. Its short check never
                # repeats a candidate read while the pipeline is full.
                timeout = max(0, next_deadline - time.monotonic()) if next_deadline is not None else None
                if outstanding:
                    timeout = min(timeout, COMPUTE_STATUS_POLL_SECONDS) if timeout is not None else COMPUTE_STATUS_POLL_SECONDS
                changed = asyncio.create_task(selection_changed.wait())
                stopped = asyncio.create_task(ctx.stop_event.wait())
                outcome = asyncio.create_task(applied.get())
                try:
                    done, _ = await asyncio.wait(
                        (changed, stopped, outcome), timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if outcome in done:
                        applied.put_nowait(outcome.result())
                finally:
                    for waiter in (changed, stopped, outcome):
                        waiter.cancel()
                    await asyncio.gather(changed, stopped, outcome, return_exceptions=True)
            except Exception:
                logger.exception("Background compute run failed; retiring before recovery")
                selection_due = True
                faulty = next(iter(awaiting_status), next(iter(pending), None))
                await retire()
                if faulty is not None:
                    failed(faulty)
                    continue
                if not await _wait_for_retry_delay(ctx.stop_event, process_delay):
                    break
                process_delay = min(process_delay * 2, 60.0)
    finally:
        if not completed_normally:
            await retire()
    # Normal completion follows the authoritative idle check. An explicit
    # stop reaches this return only after the retirement guard drains.
    return True
