"""Peer messages with files: wire, sender, receiver, read APIs, summaries, purge, CLI.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4.2, §4.8.
"""

import asyncio
import base64
import socket
import threading
import time
from datetime import timedelta

import orjson
import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as djtz

from twicc.cli._drop_request import transport, whoami
from twicc.cli._drop_request.polling import PollOutcome
from twicc.core import serializers
from twicc.core.models import Peer, PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
from twicc.core.services import peer_messages
from twicc.core.services.attachments import inline, lifecycle, staging
from twicc.core.services.peer_tokens import mint_token
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload
from twicc.peer import inbound_views, outbound, owner_views
from twicc.peer_purge_task import purge_expired_attachment_bytes
from twicc.rpc.invoker import invoke

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
# The unknown-keys rule of a receiver older than this phase (peer_messages.py before Task 6).
OLD_RECEIVER_PAYLOAD_KEYS = frozenset({"text", "images", "documents"})


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.peer_mutation.run_under_db_write_lock", _p)
    monkeypatch.setattr("twicc.core.services.peer_messages.run_under_db_write_lock", _p)


@pytest.fixture(autouse=True)
def _peer_host(monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings", lambda: {"peerBaseUrl": "https://me.example.com"},
    )


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    async def _record(data):
        pass
    monkeypatch.setattr("twicc.core.services.peer_messages._broadcast", _record)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture
def released(monkeypatch):
    calls: list = []

    async def release_refs(refs):
        calls.append(tuple(refs))

    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    return calls


@pytest.fixture
def wire(monkeypatch):
    """The decoded bodies posted to the peer; the answer is set with ``wire.answer``."""
    class Wire(list):
        answer = (202, {})

    posted = Wire()

    async def fake(base_url, *, bearer, body):
        posted.append(orjson.loads(body))
        return posted.answer

    monkeypatch.setattr("twicc.peer.outbound.post_message", fake)
    return posted


def _active_peer(**kw):
    defaults = {
        "name": "alice", "base_url": "https://alice.example.com", "state": PeerState.ACTIVE,
        "token_ours": mint_token(), "token_theirs": "their-" + "t" * 30,
        "paired_local_base_url": "https://me.example.com",
    }
    defaults.update(kw)
    return Peer.objects.create(**defaults)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _stage(name: str, data: bytes):
    return staging.stage_bytes(data, name, bucket=staging.new_bucket("cli"), origin="cli")


async def _settle():
    for _ in range(5):
        pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


def _run(coro):
    async def scenario():
        result = await coro
        await _settle()
        return result
    return asyncio.run(scenario())


def _post(client, body, *, bearer):
    return asyncio.run(client.post(
        "/peer/messages/", data=orjson.dumps(body), content_type="application/json",
        headers={"Authorization": f"Bearer {bearer}"},
    ))


def _wire_body(payload):
    return {"message_id": "pm_" + "a" * 16, "title": "Files", "payload": payload,
            "origin": {"sent_at": "2026-10-07T12:00:00+00:00"}}


def _send_files(refs, **extra):
    payload = {"peer": "alice", "title": "Files", "text": "see files", "attachments": [r._asdict() for r in refs],
               **extra}
    return _run(execute_drop_payload(payload, "peer:send_attachments"))


# ── Sender ───────────────────────────────────────────────────────────────────


def test_a_text_only_message_is_text_alone(transactional_db, wire):
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "hi"}))
    assert result.success
    assert wire[0]["payload"] == {"text": "hi"}
    row = PeerMessage.objects.get()
    assert (row.payload, row.attachments_meta) == ({"text": "hi"}, [])


