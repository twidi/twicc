"""The two pending-question services.

Both read the live agent registry; neither persists anything. The tests patch
the registry so a session can hold any pending request without a real agent,
and cover one case per rejection code the design publishes.

Design: ``docs/plans/2026-09-18-question-cli-design.md`` §5, §6, §7.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from twicc.agent.states import AgentState, PendingRequest
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.services.pending_question import (
    answer_pending_question_from_payload,
    read_pending_requests_from_payload,
)


SESSION_ID = "s-question"


def make_session(provider=Provider.CLAUDE_CODE) -> Session:
    project = Project.objects.create(id="-p-question", directory="/tmp")
    return Session.objects.create(
        id=SESSION_ID, project=project, provider=provider.value,
        type=SessionType.SESSION,
    )


def claude_pending(questions=None, **overrides) -> PendingRequest:
    fields = {
        "request_id": "req-1",
        "request_type": "ask_user_question",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions if questions is not None else [
            {"question": "Which database?", "header": "Database",
             "options": [{"label": "PostgreSQL"}, {"label": "SQLite"}]},
        ]},
        "created_at": time.time(),
    }
    fields.update(overrides)
    return PendingRequest(**fields)


def approval_pending(request_id="req-approval") -> PendingRequest:
    return PendingRequest(
        request_id=request_id, request_type="tool_approval", tool_name="Bash",
        tool_input={"command": "ls"}, created_at=time.time(),
    )


def run(coro_factory, *, pendings=(), agent=True, resolved=True, state=AgentState.ASSISTANT_TURN):
    """Run a service with a patched registry. Returns (result, manager)."""
    info = MagicMock(pending_requests=tuple(pendings), state=state)
    manager = MagicMock()
    manager.resolve_pending_request = AsyncMock(return_value=resolved)
    registry = MagicMock()
    registry.get_agent_info.return_value = info if agent else None
    registry.find_manager_for_session.return_value = manager if agent else None
    with patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry), \
         patch("twicc.core.services.session_update.ensure_provider_running"):
        return asyncio.run(coro_factory()), manager


def codes(result) -> list[str]:
    return [e.code for e in (result.errors or [])]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_read_requires_a_session_id():
    result, _ = run(lambda: read_pending_requests_from_payload({}))
    assert codes(result) == ["missing"]


@pytest.mark.django_db(transaction=True)
def test_read_refuses_an_unknown_session():
    result, _ = run(lambda: read_pending_requests_from_payload({"session_id": "nope"}))
    assert codes(result) == ["session_not_found"]


@pytest.mark.django_db(transaction=True)
def test_read_reports_a_question():
    make_session()
    result, _ = run(
        lambda: read_pending_requests_from_payload({"session_id": SESSION_ID}),
        pendings=[claude_pending()],
    )
    assert result.success
    assert result.status_extra["agent_state"] == "awaiting_user_input"
    entry = result.status_extra["pending_requests"][0]
    assert entry["kind"] == "question"
    assert [q["id"] for q in entry["questions"]] == ["1"]


@pytest.mark.django_db(transaction=True)
def test_read_reports_what_it_cannot_answer():
    # A session blocked on a tool approval must not be reported as idle.
    make_session()
    result, _ = run(
        lambda: read_pending_requests_from_payload({"session_id": SESSION_ID}),
        pendings=[approval_pending()],
    )
    entry = result.status_extra["pending_requests"][0]
    assert (entry["kind"], entry["reason"], entry["actions"]) == ("out_of_scope", "tool_approval", [])


@pytest.mark.django_db(transaction=True)
def test_read_succeeds_with_no_agent_at_all():
    # Emptiness is never a rejection on this path.
    make_session()
    result, _ = run(
        lambda: read_pending_requests_from_payload({"session_id": SESSION_ID}), agent=False,
    )
    assert result.success
    assert result.status_extra == {"agent_state": "dead", "pending_requests": []}


@pytest.mark.django_db(transaction=True)
def test_read_succeeds_with_a_live_agent_waiting_on_nothing():
    make_session()
    result, _ = run(lambda: read_pending_requests_from_payload({"session_id": SESSION_ID}))
    assert result.success
    assert result.status_extra["agent_state"] == "assistant_turn"
    assert result.status_extra["pending_requests"] == []


@pytest.mark.django_db(transaction=True)
def test_raw_covers_every_entry_of_a_mixed_list():
    make_session()
    result, _ = run(
        lambda: read_pending_requests_from_payload({"session_id": SESSION_ID, "raw": True}),
        pendings=[claude_pending(), approval_pending()],
    )
    entries = result.status_extra["pending_requests"]
    assert [e["kind"] for e in entries] == ["question", "out_of_scope"]
    assert all("raw" in e for e in entries)
    assert entries[1]["raw"] == {"tool_input": {"command": "ls"}}


# ---------------------------------------------------------------------------
# Answering — the guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_answer_refuses_an_unknown_action():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "maybe"}))
    assert codes(result) == ["invalid_action"]


@pytest.mark.django_db(transaction=True)
def test_answer_refuses_an_unknown_session():
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": "nope", "action": "cancel"}))
    assert codes(result) == ["session_not_found"]


@pytest.mark.django_db(transaction=True)
def test_a_session_cannot_answer_its_own_question():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "caller_session_id": SESSION_ID}),
        pendings=[claude_pending()])
    assert codes(result) == ["self_answer_refused"]


@pytest.mark.django_db(transaction=True)
def test_another_session_may_answer():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "caller_session_id": "other"}),
        pendings=[claude_pending()])
    assert result.success


@pytest.mark.django_db(transaction=True)
def test_answer_refuses_a_dead_agent():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}), agent=False)
    assert codes(result) == ["agent_dead"]


# ---------------------------------------------------------------------------
# Answering — target resolution
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_session_blocked_only_on_an_approval_has_no_question():
    # The commonest case, and it is not an ambiguity.
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}), pendings=[approval_pending()])
    assert codes(result) == ["no_pending_question"]


@pytest.mark.django_db(transaction=True)
def test_two_questions_without_a_request_id_are_ambiguous():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}),
        pendings=[claude_pending(), claude_pending(request_id="req-2")])
    assert codes(result) == ["ambiguous_request"]


@pytest.mark.django_db(transaction=True)
def test_a_request_id_disambiguates():
    make_session()
    result, manager = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "request_id": "req-2"}),
        pendings=[claude_pending(), claude_pending(request_id="req-2")])
    assert result.success
    assert manager.resolve_pending_request.await_args.args[1] == "req-2"


@pytest.mark.django_db(transaction=True)
def test_an_unknown_request_id_is_gone():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "request_id": "ghost"}),
        pendings=[claude_pending()])
    assert codes(result) == ["request_gone"]


@pytest.mark.django_db(transaction=True)
def test_a_request_id_naming_an_approval_is_not_a_question():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "request_id": "req-approval"}),
        pendings=[claude_pending(), approval_pending()])
    assert codes(result) == ["not_a_question"]


@pytest.mark.django_db(transaction=True)
def test_losing_the_race_is_not_a_silent_success():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}),
        pendings=[claude_pending()], resolved=False)
    assert codes(result) == ["request_gone"]


# ---------------------------------------------------------------------------
# Answering — validating the answers
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_cancel_takes_no_answer():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel", "answers": {"1": ["PostgreSQL"]}}),
        pendings=[claude_pending()])
    assert codes(result) == ["option_not_accepted"]


@pytest.mark.django_db(transaction=True)
def test_an_unknown_question_id_is_refused():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"7": ["PostgreSQL"]}}),
        pendings=[claude_pending()])
    assert codes(result) == ["unknown_question_id"]


@pytest.mark.django_db(transaction=True)
def test_repeating_an_answer_on_a_single_select_question_is_refused():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer",
         "answers": {"1": ["PostgreSQL", "SQLite"]}}),
        pendings=[claude_pending()])
    assert codes(result) == ["multi_select_unsupported"]


@pytest.mark.django_db(transaction=True)
def test_multi_select_accepts_several_values():
    make_session()
    pending = claude_pending([
        {"question": "Which caches?", "multiSelect": True,
         "options": [{"label": "Redis"}, {"label": "Memcached"}]},
    ])
    result, manager = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer",
         "answers": {"1": ["Redis", "Memcached"]}}), pendings=[pending])
    assert result.success
    response = manager.resolve_pending_request.await_args.args[2]
    assert response.updated_input["answers"] == {"Which caches?": "Redis, Memcached"}


@pytest.mark.django_db(transaction=True)
def test_free_text_is_refused_when_the_question_forbids_it():
    make_session()
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "db", "question": "Which database?", "isOther": False,
             "options": [{"label": "PostgreSQL"}]},
        ]},
        created_at=time.time(),
    )
    make_codex = Session.objects.filter(id=SESSION_ID).update(provider=Provider.CODEX.value)
    assert make_codex == 1
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"db": ["MySQL"]}}),
        pendings=[pending])
    assert codes(result) == ["free_text_not_allowed"]


@pytest.mark.django_db(transaction=True)
def test_free_text_is_exclusive_of_the_options():
    make_session()
    pending = claude_pending([
        {"question": "Which caches?", "multiSelect": True,
         "options": [{"label": "Redis"}, {"label": "Memcached"}]},
    ])
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer",
         "answers": {"1": ["Redis", "Something else"]}}), pendings=[pending])
    assert codes(result) == ["free_text_exclusive"]


@pytest.mark.django_db(transaction=True)
def test_a_secret_question_cannot_be_answered():
    Session.objects.filter(id=make_session().id).update(provider=Provider.CODEX.value)
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "token", "question": "Your token?", "isSecret": True,
             "options": [{"label": "A"}]},
        ]},
        created_at=time.time(),
    )
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"token": ["abc"]}}),
        pendings=[pending])
    assert codes(result) == ["secret_answer_unsupported"]


@pytest.mark.django_db(transaction=True)
def test_a_secret_question_can_still_be_declined():
    Session.objects.filter(id=make_session().id).update(provider=Provider.CODEX.value)
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "token", "question": "Your token?", "isSecret": True,
             "options": [{"label": "A"}]},
        ]},
        created_at=time.time(),
    )
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}), pendings=[pending])
    assert result.success


# ---------------------------------------------------------------------------
# Answering — how many answers
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_answering_nothing_is_missing_answers():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer"}), pendings=[claude_pending()])
    assert codes(result) == ["missing_answers"]


@pytest.mark.django_db(transaction=True)
def test_an_empty_question_list_never_submits():
    make_session()
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer"}),
        pendings=[claude_pending([])])
    assert codes(result) == ["missing_answers"]


@pytest.mark.django_db(transaction=True)
def test_answering_every_question_submits():
    make_session()
    result, manager = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"1": ["PostgreSQL"]}}),
        pendings=[claude_pending()])
    assert result.success
    response = manager.resolve_pending_request.await_args.args[2]
    assert response.updated_input["answers"] == {"Which database?": "PostgreSQL"}


@pytest.mark.django_db(transaction=True)
def test_answering_some_questions_is_partial_on_claude():
    make_session()
    pending = claude_pending([
        {"question": "Which database?", "options": [{"label": "PostgreSQL"}]},
        {"question": "Which cache?", "options": [{"label": "Redis"}]},
    ])
    result, manager = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"1": ["PostgreSQL"]}}),
        pendings=[pending])
    assert result.success
    response = manager.resolve_pending_request.await_args.args[2]
    assert "wants to clarify" in response.message
    assert "(No answer provided)" in response.message


@pytest.mark.django_db(transaction=True)
def test_answering_some_questions_is_missing_answers_on_codex():
    Session.objects.filter(id=make_session().id).update(provider=Provider.CODEX.value)
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "db", "question": "Which database?", "options": [{"label": "PostgreSQL"}]},
            {"id": "cache", "question": "Which cache?", "options": [{"label": "Redis"}]},
        ]},
        created_at=time.time(),
    )
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"db": ["PostgreSQL"]}}),
        pendings=[pending])
    assert codes(result) == ["missing_answers"]


@pytest.mark.django_db(transaction=True)
def test_a_codex_answer_uses_the_native_wire_shape():
    Session.objects.filter(id=make_session().id).update(provider=Provider.CODEX.value)
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "db", "question": "Which database?", "options": [{"label": "PostgreSQL"}]},
        ]},
        created_at=time.time(),
    )
    result, manager = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"db": ["PostgreSQL"]}}),
        pendings=[pending])
    assert result.success
    assert manager.resolve_pending_request.await_args.args[2] == {
        "answers": {"db": {"answers": ["PostgreSQL"]}},
    }


@pytest.mark.django_db(transaction=True)
def test_a_disabled_provider_is_refused_by_both_commands():
    # Raised server-side by the shared guard, not client-side: it belongs to the
    # read and the write alike. The test environment disables every provider, so
    # the gate is live here — this is the one test that does not patch it.
    make_session()
    registry = MagicMock()
    registry.get_agent_info.return_value = MagicMock(pending_requests=(), state=AgentState.USER_TURN)
    with patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry):
        read = asyncio.run(read_pending_requests_from_payload({"session_id": SESSION_ID}))
        write = asyncio.run(answer_pending_question_from_payload(
            {"session_id": SESSION_ID, "action": "cancel"}))
    assert codes(read) == ["provider_disabled"]
    assert codes(write) == ["provider_disabled"]


@pytest.mark.django_db(transaction=True)
def test_a_question_the_command_cannot_read_is_refused():
    # It publishes no id, so nothing can target it and the request can never be
    # answered in full. The read advertises ``cancel`` alone for the same
    # reason; refusing the answer is the other half of that promise.
    make_session()
    pending = claude_pending(["not a dict", {"question": "Which cache?"}])
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"2": ["Redis"]}}),
        pendings=[pending])
    assert codes(result) == ["missing_answers"]


@pytest.mark.django_db(transaction=True)
def test_it_can_still_be_declined():
    make_session()
    pending = claude_pending(["not a dict", {"question": "Which cache?"}])
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "cancel"}), pendings=[pending])
    assert result.success


@pytest.mark.django_db(transaction=True)
def test_two_questions_sharing_one_id_are_refused():
    # Same rule as the read's cancel-only, quoted from the same function so the
    # advertised actions and the refusal cannot drift apart.
    Session.objects.filter(id=make_session().id).update(provider=Provider.CODEX.value)
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question",
        tool_name="toolRequestUserInput",
        tool_input={"questions": [
            {"id": "db", "question": "Which database?", "options": [{"label": "A"}]},
            {"id": "db", "question": "Which one really?", "options": [{"label": "B"}]},
        ]},
        created_at=time.time(),
    )
    result, _ = run(lambda: answer_pending_question_from_payload(
        {"session_id": SESSION_ID, "action": "answer", "answers": {"db": ["A"]}}),
        pendings=[pending])
    assert codes(result) == ["missing_answers"]
    assert "sharing one id" in result.errors[0].message
