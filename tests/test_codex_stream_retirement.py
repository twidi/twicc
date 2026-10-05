"""Codex completion events and durable transport keep native item identities."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.core.serializers import serialize_session_item
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.sessions_watcher import get_session_items


def _agent(*, ephemeral=False):
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "retirement-parent"
    agent.ephemeral = ephemeral
    agent._items_by_id = {}
    agent._active_tools = {}
    agent._reasoning_summary_indices = {}
    agent._ephemeral_has_final_answer = False
    agent.ephemeral_final_text = ""
    agent._broadcast_stream_event = AsyncMock()
    return agent


def _completion(item_type, *, thread_id="retirement-parent"):
    return SimpleNamespace(method="item/completed", payload=SimpleNamespace(
        thread_id=thread_id,
        item=SimpleNamespace(type=item_type, id="native-item", text="Final answer", phase="final_answer"),
    ))


def _assert_completion(agent, block_type):
    events = [call.args[0] for call in agent._broadcast_stream_event.call_args_list]
    common = {"session_id": agent.session_id, "message_id": "native-item",
              "block_index": 0, "block_type": block_type}
    assert events == [{"type": "stream_block_stop", **common},
                      {"type": "stream_block_end", **common, "uuid": "native-item"}]


def test_completed_text_preserves_stream_end_identity():
    agent = _agent()
    asyncio.run(agent._handle_stream_event(_completion("agentMessage")))
    _assert_completion(agent, "text")


def test_reasoning_parts_complete_as_one_block():
    agent = _agent()

    async def run():
        for index in range(3):
            await agent._handle_stream_event(SimpleNamespace(
                method="item/reasoning/summaryPartAdded",
                payload=SimpleNamespace(thread_id=agent.session_id, item_id="native-item", summary_index=index),
            ))
        events = [call.args[0] for call in agent._broadcast_stream_event.call_args_list]
        assert [event["type"] for event in events] == ["stream_block_start", "stream_block_delta", "stream_block_delta"]
        assert all(event["block_index"] == 0 for event in events)
        agent._broadcast_stream_event.reset_mock()
        await agent._handle_stream_event(_completion("reasoning"))

    asyncio.run(run())
    _assert_completion(agent, "thinking")
    assert agent._reasoning_summary_indices == {}


def test_child_completion_does_not_paint_parent_streaming():
    agent = _agent()
    asyncio.run(agent._handle_stream_event(_completion("agentMessage", thread_id="retirement-child")))
    agent._broadcast_stream_event.assert_not_called()


def test_ephemeral_completion_remains_private():
    agent = _agent(ephemeral=True)
    asyncio.run(agent._handle_stream_event(_completion("agentMessage")))
    assert agent.ephemeral_final_text == "Final answer"
    assert agent._ephemeral_has_final_answer is True
    agent._broadcast_stream_event.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_persisted_transport_preserves_durable_ids_without_an_agent():
    project = Project.objects.create(id="retirement-project")
    session = Session.objects.create(id="retirement-transport", project=project, provider=Provider.CODEX)
    rows = []
    for line_num, item_type, kind in [(1, "AgentMessage", ItemKind.ASSISTANT_MESSAGE),
                                      (2, "reasoning", ItemKind.REASONING)]:
        item = {"type": item_type, "id": f"durable-{line_num}"}
        if item_type == "AgentMessage":
            item["content"] = [{"type": "Text", "text": "Answer"}]
        else:
            item["summary"] = [{"type": "summary_text", "text": "Reason"}]
        record = ({"type": "event_msg", "payload": {"type": "item_completed", "item": item}}
                  if item_type == "AgentMessage" else {"type": "response_item", "payload": item})
        rows.append(SessionItem.objects.create(session=session, line_num=line_num, kind=kind,
                                               content=orjson.dumps(record).decode()))
    expected = [serialize_session_item(row) for row in rows]
    actual = asyncio.run(get_session_items(session, [2, 1]))
    assert actual == expected
    for line_num, payload in enumerate(actual, 1):
        record = orjson.loads(payload["content"])
        durable_item = record["payload"]["item"] if line_num == 1 else record["payload"]
        assert durable_item["id"] == f"durable-{line_num}"
        assert "stream_uuid" not in payload
