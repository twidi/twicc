"""Recognize standalone publications without reading their source files.

Markdown owns block boundaries. Descriptor parsing only sees complete root
paragraphs, so code, quotes, lists, and containers cannot publish artifacts.
Offsets always refer to the original source, including CRLF and Unicode.
"""

import re
from typing import NamedTuple

from markdown_it import MarkdownIt
from markdown_it.rules_inline import backtick


class ArtifactBlock(NamedTuple):
    start: int
    end: int
    descriptor: dict | None
    error: str | None


_TAG = re.compile(r'^<twicc:inline-artifact(?=\s|/>)((?:[^"<>]|"[^"]*")*)/>$')
_ATTRIBUTE = re.compile(r'\s+([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*"([^"]*)"')
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")


def _colon_run(state, line):
    if state.sCount[line] - state.blkIndent >= 4:
        return 0
    start = state.bMarks[line] + state.tShift[line]
    end = start
    while end < state.eMarks[line] and state.src[end] == ":":
        end += 1
    return end - start


def _colon_block(state, start_line, end_line, silent):
    """Match TwiCC's colon blocks; only nesting and line maps are needed."""
    count = _colon_run(state, start_line)
    start = state.bMarks[start_line] + state.tShift[start_line]
    after = start + count
    if count < 2 or state.src[after : after + 1] not in (" ", "\t"):
        return False
    if not state.src[after : state.eMarks[start_line]].strip():
        return False
    if silent:
        return True
    if count == 2:
        token = state.push("colon_line", "div", 0)
        token.map = [start_line, start_line + 1]
        state.line = start_line + 1
        return True
    next_line = start_line + 1
    while next_line < end_line:
        run = _colon_run(state, next_line)
        pos = state.bMarks[next_line] + state.tShift[next_line] + run
        if run >= count and not state.src[pos : state.eMarks[next_line]].strip():
            break
        next_line += 1
    closed = next_line < end_line
    old_parent, old_max = state.parentType, state.lineMax
    state.parentType, state.lineMax = "container", next_line
    token = state.push("container_open", "div", 1)
    token.map = [start_line, next_line + int(closed)]
    state.md.block.tokenize(state, start_line + 1, next_line)
    state.push("container_close", "div", -1)
    state.parentType, state.lineMax = old_parent, old_max
    state.line = next_line + int(closed)
    return True


def _comment_block(state, start_line, end_line, silent):
    start = state.bMarks[start_line] + state.tShift[start_line]
    if state.src[start : start + 4] != "<!--":
        return False
    for line in range(start_line, end_line):
        text = state.src[state.bMarks[line] + state.tShift[line] : state.eMarks[line]]
        close = text.find("-->", 4 if line == start_line else 0)
        if close < 0:
            continue
        if text[close + 3 :].strip():
            return False
        if silent:
            return True
        state.line = line + 1
        state.push("html_comment", "", 0).hidden = True
        return True
    return False


def _comment_inline(state, silent):
    """Match the frontend inline comment rule before native backtick parsing."""
    start = state.pos
    if state.src[start : start + 4] != "<!--":
        return False
    close = state.src.find("-->", start + 4)
    if close < 0 or close + 3 > state.posMax:
        return False
    if not silent:
        state.push("html_comment", "", 0).hidden = True
    state.pos = close + 3
    return True


def _backtick_source_span(state, silent):
    start = state.pos
    token_count = len(state.tokens)
    matched = backtick(state, silent)
    if not silent and len(state.tokens) > token_count:
        token = state.tokens[-1]
        if token.type == "code_inline":
            token.meta["source_span"] = (start, state.pos)
    return matched


_MARKDOWN = MarkdownIt("default", {"html": False})
_MARKDOWN.block.ruler.before(
    "fence", "colon_block", _colon_block, {"alt": ["paragraph", "reference", "blockquote", "list"]}
)
_MARKDOWN.block.ruler.before("paragraph", "html_comment", _comment_block)
_MARKDOWN.inline.ruler.before("html_inline", "html_comment", _comment_inline)
_MARKDOWN.inline.ruler.at("backticks", _backtick_source_span)


def _line_offsets(text):
    # Markdown normalizes CRLF and lone CR. Its maps still count source lines.
    offsets = [0]
    offsets.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", text))
    offsets.append(len(text))
    return offsets


def _is_escaped(text, position):
    preceding = position - 1
    while preceding >= 0 and text[preceding] == "\\":
        preceding -= 1
    return (position - preceding - 1) % 2 == 1


