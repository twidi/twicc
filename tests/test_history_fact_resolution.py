"""Historical resolvers select current facts or bounded stale raw history exclusively."""

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.models import Project, Session, SessionItem
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.history_facts import HistoryFact, HistoryFactContext, HistoryFactKind, append_history_facts
from twicc.providers.helpers import get_provider_helpers
from tests.test_history_fact_extraction import call, output, replay


@pytest.fixture
def session(db):
    return Session.objects.create(id="resolution", project=Project.objects.create(id="resolution"), provider="codex")


def persist(session, records, current):
    for line, record in enumerate(records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(record).decode())
    facts, _ = replay(records, session.provider)
    # A stale index deliberately retains older occurrences but misses each
    # newest reuse. Taking a partial fact hit would produce the wrong owner.
    partial = [fact for fact in facts if any(
        newer.kind == fact.kind and newer.key == fact.key and newer.line_num > fact.line_num
        for newer in facts
    )]
    append_history_facts(session.id, facts if current else partial)
    session.compute_version = get_provider_helpers(session.provider).current_compute_version - (not current)
    session.save(update_fields=["compute_version"])


def assert_sql(queries):
    for query in queries:
        sql = query["sql"].upper()
        assert " LIKE " not in sql
        if 'FROM "CORE_SESSIONITEM"' in sql:
            assert " LIMIT " in sql, sql


@pytest.mark.parametrize("current", [False, True])
def test_call_reuse_control_calls_and_strict_bounds(session, current):
    records = [call("same"), call("same", "wait_agent"), {"text": "same"}, call("same", "future")]
    persist(session, records, current)
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_tool_call(session.id, 4, "same") == (records[1]["payload"], 2)
        assert compute._lookup_tool_call(session.id, 2, "same")[1] == 1
        assert compute._lookup_tool_call(session.id, 5, "absent") is None
    assert_sql(queries)


@pytest.mark.parametrize("current", [False, True])
def test_cell_and_process_reuse_ignore_poll_outputs(session, current):
    records = [call("old"), output("old", "Process running with session ID 42"),
               call("new"), output("new", "Process running with session ID 42"),
               call("poll", "write_stdin"), output("poll", "Process running with session ID 42"),
               output("exec", "Script running with cell ID 2\nWall time 0.1 seconds\nOutput:\n", custom=True),
               output("poll", "Script running with cell ID 2\nWall time 0.1 seconds\nOutput:\n")]
    persist(session, records, current)
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_exec_command_call_id(session.id, 9, 42, "fallback") == "new"
        assert compute._lookup_code_cell_call_id(session.id, 9, "2", "fallback") == "exec"
        assert compute._lookup_code_cell_call_id(session.id, 9, "23", "fallback") == "fallback"
    assert_sql(queries)


def test_current_absence_is_final_and_next_operation_refreshes_readiness(session):
    persist(session, [call("raw-only")], True)
    session.history_facts.all().delete()
    compute = CodexSessionCompute()
    history = HistoryFactContext("codex", session_id=session.id)
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_tool_call(session.id, 2, "raw-only") is None
        assert history.lookup_tool_call("raw-only", before_line=2) is None
    assert not any('FROM "core_sessionitem"' in q["sql"] for q in queries)
    Session.objects.filter(id=session.id).update(compute_version=0)
    assert compute._lookup_tool_call(session.id, 2, "raw-only")[1] == 1
    assert history.lookup_tool_call("raw-only", before_line=2)[1] == 1


def event(kind, **payload):
    return {"type": "event_msg", "payload": {"type": kind, **payload}}


def user(text):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
    }}


