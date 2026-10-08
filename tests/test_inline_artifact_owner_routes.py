"""Owner inline routes preserve broker wrapping and private data gates."""

import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import orjson
import pytest
from django.test import Client

from twicc import paths
from twicc.core.models import Project, Session
from twicc.inline_artifacts.views import inline_asset_response

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    project = Project.objects.create(id="inline-project", directory=str(tmp_path))
    record = {
        "artifact_id": "preferences",
        "src": "inline-artifacts/preferences/index.html",
        "line_num": 1,
        "text_block_index": 0,
        "tag_offset": 0,
        "title": "Preferences",
        "height": 360,
    }
    session = Session.objects.create(
        id="inline-session", project=project, inline_artifacts={"schema": 1, "publications": [record]}
    )
    root = tmp_path / "artifacts"
    folder = root / session.id / "inline-artifacts" / "preferences"
    folder.mkdir(parents=True)
    (folder / "index.html").write_bytes(b'<html><head></head><script src="app.js"></script></html>')
    (folder / "app.js").write_bytes(b"console.log(1)")
    (folder / "style.css").write_bytes(b"body { color: red; }")
    monkeypatch.setattr(paths, "get_artifacts_dir", lambda: root)
    return session, folder, f"/api/sessions/{session.id}/inline-artifacts/preferences/"


def body(response):
    try:
        return b"".join(response.streaming_content) if response.streaming else response.content
    finally:
        response.close()


def headers(prefix):
    return {"X-Twicc-Artifact-Doc": prefix + "index.html"}


def test_document_wrap_and_relative_assets(client, owner):
    _, _, prefix = owner
    response = client.get(prefix + "index.html", headers={"Sec-Fetch-Dest": "iframe"})
    assert response.status_code == 200
    assert "connect-src 'none'" in response["Content-Security-Policy"]
    assert b"/_twicc/artifact-broker-shim.js" in body(response)
    for name, content_type in [("app.js", "text/javascript"), ("style.css", "text/css")]:
        response = client.get(prefix + name)
        assert response.status_code == 200
        assert response["Content-Type"].split(";")[0] == content_type
        assert response["X-Content-Type-Options"] == "nosniff"
        assert "no-store" in response["Cache-Control"]
        assert "Content-Security-Policy" not in response
        assert body(response)


@pytest.mark.parametrize(
    "asset",
    ["../other/index.html", "%2e%2e/other/index.html", "%2e%2e/%2e%2e/index.html", "missing", "nested-link/secret"],
)
def test_unavailable_assets_are_404(client, owner, tmp_path, asset):
    _, folder, prefix = owner
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"OUTSIDE")
    (folder / "nested-link").symlink_to(outside, target_is_directory=True)
    assert client.get(prefix + asset).status_code == 404


def test_native_subagent_is_unavailable(client, owner):
    session, _, prefix = owner
    session.type = "subagent"
    session.save(update_fields=["type"])
    assert client.get(prefix + "index.html").status_code == 404
    assert client.put(prefix + "data/x", b"x", headers=headers(prefix)).status_code == 404


def test_unknown_publication_is_unavailable(client, owner):
    session, _, prefix = owner
    session.inline_artifacts = {}
    session.save(update_fields=["inline_artifacts"])
    assert client.get(prefix + "index.html").status_code == 404


def test_existing_password_auth_applies(owner, monkeypatch, settings):
    from twicc.auth import middleware

    settings.TWICC_PASSWORD_HASH = "test-password-hash"
    monkeypatch.setattr(middleware, "request_allowed", lambda *args: False)
    _, _, prefix = owner
    assert Client().get(prefix + "index.html").status_code == 401


def test_private_data_round_trip_list_and_remove(client, owner):
    _, folder, prefix = owner
    response = client.get(prefix + "data/", headers=headers(prefix))
    assert orjson.loads(body(response)) == {"files": []}
    assert client.put(prefix + "data/sub/x.json", b'{"a":1}', headers=headers(prefix)).status_code == 200
    assert body(client.get(prefix + "data/sub/x.json")) == b'{"a":1}'
    response = client.get(prefix + "data/", headers=headers(prefix))
    assert [(entry["path"], entry["size"]) for entry in orjson.loads(body(response))["files"]] == [("sub/x.json", 7)]
    assert client.delete(prefix + "data/sub/x.json", headers=headers(prefix)).status_code == 200
    assert not (folder / "data" / "sub" / "x.json").exists()


