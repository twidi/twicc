#!/usr/bin/env -S uv run
"""
Development process controller for TWICC.

Manages frontend (npm run dev) and backend (uv run ./run.py) processes
as independent background daemons with logging.

Data directory resolution:
    1. In a git worktree: forced to the worktree root (PROJECT_ROOT)
    2. TWICC_DATA_DIR environment variable (if set)
    3. Default: ~/.twicc/

The .env file (ports, password hash, etc.) is read from the data directory.
The backend process receives TWICC_DATA_DIR so it uses the same paths.
"""
import glob
import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DEVCTL_DIR = PROJECT_ROOT / ".devctl"
PIDS_DIR = DEVCTL_DIR / "pids"

# Default ports
DEFAULT_BACKEND_PORT = 3500
DEFAULT_FRONTEND_PORT = 5173

# How long `stop` waits for a process to exit on its own after SIGTERM before
# escalating to SIGKILL, and how long it then waits for the kill to take effect.
# The backend's graceful shutdown (agents, providers, db writer, search index)
# can take a few seconds; we must not return until it has released the data-dir
# lock, or a following `start` races it and loses (see stop() for the rationale).
STOP_GRACE_TIMEOUT = 10.0
STOP_KILL_TIMEOUT = 3.0

# Default data directory (same as twicc.paths)
DEFAULT_DATA_DIR = Path.home() / ".twicc"
TWICC_DATA_DIR_ENV = "TWICC_DATA_DIR"


def purge_claude_code_vars(env: dict) -> None:
    """Remove Claude Code environment variables from *env* in-place."""
    for key in list(env):
        if key.startswith(("CLAUDE_CODE", "CLAUDECODE")):
            del env[key]


# Provider home variables honoured from the .env — the providers' official
# names, mirroring ``twicc.paths.PROVIDER_HOME_KEYS``. They are .env-exclusive:
# an inherited value is purged from the child environment and only the values
# the .env defines are re-added (``provider_home_env``), one layer before the
# backend applies the same rule, so it never even sees an inherited one.
# Design: docs/plans/2026-09-02-provider-home-dirs-design.md.
PROVIDER_HOME_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME")


def purge_provider_home_vars(env: dict) -> None:
    """Remove the provider home variables (PROVIDER_HOME_KEYS) from *env* in-place."""
    for key in PROVIDER_HOME_KEYS:
        env.pop(key, None)


def provider_home_env(env_vars: dict[str, str]) -> dict[str, str]:
    """The provider home keys the .env defines (``env_vars`` from ``load_env_file``)."""
    return {key: env_vars[key] for key in PROVIDER_HOME_KEYS if key in env_vars}


def describe_provider_homes(env_vars: dict[str, str]) -> list[str]:
    """One human line per provider home, mirroring ``twicc.provider_homes.describe_provider_homes``."""
    home = Path.home()
    claude = env_vars.get("CLAUDE_CONFIG_DIR")
    lines = [
        f"Claude Code home: {claude} (CLAUDE_CONFIG_DIR from .env)" if claude
        else f"Claude Code home: {home / '.claude'} (default)"
    ]
    secure = env_vars.get("CLAUDE_SECURESTORAGE_CONFIG_DIR")
    if secure == "":
        lines.append(f"Claude Code credentials: {home / '.claude'} (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)")
    elif secure:
        lines.append(f"Claude Code credentials: {secure} (CLAUDE_SECURESTORAGE_CONFIG_DIR from .env)")
    codex = env_vars.get("CODEX_HOME")
    lines.append(
        f"Codex home: {codex} (CODEX_HOME from .env)" if codex
        else f"Codex home: {home / '.codex'} (default)"
    )
    return lines


def provider_home_line_warnings() -> list[str]:
    """Warn about .env lines for a provider home key that ``load_env_file`` (a plain
    ``KEY=VALUE`` parser) and the backend's python-dotenv would read differently:
    an ``export`` prefix, ``${VAR}`` interpolation, an inline ``#`` comment."""
    warnings: list[str] = []
    if not ENV_FILE.exists():
        return warnings
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        is_export = line.startswith("export ")
        body = line[len("export "):] if is_export else line
        key, _, value = body.partition("=")
        key = key.strip()
        if key not in PROVIDER_HOME_KEYS:
            continue
        problems = []
        if is_export:
            problems.append("'export ' prefix")
        if "${" in value:
            problems.append("'${...}' interpolation")
        if "#" in value:
            problems.append("inline '#' comment")
        if problems:
            warnings.append(
                f"{key} in {ENV_FILE}: write it as a plain KEY=VALUE line "
                f"({', '.join(problems)}: devctl and the backend would read different values)"
            )
    return warnings


def provider_home_hints(env_vars: dict[str, str]) -> list[str]:
    """Hint when a configured provider home looks unused (nothing logged in there yet).

    Never an error, printed at every start until one of the files appears.
    Codex: none of ``auth.json`` / ``config.toml`` / ``sessions/`` (Codex fills
    ``tmp/`` on its very first run, so "directory empty" is no signal; a
    keyring-mode login implies a ``config.toml``). Claude: none of
    ``.credentials.json`` / ``settings.json`` / ``projects/``, skipped when the
    credentials live elsewhere (``CLAUDE_SECURESTORAGE_CONFIG_DIR`` set).
    """
    hints: list[str] = []
    codex = env_vars.get("CODEX_HOME")
    if codex and not any((Path(codex) / name).exists() for name in ("auth.json", "config.toml", "sessions")):
        hints.append(f'CODEX_HOME={codex} looks unused: run "twicc codex login" from this instance\'s terminal')
    claude = env_vars.get("CLAUDE_CONFIG_DIR")
    if (
        claude
        and "CLAUDE_SECURESTORAGE_CONFIG_DIR" not in env_vars
        and not any((Path(claude) / name).exists() for name in (".credentials.json", "settings.json", "projects"))
    ):
        hints.append(f"CLAUDE_CONFIG_DIR={claude} looks unused: log in from a Claude session of this instance")
    return hints


