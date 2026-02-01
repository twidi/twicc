"""
Process state definitions for Claude agent processes.
"""

from enum import StrEnum
from typing import NamedTuple


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
        last_activity: Unix timestamp of last activity
        error: Error message if state is DEAD due to error, None otherwise
    """

    session_id: str
    project_id: str
    state: ProcessState
    last_activity: float
    error: str | None = None
