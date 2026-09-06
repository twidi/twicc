"""Atomic, durable credentials and consent. Secrets never enter owner snapshots."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from mcp.server.auth.provider import TokenError

from twicc.core.models import McpConnection, McpOAuthCredential, McpOAuthRequest
from twicc.providers.db_writer import run_under_db_write_lock


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def write(fn):
    return await run_under_db_write_lock(lambda: sync_to_async(fn)())


async def changed():
    layer = get_channel_layer()
    if layer:
        await layer.group_send("updates", {"type": "broadcast", "data": {"type": "mcp_updated"}})


def issue(connection, kind, seconds, params=None):
    value = "twicc_oauth_" + secrets.token_urlsafe(32)
    McpOAuthCredential.objects.create(
        digest=digest(value),
        connection=connection,
        kind=kind,
        params=params or {},
        expires_at=timezone.now() + timedelta(seconds=seconds),
    )
    return value


def token_pair(connection):
    from mcp.shared.auth import OAuthToken

    return OAuthToken(
        access_token=issue(connection, "access", 900),
        token_type="Bearer",
        expires_in=900,
        refresh_token=issue(connection, "refresh", 90 * 86400),
        scope="twicc:full",
    )


def exchange(value, kind, client_id):
    # Caller holds the backend write lock. The transaction prevents partial rotation.
    with transaction.atomic():
        row = (
            McpOAuthCredential.objects.select_related("connection")
            .filter(
                digest=digest(value),
                kind=kind,
                connection__client_id=client_id,
                connection__revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if row is None:
            return None
        if row.consumed:
            if kind == "refresh":
                McpConnection.objects.filter(pk=row.connection_id).update(revoked_at=timezone.now())
            return None
        if kind == "code":
            row.connection.established_at = timezone.now()
            row.connection.save(update_fields=["established_at"])
        row.consumed = True
        row.save(update_fields=["consumed"])
        return token_pair(row.connection)


async def exchange_or_error(value, kind, client_id):
    result = await write(lambda: exchange(value, kind, client_id))
    if result is None:
        raise TokenError("invalid_grant", "Credential expired, revoked, or already used.")
    await changed()
    return result


def snapshot():
    now = timezone.now()
    connections = list(McpConnection.objects.select_related("client").order_by("-created_at"))
    return {
        "connections": [
            {
                "id": c.id,
                "name": c.name,
                "client_id": c.client_id,
                "client_name": c.client.metadata.get("client_name", ""),
                "created_at": c.created_at.isoformat(),
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "revoked": c.revoked_at is not None,
                "active": c.established_at is not None,
            }
            for c in connections
        ],
        "requests": [
            {
                "id": r.id,
                "client_id": r.client_id,
                "client_name": r.client.metadata.get("client_name", ""),
                "redirect_uri": r.params["redirect_uri"],
                "created_at": r.created_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
            }
            for r in McpOAuthRequest.objects.select_related("client").filter(state="pending", expires_at__gt=now)
        ],
    }


def decide(request_id, approve, code, name):
    with transaction.atomic():
        row = McpOAuthRequest.objects.filter(pk=request_id, state="pending", expires_at__gt=timezone.now()).first()
        if row is None:
            return False, "Request is no longer pending."
        if approve and not hmac.compare_digest(row.verification_hash, digest(code.strip().upper())):
            row.attempts += 1
            if row.attempts >= 5:
                row.state = "refused"
            row.save(update_fields=["attempts", "state"])
            return False, "Verification code does not match."
        if approve:
            row.connection = McpConnection.objects.create(
                id=secrets.token_urlsafe(24),
                client=row.client,
                name=name,
                resource=row.params["resource"],
            )
        row.state = "approved" if approve else "refused"
        row.save(update_fields=["connection", "state"])
        return True, ""


def revoke_all():
    with transaction.atomic():
        McpConnection.objects.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        McpOAuthRequest.objects.filter(state__in=["pending", "approved"]).update(state="expired")


async def enforce_password_requirement():
    """Disable passwordless external access before the server accepts requests.

    Preserve existing grants for manual reactivation after restoring a password.
    Keep the MCP hostname reserved, including when other routing settings are
    invalid. An unreadable file stays intact.
    """
    if settings.TWICC_PASSWORD_HASH:
        return False

    from twicc.synced_settings import _settings_lock, read_routing_settings, write_synced_settings

    def disable():
        with _settings_lock:
            snapshot = read_routing_settings()
            if not snapshot.available or not snapshot.settings.get("externalMcpEnabled"):
                return False
            updated = {**snapshot.settings, "externalMcpEnabled": False,
                       "_version": snapshot.settings.get("_version", 0) + 1}
            write_synced_settings(updated)
            return True

    return await sync_to_async(disable)()


async def cleanup():
    """Bound expired consent data and remove unused registrations."""

    def sweep():
        now = timezone.now()
        with transaction.atomic():
            expired, _ = McpOAuthRequest.objects.filter(expires_at__lte=now).delete()
            McpOAuthCredential.objects.filter(expires_at__lte=now).delete()
            from twicc.core.models import McpOAuthClient

            McpOAuthClient.objects.filter(
                created_at__lt=now - timedelta(days=1), mcpconnection__isnull=True, mcpoauthrequest__isnull=True
            ).delete()
            return expired

    return await write(sweep)
