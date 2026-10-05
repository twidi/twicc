"""Durable question lifecycle and immutable delivery boundaries."""

from copy import deepcopy

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.services import async_questions as service
from twicc.providers.codex.async_questions import QuestionFact

pytestmark = pytest.mark.django_db


def at(second):
    return f"2026-10-05T10:00:{second:02d}Z"


def question(item_id="q1", turn_id="t1", second=1, line=None):
    return QuestionFact(
        f"question:{item_id}",
        "question",
        at(second),
        turn_id,
        item_id,
        line,
        {
            "source": "jsonl" if line else "sdk",
            "questions": [
                {"index": 0, "title": "Keep the menu?", "options": ["Yes", "No"]},
            ],
        },
    )


def end(turn_id="t1", second=2, line=None):
    return QuestionFact(f"end:{turn_id}", "turn_end", at(second), turn_id, None, line, {})


def control_return(group_id="g1", turns=None, second=3):
    return QuestionFact(
        f"return:{group_id}",
        "control_return",
        at(second),
        None,
        None,
        None,
        {"group_id": group_id, "turn_ids": turns or ["t1"]},
    )


def response(ids=None, answers=None, revision=1):
    return {"revision": revision, "batch_ids": ids or ["q1"], "answers": answers or []}


@pytest.fixture
def session():
    project = Project.objects.create(id="questions-project", directory="/test/questions")
    return Session.objects.create(
        id="questions-session",
        project=project,
        provider=Provider.CODEX,
        file_path="questions.jsonl",
    )


def prepare(session, *, payload=None, origin="human", request_id="send-1", second=4, text="Hello"):
    return service.prepare_question_send(
        session.id,
        text,
        payload,
        request_id=request_id,
        origin=origin,
        at=at(second),
    )


def state(session):
    from twicc.core.models import AsyncQuestionState

    return AsyncQuestionState.objects.get(session=session).state


def test_empty_read_does_not_create_state(session):
    from twicc.core.models import AsyncQuestionState

    assert service.read_question_snapshot(session.id) == {
        "revision": 0,
        "batches": [],
        "resolutions": {},
        "widget_enabled": True,
    }
    assert not AsyncQuestionState.objects.exists()


@pytest.mark.parametrize("setting,enabled", [(None, True), (True, True), (False, False)])
def test_widget_setting_is_live(session, setting, enabled):
    session.question_widget = setting
    session.save(update_fields=["question_widget"])
    assert service.read_question_snapshot(session.id)["widget_enabled"] is enabled


def test_sdk_jsonl_deduplicate_and_enrich_source(session):
    first = service.merge_question_facts(session.id, [question(), end()])
    second = service.merge_question_facts(session.id, [question(line=10), end(line=11)])
    assert len(second["batches"]) == 1
    assert second["batches"][0]["line"] == 10
    assert second["revision"] > first["revision"]
    assert service.merge_question_facts(session.id, [question(line=10), end(line=11)]) == second


def test_empty_merge_does_not_bump_revision(session):
    assert service.merge_question_facts(session.id, [])["revision"] == 0
    first = service.merge_question_facts(session.id, [question()])
    assert service.merge_question_facts(session.id, []) == first


def test_recompute_preserves_dismissal(session):
    service.merge_question_facts(session.id, [question(), control_return()])
    dismissed = service.dismiss_question_batch(session.id, "q1", request_id="dismiss-1")
    assert dismissed["resolutions"] == {"q1": {"status": "dismissed", "request_id": "dismiss-1"}}
    assert dismissed["batches"] == []
    enriched = service.merge_question_facts(session.id, [question(line=10), control_return()])
    assert enriched["resolutions"] == dismissed["resolutions"]
    assert service.dismiss_question_batch(session.id, "q1", request_id="dismiss-1") == enriched


