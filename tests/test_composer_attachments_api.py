"""REST endpoints of the composer staging store: content, status, release, touch.

Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.2, §6.1.4, §12.
Each async scenario runs in one ``asyncio.run``: the upload locks are per event loop.
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
import time
import uuid
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient
from PIL import Image

from twicc.core.services.attachments import lifecycle, staging
from twicc.core.services.attachments import views as attachment_views
from twicc.core.services.attachments.types import AttachmentRef
from twicc.paths import get_artifacts_dir, get_composer_attachments_dir
from twicc.uploads import locks, store

pytestmark = pytest.mark.django_db(transaction=True)

BASE = "/api/composer-attachments/"
UPLOADS_URL = "/api/uploads/"
TUS_HEADERS = {"Tus-Resumable": "1.0.0"}
OCTET = "application/offset+octet-stream"
CHUNK = 64 * 1024


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


class FakeLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))


@pytest.fixture(autouse=True)
def layer(monkeypatch):
    fake = FakeLayer()
    monkeypatch.setattr("twicc.uploads.broadcast.get_channel_layer", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def fresh_upload_locks():
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()
    yield
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def new_ref(bucket="draft-session"):
    return AttachmentRef(bucket, str(uuid.uuid4()))


@pytest.fixture
def ref():
    return new_ref()


def entry_path(ref):
    return Path(os.path.realpath(get_composer_attachments_dir())) / ref.bucket / ref.id


def tombstone_path(ref):
    return Path(os.path.realpath(get_composer_attachments_dir())) / ref.bucket / ".released" / ref.id


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_ready(ref, name="file.bin", content=b"hello"):
    entry = entry_path(ref)
    (entry / "file").mkdir(parents=True, exist_ok=True)
    (entry / "file" / name).write_bytes(content)
    (entry / "ready.json").write_bytes(orjson.dumps({"filename": name, "size": len(content)}))
    return entry


def make_promoted(ref, *, session_id="sess-1", name="report.pdf", content=b"%PDF-1.4 promoted", final_path=None):
    """An entry promoted by a commit: ``promoted.json``, old ``ready.json`` kept, ``file/`` emptied."""
    entry = make_ready(ref, name=name, content=content)
    (entry / "file" / name).unlink()
    attachments = get_artifacts_dir() / session_id / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    final = attachments / name
    final.write_bytes(content)
    record = {
        "session_id": session_id,
        "final_path": str(final_path or final),
        "final_name": name,
        "kind": "PDF",
        "original_name": name,
        "size": len(content),
    }
    (entry / "promoted.json").write_bytes(orjson.dumps(record))
    return final


async def post_json(client, url, body):
    data = body if isinstance(body, (bytes, str)) else orjson.dumps(body)
    return await client.post(url, data=data, content_type="application/json")


async def status(client, refs):
    response = await post_json(client, f"{BASE}status/", {"refs": [r._asdict() for r in refs]})
    assert response.status_code == 200, response.content
    return response.json()["statuses"]


async def delete_entry(client, ref):
    return await client.delete(f"{BASE}{ref.bucket}/{ref.id}/")


async def touch(client, refs, holder):
    return await post_json(client, f"{BASE}touch/", {"refs": [r._asdict() for r in refs], "holder": holder})


async def create_upload(client, ref, *, size=10, client_id=None, filename="x.bin"):
    body = {
        "filename": filename,
        "size": size,
        "client_id": client_id or uuid.uuid4().hex[:16],
        "fingerprint": "test:0",
        "origin": {"panel": "composer", "key": f"{ref.bucket}/{ref.id}"},
    }
    return await post_json(client, UPLOADS_URL, body)


async def patch_bytes(client, upload_id, offset, data):
    return await client.patch(
        f"/api/uploads/{upload_id}/",
        data=data,
        content_type=OCTET,
        headers={**TUS_HEADERS, "Upload-Offset": str(offset)},
    )


async def get_content(client, ref):
    return await client.get(f"{BASE}{ref.bucket}/{ref.id}/content")


async def read_all(response):
    return b"".join([chunk async for chunk in response.streaming_content])


def image_bytes(fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 10, 10)).save(buffer, format=fmt)
    return buffer.getvalue()


def run(coro):
    return asyncio.run(coro)


# ── Brief contract ────────────────────────────────────────────────────────────


def test_promoted_entry_release_contract(client, ref):
    original_bytes = b"%PDF-1.7 original bytes"
    promoted_artifact = make_promoted(ref, content=original_bytes)

    async def scenario():
        [ref_status] = await status(client, [ref])
        assert ref_status["state"] == "promoted"
        assert (await delete_entry(client, ref)).status_code == 204
        assert (await delete_entry(client, ref)).status_code == 204
        assert promoted_artifact.read_bytes() == original_bytes
        assert (await create_upload(client, ref)).status_code == 410

    run(scenario())
    assert not entry_path(ref).exists()


# ── Status ────────────────────────────────────────────────────────────────────


def test_status_precedence_and_order(client):
    promoted, gone_promoted, ready, uploading, absent, not_ready = (new_ref() for _ in range(6))
    make_promoted(promoted)
    final = make_promoted(gone_promoted, session_id="sess-2")
    final.unlink()
    # The old ready file is back in place: a missing promoted target still wins over it.
    (entry_path(gone_promoted) / "file" / "report.pdf").write_bytes(b"%PDF-1.4 promoted")
    make_ready(ready)
    (entry_path(not_ready) / "file").mkdir(parents=True)

    async def scenario():
        created = await create_upload(client, uploading, size=10, client_id="attempt-1")
        assert created.status_code == 201
        upload_id = created.json()["id"]
        assert (await patch_bytes(client, upload_id, 0, b"abcd")).status_code == 204
        order = [uploading, absent, ready, gone_promoted, promoted, not_ready]
        return await status(client, order)

    statuses = run(scenario())
    assert statuses == [
        {"bucket": uploading.bucket, "id": uploading.id, "state": "uploading", "client_id": "attempt-1", "offset": 4},
        {"bucket": absent.bucket, "id": absent.id, "state": "missing"},
        {"bucket": ready.bucket, "id": ready.id, "state": "ready"},
        {"bucket": gone_promoted.bucket, "id": gone_promoted.id, "state": "missing"},
        {"bucket": promoted.bucket, "id": promoted.id, "state": "promoted"},
        {"bucket": not_ready.bucket, "id": not_ready.id, "state": "missing"},
    ]


def test_status_of_a_finalizing_upload_reports_its_full_size(client, ref):
    async def scenario():
        created = await create_upload(client, ref, size=6, client_id="attempt-f")
        upload_id = created.json()["id"]
        await asyncio.to_thread(store.update_metadata, upload_id, state=store.STATE_FINALIZING)
        return await status(client, [ref])

    [entry] = run(scenario())
    assert entry == {"bucket": ref.bucket, "id": ref.id, "state": "uploading", "client_id": "attempt-f", "offset": 6}


def test_status_ignores_terminal_uploads(client, ref):
    async def scenario():
        created = await create_upload(client, ref, size=6)
        upload_id = created.json()["id"]
        await asyncio.to_thread(store.cancel_upload, upload_id)
        return await status(client, [ref])

    [entry] = run(scenario())
    assert entry["state"] == "missing"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        {},
        {"refs": "x"},
        {"refs": [{"bucket": "b"}]},
        {"refs": [{"bucket": "b", "id": "not-a-uuid"}]},
        {"refs": [{"bucket": "..", "id": str(uuid.uuid4())}]},
        {"refs": [{"bucket": "a/b", "id": str(uuid.uuid4())}]},
        {"refs": [{"bucket": "b", "id": str(uuid.uuid4()).upper()}]},
        {"refs": ["b/x"]},
    ],
)
def test_status_rejects_invalid_bodies(client, body):
    response = run(post_json(client, f"{BASE}status/", body))
    assert response.status_code == 400


# ── Release ───────────────────────────────────────────────────────────────────


def test_release_absent_entry_writes_tombstone(client, ref):
    response = run(delete_entry(client, ref))
    assert response.status_code == 204
    assert tombstone_path(ref).is_file()
    assert not entry_path(ref).exists()


def test_release_removes_entry_and_cancels_live_upload(client, ref):
    async def scenario():
        created = await create_upload(client, ref, size=10)
        upload_id = created.json()["id"]
        await patch_bytes(client, upload_id, 0, b"abc")
        response = await delete_entry(client, ref)
        return upload_id, response

    upload_id, response = run(scenario())
    assert response.status_code == 204
    assert store.read_metadata(upload_id)["state"] == store.STATE_CANCELLED
    assert not entry_path(ref).exists()
    assert tombstone_path(ref).is_file()


def test_release_settles_a_crashed_finalizing_upload(client, ref, monkeypatch):
    recovered = []

    async def fake_recover(upload_id):
        recovered.append(upload_id)

    monkeypatch.setattr("twicc.uploads.views.recover_upload", fake_recover)

    async def scenario():
        created = await create_upload(client, ref, size=10)
        upload_id = created.json()["id"]
        await asyncio.to_thread(store.update_metadata, upload_id, state=store.STATE_FINALIZING)
        return upload_id, await delete_entry(client, ref)

    upload_id, response = run(scenario())
    assert response.status_code == 204
    assert recovered == [upload_id]
    assert store.read_metadata(upload_id)["state"] == store.STATE_CANCELLED
    assert not entry_path(ref).exists()


def test_release_with_a_failed_cancellation_answers_500_and_keeps_the_entry(client, ref, monkeypatch):
    async def scenario():
        created = await create_upload(client, ref, size=10)
        upload_id = created.json()["id"]
        make_ready(ref, name="keep.bin")
        monkeypatch.setattr(store, "cancel_upload", lambda _id: store.WriteOutcome(None, 500))
        return upload_id, await delete_entry(client, ref)

    upload_id, response = run(scenario())
    assert response.status_code == 500
    assert store.read_metadata(upload_id)["state"] == store.STATE_ACTIVE
    assert (entry_path(ref) / "file" / "keep.bin").is_file()
    assert (entry_path(ref) / "ready.json").is_file()


def test_release_refuses_a_symlinked_bucket(client, ref, tmp_path):
    outside = tmp_path / "outside"
    (outside / ref.id / "file").mkdir(parents=True)
    root = Path(os.path.realpath(get_composer_attachments_dir()))
    root.mkdir(parents=True)
    (root / ref.bucket).symlink_to(outside)
    response = run(delete_entry(client, ref))
    assert response.status_code == 500
    assert (outside / ref.id / "file").is_dir()
    assert not (outside / ".released").exists()


def test_release_never_follows_a_symlinked_entry(client, ref, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    entry = entry_path(ref)
    entry.parent.mkdir(parents=True)
    entry.symlink_to(outside)
    response = run(delete_entry(client, ref))
    assert response.status_code == 204
    assert (outside / "precious.txt").read_text() == "keep"
    assert not os.path.lexists(entry)


def test_release_rejects_invalid_refs(client):
    response = run(client.delete(f"{BASE}bucket/not-a-uuid/"))
    assert response.status_code == 400
    response = run(client.delete(f"{BASE}../{uuid.uuid4()}/"))
    assert response.status_code in (400, 404)


def test_release_refs_releases_every_ref_even_after_a_failure(monkeypatch):
    failing, other = new_ref(), new_ref()
    make_ready(failing)
    make_ready(other)
    real_settle = lifecycle.settle_entry_uploads

    async def settle(ref):
        if ref == failing:
            raise lifecycle.SettleError("u", 500)
        await real_settle(ref)

    monkeypatch.setattr(lifecycle, "settle_entry_uploads", settle)
    with pytest.raises(lifecycle.SettleError):
        run(lifecycle.release_refs((failing, other)))
    assert entry_path(failing).exists()
    assert not entry_path(other).exists()
    assert tombstone_path(other).is_file()


# ── Release vs creation (barrier-controlled) ─────────────────────────────────


async def _wait_for(event: threading.Event, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("barrier never reached")
        await asyncio.sleep(0.005)


def test_release_waits_for_an_in_flight_creation_and_wins(client, ref, monkeypatch):
    """Creation first (blocked inside its reset), release second: the entry stays released."""
    entered, go = threading.Event(), threading.Event()
    real_reset = lifecycle.reset_entry

    def blocking_reset(target):
        entered.set()
        assert go.wait(5)
        return real_reset(target)

    monkeypatch.setattr(lifecycle, "reset_entry", blocking_reset)

    async def scenario():
        creation = asyncio.create_task(create_upload(client, ref, size=10, client_id="inflight"))
        await _wait_for(entered)
        release = asyncio.create_task(delete_entry(client, ref))
        await asyncio.sleep(0.05)
        assert not release.done()  # waits for the creation lock
        go.set()
        created = await creation
        released = await release
        upload_id = created.json()["id"]
        patched = await patch_bytes(client, upload_id, 0, b"0123456789")
        repeat = await create_upload(client, ref, client_id="another")
        return created, released, upload_id, patched, repeat

    created, released, upload_id, patched, repeat = run(scenario())
    assert created.status_code == 201
    assert released.status_code == 204
    assert store.read_metadata(upload_id)["state"] == store.STATE_CANCELLED
    assert patched.status_code != 204
    assert repeat.status_code == 410
    assert not entry_path(ref).exists()
    assert tombstone_path(ref).is_file()


def test_creation_waits_for_an_in_flight_release_and_is_refused(client, ref, monkeypatch):
    """Release first (blocked inside its settle, holding the creation lock), creation second: 410."""
    make_ready(ref)
    entered, go = asyncio.Event(), asyncio.Event()
    real_settle = lifecycle.settle_entry_uploads

    async def blocking_settle(target):
        entered.set()
        await go.wait()
        await real_settle(target)

    monkeypatch.setattr(lifecycle, "settle_entry_uploads", blocking_settle)

    async def scenario():
        release = asyncio.create_task(delete_entry(client, ref))
        await asyncio.wait_for(entered.wait(), 5)
        assert locks.get_creation_lock().locked()
        creation = asyncio.create_task(create_upload(client, ref, client_id="late"))
        await asyncio.sleep(0.05)
        assert not creation.done()
        go.set()
        return await release, await creation

    released, created = run(scenario())
    assert released.status_code == 204
    assert created.status_code == 410
    assert not entry_path(ref).exists()
    assert not store.list_metadata()


# ── Touch ─────────────────────────────────────────────────────────────────────


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))
    return old


def test_touch_draft_removes_committed_marker(client, ref):
    entry = make_ready(ref)
    (entry / "committed.json").write_bytes(b'{"at": "x"}')
    old = _age(entry, 3600)
    response = run(touch(client, [ref], "draft"))
    assert response.status_code == 204
    assert not (entry / "committed.json").exists()
    assert entry.stat().st_mtime > old + 3000
    assert (entry / "ready.json").exists()


def test_touch_snapshot_preserves_committed_marker(client, ref):
    entry = make_ready(ref)
    (entry / "committed.json").write_bytes(b'{"at": "x"}')
    old = _age(entry, 3600)
    response = run(touch(client, [ref], "snapshot"))
    assert response.status_code == 204
    assert (entry / "committed.json").exists()
    assert entry.stat().st_mtime > old + 3000


def test_touch_never_creates_an_absent_entry(client, ref):
    for holder in ("draft", "snapshot"):
        response = run(touch(client, [ref], holder))
        assert response.status_code == 204
    assert not entry_path(ref).exists()
    assert not entry_path(ref).parent.exists()


@pytest.mark.parametrize(
    "body",
    [
        b"nope",
        {"refs": [], "holder": "other"},
        {"refs": []},
        {"refs": [{"bucket": "b", "id": "x"}], "holder": "draft"},
        {"refs": None, "holder": "draft"},
        {"holder": "draft"},
    ],
)
def test_touch_rejects_invalid_bodies(client, body):
    response = run(post_json(client, f"{BASE}touch/", body))
    assert response.status_code == 400


def test_touch_refs_rejects_an_invalid_holder(ref):
    with pytest.raises(ValueError):
        lifecycle.touch_refs((ref,), holder="other")


# ── Content: headers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "content", "media_type", "inline"),
    [
        ("a.png", image_bytes("PNG"), "image/png", True),
        ("a.jpg", image_bytes("JPEG"), "image/jpeg", True),
        ("a.gif", image_bytes("GIF"), "image/gif", True),
        ("a.webp", image_bytes("WEBP"), "image/webp", True),
        # Another raster format is an image kind, but never served inline.
        ("a.bmp", image_bytes("BMP"), "application/octet-stream", False),
        ("doc.pdf", b"%PDF-1.4\n%binary\n", "application/pdf", True),
        ("notes.txt", "héllo wörld\n".encode(), "text/plain; charset=utf-8", True),
        ("noext", b"plain text without extension", "text/plain; charset=utf-8", True),
        # The type comes from the bytes, never from the name alone.
        ("fake.png", b"just some text", "text/plain; charset=utf-8", True),
        ("fake.pdf", b"\x00\x01\x02binary", "application/octet-stream", False),
        ("pic.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "application/octet-stream", False),
        ("page.html", b"<!DOCTYPE html><html><script>alert(1)</script></html>", "application/octet-stream", False),
        ("page.txt", b"  <html><body>hi</body></html>", "application/octet-stream", False),
        ("app.js", b"alert(document.cookie)", "application/octet-stream", False),
        ("mod.mjs", b"export default 1", "application/octet-stream", False),
        ("data.xml", b"<?xml version='1.0'?><a/>", "application/octet-stream", False),
        ("page.xhtml", b"hello", "application/octet-stream", False),
        ("empty.txt", b"", "application/octet-stream", False),
        ("blob.bin", bytes(range(256)), "application/octet-stream", False),
        ("latin1.txt", "caf\xe9".encode("latin-1"), "application/octet-stream", False),
    ],
)
def test_content_headers(client, ref, name, content, media_type, inline):
    make_ready(ref, name=name, content=content)

    async def scenario():
        response = await get_content(client, ref)
        return response, await read_all(response)

    response, body = run(scenario())
    assert response.status_code == 200
    assert body == content
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Type"] == media_type
    disposition = response["Content-Disposition"]
    assert disposition.startswith("inline" if inline else "attachment")
    assert response["Content-Length"] == str(len(content))


def test_content_type_detection_reads_at_most_64_kib(ref, monkeypatch):
    head = "a" * (CHUNK - 1) + "é"  # a two-byte sequence cut at the 64 KiB boundary
    make_ready(ref, name="big.txt", content=head.encode() + b"\x00" * 100)
    entry = staging.load_entry(ref)
    assert staging.content_media_type(entry) == ("text/plain; charset=utf-8", True)


def test_content_of_a_promoted_entry_streams_the_promoted_file(client, ref):
    make_promoted(ref, content=b"%PDF-1.4 the promoted bytes")

    async def scenario():
        response = await get_content(client, ref)
        return response, await read_all(response)

    response, body = run(scenario())
    assert response.status_code == 200
    assert body == b"%PDF-1.4 the promoted bytes"
    assert response["Content-Type"] == "application/pdf"


def test_content_404s(client):
    gone, absent, not_ready = new_ref(), new_ref(), new_ref()
    make_promoted(gone).unlink()
    (entry_path(not_ready) / "file").mkdir(parents=True)

    async def scenario():
        return [(await get_content(client, r)).status_code for r in (gone, absent, not_ready)]

    assert run(scenario()) == [404, 404, 404]


def test_content_rejects_an_invalid_ref(client):
    response = run(client.get(f"{BASE}bucket/not-a-uuid/content"))
    assert response.status_code == 400


def test_content_route_has_no_trailing_slash(client, ref):
    make_ready(ref)
    response = run(client.get(f"{BASE}{ref.bucket}/{ref.id}/content/"))
    assert response.status_code == 404


# ── Content: symlink escapes ──────────────────────────────────────────────────


def test_content_refuses_a_staged_file_symlink(client, ref, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    entry = make_ready(ref, name="x.txt", content=b"secret")
    (entry / "file" / "x.txt").unlink()
    (entry / "file" / "x.txt").symlink_to(secret)
    assert run(get_content(client, ref)).status_code == 404


def test_content_refuses_a_symlinked_bucket(client, ref, tmp_path):
    outside = tmp_path / "outside"
    (outside / ref.id / "file").mkdir(parents=True)
    (outside / ref.id / "file" / "x.txt").write_bytes(b"secret")
    (outside / ref.id / "ready.json").write_bytes(orjson.dumps({"filename": "x.txt", "size": 6}))
    root = Path(os.path.realpath(get_composer_attachments_dir()))
    root.mkdir(parents=True)
    (root / ref.bucket).symlink_to(outside)
    assert run(get_content(client, ref)).status_code == 404


def test_content_refuses_a_promoted_file_outside_its_attachments_dir(client, tmp_path):
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-secret")
    elsewhere, other_session, link = new_ref(), new_ref(), new_ref()
    # A final path outside any attachments directory.
    make_promoted(elsewhere, final_path=secret)
    # A final path in another session's attachments directory.
    foreign = get_artifacts_dir() / "sess-other" / "attachments" / "x.pdf"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"%PDF-foreign")
    make_promoted(other_session, session_id="sess-1", final_path=foreign)
    # A symlink inside the owner's attachments directory pointing outside it.
    attachments = get_artifacts_dir() / "sess-3" / "attachments"
    attachments.mkdir(parents=True)
    (attachments / "link.pdf").symlink_to(secret)
    make_promoted(link, session_id="sess-3", name="other.pdf", final_path=attachments / "link.pdf")

    async def scenario():
        return [(await get_content(client, r)).status_code for r in (elsewhere, other_session, link)]

    assert run(scenario()) == [404, 404, 404]


def _swap_before_open(monkeypatch, swap):
    """Run *swap(path)* between the path validation and the open of the streamed file."""
    real_open = attachment_views._open_content

    def swapping_open(path):
        swap(Path(path))
        return real_open(path)

    monkeypatch.setattr(attachment_views, "_open_content", swapping_open)


def test_content_refuses_a_file_replaced_between_validation_and_open(client, ref, tmp_path, monkeypatch):
    make_ready(ref, name="x.txt", content=b"validated")
    other = tmp_path / "other.txt"
    other.write_bytes(b"swapped in")
    _swap_before_open(monkeypatch, lambda path: os.replace(other, path))
    assert run(get_content(client, ref)).status_code == 404


def test_content_refuses_a_symlink_swapped_in_before_open(client, ref, tmp_path, monkeypatch):
    make_ready(ref, name="x.txt", content=b"validated")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")

    def to_symlink(path):
        path.unlink()
        path.symlink_to(secret)

    _swap_before_open(monkeypatch, to_symlink)
    assert run(get_content(client, ref)).status_code == 404


def test_content_refuses_a_fifo_swapped_in_before_open_without_blocking(client, ref, monkeypatch):
    make_ready(ref, name="x.txt", content=b"validated")

    def to_fifo(path):
        path.unlink()
        os.mkfifo(path)

    _swap_before_open(monkeypatch, to_fifo)
    assert run(get_content(client, ref)).status_code == 404


def test_content_refuses_a_promoted_file_swapped_before_open(client, ref, tmp_path, monkeypatch):
    make_promoted(ref, content=b"%PDF-1.4 promoted")
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 other")
    _swap_before_open(monkeypatch, lambda path: os.replace(other, path))
    assert run(get_content(client, ref)).status_code == 404


def test_content_media_type_comes_from_the_streamed_file(client, ref, monkeypatch):
    """The type is sniffed from the descriptor that is streamed, never from a second open by name."""
    make_ready(ref, name="x.txt", content=b"plain text")
    real_open = open

    def no_reopen(path, *args, **kwargs):
        if str(path).endswith("x.txt"):
            raise AssertionError("the content was reopened by name")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", no_reopen)

    async def scenario():
        response = await get_content(client, ref)
        return response, await read_all(response)

    response, body = run(scenario())
    assert response.status_code == 200
    assert body == b"plain text"
    assert response["Content-Type"] == "text/plain; charset=utf-8"


# ── Content: streaming ────────────────────────────────────────────────────────


class RecordingFile(io.FileIO):
    """A real file that records each read size and its close."""

    instances: list[RecordingFile] = []

    def __init__(self, path, *, block: threading.Event | None = None, block_on: int | None = None):
        super().__init__(path, "rb")
        self.reads: list[int] = []
        self.block = block
        self.block_on = block_on
        RecordingFile.instances.append(self)

    def read(self, size=-1):
        self.reads.append(size)
        if self.block is not None and len(self.reads) == self.block_on:
            self.block.wait(5)
        return super().read(size)


@pytest.fixture
def recording(monkeypatch):
    RecordingFile.instances = []
    options = {}
    monkeypatch.setattr(attachment_views, "_open_content", lambda path: RecordingFile(path, **options))
    return options


def test_large_sparse_file_streams_lazily_and_closes(client, ref, recording):
    size = 1024 * 1024 * 1024
    entry = make_ready(ref, name="huge.bin", content=b"")
    path = entry / "file" / "huge.bin"
    with open(path, "r+b") as f:
        f.truncate(size)
    (entry / "ready.json").write_bytes(orjson.dumps({"filename": "huge.bin", "size": size}))

    async def scenario():
        response = await get_content(client, ref)
        assert response.status_code == 200
        assert response.is_async
        iterator = aiter(response.streaming_content)
        first = await anext(iterator)
        [file] = RecordingFile.instances
        reads_after_first = list(file.reads)
        response.close()
        return response, first, file, reads_after_first

    response, first, file, reads = run(scenario())
    assert response["Content-Length"] == str(size)
    assert len(first) == CHUNK
    assert reads == [CHUNK]
    assert all(0 < r <= CHUNK for r in file.reads)
    assert file.closed


def test_full_stream_reads_64_kib_blocks_and_closes(client, ref, recording):
    content = os.urandom(CHUNK * 2 + 123)
    make_ready(ref, name="data.bin", content=content)

    async def scenario():
        response = await get_content(client, ref)
        return await read_all(response)

    body = run(scenario())
    assert body == content
    [file] = RecordingFile.instances
    assert all(0 < r <= CHUNK for r in file.reads)
    assert len(file.reads) >= 3
    assert file.closed


def test_disconnect_mid_stream_closes_the_file(client, ref, recording):
    content = os.urandom(CHUNK * 3)
    make_ready(ref, name="data.bin", content=content)
    blocked = threading.Event()
    recording.update(block=blocked, block_on=2)

    async def scenario():
        response = await get_content(client, ref)
        iterator = aiter(response.streaming_content)
        await anext(iterator)
        consumer = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0.05)
        consumer.cancel()  # the ASGI server cancels the sending task on a disconnect
        with pytest.raises(asyncio.CancelledError):
            await consumer
        blocked.set()
        await asyncio.sleep(0.05)
        await iterator.aclose()

    run(scenario())
    [file] = RecordingFile.instances
    assert file.closed


def test_asgi_disconnect_during_a_read_closes_the_file_without_response_close(ref, recording):
    """As Django's ASGI handler: ``aclosing(aiter(response))`` in a task cancelled on disconnect,
    and no ``response.close()`` afterwards (the handler skips it for a cancelled request)."""
    from contextlib import aclosing

    from django.test import AsyncRequestFactory

    content = os.urandom(CHUNK * 3)
    make_ready(ref, name="data.bin", content=content)
    blocked = threading.Event()
    recording.update(block=blocked, block_on=2)
    reading = threading.Event()

    async def scenario():
        request = AsyncRequestFactory().get(f"{BASE}{ref.bucket}/{ref.id}/content")
        response = await attachment_views.attachment_content(request, ref.bucket, ref.id)

        async def send_response():
            async with aclosing(aiter(response)) as parts:
                async for _part in parts:
                    reading.set()

        sender = asyncio.create_task(send_response())
        await _wait_for(reading)
        await asyncio.sleep(0.05)  # now blocked in the second read
        sender.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sender
        [file] = RecordingFile.instances
        closed_at_cancel = file.closed
        blocked.set()
        return closed_at_cancel

    assert run(scenario()) is True


# ── Methods and password ──────────────────────────────────────────────────────


def test_methods_not_allowed(client, ref):
    async def scenario():
        return [
            (await client.get(f"{BASE}status/")).status_code,
            (await client.get(f"{BASE}touch/")).status_code,
            (await client.post(f"{BASE}{ref.bucket}/{ref.id}/content")).status_code,
            (await client.get(f"{BASE}{ref.bucket}/{ref.id}/")).status_code,
        ]

    assert run(scenario()) == [405, 405, 405, 405]


def test_routes_require_the_password(settings, ref):
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$1$salt$hash"
    client = AsyncClient()

    async def scenario():
        return [
            (await client.get(f"{BASE}{ref.bucket}/{ref.id}/content")).status_code,
            (await post_json(client, f"{BASE}status/", {"refs": []})).status_code,
            (await client.delete(f"{BASE}{ref.bucket}/{ref.id}/")).status_code,
            (await post_json(client, f"{BASE}touch/", {"refs": [], "holder": "draft"})).status_code,
        ]

    assert run(scenario()) == [401, 401, 401, 401]
    assert not tombstone_path(ref).exists()
