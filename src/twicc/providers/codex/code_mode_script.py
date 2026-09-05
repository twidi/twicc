"""Static extraction from Codex "code mode" ``exec`` scripts (GPT-5.6+).

GPT-5.6 Codex models and GPT-6 Astra run in code mode. Every action is a
``custom_tool_call`` named ``exec`` whose ``input`` is JavaScript executed in
a V8 isolate by the CLI. The JS calls nested tools on a global ``tools`` object
(``await tools.exec_command({...})``, ``await tools.apply_patch("...")``,
``tools.mcp__server__tool({...})``) — and the rollout JSONL only persists the
outer script, never the nested calls. This module statically recovers those
nested calls WITHOUT executing anything: a string/comment-aware scanner finds
``tools.<name>(...)`` call sites and a small literal parser resolves their
arguments when they are compile-time constants.

Mirrored by ``frontend/src/providers/codex/parseCodeModeScript.js`` — any
change here must be replicated there (same precedent as ``parse_command.rs``
↔ ``parseCommand.js``).

Also hosts :func:`parse_code_mode_output`, the parser for the formatted
status header Codex prepends to every ``exec`` / ``wait`` output
(``format_script_status`` / ``prepend_script_status`` in
``codex-rs/core/src/tools/code_mode/mod.rs``).

Design: ``docs/plans/2026-07-10-codex-code-mode-display-design.md``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import orjson


class CodeModeCall(NamedTuple):
    """One ``tools.<name>(...)`` call site found in a code-mode script.

    ``arg`` is the statically-resolved value of the (single) argument when
    ``resolved`` is True, else ``None``. A call with multiple arguments, a
    non-literal argument, or an unbalanced span is reported unresolved but
    still listed — the name alone is enough for the tier-2 summary.
    """
    name: str
    arg: object
    resolved: bool


class CodeModeScript(NamedTuple):
    """Extraction result for one script — see :func:`parse_code_mode_script`."""
    calls: list[CodeModeCall]
    pragma: dict | None


class CodeModeOutput(NamedTuple):
    """Parsed ``exec`` / ``wait`` tool output — see :func:`parse_code_mode_output`.

    ``status`` is one of ``"completed"`` / ``"failed"`` / ``"terminated"`` /
    ``"running"``. ``cell_id`` is only set for ``running`` (the id a later
    ``wait`` call polls). ``error_text`` is the ``Script error:`` segment
    body when present (``failed`` outputs usually carry one). ``body`` is
    the script's own ``text(...)`` output with the status header and error
    segment stripped.
    """
    status: str
    cell_id: str | None
    wall_time_seconds: float | None
    error_text: str | None
    body: str


_EMPTY_SCRIPT = CodeModeScript([], None)

# First-line pragma the model may emit to tune the cell's execution
# (``// @exec: {"yield_time_ms": 500}``). Parsed for display only.
_PRAGMA_RE = re.compile(r"^[ \t]*//[ \t]*@exec:[ \t]*(\{.*\})[ \t]*$")

# Status header prepended to every code-mode output by
# ``prepend_script_status`` (exact format, incl. the trailing newline).
_OUTPUT_HEADER_RE = re.compile(
    r"^Script (?P<status>completed|failed|terminated|running with cell ID (?P<cell>[^\n]*))\n"
    r"Wall time (?P<wall>\d+(?:\.\d+)?) seconds\nOutput:\n"
)

_SCRIPT_ERROR_PREFIX = "Script error:\n"

_IDENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$"
)

_WHITESPACE = frozenset(" \t\r\n")

# Sentinel for "parse failed" — distinct from a successfully parsed None
# (JS ``null`` / ``undefined`` both resolve to Python ``None``).
_FAIL = object()


# =============================================================================
# Low-level scanning (string / comment aware)
# =============================================================================


def _skip_string(source: str, i: int) -> int:
    """Skip a string literal starting at ``i`` (quote char), return the index
    just past the closing quote (or ``len(source)`` when unterminated).

    Template literals are skipped to the closing backtick with escape
    handling; ``${...}`` interpolations are treated as raw text, so a
    ``tools.*`` call inside one is not detected (accepted limitation).
    """
    quote = source[i]
    i += 1
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        if ch == "\n" and quote != "`":
            # Unterminated single/double quote — stop at the line break so
            # a malformed script doesn't swallow the rest of the source.
            return i
        i += 1
    return n


def _skip_line_comment(source: str, i: int) -> int:
    end = source.find("\n", i)
    return len(source) if end == -1 else end + 1


def _skip_block_comment(source: str, i: int) -> int:
    end = source.find("*/", i + 2)
    return len(source) if end == -1 else end + 2


def _skip_ws_and_comments(source: str, i: int) -> int:
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in _WHITESPACE:
            i += 1
        elif ch == "/" and i + 1 < n and source[i + 1] == "/":
            i = _skip_line_comment(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            i = _skip_block_comment(source, i)
        else:
            break
    return i


def _capture_paren_span(source: str, i: int) -> tuple[str | None, int]:
    """Return ``(inner, end)`` for the balanced ``(...)`` starting at ``i``.

    ``inner`` excludes the outer parentheses; ``end`` is the index just past
    the closing one. String literals and comments inside are skipped, so
    parentheses within them don't unbalance the scan. Returns
    ``(None, len(source))`` when unbalanced.
    """
    n = len(source)
    depth = 0
    start = i + 1
    while i < n:
        ch = source[i]
        if ch in "'\"`":
            i = _skip_string(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "/":
            i = _skip_line_comment(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            i = _skip_block_comment(source, i)
        elif ch == "(":
            depth += 1
            i += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[start:i], i + 1
            i += 1
        else:
            i += 1
    return None, n


# =============================================================================
# Literal parsing (recursive descent over a JS literal expression)
# =============================================================================


_STRING_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
    "0": "\0", "\n": "",  # line continuation
}


def _parse_string_literal(source: str, i: int) -> tuple[object, int]:
    """Parse one quoted string starting at ``i``; template literals are
    accepted only when they contain no ``${`` interpolation."""
    quote = source[i]
    i += 1
    n = len(source)
    parts: list[str] = []
    while i < n:
        ch = source[i]
        if ch == "\\":
            if i + 1 >= n:
                return _FAIL, i
            esc = source[i + 1]
            if esc == "u":
                if i + 5 < n and source[i + 2] == "{":
                    end = source.find("}", i + 3)
                    if end == -1:
                        return _FAIL, i
                    try:
                        parts.append(chr(int(source[i + 3:end], 16)))
                    except ValueError:
                        return _FAIL, i
                    i = end + 1
                else:
                    try:
                        parts.append(chr(int(source[i + 2:i + 6], 16)))
                    except ValueError:
                        return _FAIL, i
                    i += 6
                continue
            if esc == "x":
                try:
                    parts.append(chr(int(source[i + 2:i + 4], 16)))
                except ValueError:
                    return _FAIL, i
                i += 4
                continue
            parts.append(_STRING_ESCAPES.get(esc, esc))
            i += 2
            continue
        if ch == quote:
            return "".join(parts), i + 1
        if quote == "`" and ch == "$" and i + 1 < n and source[i + 1] == "{":
            return _FAIL, i
        if ch == "\n" and quote != "`":
            return _FAIL, i
        parts.append(ch)
        i += 1
    return _FAIL, i


_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _parse_number(source: str, i: int) -> tuple[object, int]:
    match = _NUMBER_RE.match(source, i)
    if match is None:
        return _FAIL, i
    text = match.group(0)
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text), match.end()
    return float(text), match.end()


def _read_identifier(source: str, i: int) -> tuple[str, int]:
    start = i
    n = len(source)
    while i < n and source[i] in _IDENT_CHARS:
        i += 1
    return source[start:i], i


def _parse_string_expr(
    source: str, i: int, consts: dict[str, str]
) -> tuple[object, int]:
    """Parse a string literal / const identifier, optionally ``+``-chained."""
    value, i = _parse_string_operand(source, i, consts)
    if value is _FAIL:
        return _FAIL, i
    while True:
        j = _skip_ws_and_comments(source, i)
        if j >= len(source) or source[j] != "+":
            return value, i
        operand, j = _parse_string_operand(
            source, _skip_ws_and_comments(source, j + 1), consts
        )
        if operand is _FAIL:
            return _FAIL, i
        value += operand
        i = j


def _parse_string_operand(
    source: str, i: int, consts: dict[str, str]
) -> tuple[object, int]:
    if i < len(source) and source[i] in "'\"`":
        return _parse_string_literal(source, i)
    ident, j = _read_identifier(source, i)
    if ident and ident in consts:
        return consts[ident], j
    return _FAIL, i


def _parse_value(
    source: str, i: int, consts: dict[str, str]
) -> tuple[object, int]:
    """Parse one JS literal value at ``i``; returns ``(_FAIL, i)`` on any
    non-literal construct (call, spread, computed key, interpolation, …)."""
    i = _skip_ws_and_comments(source, i)
    if i >= len(source):
        return _FAIL, i
    ch = source[i]
    if ch in "'\"`":
        return _parse_string_expr(source, i, consts)
    if ch == "{":
        return _parse_object(source, i, consts)
    if ch == "[":
        return _parse_array(source, i, consts)
    if ch in "+-" or ch.isdigit() or ch == ".":
        return _parse_number(source, i)
    ident, j = _read_identifier(source, i)
    if not ident:
        return _FAIL, i
    if ident == "true":
        return True, j
    if ident == "false":
        return False, j
    if ident in ("null", "undefined"):
        return None, j
    if ident in consts:
        # Const string binding — may itself chain with ``+``.
        return _parse_string_expr(source, i, consts)
    return _FAIL, i


def _parse_object(
    source: str, i: int, consts: dict[str, str]
) -> tuple[object, int]:
    obj: dict[str, object] = {}
    i = _skip_ws_and_comments(source, i + 1)
    n = len(source)
    if i < n and source[i] == "}":
        return obj, i + 1
    while i < n:
        # Key: quoted string or bare identifier.
        if source[i] in "'\"":
            key, i = _parse_string_literal(source, i)
            if key is _FAIL:
                return _FAIL, i
        else:
            key, i = _read_identifier(source, i)
            if not key:
                return _FAIL, i
        i = _skip_ws_and_comments(source, i)
        if i >= n or source[i] != ":":
            return _FAIL, i
        value, i = _parse_value(source, i + 1, consts)
        if value is _FAIL:
            return _FAIL, i
        obj[key] = value
        i = _skip_ws_and_comments(source, i)
        if i < n and source[i] == ",":
            i = _skip_ws_and_comments(source, i + 1)
            if i < n and source[i] == "}":  # trailing comma
                return obj, i + 1
            continue
        if i < n and source[i] == "}":
            return obj, i + 1
        return _FAIL, i
    return _FAIL, i


def _parse_array(
    source: str, i: int, consts: dict[str, str]
) -> tuple[object, int]:
    arr: list[object] = []
    i = _skip_ws_and_comments(source, i + 1)
    n = len(source)
    if i < n and source[i] == "]":
        return arr, i + 1
    while i < n:
        value, i = _parse_value(source, i, consts)
        if value is _FAIL:
            return _FAIL, i
        arr.append(value)
        i = _skip_ws_and_comments(source, i)
        if i < n and source[i] == ",":
            i = _skip_ws_and_comments(source, i + 1)
            if i < n and source[i] == "]":  # trailing comma
                return arr, i + 1
            continue
        if i < n and source[i] == "]":
            return arr, i + 1
        return _FAIL, i
    return _FAIL, i


# =============================================================================
# Source-level passes
# =============================================================================


def _build_const_table(source: str) -> dict[str, str]:
    """One pass over the source collecting ``const <id> = <string-expr>;``
    bindings (string literals, optionally ``+``-joined, incl. previously
    collected consts). Only string bindings are recorded — that's all the
    argument resolver dereferences (the canonical apply_patch wrapper binds
    the patch envelope to a const)."""
    consts: dict[str, str] = {}
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in "'\"`":
            i = _skip_string(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "/":
            i = _skip_line_comment(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            i = _skip_block_comment(source, i)
        elif (
            source.startswith("const", i)
            and (i == 0 or source[i - 1] not in _IDENT_CHARS)
            and i + 5 < n
            and source[i + 5] in _WHITESPACE
        ):
            j = _skip_ws_and_comments(source, i + 5)
            name, j = _read_identifier(source, j)
            if not name:
                i += 5
                continue
            j = _skip_ws_and_comments(source, j)
            if j >= n or source[j] != "=":
                i = j
                continue
            value, end = _parse_string_expr(
                source, _skip_ws_and_comments(source, j + 1), consts
            )
            if value is not _FAIL:
                consts[name] = value
                i = end
            else:
                i = j + 1
        else:
            i += 1
    return consts


def _scan_calls(source: str) -> list[tuple[str, str | None]]:
    """Find every ``tools.<name>(`` call site, returning ``(name, arg_span)``
    pairs in source order (``arg_span`` is ``None`` when unbalanced)."""
    calls: list[tuple[str, str | None]] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in "'\"`":
            i = _skip_string(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "/":
            i = _skip_line_comment(source, i)
        elif ch == "/" and i + 1 < n and source[i + 1] == "*":
            i = _skip_block_comment(source, i)
        elif (
            source.startswith("tools.", i)
            and (i == 0 or (source[i - 1] not in _IDENT_CHARS and source[i - 1] != "."))
        ):
            name, j = _read_identifier(source, i + 6)
            k = _skip_ws_and_comments(source, j)
            if name and k < n and source[k] == "(":
                span, end = _capture_paren_span(source, k)
                calls.append((name, span))
                i = end
            else:
                i = j if j > i else i + 6
        else:
            i += 1
    return calls


# =============================================================================
# Public API
# =============================================================================


def parse_code_mode_script(source: object) -> CodeModeScript:
    """Statically extract the nested tool calls of a code-mode script.

    Never raises: any malformed construct degrades to unresolved calls (or
    no calls at all) so the consumer can fall back to the generic "Run
    code" rendering. Consumers classify the result in three tiers:

    - exactly one resolved call → dedicated per-tool rendering;
    - calls detected but not all resolved (or several of them) → generic
      rendering enriched with the call list;
    - nothing detected → raw-JS rendering.
    """
    if not isinstance(source, str) or not source:
        return _EMPTY_SCRIPT

    pragma: dict | None = None
    first_line = source.split("\n", 1)[0]
    pragma_match = _PRAGMA_RE.match(first_line)
    if pragma_match is not None:
        try:
            decoded = orjson.loads(pragma_match.group(1))
        except orjson.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            pragma = decoded

    consts = _build_const_table(source)
    calls: list[CodeModeCall] = []
    for name, span in _scan_calls(source):
        if span is not None:
            stripped = span.strip()
            if not stripped:
                # Zero-argument call — nothing to resolve, but the call
                # itself is fully known.
                calls.append(CodeModeCall(name, None, True))
                continue
            value, end = _parse_value(span, 0, consts)
            if value is not _FAIL and _skip_ws_and_comments(span, end) >= len(span):
                calls.append(CodeModeCall(name, value, True))
                continue
        calls.append(CodeModeCall(name, None, False))
    return CodeModeScript(calls, pragma)


def parse_code_mode_output(output: object) -> CodeModeOutput | None:
    """Parse a code-mode ``exec`` / ``wait`` tool output.

    ``output`` is the raw ``*_call_output.output`` payload value: either a
    plain string or a list of ``{"type": "input_text", "text": ...}``
    segments (Codex serialises single-segment outputs as a bare string).
    Returns ``None`` when the value doesn't start with the code-mode status
    header — the caller's signal that this is NOT a code-mode output.
    """
    if isinstance(output, str):
        segments = [output]
    elif isinstance(output, list):
        segments = [
            item["text"]
            for item in output
            if isinstance(item, dict)
            and item.get("type") == "input_text"
            and isinstance(item.get("text"), str)
        ]
        if not segments:
            return None
    else:
        return None

    match = _OUTPUT_HEADER_RE.match(segments[0])
    if match is None:
        return None
    raw_status = match.group("status")
    status = "running" if raw_status.startswith("running") else raw_status
    cell_id = (match.group("cell") or None) if status == "running" else None
    try:
        wall_time = float(match.group("wall"))
    except ValueError:  # pragma: no cover - regex guarantees a float
        wall_time = None

    error_text: str | None = None
    body_parts = [segments[0][match.end():]]
    for segment in segments[1:]:
        if segment.startswith(_SCRIPT_ERROR_PREFIX):
            error_text = segment[len(_SCRIPT_ERROR_PREFIX):]
        else:
            body_parts.append(segment)
    return CodeModeOutput(status, cell_id, wall_time, error_text, "".join(body_parts))