def test_files_travel_with_their_names_in_order(transactional_db, data_dir, wire, released):
    _active_peer()
    refs = [_stage("notes.txt", b"note"), _stage("shot.png", PNG), _stage("empty.bin", b"")]
    status = _send_files(refs)
    assert status["status"] == "sent", status
    assert set(wire[0]["payload"]) == {"text", "attachments"}
    assert wire[0]["payload"]["attachments"] == [
        {"name": "notes.txt", "media_type": "text/plain", "data": _b64(b"note")},
        {"name": "shot.png", "media_type": "image/png", "data": _b64(PNG)},
        {"name": "empty.bin", "media_type": "application/octet-stream", "data": ""},
    ]
    row = PeerMessage.objects.get()
    assert row.attachments_meta == [
        {"name": "notes.txt", "media_type": "text/plain", "bytes": 4},
        {"name": "shot.png", "media_type": "image/png", "bytes": len(PNG)},
        {"name": "empty.bin", "media_type": "application/octet-stream", "bytes": 0},
    ]
    assert released == [tuple(refs)]


def _old_receiver_errors(payload: dict) -> list[str]:
    """Inline copy of the payload-keys check of a receiver older than this phase (spec §7).

    ``_validate_inbound_payload`` before Task 6: any key outside ``text`` / ``images`` /
    ``documents`` is an ``unknown_keys`` error, so the whole message is refused (400).
    """
    errors = []
    unknown = set(payload) - OLD_RECEIVER_PAYLOAD_KEYS
    if unknown:
        errors.append("unknown_keys")
    return errors


def test_an_old_receiver_refuses_files_and_accepts_text_only(transactional_db, data_dir, wire):
    _active_peer()
    _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "hi"}))
    _send_files([_stage("a.txt", b"a")])
    text_only, with_files = (body["payload"] for body in wire)
    assert _old_receiver_errors(text_only) == []
    assert _old_receiver_errors(with_files) == ["unknown_keys"]


@pytest.mark.parametrize(("sizes", "ok"), [((4, 4), True), ((4, 5), False)])
def test_the_staged_total_is_checked_before_reading(transactional_db, data_dir, wire, released, monkeypatch,
                                                    sizes, ok):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_TOTAL_BYTES", 8)
    _active_peer()
    refs = [_stage(f"{n}.bin", b"x" * size) for n, size in enumerate(sizes)]
    status = _send_files(refs)
    assert (status["status"] == "sent") is ok
    if not ok:
        assert status["errors"][0]["code"] == "attachments_too_large"
        assert status["errors"][0]["message"].endswith(inline.PEER_TOO_LARGE_HINT)
        assert wire == []
    assert released == [tuple(refs)]


def test_a_body_above_the_request_cap_is_refused_before_the_post(transactional_db, wire, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_REQUEST_BYTES", 300)
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "x" * 400}))
    assert [(e.field, e.code) for e in result.errors] == [("payload", "message_too_large")]
    assert "once encoded" in result.errors[0].message
    assert wire == []
    assert PeerMessage.objects.count() == 0


@pytest.mark.parametrize(("status", "files", "expected"), [
    (400, True, "The remote instance rejected the message. It may be too old to receive attachments."),
    (413, True, ("The remote instance, or a proxy in front of it, refused the message size. "
                 "An older instance also refuses any attachment.")),
    (413, False, "The remote instance, or a proxy in front of it, refused the message size."),
    (400, False, "The remote instance rejected the message."),
])
def test_rejection_texts_follow_the_http_status(transactional_db, data_dir, wire, status, files, expected):
    _active_peer()
    wire.answer = (status, {})
    payload = {"peer": "alice", "title": "T", "text": "hi"}
    if files:
        payload["attachments"] = [_stage("a.txt", b"a")._asdict()]
    status_data = _run(execute_drop_payload(payload, "peer:send_attachments" if files else "peer:send"))
    assert status_data["errors"][0]["message"] == expected
    assert PeerMessage.objects.get().error == expected


