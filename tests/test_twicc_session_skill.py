"""Everything this repo publishes about ``session <ID> wait``, checked against Typer.

Prose is the only thing between the command and an agent, and nothing looked at
it. The gap is not theoretical, and it has two shapes, both already shipped:

* Renaming ``--timeout`` to ``--wait-timeout`` updated both signature lines and
  left two worked examples behind. The suite stayed green, the plugin shipped,
  and an agent copying the example got ``No such option`` and exit 2.
* ``--wait-blocked`` — a real flag, but of *another* command — was written as
  this one's in four places. It is refused here; the flag is ``--blocked``.

So a name being real somewhere is not enough: the copyable spans accept nothing
but this command's own options, and the prose that may legitimately mention a
neighbour's flag has each such mention counted. A cross-reference that appears
where there was none is the second defect happening again, and the count is
what says so.

Five documents, not one: the four skills that publish an invocation, this
repo's CLI reference, and the Typer command itself — whose docstring is the MCP
tool description and whose ``help=`` strings are the MCP parameter descriptions.
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
# `session <ID> wait …`, an invocation of this command. `\S+` is the id, which
# the documents write a dozen ways (`<SESSION_ID>`, `'<SESSION_ID>'`, `4a8…`).
INVOCATION = re.compile(r"session\s+\S+\s+wait(?![\w-])")
CODE_SPAN = re.compile(r"`([^`]+)`")
BRACKET_GROUP = re.compile(r"\[(--[^\]]*)\]")

# Flags of the commands that send, and the marker of the retired `process wait`.
# Legitimate in prose that points at those commands, never in an invocation of
# this one.
CROSS_REFERENCES = {"--wait-reply", "--wait-blocked", "--transition"}

# Every sanctioned mention of a neighbour's flag, per prose source. Raising one
# of these is a deliberate act: re-read the sentence first, because the last
# three times the number went up it was `--blocked` written `--wait-blocked`.
SANCTIONED = {
    "twicc-session/SKILL.md": {"--wait-reply": 3, "--wait-blocked": 1, "--transition": 0},
    "SKILLS-AND-CLI.md": {"--wait-reply": 2, "--wait-blocked": 1, "--transition": 1},
    "session wait --help": {"--wait-reply": 2, "--wait-blocked": 1, "--transition": 0},
    "--reply help": {"--wait-reply": 1, "--wait-blocked": 0, "--transition": 0},
    "--blocked help": {"--wait-reply": 0, "--wait-blocked": 1, "--transition": 0},
}


def _command():
    from twicc.cli import app

    return typer.main.get_command(app).commands["session"].commands["wait"]


def _real_options() -> set[str]:
    return {opt for param in _command().params for opt in param.opts}


def _real_default(name: str):
    return next(param.default for param in _command().params if param.name == name)


def _section(lines: list[str], heading_prefix: str, stop_prefix: str) -> list[str]:
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading_prefix)),
        None,
    )
    # A heading someone renamed, reported as itself: the bare lookup raised
    # `StopIteration` from deep inside a generator and named nothing.
    assert start is not None, heading_prefix
    end = next(
        (i for i, line in enumerate(lines[start + 1:], start + 1) if line.startswith(stop_prefix)),
        len(lines),
    )
    return lines[start:end]


def _copyable_spans() -> list[tuple[str, int, str]]:
    """Every invocation or signature of this command a reader could copy.

    A flag lives in a code span (`` `--from` ``) just as an invocation does, so
    the span has to be recognised as an invocation rather than the line holding
    it: the three sending skills put theirs mid-paragraph, next to prose that
    names `--wait-reply` on purpose.
    """
    found = []
    for path in [*sorted(SKILLS_DIR.glob("*/SKILL.md")), CLI_DOC]:
        label = f"{path.parent.name}/{path.name}" if path != CLI_DOC else path.name
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            spans = CODE_SPAN.findall(stripped)
            if stripped.startswith("$TWICC"):  # a fenced block carries no backticks
                spans.append(stripped)
            for span in spans:
                if INVOCATION.search(span) or span.startswith("wait ["):
                    found.append((label, number, span))
    return found


def _prose_sources() -> list[tuple[str, str]]:
    """The blocks that describe the command, where a neighbour may be named."""
    skill = SESSION_SKILL.read_text().splitlines()
    cli = CLI_DOC.read_text().splitlines()
    cli_section = _section(cli, "### `twicc session <SESSION_ID> <SUBCOMMAND>`", "### `twicc search")

    sources = [
        (
            "twicc-session/SKILL.md",
            "\n".join(
                [line for line in skill if line.startswith("- `wait ")]
                + _section(skill, "### Wait — ", "### Agents")
            ),
        ),
        ("SKILLS-AND-CLI.md", "\n".join(line for line in cli_section if line.startswith("- `wait "))),
        ("session wait --help", _command().help or ""),
    ]
    for param in _command().params:
        if param.help:
            sources.append((f"{param.opts[0]} help", param.help))
    return sources


def test_a_copyable_invocation_uses_nothing_but_this_command_s_options():
    real = _real_options()
    spans = _copyable_spans()

    published_by = {label for label, _, _ in spans}
    # An extraction that selects nothing passes every assertion below it. These
    # five documents publish an invocation today; losing one is a finding, not
    # a green run.
    assert published_by >= {
        "twicc-session/SKILL.md", "twicc-create-session/SKILL.md",
        "twicc-send-message/SKILL.md", "twicc-send-messages/SKILL.md",
        "SKILLS-AND-CLI.md",
    }, published_by

    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real
    ]
    assert wrong == []


def test_a_full_signature_shows_exactly_the_real_options():
    """The line a reader copies wholesale, pinned both ways.

    Union over a whole document is too loose for it: a flag dropped from the
    signature stays "documented" by a sentence further up, and the copied line
    is the one that runs.
    """
    real = _real_options()
    signatures = [
        (label, number, span)
        for label, number, span in _copyable_spans()
        if len(BRACKET_GROUP.findall(span)) > 1
    ]
    assert len(signatures) == 2, signatures  # the skill's and the CLI reference's

    for label, number, span in signatures:
        assert {flag for group in BRACKET_GROUP.findall(span) for flag in FLAG.findall(group)} == real, (
            label, number, span,
        )


def test_the_prose_names_no_flag_that_does_not_exist():
    real = _real_options() | CROSS_REFERENCES
    wrong = [
        (label, flag)
        for label, text in _prose_sources()
        for flag in FLAG.findall(text)
        if flag not in real
    ]
    assert wrong == []


def test_every_mention_of_a_neighbour_s_flag_is_a_sanctioned_one():
    counted = {
        label: {flag: FLAG.findall(text).count(flag) for flag in CROSS_REFERENCES}
        for label, text in _prose_sources()
        if any(flag in text for flag in CROSS_REFERENCES)
    }
    assert counted == SANCTIONED


def test_both_reference_documents_show_every_option():
    real = _real_options()
    for label, text in _prose_sources():
        if label in ("twicc-session/SKILL.md", "SKILLS-AND-CLI.md"):
            assert real <= set(FLAG.findall(text)), label


def test_the_documented_defaults_are_read_from_the_command():
    default = _real_default("wait_timeout")
    assert f"`--wait-timeout` (default {default:.0f} s)" in SESSION_SKILL.read_text()
    timeout_help = next(p.help for p in _command().params if p.name == "wait_timeout")
    assert f"Default {default:.0f}," in timeout_help
