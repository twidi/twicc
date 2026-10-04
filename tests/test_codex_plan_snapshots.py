"""SDK plans survive historical recompute and concurrent live ingestion."""

import asyncio
import queue
from datetime import timedelta
from types import SimpleNamespace

import orjson
import pytest
from channels.layers import get_channel_layer
from openai_codex.generated.v2_all import TurnPlanUpdatedNotification

from twicc.core.models import Project, Session
from twicc.core.enums import Provider
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.compute import get_compute
from tests.live_sync_helpers import drain_live_sync
from tests.test_codex_code_mode import _NOW, _create_items, _exec_call_line, _run_batch_compute


@pytest.fixture
def session(db):
    project = Project.objects.create(id="sdk-plan-project")
    row = Session.objects.create(id="sdk-plan", project=project, provider=Provider.CODEX, user_message_count=1)
    yield row
    get_compute().end_session_compute(row.id)


def sdk_snapshot(*, empty=False):
    return {
        "provider": "codex", "source": "turn/plan/updated", "line": None,
        "updated_at": (_NOW + timedelta(seconds=1)).isoformat(), "turn_id": "turn-plan",
        "items": [] if empty else [{"content": "Implementation", "status": "completed"}],
        "explanation": None,
    }


def old_plan():
    return _exec_call_line("plan", 'await tools.update_plan({plan:[{step:"Implementation",status:"in_progress"}]});')


@pytest.mark.parametrize("lines", [[], [old_plan()], [
    _exec_call_line("dynamic", 'const p=load("implementationSteps");p[0].status="completed";'
                    'text(await tools.update_plan({plan:p}));'),
]])
@pytest.mark.parametrize("empty", [False, True])
def test_recompute_preserves_sdk_state(session, lines, empty):
    session.tasks = sdk_snapshot(empty=empty)
    session.save(update_fields=["tasks"])
    _create_items(session, lines)
    _run_batch_compute(session)
    session.refresh_from_db()
    assert session.tasks == sdk_snapshot(empty=empty)


def test_recompute_apply_reads_sdk_update_received_after_worker_started(session):
    _create_items(session, [old_plan()])
    q = queue.Queue()
    get_compute().compute_session_metadata(session.id, q, run_id=0)
    Session.objects.filter(pk=session.pk).update(tasks=sdk_snapshot())
    while not q.empty():
        msg = orjson.loads(q.get())
        if msg.get("type") == "session_complete":
            get_compute().apply_session_complete(msg)
    session.refresh_from_db()
    assert session.tasks == sdk_snapshot()


def test_live_rollout_does_not_overwrite_newer_sdk_state(session, tmp_path):
    session.tasks = sdk_snapshot()
    session.save(update_fields=["tasks"])
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(old_plan() + "\n")
    drain_live_sync(get_compute(), session, rollout)
    assert session.tasks == sdk_snapshot()


def test_newer_rollout_replaces_sdk_state(session):
    session.tasks = sdk_snapshot()
    session.save(update_fields=["tasks"])
    parsed = orjson.loads(old_plan())
    parsed["timestamp"] = (_NOW + timedelta(seconds=2)).isoformat()
    _create_items(session, [orjson.dumps(parsed).decode()])
    _run_batch_compute(session)
    session.refresh_from_db()
    assert session.tasks["items"] == [{"content": "Implementation", "status": "in_progress"}]
    assert session.tasks["source"] == "update_plan"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("child,ephemeral,empty", [(False, False, False), (True, False, False),
                                                  (False, True, False), (False, False, True)])
