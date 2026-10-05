"""Pure protocol tests: normalization, source chronology, and send validation."""

from copy import deepcopy
from itertools import permutations

import orjson
import pytest

from twicc.providers.codex.async_questions import (
    AsyncQuestion,
    QuestionFact,
    build_question_boundary,
    format_question_answers,
    normalize_questions,
    question_fact,
    reduce_question_state,
    validate_question_answers,
)


def test_boundary_captures_settled_turn_without_known_question():
    state = reduce_question_state({}, [end()])
    assert build_question_boundary(state, at=at(4)) == {
        "at": at(4),
        "line": None,
        "batch_ids": [],
        "excluded_batch_ids": [],
        "settled_turn_ids": ["t1"],
        "group_ids": [],
    }


def test_boundary_keeps_continuation_collecting_until_interruption():
    state = reduce_question_state({}, [owner(), end(), decision("t1", "continuation", "t2")])
    assert build_question_boundary(state, at=at(4))["settled_turn_ids"] == []
    state = reduce_question_state(state, [control_return(turn_ids=["t1", "t2"], outcome="interrupted", second=5)])
    boundary = build_question_boundary(state, at=at(6))
    assert boundary["settled_turn_ids"] == ["t1", "t2"]
    assert boundary["group_ids"] == ["g1"]


def at(second):
    return f"2026-10-05T09:12:{second:02d}Z"


def question(item_id="q1", turn_id="t1", second=1, line=None):
    return QuestionFact(
        f"question:{item_id}",
        "question",
        at(second),
        turn_id,
        item_id,
        line,
        {
            "source": "jsonl" if line else "sdk",
            "questions": [
                {"index": 0, "title": "Keep the menu?", "options": ["Yes", "No"]},
            ],
        },
    )


def end(turn_id="t1", second=2, line=None, **data):
    return QuestionFact(f"end:{turn_id}", "turn_end", at(second), turn_id, None, line, data)


def control_return(group_id="g1", turn_ids=None, second=3, **data):
    return QuestionFact(
        f"return:{group_id}",
        "control_return",
        at(second),
        None,
        None,
        None,
        {"group_id": group_id, "turn_ids": turn_ids or ["t1"], **data},
    )


def owner(root_turn_id="t1", second=0):
    return QuestionFact(
        "owner:g1",
        "live_owner",
        at(second),
        root_turn_id,
        None,
        None,
        {"group_id": "g1", "root_turn_id": root_turn_id, "state": "pending"},
    )


def decision(turn_id, value, successor=None, second=2):
    return QuestionFact(
        f"decision:{turn_id}",
        "settlement_decision",
        at(second),
        turn_id,
        None,
        None,
        {"group_id": "g1", "turn_id": turn_id, "decision": value, "successor_turn_id": successor},
    )


def human_submit(
    second=4,
    *,
    origin="human",
    status="accepted",
    batch_ids=None,
    settled_turn_ids=None,
    request_id="r1",
    line=None,
    **data,
):
    return QuestionFact(
        f"send:{request_id}",
        "user_submission",
        at(second),
        None,
        None,
        line,
        {
            "request_id": request_id,
            "origin": origin,
            "status": status,
            "boundary": {
                "at": at(second),
                "line": line,
                "batch_ids": batch_ids or [],
                "settled_turn_ids": settled_turn_ids if settled_turn_ids is not None else ["t1"],
            },
            **data,
        },
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ({"title": "Wrong container"}, []),
        ([None, {"title": " "}, {"title": 7}, {"title": "Question"}], [AsyncQuestion(3, "Question", [])]),
        (
            [{"title": " Quelle option ? ", "options": ["Oui", None, "", " ", "Oui", 3, "Non"]}],
            [AsyncQuestion(0, " Quelle option ? ", ["Oui", "Non"])],
        ),
        ([{"title": "Free text", "options": "bad"}], [AsyncQuestion(0, "Free text", [])]),
    ],
)
def test_normalization_preserves_source_indexes_and_valid_labels(raw, expected):
    assert normalize_questions(raw) == expected


