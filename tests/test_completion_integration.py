"""Synthetic provider transports exercise durable completion and public waiting.

The three Claude timings contain event offsets only. All answer text is synthetic.
No provider process, provider home, production database, or live model is used.
"""

import asyncio
import logging
from copy import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from claude_agent_sdk import SystemMessage, StreamEvent, TextBlock
from django.utils import timezone

from tests import test_claude_completion as claude
from tests import test_codex_completion as codex
from tests import test_logical_turn_answers as answers
from tests.test_completion_publication import Manager
from twicc.agent.completion import CompletionController
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.cli import _wait_reply
from twicc.core.models import LogicalTurn, ProcessRun, TimelineEvent, TimelineKind

pytestmark = pytest.mark.django_db(transaction=True)
answer_fixture = answers.answer_fixture
content_row_factory = answers.content_row_factory


@pytest.fixture
def integrated(answer_fixture, monkeypatch):
    from twicc import external_notifications
    from twicc.agent import registry
    from twicc.asgi import broadcast_process_state
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WriterCoordinator, WriteResources

    f = answer_fixture
    agent = claude.make_agent() if f.provider == "claude_code" else codex.make_agent()
    agent.session_id = f.session.id
    agent.project_id = f.session.project_id
    agent.ephemeral = False
    agent.get_pid = lambda: None
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch
    now = timezone.now()
    agent.process_run = ProcessRun.objects.create(
        session_id=f.session.id, provider=f.provider, started_at=now,
        last_state_change_at=now, state="assistant_turn",
    )
    manager = Manager()
    manager._agents[f.session.id] = agent
    manager._flush_pending_title = AsyncMock()
    manager._update_session_stopped_at = AsyncMock()
    frames, notifications = [], []

    async def group_send(group, event):
        frames.append(event["data"])

    manager._broadcast_info = AsyncMock(wraps=Manager._broadcast_info.__get__(manager))
    manager._broadcast_callback = broadcast_process_state
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("twicc.asgi.get_channel_layer", lambda: layer)
    monkeypatch.setattr("twicc.agent.base_agent.get_channel_layer", lambda: layer)
    monkeypatch.setattr(external_notifications, "read_synced_settings", lambda: {
        "externalNotificationTargets": [{"url": "json://example.test", "enabled": True, "tested": True,
                                          "awayOnly": False}]})
    monkeypatch.setattr(external_notifications, "_send", lambda *args: args)
    monkeypatch.setattr(external_notifications, "_spawn", notifications.append)
    monkeypatch.setattr(external_notifications, "_last_seen", {})
    manager._bind_completion_callbacks(agent)
    coordinator = WriterCoordinator()
    monkeypatch.setattr(db_writer, "_write_coordinator", coordinator)
    monkeypatch.setattr(db_writer, "_db_write_lock", coordinator.foreground_lock(WriteResources.WRITER))
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    managers = registry.AgentManagerRegistry()
    managers._managers = {agent.provider: manager}
    monkeypatch.setattr(registry, "get_agent_manager_registry", lambda: managers)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: managers)
    return SimpleNamespace(**vars(f), agent=agent, manager=manager, frames=frames, notifications=notifications,
                           coordinator=coordinator)


async def drain(agent):
    agent._cancel_completion_timer()
    while agent._completion_tasks:
        await asyncio.gather(*tuple(agent._completion_tasks))
        # gather() can finish eagerly. Let queued ownership callbacks remove
        # completed tasks before inspecting the set again.
        await asyncio.sleep(0)


async def physical(f, identity, text):
    if f.provider == "claude_code":
        message = claude.answer(identity)
        message.content = [TextBlock(text=text)]
        await claude.feed(f.agent, [message, claude.result()])
    else:
        # Use the real event adapter and physical-turn loop with a fake SDK stream.
        final = codex.final(identity, thread_id=f.session.id)
        final.payload.turn_id = "native-A"
        final.payload.item.root.text = text
        ended = codex.event("turn/completed", turn_id="native-A", thread_id=f.session.id)
        transport = codex.Turn([final, ended])
        transport.id = "native-A"
        await f.agent._run_turn("", None, turn_handle=transport)
    await drain(f.agent)


def waiting(f, *, registry=True, background=False):
    return _wait_reply._SessionWait(f.session.id, 0, started=0, twicc_pid=None if registry else 4242,
                                   want_text=True, wait_background=background)


