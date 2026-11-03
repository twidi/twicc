"""Wait for a confirmed logical answer, with transcript fallback for unmanaged history.

Managed intervals select their exact last native final after durable confirmation.
Open intervals, unresolved references, and watcher lag never imply success.
Exceptional outcomes remain terminal after the process row disappears. A pending
request for the selected current interval takes priority over intermediate text.

Legacy history retains its strict line cursor, provider-error handling, watcher
flush barrier, and stopped-agent backstop. A record owned by any managed interval
never becomes a legacy final, including intervals consumed by earlier waits.

``--wait-background`` ignores an answer read while background work runs. Only
this explicit ignore permits continuation into a later confirmed interval. An
ignored answer never revives merely because background work ends. Timeouts carry
its text and the current background snapshot. Pending requests carry no ignored
answer. Each wait keeps source scanning separate from logical consumption.
"""

from __future__ import annotations


import os
import time
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from twicc.core.services.logical_turn_answers import WaitProgress


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
AWAITING = "awaiting_user_input"   # a pending request: the agent is blocked on a human
PENDING = "pending"                # batch only: --wait-first ended the wait before this one concluded
TIMEOUT = "timeout"                # the deadline passed, the session keeps running
BACKEND_GONE = "backend_gone"      # TwiCC stopped or restarted: nothing can be observed
WAIT_FAILED = "wait_failed"        # the wait itself broke; the session is unaffected

# ``--wait-background`` help, shared by the five commands that wait. The three
# that send append " Requires --wait-reply."; the two wait commands use it as
# is. Kept here, beside the rule it describes, so the five cannot drift.
WAIT_BACKGROUND_HELP = (
    "A final message read while background work runs behind the agent "
    "(`background_work_in_progress` not null: a subagent, background shell, "
    "Monitor, scheduled wake-up or goal) does not count; the first confirmed "
    "logical answer read once that work has ended is the answer, so a wait started or "
    "resumed after the work ended counts the final messages already written. "
    "Once a final message was ignored, an idle agent keeps the wait open until "
    "the next one comes, however late (a dead agent ends it, 'ended'). Claude "
    "Code reopens a turn when a background shell ends; Codex answers after a "
    "process ends only if the agent waited for it within its turn, so a "
    "process it left running on purpose brings no new answer and such a wait "
    "ends in 'timeout'. On 'timeout', the last ignored final message is "
    "returned with the current `background_work_in_progress`: to wait for the "
    "next answer, resume with `session <ID> wait-reply --from-line <its line_num> "
    "--wait-background`. A pending request still ends the wait at once and "
    "carries no ignored final message."
)


class _Activity(NamedTuple):
    """What the process side says about one session, read once per tick."""

    working: bool         # starting / assistant_turn, or blocked inside a turn
    awaiting: bool        # blocked on a pending request
    background: dict | None  # ``background_work_in_progress``; None when nothing runs
    alive: bool           # any state but dead: the agent can still write


def degraded_reply(cursor: int, waited: float, exc: BaseException) -> dict:
    """The block a wait hands back when the wait itself broke.

    Shared so the singular and the batch cannot drift: the empty-``str(exc)``
    guard below is easy to lose in a copy, and a ``KeyboardInterrupt`` would
    then read ``"KeyboardInterrupt: "`` on one side and ``"KeyboardInterrupt"``
    on the other, while both docs promise the same shape. The guard is
    conditional rather than a strip: a message may legitimately end in a
    colon, and trimming it would edit the provider's own words.
    """
    return _empty_reply(WAIT_FAILED, cursor, waited) | {
        "error": f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
    }


def default_wait_cursors(sessions) -> dict[str, int]:
    """The cursor a wait starts from when neither --from-line nor --since is given.

    Strictly after the session's last user message (0 when it has none): the
    answer to the last thing it was told is returned even if it came before
    the wait. A session whose compute is not current keeps its ``last_line``:
    its ``kind`` values may predate its newest lines (initial sync,
    compute-version bump, Codex legacy rollout), and the anchor would land on
    an older message — a missed answer, never a wrong one. One query for the
    ready ones, on ``idx_session_kind_line``.
    """
    from django.db.models import Max

    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.core.serializers import session_compute_ready

    cursors: dict[str, int] = {}
    ready: list[str] = []
    for session in sessions:
        if session_compute_ready(session):
            cursors[session.id] = 0
            ready.append(session.id)
        else:
            cursors[session.id] = session.last_line
    if ready:
        rows = (
            SessionItem.objects
            .filter(session_id__in=ready, kind=ItemKind.USER_MESSAGE)
            .values("session_id")
            .annotate(last=Max("line_num"))
            .values_list("session_id", "last")
        )
        for session_id, last in rows:
            cursors[session_id] = last
    return cursors