@pytest.mark.parametrize(
    ("item_id", "second", "line", "title", "options"),
    [
        (
            "call_ttjW3C3wndpxnPwKZ5J55uGd",
            6,
            5674,
            "Should clicking a process state open its session?",
            ["Yes, open the session", "No, show its state only"],
        ),
        (
            "second-observed-question",
            55,
            5707,
            "Should the tooltip retain the session action menu?",
            ["Yes, retain the menu.", "No, remove the menu."],
        ),
    ],
)
def test_sdk_and_canonical_messages_share_identity(item_id, second, line, title, options):
    item = {
        "type": "agentMessage",
        "id": item_id,
        "delivery": "async",
        "phase": "final_answer",
        "text": title,
        "questions": [{"title": title, "options": options}],
    }
    sdk = question_fact(
        {"method": "item/completed", "params": {"turnId": "t1", "item": item}}, source="sdk", at=at(second)
    )
    canonical = question_fact(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "t1",
                "thread_id": "session",
                "item": {**item, "type": "AgentMessage", "content": [{"type": "Text", "text": title}]},
            },
        },
        source="jsonl",
        at=at(second),
        line=line,
    )
    assert sdk.key == canonical.key == f"question:{item_id}"
    state = reduce_question_state({}, [sdk, canonical, sdk])
    assert list(state["batches"]) == [item_id]
    assert state["batches"][item_id]["line"] == line
    assert state["batches"][item_id]["questions"] == [{"index": 0, "title": title, "options": options}]
    assert state["batches"][item_id]["status"] == "collecting"
    assert reduce_question_state(state, [sdk, canonical]) == state
    assert orjson.loads(orjson.dumps(state)) == state


@pytest.mark.parametrize(
    "item",
    [
        {"type": "agentMessage", "id": "q", "text": "A question?"},
        {"type": "agentMessage", "id": "q", "delivery": "sync", "questions": [{"title": "Q"}]},
        {"type": "agentMessage", "id": "", "delivery": "async", "questions": [{"title": "Q"}]},
        {"type": "CommandExecution", "id": "q", "delivery": "async", "questions": [{"title": "Q"}]},
        {"type": "agentMessage", "id": "q", "delivery": "async", "questions": [None]},
    ],
)
def test_non_question_items_are_ignored(item):
    assert question_fact({"method": "item/completed", "params": {"item": item}}, source="sdk", at=at(1)) is None


def test_started_sdk_and_non_completed_canonical_items_are_ignored():
    item = {"type": "agentMessage", "id": "q", "delivery": "async", "questions": [{"title": "Q"}]}
    assert question_fact({"method": "item/started", "params": {"item": item}}, source="sdk", at=at(1)) is None
    assert (
        question_fact(
            {"type": "event_msg", "payload": {"type": "item_started", "item": item}}, source="jsonl", at=at(1)
        )
        is None
    )


@pytest.mark.parametrize("arrival", list(permutations(range(3))))
def test_steering_before_control_return_survives_replay(arrival):
    facts = [question(), human_submit(second=2), control_return(second=3)]
    state = {}
    for index in arrival:
        state = reduce_question_state(state, [facts[index]])
    assert state["batches"]["q1"]["status"] == "ready"


@pytest.mark.parametrize("arrival", list(permutations(range(3))))
def test_late_question_after_human_reply_stays_sent(arrival):
    facts = [question(), control_return(second=2), human_submit(second=3)]
    state = {}
    for index in arrival:
        state = reduce_question_state(state, [facts[index]])
    assert state["batches"]["q1"]["status"] == "sent"
    assert state["batches"]["q1"]["resolved_request_id"] == "r1"


def test_rollout_lines_override_conflicting_timestamps():
    state = reduce_question_state(
        {}, [question(second=8, line=10), end(second=7, line=11), human_submit(second=6, line=12)]
    )
    assert state["batches"]["q1"]["status"] == "sent"


@pytest.mark.parametrize("outcome", ["interrupted", "failed"])
def test_control_return_releases_interrupted_or_failed_turn(outcome):
    state = reduce_question_state({}, [question(), owner(), control_return(outcome=outcome)])
    assert state["batches"]["q1"]["status"] == "ready"


