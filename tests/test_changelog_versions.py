"""Changelog version tracking: what a launch decides to announce.

Both tracking keys absent is ambiguous — a first install or an install from
≤ 1.2.1, before the keys existed — and the two need opposite answers. The
discriminator is ``paths.is_first_run()``; these tests pin both branches.
"""

import orjson
import pytest
from django.conf import settings as django_settings

from twicc import asgi
from twicc import paths
import twicc.synced_settings as ss

CURRENT = "1.94.2"


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    monkeypatch.setattr(django_settings, "APP_VERSION", CURRENT)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.fixture
def first_run(monkeypatch):
    """Report a data dir that had no database at launch."""
    monkeypatch.setattr(asgi, "is_first_run", lambda: True)


@pytest.fixture
def not_first_run(monkeypatch):
    """Report a data dir whose database predates this launch."""
    monkeypatch.setattr(asgi, "is_first_run", lambda: False)


def stored(path):
    return orjson.loads(path.read_bytes())


def test_read_settings_never_returns_an_empty_dict(temp_settings):
    """Regression guard: emptiness cannot detect a first install.

    ``read_synced_settings`` merges ``SYNCED_SETTINGS_DEFAULTS``, so it reads
    back non-empty even with no file at all. A ``not all_settings`` test is
    therefore dead code — it used to gate the first-install branch and made
    every fresh install announce everything since 1.2.1.
    """
    assert not temp_settings.exists()

    assert ss.read_synced_settings()


def test_first_install_announces_nothing(temp_settings, first_run):
    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == (CURRENT, CURRENT, False)


def test_first_install_seeds_both_keys(temp_settings, first_run):
    """The seed lands on disk, so the next launch takes the normal path."""
    asgi._resolve_changelog_versions()

    assert stored(temp_settings)["lastChangelogVersionSeen"] == CURRENT
    assert stored(temp_settings)["previousLastChangelogVersionSeen"] == CURRENT


def test_install_from_before_1_2_1_announces_everything(temp_settings, not_first_run):
    """Settings with no tracking on an existing install → user was on ≤ 1.2.1."""
    temp_settings.write_bytes(orjson.dumps({"waTheme": "default"}))

    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == ("1.2.1", "1.2.1", True)


def test_install_from_1_3_0_backfills_previous(temp_settings, not_first_run):
    """``last`` alone means 1.3.0 — the release that introduced it."""
    temp_settings.write_bytes(orjson.dumps({"lastChangelogVersionSeen": "1.3.0"}))

    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == ("1.3.0", "1.3.0", True)


def test_same_version_announces_nothing(temp_settings, not_first_run):
    """A relaunch on the version already seen leaves everything alone."""
    temp_settings.write_bytes(orjson.dumps({
        "lastChangelogVersionSeen": CURRENT,
        "previousLastChangelogVersionSeen": "1.90.0",
    }))
    before = temp_settings.read_bytes()

    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == ("1.90.0", CURRENT, False)
    assert temp_settings.read_bytes() == before


def test_upgrade_shifts_previous_to_the_version_last_seen(temp_settings, not_first_run):
    temp_settings.write_bytes(orjson.dumps({
        "lastChangelogVersionSeen": "1.90.0",
        "previousLastChangelogVersionSeen": "1.80.0",
    }))

    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == ("1.90.0", "1.90.0", True)
    assert stored(temp_settings)["previousLastChangelogVersionSeen"] == "1.90.0"


def test_manual_edit_dropping_last_falls_back_to_previous(temp_settings, not_first_run):
    temp_settings.write_bytes(orjson.dumps({"previousLastChangelogVersionSeen": "1.90.0"}))

    previous, last, show_forced = asgi._resolve_changelog_versions()

    assert (previous, last, show_forced) == ("1.90.0", "1.90.0", True)


def test_unparseable_stored_version_never_forces(temp_settings, not_first_run):
    temp_settings.write_bytes(orjson.dumps({
        "lastChangelogVersionSeen": "not-a-version",
        "previousLastChangelogVersionSeen": "not-a-version",
    }))

    _, _, show_forced = asgi._resolve_changelog_versions()

    assert show_forced is False


def test_first_run_probe_reports_a_missing_database(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_first_run", None)
    monkeypatch.setattr(paths, "get_db_path", lambda: tmp_path / "db" / "data.sqlite")

    assert paths.probe_first_run() is True
    assert paths.is_first_run() is True


def test_first_run_probe_keeps_its_launch_time_verdict(tmp_path, monkeypatch):
    """The database exists a moment later — the answer must not change."""
    db = tmp_path / "data.sqlite"
    monkeypatch.setattr(paths, "_first_run", None)
    monkeypatch.setattr(paths, "get_db_path", lambda: db)
    paths.probe_first_run()

    db.write_bytes(b"")

    assert paths.probe_first_run() is True
    assert paths.is_first_run() is True


def test_first_run_probe_reports_an_existing_database(tmp_path, monkeypatch):
    db = tmp_path / "data.sqlite"
    db.write_bytes(b"")
    monkeypatch.setattr(paths, "_first_run", None)
    monkeypatch.setattr(paths, "get_db_path", lambda: db)

    assert paths.probe_first_run() is False
    assert paths.is_first_run() is False


def test_unprobed_process_is_not_a_first_run(monkeypatch):
    """A caller outside the server startup path cannot tell — assume not."""
    monkeypatch.setattr(paths, "_first_run", None)

    assert paths.is_first_run() is False
