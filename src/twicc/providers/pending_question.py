"""What the two providers share when they describe a pending question.

The CLI's ``session <ID> pending-request`` reports **every** pending request of
a live agent, and answers only the two that are questions — Claude's
``AskUserQuestion`` and Codex's ``toolRequestUserInput``. The filter that draws
that line, the reason an entry falls outside it, and the entry shapes are the
same on both sides; only the per-question fields and the wire response differ,
and those live in the per-provider modules next door.

Design: ``docs/plans/2026-09-18-question-cli-design.md`` §3 and §5.
"""

from __future__ import annotations

from datetime import datetime, UTC


#: The two ``tool_name`` values that carry a question. Everything else is a
#: tool approval, an elicitation or a choice, and stays out of scope.
QUESTION_TOOL_NAMES = frozenset({"AskUserQuestion", "toolRequestUserInput"})

#: Codex builds an MCP tool approval as a ``toolRequestUserInput`` form when the
#: user turned ``ToolCallMcpElicitation`` off in their own config. It authors the
#: form itself, with exactly one question whose id is
#: ``mcp_tool_call_approval_<call_id>`` — the prefix is the discriminator.
#:
#: The id is NOT a field the model is barred from writing: it is a property of
#: the ``request_user_input`` tool schema. What makes the rule work is only that
#: Codex *always* writes this prefix when the form really is an approval; the
#: converse is not guaranteed. So the rule degrades toward *accepting* if Codex
#: ever renames the id — re-check it at every Codex re-vendoring, like the enum
#: patch in the GPT-5.6 integration.
MCP_APPROVAL_ID_PREFIX = "mcp_tool_call_approval"

_ELICITATION_TOOL_NAMES = frozenset({"elicitationForm", "elicitationUrl"})

#: The three actions a question answer may carry. Codex has no ``partial`` —
#: a partially answered request is ``missing_answers`` there.
QUESTION_ACTIONS = frozenset({"submit", "partial", "cancel"})

ANSWER_ACTION = {"action": "answer", "label": "Answer the questions",
                 "accepts": ["--answer"]}
CANCEL_ACTION = {"action": "cancel", "label": "Decline to answer"}


def is_disguised_mcp_approval(pending) -> bool:
    """Whether a ``toolRequestUserInput`` is really an MCP tool approval.

    Guarded against a pathological payload: the caller is a third party's wire
    format, and ``"x".get`` raises where the front-end's ``"x".id`` would merely
    be ``undefined``.
    """
    if pending.tool_name != "toolRequestUserInput":
        return False
    questions = pending.tool_input.get("questions")
    return (
        isinstance(questions, list)
        and bool(questions)
        and isinstance(questions[0], dict)
        and isinstance(questions[0].get("id"), str)
        and questions[0]["id"].startswith(MCP_APPROVAL_ID_PREFIX)
    )


def is_answerable(pending) -> bool:
    """The one definition of *answerable*, and the only target-selection rule.

    All three conditions carry weight. ``request_type`` alone lets the MCP
    elicitations in — they are ``ask_user_question`` too. ``tool_name`` alone
    lets a degraded hybrid question through, because the GUI-expiry rewrite
    changes only the ``request_type``.
    """
    return (
        pending.request_type == "ask_user_question"
        and pending.tool_name in QUESTION_TOOL_NAMES
        and not is_disguised_mcp_approval(pending)
    )


def out_of_scope_reason(pending) -> str:
    """Why an entry is not answerable **here** — not a cross-provider taxonomy.

    Hence one asymmetry worth naming: Codex's post-plan prompt is ``choice``
    while Claude's plan gate is an ordinary tool approval and reports
    ``tool_approval``. Equivalent to a human, different to the code; the code's
    view is the one reported.
    """
    if pending.request_type == "hybrid_terminal":
        return "terminal_only"
    if is_disguised_mcp_approval(pending):
        # Its own value rather than a fold into ``tool_approval``, so the
        # exclusion is visible in the output and debuggable.
        return "mcp_tool_approval"
    if pending.tool_name in _ELICITATION_TOOL_NAMES:
        return "elicitation"
    if pending.tool_name == "planImplementation":
        return "choice"
    return "tool_approval"


def created_at_iso(created_at: float) -> str:
    """``PendingRequest.created_at`` is an epoch float; the CLI publishes ISO."""
    return datetime.fromtimestamp(created_at, UTC).isoformat()


def age_seconds(created_at: float) -> float:
    """How long the request has waited, computed at read time."""
    return round(datetime.now(UTC).timestamp() - created_at, 1)


def _with_raw(entry: dict, pending, *, raw: bool) -> dict:
    # ``--raw`` covers every entry, question or not: reading the live registry
    # means the untouched input is there for the asking, with nothing persisted.
    if raw:
        entry["raw"] = {"tool_input": pending.tool_input}
    return entry


def out_of_scope_entry(pending, *, raw: bool = False) -> dict:
    """The minimal entry for something waiting that this command cannot answer.

    It exists to say *"something is waiting and it is not mine"*, so it stops at
    what that sentence needs. No ``age_seconds``, no questions, no payload.
    """
    return _with_raw({
        "request_id": pending.request_id,
        "created_at": created_at_iso(pending.created_at),
        "kind": "out_of_scope",
        "reason": out_of_scope_reason(pending),
        "tool_name": pending.tool_name,
        "actions": [],
    }, pending, raw=raw)


def question_entry(pending, questions: list[dict], *, raw: bool = False) -> dict:
    """The full entry for an answerable question.

    ``actions`` carries ``cancel`` **alone** when the request is structurally
    unanswerable — an empty ``questions`` list, or any question flagged
    ``secret``. Both turn an answer attempt into a guaranteed rejection, so
    advertising ``answer`` would be a lie a script would act on. Declining stays
    available: it carries no value, so nothing sensitive transits.
    """
    answerable = bool(questions) and not any(q["secret"] for q in questions)
    return _with_raw({
        "request_id": pending.request_id,
        "created_at": created_at_iso(pending.created_at),
        "age_seconds": age_seconds(pending.created_at),
        "kind": "question",
        "tool_name": pending.tool_name,
        "questions": questions,
        "actions": [ANSWER_ACTION, CANCEL_ACTION] if answerable else [CANCEL_ACTION],
    }, pending, raw=raw)


def normalized_options(raw_options) -> list[dict]:
    """``options`` is typed ``array | null`` on both sides; a question may hold none."""
    if not isinstance(raw_options, list):
        return []
    return [
        {"label": o.get("label", ""), "description": o.get("description", "")}
        for o in raw_options if isinstance(o, dict)
    ]
