"""
Process state definitions for Claude agent processes.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

import psutil


def format_bytes(size: int) -> str:
    """Format a byte size into a human-readable string.

    Args:
        size: Size in bytes

    Returns:
        Human-readable string like "123.4 MB"
    """
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_process_memory(pid: int) -> int | None:
    """Get the RSS memory usage of a process by PID.

    Args:
        pid: Process ID to query

    Returns:
        RSS memory in bytes, or None if process not found or access denied
    """
    try:
        process = psutil.Process(pid)
        return process.memory_info().rss
    except Exception:
        # Catch all exceptions: NoSuchProcess, AccessDenied, ZombieProcess,
        # OSError, or any other unexpected error
        return None


@dataclass(frozen=True)
class PendingRequest:
    """A request from Claude that is waiting for user response.

    Represents either a tool approval request (Claude wants to use a tool and needs
    permission) or a clarifying question (Claude needs the user to choose between
    options via the AskUserQuestion tool).

    Attributes:
        request_id: UUID unique to this request
        request_type: Either "tool_approval" or "ask_user_question"
        tool_name: SDK tool name (e.g., "Bash", "Write", "AskUserQuestion")
        tool_input: SDK tool input data (parameters for tool approval, questions for ask_user_question)
        created_at: Unix timestamp when the request was created
    """

    request_id: str
    request_type: str
    tool_name: str
    tool_input: dict
    created_at: float


class ProcessState(StrEnum):
    """State of a Claude process in its lifecycle.

    States:
        STARTING: Process is initializing, before first message is sent
        ASSISTANT_TURN: Claude is working on a response
        USER_TURN: Waiting for user input (response complete)
        DEAD: Process has terminated (error, kill, or shutdown)
    """

    STARTING = "starting"
    ASSISTANT_TURN = "assistant_turn"
    USER_TURN = "user_turn"
    DEAD = "dead"


class ProcessInfo(NamedTuple):
    """Immutable snapshot of process state for external consumption.

    Attributes:
        session_id: Claude's session identifier
        project_id: TwiCC project this session belongs to
        state: Current process state
        previous_state: State before the last transition, None for initial state
        started_at: Unix timestamp when the process was started
        state_changed_at: Unix timestamp when the state last changed
        last_activity: Unix timestamp of last activity
        error: Error message if state is DEAD due to error, None otherwise
        memory_rss: RSS memory usage in bytes, or None if unavailable
        kill_reason: Reason for death if DEAD (e.g., "manual", "error", "shutdown")
        pending_request: Active pending request if Claude is waiting for user input, None otherwise
    """

    session_id: str
    project_id: str
    state: ProcessState
    previous_state: ProcessState | None
    started_at: float
    state_changed_at: float
    last_activity: float
    error: str | None = None
    memory_rss: int | None = None
    kill_reason: str | None = None
    pending_request: PendingRequest | None = None

    @property
    def memory_rss_human(self) -> str | None:
        """Get human-readable memory usage (e.g., '123.4 MB')."""
        if self.memory_rss is None:
            return None
        return format_bytes(self.memory_rss)


def serialize_process_info(info: ProcessInfo) -> dict:
    """Serialize a ProcessInfo to a dictionary for JSON transmission.

    Args:
        info: The ProcessInfo to serialize

    Returns:
        Dictionary with session_id, project_id, state, timestamps, and optionally error/memory
    """
    data = {
        "session_id": info.session_id,
        "project_id": info.project_id,
        "state": info.state,  # ProcessState is StrEnum, serializes directly
        "started_at": info.started_at,  # Unix timestamp when process started
        "state_changed_at": info.state_changed_at,  # Unix timestamp when state last changed
    }
    if info.error is not None:
        data["error"] = info.error
    if info.memory_rss is not None:
        data["memory"] = info.memory_rss
    if info.kill_reason is not None:
        data["kill_reason"] = info.kill_reason
    if info.pending_request is not None:
        data["pending_request"] = {
            "request_id": info.pending_request.request_id,
            "request_type": info.pending_request.request_type,
            "tool_name": info.pending_request.tool_name,
            "tool_input": info.pending_request.tool_input,
            "created_at": info.pending_request.created_at,
        }
    return data
