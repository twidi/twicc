"""Accepted input receipts use immutable admission boundaries and durable bindings."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests import test_completion_publication, test_logical_turn_answers, test_mcp_events_turns
from twicc.cli._drop_request.output import build_final
from twicc.core.models import LogicalTurnInput

bridge = test_completion_publication.bridge
env = test_mcp_events_turns.env
answer_fixture = test_logical_turn_answers.answer_fixture
content_row_factory = test_logical_turn_answers.content_row_factory


def test_optional_wait_token_is_projected_only_when_present():
    def output(data):
        return build_final(SimpleNamespace(status="sent", data=data), request_uuid="request", timeout=1)
    assert output({"session_id": "session", "wait_token": "receipt"})["wait_token"] == "receipt"
    assert "wait_token" not in output({"session_id": "session"})


@pytest.mark.parametrize("same_interval", [False, True])
def test_equal_cursors_bind_distinct_inputs_to_their_intervals(bridge, same_interval):
    async def scenario():
        manager, agent = bridge
        manager._bind_completion_callbacks(agent)
        tokens, turns = [], []
        for _ in range(2):
            token = str(uuid4())
            tokens.append(token)
            agent._completion_source_floor = 0
            agent._completion_input_pending(token)
            agent._completion_activity()
            turns.append(agent.completion_controller.snapshot().turn_id)
            agent._completion_input_settled(token, "accepted")
            assert await asyncio.wait_for(agent.wait_for_completion_input(token), timeout=2)
            if not same_interval:
                agent._completion_terminate("ended")
                while agent._completion_persist_task is not None:
                    await asyncio.sleep(0)
        rows = await asyncio.to_thread(lambda: list(LogicalTurnInput.objects.order_by("turn__position")))
        assert len(rows) == 2
        assert {row.admission_cursor for row in rows} == {0}
        assert {str(row.id) for row in rows} == set(tokens)
        assert (turns[0] == turns[1]) is same_interval
        assert {str(row.turn_id) for row in rows} == set(turns)
        for task in tuple(agent._completion_tasks):
            await task
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["rejected", "uncertain"])
def test_unaccepted_input_does_not_return_accepted_receipt(bridge, status):
    async def scenario():
        manager, agent = bridge
        manager._bind_completion_callbacks(agent)
        token = str(uuid4())
        agent._completion_input_pending(token)
        agent._completion_activity()
        agent._completion_input_settled(token, status)
        assert not await asyncio.wait_for(agent.wait_for_completion_input(token), timeout=2)
        while agent._completion_persist_task is not None:
            await asyncio.sleep(0)
        row = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=token)
        assert row.status == status
    asyncio.run(scenario())


def test_singular_token_wait_preserves_token_cursor_before_legacy_defaults(monkeypatch, capsysbinary):
    from unittest.mock import Mock
    import typer
    from twicc.cli import session as cli_session
    captured = {}
    monkeypatch.setattr(cli_session, "lookup_wait_session", lambda _: SimpleNamespace(
        session=SimpleNamespace(id="session"), indexed=True, live=True))
    monkeypatch.setattr(cli_session, "_refuse_unavailable", lambda _: None)
    monkeypatch.setattr("twicc.cli._wait_reply.default_wait_cursors", Mock(side_effect=AssertionError("legacy cursor")))
    def waiting(session_id, **kwargs):
        captured.update(kwargs)
        return {"outcome": "replied"}
    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_reply_or_degrade", waiting)
    with pytest.raises(typer.Exit):
        cli_session.wait_reply("session", wait_token="token-A", timeout=1)
    assert captured["since_line_num"] is None
    assert captured["wait_token"] == "token-A"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome,first_has_lines", [("normal", False), ("ended", False), ("ended", True), ("provider_error", False)])
@pytest.mark.parametrize("opened_by", ["initial", "transition"])
def test_monitor_consumes_empty_or_interrupted_A_then_B_after_reload(env, monkeypatch, outcome, first_has_lines, opened_by):
    from tests.test_mcp_events_turns import opened
    from tests.test_mcp_events_writer import apply
    from tests.test_wait_reply import managed_turn, native_assistant
    from twicc.cli import _wait_reply
    from twicc.core.models import Session
    from twicc.mcp.events.methods import SubscriptionSnapshot
    from twicc.mcp.events.runtime import RebaseWrite

    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    first = managed_turn(env.session, outcome=outcome,
                         refs=("a1",) if first_has_lines else (), first="a1" if first_has_lines else None)
    second = managed_turn(env.session, position=2, outcome="normal", refs=("b1",), first="b1")
    if first_has_lines:
        native_assistant(env.session, 2, "interrupted A", "a1")
    native_assistant(env.session, 8, "answer B", "b1")
    opened(env, by=opened_by)
    from twicc.agent.states import AgentState
    env.registry.set_agent(env.session.id, state=AgentState.USER_TURN, at=999)
    # The subscription can load with both intervals indexed. The initial clamp must not skip B.
    env.monitor.initial_last_line = 8
    env.monitor.first = True
    for _ in range(4):
        env.tick()
        env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
        if env.emissions:
            break
    assert len(env.emissions) == 1
    assert env.payloads()[0]["data"]["reply"]["outcome"] == ("ended" if outcome == "normal" else outcome)
    assert env.emissions[0].cursor.last_logical_turn_id == str(first.pk)
    assert env.monitor.cursor_line < 8
    emission = env.emissions.pop()
    runner = asyncio.Runner()
    runner.run(apply(env.runtime, *emission.state_writes, emission.cursor))
    env.row.refresh_from_db()
    assert env.row.last_logical_turn_id == first.pk
    env.runtime._add_monitor(SubscriptionSnapshot.from_row(env.row))
    monitor = env.runtime.monitors[env.row.id]
    env.runtime._tick_monitor(monitor)
    assert env.payloads()[0]["data"]["reply"]["text"] == "answer B"
    assert env.emissions[0].cursor.last_logical_turn_id == str(second.pk)
    for _ in range(5):
        env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
        env.runtime._tick_monitor(monitor)
    assert len(env.emissions) == 1
    # Delivery completion can enqueue an older receipt after a newer one.
    second_emission = env.emissions[0]
    runner.run(apply(env.runtime, *second_emission.state_writes, second_emission.cursor, emission.cursor))
    env.row.refresh_from_db()
    assert env.row.last_logical_turn_id == second.pk
    # A rebase must erase the logical progress in memory and in the generation-bound writer.
    Session.objects.filter(pk=env.session.pk).update(history_epoch=1)
    snapshot = env.runtime._read_monitor_session(monitor)
    env.runtime._apply_rebase(monitor, snapshot)
    assert monitor.last_logical_turn_id is None
    assert monitor.wait.after_turn_id is None
    rebase = env.writes[-1]
    assert isinstance(rebase, RebaseWrite)
    runner.run(apply(env.runtime, rebase, emission.cursor))
    runner.close()
    env.row.refresh_from_db()
    assert env.row.last_logical_turn_id is None


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_fast_send_returns_admission_cursor_and_durable_token(bridge, monkeypatch, provider):
    from twicc.core.models import Session
    from twicc.core.services import send_message
    from twicc.core.enums import Provider
    manager, agent = bridge
    agent.provider = Provider(provider)
    Session.objects.filter(pk=agent.session_id).update(provider=provider)
    helpers = SimpleNamespace(resolve_agent_settings=lambda value: value,
                              enforce_agent_settings_consistency=lambda value: value)
    monkeypatch.setattr(send_message, "ensure_provider_running", lambda _: None)
    monkeypatch.setattr(send_message, "get_provider_helpers", lambda _: helpers)
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda _: manager))
    async def deliver(session_id, project_id, cwd, text, *, completion_token, completion_cursor=None, **kwargs):
        if completion_cursor is None:
            completion_cursor = await manager._completion_admission_cursor(session_id)
        agent._completion_source_floor = completion_cursor
        agent._completion_input_pending(completion_token)
        agent._completion_activity()
        agent._completion_input_settled(completion_token, "accepted")
        # The watcher indexes a fast answer before the manager returns.
        from asgiref.sync import sync_to_async
        from twicc.providers.db_writer import run_under_db_write_lock
        await run_under_db_write_lock(lambda: sync_to_async(
            lambda: Session.objects.filter(pk=session_id).update(last_line=9))())
        return True
    manager.send_to_session = deliver
    async def scenario():
        manager._bind_completion_callbacks(agent)
        result = await send_message.send_message_from_drop_payload({"session_id": agent.session_id, "text": "go"})
        assert result.success
        assert result.status_extra["last_line"] == 0
        row = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=result.status_extra["wait_token"])
        assert row.status == "accepted" and row.admission_cursor == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("provider,case", [("claude_code", "shell"), ("claude_code", "command"),
                                          ("codex", "shell"), ("codex", "command")])
def test_internal_notices_and_commands_do_not_admit_human_input(monkeypatch, provider, case):
    from unittest.mock import AsyncMock
    if provider == "codex":
        from tests.test_codex_completion import make_agent
        agent = make_agent()
        agent._send_input = AsyncMock(return_value=True)
    else:
        from tests.test_claude_completion import make_agent
        agent = make_agent()
        agent._client = SimpleNamespace(query=AsyncMock())
        agent._build_query_prompt = AsyncMock(return_value="command")
        agent._clear_waiting_label = AsyncMock()
        agent._notify_state_change = AsyncMock()
    async def scenario():
        from twicc.agent.states import AgentState
        agent._set_state(AgentState.USER_TURN)
        kwargs = {"shell_notice": True} if case == "shell" else {}
        await agent.send("shell notice" if case == "shell" else "/compact", **kwargs)
        assert agent._completion_inputs == {}
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_staged_restart_input_binds_only_to_replacement_agent(bridge):
    from tests.test_completion_publication import Agent
    from twicc.providers.helpers import AgentSettings
    async def scenario():
        manager, old = bridge
        manager._bind_completion_callbacks(old)
        old._completion_activity()
        token = str(uuid4())
        old._completion_input_pending(token, staged=True)
        old._completion_terminate("ended")
        while old._completion_persist_task is not None:
            await asyncio.sleep(0)
        assert not await asyncio.to_thread(LogicalTurnInput.objects.filter(pk=token).exists)
        new = Agent(old.session_id, old.project_id, "/tmp", AgentSettings())
        manager._agents[old.session_id] = new
        manager._bind_completion_callbacks(new)
        new._completion_source_floor = 81
        new._completion_input_pending(token)
        new._completion_activity()
        new._completion_input_settled(token, "accepted")
        assert await asyncio.wait_for(manager.wait_for_completion_input(token), 2)
        row = await asyncio.to_thread(LogicalTurnInput.objects.select_related("turn").get, pk=token)
        assert str(row.turn.run_id) == new.completion_controller.run_id
        assert row.admission_cursor == 81
        assert await asyncio.to_thread(LogicalTurnInput.objects.count) == 1
    asyncio.run(scenario())


def test_receipt_survives_agent_death_and_replacement_before_return(bridge):
    from tests.test_completion_publication import Agent
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings
    async def scenario():
        manager, old = bridge
        manager._bind_completion_callbacks(old)
        token = str(uuid4())
        old._completion_input_pending(token)
        old._completion_activity()
        old._completion_input_settled(token, "accepted")
        old._set_state(AgentState.DEAD)
        manager._agents[old.session_id] = Agent(old.session_id, old.project_id, "/tmp", AgentSettings())
        assert await asyncio.wait_for(manager.wait_for_completion_input(token), 2)
        row = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=token)
        assert str(row.turn_id) == old.completion_controller.snapshot().turn_id
    asyncio.run(scenario())


def test_codex_initial_create_waits_for_native_open_and_durable_canonical_binding(bridge, monkeypatch):
    from tests.test_codex_completion import make_agent, Turn, cleanup
    from twicc.core.services import session_creation
    from twicc.core.models import LogicalTurn
    from unittest.mock import AsyncMock
    manager, original = bridge
    agent = make_agent(ephemeral=False)
    agent.session_id, agent.project_id = original.session_id, original.project_id
    agent.process_run = original.process_run
    helpers = SimpleNamespace(resolve_agent_settings=lambda value: value,
                              enforce_agent_settings_consistency=lambda value: value)
    monkeypatch.setattr(session_creation, "ensure_provider_running", lambda _: None)
    monkeypatch.setattr(session_creation, "get_provider_helpers", lambda _: helpers)
    monkeypatch.setattr(session_creation, "compose_addendum", lambda **kwargs: "")
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda _: manager))
    async def scenario():
        manager._agents[agent.session_id] = agent
        manager._bind_completion_callbacks(agent)
        opened, native_ack, persist, persisted = (asyncio.Event() for _ in range(4))
        original_persist = agent._completion_persist_callback
        async def blocked_persist(*args):
            persist.set()
            await persisted.wait()
            return await original_persist(*args)
        agent._completion_persist_callback = blocked_persist
        async def open_turn(*args, **kwargs):
            opened.set()
            await native_ack.wait()
            return Turn([])
        agent._open_turn = open_turn
        agent._build_turn_input = AsyncMock(return_value=[])
        agent._ensure_async_question_session = AsyncMock()
        captured = {}
        async def create(draft_id, project_id, cwd, text, **kwargs):
            captured.update(kwargs)
            await agent.start(text, AsyncMock(), False, completion_token=kwargs["completion_token"],
                              completion_cursor=kwargs["completion_cursor"])
            return agent.session_id
        manager.create_session = create
        draft_id = str(uuid4())
        request = asyncio.create_task(session_creation.create_session_from_payload({
            "session_id": draft_id, "project_id": agent.project_id,
            "provider": "codex", "text": "go", "layout": {},
        }))
        await asyncio.wait_for(opened.wait(), 2)
        assert not request.done()
        assert not await asyncio.to_thread(LogicalTurnInput.objects.filter(status="accepted").exists)
        native_ack.set()
        await asyncio.wait_for(agent._turn_task, 2)
        await asyncio.wait_for(persist.wait(), 2)
        assert not request.done()
        persisted.set()
        result = await asyncio.wait_for(request, 2)
        assert result.session_id == agent.session_id and result.session_id != draft_id
        assert result.status_extra == {"last_line": 0, "wait_token": captured["completion_token"]}
        cleanup(agent)
        while agent._completion_persist_task is not None:
            await asyncio.sleep(0)
        row = await asyncio.to_thread(LogicalTurnInput.objects.select_related("turn").get,
                                      pk=result.status_extra["wait_token"])
        assert row.turn.session_id == agent.session_id and row.status == "accepted"
        assert await asyncio.to_thread(lambda: LogicalTurn.objects.filter(session_id=draft_id).count()) == 0
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("entry_point", ["service", "internal"])
def test_send_blocked_by_codex_gate_captures_cursor_after_gate_admission(bridge, monkeypatch, entry_point):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from twicc.agent.states import AgentState
    from twicc.core.models import Session
    from twicc.core.services import send_message
    from twicc.providers.codex.agent.manager import CodexAgentManager
    _manager_unused, agent = bridge
    manager = CodexAgentManager()
    manager._agents[agent.session_id] = agent
    agent._set_state(AgentState.USER_TURN)
    agent.apply_agent_settings = AsyncMock()
    helpers = SimpleNamespace(resolve_agent_settings=lambda value: value,
                              enforce_agent_settings_consistency=lambda value: value)
    monkeypatch.setattr(send_message, "ensure_provider_running", lambda _: None)
    monkeypatch.setattr(send_message, "get_provider_helpers", lambda _: helpers)
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda _: manager))
    async def scenario():
        manager._bind_completion_callbacks(agent)
        blocked, admitted = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def gate(session_id):
            blocked.set()
            await admitted.wait()
            yield
        monkeypatch.setattr("twicc.providers.codex.agent.manager.gate_for", gate)
        observed = []
        async def native_send(text, *, completion_token, completion_cursor, **kwargs):
            observed.append(completion_cursor)
            agent._completion_source_floor = completion_cursor
            agent._completion_input_pending(completion_token)
            agent._completion_activity()
            agent._completion_input_settled(completion_token, "accepted")
            from asgiref.sync import sync_to_async
            from twicc.providers.db_writer import run_under_db_write_lock
            await run_under_db_write_lock(lambda: sync_to_async(
                lambda: Session.objects.filter(pk=agent.session_id).update(last_line=9))())
            return True
        agent.send = native_send
        if entry_point == "service":
            request = asyncio.create_task(send_message.send_message_from_drop_payload({
                "session_id": agent.session_id, "text": "go",
            }))
        else:
            request = asyncio.create_task(manager.send_to_session(
                agent.session_id, agent.project_id, "/tmp", "go", agent.agent_settings,
            ))
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(last_line=5))
        admitted.set()
        result = await asyncio.wait_for(request, 2)
        assert observed == [5]
        if entry_point == "service":
            assert result.success
            assert result.status_extra["last_line"] == 5
            token = result.status_extra["wait_token"]
        else:
            assert result is True
            [token] = manager._completion_input_receipts
            receipt = await asyncio.wait_for(manager.wait_for_completion_input(token), 2)
            assert receipt.admission_cursor == 5
        while agent._completion_persist_task is not None:
            await asyncio.sleep(0)
        row = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=token)
        assert row.admission_cursor == 5
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry_point", ["cli", "mcp"])
def test_token_default_wait_returns_A_after_later_user_input(answer_fixture, monkeypatch, entry_point):
    import orjson
    from typer.testing import CliRunner
    from twicc.cli import app
    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.mcp import server as mcp_server
    from twicc.mcp.tools import iter_mcp_tools
    f = answer_fixture
    from django.utils import timezone
    f.session.created_at = timezone.now()
    f.session.user_message_count = 2
    f.session.save(update_fields=["created_at", "user_message_count"])
    first = f.turn(refs=("A1",), first="A1")
    second = f.turn(refs=("B1",), first="B1", position=2)
    f.index("A1", 2, "answer A")
    f.index("B1", 8, "answer B")
    SessionItem.objects.create(session=f.session, line_num=3, kind=ItemKind.USER_MESSAGE,
                               content='{"type":"user","message":{"content":"later user input"}}')
    tokens = [str(uuid4()), str(uuid4())]
    for token, turn in zip(tokens, [first, second]):
        LogicalTurnInput.objects.create(id=token, turn=turn, admission_cursor=0, status="accepted")
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: SimpleNamespace(pid=1234))
    for token, expected in zip(tokens, ["answer A", "answer B"]):
        if entry_point == "cli":
            result = CliRunner().invoke(app, ["session", f.session.id, "wait-reply", "--wait-token", token,
                                             "--wait-timeout", "0.1"])
            assert result.exit_code == 0, result.output
            payload = orjson.loads(result.output)
        else:
            tool = next(tool for tool in iter_mcp_tools() if tool.name == "session_wait_reply")
            assert tool.input_schema["properties"]["wait_token"]["type"] == "string"
            result = asyncio.run(mcp_server.dispatch_tool("session_wait_reply", {
                "session_id": f.session.id, "wait_token": token, "wait_timeout": 0.1,
            }, session_id=None))
            assert result["exit_code"] == 0, result
            payload = result["result"]
        assert payload["reply"]["text"] == expected


def test_hybrid_initial_paste_waits_for_native_ingress_and_durable_receipt(bridge, monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock
    from twicc.core.models import Session
    from twicc.core.services import trust
    from twicc.providers.claude_code.agent.hybrid import agent as module
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.live_sync import CompletionObservation
    _manager_unused, original = bridge
    manager = ClaudeCodeAgentManager()
    agent = module.HybridClaudeAgent(original.session_id, original.project_id, "/tmp", original.agent_settings)
    agent.process_run = original.process_run
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    agent._notify_state_change = AsyncMock()
    agent._materialize_attachments = AsyncMock(return_value="prompt")
    agent._read_system_prompt_addendum = Mock(return_value=None)
    agent._resolve_temp_title = AsyncMock(return_value="test")
    agent._resolve_and_create_work_dirs = AsyncMock(return_value=[])
    agent._start_liveness_monitor = lambda: None
    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: Mock())
    monkeypatch.setattr(trust, "project_is_untrusted", lambda _: False)
    monkeypatch.setattr(module, "write_addendum_file", lambda *args: tmp_path / "addendum")
    monkeypatch.setattr(module, "get_session_hybrid_dir", lambda _: tmp_path)
    monkeypatch.setattr(module.hybrid_tmux, "session_exists", lambda _: False)
    monkeypatch.setattr(module.hybrid_tmux, "create_session", lambda *args: None)
    monkeypatch.setattr(module.hybrid_tmux, "pane_status", lambda _: (123, False))
    monkeypatch.setattr(module.hybrid_tmux, "paste_text", lambda *args: None)
    async def scenario():
        ready, paste = asyncio.Event(), asyncio.Event()
        async def composer():
            ready.set()
            await paste.wait()
            return True
        agent._wait_until_composer_ready = composer
        manager._agents[agent.session_id] = agent
        manager._bind_completion_callbacks(agent)
        token = str(uuid4())
        await agent.start("prompt", AsyncMock(), completion_token=token, completion_cursor=23)
        await asyncio.wait_for(ready.wait(), 2)
        receipt = asyncio.create_task(manager.wait_for_completion_input(token))
        await asyncio.sleep(0)
        assert not receipt.done()
        assert not await asyncio.to_thread(LogicalTurnInput.objects.filter(pk=token).exists)
        paste.set()
        await agent._first_paste_task
        assert not receipt.done()
        agent.observe_jsonl_observations((CompletionObservation("user_message", "claude_uuid", "native-user"),))
        committed = await asyncio.wait_for(receipt, 2)
        assert committed.admission_cursor == 23
        while agent._completion_persist_task is not None:
            await asyncio.sleep(0)
        row = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=token)
        assert row.status == "accepted" and row.admission_cursor == 23
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.django_db
def test_create_command_automatically_waits_on_accepted_input_token(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    from twicc.cli import app
    from twicc.cli._drop_request import transport, bootstrap_local
    from twicc.cli._drop_request.polling import PollOutcome
    seen = {}
    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(bootstrap_local, "read_synced_settings", lambda: {
        "disabledProviders": [], "defaultProvider": "claude_code",
    })
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: SimpleNamespace(
        request_uuid="created-request", cleanup=lambda: None))
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: PollOutcome("created", {
        "session_id": "created", "provider": "claude_code", "project_id": "project",
        "last_line": 0, "wait_token": "accepted-first-input",
    }, True))
    def waiting(session_id, **kwargs):
        seen.update(kwargs)
        return {"outcome": "replied"}
    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_reply_or_degrade", waiting)
    result = CliRunner().invoke(app, ["create-session", "hello", "--project", str(tmp_path), "--wait-reply"])
    assert result.exit_code == 0, result.output
    assert seen["wait_token"] == "accepted-first-input"
    assert seen["since_line_num"] == 0


@pytest.mark.parametrize("entry_point", ["send", "create"])
def test_native_acceptance_without_journal_commit_returns_uncertain_without_token(bridge, monkeypatch, entry_point):
    from unittest.mock import Mock
    from twicc.core.services import send_message, session_creation
    manager, agent = bridge
    helpers = SimpleNamespace(resolve_agent_settings=lambda value: value,
                              enforce_agent_settings_consistency=lambda value: value)
    for service in [send_message, session_creation]:
        monkeypatch.setattr(service, "ensure_provider_running", lambda _: None)
        monkeypatch.setattr(service, "get_provider_helpers", lambda _: helpers)
    monkeypatch.setattr(session_creation, "compose_addendum", lambda **kwargs: "")
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda _: manager))
    monkeypatch.setattr(manager, "_store_completion_snapshot_admitted", Mock(side_effect=RuntimeError("journal unavailable")))
    async def deliver(session_id, project_id, cwd, text, *, completion_token, completion_cursor=None, **kwargs):
        agent._completion_source_floor = completion_cursor or 0
        agent._completion_input_pending(completion_token)
        agent._completion_activity()
        agent._completion_input_settled(completion_token, "accepted")
        return agent.session_id if entry_point == "create" else True
    manager.send_to_session = deliver
    manager.create_session = deliver
    async def scenario():
        manager._bind_completion_callbacks(agent)
        if entry_point == "send":
            pending = send_message.send_message_from_drop_payload({"session_id": agent.session_id, "text": "go"})
        else:
            pending = session_creation.create_session_from_payload({
                "session_id": str(uuid4()), "project_id": agent.project_id, "provider": "codex",
                "text": "go", "layout": {},
            })
        result = await asyncio.wait_for(pending, 2)
        assert not result.success
        assert result.errors[0].code == "send_uncertain"
        assert "wait_token" not in result.status_extra
        assert not await asyncio.to_thread(LogicalTurnInput.objects.exists)
    asyncio.run(scenario())
