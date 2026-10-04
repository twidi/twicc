"""Title writers stamp explicit choices and preserve provider-channel origins."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.test import RequestFactory

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
from twicc.providers.codex.titles import SyncSessionTitlesJob, _apply_sync_session_titles_job
from twicc.providers.sessions_watcher import ParsedSessionFile
from twicc import title_echo

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def session():
    project = Project.objects.create(id="origin-project", directory="/test/origin")
    return Session.objects.create(
        id="00000000-0000-4000-8000-000000000001", project=project,
        provider=Provider.CLAUDE_CODE, title="Same", title_origin="auto",
    )


@pytest.fixture
def broadcasts(monkeypatch):
    from twicc import search
    from twicc.core.services import session_update
    from twicc import views
    from twicc.providers.claude_code.helpers import ClaudeCodeHelpers

    channel = SimpleNamespace(group_send=AsyncMock())
    monkeypatch.setattr("channels.layers.get_channel_layer", lambda: channel)
    monkeypatch.setattr(views, "get_channel_layer", lambda: channel)
    monkeypatch.setattr(session_update, "get_channel_layer", lambda: channel)
    monkeypatch.setattr(views, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr(session_update, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr(search, "is_initialized", lambda: False)
    monkeypatch.setattr(ClaudeCodeHelpers, "rename_session", AsyncMock(side_effect=OSError("writeback failed")))
    monkeypatch.setattr(title_echo, "_automatic_title_echoes", {})
    return channel.group_send


def run_with_writer(coroutine):
    from twicc.providers import db_writer

    async def scenario():
        db_writer.start_db_writer()
        try:
            return await coroutine
        finally:
            await db_writer.stop_db_writer()
    return asyncio.run(scenario())


def assert_user_choice(session, broadcasts, expected):
    session.refresh_from_db()
    assert (session.title, session.title_origin) == (expected, "user")
    payloads = [call.args[1]["data"] for call in broadcasts.call_args_list]
    assert len(payloads) == 1
    assert payloads[0]["type"] == "session_updated"
    assert payloads[0]["session"]["title_origin"] == "user"
    assert payloads[0]["session"]["title"] == expected
    assert title_echo.should_skip_automatic_title_echo(session.id, "Automatic", title=expected, title_origin="user")


def test_rest_same_text_validates_title(session, broadcasts):
    from twicc.views import session_detail
    title_echo.record_automatic_title_push(session.id, "Automatic")
    request = RequestFactory().patch("/", data={"title": "Same"}, content_type="application/json")
    response = run_with_writer(session_detail(request, session.project_id, session.id))
    assert response.status_code == 200
    assert_user_choice(session, broadcasts, "Same")


def test_cli_same_text_validates_title(session, broadcasts):
    from twicc.core.services.session_update import update_session_title_from_payload
    title_echo.record_automatic_title_push(session.id, "Automatic")
    run_with_writer(update_session_title_from_payload({"session_id": session.id, "title": "Same"}))
    assert_user_choice(session, broadcasts, "Same")


def test_pending_flush_validates_title(session, broadcasts):
    from twicc.agent.base_manager import BaseAgentManager
    title_echo.record_automatic_title_push(session.id, "Automatic")
    # The method uses only the common manager's broadcast hook.
    manager = SimpleNamespace(
        _broadcast_session_updated=lambda sid: BaseAgentManager._broadcast_session_updated(None, sid),
    )
    result = run_with_writer(BaseAgentManager._try_flush_pending_title(
        manager, SimpleNamespace(session_id=session.id, provider=session.provider), "Chosen"))
    assert result is False  # Provider writeback fails; persistence and notification still succeed.
    assert_user_choice(session, broadcasts, "Chosen")


def test_discovery_stamps_automatic_origin(session):
    watcher = ClaudeCodeSessionsWatcher()
    parsed = ParsedSessionFile(
        session.project_id, "discovered", SessionType.SESSION, "discovered.jsonl", title="Provider",
    )
    watcher.create_session_sync(parsed, session.project)
    discovered = Session.objects.get(id="discovered")
    assert (discovered.title, discovered.title_origin) == ("Provider", "auto")


def test_placeholder_stamps_automatic_origin(session):
    Session.objects.filter(id=session.id).update(title=None, title_origin="")
    assert get_compute().apply_placeholder_title(session.id, "First message")
    session.refresh_from_db()
    assert (session.title, session.title_origin) == ("First message", "auto")


@pytest.mark.parametrize("origin", ["", "auto", "user"])
@pytest.mark.parametrize("old_title", [None, "Same", "Other"])
@pytest.mark.parametrize("writer", ["base", "claude", "codex_import"])
def test_provider_writes_preserve_existing_origin(session, origin, old_title, writer):
    from twicc.providers.compute_base import BaseSessionCompute
    Session.objects.filter(id=session.id).update(title=old_title, title_origin=origin, provider=Provider.CODEX)
    if writer == "codex_import":
        changed = _apply_sync_session_titles_job(SyncSessionTitlesJob({session.id: "Same"}, None))
        assert len(changed) == (old_title != "Same")
        if changed:
            assert changed[0]["title_origin"] == ("auto" if old_title is None else origin)
    elif writer == "base":
        BaseSessionCompute.apply_session_title(get_compute(), session.id, "Same")
    else:
        get_compute().apply_session_title(session.id, "Same")
    session.refresh_from_db()
    assert (session.title, session.title_origin) == ("Same", "auto" if old_title is None else origin)
