from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import orjson
import pytest
from django.db import IntegrityError

from twicc.core.enums import Provider
from twicc.core.models import (
    AgentLink,
    Project,
    Session,
    SessionItem,
    Share,
    ToolResultLink,
)
from twicc.core.serializers import serialize_share, serialize_share_public_meta
from twicc.providers.codex.rollout_migration import (
    MarkSessionRebuildJob,
    MarkSessionUnavailableJob,
    _apply_mark_session_rebuild_job,
    _apply_mark_session_unavailable_job,
    SNAPSHOT_ANCHOR_KEY,
    CaptureSnapshotAnchorsJob,
    ClearSnapshotAnchorsJob,
    ReplaceCodexHistoryJob,
    RolloutMigrationError,
    _apply_capture_snapshot_anchors_job,
    _apply_clear_snapshot_anchors_job,
    _apply_replace_codex_history_job,
    apply_replace_codex_history_job_in_slices,
    prepare_full_history,
)
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db


@pytest.fixture
def session(tmp_path):
    project = Project.objects.create(id="migration-jobs-project", directory=str(tmp_path))
    return Session.objects.create(
        id="migration-jobs-session",
        project=project,
        provider=Provider.CODEX,
        compute_version=42,
        search_version=7,
        tasks={"items": [{"content": "old"}]},
        last_offset=100,
        last_line=3,
        mtime=1.0,
    )


def _item(session, line_num, timestamp=None, content=None):
    return SessionItem.objects.create(
        session=session,
        line_num=line_num,
        timestamp=timestamp,
        content=content or orjson.dumps({"line": line_num}).decode(),
    )


def _future():
    return asyncio.new_event_loop().create_future()


def test_capture_snapshot_anchor_uses_last_valid_timestamp_before_freeze(session):
    first = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
    second = datetime(2026, 8, 31, 10, 1, tzinfo=UTC)
    _item(session, 1, first)
    _item(session, 2, None)
    _item(session, 3, second)
    share = Share.objects.create(
        kind="session",
        token="a" * 64,
        session=session,
        options={"mode": "snapshot", "frozen_at_line": 2},
    )

    changed = _apply_capture_snapshot_anchors_job(
        CaptureSnapshotAnchorsJob(Provider.CODEX, session.id, _future())
    )

    share.refresh_from_db()
    assert changed == 1
    assert share.options[SNAPSHOT_ANCHOR_KEY] == {"timestamp": first.isoformat()}


