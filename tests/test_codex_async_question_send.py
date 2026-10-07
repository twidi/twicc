"""Combined Codex sends preserve durable admission and private provenance."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async

from twicc.agent import AgentState, SendDeliveryError
from twicc.core.enums import Provider
from twicc.core.models import AsyncQuestionState, Project, Session
from twicc.core.services import async_questions as service
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.async_questions import QuestionFact
from twicc.providers.helpers import AgentSettings

pytestmark = pytest.mark.django_db(transaction=True)


def question(item_id="q1", turn="t1", at="2026-10-05T10:00:01Z"):
    return QuestionFact(
        f"question:{item_id}",
        "question",
        at,
        turn,
        item_id,
        None,
        {
            "questions": [{"index": 0, "title": "Open the session?", "options": ["Yes", "Non"]}],
        },
    )


def response(value="Yes"):
    return {
        "revision": 1,
        "batch_ids": ["q1"],
        "answers": [
            {"item_id": "q1", "index": 0, "kind": "option", "value": value},
        ],
    }


@pytest.fixture
def harness(monkeypatch):
    project = Project.objects.create(id="send-project", directory="/tmp")
    session = Session.objects.create(
        id="send-session", project=project, provider=Provider.CODEX, file_path="send.jsonl"
    )
    service.merge_question_facts(
        session.id,
        [
            question(),
            QuestionFact(
                "end:t1",
                "turn_end",
                "2026-10-05T10:00:02Z",
                "t1",
                None,
                None,
                {},
            ),
        ],
    )
    agent = SimpleNamespace(
        state=AgentState.USER_TURN,
        agent_settings=AgentSettings(),
        apply_agent_settings=AsyncMock(),
        send=AsyncMock(return_value=True),
    )
    manager = CodexAgentManager()
    manager._agents[session.id] = agent
    monkeypatch.setattr(manager, "_check_ephemeral_readonly", lambda *_: None)

    async def send(text="", payload=None, origin="human", request_id="send-1", images=None):
        from twicc.providers import db_writer

        db_writer.start_db_writer()
        try:
            return await manager.send_to_session(
                session.id,
                project.id,
                "/tmp",
                text,
                AgentSettings(),
                async_questions=payload,
                request_id=request_id,
                send_origin=origin,
                images=images,
            )
        finally:
            await db_writer.stop_db_writer()

    return SimpleNamespace(session=session, agent=agent, manager=manager, send=send)


def snapshot(harness):
    return service.read_question_snapshot(harness.session.id)


def test_answer_only_is_a_real_message(harness):
    assert asyncio.run(harness.send(payload=response())) is True
    assert (
        harness.agent.send.await_args.args[0]
        == "::: Answers to your questions\n\n**Question:** Open the session?\n\n**Answer:** Yes\n\n:::"
    )
    assert snapshot(harness)["resolutions"]["q1"]["status"] == "sent"


def test_combined_send_retains_source_language_and_attachment(harness):
    image = {"source": {"data": "abc"}}
    asyncio.run(harness.send("Gardez ceci.", response("Non"), images=[image]))
    assert harness.agent.send.await_count == 1
    assert harness.agent.send.await_args.args[0].endswith("**Answer:** Non\n\n:::\n\nGardez ceci.")
    assert harness.agent.send.await_args.kwargs["images"] == [image]


@pytest.mark.parametrize("origin", ["human", "agent", "internal"])
@pytest.mark.parametrize("attachment", [False, True])
def test_plain_sends_capture_origin_and_only_human_retires(harness, origin, attachment):
    asyncio.run(harness.send("" if attachment else "next", origin=origin, images=[{}] if attachment else None))
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert data["origin"] == origin
    assert data["boundary"]["line"] is None
    assert bool(snapshot(harness)["resolutions"]) == (origin == "human")


@pytest.mark.parametrize("command", ["/compact", "/plan", "/goal objective"])
def test_answers_plus_raw_command_reject_before_delivery(harness, command):
    harness.manager._dispatch_hardcoded_command = AsyncMock()
    with pytest.raises(SendDeliveryError) as error:
        asyncio.run(harness.send(command, response()))
    assert error.value.code == "async_questions_command"
    assert snapshot(harness)["batches"][0]["status"] == "ready"
    harness.agent.send.assert_not_awaited()


def test_commands_and_settings_do_not_record_boundary(harness):
    harness.manager._dispatch_hardcoded_command = AsyncMock()
    asyncio.run(harness.send("/compact"))
    asyncio.run(harness.send())
    facts = AsyncQuestionState.objects.get(session=harness.session).state["facts"]
    assert "send:send-1" not in facts
    assert snapshot(harness)["batches"][0]["status"] == "ready"


@pytest.mark.parametrize("failure", [False, SendDeliveryError("busy", code="agent_starting")])
def test_definite_rejection_keeps_ready(harness, failure):
    if failure is False:
        harness.agent.send.return_value = False
    else:
        harness.agent.send.side_effect = failure
    if failure:
        with pytest.raises(SendDeliveryError):
            asyncio.run(harness.send("next", response()))
    else:
        assert asyncio.run(harness.send("next", response())) is False
    assert snapshot(harness)["batches"][0]["status"] == "ready"
    assert (
        AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]["status"]
        == "rejected"
    )


def test_uncertain_delivery_prohibits_replay_and_native_evidence_recovers(harness):
    harness.agent.send.side_effect = TimeoutError("delivery unknown")
    for _ in range(2):
        with pytest.raises(SendDeliveryError) as error:
            asyncio.run(harness.send("next", response()))
        assert error.value.code == "send_uncertain"
    assert harness.agent.send.await_count == 1
    service.merge_question_facts(
        harness.session.id,
        [
            QuestionFact(
                "user:u1",
                "user_submission",
                "2026-10-05T11:00:00Z",
                "t2",
                "u1",
                100,
                {"client_message_id": "send-1", "origin": "human", "status": "accepted"},
            )
        ],
    )
    assert asyncio.run(harness.send("next", response())) is True
    assert harness.agent.send.await_count == 1
    assert snapshot(harness)["resolutions"]["q1"]["status"] == "sent"


def test_question_created_during_delivery_remains_ready(harness):
    async def delivery(*args, **kwargs):
        await sync_to_async(service.merge_question_facts)(
            harness.session.id,
            [
                question("q2", "t2", "2099-01-01T00:00:00Z"),
                QuestionFact("end:t2", "turn_end", "2099-01-01T00:00:01Z", "t2", None, None, {}),
            ],
        )
        return True

    harness.agent.send.side_effect = delivery
    asyncio.run(harness.send("next", response()))
    assert [batch["item_id"] for batch in snapshot(harness)["batches"]] == ["q2"]


def test_partial_answers_resolve_presented_batches(harness):
    facts = AsyncQuestionState.objects.get(session=harness.session).state
    first = facts["facts"]["question:q1"]
    first["data"]["questions"].append({"index": 1, "title": "Keep it?", "options": ["Oui", "Non"]})
    # Prepare the source batch before testing optional second answers.
    AsyncQuestionState.objects.filter(session=harness.session).delete()
    service.merge_question_facts(
        harness.session.id,
        [
            QuestionFact(**first),
            QuestionFact(
                "end:t1",
                "turn_end",
                "2026-10-05T10:00:02Z",
                "t1",
                None,
                None,
                {},
            ),
        ],
    )
    asyncio.run(harness.send(payload=response()))
    assert "Keep it?" not in harness.agent.send.await_args.args[0]
    assert snapshot(harness)["resolutions"]["q1"]["status"] == "sent"


def test_stale_answer_rejects_without_send(harness):
    service.dismiss_question_batch(harness.session.id, "q1", request_id="dismiss")
    with pytest.raises(SendDeliveryError) as error:
        asyncio.run(harness.send("keep draft", response()))
    assert error.value.code == "async_questions_stale"
    harness.agent.send.assert_not_awaited()


def test_bookkeeping_failure_after_delivery_remains_uncertain(harness, monkeypatch):
    original = service.accept_question_send

    def outcome(session_id, submission):
        if submission["status"] == "accepted":
            raise OSError("bookkeeping unavailable")
        return original(session_id, submission)

    monkeypatch.setattr(service, "accept_question_send", outcome)
    with pytest.raises(SendDeliveryError) as error:
        asyncio.run(harness.send("next", response()))
    assert error.value.code == "send_uncertain"
    assert harness.agent.send.await_count == 1
    assert snapshot(harness)["batches"][0]["status"] == "ready"


@pytest.mark.parametrize("provider,existing", [("codex", False), ("claude_code", True)])
def test_ws_rejects_structured_fields_on_draft_and_other_provider(harness, monkeypatch, provider, existing):
    from twicc.asgi import WSConsumer
    from twicc import asgi

    monkeypatch.setattr(asgi, "get_session_provider", AsyncMock(return_value=provider if existing else None))
    consumer = WSConsumer()
    consumer.send_json = AsyncMock()
    asyncio.run(
        consumer._handle_send_message_admitted(
            {
                "session_id": harness.session.id,
                "project_id": harness.session.project_id,
                "provider": provider,
                "text": "next",
                "request_id": "browser-1",
                "async_questions": response(),
            }
        )
    )
    frame = consumer.send_json.await_args.args[0]
    assert frame["code"] == "async_questions_invalid"
    assert frame["request_id"] == "browser-1"
    assert frame["session_id"] == harness.session.id


def test_ws_answer_only_passes_raw_text_and_trusted_origin(harness, monkeypatch):
    from twicc.asgi import WSConsumer
    from twicc import asgi

    monkeypatch.setattr(asgi, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr(asgi, "get_project_directory", AsyncMock(return_value="/tmp"))
    # Settings-only early return must recognize selected answers without a live process.
    manager = SimpleNamespace(get_agent_info=lambda *_: None, send_to_session=AsyncMock(return_value=True))
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(get=lambda *_: manager))
    helpers = SimpleNamespace(resolve_agent_settings=lambda s: s, enforce_agent_settings_consistency=lambda s: s)
    monkeypatch.setattr(asgi, "get_provider_helpers", lambda *_: helpers)
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        consumer.channel_layer = SimpleNamespace(group_send=AsyncMock())
        try:
            await consumer._handle_send_message_admitted(
                {
                    "session_id": harness.session.id,
                    "project_id": harness.session.project_id,
                    "text": "",
                    "request_id": "browser-1",
                    "async_questions": response(),
                    "send_origin": "agent",
                }
            )
        finally:
            await db_writer.stop_db_writer()
        return consumer

    consumer = asyncio.run(run())
    assert manager.send_to_session.await_args.args[3] == ""
    assert manager.send_to_session.await_args.kwargs["async_questions"] == response()
    assert manager.send_to_session.await_args.kwargs["send_origin"] == "human"
    assert manager.send_to_session.await_args.kwargs["request_id"] == "browser-1"
    assert consumer.send_json.await_args.args[0]["type"] == "send_ack"


def runtime_agent(harness):
    from openai_codex import TextInput
    from twicc.providers.codex.agent.agent import CodexAgent

    sdk = SimpleNamespace(
        _client=SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None)), _ensure_initialized=AsyncMock()
    )
    turn = SimpleNamespace(id="physical-turn", thread_id=harness.session.id, _codex=sdk)
    turn.finished = asyncio.Event()

    async def stream():
        await turn.finished.wait()
        if False:
            yield

    turn.stream = stream
    thread = SimpleNamespace(id=harness.session.id, turn_with_policy=AsyncMock(return_value=turn))
    agent = CodexAgent(
        harness.session.id, harness.session.project_id, "/tmp", AgentSettings(permission_mode="yolo"), sdk, thread
    )
    agent._set_state(AgentState.USER_TURN)
    agent._notify_state_change = AsyncMock()
    agent._broadcast_stream_event = AsyncMock()
    agent._handle_error = AsyncMock()
    agent._build_turn_input = AsyncMock(return_value=[TextInput("next")])
    agent.apply_agent_settings = AsyncMock()
    harness.manager._agents[harness.session.id] = agent
    return agent, sdk, thread, turn


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "uncertain"])
def test_background_start_keeps_prepared_until_provider_outcome(harness, outcome):
    from openai_codex.errors import JsonRpcError
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        entered, release = asyncio.Event(), asyncio.Event()

        async def open_turn(*args, **kwargs):
            entered.set()
            await release.wait()
            if outcome == "rejected":
                raise JsonRpcError(-32602, "bad input")
            if outcome == "uncertain":
                raise TimeoutError("unknown delivery")
            return turn

        thread.turn_with_policy.side_effect = open_turn
        try:
            sending = asyncio.create_task(
                harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="background-1",
                    send_origin="human",
                    async_questions=response(),
                )
            )
            await entered.wait()
            assert not sending.done()
            before = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
            assert before["facts"]["send:background-1"]["data"]["status"] == "prepared"
            assert before["batches"]["q1"]["status"] == "ready"
            boundary = before["facts"]["send:background-1"]["data"]["boundary"]
            release.set()
            if outcome == "accepted":
                assert await sending is True
                await asyncio.wait_for(agent._current_turn_ready.wait(), 1)
            else:
                with pytest.raises(Exception) as failure:
                    await sending
                if outcome == "uncertain":
                    assert failure.value.code == "send_uncertain"
                await agent._turn_task
            after = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
            data = after["facts"]["send:background-1"]["data"]
            assert data["status"] == outcome
            assert data["boundary"] == boundary
            assert thread.turn_with_policy.await_args.kwargs["client_user_message_id"] == "background-1"
            assert after["batches"]["q1"]["status"] == ("sent" if outcome == "accepted" else "ready")
            if outcome != "accepted":
                agent._handle_error.assert_awaited_once()
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.parametrize("route", ["steer", "goal_steer", "fallback_start", "fallback_steer"])
@pytest.mark.parametrize("text", ["", "next"])
def test_live_routes_keep_one_native_client_id(harness, route, text):
    from openai_codex.errors import JsonRpcError
    from openai_codex.generated.v2_all import TurnSteerResponse
    from twicc.providers import db_writer

    # No terminal evidence exists: completed questions must send while active.
    AsyncQuestionState.objects.filter(session=harness.session).delete()
    service.merge_question_facts(harness.session.id, [question()])

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        agent._set_state(AgentState.ASSISTANT_TURN)
        agent._current_turn = turn
        agent._current_turn_ready.set()
        ids = []

        async def request(method, params, **kwargs):
            assert method == "turn/steer"
            ids.append(params["clientUserMessageId"])
            if route.startswith("fallback") and len(ids) == 1:
                raise JsonRpcError(-32600, "no active turn to steer")
            return TurnSteerResponse(turn_id="physical-turn")

        sdk._client.request = request
        if route == "goal_steer":

            async def goal(items, *, client_user_message_id):
                ids.append(client_user_message_id)
                return TurnSteerResponse(turn_id="physical-turn")

            agent._goal_monitor = SimpleNamespace(steer=goal)
        if route == "fallback_steer":
            thread.turn_with_policy.side_effect = JsonRpcError(-32600, "turn busy")
        try:
            assert (
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    text,
                    AgentSettings(),
                    request_id="route-1",
                    send_origin="human",
                    async_questions=response(),
                )
                is True
            )
            captured = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
            assert captured["batches"]["q1"]["status"] == "sent"
            assert captured["facts"]["send:route-1"]["data"]["text"].count("Answers to your questions") == 1
            assert ids and set(ids) == {"route-1"}
            if route.startswith("fallback"):
                assert thread.turn_with_policy.await_args.kwargs["client_user_message_id"] == "route-1"
            data = await sync_to_async(
                lambda: AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:route-1"]["data"]
            )()
            assert data["status"] == "accepted"
            assert data["target_turn_id"] == "physical-turn"
            assert data["delivery_route"] == (
                "start" if route == "fallback_start" else "steer" if route == "fallback_steer" else route
            )
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


def test_dismissal_waits_for_send_delivery_gate(harness):
    from twicc.providers import db_writer

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def deliver(*args, **kwargs):
            entered.set()
            await release.wait()
            return True

        harness.agent.send.side_effect = deliver
        db_writer.start_db_writer()
        try:
            send = asyncio.create_task(
                harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="gate-1",
                    send_origin="human",
                    async_questions=response(),
                )
            )
            await entered.wait()
            dismiss = asyncio.create_task(
                harness.manager.dismiss_async_question(
                    harness.session.id,
                    "q1",
                    request_id="dismiss-1",
                )
            )
            await asyncio.sleep(0)
            assert not dismiss.done()
            release.set()
            assert await send is True
            resolved = await dismiss
            assert resolved["resolutions"]["q1"] == {"status": "sent", "request_id": "gate-1"}
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())


def test_background_delivery_keeps_send_gate_until_native_acceptance(harness):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        entered, release = asyncio.Event(), asyncio.Event()

        async def open_turn(*args, **kwargs):
            entered.set()
            await release.wait()
            return turn

        thread.turn_with_policy.side_effect = open_turn
        send = asyncio.create_task(
            harness.manager.send_to_session(
                harness.session.id,
                harness.session.project_id,
                "/tmp",
                "next",
                AgentSettings(),
                request_id="scheduled-gate",
                send_origin="human",
                async_questions=response(),
            )
        )
        dismiss = None
        try:
            await entered.wait()
            assert not send.done(), "Scheduling alone must not acknowledge provider delivery"
            dismiss = asyncio.create_task(
                harness.manager.dismiss_async_question(harness.session.id, "q1", request_id="d")
            )
            await asyncio.sleep(0)
            assert not dismiss.done()
            release.set()
            assert await send is True
            resolved = await dismiss
            assert resolved["resolutions"]["q1"] == {"status": "sent", "request_id": "scheduled-gate"}
        finally:
            release.set()
            if dismiss:
                await asyncio.gather(dismiss, return_exceptions=True)
            await asyncio.gather(send, return_exceptions=True)
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_before_start", [True, False])
def test_cancelled_scheduled_send_settles_future_and_releases_gate(harness, cancel_before_start):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        entered = asyncio.Event()

        async def open_turn(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        thread.turn_with_policy.side_effect = open_turn
        if cancel_before_start:
            original = agent._schedule_turn

            def schedule(*args, **kwargs):
                original(*args, **kwargs)
                agent._turn_task.cancel()

            agent._schedule_turn = schedule
        sending = asyncio.create_task(
            harness.manager.send_to_session(
                harness.session.id,
                harness.session.project_id,
                "/tmp",
                "next",
                AgentSettings(),
                request_id="cancelled-1",
                send_origin="human",
                async_questions=response(),
            )
        )
        try:
            if cancel_before_start:
                with pytest.raises(SendDeliveryError) as error:
                    await asyncio.wait_for(sending, 1)
                assert error.value.code == "send_uncertain"
                thread.turn_with_policy.assert_not_awaited()
            else:
                await entered.wait()
                sending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending
            state = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
            assert state["facts"]["send:cancelled-1"]["data"]["status"] == "uncertain"
            assert state["batches"]["q1"]["status"] == "ready"
            with pytest.raises(SendDeliveryError) as error:
                await asyncio.wait_for(
                    harness.manager.send_to_session(
                        harness.session.id,
                        harness.session.project_id,
                        "/tmp",
                        "next",
                        AgentSettings(),
                        request_id="cancelled-1",
                        send_origin="human",
                        async_questions=response(),
                    ),
                    1,
                )
            assert error.value.code == "send_uncertain"
            assert agent._turn_task.done()
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.parametrize("command", ["send-message", "send-messages"])
@pytest.mark.parametrize("caller", ["human", "self_agent", "external_mcp"])
def test_cli_private_origin_and_generated_request_id(harness, monkeypatch, command, caller):
    from typer.testing import CliRunner
    from twicc.cli import app
    from twicc.cli._drop_request import transport, whoami
    from twicc.cli._drop_request.polling import PollOutcome
    from twicc.mcp.identity import external_caller, ExternalCaller

    monkeypatch.setattr(whoami, "resolve_current_session", lambda: harness.session if caller == "self_agent" else None)
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    outcome = PollOutcome("sent", {"session_id": harness.session.id, "provider": "codex"}, True)
    seen = []

    class Submission:
        request_uuid = "transport-1"

        def cleanup(self):
            pass

        def was_received(self):
            return True

        def poll(self):
            return outcome

    def submit(payload, **kwargs):
        seen.append(payload)
        return Submission()

    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", lambda *args, **kwargs: outcome)
    token = external_caller.set(ExternalCaller("external-1", "Other agent") if caller == "external_mcp" else None)
    try:
        args = [command, harness.session.id, "--no-expand"]
        args += ["next"] if command == "send-message" else ["--message", "next"]
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
    finally:
        external_caller.reset(token)
    assert len(seen) == 1
    assert seen[0]["_send_origin"] == ("human" if caller == "human" else "agent")
    from uuid import UUID

    UUID(seen[0]["_send_request_id"])
    if caller == "self_agent":
        assert seen[0]["text"] == "next"  # Self-send has no sender header; private origin still identifies the agent.


@pytest.mark.parametrize("origin", ["human", "agent", "internal"])
def test_payload_service_forwards_private_provenance(harness, monkeypatch, origin):
    import twicc.core.services.send_message as sends
    from twicc.agent.registry import get_agent_manager_registry

    registry = get_agent_manager_registry()
    monkeypatch.setattr(registry, "get", lambda *_: harness.manager)
    monkeypatch.setattr(sends, "ensure_provider_running", lambda *_: None)
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        try:
            return await sends.send_message_to_session_from_payload(
                {
                    "session_id": harness.session.id,
                    "text": "  next  ",
                    "_send_origin": origin,
                    "_send_request_id": "cli-1",
                }
            )
        finally:
            await db_writer.stop_db_writer()

    assert asyncio.run(run()).success
    assert harness.agent.send.await_args.args[0] == "  next  "
    assert bool(snapshot(harness)["resolutions"]) == (origin == "human")


def test_native_source_association_preserves_agent_origin_and_original_boundary(harness):
    asyncio.run(harness.send("next", origin="agent"))
    before = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    service.merge_question_facts(
        harness.session.id,
        [
            QuestionFact(
                "user:u1",
                "user_submission",
                "2099-01-01T00:00:00Z",
                "t2",
                "u1",
                100,
                {"client_message_id": "send-1", "source": "jsonl", "origin": "human", "status": "accepted"},
            )
        ],
    )
    after = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert after["origin"] == "agent"
    assert after["source_item_id"] == "u1"
    assert after["boundary"] == before["boundary"]
    assert snapshot(harness)["batches"][0]["status"] == "ready"


def test_late_steering_source_keeps_excluded_and_newer_questions_available(harness):
    service.merge_question_facts(harness.session.id, [question("q2", "t2")])
    asyncio.run(harness.send("next", response()))
    before = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]["boundary"]
    assert before["excluded_batch_ids"] == ["q2"]
    service.merge_question_facts(harness.session.id, [question("q3", "t2", "2099-01-01T00:00:00Z")])
    service.merge_question_facts(
        harness.session.id,
        [
            QuestionFact("end:t2", "turn_end", "2099-01-01T00:00:00Z", "t2", None, 150, {}),
            QuestionFact(
                "user:u1",
                "user_submission",
                "2099-01-01T00:00:01Z",
                "t2",
                "u1",
                160,
                {"client_message_id": "send-1", "source": "jsonl", "origin": "human", "status": "accepted"},
            ),
        ],
    )
    assert [batch["item_id"] for batch in snapshot(harness)["batches"]] == ["q2", "q3"]
    after = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert after["boundary"] == before
    assert after["source_item_id"] == "u1"


def test_resume_native_history_recovers_crashed_delivery_without_resend(harness):
    from openai_codex.generated.v2_all import UserMessageThreadItem
    from twicc.providers import db_writer

    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="crash-1", origin="human", at="2026-10-05T10:00:04Z"
    )

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        sdk._client.thread_read = AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(
                    turns=[
                        SimpleNamespace(
                            id="t2",
                            items=[
                                UserMessageThreadItem(
                                    id="native-user", client_id="crash-1", type="userMessage", content=[]
                                )
                            ],
                        )
                    ]
                )
            )
        )
        try:
            await agent._reconcile_question_submissions()
            assert (
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="crash-1",
                    send_origin="human",
                    async_questions=response(),
                )
                is True
            )
            thread.turn_with_policy.assert_not_awaited()
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:crash-1"]["data"]
    assert data["status"] == "accepted"
    assert data["source_item_id"] == "native-user"
    assert data["boundary"] == prepared.submission["boundary"]


def test_dead_before_background_start_does_not_open_provider_turn(harness):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        original = agent._schedule_turn

        def schedule(*args, **kwargs):
            original(*args, **kwargs)
            agent._set_state(AgentState.DEAD)

        agent._schedule_turn = schedule
        try:
            with pytest.raises(SendDeliveryError) as error:
                await asyncio.wait_for(
                    harness.manager.send_to_session(
                        harness.session.id,
                        harness.session.project_id,
                        "/tmp",
                        "next",
                        AgentSettings(),
                        request_id="dead-1",
                        send_origin="human",
                        async_questions=response(),
                    ),
                    1,
                )
            assert error.value.code == "agent_dead"
            thread.turn_with_policy.assert_not_awaited()
            state = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=harness.session).state)()
            assert state["facts"]["send:dead-1"]["data"]["status"] == "rejected"
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


def test_payload_service_does_not_claim_delivery_when_manager_returns_false(harness, monkeypatch):
    import twicc.core.services.send_message as sends
    from twicc.agent.registry import get_agent_manager_registry

    registry = get_agent_manager_registry()
    monkeypatch.setattr(registry, "get", lambda *_: harness.manager)
    monkeypatch.setattr(sends, "ensure_provider_running", lambda *_: None)
    harness.agent.send.return_value = False
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        try:
            return await sends.send_message_to_session_from_payload(
                {
                    "session_id": harness.session.id,
                    "text": "next",
                    "_send_origin": "human",
                    "_send_request_id": "cli-false",
                }
            )
        finally:
            await db_writer.stop_db_writer()

    result = asyncio.run(run())
    assert not result.success
    assert snapshot(harness)["batches"][0]["status"] == "ready"


def test_definite_retry_preserves_boundary_and_does_not_format_twice(harness):
    harness.agent.send.return_value = False
    assert asyncio.run(harness.send("next", response())) is False
    before = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    harness.agent.send.return_value = True
    assert asyncio.run(harness.send(before["text"], response())) is True
    assert harness.agent.send.await_args.args[0] == before["text"]
    assert before["text"].count("Answers to your questions") == 1
    after = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:send-1"]["data"]
    assert after["boundary"] == before["boundary"]
    assert after["status"] == "accepted"


@pytest.mark.parametrize("route", ["steer", "fallback_start"])
def test_provider_delivery_then_native_bookkeeping_rejection_never_replays(harness, monkeypatch, route):
    from openai_codex.errors import JsonRpcError
    from openai_codex.generated.v2_all import TurnSteerResponse
    from twicc.providers import db_writer

    original = service.accept_question_send

    def outcome(session_id, submission):
        if submission["status"] == "accepted":
            raise JsonRpcError(-32602, "bookkeeping rejection after provider delivery")
        return original(session_id, submission)

    monkeypatch.setattr(service, "accept_question_send", outcome)

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        agent._set_state(AgentState.ASSISTANT_TURN)
        agent._current_turn = turn
        agent._current_turn_ready.set()
        attempts = []

        async def request(method, params, **kwargs):
            attempts.append(params)
            if route == "fallback_start":
                raise JsonRpcError(-32600, "no active turn to steer")
            return TurnSteerResponse(turn_id=turn.id)

        sdk._client.request = request
        try:
            with pytest.raises(SendDeliveryError) as error:
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="bookkeeping-1",
                    send_origin="human",
                    async_questions=response(),
                )
            assert error.value.code == "send_uncertain"
            assert len(attempts) == 1
            assert thread.turn_with_policy.await_count == (1 if route == "fallback_start" else 0)
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    state = AsyncQuestionState.objects.get(session=harness.session).state
    assert state["facts"]["send:bookkeeping-1"]["data"]["status"] == "uncertain"
    assert state["batches"]["q1"]["status"] == "ready"


def test_cold_resume_keeps_submission_through_agent_start(harness, monkeypatch):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        harness.manager._agents.clear()
        agent._work_dirs = []
        agent._reconcile_question_submissions = AsyncMock()
        agent._reconcile_async_question_owners = AsyncMock()
        captured = {}

        async def start(session_id, project_id, cwd, text, *, resume, settings, images, submission):
            captured.update(resume=resume, submission=submission, images=images, settings=settings)
            harness.manager._agents[session_id] = agent
            await agent.start(text, AsyncMock(), resume=resume, images=images, submission=submission)
            return session_id

        monkeypatch.setattr(harness.manager, "_start_agent", start)
        try:
            assert (
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="cold-1",
                    send_origin="human",
                    async_questions=response(),
                )
                is True
            )
            assert captured["resume"] is True
            assert captured["submission"]["request_id"] == "cold-1"
            assert thread.turn_with_policy.await_args.kwargs["client_user_message_id"] == "cold-1"
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


def test_other_provider_payload_keeps_existing_text_normalization(harness, monkeypatch):
    import twicc.core.services.send_message as sends
    from twicc.agent.registry import get_agent_manager_registry

    harness.session.provider = Provider.CLAUDE_CODE
    harness.session.save(update_fields=["provider"])
    manager = SimpleNamespace(send_to_session=AsyncMock(return_value=True))
    monkeypatch.setattr(get_agent_manager_registry(), "get", lambda *_: manager)
    monkeypatch.setattr(sends, "ensure_provider_running", lambda *_: None)
    result = asyncio.run(
        sends.send_message_to_session_from_payload({"session_id": harness.session.id, "text": "  next  "})
    )
    assert result.success
    assert manager.send_to_session.await_args.args[3] == "next"
    assert "async_questions" not in manager.send_to_session.await_args.kwargs


def test_real_agent_sends_one_combined_text_with_images(harness, monkeypatch):
    from twicc.providers.codex.agent.agent import CodexAgent
    from twicc.providers import db_writer

    monkeypatch.setattr("twicc.providers.codex.agent.agent.apply_pending_context", lambda _, text: text)

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        agent._build_turn_input = CodexAgent._build_turn_input.__get__(agent, CodexAgent)
        agent._reconcile_context = AsyncMock()
        image = {"source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}}
        try:
            assert (
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "Gardez ceci.",
                    AgentSettings(),
                    images=[image],
                    request_id="sdk-combined",
                    send_origin="human",
                    async_questions=response("Non"),
                )
                is True
            )
            assert thread.turn_with_policy.await_count == 1
            items = thread.turn_with_policy.await_args.args[0]
            assert items[0].url == "data:image/png;base64,YWJj"
            assert (
                items[1].text
                == "::: Answers to your questions\n\n**Question:** Open the session?\n\n**Answer:** Non\n\n:::\n\nGardez ceci."
            )
        finally:
            if agent._turn_task:
                agent._turn_task.cancel()
                await asyncio.gather(agent._turn_task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


def test_sdk_user_start_links_submission_with_provider_timestamp(harness, monkeypatch):
    from datetime import UTC, datetime
    from openai_codex.generated.v2_all import ItemStartedNotification
    from twicc.providers import db_writer

    monkeypatch.setattr("twicc.providers.codex.agent.agent.log_stream_event", lambda *args: None)
    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="native-1", origin="human", at="2026-10-05T10:00:04Z"
    )
    timestamp = int(datetime(2026, 10, 5, 10, 0, 5, tzinfo=UTC).timestamp() * 1000)

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        payload = ItemStartedNotification.model_validate(
            {
                "item": {"type": "userMessage", "id": "sdk-user", "clientId": "native-1", "content": []},
                "turnId": "t2",
                "threadId": harness.session.id,
                "startedAtMs": timestamp,
            }
        )
        try:
            await agent._handle_stream_event(SimpleNamespace(method="item/started", payload=payload))
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    state = AsyncQuestionState.objects.get(session=harness.session).state
    assert state["facts"]["user:sdk-user"]["at"] == "2026-10-05T10:00:05+00:00"
    data = state["facts"]["send:native-1"]["data"]
    assert data["status"] == "accepted"
    assert data["source_item_id"] == "sdk-user"
    assert data["boundary"] == prepared.submission["boundary"]


def cold_reconciliation_client(monkeypatch, sdk):
    """Use the real recovery construction path with only native transport mocked."""
    from twicc.providers.codex.agent import manager as module

    monkeypatch.setattr(module, "make_codex_config", AsyncMock(return_value=object()))
    monkeypatch.setattr(module, "TwiccAsyncCodex", lambda **_: sdk)
    monkeypatch.setattr(module, "attach_stderr_logging", lambda *_: None)
    monkeypatch.setattr(module, "resolve_and_create_work_dirs", AsyncMock(side_effect=AssertionError("No workdir writes")))
    monkeypatch.setattr(module.CodexAgent, "start", AsyncMock(side_effect=AssertionError("No start")))
    monkeypatch.setattr(module.CodexAgent, "_admit_async_question_owner", AsyncMock(side_effect=AssertionError("No owner")))
    sdk.thread_resume_with_policy = AsyncMock(side_effect=AssertionError("No thread resume"))
    sdk.thread_start_with_policy = AsyncMock(side_effect=AssertionError("No thread start"))
    sdk._client.request = AsyncMock(side_effect=AssertionError("No turn start/steer"))


def test_manager_recovers_cold_uncertain_replay_without_another_sdk_send(harness, monkeypatch):
    from openai_codex.generated.v2_all import UserMessageThreadItem
    from twicc.providers import db_writer

    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="cold-uncertain", origin="human", at="2026-10-05T10:00:04Z"
    )
    service.accept_question_send(harness.session.id, {**prepared.submission, "status": "uncertain"})

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        harness.manager._agents.clear()
        sdk.close = AsyncMock()
        sdk._client.thread_read = AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(
                    turns=[
                        SimpleNamespace(
                            id="t2",
                            items=[
                                UserMessageThreadItem(
                                    id="delivered-user", client_id="cold-uncertain", type="userMessage", content=[]
                                )
                            ],
                        )
                    ]
                )
            )
        )
        cold_reconciliation_client(monkeypatch, sdk)
        try:
            assert (
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="cold-uncertain",
                    send_origin="human",
                    async_questions=response(),
                )
                is True
            )
            sdk._ensure_initialized.assert_awaited_once()
            sdk._client.thread_read.assert_awaited_once_with(harness.session.id, include_turns=True)
            sdk.thread_resume_with_policy.assert_not_awaited()
            sdk.thread_start_with_policy.assert_not_awaited()
            sdk._client.request.assert_not_awaited()
            sdk.close.assert_awaited_once()
            thread.turn_with_policy.assert_not_awaited()
            assert harness.manager._agents == {}
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    data = AsyncQuestionState.objects.get(session=harness.session).state["facts"]["send:cold-uncertain"]["data"]
    assert data["status"] == "accepted"
    assert data["source_item_id"] == "delivered-user"
    assert data["boundary"] == prepared.submission["boundary"]


@pytest.mark.parametrize("history", ["unknown_id", "failed_turn", "read_failure"])
@pytest.mark.parametrize("status", ["prepared", "uncertain"])
def test_cold_reconciliation_without_native_delivery_proof_never_retries(harness, monkeypatch, history, status):
    from openai_codex.generated.v2_all import UserMessageThreadItem
    from twicc.providers import db_writer

    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="cold-no-proof", origin="human", at="2026-10-05T10:00:04Z"
    )
    if status == "uncertain":
        service.accept_question_send(harness.session.id, {**prepared.submission, "status": status})

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        harness.manager._agents.clear()
        sdk.close = AsyncMock()
        item = UserMessageThreadItem(id="unmatched-user", type="userMessage", content=[])
        sdk._client.thread_read = AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(
                    turns=[
                        SimpleNamespace(
                            id="t2", status="failed" if history == "failed_turn" else "completed", items=[item]
                        )
                    ]
                )
            )
        )
        if history == "read_failure":
            sdk._client.thread_read.side_effect = OSError("history unavailable")
        cold_reconciliation_client(monkeypatch, sdk)
        agent.start = AsyncMock()
        agent._admit_async_question_owner = AsyncMock()
        try:
            with pytest.raises(SendDeliveryError) as error:
                await harness.manager.send_to_session(
                    harness.session.id,
                    harness.session.project_id,
                    "/tmp",
                    "next",
                    AgentSettings(),
                    request_id="cold-no-proof",
                    send_origin="human",
                    async_questions=response(),
                )
            assert error.value.code == "send_uncertain"
            sdk._ensure_initialized.assert_awaited_once()
            sdk._client.thread_read.assert_awaited_once_with(harness.session.id, include_turns=True)
            sdk.thread_resume_with_policy.assert_not_awaited()
            sdk.thread_start_with_policy.assert_not_awaited()
            sdk._client.request.assert_not_awaited()
            sdk.close.assert_awaited_once()
            thread.turn_with_policy.assert_not_awaited()
            agent.start.assert_not_awaited()
            agent._admit_async_question_owner.assert_not_awaited()
            assert harness.manager._agents == {}
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    state = AsyncQuestionState.objects.get(session=harness.session).state
    assert state["facts"]["send:cold-no-proof"]["data"]["status"] == status
    assert state["batches"]["q1"]["status"] == "ready"


def test_definite_rejected_request_bypasses_history_and_can_retry(harness):
    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="send-1", origin="human", at="2026-10-05T10:00:04Z"
    )
    service.accept_question_send(harness.session.id, {**prepared.submission, "status": "rejected"})
    harness.agent._reconcile_question_submissions = AsyncMock()
    assert asyncio.run(harness.send("next", response())) is True
    harness.agent._reconcile_question_submissions.assert_not_awaited()
    assert harness.agent.send.await_count == 1


def test_native_recovery_does_not_import_unmatched_history_as_new_human_reply(harness):
    from openai_codex.generated.v2_all import UserMessageThreadItem
    from twicc.providers import db_writer

    service.merge_question_facts(harness.session.id, [question("q2", "t2", "2026-10-05T10:00:03Z")])
    prepared = service.prepare_question_send(
        harness.session.id, "next", response(), request_id="pending-native", origin="human", at="2026-10-05T10:00:04Z"
    )
    service.merge_question_facts(
        harness.session.id,
        [
            QuestionFact("end:t2", "turn_end", "2026-10-05T10:00:05Z", "t2", None, None, {}),
        ],
    )

    async def run():
        db_writer.start_db_writer()
        agent, sdk, thread, turn = runtime_agent(harness)
        sdk._client.thread_read = AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(
                    turns=[
                        SimpleNamespace(
                            id="old-turn",
                            items=[
                                UserMessageThreadItem(
                                    id="old-unmatched-user",
                                    client_id="old-unmatched-client",
                                    type="userMessage",
                                    content=[],
                                )
                            ],
                        ),
                        SimpleNamespace(
                            id="t3",
                            items=[
                                UserMessageThreadItem(
                                    id="matching-user",
                                    client_id="pending-native",
                                    type="userMessage",
                                    content=[],
                                )
                            ],
                        ),
                    ]
                )
            )
        )
        try:
            await agent._reconcile_question_submissions()
            thread.turn_with_policy.assert_not_awaited()
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())
    state = AsyncQuestionState.objects.get(session=harness.session).state
    assert state["batches"]["q2"]["status"] == "ready"
    assert "user:old-unmatched-user" not in state["facts"]
    assert state["facts"]["send:pending-native"]["data"]["status"] == "accepted"
    assert state["facts"]["send:pending-native"]["data"]["source_item_id"] == "matching-user"
    assert state["facts"]["send:pending-native"]["data"]["boundary"] == prepared.submission["boundary"]
