"""Claude Code's half of the pending-question contract.

Two directions, one per caller. ``normalize_pending_request`` describes a
pending request for ``session <ID> pending-request``;
``build_question_response`` turns an answer into the ``PermissionResult`` the
SDK returns to the agent. The WebSocket handler and the CLI service both go
through the second one, so there is exactly one implementation of the wire
shape.

Claude has no question ids: a question is identified by its **1-based index**
into ``tool_input["questions"]``, and answered by its **text**. Nothing is
cached between the two calls — the index→text mapping is recomputed from the
stored questions, the same list the widget reads.

Design: ``docs/plans/2026-09-18-question-cli-design.md`` §5 and §6.
"""

from __future__ import annotations

import logging

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from twicc.providers.pending_question import (
    is_answerable,
    normalized_options,
    out_of_scope_entry,
    question_entry,
)


logger = logging.getLogger(__name__)


# Deny message sent when the user cancels an AskUserQuestion. Deliberately different
# from the native CLI cancel (which hard-interrupts the turn — that surfaces as a
# "terminated due to error" toast in the GUI): we keep the turn alive, exactly like
# "partial", and have the agent acknowledge the decline and hand control back. The SDK
# forwards this verbatim as the tool_result content; a non-empty message also avoids
# the empty ``is_error`` API rejection.
QUESTION_CANCEL_MESSAGE = (
    "The user chose not to answer these questions. Acknowledge this briefly and ask "
    "them how they would like to proceed."
)


def build_clarify_message(questions: list[dict], answers: dict) -> str:
    """Reproduce Claude Code's native "clarify" tool result for a partially
    answered ``AskUserQuestion``.

    When the user answers some but not all questions, the Claude Code CLI rejects
    the tool with this exact text: a fixed preamble telling the agent the user
    wants to clarify, followed by every question in order with its answer (or
    ``(No answer provided)``). Matching it verbatim means the agent receives the
    same signal it would from the native TUI.

    ``questions`` is the original ``input.questions`` list; ``answers`` maps a
    question's text to the user's answer (absent or empty for unanswered ones).
    """
    lines = [
        "The user wants to clarify these questions.",
        "    This means they may have additional information, context or questions for you.",
        "    Take their response into account and then reformulate the questions if appropriate.",
        "    Start by asking them what they would like to clarify.",
        "",
        "    Questions asked:",
    ]
    for question in questions:
        # A malformed entry keeps its line rather than vanishing: the agent
        # numbered its questions and must be able to count them back.
        text = question.get("question", "") if isinstance(question, dict) else ""
        lines.append(f'- "{text}"')
        answer = answers.get(text)
        if answer:
            lines.append(f"  Answer: {answer}")
        else:
            lines.append("  (No answer provided)")
    return "\n".join(lines)


def _stored_questions(pending) -> list:
    """The stored question list, passed on as the agent wrote it.

    Entries are NOT filtered: a question is identified by its position, so
    dropping a malformed one would renumber every question after it. The
    callers guard the entry instead.
    """
    questions = pending.tool_input.get("questions")
    return questions if isinstance(questions, list) else []


def _normalize_question(question, index: int) -> dict:
    # A pathological payload keeps its slot, so the ids of the questions after
    # it stay aligned with what the agent asked. It publishes an **empty** id,
    # which no answer can target — and which makes the whole request
    # cancel-only, rather than one that accepts an answer it would then drop.
    readable = isinstance(question, dict)
    if not readable:
        question = {}
    return {
        # 1-based index: Claude has no ids, and a 0-based one reads as a falsy
        # id in every shell that interpolates it.
        "id": str(index + 1) if readable else "",
        "header": question.get("header", ""),
        "question": question.get("question", ""),
        "multi_select": bool(question.get("multiSelect")),
        # "Other" is unconditional in the widget.
        "allows_free_text": True,
        # Claude has no secret questions; the field exists so both providers
        # publish one shape.
        "secret": False,
        "options": normalized_options(question.get("options")),
    }


def normalize_pending_request(pending, *, raw: bool = False) -> dict:
    if not is_answerable(pending):
        return out_of_scope_entry(pending, raw=raw)
    questions = [_normalize_question(q, i) for i, q in enumerate(_stored_questions(pending))]
    return question_entry(pending, questions, raw=raw)


def answers_by_text(pending, answers: dict[str, list[str]]) -> dict[str, str]:
    """Turn the ``{id: [value, …]}`` answers into the widget's ``{text: value}``.

    Multiple values join with ``", "``, which is what the widget itself sends
    for a multi-select question. Only the CLI path reaches this — the widget has
    its own entry point and never keys by id. An id naming no readable question
    is dropped; the service refuses it upstream, so this is the second guard.
    """
    stored = _stored_questions(pending)
    mapped: dict[str, str] = {}
    for question_id, values in answers.items():
        try:
            index = int(question_id) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(stored) and isinstance(stored[index], dict):
            mapped[stored[index].get("question", "")] = ", ".join(values)
    return mapped


def _response_for(pending, *, action: str, answers: dict):
    """The wire response, over the widget's own ``{question text: value}`` map.

    Both entry points land here, which is what makes the two paths one
    implementation. It reads the answers but never rebuilds them, so any value
    the caller put in reaches the agent as the caller wrote it.
    """
    if action == "cancel":
        return PermissionResultDeny(message=QUESTION_CANCEL_MESSAGE)

    stored = _stored_questions(pending)
    if action == "partial":
        # A plain deny, not an interrupt: the agent stays alive and sees the
        # partial answers through the native clarify text.
        return PermissionResultDeny(message=build_clarify_message(stored, answers))
    if action != "submit":
        raise ValueError(f"unknown question action: {action!r}")
    # The questions are rebuilt from the stored request, never from the caller,
    # so a caller cannot forge the question set.
    return PermissionResultAllow(updated_input={"questions": stored, "answers": answers})


def build_question_response(pending, *, action: str, answers: dict[str, list[str]]):
    """Return the ``PermissionResult`` for one answered question request.

    ``answers`` is keyed by the ids :func:`normalize_pending_request` publishes,
    each value a list. ``action`` is explicit on purpose: deriving submit /
    partial from how many questions are answered is a **caller** rule, which is
    what lets the web UI keep deciding its own action.
    """
    return _response_for(pending, action=action,
                         answers=answers_by_text(pending, answers))


def build_question_response_from_ui(pending, *, action: str, ui_answers):
    """Same, over the payload the web UI sends: ``{question text: value}``.

    The widget pre-joins a multi-select answer into one string, so its map needs
    no translation at all — it already is what the agent receives. Going through
    the id form and back would only add two chances to lose a value.

    A non-dict ``ui_answers`` is a malformed payload, not a user outcome: it is
    logged and read as "nothing answered" rather than raised, since this runs
    inside the WebSocket consumer, where an exception would drop the
    connection and leave the request pending.
    """
    if not isinstance(ui_answers, dict):
        logger.error("claude_code ask_user_question: invalid answers type=%r",
                     type(ui_answers).__name__)
        ui_answers = {}
    return _response_for(pending, action=action, answers=ui_answers)