@pytest.mark.parametrize("root_turn_id", ["t1", None])
def test_pending_live_owner_suppresses_raw_completion_before_rpc_link(root_turn_id):
    state = reduce_question_state({}, [question(), owner(root_turn_id), end()])
    assert state["batches"]["q1"]["status"] == "collecting"


def test_continuation_group_only_releases_after_control_return():
    facts = [
        owner(),
        question(),
        end(),
        decision("t1", "continuation", "t2"),
        question("q2", "t2", 4),
        end("t2", 5),
        decision("t2", "pending", second=5),
    ]
    state = reduce_question_state({}, facts)
    assert [batch["status"] for batch in state["batches"].values()] == ["collecting", "collecting"]
    state = reduce_question_state(
        state, [decision("t2", "return", second=6), control_return(turn_ids=["t1", "t2"], second=6)]
    )
    assert [batch["status"] for batch in state["batches"].values()] == ["ready", "ready"]


def test_historical_continuation_and_goal_hints_suppress_intermediate_completion():
    state = reduce_question_state(
        {}, [question(), end(continuation_turn_id="t2"), question("q2", "t2", 3), end("t2", 4, goal_active=True)]
    )
    assert [batch["status"] for batch in state["batches"].values()] == ["collecting", "collecting"]
    state = reduce_question_state(state, [control_return(turn_ids=["t1", "t2"], second=5)])
    assert [batch["status"] for batch in state["batches"].values()] == ["ready", "ready"]


def test_internal_continuation_user_record_groups_source_turns():
    continuation = QuestionFact(
        "user:internal",
        "user_submission",
        at(3),
        "t2",
        "internal",
        13,
        {"origin": "internal", "continuation_from_turn_id": "t1"},
    )
    state = reduce_question_state({}, [question(line=10), end(line=11), continuation, question("q2", "t2", 4, 14)])
    assert state["batches"]["q1"]["status"] == "collecting"
    state = reduce_question_state(state, [end("t2", 5, 15)])
    assert [batch["status"] for batch in state["batches"].values()] == ["ready", "ready"]


def test_control_return_uses_linked_source_end_instead_of_runtime_observation_time():
    state = reduce_question_state(
        {},
        [owner(), question(line=10), end(second=2, line=11), control_return(second=9), human_submit(second=3, line=12)],
    )
    assert state["batches"]["q1"]["status"] == "sent"


@pytest.mark.parametrize("origin", ["agent", "internal"])
def test_nonhuman_submissions_do_not_retire_questions(origin):
    state = reduce_question_state({}, [question(), end(), human_submit(origin=origin)])
    assert state["batches"]["q1"]["status"] == "ready"


@pytest.mark.parametrize("status", ["prepared", "rejected", "uncertain"])
def test_unaccepted_submissions_do_not_retire_questions(status):
    state = reduce_question_state({}, [question(), end(), human_submit(status=status)])
    assert state["batches"]["q1"]["status"] == "ready"


def test_prepared_submission_preserves_boundary_when_delivery_is_accepted():
    prepared = human_submit(second=3, status="prepared", batch_ids=["q1"])
    accepted = human_submit(second=8, status="accepted", batch_ids=["q1"])
    state = reduce_question_state({}, [question(), end(), prepared])
    state = reduce_question_state(state, [question("q2", "t2", 4), end("t2", 5), accepted])
    assert state["batches"]["q1"]["status"] == "sent"
    assert state["batches"]["q2"]["status"] == "ready"
    assert state["facts"]["send:r1"]["data"]["boundary"]["at"] == at(3)
    assert reduce_question_state(state, [prepared]) == state


def test_known_ready_batch_and_late_settled_turn_retire_but_future_question_stays():
    state = reduce_question_state(
        {}, [end(), human_submit(second=3, batch_ids=["q1"]), question(), question("q2", "t2", 4), end("t2", 5)]
    )
    assert state["batches"]["q1"]["status"] == "sent"
    assert state["batches"]["q2"]["status"] == "ready"


def test_source_user_links_to_native_client_id_without_a_second_boundary():
    state = reduce_question_state(
        {}, [question(), owner(), end(second=3), human_submit(second=2, status="uncertain"), control_return(second=4)]
    )
    source = question_fact(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "t2",
                "item": {"type": "UserMessage", "id": "u1", "clientId": "r1", "content": []},
            },
        },
        source="jsonl",
        at=at(5),
        line=20,
    )
    state = reduce_question_state(state, [source])
    assert state["batches"]["q1"]["status"] == "ready"
    assert state["facts"]["send:r1"]["data"]["source_item_id"] == "u1"