def test_the_write_timeout_follows_the_body(monkeypatch):
    seen: dict = {}

    class FakeResponse:
        status_code = 202

        def json(self):
            return {"status": "pending"}

    class FakeClient:
        def __init__(self, *, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, content, headers):
            seen.update(url=url, content=content, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(outbound.httpx, "AsyncClient", FakeClient)
    body = b"x" * (512 * 1024)
    assert asyncio.run(outbound.post_message("https://bob.example.com/", bearer="tok", body=body)) == (
        202, {"status": "pending"},
    )
    assert seen["url"] == "https://bob.example.com/peer/messages/"
    assert seen["content"] is body
    assert seen["headers"] == {"Authorization": "Bearer tok", "Content-Type": "application/json"}
    assert seen["timeout"].write == inline.transfer_timeout(len(body)) == 32.0
    assert (seen["timeout"].connect, seen["timeout"].read) == (outbound.OUTBOUND_TIMEOUT_SECONDS,) * 2


_STALL_SECONDS = 1.0
_SLOW_BODY = b"x" * (12 * 1024 * 1024)  # far above the loopback socket buffers: the write must wait


def _stalling_server():
    """A real 127.0.0.1 HTTP server that reads nothing for ``_STALL_SECONDS``, then the whole body.

    Returns ``(port, thread, received)``; ``received`` gets the body size once a 202 is sent.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    received: list[int] = []

    def serve():
        with listener:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(10)
                data = b""
                while b"\r\n\r\n" not in data:
                    part = conn.recv(65536)
                    if not part:
                        return
                    data += part
                head, _, rest = data.partition(b"\r\n\r\n")
                length = next(int(line.split(b":")[1]) for line in head.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                got = len(rest)
                try:
                    time.sleep(_STALL_SECONDS)  # a slow link: the client's write waits
                    while got < length:
                        part = conn.recv(256 * 1024)
                        if not part:
                            return
                        got += len(part)
                    conn.sendall(b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: 2\r\nConnection: close\r\n\r\n{}")
                except OSError:
                    return
                received.append(got)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread, received


@pytest.fixture
def short_peer_timeouts(monkeypatch):
    """Scale the timeouts down: 0.3 s for connect / read / the old write; 1 MiB/s for a transfer."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(outbound, "OUTBOUND_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inline, "TRANSFER_BASE_SECONDS", 0.3)
    monkeypatch.setattr(inline, "INLINE_MIN_THROUGHPUT", 1024 * 1024)


def test_a_slow_write_longer_than_the_old_timeout_succeeds(short_peer_timeouts):
    """The real network backend (no mock transport): the write budget follows the body (§4.8.3)."""
    port, thread, received = _stalling_server()
    assert inline.transfer_timeout(len(_SLOW_BODY)) > 10 * _STALL_SECONDS
    assert asyncio.run(outbound.post_message(f"http://127.0.0.1:{port}", bearer="tok", body=_SLOW_BODY)) == (202, {})
    thread.join(10)
    assert received == [len(_SLOW_BODY)]


def test_the_same_slow_write_fails_with_the_old_single_timeout(short_peer_timeouts, monkeypatch):
    """Control: the server really stalls longer than the old write timeout (30 s, scaled to 0.3 s)."""
    monkeypatch.setattr(outbound, "transfer_timeout", lambda size: outbound.OUTBOUND_TIMEOUT_SECONDS)
    port, thread, received = _stalling_server()
    with pytest.raises(outbound.PeerOutboundError, match="WriteTimeout"):
        asyncio.run(outbound.post_message(f"http://127.0.0.1:{port}", bearer="tok", body=_SLOW_BODY))
    thread.join(10)
    assert received == []


def test_the_message_body_is_serialized_once(transactional_db, data_dir, released, monkeypatch):
    """One serialization per send: the size check and the POST use the same bytes (§4.8.3)."""
    _active_peer()
    built: list[bytes] = []
    posted: list[bytes] = []
    real_build = outbound.build_message_body

    def build(**kwargs):
        built.append(real_build(**kwargs))
        return built[-1]

    async def post(base_url, *, bearer, body):
        posted.append(body)
        return 202, {}

    monkeypatch.setattr(outbound, "build_message_body", build)
    monkeypatch.setattr(outbound, "post_message", post)
    status = _send_files([_stage("a.txt", b"a"), _stage("b.png", PNG)])
    assert status["status"] == "sent", status
    assert len(built) == 1
    assert len(posted) == 1 and posted[0] is built[0]


def test_the_minted_message_id_is_used(transactional_db, wire):
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "pm_cli0000000000001"},
    ))
    assert result.message_id == "pm_cli0000000000001"
    assert wire[0]["message_id"] == "pm_cli0000000000001"
    assert PeerMessage.objects.get().message_id == "pm_cli0000000000001"


