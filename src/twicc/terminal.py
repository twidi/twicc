"""
Raw ASGI WebSocket handler for interactive terminal sessions.

Bypasses Django Channels' AsyncWebsocketConsumer entirely to eliminate
the channel layer overhead on every message. The ASGI send/receive
callables are used directly, giving near-native WebSocket performance.

Provides a PTY-backed terminal per session, communicating over a dedicated
WebSocket endpoint (`/ws/terminal/<session_id>/`).

Protocol:
  Client → Server (JSON text frames):
    { "type": "input", "data": "ls -la\\n" }       — keyboard input
    { "type": "resize", "cols": 120, "rows": 30 }  — terminal resize
    { "type": "list_windows" }                      — list tmux windows + presets
    { "type": "create_window", "name": "build" }    — create named window
    { "type": "create_window", "name": "dev",        — create from preset
      "preset_cwd": "/path", "command": "npm run dev" }
    { "type": "select_window", "name": "build" }    — switch to window

  Server → Client:
    Plain text frames — raw PTY output (no JSON wrapping for performance).
    JSON text frames (when type field present) — control responses:
      { "type": "windows", "windows": [...] }       — window list
      { "type": "window_changed", "name": "..." }   — window switched
      { "type": "pane_state", "alternate_on": true }  — pane screen mode
"""

import asyncio
import fcntl
import json
import logging
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
from typing import NamedTuple
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.env import purge_claude_code_vars

logger = logging.getLogger(__name__)

# WebSocket close code for authentication failure (same as UpdatesConsumer).
WS_CLOSE_AUTH_FAILURE = 4001

# Default terminal dimensions
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

# Read buffer size (20 KiB — large enough to avoid excessive callbacks,
# small enough to keep latency low)
READ_BUFFER_SIZE = 20480

# tmux socket name — isolates twicc sessions from user's own tmux
TMUX_SOCKET_NAME = "twicc"


class SessionContext(NamedTuple):
    """All directory context needed for terminal startup and preset resolution."""
    cwd: str                   # Resolved working directory for PTY spawn
    archived: bool
    project_dir: str | None    # project.directory (if exists on disk)
    git_dir: str | None        # session.git_directory or project.git_root (if exists on disk)
    session_cwd: str | None    # session.cwd (raw from JSONL, if exists on disk)
    project_id: str | None     # project.id (for custom preset file resolution)


@sync_to_async
def get_session_context(session_id: str) -> SessionContext:
    """Resolve the full directory context for a terminal session.

    Returns a SessionContext with:
    - cwd: the resolved working directory for spawning the PTY (same priority
      logic as before: git_directory > project.directory > project.git_root > ~)
    - archived: whether the session is archived
    - project_dir, git_dir, session_cwd: raw directory fields for preset resolution
    """
    from twicc.core.models import Session

    home = os.path.expanduser("~")

    try:
        session = Session.objects.select_related("project").get(id=session_id)
    except Session.DoesNotExist:
        return SessionContext(cwd=home, archived=False, project_dir=None, git_dir=None, session_cwd=None, project_id=None)

    project = session.project

    # Resolve project_dir (validated existence)
    project_dir = project.directory if project and project.directory and os.path.isdir(project.directory) else None

    # Resolve git_dir: prefer session.git_directory, then project.git_root
    git_dir = None
    if session.git_directory and os.path.isdir(session.git_directory):
        git_dir = session.git_directory
    elif project and project.git_root and os.path.isdir(project.git_root):
        git_dir = project.git_root

    # Resolve session_cwd (the actual CWD from the JSONL)
    session_cwd = session.cwd if session.cwd and os.path.isdir(session.cwd) else None

    # Resolve cwd for PTY spawn (same priority logic as before)
    if session.git_directory:
        candidates = [
            session.git_directory,
            project.directory if project else None,
        ]
    else:
        candidates = [
            project.directory if project else None,
            project.git_root if project else None,
        ]

    cwd = home
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            cwd = candidate
            break

    return SessionContext(
        cwd=cwd,
        archived=session.archived,
        project_dir=project_dir,
        git_dir=git_dir,
        session_cwd=session_cwd,
        project_id=project.id if project else None,
    )


# ── tmux helpers ──────────────────────────────────────────────────────────