@pytest.mark.parametrize("initial_state", ["starting", "user_turn"])
def test_send_before_provider_activity_records_one_turn(integrated, monkeypatch, initial_state):
    """The send-time state change must not compete with journal boundaries."""
    f = integrated
    ProcessRun.objects.filter(pk=f.agent.process_run.pk).update(state=initial_state)
    f.agent.process_run.state = initial_state

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        # Claude start()/send_message() publishes this before reading SDK traffic.
        # The manager has already connected the completion callbacks.
        f.agent._set_state(AgentState.ASSISTANT_TURN)
        await f.agent._notify_state_change()
        assert await asyncio.to_thread(lambda: ProcessRun.objects.get(
            pk=f.agent.process_run.pk).state) == "assistant_turn"
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(
            kind=TimelineKind.TURN_STARTED).count()) == 0

        await physical(f, "done", "DONE")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        await drain(f.agent)
        assert f.agent.state == AgentState.USER_TURN
        assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED],
        ).order_by("id").values_list("kind", flat=True))) == ["turn.started", "turn.ended"]
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 1

    asyncio.run(scenario())


def test_stop_before_provider_activity_records_no_turn(integrated):
    f = integrated
    ProcessRun.objects.filter(pk=f.agent.process_run.pk).update(state="starting")
    f.agent.process_run.state = "starting"

    async def scenario():
        f.agent._set_state(AgentState.ASSISTANT_TURN)
        await f.agent._notify_state_change()
        f.agent._set_state(AgentState.DEAD)
        await f.manager._persist_process_run_transition(f.agent, AgentState.DEAD)
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED],
        ).exists())
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(
            kind=TimelineKind.PROCESS_ENDED).count()) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("answer_fixture", ["claude_code"], indirect=True)
@pytest.mark.parametrize("request_at,stream_at", [(.025183, 3.190), (.020158, 1.878), (.020960, .674)])
def test_claude_requesting_prevents_early_completion(integrated, monkeypatch, request_at, stream_at):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "waiting", "WAITING")
        first = f.agent.completion_controller.candidate()
        assert first.deadline == 1.0
        await asyncio.to_thread(f.index, "waiting", 2, "WAITING")
        wait = await asyncio.to_thread(waiting, f)
        assert await asyncio.to_thread(wait.step) is None
        clock[0] = request_at
        await claude.feed(f.agent, [SystemMessage(subtype="status", data={"status": "requesting"})])
        await drain(f.agent)
        assert f.agent.completion_controller.candidate() is None
        if stream_at > first.deadline:
            clock[0] = first.deadline
            await f.manager._confirm_completion(f.agent, first)
            assert f.agent.state == AgentState.ASSISTANT_TURN
        clock[0] = stream_at
        await claude.feed(f.agent, [StreamEvent(uuid="stream", session_id=f.session.id,
                                              event={"type": "message_start"})])
        clock[0] = max(1.0, stream_at)
        await f.manager._confirm_completion(f.agent, first)
        assert f.agent.state == AgentState.ASSISTANT_TURN
        assert not any(call.args[0].state == AgentState.USER_TURN
                       for call in f.manager._broadcast_info.await_args_list)
        assert not any(frame.get("state") == "user_turn" for frame in f.frames)
        assert f.notifications == []
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
        await physical(f, "done", "DONE")
        final = f.agent.completion_controller.candidate()
        clock[0] = final.deadline
        await f.manager._confirm_completion(f.agent, final)
        await f.manager._confirm_completion(f.agent, final)  # Replayed timer emits no duplicate notification.
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 1
        assert sum(frame.get("state") == "user_turn" for frame in f.frames) == 1
        assert len(f.notifications) == 1
        # Publication can precede indexing. The wait must retain the final identity.
        assert await asyncio.to_thread(wait.step) is None
        await asyncio.to_thread(f.index, "done", 3, "DONE")
        reply = await asyncio.to_thread(wait.step)
        assert reply["outcome"] == "replied" and reply["text"] == "DONE"
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        await drain(f.agent)

    asyncio.run(scenario())


