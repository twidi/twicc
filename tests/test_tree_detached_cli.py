"""``update-session(s) detach-tree | attach-tree``: submit the right kind and payload.

The transport is faked (no backend); the commands and the session rows are real.
"""

import pytest
from typer.testing import CliRunner

from twicc.cli._drop_request import transport, whoami
from twicc.cli._drop_request.polling import PollOutcome
from twicc.cli.update_session.command import update_session_app
from twicc.cli.update_sessions.command import update_sessions_app
from twicc.core.models import Project, Session

KIND = "session:update_tree_detached"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture
def sessions(db, tmp_path):
    project = Project.objects.create(id="tree-project", directory=str(tmp_path))
    for sid in ("alpha", "beta"):
        Session.objects.create(
            id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
            created_at="2026-10-28T10:00:00Z", user_message_count=1,
        )
    return project


@pytest.fixture
def submitted(monkeypatch, data_dir, sessions):
    """The submitted ``(kind, payload)`` pairs; every request answers ``updated``."""
    calls: list = []

    class _Submission:
        def __init__(self, payload):
            self.request_uuid = f"req-{len(calls)}"
            self.session_id = payload["session_id"]

        def outcome(self):
            return PollOutcome("updated", {"session_id": self.session_id}, True)

        def poll(self):
            return self.outcome()

        def was_received(self):
            return True

        def cleanup(self):
            pass

    def submit(payload, *, kind):
        calls.append((kind, payload))
        return _Submission(payload)

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: sub.outcome())
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return calls


@pytest.mark.parametrize(("command", "expected"), [("detach-tree", True), ("attach-tree", False)])
def test_update_session(submitted, command, expected):
    result = CliRunner().invoke(update_session_app, ["alpha", command])
    assert result.exit_code == 0, result.output
    assert submitted == [(KIND, {"session_id": "alpha", "tree_detached": expected})]


@pytest.mark.parametrize(("command", "expected"), [("detach-tree", True), ("attach-tree", False)])
def test_update_sessions(submitted, command, expected):
    result = CliRunner().invoke(update_sessions_app, [command, "alpha", "beta"])
    assert result.exit_code == 0, result.output
    assert sorted(submitted, key=lambda c: c[1]["session_id"]) == [
        (KIND, {"session_id": "alpha", "tree_detached": expected}),
        (KIND, {"session_id": "beta", "tree_detached": expected}),
    ]
