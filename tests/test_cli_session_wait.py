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

from datetime import UTC, datetime, timedelta

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

    payload, code = run(capsysbinary, on_blocked=True)

    assert payload["reply"]["outcome"] == "awaiting_user_input"
    assert code == 0


def test_a_timeout_exits_five(session, capsysbinary):
    """And the budget it ran on is the one that was asked for.

    Asserting the outcome alone lets any budget through: `timeout` and exit 5
    are true whether the wait lasted the 0.3 s requested or five seconds. A
    mutant halving it, or pinning it to a constant, kept the suite green —
    the same gap the batch wait closed one commit earlier and this file
    reopened.
    """
    import time as _time

    running(session)

    started = _time.monotonic()
    payload, code = run(capsysbinary, timeout=1.0)
    elapsed = _time.monotonic() - started

    assert payload["reply"]["outcome"] == "timeout"
    assert code == 5
    # Both bounds, and both tight. An upper one alone misses a budget that
    # was shrunk; 2.5x of headroom missed one that was doubled.
    assert 0.8 <= elapsed < 1.6


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
    ({"timeout": 0}, "--wait-timeout must be > 0"),
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

    def probe(session_id, *, from_line, since, timeout, want_text, on_reply, on_blocked):
        # ``session_id`` too: ``ctx.obj`` is the only wiring that carries the
        # positional id down from the group callback, and the direct-call
        # tests bypass the wrapper entirely. Recorded but unasserted, a
        # hardcoded id would wait on the wrong session with the suite green.
        seen.update(session_id=session_id, from_line=from_line, since=since, timeout=timeout,
                    want_text=want_text, reply=on_reply, blocked=on_blocked)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.session.wait", probe)

    result = CliRunner().invoke(app, [
        "session", "sw-session", "wait",
        "--from", "42", "--wait-timeout", "7", "--blocked", "--no-reply-text",
    ])

    assert result.exit_code == 0, result.output
    assert seen == {"session_id": "sw-session", "from_line": 42, "since": None, "timeout": 7.0,
                    "want_text": False, "reply": False, "blocked": True}


def test_the_defaults_are_the_documented_ones(session, monkeypatch):
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_id, *, from_line, since, timeout, want_text, on_reply, on_blocked):
        # ``session_id`` too: ``ctx.obj`` is the only wiring that carries the
        # positional id down from the group callback, and the direct-call
        # tests bypass the wrapper entirely. Recorded but unasserted, a
        # hardcoded id would wait on the wrong session with the suite green.
        seen.update(session_id=session_id, from_line=from_line, since=since, timeout=timeout,
                    want_text=want_text, reply=on_reply, blocked=on_blocked)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.session.wait", probe)

    result = CliRunner().invoke(app, ["session", "sw-session", "wait"])

    assert result.exit_code == 0, result.output
    assert seen == {"session_id": "sw-session", "from_line": None, "since": None, "timeout": 300.0,
                    "want_text": True, "reply": False, "blocked": False}


def test_the_payload_names_the_session_it_waited_on(session, capsysbinary):
    """A caller batching several of these calls has only the payload to tell
    the answers apart — the request order is not something a result carries.
    """
    running(session)

    payload, _ = run(capsysbinary, timeout=0.3)

    assert payload["session_id"] == "sw-session"


def test_the_wait_is_published_as_a_read():
    """It polls and never writes, like its two siblings.

    Two things hang on the classification: `readOnlyHint`, which a client uses
    to decide whether to ask a human, and `batch_read`, which accepts reads
    only — and `batch_read` is how a caller waits on several sessions at once,
    since the plural command is deliberately not built.
    """
    from twicc.mcp.tools import MCP_READ_ONLY_PATHS, iter_mcp_tools

    assert "session/wait" in MCP_READ_ONLY_PATHS

    hints = {t.name: t.annotations.read_only_hint for t in iter_mcp_tools()}
    assert hints["session_wait"] is True
    assert hints["session_stop"] is False


def test_the_mcp_description_carries_the_contract():
    """The subcommand's docstring is the tool description an agent reads.

    A one-line `help=` on the decorator silently replaces it, which is how
    the exit codes went missing the first time. Nothing caught that, so the
    fix could have been undone by the next person shortening the help.
    """
    from twicc.mcp.tools import iter_mcp_tools

    description = next(
        t.description for t in iter_mcp_tools() if t.name == "session_wait"
    )

    # The exit codes are the answer over MCP, where a non-zero code is
    # business data rather than a failure.
    assert "Exit 0" in description

    # The resume rule, asserted as a *pairing* rather than as three words.
    # Checking that `provider_error` and `since_line_num` merely appear let a
    # mutant through that inverted the rule while keeping both — which is the
    # exact defect this test was added to guard.
    assert "`replied`, and `provider_error`" in description
    assert "resumes from its `line_num`" in description
    assert "resumes from its `since_line_num`" in description

    # The two spellings of the cursor, and that they are one cursor. A
    # description naming only `--from` sends an agent holding a timestamp to
    # invent a line number.
    assert "--since is\nthe same cursor as an instant" in description
    assert "mutually exclusive" in description

    # And the vocabulary a caller has to branch on. `pending` is batch-only
    # and must stay out: it cannot happen here.
    for outcome in ("replied", "ended", "timeout", "provider_error",
                    "backend_gone", "wait_failed", "awaiting_user_input"):
        assert f"`{outcome}`" in description, outcome
    assert "pending" not in description


