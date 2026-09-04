from __future__ import annotations

import asyncio
import os
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import orjson
from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import AgentLink, Session, SessionItem, Share, ToolResultLink

from .bin import resolve_codex_command

MAX_ROLLOUT_LINE_BYTES = 16 * 1024 * 1024
SNAPSHOT_ANCHOR_KEY = "_codex_rollout_migration_anchor"
_PROCESS_SHUTDOWN_GRACE_SECONDS = 2.0


class RolloutMigrationError(RuntimeError):
    """A rollout cannot be prepared or migrated safely."""


class HistoryMode(StrEnum):
    LEGACY = "legacy"
    PAGINATED = "paginated"


class MigrationPreparation(StrEnum):
    MIGRATE_AND_REPLACE = "migrate_and_replace"
    REPLACE_ONLY = "replace_only"
    COMPUTE_ONLY = "compute_only"
    INCONSISTENT = "inconsistent"


class RolloutPreflight(NamedTuple):
    complete_lines: int
    malformed_lines: int
    blank_lines: int
    retired_lines: int
    partial_trailing_line: bool
    oversized_line: int | None
    oversized_bytes: int | None


class CodexMigrationOutcome(NamedTuple):
    status: str
    bytes_processed: int
    message: str | None


class PreparedCodexHistory(NamedTuple):
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float


class CaptureSnapshotAnchorsJob(NamedTuple):
    provider: Provider
    session_id: str
    future: asyncio.Future


class ClearSnapshotAnchorsJob(NamedTuple):
    provider: Provider
    session_id: str
    future: asyncio.Future


class ReplaceCodexHistoryJob(NamedTuple):
    provider: Provider
    session_id: str
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    future: asyncio.Future


def history_mode_from_record(record: object) -> HistoryMode:
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return HistoryMode.LEGACY
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("history_mode") == HistoryMode.PAGINATED:
        return HistoryMode.PAGINATED
    return HistoryMode.LEGACY


def migration_preparation(source: HistoryMode, database: HistoryMode) -> MigrationPreparation:
    return {
        (HistoryMode.LEGACY, HistoryMode.LEGACY): MigrationPreparation.MIGRATE_AND_REPLACE,
        (HistoryMode.PAGINATED, HistoryMode.LEGACY): MigrationPreparation.REPLACE_ONLY,
        (HistoryMode.PAGINATED, HistoryMode.PAGINATED): MigrationPreparation.COMPUTE_ONLY,
        (HistoryMode.LEGACY, HistoryMode.PAGINATED): MigrationPreparation.INCONSISTENT,
    }[(source, database)]


async def get_db_history_mode(session_id: str) -> HistoryMode:
    first = await SessionItem.objects.filter(session_id=session_id).order_by("line_num").only("content").afirst()
    if first is None:
        raise RolloutMigrationError(f"Session {session_id} has no stored first item")
    try:
        parsed = orjson.loads(first.content)
    except (orjson.JSONDecodeError, TypeError) as error:
        raise RolloutMigrationError(f"Session {session_id} has malformed first item") from error
    if not isinstance(parsed, dict) or parsed.get("type") != "session_meta":
        raise RolloutMigrationError(f"Session {session_id} first item is not session_meta")
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        raise RolloutMigrationError(f"Session {session_id} session_meta has no payload")
    return history_mode_from_record(parsed)


def _is_retired_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False
    payload_type = payload.get("type")
    if record.get("type") == "event_msg":
        return payload_type in {"guardian_assessment", "thread_name_updated", "undo_completed"}
    return record.get("type") == "response_item" and payload_type == "ghost_snapshot"


