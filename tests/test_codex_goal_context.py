"""Goal rollout regression coverage, including captures from Codex 0.169.0.

Fixtures retain selected raw lines from the 2026-11-14 diagnostic sessions
01a1d97f-96a7-7e72-acac-c03bfdd223a9 (multi) and
01a1d980-a70f-7e61-8d35-ba4392794434 (active edit and clear).
"""

from copy import deepcopy
from pathlib import Path

import orjson
import pytest
from django.conf import settings

from tests.test_codex_hardcoded_commands import (
    _batch_compute, _goal_context, _persist_line, _thread_goal_updated,
)
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionHistoryFact
from twicc.providers.codex.async_questions import extract_async_question_facts
from twicc.providers.codex.canonical import user_message_text
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.codex.history_facts import extract_history_facts
from twicc.providers.goals import apply_goal_event
from twicc.providers.history_facts import HistoryFactContext, HistoryFactKind


def capture(name="multi"):
    path = Path(__file__).parent / "fixtures" / f"codex_goal_0_169_{name}.jsonl"
    return [orjson.loads(line) for line in path.read_bytes().splitlines()]


def new_context():
    return capture()[4]


def objective_output(objective="Build <all> & verify"):
    record = capture()[3]
    record["payload"]["output"] = objective
    return record


@pytest.fixture
def goal_session(db):
    project = Project.objects.create(id="goal-format-project")
    return Session.objects.create(id="goal-format-session", project=project, provider=Provider.CODEX)


def scan(records):
    compute = _batch_compute()
    visible, goals = [], []
    for line, record in enumerate(deepcopy(records), 1):
        compute.transform_inline(record, session_id="compute-session", line_num=line)
        text = user_message_text(record)
        if text is not None:
            visible.append(text)
        event = compute.extract_goal_event(record)
        if event is not None:
            apply_goal_event(goals, event, timestamp=None)
    return visible, goals


def test_real_new_goal_has_one_command_across_multiple_turns():
    visible, goals = scan(capture())
    assert len(visible) == 1
    assert visible[0].startswith("/goal Controlled diagnostic.")
    assert goals[0]["state"] == "completed"


def test_real_active_edit_and_clear_preserve_goal_history():
    visible, goals = scan(capture("edit_clear"))
    assert len(visible) == 3
    assert visible[0].startswith("/goal Controlled format diagnostic.")
    assert visible[1:] == ["/goal Write EDITED in /tmp/goal-test-active-edit-01a1d97c.txt", "/goal clear"]
    assert len(goals) == 1
    assert len(goals[0]["objectives"]) == 2
    assert goals[0]["cleared"] is True


@pytest.mark.parametrize("external_message", [False, True])
def test_separate_objective_without_status_event_is_literal_task_data(external_message):
    objective = objective_output()
    if external_message:
        objective["payload"] = {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "<external_goal_objective>Build <all> & verify</external_goal_objective>"}],
        }
    assert scan([objective, new_context(), new_context()])[0] == ["/goal Build <all> & verify"]


def test_full_snapshot_wins_over_truncated_objective_context():
    objective = "Build everything " * 1000
    records = [_thread_goal_updated("active", objective), objective_output("Build everything …123 tokens truncated…everything "), new_context()]
    assert scan(records)[0] == [f"/goal {objective}"]


def test_missing_objective_leaves_continuation_hidden_until_context_arrives():
    assert scan([new_context(), objective_output(), new_context()])[0] == ["/goal Build <all> & verify"]


def test_goal_probe_does_not_repeat_command_boundary():
    records = capture()
    visible, _ = scan([*records[:5], records[5], new_context()])
    assert len(visible) == 1
    assert visible[0].startswith("/goal Controlled diagnostic.")


def test_objective_that_contains_goal_json_is_not_a_lifecycle_event():
    record = objective_output('{"goal":{"threadId":"fake","status":"complete"}}')
    compute = _batch_compute()
    assert compute.extract_goal_event(record) is None
    assert compute.is_goal_continuation_stopped(record) is False
    assert scan([record, new_context()])[0] == [
        '/goal {"goal":{"threadId":"fake","status":"complete"}}',
    ]


def test_new_continuation_retains_async_question_successor_evidence():
    facts = extract_async_question_facts(new_context(), line=10)
    assert any(f.data.get("internal_successor") for f in facts)


def test_new_goal_context_and_objective_have_historical_source_pointers():
    history = HistoryFactContext(Provider.CODEX)
    records = [objective_output(), new_context()]
    facts = [fact for line, record in enumerate(records, 1)
             for fact in extract_history_facts(record, line_num=line, history=history)]
    assert any(f.kind == HistoryFactKind.GOAL_CONTEXT and f.line_num == 2 for f in facts)
    assert any(f.kind == HistoryFactKind.GOAL_UPDATE and f.line_num == 1 for f in facts)


