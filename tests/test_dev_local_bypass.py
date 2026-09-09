"""The dev-worktree local bypass of the instance password.

Its whole value is what it REFUSES: anything that is not a real worktree, and
anything that did not arrive as a direct loopback connection. Most of these
tests pin refusals.
"""

import asyncio

import pytest
from django.conf import settings as django_settings
from django.test import AsyncClient

from twicc.auth import access

HASH = "pbkdf2_sha256$600000$c2FsdA==$aGFzaA=="


@pytest.fixture(autouse=True)
def clean_cache():
    access.reset_cache()
    yield
    access.reset_cache()


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    """A data dir that really is a secondary git worktree."""
    (tmp_path / ".git").write_text(f"gitdir: {tmp_path}/../repo/.git/worktrees/mine\n")
    monkeypatch.setattr(access, "get_data_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def password_set(monkeypatch):
    monkeypatch.setattr(django_settings, "TWICC_PASSWORD_HASH", HASH)


@pytest.fixture
def bypass_asked(monkeypatch):
    monkeypatch.setattr(django_settings, "TWICC_DEV_LOCAL_BYPASS", True)


class FakeRequest:
    """Minimal stand-in for the attributes the locality classifier reads."""

    def __init__(self, remote_addr="127.0.0.1", **extra_meta):
        self.META = {"REMOTE_ADDR": remote_addr, **extra_meta}


def local_scope(client=("127.0.0.1", 51234), headers=()):
    return {"client": client, "headers": list(headers)}


# ── The gate itself ─────────────────────────────────────────────────────────


def test_disabled_without_the_flag(worktree):
    assert access.dev_local_bypass_enabled() is False


def test_disabled_when_the_data_dir_is_not_a_worktree(tmp_path, monkeypatch, bypass_asked):
    """The flag alone is not enough — this is what makes a stray .env harmless."""
    monkeypatch.setattr(access, "get_data_dir", lambda: tmp_path)

    assert access.dev_local_bypass_enabled() is False


def test_disabled_when_dot_git_is_a_normal_clone(tmp_path, monkeypatch, bypass_asked):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(access, "get_data_dir", lambda: tmp_path)

    assert access.dev_local_bypass_enabled() is False


def test_disabled_for_a_submodule_gitdir(tmp_path, monkeypatch, bypass_asked):
    """A submodule also uses a `.git` file, but its gitdir has no worktrees segment."""
    (tmp_path / ".git").write_text("gitdir: ../.git/modules/sub\n")
    monkeypatch.setattr(access, "get_data_dir", lambda: tmp_path)

    assert access.dev_local_bypass_enabled() is False


def test_enabled_in_a_real_worktree_with_the_flag(worktree, bypass_asked):
    assert access.dev_local_bypass_enabled() is True


def test_verdict_is_cached(worktree, bypass_asked, monkeypatch):
    assert access.dev_local_bypass_enabled() is True

    (worktree / ".git").unlink()

    assert access.dev_local_bypass_enabled() is True


# ── Per-request locality ────────────────────────────────────────────────────


def test_loopback_request_is_allowed(worktree, bypass_asked, password_set):
    assert access.request_allowed(FakeRequest(), None, None) is True


def test_remote_request_is_refused(worktree, bypass_asked, password_set):
    assert access.request_allowed(FakeRequest(remote_addr="192.168.1.20"), None, None) is False


@pytest.mark.parametrize("header", [
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_REAL_IP",
    "HTTP_CF_CONNECTING_IP",
    "HTTP_FLY_CLIENT_IP",
    "HTTP_FORWARDED",
])
def test_tunnelled_request_is_refused(worktree, bypass_asked, password_set, header):
    """The tunnel case the bypass must never open: loopback peer, but proxied."""
    request = FakeRequest(**{header: "203.0.113.9"})

    assert access.request_allowed(request, None, None) is False


def test_loopback_websocket_is_allowed(worktree, bypass_asked, password_set):
    assert access.scope_allowed(local_scope(), None, None) is True


def test_tunnelled_websocket_is_refused(worktree, bypass_asked, password_set):
    scope = local_scope(headers=[(b"x-forwarded-for", b"203.0.113.9")])

    assert access.scope_allowed(scope, None, None) is False


def test_remote_websocket_is_refused(worktree, bypass_asked, password_set):
    assert access.scope_allowed(local_scope(client=("192.168.1.20", 51234)), None, None) is False


# ── Without the bypass, nothing changes ─────────────────────────────────────


def test_loopback_is_refused_when_the_bypass_is_off(worktree, password_set):
    """A non-worktree instance keeps its old behaviour: session or nothing."""
    assert access.request_allowed(FakeRequest(), None, None) is False
    assert access.scope_allowed(local_scope(), None, None) is False


def test_a_valid_session_still_wins_everywhere(password_set):
    """No bypass involved: the session path is unchanged, tunnel included."""
    from twicc.auth.session_auth import compute_fingerprint

    fingerprint = compute_fingerprint(HASH)
    tunnelled = FakeRequest(HTTP_X_FORWARDED_FOR="203.0.113.9")

    assert access.request_allowed(tunnelled, True, fingerprint) is True
    assert access.scope_allowed(
        local_scope(headers=[(b"x-forwarded-for", b"203.0.113.9")]), True, fingerprint
    ) is True


def test_a_session_bound_to_another_hash_is_refused(worktree, password_set):
    """The rotated-password invalidation must survive the refactor."""
    assert access.request_allowed(FakeRequest(), True, "deadbeefdeadbeef") is False


# ── Wiring: the gates actually consult the bypass ───────────────────────────


@pytest.fixture
def password_only(tmp_path, monkeypatch, settings):
    """A worktree instance with a password set, bypass OFF — the control."""
    (tmp_path / ".git").write_text(f"gitdir: {tmp_path}/../repo/.git/worktrees/mine\n")
    monkeypatch.setattr(access, "get_data_dir", lambda: tmp_path)
    settings.TWICC_PASSWORD_HASH = HASH
    settings.TWICC_DEV_LOCAL_BYPASS = False


@pytest.fixture
def bypassing_worktree(password_only, settings):
    settings.TWICC_DEV_LOCAL_BYPASS = True


TUNNEL = {"headers": {"x-forwarded-for": "203.0.113.9"}}


def test_middleware_stops_a_loopback_api_request_without_the_bypass(password_only):
    """The control that makes the next test meaningful: the gate IS in the chain."""
    response = asyncio.run(AsyncClient().get("/api/no-such-endpoint/"))

    assert response.status_code == 401


def test_middleware_lets_a_loopback_api_request_through(bypassing_worktree):
    """404 means the request reached the URL resolver, so the gate let it pass."""
    response = asyncio.run(AsyncClient().get("/api/no-such-endpoint/"))

    assert response.status_code == 404


def test_middleware_still_stops_a_tunnelled_api_request(bypassing_worktree):
    response = asyncio.run(AsyncClient().get("/api/no-such-endpoint/", **TUNNEL))

    assert response.status_code == 401


def test_auth_check_reports_authenticated_on_loopback(bypassing_worktree, db):
    """The SPA reads this: a mismatch here shows a login screen over a working API."""
    response = asyncio.run(AsyncClient().get("/api/auth/check/"))

    assert response.json() == {"authenticated": True, "password_required": True}


def test_auth_check_reports_unauthenticated_through_a_tunnel(bypassing_worktree, db):
    response = asyncio.run(AsyncClient().get("/api/auth/check/", **TUNNEL))

    assert response.json() == {"authenticated": False, "password_required": True}
