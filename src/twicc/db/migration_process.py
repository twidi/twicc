"""Run migrations synchronously in a separate interpreter before server startup."""

import logging
import signal
import subprocess
import sys

logger = logging.getLogger("twicc.db.migration_process")


def run_migrations() -> None:
    """Wait for migrations, and reap the child before propagating failures."""
    command = [
        sys.executable, "-m", "django", "migrate",
        "--settings=twicc.settings_migration", "--verbosity=0",
    ]
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    pending_interrupt = None

    def defer_interrupt(signum, frame):
        nonlocal pending_interrupt
        if signum == signal.SIGINT and previous_sigint == signal.SIG_IGN:
            return
        if pending_interrupt is None:
            pending_interrupt = (signum, frame)

    def interrupt(signum, frame):
        raise SystemExit(128 + signum)

    # Startup has no server signal handler yet. Convert SIGTERM into an
    # exception so cleanup finishes while the parent still owns the lock.
    child = None
    try:
        # Defer exceptions until Popen returns ownership of its child. Python
        # handlers do not block signals, so the child inherits no signal mask.
        signal.signal(signal.SIGTERM, defer_interrupt)
        signal.signal(signal.SIGINT, defer_interrupt)
        # No env map: inherit the data dir and all other startup settings.
        # close_fds keeps the parent's instance lock out of the child.
        child = subprocess.Popen(command, stderr=subprocess.PIPE, text=True, close_fds=True)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, previous_sigint)
        if pending_interrupt is not None:
            signum, frame = pending_interrupt
            if signum == signal.SIGINT and callable(previous_sigint):
                previous_sigint(signum, frame)
            else:
                interrupt(signum, frame)
        _, stderr = child.communicate()
        if child.returncode:
            logger.error("Migration process failed with exit code %s:\n%s", child.returncode, stderr.rstrip())
            raise subprocess.CalledProcessError(child.returncode, command, stderr=stderr)
    except subprocess.CalledProcessError:
        raise
    except BaseException:
        if child is not None and child.poll() is None:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                child.terminate()
                try:
                    _, stderr = child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    _, stderr = child.communicate()
                logger.error("Migration process interrupted; child reaped.\n%s", stderr.rstrip())
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
        else:
            logger.exception("Migration process did not complete")
        raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
