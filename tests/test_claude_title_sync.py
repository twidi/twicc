"""Pure transactional title ordering, without provider or database access."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from twicc.providers.claude_code.title_sync import (
    SourceCursor, TitleObservation, TitleSyncCoordinator,
)


def c(n, *, epoch=0, incarnation="disk", sid="s"):
    return SourceCursor(sid, incarnation, epoch, n)


def title(n, value, **kwargs):
    return TitleObservation(c(n, **kwargs), "s", "title", value, None, None)


def command(n, value, message=None):
    return TitleObservation(c(n), "s", "rename_command", value, message or str(n), None)


def outcome(n, value=None, parent=None, *, error=False):
    return TitleObservation(c(n), "s", "command_error" if error else "command_success", value, str(n), parent)


@pytest.fixture
def sync():
    return TitleSyncCoordinator()


def setup(sync, *, hybrid=False, boundary=c(10), value="B"):
    sync.attach_process("s", "p", hybrid=hybrid)
    decision = sync.accept_decision("s", value)
    assert sync.start_dispatch(decision, boundary)
    return decision


def test_stage_isolation_and_required_mutation_context(sync):
    with pytest.raises(RuntimeError):
        sync.accept_decision("s", "B")
    with sync.staged_state() as stage:
        decision = sync.accept_decision("s", "B")
        assert sync.current_decision("s") == decision
        with ThreadPoolExecutor() as pool:
            assert pool.submit(sync.current_decision, "s").result() is None
        stage.commit()
    assert sync.current_decision("s") == decision


def test_exact_boundary_and_replaced_decision(sync):
    with sync.staged_state() as stage:
        sync.attach_process("s", "p", hybrid=False)
        first = sync.accept_decision("s", "B")
        assert sync.observe((title(11, "A"),)).apply == ()
        assert sync.start_dispatch(first, c(20))
        assert sync.observe((title(19, "B"),)).confirmed == ()
        latest = sync.accept_decision("s", "A")
        assert latest.token != first.token
        assert sync.acknowledge(first, success=True, boundary=c(21)).confirmed == ()
        assert sync.current_decision("s") == latest
        stage.commit()


@pytest.mark.parametrize("ack_first", [False, True])
def test_sdk_echo_and_ack_replay_manual_after_floor(sync, ack_first):
    with sync.staged_state() as stage:
        decision = setup(sync)
        if ack_first:
            assert sync.acknowledge(decision, success=True, boundary=c(10)).confirmed == ()
        observed = sync.observe((title(11, "B"), title(12, "A")))
        if ack_first:
            result = observed
        else:
            assert observed.apply == ()
            result = sync.acknowledge(decision, success=True, boundary=c(12))
        assert result.confirmed == (decision.token,)
        assert result.apply == (title(12, "A"),)
        assert sync.observe((title(1, "old"), title(11, "B"), title(12, "A"))).apply == ()
        assert sync.observe((title(13, "B"),)).apply == (title(13, "B"),)
        stage.commit()


def test_sdk_noop_requires_observed_equal_boundary(sync):
    with sync.staged_state() as stage:
        sync.attach_process("s", "p", hybrid=False)
        sync.observe((title(5, "B"),))
        decision = sync.accept_decision("s", "B")
        assert sync.start_dispatch(decision, c(10))
        assert sync.acknowledge(decision, success=True, boundary=c(10)).confirmed == (decision.token,)
        assert sync.observe((title(9, "old"),)).apply == ()
        stage.commit()


def test_sdk_initial_dispatch_without_cursor(sync):
    with sync.staged_state() as stage:
        decision = setup(sync, boundary=None)
        assert sync.acknowledge(decision, success=True, boundary=None).confirmed == ()
        assert not sync.bind_initial_source(decision, c(1, sid="foreign"))
        assert sync.bind_initial_source(decision, c(1))
        assert not sync.bind_initial_source(decision, c(2))
        assert sync.observe((title(1, "A"),)).confirmed == ()
        assert sync.observe((title(2, "B"),)).confirmed == (decision.token,)
        stage.commit()


def test_unbound_dispatch_rejects_existing_source_and_old_process(sync):
    with sync.staged_state() as stage:
        decision = setup(sync, boundary=None)
        sync.attach_process("s", "new", hybrid=False)
        assert not sync.bind_initial_source(decision, c(1))
        assert sync.acknowledge(decision, success=True, boundary=None).confirmed == ()
        new = sync.accept_decision("s", "C")
        sync.observe((title(1, "A"),))
        assert not sync.start_dispatch(new, None)
        stage.commit()


def test_hybrid_outcomes_do_not_clear_delivery_hazard(sync):
    with sync.staged_state() as stage:
        first = setup(sync, hybrid=True)
        sync.acknowledge(first, success=True, boundary=c(10))
        second = sync.accept_decision("s", "C")
        result = sync.observe((command(11, "A", "manual"), outcome(12, parent="manual")))
        assert result.apply[-1].title == "A"
        assert result.confirmed == ()
        assert sync.current_decision("s") is None
        third = sync.accept_decision("s", "D")
        assert not sync.start_dispatch(third, c(12))
        assert sync.retire_process("s", "wrong") is None
        assert sync.retire_process("s", "p") == third
        assert sync.current_decision("s") == third
        assert second.token != first.token
        stage.commit()


def test_replacement_source_and_foreign_source(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        sync.acknowledge(decision, success=True, boundary=c(10))
        assert sync.observe((title(20, "B", sid="foreign"),)).confirmed == ()
        replacement = title(1, "replacement", epoch=1)
        assert sync.observe((replacement,)).apply == (replacement,)
        assert sync.observe((title(100, "stale"),)).apply == ()
        assert sync.acknowledge(decision, success=True, boundary=c(101)).confirmed == ()
        stage.commit()


def test_observation_rollback_replays_once(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        sync.acknowledge(decision, success=True, boundary=c(10))
        stage.commit()
    records = (title(11, "B"), title(12, "manual"))
    with sync.staged_state():
        assert sync.observe(records).confirmed == (decision.token,)
    with sync.staged_state() as stage:
        result = sync.observe(records)
        assert result.confirmed == (decision.token,)
        assert result.apply == (records[1],)
        stage.commit()
    with sync.staged_state() as stage:
        assert sync.observe(records).apply == ()
        stage.commit()


def test_ack_rollback_preserves_hazard(sync):
    with sync.staged_state() as stage:
        first = setup(sync, hybrid=True)
        stage.commit()
    with sync.staged_state():
        sync.acknowledge(first, success=False, boundary=c(10))
        sync.retire_process("s", "p")
    with sync.staged_state() as stage:
        latest = sync.accept_decision("s", "C")
        assert not sync.start_dispatch(latest, c(20))
        stage.commit()


def test_savepoint_rollback_restores_overlay(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        checkpoint = stage.checkpoint()
        sync.acknowledge(decision, success=True, boundary=c(10))
        sync.observe((title(11, "B"), title(12, "manual")))
        sync.attach_process("s", "new", hybrid=True)
        stage.restore(checkpoint)
        assert sync.current_decision("s") == decision
        assert sync.acknowledge(decision, success=True, boundary=c(10)).confirmed == ()
        assert sync.observe((title(11, "B"),)).confirmed == (decision.token,)
        stage.commit()


def test_distinct_command_overflow_is_bounded(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        records = tuple(command(n, str(n)) for n in range(11, 10011))
        result = sync.observe(records)
        assert result.replay
        assert result.replay[0].start == c(11)
        assert result.replay[0].upper == c(10010)
        assert sync.unresolved_evidence_count("s") <= 128
        stage.commit()
    with sync.staged_state() as stage:
        sync.acknowledge(decision, success=True, boundary=c(10010))
        for offset in range(0, len(records), 128):
            sync.observe(records[offset:offset + 128])
            assert sync.unresolved_evidence_count("s") <= 128
        result = sync.observe((title(10011, "B"), title(10012, "manual")))
        assert result.confirmed == (decision.token,)
        assert result.apply == (title(10012, "manual"),)
        stage.commit()


def test_explicit_outcome_above_overflow_still_applies(sync):
    with sync.staged_state() as stage:
        setup(sync, hybrid=True)
        sync.observe(tuple(command(n, str(n)) for n in range(11, 150)))
        result = sync.observe((outcome(150, "manual"),))
        assert result.apply == (outcome(150, "manual"),)
        latest = sync.accept_decision("s", "next")
        assert not sync.start_dispatch(latest, c(150))
        stage.commit()


def test_sdk_outcome_does_not_release_inflight_request(sync):
    with sync.staged_state() as stage:
        first = setup(sync)
        sync.observe((outcome(11, "manual"),))
        latest = sync.accept_decision("s", "next")
        assert not sync.start_dispatch(latest, c(11))
        assert sync.acknowledge(first, success=True, boundary=c(11)).confirmed == ()
        assert sync.start_dispatch(latest, c(11))
        stage.commit()


def test_replay_chunking_matches_unbounded_outcomes():
    records = tuple(record for n in range(11, 311, 2) for record in (
        command(n, str(n), f"cmd-{n}"), outcome(n + 1, parent=f"cmd-{n}"),
    ))
    results = []
    for chunk_size in (1, 128, len(records)):
        sync = TitleSyncCoordinator()
        applied = []
        with sync.staged_state() as stage:
            setup(sync, hybrid=True)
            for start in range(0, len(records), chunk_size):
                applied.extend(sync.observe(records[start:start + chunk_size]).apply)
            stage.commit()
        results.append(applied)
    assert results[0] == results[1] == results[2]
    assert results[0][-1].title == "309"


def test_sdk_successor_can_send_after_ack_before_old_echo(sync):
    with sync.staged_state() as stage:
        first = setup(sync)
        sync.acknowledge(first, success=True, boundary=c(10))
        latest = sync.accept_decision("s", "next")
        assert sync.start_dispatch(latest, c(11))
        assert sync.observe((title(12, "B"),)).apply == ()
        stage.commit()


def test_source_replacement_cannot_overlap_sdk_requests(sync):
    with sync.staged_state() as stage:
        first = setup(sync)
        sync.observe((title(1, "replacement", epoch=1),))
        latest = sync.accept_decision("s", "next")
        assert not sync.start_dispatch(latest, c(1, epoch=1))
        assert sync.acknowledge(first, success=True, boundary=c(11)).confirmed == ()
        assert sync.start_dispatch(latest, c(2, epoch=1))
        stage.commit()


def test_commit_callback_can_publish_after_context_exit(sync):
    with sync.staged_state() as stage:
        decision = sync.accept_decision("s", "B")
    assert sync.current_decision("s") is None
    stage.commit()
    assert sync.current_decision("s") == decision


def test_nested_stage_rollback_preserves_outer_state(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        with pytest.raises(ValueError), sync.staged_state():
            sync.retire_process("s", "p")
            sync.accept_decision("s", "wrong")
            raise ValueError("savepoint rollback")
        assert sync.current_decision("s") == decision
        assert sync.acknowledge(decision, success=True, boundary=c(10)).confirmed == ()
        stage.commit()


def test_noop_ack_with_conflicting_evidence_stays_unconfirmed(sync):
    with sync.staged_state() as stage:
        sync.attach_process("s", "p", hybrid=False)
        sync.observe((title(5, "B"),))
        decision = sync.accept_decision("s", "B")
        sync.start_dispatch(decision, c(10))
        sync.observe((title(11, "A"),))
        assert sync.acknowledge(decision, success=True, boundary=c(12)).confirmed == ()
        assert sync.current_decision("s") == decision
        result = sync.observe((command(13, "A", "manual"), outcome(14, parent="manual")))
        assert result.apply[-1].title == "A"
        assert sync.current_decision("s") is None
        stage.commit()


def test_historical_explicit_outcome_cannot_retire_new_decision(sync):
    with sync.staged_state() as stage:
        decision = setup(sync, hybrid=True, boundary=c(20))
        assert sync.observe((command(11, "old", "old"), outcome(12, parent="old"))).apply == ()
        assert sync.current_decision("s") == decision
        stage.commit()


def test_overflow_replay_matches_ordered_manual_title_events():
    records = (title(11, "B"),) + tuple(title(n, f"manual-{n}") for n in range(12, 312))
    sequences = []
    for chunk_size in (1, 73, len(records)):
        sync = TitleSyncCoordinator()
        with sync.staged_state() as stage:
            decision = setup(sync)
            buffered = sync.observe(records)
            assert buffered.apply == ()
            assert buffered.replay[0].start == c(11)
            ack = sync.acknowledge(decision, success=True, boundary=c(311))
            assert ack.confirmed == ()
            confirmed, applied = [], []
            for start in range(0, len(records), chunk_size):
                result = sync.observe(records[start:start + chunk_size])
                confirmed.extend(result.confirmed)
                applied.extend(result.apply)
            assert confirmed == [decision.token]
            assert tuple(applied) == records[1:]
            assert sync.observe(records).apply == ()
            sequences.append(applied)
            stage.commit()
    assert sequences[0] == sequences[1] == sequences[2]


def test_savepoint_restores_replay_range_and_confirmation(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        records = tuple(title(n, "B" if n == 11 else str(n)) for n in range(11, 150))
        replay = sync.observe(records).replay
        checkpoint = stage.checkpoint()
        sync.acknowledge(decision, success=True, boundary=c(149))
        sync.observe(records)
        sync.retire_process("s", "p")
        stage.restore(checkpoint)
        assert sync.current_decision("s") == decision
        assert sync.acknowledge(decision, success=True, boundary=c(149)).replay == replay
        assert sync.observe(records).confirmed == (decision.token,)
        stage.commit()


def test_dispatch_rejects_replaced_history_and_stale_boundary(sync):
    with sync.staged_state() as stage:
        sync.attach_process("s", "p", hybrid=False)
        sync.observe((title(10, "A"),))
        decision = sync.accept_decision("s", "B")
        assert not sync.start_dispatch(decision, c(9))
        assert not sync.start_dispatch(decision, c(1, epoch=1))
        stage.commit()


def test_sdk_retry_can_confirm_noop_after_uncertain_first_write(sync):
    with sync.staged_state() as stage:
        decision = setup(sync)
        assert sync.observe((title(11, "B"),)).confirmed == ()
        assert sync.acknowledge(decision, success=False, boundary=c(11)).confirmed == ()
        assert sync.start_dispatch(decision, c(11))
        assert sync.acknowledge(decision, success=True, boundary=c(11)).confirmed == (decision.token,)
        stage.commit()


@pytest.mark.parametrize("conflict_line", [8, 10])
def test_noop_ack_rejects_late_ingested_conflict_before_dispatch_boundary(sync, conflict_line):
    with sync.staged_state() as stage:
        sync.attach_process("s", "p", hybrid=False)
        sync.observe((title(5, "B"),))
        decision = sync.accept_decision("s", "B")
        assert sync.start_dispatch(decision, c(10))
        assert sync.observe((title(conflict_line, "A"),)).apply == ()
        assert sync.acknowledge(decision, success=True, boundary=c(10)).confirmed == ()
        assert sync.current_decision("s") == decision
        assert sync.observe((title(11, "B"),)).confirmed == (decision.token,)
        stage.commit()


def test_certain_non_delivery_releases_only_exact_process_and_dispatch(sync):
    with sync.staged_state() as stage:
        first = setup(sync, hybrid=True)
        successor = sync.accept_decision("s", "successor")
        sync.cancel_dispatch_before_delivery(first)
        assert sync.start_dispatch(successor, c(10))
        # Repeating the old cancellation cannot release the successor hazard.
        sync.cancel_dispatch_before_delivery(first)
        next_decision = sync.accept_decision("s", "next")
        assert not sync.start_dispatch(next_decision, c(10))
        # A replacement process also rejects cancellation from its predecessor.
        sync.attach_process("s", "replacement", hybrid=True)
        replacement = sync.accept_decision("s", "replacement")
        assert sync.start_dispatch(replacement, c(10))
        sync.cancel_dispatch_before_delivery(successor)
        next_replacement = sync.accept_decision("s", "newest")
        assert not sync.start_dispatch(next_replacement, c(10))
        stage.commit()


def test_certain_non_delivery_is_transactional(sync):
    with sync.staged_state() as stage:
        first = setup(sync, hybrid=True)
        successor = sync.accept_decision("s", "successor")
        stage.commit()
    with pytest.raises(RuntimeError, match="rollback"), sync.staged_state():
        sync.cancel_dispatch_before_delivery(first)
        assert sync.start_dispatch(successor, c(10))
        raise RuntimeError("rollback")
    with sync.staged_state() as stage:
        assert not sync.start_dispatch(successor, c(10))
        sync.cancel_dispatch_before_delivery(first)
        assert sync.start_dispatch(successor, c(10))
        stage.commit()


@pytest.mark.parametrize("success", [False, True])
def test_certain_non_delivery_cannot_cancel_an_already_acknowledged_hybrid_attempt(sync, success):
    with sync.staged_state() as stage:
        first = setup(sync, hybrid=True)
        sync.acknowledge(first, success=success, boundary=c(10))
        sync.cancel_dispatch_before_delivery(first)
        successor = sync.accept_decision("s", "successor")
        assert not sync.start_dispatch(successor, c(10))
        stage.commit()
