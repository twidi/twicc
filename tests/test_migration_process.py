"""Migration settings and subprocess lifecycle regression tests."""

import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest


def probe(tmp_path, script, **extra):
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        env={**os.environ, "TWICC_DATA_DIR": str(tmp_path), **extra}, timeout=60,
    )


@pytest.mark.parametrize("fallback", ["", "0", "1"])
def test_runtime_never_loads_custom_driver(tmp_path, fallback):
    result = probe(tmp_path, """
import django, sys
from django.conf import settings
django.setup()
from django.db import connections
connections['default'].ensure_connection()
assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
assert not any(name.startswith('twicc.db.backends.sqlite3') for name in sys.modules)
""", DJANGO_SETTINGS_MODULE="twicc.settings", TWICC_SQLITE_STANDARD_MIGRATIONS=fallback)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fallback,engine", [("", "twicc.db.backends.sqlite3"), ("1", "django.db.backends.sqlite3")])
def test_migration_settings_preserve_database_options(tmp_path, fallback, engine):
    result = probe(tmp_path, f"""
from twicc import settings, settings_migration
assert settings_migration.DATABASES['default']['ENGINE'] == {engine!r}
assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
for key in ('NAME', 'OPTIONS'):
    assert settings_migration.DATABASES['default'][key] == settings.DATABASES['default'][key]
assert settings_migration.LOGGING == settings.LOGGING
""", TWICC_SQLITE_STANDARD_MIGRATIONS=fallback)
    assert result.returncode == 0, result.stderr


def test_real_child_migrates_disposable_database_and_logs_once(tmp_path):
    result = probe(tmp_path, """
import django, sys
django.setup()
from twicc.db.migration_process import run_migrations
run_migrations()
assert not any(name.startswith('twicc.db.backends.sqlite3') for name in sys.modules)
""", DJANGO_SETTINGS_MODULE="twicc.settings")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(tmp_path / "db/data.sqlite") as db:
        assert db.execute("SELECT count(*) FROM django_migrations").fetchone()[0] > 0
    log = (tmp_path / "logs/backend.log").read_text()
    assert log.count("Migration start migration=core.0001_initial ") == 1
    assert "Migration FK check" in log


