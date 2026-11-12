"""Sanitized reference playback through production decisions and completion."""

import asyncio
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import orjson
import pytest
from django.utils import timezone

from tests.test_completion_publication import bridge
from tests.test_timeline_messages import live_session
from twicc import timeline_classification, timeline_decision_task
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import LogicalTurn, ProcessRun, TimelineDecision, TimelineEvent, TimelineKind
from twicc.core.services.logical_turn_answers import resolve_logical_answer
from twicc.request_origin import UI_ORIGIN

# Export imported fixtures for pytest discovery.
__all__ = ['bridge', 'live_session']


pytestmark = pytest.mark.django_db(transaction=True)
PARIS = ZoneInfo("Europe/Paris")
FIXTURE = Path(__file__).parent / "fixtures" / "timeline_semantic_decisions.json"


class Playback:
    """Drive indexed sources, the existing controller, and the real worker."""

    def __init__(self, source, manager, agent, clock, responses, calls):
        self.source, self.manager, self.agent = source, manager, agent
        self.clock, self.responses, self.calls = clock, responses, calls
        self.shells = []
        self.runtime = None

    async def drain(self):
        self.agent._cancel_completion_timer()
        while self.agent._completion_tasks:
            await asyncio.gather(*tuple(self.agent._completion_tasks))
            await asyncio.sleep(0)

    async def start(self, entry):
        self.clock[0] = datetime.fromisoformat(entry["occurred_at"])
        await self.append("request", entry["text"], user=True)
        await timeline_decision_task._admitted(self.source.session.refresh_from_db)
        self.agent._completion_storage_incarnation = self.source.session.storage_incarnation
        self.agent._completion_history_epoch = self.source.session.history_epoch
        self.agent._completion_activity(first_ref=NativeMessageRef("claude_uuid", "request"))
        await self.drain()
        self.runtime = timeline_decision_task.DecisionRuntime()
        return self.agent.completion_controller.snapshot().turn_id

    async def append(self, identity, text, *, user=False):
        record = self.source.message(identity, text, user=user)
        record["timestamp"] = self.clock[0].isoformat()
        await timeline_decision_task._admitted(self.source.append, record,
            since=datetime(2026, 11, 9, tzinfo=PARIS))

    async def infer(self, response):
        self.responses.append(response)
        row, skipped = await timeline_decision_task._claim_ready(self.clock[0], set(), set())
        assert row is not None and skipped == []
        await self.runtime.worker(row)
        assert not self.responses, "The production classifier must consume the fake response"
        return await timeline_decision_task._admitted(TimelineDecision.objects.get, pk=row.pk)

    async def message(self, entry, *, resume=True):
        self.clock[0] = datetime.fromisoformat(entry["occurred_at"])
        if resume:
            self.agent._completion_activity()
        await self.drain()
        await self.append(entry["source_case"], entry["text"])
        native = NativeMessageRef("claude_uuid", entry["source_case"])
        self.agent._timeline_observe_message(native, role="assistant")
        self.agent.completion_controller.provider_ended(0, final_ref=native)
        self.clock[0] += timedelta(seconds=1)
        return await self.infer(entry["model_response"])

    async def ending(self, entry):
        self.clock[0] = datetime.fromisoformat(entry["occurred_at"])
        shell = entry.get("shell")
        self.shells[:] = [] if shell is None else [{
            "key": shell["key"], "owner": "root", "command": shell["command"],
            "description": "Required verification", "started_at": shell["started_at"],
            "start_line": None, "owner_ref": None, "generation": 1,
        }]
        candidate = self.agent.completion_controller.candidate()
        assert candidate is not None
        await self.manager._confirm_completion(self.agent, candidate)
        await self.drain()
        if shell is not None:
            return await self.infer(entry["model_response"])

    async def close(self):
        await self.drain()
        await self.runtime.stop()


