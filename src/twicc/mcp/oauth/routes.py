"""Small public OAuth surface using the official SDK protocol handlers."""

import base64
import hmac
import secrets
from urllib.parse import unquote, urlencode

import orjson
from django.db import transaction
from django.utils import timezone
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from twicc.core.models import McpOAuthClient, McpOAuthCredential, McpOAuthRequest
from .config import RESOURCE_METADATA, SERVER_METADATA, base_url, issuer_url, resource_url
from .provider import TokenExchangeProvider, provider, valid_redirect
from .storage import digest, issue, write
from . import protection

# The public OAuth application owns only these exact paths. Check this before
# preflight handling, request-body reads and rate limiting, including when the
# dedicated-host gate forwards an unrelated path here.
OAUTH_PATHS = frozenset({
    RESOURCE_METADATA,
    SERVER_METADATA,
    "/mcp/oauth/authorize",
    "/mcp/oauth/wait",
    "/mcp/oauth/continue",
    "/mcp/oauth/register",
    "/mcp/oauth/token",
    "/mcp/oauth/revoke",
})


def error(message, status=400, code="invalid_request"):
    return JSONResponse({"error": code, "error_description": message}, status_code=status)


class Authenticator:
    """SDK hook with hashed secrets and standard Basic-only client IDs."""

    async def authenticate_request(self, request):
        form = await request.form()
        client_id, secret = form.get("client_id"), form.get("client_secret", "")
        basic = request.headers.get("authorization", "")
        method = "client_secret_post" if secret else "none"
        if basic:
            try:
                if not basic.startswith("Basic "):
                    raise ValueError()
                basic_id, secret = base64.b64decode(basic[6:], validate=True).decode().split(":", 1)
                basic_id, secret = unquote(basic_id), unquote(secret)
                if client_id and client_id != basic_id:
                    raise ValueError()
                client_id, method = basic_id, "client_secret_basic"
                # SDK token models require client_id in the parsed form.
                from starlette.datastructures import FormData

                request._form = FormData({**dict(form), "client_id": client_id})
            except (ValueError, UnicodeError):
                raise AuthenticationError("Invalid Basic authentication") from None
        # Token and revocation requests never register a client or fetch CIMD.
        client = await provider.get_client(str(client_id or ""), allow_registration=False)
        if not client or client.token_endpoint_auth_method != method:
            raise AuthenticationError("Invalid client authentication")
        row = await McpOAuthClient.objects.filter(pk=client.client_id).afirst()
        if method != "none" and (not row.secret_hash or not hmac.compare_digest(row.secret_hash, digest(str(secret)))):
            raise AuthenticationError("Invalid client secret")
        return client


def continuation(data):
    with transaction.atomic():
        row = McpOAuthRequest.objects.select_related("connection").filter(pk=data.get("id")).first()
        if not row or not hmac.compare_digest(row.continuation_hash, digest(str(data.get("handle", "")))):
            return {"state": "expired"}
        if row.expires_at <= timezone.now() or row.state in ("completed", "expired"):
            return {"state": "expired"}
        if row.state == "pending":
            return {"state": "pending"}
        values = {"state": row.params.get("state"), "iss": issuer_url()}
        if row.state == "approved" and row.connection and not row.connection.revoked_at:
            params = {
                key: row.params[key]
                for key in ("redirect_uri", "redirect_uri_provided_explicitly", "code_challenge", "resource")
            }
            values["code"] = issue(row.connection, "code", 60, params)
        else:
            values["error"] = "access_denied"
        row.state = "completed"
        row.save(update_fields=["state"])
        uri = row.params["redirect_uri"]
        return {
            "state": "complete",
            "redirect": uri
            + ("&" if "?" in uri else "?")
            + urlencode({k: v for k, v in values.items() if v is not None}),
        }


