"""The ``<twicc:attachments>`` manifest: one builder and one parser.

Every protocol literal (tags, intro, headers, entry grammar, escaping) lives in
this module. Providers build the block with ``build_manifest`` and ingestion
reads it back with ``parse_manifest``. Both are pure functions: no I/O.
"""

import re

from .types import AttachmentManifest, ManifestEntry, ParsedManifest

OPEN_TAG = "<twicc:attachments>"
CLOSE_TAG = "</twicc:attachments>"
INTRO_LINE = "Files the user attached to this message, in the order they attached them."
INLINE_LINE_SDK = "inline = sent to you with this message; the inline files appear above, in this same order."
INLINE_LINE_HYBRID = "inline = attached to this message through the @ reference at the end of its line."
FILE_LINE_PREFIX = "file = "

KINDS = ("image", "PDF", "text", "video", "audio", "other")

# ASCII digits only, without leading zeros (the builder never emits anything else). The hybrid
# path keeps Unicode-aware ``\S``, matching the builder's Unicode whitespace check.
_ENTRY_RE = re.compile(
    r"([1-9]\d*)\. (.+) \((image|PDF|text|video|audio|other) ([1-9]\d*) of ([1-9]\d*), (inline|file)\)"
    r"(?:: @((?u:\S+)))?",
    re.ASCII,
)
_ENTITY_RE = re.compile(r"&(amp|lt|gt|#64);")
_ENTITY_CHARS = {"amp": "&", "lt": "<", "gt": ">", "#64": "@"}
_ABSOLUTE_DIRECTORY_RE = re.compile(r"(?:/|[A-Za-z]:[\\/]).*[\\/]")


def _escape_name(name: str) -> str:
    """Escape ``&``, ``<``, ``>`` and every ``@`` so a name is never markup or a file mention."""
    return name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("@", "&#64;")


def _unescape_name(text: str) -> str:
    """Unescape the four entities in a single pass, so ``&amp;#64;`` yields ``&#64;``."""
    return _ENTITY_RE.sub(lambda match: _ENTITY_CHARS[match.group(1)], text)


def _directory_line(manifest: AttachmentManifest) -> str:
    if manifest.directory is None:
        raise ValueError("A manifest with a file entry needs a directory")
    path = manifest.directory.as_posix()
    return f"{FILE_LINE_PREFIX}{path if path.endswith('/') else path + '/'}"


def build_manifest(manifest: AttachmentManifest, *, hybrid_paths: tuple[str | None, ...] | None = None) -> str:
    """Render the manifest block.

    ``hybrid_paths`` selects the hybrid variant. It aligns with every entry: an
    inline entry carries its ``@<path>`` reference, a file entry carries ``None``.
    """
    entries = manifest.entries
    if not entries:
        raise ValueError("A manifest needs at least one entry")
    if hybrid_paths is not None:
        if len(hybrid_paths) != len(entries):
            raise ValueError("hybrid_paths must align with the manifest entries")
        for entry, path in zip(entries, hybrid_paths):
            if entry.mode == "inline":
                if not path or re.search(r"\s", path):
                    raise ValueError(f"Entry {entry.n} needs a hybrid path without whitespace")
            elif path is not None:
                raise ValueError(f"File entry {entry.n} cannot carry a hybrid path")

    has_inline = any(entry.mode == "inline" for entry in entries)
    has_file = any(entry.mode == "file" for entry in entries)
    lines = [OPEN_TAG, INTRO_LINE]
    if has_inline:
        lines.append(INLINE_LINE_SDK if hybrid_paths is None else INLINE_LINE_HYBRID)
    if has_file:
        lines.append(_directory_line(manifest))
    for index, entry in enumerate(entries):
        name = entry.name
        if entry.mode == "file" and entry.artifact_name is not None:
            name = entry.artifact_name
        line = f"{entry.n}. {_escape_name(name)} ({entry.kind} {entry.rank} of {entry.of}, {entry.mode})"
        if hybrid_paths is not None and entry.mode == "inline":
            line += f": @{hybrid_paths[index]}"
        lines.append(line)
    lines.append(CLOSE_TAG)
    return "\n".join(lines)


def parse_manifest(text: str) -> ParsedManifest | None:
    """Parse a manifest block, or return ``None`` when it deviates from the protocol."""
    lines = text.strip().split("\n")
    if len(lines) < 4 or lines[0] != OPEN_TAG or lines[-1] != CLOSE_TAG or lines[1] != INTRO_LINE:
        return None
    body = lines[2:-1]

    hybrid = False
    has_inline_line = False
    if body and body[0] in (INLINE_LINE_SDK, INLINE_LINE_HYBRID):
        hybrid = body[0] == INLINE_LINE_HYBRID
        has_inline_line = True
        body = body[1:]
    directory = None
    if body and body[0].startswith(FILE_LINE_PREFIX):
        directory = body[0][len(FILE_LINE_PREFIX) :]
        if not _ABSOLUTE_DIRECTORY_RE.fullmatch(directory):
            return None
        body = body[1:]
    if not body:
        return None

    entries: list[ManifestEntry] = []
    paths: list[str | None] = []
    for index, line in enumerate(body):
        match = _ENTRY_RE.fullmatch(line)
        if match is None:
            return None
        n, name, kind, rank, of, mode, path = match.groups()
        n, rank, of = int(n), int(rank), int(of)
        if n != index + 1 or not 1 <= rank <= of:
            return None
        if mode == "inline":
            if hybrid != (path is not None):
                return None
        elif path is not None:
            return None
        name = _unescape_name(name)
        entries.append(ManifestEntry(n, name, kind, rank, of, mode, name if mode == "file" else None))
        paths.append(path)

    if has_inline_line != any(entry.mode == "inline" for entry in entries):
        return None
    if (directory is not None) != any(entry.mode == "file" for entry in entries):
        return None
    return ParsedManifest(tuple(entries), directory, tuple(paths), hybrid)
