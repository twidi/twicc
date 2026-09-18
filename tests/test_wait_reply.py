"""Tests for the wait-for-an-answer loop behind ``create-session --wait-reply``.

Two contracts are locked here. The loop reads the **transcript** to know an
answer arrived — so a session parked in ``ASSISTANT_TURN`` by a Monitor or a
live subagent is reported as soon as it speaks, not hours later when it goes
idle. And it watches the **process** only to know when to give up, because a
turn that crashes or closes on an empty text never produces the marker the
fast path waits for.

The cursor is the third contract: a message at or below it belongs to an
earlier turn and must never be mistaken for the answer to what was just sent.
"""

from __future__ import annotations

from datetime import timedelta

import time

import orjson
import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli import _wait_reply
from twicc.cli._wait_reply import (
    BACKEND_GONE,
    ENDED,
    PROVIDER_ERROR,
    REPLIED,
    TIMEOUT,
    WAIT_FAILED,
    wait_for_reply,
)
from twicc.core.enums import ItemKind
from twicc.core.models import ProcessRun, Project, Session, SessionItem, SessionType


TWICC_PID = 4242

# One poll of simulated wall time. The settle window below is five of these,
# so a test can put an event far enough past the moment a broken loop would
# have concluded for the difference to be observable. Coarse on purpose: the
# margins are counted in ticks, and a finer grain would make them jitter.
TICK_SECONDS = 0.02


@pytest.fixture(autouse=True)
def fast_loop(monkeypatch):
    """Strip the wall-clock out of the loop: these tests are about logic."""
    monkeypatch.setattr(_wait_reply, "POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 0.05)
    # Five ticks: long enough for a test to place an event on either side of
    # the window and see the difference.
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 5 * TICK_SECONDS)
    


@pytest.fixture
def on_tick(monkeypatch):
    """Run callbacks from inside the loop, one per poll, without threads.

    The loop's own ``time.sleep`` is the hook: replacing it lets a test change
    the world between two polls exactly the way the watcher would, which is
    what the race-sensitive behaviour below needs to be tested at all.
    """
    def install(schedule):
        """``schedule`` maps a tick number to what the world does at that tick.

        Ticks advance real (tiny) wall time, because the behaviour under test
        is a race between two clocks: a loop spinning at zero cost would let
        the answer land before any deadline could bite, and every mutant would
        survive.
        """
        ticks = {"n": 0}
        # Captured before patching: ``_wait_reply.time`` *is* the ``time``
        # module, so calling ``time.sleep`` from the replacement would call
        # the replacement.
        real_sleep = time.sleep

        def fake_sleep(_seconds):
            ticks["n"] += 1
            action = schedule.get(ticks["n"])
            if action is not None:
                action()
            real_sleep(TICK_SECONDS)

        monkeypatch.setattr(_wait_reply.time, "sleep", fake_sleep)

    return install


@pytest.fixture(autouse=True)
def live_twicc(monkeypatch):
    """Pin the running instance's pid: ``ProcessRun`` rows are keyed on it."""
    from twicc.cli import _twicc_info

    monkeypatch.setattr(
        _twicc_info, "resolve_live_twicc", lambda: type("Info", (), {"pid": TWICC_PID})(),
    )


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-twicc-wait", directory="/tmp/twicc-wait")


def make_session(project, provider="claude_code", session_id="wait-sess"):
    return Session.objects.create(
        id=session_id, project=project, provider=provider,
        file_path=f"{session_id}.jsonl", type=SessionType.SESSION,
        created_at=timezone.now(), mtime=1000, last_line=0, user_message_count=1,
    )


def assistant(session, line_num, text, stop_reason):
    """One assistant line, in the shape its provider actually writes.

    ``stop_reason`` is Claude's vocabulary, translated for Codex — the two
    mark the same thing differently, and a test that wrote Claude lines into a
    Codex session would extract nothing and pass or fail for the wrong reason.
    ``None`` means no marker at all on either side.
    """
    if session.provider == "codex":
        item = {"type": "AgentMessage", "id": f"item-{line_num}",
                "content": [{"type": "Text", "text": text}]}
        if stop_reason is not None:
            item["phase"] = "final_answer" if stop_reason == "end_turn" else "commentary"
        content = {"type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": "t", "turn_id": "turn-1", "item": item,
        }}
    else:
        message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        if stop_reason is not None:
            message["stop_reason"] = stop_reason
        content = {"type": "assistant", "message": message}
    SessionItem.objects.create(
        session=session, line_num=line_num, kind=ItemKind.ASSISTANT_MESSAGE,
        content=orjson.dumps(content).decode(),
    )


@pytest.fixture
def jsonl(tmp_path, monkeypatch):
    """Give a session a real file, so the "is the watcher behind?" check works.

    ``size > last_offset`` is what tells the loop the transcript is still
    being read; ``size == last_offset`` is what lets it conclude.

    The stored path is **relative**, and the provider root is patched to
    resolve it — that is the shape production writes. An earlier version of
    this fixture stored an absolute path, which made every test here pass
    against a resolution step that was in fact broken and never ran.
    """
    from twicc import provider_homes

    # Two *different* roots on purpose: patching both to the same directory
    # would make the provider dispatch look right whichever root it picked.
    roots = {"claude_code": tmp_path / "claude", "codex": tmp_path / "codex"}
    for directory in roots.values():
        directory.mkdir()
    monkeypatch.setattr(provider_homes, "claude_projects_dir", lambda: roots["claude_code"])
    monkeypatch.setattr(provider_homes, "codex_sessions_dir", lambda: roots["codex"])

    def write(session, *, size):
        path = roots[session.provider] / "session.jsonl"
        path.write_bytes(b"x" * size)
        Session.objects.filter(id=session.id).update(
            file_path="session.jsonl", last_offset=0,
        )
        return path

    return write


def process(session, state, *, awaiting=False):
    now = timezone.now()
    return ProcessRun.objects.create(
        provider=session.provider, session_id=session.id, twicc_pid=TWICC_PID,
        started_at=now - timedelta(seconds=5), state=state,
        last_state_change_at=now, awaiting_user_input=awaiting,
    )


def wait(session, **kwargs):
    kwargs.setdefault("since_line_num", 0)
    kwargs.setdefault("timeout", 2.0)
    kwargs.setdefault("want_text", True)
    return wait_for_reply(session.id, **kwargs)


# --- The fast path -----------------------------------------------------------


