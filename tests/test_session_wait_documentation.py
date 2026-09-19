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
    # `secondary_opts` too: the day one of these turns into `--x/--no-x`,
    # documenting `--no-x` must not read as a flag the command refuses.
    return {opt for param in _command().params for opt in (*param.opts, *param.secondary_opts)}


def _real_default(name: str):
    return next(param.default for param in _command().params if param.name == name)


def _real_help(name: str) -> str:
    return next(param.help for param in _command().params if param.name == name)


def _section(numbered: list[tuple[int, str]], heading_prefix: str) -> list[tuple[int, str]]:
    """One section, ending at the next heading of its level or above.

    Naming the *following* section instead would make inserting a sibling
    swallow it: the `wait` prose would inherit `stop`'s `--timeout`, and the
    shortest repair is to add that flag to the allowlist — which disarms the
    check for a real one. `#{1,N}`, not `#` * N, for the mirror case: a
    shallower heading closes a section just as a sibling does, and matching
    only its own level let moving the section to the end of its parent report
    a dozen flags of other sub-commands as flags that do not exist.
    """
    boundary = re.compile(rf"^#{{1,{len(heading_prefix.split(' ')[0])}}} ")
    start = next((i for i, (_, line) in enumerate(numbered) if line.startswith(heading_prefix)), None)
    # A heading someone renamed, reported as itself: the bare lookup raised
    # `StopIteration` from deep inside a generator and named nothing.
    assert start is not None, heading_prefix

    # A fenced block is not markdown. `# → {"session_id": …}` — the output
    # comment these examples end on — reads as a heading, and closed the `wait`
    # section seven lines early: the sentence that names `--wait-timeout` fell
    # outside, and a wrong flag written there stopped being seen at all.
    fenced, end = False, len(numbered)
    for i, (_, line) in enumerate(numbered[start + 1:], start + 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and boundary.match(line):
            end = i
            break
    return numbered[start:end]


def _copyable_spans() -> list[tuple[str, int, str]]:
    """Every invocation or signature of this command a reader could copy.

    A flag lives in a code span (`` `--from` ``) just as an invocation does, so
    the span has to be recognised as an invocation rather than the line holding
    it: the three sending skills put theirs mid-paragraph, next to prose that
    names `--wait-reply` on purpose.
    """
    # `processes wait` is another command with flags of its own, documented in
    # the same bullet shape a few sections down the CLI reference. An
    # invocation names the session, so it identifies itself anywhere; a bare
    # `wait [--…` only counts inside this command's own section.
    cli_section = {number for number, _ in _session_section_of_the_cli_doc()}

    found = []
    for path in [*sorted(SKILLS_DIR.glob("*/SKILL.md")), CLI_DOC]:
        label = f"{path.parent.name}/{path.name}" if path != CLI_DOC else path.name
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            spans = CODE_SPAN.findall(stripped)
            if stripped.startswith("$TWICC"):  # a fenced block carries no backticks
                spans.append(stripped)
            own_section = path == CLI_DOC and number in cli_section
            for span in spans:
                if INVOCATION.search(span) or (own_section and span.startswith("wait [--")):
                    found.append((label, number, span))
    return found


def _is_full_signature(label: str, span: str) -> bool:
    """The two spans that claim to list every option, anchored rather than guessed.

    Inferring it from "more than one bracket group" made the shape of a
    sentence decide: the skill's summary bullet shows `[--from N]` alone on
    purpose, and giving it a second bracket turned it into a signature that
    then had to grow the other three.
    """
    if "[--" not in span:
        return False
    return span.startswith("$TWICC session") or (label == "SKILLS-AND-CLI.md" and span.startswith("wait [--"))


def _session_section_of_the_cli_doc() -> list[tuple[int, str]]:
    return _section(_numbered(CLI_DOC), "### `twicc session <SESSION_ID> <SUBCOMMAND>`")


def _numbered(path: Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def _prose_sources() -> list[tuple[str, list[tuple[int, str]]]]:
    """The blocks that describe the command, where a neighbour may be named.

    Lines keep their number so a failure names the line to open. A ``help=``
    string has no line, and carries 0: its label already points at it.
    """
    skill = _numbered(SESSION_SKILL)
    cli_section = _session_section_of_the_cli_doc()

    sources = [
        (
            "twicc-session/SKILL.md",
            [(number, line) for number, line in skill if line.startswith("- `wait ")]
            + _section(skill, "### Wait — "),
        ),
        ("SKILLS-AND-CLI.md", [(n, line) for n, line in cli_section if line.startswith("- `wait ")]),
        ("session wait --help", [(0, _command().help or "")]),
    ]
    for param in _command().params:
        if param.help:
            sources.append((f"{param.opts[0]} help", [(0, param.help)]))
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
        if _is_full_signature(label, span)
    ]
    # The skill's fenced one and the CLI reference's bullet. A floor, not an
    # equality: counting them made a summary bullet gaining a second bracket
    # read as a full signature, and the shortest repair was to bump the number.
    assert len(signatures) >= 2, signatures

    for label, number, span in signatures:
        assert {flag for group in BRACKET_GROUP.findall(span) for flag in FLAG.findall(group)} == real, (
            label, number, span,
        )


def test_the_prose_names_no_flag_that_does_not_exist():
    real = _real_options() | CROSS_REFERENCES
    wrong = [
        (label, number, flag)
        for label, lines in _prose_sources()
        for number, line in lines
        for flag in FLAG.findall(line)
        if flag not in real
    ]
    assert wrong == []


def test_every_mention_of_a_neighbour_s_flag_is_a_sanctioned_one():
    counted, where = {}, []
    for label, lines in _prose_sources():
        found = [
            (number, flag)
            for number, line in lines
            for flag in FLAG.findall(line)
            if flag in CROSS_REFERENCES
        ]
        if found:
            counted[label] = {flag: [f for _, f in found].count(flag) for flag in CROSS_REFERENCES}
            where += [(label, number, flag) for number, flag in found]
    # The count says a mention appeared; `where` says which line to open. This
    # is the test that fires on the recurring defect, so it is the one that
    # must not answer "somewhere in these thirty-one lines".
    assert counted == SANCTIONED, where


def test_the_documented_numbers_are_read_from_the_code():
    """Two constants the prose quotes, and a code-only change would strand."""
    from twicc.cli import session as cli_session
    from twicc.cli._wait_reply import AGENT_FLUSH_SECONDS

    default = _real_default("wait_timeout")
    assert f"`--wait-timeout` (default {default:.0f} s)" in SESSION_SKILL.read_text(encoding="utf-8")
    assert f"Default {default:.0f}," in _real_help("wait_timeout")

    # The flush window is quoted in four places and named in none of them, so
    # halving the constant leaves every one of them wrong and nothing red.
    # Counting the mentions rather than pinning how many there are: a fifth
    # sentence is an edit, a fifth sentence quoting a stale number is a defect.
    window = f"~{AGENT_FLUSH_SECONDS:.0f} s flush window"
    for label, text in (
        ("skill", SESSION_SKILL.read_text(encoding="utf-8")),
        ("CLI reference", CLI_DOC.read_text(encoding="utf-8")),
        ("docstring", cli_session.wait.__doc__),
    ):
        assert text.count("flush window") == text.count(window) >= 1, label


def _refusal_flags(source: str | None = None) -> set[str]:
    """Every flag the command refuses on, read off its own `emit_error` calls.

    Derived rather than listed: a pinned set would be bumped to match whatever
    the code does, which is how the enumeration fell behind twice already.

    Parsed rather than grepped. A regex spanning `emit_error(` to `code=1`
    missed a refusal that takes the default code, one written `code = 1`, one
    passing its message by keyword, and one moved into a helper — while
    swallowing an unrelated `code=4` call sitting in between. Following the
    calls from `wait` costs four lines and answers all five.
    """
    import ast
    import inspect

    from twicc.cli import session as cli_session

    module = ast.parse(inspect.getsource(cli_session) if source is None else source)
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}

    flags: set[str] = set()
    seen: set[str] = set()
    stack = ["wait"]
    while stack:
        name = stack.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "emit_error":
                stack.append(node.func.id)
                continue
            code = next((kw.value for kw in node.keywords if kw.arg == "code"), None)
            # Omitted means 1: `emit_error`'s own default. A `code` this cannot
            # read — a name, an expression — counts as a refusal rather than
            # not: an unknown has to fail loudly, not disappear.
            if isinstance(code, ast.Constant) and code.value != 1:
                continue
            for part in [*node.args, *(kw.value for kw in node.keywords if kw.arg != "code")]:
                flags |= set(FLAG.findall(ast.unparse(part)))
    return flags


