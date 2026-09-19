"""Read and answer a session's pending question, for the CLI.

Two services, both reached through the drop-request transport:
``session:pending_requests`` reports what a live agent is waiting on, and
``session:answer_pending_question`` answers the one question among it that
this command owns.

Neither touches the database beyond the shared session guard. A pending
request exists only while a session has a live agent, so the truth is already
in this process's memory: the registry hands it over for both providers, and
there is nothing to keep in sync.

Design: ``docs/plans/2026-09-18-question-cli-design.md``.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from twicc.core.services.session_update import (
    UpdateSessionError,
    UpdateSessionResult,
    _lookup_session_for_update,
)
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.pending_question import is_answerable


logger = logging.getLogger(__name__)


class ReadPendingRequestsResult(NamedTuple):
    """What the read service returns.

    Same id fields as :class:`UpdateSessionResult` — the watcher copies them
    onto the status payload by name — plus ``status_extra``, which carries the
    data itself. The failure path returns the guard's ``UpdateSessionResult``
    unchanged; the watcher reads both shapes through ``getattr``.
    """

    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[UpdateSessionError] | None
    status_extra: dict | None = None


def _missing_session_id() -> UpdateSessionResult:
    return UpdateSessionResult(False, None, None, None, [
        UpdateSessionError("session_id", "missing", "session_id is required"),
    ])


def _reject(session_id, provider, project_id, field, code, message) -> UpdateSessionResult:
    return UpdateSessionResult(False, session_id, provider, project_id, [
        UpdateSessionError(field, code, message),
    ])


def _agent_state(info) -> str:
    """The 5-value projection the ``process`` family publishes.

    Derived from the live snapshot, never from a ``ProcessRun`` row: the
    persisted ``awaiting_user_input`` mirror is exactly the drift this design
    avoids. ``AgentState`` itself never says ``awaiting_user_input`` — the
    runtime stays in ``assistant_turn`` while the callback blocks, which is why
    the projection exists at all.
    """
    if info is None:
        return "dead"
    if info.pending_requests:
        return "awaiting_user_input"
    return str(info.state)


async def read_pending_requests_from_payload(payload: dict) -> ReadPendingRequestsResult | UpdateSessionResult:
    """Report every pending request of a session's live agent.

    Expected keys in ``payload``:
    - ``session_id`` (required).
    - ``raw`` (optional): when truthy, every entry also carries its untouched
      ``tool_input``.

    Emptiness is never a rejection. No live agent gives ``agent_state: "dead"``
    with an empty list and a success; so does a live agent waiting on nothing.
    The caller's next move is the same either way.
    """
    session_id = payload.get("session_id")
    if not session_id:
        return _missing_session_id()

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error

    from twicc.agent.registry import get_agent_manager_registry

    info = get_agent_manager_registry().get_agent_info(session_id)
    helpers = get_provider_helpers(provider)
    raw = bool(payload.get("raw"))
    entries = [
        helpers.normalize_pending_request(pending, raw=raw)
        for pending in (info.pending_requests if info is not None else ())
    ]
    return ReadPendingRequestsResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
        status_extra={"agent_state": _agent_state(info), "pending_requests": entries},
    )


def _resolve_target(info, request_id):
    """Pick the pending request to answer, or say why none can be.

    Returns ``(pending, error_code, message)``. The count is over the requests
    that pass the answerable filter, and what is pending *besides* them never
    enters it: a session loudly blocked on a tool approval holds zero answerable
    questions, which is ``no_pending_question`` — the ordinary case of this
    command, not an ambiguity.
    """
    if request_id:
        match = next((p for p in info.pending_requests if p.request_id == request_id), None)
        if match is None:
            # "Already resolved" and "never existed" are indistinguishable from
            # the outside, and the caller's next move is the same either way.
            return None, "request_gone", (
                f"No pending request {request_id!r} on session; it was answered "
                "or never existed"
            )
        if not is_answerable(match):
            return None, "not_a_question", (
                f"Pending request {request_id!r} is not a question this command "
                "can answer"
            )
        return match, None, None

    answerable = [p for p in info.pending_requests if is_answerable(p)]
    if not answerable:
        return None, "no_pending_question", "No answerable question is pending on this session"
    if len(answerable) > 1:
        return None, "ambiguous_request", (
            f"{len(answerable)} answerable questions are pending; "
            "name one with --request-id"
        )
    return answerable[0], None, None


def _validate_answers(entry: dict, answers: dict[str, list[str]]):
    """Check the caller's answers against the normalized question entry.

    Returns ``(error_code, message)`` or ``(None, None)``. This is where six of
    the rejection codes live, and they need the per-question metadata only the
    normalizer derives — which is why the write path normalizes too.
    """
    questions = {q["id"]: q for q in entry["questions"]}
    if any(not q["id"] for q in entry["questions"]):
        # An unreadable question publishes no id, so nothing can target it and
        # the request can never be answered in full. The read advertises
        # ``cancel`` alone for the same reason.
        return "missing_answers", (
            "This request holds a question with no id: nothing can target it, "
            "so the request cannot be answered in full. Cancel it, or answer it "
            "in the web UI."
        )
    if any(q["secret"] for q in entry["questions"]):
        # Per request, not per question: submitting needs every question
        # answered, so one secret makes the whole request unanswerable.
        return "secret_answer_unsupported", (
            "This request has a secret question; it cannot be answered from the "
            "CLI. Answer it in the web UI, or cancel."
        )

    for question_id, values in answers.items():
        question = questions.get(question_id)
        if question is None:
            return "unknown_question_id", (
                f"Question {question_id!r} is not part of this request; "
                f"known ids: {', '.join(questions) or '(none)'}"
            )
        if len(values) > 1 and not question["multi_select"]:
            return "multi_select_unsupported", (
                f"Question {question_id!r} accepts one answer, got {len(values)}"
            )
        labels = {option["label"] for option in question["options"]}
        free_text = [v for v in values if v not in labels]
        if free_text and not question["allows_free_text"]:
            return "free_text_not_allowed", (
                f"Question {question_id!r} accepts only its options: "
                f"{', '.join(sorted(labels)) or '(none)'}"
            )
        if free_text and len(free_text) != len(values):
            # The widget clears the selections when "Other" becomes active, and
            # clears "Other" when an option is picked. Without this rule the
            # translator would join them into a value the widget can never emit.
            return "free_text_exclusive", (
                f"Question {question_id!r} mixes free text with its options; "
                "free text is exclusive"
            )
    return None, None


def _derive_action(entry: dict, answers: dict[str, list[str]], *, partial_supported: bool):
    """Turn "how many questions are answered" into the translator's action.

    The rule reads *"at least one question, and every one of them answered"*, so
    a request with an empty ``questions`` list never submits — it is the one
    case where "all" and "none" would both match, and it resolves as
    ``missing_answers``. ``cancel`` stays the way out.
    """
    questions = entry["questions"]
    answered = {qid for qid, values in answers.items() if any(v.strip() for v in values)}
    if not questions or not answered:
        return None, "missing_answers", "Answer at least one question, or cancel"
    if len(answered) == len(questions):
        return "submit", None, None
    unanswered = [q["id"] for q in questions if q["id"] not in answered]
    if not partial_supported:
        return None, "missing_answers", (
            "This provider has no partial answer; unanswered questions: "
            + ", ".join(unanswered)
        )
    return "partial", None, None


async def answer_pending_question_from_payload(payload: dict) -> UpdateSessionResult:
    """Answer (or decline) the one answerable question of a session.

    Expected keys in ``payload``:
    - ``session_id`` (required).
    - ``action`` (required): ``answer`` or ``cancel``.
    - ``request_id`` (optional): the pending request to target. Without it the
      only answerable question is used, and several is an error.
    - ``answers`` (optional): ``{question id: [value, …]}``, ``answer`` only.
    - ``caller_session_id`` (optional): the session issuing the call, used for
      the one security rule below.

    A session may not answer its own pending question. The rule is
    authenticated over MCP — the connection resolves the calling session from
    its signed token — and a guardrail on the CLI, where the value is stamped
    client-side.
    """
    session_id = payload.get("session_id")
    if not session_id:
        return _missing_session_id()
    action = payload.get("action")
    if action not in ("answer", "cancel"):
        return _reject(session_id, None, None, "action", "invalid_action",
                       f"Unknown action {action!r}; expected 'answer' or 'cancel'")

    session, project, provider, error = await _lookup_session_for_update(session_id)
    if error is not None:
        return error
    project_id = session.project_id
    provider_value = provider.value

    if payload.get("caller_session_id") == session_id:
        return _reject(session_id, provider_value, project_id, "session_id",
                       "self_answer_refused",
                       "A session cannot answer its own pending question")

    from twicc.agent.registry import get_agent_manager_registry

    registry = get_agent_manager_registry()
    info = registry.get_agent_info(session_id)
    if info is None:
        return _reject(session_id, provider_value, project_id, "session_id", "agent_dead",
                       f"Session {session_id!r} has no live agent")

    pending, code, message = _resolve_target(info, payload.get("request_id"))
    if code is not None:
        return _reject(session_id, provider_value, project_id, "request_id", code, message)

    helpers = get_provider_helpers(provider)
    entry = helpers.normalize_pending_request(pending)
    answers = payload.get("answers") or {}

    if action == "cancel":
        # Nothing to validate: declining carries no value, which is why it stays
        # available even on a request this command would otherwise refuse.
        if answers:
            return _reject(session_id, provider_value, project_id, "answers",
                           "option_not_accepted",
                           "--answer is not accepted by the 'cancel' action")
        translator_action = "cancel"
    else:
        code, message = _validate_answers(entry, answers)
        if code is not None:
            return _reject(session_id, provider_value, project_id, "answers", code, message)
        translator_action, code, message = _derive_action(
            entry, answers, partial_supported=helpers.question_partial_supported,
        )
        if code is not None:
            return _reject(session_id, provider_value, project_id, "answers", code, message)

    response = helpers.build_question_response(
        pending, action=translator_action, answers=answers,
    )
    manager = registry.find_manager_for_session(session_id)
    resolved = manager is not None and await manager.resolve_pending_request(
        session_id, pending.request_id, response,
    )
    if not resolved:
        # The Future resolves once: a human clicking in the UI, or another
        # caller, wins and this one learns it rather than reporting a success.
        return _reject(session_id, provider_value, project_id, "request_id", "request_gone",
                       f"Pending request {pending.request_id!r} was already resolved")

    logger.info(
        "[answer_pending_question] session=%s request=%s action=%s",
        session_id, pending.request_id, translator_action,
    )
    return UpdateSessionResult(
        success=True,
        session_id=session_id,
        provider=provider_value,
        project_id=project_id,
        errors=None,
    )
