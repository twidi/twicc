"""Extract the ``export const meta = {...}`` block from a saved Claude Code
workflow ``.js`` file, in pure Python.

Saved workflows live as ``.js`` files — globally in ``<claude home>/workflows/``
and per-project in ``<project>/.claude/workflows/`` — and are invocable like
slash commands. Each script starts with an ``export const meta = {...}``
object literal carrying (at least) ``name`` and ``description``. Command
discovery runs entirely in the backend, so we recover that metadata without
Node and without a JS engine.

The ``meta`` object is contractually a *pure literal* (no variables, calls,
spreads, or template interpolation), which is what makes a small structural
parser sufficient — and necessary: a regex would trip over the schema objects
that follow ``meta`` in the file (they carry their own ``name:`` /
``description:`` keys) and over braces/commas that appear inside strings.

Distinct from :mod:`workflow_synthesis`, which handles workflow *runs*
(``wf_*.json`` under a session dir); this module is about saved workflow
*definitions*.

Public surface: :func:`extract_workflow_meta` and :class:`WorkflowMetaError`.
"""

from __future__ import annotations

import re


class WorkflowMetaError(ValueError):
    """Raised when the ``meta`` literal cannot be located or parsed."""


# Anchored at line start (MULTILINE) so a full-line comment such as
# ``// export const meta = {fake}`` above the real declaration cannot be
# mistaken for it. The workflow contract puts ``export const meta`` at the
# top of the script, always at column 0.
_META_START_RE = re.compile(r"^[ \t]*export\s+const\s+meta\b\s*=\s*", re.MULTILINE)


class _JsLiteralParser:
    """Minimal recursive parser for a JS object/array literal.

    Handles: nested objects/arrays, single/double/backtick strings (with
    escapes), numbers, ``true``/``false``/``null``/``undefined``, unquoted
    identifier keys, trailing commas, and ``//`` / ``/* */`` comments. Braces
    and commas *inside* strings are handled structurally (not by counting).
    Template interpolation (``${...}``) is forbidden in ``meta`` and is kept
    as literal text if present.
    """

    def __init__(self, text: str, start: int = 0) -> None:
        self.s = text
        self.i = start
        self.n = len(text)

    def _skip_ws(self) -> None:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and self.i + 1 < self.n and self.s[self.i + 1] == "/":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c == "/" and self.i + 1 < self.n and self.s[self.i + 1] == "*":
                self.i += 2
                while self.i + 1 < self.n and not (self.s[self.i] == "*" and self.s[self.i + 1] == "/"):
                    self.i += 1
                self.i += 2
            else:
                break

    def parse_value(self):
        self._skip_ws()
        if self.i >= self.n:
            raise WorkflowMetaError("unexpected end of input")
        c = self.s[self.i]
        if c == "{":
            return self._parse_object()
        if c == "[":
            return self._parse_array()
        if c in "'\"`":
            return self._parse_string()
        return self._parse_primitive()

    def _parse_object(self) -> dict:
        obj: dict = {}
        self.i += 1  # consume {
        while True:
            self._skip_ws()
            if self.i >= self.n:
                raise WorkflowMetaError("unterminated object")
            if self.s[self.i] == "}":
                self.i += 1
                return obj
            key = self._parse_key()
            self._skip_ws()
            if self.i >= self.n or self.s[self.i] != ":":
                raise WorkflowMetaError(f"expected ':' after key {key!r}")
            self.i += 1  # consume :
            obj[key] = self.parse_value()
            self._skip_ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and self.s[self.i] == "}":
                self.i += 1
                return obj
            else:
                raise WorkflowMetaError("expected ',' or '}' in object")

    def _parse_key(self) -> str:
        self._skip_ws()
        if self.i >= self.n:
            raise WorkflowMetaError("unterminated object (expected key)")
        c = self.s[self.i]
        if c in "'\"`":
            return self._parse_string()
        start = self.i
        while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] in "_$"):
            self.i += 1
        if self.i == start:
            raise WorkflowMetaError(f"invalid key at offset {self.i}")
        return self.s[start:self.i]

    def _parse_array(self) -> list:
        arr: list = []
        self.i += 1  # consume [
        while True:
            self._skip_ws()
            if self.i >= self.n:
                raise WorkflowMetaError("unterminated array")
            if self.s[self.i] == "]":
                self.i += 1
                return arr
            arr.append(self.parse_value())
            self._skip_ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and self.s[self.i] == "]":
                self.i += 1
                return arr
            else:
                raise WorkflowMetaError("expected ',' or ']' in array")

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0"}

    def _parse_string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                if self.i + 1 >= self.n:
                    break
                nxt = self.s[self.i + 1]
                if nxt == "u" and self.i + 6 <= self.n:
                    try:
                        out.append(chr(int(self.s[self.i + 2:self.i + 6], 16)))
                        self.i += 6
                        continue
                    except ValueError:
                        pass
                if nxt == "x" and self.i + 4 <= self.n:
                    try:
                        out.append(chr(int(self.s[self.i + 2:self.i + 4], 16)))
                        self.i += 4
                        continue
                    except ValueError:
                        pass
                if nxt == "\n":  # line continuation
                    self.i += 2
                    continue
                out.append(self._ESCAPES.get(nxt, nxt))
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise WorkflowMetaError("unterminated string")

    def _parse_primitive(self):
        start = self.i
        while self.i < self.n and self.s[self.i] not in ",}]\r\n \t":
            self.i += 1
        tok = self.s[start:self.i].strip()
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok in ("null", "undefined"):
            return None
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            return tok  # bare identifier / expression — keep as raw text


def extract_workflow_meta(source: str) -> dict:
    """Locate ``export const meta = {...}`` in *source* and return it as a dict.

    Parses structure only; it does not decide whether the result is usable as
    a command (``name``/``description`` present, etc.) — that is the discovery
    layer's job. Raises :class:`WorkflowMetaError` if the declaration is
    absent, ``meta`` is not an object literal, or the literal is malformed.
    """
    m = _META_START_RE.search(source)
    if not m:
        raise WorkflowMetaError("no `export const meta = ...` found")
    parser = _JsLiteralParser(source, m.end())
    parser._skip_ws()
    if parser.i >= parser.n or parser.s[parser.i] != "{":
        raise WorkflowMetaError("`meta` is not an object literal")
    value = parser.parse_value()
    if not isinstance(value, dict):
        raise WorkflowMetaError("`meta` did not parse to an object")
    return value
