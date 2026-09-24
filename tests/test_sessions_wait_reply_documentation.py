"""What the documents publish about ``sessions wait-reply``, against Typer.

The singular has its own guard, and it exists because that prose drifted from
the code twice. Its `INVOCATION` regex is anchored on ``session <ID>
wait-reply`` and cannot match the plural, so every flag the plural documents
was checked by nothing — which is how a sentence saying "the listing's
visibility switches do not apply" shipped one clause after an enumeration
listing `--only-hidden`, one of them, which does apply.

Two things are pinned here. Every flag the documents show is one the command
has; and the filter enumeration — the sentence a caller reads to know what
narrows the batch — names exactly the filter options, no more and no fewer.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "src/twicc/agent/plugin/twicc/skills"
SESSIONS_WAIT_DOC = SKILLS_DIR / "twicc-sessions/wait-reply.md"
CLI_DOC = ROOT / "SKILLS-AND-CLI.md"

FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
INVOCATION = re.compile(r"sessions\s+wait-reply(?![\w-])")
CODE_SPAN = re.compile(r"`([^`]+)`")

#: Flags of neighbouring commands, legitimate in prose that points at them.
#: `--include-hidden` and `--include-archived` are deliberately NOT here: the
#: corrected prose now discusses them by name, which is exactly where the next
#: copyable example naming a flag this command refuses would come from.
CROSS_REFERENCES = {"--from", "--wait-reply"}

#: Everything that shapes the wait rather than narrowing it. The filters are
#: what is left, so a new option must be classified here or the documents are
#: required to name it — a hand-written filter list is a pin, and a pin gets
#: bumped to match whatever the code does.
WAIT_OPTIONS = {
    "--since", "--wait-timeout", "--wait-first", "--wait-all", "--no-reply-text",
}

#: Legitimate in *prose*, never in something copyable: the documents name the
#: two switches this command drops precisely to say they do not apply here.
NAMED_IN_PROSE = CROSS_REFERENCES | {"--include-hidden", "--include-archived"}


def _command():
    from twicc.cli import app

    return typer.main.get_command(app).commands["sessions"].commands["wait-reply"]


def _real_options() -> set[str]:
    return {
        opt
        for param in _command().params
        for opt in (*param.opts, *param.secondary_opts)
        if opt.startswith("--")
    }


def _filters() -> set[str]:
    """The options that narrow the batch: everything that is not a wait knob.

    Derived by subtraction, so adding a filter to the command and naming it in
    no document fails here — which a list written out by hand cannot do.
    """
    unknown = WAIT_OPTIONS - _real_options()
    assert unknown == set(), unknown  # a renamed knob must not silently become a filter
    return _real_options() - WAIT_OPTIONS


def _copyable_spans() -> list[tuple[str, int, str]]:
    """Every invocation or signature of this command a reader could copy."""
    found = []
    # Every markdown file of every skill: a skill may document a sub-command in
    # its own file next to its `SKILL.md`.
    for path in [*sorted(SKILLS_DIR.rglob("*.md")), CLI_DOC]:
        label = path.name if path == CLI_DOC else path.relative_to(SKILLS_DIR).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            spans = CODE_SPAN.findall(stripped)
            if stripped.startswith("$TWICC"):  # a fenced block carries no backticks
                spans.append(stripped)
            found += [(label, number, s) for s in spans if INVOCATION.search(s)]
    return found


def _filter_sentences() -> dict[str, str]:
    """The sentence each surface uses to enumerate what narrows the batch.

    Anchored on "Filters:" in the two documents and on the docstring's own
    wording, because that sentence is the contract — not the flags scattered
    through the prose around it.
    """
    docstring = _command().help or ""
    start = docstring.index("Selection takes the listing's filters")
    sentences = {
        "sessions wait-reply --help": docstring[start:docstring.index(".", start)],
    }
    for label, path in (("twicc-sessions/wait-reply.md", SESSIONS_WAIT_DOC),
                        ("SKILLS-AND-CLI.md", CLI_DOC)):
        text = path.read_text(encoding="utf-8")
        i = text.index("Filters:")
        sentences[label] = text[i:text.index(".", i)]
    return sentences


def test_every_flag_the_documents_show_is_one_the_command_has():
    real = _real_options()
    spans = _copyable_spans()

    # An extraction that selects nothing passes everything below it.
    published_by = {label for label, _, _ in spans}
    assert published_by >= {"twicc-sessions/wait-reply.md", "SKILLS-AND-CLI.md"}, published_by

    wrong = [
        (label, number, flag, span)
        for label, number, span in spans
        for flag in FLAG.findall(span)
        if flag not in real and flag not in CROSS_REFERENCES
    ]
    assert wrong == []


def test_the_full_signature_shows_exactly_the_wait_options():
    """The line a reader copies wholesale. The filters stay out of it — there
    are eleven, and the signature says `[FILTERS]` instead."""
    signatures = [
        (label, number, span) for label, number, span in _copyable_spans()
        if "[--since" in span
    ]
    assert len(signatures) == 2, signatures  # the skill's and the CLI reference's

    for label, number, span in signatures:
        shown = set(FLAG.findall(span))
        assert shown == {
            "--since", "--wait-first", "--wait-all", "--wait-timeout", "--no-reply-text",
        }, (label, number, shown)


def test_every_surface_enumerates_exactly_the_filters():
    """The sentence a caller reads to know what narrows the batch.

    Derived from the command, not listed here: a missing one sends them to a
    workaround for something that works, and an invented one to `No such
    option`. Both shipped in the first two rounds of this command's review.
    """
    filters = _filters()
    assert filters, _real_options()

    for label, sentence in _filter_sentences().items():
        assert set(FLAG.findall(sentence)) == filters, (label, sentence)


def test_the_help_strings_name_no_flag_the_command_lacks():
    """The docstring is the MCP tool description and each `help=` is a
    parameter description, so an agent reads them with no document at hand.

    They were outside this guard, which is where the plural's `--since` help
    came to describe the cursor rule the code deliberately rejects.
    """
    real = _real_options() | NAMED_IN_PROSE
    command = _command()
    texts = [("docstring", command.help or "")]
    texts += [(param.opts[0], param.help) for param in command.params if param.help]

    wrong = [
        (label, flag) for label, text in texts
        for flag in FLAG.findall(text)
        if flag not in real
    ]
    assert wrong == []


def test_the_two_commands_describe_one_cursor_rule():
    """`--since` is translated by the same `_cursor_at` for both, so the two
    help strings cannot say different things about it. One said "the last line
    stamped at or before the instant", which is the rule that function rejects
    by name — and with 121 249 adjacent inversions in this machine's database,
    the difference is reachable.
    """
    from twicc.cli import app

    root = typer.main.get_command(app)
    # Keyed by label, not by `.name`: both are called `wait-reply`, so a dict
    # over the commands collapses to one entry and the singular — already
    # pinned by the other guard — silently stands in for the plural. That is
    # the half this test exists for, and it was checking neither.
    commands = (
        ("sessions wait-reply", root.commands["sessions"].commands["wait-reply"]),
        ("session wait-reply", root.commands["session"].commands["wait-reply"]),
    )

    for label, command in commands:
        text = next(p.help for p in command.params if p.name == "since")
        assert "strictly after" in text, label
        assert "at or before" not in text, label
