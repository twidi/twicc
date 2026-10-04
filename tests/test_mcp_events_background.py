"""Background work preserves the existing detector's ignored-message contract."""

from datetime import UTC, datetime

import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_mcp_events_turns import agent, opened, pending, prompt, stopped_ticks
from twicc.agent.states import AgentState
from twicc.mcp.events.runtime import CursorWrite

pytestmark = [pytest.mark.django_db, pytest.mark.parametrize("env", ["claude_code", "codex"], indirect=True)]
env = test_mcp_events_turns.env


@pytest.fixture
def background(env):
    env.monitor.arguments = {"session_id": env.session.id, "wait_background": True}
    env.monitor.wait = env.runtime._new_wait(env.monitor)
    opened(env)
    agent(env, at=999, background_work_in_progress={"shells": 1})
    append_assistant(env.session, 2, "Interim", timestamp=datetime.fromtimestamp(999, UTC))
    env.tick()
    assert env.monitor.wait.last_ignored.line_num == 2
    assert env.emissions == []
    return env


def test_ignored_final_then_final_without_background_delivers_only_second(background):
    env = background
    agent(env, at=999)
    append_assistant(env.session, 4, "Real answer")
    env.tick()
    assert len(env.emissions) == 1
    assert env.payloads()[0]["data"]["reply"]["text"] == "Real answer"
    assert env.monitor.cursor_line == 4


def test_idle_background_then_reopened_turn_resumes_after_ignored_final(background):
    env = background
    agent(env, state=AgentState.USER_TURN, background_work_in_progress={"shells": 1})
    stopped_ticks(env)
    assert not env.emissions
    env.clock.advance(1)
    agent(env, previous_state=AgentState.USER_TURN)
    env.tick()
    assert env.monitor.cursor_line == env.monitor.turn_start_line == 2
    assert env.monitor.turn_opened_by == "transition"
    assert env.monitor.wait.last_ignored is None
    assert any(isinstance(write, CursorWrite) and write.cursor_at is None for write in env.writes)
    append_assistant(env.session, 4, "Reopened answer")
    env.tick()
    assert len(env.emissions) == 1
    assert env.payloads()[0]["data"]["reply"]["text"] == "Reopened answer"


def test_work_ends_without_answer_stays_silent_then_new_crash_emits(background):
    env = background
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    env.clock.advance(360)
    env.tick()
    assert not env.emissions and env.monitor.wait.last_ignored is not None
    env.clock.advance(1)
    agent(env)
    prompt(env, 3)
    env.tick()
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    reply = env.payloads()[0]["data"]["reply"]
    assert reply["outcome"] == "ended" and reply["line_num"] is None and "text" not in reply


def test_agent_death_delivers_ignored_message_with_tick_time_and_closes(background):
    env = background
    env.registry.remove_agent(env.session.id)
    stopped_ticks(env)
    payload = env.payloads()[0]
    assert payload["data"]["reply"]["outcome"] == "ended"
    assert payload["data"]["reply"]["text"] == "Interim"
    assert payload["timestamp"] == env.clock.utcnow().isoformat()
    assert env.monitor.cursor_at == env.clock.epoch() and not env.monitor.turn_open
    stopped_ticks(env)
    assert len(env.emissions) == 1


def test_pseudo_turn_during_ignored_state_closes_old_work_before_guard(background):
    env = background
    # This old prompt must not qualify the new pseudo-turn.
    prompt(env, 1)
    agent(env, state=AgentState.USER_TURN, background_work_in_progress={"shells": 1})
    env.tick()
    env.clock.advance(1)
    agent(env, background_work_in_progress={"shells": 1})
    env.tick()
    assert env.monitor.turn_start_line == env.monitor.cursor_line == 2
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert not env.emissions and not env.monitor.turn_open


def test_reopened_turn_question_then_stop_emits_awaiting_and_ended(background):
    env = background
    env.clock.advance(1)
    agent(env, pending_requests=(pending("question", env.clock.epoch()),))
    env.tick()
    assert env.monitor.turn_opened_by == "awaiting"
    agent(env, state=AgentState.USER_TURN)
    stopped_ticks(env)
    assert [p["data"]["reply"]["outcome"] for p in env.payloads()] == ["awaiting_user_input", "ended"]


def test_new_turn_when_closed_uses_ignored_line_in_guard_reference(background):
    env = background
    # An awaited request can be followed by another conclusion while the same
    # wait holds an ignored message. Exercise the closed-turn branch explicitly.
    env.monitor.turn_open = False
    env.monitor.turn_start_line = 1
    env.clock.advance(1)
    agent(env)
    env.tick()
    assert env.monitor.turn_start_line == 2
    assert env.monitor.cursor_line == 2 and env.monitor.wait.last_ignored is None