def _refusal_clauses() -> dict[str, str]:
    """The sentence each document uses to list them, and only that sentence.

    Scoped to the sentence rather than the line it sits on: the docstring is
    one "line" and the skill's is a paragraph, so a flag dropped from the list
    would still be found further along and the check would pass.
    """
    clauses = {}
    for label, lines in _prose_sources():
        for _, line in lines:
            if "local refusal" in line:
                # Any `.` ends it, so a decimal or an "i.e." before the flags
                # would cut the clause short. That direction only ever fails
                # loudly; the repair is to reword, not to loosen this.
                clauses[label] = line[line.index("local refusal"):].split(".", 1)[0]
                break
    return clauses


HELPER_REFUSAL = """
from twicc.cli._output import emit_error


def wait(session_id, *, since=None):
    _refuse_it()


def _refuse_it():
    emit_error("Error: --only-in-a-helper is not allowed.", code=1)
"""

INDIRECT_REFUSAL = HELPER_REFUSAL.replace("_refuse_it()", "globals()['_refuse_it']()", 1)


def test_the_extraction_follows_a_call_out_of_wait():
    """Pinned on a module of its own, because nothing in the real one can say it.

    Every flag `_parse_instant` refuses on, `wait` also names — the two cursors
    passed together — so following the call currently adds nothing observable,
    and an assertion over the real module passes whether the walk descends or
    not. The shape is not hypothetical: `_get_session`'s own refusal already
    lives in a helper.
    """
    assert "--only-in-a-helper" in _refusal_flags(HELPER_REFUSAL)


