import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import CLIConnectionError, CLINotFoundError
import pytest

from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.exceptions import AgentStartError, SendDeliveryError
from twicc.agent.start_failure import (
    DIAGNOSTIC_MAX_CHARS, classify_start_failure, launch_directory_failure,
)
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.claude_code.bin import ClaudeBinaryNotFound
from twicc.providers.codex.bin import CodexRuntimeNotReady
from twicc.providers.codex.runtime import CodexRuntimeError
from twicc.providers.helpers import AgentSettings


def test_directory_check_accepts_a_directory(tmp_path):
    assert launch_directory_failure(str(tmp_path), provider=Provider.CODEX) is None


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_directory_check_refuses_a_missing_directory_or_a_file(tmp_path, kind):
    path = tmp_path / "target"
    if kind == "file":
        path.write_text("x")
    failure = launch_directory_failure(str(path), provider=Provider.CODEX)
    assert isinstance(failure, AgentStartError)
    assert failure.code == "agent_start_failed"
    assert failure.reason == "working_directory_missing"
    assert str(failure) == f"Codex did not start: the working directory {path} does not exist."
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.working_directory_missing", {"provider": "Codex", "directory": str(path)},
    )


def test_missing_directory_wins_over_a_provider_error(tmp_path):
    failure = classify_start_failure(
        CLINotFoundError("absent"), directory=str(tmp_path / "gone"), provider=Provider.CLAUDE_CODE,
    )
    assert failure.reason == "working_directory_missing"


@pytest.mark.parametrize("error", [
    CLINotFoundError("absent"), ClaudeBinaryNotFound("absent"), CodexRuntimeNotReady("absent"),
    CodexRuntimeError("download failed"),
])
def test_absent_program(tmp_path, error):
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX)
    assert failure.reason == "cli_not_found"
    assert failure.ui_message == ui_message("backend.agent.startFailed.cli_not_found", {"provider": "Codex"})


def test_permission_error_is_found_through_the_cause_chain(tmp_path):
    wrapper = CLIConnectionError("Failed to start Claude Code: denied")
    wrapper.__cause__ = PermissionError(13, "denied")
    failure = classify_start_failure(wrapper, directory=str(tmp_path), provider=Provider.CLAUDE_CODE)
    assert failure.reason == "permission_denied"
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.permission_denied", {"provider": "Claude Code"},
    )


def test_other_causes_carry_a_bounded_diagnostic(tmp_path):
    failure = classify_start_failure(
        RuntimeError("x" * 5000), directory=str(tmp_path), provider=Provider.CODEX,
    )
    assert failure.reason == "start_failed"
    assert len(failure.diagnostic) == DIAGNOSTIC_MAX_CHARS
    assert failure.diagnostic.endswith("…")
    assert failure.ui_message == ui_message(
        "backend.agent.startFailed.start_failed", {"provider": "Codex", "diagnostic": failure.diagnostic},
    )


def test_a_known_error_stays_inside_the_did_not_start_message(tmp_path):
    error = RuntimeError(ui_message_text("Hybrid off", "backend.claude.hybrid_disabled"))
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX)
    assert failure.reason == "start_failed"
    assert failure.ui_message == ui_message("backend.agent.startFailed.start_failed", {
        "provider": "Codex", "diagnostic": ui_message("backend.claude.hybrid_disabled"),
    })


def test_an_existing_send_error_is_returned_unchanged(tmp_path):
    error = SendDeliveryError("bad attachment", code="attachment_missing")
    assert classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX) is error


@pytest.mark.parametrize("error", [RuntimeError("secret prompt"), CLINotFoundError("secret")])
def test_ephemeral_failures_stay_generic(tmp_path, error):
    failure = classify_start_failure(error, directory=str(tmp_path), provider=Provider.CODEX, ephemeral=True)
    assert failure.code == "agent_start_failed"
    assert failure.diagnostic is None
    assert "secret" not in str(failure)
    assert failure.ui_message == ui_message("backend.agent.ephemeralStartFailed")


class Manager(BaseAgentManager):
    provider = Provider.CODEX

    def __init__(self, create):
        super().__init__()
        self._create_agent = create
        self._check_session_writable = AsyncMock()


def start(manager, directory, **kwargs):
    return asyncio.run(manager._start_agent("s", "p", directory, "hi", resume=True, settings=AgentSettings(), **kwargs))