def test_a_final_message_is_the_answer(project):
    session = make_session(project)
    assistant(session, 3, "on it", "tool_use")
    assistant(session, 5, "done", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 5
    assert reply["is_final"] is True
    assert reply["text"] == "done"
    assert reply["since_line_num"] == 0


def test_the_answer_is_reported_while_the_process_is_still_busy(project):
    """The whole point: a held turn must not delay the answer.

    A session parked in ASSISTANT_TURN by a Monitor or a live subagent has
    already written its closing message. Waiting for USER_TURN there costs
    hours; this returns at once.
    """
    session = make_session(project)
    assistant(session, 2, "the analysis", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    assert wait(session)["outcome"] == REPLIED


def test_no_reply_text_drops_the_text_and_keeps_the_pointer(project):
    session = make_session(project)
    assistant(session, 2, "done", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, want_text=False)

    # Absent rather than null, so "you did not ask for it" never reads as a
    # value the loop found.
    assert "text" not in reply
    assert reply["line_num"] == 2


# --- The cursor --------------------------------------------------------------


def test_a_final_message_at_or_below_the_cursor_is_not_the_answer(project):
    """It belongs to an earlier turn — the staleness the cursor exists for."""
    session = make_session(project)
    assistant(session, 7, "the previous answer", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, since_line_num=7, timeout=0.2)

    assert reply["outcome"] == TIMEOUT
    assert reply["since_line_num"] == 7


def test_the_first_final_past_the_cursor_wins(project):
    """A later one would belong to a turn the caller did not trigger."""
    session = make_session(project)
    assistant(session, 4, "answer to mine", "end_turn")
    assistant(session, 9, "answer to something else", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    assert wait(session, since_line_num=2)["line_num"] == 4


# --- The backstop ------------------------------------------------------------


def test_a_turn_that_ends_without_a_marker_stops_the_wait(project):
    """Otherwise this would hang until the deadline on every crash.

    The last thing the agent said comes back regardless, because it is
    usually what the caller wanted.
    """
    session = make_session(project)
    assistant(session, 3, "I'll start by reading it", "tool_use")
    process(session, AgentState.USER_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == ENDED
    assert reply["line_num"] == 3
    assert reply["is_final"] is False
    assert reply["text"] == "I'll start by reading it"


def test_a_dead_process_stops_the_wait_with_nothing_to_show(project):
    session = make_session(project)

    reply = wait(session)

    assert reply["outcome"] == ENDED
    assert reply["line_num"] is None
    assert "text" not in reply


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_an_answer_still_being_indexed_is_not_a_finished_turn(project, on_tick, jsonl, provider):
    """The defect the first real run exposed, in a test.

    The manager persists the ``ProcessRun`` transition synchronously while the
    answer reaches ``SessionItem`` through the watcher, whose write waits on
    the process-wide DB lock. So the process reads idle while the message that
    closed the turn is still on its way, and a backstop that trusts the
    process alone reports ``ended`` for a session that answered — which is
    what happened live: ``ended`` in 2.8s on a session whose answer sat at
    line 19.
    """
    session = make_session(project, provider=provider)
    row = process(session, AgentState.ASSISTANT_TURN.value)
    # The file is ahead of what the watcher indexed: the turn is NOT settled.
    # Both providers, because each stores its transcript under its own root —
    # a dispatch that always picked one would resolve to a path that does not
    # exist, and a missing file reads as "settled", reviving the dead guard.
    behind = jsonl(session, size=4096)

    def goes_idle():
        ProcessRun.objects.filter(pk=row.pk).update(state=AgentState.USER_TURN.value)

    def watcher_catches_up():
        assistant(session, 19, "the answer", "end_turn")
        behind.write_bytes(b"")

    # Tick 2 puts the process at rest; the answer only lands at tick 20, long
    # past the point (tick 7) where a loop trusting the process alone would
    # have given up. That gap is the test.
    on_tick({2: goes_idle, 20: watcher_catches_up})

    reply = wait(session, timeout=5.0)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 19


def test_no_session_row_yet_is_not_a_finished_turn(project, on_tick, jsonl, monkeypatch):
    """The same race as above, one window earlier.

    The watcher creates the ``Session`` row when it first reads the file, and
    that write queues behind the same lock. Meanwhile the ``ProcessRun`` row
    exists from the start, so the loop has "it was working" and "it is idle
    now" — and nothing to check the transcript against, because there is no
    row to check. Concluding there would report ``ended`` for an agent that
    answered, which is the whole defect this module exists to avoid.
    """
    # The grace is the only thing bounding a row that never arrives, so it has
    # to outlast the tick the row lands on — otherwise it, and not the fix,
    # would be what keeps the loop alive.
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 1.0)

    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value)
    session_id = session.id
    Session.objects.filter(id=session_id).delete()  # the watcher has not written it yet

    def goes_idle():
        ProcessRun.objects.filter(pk=row.pk).update(state=AgentState.USER_TURN.value)

    def watcher_catches_up():
        make_session(project)
        jsonl(Session.objects.get(id=session_id), size=0)
        assistant(Session.objects.get(id=session_id), 19, "the answer", "end_turn")

    # Idle from tick 2; the row only lands at tick 10, five ticks past the
    # point a loop counting from "it was working" would have given up.
    on_tick({2: goes_idle, 10: watcher_catches_up})

    reply = wait_for_reply(session_id, since_line_num=0, timeout=5.0, want_text=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 19


def test_an_answer_flushed_after_the_agent_stopped_is_not_lost(project, on_tick, jsonl):
    """The regression a real run caught, and the tests did not.

    An agent declares itself stopped **before** its answer is readable: the
    manager persists that transition synchronously while the line is still to
    be flushed. Measured over five sessions, the answer appeared 0.33 to 0.40s
    later — every single time. A loop that ends on "it stopped" plus "the file
    is fully indexed" therefore reports ``ended`` for a session that answered:
    the file really was complete, it just did not contain the answer yet.
    """
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value)
    jsonl(session, size=0)  # settled: the file check alone would let it conclude

    def stops():
        ProcessRun.objects.filter(pk=row.pk).update(state=AgentState.USER_TURN.value)

    def flushes():
        assistant(session, 19, "the answer", "end_turn")

    # Stopped at tick 2, answer at tick 5 — inside the flush window, which is
    # five ticks. Without that window the loop concludes at tick 2.
    on_tick({2: stops, 5: flushes})

    reply = wait(session, timeout=5.0)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 19


def test_the_offset_is_re_read_while_the_wait_runs(project, on_tick, jsonl):
    """``last_offset`` is a moving target, and the cached row holds a stale one.

    The ``Session`` row is fetched once — ``provider`` and ``file_path`` never
    change — but ``last_offset`` advances every time the watcher indexes more.
    Without the refresh it stays at whatever it was when the row was read, so
    ``_watcher_is_behind`` answers "still catching up" forever and ``ended``
    can never fire: every crashed session would cost the whole deadline.
    """
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value)
    path = jsonl(session, size=4096)  # watcher behind: 4096 written, 0 indexed

    def stops():
        ProcessRun.objects.filter(pk=row.pk).update(state=AgentState.USER_TURN.value)

    def watcher_catches_up():
        # Exactly what the watcher does: the items, then the offset it reached.
        Session.objects.filter(id=session.id).update(last_offset=path.stat().st_size)

    on_tick({2: stops, 4: watcher_catches_up})

    assert wait(session, timeout=5.0)["outcome"] == ENDED


def test_an_agent_that_died_before_writing_anything_does_not_cost_the_deadline(
    project, monkeypatch,
):
    """No ``Session`` row will ever exist, so nothing can prove completeness.

    The watcher writes that row when it first sees the JSONL file; an agent
    that died before writing leaves no file, hence no row, hence no
    ``last_offset`` to compare against. Bounded by its own grace so the caller
    is not left waiting out ``--wait-timeout`` for a session that never
    started.
    """
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 0.02)
    session = make_session(project)
    session_id = session.id
    process(session, AgentState.USER_TURN.value)
    Session.objects.filter(id=session_id).delete()

    reply = wait_for_reply(session_id, since_line_num=0, timeout=5.0, want_text=True)

    assert reply["outcome"] == ENDED
    assert reply["line_num"] is None
    # Ended on its own bound, far short of the deadline it was given.
    assert reply["waited_seconds"] < 1.0