def test_child_failure_preserves_traceback_in_backend_log(tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db/data.sqlite").write_text("invalid sqlite file")
    result = probe(tmp_path, """
import django
django.setup()
from twicc.db.migration_process import run_migrations
run_migrations()
""", DJANGO_SETTINGS_MODULE="twicc.settings")
    assert result.returncode != 0
    log = (tmp_path / "logs/backend.log").read_text()
    assert "Traceback (most recent call last)" in log
    assert "file is not a database" in log
    assert "Migration process failed" in log


@pytest.mark.parametrize("interrupt,ignore_term", [(signal.SIGINT, False), (signal.SIGTERM, False), (signal.SIGTERM, True)])
def test_interruption_reaps_child_before_lock_release(tmp_path, interrupt, ignore_term):
    # Replace only the child command with a slow real process. Keep launcher and lock real.
    child_script = f"""
import os, signal, time
assert os.environ['TWICC_MIGRATION_TEST_SENTINEL'] == 'inherited'
from pathlib import Path
assert not any(os.readlink(fd) == {str(tmp_path / 'twicc.lock')!r}
               for fd in Path('/proc/self/fd').iterdir() if fd.exists())
if {ignore_term!r}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path({str(tmp_path / 'child.ready')!r}).touch()
time.sleep(120)
"""
    script = f"""
import os, signal, subprocess, sys
from pathlib import Path
from twicc.instance_lock import InstanceLock
from twicc.db import migration_process
original = subprocess.Popen
def slow_child(command, **kwargs):
    child = original([sys.executable, '-c', {child_script!r}], **kwargs)
    Path({str(tmp_path / 'child.pid')!r}).write_text(str(child.pid))
    return child
migration_process.subprocess.Popen = slow_child
with InstanceLock(Path({str(tmp_path)!r})) as lock:
    os.set_inheritable(lock._fd, True)
    try:
        migration_process.run_migrations()
    finally:
        pid = int(Path({str(tmp_path / 'child.pid')!r}).read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            Path({str(tmp_path / 'reaped')!r}).touch()
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script], stderr=subprocess.PIPE, text=True,
        env={**os.environ, "TWICC_MIGRATION_TEST_SENTINEL": "inherited"},
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "child.ready").exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "child.ready").exists(), parent.communicate(timeout=2)[1] if parent.poll() is not None else "Child missing"
        parent.send_signal(interrupt)
        _, stderr = parent.communicate(timeout=10)
        assert parent.returncode != 0, stderr
        assert (tmp_path / "reaped").exists(), stderr
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if (tmp_path / "child.pid").exists():
            try:
                os.kill(int((tmp_path / "child.pid").read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_child_launch_failure_is_logged(tmp_path):
    result = probe(tmp_path, """
import django, sys
django.setup()
from twicc.db.migration_process import run_migrations
sys.executable = '/nonexistent/twicc-migration-python'
run_migrations()
""", DJANGO_SETTINGS_MODULE="twicc.settings")
    assert result.returncode != 0
    log = (tmp_path / "logs/backend.log").read_text()
    assert "Migration process did not complete" in log
    assert "FileNotFoundError" in log


@pytest.mark.parametrize("interrupt", [signal.SIGINT, signal.SIGTERM])
def test_construction_interruption_reaps_child_under_lock(tmp_path, interrupt):
    child_script = f"""
import signal, time
from pathlib import Path
blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
assert not blocked.intersection((signal.SIGINT, signal.SIGTERM))
Path({str(tmp_path / 'construction.ready')!r}).touch()
time.sleep(60)
"""
    result = probe(tmp_path, f"""
import os, signal, subprocess, sys, time
from pathlib import Path
from twicc.instance_lock import InstanceLock
from twicc.db import migration_process
original = subprocess.Popen
spawned = []
handlers = {{sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}}
def interrupt_before_return(command, **kwargs):
    child = original([sys.executable, '-c', {child_script!r}], **kwargs)
    spawned.append(child)
    deadline = time.monotonic() + 5
    while not Path({str(tmp_path / 'construction.ready')!r}).exists():
        assert child.poll() is None and time.monotonic() < deadline, 'Child readiness failed'
        time.sleep(0.01)
    communicate = child.communicate
    def repeated_interruption(**kwargs):
        child.communicate = communicate
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGTERM)
        return communicate(**kwargs)
    child.communicate = repeated_interruption
    os.kill(os.getpid(), {int(interrupt)})
    return child
migration_process.subprocess.Popen = interrupt_before_return
try:
    with InstanceLock(Path({str(tmp_path)!r})):
        try:
            migration_process.run_migrations()
        except (KeyboardInterrupt, SystemExit) as error:
            assert isinstance(error, {'KeyboardInterrupt' if interrupt == signal.SIGINT else 'SystemExit'})
            if isinstance(error, SystemExit):
                assert error.code == 143
        else:
            raise AssertionError('Interruption did not propagate')
        # waitpid proves reaping, rather than only termination.
        try:
            os.waitpid(spawned[0].pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            raise AssertionError('Child was not reaped before lock release')
        contender = original([sys.executable, '-c',
            'from pathlib import Path; from twicc.instance_lock import InstanceLock, InstanceAlreadyRunning\\n'
            + 'lock = InstanceLock(Path(' + repr({str(tmp_path)!r}) + '))\\n'
            + 'try:\\n    lock.acquire()\\nexcept InstanceAlreadyRunning:\\n    pass\\n'
            + 'else:\\n    raise AssertionError("Lock released before child reaped")'], stderr=subprocess.PIPE, text=True)
        _, stderr = contender.communicate(timeout=5)
        assert contender.returncode == 0, stderr
    for sig, handler in handlers.items():
        assert signal.getsignal(sig) == handler
finally:
    for child in spawned:
        if child.poll() is None:
            child.kill()
        child.communicate()
""")
    assert result.returncode == 0, result.stderr


def test_launch_failure_restores_signal_handlers(tmp_path):
    result = probe(tmp_path, """
import signal, sys
from twicc.db.migration_process import run_migrations
def original_handler(signum, frame):
    pass
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, original_handler)
sys.executable = '/nonexistent/twicc-migration-python'
try:
    run_migrations()
except FileNotFoundError:
    pass
else:
    raise AssertionError('Launch failure did not propagate')
for sig in (signal.SIGINT, signal.SIGTERM):
    assert signal.getsignal(sig) is original_handler
""")
    assert result.returncode == 0, result.stderr
