import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "diagnose_hermetic_llm.py"
spec = importlib.util.spec_from_file_location("diagnose_hermetic_llm", SCRIPT)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)   # must not run anything at import


def test_exit_code_zero_with_only_pass_skip_warn():
    r = diag.Report()
    r.add("O1", diag.PASS, "ok"); r.add("O2", diag.WARN, "new key"); r.add("D8", diag.SKIP, "no line")
    assert r.exit_code() == 0


@pytest.mark.parametrize("status", [diag.FAIL, diag.INCONCLUSIVE])
def test_exit_code_one_on_fail_or_inconclusive(status):
    r = diag.Report(); r.add("D1", status, "x")
    assert r.exit_code() == 1


def test_advisory_fail_still_fails_and_is_tagged():
    r = diag.Report(); r.add("D3", diag.FAIL, "named a tool", advisory=True)
    assert r.exit_code() == 1 and "advisory" in r.render()


def test_abort_wins_over_failures():
    r = diag.Report(); r.add("D1", diag.FAIL, "x"); r.abort("codex runtime missing")
    assert r.exit_code() == 2


def test_dependent_check_is_skipped_when_its_dependency_failed():
    r = diag.Report(); r.add("D1", diag.FAIL, "no start"); r.add("D2", diag.PASS, "ok", depends_on="D1")
    resolved = {c.id: c for c in r.resolved()}
    assert resolved["D2"].status == diag.SKIP and resolved["D2"].reason == "depends on D1"
    assert r.exit_code() == 1   # D1 itself still fails


def test_json_round_trip():
    r = diag.Report(); r.add("O1", diag.PASS, "ok")
    assert json.loads(r.to_json())["checks"][0] == {"id": "O1", "status": "PASS", "reason": "ok", "advisory": False}
    # ``depends_on`` is internal: resolved checks are serialised without it.


def test_worktree_refusal():
    assert "TWICC_DATA_DIR" in diag.worktree_refusal({}, "/x/.git/worktrees/w", "/x/.git")
    assert diag.worktree_refusal({"TWICC_DATA_DIR": "/w"}, "/x/.git/worktrees/w", "/x/.git") is None
    assert diag.worktree_refusal({}, "/x/.git", "/x/.git") is None


def test_importing_the_script_ran_nothing():
    assert hasattr(diag, "main") and hasattr(diag, "MCP_STUB") and "WRITE" in diag.PROMPTS


def test_mcp_stub_answers_the_handshake(tmp_path):
    import subprocess, sys
    stub = tmp_path / "stub.py"; stub.write_text(diag.MCP_STUB)
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
             json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "diag_ping"}})]
    out = subprocess.run([sys.executable, str(stub)], input="\n".join(lines) + "\n", capture_output=True,
                         text=True, timeout=10).stdout.splitlines()
    replies = [json.loads(x) for x in out]
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert replies[1]["result"]["tools"][0]["name"] == "diag_ping"
    assert replies[2]["result"]["content"][0]["text"] == "DIAG-PONG"


# --------------------------------------------------------------------------------------
# Live-check logic (pure: no provider call)
# --------------------------------------------------------------------------------------

import asyncio  # noqa: E402


def _control(text="", types=(), texts=(), calls=()):
    return diag.ControlRun(text, list(types), list(texts), list(calls))


def _verdict(**overrides):
    kwargs = {"control": _control(), "control_error": None, "control_effect": True,
              "hermetic": diag.HermeticRun(returned=True, text="I cannot."), "hermetic_effect": None}
    return diag.canary_verdict(**{**kwargs, **overrides})


def test_guarded_turns_an_unexpected_exception_into_a_fail():
    r = diag.Report()

    def boom(report):
        raise OSError("disk gone")

    asyncio.run(diag.guarded(r, ("O3",), boom, r))
    assert r.checks == [diag.Check("O3", diag.FAIL, "unexpected error: OSError('disk gone')")]


def test_guarded_handles_coroutines_and_keeps_ids_already_reported():
    r = diag.Report()

    async def half(report):
        report.add("D1", diag.PASS, "started")
        raise TimeoutError()

    asyncio.run(diag.guarded(r, ("D1", "D2"), half, r))
    assert [(c.id, c.status) for c in r.checks] == [("D1", diag.PASS), ("D2", diag.FAIL)]


def test_guarded_does_nothing_more_on_success():
    r = diag.Report()
    asyncio.run(diag.guarded(r, ("O1",), lambda report: report.add("O1", diag.PASS, "ok"), r))
    assert [c.status for c in r.checks] == [diag.PASS]


def test_planned_live_calls():
    assert diag.planned_live_calls(True, False) == 13
    assert diag.planned_live_calls(False, True) == 17
    assert diag.planned_live_calls(True, True) == 30


def test_live_gate():
    never = lambda question: pytest.fail("must not ask")   # noqa: E731
    assert diag.live_gate_refusal(yes=True, interactive=False, ask=never) is None
    assert "--yes" in diag.live_gate_refusal(yes=False, interactive=False, ask=never)
    assert diag.live_gate_refusal(yes=False, interactive=True, ask=lambda q: "y\n") is None
    assert diag.live_gate_refusal(yes=False, interactive=True, ask=lambda q: "Yes") is None
    assert diag.live_gate_refusal(yes=False, interactive=True, ask=lambda q: "n") is not None
    assert diag.live_gate_refusal(yes=False, interactive=True, ask=lambda q: "") is not None


def test_main_live_without_yes_and_without_tty_aborts_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(diag.sys.stdin, "isatty", lambda: False, raising=False)
    ran = []
    monkeypatch.setattr(diag, "_run_all", lambda *a: ran.append(a))
    assert diag.main(["--provider", "codex", "--live"]) == 2
    out = capsys.readouterr().out
    assert ran == [] and "13 provider calls planned" in out and "--yes" in out


