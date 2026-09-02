"""
Raw ASGI WebSocket handler for interactive terminal sessions.

Bypasses Django Channels' AsyncWebsocketConsumer entirely to eliminate
the channel layer overhead on every message. The ASGI send/receive
callables are used directly, giving near-native WebSocket performance.

Provides a PTY-backed terminal per context (session, project, workspace, or global),
communicating over dedicated WebSocket endpoints:
  /ws/terminal/<project_id>/<session_id>/<terminal_index>/  — session terminal
  /ws/terminal/<project_id>/<terminal_index>/                — project terminal
  /ws/terminal/<terminal_index>/                             — global/workspace terminal

Protocol:
  Client → Server (JSON text frames):
    { "type": "input", "data": "ls -la\n" }       — keyboard input
    { "type": "resize", "cols": 120, "rows": 30 }  — terminal resize
    { "type": "tmux_scroll", "lines": -3 }         — tmux scrollback (neg=up)

  Server → Client:
    Plain text frames — raw PTY output (no JSON wrapping for performance).
    JSON text frames (when type field present) — control messages:
      { "type": "pane_state", "alternate_on": true }  — tmux pane screen mode
      { "type": "scroll_result", "requested": -3,
        "scroll_position": 42, "history_size": 500 }  — tmux scroll position
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

from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    SESSION_FINGERPRINT_KEY,
    is_session_authenticated,
)
from twicc.paths import tmux_socket_suffix
from twicc.provider_homes import provider_env_overlay
from twicc.providers.helpers import get_provider_helpers_registry

logger = logging.getLogger(__name__)

# WebSocket close code for authentication failure (same as UpdatesConsumer).
WS_CLOSE_AUTH_FAILURE = 4001

# Default terminal dimensions
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

# Read buffer size (20 KiB — large enough to avoid excessive callbacks,
# small enough to keep latency low)
READ_BUFFER_SIZE = 20480

# tmux socket name — isolates twicc sessions from the user's own tmux AND from
# the other TwiCC instances of the same user: ``twicc`` on the default data dir
# (``~/.twicc``), ``twicc-<sha8 of the data dir>`` elsewhere (worktrees), see
# ``paths.tmux_socket_suffix``. tmux sockets are per user, so a shared name
# would make a worktree's terminals attach to main's shells (and carry main's
# provider homes). Computed once at import: the data dir is fixed per process.
TMUX_SOCKET_NAME = "twicc" + tmux_socket_suffix()

# Hybrid CLI sessions live on a SEPARATE tmux socket (same per-instance suffix).
# The user's custom tmux config (``terminalTmuxConfigPath``) is loaded
# server-wide on the main socket, so a dedicated socket is the only way to
# guarantee it never reaches the embedded Claude TUI (and vice-versa). Every
# tmux call bound to a terminal context routes through ``tmux_socket_for``; the
# hybrid agent's own primitives (hybrid/tmux.py) import this name directly.
HYBRID_TMUX_SOCKET_NAME = "twicc-hybrid" + tmux_socket_suffix()

# Maximum length for terminal tab labels (stored in tmux user options)
TERMINAL_LABEL_MAX_LENGTH = 30

# tmux user option name for storing terminal labels
_TMUX_LABEL_OPTION = "@twicc_label"

# tmux user option flagging a terminal to auto-attach into descendant panels
# (set to "1" when enabled, unset otherwise). Read back in list_tmux_terminals.
_TMUX_AUTOATTACH_OPTION = "@twicc_autoattach"

# tmux user option flagging a session as "used": set (to "1") on the FIRST byte of input written
# to its PTY, from any source (typing, snippets, an auto-injected provider-login command). Read by
# the tmux reaper (twicc.tmux_cleanup_task) to spare used sessions. MUST be set per-session
# (set-option -t) only — never -g/-s, or format expansion would resolve it for every session.
TMUX_USED_OPTION = "@twicc_used"


class TerminalInfo(NamedTuple):
    """A terminal's index and metadata read from tmux."""
    index: int
    label: str  # empty string if no custom label set
    auto_attach: bool = False  # mirrors the @twicc_autoattach user option


