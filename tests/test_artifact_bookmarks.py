import asyncio
from datetime import datetime
from pathlib import Path

import orjson
import pytest
from django.db import IntegrityError
from django.test import AsyncClient
from django.utils import timezone

from twicc import paths
from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType
from twicc.core.services import artifact_bookmark_mutation as abm


@pytest.fixture(autouse=True)
def _before_the_pagination_cutover(monkeypatch):
    """Pin the clock below ``PAGINATION_CUTOVER``.

    These tests assert the pre-cutover shape (a bare array, and the per-command
    default page size). Past the date both change, so without this they would go
    red on 2026-09-15 for a reason that has nothing to do with what they cover.
    The pinned value is naive, like the constant it replaces.
    """
    from twicc.cli import _output

    monkeypatch.setattr(_output, "PAGINATION_CUTOVER", datetime(2200, 1, 1))  # noqa: DTZ001


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-ab", directory="/tmp/ab")


@pytest.fixture
def session(project):
    now = timezone.now()
    return Session.objects.create(
        id="sess-ab", project=project, provider="claude_code",
        file_path="sess-ab.jsonl", type=SessionType.SESSION, title="sess-ab",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )


def test_create_bookmark(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )
    assert bm.id is not None
    assert bm.scope == "project"


def test_unique_per_session_and_path(session, project):
    ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope=PinMode.ALL,
    )
    with pytest.raises(IntegrityError):
        ArtifactBookmark.objects.create(
            session=session, project=project, relative_path="a.md", name="B", scope=PinMode.PROJECT,
        )


def test_serialize_bookmark(session, project):
    from twicc.core.serializers import serialize_artifact_bookmark
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.WORKSPACE,
    )
    d = serialize_artifact_bookmark(bm)
    assert d["name"] == "Demo"
    assert d["scope"] == "workspace"
    assert d["session_id"] == "sess-ab"
    assert d["project_id"] == "-tmp-ab"
    assert d["relative_path"] == "demo/index.html"
    assert d["file_ext"] == "html"
    assert d["root"].endswith("/sess-ab")
    assert d["created_at"] and d["updated_at"]


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path. get_session_artifacts_dir reads from
    get_data_dir, so patching that one function reroutes the whole tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer is only started at app boot. In tests, run the
    write factory transparently so the logic (ORM + serialize + broadcast) can
    be exercised without the writer lifecycle. The REST endpoints and the CLI
    drop-request handler both write through the shared mutation service, so the
    passthrough is installed on that module's symbol."""
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.artifact_bookmark_mutation.run_under_db_write_lock",
        _passthrough,
    )


def _run(coro):
    return asyncio.run(coro)


def _write_artifact(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def test_create_and_list_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html", b"<html></html>")
    body = {"session_id": "sess-ab", "relative_path": "demo/index.html",
            "name": "Demo", "scope": "all"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 201
    created = orjson.loads(res.content)
    assert created["name"] == "Demo"

    res = _run(client.get("/api/artifact-bookmarks/"))
    assert res.status_code == 200
    assert len(orjson.loads(res.content)["bookmarks"]) == 1


def test_create_rejects_missing_file(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "nope.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 404


def test_create_rejects_path_escape(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "../escape.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 400


def test_patch_and_delete_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="project")
    res = _run(client.patch(f"/api/artifact-bookmarks/{bm.id}/",
                            data=orjson.dumps({"name": "B", "scope": "all"}),
                            content_type="application/json"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["name"] == "B"
    res = _run(client.delete(f"/api/artifact-bookmarks/{bm.id}/"))
    assert res.status_code == 200
    assert not ArtifactBookmark.objects.filter(id=bm.id).exists()


def test_detail_reports_availability(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    present = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{present.id}/"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["available"] is True
    # A bookmark whose file is missing (created directly, bypassing POST validation):
    missing = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="gone.md", name="Gone", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{missing.id}/"))
    assert orjson.loads(res.content)["available"] is False


def test_method_not_allowed(client, session, project, artifacts_root):
    res = _run(client.delete("/api/artifact-bookmarks/"))
    assert res.status_code == 405
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="all")
    res = _run(client.put(f"/api/artifact-bookmarks/{bm.id}/",
                          data=orjson.dumps({}), content_type="application/json"))
    assert res.status_code == 405


# ── Shared mutation service (the CLI drop-request path) ──────────────────────


def test_upsert_payload_creates_with_default_scope(session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html", b"<html></html>")
    res = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "demo/index.html", "name": "Demo"}))
    assert res.success
    bm = ArtifactBookmark.objects.get(id=res.bookmark_id)
    assert bm.relative_path == "demo/index.html"
    assert bm.name == "Demo"
    assert bm.scope == "project"  # default on create


def test_upsert_payload_accepts_absolute_path(session, project, artifacts_root):
    p = _write_artifact(artifacts_root, "sess-ab", "out/report.md", b"# r")
    res = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": str(p), "name": "R", "scope": "all"}))
    assert res.success
    bm = ArtifactBookmark.objects.get(id=res.bookmark_id)
    assert bm.relative_path == "out/report.md"  # absolute resolved to the canonical key
    assert bm.scope == "all"


def test_upsert_payload_preserves_scope_on_update(session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# a")
    _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md", "name": "A", "scope": "all"}))
    # Re-upsert with a new name and NO scope → scope must stay "all".
    res = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md", "name": "A2"}))
    assert res.success
    bm = ArtifactBookmark.objects.get(id=res.bookmark_id)
    assert bm.name == "A2"
    assert bm.scope == "all"
    assert ArtifactBookmark.objects.count() == 1  # upsert, not a duplicate


def test_upsert_payload_rejections(session, project, artifacts_root):
    # path resolves under the dir but the file doesn't exist
    r = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "nope.md", "name": "X"}))
    assert not r.success and r.errors[0].code == "file_not_found"
    # path escapes the artifacts dir
    r = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "../escape.md", "name": "X"}))
    assert not r.success and r.errors[0].code == "path_escapes"
    # invalid scope (caught before any filesystem work)
    r = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md", "name": "X", "scope": "bogus"}))
    assert not r.success and r.errors[0].code == "invalid_scope"
    # unknown session
    r = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "ghost", "path": "a.md", "name": "X"}))
    assert not r.success and r.errors[0].code == "session_not_found"