_tmux_path: str | None = None
_tmux_checked = False


def get_tmux_path() -> str | None:
    """Return the path to tmux binary, or None if not installed."""
    global _tmux_path, _tmux_checked
    if not _tmux_checked:
        _tmux_path = shutil.which("tmux")
        _tmux_checked = True
    return _tmux_path


def wants_tmux(scope: dict) -> bool:
    """Check if the client requested tmux mode via query parameter."""
    qs = scope.get("query_string", b"").decode("utf-8", errors="replace")
    params = parse_qs(qs)
    return params.get("tmux", ["0"])[0] == "1"


def tmux_session_name(session_id: str) -> str:
    """Deterministic tmux session name from a twicc session ID.

    tmux session names cannot contain dots or colons, so we replace them.
    """
    return "twicc-" + session_id.replace(".", "_").replace(":", "_")


# ── PTY helpers (pure functions, no class needed) ─────────────────────────

def set_winsize(fd: int, cols: int, rows: int) -> None:
    """Set the terminal window size on a file descriptor."""
    # struct winsize { unsigned short ws_row, ws_col, ws_xpixel, ws_ypixel; }
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def spawn_pty(cwd: str) -> tuple[int, int]:
    """Fork a PTY with a shell process in the given directory.

    Uses pty.fork() which handles setsid, slave PTY setup, and
    stdin/stdout/stderr redirection in the child process.

    Returns (child_pid, master_fd).
    """
    child_pid, master_fd = pty.fork()

    if child_pid == 0:
        # ── Child process ──
        os.chdir(cwd)

        # Determine the user's shell
        shell = os.environ.get("SHELL", "/bin/bash")

        # Set TERM for proper terminal emulation
        os.environ["TERM"] = "xterm-256color"

        # Remove Claude Code env vars that may have been set by the SDK in the
        # backend process. Without this, Claude Code launched from this terminal
        # would think it's already inside an SDK session.
        purge_claude_code_vars(os.environ)

        # Exec the shell as a login shell (prefix argv[0] with -)
        os.execvp(shell, [f"-{os.path.basename(shell)}"])
        # execvp does not return; if it fails, child exits
        os._exit(1)

    # ── Parent process ──
    # Set initial window size
    set_winsize(master_fd, DEFAULT_COLS, DEFAULT_ROWS)

    # Make the fd non-blocking for event-driven reading
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return child_pid, master_fd


