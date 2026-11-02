"""What re-arms ASSISTANT_TURN after the final ``ResultMessage``.

The CLI keeps emitting lifecycle markers once a turn is over. Only real
conversation traffic means a new turn started; anything else must leave a
finished session in USER_TURN.
"""

from claude_agent_sdk import AssistantMessage, StreamEvent, SystemMessage, UserMessage

from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent


def test_assistant_output_is_activity() -> None:
    assert ClaudeCodeAgent._is_turn_activity(AssistantMessage(
        content=[], model="claude-opus-5",
    ))
    assert ClaudeCodeAgent._is_turn_activity(StreamEvent(
        uuid="evt-1", session_id="session-1",
        event={"type": "content_block_delta", "index": 0},
    ))
    assert ClaudeCodeAgent._is_turn_activity(UserMessage(content="tool result"))


def test_commands_changed_is_not_activity() -> None:
    """A skill/command file changed on disk — it reaches every live CLI."""
    assert not ClaudeCodeAgent._is_turn_activity(SystemMessage(
        subtype="commands_changed",
        data={"commands": [{"name": "some-skill", "description": "…"}]},
    ))


def test_unknown_system_subtypes_are_not_activity() -> None:
    for subtype in ("mcp_status", "hook_started", "some_future_marker"):
        assert not ClaudeCodeAgent._is_turn_activity(SystemMessage(subtype=subtype, data={}))


def test_settings_change_acks_are_not_activity() -> None:
    assert not ClaudeCodeAgent._is_turn_activity(SystemMessage(
        subtype="status", data={"permissionMode": "acceptEdits", "status": None},
    ))
    assert not ClaudeCodeAgent._is_turn_activity(UserMessage(
        content="<local-command-stdout>Set model to opus</local-command-stdout>",
    ))


def test_requesting_is_root_activity_but_other_statuses_are_not() -> None:
    assert ClaudeCodeAgent._is_turn_activity(SystemMessage(
        subtype="status", data={"status": "requesting"},
    ))
    assert not ClaudeCodeAgent._is_turn_activity(SystemMessage(
        subtype="status", data={"status": "idle"},
    ))


def test_child_output_is_not_root_activity() -> None:
    assert not ClaudeCodeAgent._is_turn_activity(AssistantMessage(
        content=[], model="claude-opus-5", parent_tool_use_id="child-tool",
    ))