def test_the_flush_window_applies_without_a_session_row_too(project, on_tick, monkeypatch):
    """The no-row ending measures from the wait's start, not from the stop.

    So on its own it concludes the instant the grace elapses, however recently
    the agent stopped — and an agent that stops at the very end of that grace
    has not had a moment to flush. Both bounds have to hold.
    """
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 3 * TICK_SECONDS)
    session = make_session(project)
    session_id = session.id
    row = process(session, AgentState.ASSISTANT_TURN.value)
    Session.objects.filter(id=session_id).delete()

    def stops():
        ProcessRun.objects.filter(pk=row.pk).update(state=AgentState.USER_TURN.value)

    # Stops at tick 4, past the grace — so only the flush window can keep the
    # loop alive long enough for the deadline to be what ends it.
    on_tick({4: stops})

    reply = wait_for_reply(session_id, since_line_num=0, timeout=6 * TICK_SECONDS,
                           want_text=True)

    assert reply["outcome"] == TIMEOUT


def test_an_answer_committed_during_the_settle_check_is_not_lost(project, monkeypatch, jsonl):
    """The scan runs before the check, and the watcher commits both at once.

    Items and ``last_offset`` land in one transaction, so a commit slipping
    between this tick's scan and its offset read leaves an answer in the DB,
    unscanned, while the offset already reads "fully indexed". Simulated by
    committing from inside the check itself — the exact interleaving.
    """
    session = make_session(project)
    jsonl(session, size=0)
    process(session, AgentState.USER_TURN.value)
    monkeypatch.setattr(_wait_reply, "AGENT_FLUSH_SECONDS", 0.0)

    real = _wait_reply._watcher_is_behind
    fired = {"done": False}

    def commits_then_answers(sess):
        if not fired["done"]:
            fired["done"] = True
            assistant(session, 19, "the answer", "end_turn")
        return real(sess)

    monkeypatch.setattr(_wait_reply, "_watcher_is_behind", commits_then_answers)

    reply = wait(session, timeout=2.0)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 19


def test_a_cleared_block_is_not_reported_on_the_answer(project, on_tick):
    """The flag says why nothing came; on an answer there is nothing to explain.

    The block has to be seen *first* and cleared *after*, which is the whole
    point: the flag is sticky, so an agent that asked for an approval, got it
    clicked, and then answered would otherwise hand back ``replied`` carrying a
    block that no longer exists.
    """
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    def clicked_then_answered():
        ProcessRun.objects.filter(pk=row.pk).update(awaiting_user_input=False)
        assistant(session, 5, "here it is", "end_turn")

    on_tick({2: clicked_then_answered})

    reply = wait(session, timeout=2.0)

    assert reply["outcome"] == REPLIED
    assert "awaiting_user_input" not in reply


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_a_settled_transcript_with_no_answer_really_is_over(project, jsonl, provider):
    """The counterpart: once the file is fully indexed, stop waiting.

    Parametrized because the transcript lives under a different root per
    provider, and a dispatch that always picked one would resolve to a path
    that does not exist — silently reviving the dead-guard bug for the other.
    """
    session = make_session(project, provider=provider)
    jsonl(session, size=0)
    assistant(session, 3, "I'll start by reading it", "tool_use")
    process(session, AgentState.USER_TURN.value)

    assert wait(session)["outcome"] == ENDED


def test_an_answer_beats_a_pending_request_seen_on_the_same_tick(project):
    """Order matters: the transcript is read before the process is judged.

    An agent that answers and immediately asks for a tool approval has still
    answered — reporting the block instead would hide the reply behind a
    condition the caller could not act on.
    """
    session = make_session(project)
    assistant(session, 5, "here is the answer", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    assert wait(session)["outcome"] == REPLIED


def test_a_pending_click_does_not_end_the_wait(project, jsonl):
    """A human can still click, so abandoning here would not be certain.

    Every other early abort proves nothing more is coming. This one cannot:
    the answer is one click away. So the wait runs to its deadline and reports
    the condition instead of acting on it.
    """
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 3, "Which approach do you prefer?", "tool_use")
    process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    reply = wait(session, timeout=0.3)

    assert reply["outcome"] == TIMEOUT
    assert reply["awaiting_user_input"] is True
    assert reply["text"] == "Which approach do you prefer?"


def test_a_wait_that_never_saw_a_pending_click_says_nothing_about_it(project, jsonl):
    session = make_session(project)
    jsonl(session, size=0)
    process(session, AgentState.ASSISTANT_TURN.value)

    assert "awaiting_user_input" not in wait(session, timeout=0.2)


def test_inside_the_backend_the_agent_state_comes_from_memory(project, monkeypatch, jsonl):
    """No query for something the process already holds.

    Over MCP or ``/rpc/`` the command runs in the very process that owns the
    agents. A session working for an hour polls 14 400 times; going to the DB
    each time would ask for state sitting in memory. The registry answering
    "still working" must be enough on its own — here the ``ProcessRun`` row
    says the opposite, and the registry has to win.
    """
    from twicc.agent.registry import AgentManagerRegistry
    from twicc.cli._drop_request import transport

    session = make_session(project)
    jsonl(session, size=0)
    process(session, AgentState.USER_TURN.value)  # the DB says: stopped

    live = type("Info", (), {"state": AgentState.ASSISTANT_TURN, "pending_requests": ()})()
    monkeypatch.setattr(transport, "_in_backend", lambda: True)
    monkeypatch.setattr(AgentManagerRegistry, "get_agent_info", lambda self, sid: live)

    # Still working according to memory, so no ``ended`` despite the row.
    assert wait(session, timeout=0.2)["outcome"] == TIMEOUT


