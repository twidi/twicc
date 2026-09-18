"""The two documents that describe ``session <ID> wait``, checked against Typer.

They are the only thing an agent reads before running the command, and nothing
looked at them. The gap is not theoretical: renaming ``--timeout`` to
``--wait-timeout`` updated both signature lines and left two worked examples
behind, the suite stayed green, the plugin shipped, and an agent following the
skill to the letter got ``No such option`` and exit 2. A review round found it
by hand. This test is what finds it next time.

``--wait-reply``, ``--wait-blocked`` and ``--transition`` appear in both
documents on purpose — they name *other* commands, which is the whole point of
the sentences carrying them. Listing them here is how that stays a deliberate
cross-reference rather than a typo nobody can tell apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-session/SKILL.md"
CLI_DOC = ROOT / "SKILLS-AND-CLI.md"

# Flags of the commands that send, and the retired `process wait` marker. They
# are referred to, never offered.
CROSS_REFERENCES = {"--wait-reply", "--wait-blocked", "--transition"}

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
# `session <ID> wait …` — an invocation, not a sentence that says "session" and
# "wait" on its way to describing `create-session --wait-reply`.
INVOCATION = re.compile(r"session\s+\S+\s+wait\b")


def _real_options() -> set[str]:
    from twicc.cli import app

    command = typer.main.get_command(app).commands["session"].commands["wait"]
    return {opt for param in command.params for opt in param.opts}


def _real_default(name: str):
    from twicc.cli import app

    command = typer.main.get_command(app).commands["session"].commands["wait"]
    return next(param.default for param in command.params if param.name == name)


def _cli_doc_section() -> list[tuple[int, str]]:
    """The ``twicc session`` section of SKILLS-AND-CLI.md, and only it.

    ``processes`` has a ``wait`` sub-command too, documented the same way and
    with flags of its own. Cutting the section is what keeps this test from
    reading one command's signature as the other's.
    """
    heading = "### `twicc session <SESSION_ID> <SUBCOMMAND>`"
    lines = CLI_DOC.read_text().splitlines()
    start = lines.index(heading)
    end = next(i for i, line in enumerate(lines[start + 1:], start + 1) if line.startswith("### "))
    return [(number, lines[number - 1]) for number in range(start + 2, end + 1)]


def _wait_lines() -> list[tuple[Path, int, str]]:
    """Every documented line that shows or describes the ``wait`` sub-command."""
    found = []
    for number, line in enumerate(SKILL.read_text().splitlines(), start=1):
        if INVOCATION.search(line):
            found.append((SKILL, number, line.strip()))
    for number, line in _cli_doc_section():
        stripped = line.strip()
        if INVOCATION.search(stripped) or stripped.startswith("- `wait "):
            found.append((CLI_DOC, number, stripped))
    return found


def test_every_flag_the_documents_show_is_a_flag_the_command_has():
    real = _real_options()
    lines = _wait_lines()

    # A rename that empties the extraction would make this test pass on nothing.
    assert len(lines) >= 4, lines

    unknown = [
        (path.name, number, flag, line)
        for path, number, line in lines
        for flag in FLAG.findall(line)
        if flag not in real and flag not in CROSS_REFERENCES
    ]
    assert unknown == []


def test_the_documents_show_every_flag_the_command_has():
    """The other direction: a new option nobody documented is just as wrong.

    Per document, not across both: an agent reads one of them, and a flag that
    only the other one mentions is a flag it does not know about.
    """
    real = _real_options()
    for path in (SKILL, CLI_DOC):
        shown = {
            flag
            for source, _, line in _wait_lines() if source == path
            for flag in FLAG.findall(line)
        }
        assert real <= shown, path.name


def test_the_skill_quotes_the_real_wait_timeout_default():
    assert f"`--wait-timeout` (default {_real_default('wait_timeout'):.0f} s)" in SKILL.read_text()