def print_provider_homes() -> None:
    """Print the provider homes + .env-line warnings + unused-home hints (start/status)."""
    env_vars = load_env_file()
    print("Provider homes:")
    for line in describe_provider_homes(env_vars):
        print(f"  {line}")
    for warning in provider_home_line_warnings():
        print(f"  Warning: {warning}")
    for hint in provider_home_hints(env_vars):
        print(f"  Hint: {hint}")


def tmux_socket_suffix() -> str:
    """Per-data-dir tmux socket suffix — mirrors ``twicc.paths.tmux_socket_suffix``
    standalone (same hash input ``str(data_dir.resolve())``, same default-dir
    comparison; a test pins both to the same value)."""
    data_dir = DATA_DIR.resolve()
    if data_dir == DEFAULT_DATA_DIR.resolve():
        return ""
    return "-" + hashlib.sha256(str(data_dir).encode()).hexdigest()[:8]


def tmux_socket_names() -> tuple[str, str]:
    """``(terminal socket, hybrid socket)`` of this instance (``terminal.py`` constants)."""
    suffix = tmux_socket_suffix()
    return "twicc" + suffix, "twicc-hybrid" + suffix


def kill_tmux() -> None:
    """``tmux -L <name> kill-server`` on both of this instance's sockets.

    A suffixed tmux server outlives a deleted worktree (its terminals, any
    surviving hybrid CLI): this is the cleanup. Refuses on the default data
    dir, and is deliberately NOT part of ``stop`` — hybrid CLIs must survive a
    backend restart by design.
    """
    if tmux_socket_suffix() == "":
        print("Error: kill-tmux refuses to run on the default data dir (~/.twicc): "
              "it would kill the main instance's terminals and hybrid CLIs")
        sys.exit(1)
    tmux = shutil.which("tmux")
    if tmux is None:
        print("tmux is not installed, nothing to kill")
        return
    for socket_name in tmux_socket_names():
        result = subprocess.run([tmux, "-L", socket_name, "kill-server"], capture_output=True)
        outcome = "killed" if result.returncode == 0 else "no server running"
        print(f"  tmux -L {socket_name} kill-server: {outcome}")


def purge_foreign_venvs(env: dict) -> None:
    """Drop other projects' Python environments from *env* in-place.

    devctl inherits the launching shell's environment and hands it to the
    backend and the frontend — and, through the backend, to every agent
    session. When that shell carries another project's ``.venv/bin`` on PATH,
    a command missing from this project's venv resolves there instead: a
    console script then starts that venv's interpreter, with its site-packages
    and its editable install, so a tool run from this repo can silently import
    another checkout's sources.

    Only uv-style project venvs are dropped from PATH, and only foreign ones:
    an entry qualifies when its parent directory is named ``.venv`` and holds
    a ``pyvenv.cfg``. System dirs, ``~/.local/bin``, ``~/.cargo/bin`` and uv's
    own tool venvs (``…/uv/tools/<tool>/bin``) keep their place and their
    order. VIRTUAL_ENV is dropped when it points elsewhere; ``uv run`` sets
    the right one for the backend.

    PYTHONPATH goes altogether: it outranks every venv, and the backend has no
    use for one — the editable install provides ``twicc``.
    """
    own_venv = (PROJECT_ROOT / ".venv").resolve()

    kept_path = []
    for entry in env.get("PATH", "").split(os.pathsep):
        venv = Path(entry).parent
        foreign = (
            bool(entry)
            and venv.name == ".venv"
            and (venv / "pyvenv.cfg").is_file()
            and venv.resolve() != own_venv
        )
        if not foreign:
            kept_path.append(entry)
    env["PATH"] = os.pathsep.join(kept_path)

    virtual_env = env.get("VIRTUAL_ENV")
    if virtual_env and Path(virtual_env).resolve() != own_venv:
        del env["VIRTUAL_ENV"]

    env.pop("PYTHONPATH", None)


def is_git_worktree() -> bool:
    """Detect if we're running inside a git worktree (not the main working tree).

    Compares git-dir (per-worktree) with git-common-dir (shared).
    In the main worktree they resolve to the same path; in a secondary
    worktree, git-dir points to .git/worktrees/<name>.
    """
    try:
        git_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        common_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        git_dir_resolved = os.path.realpath(os.path.join(str(PROJECT_ROOT), git_dir))
        common_dir_resolved = os.path.realpath(os.path.join(str(PROJECT_ROOT), common_dir))
        return git_dir_resolved != common_dir_resolved
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_data_dir() -> Path:
    """Resolve the data directory for this devctl instance.

    Priority:
    1. Git worktree detected → PROJECT_ROOT (always forced, no override)
    2. TWICC_DATA_DIR environment variable (if set)
    3. Default → ~/.twicc/
    """
    if is_git_worktree():
        return PROJECT_ROOT
    env_value = os.environ.get(TWICC_DATA_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value).resolve()
    return DEFAULT_DATA_DIR


# Resolve once at module level
DATA_DIR = get_data_dir()
ENV_FILE = DATA_DIR / ".env"
LOGS_DIR = DATA_DIR / "logs"


