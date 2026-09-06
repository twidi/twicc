"""Bound OAuth admission without letting anonymous traffic exhaust token budgets.

Transient counters are bounded and process-local. Durable inventory quotas use
source hashes on clients and requests. ASGI's client IP is authoritative: only
Uvicorn's configured trusted proxies may replace it, never headers read here.
"""

import hashlib
import hmac
import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque
from contextvars import ContextVar
from datetime import datetime, timezone

from django.conf import settings

WINDOW_SECONDS = 300
PAUSE_SECONDS = 600
MAX_BUCKETS = 4096
MAX_EVENTS = 2048
MAX_NEW_ATTEMPTS = 120
MAX_REJECTIONS = 20
MAX_CLIENT_PENDING = 3
MAX_SOURCE_PENDING = 10
MAX_PENDING = 30
MAX_SOURCE_CLIENTS = 20
MAX_UNAPPROVED_CLIENTS = 1000
RATE_LIMITS = {"register": 10, "authorize": 30, "token": 120, "revoke": 60, "continue": 120, "discovery": 300}
ADMISSION = frozenset({"register", "authorize"})
request_source = ContextVar("mcp_oauth_source", default="")


def source_hash(scope):
    peer = scope.get("client")
    raw = peer[0] if peer else "unknown"
    try:
        address = ipaddress.ip_address(raw)
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped or ipaddress.ip_network(f"{address}/64", strict=False)
        raw = str(address)
    except ValueError:
        raw = "unknown"
    return hmac.new(settings.SECRET_KEY.encode(), f"mcp-source:{raw}".encode(), hashlib.sha256).hexdigest()


class Protection:
    def __init__(self):
        self.lock = threading.RLock()
        self.buckets = {}
        self.events = deque(maxlen=MAX_EVENTS)
        self.pause_until = 0
        self.incident = None
        self.notice = False
        self.was_paused = False

    def _trim(self, now):
        while self.events and self.events[0][0] <= now - WINDOW_SECONDS:
            self.events.popleft()
        if self.was_paused and now >= self.pause_until:
            self.was_paused = False
            self.notice = True

    def _trip(self, reason, now):
        if now < self.pause_until:
            return
        self.pause_until = now + PAUSE_SECONDS
        self.was_paused = True
        self.incident = {
            "detectedAt": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        self.notice = True

    def _detect(self, now):
        rejected = sum(kind == "rejected" for _, kind, _ in self.events)
        attempts = sum(kind in ADMISSION for _, kind, _ in self.events)
        if rejected >= MAX_REJECTIONS:
            self._trip("Repeated OAuth admission limits", now)
        elif attempts >= MAX_NEW_ATTEMPTS:
            self._trip("Unusually high OAuth admission traffic", now)

    def reject(self, source, *, capacity=False):
        with self.lock:
            now = time.monotonic()
            self._trim(now)
            self.events.append((now, "rejected", source))
            if capacity:
                self._trip("OAuth admission capacity reached", now)
            else:
                self._detect(now)

    def check(self, category, source, identity=None):
        """Return Retry-After seconds, or zero when admitted. No await under lock."""
        with self.lock:
            now = time.monotonic()
            self._trim(now)
            if category in ADMISSION:
                if now < self.pause_until:
                    self.events.append((now, "paused", source))
                    return max(1, math.ceil(self.pause_until - now))
                self.events.append((now, category, source))
                self._detect(now)
                if now < self.pause_until:
                    return PAUSE_SECONDS
            # Anonymous identity churn cannot fill the map reserved for live secrets.
            buckets = self.buckets.setdefault((category, bool(identity)), OrderedDict())
            key = identity or source
            times = buckets.get(key)
            if times is None:
                # Remove idle buckets without ever resetting active callers' limits.
                while buckets:
                    oldest, entries = next(iter(buckets.items()))
                    if entries[-1] > now - 60:
                        break
                    del buckets[oldest]
                if len(buckets) >= MAX_BUCKETS:
                    if category in ADMISSION:
                        self.reject(source, capacity=True)
                    return 60
                times = buckets[key] = deque()
            while times and times[0] <= now - 60:
                times.popleft()
            if len(times) >= RATE_LIMITS[category]:
                if category in ADMISSION:
                    self.reject(source)
                return max(1, math.ceil(times[0] + 60 - now))
            times.append(now)
            buckets.move_to_end(key)
            return 0

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            self._trim(now)
            return {
                "paused": now < self.pause_until,
                "retryAfter": max(0, math.ceil(self.pause_until - now)),
                "incident": self.incident,
                "windowSeconds": WINDOW_SECONDS,
                "registrations": sum(kind == "register" for _, kind, _ in self.events),
                "authorizations": sum(kind == "authorize" for _, kind, _ in self.events),
                "rejections": sum(kind in ("rejected", "paused") for _, kind, _ in self.events),
                "sources": len({source for _, _, source in self.events}),
                "sampleLimited": len(self.events) == MAX_EVENTS,
            }

    def acknowledge(self):
        with self.lock:
            if time.monotonic() < self.pause_until:
                return False
            self.incident = None
            self.notice = True
            return True

    async def publish(self):
        with self.lock:
            notice, self.notice = self.notice, False
        if notice:
            from .storage import changed
            await changed()


protection = Protection()


def registration_allowed():
    """Caller holds the DB write lock; no check/create race across requests."""
    from twicc.core.models import McpOAuthClient

    source = request_source.get()
    clients = McpOAuthClient.objects.filter(mcpconnection__isnull=True)
    if clients.count() >= MAX_UNAPPROVED_CLIENTS:
        protection.reject(source, capacity=True)
        return False
    if clients.filter(source_hash=source).count() >= MAX_SOURCE_CLIENTS:
        protection.reject(source)
        return False
    return True
