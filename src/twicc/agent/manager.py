"""
Process manager for tracking and managing all active Claude processes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from django.conf import settings

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from .process import ClaudeProcess
from .states import ProcessInfo, ProcessState

logger = logging.getLogger(__name__)

# Global process manager instance (singleton pattern)
_process_manager: ProcessManager | None = None

# Type alias for the broadcast callback
BroadcastCallback = Callable[[ProcessInfo], Coroutine[Any, Any, None]]


def _get_session_slug_sync(session_id: str) -> str | None:
    """Retrieve the slug stored on the Session model (synchronous).

    Args:
        session_id: The session identifier to look up

    Returns:
        The slug string, or None if not set
    """
    from twicc.core.models import Session

    try:
        return Session.objects.values_list('slug', flat=True).get(id=session_id)
    except Session.DoesNotExist:
        return None


async def get_session_slug(session_id: str) -> str | None:
    """Async wrapper around _get_session_slug_sync.

    Runs the DB query in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_get_session_slug_sync, session_id)


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
        self._cron_expiry_monitor_task: asyncio.Task[None] | None = None
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
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = False,
        context_max: int = 200_000,
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
                    await process.apply_live_settings(permission_mode, selected_model)

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
                effort=effort, thinking_enabled=thinking_enabled,
                claude_in_chrome=claude_in_chrome, context_max=context_max,
                images=images, documents=documents
            )

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        permission_mode: str = "default",
        selected_model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = False,
        context_max: int = 200_000,
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
                effort=effort, thinking_enabled=thinking_enabled,
                claude_in_chrome=claude_in_chrome, context_max=context_max,
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
        effort: str | None = None,
        thinking_enabled: bool | None = None,
        claude_in_chrome: bool = False,
        context_max: int = 200_000,
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
        process = ClaudeProcess(
            session_id=session_id,
            project_id=project_id,
            cwd=cwd,
            permission_mode=permission_mode,
            selected_model=selected_model,
            effort=effort,
            thinking_enabled=thinking_enabled,
            get_session_slug=get_session_slug,
            on_cron_created=self._on_cron_created,
            on_cron_deleted=self._on_cron_deleted,
            claude_in_chrome=claude_in_chrome,
            context_max=context_max,
        )
        self._processes[session_id] = process

        # Update lifecycle timestamps: every process start (new or resume) is a session start
        from django.utils import timezone as dj_timezone
        from twicc.core.models import ProcessRun as ProcessRunModel, Session
        now = dj_timezone.now()

        # Create ProcessRun in DB and attach to process.
        # session_id is a plain CharField (not FK), so this works even for new sessions
        # that don't have a Session row yet.
        process_run = await asyncio.to_thread(
            lambda: ProcessRunModel.objects.create(
                session_id=session_id,
                started_at=now,
            )
        )
        process.process_run = process_run

        await asyncio.to_thread(
            lambda: Session.objects.filter(id=session_id).update(
                last_started_at=now, last_updated_at=now
            )
        )
        await self._broadcast_session_updated(session_id)

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

        if self._cron_expiry_monitor_task is not None:
            self._cron_expiry_monitor_task.cancel()
            try:
                await self._cron_expiry_monitor_task
            except asyncio.CancelledError:
                pass
            self._cron_expiry_monitor_task = None

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

        # Start cron expiry monitor alongside timeout monitor
        from django.conf import settings

        if not settings.CRON_AUTO_RESTART:
            logger.debug("Cron expiry monitor skipped (TWICC_NO_CRON_RESTART is set)")
        elif self._cron_expiry_monitor_task is None or self._cron_expiry_monitor_task.done():
            self._cron_expiry_monitor_task = asyncio.create_task(
                self._run_cron_expiry_monitor(),
                name="cron-expiry-monitor",
            )
            logger.debug("Started cron expiry monitor task")

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

    CRON_EXPIRY_MONITOR_INTERVAL = 60  # Check every 60 seconds

    async def _run_cron_expiry_monitor(self) -> None:
        """Background task that detects recurring crons auto-deleted by the CLI after 3 days.

        Runs every 60 seconds. For each active process, checks if any recurring crons
        have passed their computed expiry time (last_fire + jitter + margin).
        """
        logger.info("Cron expiry monitor started")

        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                await self._check_expired_crons()
            except Exception as e:
                logger.error("Error in cron expiry monitor: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.CRON_EXPIRY_MONITOR_INTERVAL
                )
            except asyncio.TimeoutError:
                pass

        logger.info("Cron expiry monitor stopped")

    async def _check_expired_crons(self) -> None:
        """Check all active processes for expired recurring crons.

        Only checks processes in USER_TURN state: this guarantees the last cron fire
        has completed (Claude is no longer working on the cron's prompt).

        When expired crons are found, they are deleted from the DB and a message is
        sent to Claude asking it to recreate them via CronCreate. The new crons will
        be persisted by the existing PostToolUse hooks.
        """
        from twicc.core.models import SessionCron
        from twicc.cron_restart import _build_renewal_message

        # Snapshot active processes (no lock needed — dict iteration is safe in asyncio)
        processes = list(self._processes.values())

        for process in processes:
            if process.state != ProcessState.USER_TURN:
                continue
            try:
                expired = await asyncio.to_thread(process.get_expired_recurring_crons)
                if not expired:
                    continue

                logger.info(
                    "Session %s has %d expired recurring cron(s): %s",
                    process.session_id,
                    len(expired),
                    ", ".join(c.cron_id for c in expired),
                )

                # Build renewal message from expired crons data before deleting them.
                # Includes cron_id so Claude can delete the old CLI crons first.
                crons_data = [
                    {
                        "cron_id": c.cron_id,
                        "cron_expr": c.cron_expr,
                        "recurring": c.recurring,
                        "prompt": c.prompt,
                    }
                    for c in expired
                ]
                message = _build_renewal_message(crons_data)

                # Delete expired crons from DB (they're already dead in the CLI)
                expired_ids = [c.pk for c in expired]
                await asyncio.to_thread(
                    lambda: SessionCron.objects.filter(pk__in=expired_ids).delete()
                )
                logger.info(
                    "Deleted %d expired cron(s) from DB for session %s",
                    len(expired_ids), process.session_id,
                )

                # Send message to Claude asking to recreate the crons
                try:
                    await process.send(message)
                    logger.info(
                        "Sent cron renewal message to session %s for %d cron(s)",
                        process.session_id, len(crons_data),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to send cron renewal message to session %s: %s",
                        process.session_id, e,
                    )

            except Exception as e:
                logger.error(
                    "Error checking expired crons for session %s: %s",
                    process.session_id, e,
                )

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

            # Don't timeout processes with active cron jobs — the CLI has
            # scheduled work pending that would be lost if we kill the process.
            try:
                from twicc.core.models import SessionCron
                has_crons = await asyncio.to_thread(
                    lambda sid=session_id: SessionCron.has_active_for_session(sid)
                )
                if has_crons:
                    continue
            except Exception as e:
                logger.error("Error checking active crons for session %s: %s", session_id, e)

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

    async def _on_cron_created(
        self,
        session_id: str,
        cron_id: str,
        cron_expr: str,
        recurring: bool,
        prompt: str,
        created_at: "datetime",
        next_fire: "datetime",
    ) -> None:
        """Persist a newly created cron job to the database and broadcast the update."""
        from twicc.core.models import SessionCron

        # Associate cron with the current process run (if any)
        process = self._processes.get(session_id)
        process_run = process.process_run if process else None

        await asyncio.to_thread(
            lambda: SessionCron.objects.create(
                cron_id=cron_id,
                session_id=session_id,
                process_run=process_run,
                cron_expr=cron_expr,
                recurring=recurring,
                prompt=prompt,
                created_at=created_at,
                next_fire=next_fire,
            )
        )
        logger.info(
            "Persisted cron %s for session %s (process run: %s)",
            cron_id, session_id, process_run.pk if process_run else None,
        )
        await self._broadcast_process_state(session_id)

    async def _on_cron_deleted(self, session_id: str, cron_id: str) -> None:
        """Delete a cron job from the database and broadcast the update."""
        from twicc.core.models import SessionCron

        deleted, _ = await asyncio.to_thread(
            lambda: SessionCron.objects.filter(cron_id=cron_id).delete()
        )
        if deleted:
            logger.info("Deleted cron %s for session %s", cron_id, session_id)
        else:
            logger.debug("Cron %s not found in DB for session %s (may already be deleted)", cron_id, session_id)
        await self._broadcast_process_state(session_id)

    async def _broadcast_process_state(self, session_id: str) -> None:
        """Trigger a process state broadcast for a session (used after cron changes)."""
        process = self._processes.get(session_id)
        if process and self._broadcast_callback:
            try:
                await self._broadcast_callback(process.get_info())
            except Exception as e:
                logger.error("Error broadcasting process state for session %s: %s", session_id, e)

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

        # First USER_TURN: purge old ProcessRuns for this session (cascade deletes their crons).
        # Done BEFORE broadcasting so that _enrich_with_active_crons sees the correct cron count
        # (without this, crons from the old run + new run would both appear in the broadcast).
        # Uses _old_runs_purged flag because _first_user_turn_reached is already True at this point
        # (set in _run_message_loop before _notify_state_change is called).
        # Systematic for all processes — no-op if no old process runs exist (DELETE affects 0 rows).
        if (
            process.state == ProcessState.USER_TURN
            and not process._old_runs_purged
            and process.process_run is not None
        ):
            process._old_runs_purged = True
            current_run_id = process.process_run.pk
            try:
                from twicc.core.models import ProcessRun as ProcessRunModel
                deleted_count, _ = await asyncio.to_thread(
                    lambda: ProcessRunModel.objects.filter(
                        session_id=process.session_id
                    ).exclude(pk=current_run_id).delete()
                )
                if deleted_count:
                    logger.info(
                        "Purged %d old process run(s) for session %s (current: %s)",
                        deleted_count, process.session_id, current_run_id,
                    )
            except Exception as e:
                logger.error("Error purging old process runs for session %s: %s", process.session_id, e)

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

        # Update last_stopped_at when process dies, and propagate to recent subagents
        if process.state == ProcessState.DEAD:
            try:
                from django.utils import timezone as dj_timezone
                from twicc.core.models import Session
                now = dj_timezone.now()

                # Get the previous cutoff BEFORE updating, to find subagents started in this run
                session = await asyncio.to_thread(Session.objects.filter(id=process.session_id).first)
                previous_cutoff = session.cutoff if session else None

                await asyncio.to_thread(
                    lambda: Session.objects.filter(id=process.session_id).update(
                        last_stopped_at=now, last_updated_at=now
                    )
                )
                await self._broadcast_session_updated(process.session_id)

                # Propagate last_stopped_at to subagents started after the previous cutoff
                if session is not None:
                    subagent_filter = Session.objects.filter(parent_session_id=process.session_id)
                    if previous_cutoff is not None:
                        subagent_filter = subagent_filter.filter(last_started_at__gte=previous_cutoff)
                    subagent_ids = await asyncio.to_thread(
                        lambda: list(subagent_filter.values_list('id', flat=True))
                    )
                    if subagent_ids:
                        await asyncio.to_thread(
                            lambda: Session.objects.filter(id__in=subagent_ids).update(
                                last_stopped_at=now, last_updated_at=now
                            )
                        )
                        for subagent_id in subagent_ids:
                            await self._broadcast_session_updated(subagent_id)

            except Exception as e:
                logger.error("Error updating last_stopped_at for session %s: %s", process.session_id, e)

            # ProcessRun lifecycle cleanup on process death
            if process.process_run is not None:
                should_delete_run = False

                if process.kill_reason == "manual":
                    # User explicitly stopped → delete process run (cascade deletes crons)
                    should_delete_run = True
                elif not process._first_user_turn_reached:
                    # Died before first USER_TURN (failed cron restart, early crash, etc.)
                    # Delete current process run to discard partial crons, keep old runs for retry
                    should_delete_run = True
                else:
                    # Died after USER_TURN (old runs already purged). Keep only if it has crons.
                    has_crons = await asyncio.to_thread(lambda: process.process_run.crons.exists())
                    if not has_crons:
                        should_delete_run = True

                if should_delete_run:
                    try:
                        run_pk = process.process_run.pk
                        await asyncio.to_thread(lambda: process.process_run.delete())
                        logger.info(
                            "Deleted process run %s for session %s (kill_reason=%s, user_turn_reached=%s)",
                            run_pk, process.session_id, process.kill_reason, process._first_user_turn_reached,
                        )
                    except Exception as e:
                        logger.error("Error deleting process run for session %s: %s", process.session_id, e)

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

    async def _broadcast_session_updated(self, session_id: str) -> None:
        """Broadcast a session_updated message via WebSocket after lifecycle timestamp changes."""
        from channels.layers import get_channel_layer
        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        try:
            session = await asyncio.to_thread(Session.objects.get, id=session_id)
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
        except Exception as e:
            logger.error("Error broadcasting session_updated for %s: %s", session_id, e)


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
