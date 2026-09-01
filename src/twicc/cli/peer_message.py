"""CLI implementation for the ``twicc peer-message`` subcommand (read-only)."""

from __future__ import annotations

import typer


def peer_message_cmd(
    message_id: str = typer.Argument(
        ...,
        help="The message_id returned by peer-send (pm_...).",
    ),
) -> None:
    """Re-check the status of an outbound peer message.

    ``pending`` = awaiting the remote user's approval; ``delivered`` /
    ``refused`` = their resolution; ``failed`` = the sender received no
    confirmed acceptance, so the peer may still have stored the message
    (detail in ``error``).
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._output import emit_error, emit_json
    from twicc.core.models import PeerMessage, PeerMessageDirection
    from twicc.core.serializers import serialize_peer_message

    message = (
        PeerMessage.objects.filter(direction=PeerMessageDirection.OUT, message_id=message_id)
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
        .prefetch_related("replies")
        .first()
    )
    if message is None:
        emit_error(f"unknown message_id {message_id!r}", code=1)
    emit_json(serialize_peer_message(message))