@pytest.mark.parametrize("stopped", [False, True])
def test_wait_survives_process_deletion_and_delayed_indexing(integrated, monkeypatch, stopped):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "done", "DONE")
        if stopped:
            terminal = f.agent._completion_terminate("ended")
            await drain(f.agent)
            await f.manager._end_completion(f.agent, terminal)
        else:
            ticket = f.agent.completion_controller.candidate()
            clock[0] = ticket.deadline
            await f.manager._confirm_completion(f.agent, ticket)
        await drain(f.agent)
        in_backend = await asyncio.to_thread(waiting, f)
        assert (await asyncio.to_thread(in_backend.step)) is None or stopped
        await asyncio.to_thread(ProcessRun.objects.filter(pk=f.agent.process_run.pk).delete)
        f.manager._agents.clear()
        shell = await asyncio.to_thread(waiting, f, registry=False)
        await asyncio.to_thread(f.index, "done", 2, "DONE")
        for wait in (in_backend, shell):
            reply = await asyncio.to_thread(wait.step)
            assert reply is not None, await asyncio.to_thread(lambda: list(LogicalTurn.objects.values()))
            assert reply["outcome"] == ("ended" if stopped else "replied")
            if not stopped:
                assert reply["text"] == "DONE"

    asyncio.run(scenario())


def test_real_writer_admission_stall_rechecks_root_ingress(integrated, monkeypatch):
    from twicc.providers import db_writer
    f = integrated

    async def scenario():
        clock = [0.0]
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        await physical(f, "waiting", "WAITING")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        async with db_writer._db_write_lock:
            # Preparation now precedes writer admission. Observe actual queue
            # entry rather than assuming one ready-queue tick completes reads.
            queued = asyncio.Event()
            acquire = f.coordinator.acquire

            async def observe_admission(*args, **kwargs):
                queued.set()
                return await acquire(*args, **kwargs)

            monkeypatch.setattr(f.coordinator, 'acquire', observe_admission)
            task = asyncio.create_task(f.manager._confirm_completion(f.agent, ticket))
            await queued.wait()
            assert not task.done()
            assert len(f.coordinator.requests) == 1
            f.agent._completion_activity()
            f.agent._cancel_completion_timer()
        await task
        await drain(f.agent)
        row = await asyncio.to_thread(LogicalTurn.objects.get)
        assert row.outcome is None
        assert f.agent.state == AgentState.ASSISTANT_TURN
        assert not any(call.args[0].state == AgentState.USER_TURN
                       for call in f.manager._broadcast_info.await_args_list)

    asyncio.run(scenario())


def test_replaced_agent_cannot_publish_stale_timer(integrated, monkeypatch):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "waiting", "WAITING")
        ticket = f.agent.completion_controller.candidate()
        f.manager._agents[f.session.id] = object()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        row = await asyncio.to_thread(LogicalTurn.objects.get)
        assert row.outcome is None
        f.manager._broadcast_info.reset_mock()
        await f.manager._confirm_completion(f.agent, ticket)
        f.manager._broadcast_info.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["activity", "replacement"])