def test_a_bad_or_reused_message_id_is_refused(transactional_db, wire):
    peer = _active_peer()
    bad = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "-bad"},
    ))
    assert [e.code for e in bad.errors] == ["invalid_message_id"]
    PeerMessage.objects.create(peer=peer, direction=PeerMessageDirection.IN, message_id="pm_used", thread_id="pm_used",
                               payload={"text": "x"}, status=PeerMessageStatus.PENDING)
    reused = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "pm_used"},
    ))
    assert [e.code for e in reused.errors] == ["invalid_message_id"]
    assert wire == []
    assert PeerMessage.objects.count() == 1


@pytest.mark.parametrize("change", [
    {},
    {"text": ""},
    {"peer": "nobody"},
    {"reply_to": "pm_unknown"},
    {"__state__": PeerState.PENDING_SENT},
    {"__http__": 500},
])
def test_refs_are_released_on_every_outcome(transactional_db, data_dir, wire, released, change):
    change = dict(change)
    peer = _active_peer()
    if "__state__" in change:
        Peer.objects.filter(pk=peer.pk).update(state=change.pop("__state__"))
    if "__http__" in change:
        wire.answer = (change.pop("__http__"), {})
    refs = [_stage("a.txt", b"a")]
    _send_files(refs, **change)
    assert released == [tuple(refs)]


def test_peer_send_refuses_refs_and_both_kinds_route_to_one_service(transactional_db, data_dir, wire, released):
    _active_peer()
    ref = _stage("a.txt", b"a")
    status = _run(execute_drop_payload(
        {"peer": "alice", "title": "T", "text": "hi", "attachments": [ref._asdict()]}, "peer:send",
    ))
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert wire == []
    assert released == [(ref,)]
    assert _KIND_HANDLERS["peer:send"][:2] == (
        "twicc.core.services.peer_messages", "send_peer_message_from_drop_payload",
    )
    assert _KIND_HANDLERS["peer:send_attachments"][:2] == (
        "twicc.core.services.peer_messages", "send_peer_attachments_from_drop_payload",
    )


@pytest.mark.parametrize("kind", ["peer:send", "peer:send_attachments"])
def test_legacy_fields_are_refused(transactional_db, wire, kind):
    _active_peer()
    status = _run(execute_drop_payload(
        {"peer": "alice", "title": "T", "text": "hi", "images": [{"type": "image"}]}, kind,
    ))
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert wire == []


def test_an_older_watcher_fails_the_new_kind_and_sends_nothing(transactional_db, data_dir, wire, monkeypatch):
    _active_peer()
    monkeypatch.delitem(_KIND_HANDLERS, "peer:send_attachments")
    status = _send_files([_stage("a.txt", b"a")])
    assert status["status"] == "failed"
    assert "Unknown payload kind" in status["error"]
    assert wire == []


# ── peer-send CLI ────────────────────────────────────────────────────────────


def _in_backend(argv):
    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)
    return asyncio.run(scenario())


def test_peer_send_cli_stages_files_and_uses_the_new_kind(transactional_db, data_dir, wire, tmp_path):
    _active_peer()
    path = tmp_path / "fix.patch"
    path.write_bytes(b"diff --git a b")
    result = _in_backend(["peer-send", "alice", "Patch", "see the patch", "--attach", str(path),
                          "--attach", "data:text/plain;name=n%C3%A9.txt;base64," + _b64(b"x")])
    assert result.exit_code == 0, result.error
    row = PeerMessage.objects.get()
    assert result.result["message_id"] == row.message_id == wire[0]["message_id"]
    assert [e["name"] for e in wire[0]["payload"]["attachments"]] == ["fix.patch", "né.txt"]