@pytest.mark.parametrize("already_consumed", [False, True])
@pytest.mark.parametrize("indexed", [False, True])
def test_live_restart_resolves_new_objective_without_repeating_command(goal_session, already_consumed, indexed):
    session = goal_session
    records = [capture()[1], capture()[3]]
    if already_consumed:
        records.append(new_context())
    history = HistoryFactContext(Provider.CODEX)
    for line, record in enumerate(records, 1):
        _persist_line(session, line, record)
        if indexed:
            for fact in extract_history_facts(record, line_num=line, history=history):
                SessionHistoryFact.objects.create(
                    session=session, line_num=line, kind=fact.kind, key=fact.key, data=fact.data,
                )
    if indexed:
        session.compute_version = settings.CODEX_COMPUTE_VERSION
        session.save(update_fields=["compute_version"])
    compute = CodexSessionCompute()
    record = new_context()
    compute.transform_inline(record, session_id=session.id, line_num=10)
    if already_consumed:
        assert compute.compute_item_kind(record) == ItemKind.SYSTEM
    else:
        assert user_message_text(record).startswith("/goal Controlled diagnostic.")


def test_old_and_new_goal_formats_coexist_in_one_rollout():
    records = [_thread_goal_updated("active", "Old objective"), _goal_context("Old objective"),
               _thread_goal_updated("active", "New objective"), objective_output("New objective"), new_context()]
    assert scan(records)[0] == ["/goal Old objective", "/goal New objective"]


def test_recompute_of_private_new_format_rewrites_keeps_only_one_boundary():
    records = capture()[:5]
    first = _batch_compute()
    for line, record in enumerate(records, 1):
        first.transform_inline(record, session_id="compute-session", line_num=line)
    assert user_message_text(records[-1]).startswith("/goal Controlled diagnostic.")
    assert scan([*records, deepcopy(records[-1])])[0] == [user_message_text(records[-1])]


@pytest.mark.parametrize("extra", [{"namespace": "other"}, {"name": "other"}, {"call_id": "actual-call"}])
def test_other_tool_outputs_do_not_supply_a_goal_objective(extra):
    record = objective_output()
    record["payload"].update(extra)
    assert scan([record, new_context()])[0] == []


def test_new_named_objective_supersedes_an_older_goal_snapshot():
    records = [_thread_goal_updated("active", "First"), new_context(),
               _thread_goal_updated("complete", "First"), objective_output("Second"), new_context()]
    assert scan(records)[0] == ["/goal First", "/goal Second"]


def test_embedded_edit_without_status_event_supersedes_old_snapshot():
    records = [_thread_goal_updated("active", "First"), new_context(), _goal_context("Second"), new_context()]
    assert scan(records)[0] == ["/goal First", "/goal Second"]


def test_restart_compares_tool_snapshot_with_objective_at_previous_continuation(goal_session):
    records = [_thread_goal_updated("active", "First"), new_context(), new_context()]
    compute = _batch_compute()
    for line, record in enumerate(records, 1):
        compute.transform_inline(record, session_id="compute-session", line_num=line)
        _persist_line(goal_session, line, record)
    result = capture()[5]
    output = orjson.loads(result["payload"]["output"][1]["text"])
    output["goal"]["objective"] = "Second"
    result["payload"]["output"][1]["text"] = orjson.dumps(output).decode()
    _persist_line(goal_session, 4, result)
    assert scan([_thread_goal_updated("active", "First"), new_context(), new_context(), result, new_context()])[0] == [
        "/goal First", "/goal Second",
    ]
    record = new_context()
    CodexSessionCompute().transform_inline(record, session_id=goal_session.id, line_num=5)
    assert user_message_text(record) == "/goal Second"


def test_restart_keeps_embedded_edit_before_a_hidden_new_continuation(goal_session):
    records = [_thread_goal_updated("active", "First"), new_context(), _goal_context("Second"), new_context()]
    compute = _batch_compute()
    for line, record in enumerate(records, 1):
        compute.transform_inline(record, session_id="compute-session", line_num=line)
        _persist_line(goal_session, line, record)
    record = objective_output("Second")
    restarted = CodexSessionCompute()
    restarted.transform_inline(record, session_id=goal_session.id, line_num=5)
    continuation = new_context()
    restarted.transform_inline(continuation, session_id=goal_session.id, line_num=6)
    assert user_message_text(continuation) is None
