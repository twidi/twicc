"""``twicc sessions wait-reply`` — who is waited on, and from where.

The waiting itself is ``_wait_reply``'s, already covered from the singular and
from ``send-messages``. What is new here is everything around it:

- **the cursors.** A send hands each recipient's back; nothing is sent here, so
  each session starts above its own ``last_line``, or above the instant
  ``--since`` names, translated per session. One cursor shared across a batch
  would let a chatty session close a quiet one's wait.
- **the selection.** The listing's filters, with explicit ids unioned on top —
  and one refusal the listing does not make, since a bare call would poll every
  session TwiCC has indexed.
- **the reporting.** A named id that does not exist has to come back, or the
  caller cannot align the result with what they asked for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.cli import sessions_wait_reply
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(id="swr-project", directory="/tmp/swr")


@pytest.fixture
def other_project(db):
    return Project.objects.create(id="swr-other", directory="/tmp/swr-other")


def make_session(project, sid, *, last_line=10, **kwargs):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        type=SessionType.SESSION, created_at=timezone.now(), mtime=1000,
        last_line=last_line, user_message_count=1, **kwargs,
    )


def stamped(session, line_num, when):
    item = session.items.create(
        line_num=line_num, kind=ItemKind.ASSISTANT_MESSAGE,
        content=orjson.dumps({"type": "assistant", "message": {
            "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hi"}]}}).decode(),
    )
    session.items.filter(pk=item.pk).update(timestamp=when)


@pytest.fixture
def loop(monkeypatch):
    """Record what reaches the shared loop, and answer without polling."""
    seen: dict = {}

    def fake(cursors, *, timeout, want_text, first):
        seen.update(cursors=dict(cursors), timeout=timeout,
                    want_text=want_text, first=first)
        return {sid: {"outcome": "replied", "line_num": 9, "since_line_num": c}
                for sid, c in cursors.items()}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake)
    return seen


def run(capsysbinary, *ids, **kwargs):
    kwargs.setdefault("timeout", 5.0)
    sessions_wait_reply.main(list(ids), **kwargs)
    return orjson.loads(capsysbinary.readouterr().out)


# ---------------------------------------------------------------------------
# The refusal the listing does not make
# ---------------------------------------------------------------------------


def test_a_bare_call_is_refused(project, capsysbinary):
    """The listing with no filter shows a page; a wait with no filter would
    poll every session TwiCC has indexed and mean nothing by it."""
    make_session(project, "a")

    with pytest.raises(typer.Exit) as exc:
        sessions_wait_reply.main([], timeout=5.0)

    assert exc.value.exit_code == 1
    assert b"at least one session id or one filter" in capsysbinary.readouterr().err


def test_one_id_is_enough(project, capsysbinary, loop):
    make_session(project, "a")

    payload = run(capsysbinary, "a")

    assert set(payload["results"]) == {"a"}


def test_one_filter_is_enough(project, capsysbinary, loop):
    make_session(project, "a")

    payload = run(capsysbinary, project=project.id)

    assert set(payload["results"]) == {"a"}


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_explicit_ids_are_added_to_the_filter_not_replaced_by_it(
    project, other_project, capsysbinary, loop,
):
    """The rule the whole plural family follows. Replacing quietly turns a
    scoped call with one named id into a one-session wait."""
    make_session(project, "in-filter")
    make_session(other_project, "named")

    payload = run(capsysbinary, "named", project=project.id)

    assert set(payload["results"]) == {"named", "in-filter"}


def test_a_named_id_is_not_waited_on_twice(project, capsysbinary, loop):
    make_session(project, "a")

    run(capsysbinary, "a", "a", project=project.id)

    assert list(loop["cursors"]) == ["a"]


def test_a_named_id_that_does_not_exist_comes_back(project, capsysbinary, loop):
    """Dropped, the caller cannot line the result up with what they asked."""
    make_session(project, "a")

    payload = run(capsysbinary, "a", "ghost")

    assert payload["results"]["ghost"]["outcome"] == "unknown_session"
    assert "ghost" not in loop["cursors"]


def test_an_empty_selection_is_not_an_error(project, capsysbinary, loop):
    payload = run(capsysbinary, project="no-such-project")

    assert payload == {"summary": {
        "total": 0, "replied": 0, "awaiting_user_input": 0,
        "concluded": 0, "all_replied": False,
    }, "results": {}}


# ---------------------------------------------------------------------------
# The cursors
# ---------------------------------------------------------------------------


def test_each_session_starts_above_its_own_last_line(project, capsysbinary, loop):
    """One cursor for the batch would let a chatty session close a quiet
    one's wait: line 40 is a different place in every transcript."""
    make_session(project, "a", last_line=40)
    make_session(project, "b", last_line=7)

    run(capsysbinary, "a", "b")

    assert loop["cursors"] == {"a": 40, "b": 7}


def test_an_instant_is_translated_per_session(project, capsysbinary, loop):
    """`--since` is what addresses a batch at all — the same moment lands on a
    different line in each transcript."""
    base = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    a = make_session(project, "a", last_line=40)
    b = make_session(project, "b", last_line=7)
    for offset in range(1, 4):
        stamped(a, offset, base + timedelta(minutes=offset))
    stamped(b, 1, base + timedelta(minutes=1))
    stamped(b, 2, base + timedelta(minutes=9))

    run(capsysbinary, "a", "b", since="2026-09-20T12:05:00+00:00")

    # `a`: every line is before the instant, so nothing is new — the cursor
    # sits at its last stamped line. `b`: line 2 is after it.
    assert loop["cursors"] == {"a": 3, "b": 1}


