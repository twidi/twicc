from __future__ import annotations

import asyncio

import pytest

from twicc.core.enums import Provider
from twicc.core.models import HistoryFactKind, Project, Session, SessionHistoryFact
from twicc.providers import db_writer
from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult
from twicc.providers.helpers import get_provider_helpers


def _run_compute_message(monkeypatch, outcome: str):
    async def scenario():
        db_writer.start_compute_executor()
        applied_queue = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(
            Provider.CODEX,
            display_session_ids=set(),
            total_display=0,
            applied_queue=applied_queue,
        )
        monkeypatch.setattr(
            BaseSessionCompute,
            "apply_session_complete",
            staticmethod(lambda _msg: ComputeApplyResult(outcome, title_updated_session_ids=("other-target",))),
        )

        async def reject_broadcast(_session_id):
            raise AssertionError("non-applied outcomes must not broadcast")

        monkeypatch.setattr(db_writer, "broadcast_session_updated", reject_broadcast)
        try:
            await db_writer._process_compute_message({
                "type": "session_complete",
                "provider": Provider.CODEX.value,
                "run_id": run_id,
                "session_id": "session-1",
            })
            return applied_queue.get_nowait()
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_compute_executor()

    return asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["superseded", "missing"])
def test_db_writer_emits_non_applied_compute_outcomes(monkeypatch, outcome):
    signal = _run_compute_message(monkeypatch, outcome)

    assert signal == db_writer.ComputeApplied("session-1", outcome)


def test_db_writer_emits_apply_exception(monkeypatch):
    async def scenario():
        db_writer.start_compute_executor()
        applied_queue = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(
            Provider.CODEX,
            display_session_ids=set(),
            total_display=0,
            applied_queue=applied_queue,
        )

        def fail(_msg):
            raise RuntimeError("apply failed")

        monkeypatch.setattr(BaseSessionCompute, "apply_session_complete", staticmethod(fail))
        try:
            await db_writer._process_compute_message({
                "type": "session_complete",
                "provider": Provider.CODEX.value,
                "run_id": run_id,
                "session_id": "session-2",
            })
            return applied_queue.get_nowait()
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_compute_executor()

    signal = asyncio.run(scenario())

    assert signal == db_writer.ComputeApplied("session-2", "failed", "apply failed")


def test_db_writer_emits_worker_error(monkeypatch):
    async def scenario():
        applied_queue = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(
            Provider.CODEX,
            display_session_ids=set(),
            total_display=0,
            applied_queue=applied_queue,
        )
        try:
            await db_writer._process_compute_message({
                "type": "error",
                "provider": Provider.CODEX.value,
                "run_id": run_id,
                "session_id": "session-3",
                "error": "worker failed",
            })
            return applied_queue.get_nowait()
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)

    signal = asyncio.run(scenario())

    assert signal == db_writer.ComputeApplied("session-3", "failed", "worker failed")


@pytest.mark.django_db(transaction=True)
def test_invalid_final_facts_emit_failure_without_publishing_version():
    project = Project.objects.create(id="compute-signal-facts-project")
    session = Session.objects.create(
        id="compute-signal-facts-session", project=project, provider=Provider.CODEX,
        file_path="history.jsonl", last_offset=10,
    )

    async def scenario():
        db_writer.start_compute_executor()
        applied_queue = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(
            Provider.CODEX, display_session_ids=set(), total_display=0, applied_queue=applied_queue,
        )
        try:
            await db_writer._process_compute_message({
                "type": "session_complete", "provider": Provider.CODEX.value,
                "run_id": run_id, "session_id": session.id, "observed_last_offset": 10,
                "history_facts": [{
                    "line_num": 1, "kind": HistoryFactKind.TOOL_CALL, "key": "", "data": {},
                }],
                "session_fields": {
                    "compute_version": get_provider_helpers(Provider.CODEX).current_compute_version,
                },
            })
            return applied_queue.get_nowait()
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_compute_executor()

    signal = asyncio.run(scenario())

    session.refresh_from_db()
    assert signal.session_id == session.id
    assert signal.outcome == "failed"
    assert "key must be a nonempty string" in signal.error
    assert session.compute_version is None
    assert not SessionHistoryFact.objects.filter(session=session).exists()


@pytest.mark.django_db(transaction=True)
def test_session_updated_broadcast_exposes_latest_inline_descriptor():
    from channels.layers import get_channel_layer
    from tests.test_inline_artifact_compute import RECORD

    session = Session.objects.create(
        id='inline-broadcast', file_path='inline-broadcast.jsonl',
        project=Project.objects.create(id='inline-broadcast-project'), provider=Provider.CODEX,
        user_message_count=1,
        inline_artifacts={'schema': 1, 'publications': [RECORD, {**RECORD, 'line_num': 87}]},
    )

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add('updates', channel)
        try:
            await db_writer.broadcast_session_updated(session.id)
            return await asyncio.wait_for(layer.receive(channel), timeout=2)
        finally:
            await layer.group_discard('updates', channel)

    event = asyncio.run(scenario())
    assert event['data']['type'] == 'session_updated'
    assert event['data']['session']['inline_artifacts'] == {'preferences': {**RECORD, 'line_num': 87}}


@pytest.mark.parametrize('outcome', ['applied', 'superseded', 'missing'])
def test_inline_coordinator_notified_only_after_accepted_apply(monkeypatch, outcome):
    from twicc.inline_artifacts import share_exports
    calls = []
    class Coordinator:
        def publication_changed(self, session_id):
            calls.append(session_id)
    monkeypatch.setattr(share_exports, 'get_inline_export_coordinator', lambda: Coordinator())
    monkeypatch.setattr(BaseSessionCompute, 'apply_session_complete',
                        staticmethod(lambda msg: ComputeApplyResult(outcome)))
    async def broadcast(session_id):
        assert calls == [session_id]
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', broadcast)
    async def scenario():
        db_writer.start_compute_executor()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0)
        try:
            await db_writer._process_compute_message({
                'type': 'session_complete', 'provider': Provider.CODEX.value,
                'run_id': run_id, 'session_id': 'root-inline',
            })
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_compute_executor()
    asyncio.run(scenario())
    assert calls == (['root-inline'] if outcome == 'applied' else [])
