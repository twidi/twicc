"""The lowlevel MCP server: tool listing + in-process command dispatch.

The internal ``Server`` instance serves every agent session; per-call identity comes
from the ``Authorization`` header of the underlying HTTP request (available
via the handler's ``ctx.request`` on the streamable-HTTP transport) and is
bound into two ContextVars before the command runs in a worker thread:

- ``whoami.forced_session_id`` — makes ``self``/``parent``/``whoami``/
  ``spawned_by`` auto-fill resolve to the calling session;
- ``transport.backend_loop`` — routes mutations straight to the drop-request
  service handlers on this event loop instead of the drop-file dance.

The tool result is the same envelope as ``POST /rpc/<command>``:
``{"exit_code": int, "result": ..., "error": ...}`` — returned as MCP
structured content. Non-zero exit codes are data, not MCP errors (parity with
the CLI/skills contract agents already know).
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from uuid import uuid4
from collections.abc import Callable

import jsonschema
import orjson
from mcp import types as mcp_types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from twicc.cli._drop_request import transport
from twicc.cli._drop_request.whoami import forced_session_id
from twicc.mcp.identity import resolve_session_token, external_caller, batch_correlation, mcp_call
from twicc.mcp.batch import BatchRuntime
from twicc.mcp.batch_contract import BATCH_NAMES, validate_batch, fit_result, rejected_batch
from twicc.mcp.dispatch import PreparedTool, UnknownToolError, check_caller_arguments, prepare_tool
from twicc.mcp.tools import iter_mcp_tools, tools_by_name, MCP_READ_ONLY_PATHS
from twicc.rpc.generator import render_argv
from twicc.rpc.views import _run_invoke

logger = logging.getLogger(__name__)


INSTRUCTIONS = """\
Ordinary tools are the TwiCC CLI (`twicc <command>`), one tool per command; the
`twicc-*` skills document the same surface in depth. Results are the CLI's
JSON wrapped in {"exit_code", "result", "error"} — exit_code 0 is success,
non-zero maps to the exit codes the skills document (3 rejected, 4 failed,
5 timeout, ...).

You are reading this because the TwiCC MCP server is connected, so its whole
tool set is available to you — but most schemas are deferred (all of them on
Codex, all but a handful on Claude Code). A tool missing from your visible tool
list is therefore not a missing tool: search your full tool list for the one you
need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex) instead of falling back
to the `twicc` shell CLI.

Conventions:
- Session-targeting parameters accept `self` (your own session) and/or
  `parent` (the session that spawned you) where their parameter description
  says so; the connection carries the identity needed to resolve them,
  so `whoami` works and `create_session` records you as the spawner.
- Always pass absolute paths (directories, attachments): tools execute inside
  the TwiCC backend, whose working directory is not yours.