@pytest.mark.parametrize("method", ["put", "delete"])
def test_data_writes_require_own_document_and_data_boundary(client, owner, method):
    _, _, prefix = owner
    call = getattr(client, method)
    assert call(prefix + "data/x", headers={}).status_code == 405
    assert call(prefix + "data/x", headers=headers(prefix.replace("preferences", "sibling"))).status_code == 405
    assert call(prefix + "index.html", headers=headers(prefix)).status_code == 403
    assert call(prefix + "../sibling/data/x", headers=headers(prefix)).status_code == 403


def test_private_data_limits(client, owner):
    _, folder, prefix = owner
    assert (
        client.put(
            prefix + "data/x",
            b"x" * (10 * 1024 * 1024 + 1),
            content_type="application/octet-stream",
            headers=headers(prefix),
        ).status_code
        == 413
    )
    data = folder / "data"
    data.mkdir()
    for index in range(10):
        with (data / str(index)).open("wb") as file:
            file.truncate(10 * 1024 * 1024)
    response = client.put(prefix + "data/extra", b"x", headers=headers(prefix))
    assert response.status_code == 413
    assert orjson.loads(body(response))["error"] == "quota_exceeded"
    assert client.put(prefix + "data/0", b"small", headers=headers(prefix)).status_code == 200


def test_data_symlink_never_writes_or_reads_sibling(client, owner, tmp_path):
    _, folder, prefix = owner
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x").write_bytes(b"OUTSIDE")
    (folder / "data").symlink_to(outside, target_is_directory=True)
    assert client.put(prefix + "data/x", b"changed", headers=headers(prefix)).status_code == 404
    assert client.get(prefix + "data/x").status_code == 404
    assert (outside / "x").read_bytes() == b"OUTSIDE"


@pytest.mark.parametrize("asset,document", [("index.html", True), ("app.js", False)])
def test_http_response_uses_open_handle_after_file_replacement(client, owner, tmp_path, monkeypatch, asset, document):
    from twicc.inline_artifacts import views

    _, folder, prefix = owner
    original = views.open_source_asset
    expected = (folder / asset).read_bytes()
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")

    def replace(*args):
        file = original(*args)
        (folder / asset).unlink()
        (folder / asset).symlink_to(outside)
        return file

    monkeypatch.setattr(views, "open_source_asset", replace)
    response = client.get(prefix + asset, headers={"Sec-Fetch-Dest": "iframe"} if document else {})
    assert response.status_code == 200
    content = body(response)
    assert (b'<script src="app.js"></script>' if document else expected) in content
    assert b"OUTSIDE" not in content


@pytest.mark.parametrize("document,head", [(True, False), (True, True), (False, False), (False, True)])
def test_response_owns_handle_and_release(document, head):
    file = io.BytesIO(b"<p>inside</p>")
    released = []
    response = inline_asset_response(
        file, "text/html", as_document=document, head=head, release=lambda: released.append(True)
    )
    if head:
        assert body(response) == b""
    else:
        assert b"<p>inside</p>" in body(response)
    assert file.closed
    assert released == [True]


@pytest.mark.parametrize("asset,document", [("index.html", True), ("app.js", False)])
def test_directory_swap_before_http_open_never_reads_outside(owner, tmp_path, monkeypatch, asset, document):
    _, folder, prefix = owner
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / asset).write_bytes(b"OUTSIDE")
    barrier = threading.Barrier(2)
    original = os.open
    paused = False

    def paused_open(path, flags, *args, **kwargs):
        nonlocal paused
        if not paused and path == asset and kwargs.get("dir_fd") is not None:
            paused = True
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", paused_open)

    def request():
        response = Client().get(prefix + asset, headers={"Sec-Fetch-Dest": "iframe"} if document else {})
        return response.status_code, body(response)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(request)
        barrier.wait(timeout=5)
        folder.rename(folder.with_name("original"))
        folder.symlink_to(outside, target_is_directory=True)
        barrier.wait(timeout=5)
        status, content = future.result(timeout=5)
        assert status in (200, 404)
        assert b"OUTSIDE" not in content


