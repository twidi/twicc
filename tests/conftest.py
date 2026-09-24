"""Pytest configuration for Django tests."""

from pathlib import Path
from typing import NamedTuple

import django
import pytest
from django.conf import settings

from twicc import provider_homes


def pytest_configure():
    """Configure Django settings before tests run."""
    if not settings.configured:
        settings.configure()
    django.setup()


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
