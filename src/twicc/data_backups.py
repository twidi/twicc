"""Versioned backups of the user-data files at the root of the data dir.

Each covered file (synced settings, workspaces, layouts, presets, tokens,
``.env``, ``secret-key``…) gets a dated copy under
``<data_dir>/backups/<file name>/`` each time its content really changes. The
comparison ignores the values that change on writes without carrying user data,
so a write that only bumps a counter stores nothing. Restoring is a manual copy.

Synchronous and Django-free: called at startup before the backend writes
anything, by the data dir watcher, and at shutdown. A backup is a safety net,
never a dependency: no function here raises.

Design: docs/plans/2026-10-17-data-dir-file-backups-design.md.
"""

import fnmatch
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson

from twicc.paths import get_backups_dir, get_data_dir

logger = logging.getLogger(__name__)

COVERED_FILENAMES: frozenset[str] = frozenset({
    "settings.json",
    "workspaces.json",
    "layouts.json",
    "message-snippets.json",
    "terminal-config.json",
    "seen-tips.json",
    "seen-help.json",
    "api-tokens.json",
    ".env",
    "secret-key",
})
COVERED_PATTERN = "*-settings-presets.json"

# Retention, per file: the newest versions, plus the newest version of each of
# the most recent local calendar days that have one (days without a change do
# not count), so a burst of writes never erases the history of earlier days.
KEEP_NEWEST = 30
KEEP_DAYS = 30

# A version is named after the UTC instant it was taken: no dot in the
# timestamp, so a version of a file without extension has no ``Path.suffix``.
_TIME_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"
_JSON_SUFFIX = ".json"

# Serializes every version write of the process (startup scan, watcher worker
# threads, final scan): the free-name check and the write cannot race.
_lock = threading.Lock()


def is_covered(name: str) -> bool:
    """Whether ``name`` (a data dir root file name) is backed up."""
    return name in COVERED_FILENAMES or fnmatch.fnmatchcase(name, COVERED_PATTERN)


def version_folder(name: str) -> Path:
    """The folder holding the versions of ``name`` (leading dot dropped: ``.env`` → ``env``)."""
    return get_backups_dir() / name.lstrip(".")


def normalize(name: str, raw: bytes) -> bytes:
    """The comparison key of a file content: what must differ for a new version.

    JSON files are compared as their sorted dump, without the write-churn values
    (``_version`` of ``settings.json``, each token's ``last_used_at`` in
    ``api-tokens.json``) when the shape matches. Anything else (not JSON, invalid
    JSON) is compared as raw bytes.
    """
    if not name.endswith(_JSON_SUFFIX):
        return raw
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return raw
    if name == "settings.json" and isinstance(data, dict):
        data.pop("_version", None)
    elif name == "api-tokens.json" and isinstance(data, dict) and isinstance(data.get("tokens"), list):
        data["tokens"] = [
            {key: value for key, value in token.items() if key != "last_used_at"} if isinstance(token, dict)
            else token
            for token in data["tokens"]
        ]
    return orjson.dumps(data, option=orjson.OPT_SORT_KEYS)


def version_time(path: Path) -> datetime:
    """The aware UTC instant a version was taken, read from its name (``ValueError`` if not a version)."""
    stem = path.name.removesuffix(_JSON_SUFFIX)
    return datetime.strptime(stem, _TIME_FORMAT).replace(tzinfo=UTC)


def list_versions(name: str) -> list[Path]:
    """The versions of ``name``, oldest first (anything else in the folder is ignored)."""
    folder = version_folder(name)
    if not folder.is_dir():
        return []
    versions = []
    for entry in folder.iterdir():
        try:
            version_time(entry)
        except ValueError:
            continue
        versions.append(entry)
    return sorted(versions, key=lambda path: path.name)


def take_version(name: str, *, now: datetime | None = None) -> bool:
    """Store a version of the data dir root file ``name`` if its content changed.

    Returns whether a version was stored. A missing file stores nothing and
    removes nothing. Never raises.
    """
    try:
        with _lock:
            return _take_version_locked(name, now or datetime.now(UTC))
    except Exception:
        logger.exception("Data backup of %s failed", name)
        return False


def _take_version_locked(name: str, now: datetime) -> bool:
    source = get_data_dir() / name
    if not source.is_file():
        return False
    raw = source.read_bytes()
    versions = list_versions(name)
    if versions and normalize(name, versions[-1].read_bytes()) == normalize(name, raw):
        return False

    folder = version_folder(name)
    for directory in (folder.parent, folder):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)

    suffix = _JSON_SUFFIX if name.endswith(_JSON_SUFFIX) else ""
    instant = now.astimezone(UTC)
    target = folder / (instant.strftime(_TIME_FORMAT) + suffix)
    while target.exists():
        instant += timedelta(microseconds=1)
        target = folder / (instant.strftime(_TIME_FORMAT) + suffix)

    fd, tmp_path = tempfile.mkstemp(dir=folder, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(raw)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.debug("Backed up %s (%s)", name, target.name)
    prune(name)
    return True


def prune(name: str) -> None:
    """Delete the versions of ``name`` that the retention does not keep.

    Kept: the ``KEEP_NEWEST`` newest versions, and the newest version of each of
    the ``KEEP_DAYS`` most recent local calendar days that have a version.
    Never raises.
    """
    try:
        versions = list_versions(name)
        keep = set(versions[-KEEP_NEWEST:])
        newest_by_day: dict = {}
        for version in versions:  # oldest first: the last one of a day wins
            newest_by_day[version_time(version).astimezone().date()] = version
        keep.update(newest_by_day[day] for day in sorted(newest_by_day)[-KEEP_DAYS:])
        for version in versions:
            if version not in keep:
                try:
                    version.unlink()
                except OSError:
                    logger.exception("Cannot delete the data backup %s", version)
    except Exception:
        logger.exception("Data backups pruning of %s failed", name)


def scan_all(*, now: datetime | None = None, reason: str = "startup scan") -> int:
    """Take a version of every covered file of the data dir root that changed.

    Returns the number of versions stored. Never raises.
    """
    try:
        names = sorted(entry.name for entry in os.scandir(get_data_dir())
                       if is_covered(entry.name) and entry.is_file())
    except Exception:
        logger.exception("Data backups %s failed", reason)
        return 0
    stored = sum(take_version(name, now=now) for name in names)
    if stored:
        logger.info("Data backups: %d new version(s) at %s", stored, reason)
    return stored