def wait_for_reply_or_degrade(
    session_id: str,
    *,
    since_line_num: int | None = None,
    timeout: float,
    want_text: bool,
    wait_background: bool = False,
    wait_token: str | None = None,
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
            want_text=want_text, wait_background=wait_background, wait_token=wait_token,
        )
    except BaseException as exc:  # noqa: BLE001 - deliberate, see above
        return degraded_reply(since_line_num, time.monotonic() - started, exc)


def _empty_reply(outcome: str, since_line_num: int, waited: float = 0.0) -> dict:
    """A ``reply`` block for an ending that never got to look at anything.

    Same keys as the loop's own builder, minus what needs a message. Shared so
    the two shapes cannot drift — the loop's builder belongs to a
    ``_SessionWait`` that may not exist yet when this is needed.
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


def _agent_activity(session_id: str, twicc_pid: int | None) -> _Activity:
    """Is it working, is it blocked on a click, and what runs behind it.

    ``background`` is the agent's ``background_work_in_progress`` — the same
    snapshot the ``process`` block exposes, ``None`` when nothing runs and
    always ``None`` for a dead agent.

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
            return _Activity(False, False, None, False)
        awaiting = bool(info.pending_requests)
        # The ``or awaiting`` mirrors the DB branch below rather than relying on
        # "a pending request implies ASSISTANT_TURN" holding forever: a request
        # blocks *inside* a running turn, so the agent is working either way.
        return _Activity(
            awaiting or info.state in working_states, awaiting,
            info.background_work_in_progress or None, True,
        )

    from twicc.cli._process_state import (
        AWAITING_VIRTUAL_STATE,
        DEAD_VIRTUAL_STATE,
        project_virtual_state,
    )
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
    # Same projection as ``serialize_compact_process``: a DEAD row may still
    # hold the last snapshot it was written with, and a dead agent runs nothing.
    background = (
        row.background_work_in_progress if virtual != DEAD_VIRTUAL_STATE else None
    ) or None
    return _Activity(working, awaiting, background, virtual != DEAD_VIRTUAL_STATE)


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


