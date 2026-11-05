import asyncio
from datetime import timedelta

import orjson
import pytest
from django.test import AsyncClient, RequestFactory
from django.utils import timezone

from twicc.artifacts import proxy as proxy_module
from twicc.artifacts.proxy import ProxyResult, ResolvedTarget
from twicc.core.models import ArtifactBookmark, ArtifactNetworkDenial, Project, Session, SessionType, Share
from twicc.core.serializers import serialize_artifact_bookmark, serialize_network_denial
from twicc.core.services import artifact_bookmark_mutation as abm
from twicc.core.services.share_tokens import mint_token


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
    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-and", directory="/tmp/and")


@pytest.fixture
def session(project):
    now = timezone.now()
    return Session.objects.create(
        id="sess-and", project=project, provider="claude_code",
        file_path="sess-and.jsonl", type=SessionType.SESSION, title="sess-and",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )


@pytest.fixture
def share(session):
    return Share.objects.create(kind="session", token=mint_token(), session=session)


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")


@pytest.fixture(autouse=True)
def _clear_pending_denials():
    """`denial_tracking._pending` is process-global module state; scrub it
    before and after each test so entries never leak across tests."""
    from twicc.artifacts import denial_tracking
    denial_tracking._pending.clear()
    yield
    denial_tracking._pending.clear()


def test_new_bookmark_has_empty_denied_hosts(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    assert bm.denied_hosts == {}


def test_denial_row_defaults_and_cascade(session, project, share):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=share, host_key="https://api.x:443", kind="public", ip="1.2.3.4", user_agent="UA")
    assert d.count == 1 and d.first_at and d.last_at
    share.delete()
    assert not ArtifactNetworkDenial.objects.filter(id=d.id).exists()


def test_bookmark_cascade(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="https://api.x:443", kind="public")
    bm.delete()
    assert not ArtifactNetworkDenial.objects.exists()


def test_owner_denial_has_null_share(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="http://localhost:9000", kind="loopback")
    assert d.share is None and d.ip == "" and d.user_agent == ""


def test_serializers(session, project, share):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope="project",
        denied_hosts={"http://localhost:9000": {"kind": "loopback"}},
    )
    assert serialize_artifact_bookmark(bm)["denied_hosts"] == {"http://localhost:9000": {"kind": "loopback"}}
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=share, host_key="https://api.x:443", kind="public", ip="1.2.3.4", user_agent="UA")
    d = ArtifactNetworkDenial.objects.select_related("share").get(id=d.id)
    s = serialize_network_denial(d)
    assert s["host_key"] == "https://api.x:443" and s["kind"] == "public" and s["count"] == 1
    assert s["share"] == {"id": share.id, "label": share.label or "", "status": share.status()}
    assert s["ip"] == "1.2.3.4" and s["user_agent"] == "UA" and s["first_at"] and s["last_at"]
    d2 = ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="https://api.x:443", kind="public")
    assert serialize_network_denial(d2)["share"] is None


# ── Services: deny / un-deny / allow purge / denials broadcast ─────────────────


def test_add_denied_host_normalizes_and_stores_kind(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    key = _run(abm.add_artifact_denied_host(bookmark=bm, url="http://LOCALHOST:9000/x", kind="loopback"))
    assert key == "http://localhost:9000"
    bm.refresh_from_db()
    assert bm.denied_hosts == {"http://localhost:9000": {"kind": "loopback"}}


def test_deny_host_removes_it_from_allowed_hosts_but_keeps_denial_rows(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope="project",
        allowed_hosts={"http://localhost:9000": {"kind": "loopback"}},
    )
    ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="http://localhost:9000", kind="loopback")

    _run(abm.add_artifact_denied_host(bookmark=bm, url="http://localhost:9000/x", kind="loopback"))

    bm.refresh_from_db()
    assert "http://localhost:9000" not in bm.allowed_hosts
    assert bm.denied_hosts == {"http://localhost:9000": {"kind": "loopback"}}
    assert ArtifactNetworkDenial.objects.filter(bookmark=bm, host_key="http://localhost:9000").exists()


