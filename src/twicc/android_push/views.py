"""Session-authenticated enrollment and narrowly scoped native endpoint renewal."""

from uuid import UUID

import orjson
from django.http import JsonResponse
from twicc.android_push import storage
from twicc.android_push.validation import subscription
from twicc.core.ui_messages import ui_error


def response(value: dict, status=200):
    result = JsonResponse(value, status=status)
    result["Cache-Control"] = "no-store"
    return result


def body(request):
    if request.content_type != "application/json" or len(request.body) > 8192:
        raise ValueError("Invalid registration")
    value = orjson.loads(request.body)
    value["device_id"] = str(UUID(value["device_id"]))
    return value


def enrollment(request):
    # PasswordAuthMiddleware protects this API through the existing session.
    if request.method == "GET":
        return response(storage.configuration())
    if request.method != "POST":
        return response(ui_error("Method not allowed", 'backend.common.methodNotAllowed'), 405)
    try:
        value = body(request)
        name = value["name"]
        preferences = value["preferences"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError("Invalid name")
        if not isinstance(preferences, dict) or set(preferences) != set(storage.KINDS):
            raise ValueError("Invalid preferences")
        if any(type(choice) is not bool for choice in preferences.values()) or type(value["enabled"]) is not bool:
            raise ValueError("Invalid preferences")
        token = storage.enroll(value["device_id"], name.strip(), preferences, value["enabled"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return response(ui_error("Invalid Android notification registration.", 'backend.androidPush.invalidRegistration'), 400)
    return response({**storage.configuration(), "token": token})


def native_registration(request):
    # /api/auth/ is reachable without a login cookie. This capability only
    # changes/removes its own device subscription; it grants no TwiCC data access.
    if request.method not in ("PUT", "DELETE"):
        return response(ui_error("Method not allowed", 'backend.common.methodNotAllowed'), 405)
    try:
        value = body(request)
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        with storage.lock:
            device = storage.grant_device(value["device_id"], token)
            if device is None:
                return response(ui_error("Sign in to renew Android notifications.", 'backend.androidPush.renewSignIn'), 401)
            if request.method == "DELETE":
                storage.device_path(value["device_id"]).unlink(missing_ok=True)
            else:
                device["subscription"] = subscription(value["subscription"])
                if "enabled" in value:
                    if type(value["enabled"]) is not bool:
                        raise ValueError("Invalid permission")
                    device["enabled"] = value["enabled"]
                if "preferences" in value:
                    choices = value["preferences"]
                    if (
                        not isinstance(choices, dict)
                        or set(choices) != set(storage.KINDS)
                        or any(type(v) is not bool for v in choices.values())
                    ):
                        raise ValueError("Invalid preferences")
                    device["preferences"] = choices
                storage.save_device(device)
    except (ValueError, TypeError, KeyError, AttributeError):
        return response(ui_error("Invalid Android notification registration.", 'backend.androidPush.invalidRegistration'), 400)
    return response({"ok": True})
