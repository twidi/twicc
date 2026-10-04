import itertools
from datetime import UTC, datetime, timedelta

from twicc.title_cadence import (
    CHECK_MIN_INTERVAL,
    CHECK_MIN_MESSAGES,
    CLOSING_MIN_MESSAGES,
    TitleCheckState,
    after_check,
    closing_check_due,
    is_relevant_message,
    rebased,
    relevant_texts,
    simulate_checks,
    title_check_due,
)

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def test_values():
    assert CHECK_MIN_MESSAGES == 6
    assert CHECK_MIN_INTERVAL == timedelta(minutes=15)
    assert CLOSING_MIN_MESSAGES == 3


def test_the_first_title_is_due_at_the_first_message():
    assert not title_check_due(TitleCheckState(), 0, T0)
    assert title_check_due(TitleCheckState(), 1, T0)


def test_both_conditions_are_needed():
    state = TitleCheckState(1, T0)
    # Enough messages, not enough time.
    assert not title_check_due(state, 7, T0 + 14 * MIN + timedelta(seconds=59))
    # Enough time, not enough messages.
    assert not title_check_due(state, 6, T0 + 15 * MIN)
    # Both, at the exact limits.
    assert title_check_due(state, 7, T0 + 15 * MIN)


def test_time_alone_never_triggers():
    state = TitleCheckState(10, T0)
    for new_messages in range(CHECK_MIN_MESSAGES):
        assert not title_check_due(state, 10 + new_messages, T0 + timedelta(days=30))


def test_a_burst_waits_for_the_interval_then_the_next_message_triggers():
    """12 messages in 10 minutes: nothing; the first one after 15 minutes: a check."""
    state = TitleCheckState(1, T0)
    for i in range(1, 13):
        assert not title_check_due(state, 1 + i, T0 + timedelta(seconds=50 * i))
    assert title_check_due(state, 14, T0 + 16 * MIN)


def test_a_failed_attempt_waits_the_interval_but_keeps_the_messages():
    state = TitleCheckState(1, T0)
    failed_at = T0 + 20 * MIN
    state = after_check(state, 8, failed_at, succeeded=False)
    assert state == TitleCheckState(1, failed_at)
    assert not title_check_due(state, 8, failed_at + 14 * MIN)
    assert title_check_due(state, 8, failed_at + 15 * MIN)   # no need for 6 more messages


def test_a_failed_first_title_retries_after_the_interval():
    state = after_check(TitleCheckState(), 1, T0, succeeded=False)
    assert state == TitleCheckState(None, T0)
    assert not title_check_due(state, 1, T0 + 10 * MIN)
    assert title_check_due(state, 1, T0 + 15 * MIN)


def test_a_success_resets_the_baseline():
    state = after_check(TitleCheckState(1, T0), 7, T0 + 30 * MIN, succeeded=True)
    assert state == TitleCheckState(7, T0 + 30 * MIN)
    assert not title_check_due(state, 12, T0 + 90 * MIN)
    assert title_check_due(state, 13, T0 + 90 * MIN)


def test_a_rewind_counts_again_from_the_lower_number():
    state = TitleCheckState(30, T0)
    later = T0 + 60 * MIN
    assert not title_check_due(state, 20, later)   # without a rebase it would wait for 36
    state = rebased(state, 20)
    assert state == TitleCheckState(20, T0)
    assert not title_check_due(state, 25, later)
    assert title_check_due(state, 26, later)


def test_rebase_leaves_a_normal_state_alone():
    state = TitleCheckState(10, T0)
    assert rebased(state, 10) is state
    assert rebased(state, 14) is state
    assert rebased(TitleCheckState(), 5) == TitleCheckState()


def test_closing_check_ignores_the_interval():
    state = TitleCheckState(10, T0)
    assert not closing_check_due(state, 12)
    assert closing_check_due(state, 13)
    assert not closing_check_due(state, 10)


def test_closing_check_with_no_previous_check_needs_enough_messages():
    assert not closing_check_due(TitleCheckState(), 2)
    assert closing_check_due(TitleCheckState(), 3)


def test_relevant_messages():
    assert is_relevant_message("fix the scroll")
    assert is_relevant_message("/compact keep the search part")   # a command with arguments says something
    assert not is_relevant_message("/compact")
    assert not is_relevant_message("  /context \n")
    assert not is_relevant_message("...")
    assert not is_relevant_message("")


def _messages(*offsets_in_minutes, text="a real message"):
    return [(T0 + offset * MIN, text) for offset in offsets_in_minutes]


def test_simulation_fires_every_sixth_message_when_the_pace_is_slow():
    result = simulate_checks(_messages(*range(0, 20 * 40, 20)))   # 40 messages, 20 min apart
    assert result.checks == [1, 7, 13, 19, 25, 31, 37]
    assert result.closing == 40   # 3 messages since the last check


def test_simulation_delays_checks_inside_a_burst():
    # 1 message, then 12 within 10 minutes, then one an hour later.
    messages = _messages(0, *[1 + i * 0.75 for i in range(12)], 70)
    result = simulate_checks(messages)
    assert result.checks == [1, 14]   # the 7th message of the burst (count 7) came too early
    assert result.closing is None


def test_simulation_skips_bare_commands_and_empty_messages():
    messages = [
        (T0, "start"),
        (T0 + 1 * MIN, "/compact"),
        (T0 + 2 * MIN, "..."),
        *[(T0 + (20 + 20 * i) * MIN, f"message {i}") for i in range(6)],
    ]
    result = simulate_checks(messages)
    assert result.checks == [1, 7]
    assert relevant_texts(messages)[:2] == ["start", "message 0"]


def test_simulated_checks_respect_both_limits():
    # Irregular pace: bursts and long pauses.
    offsets = []
    t = 0.0
    for i in range(120):
        t += 0.5 if i % 9 else 90
        offsets.append(t)
    result = simulate_checks(_messages(*offsets))
    times = [T0 + offsets[c - 1] * MIN for c in result.checks]
    assert len(result.checks) > 3
    for (count_a, time_a), (count_b, time_b) in itertools.pairwise(zip(result.checks, times)):
        assert count_b - count_a >= CHECK_MIN_MESSAGES
        assert time_b - time_a >= CHECK_MIN_INTERVAL


def test_bare_commands_and_commands_with_arguments():
    assert not is_relevant_message(" /compact ")
    assert is_relevant_message("/rename Session titles")
    assert is_relevant_message("/custom implement title updates")
    assert not is_relevant_message("... 🐈")
