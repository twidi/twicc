"""
Background tasks for the TWICC application.

This module provides background tasks:
- Compute task: Processes existing sessions that need metadata computation at startup, then stops.
  New sessions created by the watcher get compute_version set at creation time.
- Price sync task: Synchronizes model prices from OpenRouter API (runs continuously)

Architecture for compute task:
- A separate Process handles CPU-intensive work (JSON parsing, metadata computation)
- The worker process only READS from the database (WAL mode supports multiple readers)
- All database WRITES happen in the main process via a Queue
- This eliminates "database is locked" errors by serializing all writes in the event loop
"""

from __future__ import annotations

import asyncio
import logging
import queue
from decimal import Decimal
from multiprocessing import Event as MPEvent, Process, Queue

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from twicc.compute import ensure_project_git_root, get_project_git_root, load_project_directories, load_project_git_roots
from twicc.core.models import AgentLink, Project, Session, SessionItem, ToolResultLink
from twicc.core.pricing import sync_model_prices
from twicc.core.usage import compute_period_costs, fetch_and_save_usage, has_oauth_credentials
from twicc.core.models import UsageSnapshot
from twicc.core.serializers import serialize_project, serialize_session, serialize_usage_snapshot

logger = logging.getLogger(__name__)

# Worker process for computation
_compute_process: Process | None = None

# IPC queues for communication with worker process
_command_queue: Queue | None = None  # main -> worker (session_id to process)
_result_queue: Queue | None = None   # worker -> main (update batches)

# Multiprocessing event to signal worker process to stop
_worker_stop_event: MPEvent | None = None

# Stop event for graceful shutdown (compute task)
_stop_event: asyncio.Event | None = None

# Stop event for price sync task
_price_sync_stop_event: asyncio.Event | None = None

# Stop event for usage sync task
_usage_sync_stop_event: asyncio.Event | None = None

# Progress tracking for computation
_total_to_compute: int | None = None
_processed_count: int = 0
_last_logged_percent: int = 0
_last_logged_time: float = 0.0


def get_queues() -> tuple[Queue, Queue]:
    """Get or create the IPC queues for worker communication."""
    global _command_queue, _result_queue
    if _command_queue is None:
        _command_queue = Queue()
    if _result_queue is None:
        _result_queue = Queue()
    return _command_queue, _result_queue


def get_worker_stop_event() -> MPEvent:
    """Get or create the multiprocessing stop event for the worker process."""
    global _worker_stop_event
    if _worker_stop_event is None:
        _worker_stop_event = MPEvent()
    return _worker_stop_event


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event for the compute task."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def get_price_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the price sync task."""
    global _price_sync_stop_event
    if _price_sync_stop_event is None:
        _price_sync_stop_event = asyncio.Event()
    return _price_sync_stop_event


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_background_task() -> None:
    """Signal the background compute task to stop and terminate worker process."""
    global _compute_process, _command_queue, _result_queue, _stop_event, _worker_stop_event
    logger.info("stop_background_task: starting shutdown...")

    # Signal asyncio tasks to stop
    if _stop_event is not None:
        _stop_event.set()
        logger.info("stop_background_task: asyncio stop_event set")

    # Signal worker process to stop via multiprocessing event
    if _worker_stop_event is not None:
        _worker_stop_event.set()
        logger.info("stop_background_task: worker_stop_event set")

    # Send stop signal to worker process via queue (backup)
    if _command_queue is not None:
        try:
            _command_queue.put_nowait(None)  # None = stop signal
            logger.info("stop_background_task: stop signal sent to queue")
        except Exception as e:
            logger.warning(f"stop_background_task: failed to send stop signal to queue: {e}")

    # Wait for worker process to exit gracefully, then terminate if needed
    if _compute_process is not None and _compute_process.is_alive():
        logger.info(f"stop_background_task: waiting for worker process (PID: {_compute_process.pid}) to exit...")
        _compute_process.join(timeout=2.0)
        if _compute_process.is_alive():
            logger.warning("stop_background_task: worker process still alive, terminating...")
            _compute_process.terminate()
            _compute_process.join(timeout=1.0)
            if _compute_process.is_alive():
                logger.error("stop_background_task: worker process did not terminate, killing...")
                _compute_process.kill()
                _compute_process.join(timeout=0.5)
        else:
            logger.info("stop_background_task: worker process exited gracefully")
        _compute_process = None
    else:
        logger.info("stop_background_task: no worker process to stop")

    # Cancel queue threads to prevent blocking on shutdown
    # (don't wait for queue data to be flushed)
    if _command_queue is not None:
        try:
            _command_queue.cancel_join_thread()
            _command_queue.close()
        except Exception:
            pass
    if _result_queue is not None:
        try:
            _result_queue.cancel_join_thread()
            _result_queue.close()
        except Exception:
            pass

    _command_queue = None
    _result_queue = None
    _worker_stop_event = None
    logger.info("stop_background_task: shutdown complete")


