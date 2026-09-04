from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers.codex.background_compute import (
    CodexComputeCandidate,
    CodexComputeCoordinator,
    DeferredCandidate,
    FailedCandidate,
    PreparedCandidate,
)
from twicc.providers.codex.initial_sync import extract_session_meta
from twicc.providers.codex.rollout_migration import (
    MAX_ROLLOUT_LINE_BYTES,
    CodexMigrationRunner,
    HistoryMode,
    MigrationPreparation,
    RolloutMigrationError,
    get_db_history_mode,
    history_mode_from_record,
    migration_preparation,
    parse_migration_report,
    preflight_rollout,
)
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.db_writer import start_db_writer, stop_db_writer
from twicc.providers.sessions_watcher import ParsedSessionFile


def _session_meta(*, history_mode: str | None = None) -> dict:
    payload = {"id": "thread-1", "cwd": "/repo"}
    if history_mode is not None:
        payload["history_mode"] = history_mode
    return {"timestamp": "2026-08-31T10:00:00Z", "type": "session_meta", "payload": payload}


@pytest.mark.parametrize(
    ("source", "database", "expected"),
    [
        (HistoryMode.LEGACY, HistoryMode.LEGACY, MigrationPreparation.MIGRATE_AND_REPLACE),
        (HistoryMode.PAGINATED, HistoryMode.LEGACY, MigrationPreparation.REPLACE_ONLY),
        (HistoryMode.PAGINATED, HistoryMode.PAGINATED, MigrationPreparation.COMPUTE_ONLY),
        (HistoryMode.LEGACY, HistoryMode.PAGINATED, MigrationPreparation.INCONSISTENT),
    ],
)
def test_migration_preparation_matrix(source, database, expected):
    assert migration_preparation(source, database) == expected


def test_only_explicit_paginated_history_mode_is_paginated(tmp_path):
    assert history_mode_from_record(_session_meta(history_mode="paginated")) == HistoryMode.PAGINATED
    assert history_mode_from_record(_session_meta(history_mode="legacy")) == HistoryMode.LEGACY
    assert history_mode_from_record(_session_meta()) == HistoryMode.LEGACY

    rollout = tmp_path / "rollout-thread-1.jsonl"
    rollout.write_bytes(orjson.dumps(_session_meta(history_mode="paginated")) + b"\n")
    assert extract_session_meta(rollout).history_mode == HistoryMode.PAGINATED


@pytest.mark.django_db
def test_watcher_creates_a_new_legacy_session_as_compute_stale():
    project = Project.objects.create(id="watcher-migration-project")
    parsed = ParsedSessionFile(
        project.id,
        "legacy-created-after-startup",
        SessionType.SESSION,
        "2026/08/31/rollout.jsonl",
        compute_ready_on_create=False,
    )

    session = CodexSessionsWatcher().create_session_sync(parsed, project)

    assert session.compute_version is None


@pytest.mark.django_db(transaction=True)
def test_db_history_mode_reads_the_first_stored_item():
    project = Project.objects.create(id="migration-project")
    session = Session.objects.create(id="thread-1", project=project, provider=Provider.CODEX)
    SessionItem.objects.create(
        session=session,
        line_num=2,
        content=orjson.dumps({"type": "event_msg", "payload": {"type": "token_count"}}).decode(),
    )
    SessionItem.objects.create(
        session=session,
        line_num=1,
        content=orjson.dumps(_session_meta(history_mode="paginated")).decode(),
    )

    assert asyncio.run(get_db_history_mode(session.id)) == HistoryMode.PAGINATED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("content", [None, "not-json", "[]", '{"type":"event_msg"}'])
def test_db_history_mode_rejects_missing_or_malformed_first_item(content):
    project = Project.objects.create(id=f"migration-project-{content}")
    session = Session.objects.create(id=f"thread-{abs(hash(content))}", project=project, provider=Provider.CODEX)
    if content is not None:
        SessionItem.objects.create(session=session, line_num=1, content=content)

    with pytest.raises(RolloutMigrationError):
        asyncio.run(get_db_history_mode(session.id))