@sync_to_async
def get_terminal_cwd(session_id: str | None = None, project_id: str | None = None) -> tuple[str, bool]:
    """Resolve the working directory and archived status for a terminal.

    Returns (cwd, archived).

    When session_id is provided, the priority order is:
    - Session.git_directory → Project.directory → Project.git_root → ~
    When only project_id is provided:
    - Project.directory → Project.git_root → ~
    When neither is provided:
    - ~ (home directory)
    """
    from twicc.core.models import Project, Session

    home = os.path.expanduser("~")

    # No session — resolve from project only
    if not session_id:
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                for candidate in (project.directory, project.git_root):
                    if candidate and os.path.isdir(candidate):
                        return candidate, False
            except Project.DoesNotExist:
                pass
        return home, False

    # Session provided — existing logic
    try:
        session = Session.objects.select_related("project").get(id=session_id)
    except Session.DoesNotExist:
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                for candidate in (project.directory, project.git_root):
                    if candidate and os.path.isdir(candidate):
                        return candidate, False
            except Project.DoesNotExist:
                pass
        return home, False

    if session.git_directory:
        candidates = [
            session.git_directory,
            session.project.directory if session.project else None,
        ]
    else:
        candidates = [
            session.project.directory if session.project else None,
            session.project.git_root if session.project else None,
        ]

    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return candidate, session.archived

    return home, session.archived


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


def validate_tmux_config_path(file_path: str) -> tuple[bool, str]:
    """Validate that a tmux config file exists and is readable.

    Does not attempt to parse the file — tmux itself will report errors on
    start if the syntax is invalid. We only gate path-level issues here.

    Returns a ``(valid, message)`` tuple.
    """
    if not file_path:
        return False, "No file path provided"

    path = os.path.expanduser(file_path)
    if not os.path.isfile(path):
        return False, "File not found"
    if not os.access(path, os.R_OK):
        return False, "File is not readable"
    return True, "Valid tmux config"


def resolve_tmux_config_path(file_path: str) -> str | None:
    """Return an absolute usable tmux config path, or None if unusable.

    Expands ``~`` and verifies the path is readable. Invalid or missing paths
    return None so the caller can fall back to ``/dev/null`` (no config).
    """
    if not file_path:
        return None
    path = os.path.expanduser(file_path)
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        return None
    return path


def tmux_session_name(terminal_context: str, terminal_index: int = 0) -> str:
    """Return the tmux session name for a given terminal context and index.

    terminal_context is a string like 's:<id>', 'p:<id>', 'w:<id>', 'h:<id>',
    or 'global'. For session terminals ('s:' prefix), keep the existing naming
    scheme for backward compatibility with running tmux sessions. Hybrid
    contexts ('h:' prefix) map onto the hybrid agent's own tmux session —
    the terminal layer only ever ATTACHES to those, it never creates them.
    All names are prefixed with 'twicc-' and sanitized (no dots/colons).
    """
    if terminal_context.startswith("h:"):
        # Single source of truth for the name lives with the hybrid agent.
        # Local import: hybrid/tmux.py imports this module's constants.
        from twicc.providers.claude_code.agent.hybrid.tmux import hybrid_tmux_session_name

        return hybrid_tmux_session_name(terminal_context[2:])
    if terminal_context.startswith("s:"):
        # Backward compat: session terminals keep old naming
        session_id = terminal_context[2:]
        base = "twicc-" + session_id.replace(".", "_").replace(":", "_")
    else:
        # Project, workspace, global: twicc- prefix + sanitized context
        sanitized = terminal_context.replace(":", "_").replace(".", "_")
        base = "twicc-" + sanitized
    if terminal_index == 0:
        return base
    return f"{base}__{terminal_index}"


def tmux_socket_for(terminal_context: str) -> str:
    """Return the tmux socket name to use for a given terminal context.

    Hybrid contexts (``h:`` prefix) get their own dedicated socket
    (``HYBRID_TMUX_SOCKET_NAME``) so the user's custom tmux config — loaded
    server-wide on the main socket — can never reach the embedded Claude TUI.
    Every context-bound tmux invocation MUST route its ``-L`` through here.
    """
    return HYBRID_TMUX_SOCKET_NAME if terminal_context.startswith("h:") else TMUX_SOCKET_NAME


# ── Terminal environment sanitation ───────────────────────────────────────
# Env vars that agent harnesses / CI set to force *non-interactive* behaviour.
# In a human terminal they break things (a rebase that never opens an editor,
# a dead pager, a swallowed credential prompt) or make tools believe they run
# under an agent. We strip them from every human terminal PTY, on top of the
# provider ``purge_env_vars``.
#
# Deliberately a NAMED list (+ the indexed ``GIT_CONFIG_*`` family), never a
# blanket ``CLAUDE_``/``ANTHROPIC_``/``TWICC_`` wipe: those carry the user's
# own config, model overrides and backend wiring, which must reach commands
# run from the terminal untouched — the same narrow-purge rationale as the
# Codex helper's ``_ENV_VAR_PREFIXES``. Unsetting is safe even for a var the
# user legitimately defines: the login shell re-sources their profile after we
# strip, so a profile-defined value comes back while the inherited agent one
# stays gone.
_TERMINAL_ENV_STRIP_NAMES: frozenset[str] = frozenset({
    # git → forced non-interactive
    "GIT_EDITOR",           # =true ⇒ `git rebase -i`/`commit`/`amend` never open an editor
    "GIT_SEQUENCE_EDITOR",  # the rebase todo editor (wins over everything)
    "GIT_PAGER",            # =cat ⇒ no pager for log/diff/show
    "GIT_TERMINAL_PROMPT",  # =0 ⇒ HTTPS credential prompt fails instead of asking
    "GIT_ASKPASS",          # redirects the password helper to a non-interactive stub
    "GIT_CONFIG_GLOBAL",    # masks the real ~/.gitconfig
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",     # drives the GIT_CONFIG_KEY_n/VALUE_n inline-config family
    # agent / CI markers
    "AI_AGENT",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_EFFORT",
    "CI",
    "CONTINUOUS_INTEGRATION",
    "DEBIAN_FRONTEND",
})