def stop_price_sync_task() -> None:
    """Signal the price sync task to stop."""
    global _price_sync_stop_event
    if _price_sync_stop_event is not None:
        _price_sync_stop_event.set()


def stop_usage_sync_task() -> None:
    """Signal the usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


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


def start_compute_process() -> Process:
    """Start the compute worker process if not already running."""
    global _compute_process, _worker_stop_event
    if _compute_process is None or not _compute_process.is_alive():
        command_queue, result_queue = get_queues()
        # Reset stop event for new process
        _worker_stop_event = MPEvent()
        _compute_process = Process(
            target=compute_worker_main,
            args=(command_queue, result_queue, _worker_stop_event),
            daemon=True,
            name="compute-worker",
        )
        _compute_process.start()
        logger.info(f"Started compute worker process (PID: {_compute_process.pid})")
    return _compute_process


async def run_initial_price_sync() -> None:
    """
    Run the initial price sync before starting other background tasks.

    This ensures fresh prices are available before the background compute task
    starts processing sessions. Must be awaited at startup.

    If sync fails, logs a warning but continues - existing prices in DB or
    DEFAULT_FAMILY_PRICES will be used as fallback.
    """
    logger.info("Running initial price sync...")
    try:
        stats = await asyncio.to_thread(sync_model_prices)
        logger.info(
            f"Initial price sync completed: {stats['created']} created, {stats['unchanged']} unchanged"
        )
    except Exception as e:
        logger.warning(
            f"Initial price sync failed: {e}. Will use existing DB prices or default family prices."
        )


# =============================================================================
# Result Queue Consumer (runs in main process event loop)
# =============================================================================


@sync_to_async
def _apply_batch_update(msg: dict) -> None:
    """Apply a batch of SessionItem updates from the worker."""
    updates = msg['updates']
    fields = msg['fields']

    # Reconstruct SessionItem objects for bulk_update
    items = []
    for upd in updates:
        item = SessionItem(id=upd['id'])
        for field in fields:
            value = upd.get(field)
            # Handle Decimal fields (serialized as strings)
            if field == 'cost' and value is not None:
                value = Decimal(value)
            setattr(item, field, value)
        items.append(item)

    if items:
        SessionItem.objects.bulk_update(items, fields)


@sync_to_async
def _apply_links_create(msg: dict) -> None:
    """Create ToolResultLinks and AgentLinks from worker data."""
    tool_result_links_data = msg.get('tool_result_links', [])
    if tool_result_links_data:
        links = [ToolResultLink(**d) for d in tool_result_links_data]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_data = msg.get('agent_links', [])
    if agent_links_data:
        links = [AgentLink(**d) for d in agent_links_data]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)


@sync_to_async
def _apply_session_update(msg: dict) -> None:
    """Update session fields from worker data."""
    from twicc.compute import ensure_project_directory, update_project_total_cost

    session_id = msg['session_id']
    fields = msg['fields']

    # Handle Decimal fields
    for decimal_field in ('self_cost', 'subagents_cost', 'total_cost'):
        if decimal_field in fields and fields[decimal_field] is not None:
            fields[decimal_field] = Decimal(fields[decimal_field])

    Session.objects.filter(id=session_id).update(**fields)

    # Update titles if present
    if 'titles' in msg:
        for target_id, title in msg['titles'].items():
            Session.objects.filter(id=target_id).update(title=title)

    # Update project directory if provided
    if 'project_directory' in msg:
        project_id = msg['project_id']
        directory = msg['project_directory']
        if project_id and directory:
            ensure_project_directory(project_id, directory)

    # Update project total_cost
    if 'project_id' in msg:
        update_project_total_cost(msg['project_id'])


@sync_to_async
def _delete_session_links(session_id: str) -> None:
    """Delete all links for a session before recomputing."""
    ToolResultLink.objects.filter(session_id=session_id).delete()
    AgentLink.objects.filter(session_id=session_id).delete()


@sync_to_async
def _apply_session_complete(msg: dict) -> None:
    """
    Apply all results for a session in one go.

    This handles the new 'session_complete' message type that contains
    all updates for a session in a single message.
    """
    from twicc.compute import ensure_project_directory, update_project_total_cost

    session_id = msg['session_id']

    # 1. Delete existing links
    ToolResultLink.objects.filter(session_id=session_id).delete()
    AgentLink.objects.filter(session_id=session_id).delete()

    # 2. Apply item updates
    item_updates = msg.get('item_updates', [])
    item_fields = msg.get('item_fields', [])
    if item_updates and item_fields:
        items = []
        for upd in item_updates:
            item = SessionItem(id=upd['id'])
            for field in item_fields:
                value = upd.get(field)
                if field == 'cost' and value is not None:
                    value = Decimal(value)
                setattr(item, field, value)
            items.append(item)
        if items:
            SessionItem.objects.bulk_update(items, item_fields)

    # 3. Create links
    tool_result_links_data = msg.get('tool_result_links', [])
    if tool_result_links_data:
        links = [ToolResultLink(**d) for d in tool_result_links_data]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_data = msg.get('agent_links', [])
    if agent_links_data:
        links = [AgentLink(**d) for d in agent_links_data]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)

    # 4. Update session fields
    session_fields = msg.get('session_fields', {})
    if session_fields:
        # Handle Decimal fields
        for decimal_field in ('self_cost', 'subagents_cost', 'total_cost'):
            if decimal_field in session_fields and session_fields[decimal_field] is not None:
                session_fields[decimal_field] = Decimal(session_fields[decimal_field])
        Session.objects.filter(id=session_id).update(**session_fields)

    # 5. Update titles
    titles = msg.get('titles', {})
    for target_id, title in titles.items():
        Session.objects.filter(id=target_id).update(title=title)

    # 6. Update project directory
    project_id = msg.get('project_id')
    project_directory = msg.get('project_directory')
    if project_id and project_directory:
        ensure_project_directory(project_id, project_directory)

    # 7. Resolve project git_root if session has git info but project doesn't
    session_git_dir = session_fields.get('git_directory') if session_fields else None
    if session_git_dir and project_id and get_project_git_root(project_id) is None:
        ensure_project_git_root(project_id)

    # 8. Update activity counters (only present for real sessions, not subagents)
    activity_weekly = msg.get('activity_weekly')
    activity_daily = msg.get('activity_daily')
    if project_id and (activity_weekly or activity_daily):
        from twicc.sync import apply_activity_counts
        apply_activity_counts(project_id, activity_weekly, activity_daily)

    # 9. Update project total_cost
    if project_id:
        update_project_total_cost(project_id)


async def _handle_compute_done(session_id: str) -> None:
    """Handle completion of a session compute - broadcast updates."""
    try:
        session = await sync_to_async(Session.objects.get)(id=session_id)
        await broadcast_session_updated(session)

        project = await get_project(session.project_id)
        if project:
            await broadcast_project_updated(project)
    except Session.DoesNotExist:
        logger.warning(f"Session {session_id} not found for broadcast")
    except Exception as e:
        logger.error(f"Error broadcasting updates for {session_id}: {e}")


async def consume_compute_results(stop_event: asyncio.Event) -> None:
    """
    Consume results from the compute worker and apply DB writes.

    Runs in the main process event loop. All DB writes happen here,
    ensuring no concurrent writes with the FileWatcher.
    """
    import orjson

    _, result_queue = get_queues()

    while not stop_event.is_set():
        # Collect available messages (non-blocking)
        messages = []
        try:
            while True:
                raw_msg = result_queue.get_nowait()
                # Deserialize from orjson bytes
                msg = orjson.loads(raw_msg)
                messages.append(msg)
                if len(messages) >= 10:  # Process in batches of max 10
                    break
        except queue.Empty:
            pass

        if not messages:
            # No messages, yield and wait a bit
            await asyncio.sleep(0.05)
            continue

        # Process collected messages
        for msg in messages:
            msg_type = msg.get('type')

            try:
                if msg_type == 'session_complete':
                    # New unified message type - all data in one message
                    await _apply_session_complete(msg)
                    await _handle_compute_done(msg['session_id'])

                elif msg_type == 'delete_links':
                    await _delete_session_links(msg['session_id'])

                elif msg_type == 'batch':
                    await _apply_batch_update(msg)

                elif msg_type == 'links':
                    await _apply_links_create(msg)

                elif msg_type == 'session_update':
                    await _apply_session_update(msg)

                elif msg_type == 'done':
                    await _handle_compute_done(msg['session_id'])

                elif msg_type == 'error':
                    logger.error(f"Compute error for {msg['session_id']}: {msg['error']}")

            except Exception as e:
                logger.error(f"Error processing result message {msg_type}: {e}", exc_info=True)

        # Yield to event loop between batches
        await asyncio.sleep(0)

    # Drain result queue on shutdown to prevent blocking
    # (may already be closed by stop_background_task, so handle ValueError)
    logger.info("consume_compute_results: draining result queue...")
    drained = 0
    try:
        while True:
            try:
                result_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
    except ValueError:
        # Queue already closed, nothing to drain
        pass
    if drained:
        logger.info(f"consume_compute_results: drained {drained} messages from queue")
    logger.info("consume_compute_results: stopped")


def _reset_progress_tracking() -> None:
    """Reset progress tracking state for a new run."""
    global _total_to_compute, _processed_count, _last_logged_percent, _last_logged_time
    _total_to_compute = None
    _processed_count = 0
    _last_logged_percent = 0
    _last_logged_time = 0.0


@sync_to_async
def _count_sessions_to_compute() -> int:
    """Count the number of sessions needing computation."""
    return Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).count()


@sync_to_async
def get_next_session_to_compute() -> Session | None:
    """
    Find the next session needing metadata computation.

    Returns sessions where:
    - compute_version IS NULL (never computed)
    - compute_version != CURRENT_COMPUTE_VERSION (outdated)

    Ordered by mtime descending (most recent first).
    """
    return Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).order_by('-mtime').first()


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


@sync_to_async
def get_project(project_id: str) -> Project | None:
    """Get a project by ID."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