def find_available_port(start: int, max_attempts: int = 100) -> int:
    """Find an available port by incrementing from start.

    Tries start, start+1, start+2, ... until a free port is found.
    Raises RuntimeError if no port is available within max_attempts.
    """
    for offset in range(max_attempts):
        port = start + offset
        if port > 65535:
            break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(
        f"Could not find an available port starting from {start} "
        f"(tried {max_attempts} ports)"
    )


def save_ports_to_env(backend_port: int, frontend_port: int) -> None:
    """Append port configuration to the .env file in the data directory."""
    lines_to_add = [
        "",
        "# Ports auto-configured by devctl (worktree mode)",
        f"TWICC_PORT={backend_port}",
        f"VITE_PORT={frontend_port}",
        "",
    ]
    with open(ENV_FILE, "a") as f:
        f.write("\n".join(lines_to_add))


# User preference files carried into a worktree's data dir alongside the DB.
# Infra (.env), logs/, and drop-requests/ are deliberately excluded.
SYNCED_CONFIG_FILENAMES = (
    "settings.json",
    "workspaces.json",
    "layouts.json",
    "terminal-config.json",
    "message-snippets.json",
    "seen-tips.json",
    "seen-help.json",
    "providers-status.json",
)
SYNCED_CONFIG_GLOBS = ("*-settings-presets.json",)

# Data-dir subdirectories shared with the main data dir via symlink in worktree
# mode (see link_shared_dirs_from_main): per-session artifacts and scratch live
# under the main data dir so a worktree instance reads/writes the same files.
SHARED_LINK_DIRS = ("artifacts", "scratch")


def synced_config_files(base_dir: Path) -> list[Path]:
    """Resolve the user preference files present at the root of base_dir.

    Fixed config files plus the per-provider *-settings-presets.json bundles.
    Returns only files that actually exist, so the result is usable both as the
    copy source list (from the main data dir) and the clear list (from the
    worktree data dir).
    """
    files = [base_dir / name for name in SYNCED_CONFIG_FILENAMES]
    for pattern in SYNCED_CONFIG_GLOBS:
        files.extend(sorted(base_dir.glob(pattern)))
    return [path for path in files if path.exists()]


def copy_data_from_main() -> bool:
    """Copy the database, search index and user config from the main data directory to the worktree.

    Copies data.sqlite and any WAL/SHM files, the search-index/ directory, the
    project-icons/ directory, the user preference files (settings, workspaces,
    presets, snippets, tips) and the per-install secret-key.
    Only called in worktree mode when the local database doesn't exist yet, so
    everything lands together on first setup. Existing local files are never
    overwritten.

    Returns True if the copy succeeded (or source doesn't exist), False on error.
    """
    source_db = DEFAULT_DATA_DIR / "db" / "data.sqlite"
    target_db_dir = DATA_DIR / "db"
    target_db = target_db_dir / "data.sqlite"

    if target_db.exists():
        return True  # Already have local data

    if not source_db.exists():
        print("  No main database found, starting fresh")
        return True  # No source to copy, Django migrate will create it

    # Copy database
    print(f"  Copying database from {source_db.parent}...", end=" ", flush=True)

    # Ensure target directory exists
    target_db_dir.mkdir(parents=True, exist_ok=True)

    # Copy data.sqlite and any associated files (-wal, -shm)
    source_pattern = str(source_db) + "*"
    copied_files = []
    for source_file in glob.glob(source_pattern):
        filename = os.path.basename(source_file)
        shutil.copy2(source_file, target_db_dir / filename)
        copied_files.append(filename)

    print("OK")
    print(f"    Files: {', '.join(copied_files)}")

    # Copy search index (if it exists in the main data dir)
    source_search = DEFAULT_DATA_DIR / "search-index"
    target_search = DATA_DIR / "search-index"

    if source_search.exists() and not target_search.exists():
        print(f"  Copying search index from {source_search}...", end=" ", flush=True)
        shutil.copytree(source_search, target_search)
        print("OK")

    # Copy project icons. The copied database carries each project's icon state
    # (a manual override names a file under its proj-<hash> bucket), so without
    # the files the worktree serves a 404 for every manual icon. Auto-discovery
    # only ever rebuilds the repo-<hash> buckets, never the overrides.
    source_icons = DEFAULT_DATA_DIR / "project-icons"
    target_icons = DATA_DIR / "project-icons"

    if source_icons.exists() and not target_icons.exists():
        print(f"  Copying project icons from {source_icons}...", end=" ", flush=True)
        shutil.copytree(source_icons, target_icons)
        print("OK")

    # Copy user preference files (settings, workspaces, presets, snippets, tips).
    # Same one-shot timing as the DB — we only reach here on first worktree
    # setup (guarded by the target_db.exists() check above). A file the worktree
    # already has is left untouched, so local divergence is preserved.
    copied_config = []
    for source_file in synced_config_files(DEFAULT_DATA_DIR):
        target_file = DATA_DIR / source_file.name
        if not target_file.exists():
            shutil.copy2(source_file, target_file)
            copied_config.append(source_file.name)
    if copied_config:
        print(f"  Copied config files: {', '.join(copied_config)}")

    # Copy the per-install SECRET_KEY so the Django sessions carried over in
    # the copied database stay valid in the worktree (same signing key).
    source_secret = DEFAULT_DATA_DIR / "secret-key"
    target_secret = DATA_DIR / "secret-key"
    if source_secret.exists() and not target_secret.exists():
        shutil.copy2(source_secret, target_secret)
        print("  Copied secret-key")

    return True