def test_dismiss_retry_is_idempotent(session):
    service.merge_question_facts(session.id, [question(), end()])
    first = service.dismiss_question_batch(session.id, "q1", request_id="dismiss-1")
    assert service.dismiss_question_batch(session.id, "q1", request_id="dismiss-1") == first


@pytest.mark.parametrize("item_id", ["missing", "q1"])
def test_dismiss_requires_ready_batch(session, item_id):
    service.merge_question_facts(session.id, [question()])
    with pytest.raises(ValueError, match="async_questions_stale"):
        service.dismiss_question_batch(session.id, item_id, request_id="dismiss-1")


def test_prepare_formats_answers_without_retiring(session):
    service.merge_question_facts(session.id, [question(), end()])
    prepared = prepare(
        session,
        payload=response(
            answers=[
                {"item_id": "q1", "index": 0, "kind": "option", "value": "Yes"},
            ]
        ),
    )
    assert (
        prepared.text
        == "Answers to your questions:\n\nQuestion: Keep the menu?\nAnswer: Yes\n\nAdditional message:\nHello"
    )
    assert prepared.submission["status"] == "prepared"
    assert prepared.submission["client_message_id"] == "send-1"
    assert service.read_question_snapshot(session.id)["batches"][0]["status"] == "ready"
    assert state(session)["facts"]["send:send-1"]["data"]["boundary"] == prepared.submission["boundary"]


@pytest.mark.parametrize("status,remaining", [("accepted", False), ("rejected", True), ("uncertain", True)])
def test_delivery_status_controls_retirement(session, status, remaining):
    service.merge_question_facts(session.id, [question(), end()])
    prepared = prepare(session)
    snapshot = service.accept_question_send(session.id, {**prepared.submission, "status": status})
    assert bool(snapshot["batches"]) is remaining
    assert state(session)["facts"]["send:send-1"]["data"]["status"] == status
    assert service.merge_question_facts(session.id, [question(), end()]) == snapshot


def test_accept_default_retires_unanswered_ready_batches(session):
    service.merge_question_facts(session.id, [question(), end(), question("q2", "t2", 2), end("t2", 3)])
    prepared = prepare(session, payload=response(["q1", "q2"]))
    snapshot = service.accept_question_send(session.id, prepared.submission)
    assert snapshot["batches"] == []
    assert snapshot["resolutions"] == {
        "q1": {"status": "sent", "request_id": "send-1"},
        "q2": {"status": "sent", "request_id": "send-1"},
    }


@pytest.mark.parametrize("origin", ["agent", "internal"])
def test_nonhuman_send_does_not_retire(session, origin):
    service.merge_question_facts(session.id, [question(), end()])
    snapshot = service.accept_question_send(session.id, prepare(session, origin=origin).submission)
    assert snapshot["batches"][0]["status"] == "ready"


def test_accepted_boundary_retires_late_older_question_without_initial_batch(session):
    service.merge_question_facts(session.id, [end()])
    prepared = prepare(session)
    assert prepared.submission["boundary"]["batch_ids"] == []
    service.accept_question_send(session.id, prepared.submission)
    snapshot = service.merge_question_facts(session.id, [question()])
    assert snapshot["resolutions"]["q1"] == {"status": "sent", "request_id": "send-1"}
    assert snapshot["batches"] == []


def test_question_after_boundary_remains_ready(session):
    service.merge_question_facts(session.id, [end()])
    service.accept_question_send(session.id, prepare(session).submission)
    snapshot = service.merge_question_facts(session.id, [question(second=5)])
    assert snapshot["batches"][0]["status"] == "ready"


@pytest.mark.parametrize("same_turn", [False, True])
def test_new_ready_batch_absent_from_response_remains(session, same_turn):
    first = service.merge_question_facts(session.id, [question(), end()])
    turn = "t1" if same_turn else "t2"
    service.merge_question_facts(session.id, [question("q2", turn, 3), end(turn, 3)])
    prepared = prepare(session, payload=response(revision=first["revision"]))
    snapshot = service.accept_question_send(session.id, prepared.submission)
    assert [batch["item_id"] for batch in snapshot["batches"]] == ["q2"]
    assert snapshot["resolutions"]["q1"]["status"] == "sent"


