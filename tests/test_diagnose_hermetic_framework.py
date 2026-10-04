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
    assert diag.mcp_control_effect(_control(texts=['{"result": "DIAG-PONG"}']))
    assert diag.mcp_control_effect(_control(types=["mcpToolCall"]))
    assert not diag.mcp_control_effect(_control(types=["agentMessage"]))
    assert diag.interact_control_effect(_control(calls=["item/tool/requestUserInput"]))
    assert diag.interact_control_effect(_control(types=["requestUserInput"]))
    assert not diag.interact_control_effect(_control())


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


@pytest.mark.parametrize("text,status", [("NONE", "PASS"), ("none.", "PASS"), ("", "PASS"), ("**NONE**", "PASS"),
                                         ("shell\napply_patch", "FAIL")])
def test_d3_verdict(text, status):
    assert diag.d3_verdict(text)[0] == status


def test_d6b_hermetic_effect():
    ok = diag.HermeticRun(returned=True, text="I cannot", start={}, disabled_mcp_servers=("a", "diag_stub"))
    assert diag.d6b_hermetic_effect(ok, ("a",)) is None
    no_stub = ok._replace(disabled_mcp_servers=("a",))
    assert "stub" in diag.d6b_hermetic_effect(no_stub, ("a",))
    missing_user = ok._replace(disabled_mcp_servers=("diag_stub",))
    assert "a" in diag.d6b_hermetic_effect(missing_user, ("a",))
    assert "DIAG-PONG" in diag.d6b_hermetic_effect(ok._replace(text="DIAG-PONG"), ("a",))


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