def test_and_stops_at_a_call_it_cannot_read():
    """The limit, stated rather than discovered: the walk follows a plain name.

    Reached through anything else — a dict, an attribute, a variable — the
    callee is invisible and its refusal is lost. Nothing in this CLI is
    written that way; this says what would have to change if it were.
    """
    assert "--only-in-a-helper" not in _refusal_flags(INDIRECT_REFUSAL)


def test_the_exit_code_enumerations_name_every_refusal():
    """The three documents list the exit-1 refusals as a closed set.

    `--since` added two, and all three lists kept the old three — the second
    time in this loop that a new flag left them stale. The flag guard cannot
    see it: it reads names, not sentences.
    """
    flags = _refusal_flags()
    assert flags, "no `emit_error(..., code=1)` found — the extraction broke"

    for label, clause in _refusal_clauses().items():
        missing = sorted(flag for flag in flags if flag not in clause)
        assert missing == [], (label, missing, clause)


def test_all_three_documents_carry_that_clause():
    """The loop above skips a source that has no clause, so something has to
    say the clause is still there at all."""
    assert set(_refusal_clauses()) == {
        "twicc-session/SKILL.md", "SKILLS-AND-CLI.md", "session wait --help",
    }


# The rule itself, not just the flag names. It has now fallen behind the code
# twice — once per rewrite of `_cursor_at` — and both times every document
# still described a cursor the command had stopped returning.
# Both halves: where the boundary is, and that an untimed line is not one.
# The second half is the regression `63575678` shipped, so a document keeping
# the first and dropping the second describes a cursor that once existed.
RULE = ("strictly after", "not a boundary")
RETIRED = ("at or before that moment", "select the same lines", "mean the same thing")


RULE_SOURCES = ("twicc-session/SKILL.md", "SKILLS-AND-CLI.md", "session wait --help", "--since help")


def _rule_sources() -> dict[str, str]:
    """The four places that state how the instant becomes a cursor.

    Named, not detected. Selecting whatever mentions `--since` pulled in a
    neighbour's help the moment it gained a "mutually exclusive with --since"
    line — a good edit — and the shortest repair was to widen the expected
    set, which then demands the rule in a help that has no business stating
    it. Nothing is lost: a document that stops stating the rule fails on the
    anchors below.
    """
    sources = {label: "\n".join(line for _, line in lines) for label, lines in _prose_sources()}
    missing = [label for label in RULE_SOURCES if label not in sources]
    assert missing == [], missing
    return {label: sources[label] for label in RULE_SOURCES}


def test_every_document_states_the_rule_the_code_implements():
    """Substring presence, not meaning: this catches a document left behind by
    a rewrite, not one whose author writes a new sentence that is wrong."""
    missing = sorted(
        (label, anchor)
        for label, text in _rule_sources().items()
        for anchor in RULE
        if anchor not in text
    )
    assert missing == []


def test_no_document_still_states_the_rule_that_lost_lines():
    """`--from N` and `--since T` are not interchangeable, and saying so was
    the claim both rewrites invalidated."""
    wrong = [
        (label, phrase)
        for label, text in _rule_sources().items()
        for phrase in RETIRED
        if phrase in text
    ]
    assert wrong == []
