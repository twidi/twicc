from __future__ import annotations

import asyncio

import pytest
from django.conf import settings
from watchfiles import Change

import twicc.providers.sessions_watcher as watcher_module
import twicc.search_indexing_task as indexing
from twicc import search
from twicc.core.models import Project, Session, SessionType
from twicc.providers.sessions_watcher import (
    BaseSessionsWatcher,
    IndexingRequest,
    ParsedSessionFile,
)
from twicc.search_indexing_task import (
    _index_session,
    remove_obsolete_session_documents,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def reset_reindex_state():
    indexing._pending_reindex_ids.clear()
    indexing._session_reindex_task = None
    indexing._indexing_tasks.clear()
    indexing._shutting_down = False
    yield
    indexing._pending_reindex_ids.clear()
    indexing._session_reindex_task = None
    indexing._indexing_tasks.clear()
    indexing._shutting_down = False


def _session(session_id: str, compute_version: int, title: str | None = None):
    project, _ = Project.objects.get_or_create(id="search-readiness-project")
    return Session.objects.create(
        id=session_id,
        project=project,
        provider="claude_code",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        compute_version=compute_version,
        title=title,
    )


def test_obsolete_session_is_deleted_and_not_indexed(monkeypatch):
    stale = _session("stale-index", settings.CLAUDE_CODE_COMPUTE_VERSION - 1, title="old title")
    calls = []
    monkeypatch.setattr(search, "delete_session_documents", lambda sid: calls.append(("delete", sid)))
    monkeypatch.setattr(search, "commit", lambda: calls.append(("commit", None)))
    monkeypatch.setattr(search, "index_document", lambda *_args, **_kwargs: calls.append(("index", None)))

    class Buffer:
        async def add(self, _session_id):
            raise AssertionError("obsolete session was marked indexed")

    asyncio.run(_index_session(stale.id, Buffer()))

    assert calls == [("delete", stale.id), ("commit", None)]


def test_session_becoming_obsolete_during_index_is_deleted_again(monkeypatch):
    ready = _session("racing-index", settings.CLAUDE_CODE_COMPUTE_VERSION, title="current title")
    calls = []

    def index_document(*_args, **_kwargs):
        calls.append(("index", ready.id))
        Session.objects.filter(id=ready.id).update(
            compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION - 1,
        )

    monkeypatch.setattr(search, "delete_session_documents", lambda sid: calls.append(("delete", sid)))
    monkeypatch.setattr(search, "commit", lambda: calls.append(("commit", None)))
    monkeypatch.setattr(search, "index_document", index_document)

    class Buffer:
        async def add(self, _session_id):
            raise AssertionError("racing session was marked indexed")

    asyncio.run(_index_session(ready.id, Buffer()))

    assert calls == [
        ("delete", ready.id),
        ("index", ready.id),
        ("delete", ready.id),
        ("commit", None),
    ]


def test_startup_removes_only_obsolete_documents(monkeypatch):
    ready = _session("ready-startup", settings.CLAUDE_CODE_COMPUTE_VERSION)
    stale = _session("stale-startup", settings.CLAUDE_CODE_COMPUTE_VERSION - 1)
    deleted = []
    commits = []
    monkeypatch.setattr(search, "delete_session_documents", deleted.append)
    monkeypatch.setattr(search, "commit", lambda: commits.append(True))

    removed = asyncio.run(remove_obsolete_session_documents())

    assert removed == 1
    assert deleted == [stale.id]
    assert ready.id not in deleted
    assert commits == [True]


def test_requested_reindexes_coalesce_behind_one_task(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "is_initialized", lambda: True)

    async def fake_index(session_id, _buffer):
        calls.append(session_id)
        await asyncio.sleep(0)

    monkeypatch.setattr(indexing, "_index_session", fake_index)

    async def scenario():
        first = indexing.request_session_reindex("one")
        second = indexing.request_session_reindex("two")
        assert first is second
        await first

    asyncio.run(scenario())

    assert set(calls) == {"one", "two"}


def test_shutdown_rejects_new_session_reindexes(monkeypatch):
    monkeypatch.setattr(search, "is_initialized", lambda: True)

    async def scenario():
        indexing.stop_search_index_task()

        task = indexing.request_session_reindex("late-session")

        assert task is None
        assert "late-session" not in indexing._pending_reindex_ids

    asyncio.run(scenario())


def test_watcher_removes_obsolete_session_without_indexing_or_marking(monkeypatch):
    stale = _session("stale-watcher", settings.CLAUDE_CODE_COMPUTE_VERSION - 1)
    calls = []

    class Watcher(BaseSessionsWatcher):
        def get_compute(self):
            return type("Compute", (), {"provider": "claude_code"})()

    watcher = Watcher()
    parsed = ParsedSessionFile(
        stale.project_id,
        stale.id,
        SessionType.SESSION,
        stale.file_path,
        title="known",
    )

    async def sync_and_broadcast(*_args):
        return IndexingRequest(stale.id, [1], False)

    async def unlocked(factory):
        return await factory()

    async def get_session(_session_id):
        return await Session.objects.aget(id=stale.id)

    async def fail_index(*_args):
        raise AssertionError("obsolete session was indexed")

    async def fail_mark(*_args):
        raise AssertionError("obsolete session was marked indexed")

    watcher.sync_and_broadcast = sync_and_broadcast
    watcher._index_new_items_for_search = fail_index
    monkeypatch.setattr(watcher_module, "run_under_db_write_lock", unlocked)
    monkeypatch.setattr(watcher_module, "get_session_by_id", get_session)
    monkeypatch.setattr(watcher_module, "mark_session_search_version_current", fail_mark)
    monkeypatch.setattr(search, "is_initialized", lambda: True)
    monkeypatch.setattr(search, "delete_session_documents", lambda session_id: calls.append(("delete", session_id)))
    monkeypatch.setattr(search, "commit", lambda: calls.append(("commit", None)))

    asyncio.run(
        watcher._process_parsed_session_change(
            stale.file_path,
            parsed,
            Change.modified,
            None,
        )
    )

    assert calls == [("delete", stale.id), ("commit", None)]
