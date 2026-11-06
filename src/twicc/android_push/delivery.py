"""Bounded, best-effort delivery of current notification events; no event history."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import orjson
from twicc import presence
from twicc.android_push import storage

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def dispatch(kind: str, title: str, body: str, *, path=None, action=None, detail=None):
    if presence.is_browser_present() or len(_tasks) >= 64:
        return
    try:
        task = asyncio.get_running_loop().create_task(send(kind, title, body, path=path, action=action, detail=detail))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except RuntimeError:
        return


async def send(kind: str, title: str, body: str, *, path=None, action=None, detail=None):
    try:
        if kind not in storage.KINDS or presence.is_browser_present():
            return
        payload = {
            "type": "twicc.notification",
            "id": str(uuid4()),
            "sent_at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "title": title[:180],
            "body": body[:800],
        }
        if path is not None:
            payload["path"] = path
        if action is not None:
            payload["action"] = action
        if detail is not None:
            payload["detail"] = detail
        # Web Push permits at most 4096 encrypted bytes. Reserve record/tag overhead.
        while len(orjson.dumps(payload)) > 3900 and payload["body"]:
            payload["body"] = payload["body"][:-40]
        for device in await asyncio.to_thread(storage.devices):
            if device.get("preferences", {}).get(kind) and not presence.is_browser_present():
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
