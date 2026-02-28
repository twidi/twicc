"""
Claude process wrapper for a single SDK client instance.
"""

import asyncio
import logging
import time
import uuid

import orjson
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate, ResultMessage, ToolPermissionContext,
)

from .sdk_logger import patch_client as patch_client_for_logging
from .states import PendingRequest, ProcessInfo, ProcessState, get_process_memory

logger = logging.getLogger(__name__)

# Type alias for the state change callback
StateChangeCallback = Callable[["ClaudeProcess"], Coroutine[Any, Any, None]]


async def _dummy_hook(input_data: dict, tool_use_id: str, context: Any) -> dict:
    """SDK PreToolUse hook required to keep the stream open for can_use_tool callbacks.

    The Python SDK requires at least one PreToolUse hook registered for the
    can_use_tool callback to fire during streaming mode. This hook approves
    all tool uses unconditionally (actual permission logic is in can_use_tool).
    """
    return {"continue_": True}


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

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        permission_mode: str,
        selected_model: str | None,
        get_last_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
    ) -> None:
        """Initialize a Claude process wrapper.

        Args:
            session_id: The session to resume
            project_id: The TwiCC project this belongs to
            cwd: Working directory for Claude operations
            permission_mode: SDK permission mode (e.g., "default", "bypassPermissions")
            selected_model: SDK model shorthand (e.g., "opus", "sonnet") or None for default
            get_last_session_slug: Async callback that retrieves the most recent
                slug from a session's JSONL items. Takes a session_id and returns the slug
                string or None if not found.
        """
        self.session_id = session_id
        self.project_id = project_id
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.selected_model = selected_model
        self.state = ProcessState.STARTING
        self.previous_state: ProcessState | None = None
        self.started_at = time.time()
        self.state_changed_at = self.started_at
        self.last_activity = self.started_at
        self.error: str | None = None
        self.kill_reason: str | None = None

        self._client: ClaudeSDKClient | None = None
        self._message_loop_task: asyncio.Task[None] | None = None
        self._state_change_callback: StateChangeCallback | None = None
        self._pending_request: PendingRequest | None = None
        self._pending_request_future: asyncio.Future[PermissionResultAllow | PermissionResultDeny] | None = None
        self._get_last_session_slug = get_last_session_slug

        logger.debug(
            "ClaudeProcess created for session %s, project %s, cwd=%s, permission_mode=%s, model=%s",
            session_id,
            project_id,
            cwd,
            permission_mode,
            selected_model,
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
        self.previous_state = old_state
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

    @property
    def pending_request(self) -> PendingRequest | None:
        """The active pending request, if Claude is waiting for user input."""
        return self._pending_request

    def get_info(self) -> ProcessInfo:
        """Get an immutable snapshot of the process state."""
        # Don't query memory for dead processes - the subprocess no longer exists
        memory_rss = None if self.state == ProcessState.DEAD else self.get_memory_rss()
        return ProcessInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            state=self.state,
            previous_state=self.previous_state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
            pending_request=self._pending_request,
        )

    def get_permission_suggestions(
        self, tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> list[dict] | None:
        """Extract, serialize and filter permission suggestions from the SDK context.

        The SDK may provide suggestions as ``PermissionUpdate`` dataclass instances
        or, in some cases, as plain dicts. This method normalises both forms into
        a list of JSON-serialisable dicts ready for transmission to the frontend.

        Filtering applied:
        - For ``addDirectories`` / ``removeDirectories`` suggestions, the project
          directory (``self.cwd``) is removed from the directories list (it is always
          implicitly allowed). If the directories list becomes empty after filtering,
          the suggestion is dropped entirely.

        Args:
            tool_name: The name of the tool requesting permission (used to generate
                MCP suggestions when the SDK provides none)
            input_data: The tool's input parameters (reserved for future filtering)
            context: SDK ``ToolPermissionContext`` containing the suggestions list

        Returns:
            A list of serialised permission suggestion dicts, or ``None`` if there
            are none (or all were filtered out).
        """
        serialized = [s.to_dict() if isinstance(s, PermissionUpdate) else s for s in context.suggestions or ()]

        result = []
        for suggestion in serialized:
            suggestion_type = suggestion.get("type")

            # Strip the project directory from directory suggestions (always implicitly allowed).
            if suggestion_type in ("addDirectories", "removeDirectories"):
                directories = suggestion.get("directories")
                if directories is not None:
                    # Filter out the project directory — it is always implicitly allowed
                    directories = [d for d in directories if d != self.cwd]
                    if not directories:
                        # Nothing left to suggest after filtering
                        continue
                    suggestion = {**suggestion, "directories": directories}

            # Ungroup suggestions that bundle multiple rules: split into one
            # suggestion per rule so the frontend can present them individually.
            rules = suggestion.get("rules")
            if rules and len(rules) > 1:
                for rule in rules:
                    result.append({k: ([rule] if k == "rules" else v) for k, v in suggestion.items()})
            else:
                result.append(suggestion)

        # Inject wildcard MCP suggestions: for each rule targeting a specific MCP tool
        # (mcp__{name}__{tool}), add a wildcard suggestion (mcp__{name}__*) so the user
        # can choose to allow all tools from that MCP server at once.
        # Collect all existing toolNames to know what's already covered.
        existing_tool_names: set[str] = set()
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                if tn:
                    existing_tool_names.add(tn)

        # If no suggestion covers the current tool and it's an MCP tool, create one.
        # The wildcard loop below will then automatically add the server-wide variant.
        if tool_name not in existing_tool_names:
            parts = tool_name.split("__")
            if len(parts) == 3 and parts[0] == "mcp":
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': tool_name}],
                    'behavior': 'allow',
                    'destination': 'localSettings',
                })
                existing_tool_names.add(tool_name)

        # For each specific MCP tool suggestion, add a server-wide wildcard variant
        # (mcp__{name}__*) so the user can allow all tools from that server at once.
        seen_mcp_prefixes: set[str] = set()
        extra: list[dict] = []
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                # Match mcp__{name}__{tool} — exactly 3 parts separated by "__"
                parts = tn.split("__")
                if len(parts) != 3 or parts[0] != "mcp":
                    continue
                mcp_prefix = f"mcp__{parts[1]}"
                if mcp_prefix in seen_mcp_prefixes:
                    continue
                seen_mcp_prefixes.add(mcp_prefix)
                wildcard = f"{mcp_prefix}__*"
                # Only add if neither the bare prefix nor the wildcard already exist
                if mcp_prefix not in existing_tool_names and wildcard not in existing_tool_names:
                    extra.append(
                        {k: ([{"toolName": wildcard}] if k == "rules" else v) for k, v in s.items()}
                    )

        result.extend(extra)

        return result or None

    async def _handle_pending_request(
        self,
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK can_use_tool callback: creates a pending request and waits for resolution.

        Called by the SDK when Claude wants to use a tool that requires permission,
        or when Claude asks a clarifying question via AskUserQuestion. This method
        blocks (via an asyncio Future) until the frontend resolves the request.

        Args:
            tool_name: The tool Claude wants to use (e.g., "Bash", "AskUserQuestion")
            input_data: The tool's input parameters
            context: SDK ToolPermissionContext with optional permission suggestions

        Returns:
            PermissionResultAllow or PermissionResultDeny from the user's response
        """
        logger.debug(
            "can_use_tool called: tool_name=%s, input_data=%s, permission_suggestions=%s",
            tool_name,
            orjson.dumps(input_data, option=orjson.OPT_INDENT_2).decode(),
            getattr(context, "suggestions", None),
        )

        request_id = str(uuid.uuid4())

        if tool_name == "AskUserQuestion":
            request_type = "ask_user_question"
        else:
            request_type = "tool_approval"

        permission_suggestions = self.get_permission_suggestions(tool_name, input_data, context)

        self._pending_request = PendingRequest(
            request_id=request_id,
            request_type=request_type,
            tool_name=tool_name,
            tool_input=input_data,
            created_at=time.time(),
            permission_suggestions=permission_suggestions,
        )

        # Create a Future for the response
        self._pending_request_future = asyncio.get_event_loop().create_future()

        # Notify frontend via state change callback (broadcasts WebSocket message)
        await self._notify_state_change()

        # Block here until frontend responds
        response = await self._pending_request_future

        # For ExitPlanMode: Detect if the user modified the plan content
        # Because of a "bug" in claude agent sdk / claude code, the plan passed via the response is not taken into
        #  account, so we'll update it ourselves in the plan file (via the `_update_plan` method)
        if (
            tool_name == "ExitPlanMode"
            and isinstance(response, PermissionResultAllow)
            and response.updated_input is not None
            and response.updated_input.get("plan") != input_data.get("plan")
        ):
            await self._update_plan(response.updated_input["plan"])

        # Clear pending state
        self._pending_request = None
        self._pending_request_future = None

        # Notify that pending request is resolved
        await self._notify_state_change()

        return response

    def resolve_pending_request(
        self, response: PermissionResultAllow | PermissionResultDeny
    ) -> bool:
        """Resolve the pending request with the user's response.

        Called by ProcessManager when a WebSocket response arrives from the frontend.

        Args:
            response: The permission result to send back to the SDK

        Returns:
            True if resolved, False if no pending request or already resolved.
        """
        if self._pending_request_future is None or self._pending_request_future.done():
            return False
        self._pending_request_future.set_result(response)
        return True

    def _cancel_pending_request_future(self) -> None:
        """Cancel any active pending request Future to avoid asyncio warnings.

        Called during process death (error or kill) to ensure the Future is not
        left unawaited.
        """
        if self._pending_request_future is not None and not self._pending_request_future.done():
            self._pending_request_future.cancel()
        self._pending_request = None
        self._pending_request_future = None

    def _build_query_prompt(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> AsyncIterator[dict]:
        """Build prompt for SDK query() as an async generator.

        Always returns an async generator yielding a single transport message,
        which is required for streaming mode (needed by the can_use_tool callback).

        Each yielded dict is a complete transport message with the structure:
            {"type": "user", "message": {"role": "user", "content": [...]}, ...}

        Args:
            text: The message text
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Returns:
            An async iterator yielding a single transport message dict.
        """
        content_blocks: list[dict] = []

        if images:
            content_blocks.extend(images)

        if documents:
            content_blocks.extend(documents)

        content_blocks.append({"type": "text", "text": text})

        async def _message_stream() -> AsyncIterator[dict]:
            yield {
                "type": "user",
                "message": {"role": "user", "content": content_blocks},
                "parent_tool_use_id": None,
            }

        return _message_stream()

    async def start(
        self,
        prompt: str,
        on_state_change: StateChangeCallback,
        resume: bool = True,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Start the process and send the first message.

        This connects to Claude and sends the initial prompt. A background task
        is started to consume messages and track state changes.

        Args:
            prompt: The initial message to send
            on_state_change: Async callback invoked when process state changes
            resume: If True (default), resume an existing session. If False,
                   create a new session with the session_id as the custom UUID.
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

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
                permission_mode=self.permission_mode,
                model=self.selected_model,
                setting_sources=["user", "project", "local"],
                can_use_tool=self._handle_pending_request,
                hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_dummy_hook])]},
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
            patch_client_for_logging(self._client, self.session_id)

            # Connect without prompt to enter streaming mode, then send the message
            # via query(). The SDK's connect(prompt) with a string puts the transport
            # in "print mode" which closes stdin immediately, but ClaudeSDKClient
            # always uses streaming mode for the control protocol. We must connect()
            # first, then query() to send messages.
            await self._client.connect()

            # Build query prompt as async generator (streaming mode)
            query_prompt = self._build_query_prompt(prompt, images, documents)
            await self._client.query(query_prompt)

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

    async def set_permission_mode(self, mode: str) -> None:
        """Change permission mode on the live SDK client.

        Calls the SDK's set_permission_mode() method to update the permission mode
        on the running Claude process, then updates the local attribute to keep
        the in-memory state in sync.

        Args:
            mode: The permission mode to set (e.g., "default", "acceptEdits", "bypassPermissions")

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.debug(
            "Setting permission mode to '%s' for session %s",
            mode,
            self.session_id,
        )
        await self._client.set_permission_mode(mode)
        self.permission_mode = mode

    async def set_model(self, model: str | None) -> None:
        """Change the AI model on the live SDK client.

        Calls the SDK's set_model() method to update the model on the running
        Claude process, then updates the local attribute to keep the in-memory
        state in sync.

        Args:
            model: The model shorthand (e.g., "opus", "sonnet") or None for default

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.debug(
            "Setting model to '%s' for session %s",
            model,
            self.session_id,
        )
        await self._client.set_model(model)
        self.selected_model = model

    async def apply_live_settings(
        self,
        permission_mode: str,
        selected_model: str | None,
    ) -> None:
        """Apply permission mode and model changes to the live SDK client.

        Compares the requested values with the current values and calls the SDK
        methods only when they differ.

        Permission mode can be changed in any active state (USER_TURN or ASSISTANT_TURN).
        Model can only be changed during USER_TURN (the SDK's set_model() has no effect
        during ASSISTANT_TURN).

        Args:
            permission_mode: Desired permission mode
            selected_model: Desired model shorthand, or None
        """
        if permission_mode != self.permission_mode:
            await self.set_permission_mode(permission_mode)

        if selected_model != self.selected_model and self.state == ProcessState.USER_TURN:
            await self.set_model(selected_model)

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Send a follow-up message to the process.

        Args:
            text: The message text to send
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

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

            # Build query prompt as async generator (streaming mode)
            query_prompt = self._build_query_prompt(text, images, documents)
            await self._client.query(query_prompt)

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

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_pending_request_future()

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

                    self._set_state(ProcessState.USER_TURN)
                    await self._notify_state_change()

                elif self.state != ProcessState.ASSISTANT_TURN:
                    # Enforce assistant state if another message came after the ResultMessage
                    self._set_state(ProcessState.ASSISTANT_TURN)
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

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_pending_request_future()

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

    async def _update_plan(self, new_plan: str) -> None:
        """Handle a user-modified plan for ExitPlanMode.

        Called when the user approves ExitPlanMode with changes and the plan
        content differs from the original. Retrieves the session slug from the
        database, then overwrites the plan file at ~/.claude/plans/{slug}.md.

        Args:
            new_plan: The modified plan content from the user
        """
        from pathlib import Path

        slug = await self._get_last_session_slug(self.session_id)
        if slug is None:
            logger.warning(
                "Cannot update plan for session %s: no slug found in session items",
                self.session_id,
            )
            return

        plan_file = Path.home() / ".claude" / "plans" / f"{slug}.md"
        if not plan_file.exists():
            logger.warning(
                "Plan file does not exist for session %s: %s",
                self.session_id,
                plan_file,
            )
            return

        try:
            plan_file.write_text(new_plan, encoding="utf-8")
            logger.info(
                "Plan file updated for session %s: %s (%d chars)",
                self.session_id,
                plan_file,
                len(new_plan),
            )
        except OSError as e:
            logger.error(
                "Failed to write plan file for session %s: %s",
                self.session_id,
                e,
            )

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