@pytest.fixture
def playback(bridge, live_session, monkeypatch):
    source = live_session
    manager, agent = bridge
    source.session.refresh_from_db()
    agent.session_id, agent.project_id = source.session.pk, source.session.project_id
    agent.provider = manager.provider = Provider.CLAUDE_CODE
    agent.process_run.session_id, agent.process_run.provider = source.session.pk, source.provider
    agent.process_run.save(update_fields=["session_id", "provider"])
    manager._agents = {source.session.pk: agent}
    agent._completion_storage_incarnation = source.session.storage_incarnation
    agent._completion_history_epoch = source.session.history_epoch
    manager._bind_completion_callbacks(agent)
    clock = [datetime(2026, 11, 9, 10, 36, tzinfo=PARIS)]
    monkeypatch.setattr(timezone, "now", lambda: clock[0])
    responses, calls = deque(), []

    async def offline_route(route, system_text, user_json, *, deadline):
        assert system_text in (timeline_classification.MESSAGE_SYSTEM_PROMPT,
                               timeline_classification.ENDING_SYSTEM_PROMPT)
        calls.append(orjson.loads(user_json))
        response = responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    # Replace only the external inference boundary. Keep context, parser, source
    # validation, claims, result guards, publication and final forcing intact.
    monkeypatch.setattr(timeline_classification, "_route_available", lambda route: route == "haiku")
    monkeypatch.setattr(timeline_classification, "_run_route", offline_route)
    monkeypatch.setattr(timeline_decision_task, "_live_ending", lambda row: (manager, agent)
        if agent._completion_pending_ending is not None
        and agent._completion_pending_ending.id == str(row.pk) else None)
    result = Playback(source, manager, agent, clock, responses, calls)
    monkeypatch.setattr(agent, "completion_shell_snapshot", lambda: tuple(result.shells))
    return result


def reference():
    return orjson.loads(FIXTURE.read_bytes())


def assistant_events():
    return list(TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).order_by("id"))


@pytest.mark.parametrize("live_session", ["claude_code"], indirect=True)
def test_reference_progress_is_suppressed_and_final_preserves_original_time(playback):
    data = reference()
    progress, final = data["entries"][:2]

    async def scenario():
        await playback.start(data["turn_inputs"][0])
        row = await playback.message(progress)
        assert row.status == "rejected" and row.effective_route == "haiku"
        assert await timeline_decision_task._admitted(assistant_events) == []
        row = await playback.message(final)
        assert row.status == "accepted" and row.effective_route == "haiku"
        await playback.close()

    asyncio.run(scenario())
    events = assistant_events()
    assert [event.data["text"] for event in events] == [final["text"]]
    assert events[0].occurred_at == datetime.fromisoformat("2026-11-09T10:40:23.104000+01:00")


