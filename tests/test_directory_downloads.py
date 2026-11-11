"""Authorized directory ZIP HTTP contracts, independent of archive internals."""

import asyncio
import io
from pathlib import Path
from urllib.parse import urlencode
import zipfile

import pytest
from django.test import AsyncClient
from django.utils import timezone

from tests.test_file_downloads import _body, _request
from twicc import directory_archives, views
from twicc.core.models import Project, Session, SessionType

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def sources(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    nested = project_dir / "résumé"
    nested.mkdir()
    (nested / "notes.txt").write_bytes(b"directory content")
    extra = tmp_path / "session-only"
    extra.mkdir()
    (extra / "session.txt").write_bytes(b"session content")
    project = Project.objects.create(id="directory-project", directory=str(project_dir))
    now = timezone.now()
    session = Session.objects.create(
        id="directory-session", project=project, provider="claude_code", file_path="session.jsonl",
        type=SessionType.SESSION, created_at=now, last_new_content_at=now, cwd=str(extra),
    )
    return project, session, nested, extra


def route(sources, scope, directory, **params):
    project, session, _, _ = sources
    prefix = {
        "project": f"/api/projects/{project.id}/",
        "session": f"/api/projects/{project.id}/sessions/{session.id}/",
        "standalone": "/api/",
    }[scope]
    return prefix + "directory-download/?" + urlencode({"path": str(directory), **params})


@pytest.mark.parametrize("scope", ["project", "session", "standalone"])
def test_directory_download_route_matrix(client, sources, scope, recwarn):
    _, _, nested, _ = sources
    nested.chmod(0o555)
    try:
        response = _request(client, route(sources, scope, nested, root=nested))
    finally:
        nested.chmod(0o755)
    assert response.status_code == 200
    assert response.is_async
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == "attachment; filename*=utf-8''r%C3%A9sum%C3%A9.zip"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert set(response["Cache-Control"].split(", ")) == {
        "max-age=0", "no-cache", "no-store", "must-revalidate", "private",
    }
    assert response["CDN-Cache-Control"] == "no-store"
    assert "Expires" in response
    assert "Content-Length" not in response
    with zipfile.ZipFile(io.BytesIO(_body(response))) as archive:
        assert set(archive.namelist()) == {"résumé/", "résumé/notes.txt"}
        assert archive.read("résumé/notes.txt") == b"directory content"
    assert not any("synchronous iterators" in str(warning.message) for warning in recwarn)


def test_directory_download_root_and_session_allowed_directory(client, sources):
    project, _, _, extra = sources
    for scope, directory, entry, content in [
        ("project", project.directory, "project/résumé/notes.txt", b"directory content"),
        ("session", extra, "session-only/session.txt", b"session content"),
        ("standalone", extra, "session-only/session.txt", b"session content"),
    ]:
        response = _request(client, route(sources, scope, directory))
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(_body(response))) as archive:
            assert archive.read(entry) == content


def test_directory_download_access(client, sources, settings, tmp_path):
    project, _, nested, extra = sources
    urls = [
        (route(sources, "project", extra), 403, "backend.file_tree.path_outside_allowed"),
        (route(sources, "standalone", extra, root=project.directory), 403, "backend.files.outside_root"),
    ]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "child").mkdir()
    (Path(project.directory) / "escape").symlink_to(outside, target_is_directory=True)
    urls.append((route(sources, "standalone", Path(project.directory) / "escape" / "child", root=project.directory),
                 404, "backend.file_tree.directory_missing"))
    for url, status, key in urls:
        response = _request(client, url)
        assert response.status_code == status
        assert response.json()["ui_message"]["key"] == key
    response = _request(client, route(sources, "project", nested).replace(project.id, "unknown"))
    assert response.status_code == 404
    other = Project.objects.create(id="other-project", directory=project.directory)
    response = _request(client, route(sources, "session", nested).replace(project.id, other.id))
    assert response.status_code == 404
    settings.TWICC_PASSWORD_HASH = "configured-password"
    settings.TWICC_DEV_LOCAL_BYPASS = False
    for scope in ["project", "session", "standalone"]:
        response = _request(AsyncClient(), route(sources, scope, nested))
        assert response.status_code == 401
        assert response.json()["ui_message"]["key"] == "backend.common.authenticationRequired"