# GIT_CONFIG_COUNT enables an indexed family of inline-config vars; strip them
# all, whatever the count.
_TERMINAL_ENV_STRIP_PREFIXES: tuple[str, ...] = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def _terminal_env_keys_to_strip(keys) -> list[str]:
    """Return the subset of ``keys`` that are terminal-env noise to remove."""
    return [
        key for key in keys
        if key in _TERMINAL_ENV_STRIP_NAMES or key.startswith(_TERMINAL_ENV_STRIP_PREFIXES)
    ]


def sanitize_terminal_env(env) -> None:
    """Strip agent/CI non-interactive env vars from ``env`` in place.

    Applied to every human terminal PTY (raw shell and tmux client) on top of
    the provider ``purge_env_vars``. See ``_TERMINAL_ENV_STRIP_NAMES``.
    """
    for key in _terminal_env_keys_to_strip(list(env)):
        del env[key]


def purge_tmux_global_env(socket: str) -> None:
    """Remove agent/CI noise from a tmux server's *global* environment.

    A tmux server freezes the environment of whatever process first started it
    and hands that to every new pane — so a stale ``GIT_EDITOR=true`` survives
    backend restarts and keeps poisoning freshly opened terminals. Unsetting
    the vars globally makes newly created sessions/panes clean (already-running
    panes keep their env). A no-op when no server is running on ``socket``.

    Only ever called on the main terminal socket, never the hybrid one — the
    embedded Claude CLI there legitimately wants the agent environment.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return
    try:
        result = subprocess.run(
            [tmux_path, "-L", socket, "show-environment", "-g"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return
    if result.returncode != 0:
        return  # no server running on this socket

    # show-environment -g prints "NAME=value" for set vars and "-NAME" for
    # removed ones; only NAME=value lines are candidates to unset.
    present = [
        line.split("=", 1)[0]
        for line in result.stdout.splitlines()
        if line and not line.startswith("-")
    ]
    for name in _terminal_env_keys_to_strip(present):
        try:
            subprocess.run(
                [tmux_path, "-L", socket, "set-environment", "-g", "-u", name],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


# ── PTY helpers (pure functions, no class needed) ─────────────────────────

def set_winsize(fd: int, cols: int, rows: int) -> None:
    """Set the terminal window size on a file descriptor."""
    # struct winsize { unsigned short ws_row, ws_col, ws_xpixel, ws_ypixel; }
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def parse_initial_size(qs: dict[str, list[str]]) -> tuple[int, int]:
    """Initial PTY size from the WS query string, or the defaults.

    The frontend fits xterm.js before connecting and passes the resulting
    dimensions, so the PTY spawns (and tmux attaches) at the right size
    instead of 80x24-then-resize — that transient default made tmux re-wrap
    its history twice and TUI apps repaint fully on every attach.
    """
    try:
        cols = int(qs.get("cols", [""])[0])
        rows = int(qs.get("rows", [""])[0])
    except ValueError:
        return DEFAULT_COLS, DEFAULT_ROWS
    if not (2 <= cols <= 1000 and 1 <= rows <= 1000):
        return DEFAULT_COLS, DEFAULT_ROWS
    return cols, rows


def spawn_pty(cwd: str, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> tuple[int, int]:
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

        # Strip provider-specific env vars that may have been set by an
        # SDK in the backend process — without this, the same provider
        # CLI launched from this terminal would think it's already
        # inside an SDK session.
        get_provider_helpers_registry().purge_env_vars(os.environ)

        # Also strip agent/CI vars that force non-interactive behaviour
        # (GIT_EDITOR=true, dead pagers, credential-prompt kill switches, …)
        # so the terminal behaves like a normal human login shell.
        sanitize_terminal_env(os.environ)

        # Configured provider homes, explicit after the purges: a ``twicc codex
        # login`` typed in this terminal must land in THIS instance's home.
        os.environ.update(provider_env_overlay())

        # Exec the shell as a login shell (prefix argv[0] with -)
        os.execvp(shell, [f"-{os.path.basename(shell)}"])
        # execvp does not return; if it fails, child exits
        os._exit(1)

    # ── Parent process ──
    # Set initial window size
    set_winsize(master_fd, cols, rows)

    # Make the fd non-blocking for event-driven reading
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return child_pid, master_fd


def _tmux_client_argv(
    *,
    socket: str,
    config_arg: str,
    name: str,
    attach_only: bool,
    env_overlay: dict[str, str],
) -> list[str]:
    """argv of the tmux client exec'd by :func:`spawn_tmux_pty` (pure, testable).

    ``attach_only`` → ``attach-session -t =<name>`` (never creates anything).
    Otherwise ``new-session -A -s <name>`` plus one ``-e NAME=VALUE`` per
    ``env_overlay`` entry (tmux ≥ 3.2): a freshly created session carries the
    configured provider homes even when the tmux server predates a ``.env``
    change and froze another environment. ``-e`` is ignored by ``-A`` when the
    session already exists (that session keeps its own environment).
    """
    argv = ["tmux", "-L", socket, "-f", config_arg]
    if attach_only:
        return [*argv, "attach-session", "-t", "=" + name]
    argv += ["new-session", "-A", "-s", name]
    for key, value in env_overlay.items():
        argv += ["-e", f"{key}={value}"]
    return argv


def spawn_tmux_pty(
    cwd: str,
    terminal_context: str,
    terminal_index: int = 0,
    config_path: str | None = None,
    attach_only: bool = False,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> tuple[int, int]:
    """Fork a PTY running tmux, attaching to or creating a named terminal.

    Uses ``tmux -L <socket> -f <cfg> new-session -A -s <name> [-e NAME=VALUE…]``
    which:
    - ``-L <socket>``: a dedicated socket (isolation from the user's tmux and
      from other TwiCC instances), ``tmux_socket_for(terminal_context)`` —
      hybrid contexts get their own socket so the user's tmux config can't
      leak into the embedded Claude TUI
    - ``-f <cfg>``: config file — ``/dev/null`` unless a valid user path is passed
    - ``new-session -A``: attach if session exists, create if not
    - ``-s <name>``: deterministic session name
    - ``-e NAME=VALUE``: one per configured provider home, so a NEW session
      gets this instance's values even on a server whose global environment
      was frozen by an older backend (see ``_tmux_client_argv``)

    With ``attach_only=True`` the client runs ``attach-session -t =<name>``
    instead: it never creates anything, and exits immediately when the target
    session does not exist (hybrid terminals: the tmux session belongs to the
    hybrid agent, the terminal layer only ever views it).

    The dedicated socket is non-negotiable. When a user config is loaded, the
    caller must still force ``mouse off`` at session level after the session is
    created (see ``terminal_application``). This protects the frontend's
    mouse-driven selection and scroll from any ``set -g mouse on`` in the user's
    config.

    Returns (child_pid, master_fd) — same interface as spawn_pty.
    The child_pid is the tmux *client* process, not the server.
    Killing it just detaches; the tmux session keeps running.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        raise FileNotFoundError("tmux is not installed")

    config_arg = config_path if config_path else "/dev/null"

    name = tmux_session_name(terminal_context, terminal_index)
    child_pid, master_fd = pty.fork()

    if child_pid == 0:
        # ── Child process ──
        os.chdir(cwd)
        os.environ["TERM"] = "xterm-256color"
        # Unset TMUX to avoid nesting issues if the server itself runs in tmux
        os.environ.pop("TMUX", None)
        # Strip provider-specific env vars (same reason as in spawn_pty), then
        # the agent/CI non-interactive vars. For a freshly created server this
        # sanitized env becomes the server's global environment (clean panes);
        # for an existing server the pane inherits the server's frozen global
        # env instead — handled by purge_tmux_global_env before the spawn.
        get_provider_helpers_registry().purge_env_vars(os.environ)
        sanitize_terminal_env(os.environ)
        # Configured provider homes, explicit after the purges (same reason as
        # in spawn_pty); also passed as ``-e`` below for the new-session case.
        overlay = provider_env_overlay()
        os.environ.update(overlay)

        os.execvp(tmux_path, _tmux_client_argv(
            socket=tmux_socket_for(terminal_context),
            config_arg=config_arg,
            name=name,
            attach_only=attach_only,
            env_overlay=overlay,
        ))
        os._exit(1)

    # ── Parent process ──
    set_winsize(master_fd, cols, rows)

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