def preflight_rollout(path: Path) -> RolloutPreflight:
    complete_lines = 0
    malformed_lines = 0
    blank_lines = 0
    retired_lines = 0
    partial_trailing_line = False
    oversized_line: int | None = None
    oversized_bytes: int | None = None
    source_line = 0

    try:
        source = path.open("rb")
    except OSError as error:
        raise RolloutMigrationError(f"Cannot read rollout {path}: {error}") from error

    with source:
        while True:
            first_chunk = source.readline(MAX_ROLLOUT_LINE_BYTES + 1)
            if not first_chunk:
                break
            source_line += 1
            byte_count = len(first_chunk)
            complete = first_chunk.endswith(b"\n")
            while not complete:
                chunk = source.readline(MAX_ROLLOUT_LINE_BYTES + 1)
                if not chunk:
                    partial_trailing_line = True
                    break
                byte_count += len(chunk)
                complete = chunk.endswith(b"\n")
            if not complete:
                break

            complete_lines += 1
            if byte_count > MAX_ROLLOUT_LINE_BYTES:
                if oversized_line is None:
                    oversized_line = source_line
                    oversized_bytes = byte_count
                continue

            if not first_chunk.strip():
                blank_lines += 1
                continue
            try:
                parsed = orjson.loads(first_chunk)
            except orjson.JSONDecodeError:
                malformed_lines += 1
                continue
            if _is_retired_record(parsed):
                retired_lines += 1

    return RolloutPreflight(
        complete_lines=complete_lines,
        malformed_lines=malformed_lines,
        blank_lines=blank_lines,
        retired_lines=retired_lines,
        partial_trailing_line=partial_trailing_line,
        oversized_line=oversized_line,
        oversized_bytes=oversized_bytes,
    )


def prepare_full_history(path: Path) -> PreparedCodexHistory:
    try:
        with path.open("rb") as source:
            raw = source.read()
            last_offset = source.tell()
            opened_stat = os.fstat(source.fileno())
    except OSError as error:
        raise RolloutMigrationError(f"Cannot read canonical rollout {path}: {error}") from error
    try:
        current_stat = path.stat()
    except OSError as error:
        raise RolloutMigrationError(f"Canonical rollout disappeared after read: {path}") from error
    if (opened_stat.st_dev, opened_stat.st_ino) != (current_stat.st_dev, current_stat.st_ino):
        raise RolloutMigrationError(f"Canonical rollout changed identity during read: {path}")

    items: list[tuple[int, str]] = []
    for record in raw.splitlines():
        if not record.strip():
            continue
        items.append((len(items) + 1, record.decode("utf-8", errors="replace")))
    if not items:
        raise RolloutMigrationError(f"Canonical rollout is empty: {path}")
    try:
        first = orjson.loads(items[0][1])
    except orjson.JSONDecodeError as error:
        raise RolloutMigrationError(f"Canonical rollout has malformed session_meta: {path}") from error
    if history_mode_from_record(first) != HistoryMode.PAGINATED:
        raise RolloutMigrationError(f"Rollout is not paginated after migration: {path}")
    return PreparedCodexHistory(
        items=items,
        last_offset=last_offset,
        last_line=len(items),
        mtime=current_stat.st_mtime,
    )


def _snapshot_shares_for_update(session_id: str):
    return Share.objects.select_for_update().filter(session_id=session_id, kind="session")


@transaction.atomic
def _apply_capture_snapshot_anchors_job(job: CaptureSnapshotAnchorsJob) -> int:
    Session.objects.select_for_update().get(id=job.session_id)
    changed = 0
    for share in _snapshot_shares_for_update(job.session_id):
        options = dict(share.options or {})
        if options.get("mode") != "snapshot":
            continue
        existing = options.get(SNAPSHOT_ANCHOR_KEY)
        if isinstance(existing, dict) and isinstance(existing.get("timestamp"), str):
            continue
        frozen_at_line = options.get("frozen_at_line")
        if not isinstance(frozen_at_line, int) or isinstance(frozen_at_line, bool):
            raise RolloutMigrationError(f"Snapshot share {share.id} has no valid frozen_at_line")
        timestamp = (
            SessionItem.objects.filter(
                session_id=job.session_id,
                line_num__lte=frozen_at_line,
                timestamp__isnull=False,
            )
            .order_by("-line_num")
            .values_list("timestamp", flat=True)
            .first()
        )
        if timestamp is None:
            raise RolloutMigrationError(f"Snapshot share {share.id} has no timestamp anchor")
        options[SNAPSHOT_ANCHOR_KEY] = {"timestamp": timestamp.isoformat()}
        share.options = options
        share.save(update_fields=["options", "updated_at"])
        changed += 1
    return changed


