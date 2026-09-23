"""Tests for the ``is_final`` flag on extracted messages.

An assistant turn alternates text and tool calls several times before the
reply that closes it. ``IndexableMessage.is_final`` is how a consumer tells
that closing message apart, and both providers already mark it on the line
itself — Claude with ``message.stop_reason``, Codex with the canonical
``AgentMessage.phase``. Nothing here needs a lookahead or a grouping pass.

The second contract locked below is the single deserialization: the flag
rides along with the text extraction on **one** ``orjson.loads`` per item,
which is the whole point of routing both through ``parse_item_content``.
"""

from __future__ import annotations

from datetime import datetime

import orjson
import pytest
import typer
from django.utils import timezone

from twicc.cli import _output
from twicc.cli import session as cli_session
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers.codex.canonical import build_twicc_agent_message
from twicc.providers.helpers import get_provider_helpers


CLAUDE = get_provider_helpers("claude_code")
CODEX = get_provider_helpers("codex")


# --- Line shapes -------------------------------------------------------------


def claude_assistant(stop_reason, *, text="hello", blocks=None):
    """One Claude ``assistant`` JSONL line. ``stop_reason=...`` omits the key."""
    message = {"role": "assistant", "content": blocks if blocks is not None else [
        {"type": "text", "text": text},
    ]}
    if stop_reason is not Ellipsis:
        message["stop_reason"] = stop_reason
    return {"type": "assistant", "message": message}


def claude_user(text="hi"):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": text},
    ]}}


def codex_agent(phase, *, text="hello"):
    """One canonical Codex ``AgentMessage`` line. ``phase=...`` omits the key."""
    item = {"type": "AgentMessage", "id": "item-1", "content": [
        {"type": "Text", "text": text},
    ]}
    if phase is not Ellipsis:
        item["phase"] = phase
    return {"type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "t", "turn_id": "turn-1", "item": item,
    }}


def codex_user(text="hi"):
    return {"type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "t", "turn_id": "turn-1",
        "item": {"type": "UserMessage", "id": "item-0", "content": [
            {"type": "text", "text": text},
        ]},
    }}


# --- Claude Code -------------------------------------------------------------


@pytest.mark.parametrize(("stop_reason", "expected"), [
    # The four values that end a turn: the model has stopped talking,
    # whether it finished, ran out of room, or declined.
    ("end_turn", True),
    ("max_tokens", True),
    ("stop_sequence", True),
    ("refusal", True),
    # The two that leave it open.
    ("tool_use", False),
    ("pause_turn", False),
    # Absent, null, or a value added after this was written.
    (None, None),
    (Ellipsis, None),
])
def test_claude_reads_the_verdict_off_stop_reason(stop_reason, expected):
    assert CLAUDE.is_final_assistant_message(claude_assistant(stop_reason)) is expected


@pytest.mark.parametrize("stop_reason", ["something_new", ""])
def test_claude_reports_unknown_for_a_stop_reason_it_does_not_know(stop_reason):
    """A value in neither set is unknown — never "not final".

    Reading an unrecognised reason as ``False`` would tell a caller "more is
    coming" about a turn that may well have ended.
    """
    assert CLAUDE.is_final_assistant_message(claude_assistant(stop_reason)) is None


def test_claude_split_message_reports_unknown_on_the_lines_without_the_marker():
    """The CLI writes stop_reason on the last line of a split message only.

    Shape taken verbatim from the local corpus, and current: it dominates
    subagent transcripts on today's CLI. The text line is the one a
    messages listing returns, and it must say "unknown" — reading it as
    ``False`` would be a guess that happens to be right here and would be
    wrong on a trailing ``end_turn``.
    """
    text_line = claude_assistant(Ellipsis, blocks=[{"type": "text", "text": "Let me read it."}])
    tool_line = claude_assistant("tool_use", blocks=[{"type": "tool_use", "id": "t1", "name": "Read"}])
    assert CLAUDE.is_final_assistant_message(text_line) is None
    assert CLAUDE.is_final_assistant_message(tool_line) is False


def test_claude_closing_message_keeps_its_marker_when_split():
    """A closing message has no tool call after it, so it carries the value.

    That is what keeps ``True`` trustworthy even where ``None`` is common:
    the thinking line is dropped from the listing (CONTENT_ITEMS, not
    ASSISTANT_MESSAGE) and the text line that remains says ``end_turn``.
    """
    thinking = claude_assistant(Ellipsis, blocks=[{"type": "thinking", "thinking": "hm"}])
    text = claude_assistant("end_turn", blocks=[{"type": "text", "text": "done"}])
    assert CLAUDE.is_final_assistant_message(thinking) is None
    assert CLAUDE.is_final_assistant_message(text) is True


