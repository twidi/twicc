"""CLI implementation for the ``twicc peers`` subcommand (read-only)."""

from __future__ import annotations

import typer

from twicc.cli._output import PEERS_ENVELOPE_HELP, pagination_notice


def peers_cmd(
    paginated: bool = typer.Option(False, "--paginated", help=PEERS_ENVELOPE_HELP),
) -> None:
    """List peer instances approved for cross-instance messaging.

    Peers are other TwiCC instances the user has paired with (friend-request
    flow, managed in the web UI only). Use this to resolve a peer's id or
    exact name before ``twicc peer-send``. Output includes ``active`` peers
    (messageable) and ``broken`` ones (revoked/unreachable — listed so a
    failing send can be explained instead of "peer unknown").
    """
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()
    paginated = pagination_notice("peers", paginated, default_limit=None, shape="peers")

    from twicc.cli._output import emit_json
    from twicc.core.models import Peer, PeerState

    # Agent surface: name/state/last contact + the id peer-send needs. No
    # base_url (a network address the agent has no use for), no tokens ever,
    # never the verification code.
    peers = [
        {
            "id": peer.id,
            "name": peer.name,
            "state": peer.state,
            "broken_reason": peer.broken_reason,
            "last_contact_at": peer.last_contact_at.isoformat() if peer.last_contact_at else None,
        }
        for peer in Peer.objects.filter(state__in=[PeerState.ACTIVE, PeerState.BROKEN])
    ]
    emit_json({"items": peers} if paginated else {"peers": peers})
