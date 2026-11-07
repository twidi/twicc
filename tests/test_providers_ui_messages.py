"""Shared provider messages retain English and explicit browser provenance."""

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.compute_base import tool_error_extra
from twicc.providers.helpers import BaseProviderHelpers, ModelVersion
from twicc.providers.state import ProviderDisabledError


@pytest.mark.parametrize("title", [None, "", "  "])
def test_empty_title_keeps_validation_shape_and_message(title):
    result = BaseProviderHelpers().validate_title(title)
    assert result == (None, "Title cannot be empty")
    assert result.error.ui_message == ui_message("backend.providers.title_empty")
    assert orjson.loads(orjson.dumps(result._asdict())) == {"title": None, "error": "Title cannot be empty"}


def test_title_limit_uses_provider_limit_and_keeps_valid_title():
    helpers = BaseProviderHelpers()
    helpers.MAX_TITLE_LENGTH = 3
    result = helpers.validate_title("four")
    assert result == (None, "Title must be 3 characters or less")
    assert result.error.ui_message == ui_message("backend.providers.title_too_long", {"limit": 3})
    assert helpers.validate_title("  yes  ") == ("yes", None)


def test_usage_default_and_disabled_error_keep_english():
    valid, message = BaseProviderHelpers().validate_usage_file_payload({})
    assert not valid
    assert message == "This provider does not support reading usage from a file"
    assert message.ui_message == ui_message("backend.providers.usage_file_unsupported")
    error = ProviderDisabledError(Provider.CODEX)
    assert str(error) == "Provider codex is disabled"
    assert error.provider == Provider.CODEX
    assert error.ui_message == ui_message("backend.providers.disabled", {"provider": "codex"})


def test_model_registry_exposes_metadata_only_for_browser_call():
    helpers = BaseProviderHelpers()
    reason = ui_message_text("Unavailable for testing", "backend.test.model_disabled")
    helpers.MODEL_VERSIONS = [ModelVersion(
        Provider.CODEX, "gpt", "6", "gpt-6", None, True, None, 1,
        enabled=False, disable_reason=reason,
    )]
    cli = helpers.serialize_model_registry()
    browser = helpers.serialize_model_registry(include_ui_messages=True)
    assert "disable_reason_ui_message" not in cli[0]
    assert browser[0].pop("disable_reason_ui_message") == reason.ui_message
    assert browser == cli
    assert orjson.loads(orjson.dumps(cli))[0]["disable_reason"] == "Unavailable for testing"


@pytest.mark.parametrize("extra", [None, '{"is_terminated":true,"lines_added":2}'])
def test_tool_error_metadata_survives_serialization_without_replacing_extra(extra):
    error = ui_message_text("Exit code 9", "backend.test.exit_code", {"code": 9})
    result = tool_error_extra(extra, error)
    parsed = orjson.loads(result)
    assert parsed.pop("error_ui_message") == error.ui_message
    assert parsed == (orjson.loads(extra) if extra is not None else {})
    assert orjson.loads(orjson.dumps({"error": error, "extra": result}))["error"] == "Exit code 9"


@pytest.mark.parametrize("extra", [None, "bad json", "[]", '{"is_terminated":true}'])
def test_external_tool_diagnostics_keep_extra_unchanged(extra):
    assert tool_error_extra(extra, "Provider diagnostic") == extra


@pytest.mark.django_db
def test_live_tool_result_persists_tagged_error_metadata(monkeypatch):
    from twicc.core.models import Project, Session, SessionItem, ToolResultLink
    from twicc.providers.codex.compute import CodexSessionCompute
    from twicc.providers.compute_base import ToolResultInfo

    session = Session.objects.create(id="ui-live", project=Project.objects.create(id="ui-live"), provider="codex")
    call = {"type": "response_item", "payload": {
        "type": "function_call", "call_id": "call", "name": "exec_command", "arguments": '{"cmd":"false"}',
    }}
    SessionItem.objects.create(session=session, line_num=1, content=orjson.dumps(call).decode())
    item = SessionItem.objects.create(session=session, line_num=2, content="{}")
    error = ui_message_text("Exit code 9", "backend.test.exit_code", {"code": 9})
    compute = CodexSessionCompute()
    monkeypatch.setattr(compute, "extract_tool_result_info", lambda *args, **kwargs: ToolResultInfo("call", True, error))
    monkeypatch.setattr(compute, "compute_link_error_override", lambda *args, **kwargs: None)
    monkeypatch.setattr(compute, "compute_link_extra", lambda *args, **kwargs: '{"is_terminated":true}')
    update = compute.create_tool_result_link_live(session.id, item, {})
    link = ToolResultLink.objects.get(session=session)
    assert link.error == update.error == "Exit code 9"
    assert orjson.loads(link.extra)["error_ui_message"] == error.ui_message
    assert orjson.loads(update.extra)["error_ui_message"] == error.ui_message