def spawn_tmux_pty(cwd: str, session_id: str) -> tuple[int, int]:
    """Fork a PTY running tmux, attaching to or creating a named session.

    Uses ``tmux -L twicc -f /dev/null new-session -A -s <name>`` which:
    - ``-L twicc``: use a dedicated socket (isolation from user's tmux)
    - ``-f /dev/null``: ignore user's tmux.conf
    - ``new-session -A``: attach if session exists, create if not
    - ``-s <name>``: deterministic session name

    Returns (child_pid, master_fd) — same interface as spawn_pty.
    The child_pid is the tmux *client* process, not the server.
    Killing it just detaches; the tmux session keeps running.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        raise FileNotFoundError("tmux is not installed")

    name = tmux_session_name(session_id)
    child_pid, master_fd = pty.fork()

    if child_pid == 0:
        # ── Child process ──
        os.chdir(cwd)
        os.environ["TERM"] = "xterm-256color"
        # Unset TMUX to avoid nesting issues if the server itself runs in tmux
        os.environ.pop("TMUX", None)
        # Remove Claude Code env vars (same reason as in spawn_pty)
        purge_claude_code_vars(os.environ)

        os.execvp(tmux_path, [
            "tmux",
            "-L", TMUX_SOCKET_NAME,
            "-f", "/dev/null",
            "new-session", "-A",
            "-s", name,
        ])
        os._exit(1)

    # ── Parent process ──
    set_winsize(master_fd, DEFAULT_COLS, DEFAULT_ROWS)

    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return child_pid, master_fd


def cleanup_pty(master_fd: int | None, child_pid: int | None) -> None:
    """Release PTY resources: remove reader, close fd, kill child."""
    loop = asyncio.get_event_loop()

    if master_fd is not None:
        try:
            loop.remove_reader(master_fd)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    if child_pid is not None:
        try:
            os.kill(child_pid, signal.SIGTERM)
        except OSError:
            pass
        # Non-blocking waitpid to reap zombie without blocking the event loop
        try:
            os.waitpid(child_pid, os.WNOHANG)
        except ChildProcessError:
            pass


def tmux_session_exists(session_id: str) -> bool:
    """Check if a tmux session exists for the given twicc session ID."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "has-session", "-t", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def kill_tmux_session(session_id: str) -> bool:
    """Kill the tmux session for the given twicc session ID.

    Called when a session is archived to clean up persistent tmux sessions.
    Returns True if the session was killed, False if it didn't exist or
    tmux is not installed.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "kill-session", "-t", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("Failed to kill tmux session %s", name)
        return False


def tmux_set_option(session_id: str, option: str, value: str) -> bool:
    """Set a tmux session option.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "set-option", "-t", name, option, value],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_pane_is_alternate(session_id: str) -> bool:
    """Check if the active pane of the active window is in alternate screen mode.

    Alternate screen is used by full-screen apps (less, vim, htop, etc.).
    This allows the frontend to choose the right scroll strategy:
    - Alternate on: send arrow keys (the app handles scrolling)
    - Alternate off: send mouse wheel (tmux copy-mode scrolls the buffer)
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "display-message",
             "-t", name, "-p", "#{alternate_on}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "1"
    except (subprocess.TimeoutExpired, OSError):
        return False



# ── tmux window management ───────────────────────────────────────────────


def tmux_list_windows(session_id: str) -> list[dict[str, object]]:
    """List all windows in the tmux session for the given twicc session ID.

    Returns a list of dicts: [{"name": "main", "active": True}, ...]
    Returns an empty list if the session doesn't exist or tmux is not installed.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return []

    name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "list-windows",
             "-t", name, "-F", "#{window_name}\t#{window_active}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                windows.append({"name": parts[0], "active": parts[1] == "1"})
        return windows
    except (subprocess.TimeoutExpired, OSError):
        return []


def tmux_create_window(session_id: str, window_name: str, cwd: str | None = None) -> bool:
    """Create a new window in the tmux session with the given name.

    Args:
        session_id: The Claude session ID
        window_name: Display name for the new tmux window
        cwd: Working directory for the new window. If None, inherits tmux default.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    cmd = [tmux_path, "-L", TMUX_SOCKET_NAME, "new-window",
           "-t", session_name, "-n", window_name]
    if cwd:
        cmd.extend(["-c", cwd])
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_select_window(session_id: str, window_name: str) -> bool:
    """Switch the active window in the tmux session.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "select-window",
             "-t", f"{session_name}:{window_name}"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_rename_window(session_id: str, target: str, new_name: str) -> bool:
    """Rename a window in the tmux session.

    target can be a window index (e.g., "0") or name.
    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "rename-window",
             "-t", f"{session_name}:{target}", new_name],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def tmux_send_keys(session_id: str, window_name: str, keys: str) -> bool:
    """Send keys to a specific tmux window.

    Used to execute a command in a newly created preset window.
    Appends Enter to simulate pressing the Enter key.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    session_name = tmux_session_name(session_id)
    try:
        result = subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME, "send-keys",
             "-t", f"{session_name}:{window_name}", keys, "Enter"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def load_tmux_presets(project_dir: str) -> list[dict]:
    """Load tmux shell presets from a .twicc-tmux.json file in the given directory.

    Convenience wrapper around load_tmux_presets_from_file() for the standard
    `.twicc-tmux.json` filename.
    """
    return load_tmux_presets_from_file(os.path.join(project_dir, ".twicc-tmux.json"))


VALID_RELATIVE_TO = {"preset_dir", "project_dir", "git_dir", "session_cwd"}


