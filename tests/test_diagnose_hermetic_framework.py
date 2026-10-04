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
