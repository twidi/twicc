"""Owner-only connection management on the normal TwiCC application origin."""

import orjson
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from twicc.auth.access import request_allowed
from twicc.auth.local_access import request_is_local
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY
from twicc.core.ui_messages import ui_error
from twicc.core.models import McpConnection
from twicc.synced_settings import read_synced_settings
from .oauth.storage import changed, decide, snapshot, write


def _mutation_errors(errors):
    payload = {"error": "; ".join(error.message for error in errors)}
    payload["errors"] = [{**error._asdict(), **({"ui_message": error.message.ui_message}
                         if getattr(error.message, "ui_message", None) else {})} for error in errors]
    if payload["errors"] and payload["errors"][0].get("ui_message"):
        payload["ui_message"] = payload["errors"][0]["ui_message"]
    return payload


async def management(request):
    if settings.TWICC_PASSWORD_HASH:
        auth, fingerprint = await sync_to_async(
            lambda: (request.session.get(SESSION_AUTH_KEY), request.session.get(SESSION_FINGERPRINT_KEY))
        )()
        authorized = request_allowed(request, auth, fingerprint)
    else:
        authorized = request_is_local(request)
    if not authorized:
        return JsonResponse(ui_error('Owner authentication required.', 'backend.mcp.ownerRequired'), status=403)
    if request.method == "GET":
        state = await sync_to_async(snapshot)()
        config = read_synced_settings()
        state["config"] = {
            "mcpBaseUrl": config.get("mcpBaseUrl", ""),
            "externalMcpEnabled": config.get("externalMcpEnabled", False),
            "passwordConfigured": bool(settings.TWICC_PASSWORD_HASH),
        }
        from .oauth.protection import protection
        state["protection"] = protection.snapshot()
        return JsonResponse(state)
    if request.method != "POST":
        return JsonResponse(ui_error("Method not allowed", "backend.common.methodNotAllowed"), status=405)
    # A custom header prevents browser form/CSRF submissions. No CORS is enabled here.
    if request.headers.get("X-TwiCC-MCP-Owner") != "1":
        return JsonResponse(ui_error('Owner request header required.', 'backend.mcp.ownerHeader'), status=403)
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse(ui_error("Invalid JSON", "backend.common.invalidJson"), status=400)
    if not isinstance(data, dict):
        return JsonResponse(ui_error('Expected an object', 'backend.mcp.expectedObject'), status=400)
    action = data.get("action")
    name = data.get("name", "")
    if not isinstance(name, str) or len(name.strip()) > 80:
        return JsonResponse(ui_error('Name must have at most 80 characters.', 'backend.mcp.nameLength'), status=400)
    name = name.strip()
    if action == "suspend":
        from twicc.core.services.settings_mutation import update_synced_settings

        result = await update_synced_settings({"externalMcpEnabled": False}, revoke_mcp_grants=False)
        if result.status != "accepted":
            return JsonResponse(_mutation_errors(result.errors), status=400)
    elif action == "acknowledge_security":
        from .oauth.protection import protection

        if not protection.acknowledge():
            return JsonResponse(ui_error('Automatic protection is still active.', 'backend.mcp.protectionActive'), status=409)
    elif action == "configure":
        from twicc.core.services.settings_mutation import update_synced_settings

        result = await update_synced_settings({k: data[k] for k in ("mcpBaseUrl", "externalMcpEnabled") if k in data})
        if result.status != "accepted":
            return JsonResponse(_mutation_errors(result.errors), status=400)
    elif action in ("approve", "refuse"):
        ok, message = await write(
            lambda: decide(str(data.get("id", "")), action == "approve", str(data.get("code", "")), name)
        )
        await changed()
        if not ok:
            return JsonResponse({"error": message, **({"ui_message": message.ui_message} if getattr(message, "ui_message", None) else {})}, status=409)
    elif action in ("rename", "revoke"):
        fields = {"name": name} if action == "rename" else {"revoked_at": timezone.now()}
        await write(lambda: McpConnection.objects.filter(pk=str(data.get("id", ""))).update(**fields))
    else:
        return JsonResponse(ui_error('Unknown action', 'backend.mcp.unknownAction'), status=400)
    await changed()
    return JsonResponse({"ok": True})
