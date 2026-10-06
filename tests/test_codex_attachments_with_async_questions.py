"""Composer attachments and Codex async-question answers in the same send.

Both features meet in three places, covered here:

- the WebSocket handler: inline shape validation of BOTH new fields before the detached task,
  both forwarded to the manager, one ack, refs released only after a delivered ack attempt;
- the Codex manager: the question answers are folded into the user text, which stays the LAST
  part of the turn input, after the images and the manifest (never a second copy on a retry);
- the Codex turn input: answers never move ahead of the manifest.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc.agent import AgentState, SendDeliveryError
from twicc.core.enums import Provider
from twicc.core.models import Project, Session
from twicc.core.services import async_questions as service
from twicc.core.services.attachments.manifest import build_manifest
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.async_questions import QuestionFact
from twicc.providers.helpers import AgentSettings

from tests.test_codex_attachment_delivery import (  # noqa: F401 - fixtures and builders
    _content,
    _file_only_content,
    _items,
    _make_agent,
    _plan,
)
from tests.test_composer_attachment_send import (  # noqa: F401 - fixtures and builders
    REFS,
    REQUEST_ID,
    SESSION_ID,
    WIRE_REFS,
    _acks,
    _clean_state,
    _errors,
    released,
    ws,
)

ANSWERS = {
    "revision": 1,
    "batch_ids": ["q1"],
    "answers": [{"item_id": "q1", "index": 0, "kind": "option", "value": "Yes"}],
}
ANSWER_TEXT = "::: Answers to your questions\n\n**Question:** Open the session?\n\n**Answer:** Yes\n\n:::"


# ----------------------------------------------------------------------
# WebSocket handler
# ----------------------------------------------------------------------


@pytest.fixture
def codex_ws(ws, monkeypatch):  # noqa: F811
    monkeypatch.setattr("twicc.asgi.get_session_provider", AsyncMock(return_value="codex"))
    return ws


@pytest.mark.django_db(transaction=True)
def test_refs_and_answers_reach_the_manager_together_with_one_ack_then_one_release(codex_ws):
    asyncio.run(codex_ws.send(codex_ws.frame(async_questions=ANSWERS)))

    call = codex_ws.manager.calls[0]
    assert call.kwargs["attachment_plan"] is not None
    assert call.kwargs["async_questions"] == ANSWERS
    assert call.kwargs["request_id"] == REQUEST_ID
    assert call.kwargs["send_origin"] == "human"
    assert codex_ws.frames == [{"type": "send_ack", "request_id": REQUEST_ID, "session_id": SESSION_ID}]
    assert codex_ws.released == [REFS]


@pytest.mark.django_db(transaction=True)
def test_refs_and_answers_without_any_text_are_still_a_message(codex_ws):
    asyncio.run(codex_ws.send(codex_ws.frame(text="", async_questions=ANSWERS)))
    assert codex_ws.events.count("send") == 1
    assert len(_acks(codex_ws.frames)) == 1


@pytest.mark.django_db(transaction=True)
def test_answers_alone_are_a_message_and_plan_nothing(codex_ws):
    content = codex_ws.frame(text="", async_questions=ANSWERS)
    del content["attachments"]
    asyncio.run(codex_ws.send(content))

    call = codex_ws.manager.calls[0]
    assert "attachment_plan" not in call.kwargs
    assert call.kwargs["async_questions"] == ANSWERS
    assert codex_ws.plan_calls == []
    assert codex_ws.released == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("bad", ["answers", ["a"], 3, True])
def test_a_non_object_async_questions_is_answered_inline_and_nothing_runs(codex_ws, bad):
    asyncio.run(codex_ws.send(codex_ws.frame(async_questions=bad)))

    errors = _errors(codex_ws.frames)
    assert [(e["code"], e["request_id"], e["session_id"]) for e in errors] == [
        ("async_questions_invalid", REQUEST_ID, SESSION_ID),
    ]
    assert codex_ws.manager.calls == []
    assert codex_ws.plan_calls == []
    assert codex_ws.released == []
    assert _acks(codex_ws.frames) == []


@pytest.mark.django_db(transaction=True)
def test_invalid_refs_next_to_valid_answers_fail_the_attachment_family_only(codex_ws):
    asyncio.run(codex_ws.send(codex_ws.frame(attachments=[{"bucket": "b", "id": "not-a-uuid"}], async_questions=ANSWERS)))

    errors = _errors(codex_ws.frames)
    assert [e["code"] for e in errors] == ["invalid_attachments"]
    assert errors[0]["request_id"] == REQUEST_ID
    assert codex_ws.manager.calls == []


@pytest.mark.django_db(transaction=True)
def test_answers_on_a_claude_session_fail_before_planning_and_keep_the_refs(codex_ws, monkeypatch):
    monkeypatch.setattr("twicc.asgi.get_session_provider", AsyncMock(return_value="claude_code"))
    asyncio.run(codex_ws.send(codex_ws.frame(async_questions=ANSWERS)))

    assert [e["code"] for e in _errors(codex_ws.frames)] == ["async_questions_invalid"]
    assert codex_ws.plan_calls == []
    assert codex_ws.released == []
    assert codex_ws.manager.calls == []


@pytest.mark.django_db(transaction=True)
def test_a_stale_question_rejection_keeps_the_refs_and_sends_no_ack(codex_ws):
    codex_ws.manager.error = SendDeliveryError("These questions changed.", code="async_questions_stale")
    asyncio.run(codex_ws.send(codex_ws.frame(async_questions=ANSWERS)))

    errors = _errors(codex_ws.frames)
    assert [(e["code"], e["request_id"]) for e in errors] == [("async_questions_stale", REQUEST_ID)]
    assert _acks(codex_ws.frames) == []
    assert codex_ws.released == []


# ----------------------------------------------------------------------
# Codex manager: answers folded into the user text of the attachment content
# ----------------------------------------------------------------------

def _question(item_id="q1", turn="t1", at="2026-10-05T10:00:01Z"):
    return QuestionFact(
        f"question:{item_id}", "question", at, turn, item_id, None,
        {"questions": [{"index": 0, "title": "Open the session?", "options": ["Yes", "Non"]}]},
    )


@pytest.fixture
def harness(monkeypatch):
    project = Project.objects.create(id="mix-project", directory="/tmp")
    session = Session.objects.create(id="mix-session", project=project, provider=Provider.CODEX, file_path="mix.jsonl")
    service.merge_question_facts(session.id, [
        _question(), QuestionFact("end:t1", "turn_end", "2026-10-05T10:00:02Z", "t1", None, None, {}),
    ])
    agent = SimpleNamespace(
        state=AgentState.USER_TURN, agent_settings=AgentSettings(),
        apply_agent_settings=AsyncMock(), send=AsyncMock(return_value=True),
    )
    manager = CodexAgentManager()
    manager._agents[session.id] = agent
    monkeypatch.setattr(manager, "_check_ephemeral_readonly", lambda *_: None)
    commits: list[str] = []

    async def commit(plan, *, session_id, text):
        commits.append(text)
        return _file_only_content(text)

    monkeypatch.setattr(manager, "_commit_attachment_plan", commit)

    async def send(text="", payload=None, request_id="send-1", plan=None):
        from twicc.providers import db_writer

        db_writer.start_db_writer()
        try:
            return await manager.send_to_session(
                session.id, project.id, "/tmp", text, AgentSettings(),
                attachment_plan=_plan() if plan is None else plan,
                async_questions=payload, request_id=request_id, send_origin="human",
            )
        finally:
            await db_writer.stop_db_writer()

    return SimpleNamespace(session=session, agent=agent, manager=manager, send=send, commits=commits)


@pytest.mark.django_db(transaction=True)
def test_combined_send_carries_the_answers_in_the_user_text_of_the_content(harness):
    assert asyncio.run(harness.send("Keep this.", ANSWERS)) is True

    # The attachments are committed once with the raw text, before the answers are folded.
    assert harness.commits == ["Keep this."]
    harness.agent.send.assert_awaited_once()
    text = harness.agent.send.await_args.args[0]
    assert text == f"{ANSWER_TEXT}\n\nKeep this."
    kwargs = harness.agent.send.await_args.kwargs
    assert kwargs["content"].user_text == text
    assert kwargs["content"].manifest is not None
    assert kwargs["submission"]["request_id"] == "send-1"
    assert service.read_question_snapshot(harness.session.id)["resolutions"]["q1"]["status"] == "sent"


@pytest.mark.django_db(transaction=True)
def test_files_plus_answers_without_text_send_the_answers_as_the_user_text(harness):
    assert asyncio.run(harness.send("", ANSWERS)) is True
    assert harness.agent.send.await_args.kwargs["content"].user_text == ANSWER_TEXT


@pytest.mark.django_db(transaction=True)
def test_a_file_only_message_without_answers_still_sends_and_retires_the_ready_questions(harness):
    assert asyncio.run(harness.send("", None)) is True
    harness.agent.send.assert_awaited_once()
    assert harness.agent.send.await_args.kwargs["content"].user_text == ""
    assert service.read_question_snapshot(harness.session.id)["resolutions"]["q1"]["status"] == "sent"


@pytest.mark.django_db(transaction=True)
def test_a_retry_of_the_same_request_never_folds_the_answers_twice(harness):
    # An explicit rejection (a pre-delivery guard) permits another send under the same identity.
    harness.agent.send = AsyncMock(side_effect=[SendDeliveryError("guard", code="agent_starting"), True])
    with pytest.raises(SendDeliveryError):
        asyncio.run(harness.send("Keep this.", ANSWERS))
    assert asyncio.run(harness.send("Keep this.", ANSWERS)) is True

    second = harness.agent.send.await_args_list[1]
    assert second.args[0] == f"{ANSWER_TEXT}\n\nKeep this."
    assert second.kwargs["content"].user_text == second.args[0]
    assert second.args[0].count("Answers to your questions") == 1


@pytest.mark.django_db(transaction=True)
def test_a_stale_rejection_after_the_commit_then_a_retry_under_a_new_request_id_sends_once(harness, released, monkeypatch):
    stale = {**ANSWERS, "batch_ids": ["gone"], "answers": [{**ANSWERS["answers"][0], "item_id": "gone"}]}
    # The attachments are committed with the raw text at entry; the question guard then refuses the send.
    with pytest.raises(SendDeliveryError) as refused:
        asyncio.run(harness.send("Keep this.", stale, request_id="send-1"))
    assert refused.value.code == "async_questions_stale"
    assert harness.commits == ["Keep this."]
    harness.agent.send.assert_not_awaited()
    assert released == []

    # The same refs are usable again under a NEW request id: committed again (same-session tombstones),
    # answers folded exactly once.
    assert asyncio.run(harness.send("Keep this.", ANSWERS, request_id="send-2")) is True
    assert harness.commits == ["Keep this.", "Keep this."]
    harness.agent.send.assert_awaited_once()
    text = harness.agent.send.await_args.args[0]
    content = harness.agent.send.await_args.kwargs["content"]
    assert text == f"{ANSWER_TEXT}\n\nKeep this."
    assert content.user_text == text
    assert text.count("Answers to your questions") == 1
    assert harness.agent.send.await_args.kwargs["submission"]["request_id"] == "send-2"
    assert service.read_question_snapshot(harness.session.id)["resolutions"]["q1"]["status"] == "sent"
    assert released == []

    # The pending context is folded exactly once into the final turn input.
    events: list = []
    items = _items(_make_agent(monkeypatch, events, pending="<ctx>"), text, content)
    assert items[-1].text == f"<ctx>{text}"
    assert [event for event in events if event[0] == "pending"] == [("pending", content.user_text)]


@pytest.mark.django_db(transaction=True)
def test_answers_with_a_command_and_attachments_are_refused_before_any_delivery(harness):
    harness.manager._dispatch_hardcoded_command = AsyncMock()
    with pytest.raises(SendDeliveryError):
        asyncio.run(harness.send("/compact", ANSWERS))
    harness.agent.send.assert_not_awaited()


# ----------------------------------------------------------------------
# Codex turn input
# ----------------------------------------------------------------------


def test_the_turn_input_keeps_answers_after_the_manifest_and_folds_the_pending_context_once(monkeypatch):
    events: list = []
    content = _content(f"{ANSWER_TEXT}\n\nKeep this.")
    items = _items(_make_agent(monkeypatch, events, pending="<ctx>"), f"{ANSWER_TEXT}\n\nKeep this.", content)

    assert [type(item).__name__ for item in items] == ["ImageInput", "ImageInput", "TextInput", "TextInput"]
    assert items[2].text == build_manifest(content.manifest)
    assert items[3].text == f"<ctx>{ANSWER_TEXT}\n\nKeep this."
    assert [event for event in events if event[0] == "pending"] == [("pending", content.user_text)]
