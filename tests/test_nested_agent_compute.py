"""Nested Claude agent evidence across live ingestion and background compute."""
from datetime import UTC, datetime
from queue import Queue

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import AgentLink, Project, Session, SessionItem, SessionType
from twicc.providers.claude_code.compute import get_compute

NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)


def entry(role, content, **extra):
    return {"type": role, "timestamp": NOW.isoformat(), "message": {"role": role, "content": content}, **extra}


def spawn(tool="tool_nested", prompt="nested work"):
    return entry("assistant", [{"type": "tool_use", "id": tool, "name": "Agent", "input": {"prompt": prompt}}])


def ack(agent="ad123", tool="tool_nested"):
    return entry("user", [{"type": "tool_result", "tool_use_id": tool,
        "content": f"Async agent launched successfully.\nagentId: {agent} (internal)"}])


def queue_entry(agent="ad123", tool="tool_nested", status="completed"):
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": NOW.isoformat(), "content":
        f"<task-notification><task-id>{agent}</task-id><tool-use-id>{tool}</tool-use-id>"
        f"<status>{status}</status><result>done</result></task-notification>"}


@pytest.fixture
def tree(db, provider_home):
    project = Project.objects.create(id="nested-project")
    root = Session.objects.create(id="nested-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path="nested-project/nested-root.jsonl")
    def child(id):
        return Session.objects.create(id=id, project=project, provider=Provider.CLAUDE_CODE,
            type=SessionType.SUBAGENT, parent_session=root,
            file_path=f"nested-project/nested-root/subagents/agent-{id}.jsonl")
    return root, child("aa123"), child("ad123"), provider_home.claude / "projects"


def meta(tree, tool="tool_nested", launcher=None):
    root, owner, child, home = tree
    path = home / child.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".meta.json").write_bytes(orjson.dumps({"toolUseId": tool, "parentAgentId": launcher or owner.id}))


def seed(session, *entries):
    start = session.items.count()
    for n, parsed in enumerate(entries, start + 1):
        SessionItem.objects.create(session=session, line_num=n, content=orjson.dumps(parsed).decode(),
            timestamp=NOW, kind=get_compute().compute_item_kind(parsed))


def live(session, home, *entries):
    path = home / session.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for parsed in entries:
            f.write(orjson.dumps(parsed) + b"\n")
    return get_compute().sync_session_items_from_file(session, path)


def compute(session, apply=True):
    queue = Queue()
    get_compute().compute_session_metadata(session.id, queue, "nested-test")
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    msg = next(m for m in messages if m["type"] == "session_complete")
    if apply:
        assert get_compute().apply_session_complete(msg).outcome == "applied"
    return msg


def test_live_sidecar_then_ack_upgrades_before_stop(tree):
    root, owner, child, home = tree
    meta(tree)
    live(owner, home, spawn())
    live(child, home, entry("user", "nested work", agentId=child.id))
    assert AgentLink.objects.get(agent_id=child.id).session_id == owner.id
    result = live(owner, home, ack())
    assert AgentLink.objects.get(agent_id=child.id).is_background
    assert result[5] == []


def test_authoritative_sidecar_waits_for_exact_tool(tree):
    root, owner, child, home = tree
    meta(tree)
    live(owner, home, spawn("old_tool"))
    live(child, home, entry("user", "nested work", agentId=child.id))
    assert not AgentLink.objects.filter(agent_id=child.id).exists()
    live(owner, home, spawn())
    assert AgentLink.objects.get(agent_id=child.id).tool_use_id == "tool_nested"


def test_recompute_prompt_root_keeps_own_link(tree):
    root, owner, child, home = tree
    seed(root, spawn())
    seed(child, entry("user", "nested work"))
    compute(root)
    original = AgentLink.objects.get(agent_id=child.id).id
    msg = compute(root)
    assert AgentLink.objects.get(agent_id=child.id).id == original
    assert not msg.get("agent_links_to_update")
    assert not msg.get("agent_links_to_create")
    assert not msg.get("agent_links_to_delete")


def test_recompute_foreign_sidecar_never_prompt_matches(tree):
    root, owner, child, home = tree
    meta(tree)
    seed(root, spawn("other_tool"))
    seed(child, entry("user", "nested work"))
    compute(root)
    assert not AgentLink.objects.exists()