def test_omitted_same_group_batch_does_not_block_unknown_older_question(session):
    service.merge_question_facts(session.id, [question(), question("q2"), end()])
    prepared = prepare(session, payload=response())
    service.accept_question_send(session.id, prepared.submission)
    snapshot = service.merge_question_facts(session.id, [question("q3")])
    assert [batch["item_id"] for batch in snapshot["batches"]] == ["q2"]
    assert snapshot["resolutions"]["q3"]["status"] == "sent"


def test_stale_resolved_response_rejects_before_preparation(session):
    service.merge_question_facts(session.id, [question(), end()])
    service.dismiss_question_batch(session.id, "q1", request_id="dismiss-1")
    with pytest.raises(ValueError, match="async_questions_stale"):
        prepare(session, payload=response())
    assert "send:send-1" not in state(session)["facts"]


def test_invalid_option_rejects_before_preparation(session):
    service.merge_question_facts(session.id, [question(), end()])
    with pytest.raises(ValueError, match="async_questions_invalid"):
        prepare(
            session,
            payload=response(
                answers=[
                    {"item_id": "q1", "index": 0, "kind": "option", "value": "Maybe"},
                ]
            ),
        )
    assert "send:send-1" not in state(session)["facts"]


def test_retry_and_accept_preserve_original_boundary_and_origin(session):
    service.merge_question_facts(session.id, [question(), end()])
    original = prepare(session)
    service.accept_question_send(
        session.id, {**original.submission, "status": "uncertain", "delivery_route": "steer", "target_turn_id": "t1"}
    )
    service.merge_question_facts(session.id, [question("q2", "t2", 5), end("t2", 6)])
    retry = prepare(session, second=7)
    assert retry.submission["boundary"] == original.submission["boundary"]
    forged = {
        **retry.submission,
        "status": "accepted",
        "origin": "agent",
        "boundary": {"at": at(9), "batch_ids": ["q2"]},
    }
    snapshot = service.accept_question_send(session.id, forged)
    stored = state(session)["facts"]["send:send-1"]["data"]
    assert stored["boundary"] == original.submission["boundary"]
    assert stored["origin"] == "human"
    assert stored["delivery_route"] == "steer"
    assert [batch["item_id"] for batch in snapshot["batches"]] == ["q2"]


def test_native_source_association_proves_uncertain_delivery(session):
    service.merge_question_facts(session.id, [question(), end()])
    prepared = prepare(session)
    service.accept_question_send(session.id, {**prepared.submission, "status": "uncertain"})
    source = QuestionFact(
        "user:u1",
        "user_submission",
        at(5),
        "t2",
        "u1",
        15,
        {
            "source": "jsonl",
            "origin": "human",
            "client_message_id": "send-1",
        },
    )
    snapshot = service.merge_question_facts(session.id, [source])
    assert snapshot["resolutions"]["q1"]["status"] == "sent"
    assert state(session)["facts"]["send:send-1"]["data"]["source_item_id"] == "u1"


def test_accept_requires_durable_preparation(session):
    with pytest.raises(ValueError, match="async_questions_invalid"):
        service.accept_question_send(session.id, {"request_id": "unknown"})


def test_rejected_steer_fallback_accepts_original_boundary(session):
    service.merge_question_facts(session.id, [question(), end()])
    prepared = prepare(session)
    service.accept_question_send(session.id, {**prepared.submission, "status": "rejected", "delivery_route": "steer"})
    retry = prepare(session, second=7)
    assert retry.submission["boundary"] == prepared.submission["boundary"]
    accepted = service.accept_question_send(
        session.id, {**retry.submission, "status": "accepted", "delivery_route": "start"}
    )
    assert accepted["resolutions"]["q1"]["status"] == "sent"
    assert state(session)["facts"]["send:send-1"]["data"]["delivery_route"] == "start"
    assert service.accept_question_send(session.id, {**retry.submission, "status": "rejected"}) == accepted


