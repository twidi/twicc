"""RPC authentication & authorization gate.

Covers ``RpcTokenAuthMiddleware`` (authentication → scope) and the dispatch
view's authorization (read-scope allowlist + argv-form lockout). The real
command invoker is replaced by a spy so the tests exercise the gate alone,
independently of any command's behavior: a request that the gate blocks never
reaches the spy.
"""

import asyncio
from importlib import import_module

import orjson
import pytest
from django.conf import settings as dj_settings
from django.test import AsyncClient

from twicc.auth import tokens as api_tokens
from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    SESSION_FINGERPRINT_KEY,
    compute_fingerprint,
)
from twicc.auth.tokens import TokenRecord
from twicc.rpc.generator import build_registry
from twicc.rpc.invoker import InvocationResult
from twicc.rpc.permissions import COOKIE_READONLY_COMMANDS

# Arbitrary non-empty value: only its fingerprint is ever compared, never the
# value itself (see twicc.auth.session_auth).
PASSWORD_HASH = "pbkdf2_sha256$test$deadbeef"


def _run(coro):
    return asyncio.run(coro)


def _record() -> TokenRecord:
    return TokenRecord(id="tok_test", name="t", digest="x", created_at="", last_used_at=None)


@pytest.fixture
def client():
    return AsyncClient()


@pytest.fixture
def invoke_calls(monkeypatch):
    """Spy on the command invoker. Returns the list of argv lists that reached
    execution; the gate blocking a request leaves it empty."""
    calls: list[list[str]] = []

    def _fake_invoke(argv):
        calls.append(list(argv))
        return InvocationResult(exit_code=0, result={"ok": True}, error=None)

    monkeypatch.setattr("twicc.rpc.views.invoke", _fake_invoke)
    return calls


@pytest.fixture
def tokens_store(monkeypatch):
    """Hermetic token store so tests never read the developer's real
    ~/.twicc/api-tokens.json. Add ``records[plaintext] = TokenRecord`` to make
    a token valid; an empty store means ``has_tokens()`` is False."""
    records: dict[str, TokenRecord] = {}
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: bool(records))
    monkeypatch.setattr(api_tokens, "verify_token", lambda tok: records.get(tok))
    return records


@pytest.fixture
def protected(settings):
    """Enable protection by configuring a password hash."""
    settings.TWICC_PASSWORD_HASH = PASSWORD_HASH
    return settings


def _login(client, *, fingerprint_hash=PASSWORD_HASH):
    """Give the client an authenticated session cookie bound to ``fingerprint_hash``.

    Uses the configured session engine directly (no reliance on the test
    client's ``session`` helper) and stamps the resulting key as the cookie.
    """
    engine = import_module(dj_settings.SESSION_ENGINE)
    store = engine.SessionStore()
    store[SESSION_AUTH_KEY] = True
    store[SESSION_FINGERPRINT_KEY] = compute_fingerprint(fingerprint_hash)
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key


def _post(client, path, body=b"", **extra):
    return _run(client.post(path, data=body, content_type="application/json", **extra))


# --- allowlist integrity (no HTTP) --------------------------------------------

def test_allowlist_entries_are_real_registry_paths():
    """Every read-scope path must be an actual RPC command (catches drift)."""
    unknown = COOKIE_READONLY_COMMANDS - set(build_registry())
    assert not unknown, f"allowlist references unknown commands: {sorted(unknown)}"


def test_allowlist_excludes_known_write_commands():
    """Mutating commands must never be reachable with an ambient cookie."""
    dangerous = {
        "create-session",
        "send-message",
        "send-messages",
        "processes/stop",
        "process/stop",
        "artifacts/bookmark",
        "artifacts/unbookmark",
        "create-workspace",
        "update-workspace",
        "delete-workspace",
        "create-project",
    }
    leaked = dangerous & COOKIE_READONLY_COMMANDS
    assert not leaked, f"write commands present in read allowlist: {sorted(leaked)}"
    # Guard the test itself against renames: these must exist in the registry.
    unknown = dangerous - set(build_registry())
    assert not unknown, f"test references unknown commands: {sorted(unknown)}"


# --- no credentials -----------------------------------------------------------

def test_no_credentials_returns_401(client, protected, tokens_store, invoke_calls):
    res = _post(client, "/rpc/sessions")
    assert res.status_code == 401
    assert invoke_calls == []


# --- bearer token = full scope ------------------------------------------------

def test_token_allows_read_command(client, protected, tokens_store, invoke_calls):
    tokens_store["secret"] = _record()
    res = _post(client, "/rpc/sessions", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
    assert invoke_calls == [["sessions"]]


def test_token_allows_write_command(client, protected, tokens_store, invoke_calls):
    tokens_store["secret"] = _record()
    # A write route that is not retired: past 2026-10-01 `processes/stop`
    # answers with its removal error before reaching `invoke`.
    res = _post(client, "/rpc/sessions/stop", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
    assert invoke_calls == [["sessions", "stop"]]


def test_token_allows_argv_form(client, protected, tokens_store, invoke_calls):
    tokens_store["secret"] = _record()
    res = _post(
        client,
        "/rpc/sessions",
        body=orjson.dumps({"argv": ["sessions"]}),
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 200
    assert invoke_calls == [["sessions"]]


def test_invalid_token_returns_401(client, protected, tokens_store, invoke_calls):
    tokens_store["secret"] = _record()
    res = _post(client, "/rpc/sessions", headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401
    assert invoke_calls == []


# --- session cookie = read scope ----------------------------------------------

def test_cookie_allows_read_command(client, protected, tokens_store, invoke_calls, transactional_db):
    _login(client)
    res = _post(client, "/rpc/sessions")
    assert res.status_code == 200
    assert invoke_calls == [["sessions"]]


def test_cookie_blocks_write_command(client, protected, tokens_store, invoke_calls, transactional_db):
    _login(client)
    res = _post(client, "/rpc/processes/stop")
    assert res.status_code == 403
    assert invoke_calls == []


def test_cookie_blocks_create_session_before_validation(
    client, protected, tokens_store, invoke_calls, transactional_db
):
    # The 403 fires before body validation, so an empty body still 403s (never
    # leaks that create-session has required fields).
    _login(client)
    res = _post(client, "/rpc/create-session")
    assert res.status_code == 403
    assert invoke_calls == []


def test_cookie_blocks_argv_form_even_for_read_url(
    client, protected, tokens_store, invoke_calls, transactional_db
):
    # URL names a read command (passes the path allowlist) but the argv escape
    # hatch is token-only — otherwise it could smuggle any command through.
    _login(client)
    res = _post(client, "/rpc/sessions", body=orjson.dumps({"argv": ["sessions"]}))
    assert res.status_code == 403
    assert invoke_calls == []


def test_cookie_with_stale_fingerprint_rejected(
    client, protected, tokens_store, invoke_calls, transactional_db
):
    # A session bound to a since-rotated password hash is not authenticated.
    _login(client, fingerprint_hash="some-old-rotated-hash")
    res = _post(client, "/rpc/sessions")
    assert res.status_code == 401
    assert invoke_calls == []


# --- unprotected instance -----------------------------------------------------

def test_unprotected_allows_everything(client, settings, tokens_store, invoke_calls):
    settings.TWICC_PASSWORD_HASH = ""  # no password, and tokens_store is empty
    res = _post(client, "/rpc/sessions/stop")
    assert res.status_code == 200
    assert invoke_calls == [["sessions", "stop"]]
