"""In-memory human-presence tracking to gate "away-only" external notifications.

A web page can only observe its own tab, so "is a human present at TwiCC right
now" is approximated per device class and gated with a per-class grace window:

* **desktop** clients report genuine input activity (mouse / keyboard / scroll).
  Input only reaches the focused tab, so switching to another tab or app — or
  walking away and leaving the tab focused — stops the pings; the grace window
  (minutes) then lets a brief detour pass while the desktop OS notification
  banner still covers the user.
* **mobile / touch** clients report a foreground heartbeat. Backgrounding the
  browser stops the pings and the OS suspends the tab anyway; with no banner
  fallback, "backgrounded" means the user genuinely left, so the grace is a
  short anti-flicker buffer for quick app switches.

A user counts as present when **any** client of **any** class pinged within its
own grace window (a plain OR). State is process-local and ephemeral (single ASGI
process): no DB, no migration. Timestamps are server-stamped on receipt with
``time.monotonic()`` so client clocks are never trusted.

Used by :mod:`twicc.external_notifications` to hold an "away-only" notification
while the user looks present and send it only once they are away.
"""

import time

# Per-device-class grace windows, in seconds. A desktop tab switch is brief and
# the desktop notification banner still reaches the user, so the window is
# generous; backgrounding a mobile browser means the user left (no banner), so
# it is just a short anti-flicker buffer for quick app switches.
DESKTOP_GRACE_SECONDS = 5 * 60
MOBILE_GRACE_SECONDS = 30

_GRACE: dict[str, int] = {
    "desktop": DESKTOP_GRACE_SECONDS,
    "mobile": MOBILE_GRACE_SECONDS,
}

# Last activity time per device class (server-stamped monotonic seconds), taken
# as the max across all clients of that class — a single client interacting is
# enough to keep the class present.
_last_active: dict[str, float] = {}
_browser_clients: dict[str, tuple[str, float]] = {}


def touch(device_class: object, client_kind: object = 'browser', client_id: str = 'legacy') -> None:
    """Record an activity ping from a client of ``device_class``.

    Fed straight from a WS payload field, so unknown / malformed classes are
    silently ignored.
    """
    if isinstance(device_class, str) and device_class in _GRACE:
        now = time.monotonic()
        _last_active[device_class] = now
        _prune_browsers(now)
        if client_kind == 'android':
            _browser_clients.pop(client_id, None)
        else:
            _browser_clients[client_id] = (device_class, now)


def _prune_browsers(now: float):
    for client, (device_class, stamp) in list(_browser_clients.items()):
        if now - stamp >= _GRACE[device_class]:
            _browser_clients.pop(client, None)


def is_browser_present() -> bool:
    """An ordinary browser is active; native Android never suppresses its own push."""
    _prune_browsers(time.monotonic())
    return bool(_browser_clients)


def latest_activity() -> float:
    """Most recent activity time across all classes (monotonic seconds, ``0.0`` if none)."""
    return max(_last_active.values(), default=0.0)


def is_user_present() -> bool:
    """True if any device class pinged within its own grace window."""
    now = time.monotonic()
    return any(now - ts < _GRACE[cls] for cls, ts in _last_active.items())


def present_until() -> float | None:
    """Monotonic deadline at which the user stops being present, or ``None`` if already absent.

    The latest expiry among the classes still inside their grace window — i.e.
    how long a deferred notification must wait before the user is considered
    away, assuming no fresh activity arrives meanwhile.
    """
    now = time.monotonic()
    deadlines = [ts + _GRACE[cls] for cls, ts in _last_active.items() if now - ts < _GRACE[cls]]
    return max(deadlines) if deadlines else None