def link_shared_dirs_from_main() -> None:
    """Symlink the artifacts/ and scratch/ dirs to the main data directory.

    In worktree mode, point ``<worktree>/artifacts`` and ``<worktree>/scratch``
    at the main data dir's equivalents so a worktree instance shares the very
    same per-session artifacts and scratch files as the primary instance (a
    session's Artifacts tab then works in the worktree without copying gigabytes
    of screenshots). Paired with ``copy_data_from_main`` — i.e. NOT under
    ``--empty-db``, which keeps the worktree fully isolated.

    Idempotent and conservative: only creates a link when the worktree path is
    absent (never clobbers an existing real dir or link), and ensures the
    main-side target exists first so the link is never dangling.
    """
    if DATA_DIR == DEFAULT_DATA_DIR:
        return  # Not a worktree — main instance owns these dirs directly
    for name in SHARED_LINK_DIRS:
        target = DATA_DIR / name           # <worktree>/artifacts
        # is_symlink() catches a broken link (exists() is False for those).
        if target.exists() or target.is_symlink():
            continue  # Leave an existing real dir or link untouched
        source = DEFAULT_DATA_DIR / name   # ~/.twicc/artifacts
        source.mkdir(parents=True, exist_ok=True)  # never leave the link dangling
        os.symlink(source, target, target_is_directory=True)
        print(f"  Linked {name}/ -> {source}")


def clear_local_data() -> None:
    """Delete the local database, search index and user config in the worktree.

    Removes data.sqlite and any WAL/SHM files, the search-index/ directory, the
    project-icons/ directory, the user preference files (settings, workspaces,
    presets, snippets, tips) and the per-install secret-key, so the next start
    creates a fresh empty database, rebuilds the search index, rediscovers the
    icons, and carries no config or key over from the main data directory.
    """
    # Clear database
    target_db = DATA_DIR / "db" / "data.sqlite"
    if target_db.exists():
        removed_files = []
        for db_file in glob.glob(str(target_db) + "*"):
            os.remove(db_file)
            removed_files.append(os.path.basename(db_file))
        print(f"  Cleared local database: {', '.join(removed_files)}")

    # Clear search index
    target_search = DATA_DIR / "search-index"
    if target_search.exists():
        shutil.rmtree(target_search)
        print("  Cleared local search index")

    # Clear project icons — the fresh database has no manual override left, and
    # auto-discovery rebuilds the repo buckets on the first scan.
    target_icons = DATA_DIR / "project-icons"
    if target_icons.exists():
        shutil.rmtree(target_icons)
        print("  Cleared local project icons")

    # Clear user preference files (settings, workspaces, presets, snippets, tips).
    removed_config = []
    for config_file in synced_config_files(DATA_DIR):
        os.remove(config_file)
        removed_config.append(config_file.name)
    if removed_config:
        print(f"  Cleared config files: {', '.join(removed_config)}")

    # Clear the per-install SECRET_KEY so the isolated instance generates its
    # own instead of sharing the main install's signing key.
    target_secret = DATA_DIR / "secret-key"
    if target_secret.exists():
        os.remove(target_secret)
        print("  Cleared secret-key")

    # Remove shared-dir symlinks (artifacts/, scratch/) so the fresh start gets
    # isolated local dirs. Only unlink a symlink — never delete a real directory
    # or the shared target's contents.
    for name in SHARED_LINK_DIRS:
        target = DATA_DIR / name
        if target.is_symlink():
            target.unlink()
            print(f"  Unlinked {name}/ (was shared with main)")


def load_env_file() -> dict[str, str]:
    """Load environment variables from .env file in the data directory."""
    env_vars = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue
                # Parse KEY=VALUE (strip optional quotes around value)
                if "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    env_vars[key.strip()] = value
    return env_vars


def get_ports(auto_find: bool = False) -> tuple[int, int]:
    """Get backend and frontend ports from .env file or defaults.

    If auto_find is True and we're in a worktree, automatically find
    available ports (incrementing from defaults) and save them to .env.
    This avoids port conflicts when running multiple worktrees.
    """
    env_vars = load_env_file()

    backend_port = DEFAULT_BACKEND_PORT
    frontend_port = DEFAULT_FRONTEND_PORT

    has_backend_port = "TWICC_PORT" in env_vars
    has_frontend_port = "VITE_PORT" in env_vars

    if has_backend_port:
        try:
            backend_port = int(env_vars["TWICC_PORT"])
        except ValueError:
            print(f"Warning: Invalid TWICC_PORT in .env, using default {DEFAULT_BACKEND_PORT}")

    if has_frontend_port:
        try:
            frontend_port = int(env_vars["VITE_PORT"])
        except ValueError:
            print(f"Warning: Invalid VITE_PORT in .env, using default {DEFAULT_FRONTEND_PORT}")

    # In worktree mode, auto-find available ports if not already configured
    if auto_find and is_git_worktree() and (not has_backend_port or not has_frontend_port):
        if not has_backend_port:
            backend_port = find_available_port(DEFAULT_BACKEND_PORT + 1)
        if not has_frontend_port:
            frontend_port = find_available_port(DEFAULT_FRONTEND_PORT + 1)
        save_ports_to_env(backend_port, frontend_port)
        print(f"  Auto-configured ports for worktree: backend={backend_port}, frontend={frontend_port}")
        print(f"  Saved to {ENV_FILE}")

    return backend_port, frontend_port