def test_remove_denied_host(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope="project",
        denied_hosts={"http://localhost:9000": {"kind": "loopback"}},
    )
    assert _run(abm.remove_artifact_denied_host(bookmark=bm, url="http://localhost:9000/x")) is True
    bm.refresh_from_db()
    assert bm.denied_hosts == {}


def test_remove_denied_host_absent_returns_false(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    assert _run(abm.remove_artifact_denied_host(bookmark=bm, url="http://localhost:9000/x")) is False


@pytest.mark.parametrize("kind", ["metadata", "weird"])
def test_add_denied_host_rejects_invalid_kind(session, project, kind):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    with pytest.raises(ValueError):
        _run(abm.add_artifact_denied_host(bookmark=bm, url="http://localhost:9000/x", kind=kind))
    bm.refresh_from_db()
    assert bm.denied_hosts == {}


def test_allow_host_purges_denied_entry_and_its_denial_rows(session, project, share):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope="project",
        denied_hosts={"https://api.x:443": {"kind": "public"}},
    )
    row_with_share = ArtifactNetworkDenial.objects.create(
        bookmark=bm, share=share, host_key="https://api.x:443", kind="public",
    )
    row_without_share = ArtifactNetworkDenial.objects.create(
        bookmark=bm, share=None, host_key="https://api.x:443", kind="public",
    )
    other_host_row = ArtifactNetworkDenial.objects.create(
        bookmark=bm, share=None, host_key="https://other.x:443", kind="public",
    )

    _run(abm.add_artifact_allowed_host(bookmark=bm, url="https://api.x/", kind="public"))

    bm.refresh_from_db()
    assert bm.allowed_hosts == {"https://api.x:443": {"kind": "public"}}
    assert "https://api.x:443" not in bm.denied_hosts
    assert not ArtifactNetworkDenial.objects.filter(id=row_with_share.id).exists()
    assert not ArtifactNetworkDenial.objects.filter(id=row_without_share.id).exists()
    assert ArtifactNetworkDenial.objects.filter(id=other_host_row.id).exists()


# ── denial_tracking: in-memory coalescing, flush persist, owner-denial path ────


def test_note_denial_coalesces_same_key(bookmark, share):
    from twicc.artifacts import denial_tracking

    denial_tracking.note_denial(
        bookmark_id=bookmark.id, share_id=share.id, host_key="https://api.x:443",
        kind="public", ip="1.2.3.4", user_agent="UA",
    )
    denial_tracking.note_denial(
        bookmark_id=bookmark.id, share_id=share.id, host_key="https://api.x:443",
        kind="public", ip="1.2.3.4", user_agent="UA",
    )
    denial_tracking.note_denial(
        bookmark_id=bookmark.id, share_id=share.id, host_key="https://api.x:443",
        kind="public", ip="1.2.3.4", user_agent="UA",
    )

    assert len(denial_tracking._pending) == 1
    key = (bookmark.id, share.id, "https://api.x:443", "1.2.3.4", "UA")
    assert denial_tracking._pending[key] == {"kind": "public", "count": 3}


def test_note_denial_different_ip_is_a_separate_entry(bookmark, share):
    from twicc.artifacts import denial_tracking

    denial_tracking.note_denial(
        bookmark_id=bookmark.id, share_id=share.id, host_key="https://api.x:443",
        kind="public", ip="1.2.3.4", user_agent="UA",
    )
    denial_tracking.note_denial(
        bookmark_id=bookmark.id, share_id=share.id, host_key="https://api.x:443",
        kind="public", ip="5.6.7.8", user_agent="UA",
    )

    assert len(denial_tracking._pending) == 2