def test_leak_line_selection(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text("# A heading that is long enough to be a line of forty chars\nshort\n"
                 "See docs/plans for the details of this very long sentence here\n"
                 "Read the CLAUDE.md file before anything else in this project ok\n"
                 "This is the first distinctive line of the instruction file.\nlater line that is also long enough!!!!!\n")
    assert diag.leak_line(f) == "This is the first distinctive line of the instruction file."
    assert diag.leak_line(tmp_path / "missing.md") is None
    (tmp_path / "short.md").write_text("tiny\n# heading\n")
    assert diag.leak_line(tmp_path / "short.md") is None


def test_skills_block_problems():
    empty = "<skills_instructions>\n### Available skills\n\n</skills_instructions>"
    listed = "<skills_instructions>\n### Available skills\n- foo: does things\n</skills_instructions>"
    changed = "<skills_instructions>\n## Skills\n- foo\n</skills_instructions>"
    assert diag.skills_block_problems(empty) == []
    assert diag.skills_block_problems("no block at all") == []
    assert diag.skills_block_problems(listed) == ["the skills block lists a skill"]
    assert "format changed" in diag.skills_block_problems(changed)[0]


def test_control_effects():
    assert diag.write_control_effect(_control(), file_created=True)
    assert diag.write_control_effect(_control(types=["commandExecution"]), file_created=False)
    assert not diag.write_control_effect(_control(types=["agentMessage"]), file_created=False)
    assert diag.read_control_effect(_control(text="token abc123"), "abc123")
    assert not diag.read_control_effect(_control(text="cannot"), "abc123")
    assert diag.web_control_effect(_control(types=["WebSearch"]))
    assert not diag.web_control_effect(_control(types=["agentMessage", None]))
    assert diag.interact_control_effect(_control(calls=["item/tool/requestUserInput"]))
    assert diag.interact_control_effect(_control(types=["requestUserInput"]))
    assert not diag.interact_control_effect(_control())


def test_mcp_list_control_effect():
    servers = ("hey", "cloudflare-api", "node_repl")
    # The live answer of 2026-10-04 (hermetic plan, disabling removed).
    live = "mcp__cloudflare_api.docs\nmcp__hey.hey_boxes\nmcp__node_repl.js"
    assert diag.mcp_servers_named(live, servers) == ["hey", "cloudflare-api", "node_repl"]
    assert diag.mcp_list_control_effect(_control(text=live), servers)
    assert diag.mcp_list_control_effect(_control(text="cloudflare-api: search, execute"), servers)
    # A built-in MCP tool (present with every user server disabled) or built-in tools alone are no evidence.
    assert not diag.mcp_list_control_effect(_control(text="functions.exec\nmcp__cua_repl.js"), servers)
    assert not diag.mcp_list_control_effect(_control(text="They said cloudflare-apis"), servers)
    assert not diag.mcp_list_control_effect(_control(text="NONE"), servers)
    assert not diag.mcp_list_control_effect(_control(text=""), servers)


def test_canary_pass_needs_a_control_effect_and_a_text_only_answer():
    assert _verdict()[0] == diag.PASS


def test_canary_guard_violation_is_a_fail_even_without_control_effect():
    status, reason = _verdict(control=None, control_error="boom", control_effect=False,
                              hermetic=diag.HermeticRun(violation="unexpected stream item type 'commandExecution'"))
    assert status == diag.FAIL and "commandExecution" in reason


def test_canary_hermetic_effect_is_a_fail():
    status, reason = _verdict(hermetic_effect="the hermetic run created /tmp/x")
    assert status == diag.FAIL and "/tmp/x" in reason


def test_canary_hermetic_start_error_is_a_fail():
    assert _verdict(hermetic=diag.HermeticRun(error="HermeticConfigError('start')"))[0] == diag.FAIL


def test_canary_control_without_effect_is_inconclusive():
    status, reason = _verdict(control_effect=False)
    assert status == diag.INCONCLUSIVE and "no effect" in reason


def test_canary_control_error_is_inconclusive():
    status, reason = _verdict(control=None, control_error="TransportClosedError()", control_effect=False)
    assert status == diag.INCONCLUSIVE and "TransportClosedError" in reason


def test_canary_hermetic_timeout_is_inconclusive():
    assert _verdict(hermetic=diag.HermeticRun(timed_out=True))[0] == diag.INCONCLUSIVE


def test_d2_verdict():
    assert diag.d2_verdict("OK", 670)[0] == diag.PASS
    assert diag.d2_verdict("OK", 3000)[0] == diag.FAIL
    assert diag.d2_verdict("OK", None)[0] == diag.FAIL
    status, reason = diag.d2_verdict("Sure", 500)
    assert status == diag.FAIL and "input_tokens=500" in reason


@pytest.mark.parametrize("text,status", [
    ("NONE", "PASS"), ("none.", "PASS"), ("", "PASS"), ("**NONE**", "PASS"), ("  \n", "PASS"),
    # The live D11a answer of 2026-10-04: a NONE line followed by prose.
    ("NONE\n\nI don't see any tools or functions defined in the context provided to me.", "PASS"),
    ("\nNone.\nI have no tools.", "PASS"),
    ("shell\napply_patch", "FAIL"), ("shell\nNONE", "FAIL"), ("None of my tools: shell", "FAIL"),
    # A NONE first line does not hide a listed tool.
    ("NONE\nshell", "FAIL"), ("NONE\n- apply_patch", "FAIL"), ("NONE\nmcp__hey.hey_boxes", "FAIL"),
    ("NONE\n`exec_command`", "FAIL"), ("NONE\nshell\napply_patch", "FAIL"),
    ("NONE\nI could call mcp__hey.hey_boxes if it were enabled.", "FAIL"),
])
def test_d3_verdict(text, status):
    assert diag.d3_verdict(text)[0] == status


def test_d6b_hermetic_effect():
    ok = diag.HermeticRun(returned=True, text="NONE", start={}, disabled_mcp_servers=("a", "b"))
    assert diag.d6b_hermetic_effect(ok, ("a", "b")) is None
    missing_user = ok._replace(disabled_mcp_servers=("a",))
    assert "b" in diag.d6b_hermetic_effect(missing_user, ("a", "b"))
    assert "hey__hey_boxes" in diag.d6b_hermetic_effect(ok._replace(text="hey__hey_boxes"), ("a", "b"))
    for listed in ("NONE\nshell", "NONE\n- apply_patch", "NONE\nmcp__hey.hey_boxes", "NONE\nb: list_items"):
        assert diag.d6b_hermetic_effect(ok._replace(text=listed), ("a", "b")) is not None, listed
    live = "NONE\n\nI don't see any tools or functions defined in the context provided to me."
    assert diag.d6b_hermetic_effect(ok._replace(text=live), ("a", "b")) is None
    # Prose naming a server is not a listed tool.
    prose = "NONE\nThe b server is disabled for this conversation."
    assert diag.d6b_hermetic_effect(ok._replace(text=prose), ("a", "b")) is None
    # An error text is not an answer: canary_verdict turns the terminal error into INCONCLUSIVE.
    assert diag.d6b_hermetic_effect(ok._replace(text="API error", terminal_error=MODEL_ERROR), ("a", "b")) is None


def _fake_d6b(monkeypatch, *, control_text="mcp__hey.hey_boxes", hermetic=None):
    """Fakes ``_hermetic``: the control is the call made while the thread-level disabling is removed."""
    from twicc.providers.codex import hermetic as codex_hermetic

    calls = []
    real_read = codex_hermetic._read_mcp_server_names

    async def fake_plan():
        return "neutral"

    async def fake_hermetic(plan, prompt):
        is_control = codex_hermetic._read_mcp_server_names is not real_read
        calls.append(("control" if is_control else "hermetic", prompt, plan))
        if is_control:
            assert await codex_hermetic._read_mcp_server_names(None, None) == ()
            return diag.HermeticRun(returned=True, text=control_text, input_tokens=4171, start={})
        return hermetic or diag.HermeticRun(returned=True, text="NONE", input_tokens=650, start={},
                                            disabled_mcp_servers=("hey", "cf"))

    monkeypatch.setattr(diag, "_neutral_plan", fake_plan)
    monkeypatch.setattr(diag, "_hermetic", fake_hermetic)
    return calls


def _run_d6b(user_servers):
    r = diag.Report()
    asyncio.run(diag.check_d6b(r, user_servers))
    [check] = r.checks
    return check


def test_d6b_pass(monkeypatch):
    from twicc.providers.codex import hermetic as codex_hermetic

    real_read = codex_hermetic._read_mcp_server_names
    calls = _fake_d6b(monkeypatch)
    check = _run_d6b(("hey", "cf"))
    assert check.status == diag.PASS and "disabled: 2" in check.reason and "['hey']" in check.reason
    assert "4171 enabled vs 650 disabled" in check.reason
    control, hermetic = calls
    assert control[0] == "control" and hermetic[0] == "hermetic"
    assert "do not call any tool" in control[1].lower() and hermetic[1] == control[1]
    assert control[2] == hermetic[2] == "neutral"   # the same neutral plan for both
    assert codex_hermetic._read_mcp_server_names is real_read   # the patch is undone


def test_mcp_disabling_removed_is_undone_on_error():
    from twicc.providers.codex import hermetic as codex_hermetic

    real_read = codex_hermetic._read_mcp_server_names
    with pytest.raises(RuntimeError), diag.mcp_disabling_removed():
        assert codex_hermetic._read_mcp_server_names is not real_read
        raise RuntimeError
    assert codex_hermetic._read_mcp_server_names is real_read


@pytest.mark.parametrize("control_run,fragment", [
    (diag.HermeticRun(violation="unexpected stream item type 'mcpToolCall'"), "guard violation"),
    (diag.HermeticRun(timed_out=True), "timed out"),
    (diag.HermeticRun(returned=True, terminal_error="usage limit reached"), "usage limit reached"),
])
def test_d6b_control_failure_is_inconclusive(monkeypatch, control_run, fragment):
    from twicc.providers.codex import hermetic as codex_hermetic

    _fake_d6b(monkeypatch)
    fake_hermetic = diag._hermetic

    async def hermetic(plan, prompt):
        if codex_hermetic._read_mcp_server_names.__name__ == "no_names":   # the control
            return control_run
        return await fake_hermetic(plan, prompt)

    monkeypatch.setattr(diag, "_hermetic", hermetic)
    check = _run_d6b(("hey", "cf"))
    assert check.status == diag.INCONCLUSIVE and fragment in check.reason


def test_d6b_without_user_servers_is_a_skip_with_no_call(monkeypatch):
    calls = _fake_d6b(monkeypatch)
    check = _run_d6b(())
    assert check.status == diag.SKIP and "no MCP server" in check.reason and calls == []


def test_d6b_unreadable_server_list_is_inconclusive(monkeypatch):
    calls = _fake_d6b(monkeypatch)
    assert _run_d6b(None).status == diag.INCONCLUSIVE and calls == []


@pytest.mark.parametrize("control_text", ["NONE", "functions.exec\nfunctions.wait"])
def test_d6b_control_naming_no_mcp_tool_is_inconclusive(monkeypatch, control_text):
    _fake_d6b(monkeypatch, control_text=control_text)
    check = _run_d6b(("hey", "cf"))
    assert check.status == diag.INCONCLUSIVE and "no effect" in check.reason


@pytest.mark.parametrize("hermetic", [
    diag.HermeticRun(returned=True, text="hey__hey_boxes", start={}, disabled_mcp_servers=("hey", "cf")),
    diag.HermeticRun(returned=True, text="NONE", start={}, disabled_mcp_servers=("hey",)),
    diag.HermeticRun(violation="unexpected stream item type 'mcpToolCall'"),
])
def test_d6b_hermetic_tool_or_enabled_server_is_a_fail(monkeypatch, hermetic):
    _fake_d6b(monkeypatch, hermetic=hermetic)
    assert _run_d6b(("hey", "cf")).status == diag.FAIL


def test_d7_refused_request_is_a_fail():
    run = diag.HermeticRun(returned=True, text="cannot", refused_method="item/tool/requestUserInput")
    status, reason = _verdict(hermetic=run, hermetic_effect=diag.d7_hermetic_effect(run))
    assert status == diag.FAIL and "item/tool/requestUserInput" in reason
    assert diag.d7_hermetic_effect(diag.HermeticRun(returned=True)) is None


def test_d8_verdict():
    assert diag.d8_verdict(None, None, "anything")[0] == diag.SKIP
    assert diag.d8_verdict("repo line", "global line", "... repo line ...")[0] == diag.FAIL
    assert diag.d8_verdict("repo line", "global line", "global line")[0] == diag.WARN
    assert diag.d8_verdict("repo line", None, "NONE")[0] == diag.PASS


def test_codex_live_skips_everything_after_a_failed_d1(monkeypatch):
    async def failed_d1(report):
        report.add("D1", diag.FAIL, "no start")

    monkeypatch.setattr(diag, "check_d1_d2", failed_d1)
    r = diag.Report()
    asyncio.run(diag.run_codex_live(r))
    resolved = {c.id: c.status for c in r.resolved()}
    assert resolved.pop("D1") == diag.FAIL
    assert set(resolved) == {"D2", "D3", "D4", "D5", "D6a", "D6b", "D7", "D8"}
    assert set(resolved.values()) == {diag.SKIP}


def test_d4_wiring_with_fakes_removes_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(diag.tempfile, "gettempdir", lambda: str(tmp_path))
    created = []

    async def fake_control(prompt, *, user_servers, **kwargs):
        path = diag.Path(prompt.split("create the file ")[1].split(" containing")[0])
        path.write_text("OK")
        created.append(path)
        return _control(types=["commandExecution"]), None

    async def fake_plan(extra=()):
        return None

    async def fake_hermetic(plan, prompt):
        assert not created[0].exists()   # the control's file is removed before the hermetic run
        return diag.HermeticRun(returned=True, text="I cannot run commands.")

    monkeypatch.setattr(diag, "_control", fake_control)
    monkeypatch.setattr(diag, "_neutral_plan", fake_plan)
    monkeypatch.setattr(diag, "_hermetic", fake_hermetic)
    r = diag.Report()
    asyncio.run(diag.check_d4(r, ()))
    assert [(c.id, c.status) for c in r.checks] == [("D4", diag.PASS)]
    assert list(tmp_path.iterdir()) == []


# A non-retrying ErrorNotification: the model never processed the prompt, so nothing may PASS.

MODEL_ERROR = "usage limit reached"


def test_describe_terminal_error():
    from types import SimpleNamespace
    assert diag.describe_terminal_error(None) is None
    assert diag.describe_terminal_error(SimpleNamespace(error=SimpleNamespace(message=MODEL_ERROR))) == MODEL_ERROR
    assert "boom" in diag.describe_terminal_error(SimpleNamespace(error=None, detail="boom"))


def test_canary_terminal_error_is_inconclusive_not_pass():
    status, reason = _verdict(hermetic=diag.HermeticRun(returned=True, terminal_error=MODEL_ERROR))
    assert status == diag.INCONCLUSIVE and MODEL_ERROR in reason


def test_canary_terminal_error_does_not_hide_a_hermetic_failure():
    run = diag.HermeticRun(returned=True, terminal_error=MODEL_ERROR)
    assert _verdict(hermetic=run, hermetic_effect="the hermetic run created /tmp/x")[0] == diag.FAIL
    assert _verdict(hermetic=run._replace(returned=False, violation="refused request: x"))[0] == diag.FAIL


def test_d2_verdict_terminal_error_is_inconclusive():
    status, reason = diag.d2_verdict("", None, MODEL_ERROR)
    assert status == diag.INCONCLUSIVE and MODEL_ERROR in reason and "input_tokens=None" in reason


def test_d3_verdict_terminal_error():
    status, reason = diag.d3_verdict("", MODEL_ERROR)
    assert status == diag.INCONCLUSIVE and MODEL_ERROR in reason
    assert diag.d3_verdict("shell", MODEL_ERROR)[0] == diag.FAIL   # a named tool still fails


def test_d8_verdict_terminal_error():
    status, reason = diag.d8_verdict("repo line", "global line", "", MODEL_ERROR)
    assert status == diag.INCONCLUSIVE and MODEL_ERROR in reason
    assert diag.d8_verdict("repo line", None, "repo line", MODEL_ERROR)[0] == diag.FAIL


def test_hermetic_terminal_error_wiring_yields_inconclusive(monkeypatch):
    errored = diag.HermeticRun(returned=True, text="", terminal_error=MODEL_ERROR)

    async def fake_control(prompt, *, user_servers, **kwargs):
        token = None
        return _control(text="anything", types=["webSearch"]), token

    async def fake_plan(extra=()):
        return None

    async def fake_hermetic(plan, prompt):
        return errored

    monkeypatch.setattr(diag, "_control", fake_control)
    monkeypatch.setattr(diag, "_neutral_plan", fake_plan)
    monkeypatch.setattr(diag, "_hermetic", fake_hermetic)
    r = diag.Report()
    asyncio.run(diag.check_d6a(r, ()))
    asyncio.run(diag.check_d3(r))
    assert [(c.id, c.status) for c in r.checks] == [("D6a", diag.INCONCLUSIVE), ("D3", diag.INCONCLUSIVE)]
    assert all(MODEL_ERROR in c.reason for c in r.checks)


# --------------------------------------------------------------------------------------
# Claude live checks (pure: no provider call)
# --------------------------------------------------------------------------------------

def _claude_result(**overrides):
    from twicc.providers.claude_code.hermetic import HermeticClaudeResult

    base = {"text": "I cannot.", "assistant_error": None, "is_error": False, "usage": {}, "init": {},
            "num_turns": 1, "tool_blocks_seen": 0, "permission_callback_calls": 0}
    return HermeticClaudeResult(**{**base, **overrides})


def test_claude_options_are_a_dataclass():
    import dataclasses

    from claude_agent_sdk import ClaudeAgentOptions
    assert dataclasses.is_dataclass(ClaudeAgentOptions)


def test_unrestricted_drops_every_restriction_and_keeps_the_rest(tmp_path):
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from twicc.providers.claude_code.hermetic import _build_options

    original = _build_options(model="haiku", effort="low", cwd=tmp_path, permission_calls=[])
    control = diag.unrestricted(original)
    assert control.permission_mode == "bypassPermissions"
    assert control.tools is None and control.setting_sources is None and control.strict_mcp_config is False
    assert control.can_use_tool is None and control.system_prompt is None
    assert control.max_turns == diag.CONTROL_MAX_TURNS == 8   # bounded: a looping control must not burn tokens
    assert control.extra_args == {"no-session-persistence": None}   # no disable-slash-commands
    assert control.cwd == str(tmp_path) and control.model == "haiku" and control.env == original.env
    assert original.permission_mode == "dontAsk" and original.tools == []   # the original is not mutated
    transport = SubprocessCLITransport(prompt="x", options=control)
    transport._cli_path = "claude"
    cmd = transport._build_command()
    assert "--disable-slash-commands" not in cmd and "--strict-mcp-config" not in cmd and "--tools" not in cmd
    assert not any(part.startswith("--setting-sources") for part in cmd)
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert cmd[cmd.index("--max-turns") + 1] == "8"


def test_unrestricted_options_fall_back_to_a_copy_for_a_non_dataclass():
    class Plain:
        permission_mode = "dontAsk"

    original = Plain()
    clone = diag._replace_options(original, permission_mode="bypassPermissions")
    assert clone.permission_mode == "bypassPermissions" and original.permission_mode == "dontAsk"


def test_unrestricted_with_mcp_stub_adds_the_stub_explicitly(tmp_path):
    from twicc.providers.claude_code.hermetic import _build_options

    stub = tmp_path / "stub.py"
    control = diag.unrestricted_with_mcp_stub(stub)(
        _build_options(model="haiku", effort="low", cwd=tmp_path, permission_calls=[]),
    )
    assert control.mcp_servers == {"diag_stub": {"command": diag.sys.executable, "args": [str(stub)]}}
    assert control.permission_mode == "bypassPermissions" and control.strict_mcp_config is False


def _unrestricted_base(cwd):
    from twicc.providers.claude_code.hermetic import _build_options

    return _build_options(model="haiku", effort="low", cwd=diag.Path(cwd), permission_calls=[])


def test_unrestricted_with_recording_callback(tmp_path):
    from claude_agent_sdk import PermissionResultDeny
    from claude_agent_sdk.types import CanUseToolShadowedWarning, _configure_can_use_tool

    original = _unrestricted_base(tmp_path)
    recorded = []
    control = diag.unrestricted_with_recording_callback(recorded)(original)
    plain = diag.unrestricted(original)
    # Every unrestricted setting is kept; only the callback differs.
    for field in ("permission_mode", "tools", "setting_sources", "strict_mcp_config", "max_turns", "system_prompt",
                  "extra_args", "cwd", "model", "env"):
        assert getattr(control, field) == getattr(plain, field), field
    assert control.permission_mode == "bypassPermissions"
    assert control.can_use_tool is not None and control.can_use_tool is not original.can_use_tool
    # The callback switches the stdio permission prompt on, as in production.
    with pytest.warns(CanUseToolShadowedWarning):   # the SDK's advisory; measured wrong for the question tool
        assert _configure_can_use_tool(control).permission_prompt_tool_name == "stdio"
    decision = asyncio.run(control.can_use_tool("AskUserQuestion", {}, None))
    assert isinstance(decision, PermissionResultDeny) and decision.interrupt and recorded == ["AskUserQuestion"]


def test_write_claude_fixture(tmp_path):
    stub = tmp_path / "stub.py"
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    diag.write_claude_fixture(fixture, stub)
    assert json.loads((fixture / ".mcp.json").read_text()) == {
        "mcpServers": {"diag_stub": {"command": diag.sys.executable, "args": [str(stub)]}},
    }
    skill = (fixture / ".claude" / "skills" / "diag-skill" / "SKILL.md").read_text()
    assert skill == ("---\nname: diag-skill\ndescription: When asked to run the diag skill, reply with the single "
                     "word DIAG-SKILL-OK.\n---\nReply with the single word DIAG-SKILL-OK.\n")
    assert sorted(p.name for p in fixture.iterdir()) == [".claude", ".mcp.json"]


def test_loopback_listener_counts_hits_and_stops():
    import urllib.request

    with diag.LoopbackListener() as listener:
        assert listener.url.startswith("http://127.0.0.1:") and listener.url.endswith("/")
        assert listener.hits == 0
        with urllib.request.urlopen(listener.url, timeout=5) as response:
            assert response.read() == b"DIAG-WEB-OK"
        assert listener.hits == 1
        url = listener.url
    with pytest.raises(OSError):
        urllib.request.urlopen(url, timeout=2)


def test_claude_prompt_tokens():
    assert diag.claude_prompt_tokens({}) == 0
    assert diag.claude_prompt_tokens({"input_tokens": 10, "cache_creation_input_tokens": 20,
                                      "cache_read_input_tokens": 400, "output_tokens": 999}) == 430
    assert diag.claude_prompt_tokens({"input_tokens": 5, "cache_read_input_tokens": None}) == 5


def test_claude_error():
    assert diag.claude_error(_claude_result()) is None
    assert "rate_limit" in diag.claude_error(_claude_result(assistant_error="rate_limit"))
    assert "usage limit" in diag.claude_error(_claude_result(is_error=True, text="usage limit"))
    assert "no result" in diag.claude_error(_claude_result(num_turns=None))


def test_d10_verdict():
    assert diag.d10_verdict("OK", 427, None)[0] == diag.PASS
    status, reason = diag.d10_verdict("OK", 3000, None)
    assert status == diag.FAIL and "tokens=3000" in reason
    assert diag.d10_verdict("Sure", 400, None)[0] == diag.FAIL
    status, reason = diag.d10_verdict("", 0, "error result")
    assert status == diag.INCONCLUSIVE and "error result" in reason and "tokens=0" in reason


def test_claude_control_effect():
    tool = _claude_result(tool_blocks_seen=1)
    none = _claude_result()
    assert diag.claude_control_effect("WRITE", none, file_created=True)
    assert diag.claude_control_effect("WRITE", tool)
    assert not diag.claude_control_effect("WRITE", none)
    assert diag.claude_control_effect("READ", _claude_result(text="abc123"), token="abc123")
    assert not diag.claude_control_effect("READ", tool, token="abc123")
    assert diag.claude_control_effect("INTERACT", tool) and not diag.claude_control_effect("INTERACT", none)
    assert diag.claude_control_effect("MCP", _claude_result(text="DIAG-PONG"))
    assert diag.claude_control_effect("SKILL", _claude_result(text="DIAG-SKILL-OK"))
    assert not diag.claude_control_effect("SKILL", none)
    assert diag.claude_control_effect("WEB", tool, listener_hits=1)
    assert not diag.claude_control_effect("WEB", tool, listener_hits=0)
    assert not diag.claude_control_effect("WEB", none, listener_hits=1)


def test_claude_mcp_and_skill_controls_need_the_fixture_reached():
    # A tool block alone (ToolSearch, Glob, Bash ls while looking for the fixture) proves nothing.
    searched = _claude_result(tool_blocks_seen=1, init={"mcp_servers": [{"name": "other"}], "skills": ["other"]})
    assert not diag.claude_control_effect("MCP", searched)
    assert not diag.claude_control_effect("SKILL", searched)
    assert not diag.claude_control_effect("MCP", _claude_result(init={"mcp_servers": [{"name": "diag_stub"}]}))
    assert not diag.claude_control_effect("SKILL", _claude_result(init={"skills": ["diag-skill"]}))
    for servers in ([{"name": "diag_stub", "status": "connected"}], ["diag_stub"]):
        assert diag.claude_control_effect("MCP", _claude_result(tool_blocks_seen=1, init={"mcp_servers": servers}))
    for init in ({"skills": ["diag-skill"]}, {"slash_commands": ["diag-skill"]}, {"skills": [{"name": "diag-skill"}]},
                 {"slash_commands": ["project:diag-skill"]}):
        assert diag.claude_control_effect("SKILL", _claude_result(tool_blocks_seen=1, init=init))
    status, reason = _claude_verdict(control=searched, control_effect=diag.claude_control_effect("MCP", searched))
    assert status == diag.INCONCLUSIVE and "no effect" in reason


def test_init_names():
    init = {"mcp_servers": [{"name": "a", "status": "connected"}, "b", {"status": "x"}, None]}
    assert diag.init_names(init, "mcp_servers") == ["a", "b"]
    assert diag.init_names({}, "skills") == [] and diag.init_names({"skills": None}, "skills") == []


def test_claude_hermetic_effect():
    assert diag.claude_hermetic_effect(_claude_result(), markers=diag.D11C_MARKERS) is None
    assert "tool block" in diag.claude_hermetic_effect(_claude_result(tool_blocks_seen=2))
    assert "permission" in diag.claude_hermetic_effect(_claude_result(permission_callback_calls=1))
    assert "listener" in diag.claude_hermetic_effect(_claude_result(), listener_hits=1)
    for marker in ("DIAG-PONG", "DIAG-SKILL-OK", "DIAG-WEB-OK"):
        assert marker in diag.claude_hermetic_effect(_claude_result(text=f"x {marker}"), markers=diag.D11C_MARKERS)


def _claude_verdict(control=None, control_error=None, control_effect=True, result=None, hermetic_effect=None):
    result = result if result is not None else _claude_result()
    return diag.claude_canary_verdict(
        control=control if control is not None else _claude_result(tool_blocks_seen=1),
        control_error=control_error, control_effect=control_effect,
        hermetic=diag.claude_hermetic_run(result), hermetic_effect=hermetic_effect,
    )


def test_claude_canary_pass():
    assert _claude_verdict()[0] == diag.PASS


def test_claude_canary_violation_is_a_fail_even_with_an_error_result():
    result = _claude_result(is_error=True, violation="tool activity: 1 tool block(s), 0 permission request(s)")
    status, reason = _claude_verdict(control_effect=False, result=result)
    assert status == diag.FAIL and "tool activity" in reason


def test_claude_canary_error_result_is_inconclusive():
    for result in (_claude_result(is_error=True, text="usage limit"), _claude_result(assistant_error="rate_limit")):
        assert _claude_verdict(result=result)[0] == diag.INCONCLUSIVE


def test_claude_canary_control_without_effect_is_inconclusive():
    status, reason = _claude_verdict(control=_claude_result(), control_effect=False)
    assert status == diag.INCONCLUSIVE and "no effect" in reason
    status, reason = _claude_verdict(control=_claude_result(is_error=True, text="overloaded"), control_effect=False)
    assert status == diag.INCONCLUSIVE and "overloaded" in reason
    status, reason = _claude_verdict(control=None, control_error=diag.TIMED_OUT, control_effect=False)
    assert status == diag.INCONCLUSIVE and diag.TIMED_OUT in reason


def test_claude_canary_hermetic_effect_is_a_fail():
    status, reason = _claude_verdict(hermetic_effect="the hermetic answer contains DIAG-PONG")
    assert status == diag.FAIL and "DIAG-PONG" in reason


def test_d12_verdict():
    clean = diag.claude_hermetic_run(_claude_result(text="NONE"))
    cwd = diag.claude_hermetic_run(_claude_result(text="/tmp/hermetic-llm-1000"))
    assert diag.d12_verdict("repo line", "global line", clean, cwd)[0] == diag.PASS
    assert diag.d12_verdict(None, None, None, cwd)[0] == diag.PASS
    leaked = diag.claude_hermetic_run(_claude_result(text="... global line ..."))
    assert diag.d12_verdict("repo line", "global line", leaked, cwd)[0] == diag.FAIL
    leaked = diag.claude_hermetic_run(_claude_result(text="repo line"))
    assert diag.d12_verdict("repo line", None, leaked, cwd)[0] == diag.FAIL
    named = diag.claude_hermetic_run(_claude_result(text="/home/x/dev/TwiCC-poc"))
    status, reason = diag.d12_verdict("repo line", None, clean, named)
    assert status == diag.FAIL and "TwiCC-poc" in reason
    violated = diag.claude_hermetic_run(_claude_result(violation="init reports non-empty tools"))
    assert diag.d12_verdict("repo line", None, violated, cwd)[0] == diag.FAIL
    errored = diag.claude_hermetic_run(_claude_result(text="", is_error=True))
    assert diag.d12_verdict("repo line", None, clean, errored)[0] == diag.INCONCLUSIVE
    assert diag.d12_verdict("repo line", None, diag.HermeticRun(timed_out=True), cwd)[0] == diag.INCONCLUSIVE


def test_d14(monkeypatch, tmp_path):
    import twicc.providers.hermetic as shared

    monkeypatch.setattr(shared, "hermetic_cwd", lambda: tmp_path)
    r = diag.Report()
    diag.check_d14_neutral_dir_still_empty(r)
    assert [(c.id, c.status) for c in r.checks] == [("D14", diag.PASS)]

    def dirty():
        raise shared.HermeticConfigError("cwd", "the directory holds 1 entry: .claude")

    monkeypatch.setattr(shared, "hermetic_cwd", dirty)
    r = diag.Report()
    diag.check_d14_neutral_dir_still_empty(r)
    assert r.checks[0].status == diag.FAIL and ".claude" in r.checks[0].reason


CLAUDE_IDS = {"D10", "D11a", "D11b-write", "D11b-read", "D11b-interact", "D11c-mcp", "D11c-skill", "D11c-web", "D12"}


def test_claude_live_skips_everything_after_a_failed_d9(monkeypatch):
    from twicc.providers.hermetic import HermeticGuardViolation

    async def violated(prompt):
        raise HermeticGuardViolation("init reports non-empty tools: ['Bash']")

    async def never(*args, **kwargs):
        pytest.fail("no call may run after a failed D9")

    monkeypatch.setattr(diag, "_claude_production", violated)
    monkeypatch.setattr(diag, "_claude_control", never)
    monkeypatch.setattr(diag, "_claude_hermetic", never)
    r = diag.Report()
    asyncio.run(diag.run_claude_live(r))
    resolved = {c.id: c for c in r.resolved()}
    assert resolved.pop("D9").status == diag.FAIL
    assert set(resolved) == CLAUDE_IDS and {c.status for c in resolved.values()} == {diag.SKIP}


def _fake_claude(monkeypatch, tmp_path, hermetic_results=None, interact_tool="AskUserQuestion"):
    """Fakes for every provider call of ``run_claude_live``; the controls really produce their effects."""
    import urllib.request

    monkeypatch.setattr(diag.tempfile, "tempdir", str(tmp_path))
    calls = []
    hermetic_results = hermetic_results or {}

    async def production(prompt):
        calls.append(("production", prompt))
        return _claude_result(text="OK", usage={"input_tokens": 10, "cache_read_input_tokens": 400}, init={
            "tools": [], "mcp_servers": [], "slash_commands": [], "skills": [], "permissionMode": "dontAsk",
            "model": "claude-haiku-4-5"})

    async def control(prompt, cwd, *, override=diag.unrestricted):
        calls.append(("control", prompt))
        if "create the file" in prompt:
            diag.Path(prompt.split("create the file ")[1].split(" containing")[0]).write_text("OK")
            return _claude_result(tool_blocks_seen=1), None
        if "read the file" in prompt:
            token = diag.Path(prompt.split("read the file ")[1].split(" with a tool")[0]).read_text()
            return _claude_result(text=token, tool_blocks_seen=1), None
        if "fetch the URL" in prompt:
            assert (diag.Path(cwd) / ".mcp.json").is_file()
            url = prompt.split("fetch the URL ")[1].split(" ")[0]
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(url, timeout=5).read().decode())
            return _claude_result(text=body, tool_blocks_seen=1), None
        if "diag_ping" in prompt:
            assert override is not diag.unrestricted   # the MCP control carries the stub explicitly
        if "which color" in prompt:   # the interaction control carries a recording callback: the model calls a tool
            await override(_unrestricted_base(cwd)).can_use_tool(interact_tool, {}, None)
        return _claude_result(tool_blocks_seen=1, init={
            "mcp_servers": [{"name": "diag_stub"}, {"status": "nameless"}], "skills": ["diag-skill"]}), None

    async def hermetic(prompt, cwd=None):
        calls.append(("hermetic", prompt, cwd, diag.os.getcwd()))
        for fragment, result in hermetic_results.items():
            if fragment in prompt:
                return diag.claude_hermetic_run(result), result
        result = _claude_result(text="NONE" if "tool or function" in prompt or "verbatim" in prompt else "I cannot.")
        return diag.claude_hermetic_run(result), result

    monkeypatch.setattr(diag, "_claude_production", production)
    monkeypatch.setattr(diag, "_claude_control", control)
    monkeypatch.setattr(diag, "_claude_hermetic", hermetic)
    return calls


