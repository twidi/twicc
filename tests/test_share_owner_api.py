import asyncio
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient
from django.utils import timezone as djtz

from twicc import paths
from twicc.core.models import (
    ArtifactBookmark,
    PinMode,
    Project,
    Session,
    SessionType,
    Share,
    ShareAccess,
)
from twicc.core.services.share_tokens import mint_token
from twicc.providers.helpers import get_provider_helpers


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def session(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-owner", directory="/tmp/owner")
    return Session.objects.create(
        id="sess-owner", project=project, provider="claude_code",
        file_path="sess-owner.jsonl", type=SessionType.SESSION, title="Owner",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=5,
        compute_version=get_provider_helpers("claude_code").current_compute_version,
    )


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.share_mutation.run_under_db_write_lock", _p)


@pytest.fixture
def share_host(monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings",
                        lambda: {"shareBaseUrl": "share.example.com"})


@pytest.fixture
def bookmark(session):
    return ArtifactBookmark.objects.create(
        session=session, project_id=session.project_id,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


def _write(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _run(coro):
    return asyncio.run(coro)


def _share(session, **kw):
    return Share.objects.create(kind="session", token=mint_token(), session=session, **kw)


def test_create_refused_without_share_host(client, session, monkeypatch):
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: {"shareBaseUrl": ""})
    body = {"kind": "session", "session_id": "sess-owner", "options": {"mode": "live"}}
    res = _run(client.post("/api/shares/", data=orjson.dumps(body), content_type="application/json"))
    assert res.status_code == 400
    assert orjson.loads(res.content)["error"] == "share_host_unset"


def test_create_refused_with_invalid_legacy_share_host(client, session, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"shareBaseUrl": "ftp://share.example.com"},
    )
    body = {"kind": "session", "session_id": "sess-owner", "options": {"mode": "live"}}
    res = _run(client.post("/api/shares/", data=orjson.dumps(body), content_type="application/json"))
    assert res.status_code == 400
    assert orjson.loads(res.content)["error"] == "share_host_unset"


def test_create_and_list(client, session, share_host):
    body = {"kind": "session", "session_id": "sess-owner", "label": "priv", "options": {"mode": "live"}}
    res = _run(client.post("/api/shares/", data=orjson.dumps(body), content_type="application/json"))
    assert res.status_code == 201
    created = orjson.loads(res.content)
    assert created["kind"] == "session"
    assert created["token"]
    assert created["url_path"].startswith("/share/")
    res = _run(client.get("/api/shares/"))
    assert len(orjson.loads(res.content)["shares"]) == 1


def test_patch_revoke_unrevoke_delete(client, session, share_host):
    share = _share(session, label="a")
    res = _run(client.patch(f"/api/shares/{share.id}/",
                            data=orjson.dumps({"label": "b"}), content_type="application/json"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["label"] == "b"
    res = _run(client.post(f"/api/shares/{share.id}/revoke/"))
    assert orjson.loads(res.content)["status"] == "revoked"
    res = _run(client.post(f"/api/shares/{share.id}/unrevoke/"))
    assert orjson.loads(res.content)["status"] == "active"
    res = _run(client.delete(f"/api/shares/{share.id}/"))
    assert res.status_code == 200
    assert not Share.objects.filter(id=share.id).exists()


def test_accesses_endpoint(client, session, share_host):
    share = _share(session)
    ShareAccess.objects.create(share=share, ip="1.2.3.4", user_agent="UA")
    res = _run(client.get(f"/api/shares/{share.id}/accesses/"))
    assert res.status_code == 200
    accesses = orjson.loads(res.content)["accesses"]
    assert len(accesses) == 1
    assert accesses[0]["ip"] == "1.2.3.4"


def test_unknown_share_404(client, session, share_host):
    assert _run(client.get("/api/shares/shr_nope/")).status_code == 404


@pytest.mark.parametrize("bad_expiry", ["junk", False, 0, [], {}])
def test_rest_create_invalid_expiry_rejected(
        bad_expiry, client, session, share_host):
    body = {
        "kind": "session", "session_id": session.id,
        "options": {"mode": "live"}, "expires_at": bad_expiry,
    }
    res = _run(client.post(
        "/api/shares/", data=orjson.dumps(body), content_type="application/json",
    ))
    assert res.status_code == 400
    errors = orjson.loads(res.content)["errors"]
    assert [(e["field"], e["code"]) for e in errors] == [("expires_at", "invalid")]
    assert Share.objects.count() == 0


def test_rest_artifact_create_preserves_title_options(
        client, session, bookmark, artifacts_root, share_host):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    body = {
        "kind": "artifact", "bookmark_id": bookmark.id,
        "options": {"show_title": False, "display_title": "Custom"},
    }
    res = _run(client.post(
        "/api/shares/", data=orjson.dumps(body), content_type="application/json",
    ))
    assert res.status_code == 201
    data = orjson.loads(res.content)
    assert data["options"]["show_title"] is False
    assert data["options"]["display_title"] == "Custom"
    assert "snapshot_at" in data["options"]


def test_rest_patch_invalid_expiry_keeps_existing_raise(client, session, share_host):
    """Accepted §7.2 limitation: REST PATCH parses in-view and still raises."""
    share = _share(session)
    with pytest.raises(ValueError):
        _run(client.patch(
            f"/api/shares/{share.id}/",
            data=orjson.dumps({"expires_at": "junk"}),
            content_type="application/json",
        ))


def test_list_serializes_visible_creator_without_async_lazy_load(
        client, session, share_host):
    now = djtz.now()
    creator = Session.objects.create(
        id="agent-owner", project=session.project, provider="claude_code",
        file_path="agent-owner.jsonl", type=SessionType.SESSION,
        title="Creator", created_at=now, last_new_content_at=now,
        user_message_count=1, last_line=4,
    )
    share = _share(session, created_by_session=creator)
    response = _run(client.get("/api/shares/"))
    assert response.status_code == 200
    row = next(
        item for item in orjson.loads(response.content)["shares"]
        if item["id"] == share.id
    )
    assert row["created_by"] == {
        "kind": "agent",
        "session": {
            "id": creator.id,
            "title": "Creator",
            "project_id": session.project_id,
        },
    }


def test_human_rest_patch_bypasses_agent_gate_for_options_and_password_clear(
        client, session, share_host, monkeypatch):
    from twicc.auth.hashers import hash_password

    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {
            "shareBaseUrl": "share.example.com",
            "allowAgentSessionShares": False,
            "allowAgentArtifactShares": False,
        },
    )
    share = _share(
        session,
        options={"mode": "live", "max_display_mode": "normal"},
        password_hash=hash_password("old-password"),
    )
    response = _run(client.patch(
        f"/api/shares/{share.id}/",
        data=orjson.dumps({
            "options": {"mode": "snapshot", "max_display_mode": "normal"},
            "password": "",
        }),
        content_type="application/json",
    ))
    assert response.status_code == 200
    share.refresh_from_db()
    assert share.options["mode"] == "snapshot"
    assert share.password_hash == ""


def test_human_rest_create_drops_injected_caller_and_bypasses_agent_shape(
        client, session, monkeypatch):
    """The owner POST builds an explicit eight-key payload. Request-body
    caller identity cannot turn this browser action into an agent call."""
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {
            "shareBaseUrl": "share.example.com",
            "allowAgentSessionShares": False,
            "allowAgentArtifactShares": False,
        },
    )
    response = _run(client.post(
        "/api/shares/",
        data=orjson.dumps({
            "kind": "session", "session_id": session.id,
            "caller_session_id": session.id,
            "notify_on_view": True,
            "options": {"mode": "live", "max_display_mode": "debug"},
        }),
        content_type="application/json",
    ))
    assert response.status_code == 201
    share = Share.objects.get(id=orjson.loads(response.content)["id"])
    assert share.notify_on_view is True
    assert share.options["max_display_mode"] == "debug"
    assert share.created_by_session_id is None