def test_claude_reports_unknown_for_non_assistant_and_malformed_lines():
    assert CLAUDE.is_final_assistant_message(claude_user()) is None
    assert CLAUDE.is_final_assistant_message({"type": "assistant"}) is None
    assert CLAUDE.is_final_assistant_message({"type": "assistant", "message": "oops"}) is None
    assert CLAUDE.is_final_assistant_message({}) is None


# --- Codex -------------------------------------------------------------------


@pytest.mark.parametrize(("phase", "expected"), [
    ("final_answer", True),
    ("commentary", False),
    # Unknown values stay unknown, same rule as Claude's stop_reason.
    ("something_new", None),
    ("", None),
    (None, None),
    (Ellipsis, None),
])
def test_codex_reads_the_verdict_off_the_agent_message_phase(phase, expected):
    assert CODEX.is_final_assistant_message(codex_agent(phase)) is expected


def test_codex_reports_unknown_for_non_agent_messages():
    assert CODEX.is_final_assistant_message(codex_user()) is None
    assert CODEX.is_final_assistant_message({"type": "response_item"}) is None
    assert CODEX.is_final_assistant_message({}) is None


def test_codex_reports_unknown_for_a_twicc_built_agent_message():
    # ``build_twicc_agent_message`` relabels a plan-mode answer into the
    # canonical shape but sets no phase — we must not guess one.
    built = build_twicc_agent_message(
        {"type": "response_item", "timestamp": "2026-09-16T11:29:51Z"},
        session_id="thread-1", line_num=8, text="the plan",
    )
    assert CODEX.is_final_assistant_message(built) is None


# --- Parsing contract --------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"a string"', "null"])
@pytest.mark.parametrize("helpers", [CLAUDE, CODEX], ids=["claude_code", "codex"])
def test_unusable_content_yields_no_message_at_all(helpers, content):
    item = SessionItem(line_num=1, kind=ItemKind.ASSISTANT_MESSAGE, content=content)
    assert helpers.parse_item_content(item) is None
    assert helpers.extract_indexable_text(item) == ""
    assert helpers.get_indexable_messages([item]) == []
    assert helpers.get_user_messages([item]) == []


# --- End to end, through the DB and the CLI ----------------------------------


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-twicc-final", directory="/tmp/twicc-final")


def make_session(project, provider):
    return Session.objects.create(
        id=f"final-{provider}", project=project, provider=provider,
        file_path=f"final-{provider}.jsonl", type=SessionType.SESSION,
        # ``_get_session`` rejects a session with no ``created_at`` or no
        # user message, so both are part of a usable fixture.
        created_at=timezone.now(), mtime=1000, last_line=1, user_message_count=1,
    )


def add_items(session, lines):
    for i, (kind, payload) in enumerate(lines, start=1):
        SessionItem.objects.create(
            session=session, line_num=i, kind=kind,
            content=orjson.dumps(payload).decode(),
        )


# One turn per provider, in JSONL order. The unmarked line in the middle is
# the legacy / TwiCC-built shape: it must surface as ``None``, not ``False``.
CLAUDE_TURN = [
    (ItemKind.USER_MESSAGE, claude_user("do the thing")),
    (ItemKind.ASSISTANT_MESSAGE, claude_assistant("tool_use", text="on it")),
    (ItemKind.ASSISTANT_MESSAGE, claude_assistant("tool_use", text="still on it")),
    (ItemKind.ASSISTANT_MESSAGE, claude_assistant(Ellipsis, text="unmarked")),
    (ItemKind.ASSISTANT_MESSAGE, claude_assistant("end_turn", text="done")),
]

CODEX_TURN = [
    (ItemKind.USER_MESSAGE, codex_user("do the thing")),
    (ItemKind.ASSISTANT_MESSAGE, codex_agent("commentary", text="on it")),
    (ItemKind.ASSISTANT_MESSAGE, codex_agent("commentary", text="still on it")),
    (ItemKind.ASSISTANT_MESSAGE, codex_agent(Ellipsis, text="unmarked")),
    (ItemKind.ASSISTANT_MESSAGE, codex_agent("final_answer", text="done")),
]

EXPECTED_TURN = [
    ("do the thing", None),   # a user message is never a turn's closing one
    ("on it", False),
    ("still on it", False),
    ("unmarked", None),       # marker absent → unknown, not "not final"
    ("done", True),
]

TURNS = [("claude_code", CLAUDE_TURN), ("codex", CODEX_TURN)]