def _comment_ranges(text, tokens, offsets):
    """Find comment boundaries outside Markdown code spans and code blocks.

    Comment scopes can cross paragraph boundaries, including blank lines and
    unterminated comments. Native inline parsing owns code delimiter boundaries.
    """
    code_ranges = []
    for token in tokens:
        if not token.map:
            continue
        start, end = (offsets[line] for line in token.map)
        if token.type in ("fence", "code_block"):
            code_ranges.append((start, end))
        elif token.type == "inline":
            if _TAG.fullmatch(text[start:end].strip()):
                code_ranges.append((start, end))
                continue
            children = []
            _MARKDOWN.inline.parse(text[start:end], _MARKDOWN, {}, children)
            for child in children:
                if child.type == "code_inline" and "source_span" in child.meta:
                    left, right = child.meta["source_span"]
                    code_ranges.append((start + left, start + right))
    code_ranges.sort()
    ranges = []
    pos = 0
    code_index = 0
    while pos < len(text):
        while code_index < len(code_ranges) and code_ranges[code_index][1] <= pos:
            code_index += 1
        if code_index < len(code_ranges) and code_ranges[code_index][0] <= pos:
            pos = code_ranges[code_index][1]
            continue
        if text.startswith("<!--", pos) and not _is_escaped(text, pos):
            close = text.find("-->", pos + 4)
            end = len(text) if close < 0 else close + 3
            ranges.append((pos, end))
            pos = end
        else:
            pos += 1
    return ranges


def _descriptor(source):
    match = _TAG.fullmatch(source)
    if match is None:
        return None, "invalid_attributes"
    attributes = {}
    content = match[1]
    pos = 0
    while pos < len(content):
        if not content[pos:].strip():
            break
        attribute = _ATTRIBUTE.match(content, pos)
        if attribute is None or attribute[1] in attributes:
            return None, "invalid_attributes"
        attributes[attribute[1]] = attribute[2]
        pos = attribute.end()
    if "id" not in attributes:
        return None, "missing_id"
    if "src" not in attributes:
        return None, "missing_src"
    artifact_id = attributes["id"]
    if not _ID.fullmatch(artifact_id):
        return None, "invalid_id"
    src = attributes["src"]
    parts = src.split("/")
    if (
        len(parts) != 3
        or parts[:2] != ["inline-artifacts", artifact_id]
        or any(char in src for char in "\\?#\x00")
        or not re.fullmatch(r".+\.(?:html|htm)", parts[-1])
        or parts[-1] in (".", "..")
    ):
        return None, "invalid_src"
    title = attributes.get("title", artifact_id)
    if len(title) > 200:
        return None, "invalid_title"
    raw_height = attributes.get("height", "360")
    if not _INTEGER.fullmatch(raw_height):
        return None, "invalid_height"
    # Avoid Python's integer-digit limit for extreme, otherwise valid requests.
    magnitude = raw_height.lstrip("+-").lstrip("0")
    height = 900 if len(magnitude) > 3 else int(magnitude or "0")
    if raw_height.startswith("-"):
        height = -height
    return {
        "artifact_id": artifact_id,
        "src": src,
        "title": title,
        "height": max(160, min(900, height)),
    }, None


def parse_inline_artifact_blocks(text: str) -> list[ArtifactBlock]:
    tokens = _MARKDOWN.parse(text)
    offsets = _line_offsets(text)
    comments = _comment_ranges(text, tokens, offsets)
    blocks = []
    for token in tokens:
        if token.type != "paragraph_open" or token.level != 0 or not token.map:
            continue
        start_line, end_line = token.map
        start, end = (offsets[line] for line in token.map)
        if start_line and text[offsets[start_line - 1] : start].strip():
            continue
        if end_line < len(offsets) - 1 and text[end : offsets[end_line + 1]].strip():
            continue
        raw = text[start:end]
        source = raw.strip()
        if not source.startswith("<twicc:inline-artifact") or not re.match(r"<twicc:inline-artifact(?=\s|/>)", source):
            continue
        # Incomplete tags and paragraphs with neighboring prose remain Markdown.
        if not re.fullmatch(r'<twicc:inline-artifact(?:[^"<>]|"[^"]*")*/>', source):
            continue
        start += len(raw) - len(raw.lstrip())
        end = start + len(source)
        if any(left <= start < right for left, right in comments):
            continue
        descriptor, error = _descriptor(source)
        blocks.append(ArtifactBlock(start, end, descriptor, error))
    return blocks


def _source_identity(record):
    return record["line_num"], record["text_block_index"], record["tag_offset"]


def merge_publications(catalog: dict, records: list[dict]) -> dict:
    publications = {_source_identity(record): dict(record) for record in catalog.get("publications", [])}
    for record in records:
        publications[_source_identity(record)] = dict(record)
    if not publications:
        return {}
    return {"schema": 1, "publications": [publications[key] for key in sorted(publications)]}


def latest_publications(catalog: dict) -> dict[str, dict]:
    latest = {}
    for record in sorted(catalog.get("publications", []), key=_source_identity):
        latest[record["artifact_id"]] = dict(record)
    return latest
