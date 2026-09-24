"""self/parent keyword resolution for the four §11 call sites, and the
remote preflight extension."""

import pytest
import typer
from typer.testing import CliRunner

runner = CliRunner()


class _FakeSession:
    def __init__(self, sid, spawned_by_id=None):
        self.id = sid
        self.spawned_by_id = spawned_by_id


def test_literal_id_passes_through(monkeypatch):
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    assert resolve_session_keyword(
        "abc-123", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "abc-123"
    # A known keyword outside this call site's declared set is a literal id.
    assert resolve_session_keyword(
        "parent", param_name="SESSION_ID", allowed=frozenset({"self"}),
    ) == "parent"


def test_self_resolves(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me"))
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    assert resolve_session_keyword(
        "self", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "me"


def test_parent_resolves(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id="mom"))
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    assert resolve_session_keyword(
        "parent", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "mom"


def test_unresolved_context_fails_structured(monkeypatch, capsys):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: None)
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    with pytest.raises(typer.Exit) as exc:
        resolve_session_keyword(
            "self", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
        )
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert '"validation_error"' in out
    assert "session_context_not_found" in out
    assert "SESSION_ID" in out


def test_root_session_parent_fails_structured(monkeypatch, capsys):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id=None))
    from twicc.cli._session_keywords import (
        SELF_PARENT_KEYWORDS,
        resolve_session_keyword,
    )

    with pytest.raises(typer.Exit) as exc:
        resolve_session_keyword(
            "parent", param_name="--session", allowed=SELF_PARENT_KEYWORDS,
        )
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "parent_not_found" in out and "--session" in out


# ── the four call sites actually resolve ────────────────────────────────────

@pytest.fixture
def call_captures(monkeypatch):
    captured = {"bookmark": [], "unbookmark": [], "create": [], "list": []}
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id="mom"))
    monkeypatch.setattr("twicc.cli.artifacts_mutation.run_bookmark",
                        lambda **kw: captured["bookmark"].append(kw))
    monkeypatch.setattr("twicc.cli.artifacts_mutation.run_unbookmark",
                        lambda **kw: captured["unbookmark"].append(kw))
    monkeypatch.setattr("twicc.cli.share_mutation.run_create_session",
                        lambda **kw: captured["create"].append(kw))
    monkeypatch.setattr("twicc.cli.share.list_main",
                        lambda **kw: captured["list"].append(kw))
    return captured


def test_call_sites_resolve_keywords(call_captures):
    """§14 Keywords: BOTH keywords at all four call sites (self → "me",
    parent → "mom"). `share create session parent` resolves here too — the
    scope refusal is server-side (Task 12); the CLI forwards the resolved id."""
    from twicc.cli import app
    runner.invoke(app, ["artifacts", "bookmark", "self", "f.html", "--name", "test"])
    runner.invoke(app, ["artifacts", "bookmark", "parent", "f.html", "--name", "test"])
    runner.invoke(app, ["artifacts", "unbookmark", "self", "f.html"])
    runner.invoke(app, ["artifacts", "unbookmark", "parent", "f.html"])
    runner.invoke(app, ["share", "create", "session", "self"])
    runner.invoke(app, ["share", "create", "session", "parent"])
    runner.invoke(app, ["share", "--session", "self"])
    runner.invoke(app, ["share", "--session", "parent"])
    assert [kw["session_id"] for kw in call_captures["bookmark"]] == ["me", "mom"]
    assert [kw["session_id"] for kw in call_captures["unbookmark"]] == ["me", "mom"]
    assert [kw["session_id"] for kw in call_captures["create"]] == ["me", "mom"]
    assert [kw["session"] for kw in call_captures["list"]] == ["me", "mom"]


@pytest.mark.parametrize(
    ("keyword", "current", "code"),
    [
        ("self", None, "session_context_not_found"),
        ("parent", None, "session_context_not_found"),
        ("parent", _FakeSession("root"), "parent_not_found"),
    ],
)
def test_failure_contract_fires_at_each_call_site(monkeypatch, keyword, current, code):
    """§14 Keywords: all three failure states fire locally at all four sites."""
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session",
        lambda: current,
    )
    from twicc.cli import app
    for args in (["artifacts", "bookmark", keyword, "f.html", "--name", "test"],
                 ["artifacts", "unbookmark", keyword, "f.html"],
                 ["share", "create", "session", keyword],
                 ["share", "--session", keyword]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, args
        assert code in result.output, args


@pytest.mark.parametrize("keyword", ["self", "parent"])
@pytest.mark.parametrize(
    "argv_template",
    [
        ["artifacts", "bookmark", "KEYWORD", "f.html", "--name", "test"],
        ["artifacts", "unbookmark", "KEYWORD", "f.html"],
        ["share", "create", "session", "KEYWORD"],
        ["share", "--session", "KEYWORD"],
        ["session", "KEYWORD"],
        ["session", "KEYWORD", "messages"],
    ],
)
def test_remote_preflight_rejects_every_keyword_call_site_before_http(
        monkeypatch, keyword, argv_template):
    """§14 remote row: both keywords fail at every real command shape."""
    from twicc.cli import _remote

    def fail_if_client_constructed(*args, **kwargs):
        raise AssertionError("remote keyword preflight reached HTTP")

    monkeypatch.setattr(_remote.httpx, "Client", fail_if_client_constructed)
    argv = [keyword if token == "KEYWORD" else token for token in argv_template]
    assert _remote.maybe_forward([
        "--remote", "https://remote.example", *argv,
    ]) == 2


@pytest.mark.parametrize("argv", [
    ["session", "--full", "abc", "agents"],
    ["session", "abc", "--full", "agents"],
    ["session", "abc", "--slim", "messages"],
])
def test_remote_refuses_a_group_flag_before_a_subcommand(argv):
    from twicc.cli._remote import RemoteUsageError, resolve_command

    with pytest.raises(RemoteUsageError, match="apply to `session <id>` alone"):
        resolve_command(argv)


@pytest.mark.parametrize("argv", [
    ["session", "abc", "--full"],
    ["session", "--full", "abc"],
])
def test_remote_accepts_the_group_flag_on_the_bare_call(argv):
    from twicc.cli._remote import resolve_command

    resolved = resolve_command(argv)
    assert resolved.path == "session"
    assert resolved.params["full"] is True


def test_remote_reports_an_unknown_token_after_the_flag_as_unknown():
    from twicc.cli._remote import RemoteUsageError, resolve_command

    with pytest.raises(RemoteUsageError, match="unknown command") as exc:
        resolve_command(["session", "abc", "--full", "bogus"])
    assert "apply to `session <id>` alone" not in str(exc.value)


def test_remote_preflight_allows_explicit_share_session_id():
    from twicc.cli._remote import reject_host_bound, resolve_command

    reject_host_bound(resolve_command(["share", "--session", "abc"]))
