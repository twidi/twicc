"""Recorded Codex questions converge through ingestion, delivery, and recompute."""

import asyncio
import queue
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from asgiref.sync import sync_to_async
from django.test import Client
from openai_codex.errors import JsonRpcError
from openai_codex.generated.v2_all import ItemCompletedNotification, ItemStartedNotification, TurnSteerResponse
from openai_codex.models import Notification

from tests.test_codex_async_question_lifecycle import record, user
from tests.test_codex_send_fallback import Turn, cleanup
from twicc.agent import AgentState, SendDeliveryError
from twicc.auth.session_auth import bind_session
from twicc.core.enums import Provider
from twicc.core.models import AsyncQuestionState, Project, Session, SessionItem
from twicc.core.services import async_questions as service
from twicc.providers import db_writer
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.compute import get_compute
from twicc.providers.helpers import AgentSettings
from twicc.providers.live_sync import LiveSyncLimits

pytestmark = pytest.mark.django_db(transaction=True)

# Recorded question content from the two protocol observations in spec section 2.
OBSERVED = [
    ("call_ttjW3C3wndpxnPwKZ5J55uGd", "Should clicking a process state open its session?",
     ["Yes, open the session", "No, show its state only"]),
    ("second-observed-question", "Should the tooltip retain the session action menu?",
     ["Yes, retain the menu.", "No, remove the menu."]),
]


def canonical_question(number=0, *, turn="t1", second=1):
    item_id, title, options = OBSERVED[number]
    return record("item_completed", turn=turn, second=second, thread_id="integration-session", item={
        "type": "AgentMessage", "id": item_id, "delivery": "async", "phase": "final_answer",
        "content": [{"type": "Text", "text": title}],
        "questions": [{"title": title, "options": options}],
    })


def sdk_question(number=0, *, turn="t1", second=1, started=False):
    item_id, title, options = OBSERVED[number]
    model = ItemStartedNotification if started else ItemCompletedNotification
    return Notification("item/started" if started else "item/completed", model.model_validate({
        "threadId": "integration-session", "turnId": turn,
        "startedAtMs" if started else "completedAtMs": int(
            datetime.fromisoformat(f"2026-10-05T09:12:{second:02d}+00:00").timestamp() * 1000
        ),
        "item": {"type": "agentMessage", "id": item_id, "delivery": "async", "phase": "final_answer",
                 "text": title, "questions": [{"title": title, "options": options}]},
    }))


def source_user(text, *, request_id=None, second=31):
    value = user(text, turn="reply", second=second)
    if request_id is not None:
        value["payload"]["item"]["clientId"] = request_id
    return value


def projection(snapshot):
    return {key: snapshot[key] for key in ("batches", "resolutions", "widget_enabled")}


def recompute(session):
    """Run the CPU producer and the guarded application against canonical rows."""
    compute, results = get_compute(), queue.Queue()
    before = service.read_question_snapshot(session.id)
    compute.compute_session_metadata(session.id, results, run_id=0)
    assert service.read_question_snapshot(session.id) == before
    applied = 0
    while not results.empty():
        message = orjson.loads(results.get_nowait())
        if message.get("type") == "session_complete":
            assert compute.apply_session_complete(message).outcome == "applied"
            applied += 1
    assert applied == 1
    return service.read_question_snapshot(session.id)


