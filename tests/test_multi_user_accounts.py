"""Multi-user accounts: identities, project access, the HTTP and WS gates.

Design: docs/plans/2026-11-18-multi-user-design.md.
"""

import asyncio
import time
from importlib import import_module

import orjson
import pytest
from django.conf import settings as dj_settings
from django.test import AsyncClient

from twicc.accounts import google, service
from twicc.accounts.principal import (
    OWNER,
    SESSION_USER_EPOCH_KEY,
    SESSION_USER_ID_KEY,
    invalidate_accounts_enabled,
    principal_for_user,
)
from twicc.accounts.project_access import (
    filter_workspaces_payload,
    get_project_access_sync,
    invalidate_access_cache,
    resolve_user_access_sync,
)
from twicc.accounts.ws_filter import MemberFrameFilter
from twicc.core.models import Project, Session

WORKSPACES = {
    "workspaces": [
        {"id": "ws-a", "name": "Client A", "projectIds": ["-proj-a1", "-proj-a2"], "autoProjectPatterns": ["/x/*"]},
        {"id": "ws-b", "name": "Client B", "projectIds": ["-proj-b1"], "autoProjectPatterns": []},
    ]
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr("twicc.workspaces.read_workspaces", lambda: orjson.loads(orjson.dumps(WORKSPACES)))
    # Hashing at 600k PBKDF2 iterations per account would make this module slow.
    monkeypatch.setattr("twicc.accounts.service.hash_password", lambda pw: f"plain${pw}")
    monkeypatch.setattr(
        "twicc.auth.views.verify_password",
        lambda pw, stored: stored == f"plain${pw}" or stored == "plain$twicc-timing-equalizer",
    )
    monkeypatch.setattr("twicc.accounts.notify.schedule_accounts_changed", lambda: None)

    async def _passthrough(coro_factory, **kwargs):
        return await coro_factory()

    # The global DB writer only runs at app boot: run lock-guarded writes directly.
    monkeypatch.setattr("twicc.accounts.views.run_under_db_write_lock", _passthrough)
    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", _passthrough)
    invalidate_access_cache()
    invalidate_accounts_enabled()
    yield
    invalidate_access_cache()
    invalidate_accounts_enabled()


@pytest.fixture
def projects(transactional_db):
    for pid in ("-proj-a1", "-proj-a2", "-proj-b1", "-proj-solo"):
        Project.objects.create(id=pid, directory=f"/tmp/{pid}")
    Project.objects.create(id="-proj-a1-wt", directory="/tmp/wt", worktree_of_id="-proj-a1")
    return Project.objects.all()


def _user(email="member@example.com", role="member", password="secret-pass"):
    result = service.create_user(email=email, role=role, password=password)
    assert result.success, result.errors
    return result.user


def _login_as(client, user):
    engine = import_module(dj_settings.SESSION_ENGINE)
    store = engine.SessionStore()
    store[SESSION_USER_ID_KEY] = user.id
    store[SESSION_USER_EPOCH_KEY] = user.auth_epoch
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key


# --- access resolution ---------------------------------------------------------


def test_project_grant_covers_project_and_its_worktrees(projects):
    user = _user()
    service.set_project_grant(user.id, "-proj-a1", "view")
    access = resolve_user_access_sync(user.id)
    assert access.viewable == {"-proj-a1", "-proj-a1-wt"}
    assert access.operable == frozenset()


def test_workspace_grant_covers_its_projects_only(projects):
    user = _user()
    service.set_workspace_grant(user.id, "ws-a", "operate")
    access = resolve_user_access_sync(user.id)
    assert access.can_operate("-proj-a1") and access.can_operate("-proj-a2")
    assert access.can_operate("-proj-a1-wt")
    assert not access.can_view("-proj-b1")
    assert not access.can_view("-proj-solo")


def test_strongest_level_wins(projects):
    user = _user()
    service.set_workspace_grant(user.id, "ws-a", "view")
    service.set_project_grant(user.id, "-proj-a2", "operate")
    access = resolve_user_access_sync(user.id)
    assert access.can_view("-proj-a1") and not access.can_operate("-proj-a1")
    assert access.can_operate("-proj-a2")


def test_admins_and_owner_are_unrestricted(projects):
    admin = _user("admin@example.com", role="admin")
    assert get_project_access_sync(principal_for_user(admin)).unrestricted
    assert get_project_access_sync(OWNER).unrestricted


def test_workspace_payload_is_stripped_for_members(projects):
    user = _user()
    service.set_project_grant(user.id, "-proj-a2", "view")
    access = resolve_user_access_sync(user.id)
    out = filter_workspaces_payload(WORKSPACES["workspaces"], access, {"ws-a"})
    assert [ws["id"] for ws in out] == ["ws-a"]
    assert out[0]["projectIds"] == ["-proj-a2"]
    assert out[0]["autoProjectPatterns"] == []


def test_replace_grants_rejects_unknown_targets(projects):
    user = _user()
    bad = service.replace_grants(user.id, project_grants=[{"project_id": "-nope", "level": "view"}], workspace_grants=[])
    assert not bad.success and bad.errors[0].code == "project_not_found"
    ok = service.replace_grants(
        user.id,
        project_grants=[{"project_id": "-proj-solo", "level": "operate"}],
        workspace_grants=[{"workspace_id": "ws-b", "level": "view"}],
    )
    assert ok.success
    access = resolve_user_access_sync(user.id)
    assert access.viewable == {"-proj-solo", "-proj-b1"}


# --- account service -------------------------------------------------------------


def test_duplicate_email_is_case_insensitive(transactional_db):
    _user("Someone@Example.com")
    result = service.create_user(email="someone@example.COM")
    assert not result.success and result.errors[0].code == "duplicate_email"


def test_last_admin_cannot_be_demoted_without_instance_password(transactional_db, settings):
    settings.TWICC_PASSWORD_HASH = ""
    admin = _user("admin@example.com", role="admin")
    result = service.update_user(admin.id, role="member")
    assert not result.success and result.errors[0].code == "last_admin"
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$x$y$z"
    assert service.update_user(admin.id, role="member").success


def test_authority_changes_bump_the_epoch(transactional_db):
    user = _user()
    epoch = user.auth_epoch
    user = service.update_user(user.id, display_name="New name").user
    assert user.auth_epoch == epoch  # cosmetic change keeps sessions
    user = service.update_user(user.id, password="another-pass").user
    assert user.auth_epoch == epoch + 1
    user = service.revoke_sessions(user.id).user
    assert user.auth_epoch == epoch + 2


# --- Google claims ---------------------------------------------------------------

CONFIG = google.GoogleConfig(
    client_id="cid.apps.googleusercontent.com", client_secret="s", redirect_uri="", allowed_domains=frozenset()
)


def _claims(**over):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CONFIG.client_id,
        "exp": time.time() + 600,
        "nonce": "n0nce",
        "email": "Person@Example.com",
        "email_verified": True,
        "sub": "1234",
    }
    claims.update(over)
    return claims