def test_a_working_session_costs_one_query_per_tick(project, monkeypatch, jsonl):
    """What a session that runs for an hour actually pays.

    At four polls a second, an hour is 14 400 ticks. Only the transcript scan
    has to happen every time: the ``Session`` row never changes, the agent
    state is in memory, and ``last_offset`` plus the file stat matter only once
    the agent stops. Anything else here is a per-tick cost multiplied by 14 400.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from twicc.agent.registry import AgentManagerRegistry
    from twicc.cli._drop_request import transport

    session = make_session(project)
    jsonl(session, size=0)
    process(session, AgentState.ASSISTANT_TURN.value)

    live = type("Info", (), {"state": AgentState.ASSISTANT_TURN, "pending_requests": ()})()
    monkeypatch.setattr(transport, "_in_backend", lambda: True)
    monkeypatch.setattr(AgentManagerRegistry, "get_agent_info", lambda self, sid: live)

    with CaptureQueriesContext(connection) as queries:
        wait(session, timeout=0.2)

    tables = [q["sql"] for q in queries]
    assert sum("core_session" in sql and "core_sessionitem" not in sql for sql in tables) == 1
    assert sum("core_processrun" in sql for sql in tables) == 0
    # Everything left is the transcript scan, the one thing that must happen.
    assert all("core_sessionitem" in sql for sql in tables
               if "core_session" not in sql or "core_sessionitem" in sql)


# --- Conditions the caller must be able to tell apart -------------------------


def api_error(session, line_num, *, terminal, text="You've hit your usage limit.", structured=False):
    """One ``API_ERROR`` item.

    ``terminal`` is what ``isApiErrorMessage`` marks: the error the CLI
    surfaces once retries are exhausted. Without it the line is a retry the
    CLI is still working through — same kind, opposite meaning.
    ``structured`` uses Codex's shape, where the text lives in
    ``error.message`` and the payload the extractors need has been popped.
    """
    if structured:
        content = {"type": "twicc_provider_error", "isApiErrorMessage": True,
                   "error": {"type": "usage_limit", "message": text}}
    else:
        content = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}}
        if terminal:
            content["isApiErrorMessage"] = True
        else:
            content = {"type": "system", "subtype": "api_error",
                       "retryAttempt": 3, "maxRetries": 10, "content": text}
    SessionItem.objects.create(
        session=session, line_num=line_num, kind=ItemKind.API_ERROR,
        content=orjson.dumps(content).decode(),
    )


def test_a_provider_refusal_is_not_a_crash(project):
    """A quota error and a crash both mean "no answer" and call for opposite
    responses: come back tomorrow, versus go and investigate."""
    session = make_session(project)
    api_error(session, 13, terminal=True)
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == PROVIDER_ERROR
    assert reply["line_num"] == 13
    assert "usage limit" in reply["text"]


def test_a_codex_refusal_carries_its_message(project):
    """Codex puts the text in ``error.message`` and pops what extraction reads.

    Falling back to extraction alone returns the empty string — which drops
    the only thing that makes this outcome actionable: whether to come back
    tomorrow or retry now.
    """
    session = make_session(project, provider="codex")
    api_error(session, 13, terminal=True, structured=True,
              text="You've hit your usage limit. Try again at Sep 19th.")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == PROVIDER_ERROR
    assert "Sep 19th" in reply["text"]


def test_a_retry_the_cli_is_still_working_through_is_not_a_refusal(project):
    """``type=system, subtype=api_error`` is progress, not an ending.

    Claude writes one per attempt of a 529 and keeps going — the watcher makes
    the same distinction for the same reason. Treating it as terminal tells
    the caller "your request was not the problem and retrying now fails the
    same way" about an error the provider healed one second later.
    """
    session = make_session(project)
    api_error(session, 3, terminal=False, text="API Error: 529 overloaded_error")
    process(session, AgentState.ASSISTANT_TURN.value)

    assert wait(session, timeout=0.3)["outcome"] == TIMEOUT


def test_an_answer_below_a_terminal_error_is_not_lost(project):
    """Even a *surfaced* error must not outrank a reply written after it.

    Order is what decides: the error sits at the lower line number, so a loop
    that scans errors first returns it and drops the answer below.
    """
    session = make_session(project)
    api_error(session, 3, terminal=True, text="API Error: 500")
    assistant(session, 9, "recovered, here it is", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == REPLIED
    assert reply["text"] == "recovered, here it is"


def test_an_answer_below_a_retry_is_not_lost(project):
    """The recovered turn: retries first, then the reply.

    Scanning errors before messages would return the retry at the lower line
    number and throw away the answer sitting right below it.
    """
    session = make_session(project)
    api_error(session, 3, terminal=False, text="API Error: 529 overloaded_error")
    assistant(session, 9, "the real answer", "end_turn")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session)

    assert reply["outcome"] == REPLIED
    assert reply["text"] == "the real answer"


def test_a_backend_that_went_away_is_not_a_finished_turn(project, monkeypatch):
    """Every poll re-reads the live instance, not just the first one.

    A backend that stops or restarts mid-wait makes every later poll see no
    ``ProcessRun`` row — which reads exactly like a finished turn. Reporting
    ``ended`` there would be a lie shaped like a crash.
    """
    from twicc.cli import _twicc_info

    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return type("Info", (), {"pid": TWICC_PID})() if calls["n"] == 1 else None

    monkeypatch.setattr(_twicc_info, "resolve_live_twicc", flaky)

    assert wait(session, timeout=2.0)["outcome"] == BACKEND_GONE


def test_a_backend_that_restarted_is_not_the_same_backend(project, monkeypatch):
    """A new pid means the rows this loop reads are about a dead instance.

    Distinct from the agent restarting under the same backend, which is normal
    and must keep the wait going.
    """
    from twicc.cli import _twicc_info

    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value)

    calls = {"n": 0}

    def restarted():
        calls["n"] += 1
        pid = TWICC_PID if calls["n"] == 1 else TWICC_PID + 1
        return type("Info", (), {"pid": pid})()

    monkeypatch.setattr(_twicc_info, "resolve_live_twicc", restarted)

    assert wait(session, timeout=2.0)["outcome"] == BACKEND_GONE


@pytest.mark.parametrize("boom", [
    RuntimeError("database is locked"),
    KeyboardInterrupt(),
])
def test_a_broken_wait_still_hands_back_an_outcome(project, monkeypatch, boom):
    """The id must survive the wait, whatever kills it.

    A long poll makes thousands of queries; one locked SQLite, or a Ctrl-C
    from a shell, and an unguarded call would raise past the command before it
    printed the session it just created — leaving an agent running that nobody
    can name. ``KeyboardInterrupt`` is in the list because it is the likeliest
    one and is not an ``Exception``.
    """
    from twicc.cli import _wait_reply as module

    def explode(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(module, "wait_for_reply", explode)

    reply = module.wait_for_reply_or_degrade(
        "sess", since_line_num=7, timeout=1.0, want_text=True,
    )

    assert reply["outcome"] == WAIT_FAILED
    assert reply["since_line_num"] == 7
    assert type(boom).__name__ in reply["error"]


# --- The startup window ------------------------------------------------------


def test_a_session_row_that_does_not_exist_yet_is_not_a_finished_turn(project, monkeypatch):
    """``create-session`` returns before the watcher has seen the JSONL file.

    Without the grace period the very first tick would read "no row" as "the
    turn is over" and every wait would report ``ended`` immediately. The
    deadline is set below the grace so only a premature conclusion could beat
    it: getting ``timeout`` is the proof that none happened.
    """
    monkeypatch.setattr(_wait_reply, "SESSION_ROW_GRACE_SECONDS", 5.0)

    reply = wait_for_reply(
        "not-created-yet", since_line_num=0, timeout=0.2, want_text=True,
    )

    assert reply["outcome"] == TIMEOUT


def test_the_deadline_hands_back_the_cursor_to_resume_from(project):
    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, since_line_num=41, timeout=0.2)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] is None
    assert reply["since_line_num"] == 41
    assert reply["waited_seconds"] >= 0


def test_the_deadline_is_actually_honoured(project):
    """Pin the duration, not just the outcome.

    Asserting only ``waited_seconds >= 0`` is vacuous: multiplying the
    deadline by ten leaves every other test green, and the overrun would also
    blow past the mirrored read timeout ``--remote`` computes from it, turning
    a wait into a transport failure.
    """
    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, timeout=0.3)

    assert reply["outcome"] == TIMEOUT
    assert 0.3 <= reply["waited_seconds"] < 1.5


def test_a_session_still_booting_is_not_a_finished_turn(project, jsonl):
    """``starting`` counts as working.

    Plugin and MCP initialisation routinely outlast the grace and settle
    windows, so dropping that state would report ``ended`` on a session that
    has not had a chance to speak yet.
    """
    session = make_session(project)
    jsonl(session, size=0)  # settled: only the state can hold the window open
    process(session, AgentState.STARTING.value)

    assert wait(session, timeout=0.3)["outcome"] == TIMEOUT


def test_the_deadline_keeps_what_the_agent_did_say(project):
    """A caller that times out mid-turn still gets the evidence it spoke."""
    session = make_session(project)
    assistant(session, 4, "I'm on it", "tool_use")
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, timeout=0.2)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 4
    assert reply["text"] == "I'm on it"


def test_an_unmarked_message_is_not_an_answer(project):
    """``is_final`` must be ``True``, not merely "not False".

    ``None`` means the marker is missing — common on subagent transcripts and
    on interrupted messages — and reading it as an answer would hand back a
    preamble as the reply.
    """
    session = make_session(project)
    assistant(session, 4, "no marker here", None)
    process(session, AgentState.ASSISTANT_TURN.value)

    reply = wait(session, timeout=0.2)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 4


def test_the_last_message_is_the_one_reported_not_the_first(project):
    session = make_session(project)
    assistant(session, 2, "first", "tool_use")
    assistant(session, 6, "most recent", "tool_use")
    process(session, AgentState.ASSISTANT_TURN.value)

    assert wait(session, timeout=0.2)["text"] == "most recent"


# --- The command's own surface -----------------------------------------------
#
# The loop is covered above; these pin what surrounds it — the flag guards, and
# the read timeout ``--remote`` computes from them. Both were entirely untested
# until a review mutated them and watched 3573 tests stay green.


def run_cli(*args):
    from typer.testing import CliRunner

    from twicc.cli import app

    return CliRunner().invoke(app, ["create-session", *args])


@pytest.mark.parametrize(("args", "flag"), [
    (["--wait-timeout", "42"], "--wait-timeout"),
    (["--no-reply-text"], "--no-reply-text"),
    (["--wait-blocked"], "--wait-blocked"),
    # The documented default, spelled out: comparing against the value instead
    # of "was it passed" would let this one through silently.
    (["--wait-timeout", "300"], "--wait-timeout"),
])
def test_the_wait_flags_are_refused_without_the_wait(args, flag):
    result = run_cli("hello", *args)

    assert result.exit_code == 1
    assert f"{flag} requires --wait-reply" in result.output


@pytest.mark.parametrize("value", ["0", "-5"])
def test_a_deadline_that_cannot_elapse_is_refused(value):
    result = run_cli("hello", "--wait-reply", "--wait-timeout", value)

    assert result.exit_code == 1
    assert "--wait-timeout must be > 0" in result.output


def test_the_remote_read_timeout_outlasts_the_wait():
    """``--remote`` holds the connection for the drop request *and* the wait.

    Without this the client gives up at its ordinary timeout while the session
    it just created keeps running, and the caller never learns its id — the
    failure the whole `reply` block exists to prevent.
    """
    from twicc.cli import _remote

    def read_timeout(**params):
        resolved = type("R", (), {"path": "create-session", "params": params})()
        return _remote._request_timeout(resolved).read

    plain = read_timeout(timeout=30)
    waiting = read_timeout(timeout=30, wait_reply=True, wait_timeout=None)
    explicit = read_timeout(timeout=30, wait_reply=True, wait_timeout=900)

    assert plain == _remote._DEFAULT_TIMEOUT
    assert waiting > _remote._DEFAULT_WAIT_TIMEOUT
    assert explicit > 900


def test_the_mirrored_default_cannot_drift():
    """``_remote`` duplicates the default rather than import a command module.

    Deliberate — that module sits on the ``--help`` path for every command —
    but a silent divergence would make a remote wait give up before the server
    answers, so the copy is pinned here instead.
    """
    from twicc.cli import _remote
    from twicc.cli.create_session.command import DEFAULT_WAIT_TIMEOUT_SECONDS

    assert _remote._DEFAULT_WAIT_TIMEOUT == DEFAULT_WAIT_TIMEOUT_SECONDS


# --- send-message's half ------------------------------------------------------
#
# Its cursor is the one thing that differs from create-session's: read
# server-side the instant the agent takes the message, it is what keeps the
# previous turn's closing message from answering for this one.


def run_send_cli(*args):
    from typer.testing import CliRunner

    from twicc.cli import app

    return CliRunner().invoke(app, ["send-message", "some-session", "hi", *args])


@pytest.mark.parametrize(("args", "flag"), [
    (["--wait-timeout", "42"], "--wait-timeout"),
    (["--no-reply-text"], "--no-reply-text"),
    (["--wait-blocked"], "--wait-blocked"),
    (["--wait-timeout", "300"], "--wait-timeout"),
])
def test_send_message_refuses_the_wait_flags_without_the_wait(args, flag):
    result = run_send_cli(*args)

    assert result.exit_code == 1
    assert f"{flag} requires --wait-reply" in result.output


@pytest.mark.parametrize("value", ["0", "-5"])
def test_send_message_rejects_a_deadline_that_cannot_elapse(value):
    result = run_send_cli("--wait-reply", "--wait-timeout", value)

    assert result.exit_code == 1
    assert "--wait-timeout must be > 0" in result.output


def test_the_cursor_reaches_the_caller_when_the_service_produced_one():
    """``last_line`` rides the generic ``status_extra`` passthrough.

    Without it the wait restarts from 0 and the previous turn's closing
    message answers for this one — which is the exact defect the cursor
    exists to prevent, and what a real two-turn run showed when the backend
    was still running a build without the field.
    """
    from twicc.cli._drop_request.output import build_final

    outcome = type("O", (), {"status": "sent", "data": {
        "session_id": "s", "provider": "claude_code", "project_id": "p",
        "last_line": 1243,
    }})()

    assert build_final(outcome, request_uuid="r", timeout=30)["last_line"] == 1243


def test_a_command_with_no_cursor_does_not_grow_a_null_one():
    """``create-session`` has no use for it; an absent key says so."""
    from twicc.cli._drop_request.output import build_final

    outcome = type("O", (), {"status": "created", "data": {
        "session_id": "s", "provider": "claude_code", "project_id": "p",
    }})()

    assert "last_line" not in build_final(outcome, request_uuid="r", timeout=30)


# ---------------------------------------------------------------------------
# Command wiring — the cursor actually reaches the wait
# ---------------------------------------------------------------------------
#
# ``build_final`` carrying ``last_line`` and the loop honouring a cursor are
# both locked above, but neither pins the one line that joins them. A mutant
# replacing the command's ``since_line_num=`` argument with a literal ``0``
# survived every test in this file — and a run with that mutant is exactly the
# two-turn failure the field exists to prevent, where the previous turn's
# closing message answers for the new one.


def _run_send_message(monkeypatch, status_data: dict, *, wait_blocked: bool = False,
                      wait_timeout=None, no_reply_text: bool = False) -> dict:
    """Drive ``send_message_cmd --wait-reply`` over a stubbed transport.

    Everything the command does before the send is real (prompt resolution,
    attachment validation, the settings lookup on the row); only the two
    process boundaries are cut — the drop-request round trip, and the wait
    loop, which is replaced by a probe returning the arguments it received.
    """
    import typer

    from twicc.cli import _wait_reply as wait_reply_module
    from twicc.cli._drop_request import session_lookup, transport, whoami
    from twicc.cli._drop_request.polling import PollOutcome
    from twicc.cli.send_message.command import send_message_cmd

    project = Project.objects.create(id="cursor-wiring-project")
    session = Session.objects.create(
        id="cursor-wiring-session", project=project, provider="claude_code",
    )

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    monkeypatch.setattr(
        session_lookup, "lookup_session",
        lambda sid: type("R", (), {
            "session_id": session.id, "provider": "claude_code",
            "spawned_by_id": None,
        })(),
    )

    class _Submission:
        request_uuid = "req-cursor-wiring"

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: _Submission())
    monkeypatch.setattr(
        transport, "wait",
        lambda sub, timeout_seconds: PollOutcome("sent", status_data, True),
    )

    seen: dict = {}

    def _probe(session_id, *, since_line_num, timeout, want_text, stop_when_blocked):
        seen["session_id"] = session_id
        seen["since_line_num"] = since_line_num
        seen["stop_when_blocked"] = stop_when_blocked
        seen["timeout"] = timeout
        seen["want_text"] = want_text
        return {"outcome": REPLIED}

    monkeypatch.setattr(wait_reply_module, "wait_for_reply_or_degrade", _probe)

    with pytest.raises(typer.Exit):
        send_message_cmd(
            session_id=session.id, prompt="hello", no_expand=False, attach=[],
            wait_reply=True, wait_timeout=wait_timeout, no_reply_text=no_reply_text,
            wait_blocked=wait_blocked, timeout=30,
        )
    return seen


@pytest.mark.django_db
def test_send_message_hands_the_server_cursor_to_the_wait(monkeypatch):
    seen = _run_send_message(monkeypatch, {
        "session_id": "cursor-wiring-session", "provider": "claude_code",
        "project_id": "cursor-wiring-project", "last_line": 87,
    })

    assert seen["since_line_num"] == 87


@pytest.mark.django_db
def test_send_message_starts_from_zero_when_the_server_sent_no_cursor(monkeypatch):
    """An older backend, or a result kind that carries no cursor.

    Waiting from 0 then risks returning an earlier message, but the command
    must still wait rather than crash on the missing key.
    """
    seen = _run_send_message(monkeypatch, {
        "session_id": "cursor-wiring-session", "provider": "claude_code",
        "project_id": "cursor-wiring-project",
    })

    assert seen["since_line_num"] == 0


# ---------------------------------------------------------------------------
# Service wiring — where the cursor is produced
# ---------------------------------------------------------------------------
#
# The two tests above pin the CLI end of the chain. This one pins its source:
# the single line in ``send_message_to_session_from_payload`` that reads
# ``Session.last_line`` once the agent has taken the message. Deleting it left
# the whole suite green, while every ``--wait-reply`` would have silently
# restarted from 0 and returned the previous turn's answer.


@pytest.mark.django_db(transaction=True)
def test_the_service_reads_the_cursor_after_the_agent_took_the_message(monkeypatch):
    """The moment matters as much as the value.

    A cursor read *before* the send would sit below lines the new turn is
    about to write only by luck; read after, every line past it provably
    belongs to the turn just triggered. The stub moves ``last_line`` while
    the send is in flight, so a read placed too early returns the old value.
    """
    import asyncio

    from asgiref.sync import sync_to_async

    from twicc.core.services.send_message import send_message_to_session_from_payload

    project = Project.objects.create(id="cursor-service-project", directory="/tmp")
    session = Session.objects.create(
        id="cursor-service-session", project=project,
        provider="claude_code", last_line=100,
    )

    class _Manager:
        async def send_to_session(self, session_id, *args, **kwargs):
            # What the watcher does while the agent starts its turn.
            await sync_to_async(
                lambda: Session.objects.filter(id=session_id).update(last_line=137),
            )()

    class _Registry:
        def get(self, provider):
            return _Manager()

    import twicc.agent.registry as registry_module
    import twicc.core.services.send_message as service_module

    monkeypatch.setattr(registry_module, "get_agent_manager_registry", lambda: _Registry())
    # Unrelated precondition: the runtime provider gate reads process-wide
    # state no test sets up.
    monkeypatch.setattr(service_module, "ensure_provider_running", lambda provider: None)

    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": session.id, "text": "hello"},
    ))

    assert result.success is True
    assert result.status_extra["last_line"] == 137


# ---------------------------------------------------------------------------
# `--wait-blocked` — stop when a human is what is missing
# ---------------------------------------------------------------------------
#
# Off by default, and deliberately so: waiting through a block is right
# whenever a human is there to clear it, and the case where nobody is —
# a `--hidden` worker — is the case where blocking cannot happen at all
# (hidden enforces a non-interactive permission mode). The flag is for a
# visible session, where a script would otherwise burn its whole timeout
# waiting on a click nobody is coming to make.


def test_a_block_ends_the_wait_when_asked(project):
    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    reply = wait(session, timeout=2.0, stop_when_blocked=True)

    assert reply["outcome"] == "awaiting_user_input"


def test_a_block_is_waited_through_by_default(project, on_tick):
    """The shipped behaviour, unchanged: a human clicks, the answer arrives."""
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    def clicked_then_answered():
        ProcessRun.objects.filter(pk=row.pk).update(awaiting_user_input=False)
        assistant(session, 5, "here it is", "end_turn")

    on_tick({2: clicked_then_answered})

    reply = wait(session, timeout=2.0)

    assert reply["outcome"] == REPLIED


def test_an_answer_wins_a_tie_against_a_block(project, on_tick):
    """Both flags are OR-combined, and the transcript is scanned first.

    A turn that answered and then asked for something has answered — giving
    back ``awaiting_user_input`` would hide a reply the caller can read.
    """
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value)

    def answered_and_blocked():
        assistant(session, 5, "done, now approve this", "end_turn")
        ProcessRun.objects.filter(pk=row.pk).update(awaiting_user_input=True)

    on_tick({2: answered_and_blocked})

    reply = wait(session, timeout=2.0, stop_when_blocked=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 5


def test_a_block_that_appears_later_still_ends_the_wait(project, on_tick):
    """The flag is not only about a session already blocked when the wait
    starts — the interesting case is the turn that runs, then asks."""
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value)

    on_tick({2: lambda: ProcessRun.objects.filter(pk=row.pk).update(awaiting_user_input=True)})

    reply = wait(session, timeout=2.0, stop_when_blocked=True)

    assert reply["outcome"] == "awaiting_user_input"


def test_the_degrade_wrapper_forwards_the_flag(project):
    """``wait_for_reply_or_degrade`` is what the commands actually call; a flag
    stopping at the wrapper is a flag that does nothing."""
    from twicc.cli._wait_reply import wait_for_reply_or_degrade

    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    reply = wait_for_reply_or_degrade(
        session.id, since_line_num=0, timeout=2.0, want_text=True,
        stop_when_blocked=True,
    )

    assert reply["outcome"] == "awaiting_user_input"


@pytest.mark.django_db
def test_send_message_forwards_the_blocked_flag(monkeypatch):
    """A flag that stops at the command is a flag that does nothing — and the
    loop's own tests cannot see it, since they call the loop directly."""
    seen = _run_send_message(monkeypatch, {
        "session_id": "cursor-wiring-session", "provider": "claude_code",
        "project_id": "cursor-wiring-project", "last_line": 3,
    }, wait_blocked=True)

    assert seen["stop_when_blocked"] is True


