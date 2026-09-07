"""Whole-tree state through the owner API and shared query boundary."""
import asyncio
from datetime import datetime, timedelta, UTC

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.models import AgentLink, Project, Session, SessionItem, SessionType, ToolResultLink
from twicc.core.session_queries import build_subagents_state
from twicc.providers.helpers import get_provider_helpers

NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)


@pytest.fixture
def tree(transactional_db, settings):
    project = Project.objects.create(id="tree-project", directory="/tmp/tree-project")
    root = Session.objects.create(id="tree-root", project=project, file_path="tree/root.jsonl",
        provider="claude_code", compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION,
        created_at=NOW, last_line=200, user_message_count=1)
    children = []
    for sid in ("tree-launcher", "tree-child"):
        children.append(Session.objects.create(id=sid, project=project, file_path=f"tree/{sid}.jsonl",
            provider="claude_code", parent_session=root, type=SessionType.SUBAGENT,
            compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION, created_at=NOW, last_line=100))
    launcher, child = children
    for owner, target, tool, line, background in (
        (root, launcher, "spawn-launcher", 115, False), (launcher, child, "spawn-child", 60, True),
    ):
        AgentLink.objects.create(session=owner, agent_id=target.id, tool_use_id=tool,
            tool_use_line_num=line, started_at=NOW, is_background=background)
        ToolResultLink.objects.create(session=owner, tool_use_id=tool, tool_use_line_num=line,
            tool_result_line_num=line + 1, tool_result_at=NOW, tool_name="Agent")
        SessionItem.objects.create(session=owner, line_num=line, content="{}", display_level=1,
            kind="assistant_message", timestamp=NOW)
    SessionItem.objects.create(session=child, line_num=1, content="{}", display_level=1,
        kind="user_message", timestamp=NOW)
    return root, launcher, child


def queue(root, child, tool="spawn-child", line=120, timestamp=NOW, status="completed"):
    xml = (f"<task-notification><task-id>{child.id}</task-id><tool-use-id>{tool}</tool-use-id>"
           f"<status>{status}</status><result>done</result></task-notification>")
    return SessionItem.objects.create(session=root, line_num=line, timestamp=timestamp,
        content=orjson.dumps({"type": "queue-operation", "operation": "enqueue", "content": xml}).decode())


def test_owner_api_returns_all_launchers(tree):
    root, launcher, child = tree
    response = asyncio.run(AsyncClient().get(f"/api/projects/{root.project_id}/sessions/{root.id}/subagents/"))
    assert response.status_code == 200
    rows = {row["agent_id"]: row for row in response.json()}
    assert rows[launcher.id]["running"] is False
    assert rows[child.id]["running"] is True
    assert rows[child.id]["owner_session_id"] == launcher.id
    assert rows[child.id]["root_session_id"] == root.id
    assert asyncio.run(AsyncClient().get(
        f"/api/projects/{root.project_id}/sessions/{launcher.id}/subagents/"
    )).status_code == 404


def test_completion_identity_latest_and_nonterminal(tree):
    root, launcher, child = tree
    queue(root, launcher)  # same tool id but unrelated child
    queue(root, child, line=121, status="running")
    assert build_subagents_state(root)[1]["running"] is True
    later = NOW + timedelta(seconds=10)
    queue(root, child, line=122, timestamp=later)
    queue(root, child, line=123, timestamp=NOW)
    entry = build_subagents_state(root)[1]
    assert entry["running"] is False
    assert entry["stopped_at"] == later.isoformat()


def test_child_idle_is_provider_gated(tree):
    root, launcher, child = tree
    child.last_stopped_at = NOW
    child.save(update_fields=["last_stopped_at"])
    entry = build_subagents_state(root)[1]
    assert entry["agent_stopped_at"] == NOW.isoformat()
    assert entry["stopped_at"] is None
    assert entry["running"] is True
    root.provider = "codex"
    assert build_subagents_state(root)[1]["running"] is False
    assert get_provider_helpers("codex").subagent_idle_trusted is True
    assert get_provider_helpers("claude_code").subagent_idle_trusted is False


def test_only_root_cutoff_ends_tree(tree):
    root, launcher, child = tree
    launcher.last_stopped_at = NOW + timedelta(seconds=20)
    launcher.save(update_fields=["last_stopped_at"])
    assert build_subagents_state(root)[1]["running"] is True
    root.last_started_at = NOW + timedelta(seconds=30)
    assert build_subagents_state(root)[1]["running"] is False


def test_cross_project_owner_and_missing_child_row(tree):
    root, launcher, child = tree
    launcher.project = Project.objects.create(id="elsewhere", directory="/tmp/elsewhere")
    launcher.save(update_fields=["project"])
    child.delete()
    entry = build_subagents_state(root)[1]
    assert entry["owner_session_id"] == launcher.id
    assert entry["agent_slug"] is None
    assert entry["running"] is True


class _SpawnItem:
    """Minimal stand-in for the launcher's ``SessionItem`` (only ``content`` is read)."""

    def __init__(self, content):
        self.content = content


def spawn_call(owner, line, tool, **input_fields):
    """Turn a fixture placeholder item into the real spawning tool_use."""
    SessionItem.objects.filter(session=owner, line_num=line).update(
        content=orjson.dumps({"message": {"content": [
            {"type": "tool_use", "id": tool, "name": "Agent", "input": input_fields},
        ]}}).decode()
    )


def test_display_name_reads_the_launchers_spawn_call(tree):
    root, launcher, child = tree
    spawn_call(root, 115, "spawn-launcher", subagent_type="Explore", description="Map the sidebar")
    spawn_call(launcher, 60, "spawn-child", subagent_type="general-purpose", description="Verify the claims")
    rows = {row["agent_id"]: row for row in build_subagents_state(root)}
    assert rows[launcher.id]["display_name"] == "Explore — Map the sidebar"
    # ``general-purpose`` names nothing, so the description carries alone —
    # and the nested agent resolves from its own launcher's transcript.
    assert rows[child.id]["display_name"] == "Verify the claims"


def test_display_name_sentence_cases_an_identifier_description(tree):
    root, launcher, child = tree
    spawn_call(root, 115, "spawn-launcher", subagent_type="general-purpose", description="backend-reader")
    spawn_call(launcher, 60, "spawn-child", subagent_type="code-reviewer", description="Launcher: spawn two readers")
    rows = {row["agent_id"]: row for row in build_subagents_state(root)}
    # An identifier-shaped description reads like a Codex task name...
    assert rows[launcher.id]["display_name"] == "Backend reader"
    # ...but a real sentence stays as written, and the type is cased too.
    assert rows[child.id]["display_name"] == "Code reviewer — Launcher: spawn two readers"


def test_display_name_stays_none_without_a_usable_call(tree):
    root, _, _ = tree
    assert build_subagents_state(root)[0]["display_name"] is None


def test_codex_display_name_reads_the_v2_task_name():
    helpers = get_provider_helpers("codex")
    call = lambda tool, args: _SpawnItem(orjson.dumps({"payload": {  # noqa: E731
        "name": "spawn_agent", "call_id": tool, "arguments": orjson.dumps(args).decode(),
    }}).decode())
    assert helpers.get_spawn_display_name(call("call_1", {"task_name": "frontend_reader"}), "call_1") == "Frontend reader"
    # A v1 spawn carries only its prompt, so nothing names the agent.
    assert helpers.get_spawn_display_name(call("call_2", {"fork_context": True}), "call_2") is None
    # Another call's item never names this one.
    assert helpers.get_spawn_display_name(call("call_3", {"task_name": "other"}), "call_1") is None
