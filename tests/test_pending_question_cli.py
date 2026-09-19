"""The pending-question CLI surface.

Three things the services cannot test: that the flags reach the command body,
that the client-side validations refuse what the design says they refuse, and
that the answer payload carries the calling session — the one input the
self-answer rule reads, and the half the service tests have to assume.

Design: ``docs/plans/2026-09-18-question-cli-design.md`` §6, §7.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from twicc.cli import app, pending_question


SESSION_ID = "s-cli-question"


@pytest.fixture
def submitted(monkeypatch):
    """Capture the payload and kind, and stop before any transport work."""
    captured: dict = {}

    def probe(payload, *, kind, success_status, timeout):
        captured.update(payload=payload, kind=kind,
                        success_status=success_status, timeout=timeout)
        raise typer.Exit(0)

    monkeypatch.setattr(pending_question, "_run", probe)
    monkeypatch.setattr(
        pending_question, "_prepare",
        lambda session_id, timeout: SimpleNamespace(session_id=session_id),
    )
    # The caller resolution reads the DB. Default to "a human ran this"; the
    # tests that care about the stamp re-patch it themselves.
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )
    return captured


# ---------------------------------------------------------------------------
# Parsing an answer
# ---------------------------------------------------------------------------


class TestParseAnswers:
    def test_it_splits_on_the_first_equals(self):
        # Free text is accepted, so a value really can contain one.
        assert pending_question.parse_answers(["1=a=b"]) == {"1": ["a=b"]}

    def test_a_repeated_id_collects_its_values(self):
        assert pending_question.parse_answers(["1=A", "1=B"]) == {"1": ["A", "B"]}

    def test_an_empty_value_is_valid(self):
        assert pending_question.parse_answers(["1="]) == {"1": [""]}

    @pytest.mark.parametrize("bad", ["no-equals-at-all", "=orphan"])
    def test_a_malformed_answer_is_refused(self, bad):
        with pytest.raises(ValueError):
            pending_question.parse_answers([bad])


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_read_flags_travel_from_the_command_line(submitted):
    result = CliRunner().invoke(app, [
        "session", SESSION_ID, "pending-request", "--raw", "--timeout", "7",
    ])

    assert result.exit_code == 0, result.output
    assert submitted["kind"] == "session:pending_requests"
    assert submitted["success_status"] == "fetched"
    assert submitted["timeout"] == 7
    assert submitted["payload"] == {"session_id": SESSION_ID, "raw": True}


def test_the_read_defaults_omit_raw(submitted):
    result = CliRunner().invoke(app, ["session", SESSION_ID, "pending-request"])

    assert result.exit_code == 0, result.output
    assert submitted["payload"] == {"session_id": SESSION_ID}
    assert submitted["timeout"] == 30


def test_the_answer_flags_travel_from_the_command_line(submitted):
    result = CliRunner().invoke(app, [
        "session", SESSION_ID, "answer", "answer",
        "--request-id", "req-9", "--answer", "1=PostgreSQL", "--answer", "2=Redis",
        "--timeout", "12",
    ])

    assert result.exit_code == 0, result.output
    assert submitted["kind"] == "session:answer_pending_question"
    assert submitted["success_status"] == "updated"
    assert submitted["timeout"] == 12
    payload = submitted["payload"]
    assert payload["session_id"] == SESSION_ID
    assert payload["action"] == "answer"
    assert payload["request_id"] == "req-9"
    assert payload["answers"] == {"1": ["PostgreSQL"], "2": ["Redis"]}


def test_cancel_carries_no_answers_key(submitted):
    result = CliRunner().invoke(app, ["session", SESSION_ID, "answer", "cancel"])

    assert result.exit_code == 0, result.output
    assert "answers" not in submitted["payload"]
    assert "request_id" not in submitted["payload"]


# ---------------------------------------------------------------------------
# Client-side validation — exit 1, never 64
# ---------------------------------------------------------------------------


def test_an_unknown_action_word_exits_1(submitted):
    # A positional argument, so Typer accepts it and the body refuses it.
    result = CliRunner().invoke(app, ["session", SESSION_ID, "answer", "maybe"])
    assert result.exit_code == 1
    assert submitted == {}


def test_a_malformed_answer_exits_1(submitted):
    result = CliRunner().invoke(app, [
        "session", SESSION_ID, "answer", "answer", "--answer", "no-equals",
    ])
    assert result.exit_code == 1
    assert submitted == {}


@pytest.mark.parametrize("command", [
    ["pending-request", "--timeout", "0"],
    ["answer", "cancel", "--timeout", "-1"],
])
def test_a_non_positive_timeout_exits_1(monkeypatch, command):
    monkeypatch.setattr(pending_question, "_setup_django", lambda: None)
    result = CliRunner().invoke(app, ["session", SESSION_ID, *command])
    assert result.exit_code == 1


def test_a_stray_token_after_the_read_is_a_usage_error():
    # Typer rejects it; Click's UsageError exit code is 2, not the body's 1.
    result = CliRunner().invoke(app, ["session", SESSION_ID, "pending-request", "foo"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# The caller stamp
# ---------------------------------------------------------------------------


def test_the_answer_payload_carries_the_calling_session(submitted):
    # Without this the design's one security rule never fires, on either route:
    # over MCP the same resolution reads the id pinned from the signed token.
    with patch("twicc.cli._drop_request.whoami.resolve_current_session",
               return_value=SimpleNamespace(id="caller-session")):
        result = CliRunner().invoke(app, ["session", SESSION_ID, "answer", "cancel"])

    assert result.exit_code == 0, result.output
    assert submitted["payload"]["caller_session_id"] == "caller-session"


def test_a_human_caller_stamps_nothing(submitted):
    with patch("twicc.cli._drop_request.whoami.resolve_current_session",
               return_value=None):
        result = CliRunner().invoke(app, ["session", SESSION_ID, "answer", "cancel"])

    assert result.exit_code == 0, result.output
    assert "caller_session_id" not in submitted["payload"]


def test_the_read_never_stamps_a_caller(submitted):
    # It is a read: there is nothing for the self-answer rule to refuse.
    with patch("twicc.cli._drop_request.whoami.resolve_current_session",
               return_value=SimpleNamespace(id="caller-session")):
        result = CliRunner().invoke(app, ["session", SESSION_ID, "pending-request"])

    assert result.exit_code == 0, result.output
    assert "caller_session_id" not in submitted["payload"]


# ---------------------------------------------------------------------------
# The generated surface
# ---------------------------------------------------------------------------


def test_the_read_is_registered_as_read_only():
    # Without the entry it ships advertised as a mutation and batch_read
    # refuses it. ``session/answer`` stays out, deliberately.
    from twicc.rpc.permissions import COOKIE_READONLY_COMMANDS

    assert "session/pending-request" in COOKIE_READONLY_COMMANDS
    assert "session/answer" not in COOKIE_READONLY_COMMANDS


def test_both_commands_have_an_rpc_route():
    from twicc.rpc.generator import build_registry

    registry = build_registry()
    assert "session/pending-request" in registry
    assert "session/answer" in registry