def test_google_claims_valid():
    identity = google.validate_claims(_claims(), config=CONFIG, nonce="n0nce")
    assert identity.email == "person@example.com" and identity.sub == "1234"


@pytest.mark.parametrize(
    "override,code",
    [
        ({"iss": "https://evil.example"}, "invalid_token"),
        ({"aud": "other-client"}, "invalid_token"),
        ({"exp": time.time() - 3600}, "invalid_token"),
        ({"nonce": "replayed"}, "invalid_token"),
        ({"email_verified": False}, "email_not_verified"),
    ],
)
def test_google_claims_refused(override, code):
    with pytest.raises(google.GoogleError) as exc:
        google.validate_claims(_claims(**override), config=CONFIG, nonce="n0nce")
    assert exc.value.code == code


def test_google_matches_existing_account_and_latches_sub(transactional_db):
    user = _user("person@example.com")
    identity = google.GoogleIdentity(sub="1234", email="person@example.com", name="P", picture="", hosted_domain="")
    assert google.match_or_create_account(identity, CONFIG).id == user.id
    user.refresh_from_db()
    assert user.google_sub == "1234"
    other = identity._replace(sub="9999")
    with pytest.raises(google.GoogleError) as exc:
        google.match_or_create_account(other, CONFIG)
    assert exc.value.code == "identity_mismatch"


