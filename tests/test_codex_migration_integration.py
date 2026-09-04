"""Rollout-migration integration tests against the REAL bundled Codex binary.

Opt-in: ``TWICC_CODEX_INTEGRATION=1`` (and the runtime already downloaded
under ``~/.cache/twicc/codex-runtime/<version>/``). Everything runs in a
throwaway ``CODEX_HOME`` under ``tmp_path`` (the ``provider_home`` fixture):
Codex creates its state DB there, so the developer's real ``~/.codex`` is
never read or written.

Each test drives ``CodexComputeCoordinator.prepare_candidate`` — the part of
the pipeline that talks to Codex (``migrate-rollouts --apply``, the
registration ``thread/resume``) and replaces TwiCC's history through the real
DB writer. The CPU worker / metadata apply half is covered by the unit tests.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers.codex import background_compute
from twicc.providers.codex.background_compute import (
    CodexComputeCandidate,
    CodexComputeCoordinator,
    DeferredCandidate,
    FailedCandidate,
    PreparedCandidate,
)
from twicc.providers.codex.migration_gate import is_migrating
from twicc.providers.codex.rollout_migration import (
    CodexMigrationRunner,
    HistoryMode,
    get_db_history_mode,
)
from twicc.providers.codex.runtime import is_runtime_ready
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.db_writer import start_db_writer, stop_db_writer
from twicc.providers.sessions_watcher import ParsedSessionFile

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("TWICC_CODEX_INTEGRATION") != "1",
        reason="set TWICC_CODEX_INTEGRATION=1 to run the real-binary Codex migration tests",
    ),
    pytest.mark.skipif(
        os.environ.get("TWICC_CODEX_INTEGRATION") == "1" and not is_runtime_ready(),
        reason="the bundled Codex runtime is not downloaded yet",
    ),
    pytest.mark.django_db(transaction=True),
]

_DAY = "2026/09/04"


def _line(second: int, type_: str, payload: dict) -> bytes:
    return orjson.dumps({"timestamp": f"2026-09-04T10:00:{second:02d}.000Z", "type": type_, "payload": payload})


def _session_meta(thread_id: str) -> dict:
    return {
        "id": thread_id,
        "timestamp": "2026-09-04T10:00:00.000Z",
        "cwd": "/tmp",
        "originator": "codex_python_sdk",
        "cli_version": "0.150.1",
        "source": "vscode",
        "model_provider": "openai",
        "base_instructions": {"text": "You are Codex."},
    }


def _legacy_lines(thread_id: str) -> list[bytes]:
    """A legacy (0.150) rollout: one turn with a shell call, a patch and an answer."""

    return [
        _line(0, "session_meta", _session_meta(thread_id)),
        _line(1, "event_msg", {
            "type": "task_started", "turn_id": "turn-1", "started_at": 1788516001,
            "model_context_window": 258400, "collaboration_mode_kind": "default",
        }),
        _line(1, "event_msg", {
            "type": "user_message", "message": "hello legacy",
            "images": [], "local_images": [], "text_elements": [],
        }),
        _line(1, "response_item", {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello legacy"}],
        }),
        _line(2, "response_item", {
            "type": "function_call", "name": "exec_command", "arguments": '{"cmd":"echo hi"}', "call_id": "call_1",
        }),
        _line(3, "response_item", {"type": "function_call_output", "call_id": "call_1", "output": "hi\n"}),
        _line(4, "response_item", {
            "type": "custom_tool_call", "status": "completed", "call_id": "call_2", "name": "apply_patch",
            "input": "*** Begin Patch\n*** Add File: a.txt\n+x\n*** End Patch\n",
        }),
        _line(4, "event_msg", {
            "type": "patch_apply_end", "call_id": "call_2", "turn_id": "turn-1",
            "stdout": "Success. Updated the following files:\nA a.txt\n", "stderr": "", "success": True,
            "changes": {"/tmp/a.txt": {"type": "add", "content": "x\n"}}, "status": "completed",
        }),
        _line(4, "response_item", {"type": "custom_tool_call_output", "call_id": "call_2", "output": '{"output":"Success"}'}),
        _line(5, "event_msg", {"type": "agent_message", "message": "done", "phase": "final_answer", "memory_citation": None}),
        _line(5, "response_item", {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}],
            "phase": "final_answer",
        }),
        _line(6, "event_msg", {"type": "token_count", "info": None, "rate_limits": None}),
        _line(6, "event_msg", {
            "type": "task_complete", "turn_id": "turn-1", "last_agent_message": "done",
            "completed_at": 1788516006, "duration_ms": 5000, "time_to_first_token_ms": 1000,
        }),
    ]


def _write_rollout(codex_home: Path, thread_id: str, second: int, lines: list[bytes]) -> tuple[Path, str]:
    relative = f"{_DAY}/rollout-2026-09-04T10-00-{second:02d}-{thread_id}.jsonl"
    path = codex_home / "sessions" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n".join(lines) + b"\n")
    return path, relative


def _seed_session(project: Project, thread_id: str, relative: str, first_line: bytes) -> Session:
    session = Session.objects.create(id=thread_id, project=project, provider=Provider.CODEX, file_path=relative)
    SessionItem.objects.create(session=session, line_num=1, content=first_line.decode())
    return session


def _coordinator() -> CodexComputeCoordinator:
    return CodexComputeCoordinator(SimpleNamespace(compute_version=48, stop_event=asyncio.Event()), asyncio.Event())


def _prepare(coordinator: CodexComputeCoordinator, thread_id: str, path: Path):
    """Run one preparation with the real DB writer; release the lease afterwards."""

    async def scenario():
        start_db_writer()
        result = None
        try:
            result = await coordinator.prepare_candidate(
                CodexComputeCandidate(thread_id, path, SessionType.SESSION)
            )
            return result
        finally:
            if isinstance(result, PreparedCandidate) and result.migration_lease is not None:
                result.migration_lease.release()
                coordinator._release_lease(thread_id, replay=False)
            await stop_db_writer()

    return asyncio.run(scenario())


@pytest.fixture
def project(db):
    return Project.objects.create(id="codex-integration-project")


def test_real_codex_migrates_a_legacy_rollout(provider_home, project):
    thread_id = "01a06f00-0000-7000-8000-00000000a001"
    lines = _legacy_lines(thread_id)
    path, relative = _write_rollout(provider_home.codex, thread_id, 0, lines)
    _seed_session(project, thread_id, relative, lines[0])

    result = _prepare(_coordinator(), thread_id, path)

    assert isinstance(result, PreparedCandidate)
    assert result.migrated_history is True
    assert not is_migrating(thread_id)
    session = Session.objects.get(id=thread_id)
    assert asyncio.run(get_db_history_mode(thread_id)) == HistoryMode.PAGINATED
    assert session.last_offset == path.stat().st_size
    assert session.last_line == 13
    assert session.compute_version is None
    assert session.unavailable_reason is None
    kinds = []
    for item in session.items.order_by("line_num"):
        record = orjson.loads(item.content)
        payload = record["payload"]
        kinds.append((record["type"], payload.get("type"), (payload.get("item") or {}).get("type")))
    assert ("event_msg", "item_completed", "UserMessage") in kinds
    assert ("event_msg", "item_completed", "FileChange") in kinds
    assert ("event_msg", "item_completed", "AgentMessage") in kinds
    assert not any(payload_type in {"user_message", "agent_message", "patch_apply_end"} for _, payload_type, _ in kinds)
    # Codex's own state DB now lives in the throwaway home, never in ~/.codex.
    assert (provider_home.codex / "state_5.sqlite").exists()


def test_real_codex_registers_a_thread_its_state_db_never_indexed(provider_home, project, monkeypatch):
    # The first migration completes Codex's state-DB backfill; a rollout that
    # appears afterwards is unknown to Codex until TwiCC resumes it once.
    known = "01a06f00-0000-7000-8000-00000000b001"
    known_lines = _legacy_lines(known)
    known_path, known_relative = _write_rollout(provider_home.codex, known, 0, known_lines)
    _seed_session(project, known, known_relative, known_lines[0])
    assert isinstance(_prepare(_coordinator(), known, known_path), PreparedCandidate)

    unknown = "01a06f00-0000-7000-8000-00000000b002"
    unknown_lines = _legacy_lines(unknown)
    unknown_path, unknown_relative = _write_rollout(provider_home.codex, unknown, 1, unknown_lines)
    _seed_session(project, unknown, unknown_relative, unknown_lines[0])
    registrations = []
    real_register = background_compute.register_thread_with_codex

    async def spy(session_id, path):
        registrations.append(session_id)
        await real_register(session_id, path)

    monkeypatch.setattr(background_compute, "register_thread_with_codex", spy)

    result = _prepare(_coordinator(), unknown, unknown_path)

    assert isinstance(result, PreparedCandidate)
    assert registrations == [unknown]
    assert asyncio.run(get_db_history_mode(unknown)) == HistoryMode.PAGINATED
    assert Session.objects.get(id=unknown).unavailable_reason is None


def test_real_codex_defers_a_thread_another_writer_holds(provider_home, project):
    thread_id = "01a06f00-0000-7000-8000-00000000c001"
    lines = _legacy_lines(thread_id)
    path, relative = _write_rollout(provider_home.codex, thread_id, 0, lines)
    _seed_session(project, thread_id, relative, lines[0])

    from openai_codex import AsyncCodex

    from twicc.providers.codex.bin import make_codex_config

    async def scenario():
        # An "external" Codex process resumes the thread and keeps its writer
        # lock; Codex then reports the migration as skipped_busy.
        config = await make_codex_config(cwd="/tmp")
        async with AsyncCodex(config=config) as external:
            await external.thread_resume(thread_id, cwd="/tmp")
            start_db_writer()
            coordinator = _coordinator()
            try:
                busy = await coordinator.prepare_candidate(
                    CodexComputeCandidate(thread_id, path, SessionType.SESSION)
                )
            finally:
                await stop_db_writer()
        return busy

    busy = asyncio.run(scenario())

    assert busy == DeferredCandidate(thread_id, "skipped_busy")
    assert not is_migrating(thread_id)
    assert asyncio.run(get_db_history_mode(thread_id)) == HistoryMode.LEGACY
    assert SessionItem.objects.filter(session_id=thread_id).count() == 1

    # Writer gone: the retry migrates.
    result = _prepare(_coordinator(), thread_id, path)
    assert isinstance(result, PreparedCandidate)
    assert asyncio.run(get_db_history_mode(thread_id)) == HistoryMode.PAGINATED


def test_real_codex_refusal_flags_the_session_unavailable(provider_home, project):
    # A raw response_item Codex cannot parse (``ResponseItem::Other``) makes the
    # canonicalisation fail atomically: the legacy file is left untouched and
    # TwiCC records why the history cannot be shown.
    thread_id = "01a06f00-0000-7000-8000-00000000d001"
    lines = [
        _line(0, "session_meta", _session_meta(thread_id)),
        _line(1, "event_msg", {"type": "user_message", "message": "hello", "images": [], "local_images": [], "text_elements": []}),
        _line(2, "response_item", {"type": "totally_unknown_item", "foo": 1}),
    ]
    path, relative = _write_rollout(provider_home.codex, thread_id, 0, lines)
    _seed_session(project, thread_id, relative, lines[0])
    before = path.read_bytes()

    result = _prepare(_coordinator(), thread_id, path)

    assert isinstance(result, FailedCandidate)
    assert result.phase == "Codex migration"
    assert result.unavailable_reason == "codex_migration_failed:legacy_rollout_conversion_failed"
    assert path.read_bytes() == before
    assert Session.objects.get(id=thread_id).unavailable_reason == "codex_migration_failed:legacy_rollout_conversion_failed"
    assert SessionItem.objects.filter(session_id=thread_id).count() == 1
    assert not is_migrating(thread_id)


def test_watcher_detects_a_migration_run_outside_twicc(provider_home, project, monkeypatch):
    thread_id = "01a06f00-0000-7000-8000-00000000e001"
    lines = _legacy_lines(thread_id)
    path, relative = _write_rollout(provider_home.codex, thread_id, 0, lines)
    session = _seed_session(project, thread_id, relative, lines[0])
    Session.objects.filter(id=thread_id).update(compute_version=48, last_offset=path.stat().st_size, last_line=13)

    # Someone runs ``codex migrate-rollouts --apply`` by hand (or Codex's own
    # background migration does): the file is rewritten under TwiCC.
    outcome = asyncio.run(CodexMigrationRunner().run(thread_id, path))
    assert outcome.status == "migrated"

    from twicc.providers.codex import migration_gate

    monkeypatch.setattr(migration_gate, "_rebuild_requests", set())
    parsed = ParsedSessionFile(
        project.id, thread_id, SessionType.SESSION, relative, compute_ready_on_create=True,
    )

    async def scenario():
        start_db_writer()
        try:
            return await CodexSessionsWatcher()._rewrite_detected(parsed, path)
        finally:
            await stop_db_writer()

    assert asyncio.run(scenario()) is True
    session.refresh_from_db()
    assert session.compute_version is None
    assert migration_gate._rebuild_requests == {thread_id}

    # The coordinator then replaces the history from byte zero.
    result = _prepare(_coordinator(), thread_id, path)
    assert isinstance(result, PreparedCandidate)
    assert asyncio.run(get_db_history_mode(thread_id)) == HistoryMode.PAGINATED
    assert Session.objects.get(id=thread_id).last_line == 13
