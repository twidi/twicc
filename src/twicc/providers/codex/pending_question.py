"""Codex's half of the pending-question contract.

Mirror of the Claude module: ``normalize_pending_request`` describes a pending
request, ``build_question_response`` turns an answer into the wire dict the
approval bridge returns to Codex. Two differences carry through everything
else — Codex questions have **native ids** and are answered by them, and Codex
is **single-select only** (the native ``request_user_input`` tool never emits
multi-select, and the front-end drops the concept entirely).

Design: ``docs/plans/2026-09-18-question-cli-design.md`` §5 and §6.
"""

from __future__ import annotations

import logging

from twicc.providers.pending_question import (
    is_answerable,
    normalized_options,
    out_of_scope_entry,
    question_entry,
)


def _stored_questions(pending) -> list[dict]:
    questions = pending.tool_input.get("questions")
    return [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []


def _normalize_question(question: dict) -> dict:
    question_id = question.get("id")
    options = normalized_options(question.get("options"))
    return {
        # The wire guarantees a string id; a pathological payload gets an empty
        # one, which no ``--answer`` can target — the same defensive stance the
        # front-end takes when it skips such a question.
        "id": question_id if isinstance(question_id, str) else "",
        "header": question.get("header", ""),
        "question": question.get("question", ""),
        "multi_select": False,
        # ``isOther`` decides; a question with no options at all is bare free
        # text. That second branch is defensive — the native tool rejects a
        # question without options — and is kept because the front-end keeps it.
        "allows_free_text": bool(question.get("isOther")) or not options,
        "secret": bool(question.get("isSecret")),
        "options": options,
    }


def normalize_pending_request(pending, *, raw: bool = False) -> dict:
    if not is_answerable(pending):
        return out_of_scope_entry(pending, raw=raw)
    questions = [_normalize_question(q) for q in _stored_questions(pending)]
    return question_entry(pending, questions, raw=raw)


def build_question_response(pending, *, action: str, answers: dict[str, list[str]]) -> dict:
    """Return the ``ToolRequestUserInputResponse`` wire dict.

    ``{"answers": {question_id: {"answers": [str, …]}}}`` — an empty map is
    valid, and is how a cancel is expressed: Codex reads a missing answer as a
    decline.

    **No ``partial``.** A partially answered request is ``missing_answers`` on
    this provider (the caller refuses it before reaching here), so being asked
    for one is a programming error, not a user outcome.
    """
    if action == "cancel":
        return {"answers": {}}
    if action != "submit":
        raise ValueError(f"unknown question action for codex: {action!r}")
    return {"answers": {qid: {"answers": list(values)} for qid, values in answers.items()}}


logger = logging.getLogger(__name__)


def response_from_ui(content: dict) -> dict | None:
    """Validate the widget's ``{tool_name, answers}`` into the wire response.

    The web UI sends the native shape already — ``{id: {"answers": [str, …]}}``
    — so this only validates it and hands the values to the shared translator.
    Returns ``None`` on any validation failure, which the handler turns into its
    wire-safe fallback rather than letting a malformed dict reach the SDK. An
    empty map is valid and means cancel.
    """
    answers = content.get("answers")
    if not isinstance(answers, dict):
        logger.error(
            "codex toolRequestUserInput: invalid answers type=%r",
            type(answers).__name__,
        )
        return None
    values: dict[str, list[str]] = {}
    for question_id, entry in answers.items():
        entry_values = entry.get("answers") if isinstance(entry, dict) else None
        if (
            not isinstance(question_id, str)
            or not isinstance(entry_values, list)
            or not all(isinstance(v, str) for v in entry_values)
        ):
            logger.error(
                "codex toolRequestUserInput: invalid entry for %r: %r",
                question_id, entry,
            )
            return None
        values[question_id] = entry_values
    # ``pending`` is unused on this provider: the ids are native, so nothing has
    # to be mapped back onto the stored questions.
    return build_question_response(None, action="submit", answers=values)