class _SessionWait:
    """Track one selected interval and separate reading from durable consumption.

    The batch and singular APIs share this state. ``progress()`` exposes the
    safe line cursor and the last consumed logical position for reconstruction.
    """

    def __init__(self, session_id: str, since_line_num: int | None = None, *, started: float,
                 twicc_pid, want_text: bool, wait_background: bool = False,
                 wait_token: str | None = None, after_turn_id: str | None = None):
        self.session_id = session_id
        if since_line_num is None:
            from twicc.core.services.logical_turns import input_for_token
            association = input_for_token(session_id=session_id, token=wait_token) if wait_token is not None else None
            since_line_num = association.admission_cursor if association is not None else 0
        self.since_line_num = since_line_num
        self.scanned_up_to = since_line_num
        self.started = started
        self.twicc_pid = twicc_pid
        self.want_text = want_text
        self.wait_background = wait_background
        # Read once and kept: ``provider`` and ``file_path`` never change, and
        # ``last_offset`` is refreshed below only where it is actually used.
        self.session = None
        self.stopped_since = None
        self.confirming = False
        self.last_message = None
        # ``wait_background`` only: the last final message ignored because
        # background work ran when it was read, and the latest snapshot. Both
        # stay ``None`` without the flag, so every ending keeps its shape.
        self.last_ignored = None
        self.ignored_legacy_up_to = since_line_num
        self.background = None
        self.wait_token = wait_token
        self.after_turn_id = after_turn_id
        self.consumed_up_to = since_line_num
        self.last_logical_turn_id = after_turn_id
        self.logical_turn_id = None
        self.empty_since = None
        self.empty_confirming = False
        self.empty_flush_ready = False
        from twicc.core.services.logical_turn_answers import NativeAnswerCache
        self.native_cache = NativeAnswerCache(session_id)

    def progress(self) -> WaitProgress:
        from twicc.core.services.logical_turn_answers import WaitProgress
        return WaitProgress(self.consumed_up_to, self.last_logical_turn_id)

    def _consume_turn(self, turn, turns, message=None):
        boundary = self.consumed_up_to
        own_lines = [line for ref in turn.final_refs for line in self.native_cache.lines(ref)]
        if own_lines:
            boundary = max(boundary, max(own_lines))
        if message is not None:
            boundary = max(boundary, message.line_num)
        later_lines = [line for later in turns if later.position > turn.position
                       for ref in ([later.first_ref] if later.first_ref else []) + later.final_refs
                       for line in self.native_cache.lines(ref)]
        if later_lines:
            boundary = min(boundary, max(self.consumed_up_to, min(later_lines) - 1))
        self.consumed_up_to = boundary
        self.last_logical_turn_id = str(turn.pk)

    def _legacy_is_owned(self, line, turns, activity):
        for index, turn in enumerate(turns):
            start = self.native_cache.lines(turn.first_ref)
            next_starts = [source_line for later in turns[index + 1:]
                           for source_line in self.native_cache.lines(later.first_ref)]
            if next_starts and line >= min(next_starts):
                continue
            final_lines = self.native_cache.lines(turn.final_refs[-1]) if turn.final_refs else []
            if (turn.outcome == 'normal' and final_lines and line > max(final_lines)
                    and not activity.alive):
                continue
            if start:
                if line >= min(start):
                    return True
            elif line > turn.source_floor:
                # A known-empty interval owns no final, but only the existing
                # flush barrier permits historical records beyond its floor.
                if turn.outcome == 'normal' and not turn.final_refs and not turn.first_ref:
                    if self.empty_flush_ready and not activity.alive:
                        continue
                return True
        return False

    def _legacy_precedes_turn(self, line, turn):
        if turn is None:
            return True
        start = self.native_cache.lines(turn.first_ref)
        if start:
            return line < min(start)
        if turn.outcome == 'normal' and not turn.first_ref and not turn.final_refs:
            # Known-empty coverage has its separate watcher-flush barrier.
            return True
        # An unresolved opening cannot order records newer than admission.
        return line <= turn.source_floor

    def _managed_step(self):
        """Select a durable interval before considering unmanaged transcript results."""
        from twicc.core.models import LogicalTurn
        from twicc.core.services.logical_turn_answers import resolve_logical_answer, select_logical_turn

        turns = list(LogicalTurn.objects.filter(session_id=self.session_id).order_by('position'))
        if not turns and self.wait_token is None and self.after_turn_id is None:
            return False, None
        self.native_cache.refresh()
        if self.native_cache.all_messages:
            self.scanned_up_to = max(self.scanned_up_to, max(self.native_cache.all_messages))
        try:
            if self.logical_turn_id is None:
                turn = select_logical_turn(session_id=self.session_id, cursor=self.since_line_num,
                    wait_token=self.wait_token, after_turn_id=self.after_turn_id, cache=self.native_cache)
                if turn is not None:
                    self.logical_turn_id = str(turn.pk)
                    self.empty_since = None
                    self.empty_confirming = False
                    self.empty_flush_ready = False
            else:
                turn = next((row for row in turns if str(row.pk) == self.logical_turn_id), None)
                if turn is None:
                    return True, self.build(WAIT_FAILED)
        except ValueError as exc:
            return True, self.build(WAIT_FAILED) | {'error': str(exc)}

        answer = resolve_logical_answer(turn, cursor=self.since_line_num, cache=self.native_cache) if turn else None
        if answer is not None and answer.status == 'invalidated':
            return True, self.build(WAIT_FAILED) | {'error': 'Logical turn source changed'}
        activity = _agent_activity(self.session_id, self.twicc_pid)
        self.background = activity.background
        empty_history = any(
            row.outcome == 'normal' and not row.first_ref and not row.final_refs
            and any(line > max(self.since_line_num, row.source_floor)
                    and not any(boundary <= line for later in turns if later.position > row.position
                                for boundary in self.native_cache.lines(later.first_ref))
                    for line in self.native_cache.all_messages)
            for row in turns)
        if empty_history:
            if self.empty_since is None:
                self.empty_since = time.monotonic()
            if time.monotonic() - self.empty_since >= AGENT_FLUSH_SECONDS and self.session is not None:
                self.session.refresh_from_db(fields=['last_offset'])
                if _watcher_is_behind(self.session):
                    self.empty_confirming = False
                elif self.empty_confirming:
                    self.empty_flush_ready = True
                else:
                    self.empty_confirming = True

        # Keep historical records before managed coverage. Consumed intervals
        # still participate in ownership, so their finals never become legacy.
        unowned_messages = [message for line, message in sorted(self.native_cache.all_messages.items())
                            if line > max(self.since_line_num, self.ignored_legacy_up_to)
                            and not self._legacy_is_owned(line, turns, activity)
                            and self._legacy_precedes_turn(line, turn)]
        legacy = [message for message in unowned_messages if message.is_final is True]
        if legacy and self.wait_token is None:
            if self.wait_background and activity.background is not None:
                self.last_ignored = legacy[-1]
                self.ignored_legacy_up_to = max(self.ignored_legacy_up_to, legacy[-1].line_num)
            else:
                self.consumed_up_to = max(self.consumed_up_to, legacy[0].line_num)
                return True, self.build(REPLIED, legacy[0])
        if turn is None:
            if unowned_messages:
                self.last_message = unowned_messages[-1]
            errors = [(line, text) for line, text in sorted(self.native_cache.errors.items())
                      if line > self.since_line_num and not self._legacy_is_owned(line, turns, activity)]
            if errors:
                line, text = errors[0]
                self.consumed_up_to = max(self.consumed_up_to, line)
                block = self.build(PROVIDER_ERROR, line_num=line)
                if self.want_text:
                    block['text'] = text
                return True, block
            if activity.awaiting:
                return True, self.build(AWAITING)
            return True, self._stopped_backstop(activity)
        observed_messages = [self.native_cache.answer(ref, cursor=self.since_line_num)
                             for ref in turn.final_refs]
        observed_messages = [message for message in observed_messages if message is not None]
        if observed_messages:
            self.last_message = observed_messages[-1]
        if answer.status == 'pending':
            if activity.awaiting and turns and turn.pk == turns[-1].pk:
                return True, self.build(AWAITING)
            return True, None
        if answer.status == 'ended' and turn.outcome == 'normal' and not turn.final_refs and not turn.first_ref:
            if empty_history and not self.empty_flush_ready:
                return True, None
        if answer.status == 'provider_error':
            later_starts = [line for later in turns if later.position > turn.position
                            for line in self.native_cache.lines(later.first_ref)]
            errors = [(line, text) for line, text in sorted(self.native_cache.errors.items())
                      if line > max(self.since_line_num, turn.source_floor)
                      and (not later_starts or line < min(later_starts))]
            self._consume_turn(turn, turns)
            if errors:
                line, text = errors[-1]
                block = self.build(PROVIDER_ERROR, line_num=line)
                if self.want_text:
                    block['text'] = text
                return True, block
        if answer.status == 'replied' and self.wait_background and activity.background is not None:
            self.last_ignored = answer.message
            self._consume_turn(turn, turns, answer.message)
            self.after_turn_id = str(turn.pk)
            self.logical_turn_id = None
            self.wait_token = None
            return True, None
        self._consume_turn(turn, turns, answer.message)
        if answer.status == 'ended' and self.last_ignored is not None and turn.outcome == 'normal':
            self.after_turn_id = str(turn.pk)
            self.logical_turn_id = None
            self.wait_token = None
            if activity.awaiting:
                return True, self.build(AWAITING)
            if activity.alive:
                return True, None
        return True, self.build(answer.status, answer.message)

    def build(self, outcome: str, message=None, *, line_num=None) -> dict:
        block = {
            "outcome": outcome,
            "line_num": message.line_num if message is not None else line_num,
            "is_final": message.is_final if message is not None else None,
            "since_line_num": self.since_line_num,
            "waited_seconds": round(time.monotonic() - self.started, 1),
        }
        if self.want_text and message is not None:
            block["text"] = message.text
        return block

    def build_cut_short(self, outcome: str) -> dict:
        """The block of a wait stopped from outside: ``timeout`` or ``pending``.

        The last thing the session said, as always — except that under
        ``wait_background`` an answer may have been ignored because work ran
        behind it. The caller gets that answer and what still ran, so a
        timeout on a long background job does not throw the answer away. The
        cursor is untouched: resuming from ``since_line_num`` reads that answer
        again and judges it on the snapshot of that new reading; resuming from
        its ``line_num`` waits for the next one.
        """
        if self.last_ignored is None:
            return self.build(outcome, self.last_message)
        return self.build(outcome, self.last_ignored) | {
            "background_work_in_progress": self.background,
        }

    def step(self) -> dict | None:
        """One poll for this session. Returns its ending, or ``None`` to go on."""
        from twicc.core.enums import ItemKind
        from twicc.core.models import Session, SessionItem
        from twicc.providers.helpers import get_provider_helpers

        # The row appears when the watcher first sees the JSONL file, which is
        # *after* ``create-session`` returns — until then there is nothing to
        # scan, and that window is "still starting", not "nothing".
        if self.session is None:
            self.session = (
                Session.objects
                .filter(id=self.session_id)
                .only("id", "provider", "file_path", "last_offset")
                .first()
            )

        handled, block = self._managed_step()
        if handled:
            return block

        activity = None
        if self.session is not None:
            new_items = list(
                SessionItem.objects
                .filter(session_id=self.session_id, line_num__gt=self.scanned_up_to,
                        kind__in=[ItemKind.ASSISTANT_MESSAGE, ItemKind.API_ERROR])
                .order_by("line_num")
            )
            if new_items:
                self.scanned_up_to = new_items[-1].line_num
                helpers = get_provider_helpers(self.session.provider)

                # An answer is looked for **first**, on purpose: a turn that hit
                # a retryable error and then recovered writes both, and the
                # error sits at the lower line number. Reporting it would throw
                # away the reply sitting right below it.
                messages = helpers.get_indexable_messages(
                    [item for item in new_items if item.kind == ItemKind.ASSISTANT_MESSAGE]
                )
                finals = [message for message in messages if message.is_final is True]
                if finals and self.wait_background:
                    # Read AFTER the scan, in the same tick: a final message
                    # written just after the work ended meets a snapshot that
                    # already says so. One that meets running work is
                    # ignored — the cursor has moved past it all the same.
                    activity = _agent_activity(self.session_id, self.twicc_pid)
                    self.background = activity.background
                    if activity.background is not None:
                        self.last_ignored = finals[-1]
                        finals = []
                if finals:
                    # The *first* final message past the cursor is the answer to
                    # what the caller just sent. A later one would belong to a
                    # turn the caller did not trigger.
                    self.consumed_up_to = max(self.consumed_up_to, finals[0].line_num)
                    return self.build(REPLIED, finals[0])
                if messages:
                    self.last_message = messages[-1]

                for item in new_items:
                    if item.kind != ItemKind.API_ERROR:
                        continue
                    from twicc.core.content_storage.readers import load_current

                    load_current([item])
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
                    block = self.build(PROVIDER_ERROR, line_num=item.line_num)
                    if self.want_text:
                        block["text"] = _error_text(parsed, item, helpers)
                    return block

        if activity is None:
            activity = _agent_activity(self.session_id, self.twicc_pid)
            self.background = activity.background

        if activity.awaiting:
            # Checked AFTER the transcript scan above, so a turn that both
            # answered and then blocked reports the answer: an arrived reply is
            # always the better ending.
            return self.build(AWAITING)

        return self._stopped_backstop(activity)

    def _stopped_backstop(self, activity):
        if activity.working or (self.wait_background and (
            activity.background is not None
            or (self.last_ignored is not None and activity.alive)
        )):
            # A fresh turn (a cron, a wake-up, a subagent finishing) restarts
            # the count from zero: whatever was being waited out no longer
            # describes the session. Under ``wait_background``, two more
            # things keep an idle agent's wait open: work running behind it (a
            # shell left running in ``user_turn``), and — once a final message
            # was ignored — the next final message still owed, however long
            # it takes to come (Claude Code reopens the turn only on the
            # model's first output, seconds later). Only a new final message,
            # a pending request, a provider error, the agent dying or the
            # deadline end that wait.
            self.stopped_since = None
            self.confirming = False
            return None

        if self.stopped_since is None:
            self.stopped_since = time.monotonic()
        stopped_for = time.monotonic() - self.stopped_since

        if self.session is not None:
            # Only now are ``last_offset`` and the file stat worth their cost:
            # while the agent works neither can end the wait, and a session
            # running for an hour would pay 14 400 of each for nothing.
            if stopped_for >= AGENT_FLUSH_SECONDS:
                self.session.refresh_from_db(fields=["last_offset"])
                if not _watcher_is_behind(self.session):
                    if not self.confirming:
                        # The scan above ran before this check, and the watcher
                        # commits items and ``last_offset`` in one transaction:
                        # a commit landing between the two would leave an answer
                        # in the DB, unscanned, while the offset already says
                        # "fully indexed". One more tick re-scans before
                        # concluding.
                        self.confirming = True
                    else:
                        # Stopped long enough to have flushed, everything it
                        # wrote is indexed, and a scan since then found no
                        # answer: none is coming. A crash, an interruption, or a
                        # closing message whose text was empty (extraction drops
                        # those). Under ``wait_background`` only a dead agent
                        # gets here with an ignored final message, which it
                        # carries: nothing more will come after it.
                        return self.build(ENDED, self.last_ignored or self.last_message)
        elif (
            stopped_for >= AGENT_FLUSH_SECONDS
            and time.monotonic() - self.started >= SESSION_ROW_GRACE_SECONDS
        ):
            # No row to check against, and long past the point one should have
            # appeared: the agent died before writing anything. The flush window
            # applies here too — without it this branch would conclude the
            # moment the grace elapsed, however recently the agent stopped,
            # which is the one thing the window exists to prevent.
            return self.build(ENDED, self.last_message)

        return None


