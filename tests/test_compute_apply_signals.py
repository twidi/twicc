from __future__ import annotations

import asyncio

import pytest

from twicc.core.enums import Provider
from twicc.providers import db_writer
from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult


def _run_compute_message(monkeypatch, outcome: str):
    async def scenario():
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
            staticmethod(lambda _msg: ComputeApplyResult(outcome)),
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

    return asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["superseded", "missing"])
def test_db_writer_emits_non_applied_compute_outcomes(monkeypatch, outcome):
    signal = _run_compute_message(monkeypatch, outcome)

    assert signal == db_writer.ComputeApplied("session-1", outcome)


def test_db_writer_emits_apply_exception(monkeypatch):
    async def scenario():
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
