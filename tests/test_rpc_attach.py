"""/rpc/: inline attachments reach the services; token calls get the 72 MB body cap (§4.6)."""

import asyncio
import contextlib
import logging
from importlib import import_module

import orjson
import pytest
from django.conf import settings as dj_settings
from django.test import AsyncClient, RequestFactory

from twicc.auth import tokens as api_tokens
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint
from twicc.cli._drop_request import whoami
from twicc.core.models import Project, Session
from twicc.core.services import send_message as send_message_service
from twicc.core.services.attachments import inline, staging
from twicc.core.services.send_message import SendMessageResult
from twicc.rpc import views
from twicc.rpc.invoker import InvocationResult

PASSWORD_HASH = "pbkdf2_sha256$test$deadbeef"


def _post(client, path, body=b"", **extra):
    return asyncio.run(client.post(path, data=body, content_type="application/json", **extra))


@contextlib.contextmanager
def _logs(name: str):
    logger = logging.getLogger(name)
    was_disabled, was_level = logger.disabled, logger.level
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


@pytest.fixture
def open_instance(settings, monkeypatch):
    """No password and no token: every /rpc/ call is full scope."""
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: False)
    return settings


@pytest.fixture
def invoke_calls(monkeypatch):
    calls: list[list[str]] = []

    def fake_invoke(argv):
        calls.append(list(argv))
        return InvocationResult(exit_code=0, result={"ok": True}, error=None)

    monkeypatch.setattr(views, "invoke", fake_invoke)
    return calls


def test_a_token_call_is_not_capped_by_django(open_instance, invoke_calls, monkeypatch):
    open_instance.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 4096)
    body = orjson.dumps({"argv": ["sessions", "--project", "x" * 2000]})
    response = _post(AsyncClient(), "/rpc/sessions", body)
    assert response.status_code == 200
    assert invoke_calls == [["sessions", "--project", "x" * 2000]]


def test_a_token_call_above_the_cap_gets_413(open_instance, invoke_calls, monkeypatch):
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 64)
    response = _post(AsyncClient(), "/rpc/sessions", orjson.dumps({"argv": ["sessions", "x" * 100]}))
    assert response.status_code == 413
    assert orjson.loads(response.content) == {"error": "Request body too large"}
    assert invoke_calls == []


def test_a_body_without_content_length_is_read_within_the_cap(invoke_calls, monkeypatch):
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 64)
    factory = RequestFactory()
    small = factory.post("/rpc/sessions", data=orjson.dumps({"argv": ["sessions"]}), content_type="application/json")
    del small.META["CONTENT_LENGTH"]
    assert asyncio.run(views.dispatch(small, "sessions")).status_code == 200
    big = factory.post("/rpc/sessions", data=b"x" * 100, content_type="application/json")
    del big.META["CONTENT_LENGTH"]
    assert asyncio.run(views.dispatch(big, "sessions")).status_code == 413
    assert invoke_calls == [["sessions"]]


def test_a_cookie_call_keeps_the_django_cap(settings, invoke_calls, monkeypatch, transactional_db):
    settings.TWICC_PASSWORD_HASH = PASSWORD_HASH
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: False)
    client = AsyncClient()
    engine = import_module(dj_settings.SESSION_ENGINE)
    store = engine.SessionStore()
    store[SESSION_AUTH_KEY] = True
    store[SESSION_FINGERPRINT_KEY] = compute_fingerprint(PASSWORD_HASH)
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key
    response = _post(client, "/rpc/sessions", orjson.dumps({"project": "x" * 2000}))
    assert response.status_code == 400
    assert invoke_calls == []


def test_a_failing_command_logs_no_attachment_bytes(open_instance, monkeypatch):
    def explode(argv):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "_run_invoke", explode)
    data = "A" * 4000
    body = orjson.dumps({"argv": ["send-message", "sid", "hi", f"--attach=data:image/png;base64,{data}"]})
    with _logs("twicc.rpc.views") as records:
        response = _post(AsyncClient(), "/rpc/send-message", body)
    assert response.status_code == 500
    text = " ".join(record.getMessage() for record in records)
    assert data not in text
    assert "chars>" in text


@pytest.fixture
def rpc_session(transactional_db, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    project = Project.objects.create(id="rpc-project", directory=str(tmp_path))
    Session.objects.create(id="rpc-session", project=project, provider="claude_code", file_path="r.jsonl")
    seen: list[dict] = []

    async def fake_service(payload, *, release_refs_on_outcome=False):
        seen.append(payload)
        return SendMessageResult(True, "rpc-session", "claude_code", "rpc-project", None, {"last_line": 0})

    monkeypatch.setattr(send_message_service, "send_message_to_session_from_payload", fake_service)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


def test_an_rpc_data_uri_is_staged_by_the_backend_and_reaches_the_service(open_instance, rpc_session):
    body = orjson.dumps({
        "session_id": "rpc-session", "prompt": "hi", "attach": ["data:text/plain;name=n.txt;base64,aGk="],
    })
    envelope = orjson.loads(_post(AsyncClient(), "/rpc/send-message", body).content)
    assert envelope["exit_code"] == 0, envelope
    [ref] = [staging.validate_ref(item) for item in rpc_session[0]["attachments"]]
    assert ref.bucket.startswith("api-")
    assert staging.load_entry(ref).filename == "n.txt"


def test_an_absolute_server_path_does_not_count_toward_the_limit(open_instance, rpc_session, tmp_path, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 20)
    body = orjson.dumps({"session_id": "rpc-session", "prompt": "hi", "attach": [str(path)]})
    envelope = orjson.loads(_post(AsyncClient(), "/rpc/send-message", body).content)
    assert envelope["exit_code"] == 0, envelope
    assert staging.load_entry(staging.validate_ref(rpc_session[0]["attachments"][0])).size == 20
