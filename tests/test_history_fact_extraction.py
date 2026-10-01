"""Shared chronological fact extraction and batch/live parity."""

from tests.live_sync_helpers import drain_live_sync

from queue import Queue

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionHistoryFact, SessionItem
from twicc.providers.history_facts import HistoryFactContext, HistoryFactKind
from twicc.providers.codex.history_facts import extract_history_facts as codex_facts
from twicc.providers.claude_code.history_facts import extract_history_facts as claude_facts


def call(id, name="exec_command", **extra):
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "call_id": id,
            "name": name,
            "arguments": "{}",
            **extra,
        },
    }


def output(id, text, custom=False):
    return {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output" if custom else "function_call_output",
            "call_id": id,
            "output": text,
        },
    }


def replay(records, provider=Provider.CODEX):
    history = HistoryFactContext(provider)
    extract = codex_facts if provider == Provider.CODEX else claude_facts
    facts = []
    for line, parsed in enumerate(records, 1):
        current = extract(parsed, line_num=line, history=history)
        history.register(parsed, current, line_num=line)
        facts.extend(current)
    return facts, history


def family(facts, kind):
    return [fact for fact in facts if fact.kind == kind]


@pytest.mark.parametrize("identifier", [None, "", " ", 1, True, {}, []])
def test_invalid_call_identifiers(identifier):
    assert replay([call(identifier)])[0] == []


def test_wait_agent_and_reused_calls_are_retained_with_strict_bounds():
    facts, history = replay([call("same"), call("same", "wait_agent")])
    assert [f.line_num for f in family(facts, HistoryFactKind.TOOL_CALL)] == [1, 2]
    assert history.lookup_tool_call("same", before_line=2)[0]["name"] == "exec_command"
    assert history.lookup_tool_call("same", before_line=3)[0]["name"] == "wait_agent"
    assert history.lookup_tool_call("same", before_line=1) is None


@pytest.mark.parametrize("name, expected", [("exec_command", 1), ("write_stdin", 0)])
def test_process_announcement_requires_starter(name, expected):
    facts, _ = replay([call("a", name), output("a", "Process running with session ID 42")])
    starts = family(facts, HistoryFactKind.PROCESS_START)
    assert len(starts) == expected
    if starts:
        assert starts[0].key == "42"
        assert starts[0].data == {"call_id": "a", "call_line": 1}


def test_wait_cell_exec_chain_uses_output_bound_even_after_owner_id_reuse():
    records = [
        call("exec", type="custom_tool_call", name="exec", input="await tools.write_stdin({session_id:42})"),
        output("exec", "Script running with cell ID cell\nWall time 0.1 seconds\nOutput:\n", custom=True),
        call("exec", type="custom_tool_call", name="exec", input='await tools.exec_command({cmd:"private command"})'),
        call("wait", "wait", arguments='{"cell_id":"cell"}'),
        output("wait", "Script completed\nWall time 0.1 seconds\nOutput:\nSESSION_ID=42"),
    ]
    facts, history = replay(records)
    assert family(facts, HistoryFactKind.PROCESS_START)[0].data == {"call_id": "exec", "call_line": 3}
    assert history.lookup_code_cell("cell", before_line=2) is None
    assert history.lookup_code_cell("cell", before_line=5) == ("exec", 2)
    assert "private command" not in orjson.dumps([f.data for f in facts]).decode()


def test_cell_announcements_require_custom_output_and_exact_identifier():
    facts, history = replay(
        [
            output("exec", "Script running with cell ID 23\nWall time 0.1 seconds\nOutput:\n", custom=True),
            output("wait", "Script running with cell ID 23\nWall time 0.1 seconds\nOutput:\n"),
        ]
    )
    assert len(family(facts, HistoryFactKind.CODE_CELL)) == 1
    assert history.lookup_code_cell("2", before_line=3) is None
    assert history.lookup_code_cell("23", before_line=3) == ("exec", 1)


def test_claude_multiple_calls_and_same_line_duplicates():
    parsed = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "large secret"}},
                {"type": "tool_use", "id": "b", "name": "Agent", "input": {"run_in_background": True}},
                {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "secret"}},
                {"type": "tool_use", "id": [], "name": "Read"},
            ]
        },
    }
    facts, history = replay([parsed], Provider.CLAUDE_CODE)
    assert [f.key for f in facts] == ["a", "b"]
    assert facts[0].data["blocks"] == [0, 2]
    assert facts[1].data["is_background"] is True
    assert history.lookup_tool_call("a", before_line=2)[0]["name"] == "Read"
    assert "large secret" not in orjson.dumps([f.data for f in facts]).decode()