WAIT_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Authorize TwiCC MCP</title><style>:root{color-scheme:light dark}
body{font:18px system-ui;max-width:38rem;margin:10vh auto;padding:24px}
code{font-size:2em;letter-spacing:.15em}p{line-height:1.5}</style>
<h1>Authorize TwiCC MCP</h1><p>Open your TwiCC instance on any device. Review the pending MCP request and enter this code:</p>
<code id="code"></code><p id="status">Waiting for your decision. Keep this page open.</p>
<script>
const key='twicc-mcp-continuation';
let parts=location.hash.slice(1).split(':');
if(parts.length===3){sessionStorage.setItem(key,JSON.stringify(parts));history.replaceState(null,'',location.pathname)}
else{try{parts=JSON.parse(sessionStorage.getItem(key))||[]}catch{parts=[]}}
document.getElementById('code').textContent=parts[2]||'Expired';
async function poll(){if(parts.length!==3)return;
try{const r=await fetch('continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:parts[0],handle:parts[1]})});
if(!r.ok)throw Error();const d=await r.json();if(d.redirect){sessionStorage.removeItem(key);location.replace(d.redirect);return}
if(d.state==='expired'){sessionStorage.removeItem(key);document.getElementById('status').textContent='This request expired. Start a new connection.';return}}
catch{document.getElementById('status').textContent='Waiting for TwiCC. Retrying…'}setTimeout(poll,2000)}poll();
</script></html>"""


async def handle(request):
    path, method = request.url.path, request.method
    if path == RESOURCE_METADATA and method == "GET":
        return JSONResponse(
            {"resource": resource_url(), "authorization_servers": [issuer_url()], "scopes_supported": ["twicc:full"]}
        )
    if path == SERVER_METADATA and method == "GET":
        issuer = issuer_url()
        return JSONResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": issuer + "/authorize",
                "token_endpoint": issuer + "/token",
                "registration_endpoint": issuer + "/register",
                "revocation_endpoint": issuer + "/revoke",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "scopes_supported": ["twicc:full"],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "code_challenge_methods_supported": ["S256"],
                "client_id_metadata_document_supported": True,
                "authorization_response_iss_parameter_supported": True,
            }
        )
    if path == "/mcp/oauth/wait" and method == "GET":
        return HTMLResponse(
            WAIT_PAGE,
            headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            },
        )
    if path == "/mcp/oauth/continue" and method == "POST":
        data = await request.json()
        if not isinstance(data, dict):
            return error("Expected an object")
        return JSONResponse(await write(lambda: continuation(data)))
    if path == "/mcp/oauth/authorize" and method in ("GET", "POST"):
        params = request.query_params if method == "GET" else await request.form()
        if params.get("code_challenge_method") != "S256":
            return error("Explicit PKCE S256 is required")
        response = await AuthorizationHandler(provider).handle(request)
        # Include issuer identification on error redirects generated by the SDK too.
        location = response.headers.get("location")
        if location and not location.startswith(base_url() + "/mcp/oauth/wait#"):
            response.headers["location"] = (
                location + ("&" if "?" in location else "?") + urlencode({"iss": issuer_url()})
            )
        return response
    if path == "/mcp/oauth/register" and method == "POST":
        data = await request.json()
        if not isinstance(data, dict):
            return error("Expected an object")
        method = data.get("token_endpoint_auth_method", "client_secret_post")
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            return error("Unsupported client authentication method")
        client = OAuthClientInformationFull.model_validate(
            {
                **data,
                "client_id": secrets.token_urlsafe(24),
                "client_secret": None if method == "none" else secrets.token_urlsafe(32),
                "token_endpoint_auth_method": method,
                "scope": "twicc:full",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }
        )
        if (
            not client.redirect_uris
            or len(client.redirect_uris) > 10
            or not all(valid_redirect(u) for u in client.redirect_uris)
        ):
            return error("Valid HTTPS or localhost redirect URIs are required")
        if not await provider.register_client(client):
            return limited(60, "Client registration capacity reached. Try again later.")
        return JSONResponse(client.model_dump(mode="json", exclude_none=True), status_code=201)
    if path == "/mcp/oauth/token" and method == "POST":
        form = await request.form()
        if form.get("resource") != resource_url():
            return error("Use the advertised MCP resource", code="invalid_target")
        token_provider = TokenExchangeProvider(redirect_uri_present="redirect_uri" in form)
        return await TokenHandler(token_provider, Authenticator()).handle(request)
    if path == "/mcp/oauth/revoke" and method == "POST":
        try:
            client = await Authenticator().authenticate_request(request)
        except AuthenticationError:
            return error("Invalid client", 401, "invalid_client")
        form = await request.form()
        token = await provider.load_refresh_token(client, str(form.get("token", "")))
        if token is None:
            token = await provider.load_access_token(str(form.get("token", "")))
        if token and token.client_id == client.client_id:
            await provider.revoke_token(token)
        return Response(status_code=200)
    return Response(status_code=404)


def limited(seconds, message="OAuth request limit reached. Try again later."):
    response = error(message, 429, "temporarily_unavailable")
    response.headers["Retry-After"] = str(seconds)
    return response


async def budget_identity(request, category):
    """Only possession of a live secret gets a separate authorized budget.

    A client_id, including an approved client's public ID, is never sufficient.
    The actual SDK authentication and grant validation still run afterwards.
    """
    if request.method != "POST":
        return None
    if category in ("token", "revoke"):
        form = await request.form()
        value = form.get("token") if category == "revoke" else form.get("refresh_token") or form.get("code")
        if not isinstance(value, str) or not value:
            return None
        row = await McpOAuthCredential.objects.filter(
            digest=digest(value), expires_at__gt=timezone.now(),
            connection__revoked_at__isnull=True, connection__resource=resource_url(),
        ).values_list("connection_id", flat=True).afirst()
        return f"grant:{row}" if row else None
    if category == "continue":
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            return None
        row = await McpOAuthRequest.objects.filter(
            pk=data["id"], continuation_hash=digest(str(data.get("handle", ""))),
            expires_at__gt=timezone.now(), state__in=["pending", "approved", "refused"],
        ).values_list("id", flat=True).afirst()
        return f"request:{row}" if row else None
    return None


async def process(request, source):
    # Cheap static prerequisites carry no secret that could identify the grant.
    # Keep them available so a discovery flood cannot block an admitted flow.
    if request.method == "OPTIONS":
        return Response(status_code=204)
    if request.method == "GET" and request.url.path == "/mcp/oauth/wait":
        return await handle(request)
    category = request.url.path.rsplit("/", 1)[-1]
    if category not in protection.RATE_LIMITS:
        category = "discovery"
    # Cheap admission checks precede parsing and CIMD network requests.
    if category in protection.ADMISSION or category == "discovery":
        retry = protection.protection.check(category, source)
        if retry:
            return limited(retry)
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 65536:
            return error("Request too large", 413)
    request._body = bytes(body)
    if category not in protection.ADMISSION and category != "discovery":
        identity = await budget_identity(request, category)
        retry = protection.protection.check(category, source, identity)
        if retry:
            return limited(retry)
    return await handle(request)


async def application(scope, receive, send):
    if scope.get("path") not in OAUTH_PATHS or not base_url():
        await Response(status_code=404)(scope, receive, send)
        return
    request = Request(scope, receive)
    source = protection.source_hash(scope)
    context = protection.request_source.set(source)
    try:
        response = await process(request, source)
    except (ValueError, orjson.JSONDecodeError):
        response = error("Invalid request")
    finally:
        protection.request_source.reset(context)
        await protection.protection.publish()
    response.headers.update(
        {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
    )
    if request.url.path not in ("/mcp/oauth/authorize", "/mcp/oauth/wait", "/mcp/oauth/continue"):
        response.headers.update(
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version",
                "Access-Control-Expose-Headers": "Retry-After",
            }
        )
    await response(scope, receive, send)
