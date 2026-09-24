"""The default cursor: after the last user message, when the compute is current."""

from __future__ import annotations

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import session as cli_session
from twicc.cli import sessions_wait_reply
from twicc.cli._wait_reply import default_wait_cursors
from twicc.core.enums import ItemKind
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.providers.helpers import get_provider_helpers

TWICC_PID = 7171


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    from twicc.cli import _twicc_info, _wait_reply

    monkeypatch.setattr(_wait_reply, "POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 0.02)
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(_twicc_info, "resolve_live_twicc", lambda: type("I", (), {"pid": TWICC_PID})())


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-dc", directory="/tmp/dc")


def make(project, sid, *, provider="claude_code", ready=True, last_line=10):
    # Never a literal version: settings_test sets its own (CLAUDE_CODE_COMPUTE_VERSION = 99).
    version = get_provider_helpers(provider).current_compute_version if ready else None
    return Session.objects.create(
        id=sid, project=project, provider=provider, file_path=f"{sid}.jsonl",
        type=SessionType.SESSION, created_at=timezone.now(), last_line=last_line,
        user_message_count=1, compute_version=version,
    )


def running(session):
    now = timezone.now()
    ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now, state=AgentState.ASSISTANT_TURN.value, last_state_change_at=now,
        awaiting_user_input=False,
    )


def user(session, line):
    session.items.create(line_num=line, kind=ItemKind.USER_MESSAGE, content=orjson.dumps(
        {"type": "user", "message": {"role": "user", "content": "go"}}).decode())


def final(session, line, text="done"):
    session.items.create(line_num=line, kind=ItemKind.ASSISTANT_MESSAGE, content=orjson.dumps({
        "type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": text}]}}).decode())


def wait(capsysbinary, sid, **kw):
    kw.setdefault("timeout", 0.5)
    with pytest.raises(typer.Exit):
        cli_session.wait_reply(sid, **kw)
    return orjson.loads(capsysbinary.readouterr().out)["reply"]


def test_an_answer_given_before_the_wait_is_returned(project, capsysbinary):
    s = make(project, "a1")
    running(s)
    user(s, 3)
    final(s, 5, "early")
    reply = wait(capsysbinary, "a1")
    assert (reply["outcome"], reply["line_num"], reply["text"]) == ("replied", 5, "early")


def test_no_user_message_waits_from_zero(project):
    assert default_wait_cursors([make(project, "a2")]) == {"a2": 0}


def test_a_re_messaged_session_returns_the_new_answer(project, capsysbinary):
    s = make(project, "a3")
    running(s)
    user(s, 2)
    final(s, 3, "first")
    user(s, 4)
    final(s, 6, "second")
    assert wait(capsysbinary, "a3")["text"] == "second"


def test_a_re_messaged_session_not_answered_yet_waits(project, capsysbinary):
    """Review focus 4: the answer to the previous message is not returned."""
    s = make(project, "a4")
    running(s)
    user(s, 2)
    final(s, 3, "old")
    user(s, 4)
    assert wait(capsysbinary, "a4", timeout=0.2)["outcome"] != "replied"


def test_a_queued_command_does_not_move_the_anchor(project, capsysbinary):
    """The documented Claude busy-message limit, pinned: a message delivered
    while busy is a SYSTEM attachment, so the anchor stays on the previous one."""
    s = make(project, "a5")
    running(s)
    user(s, 2)
    final(s, 5, "running turn's close")
    s.items.create(line_num=6, kind=ItemKind.SYSTEM, content=orjson.dumps(
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "later"}}).decode())
    assert default_wait_cursors([s]) == {"a5": 2}
    assert wait(capsysbinary, "a5")["line_num"] == 5


def test_an_idle_session_ending_on_an_api_error(project, capsysbinary):
    s = make(project, "a6")
    user(s, 2)
    s.items.create(line_num=4, kind=ItemKind.API_ERROR, content=orjson.dumps({
        "type": "system", "subtype": "api_error", "isApiErrorMessage": True,
        "result": "You've hit your usage limit."}).decode())
    assert wait(capsysbinary, "a6")["outcome"] == "provider_error"


