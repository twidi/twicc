from types import SimpleNamespace

from twicc.providers.codex.agent.active_tools import IMAGE_GEN_TOOL_NAME, active_tool_from_item


def item(**fields):
    return SimpleNamespace(**fields)


def test_sleep_item_maps_to_clock_sleep():
    assert active_tool_from_item(item(type="sleep", duration_ms=45000)) == ("clock__sleep", {"duration_ms": 45000})


def test_command_execution_maps_to_exec_command():
    assert active_tool_from_item(item(type="commandExecution", command="ls")) == ("exec_command", {"cmd": "ls"})


def test_mcp_and_dynamic_tools_get_the_canonical_prefixed_names():
    mcp = item(type="mcpToolCall", server="chrome", tool="click", arguments={"uid": "1"})
    assert active_tool_from_item(mcp) == ("mcp__chrome__click", {"uid": "1"})
    namespaced = item(type="dynamicToolCall", namespace="clock", tool="sleep", arguments="not-a-dict")
    assert active_tool_from_item(namespaced) == ("clock__sleep", {})
    bare = item(type="dynamicToolCall", namespace=None, tool="foo", arguments={})
    assert active_tool_from_item(bare) == ("foo", {})


def test_other_tool_items():
    assert active_tool_from_item(item(type="fileChange")) == ("apply_patch", {})
    assert active_tool_from_item(item(type="webSearch")) == ("web_search_call", {})
    assert active_tool_from_item(item(type="imageView")) == ("view_image", {})
    assert active_tool_from_item(item(type="imageGeneration")) == (IMAGE_GEN_TOOL_NAME, {})


def test_non_tool_items_are_ignored():
    for kind in ("agentMessage", "reasoning", "collabAgentToolCall", "subAgentActivity", "userMessage"):
        assert active_tool_from_item(item(type=kind)) is None