@pytest.mark.parametrize("method", ["put", "delete", "get"])
def test_data_parent_swap_keeps_operations_on_pinned_directory(owner, tmp_path, monkeypatch, method):
    _, folder, prefix = owner
    nested = folder / "data" / "sub"
    nested.mkdir(parents=True)
    (nested / "x").write_bytes(b"INSIDE")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x").write_bytes(b"OUTSIDE")
    barrier = threading.Barrier(2)
    original = os.open
    paused = False

    def paused_open(path, flags, *args, **kwargs):
        nonlocal paused
        fd = original(path, flags, *args, **kwargs)
        if not paused and path == "sub" and flags & os.O_DIRECTORY:
            paused = True
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)
        return fd

    monkeypatch.setattr(os, "open", paused_open)

    def request():
        client = Client()
        if method == "put":
            response = client.put(prefix + "data/sub/x", b"CHANGED", headers=headers(prefix))
        elif method == "delete":
            response = client.delete(prefix + "data/sub/x", headers=headers(prefix))
        else:
            response = client.get(prefix + "data/sub/x")
        return response.status_code, body(response)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(request)
        barrier.wait(timeout=5)
        nested.rename(folder / "data" / "original-sub")
        nested.symlink_to(outside, target_is_directory=True)
        barrier.wait(timeout=5)
        status, content = future.result(timeout=5)
        assert status in (200, 404)
        assert b"OUTSIDE" not in content
    assert (outside / "x").read_bytes() == b"OUTSIDE"


def test_http_head_keeps_headers_and_closes_file(client, owner):
    _, _, prefix = owner
    for asset, expected_length in [("index.html", None), ("app.js", 14)]:
        response = client.head(prefix + asset, headers={"Sec-Fetch-Dest": "iframe"})
        assert response.status_code == 200
        assert body(response) == b""
        assert int(response["Content-Length"]) > 0
        if expected_length is not None:
            assert int(response["Content-Length"]) == expected_length
        else:
            assert "connect-src 'none'" in response["Content-Security-Policy"]


def test_read_error_releases_handle_with_stable_error():
    from twicc.inline_artifacts.files import InlineArtifactUnavailable

    class BrokenFile(io.BytesIO):
        def read(self, *args):
            raise OSError("source path must stay private")

    file = BrokenFile(b"html")
    released = []
    with pytest.raises(InlineArtifactUnavailable, match="unavailable"):
        inline_asset_response(file, "text/html", as_document=True, head=False, release=lambda: released.append(True))
    assert file.closed
    assert released == [True]


@pytest.mark.parametrize("document,head", [(True, False), (True, True), (False, False), (False, True)])
def test_response_closes_handle_exactly_once(document, head):
    class CountingFile(io.BytesIO):
        close_count = 0

        def close(self):
            self.close_count += 1
            super().close()

    file = CountingFile(b"<p>inside</p>")
    response = inline_asset_response(file, "text/html", as_document=document, head=head)
    body(response)
    response.close()
    assert file.close_count == 1


@pytest.mark.parametrize("method", ["put", "delete"])
def test_data_operation_errors_keep_existing_payload_codes(client, owner, monkeypatch, method):
    _, folder, prefix = owner
    data = folder / "data"
    data.mkdir()
    (data / "x").write_bytes(b"INSIDE")
    operation = "replace" if method == "put" else "unlink"
    original = getattr(os, operation)

    def refuse(*args, **kwargs):
        if (method == "put" and kwargs.get("dst_dir_fd") is not None) or (method == "delete" and args[0] == "x"):
            raise PermissionError("operation refused")
        return original(*args, **kwargs)

    monkeypatch.setattr(os, operation, refuse)
    response = getattr(client, method)(
        prefix + "data/x", b"changed" if method == "put" else None, headers=headers(prefix)
    )
    assert response.status_code == 500
    assert orjson.loads(body(response))["error"] == ("write_failed" if method == "put" else "delete_failed")
    assert (data / "x").read_bytes() == b"INSIDE"