def get_process_config(backend_port: int, frontend_port: int) -> dict:
    """Build process configuration with the given port settings."""
    env_vars = load_env_file()
    dev_hostname = env_vars.get("TWICC_DEV_HOSTNAME", "")

    frontend_env = {
        "BACKEND_PORT": str(backend_port),
    }
    if dev_hostname:
        frontend_env["DEV_HOSTNAME"] = dev_hostname

    return {
        "front": {
            "name": "Frontend (Vite)",
            "cmd": ["npm", "run", "dev", "--", "--port", str(frontend_port)],
            "cwd": PROJECT_ROOT / "frontend",
            "log": LOGS_DIR / "frontend.log",
            "pid": PIDS_DIR / "frontend.pid",
            "port": frontend_port,
            "env": frontend_env,
        },
        "back": {
            "name": "Backend (Django)",
            "cmd": ["uv", "run", "./run.py"],
            "cwd": PROJECT_ROOT,
            "log": LOGS_DIR / "backend.log",
            "pid": PIDS_DIR / "backend.pid",
            "port": backend_port,
            "env": {
                "TWICC_PORT": str(backend_port),
                TWICC_DATA_DIR_ENV: str(DATA_DIR),
                "TWICC_DEBUG": "1",
                # In worktree mode:
                # - Use a distinct session cookie name to avoid conflicts with the main instance
                #   (browsers share cookies across ports).
                # - Disable cron auto-restart (worktrees are for development, not for running
                #   persistent cron jobs from previous sessions).
                # - Auto-enable every registered provider: at backend boot, if settings.json has
                #   no ``disabledProviders`` key yet, seed it with an empty list so the activation
                #   dialog never opens and the orchestrators start immediately. After that one-shot
                #   seed, toggles from Settings keep working normally.
                # - Skip the Codex marketplace + plugin install when the worktree shares the
                #   default ~/.codex (its config.toml is already managed by the main install —
                #   worktrees must not race on it). A worktree whose .env sets its own
                #   CODEX_HOME installs its own copy of the plugin there; that is the point.
                # - Disable the empty-session-dirs janitor: artifacts/ and scratch/ are symlinks
                #   shared with the main instance, which owns the cleanup (a worktree would
                #   otherwise prune the shared dirs based on its own, partial DB).
                # - The tmux reaper stays ON: the tmux sockets are per data dir (see
                #   tmux_socket_suffix), so a worktree only ever sees its own sessions.
                # - Disable telemetry: dev worktrees are throwaway instances that would each
                #   register as a distinct install and pollute the collected stats.
                # - Never prune old Codex runtimes: ~/.cache/twicc/codex-runtime/ is shared
                #   with the main instance, and a worktree bumping CODEX_VERSION would delete
                #   the version that instance is running on. It still downloads its own.
                **({
                    "TWICC_SESSION_COOKIE": f"sessionid_{backend_port}",
                    "TWICC_NO_CRON_RESTART": "1",
                    "TWICC_AUTO_ENABLE_PROVIDERS": "1",
                    **({"TWICC_NO_CODEX_PLUGIN": "1"} if "CODEX_HOME" not in load_env_file() else {}),
                    "TWICC_NO_SESSION_DIRS_CLEANUP": "1",
                    "TWICC_NO_TELEMETRY": "1",
                    "TWICC_NO_CODEX_RUNTIME_CLEANUP": "1",
                } if is_git_worktree() else {}),
            },
        },
    }


def build_process_env(config: dict) -> dict[str, str]:
    """The environment handed to a child process: the inherited one, purged, plus the config's."""
    proc_env = os.environ.copy()
    # Purge Claude Code environment variables so the backend/frontend processes
    # don't inherit them (e.g., when devctl is launched from within Claude Code).
    # CLAUDE_CODE_ENTRYPOINT in particular causes Claude Code to think it's
    # already running inside an SDK session, preventing interactive use.
    purge_claude_code_vars(proc_env)
    # Provider homes are .env-exclusive (all modes): drop any inherited
    # CLAUDE_CONFIG_DIR / CLAUDE_SECURESTORAGE_CONFIG_DIR / CODEX_HOME, then
    # re-add below only what this data dir's .env defines. A worktree started
    # from an agent session of another instance must never inherit that
    # instance's homes.
    purge_provider_home_vars(proc_env)
    # Drop any other project's virtualenv from the inherited environment, so a
    # command missing from this project's venv can never resolve to another
    # checkout's (see purge_foreign_venvs).
    purge_foreign_venvs(proc_env)
    # In worktree mode, purge inherited TWICC_* variables so the child
    # process only sees values from the worktree's .env (loaded by run.py).
    # Without this, variables like TWICC_PASSWORD_HASH from the parent
    # shell would leak into the backend and override the worktree config.
    if is_git_worktree():
        for key in list(proc_env):
            if key.startswith("TWICC_"):
                del proc_env[key]
    if "env" in config:
        proc_env.update(config["env"])
    proc_env.update(provider_home_env(load_env_file()))
    return proc_env