@pytest.mark.parametrize("scope", ["project", "session", "standalone"])
def test_directory_download_invalid_source(client, sources, scope):
    project, _, nested, _ = sources
    missing = route(sources, scope, nested).partition("?")[0]
    response = _request(client, missing)
    assert response.status_code == 400
    assert response.json()["ui_message"]["key"] == "backend.file_tree.path_required"
    link = Path(project.directory) / "link"
    link.symlink_to(nested, target_is_directory=True)
    for directory in ["relative", "bad\x00path", nested / "missing", nested / "notes.txt", link]:
        response = _request(client, route(sources, scope, directory))
        assert response.status_code == 404
        assert response.json()["ui_message"]["key"] == "backend.file_tree.directory_missing"


@pytest.mark.parametrize("scope", ["project", "session", "standalone"])
def test_directory_download_preparation_permission_error(client, sources, scope, monkeypatch):
    _, _, nested, _ = sources
    original = directory_archives.os.scandir

    def denied(path):
        if str(path) == str(nested):
            raise PermissionError("Root access denied")
        return original(path)

    monkeypatch.setattr(directory_archives.os, "scandir", denied)
    response = _request(client, route(sources, scope, nested))
    assert response.status_code == 403
    assert response.json()["ui_message"]["key"] == "backend.file_content.permission_denied"


@pytest.mark.parametrize("scope", ["project", "session", "standalone"])
@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_directory_download_methods(client, sources, scope, method):
    _, _, nested, _ = sources
    response = _request(client, route(sources, scope, nested), method=method)
    assert response.status_code == 405
    assert set(response["Allow"].split(", ")) == {"GET", "HEAD"}
    assert (nested / "notes.txt").read_bytes() == b"directory content"
    absent = nested / "not-created"
    response = _request(client, route(sources, scope, absent), method=method)
    assert response.status_code == 405
    assert not absent.exists()


@pytest.mark.parametrize("scope", ["project", "session", "standalone"])
def test_directory_download_head(client, sources, scope, monkeypatch):
    _, _, nested, _ = sources
    readers = []
    original = directory_archives.DirectoryZipReader

    def track(directory):
        reader = original(directory)
        readers.append(reader)
        return reader

    def no_content(*args, **kwargs):
        raise AssertionError("HEAD traverses or compresses content")

    monkeypatch.setattr(views, "DirectoryZipReader", track)
    # Construction must remain lazy. Detect actual generator advancement instead.
    def lazy_chunks(*args):
        no_content()
        yield b""

    monkeypatch.setattr(directory_archives, "_archive_chunks", lazy_chunks)

    async def request_head():
        response = await client.head(route(sources, scope, nested))
        if response.streaming:
            response._download_body = b"".join(response.streaming_content)
            response.close()
            # AsyncClient strips HEAD streaming content. Await owned cleanup
            # before asyncio.run closes its event loop.
            await response._release()
        return response

    response = asyncio.run(request_head())
    assert response.status_code == 200
    assert _body(response) == b""
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == "attachment; filename*=utf-8''r%C3%A9sum%C3%A9.zip"
    assert "Content-Length" not in response
    assert readers and all(reader.closed for reader in readers)


def test_directory_download_not_http_gzipped(client, sources):
    _, _, nested, _ = sources
    response = _request(client, route(sources, "standalone", nested), headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert "Content-Encoding" not in response
    assert _body(response).startswith(b"PK\x03\x04")


def test_directory_download_never_creates_artifacts_root(client, tmp_path, monkeypatch):
    root = tmp_path / "absent-artifacts"

    def forbid_creation(*args):
        raise AssertionError("Downloads must not create artifacts directories")

    monkeypatch.setattr(views, "ensure_session_artifacts_root", forbid_creation)
    response = _request(client, "/api/directory-download/?" + urlencode({"path": root, "root": root}))
    assert response.status_code == 404
    assert response.json()["ui_message"]["key"] == "backend.file_tree.directory_missing"
    assert not root.exists()


def test_directory_download_preserves_trailing_space_in_root(client, tmp_path):
    root = tmp_path / "allowed "
    directory = root / "reports"
    directory.mkdir(parents=True)
    (directory / "notes.txt").write_bytes(b"report inside spaced root")
    url = "/api/directory-download/?" + urlencode({"path": directory, "root": root})

    response = _request(client, url)

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(_body(response))) as archive:
        assert archive.read("reports/notes.txt") == b"report inside spaced root"
