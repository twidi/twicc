"""``--wait-background``: a final message written while background work runs
does not count.

Without the flag, the first final message past the cursor ends the wait —
locked by ``test_wait_reply.py`` and re-checked here against a busy snapshot.
With it, the same loop runs, with one change: a final message read in a tick
whose ``background_work_in_progress`` snapshot (read after the scan) is not
null is ignored, and the first final message read with a null snapshot ends
the wait ``replied``. An idle agent with work behind it keeps the wait open;
a ``timeout`` (or a batch's ``pending``) carries the last ignored final
message and the snapshot. Pending requests and provider errors are untouched.
"""

from __future__ import annotations

import pytest

from twicc.agent.states import AgentState
from twicc.cli._wait_reply import (
    AWAITING,
    ENDED,
    PENDING,
    PROVIDER_ERROR,
    REPLIED,
    TIMEOUT,
    wait_for_replies,
)
from twicc.core.models import ProcessRun
from tests.test_wait_reply import (  # noqa: F401 — fixtures, used by name
    api_error,
    assistant,
    fast_loop,
    jsonl,
    live_twicc,
    make_session,
    on_tick,
    process,
    project,
    wait,
)


SHELL = {"subagents": 0, "shells": 1, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}


def with_background(row, background=SHELL):
    ProcessRun.objects.filter(pk=row.pk).update(background_work_in_progress=background)
    return row


def set_state(row, *, state=AgentState.USER_TURN.value, background=None):
    ProcessRun.objects.filter(pk=row.pk).update(state=state, background_work_in_progress=background)


# --- Without the flag, nothing changes ----------------------------------------


def test_without_the_flag_running_work_does_not_hold_the_answer(project):
    session = make_session(project)
    assistant(session, 5, "done", "end_turn")
    with_background(process(session, AgentState.USER_TURN.value))

    reply = wait(session)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 5
    assert "background_work_in_progress" not in reply


def test_without_the_flag_a_timeout_keeps_its_shape(project):
    session = make_session(project)
    assistant(session, 3, "on it", "tool_use")
    with_background(process(session, AgentState.ASSISTANT_TURN.value))

    reply = wait(session, timeout=0.1)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 3
    assert "background_work_in_progress" not in reply


# --- A final message read while work runs is ignored ----------------------------