def test_explicit_cursors_are_unchanged(project, capsysbinary):
    s = make(project, "a7")
    running(s)
    user(s, 2)
    final(s, 5)
    assert wait(capsysbinary, "a7", from_line=5, timeout=0.2)["outcome"] != "replied"
    assert wait(capsysbinary, "a7", from_line=0)["line_num"] == 5


def test_since_is_unchanged_on_a_ready_session(project, capsysbinary):
    """Spec § Tests "Cursor": `--since` keeps its meaning when the compute is
    current — the instant places the cursor, not the last user message."""
    from datetime import UTC, datetime, timedelta

    s = make(project, "a9")
    running(s)
    base = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    user(s, 2)
    final(s, 5, "after the instant")
    s.items.filter(line_num=2).update(timestamp=base)
    s.items.filter(line_num=5).update(timestamp=base + timedelta(minutes=10))
    early = (base + timedelta(minutes=5)).isoformat()
    late = (base + timedelta(minutes=20)).isoformat()
    assert wait(capsysbinary, "a9", since=early)["line_num"] == 5
    assert wait(capsysbinary, "a9", since=late, timeout=0.2)["outcome"] != "replied"


def test_the_indexing_lag_case_returns_the_previous_answer(project, capsysbinary):
    """Documented and accepted: a send without --wait-reply, then a wait
    without --from, before the new message is indexed."""
    s = make(project, "a8")
    running(s)
    user(s, 2)
    final(s, 3, "previous")
    assert wait(capsysbinary, "a8")["text"] == "previous"


@pytest.mark.parametrize("version", ["null", "older"])
def test_a_session_whose_compute_is_not_current_keeps_last_line(project, capsysbinary, version):
    s = make(project, "b1", ready=False, last_line=10)
    if version == "older":
        current = get_provider_helpers("claude_code").current_compute_version
        Session.objects.filter(id="b1").update(compute_version=current - 1)
        s.refresh_from_db()
    running(s)
    user(s, 2)
    final(s, 5, "below last_line")
    assert default_wait_cursors([s]) == {"b1": 10}
    assert wait(capsysbinary, "b1", timeout=0.2)["outcome"] != "replied"


def test_a_codex_null_kind_item_does_not_move_the_anchor(project):
    s = make(project, "c1", provider="codex")
    user(s, 3)
    s.items.create(line_num=4, kind=None, content=orjson.dumps(
        {"type": "response_item", "payload": {"type": "function_call_output"}}).decode())
    assert default_wait_cursors([s]) == {"c1": 3}


def test_the_helper_asks_one_query_for_a_batch(project, django_assert_num_queries):
    ready = [make(project, f"r{i}") for i in range(3)]
    for s in ready:
        user(s, 7)
    stale = make(project, "st", ready=False, last_line=40)
    with django_assert_num_queries(1):
        cursors = default_wait_cursors([*ready, stale])
    assert cursors == {"r0": 7, "r1": 7, "r2": 7, "st": 40}


def test_the_plural_reads_every_cursor_in_one_query(project, capsysbinary, monkeypatch):
    """The command itself, not only the helper: one USER_MESSAGE query per batch."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    monkeypatch.setattr(
        "twicc.cli._wait_reply.wait_for_replies",
        lambda cursors, **kw: {sid: {"outcome": "timeout", "since_line_num": c} for sid, c in cursors.items()},
    )
    for i in range(3):
        user(make(project, f"q{i}"), 5)
    with CaptureQueriesContext(connection) as queries:
        sessions_wait_reply.main(["q0", "q1", "q2"], timeout=1)
    item_queries = [q for q in queries.captured_queries if "core_sessionitem" in q["sql"]]
    assert len(item_queries) == 1


def test_the_plural_applies_both_rules(project, capsysbinary, monkeypatch):
    seen = {}

    def fake(cursors, **kw):
        seen.update(cursors)
        return {sid: {"outcome": "timeout", "since_line_num": c} for sid, c in cursors.items()}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake)
    ready = make(project, "p1")
    user(ready, 4)
    make(project, "p2", ready=False, last_line=9)
    sessions_wait_reply.main(["p1", "p2"], timeout=1)
    assert seen == {"p1": 4, "p2": 9}