def test_sdk_event_persists_and_broadcasts_to_its_session(child, ephemeral, empty, monkeypatch):
    async def run():
        from twicc.providers import db_writer

        monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
        monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
        project = await Project.objects.acreate(id="event-project")
        root = await Session.objects.acreate(id="root-plan", project=project, provider=Provider.CODEX,
                                            user_message_count=1, file_path="root.jsonl")
        target = await Session.objects.acreate(id="child-plan", project=project, provider=Provider.CODEX,
                                              parent_session=root, type="subagent", file_path="child.jsonl") if child else root
        agent = CodexAgent.__new__(CodexAgent)
        agent.session_id = root.id
        agent.ephemeral = ephemeral
        payload = TurnPlanUpdatedNotification(threadId=target.id, turnId="turn-live", explanation="Done",
                    plan=[] if empty else [{"step": "Implementation", "status": "inProgress"}])
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)
        await agent._handle_stream_event(SimpleNamespace(method="turn/plan/updated", payload=payload))
        await target.arefresh_from_db()
        await root.arefresh_from_db()
        if ephemeral:
            assert target.tasks == {}
        else:
            assert target.tasks["items"] == ([] if empty else [{"content": "Implementation", "status": "in_progress"}])
            assert target.tasks["turn_id"] == "turn-live"
            assert target.tasks["source"] == "turn/plan/updated"
            assert target.tasks["explanation"] == "Done"
            if child:
                assert root.tasks == {}
            else:
                message = await asyncio.wait_for(layer.receive(channel), timeout=2)
                assert message["data"]["session"]["tasks"] == target.tasks
        if not ephemeral and not empty:
            for status in ("completed", "pending"):
                update = TurnPlanUpdatedNotification(threadId=target.id, turnId="turn-next", explanation=None,
                                                     plan=[{"step": "Implementation", "status": status}])
                await agent._handle_stream_event(SimpleNamespace(method="turn/plan/updated", payload=update))
                # A fresh ORM read uses persistent state, not an agent cache.
                tasks = await Session.objects.filter(pk=target.pk).values_list("tasks", flat=True).aget()
                assert tasks["items"] == [{"content": "Implementation", "status": status}]
                assert tasks["turn_id"] == "turn-next"
        await layer.group_discard("updates", channel)
    asyncio.run(run())


@pytest.mark.parametrize("candidate_time", [_NOW.isoformat(), None, "invalid"])
def test_recompute_cannot_discard_sdk_state_with_equal_or_unknown_jsonl_time(session, candidate_time):
    snapshot = sdk_snapshot()
    snapshot["updated_at"] = _NOW.isoformat()
    session.tasks = snapshot
    session.save(update_fields=["tasks"])
    parsed = orjson.loads(old_plan())
    parsed["timestamp"] = candidate_time
    _create_items(session, [orjson.dumps(parsed).decode()])
    _run_batch_compute(session)
    session.refresh_from_db()
    assert session.tasks == snapshot


def test_recompute_still_clears_unreconstructible_non_sdk_state(session):
    session.tasks = {"provider": "codex", "source": "update_plan", "items": [{"content": "Stale"}]}
    session.save(update_fields=["tasks"])
    _run_batch_compute(session)
    session.refresh_from_db()
    assert session.tasks == {}


@pytest.mark.django_db(transaction=True)
def test_first_sdk_plan_waits_for_watcher_session_creation(monkeypatch):
    from asgiref.sync import sync_to_async
    from twicc.core.models import SessionType
    from twicc.providers import db_writer
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile

    async def run():
        monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
        monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
        project = await Project.objects.acreate(id="late-plan-project")
        agent = CodexAgent.__new__(CodexAgent)
        agent.session_id = "late-plan"
        agent.ephemeral = False
        payload = TurnPlanUpdatedNotification(threadId=agent.session_id, turnId="first-turn",
                                             plan=[{"step": "First plan", "status": "completed"}])
        await agent._handle_stream_event(SimpleNamespace(method="turn/plan/updated", payload=payload))
        parsed = ParsedSessionFile(project.id, agent.session_id, SessionType.SESSION, "late.jsonl")
        session = await sync_to_async(CodexSessionsWatcher().create_session_sync)(parsed, project)
        await session.arefresh_from_db()
        assert session.tasks["items"] == [{"content": "First plan", "status": "completed"}]
        assert session.tasks["turn_id"] == "first-turn"
    asyncio.run(run())
