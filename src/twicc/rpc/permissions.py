"""Authorization policy for the ``/rpc/`` API.

``RpcTokenAuthMiddleware`` authenticates a request and tags it with a *scope*;
the dispatch view then authorizes the actual command against that scope.

Two scopes, two kinds of authority:

- ``RPC_SCOPE_FULL`` — a valid Bearer **token** (a deliberately-held secret =
  a *capability*), or protection disabled entirely. Every command is allowed.
- ``RPC_SCOPE_READ`` — the SPA's **session cookie** (*ambient authority*: the
  browser attaches it to every same-origin request, including from rendered
  artifact pages whose JavaScript provenance is untrusted). Only the read-only
  commands in :data:`COOKIE_READONLY_COMMANDS` are allowed.

Why the asymmetry. A same-origin artifact page carries the logged-in user's
cookie automatically, so any code in it (possibly authored by a restricted
agent) could reach ``/rpc/`` with the user's identity. Read commands grant such
code nothing it couldn't already obtain — a read-only / untrusted agent can
already read the same data from disk, and an artifact is already an
exfiltration channel. But **write** commands (``create-session``,
``send-message``, ``*/stop``, ``update-*``, ``create/delete-*``,
``artifacts bookmark/unbookmark``) are a brand-new capability: they would let
that code spawn or drive an *unrestricted* agent, kill processes, or mutate
state — a privilege escalation the agent has no other way to perform. So
mutation requires a token; ambient authority is read-only.

The allowlist is **fail-closed**: a command absent from it is token-only, so
any newly added command stays write-protected until it is explicitly vetted and
listed here. ``tests/test_rpc_auth.py`` asserts every entry is a real registry
path and that known write commands are never present.
"""

from __future__ import annotations

# Request-scope tags set by ``RpcTokenAuthMiddleware`` and read by the dispatch
# view. Plain strings (not an enum) to keep this a dependency-free leaf module.
RPC_SCOPE_FULL = "full"
RPC_SCOPE_READ = "read"

# Read-only RPC command paths reachable with an ambient session cookie. Keys are
# registry paths (see ``twicc.rpc.generator.build_registry``): the bare group
# name for a listing (e.g. ``sessions``), ``group/subcommand`` otherwise.
#
# ``processes/wait``, ``process/wait`` and ``session/wait-reply`` are long-polls:
# they block but never mutate, so they qualify as reads under the
# mutation-is-the-line rule. ``session/wait-reply`` matters twice over: ``batch_read``
# only accepts reads, and it is the primitive that gives one cursor per call —
# which is how a caller waits on several sessions at once, since the plural
# command is deliberately not built.
# ``artifacts`` is the listing only — ``artifacts/bookmark`` and
# ``artifacts/unbookmark`` write and are deliberately excluded.
COOKIE_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "projects",
        "projects/get",
        "project",
        "workspaces",
        "workspaces/get",
        "workspace",
        "sessions",
        "sessions/get",
        "session",
        "session/content",
        "session/messages",
        "session/agents",
        "session/wait-reply",
        "session/pending-request",
        "artifacts",
        "processes",
        "processes/get",
        "processes/wait",
        "process",
        "process/wait",
        "topology",
        "search",
        "status",
        "usage",
        "info",
        "peers",
        "peer-message",
    }
)


def cookie_scope_allows(command_path: str) -> bool:
    """Whether a cookie-authenticated (read-scope) caller may run ``command_path``.

    ``command_path`` is matched after stripping a trailing slash, mirroring how
    the dispatch view resolves it against the registry.
    """
    return command_path.rstrip("/") in COOKIE_READONLY_COMMANDS
