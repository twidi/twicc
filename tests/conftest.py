"""Pytest configuration for Django tests."""

import asyncio
import itertools
import os
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import NamedTuple

import django
import pytest
from django.conf import settings

from twicc import provider_homes


# One counter per directory name prefix, for the whole run (see ``tmp_path``).
_tmp_path_numbers = defaultdict(itertools.count)


@pytest.fixture
def tmp_path(request, tmp_path_factory) -> Path:
    """pytest's ``tmp_path``, minus its cost that grows with the run.

    pytest numbers each test's directory by listing the whole base temp dir,
    which by then holds a directory and a ``*current`` symlink for every test
    that ran before: quadratic over the suite, about ten seconds of the full
    run. Here the number comes from a counter and the directory sits in one
    folder per test module, so nothing is listed. Same name prefix (the test
    name, sanitized, cut to 30 characters, then a dash before the number) and
    same base temp dir, hence the same retention across runs. The
    ``tmp_path_retention_policy`` option is not honoured: this project keeps
    pytest's default (keep everything).
    """
    module = re.sub(r"\W", "_", request.node.path.stem)
    name = re.sub(r"\W", "_", request.node.name)[:30]
    # The dash cannot occur in the sanitized name: "test_x1" + 0 and
    # "test_x" + 10 stay distinct.
    path = tmp_path_factory.getbasetemp() / module / f"{name}-{next(_tmp_path_numbers[(module, name)])}"
    path.mkdir(mode=0o700, parents=True)
    return path


@pytest.fixture
def compute_executor_started():
    """Give direct watcher and compute-adapter tests their runtime worker."""
    from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor

    start_compute_executor()
    try:
        yield
    finally:
        asyncio.run(stop_compute_executor())


def pytest_configure():
    """Configure Django settings before tests run."""
    if not settings.configured:
        settings.configure()
    django.setup()


@pytest.fixture(autouse=True)
def cheap_password_hashes(monkeypatch):
    """Hash new passwords with 1 000 PBKDF2 rounds instead of 600 000.

    The real count costs ~0.3 s per hash and per check, and no test is about
    the work factor. The count is written in the hash itself, so
    ``verify_password`` still runs the real check on what was stored.
    """
    from twicc.auth import hashers

    monkeypatch.setattr(hashers, "_PBKDF2_ITERATIONS", 1000)


class _ShortSleepAsyncio:
    """``asyncio`` as seen by ``db_writer``, with its sleeps cut to 1 ms."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(delay, result=None):
        return await asyncio.sleep(min(delay, 0.001), result)


@pytest.fixture(autouse=True)
def short_db_writer_polls(monkeypatch):
    """Cut the DB writer's 50 ms sleeps to 1 ms.

    The writer sleeps 50 ms whenever its queues are empty (so each job waits up
    to 50 ms) and between retries on a full queue. Both are pure latency, never
    an ordering the code relies on: jobs still go through the real queues, lock
    and loop, in the same order.
    """
    from twicc.providers import db_writer

    monkeypatch.setattr(db_writer, "asyncio", _ShortSleepAsyncio())


@pytest.fixture(autouse=True)
def no_fsync(monkeypatch):
    """Skip the disk flush of ``os.fsync``: tests never survive a power cut anyway.

    The attachment and upload stores fsync every file and directory they
    write, a few milliseconds each. Nothing a test observes depends on the
    data reaching the disk. The descriptor is still checked, so fsyncing a
    closed or invalid one keeps raising, as the real call does. A test that is
    about fsync (that it is called, or that its failure is handled) patches it
    again on top of this one.
    """
    def checked_fsync(fd):
        os.fstat(fd)

    monkeypatch.setattr(os, "fsync", checked_fsync)


@pytest.fixture(autouse=True)
def no_real_notification_delivery(monkeypatch):
    """Cut the wire at Apprise: no test may reach a real notification service.

    ``settings_test`` isolates the DB and the provider homes, but not the data
    dir: ``read_synced_settings()`` reads the developer's real
    ``<data_dir>/settings.json``, so any test exercising a genuine dispatch
    path picks up their live targets. On 2026-09-09 a full-suite run pushed
    several "Message from alice" events from ``test_peer_messages.py`` to the
    developer's ntfy phone.

    ``async_notify`` is the single network boundary — the only Apprise call
    either send path makes. Stubbing it there leaves every layer above it
    real: the target filters, the presence deferral, ``_spawn``/``_send``, URL
    validation and privacy masking all still run, so tests keep asserting that
    a notification *is* dispatched, and what it contains. Only the delivery is
    dropped, which is exactly what a real success returns (``True``).
    """
    import apprise

    async def _swallow(self, *args, **kwargs):
        return True

    monkeypatch.setattr(apprise.Apprise, "async_notify", _swallow)


@pytest.fixture(autouse=True)
def fresh_title_auto_task_state(monkeypatch):
    """Keep live-sync title requests from entering another test's workers."""
    from twicc import title_auto_task

    monkeypatch.setattr(title_auto_task, "_queued", deque())
    monkeypatch.setattr(title_auto_task, "_pending", {})
    monkeypatch.setattr(title_auto_task, "_running", set())
    monkeypatch.setattr(title_auto_task, "_wake", None)


@pytest.fixture(autouse=True)
def fresh_project_directory_cache(monkeypatch):
    """The CLI session payload reads project directories through a module-level
    cache (twicc.projects._project_directories); give each test its own."""
    from twicc import projects

    monkeypatch.setattr(projects, "_project_directories", {})


@pytest.fixture
def db_setup(db):
    """Fixture that provides database access and creates test data helpers."""
    return db


class ProviderHomeDirs(NamedTuple):
    """The two provider homes a test owns (see :func:`provider_home`)."""
    claude: Path
    codex: Path


@pytest.fixture
def provider_home(tmp_path, monkeypatch) -> ProviderHomeDirs:
    """Point both provider homes at fresh directories under ``tmp_path``.

    Sets ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` (``settings_test`` already
    isolates them under a per-process temp root; this narrows them to the
    test) and resets the resolver's cache so every accessor of
    ``twicc.provider_homes`` — ``claude_projects_dir()``, ``codex_sessions_dir()``,
    ``claude_plans_dir()``… — reads the new values. The test creates the
    subfolder it needs (``projects/``, ``sessions/``, ``plans/``).
    """
    dirs = ProviderHomeDirs(claude=tmp_path / "claude-home", codex=tmp_path / "codex-home")
    dirs.claude.mkdir()
    dirs.codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(dirs.claude))
    monkeypatch.setenv("CODEX_HOME", str(dirs.codex))
    provider_homes.reset_cache()
    yield dirs
    # Runs before monkeypatch restores the environment: the next resolution
    # (empty cache) reads the restored settings_test values.
    provider_homes.reset_cache()