@pytest.fixture
def harness(tmp_path, monkeypatch, settings):
    project = Project.objects.create(id="integration-project", directory=str(tmp_path))
    path = tmp_path / "rollout.jsonl"
    path.touch()
    session = Session.objects.create(id="integration-session", project=project, provider=Provider.CODEX,
                                     file_path=str(path))
    layer = SimpleNamespace(group_send=AsyncMock())
    monkeypatch.setattr("twicc.providers.codex.question_snapshots.get_channel_layer", lambda: layer)
    monkeypatch.setattr("twicc.providers.codex.agent.agent.log_stream_event", lambda *_: None)
    monkeypatch.setattr("twicc.providers.codex.agent.agent.apply_pending_context", lambda _, text: text)
    clock = SimpleNamespace(second=0)
    dates = SimpleNamespace(now=lambda _: datetime(2026, 10, 5, 9, 12, clock.second, tzinfo=UTC),
                            fromtimestamp=datetime.fromtimestamp)
    monkeypatch.setattr("twicc.providers.codex.agent.agent.datetime", dates)
    monkeypatch.setattr("twicc.providers.codex.agent.manager.datetime", dates)
    sdk = SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None),
                                                 request=AsyncMock(return_value=TurnSteerResponse(turn_id="reply"))),
                          _ensure_initialized=AsyncMock())
    turn = Turn("reply", [])
    turn.thread_id, turn._codex = session.id, sdk
    thread = SimpleNamespace(id=session.id, turn_with_policy=AsyncMock(return_value=turn))
    agent = CodexAgent(session.id, project.id, str(tmp_path), AgentSettings(permission_mode="yolo"), sdk, thread)
    agent._set_state(AgentState.USER_TURN)
    agent._notify_state_change = AsyncMock()
    agent._broadcast_stream_event = AsyncMock()
    agent._reconcile_context = AsyncMock()
    agent._handle_error = AsyncMock()
    agent._try_arm_subagent_hold = AsyncMock(return_value=False)
    agent.apply_agent_settings = AsyncMock()
    manager = CodexAgentManager()
    manager._agents[session.id] = agent
    monkeypatch.setattr(manager, "_check_ephemeral_readonly", lambda *_: None)
    settings.TWICC_PASSWORD_HASH = "integration-password-hash"
    settings.TWICC_DEV_LOCAL_BYPASS = False

    def browser():
        client = Client()
        auth = client.session
        bind_session(auth, settings.TWICC_PASSWORD_HASH)
        auth.save()
        return client

    def hydrate(client=None):
        response = (client or browser()).get(
            f"/api/projects/{project.id}/sessions/{session.id}/async-questions/?after=9999"
        )
        assert response.status_code == 200
        return response.json()

    def ingest(values):
        with path.open("ab") as rollout:
            for value in values:
                rollout.write(orjson.dumps(value) + b"\n")
        result = get_compute().sync_session_slice(session.id, path, limits=LiveSyncLimits())
        assert result.lines_processed == len(values)
        assert not result.has_more
        return service.read_question_snapshot(session.id)

    async def send(text="", payload=None, *, origin="human", request_id="send-1", images=None):
        clock.second = 30
        return await manager.send_to_session(session.id, project.id, str(tmp_path), text,
                                             AgentSettings(permission_mode="yolo"), async_questions=payload,
                                             send_origin=origin, request_id=request_id, images=images)

    def run(callback):
        async def execute():
            db_writer.start_db_writer()
            try:
                return await callback()
            finally:
                await cleanup(agent)
                await db_writer.stop_db_writer()
        return asyncio.run(execute())

    return SimpleNamespace(session=session, path=path, agent=agent, manager=manager, sdk=sdk, thread=thread,
                           turn=turn, layer=layer, clock=clock, ingest=ingest, send=send, run=run,
                           browser=browser, hydrate=hydrate)


def structured(snapshot, *, answer=True):
    return {"revision": snapshot["revision"], "batch_ids": [batch["item_id"] for batch in snapshot["batches"]],
            "answers": [{"item_id": OBSERVED[0][0], "index": 0, "kind": "option",
                         "value": OBSERVED[0][2][0]}] if answer else []}