def test_a_provider_error_consumes_a_line_like_an_answer_does(session, capsysbinary):
    """Why the resume rule singles it out.

    `provider_error` points at the line the provider wrote, exactly as
    `replied` points at the answer. Resuming from `since_line_num` — the rule
    for every *other* ending — hands back the same error forever, and an agent
    following the documented rule would never get past a quota limit.

    Prose cannot be tested; this is the behaviour the prose describes.
    """
    from twicc.core.enums import ItemKind

    running(session)
    session.items.create(
        line_num=15, kind=ItemKind.API_ERROR,
        content=orjson.dumps({
            "type": "system", "subtype": "api_error", "isApiErrorMessage": True,
            "result": "You've hit your usage limit.",
        }).decode(),
    )

    first, _ = run(capsysbinary, from_line=0)
    assert first["reply"]["outcome"] == "provider_error"
    assert first["reply"]["line_num"] == 15

    # The rule for the other endings, applied here: the same error comes back.
    again, _ = run(capsysbinary, from_line=first["reply"]["since_line_num"])
    assert again["reply"]["line_num"] == 15

    # The rule this ending actually needs.
    past, _ = run(capsysbinary, from_line=first["reply"]["line_num"], timeout=0.3)
    assert past["reply"]["outcome"] != "provider_error"


# ---------------------------------------------------------------------------
# What ends the wait
# ---------------------------------------------------------------------------


def test_an_answer_ends_the_wait_by_default(session, capsysbinary):
    """An answer always ends the wait, with or without `--reply`."""
    running(session)
    answer(session, 20, "here")

    payload, code = run(capsysbinary, from_line=0)

    assert payload["reply"]["outcome"] == "replied"
    assert code == 0


def test_blocked_is_an_addition_not_a_replacement(session, capsysbinary):
    """`--blocked` adds a second way to finish; an answer still ends the wait.

    The same shape `--wait-blocked` has on the commands that send, so a caller
    who knows one surface knows the other. An earlier version made the two
    flags exclusive selectors, which read well on its own and made the same
    word mean two different things across the CLI.
    """
    running(session)
    answer(session, 20, "here")

    payload, code = run(capsysbinary, from_line=0, on_blocked=True)

    assert payload["reply"]["outcome"] == "replied"
    assert code == 0


def test_an_answer_wins_a_tie_against_a_block(session, capsysbinary):
    """OR-combined, and the answer wins a tie: the transcript is scanned
    before the agent's state is read."""
    now = timezone.now()
    ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now, awaiting_user_input=True,
    )
    answer(session, 20, "answered then asked")

    payload, code = run(capsysbinary, from_line=0, on_blocked=True)

    assert payload["reply"]["outcome"] == "replied"
    assert code == 0


def test_a_block_is_ignored_unless_asked_for(session, capsysbinary):
    """`--blocked` is off by default, and that is the half nothing pinned.

    A session blocked on a tool approval must be waited through: a human can
    still click, and the answer would arrive. Wiring the flag on by default
    survived the whole suite — the shared loop's own test covers it from
    `send-message`, not from here.
    """
    now = timezone.now()
    ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now, awaiting_user_input=True,
    )

    payload, code = run(capsysbinary, timeout=0.3)

    assert payload["reply"]["outcome"] == "timeout"
    assert code == 5


def test_the_reply_flag_is_accepted(session, monkeypatch):
    """It changes nothing, so being accepted is its whole contract.

    A typo in the option string makes every documented `--reply` invocation
    exit 2, and no test ever passed it on a command line.
    """
    from typer.testing import CliRunner

    from twicc.cli import app

    monkeypatch.setattr("twicc.cli.session.wait", lambda *a, **k: None)

    result = CliRunner().invoke(app, ["session", "sw-session", "wait", "--reply"])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("kwargs, message", [
    ({"timeout": 0}, "--wait-timeout must be > 0"),
    ({"timeout": 1.0, "from_line": -1}, "--from must be >= 0"),
    ({"timeout": 1.0, "since": "bogus"}, "--since"),
    ({"timeout": 1.0, "from_line": 2, "since": "2026-09-19"}, "--from and --since"),
])
def test_a_bad_flag_is_named_before_the_session_is_looked_up(db, capsysbinary, kwargs, message):
    """Every check runs before the lookup, and the session here does not exist.

    Reordering them behind it makes a typo report "session not found", which
    sends the caller looking for the wrong problem. Nothing pinned the order,
    so a refactor moved it without noticing.
    """
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("no-such-session", **kwargs)

    assert exc.value.exit_code == 1
    assert message in capsysbinary.readouterr().err.decode()


# ---------------------------------------------------------------------------
# `--since` — the same cursor, named as an instant
# ---------------------------------------------------------------------------