def ensure_dirs():
    """Create directory structure for PIDs and logs."""
    PIDS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def is_running(proc_key: str, processes: dict) -> tuple[bool, int | None]:
    """Check if a process is running. Returns (is_running, pid)."""
    pid_file = processes[proc_key]["pid"]
    if not pid_file.exists():
        return False, None

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True, pid
    except OSError:
        # Process doesn't exist, clean up stale pid file
        pid_file.unlink()
        return False, None


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* still exists (signal 0 probe, no signal delivered)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_exit(pid: int, timeout: float) -> bool:
    """Poll until *pid* is gone or *timeout* seconds elapse.

    devctl is not the process's parent (a previous invocation detached it via
    start_new_session), so we can't waitpid() it — we probe liveness with
    signal 0. Returns True if the process exited within the timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def verify_port(proc_key: str, log_start_pos: int, processes: dict, timeout: float = 5.0) -> bool:
    """Verify that process started on the expected port by checking NEW log lines only."""
    config = processes[proc_key]
    expected_port = config.get("port")
    if not expected_port:
        return True  # No port check needed

    log_file = config["log"]
    start_time = time.time()

    # Pattern to match the port in logs
    # Frontend: "Local:   http://localhost:5173/"
    # Backend: "Uvicorn running on http://0.0.0.0:3500"
    port_patterns = [
        rf"localhost:{expected_port}",
        rf"0\.0\.0\.0:{expected_port}",
        rf"127\.0\.0\.1:{expected_port}",
    ]

    while time.time() - start_time < timeout:
        if log_file.exists():
            # Only read NEW content since process started
            with open(log_file) as f:
                f.seek(log_start_pos)
                new_content = f.read()

            if new_content:
                for pattern in port_patterns:
                    if re.search(pattern, new_content):
                        return True
                # Check if wrong port was used (Vite fallback)
                wrong_port_match = re.search(r"localhost:(\d+)/", new_content)
                if wrong_port_match:
                    actual_port = int(wrong_port_match.group(1))
                    if actual_port != expected_port:
                        print(f"    WARNING: Started on port {actual_port} instead of {expected_port}!")
                        print(f"    Port {expected_port} may be in use. Check with: lsof -i :{expected_port}")
                        return False
        time.sleep(0.3)

    print(f"    WARNING: Could not verify port {expected_port} (timeout)")
    return False


def npm_install(processes: dict) -> bool:
    """Run npm install in the frontend directory if needed."""
    frontend_dir = processes["front"]["cwd"]
    node_modules = frontend_dir / "node_modules"

    if node_modules.exists():
        return True

    print("  Installing frontend dependencies (npm install)...", end=" ", flush=True)
    result = subprocess.run(
        ["npm", "install"],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("FAILED")
        print(f"    {result.stderr.strip()}")
        return False
    print("OK")
    return True


def start(proc_key: str, processes: dict) -> bool:
    """Start a process as a detached daemon."""
    config = processes[proc_key]
    running, pid = is_running(proc_key, processes)

    if running:
        print(f"  {config['name']}: already running (PID {pid})")
        return True

    ensure_dirs()

    # Ensure frontend dependencies are installed before starting Vite
    if proc_key == "front":
        if not npm_install(processes):
            return False

    # Remember log file position before starting (to only check new lines)
    log_start_pos = 0
    if config["log"].exists():
        log_start_pos = config["log"].stat().st_size

    proc_env = build_process_env(config)

    # Backend: stdout/stderr → DEVNULL (logs go via Python logging FileHandler)
    # Frontend: stdout/stderr → log file (Vite has no Python logger)
    if proc_key == "back":
        stdout_target = subprocess.DEVNULL
        log_file_handle = None
    else:
        log_file_handle = open(config["log"], "a")
        stdout_target = log_file_handle

    # Start detached process
    # stdin must be redirected to DEVNULL to prevent child processes
    # (especially Vite's readline interface for CLI shortcuts) from
    # modifying the parent terminal settings. Without this, killing
    # the process leaves the terminal in a corrupted state (raw mode)
    # because readline doesn't get a chance to restore terminal settings.
    proc = subprocess.Popen(
        config["cmd"],
        cwd=config["cwd"],
        stdin=subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=subprocess.STDOUT,
        env=proc_env,
        start_new_session=True,  # Detach from parent
    )

    # Close the log file handle in the parent process (if opened);
    # the child has its own copy of the file descriptor
    if log_file_handle is not None:
        log_file_handle.close()

    # Save PID
    config["pid"].write_text(str(proc.pid))
    print(f"  {config['name']}: started (PID {proc.pid})")
    print(f"    Logs: {config['log']}")

    # Verify correct port
    expected_port = config.get("port")
    if expected_port:
        print(f"    Verifying port {expected_port}...", end=" ", flush=True)
        if verify_port(proc_key, log_start_pos, processes):
            print("OK")
        else:
            print("FAILED")
            return False

    return True


def stop(proc_key: str, processes: dict) -> bool:
    """Stop a process group and wait for it to actually exit.

    Sends SIGTERM to the whole group, then BLOCKS until the leader PID is gone
    (escalating to SIGKILL if it overruns STOP_GRACE_TIMEOUT). This wait is the
    whole point: the backend holds an exclusive flock on the data dir for the
    entire duration of its graceful shutdown and releases it only once fully
    stopped. Returning early — as a fire-and-forget SIGTERM would — lets the
    `start` that `restart` runs next race the dying process for the lock and
    lose with "Another TwiCC instance is already running". The kernel frees the
    flock on death either way, so a SIGKILL escalation always unblocks start.
    """
    config = processes[proc_key]
    running, pid = is_running(proc_key, processes)

    if not running:
        print(f"  {config['name']}: not running")
        return True

    # Send SIGTERM to the entire process group (negative PID): kills npm AND its
    # node/vite children, or the backend AND its spawn-compute child. Fall back
    # to the single PID if the group signal fails.
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError as e:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            print(f"  {config['name']}: failed to stop - {e}")
            return False

    # Wait for the (lock-holding) leader to actually exit before returning.
    if wait_for_exit(pid, STOP_GRACE_TIMEOUT):
        print(f"  {config['name']}: stopped (was PID {pid})")
        config["pid"].unlink(missing_ok=True)
        return True

    # Grace period exceeded — escalate to SIGKILL so the next start isn't blocked.
    print(f"  {config['name']}: still alive after {STOP_GRACE_TIMEOUT:.0f}s, sending SIGKILL...")
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    if wait_for_exit(pid, STOP_KILL_TIMEOUT):
        print(f"  {config['name']}: killed (was PID {pid})")
        config["pid"].unlink(missing_ok=True)
        return True

    print(f"  {config['name']}: failed to stop (PID {pid} still alive after SIGKILL)")
    return False


def status(processes: dict):
    """Show status of all processes."""
    backend_port = processes["back"]["port"]
    frontend_port = processes["front"]["port"]

    print(f"Data directory: {DATA_DIR}")
    if is_git_worktree():
        print("  (git worktree detected, using project root)")
    elif os.environ.get(TWICC_DATA_DIR_ENV, "").strip():
        print(f"  (from ${TWICC_DATA_DIR_ENV})")
    else:
        print("  (default)")
    print()

    print(f"Port configuration: frontend={frontend_port}, backend={backend_port}")
    if ENV_FILE.exists():
        print(f"  (from {ENV_FILE})")
    else:
        print("  (defaults, no .env file)")
    print()
    print_provider_homes()
    print()
    terminal_socket, hybrid_socket = tmux_socket_names()
    print(f"tmux sockets: terminals -L {terminal_socket}, hybrid CLIs -L {hybrid_socket}")
    print()
    print("Process status:")
    for key, config in processes.items():
        running, pid = is_running(key, processes)
        if running:
            print(f"  {config['name']}: running (PID {pid}) on port {config['port']}")
        else:
            print(f"  {config['name']}: stopped")


def logs(proc_key: str, processes: dict, lines: int = 50):
    """Show last N lines of logs for a process."""
    config = processes[proc_key]
    log_file = config["log"]

    if not log_file.exists():
        print(f"  No logs found for {config['name']}")
        print(f"  Expected at: {log_file}")
        return

    # Read last N lines
    with open(log_file) as f:
        all_lines = f.readlines()

    last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

    print(f"=== {config['name']} logs (last {len(last_lines)} lines) ===")
    print(f"=== Log file: {log_file} ===")
    print()
    for line in last_lines:
        print(line, end="")

    if not last_lines:
        print("  (empty)")


def parse_target(target: str | None, processes: dict) -> list[str]:
    """Parse target argument into list of process keys."""
    if target is None or target == "all":
        return list(processes.keys())
    if target in processes:
        return [target]
    print(f"Error: Unknown target '{target}'. Use: front, back, or all")
    sys.exit(1)


def print_help():
    """Print help message."""
    help_text = """
