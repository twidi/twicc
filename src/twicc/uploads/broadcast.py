"""``upload_state`` WebSocket broadcast (design 2026-09-28-file-upload §5.8).

A broadcast always sends the record that was **just persisted** (§5.2): the
caller passes the metadata returned by a successful write of
:mod:`twicc.uploads.store`. After a failed write there is nothing to pass,
so there is no broadcast, and a client never holds a version that the next
successful write reuses.
"""

from __future__ import annotations

from channels.layers import get_channel_layer

from twicc.uploads.store import build_record


async def broadcast_upload_state(meta: dict) -> None:
    """Send ``{"type": "upload_state", "upload": <record>}`` to every ``/ws/`` client.

    *meta* is the persisted metadata returned by ``store.create_metadata`` or
    ``store.update_metadata``.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {"type": "upload_state", "upload": build_record(meta)},
        },
    )
