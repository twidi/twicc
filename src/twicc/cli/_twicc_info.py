"""Read the live TwiCC backend's PID from ``twicc.info.json``.

Used by the ``agents`` / ``agent`` CLIs to scope DB queries to rows owned by
the currently running TwiCC backend. CLI subcommands run in a fresh
process (no in-memory state to consult) and reach the live TwiCC by:

1. Resolving the data directory via :func:`twicc.paths.get_data_dir`.
2. Reading the sidecar info file written by :class:`InstanceLock`.
3. Verifying the recorded PID is still alive (``psutil.pid_exists``).

If the file is missing (TwiCC not running, or shut down cleanly) or the
PID has been recycled / cleared by the kernel, callers get ``None`` rather than ambiguous data.
"""

from __future__ import annotations

from typing import NamedTuple

import orjson
import psutil


class TwiCCInfo(NamedTuple):
    """Snapshot of the live TwiCC backend's sidecar info."""

    pid: int
    port: int | None
    started_at: str | None


def resolve_live_twicc() -> TwiCCInfo | None:
    """Return the live TwiCC info, or ``None`` when nothing is running.

    The file format matches :meth:`InstanceLock.write_info` — ``pid``,
    optional ``port``, ISO ``started_at``. A missing file, malformed JSON,
    or a recorded PID that no longer exists in the kernel process table
    all collapse to ``None``: the caller cannot trust any DB row tagged
    with that PID and should refuse to operate.
    """
    from twicc.paths import get_data_dir

    info_path = get_data_dir() / "twicc.info.json"
    try:
        data = orjson.loads(info_path.read_bytes())
    except (FileNotFoundError, ValueError, OSError):
        return None

    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    try:
        if not psutil.pid_exists(pid):
            return None
    except Exception:
        # psutil may raise on permission-restricted systems; treat any failure
        # the same as "PID not provably alive" so we never return matches
        # against a possibly-recycled PID.
        return None

    return TwiCCInfo(
        pid=pid,
        port=data.get("port"),
        started_at=data.get("started_at"),
    )