def test_persist_creates_then_increments_and_refreshes_kind(bookmark):
    from twicc.artifacts import denial_tracking

    key = (bookmark.id, None, "https://api.x:443", "", "")
    updated = denial_tracking._persist({key: {"kind": "public", "count": 3}})
    assert updated == {bookmark.id}
    row = ArtifactNetworkDenial.objects.get(bookmark=bookmark, host_key="https://api.x:443")
    assert row.count == 3 and row.kind == "public"

    updated = denial_tracking._persist({key: {"kind": "loopback", "count": 2}})
    assert updated == {bookmark.id}
    row.refresh_from_db()
    assert row.count == 5 and row.kind == "loopback"
    assert ArtifactNetworkDenial.objects.filter(bookmark=bookmark).count() == 1


def test_persist_drops_allowed_host_and_unknown_bookmark(bookmark):
    from twicc.artifacts import denial_tracking

    bookmark.allowed_hosts = {"https://api.x:443": {"kind": "public"}}
    bookmark.save(update_fields=["allowed_hosts"])

    snapshot = {
        (bookmark.id, None, "https://api.x:443", "", ""): {"kind": "public", "count": 1},
        (999999, None, "https://other.x:443", "", ""): {"kind": "public", "count": 1},
    }
    updated = denial_tracking._persist(snapshot)
    assert updated == set()
    assert not ArtifactNetworkDenial.objects.exists()


def test_persist_prunes_to_max_rows_per_bookmark(bookmark, monkeypatch):
    from twicc.artifacts import denial_tracking

    monkeypatch.setattr(denial_tracking, "_MAX_DENIAL_ROWS", 3)
    snapshot = {
        (bookmark.id, None, "https://api.x:443", f"1.2.3.{i}", ""): {"kind": "public", "count": 1}
        for i in range(6)
    }
    denial_tracking._persist(snapshot)
    assert ArtifactNetworkDenial.objects.filter(bookmark=bookmark).count() == 3


def test_record_owner_denial_upserts_share_none_row(bookmark):
    from twicc.artifacts import denial_tracking

    _run(denial_tracking.record_owner_denial(bookmark=bookmark, url="http://localhost:9000/x", kind="loopback"))

    row = ArtifactNetworkDenial.objects.get(bookmark=bookmark, host_key="http://localhost:9000")
    assert row.share is None and row.kind == "loopback" and row.count == 1 and row.ip == "" and row.user_agent == ""