def wait_for_replies(
    cursors: dict,
    *,
    timeout: float,
    want_text: bool,
    first: bool = False,
    wait_background: bool = False,
    wait_tokens: dict[str, str] | None = None,
) -> dict:
    """Wait on one or more sessions; return one reply block per id.

    ``cursors`` maps session_id → the line each wait starts above, which for a
    broadcast is the ``last_line`` its own send returned. One loop polls them
    all every :data:`POLL_INTERVAL_SECONDS`, and one shared deadline covers the
    batch: the sessions are watched together, so the timeout is a wall-clock
    budget rather than N × timeout.

    ``first`` stops as soon as one session **answers** — or blocks on a human,
    which is the other way a wait concludes. A turn that crashed or was refused
    does not end the batch: the others may still answer, and the caller asked
    for an answer.
    Sessions still waiting when that happens get ``outcome: "pending"``.

    ``wait_background`` applies the rule of the module docstring to every
    session alike: a final message counts only when it is read while nothing
    runs behind that session.

    Never raises for a business outcome: every ending is an ``outcome`` value,
    because the send or the creation it follows already succeeded and must not
    be reported as a failure.
    """
    from twicc.cli._twicc_info import resolve_live_twicc

    if not cursors:
        return {}

    info = resolve_live_twicc()
    twicc_pid = info.pid if info is not None else None
    started = time.monotonic()
    deadline = started + timeout

    waiting = {
        session_id: _SessionWait(
            session_id, cursor, started=started, twicc_pid=twicc_pid,
            want_text=want_text, wait_background=wait_background,
            wait_token=(wait_tokens or {}).get(session_id),
        )
        for session_id, cursor in cursors.items()
    }
    results: dict = {}

    while True:
        # TwiCC still there? Read every tick, not once: a backend that stops or
        # restarts mid-wait would otherwise make every later poll see no
        # ``ProcessRun`` row, which reads exactly like a finished turn — the
        # loop would report ``ended`` for an agent that is alive and working.
        # Once per tick for the whole batch, not once per session.
        live = resolve_live_twicc()
        if live is None or live.pid != twicc_pid:
            # ``results |`` and not a bare dict: a session that answered before
            # the backend went away has answered, and folding it into the
            # give-up would throw away work the caller can use.
            return results | {
                session_id: waiter.build(BACKEND_GONE)
                for session_id, waiter in waiting.items()
            }

        for session_id, waiter in list(waiting.items()):
            block = waiter.step()
            if block is None:
                continue
            results[session_id] = block
            del waiting[session_id]
            if first and block["outcome"] in (REPLIED, AWAITING):
                return results | {
                    other_id: other.build_cut_short(PENDING)
                    for other_id, other in waiting.items()
                }

        if not waiting:
            return results

        if time.monotonic() >= deadline:
            return results | {
                session_id: waiter.build_cut_short(TIMEOUT)
                for session_id, waiter in waiting.items()
            }

        time.sleep(POLL_INTERVAL_SECONDS)


def wait_for_reply(
    session_id: str,
    *,
    since_line_num: int | None = None,
    timeout: float,
    want_text: bool,
    wait_background: bool = False,
    wait_token: str | None = None,
) -> dict:
    """Poll until the session answers, blocks on a human, the turn ends, or
    ``timeout`` elapses.

    The one-session case of :func:`wait_for_replies`, kept as its own name
    because that is what every singular command asks for.

    Returns the ``reply`` block of the command's JSON payload. ``text`` is
    present only when ``want_text`` and a message was found, never ``null``.
    """
    return wait_for_replies(
        {session_id: since_line_num},
        timeout=timeout, want_text=want_text, wait_background=wait_background,
        wait_tokens={session_id: wait_token} if wait_token is not None else None,
    )[session_id]
