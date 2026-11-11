"""Deterministic logical completion facts, without clocks or provider SDKs."""

import logging
import random

import pytest

from twicc.agent.completion import CompletionController
from twicc.agent.completion_types import NativeMessageRef


@pytest.fixture
def controller():
    value = CompletionController(run_id="run")
    value.activity(0.0)
    return value


@pytest.fixture
def diagnostic_messages(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("twicc.agent.completion"), "disabled", False)
    caplog.set_level(logging.DEBUG, logger="twicc.agent.completion")
    return caplog


@pytest.mark.parametrize("retired_cause", ["released_hold", "rejected_input", "accepted_input"])
def test_completed_interval_does_not_label_unannounced_restart(controller, diagnostic_messages, retired_cause):
    controller.provider_ended(0)
    if retired_cause == "released_hold":
        controller.set_holds(frozenset({"subagent"}), 0)
        controller.set_holds(frozenset(), 1)
    else:
        controller.input_pending("input", 0)
        controller.input_settled("input", "rejected" if retired_cause == "rejected_input" else "accepted", 1)
        if retired_cause == "accepted_input":
            controller.provider_ended(1)
    ticket = controller.candidate()
    assert ticket.deadline == 2
    closed = controller.claim_due(ticket, 2)
    assert closed.outcome == "normal"
    controller.activity(3)
    assert controller.snapshot().ordinal == closed.ordinal + 1
    restarts = [record.message for record in diagnostic_messages.records if "root_restart" in record.message]
    assert len(restarts) == 1
    assert "cause=unannounced_root" in restarts[0]


@pytest.mark.parametrize("active_cause,retired_cause", [
    ("input", "rejected_input"), ("input", "released_hold"),
    ("shell", "rejected_input"), ("shell", "released_hold"),
    ("hold", "rejected_input"),
])
def test_retiring_one_cause_preserves_another_announced_restart(
    controller, diagnostic_messages, active_cause, retired_cause,
):
    controller.provider_ended(0)
    assert controller.claim_due(controller.candidate(), 1).outcome == "normal"
    if active_cause == "input":
        controller.input_pending("still-pending", 2)
    elif active_cause == "shell":
        controller.note_shell_delivery()
    else:
        controller.set_holds(frozenset({"subagent"}), 2)
    if retired_cause == "rejected_input":
        controller.input_pending("rejected", 2.1)
        controller.input_settled("rejected", "rejected", 2.2)
    else:
        controller.set_holds(frozenset({"temporary"}), 2.1)
        controller.set_holds(frozenset(), 2.2)
    controller.activity(3)
    restarts = [record.message for record in diagnostic_messages.records if "root_restart" in record.message]
    assert len(restarts) == 1
    assert f"cause={active_cause}" in restarts[0]


@pytest.mark.parametrize("active_cause", ["hold", "shell"])
def test_terminal_closure_preserves_unconsumed_hold_or_shell(controller, diagnostic_messages, active_cause):
    if active_cause == "hold":
        controller.set_holds(frozenset({"subagent"}), .1)
    else:
        controller.note_shell_delivery()
    assert controller.terminate("ended", 1).outcome == "ended"
    controller.activity(2)
    assert any("root_restart" in record.message and f"cause={active_cause}" in record.message
               for record in diagnostic_messages.records)


