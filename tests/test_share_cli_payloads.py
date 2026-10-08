"""Payloads the `twicc share` CLI produces (agent-sharing design §7.2): the
tri-state --live/--frozen flag must let `options.mode` be ABSENT, or the
server-side frozen default for agents can never fire."""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def captured_drop(monkeypatch):
    calls = []

    def fake_run_drop(payload, *, kind, success_status, timeout):
        calls.append({"payload": payload, "kind": kind})

    monkeypatch.setattr("twicc.cli.share_mutation._run_drop", fake_run_drop)
    return calls


def _invoke(args):
    from twicc.cli import app
    return runner.invoke(app, args)


def test_no_flag_omits_mode(captured_drop):
    result = _invoke(["share", "create", "session", "sess-1"])
    assert result.exit_code == 0
    options = captured_drop[0]["payload"]["options"]
    assert "mode" not in options


def test_explicit_live_and_frozen(captured_drop):
    _invoke(["share", "create", "session", "sess-1", "--live"])
    _invoke(["share", "create", "session", "sess-1", "--frozen"])
    assert captured_drop[0]["payload"]["options"]["mode"] == "live"
    assert captured_drop[1]["payload"]["options"]["mode"] == "snapshot"


@pytest.fixture(autouse=True)
def _default_human_caller(monkeypatch):
    """Keep Task 8's CLI-only tests out of the real ProcessRun query."""
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )


class _FakeSession:
    id = "caller-1"


def test_mutations_carry_caller_session_id(captured_drop, monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _FakeSession(),
    )
    results = [
        _invoke(["share", "create", "session", "sess-1"]),
        _invoke(["share", "create", "artifact", "3", "--label", "x"]),
        _invoke(["share", "update", "shr_1", "--label", "y"]),
        _invoke(["share", "revoke", "shr_1"]),
    ]
    assert all(result.exit_code == 0 for result in results)
    assert len(captured_drop) == 4
    for call in captured_drop:
        assert call["payload"]["caller_session_id"] == "caller-1"


def test_human_payload_has_no_caller_key(captured_drop, monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )
    _invoke(["share", "create", "session", "sess-1"])
    assert "caller_session_id" not in captured_drop[0]["payload"]


def test_bare_share_create_is_a_usage_error_not_silent_success():
    """§12: the group had invoke_without_command=True with no callback — a
    silent no-op exit 0, and a phantom zero-arg MCP tool. Now: exit 2."""
    from twicc.cli import app
    result = runner.invoke(app, ["share", "create"])
    assert result.exit_code == 2
    assert "Missing command" in result.output


@pytest.mark.parametrize('flag,expected', [(None, True), ('--include-inline-artifacts', True),
                                           ('--no-inline-artifacts', False)])
def test_inline_artifact_cli_option(captured_drop, flag, expected):
    args = ['share', 'create', 'session', 'sess-1']
    if flag:
        args.append(flag)
    result = _invoke(args)
    assert result.exit_code == 0, result.output
    assert captured_drop[0]['payload']['options']['include_inline_artifacts'] is expected
