"""Push "accounts or grants changed" to connected clients.

The message carries no data: each ``/ws/`` connection reacts by re-resolving
its own principal and project access (see ``WSConsumer._refresh_access``), and
the SPA re-fetches what depends on them. A connection whose account was
disabled or whose epoch moved on is closed with an ``auth_failure``.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync

logger = logging.getLogger(__name__)

ACCOUNTS_CHANGED = "accounts_changed"


def schedule_accounts_changed() -> None:
    """Best-effort broadcast from sync code (a worker thread in the backend).

    A no-op outside the backend (the CLI runs in its own process with its own
    in-memory channel layer): live connections then catch up through the
    access cache TTL and their next reconnect.
    """
    try:
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            "updates", {"type": "broadcast", "data": {"type": ACCOUNTS_CHANGED}}
        )
    except RuntimeError:
        # Called from the event loop thread itself (should not happen: the
        # service layer is always reached through sync_to_async).
        logger.debug("accounts_changed broadcast skipped (event loop thread)")
    except Exception:
        logger.exception("accounts_changed broadcast failed")