def test_target_and_context_families_use_compact_source_pointers():
    goal = {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": '<codex_internal_context source="goal"><objective>Large objective</objective></codex_internal_context>',
            }
        ],
    }
    records = [
        call(
            "e",
            type="custom_tool_call",
            name="exec",
            input='await tools.apply_patch("*** Begin Patch\\n*** Add File: x.py\\n+x\\n*** End Patch");'
            'await tools.mcp__server__tool({secret:"large argument"});',
        ),
        {"type": "turn_context", "payload": {"collaboration_mode": {"mode": "plan"}, "large": "secret"}},
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "/plan"}]},
        },
        {"type": "response_item", "payload": goal},
        {
            "type": "event_msg",
            "payload": {"type": "thread_goal_updated", "goal": {"status": "active", "objective": "Large objective"}},
        },
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 42}}}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn"}},
    ]
    facts, _ = replay(records)
    assert {f.kind for f in facts} == {
        HistoryFactKind.TOOL_CALL,
        HistoryFactKind.CODE_EXEC_TARGET,
        HistoryFactKind.TURN_CONTEXT,
        HistoryFactKind.PLAN_MARKER,
        HistoryFactKind.GOAL_CONTEXT,
        HistoryFactKind.GOAL_UPDATE,
        HistoryFactKind.TOKEN_USAGE,
        HistoryFactKind.TURN_START,
    }
    assert [f.key for f in family(facts, HistoryFactKind.CODE_EXEC_TARGET)] == ["patch", "mcp"]
    assert family(facts, HistoryFactKind.GOAL_CONTEXT)[0].data == {"source_line": 4}
    assert "Large objective" not in orjson.dumps([f.data for f in facts]).decode()


@pytest.mark.django_db
@pytest.mark.parametrize("provider", [Provider.CLAUDE_CODE, Provider.CODEX])
def test_batch_whole_chunk_and_per_line_live_parity(provider, tmp_path):
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.codex.compute import CodexSessionCompute

    cls = CodexSessionCompute if provider == Provider.CODEX else ClaudeCodeSessionCompute
    if provider == Provider.CODEX:
        records = [call("a"), output("a", "Process running with session ID 42"), call("a", "wait_agent")]
    else:
        records = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "a", "name": "Agent", "input": {"prompt": "work"}}]},
            }
        ]
    project = Project.objects.create(id="facts")
    snapshots = []
    for mode in ["batch", "chunk", "line"]:
        session = Session.objects.create(id=mode, project=project, provider=provider, file_path=f"{mode}.jsonl")
        compute = cls()
        path = tmp_path / f"{mode}.jsonl"
        if mode == "batch":
            for n, parsed in enumerate(records, 1):
                SessionItem.objects.create(session=session, line_num=n, content=orjson.dumps(parsed).decode())
            queue = Queue()
            compute.compute_session_metadata(session.id, queue, 1)
            messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
            snapshots.append(next(m for m in messages if m["type"] == "session_complete")["history_facts"])
        else:
            chunks = [records] if mode == "chunk" else [[record] for record in records]
            for chunk in chunks:
                with path.open("ab") as stream:
                    for record in chunk:
                        stream.write(orjson.dumps(record) + b"\n")
                drain_live_sync(compute, session, path)
            snapshots.append(
                list(
                    SessionHistoryFact.objects.filter(session=session)
                    .order_by("line_num", "kind", "key")
                    .values("line_num", "kind", "key", "data")
                )
            )
    assert snapshots[0] == snapshots[1] == snapshots[2]


def test_exact_spawn_paths_and_spawn_kind():
    def activity(path, kind="started"):
        return {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "SubAgentActivity",
                    "id": "spawn",
                    "agent_thread_id": "agent",
                    "agent_path": path,
                    "kind": kind,
                },
            },
        }

    facts, _ = replay([activity("/root/a"), activity("/root/ab"), activity("/root/a", "interacted")])
    assert [(f.key, f.data) for f in family(facts, HistoryFactKind.AGENT_SPAWN)] == [
        ("/root/a", {"call_id": "spawn", "agent_id": "agent"}),
        ("/root/ab", {"call_id": "spawn", "agent_id": "agent"}),
    ]


def test_goal_and_plan_facts_survive_private_normalization():
    goal_payload = {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": '<codex_internal_context source="goal"><objective>private objective</objective></codex_internal_context>',
            }
        ],
    }
    plan_payload = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "/plan"}]}
    for original, kind in [(goal_payload, HistoryFactKind.GOAL_CONTEXT), (plan_payload, HistoryFactKind.PLAN_MARKER)]:
        raw = {"type": "response_item", "payload": original}
        rewritten = {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "UserMessage", "id": "u", "content": [{"type": "text", "text": "display text"}]},
            },
            "twiccOriginalContent": original,
        }
        assert replay([raw])[0] == replay([rewritten])[0]
        assert replay([rewritten])[0][0].kind == kind


def test_claude_original_attachment_does_not_invent_a_tool_call():
    parsed = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "done"}]},
        "twiccOriginalEntry": orjson.dumps({"type": "attachment", "attachment": {"type": "queued_command"}}).decode(),
    }
    assert replay([parsed], Provider.CLAUDE_CODE)[0] == []