devctl.py - Development process controller for TWICC

Manages frontend and backend dev servers as background processes with logging.
Processes run independently and survive after the command exits.

USAGE:
    uv run ./devctl.py <command> [target] [options]

COMMANDS:
    start [target]     Start process(es) in background
    stop [target]      Stop running process(es)
    restart [target]   Stop then start process(es)
    status             Show running status, port configuration, provider homes, tmux sockets
    logs <target>      Show recent log output
    kill-tmux          Kill this instance's two tmux servers (terminals + hybrid CLIs);
                       worktrees only, refuses on ~/.twicc. Not part of stop: hybrid
                       CLIs survive a backend restart by design. Run it before
                       deleting a worktree.
    help, --help, -h   Show this help message

TARGETS:
    front              Frontend dev server (npm run dev)
    back               Backend server (uv run ./run.py)
    all                Both frontend and backend (default for start/stop/restart)

OPTIONS:
    --empty-db         Start with an empty database and no copied config
                       instead of copying from the main data directory
                       (worktree mode only)
    --lines=N          Number of log lines to show (default: 50)

DATA DIRECTORY:
    All persistent data (database, logs, config) lives in a data directory:
    1. Git worktree detected → project root (always forced)
    2. $TWICC_DATA_DIR environment variable (if set)
    3. Default → ~/.twicc/

    The .env file is read from the data directory.
    The backend process receives TWICC_DATA_DIR automatically.

PROVIDER HOMES:
    The .env may relocate the provider homes (the providers' own variables,
    absolute paths, plain KEY=VALUE lines — no `export`, no ${VAR}, no
    inline # comment):

        CLAUDE_CONFIG_DIR=/abs/path/claude-home
        CLAUDE_SECURESTORAGE_CONFIG_DIR=   # empty: keep the default credentials
        CODEX_HOME=/abs/path/codex-home

    These keys are .env-exclusive: an inherited value from the launching
    shell is dropped, only the .env's values reach the backend (which
    applies the same rule again). `start` and `status` print the resolved
    homes, a warning for a non-plain line, and a hint when a configured
    home looks unused (log in once from a terminal of this instance).
    A worktree with its own CODEX_HOME installs its own copy of the TwiCC
    plugin there (TWICC_NO_CODEX_PLUGIN is only set without one).

TMUX SOCKETS:
    Each instance runs its terminals and hybrid CLIs on its own tmux
    sockets: `twicc` / `twicc-hybrid` on ~/.twicc, `twicc-<sha8>` /
    `twicc-hybrid-<sha8>` (sha256 of the data dir) elsewhere. `status`
    prints them. `kill-tmux` kills both servers of a worktree instance
    (refused on ~/.twicc); run it before deleting a worktree.

PORT CONFIGURATION:
    Ports are configured via .env file in the data directory.
    If no .env file exists, defaults are used.

    .env file contents:
        TWICC_PORT=3500   # Backend port (default: 3500)
        VITE_PORT=5173    # Frontend port (default: 5173)

    In git worktrees, if ports are not configured in .env, devctl
    automatically finds available ports by incrementing from
    default+1 (3501→3502→3503... and 5174→5175→5176...) and saves
    them to the worktree's .env file on first start.