@pytest.mark.django_db
def test_create_session_forwards_the_blocked_flag(monkeypatch, tmp_path):
    """Same wiring, the other command. Both were unasserted, and both mutants
    survived the whole file."""
    from twicc.cli import _wait_reply as wait_reply_module

    seen: dict = {}

    def _probe(session_id, *, since_line_num, timeout, want_text, stop_when_blocked):
        seen["stop_when_blocked"] = stop_when_blocked
        return {"outcome": REPLIED}

    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.polling import PollOutcome

    class _Sub:
        request_uuid = "req-create-blocked"

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: _Sub())
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: PollOutcome(
        "created", {"session_id": "s", "provider": "claude_code", "project_id": "p"}, True,
    ))
    monkeypatch.setattr(wait_reply_module, "wait_for_reply_or_degrade", _probe)

    result = run_cli("hello", "--project", str(tmp_path), "--wait-reply", "--wait-blocked")

    assert result.exit_code == 0, result.output
    assert seen["stop_when_blocked"] is True


# ---------------------------------------------------------------------------
# Waiting on several sessions at once
# ---------------------------------------------------------------------------
#
# One loop polls them all, and the single-session wait is its one-session
# case — so every test above exercises this code too, which is what made the
# extraction safe to do at all.


def wait_many(sessions_cursors, **kwargs):
    from twicc.cli._wait_reply import wait_for_replies

    kwargs.setdefault("timeout", 2.0)
    kwargs.setdefault("want_text", True)
    return wait_for_replies(sessions_cursors, **kwargs)