def test_recorded_questions_hydrate_and_send_partial_answers_text_and_attachment_once(harness, monkeypatch):
    from twicc import asgi
    from twicc.asgi import WSConsumer

    records = [
        canonical_question(),
        {"timestamp": "2026-10-05T09:12:02Z", "type": "response_item",
         "payload": {"type": "function_call", "call_id": "tool-1", "name": "exec_command", "arguments": "{}"}},
        {"timestamp": "2026-10-05T09:12:03Z", "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "tool-1", "output": "done"}},
        record("item_completed", second=4, item={"type": "AgentMessage", "id": "final",
                                                "phase": "final_answer", "content": [{"type": "Text",
                                                "text": "A long final response. " * 100}]}),
    ]
    assert harness.ingest(records)["batches"][0]["status"] == "collecting"
    harness.ingest([record("task_complete", second=5), canonical_question(1, turn="t2", second=6),
                    record("task_complete", turn="t2", second=7)])
    ready = harness.hydrate()
    assert [(batch["item_id"], batch["status"]) for batch in ready["batches"]] == [
        (OBSERVED[0][0], "ready"), (OBSERVED[1][0], "ready"),
    ]
    original = list(SessionItem.objects.filter(session=harness.session).order_by("line_num")
                    .values_list("content", flat=True))
    image = {"source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}}
    expected = ("Answers to your questions:\n\nQuestion: " + OBSERVED[0][1] + "\nAnswer: " + OBSERVED[0][2][0]
                + "\n\nAdditional message:\nLimit the tooltip width to 24rem.")
    monkeypatch.setattr(asgi, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr(asgi, "get_project_directory", AsyncMock(return_value=harness.session.project.directory))
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(get=lambda *_: harness.manager))
    helpers = SimpleNamespace(resolve_agent_settings=lambda value: value,
                              enforce_agent_settings_consistency=lambda value: value)
    monkeypatch.setattr(asgi, "get_provider_helpers", lambda *_: helpers)
    consumer = WSConsumer()
    consumer.send_json = AsyncMock()
    consumer.channel_layer = SimpleNamespace(group_send=AsyncMock())
    harness.clock.second = 30
    assert harness.run(lambda: consumer._handle_send_message_admitted({
        "session_id": harness.session.id, "project_id": harness.session.project_id, "request_id": "send-1",
        "text": "Limit the tooltip width to 24rem.", "async_questions": structured(ready), "images": [image],
        "send_origin": "internal", "permission_mode": "yolo",
    }))
    consumer.send_json.assert_awaited_once_with({"type": "send_ack", "request_id": "send-1",
                                                "session_id": harness.session.id})
    harness.thread.turn_with_policy.assert_awaited_once()
    call = harness.thread.turn_with_policy.await_args
    assert call.kwargs["client_user_message_id"] == "send-1"
    assert call.args[0][0].url == "data:image/png;base64,YWJj"
    assert call.args[0][1].text == expected
    assert OBSERVED[1][1] not in expected
    resolved = harness.hydrate()
    assert resolved["batches"] == []
    assert resolved["resolutions"] == {item[0]: {"status": "sent", "request_id": "send-1"} for item in OBSERVED}
    harness.ingest([source_user(expected, request_id="send-1")])
    assert list(SessionItem.objects.filter(session=harness.session).order_by("line_num")
                .values_list("content", flat=True))[:-1] == original
    assert projection(recompute(harness.session)) == projection(resolved)
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert (data["source_item_id"], data["delivery_route"], data["status"]) == ("u1", "start", "accepted")
    assert data["origin"] == "human"


@pytest.mark.parametrize("continuation", [False, True])
def test_sdk_and_canonical_ingestion_wait_for_real_control_return_before_subagent_hold(harness, continuation):
    async def run():
        await harness.agent._link_async_question_turn("t1")
        await harness.agent._handle_stream_event(sdk_question(started=True))
        assert (await sync_to_async(service.read_question_snapshot)(harness.session.id))["batches"] == []
        await harness.agent._handle_stream_event(sdk_question())
        collecting = await sync_to_async(harness.ingest)([canonical_question(), record("task_complete", second=2)])
        assert collecting["batches"][0]["status"] == "collecting"
        first = Turn("t1", [])
        first.events, first.finished = [sdk_question()], asyncio.Event()
        first.finished.set()
        harness.agent._set_state(AgentState.ASSISTANT_TURN)

        async def hold():
            ready = await sync_to_async(service.read_question_snapshot)(harness.session.id)
            assert ready["batches"][0]["status"] == "ready"
            return True

        harness.agent._try_arm_subagent_hold = hold
        if continuation:
            harness.agent._auto_review_retry_after_turn = True
            second = Turn("t2", [])
            second.finished.set()

            async def open_successor(*args, **kwargs):
                before = await sync_to_async(service.read_question_snapshot)(harness.session.id)
                assert before["batches"][0]["status"] == "collecting"
                await sync_to_async(harness.ingest)([record("task_complete", turn="t2", second=4)])
                assert (await sync_to_async(service.read_question_snapshot)(harness.session.id))["batches"][0][
                    "status"] == "collecting"
                return second

            harness.thread.turn_with_policy.side_effect = open_successor
        await harness.agent._run_turn("", None, turn_handle=first)
        assert harness.agent.state == AgentState.ASSISTANT_TURN

    harness.run(run)
    before = harness.hydrate()
    assert len(before["batches"]) == 1
    assert before["batches"][0]["line"] == 1
    assert before["batches"][0]["status"] == "ready"
    assert projection(recompute(harness.session)) == projection(before)


@pytest.mark.parametrize("text,before_completion,expected", [
    ("Continue working", True, "ready"),
    ("Yes, keep it", False, "sent"),
    ("/compact", False, "ready"),
    ("/goal clear", False, "ready"),
    ("<twicc-resume>Continue</twicc-resume>", False, "collecting"),
    ("Message Type: MESSAGE\nTask name: /root\nSender: /root/worker\nPayload:\nDone", False, "ready"),
])
def test_incremental_and_cold_recompute_apply_the_same_source_submission_boundary(
    harness, text, before_completion, expected,
):
    values = [canonical_question()]
    if before_completion:
        # Source lines establish order even when provider timestamps differ.
        values += [user(text, turn="t1", second=9), record("task_complete", second=3)]
    else:
        values += [record("task_complete", second=2), user(text, second=3)]
    live = harness.ingest(values)
    batch_id = OBSERVED[0][0]
    if expected in {"ready", "collecting"}:
        assert live["batches"][0]["status"] == expected
    else:
        assert live["batches"] == []
        assert live["resolutions"][batch_id]["status"] == expected
    AsyncQuestionState.objects.filter(session=harness.session).delete()
    assert projection(recompute(harness.session)) == projection(live)
    assert projection(harness.hydrate()) == projection(live)


@pytest.mark.parametrize("origin", ["agent", "internal"])
def test_native_source_association_preserves_private_nonhuman_origin_through_full_recompute(harness, origin):
    harness.ingest([canonical_question(), record("task_complete", second=2)])
    assert harness.run(lambda: harness.send("Ordinary model-visible text", origin=origin))
    assert harness.thread.turn_with_policy.await_args.args[0][0].text == "Ordinary model-visible text"
    harness.ingest([source_user("Ordinary model-visible text", request_id="send-1")])
    before = harness.hydrate()
    assert before["batches"][0]["status"] == "ready"
    assert before["resolutions"] == {}
    assert projection(recompute(harness.session)) == projection(before)
    facts = AsyncQuestionState.objects.get(session=harness.session).state["facts"]
    submission = facts["send:send-1"]["data"]
    assert submission["origin"] == origin
    assert submission["origin_source"] == "live"
    assert submission["source_item_id"] == "u1"
    assert submission["boundary"]["batch_ids"] == [OBSERVED[0][0]]


def test_late_canonical_question_after_accepted_reply_does_not_reappear_on_recompute(harness):
    # Historical import can expose completion before its earlier question metadata.
    SessionItem.objects.create(session=harness.session, line_num=2,
                               content=orjson.dumps(record("task_complete", second=2)).decode())
    assert recompute(harness.session)["batches"] == []
    assert harness.run(lambda: harness.send("A natural answer"))
    submission = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert submission["boundary"]["batch_ids"] == []
    assert submission["boundary"]["settled_turn_ids"] == ["t1"]
    harness.run(lambda: harness.agent._handle_stream_event(sdk_question()))
    assert harness.hydrate()["resolutions"] == {OBSERVED[0][0]: {"status": "sent", "request_id": "send-1"}}
    SessionItem.objects.create(session=harness.session, line_num=3,
                               content=orjson.dumps(source_user("A natural answer", request_id="send-1")).decode())
    SessionItem.objects.create(session=harness.session, line_num=1, content=orjson.dumps(canonical_question()).decode())
    late = recompute(harness.session)
    assert late["batches"] == []
    assert late["resolutions"] == {OBSERVED[0][0]: {"status": "sent", "request_id": "send-1"}}
    assert projection(recompute(harness.session)) == projection(late)
    assert harness.thread.turn_with_policy.await_count == 1


@pytest.mark.parametrize("resolution", ["sent", "dismissed"])
def test_second_browser_resolution_remains_correlated_after_recompute_and_rejects_stale_send(harness, resolution):
    harness.ingest([canonical_question(), record("task_complete", second=2)])
    first_browser, second_browser = harness.browser(), harness.browser()
    presented = harness.hydrate(first_browser)
    if resolution == "sent":
        assert harness.run(lambda: harness.send("Natural reply from another browser", request_id="external"))
    else:
        harness.run(lambda: harness.manager.dismiss_async_question(harness.session.id, OBSERVED[0][0],
                                                                  request_id="external"))
        harness.thread.turn_with_policy.assert_not_awaited()
    expected = {OBSERVED[0][0]: {"status": resolution, "request_id": "external"}}
    assert harness.hydrate(second_browser)["resolutions"] == expected
    assert harness.hydrate(first_browser)["resolutions"] == expected
    assert recompute(harness.session)["resolutions"] == expected
    delivered = harness.thread.turn_with_policy.await_count
    with pytest.raises(SendDeliveryError) as failure:
        harness.run(lambda: harness.send("Keep this draft", structured(presented), request_id="stale"))
    assert failure.value.code == "async_questions_stale"
    assert harness.thread.turn_with_policy.await_count == delivered
    frames = [call.args[1]["data"] for call in harness.layer.group_send.await_args_list]
    assert any(frame["snapshot"]["resolutions"] == expected for frame in frames)


def test_uncertain_combined_send_recovers_native_acceptance_without_reformatting_or_redelivery(harness):
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])
    harness.thread.turn_with_policy.side_effect = TimeoutError("Provider reply lost")
    response = structured(ready)
    with pytest.raises(SendDeliveryError) as failure:
        harness.run(lambda: harness.send("Keep draft text", response))
    assert failure.value.code == "send_uncertain"
    before = harness.hydrate()
    assert before["batches"][0]["status"] == "ready"
    stored = deepcopy(AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"])
    assert stored["data"]["status"] == "uncertain"
    final_text = harness.thread.turn_with_policy.await_args.args[0][0].text
    assert final_text.count("Answers to your questions:") == 1
    harness.ingest([source_user(final_text, request_id="send-1")])
    assert harness.run(lambda: harness.send("Keep draft text", response))
    assert harness.thread.turn_with_policy.await_count == 1
    accepted = harness.hydrate()
    assert accepted["resolutions"] == {OBSERVED[0][0]: {"status": "sent", "request_id": "send-1"}}
    assert projection(recompute(harness.session)) == projection(accepted)
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert data["boundary"] == stored["data"]["boundary"]
    assert data["text"] == final_text
    assert data["status"] == "accepted"


def test_question_generated_during_older_send_remains_ready_after_acceptance_and_recompute(harness):
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])

    async def delayed_delivery(*args, **kwargs):
        prepared = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
        assert prepared["facts"]["send:send-1"]["data"]["status"] == "prepared"
        await db_writer.run_under_db_write_lock(lambda: sync_to_async(harness.ingest)([
            canonical_question(1, turn="reply", second=40), record("task_complete", turn="reply", second=41),
        ]))
        return harness.turn

    harness.thread.turn_with_policy.side_effect = delayed_delivery
    assert harness.run(lambda: harness.send("Answer the first question", structured(ready)))
    collecting = harness.hydrate()
    assert collecting["batches"][0]["status"] == "collecting"
    assert [batch["item_id"] for batch in collecting["batches"]] == [OBSERVED[1][0]]
    assert collecting["resolutions"] == {OBSERVED[0][0]: {"status": "sent", "request_id": "send-1"}}

    async def settle():
        await harness.agent._settle_async_questions("reply", outcome="completed")

    harness.run(settle)
    after = harness.hydrate()
    assert after["batches"][0]["status"] == "ready"
    assert projection(recompute(harness.session)) == projection(after)