def test_completion_changed_during_broadcast_preparation_does_not_notify(integrated, monkeypatch, change):
    from twicc import asgi

    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "done", "SYNTHETIC ANSWER")
        ticket = f.agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        original = asgi.get_session_and_project_display

        async def delayed(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(asgi, "get_session_and_project_display", delayed)
        clock[0] = ticket.deadline
        completion = asyncio.create_task(f.manager._confirm_completion(f.agent, ticket))
        await entered.wait()
        if change == "activity":
            f.agent._completion_activity()
            resumed = asyncio.create_task(f.agent._notify_state_change())
        else:
            # Keep identical public fields: identity must also invalidate the old publication.
            f.manager._agents[f.session.id] = copy(f.agent)
            resumed = None
        release.set()
        await completion
        if resumed is not None:
            await resumed
        await drain(f.agent)

        assert not any(frame.get("state") == "user_turn" for frame in f.frames)
        assert f.notifications == []

        if change == "activity":
            assert f.agent.state == AgentState.ASSISTANT_TURN
            assert f.frames[-1]["state"] == "assistant_turn"
            # Suppressing the stale finish must not suppress the next real finish.
            await physical(f, "next", "NEXT ANSWER")
            ticket = f.agent.completion_controller.candidate()
            clock[0] = ticket.deadline
            await f.manager._confirm_completion(f.agent, ticket)
            await drain(f.agent)
            assert sum(frame.get("state") == "user_turn" for frame in f.frames) == 1
            assert len(f.notifications) == 1

    asyncio.run(scenario())


@pytest.mark.usefixtures("compute_executor_started")
def test_watcher_publication_observes_committed_answer_and_journal(integrated, monkeypatch, tmp_path):
    from watchfiles import Change
    from twicc.core.models import Session, SessionType
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    from twicc.providers import sessions_watcher
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

    f = integrated
    watcher = ClaudeCodeSessionsWatcher() if f.provider == "claude_code" else CodexSessionsWatcher()
    f.manager.has_goal_continuation = lambda _: False
    f.manager.has_live_shells = lambda: False
    path = tmp_path / "synthetic.jsonl"
    if f.provider == "claude_code":
        record = {"type": "assistant", "uuid": "done", "message": {"role": "assistant",
                  "stop_reason": "end_turn", "content": [{"type": "text", "text": "DONE"}]}}
    else:
        record = {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": f.session.id,
                  "turn_id": "native-A", "item": {"type": "AgentMessage", "id": "done",
                  "phase": "final_answer", "content": [{"type": "Text", "text": "DONE"}]}}}
    path.write_bytes(orjson.dumps(record) + b"\n")
    Session.objects.filter(pk=f.session.pk).update(file_path=str(path), user_message_count=1)
    parsed = sessions_watcher.ParsedSessionFile(f.session.project_id, f.session.id, SessionType.SESSION, str(path))
    publications = []

    async def publish(layer, payload):
        if payload["type"] == "session_items_added":
            row = await asyncio.to_thread(LogicalTurn.objects.get)
            assert row.outcome == "normal"
            answer = await asyncio.to_thread(resolve_logical_answer, row, cursor=0)
            assert answer.status == "replied" and answer.message.text == "DONE"
            publications.append(payload)

    monkeypatch.setattr(sessions_watcher, "broadcast_message", publish)

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "done", "DONE")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        wait = await asyncio.to_thread(waiting, f)
        assert await asyncio.to_thread(wait.step) is None
        result = await watcher.sync_and_broadcast(path, parsed, Change.modified, None)
        assert result.disposition == "drained"
        assert (await asyncio.to_thread(wait.step))["text"] == "DONE"
        await drain(f.agent)

    asyncio.run(scenario())
    assert len(publications) == 1


def test_explicit_background_wait_continues_into_later_confirmed_interval(integrated, monkeypatch):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        shell = {"subagents": 0, "shells": 1, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}
        monkeypatch.setattr(f.agent, "current_background_work", lambda: shell)
        await physical(f, "waiting", "WAITING")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        await asyncio.to_thread(f.index, "waiting", 2, "WAITING")
        ordinary = await asyncio.to_thread(waiting, f)
        assert (await asyncio.to_thread(ordinary.step))["text"] == "WAITING"
        background = await asyncio.to_thread(waiting, f, background=True)
        assert await asyncio.to_thread(background.step) is None
        monkeypatch.setattr(f.agent, "current_background_work", lambda: None)
        assert await asyncio.to_thread(background.step) is None
        await physical(f, "done", "DONE")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        await asyncio.to_thread(f.index, "done", 3, "DONE")
        assert (await asyncio.to_thread(background.step))["text"] == "DONE"
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 2
        await drain(f.agent)

    asyncio.run(scenario())


def test_late_final_indexing_stays_with_its_interval_after_later_root_starts(integrated, monkeypatch):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "first", "WAITING")
        await asyncio.to_thread(f.index, "first", 2, "WAITING")
        wait = await asyncio.to_thread(waiting, f)
        assert await asyncio.to_thread(wait.step) is None
        clock[0] = .2
        await physical(f, "conclusion", "DONE")
        ticket = f.agent.completion_controller.candidate()
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        assert await asyncio.to_thread(wait.step) is None
        clock[0] += .1
        await physical(f, "later", "WAITING")
        await asyncio.to_thread(f.index, "later", 8, "WAITING")
        assert await asyncio.to_thread(wait.step) is None
        await asyncio.to_thread(f.index, "conclusion", 5, "DONE")
        reply = await asyncio.to_thread(wait.step)
        assert reply["outcome"] == "replied" and reply["line_num"] == 5 and reply["text"] == "DONE"
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 2
        await drain(f.agent)

    asyncio.run(scenario())