def tmux_session_exists(terminal_context: str, terminal_index: int = 0) -> bool:
    """Check if a tmux session exists for the given terminal context and index."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "has-session", "-t", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def list_tmux_terminals(terminal_context: str) -> list[TerminalInfo]:
    """List all terminal indices (with labels) that have active tmux sessions for a given terminal context.

    Queries the twicc tmux socket and filters by session name prefix.
    Uses a single tmux call with ``#{session_name}`` and ``#{@twicc_label}``
    in the format string to retrieve both name and label at once.

    Returns a sorted list of TerminalInfo (by index).
    Returns an empty list if tmux is not installed or no sessions exist.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return []

    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "list-sessions",
             "-F", "#{session_name}\t#{@twicc_label}\t#{@twicc_autoattach}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []

    prefix = tmux_session_name(terminal_context, 0)  # "twicc-<normalized_context>"
    terminals: list[TerminalInfo] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # split() (not partition()) so we can read the third column; an unset
        # user option expands to "" → label "" / auto_attach False.
        parts = line.split("\t")
        name = parts[0]
        label = parts[1] if len(parts) > 1 else ""
        auto_attach = len(parts) > 2 and parts[2] == "1"
        if name == prefix:
            terminals.append(TerminalInfo(index=0, label=label, auto_attach=auto_attach))
        elif name.startswith(prefix + "__"):
            suffix = name[len(prefix) + 2:]
            try:
                terminals.append(TerminalInfo(index=int(suffix), label=label, auto_attach=auto_attach))
            except ValueError:
                continue
    return sorted(terminals, key=lambda t: t.index)


