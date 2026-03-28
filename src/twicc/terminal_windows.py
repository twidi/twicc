"""
Windows ConPTY-based terminal handler for WebSocket sessions.

Replaces the Unix PTY implementation (terminal.py) on Windows.
Uses pywinpty for ConPTY access and a background thread for reading,
since Windows' ProactorEventLoop does not support add_reader().

Same WebSocket protocol as terminal.py:
  Client → Server (JSON text frames):
    { "type": "input", "data": "ls -la\n" }
    { "type": "resize", "cols": 120, "rows": 30 }

  Server → Client (plain text frames):
    Raw terminal output (no JSON wrapping).
"""

import asyncio
import json
import logging
import os
import shutil
import threading

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.env import purge_claude_code_vars

logger = logging.getLogger(__name__)

WS_CLOSE_AUTH_FAILURE = 4001

DEFAULT_COLS = 80
DEFAULT_ROWS = 24


def kill_tmux_session(session_id: str) -> bool:
    """No-op on Windows — tmux does not exist."""
    return False


@sync_to_async
def _get_session_info(session_id: str) -> tuple[str, bool]:
    """Resolve working directory and archived status (same logic as Unix version)."""
    from twicc.core.models import Session

    home = os.path.expanduser("~")

    try:
        session = Session.objects.select_related("project").get(id=session_id)
    except Session.DoesNotExist:
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


def _find_shell() -> str:
    """Find a suitable shell on Windows."""
    # Prefer PowerShell, fall back to cmd.exe
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh:
        return pwsh
    return os.environ.get("COMSPEC", "cmd.exe")


def _env_dict_to_block(env: dict) -> str:
    """Convert an env dict to the NUL-separated string format required by pywinpty/CreateProcessW."""
    return "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"


def _spawn_pty(cwd: str, cols: int, rows: int):
    """Spawn a ConPTY process via pywinpty.

    Returns a winpty.PTY instance.
    """
    import winpty

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    purge_claude_code_vars(env)

    shell = _find_shell()

    pty = winpty.PTY(cols, rows)
    pty.spawn(shell, cwd=cwd, env=_env_dict_to_block(env))
    return pty


def _pty_reader_thread(pty, output_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, stop_event: threading.Event):
    """Background thread that reads from the ConPTY and pushes data to the async queue.

    Runs until the process exits or stop_event is set.
    """
    import winpty

    while not stop_event.is_set():
        try:
            if not pty.isalive():
                loop.call_soon_threadsafe(output_queue.put_nowait, None)
                return
            data = pty.read(blocking=False)
            if data:
                loop.call_soon_threadsafe(output_queue.put_nowait, data)
            else:
                # No data available, brief sleep to avoid busy-waiting
                stop_event.wait(0.01)
        except winpty.WinptyError:
            loop.call_soon_threadsafe(output_queue.put_nowait, None)
            return
        except Exception:
            loop.call_soon_threadsafe(output_queue.put_nowait, None)
            return


async def terminal_application(scope, receive, send):
    """Raw ASGI WebSocket handler for terminal sessions on Windows.

    Same interface as the Unix version in terminal.py.
    """
    assert scope["type"] == "websocket"

    message = await receive()
    if message["type"] != "websocket.connect":
        return

    # Authentication
    if settings.TWICC_PASSWORD_HASH:
        session = scope.get("session", {})
        is_authenticated = await sync_to_async(session.get)("authenticated")
        if not is_authenticated:
            logger.warning("Terminal WebSocket rejected: not authenticated")
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": json.dumps({"type": "auth_failure"})})
            await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_FAILURE})
            return

    session_id = scope["url_route"]["kwargs"]["session_id"]
    cwd, archived = await _get_session_info(session_id)

    # Spawn ConPTY
    try:
        pty = _spawn_pty(cwd, DEFAULT_COLS, DEFAULT_ROWS)
    except Exception:
        logger.exception("Failed to spawn ConPTY for session %s", session_id)
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.send", "text": "\r\nError: failed to start shell.\r\n"})
        await send({"type": "websocket.close", "code": 1011})
        return

    await send({"type": "websocket.accept"})

    # PTY output reader via background thread + asyncio.Queue
    output_queue = asyncio.Queue()
    stop_event = threading.Event()
    loop = asyncio.get_running_loop()

    reader_thread = threading.Thread(
        target=_pty_reader_thread,
        args=(pty, output_queue, loop, stop_event),
        daemon=True,
    )
    reader_thread.start()

    async def pty_output_sender():
        """Drain output_queue and send to WebSocket."""
        try:
            while True:
                data = await output_queue.get()
                if data is None:
                    await send({"type": "websocket.send", "text": "\r\n[Process exited]\r\n"})
                    return
                # pywinpty returns str directly
                text = data if isinstance(data, str) else data.decode(errors="replace")
                await send({"type": "websocket.send", "text": text})
        except Exception:
            return

    sender_task = asyncio.create_task(pty_output_sender())

    async def receive_loop():
        """Process incoming WebSocket messages."""
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
                    if data and pty.isalive():
                        try:
                            pty.write(data)
                        except Exception:
                            return

                elif msg_type == "resize":
                    cols = msg.get("cols", DEFAULT_COLS)
                    rows = msg.get("rows", DEFAULT_ROWS)
                    if pty.isalive():
                        try:
                            pty.set_size(cols, rows)
                        except Exception:
                            pass

            elif message["type"] == "websocket.disconnect":
                return

    recv_task = asyncio.create_task(receive_loop())

    try:
        done, pending = await asyncio.wait(
            [sender_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if sender_task in done:
            try:
                await send({"type": "websocket.close", "code": 1000})
            except Exception:
                pass

    except Exception:
        logger.exception("Error in terminal WebSocket for session %s", session_id)
    finally:
        stop_event.set()
        # pywinpty has no close() — deleting the object releases the ConPTY handle
        del pty
