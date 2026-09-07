"""Exercise Codex ancestry at ingestion boundaries and in the real migration."""

import asyncio
import importlib
import queue
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from watchfiles import Change

from twicc.core.models import Project, Session
from twicc.providers.codex.initial_sync import sync_all
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.db_writer import CreateSessionPayload
from twicc.providers.subagent_roots import resolve_flat_parent_id


def rollout(provider_home, session_id, parent=None, cwd="/tmp/root-project"):
    path = provider_home.codex / "sessions" / "2026" / "09" / "07" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {
            "id": session_id, "cwd": cwd,
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli",
        }},
        {"type": "response_item", "timestamp": "2026-09-07T00:00:00Z", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Work"}],
        }},
    ]
    path.write_bytes(b"\n".join(orjson.dumps(record) for record in records) + b"\n")
    return path


@pytest.mark.django_db
def test_initial_sync_entire_new_deep_cross_project_tree_without_writer(provider_home):
    root = "00000000-0000-4000-8000-000000000000"
    rollout(provider_home, root)
    parent = root
    children = []
    for depth in range(1, 41):
        child = f"00000000-0000-4000-8000-{depth:012d}"
        rollout(provider_home, child, parent, cwd=f"/tmp/child-project-{depth % 2}")
        children.append(child)
        parent = child
    output = queue.Queue()
    sync_all(output)
    payloads = [item for item in output.queue if isinstance(item, CreateSessionPayload)]
    assert len(payloads) == 41
    assert not Session.objects.exists()
    assert {item.session.parent_session_id for item in payloads if item.session.id in children} == {root}


@pytest.mark.django_db
def test_resolver_rejects_cycles_missing_and_parentless_subagent():
    project = Project.objects.create(id="p")
    a = Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", type="subagent")
    b = Session.objects.create(id="b", file_path="b.jsonl", project=project, provider="codex", type="subagent", parent_session=a)
    assert resolve_flat_parent_id(a.id) is None
    a.parent_session = b
    a.save()
    assert resolve_flat_parent_id(a.id) is None
    assert resolve_flat_parent_id("missing") is None
    assert resolve_flat_parent_id("a", parent_of={"a": "b", "b": "a"}) is None


@pytest.mark.django_db
def test_initial_sync_uses_existing_ancestry_and_skips_orphans(provider_home):
    project = Project.objects.create(id="existing")
    root = Session.objects.create(id="root", file_path="root.jsonl", project=project, provider="codex")
    parent = Session.objects.create(
        id="parent", file_path="parent.jsonl", project=project, provider="codex", type="subagent", parent_session=root,
    )
    rollout(provider_home, "00000000-0000-4000-8000-000000000001", parent.id)
    rollout(provider_home, "00000000-0000-4000-8000-000000000002", "missing")
    output = queue.Queue()
    sync_all(output)
    payloads = [item for item in output.queue if isinstance(item, CreateSessionPayload)]
    assert len(payloads) == 1
    assert payloads[0].session.parent_session_id == root.id


@pytest.mark.django_db(transaction=True)
def test_watcher_creates_root_anchor_and_routes_activity_and_items(provider_home, monkeypatch):
    from twicc.providers import sessions_watcher
    from twicc.agent.registry import get_agent_manager_registry

    project = Project.objects.create(id="p", directory="/tmp/root-project")
    root = Session.objects.create(id="root", file_path="root.jsonl", project=project, provider="codex")
    parent = Session.objects.create(id="parent", file_path="parent.jsonl", project=project, provider="codex", type="subagent", parent_session=root)
    child = "00000000-0000-4000-8000-000000000099"
    path = rollout(provider_home, child, parent.id)
    watcher = CodexSessionsWatcher()
    parsed = asyncio.run(watcher.parse_session_file(path))
    assert parsed is not None
    parsed.project_id = project.id
    messages = AsyncMock()
    monkeypatch.setattr(sessions_watcher, "broadcast_message", messages)
    activity = []
    monkeypatch.setattr(get_agent_manager_registry(), "touch_agent_activity", activity.append)
    asyncio.run(watcher.sync_and_broadcast(path, parsed, Change.added, None))
    assert Session.objects.get(id=child).parent_session_id == root.id
    assert parsed.parent_session_id == root.id
    assert activity == [root.id]
    items = [call.args[1] for call in messages.call_args_list if call.args[1]["type"] == "session_items_added"]
    assert items and items[0]["parent_session_id"] == root.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cycle", [False, True])
def test_watcher_defers_missing_or_cyclic_ancestry(provider_home, cycle):
    if cycle:
        project = Project.objects.create(id="p")
        a = Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", type="subagent")
        b = Session.objects.create(
            id="b", file_path="b.jsonl", project=project, provider="codex", type="subagent", parent_session=a,
        )
        a.parent_session = b
        a.save()
    child = "00000000-0000-4000-8000-000000000098"
    path = rollout(provider_home, child, "a" if cycle else "missing")
    watcher = CodexSessionsWatcher()
    parsed = asyncio.run(watcher.parse_session_file(path))
    asyncio.run(watcher.sync_and_broadcast(path, parsed, Change.added, None))
    assert not Session.objects.filter(id=child).exists()


@pytest.mark.django_db
def test_actual_migration_deep_chain_repairs_former_parents_and_projects():
    migration = importlib.import_module("twicc.core.migrations.0142_flatten_codex_subagent_parents")
    apps = MigrationLoader(connection).project_state([("core", "0141_mcp_oauth_source_hash")]).apps
    HistoricalSession = apps.get_model("core", "Session")
    HistoricalProject = apps.get_model("core", "Project")
    HistoricalItem = apps.get_model("core", "SessionItem")
    project = HistoricalProject.objects.create(id="migration-project", total_cost=Decimal(1))
    root = HistoricalSession.objects.create(id="migration-root", file_path="migration-root.jsonl", project=project, provider="codex", total_cost=Decimal(1))
    HistoricalItem.objects.create(session=root, line_num=1, content="{}", cost=Decimal(1))
    parent = root
    descendants = []
    for depth in range(40):
        child = HistoricalSession.objects.create(
            id=f"migration-child-{depth}", file_path=f"child-{depth}.jsonl", project=project, provider="codex", type="subagent",
            parent_session=parent, self_cost=Decimal("0.5"), subagents_cost=Decimal("0.5"), total_cost=Decimal(1),
        )
        HistoricalItem.objects.create(session=child, line_num=1, content="{}", cost=Decimal("0.5"))
        descendants.append(child)
        parent = child
    # Last leaf already has correct pre-migration aggregates.
    parent.subagents_cost = None
    parent.total_cost = Decimal("0.5")
    parent.save()
    migration.flatten(apps, SimpleNamespace(connection=connection))
    assert set(HistoricalSession.objects.filter(type="subagent").values_list("parent_session_id", flat=True)) == {root.id}
    root.refresh_from_db()
    project.refresh_from_db()
    assert root.subagents_cost == Decimal(20)
    assert root.total_cost == project.total_cost == Decimal(21)
    for child in descendants:
        child.refresh_from_db()
        assert child.subagents_cost is None
        assert child.total_cost == Decimal("0.5")
    migration.flatten(apps, SimpleNamespace(connection=connection))
    root.refresh_from_db()
    assert root.total_cost == Decimal(21)
