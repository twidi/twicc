"""What this repo publishes about the two pending-question commands.

A port of the one check that matters from
``tests/test_session_wait_documentation.py``: a copyable invocation names
nothing but that command's own options. That file was written after a shipped
mix-up — ``--wait-blocked``, a real flag of *another* command, documented as
``session <ID> wait``'s in four places — and it is not command-parameterised,
so the check is ported rather than reused.

The rest of that file (the prose cross-reference counts, the full-signature
pinning, the refusal clauses) needs allowlists these commands do not have yet,
and porting it would yield dead code.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "src/twicc/agent/plugin/twicc/skills"
SESSION_SKILL = SKILLS_DIR / "twicc-session/SKILL.md"
CLI_DOC = ROOT / "SKILLS-AND-CLI.md"

FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
CODE_SPAN = re.compile(r"`([^`]+)`")

# One entry per command: how an invocation of it reads, and how its bare
# signature opens in a bullet. ``\S+`` is the session id, which the documents
# write a dozen ways (``<SESSION_ID>``, ``abc123``).
COMMANDS = {
    "pending-request": (
        re.compile(r"session\s+\S+\s+pending-request(?![\w-])"),
        ("pending-request [--", "pending-request ["),
    ),
    "answer": (
        re.compile(r"session\s+\S+\s+answer(?![\w-])"),
        ("answer <answer|cancel>",),
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
    # ``pending-request`` is a group whose callback carries the options; its
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


def _copyable_spans(name: str) -> list[tuple[str, int, str]]:
    """Every invocation or signature of ``name`` a reader could copy.

    A flag lives in a code span just as an invocation does, so the span is what
    gets recognised, not the line holding it.

    A bare signature — the bullet that opens with the sub-command name — only
    counts where the surrounding text already says *which* session command it
    belongs to: the CLI reference's session section, and the ``twicc-session``
    skill, which is about nothing else.
    """
    invocation, signature_starts = COMMANDS[name]
    cli_section = {number for number, _ in _section(
        _numbered(CLI_DOC), "### `twicc session <SESSION_ID> <SUBCOMMAND>`")}

    found = []
    for path in [*sorted(SKILLS_DIR.glob("*/SKILL.md")), CLI_DOC]:
        label = f"{path.parent.name}/{path.name}" if path != CLI_DOC else path.name
        for number, line in _numbered(path):
            stripped = line.strip()
            spans = CODE_SPAN.findall(stripped)
            if stripped.startswith("$TWICC"):  # a fenced block carries no backticks
                spans.append(stripped)
            own_section = path == SESSION_SKILL or (path == CLI_DOC and number in cli_section)
            for span in spans:
                if invocation.search(span) or (
                    own_section and span.startswith(signature_starts)
                ):
                    found.append((label, number, span))
    return found


def test_a_copyable_read_invocation_uses_nothing_but_its_options():
    spans = _copyable_spans("pending-request")
    # An extraction that selects nothing passes every assertion below it.
    assert {label for label, _, _ in spans} >= {
        "twicc-session/SKILL.md", "SKILLS-AND-CLI.md",
    }, spans

    real = _real_options("pending-request")
    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real
    ]
    assert wrong == []


def test_a_copyable_answer_invocation_uses_nothing_but_its_options():
    spans = _copyable_spans("answer")
    assert {label for label, _, _ in spans} >= {
        "twicc-session/SKILL.md", "SKILLS-AND-CLI.md",
    }, spans

    real = _real_options("answer")
    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real
    ]
    assert wrong == []
