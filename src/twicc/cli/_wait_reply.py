"""Block until a session answers. Used by ``create-session --wait-reply``.

The question "has it answered?" is read off the **transcript**, not off the
process state machine: a session parked in ``ASSISTANT_TURN`` by a Monitor, a
live subagent or a pending wake-up has already written its answer long before
it returns to ``USER_TURN`` (see ``ClaudeCodeAgent``'s ResultMessage handler).
Waiting on the state machine there costs hours; waiting on
``SessionItem.is_final`` costs nothing.

Whether the agent is still working is the **backstop**, never the signal.
``is_final`` says when an answer arrived; it never says when one will not.
A turn that crashes, that is interrupted, or that closes on an empty text
produces no ``is_final: true`` and would otherwise hang until the deadline.

"The agent stopped" is never enough on its own: it says so before its answer
is readable, twice over — it has the line left to flush, and the watcher has
it left to index. So that ending waits for both, one measured
(:data:`AGENT_FLUSH_SECONDS`) and one proven (:func:`_watcher_is_behind`).
It is the module's one ending that rests on a bet rather than a proof, sized
well past anything observed; the improbable cases belong to the deadline.

The caller owns the cursor: the wait only ever accepts a line **strictly
past** it. ``create-session`` passes ``0`` — a new session's transcript is
empty, so any final message is ours. The contract is written for a second
caller that does not exist yet: a ``send-message --wait-reply`` would pass
the session's ``last_line`` read right after the message was handed to the
agent, which is what keeps it from returning the previous turn's answer.
"""

from __future__ import annotations

import os
import time


POLL_INTERVAL_SECONDS = 0.25

# How long a session with no ``Session`` row yet is given before "the agent
# stopped" counts as the turn being over.
#
# That row is written by the watcher when it first sees the JSONL file, which
# is **after** ``create-session`` returns — and until it exists there is no
# ``file_path`` and no ``last_offset``, so :func:`_watcher_is_behind` cannot
# run and nothing can prove the transcript is complete. The only case that
# reaches this is an agent that died before writing anything at all; without
# a bound it would wait out the whole deadline.
#
# Measured rather than guessed: three ``create-session`` runs put the row at
# 0.97s, 0.96s and 0.97s after the command returned — stable enough to look
# like a scan interval, not a variable latency. Five times that leaves room
# for a loaded machine while still failing fast. Like
# :data:`AGENT_FLUSH_SECONDS`, this is a bet rather than a proof.
#
# ``send-message`` never reaches it: its cursor is the session's ``last_line``,
# a column of that very row, so having a cursor proves the row exists.
SESSION_ROW_GRACE_SECONDS = 5.0

# How long the agent has to stay stopped before "it stopped" is allowed to end
# the wait.
#
# It declares itself stopped **before** its answer is readable: the manager
# persists the state transition synchronously while the agent still has the
# JSONL line to flush. Measured over five sessions, the answer became visible
# 0.33, 0.34, 0.40, 0.38 and 0.33s after the agent read as stopped — always
# positive, and remarkably tight (70ms of spread) while the stop itself moved
# between 1.7s and 3.3s. So it is a fixed flush cost, not something that scales
# with the turn.
#
# Five seconds is twelve times the worst of those. It is a **bet**, not a
# proof — unlike the ``last_offset`` comparison, nothing here can prove the
# agent will not write again. An earlier version of this module called that
# comparison exact and dropped this window as redundant; a real run then
# reported ``ended`` for a session whose answer landed 0.4s later. The
# comparison only proves the watcher has read everything *on disk*, never that
# the agent has finished writing to it.
AGENT_FLUSH_SECONDS = 5.0

REPLIED = "replied"                # a final assistant message landed past the cursor
PROVIDER_ERROR = "provider_error"  # the provider refused the turn (quota, outage, ...)
ENDED = "ended"                    # the turn is over and no final message appeared
AWAITING = "awaiting_user_input"   # the agent is blocked on a human, and the caller asked to be told
PENDING = "pending"                # batch only: --wait-first ended the wait before this one concluded
TIMEOUT = "timeout"                # the deadline passed, the session keeps running
BACKEND_GONE = "backend_gone"      # TwiCC stopped or restarted: nothing can be observed
WAIT_FAILED = "wait_failed"        # the wait itself broke; the session is unaffected


