"""
Platform dispatcher for the terminal WebSocket handler.

Imports the Unix PTY-based implementation on Linux/macOS,
or the Windows ConPTY-based implementation on Windows.
"""

import sys

if sys.platform == "win32":
    from twicc.terminal_windows import terminal_application, kill_tmux_session  # noqa: F401
else:
    from twicc.terminal import terminal_application, kill_tmux_session  # noqa: F401