def test_record_owner_denial_skips_when_host_is_allowed(bookmark):
    from twicc.artifacts import denial_tracking

    bookmark.allowed_hosts = {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.save(update_fields=["allowed_hosts"])

    _run(denial_tracking.record_owner_denial(bookmark=bookmark, url="http://localhost:9000/x", kind="loopback"))

    assert not ArtifactNetworkDenial.objects.exists()


def test_record_owner_denial_rejects_bad_kind(bookmark):
    from twicc.artifacts import denial_tracking

    with pytest.raises(ValueError):
        _run(denial_tracking.record_owner_denial(bookmark=bookmark, url="http://localhost:9000/x", kind="weird"))
    assert not ArtifactNetworkDenial.objects.exists()


def test_record_owner_denial_rejects_bad_scheme(bookmark):
    from twicc.artifacts import denial_tracking

    with pytest.raises(ValueError):
        _run(denial_tracking.record_owner_denial(bookmark=bookmark, url="ftp://localhost:9000/x", kind="loopback"))
    assert not ArtifactNetworkDenial.objects.exists()


def test_flush_task_requeues_pending_entries_on_persist_failure(bookmark, monkeypatch):
    from twicc.artifacts import denial_tracking

    denial_tracking.note_denial(bookmark_id=bookmark.id, share_id=None, host_key="https://api.x:443", kind="public")
    denial_tracking.note_denial(bookmark_id=bookmark.id, share_id=None, host_key="https://api.x:443", kind="public")

    stop_event = asyncio.Event()

    def _boom(snapshot):
        stop_event.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(denial_tracking, "_persist", _boom)
    monkeypatch.setattr(denial_tracking, "_FLUSH_INTERVAL", 0.01)

    _run(denial_tracking.start_denial_flush_task(stop_event))

    key = (bookmark.id, None, "https://api.x:443", "", "")
    assert denial_tracking._pending[key]["count"] == 2


# ── Proxy hook: on_not_allowed callback (Task 4) ────────────────────────────


def _proxy_req(body: dict):
    return RequestFactory().post(
        "/share/tkn/api/proxy/", data=orjson.dumps(body), content_type="application/json"
    )


def test_proxy_calls_on_not_allowed_when_blocked(monkeypatch):
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    calls = []
    req = _proxy_req({"mode": "fetch", "request": {"url": "https://evil.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(
        req, enforced_allowlist={"https://api.example.com:443"},
        on_not_allowed=lambda key, kind: calls.append((key, kind)),
    ))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "not_allowed"}
    assert calls == [("https://evil.example.com:443", "public")]


def test_proxy_does_not_call_on_not_allowed_when_host_is_allowed(monkeypatch):
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    async def fake_fetch(*, method, url, headers, body, pinned_ip, client, **kw):
        return ProxyResult(status=200, reason="OK", headers={}, body=b"ok")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    monkeypatch.setattr(proxy_module, "proxy_fetch", fake_fetch)
    calls = []
    req = _proxy_req({"mode": "fetch", "request": {"url": "https://api.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(
        req, enforced_allowlist={"https://api.example.com:443"},
        on_not_allowed=lambda key, kind: calls.append((key, kind)),
    ))
    assert res.status_code == 200
    assert calls == []


def test_proxy_does_not_call_on_not_allowed_for_metadata_target(monkeypatch):
    """The metadata hard-block fires before the allowlist check, so a metadata
    target never reaches (and never triggers) the denial callback."""
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="169.254.169.254", kind="metadata")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    calls = []
    req = _proxy_req({"mode": "fetch", "request": {"url": "https://api.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(
        req, enforced_allowlist={"https://api.example.com:443"},
        on_not_allowed=lambda key, kind: calls.append((key, kind)),
    ))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "metadata"}
    assert calls == []


def test_proxy_on_not_allowed_defaults_to_none_unchanged_behavior(monkeypatch):
    """Omitting on_not_allowed (the owner-proxy call shape, pre-Task-4 default)
    must behave exactly as before: still refused, no crash from a missing callback."""
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    req = _proxy_req({"mode": "fetch", "request": {"url": "https://evil.example.com/", "method": "GET"}})
    res = _run(proxy_module.artifact_proxy(req, enforced_allowlist={"https://api.example.com:443"}))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "not_allowed"}


# ── Share wrapper: end-to-end denial recording (Task 4) ─────────────────────


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def test_share_artifact_proxy_records_pending_denial(client, bookmark, monkeypatch):
    from twicc.artifacts import denial_tracking

    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)

    art_share = Share.objects.create(kind="artifact", token=mint_token(), artifact_bookmark=bookmark)
    body = {"mode": "fetch", "request": {"url": "https://evil.example.com/", "method": "GET"}}
    res = _run(client.post(
        f"/share/{art_share.token}/api/proxy/",
        data=orjson.dumps(body), content_type="application/json",
    ))
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "not_allowed"}

    key = (bookmark.id, art_share.id, "https://evil.example.com:443", "127.0.0.1", "")
    assert denial_tracking._pending[key] == {"kind": "public", "count": 1}


# ── REST endpoints: /network-denials/ and /denied-hosts/ (Task 4) ──────────


def test_network_denials_get_returns_serialized_rows_ordered_by_last_at(client, bookmark, share):
    older = ArtifactNetworkDenial.objects.create(
        bookmark=bookmark, share=share, host_key="https://a.x:443", kind="public",
    )
    ArtifactNetworkDenial.objects.create(
        bookmark=bookmark, share=None, host_key="https://b.x:443", kind="loopback",
    )
    ArtifactNetworkDenial.objects.filter(id=older.id).update(last_at=timezone.now() - timedelta(minutes=5))

    res = _run(client.get(f"/api/artifact-bookmarks/{bookmark.id}/network-denials/"))
    assert res.status_code == 200
    data = orjson.loads(res.content)["denials"]
    assert [d["host_key"] for d in data] == ["https://b.x:443", "https://a.x:443"]
    assert data[0]["share"] is None
    assert data[1]["share"] == {"id": share.id, "label": share.label or "", "status": share.status()}


