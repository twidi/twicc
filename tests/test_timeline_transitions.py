from twicc.core.timeline import EventSpec, process_transition_events

WORK = {"subagents": 0, "shells": 2, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}
OTHER_WORK = {"subagents": 0, "shells": 2, "monitors": 1, "scheduled_wakeup_at": None, "goal": False}


def specs(**kw):
    base = {"prev_state": "user_turn", "prev_awaiting": False, "prev_work": None,
            "new_state": "user_turn", "new_awaiting": False, "new_work": None}
    base.update(kw)
    return process_transition_events(**base)


def diff(**kw):
    return [spec.kind for spec in specs(**kw)]


def test_no_change_emits_nothing():
    assert diff() == []
    assert diff(prev_state="starting", new_state="starting") == []
    assert diff(prev_work=WORK, new_work=WORK) == []


def test_entering_and_leaving_the_assistant_turn():
    assert specs(prev_state="starting", new_state="assistant_turn") == [EventSpec("turn.started", {})]
    assert diff(prev_state="user_turn", new_state="assistant_turn") == ["turn.started"]
    assert specs(prev_state="assistant_turn", new_state="user_turn") == [
        EventSpec("turn.ended", {"background_work": None})]


def test_turn_ended_carries_the_background_work_of_the_new_row():
    assert specs(prev_state="assistant_turn", new_state="user_turn", new_work=WORK) == [
        EventSpec("turn.ended", {"background_work": WORK})]


def test_work_changes_while_idle_re_emit_turn_ended_with_the_new_snapshot():
    assert specs(prev_work=None, new_work=WORK) == [EventSpec("turn.ended", {"background_work": WORK})]
    assert specs(prev_work=WORK, new_work=OTHER_WORK) == [EventSpec("turn.ended", {"background_work": OTHER_WORK})]
    assert specs(prev_work=WORK, new_work=None) == [EventSpec("turn.ended", {"background_work": None})]


def test_work_changes_outside_the_idle_state_emit_nothing():
    assert diff(prev_state="assistant_turn", new_state="assistant_turn", new_work=WORK) == []
    assert diff(prev_state="assistant_turn", prev_work=WORK, new_state="assistant_turn") == []
    assert diff(prev_state="starting", new_state="starting", new_work=WORK) == []
    # Entering a turn supersedes a pending "idle but busy" state: only turn.started.
    assert diff(prev_work=WORK, new_state="assistant_turn", new_work=WORK) == ["turn.started"]


def test_awaiting_input_follows_the_flag_and_carries_the_request():
    started = specs(
        prev_state="assistant_turn", new_state="assistant_turn", new_awaiting=True,
        request_type="tool_approval", tool_name="Bash",
    )
    assert started == [EventSpec("awaiting_input.started", {"request_type": "tool_approval", "tool_name": "Bash"})]
    assert diff(prev_state="assistant_turn", prev_awaiting=True,
                new_state="assistant_turn", new_awaiting=False) == ["awaiting_input.ended"]


def test_death_closes_the_turn_and_the_wait_with_no_background_work():
    got = specs(prev_state="assistant_turn", prev_awaiting=True, prev_work=WORK, new_state="dead", new_work=WORK)
    assert got == [EventSpec("turn.ended", {"background_work": None}), EventSpec("awaiting_input.ended", {})]
    assert diff(prev_state="assistant_turn", new_state="dead") == ["turn.ended"]


def test_death_from_user_turn_emits_nothing_even_with_work_running():
    assert diff(new_state="dead") == []
    assert diff(prev_work=WORK, new_state="dead") == []


def test_ends_come_before_starts_in_one_transition():
    assert diff(prev_state="assistant_turn", prev_awaiting=True,
                new_state="user_turn", new_awaiting=False) == ["turn.ended", "awaiting_input.ended"]
    assert diff(prev_state="user_turn", prev_awaiting=False,
                new_state="assistant_turn", new_awaiting=True) == ["turn.started", "awaiting_input.started"]
