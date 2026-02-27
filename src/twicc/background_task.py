"""
Background compute task for session metadata.

Processes existing sessions that need metadata computation at startup, then stops.
New sessions created by the watcher get compute_version set at creation time.

Architecture:
- A separate Process handles CPU-intensive work (JSON parsing, metadata computation)
- The worker process only READS from the database (WAL mode supports multiple readers)
- All database WRITES happen in the main process via a Queue
- This eliminates "database is locked" errors by serializing all writes in the event loop
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from contextlib import suppress
from dataclasses import dataclass, field
from multiprocessing import Event as MPEvent, Process, Queue

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from twicc.compute import apply_session_complete, load_project_directories, load_project_git_roots
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_project, serialize_session
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)


@dataclass
class ComputeContext:
    """Mutable state for the background compute pipeline.

    Created once at startup and passed explicitly to all functions
    that need access to the compute infrastructure.
    """

    command_queue: Queue = field(default_factory=Queue)
    result_queue: Queue = field(default_factory=Queue)
    worker_stop_event: MPEvent = field(default_factory=MPEvent)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    process: Process | None = None


def stop_background_task(ctx: ComputeContext) -> None:
    """Signal the background compute task to stop and terminate worker process."""
    logger.info("stop_background_task: starting shutdown...")

    # Signal asyncio tasks to stop
    ctx.stop_event.set()
    logger.info("stop_background_task: asyncio stop_event set")

    # Signal worker process to stop via multiprocessing event
    ctx.worker_stop_event.set()
    logger.info("stop_background_task: worker_stop_event set")

    # Send stop signal to worker process via queue (backup)
    try:
        ctx.command_queue.put_nowait(None)  # None = stop signal
        logger.info("stop_background_task: stop signal sent to queue")
    except Exception as e:
        logger.warning(f"stop_background_task: failed to send stop signal to queue: {e}")

    # Wait for worker process to exit gracefully, then terminate if needed
    if ctx.process is not None and ctx.process.is_alive():
        logger.info(f"stop_background_task: waiting for worker process (PID: {ctx.process.pid}) to exit...")
        ctx.process.join(timeout=2.0)
        if ctx.process.is_alive():
            logger.warning("stop_background_task: worker process still alive, terminating...")
            ctx.process.terminate()
            ctx.process.join(timeout=1.0)
            if ctx.process.is_alive():
                logger.error("stop_background_task: worker process did not terminate, killing...")
                ctx.process.kill()
                ctx.process.join(timeout=0.5)
        else:
            logger.info("stop_background_task: worker process exited gracefully")
        ctx.process = None
    else:
        logger.info("stop_background_task: no worker process to stop")

    # Cancel queue threads to prevent blocking on shutdown
    # (don't wait for queue data to be flushed)
    with suppress(Exception):
        ctx.command_queue.cancel_join_thread()
        ctx.command_queue.close()
    with suppress(Exception):
        ctx.result_queue.cancel_join_thread()
        ctx.result_queue.close()

    logger.info("stop_background_task: shutdown complete")


# =============================================================================
# Worker Process Functions (run in separate process)
# =============================================================================


def compute_worker_main(command_queue: Queue, result_queue: Queue, stop_event: MPEvent) -> None:
    """
    Main function running in the compute worker process.

    Receives session_ids via command_queue, computes metadata (READ-ONLY DB access),
    sends update batches via result_queue.

    This function runs in a separate process and must initialize Django itself.
    """
    import django
    django.setup()

    import logging
    worker_logger = logging.getLogger(__name__)

    from twicc.compute import compute_session_metadata

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
            except Exception:
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
                    compute_session_metadata(session_id, result_queue)
                except Exception as e:
                    worker_logger.error(f"Error computing session {session_id}: {e}")
                    result_queue.put({
                        'type': 'error',
                        'session_id': session_id,
                        'error': str(e),
                    })

        except Exception as e:
            worker_logger.error(f"Unexpected error in compute worker: {e}")

    # Signal the consumer that the worker is done
    import orjson
    result_queue.put(orjson.dumps({'type': 'done'}))
    worker_logger.info("Compute worker sent 'done' signal")

    # Drain command queue before exiting to prevent blocking
    worker_logger.info("Compute worker draining command queue...")
    drained = 0
    while True:
        try:
            command_queue.get_nowait()
            drained += 1
        except Exception:
            break
    if drained:
        worker_logger.info(f"Compute worker drained {drained} commands from queue")

    worker_logger.info("Compute worker process stopped")


def start_compute_process(ctx: ComputeContext) -> None:
    """Start the compute worker process if not already running."""
    if ctx.process is None or not ctx.process.is_alive():
        # Reset stop event for new process
        ctx.worker_stop_event = MPEvent()
        ctx.process = Process(
            target=compute_worker_main,
            args=(ctx.command_queue, ctx.result_queue, ctx.worker_stop_event),
            daemon=True,
            name="compute-worker",
        )
        ctx.process.start()
        logger.info(f"Started compute worker process (PID: {ctx.process.pid})")


async def _handle_compute_done(session_id: str) -> None:
    """Handle completion of a session compute - broadcast updates."""
    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "session_updated",
                    "session": serialize_session(session),
                },
            },
        )

        if project := await sync_to_async(Project.objects.filter(id=session.project_id).first)():
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )
    except Session.DoesNotExist:
        logger.warning(f"Session {session_id} not found for broadcast")
    except Exception as e:
        logger.error(f"Error broadcasting updates for {session_id}: {e}")


@sync_to_async
def _flush_pending_activities(pending_activity_days: dict[str, set]) -> None:
    """Flush accumulated activity recalculations for all projects."""
    from twicc.core.models import PeriodicActivity
    for project_id, days in pending_activity_days.items():
        PeriodicActivity.recalculate_for_days(project_id, days, do_global=False)
    days = set.union(*pending_activity_days.values())
    PeriodicActivity.recalculate_for_days(None, days, do_global=True)


async def consume_compute_results(
    ctx: ComputeContext,
    worker_done_event: asyncio.Event,
    *,
    display_session_ids: set[str] | None = None,
    total_display: int = 0,
) -> None:
    """
    Consume results from the compute worker and apply DB writes.

    Runs in the main process event loop. All DB writes happen here,
    ensuring no concurrent writes with the FileWatcher.

    Sets worker_done_event when the worker signals it has finished processing
    all sessions (via a 'done' message in the result queue).

    Args:
        ctx: The compute context with queues and events.
        worker_done_event: Set when all results have been processed.
        display_session_ids: Set of session IDs that are real sessions (not subagents),
            used to filter progress broadcasting. If None, all sessions are counted.
        total_display: Total number of real sessions for progress display.
    """
    import orjson

    from collections import defaultdict
    from datetime import date as date_cls

    # Accumulate affected days per project across multiple sessions
    batch_activity_count = 50
    pending_activity_days: dict[str, set] = defaultdict(set)
    sessions_since_activities_flush = 0

    # Progress broadcasting — only count real sessions (not subagents) for display
    completed_count = 0

    while not ctx.stop_event.is_set():
        # Collect available messages (non-blocking)
        try:
            raw_msg = ctx.result_queue.get_nowait()
            # Deserialize from orjson bytes
            msg = orjson.loads(raw_msg)
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

        # Process collected message
        msg_type = msg.get('type')

        try:
            if msg_type == 'session_complete':
                # New unified message type - all data in one message
                await sync_to_async(apply_session_complete)(msg)
                await _handle_compute_done(msg['session_id'])

                # Broadcast progress only for real sessions (not subagents)
                session_id = msg['session_id']
                if display_session_ids is None or session_id in display_session_ids:
                    completed_count += 1
                    await broadcast_startup_progress(
                        "background_compute", completed_count, total_display
                    )

                # Accumulate affected days for batched activity recalculation
                affected_days = msg.get('affected_days')
                project_id = msg.get('project_id')
                if project_id and affected_days:
                    pending_activity_days[project_id].update(
                        date_cls.fromisoformat(d) for d in affected_days
                    )
                    sessions_since_activities_flush += 1

            elif msg_type == 'done':
                # Worker has finished processing all sessions
                logger.info("consume_compute_results: received 'done' from worker")
                break

            elif msg_type == 'error':
                logger.error(f"Compute error for {msg['session_id']}: {msg['error']}")

            else:
                logger.error(f"Unexpected result message type: {msg_type} => {msg}")

        except Exception as e:
            logger.error(f"Error processing result message {msg_type}: {e}", exc_info=True)

        # Flush activity recalculations every batch_activity_count sessions
        if sessions_since_activities_flush >= batch_activity_count:
            await _flush_pending_activities(pending_activity_days)
            pending_activity_days.clear()
            sessions_since_activities_flush = 0

        # Yield to event loop between batches
        await asyncio.sleep(0)

    # Flush any remaining pending activity recalculations before shutdown
    if pending_activity_days:
        await _flush_pending_activities(pending_activity_days)
        pending_activity_days.clear()

    # Signal that all results have been processed and activities flushed
    worker_done_event.set()

    logger.info("consume_compute_results: stopped")


async def start_background_compute_task(ctx: ComputeContext) -> None:
    """
    Background task that processes existing sessions needing computation at startup.

    Architecture:
    - Starts a separate Process for CPU-intensive work (JSON parsing, metadata computation)
    - The worker process only READS from the database
    - All database WRITES happen in the main process via consume_compute_results()
    - This eliminates "database is locked" errors

    This task processes all sessions with outdated or missing compute_version,
    then stops. New sessions created by the watcher get compute_version set
    at creation time, so they don't need background reprocessing.

    Progress logging: Logs progress at 10% intervals during processing.
    """

    # Initialize progress tracking
    processed_count = 0
    last_logged_percent = 0
    total_to_compute = await sync_to_async(Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).count)()
    last_logged_time = time.monotonic()

    if total_to_compute == 0:
        logger.info("Background compute: no sessions to process")
        await broadcast_startup_progress("background_compute", 0, 0, completed=True)
        return

    # Count only real sessions (not subagents) for progress display.
    # The actual compute processes ALL sessions, but users only care about session count.
    sessions_to_display = await sync_to_async(
        lambda: set(
            Session.objects.filter(type=SessionType.SESSION)
            .exclude(compute_version=settings.CURRENT_COMPUTE_VERSION)
            .values_list("id", flat=True)
        )
    )()
    total_display = len(sessions_to_display)

    # Broadcast initial progress state (0/N) — using display total (sessions only)
    await broadcast_startup_progress("background_compute", 0, total_display)

    # Load project caches at startup
    await sync_to_async(load_project_directories)()
    await sync_to_async(load_project_git_roots)()

    # Start the worker process
    start_compute_process(ctx)

    # Start the result consumer as a concurrent task (passes display info for progress broadcasting)
    worker_done_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        consume_compute_results(
            ctx, worker_done_event,
            display_session_ids=sessions_to_display,
            total_display=total_display,
        )
    )

    logger.info(f"Background compute task started ({total_to_compute} sessions to process)")

    # Track sessions sent to worker to avoid sending duplicates
    sent_sessions: set[str] = set()

    while not ctx.stop_event.is_set():
        try:
            # Find next session needing computation
            session = await sync_to_async(Session.objects.exclude(
                compute_version=settings.CURRENT_COMPUTE_VERSION
            ).order_by('-mtime').first)()

            if session is None:
                # All sessions processed, we're done
                break

            # Skip if already sent to worker
            if session.id in sent_sessions:
                await asyncio.sleep(0.01)
                continue

            # Send command to worker process (non-blocking)
            sent_sessions.add(session.id)
            ctx.command_queue.put({'session_id': session.id})

            # Progress logging
            if total_to_compute and total_to_compute > 0:
                processed_count += 1
                current_percent = (processed_count * 100) // total_to_compute

                # Log at 10% intervals
                if current_percent >= last_logged_percent + 10:
                    last_logged_percent = (current_percent // 10) * 10
                    now = time.monotonic()
                    elapsed = now - last_logged_time
                    last_logged_time = now
                    logger.info(f"Background compute progress: {last_logged_percent}% ({processed_count}/{total_to_compute}) [{elapsed:.1f}s]")

            # Small yield to allow result consumer to process
            await asyncio.sleep(0.01)

        except Exception as e:
            logger.error(f"Error in background compute task: {e}", exc_info=True)
            # Wait before retrying to avoid tight error loop
            try:
                await asyncio.wait_for(ctx.stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    logger.info(f"Background compute: all sessions sent to worker ({processed_count}/{total_to_compute})")

    # Send stop signal to worker so it finishes and sends 'done'
    ctx.command_queue.put(None)

    # Wait for the consumer to receive the worker's 'done' signal
    # (which means all results have been processed and activities flushed)
    await worker_done_event.wait()

    # Broadcast completion (using display total — sessions only, not subagents)
    await broadcast_startup_progress("background_compute", total_display, total_display, completed=True)

    # Stop the consumer task gracefully via stop_event
    ctx.stop_event.set()
    await consumer_task

    # Stop the worker process
    stop_background_task(ctx)

    logger.info("Background compute task completed")