def load_tmux_presets_from_file(file_path: str) -> list[dict]:
    """Load tmux shell presets from any JSON file.

    The file must contain an array of preset objects. Each preset
    has a name (required), optional cwd (relative or absolute), optional
    command, and optional relative_to (preset_dir|project_dir|git_dir|session_cwd).

    Returns raw preset dicts without resolving cwd — resolution happens in
    resolve_preset_sources() where the session context is available.

    Returns [] on missing file / parse error.
    """
    try:
        with open(file_path, "r") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    preset_dir = os.path.dirname(os.path.abspath(file_path))
    presets = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        preset: dict = {"name": str(entry["name"]), "preset_dir": preset_dir}
        if "command" in entry:
            preset["command"] = str(entry["command"])
        if "cwd" in entry:
            preset["raw_cwd"] = str(entry["cwd"])
        relative_to = str(entry.get("relative_to", "preset_dir"))
        if relative_to not in VALID_RELATIVE_TO:
            relative_to = "preset_dir"
        preset["relative_to"] = relative_to
        presets.append(preset)
    return presets


def _resolve_preset_cwd(preset: dict, ctx: SessionContext) -> dict:
    """Resolve a preset's cwd using session context and relative_to field.

    Mutates and returns the preset dict, adding 'cwd' (resolved path) and
    optionally 'unavailable' (True if the base directory is not available).
    """
    relative_to = preset.get("relative_to", "preset_dir")
    preset_dir = preset.get("preset_dir", "")

    # Map relative_to to actual base directory
    base_map: dict[str, str | None] = {
        "preset_dir": preset_dir,
        "project_dir": ctx.project_dir,
        "git_dir": ctx.git_dir,
        "session_cwd": ctx.session_cwd,
    }
    base_dir = base_map.get(relative_to)

    if not base_dir or not os.path.isdir(base_dir):
        # Base not available — mark preset as unavailable
        preset["unavailable"] = True
        preset["cwd"] = None
        preset.pop("raw_cwd", None)
        preset.pop("preset_dir", None)
        return preset

    raw_cwd = preset.pop("raw_cwd", None)
    if raw_cwd:
        if os.path.isabs(raw_cwd):
            preset["cwd"] = raw_cwd
        else:
            preset["cwd"] = os.path.normpath(os.path.join(base_dir, raw_cwd))
    else:
        preset["cwd"] = base_dir

    # Clean up internal fields not needed by the frontend
    preset.pop("preset_dir", None)

    return preset


def get_custom_preset_files(project_id: str) -> list[dict]:
    """Read the list of custom preset file references for a project.

    Returns a list of dicts: [{"name": "My tools", "path": "/path/to/presets.json"}, ...]
    Returns [] if no file exists or on parse error.
    """
    from twicc.paths import get_project_presets_path

    presets_path = get_project_presets_path(project_id)
    try:
        with open(presets_path, "r") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    result = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("name") and entry.get("path"):
            result.append({"name": str(entry["name"]), "path": str(entry["path"])})
    return result


def save_custom_preset_files(project_id: str, entries: list[dict]) -> None:
    """Write the list of custom preset file references for a project."""
    from twicc.paths import get_project_presets_path

    presets_path = get_project_presets_path(project_id)
    presets_path.parent.mkdir(parents=True, exist_ok=True)
    with open(presets_path, "w") as f:
        f.write(json.dumps(entries, indent=2))


def add_custom_preset_file(project_id: str, name: str, path: str) -> list[dict]:
    """Add a custom preset file reference for a project. Returns the updated list."""
    entries = get_custom_preset_files(project_id)
    # Avoid duplicates by path
    if any(e["path"] == path for e in entries):
        return entries
    entries.append({"name": name, "path": path})
    save_custom_preset_files(project_id, entries)
    return entries


def remove_custom_preset_file(project_id: str, path: str) -> list[dict]:
    """Remove a custom preset file reference by path. Returns the updated list."""
    entries = get_custom_preset_files(project_id)
    entries = [e for e in entries if e["path"] != path]
    save_custom_preset_files(project_id, entries)
    return entries


