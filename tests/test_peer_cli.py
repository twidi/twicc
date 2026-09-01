"""Peer CLI surface: peers / peer-message / peer-send (in-process invoker)."""

import asyncio

import pytest
from django.db.models.query import QuerySet

from twicc.cli._drop_request import transport
from twicc.core.models import Peer, PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
from twicc.core.services.peer_tokens import mint_token
from twicc.drop_requests_watcher import execute_drop_payload
from twicc.peer import outbound
from twicc.rpc.invoker import invoke


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.peer_mutation.run_under_db_write_lock", _p)
    monkeypatch.setattr("twicc.core.services.peer_messages.run_under_db_write_lock", _p)


@pytest.fixture(autouse=True)
def _peer_host(monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"peerBaseUrl": "https://me.example.com"},
    )


def _active_peer(**kw):
    defaults = {
        "name": "alice", "base_url": "https://alice.example.com", "state": PeerState.ACTIVE,
        "token_ours": mint_token(), "token_theirs": "their-" + "t" * 30,
        "paired_local_base_url": "https://me.example.com",
    }
    defaults.update(kw)
    return Peer.objects.create(**defaults)


@pytest.mark.django_db(transaction=True)
def test_peers_lists_active_and_broken_only():
    _active_peer()
    _active_peer(name="bob", base_url="https://bob.example.com", state=PeerState.BROKEN,
                 token_ours=mint_token())
    _active_peer(name="carol", base_url="https://carol.example.com",
                 state=PeerState.PENDING_RECEIVED, token_ours=None, verification_code="123456")
    _active_peer(name="revoked", base_url="https://revoked.example.com",
                 state=PeerState.REVOKED, token_ours=None, token_theirs=None)
    res = invoke(["peers"])
    assert res.exit_code == 0
    peers = res.result["peers"]
    assert {p["name"] for p in peers} == {"alice", "bob"}
    for p in peers:
        assert set(p) == {"id", "name", "state", "broken_reason", "last_contact_at"}
    assert next(p for p in peers if p["name"] == "bob")["broken_reason"] == ""


@pytest.mark.django_db(transaction=True)
def test_peer_message_found_and_not_found():
    peer = _active_peer()
    PeerMessage.objects.create(
        peer=peer, direction=PeerMessageDirection.OUT, message_id="pm_cli1",
        thread_id="pm_cli1",
        payload={"text": "hello", "images": [], "documents": []},
        origin={"sent_at": "2026-07-24T12:00:00+00:00"},
        status=PeerMessageStatus.PENDING,
    )
    res = invoke(["peer-message", "pm_cli1"])
    assert res.exit_code == 0
    assert res.result["message_id"] == "pm_cli1"
    assert res.result["status"] == "pending"
    assert "payload" not in res.result  # summary only on the agent surface

    res = invoke(["peer-message", "pm_nope"])
    assert res.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_peer_message_resolved_reply_uses_one_query(django_assert_num_queries):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="cli-parent",
        thread_id="cli-parent",
        title="CLI parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="cli-child",
        reply_to=parent.message_id,
        reply_to_message=parent,
        thread_id=parent.thread_id,
        payload={"text": "child", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    # One SELECT with its JOINs, plus the `replies` prefetch that feeds
    # `latest_reply_author` — never a query per relation.
    with django_assert_num_queries(2):
        response = invoke(["peer-message", child.message_id])

    assert response.exit_code == 0
    assert response.result["thread_id"] == parent.thread_id
    assert response.result["reply_to"] == parent.message_id
    assert response.result["reply_to_ref"]["message_id"] == parent.message_id
    assert response.result["reply_target"] is None


@pytest.mark.django_db(transaction=True)
def test_peer_send_precheck_errors():
    async def scenario(argv):
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario(["peer-send", "ghost", "Subject", "hello"]))
    assert res.exit_code == 1

    _active_peer(name="brk", base_url="https://brk.example.com", state=PeerState.BROKEN,
                 token_ours=mint_token())
    res = asyncio.run(scenario(["peer-send", "brk", "Subject", "hello"]))
    assert res.exit_code == 1

    # Title pre-check (local, before the drop-request): empty and over-cap.
    _active_peer()
    res = asyncio.run(scenario(["peer-send", "alice", "   ", "hello"]))
    assert res.exit_code == 1
    res = asyncio.run(scenario(["peer-send", "alice", "x" * 101, "hello"]))
    assert res.exit_code == 1


@pytest.mark.django_db(transaction=True)
def test_peer_send_precheck_rejects_old_local_origin(monkeypatch):
    _active_peer(paired_local_base_url="https://old.example.com")
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)

    response = invoke(["peer-send", "alice", "Subject", "hello"])

    assert response.exit_code == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "bad_reply",
    [
        ".", pytest.param(":", id="standalone-colon"), "..",
        "A\n", "A\nB", " A", "A ", r"A\B", "A`", "A*", "A[", "A]",
        "-abc", "a.b", "a:b", "x" * 41,
    ],
)
def test_peer_send_reply_to_rejects_nonconforming_value_before_lookup(
        bad_reply, monkeypatch):
    _active_peer()
    original_filter = QuerySet.filter

    def _reject_peer_message_lookup(queryset, *args, **kwargs):
        if queryset.model is PeerMessage:
            raise AssertionError("invalid reply_to reached PeerMessage lookup")
        return original_filter(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "filter", _reject_peer_message_lookup)
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    res = invoke(["peer-send", "alice", "Subject", "hello", "--reply-to", bad_reply])

    assert res.exit_code == 1
    assert res.result["status"] == "validation_error"
    assert res.result["errors"][0]["code"] == "invalid_reply_to"