@pytest.mark.django_db
def test_live_context_ignores_partial_persisted_facts_and_keeps_bounds():
    from twicc.providers.history_facts import HistoryFact, append_history_facts

    project = Project.objects.create(id="stale")
    session = Session.objects.create(id="stale", project=project, provider=Provider.CODEX, file_path="stale.jsonl")
    records = [call("same"), call("same", "write_stdin"), output("same", "Process running with session ID 42")]
    for line, parsed in enumerate(records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(parsed).decode())
    append_history_facts(session.id, [HistoryFact(1, HistoryFactKind.TOOL_CALL, "same", {"tool_name": "exec_command"})])
    history = HistoryFactContext(Provider.CODEX, session_id=session.id)
    assert history.lookup_tool_call("same", before_line=2)[1] == 1
    assert history.lookup_tool_call("same", before_line=3)[1] == 2
    assert codex_facts(records[2], line_num=3, history=history) == []


@pytest.mark.django_db
def test_fact_failure_rolls_back_items_and_checkpoint(tmp_path, monkeypatch):
    from twicc.providers.codex.compute import CodexSessionCompute

    project = Project.objects.create(id="rollback")
    session = Session.objects.create(
        id="rollback", project=project, provider=Provider.CODEX, file_path="rollback.jsonl"
    )
    path = tmp_path / "rollback.jsonl"
    path.write_bytes(orjson.dumps(call("a")) + b"\n")

    def fail(*args):
        raise RuntimeError("fact insertion failed")

    monkeypatch.setattr("twicc.providers.compute_base.append_history_facts", fail)
    with pytest.raises(RuntimeError, match="fact insertion failed"):
        drain_live_sync(CodexSessionCompute(), session, path)
    session.refresh_from_db()
    assert session.last_offset == 0
    assert not session.items.exists()
    assert not SessionHistoryFact.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_batch_reuses_target_classification_without_replaying_analysis(monkeypatch):
    from twicc.providers.codex import compute as codex_compute

    project = Project.objects.create(id="once")
    session = Session.objects.create(id="once", project=project, provider=Provider.CODEX, file_path="once.jsonl")
    record = call("exec", type="custom_tool_call", name="exec", input="await tools.mcp__server__tool({})")
    SessionItem.objects.create(session=session, line_num=1, content=orjson.dumps(record).decode())
    compute = codex_compute.CodexSessionCompute()
    analyze = compute.analyze_content
    targets = codex_compute._script_targets
    counts = {"analysis": 0, "targets": 0}

    def count_analysis(*args, **kwargs):
        counts["analysis"] += 1
        return analyze(*args, **kwargs)

    def count_targets(*args, **kwargs):
        counts["targets"] += 1
        return targets(*args, **kwargs)

    monkeypatch.setattr(compute, "analyze_content", count_analysis)
    monkeypatch.setattr(codex_compute, "_script_targets", count_targets)
    queue = Queue()
    compute.compute_session_metadata(session.id, queue, 1)
    assert counts == {"analysis": 1, "targets": 1}
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    facts = next(m for m in messages if m["type"] == "session_complete")["history_facts"]
    assert any(f["kind"] == HistoryFactKind.CODE_EXEC_TARGET for f in facts)


def test_record_evidence_is_line_bound_and_expires_on_registration():
    history = HistoryFactContext(Provider.CODEX)
    history.set_record_evidence(1, {"tool_name": "exec_command"})
    assert history.record_evidence(line_num=2) == {}
    assert history.record_evidence(line_num=1) == {"tool_name": "exec_command"}
    history.register(call("a"), [], line_num=1)
    assert history.record_evidence(line_num=1) == {}


def test_shared_targets_preserve_combined_latest_fifty_legacy_candidates():
    from twicc.providers.codex.compute import CodexSessionCompute

    compute = CodexSessionCompute()
    history = HistoryFactContext(Provider.CODEX)
    for line in range(1, 53):
        script = (
            "await tools.mcp__server__tool({})"
            if line % 2
            else 'await tools.apply_patch("*** Begin Patch\\n*** Add File: x.py\\n+x\\n*** End Patch")'
        )
        parsed = call(str(line), type="custom_tool_call", name="exec", input=script)
        analysis = compute.analyze_content(parsed, session_id="bounded", tool_use_map={})
        history.set_record_evidence(line, analysis.history_evidence)
        facts = compute.extract_history_facts(parsed, line_num=line, history=history)
        history.register(parsed, facts, line_num=line)
    assert [identifier for identifier, _ in compute._code_exec_targets["bounded"]] == [
        str(line) for line in range(3, 53)
    ]
    assert len(family(history.facts, HistoryFactKind.CODE_EXEC_TARGET)) == 52
