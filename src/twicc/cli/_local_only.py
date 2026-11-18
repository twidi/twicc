"""Canonical set of CLI commands that stay local to the host.

These root commands are never exposed over the RPC HTTP API and (later) are
never forwarded over the ``--remote`` CLI forwarder. They only make sense
against the local machine running the command.

This is a leaf constant module: it must import nothing from the ``cli`` or
``rpc`` packages so that both can depend on it without an import cycle.
"""

from __future__ import annotations

# Why each command is local-only:
# - "password", "token": interactive secret management (must run on the host).
# - "user": account/grant management — an agent must never mint itself an admin.
# - "claude", "codex": provider passthroughs (spawn local provider CLIs).
# - "run": local process exec.
# - "whoami": host-bound caller identity (resolved from the local process).
LOCAL_ONLY_COMMANDS = frozenset({"password", "claude", "codex", "run", "token", "user", "whoami"})
