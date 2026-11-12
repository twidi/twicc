"""The plain text of a chat message, for the timeline and for the full-text search.

Both read the same text: the timeline shows it and the speech reads it aloud, the search indexes it. It keeps the
Markdown, and it leaves out the XML that only TwiCC and the CLIs understand:

* the tags an agent writes for TwiCC (``<twicc:inline-artifact .../>`` publishes a page, ``<twicc:insert-screenshot />``
  inserts an image): each becomes a short English description, ``[artifact: title]`` or ``[image: title]``. The labels
  are English on purpose: they are stored in the timeline events and the index, which have no language;
* a Claude slash command (``<command-name>...``): see :func:`unwrap_command`.

A tag inside a code span or a fenced code block is a quotation of the tag, not a use of it (an agent explaining the
tags): it is left as it is. The ``<twicc:context>``, ``<twicc:instruction>`` and ``<twicc:attachments>`` blocks are not
handled here: they are removed when a JSONL item is ingested, before any message text is read.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from twicc.providers.compute_base import INSERT_SCREENSHOT_TAG_RE

#: Code that quotes a tag: a fenced block (``` or ~~~, closed or not) or an inline code span.
_CODE = re.compile(r"(```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|`[^`\n]*`)", re.DOTALL)

#: A publication tag alone on its line, attributes possibly on several lines (the rule of inline artifacts).
_INLINE_ARTIFACT = re.compile(r'(?m)^[ \t]*<twicc:inline-artifact(?=\s|/>)((?:[^"<>]|"[^"]*")*)/>[ \t]*$')

#: What :func:`clean_message_text` writes for a publication, alone on its line.
_ARTIFACT_LABEL_LINE = re.compile(r"(?m)^[ \t]*\[artifact(?:: [^\]\n]*)?\][ \t]*(?:\n|$)")

_ATTRIBUTE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_-]*)="([^"]*)"')


def _label(prefix: str, attributes: str, *names: str) -> str:
    """``[prefix: value]`` with the first of ``names`` the tag carries, else ``[prefix]``."""
    values = {name: value.strip() for name, value in _ATTRIBUTE.findall(attributes)}
    value = next((values[name] for name in names if values.get(name)), "")
    value = " ".join(value.replace("]", ")").split())
    return f"[{prefix}: {value}]" if value else f"[{prefix}]"


def _describe_tags(text: str) -> str:
    text = _INLINE_ARTIFACT.sub(lambda match: _label("artifact", match.group(1), "title", "id"), text)
    return INSERT_SCREENSHOT_TAG_RE.sub(lambda match: _label("image", match.group(1), "title"), text)


def clean_message_text(text: str) -> str:
    """``text`` with TwiCC's own tags described instead of written, out of code. Cheap when there is none."""
    if "<twicc:" not in text:
        return text
    # Odd parts of the split are the code spans: they stay as they are.
    parts = _CODE.split(text)
    return "".join(part if index % 2 else _describe_tags(part) for index, part in enumerate(parts))


def without_artifact_labels(text: str) -> str:
    """``text`` without the lines that only describe a published artifact (what a summary must not read)."""
    return _ARTIFACT_LABEL_LINE.sub("", text) if "[artifact" in text else text


class CompactionRequest(NamedTuple):
    """A ``/compact`` command: its instructions (Claude Code only, Codex takes none), else None."""

    prompt: str | None


_COMPACT_COMMAND = re.compile(r"/compact(?:\s+(.*))?", re.DOTALL)


def parse_compaction_request(text: str, provider: str | None) -> CompactionRequest | None:
    """The compaction ``text`` asks for (a sent text, or a message text as above), else None.

    The command must be the whole message, as the CLIs read it: ``/compact`` alone or followed by its instructions.
    """
    match = _COMPACT_COMMAND.fullmatch(text.strip()) if text else None
    if match is None:
        return None
    prompt = (match.group(1) or "").strip() or None
    return CompactionRequest(prompt if provider == "claude_code" else None)
