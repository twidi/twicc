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
