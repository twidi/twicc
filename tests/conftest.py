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
