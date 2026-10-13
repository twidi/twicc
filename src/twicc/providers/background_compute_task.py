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
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import orjson
from asgiref.sync import sync_to_async

from twicc.providers.compute_ipc import ComputeResultStream, ComputeStatusSender, ComputeStopFlag
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

COMPUTE_STALE_RETRY_DELAYS = (1.0, 2.0, 4.0)
COMPUTE_STALE_RECOVERY_DELAYS = (15.0, 30.0, 60.0)
COMPUTE_STALE_RECOVERY_INTERVAL = 60.0


class ComputeRetryResult(NamedTuple):
    """Outcome of a compute pass and its stale-revision recovery."""

    failed_count: int
    remaining_session_ids: list[str]


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


async def run_compute_with_stale_retries(
    initial_session_ids: list[str],
    *,
    run_pass: Callable[[list[str]], Awaitable[int]],
    load_remaining: Callable[[list[str]], Awaitable[list[str]]],
    load_offsets: Callable[[list[str]], Awaitable[dict[str, int]]],
    stop_event: asyncio.Event,
    wait_for_delay: Callable[[asyncio.Event, float], Awaitable[bool]] = _wait_for_retry_delay,
    retry_delays: tuple[float, ...] = COMPUTE_STALE_RETRY_DELAYS,
) -> ComputeRetryResult:
    """Run one complete pass, then retry only quiet sessions still outdated.

    A session remains outdated when the revision guard rejected a stale full
    compute result. Each retry window compares ``last_offset`` before and after
    its delay. A changing session waits for a later window instead of starting
    another full compute that is already likely to become stale.

    Failed sessions remain eligible. Query stored versions after each pass;
    partial commits and successful sessions need no repeat computation.
    """

    session_ids = list(initial_session_ids)
    if not session_ids or stop_event.is_set():
        return ComputeRetryResult(0, session_ids)

    failed_count = await _run_recoverable_pass(run_pass, session_ids)
    if stop_event.is_set():
        return ComputeRetryResult(failed_count, session_ids)

    remaining = await read_compute_state(load_remaining, session_ids, stop_event, wait_for_delay, session_ids)
    if failed_count and len(remaining) > 1:
        remaining = remaining[1:] + remaining[:1]
    for retry_number, delay in enumerate(retry_delays, start=1):
        if not remaining or stop_event.is_set():
            break

        offsets_before = await read_compute_state(load_offsets, remaining, stop_event, wait_for_delay, {})
        if not await wait_for_delay(stop_event, delay):
            break
        offsets_after = await read_compute_state(load_offsets, remaining, stop_event, wait_for_delay, {})
        stable = [
            session_id
            for session_id in remaining
            if session_id in offsets_before
            and offsets_before[session_id] == offsets_after.get(session_id)
        ]

        if failed_count and len(stable) > 1:
            # Cool the last rotated candidate for one pass. A crashing worker
            # must not fence healthy results before the writer can apply them.
            stable = stable[:-1]
        if stable:
            logger.info(
                "Background compute: stale retry %d/%d for %d quiet session(s)",
                retry_number,
                len(retry_delays),
                len(stable),
            )
            failed_count = await _run_recoverable_pass(run_pass, stable)

        remaining = await read_compute_state(load_remaining, remaining, stop_event, wait_for_delay, remaining)
        if failed_count and len(remaining) > 1:
            remaining = remaining[1:] + remaining[:1]

    return ComputeRetryResult(failed_count if remaining else 0, remaining)