def wait_for_reply_or_degrade(
    session_id: str,
    *,
    since_line_num: int,
    timeout: float,
    want_text: bool,
    stop_when_blocked: bool = False,
) -> dict:
    """:func:`wait_for_reply`, unable to take its caller's payload down with it.

    The session exists and is burning tokens by the time anyone waits on it,
    and its id is the one thing the command must always hand back. A wait that
    dies — a locked SQLite across the thousands of queries a long poll makes,
    a Ctrl-C, a shutdown — would otherwise raise past the caller and leave an
    orphan nobody can name. Every failure becomes an outcome instead, which is
    also what the command's "the exit code only says whether the session was
    created" contract requires.

    ``BaseException`` on purpose: ``KeyboardInterrupt`` is the likeliest of
    them from a shell, and the point is to print the id, not to be tidy.
    """
    started = time.monotonic()
    try:
        return wait_for_reply(
            session_id, since_line_num=since_line_num, timeout=timeout,
            want_text=want_text, stop_when_blocked=stop_when_blocked,
        )
    except BaseException as exc:  # noqa: BLE001 - deliberate, see above
        return _empty_reply(WAIT_FAILED, since_line_num, time.monotonic() - started) | {
            # Conditional rather than a strip: a message may legitimately end
            # in a colon, and trimming it would edit the provider's own words.
            "error": f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
        }


def _empty_reply(outcome: str, since_line_num: int, waited: float = 0.0) -> dict:
    """A ``reply`` block for an ending that never got to look at anything.

    Same keys as the loop's own builder, minus what needs a message. Shared so
    the two shapes cannot drift — the loop's builder is a closure and cannot
    be reached from here.
    """
    return {
        "outcome": outcome,
        "line_num": None,
        "is_final": None,
        "since_line_num": since_line_num,
        "waited_seconds": round(waited, 1),
    }


def _error_text(parsed: dict, item, helpers) -> str:
    """The message of a terminal provider error, for both providers.

    Codex puts it in a structured ``error.message`` and pops the payload the
    text extractors need, so extraction alone returns the empty string —
    dropping exactly what makes this outcome worth having (a quota tells you
    when to come back, an outage tells you to retry now). Claude's surfaced
    error instead carries the text as a plain content block, which extraction
    does read; hence the fallback rather than a choice between the two.
    """
    error = parsed.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
        return error["message"]
    return helpers.extract_indexable_text(item)


def _agent_activity(session_id: str, twicc_pid: int | None) -> tuple[bool, bool]:
    """``(is it working, is it blocked on a click)`` for one session.

    Read from the **in-memory agent registry** when this runs inside the
    backend — over MCP or ``/rpc/`` the command executes in the very process
    that owns the agents, so the answer is already there and a query would ask
    the DB for something we hold. A session that works for an hour polls 14 400
    times; that is 14 400 queries saved.

    From a plain shell the registry belongs to another process, so
    ``ProcessRun`` answers instead — the persisted mirror of the same state.
    It can lag the registry by one transition, never lead it, so the shell path
    is at worst slightly late and never wrong.
    """
    from twicc.agent.states import AgentState
    from twicc.cli._drop_request.transport import _in_backend

    working_states = (AgentState.STARTING, AgentState.ASSISTANT_TURN)

    if _in_backend():
        from twicc.agent.registry import get_agent_manager_registry

        info = get_agent_manager_registry().get_agent_info(session_id)
        if info is None:
            return False, False
        awaiting = bool(info.pending_requests)
        # The ``or awaiting`` mirrors the DB branch below rather than relying on
        # "a pending request implies ASSISTANT_TURN" holding forever: a request
        # blocks *inside* a running turn, so the agent is working either way.
        return awaiting or info.state in working_states, awaiting

    from twicc.cli._process_state import AWAITING_VIRTUAL_STATE, project_virtual_state
    from twicc.core.models import ProcessRun

    row = (
        ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id=session_id)
        .order_by("-started_at")
        .first()
    )
    virtual = project_virtual_state(row)
    # ``awaiting_user_input`` masks the state underneath, and that state is
    # always ``ASSISTANT_TURN``: a pending request blocks inside a running
    # turn. Reading it as "stopped" would end the wait on a session whose turn
    # is very much alive and one click from answering.
    awaiting = virtual == AWAITING_VIRTUAL_STATE
    working = awaiting or virtual in (
        AgentState.STARTING.value, AgentState.ASSISTANT_TURN.value,
    )
    return working, awaiting


