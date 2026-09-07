"""Provider boundaries for one-shot runs without transcript persistence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.agent.system_prompt import compose_addendum
from twicc.providers.helpers import AgentSettings


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_ephemeral_addendum_has_no_session_or_work_directory_contract(provider):
    text = compose_addendum(
        provider=provider,
        project_id="project",
        resolved_settings=AgentSettings(),
        session_id="sensitive-id",
        ephemeral=True,
    )
    assert "- ephemeral: true" in text
    assert "local session transcript" in text
    assert "sensitive-id" not in text
    assert "artifacts_base_dir" not in text
    assert "scratch_base_dir" not in text
    assert "twicc-create-session" not in text


def test_codex_ephemeral_factory_disables_inherited_tools_and_all_persistence(monkeypatch):
    from tests.test_codex_thread_work_dirs import _install_factory_fakes
    from twicc.providers.codex.agent import manager as module
    from openai_codex import CodexConfig

    codex, resolved = _install_factory_fakes(monkeypatch, ["/forbidden"])
    config = CodexConfig()

    async def make_config(**kwargs):
        return config

    monkeypatch.setattr(module, "make_codex_config", make_config)
    codex._ensure_initialized = AsyncMock()
    codex._client = SimpleNamespace(
        request=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    model_dump=lambda: {
                        "mcp_servers": {"twicc": {"command": "x"}, 'literal.dot"quote': {"url": "http://local"}}
                    }
                )
            )
        )
    )
    monkeypatch.setattr(module, "attach_stderr_logging", MagicMock(side_effect=AssertionError("SDK logging")))
    agent = asyncio.run(
        module.CodexAgentManager()._create_agent(
            "draft", "project", "/project", resume=False, settings=AgentSettings(permission_mode="auto"), ephemeral=True
        )
    )
    assert config.config_overrides == ("features.plugins=false",)
    assert resolved == []
    assert agent.kwargs["ephemeral"] is True
    assert agent.kwargs["work_dirs"] == []
    call = codex.start_calls[0]
    assert call["ephemeral"] is True
    assert call["config"]["mcp_servers"] == {
        "twicc": {"enabled": False},
        'literal.dot"quote': {"enabled": False},
    }
    assert codex.started_thread.settings_updates == []


def test_codex_final_answer_ignores_commentary_children_and_sdk_logging(monkeypatch):
    from twicc.providers.codex.agent import agent as module

    agent = module.CodexAgent.__new__(module.CodexAgent)
    agent.session_id = "parent"
    agent.ephemeral = True
    agent.ephemeral_final_text = ""
    agent._ephemeral_has_final_answer = False
    agent._items_by_id = {}
    agent._broadcast_stream_event = AsyncMock()
    monkeypatch.setattr(module, "log_stream_event", MagicMock(side_effect=AssertionError("SDK logging")))

    async def send(text, phase=None, thread_id="parent"):
        item = SimpleNamespace(type="agentMessage", id=text, text=text, phase=phase)
        await agent._handle_stream_event(
            SimpleNamespace(method="item/completed", payload=SimpleNamespace(thread_id=thread_id, item=item))
        )

    async def run():
        await send("fallback")
        assert agent.ephemeral_final_text == "fallback"
        await send("status", "commentary")
        await send("child final", "final_answer", "child")
        assert agent.ephemeral_final_text == "fallback"
        await send("final", "final_answer")
        await send("late unphased")
        assert agent.ephemeral_final_text == "final"

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_ephemeral_error_retained_in_memory_but_not_logged(provider, caplog):
    if provider == "codex":
        from twicc.providers.codex.agent.agent import CodexAgent as Agent
    else:
        from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent as Agent
    agent = Agent.__new__(Agent)
    agent.ephemeral = True
    agent.session_id = "id"
    agent.project_id = "project"
    agent._codex = SimpleNamespace(close=AsyncMock())
    agent._cancel_all_pending_futures = MagicMock()
    agent._first_turn_done_event = MagicMock()
    agent._transition_to_dead = AsyncMock()
    agent._shutdown_sdk_client = AsyncMock()
    asyncio.run(agent._handle_error("SECRET_MARKER", RuntimeError("SECRET_MARKER")))
    assert agent.error == "SECRET_MARKER"
    assert "SECRET_MARKER" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_claude_start_options_exclude_persistence_crons_mcp_and_work_dirs(monkeypatch):
    from twicc.providers.claude_code.agent import agent as module
    from twicc.providers.claude_code import sessions_watcher
    from twicc.core.services import trust

    captured = []
    fake = SimpleNamespace(connect=AsyncMock(), query=AsyncMock())

    def client(*, options):
        captured.append(options)
        return fake

    monkeypatch.setattr(module, "ClaudeSDKClient", client)
    monkeypatch.setattr(module, "patch_client_for_logging", MagicMock(side_effect=AssertionError("SDK log")))
    monkeypatch.setattr(module, "attach_elicitation_handler", lambda *args: None)
    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: SimpleNamespace(request_fast_poll=lambda: None))
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project: False)
    agent = module.ClaudeCodeAgent(
        "id",
        "project",
        "/project",
        AgentSettings(permission_mode="default"),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        ephemeral=True,
    )
    agent._resolve_and_create_work_dirs = AsyncMock(side_effect=AssertionError("work directories"))
    agent._seed_context_baseline = AsyncMock()
    agent._reconcile_context = AsyncMock()
    agent._run_message_loop = AsyncMock()

    async def run():
        await agent.start("prompt", AsyncMock(), resume=False)
        if agent._message_loop_task:
            await agent._message_loop_task

    asyncio.run(run())
    assert agent.error is None
    options = captured[0]
    assert "no-session-persistence" in options.extra_args
    assert options.plugins == []
    assert options.add_dirs == []
    assert options.mcp_servers == {}
    assert options.strict_mcp_config is True
    assert {"CronCreate", "CronDelete", "CronList"} <= set(options.disallowed_tools)
    assert all(h.matcher != "CronCreate|CronDelete" for h in options.hooks["PostToolUse"])


def test_ephemeral_claude_cron_callback_does_not_persist():
    from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent

    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.ephemeral = True
    agent._on_cron_created = AsyncMock(side_effect=AssertionError("cron persistence"))
    asyncio.run(
        agent._handle_cron_tool_event(
            {
                "tool_name": "CronCreate",
                "tool_input": {"cron": "* * * * *", "prompt": "secret"},
                "tool_response": {"id": "cron"},
            }
        )
    )


def test_codex_ephemeral_hold_uses_runtime_status_without_watcher():
    from twicc.agent.states import AgentState
    from twicc.providers.codex.agent.agent import CodexAgent

    agent = CodexAgent.__new__(CodexAgent)
    agent.ephemeral = True
    agent.session_id = "parent"
    agent.state = AgentState.ASSISTANT_TURN
    agent._live_subagents = {"child": "/root/child"}
    agent._subagent_hold_active = False
    agent._ephemeral_subagent_task = None
    agent._current_turn = None
    agent._manual_compaction = False
    agent._goal_continuation_active = False
    agent._subagent_wait_label_active = False
    agent._set_state = lambda state: setattr(agent, "state", state)
    agent._notify_state_change = AsyncMock()
    agent._broadcast_process_label = AsyncMock()
    statuses = iter(["active", "idle"])

    async def read(child_id):
        return SimpleNamespace(
            thread=SimpleNamespace(status=SimpleNamespace(root=SimpleNamespace(type=next(statuses))))
        )

    agent._codex = SimpleNamespace(_client=SimpleNamespace(thread_read=read))

    async def run():
        assert await agent._try_arm_subagent_hold() is True
        await asyncio.wait_for(agent._ephemeral_subagent_task, 2)
        assert agent.state == AgentState.USER_TURN
        assert not agent._live_subagents

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_ephemeral_provider_state_change_skips_persistent_provider_hooks(monkeypatch, provider):
    from twicc.agent.base_manager import BaseAgentManager

    if provider == "claude_code":
        from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager as Manager
    else:
        from twicc.providers.codex.agent.manager import CodexAgentManager as Manager
    base = AsyncMock()
    monkeypatch.setattr(BaseAgentManager, "_on_state_change", base)
    agent = SimpleNamespace(ephemeral=True)
    asyncio.run(Manager()._on_state_change(agent))
    base.assert_awaited_once_with(agent)


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_provider_send_refuses_ephemeral_before_runtime_work(monkeypatch, provider):
    from twicc.agent import SendDeliveryError

    if provider == "claude_code":
        from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager as Manager
    else:
        from twicc.providers.codex.agent.manager import CodexAgentManager as Manager
    manager = Manager()

    def refuse(*args):
        raise SendDeliveryError("read only", code="ephemeral_readonly")

    monkeypatch.setattr(manager, "_check_ephemeral_readonly", refuse)
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(manager.send_to_session("ephemeral", "project", "/project", "/goal test", AgentSettings()))
    assert caught.value.code == "ephemeral_readonly"


def test_codex_normal_command_forwards_its_admission(monkeypatch):
    from twicc.providers.codex.agent.manager import CodexAgentManager

    manager = CodexAgentManager()
    token = object()
    monkeypatch.setattr(manager, "_check_ephemeral_readonly", lambda *args: None)
    start = AsyncMock(return_value="canonical")
    monkeypatch.setattr(manager, "_start_agent", start)
    asyncio.run(
        manager.create_session("draft", "project", "/project", "/goal test", AgentSettings(), ephemeral_admission=token)
    )
    assert start.await_args.kwargs["ephemeral_admission"] is token


def test_codex_factory_cancellation_closes_initialized_transport(monkeypatch):
    from tests.test_codex_thread_work_dirs import _install_factory_fakes
    from twicc.providers.codex.agent import manager as module
    from openai_codex import CodexConfig

    codex, _ = _install_factory_fakes(monkeypatch, [])

    async def config(**kwargs):
        return CodexConfig()

    monkeypatch.setattr(module, "make_codex_config", config)
    initialized = asyncio.Event()

    async def request(*args, **kwargs):
        initialized.set()
        await asyncio.Future()

    codex._ensure_initialized = AsyncMock()
    codex._client = SimpleNamespace(request=request)
    codex.close = AsyncMock()

    async def run():
        task = asyncio.create_task(
            module.CodexAgentManager()._create_agent(
                "draft",
                "project",
                "/project",
                resume=False,
                settings=AgentSettings(permission_mode="auto"),
                ephemeral=True,
            )
        )
        await initialized.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        codex.close.assert_awaited_once()

    asyncio.run(run())


def test_codex_ephemeral_file_change_does_not_capture_transcript_diff(monkeypatch):
    from twicc.providers.codex.agent import agent as module

    agent = module.CodexAgent.__new__(module.CodexAgent)
    agent.ephemeral = True
    agent.session_id = "parent"
    agent._items_by_id = {}
    item = SimpleNamespace(type="fileChange", id="change", model_dump=lambda **kwargs: {"type": "fileChange"})
    capture = MagicMock(side_effect=AssertionError("persistent diff capture"))
    monkeypatch.setattr(module, "_capture_original_files_for_apply_patch", capture)
    asyncio.run(
        agent._handle_stream_event(
            SimpleNamespace(method="item/started", payload=SimpleNamespace(thread_id="parent", item=item))
        )
    )
    capture.assert_not_called()


@pytest.mark.parametrize("price_available", [True, False])
def test_codex_ephemeral_cost_uses_live_usage_without_duplicates(monkeypatch, price_available):
    from decimal import Decimal
    from openai_codex.generated.v2_all import ThreadTokenUsageUpdatedNotification
    from twicc.providers.codex.agent.agent import CodexAgent
    from twicc.providers.helpers import get_provider_helpers
    from twicc.core.enums import Provider

    helpers = get_provider_helpers(Provider.CODEX)
    calculate = MagicMock(return_value=Decimal("0.012345") if price_available else None)
    monkeypatch.setattr(helpers, "calculate_line_cost", calculate)
    agent = CodexAgent(
        "parent", "p", "/tmp", AgentSettings(selected_model="gpt-5.4"),
        MagicMock(), MagicMock(), ephemeral=True, work_dirs=[],
    )

    async def send(total, thread_id="parent"):
        payload = ThreadTokenUsageUpdatedNotification.model_validate({
            "threadId": thread_id, "turnId": "turn",
            "tokenUsage": {
                "total": {"inputTokens": total - 20, "cachedInputTokens": 0,
                          "outputTokens": 20, "reasoningOutputTokens": 0, "totalTokens": total},
                "last": {"inputTokens": 100, "cachedInputTokens": 30,
                         "outputTokens": 20, "reasoningOutputTokens": 5, "totalTokens": 120},
            },
        })
        await agent._handle_stream_event(SimpleNamespace(method="thread/tokenUsage/updated", payload=payload))

    async def run():
        await send(0)
        await send(120, "child")
        calculate.assert_not_called()
        await send(120)
        await send(120)
        assert calculate.call_count == 1
        await send(240)
        assert calculate.call_count == (2 if price_available else 1)
        usage, model, date = calculate.call_args.args
        assert usage.input_tokens == 70
        assert usage.cache_read_input_tokens == 30
        assert usage.output_tokens == 20  # Reasoning is already included.
        assert model == f"openai/{helpers.resolve_sdk_model(agent.agent_settings.selected_model)}"
        assert date is not None
        assert agent.ephemeral_usage.get("cost_usd") == (0.02469 if price_available else None)

    asyncio.run(run())
