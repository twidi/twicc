"""Regression test: background re-compute must not erase ToolResultLink.error.

Scenario
--------
1. The live path (watcher) processes a Codex JSONL line where the user denied
   a tool. It creates a ``ToolResultLink`` row with
   ``error="User denied this action"`` by consulting
   ``CodexAgent._user_terminated_tool_ids`` (in-memory map).
2. The backend restarts. ``_user_terminated_tool_ids`` is gone;
   ``_user_terminated_tool_reason`` returns ``None`` for every call_id.
3. Background re-compute reprocesses the same JSONL. ``analysis.tool_result_error``
   is ``None`` (no live agent). The old diff loop would detect a difference
   (original.error="User denied this action" vs new.error=None) and call
   ``bulk_update`` with ``error=None``, silently erasing the refusal reason.
4. After the fix the diff loop preserves non-None ``error`` / ``extra`` from
   the original row when the re-compute produces ``None``.

This test drives ``compute_session_metadata`` → ``apply_session_complete`` end
to end using real Codex JSONL that produces a ``function_call`` / ``function_call_output``
pair. The initial ``ToolResultLink`` is pre-seeded with ``error="User denied this action"``
to simulate the live path. After re-compute the row must still carry the original error.
"""

from __future__ import annotations

import json
import queue
from datetime import datetime, UTC

import orjson
import pytest
from django.db.models import F

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, ToolResultLink
from twicc.providers.codex.compute import get_compute


# ---------------------------------------------------------------------------
# Minimal Codex JSONL helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)


def _codex_line(type_: str, payload_type: str, payload_extra: dict | None = None, **top) -> str:
    """Return a single Codex JSONL line (JSON string)."""
    payload = {"type": payload_type, **(payload_extra or {})}
    line = {
        "timestamp": _NOW.isoformat(),
        "type": type_,
        "payload": payload,
        **top,
    }
    return json.dumps(line)


def _function_call_line(call_id: str, name: str = "exec_command") -> str:
    """``response_item.function_call`` — creates a TOOL_USE item."""
    return _codex_line(
        "response_item",
        "function_call",
        payload_extra={"call_id": call_id, "name": name, "arguments": "{}"},
    )


def _function_call_output_line(call_id: str, output: str = "ok") -> str:
    """``response_item.function_call_output`` — creates a tool_result item."""
    return _codex_line(
        "response_item",
        "function_call_output",
        payload_extra={"call_id": call_id, "output": output},
    )


# ---------------------------------------------------------------------------
# Helpers shared with test_group_logic.py
# ---------------------------------------------------------------------------