def test_claude_live_wiring_with_fakes(monkeypatch, tmp_path, capsys):
    calls = _fake_claude(monkeypatch, tmp_path)
    monkeypatch.chdir(diag.REPO_ROOT / "tests")   # not the repository root: D12 must move there itself
    before = diag.os.getcwd()
    r = diag.Report()
    asyncio.run(diag.run_claude_live(r))
    statuses = {c.id: c.status for c in r.resolved()}
    assert statuses == {"D9": diag.PASS, **{check_id: diag.PASS for check_id in CLAUDE_IDS}}, r.render()
    assert "tokens=410" in next(c.reason for c in r.checks if c.id == "D10")
    assert all(c.depends_on == "D9" for c in r.checks if c.id != "D9")
    assert len(calls) == 16 <= diag.PLANNED_LIVE_CALLS["claude"]
    d11c = [call for call in calls if call[0] == "hermetic" and call[2] is not None]
    assert len(d11c) == 3   # the D11c hermetic runs use the fixture as cwd, the others the neutral directory
    cwd_call = next(call for call in calls if call[0] == "hermetic" and "working directory" in call[1])
    assert cwd_call[3] == str(diag.REPO_ROOT) and diag.os.getcwd() == before
    assert list(tmp_path.iterdir()) == []   # fixture, stub, token and target files are all removed
    assert "['diag_stub', {'status': 'nameless'}]" in capsys.readouterr().err   # a nameless entry is shown whole