@transaction.atomic
def _apply_clear_snapshot_anchors_job(job: ClearSnapshotAnchorsJob) -> int:
    changed = 0
    for share in _snapshot_shares_for_update(job.session_id):
        options = dict(share.options or {})
        if SNAPSHOT_ANCHOR_KEY not in options:
            continue
        options.pop(SNAPSHOT_ANCHOR_KEY)
        share.options = options
        share.save(update_fields=["options", "updated_at"])
        changed += 1
    return changed


@transaction.atomic
def _apply_replace_codex_history_job(job: ReplaceCodexHistoryJob) -> int:
    session = Session.objects.select_for_update().get(id=job.session_id)
    ToolResultLink.objects.filter(session_id=job.session_id).delete()
    AgentLink.objects.filter(session_id=job.session_id).delete()
    SessionItem.objects.filter(session_id=job.session_id).delete()
    SessionItem.objects.bulk_create([
        SessionItem(session_id=job.session_id, line_num=line_num, content=content)
        for line_num, content in job.items
    ], batch_size=100)
    session.last_offset = job.last_offset
    session.last_line = job.last_line
    session.mtime = job.mtime
    session.tasks = {}
    session.search_version = None
    session.save(update_fields=["last_offset", "last_line", "mtime", "tasks", "search_version"])
    return len(job.items)


_KNOWN_APPLY_STATUSES = frozenset({
    "migrated",
    "already_paginated",
    "skipped_busy",
    "failed",
    "skipped_empty",
})


def parse_migration_report(
    stdout: bytes,
    session_id: str,
    rollout_path: Path,
) -> CodexMigrationOutcome:
    try:
        report = orjson.loads(stdout)
    except orjson.JSONDecodeError as error:
        raise RolloutMigrationError("Codex migration returned invalid JSON") from error
    outcomes = report.get("outcomes") if isinstance(report, dict) else None
    if not isinstance(outcomes, list) or len(outcomes) != 1 or not isinstance(outcomes[0], dict):
        raise RolloutMigrationError("Codex migration must return exactly one outcome")
    outcome = outcomes[0]
    if outcome.get("thread_id") != session_id:
        raise RolloutMigrationError("Codex migration returned a different thread id")
    returned_path = outcome.get("rollout_path")
    if not isinstance(returned_path, str) or Path(returned_path).resolve() != rollout_path.resolve():
        raise RolloutMigrationError("Codex migration returned a different rollout path")
    status = outcome.get("status")
    if status not in _KNOWN_APPLY_STATUSES:
        raise RolloutMigrationError(f"Unexpected Codex migration status: {status!r}")
    bytes_processed = outcome.get("bytes_processed")
    if not isinstance(bytes_processed, int) or isinstance(bytes_processed, bool) or bytes_processed < 0:
        raise RolloutMigrationError("Codex migration returned invalid bytes_processed")
    message = outcome.get("message")
    if message is not None and not isinstance(message, str):
        raise RolloutMigrationError("Codex migration returned an invalid message")
    return CodexMigrationOutcome(status, bytes_processed, message)


class CodexMigrationRunner:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    async def run(self, session_id: str, rollout_path: Path) -> CodexMigrationOutcome:
        command = await resolve_codex_command()
        process = await asyncio.create_subprocess_exec(
            str(command.binary),
            "migrate-rollouts",
            "--apply",
            "--thread",
            session_id,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command.env,
        )
        self.process = process
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            await self.stop()
            raise
        finally:
            if self.process is process and process.returncode is not None:
                self.process = None

        outcome = parse_migration_report(stdout, session_id, rollout_path)
        if process.returncode and outcome.status != "failed":
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RolloutMigrationError(
                f"Codex migration exited with {process.returncode}: {detail or outcome.status}"
            )
        return outcome

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=_PROCESS_SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
        else:
            await process.wait()
        if self.process is process:
            self.process = None