def kill_tmux_terminal(terminal_context: str, terminal_index: int = 0) -> bool:
    """Kill the tmux session for the given terminal context and index.

    Called when a terminal context is cleaned up (e.g. session archived).
    Returns True if the session was killed, False if it didn't exist or
    tmux is not installed.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "kill-session", "-t", name],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("Failed to kill tmux session %s", name)
        return False


def kill_all_tmux_terminals(terminal_context: str) -> int:
    """Kill all tmux sessions for the given terminal context (all terminal indices).

    Returns the number of sessions killed.
    """
    terminals = list_tmux_terminals(terminal_context)
    killed = 0
    for terminal in terminals:
        if kill_tmux_terminal(terminal_context, terminal.index):
            killed += 1
    return killed


def tmux_set_option(terminal_context: str, option: str, value: str, terminal_index: int = 0) -> bool:
    """Set a tmux session option.

    Returns True on success, False on failure.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "set-option", "-t", name, option, value],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def set_tmux_terminal_label(terminal_context: str, terminal_index: int, label: str) -> bool:
    """Set a display label on a tmux terminal session using a user option.

    The label is stored as a tmux user option (``@twicc_label``) on the session,
    which can be read back via ``list_tmux_terminals`` (in the format string)
    or ``get_tmux_terminal_label``.

    The label is truncated to ``TERMINAL_LABEL_MAX_LENGTH`` characters.
    An empty label removes the option.

    Returns True on success, False on failure (tmux not installed, session
    doesn't exist, etc.).
    """
    label = label[:TERMINAL_LABEL_MAX_LENGTH].strip()
    if not label:
        # Remove the option entirely when label is empty
        return _unset_tmux_terminal_label(terminal_context, terminal_index)
    return tmux_set_option(terminal_context, _TMUX_LABEL_OPTION, label, terminal_index)


