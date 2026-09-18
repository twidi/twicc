"""``twicc session <ID> wait`` — waiting on a session nobody just prodded.

Every other wait rides on a command that triggered the turn, so its cursor
falls out of the send. Here nothing was sent: the caller names the line to
start above, which is the ``line_num`` or ``since_line_num`` a previous wait
handed back. That is the whole point — it is what makes a timed-out wait
resumable, and a batch of five that timed out needs five cursors, not one.

The race that killed ``process wait --transition`` does not apply. That
marker was read too late and then never moved again; a cursor is monotonic,
and a session that has simply gone idle is observable — it reports ``ended``
rather than hanging to the deadline.
"""

from __future__ import annotations

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.core.models import ProcessRun, Project, Session, SessionType


TWICC_PID = 4242


@pytest.fixture(autouse=True)
def fast_loop(monkeypatch):
    from twicc.cli import _wait_reply

    monkeypatch.setattr(_wait_reply, "POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 0.02)
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 0.02)


@pytest.fixture(autouse=True)
def live_twicc(monkeypatch):
    from twicc.cli import _twicc_info

    monkeypatch.setattr(
        _twicc_info, "resolve_live_twicc", lambda: type("I", (), {"pid": TWICC_PID})(),
    )


@pytest.fixture
def session(db):
    project = Project.objects.create(id="sw-project", directory="/tmp/sw")
    return Session.objects.create(
        id="sw-session", project=project, provider="claude_code",
        file_path="sw-session.jsonl", type=SessionType.SESSION,
        created_at=timezone.now(), mtime=1000, last_line=10, user_message_count=1,
    )


def running(session):
    now = timezone.now()
    return ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now, awaiting_user_input=False,
    )


def answer(session, line_num, text="done"):
    from twicc.core.enums import ItemKind

    return session.items.create(
        line_num=line_num, kind=ItemKind.ASSISTANT_MESSAGE,
        content=orjson.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]},
        }).decode(),
    )


def run(capsysbinary, **kwargs):
    kwargs.setdefault("timeout", 2.0)
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("sw-session", **kwargs)
    payload = orjson.loads(capsysbinary.readouterr().out)
    return payload, exc.value.exit_code


# ---------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------


def test_an_explicit_cursor_finds_an_answer_already_there(session, capsysbinary):
    """What resuming a timed-out wait means: the answer may have landed while
    nobody was looking, and it must not be lost for being early."""
    running(session)
    answer(session, 5, "already said")

    payload, code = run(capsysbinary, from_line=0)

    assert payload["reply"]["outcome"] == "replied"
    assert payload["reply"]["line_num"] == 5
    assert payload["reply"]["text"] == "already said"
    assert code == 0


def test_the_cursor_defaults_to_the_session_s_last_line(session, capsysbinary):
    """"Tell me the next thing it says": the answer below the cursor belongs
    to a turn the caller is not waiting for."""
    running(session)
    answer(session, 5, "old news")  # below last_line=10

    payload, code = run(capsysbinary)

    assert payload["reply"]["since_line_num"] == 10
    assert payload["reply"]["outcome"] != "replied"


def test_a_line_at_the_cursor_does_not_count(session, capsysbinary):
    """Strictly past, not at: the cursor is the last line already seen."""
    running(session)
    answer(session, 7, "seen before")

    payload, _ = run(capsysbinary, from_line=7)

    assert payload["reply"]["outcome"] != "replied"


def test_an_idle_session_ends_instead_of_hanging(session, capsysbinary):
    """The defect that opened this whole work, in its new setting.

    ``--transition`` read a marker too late and then waited for a change that
    could never come. Here the session is simply idle, which is observable:
    the wait concludes rather than burning its budget.
    """
    payload, code = run(capsysbinary, timeout=30.0)

    assert payload["reply"]["outcome"] == "ended"
    assert code == 5


# ---------------------------------------------------------------------------
# Exit codes — this command exists to be chained on
# ---------------------------------------------------------------------------


def test_a_block_can_end_the_wait_and_exits_zero(session, capsysbinary):
    """Asked for, so it is an ending the caller wanted, not a failure."""
    now = timezone.now()
    ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now, awaiting_user_input=True,
    )

    payload, code = run(capsysbinary, stop_when_blocked=True)

    assert payload["reply"]["outcome"] == "awaiting_user_input"
    assert code == 0


def test_a_timeout_exits_five(session, capsysbinary):
    running(session)

    payload, code = run(capsysbinary, timeout=0.3)

    assert payload["reply"]["outcome"] == "timeout"
    assert code == 5


def test_a_vanished_backend_exits_two(session, capsysbinary, monkeypatch):
    """The family's code for "the server is not there", not a wait outcome."""
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)

    payload, code = run(capsysbinary)

    assert payload["reply"]["outcome"] == "backend_gone"
    assert code == 2


def test_a_broken_wait_exits_one(session, capsysbinary, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_reply", boom)

    payload, code = run(capsysbinary)

    assert payload["reply"]["outcome"] == "wait_failed"
    assert code == 1


def test_the_text_can_be_dropped(session, capsysbinary):
    running(session)
    answer(session, 20, "a very long answer")

    payload, _ = run(capsysbinary, want_text=False)

    assert "text" not in payload["reply"]
    assert payload["reply"]["line_num"] == 20


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs, message", [
    ({"timeout": 0}, "--timeout must be > 0"),
    ({"timeout": 1.0, "from_line": -1}, "--from must be >= 0"),
])
def test_bad_arguments_are_refused(session, capsysbinary, kwargs, message):
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("sw-session", **kwargs)

    assert exc.value.exit_code == 1
    assert message in capsysbinary.readouterr().err.decode()


def test_an_unknown_session_is_refused(db, capsysbinary):
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("nope", timeout=1.0)

    assert exc.value.exit_code == 1


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_flags_travel_from_the_command_line(session, monkeypatch):
    """Calling ``wait()`` directly leaves the Typer wiring untested, and an
    option that never arrives is a silent no-op."""
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_id, *, from_line, timeout, want_text, stop_when_blocked):
        seen.update(from_line=from_line, timeout=timeout, want_text=want_text,
                    blocked=stop_when_blocked)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.session.wait", probe)

    result = CliRunner().invoke(app, [
        "session", "sw-session", "wait",
        "--from", "42", "--timeout", "7", "--wait-blocked", "--no-reply-text",
    ])

    assert result.exit_code == 0, result.output
    assert seen == {"from_line": 42, "timeout": 7.0, "want_text": False, "blocked": True}


def test_the_defaults_are_the_documented_ones(session, monkeypatch):
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_id, *, from_line, timeout, want_text, stop_when_blocked):
        seen.update(from_line=from_line, timeout=timeout, want_text=want_text,
                    blocked=stop_when_blocked)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.session.wait", probe)

    result = CliRunner().invoke(app, ["session", "sw-session", "wait"])

    assert result.exit_code == 0, result.output
    assert seen == {"from_line": None, "timeout": 300.0, "want_text": True, "blocked": False}
