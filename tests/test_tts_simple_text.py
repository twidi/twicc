import time

import pytest

from twicc.tts.simple_text import direct_text

SIMPLE = [
    ("Hello there.", "Hello there."),
    ("This is **bold**, *italic* and ~~gone~~ text.", "This is bold, italic and gone text."),
    ("This is __bold__ and _italic_.", "This is bold and italic."),
    ("# Title\n\nSome words.", "Title\n\nSome words."),
    ("> quoted line", "quoted line"),
    ("See [the docs](https://example.org/a) now.", "See the docs now."),
    ("- one\n- two\n- three", "one\ntwo\nthree"),
    ("1. a\n2. b", "a\nb"),
    ("a | b is not a table", "a | b is not a table"),
    ("in 2024. then", "in 2024. then"),
    ("well - maybe", "well - maybe"),
    ("and/or", "and/or"),
    ("snake_case_word and 2 * 3 * 4", "snake_case_word and 2 * 3 * 4"),
    ("*italic* start", "italic start"),
    ("\\*not italic\\*", "*not italic*"),
    ("first\n\n---\n\nsecond", "first\n\nsecond"),
    ("1234567 and deadbeef", "1234567 and deadbeef"),
    ("- a\n\n- b\n\n- c\n\n- d\n\n- e", "a\n\nb\n\nc\n\nd\n\ne"),
    ("- " + "x" * 149 + "\n- y", "x" * 149 + "\ny"),
    ("- a\n- b\n- c\n- d\n- e", "a\nb\nc\nd\ne"),
    ("1999. Was a good year", "1999. Was a good year"),
    ("1999) Was a good year", "1999) Was a good year"),
    # Undecidable against ordinary prose: a single slash between two words stays simple.
    ("Open src/main and TCP/IP", "Open src/main and TCP/IP"),
]

COMPLEX = [
    "| a | b |\n|---|---|\n| 1 | 2 |",
    "a | b\n--- | ---\n1 | 2",
    "| a | b |",
    "```\ncode\n```",
    "```mermaid\ngraph TD\n```",
    "~~~\ncode\n~~~",
    "Use `x` here.",
    "Go to https://x.org/a now.",
    "Go to www.x.org now.",
    "[https://x.org](https://x.org)",
    "Open src/twicc/tts/views.py now.",
    "Open /home/me/a.txt now.",
    "Open ./x now.",
    "Open ~/x now.",
    "Open a/b.py now.",
    "Commit 3fa9c1d done.",
    "Id 123e4567-e89b-12d3-a456-426614174000 here.",
    "Number 12345678 here.",
    "- 1\n- 2\n- 3\n- 4\n- 5\n- 6",
    "- " + "x" * 150 + "\n- y",
    "- a\n  - b",
    "1. a\n   1. b",
    "Some <div>html</div>",
    "Some $$x$$ math",
    "Note[^1]",
    "Title\n=====",
    "    indented code",
    "- a\n\n- b\n\n- c\n\n- d\n\n- e\n\n- f",
    "[a [b] c](x)",
    "[a](foo(1).html)",
    "- [ ] todo",
    "- [x] done",
    "See [x][ref]",
    "[x]: https_not_needed",
    "## T ##",
    "---\ntitle: x\n---\nBody",
    "Fish &amp; chips",
    "Fish &#38; chips",
    "Get ftp://x now",
    "Write mailto:a@b.com now",
    "Go to example.com/path now",
    "Open C:\\x now",
    "Open C:/x now",
    "Value 0x1F3A9B2C here",
    "Open foo.py now",
    "Read README.md now",
    "Open a.h now",
]


@pytest.mark.parametrize(("text", "expected"), SIMPLE)
def test_simple_text_is_cleaned(text, expected):
    assert direct_text(text) == expected


@pytest.mark.parametrize("text", COMPLEX)
def test_complex_text_goes_to_the_model(text):
    assert direct_text(text) is None


def test_the_url_of_a_link_is_not_a_bare_url():
    assert direct_text("[docs](https://example.org/a)") == "docs"


def test_thematic_break_is_removed():
    assert "---" not in direct_text("first\n\n---\n\nsecond")


def test_digit_boundary():
    assert direct_text("1234567") == "1234567"
    assert direct_text("12345678") is None


def test_file_origin_is_always_complex():
    assert direct_text("Hello.", "file") is None


def test_rendered_origin():
    assert direct_text("a\nb", "rendered") is None
    assert direct_text("a\tb", "rendered") is None
    assert direct_text("Some **plain** text.", "rendered") == "Some plain text."
    # Accepted limit: a one-line rendered table cell shows no table syntax, so it reads as prose.
    assert direct_text("42", "rendered") == "42"


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "---", "|", "", "\\", "[", "<", "* * *"])
def test_never_raises(text):
    result = direct_text(text)
    assert result is None or isinstance(result, str)


@pytest.mark.parametrize("text", ["[](x)", "1.", "**", "- "])
def test_cleaned_to_nothing(text):
    result = direct_text(text)
    assert result is not None
    assert not any(c.isalnum() for c in result)


@pytest.mark.parametrize(
    "text",
    ["word " * 20_000, "* " * 50_000, "__x " * 25_000, "[" * 100_000, "~~a " * 25_000, "a/" * 50_000, "-" * 100_000, " " * 100_000 + "x"],
    ids=["prose", "stars", "underscores", "brackets", "tildes", "slashes", "dashes", "spaces"],
)
def test_linear_on_a_big_text(text):
    started = time.perf_counter()
    direct_text(text)
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize("mark", ["*a ", "**a ", "_a ", "__a ", "~~a ", "[a ", "***a "])
def test_many_long_lines_of_unclosed_marks_stay_fast(mark):
    text = "\n".join([mark * (2000 // len(mark))] * 50)
    started = time.perf_counter()
    direct_text(text)
    assert time.perf_counter() - started < 0.2