def _watcher_is_behind(session) -> bool:
    """Is the JSONL on disk ahead of what the watcher has indexed?

    The two sources this loop reads do not land together. The manager persists
    the ``ProcessRun`` transition synchronously, while the answer reaches
    ``SessionItem`` through the watcher, whose write takes the process-wide
    ``_db_write_lock`` — the same lock the background-compute apply loop holds,
    a chunk at a time, for as long as a chunk takes. So "the agent went idle"
    can be visible while the message that closed its turn is not, and the lag
    has no useful upper bound: a timer cannot cover it.

    Comparing the file's size to ``Session.last_offset`` does, exactly and for
    free — that offset is where the watcher stopped reading. Unknown states
    (no path, file gone, unreadable) answer ``False``: the timer guard stays,
    and refusing to ever conclude would be worse than concluding late.
    """
    path = _session_jsonl_path(session)
    if path is None:
        return False
    try:
        return os.path.getsize(path) > session.last_offset
    except OSError:
        return False


def _session_jsonl_path(session):
    """Absolute path of a session's transcript, or ``None``.

    ``Session.file_path`` is stored **relative to its provider's root** — the
    watchers write ``str(relative)`` and every reader re-joins it. Calling
    ``getsize`` on it directly resolves against the caller's cwd, raises, and
    silently turns the check above into a constant ``False``; that is exactly
    what happened here until a review caught it, leaving a guard the whole
    design leans on as dead code in production.
    """
    from pathlib import Path

    from twicc.core.enums import Provider
    from twicc.provider_homes import claude_projects_dir, codex_sessions_dir

    if not session.file_path:
        return None
    path = Path(session.file_path)
    if path.is_absolute():
        return path
    roots = {
        Provider.CLAUDE_CODE.value: claude_projects_dir,
        Provider.CODEX.value: codex_sessions_dir,
    }
    root = roots.get(session.provider)
    return None if root is None else root() / path


