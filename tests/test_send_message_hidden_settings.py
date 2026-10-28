"""A WS ``send_message`` on an existing session keeps the settings the frontend cannot send.

``question_widget`` is hidden from the frontend, so the payload never carries it. The stored value
must survive the send, and the agent must receive it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc import asgi
from twicc.asgi import WSConsumer
from twicc.core.models import Project, Session
from twicc.core.enums import Provider


class FakeManager:
    def __init__(self):
        self.calls: list = []

    def get_agent_info(self, session_id):
        return None

    async def send_to_session(self, session_id, project_id, cwd, text, *, settings, **kwargs):
        self.calls.append(settings)
        return True


@pytest.fixture
def consumer(monkeypatch):
    manager = FakeManager()
    helpers = SimpleNamespace(
        resolve_agent_settings=lambda s: s,
        enforce_agent_settings_consistency=lambda s: s,
        validate_title=lambda t: SimpleNamespace(error=None, title=t),
    )

    async def run_directly(fn):
        return await fn()

    monkeypatch.setattr("twicc.asgi.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr("twicc.asgi.get_provider_helpers", lambda provider: helpers)
    monkeypatch.setattr("twicc.asgi.get_project_directory", AsyncMock(return_value="/project"))
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: manager))
    monkeypatch.setattr("twicc.asgi.run_under_db_write_lock", run_directly)

    instance = WSConsumer()
    instance.send_json = AsyncMock()
    instance.channel_layer = SimpleNamespace(group_send=AsyncMock())
    instance.manager = manager
    return instance


async def _send(consumer, session_id):
    await consumer._handle_send_message({
        "type": "send_message", "session_id": session_id, "project_id": "p", "text": "again",
        "request_id": "req-1", "selected_model": "opus", "effort": "high",
    })
    while asgi._DETACHED_TASKS:
        await asyncio.gather(*list(asgi._DETACHED_TASKS), return_exceptions=True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("provider", [Provider.CLAUDE_CODE, Provider.CODEX])
@pytest.mark.parametrize("stored", [False, True, None])
def test_stored_question_widget_survives_a_send(consumer, provider, stored):
    project = Project.objects.create(id="p", directory="/project")
    Session.objects.create(
        id="s1", project=project, provider=provider, file_path="s1.jsonl", question_widget=stored,
    )

    asyncio.run(_send(consumer, "s1"))

    session = Session.objects.get(id="s1")
    assert session.question_widget is stored
    assert session.selected_model == "opus"  # the settings the frontend sends are still written
    assert [s.question_widget for s in consumer.manager.calls] == [stored]
