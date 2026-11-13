"""The light "a new timeline event exists" signal (display design 2026-10-15, section 9.1).

A writer calls :func:`poke_on_commit` after a successful insert. Once the transaction commits, the poke
arms a trailing throttle on the event loop; when it fires, ONE ``timeline_changed`` message carrying the
highest event id goes to the ``updates`` group. Final-message resolutions also carry their ending IDs,
even when the highest event ID stays unchanged. The message holds no session, project or content, so it
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
from collections.abc import Sequence
from functools import partial

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
_finalized_ending_ids: set[int] = set()


def start_timeline_signal(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Remember the loop the signal runs on (the running one by default). Called at startup."""
    global _loop
    _loop = loop or asyncio.get_running_loop()


def stop_timeline_signal() -> None:
    """Forget the loop and drop a pending timer. Called at shutdown; safe to call twice."""
    global _loop, _timer
    timer, _timer, _loop = _timer, None, None
    _finalized_ending_ids.clear()
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            logger.debug("Timeline signal timer could not be cancelled", exc_info=True)


def poke_on_commit(*, finalized_ending_ids: Sequence[int] = ()) -> None:
    """Request a signal once the current transaction commits (at once outside a transaction).

    A rolled-back transaction never runs the callback, so a row that did not persist sends nothing.
    Never raises.
    """
    try:
        callback = partial(poke, finalized_ending_ids=tuple(finalized_ending_ids)) if finalized_ending_ids else poke
        transaction.on_commit(callback, robust=True)
    except Exception:
        logger.exception("Timeline signal could not be scheduled")


def poke(*, finalized_ending_ids: Sequence[int] = ()) -> None:
    """Ask for a signal. Thread-safe; a no-op without a running loop. Never raises."""
    loop = _loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(_arm, tuple(finalized_ending_ids))
    except RuntimeError:
        logger.debug("Timeline signal dropped: the event loop is closed")
    except Exception:
        logger.exception("Timeline signal could not be requested")


def _arm(finalized_ending_ids: Sequence[int] = ()) -> None:
    """On the loop: start the throttle window unless one is already running (the pokes coalesce)."""
    global _timer
    loop = _loop
    if loop is None:
        return
    _finalized_ending_ids.update(finalized_ending_ids)
    if _timer is not None:
        return
    _timer = loop.call_later(THROTTLE_SECONDS, _fire)


def _fire() -> None:
    """On the loop: the window ended. A poke from now on opens the next window."""
    global _timer
    _timer = None
    loop = _loop
    if loop is None:
        return
    finalized = sorted(_finalized_ending_ids)
    _finalized_ending_ids.clear()
    task = loop.create_task(_flush(finalized))
    _flushes.add(task)
    task.add_done_callback(_flushes.discard)


def _read_latest_id() -> int | None:
    from twicc.core.models import TimelineEvent

    return TimelineEvent.objects.order_by("-id").values_list("id", flat=True).first()


async def _flush(finalized_ending_ids: Sequence[int] = ()) -> None:
    """Read the latest id (in a thread, outside the write lock) and send the message."""
    try:
        latest_id = await asyncio.to_thread(_read_latest_id)
        layer = get_channel_layer()
        if latest_id is None or layer is None:
            return
        data = {"type": MESSAGE_TYPE, "latest_id": latest_id}
        if finalized_ending_ids:
            data["finalized_ending_ids"] = list(finalized_ending_ids)
        await layer.group_send("updates", {"type": "broadcast", "data": data})
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Timeline signal could not be sent")