DEV HOSTNAME:
    If you access the dev server through a custom hostname (e.g. via a
    reverse proxy or tunnel), set it in the .env file:

        TWICC_DEV_HOSTNAME=myhost.example.com

    This adds the hostname to Vite's allowedHosts so it accepts requests
    for that host. Without this, Vite rejects requests from unknown hosts.

DATABASE, SEARCH INDEX & CONFIG (WORKTREE MODE):
    On start/restart in a worktree, devctl automatically copies the
    database, search index, project icons, and user config (settings.json, workspaces.json,
    layouts.json, terminal-config.json, message-snippets.json, seen-tips.json,
    seen-help.json, providers-status.json, and the *-settings-presets.json bundles) from ~/.twicc/ if no local data exists
    yet. It also symlinks artifacts/ and scratch/ to ~/.twicc/ so the worktree
    shares the same per-session artifact and scratch files as the main instance
    (the Artifacts tab then works for sessions copied into the worktree).
    Infra (.env), logs/, and drop-requests/ are never copied.
    Use --empty-db to skip the copy, drop those symlinks, and start fresh
    (isolated, empty data).

EXAMPLES:
    uv run ./devctl.py start           # Start both servers
    uv run ./devctl.py start back      # Start only backend
    uv run ./devctl.py stop front      # Stop frontend
    uv run ./devctl.py restart back    # Restart backend
    uv run ./devctl.py status          # Check what's running and port config
    uv run ./devctl.py logs back       # Show last 50 lines of backend logs
    uv run ./devctl.py logs front --lines=100
    uv run ./devctl.py start --empty-db    # Worktree: start with fresh database
    uv run ./devctl.py kill-tmux       # Worktree: kill its tmux servers before deleting it

FILES:
    <data_dir>/.env               Configuration (ports, password hash)
    <data_dir>/db/data.sqlite     SQLite database
    <data_dir>/search-index/      Tantivy full-text search index
    <data_dir>/project-icons/     Project icon files (repo + per-project)
    <data_dir>/logs/backend.log   Backend application logs
    <data_dir>/logs/frontend.log  Frontend (Vite) process output
    <data_dir>/logs/sdk/          Raw SDK message logs (per session)
    .devctl/pids/                 PID files for running processes (local)
"""
    print(help_text.strip())


def print_access_urls(frontend_port: int) -> None:
    """Print URLs where the application can be accessed."""
    env_vars = load_env_file()
    dev_hostname = env_vars.get("TWICC_DEV_HOSTNAME", "")

    print()
    print(f"  Access: http://localhost:{frontend_port}")
    if dev_hostname:
        print(f"          https://{dev_hostname}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "--help", "-h"):
        print_help()
        sys.exit(0)

    command = sys.argv[1]

    # Parse positional target and flags from remaining args
    target = None
    empty_db = False
    for arg in sys.argv[2:]:
        if arg == "--empty-db":
            empty_db = True
        elif not arg.startswith("--") and target is None:
            target = arg

    # Validate --empty-db: only allowed in worktree mode
    if empty_db:
        if not is_git_worktree():
            print("Error: --empty-db is only supported in git worktree mode")
            sys.exit(1)
        if command not in ("start", "restart"):
            print("Error: --empty-db is only supported with start/restart commands")
            sys.exit(1)

    # Auto-find ports only on start/restart (may write to .env in worktree mode)
    auto_find = command in ("start", "restart")
    backend_port, frontend_port = get_ports(auto_find=auto_find)
    processes = get_process_config(backend_port, frontend_port)

    if command == "start":
        targets = parse_target(target, processes)
        # In worktree mode: copy DB + search index + user config from main (and
        # symlink the shared artifacts/scratch dirs), or clear all if --empty-db
        if is_git_worktree():
            if empty_db:
                clear_local_data()
            else:
                copy_data_from_main()
                link_shared_dirs_from_main()
        print_provider_homes()
        print(f"Starting processes (frontend:{frontend_port}, backend:{backend_port})...")
        for key in targets:
            start(key, processes)
        print_access_urls(frontend_port)

    elif command == "stop":
        targets = parse_target(target, processes)
        print("Stopping processes...")
        for key in targets:
            stop(key, processes)

    elif command == "restart":
        targets = parse_target(target, processes)
        # In worktree mode: copy DB + search index + user config from main (and
        # symlink the shared artifacts/scratch dirs), or clear all if --empty-db
        if is_git_worktree():
            if empty_db:
                clear_local_data()
            else:
                copy_data_from_main()
                link_shared_dirs_from_main()
        print_provider_homes()
        print(f"Restarting processes (frontend:{frontend_port}, backend:{backend_port})...")
        for key in targets:
            stop(key, processes)
            start(key, processes)
        print_access_urls(frontend_port)

    elif command == "status":
        status(processes)

    elif command == "kill-tmux":
        kill_tmux()

    elif command == "logs":
        if target is None:
            print("Error: logs requires a target (front or back)")
            sys.exit(1)
        if target not in processes:
            print(f"Error: Unknown target '{target}'. Use: front or back")
            sys.exit(1)

        # Parse --lines=N
        lines = 50
        for arg in sys.argv[3:]:
            if arg.startswith("--lines="):
                try:
                    lines = int(arg.split("=")[1])
                except ValueError:
                    print("Error: --lines must be a number")
                    sys.exit(1)

        logs(target, processes, lines)

    else:
        print(f"Error: Unknown command '{command}'")
        print("Commands: start, stop, restart, status, logs, kill-tmux")
        sys.exit(1)


if __name__ == "__main__":
    main()
