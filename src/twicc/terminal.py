"""
Raw ASGI WebSocket handler for interactive terminal sessions.

Bypasses Django Channels' AsyncWebsocketConsumer entirely to eliminate
the channel layer overhead on every message. The ASGI send/receive
callables are used directly, giving near-native WebSocket performance.

Provides a PTY-backed terminal per session, communicating over a dedicated
WebSocket endpoint (`/ws/terminal/<session_id>/`).

Protocol:
  Client → Server (JSON text frames):
    { "type": "input", "data": "ls -la\n" }       — keyboard input
    { "type": "resize", "cols": 120, "rows": 30 }  — terminal resize

  Server → Client (plain text frames):
    Raw PTY output (no JSON wrapping for performance).
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
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from django.conf import settings

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


@sync_to_async
def get_session_info(session_id: str) -> tuple[str, bool]:
    """Resolve the working directory and archived status for a terminal session.

    Returns (cwd, archived).

    Working directory priority order:
    1. Session.git_directory (resolved git root)
    2. Session.cwd (Claude's working directory)
    3. Session.project.directory (project root)
    4. ~ (home directory fallback)
    """
    from twicc.core.models import Session

    try:
        session = Session.objects.select_related("project").get(id=session_id)
        cwd = (
            session.git_directory
            or session.cwd
            or session.project.directory
            or os.path.expanduser("~")
        )
        return cwd, session.archived
    except Session.DoesNotExist:
        return os.path.expanduser("~"), False


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
    cwd, archived = await get_session_info(session_id)

    # Ensure cwd exists; fall back to home if it doesn't
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

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

            elif message["type"] == "websocket.disconnect":
                return

    recv_task = asyncio.create_task(receive_loop())

    try:
        # Wait for either the PTY to die or the client to disconnect
        done, pending = await asyncio.wait(
            [sender_task, recv_task],
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
        cleanup_pty(master_fd, child_pid)