def test_capture_snapshot_anchor_is_idempotent_after_crash(session):
    _item(session, 1, datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    existing = {"timestamp": "2020-01-01T00:00:00+00:00"}
    share = Share.objects.create(
        kind="session",
        token="b" * 64,
        session=session,
        options={"mode": "snapshot", "frozen_at_line": 1, SNAPSHOT_ANCHOR_KEY: existing},
    )

    assert _apply_capture_snapshot_anchors_job(
        CaptureSnapshotAnchorsJob(Provider.CODEX, session.id, _future())
    ) == 0
    share.refresh_from_db()
    assert share.options[SNAPSHOT_ANCHOR_KEY] == existing


def test_capture_snapshot_anchor_fails_without_a_valid_timestamp(session):
    _item(session, 1, None)
    Share.objects.create(
        kind="session",
        token="c" * 64,
        session=session,
        options={"mode": "snapshot", "frozen_at_line": 1},
    )

    with pytest.raises(RolloutMigrationError):
        _apply_capture_snapshot_anchors_job(
            CaptureSnapshotAnchorsJob(Provider.CODEX, session.id, _future())
        )


def test_clear_snapshot_anchors_removes_private_state(session):
    share = Share.objects.create(
        kind="session",
        token="d" * 64,
        session=session,
        options={
            "mode": "snapshot",
            "frozen_at_line": 1,
            SNAPSHOT_ANCHOR_KEY: {"timestamp": "2026-08-31T10:00:00+00:00"},
        },
    )

    assert _apply_clear_snapshot_anchors_job(
        ClearSnapshotAnchorsJob(Provider.CODEX, session.id, _future())
    ) == 1
    share.refresh_from_db()
    assert SNAPSHOT_ANCHOR_KEY not in share.options


def test_share_serializers_hide_private_anchor(session):
    share = Share.objects.create(
        kind="session",
        token="e" * 64,
        session=session,
        options={
            "mode": "snapshot",
            "frozen_at_line": 1,
            SNAPSHOT_ANCHOR_KEY: {"timestamp": "2026-08-31T10:00:00+00:00"},
        },
    )

    assert SNAPSHOT_ANCHOR_KEY not in serialize_share(share)["options"]
    assert SNAPSHOT_ANCHOR_KEY not in orjson.dumps(serialize_share_public_meta(share)).decode()


def _seed_links(session):
    _item(session, 1)
    _item(session, 2)
    ToolResultLink.objects.create(
        session=session,
        tool_use_line_num=1,
        tool_result_line_num=2,
        tool_use_id="call-1",
        tool_name="apply_patch",
    )
    AgentLink.objects.create(
        session=session,
        tool_use_line_num=1,
        tool_use_id="call-1",
        agent_id="child-1",
    )


def test_replace_history_resets_structure_but_keeps_compute_stale(session):
    _seed_links(session)
    old_compute_version = session.compute_version
    # An earlier attempt may have condemned the session; a rebuilt history
    # makes it showable again.
    Session.objects.filter(id=session.id).update(unavailable_reason="rollout_missing", stale=True)
    contents = [
        orjson.dumps({"type": "session_meta", "payload": {"history_mode": "paginated"}}).decode(),
        orjson.dumps({"type": "event_msg", "payload": {"type": "item_completed"}}).decode(),
    ]
    job = ReplaceCodexHistoryJob(
        Provider.CODEX,
        session.id,
        list(enumerate(contents, 1)),
        555,
        2,
        99.5,
        _future(),
    )

    assert _apply_replace_codex_history_job(job) == 2

    session.refresh_from_db()
    assert list(session.items.values_list("line_num", "content")) == [(1, contents[0]), (2, contents[1])]
    assert not ToolResultLink.objects.filter(session=session).exists()
    assert not AgentLink.objects.filter(session=session).exists()
    assert (session.last_offset, session.last_line, session.mtime) == (555, 2, 99.5)
    assert session.tasks == {}
    assert session.search_version is None
    assert session.compute_version == old_compute_version
    assert session.unavailable_reason is None
    assert session.stale is False


def test_replace_history_failure_leaves_the_offset_zero_repair_marker(session):
    """The replacement runs in slices: a failure mid-way is not rolled back to
    the old history but leaves ``last_offset == 0``, the durable marker the
    coordinator repairs by replacing the history again (see
    ``_begin_replace_codex_history``)."""
    _seed_links(session)
    Session.objects.filter(id=session.id).update(last_offset=333, last_line=2)
    job = ReplaceCodexHistoryJob(
        Provider.CODEX,
        session.id,
        [(1, "new"), (1, "duplicate")],
        10,
        2,
        3.0,
        _future(),
    )

    with pytest.raises(IntegrityError):
        _apply_replace_codex_history_job(job)

    session.refresh_from_db()
    assert list(session.items.values_list("line_num", flat=True)) == []
    assert not ToolResultLink.objects.filter(session=session).exists()
    assert not AgentLink.objects.filter(session=session).exists()
    assert (session.last_offset, session.last_line) == (0, 0)


@pytest.mark.django_db(transaction=True)
def test_replace_history_in_slices_matches_the_one_shot_form(session, monkeypatch):
    from twicc.providers.codex import rollout_migration

    monkeypatch.setattr(rollout_migration, "REPLACE_HISTORY_CHUNK_SIZE", 2)
    _seed_links(session)
    contents = [orjson.dumps({"type": "session_meta", "payload": {"history_mode": "paginated"}}).decode()]
    contents += [orjson.dumps({"type": "event_msg", "payload": {"type": "token_count", "n": i}}).decode() for i in range(6)]
    job = ReplaceCodexHistoryJob(Provider.CODEX, session.id, list(enumerate(contents, 1)), 777, 7, 5.5, _future())

    count = asyncio.run(apply_replace_codex_history_job_in_slices(job))

    session.refresh_from_db()
    assert count == 7
    assert list(session.items.order_by("line_num").values_list("content", flat=True)) == contents
    assert (session.last_offset, session.last_line, session.mtime) == (777, 7, 5.5)
    assert not ToolResultLink.objects.filter(session=session).exists()


def test_item_updates_fast_path_stores_the_same_values_as_the_model_layer(session):
    """The worker's JSON shape (cost as str, timestamp as ISO) lands typed."""
    item = _item(session, 1)
    BaseSessionCompute.apply_item_updates(
        ["display_level", "group_head", "group_tail", "kind", "message_id", "cost", "context_usage", "timestamp", "git_directory", "git_branch"],
        [{
            "id": item.id, "display_level": 2, "group_head": 1, "group_tail": None, "kind": "user_message",
            "message_id": "msg-1", "cost": "0.001234", "context_usage": 4321,
            "timestamp": "2026-08-31T10:00:05.250000+00:00", "git_directory": "/repo", "git_branch": "main",
        }],
    )
    BaseSessionCompute.apply_content_overrides([{"id": item.id, "content": "rewritten"}])

    item.refresh_from_db()
    assert (item.display_level, item.group_head, item.group_tail, item.kind, item.message_id) == (2, 1, None, "user_message", "msg-1")
    assert item.cost == Decimal("0.001234")
    assert item.context_usage == 4321
    assert item.timestamp == datetime(2026, 8, 31, 10, 0, 5, 250000, tzinfo=UTC)
    assert (item.git_directory, item.git_branch, item.content) == ("/repo", "main", "rewritten")


@pytest.mark.django_db(transaction=True)
def test_db_writer_applies_large_results_in_slices_and_stops_when_superseded(session, monkeypatch):
    from twicc.providers import db_writer

    monkeypatch.setattr(db_writer, "COMPUTE_APPLY_CHUNK_SIZE", 2)
    items = [_item(session, n) for n in range(1, 8)]
    Session.objects.filter(id=session.id).update(last_offset=100)
    msg = {
        "session_id": session.id,
        "observed_last_offset": 100,
        "item_fields": ["kind"],
        "item_updates": [{"id": it.id, "kind": f"k{it.line_num}"} for it in items],
        "content_overrides": [{"id": items[0].id, "content": "override"}],
    }

    trimmed, outcome = asyncio.run(db_writer._apply_compute_items_in_chunks(msg))

    assert outcome == "ok"
    assert trimmed["item_updates"] == [] and trimmed["content_overrides"] == []
    assert list(session.items.order_by("line_num").values_list("kind", flat=True)) == [f"k{n}" for n in range(1, 8)]
    assert session.items.get(line_num=1).content == "override"

    # The watcher moved on (last_offset advanced): the first slice already
    # reports it and nothing more is written.
    Session.objects.filter(id=session.id).update(last_offset=200)
    msg["item_updates"] = [{"id": it.id, "kind": "stale"} for it in items]
    _, outcome = asyncio.run(db_writer._apply_compute_items_in_chunks(msg))
    assert outcome == "superseded"
    assert not session.items.filter(kind="stale").exists()


def test_prepare_full_history_reads_from_zero_and_drops_blank_records(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    first = orjson.dumps({
        "type": "session_meta",
        "payload": {"id": "thread-1", "cwd": "/repo", "history_mode": "paginated"},
    })
    second = b'{"type":"event_msg","bad":"\xff"}'
    rollout.write_bytes(first + b"\n\n" + second + b"\n")

    prepared = prepare_full_history(rollout)

    assert prepared.items[0] == (1, first.decode())
    assert prepared.items[1][0] == 2
    assert "�" in prepared.items[1][1]
    assert prepared.last_line == 2
    assert prepared.last_offset == rollout.stat().st_size
    assert prepared.mtime == rollout.stat().st_mtime


def test_prepare_full_history_rejects_a_non_paginated_source(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_bytes(orjson.dumps({"type": "session_meta", "payload": {"id": "thread-1"}}) + b"\n")
    with pytest.raises(RolloutMigrationError):
        prepare_full_history(rollout)


def test_final_compute_apply_remaps_snapshot_and_invalidates_search(session):
    anchor = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
    first = _item(session, 1, anchor)
    second = _item(session, 2, anchor)
    share = Share.objects.create(
        kind="session",
        token="f" * 64,
        session=session,
        options={
            "mode": "snapshot",
            "frozen_at_line": 99,
            SNAPSHOT_ANCHOR_KEY: {"timestamp": anchor.isoformat()},
        },
    )
    msg = {
        "session_id": session.id,
        "observed_last_offset": session.last_offset,
        "item_updates": [
            {"id": first.id, "timestamp": anchor.isoformat()},
            {"id": second.id, "timestamp": anchor.isoformat()},
        ],
        "item_fields": ["timestamp"],
        "session_fields": {"compute_version": 43},
    }

    result = BaseSessionCompute.apply_session_complete(msg)

    assert result.outcome == "applied"
    session.refresh_from_db()
    share.refresh_from_db()
    assert session.search_version is None
    assert share.options["frozen_at_line"] == 2
    assert SNAPSHOT_ANCHOR_KEY not in share.options


def test_unavailable_and_rebuild_jobs_touch_only_their_column(session):
    assert _apply_mark_session_unavailable_job(
        MarkSessionUnavailableJob(Provider.CODEX, session.id, "codex_migration_failed:invalid_session_metadata", _future()),
    ) == 1
    session.refresh_from_db()
    assert session.unavailable_reason == "codex_migration_failed:invalid_session_metadata"

    assert _apply_mark_session_unavailable_job(MarkSessionUnavailableJob(Provider.CODEX, session.id, None, _future())) == 1
    session.refresh_from_db()
    assert session.unavailable_reason is None

    Session.objects.filter(id=session.id).update(compute_version=7)
    assert _apply_mark_session_rebuild_job(MarkSessionRebuildJob(Provider.CODEX, session.id, _future())) == 1
    session.refresh_from_db()
    assert session.compute_version is None