def wait_for_reply(
    session_id: str,
    *,
    since_line_num: int,
    timeout: float,
    want_text: bool,
    stop_when_blocked: bool = False,
    stop_event=None,
) -> dict:
    """Poll until the session answers, the turn ends, or ``timeout`` elapses.

    ``stop_event`` lets a batch caller cut this wait short — ``--wait-first``
    has its answer and the rest no longer matter. It is checked once per tick,
    so a cut costs at most one poll interval.

    Returns the ``reply`` block of the command's JSON payload. ``text`` is
    present only when ``want_text`` and a message was found, never ``null``.

    Never raises for a business outcome: every ending is an ``outcome`` value,
    because the send or the creation it follows already succeeded and must not
    be reported as a failure.
    """
    from twicc.cli._twicc_info import resolve_live_twicc
    from twicc.core.enums import ItemKind
    from twicc.core.models import Session, SessionItem
    from twicc.providers.helpers import get_provider_helpers

    info = resolve_live_twicc()
    twicc_pid = info.pid if info is not None else None

    started = time.monotonic()
    deadline = started + timeout
    scanned_up_to = since_line_num
    session = None       # read once: provider and file_path never change
    stopped_since = None
    confirming = False
    last_message = None
    awaiting_user_input = False

    def build(outcome: str, message=None, *, line_num=None) -> dict:
        block = {
            "outcome": outcome,
            "line_num": message.line_num if message is not None else line_num,
            "is_final": message.is_final if message is not None else None,
            "since_line_num": since_line_num,
            "waited_seconds": round(time.monotonic() - started, 1),
        }
        if want_text and message is not None:
            block["text"] = message.text
        if awaiting_user_input and outcome not in (REPLIED, PROVIDER_ERROR):
            # Reported, never acted on: a human clicking would unblock the
            # agent and the answer would arrive, so giving up there would not
            # be the safe ending every other one is. It answers "why did my
            # wait end with nothing", so it is attached only to the endings
            # that have nothing — on a ``replied`` it would describe a block
            # that was cleared before the answer came.
            block["awaiting_user_input"] = True
        return block

    while True:
        if stop_event is not None and stop_event.is_set():
            return build(PENDING, last_message)

        # TwiCC still there? Read every tick, not once: a backend that stops or
        # restarts mid-wait would otherwise make every later poll see no
        # ``ProcessRun`` row, which reads exactly like a finished turn — the
        # loop would report ``ended`` for an agent that is alive and working.
        live = resolve_live_twicc()
        if live is None or live.pid != twicc_pid:
            return build(BACKEND_GONE)

        # Read once and kept: ``provider`` and ``file_path`` never change, and
        # ``last_offset`` is refreshed below only where it is actually used.
        # The row appears when the watcher first sees the JSONL file, which is
        # *after* ``create-session`` returns — until then there is nothing to
        # scan, and that window is "still starting", not "nothing".
        if session is None:
            session = (
                Session.objects
                .filter(id=session_id)
                .only("id", "provider", "file_path", "last_offset")
                .first()
            )

        if session is not None:
            new_items = list(
                SessionItem.objects
                .filter(session_id=session_id, line_num__gt=scanned_up_to,
                        kind__in=[ItemKind.ASSISTANT_MESSAGE, ItemKind.API_ERROR])
                .order_by("line_num")
            )
            if new_items:
                scanned_up_to = new_items[-1].line_num
                helpers = get_provider_helpers(session.provider)

                # An answer is looked for **first**, on purpose: a turn that hit
                # a retryable error and then recovered writes both, and the
                # error sits at the lower line number. Reporting it would throw
                # away the reply sitting right below it.
                messages = helpers.get_indexable_messages(
                    [item for item in new_items if item.kind == ItemKind.ASSISTANT_MESSAGE]
                )
                for message in messages:
                    # The *first* final message past the cursor is the answer to
                    # what the caller just sent. A later one would belong to a
                    # turn the caller did not trigger.
                    if message.is_final is True:
                        return build(REPLIED, message)
                if messages:
                    last_message = messages[-1]

                for item in new_items:
                    if item.kind != ItemKind.API_ERROR:
                        continue
                    parsed = helpers.parse_item_content(item)
                    if parsed is None or not parsed.get("isApiErrorMessage"):
                        # A retry the CLI is still working through
                        # (``type=system, subtype=api_error, retryAttempt=N/M``).
                        # It classifies as ``API_ERROR`` but the turn is alive —
                        # ``sessions_watcher`` makes the same distinction for the
                        # same reason. Only the surfaced error is terminal.
                        continue
                    # The provider refused the turn — a quota, an outage. Shaped
                    # like "no answer", but the caller's request was never the
                    # problem and retrying it now would fail the same way.
                    block = build(PROVIDER_ERROR, line_num=item.line_num)
                    if want_text:
                        block["text"] = _error_text(parsed, item, helpers)
                    return block

        working, awaiting = _agent_activity(session_id, twicc_pid)
        awaiting_user_input = awaiting_user_input or awaiting

        if stop_when_blocked and awaiting:
            # Checked AFTER the transcript scan above, so a turn that both
            # answered and then blocked reports the answer: an arrived reply is
            # always the better ending. Opt-in, because waiting through a block
            # is right whenever a human is there to clear it — and the case
            # where nobody is (a --hidden worker) is the case where blocking
            # cannot happen at all.
            return build(AWAITING)

        if working:
            # A fresh turn (a cron, a wake-up, a subagent finishing) restarts
            # the count from zero: whatever was being waited out no longer
            # describes the session.
            stopped_since = None
            confirming = False
        else:
            if stopped_since is None:
                stopped_since = time.monotonic()
            stopped_for = time.monotonic() - stopped_since

            if session is not None:
                # Only now are ``last_offset`` and the file stat worth their
                # cost: while the agent works neither can end the wait, and a
                # session running for an hour would pay 14 400 of each for
                # nothing.
                if stopped_for >= AGENT_FLUSH_SECONDS:
                    session.refresh_from_db(fields=["last_offset"])
                    if not _watcher_is_behind(session):
                        if not confirming:
                            # The scan above ran before this check, and the
                            # watcher commits items and ``last_offset`` in one
                            # transaction: a commit landing between the two
                            # would leave an answer in the DB, unscanned, while
                            # the offset already says "fully indexed". One more
                            # tick re-scans before concluding.
                            confirming = True
                        else:
                            # Stopped long enough to have flushed, everything it
                            # wrote is indexed, and a scan since then found no
                            # answer: none is coming. A crash, an interruption,
                            # or a closing message whose text was empty
                            # (extraction drops those).
                            return build(ENDED, last_message)
            elif (
                stopped_for >= AGENT_FLUSH_SECONDS
                and time.monotonic() - started >= SESSION_ROW_GRACE_SECONDS
            ):
                # No row to check against, and long past the point one should
                # have appeared: the agent died before writing anything. The
                # flush window applies here too — without it this branch would
                # conclude the moment the grace elapsed, however recently the
                # agent stopped, which is the one thing the window exists to
                # prevent.
                return build(ENDED, last_message)

        if time.monotonic() >= deadline:
            return build(TIMEOUT, last_message)

        time.sleep(POLL_INTERVAL_SECONDS)