def test_pending_request_takes_priority_over_unconfirmed_answer(integrated, monkeypatch):
    from twicc.agent.states import PendingRequest
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await physical(f, "waiting", "WAITING")
        ticket = f.agent.completion_controller.candidate()
        await asyncio.to_thread(f.index, "waiting", 2, "WAITING")
        published = asyncio.Event()

        async def broadcast(info):
            await Manager._broadcast_info(f.manager, info)
            if info.pending_requests:
                published.set()

        f.manager._broadcast_info.side_effect = broadcast
        request = PendingRequest("synthetic-request", "tool_approval", "synthetic-tool", {}, 0)
        task = asyncio.create_task(f.agent._await_pending_request(request))
        task.add_done_callback(lambda _: published.set())
        await published.wait()
        if task.done():
            task.result()
        # State publication does not wait for the logical-turn snapshot writer.
        # Finish that write before the separate SQLite reader checks its result.
        await drain(f.agent)
        wait = await asyncio.to_thread(waiting, f)
        assert (await asyncio.to_thread(wait.step))["outcome"] == "awaiting_user_input"
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        assert f.agent.completion_controller.snapshot().outcome is None
        assert not any(frame.get("state") == "user_turn" for frame in f.frames)
        f.agent.resolve_pending_request(request.request_id, {"decision": "allow"})
        assert await task == {"decision": "allow"}
        await drain(f.agent)

    asyncio.run(scenario())


@pytest.mark.parametrize("answer_fixture", ["claude_code"], indirect=True)
def test_final_child_stop_confirms_without_parent_resume(integrated, monkeypatch):
    f = integrated

    async def scenario():
        clock = [0.0]
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock[0])
        await claude.feed(f.agent, [SystemMessage(subtype="task_started", data={
            "task_id": "synthetic-child", "task_type": "local_agent"})])
        await physical(f, "done", "DONE")
        assert f.agent.completion_controller.candidate() is None
        clock[0] = 2.0
        await claude.feed(f.agent, [SystemMessage(subtype="task_notification", data={
            "task_id": "synthetic-child", "status": "completed"})])
        await drain(f.agent)
        ticket = f.agent.completion_controller.candidate()
        assert ticket.deadline == 3.0
        clock[0] = ticket.deadline
        await f.manager._confirm_completion(f.agent, ticket)
        await asyncio.to_thread(f.index, "done", 2, "DONE")
        wait = await asyncio.to_thread(waiting, f)
        assert (await asyncio.to_thread(wait.step))["text"] == "DONE"
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 1

    asyncio.run(scenario())


def test_completion_diagnostics_distinguish_causes_without_answer_text(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("twicc.agent.completion"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="twicc.agent.completion")
    controller = CompletionController(run_id="synthetic-run")
    controller.activity(0, first_ref=NativeMessageRef("claude_uuid", "synthetic"))
    controller.provider_ended(0)
    controller.input_pending("synthetic-input", .02)
    controller.input_settled("synthetic-input", "rejected", .03)
    controller.set_holds(frozenset({"subagent"}), .04)
    controller.set_holds(frozenset(), .05)
    controller.set_awaiting(True, .06)
    controller.set_awaiting(False, .07)
    controller.activity(.08)
    controller.provider_ended(.09)
    ticket = controller.candidate()
    controller.provider_ended(.1)  # Duplicate physical result changes no diagnostic facts.
    assert controller.candidate() == ticket
    controller.claim_due(ticket, ticket.deadline)
    controller.activity(2)
    messages = [record.message for record in caplog.records]
    assert any("candidate_created" in message and "duration=1.000" in message for message in messages)
    for cause in ("input", "hold", "awaiting", "root"):
        assert any("candidate_cancelled" in message and f"cause={cause}" in message for message in messages)
    assert any("root_restart" in message and "cause=unannounced_root" in message for message in messages)
    assert sum("confirmed" in message for message in messages) == 1
    assert all("revision=" in message and "interval=" in message for message in messages)
    assert "WAITING" not in caplog.text and "DONE" not in caplog.text


@pytest.mark.parametrize("cause", ["input", "shell", "hold", "unannounced_root"])
def test_later_root_diagnostic_cause_is_distinct(caplog, monkeypatch, cause):
    monkeypatch.setattr(logging.getLogger("twicc.agent.completion"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="twicc.agent.completion")
    controller = CompletionController(run_id="synthetic-run")
    controller.activity(0)
    controller.provider_ended(0)
    controller.claim_due(controller.candidate(), 1)
    if cause == "input":
        controller.input_pending("synthetic-input", 2)
    elif cause == "shell":
        controller.note_shell_delivery()
    elif cause == "hold":
        controller.set_holds(frozenset({"subagent"}), 2)
    controller.activity(2)
    assert any("root_restart" in record.message and f"cause={cause}" in record.message
               for record in caplog.records)
