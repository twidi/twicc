"""Polling loop for the status file."""

from __future__ import annotations

import time
from pathlib import Path
from typing import NamedTuple

import orjson


POLL_INTERVAL_SECONDS = 0.1

# Every status that ends a request. ``fetched`` is the one read outcome:
# ``session <ID> pending-requests`` returns data instead of confirming a
# mutation, so no mutation word fits it. Owned here and imported by
# ``transport``; a second copy would drift. Adding a member means adding its
# timestamp field to ``drop_requests_watcher._STATUS_TIME_FIELDS`` too.
FINAL_STATUSES = ("created", "sent", "updated", "stopped", "deleted",
                  "fetched", "rejected", "failed")


class PollOutcome(NamedTuple):
    status: str | None        # None => timeout
    data: dict | None
    received_seen: bool       # True if at any point the status was "received"


def poll_status(status_path: Path, timeout_seconds: int) -> PollOutcome:
    """Loop reading the status file until a final status appears or timeout."""
    deadline = time.time() + timeout_seconds
    received_seen = False
    last_data: dict | None = None

    while time.time() < deadline:
        if status_path.exists():
            try:
                data = orjson.loads(status_path.read_bytes())
            except (orjson.JSONDecodeError, OSError):
                # Status file mid-rename — retry next tick.
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            status = data.get("status")
            last_data = data
            if status == "received":
                received_seen = True
            elif status in FINAL_STATUSES:
                return PollOutcome(status=status, data=data,
                                   received_seen=received_seen)
        time.sleep(POLL_INTERVAL_SECONDS)

    return PollOutcome(status=None, data=last_data,
                       received_seen=received_seen)
