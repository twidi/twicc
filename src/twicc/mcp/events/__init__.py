"""External MCP events boundary.

Internal per-session MCP tools and the shared wait detector remain independent.
The clock separates UTC persistence, float epoch comparisons and monotonic delays.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import time
from typing import NamedTuple


class Clock(NamedTuple):
    utcnow: Callable[[], datetime]
    epoch: Callable[[], float]
    monotonic: Callable[[], float]


SYSTEM_CLOCK = Clock(lambda: datetime.now(UTC), time.time, time.monotonic)


_runtime = None


def get_runtime():
    """Return the installed backend runtime, or None during offline compute."""
    return _runtime


def set_runtime(runtime):
    """Install or clear the runtime at the external MCP lifespan boundary."""
    global _runtime
    _runtime = runtime


def rebase(session_id):
    """Wake event monitors after a committed history reset, when enabled."""
    runtime = get_runtime()
    if runtime is not None:
        runtime.rebase(session_id)
