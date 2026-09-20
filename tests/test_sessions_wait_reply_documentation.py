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
SESSIONS_SKILL = SKILLS_DIR / "twicc-sessions/SKILL.md"
CLI_DOC = ROOT / "SKILLS-AND-CLI.md"

FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")
INVOCATION = re.compile(r"sessions\s+wait-reply(?![\w-])")
CODE_SPAN = re.compile(r"`([^`]+)`")

#: Flags of neighbouring commands, legitimate in prose that points at them.
CROSS_REFERENCES = {"--from", "--wait-reply", "--include-hidden", "--include-archived"}

#: The options that narrow the batch, as opposed to shaping the wait. This is
#: the list the documents have to match: one missing sends a caller to a
#: workaround, one invented sends them to `No such option`.
FILTERS = {
    "--project", "--workspace", "--provider", "--state", "--active",
    "--only-hidden", "--spawned-by", "--spawn-tree", "--descendants",
    "--annotation", "--siblings",
}


def _command():
    from twicc.cli import app

    return typer.main.get_command(app).commands["sessions"].commands["wait-reply"]


def _real_options() -> set[str]:
    return {
        opt
        for param in _command().params
        for opt in (*param.opts, *param.secondary_opts)
    }


def _copyable_spans() -> list[tuple[str, int, str]]:
    """Every invocation or signature of this command a reader could copy."""
    found = []
    for path in [*sorted(SKILLS_DIR.glob("*/SKILL.md")), CLI_DOC]:
        label = f"{path.parent.name}/{path.name}" if path != CLI_DOC else path.name
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
    for label, path in (("twicc-sessions/SKILL.md", SESSIONS_SKILL),
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
    assert published_by >= {"twicc-sessions/SKILL.md", "SKILLS-AND-CLI.md"}, published_by

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
    assert FILTERS < _real_options()  # the split is still the command's

    for label, sentence in _filter_sentences().items():
        assert set(FLAG.findall(sentence)) == FILTERS, (label, sentence)