def test_definite_sdk_rejection_preserves_ready_batches_and_submission_boundary(harness):
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])
    harness.thread.turn_with_policy.side_effect = JsonRpcError(-32602, "Invalid input")
    with pytest.raises(JsonRpcError):
        harness.run(lambda: harness.send("Keep draft text", structured(ready)))
    after = harness.hydrate()
    assert after["resolutions"] == {}
    assert after["batches"][0]["status"] == "ready"
    assert projection(recompute(harness.session)) == projection(after)
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert data["status"] == "rejected"
    assert data["boundary"]["batch_ids"] == [OBSERVED[0][0]]
    assert data["text"].endswith("Additional message:\nKeep draft text")


@pytest.mark.parametrize("mode", ["answers", "text", "attachment"])
def test_optional_answers_use_the_normal_send_path_and_retire_ready_batches(harness, mode):
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])
    image = {"source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}}
    assert harness.run(lambda: harness.send(
        "A natural answer" if mode == "text" else "", structured(ready, answer=mode == "answers"),
        images=[image] if mode == "attachment" else None,
    ))
    harness.thread.turn_with_policy.assert_awaited_once()
    inputs = harness.thread.turn_with_policy.await_args.args[0]
    if mode == "text":
        assert inputs[0].text == "A natural answer"
    elif mode == "answers":
        assert inputs[0].text == f"Answers to your questions:\n\nQuestion: {OBSERVED[0][1]}\nAnswer: {OBSERVED[0][2][0]}"
    else:
        assert len(inputs) == 1
        assert inputs[0].url == "data:image/png;base64,YWJj"
    resolved = harness.hydrate()
    assert resolved["batches"] == []
    assert resolved["resolutions"] == {OBSERVED[0][0]: {"status": "sent", "request_id": "send-1"}}
    assert projection(recompute(harness.session)) == projection(resolved)


def test_disabled_widget_hydration_keeps_canonical_transcript_and_recompute_state(harness):
    harness.session.question_widget = False
    harness.session.save(update_fields=["question_widget"])
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])
    assert ready["widget_enabled"] is False
    assert ready["batches"][0]["status"] == "ready"
    assert harness.hydrate() == ready
    assert projection(recompute(harness.session)) == projection(ready)
    source = orjson.loads(SessionItem.objects.get(session=harness.session, line_num=1).content)
    assert source == canonical_question()

