"""What this repo publishes about the two pending-question commands.

A port of the one check that matters from
``tests/test_session_wait_documentation.py``: a copyable invocation names
nothing but that command's own options. That file was written after a shipped
mix-up — ``--wait-blocked``, a real flag of *another* command, documented as
``session <ID> wait-reply``'s — then named ``wait`` — in four places — and it is not command-parameterised,
so the check is ported rather than reused.

The rest of that file (the prose cross-reference counts, the full-signature
pinning, the refusal clauses) needs allowlists these commands do not have yet,
and porting it would yield dead code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "src/twicc/agent/plugin/twicc/skills"
QUESTIONS_DOC = SKILLS_DIR / "twicc-session/questions.md"
QUESTIONS_DOC_LABEL = "twicc-session/questions.md"
CLI_DOC = ROOT / "SKILLS-AND-CLI.md"

FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
CODE_SPAN = re.compile(r"`([^`]+)`")

# One entry per command: how an invocation of it reads, and how its bare
# signature opens in a bullet. ``\S+`` is the session id, which the documents
# write a dozen ways (``<SESSION_ID>``, ``abc123``).
COMMANDS = {
    "pending-requests": (
        re.compile(r"session\s+\S+\s+pending-requests(?![\w-])"),
        ("pending-requests [--", "pending-requests ["),
    ),
    "answer-questions": (
        re.compile(r"session\s+\S+\s+answer-questions(?![\w-])"),
        ("answer-questions [--", "answer-questions ["),
    ),
    "cancel-questions": (
        re.compile(r"session\s+\S+\s+cancel-questions(?![\w-])"),
        ("cancel-questions [--", "cancel-questions ["),
    ),
}


def _command(name: str):
    from twicc.cli import app

    return typer.main.get_command(app).commands["session"].commands[name]


def _real_options(name: str) -> set[str]:
    # ``secondary_opts`` too: the day one of these turns into ``--x/--no-x``,
    # documenting ``--no-x`` must not read as a flag the command refuses.
    command = _command(name)
    options = {opt for param in command.params for opt in (*param.opts, *param.secondary_opts)}
    # ``pending-requests`` is a group whose callback carries the options; its
    # click wrapper exposes them on the group itself, so nothing extra is
    # needed — this assertion is what says so.
    assert options, name
    return options


def _section(numbered, heading_prefix: str):
    """One section, ending at the next heading of its level or above.

    A fenced block is not markdown: a ``# comment`` inside one reads as a
    heading and would close the section early.
    """
    boundary = re.compile(rf"^#{{1,{len(heading_prefix.split(' ')[0])}}} ")
    start = next((i for i, (_, line) in enumerate(numbered) if line.startswith(heading_prefix)), None)
    assert start is not None, heading_prefix

    fenced, end = False, len(numbered)
    for i, (_, line) in enumerate(numbered[start + 1:], start + 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and boundary.match(line):
            end = i
            break
    return numbered[start:end]


def _numbered(path: Path):
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def _documents() -> list[Path]:
    """Every markdown file of every skill — sub-command files included — and the CLI reference."""
    return [*sorted(SKILLS_DIR.rglob("*.md")), CLI_DOC]


def _label(path: Path) -> str:
    return path.name if path == CLI_DOC else path.relative_to(SKILLS_DIR).as_posix()


def _copyable_spans(name: str) -> list[tuple[str, int, str]]:
    """Every invocation or signature of ``name`` a reader could copy.

    A flag lives in a code span just as an invocation does, so the span is what
    gets recognised, not the line holding it.

    A bare signature — the bullet that opens with the sub-command name — only
    counts where the surrounding text already says *which* session command it
    belongs to: the CLI reference's session section, and the ``twicc-session``
    skill's ``questions.md``, which documents these three commands and nothing else.
    """
    invocation, signature_starts = COMMANDS[name]
    cli_section = {number for number, _ in _section(
        _numbered(CLI_DOC), "### `twicc session <SESSION_ID> <SUBCOMMAND>`")}

    found = []
    for path in _documents():
        label = _label(path)
        for number, line in _numbered(path):
            stripped = line.strip()
            spans = CODE_SPAN.findall(stripped)
            if stripped.startswith("$TWICC"):  # a fenced block carries no backticks
                spans.append(stripped)
            own_section = path == QUESTIONS_DOC or (path == CLI_DOC and number in cli_section)
            for span in spans:
                if invocation.search(span) or (
                    own_section and span.startswith(signature_starts)
                ):
                    found.append((label, number, span))
    return found


#: Names this feature used and dropped. A rename updates the lines a reader
#: would copy — the guard below sees those — and leaves the ones that merely
#: *mention* a command in a sentence. That is how "answer it with `answer`"
#: survived the rename to `answer-questions`, and how "its only action is
#: `cancel`" survived the one to `cancel-questions`: neither is an invocation,
#: so nothing looked at them. Adding a name here is how a rename stays finished.
RETIRED = (
    "pending-request",
    "answer",
    "cancel",
    "--answer",
    "--choices",
)


def _mentions(name: str) -> list[tuple[str, int, str]]:
    """Every code span in these documents that is exactly ``name``.

    A bare mention, not an invocation: `` `answer` `` in the middle of a
    sentence. Exactness matters — `answer-questions` must not match `answer`.
    """
    found = []
    for path in _documents():
        label = _label(path)
        for number, line in _numbered(path):
            for span in CODE_SPAN.findall(line):
                if span.strip() == name:
                    found.append((label, number, line.strip()))
    return found


@pytest.mark.parametrize("name", RETIRED)
def test_no_document_still_names_a_command_this_feature_dropped(name):
    assert _mentions(name) == []


def test_the_retired_check_can_see_a_name_that_is_there():
    # The mirror of every other assertion here: a check that finds nothing
    # passes whatever the documents say. These three exist, so the extraction
    # is looking where it thinks it is.
    for live in ("pending-requests", "answer-questions", "cancel-questions"):
        assert _mentions(live), live


def test_a_copyable_read_invocation_uses_nothing_but_its_options():
    spans = _copyable_spans("pending-requests")
    # An extraction that selects nothing passes every assertion below it.
    assert {label for label, _, _ in spans} >= {
        QUESTIONS_DOC_LABEL, "SKILLS-AND-CLI.md",
    }, spans

    real = _real_options("pending-requests")
    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real
    ]
    assert wrong == []


@pytest.mark.parametrize("command", ["answer-questions", "cancel-questions"])
def test_a_copyable_write_invocation_uses_nothing_but_its_options(command):
    spans = _copyable_spans(command)
    assert {label for label, _, _ in spans} >= {
        QUESTIONS_DOC_LABEL, "SKILLS-AND-CLI.md",
    }, spans

    real = _real_options(command)
    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real
    ]
    assert wrong == []