def _unset_tmux_terminal_label(terminal_context: str, terminal_index: int) -> bool:
    """Remove the label user option from a tmux terminal session."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "set-option", "-u", "-t", name, _TMUX_LABEL_OPTION],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def set_tmux_terminal_autoattach(terminal_context: str, terminal_index: int, enabled: bool) -> bool:
    """Set/clear the "auto-attach into children" flag on a tmux terminal session.

    Stored as a tmux user option (``@twicc_autoattach`` = ``"1"``) on the session,
    read back via ``list_tmux_terminals``. Twin of ``set_tmux_terminal_label``.

    Returns True on success, False on failure (tmux not installed, no session…).
    """
    if not enabled:
        return _unset_tmux_terminal_autoattach(terminal_context, terminal_index)
    return tmux_set_option(terminal_context, _TMUX_AUTOATTACH_OPTION, "1", terminal_index)


def _unset_tmux_terminal_autoattach(terminal_context: str, terminal_index: int) -> bool:
    """Remove the auto-attach user option from a tmux terminal session."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "set-option", "-u", "-t", name, _TMUX_AUTOATTACH_OPTION],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _tmux_set_global_option(option: str, value: str, socket: str = TMUX_SOCKET_NAME) -> bool:
    """Set a tmux global (server-wide) option on the given twicc socket.

    ``socket`` defaults to the main socket; pass ``tmux_socket_for(context)``
    so a hybrid attach configures its own dedicated server, not the main one.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False
    try:
        result = subprocess.run(
            [tmux_path, "-L", socket, "set-option", "-g", option, value],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _tmux_scroll(terminal_context: str, lines: int, terminal_index: int = 0) -> tuple[int | None, int | None]:
    """Scroll the tmux pane by the given number of lines.

    Enters hidden copy-mode (-eH) if not already in copy-mode, then
    scrolls exactly N lines. Positive = down, negative = up.
    The -e flag auto-exits copy-mode when scrolling back to the bottom.

    Returns (scroll_position, history_size) after the scroll, or (None, None)
    if the position could not be determined.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return None, None
    name = tmux_session_name(terminal_context, terminal_index)
    cmd = "scroll-up" if lines < 0 else "scroll-down"
    count = abs(lines)
    # Single tmux invocation: enter copy-mode (no-op if already in) + scroll N lines
    subprocess.run(
        [tmux_path, "-L", tmux_socket_for(terminal_context),
         "copy-mode", "-eH", "-t", name, ";",
         "send-keys", "-t", name, "-X", "-N", str(count), cmd],
        capture_output=True, timeout=5,
    )
    # Query scroll position after the scroll.
    # If copy-mode auto-exited (via -e flag, reached bottom), scroll_position
    # will be empty — that means we're at the bottom (position 0).
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "display-message",
             "-t", name, "-p", "#{scroll_position},#{history_size}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) == 2:
                # Empty scroll_position means copy-mode exited → at bottom
                pos = int(parts[0]) if parts[0] else 0
                size = int(parts[1]) if parts[1] else 0
                return pos, size
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None, None


def _tmux_pane_state(terminal_context: str, terminal_index: int = 0) -> dict | None:
    """Query the full pane state in a single tmux call.

    Returns a dict with:
    - alternate_on (bool): pane is in alternate screen (less, vim, etc.)
    - in_copy_mode (bool): pane is in copy-mode (scrollback active)
    - scroll_position (int): lines scrolled from bottom (0 = at bottom)
    - history_size (int): total scrollback history lines

    Returns None if the query fails.
    """
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return None

    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "display-message",
             "-t", name, "-p",
             "#{alternate_on},#{pane_in_mode},#{scroll_position},#{history_size}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split(",")
        if len(parts) != 4:
            return None
        return {
            "alternate_on": parts[0] == "1",
            "in_copy_mode": parts[1] == "1",
            "scroll_position": int(parts[2]) if parts[2] else 0,
            "history_size": int(parts[3]) if parts[3] else 0,
        }
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


# ── tmux pane state monitor ──────────────────────────────────────────────

# Polling interval for detecting pane state changes
_PANE_POLL_INTERVAL = 2  # seconds


