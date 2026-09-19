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

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from twicc.providers.pending_question import (
    is_answerable,
    normalized_options,
    out_of_scope_entry,
    question_entry,
)


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
        text = question.get("question", "")
        lines.append(f'- "{text}"')
        answer = answers.get(text)
        if answer:
            lines.append(f"  Answer: {answer}")
        else:
            lines.append("  (No answer provided)")
    return "\n".join(lines)


def _stored_questions(pending) -> list[dict]:
    questions = pending.tool_input.get("questions")
    return [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []


def _normalize_question(question: dict, index: int) -> dict:
    return {
        # 1-based index: Claude has no ids, and a 0-based one reads as a falsy
        # id in every shell that interpolates it.
        "id": str(index + 1),
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
    for a multi-select question. Unknown ids are dropped — the service validates
    them before reaching here, and the web UI cannot produce one.
    """
    stored = _stored_questions(pending)
    mapped: dict[str, str] = {}
    for question_id, values in answers.items():
        try:
            index = int(question_id) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(stored):
            mapped[stored[index].get("question", "")] = ", ".join(values)
    return mapped


def answers_from_ui(pending, ui_answers: dict) -> dict[str, list[str]]:
    """Map the widget's ``{question text: value}`` onto the normalized ids.

    Two jobs, and the second is easy to miss: the widget sends one **pre-joined**
    string per question, while the normalized form carries a list. Wrapping each
    value in a single-element list keeps the round trip lossless — forget it and
    the translator re-joins an already-joined string.
    """
    by_text = {
        q.get("question", ""): str(i + 1)
        for i, q in enumerate(_stored_questions(pending))
    }
    return {
        by_text[text]: [value]
        for text, value in ui_answers.items() if text in by_text
    }


def build_question_response(pending, *, action: str, answers: dict[str, list[str]]):
    """Return the ``PermissionResult`` for one answered question request.

    ``action`` is explicit on purpose. Deriving submit / partial from how many
    questions are answered is a **caller** rule: the CLI service derives it and
    raises the rejections, while the web UI already decides its own action
    front-end side and passes it straight through. That is what keeps the
    widget's behaviour identical.
    """
    if action == "cancel":
        return PermissionResultDeny(message=QUESTION_CANCEL_MESSAGE)

    stored = _stored_questions(pending)
    mapped = answers_by_text(pending, answers)
    if action == "partial":
        # A plain deny, not an interrupt: the agent stays alive and sees the
        # partial answers through the native clarify text.
        return PermissionResultDeny(message=build_clarify_message(stored, mapped))
    if action != "submit":
        raise ValueError(f"unknown question action: {action!r}")
    # The questions are rebuilt from the stored request, never from the caller,
    # so a caller cannot forge the question set.
    return PermissionResultAllow(updated_input={"questions": stored, "answers": mapped})