async def broadcast_session_updated(session: Session) -> None:
    """Broadcast session_updated message via WebSocket."""
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


async def broadcast_project_updated(project: Project) -> None:
    """Broadcast project_updated message via WebSocket."""
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


@sync_to_async
def _get_latest_usage_snapshot() -> UsageSnapshot | None:
    """Get the most recent usage snapshot from the database."""
    return UsageSnapshot.objects.first()  # ordered by -fetched_at


def _build_usage_message(
    success: bool, reason: str, has_oauth: bool, snapshot: UsageSnapshot | None
) -> dict:
    """
    Build a usage_updated message payload.

    If has_oauth is False, usage data is omitted even if a snapshot exists
    in the database (the user may have switched from OAuth to API mode).

    Includes period cost data (spent, estimated_period, estimated_monthly)
    for the 5-hour and 7-day windows when a snapshot is available.
    """
    if has_oauth and snapshot:
        period_costs = compute_period_costs(snapshot)
        usage = serialize_usage_snapshot(snapshot, period_costs=period_costs)
    else:
        usage = None

    return {
        "type": "usage_updated",
        "success": success,
        "reason": reason,  # "sync" = after API fetch, "connection" = on WS connect
        "has_oauth": has_oauth,
        "usage": usage,
    }


@sync_to_async
def _build_usage_message_sync(
    success: bool, reason: str, has_oauth: bool, snapshot: UsageSnapshot | None
) -> dict:
    """
    sync_to_async wrapper for _build_usage_message.

    Required because compute_period_costs() performs database queries
    that cannot run in an async context.
    """
    return _build_usage_message(success, reason, has_oauth, snapshot)