@pytest.mark.parametrize("current", [False, True])
def test_spawn_and_turn_exact_keys_and_future_bounds(session, current):
    def spawn(id, path, kind="started"):
        return event("item_completed", item={"type": "SubAgentActivity", "id": id,
                     "agent_thread_id": id, "agent_path": path, "kind": kind})
    records = [spawn("old", "/root/a"), spawn("new", "/root/a"), spawn("prefix", "/root/ab"),
               spawn("poll", "/root/a", "interacted"), event("task_started", turn_id="t"),
               event("task_started", turn_id="t"), event("task_started", turn_id="future")]
    persist(session, records, current)
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_spawn_for_agent_path(session.id, 5, "/root/a").call_id == "new"
        assert compute._lookup_spawn_for_agent_path(session.id, 2, "/root/a").call_id == "old"
        assert compute._lookup_spawn_for_agent_path(session.id, 5, "/root") is None
        assert compute._lookup_task_started_line(session.id, 6, "t") == 5
        assert compute._lookup_task_started_line(session.id, 7, "t") == 6
        assert compute._lookup_task_started_line(session.id, 7, "future") is None
    assert_sql(queries)


@pytest.mark.parametrize("current", [False, True])
def test_plan_goal_and_token_cold_contexts(session, current):
    records = [{"type": "turn_context", "payload": {"collaboration_mode": {"mode": "plan"}}},
               {"type": "turn_context", "payload": {"collaboration_mode": {"mode": "default"}}},
               event("item_completed", item={"type": "UserMessage", "id": "plan",
                     "content": [{"type": "text", "text": "/plan", "text_elements": []}]}),
               user('<codex_internal_context source="goal"><objective>Old</objective></codex_internal_context>'),
               user('<codex_internal_context source="goal"><objective>New</objective></codex_internal_context>'),
               event("thread_goal_updated", goal={"status": "active"}),
               event("token_count", info={"total_token_usage": {"total_tokens": 40}}),
               event("token_count", info={"total_token_usage": {"total_tokens": 50}}),
               event("token_count", info=[])]
    persist(session, records, current)
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_prev_plan_context(session.id, 3) == ("default", False)
        assert compute._lookup_prev_plan_context(session.id, 10) == ("default", True)
        state = compute._lookup_prev_goal_context_state(session.id, 10)
        assert (state.seen_context, state.last_objective, state.show_next) == (True, "New", True)
        assert not compute._lookup_prev_goal_context_state(session.id, 6).show_next
        assert compute._lookup_prev_total_tokens(session.id, 8) == 40
        assert compute._lookup_prev_total_tokens(session.id, 10) == 50
    assert_sql(queries)


@pytest.mark.parametrize("current", [False, True])
def test_orphan_target_exhaustive_matching_and_recency_fallback(session, current):
    records = [call("old", "exec", type="custom_tool_call",
                    input='await tools.mcp__server__old({});'),
               *[call(f"new-{i}", "exec", type="custom_tool_call",
                      input='await tools.mcp__server__new({});') for i in range(55)],
               call("patch", "exec", type="custom_tool_call",
                    input='await tools.apply_patch("*** Begin Patch\\n*** Add File: x.py\\n+x\\n*** End Patch");')]
    persist(session, records, current)
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        lookup = compute._lookup_orphan_end_exec_call_id
        assert lookup(session.id, 58, "fallback", event_type="McpToolCall", mcp_qualified="mcp__server__old") == "old"
        assert lookup(session.id, 58, "fallback", event_type="McpToolCall", mcp_qualified="absent") == "new-54"
        assert lookup(session.id, 58, "fallback", event_type="FileChange", changes={"/tmp/x.py": {}}) == "patch"
        assert lookup(session.id, 57, "fallback", event_type="FileChange", changes={"x.py": {}}) == "fallback"
    assert_sql(queries)


def test_invalid_indexed_source_does_not_hide_older_valid_call(session):
    persist(session, [call("a"), {"type": "response_item", "payload": {"type": "message", "call_id": "a"}}], True)
    append_history_facts(session.id, [HistoryFact(2, HistoryFactKind.TOOL_CALL, "a", {})])
    assert CodexSessionCompute()._lookup_tool_call(session.id, 3, "a")[1] == 1