def test_each_session_gets_its_own_answer(project):
    """Cursors are per session, so are the answers: a shared one would let a
    chatty session's line close a quiet one's wait."""
    one = make_session(project, session_id="many-one")
    two = make_session(project, session_id="many-two")
    process(one, AgentState.ASSISTANT_TURN.value)
    process(two, AgentState.ASSISTANT_TURN.value)
    assistant(one, 5, "from one", "end_turn")
    assistant(two, 9, "from two", "end_turn")

    replies = wait_many({one.id: 0, two.id: 0})

    assert replies[one.id]["text"] == "from one"
    assert replies[two.id]["text"] == "from two"
    assert replies[one.id]["line_num"] == 5
    assert replies[two.id]["line_num"] == 9


def test_a_cursor_is_honoured_per_session(project):
    """The quiet session's answer sits below its own cursor: it must not be
    returned, even though the other session's cursor would have let it pass.
    """
    one = make_session(project, session_id="cur-one")
    two = make_session(project, session_id="cur-two")
    process(one, AgentState.ASSISTANT_TURN.value)
    process(two, AgentState.ASSISTANT_TURN.value)
    assistant(one, 5, "old news", "end_turn")
    assistant(two, 9, "fresh", "end_turn")

    replies = wait_many({one.id: 7, two.id: 0}, timeout=0.5)

    assert replies[one.id]["outcome"] == TIMEOUT
    assert replies[two.id]["outcome"] == REPLIED


