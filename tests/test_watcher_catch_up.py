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

    async def fake_process(change_type, path_str, channel_layer):
        seen.append((change_type, path_str))

    monkeypatch.setattr(watcher, "_process_change", fake_process)
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
