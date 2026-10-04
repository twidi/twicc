"""
When an automatic title is checked again during a session's life.

Pure functions, no I/O and no clock of their own: the caller passes the time, so
the live backend passes the check's wall clock, including closing and catch-up
requests without a new message. A replay supplies historical check times.

A check is due when TWO conditions hold together:

- at least ``CHECK_MIN_MESSAGES`` new relevant messages since the last check;
- at least ``CHECK_MIN_INTERVAL`` since that same check.

One alone never triggers anything: time without new messages changes nothing,
and a burst of messages is not worth a check until the interval has passed. The
rule is evaluated for new relevant messages and catch-up requests. Closing
requests use their separate message-count rule.

On real sessions (130 of 35 to 90 messages) this gives about 7 checks for a
45-message session, and 92% of the checks fire at the 6th new message.
"""
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import NamedTuple

from twicc.title_transcript import has_title_content

# New relevant messages since the last check.
CHECK_MIN_MESSAGES = 6
# Time since the last check.
CHECK_MIN_INTERVAL = timedelta(minutes=15)
# A closing check (stop, archive) needs this many new messages, whatever the time.
CLOSING_MIN_MESSAGES = 3

# ``/compact``, ``/context``: a command with no argument says nothing about the subject.
_BARE_SLASH_COMMAND = re.compile(r"/[\w-]+")


class TitleCheckState(NamedTuple):
    """What the last check left behind.

    ``count`` is the number of relevant messages it was based on (``None`` before
    the first check). ``at`` is when it ran. A failed attempt sets ``at`` only, so
    its retry waits the interval but still counts the messages already gathered.
    """
    count: int | None = None
    at: datetime | None = None


def is_relevant_message(text: str) -> bool:
    """Whether a user message counts toward the cadence (and nourishes a title)."""
    return has_title_content(text) and not _BARE_SLASH_COMMAND.fullmatch(text.strip())


def rebased(state: TitleCheckState, count: int) -> TitleCheckState:
    """The state to evaluate when ``count`` fell below the last check's count.

    A rewind or a compaction can remove messages. The messages gathered since are
    then counted from the lower number instead of waiting to climb back to the old
    one. The caller persists the result when it differs from ``state``, and calls
    this before :func:`title_check_due` and :func:`closing_check_due`.
    """
    if state.count is not None and count < state.count:
        return state._replace(count=count)
    return state


def title_check_due(state: TitleCheckState, count: int, now: datetime) -> bool:
    """Whether a check is due now.

    ``count`` is the number of relevant messages so far. ``now`` is the check's
    wall clock, including catch-up requests without a new message. Whether the title may be touched at all (a
    title the user validated is frozen) is the caller's business, not this rule's.
    """
    if count < 1:
        return False
    if state.at is not None and now - state.at < CHECK_MIN_INTERVAL:
        return False
    if state.count is None:
        return True   # the first title: nothing to wait for
    return count - state.count >= CHECK_MIN_MESSAGES


def closing_check_due(state: TitleCheckState, count: int) -> bool:
    """Whether a last check is worth running when the user stops or archives.

    The user is done, so the interval does not apply: only enough new messages
    to be worth a call.
    """
    return count - (state.count or 0) >= CLOSING_MIN_MESSAGES


def after_check(state: TitleCheckState, count: int, now: datetime, *, succeeded: bool) -> TitleCheckState:
    """The state once a check has run (the title kept or changed counts as success)."""
    if succeeded:
        return TitleCheckState(count, now)
    return state._replace(at=now)


class SimulatedChecks(NamedTuple):
    """Result of :func:`simulate_checks`: counts of relevant messages at each check."""
    checks: list[int]
    closing: int | None   # count at which a closing check would run, if any


def simulate_checks(messages: Iterable[tuple[datetime, str]]) -> SimulatedChecks:
    """Replay the rule over ``(time, text)`` messages, in order, with no waiting.

    Returns the relevant-message counts at which a check would have fired, the
    first one being the first title. ``closing`` is the final count when a closing
    check would also run at the end (a stop or an archive right after the last
    message), else ``None``. Every simulated check succeeds.
    """
    state = TitleCheckState()
    count = 0
    checks: list[int] = []
    for when, text in messages:
        if not is_relevant_message(text):
            continue
        count += 1
        if title_check_due(state, count, when):
            checks.append(count)
            state = after_check(state, count, when, succeeded=True)
    closing = count if checks and closing_check_due(state, count) else None
    return SimulatedChecks(checks, closing)


def relevant_texts(messages: Sequence[tuple[datetime, str]]) -> list[str]:
    """The texts that count, in order (what a title is built from)."""
    return [text for _, text in messages if is_relevant_message(text)]