def test_peer_send_cli_refuses_more_than_50_mb_before_any_copy(transactional_db, data_dir, wire, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    _active_peer()
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    result = _in_backend(["peer-send", "alice", "T", "hi", "--attach", str(path),
                          "--attach", "data:text/plain;base64," + _b64(b"y" * 6)])
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "attachments_too_large"
    assert result.result["errors"][0]["message"].endswith(inline.PEER_TOO_LARGE_HINT)
    assert not staging.get_composer_attachments_dir().exists() or not any(
        staging.get_composer_attachments_dir().iterdir()
    )
    assert wire == []


@pytest.fixture
def fake_peer_transport(monkeypatch, transactional_db, data_dir):
    """A local-mode transport that records the submission and answers ``seen["outcome"]``."""
    seen: dict = {"outcome": PollOutcome(None, None, True)}

    class _Submission:
        request_uuid = "req-peer"

        def cleanup(self):
            pass

    def submit(payload, *, kind):
        seen.update(payload=payload, kind=kind)
        return _Submission()

    def wait(sub, timeout_seconds):
        seen["timeout"] = timeout_seconds
        return seen["outcome"]

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", wait)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


@pytest.mark.parametrize(("extra", "with_file", "expected"), [
    ([], True, inline.PEER_SEND_TIMEOUT_WITH_FILES),
    ([], False, 30),
    (["--timeout", "99"], True, 99),
])
def test_peer_send_default_timeout(fake_peer_transport, tmp_path, extra, with_file, expected):
    peer = _active_peer()
    argv = ["peer-send", "alice", "T", "hi", *extra]
    if with_file:
        path = tmp_path / "a.txt"
        path.write_bytes(b"a")
        argv += ["--attach", str(path)]
    result = invoke(argv)
    assert fake_peer_transport["timeout"] == expected
    assert fake_peer_transport["kind"] == ("peer:send_attachments" if with_file else "peer:send")
    # A timeout (exit 5) still names the message and the peer.
    assert result.exit_code == 5
    assert result.result["message_id"] == fake_peer_transport["payload"]["message_id"]
    assert result.result["peer_id"] == peer.id


@pytest.mark.parametrize(("outcome", "exit_code"), [
    (PollOutcome("rejected", {"errors": [{"field": "peer", "code": "unreachable", "message": "x"}]}, True), 3),
    (PollOutcome("failed", {"error": "boom"}, True), 4),
])
def test_peer_send_prints_the_ids_on_rejected_and_failed(fake_peer_transport, outcome, exit_code):
    peer = _active_peer()
    fake_peer_transport["outcome"] = outcome
    result = invoke(["peer-send", "alice", "T", "hi"])
    assert result.exit_code == exit_code
    assert result.result["message_id"] == fake_peer_transport["payload"]["message_id"]
    assert peer_messages.PEER_MESSAGE_ID_PATTERN.fullmatch(result.result["message_id"])
    assert result.result["peer_id"] == peer.id


def test_peer_send_prints_the_id_of_a_sent_status(fake_peer_transport):
    peer = _active_peer()
    fake_peer_transport["outcome"] = PollOutcome(
        "sent", {"message_id": "pm_server00000001", "peer_id": peer.id, "peer_status": "pending"}, True,
    )
    result = invoke(["peer-send", "alice", "T", "hi"])
    assert result.exit_code == 0
    assert result.result["message_id"] == "pm_server00000001"


# ── Receiver ─────────────────────────────────────────────────────────────────


def test_the_receiver_stores_sanitized_entries(transactional_db, data_dir):
    peer = _active_peer()
    payload = {"text": "files", "attachments": [
        {"name": "a/b.txt", "media_type": "text/plain", "data": _b64(b"ab")},
        {"name": "shot.png", "media_type": "not a type", "data": _b64(PNG)},
    ]}
    response = _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours)
    assert response.status_code == 202
    row = PeerMessage.objects.get()
    assert row.payload == {"text": "files", "attachments": [
        {"name": "a_b.txt", "media_type": "text/plain", "data": _b64(b"ab")},
        {"name": "shot.png", "media_type": "application/octet-stream", "data": _b64(PNG)},
    ]}
    assert row.attachments_meta == [
        {"name": "a_b.txt", "media_type": "text/plain", "bytes": 2},
        {"name": "shot.png", "media_type": "application/octet-stream", "bytes": len(PNG)},
    ]