def _apply_compute_results(result_queue, provider_compute) -> None:
    """Drain the queue and apply every ``session_complete`` message."""
    from queue import Empty

    while True:
        try:
            raw_msg = result_queue.get_nowait()
        except Empty:
            break
        msg = orjson.loads(raw_msg)
        if msg.get("type") == "session_complete":
            provider_compute.apply_session_complete(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def codex_session(db):
    """Create a minimal Project + Session for Codex."""
    project = Project.objects.create(id="test-project-codex")
    session = Session.objects.create(
        id="test-session-codex",
        project=project,
        provider=Provider.CODEX,
    )
    return session


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestRecomputePreservesToolResultLinkError:
    """Background re-compute must not overwrite a live-path-recorded error."""

    def test_existing_error_not_erased_on_recompute(self, codex_session):
        """
        Pre-seed a ToolResultLink with error="User denied this action".
        Run batch compute without a live agent (error=None from
        _user_terminated_tool_reason).
        Verify the error is preserved after apply_session_complete.
        """
        session = codex_session
        call_id = "call-test-denied-001"

        # 1. Create the two JSONL items: tool_use (function_call) + tool_result
        #    (function_call_output with a benign "aborted by user" text that
        #    none of the static helpers pick up as an error — they only parse
        #    exit codes, not freeform refusal text).
        tool_use_content = _function_call_line(call_id, name="exec_command")
        tool_result_content = _function_call_output_line(
            call_id,
            output="aborted by user after 0.0s",  # no exit code → helpers return None
        )

        tool_use_item = SessionItem.objects.create(
            session=session,
            line_num=1,
            content=tool_use_content,
        )
        tool_result_item = SessionItem.objects.create(
            session=session,
            line_num=2,
            content=tool_result_content,
        )

        # 2. Pre-seed the ToolResultLink as the live path would have created it,
        #    with error="User denied this action" (recorded by
        #    _user_terminated_tool_ids).
        ToolResultLink.objects.create(
            session=session,
            tool_use_line_num=tool_use_item.line_num,
            tool_result_line_num=tool_result_item.line_num,
            tool_use_id=call_id,
            tool_name="exec_command",
            tool_result_at=_NOW,
            error="User denied this action",
            extra=json.dumps({"is_terminated": True}),
        )

        # 3. Run background re-compute (no live agent → _user_terminated_tool_reason
        #    returns None → error=None in computed result).
        compute = get_compute()
        result_q = queue.Queue()
        compute.compute_session_metadata(session.id, result_q, run_id=0)
        _apply_compute_results(result_q, compute)

        # 4. The ToolResultLink row must still carry the original values.
        link = ToolResultLink.objects.get(
            session=session,
            tool_use_id=call_id,
            tool_result_line_num=tool_result_item.line_num,
        )
        assert link.error == "User denied this action", (
            "Background re-compute erased the live-path refusal reason. "
            f"Got: {link.error!r}"
        )
        assert link.extra == json.dumps({"is_terminated": True}), (
            "Background re-compute erased the live-path extra payload. "
            f"Got: {link.extra!r}"
        )

    def test_recompute_creates_new_row_when_none_exists(self, codex_session):
        """
        If no ToolResultLink existed before (e.g. initial sync skipped it),
        the batch re-compute should create the row normally.
        """
        session = codex_session
        call_id = "call-test-new-001"

        SessionItem.objects.create(
            session=session,
            line_num=3,
            content=_function_call_line(call_id, name="exec_command"),
        )
        SessionItem.objects.create(
            session=session,
            line_num=4,
            content=_function_call_output_line(call_id, output="ok"),
        )

        # No pre-existing ToolResultLink.
        assert not ToolResultLink.objects.filter(session=session, tool_use_id=call_id).exists()

        compute = get_compute()
        result_q = queue.Queue()
        compute.compute_session_metadata(session.id, result_q, run_id=0)
        _apply_compute_results(result_q, compute)

        link = ToolResultLink.objects.filter(
            session=session,
            tool_use_id=call_id,
        ).first()
        assert link is not None, "Batch compute should have created a ToolResultLink for the new pair"
        # No error expected for a clean 'ok' output with no exit code trailer.
        assert link.error is None


@pytest.mark.parametrize("stale", [False, True])
def test_async_questions_apply_only_after_compute_guard(codex_session, stale):
    from twicc.core.models import AsyncQuestionState
    from twicc.core.services.async_questions import read_question_snapshot
    from tests.test_codex_async_question_lifecycle import question, record, user

    records = [question(), record("task_complete", second=2), user("Yes")]
    for line, value in enumerate(records, 1):
        SessionItem.objects.create(session=codex_session, line_num=line, content=orjson.dumps(value).decode())
    compute, result_q = get_compute(), queue.Queue()
    compute.compute_session_metadata(codex_session.id, result_q, run_id=0)
    assert not AsyncQuestionState.objects.filter(session=codex_session).exists()
    if stale:
        Session.objects.filter(pk=codex_session.pk).update(last_offset=1000)
    _apply_compute_results(result_q, compute)
    snapshot = read_question_snapshot(codex_session.id)
    if stale:
        assert snapshot["revision"] == 0
    else:
        assert snapshot["resolutions"] == {"q1": {"status": "sent", "request_id": None}}


def test_async_questions_recompute_remaps_lines_preserves_dismissal(codex_session):
    from twicc.core.models import AsyncQuestionState
    from twicc.core.services.async_questions import dismiss_question_batch, read_question_snapshot
    from tests.test_codex_async_question_lifecycle import question, record

    values = [record("thread_goal_updated", second=0, goal={"id": "g1", "status": "active"}),
              question(), record("thread_goal_updated", second=2, goal={"id": "g1", "status": "complete"}),
              record("task_complete", second=3)]
    for line, value in enumerate(values, 1):
        SessionItem.objects.create(session=codex_session, line_num=line, content=orjson.dumps(value).decode())
    compute = get_compute()
    def rebuild():
        result_q = queue.Queue()
        compute.compute_session_metadata(codex_session.id, result_q, run_id=0)
        _apply_compute_results(result_q, compute)
    rebuild()
    dismiss_question_batch(codex_session.id, "q1", request_id="dismiss-1")
    before = AsyncQuestionState.objects.get(session=codex_session).state
    SessionItem.objects.filter(session=codex_session).update(line_num=100 + F("line_num"))
    rebuild()
    after = AsyncQuestionState.objects.get(session=codex_session).state
    assert set(after["facts"]) == set(before["facts"])
    assert after["facts"]["question:q1"]["line"] == 102
    assert read_question_snapshot(codex_session.id)["resolutions"]["q1"] == {
        "status": "dismissed", "request_id": "dismiss-1",
    }


@pytest.mark.django_db(transaction=True)
def test_full_recompute_preserves_private_agent_origin_without_sender_header(codex_session):
    """Canonical text has no sender header; the durable journal supplies origin."""
    import asyncio
    from pathlib import Path

    from asgiref.sync import sync_to_async

    from twicc.core.models import AsyncQuestionState
    from twicc.core.services.async_questions import accept_question_send, merge_question_facts, prepare_question_send
    from twicc.providers import db_writer
    from twicc.providers.codex.async_questions import QuestionFact

    session = codex_session
    fixture = Path(__file__).with_name("fixtures") / "codex_agent_question_reply.jsonl"
    records = [orjson.loads(line) for line in fixture.read_bytes().splitlines()]
    for line, record in enumerate(records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(record).decode())
    merge_question_facts(session.id, [
        QuestionFact("question:recorded-question", "question", "2026-10-05T09:12:01Z", "question-turn",
                     "recorded-question", None,
                     {"source": "sdk", "questions": [{"index": 0, "title": "Keep the menu?", "options": ["Yes", "No"]}]}),
        QuestionFact("end:question-turn", "turn_end", "2026-10-05T09:12:02Z", "question-turn", None, None, {}),
    ])
    prepared = prepare_question_send(session.id, "Continue verification.", None, request_id="private-agent-send",
                                     origin="agent", at="2026-10-05T09:12:03Z")
    accept_question_send(session.id, {**prepared.submission, "source_item_id": "recorded-agent-user",
                                      "target_turn_id": "agent-reply-turn", "delivery_route": "start"})
    before = AsyncQuestionState.objects.get(session=session).state
    assert before["batches"]["recorded-question"]["status"] == "ready"
    assert records[4]["payload"]["item"]["content"][0]["text"] == "Continue verification."

    async def rebuild():
        db_writer.start_db_writer()
        try:
            compute, result_q = get_compute(), queue.Queue()
            await sync_to_async(compute.compute_session_metadata)(session.id, result_q, run_id=0)
            unchanged = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=session).state)()
            assert unchanged == before  # CPU extraction cannot mutate the durable journal.
            messages = []
            while not result_q.empty():
                messages.append(orjson.loads(result_q.get_nowait()))
            complete = next(msg for msg in messages if msg["type"] == "session_complete")
            historical_user = next(fact for fact in complete["async_question_facts"]
                                   if fact["key"] == "user:recorded-agent-user")
            assert historical_user["data"]["origin"] == "human"  # Source text alone cannot identify the agent.
            assert complete["observed_last_offset"] == session.last_offset
            result = await db_writer.run_under_db_write_lock(lambda: sync_to_async(compute.apply_session_complete)(complete))
            assert result.outcome == "applied"
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(rebuild())
    after = AsyncQuestionState.objects.get(session=session).state
    submission = after["facts"]["send:private-agent-send"]["data"]
    assert submission["origin"] == "agent"
    assert submission["origin_source"] == "live"
    assert submission["boundary"] == prepared.submission["boundary"]
    assert submission["source_item_id"] == "recorded-agent-user"
    assert submission["status"] == "accepted"
    assert after["batches"]["recorded-question"]["status"] == "ready"
    assert after["facts"]["question:recorded-question"]["line"] == 2


def test_stale_recompute_does_not_erase_live_inline_correction(codex_session, tmp_path):
    from tests.test_inline_artifact_compute import TAG, assistant, capture, ingest

    compute = get_compute()
    path = tmp_path / 'catalog.jsonl'
    ingest(compute, codex_session, path, [assistant(Provider.CODEX, TAG)])
    rebuilt = capture(compute, codex_session)
    ingest(compute, codex_session, path, [assistant(Provider.CODEX, TAG)], append=True)
    result = compute.apply_session_complete(rebuilt)
    codex_session.refresh_from_db()
    assert result.outcome == 'superseded'
    assert codex_session.inline_artifacts['publications'][-1]['line_num'] == 2
