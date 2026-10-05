"""Canonical recovery and SDK settlement use the same durable question facts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import orjson

from twicc.providers.codex import async_questions as protocol
from twicc.providers.codex.agent.agent import CodexAgent


def record(kind, *, turn="t1", second=1, **data):
    return {
        "timestamp": f"2026-10-05T09:12:{second:02d}Z",
        "type": "event_msg",
        "payload": {"type": kind, "turn_id": turn, **data},
    }


def question(*, turn="t1", item="q1", second=1):
    return record(
        "item_completed",
        turn=turn,
        second=second,
        item={
            "type": "AgentMessage",
            "id": item,
            "delivery": "async",
            "phase": "final_answer",
            "content": [],
            "questions": [{"title": "Keep the menu?", "options": ["Yes", "No"]}],
        },
    )


def user(text, *, turn="t2", item="u1", second=3):
    return record(
        "item_completed",
        turn=turn,
        second=second,
        item={
            "type": "UserMessage",
            "id": item,
            "content": [{"type": "text", "text": text}],
        },
    )


def replay(records):
    facts = [
        fact
        for line, value in enumerate(records, 1)
        for fact in protocol.extract_async_question_facts(value, line=line)
    ]
    return protocol.reduce_question_state({}, facts)


def test_final_answer_phase_does_not_release_question():
    state = replay([question(), record("item_completed", item={"id": "tool", "type": "CommandExecution"})])
    assert state["batches"]["q1"]["status"] == "collecting"


def test_canonical_completion_and_later_human_retire_question():
    state = replay([question(), record("task_complete", second=2), user("Yes")])
    assert state["batches"]["q1"]["status"] == "sent"


@pytest.mark.parametrize(
    "text",
    [
        "<twicc-resume>continue</twicc-resume>",
        "/compact",
        "/goal clear",
        "Message Type: MESSAGE\nTask name: /root\nSender: /root/worker\nPayload:\nDone",
    ],
)
def test_internal_and_agent_inputs_do_not_retire_questions(text):
    state = replay([question(), record("task_complete", second=2), user(text)])
    assert state["batches"]["q1"]["status"] != "sent"


def test_historical_steering_before_completion_preserves_question():
    state = replay([question(), user("Go on", turn="t1", second=2), record("task_complete", second=3)])
    assert state["batches"]["q1"]["status"] == "ready"


def test_historical_active_goal_waits_for_terminal_goal_and_last_turn():
    active = record("thread_goal_updated", second=0, goal={"id": "g1", "status": "active", "objective": "Build"})
    terminal = record("thread_goal_updated", turn="t2", second=5, goal={"id": "g1", "status": "complete"})
    records = [active, question(), record("task_complete", second=2), record("task_started", turn="t2", second=3)]
    assert replay(records)["batches"]["q1"]["status"] == "collecting"
    assert replay([*records, terminal])["batches"]["q1"]["status"] == "collecting"
    assert (
        replay([*records, terminal, record("task_complete", turn="t2", second=6)])["batches"]["q1"]["status"] == "ready"
    )


def test_internal_successor_keeps_preceding_questions_collecting():
    from twicc.providers.codex.agent.agent import _AUTO_REVIEW_RETRY_PROMPT

    records = [question(), record("task_complete", second=2), user(_AUTO_REVIEW_RETRY_PROMPT)]
    assert replay(records)["batches"]["q1"]["status"] == "collecting"
    assert replay([*records, record("task_complete", turn="t2", second=4)])["batches"]["q1"]["status"] == "ready"


def test_runtime_owner_holds_watcher_completion_until_explicit_settlement(monkeypatch):
    from datetime import datetime

    clock = SimpleNamespace(now=lambda _: datetime.fromisoformat("2026-10-05T09:12:00+00:00"))
    monkeypatch.setattr("twicc.providers.codex.agent.agent.datetime", clock)
    agent = CodexAgent.__new__(CodexAgent)
    agent.ephemeral = False
    state = {}

    async def merge(facts):
        nonlocal state
        state = protocol.reduce_question_state(state, facts)

    agent._record_async_question_facts = merge

    async def run():
        await agent._admit_async_question_owner()
        await merge(
            [
                fact
                for line, value in enumerate([question(), record("task_complete", second=2)], 1)
                for fact in protocol.extract_async_question_facts(value, line=line)
            ]
        )
        assert state["batches"]["q1"]["status"] == "collecting"
        await agent._link_async_question_turn("t1")
        assert state["batches"]["q1"]["status"] == "collecting"
        await agent._settle_async_questions("t1", outcome="completed")
        assert state["batches"]["q1"]["status"] == "ready"

    asyncio.run(run())


def live_agent(monkeypatch):
    from datetime import datetime
    from tests.test_codex_send_fallback import fixture

    agent, thread, deliveries = fixture()
    state = {}

    async def merge(facts):
        nonlocal state
        state = protocol.reduce_question_state(state, facts)

    agent._record_async_question_facts = merge
    monkeypatch.setattr("twicc.providers.codex.agent.agent.log_stream_event", lambda *args: None)
    # The admission precedes the source fixtures, including when wall time differs.
    monkeypatch.setattr(
        "twicc.providers.codex.agent.agent.datetime",
        SimpleNamespace(
            now=lambda _: datetime.fromisoformat("2026-10-05T09:12:00+00:00"),
            fromtimestamp=datetime.fromtimestamp,
        ),
    )
    return agent, lambda: state


def sdk_question(turn="t1", item="q1"):
    from datetime import datetime
    from openai_codex.generated.v2_all import ItemCompletedNotification
    from openai_codex.models import Notification

    return Notification(
        "item/completed",
        ItemCompletedNotification.model_validate(
            {
                "threadId": "session",
                "turnId": turn,
                "completedAtMs": int(datetime.fromisoformat("2026-10-05T09:12:01+00:00").timestamp() * 1000),
                "item": {
                    "id": item,
                    "type": "agentMessage",
                    "text": "Choose",
                    "phase": "final_answer",
                    "delivery": "async",
                    "questions": [{"title": "Keep the menu?", "options": ["Yes", "No"]}],
                },
            }
        ),
    )


def test_sdk_and_watcher_deduplicate_and_keep_source_timestamp(monkeypatch):
    agent, state = live_agent(monkeypatch)

    async def run():
        await agent._link_async_question_turn("t1")
        await agent._handle_stream_event(sdk_question())
        assert state()["facts"]["question:q1"]["at"] == "2026-10-05T09:12:01+00:00"
        await agent._record_async_question_facts(protocol.extract_async_question_facts(question(), line=10))
        assert list(state()["batches"]) == ["q1"]
        assert state()["batches"]["q1"]["line"] == 10
        assert state()["batches"]["q1"]["status"] == "collecting"

    asyncio.run(run())


def test_parent_completion_releases_question_during_subagent_hold(monkeypatch):
    from tests.test_codex_send_fallback import Turn
    from twicc.agent import AgentState

    agent, state = live_agent(monkeypatch)
    turn = Turn("t1", [])
    turn.events = [sdk_question()]
    turn.finished.set()

    async def hold():
        assert state()["batches"]["q1"]["status"] == "ready"
        return True

    agent._try_arm_subagent_hold = hold
    asyncio.run(agent._run_turn("", None, turn_handle=turn))
    assert agent.state == AgentState.ASSISTANT_TURN


def test_auto_review_continuation_retains_question_until_successor_completes(monkeypatch):
    from tests.test_codex_send_fallback import Turn

    agent, state = live_agent(monkeypatch)
    first, second = Turn("t1", []), Turn("t2", [])
    first.events = [sdk_question()]
    first.finished.set()
    second.finished.set()
    agent._auto_review_retry_after_turn = True

    async def open_next(*args, **kwargs):
        assert state()["batches"]["q1"]["status"] == "collecting"
        assert state()["facts"]["decision:t1"]["data"]["decision"] == "continuation"
        return second

    agent._thread.turn_with_policy = open_next
    asyncio.run(agent._run_turn("", None, turn_handle=first))
    assert state()["batches"]["q1"]["status"] == "ready"
    assert state()["facts"]["decision:t1"]["data"]["successor_turn_id"] == "t2"


def test_plan_prompt_has_ready_questions_without_bypassing_pending_request(monkeypatch):
    from tests.test_codex_send_fallback import Turn

    agent, state = live_agent(monkeypatch)
    turn = Turn("t1", [])
    turn.events = [sdk_question()]
    turn.finished.set()
    original = agent._handle_stream_event

    async def handle(event):
        await original(event)
        agent._plan_item_this_turn = True

    agent._handle_stream_event = handle

    async def prompt():
        assert state()["batches"]["q1"]["status"] == "ready"

    agent._prompt_plan_implementation = prompt
    asyncio.run(agent._run_turn("", None, turn_handle=turn))
    agent._try_arm_subagent_hold.assert_not_awaited()


def test_unlinked_runtime_goal_owner_overrides_historical_terminal(monkeypatch):
    agent, state = live_agent(monkeypatch)
    records = [
        record("thread_goal_updated", goal={"id": "g1", "status": "active"}),
        question(),
        record("task_complete", second=2),
        record("thread_goal_updated", second=3, goal={"id": "g1", "status": "complete"}),
    ]

    async def run():
        await agent._admit_async_question_owner()
        await agent._record_async_question_facts(
            [
                fact
                for line, value in enumerate(records, 1)
                for fact in protocol.extract_async_question_facts(value, line=line)
            ]
        )
        assert state()["batches"]["q1"]["status"] == "collecting"

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("linked,completed", [(True, True), (False, True), (False, False)])
@pytest.mark.parametrize(
    "status,goal_status,expected",
    [
        ("idle", None, "ready"),
        ("systemError", None, "ready"),
        ("active", None, "collecting"),
        ("idle", "active", "collecting"),
        (None, None, "collecting"),
    ],
)
def test_restart_reconciles_pending_owner_completion_window(status, goal_status, expected, linked, completed):
    from asgiref.sync import sync_to_async
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session
    from twicc.core.services.async_questions import merge_question_facts, read_question_snapshot
    from twicc.providers import db_writer

    project = Project.objects.create(id="restart-project")
    session = Session.objects.create(id="restart-session", project=project, provider=Provider.CODEX)
    facts = [
        protocol.QuestionFact(
            "owner:g1",
            "live_owner",
            "2026-10-05T09:12:00Z",
            "t1" if linked else None,
            None,
            None,
            {"group_id": "g1", "root_turn_id": "t1" if linked else None, "state": "pending"},
        )
    ]
    facts.extend(
        fact
        for line, value in enumerate([question(), *([record("task_complete", second=2)] if completed else [])], 1)
        for fact in protocol.extract_async_question_facts(value, line=line)
    )
    merge_question_facts(session.id, facts)
    assert read_question_snapshot(session.id)["batches"][0]["status"] == "collecting"
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id, agent.ephemeral = session.id, False
    read = AsyncMock(
        return_value=SimpleNamespace(
            thread=SimpleNamespace(
                status=SimpleNamespace(root=SimpleNamespace(type=status)),
                turns=[SimpleNamespace(id="t1")],
            )
        )
    )
    if status is None:
        read.side_effect = RuntimeError("Cannot establish provider status")
    agent._codex = SimpleNamespace(_client=SimpleNamespace(thread_read=read))
    agent._thread = SimpleNamespace(
        goal_get=AsyncMock(return_value=SimpleNamespace(status=goal_status) if goal_status else None)
    )

    async def run():
        db_writer.start_db_writer()
        try:
            await agent._reconcile_async_question_owners()
            return await sync_to_async(read_question_snapshot)(session.id)
        finally:
            await db_writer.stop_db_writer()

    assert asyncio.run(run())["batches"][0]["status"] == expected


def test_goal_terminal_boundary_follows_last_completion_and_intermediate_human():
    values = [
        record("thread_goal_updated", second=0, goal={"id": "g1", "status": "active"}),
        question(),
        record("task_complete", second=2),
        user("Wait", turn="t1", second=3),
        record("thread_goal_updated", second=4, goal={"id": "g1", "status": "complete"}),
    ]
    assert replay(values)["batches"]["q1"]["status"] == "ready"


def test_historical_native_goal_context_links_preceding_turn():
    context = {
        "timestamp": "2026-10-05T09:12:04Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": '<codex_internal_context source="goal"><objective>Build</objective></codex_internal_context>',
                }
            ],
        },
    }
    values = [question(), record("task_complete", second=2), record("task_started", turn="t2", second=3), context]
    assert replay(values)["batches"]["q1"]["status"] == "collecting"


@pytest.mark.parametrize("outcome", ["interrupted", "failed"])
def test_terminal_sdk_status_is_preserved_at_control_return(monkeypatch, outcome):
    from openai_codex.models import Notification
    from openai_codex.generated.v2_all import TurnCompletedNotification
    from tests.test_codex_send_fallback import Turn

    agent, state = live_agent(monkeypatch)
    turn = Turn("t1", [])
    turn.events = [
        sdk_question(),
        Notification(
            "turn/completed",
            TurnCompletedNotification.model_validate(
                {
                    "threadId": "session",
                    "turn": {"id": "t1", "status": outcome, "items": [], "itemsView": "full"},
                }
            ),
        ),
    ]
    turn.finished.set()
    asyncio.run(agent._run_turn("", None, turn_handle=turn))
    assert state()["batches"]["q1"]["status"] == "ready"
    returned = next(fact for fact in state()["facts"].values() if fact["kind"] == "control_return")
    assert returned["data"]["outcome"] == outcome


@pytest.mark.django_db(transaction=True)
def test_new_thread_uses_watcher_creation_before_owner_rpc(tmp_path, monkeypatch):
    from asgiref.sync import sync_to_async
    from twicc.core.models import AsyncQuestionState, Project, Session
    from twicc.pending_agent_settings import set_pending_agent_settings
    from twicc.pending_session_attributes import set_pending_session_attributes
    from twicc.providers import db_writer
    from twicc.providers.helpers import AgentSettings
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.compute_base import LiveSyncLimits

    project = Project.objects.create(id="new-project", directory=str(tmp_path))
    path = tmp_path / "2026/10/05/rollout-new-session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("")
    monkeypatch.setattr("twicc.provider_homes.codex_sessions_dir", lambda: tmp_path)
    watcher = CodexSessionsWatcher()
    monkeypatch.setattr("twicc.providers.codex.sessions_watcher.get_watcher", lambda: watcher)
    settings = AgentSettings(permission_mode="yolo", effort="high")
    set_pending_agent_settings("new-session", settings)
    set_pending_session_attributes("new-session", hidden=True, annotations={"task": "worker"})
    thread = SimpleNamespace(start_response=SimpleNamespace(thread=SimpleNamespace(path=str(path))))
    agent = CodexAgent(
        "new-session",
        project.id,
        str(tmp_path),
        settings,
        SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))),
        thread,
    )

    async def run():
        db_writer.start_db_writer()
        try:
            await agent._ensure_async_question_session()
            await agent._admit_async_question_owner()

            def verify():
                session = Session.objects.get(pk="new-session")
                assert session.hidden is True
                assert session.effort == "high"
                assert session.annotations == {"task": "worker"}
                assert session.last_line == 0
                assert AsyncQuestionState.objects.get(session=session).state["facts"]
                path.write_bytes(
                    b"\n".join(orjson.dumps(value) for value in [question(), record("task_complete", second=2)]) + b"\n"
                )
                watcher.get_compute().sync_session_slice(session.id, path, limits=LiveSyncLimits())
                session.refresh_from_db()
                assert session.last_line == 2
                assert session.hidden is True
                assert session.effort == "high"
                assert session.annotations == {"task": "worker"}

            await db_writer.run_under_db_write_lock(lambda: sync_to_async(verify)())
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("source_order", ["reply_after_end", "steering_before_end"])
def test_live_incremental_history_retires_only_ready_questions(tmp_path, source_order):
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session
    from twicc.core.services.async_questions import read_question_snapshot
    from twicc.providers.codex.compute import CodexSessionCompute
    from twicc.providers.compute_base import LiveSyncLimits

    project = Project.objects.create(id="live-project")
    session = Session.objects.create(id="live-session", project=project, provider=Provider.CODEX)
    compute = CodexSessionCompute()
    path = tmp_path / "live.jsonl"
    records = (
        [question(), record("task_complete", second=2), user("Yes")]
        if source_order == "reply_after_end"
        else [question(), user("Yes", turn="t1", second=2), record("task_complete", second=3)]
    )
    path.write_bytes(b"\n".join(orjson.dumps(value) for value in records) + b"\n")
    # Separate transactions reconstruct the complete timeline from durable facts.
    for _ in records:
        compute.sync_session_slice(session.id, path, limits=LiveSyncLimits(max_lines=1))
    snapshot = read_question_snapshot(session.id)
    if source_order == "reply_after_end":
        assert snapshot["resolutions"]["q1"]["status"] == "sent"
    else:
        assert snapshot["batches"][0]["status"] == "ready"


@pytest.mark.django_db(transaction=True)
def test_snapshot_publication_occurs_after_commit_and_never_after_rollback(monkeypatch):
    from django.db import transaction
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session
    from twicc.core.services.async_questions import merge_question_facts

    layer = SimpleNamespace(group_send=AsyncMock())
    monkeypatch.setattr("twicc.providers.codex.question_snapshots.get_channel_layer", lambda: layer)
    session = Session.objects.create(
        id="publish-session", project=Project.objects.create(id="publish-project"), provider=Provider.CODEX
    )
    facts = protocol.extract_async_question_facts(question(), line=1)
    with transaction.atomic():
        merge_question_facts(session.id, facts)
        layer.group_send.assert_not_awaited()
        transaction.set_rollback(True)
    layer.group_send.assert_not_awaited()
    with transaction.atomic():
        snapshot = merge_question_facts(session.id, facts)
        layer.group_send.assert_not_awaited()
    layer.group_send.assert_awaited_once()
    assert layer.group_send.await_args.args == (
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "async_questions_updated",
                "session_id": session.id,
                "async_questions": snapshot,
            },
        },
    )


def test_new_historical_goal_does_not_inherit_old_goal_return_boundary():
    context = {
        "timestamp": "2026-10-05T09:12:06Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": '<codex_internal_context source="goal"><objective>Next</objective></codex_internal_context>',
                }
            ],
        },
    }
    values = [
        record("thread_goal_updated", second=0, goal={"id": "g1", "status": "active"}),
        question(),
        record("task_complete", second=2),
        record("thread_goal_updated", second=3, goal={"id": "g1", "status": "complete"}),
        record("thread_goal_updated", turn="t2", second=4, goal={"id": "g2", "status": "active"}),
        record("task_started", turn="t2", second=5),
        context,
        question(turn="t2", item="q2", second=7),
        record("task_complete", turn="t2", second=8),
        user("Wait for goal", turn="t2", second=9),
        record("thread_goal_updated", turn="t2", second=10, goal={"id": "g2", "status": "complete"}),
    ]
    assert replay(values)["batches"]["q2"]["status"] == "ready"


def test_goal_router_owns_fast_physical_turns_before_consumer_settlement(monkeypatch):
    from datetime import datetime
    from tests.test_codex_goal_steering import agent_fixture, event, goal_event

    agent, router, client = agent_fixture()
    state = {}

    async def merge(facts):
        nonlocal state
        state = protocol.reduce_question_state(state, facts)

    agent._record_async_question_facts = merge
    monkeypatch.setattr(
        "twicc.providers.codex.agent.agent.datetime",
        SimpleNamespace(now=lambda _: datetime.fromisoformat("2026-10-05T09:12:00+00:00")),
    )

    async def hold():
        assert state["batches"]["q1"]["status"] == "ready"
        return True

    agent._try_arm_subagent_hold = hold

    async def run():
        await agent.run_goal_command("Build")
        event(router, "turn/started", "a")
        event(router, "turn/completed", "a")
        event(router, "turn/started", "b")
        records = [
            question(turn="a"),
            record("task_complete", turn="a", second=2),
            record("task_complete", turn="b", second=3),
        ]
        await merge(
            [
                fact
                for line, value in enumerate(records, 1)
                for fact in protocol.extract_async_question_facts(value, line=line)
            ]
        )
        assert state["batches"]["q1"]["status"] == "collecting"
        goal_event(router, "complete")
        event(router, "turn/completed", "b")
        await asyncio.wait_for(agent._turn_task, 1)
        assert state["facts"]["decision:a"]["data"]["successor_turn_id"] == "b"
        assert state["batches"]["q1"]["status"] == "ready"

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_sdk_question_persistence_ignores_provider_subagent_session():
    from twicc.core.enums import Provider
    from twicc.core.models import Project, Session, SessionType, AsyncQuestionState
    from twicc.providers import db_writer

    project = Project.objects.create(id="child-project")
    parent = Session.objects.create(id="main", project=project, provider=Provider.CODEX, file_path="main.jsonl")
    child = Session.objects.create(
        id="child",
        project=project,
        provider=Provider.CODEX,
        file_path="child.jsonl",
        type=SessionType.SUBAGENT,
        parent_session=parent,
    )
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id, agent.ephemeral = child.id, False

    async def run():
        db_writer.start_db_writer()
        try:
            await agent._record_async_question_facts(protocol.extract_async_question_facts(question(), line=1))
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    assert not AsyncQuestionState.objects.filter(session=child).exists()


def test_historical_internal_prompt_cannot_join_distinct_runtime_owners(monkeypatch):
    agent, state = live_agent(monkeypatch)

    async def run():
        await agent._link_async_question_turn("t1")
        await agent._record_async_question_facts(
            protocol.extract_async_question_facts(record("task_complete", second=2), line=2)
        )
        await agent._settle_async_questions("t1", outcome="completed")
        await agent._link_async_question_turn("t2")
        await agent._record_async_question_facts(
            protocol.extract_async_question_facts(user("<twicc-resume>Continue</twicc-resume>", turn="t2"), line=3)
        )
        await agent._record_async_question_facts(
            protocol.extract_async_question_facts(question(turn="t2", item="q2", second=4), line=4)
        )
        assert state()["batches"]["q2"]["status"] == "collecting"

    asyncio.run(run())
