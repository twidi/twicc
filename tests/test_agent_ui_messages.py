"""Browser metadata accompanies legacy English agent errors and status labels."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from twicc.agent import ephemeral
from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.exceptions import SendDeliveryError, SessionUnavailableError
from twicc.agent.states import AgentInfo, AgentState, serialize_agent_info
from twicc.agent.timeline_hooks import ProcessSnapshot, record_process_ended
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.helpers import AgentSettings


class MetadataAgent(BaseAgent):
    provider = Provider.CODEX

    def current_status_label(self):
        return "waiting for 2 subagents"

    def current_status_label_ui_message(self):
        return ui_message("backend.agent.testWaiting", {"count": 2}, count=2)


def make_agent():
    return MetadataAgent("session", "project", "/tmp", AgentSettings())


def test_snapshot_preserves_legacy_fields_and_forwards_optional_metadata():
    agent = make_agent()
    assert agent.error_ui_message is None
    agent.error = "Native detail"
    agent.error_ui_message = ui_message("backend.agent.testFailure", {"error": "Native detail"})
    info = agent.get_info()
    assert info.error == "Native detail"
    assert info.label == "waiting for 2 subagents"
    assert info.error_ui_message is agent.error_ui_message
    assert info.label_ui_message == ui_message("backend.agent.testWaiting", {"count": 2}, count=2)
    payload = serialize_agent_info(info)
    assert payload["error"] == "Native detail"
    assert payload["label"] == "waiting for 2 subagents"
    assert payload["error_ui_message"] == agent.error_ui_message
    assert payload["label_ui_message"] == info.label_ui_message
    assert orjson.loads(orjson.dumps(payload))["error"] == "Native detail"


def test_legacy_positional_snapshot_and_base_label_hook_remain_compatible():
    info = AgentInfo("s", "p", Provider.CODEX, AgentState.STARTING, None, 1.0, 2.0, 3.0)
    assert info.error_ui_message is None
    assert info.label_ui_message is None
    assert serialize_agent_info(info) == {
        "session_id": "s", "project_id": "p", "provider": Provider.CODEX,
        "state": AgentState.STARTING, "started_at": 1.0, "state_changed_at": 2.0,
    }
    assert BaseAgent.current_status_label_ui_message(make_agent()) is None


def test_process_label_broadcast_adds_metadata_only_when_provided():
    agent = make_agent()
    agent.set_hidden(False)
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.agent.base_agent.get_channel_layer", return_value=layer):
        asyncio.run(agent._broadcast_process_label("compacting"))
        assert layer.group_send.call_args.args == ("updates", {"type": "broadcast", "data": {
            "type": "process_label", "session_id": "session", "label": "compacting",
        }})
        descriptor = ui_message("backend.agent.testCompacting")
        asyncio.run(agent._broadcast_process_label("compacting", descriptor))
        assert layer.group_send.call_args.args[1]["data"]["label_ui_message"] == descriptor
        agent.set_hidden(True)
        asyncio.run(agent._broadcast_process_label("compacting", descriptor))
        assert layer.group_send.call_count == 2


def test_send_delivery_error_inherits_string_metadata_and_explicit_metadata_wins():
    message = ui_message_text("Original English", "backend.agent.testOriginal")
    error = SendDeliveryError(message, code="agent_dead")
    assert isinstance(error, RuntimeError)
    assert str(error) is message
    assert error.code == "agent_dead"
    assert error.ui_message == message.ui_message
    assert orjson.loads(orjson.dumps({"error": str(error)})) == {"error": "Original English"}
    descriptor = ui_message("backend.agent.testExplicit")
    explicit = SendDeliveryError(message, ui_message=descriptor)
    assert explicit.ui_message is descriptor
    assert str(explicit) == "Original English"
    external = SendDeliveryError("Upstream native diagnostic")
    assert external.ui_message is None
    assert external.code == "send_failed"
    assert str(external) == "Upstream native diagnostic"
    unavailable = SessionUnavailableError(message, session_id="s", reason="missing")
    assert unavailable.ui_message == message.ui_message
    assert unavailable.session_id == "s"
    assert unavailable.reason == "missing"


def test_ephemeral_guards_keep_english_and_stable_codes_with_descriptors():
    try:
        ephemeral.reserve("ui-test-ephemeral", "codex", "p")
        with pytest.raises(SendDeliveryError) as failure:
            ephemeral.check_readonly("ui-test-ephemeral")
        assert str(failure.value) == "Ephemeral sessions cannot receive another message."
        assert failure.value.code == "ephemeral_readonly"
        assert failure.value.ui_message == ui_message("backend.agent.ephemeralReadOnly")
        ephemeral.reserve("ui-test-normal", "codex", "p", ephemeral=False)
        with pytest.raises(SendDeliveryError) as failure:
            ephemeral.check_readonly("ui-test-normal", creation=True)
        assert str(failure.value) == "A session already owns this creation id."
        assert failure.value.code == "agent_starting"
        assert failure.value.ui_message == ui_message("backend.agent.creationIdOwned")
    finally:
        for key in ("ui-test-ephemeral", "ui-test-normal"):
            admission = ephemeral._known.get(key) or ephemeral._claims.get(key)
            if admission is not None:
                ephemeral.release(admission)


def test_failed_ephemeral_admission_broadcast_keeps_english_error():
    async def run():
        admission = ephemeral.reserve("ui-test-admission", "codex", "p")
        layer = SimpleNamespace(group_send=AsyncMock())
        try:
            with patch("channels.layers.get_channel_layer", return_value=layer):
                await ephemeral.finish(admission, failed=True)
            payload = layer.group_send.call_args.args[1]["data"]
            assert payload["error"] == "The ephemeral run could not start."
            assert payload["ui_message"] == ui_message("backend.agent.ephemeralStartFailed")
            assert payload["type"] == "ephemeral_admission_failed"
        finally:
            ephemeral.release(admission)
    asyncio.run(run())


def test_unresolved_delivery_receipt_preserves_code_and_provides_metadata():
    async def run():
        manager = BaseAgentManager()
        receipt = asyncio.get_running_loop().create_future()
        receipt.set_result(False)
        manager._completion_input_receipts["request"] = receipt
        with pytest.raises(SendDeliveryError) as failure:
            await manager.wait_for_completion_input("request")
        assert str(failure.value) == "Message delivery remains unresolved; do not retry"
        assert failure.value.code == "send_uncertain"
        assert failure.value.ui_message == ui_message("backend.agent.deliveryUnresolved")
    asyncio.run(run())


@pytest.mark.parametrize("descriptor", [None, ui_message("backend.agent.testFailure", {"error": "external"})])
def test_ephemeral_result_forwards_known_error_metadata_and_preserves_raw_result(descriptor):
    async def run():
        manager = BaseAgentManager()
        agent = make_agent()
        agent.ephemeral = True
        agent.ephemeral_result_emitted = False
        agent.ephemeral_started_monotonic = 0.0
        agent.ephemeral_usage = {"duration_ms": 120}
        agent.ephemeral_final_text = "Provider output stays unchanged"
        agent.error = "Native error detail"
        agent.error_ui_message = descriptor
        agent.kill_reason = "error"
        layer = SimpleNamespace(group_send=AsyncMock())
        with patch("channels.layers.get_channel_layer", return_value=layer):
            await manager._complete_ephemeral(agent, AgentState.DEAD)
        payload = layer.group_send.call_args.args[1]["data"]
        assert payload["error"] == "Native error detail"
        assert payload["text"] == "Provider output stays unchanged"
        assert payload["status"] == "error"
        if descriptor is None:
            assert "error_ui_message" not in payload
        else:
            assert payload["error_ui_message"] == descriptor
    asyncio.run(run())


@pytest.mark.parametrize("descriptor", [None, ui_message("backend.agent.testFailure", {"error": "external"})])
def test_process_ended_timeline_retains_error_cap_and_optional_descriptor(descriptor):
    agent = SimpleNamespace(session_id="s", project_id="p", provider=Provider.CODEX,
                            kill_reason="error", error="e" * 600, error_ui_message=descriptor)
    with patch("twicc.agent.timeline_hooks.record_event_sync") as record, patch(
        "twicc.agent.timeline_hooks.process_transition_events", return_value=[],
    ):
        record_process_ended(agent, ProcessSnapshot(AgentState.USER_TURN, False, None))
    data = record.call_args.kwargs["data"]
    assert data["error"] == "e" * 500
    if descriptor is None:
        assert data == {"error": "e" * 500}
    else:
        assert data["error_ui_message"] == descriptor
