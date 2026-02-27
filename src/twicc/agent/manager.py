"""
Process manager for tracking and managing all active Claude processes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
from django.conf import settings

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from .process import ClaudeProcess
from .states import ProcessInfo, ProcessState

logger = logging.getLogger(__name__)

# Global process manager instance (singleton pattern)
_process_manager: ProcessManager | None = None

# Type alias for the broadcast callback
BroadcastCallback = Callable[[ProcessInfo], Coroutine[Any, Any, None]]


def _get_last_session_slug_sync(session_id: str) -> str | None:
    """Retrieve the most recent slug from a session's JSONL items (synchronous).

    Iterates backwards through the session items (by descending line_num),
    parsing each content JSON to find a root-level "slug" key. Returns
    the first slug found, or None if no item has one.

    This is a sync function meant to be called via asyncio.to_thread().

    Args:
        session_id: The session identifier to look up

    Returns:
        The slug string, or None if not found
    """
    from twicc.core.models import SessionItem

    items = (
        SessionItem.objects
        .filter(session_id=session_id)
        .order_by("-line_num")
        .only("content")
        .iterator(chunk_size=1)
    )
    for item in items:
        try:
            data = orjson.loads(item.content)
        except (orjson.JSONDecodeError, TypeError):
            continue
        slug = data.get("slug")
        if slug:
            return slug
    return None


async def get_last_session_slug(session_id: str) -> str | None:
    """Async wrapper around _get_last_session_slug_sync.

    Runs the DB query in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_get_last_session_slug_sync, session_id)


