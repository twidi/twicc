"""Tests for the dated cutover to the pagination envelope.

Design: docs/plans/2026-09-08-pagination-cutover-design.md. Every test drives a
real command on one side of ``PAGINATION_CUTOVER`` or the other. The pinned
value is always **naive**: the constant is a local wall-clock instant, and
comparing it against an aware datetime raises ``TypeError``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.cli import sessions as cli_sessions
from twicc.cli import share as cli_share
from twicc.cli import workspaces as cli_workspaces
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType

PAST = datetime(2000, 1, 1)     # noqa: DTZ001 — naive, like the constant
FUTURE = datetime(2200, 1, 1)   # noqa: DTZ001 — naive, like the constant


@pytest.fixture
def before(monkeypatch):
    monkeypatch.setattr(_output, "PAGINATION_CUTOVER", FUTURE)


@pytest.fixture
def after(monkeypatch):
    monkeypatch.setattr(_output, "PAGINATION_CUTOVER", PAST)


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-cutover", directory="/tmp/twicc-cutover",
    )


def make_sessions(project, count):
    now = timezone.now()
    return [
        Session.objects.create(
            id=f"co-sess-{i}", project=project, provider="claude_code",
            file_path=f"co-sess-{i}.jsonl", type=SessionType.SESSION,
            created_at=now + timedelta(minutes=i), mtime=1000 + i,
            last_line=1, user_message_count=1,
        )
        for i in range(count)
    ]


def read(capsysbinary):
    out, err = capsysbinary.readouterr()
    return orjson.loads(out), err.decode()


# --- the predicate ----------------------------------------------------------


def test_the_predicate_flips_at_the_constant():
    cutover = _output.PAGINATION_CUTOVER
    assert _output.pagination_is_default(cutover - timedelta(minutes=1)) is False
    assert _output.pagination_is_default(cutover) is True


def test_the_predicate_refuses_an_aware_now():
    """The constant is naive local time; `timezone.now()` would raise TypeError
    deep inside a command body, so it is rejected up front instead."""
    with pytest.raises(ValueError, match="naive datetime"):
        _output.pagination_is_default(datetime.now(UTC))


# --- phase 1: the shape does not move, a notice is emitted ------------------


def test_before_the_cutover_the_shape_is_untouched(before, project, capsysbinary):
    make_sessions(project, 3)
    cli_sessions.main(project=project.id, limit=2)
    payload, _ = read(capsysbinary)
    assert isinstance(payload, list)
    assert len(payload) == 2


def test_before_the_cutover_a_flagless_call_is_notified(before, project, capsysbinary):
    make_sessions(project, 3)
    cli_sessions.main(project=project.id, limit=2)
    _, err = read(capsysbinary)
    assert "2200-01-01" in err
    assert "`sessions`" in err
    assert "--paginated" in err


def test_the_date_comes_from_the_constant(before, project, capsysbinary, monkeypatch):
    monkeypatch.setattr(_output, "PAGINATION_CUTOVER", datetime(2199, 3, 4))  # noqa: DTZ001
    make_sessions(project, 1)
    cli_sessions.main(project=project.id)
    assert "2199-03-04" in read(capsysbinary)[1]


def test_a_migrated_caller_is_left_alone(before, project, capsysbinary):
    make_sessions(project, 3)
    cli_sessions.main(project=project.id, limit=2, paginated=True)
    payload, err = read(capsysbinary)
    assert set(payload) == {"items", "pagination"}
    assert err == ""


# --- phase 2: the envelope and the page size become the default -------------


def test_after_the_cutover_the_envelope_is_the_default(after, project, capsysbinary):
    make_sessions(project, 60)
    cli_sessions.main(project=project.id)
    payload, err = read(capsysbinary)
    assert set(payload) == {"items", "pagination"}
    assert payload["pagination"]["limit"] == 50
    assert len(payload["items"]) == 50
    assert err == "", "nothing left to announce"


def test_after_the_cutover_the_flag_is_a_no_op(after, project, capsysbinary):
    make_sessions(project, 5)
    cli_sessions.main(project=project.id, limit=2)
    without, _ = read(capsysbinary)
    cli_sessions.main(project=project.id, limit=2, paginated=True)
    with_flag, _ = read(capsysbinary)
    assert without == with_flag


def test_an_explicit_limit_still_wins_after_the_cutover(after, project, capsysbinary):
    make_sessions(project, 60)
    cli_sessions.main(project=project.id, limit=3)
    payload, _ = read(capsysbinary)
    assert payload["pagination"]["limit"] == 3


# --- the notice text adapts to the command ----------------------------------


def test_share_omits_the_page_size_clause(before, project, capsysbinary):
    """`share` already pages at 50, so only its shape changes."""
    cli_share.list_main(limit=2)
    _, err = read(capsysbinary)
    assert "`share`" in err
    assert "pages at 50" not in err


def test_search_announces_key_renames_not_a_bare_array(before, capsysbinary, monkeypatch):
    """`search` is the one listing whose current output is already an object."""
    monkeypatch.setattr("twicc.search.raw_search", lambda *a, **k: {
        "query": "x", "total_hits": 0, "limit": 20, "offset": 0, "hits": [],
    })
    from twicc.cli.search import main as search_main

    search_main("x")
    _, err = read(capsysbinary)
    assert "renames `hits` to `items`" in err
    assert "bare array" not in err


def test_a_sub_command_is_named_by_its_full_path(before, project, capsysbinary):
    session = make_sessions(project, 1)[0]
    cli_session.agents(session.id)
    assert "`session agents`" in read(capsysbinary)[1]


# --- session content: the guard retires itself ------------------------------


def test_before_the_cutover_a_bare_content_call_is_refused(before, project):
    session = make_sessions(project, 1)[0]
    with pytest.raises(typer.Exit):
        cli_session.content(session.id)


def test_after_the_cutover_a_bare_content_call_returns_a_page(after, project, capsysbinary):
    """`--paginated` is one of the guard's accepted selectors, so once the flag
    is always on the guard can never fire — and a bounded page is all it ever
    protected against."""
    session = make_sessions(project, 1)[0]
    for i in range(60):
        SessionItem.objects.create(
            session=session, line_num=i + 1, kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps({"type": "user", "n": i}).decode(),
        )
    cli_session.content(session.id)
    payload, _ = read(capsysbinary)
    assert len(payload["items"]) == 50
    assert payload["pagination"]["total"] == 60


# --- carriers ---------------------------------------------------------------


def test_the_terminal_path_carries_the_notice_on_stderr(before, project, capsysbinary):
    make_sessions(project, 1)
    cli_sessions.main(project=project.id)
    out, err = read(capsysbinary)
    assert err.startswith("twicc: from ")
    assert isinstance(out, list), "stdout stays pure JSON"


def test_a_closed_stderr_never_diverts_the_notice_to_stdout(before, project, capsysbinary, monkeypatch):
    """Closing fd 2 sets sys.stderr to None; `print(file=None)` would fall back
    to stdout and corrupt the payload. click.echo drops the write instead."""
    import sys

    make_sessions(project, 1)
    monkeypatch.setattr(sys, "stderr", None)
    cli_sessions.main(project=project.id)
    out, _ = read(capsysbinary)
    assert isinstance(out, list)


def test_the_rpc_path_carries_the_notice_in_the_envelope(before, project):
    from twicc.rpc.invoker import invoke

    make_sessions(project, 1)
    result = invoke(["sessions", "--project", project.id])
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("twicc: from ")


def test_the_rpc_envelope_omits_the_key_when_there_is_nothing_to_say(after, project):
    from twicc.rpc.invoker import invoke

    make_sessions(project, 1)
    assert invoke(["sessions", "--project", project.id]).warnings == ()


def test_notices_do_not_accumulate_across_invocations(before, project):
    from twicc.rpc.invoker import invoke

    make_sessions(project, 1)
    invoke(["sessions", "--project", project.id])
    assert len(invoke(["sessions", "--project", project.id]).warnings) == 1


def test_mcp_is_never_notified(before, project):
    from twicc.mcp.identity import mcp_call
    from twicc.rpc.invoker import invoke

    make_sessions(project, 1)
    token = mcp_call.set(True)
    try:
        result = invoke(["sessions", "--project", project.id])
    finally:
        mcp_call.reset(token)
    assert result.warnings == ()


def test_a_failing_command_still_carries_its_notice(before, project):
    """The notice is about how the command was called, not about its outcome."""
    from twicc.rpc.invoker import invoke

    result = invoke(["sessions", "--include-hidden", "--only-hidden"])
    assert result.exit_code != 0
    # This one exits in the Typer wrapper, above the body: no notice, by design.
    assert result.warnings == ()

    result = invoke(["session", "co-sess-0", "messages", "--role", "bogus"])
    assert result.exit_code != 0
    assert len(result.warnings) == 1, "an emit_error inside the body keeps it"


# --- the log carrier --------------------------------------------------------


def _isolate_logger(monkeypatch, *, handlers):
    """Drive the log branch explicitly, on a logger the harness cannot reach.

    Patching the real ``twicc.cli.pagination`` logger does not survive: every
    command body calls ``django.setup()``, which re-applies ``dictConfig``, and
    ``settings_test`` sets ``disable_existing_loggers: True`` — so the logger is
    re-disabled and its handlers wiped mid-test. Production sets that flag to
    ``False`` (``settings.py:347``), where the logger stays live and writes to
    ``backend.log`` normally.

    A directly-constructed ``Logger`` is absent from ``Logger.manager``, so
    ``dictConfig`` cannot touch it and both branches stay under the test's
    control.
    """
    logger = logging.Logger("pagination-notice-test")  # noqa: LOG001 — unregistered on purpose
    for handler in handlers:
        logger.addHandler(handler)
    monkeypatch.setattr(_output, "_NOTICE_LOGGER", logger)
    return logger


def test_the_log_carrier_fires_when_a_handler_exists(before, project, capsysbinary, monkeypatch):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    _isolate_logger(monkeypatch, handlers=[Collect()])
    make_sessions(project, 1)
    cli_sessions.main(project=project.id)
    assert len(records) == 1
    assert "--paginated" in records[0].getMessage()


def test_the_log_carrier_is_skipped_without_a_handler(before, project, capsysbinary, monkeypatch):
    """No handler, no record — and crucially no duplicate on stderr via
    ``logging.lastResort``, which passes anything at WARNING."""
    _isolate_logger(monkeypatch, handlers=[])
    make_sessions(project, 1)
    cli_sessions.main(project=project.id)
    _, err = read(capsysbinary)
    assert err.count("twicc: from ") == 1


def test_a_django_free_command_is_still_covered_by_stderr(before, capsysbinary, monkeypatch):
    """`workspaces` never calls django.setup(), so it has no log line on the
    terminal path — the guarantee holds through stderr alone."""
    _isolate_logger(monkeypatch, handlers=[])
    cli_workspaces.main()
    _, err = read(capsysbinary)
    assert "`workspaces`" in err


# --- the help texts flip themselves ----------------------------------------


def test_the_help_builders_track_the_cutover(before):
    """Click help becomes the MCP tool schema, so a string that outlives its
    behaviour misinforms every agent. Deriving it from the constant removes the
    follow-up edit — and the release that would have to be cut to carry it."""
    assert _output.cutover_help("old", "new") == "old"


def test_the_help_builders_flip_past_the_cutover(after):
    assert _output.cutover_help("old", "new") == "new"


def test_the_limit_help_names_both_page_sizes_before(before):
    assert _output.limit_help("sessions", 20) == (
        "Max number of sessions to return (default: 20; 50 with --paginated)."
    )


def test_the_limit_help_collapses_after(after):
    assert _output.limit_help("sessions", 20) == (
        "Max number of sessions to return (default: 50)."
    )


def test_a_command_already_at_fifty_never_mentions_the_flag(before):
    """`share` pages at 50 on both sides, so the clause would be noise."""
    assert "--paginated" not in _output.limit_help("shares", 50)


def test_the_mcp_descriptions_match_the_side_of_the_cutover_we_are_on():
    """The notice reaches agents through the schema, which is the reason they
    need no runtime warning.

    No clock fixture here, deliberately: the descriptions are built from
    import-time constants, so monkeypatching after import cannot move them. The
    assertion is therefore two-sided — every listing carries the notice while
    the cutover is ahead, and none does once it has passed.
    """
    from twicc.mcp.tools import iter_mcp_tools

    listings = {
        "projects", "workspaces", "sessions", "artifacts", "share", "processes",
        "search", "session_content", "session_messages", "session_agents",
        "session_workflows",
    }
    described = {
        t.name: t.description for t in iter_mcp_tools() if t.name in listings
    }
    assert set(described) == listings

    announced = {n for n, d in described.items() if d.startswith("DEPRECATION")}
    if _output.pagination_is_default():
        assert announced == set(), "the migration is over; the notice should be gone"
    else:
        assert announced == listings, f"no notice in: {sorted(listings - announced)}"