def test_a_missing_directory_never_creates_an_agent_and_logs_one_entry(tmp_path):
    create = AsyncMock()
    gone = str(tmp_path / "gone")
    with patch("twicc.agent.base_manager.logger") as logger, pytest.raises(AgentStartError) as caught:
        start(Manager(create), gone)
    assert caught.value.reason == "working_directory_missing"
    create.assert_not_awaited()
    logger.error.assert_called_once()
    assert "s" in logger.error.call_args.args and gone in logger.error.call_args.args


def test_the_availability_check_runs_before_the_directory_check(tmp_path):
    manager = Manager(AsyncMock())
    manager._check_session_writable = AsyncMock(side_effect=SendDeliveryError("no", code="session_unavailable"))
    with pytest.raises(SendDeliveryError) as caught:
        start(manager, str(tmp_path / "gone"))
    assert caught.value.code == "session_unavailable"


def test_a_create_failure_is_classified_and_logged_once(tmp_path):
    error = FileNotFoundError(2, "No such file", "/some/file")
    with patch("twicc.agent.start_failure.logger") as logger, pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path))
    assert caught.value.reason == "start_failed"
    assert caught.value.__cause__ is error
    logger.error.assert_called_once()
    assert logger.error.call_args.kwargs["exc_info"] is error


def test_an_existing_send_error_from_create_keeps_its_code(tmp_path):
    error = SendDeliveryError("bad", code="attachment_missing")
    with pytest.raises(SendDeliveryError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path))
    assert caught.value is error


def test_a_recorded_failure_is_raised_after_the_agent_started(tmp_path):
    failure = AgentStartError("x", reason="start_failed", diagnostic="x")
    agent = SimpleNamespace(session_id="s", start_failure=failure, ephemeral=False, interrupt_or_kill=AsyncMock())
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock()
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is failure
    agent.interrupt_or_kill.assert_not_awaited()


def started_agent(**kwargs):
    return SimpleNamespace(
        session_id="s", start_failure=None, handoff_attempted=False, ephemeral=False,
        interrupt_or_kill=AsyncMock(), _completion_input_settled=MagicMock(), **kwargs,
    )


def test_a_failure_after_create_and_before_handoff_is_classified(tmp_path):
    agent = started_agent()
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=RuntimeError("db write failed"))
    with pytest.raises(AgentStartError) as caught:
        start(manager, str(tmp_path), completion_token="t")
    assert caught.value.reason == "start_failed"
    agent.interrupt_or_kill.assert_awaited_once_with(reason="agent-start-failed")
    agent._completion_input_settled.assert_called_once_with("t", "rejected")


def test_a_failure_after_handoff_keeps_its_exception(tmp_path):
    agent = started_agent()
    agent.handoff_attempted = True
    error = RuntimeError("late")
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is error
    agent.interrupt_or_kill.assert_awaited_once_with(reason="startup-failed")


def test_a_send_error_during_start_keeps_its_code(tmp_path):
    error = SendDeliveryError("bad", code="attachment_missing")
    agent = started_agent()
    manager = Manager(AsyncMock(return_value=agent))
    manager._register_and_start = AsyncMock(side_effect=error)
    with pytest.raises(SendDeliveryError) as caught:
        start(manager, str(tmp_path))
    assert caught.value is error
    agent.interrupt_or_kill.assert_awaited_once_with(reason="startup-failed")


def test_without_a_waiting_caller_failures_stay_raw(tmp_path):
    error = RuntimeError("raw")
    with pytest.raises(RuntimeError) as caught:
        start(Manager(AsyncMock(side_effect=error)), str(tmp_path), report_start_failure=False)
    assert caught.value is error


def test_the_directory_check_applies_without_a_waiting_caller(tmp_path):
    with pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock()), str(tmp_path / "gone"), report_start_failure=False)
    assert caught.value.reason == "working_directory_missing"


def test_the_completion_wait_raises_the_recorded_start_failure():
    from twicc.agent.base_agent import BaseAgent

    class Agent(BaseAgent):
        provider = Provider.CODEX

        def current_status_label(self):
            return None

    async def run():
        manager = BaseAgentManager()
        agent = Agent("s", "p", "/tmp", AgentSettings())
        manager._bind_completion_callbacks(agent)
        agent._completion_input_pending("t")
        failure = AgentStartError("x", reason="start_failed", diagnostic="x")
        agent._completion_input_settled("t", "rejected", failure=failure)
        with pytest.raises(AgentStartError) as caught:
            await manager.wait_for_completion_input("t")
        assert caught.value is failure

    asyncio.run(run())