class ProcessManager:
    """Manages all active Claude processes.

    This singleton-like class tracks active processes and provides methods to
    send messages (with auto-resume), list processes, and shut down gracefully.

    The manager does not write to the database - it only tracks in-memory state.
    Process content is handled by the JSONL watcher which reads files written
    by Claude.

    Typical usage:
        manager = ProcessManager()

        # Send a message to an existing session
        await manager.send_to_session(session_id, project_id, cwd, "Hello")

        # Create a new session
        await manager.create_session(session_id, project_id, cwd, "Hello")

        # Get active processes
        processes = manager.get_active_processes()

        # Shutdown all processes
        await manager.shutdown()
    """

    # Interval for timeout monitoring: 30 seconds
    # Frequent enough to catch the 1-minute STARTING timeout accurately
    TIMEOUT_MONITOR_INTERVAL = 30

    def __init__(self) -> None:
        """Initialize the process manager with empty state."""
        self._processes: dict[str, ClaudeProcess] = {}  # session_id -> process
        self._lock = asyncio.Lock()
        self._broadcast_callback: BroadcastCallback | None = None
        self._timeout_monitor_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def set_broadcast_callback(self, callback: BroadcastCallback) -> None:
        """Set the callback for broadcasting state changes.

        This callback is invoked whenever a process state changes, allowing
        the caller to broadcast updates via WebSocket.

        Args:
            callback: Async function that receives ProcessInfo and broadcasts it
        """
        self._broadcast_callback = callback

    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Send a message to an existing session, optionally updating model/permission settings.

        If no active process exists for this session, a new one is created
        with the resume option to continue the existing conversation.

        Messages can be sent during USER_TURN (normal) or ASSISTANT_TURN
        (Claude Agent SDK queues them and processes after current response).

        When a live process exists, model and permission mode are updated on the
        SDK client before sending the message. If text is empty and there are no
        attachments, only the settings are applied (no query is sent to Claude).

        Args:
            session_id: The Claude session identifier (must exist in database)
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The message text to send (may be empty for settings-only updates)
            permission_mode: Permission mode to apply
            selected_model: Model shorthand to apply, or None for default
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Raises:
            RuntimeError: If the process cannot be started or message cannot be sent
        """
        async with self._lock:
            if session_id in self._processes:
                process = self._processes[session_id]

                if process.state == ProcessState.DEAD:
                    # Dead process, clean it up and create new one
                    logger.debug(
                        "Removing dead process for session %s",
                        session_id,
                    )
                    del self._processes[session_id]
                elif process.state in (ProcessState.USER_TURN, ProcessState.ASSISTANT_TURN):
                    # Process ready for input or busy responding.
                    # Apply settings changes on the live SDK client before sending.
                    # Permission mode works in any state; model only in USER_TURN.
                    await self._apply_live_settings(process, permission_mode, selected_model)

                    # If there is actual content to send, forward it to Claude
                    has_content = bool(text) or bool(images) or bool(documents)
                    if has_content:
                        await process.send(text, images=images, documents=documents)

                    return
                else:
                    # Process starting - cannot send yet
                    raise RuntimeError(
                        f"Cannot send message: process is in state {process.state}"
                    )

            # No live process — text is required to start a new one
            if not text:
                raise RuntimeError(
                    "Cannot start a new process without a message"
                )

            # Create and start new process with resume
            await self._start_process(
                session_id, project_id, cwd, text, resume=True,
                permission_mode=permission_mode, selected_model=selected_model,
                images=images, documents=documents
            )

    @staticmethod
    async def _apply_live_settings(
        process: ClaudeProcess,
        permission_mode: str,
        selected_model: str | None,
    ) -> None:
        """Apply permission mode and model changes to a live process.

        Compares the requested values with the process's current values and
        calls the SDK methods only when they differ.

        Permission mode can be changed in any active state (USER_TURN or ASSISTANT_TURN).
        Model can only be changed during USER_TURN (the SDK's set_model() has no effect
        during ASSISTANT_TURN).

        Args:
            process: The live ClaudeProcess instance
            permission_mode: Desired permission mode
            selected_model: Desired model shorthand, or None
        """
        if permission_mode != process.permission_mode:
            await process.set_permission_mode(permission_mode)

        if selected_model != process.selected_model and process.state == ProcessState.USER_TURN:
            await process.set_model(selected_model)

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create a new session with a client-provided session ID.

        Unlike send_to_session which handles existing sessions, this creates
        a brand new session. The session_id is passed to the Claude CLI via
        the --session-id flag.

        Args:
            session_id: The client-provided session UUID
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The initial message text
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Raises:
            RuntimeError: If a process already exists for this session_id
        """
        async with self._lock:
            if session_id in self._processes:
                process = self._processes[session_id]
                if process.state != ProcessState.DEAD:
                    raise RuntimeError(
                        f"Session {session_id} already exists and is active"
                    )
                # Dead process, clean it up
                logger.debug(
                    "Removing dead process for session %s before creating new",
                    session_id,
                )
                del self._processes[session_id]

            # Create and start new process without resume
            await self._start_process(
                session_id, project_id, cwd, text, resume=False,
                permission_mode=permission_mode, selected_model=selected_model,
                images=images, documents=documents
            )

    async def _start_process(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        permission_mode: str = "default",
        selected_model: str | None = None,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Create and start a new Claude process.

        This is the common implementation for both send_to_session (resume=True)
        and create_session (resume=False).

        Must be called while holding self._lock.

        Args:
            session_id: The Claude session identifier
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The message text to send
            resume: If True, resume existing session. If False, create new session.
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects
        """
        logger.debug(
            "Creating process for session %s, project %s (resume=%s)",
            session_id,
            project_id,
            resume,
        )
        process = ClaudeProcess(session_id, project_id, cwd, permission_mode, selected_model, get_last_session_slug=get_last_session_slug)
        self._processes[session_id] = process

        # Broadcast the starting state before starting
        await self._on_state_change(process)

        # Start the process. If it fails, it transitions to DEAD state and
        # broadcasts the error via the callback - it does not raise exceptions.
        await process.start(
            text, self._on_state_change, resume=resume,
            images=images, documents=documents
        )

        # Ensure timeout monitor is running (starts lazily on first process)
        self._ensure_timeout_monitor_running()

    def get_active_processes(self) -> list[ProcessInfo]:
        """Get information about all active (non-dead) processes.

        Returns:
            List of ProcessInfo snapshots for active processes
        """
        return [
            process.get_info()
            for process in self._processes.values()
            if process.state != ProcessState.DEAD
        ]

    def get_process_info(self, session_id: str) -> ProcessInfo | None:
        """Get information about a specific process.

        Args:
            session_id: The session identifier to look up

        Returns:
            ProcessInfo if found, None otherwise
        """
        process = self._processes.get(session_id)
        if process is None:
            return None
        return process.get_info()

    async def kill_process(self, session_id: str, reason: str = "manual") -> bool:
        """Kill a specific process by session ID.

        This terminates the process gracefully. Processes in any state except DEAD
        can be killed - this includes USER_TURN since the process still consumes
        memory even when idle.

        Args:
            session_id: The session identifier of the process to kill
            reason: Reason for killing (e.g., "manual", "timeout")

        Returns:
            True if a process was killed, False if not found or already dead
        """
        async with self._lock:
            process = self._processes.get(session_id)
            if process is None:
                logger.debug("kill_process: session %s not found", session_id)
                return False

            # Already dead, nothing to do
            if process.state == ProcessState.DEAD:
                logger.debug(
                    "kill_process: session %s already dead",
                    session_id,
                )
                return False

            logger.info(
                "Killing process for session %s (reason: %s)", session_id, reason
            )
            await process.kill(reason=reason)
            return True

    async def resolve_pending_request(
        self, session_id: str, response: PermissionResultAllow | PermissionResultDeny
    ) -> bool:
        """Resolve a pending request on a process.

        Routes the user's response to the correct process based on session_id.
        Called by the WebSocket handler when a pending_request_response message
        arrives from the frontend.

        Args:
            session_id: The session to resolve
            response: The permission result to send to the SDK

        Returns:
            True if resolved, False if no matching process or no pending request
        """
        process = self._processes.get(session_id)
        if process is None:
            return False
        return process.resolve_pending_request(response)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown all active processes and the timeout monitor.

        This is called during server shutdown. It attempts graceful termination
        but does not wait indefinitely for Claude to finish responding.

        Args:
            timeout: Maximum seconds to wait for processes to terminate
        """
        # Stop the timeout monitor task first
        if self._stop_event is not None:
            self._stop_event.set()

        if self._timeout_monitor_task is not None:
            self._timeout_monitor_task.cancel()
            try:
                await self._timeout_monitor_task
            except asyncio.CancelledError:
                pass
            self._timeout_monitor_task = None

        async with self._lock:
            if not self._processes:
                return

            logger.info(
                "Shutting down %d active Claude processes", len(self._processes)
            )

            # Create kill tasks for all processes
            kill_tasks = [
                asyncio.create_task(
                    process.kill(reason="shutdown"), name=f"kill-{session_id}"
                )
                for session_id, process in self._processes.items()
            ]

            if kill_tasks:
                # Wait for all kills with timeout
                done, pending = await asyncio.wait(
                    kill_tasks,
                    timeout=timeout,
                    return_when=asyncio.ALL_COMPLETED,
                )

                # Cancel any that didn't finish in time
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if pending:
                    logger.warning(
                        "%d processes did not terminate within %ss timeout",
                        len(pending),
                        timeout,
                    )

            # Clear all processes
            self._processes.clear()
            logger.info("All Claude processes shut down")

    def _ensure_timeout_monitor_running(self) -> None:
        """Start the timeout monitor task if not already running.

        Called when a process is started to ensure monitoring is active.
        The task runs until shutdown() is called.
        """
        if self._timeout_monitor_task is not None and not self._timeout_monitor_task.done():
            return  # Already running

        self._stop_event = asyncio.Event()
        self._timeout_monitor_task = asyncio.create_task(
            self._run_timeout_monitor(),
            name="process-timeout-monitor",
        )
        logger.debug("Started process timeout monitor task")

    async def _run_timeout_monitor(self) -> None:
        """Background task that monitors process timeouts and auto-stops idle processes.

        Runs until stop event is set. Checks all active processes every 30 seconds
        and kills those that exceed their state-specific timeout:
        - STARTING: 1 minute (stuck during startup)
        - USER_TURN: 15 minutes (idle, waiting for user)
        - ASSISTANT_TURN: 2 hours inactivity or 6 hours absolute
        """
        logger.info("Process timeout monitor started")

        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                killed = await self.check_and_stop_timed_out_processes()

                if killed:
                    logger.info(
                        "Auto-stopped %d timed out process(es): %s",
                        len(killed),
                        ", ".join(killed),
                    )

            except Exception as e:
                logger.error("Error in process timeout monitor: %s", e, exc_info=True)

            # Wait for the next check interval (or until stop event is set)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.TIMEOUT_MONITOR_INTERVAL
                )
            except asyncio.TimeoutError:
                # Timeout means it's time to check again
                pass

        logger.info("Process timeout monitor stopped")

    def touch_process_activity(self, session_id: str) -> bool:
        """Update last_activity timestamp for a process.

        Called when the user is actively preparing a message (typing, adding images, etc.)
        to prevent the process from being auto-stopped due to inactivity timeout.

        Works for processes in USER_TURN or ASSISTANT_TURN state.

        Args:
            session_id: The session identifier

        Returns:
            True if the process was found and updated, False otherwise
        """
        process = self._processes.get(session_id)
        if process is None:
            return False

        if process.state not in (ProcessState.USER_TURN, ProcessState.ASSISTANT_TURN):
            return False

        process.last_activity = time.time()
        logger.debug(
            "Touched last_activity for session %s (state=%s)",
            session_id,
            process.state.value,
        )
        return True

    async def check_and_stop_timed_out_processes(self) -> list[str]:
        """Check all active processes and kill those that exceeded their timeout.

        Timeout rules based on process state:
        - STARTING: Uses state_changed_at (stuck during startup)
        - USER_TURN: Uses last_activity (idle, waiting for user)
        - ASSISTANT_TURN: Uses last_activity (no activity from Claude)

        Returns:
            List of session_ids that were killed due to timeout
        """
        current_time = time.time()
        killed: list[str] = []

        # Take a snapshot to avoid modification during iteration
        processes_snapshot = list(self._processes.items())

        for session_id, process in processes_snapshot:
            # Don't timeout processes waiting for user input
            if process.pending_request is not None:
                continue

            timeout: int | None = None
            reason: str | None = None
            reference_time: float | None = None

            if process.state == ProcessState.STARTING:
                # For STARTING, use state_changed_at (when it entered STARTING)
                timeout = getattr(settings, "PROCESS_TIMEOUT_STARTING", 60)
                reason = "timeout_starting"
                reference_time = process.state_changed_at
            elif process.state == ProcessState.USER_TURN:
                # For USER_TURN, use last_activity (effectively same as state_changed_at)
                timeout = getattr(settings, "PROCESS_TIMEOUT_USER_TURN", 15 * 60)
                reason = "timeout_user_turn"
                reference_time = process.last_activity
            elif process.state == ProcessState.ASSISTANT_TURN:
                # For ASSISTANT_TURN, check both inactivity and absolute duration
                inactivity_timeout = getattr(
                    settings, "PROCESS_TIMEOUT_ASSISTANT_TURN", 2 * 60 * 60
                )
                absolute_timeout = getattr(
                    settings, "PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE", 6 * 60 * 60
                )

                inactivity_elapsed = current_time - process.last_activity
                absolute_elapsed = current_time - process.state_changed_at

                # Check absolute timeout first (takes precedence for the reason)
                if absolute_elapsed > absolute_timeout:
                    timeout = absolute_timeout
                    reason = "timeout_assistant_turn_absolute"
                    reference_time = process.state_changed_at
                elif inactivity_elapsed > inactivity_timeout:
                    timeout = inactivity_timeout
                    reason = "timeout_assistant_turn"
                    reference_time = process.last_activity
                else:
                    continue
            else:
                # DEAD - nothing to do
                continue

            elapsed = current_time - reference_time

            if elapsed > timeout:
                logger.info(
                    "Auto-stopping process %s: state=%s, elapsed=%.1fs, timeout=%ds",
                    session_id,
                    process.state.value,
                    elapsed,
                    timeout,
                )
                # kill_process acquires the lock, so we call it outside snapshot iteration
                if await self.kill_process(session_id, reason=reason):
                    killed.append(session_id)

        return killed

    async def _on_state_change(self, process: ClaudeProcess) -> None:
        """Handle process state change by cleaning up dead processes and broadcasting.

        Concurrency model:
        This callback is invoked in two contexts:
        1. Synchronously from send_message() when start()/send() fails - the lock is
           already held by send_message(), so we must not try to acquire it again.
        2. Asynchronously from the message loop background task when Claude finishes
           or an error occurs - the lock is NOT held.

        For dead process cleanup, we do NOT acquire the lock because:
        - In context 1, we'd deadlock (the lock is already held)
        - In context 2, the cleanup is effectively atomic in asyncio (no await between
          the check and delete operations, so no preemption)
        - The identity check (is process) ensures we only delete the exact instance

        Args:
            process: The process that changed state
        """
        info = process.get_info()

        logger.debug(
            "State change callback triggered for session %s: state=%s",
            info.session_id,
            info.state.value,
        )

        # Broadcast state change if callback is set
        if self._broadcast_callback is not None:
            try:
                await self._broadcast_callback(info)
            except Exception as e:
                logger.error("Error broadcasting state change: %s", e)

        # Flush pending title when process becomes safe to write.
        # We add a small delay to let Claude CLI finish flushing its own I/O
        # buffers to the JSONL file — the ResultMessage arrives via the SDK stream
        # before Claude CLI has necessarily finished writing to disk.
        if process.state in (ProcessState.USER_TURN, ProcessState.DEAD):
            from twicc.titles import flush_pending_title

            try:
                await asyncio.sleep(0.5)
                await asyncio.to_thread(flush_pending_title, process.session_id)
            except Exception as e:
                logger.error("Error flushing pending title: %s", e)

        # Clean up dead processes. No lock needed - see docstring for concurrency model.
        if process.state == ProcessState.DEAD:
            if (
                process.session_id in self._processes
                and self._processes[process.session_id] is process
            ):
                logger.debug(
                    "Cleaning up dead process from _processes for session %s",
                    process.session_id,
                )
                del self._processes[process.session_id]


def get_process_manager() -> ProcessManager:
    """Get the global ProcessManager instance.

    Creates the instance lazily on first access. The same instance is shared
    across the application (singleton pattern).

    Returns:
        The global ProcessManager instance
    """
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager()
    return _process_manager


async def shutdown_process_manager() -> None:
    """Shutdown the global ProcessManager if it exists.

    This should be called during server shutdown to gracefully terminate
    all active Claude processes. Safe to call even if the manager was never
    initialized.
    """
    global _process_manager
    if _process_manager is not None:
        await _process_manager.shutdown()
        _process_manager = None