def resolve_preset_sources(ctx: SessionContext, project_id: str | None = None) -> list[dict]:
    """Resolve up to 3 preset sources from session context.

    Returns a list of dicts with {label, directory, presets} for each source
    that has a valid .twicc-tmux.json file. Sources:
    1. Project dir — always checked.
    2. Git dir — checked if different from project dir.
    3. CWD walk — walk parents from session_cwd up to first .twicc-tmux.json,
       bounded by project_dir / git_dir.
    """
    sources: list[dict] = []
    seen_dirs: set[str] = set()
    seen_files: set[str] = set()  # Track loaded file paths for dedup with custom sources
    home = os.path.expanduser("~")

    def _norm(path: str) -> str:
        return os.path.normpath(os.path.realpath(path))

    def _resolve_all(presets: list[dict]) -> list[dict]:
        """Resolve cwd for all presets using the session context."""
        return [_resolve_preset_cwd(p, ctx) for p in presets]

    def _try_add(label: str, directory: str) -> bool:
        """Load presets from directory. Add to sources if file exists. Returns True if added."""
        norm = _norm(directory)
        if norm in seen_dirs:
            return False
        seen_dirs.add(norm)
        presets = load_tmux_presets(directory)
        if presets:
            seen_files.add(_norm(os.path.join(directory, ".twicc-tmux.json")))
            sources.append({"label": label, "directory": directory, "presets": _resolve_all(presets)})
            return True
        return False

    def _shorten(path: str) -> str:
        """Replace $HOME prefix with ~."""
        if path.startswith(home):
            return "~" + path[len(home):]
        return path

    # Source 1: Project directory
    if ctx.project_dir:
        _try_add("Project", ctx.project_dir)

    # Source 2: Git directory (only if different from project dir)
    if ctx.git_dir:
        _try_add("Git root", ctx.git_dir)

    # Source 3: Walk up from session CWD
    if ctx.session_cwd:
        cwd_norm = _norm(ctx.session_cwd)
        if cwd_norm not in seen_dirs:
            current = ctx.session_cwd
            while True:
                norm_current = _norm(current)
                if norm_current in seen_dirs:
                    break  # Already covered by project or git source
                presets = load_tmux_presets(current)
                if presets:
                    seen_files.add(_norm(os.path.join(current, ".twicc-tmux.json")))
                    sources.append({"label": _shorten(current), "directory": current, "presets": _resolve_all(presets)})
                    break  # Stop at first found
                parent = os.path.dirname(current)
                if parent == current:
                    break  # Reached filesystem root
                current = parent

    # Source 4: Custom preset files (user-added per project)
    if project_id:
        for entry in get_custom_preset_files(project_id):
            file_path = entry["path"]
            if not os.path.isfile(file_path):
                continue
            if _norm(file_path) in seen_files:
                continue
            file_presets = load_tmux_presets_from_file(file_path)
            if file_presets:
                sources.append({
                    "label": entry["name"],
                    "directory": os.path.dirname(file_path),
                    "presets": _resolve_all(file_presets),
                    "custom_file": file_path,
                })

    return sources