def stamped(session, line_num, when, text="tick"):
    """One item carrying a timestamp, which is all `--since` reads."""
    item = answer(session, line_num, text)
    session.items.filter(pk=item.pk).update(timestamp=when)
    return item


def at(session, since):
    return cli_session._cursor_at(session, cli_session._parse_instant(since))


@pytest.fixture
def timeline(session):
    """Lines 1..5, one minute apart, starting at 12:00 UTC."""
    base = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    for offset in range(5):
        stamped(session, offset + 1, base + timedelta(minutes=offset))
    return base


@pytest.mark.parametrize("since,expected", [
    # At the instant of line 3 — "at or before" includes it, so the wait
    # starts above it and line 4 is the next thing said.
    ("2026-09-19T12:02:00+00:00", 3),
    # A microsecond earlier: line 3 is now strictly after, and is returned.
    ("2026-09-19T12:01:59.999999+00:00", 2),
    # No offset is UTC, not the server's zone — same answer as the first.
    ("2026-09-19 12:02:00", 3),
    # A bare date is its midnight, which is before every line here.
    ("2026-09-19", 0),
    # Older than the session: the wait starts above the first line, rather
    # than refusing.
    ("2020-01-01T00:00:00+00:00", 0),
    # Past the last line: nothing before it is new.
    ("2026-09-19T23:00:00+00:00", 5),
])
def test_an_instant_becomes_the_line_below_the_first_one_after_it(session, timeline, since, expected):
    assert at(session, since) == expected


def test_the_cursor_it_computes_is_the_cursor_the_wait_uses(session, timeline, capsysbinary):
    """The translation is not a separate feature: `--since` and the matching
    `--from` must select the same line, or the two spellings drift apart."""
    running(session)

    payload, code = run(capsysbinary, since="2026-09-19T12:01:00+00:00")

    assert payload["reply"]["since_line_num"] == 2
    assert payload["reply"]["line_num"] == 3  # the first line strictly after
    assert code == 0


def test_a_transcript_without_timestamps_is_new_from_the_top(session):
    """No timestamp is not "before": it is unknown, and unknown re-scans."""
    answer(session, 1)
    answer(session, 2)

    assert at(session, "2026-09-19T12:00:00+00:00") == 0


def test_one_line_without_a_timestamp_holds_the_cursor_above_it(session):
    """The mixed case, which the all-null one does not cover.

    Reading the cursor as "the last line at or before the instant" would put
    it at line 4 and hide line 3 for good. Nothing says line 3 is old.
    """
    base = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    stamped(session, 1, base)
    stamped(session, 2, base + timedelta(minutes=1))
    answer(session, 3)  # no timestamp
    stamped(session, 4, base + timedelta(minutes=3))

    assert at(session, "2026-09-19T12:03:00+00:00") == 2


def test_a_timestamp_that_goes_backwards_never_hides_a_line(session):
    """Timestamps are not monotonic — 121 249 adjacent inversions in this
    machine's own database.

    Line 2 was written after the instant and line 3 before it. Taking the
    last line at or before the instant lands on 3, and line 2 is never
    returned to anyone. The cursor can only ever sit too low.
    """
    base = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    stamped(session, 1, base)
    stamped(session, 2, base + timedelta(minutes=9))
    stamped(session, 3, base + timedelta(minutes=2))

    assert at(session, "2026-09-19T12:05:00+00:00") == 1


def test_an_empty_transcript_starts_above_the_first_line(session):
    assert at(session, "2026-09-19T12:00:00+00:00") == 0


def test_the_two_cursors_are_mutually_exclusive(session, capsysbinary):
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("sw-session", from_line=2, since="2026-09-19", timeout=2.0)

    assert exc.value.exit_code == 1
    assert b"--from and --since" in capsysbinary.readouterr().err


@pytest.mark.parametrize("since", [
    "bogus", "yesterday", "", "   ", "2026-09",
    # Not a date at all, and a date that cannot exist: both come back as
    # "no match".
    "2026-13-01", "2026-02-30",
    # Well formed enough to be parsed, then impossible. These *raise* out of
    # the parser instead of coming back empty, and to a caller they read
    # exactly like the typos above.
    "2026-09-19T25:00:00Z", "2026-09-19T12:60:00Z",
])
def test_an_unparseable_instant_is_refused_by_name(session, capsysbinary, since):
    with pytest.raises(typer.Exit) as exc:
        cli_session.wait("sw-session", since=since, timeout=2.0)

    assert exc.value.exit_code == 1
    assert b"--since" in capsysbinary.readouterr().err


def test_the_instant_travels_from_the_command_line(session, monkeypatch):
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_id, *, from_line, since, timeout, want_text, on_reply, on_blocked):
        seen.update(session_id=session_id, from_line=from_line, since=since)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.session.wait", probe)

    result = CliRunner().invoke(app, ["session", "sw-session", "wait", "--since", "2026-09-19"])

    assert result.exit_code == 0, result.output
    assert seen == {"session_id": "sw-session", "from_line": None, "since": "2026-09-19"}