def test_unlinked_user_source_does_not_resolve_uncertain_route(session):
    service.merge_question_facts(session.id, [question(), end()])
    prepared = prepare(session)
    service.accept_question_send(session.id, {**prepared.submission, "status": "uncertain"})
    source = QuestionFact(
        "user:u1",
        "user_submission",
        at(5),
        "t2",
        "u1",
        None,
        {
            "source": "jsonl",
            "origin": "human",
        },
    )
    snapshot = service.merge_question_facts(session.id, [source, question(line=10)])
    assert snapshot["resolutions"] == {}
    assert snapshot["batches"][0]["status"] == "ready"
    stored = state(session)["facts"]["send:send-1"]["data"]
    assert stored["boundary"] == prepared.submission["boundary"]
    assert stored["origin"] == "human"
    assert stored["status"] == "uncertain"


def test_retry_does_not_bump_revision_or_reformat(session):
    service.merge_question_facts(session.id, [question(), end()])
    original = prepare(
        session,
        payload=response(
            answers=[
                {"item_id": "q1", "index": 0, "kind": "option", "value": "Yes"},
            ]
        ),
    )
    before = service.read_question_snapshot(session.id)
    retry = prepare(session, payload=response(), text=original.text, second=7)
    assert retry.text == original.text
    assert service.read_question_snapshot(session.id) == before


@pytest.mark.django_db(transaction=True)
def test_mutations_run_under_project_writer_serialization(session):
    import asyncio
    from asgiref.sync import sync_to_async
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        try:
            await db_writer.run_under_db_write_lock(
                lambda: sync_to_async(service.merge_question_facts)(session.id, [question(), end()]),
            )
            prepared = await db_writer.run_under_db_write_lock(lambda: sync_to_async(prepare)(session))
            return await db_writer.run_under_db_write_lock(
                lambda: sync_to_async(service.accept_question_send)(session.id, prepared.submission),
            )
        finally:
            await db_writer.stop_db_writer()

    assert asyncio.run(run())["resolutions"]["q1"] == {"status": "sent", "request_id": "send-1"}


@pytest.mark.parametrize(
    "provider,session_type,parent",
    [
        (Provider.CLAUDE_CODE, SessionType.SESSION, False),
        (Provider.CODEX, SessionType.SUBAGENT, True),
        (Provider.CODEX, SessionType.SESSION, True),
    ],
)
def test_other_provider_or_child_is_read_empty_and_mutation_rejected(session, provider, session_type, parent):
    child = Session.objects.create(
        id="other",
        project=session.project,
        provider=provider,
        type=session_type,
        parent_session=session if parent else None,
        file_path="other.jsonl",
    )
    assert service.read_question_snapshot(child.id)["batches"] == []
    for mutation in [
        lambda: service.merge_question_facts(child.id, [question()]),
        lambda: prepare(child),
        lambda: service.accept_question_send(child.id, {"request_id": "send-1"}),
        lambda: service.dismiss_question_batch(child.id, "q1", request_id="dismiss-1"),
    ]:
        with pytest.raises(ValueError, match="async_questions_invalid"):
            mutation()


def test_session_delete_cascades_question_state(session):
    from twicc.core.models import AsyncQuestionState

    service.merge_question_facts(session.id, [question()])
    session.delete()
    assert not AsyncQuestionState.objects.exists()


def test_read_snapshot_cannot_mutate_persisted_state(session):
    service.merge_question_facts(session.id, [question(), end()])
    original = deepcopy(state(session))
    snapshot = service.read_question_snapshot(session.id)
    snapshot["batches"][0]["questions"][0]["options"].clear()
    assert state(session) == original