def test_network_denials_post_creates_owner_row(client, bookmark):
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/network-denials/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "loopback"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content) == {"ok": True}
    row = ArtifactNetworkDenial.objects.get(bookmark=bookmark, host_key="http://localhost:9000")
    assert row.share is None and row.kind == "loopback"


def test_network_denials_post_rejects_bad_kind(client, bookmark):
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/network-denials/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "weird"}),
        content_type="application/json",
    ))
    assert res.status_code == 400
    assert not ArtifactNetworkDenial.objects.exists()


def test_network_denials_post_allowed_host_creates_no_row(client, bookmark):
    bookmark.allowed_hosts = {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.save(update_fields=["allowed_hosts"])
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/network-denials/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "loopback"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert not ArtifactNetworkDenial.objects.exists()


def test_network_denials_method_not_allowed_for_put(client, bookmark):
    res = _run(client.put(
        f"/api/artifact-bookmarks/{bookmark.id}/network-denials/",
        data=orjson.dumps({}), content_type="application/json",
    ))
    assert res.status_code == 405


def test_denied_hosts_post_adds_entry(client, bookmark):
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "loopback"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content)["denied_hosts"] == {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.refresh_from_db()
    assert bookmark.denied_hosts == {"http://localhost:9000": {"kind": "loopback"}}


def test_denied_hosts_post_with_stored_host_key_as_url(client, bookmark):
    _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "http://localhost:9000/some/path", "kind": "loopback"}),
        content_type="application/json",
    ))
    bookmark.refresh_from_db()
    stored_key = next(iter(bookmark.denied_hosts))
    assert stored_key == "http://localhost:9000"

    # Passing the stored key itself back as ``url`` must work (idempotent normalize).
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": stored_key, "kind": "loopback"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content)["denied_hosts"] == {stored_key: {"kind": "loopback"}}


def test_denied_hosts_delete_removes_entry(client, bookmark):
    bookmark.denied_hosts = {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.save(update_fields=["denied_hosts"])
    res = _run(client.delete(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "http://localhost:9000"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content)["denied_hosts"] == {}
    bookmark.refresh_from_db()
    assert bookmark.denied_hosts == {}


def test_denied_hosts_delete_absent_key_returns_200_unchanged(client, bookmark):
    bookmark.denied_hosts = {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.save(update_fields=["denied_hosts"])
    res = _run(client.delete(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "http://other.x:443"}),
        content_type="application/json",
    ))
    assert res.status_code == 200
    assert orjson.loads(res.content)["denied_hosts"] == {"http://localhost:9000": {"kind": "loopback"}}
    bookmark.refresh_from_db()
    assert bookmark.denied_hosts == {"http://localhost:9000": {"kind": "loopback"}}


def test_denied_hosts_post_rejects_bad_kind(client, bookmark):
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "weird"}),
        content_type="application/json",
    ))
    assert res.status_code == 400
    bookmark.refresh_from_db()
    assert bookmark.denied_hosts == {}


def test_denied_hosts_post_rejects_bad_scheme(client, bookmark):
    res = _run(client.post(
        f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/",
        data=orjson.dumps({"url": "ftp://x/", "kind": "public"}),
        content_type="application/json",
    ))
    assert res.status_code == 400


def test_denied_hosts_unknown_bookmark_404(client, transactional_db):
    res = _run(client.post(
        "/api/artifact-bookmarks/999999/denied-hosts/",
        data=orjson.dumps({"url": "http://localhost:9000", "kind": "loopback"}),
        content_type="application/json",
    ))
    assert res.status_code == 404


def test_denied_hosts_get_method_not_allowed(client, bookmark):
    res = _run(client.get(f"/api/artifact-bookmarks/{bookmark.id}/denied-hosts/"))
    assert res.status_code == 405