async def _tmux_pane_monitor(terminal_context: str, send, terminal_index: int = 0) -> None:
    """Periodically poll tmux pane state and push updates to the frontend.

    Tracks: alternate screen mode, copy-mode status, scroll position,
    and history size. Only sends a message when something changed.
    """
    prev_state = await asyncio.to_thread(_tmux_pane_state, terminal_context, terminal_index)
    try:
        while True:
            await asyncio.sleep(_PANE_POLL_INTERVAL)
            state = await asyncio.to_thread(_tmux_pane_state, terminal_context, terminal_index)
            if state is None:
                continue
            if state != prev_state:
                prev_state = state
                await send({"type": "websocket.send",
                            "text": json.dumps({"type": "pane_state", **state})})
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
    # Unprotected instance (no password): refuse non-local connections —
    # there's nothing to authenticate against. No-op when a password is
    # configured or the operator opted out (see twicc.auth.local_access).
    if scope_remote_access_blocked(scope):
        logger.warning("Terminal WebSocket rejected: remote access requires a password")
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": json.dumps({"type": "auth_failure"})})
        await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILURE})
        return

    # A session bound to an older password hash (rotated since login)
    # is rejected the same way as an unauthenticated one.
    if settings.TWICC_PASSWORD_HASH:
        session = scope.get("session", {})
        session_auth, session_fp = await sync_to_async(
            lambda: (session.get(SESSION_AUTH_KEY), session.get(SESSION_FINGERPRINT_KEY))
        )()
        if not is_session_authenticated(session_auth, session_fp, settings.TWICC_PASSWORD_HASH):
            logger.warning("Terminal WebSocket rejected: not authenticated")
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": json.dumps({"type": "auth_failure"})})
            await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILURE})
            return

    # ── Resolve terminal context and working directory ──────────────────
    session_id = scope["url_route"]["kwargs"].get("session_id")
    project_id = scope["url_route"]["kwargs"].get("project_id")
    terminal_index = scope["url_route"]["kwargs"].get("terminal_index", 0)
    qs = parse_qs(scope.get("query_string", b"").decode())
    cols, rows = parse_initial_size(qs)

    # Build terminal context key and resolve cwd
    if session_id:
        terminal_context = f"s:{session_id}"
        cwd, archived = await get_terminal_cwd(session_id, project_id)
    elif project_id:
        terminal_context = f"p:{project_id}"
        cwd, archived = await get_terminal_cwd(None, project_id)
    else:
        # Global or workspace: check query string for name and cwd
        terminal_context = qs.get("name", ["global"])[0]

        # Only accept cwd from query string for workspace terminals (name present)
        home = os.path.expanduser("~")
        if terminal_context != "global":
            # Workspace: use frontend-provided cwd, validate it exists
            requested_cwd = qs.get("cwd", [None])[0]
            cwd = requested_cwd if requested_cwd and os.path.isdir(requested_cwd) else home
        else:
            cwd = home
        archived = False

    # ── Hybrid terminals: attach-only ─────────────────────────────────
    # An ``h:<session_id>`` context views the hybrid agent's own tmux
    # session (the one running the claude TUI). The terminal layer NEVER
    # creates it — when it does not exist (CLI not launched yet, or dead
    # and cleaned up), tell the client the PTY is gone and close. The
    # next send relaunches the CLI and the frontend reconnects.
    hybrid_attach = terminal_context.startswith("h:")
    if hybrid_attach:
        exists = (
            get_tmux_path() is not None
            and await asyncio.to_thread(tmux_session_exists, terminal_context, terminal_index)
        )
        if not exists:
            await send({"type": "websocket.accept"})
            await send({
                "type": "websocket.send",
                "text": json.dumps({"type": "pty_exited"}),
            })
            await send({"type": "websocket.close", "code": 1000})
            return

    # ── Spawn PTY (tmux or raw shell) ────────────────────────────────
    use_tmux = wants_tmux(scope) or hybrid_attach
    if use_tmux and get_tmux_path() is None:
        logger.warning(
            "tmux requested but not installed for terminal %s, falling back to raw shell",
            terminal_context,
        )
        use_tmux = False

    # For archived sessions, only use tmux if a session already exists
    # (don't create new tmux sessions for archived conversations).
    if use_tmux and archived and not tmux_session_exists(terminal_context, terminal_index):
        use_tmux = False

    tmux_config_path: str | None = None
    if use_tmux:
        # Resolve user-provided tmux config (synced setting), fall back to no
        # config if missing or unreadable. The dedicated socket and forced
        # `mouse off` (set below after creation) remain non-negotiable.
        from twicc.synced_settings import read_synced_settings
        synced = await sync_to_async(read_synced_settings)()
        configured_path = synced.get("terminalTmuxConfigPath") or ""
        tmux_config_path = resolve_tmux_config_path(configured_path)
        if configured_path and tmux_config_path is None:
            logger.warning(
                "Configured tmux config path is unreadable, falling back to default: %s",
                configured_path,
            )

    # A tmux server freezes the env of whatever first started it and hands it
    # to every new pane; if a stale server carries agent/CI noise (e.g. a
    # GIT_EDITOR=true from a backend once launched under an agent), purge its
    # global env BEFORE we create the session, so the new pane's shell starts
    # clean. No-op when no server exists yet — the sanitized child env then
    # seeds a clean server. Never the hybrid socket (its CLI wants agent env).
    if use_tmux and not hybrid_attach:
        await asyncio.to_thread(purge_tmux_global_env, tmux_socket_for(terminal_context))

    try:
        if use_tmux:
            child_pid, master_fd = spawn_tmux_pty(
                cwd, terminal_context, terminal_index, config_path=tmux_config_path,
                attach_only=hybrid_attach, cols=cols, rows=rows,
            )
        else:
            child_pid, master_fd = spawn_pty(cwd, cols=cols, rows=rows)
    except OSError:
        logger.exception("Failed to spawn PTY for terminal %s", terminal_context)
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": "\r\nError: failed to start shell.\r\n"})
        await send({"type": "websocket.close", "code": 1011})
        return

    # ── Accept connection ─────────────────────────────────────────────
    await send({"type": "websocket.accept"})

    # Configure tmux session: force mouse OFF so tmux doesn't capture
    # mouse events. All scroll and selection is handled by the frontend
    # (touch handlers on mobile, capture-phase handlers on desktop).
    # Tmux scrollback is handled via the tmux_scroll backend command.
    #
    # This override is critical when a user-provided tmux config is loaded:
    # any `set -g mouse on` in that config would otherwise break frontend
    # selection. The session-level set-option below overrides the global
    # one loaded from the config file.
    if use_tmux:
        # Wait for the tmux session to be fully created — spawn_tmux_pty
        # forks and returns immediately, but the child needs time to exec
        # tmux and create the session before we can configure it.
        for _ in range(20):  # up to 2s
            if await asyncio.to_thread(tmux_session_exists, terminal_context, terminal_index):
                break
            await asyncio.sleep(0.1)

        # Force mouse off at both session and global level — ensures clean
        # state regardless of prior tmux server configuration or user config.
        await asyncio.to_thread(tmux_set_option, terminal_context, "mouse", "off", terminal_index=terminal_index)
        await asyncio.to_thread(_tmux_set_global_option, "mouse", "off", tmux_socket_for(terminal_context))

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
        """Drains output_queue and sends to WebSocket.

        After the first non-empty PTY output is forwarded, emits a
        ``{"type": "ready"}`` control frame exactly once per PTY lifecycle.
        Auto-injection flows on the frontend (provider auth "Launch in
        terminal", snippets opened in a new tab) wait for this signal
        before writing their command, so that the shell prompt has been
        rendered and the PTY → (tmux) → shell chain is fully wired before
        any input is sent.
        """
        ready_sent = False
        try:
            while True:
                data = await output_queue.get()
                if data is None:
                    # PTY closed — send exit message and signal main loop
                    await send({"type": "websocket.send", "text": "\r\n[Process exited]\r\n"})
                    return
                text = data.decode(errors="replace")
                await send({"type": "websocket.send", "text": text})
                if not ready_sent:
                    await send({
                        "type": "websocket.send",
                        "text": json.dumps({"type": "ready"}),
                    })
                    ready_sent = True
        except Exception:
            # WebSocket might be closed already
            return

    sender_task = asyncio.create_task(pty_output_sender())

    # ── Main receive loop ─────────────────────────────────────────────
    # We also watch the sender_task: when the PTY dies, the sender
    # finishes and we should close the WebSocket.

    async def receive_loop():
        """Process incoming WebSocket messages until disconnect."""
        # Set @twicc_used once, on the first input to a (non-hybrid) tmux session, so the tmux
        # reaper spares it. Local to the loop → persists across iterations, fires at most once.
        marked_used = False
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
                        # First real input → mark the session "used" for the tmux reaper. Skip
                        # hybrid (separate socket, out of the reaper's scope). Once per connection.
                        if use_tmux and not hybrid_attach and not marked_used:
                            marked_used = True
                            await asyncio.to_thread(
                                tmux_set_option, terminal_context, TMUX_USED_OPTION, "1",
                                terminal_index=terminal_index,
                            )

                elif msg_type == "resize":
                    cols = msg.get("cols", DEFAULT_COLS)
                    rows = msg.get("rows", DEFAULT_ROWS)
                    if not pty_dead:
                        try:
                            set_winsize(master_fd, cols, rows)
                        except OSError:
                            pass

                elif msg_type == "tmux_scroll" and use_tmux:
                    scroll_lines = msg.get("lines", 0)
                    if scroll_lines:
                        pos, size = await asyncio.to_thread(
                            _tmux_scroll, terminal_context, scroll_lines, terminal_index=terminal_index,
                        )
                        if pos is not None:
                            await send({"type": "websocket.send", "text": json.dumps({
                                "type": "scroll_result",
                                "requested": scroll_lines,
                                "scroll_position": pos,
                                "history_size": size,
                            })})

            elif message["type"] == "websocket.disconnect":
                return

    recv_task = asyncio.create_task(receive_loop())

    # ── tmux pane monitor (detects alternate screen changes) ─────────
    monitor_task = None
    if use_tmux:
        monitor_task = asyncio.create_task(_tmux_pane_monitor(terminal_context, send, terminal_index=terminal_index))

    # ── Broadcast terminal_created to all clients ─────────────────────
    try:
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "terminal_created",
                        "terminal_context": terminal_context,
                        "terminal_index": terminal_index,
                    },
                },
            )
    except Exception:
        logger.debug("Failed to broadcast terminal_created", exc_info=True)

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

        # If the PTY died (sender finished first), notify the client then close
        if sender_task in done:
            try:
                import orjson
                await send({"type": "websocket.send", "text": orjson.dumps({"type": "pty_exited"}).decode()})
            except Exception:
                pass
            try:
                await send({"type": "websocket.close", "code": 1000})
            except Exception:
                pass

    except Exception:
        logger.exception("Error in terminal WebSocket for terminal %s", terminal_context)
    finally:
        # ── Cleanup ───────────────────────────────────────────────────
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        cleanup_pty(master_fd, child_pid)