# An item a listing counts but never returns: its kind puts it in the queryset,
# its extraction yields nothing. It is the only thing that makes the two
# windowing branches disagree on ``total``, so the identity test needs it.
EMPTY_EXTRACTION = {
    "claude_code": claude_assistant("tool_use", blocks=[{"type": "thinking", "thinking": "hm"}]),
    "codex": codex_agent("commentary", text="   "),
}


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_a_whole_turn_flags_only_its_closing_message(project, provider, lines):
    session = make_session(project, provider)
    add_items(session, lines)
    items = list(SessionItem.objects.filter(session=session).order_by("line_num"))

    messages = get_provider_helpers(provider).get_indexable_messages(items)

    assert [(m.text, m.is_final) for m in messages] == EXPECTED_TURN


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_each_item_is_deserialized_exactly_once(project, provider, lines, monkeypatch):
    """The refactor's reason to exist: one ``orjson.loads`` per item.

    Every module reaches ``loads`` through the ``orjson`` module object, so
    patching it there counts calls wherever they happen — including a
    provider that would re-parse the same string behind our back.
    """
    session = make_session(project, provider)
    add_items(session, lines)
    items = list(SessionItem.objects.filter(session=session).order_by("line_num"))

    calls = []
    real_loads = orjson.loads
    monkeypatch.setattr(orjson, "loads", lambda *a, **kw: calls.append(1) or real_loads(*a, **kw))
    messages = get_provider_helpers(provider).get_indexable_messages(items)
    monkeypatch.undo()

    assert len(messages) == len(items)
    assert len(calls) == len(items)


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_get_user_messages_still_stops_at_the_limit(project, provider, lines, monkeypatch):
    """``get_user_messages`` was hoisted out of both providers — same contract.

    It feeds the composer history picker and the title suggestion
    (``get_first_user_message`` calls it with ``limit=1``), so the
    short-circuit must still fire before extraction, not after: a session
    with thousands of items must not be fully parsed to answer "give me the
    first user message".
    """
    session = make_session(project, provider)
    add_items(session, lines + lines)  # two turns, so two user messages
    # Callers hand this helper an already-filtered queryset (see
    # ``get_first_user_message`` and ``views.py``'s history picker).
    items = list(
        SessionItem.objects
        .filter(session=session, kind=ItemKind.USER_MESSAGE).order_by("line_num")
    )
    helpers = get_provider_helpers(provider)

    assert [m.text for m in helpers.get_user_messages(items)] == ["do the thing"] * 2

    calls = []
    real_loads = orjson.loads
    monkeypatch.setattr(orjson, "loads", lambda *a, **kw: calls.append(1) or real_loads(*a, **kw))
    first = helpers.get_user_messages(items, limit=1)
    monkeypatch.undo()

    assert [m.text for m in first] == ["do the thing"]
    # One parse for the item it kept, and not one for every remaining item.
    assert len(calls) == 1


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_cli_messages_exposes_final_on_every_entry(project, provider, lines, capsysbinary, monkeypatch):
    session = make_session(project, provider)
    add_items(session, lines)

    # Pinned past ``LISTING_CUTOVER``, where every listing is wrapped in the
    # ``{items, pagination}`` envelope. Naive, like the constant it replaces.
    monkeypatch.setattr(_output, "LISTING_CUTOVER", datetime(2000, 1, 1))  # noqa: DTZ001
    cli_session.messages(session.id)

    entries = orjson.loads(capsysbinary.readouterr().out)["items"]
    assert [(entry["text"], entry["is_final"]) for entry in entries] == EXPECTED_TURN
    assert [entry["role"] for entry in entries] == [
        "user", "assistant", "assistant", "assistant", "assistant",
    ]


# --- The --is-final filter ---------------------------------------------------


def run_messages(session, capsysbinary, **kwargs):
    """Call the CLI and return the listing entries as ``(text, is_final)``."""
    cli_session.messages(session.id, **kwargs)
    payload = orjson.loads(capsysbinary.readouterr().out)
    entries = payload["items"] if isinstance(payload, dict) else payload
    return [(entry["text"], entry["is_final"]) for entry in entries]


