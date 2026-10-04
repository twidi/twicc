from pathlib import Path

import pytest
from asgiref.sync import async_to_sync

from twicc.providers.claude_code.hermetic import (
    HermeticClaudeResult,
    _build_options,
    check_claude_init,
    check_claude_result,
    hermetic_client_options,
)
from twicc.providers.hermetic import HermeticGuardViolation

CWD = Path("/tmp/hermetic-llm-1000")


def good_init(**over):
    init = {
        "tools": [], "mcp_servers": [], "slash_commands": [], "skills": [],
        "permissionMode": "dontAsk", "cwd": str(CWD), "model": "claude-haiku-4-5-20251001",
        "plugins": [{"name": "cc-plugin-agents-md"}], "agents": ["general-purpose"],
    }
    init.update(over)
    return init


def result(**over):
    base = {"text": "OK", "assistant_error": None, "is_error": False, "usage": {}, "init": good_init(),
            "num_turns": 1, "tool_blocks_seen": 0, "permission_callback_calls": 0}
    base.update(over)
    return HermeticClaudeResult(**base)


def test_good_init_passes():
    check_claude_init(good_init(), cwd=CWD, alias="haiku")


@pytest.mark.parametrize("field,value", [
    ("tools", ["Bash"]), ("mcp_servers", [{"name": "x"}]), ("slash_commands", ["compact"]),
    ("skills", ["pdf"]), ("permissionMode", "default"), ("cwd", "/home/me/project"),
    ("model", "claude-opus-5-5"),
])
def test_bad_init_field_is_a_violation(field, value):
    with pytest.raises(HermeticGuardViolation) as info:
        check_claude_init(good_init(**{field: value}), cwd=CWD, alias="haiku")
    assert field.split("_")[0].lower()[:4] in info.value.reason.lower()


def test_missing_init_field_is_a_violation():
    init = good_init()
    del init["tools"]
    with pytest.raises(HermeticGuardViolation):
        check_claude_init(init, cwd=CWD, alias="haiku")


def test_plugins_and_agents_are_not_checked():
    check_claude_init(good_init(plugins=[{"name": "a"}, {"name": "b"}], agents=["x", "y"]), cwd=CWD, alias="haiku")


def test_success_result_passes():
    check_claude_result(result())


def test_error_result_is_returned_untouched():
    check_claude_result(result(is_error=True, assistant_error="authentication_failed", num_turns=0))


def test_tool_activity_without_any_result_is_still_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(num_turns=None, tool_blocks_seen=1))


def test_stream_without_result_and_without_tools_passes_the_guard():
    check_claude_result(result(num_turns=None))   # the call sites treat the empty reply as a failure


def test_success_result_with_two_turns_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(num_turns=2))


def test_result_with_tool_block_is_a_violation_even_when_error():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(is_error=True, tool_blocks_seen=1))


def test_permission_callback_call_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check_claude_result(result(permission_callback_calls=1))


def test_options_builder_sets_every_field(monkeypatch, tmp_path):
    monkeypatch.setattr("twicc.providers.claude_code.hermetic.hermetic_cwd", lambda base=None: tmp_path)
    monkeypatch.setattr("twicc.provider_homes.provider_env_overlay", lambda: {"CLAUDE_CONFIG_DIR": "/c"})
    o = hermetic_client_options(model="haiku")
    assert o.model == "haiku" and o.effort == "low"
    assert o.tools == [] and o.allowed_tools == [] and o.setting_sources == []
    assert o.strict_mcp_config is True and o.permission_mode == "dontAsk" and o.max_turns == 1
    assert o.cwd == str(tmp_path) and o.system_prompt is None
    assert o.extra_args == {"no-session-persistence": None, "disable-slash-commands": None}
    assert o.env == {"CLAUDE_CONFIG_DIR": "/c", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    assert o.can_use_tool is not None


def test_deny_callback_records_and_interrupts(tmp_path):
    calls: list[str] = []
    o = _build_options(model="haiku", effort="low", cwd=tmp_path, permission_calls=calls)
    decision = async_to_sync(o.can_use_tool)("Bash", {"command": "ls"}, None)
    assert calls == ["Bash"]
    assert decision.interrupt is True