def test_preflight_counts_complete_malformed_blank_retired_and_partial_lines(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    records = [
        orjson.dumps(_session_meta()),
        b"not-json",
        b"   ",
        orjson.dumps({"type": "event_msg", "payload": {"type": "guardian_assessment"}}),
        orjson.dumps({"type": "event_msg", "payload": {"type": "thread_name_updated"}}),
        orjson.dumps({"type": "event_msg", "payload": {"type": "undo_completed"}}),
        orjson.dumps({"type": "response_item", "payload": {"type": "ghost_snapshot"}}),
    ]
    rollout.write_bytes(b"\n".join(records) + b"\n" + b'{"partial":true}')

    result = preflight_rollout(rollout)

    assert result.complete_lines == 7
    assert result.malformed_lines == 1
    assert result.blank_lines == 1
    assert result.retired_lines == 4
    assert result.partial_trailing_line is True
    assert result.oversized_line is None
    assert result.oversized_bytes is None


def test_preflight_streams_and_reports_the_exact_oversized_record_size(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    oversized = b'x' * (MAX_ROLLOUT_LINE_BYTES + 123)
    rollout.write_bytes(orjson.dumps(_session_meta()) + b"\n" + oversized + b"\n" + b"{}\n")

    result = preflight_rollout(rollout)

    assert result.complete_lines == 3
    assert result.oversized_line == 2
    assert result.oversized_bytes == len(oversized) + 1


def _report(path: Path, *, status: str = "migrated", thread_id: str = "thread-1") -> bytes:
    return orjson.dumps({
        "outcomes": [{
            "thread_id": thread_id,
            "rollout_path": str(path),
            "status": status,
            "bytes_processed": 123,
            "message": None,
        }],
    })


@pytest.mark.parametrize("status", ["migrated", "already_paginated", "skipped_busy", "failed", "skipped_empty"])
def test_parse_migration_report_accepts_known_apply_statuses(tmp_path, status):
    rollout = tmp_path / "rollout.jsonl"
    outcome = parse_migration_report(_report(rollout, status=status), "thread-1", rollout)
    assert outcome.status == status
    assert outcome.bytes_processed == 123


@pytest.mark.parametrize(
    "report",
    [
        b"not-json",
        orjson.dumps({"outcomes": []}),
        orjson.dumps({"outcomes": [{"thread_id": "thread-1"}, {"thread_id": "thread-1"}]}),
    ],
)
def test_parse_migration_report_rejects_bad_shapes(tmp_path, report):
    with pytest.raises(RolloutMigrationError):
        parse_migration_report(report, "thread-1", tmp_path / "rollout.jsonl")


def test_parse_migration_report_rejects_thread_path_and_status_mismatches(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    with pytest.raises(RolloutMigrationError):
        parse_migration_report(_report(rollout, thread_id="other"), "thread-1", rollout)
    with pytest.raises(RolloutMigrationError):
        parse_migration_report(_report(tmp_path / "other.jsonl"), "thread-1", rollout)
    with pytest.raises(RolloutMigrationError):
        parse_migration_report(_report(rollout, status="eligible"), "thread-1", rollout)


def test_runner_uses_the_exact_cli_and_parses_nonzero_failed_report(tmp_path, monkeypatch):
    rollout = tmp_path / "rollout.jsonl"
    argv_path = tmp_path / "argv.json"
    binary = tmp_path / "fake-codex"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['ARGV_PATH']).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({_report(rollout, status='failed').decode()!r})\n"
        "raise SystemExit(1)\n"
    )
    binary.chmod(0o755)

    async def fake_command():
        from twicc.providers.codex.bin import CodexCommand
        return CodexCommand(binary=binary, env={**os.environ, "ARGV_PATH": str(argv_path)})

    monkeypatch.setattr("twicc.providers.codex.rollout_migration.resolve_codex_command", fake_command)
    outcome = asyncio.run(CodexMigrationRunner().run("thread-1", rollout))

    assert outcome.status == "failed"
    assert orjson.loads(argv_path.read_bytes()) == [
        "migrate-rollouts", "--apply", "--thread", "thread-1", "--json",
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("status", ["migrated", "skipped_busy", "failed"])
def test_fake_codex_binary_drives_candidate_preparation(tmp_path, monkeypatch, status):
    session_id = f"fake-binary-{status}"
    legacy_meta = _session_meta()
    legacy_meta["payload"]["id"] = session_id
    paginated_meta = _session_meta(history_mode="paginated")
    paginated_meta["payload"]["id"] = session_id
    canonical = b"\n".join([
        orjson.dumps(paginated_meta),
        orjson.dumps({
            "timestamp": "2026-08-31T10:00:01Z",
            "ordinal": 1,
            "type": "event_msg",
            "payload": {"type": "token_count"},
        }),
    ]) + b"\n"

    rollout = tmp_path / f"rollout-{session_id}.jsonl"
    rollout.write_bytes(orjson.dumps(legacy_meta) + b"\n")
    binary = tmp_path / f"fake-codex-{status}"
    rewrite = (
        f"pathlib.Path(os.environ['ROLLOUT_PATH']).write_bytes({canonical!r})\n"
        if status == "migrated"
        else ""
    )
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib\n"
        f"{rewrite}"
        f"print({_report(rollout, status=status, thread_id=session_id).decode()!r})\n"
        f"raise SystemExit({1 if status == 'failed' else 0})\n"
    )
    binary.chmod(0o755)

    project = Project.objects.create(id=f"fake-binary-project-{status}")
    session = Session.objects.create(
        id=session_id,
        project=project,
        provider=Provider.CODEX,
        file_path=str(rollout),
    )
    SessionItem.objects.create(
        session=session,
        line_num=1,
        content=orjson.dumps(legacy_meta).decode(),
    )

    async def fake_command():
        from twicc.providers.codex.bin import CodexCommand

        return CodexCommand(
            binary=binary,
            env={**os.environ, "ROLLOUT_PATH": str(rollout)},
        )

    monkeypatch.setattr("twicc.providers.codex.rollout_migration.resolve_codex_command", fake_command)

    async def scenario():
        start_db_writer()
        result = None
        try:
            coordinator = CodexComputeCoordinator(
                SimpleNamespace(compute_version=2, stop_event=asyncio.Event()),
                asyncio.Event(),
            )
            result = await coordinator.prepare_candidate(
                CodexComputeCandidate(session_id, rollout, SessionType.SESSION)
            )
            mode = await get_db_history_mode(session_id)
            count = await SessionItem.objects.filter(session_id=session_id).acount()
            return result, mode, count
        finally:
            if isinstance(result, PreparedCandidate) and result.migration_lease is not None:
                result.migration_lease.release()
            await stop_db_writer()

    result, mode, count = asyncio.run(scenario())

    if status == "migrated":
        assert isinstance(result, PreparedCandidate)
        assert mode == HistoryMode.PAGINATED
        assert count == 2
    elif status == "skipped_busy":
        assert result == DeferredCandidate(session_id, "skipped_busy")
        assert mode == HistoryMode.LEGACY
        assert count == 1
    else:
        assert isinstance(result, FailedCandidate)
        assert result.phase == "Codex migration"
        assert mode == HistoryMode.LEGACY
        assert count == 1