@pytest.mark.parametrize(("provider", "lines"), TURNS)
@pytest.mark.parametrize(("tokens", "expected_texts"), [
    # The three values, alone.
    (["true"], ["done"]),
    (["false"], ["on it", "still on it"]),
    (["null"], ["do the thing", "unmarked"]),
    # OR-combined. ``true`` + ``null`` is the recipe for a transcript where the
    # marker is often missing: keep the closing message *and* the candidates.
    (["true", "null"], ["do the thing", "unmarked", "done"]),
    (["true", "false"], ["on it", "still on it", "done"]),
    # A repeated value is the same as one.
    (["true", "true"], ["done"]),
])
def test_is_final_selects_exactly_the_listed_values(
    project, provider, lines, tokens, expected_texts, capsysbinary,
):
    session = make_session(project, provider)
    add_items(session, lines)

    got = run_messages(session, capsysbinary, is_final=tokens)

    assert [text for text, _ in got] == expected_texts


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_without_the_flag_nothing_is_filtered_out(project, provider, lines, capsysbinary):
    """The default must never drop ``null`` — it is unknown, not "not final"."""
    session = make_session(project, provider)
    add_items(session, lines)

    assert run_messages(session, capsysbinary) == EXPECTED_TURN


def run_messages_paginated(session, capsysbinary, **kwargs):
    """Call the CLI with ``--paginated`` and return the raw envelope."""
    cli_session.messages(session.id, paginated=True, **kwargs)
    return orjson.loads(capsysbinary.readouterr().out)


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_asking_for_all_three_values_is_the_identity(project, provider, lines, capsysbinary):
    """All three values must return the no-flag answer, envelope included.

    Not just "the same entries": the vacuous filter has to stay on the
    cheap DB-windowing branch, or its ``total`` would be computed the other
    way and an identity would report different pagination than the call it
    claims to be identical to. The empty-extracting item below is what makes
    the two branches disagree — without it the branches coincide and the
    test would pass on a broken implementation.
    """
    session = make_session(project, provider)
    add_items(session, lines + [(ItemKind.ASSISTANT_MESSAGE, EMPTY_EXTRACTION[provider])])

    plain = run_messages_paginated(session, capsysbinary)
    all_three = run_messages_paginated(
        session, capsysbinary, is_final=["true", "false", "null"],
    )

    assert all_three == plain
    # The raw count includes the item that extracts to nothing; the entries
    # do not. That gap is precisely what the other branch would erase.
    assert plain["pagination"]["total"] == len(lines) + 1
    assert len(plain["items"]) == len(lines)


@pytest.mark.parametrize("token", ["oui", "True", "TRUE", "none", "unknown", ""])
def test_an_unknown_token_is_rejected(project, token, capsys):
    session = make_session(project, "claude_code")
    add_items(session, CLAUDE_TURN)

    with pytest.raises(typer.Exit) as exc:
        cli_session.messages(session.id, is_final=[token])

    assert exc.value.exit_code == 1
    assert f"invalid --is-final '{token}'" in capsys.readouterr().err


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_is_final_stacks_with_role_and_contains(project, provider, lines, capsysbinary):
    """Different flags AND together; only --is-final's own values are OR-ed."""
    session = make_session(project, provider)
    add_items(session, lines)

    # ``null`` alone also matches the user message — the flags are orthogonal,
    # so narrowing to assistants is the caller's job.
    assert run_messages(session, capsysbinary, is_final=["null"]) == [
        ("do the thing", None), ("unmarked", None),
    ]
    assert run_messages(session, capsysbinary, role="assistant", is_final=["null"]) == [
        ("unmarked", None),
    ]
    # AND with --contains: "on it" is in "still on it" too, so the is_final
    # value is what separates them.
    assert run_messages(
        session, capsysbinary, contains=["on it"], is_final=["false"],
    ) == [("on it", False), ("still on it", False)]
    assert run_messages(session, capsysbinary, contains=["done"], is_final=["false"]) == []


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_is_final_applies_before_the_window(project, provider, lines, capsysbinary):
    """--tail counts the matches, not the raw items."""
    session = make_session(project, provider)
    add_items(session, lines)

    assert run_messages(session, capsysbinary, is_final=["false"], tail=1) == [
        ("still on it", False),
    ]
    # Without the filter the last message is the closing one, not an
    # intermediate: the filter really moved the window.
    assert run_messages(session, capsysbinary, tail=1) == [("done", True)]


@pytest.mark.parametrize(("provider", "lines"), TURNS)
def test_paginated_total_counts_the_matches(project, provider, lines, capsysbinary):
    """A filtering --is-final puts the window on extracted messages.

    ``total`` is then exact — including past the empty-extracting item, which
    the raw-item count of the other branch would have included.
    """
    session = make_session(project, provider)
    add_items(session, lines + [(ItemKind.ASSISTANT_MESSAGE, EMPTY_EXTRACTION[provider])])

    payload = run_messages_paginated(session, capsysbinary, is_final=["false"])

    assert payload["pagination"]["total"] == 2
    assert payload["pagination"]["has_more"] is False
    assert [entry["text"] for entry in payload["items"]] == ["on it", "still on it"]
