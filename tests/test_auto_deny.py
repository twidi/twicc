"""Auto-deny of unanswered approval requests: the base timer and the wire field.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md`` (§4).
"""

import asyncio
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from twicc.agent import base_agent as base_agent_module
from twicc.agent.auto_deny import (
    AUTO_DENY_DELAY_SECONDS,
    AUTO_DENY_MESSAGE,
    AUTO_DENY_TOOLS,
    auto_deny_remaining,
)
from twicc.agent.base_agent import BaseAgent
from twicc.agent.states import AgentInfo, AgentState, PendingRequest, serialize_agent_info
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

# Stands for the provider's deny value: the base class never looks inside it.
DENY = object()


class _Agent(BaseAgent):
    """Minimal concrete agent — the timer lives on the base class."""

    provider = Provider.CLAUDE_CODE

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _make_agent():
    return _Agent(session_id="s-1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


def _request(request_id="r-1", *, created_at=None):
    return PendingRequest(
        request_id=request_id,
        request_type="tool_approval",
        tool_name="Bash",
        tool_input={"command": "rm -rf /tmp/x"},
        created_at=time.time() if created_at is None else created_at,
    )


@pytest.fixture
def fast_timer(monkeypatch):
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 0.2)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_the_message_names_the_delay_in_minutes():
    assert AUTO_DENY_DELAY_SECONDS == 120
    assert "within 2 minutes" in AUTO_DENY_MESSAGE
    assert "find another way" in AUTO_DENY_MESSAGE


def test_the_allow_list_holds_only_action_tools():
    assert AUTO_DENY_TOOLS == {"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def test_remaining_is_none_when_unarmed_and_floored_at_zero():
    assert auto_deny_remaining(_request(), time.time()) is None
    armed = replace(_request(), auto_deny_at=1000.0)
    assert auto_deny_remaining(armed, 940.04) == 60.0
    assert auto_deny_remaining(armed, 1005.0) == 0.0


# ---------------------------------------------------------------------------
# The base timer
# ---------------------------------------------------------------------------


def test_an_unanswered_armed_request_resolves_with_the_deny_value(fast_timer):
    agent = _make_agent()

    result = asyncio.run(agent._await_pending_request(_request(), auto_deny_response=DENY))

    assert result is DENY
    assert agent.pending_requests == ()
    assert agent.last_pending_resolved_at > 0
    assert agent._auto_deny_timers == {}


def test_an_armed_request_carries_its_deadline_and_an_answer_wins(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0)
        (pending,) = agent.pending_requests
        assert pending.auto_deny_at == pytest.approx(request.created_at + 0.2)
        assert request.request_id in agent._auto_deny_timers
        assert agent.resolve_pending_request(request.request_id, "allow")
        return await task

    assert asyncio.run(run()) == "allow"
    assert agent._auto_deny_timers == {}


def test_an_unarmed_request_has_no_deadline_and_no_timer():
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request))
        await asyncio.sleep(0)
        (pending,) = agent.pending_requests
        assert pending.auto_deny_at is None
        assert agent._auto_deny_timers == {}
        agent.resolve_pending_request(request.request_id, "allow")
        return await task

    assert asyncio.run(run()) == "allow"


def test_a_deadline_already_passed_denies_at_once():
    agent = _make_agent()
    request = _request(created_at=time.time() - AUTO_DENY_DELAY_SECONDS - 1)

    assert asyncio.run(agent._await_pending_request(request, auto_deny_response=DENY)) is DENY


def test_the_check_reschedules_until_the_deadline(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0.05)
        assert not task.done()
        assert request.request_id in agent._auto_deny_timers
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(run()) is DENY


def test_a_wall_clock_jump_denies_at_the_next_check(monkeypatch):
    # A machine suspend does not advance the event-loop clock: only the
    # wall-clock re-check can see that the deadline has passed.
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 100)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0.05)
        assert not task.done()
        jumped = time.time() + 200
        monkeypatch.setattr(base_agent_module, "time", SimpleNamespace(time=lambda: jumped))
        return await asyncio.wait_for(task, 1)

    assert asyncio.run(run()) is DENY


def test_a_cancelled_wait_leaves_no_timer(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0)
        agent._cancel_all_pending_futures()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent._auto_deny_timers == {}
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert agent.pending_requests == ()


def test_a_check_after_an_answer_is_a_no_op(fast_timer):
    agent = _make_agent()

    async def run():
        future = asyncio.get_running_loop().create_future()
        future.set_result("allow")
        armed = replace(_request(), auto_deny_at=time.time() - 1)
        agent._check_auto_deny(armed, future, DENY)
        assert future.result() == "allow"
        assert agent._auto_deny_timers == {}

    asyncio.run(run())


def test_parallel_requests_keep_their_own_timers(fast_timer):
    agent = _make_agent()
    first, second = _request("r-1"), _request("r-2")

    async def run():
        first_task = asyncio.create_task(agent._await_pending_request(first, auto_deny_response=DENY))
        second_task = asyncio.create_task(agent._await_pending_request(second, auto_deny_response=DENY))
        await asyncio.sleep(0)
        assert set(agent._auto_deny_timers) == {"r-1", "r-2"}
        agent.resolve_pending_request("r-1", "allow")
        assert await first_task == "allow"
        assert set(agent._auto_deny_timers) == {"r-2"}
        return await asyncio.wait_for(second_task, 2)

    assert asyncio.run(run()) is DENY
    assert agent._auto_deny_timers == {}


# ---------------------------------------------------------------------------
# The process-state payload
# ---------------------------------------------------------------------------


def _info(*pending):
    now = time.time()
    return AgentInfo(
        session_id="s-1", project_id="-p", provider=Provider.CLAUDE_CODE,
        state=AgentState.ASSISTANT_TURN, previous_state=None,
        started_at=now, state_changed_at=now, last_activity=now,
        pending_requests=pending,
    )


def test_the_payload_carries_the_remaining_seconds_only_when_armed():
    armed = replace(_request("armed"), auto_deny_at=time.time() + 60)
    plain = _request("plain")

    entries = {e["request_id"]: e for e in serialize_agent_info(_info(armed, plain))["pending_requests"]}

    assert 59 <= entries["armed"]["auto_deny_in_seconds"] <= 60
    assert "auto_deny_in_seconds" not in entries["plain"]


def test_a_passed_deadline_is_sent_as_zero():
    late = replace(_request(), auto_deny_at=time.time() - 5)

    (entry,) = serialize_agent_info(_info(late))["pending_requests"]

    assert entry["auto_deny_in_seconds"] == 0.0