- Keep `*_wait` timeouts <= 300 seconds; poll again rather than exceeding them.
- Catalogues (models, presets, providers) drift: fetch them live with `info`.
"""


async def dispatch_tool(name: str, arguments: dict, *, session_id: str | None) -> dict:
    """Execute one tool call in-process; returns the RPC-style envelope."""
    spec = tools_by_name().get(name)
    if spec is None:
        raise UnknownToolError(name)
    check_caller_arguments(name, arguments, external=external_caller.get() is not None)
    return await execute_prepared(PreparedTool(name, spec, arguments), session_id=session_id)


async def execute_prepared(prepared: PreparedTool, *, session_id: str | None,
                           on_start: Callable[[], None] | None = None) -> dict:
    """Run a prepared command using the existing invocation and provenance path."""
    name, spec, arguments = prepared
    external = external_caller.get()
    argv = render_argv(spec, arguments)
    loop = asyncio.get_running_loop()
    tok_sid = forced_session_id.set(session_id)
    tok_loop = transport.backend_loop.set(loop)
    tok_mcp = mcp_call.set(True)
    try:
        if on_start is not None:
            on_start()
        result = await asyncio.to_thread(_run_invoke, argv)
    finally:
        mcp_call.reset(tok_mcp)
        transport.backend_loop.reset(tok_loop)
        forced_session_id.reset(tok_sid)
    if external is not None:
        from twicc.core.models import McpOperation
        from twicc.mcp.oauth.storage import write

        targets = {
            k: v
            for k, v in arguments.items()
            if k in {"session_id", "session_ids", "project", "project_id", "bookmark_id", "share_id", "peer"}
        }
        if isinstance(result.result, dict):
            # Only when something was actually identified. A listing's paginated
            # envelope is a dict carrying none of these keys, so without the
            # emptiness check every list call would file a bare ``"result": {}``
            # into the audit row.
            identified = {
                k: v
                for k, v in result.result.items()
                if k in {"id", "session_id", "project_id", "share_id", "bookmark_id", "message_id"}
            }
            if identified:
                targets["result"] = identified
        correlation = batch_correlation.get()
        if correlation is not None:
            targets["_batch"] = {
                "id": correlation.batch_id, "call_id": correlation.call_id, "index": correlation.index,
            }
        await write(
            lambda: McpOperation.objects.create(
                connection_id=external.connection_id, name=external.name, tool=name, targets=targets
            )
        )
    envelope = {"exit_code": result.exit_code, "result": result.result, "error": result.error}
    # Normalize to plain JSON-native types, exactly as the CLI (``_output.emit_json``)
    # and the ``/rpc/`` view (``views._json``) do. Command results carry orjson-native
    # objects (``datetime`` timestamps, ...) the MCP SDK would otherwise hand to stdlib
    # ``json.dumps`` (lowlevel/server.py), which raises "Object of type datetime is not
    # JSON serializable" and surfaces as a tool error. The orjson round-trip gives the
    # SDK the same ISO-string shape agents already get from the CLI/skills path.
    return orjson.loads(orjson.dumps(envelope))


def _session_id_from_request(ctx: ServerRequestContext) -> str | None:
    """Caller identity from the HTTP Authorization header, if session-bound."""
    request = getattr(ctx, "request", None)
    if request is None:
        return None
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    return resolve_session_token(token)


async def _list_tools(
    ctx: ServerRequestContext,
    params: mcp_types.PaginatedRequestParams | None,
) -> mcp_types.ListToolsResult:
    return mcp_types.ListToolsResult(tools=iter_mcp_tools())


_batch_runtime: BatchRuntime | None = None


def start_batch_runtime() -> BatchRuntime:
    global _batch_runtime
    from twicc.mcp.oauth.provider import batch_grant_valid

    async def execute(prepared, session_id, *, on_start):
        return await execute_prepared(prepared, session_id=session_id, on_start=on_start)

    _batch_runtime = BatchRuntime(execute=execute, check_grant=batch_grant_valid)
    return _batch_runtime


async def _call_batch(ctx, params, session_id):
    batch_id = str(uuid4())
    started = monotonic()
    try:
        validation = validate_batch(
            params.name, params.arguments, registry=tools_by_name(),
            read_only_paths=MCP_READ_ONLY_PATHS, external=external_caller.get() is not None, batch_id=batch_id,
        )
        request = getattr(ctx, "request", None)
        auth_ms = request.scope.get("twicc_mcp_auth_ms") if request is not None else None
        logger.info("MCP batch validated batch_id=%s auth_ms=%s validation_ms=%.3f rejected=%s", batch_id,
                    auth_ms, (monotonic() - started) * 1000, validation.rejection is not None)
        if validation.rejection is not None:
            payload = validation.rejection
        elif _batch_runtime is None:
            payload = rejected_batch(batch_id, "server_busy")
        else:
            payload = await _batch_runtime.run(validation.prepared, session_id=session_id)
        started = monotonic()
        result = fit_result(payload)
        logger.info("MCP batch serialized batch_id=%s elapsed_ms=%.3f", batch_id, (monotonic() - started) * 1000)
        return result
    except Exception:
        # Never route batch arguments or exception text to the single-call logger.
        logger.error("MCP batch failed batch_id=%s", batch_id)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="Batch result unavailable. Commands may have executed. "
                                          "Inspect affected resources before retrying.")], is_error=True,
        )


async def _call_tool(
    ctx: ServerRequestContext,
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    name, arguments = params.name, params.arguments or {}
    session_id = _session_id_from_request(ctx)
    if name in BATCH_NAMES:
        return await _call_batch(ctx, params, session_id)
    try:
        try:
            prepared = prepare_tool(name, arguments, registry=tools_by_name(), external=external_caller.get() is not None)
        except jsonschema.ValidationError as exc:
            return mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=f"Input validation error: {exc.message}")],
                is_error=True,
            )
        envelope = await execute_prepared(prepared, session_id=session_id)
    except UnknownToolError:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=f"Unknown tool: {name}")],
            is_error=True,
        )
    except Exception as exc:
        logger.exception("MCP tool %r failed (arguments=%r)", name, arguments)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(exc))],
            is_error=True,
        )
    # v1 wrapped dicts automatically; v2 lowlevel handlers own both representations.
    # A non-zero CLI exit_code remains business data, not an MCP tool error.
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=orjson.dumps(envelope, option=orjson.OPT_INDENT_2).decode())],
        structured_content=envelope,
        is_error=False,
    )


BATCH_INSTRUCTIONS = """
Batch tools are MCP-only wrappers. Discover each child's ordinary schema first.
Use batch_read for independent reads (parallel default, up to four at once), or batch for ordered commands.
Supply calls entries with id, name, arguments. All inputs validate before any command starts.
Results retain input order in a batch aggregate. Check ok, each status, and response_omitted.
Batch defaults to stop on execution failure; batch_read defaults to continue. Parallel stop is invalid.
No rollback, nested batches, result references, or automatic retries. Keep long waits separate.
A timeout/cancellation does not prove writes stopped; inspect resources before retrying uncertain writes.
Limits: 20 calls, 4 active batches, 8 active batch commands globally, 384 KiB per command response.
Oversized responses are explicitly omitted even on success; the complete tool result is below 16 MiB.
Long waits can occupy all batch capacity. Individual tools remain available outside batch admission.
"""
INSTRUCTIONS += BATCH_INSTRUCTIONS

_server: Server = Server(
    "twicc",
    instructions=INSTRUCTIONS,
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


# A tool call carrying attachments (``create_session``, ``send_message``, ...)
# is capped at 32 MB of files by both providers' ATTACHMENT_SUPPORT
# (providers/*/helpers.py) — ≈ 43 MB once base64-encoded, plus the prompt and
# the JSON-RPC envelope. The MCP SDK caps the HTTP body at 4 MiB by default,
# which would reject any real attachment with a 413.
MAX_REQUEST_BODY_BYTES = 48 * 1024 * 1024

_session_manager: StreamableHTTPSessionManager | None = None


def get_session_manager() -> StreamableHTTPSessionManager:
    """Process-wide singleton; created lazily, run by twicc.mcp.endpoint."""
    global _session_manager
    if _session_manager is None:
        _session_manager = StreamableHTTPSessionManager(
            app=_server,
            json_response=True,
            stateless=True,
            max_request_body_size=MAX_REQUEST_BODY_BYTES,
            # The Bearer token is the real gate (endpoint.py); Host/Origin
            # validation would only break worktree ports and tunnels.
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            ),
        )
    return _session_manager


EXTERNAL_INSTRUCTIONS = """TwiCC external MCP: tools run on the TwiCC host.
Use explicit session IDs; self, parent, and whoami are unavailable.
Paths refer to the server filesystem. Attachments can use base64 data URIs.
Ordinary results contain exit_code, result, and error. Use info for current models and settings.
"""


async def _external_list(ctx, params):
    tools = []
    for tool in iter_mcp_tools():
        if tool.name == "whoami":
            continue
        item = tool.model_copy(deep=True)
        item.description = (item.description or "") + "\nExternal MCP: use explicit IDs. No self or parent references."
        item.meta = {"securitySchemes": [{"type": "oauth2", "scopes": ["twicc:full"]}]}
        tools.append(item)
    return mcp_types.ListToolsResult(tools=tools)


_external_server = Server(
    "twicc", instructions=EXTERNAL_INSTRUCTIONS + BATCH_INSTRUCTIONS, on_list_tools=_external_list, on_call_tool=_call_tool
)
_external_manager = None


def get_external_session_manager():
    global _external_manager
    if _external_manager is None:
        _external_manager = StreamableHTTPSessionManager(
            app=_external_server,
            json_response=True,
            stateless=True,
            max_request_body_size=MAX_REQUEST_BODY_BYTES,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    return _external_manager
