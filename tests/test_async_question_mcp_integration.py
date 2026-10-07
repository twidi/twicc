"""Optional questions preserve MCP conclusions and history replacement contracts."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import orjson
import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_async_question_integration import canonical_question, recompute, source_user
from tests.test_mcp_events_turns import agent, opened, pending, stopped_ticks
from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AsyncQuestionState, Session, SessionItem
from twicc.core.services import async_questions as service
from twicc.mcp import events
from twicc.mcp.events.runtime import RebaseWrite
from twicc.providers.codex.async_questions import QuestionFact, extract_async_question_facts
from twicc.providers.codex.rollout_migration import (
    ReplaceCodexHistoryJob,
    _begin_replace_codex_history,
    _finish_replace_codex_history,
    _insert_replace_codex_history_chunk,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.parametrize("env", ["codex"], indirect=True)]
env = test_mcp_events_turns.env


def append_question(env):
    value = canonical_question()
    value["timestamp"] = env.clock.utcnow().isoformat()
    value["payload"]["thread_id"] = env.session.id
    SessionItem.objects.create(session=env.session, line_num=1, kind=ItemKind.ASSISTANT_MESSAGE,
                               timestamp=env.clock.utcnow(), content=orjson.dumps(value).decode())
    Session.objects.filter(pk=env.session.pk).update(last_line=1)
    service.merge_question_facts(env.session.id, extract_async_question_facts(value, line=1))
    return value


def test_async_question_then_tools_then_final_emits_one_reply(env):
    opened(env)
    agent(env)
    append_question(env)
    env.tick()
    assert env.emissions == []
    assert env.monitor.wait.last_message.is_final is False
    assert service.read_question_snapshot(env.session.id)["batches"][0]["status"] == "ready"
    SessionItem.objects.create(session=env.session, line_num=2, kind=ItemKind.TOOL_USE,
                               content='{"type":"response_item","payload":{"type":"function_call",'
                                       '"call_id":"tool-1","name":"exec_command","arguments":"{}"}}')
    Session.objects.filter(pk=env.session.pk).update(last_line=2)
    env.tick()
    assert env.emissions == []
    append_assistant(env.session, 3, "Actual final answer", timestamp=datetime.fromtimestamp(1001, UTC))
    env.tick()
    assert [body["data"]["reply"]["outcome"] for body in env.payloads()] == ["replied"]
    assert env.payloads()[0]["data"]["reply"]["is_final"] is True
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert len(env.emissions) == 1


def test_async_only_control_return_releases_questions_then_emits_ended(env):
    opened(env)
    agent(env)
    value = append_question(env)
    env.tick()
    assert env.emissions == []
    service.merge_question_facts(env.session.id, [QuestionFact(
        "return:parent", "control_return", value["timestamp"], None, None, None,
        {"group_id": "parent", "turn_ids": ["t1"], "outcome": "completed"},
    )])
    assert service.read_question_snapshot(env.session.id)["batches"][0]["status"] == "ready"
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    reply = env.payloads()[0]["data"]["reply"]
    assert reply["outcome"] == "ended"
    assert reply["is_final"] is False
    assert len(env.emissions) == 1


def test_async_question_keeps_blocking_request_schema(env):
    opened(env)
    append_question(env)
    agent(env, state=AgentState.USER_TURN, pending_requests=(pending("approval"),))
    env.tick()
    reply = env.payloads()[0]["data"]["reply"]
    assert reply["outcome"] == "awaiting_user_input"
    assert env.payloads()[0]["data"]["request_type"] == "tool_approval"
    assert service.read_question_snapshot(env.session.id)["batches"][0]["status"] == "ready"


def test_history_replacement_preserves_question_decisions_until_mcp_adopts_new_epoch(env, monkeypatch):
    from tests.test_codex_async_question_lifecycle import record

    records = [canonical_question(), record("task_complete", second=2),
               canonical_question(1, turn="t2", second=3), record("task_complete", turn="t2", second=4)]
    for line, value in enumerate(records, 1):
        SessionItem.objects.create(session=env.session, line_num=line, content=orjson.dumps(value).decode())
    Session.objects.filter(pk=env.session.pk).update(last_line=len(records))
    recompute(env.session)
    dismissed_id = records[0]["payload"]["item"]["id"]
    sent_id = records[2]["payload"]["item"]["id"]
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 10, 5, 9, 12, 2, 500000, tzinfo=UTC)))
    service.dismiss_question_batch(env.session.id, dismissed_id, request_id="dismiss-1")
    prepared = service.prepare_question_send(env.session.id, "Natural answer", None,
                                            request_id="native-send-1", origin="human", at="2026-10-05T09:12:10Z")
    service.accept_question_send(env.session.id, {**prepared.submission, "source_item_id": "u1",
                                                "target_turn_id": "reply", "delivery_route": "start"})
    source = source_user(prepared.text, request_id="native-send-1", second=10)
    records.append(source)
    service.merge_question_facts(env.session.id, extract_async_question_facts(source, line=5))
    service.merge_question_facts(env.session.id, [QuestionFact(
        "owner:pending", "live_owner", "2026-10-05T09:12:11Z", None, None, None,
        {"group_id": "pending", "root_turn_id": None, "state": "pending"},
    )])
    before = deepcopy(AsyncQuestionState.objects.get(session=env.session).state)
    monkeypatch.setattr(events, "_runtime", env.runtime)
    replacement = [(line, orjson.dumps(value).decode()) for line, value in enumerate(records, 11)]
    job = ReplaceCodexHistoryJob(Provider.CODEX, env.session.id, replacement, 2000, 15, 42.0, None)
    _begin_replace_codex_history(job)
    env.runtime._drain_commands()
    env.runtime._tick()
    assert env.monitor.pending_rebase and env.emissions == env.writes == []
    _insert_replace_codex_history_chunk(env.session.id, replacement)
    _finish_replace_codex_history(job)
    env.runtime._tick()
    assert env.monitor.pending_rebase and env.emissions == env.writes == []
    snapshot = recompute(env.session)
    after = AsyncQuestionState.objects.get(session=env.session).state
    assert snapshot["resolutions"] == {
        dismissed_id: {"status": "dismissed", "request_id": "dismiss-1"},
        sent_id: {"status": "sent", "request_id": "native-send-1"},
    }
    assert after["facts"][f"question:{dismissed_id}"]["line"] == 11
    assert after["facts"][f"question:{sent_id}"]["line"] == 13
    assert after["facts"]["send:native-send-1"]["data"] == before["facts"]["send:native-send-1"]["data"]
    assert after["facts"]["owner:pending"] == before["facts"]["owner:pending"]
    env.runtime._tick()
    assert env.emissions == []
    assert isinstance(env.writes[-1], RebaseWrite)
    assert (env.monitor.numbering, env.monitor.cursor_line, env.monitor.initial_last_line) == (1, 15, 15)
    assert not env.monitor.pending_rebase