def test_claude_live_hermetic_violation_or_error_result(monkeypatch, tmp_path):
    _fake_claude(monkeypatch, tmp_path, hermetic_results={
        "diag_ping": _claude_result(violation="tool activity: 1 tool block(s), 0 permission request(s)"),
        "diag-skill": _claude_result(text="", is_error=True),
        "fetch the URL": _claude_result(text="DIAG-WEB-OK"),
    })
    r = diag.Report()
    asyncio.run(diag.run_claude_live(r))
    statuses = {c.id: c.status for c in r.resolved()}
    assert statuses["D11c-mcp"] == diag.FAIL
    assert statuses["D11c-skill"] == diag.INCONCLUSIVE
    assert statuses["D11c-web"] == diag.FAIL
    assert statuses["D11b-write"] == diag.PASS


# An error result ("API Error: ...", ``error`` set) is not an answer: never PASS, never FAIL.

API_ERROR = {"assistant_error": "rate_limit", "text": "API Error: rate limited"}


def test_d11a_error_result_is_inconclusive():
    status, reason = diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(**API_ERROR)))
    assert status == diag.INCONCLUSIVE and "rate_limit" in reason
    status, _ = diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(is_error=True, text="API Error: overloaded")))
    assert status == diag.INCONCLUSIVE
    violated = _claude_result(violation="tool activity: 1 tool block(s), 0 permission request(s)", **API_ERROR)
    assert diag.d11a_verdict(diag.claude_hermetic_run(violated))[0] == diag.FAIL
    assert diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(text="Bash\nRead")))[0] == diag.FAIL
    assert diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(text="NONE")))[0] == diag.PASS
    live = "NONE\n\nI don't see any tools or functions defined in the context provided to me for this conversation."
    assert diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(text=live)))[0] == diag.PASS
    for listed in ("NONE\nshell", "NONE\n- apply_patch", "NONE\nmcp__hey.hey_boxes"):
        assert diag.d11a_verdict(diag.claude_hermetic_run(_claude_result(text=listed)))[0] == diag.FAIL, listed