def test_google_unknown_address_is_refused_unless_domain_allowed(transactional_db):
    identity = google.GoogleIdentity(sub="1", email="new@corp.example", name="N", picture="", hosted_domain="corp.example")
    with pytest.raises(google.GoogleError) as exc:
        google.match_or_create_account(identity, CONFIG)
    assert exc.value.code == "no_account"
    allowed = CONFIG._replace(allowed_domains=frozenset({"corp.example"}))
    created = google.match_or_create_account(identity, allowed)
    assert created.role == "member" and created.google_sub == "1"
    # A matching domain WITHOUT the Workspace ``hd`` claim (a consumer
    # account using that address) never auto-creates.
    consumer = identity._replace(sub="2", email="other@corp.example", hosted_domain="")
    with pytest.raises(google.GoogleError):
        google.match_or_create_account(consumer, allowed)


# --- HTTP gates --------------------------------------------------------------------


@pytest.fixture
def client():
    return AsyncClient()


def test_account_login_and_auth_check(client, projects):
    _user("member@example.com", password="secret-pass")
    response = _run(client.post(
        "/api/auth/login/", data={"email": "MEMBER@example.com", "password": "secret-pass"},
        content_type="application/json",
    ))
    assert response.status_code == 200
    check = _run(client.get("/api/auth/check/")).json()
    assert check["authenticated"] is True and check["accounts_enabled"] is True
    assert check["principal"]["email"] == "member@example.com"
    assert check["principal"]["role"] == "member"


def test_wrong_password_is_refused(client, projects):
    _user("member@example.com", password="secret-pass")
    response = _run(client.post(
        "/api/auth/login/", data={"email": "member@example.com", "password": "nope"},
        content_type="application/json",
    ))
    assert response.status_code == 401


def test_accounts_make_the_instance_authenticated(client, projects):
    _user()
    assert _run(client.get("/api/projects/")).status_code == 401


def test_member_sees_only_granted_projects(client, projects):
    user = _user()
    service.set_workspace_grant(user.id, "ws-a", "view")
    _login_as(client, user)
    ids = {p["id"] for p in _run(client.get("/api/projects/")).json()}
    assert ids == {"-proj-a1", "-proj-a2", "-proj-a1-wt"}


def test_member_cannot_reach_a_foreign_project(client, projects):
    user = _user()
    service.set_workspace_grant(user.id, "ws-a", "view")
    _login_as(client, user)
    assert _run(client.get("/api/projects/-proj-b1/")).status_code == 404
    assert _run(client.get("/api/projects/-proj-a1/sessions/")).status_code == 200


def test_member_cannot_pair_a_visible_project_with_a_foreign_session(client, projects):
    user = _user()
    service.set_project_grant(user.id, "-proj-a1", "view")
    Session.objects.create(id="foreign-session", project_id="-proj-b1", file_path="/tmp/foreign-session.jsonl")
    _login_as(client, user)
    response = _run(client.get("/api/projects/-proj-a1/sessions/foreign-session/items/"))
    assert response.status_code == 404


def test_view_level_cannot_write(client, projects):
    user = _user()
    service.set_project_grant(user.id, "-proj-a1", "view")
    _login_as(client, user)
    response = _run(client.post(
        "/api/projects/-proj-a1/file-create/", data={}, content_type="application/json",
    ))
    assert response.status_code == 403


