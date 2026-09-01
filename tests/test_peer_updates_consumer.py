"""Initial owner WebSocket snapshot for threaded peer messages."""

import asyncio

from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.asgi import WSConsumer
from twicc.core.models import (
    Peer,
    PeerMessage,
    PeerMessageDirection,
    PeerMessageStatus,
    PeerState,
    Project,
    Session,
    SessionType,
)


def _run(coro):
    return asyncio.run(coro)


def test_initial_peer_message_snapshot_serializes_resolved_reply_without_async_lazy_load(
        transactional_db, monkeypatch, settings):
    settings.TWICC_PASSWORD_HASH = ""
    now = djtz.now()
    project = Project.objects.create(
        id="-tmp-peer-updates", directory="/tmp/peer-updates",
    )
    session = Session.objects.create(
        id="peer-origin", project=project, provider="claude_code",
        file_path="peer-origin.jsonl", type=SessionType.SESSION,
        title="Origin", created_at=now, last_new_content_at=now,
    )
    peer = Peer.objects.create(
        name="alice", base_url="https://alice.example.com", state=PeerState.ACTIVE,
    )
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="parent",
        thread_id="parent",
        title="Parent",
        payload={"text": "one", "images": [], "documents": []},
        origin_session=session,
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="child",
        reply_to="parent",
        reply_to_message=parent,
        thread_id="parent",
        title="Child",
        payload={"text": "two", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )
    revoked_peer = Peer.objects.create(
        name="revoked", base_url="https://revoked.example.com", state=PeerState.REVOKED,
    )
    revoked_message = PeerMessage.objects.create(
        peer=revoked_peer,
        direction=PeerMessageDirection.IN,
        message_id="revoked-child",
        thread_id="revoked-child",
        title="Hidden revoked message",
        payload={"text": "hidden", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    class Registry:
        def set_broadcast_callback(self, callback):
            self.callback = callback

    registry = Registry()
    monkeypatch.setattr("twicc.asgi.scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: registry)

    async def scenario():
        comm = WebsocketCommunicator(
            WSConsumer.as_asgi(), "/ws/?subscribe=peer_messages_updated",
        )
        connected, _ = await comm.connect()
        assert connected
        message = await comm.receive_json_from(timeout=2)
        assert message["type"] == "peer_messages_updated"
        row = next(item for item in message["messages"] if item["id"] == child.pk)
        assert row["thread_id"] == "parent"
        assert row["reply_to"] == "parent"
        assert row["reply_to_ref"] == {
            "id": parent.pk,
            "message_id": "parent",
            "title": "Parent",
            "direction": "out",
            "status": "delivered",
            # No `author` on the parent's origin: the historical reading.
            "author": "agent",
        }
        assert row["reply_target"] == session.id
        assert revoked_message.pk not in {item["id"] for item in message["messages"]}
        await comm.disconnect()

    _run(scenario())