def test_d10_error_result_is_inconclusive():
    result = _claude_result(**API_ERROR)
    status, reason = diag.d10_verdict(result.text, 0, diag.claude_error(result))
    assert status == diag.INCONCLUSIVE and "rate_limit" in reason


def test_d12_error_text_never_counts():
    errored = diag.claude_hermetic_run(_claude_result(assistant_error="rate_limit", text="API Error twicc repo line"))
    cwd = diag.claude_hermetic_run(_claude_result(text="/tmp/hermetic-llm-1000"))
    assert diag.d12_verdict("repo line", None, errored, cwd)[0] == diag.INCONCLUSIVE
    assert diag.d12_verdict("repo line", None, diag.claude_hermetic_run(_claude_result(text="NONE")), errored)[0] \
        == diag.INCONCLUSIVE
    leaked = diag.claude_hermetic_run(_claude_result(text="repo line"))
    assert diag.d12_verdict("repo line", None, leaked, errored)[0] == diag.FAIL   # real evidence still fails


def test_claude_canary_error_text_with_a_marker_is_inconclusive(monkeypatch, tmp_path):
    _fake_claude(monkeypatch, tmp_path, hermetic_results={
        "diag_ping": _claude_result(assistant_error="rate_limit", text="API Error: DIAG-PONG"),
    })
    r = diag.Report()
    asyncio.run(diag.run_claude_live(r))
    statuses = {c.id: c.status for c in r.resolved()}
    assert statuses["D11c-mcp"] == diag.INCONCLUSIVE


def test_claude_interaction_control_needs_the_question_tool(monkeypatch, tmp_path):
    # Another tool reaching the callback (with a tool block) is not the interaction effect.
    _fake_claude(monkeypatch, tmp_path, interact_tool="Bash")
    r = diag.Report()
    asyncio.run(diag.check_d11b_interact(r))
    [check] = r.checks
    assert check.status == diag.INCONCLUSIVE and "no effect" in check.reason
