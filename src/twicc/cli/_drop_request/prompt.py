"""Resolve the positional ``PROMPT`` argument.

If the value points to an existing file (absolute or relative), read its
UTF-8 content. Otherwise treat the value as the prompt text.

The resolved text then goes through ``@@`` include expansion (unless the
caller opts out): a marker referencing a file path is replaced by the
file's content, recursively. Grammar, scanned left to right, first match
wins:

- ``@@@@`` — escape, renders as a literal ``@@``.
- ``@@{<path>}`` — delimited marker (allows spaces); no closing ``}`` on
  the same line, or a path without a valid prefix → literal text.
- ``@@<path>`` — bare marker, ends at the first whitespace.
- any other ``@@`` — literal text.

A path must start with ``/``, ``~/``, ``./``, ``../`` or ``remote:``
(else the ``@@`` is literal). A ``./`` or ``../`` path resolves against
the directory of the file that *carries* the marker — the included file
for a nested marker, the prompt file when the prompt argument is a path —
never the process cwd, so a tree of prepared prompt files stays portable
and only its entry point needs an absolute path. Inline prompt text has
no such base: a relative marker there is an error. ``remote:`` stays
absolute-only (the client cannot resolve a base on the server).

A missing file expands to nothing (the whole line is dropped when the
marker sits alone on it); a directory, unreadable or non-UTF-8 file is an
error. Included content is re-scanned, up to :data:`MAX_INCLUDE_DEPTH`
levels; the total output is capped at :data:`MAX_PROMPT_BYTES` (checked
while expanding and on the final prompt, expansion or not).

``forward`` mode is the client half of ``--remote``: local markers are
expanded here, ``remote:`` markers are rewritten to bare markers for the
server, and every literal ``@@`` in the produced text is re-escaped so
the server's own pass (a plain local expansion) reproduces the intended
text exactly.
"""

from __future__ import annotations

import os
import uuid
from typing import NamedTuple

from twicc.cli._output import in_api_mode
from twicc.cli._drop_request.remote_scheme import (
    REMOTE_PATH_SCHEME,
    has_remote_scheme,
    remote_scheme_path,
)

# Byte cap on the resolved prompt (UTF-8), enforced as a running budget
# during include expansion and again on the final text.
MAX_PROMPT_BYTES = 500 * 1024

# How many nested include levels are allowed. A marker found while already
# expanding at this depth is an error, so an include cycle terminates with
# a clear message instead of looping.
MAX_INCLUDE_DEPTH = 5


class PromptError(Exception):
    pass


class _Marker(NamedTuple):
    """One include marker: its raw path spec and whether it was ``@@{...}``."""

    spec: str
    delimited: bool


class _Ctx:
    """Per-expansion state: byte budget, mode, and remote-marker registry."""

    __slots__ = ("forward", "remote_markers", "stamp", "used")

    def __init__(self, forward: bool) -> None:
        self.forward = forward
        self.used = 0
        # Sentinel stamp: NUL-framed and free of ``@``, so the global
        # ``@@`` escaping pass cannot corrupt a placeholder and user text
        # cannot collide with one.
        self.stamp = uuid.uuid4().hex
        self.remote_markers: list[_Marker] = []

    def charge(self, text: str) -> None:
        self.used += len(text.encode("utf-8"))
        if self.used > MAX_PROMPT_BYTES:
            raise PromptError(
                f"prompt exceeds the {MAX_PROMPT_BYTES // 1024} KB limit "
                "after include expansion"
            )

    def sentinel(self, marker: _Marker) -> str:
        self.remote_markers.append(marker)
        return f"\x00{self.stamp}:{len(self.remote_markers) - 1}\x00"


def _marker_path_start(rest: str) -> bool:
    """True when ``rest`` starts like a marker path (``/``, ``~/``, ``./``, ``../``, ``remote:``)."""
    return rest.startswith(("/", "~/", "./", "../", REMOTE_PATH_SCHEME))


def _tokenize_line(line: str) -> list[tuple[str, _Marker | str | None]]:
    """Split one line into ``("lit", text)`` / ``("esc", None)`` / ``("marker", _Marker)`` parts."""
    parts: list[tuple[str, _Marker | str | None]] = []
    i = 0
    n = len(line)
    lit_start = 0

    def flush(end: int) -> None:
        if end > lit_start:
            parts.append(("lit", line[lit_start:end]))

    while i < n:
        if not line.startswith("@@", i):
            i += 1
            continue
        if line.startswith("@@@@", i):
            flush(i)
            parts.append(("esc", None))
            i += 4
            lit_start = i
            continue
        if line.startswith("@@{", i):
            close = line.find("}", i + 3)
            if close != -1 and _marker_path_start(line[i + 3 : close]):
                flush(i)
                parts.append(("marker", _Marker(line[i + 3 : close], True)))
                i = close + 1
                lit_start = i
                continue
            # Unclosed or invalid inner path: the ``@@`` pair is literal.
            i += 2
            continue
        if _marker_path_start(line[i + 2 :]):
            j = i + 2
            while j < n and not line[j].isspace():
                j += 1
            flush(i)
            parts.append(("marker", _Marker(line[i + 2 : j], False)))
            i = j
            lit_start = i
            continue
        # ``@@`` not followed by a path start: both chars stay literal, and
        # consuming the pair keeps the second ``@`` from seeding a new match.
        i += 2
    flush(n)
    return parts


def _include_path(spec: str, base: str | None) -> str:
    """Resolve one marker spec to a filesystem path.

    ``/`` and ``~/`` specs stand on their own; ``./`` and ``../`` specs
    resolve against ``base``, the directory of the file carrying the marker
    (never the process cwd — a CLI call from anywhere, or an in-process MCP
    call, must resolve the same way).
    """
    if spec.startswith(("/", "~/")):
        return os.path.expanduser(spec)
    if base is None:
        raise PromptError(
            f"include {spec!r} is relative but has no file to resolve against: "
            "relative markers only work inside a file (an included file, or "
            "the prompt when it is given as a path)"
        )
    return os.path.normpath(os.path.join(base, spec))


