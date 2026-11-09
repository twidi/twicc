"""End a test process whose parent has died.

A timeout that kills only a wrapper, such as ``subprocess.run(["uv", "run",
"pytest"], timeout=45)``, leaves pytest running under PID 1: ``uv`` cannot relay
a SIGKILL. From then on no stop reaches it, because every stop follows the
parent links, and a test stuck in a loop runs until the machine is restarted.

The watchdog polls ``os.getppid()`` rather than using ``PR_SET_PDEATHSIG``: that
signal fires when the *thread* that started the process ends, and ``uv`` is
multi-threaded.
"""

from __future__ import annotations

import _thread
import os
import threading
import time


def _watch(parent_pid: int, poll_seconds: float, grace_seconds: float) -> None:
    while os.getppid() == parent_pid:
        time.sleep(poll_seconds)
    # Interrupt first, so that fixtures and the removal of the test root run.
    _thread.interrupt_main()
    time.sleep(grace_seconds)
    # Still alive: the test swallows the interrupt.
    os._exit(1)


def start_orphan_watchdog(poll_seconds: float = 1.0, grace_seconds: float = 10.0) -> None:
    """Interrupt this process when its parent dies, then end it after the grace period."""
    threading.Thread(
        target=_watch,
        args=(os.getppid(), poll_seconds, grace_seconds),
        name="orphan-watchdog",
        daemon=True,
    ).start()