def test_the_receiver_converts_legacy_blocks(transactional_db, data_dir):
    peer = _active_peer()
    payload = {"text": "old", "images": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(PNG)}},
    ], "documents": [
        {"type": "document", "title": "n.md", "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
        # A legacy block is sanitized like a wire entry: its title is a path, its media type is not one.
        {"type": "document", "title": "a/b.md",
         "source": {"type": "base64", "media_type": "not a type", "data": _b64(b"md")}},
    ]}
    assert _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours).status_code == 202
    row = PeerMessage.objects.get()
    assert row.payload == {"text": "old", "attachments": [
        {"name": "attachment-1.png", "media_type": "image/png", "data": _b64(PNG)},
        {"name": "n.md", "media_type": "text/plain", "data": _b64("hé".encode())},
        {"name": "a_b.md", "media_type": "application/octet-stream", "data": _b64(b"md")},
    ]}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": len(PNG)},
        {"name": "n.md", "media_type": "text/plain", "bytes": 3},
        {"name": "a_b.md", "media_type": "application/octet-stream", "bytes": 2},
    ]


@pytest.mark.parametrize("payload", [
    {"text": "x", "attachments": [], "images": []},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "YQ=="}], "documents": []},
    {"text": "x", "extra": 1},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "YQ==", "size": 1}]},
    {"text": "x", "attachments": [{"name": "", "media_type": "text/plain", "data": "YQ=="}]},
    {"text": "x", "attachments": [{"media_type": "text/plain", "data": "YQ=="}]},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": 1}]},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "QUJD!!!!"}]},
    {"text": "x", "attachments": "nope"},
])
def test_the_receiver_refuses_bad_payloads(transactional_db, data_dir, payload):
    peer = _active_peer()
    response = _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours)
    assert response.status_code == 400
    assert orjson.loads(response.content) == {"error": "invalid_payload"}
    assert PeerMessage.objects.count() == 0


@pytest.mark.parametrize(("sizes", "status"), [((3, 3), 202), ((3, 4), 400)])
def test_the_receiver_total_boundary(transactional_db, data_dir, monkeypatch, sizes, status):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_TOTAL_BYTES", 6)
    peer = _active_peer()
    payload = {"text": "x", "attachments": [
        {"name": f"{n}.bin", "media_type": "application/octet-stream", "data": _b64(b"x" * size)}
        for n, size in enumerate(sizes)
    ]}
    assert _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours).status_code == status


def test_the_receiver_body_cap_boundary(transactional_db, data_dir, monkeypatch):
    peer = _active_peer()
    body = _wire_body({"text": "x"})
    size = len(orjson.dumps(body))
    monkeypatch.setattr(inbound_views, "PEER_MESSAGE_MAX_REQUEST_BYTES", size)
    assert _post(AsyncClient(), body, bearer=peer.token_ours).status_code == 202
    monkeypatch.setattr(inbound_views, "PEER_MESSAGE_MAX_REQUEST_BYTES", size - 1)
    other = {**body, "message_id": "pm_" + "b" * 16}
    assert _post(AsyncClient(), other, bearer=peer.token_ours).status_code == 413


def test_the_receiver_cap_is_the_inline_request_cap():
    assert inbound_views.PEER_MESSAGE_MAX_REQUEST_BYTES == inline.INLINE_MAX_REQUEST_BYTES
    assert peer_messages.PEER_ATTACHMENT_MAX_TOTAL_BYTES == inline.INLINE_MAX_BYTES


def test_receiver_edge_shapes(transactional_db, data_dir):
    peer = _active_peer()
    cases = [
        ({"text": "a", "attachments": []}, {"text": "a"}, []),
        ({"text": "b", "images": None, "documents": []}, {"text": "b"}, []),
        ({"text": "c", "attachments": [{"name": "e.bin", "media_type": "application/octet-stream", "data": ""}]},
         {"text": "c", "attachments": [{"name": "e.bin", "media_type": "application/octet-stream", "data": ""}]},
         [{"name": "e.bin", "media_type": "application/octet-stream", "bytes": 0}]),
    ]
    for index, (payload, stored, meta) in enumerate(cases):
        body = {**_wire_body(payload), "message_id": f"pm_edge{index:012d}"}
        assert _post(AsyncClient(), body, bearer=peer.token_ours).status_code == 202, payload
        row = PeerMessage.objects.get(message_id=body["message_id"])
        assert (row.payload, row.attachments_meta) == (stored, meta)