@pytest.mark.django_db(transaction=True)
def test_batch_tool_result_carries_metadata_across_worker_json(monkeypatch):
    from queue import SimpleQueue

    from twicc.core.models import Project, Session, SessionItem
    from twicc.providers.codex.compute import CodexSessionCompute

    session = Session.objects.create(id="ui-batch", project=Project.objects.create(id="ui-batch"), provider="codex")
    records = [
        {"type": "response_item", "payload": {
            "type": "function_call", "call_id": "call", "name": "exec_command", "arguments": '{"cmd":"false"}',
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call", "output": "Process exited with code 9",
        }},
    ]
    for line, record in enumerate(records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(record).decode())
    error = ui_message_text("Exit code 9", "backend.test.exit_code", {"code": 9})
    compute = CodexSessionCompute()
    original = compute.analyze_content

    def analyze(*args, **kwargs):
        analysis = original(*args, **kwargs)
        return analysis._replace(tool_result_error=error) if analysis.tool_result_id else analysis

    monkeypatch.setattr(compute, "analyze_content", analyze)
    monkeypatch.setattr(compute, "compute_link_error_override", lambda *args, **kwargs: None)
    monkeypatch.setattr(compute, "compute_link_extra", lambda *args, **kwargs: '{"is_terminated":true}')
    results = SimpleQueue()
    compute.compute_session_metadata(session.id, results, 1)
    payload = orjson.loads(results.get_nowait())
    assert payload["type"] == "session_complete"
    link = payload["tool_result_links_to_create"][0]
    assert link["error"] == "Exit code 9"
    assert orjson.loads(link["extra"]) == {"is_terminated": True, "error_ui_message": error.ui_message}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("error_kind", ["restored", "tagged", "raw"])
def test_batch_preserves_restored_error_metadata_with_new_extra(monkeypatch, error_kind):
    from queue import SimpleQueue

    from twicc.core.models import Project, Session, SessionItem, ToolResultLink
    from twicc.providers.codex.compute import CodexSessionCompute

    session = Session.objects.create(id="ui-restored", project=Project.objects.create(id="ui-restored"), provider="codex")
    records = [
        {"type": "response_item", "payload": {
            "type": "function_call", "call_id": "call", "name": "exec_command", "arguments": '{"cmd":"false"}',
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call", "output": "Provider output",
        }},
    ]
    for line, record in enumerate(records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(record).decode())
    old_error = ui_message_text("User interrupted the turn", "backend.codex.turnInterrupted")
    ToolResultLink.objects.create(
        session=session, tool_use_line_num=1, tool_result_line_num=2, tool_use_id="call",
        tool_name="exec_command", error=old_error,
        extra=orjson.dumps({"is_terminated": True, "error_ui_message": old_error.ui_message}).decode(),
    )
    new_error = {
        "restored": None,
        "tagged": ui_message_text("Exit code 9", "backend.codex.exitCode", {"code": 9}),
        "raw": "Provider diagnostic",
    }[error_kind]
    compute = CodexSessionCompute()
    original = compute.analyze_content

    def analyze(*args, **kwargs):
        analysis = original(*args, **kwargs)
        return analysis._replace(tool_result_error=new_error) if analysis.tool_result_id else analysis

    monkeypatch.setattr(compute, "analyze_content", analyze)
    monkeypatch.setattr(compute, "compute_link_error_override", lambda *args, **kwargs: None)
    new_extra = None if error_kind == "raw" else '{"is_terminated":true,"lines_added":2}'
    monkeypatch.setattr(compute, "compute_link_extra", lambda *args, **kwargs: new_extra)
    results = SimpleQueue()
    compute.compute_session_metadata(session.id, results, 1)
    payload = orjson.loads(results.get_nowait())
    link = payload["tool_result_links_to_update"][0]
    expected_error = old_error if error_kind == "restored" else new_error
    assert link["error"] == expected_error
    if error_kind == "raw":
        assert orjson.loads(link["extra"]) == {"is_terminated": True}
    else:
        assert orjson.loads(link["extra"]) == {
            "is_terminated": True, "lines_added": 2, "error_ui_message": expected_error.ui_message,
        }