def test_an_ephemeral_directory_failure_stays_generic(tmp_path):
    with pytest.raises(AgentStartError) as caught:
        start(Manager(AsyncMock()), str(tmp_path / "gone"), ephemeral=True)
    assert caught.value.ui_message == ui_message("backend.agent.ephemeralStartFailed")


def test_an_ephemeral_create_failure_logs_only_the_exception_type(tmp_path):
    with patch("twicc.agent.start_failure.logger") as logger, pytest.raises(AgentStartError):
        start(Manager(AsyncMock(side_effect=RuntimeError("secret prompt"))), str(tmp_path), ephemeral=True)
    logger.error.assert_called_once_with("Ephemeral agent failed to start (%s)", "RuntimeError")


def make_started_claude_agent(monkeypatch, directory, connect=None, query=None, report=True):
    from twicc.core.services import trust
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.claude_code.agent import agent as agent_module

    fake = SimpleNamespace(connect=connect or AsyncMock(), query=query or AsyncMock())
    monkeypatch.setattr(agent_module, "ClaudeSDKClient", lambda *, options: fake)
    monkeypatch.setattr(agent_module, "patch_client_for_logging", lambda *args: None)
    monkeypatch.setattr(agent_module, "attach_elicitation_handler", lambda *args: None)
    monkeypatch.setattr(agent_module, "apply_pending_context", lambda session_id, text: text)
    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: SimpleNamespace(request_fast_poll=lambda: None))
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project: False)
    agent = agent_module.ClaudeCodeAgent(
        "id", "project", directory, AgentSettings(permission_mode="default"),
        AsyncMock(), AsyncMock(), AsyncMock(), ephemeral=False,
    )
    agent._seed_context_baseline = AsyncMock()
    agent._reconcile_context = AsyncMock()
    agent._run_message_loop = AsyncMock()
    agent.report_start_failure = report
    agent._transition_to_dead = AsyncMock()
    agent._shutdown_sdk_client = AsyncMock()
    return agent


def run_start(agent):
    asyncio.run(agent.start("hi", AsyncMock(), resume=True, completion_token="token"))


@pytest.mark.django_db(transaction=True)
def test_connect_failure_is_recorded_when_a_caller_waits(monkeypatch, tmp_path):
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), connect=connect)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure.code == "agent_start_failed"
    assert agent.start_failure.reason == "start_failed"
    assert agent.kill_reason == "agent-start-failed"
    assert agent._completion_inputs["token"].status == "rejected"


@pytest.mark.django_db(transaction=True)
def test_connect_failure_keeps_the_process_error_without_a_waiting_caller(monkeypatch, tmp_path):
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), connect=connect, report=False)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure is None
    assert agent.kill_reason == "error"
    assert agent._completion_inputs["token"].status == "uncertain"


@pytest.mark.django_db(transaction=True)
def test_failure_between_connect_and_query_is_a_start_failure(monkeypatch, tmp_path):
    agent = make_started_claude_agent(monkeypatch, str(tmp_path))
    agent._build_query_prompt = AsyncMock(side_effect=RuntimeError("prompt build failed"))
    run_start(agent)
    agent._build_query_prompt.assert_awaited_once()
    assert agent.start_failure.reason == "start_failed"


@pytest.mark.django_db(transaction=True)
def test_query_failure_stays_uncertain(monkeypatch, tmp_path):
    query = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, str(tmp_path), query=query)
    run_start(agent)
    query.assert_awaited_once()
    assert agent.start_failure is None
    assert agent.handoff_attempted is True
    assert agent.kill_reason == "error"
    assert agent._completion_inputs["token"].status == "uncertain"


@pytest.mark.django_db(transaction=True)
def test_missing_directory_at_connect_gets_its_reason(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone")
    connect = AsyncMock(side_effect=CLIConnectionError("no"))
    agent = make_started_claude_agent(monkeypatch, gone, connect=connect)
    run_start(agent)
    connect.assert_awaited_once()
    assert agent.start_failure.reason == "working_directory_missing"