def test_recompute_ack_and_backfill_interleaving(tree):
    root, owner, child, home = tree
    seed(owner, spawn(), ack())
    seed(root, queue_entry())
    owner_message = compute(owner, apply=False)
    compute(root)
    assert AgentLink.objects.get(agent_id=child.id).session_id == owner.id
    assert get_compute().apply_session_complete(owner_message).outcome == "applied"
    assert AgentLink.objects.filter(agent_id=child.id).count() == 1
    compute(owner)
    compute(root)
    assert AgentLink.objects.filter(agent_id=child.id).count() == 1


def test_queue_terminal_parser():
    from twicc.providers.claude_code.notifications import parse_queue_completion
    assert parse_queue_completion(queue_entry()).task_id == "ad123"
    assert parse_queue_completion(queue_entry(status="running")) is None


def test_live_queue_only_completion_and_missing_child_transport(tree):
    root, owner, child, home = tree
    live(owner, home, spawn())
    child.delete()
    result = live(root, home, queue_entry())
    assert result[2][0].parent_session_id == owner.id
    assert result[5][0].agent_session_id == "ad123"
    assert not Session.objects.filter(id="ad123").exists()


def test_live_queue_rejects_foreign_child_and_nonterminal(tree):
    root, owner, child, home = tree
    child.parent_session = None
    child.save(update_fields=["parent_session"])
    assert live(root, home, queue_entry())[5] == []
    child.parent_session = root
    child.save(update_fields=["parent_session"])
    assert live(root, home, queue_entry(status="running"))[5] == []


def test_sidecar_cannot_claim_existing_foreign_child(tree):
    root, owner, child, home = tree
    meta(tree)
    child.parent_session = None
    child.save(update_fields=["parent_session"])
    live(owner, home, spawn())
    assert not AgentLink.objects.exists()
    compute(owner)
    assert not AgentLink.objects.exists()


def test_same_prompt_on_two_launchers_is_ambiguous_without_meta(tree):
    root, owner, child, home = tree
    seed(root, spawn("root_tool"))
    seed(owner, spawn())
    seed(child, entry("user", "nested work"))
    compute(root)
    compute(owner)
    assert not AgentLink.objects.exists()


def test_root_backfill_without_ack_or_sidecar_survives_owner_recompute(tree):
    root, owner, child, home = tree
    seed(owner, spawn())
    seed(root, queue_entry())
    root_msg = compute(root, apply=False)
    compute(owner)
    assert get_compute().apply_session_complete(root_msg).outcome == "applied"
    first = AgentLink.objects.get().id
    compute(owner)
    assert AgentLink.objects.get().id == first
    assert AgentLink.objects.get().is_background


def test_structured_identity_wins_conflicting_ack(tree):
    root, owner, child, home = tree
    parsed = ack("ae456")
    parsed["toolUseResult"] = {"agentId": child.id, "isAsync": True}
    live(owner, home, spawn(), parsed)
    assert AgentLink.objects.get().agent_id == child.id
    compute(owner)
    assert AgentLink.objects.get().agent_id == child.id


def test_depth1_metadata_and_incomplete_sidecars(tree):
    from twicc.providers.claude_code.subagent_meta import read_subagent_metas, subagents_dir_for_file
    root, owner, child, home = tree
    directory = subagents_dir_for_file(child.file_path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"agent-{child.id}.meta.json").write_bytes(orjson.dumps({"toolUseId": "root_tool"}))
    (directory / "agent-acompact-001.meta.json").write_text("{}")
    (directory / "agent-ab666.meta.json").write_text("{broken")
    assert list(read_subagent_metas(directory)) == [child.id]
    assert get_compute().get_subagent_spawn_meta(child).launcher_session_id == root.id
    live(root, home, spawn("root_tool"))
    assert AgentLink.objects.get().session_id == root.id


def test_queue_parser_historical_and_malformed_payload():
    from twicc.providers.claude_code.notifications import parse_queue_completion
    parsed = queue_entry()
    parsed["content"] = parsed["content"].replace("<status>completed</status>", "").replace("done", "raw < text")
    assert parse_queue_completion(parsed).tool_use_id == "tool_nested"
    parsed["operation"] = "remove"
    assert parse_queue_completion(parsed) is None