async def broadcast_usage_updated(success: bool) -> None:
    """
    Broadcast usage_updated message via WebSocket to all connected clients.

    Always sends the latest snapshot from the database (not necessarily
    the one just fetched), plus a success flag indicating whether the
    last fetch succeeded. If OAuth is not configured, sends has_oauth=False
    with no usage data.
    """
    oauth = await asyncio.to_thread(has_oauth_credentials)
    snapshot = await _get_latest_usage_snapshot() if oauth else None
    data = await _build_usage_message_sync(success, reason="sync", has_oauth=oauth, snapshot=snapshot)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": data,
        },
    )


async def get_usage_message_for_connection() -> dict:
    """
    Build a usage_updated message to send to a single client on WS connect.

    Returns the latest snapshot from the database with reason="connection".
    If OAuth is not configured, returns has_oauth=False with no usage data.
    """
    oauth = await asyncio.to_thread(has_oauth_credentials)
    snapshot = await _get_latest_usage_snapshot() if oauth else None
    return await _build_usage_message_sync(success=True, reason="connection", has_oauth=oauth, snapshot=snapshot)


async def start_background_compute_task() -> None:
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
    global _total_to_compute, _processed_count, _last_logged_percent

    stop_event = get_stop_event()
    command_queue, _ = get_queues()

    # Reset and initialize progress tracking
    import time
    global _last_logged_time

    _reset_progress_tracking()
    _total_to_compute = await _count_sessions_to_compute()
    _last_logged_time = time.monotonic()

    if _total_to_compute == 0:
        logger.info("Background compute: no sessions to process")
        return

    # Load project caches at startup
    await sync_to_async(load_project_directories)()
    await sync_to_async(load_project_git_roots)()

    # Start the worker process
    start_compute_process()

    # Start the result consumer as a concurrent task
    consumer_task = asyncio.create_task(consume_compute_results(stop_event))

    logger.info(f"Background compute task started ({_total_to_compute} sessions to process)")

    # Track sessions sent to worker to avoid sending duplicates
    sent_sessions: set[str] = set()

    while not stop_event.is_set():
        try:
            # Find next session needing computation
            session = await get_next_session_to_compute()

            if session is None:
                # All sessions processed, we're done
                break

            # Skip if already sent to worker
            if session.id in sent_sessions:
                await asyncio.sleep(0.1)
                continue

            # Send command to worker process (non-blocking)
            sent_sessions.add(session.id)
            command_queue.put({'session_id': session.id})

            # Progress logging
            if _total_to_compute and _total_to_compute > 0:
                _processed_count += 1
                current_percent = (_processed_count * 100) // _total_to_compute

                # Log at 10% intervals
                if current_percent >= _last_logged_percent + 10:
                    _last_logged_percent = (current_percent // 10) * 10
                    now = time.monotonic()
                    elapsed = now - _last_logged_time
                    _last_logged_time = now
                    logger.info(f"Background compute progress: {_last_logged_percent}% ({_processed_count}/{_total_to_compute}) [{elapsed:.1f}s]")

            # Small yield to allow result consumer to process
            await asyncio.sleep(0.01)

        except Exception as e:
            logger.error(f"Error in background compute task: {e}", exc_info=True)
            # Wait before retrying to avoid tight error loop
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    logger.info(f"Background compute: all sessions sent to worker ({_processed_count}/{_total_to_compute})")

    # Wait for the worker to finish processing remaining results.
    # The consumer needs time to apply all DB writes from the result queue.
    # We wait until the result queue is empty and give it a moment to finalize.
    _, result_queue = get_queues()
    while not stop_event.is_set():
        try:
            if result_queue.empty():
                # Give consumer one last chance to process any in-flight messages
                await asyncio.sleep(0.2)
                if result_queue.empty():
                    break
            await asyncio.sleep(0.1)
        except (ValueError, OSError):
            # Queue already closed
            break

    # Stop the consumer task
    stop_event.set()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # Stop the worker process
    stop_background_task()

    logger.info("Background compute task completed")


# Interval for price sync: 24 hours in seconds
PRICE_SYNC_INTERVAL = 24 * 60 * 60


async def start_price_sync_task() -> None:
    """
    Background task that periodically synchronizes model prices from OpenRouter.

    Runs until stop event is set:
    - Executes sync_model_prices() immediately on startup
    - Then waits 24 hours before the next sync
    - Handles graceful shutdown via stop event

    The sync operation runs in a thread to avoid blocking the event loop,
    as it involves HTTP requests to the OpenRouter API.
    """
    stop_event = get_price_sync_stop_event()

    logger.info("Price sync task started")

    while not stop_event.is_set():
        try:
            # Run price sync in a thread to avoid blocking the event loop
            stats = await asyncio.to_thread(sync_model_prices)
            logger.info(
                f"Price sync completed: {stats['created']} created, {stats['unchanged']} unchanged"
            )
        except Exception as e:
            logger.error(f"Price sync failed: {e}", exc_info=True)

        # Wait for the next sync interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PRICE_SYNC_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to sync again
            pass

    logger.info("Price sync task stopped")


# Interval for usage sync: 5 minutes in seconds
USAGE_SYNC_INTERVAL = 5 * 60


async def start_usage_sync_task() -> None:
    """
    Background task that periodically fetches and stores Claude usage quotas.

    Runs until stop event is set:
    - Executes fetch_and_save_usage() immediately on startup
    - Then waits USAGE_SYNC_INTERVAL before the next fetch
    - Handles graceful shutdown via stop event

    The fetch operation runs in a thread to avoid blocking the event loop,
    as it involves an HTTP request to the Anthropic API.
    """
    stop_event = get_usage_sync_stop_event()

    logger.info("Usage sync task started")

    while not stop_event.is_set():
        success = False
        try:
            snapshot = await asyncio.to_thread(fetch_and_save_usage)
            if snapshot:
                success = True
                logger.info(
                    "Usage sync completed: 5h=%.1f%% (time: %.1f%%), 7d=%.1f%% (time: %.1f%%)",
                    snapshot.five_hour_utilization or 0,
                    snapshot.five_hour_temporal_pct or 0,
                    snapshot.seven_day_utilization or 0,
                    snapshot.seven_day_temporal_pct or 0,
                )
            else:
                logger.warning("Usage sync: no data (credentials missing or API error)")
        except Exception as e:
            logger.error("Usage sync failed: %s", e, exc_info=True)

        # Broadcast to frontend (always sends latest snapshot from DB + success flag)
        try:
            await broadcast_usage_updated(success)
        except Exception as e:
            logger.error("Usage broadcast failed: %s", e, exc_info=True)

        # Wait for the next sync interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=USAGE_SYNC_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to sync again
            pass

    logger.info("Usage sync task stopped")
