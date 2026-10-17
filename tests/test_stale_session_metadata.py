"""A stale session (conversation file gone) keeps its metadata editable.

Only the callers that need the conversation (send-message) refuse it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.cli._drop_request.session_lookup import SessionLookupError, ensure_conversation, lookup_session
from twicc.core.models import Project, Session
from twicc.core.services import session_update
from twicc.providers import db_writer


@pytest.fixture
def stale_session(db):
    project = Project.objects.create(id="stale-metadata-project", directory="/tmp/stale-metadata")
    return Session.objects.create(
        id="stale-metadata", project=project, provider="claude_code",
        file_path="missing.jsonl", stale=True, created_at="2026-10-17T00:00:00Z",
    )


@pytest.fixture
def quiet_services(monkeypatch):
    monkeypatch.setattr(session_update, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(session_update, "get_channel_layer", lambda: None)
    monkeypatch.setattr("twicc.search.is_initialized", lambda: False)
    registry = MagicMock()
    registry.kill_agent = AsyncMock()
    registry.get.return_value.get_agent_info.return_value = None
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: registry)
    monkeypatch.setattr("twicc.terminal.kill_all_tmux_terminals", lambda *args, **kwargs: None)
    monkeypatch.setattr("twicc.title_auto_task.request_title_check", lambda *args, **kwargs: None)


def run(coro_fn):
    async def wrapper():
        db_writer.start_db_writer()
        try:
            return await coro_fn()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(wrapper())


def test_lookup_accepts_a_stale_session_and_only_send_refuses_it(stale_session):
    resolved = lookup_session(stale_session.id)
    assert resolved.stale is True
    with pytest.raises(SessionLookupError) as error:
        ensure_conversation(resolved)
    assert error.value.code == "session_stale"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("update,payload,field,expected", [
    (session_update.update_session_archived_from_payload, {"archived": True}, "archived", True),
    (session_update.update_session_pinned_from_payload, {"pinned": "all"}, "pinned", "all"),
    (session_update.update_session_hidden_from_payload, {"hidden": True}, "hidden", True),
    (session_update.update_session_title_from_payload, {"title": "New title"}, "title", "New title"),
    (session_update.update_session_settings_from_payload, {"updates": {"effort": "high"}}, "effort", "high"),
    (
        session_update.update_session_annotations_from_payload,
        {"operations": [{"op": "set", "path": ["reviewed"], "value": True}]},
        "annotations", {"reviewed": True},
    ),
])
def test_services_update_a_stale_session(stale_session, quiet_services, update, payload, field, expected):
    result = run(lambda: update({"session_id": stale_session.id, **payload}))
    assert result.success, result.errors
    stale_session.refresh_from_db()
    assert getattr(stale_session, field) == expected
    assert stale_session.stale is True


@pytest.mark.django_db(transaction=True)
def test_title_of_a_stale_session_does_not_reach_the_provider(stale_session, quiet_services, monkeypatch):
    from twicc.providers.helpers import get_provider_helpers

    helpers = get_provider_helpers("claude_code")
    rename = AsyncMock()
    monkeypatch.setattr(helpers, "rename_session", rename)
    result = run(lambda: session_update.update_session_title_from_payload(
        {"session_id": stale_session.id, "title": "Local only"},
    ))
    assert result.success, result.errors
    rename.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_hidden_session_settings_ignore_the_hidden_whitelist_when_stale(stale_session, quiet_services):
    stale_session.hidden = True
    stale_session.save(update_fields=["hidden"])
    result = run(lambda: session_update.update_session_settings_from_payload(
        {"session_id": stale_session.id, "updates": {"permission_mode": "default"}},
    ))
    assert result.success, result.errors
    stale_session.refresh_from_db()
    assert stale_session.permission_mode == "default"