def test_source_proof_accepts_uncertain_send_but_preserves_live_agent_origin():
    source = QuestionFact(
        "user:u1",
        "user_submission",
        at(5),
        "t2",
        "u1",
        20,
        {"source": "jsonl", "origin": "human", "client_message_id": "r1"},
    )
    human = reduce_question_state({}, [question(), end(), human_submit(status="uncertain"), source])
    agent = reduce_question_state({}, [question(), end(), human_submit(origin="agent", status="uncertain"), source])
    assert human["batches"]["q1"]["status"] == "sent"
    assert agent["batches"]["q1"]["status"] == "ready"


def test_unlinked_source_user_is_not_retirement_during_uncertain_delivery():
    source = QuestionFact("user:u1", "user_submission", at(5), "t2", "u1", 20, {"source": "jsonl", "origin": "human"})
    state = reduce_question_state(
        {}, [question(), end(), human_submit(status="uncertain", target_turn_id="t2", delivery_route="steer"), source]
    )
    assert state["batches"]["q1"]["status"] == "ready"


def test_repeated_question_text_has_distinct_batches_and_dismiss_survives_merge():
    dismissed = QuestionFact(
        "dismiss:d1", "dismiss", at(4), None, None, None, {"request_id": "d1", "batch_ids": ["q1"]}
    )
    state = reduce_question_state({}, [question(), end(), question("q2", "t2", 3), dismissed])
    state = reduce_question_state(state, [question(line=10), end("t2", 5)])
    assert list(state["batches"]) == ["q1", "q2"]
    assert state["batches"]["q1"]["status"] == "dismissed"
    assert state["batches"]["q1"]["resolved_request_id"] == "d1"
    assert state["batches"]["q2"]["status"] == "ready"


def test_reducer_does_not_mutate_inputs_and_runtime_provenance_survives_recompute():
    submit = human_submit(origin="agent")
    state = reduce_question_state({}, [question(), end(), submit])
    before = deepcopy(state)
    historical = submit._replace(data={"source": "jsonl", "origin": "human", "status": "accepted"}, line=30)
    rebuilt = reduce_question_state(state, [historical])
    assert state == before
    assert rebuilt["batches"]["q1"]["status"] == "ready"
    assert rebuilt["facts"]["send:r1"]["line"] == 30


def ready_snapshot():
    return {
        "revision": 3,
        "batches": [
            {
                "item_id": "q1",
                "status": "ready",
                "questions": [
                    {"index": 2, "title": "Keep the menu?", "options": ["Yes", "No"]},
                    {"index": 4, "title": "Details?", "options": []},
                ],
            }
        ],
        "resolutions": {},
        "widget_enabled": True,
    }


def response(answers=None, **data):
    return {"revision": 1, "batch_ids": ["q1"], "answers": answers or [], **data}


def test_optional_answers_accept_newer_snapshot_and_preserve_source_text():
    answers = [
        {"item_id": "q1", "index": 2, "kind": "option", "value": "Yes"},
        {"item_id": "q1", "index": 4, "kind": "other", "value": " Keep this text. "},
    ]
    assert validate_question_answers(ready_snapshot(), response(answers)) == answers
    assert validate_question_answers(ready_snapshot(), None) == []
    assert validate_question_answers(ready_snapshot(), response()) == []
    assert (
        validate_question_answers(
            ready_snapshot(), response([{"item_id": "q1", "index": 4, "kind": "other", "value": "  "}])
        )
        == []
    )


