"""The watcher catches up the session files already present when the projects dir appears.

A brand-new provider home (relocated ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``, or a
first install) gets its directory and its first session file in one go, before
``awatch`` is armed — without the catch-up that file was only seen at the next
boot's initial sync.
"""

import asyncio

import pytest
from watchfiles import Change

from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher

pytestmark = pytest.mark.django_db


def test_catch_up_processes_every_existing_jsonl_as_added(provider_home, monkeypatch):
    watcher = ClaudeCodeSessionsWatcher()
    projects = provider_home.claude / "projects"
    (projects / "-p1" / "s1" / "subagents").mkdir(parents=True)
    (projects / "-p1" / "s1.jsonl").write_text("{}\n")
    (projects / "-p1" / "s1" / "subagents" / "agent-a1.jsonl").write_text("{}\n")
    (projects / "-p1" / "notes.txt").write_text("ignored")
    (projects / "-p2").mkdir()

    seen: list[tuple[Change, str]] = []

    def enqueue(path, change_type):
        seen.append((change_type, str(path)))

    monkeypatch.setattr(watcher, "_enqueue", enqueue)
    asyncio.run(watcher._catch_up_existing_files(channel_layer=None))

    assert seen == [
        (Change.added, str(projects / "-p1" / "s1" / "subagents" / "agent-a1.jsonl")),
        (Change.added, str(projects / "-p1" / "s1.jsonl")),
    ]


def test_catch_up_is_silent_on_an_empty_dir(provider_home, monkeypatch):
    watcher = ClaudeCodeSessionsWatcher()
    (provider_home.claude / "projects").mkdir()
    calls = []
    monkeypatch.setattr(watcher, "_process_change", lambda *a: calls.append(a))
    asyncio.run(watcher._catch_up_existing_files(channel_layer=None))
    assert calls == []


def test_live_sync_supplies_capture_boundary_without_changing_it(tmp_path):
    from datetime import timedelta
    import orjson
    from django.utils import timezone
    from twicc.core.models import Project, Session, TimelineEvent
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.live_sync import LiveSyncLimits
    from twicc.providers.sessions_watcher import _sync_live_session_items

    watcher = ClaudeCodeSessionsWatcher()
    boundary = watcher.timeline_since
    session = Session.objects.create(id='catch-up', provider='claude_code', project=Project.objects.create(id='p'))
    path = tmp_path / 'live.jsonl'
    records = [
        {'type': 'user', 'uuid': identity, 'timestamp': timestamp.isoformat(),
         'message': {'role': 'user', 'content': text}}
        for identity, timestamp, text in [
            ('old', boundary - timedelta(days=1), 'history'),
            ('new', timezone.now(), 'live'),
        ]
    ]
    path.write_bytes(b''.join(orjson.dumps(record) + b'\n' for record in records))
    compute = ClaudeCodeSessionCompute()
    _sync_live_session_items(compute, session.id, path, LiveSyncLimits(1), boundary)
    _sync_live_session_items(compute, session.id, path, LiveSyncLimits(1), boundary)
    assert [event.data['text'] for event in TimelineEvent.objects.filter(kind='user_message.sent')] == ['live']
    assert watcher.timeline_since == boundary