def test_full_second_is_required(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    assert ticket.deadline == 1.0
    assert controller.claim_due(ticket, 0.999) is None
    assert controller.claim_due(ticket, 1.0).outcome == "normal"
    assert controller.claim_due(ticket, 2.0) is None


def test_requesting_invalidates_old_candidate(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    controller.activity(0.02)
    assert controller.claim_due(ticket, 1.0) is None
    controller.provider_ended(4.0)
    assert controller.claim_due(controller.candidate(), 5.0).outcome == "normal"
    assert controller.claim_due(ticket, 6.0) is None


def test_hold_release_starts_a_full_new_delay(controller):
    controller.set_holds(frozenset({"child"}), 0.0)
    controller.provider_ended(0.0)
    assert controller.candidate() is None
    controller.set_holds(frozenset(), 8.0)
    assert controller.candidate().deadline == 9.0


def test_pending_input_does_not_manufacture_an_end(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    controller.input_pending("input", 0.1)
    assert controller.claim_due(ticket, 2.0) is None
    controller.input_settled("input", "accepted", 0.2)
    assert controller.candidate() is None
    controller.provider_ended(1.0)
    assert controller.candidate().deadline == 2.0


@pytest.mark.parametrize("status", ["rejected", "uncertain"])
def test_rejected_or_uncertain_input_never_creates_success(controller, status):
    controller.input_pending("input", 0.0)
    controller.input_settled("input", status, 1.0)
    assert controller.candidate() is None
    if status == "uncertain":
        controller.provider_ended(2.0)
        assert controller.candidate() is None


def test_pending_action_invalidates_candidate_and_release_restarts_delay(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    controller.set_awaiting(True, 0.1)
    assert controller.candidate() is None
    assert controller.claim_due(ticket, 5.0) is None
    controller.set_awaiting(False, 6.0)
    assert controller.candidate().deadline == 7.0


def test_duplicate_end_and_metadata_noise_do_not_extend_deadline(controller):
    ref = NativeMessageRef("codex_item", "answer", "physical")
    controller.provider_ended(0.0, final_ref=ref)
    ticket = controller.candidate()
    controller.provider_ended(0.5, final_ref=ref)
    controller.set_holds(frozenset(), 0.6)
    controller.set_awaiting(False, 0.7)
    assert controller.candidate() == ticket
    assert controller.claim_due(ticket, 1.0).final_refs == (ref,)


def test_no_answer_retains_previous_physical_final(controller):
    ref = NativeMessageRef("claude_uuid", "answer")
    controller.provider_ended(0.0, final_ref=ref)
    controller.activity(0.1)
    controller.provider_ended(2.0)
    assert controller.claim_due(controller.candidate(), 3.0).final_refs == (ref,)


def test_known_empty_answer_can_complete(controller):
    controller.provider_ended(0.0)
    assert controller.claim_due(controller.candidate(), 1.0).final_refs == ()


def test_new_interval_starts_only_on_actual_activity(controller):
    controller.provider_ended(0.0)
    closed = controller.claim_due(controller.candidate(), 1.0)
    controller.provider_ended(2.0)
    controller.input_pending("next", 3.0)
    assert controller.snapshot() == closed
    assert controller.candidate() is None
    controller.activity(4.0)
    assert controller.snapshot().turn_id != closed.turn_id
    assert controller.snapshot().ordinal == 2
    controller.provider_ended(4.0)
    assert controller.candidate() is None  # Staged input remains pending.


def test_input_before_first_activity_does_not_open_an_interval():
    controller = CompletionController(run_id="run")
    controller.input_pending("input", 0.0)
    assert controller.snapshot().ordinal == 0
    controller.input_settled("input", "accepted", 0.1)
    assert controller.snapshot().ordinal == 0
    controller.activity(0.2)
    controller.provider_ended(1.0)
    assert controller.claim_due(controller.candidate(), 2.0).ordinal == 1


def test_terminal_outcome_is_immutable(controller):
    controller.provider_ended(0.0)
    ticket = controller.candidate()
    closed = controller.terminate("ended", 0.1)
    assert closed.outcome == "ended"
    assert controller.terminate("provider_error", 2.0) is None
    assert controller.claim_due(ticket, 2.0) is None
    assert controller.snapshot() == closed


def test_deterministic_event_sequences_have_one_terminal_snapshot_per_interval():
    for seed in range(1000):
        rng = random.Random(seed)
        controller = CompletionController(run_id="run")
        terminal = {}
        tickets = []
        for tick in range(40):
            now = tick * 0.4
            event = rng.randrange(8)
            if event == 0:
                controller.activity(now)
            elif event == 1:
                controller.provider_ended(now)
            elif event == 2:
                controller.set_holds(frozenset({"hold"}) if rng.randrange(2) else frozenset(), now)
            elif event == 3:
                controller.set_awaiting(bool(rng.randrange(2)), now)
            elif event == 4:
                controller.input_pending("input", now)
            elif event == 5:
                controller.input_settled("input", rng.choice(["accepted", "rejected", "uncertain"]), now)
            elif event == 6:
                result = controller.terminate("ended", now)
                if result:
                    assert result.turn_id not in terminal
                    terminal[result.turn_id] = result
            else:
                ticket = rng.choice(tickets) if tickets else controller.candidate()
                result = controller.claim_due(ticket, now) if ticket else None
                if result:
                    assert result.turn_id not in terminal
                    terminal[result.turn_id] = result
            if controller.candidate():
                tickets.append(controller.candidate())
            snapshot = controller.snapshot()
            if snapshot.turn_id in terminal:
                assert snapshot == terminal[snapshot.turn_id]


def test_rejected_delivery_rearms_a_known_end_without_inventing_one(controller):
    controller.provider_ended(0.0)
    controller.input_pending("input", 0.1)
    controller.input_settled("input", "rejected", 0.2)
    assert controller.candidate().deadline == 1.2


def test_rejected_delivery_after_real_activity_requires_a_new_end(controller):
    controller.provider_ended(0.0)
    controller.input_pending("input", 0.1)
    controller.activity(0.2)
    controller.input_settled("input", "rejected", 0.3)
    assert controller.candidate() is None


def test_accepted_delivery_after_known_end_requires_a_new_end(controller):
    controller.provider_ended(0.0)
    controller.input_pending("input", 0.1)
    controller.input_settled("input", "accepted", 0.2)
    assert controller.candidate() is None
    controller.provider_ended(0.3)
    assert controller.candidate().deadline == 1.3


def test_acceptance_after_its_actual_result_keeps_that_result_eligible(controller):
    controller.provider_ended(0.0)
    controller.input_pending("input", 0.1)
    controller.activity(0.2)
    controller.provider_ended(0.3)
    controller.input_settled("input", "accepted", 0.4)
    assert controller.candidate().deadline == 1.4
    assert controller.claim_due(controller.candidate(), 1.4).outcome == "normal"


def test_same_root_active_stream_does_not_change_persisted_facts(controller):
    snapshot = controller.snapshot()
    for tick in range(1000):
        controller.activity(tick * 0.01)
    assert controller.snapshot() == snapshot
    ref = NativeMessageRef("codex_item", "first", "native")
    controller.activity(20.0, first_ref=ref)
    assert controller.snapshot().first_ref == ref
    assert controller.snapshot().revision > snapshot.revision


def test_defer_due_consumes_eligibility_until_safe_reconsider():
    controller = CompletionController(run_id='run')
    controller.activity(0)
    controller.provider_ended(0)
    candidate = controller.candidate()
    assert controller.defer_due(candidate, 1)
    assert controller.is_open and controller.candidate() is None
    controller.set_holds(frozenset({'hold'}), 2)
    controller.reconsider(3)
    assert controller.candidate() is None
    controller.set_holds(frozenset(), 4)
    assert controller.candidate() is None
    controller.reconsider(5)
    assert controller.candidate().deadline == 6
    assert controller.claim_due(candidate, 9) is None


def test_root_restart_clears_shell_only_reconsideration():
    controller = CompletionController(run_id='run')
    controller.activity(0)
    controller.provider_ended(0)
    assert controller.defer_due(controller.candidate(), 1)
    controller.activity(2)
    controller.reconsider(3)
    assert controller.candidate() is None