def _configure_tmux_scroll_bindings() -> None:
    """Configure tmux mouse wheel bindings for proper scroll in all contexts.

    Overrides the default WheelUpPane / WheelDownPane bindings so that:
    - If the pane is already in copy-mode or the app captures the mouse
      (e.g. vim with mouse): pass the mouse event through (send-keys -M).
    - If the pane is in alternate screen mode (less, htop, etc.) WITHOUT
      mouse capture: send arrow keys so the app scrolls natively.
    - Otherwise (shell prompt): enter copy-mode for tmux scrollback.

    These bindings are server-wide (shared by all twicc tmux sessions on
    the same socket), so setting them repeatedly is harmless.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return

    condition = "#{||:#{pane_in_mode},#{mouse_any_flag}}"

    for event, arrow_keys, normal_cmd in [
        ("WheelUpPane", "Up Up Up", "copy-mode -e ; send-keys -M"),
        ("WheelDownPane", "Down Down Down", "send-keys -M"),
    ]:
        alt_branch = (
            f'if-shell -F "#{{alternate_on}}" '
            f'"send-keys {arrow_keys}" '
            f'"{normal_cmd}"'
        )
        subprocess.run(
            [tmux_path, "-L", TMUX_SOCKET_NAME,
             "bind-key", "-T", "root", event,
             "if-shell", "-F", condition, "send-keys -M", alt_branch],
            capture_output=True, timeout=5,
        )



# ── tmux window monitor ──────────────────────────────────────────────────

# Polling interval for detecting external window changes (close, rename, etc.)
_WINDOW_POLL_INTERVAL = 2  # seconds


async def _tmux_window_monitor(session_id: str, send, ctx: SessionContext) -> None:
    """Periodically check for tmux window and pane state changes and push updates.

    Detects when windows are created or destroyed externally (e.g., shell
    exits, user closes a pane) and when the active pane switches between
    normal and alternate screen (e.g., entering/exiting less or vim).
    Sends updated state to the frontend so the tab bar / dropdown and
    scroll behavior stay in sync.
    """
    # Initialize with current state to avoid a duplicate update on first poll
    prev_windows = await asyncio.to_thread(tmux_list_windows, session_id)
    prev_alternate = await asyncio.to_thread(tmux_pane_is_alternate, session_id)
    try:
        while True:
            await asyncio.sleep(_WINDOW_POLL_INTERVAL)
            win_list = await asyncio.to_thread(tmux_list_windows, session_id)
            alternate = await asyncio.to_thread(tmux_pane_is_alternate, session_id)
            if win_list != prev_windows or alternate != prev_alternate:
                prev_windows = win_list
                prev_alternate = alternate
                presets = await asyncio.to_thread(resolve_preset_sources, ctx, ctx.project_id)
                await send({"type": "websocket.send",
                            "text": json.dumps({"type": "windows", "windows": win_list,
                                                "presets": presets,
                                                "alternate_on": alternate})})
    except asyncio.CancelledError:
        return
    except Exception:
        return


# ── Raw ASGI WebSocket application ────────────────────────────────────────

async def terminal_application(scope, receive, send):
    """Raw ASGI WebSocket handler for terminal sessions.

    This bypasses Django Channels' consumer machinery entirely.
    The ASGI send/receive callables are used directly for minimal overhead.

    The scope is populated by Channels' SessionMiddlewareStack and URLRouter,
    so we still get session auth and URL kwargs for free.
    """
    assert scope["type"] == "websocket"

    # Wait for the WebSocket connect message
    message = await receive()
    if message["type"] != "websocket.connect":
        return

    # ── Authentication ────────────────────────────────────────────────
    if settings.TWICC_PASSWORD_HASH:
        session = scope.get("session", {})
        is_authenticated = await sync_to_async(session.get)("authenticated")
        if not is_authenticated:
            logger.warning("Terminal WebSocket rejected: not authenticated")
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": json.dumps({"type": "auth_failure"})})
            await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILURE})
            return

    # ── Resolve working directory and session state ──────────────────
    session_id = scope["url_route"]["kwargs"]["session_id"]
    ctx = await get_session_context(session_id)
    cwd = ctx.cwd
    archived = ctx.archived

    # ── Spawn PTY (tmux or raw shell) ────────────────────────────────
    use_tmux = wants_tmux(scope)
    if use_tmux and get_tmux_path() is None:
        logger.warning(
            "tmux requested but not installed for session %s, falling back to raw shell",
            session_id,
        )
        use_tmux = False

    # For archived sessions, only use tmux if a session already exists
    # (don't create new tmux sessions for archived conversations).
    if use_tmux and archived and not tmux_session_exists(session_id):
        use_tmux = False

    try:
        if use_tmux:
            child_pid, master_fd = spawn_tmux_pty(cwd, session_id)
        else:
            child_pid, master_fd = spawn_pty(cwd)
    except OSError:
        logger.exception("Failed to spawn PTY for session %s", session_id)
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": "\r\nError: failed to start shell.\r\n"})
        await send({"type": "websocket.close", "code": 1011})
        return

    # ── Accept connection ─────────────────────────────────────────────
    await send({"type": "websocket.accept"})

    # Configure tmux session
    if use_tmux:
        # Enable mouse mode so wheel events scroll the buffer (not command history)
        tmux_set_option(session_id, "mouse", "on")
        # Override default wheel bindings for proper scroll in less/htop/etc.
        _configure_tmux_scroll_bindings()
        # Rename the default tmux window to "main" for fresh sessions
        windows = tmux_list_windows(session_id)
        if windows and windows[0]["name"] != "main":
            tmux_rename_window(session_id, "0", "main")

    # ── PTY output reader task ────────────────────────────────────────
    # Uses add_reader for event-driven reading, and an asyncio.Queue
    # to bridge the sync callback to the async send loop.
    output_queue = asyncio.Queue()
    pty_dead = False

    def on_pty_output():
        """Sync callback from add_reader — reads PTY and enqueues output."""
        nonlocal pty_dead
        if pty_dead:
            return
        try:
            data = os.read(master_fd, READ_BUFFER_SIZE)
        except OSError:
            pty_dead = True
            output_queue.put_nowait(None)  # Sentinel: PTY closed
            return

        if not data:
            pty_dead = True
            output_queue.put_nowait(None)
            return

        output_queue.put_nowait(data)

    loop = asyncio.get_event_loop()
    loop.add_reader(master_fd, on_pty_output)

    async def pty_output_sender():
        """Drains output_queue and sends to WebSocket."""
        try:
            while True:
                data = await output_queue.get()
                if data is None:
                    # PTY closed — send exit message and signal main loop
                    await send({"type": "websocket.send", "text": "\r\n[Process exited]\r\n"})
                    return
                text = data.decode(errors="replace")
                await send({"type": "websocket.send", "text": text})
        except Exception:
            # WebSocket might be closed already
            return

    sender_task = asyncio.create_task(pty_output_sender())

    # ── Main receive loop ─────────────────────────────────────────────
    # We also watch the sender_task: when the PTY dies, the sender
    # finishes and we should close the WebSocket.

    async def receive_loop():
        """Process incoming WebSocket messages until disconnect."""
        while True:
            message = await receive()

            if message["type"] == "websocket.receive":
                text = message.get("text")
                if text is None:
                    continue

                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "input":
                    data = msg.get("data", "")
                    if data and not pty_dead:
                        try:
                            os.write(master_fd, data.encode())
                        except OSError:
                            return

                elif msg_type == "resize":
                    cols = msg.get("cols", DEFAULT_COLS)
                    rows = msg.get("rows", DEFAULT_ROWS)
                    if not pty_dead:
                        try:
                            set_winsize(master_fd, cols, rows)
                        except OSError:
                            pass

                # ── tmux window control messages ──────────────────
                elif msg_type == "list_windows" and use_tmux:
                    ctx = await get_session_context(session_id)
                    win_list = tmux_list_windows(session_id)
                    presets = resolve_preset_sources(ctx, ctx.project_id)
                    alternate = tmux_pane_is_alternate(session_id)
                    await send({"type": "websocket.send",
                                "text": json.dumps({"type": "windows", "windows": win_list,
                                                    "presets": presets,
                                                    "alternate_on": alternate})})

                elif msg_type == "create_window" and use_tmux:
                    ctx = await get_session_context(session_id)
                    window_name = msg.get("name", "").strip()
                    if window_name:
                        window_cwd = msg.get("preset_cwd") or ctx.cwd
                        tmux_create_window(session_id, window_name, cwd=window_cwd)
                        # Run optional command in the new window
                        command = msg.get("command")
                        if command:
                            tmux_send_keys(session_id, window_name, command)
                        win_list = tmux_list_windows(session_id)
                        presets = resolve_preset_sources(ctx, ctx.project_id)
                        alternate = tmux_pane_is_alternate(session_id)
                        await send({"type": "websocket.send",
                                    "text": json.dumps({"type": "windows", "windows": win_list,
                                                        "presets": presets,
                                                        "alternate_on": alternate})})

                elif msg_type == "select_window" and use_tmux:
                    window_name = msg.get("name", "")
                    if window_name and tmux_select_window(session_id, window_name):
                        await send({"type": "websocket.send",
                                    "text": json.dumps({"type": "window_changed", "name": window_name})})

            elif message["type"] == "websocket.disconnect":
                return

    recv_task = asyncio.create_task(receive_loop())

    # ── tmux window monitor (detects external changes like shell exit) ──
    monitor_task = None
    if use_tmux:
        monitor_task = asyncio.create_task(_tmux_window_monitor(session_id, send, ctx))

    try:
        # Wait for either the PTY to die or the client to disconnect
        tasks = [sender_task, recv_task]
        if monitor_task:
            tasks.append(monitor_task)

        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel whichever is still running
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # If the PTY died (sender finished first), close the WebSocket
        if sender_task in done:
            try:
                await send({"type": "websocket.close", "code": 1000})
            except Exception:
                pass

    except Exception:
        logger.exception("Error in terminal WebSocket for session %s", session_id)
    finally:
        # ── Cleanup ───────────────────────────────────────────────────
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        cleanup_pty(master_fd, child_pid)