@pytest.mark.parametrize("live_session", ["claude_code"], indirect=True)
def test_reference_shell_checks_remain_one_logical_turn_until_1109(playback):
    data = reference()

    async def scenario():
        turn_id = await playback.start(data["turn_inputs"][1])
        rejected = []
        for entry in data["entries"][2:]:
            if entry["kind"] == "message":
                await playback.message(entry)
            else:
                row = await playback.ending(entry)
                rejected.append(row)
                assert (row.status, row.effective_route) == ("rejected", "haiku")
                turn = await timeline_decision_task._admitted(LogicalTurn.objects.get, pk=turn_id)
                assert turn.closed_at is None and turn.outcome is None
                assert (await timeline_decision_task._admitted(resolve_logical_answer, turn, cursor=0)).status == "pending"
                process = await timeline_decision_task._admitted(ProcessRun.objects.get, pk=playback.agent.process_run.pk)
                assert process.state == "assistant_turn"
                assert playback.agent.state == AgentState.ASSISTANT_TURN
                playback.manager._broadcast_info.assert_not_awaited()
                assert not await timeline_decision_task._admitted(
                    lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
        assert [row.occurred_at.astimezone(PARIS).strftime("%H:%M") for row in rejected] == [
            "10:56", "11:01", "11:03", "11:08"]
        # The final message has no remaining shell. Existing normal completion
        # skips shell inference and forces the exact final source if necessary.
        await playback.ending({"occurred_at": "2026-11-09T11:09:14.174000+01:00"})
        assert playback.agent.state == AgentState.USER_TURN
        playback.manager._broadcast_info.assert_awaited_once()
        await playback.close()
        return turn_id

    turn_id = asyncio.run(scenario())
    assert LogicalTurn.objects.count() == 1
    turn = LogicalTurn.objects.get(pk=turn_id)
    assert turn.opened_at == datetime.fromisoformat("2026-11-09T10:55:52.948000+01:00")
    assert turn.closed_at == datetime.fromisoformat("2026-11-09T11:09:14.174000+01:00")
    assert turn.outcome == "normal"
    assert TimelineEvent.objects.filter(kind=TimelineKind.TURN_STARTED).count() == 1
    assert TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count() == 1
    events = assistant_events()
    assert [event.data["text"] for event in events] == [data["entries"][-1]["text"]]
    assert events[0].occurred_at == datetime.fromisoformat("2026-11-09T11:09:13.174000+01:00")
    assert len(playback.calls) == 9  # Five messages and four shell decisions.


def synthetic_message(response="YES\nA useful answer during work."):
    return {"source_case": "synthetic-answer", "occurred_at": "2026-11-09T10:56:00+01:00",
            "text": "The timeout applies to each test. Verification continues.", "model_response": response}


def synthetic_ending():
    return {"occurred_at": "2026-11-09T10:56:02+01:00",
            "shell": {"key": "verify", "command": "run-test-suite --verify",
                      "started_at": "2026-11-09T09:55:59Z"},
            "model_response": "NO\nRequired verification still runs."}


@pytest.mark.parametrize("live_session", ["claude_code"], indirect=True)
def test_useful_midwork_answer_publishes_while_shell_ending_is_rejected(playback):
    async def scenario():
        await playback.start(reference()["turn_inputs"][1])
        await playback.message(synthetic_message())
        await playback.ending(synthetic_ending())
        assert playback.agent.completion_controller.is_open
        playback.manager._broadcast_info.assert_not_awaited()
        await playback.close()

    asyncio.run(scenario())
    assert [event.data["text"] for event in assistant_events()] == [synthetic_message()["text"]]
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()


@pytest.mark.parametrize("live_session", ["claude_code"], indirect=True)
@pytest.mark.parametrize("winner", ["user", "root", "already_rejected"])
def test_synthetic_input_races_keep_existing_turn_ownership(playback, winner):
    async def scenario():
        turn_id = await playback.start(reference()["turn_inputs"][1])
        # A rejected message can still become the exact final message after a
        # user wins. Message publication and ending resolution are independent.
        await playback.message(synthetic_message("NO\nProgress only."))
        ending = synthetic_ending()
        playback.clock[0] = datetime.fromisoformat(ending["occurred_at"])
        playback.shells[:] = [{"key": "verify", "owner": "root", "command": "run-test-suite --verify",
            "description": "Required verification", "started_at": "2026-11-09T09:55:59Z",
            "start_line": None, "owner_ref": None, "generation": 1}]
        await playback.manager._confirm_completion(playback.agent, playback.agent.completion_controller.candidate())
        context = playback.agent._completion_pending_ending
        await playback.drain()
        if winner == "root":
            playback.agent._completion_activity()
        elif winner == "already_rejected":
            await playback.infer(ending["model_response"])
        token = str(uuid4())
        playback.agent._timeline_input_origins[token] = UI_ORIGIN
        playback.agent._completion_input_pending(token, staged=winner == "user")
        if winner == "user":
            assert playback.agent.completion_controller.snapshot().outcome == "normal"
            playback.agent._completion_input_pending(token)
            playback.agent._completion_activity()
            assert playback.agent._completion_inputs[token].turn_id != turn_id
        else:
            assert playback.agent.completion_controller.is_open
            assert playback.agent._completion_inputs[token].turn_id == turn_id
        playback.agent._completion_input_settled(token, "rejected")
        await playback.drain()
        if winner == "user" and turn_id in playback.agent._completion_pending_publications:
            await playback.manager._retry_completion(playback.agent,
                playback.agent._completion_pending_publications[turn_id])
        row = await timeline_decision_task._admitted(TimelineDecision.objects.get, pk=context.id)
        assert (row.status, row.reason) == {
            "user": ("accepted", "user_input"), "root": ("canceled", "root_activity"),
            "already_rejected": ("rejected", "Required verification still runs."),
        }[winner]
        turn = await timeline_decision_task._admitted(LogicalTurn.objects.get, pk=turn_id)
        assert turn.closed_at == (context.occurred_at if winner == "user" else None)
        await playback.close()

    asyncio.run(scenario())
    assert [event.data["text"] for event in assistant_events()] == (
        [synthetic_message()["text"]] if winner == "user" else [])


@pytest.mark.parametrize("live_session", ["claude_code"], indirect=True)
@pytest.mark.parametrize("kind", ["message", "ending"])
def test_synthetic_inference_timeout_fails_open_through_normal_publication(playback, kind):
    async def scenario():
        await playback.start(reference()["turn_inputs"][1])
        message = synthetic_message()
        if kind == "message":
            message["model_response"] = TimeoutError()
            row = await playback.message(message)
        else:
            await playback.message(synthetic_message("NO\nProgress only."))
            ending = synthetic_ending()
            ending["model_response"] = TimeoutError()
            row = await playback.ending(ending)
            assert playback.agent.state == AgentState.USER_TURN
            playback.manager._broadcast_info.assert_awaited_once()
        assert row.status == "accepted" and row.reason == "timeout" and row.effective_route is None
        await playback.close()

    asyncio.run(scenario())
    assert [event.data["text"] for event in assistant_events()] == [synthetic_message()["text"]]
    event = assistant_events()[0]
    assert event.occurred_at == datetime.fromisoformat(synthetic_message()["occurred_at"])
