"""Startup preserves logical identity when Codex changes rollout representation."""

import asyncio
import builtins
import os
import queue
from pathlib import Path

import orjson
import pytest
import zstandard
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.codex import initial_sync
from twicc.providers import db_writer

pytestmark = pytest.mark.django_db


def rollout(provider_home, session_id="thread", *, parent=None, guardian=False, name=None, final_newline=True):
    path = provider_home.codex / "sessions" / "2026" / "11" / "08" / f"rollout-{name or session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = {"subagent": {"other": "guardian"}} if guardian else "cli"
    if parent:
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
    records = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": "/tmp/compressed-project", "source": source}},
        {"type": "event_msg", "payload": {"type": "token_count", "n": 1}},
        {"type": "event_msg", "payload": {"type": "token_count", "n": 2}},
    ]
    raw = b"\n".join(orjson.dumps(record) for record in records) + (b"\n" if final_newline else b"")
    path.write_bytes(raw)
    return path, raw


def compress(path, *, remove=True):
    target = path.with_suffix(".jsonl.zst")
    target.write_bytes(zstandard.ZstdCompressor().compress(path.read_bytes()))
    source_stat = path.stat()
    os.utime(target, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    if remove:
        path.unlink()
    return target


def existing(provider_home, path, raw, *, prefix=3, **fields):
    project, _ = Project.objects.get_or_create(id="-tmp-compressed-project", defaults={"directory": "/tmp/compressed-project"})
    lines = raw.splitlines(keepends=True)
    session = Session.objects.create(
        id="thread", project=project, provider=Provider.CODEX,
        file_path=str(path.relative_to(provider_home.codex / "sessions")),
        last_offset=len(b"".join(lines[:prefix])), last_line=prefix, mtime=path.stat().st_mtime,
        compute_version=42, search_version=7, history_epoch=4, title="Keep title", pinned="2026-11-08T00:00:00Z",
        **fields,
    )
    for number, line in enumerate(lines[:prefix], 1):
        SessionItem.objects.create(session=session, line_num=number, content=line.decode().strip())
    return session


def produce():
    output = queue.Queue()
    stats = initial_sync.sync_all(output)
    return list(output.queue), stats


def apply(messages):
    # Use real DB-only handlers; network/workspace side effects have separate routing tests.
    from twicc.providers.codex.source_binding import (
        CodexSourcePayload, MarkCodexSourcesMissingPayload, apply_codex_source_payload,
        apply_mark_codex_sources_missing_payload,
    )
    results = []
    for message in messages:
        if isinstance(message, CodexSourcePayload):
            results.append(apply_codex_source_payload(message))
        elif isinstance(message, db_writer.CreateSessionPayload):
            db_writer._apply_create_session_payload(message)
        elif isinstance(message, db_writer.UpdateSessionPayload):
            db_writer._apply_update_session_payload(message)
        elif isinstance(message, MarkCodexSourcesMissingPayload):
            apply_mark_codex_sources_missing_payload(message)
        elif isinstance(message, db_writer.UpdateProjectMetadataPayload):
            db_writer._apply_update_project_metadata_payload(message)
    return results


def test_compression_while_stopped_repairs_existing_session(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    before_ids = list(session.items.values_list("id", flat=True))
    incarnation = session.storage_incarnation
    compressed = compress(path)

    messages, stats = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.file_path == str(compressed.relative_to(provider_home.codex / "sessions"))
    assert list(session.items.values_list("id", flat=True)) == before_ids
    assert (session.last_offset, session.last_line, session.history_epoch) == (len(raw), 3, 4)
    assert (session.compute_version, session.search_version, session.storage_incarnation) == (42, 7, incarnation)
    assert (session.title, session.pinned, session.stale) == ("Keep title", "2026-11-08T00:00:00Z", False)
    assert stats["inventory_complete"] == 1
    assert stats["sessions_stale"] == 0


@pytest.mark.parametrize("reason", ["rollout_missing", "codex_migration_failed:invalid_session_metadata"])
def test_unchanged_source_repairs_stale_without_new_items(provider_home, reason):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, stale=True, unavailable_reason=reason)
    compressed = compress(path)
    Session.objects.filter(pk=session.pk).update(file_path=str(compressed.relative_to(provider_home.codex / "sessions")))
    ids = list(session.items.values_list("id", flat=True))

    messages, _ = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.stale is False
    assert session.unavailable_reason == (None if reason == "rollout_missing" else reason)
    assert list(session.items.values_list("id", flat=True)) == ids
    assert session.compute_version == 42


def test_partial_compressed_import_appends_only_missing_records(provider_home):
    path, raw = rollout(provider_home, final_newline=False)
    session = existing(provider_home, path, raw, prefix=1)
    first_id = session.items.get().id
    compress(path)

    messages, _ = produce()
    apply(messages)

    session.refresh_from_db()
    assert list(session.items.order_by("line_num").values_list("line_num", flat=True)) == [1, 2, 3]
    assert session.items.get(line_num=1).id == first_id
    assert session.last_offset == len(raw)
    assert session.compute_version is None


def test_obsolete_compressed_payload_cannot_reverse_restoration(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, prefix=1)
    compress(path)
    messages, _ = produce()
    path.write_bytes(raw + b'{}\n')
    Session.objects.filter(pk=session.pk).update(last_offset=len(raw) + 3, last_line=4)

    results = apply(messages)

    session.refresh_from_db()
    assert results == [False]
    assert session.file_path.endswith(".jsonl")
    assert (session.last_offset, session.last_line) == (len(raw) + 3, 4)
    assert session.items.count() == 1


def test_new_compressed_sessions_and_subagents_ignore_guardians(provider_home):
    for session_id, parent, guardian in [("root", None, False), ("child", "root", False), ("guardian", None, True)]:
        path, _ = rollout(provider_home, session_id, parent=parent, guardian=guardian)
        compress(path)

    messages, stats = produce()
    apply(messages)

    assert set(Session.objects.values_list("id", flat=True)) == {"root", "child"}
    assert Session.objects.get(pk="child").parent_session_id == "root"
    assert all(path.endswith(".jsonl.zst") for path in Session.objects.values_list("file_path", flat=True))
    assert stats["sessions_created"] == 2
    assert stats["inventory_complete"] == 1


def test_plain_source_wins_when_both_siblings_exist(provider_home):
    path, raw = rollout(provider_home)
    compressed = compress(path, remove=False)
    compressed.write_bytes(b"broken compressed sibling")

    messages, stats = produce()
    apply(messages)

    session = Session.objects.get(pk="thread")
    assert session.file_path.endswith(".jsonl")
    assert session.last_offset == len(raw)
    assert session.items.count() == 3
    assert stats["inventory_complete"] == 1


@pytest.mark.parametrize("change", ["mismatched_id", "unrelated_path"])
def test_source_cannot_adopt_a_conflicting_identity(provider_home, change):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    original_path = session.file_path
    path.unlink()
    replacement, _ = rollout(provider_home, "other" if change == "mismatched_id" else "thread", name="thread" if change == "mismatched_id" else "duplicate")
    compress(replacement)

    messages, stats = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.file_path == original_path
    assert session.items.count() == 3
    assert Session.objects.count() == 1
    assert stats["inventory_complete"] == 0


def test_missing_classification_rechecks_both_siblings(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    path.unlink()
    messages, _ = produce()
    path.write_bytes(raw)
    compress(path)

    apply(messages)

    session.refresh_from_db()
    assert session.stale is False


def test_unchanged_jsonl_startup_keeps_existing_io_and_query_counts(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    existing(provider_home, path, raw)
    counts = {"stat": 0, "open": 0}
    original_stat, original_open = Path.stat, builtins.open

    def count_stat(value, *args, **kwargs):
        if value == path or value == provider_home.codex / "sessions":
            counts["stat"] += 1
        return original_stat(value, *args, **kwargs)

    def count_open(value, *args, **kwargs):
        if Path(value) == path:
            counts["open"] += 1
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", count_stat)
    monkeypatch.setattr(builtins, "open", count_open)
    with CaptureQueriesContext(connection) as queries:
        messages, stats = produce()

    assert counts == {"stat": 4, "open": 0}
    assert len(queries) == 2
    assert not any(isinstance(message, db_writer.UpdateSessionPayload) for message in messages)
    assert stats["items_added"] == 0


@pytest.mark.parametrize("field,value", [
    ("last_offset", 9999), ("last_line", 100), ("history_epoch", 20),
    ("storage_incarnation", "new-incarnation"), ("provider", "claude_code"),
    ("file_path", "other/rollout-thread.jsonl"),
])
def test_changed_checkpoint_or_association_rejects_queued_envelope(provider_home, field, value):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, prefix=1)
    compress(path)
    messages, _ = produce()
    Session.objects.filter(pk=session.pk).update(**{field: value})

    assert apply(messages) == [False]
    session.refresh_from_db()
    assert getattr(session, field) == value
    assert session.items.count() == 1


def test_compressed_source_identity_changes_after_inventory(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, prefix=1)
    compressed = compress(path)
    original_prepare = initial_sync.prepare_rollout

    def replace_source(*args, **kwargs):
        changed_raw = raw.replace(b'"id":"thread"', b'"id":"different"')
        compressed.write_bytes(zstandard.ZstdCompressor().compress(changed_raw))
        return original_prepare(*args, **kwargs)

    # Keep the exact physical association: even this read must validate identity.
    Session.objects.filter(pk=session.pk).update(file_path=str(compressed.relative_to(provider_home.codex / "sessions")))
    monkeypatch.setattr(initial_sync, "prepare_rollout", replace_source)
    messages, stats = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.items.count() == 1
    assert session.last_line == 1
    assert stats["inventory_complete"] == 0


def test_corrupt_compressed_tail_publishes_no_prefix(provider_home):
    path, raw = rollout(provider_home)
    compressed = compress(path)
    compressed.write_bytes(compressed.read_bytes()[:-1])

    messages, stats = produce()
    apply(messages)

    assert not Session.objects.exists()
    assert stats["inventory_complete"] == 0
    assert stats["items_added"] == 0


def test_source_transition_during_plain_read_uses_selected_sibling(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, prefix=1)
    original_read = initial_sync.read_session_items_from_file

    def transition(row, read_path, **kwargs):
        if read_path == path and path.exists():
            compress(path)
        return original_read(row, read_path, **kwargs)

    monkeypatch.setattr(initial_sync, "read_session_items_from_file", transition)
    messages, stats = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.file_path.endswith(".jsonl.zst")
    assert (session.last_line, session.last_offset) == (3, len(raw))
    assert stats["inventory_complete"] == 1


def test_missing_payload_ignores_unreadable_source(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    path.unlink()
    messages, _ = produce()
    original_stat = Path.stat

    def unreadable(value, *args, **kwargs):
        if value == path.with_suffix(".jsonl.zst"):
            raise PermissionError("unreadable sibling")
        return original_stat(value, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unreadable)
    apply(messages)

    session.refresh_from_db()
    assert session.stale is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("compute_executor_started")
def test_envelope_keeps_off_lease_preparation_and_project_effects(provider_home, monkeypatch):
    from twicc import projects
    from twicc.core.content_storage import ingestion
    from twicc.providers.codex.source_binding import CodexSourcePayload

    path, _ = rollout(provider_home)
    compress(path)
    messages, _ = produce()
    envelope = next(message for message in messages if isinstance(message, CodexSourcePayload))
    prepared = db_writer._prepare_ingestion_payload(envelope)
    effects = []

    async def added(project):
        effects.append(("added", project.id))

    async def adopted(project_id, directory):
        effects.append(("workspace", project_id, directory))

    async def no_effect(*args):
        return None

    def forbidden_prepare(*args, **kwargs):
        pytest.fail("content preparation runs inside writer publication")

    monkeypatch.setattr(ingestion, "prepare_ingestion_contents", forbidden_prepare)
    monkeypatch.setattr(db_writer, "_broadcast_project_added", added)
    monkeypatch.setattr(db_writer, "auto_add_project_to_workspaces", adopted)
    monkeypatch.setattr(projects, "ensure_worktree_link", no_effect)
    monkeypatch.setattr(projects, "ensure_project_color", no_effect)

    asyncio.run(db_writer._process_thread_message(envelope, prepared=prepared))

    assert Session.objects.get(pk="thread").items.count() == 3
    assert effects == [("added", "-tmp-compressed-project"), ("workspace", "-tmp-compressed-project", "/tmp/compressed-project")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("compute_executor_started")
def test_reconcile_job_repairs_only_association_and_broadcasts_availability(provider_home, monkeypatch):
    from twicc.providers.codex.helpers import CodexHelpers
    from twicc.providers.codex.source_binding import CodexSourcePayload, ReconcileCodexSourceJob

    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, stale=True, unavailable_reason="rollout_missing")
    compress(path)
    messages, _ = produce()
    envelope = next(message for message in messages if isinstance(message, CodexSourcePayload))._replace(ingestion=None)
    broadcasts = []

    async def broadcast(session_id):
        broadcasts.append(session_id)

    monkeypatch.setattr(db_writer, "broadcast_session_updated", broadcast)

    async def scenario():
        future = asyncio.get_running_loop().create_future()
        assert await CodexHelpers().try_handle_async_job(ReconcileCodexSourceJob(Provider.CODEX, envelope, future), None)
        assert await future is True
        return future

    asyncio.run(scenario())
    session.refresh_from_db()
    assert session.file_path.endswith(".jsonl.zst")
    assert (session.stale, session.unavailable_reason) == (False, None)
    assert broadcasts == [session.id]


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("compute_executor_started")
def test_reconcile_job_rejects_ingestion(provider_home):
    from twicc.providers.codex.helpers import CodexHelpers
    from twicc.providers.codex.source_binding import CodexSourcePayload, ReconcileCodexSourceJob

    path, _ = rollout(provider_home)
    compress(path)
    messages, _ = produce()
    envelope = next(message for message in messages if isinstance(message, CodexSourcePayload))

    async def scenario():
        future = asyncio.get_running_loop().create_future()
        assert await CodexHelpers().try_handle_async_job(ReconcileCodexSourceJob(Provider.CODEX, envelope, future), None)
        with pytest.raises(ValueError, match="only repair source associations"):
            await future

    asyncio.run(scenario())
    assert not Session.objects.exists()


def test_pure_representation_repair_keeps_uninitialized_storage_identity(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, stale=True, unavailable_reason="rollout_missing")
    before = (session.storage_incarnation, session.history_epoch, session.content_storage_version)
    compressed = compress(path)
    os.utime(compressed, (1234, 1234))

    messages, _ = produce()
    apply(messages)

    session.refresh_from_db()
    assert (session.storage_incarnation, session.history_epoch, session.content_storage_version) == before
    assert session.mtime == 1234
    assert session.compute_version == 42


def test_restoration_selects_plain_and_preserves_checkpoint(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    compressed = compress(path, remove=False)
    Session.objects.filter(pk=session.pk).update(file_path=str(compressed.relative_to(provider_home.codex / "sessions")))

    messages, _ = produce()
    apply(messages)

    session.refresh_from_db()
    assert session.file_path.endswith(".jsonl")
    assert (session.last_offset, session.last_line, session.history_epoch) == (len(raw), 3, 4)
    assert session.items.count() == 3


def test_unrelated_duplicate_does_not_clear_missing_diagnosis(provider_home):
    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, stale=True, unavailable_reason="rollout_missing")
    path.unlink()
    duplicate, _ = rollout(provider_home, name="unrelated")
    compress(duplicate)

    messages, _ = produce()
    apply(messages)

    session.refresh_from_db()
    assert (session.stale, session.unavailable_reason) == (True, "rollout_missing")


def test_new_compressed_envelope_cannot_overwrite_concurrent_existing_session(provider_home):
    path, raw = rollout(provider_home)
    compressed = compress(path)
    messages, _ = produce()
    # The watcher creates this ID elsewhere after inventory but before apply.
    path.write_bytes(raw)
    session = existing(provider_home, path, raw, prefix=1)
    path.unlink()
    Session.objects.filter(pk=session.pk).update(file_path="different/rollout-thread.jsonl")

    assert apply(messages) == [False]
    session.refresh_from_db()
    assert session.file_path == "different/rollout-thread.jsonl"
    assert session.items.count() == 1
    assert compressed.exists()


def test_new_source_compressed_after_inventory_still_imports(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    original_probe = initial_sync.check_file_has_content

    def transition(read_path):
        if read_path == path and path.exists():
            compress(path)
        return original_probe(read_path)

    monkeypatch.setattr(initial_sync, "check_file_has_content", transition)
    messages, stats = produce()
    apply(messages)

    session = Session.objects.get(pk="thread")
    assert session.file_path.endswith(".jsonl.zst")
    assert (session.last_offset, session.last_line) == (len(raw), 3)
    assert stats["inventory_complete"] == 1


def test_compressed_preparation_can_select_restored_plain_for_new_source(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    compress(path)
    original_prepare = initial_sync.prepare_rollout

    def restore(*args, **kwargs):
        path.write_bytes(raw)
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(initial_sync, "prepare_rollout", restore)
    messages, stats = produce()
    assert apply(messages) == [True]

    session = Session.objects.get(pk="thread")
    assert session.file_path.endswith(".jsonl")
    assert session.last_offset == len(raw)
    assert stats["inventory_complete"] == 1


def test_queued_compressed_payload_owns_items_after_temporary_cleanup(provider_home, monkeypatch):
    path, _ = rollout(provider_home)
    compress(path)
    original_prepare = initial_sync.prepare_rollout
    temporary_paths = []

    def record(*args, **kwargs):
        source = original_prepare(*args, **kwargs)
        temporary_paths.append(source.read_path)
        return source

    monkeypatch.setattr(initial_sync, "prepare_rollout", record)
    messages, _ = produce()

    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()
    apply(messages)
    assert Session.objects.get(pk="thread").items.count() == 3


def test_legacy_guardian_compressed_while_stopped_is_removed(provider_home):
    path, raw = rollout(provider_home, guardian=True)
    session = existing(provider_home, path, raw, model="codex-auto-review")
    compress(path)

    messages, stats = produce()
    for message in messages:
        if isinstance(message, db_writer.DeleteSessionsPayload):
            db_writer._apply_delete_sessions_payload(message)
    apply(messages)

    assert not Session.objects.filter(pk=session.pk).exists()
    assert stats["inventory_complete"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("compute_executor_started")
def test_obsolete_startup_envelope_counts_failure_and_queues_selected_catch_up(provider_home, monkeypatch):
    from collections import defaultdict
    from watchfiles import Change
    from twicc.providers.codex import sessions_watcher
    from twicc.providers.codex.source_binding import CodexSourcePayload

    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw, prefix=1)
    compress(path)
    messages, _ = produce()
    envelope = next(message for message in messages if isinstance(message, CodexSourcePayload))
    path.write_bytes(raw)
    watcher = sessions_watcher.CodexSessionsWatcher()
    monkeypatch.setattr(sessions_watcher, "_watcher", watcher)
    monkeypatch.setattr(db_writer, "_initial_sync_failures", defaultdict(int))

    async def scenario():
        await db_writer._process_thread_message(envelope)
        turn = await asyncio.wait_for(watcher._queue.next_change(), 1)
        assert (turn.path, turn.change) == (path, Change.modified)

    asyncio.run(scenario())
    session.refresh_from_db()
    assert session.last_line == 1
    assert session.file_path.endswith(".jsonl")
    assert db_writer._initial_sync_failures[Provider.CODEX] == 1


@pytest.mark.parametrize("state", ["absent", "closed", "inactive", "active"])
def test_rejected_source_catch_up_retains_pending_work_until_watcher_activation(provider_home, monkeypatch, state):
    from twicc.providers.codex import sessions_watcher
    from twicc.providers.codex.source_binding import CodexSourcePayload, request_selected_source_catch_up
    from watchfiles import Change

    path, _ = rollout(provider_home)
    compress(path)
    messages, _ = produce()
    envelope = next(message for message in messages if isinstance(message, CodexSourcePayload))
    watcher = None if state == "absent" else sessions_watcher.CodexSessionsWatcher()
    if state == "closed":
        watcher._queue.close(cancel_pending=True)
    monkeypatch.setattr(sessions_watcher, "_watcher", watcher)

    async def scenario():
        activated = asyncio.Event()
        consumed = []
        if state == "active":
            watcher._channel_layer = object()

            async def consumer():
                consumed.append(await watcher._queue.next_change())
                activated.set()

            monkeypatch.setattr(watcher, "_consume_changes", consumer)
        await request_selected_source_catch_up(envelope)
        current = sessions_watcher.get_watcher()
        if state == "active":
            await asyncio.wait_for(activated.wait(), 1)
            await current._consumer_task
            assert [(turn.path, turn.change) for turn in consumed] == [(path, Change.modified)]
        else:
            assert current._consumer_task is None
            current.prepare_start()
            turn = await asyncio.wait_for(current._queue.next_change(), 1)
            assert (turn.path, turn.change) == (path, Change.modified)

    asyncio.run(scenario())
