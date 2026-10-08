"""Protect generated catalogues and remove files unused by running backends."""

import fcntl
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .runtime import _file_lock

logger = logging.getLogger(__name__)
_leases: dict[Path, BinaryIO] = {}
_lease_guard = threading.Lock()


@contextmanager
def catalog_cache(path: Path, *, prefix: str):
    """Serialize file preparation, then retain it and clean unused files of its family.

    Shared leases last for the backend's lifetime, including idle agents that
    can resume or spawn children later. OS locks disappear when the backend exits.
    The directory lock prevents locking a sidecar inode unlinked by cleanup.
    """
    with _lease_guard, _file_lock(path.parent / f".{prefix}.lock"):
        yield
        _retain_catalog(path)
        _cleanup_catalogs(path, prefix)


def _retain_catalog(path: Path) -> None:
    if path not in _leases:
        handle = path.with_suffix(".lease").open("a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_SH)
        except BaseException:
            handle.close()
            raise
        _leases[path] = handle


def _cleanup_catalogs(current: Path, prefix: str) -> None:
    candidates = set(current.parent.glob(f"{prefix}-*.json"))
    candidates.update(path.with_suffix(".json") for path in current.parent.glob(f"{prefix}-*.lease"))
    for path in candidates:
        if path == current:
            continue
        lease_path = path.with_suffix(".lease")
        try:
            with lease_path.open("a+b") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                path.unlink(missing_ok=True)
                lease_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Cannot remove obsolete Codex catalogue %s", path, exc_info=True)