def test_delete_payload_round_trip(session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# a")
    up = _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md", "name": "A"}))
    res = _run(abm.delete_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md"}))
    assert res.success and res.bookmark_id == up.bookmark_id
    assert ArtifactBookmark.objects.count() == 0
    # deleting again → not_bookmarked (file still on disk, but no row)
    res = _run(abm.delete_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md"}))
    assert not res.success and res.errors[0].code == "not_bookmarked"


def test_cli_artifacts_listing_and_scope_filter(session, project, artifacts_root, capfd):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# a")
    _write_artifact(artifacts_root, "sess-ab", "b.md", b"# b")
    _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "a.md", "name": "A", "scope": "all"}))
    _run(abm.upsert_artifact_bookmark_from_payload(
        {"session_id": "sess-ab", "path": "b.md", "name": "B", "scope": "project"}))

    from twicc.cli import artifacts as cli_artifacts

    cli_artifacts.main()
    out = orjson.loads(capfd.readouterr().out)
    assert {b["name"] for b in out} == {"A", "B"}

    cli_artifacts.main(scope="all")
    out = orjson.loads(capfd.readouterr().out)
    assert [b["name"] for b in out] == ["A"]


# ── Network-broker allowlist (allowed_hosts) — phase 2 ─────────────────────────


def test_new_bookmark_has_empty_allowed_hosts(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="y.html", name="Y", scope=PinMode.PROJECT,
    )
    assert bm.allowed_hosts == {}


def test_serialize_includes_allowed_hosts(session, project):
    from twicc.core.serializers import serialize_artifact_bookmark

    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="x.html", name="X", scope=PinMode.PROJECT,
        allowed_hosts={"https://api.example.com:443": {"kind": "public"}},
    )
    assert serialize_artifact_bookmark(bm)["allowed_hosts"] == {
        "https://api.example.com:443": {"kind": "public"}
    }


def test_add_allowed_host_normalizes_and_stores_kind(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope=PinMode.PROJECT,
    )
    key = _run(abm.add_artifact_allowed_host(bookmark=bm, url="https://API.example.com/data?q=1", kind="public"))
    assert key == "https://api.example.com:443"
    bm.refresh_from_db()
    assert bm.allowed_hosts == {"https://api.example.com:443": {"kind": "public"}}


def test_add_allowed_host_is_port_specific_and_idempotent(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="b.html", name="B", scope=PinMode.PROJECT,
    )
    _run(abm.add_artifact_allowed_host(bookmark=bm, url="http://localhost:9000/a", kind="loopback"))
    _run(abm.add_artifact_allowed_host(bookmark=bm, url="http://localhost:9000/b", kind="loopback"))  # same host:port
    _run(abm.add_artifact_allowed_host(bookmark=bm, url="http://localhost:5432/", kind="loopback"))   # different port
    bm.refresh_from_db()
    assert set(bm.allowed_hosts) == {"http://localhost:9000", "http://localhost:5432"}


def test_remove_allowed_host(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="c.html", name="C", scope=PinMode.PROJECT,
        allowed_hosts={"https://api.x:443": {"kind": "public"}},
    )
    assert _run(abm.remove_artifact_allowed_host(bookmark=bm, url="https://api.x/")) is True
    bm.refresh_from_db()
    assert bm.allowed_hosts == {}