@pytest.mark.django_db(transaction=True)
def test_peer_send_reply_to_rejects_unknown_and_cross_peer_ids(monkeypatch):
    _active_peer()
    other = _active_peer(
        name="bob", base_url="https://bob.example.com", token_ours=mint_token(),
    )
    PeerMessage.objects.create(
        peer=other,
        direction=PeerMessageDirection.IN,
        message_id="other-message",
        thread_id="other-message",
        payload={"text": "other", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)

    for reply_to in ("unknown", "other-message"):
        res = invoke(["peer-send", "alice", "Subject", "hello", "--reply-to", reply_to])
        assert res.exit_code == 1
        assert res.result["status"] == "validation_error"
        assert res.result["errors"][0]["code"] == "unknown_reply_to"


@pytest.mark.django_db(transaction=True)
def test_peer_send_end_to_end_in_process(monkeypatch):
    peer = _active_peer()
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append({
            "bearer": bearer,
            "message_id": message_id,
            "title": title,
            "reply_to": reply_to,
        })
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, ["peer-send", "alice", "Daily recap", "recap of the day"])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0, res.error
    assert res.result["status"] == "sent"
    assert res.result["peer_id"] == peer.id
    assert res.result["message_id"].startswith("pm_")
    assert res.result["peer_status"] == "pending"  # remote state via status_extra
    assert calls[0]["bearer"] == peer.token_theirs
    assert calls[0]["title"] == "Daily recap"
    assert calls[0]["reply_to"] == ""
    message = PeerMessage.objects.get()
    assert message.direction == PeerMessageDirection.OUT
    assert message.status == PeerMessageStatus.PENDING
    assert message.title == "Daily recap"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cli_args", [[], ["--reply-to", ""]])
def test_peer_send_omitted_or_empty_reply_to_creates_root(monkeypatch, cli_args):
    peer = _active_peer()
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append(reply_to)
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            argv = ["peer-send", peer.id, "Subject", "hello", *cli_args]
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    message = PeerMessage.objects.get()
    assert message.reply_to == ""
    assert message.reply_to_message_id is None
    assert message.thread_id == message.message_id
    assert calls == [""]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "reply_to",
    [
        "A", pytest.param("1abc", id="leading-digit"), "_abc", "a-b",
        pytest.param("abc-", id="trailing-hyphen"), "x" * 40,
    ],
)
def test_peer_send_conforming_reply_to_reaches_transport_unchanged(
        monkeypatch, reply_to):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id=reply_to,
        thread_id="root",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.REFUSED,
    )
    calls = []

    async def _fake_post(base_url, *, bearer, message_id, title, reply_to, payload, origin):
        calls.append(reply_to)
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, [
                "peer-send", peer.id, "Subject", "hello", "--reply-to", reply_to,
            ])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    child = PeerMessage.objects.exclude(pk=parent.pk).get()
    assert child.reply_to == reply_to
    assert child.reply_to_message_id == parent.pk
    assert child.thread_id == "root"
    assert calls == [reply_to]


@pytest.mark.django_db(transaction=True)
def test_peer_send_accepts_failed_outbound_parent(monkeypatch):
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="failed-parent",
        thread_id="failed-parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.FAILED,
    )

    async def _fake_post(base_url, **kwargs):
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, [
                "peer-send", peer.id, "Follow-up", "hello",
                "--reply-to", parent.message_id,
            ])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    assert res.exit_code == 0
    child = PeerMessage.objects.exclude(pk=parent.pk).get()
    assert child.reply_to_message_id == parent.pk


@pytest.mark.django_db(transaction=True)
def test_peer_message_cli_outputs_all_threading_fields():
    peer = _active_peer()
    parent = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.IN,
        message_id="parent",
        thread_id="parent",
        title="Parent",
        payload={"text": "parent", "images": [], "documents": []},
        status=PeerMessageStatus.DELIVERED,
    )
    child = PeerMessage.objects.create(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id="child",
        reply_to="parent",
        reply_to_message=parent,
        thread_id="parent",
        payload={"text": "child", "images": [], "documents": []},
        status=PeerMessageStatus.PENDING,
    )

    res = invoke(["peer-message", child.message_id])

    assert res.exit_code == 0
    assert res.result["thread_id"] == "parent"
    assert res.result["reply_to"] == "parent"
    assert res.result["reply_to_ref"]["message_id"] == "parent"
    assert res.result["reply_target"] is None


@pytest.mark.django_db(transaction=True)
def test_peer_send_rejected_maps_exit_3(monkeypatch):
    _active_peer()

    async def _fake_post(base_url, **kw):
        raise outbound.PeerOutboundError("ConnectError")

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, ["peer-send", "alice", "Subject", "hello"])
        finally:
            transport.backend_loop.reset(token)

    res = asyncio.run(scenario())
    # Every service failure (network included) surfaces as watcher "rejected"
    # → exit 3; the distinction lives in the error code (accepted collapse).
    assert res.exit_code == 3


@pytest.mark.django_db(transaction=True)
def test_execute_drop_payload_peer_send(monkeypatch):
    peer = _active_peer()

    async def _fake_post(base_url, **kw):
        return 202, {}

    monkeypatch.setattr("twicc.peer.outbound.post_message", _fake_post)
    status = asyncio.run(execute_drop_payload({"peer": peer.id, "title": "Subject", "text": "hi"}, "peer:send"))
    assert status["status"] == "sent"
    assert status["peer_id"] == peer.id
    assert status["peer_status"] == "pending"
    assert "sent_at" in status
