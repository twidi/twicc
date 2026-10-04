"""
Shared pieces of the hermetic LLM calls (see docs/plans/2026-10-03-hermetic-llm-calls-design.md).

A hermetic call is a short, non-session model call: one prompt in, one text out,
no tool, no user interaction. This module holds what both providers share: the
two error types and the neutral working directory.
"""
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class HermeticConfigError(Exception):
    """The hermetic configuration cannot be built or started.

    ``reason`` is a stable code for the logs: ``catalog``, ``start``, ``cwd``
    or ``mcp-config``. Creating the error logs the one ``warning`` line of the
    failure (spec §5.6/§7); the prompt is never logged.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        logger.warning("hermetic call failed: %s (%s)", reason, message)


class HermeticGuardViolation(Exception):
    """The provider reported a state, or produced an item, that is not allowed.

    Creating the violation logs the one ``warning`` line of the failure, with the stable code ``guard``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        logger.warning("hermetic call failed: guard (%s)", reason)


_logged_directories: set[str] = set()


def _dir_name() -> str:
    return f"hermetic-llm-{os.getuid()}"


def hermetic_cwd(base: Path | None = None) -> Path:
    """Return the shared neutral working directory, creating it when missing.

    Both providers disclose the working directory path to the model and read
    instruction files from it and from its parents, so the directory must be
    empty and its path must carry no information (no product name). It is never
    purged: a stray file there is a signal, not litter.

    ``base`` is the parent directory (default: the system temporary directory);
    it exists for tests and for the diagnostic.
    """
    if sys.platform == "win32":
        raise HermeticConfigError("cwd", "Hermetic calls need a POSIX system (ownership and mode checks)")
    parent = Path(base) if base is not None else Path(tempfile.gettempdir())
    path = parent / _dir_name()
    if "twicc" in str(path).lower() or "twicc" in os.path.realpath(parent).lower():
        raise HermeticConfigError(
            "cwd",
            f"The neutral directory path {path} contains 'twicc'; the path is shown to the model. "
            "Set TMPDIR to a directory whose path does not contain it.",
        )
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(path)
    except OSError as exc:
        raise HermeticConfigError("cwd", f"Cannot create or inspect {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise HermeticConfigError("cwd", f"{path} is a symlink; remove it.")
    if not stat.S_ISDIR(info.st_mode):
        raise HermeticConfigError("cwd", f"{path} is not a directory; remove it.")
    if info.st_uid != os.getuid():
        raise HermeticConfigError(
            "cwd", f"{path} is owned by another user; set TMPDIR to a private directory.",
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise HermeticConfigError("cwd", f"{path} must have mode 0700 (found {stat.S_IMODE(info.st_mode):04o}).")
    try:
        entries = sorted(entry.name for entry in path.iterdir())
    except OSError as exc:
        raise HermeticConfigError("cwd", f"Cannot list {path}: {exc}") from exc
    if entries:
        raise HermeticConfigError(
            "cwd", f"{path} must be empty but contains {entries[0]!r}; remove it (the directory is never purged).",
        )
    resolved = Path(os.path.realpath(path))
    if str(resolved) not in _logged_directories:
        _logged_directories.add(str(resolved))
        logger.info("Hermetic calls use the neutral directory %s", resolved)
    return resolved
