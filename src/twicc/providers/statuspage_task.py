"""Shared status-page poll: one loop per provider that declares a ``STATUSPAGE``.

Every 2 minutes the loop fetches the provider's component status and hands it
to :func:`twicc.providers_status.record_status`, which owns the persisted
record and its incident bookkeeping. When that record changes, the whole
``providers-status.json`` is broadcast to the connected clients; a newly
connecting client receives it in its connect messages instead, so the loop
never needs to know who is listening.

Provider differences are data, not code: the components URL and the component
name live on each provider's helpers (``StatuspageConfig``), the same way
``model_retirement_task`` is one module driven per provider.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.providers_status import broadcast_providers_status, record_status

logger = logging.getLogger(__name__)

# Poll interval in seconds.
STATUSPAGE_INTERVAL = 2 * 60

# One stop event per provider: the loops are independent, so stopping one
# provider's orchestrator must not stop the other's poll.
_stop_events: dict[Provider, asyncio.Event] = {}


def get_statuspage_stop_event(provider: Provider) -> asyncio.Event:
    event = _stop_events.get(provider)
    if event is None:
        event = asyncio.Event()
        _stop_events[provider] = event
    return event


def stop_statuspage_task(provider: Provider) -> None:
    """Signal *provider*'s poll loop to stop."""
    event = _stop_events.get(provider)
    if event is not None:
        event.set()


async def fetch_component_status(components_url: str, component_name: str) -> str | None:
    """Return the ``status`` of *component_name* on the Statuspage-v2 *components_url*.

    ``None`` when the component is absent from the payload. Network and HTTP
    errors propagate — the caller logs them and retries at the next tick.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(components_url)
        response.raise_for_status()
        data = response.json()
    for component in data.get("components", []):
        if component.get("name") == component_name:
            return component.get("status") or None
    return None


async def check_once(provider: Provider) -> dict | None:
    """Poll *provider* once; record and broadcast a change.

    Returns the new full state when the recorded status changed, ``None``
    otherwise (unchanged, component missing, or a fetch error — all logged,
    none raised, so a bad tick never kills the loop).
    """
    from twicc.providers.helpers import get_provider_helpers

    config = get_provider_helpers(provider).STATUSPAGE
    if config is None:
        return None
    try:
        status = await fetch_component_status(config.components_url, config.component_name)
    except Exception as exc:
        logger.warning("Statuspage check failed for %s: %s", provider.value, exc)
        return None
    if status is None:
        logger.warning("Statuspage check: %r component not found for %s", config.component_name, provider.value)
        return None
    # The file is the only memory: a status that changed while TwiCC was down
    # differs from the recorded one and is treated as the real change it is.
    state = await sync_to_async(record_status)(provider, status)
    if state is None:
        return None
    logger.info("%s upstream status is now %s", provider.value, status)
    await broadcast_providers_status(state)
    return state


async def start_statuspage_task(provider: Provider) -> None:
    """Run *provider*'s poll loop until its stop event is set."""
    from twicc.providers.helpers import get_provider_helpers

    if get_provider_helpers(provider).STATUSPAGE is None:
        logger.info("No status page declared for %s, statuspage task not started", provider.value)
        return

    stop_event = get_statuspage_stop_event(provider)
    # Hot-restart support: a stale set() from the previous shutdown must not
    # kill the relaunched loop.
    stop_event.clear()
    logger.info("Statuspage task started for %s", provider.value)

    while not stop_event.is_set():
        await check_once(provider)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=STATUSPAGE_INTERVAL)
        except TimeoutError:
            pass  # next tick

    logger.info("Statuspage task stopped for %s", provider.value)