def test_the_receiver_parses_and_validates_off_the_event_loop(transactional_db, data_dir, monkeypatch):
    peer = _active_peer()
    threads: dict = {}
    real_read = inbound_views._read_message_body
    real_prepare = peer_messages.prepare_inbound_payload

    def read(request):
        threads["read"] = threading.get_ident()
        return real_read(request)

    def prepare(payload):
        threads["prepare"] = threading.get_ident()
        return real_prepare(payload)

    monkeypatch.setattr(inbound_views, "_read_message_body", read)
    monkeypatch.setattr(peer_messages, "prepare_inbound_payload", prepare)
    assert _post(AsyncClient(), _wire_body({"text": "x"}), bearer=peer.token_ours).status_code == 202
    main = threading.get_ident()
    assert threads["read"] != main
    assert threads["prepare"] != main


# ── Read APIs and summaries ──────────────────────────────────────────────────


def _row(peer, **kw):
    message_id = kw.pop("message_id", "pm_" + "c" * 16)
    defaults = {"peer": peer, "direction": PeerMessageDirection.IN, "message_id": message_id,
                "thread_id": message_id, "title": "Row", "status": PeerMessageStatus.PENDING,
                "payload": {"text": "body", "attachments": [
                    {"name": "a.txt", "media_type": "text/plain", "data": _b64(b"sentinel-bytes")},
                ]},
                "attachments_meta": [{"name": "a.txt", "media_type": "text/plain", "bytes": 14}]}
    defaults.update(kw)
    return PeerMessage.objects.create(**defaults)


@pytest.fixture
def owner_client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def test_the_attachments_endpoint_returns_entries_off_the_event_loop(transactional_db, owner_client, monkeypatch):
    message = _row(_active_peer())
    threads = []
    real = owner_views._attachments_body

    def spy(pk):
        threads.append(threading.get_ident())
        return real(pk)

    monkeypatch.setattr(owner_views, "_attachments_body", spy)
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/attachments/"))
    assert response.status_code == 200
    assert orjson.loads(response.content) == {"attachments": message.payload["attachments"]}
    assert threads and threads[0] != threading.get_ident()
    assert b"body" not in response.content


def test_the_detail_without_bytes_has_text_and_no_entry(transactional_db, owner_client):
    message = _row(_active_peer())
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/?include_attachments=0"))
    row = orjson.loads(response.content)
    assert row["payload"] == {"text": "body", "attachments": []}
    assert b"sentinel" not in response.content


def test_the_detail_with_bytes_has_the_stored_payload(transactional_db, owner_client):
    message = _row(_active_peer())
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/"))
    assert orjson.loads(response.content)["payload"] == message.payload


@pytest.fixture
def summary_spy(monkeypatch):
    """Record, for every serialized summary, whether its payload (and its parent's) stayed deferred."""
    seen: list = []
    real = serializers.serialize_peer_message

    def spy(message, *args, **kwargs):
        if not kwargs.get("include_attachments", True) or not kwargs.get("include_payload"):
            parent = message.reply_to_message
            seen.append((
                "payload" in message.get_deferred_fields(),
                hasattr(message, "payload_text"),
                parent is None or "payload" in parent.get_deferred_fields(),
            ))
        return real(message, *args, **kwargs)

    monkeypatch.setattr(serializers, "serialize_peer_message", spy)
    monkeypatch.setattr(owner_views, "serialize_peer_message", spy)
    return seen


