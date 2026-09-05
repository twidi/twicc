"""MCP SDK provider backed by TwiCC models."""

import asyncio
import ipaddress
import re
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from django.utils import timezone
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizeError, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull

from twicc.core.models import McpConnection, McpOAuthClient, McpOAuthCredential, McpOAuthRequest
from .config import base_url, resource_url
from .storage import changed, digest, exchange_or_error, write


def valid_redirect(uri):
    parsed = urlsplit(str(uri))
    return (
        not parsed.fragment
        and not parsed.username
        and not parsed.password
        and bool(parsed.hostname)
        and (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        )
    )


async def fetch_metadata(client_id):
    try:
        return await asyncio.wait_for(_fetch_metadata(client_id), timeout=10)
    except (TimeoutError, ValueError):
        return None


async def _fetch_metadata(client_id):
    """Fetch one HTTPS CIMD document, pinned to a public address; no redirects."""
    url = urlsplit(client_id)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        return None
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, url.hostname, url.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            return None
        address = addresses[0][4][0]
        host = f"[{address}]" if ":" in address else address
        target = f"https://{host}:{url.port or 443}{url.path or '/'}"
        if url.query:
            target += "?" + url.query
        async with (
            httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client,
            client.stream(
                "GET", target, headers={"Host": url.netloc}, extensions={"sni_hostname": url.hostname}
            ) as response,
        ):
            if response.status_code != 200:
                return None
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 65536:
                    return None
        import orjson

        metadata = orjson.loads(data)
        if not isinstance(metadata, dict) or metadata.get("client_id") != client_id:
            return None
        supported = metadata.get("token_endpoint_auth_methods_supported")
        if supported is None:
            supported = [metadata.get("token_endpoint_auth_method", "none")]
        if not isinstance(supported, list) or "none" not in supported:
            return None
        # Public clients with PKCE are supported. Do not trust a remote client secret.
        metadata.update(client_secret=None, token_endpoint_auth_method="none", scope="twicc:full")
        info = OAuthClientInformationFull.model_validate(metadata)
        if not info.redirect_uris or not all(valid_redirect(u) for u in info.redirect_uris):
            return None
        return info
    except (ValueError, OSError, httpx.HTTPError):
        return None


class Provider:
    async def get_client(self, client_id):
        if not isinstance(client_id, str) or len(client_id) > 2048:
            return None
        row = await McpOAuthClient.objects.filter(pk=client_id).afirst()
        if row and (not client_id.startswith("https://") or row.created_at > timezone.now() - timedelta(hours=1)):
            return OAuthClientInformationFull.model_validate(row.metadata)
        if not client_id.startswith("https://"):
            return None
        info = await fetch_metadata(client_id)
        if info:

            def store():
                if row is None and McpOAuthClient.objects.count() >= 1000:
                    return False
                McpOAuthClient.objects.update_or_create(
                    id=client_id,
                    defaults={
                        "metadata": info.model_dump(mode="json"),
                        "created_at": timezone.now(),
                    },
                )
                return True

            if not await write(store):
                return None
        return info

    async def register_client(self, client_info):
        metadata = client_info.model_dump(mode="json")
        secret = metadata.pop("client_secret", None)
        await write(
            lambda: McpOAuthClient.objects.create(
                id=client_info.client_id, metadata=metadata, secret_hash=digest(secret) if secret else ""
            )
        )

    async def authorize(self, client, params):
        if params.resource != resource_url():
            raise AuthorizeError("invalid_target", "Use the advertised MCP resource.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "A valid S256 code challenge is required.")
        if params.scopes and params.scopes != ["twicc:full"]:
            raise AuthorizeError("invalid_scope", "Only twicc:full is supported.")
        if not valid_redirect(params.redirect_uri):
            raise AuthorizeError("invalid_request", "Unsupported redirect URI.")
        request_id, handle = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        code = secrets.token_hex(4).upper()

        def create():
            if McpOAuthRequest.objects.filter(state="pending", expires_at__gt=timezone.now()).count() >= 30:
                return False
            McpOAuthRequest.objects.create(
                id=request_id,
                client_id=client.client_id,
                params=params.model_dump(mode="json"),
                continuation_hash=digest(handle),
                verification_hash=digest(code),
                expires_at=timezone.now() + timedelta(minutes=10),
            )
            return True

        if not await write(create):
            raise AuthorizeError("temporarily_unavailable", "Too many pending requests.")
        await changed()
        return base_url() + f"/mcp/oauth/wait#{request_id}:{handle}:{code}"

    async def load_authorization_code(self, client, authorization_code):
        row = (
            await McpOAuthCredential.objects.select_related("connection")
            .filter(
                digest=digest(authorization_code),
                kind="code",
                consumed=False,
                connection__client_id=client.client_id,
                connection__revoked_at__isnull=True,
            )
            .afirst()
        )
        if not row:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            expires_at=row.expires_at.timestamp(),
            scopes=["twicc:full"],
            **row.params,
        )

    async def exchange_authorization_code(self, client, authorization_code):
        return await exchange_or_error(authorization_code.code, "code", client.client_id)

    async def load_refresh_token(self, client, refresh_token):
        row = (
            await McpOAuthCredential.objects.select_related("connection")
            .filter(
                digest=digest(refresh_token),
                kind="refresh",
                connection__client_id=client.client_id,
                connection__revoked_at__isnull=True,
            )
            .afirst()
        )
        if row:
            return RefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=["twicc:full"],
                expires_at=int(row.expires_at.timestamp()),
                resource=row.connection.resource,
            )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        return await exchange_or_error(refresh_token.token, "refresh", client.client_id)

    async def load_access_token(self, token):
        if not base_url():
            return None
        row = (
            await McpOAuthCredential.objects.select_related("connection")
            .filter(
                digest=digest(token),
                kind="access",
                expires_at__gt=timezone.now(),
                connection__revoked_at__isnull=True,
                connection__resource=resource_url(),
            )
            .afirst()
        )
        if row:
            now = timezone.now()
            if not row.connection.last_used_at or row.connection.last_used_at < now - timedelta(minutes=1):
                await write(lambda: McpConnection.objects.filter(pk=row.connection_id).update(last_used_at=now))
            return AccessToken(
                token=token,
                client_id=row.connection.client_id,
                scopes=["twicc:full"],
                expires_at=int(row.expires_at.timestamp()),
                resource=row.connection.resource,
                subject=row.connection_id,
            )

    async def revoke_token(self, token):
        row = await McpOAuthCredential.objects.filter(digest=digest(token.token)).afirst()
        if row:
            await write(lambda: McpConnection.objects.filter(pk=row.connection_id).update(revoked_at=timezone.now()))
            await changed()


provider = Provider()
