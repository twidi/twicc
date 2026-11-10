"""Native subagents stay outside the search index on every indexing path."""

import pytest
from django.conf import settings
from django.utils import timezone

from tests.db_write_helpers import run_with_direct_writes, run_with_writer
from twicc import paths, search
from twicc.core.models import Project, Session, SessionType
from twicc import search_indexing_task as indexing

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_search_dir", lambda: tmp_path / "search-index")
    monkeypatch.setattr(indexing, "_indexing_run_lock", None)
    indexing._pending_reindex_ids.clear()
    indexing._indexing_tasks.clear()
    indexing._session_reindex_task = None
    indexing._shutting_down = False
    search.init_search_index()
    try:
        yield
    finally:
        search.shutdown_search_index()
        indexing._pending_reindex_ids.clear()
        indexing._indexing_tasks.clear()
        indexing._session_reindex_task = None


@pytest.fixture(params=["claude_code", "codex"])
def sessions(index, request):
    provider = request.param
    compute_version = (
        settings.CLAUDE_CODE_COMPUTE_VERSION if provider == "claude_code" else settings.CODEX_COMPUTE_VERSION
    )
    project = Project.objects.create(id="search-types-project")
    root = Session.objects.create(
        id="root", project=project, provider=provider, file_path="root.jsonl",
        type=SessionType.SESSION, title="rootneedle", compute_version=compute_version,
        search_version=settings.CURRENT_SEARCH_VERSION,
    )
    child = Session.objects.create(
        id="child", project=project, provider=provider, file_path="child.jsonl",
        type=SessionType.SUBAGENT, parent_session=root, title="childneedle", compute_version=compute_version,
        search_version=settings.CURRENT_SEARCH_VERSION,
    )
    return root, child


def seed_legacy_document(session):
    search.index_document(
        session.id, session.project_id, 0, session.title, "title", timezone.now(),
        session.archived, hidden=session.hidden,
    )
    search.commit()


def test_compute_reindex_request_removes_subagent_without_marking_it_indexed(sessions):
    _, child = sessions
    seed_legacy_document(child)

    async def reindex_after_compute():
        await indexing.request_session_reindex(child.id)

    run_with_writer(reindex_after_compute)

    child.refresh_from_db()
    assert child.search_version is None
    assert search.search("childneedle", include_hidden=True).total_sessions == 0


def test_direct_reindex_removes_subagent_without_recreating_documents(sessions):
    _, child = sessions
    seed_legacy_document(child)

    assert run_with_direct_writes(lambda: search.areindex_session(child.id)) is False

    child.refresh_from_db()
    assert child.search_version is None
    assert search.search("childneedle", include_hidden=True).total_sessions == 0


@pytest.mark.parametrize("already_invalidated", [False, True])
def test_startup_purges_legacy_subagents_without_reindexing_primary_sessions(sessions, already_invalidated):
    root, child = sessions
    seed_legacy_document(root)
    seed_legacy_document(child)
    if already_invalidated:
        Session.objects.filter(id=child.id).update(search_version=None)

    # Cleanup runs at every startup, independent of the search schema version.
    run_with_direct_writes(indexing.remove_obsolete_session_documents)
    run_with_direct_writes(indexing.remove_obsolete_session_documents)

    root.refresh_from_db()
    child.refresh_from_db()
    assert root.search_version == settings.CURRENT_SEARCH_VERSION
    assert child.search_version is None
    assert search.search("rootneedle").total_sessions == 1
    assert search.search("childneedle", include_hidden=True).total_sessions == 0


def test_hidden_archived_spawned_primary_session_remains_indexable(sessions):
    root, _ = sessions
    spawner = Session.objects.create(
        id="spawner", project=root.project, provider=root.provider, file_path="spawner.jsonl",
        type=SessionType.SESSION,
    )
    Session.objects.filter(id=root.id).update(hidden=True, archived=True, spawned_by_id=spawner.id)

    assert run_with_direct_writes(lambda: search.areindex_session(root.id)) is True

    root.refresh_from_db()
    assert root.search_version == settings.CURRENT_SEARCH_VERSION
    assert search.search("rootneedle", include_hidden=True, include_archived=True).total_sessions == 1