@pytest.mark.parametrize("background", [
    SHELL,
    SHELL | {"shells": 0, "subagents": 2},
    SHELL | {"shells": 0, "monitors": 1},
    SHELL | {"shells": 0, "scheduled_wakeup_at": "2026-09-25T14:05:00+00:00"},
    SHELL | {"shells": 0, "goal": True},
], ids=["shell", "subagents", "monitor", "wakeup", "goal"])
def test_a_final_message_read_while_work_runs_does_not_count(project, jsonl, background):
    """Every kind of work counts. The deadline is three times the flush
    window: an idle agent with work behind it is not a finished turn."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "done", "end_turn")
    with_background(process(session, AgentState.USER_TURN.value), background)

    reply = wait(session, timeout=0.3, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 5
    assert reply["is_final"] is True
    assert reply["text"] == "done"
    assert reply["since_line_num"] == 0
    assert reply["background_work_in_progress"] == background


def test_the_first_final_message_after_the_work_ended_is_the_answer(project, on_tick, jsonl):
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "started the job", "end_turn")
    row = with_background(process(session, AgentState.USER_TURN.value))

    def work_ends_and_a_turn_starts():
        set_state(row, state=AgentState.ASSISTANT_TURN.value)

    def turn_closes():
        assistant(session, 9, "the conclusion", "end_turn")
        assistant(session, 11, "a later turn", "end_turn")
        set_state(row)

    on_tick({2: work_ends_and_a_turn_starts, 4: turn_closes})

    reply = wait(session, timeout=5.0, wait_background=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 9
    assert reply["text"] == "the conclusion"
    assert "background_work_in_progress" not in reply


def test_an_ignored_final_message_is_not_read_again(project, on_tick):
    """The cursor moves past it: the work ending later does not revive it."""
    session = make_session(project)
    assistant(session, 5, "started the job", "end_turn")
    row = with_background(process(session, AgentState.ASSISTANT_TURN.value))

    on_tick({3: lambda: set_state(row, state=AgentState.ASSISTANT_TURN.value)})

    reply = wait(session, timeout=0.3, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 5
    assert reply["background_work_in_progress"] is None


def test_a_final_message_written_right_after_the_work_ended_counts(project, on_tick):
    """The snapshot is read after the scan, in the same tick: the work ended
    before the line was written, so the tick that reads the line already sees
    no work."""
    session = make_session(project)
    row = with_background(process(session, AgentState.ASSISTANT_TURN.value))

    def work_ends_then_the_answer_lands():
        set_state(row, state=AgentState.ASSISTANT_TURN.value)
        assistant(session, 5, "the conclusion", "end_turn")

    on_tick({2: work_ends_then_the_answer_lands})

    reply = wait(session, timeout=5.0, wait_background=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 5


def test_a_timeout_carries_the_last_ignored_final_message(project, on_tick):
    session = make_session(project)
    assistant(session, 5, "first", "end_turn")
    with_background(process(session, AgentState.USER_TURN.value))

    on_tick({2: lambda: assistant(session, 8, "second", "end_turn")})

    reply = wait(session, timeout=0.3, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 8
    assert reply["text"] == "second"
    assert reply["background_work_in_progress"] == SHELL


def test_a_timeout_honours_no_reply_text(project):
    session = make_session(project)
    assistant(session, 5, "done", "end_turn")
    with_background(process(session, AgentState.USER_TURN.value))

    reply = wait(session, timeout=0.2, want_text=False, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 5
    assert "text" not in reply
    assert reply["background_work_in_progress"] == SHELL


def test_a_timeout_with_nothing_ignored_keeps_its_shape(project):
    session = make_session(project)
    assistant(session, 3, "on it", "tool_use")
    with_background(process(session, AgentState.USER_TURN.value))

    reply = wait(session, timeout=0.2, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 3
    assert "background_work_in_progress" not in reply


# --- Endings the flag does not change ------------------------------------------


def test_a_pending_request_still_ends_the_wait_at_once(project):
    session = make_session(project)
    assistant(session, 5, "done", "end_turn")
    with_background(process(session, AgentState.ASSISTANT_TURN.value, awaiting=True))

    reply = wait(session, timeout=2.0, wait_background=True)

    assert reply["outcome"] == AWAITING
    assert reply["line_num"] is None
    assert reply["waited_seconds"] < 1.0


def test_a_provider_error_still_ends_the_wait(project):
    session = make_session(project)
    api_error(session, 13, terminal=True)
    with_background(process(session, AgentState.ASSISTANT_TURN.value))

    reply = wait(session, wait_background=True)

    assert reply["outcome"] == PROVIDER_ERROR
    assert reply["line_num"] == 13


@pytest.mark.parametrize("with_row", [False, True], ids=["no-row", "dead-row"])
def test_a_dead_agent_has_nothing_behind_it(project, jsonl, with_row):
    """No row and a DEAD row both project to ``dead``; a DEAD row's stale
    snapshot must not make a final message look ignored."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "done", "end_turn")
    if with_row:
        with_background(process(session, AgentState.DEAD.value))

    reply = wait(session, timeout=2.0, wait_background=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 5


def test_after_an_ignored_final_an_alive_idle_agent_runs_to_the_deadline(project, on_tick, jsonl):
    """The next final message is owed, however late: the idle backstop does
    not end the wait. The deadline here is six flush windows."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "done", "end_turn")
    row = with_background(process(session, AgentState.USER_TURN.value))

    on_tick({2: lambda: set_state(row)})

    reply = wait(session, timeout=0.6, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["line_num"] == 5
    assert reply["text"] == "done"
    assert reply["background_work_in_progress"] is None


def test_a_final_long_after_the_work_ended_is_the_answer(project, on_tick, jsonl):
    """Claude reopens the turn only on the model's first output: the answer
    may come well past the flush window (five ticks here), and still counts."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "started the job", "end_turn")
    row = with_background(process(session, AgentState.USER_TURN.value))

    on_tick({
        2: lambda: set_state(row),
        25: lambda: assistant(session, 9, "the conclusion", "end_turn"),
    })

    reply = wait(session, timeout=5.0, wait_background=True)

    assert reply["outcome"] == REPLIED
    assert reply["line_num"] == 9


def test_after_an_ignored_final_a_dead_agent_falls_to_the_backstop(project, on_tick, jsonl):
    """A dead agent answers nothing more: ``ended``, carrying the ignored
    final message."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "done", "end_turn")
    row = with_background(process(session, AgentState.USER_TURN.value))

    def dies():
        set_state(row, state=AgentState.DEAD.value)
        assistant(session, 7, "a last word", "tool_use")

    on_tick({2: dies})

    reply = wait(session, timeout=5.0, wait_background=True)

    assert reply["outcome"] == ENDED
    assert reply["line_num"] == 5
    assert reply["text"] == "done"


def test_with_nothing_ignored_the_idle_backstop_is_unchanged(project, jsonl):
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 3, "on it", "tool_use")
    process(session, AgentState.USER_TURN.value)

    reply = wait(session, timeout=5.0, wait_background=True)

    assert reply["outcome"] == ENDED
    assert reply["line_num"] == 3


# --- Inside the backend --------------------------------------------------------


def test_inside_the_backend_the_snapshot_comes_from_memory(project, monkeypatch):
    """Over MCP the agent registry answers, not ``ProcessRun``: here the row
    says nothing runs, and the registry's snapshot has to win."""
    from twicc.agent.registry import AgentManagerRegistry
    from twicc.cli._drop_request import transport

    session = make_session(project)
    assistant(session, 5, "done", "end_turn")
    process(session, AgentState.USER_TURN.value)

    live = type("Info", (), {"state": AgentState.USER_TURN, "pending_requests": (),
                             "background_work_in_progress": SHELL})()
    monkeypatch.setattr(transport, "_in_backend", lambda: True)
    monkeypatch.setattr(AgentManagerRegistry, "get_agent_info", lambda self, sid: live)

    reply = wait(session, timeout=0.3, wait_background=True)

    assert reply["outcome"] == TIMEOUT
    assert reply["background_work_in_progress"] == SHELL


# --- A batch applies the rule per session --------------------------------------


@pytest.fixture
def busy_and_done(project):
    """One session answered with a shell still running, one answered and idle."""
    busy = make_session(project, session_id="bg-busy")
    done = make_session(project, session_id="bg-done")
    assistant(busy, 5, "started the job", "end_turn")
    with_background(process(busy, AgentState.USER_TURN.value))
    assistant(done, 4, "all done", "end_turn")
    process(done, AgentState.USER_TURN.value)
    return busy, done


def test_each_session_of_a_batch_is_judged_on_its_own_work(busy_and_done):
    busy, done = busy_and_done

    replies = wait_for_replies(
        {busy.id: 0, done.id: 0}, timeout=0.5, want_text=True, wait_background=True,
    )

    assert replies[done.id]["outcome"] == REPLIED
    assert replies[done.id]["line_num"] == 4
    assert replies[busy.id]["outcome"] == TIMEOUT
    assert replies[busy.id]["line_num"] == 5
    assert replies[busy.id]["background_work_in_progress"] == SHELL


def test_wait_first_leaves_the_busy_one_pending_with_its_ignored_answer(busy_and_done):
    busy, done = busy_and_done

    replies = wait_for_replies(
        {busy.id: 0, done.id: 0}, timeout=2.0, want_text=True, first=True,
        wait_background=True,
    )

    assert replies[done.id]["outcome"] == REPLIED
    assert replies[busy.id]["outcome"] == PENDING
    assert replies[busy.id]["line_num"] == 5
    assert replies[busy.id]["text"] == "started the job"
    assert replies[busy.id]["background_work_in_progress"] == SHELL


def test_without_the_flag_a_batch_returns_both_answers_at_once(busy_and_done):
    busy, done = busy_and_done

    replies = wait_for_replies({busy.id: 0, done.id: 0}, timeout=2.0, want_text=True)

    assert {sid: r["outcome"] for sid, r in replies.items()} == {busy.id: REPLIED, done.id: REPLIED}


# --- The documented resume -----------------------------------------------------


def test_resuming_from_the_ignored_final_waits_for_the_next_answer(project, on_tick, jsonl):
    """What every document tells the caller after a ``timeout`` carrying an
    ignored final message: resume from its ``line_num``. The judgement is on
    the reading, so a resume from ``since_line_num`` once the work has ended
    counts that message; from its ``line_num``, only the next one counts."""
    session = make_session(project)
    jsonl(session, size=0)
    assistant(session, 5, "started the job", "end_turn")
    row = with_background(process(session, AgentState.USER_TURN.value))

    first = wait(session, timeout=0.2, wait_background=True)
    assert first["outcome"] == TIMEOUT
    assert first["line_num"] == 5

    set_state(row)  # the work has ended; the next answer is still to come

    from_since = wait(session, since_line_num=first["since_line_num"], timeout=2.0,
                      wait_background=True)
    assert from_since["outcome"] == REPLIED
    assert from_since["line_num"] == 5  # read with no work running: it counts now

    on_tick({3: lambda: assistant(session, 9, "the conclusion", "end_turn")})

    from_ignored = wait(session, since_line_num=first["line_num"], timeout=5.0,
                        wait_background=True)
    assert from_ignored["outcome"] == REPLIED
    assert from_ignored["line_num"] == 9
    assert from_ignored["text"] == "the conclusion"