@pytest.mark.parametrize("outcome", ["interrupted", "failed", "unknown"])
def test_goal_records_terminal_physical_outcome(harness, outcome):
    from openai_codex.generated.v2_all import TurnCompletedNotification

    harness.ingest([canonical_question()])

    class Monitor:
        async def stream(self):
            if outcome == "unknown":
                return
            yield Notification("turn/completed", TurnCompletedNotification.model_validate({
                "threadId": harness.session.id,
                "turn": {"id": "t1", "status": outcome, "items": [], "itemsView": "full"},
            }))

        def close(self):
            pass

    async def run():
        await harness.agent._admit_async_question_owner()
        monitor = Monitor()
        harness.agent._goal_monitor = monitor
        await harness.agent._run_goal_continuation(monitor)

    harness.run(run)
    state = AsyncQuestionState.objects.get(session=harness.session).state
    returned = next(fact for fact in state["facts"].values() if fact["kind"] == "control_return")
    assert returned["data"]["outcome"] == outcome

@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("delivery", ["rejected", "accepted", "uncertain", "not_admitted"])
def test_browser_request_reconciliation_never_redelivers(harness, monkeypatch, delivery, cold):
    monkeypatch.setattr("twicc.views.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda _: harness.manager))
    ready = harness.ingest([canonical_question(), record("task_complete", second=2)])
    payload = structured(ready)
    if delivery == "rejected":
        harness.thread.turn_with_policy.side_effect = JsonRpcError(-32602, "Invalid input")
        with pytest.raises(JsonRpcError):
            harness.run(lambda: harness.send("Keep text", payload))
    elif delivery in {"accepted", "uncertain"}:
        harness.thread.turn_with_policy.side_effect = TimeoutError("Lost reply")
        with pytest.raises(SendDeliveryError):
            harness.run(lambda: harness.send("Keep text", payload))
        # Read-only native history is the only acceptance evidence.
        item = SimpleNamespace(model_dump=lambda **_: {
            "type": "userMessage", "id": "native-input", "clientId": "send-1",
            "content": [{"type": "text", "text": "Unrelated text is not an acceptance key"}],
        })
        harness.sdk._client.thread_read = AsyncMock(return_value=SimpleNamespace(thread=SimpleNamespace(
            turns=[SimpleNamespace(id="reply", items=[item] if delivery == "accepted" else [])],
        )))
    count = harness.thread.turn_with_policy.await_count
    owners_before = {key for key in AsyncQuestionState.objects.get(session=harness.session).state["facts"]
                     if key.startswith("owner:")}
    original_start, original_admit = CodexAgent.start, CodexAgent._admit_async_question_owner
    if cold:
        from tests.test_codex_async_question_send import cold_reconciliation_client
        harness.manager._agents.clear()
        harness.sdk.close = AsyncMock()
        cold_reconciliation_client(monkeypatch, harness.sdk)
    client = harness.browser()

    async def reconcile():
        return await sync_to_async(client.post)(
            f"/api/projects/{harness.session.project_id}/sessions/{harness.session.id}/async-questions/reconcile/",
            data=orjson.dumps({"request_ids": ["send-1"]}), content_type="application/json",
        )

    response = harness.run(reconcile)
    assert response.status_code == 200
    assert response.json()["requests"] == {"send-1": delivery}
    assert harness.thread.turn_with_policy.await_count == count
    assert {key for key in AsyncQuestionState.objects.get(session=harness.session).state["facts"]
            if key.startswith("owner:")} == owners_before
    if cold:
        assert harness.manager._agents == {}
        if delivery in {"accepted", "uncertain"}:
            harness.sdk.close.assert_awaited_once()
            harness.sdk._ensure_initialized.assert_awaited_once()
        else:
            harness.sdk.close.assert_not_awaited()
    if delivery == "accepted":
        assert response.json()["snapshot"]["resolutions"][OBSERVED[0][0]]["request_id"] == "send-1"
    else:
        assert response.json()["snapshot"]["batches"][0]["status"] == "ready"
    if delivery == "not_admitted":
        recompute(harness.session)
        # A delayed original socket frame cannot race a fresh explicit Retry.
        with pytest.raises(SendDeliveryError) as failure:
            harness.run(lambda: harness.send("Keep text", payload))
        assert failure.value.code == "async_questions_not_admitted"
        assert harness.thread.turn_with_policy.await_count == 0
        if cold:
            monkeypatch.setattr(CodexAgent, "start", original_start)
            monkeypatch.setattr(CodexAgent, "_admit_async_question_owner", original_admit)
            harness.manager._agents[harness.session.id] = harness.agent
        assert harness.run(lambda: harness.send("Keep text", payload, request_id="fresh-retry"))
        assert harness.thread.turn_with_policy.await_count == 1