@pytest.mark.parametrize(
    "answer",
    [
        {"item_id": "q2", "index": 2, "kind": "option", "value": "Yes"},
        {"item_id": "q1", "index": 0, "kind": "option", "value": "Yes"},
        {"item_id": "q1", "index": True, "kind": "option", "value": "Yes"},
        {"item_id": "q1", "index": 2, "kind": "option", "value": "Invalid"},
        {"item_id": "q1", "index": 4, "kind": "option", "value": "No option"},
        {"item_id": "q1", "index": 2, "kind": "invalid", "value": "Yes"},
        {"item_id": "q1", "index": 2, "kind": "other", "value": None},
    ],
)
def test_invalid_answer_identity_or_option_has_stable_error(answer):
    with pytest.raises(ValueError, match="^async_questions_invalid$"):
        validate_question_answers(ready_snapshot(), response([answer]))


@pytest.mark.parametrize("batch_ids", [["missing"], ["q1", "q1"]])
def test_invalid_or_missing_batch_references_are_rejected(batch_ids):
    with pytest.raises(ValueError, match="^async_questions_(stale|invalid)$"):
        validate_question_answers(ready_snapshot(), response(batch_ids=batch_ids))


@pytest.mark.parametrize("status", ["collecting", "sent", "dismissed"])
def test_nonready_batch_is_stale(status):
    snapshot = ready_snapshot()
    snapshot["batches"][0]["status"] = status
    with pytest.raises(ValueError, match="^async_questions_stale$"):
        validate_question_answers(snapshot, response())


def test_resolved_batch_without_active_snapshot_is_stale():
    snapshot = ready_snapshot()
    snapshot["batches"] = []
    snapshot["resolutions"] = {"q1": {"status": "sent", "request_id": "other"}}
    with pytest.raises(ValueError, match="^async_questions_stale$"):
        validate_question_answers(snapshot, response())


def test_duplicate_answer_is_invalid():
    answer = {"item_id": "q1", "index": 2, "kind": "option", "value": "Yes"}
    with pytest.raises(ValueError, match="^async_questions_invalid$"):
        validate_question_answers(ready_snapshot(), response([answer, answer]))


def test_formatter_matches_spec_and_orders_answers_by_source():
    batches = [
        {"item_id": "q2", "at": at(2), "questions": [{"index": 0, "title": "Quelle limite ?", "options": []}]},
        {
            "item_id": "q1",
            "at": at(1),
            "questions": [
                {
                    "index": 2,
                    "title": "Should the tooltip retain the session action menu?",
                    "options": ["Yes, retain the menu."],
                },
                {"index": 4, "title": "Unanswered", "options": []},
            ],
        },
    ]
    answers = [
        {"item_id": "q2", "index": 0, "kind": "other", "value": "24rem, merci."},
        {"item_id": "q1", "index": 2, "kind": "option", "value": "Yes, retain the menu."},
    ]
    assert format_question_answers(batches, answers, "Limit the tooltip width to 24rem.") == (
        "Answers to your questions:\n\n"
        "Question: Should the tooltip retain the session action menu?\nAnswer: Yes, retain the menu.\n\n"
        "Question: Quelle limite ?\nAnswer: 24rem, merci.\n\n"
        "Additional message:\nLimit the tooltip width to 24rem."
    )


@pytest.mark.parametrize(
    ("answers", "text", "expected"),
    [
        ([], "Ordinary text", "Ordinary text"),
        ([], "", ""),
        (
            [{"item_id": "q1", "index": 2, "kind": "option", "value": "Yes"}],
            "",
            "Answers to your questions:\n\nQuestion: Keep the menu?\nAnswer: Yes",
        ),
        ([{"item_id": "q1", "index": 4, "kind": "other", "value": " "}], "Text", "Text"),
    ],
)
def test_formatter_omits_empty_sections(answers, text, expected):
    assert format_question_answers(ready_snapshot()["batches"], answers, text) == expected


def test_repeated_pending_decision_does_not_erase_continuation_link():
    state = reduce_question_state(
        {}, [owner(), question(), end(), decision("t1", "continuation", "t2"), question("q2", "t2", 4)]
    )
    state = reduce_question_state(state, [decision("t1", "pending")])
    assert state["batches"]["q1"]["status"] == "collecting"
    assert state["facts"]["decision:t1"]["data"]["successor_turn_id"] == "t2"


def test_repeated_historical_provenance_does_not_overwrite_live_origin():
    state = reduce_question_state({}, [question(), end(), human_submit(origin="agent")])
    historical = human_submit()._replace(data={"source": "jsonl", "origin": "human", "status": "accepted"}, line=30)
    state = reduce_question_state(state, [historical])
    state = reduce_question_state(state, [historical])
    assert state["batches"]["q1"]["status"] == "ready"


