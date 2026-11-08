"""Bounded, best-effort delivery of current notification events; no event history."""

import asyncio
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

import orjson
from twicc import presence
from twicc.android_push import storage
from twicc.android_push.messages import bounded_payload

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()
_session_path = re.compile(r"/projects?/[A-Za-z0-9_-]{1,255}/session/([A-Za-z0-9_-]{1,255})/?")


def dismiss_sessions(session_ids, *, sent_at=None):
    """Clear these sessions on enrolled phones, including while a browser is active."""
    ids = tuple(dict.fromkeys(session_ids))
    if not ids:
        return
    try:
        task = asyncio.get_running_loop().create_task(send_dismissals(ids, sent_at=sent_at or datetime.now(UTC).isoformat()))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        return


async def send_dismissals(session_ids, *, sent_at=None):
    try:
        ids = list(dict.fromkeys(session_ids))
        sent_at = sent_at or datetime.now(UTC).isoformat()
        devices = await asyncio.to_thread(storage.devices)
        # Twelve maximum-length session IDs fit within one encrypted push record.
        for offset in range(0, len(ids), 12):
            payload = {"type": "twicc.dismiss_sessions", "id": str(uuid4()), "sent_at": sent_at,
                       "session_ids": ids[offset:offset + 12]}
            for device in devices:
                await _send_one(device, payload)
    except Exception as error:
        logger.warning("Android notification dismissal failed (%s)", type(error).__name__)


async def _session_can_notify(path):
    match = _session_path.fullmatch(path or "")
    if match is None:
        return True  # Instance-wide and peer notifications have no session.
    from twicc.core.models import Session

    return await Session.objects.filter(id=match[1], archived=False, hidden=False).aexists()


def dispatch(kind: str, title_ui_message: dict, body_ui_message: dict, *, path=None, action=None, detail=None):
    if presence.is_browser_present() or len(_tasks) >= 64:
        return
    try:
        task = asyncio.get_running_loop().create_task(send(
            kind, title_ui_message, body_ui_message, path=path, action=action, detail=detail, sent_at=datetime.now(UTC).isoformat(),
        ))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        return


async def send(kind: str, title_ui_message: dict, body_ui_message: dict, *, path=None, action=None, detail=None, sent_at=None):
    try:
        if kind not in storage.KINDS or presence.is_browser_present():
            return
        payload = {
            "type": "twicc.notification",
            "version": 2,
            "id": str(uuid4()),
            "sent_at": sent_at or datetime.now(UTC).isoformat(),
            "kind": kind,
            "title_ui_message": title_ui_message,
            "body_ui_message": body_ui_message,
        }
        if path is not None:
            payload["path"] = path
        if action is not None:
            payload["action"] = action
        if detail is not None:
            payload["detail"] = detail
        payload = bounded_payload(payload)
        if payload is None:
            return
        for device in await asyncio.to_thread(storage.devices, notification_payload_version=2):
            if device.get("preferences", {}).get(kind) and not presence.is_browser_present() and await _session_can_notify(path):
                await _send_one(device, payload)
    except Exception as error:
        # Library/network errors can contain the endpoint capability. Log only the class.
        logger.warning("Android notification delivery failed (%s)", type(error).__name__)


async def _send_one(device: dict, payload: dict):
    await asyncio.to_thread(_post, device, payload)


def _post(device: dict, payload: dict):
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=device["subscription"],
            data=orjson.dumps(payload).decode(),
            vapid_private_key=str(storage.directory() / "vapid-private.pem"),
            vapid_claims={"sub": "https://twidi.com"},
            ttl=300,
            headers={"Urgency": "high"},
            timeout=15,
        )
    except WebPushException as error:
        if error.response is not None and error.response.status_code in (404, 410):
            storage.retire_subscription(device)
        else:
            logger.warning("Android push provider rejected a notification")
    except Exception as error:
        logger.warning("Android push transport failed (%s)", type(error).__name__)
