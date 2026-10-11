"""The connect-time snapshot of hidden session ids.

Hiding a session emits one ``session_removed`` frame and then nothing: the
archive broadcast, the process states and the JSONL watcher all fall silent for
a hidden session, and no REST listing returns one. A client that missed that
single frame keeps the row on screen forever — reconciliation merges what the
API returns and never removes what it omits.

The snapshot closes that hole by stating the fact positively on every connect.
"""

import asyncio

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.asgi import WSConsumer
from twicc.core.models import Project, Session, SessionType


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-hid", directory="/tmp/hid")


def _session(project, sid, *, hidden):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider="claude_code",
        file_path=f"{sid}.jsonl", type=SessionType.SESSION, title=sid,
        created_at=now, last_new_content_at=now, user_message_count=1,
        hidden=hidden,
    )


def _communicator(subscribe=None):
    path = "/ws/" if subscribe is None else f"/ws/?subscribe={subscribe}"
    comm = WebsocketCommunicator(WSConsumer.as_asgi(), path)
    # A password-less instance refuses non-local clients, and a scope with no
    # ``client`` reads as remote. Without this the consumer answers
    # ``auth_failure`` and closes before sending anything.
    comm.scope["client"] = ("127.0.0.1", 43210)
    return comm


async def _collect(comm, wanted, limit=40):
    """Read frames until the wanted type shows up (or the stream runs dry)."""
    for _ in range(limit):
        try:
            msg = await comm.receive_json_from(timeout=2)
        except Exception:
            return None
        if msg.get("type") == wanted:
            return msg
    return None


async def _connect_frames(comm):
    """Every frame ``connect()`` sent, read up to a barrier instead of a timeout.

    Channels dispatches one event at a time, so a request sent after the
    handshake is handled only once ``connect()`` has returned: its reply comes
    after every connect-time frame. An empty ``validate_usage_dump_path``
    answers at once and touches nothing. Not a ``ping``: the heartbeat
    transport answers that one itself, ahead of the consumer.

    Proving a frame absent then costs one round trip, not a receive timeout.
    Returns ``None`` if the barrier never comes back.
    """
    await comm.send_json_to({"type": "validate_usage_dump_path"})
    frames = []
    for _ in range(40):
        try:
            msg = await comm.receive_json_from(timeout=2)
        except Exception:
            return None
        if msg.get("type") == "usage_dump_path_validated":
            return frames
        frames.append(msg)
    return None


async def _close(comm):
    """Disconnect, tolerating a communicator already torn down by a timeout.

    ``receive_json_from`` cancels its internal task when it times out, which
    makes the later ``disconnect`` re-raise that cancellation — noise from the
    harness, not from the code under test. ``CancelledError`` derives from
    ``BaseException``, so it has to be named explicitly.
    """
    try:
        await comm.disconnect()
    except (Exception, asyncio.CancelledError):
        pass


def test_the_snapshot_lists_every_hidden_session(project):
    _session(project, "visible-1", hidden=False)
    _session(project, "hidden-1", hidden=True)
    _session(project, "hidden-2", hidden=True)

    async def scenario():
        comm = _communicator()
        connected, _ = await comm.connect()
        assert connected
        msg = await _collect(comm, "hidden_sessions")
        await _close(comm)
        return msg

    msg = _run(scenario())
    assert msg is not None, "the snapshot must be sent on connect"
    assert msg["session_ids"] == ["hidden-1", "hidden-2"]


def test_a_visible_session_is_never_listed(project):
    """The client drops every id it receives, so a false positive would make a
    live session vanish from the sidebar."""
    _session(project, "visible-1", hidden=False)
    _session(project, "visible-2", hidden=False)

    async def scenario():
        comm = _communicator()
        await comm.connect()
        msg = await _collect(comm, "hidden_sessions")
        await _close(comm)
        return msg

    assert _run(scenario())["session_ids"] == []


def test_it_is_sent_on_every_connect_not_just_the_first(project):
    """Healing depends on it: the row is repaired by reconnecting or reloading,
    both of which are just another connect."""
    _session(project, "hidden-1", hidden=True)

    async def scenario():
        seen = []
        for _ in range(2):
            comm = _communicator()
            await comm.connect()
            seen.append(await _collect(comm, "hidden_sessions"))
            await _close(comm)
        return seen

    first, second = _run(scenario())
    assert first["session_ids"] == ["hidden-1"]
    assert second["session_ids"] == ["hidden-1"]


def test_a_subscribe_filter_still_gets_it_when_asked(project):
    _session(project, "hidden-1", hidden=True)

    async def scenario():
        comm = _communicator(subscribe="hidden_sessions")
        await comm.connect()
        msg = await _collect(comm, "hidden_sessions")
        await _close(comm)
        return msg

    assert _run(scenario())["session_ids"] == ["hidden-1"]


def test_a_subscribe_filter_that_excludes_it_gets_nothing(project):
    """The filtered path must not pay for a query it never uses."""
    _session(project, "hidden-1", hidden=True)

    async def scenario():
        comm = _communicator(subscribe="server_version")
        await comm.connect()
        frames = await _connect_frames(comm)
        await _close(comm)
        return frames

    frames = _run(scenario())
    assert frames is not None, "the barrier must come back"
    types = [frame.get("type") for frame in frames]
    # The filter did let its own type through: the stream was live.
    assert "server_version" in types
    assert "hidden_sessions" not in types


def test_the_active_process_filter_still_works(project):
    """Both consumers read the same set — hoisting the query must not have
    changed what active_processes excludes."""
    _session(project, "hidden-1", hidden=True)

    async def scenario():
        comm = _communicator()
        await comm.connect()
        msg = await _collect(comm, "active_processes")
        await _close(comm)
        return msg

    msg = _run(scenario())
    assert msg is not None
    assert all(p["session_id"] != "hidden-1" for p in msg["processes"])
