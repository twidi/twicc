"""Occurrence payloads, bounded frozen bodies, and per-attempt signatures."""

from datetime import UTC, datetime
import logging

import orjson
from standardwebhooks import Webhook

from twicc.mcp.events import SYSTEM_CLOCK, Clock
from twicc.mcp.events.catalog import EVENT_NAME, MAX_BODY_BYTES, event_id
from twicc.providers.pending_question import created_at_iso

logger = logging.getLogger(__name__)


def build_data(session_id: str, session_title: str | None, reply: dict, *, pending_request=None) -> dict:
    """Copy the detector's exact reply; never format a conclusion a second time."""
    block = {key: value for key, value in reply.items() if key != "waited_seconds"}
    data = {"session_id": session_id, "session_title": session_title, "reply": block}
    if reply["outcome"] == "awaiting_user_input":
        data["request_type"] = pending_request.request_type
    return data


def build_occurrence(
    subscription_id: str,
    session_id: str,
    session_title: str | None,
    reply: dict,
    *,
    numbering: int,
    last_line: int = 0,
    tick_started_at: datetime,
    item_timestamp: datetime | None = None,
    pending_request=None,
) -> dict:
    """Use detection-time snapshots for identity and occurrence time."""
    outcome = reply["outcome"]
    line_num = reply["line_num"]
    timestamp = tick_started_at.isoformat()
    if outcome == "awaiting_user_input":
        key = pending_request.request_id
        timestamp = created_at_iso(pending_request.created_at)
    else:
        key = f"{numbering}:{line_num}" if line_num is not None else f"last_line:{numbering}:{last_line}"
        if outcome in ("replied", "provider_error") and item_timestamp is not None:
            timestamp = item_timestamp.isoformat()
    return {
        "eventId": event_id(subscription_id, outcome, key),
        "name": EVENT_NAME,
        "timestamp": timestamp,
        "data": build_data(session_id, session_title, reply, pending_request=pending_request),
        "cursor": None,
    }


def fit_body(occurrence: dict) -> bytes | None:
    """Freeze complete UTF-8 bytes, or return None for a logged oversize drop.

    None means no delivery is enqueued. The monitor still consumes the conclusion.
    The input and the original detector text remain unchanged.
    """
    body = orjson.dumps(occurrence)
    if len(body) <= MAX_BODY_BYTES:
        return body
    reply = occurrence["data"]["reply"]
    if "text" in reply:
        fitted_reply = {**reply, "text": "", "text_truncated": True}
        fitted = {**occurrence, "data": {**occurrence["data"], "reply": fitted_reply}}
        body = orjson.dumps(fitted)
        if len(body) <= MAX_BODY_BYTES:
            text = reply["text"]
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                fitted_reply["text"] = text[:middle]
                candidate = orjson.dumps(fitted)
                if len(candidate) <= MAX_BODY_BYTES:
                    low = middle
                    body = candidate
                else:
                    high = middle - 1
            return body
    logger.warning("Dropping MCP event %s: non-text fields exceed body limit", occurrence["eventId"])
    return None


def signing_headers(
    subscription_id: str,
    event_id: str,
    body: bytes,
    secret: str,
    *,
    previous_secret: str | None = None,
    previous_secret_until: datetime | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> dict[str, str]:
    """Sign the frozen body using the current authority snapshot on each attempt."""
    now = clock.epoch()
    timestamp = int(now)
    signing_time = datetime.fromtimestamp(timestamp, UTC)
    data = body.decode("utf-8")
    signatures = [Webhook(secret).sign(event_id, signing_time, data)]
    if previous_secret and previous_secret_until is not None and previous_secret_until.timestamp() > now:
        signatures.append(Webhook(previous_secret).sign(event_id, signing_time, data))
    return {
        "Content-Type": "application/json",
        "webhook-id": event_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": " ".join(signatures),
        "X-MCP-Subscription-Id": subscription_id,
    }
