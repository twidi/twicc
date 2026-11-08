from claude_agent_sdk import CLIConnectionError, CLINotFoundError
import pytest

from twicc.agent.exceptions import AgentStartError, SendDeliveryError
from twicc.agent.start_failure import (
    DIAGNOSTIC_MAX_CHARS, classify_start_failure, launch_directory_failure,
)
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.claude_code.bin import ClaudeBinaryNotFound
from twicc.providers.codex.bin import CodexRuntimeNotReady
from twicc.providers.codex.runtime import CodexRuntimeError


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