def test_stale_absence_exhausts_bounded_pages(session):
    persist(session, [call("old"), *[{"text": "noise"} for _ in range(260)]], False)
    with CaptureQueriesContext(connection) as queries:
        assert CodexSessionCompute()._lookup_tool_call(session.id, 262, "old")[1] == 1
        assert CodexSessionCompute()._lookup_tool_call(session.id, 262, "absent") is None
    assert_sql(queries)
    raw_queries = [q for q in queries if 'FROM "core_sessionitem"' in q["sql"]]
    assert len(raw_queries) == 6


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
@pytest.mark.parametrize("current", [False, True])
def test_generic_tool_agent_and_spawn_recovery(session, provider, current):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from tests.test_nested_agent_compute import ack, spawn

    session.provider = provider
    session.save(update_fields=["provider"])
    if provider == "codex":
        records = [call("spawn", "spawn_agent", arguments='{"message":"work"}'),
                   output("spawn", '{"agent_id":"child"}')]
        compute = CodexSessionCompute()
    else:
        records = [spawn("spawn", "work"), ack("ad123", "spawn")]
        compute = ClaudeCodeSessionCompute()
    persist(session, records, current)
    item = session.items.get(line_num=2)
    with CaptureQueriesContext(connection) as queries:
        tool = compute.create_tool_result_link_live(session.id, item, records[1])
        agent = compute.create_agent_link_from_tool_result(session.id, item, records[1])
        spawns = list(compute._spawn_items(session.id))
    assert tool is not None
    assert agent.tool_use_line_num == 1
    assert [(item.line_num, id, prompt) for item, id, prompt, _ in spawns] == [(1, "spawn", "work")]
    assert_sql(queries)


@pytest.mark.parametrize("current", [False, True])
def test_claude_queue_recovery_is_explicit_bounded_raw_residual(session, current):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from tests.test_nested_agent_compute import queue_entry, spawn

    session.provider = "claude_code"
    session.save(update_fields=["provider"])
    records = [spawn("spawn"), queue_entry("child", "spawn"), *[{} for _ in range(130)]]
    persist(session, records, current)
    compute = ClaudeCodeSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        completions = list(compute._tree_queue_completions(session.id))
        assert [item.line_num for item, _ in completions] == [2]
        link = compute._resolve_queue_spawn(session, completions[0][1])
    assert link.tool_use_line_num == 1
    assert_sql(queries)


def test_batch_orphan_candidates_share_latest_fifty_limit():
    compute = CodexSessionCompute()
    compute.begin_session_compute("batch-targets")
    records = [call("old-mcp", "exec", type="custom_tool_call", input='await tools.mcp__server__old({});')]
    records.extend(call(f"patch-{i}", "exec", type="custom_tool_call",
                        input='await tools.apply_patch("*** Begin Patch\\n*** Add File: x.py\\n+x\\n*** End Patch");')
                   for i in range(50))
    for record in records:
        compute.analyze_content(record, session_id="batch-targets", tool_use_map={})
    assert [id for id, _ in compute._code_exec_targets["batch-targets"]] == [f"patch-{i}" for i in range(50)]
    end = event("item_completed", item={"type": "McpToolCall", "id": "exec-orphan",
                                       "invocation": {"server": "server", "tool": "old"}})
    assert compute._remap_orphan_end_event(end, "exec-orphan", session_id="batch-targets") == "exec-orphan"


def test_current_absence_is_final_for_all_context_families(session):
    persist(session, [call("call")], True)
    session.history_facts.all().delete()
    compute = CodexSessionCompute()
    with CaptureQueriesContext(connection) as queries:
        assert compute._lookup_task_started_line(session.id, 2, "t") is None
        assert compute._lookup_spawn_for_agent_path(session.id, 2, "/root/a") is None
        assert compute._lookup_code_cell_call_id(session.id, 2, "2", "fallback") == "fallback"
        assert compute._lookup_exec_command_call_id(session.id, 2, 42, "fallback") == "fallback"
        assert compute._lookup_orphan_end_exec_call_id(session.id, 2, "fallback", event_type="FileChange") == "fallback"
        assert compute._lookup_prev_plan_context(session.id, 2) == ("default", False)
        assert not compute._lookup_prev_goal_context_state(session.id, 2).seen_context
        assert compute._lookup_prev_total_tokens(session.id, 2) == 0
    assert not any('FROM "core_sessionitem"' in q["sql"] for q in queries)
