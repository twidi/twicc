"""Sender-identity header for inter-session messages.

A ``send-message`` / ``send-messages`` issued from inside a TwiCC agent (CLI
via PID ancestry, MCP via the forced session id) delivers its text under a
header naming the sender — otherwise the recipient would read an anonymous
follow-up from "the user" with no way to tell another session is talking. A
human invoking the CLI from a plain shell resolves to no caller session, so no
header is added.

The header is a ``::`` line block (see the colon-block primitive in
``frontend/src/utils/markdownContainers.js``): a two-colon marker means "this
line and nothing else", so nothing wraps the message — the text below stays
ordinary top-level markdown and renders like any other message. The first word
of the label names the block (here ``message``). TwiCC styles the header line
on its own; in any other reader it stays plain, readable text.

The wording encodes the spawn-tree relation between the two sessions, from
the recipient's point of view::

    :: message from your spawned session `<id>` ("**<title>**")   # caller is a child of the recipient
    :: message from your parent session `<id>` ("**<title>**")    # caller spawned the recipient
    :: message from a sibling session `<id>` ("**<title>**")      # same spawner on both sides
    :: message from another session `<id>` ("**<title>**")        # no spawn-tree relation

    <original text>

The ``("**<title>**")`` segment is omitted when the caller has no title yet.
"""

from __future__ import annotations

import re

TITLE_MAX_CHARS = 80

# The invariant opening of every header, whatever the relation wording. Readers
# use it to tell an inter-session message from a human one -- see
# :func:`has_sender_header`.
SENDER_HEADER_PREFIX = ":: message from "

_WHITESPACE_RUN_RE = re.compile(r"\s+")

# The title is arbitrary text dropped into a bold span on the header line. Only
# the characters that could break out of it are escaped — emphasis, code, and
# link markers — so the raw line an agent reads stays close to the real title.
_MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]])")


def inline_md(value: str | None, *, max_chars: int = TITLE_MAX_CHARS) -> str:
    """Make arbitrary text safe to interpolate into a ``::`` header line.

    A header owns a single line and mixes generated markdown with values that
    are not ours (a session title, a peer's claimed name), so newlines are
    flattened, the result is truncated, and the markdown specials that could
    break out of a bold span or a code span are escaped. Truncation measures
    the real text, before escaping adds characters. Shared with the peer
    delivery envelope (``core/services/peer_messages.py``).
    """
    flattened = _WHITESPACE_RUN_RE.sub(" ", (value or "")).strip()
    if not flattened:
        return ""
    if len(flattened) > max_chars:
        flattened = flattened[: max_chars - 1] + "…"
    return _MD_SPECIAL_RE.sub(r"\\\1", flattened)


def prefix_sender_header(
    text: str,
    caller,
    *,
    recipient_id: str,
    recipient_spawned_by_id: str | None,
) -> str:
    """Return ``text`` under the sender header, or unchanged.

    ``caller`` is the calling agent's ``Session`` row (or ``None`` when the
    command runs outside a TwiCC agent). No header is added when there is no
    caller, or on a self-send (a session messaging itself needs no
    attribution).
    """
    from twicc.mcp.identity import external_caller
    external = external_caller.get()
    if external is not None:
        name = inline_md(external.name)
        header = f":: message via {name or 'external MCP'}"
        return f"{header}\n\n{text}" if text else header
    if caller is None or caller.id == recipient_id:
        return text

    if caller.spawned_by_id is not None and caller.spawned_by_id == recipient_id:
        relation = "your spawned session"
    elif recipient_spawned_by_id is not None and recipient_spawned_by_id == caller.id:
        relation = "your parent session"
    elif caller.spawned_by_id is not None and caller.spawned_by_id == recipient_spawned_by_id:
        relation = "a sibling session"
    else:
        relation = "another session"

    title = inline_md(caller.title)
    suffix = f' ("**{title}**")' if title else ""

    header = f"{SENDER_HEADER_PREFIX}{relation} `{caller.id}`{suffix}"
    # An attachments-only message has no text: the header is then the whole
    # body, so don't append a dangling blank line.
    return f"{header}\n\n{text}" if text else header


def has_sender_header(text: str) -> bool:
    """Tell whether ``text`` is an inter-session message (agent to agent).

    The header always opens the message, so a prefix test is enough. It reads
    the text as stored on the ``SessionItem`` -- already scrubbed of the
    ``<twicc:context>`` / ``<twicc:instruction>`` blocks at ingestion (see
    :mod:`twicc.context_injection`), so the header really sits at the front.

    Two shapes are deliberately NOT covered, because they carry no header: a
    session messaging itself, and the initial prompt of a spawned session.
    """
    return text.lstrip().startswith((SENDER_HEADER_PREFIX, ":: message via "))
