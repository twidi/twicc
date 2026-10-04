from pathlib import Path
from types import SimpleNamespace

import pytest

from twicc.providers.codex import hermetic as mod
from twicc.providers.hermetic import HermeticGuardViolation

CWD = Path("/tmp/hermetic-llm-1000")
HOME = Path("/home/u/.codex")


def test_process_overrides_contain_every_item_of_the_spec():
    o = mod.process_overrides(Path("/c/cat.json"))
    assert 'model_catalog_json="/c/cat.json"' in o
    for item in ("project_doc_max_bytes=0", "skills.max_context_tokens=1", 'web_search="disabled"', "notify=[]"):
        assert item in o
    for name in ("hooks", "plugins", "apps", "browser_use", "browser_use_external", "computer_use",
                 "in_app_browser", "image_generation", "goals", "memories", "shell_tool", "unified_exec",
                 "multi_agent", "view_image", "skill_search", "tool_suggest", "sleep_tool"):
        assert f"features.{name}=false" in o
    assert len(mod.FEATURES_OFF) == 17
    assert not any(x.startswith("mcp_servers") for x in o)


def test_catalog_path_is_a_toml_basic_string():
    o = mod.process_overrides(Path('/c/we"ird\\dir/cat.json'))
    assert 'model_catalog_json="/c/we\\"ird\\\\dir/cat.json"' in o


def test_extra_overrides_are_appended():
    assert mod.process_overrides(Path("/c"), ("x=1",))[-1] == "x=1"


def test_thread_config_keeps_mcp_names_literal():
    cfg = mod.thread_config(["plain", "with-hyphen", "with.dot"])
    assert cfg["mcp_servers"] == {n: {"enabled": False} for n in ("plain", "with-hyphen", "with.dot")}


def test_thread_config_uses_dotted_keys_for_features_and_tools():
    cfg = mod.thread_config([])
    assert cfg["features.default_mode_request_user_input"] is False
    assert cfg["tools.experimental_request_user_input.enabled"] is False
    assert cfg["suppress_unstable_features_warning"] is True
    assert "features" not in cfg and "tools" not in cfg
    assert all(not isinstance(v, dict) for k, v in cfg.items() if k != "mcp_servers")


def models(*slugs, cursor=None):
    return SimpleNamespace(data=[SimpleNamespace(id=s, model=s) for s in slugs], next_cursor=cursor)


def test_model_list_with_exactly_the_requested_model_passes():
    mod.check_codex_model_list(models("gpt-6-luna"), model="gpt-6-luna")


def test_model_list_with_several_models_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("gpt-6-luna", "gpt-6-sol"), model="gpt-6-luna")


def test_model_list_with_cursor_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("gpt-6-luna", cursor="next"), model="gpt-6-luna")


def test_model_list_with_another_model_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        mod.check_codex_model_list(models("other"), model="gpt-6-luna")


def start(**over):
    s = {"model": "gpt-6-luna", "cwd": str(CWD), "sandbox": {"type": "readOnly", "network_access": False},
         "approval_policy": "never", "instruction_sources": [str(HOME / "AGENTS.md")]}
    s.update(over)
    return s


def check(s):
    mod.check_codex_thread_start(s, model="gpt-6-luna", cwd=CWD, codex_home=HOME)


def test_good_thread_start_passes_and_empty_instruction_sources_are_accepted():
    check(start())
    check(start(instruction_sources=[]))
    check(start(instruction_sources=[str(HOME / "AGENTS.override.md")]))


@pytest.mark.parametrize("over", [
    {"model": "other"}, {"cwd": "/home/me/project"},
    {"sandbox": {"type": "dangerFullAccess"}}, {"sandbox": {"type": "readOnly", "network_access": True}},
    {"approval_policy": "on-request"},
    {"instruction_sources": ["/home/me/project/AGENTS.md"]},
    {"instruction_sources": [str(HOME / "sub" / "AGENTS.md")]},
])
def test_bad_thread_start_is_a_violation(over):
    with pytest.raises(HermeticGuardViolation):
        check(start(**over))


@pytest.mark.parametrize("missing", ["sandbox", "instruction_sources", "approval_policy", "model", "cwd"])
def test_missing_thread_start_field_is_a_violation(missing):
    s = start(); del s[missing]
    with pytest.raises(HermeticGuardViolation):
        check(s)


def test_none_instruction_sources_is_a_violation():
    with pytest.raises(HermeticGuardViolation):
        check(start(instruction_sources=None))


def test_symlinked_agents_file_inside_the_home_is_accepted(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    target = tmp_path / "dotfiles-AGENTS.md"; target.write_text("x")
    (home / "AGENTS.md").symlink_to(target)
    mod.check_codex_thread_start(start(instruction_sources=[str(home / "AGENTS.md")]),
                                 model="gpt-6-luna", cwd=CWD, codex_home=home)


def test_symlinked_codex_home_instruction_file_is_accepted_after_resolution(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "home"; link.symlink_to(real)
    mod.check_codex_thread_start(start(instruction_sources=[str(real / "AGENTS.md")]),
                                 model="gpt-6-luna", cwd=CWD, codex_home=link)


@pytest.mark.parametrize("t", ["userMessage", "agentMessage", "reasoning"])
def test_allowed_item_types(t):
    mod.classify_codex_item(t)


@pytest.mark.parametrize("t", ["commandExecution", "fileChange", "mcpToolCall", "webSearch", "somethingNew", None])
def test_other_item_types_are_violations_and_named(t):
    with pytest.raises(HermeticGuardViolation) as info:
        mod.classify_codex_item(t)
    assert str(t) in info.value.reason


@pytest.mark.parametrize("method,expected", [
    ("item/commandExecution/requestApproval", {"decision": "decline"}),
    ("item/fileChange/requestApproval", {"decision": "decline"}),
    ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
    ("item/tool/requestUserInput", {"answers": {}}),
    ("mcpServer/elicitation/request", {"action": "cancel"}),
    ("something/else", {}),
])
def test_refusing_handler_replies_and_records(method, expected):
    h = mod.RefusingApprovalHandler()
    assert not h.violated.is_set()
    assert h(method, {}) == expected
    assert h.violated.is_set() and h.method == method


def test_refusing_replies_equal_the_agent_defaults():
    from twicc.providers.codex.agent.approvals import APPROVAL_METHODS, default_response_for

    assert set(mod._REFUSALS) == set(APPROVAL_METHODS)
    for method in APPROVAL_METHODS:
        assert mod._REFUSALS[method] == default_response_for(method)


def test_refusing_handler_keeps_the_first_method():
    h = mod.RefusingApprovalHandler()
    h("a/b", None); h("c/d", None)
    assert h.method == "a/b"