def test_the_first_answer_ends_the_batch_when_asked(project):
    """The others are ``pending``, the word ``processes wait`` already uses:
    they did not fail, the wait simply stopped caring."""
    fast = make_session(project, session_id="first-fast")
    slow = make_session(project, session_id="first-slow")
    process(fast, AgentState.ASSISTANT_TURN.value)
    process(slow, AgentState.ASSISTANT_TURN.value)
    assistant(fast, 5, "here", "end_turn")

    replies = wait_many({fast.id: 0, slow.id: 0}, timeout=3.0, first=True)

    assert replies[fast.id]["outcome"] == REPLIED
    assert replies[slow.id]["outcome"] == "pending"


def test_wait_all_waits_for_the_slow_one(project):
    """The default. Without it the batch would return on the fast session and
    the caller would read the slow one's silence as an answer."""
    fast = make_session(project, session_id="all-fast")
    slow = make_session(project, session_id="all-slow")
    process(fast, AgentState.ASSISTANT_TURN.value)
    process(slow, AgentState.ASSISTANT_TURN.value)
    assistant(fast, 5, "here", "end_turn")

    replies = wait_many({fast.id: 0, slow.id: 0}, timeout=0.5)

    assert replies[fast.id]["outcome"] == REPLIED
    assert replies[slow.id]["outcome"] == TIMEOUT


def test_a_crashed_turn_does_not_end_a_first_batch(project):
    """``--wait-first`` asks for an answer, and a turn that ended without one
    has not given it.

    The pairing is what makes this deterministic: the crashed session concludes
    on its own, the working one never will. If an ending cut the batch, the
    worker would come back ``pending`` instead of running out its budget — the
    first one out would have decided for everyone.
    """
    crashed = make_session(project, session_id="crash-one")
    working = make_session(project, session_id="crash-two")
    process(working, AgentState.ASSISTANT_TURN.value)

    replies = wait_many({crashed.id: 0, working.id: 0}, timeout=1.5, first=True)

    assert replies[crashed.id]["outcome"] == ENDED
    assert replies[working.id]["outcome"] == TIMEOUT


def test_an_empty_batch_waits_for_nothing(project):
    assert wait_many({}) == {}


def test_an_answer_returns_at_once_not_at_the_deadline(project):
    """``if not waiting: return`` is the only normal exit, and it is new.

    Every other test here uses a timeout of a few seconds, so "returned on the
    answer" and "returned when the budget ran out" look identical to them —
    a mutant that waits out the deadline before returning keeps the suite
    green and merely makes it slower. In production that is the right answer
    delivered five minutes late, with the MCP client long gone.
    """
    session = make_session(project)
    process(session, AgentState.ASSISTANT_TURN.value)
    assistant(session, 5, "immediate", "end_turn")

    # Wall clock, not ``waited_seconds``: that field is stamped when the block
    # is *built*, so a mutant that builds it at once and then loops to the
    # deadline leaves it at 0.0. The first version of this test asserted the
    # field and let its own mutant through.
    started = time.monotonic()
    reply = wait(session, timeout=30.0)
    elapsed = time.monotonic() - started

    assert reply["outcome"] == REPLIED
    assert elapsed < 1.0


def test_a_batch_returns_when_the_last_one_answers(project):
    """The same guard for several: the loop must stop when the set empties,
    not when the budget does."""
    one = make_session(project, session_id="quick-one")
    two = make_session(project, session_id="quick-two")
    process(one, AgentState.ASSISTANT_TURN.value)
    process(two, AgentState.ASSISTANT_TURN.value)
    assistant(one, 5, "a", "end_turn")
    assistant(two, 5, "b", "end_turn")

    started = time.monotonic()
    replies = wait_many({one.id: 0, two.id: 0}, timeout=30.0)
    elapsed = time.monotonic() - started

    assert {r["outcome"] for r in replies.values()} == {REPLIED}
    assert elapsed < 1.0


def test_a_block_ends_a_first_batch_when_blocking_was_asked_for(project):
    """``--wait-first`` fires on either ending the caller asked for, and the
    tuple that decides it had only its answer half covered."""
    blocked = make_session(project, session_id="fb-blocked")
    other = make_session(project, session_id="fb-other")
    process(blocked, AgentState.ASSISTANT_TURN.value, awaiting=True)
    process(other, AgentState.ASSISTANT_TURN.value)

    replies = wait_many(
        {blocked.id: 0, other.id: 0}, timeout=3.0, first=True, stop_when_blocked=True,
    )

    assert replies[blocked.id]["outcome"] == "awaiting_user_input"
    assert replies[other.id]["outcome"] == "pending"


