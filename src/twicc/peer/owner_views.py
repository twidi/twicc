"""Owner-side peer management REST. Under /api/ — password-gated.

Relationship management is human-only by design (§5): these endpoints have no
CLI/RPC/MCP counterpart, so an agent can neither self-authorize a channel nor
pick an arbitrary URL to exfiltrate to.
"""

from __future__ import annotations

import orjson
from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import serialize_peer, serialize_peer_message
from twicc.core.services import peer_messages, peer_mutation
from twicc.core.text_filter import match_text_query


def _err_response(errors) -> JsonResponse:
    return JsonResponse({"errors": [e._asdict() for e in (errors or [])]}, status=400)


def _parse_body(request) -> dict | None:
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _load_peer(peer_id):
    from twicc.core.models import Peer

    peer = await sync_to_async(lambda: Peer.objects.filter(id=peer_id).first())()
    if peer is None:
        raise Http404("Peer not found")
    return peer


async def _load_message(pk):
    from twicc.core.models import PeerMessage

    message = await sync_to_async(
        lambda: PeerMessage.objects
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
        .prefetch_related("replies")
        .filter(pk=pk).first()
    )()
    if message is None:
        raise Http404("Peer message not found")
    return message


async def peers_list(request):
    """GET /api/peers/ — all states (the UI shows pending ones). POST — create + request."""
    from twicc.core.models import Peer

    if request.method == "GET":
        peers = await sync_to_async(list)(Peer.objects.all())
        return JsonResponse({"peers": [serialize_peer(p) for p in peers]})
    if request.method == "POST":
        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        result = await peer_mutation.create_peer_and_request(
            name=data.get("name") or "", base_url=data.get("base_url") or "",
        )
        if not result.success:
            return _err_response(result.errors)
        peer = await _load_peer(result.peer_id)
        return JsonResponse(serialize_peer(peer), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


async def peer_detail(request, peer_id):
    """GET / PATCH {name?} / DELETE /api/peers/<id>/."""
    peer = await _load_peer(peer_id)
    if request.method == "GET":
        return JsonResponse(serialize_peer(peer))
    if request.method == "PATCH":
        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        if "base_url" in data:
            return _err_response([peer_mutation.PeerError(
                "base_url",
                "immutable",
                "A peer address cannot be changed. Create a new peering for the new address.",
            )])
        if "name" in data:
            result = await peer_mutation.rename_peer(peer, data.get("name") or "")
            if not result.success:
                return _err_response(result.errors)
        return JsonResponse(serialize_peer(await _load_peer(peer_id)))
    if request.method == "DELETE":
        from twicc.core.models import PeerState

        if peer.state in (PeerState.PENDING_SENT, PeerState.PENDING_RECEIVED):
            result = await peer_mutation.delete_peer(peer)
        else:
            result = await peer_mutation.revoke_peer(peer)
        if not result.success:
            return _err_response(result.errors)
        return JsonResponse({"ok": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])


async def peer_verify(request, peer_id):
    """POST /api/peers/<id>/verify/ {code} — requester side submits the out-of-band code."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    result = await peer_mutation.submit_verification_code(peer, data.get("code") or "")
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_accept(request, peer_id):
    """POST /api/peers/<id>/accept/ {name}."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    result = await peer_mutation.accept_peer(peer, name=data.get("name") or "")
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_refuse(request, peer_id):
    """POST /api/peers/<id>/refuse/ — silent local delete."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    result = await peer_mutation.refuse_peer(peer)
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse({"ok": True})


async def peer_reconnect(request, peer_id):
    """POST /api/peers/<id>/reconnect/ — start or manually retry."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    result = await peer_mutation.reconnect_peer(peer)
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_reconnect_cancel(request, peer_id):
    """POST /api/peers/<id>/reconnect/cancel/ — clear a sent attempt."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _load_peer(peer_id)
    result = await peer_mutation.cancel_reconnect(peer)
    if not result.success:
        return _err_response(result.errors)
    return JsonResponse(serialize_peer(await _load_peer(peer_id)))


async def peer_message_send(request):
    """POST /api/peer-messages/send/ — {peer_id, title, text, reply_to?,
    resolve_reply_to?}.

    The owner composes a message directly (peer compose dialog). Text-only by
    design: attachments, drafts and long-form writing stay on the agent path
    (``peer-send``). This endpoint is the ONLY caller allowed to claim human
    authorship — the CLI/RPC/MCP surface always sends ``author="agent"`` — so
    an agent can never pass itself off as its user.

    ``resolve_reply_to`` (``"done"`` | ``"refused"``, needs ``reply_to``)
    resolves the LOCAL inbound message being answered, in the same request
    and only once the peer accepted the reply: a reply that never left
    resolves nothing. The send is not rolled back when the resolution fails
    (it already left); the outcome is reported alongside.
    """
    from twicc.core.models import PeerMessage, PeerMessageDirection, PeerMessageStatus

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    reply_to = data.get("reply_to") or None
    resolve_reply_to = data.get("resolve_reply_to") or None
    if resolve_reply_to is not None:
        if resolve_reply_to not in (PeerMessageStatus.DONE, PeerMessageStatus.REFUSED):
            return JsonResponse({"error": "resolve_reply_to must be \"done\" or \"refused\""}, status=400)
        if not reply_to:
            return JsonResponse({"error": "resolve_reply_to requires reply_to"}, status=400)
    result = await peer_messages.send_peer_message_from_payload(
        {
            "peer": data.get("peer_id") or "",
            "title": data.get("title"),
            "reply_to": reply_to,
            "text": data.get("text"),
        },
        author=peer_messages.PEER_MESSAGE_AUTHOR_HUMAN,
    )
    if not result.success:
        return _err_response(result.errors)
    response = {"message_id": result.message_id, **result.status_extra}
    if resolve_reply_to is not None:
        # The reply is out. Resolve the inbound message it answers — the same
        # row `send` threaded on, re-read here scoped to the peer the reply
        # went to.
        parent = await sync_to_async(
            lambda: PeerMessage.objects
            .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
            .prefetch_related("replies")
            .filter(peer_id=result.peer_id, direction=PeerMessageDirection.IN, message_id=reply_to)
            .first()
        )()
        if parent is None:
            response["resolution"] = {"ok": False, "errors": [
                {"field": "reply_to", "code": "not_inbound", "message": "The answered message is not an inbound message."},
            ]}
        else:
            resolve = peer_messages.mark_done if resolve_reply_to == PeerMessageStatus.DONE else peer_messages.refuse_peer_message
            ok, errors = await resolve(parent)
            response["resolution"] = {"ok": ok, "errors": [e._asdict() for e in (errors or [])]}
    return JsonResponse(response)


async def peer_message_done(request, pk):
    """POST /api/peer-messages/<pk>/done/ — read and dealt with by the owner
    themselves; no agent receives it. Reversible like every resolution."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    success, errors = await peer_messages.mark_done(message)
    if not success:
        return _err_response(errors)
    return JsonResponse({"ok": True})


async def peer_messages_list(request):
    """GET peer-message summaries, optionally filtered by peer, project and full text.

    ``project_id`` keeps the messages whose effective project (their local
    session's, else the nearest reply-chain ancestor's — see
    ``peer_messages.resolve_peer_message_project_ids``) is the project or one
    of its git worktrees (``project_scope_ids``: a worktree scopes to itself).
    """
    from twicc.core.models import PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
    from twicc.projects import project_scope_ids

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        limit = min(max(int(request.GET.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50
    peer_id = (request.GET.get("peer_id") or "").strip()
    project_id = (request.GET.get("project_id") or "").strip()
    query = (request.GET.get("q") or "").strip()

    def _fetch():
        # The serializer reads each message's local session titles: one JOIN,
        # not one query per row.
        rows = PeerMessage.objects.all()
        if peer_id:
            rows = rows.filter(peer_id=peer_id)
        else:
            rows = rows.exclude(peer__state=PeerState.REVOKED)

        if project_id:
            # One pass over the (peer-filtered) rows: a reply chain never
            # leaves its peer relationship, so the ancestors are all here.
            scope = set(project_scope_ids(project_id))
            chain_rows = (
                (pk, parent_pk, origin_project or delivered_project)
                for pk, parent_pk, origin_project, delivered_project in rows.values_list(
                    "pk", "reply_to_message_id",
                    "origin_session__project_id", "delivered_to_session__project_id",
                ).iterator()
            )
            effective = peer_messages.resolve_peer_message_project_ids(chain_rows)
            rows = rows.filter(pk__in=[pk for pk, project in effective.items() if project in scope])

        if query:
            pending_ids = []
            history_ids = []
            history_has_more = False
            candidates = rows.values_list(
                "pk", "title", "payload__text", "direction", "status",
            )
            for pk, title, text, direction, status in candidates.iterator():
                if not (
                    match_text_query(query, title or "")
                    or match_text_query(query, text or "")
                ):
                    continue
                if direction == PeerMessageDirection.IN and status == PeerMessageStatus.PENDING:
                    pending_ids.append(pk)
                elif len(history_ids) < limit:
                    history_ids.append(pk)
                else:
                    history_has_more = True

            ordered_ids = pending_ids + history_ids
            selected = PeerMessage.objects.select_related(
                "origin_session", "delivered_to_session", "reply_to_message",
            ).prefetch_related("replies").filter(pk__in=ordered_ids)
            by_id = {message.pk: message for message in selected}
            return [by_id[pk] for pk in ordered_ids], history_has_more

        # `replies` feeds the "answered by" line: one extra query for the
        # whole list, never one per row.
        rows = rows.select_related(
            "origin_session", "delivered_to_session", "reply_to_message",
        ).prefetch_related("replies")
        pending = list(rows.filter(
            direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
        ))
        history = list(rows.exclude(
            direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
        )[:limit + 1])
        return pending + history[:limit], len(history) > limit

    messages, history_has_more = await sync_to_async(_fetch)()
    return JsonResponse({
        "messages": [serialize_peer_message(message) for message in messages],
        "history_has_more": history_has_more,
    })


async def peer_message_projects(request):
    """GET /api/peer-messages/projects/ — ``{project_ids}``: the effective
    project of every message (same resolution as the list's ``project_id``
    filter), revoked peers excluded like the unfiltered list. Feeds the
    inbox's project filter, which offers only projects that own messages.
    One query over four columns, then the in-memory chain walk."""
    from twicc.core.models import PeerMessage, PeerState

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    def _fetch():
        chain_rows = (
            (pk, parent_pk, origin_project or delivered_project)
            for pk, parent_pk, origin_project, delivered_project in PeerMessage.objects.exclude(
                peer__state=PeerState.REVOKED,
            ).values_list(
                "pk", "reply_to_message_id",
                "origin_session__project_id", "delivered_to_session__project_id",
            ).iterator()
        )
        effective = peer_messages.resolve_peer_message_project_ids(chain_rows)
        return sorted({project for project in effective.values() if project})

    return JsonResponse({"project_ids": await sync_to_async(_fetch)()})


async def peer_message_detail(request, pk):
    """GET /api/peer-messages/<pk>/ — message detail, optionally without attachment bytes."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    message = await _load_message(pk)
    return JsonResponse(serialize_peer_message(
        message,
        include_payload=True,
        include_attachments=request.GET.get("include_attachments") != "0",
    ))


async def peer_message_attachments(request, pk):
    """GET /api/peer-messages/<pk>/attachments/ — attachment blocks without message text."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    message = await _load_message(pk)
    payload = message.payload or {}
    return JsonResponse({
        "images": payload.get("images") or [],
        "documents": payload.get("documents") or [],
    })


async def peer_message_deliver(request, pk):
    """POST /api/peer-messages/<pk>/deliver/ — {session_id?, note?, redeliver?}.

    NOTHING is injected server-side: the message is marked delivered and the
    envelope text is returned; the UI prefills a composer with it — the
    picked existing session's draft (``session_id`` given, recorded as
    ``delivered_to_session``) or a locally-created new draft session. The
    user reviews and sends through the normal pipeline in both cases.

    ``redeliver`` (opt-in, never implicit) re-routes an already-delivered
    message to another target — the inbox's way to fix a wrong pick.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    success, envelope, errors = await peer_messages.mark_delivered(
        message, session_id=data.get("session_id") or None, note=data.get("note") or "",
        redeliver=bool(data.get("redeliver")),
    )
    if not success:
        return _err_response(errors)
    return JsonResponse({"envelope": envelope})


async def peer_message_link_session(request, pk):
    """POST /api/peer-messages/<pk>/link-session/ — {session_id}.

    Late completion of a "deliver to a new session": the session did not exist
    when the message was delivered (it was a local draft), so the UI comes back
    with the real id once the provider created it. Fills an empty link only —
    see ``peer_messages.link_delivered_session``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    data = _parse_body(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    session_id = data.get("session_id") or ""
    if not isinstance(session_id, str) or not session_id:
        return JsonResponse({"error": "session_id is required"}, status=400)
    success, errors = await peer_messages.link_delivered_session(message, session_id)
    if not success:
        return _err_response(errors)
    return JsonResponse({"status": "ok"})


async def peer_message_refuse(request, pk):
    """POST /api/peer-messages/<pk>/refuse/."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    message = await _load_message(pk)
    success, errors = await peer_messages.refuse_peer_message(message)
    if not success:
        return _err_response(errors)
    return JsonResponse({"ok": True})
