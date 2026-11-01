"""Hybrid evidence proves convergence, never ownership of pasted terminal input."""

import asyncio

import pytest

from twicc.agent.states import AgentState, PendingRequest
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
from twicc.providers.claude_code.agent.hybrid import tmux
from twicc.providers.claude_code.agent.hybrid.signals import HybridRenameEvidence
from twicc.providers.claude_code.title_control import TitleControlError
from twicc.providers.claude_code.title_sync import SourceCursor
from twicc.providers.helpers import AgentSettings


def evidence(kind, title=None, *, message="result", parent="command", line=2):
    return HybridRenameEvidence(SourceCursor("session", "storage", 0, line), "session", kind, title, message, parent)


def make_agent(monkeypatch):
    agent = HybridClaudeAgent("session", "project", "/tmp", AgentSettings())
    agent.state = AgentState.USER_TURN
    pasted = asyncio.Event()
    sent = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(tmux, "composer_ready", lambda sid: True)

    def paste(sid, text):
        sent.append(text)
        loop.call_soon_threadsafe(pasted.set)

    monkeypatch.setattr(tmux, "paste_text", paste)
    return agent, pasted, sent


@pytest.mark.asyncio
async def test_only_ancestry_scoped_outcome_completes_rename_without_turn_mutation(monkeypatch):
    agent, pasted, sent = make_agent(monkeypatch)
    agent._local_cmd_turn = False
    agent.state = AgentState.ASSISTANT_TURN
    permission = PendingRequest("permission", "tool_approval", "Bash", {}, 0)
    permission_future = asyncio.get_running_loop().create_future()
    agent._pending_requests["permission"] = permission
    agent._pending_futures["permission"] = permission_future
    task = asyncio.create_task(agent.rename("title", token="token"))
    await pasted.wait()
    agent.on_title_evidence((evidence("title", "title"), evidence("command_success", None, parent="model")))
    assert not task.done()
    agent.on_title_evidence((evidence("rename_command", "title", message="command"), evidence("command_success", "title")))
    await task
    assert sent == ["/rename title"]
    assert agent.state == AgentState.ASSISTANT_TURN
    assert agent._local_cmd_turn is False
    assert agent.pending_requests == (permission,)
    assert not permission_future.done()
    permission_future.cancel()
    assert agent._auto_paste_tasks == {}


@pytest.mark.asyncio
async def test_paste_without_result_times_out_without_resend(monkeypatch):
    agent, pasted, sent = make_agent(monkeypatch)
    agent.TITLE_CONFIRMATION_TIMEOUT = 0
    with pytest.raises(TimeoutError):
        await agent.rename("title", token="token")
    assert sent == ["/rename title"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,title", [("command_error", None), ("command_success", "title (2)")])
async def test_rejects_command_failure_and_collision_effective_name(monkeypatch, kind, title):
    agent, pasted, sent = make_agent(monkeypatch)
    task = asyncio.create_task(agent.rename("title", token="token"))
    await pasted.wait()
    agent.on_title_evidence((evidence("rename_command", "title", message="command"), evidence(kind, title)))
    with pytest.raises(TitleControlError):
        await task
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_superseded_decision_does_not_paste(monkeypatch):
    agent, pasted, sent = make_agent(monkeypatch)

    async def before_paste():
        return False

    with pytest.raises(TitleControlError):
        await agent.rename("obsolete", token="token", before_paste=before_paste)
    assert sent == []


@pytest.mark.asyncio
async def test_pre_paste_same_title_command_does_not_confirm(monkeypatch):
    agent, pasted, sent = make_agent(monkeypatch)

    async def before_paste():
        agent.on_title_evidence((evidence("rename_command", "title", message="manual-before"),
                                 evidence("command_success", "title", parent="manual-before")))
        return True

    task = asyncio.create_task(agent.rename("title", token="token", before_paste=before_paste))
    await pasted.wait()
    assert not task.done()
    # This may also be manual input queued before the paste. It proves only
    # convergence. The coordinator retains the independent paste hazard.
    agent.on_title_evidence((evidence("rename_command", "title", message="manual-after"),
                             evidence("command_success", "title", parent="manual-after")))
    await task
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_busy_composer_can_retire_queued_title_before_any_paste(monkeypatch):
    import threading

    agent, pasted, sent = make_agent(monkeypatch)
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def composer_ready(sid):
        loop.call_soon_threadsafe(entered.set)
        release.wait(timeout=1)
        return True

    monkeypatch.setattr(tmux, "composer_ready", composer_ready)
    current = True

    async def before_paste():
        return current

    task = asyncio.create_task(agent.rename("obsolete", token="token", before_paste=before_paste))
    await entered.wait()
    assert sent == []
    current = False
    release.set()
    with pytest.raises(TitleControlError):
        await task
    assert sent == []
