"""Derive the MCP tool list from the CLI's Click tree.

Selection rule: the RPC registry (everything the CLI exposes minus
local-only) minus the ``settings`` group (no skill and no MCP tool — though
the CLI stays reachable from a session, an accepted property of the trust
model; see the agent-sharing design §5.2/A17) plus ``whoami`` (local-only for
/rpc/ because PID ancestry is meaningless over HTTP — but the MCP dispatcher
injects the caller identity, making it THE discovery primitive).

Naming: registry path with ``/`` and ``-`` mapped to ``_``
(``update-session/settings`` → ``update_session_settings``). Claude prefixes
these as ``mcp__twicc__<name>``.
"""

from __future__ import annotations

from functools import cache

import click
from mcp import types as mcp_types

from twicc.cli._local_only import LOCAL_ONLY_COMMANDS
from twicc.mcp.batch_contract import BATCH_INPUT_SCHEMAS, BATCH_OUTPUT_SCHEMA, BATCH_NAMES
from twicc.rpc.generator import CommandSpec, build_registry
from twicc.rpc.invoker import get_command
from twicc.rpc.permissions import COOKIE_READONLY_COMMANDS

# The MCP surface: local-only minus whoami, plus the settings group.
MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
    (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings"}
)

# Read-only annotation source and enforced eligibility for batch_read.
# This does not change individual-tool availability.
# Every tool is exposed in every mode (D9); `readOnlyHint` is honest metadata
# for clients (and on Codex it feeds `requires_mcp_tool_approval`, though our
# `default_tools_approval_mode="approve"` makes that moot). COOKIE_READONLY_COMMANDS
# is the vetted fail-closed list; the session read subviews, whoami, and share
# list/show are pure reads. Share reads stay out of COOKIE_READONLY_COMMANDS:
# the owner UI uses `/api/shares/`, and the cookie list remains fail-closed.
MCP_READ_ONLY_PATHS: frozenset[str] = COOKIE_READONLY_COMMANDS | frozenset(
    {"session/plan", "session/workflows", "session/workflow", "whoami", "share", "share/show"}
)

# Hot tools Claude should never defer (Tool Search loads names only for the
# rest). Keep this list tiny — every entry is permanent context.
ALWAYS_LOAD_PATHS: frozenset[str] = frozenset(
    {"whoami", "create-session", "send-message", "sessions", "session"}
)


def tool_name_for(path: str) -> str:
    return path.replace("/", "_").replace("-", "_")


@cache
def build_mcp_registry() -> dict[str, CommandSpec]:
    """path → CommandSpec for the MCP-exposed surface."""
    return build_registry(excluded_roots=MCP_EXCLUDED_ROOTS)


@cache
def tools_by_name() -> dict[str, CommandSpec]:
    return {tool_name_for(p): spec for p, spec in build_mcp_registry().items()}


def _click_leaf(path: str) -> click.Command:
    cmd: click.Command = get_command()
    for token in path.split("/"):
        if isinstance(cmd, click.Group) and token in cmd.commands:
            cmd = cmd.commands[token]
    return cmd


def _description_for(path: str, spec: CommandSpec) -> str:
    help_text = (_click_leaf(path).help or "").strip()
    return help_text or spec.summary


@cache
def iter_mcp_tools() -> list[mcp_types.Tool]:
    out: list[mcp_types.Tool] = []
    for path, spec in sorted(build_mcp_registry().items()):
        meta = {"anthropic/alwaysLoad": True} if path in ALWAYS_LOAD_PATHS else None
        out.append(
            mcp_types.Tool(
                name=tool_name_for(path),
                description=_description_for(path, spec),
                input_schema=spec.json_schema,
                annotations=mcp_types.ToolAnnotations(
                    read_only_hint=path in MCP_READ_ONLY_PATHS,
                ),
                _meta=meta,
            )
        )
    if BATCH_NAMES & {tool.name for tool in out}:
        raise RuntimeError("Generated MCP command collides with a batch tool.")
    for name in sorted(BATCH_NAMES):
        read = name == "batch_read"
        out.append(mcp_types.Tool(
            name=name,
            description=(
                "Run up to 20 read-only commands in one call. Parallel by default (4 at once); "
                "mode=sequential is also available. on_error defaults to continue. "
                if read else
                "Run up to 20 commands in order through one call. Sequential only. "
                "on_error defaults to stop; continue is available. "
            ) + "Discover each command's schema first. Each calls entry needs id, name, arguments. "
            "All inputs validate before execution. Results retain input order. "
            "No rollback, result references, or automatic retries. Keep long waits separate. "
            "A timeout does not prove writes stopped. Responses over 384 KiB are explicitly omitted. "
            "Returns a batch aggregate, not a single CLI envelope. Parallel mode requires on_error=continue.",
            input_schema=BATCH_INPUT_SCHEMAS[name], output_schema=BATCH_OUTPUT_SCHEMA,
            annotations=mcp_types.ToolAnnotations(
                read_only_hint=read, destructive_hint=not read, idempotent_hint=read, open_world_hint=True,
            ),
        ))
    return out