def _resolve_marker(
    marker: _Marker, depth: int, ctx: _Ctx, chain: tuple[str, ...], base: str | None
) -> str:
    spec = marker.spec
    if spec.startswith(REMOTE_PATH_SCHEME):
        path = remote_scheme_path(spec)
        if not ctx.forward:
            raise PromptError(
                f"@@{REMOTE_PATH_SCHEME} include markers are only valid with --remote, "
                f"got {spec!r}"
            )
        if not path.startswith(("/", "~/")):
            # The server resolves these on its own filesystem, so the client
            # has no base to offer: relative forms stay refused.
            raise PromptError(
                f"@@{REMOTE_PATH_SCHEME} requires an absolute path "
                f"(e.g. @@remote:/abs/path), got {spec!r}"
            )
        ctx.charge("@@" + path)
        return ctx.sentinel(_Marker(path, marker.delimited))
    path = _include_path(spec, base)
    if depth >= MAX_INCLUDE_DEPTH:
        raise PromptError(
            f"include depth exceeds {MAX_INCLUDE_DEPTH} "
            f"(chain: {' -> '.join(chain) or '<prompt>'} -> {path})"
        )
    if os.path.isdir(path):
        raise PromptError(f"include {path!r} is a directory")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return ""  # optional include: a missing file expands to nothing
    except UnicodeDecodeError as e:
        raise PromptError(f"include {path!r} is not valid UTF-8: {e}")
    except OSError as e:
        raise PromptError(f"include {path!r} is not readable: {e}")
    content = content.rstrip("\n")
    if not content:
        return ""
    # The chain records resolved paths: a cycle written with relative
    # markers is unreadable as specs alone.
    return _expand_text(
        content, depth + 1, ctx, chain + (path,), os.path.dirname(os.path.abspath(path))
    )


def _render_line(
    line: str, depth: int, ctx: _Ctx, chain: tuple[str, ...], base: str | None
) -> str | None:
    """Expand one line; None means the line is dropped entirely."""
    parts = _tokenize_line(line)
    # A marker alone on its line (only whitespace around it) that expands to
    # nothing takes the whole line with it, so optional includes leave no
    # blank line behind.
    sole_marker = (
        sum(1 for kind, _ in parts if kind == "marker") == 1
        and all(kind != "esc" for kind, _ in parts)
        and all(str(value).strip() == "" for kind, value in parts if kind == "lit")
    )
    out: list[str] = []
    marker_render: str | None = None
    for kind, value in parts:
        if kind == "lit":
            ctx.charge(value)  # type: ignore[arg-type]
            out.append(value)  # type: ignore[arg-type]
        elif kind == "esc":
            ctx.charge("@@")
            out.append("@@")
        else:
            rendered = _resolve_marker(value, depth, ctx, chain, base)  # type: ignore[arg-type]
            marker_render = rendered
            out.append(rendered)
    if sole_marker and marker_render == "":
        return None
    return "".join(out)


def _expand_text(
    text: str, depth: int, ctx: _Ctx, chain: tuple[str, ...], base: str | None
) -> str:
    lines = [_render_line(line, depth, ctx, chain, base) for line in text.split("\n")]
    return "\n".join(line for line in lines if line is not None)


def expand_prompt_includes(
    text: str, *, forward: bool = False, base: str | None = None
) -> str:
    """Expand ``@@`` include markers in ``text`` (see the module docstring).

    ``base`` is the directory the top-level ``./``/``../`` markers resolve
    against — the directory of the file ``text`` was read from, or None for
    inline text (relative markers are then an error). Nested markers always
    resolve against their own file, whatever ``base`` is.

    ``forward=True`` runs the client half of a ``--remote`` invocation:
    ``remote:`` markers survive as bare/delimited markers for the server and
    every other ``@@`` in the output is escaped for the server's own pass.
    """
    ctx = _Ctx(forward)
    rendered = _expand_text(text, 0, ctx, (), base)
    if not forward:
        return rendered
    rendered = rendered.replace("@@", "@@@@")
    for index, marker in enumerate(ctx.remote_markers):
        replacement = (
            "@@{" + marker.spec + "}" if marker.delimited else "@@" + marker.spec
        )
        rendered = rendered.replace(f"\x00{ctx.stamp}:{index}\x00", replacement)
    return rendered


def resolve_prompt(prompt_arg: str, *, expand: bool = True) -> str:
    if has_remote_scheme(prompt_arg):
        # `remote:` only has meaning over --remote, where the forwarder strips it
        # before the server ever runs this. Reaching it here means a local run.
        raise PromptError("remote: paths are only valid with --remote")
    base: str | None = None
    if os.path.isfile(prompt_arg) and (not in_api_mode() or os.path.isabs(prompt_arg)):
        try:
            with open(prompt_arg, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError as e:
            raise PromptError(f"prompt: file {prompt_arg!r} is not valid UTF-8: {e}")
        if not text.strip():
            raise PromptError(f"prompt: file {prompt_arg!r} is empty")
        # A prompt file carries markers like any included file, so its own
        # directory is the base for its relative ones.
        base = os.path.dirname(os.path.abspath(prompt_arg))
    else:
        text = prompt_arg
    if expand:
        text = expand_prompt_includes(text, base=base)
    if not text.strip():
        raise PromptError("prompt is empty")
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise PromptError(
            f"prompt is too large: exceeds the {MAX_PROMPT_BYTES // 1024} KB limit"
        )
    return text
