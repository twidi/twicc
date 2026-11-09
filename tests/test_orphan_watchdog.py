"""Tests for the watchdog that ends a test process whose parent has died."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The middle process of the chain: the test kills it, as a timeout kills ``uv``.
PARENT = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', sys.argv[1]]); time.sleep(60)"

STUCK_CHILD = textwrap.dedent("""
    import os
    from tests.orphan_watchdog import start_orphan_watchdog
    start_orphan_watchdog(poll_seconds=0.05, grace_seconds=30)
    print(os.getpid(), flush=True)
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("interrupted", flush=True)
""")

CHILD_SWALLOWING_THE_INTERRUPT = textwrap.dedent("""
    import os
    from tests.orphan_watchdog import start_orphan_watchdog
    start_orphan_watchdog(poll_seconds=0.05, grace_seconds=0.2)
    print(os.getpid(), flush=True)
    while True:
        try:
            while True:
                pass
        except KeyboardInterrupt:
            pass
""")


@pytest.fixture
def start_chain():
    """Start parent and child, and never leave either of them behind."""
    pids = []
    parents = []

    def start(child_code):
        parent = subprocess.Popen(
            [sys.executable, "-c", PARENT, child_code],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            text=True,
        )
        parents.append(parent)
        pids.append(int(parent.stdout.readline()))
        return parent

    yield start

    for parent in parents:
        parent.kill()
        parent.wait()
        parent.stdout.close()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def kill_parent_and_read_child_output(parent):
    parent.kill()
    # EOF on the pipe proves that the child has exited, whoever reaps it.
    output, _ = parent.communicate(timeout=20)
    return output


def test_child_keeps_running_while_its_parent_lives(start_chain):
    parent = start_chain(STUCK_CHILD)
    with pytest.raises(subprocess.TimeoutExpired):
        parent.communicate(timeout=0.3)


def test_orphaned_child_is_interrupted(start_chain):
    parent = start_chain(STUCK_CHILD)
    assert kill_parent_and_read_child_output(parent) == "interrupted\n"


def test_orphaned_child_that_swallows_the_interrupt_is_ended(start_chain):
    parent = start_chain(CHILD_SWALLOWING_THE_INTERRUPT)
    assert kill_parent_and_read_child_output(parent) == ""
