"""Aggregate admission requires completed and successful provider inventory."""
import asyncio
import queue
import threading
from pathlib import Path

import pytest

from twicc.providers.claude_code import initial_sync, orchestrator


@pytest.mark.parametrize('outcome', ['success', 'incomplete', 'payload_failure', 'producer_failure', 'stopped'])
def test_claude_inventory_done_is_not_success(monkeypatch, outcome):
    instance = orchestrator.ClaudeCodeOrchestrator()
    monkeypatch.setattr(orchestrator, '_count_total_sessions', lambda: 0)
    async def quiet(*args, **kwargs):
        pass
    monkeypatch.setattr(orchestrator, 'broadcast_startup_progress', quiet)
    def produce(*args, **kwargs):
        if outcome == 'producer_failure':
            raise OSError('inventory scan failed')
        if outcome == 'stopped':
            instance._sync_stop_event.set()
        return {'inventory_complete': int(outcome != 'incomplete')}
    monkeypatch.setattr(orchestrator, 'sync_all', produce)
    from twicc.providers import db_writer
    monkeypatch.setattr(db_writer, 'get_thread_queue', queue.Queue)
    async def accept(marker, stop):
        marker.done_future.set_result(int(outcome == 'payload_failure'))
        return True
    monkeypatch.setattr(db_writer, 'put_thread_message', accept)
    asyncio.run(instance._initial_sync_task())
    assert instance.initial_sync_done.is_set()
    assert instance.initial_sync_succeeded == (outcome == 'success')


def test_claude_scan_does_not_hide_permission_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(initial_sync, 'claude_projects_dir', lambda: tmp_path)
    original = Path.stat
    def denied(self, *args, **kwargs):
        if self == tmp_path:
            raise PermissionError('inventory denied')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', denied)
    with pytest.raises(PermissionError, match='inventory denied'):
        initial_sync.scan_projects()


@pytest.mark.django_db
def test_claude_stopped_inventory_is_incomplete(monkeypatch):
    monkeypatch.setattr(initial_sync, 'scan_projects', lambda: set())
    stop = threading.Event()
    stop.set()
    assert initial_sync.sync_all(queue.Queue(), stop_event=stop)['inventory_complete'] == 0


@pytest.mark.django_db
@pytest.mark.parametrize('existing', [False, True])
def test_skipped_claude_source_read_marks_inventory_incomplete(monkeypatch, tmp_path, existing):
    import builtins
    from twicc.core.models import Project, Session
    project_dir = tmp_path / 'p'
    project_dir.mkdir()
    path = project_dir / 'session.jsonl'
    path.write_text('{}\n')
    monkeypatch.setattr(initial_sync, 'claude_projects_dir', lambda: tmp_path)
    monkeypatch.setattr(initial_sync, 'scan_sessions', lambda project: {'session': path})
    monkeypatch.setattr(initial_sync, 'scan_subagents', lambda *args: {})
    monkeypatch.setattr(initial_sync, 'scan_workflow_files', lambda *args: {})
    if existing:
        project = Project.objects.create(id='p')
        Session.objects.create(id='session', project=project, provider='claude_code')
    original = builtins.open
    def denied(file, *args, **kwargs):
        if Path(file) == path:
            raise PermissionError('source unreadable')
        return original(file, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', denied)
    stats = initial_sync.sync_project('p', queue.Queue())
    assert stats['inventory_complete'] == 0


def test_default_gate_requires_every_enabled_inventory_success(monkeypatch):
    from types import SimpleNamespace
    from twicc import orchestrator as registry_module
    from twicc.providers import state, tree_aggregate_runtime
    done = threading.Event()
    done.set()
    providers = {name: SimpleNamespace(initial_sync_done=done, initial_sync_succeeded=True)
                 for name in ('claude_code', 'codex')}
    monkeypatch.setattr(state, 'get_enabled_providers', lambda: tuple(providers))
    monkeypatch.setattr(registry_module, 'get_orchestrator_registry', lambda: providers)
    assert tree_aggregate_runtime.inventory_ready()
    providers['claude_code'].initial_sync_succeeded = False
    assert not tree_aggregate_runtime.inventory_ready()
