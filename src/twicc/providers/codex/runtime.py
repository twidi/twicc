"""Download and cache the Codex CLI runtime from GitHub Releases.

OpenAI stopped publishing stable ``openai-codex-cli-bin`` wheels on PyPI after
0.136.0 (the wheel is ~122 MB, above practical PyPI quotas), but every tagged
stable ships the same manylinux/macOS wheels as GitHub Release assets. Since we
can no longer depend on the PyPI package, we download the platform wheel at
launch, extract it into a shared cache, and point the SDK at the extracted
binary via ``CodexConfig(codex_bin=...)``.

The whole ``codex_cli_bin/`` tree is extracted (not just the ``codex`` binary)
so the sibling resources ship too: ``codex-resources/bwrap`` (Linux sandbox
helper) and ``codex-resources/zsh`` are found by ``codex`` relative to its own
path, and ``codex-path/rg`` (ripgrep) is put on PATH by
``twicc.providers.codex.bin.make_codex_config``.

Cache location: ``$XDG_CACHE_HOME/twicc/codex-runtime/<version>/`` (default
``~/.cache/twicc/...``). Shared across the main instance and every worktree,
so the ~300 MB extracted tree is downloaded once. Independent of
``TWICC_DATA_DIR`` on purpose — this is a regenerable runtime cache, not user
data.

Because the cache is shared, a checkout pinned to a newer ``CODEX_VERSION``
prunes the version a concurrently running checkout still uses. Two guards:
``TWICC_NO_CODEX_RUNTIME_CLEANUP=1`` (set by devctl in worktree mode) makes a
checkout download without ever pruning, and provisioning re-checks the store on
every call, so a runtime deleted under a live process is downloaded again
instead of surfacing a missing-binary error.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import platform
import shutil
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

CODEX_VERSION = "0.153.2"
CODEX_RELEASE_TAG = "rust-v0.153.2"
_RELEASE_URL = f"https://github.com/openai/codex/releases/download/{CODEX_RELEASE_TAG}"

# our platform key -> (wheel filename, sha256 of the wheel)
# Recompute the sha256 values on every version bump (see the bump note at the
# bottom of docs/plans/2026-07-09-codex-runtime-download-plan.md).
_WHEELS: dict[str, tuple[str, str]] = {
    "manylinux_2_17_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_x86_64.whl",
        "133fa38a8b0a37abac9c4d1518ce9190dcff568595e953c7725f76feb51e059d",
    ),
    "manylinux_2_17_aarch64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-manylinux_2_17_aarch64.whl",
        "1921d0e42e1df0157dfcae055bd0a768560ab201d6f857b85aee15eae25e37d5",
    ),
    "macosx_11_0_arm64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_11_0_arm64.whl",
        "fb307f93a9343b5a54f08e37c121e8a562577a628f048460a61f8216efa393e5",
    ),
    "macosx_10_9_x86_64": (
        f"openai_codex_cli_bin-{CODEX_VERSION}-py3-none-macosx_10_9_x86_64.whl",
        "cc4cd246b6efcd167ebe4643008afe472aec28fa25cff294261d404cfd91a263",
    ),
}

# Executable bits to restore after extraction (relative to the store dir).
_EXECUTABLES = (
    "codex_cli_bin/bin/codex",
    "codex_cli_bin/bin/codex-code-mode-host",
    "codex_cli_bin/codex-path/rg",
    "codex_cli_bin/codex-resources/bwrap",
    "codex_cli_bin/codex-resources/zsh/bin/zsh",
)


class CodexRuntimeError(RuntimeError):
    """Base error for Codex runtime provisioning."""


class CodexRuntimeUnsupportedPlatform(CodexRuntimeError):
    """The current OS/arch has no published Codex binary."""


class CodexRuntimeIntegrityError(CodexRuntimeError):
    """Downloaded wheel failed sha256 verification."""


# In-process short-circuit for the cleanup scan, once it has run.
_ready_in_process = False

# Set to 1 to keep every cached runtime version, i.e. download but never prune.
# devctl sets it in worktree mode: a worktree pinned to a newer CODEX_VERSION
# shares the cache with the main instance and would otherwise delete the version
# that instance is running on.
_NO_CLEANUP_ENV = "TWICC_NO_CODEX_RUNTIME_CLEANUP"


def _platform_tag() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "manylinux_2_17_x86_64"
        if machine in ("aarch64", "arm64"):
            return "manylinux_2_17_aarch64"
    elif system == "Darwin":
        if machine == "arm64":
            return "macosx_11_0_arm64"
        if machine == "x86_64":
            return "macosx_10_9_x86_64"
    raise CodexRuntimeUnsupportedPlatform(
        f"No Codex binary for system={system!r} machine={machine!r}. "
        f"Supported: {sorted(_WHEELS)}."
    )


def _cache_root() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "twicc" / "codex-runtime"


def _store_dir() -> Path:
    return _cache_root() / CODEX_VERSION


def _version_key(value: str) -> tuple[int, ...] | None:
    """Return a comparable key for the stable numeric Codex versions we cache."""
    parts = value.split(".")
    if not parts or any(not part.isdecimal() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _ready_marker() -> Path:
    return _store_dir() / ".ready"


def codex_binary_path() -> Path:
    return _store_dir() / "codex_cli_bin" / "bin" / "codex"


def codex_path_dir() -> Path:
    """Directory holding ``rg`` — to prepend to PATH (SDK does this itself only
    when codex_bin is auto-resolved, which is never our case)."""
    return _store_dir() / "codex_cli_bin" / "codex-path"


def is_runtime_ready() -> bool:
    return _ready_marker().is_file() and codex_binary_path().is_file()


@contextmanager
def _file_lock(path: Path):
    """Inter-process exclusive lock (POSIX flock). TwiCC is Linux/macOS only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = open(path, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading Codex runtime %s from %s", CODEX_VERSION, url)
    with urllib.request.urlopen(url) as resp:  # follows the GitHub 302 redirect
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        next_pct = 10
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct >= next_pct:
                        logger.info(
                            "Codex runtime download: %d%% (%d/%d MB)",
                            pct,
                            downloaded // (1024 * 1024),
                            total // (1024 * 1024),
                        )
                        next_pct += 10
    logger.info("Codex runtime download complete (%d bytes)", downloaded)


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    if got != expected:
        path.unlink(missing_ok=True)
        raise CodexRuntimeIntegrityError(
            f"sha256 mismatch for {path.name}: expected {expected}, got {got}"
        )


def _cleanup_previous_runtimes() -> None:
    """Remove cached runtime directories older than the bundled version.

    Each old version's download lock is held while deleting its directory, so
    cleanup cannot race an extraction of that version in another TwiCC
    process. Lock files themselves are tiny and deliberately retained: unlinking
    a lock that another process still has open would break ``flock`` mutual
    exclusion by allowing a second inode to be created at the same path.

    Cleanup is best-effort because a cache permission issue must not make an
    otherwise ready Codex runtime unusable. It is skipped entirely when
    ``TWICC_NO_CODEX_RUNTIME_CLEANUP`` is set.
    """
    if os.environ.get(_NO_CLEANUP_ENV, "").strip().lower() in ("1", "true", "yes"):
        logger.info("Codex runtime cleanup disabled (%s is set)", _NO_CLEANUP_ENV)
        return

    cache_root = _cache_root()
    current_key = _version_key(CODEX_VERSION)
    if current_key is None or not cache_root.is_dir():
        return

    try:
        entries = list(cache_root.iterdir())
    except OSError:
        logger.warning(
            "Could not inspect the Codex runtime cache for old versions", exc_info=True
        )
        return

    for entry in entries:
        version_key = _version_key(entry.name)
        if (
            version_key is None
            or version_key >= current_key
            or entry.is_symlink()
            or not entry.is_dir()
        ):
            continue
        try:
            with _file_lock(cache_root / f"{entry.name}.lock"):
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                    logger.info(
                        "Removed old Codex runtime %s from %s", entry.name, entry
                    )
        except OSError:
            logger.warning(
                "Could not remove old Codex runtime at %s", entry, exc_info=True
            )


def _download_and_extract() -> None:
    tag = _platform_tag()
    wheel_name, expected_sha = _WHEELS[tag]
    url = f"{_RELEASE_URL}/{wheel_name}"

    cache_root = _cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)

    with _file_lock(cache_root / f"{CODEX_VERSION}.lock"):
        if is_runtime_ready():
            return

        tmp_whl = cache_root / f"{wheel_name}.part"
        tmp_extract = cache_root / f"{CODEX_VERSION}.tmp"
        store = _store_dir()

        _download(url, tmp_whl)
        _verify_sha256(tmp_whl, expected_sha)

        shutil.rmtree(tmp_extract, ignore_errors=True)
        with zipfile.ZipFile(tmp_whl) as zf:
            zf.extractall(tmp_extract)
        tmp_whl.unlink(missing_ok=True)

        for rel in _EXECUTABLES:
            member = tmp_extract / rel
            if member.is_file():
                member.chmod(0o755)

        # Atomic-ish swap: remove any stale store, move the fresh tree in,
        # then write the .ready marker last so a crash mid-swap is never
        # mistaken for a ready runtime.
        shutil.rmtree(store, ignore_errors=True)
        tmp_extract.rename(store)
        _ready_marker().write_text(f"{CODEX_VERSION}\n{tag}\n", encoding="utf-8")
        logger.info("Codex runtime %s ready at %s", CODEX_VERSION, store)


def ensure_codex_runtime_sync() -> Path:
    """Ensure the runtime is present on disk; download+extract if missing.

    Blocking. Idempotent. Safe across threads and processes. Returns the store
    directory. Callers in an async context must use :func:`ensure_codex_runtime`
    instead so the download runs off the event loop.

    The store is re-checked on every call (two ``stat`` calls) rather than
    short-circuited by ``_ready_in_process``: another checkout sharing the cache
    can prune our version while this process runs, and a re-download is a much
    better outcome than handing out a path to a deleted binary. Only the cleanup
    scan is short-circuited.
    """
    global _ready_in_process
    if not is_runtime_ready():
        _download_and_extract()
        _ready_in_process = False
    if not _ready_in_process:
        _cleanup_previous_runtimes()
    _ready_in_process = True
    return _store_dir()


async def ensure_codex_runtime() -> Path:
    """Async wrapper: run the (blocking) provisioning in a worker thread."""
    return await asyncio.to_thread(ensure_codex_runtime_sync)
