"""GeminiAgent against a fake ``gemini --acp`` speaking the real ACP shapes.

The fake CLI answers ``initialize`` / ``session/new`` / ``session/load`` /
``session/set_mode`` / ``session/set_model`` and, on ``session/prompt``, streams a
thought and a text chunk, asks for one tool permission, then ends the turn.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import textwrap

import pytest

from twicc.agent.states import AgentState
from twicc.providers.gemini.agent.agent import GEMINI_BIN_ENV, GeminiAgent
from twicc.providers.helpers import AgentSettings

FAKE_CLI = textwrap.dedent('''\
    import json, sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()

    def update(session_id, payload):
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": session_id, "update": payload}})

    log = open(sys.argv[-1], "a")
    for line in sys.stdin:
        message = json.loads(line)
        log.write(line)
        log.flush()
        method, mid = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "canonical-id"}})
        elif method == "session/load":
            update(params["sessionId"], {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old"}})
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method in ("session/set_mode", "session/set_model"):
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/prompt":
            sid = params["sessionId"]
            update(sid, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "**Plan**\\nthink"}})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "write_1", "status": "pending", "kind": "edit"})
            send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission", "params": {
                "sessionId": sid,
                "options": [{"optionId": "proceed_always", "name": "Allow for this session", "kind": "allow_always"},
                            {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "cancel", "name": "Reject", "kind": "reject_once"}],
                "toolCall": {"toolCallId": "write_1", "title": "Writing to out.txt", "kind": "edit",
                             "locations": [{"path": "/tmp/out.txt"}],
                             "content": [{"type": "diff", "path": "/tmp/out.txt", "oldText": "", "newText": "bye"}]}}})
            answer = json.loads(sys.stdin.readline())
            log.write(json.dumps(answer) + "\\n")
            log.flush()
            update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Done."}})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
''')


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    script = tmp_path / "fake_gemini.py"
    script.write_text(FAKE_CLI)
    log = tmp_path / "acp.log"
    wrapper = tmp_path / "gemini"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\" {log}\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(GEMINI_BIN_ENV, str(wrapper))
    return log


def _settings(**overrides) -> AgentSettings:
    base = {"permission_mode": "default", "selected_model": "gemini-flash", "effort": "medium", "thinking_enabled": None,
                "claude_in_chrome": None, "fast_mode": None, "context_max": None, "question_widget": None}
    base.update(overrides)
    return AgentSettings(**base)


class _Recorder(GeminiAgent):
    """Capture broadcasts instead of going through the channel layer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events: list[dict] = []
        self.states: list[AgentState] = []

    async def _broadcast_stream_event(self, data):
        self.events.append(data)

    async def _transition_to_dead(self):
        self._set_state(AgentState.DEAD)


async def _wait_for(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_new_session_turn_with_streaming_and_approval(fake_cli, tmp_path):
    agent = _Recorder("draft-id", "project", str(tmp_path), _settings(permission_mode="auto_edit"),
                      model="gemini-3.8-flash", trusted=True)
    await agent.open(resume=False)
    assert agent.session_id == "canonical-id"

    async def on_state_change(changed):
        changed.states.append(changed.state)

    await agent.start("hello", on_state_change, resume=False)
    await _wait_for(lambda: agent.pending_requests)
    request = agent.pending_requests[0]
    assert request.request_type == "tool_approval"
    assert request.tool_input["locations"] == ["/tmp/out.txt"]
    assert [option["optionId"] for option in request.tool_input["options"]] == [
        "proceed_always", "proceed_once", "cancel"]
    assert agent.resolve_pending_request(request.request_id, {"option_id": "proceed_once"})

    await _wait_for(lambda: agent.state == AgentState.USER_TURN)
    assert AgentState.ASSISTANT_TURN in agent.states
    kinds = [(event["type"], event.get("block_type")) for event in agent.events]
    assert ("stream_block_start", "thinking") in kinds
    assert ("stream_block_start", "text") in kinds
    assert kinds[-1] == ("stream_block_end", "text")
    deltas = [event["text"] for event in agent.events if event["type"] == "stream_block_delta"]
    assert deltas == ["**Plan**\nthink", "Done."]

    log = fake_cli.read_text().replace(" ", "")
    assert '"modeId":"autoEdit"' in log
    assert '"optionId":"proceed_once"' in log

    await agent.interrupt_or_kill("manual")
    assert agent.state == AgentState.DEAD
    assert agent._connection.returncode is not None


@pytest.mark.asyncio
async def test_resume_ignores_the_history_replay_and_switches_model(fake_cli, tmp_path):
    agent = _Recorder("canonical-id", "project", str(tmp_path), _settings(), model="gemini-3.8-flash", trusted=False)
    await agent.open(resume=True)
    assert agent.session_id == "canonical-id"
    assert agent.events == []  # the ``session/load`` replay is not streamed
    await agent.apply_agent_settings(_settings(selected_model="gemini-pro"), model="gemini-3.1-pro-preview")
    await agent.interrupt_or_kill("manual")
    log = fake_cli.read_text().replace(" ", "")
    assert '"method":"session/load"' in log
    assert '"modelId":"gemini-3.1-pro-preview"' in log


@pytest.mark.asyncio
async def test_missing_cli_is_reported(monkeypatch, tmp_path):
    from twicc.agent.exceptions import SendDeliveryError

    monkeypatch.setenv(GEMINI_BIN_ENV, os.fspath(tmp_path / "nowhere" / "gemini"))
    agent = _Recorder("draft", "project", str(tmp_path), _settings(), model=None, trusted=True)
    with pytest.raises(SendDeliveryError) as excinfo:
        await agent.open(resume=False)
    assert excinfo.value.ui_message["key"] == "backend.gemini.cliNotFound"