def test_summary_readers_never_load_the_payload(transactional_db, owner_client, summary_spy, monkeypatch):
    async def no_callback(*args, **kwargs):
        return 200, {}

    monkeypatch.setattr("twicc.peer.outbound.post_status", no_callback)
    peer = _active_peer()
    parent = _row(peer, message_id="pm_parent0000000001", direction=PeerMessageDirection.OUT)
    child = _row(peer, message_id="pm_child00000000001", reply_to=parent.message_id, reply_to_message=parent,
                 thread_id=parent.thread_id)
    asyncio.run(owner_client.get("/api/peer-messages/"))
    asyncio.run(owner_client.get("/api/peer-messages/", {"q": "body"}))
    asyncio.run(owner_client.get(f"/api/peer-messages/{child.pk}/?include_attachments=0"))
    asyncio.run(owner_client.post(f"/api/peer-messages/{child.pk}/done/"))
    invoke(["peer-message", parent.message_id])
    assert len(summary_spy) >= 5
    assert all(entry == (True, True, True) for entry in summary_spy), summary_spy


def test_the_delivery_envelope_reads_the_annotated_text(transactional_db, monkeypatch):
    async def no_callback(*args, **kwargs):
        return 200, {}

    monkeypatch.setattr("twicc.peer.outbound.post_status", no_callback)
    message = _row(_active_peer())
    success, envelope, errors = asyncio.run(peer_messages.mark_delivered(message))
    assert success and errors == []
    assert "> body" in envelope


def _add_pair(peer, index):
    parent = _row(peer, message_id=f"pm_par{index:013d}", direction=PeerMessageDirection.OUT)
    _row(peer, message_id=f"pm_chi{index:013d}", reply_to=parent.message_id, reply_to_message=parent,
         thread_id=parent.thread_id)


def _summary_query_count() -> tuple[int, int]:
    """``(queries, rows)`` of loading and serializing every summary, counted in THIS thread.

    Not around an async view: its ORM work runs in a worker thread, which the query capture
    of the test thread never sees (a count of 0 that can never fail).
    """
    with CaptureQueriesContext(connection) as queries:
        rows = list(peer_messages.peer_message_summary_queryset())
        for row in rows:
            serializers.serialize_peer_message(row)
    return len(queries), len(rows)


def test_summary_queries_do_not_grow_with_rows(transactional_db):
    peer = _active_peer()
    _add_pair(peer, 0)
    small, small_rows = _summary_query_count()
    for index in range(1, 11):
        _add_pair(peer, index)
    large, large_rows = _summary_query_count()
    assert (small_rows, large_rows) == (2, 22)
    # One query for the rows (peer, sessions and parent joined), one for the prefetched replies.
    assert small == large == 2


# ── Purge ────────────────────────────────────────────────────────────────────


def test_the_purge_removes_the_attachments_key_once(transactional_db):
    peer = _active_peer()
    now = djtz.now()
    old = now - timedelta(days=8)
    with_files = _row(peer, message_id="pm_files000000001", status=PeerMessageStatus.DELIVERED, resolved_at=old)
    text_only = _row(peer, message_id="pm_text0000000001", status=PeerMessageStatus.DELIVERED, resolved_at=old,
                     payload={"text": "only"}, attachments_meta=[])
    assert purge_expired_attachment_bytes(now=now) == 1
    with_files.refresh_from_db()
    text_only.refresh_from_db()
    assert with_files.payload == {"text": "body"}
    assert with_files.attachments_meta == [{"name": "a.txt", "media_type": "text/plain", "bytes": 14}]
    assert with_files.purged_at is not None
    assert text_only.purged_at is None
    assert purge_expired_attachment_bytes(now=now) == 0


def test_the_purge_loads_one_row_at_a_time(transactional_db):
    peer = _active_peer()
    old = djtz.now() - timedelta(days=8)
    for index in range(3):
        _row(peer, message_id=f"pm_purge{index:010d}", status=PeerMessageStatus.DONE, resolved_at=old)
    with CaptureQueriesContext(connection) as queries:
        assert purge_expired_attachment_bytes() == 3
    loads = [q["sql"] for q in queries if q["sql"].startswith("SELECT") and '"payload"' in q["sql"].split("FROM")[0]]
    assert len(loads) == 3
    assert all("LIMIT 1" in sql for sql in loads)
