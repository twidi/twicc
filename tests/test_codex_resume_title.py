"""A resumed thread must receive the current title, not a stale snapshot."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from twicc.core.models import Project, Session
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.helpers import CodexHelpers
from twicc.providers.helpers import AgentSettings

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change_at", ["delay", "sdk"])
@pytest.mark.parametrize("origin", ["auto", "user"])
def test_resume_publishes_latest_title(monkeypatch, change_at, origin):
    session = Session.objects.create(
        id="resume-title", project=Project.objects.create(id="resume-project"),
        provider="codex", title="Old title", title_origin="auto",
    )

    async def scenario():
        manager = CodexAgentManager()
        agent = CodexAgent(session.id, session.project_id, "/tmp", AgentSettings(), AsyncMock(), AsyncMock(),
                           work_dirs=[])
        manager._agents[session.id] = agent
        entered_delay = asyncio.Event()
        release_delay = asyncio.Event()
        real_sleep = asyncio.sleep
        provider_titles = []

        async def sleep(delay):
            if delay == 5.0:
                entered_delay.set()
                await release_delay.wait()
            else:
                await real_sleep(delay)

        async def rename(self, session_id, title):
            if change_at == "sdk" and not provider_titles:
                await Session.objects.filter(pk=session_id).aupdate(title="New title", title_origin=origin)
            provider_titles.append(title)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        monkeypatch.setattr(CodexHelpers, "rename_session", rename)
        await manager._reassert_codex_title_after_resume(agent)
        task = manager._pending_title_verify_tasks[session.id]
        await entered_delay.wait()
        if change_at == "delay":
            await Session.objects.filter(pk=session.id).aupdate(title="New title", title_origin=origin)
        release_delay.set()
        await task

        assert provider_titles[-1] == "New title"
        if change_at == "delay":
            assert provider_titles == ["New title"]
        assert session.id not in manager._pending_title_verify_tasks

    asyncio.run(scenario())