def test_child_live_prompt_cannot_choose_between_launchers(tree):
    root, owner, child, home = tree
    seed(root, spawn("root_tool"))
    seed(owner, spawn())
    live(child, home, entry("user", "nested work", agentId=child.id))
    assert not AgentLink.objects.exists()


def test_queue_backfill_reads_uncomputed_launcher_history(tree):
    root, owner, child, home = tree
    SessionItem.objects.create(session=owner, line_num=1, content=orjson.dumps(spawn()).decode(), timestamp=NOW)
    seed(root, queue_entry())
    compute(root)
    assert AgentLink.objects.get().session_id == owner.id


def test_full_sidecar_sync_result_marks_child_stopped(tree):
    root, owner, child, home = tree
    meta(tree)
    seed(owner, spawn(), entry("user", [{"type": "tool_result", "tool_use_id": "tool_nested", "content": "done"}]))
    msg = compute(owner)
    assert not AgentLink.objects.get().is_background
    assert any(stop["agent_session_id"] == child.id for stop in msg["agent_stopped"])


def test_queue_upgrades_existing_matching_launch_only(tree):
    root, owner, child, home = tree
    meta(tree)
    live(owner, home, spawn())
    assert not AgentLink.objects.get().is_background
    result = live(root, home, queue_entry())
    assert AgentLink.objects.get().is_background
    assert result[2][0].is_background


def test_live_queue_ignores_stale_stop_after_child_activity(tree):
    from datetime import timedelta
    root, owner, child, home = tree
    child.last_updated_at = NOW + timedelta(seconds=10)
    child.save(update_fields=["last_updated_at"])
    assert live(root, home, queue_entry())[5] == []
    child.refresh_from_db()
    assert child.last_stopped_at is None


def test_queue_sendmessage_stops_without_creating_or_upgrading_launch(tree):
    root, owner, child, home = tree
    AgentLink.objects.create(session=owner, agent_id=child.id, tool_use_id="original", tool_use_line_num=1)
    data = entry("assistant", [{"type": "tool_use", "id": "continuation", "name": "SendMessage", "input": {"to": child.id}}])
    live(owner, home, data)
    result = live(root, home, queue_entry(tool="continuation"))
    assert len(result[5]) == 1
    assert result[2] == []
    assert AgentLink.objects.count() == 1
    assert not AgentLink.objects.get().is_background


def test_workflow_launcher_cannot_claim_sidecared_root_child(tree):
    root, owner, child, home = tree
    meta(tree, tool="root_tool", launcher=root.id)
    owner.file_path = "nested-project/nested-root/subagents/workflows/wf_test/agent-aa123.jsonl"
    owner.save(update_fields=["file_path"])
    seed(child, entry("user", "nested work"))
    live(owner, home, spawn())
    assert not AgentLink.objects.exists()


def test_child_live_prompt_rejects_same_prompt_siblings(tree):
    root, sibling, child, home = tree
    seed(root, spawn("root_tool"))
    seed(sibling, entry("user", "nested work"))
    result = live(child, home, entry("user", "nested work", agentId=child.id))
    assert result[2] == []
    assert not AgentLink.objects.exists()
    compute(root)
    assert not AgentLink.objects.exists()


def test_launcher_live_prompt_rejects_same_prompt_tools_in_batch(tree):
    root, owner, child, home = tree
    seed(child, entry("user", "nested work"))
    result = live(root, home, spawn("first_tool"), spawn("second_tool"))
    assert result[2] == []
    assert not AgentLink.objects.exists()
    compute(root)
    assert not AgentLink.objects.exists()


def test_child_live_authoritative_tool_wins_same_prompt_sibling(tree):
    root, sibling, child, home = tree
    meta(tree, tool="root_tool", launcher=root.id)
    seed(root, spawn("root_tool"), spawn("other_tool"))
    seed(sibling, entry("user", "nested work"))
    live(child, home, entry("user", "nested work", agentId=child.id))
    assert AgentLink.objects.get().agent_id == child.id
    assert AgentLink.objects.get().tool_use_id == "root_tool"
    compute(root)
    assert AgentLink.objects.get().agent_id == child.id
    assert AgentLink.objects.get().tool_use_id == "root_tool"
