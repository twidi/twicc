"""Owner-side share management REST (design §11). Under /api/ — password-gated."""

from __future__ import annotations

from datetime import datetime

import orjson
from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import serialize_share
from twicc.core.services import share_mutation
from twicc.core.services.public_origin import usable_public_origin


def _err_response(result):
    errors = result.errors or []
    status = 409 if any(error.code == "session_not_ready" for error in errors) else 400
    return JsonResponse({"errors": [e._asdict() for e in errors]}, status=status)


async def _load(share_id):
    from twicc.core.models import Share

    share = await sync_to_async(
        lambda: Share.objects.select_related(
            "session", "artifact_bookmark", "created_by_session",
        ).filter(id=share_id).first()
    )()
    if share is None:
        raise Http404("Share not found")
    return share


async def shares_list(request):
    """GET /api/shares/ — all shares. POST /api/shares/ — create."""
    from twicc.core.models import Share

    if request.method == "GET":
        shares = await sync_to_async(list)(
            Share.objects.select_related(
                "session", "artifact_bookmark", "created_by_session",
            ).all()
        )
        return JsonResponse({"shares": [serialize_share(s) for s in shares]})
    if request.method == "POST":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        # Sharing requires a dedicated share host (design §12): with shareBaseUrl
        # unset, /share/ 404s everywhere, so a fresh link would be dead. Refuse up
        # front — the UI already gates on this; this is the server-side backstop.
        from twicc.synced_settings import read_synced_settings
        if not usable_public_origin(read_synced_settings().get("shareBaseUrl")):
            return JsonResponse(
                {"error": "share_host_unset",
                 "reason": "Configure a share host in Settings → Sharing first."},
                status=400,
            )
        payload = {
            "kind_target": data.get("kind"),
            "session_id": data.get("session_id"),
            "bookmark_id": data.get("bookmark_id"),
            "label": data.get("label") or "",
            "options": data.get("options") or {},
            "password": data.get("password") or None,
            "expires_at": data.get("expires_at"),
            "notify_on_view": bool(data.get("notify_on_view", False)),
        }
        result = await share_mutation.create_share_from_payload(payload)
        if not result.success:
            return _err_response(result)
        share = await _load(result.share_id)
        return JsonResponse(serialize_share(share), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


async def share_detail(request, share_id):
    """GET / PATCH / DELETE /api/shares/<id>/."""
    share = await _load(share_id)
    if request.method == "GET":
        return JsonResponse(serialize_share(share))
    if request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        fields = {}
        for k in ("label", "notify_on_view", "options", "password"):
            if k in data:
                fields[k] = data[k]
        if "expires_at" in data:
            raw = data["expires_at"]
            fields["expires_at"] = datetime.fromisoformat(raw) if raw else None
        result = await share_mutation.patch_share(share, fields)
        if not result.success:
            return _err_response(result)
        return JsonResponse(serialize_share(await _load(share_id)))
    if request.method == "DELETE":
        await share_mutation.delete_share(share)
        return JsonResponse({"ok": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])


async def share_revoke(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    await share_mutation.revoke_share(await _load(share_id), revoked=True)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_unrevoke(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    await share_mutation.revoke_share(await _load(share_id), revoked=False)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_propagate(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    result = await share_mutation.propagate_share(await _load(share_id))
    if not result.success:
        return _err_response(result)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_accesses(request, share_id):
    """GET /api/shares/<id>/accesses/ — the pruned recent-views log."""
    from twicc.core.models import ShareAccess

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    await _load(share_id)  # 404 if unknown
    rows = await sync_to_async(list)(
        ShareAccess.objects.filter(share_id=share_id).order_by("-at")[:200]
    )
    return JsonResponse({"accesses": [
        {"at": r.at.isoformat(), "ip": r.ip, "user_agent": r.user_agent} for r in rows
    ]})