def test_member_is_denied_instance_routes(client, projects):
    user = _user()
    service.set_project_grant(user.id, "-proj-a1", "operate")
    _login_as(client, user)
    for path in ("/api/usage-history/", "/api/peers/", "/api/shares/", "/api/home-directory/", "/api/admin/users/"):
        assert _run(client.get(path)).status_code == 403, path
    # Project metadata edits stay admin-only whatever the grant.
    response = _run(client.patch("/api/projects/-proj-a1/", data={"name": "x"}, content_type="application/json"))
    assert response.status_code == 403


def test_admin_account_manages_users(client, projects):
    admin = _user("admin@example.com", role="admin")
    _login_as(client, admin)
    response = _run(client.post(
        "/api/admin/users/", data={"email": "new@example.com", "role": "member"}, content_type="application/json",
    ))
    assert response.status_code == 201
    new_id = response.json()["user"]["id"]
    response = _run(client.put(
        f"/api/admin/users/{new_id}/grants/",
        data={"project_grants": [{"project_id": "-proj-solo", "level": "view"}], "workspace_grants": []},
        content_type="application/json",
    ))
    assert response.status_code == 200
    assert response.json()["user"]["project_grants"] == [{"project_id": "-proj-solo", "level": "view"}]
    # An admin cannot lock themselves out.
    response = _run(client.patch(
        f"/api/admin/users/{admin.id}/", data={"role": "member"}, content_type="application/json",
    ))
    assert response.status_code == 409


def test_stale_epoch_logs_out(client, projects):
    user = _user()
    _login_as(client, user)
    assert _run(client.get("/api/me/")).status_code == 200
    service.revoke_sessions(user.id)
    assert _run(client.get("/api/me/")).status_code == 401


def test_disabled_account_is_logged_out(client, projects):
    user = _user()
    _login_as(client, user)
    service.update_user(user.id, is_active=False)
    assert _run(client.get("/api/me/")).status_code == 401


def test_disabling_every_account_does_not_reopen_the_instance(client, projects, settings):
    """Accounts exist (even all disabled) → authentication stays required."""
    settings.TWICC_PASSWORD_HASH = ""
    user = _user()
    service.update_user(user.id, is_active=False)
    invalidate_accounts_enabled()
    assert _run(client.get("/api/projects/")).status_code == 401


# --- WebSocket frame filter ------------------------------------------------------


def test_member_frame_filter(projects):
    user = _user()
    service.set_workspace_grant(user.id, "ws-a", "view")
    Session.objects.create(id="s-visible", project_id="-proj-a1", file_path="/tmp/s-visible.jsonl")
    Session.objects.create(id="s-foreign", project_id="-proj-b1", file_path="/tmp/s-foreign.jsonl")
    principal = principal_for_user(user)
    flt = MemberFrameFilter(principal, get_project_access_sync(principal))

    async def go():
        assert await flt.filter({"type": "session_items_added", "project_id": "-proj-a1", "session_id": "x"})
        assert await flt.filter({"type": "session_items_added", "project_id": "-proj-b1", "session_id": "y"}) is None
        assert await flt.filter({"type": "stream_block_delta", "session_id": "s-visible"})
        assert await flt.filter({"type": "stream_block_delta", "session_id": "s-foreign"}) is None
        assert await flt.filter({"type": "session_updated", "session": {"id": "z", "project_id": "-proj-a2"}})
        assert await flt.filter({"type": "peers_updated", "peers": []}) is None  # instance-level
        assert await flt.filter({"type": "brand_new_type"}) is None  # deny by default
        settings_frame = await flt.filter({
            "type": "synced_settings_updated",
            "settings": {"theme": "dark", "externalNotificationTargets": [{"url": "secret"}]},
        })
        assert settings_frame["settings"] == {"theme": "dark"}
        hidden = await flt.filter({"type": "hidden_sessions", "session_ids": ["s-visible", "s-foreign"]})
        assert hidden["session_ids"] == ["s-visible"]

    asyncio.run(go())