def test_the_instant_wins_over_the_last_line(project, capsysbinary, loop):
    a = make_session(project, "a", last_line=40)
    stamped(a, 1, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))

    run(capsysbinary, "a", since="2026-01-01")

    assert loop["cursors"] == {"a": 0}


# ---------------------------------------------------------------------------
# What reaches the loop, and what comes back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs, key, expected", [
    ({"timeout": 12.0}, "timeout", 12.0),
    ({"timeout": 5.0, "first": True}, "first", True),
    ({"timeout": 5.0}, "first", False),
    ({"timeout": 5.0, "want_text": False}, "want_text", False),
    ({"timeout": 5.0}, "want_text", True),
])
def test_the_flags_reach_the_loop(project, capsysbinary, loop, kwargs, key, expected):
    """A flag that stops at the command is a flag that does nothing, and the
    loop's own tests cannot see it."""
    make_session(project, "a")

    sessions_wait_reply.main(["a"], **kwargs)
    capsysbinary.readouterr()

    assert loop[key] == expected


def test_the_summary_counts_answers_and_conclusions_apart(project, capsysbinary, monkeypatch):
    """A session that ended on a pending request concluded, but it did not
    answer — so `all_replied` is false while nothing is left to wait for."""
    make_session(project, "a")
    make_session(project, "b")
    monkeypatch.setattr(
        "twicc.cli._wait_reply.wait_for_replies",
        lambda cursors, **kw: {"a": {"outcome": "replied"},
                               "b": {"outcome": "awaiting_user_input"}},
    )

    payload = run(capsysbinary, "a", "b")

    assert payload["summary"] == {
        "total": 2, "replied": 1, "awaiting_user_input": 1,
        "concluded": 2, "all_replied": False,
    }


# ---------------------------------------------------------------------------
# Refusals, all before anything is read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs, message", [
    ({"timeout": 0}, "--wait-timeout must be > 0"),
    ({"timeout": 5.0, "since": "bogus"}, "--since"),
    ({"timeout": 5.0, "spawned_by": "x", "descendants": "y"}, "mutually exclusive"),
    ({"timeout": 5.0, "project": "p", "workspace": "w"}, "mutually exclusive"),
])
def test_a_bad_flag_is_named_before_the_selection_is_built(db, capsysbinary, kwargs, message):
    """No session exists here: a check that ran after the query would report
    an empty selection, or nothing at all, instead of the flag."""
    with pytest.raises(typer.Exit) as exc:
        sessions_wait_reply.main(["a"], **kwargs)

    assert exc.value.exit_code == 1
    assert message in capsysbinary.readouterr().err.decode()


# ---------------------------------------------------------------------------
# The command line and the MCP surface
# ---------------------------------------------------------------------------


def test_the_wait_is_published_as_a_read():
    """It polls and never writes, like its singular. `batch_read` only accepts
    reads, and a client uses `readOnlyHint` to decide whether to ask a human."""
    from twicc.mcp.tools import MCP_READ_ONLY_PATHS, iter_mcp_tools

    assert "sessions/wait-reply" in MCP_READ_ONLY_PATHS

    hints = {t.name: t.annotations.read_only_hint for t in iter_mcp_tools()}
    assert hints["sessions_wait_reply"] is True


def test_the_flags_travel_from_the_command_line(project, monkeypatch):
    """Calling `main()` directly leaves the Typer wiring untested, and an
    option that never arrives is a silent no-op."""
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_ids, **kwargs):
        seen.update(ids=list(session_ids), **kwargs)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.sessions_wait_reply.main", probe)

    result = CliRunner().invoke(app, [
        "sessions", "wait-reply", "a", "b", "--since", "2026-09-20",
        "--wait-timeout", "7", "--wait-first", "--no-reply-text",
        "--spawned-by", "s", "--annotation", "k=v", "--provider", "codex",
        "--state", "user_turn",
    ])

    assert result.exit_code == 0, result.output
    assert seen["ids"] == ["a", "b"]
    assert seen["since"] == "2026-09-20"
    assert seen["timeout"] == 7.0
    assert seen["first"] is True
    assert seen["want_text"] is False
    assert seen["spawned_by"] == "s"
    assert seen["annotation"] == ["k=v"]
    assert seen["provider"] == "codex"
    assert seen["state"] == ["user_turn"]


def test_the_defaults_are_the_documented_ones(project, monkeypatch):
    from typer.testing import CliRunner

    from twicc.cli import app

    seen: dict = {}

    def probe(session_ids, **kwargs):
        seen.update(ids=list(session_ids), **kwargs)
        raise typer.Exit(0)

    monkeypatch.setattr("twicc.cli.sessions_wait_reply.main", probe)

    result = CliRunner().invoke(app, ["sessions", "wait-reply", "a"])

    assert result.exit_code == 0, result.output
    assert seen["timeout"] == 300.0
    assert seen["first"] is False
    assert seen["want_text"] is True
    assert seen["since"] is None
