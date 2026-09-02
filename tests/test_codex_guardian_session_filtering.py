import asyncio
import queue
from datetime import datetime, UTC
from pathlib import Path

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.providers.codex.initial_sync import extract_session_meta, sync_all
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload


pytestmark = pytest.mark.django_db


def _write_session_meta(
    root: Path,
    session_id: str,
    *,
    source: object,
    parent_thread_id: str | None = None,
) -> Path:
    path = root / "2026" / "07" / "16" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": session_id,
        "session_id": parent_thread_id or session_id,
        "parent_thread_id": parent_thread_id,
        "cwd": "/tmp/project",
        "source": source,
    }
    path.write_bytes(orjson.dumps({"type": "session_meta", "payload": payload}) + b"\n")
    return path


def test_extract_session_meta_marks_guardian_rollout_ignored(tmp_path: Path) -> None:
    parent_id = "019f6a8f-c435-7d02-8e3a-eddc35fae37b"
    path = _write_session_meta(
        tmp_path,
        "019f6a8f-c563-7800-b6d8-e2cc96ebe777",
        source={"subagent": {"other": "guardian"}},
        parent_thread_id=parent_id,
    )

    meta = extract_session_meta(path)

    assert meta is not None
    assert meta.ignored is True
    assert meta.parent_session_id is None


def test_extract_session_meta_keeps_user_visible_subagent(tmp_path: Path) -> None:
    parent_id = "019f6a8f-c435-7d02-8e3a-eddc35fae37b"
    path = _write_session_meta(
        tmp_path,
        "019f6a8f-c563-7800-b6d8-e2cc96ebe778",
        source={"subagent": {"thread_spawn": {"parent_thread_id": parent_id}}},
        parent_thread_id=parent_id,
    )

    meta = extract_session_meta(path)

    assert meta is not None
    assert meta.ignored is False
    assert meta.parent_session_id == parent_id


def test_watcher_rejects_guardian_before_session_creation(tmp_path: Path) -> None:
    path = _write_session_meta(
        tmp_path,
        "019f6a8f-c563-7800-b6d8-e2cc96ebe777",
        source={"subagent": {"other": "guardian"}},
        parent_thread_id="019f6a8f-c435-7d02-8e3a-eddc35fae37b",
    )
    watcher = CodexSessionsWatcher()
    watcher.projects_dir = tmp_path

    assert asyncio.run(watcher.parse_session_file(path)) is None


def test_initial_sync_does_not_enqueue_new_guardian(provider_home) -> None:
    _write_session_meta(
        provider_home.codex / "sessions",
        "019f6a8f-c563-7800-b6d8-e2cc96ebe777",
        source={"subagent": {"other": "guardian"}},
        parent_thread_id="019f6a8f-c435-7d02-8e3a-eddc35fae37b",
    )
    sync_queue = queue.Queue()

    stats = sync_all(sync_queue)

    assert stats["sessions_created"] == 0
    assert sync_queue.empty()


def test_initial_sync_enqueues_legacy_guardian_deletion(provider_home) -> None:
    sessions_dir = provider_home.codex / "sessions"
    session_id = "019f6a8f-c563-7800-b6d8-e2cc96ebe777"
    path = _write_session_meta(
        sessions_dir,
        session_id,
        source={"subagent": {"other": "guardian"}},
        parent_thread_id="019f6a8f-c435-7d02-8e3a-eddc35fae37b",
    )
    project = Project.objects.create(id="-tmp-project", directory="/tmp/project")
    Session.objects.create(
        id=session_id,
        project=project,
        provider=Provider.CODEX,
        file_path=str(path.relative_to(sessions_dir)),
        model="codex-auto-review",
    )
    sync_queue = queue.Queue()

    sync_all(sync_queue)

    payload = sync_queue.get_nowait()
    assert isinstance(payload, DeleteSessionsPayload)
    assert payload.session_ids == [session_id]
    assert sync_queue.empty()


def test_initial_sync_enqueues_uncomputed_legacy_guardian_deletion(provider_home) -> None:
    sessions_dir = provider_home.codex / "sessions"
    session_id = "019f6a8f-c563-7800-b6d8-e2cc96ebe779"
    path = _write_session_meta(
        sessions_dir,
        session_id,
        source={"subagent": {"other": "guardian"}},
        parent_thread_id="019f6a8f-c435-7d02-8e3a-eddc35fae37b",
    )
    project = Project.objects.create(id="-tmp-project", directory="/tmp/project")
    Session.objects.create(
        id=session_id,
        project=project,
        provider=Provider.CODEX,
        file_path=str(path.relative_to(sessions_dir)),
        model=None,
        compute_version=None,
    )
    sync_queue = queue.Queue()

    sync_all(sync_queue)

    payload = sync_queue.get_nowait()
    assert isinstance(payload, DeleteSessionsPayload)
    assert payload.session_ids == [session_id]
    assert sync_queue.empty()


def test_delete_ignored_session_repairs_project_metadata() -> None:
    project = Project.objects.create(
        id="-tmp-project",
        directory="/tmp/project",
        sessions_count=1,
        mtime=123,
    )
    session = Session.objects.create(
        id="019f6a8f-c563-7800-b6d8-e2cc96ebe777",
        project=project,
        provider=Provider.CODEX,
        file_path="2026/07/16/guardian.jsonl",
        type=SessionType.SESSION,
        model="codex-auto-review",
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
        user_message_count=1,
        mtime=123,
    )

    deleted = _apply_delete_sessions_payload(DeleteSessionsPayload(
        provider=Provider.CODEX,
        session_ids=[session.id],
    ))

    assert deleted == [session.id]
    assert not Session.objects.filter(id=session.id).exists()
    project.refresh_from_db()
    assert project.sessions_count == 0
    assert project.mtime == 0