async def recover_stale_compute_sessions(
    initial_session_ids: list[str],
    *,
    run_pass: Callable[[list[str]], Awaitable[int]],
    load_remaining: Callable[[list[str]], Awaitable[list[str]]],
    load_offsets: Callable[[list[str]], Awaitable[dict[str, int]]],
    stop_event: asyncio.Event,
    wait_for_delay: Callable[[asyncio.Event, float], Awaitable[bool]] = _wait_for_retry_delay,
    recovery_delays: tuple[float, ...] = COMPUTE_STALE_RECOVERY_DELAYS,
    recovery_interval: float = COMPUTE_STALE_RECOVERY_INTERVAL,
) -> ComputeRetryResult:
    """Monitor stale sessions until they become quiet enough to recompute.

    The finite delay sequence ramps up after the startup retries. Once it is
    exhausted, the final interval repeats until every session is current or
    provider shutdown interrupts the wait. Only an unchanged ``last_offset``
    starts an expensive compute pass.
    """

    remaining = list(initial_session_ids)
    failed_count = 0
    delay_index = 0

    while remaining and not stop_event.is_set():
        if delay_index < len(recovery_delays):
            delay = recovery_delays[delay_index]
        else:
            delay = recovery_interval
        delay_index += 1
        delay = min(delay, 60.0)

        offsets_before = await read_compute_state(load_offsets, remaining, stop_event, wait_for_delay, {})
        if not await wait_for_delay(stop_event, delay):
            break
        offsets_after = await read_compute_state(load_offsets, remaining, stop_event, wait_for_delay, {})
        stable = [
            session_id
            for session_id in remaining
            if session_id in offsets_before
            and offsets_before[session_id] == offsets_after.get(session_id)
        ]

        if failed_count and len(stable) > 1:
            # Cool the last rotated candidate for one pass. A crashing worker
            # must not fence healthy results before the writer can apply them.
            stable = stable[:-1]
        if stable:
            logger.info(
                "Background compute: recovery pass for %d quiet session(s) after %.0fs",
                len(stable),
                delay,
            )
            failed_count = await _run_recoverable_pass(run_pass, stable)

        remaining = await read_compute_state(load_remaining, remaining, stop_event, wait_for_delay, remaining)
        if failed_count and len(remaining) > 1:
            remaining = remaining[1:] + remaining[:1]

    return ComputeRetryResult(failed_count if remaining else 0, remaining)


