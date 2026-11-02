"""Decide whether a text is simple enough to be read directly, and clean its Markdown.

The thresholds are the spec's (``docs/plans/2026-11-02-tts-read-selection-design.md``). The rules
are instant and conservative: they read the text only, make no network or model call, and never
raise. When a construct is not recognised with certainty, the text is complex and the model
decides.

Every regex is written without nested quantifiers, and the emphasis scan has a work budget, so the
cost stays linear in the text size.

A token with a single slash between two plain words (``src/main``, ``and/or``, ``TCP/IP``) stays
simple: it cannot be told from ordinary prose.
"""

import re

ORIGINS = ("rendered", "file")

# A short list: at most this many items, each under this many characters, no sub-list.
_MAX_LIST_ITEMS = 5
_ITEM_LENGTH_LIMIT = 150
# A line longer than this, with Markdown marks in it, goes to the model (bounds the emphasis scan).
_MAX_MARKUP_LINE = 2000
# Total work (sum of squared lengths of the lines with marks) allowed to the emphasis scan: it
# keeps many long lines of unclosed marks linear.
_MARKUP_WORK_BUDGET = 4_000_000

_PRIVATE_USE = re.compile("[-]")
_ESCAPE = re.compile(r"\\([\\*_~\[\]()#+\-.!|>{}<`])")
_ESCAPE_BACK = re.compile("[-]")
_MARKUP_CHARS = re.compile(r"[*_~\[]")

_LINK = re.compile(r"!?\[([^\[\]\n]*)\]\([^()\n]*\)")
_BACKTICK = "`"
_HTML = re.compile(r"<[A-Za-z/!?]")
_FOOTNOTE = re.compile(r"\[\^")
_URL = re.compile(r"://|mailto:|www\.", re.IGNORECASE)
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_BRACKET = re.compile(r"[\[\]]")
_CLOSING_HASHES = re.compile(r"\s#+$")
_HEX_LITERAL = re.compile(r"(?<![0-9A-Za-z])0[xX][0-9a-fA-F]{6,}")
_DOMAIN_PATH = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/")
_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/]")
_FILE_NAME = re.compile(
    r"[\w-]\.(?:py|js|ts|vue|md|json|yaml|yml|toml|txt|html|css|sh|rs|go|java|cpp|c|h)$", re.IGNORECASE
)
_UUID = re.compile(
    r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9A-Za-z])"
)
_HEX_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{7,}(?![0-9A-Za-z])")
_LONG_DIGITS = re.compile(r"\d{8,}")
_SLASH_BETWEEN_LETTERS = re.compile(r"[^\W\d_]/[^\W\d_]")
_FILE_EXTENSION = re.compile(r"\.[A-Za-z]{1,5}$")
_TOKEN_EDGES = "\"'()[]{},;:!?*_~"

_TABLE_SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?")
_SETEXT_UNDERLINE = re.compile(r"={3,}")
_THEMATIC_BREAK = re.compile(r"([-*_])(?:\s*\1){2,}")
_QUOTE = re.compile(r"\s*(?:>\s?)+")
_HEADING = re.compile(r"#{1,6}\s+")
_LIST_ITEM = re.compile(r"(\s*)(?:[-+*]|\d{1,3}[.)])(?:\s+|$)")

