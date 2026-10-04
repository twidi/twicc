"""Read-only chained title regression, with only model calls replaced."""

import asyncio
import importlib.util
import logging
from pathlib import Path
import subprocess
import sys

import orjson
import pytest


@pytest.fixture
def diagnostic():
    path = Path(__file__).resolve().parents[1] / "scripts/diagnose_automatic_titles.py"
    assert path.exists(), "automatic title diagnostic is not implemented"
    spec = importlib.util.spec_from_file_location("automatic_title_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_data():
    return {"cases": [{"id": "café", "messages": ["OAuth", "Token refresh", "Billing", "Invoices"],
                       "check_counts": [1, 2, 3, 4], "expected_subjects": ["OAuth", "Billing"]}]}


@pytest.mark.parametrize("flags", [[], ["--yes"], ["--live"]])
def test_cli_requires_both_live_flags(diagnostic, tmp_path, monkeypatch, flags):
    async def forbidden(*args):
        pytest.fail("provider called without both consent flags")
    monkeypatch.setattr(diagnostic, "call_model", forbidden)
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_bytes(orjson.dumps(fixture_data()))
    assert diagnostic.main(["--input", str(source), "--output", str(output), *flags]) == 0
    report = orjson.loads(output.read_bytes())
    assert report["live"] is False
    assert report["aggregate"]["call_count"] == 0
    assert report["planned_checks"] == 8


def test_chains_only_accepted_titles_and_prefixes(diagnostic):
    prompts = []
    replies = iter([" OAuth ", "OAuth", "", "OAuth Billing"])
    async def fake(provider, prompt):
        prompts.append(prompt)
        return next(replies)
    report = asyncio.run(diagnostic.run_cases(fixture_data(), ["haiku"], "Title: {text}", call=fake))
    assert prompts[0] == "Title: OAuth"
    assert prompts[1].startswith("Title: [Message 1]\nOAuth\n\n[Message 2]\nToken refresh")
    assert "Billing" not in prompts[1]
    assert all("<current_title>OAuth</current_title>" in prompt for prompt in prompts[1:])
    assert [row["title"] for row in report["checks"]] == ["OAuth", "OAuth", None, "OAuth Billing"]
    assert [row["kept"] for row in report["checks"]] == [False, True, False, False]
    assert report["aggregate"]["call_count"] == 4
    assert report["aggregate"]["failure_count"] == 1
    assert report["aggregate"]["keep_fraction"] == 0.5
    assert report["coverage_notes"][0]["missing_literals"] == []


def test_invalid_output_and_exceptions_fail_without_poisoning_chain(diagnostic):
    replies = iter([None, "two\nlines", RuntimeError("unavailable"), "OAuth"])
    async def fake(provider, prompt):
        assert "<current_title>" not in prompt
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    report = asyncio.run(diagnostic.run_cases(fixture_data(), ["luna"], "{text}", call=fake))
    assert report["aggregate"]["failure_count"] == 3
    assert [row["error"] for row in report["checks"]][:3] == [
        "empty response", "line break", "RuntimeError: unavailable",
    ]
    assert report["aggregate"]["keep_fraction"] is None


def test_provider_and_case_chains_are_independent(diagnostic):
    data = fixture_data()
    data["cases"].append({"id": "other", "messages": ["Search"], "check_counts": [1], "expected_subjects": []})
    starts = []
    async def fake(provider, prompt):
        if "<current_title>" not in prompt:
            starts.append((provider, prompt))
        return "OAuth"
    report = asyncio.run(diagnostic.run_cases(data, ["haiku", "luna"], "{text}", call=fake))
    assert len(starts) == 4
    assert report["aggregate"]["call_count"] == 10


@pytest.mark.parametrize("counts", [[0], [5], [2, 1], [True], []])
def test_invalid_fixture_fails_before_calls(diagnostic, counts):
    data = fixture_data()
    data["cases"][0]["check_counts"] = counts
    with pytest.raises(ValueError, match="check_counts"):
        diagnostic.validate_fixture(data)


def test_live_cli_writes_json_and_fails_on_rejected_response(diagnostic, tmp_path, monkeypatch, caplog):
    logger = logging.getLogger("twicc.core.services.title_automation")
    monkeypatch.setattr(logger, "disabled", False)
    async def fake(provider, prompt):
        return ""
    monkeypatch.setattr(diagnostic, "call_model", fake)
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_bytes(orjson.dumps(fixture_data()))
    assert diagnostic.main(["--provider", "luna", "--input", str(source), "--output", str(output),
                            "--live", "--yes"]) == 1
    report = orjson.loads(output.read_bytes())
    assert report["checks"][0]["case_id"] == "café"
    assert report["aggregate"]["failure_count"] == 4
    rows = [orjson.loads(line) for line in Path(str(output) + ".checks.jsonl").read_bytes().splitlines()]
    assert rows == report["checks"]
    logger.warning("diagnostic preserves configured logging")
    assert "diagnostic preserves configured logging" in caplog.text


def test_standalone_live_cli_initializes_default_prompt(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_bytes(orjson.dumps(fixture_data()))
    code = """
import runpy, sys
namespace = runpy.run_path('scripts/diagnose_automatic_titles.py')
async def fake(provider, prompt):
    return 'OAuth'
namespace['main'].__globals__['call_model'] = fake
raise SystemExit(namespace['main'](sys.argv[1:]))
"""
    result = subprocess.run([sys.executable, "-c", code, "--input", str(source), "--output", str(output),
                             "--provider", "haiku", "--live", "--yes"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert orjson.loads(output.read_bytes())["aggregate"]["call_count"] == 4