async def _run_recoverable_pass(
    run_pass: Callable[[list[str]], Awaitable[int]], session_ids: list[str],
) -> int:
    try:
        return await run_pass(session_ids)
    except Exception:
        logger.exception("Background compute run failed; outdated sessions remain eligible")
        return len(session_ids)


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
    # Codex enables this optional worker→coordinator channel. Claude uses
    # the generic producer and must not accumulate status messages it never reads.
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
                    compute.compute_session_metadata(session_id, result_queue, run_id)
                    if status_queue is not None:
                        status_queue.put({"type": "computed", "session_id": session_id})
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
    """
    Background task that processes existing sessions needing computation at startup.

    Architecture:
    - Starts a separate Process for CPU-intensive work (JSON parsing, metadata computation)
    - The worker process only READS from the database
    - All database WRITES happen via the DB writer (:mod:`db_writer`),
      which serializes writes across providers and phases

    This task processes all sessions with outdated or missing compute_version.
    It releases ``initial_done`` after the finite startup classification pass.
    Successful progress stays pending until every selected session is current.
    A low-cost recovery monitor retries sessions that remain outdated. New
    sessions created by the watcher get compute_version set at creation time,
    so they don't need background reprocessing.
    """
    from twicc.core.models import Session, SessionType
    from twicc.projects import load_project_directories, load_project_git_roots
    from twicc.providers.db_writer import arm_compute_completion

    async def query(read):
        async def load(_session_ids):
            return await sync_to_async(read)()
        return await read_compute_state(load, [], ctx.stop_event, stopped=[])

    provider_value = ctx.provider.value

    # Count sessions needing computation
    total_to_compute = await query(Session.objects.filter(
        provider=ctx.provider,
    ).exclude(
        compute_version=ctx.compute_version
    ).count)

    if ctx.stop_event.is_set():
        return

    if total_to_compute == 0:
        if initial_done is not None:
            initial_done.set()
        logger.info("Background compute: no sessions to process")
        # Report the total session count so the frontend can show "N/N" instead of "0/0"
        total_display = await query(
            Session.objects.filter(
                provider=ctx.provider, type=SessionType.SESSION,
            ).count
        )
        if not ctx.stop_event.is_set():
            await broadcast_startup_progress(
                "background_compute", total_display, total_display,
                provider=provider_value, completed=True,
            )
        return

    # Count only real sessions (not subagents) for progress display.
    # The actual compute processes ALL sessions, but users only care about session count.
    sessions_to_display = await query(
        lambda: set(
            Session.objects.filter(
                provider=ctx.provider, type=SessionType.SESSION,
            )
            .exclude(compute_version=ctx.compute_version)
            .values_list("id", flat=True)
        )
    )
    total_display = len(sessions_to_display)
    ready_session_ids: set[str] = set()

    # Broadcast initial progress state (0/N) — using display total (sessions only)
    await broadcast_startup_progress(
        "background_compute", 0, total_display, provider=provider_value
    )

    # Load project caches at startup
    await query(load_project_directories)
    await query(load_project_git_roots)

    try:
        # Load all session IDs needing computation in one query, ordered by most recent first
        session_ids_to_compute = await query(
            lambda: list(
                Session.objects
                .filter(provider=ctx.provider)
                .exclude(compute_version=ctx.compute_version)
                .order_by('-mtime')
                .values_list('id', flat=True)
            )
        )

        async def run_pass(session_ids: list[str]) -> int:
            # A failed teardown retains its resources. Never arm a replacement
            # until another retirement attempt closes the previous transport.
            if ctx.process is not None or ctx.result_stream is not None:
                await retire_compute_run(ctx)
            if ctx.stop_event.is_set():
                return 0

            # Arm each pass before spawning its worker. A new run_id prevents a
            # completed retry from accepting messages from the previous worker.
            run_id, done_future = arm_compute_completion(
                ctx.provider,
                sessions_to_display,
                total_display,
                ready_session_ids=ready_session_ids,
            )
            ctx.run_id = run_id
            try:
                start_compute_process(ctx)
                initialization_failed = 0
                for session_id in session_ids:
                    if ctx.stop_event.is_set():
                        break
                    from twicc.providers.db_writer import InitializeComputeStorageJob, submit_async_job

                    try:
                        await submit_async_job(InitializeComputeStorageJob(
                            ctx.provider, session_id, asyncio.get_running_loop().create_future(),
                        ))
                    except Exception:
                        initialization_failed += 1
                        logger.exception("Compute storage initialization failed for %s", session_id)
                        continue
                    ctx.command_queue.put({"session_id": session_id})
                ctx.command_queue.put(None)
                # Shield retains the future for retirement after task cancellation.
                failed_count = await asyncio.shield(done_future)
                return failed_count + initialization_failed
            finally:
                await retire_compute_run(ctx, done_future)

        async def publish_ready_progress(*, completed: bool = False) -> None:
            confirmed = await query(lambda: set(
                Session.objects.filter(
                    id__in=sessions_to_display, provider=ctx.provider,
                    compute_version=ctx.compute_version, unavailable_reason__isnull=True,
                ).values_list("id", flat=True)
            ))
            ready_session_ids.update(confirmed)
            await broadcast_startup_progress(
                "background_compute", len(ready_session_ids), total_display,
                provider=provider_value, completed=completed and len(confirmed) == total_display,
            )

        async def load_remaining(candidate_ids: list[str]) -> list[str]:
            await publish_ready_progress()
            outdated = await query(
                lambda: set(
                    Session.objects.filter(
                        id__in=candidate_ids,
                        provider=ctx.provider,
                    ).exclude(
                        compute_version=ctx.compute_version,
                    ).values_list("id", flat=True)
                )
            )
            return [session_id for session_id in candidate_ids if session_id in outdated]

        async def load_offsets(candidate_ids: list[str]) -> dict[str, int]:
            pairs = await query(
                lambda: list(
                    Session.objects.filter(
                        id__in=candidate_ids,
                        provider=ctx.provider,
                    ).values_list("id", "last_offset")
                )
            )
            return dict(pairs)

        logger.info(f"Background compute task started ({total_to_compute} sessions to process)")
        retry_result = await run_compute_with_stale_retries(
            session_ids_to_compute,
            run_pass=run_pass,
            load_remaining=load_remaining,
            load_offsets=load_offsets,
            stop_event=ctx.stop_event,
        )

        if initial_done is not None:
            initial_done.set()

        if retry_result.remaining_session_ids and not ctx.stop_event.is_set():
            logger.warning(
                "Background compute for %s left %d outdated session(s); recovery continues",
                provider_value, len(retry_result.remaining_session_ids),
            )
            retry_result = await recover_stale_compute_sessions(
                retry_result.remaining_session_ids,
                run_pass=run_pass, load_remaining=load_remaining,
                load_offsets=load_offsets, stop_event=ctx.stop_event,
            )

        if not retry_result.remaining_session_ids and not ctx.stop_event.is_set():
            await publish_ready_progress(completed=True)

    finally:
        # Stop the worker process. Idempotent if it has already exited.
        await retire_compute_run(ctx)

    logger.info("Background compute task completed")