_BOLD_ITALIC = re.compile(r"(?<![*\w])\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*(?![*\w])")
_BOLD_STAR = re.compile(r"(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_BOLD_UNDERSCORE = re.compile(r"(?<![_\w])__(?=\S)(.+?)(?<=\S)__(?![_\w])")
_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")
_ITALIC_UNDERSCORE = re.compile(r"(?<![_\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])")
_STRIKETHROUGH = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
# Symbols that survive the cleanup only in code, commands, paths or markup: read aloud they are
# noise, so the model decides. ``%``, ``;``, ``:``, parentheses, ``+`` and ``-`` are prose.
_LEFTOVER_SYMBOL = re.compile(r"[${}\\|=<>^@*_]|~~|&&|#[^\W_]")
_BLANK_RUN = re.compile(r"\n{3,}")


def _is_path(token: str) -> bool:
    token = token.strip(_TOKEN_EDGES).rstrip(".")
    if len(token) > 1 and token.startswith(("/", "./", "~/", "../")):
        return True
    if _DRIVE_PATH.match(token) or _DOMAIN_PATH.match(token) or _FILE_NAME.search(token):
        return True
    if "/" not in token or not _SLASH_BETWEEN_LETTERS.search(token):
        return False
    return token.count("/") >= 2 or bool(_FILE_EXTENSION.search(token))


def _is_hash(run: str) -> bool:
    return any(c.isdigit() for c in run) and any(c.isalpha() for c in run)


def _has_complex_content(text: str) -> bool:
    """Text-wide constructs the rules refuse (``text`` has its links already reduced to their text)."""
    if (
        _BACKTICK in text
        or "$$" in text
        or _HTML.search(text)
        or _FOOTNOTE.search(text)
        or _URL.search(text)
        or _ENTITY.search(text)
        or _BRACKET.search(text)  # a link, task box or reference the link rule did not reduce
        or _HEX_LITERAL.search(text)
        or _UUID.search(text)
        or _LONG_DIGITS.search(text)
    ):
        return True
    if any(_is_hash(match.group()) for match in _HEX_RUN.finditer(text)):
        return True
    return any(_is_path(token) for token in text.split())


def _clean_emphasis(line: str) -> str:
    if not _MARKUP_CHARS.search(line):
        return line
    line = _STRIKETHROUGH.sub(r"\1", line)
    line = _BOLD_ITALIC.sub(r"\1", line)
    line = _BOLD_STAR.sub(r"\1", line)
    line = _BOLD_UNDERSCORE.sub(r"\1", line)
    line = _ITALIC_STAR.sub(r"\1", line)
    return _ITALIC_UNDERSCORE.sub(r"\1", line)


def _escape(match: re.Match) -> str:
    return chr(0xE000 + ord(match.group(1)))


def _unescape(match: re.Match) -> str:
    return chr(ord(match.group()) - 0xE000)


def _clean(text: str) -> str | None:
    """The cleaned text, or None when it is complex."""
    if _PRIVATE_USE.search(text):
        return None
    text = _LINK.sub(r"\1", text.replace("\r\n", "\n").replace("\r", "\n"))
    if _has_complex_content(text):
        return None

    lines = text.split("\n")
    if lines[0].strip() == "---" and any(line.strip() in ("---", "...") for line in lines[1:]):
        return None  # front matter
    output: list[str] = []
    work = 0
    items = 0
    in_list = False
    item_length = 0
    for raw in lines:
        line = raw.rstrip()
        if _MARKUP_CHARS.search(line):
            work += len(line) ** 2
            if len(line) > _MAX_MARKUP_LINE or work > _MARKUP_WORK_BUDGET:
                return None
        if not line.strip():
            output.append("")  # a blank line does not end a list: a loose list is one list
            continue
        stripped = line.strip()
        if (
            _TABLE_SEPARATOR.fullmatch(stripped)
            or (len(stripped) > 1 and stripped[0] == "|" and stripped[-1] == "|")
            or stripped.startswith("~~~")
            or _SETEXT_UNDERLINE.fullmatch(stripped)
        ):
            return None
        quote = _QUOTE.match(line)
        if quote:
            line = line[quote.end() :]
        stripped = line.strip()
        if not stripped or _THEMATIC_BREAK.fullmatch(stripped):
            in_list = False
            items = 0
            output.append("")
            continue
        heading = _HEADING.match(line)
        item = _LIST_ITEM.match(line)
        if heading:
            if _CLOSING_HASHES.search(line):
                return None
            in_list = False
            items = 0
            line = line[heading.end() :]
        elif item:
            if item.group(1):
                return None  # a sub-list
            items += 1
            in_list = True
            line = line[item.end() :].strip()
            item_length = len(line)
            if items > _MAX_LIST_ITEMS or item_length >= _ITEM_LENGTH_LIMIT:
                return None
        elif in_list and line[0].isspace():
            item_length += len(stripped)  # the wrapped part of an item
            if item_length >= _ITEM_LENGTH_LIMIT:
                return None
            line = stripped
        else:
            in_list = False
            items = 0
            if line.startswith(("    ", "\t")):
                return None  # maybe an indented code block
        line = _ESCAPE.sub(_escape, line)
        output.append(_clean_emphasis(line).rstrip())

    cleaned = _BLANK_RUN.sub("\n\n", "\n".join(output)).strip()
    if _LEFTOVER_SYMBOL.search(cleaned):
        return None
    return _ESCAPE_BACK.sub(_unescape, cleaned)


def direct_text(text: str, origin: str | None = None) -> str | None:
    """The text cleaned of its Markdown when it is simple, else None (complex: the model decides).

    ``origin`` is None for a message (judged on its Markdown source), ``"rendered"`` for a
    selection from rendered content, ``"file"`` for a selection from a file. Any other value is
    treated as a message: the caller validates ``origin``.
    """
    try:
        if origin == "file":
            return None
        if origin == "rendered" and (len(text.splitlines()) > 1 or "\t" in text):
            return None
        return _clean(text)
    except Exception:  # noqa: BLE001 - the contract is: never raise, doubt means complex
        return None