def test_accepted_known_ready_membership_survives_new_owner_evidence():
    state = reduce_question_state({}, [question(), end(), human_submit(batch_ids=["q1"])])
    state = reduce_question_state(state, [owner()])
    assert state["batches"]["q1"]["status"] == "sent"


@pytest.mark.parametrize(
    "malformed",
    [
        [],
        {"batch_ids": "q1", "answers": []},
        {"batch_ids": [None], "answers": []},
        {"batch_ids": ["q1"], "answers": [None]},
        {"batch_ids": ["q1"], "answers": [{"item_id": "q1", "index": 2, "kind": [], "value": "Yes"}]},
    ],
)
def test_malformed_responses_use_invalid_error_code(malformed):
    with pytest.raises(ValueError, match="^async_questions_invalid$"):
        validate_question_answers(ready_snapshot(), malformed)


def test_later_owned_turn_does_not_hide_already_ready_question():
    next_owner = owner()._replace(
        key="owner:g2", at=at(4), turn_id="t2", data={"group_id": "g2", "root_turn_id": "t2", "state": "pending"}
    )
    state = reduce_question_state({}, [question(), end(), next_owner, question("q2", "t2", 5)])
    assert state["batches"]["q1"]["status"] == "ready"
    assert state["batches"]["q2"]["status"] == "collecting"


def test_late_question_in_settled_group_uses_submission_group_boundary():
    submit = human_submit(second=7, settled_turn_ids=[])
    submit.data["boundary"]["group_ids"] = ["g1"]
    state = reduce_question_state(
        {},
        [
            owner(),
            end(),
            decision("t1", "continuation", "t2"),
            end("t2", 5),
            control_return(turn_ids=["t1", "t2"], second=6),
            submit,
            question("q2", "t2", 4),
        ],
    )
    assert state["batches"]["q2"]["status"] == "sent"


def test_successor_completion_before_rpc_link_stays_collecting():
    state = reduce_question_state(
        {}, [owner(), question(), end(), decision("t1", "continuation"), question("q2", "t2", 4), end("t2", 5)]
    )
    assert state["batches"]["q1"]["status"] == "collecting"
    assert state["batches"]["q2"]["status"] == "collecting"
    state = reduce_question_state(
        state, [decision("t1", "continuation", "t2"), control_return(turn_ids=["t1", "t2"], second=6)]
    )
    assert [batch["status"] for batch in state["batches"].values()] == ["ready", "ready"]


@pytest.mark.parametrize("arrival", list(permutations(range(3))))
def test_mixed_sdk_and_rollout_batches_have_deterministic_source_order(arrival):
    facts = [question("q1", second=8, line=10), question("q2", second=4), question("q3", second=2, line=12)]
    state = reduce_question_state({}, [facts[index] for index in arrival])
    assert list(state["batches"]) == ["q2", "q1", "q3"]


@pytest.mark.parametrize("outcome", ["interrupted", "failed"])
@pytest.mark.parametrize("arrival", list(permutations(range(5))))
def test_successor_without_terminal_completion_preserves_control_return_boundary(outcome, arrival):
    facts = [
        question()._replace(
            line=1, data={"source": "jsonl", "questions": [{"index": 0, "title": "First?", "options": []}]}
        ),
        end(line=2, continuation_turn_id="t2"),
        question("q2", "t2", 4, 4)._replace(
            data={"source": "jsonl", "questions": [{"index": 0, "title": "Second?", "options": []}]}
        ),
        QuestionFact(
            "user:u1",
            "user_submission",
            at(5),
            "t2",
            "u1",
            5,
            {"source": "jsonl", "origin": "human", "status": "accepted"},
        ),
        control_return(turn_ids=["t1", "t2"], second=6, outcome=outcome)._replace(turn_id="t2", line=6),
    ]
    state = {}
    for index in arrival:
        state = reduce_question_state(state, [facts[index]])
    assert state["batches"]["q1"]["status"] == "ready"
    assert state["batches"]["q2"]["status"] == "ready"
