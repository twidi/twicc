"""Async Django views for the /rpc/ API: dispatch, index, openapi."""

from __future__ import annotations

import asyncio
import logging

import orjson
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse

from twicc.rpc.generator import build_registry, render_argv
from twicc.rpc.invoker import invoke
from twicc.rpc.permissions import RPC_SCOPE_FULL, RPC_SCOPE_READ, cookie_scope_allows

# Child of the ``twicc`` logger, so it inherits the file handler
# (backend.log) — unlike ``django.request``, which is not wired to it.
logger = logging.getLogger(__name__)

# Returned (403) when a cookie-authenticated (read-scope) caller tries a command
# that is not in the read-only allowlist, or uses the ``argv`` escape hatch.
_READ_SCOPE_DENIED = (
    "This command requires an API token. Session-cookie authentication "
    "(e.g. from an artifact page) is limited to read-only commands."
)


def _json(payload, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        orjson.dumps(payload, option=orjson.OPT_INDENT_2),
        status=status,
        content_type="application/json",
    )


def _validate_body(spec, body) -> tuple[bool, dict | None]:
    """Lightweight in-house validation (presence + unknown fields)."""
    if not isinstance(body, dict):
        return False, {"error": "Body must be a JSON object."}
    props = spec.json_schema["properties"]
    unknown = sorted(k for k in body if k not in props)
    if unknown:
        return False, {"error": f"Unknown field(s): {', '.join(unknown)}"}
    missing = [r for r in spec.json_schema.get("required", []) if r not in body]
    if missing:
        return False, {"error": f"Missing required field(s): {', '.join(missing)}"}
    return True, None


def _run_invoke(argv: list[str]):
    # The invoker runs sync command logic (ORM reads / drop-file + poll) in this
    # worker thread. Bracket with close_old_connections() so per-thread DB
    # connections from read commands are not leaked.
    close_old_connections()
    try:
        return invoke(argv)
    finally:
        close_old_connections()


async def dispatch(request: HttpRequest, command_path: str) -> HttpResponse:
    if request.method != "POST":
        return _json({"error": "Use POST."}, status=405)
    spec = build_registry().get(command_path.rstrip("/"))
    if spec is None:
        return _json({"error": f"Unknown command: {command_path}"}, status=404)

    # Authorization. The middleware authenticated the request and tagged its
    # scope; a missing tag means the gate was bypassed for an unprotected
    # instance, i.e. full access. A read-scope (session-cookie) caller may only
    # reach the read-only allowlist — and never the ``argv`` escape hatch below,
    # which would otherwise let it name any command regardless of the URL.
    scope = getattr(request, "rpc_scope", RPC_SCOPE_FULL)
    if scope == RPC_SCOPE_READ and not cookie_scope_allows(command_path):
        return _json({"error": _READ_SCOPE_DENIED}, status=403)

    raw = request.body
    if not raw:
        body = {}
    else:
        try:
            body = orjson.loads(raw)
        except ValueError:
            # Only an explicit application/json content-type makes an
            # unparseable body an error; otherwise (test-client multipart,
            # stray form posts) fall back to empty so a valid JSON body sent
            # without the header is still honored above, and junk is ignored.
            if (request.content_type or "").startswith("application/json"):
                return _json({"error": "Malformed JSON body."}, status=400)
            body = {}

    # Forward-compat: the future --remote forwarder sends {"argv": [...]}.
    # The argv form bypasses body→argv rendering, so it must be allowlist-checked
    # itself: non-empty, and its first token must be an exposed top-level command
    # (denylisted commands are absent from the registry, hence rejected).
    if isinstance(body, dict) and set(body) == {"argv"} and isinstance(body["argv"], list):
        # The argv form picks the executed command from the body, bypassing the
        # URL→command mapping the read-scope check above relies on. Reserve it
        # for token callers so a cookie session can't smuggle a write command
        # through a read-only URL.
        if scope == RPC_SCOPE_READ:
            return _json({"error": _READ_SCOPE_DENIED}, status=403)
        argv = [str(x) for x in body["argv"]]
        allowed_roots = {path.split("/", 1)[0] for path in build_registry()}
        if not argv or argv[0] not in allowed_roots:
            return _json(
                {"error": "argv must start with an allowed command."}, status=400
            )
    else:
        ok, err = _validate_body(spec, body)
        if not ok:
            return _json(err, status=400)
        argv = render_argv(spec, body)

    # Execute in-process: set the transport seam's backend_loop so any mutating
    # command runs its drop-request payload straight through the service handlers
    # on this loop (no drop file, no polling). Reads are unaffected. Identity is
    # deliberately NOT bound here — /rpc/ callers are not sessions, so self/parent
    # keep failing with the clean "no TwiCC session found" error, as today.
    from twicc.cli._drop_request import transport

    loop = asyncio.get_running_loop()
    token = transport.backend_loop.set(loop)
    try:
        result = await asyncio.to_thread(_run_invoke, argv)
    except Exception:
        # An exception reaching here is unexpected: invoke() already converts
        # Click/Typer failures into an InvocationResult. Anything else (ORM
        # errors, a bug in a command body, ...) would otherwise surface as a
        # bare Django 500 whose traceback never reaches backend.log — the
        # django.request logger is not wired to the file handler and DEBUG is
        # off in production. Log the full traceback ourselves and return a
        # structured 500 so the root cause is recoverable from the logs.
        logger.exception("RPC command %r failed (argv=%r)", command_path, argv)
        return _json({"error": "Internal error while executing the command."}, status=500)
    finally:
        transport.backend_loop.reset(token)
    return _json({"exit_code": result.exit_code, "result": result.result, "error": result.error})


async def index(request: HttpRequest) -> HttpResponse:
    reg = build_registry()
    return _json(
        {
            "commands": [{"path": p, "summary": s.summary} for p, s in sorted(reg.items())],
            "openapi": "/rpc/openapi.json",
        }
    )


async def openapi(request: HttpRequest) -> HttpResponse:
    from twicc.rpc.openapi import build_openapi

    return _json(build_openapi(build_registry()))