def test_remove_allowed_host_absent_returns_false(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="d.html", name="D", scope=PinMode.PROJECT,
    )
    assert _run(abm.remove_artifact_allowed_host(bookmark=bm, url="https://nope.x/")) is False


def test_allow_host_endpoint_adds_entry(client, session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="e.html", name="E", scope=PinMode.PROJECT,
    )
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bm.id}/allowed-hosts/",
        data=orjson.dumps({"url": "https://api.example.com/data", "kind": "public"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content)["allowed_hosts"] == {"https://api.example.com:443": {"kind": "public"}}
    bm.refresh_from_db()
    assert bm.allowed_hosts == {"https://api.example.com:443": {"kind": "public"}}


def test_allow_host_endpoint_rejects_metadata_kind(client, session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="f.html", name="F", scope=PinMode.PROJECT,
    )
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bm.id}/allowed-hosts/",
        data=orjson.dumps({"url": "https://x/", "kind": "metadata"}),
        content_type="application/json",
    ))
    assert res.status_code == 400
    bm.refresh_from_db()
    assert bm.allowed_hosts == {}


def test_allow_host_endpoint_rejects_bad_scheme(client, session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="g.html", name="G", scope=PinMode.PROJECT,
    )
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bm.id}/allowed-hosts/",
        data=orjson.dumps({"url": "ftp://x/", "kind": "public"}),
        content_type="application/json",
    ))
    assert res.status_code == 400


def test_unallow_host_endpoint_removes_entry(client, session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="h.html", name="H", scope=PinMode.PROJECT,
        allowed_hosts={"https://api.x:443": {"kind": "public"}},
    )
    res = _run(client.delete(
        f"/api/artifact-bookmarks/{bm.id}/allowed-hosts/",
        data=orjson.dumps({"url": "https://api.x/"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    bm.refresh_from_db()
    assert bm.allowed_hosts == {}


# ── Broker HTML wrapping on the dedicated page (artifact_serve) — phase 3 ──────


def test_artifact_serve_root_returns_trusted_shell(client, session, project, artifacts_root):
    # The dedicated page (root) now serves the trusted *shell* (which iframes the
    # artifact + mounts the broker), so it behaves like the in-SPA preview. The
    # shell is NOT the artifact: no artifact CSP, no shim injected here.
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html",
                    b"<html><head></head><body>hi</body></html>")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="demo/index.html",
        name="Demo", scope=PinMode.PROJECT,
        allowed_hosts={"https://api.example.com:443": {"kind": "public"}},
    )
    res = _run(client.get(f"/artifacts/{bm.id}/"))
    assert res.status_code == 200
    assert not res.has_header("Content-Security-Policy")
    assert b"artifact-broker-shim" not in res.content
    assert b"/_twicc/artifact-shell/shell.js" in res.content   # the shell bundle
    assert f"/artifacts/{bm.id}/__twicc_doc__".encode() in res.content  # the inner doc
    assert b"api.example.com" in res.content                   # allowlist seeded in


def test_artifact_serve_inner_doc_wraps_the_artifact(client, session, project, artifacts_root):
    # The inner-doc sentinel serves the artifact document itself, wrapped (shim +
    # strict CSP) — this is what the shell's iframe loads.
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html",
                    b"<html><head></head><body>hi</body></html>")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="demo/index.html",
        name="Demo", scope=PinMode.PROJECT,
    )
    res = _run(client.get(f"/artifacts/{bm.id}/__twicc_doc__"))
    assert res.status_code == 200
    assert "connect-src 'none'" in res["Content-Security-Policy"]
    assert b"artifact-broker-shim" in res.content


def test_artifact_serve_non_html_root_served_directly(client, session, project, artifacts_root):
    # A non-HTML artifact has no broker need → served directly (unchanged), not
    # wrapped in the shell.
    _write_artifact(artifacts_root, "sess-ab", "report.md", b"# title")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="report.md",
        name="Report", scope=PinMode.PROJECT,
    )
    res = _run(client.get(f"/artifacts/{bm.id}/"))
    assert res.status_code == 200
    assert not res.has_header("Content-Security-Policy")
    # Served directly as a raw FileResponse (streaming), not the shell.
    body = b"".join(res.streaming_content)
    assert b"/_twicc/artifact-shell/" not in body   # not the shell
    assert b"# title" in body                        # the file's own bytes


def test_artifact_serve_asset_is_served_raw(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html", b"<html></html>")
    _write_artifact(artifacts_root, "sess-ab", "demo/app.js", b"console.log(1)")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="demo/index.html",
        name="Demo", scope=PinMode.PROJECT,
    )
    res = _run(client.get(f"/artifacts/{bm.id}/app.js"))
    assert res.status_code == 200
    # A sub-asset is never wrapped: no broker CSP header.
    assert not res.has_header("Content-Security-Policy")
