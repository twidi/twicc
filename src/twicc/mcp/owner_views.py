"""Owner-only connection management on the normal TwiCC application origin."""

import orjson
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from twicc.auth.local_access import request_is_local
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, is_session_authenticated
from twicc.core.models import McpConnection
from twicc.synced_settings import read_synced_settings
from .oauth.storage import changed, decide, snapshot, write


async def management(request):
    if settings.TWICC_PASSWORD_HASH:
        auth, fingerprint = await sync_to_async(
            lambda: (request.session.get(SESSION_AUTH_KEY), request.session.get(SESSION_FINGERPRINT_KEY))
        )()
        authorized = is_session_authenticated(auth, fingerprint, settings.TWICC_PASSWORD_HASH)
    else:
        authorized = request_is_local(request)
    if not authorized:
        return JsonResponse({"error": "Owner authentication required."}, status=403)
    if request.method == "GET":
        state = await sync_to_async(snapshot)()
        config = read_synced_settings()
        state["config"] = {
            "mcpBaseUrl": config.get("mcpBaseUrl", ""),
            "externalMcpEnabled": config.get("externalMcpEnabled", False),
        }
        return JsonResponse(state)
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    # A custom header prevents browser form/CSRF submissions. No CORS is enabled here.
    if request.headers.get("X-TwiCC-MCP-Owner") != "1":
        return JsonResponse({"error": "Owner request header required."}, status=403)
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Expected an object"}, status=400)
    action = data.get("action")
    name = data.get("name", "")
    if not isinstance(name, str) or len(name.strip()) > 80:
        return JsonResponse({"error": "Name must have at most 80 characters."}, status=400)
    name = name.strip()
    if action == "configure":
        from twicc.core.services.settings_mutation import update_synced_settings

        result = await update_synced_settings({k: data[k] for k in ("mcpBaseUrl", "externalMcpEnabled") if k in data})
        if result.status != "accepted":
            return JsonResponse({"error": "; ".join(e.message for e in result.errors)}, status=400)
    elif action in ("approve", "refuse"):
        ok, message = await write(
            lambda: decide(str(data.get("id", "")), action == "approve", str(data.get("code", "")), name)
        )
        await changed()
        if not ok:
            return JsonResponse({"error": message}, status=409)
    elif action in ("rename", "revoke"):
        fields = {"name": name} if action == "rename" else {"revoked_at": timezone.now()}
        await write(lambda: McpConnection.objects.filter(pk=str(data.get("id", ""))).update(**fields))
    else:
        return JsonResponse({"error": "Unknown action"}, status=400)
    await changed()
    return JsonResponse({"ok": True})
