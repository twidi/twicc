"""Actual watcher broadcasts carry launcher, root, and persisted completion."""
import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock

import orjson
import pytest
from watchfiles import Change

from twicc.core.models import AgentLink, Project, Session, SessionType
from twicc.providers import sessions_watcher
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher


@pytest.mark.django_db(transaction=True)
def test_nested_launch_and_root_queue_completion_broadcast(provider_home, monkeypatch):
    project = Project.objects.create(id="p", directory="/tmp/nested-watch")
    root_id = "00000000-0000-4000-8000-000000000001"
    launcher_id, child_id = "abcdef123", "a123456"
    root = Session.objects.create(id=root_id, project=project, provider="claude_code", file_path=f"p/{root_id}.jsonl")
    launcher = Session.objects.create(id=launcher_id, project=project, provider="claude_code",
        type=SessionType.SUBAGENT, parent_session=root, file_path=f"p/{root_id}/subagents/agent-{launcher_id}.jsonl")
    Session.objects.create(id=child_id, project=project, provider="claude_code", type=SessionType.SUBAGENT,
        parent_session=root, file_path=f"p/{root_id}/subagents/agent-{child_id}.jsonl")
    now = datetime(2026, 8, 8, 12, tzinfo=UTC).isoformat()
    path = provider_home.claude / "projects" / launcher.file_path
    path.parent.mkdir(parents=True)
    entries = [
        {"type": "assistant", "timestamp": now, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "spawn-nested", "name": "Agent", "input": {"prompt": "work"}},
        ]}},
        {"type": "user", "timestamp": now, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "spawn-nested", "content":
             f"Async agent launched successfully.\nagentId: {child_id} (internal)"},
        ]}},
    ]
    path.write_bytes(b"\n".join(orjson.dumps(entry) for entry in entries) + b"\n")
    messages = AsyncMock()
    monkeypatch.setattr(sessions_watcher, "broadcast_message", messages)
    watcher = ClaudeCodeSessionsWatcher()
    parsed = asyncio.run(watcher.parse_session_file(path))
    assert parsed is not None
    asyncio.run(watcher.sync_and_broadcast(path, parsed, Change.modified, None))
    assert AgentLink.objects.get(agent_id=child_id).session_id == launcher.id
    links = [call.args[1] for call in messages.call_args_list if call.args[1]["type"] == "agent_link_created"]
    assert links and links[0]["root_session_id"] == root.id
    assert links[0]["parent_session_id"] == launcher.id
    root_path = provider_home.claude / "projects" / root.file_path
    root_path.write_bytes(orjson.dumps({"type": "user", "timestamp": now,
        "message": {"role": "user", "content": "Run nested agents"}}) + b"\n" + orjson.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": now,
        "content": f"<task-notification><task-id>{child_id}</task-id><tool-use-id>spawn-nested</tool-use-id>"
                   "<status>completed</status><result>done</result></task-notification>"}) + b"\n")
    parsed = asyncio.run(watcher.parse_session_file(root_path))
    asyncio.run(watcher.sync_and_broadcast(root_path, parsed, Change.modified, None))
    stops = [call.args[1] for call in messages.call_args_list if call.args[1]["type"] == "agent_stopped"]
    assert stops == [{"type": "agent_stopped", "agent_session_id": child_id,
        "stopped_at": now, "root_session_id": root.id}]
