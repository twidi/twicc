"""Telemetry background task (design §5.1).

One loop, 60 s granularity: every tick accumulates presence/peak into the
state file; every TELEMETRY_SEND_INTERVAL (and once at startup) builds and
POSTs the pending payload. Failures are logged at debug and never raise
out of the loop. The enabled state is re-checked every tick, so toggling
the synced setting applies without a restart.

Two conditions gate the whole thing, both re-read every tick: the user must
have acknowledged the notice dialog (``is_telemetry_active``), and telemetry
must have been active for at least ``GRACE_AFTER_ACTIVATION``
(``within_activation_grace``) — so an acknowledgement is never immediately
followed by a send.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TICK_INTERVAL = 60
TELEMETRY_SEND_INTERVAL = 24 * 60 * 60

# How long after telemetry becomes active nothing is sent yet. The user has
# just acknowledged the notice (or re-enabled the setting) and may still want
# to go and turn it off; nothing must have left the machine by then. Only a
# floor: "complete UTC days only" usually delays the first send much longer,
# but on its own it can be as short as the minutes left before midnight.
GRACE_AFTER_ACTIVATION = 60 * 60  # 1 hour


def is_telemetry_active() -> bool:
    if not settings.TELEMETRY_ENABLED:
        return False
    from twicc.synced_settings import read_synced_settings

    synced = read_synced_settings()
    # Nothing at all before the user has acknowledged the notice: an unread
    # dialog is not informed consent. Asymmetric with the setting below on
    # purpose — absent/null means "not acknowledged yet", so this is
    # ``is True``, not ``is not False``. Gating here (rather than only in the
    # sender) also stops the accumulators, so the pre-consent window is
    # treated as a disabled one: ``note_active_transition`` drops its days at
    # the acknowledgement.
    if synced.get("telemetryNoticeSeen") is not True:
        return False
    # Default-on: only an explicit False disables. A present ``null`` (the
    # frontend syncs a null placeholder for unset synced keys) must read as
    # enabled, matching the frontend getter ``telemetryEnabled !== false`` --
    # otherwise a synced null would silently disable telemetry despite the
    # default-on intent.
    return synced.get("telemetryEnabled") is not False


def within_activation_grace(state: dict) -> bool:
    """Whether telemetry became active too recently to send anything yet.

    ``active_since`` unset means the instance was never observed switching on
    — enabled all along, including every install predating the field — so no
    grace applies and its behaviour is unchanged. An unparseable value is
    treated the same way rather than blocking sends forever.
    """
    raw = state.get("active_since")
    if not raw:
        return False
    try:
        since = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (datetime.now(UTC) - since).total_seconds() < GRACE_AFTER_ACTIVATION


def tick_once() -> None:
    """Sync: one accumulator sample. Called in a thread."""
    from twicc.telemetry.state import note_active_transition

    active = is_telemetry_active()
    # Track the enabled state on every tick so an off->on transition advances
    # the last-sent marker: days elapsed while disabled are never sent.
    note_active_transition(active)
    if not active:
        return
    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun
    from twicc.presence import is_user_present
    from twicc.telemetry.state import record_tick

    live = ProcessRun.objects.exclude(state=AgentState.DEAD.value).count()
    record_tick(present=is_user_present(), live_agents=live)


def build_pending_payload() -> dict | None:
    """Sync: state + snapshot. Called in a thread."""
    if not is_telemetry_active():
        return None
    from twicc.telemetry.snapshot import build_payload
    from twicc.telemetry.state import ensure_state

    state = ensure_state()
    if within_activation_grace(state):
        return None
    return build_payload(state)


async def send_cycle() -> None:
    payload = await asyncio.to_thread(build_pending_payload)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(settings.TELEMETRY_ENDPOINT, json=payload)
            response.raise_for_status()
    except Exception as exc:
        logger.debug("Telemetry send failed (will retry next cycle): %s", exc)
        return
    from twicc.telemetry.state import mark_sent
    sent_through = payload["days"][-1]["date"]
    await asyncio.to_thread(mark_sent, sent_through, payload)


async def start_telemetry_task(stop_event: asyncio.Event) -> None:
    if not settings.TELEMETRY_ENABLED:
        logger.info("Telemetry disabled (TWICC_NO_TELEMETRY)")
        # Record the disabled state so a later start without the kill switch
        # counts as an off->on transition (the disabled window is never sent).
        from twicc.telemetry.state import note_active_transition

        await asyncio.to_thread(note_active_transition, False)
        return
    logger.info("Telemetry task started")
    ticks_since_send = TELEMETRY_SEND_INTERVAL  # send on first loop entry
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(tick_once)
                ticks_since_send += TICK_INTERVAL
                if ticks_since_send >= TELEMETRY_SEND_INTERVAL:
                    ticks_since_send = 0
                    await send_cycle()
            except Exception:
                logger.debug("Telemetry cycle failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TICK_INTERVAL)
            except TimeoutError:
                pass
            else:
                break
    finally:
        logger.info("Telemetry task stopped")
