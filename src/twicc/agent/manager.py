"""
Process manager for tracking and managing all active Claude processes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from .process import ClaudeProcess
from .states import ProcessInfo, ProcessState

logger = logging.getLogger(__name__)

# Global process manager instance (singleton pattern)
_process_manager: ProcessManager | None = None

# Type alias for the broadcast callback
BroadcastCallback = Callable[[ProcessInfo], Coroutine[Any, Any, None]]


class ProcessManager:
    """Manages all active Claude processes.

    This singleton-like class tracks active processes and provides methods to
    send messages (with auto-resume), list processes, and shut down gracefully.

    The manager does not write to the database - it only tracks in-memory state.
    Process content is handled by the JSONL watcher which reads files written
    by Claude.

    Typical usage:
        manager = ProcessManager()

        # Resume an existing session with a new message
        await manager.resume_session(session_id, project_id, cwd, "Hello")

        # Create a new session
        await manager.new_session(session_id, project_id, cwd, "Hello")

        # Get active processes
        processes = manager.get_active_processes()

        # Shutdown all processes
        await manager.shutdown()
    """

    def __init__(self) -> None:
        """Initialize the process manager with empty state."""
        self._processes: dict[str, ClaudeProcess] = {}  # session_id -> process
        self._lock = asyncio.Lock()
        self._broadcast_callback: BroadcastCallback | None = None

    def set_broadcast_callback(self, callback: BroadcastCallback) -> None:
        """Set the callback for broadcasting state changes.

        This callback is invoked whenever a process state changes, allowing
        the caller to broadcast updates via WebSocket.

        Args:
            callback: Async function that receives ProcessInfo and broadcasts it
        """
        self._broadcast_callback = callback

    async def resume_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
    ) -> None:
        """Resume an existing session with a new message.

        If no active process exists for this session, a new one is created
        with the resume option to continue the existing conversation.

        Args:
            session_id: The Claude session identifier (must exist in database)
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The message text to send

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
                elif process.state == ProcessState.USER_TURN:
                    # Process ready for input, send message
                    await process.send(text)
                    return
                else:
                    # Process busy (starting or assistant turn)
                    raise RuntimeError(
                        f"Cannot send message: process is in state {process.state}"
                    )

            # Create and start new process with resume
            await self._start_process(session_id, project_id, cwd, text, resume=True)

    async def new_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
    ) -> None:
        """Create a new session with a client-provided session ID.

        Unlike send_message which auto-resumes existing sessions, this creates
        a brand new session. The session_id is passed to the Claude CLI via
        the --session-id flag.

        Args:
            session_id: The client-provided session UUID
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The initial message text

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
            await self._start_process(session_id, project_id, cwd, text, resume=False)

    async def _start_process(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
    ) -> None:
        """Create and start a new Claude process.

        This is the common implementation for both resume_session (resume=True)
        and new_session (resume=False).

        Must be called while holding self._lock.

        Args:
            session_id: The Claude session identifier
            project_id: The TwiCC project identifier
            cwd: Working directory for Claude operations
            text: The message text to send
            resume: If True, resume existing session. If False, create new session.
        """
        logger.debug(
            "Creating process for session %s, project %s (resume=%s)",
            session_id,
            project_id,
            resume,
        )
        process = ClaudeProcess(session_id, project_id, cwd)
        self._processes[session_id] = process

        # Broadcast the starting state before starting
        await self._on_state_change(process)

        # Start the process. If it fails, it transitions to DEAD state and
        # broadcasts the error via the callback - it does not raise exceptions.
        await process.start(text, self._on_state_change, resume=resume)

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

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown all active processes.

        This is called during server shutdown. It attempts graceful termination
        but does not wait indefinitely for Claude to finish responding.

        Args:
            timeout: Maximum seconds to wait for processes to terminate
        """
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