def test_a_cut_wait_still_reports_what_it_had_seen(project):
    """``pending`` is not "nothing happened": the session may have spoken
    without closing its turn, and reporting that line is what lets a caller
    see how far it got.

    The order in the map matters and is the point: the slow session is stepped
    first, so it has been seen once before the fast one's answer cuts the
    batch. Cut before ever being polled, it would legitimately report nothing.
    """
    fast = make_session(project, session_id="cut-fast")
    slow = make_session(project, session_id="cut-slow")
    process(fast, AgentState.ASSISTANT_TURN.value)
    process(slow, AgentState.ASSISTANT_TURN.value)
    assistant(slow, 4, "thinking out loud", "tool_use")
    assistant(fast, 5, "done", "end_turn")

    replies = wait_many({slow.id: 0, fast.id: 0}, timeout=3.0, first=True)

    assert replies[slow.id]["outcome"] == "pending"
    assert replies[slow.id]["line_num"] == 4


@pytest.mark.django_db
@pytest.mark.parametrize("given, expected", [(None, 300.0), (12.0, 12.0)])
def test_send_message_hands_the_budget_to_the_wait(monkeypatch, given, expected):
    """Both halves: the documented default, and an explicit value.

    The `--reply-timeout` → `--wait-timeout` rename touched this line on both
    singular commands, and nothing asserted either end of it — a rename that
    forgot one of them would have kept the suite green.
    """
    seen = _run_send_message(monkeypatch, {
        "session_id": "cursor-wiring-session", "provider": "claude_code",
        "project_id": "cursor-wiring-project", "last_line": 3,
    }, wait_timeout=given)

    assert seen["timeout"] == expected


@pytest.mark.django_db
def test_send_message_drops_the_text_when_asked(monkeypatch):
    seen = _run_send_message(monkeypatch, {
        "session_id": "cursor-wiring-session", "provider": "claude_code",
        "project_id": "cursor-wiring-project", "last_line": 3,
    }, no_reply_text=True)

    assert seen["want_text"] is False


@pytest.mark.django_db
@pytest.mark.parametrize("args, key, expected", [
    ([], "timeout", 300.0),
    (["--wait-timeout", "12"], "timeout", 12.0),
    (["--no-reply-text"], "want_text", False),
])
def test_create_session_hands_its_wait_arguments_over(monkeypatch, tmp_path, args, key, expected):
    """Same wiring on the other singular command, and its cursor with it:
    a new session's transcript is empty, so the wait must start at 0."""
    from twicc.cli import _wait_reply as wait_reply_module
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.polling import PollOutcome

    seen: dict = {}

    def _probe(session_id, *, since_line_num, timeout, want_text, stop_when_blocked):
        seen.update(since_line_num=since_line_num, timeout=timeout, want_text=want_text)
        return {"outcome": REPLIED}

    class _Sub:
        request_uuid = "req-budget"

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: _Sub())
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: PollOutcome(
        "created", {"session_id": "s", "provider": "claude_code", "project_id": "p"}, True,
    ))
    monkeypatch.setattr(wait_reply_module, "wait_for_reply_or_degrade", _probe)

    result = run_cli("hello", "--project", str(tmp_path), "--wait-reply", *args)

    assert result.exit_code == 0, result.output
    assert seen[key] == expected
    assert seen["since_line_num"] == 0


def test_a_degraded_batch_and_a_degraded_single_say_the_same_thing(project):
    """Both go through one helper, so an exception with no message reads the
    same on each — a copy would drop the guard and print a trailing colon."""
    from twicc.cli._wait_reply import degraded_reply, wait_for_reply_or_degrade

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    session = make_session(project)
    import twicc.cli._wait_reply as module
    original = module.wait_for_reply
    module.wait_for_reply = boom
    try:
        single = wait_for_reply_or_degrade(
            session.id, since_line_num=3, timeout=1.0, want_text=True,
        )
    finally:
        module.wait_for_reply = original

    batch = degraded_reply(3, single["waited_seconds"], KeyboardInterrupt())

    assert single["error"] == "KeyboardInterrupt"
    assert single == batch


def test_a_backend_that_vanishes_keeps_the_answers_already_in_hand(project, monkeypatch):
    """The batch stops, it does not forget.

    A session that answered before the backend went away has answered; folding
    those results into the give-up throws away work the caller can use.

    This test failed three times before it passed, and each time the code was
    the thing at fault: a mutation harness killed by a timeout had left
    ``return results | {…}`` as a bare ``return {…}`` in the source. The
    failure was real and the test was right — worth remembering the next time
    a new test looks broken.
    """
    from twicc.cli._wait_reply import _SessionWait, wait_for_replies

    answered = make_session(project, session_id="gone-answered")
    pending_one = make_session(project, session_id="gone-pending")

    def fake_step(self):
        return {"outcome": REPLIED} if self.session_id == answered.id else None

    monkeypatch.setattr(_SessionWait, "step", fake_step)

    calls = {"n": 0}

    def vanishing():
        calls["n"] += 1
        # The pid read, then one tick, then gone.
        return type("I", (), {"pid": TWICC_PID})() if calls["n"] <= 2 else None

    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", vanishing)

    replies = wait_for_replies(
        {answered.id: 0, pending_one.id: 0}, timeout=3.0, want_text=True,
    )

    assert replies[answered.id]["outcome"] == REPLIED
    assert replies[pending_one.id]["outcome"] == BACKEND_GONE


def test_a_refused_turn_does_not_end_a_first_batch(project):
    """Four documents say "crashed **or was refused**" never ends a batch, and
    only the crash half was covered. A provider refusal is not an answer."""
    refused = make_session(project, session_id="refused-one")
    working = make_session(project, session_id="refused-two")
    process(refused, AgentState.ASSISTANT_TURN.value)
    process(working, AgentState.ASSISTANT_TURN.value)
    api_error(refused, 4, terminal=True)

    replies = wait_many({refused.id: 0, working.id: 0}, timeout=1.5, first=True)

    assert replies[refused.id]["outcome"] == PROVIDER_ERROR
    assert replies[working.id]["outcome"] == TIMEOUT


def test_the_budget_is_one_for_the_batch_not_one_each(project):
    """The headline claim of the batch wait, asserted in the help, the skill,
    SKILLS-AND-CLI and the MCP schema — and timed nowhere.

    Three sessions that never answer must exhaust ONE budget between them, not
    three in sequence.
    """
    ids = {}
    for name in ("budget-a", "budget-b", "budget-c"):
        session = make_session(project, session_id=name)
        process(session, AgentState.ASSISTANT_TURN.value)
        ids[session.id] = 0

    started = time.monotonic()
    replies = wait_many(ids, timeout=0.6)
    elapsed = time.monotonic() - started

    assert {r["outcome"] for r in replies.values()} == {TIMEOUT}
    assert elapsed < 1.5


def test_a_block_that_was_cleared_is_still_reported_on_a_timeout(project, on_tick):
    """The flag is sticky, and that is the whole point of it.

    A human was asked for during the turn; that the click came before the
    deadline does not erase it. The existing test pins the opposite half —
    that it is *not* reported on an answer.
    """
    session = make_session(project)
    row = process(session, AgentState.ASSISTANT_TURN.value, awaiting=True)

    on_tick({2: lambda: ProcessRun.objects.filter(pk=row.pk).update(awaiting_user_input=False)})

    reply = wait(session, timeout=0.5)

    assert reply["outcome"] == TIMEOUT
    assert reply["awaiting_user_input"] is True
