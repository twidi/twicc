# src/twicc/core/services/timeline_signal.py
"""The light "a new timeline event exists" signal (display design 2026-10-15, section 9.1).

A writer calls :func:`poke_on_commit` after a successful insert. Once the transaction commits, the poke
arms a trailing throttle on the event loop; when it fires, ONE ``timeline_changed`` message carrying the
highest event id goes to the ``updates`` group. The message holds no session, project or content, so it
is not filtered by project and leaks nothing. Clients then fetch ``after_id`` themselves.

Rules of the module:

* Nothing here ever raises into a writer: a failure is logged and the event stays written.
* No running loop (tests, CLI, the boot before startup) means the poke is a no-op. A closed loop
  (shutdown) is caught.
* The emission (the read of the latest id and the send) never runs under the DB write lock.
* All timeline writes happen in the main process, where the loop lives.
"""

from __future__ import annotations

import asyncio
import logging

from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)

#: Seconds between two signals at most (trailing): about three per second.
THROTTLE_SECONDS = 0.3
#: ``type`` of the WebSocket message.
MESSAGE_TYPE = "timeline_changed"

_loop: asyncio.AbstractEventLoop | None = None
_timer: asyncio.TimerHandle | None = None
_flushes: set[asyncio.Task] = set()


def start_timeline_signal(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Remember the loop the signal runs on (the running one by default). Called at startup."""
    global _loop
    _loop = loop or asyncio.get_running_loop()


def stop_timeline_signal() -> None:
    """Forget the loop and drop a pending timer. Called at shutdown; safe to call twice."""
    global _loop, _timer
    timer, _timer, _loop = _timer, None, None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            logger.debug("Timeline signal timer could not be cancelled", exc_info=True)


def poke_on_commit() -> None:
    """Request a signal once the current transaction commits (at once outside a transaction).

    A rolled-back transaction never runs the callback, so a row that did not persist sends nothing.
    Never raises.
    """
    try:
        transaction.on_commit(poke, robust=True)
    except Exception:
        logger.exception("Timeline signal could not be scheduled")


def poke() -> None:
    """Ask for a signal. Thread-safe; a no-op without a running loop. Never raises."""
    loop = _loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(_arm)
    except RuntimeError:
        logger.debug("Timeline signal dropped: the event loop is closed")
    except Exception:
        logger.exception("Timeline signal could not be requested")


def _arm() -> None:
    """On the loop: start the throttle window unless one is already running (the pokes coalesce)."""
    global _timer
    loop = _loop
    if loop is None or _timer is not None:
        return
    _timer = loop.call_later(THROTTLE_SECONDS, _fire)


def _fire() -> None:
    """On the loop: the window ended. A poke from now on opens the next window."""
    global _timer
    _timer = None
    loop = _loop
    if loop is None:
        return
    task = loop.create_task(_flush())
    _flushes.add(task)
    task.add_done_callback(_flushes.discard)


def _read_latest_id() -> int | None:
    from twicc.core.models import TimelineEvent

    return TimelineEvent.objects.order_by("-id").values_list("id", flat=True).first()


async def _flush() -> None:
    """Read the latest id (in a thread, outside the write lock) and send the message."""
    try:
        latest_id = await asyncio.to_thread(_read_latest_id)
        layer = get_channel_layer()
        if latest_id is None or layer is None:
            return
        await layer.group_send(
            "updates", {"type": "broadcast", "data": {"type": MESSAGE_TYPE, "latest_id": latest_id}},
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Timeline signal could not be sent")