def wait_for_replies(
    cursors: dict,
    *,
    timeout: float,
    want_text: bool,
    stop_when_blocked: bool = False,
    first: bool = False,
) -> dict:
    """Wait on several sessions at once; return one reply block per id.

    ``cursors`` maps session_id → the line each wait starts above, which for a
    broadcast is the ``last_line`` its own send returned. One shared deadline
    covers the whole batch, as in ``processes wait``: the sessions are waited
    on in parallel, so it is a wall-clock budget rather than N × timeout.

    ``first`` stops as soon as one session **answers** — or blocks on a human,
    when that was asked for. A turn that crashed or was refused does not end
    the batch: the others may still answer, and the caller asked for an answer.
    Sessions still waiting when that happens get ``outcome: "pending"``, the
    word ``processes wait`` already uses for the same situation.

    **One thread per session**, rather than one loop polling all of them. The
    single-session loop has been through six review rounds and its delicate
    parts — the flush window, the watcher-lag check, the confirming pass — are
    exactly what a mechanical extraction would break; this keeps them under
    the tests that already cover them. The cost is N pollers instead of one,
    which for a broadcast of a few dozen is a small indexed SELECT each, four
    times a second.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not cursors:
        return {}

    from django.db import connection

    stop = threading.Event()

    def one(session_id: str, cursor: int) -> dict:
        try:
            return wait_for_reply(
                session_id, since_line_num=cursor, timeout=timeout,
                want_text=want_text, stop_when_blocked=stop_when_blocked,
                stop_event=stop if first else None,
            )
        finally:
            # Django opens a connection per thread; leaving them behind would
            # leak one file handle per recipient, per batch.
            connection.close()

    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(cursors)) as pool:
        futures = {
            pool.submit(one, sid, cursor): sid for sid, cursor in cursors.items()
        }
        for future in as_completed(futures):
            sid = futures[future]
            results[sid] = future.result()
            if first and results[sid]["outcome"] in (REPLIED, AWAITING):
                stop.set()
    return results
