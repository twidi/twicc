"""
Claude process wrapper for a single SDK client instance.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
)

from .states import ProcessInfo, ProcessState, get_process_memory

logger = logging.getLogger(__name__)

# Type alias for the state change callback
StateChangeCallback = Callable[["ClaudeProcess"], Coroutine[Any, Any, None]]


class ClaudeProcess:
    """Wraps a single Claude SDK client for one session.

    This class manages the lifecycle of a Claude process, handling connection,
    message sending, and state tracking. It is designed to be completely isolated
    so that any errors in the Claude process never propagate to crash the server.

    Attributes:
        session_id: The Claude session identifier
        project_id: The TwiCC project identifier
        state: Current process state
        last_activity: Unix timestamp of last activity
        error: Error message if the process died due to error
    """

    def __init__(self, session_id: str, project_id: str, cwd: str) -> None:
        """Initialize a Claude process wrapper.

        Args:
            session_id: The session to resume
            project_id: The TwiCC project this belongs to
            cwd: Working directory for Claude operations
        """
        self.session_id = session_id
        self.project_id = project_id
        self.cwd = cwd
        self.state = ProcessState.STARTING
        self.started_at = time.time()
        self.state_changed_at = self.started_at
        self.last_activity = self.started_at
        self.error: str | None = None
        self.kill_reason: str | None = None

        self._client: ClaudeSDKClient | None = None
        self._message_loop_task: asyncio.Task[None] | None = None
        self._state_change_callback: StateChangeCallback | None = None

        logger.debug(
            "ClaudeProcess created for session %s, project %s, cwd=%s",
            session_id,
            project_id,
            cwd,
        )

    def _log_stderr(self, line: str) -> None:
        """Log stderr output from the Claude CLI subprocess.

        This callback is passed to the SDK to capture stderr lines.
        """
        logger.warning(
            "Claude stderr for session %s: %s",
            self.session_id,
            line.rstrip(),
        )

    def _set_state(self, new_state: ProcessState) -> None:
        """Update state with DEBUG logging."""
        old_state = self.state
        self.state = new_state
        self.state_changed_at = time.time()
        logger.debug(
            "State transition for session %s: %s -> %s",
            self.session_id,
            old_state.value,
            new_state.value,
        )

    def get_pid(self) -> int | None:
        """Get the PID of the underlying Claude CLI subprocess.

        This accesses internal SDK attributes to extract the subprocess PID.
        May return None if the process is not started or has terminated.

        Returns:
            Process ID of the Claude CLI subprocess, or None if unavailable
        """
        try:
            if self._client is None:
                return None
            # Access internal SDK attributes: ClaudeSDKClient._transport._process.pid
            transport = getattr(self._client, "_transport", None)
            if transport is None:
                return None
            process = getattr(transport, "_process", None)
            if process is None:
                return None
            return getattr(process, "pid", None)
        except Exception:
            return None

    def get_memory_rss(self) -> int | None:
        """Get RSS memory usage of the Claude CLI subprocess.

        Returns:
            Memory usage in bytes, or None if unavailable
        """
        try:
            pid = self.get_pid()
            if pid is None:
                return None
            return get_process_memory(pid)
        except Exception:
            return None

    def get_info(self) -> ProcessInfo:
        """Get an immutable snapshot of the process state."""
        # Don't query memory for dead processes - the subprocess no longer exists
        memory_rss = None if self.state == ProcessState.DEAD else self.get_memory_rss()
        return ProcessInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            state=self.state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
        )

    async def start(
        self, prompt: str, on_state_change: StateChangeCallback, resume: bool = True
    ) -> None:
        """Start the process and send the first message.

        This connects to Claude and sends the initial prompt. A background task
        is started to consume messages and track state changes.

        Args:
            prompt: The initial message to send
            on_state_change: Async callback invoked when process state changes
            resume: If True (default), resume an existing session. If False,
                   create a new session with the session_id as the custom UUID.

        Raises:
            RuntimeError: If the process is already started
        """
        if self._client is not None:
            raise RuntimeError("Process already started")

        self._state_change_callback = on_state_change

        logger.debug(
            "Starting process for session %s (resume=%s)", self.session_id, resume
        )

        try:
            # Create options - either resume existing session or create new with custom ID
            options = ClaudeAgentOptions(
                cwd=self.cwd,
                permission_mode="bypassPermissions",
                stderr=self._log_stderr,
                extra_args={
                    "chrome": None
                },
            )

            if resume:
                # Resume existing session
                options.resume = self.session_id
            else:
                # New session with custom session ID
                options.extra_args["session-id"] = self.session_id

            self._client = ClaudeSDKClient(options=options)

            # Connect without prompt to enter streaming mode, then send the message
            # via query(). The SDK's connect(prompt) with a string puts the transport
            # in "print mode" which closes stdin immediately, but ClaudeSDKClient
            # always uses streaming mode for the control protocol. We must connect()
            # first, then query() to send messages.
            await self._client.connect()
            await self._client.query(prompt)

            logger.debug(
                "Connection established for session %s",
                self.session_id,
            )

            # Transition to assistant turn
            self._set_state(ProcessState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()

            # Start background message loop
            self._message_loop_task = asyncio.create_task(
                self._run_message_loop(),
                name=f"claude-process-{self.session_id}",
            )

            logger.debug(
                "Message loop task created for session %s",
                self.session_id,
            )

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            await self._handle_error(f"Failed to start process: {e}")

    async def send(self, text: str) -> None:
        """Send a follow-up message to the process.

        Args:
            text: The message text to send

        Raises:
            RuntimeError: If the process is not running or not ready for input
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        if self.state not in (ProcessState.USER_TURN, ProcessState.ASSISTANT_TURN):
            raise RuntimeError(f"Cannot send message in state {self.state}")

        logger.debug("Sending message to session %s", self.session_id)

        try:
            # Only transition and notify if we're not already in ASSISTANT_TURN
            # (if this case, Claude will queue the message, we stay in ASSISTANT_TURN)
            if self.state != ProcessState.ASSISTANT_TURN:
                self._set_state(ProcessState.ASSISTANT_TURN)
                self.last_activity = time.time()
                await self._notify_state_change()

            await self._client.query(text)

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            await self._handle_error(f"Failed to send message: {e}")

    async def kill(self, reason: str = "manual") -> None:
        """Terminate the process gracefully.

        This cancels the message loop and kills the underlying Claude CLI process.
        Safe to call multiple times or on an already dead process.

        Args:
            reason: Reason for killing the process (e.g., "manual", "shutdown")
        """
        logger.debug(
            "Kill requested for session %s (reason: %s)", self.session_id, reason
        )

        if self.state == ProcessState.DEAD:
            logger.debug("Session %s already dead, skipping kill", self.session_id)
            return

        self.kill_reason = reason

        # Get PID BEFORE dropping the client reference
        pid = self.get_pid()

        # Cancel message loop first
        if self._message_loop_task is not None:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        # Don't call disconnect() - the SDK's anyio cancel scopes leak
        # cancellation to other asyncio tasks. Just drop the reference.
        self._client = None

        # Kill the system process directly (isolated from anyio context)
        if pid is not None:
            await self._kill_system_process(pid)

        # Update state
        self._set_state(ProcessState.DEAD)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def _run_message_loop(self) -> None:
        """Background task that consumes messages and tracks state.

        This loop processes messages from Claude. We don't use the message content
        (the JSONL watcher handles that), but we need to consume them to detect
        when Claude is done responding (ResultMessage).
        """
        if self._client is None:
            return

        logger.debug("Entering message loop for session %s", self.session_id)

        try:
            async for msg in self._client.receive_messages():
                self.last_activity = time.time()
                msg_type = type(msg).__name__
                logger.debug(
                    "Received message for session %s: %s",
                    self.session_id,
                    msg_type,
                )

                if isinstance(msg, ResultMessage):
                    # Claude finished responding, ready for user input
                    if msg.is_error:
                        # Log full ResultMessage details for debugging
                        logger.error(
                            "Claude error for session %s: result=%r, subtype=%s, "
                            "num_turns=%s, duration_ms=%s",
                            self.session_id,
                            msg.result,
                            msg.subtype,
                            msg.num_turns,
                            msg.duration_ms,
                        )
                        await self._handle_error(
                            f"Claude reported error: {msg.result or 'Unknown error'}"
                        )
                        return

                    logger.debug(
                        "ResultMessage received for session %s",
                        self.session_id,
                    )
                    self._set_state(ProcessState.USER_TURN)
                    await self._notify_state_change()

        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            raise
        except ClaudeSDKError as e:
            await self._handle_error(f"SDK error: {e}")
        except Exception as e:
            await self._handle_error(f"Unexpected error in message loop: {e}")

    async def _handle_error(self, error_message: str) -> None:
        """Handle an error by transitioning to DEAD state.

        Args:
            error_message: Description of what went wrong
        """
        logger.error(
            "Process %s for session %s died: %s",
            self.project_id,
            self.session_id,
            error_message,
        )

        # Get PID BEFORE dropping the client reference
        pid = self.get_pid()

        self._set_state(ProcessState.DEAD)
        self.error = error_message
        self.kill_reason = "error"
        self.last_activity = time.time()

        await self._notify_state_change()

        # Don't call disconnect() - the SDK's anyio cancel scopes leak
        # cancellation to other asyncio tasks. Just drop the reference.
        self._client = None

        # Kill the system process directly (isolated from anyio context)
        if pid is not None:
            await self._kill_system_process(pid)

    async def _kill_system_process(self, pid: int) -> None:
        """Kill the system process and all its children, isolated from anyio.

        Uses psutil to find and kill all child processes recursively (including
        bash commands spawned by tools). Falls back to single process kill if
        psutil fails.

        Uses SIGTERM first for graceful shutdown, then SIGKILL after timeout.
        Runs in a thread executor to avoid any async context pollution that
        could cause cancel scope leaks.

        Args:
            pid: Process ID to kill
        """
        import psutil

        def _do_kill() -> None:
            """Synchronous kill logic, runs in a separate thread."""
            try:
                parent = psutil.Process(pid)
            except psutil.NoSuchProcess:
                logger.debug("Process %d already dead", pid)
                return

            # Get all children recursively BEFORE killing parent
            # (once parent is dead, children become orphans and harder to find)
            try:
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []

            all_procs = children + [parent]  # Kill children first, then parent
            logger.debug("Killing process %d and %d children", pid, len(children))

            # SIGTERM to all processes
            for proc in all_procs:
                try:
                    proc.terminate()  # SIGTERM
                    logger.debug("Sent SIGTERM to process %d", proc.pid)
                except psutil.NoSuchProcess:
                    pass

            # Wait for graceful termination (up to 2 seconds)
            gone, alive = psutil.wait_procs(all_procs, timeout=2)

            if gone:
                logger.debug("%d process(es) terminated gracefully", len(gone))

            # SIGKILL any survivors
            for proc in alive:
                try:
                    logger.warning(
                        "Process %d did not terminate after SIGTERM, sending SIGKILL",
                        proc.pid,
                    )
                    proc.kill()  # SIGKILL
                except psutil.NoSuchProcess:
                    pass

        # Run in thread executor for complete isolation from async context
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_kill)

    async def _notify_state_change(self) -> None:
        """Invoke the state change callback if set."""
        if self._state_change_callback is not None:
            try:
                await self._state_change_callback(self)
            except Exception as e:
                logger.error(
                    "Error in state change callback for session %s: %s",
                    self.session_id,
                    e,
                )
