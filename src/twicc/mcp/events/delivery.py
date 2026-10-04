"""Frozen occurrences, fresh delivery authority, signatures, and bounded retries."""

import asyncio
from collections import deque
from datetime import UTC, datetime
import hmac
import logging
import secrets
from urllib.parse import urlsplit

from django.db import transaction
import orjson
from standardwebhooks import Webhook

from twicc.core.models import McpEventSubscription
from twicc.mcp import pinned_https
from twicc.mcp.events import SYSTEM_CLOCK, Clock
from twicc.mcp.events.catalog import EVENT_NAME, MAX_BODY_BYTES, event_id
from twicc.mcp.oauth import config, storage
from twicc.providers.pending_question import created_at_iso

logger = logging.getLogger(__name__)


class DeliveryService:
    """Send frozen occurrences with fresh authority before each bounded attempt."""

    def __init__(self, runtime, *, send=None, sleep=None):
        self.runtime = runtime
        self.send = send or pinned_https.post_webhook
        self.sleep = sleep or asyncio.sleep

    def _authority(self, emission, loop):
        """Read private delivery keys; deletes run under storage.write and commit."""
        with transaction.atomic():
            row = McpEventSubscription.objects.select_related("connection").filter(
                id=emission.id, created_at=emission.created_at, data_dir=self.runtime.data_dir,
            ).first()
            if row is None:
                return None, "gone"
            if row.refresh_before <= self.runtime.clock.utcnow():
                return None, "expired"
            configured = config.base_url()
            reason = ""
            if row.connection.revoked_at is not None:
                reason = "revoked"
            # Match resource_url's construction without reading live config twice.
            elif configured and row.connection.resource != configured + "/mcp":
                reason = "resource_changed"
            if reason:
                row.delete()
                transaction.on_commit(lambda: loop.call_soon_threadsafe(
                    self.runtime.remove, emission.id, emission.created_at,
                ))
                return None, reason
            if not configured:
                return None, "unconfigured"
            return row, ""

    async def deliver(self, emission):
        """Cancellation preserves the persisted cursor for restart detection."""
        loop = asyncio.get_running_loop()
        try:
            for attempt, delay in enumerate((0, 30, 120), start=1):
                if delay:
                    await self.sleep(delay)
                row, reason = await storage.write(lambda: self._authority(emission, loop))
                if row is None:
                    logger.info("MCP event %s suppressed: category=%s", emission.event_id, reason)
                    break
                headers = signing_headers(
                    emission.id, emission.event_id, emission.body, row.secret,
                    previous_secret=row.previous_secret, previous_secret_until=row.previous_secret_until,
                    clock=self.runtime.clock,
                )
                status, category = None, "http"
                try:
                    response = await self.send(row.callback_url, headers=headers, body=emission.body)
                    status = response.status_code
                    retry = status in (408, 425, 429) or 500 <= status < 600
                    success = 200 <= status < 300
                except Exception as error:
                    category = pinned_https.classify_send_error(error)
                    retry = category in ("connection_refused", "timeout")
                    success = False
                retry = retry and attempt < 3
                outcome = "success" if success else "retry" if retry else "failed"
                logger.info("MCP event %s attempt=%s outcome=%s status=%s category=%s",
                            emission.event_id, attempt, outcome, status, category)
                if not retry:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never include exception text: a dependency can include keys or bodies.
            logger.error("MCP event %s failed: category=internal_error", emission.event_id)
        if emission.cursor is not None:
            self.runtime.writes.put_nowait(emission.cursor)


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


class VerificationError(Exception):
    """Closed protocol failure for callback verification."""

    def __init__(self, code: int, data: dict):
        self.code = code
        self.data = data
        self.message = "ResourceExhausted" if code == -32013 else "CallbackEndpointError"
        super().__init__(self.message)


class VerificationService:
    """Share callback challenges and bound their concurrency and host rate.

    One service belongs to one server event loop. Caller cancellation does not
    cancel a challenge shared with other callers. Shutdown cancels all leaders.
    """

    def __init__(self, *, clock: Clock = SYSTEM_CLOCK, send=None, slot_timeout: float = 5):
        self.clock = clock
        self.send = send or pinned_https.post_webhook
        self.slot_timeout = slot_timeout
        self._slots = asyncio.Semaphore(8)
        self._verified = {}
        self._inflight = {}
        self._host_requests = {}

    async def verify(self, connection_id: str, url: str, subscription_id: str, secret: str) -> None:
        key = (connection_id, url)
        now = self.clock.monotonic()
        for cached_key, verified_at in tuple(self._verified.items()):
            if now - verified_at >= 86400:
                del self._verified[cached_key]
        if key in self._verified:
            return
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._verify(key, url, subscription_id, secret))
            self._inflight[key] = task
            task.add_done_callback(lambda completed: self._finished(key, completed))
        await asyncio.shield(task)

    def _finished(self, key, task):
        if self._inflight.get(key) is task:
            del self._inflight[key]
        # Retrieve failures even when every caller cancels its shielded wait.
        if not task.cancelled():
            task.exception()

    async def aclose(self) -> None:
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _verify(self, key, url, subscription_id, secret):
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.slot_timeout)
        except TimeoutError:
            raise VerificationError(-32013, {"limit": "concurrent_verifications", "max": 8}) from None
        try:
            now = self.clock.monotonic()
            for host, requests in tuple(self._host_requests.items()):
                while requests and requests[0] <= now - 60:
                    requests.popleft()
                if not requests:
                    del self._host_requests[host]
            hostname = urlsplit(url).hostname
            requests = self._host_requests.setdefault(hostname, deque())
            if len(requests) >= 60:
                raise VerificationError(-32013, {"limit": "verifications_per_minute", "max": 60})
            requests.append(now)
            challenge = secrets.token_urlsafe(32)
            body = orjson.dumps({"type": "verification", "challenge": challenge})
            identifier = "msg_verification_" + secrets.token_urlsafe(32)
            headers = signing_headers(subscription_id, identifier, body, secret, clock=self.clock)
            try:
                response = await self.send(url, headers=headers, body=body)
            except Exception as error:
                raise VerificationError(-32015, {"reason": pinned_https.classify_send_error(error)}) from error
            reason = "challenge_failed"
            if 400 <= response.status_code < 500:
                reason = "http_4xx"
            elif 500 <= response.status_code < 600:
                reason = "http_5xx"
            elif 200 <= response.status_code < 300 and not response.overflow:
                try:
                    received = orjson.loads(response.body)
                except orjson.JSONDecodeError:
                    received = None
                if isinstance(received, dict):
                    echo = received.get("challenge")
                    if isinstance(echo, str) and hmac.compare_digest(echo.encode(), challenge.encode()):
                        self._verified[key] = self.clock.monotonic()
                        return
            raise VerificationError(-32015, {"reason": reason})
        finally:
            self._slots.release()
