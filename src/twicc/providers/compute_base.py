"""
Provider-agnostic compute primitives for session items.

Each provider stores its native JSONL lines in :class:`~twicc.core.models.SessionItem.content`
without conversion at ingestion time. This module owns the shared *output*
structures every provider produces from those lines (kind, display level,
group membership, agent / tool-result link records) and the machinery that
operates on those outputs (group state machine, agent prompt cache, batch
orchestration, watcher live sync).

The :class:`BaseSessionCompute` class is the compute surface every provider
inherits. Each provider lives under ``providers/<name>/compute.py`` and
overrides the extraction methods (``compute_item_kind``,
``extract_tool_result_info``, ...) by parsing its own native format.
Higher-level orchestration methods (group state, link creation, batch
compute, watcher sync) are concrete in this base class — they are
implemented in terms of the abstract extractors so each provider gets
them for free once its parsing layer is in place.

Unlike :class:`~twicc.providers.helpers.BaseProviderHelpers`, there is no
cross-provider registry for the compute classes: each provider's
orchestrator instantiates and uses its own subclass directly. The compute
surface is internal plumbing — nothing outside a provider's ingestion
path needs to dispatch dynamically by ``Provider`` enum.
"""

from __future__ import annotations

import base64
import copy
import logging
import os
import re
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, UTC
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal, NamedTuple

import orjson
from django.core.exceptions import MultipleObjectsReturned
from django.db import transaction
from django.db.models import F, QuerySet

from twicc.context_injection import strip_context_blocks_in_place
from twicc.core.enums import ItemDisplayLevel, ItemKind, Provider
from twicc.core.models import AgentLink, Session, SessionItem, SessionType, Share, ToolResultLink
from twicc.core.session_queries import TOOL_STATE_ANNOTATIONS
from twicc.git import is_git_root_related, read_head_branch, resolve_git_from_path
from twicc.providers.goals import GoalEvent, apply_goal_event, preserve_dismissed_flags
from twicc.providers.plan_docs import (
    FOLDED_SOURCES,
    DocEditEvent,
    apply_doc_edit_events,
    fold_concurrent_entries,
    refresh_entries_existence,
)
from twicc.projects import (
    ensure_project_directory,
    ensure_project_git_root,
    get_project_directory,
    get_project_git_root,
    update_project_metadata,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Shared NamedTuples — broadcast updates and extraction outputs
# =============================================================================


class AgentLinkUpdate(NamedTuple):
    """Describes a new AgentLink creation to broadcast to the frontend."""
    parent_session_id: str
    agent_id: str
    tool_use_id: str
    tool_use_line_num: int
    is_background: bool
    started_at: datetime | None


class WorkflowLinkUpdate(NamedTuple):
    """Describes a new Workflow tool-link (tool_use_id ↔ run_id) to broadcast.

    The in-chat counterpart of :class:`AgentLinkUpdate`: it lets a ``Workflow``
    tool_use show a "View Workflow" button routing to its run. Captured from the
    tool_result's ``toolUseResult.runId`` (Claude-Code-only).
    """
    session_id: str
    tool_use_id: str
    run_id: str


class ToolResultUpdate(NamedTuple):
    """Describes a tool completion state change to broadcast to the frontend."""
    session_id: str
    tool_use_id: str
    result_count: int
    completed_at: datetime | None  # Timestamp of the latest tool_result
    extra: str | None = None  # Optional extra data (e.g. diff stats JSON for Edit tools)
    error: str | None = None  # Error message from tool_result (None = no error)
    # Line numbers of every persisted ``ToolResultLink`` for this tool_use,
    # ordered ASC. Codex tools have two — ``event_msg.*_end`` plus the
    # LLM-facing ``*_call_output`` — at non-adjacent line numbers, so
    # helpers iterate the list to find the row they need. Single-result
    # tools (Claude Code's Edit / Write / …) carry a single-element list.
    tool_result_line_nums: tuple[int, ...] = ()


class AgentStoppedUpdate(NamedTuple):
    """Describes a subagent session whose process has naturally finished."""
    agent_session_id: str
    stopped_at: datetime


class ComputeApplyResult(NamedTuple):
    """Outcome of applying one background-compute result."""

    outcome: Literal["applied", "superseded", "missing"]
    folded_ancestor_id: str | None = None


class ToolResultInfo(NamedTuple):
    """Provider-neutral output of ``extract_tool_result_info``.

    Each provider populates this from its own native tool-result block:
    Claude reads ``content[*].type == "tool_result"`` blocks; other
    providers parse their equivalents and fill the same shape.
    """
    tool_use_id: str | None
    is_error: bool
    error_text: str | None


class ItemGroupInfo(NamedTuple):
    """Group assignment for a single item, returned by :meth:`GroupState.process_item`."""
    group_head: int | None
    group_tail: int | None
    closed_items: list[Any] = []  # Items whose group was just closed


class ToolUseEntry(NamedTuple):
    """Entry stored in the batch orchestrator's ``tool_use_map``.

    Indexed by tool_use_id (Claude's ``id``, Codex's ``call_id``), this carries
    everything a provider may need to resolve a later tool_result back to its
    originating tool_use — including the parsed payload of the tool_use line
    so providers can read its arguments without re-parsing or hitting the DB.
    """
    line_num: int
    tool_name: str
    parsed_json: dict


class ContentAnalysis(NamedTuple):
    """
    Single-pass extraction output used by the batch compute path.

    Replaces multiple individual content traversals with one structured
    payload. The shape is provider-neutral; each provider's
    :meth:`BaseSessionCompute.analyze_content` populates the fields from
    its own native content layout.
    """
    # Content visibility (any visible block: text, document, image, ...)
    has_visible_content: bool
    # First text block's text value, or None when missing
    text_content: str | None
    # Content is a string starting with a system XML prefix
    is_system_xml: bool
    # User message has a tool_result in content
    has_tool_result: bool
    # First tool_result's tool_use_id
    tool_result_id: str | None
    # Error from first tool_result (None when no error)
    tool_result_error: str | None
    # tool_use_id -> tool_name mapping
    tool_use_entries: dict[str, str]
    # [(tool_use_id, is_background)] for agent-spawning tool calls
    task_tool_uses: list[tuple[str, bool]]
    # Absolute file paths from tool_use inputs (for git resolution)
    file_paths: list[str]
    # Raw prefix/suffix detection (caller filters by kind)
    has_prefix: bool
    has_suffix: bool
    # (tool_use_id, agent_id, is_async) when the tool_result references a
    # spawned agent. ``is_async`` is True when the result itself signals an
    # asynchronous launch (e.g. Claude Code's ``toolUseResult.isAsync`` ack):
    # since the async-by-default CLI dropped the ``run_in_background`` input
    # flag, the ack is the only reliable backgroundness signal.
    tool_result_agent_info: tuple[str, str, bool] | None


# Shared empty constants used by every provider's ``analyze_content`` to
# avoid allocating fresh empty containers for items that don't carry the
# corresponding payload. MUST NOT be mutated.
_EMPTY_TOOL_USE_ENTRIES: dict[str, str] = {}
_EMPTY_TASK_TOOL_USES: list[tuple[str, bool]] = []
_EMPTY_FILE_PATHS: list[str] = []

_EMPTY_ANALYSIS = ContentAnalysis(
    has_visible_content=False,
    text_content=None,
    is_system_xml=False,
    has_tool_result=False,
    tool_result_id=None,
    tool_result_error=None,
    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
    task_tool_uses=_EMPTY_TASK_TOOL_USES,
    file_paths=_EMPTY_FILE_PATHS,
    has_prefix=False,
    has_suffix=False,
    tool_result_agent_info=None,
)


class ImageHit(NamedTuple):
    """One base64 image located in a prior ``tool_result`` item.

    Produced by :meth:`BaseSessionCompute.iter_images_backward` when the
    ``<twicc:insert-screenshot />`` substitution walks back through a
    session's history. Carries everything the substitution helper needs
    to save the image to disk and emit a markdown image link:

    - ``tool_use_id`` of the originating tool_use (used in the filename
      for deterministic, idempotent saves);
    - ``media_type`` and base64 ``data`` to reconstruct the bytes;
    - ``source_line_num`` of the SessionItem that carried the image;
    - ``source_timestamp`` of that item (used for the filename prefix;
      ``None`` when the source item has no timestamp).
    """

    tool_use_id: str
    media_type: str
    data: str
    source_line_num: int
    source_timestamp: datetime | None


# =============================================================================
# Pure utilities — shared helpers that don't depend on provider parsing
# =============================================================================


# Maximum length for extracted titles before truncation (with ellipsis).
# Provider-agnostic — every provider's title must fit this budget so the
# UI rendering stays consistent.
TITLE_MAX_LENGTH = 200


_MARKDOWN_PATTERNS = [
    (re.compile(r'^#{1,6}\s+', re.MULTILINE), ''),  # Headers: # ## ### etc.
    (re.compile(r'\*\*(.+?)\*\*'), r'\1'),  # Bold: **text**
    (re.compile(r'__(.+?)__'), r'\1'),  # Bold: __text__
    (re.compile(r'\*(.+?)\*'), r'\1'),  # Italic: *text*
    (re.compile(r'_(.+?)_'), r'\1'),  # Italic: _text_
    (re.compile(r'~~(.+?)~~'), r'\1'),  # Strikethrough: ~~text~~
    (re.compile(r'`(.+?)`'), r'\1'),  # Inline code: `text`
    (re.compile(r'^\s*[-*+]\s+', re.MULTILINE), ''),  # Unordered list markers
    (re.compile(r'^\s*\d+\.\s+', re.MULTILINE), ''),  # Ordered list markers
    (re.compile(r'^\s*>\s*', re.MULTILINE), ''),  # Blockquotes
    (re.compile(r'\[([^\]]+)\]\([^)]+\)'), r'\1'),  # Links: [text](url) -> text
]


def strip_markdown(text: str) -> str:
    """Remove common markdown formatting from ``text``."""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def parse_timestamp_to_datetime(timestamp: str) -> datetime | None:
    """
    Parse an ISO timestamp string to a UTC-aware :class:`datetime`.

    Returns ``None`` for empty input or unparseable values. Handles the
    ``Z`` suffix by rewriting it as ``+00:00``.
    """
    if not timestamp:
        return None

    try:
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None


# =============================================================================
# Inline screenshot insertion — shared utilities
# =============================================================================


# Pattern recognised in assistant text output by :func:`substitute_insert_screenshot_tags`.
# Matches ``<twicc:insert-screenshot />`` with any number of
# ``name="value"`` attributes in any order. Recognised attributes are
# ``offset`` (0-indexed count back from the most recent image) and
# ``title`` (used as the markdown alt text and in the missing-image
# placeholder). Unknown attribute names are silently ignored so the
# vocabulary can grow without breaking older agents. The values are
# parsed out separately via :data:`_INSERT_SCREENSHOT_ATTR_RE`.
INSERT_SCREENSHOT_TAG_RE = re.compile(
    r'<twicc:insert-screenshot((?:\s+[a-zA-Z_][a-zA-Z0-9_-]*="[^"]*")*)\s*/>'
)

# Parses ``name="value"`` pairs out of the attribute blob captured by
# :data:`INSERT_SCREENSHOT_TAG_RE`. Values cannot contain literal ``"``
# (intentional — the agent is expected to drop or rephrase rather than
# escape).
_INSERT_SCREENSHOT_ATTR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_-]*)="([^"]*)"')

# Replacement when no image is available at the requested offset.
INSERT_SCREENSHOT_MISSING_PLACEHOLDER = "*[no screenshot available]*"

# Default alt text used in the rendered ``![…](url)`` markdown when the
# agent doesn't provide a ``title`` attribute.
_DEFAULT_SCREENSHOT_ALT = "screenshot"

# Extension picked for each ``image/...`` media type. Anything not listed
# falls back to the second tuple member after the slash (e.g. ``image/avif``
# would land as ``avif``); fallback is fine for serving but the artifacts
# backend only renders the explicit list — same set as the addendum.
_MEDIA_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


def is_base64_image(value: Any) -> tuple[str, str] | None:
    """Detect a base64 image content block.

    Mirrors ``detectContentBlockSource`` in
    ``frontend/src/components/json/JsonHumanView.vue``: a dict with
    ``type`` (anything but ``"text"``), ``media_type`` starting with
    ``image/`` and a ``data`` field. Returns ``(media_type, data)`` on a
    match, ``None`` otherwise.

    Provider-agnostic: works on the Claude SDK source shape
    (``{type: "base64", media_type, data}``) and on any other emitter
    that uses the same wrapper.
    """
    if not isinstance(value, dict):
        return None
    media_type = value.get("media_type")
    data = value.get("data")
    typ = value.get("type")
    if not isinstance(media_type, str) or not isinstance(data, str) or not typ:
        return None
    if typ == "text":
        return None
    if not media_type.startswith("image/"):
        return None
    return media_type, data


def _extension_for_media_type(media_type: str) -> str:
    """Return the on-disk extension for an ``image/...`` media type."""
    ext = _MEDIA_TYPE_TO_EXT.get(media_type)
    if ext is not None:
        return ext
    # Unknown image subtype: derive from the slash, strip suffixes like
    # ``+xml`` (e.g. ``image/svg+xml`` already covered above, defensive).
    _, _, subtype = media_type.partition("/")
    subtype = subtype.split("+", 1)[0]
    return subtype or "bin"


def _escape_markdown_alt(text: str) -> str:
    """Escape characters that would break a markdown ``![alt](url)`` link.

    Markdown parsers are strict about the alt-text bracket pair: ``]``
    would terminate the alt segment early, and an unmatched inner ``[``
    can trigger nested-link parsing that breaks the surrounding image
    syntax altogether (the parser bails and renders the raw markdown as
    plain text — see the title roundtrip test for the exact failure
    case). ``\\`` would partially escape the following character.

    Order matters: replace ``\\`` first so that the backslashes
    introduced by ``[`` / ``]`` escaping below don't get double-escaped.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


class _TagAttrs(NamedTuple):
    """Parsed attributes of a single ``<twicc:insert-screenshot />`` tag."""
    offset: int
    title: str | None  # ``None`` ≡ no ``title=…`` attribute on the tag


def _parse_tag_attrs(blob: str) -> _TagAttrs:
    """Pull ``offset`` and ``title`` out of a tag's attribute blob.

    Unknown attribute names are silently dropped. Malformed ``offset``
    values (non-integer, negative) fall back to ``0`` rather than
    raising — the substitution is best-effort, never a fatal failure,
    and a negative index would otherwise wrap around to ``hits[-1]``
    via Python's indexing rules.
    """
    offset = 0
    title: str | None = None
    for name, value in _INSERT_SCREENSHOT_ATTR_RE.findall(blob):
        if name == "offset":
            try:
                parsed_offset = int(value)
            except ValueError:
                parsed_offset = 0
            offset = max(parsed_offset, 0)
        elif name == "title":
            title = value
    return _TagAttrs(offset=offset, title=title)


def _artifact_filename(hit: ImageHit, offset: int) -> str:
    """Compose the deterministic filename for an extracted image.

    Format: ``{iso_timestamp}-{tool_use_id}-off{N}.{ext}``. Determinism
    matters: re-processing the same SessionItem must point to the same
    file so the disk save short-circuits when the file is already there.

    The ``iso_timestamp`` is taken from the source SessionItem's own
    timestamp (the moment the tool_result landed); when the source item
    carries no timestamp (rare), ``unknowntime`` is used as a stable
    sentinel rather than a wall-clock fallback.
    """
    if hit.source_timestamp is not None:
        # Match the addendum's documented ``YYYY-MM-DD-HH-MM-SS`` prefix.
        iso = hit.source_timestamp.strftime("%Y-%m-%d-%H-%M-%S")
    else:
        iso = "unknowntime"
    # ``tool_use_id`` is short and ASCII-safe in every provider we ship;
    # the artifacts backend validates the filename charset anyway and
    # would reject anything exotic at the 404 layer. Defensive fallback
    # for the (rare) case where the provider couldn't extract one.
    tool_use_id = hit.tool_use_id or "unknown"
    ext = _extension_for_media_type(hit.media_type)
    return f"{iso}-{tool_use_id}-off{offset}.{ext}"


def substitute_insert_screenshot_tags(
    text: str,
    *,
    session_id: str,
    images_provider: Callable[[int], Iterator[ImageHit]],
    artifacts_dir: Path,
) -> str:
    """Replace every ``<twicc:insert-screenshot />`` tag in ``text``.

    Walk the tag occurrences, ask ``images_provider`` for up to ``max_offset + 1``
    images, then rewrite each tag inline:

    - on success: ``![<alt>](/artifacts/<session_id>/<filename>)`` where
      ``<alt>`` is the tag's ``title="…"`` (escaped for markdown safety)
      when provided, else ``"screenshot"``; the image is saved to
      ``artifacts_dir / session_id / filename`` (the directory is
      created if missing, and an existing file with the same
      deterministic name is left untouched);
    - on miss: :data:`INSERT_SCREENSHOT_MISSING_PLACEHOLDER`, augmented
      with ``"*[no screenshot available: <title>]*"`` when the tag
      carried a title.

    ``images_provider`` is a callable returning an iterator of
    :class:`ImageHit` ordered most-recent-first; called once with the
    number of images needed (``max_offset + 1``). Returning fewer hits
    than requested is fine — every tag whose offset isn't reached lands
    on the placeholder.

    Returns ``text`` unchanged if no tag is present.
    """
    matches = list(INSERT_SCREENSHOT_TAG_RE.finditer(text))
    if not matches:
        return text

    # Resolve each tag's parsed attributes (offset defaults to 0; title
    # to None when absent — distinct from an empty title="").
    attrs_per_match = [_parse_tag_attrs(m.group(1)) for m in matches]
    max_offset = max(a.offset for a in attrs_per_match)

    # Pull just enough images to cover the highest offset requested. The
    # provider walks the session history backward, yields lazily, and
    # caps its own DB query — so asking for ``max_offset + 1`` images
    # bounds the work even when the session has many.
    needed = max_offset + 1
    hits: list[ImageHit] = []
    for hit in images_provider(needed):
        hits.append(hit)
        if len(hits) >= needed:
            break

    session_dir: Path | None = None  # lazily created on first save

    def _replacement_for(attrs: _TagAttrs) -> str:
        nonlocal session_dir
        if attrs.offset >= len(hits):
            # Mirror the title back in the placeholder so a message with
            # several distinctly-titled missing images stays readable.
            if attrs.title:
                return f"*[no screenshot available: {attrs.title}]*"
            return INSERT_SCREENSHOT_MISSING_PLACEHOLDER
        hit = hits[attrs.offset]
        filename = _artifact_filename(hit, attrs.offset)
        if session_dir is None:
            session_dir = artifacts_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
        target = session_dir / filename
        if not target.exists():
            try:
                payload = base64.b64decode(hit.data, validate=False)
            except (ValueError, base64.binascii.Error):
                logger.warning(
                    "Failed to decode base64 image for tool_use_id=%s "
                    "(session=%s, source_line=%d)",
                    hit.tool_use_id, session_id, hit.source_line_num,
                )
                return INSERT_SCREENSHOT_MISSING_PLACEHOLDER
            try:
                target.write_bytes(payload)
            except OSError:
                logger.exception(
                    "Failed to write extracted screenshot to %s", target,
                )
                return INSERT_SCREENSHOT_MISSING_PLACEHOLDER
        alt_raw = attrs.title or _DEFAULT_SCREENSHOT_ALT
        return f"![{_escape_markdown_alt(alt_raw)}](/artifacts/{session_id}/{filename})"

    # Rebuild the text by walking the original tag matches in order.
    out: list[str] = []
    cursor = 0
    for m, attrs in zip(matches, attrs_per_match, strict=True):
        out.append(text[cursor:m.start()])
        out.append(_replacement_for(attrs))
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out)


# =============================================================================
# Agent link caches — cross-call memoization
# =============================================================================


# (session_id, agent_id) pairs whose AgentLink has already been created.
# Prevents redundant DB writes and short-circuits the matching loops.
AGENTS_LINKS_DONE_CACHE: set[tuple[str, str]] = set()

# Cached subagent prompts keyed by (parent_session_id, agent_id), used by
# the watcher to match a freshly-synced subagent against an existing Task
# tool_use in the parent session.
AGENTS_PROMPT_CACHE: dict[tuple[str, str], str] = {}


def mark_agent_link_done(session_id: str, agent_id: str) -> None:
    """Record that the AgentLink for this subagent has been created."""
    AGENTS_LINKS_DONE_CACHE.add((session_id, agent_id))
    uncache_agent_prompt(session_id, agent_id)


def is_agent_link_done(session_id: str, agent_id: str) -> bool:
    """Return ``True`` when the AgentLink for this subagent already exists."""
    return (session_id, agent_id) in AGENTS_LINKS_DONE_CACHE


def get_cached_agent_prompt(session_id: str, agent_id: str) -> str | None:
    """Read a cached subagent prompt, or ``None`` when missing."""
    return AGENTS_PROMPT_CACHE.get((session_id, agent_id))


def cache_agent_prompt(session_id: str, agent_id: str, prompt: str) -> None:
    """Store a subagent prompt for later matching against parent tool_uses."""
    AGENTS_PROMPT_CACHE[(session_id, agent_id)] = prompt


def uncache_agent_prompt(session_id: str, agent_id: str) -> None:
    """Drop a cached subagent prompt (e.g. after a successful link)."""
    AGENTS_PROMPT_CACHE.pop((session_id, agent_id), None)


# =============================================================================
# GroupState — provider-agnostic state machine for collapsible groups
# =============================================================================


class GroupState:
    """
    Tracks group state during sequential item processing.

    A group is "open" when:

    - the previous item was COLLAPSIBLE, or
    - the previous ALWAYS item had a collapsible suffix (potential group start).

    The state machine operates purely on already-extracted metadata
    (``display_level``, ``has_prefix``, ``has_suffix``) so it is
    provider-agnostic — each provider populates those flags from its
    own native content layout.

    Usage::

        state = GroupState()
        for item in items:
            info = state.process_item(item.line_num, display_level, has_prefix, has_suffix, item)
            item.group_head = info.group_head
            item.group_tail = info.group_tail
        state.finalize()  # Close any pending group
    """

    def __init__(self) -> None:
        # Current open group (COLLAPSIBLE items accumulating)
        self._group_head: int | None = None
        self._group_items: list[tuple[int, Any]] = []  # (line_num, item_ref)

        # Pending ALWAYS with suffix (might start a group)
        self._pending_suffix: tuple[int, Any] | None = None  # (line_num, item_ref)

    def has_open_group(self) -> bool:
        """Check if there's an open group that the next item could join."""
        return self._group_head is not None or self._pending_suffix is not None

    def get_current_head(self) -> int | None:
        """Get the head of the current open group."""
        if self._group_head is not None:
            return self._group_head
        if self._pending_suffix is not None:
            return self._pending_suffix[0]
        return None

    def process_item(
        self,
        line_num: int,
        display_level: ItemDisplayLevel,
        has_prefix: bool,
        has_suffix: bool,
        item_ref: Any = None,
    ) -> ItemGroupInfo:
        """
        Process a single item and return its group assignment.

        Args:
            line_num: The item's line number
            display_level: ALWAYS, COLLAPSIBLE, or DEBUG_ONLY
            has_prefix: True if ALWAYS item has collapsible prefix
            has_suffix: True if ALWAYS item has collapsible suffix
            item_ref: Reference to item object (for batch updates)

        Returns:
            ItemGroupInfo with group_head and group_tail assignments
        """
        if display_level == ItemDisplayLevel.DEBUG_ONLY:
            # DEBUG_ONLY: transparent to groups, no participation
            return ItemGroupInfo(group_head=None, group_tail=None)

        if display_level == ItemDisplayLevel.COLLAPSIBLE:
            return self._process_collapsible(line_num, item_ref)

        # ALWAYS
        return self._process_always(line_num, has_prefix, has_suffix, item_ref)

    def _process_collapsible(self, line_num: int, item_ref: Any) -> ItemGroupInfo:
        """Process a COLLAPSIBLE item."""
        # Check if we're connecting to a pending ALWAYS suffix
        if self._pending_suffix is not None:
            suffix_line, suffix_ref = self._pending_suffix
            self._pending_suffix = None

            # The ALWAYS suffix starts this group
            self._group_head = suffix_line
            self._group_items = [(suffix_line, suffix_ref), (line_num, item_ref)]
            return ItemGroupInfo(group_head=suffix_line, group_tail=None)

        # Join existing group or start new one
        if self._group_head is not None:
            # Continue existing group
            self._group_items.append((line_num, item_ref))
            return ItemGroupInfo(group_head=self._group_head, group_tail=None)
        else:
            # Start new group
            self._group_head = line_num
            self._group_items = [(line_num, item_ref)]
            return ItemGroupInfo(group_head=line_num, group_tail=None)

    def _process_always(
        self, line_num: int, has_prefix: bool, has_suffix: bool, item_ref: Any
    ) -> ItemGroupInfo:
        """Process an ALWAYS item."""
        result_head: int | None = None
        closed_items: list[Any] = []
        joined_via_prefix = False

        # Handle prefix: can join an open group
        if has_prefix and self.has_open_group():
            result_head = self.get_current_head()
            joined_via_prefix = True

            # Add to group items for tail update (but track that this is the joining ALWAYS)
            if self._pending_suffix is not None:
                # Connect pending suffix to this prefix
                suffix_line, suffix_ref = self._pending_suffix
                self._group_items = [(suffix_line, suffix_ref)]
                self._group_head = suffix_line
                self._pending_suffix = None
            # Don't add the current ALWAYS to _group_items - it joins but doesn't get group_tail

        # ALWAYS always terminates any group before it
        if self._group_items:
            # Determine tail: this item if it joined via prefix, else last item in group
            if joined_via_prefix:
                tail = line_num
            else:
                tail = self._group_items[-1][0]

            # Update all items in the group (not including current ALWAYS)
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    closed_items.append(ref)

            # Reset group state
            self._group_items = []
            self._group_head = None

        # Also close pending suffix if not joined by this item's prefix
        if self._pending_suffix is not None and not joined_via_prefix:
            # Pending suffix was not connected, close it as orphan
            suffix_line, suffix_ref = self._pending_suffix
            if suffix_ref is not None:
                # Suffix stays orphan (group_tail already None)
                closed_items.append(suffix_ref)
            self._pending_suffix = None

        # Handle suffix: might start a new group
        if has_suffix:
            self._pending_suffix = (line_num, item_ref)

        # ALWAYS item itself doesn't get group_tail from this operation
        # group_tail for ALWAYS is only set when its suffix connects to something later
        return ItemGroupInfo(group_head=result_head, group_tail=None, closed_items=closed_items)

    def finalize(self) -> list[Any]:
        """
        Finalize any open groups at end of processing.

        Returns:
            List of item references that were updated (for batch save)
        """
        updated = []

        # Close any open COLLAPSIBLE group
        if self._group_items:
            tail = self._group_items[-1][0]
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    updated.append(ref)
            self._group_items = []
            self._group_head = None

        # Pending ALWAYS suffix stays orphan (group_tail = None)
        if self._pending_suffix is not None:
            _, ref = self._pending_suffix
            if ref is not None:
                updated.append(ref)
            self._pending_suffix = None

        return updated


# =============================================================================
# BaseSessionCompute — provider-agnostic compute surface
# =============================================================================


class BaseSessionCompute:
    """
    Abstract per-provider session compute.

    Each provider subclasses this and overrides the extraction methods
    (the ones that parse a native JSONL line into TwiCC's neutral
    structures). The orchestration methods (group state machine, agent /
    tool-result link creation, batch compute, watcher live sync) will be
    implemented concretely in this base class in later steps so every
    provider inherits them for free.

    Step 1 (this commit) only declares the surface — every method raises
    :class:`NotImplementedError`. Steps 2-4 incrementally fill it in:

    - Step 2 wires the Claude Code subclass to the live extraction +
      :meth:`compute_item_metadata_live` and link methods.
    - Step 3 migrates the batch path (:meth:`compute_session_metadata`,
      :meth:`apply_session_complete`).
    - Step 4 migrates the watcher's :meth:`sync_session_items_from_file`.
    """

    provider: ClassVar[Provider]

    # ------------------------------------------------------------------
    # Extraction surface — overridden by each provider
    # ------------------------------------------------------------------

    def transform_inline(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> str | None:
        """Rewrite a parsed item in place before metadata computation.

        Template method shared by every provider. It first scrubs any injected
        TwiCC blocks (``<twicc:context>``, ``<twicc:instruction>``) from the user
        text (a generic, cross-provider step — see :mod:`twicc.context_injection`),
        then runs the provider's own rewrites (:meth:`_transform_inline_provider`).
        Stripping first means the provider logic and the stored
        ``SessionItem.content`` only ever see the clean text, regardless of what
        the provider does. Both steps mutate ``parsed_json`` in place, so the
        downstream computation (``analyze_content``, title extraction, ...)
        operates on the cleaned, rewritten item.

        The strip keeps those blocks out of the stored content — and therefore
        out of the UI, full-text search, session title and message browser. The
        agent still saw the block in its turn input and replayed rollout; only
        the persisted copy is cleaned.

        Returns the new serialised JSON string when anything changed (the
        caller updates ``SessionItem.content``), or ``None`` when the item was
        left untouched.
        """
        stripped = strip_context_blocks_in_place(parsed_json)
        provider_content = self._transform_inline_provider(
            parsed_json,
            session_id=session_id,
            line_num=line_num,
            in_memory_items=in_memory_items,
        )
        # A provider rewrite serialises ``parsed_json`` in place, so its return
        # already reflects the earlier strip; otherwise fall back to the strip's
        # own serialisation when only the strip fired.
        if provider_content is not None:
            return provider_content
        if stripped:
            return orjson.dumps(parsed_json).decode()
        return None

    def _transform_inline_provider(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> str | None:
        """
        Provider-specific inline rewrite hook (invoked by :meth:`transform_inline`).

        Optionally rewrite a parsed item in place before metadata computation.
        Used by Claude Code to normalise legacy or non-standard formats
        (``<task-notification>``, ``<local-command-stdout>``) into the
        standard tool_result / assistant_message shape that the rest of
        the compute pipeline expects, and by every provider to substitute
        ``<twicc:insert-screenshot />`` tags in assistant text against
        prior tool_result images.

        Implementations MUST mutate ``parsed_json`` in place when they rewrite
        it (and return the new serialised JSON string); return ``None`` when
        the item is left untouched. That in-place contract lets the generic
        context-tag strip in :meth:`transform_inline` compose with the rewrite.

        ``session_id`` is the SessionItem's owning session, threaded for
        provider hooks (task enrichment, screenshot substitution) that
        need to scope DB lookups or artifact paths to that session.
        Providers historically read ``parsed_json.get('sessionId')``;
        prefer the explicit argument — it stays correct if the agent
        emits a line missing the legacy field.

        ``in_memory_items`` carries ``(line_num, timestamp, parsed)``
        tuples for items already processed in the current live-watcher
        batch but not yet committed to the DB. Used by the screenshot
        substitution helper so a tag can resolve against tool_results
        that landed in the same batch as the assistant message
        referencing them. The base value is ``None`` (batch mode: every
        prior item is already in the DB).
        """
        raise NotImplementedError

    def analyze_content(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> ContentAnalysis:
        """
        Single-pass content extraction used by the batch compute path.

        Returns a :class:`ContentAnalysis` populated from the provider's
        native content layout. Each provider produces the same neutral
        shape so the batch orchestration stays format-agnostic.

        ``session_id`` lets providers stash per-session state if needed
        (see :meth:`begin_session_compute` / :meth:`end_session_compute`).
        ``tool_use_map`` is the orchestrator's batch-only map of every
        tool_use seen so far; providers may inspect it to resolve cross-line
        relationships (e.g. Codex's exec_command/write_stdin pairing).
        """
        raise NotImplementedError

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        """Determine the :class:`ItemKind` for a parsed JSONL line, or ``None``."""
        raise NotImplementedError

    def extract_tasks_payload(self, parsed_json: dict) -> dict | None:
        """Return the task/todo/plan state carried by this JSONL line, in the
        cross-provider common shape, or ``None`` when the line carries none.

        Returned payload (provider-agnostic core; the caller wraps it with
        ``provider`` / ``line`` / ``updated_at`` via
        :meth:`build_tasks_snapshot`)::

            {"source": <tool name>,
             "items": [{"status": str, "content"?: str, "activeForm"?: str}, ...],
             "explanation": <str | None>}

        Every task-bearing line carries the FULL list (a full replacement for
        Claude ``TodoWrite`` / Codex ``update_plan``, or a full snapshot for
        Claude's incremental ``Task*`` tools), so the caller keeps the last
        non-``None`` result as the session's current complete state. Called on
        every line in both compute paths, after ``transform_inline`` (so Claude
        Code's ``twiccTasksData`` enrichment is already present).

        Default: the provider has no task model — returns ``None``.
        """
        return None

    def build_tasks_snapshot(
        self, parsed_json: dict, *, line_num: int, timestamp: datetime | None
    ) -> dict | None:
        """Wrap :meth:`extract_tasks_payload` with the storage envelope used by
        :attr:`Session.tasks` (``provider`` + ``line`` + ``updated_at`` meta).

        Returns ``None`` when the line carries no task/plan state, so callers
        keep the previous snapshot untouched.
        """
        payload = self.extract_tasks_payload(parsed_json)
        if payload is None:
            return None
        return {
            'provider': self.provider.value,
            'line': line_num,
            'updated_at': timestamp.isoformat() if timestamp else None,
            **payload,
        }

    def extract_doc_edit_events(self, parsed_json: dict, *, cwd: str | None) -> list[DocEditEvent]:  # noqa: ARG002
        """Return the plan-doc writes/deletes this JSONL line carries, if any.

        Providers inspect their file-edit tool calls (Claude ``Write``/``Edit``,
        Codex ``apply_patch``) and shell commands (via
        :func:`~twicc.providers.plan_docs.extract_shell_write_targets`),
        cwd-join relative targets (``cwd`` is the loop's freshest known value;
        targets that stay relative because no cwd is known must be dropped)
        and keep only paths passing
        :func:`~twicc.providers.plan_docs.is_plan_doc_path`. Called on every
        line in both compute paths, for main sessions and subagents alike —
        subagent events are folded into the top-level ancestor's
        ``plan_paths`` (source ``subagent``), never stored on the subagent's
        own row. Default: none.
        """
        return []

    def extra_doc_edit_events(
        self, session: Session, *, last_slug: str | None,  # noqa: ARG002
    ) -> list[tuple[DocEditEvent, datetime | str | None]]:
        """Provider hook: end-of-compute filesystem-derived plan-doc events.

        Runs in the background-compute worker only (it may stat the disk),
        right before the authoritative ``plan_paths`` rebuild — Claude Code
        probes the native plan file (``<claude home>/plans/<slug>.md``) here,
        using ``last_slug`` accumulated from the replay (the row's ``slug``
        may be stale at this point). Returns ``(event, timestamp)`` pairs.
        Default: none.
        """
        return []

    def extract_goal_event(self, parsed_json: dict) -> GoalEvent | None:
        """Return the goal-lifecycle signal this JSONL line carries, or ``None``.

        Unlike :meth:`extract_tasks_payload` (a last-wins full snapshot), goal
        state is a stream of transitions folded into :attr:`Session.goals` by
        :func:`~twicc.providers.goals.apply_goal_event`. Providers map their
        native lines (Claude ``goal_status`` attachments / ``/goal clear``
        command, Codex ``thread_goal_updated`` / injected ``/goal clear``) to a
        :class:`~twicc.providers.goals.GoalEvent`. Called on every line in both
        compute paths, after :meth:`transform_inline` (so Codex's injected
        ``/goal`` user message is already materialised).

        Default: the provider has no goal model — returns ``None``.
        """
        return None

    def compute_item_display_level(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> int:
        """Determine the :class:`ItemDisplayLevel` for a parsed JSONL line.

        Generic decision tree shared by every provider:

        - ALWAYS for the kinds that the UI never collapses
          (USER_MESSAGE, ASSISTANT_MESSAGE, API_ERROR, COMPACT_SUMMARY).
        - DEBUG_ONLY for SYSTEM items and standalone tool_result items
          (whose payload is reached via :class:`ToolResultLink` instead).
        - COLLAPSIBLE otherwise (meta messages, thinking, tool_use only,
          summaries, file snapshots, ...).

        The only provider-specific call is :meth:`is_tool_result_item`,
        which inspects ``parsed_json`` in the provider's native shape.
        """
        if kind in (
            ItemKind.USER_MESSAGE,
            ItemKind.ASSISTANT_MESSAGE,
            ItemKind.API_ERROR,
            ItemKind.COMPACT_SUMMARY,
            ItemKind.IMAGE,
        ):
            return ItemDisplayLevel.ALWAYS

        if kind == ItemKind.SYSTEM:
            return ItemDisplayLevel.DEBUG_ONLY

        if self.is_tool_result_item(parsed_json):
            return ItemDisplayLevel.DEBUG_ONLY

        return ItemDisplayLevel.COLLAPSIBLE

    def compute_item_metadata(self, parsed_json: dict) -> dict:
        """
        Compute ``{display_level, kind}`` for one item.

        Convenience wrapper that calls :meth:`compute_item_kind` and then
        :meth:`compute_item_display_level`. Providers usually inherit this
        as-is unless they need extra fields in the metadata dict.
        """
        kind = self.compute_item_kind(parsed_json)
        return {
            'display_level': self.compute_item_display_level(parsed_json, kind),
            'kind': kind,
        }

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        """Return the item's timestamp as a UTC-aware ``datetime``, or ``None``."""
        raise NotImplementedError

    def extract_title_from_user_message(self, parsed_json: dict) -> str | None:
        """
        Extract a session title candidate from a user message, or ``None``.

        Generic algorithm shared by every provider:

        1. Pull the raw user-facing text via :meth:`extract_user_message_text`.
        2. Ask the provider whether the text is a command invocation via
           :meth:`format_command_for_title` (e.g. Claude Code's ``<command-…>``
           slash syntax). When it is, the returned string is used verbatim;
           otherwise the raw text is run through :func:`strip_markdown`.
        3. Collapse internal whitespace and truncate to
           :data:`TITLE_MAX_LENGTH` (with an ellipsis when truncated).
        """
        text = self.extract_user_message_text(parsed_json)
        if not text:
            return None
        command_title = self.format_command_for_title(text)
        if command_title is not None:
            cleaned = command_title
        else:
            cleaned = strip_markdown(text).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        if not cleaned:
            return None
        if len(cleaned) > TITLE_MAX_LENGTH:
            return cleaned[:TITLE_MAX_LENGTH] + '…'
        return cleaned

    def format_command_for_title(self, text: str) -> str | None:
        """
        Return the title-friendly rendering of a command invocation, or ``None``.

        Default implementation never recognises a command (returns ``None``)
        so :meth:`extract_title_from_user_message` falls through to the
        plain-text branch. Providers that have a slash-command syntax
        embedded in user messages (Claude Code's ``<command-name>...``)
        override this to extract ``"<name> <args>"`` form.
        """
        return None

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        """
        Return a dict with the runtime environment fields carried by ``parsed_json``.

        Keys (each optional, missing keys are equivalent to ``None``):

        - ``cwd``: working directory recorded for the line
        - ``cwd_git_branch``: native git branch reported alongside ``cwd``
        - ``model``: model identifier last seen
        - ``slug``: session slug last seen
        - ``context_max``: model context window in tokens. Only emitted
          by providers that publish the active window in their JSONL
          (Codex's ``event_msg.task_started.model_context_window``).
          Providers that don't expose it leave the key absent so the
          base loop falls back to the persisted ``Session.context_max``
          — important for Claude Code, where this field is set by the
          user via the agent settings dialog and must not be wiped on
          re-compute.
        """
        raise NotImplementedError

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],
        current_model: str | None,
    ) -> None:
        """
        Compute cost / context usage and assign them on ``item`` in place.

        Handles deduplication via ``seen_message_ids``: cost is only
        assigned the first time a given ``message_id`` is encountered
        (providers that stream multiple lines per API call share the
        same ``message_id``).

        ``current_model`` is the running ``extract_runtime_fields``
        ``model`` accumulated up to and including the current item
        ("last non-null value seen" semantics), so it reflects the model
        in effect when the line was emitted even if the model identifier
        lives on a previous line. Providers whose billable lines don't
        carry the model (e.g. Codex emits costs on
        ``event_msg.token_count`` between two ``turn_context`` lines)
        read the active model from this argument. Providers whose
        billable lines carry their own model (e.g. Claude Code's
        ``message.model``) ignore it.
        """
        raise NotImplementedError

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        """Return ``True`` when the line carries a tool_result block."""
        raise NotImplementedError

    def extract_tool_use_entries(
        self, parsed_json: dict, *, session_id: str
    ) -> dict[str, str]:
        """Return a ``{tool_use_id: tool_name}`` mapping for the line, possibly empty.

        ``session_id`` lets providers route to per-session state when needed.
        """
        raise NotImplementedError

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry] | None = None,
    ) -> ToolResultInfo | None:
        """Return :class:`ToolResultInfo` for the first tool_result, or ``None``.

        ``tool_use_map`` is provided by the batch orchestrator (used when a
        provider needs to resolve relationships across lines, e.g. Codex's
        exec_command/write_stdin pairing). It is ``None`` in live mode
        (``create_tool_result_link_live``) where the map does not exist
        in memory.
        """
        raise NotImplementedError

    def iter_tool_result_image_refs(
        self, parsed_json: dict
    ) -> Iterator[tuple[str, str, str]]:
        """Yield ``(tool_use_id, media_type, data)`` for each base64 image
        carried by this item's tool_result block(s).

        Default: yield nothing. Each provider that emits images inside
        tool_results overrides this to walk its own native content layout
        and surface the underlying base64 payloads to
        :meth:`iter_images_backward`.

        Images are yielded **most-recent-first within a single item**:
        when a tool_result contains several images (e.g. an MCP
        ``browser_batch`` that took two screenshots in one tool call),
        the chronologically later image is yielded first, so it ends up
        at ``offset=0`` in :meth:`iter_images_backward`'s hits list.
        Implementations therefore walk the content blocks in *reverse
        document order*; single-image tool_results (the common case)
        are unaffected.
        """
        return
        yield  # pragma: no cover — placate the type checker

    def image_candidate_queryset(
        self, session_id: str, before_line_num: int
    ) -> QuerySet[SessionItem]:
        """Return a queryset of items that may contain a base64 image,
        ordered ``line_num`` DESC, strictly before ``before_line_num``.

        Used by :meth:`iter_images_backward` to look up images that
        landed in earlier watcher batches (so the row is already in the
        DB). The default returns every prior item — correct but
        unnecessarily expensive on large sessions. Each provider should
        override with a tight ``content__contains`` filter on a literal
        that occurs in *every* image-bearing tool_result (e.g. Claude
        Code uses ``'"media_type":"image/'``).

        The caller slices the queryset with ``[:images_needed]`` so
        providers don't need to limit themselves — they just have to
        keep the ordering and the cheap pre-filter.
        """
        return SessionItem.objects.filter(
            session_id=session_id, line_num__lt=before_line_num,
        ).order_by('-line_num')

    def iter_images_backward(
        self,
        *,
        session_id: str,
        before_line_num: int,
        images_needed: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> Iterator[ImageHit]:
        """Yield up to ``images_needed`` :class:`ImageHit` from prior items,
        most recent first.

        Walks two sources in sequence:

        1. ``in_memory_items`` (when supplied), in descending ``line_num``
           order — used in live watcher mode where the item carrying the
           image landed in the same JSONL batch as the assistant message
           referencing it and is therefore not yet committed to the DB.
        2. :meth:`image_candidate_queryset` sliced to ``images_needed``
           rows — handles older items already persisted.

        Stops as soon as ``images_needed`` hits have been yielded. A
        single tool_result item may contribute several images, in which
        case fewer DB rows are needed than the offset would naively
        suggest.
        """
        if images_needed <= 0:
            return
        yielded = 0
        if in_memory_items:
            for line_num, ts, parsed in sorted(
                in_memory_items, key=lambda x: -x[0],
            ):
                if line_num >= before_line_num:
                    continue
                for tool_use_id, media_type, data in self.iter_tool_result_image_refs(parsed):
                    yield ImageHit(tool_use_id, media_type, data, line_num, ts)
                    yielded += 1
                    if yielded >= images_needed:
                        return

        qs = self.image_candidate_queryset(session_id, before_line_num)[:images_needed]
        for item in qs:
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            for tool_use_id, media_type, data in self.iter_tool_result_image_refs(parsed):
                yield ImageHit(
                    tool_use_id, media_type, data, item.line_num, item.timestamp,
                )
                yielded += 1
                if yielded >= images_needed:
                    return

    # ------------------------------------------------------------------
    # Per-session lifecycle and tool_result remap hooks
    # ------------------------------------------------------------------

    def begin_session_compute(self, session_id: str) -> None:
        """
        Called by the orchestrator before processing any item of a session.

        Default: no-op. Providers may override to initialise per-session
        state used by their extraction hooks (e.g. Codex maintains a
        ``{exec_command_id: call_id}`` map populated on the fly so it can
        remap write_stdin tool_results to the parent exec_command).
        """

    def end_session_compute(self, session_id: str) -> None:
        """
        Called by the orchestrator after the last item of a session.

        Default: no-op. Mirror of :meth:`begin_session_compute` for
        per-session cleanup. Optional in practice (state can survive
        between sessions without correctness impact in most providers).
        """

    def remap_tool_result_id(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str:
        """
        Remap a ``tool_result_id`` before pairing with ``tool_use_map`` (batch).

        Called between :meth:`analyze_content` and the actual pairing in
        ``compute_session_metadata``. The provider may inspect ``tool_use_map``
        and the parsed result line to substitute a different ``tool_use_id``
        — for instance, attach a Codex ``write_stdin`` ``function_call_output``
        to the parent ``exec_command`` instead of the write_stdin itself.

        Default: identity (return ``naive_tool_use_id`` unchanged).
        """
        return naive_tool_use_id

    def remap_tool_result_id_live(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        item: SessionItem,
    ) -> str:
        """
        Remap a ``tool_result_id`` before the LIKE search in live mode.

        Called between :meth:`extract_tool_result_info` and the candidate
        search in ``create_tool_result_link_live``. The live equivalent of
        :meth:`remap_tool_result_id` — but without an in-memory
        ``tool_use_map`` (providers that need cross-line resolution must
        either query the DB or rely on internal per-session caches kept
        up to date by their other extraction hooks).

        Default: identity (return ``naive_tool_use_id`` unchanged).
        """
        return naive_tool_use_id

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str, bool] | None:
        """Return ``(tool_use_id, agent_id, is_async)`` when the tool_result links to a subagent.

        ``is_async`` is True when the tool_result itself signals an
        asynchronous launch (see :attr:`ContentAnalysis.tool_result_agent_info`).
        """
        raise NotImplementedError

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        """Return ``[(tool_use_id, is_background)]`` for agent-spawning tool_uses."""
        raise NotImplementedError

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        """Return ``[(tool_use_id, prompt, is_background)]`` for agent-spawning tool_uses."""
        raise NotImplementedError

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        """
        Return absolute file/directory paths referenced by tool_use blocks.

        Used by :meth:`resolve_git_for_item` to locate the git root
        relevant to the item.
        """
        raise NotImplementedError

    def compute_link_error_override(
        self,
        parsed_json: dict,
        tool_name: str,
        *,
        session_id: str | None = None,
    ) -> str | None:
        """
        Per-tool error refinement once ``tool_name`` is known.

        :meth:`extract_tool_result_info` runs before the parent ``tool_use``
        is resolved, so it cannot apply error rules that depend on the
        tool name (e.g. Codex's ``spawn_agent`` whose ``function_call_output``
        carries a JSON ``{"agent_id": ...}`` on success and a freeform
        rejection string on failure). After the live and batch paths have
        looked up the parent's ``tool_name`` they invoke this hook; a
        non-``None`` return value replaces the existing error for the
        ``ToolResultLink``. Returning ``None`` keeps whatever
        :meth:`extract_tool_result_info` produced.

        Default: no override.
        """
        return None

    def compute_link_extra(
        self, parsed_json: dict, tool_name: str, *, session_id: str | None = None,
    ) -> str | None:
        """
        Compute the ``ToolResultLink.extra`` JSON payload for this result.

        ``tool_name`` is the name of the tool whose result is being looked
        at. Implementations return any provider-specific structured data
        they want to surface to the front-end (diff stats for file-mutating
        tools, completion flags for streamed tools, etc.) as a JSON string
        ready to store in :attr:`ToolResultLink.extra`, or ``None`` when no
        data applies.

        ``session_id`` is forwarded so providers can cross-reference
        per-session side-state (e.g. Codex's in-memory
        ``_user_terminated_tool_ids`` map, which the spinner logic needs to
        flag a user-ended tool — denied, cancelled, or interrupted — as
        terminated when the JSONL trailer says nothing of the sort).
        Providers that don't need it ignore the kwarg.

        Aggregation across multiple links of the same ``tool_use_id`` is
        handled downstream via ``Max`` — providers must therefore produce
        values that compare sensibly under that aggregation when several
        links coexist (e.g. boolean flags become ``true`` once any link
        sets them).
        """
        raise NotImplementedError

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        """Return ``(has_collapsible_prefix, has_collapsible_suffix)`` for an ALWAYS item."""
        raise NotImplementedError

    def resolve_git_for_item(
        self, parsed_json: dict, *, anchor_dir: str | None = None, use_cache: bool = True
    ) -> tuple[str, str] | None:
        """
        Resolve ``(git_directory, git_branch)`` for the item, or ``None``.

        Walks the absolute paths returned by
        :meth:`extract_paths_from_tool_uses` through
        :func:`twicc.git.resolve_git_from_path`, then picks the most
        frequently-resolved git root (in case the item references several
        files in different repos). Only the path extraction is
        provider-specific.

        ``anchor_dir`` is the session's working directory (its launch
        directory). When given, candidates unrelated to it are discarded
        via :func:`twicc.git.is_git_root_related`, so a session that merely
        touches files in a foreign repository does not adopt that repo as
        its git directory. When ``None`` (no cwd known yet), no filtering
        is applied.

        ``use_cache`` is forwarded to :func:`resolve_git_from_path`; the
        watcher path passes ``False`` to bypass the cache for fresh
        results.
        """
        paths = self.extract_paths_from_tool_uses(parsed_json)
        if not paths:
            return None

        resolutions: list[tuple[str, str]] = []
        for path in paths:
            # Use the directory part of the path (for files)
            dir_path = os.path.dirname(path) if not os.path.isdir(path) else path
            result = resolve_git_from_path(dir_path, use_cache=use_cache)
            if result is None:
                continue
            if anchor_dir is not None and not is_git_root_related(
                result[0], anchor_dir, use_cache=use_cache
            ):
                continue
            resolutions.append(result)

        if not resolutions:
            return None

        if len(resolutions) == 1:
            return resolutions[0]

        # Multiple resolutions: use the most frequent git_directory
        counter = Counter(r[0] for r in resolutions)
        most_common_dir = counter.most_common(1)[0][0]
        for r in resolutions:
            if r[0] == most_common_dir:
                return r

        return resolutions[0]  # Fallback (shouldn't reach here)

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        """
        Return the plain text payload of a user message, or ``None``.

        Used by :meth:`create_agent_link_from_tool_use` to compare a
        subagent's first user message against the ``Task`` tool_use
        prompts in the parent session. Each provider parses its own
        native content layout.
        """
        raise NotImplementedError

    def agent_tool_candidates_query(self, parent_session_id: str) -> QuerySet[SessionItem]:
        """
        Return a :class:`SessionItem` queryset of items in ``parent_session_id``
        likely to contain an agent-spawning ``tool_use``, ordered by descending
        ``line_num``.

        The base implementation queries every item of the session — fine for
        correctness, expensive on large sessions. Providers override to apply
        a fast pre-filter (Claude Code uses
        ``Q(content__contains='"name":"Task"') | Q(content__contains='"name":"Agent"')``)
        and let the caller verify each candidate by parsing its content.
        """
        return SessionItem.objects.filter(session_id=parent_session_id).order_by('-line_num')

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        """
        Return ``True`` when ``parsed_json`` represents a fresh session-start event.

        Used by the batch and watcher paths to refresh
        :attr:`Session.last_started_at` when the agent CLI signals a new run on
        an existing session (e.g. Claude Code emits a ``progress`` line with
        ``data.hookEvent == 'SessionStart'``).
        """
        raise NotImplementedError

    def subagent_turn_boundary(self, parsed_json: dict) -> bool | None:
        """Return the turn boundary this line marks in a *subagent* file.

        ``True`` = the subagent just went idle (its own turn ended),
        ``False`` = it started working again, ``None`` = not a boundary.

        Only consulted on subagent files, and only by the live path, to keep
        :attr:`Session.last_stopped_at` in step with what the subagent is
        actually doing. The parent-side rule
        (:meth:`check_agent_naturally_stopped`, which counts the spawning
        tool's results) stays the primary signal; this one covers the
        providers whose subagent can finish a turn without producing that
        second result — Codex multi-agent v2, where a subagent answering
        through ``send_message`` stays alive and never emits the
        ``FINAL_ANSWER`` the parent would pair with its ``spawn_agent``.

        Default: no boundary, so a provider that doesn't override it keeps
        the parent-side rule as its only source.
        """
        return None

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        """
        Return ``(target_session_id, title)`` when ``parsed_json`` carries a
        custom-title directive, else ``None``.

        The compute pipeline applies the returned title to the matching session.
        """
        raise NotImplementedError

    def transform_tool_result_with_cache(
        self, parsed_json: dict, session_id: str, line_num: int
    ) -> str | None:
        """
        Optionally enrich a tool_result line with cached out-of-band data.

        Used by Claude Code to splice ``originalFile`` content captured by
        the PreToolUse hook into Edit/Write tool_results. Default is a
        no-op (``None``); providers that have such an out-of-band cache
        override.
        """
        return None

    def extract_subagent_marker(self, parsed_json: dict) -> str | None:
        """
        Return the subagent agent_id carried by ``parsed_json`` when it is
        the first line of a subagent file, otherwise ``None``.

        Used to bootstrap the AgentLink between a freshly-synced subagent
        and the parent session's agent-spawning tool_use. Default is a
        no-op (``None``); providers that mark their subagent files with
        an agent_id field override.
        """
        return None

    def apply_session_title(self, target_session_id: str, title: str) -> bool:
        """
        Persist ``title`` for ``target_session_id``; return ``True`` when applied.

        The default implementation simply writes the new title to the DB.
        Providers that need anti-stale-write protection (e.g. Claude Code
        guarding against the CLI re-appending an old title) override this
        to refuse the update and possibly re-write the correct value.
        """
        Session.objects.filter(id=target_session_id).update(title=title)
        return True

    # ------------------------------------------------------------------
    # Provider metadata accessors
    # ------------------------------------------------------------------

    @property
    def compute_version(self) -> int | None:
        """
        Return the provider's current compute version as exposed by its helpers.

        Mirrors :attr:`BaseProviderHelpers.current_compute_version`; the base
        compute pipeline writes this value into ``Session.compute_version`` so
        the cross-provider startup task can detect outdated sessions.
        """
        from twicc.providers.helpers import get_provider_helpers

        return get_provider_helpers(self.provider).current_compute_version

    # ------------------------------------------------------------------
    # Live (watcher) orchestration — concrete in later steps
    # ------------------------------------------------------------------

    def find_open_group_head(
        self, session_id: str, before_line_num: int
    ) -> int | None:
        """
        Find the head of any open group before ``before_line_num``.

        Skips DEBUG_ONLY items. Returns ``None`` if no open group.

        Only the suffix detection on the previous ALWAYS item is
        provider-specific; the rest of the lookup is pure DB plumbing.
        """
        previous = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=before_line_num,
        ).exclude(
            display_level=ItemDisplayLevel.DEBUG_ONLY,
        ).order_by('-line_num').first()

        if not previous:
            return None

        # COLLAPSIBLE with group_head = group is open
        if previous.display_level == ItemDisplayLevel.COLLAPSIBLE and previous.group_head:
            return previous.group_head

        # ALWAYS with collapsible suffix = ALWAYS item is the head
        if previous.display_level == ItemDisplayLevel.ALWAYS:
            try:
                parsed = orjson.loads(previous.content)
                _, has_suffix = self.detect_prefix_suffix(parsed, previous.kind)
                if has_suffix:
                    return previous.line_num
            except orjson.JSONDecodeError:
                pass

        return None

    def compute_item_metadata_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> set[int]:
        """
        Compute group membership for ``item`` during live watcher sync.

        Updates ``item.group_head`` / ``item.group_tail`` in place, and
        returns the set of pre-existing item line numbers whose
        ``group_tail`` was updated as a side effect.

        Algorithm is shared across providers; only prefix/suffix
        detection is dispatched through a provider hook. Git resolution
        is handled separately at the call site so it runs for every
        item, including DEBUG_ONLY ones that carry tool paths
        (e.g. Codex's ``event_msg.patch_apply_end``).
        """
        # Initialize group fields
        item.group_head = None
        item.group_tail = None

        if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
            return set()

        # Track which pre-existing items were modified
        modified_line_nums: set[int] = set()

        # Find if there's an open group before us
        open_group_head = self.find_open_group_head(session_id, item.line_num)

        if item.display_level == ItemDisplayLevel.COLLAPSIBLE:
            if open_group_head is not None:
                # Join existing group
                item.group_head = open_group_head
                item.group_tail = item.line_num

                # Get line_nums of pre-existing items that will be updated
                affected_collapsibles = SessionItem.objects.filter(
                    session_id=session_id,
                    group_head=open_group_head,
                    line_num__lt=item.line_num,
                ).values_list('line_num', flat=True)
                modified_line_nums.update(affected_collapsibles)

                # Check if ALWAYS started this group
                always_starter = SessionItem.objects.filter(
                    session_id=session_id,
                    line_num=open_group_head,
                    display_level=ItemDisplayLevel.ALWAYS,
                ).exists()
                if always_starter:
                    modified_line_nums.add(open_group_head)

                # Update all items in group with new tail
                SessionItem.objects.filter(
                    session_id=session_id,
                    group_head=open_group_head,
                ).update(group_tail=item.line_num)

                # Also update ALWAYS item if it started the group (via suffix)
                SessionItem.objects.filter(
                    session_id=session_id,
                    line_num=open_group_head,
                    display_level=ItemDisplayLevel.ALWAYS,
                ).update(group_tail=item.line_num)
            else:
                # Start new group
                item.group_head = item.line_num
                item.group_tail = item.line_num

        elif item.display_level == ItemDisplayLevel.ALWAYS:
            has_prefix, _ = self.detect_prefix_suffix(parsed_json, item.kind)

            # Handle prefix
            if has_prefix and open_group_head is not None:
                item.group_head = open_group_head

                # Get line_nums of pre-existing items that will be updated
                affected_collapsibles = SessionItem.objects.filter(
                    session_id=session_id,
                    group_head=open_group_head,
                    line_num__lt=item.line_num,
                ).values_list('line_num', flat=True)
                modified_line_nums.update(affected_collapsibles)

                # Check if ALWAYS started this group
                always_starter = SessionItem.objects.filter(
                    session_id=session_id,
                    line_num=open_group_head,
                    display_level=ItemDisplayLevel.ALWAYS,
                ).exists()
                if always_starter:
                    modified_line_nums.add(open_group_head)

                # Update all items in group with new tail (this item)
                SessionItem.objects.filter(
                    session_id=session_id,
                    group_head=open_group_head,
                ).update(group_tail=item.line_num)

                # Also update ALWAYS item if it started the group
                SessionItem.objects.filter(
                    session_id=session_id,
                    line_num=open_group_head,
                    display_level=ItemDisplayLevel.ALWAYS,
                ).update(group_tail=item.line_num)

            # Suffix: group_tail stays null until next item arrives and connects
            # (will be updated by the next item's compute_item_metadata_live)

        return modified_line_nums

    def create_tool_result_link_live(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> ToolResultUpdate | None:
        """
        Create a :class:`~twicc.core.models.ToolResultLink` during live sync.

        Generic algorithm:

        1. Pull ``(tool_use_id, error)`` from ``parsed_json`` via the
           provider's :meth:`extract_tool_result_info` hook.
        2. Search prior items whose content textually contains the
           ``tool_use_id`` (cheap LIKE pre-filter), then verify each
           candidate by parsing it and asking the provider for its
           ``tool_use_entries`` mapping.
        3. On match, ask the provider for the link's structured ``extra``
           via :meth:`compute_link_extra`, persist the link, and
           emit a :class:`ToolResultUpdate` reflecting the current
           tool-completion state for the front-end (spinner / error
           indicator).
        """
        info = self.extract_tool_result_info(parsed_json, session_id=session_id)
        if info is None or not info.tool_use_id:
            return None
        # Provider hook: remap the tool_use_id before searching for the
        # parent (e.g. Codex resolves a ``write_stdin`` ``function_call_output``
        # back to the parent ``exec_command``). Default is identity.
        tool_use_id = self.remap_tool_result_id_live(
            parsed_json,
            info.tool_use_id,
            session_id=session_id,
            item=item,
        )
        error = info.error_text

        # Find candidates by text search (LIKE), ordered most recent first.
        # The tool_use_id string could appear in text content (e.g. assistant mentioning it),
        # so we iterate candidates and verify each one until we find an actual tool_use match.
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=item.line_num,
            content__contains=tool_use_id,
        ).order_by('-line_num')

        for candidate in candidates.iterator(chunk_size=10):
            try:
                candidate_parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue

            tool_use_entries = self.extract_tool_use_entries(
                candidate_parsed, session_id=session_id
            )
            if tool_use_id in tool_use_entries:
                tool_name = tool_use_entries[tool_use_id]

                extra = self.compute_link_extra(
                    parsed_json, tool_name, session_id=session_id,
                )
                # Per-tool error refinement (e.g. Codex's spawn_agent whose
                # output is a JSON ``{"agent_id": ...}`` on success and a
                # freeform rejection string on failure). The hook needs
                # ``tool_name``, which only becomes known here.
                error_override = self.compute_link_error_override(
                    parsed_json, tool_name, session_id=session_id,
                )
                if error_override is not None:
                    error = error_override
                _, created = ToolResultLink.objects.get_or_create(
                    session_id=session_id,
                    tool_use_line_num=candidate.line_num,
                    tool_result_line_num=item.line_num,
                    tool_use_id=tool_use_id,
                    defaults={
                        'tool_name': tool_name,
                        'tool_result_at': item.timestamp,
                        'extra': extra,
                        'error': error,
                    },
                )
                if not created:
                    return None

                # Emit ToolResultUpdate for all tools (spinner + error indicator).
                # Aggregate ``extra`` / ``error`` across every link of this
                # tool_use_id: Codex routinely emits multiple links per
                # call (apply_patch / MCP / web / image: the LLM-facing
                # ``*_call_output`` plus the structured ``event_msg.*_end``;
                # exec_command shells: one chunk per ``write_stdin`` poll
                # chained to the parent's call_id). Falling back to ``Max``
                # keeps the rich values regardless of arrival order and
                # matches the ``tool_states`` REST view's aggregation —
                # importantly, the ``is_terminated: true`` flag carried
                # by the closing chunk's ``extra`` flips the whole tool
                # to "done" via this exact path.
                links = ToolResultLink.objects.filter(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                aggregated = links.aggregate(**TOOL_STATE_ANNOTATIONS)
                line_nums = tuple(
                    links.order_by('tool_result_line_num')
                    .values_list('tool_result_line_num', flat=True)
                )
                return ToolResultUpdate(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                    result_count=aggregated['result_count'],
                    completed_at=aggregated['completed_at'],
                    extra=aggregated['extra'],
                    error=aggregated['error'],
                    tool_result_line_nums=line_nums,
                )

        return None

    def check_agent_naturally_stopped(
        self, session_id: str, tool_result_update: ToolResultUpdate
    ) -> AgentStoppedUpdate | None:
        """
        Detect when a subagent has finished after the latest tool_result arrived.

        For non-background agents, 1 tool_result means done. For background
        agents, 2 tool_results means done. Updates the agent session's
        ``last_stopped_at`` and ``last_updated_at`` and returns an
        :class:`AgentStoppedUpdate` for broadcast.

        Pure DB plumbing — no provider hook involved.
        """
        agent_link = AgentLink.objects.filter(
            session_id=session_id,
            tool_use_id=tool_result_update.tool_use_id,
        ).first()
        if agent_link is None:
            return None

        required_results = 2 if agent_link.is_background else 1
        if tool_result_update.result_count < required_results:
            return None

        stopped_at = tool_result_update.completed_at
        if stopped_at is None:
            return None

        agent_session_id = agent_link.agent_id
        # Monotonic guard: never let a stale parent-side stop overwrite a
        # subagent that has activity NEWER than the stop signal. The case is
        # real with resumable agents (Claude): the subagent's own file sync
        # (:meth:`subagent_turn_boundary`) cleared ``last_stopped_at`` on a
        # wake-up, then the parent's file delivers an older notification —
        # stamping it would re-freeze a working agent as "stopped". On the
        # historical flow (agent stops once, notification written after its
        # last line) the guard is a no-op: ``stopped_at`` is always >= the
        # subagent's ``last_updated_at`` there.
        updated = Session.objects.filter(
            id=agent_session_id,
        ).exclude(
            last_updated_at__gt=stopped_at,
        ).update(last_stopped_at=stopped_at, last_updated_at=stopped_at)

        if updated:
            return AgentStoppedUpdate(
                agent_session_id=agent_session_id,
                stopped_at=stopped_at,
            )

        return None

    def create_agent_link_from_tool_result(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> AgentLinkUpdate | None:
        """
        Create an :class:`~twicc.core.models.AgentLink` from a tool_result with agentId.

        When a tool_result arrives carrying an ``agent_id`` reference, the
        provider supplies the ``(tool_use_id, agent_id, is_async)`` triple via
        :meth:`extract_agent_info_from_tool_result`. This method then
        backfills the matching tool_use in the parent session and creates
        the link, broadcasting an :class:`AgentLinkUpdate` on success. When
        the link already exists but an async ack proves it should be
        background, the flag is upgraded (and re-broadcast) instead.
        """
        agent_info = self.extract_agent_info_from_tool_result(parsed_json)
        if not agent_info:
            return None

        tool_use_id, agent_id, is_async = agent_info

        if is_agent_link_done(session_id, agent_id) or AgentLink.objects.filter(
            session_id=session_id,
            agent_id=agent_id,
        ).exists():
            mark_agent_link_done(session_id, agent_id)
            # The link may pre-exist via the prompt-matching paths, which
            # only see the tool_use input — and the async-by-default CLI
            # dropped the ``run_in_background`` flag there. An async launch
            # ack must upgrade such a link to background, otherwise
            # ``check_agent_naturally_stopped`` counts this very ack as the
            # single result of a foreground agent and stops it immediately.
            #
            # Restricted to the link's own tool_use: a SendMessage
            # continuation of a finished agent resumes it in the background,
            # and its terminal task-notification (rewritten with
            # ``isAsync: true``) carries the SendMessage tool_use_id, not
            # the launching one. That must NOT flip the original link: the
            # launch really was synchronous and already complete, and the
            # flip's re-broadcast resurrects the subagent's synthetic
            # "running" state in the frontend with no removal path — the
            # second result the background rule then waits for lands on the
            # SendMessage tool_use, never on the link's.
            if is_async:
                link = AgentLink.objects.filter(
                    session_id=session_id,
                    agent_id=agent_id,
                    tool_use_id=tool_use_id,
                    is_background=False,
                ).first()
                if link is not None:
                    link.is_background = True
                    link.save(update_fields=['is_background'])
                    return AgentLinkUpdate(
                        parent_session_id=session_id,
                        agent_id=agent_id,
                        tool_use_id=link.tool_use_id,
                        tool_use_line_num=link.tool_use_line_num,
                        is_background=True,
                        started_at=link.started_at,
                    )
            return None

        # Find the agent-spawning tool_use by searching for the tool_use_id
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=item.line_num,
            content__contains=tool_use_id,
        ).order_by('-line_num')

        for candidate in candidates.iterator(chunk_size=10):
            try:
                candidate_parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue

            for tu_id, input_is_background in self.extract_task_tool_uses(candidate_parsed):
                if tu_id != tool_use_id:
                    continue
                is_background = input_is_background or is_async
                try:
                    obj, created = AgentLink.objects.get_or_create(
                        session_id=session_id,
                        tool_use_line_num=candidate.line_num,
                        tool_use_id=tool_use_id,
                        defaults={
                            "agent_id": agent_id,
                            "is_background": is_background,
                            "started_at": candidate.timestamp,
                        },
                    )
                    mark_agent_link_done(session_id, agent_id)
                    if created:
                        return AgentLinkUpdate(
                            parent_session_id=session_id,
                            agent_id=agent_id,
                            tool_use_id=tool_use_id,
                            tool_use_line_num=candidate.line_num,
                            is_background=is_background,
                            started_at=candidate.timestamp,
                        )
                except MultipleObjectsReturned:  # defensive mode
                    pass
                return None
        return None

    def extract_workflow_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        """Return ``(tool_use_id, run_id)`` if this tool_result launched a workflow.

        Provider hook, mirroring :meth:`extract_agent_info_from_tool_result`.
        Default: no provider but Claude Code has workflows, so this is a no-op.
        """
        return None

    def create_workflow_link_from_tool_result(
        self, session_id: str, item: SessionItem, parsed_json: dict
    ) -> WorkflowLinkUpdate | None:
        """Surface the in-chat workflow link from a tool_result, for live broadcast.

        Unlike agents, nothing is persisted: the ``(tool_use_id, run_id)`` couple
        lives in the tool_result item itself and is re-derived on demand by
        ``views.workflow_links`` (stale-safe, since the SessionItem rows outlive
        the JSONL). This only surfaces it as the line is synced so the watcher
        can emit ``workflow_link_created``, mirroring ``agent_link_created``.
        """
        info = self.extract_workflow_info_from_tool_result(parsed_json)
        if not info:
            return None
        tool_use_id, run_id = info
        return WorkflowLinkUpdate(
            session_id=session_id,
            tool_use_id=tool_use_id,
            run_id=run_id,
        )

    def create_agent_link_from_subagent(
        self,
        parent_session_id: str,
        agent_id: str,
        agent_prompt: str,
    ) -> AgentLinkUpdate | None:
        """
        Create an :class:`~twicc.core.models.AgentLink` by matching a subagent prompt.

        Used by the subagent watcher path: when a fresh subagent file is
        synced, find the parent's matching agent-spawning tool_use and
        link them.

        Provider hooks involved:

        - :meth:`agent_tool_candidates_query` for the pre-filtered queryset
          of items likely to contain an agent-spawning tool_use.
        - :meth:`extract_task_tool_use_prompts` for the per-candidate
          extraction of ``(tool_use_id, prompt, is_background)`` triples.
        """
        if is_agent_link_done(parent_session_id, agent_id):
            return None

        # Check if we already have this agent link
        if AgentLink.objects.filter(
            session_id=parent_session_id,
            agent_id=agent_id,
        ).exists():
            mark_agent_link_done(parent_session_id, agent_id)
            return None

        agent_prompt = agent_prompt.strip()

        candidates = self.agent_tool_candidates_query(parent_session_id)

        for candidate in candidates.iterator(chunk_size=20):
            try:
                candidate_parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue

            for tu_id, prompt, is_background in self.extract_task_tool_use_prompts(candidate_parsed):
                if prompt.strip() == agent_prompt:
                    try:
                        obj, created = AgentLink.objects.get_or_create(
                            session_id=parent_session_id,
                            tool_use_line_num=candidate.line_num,
                            tool_use_id=tu_id,
                            defaults={
                                "agent_id": agent_id,
                                "is_background": is_background,
                                "started_at": candidate.timestamp,
                            },
                        )
                        if created:
                            mark_agent_link_done(parent_session_id, agent_id)
                            return AgentLinkUpdate(
                                parent_session_id=parent_session_id,
                                agent_id=agent_id,
                                tool_use_id=tu_id,
                                tool_use_line_num=candidate.line_num,
                                is_background=is_background,
                                started_at=candidate.timestamp,
                            )
                    except MultipleObjectsReturned:  # defensive mode
                        continue

        return None

    def create_agent_link_from_tool_use(
        self,
        session_id: str,
        item: SessionItem,
        parsed_json: dict,
    ) -> list[AgentLinkUpdate]:
        """
        Create AgentLinks for newly-synced tool_uses against existing subagents.

        Handles the race condition where the subagent file landed before
        the parent session's agent-spawning tool_use line was synced.

        Provider hooks involved:

        - :meth:`extract_task_tool_use_prompts` to pull the
          ``(tool_use_id, prompt, is_background)`` triples from ``item``.
        - :meth:`extract_user_message_text` to read the subagent's first
          user message for prompt comparison.
        """
        # Collect all (tool_use_id, prompt, is_background) triples from the new item
        task_prompts = self.extract_task_tool_use_prompts(parsed_json)
        # Normalize prompts for matching
        task_prompts = [(tu_id, prompt.strip(), is_bg) for tu_id, prompt, is_bg in task_prompts]

        if not task_prompts:
            return []

        updates: list[AgentLinkUpdate] = []

        # Get all subagents for this session that don't have a link yet
        subagents = Session.objects.filter(
            parent_session_id=session_id,
            type=SessionType.SUBAGENT,
        )

        # For each subagent, check if its prompt matches one of the new task prompts
        for subagent in subagents:
            if is_agent_link_done(session_id, subagent.id):
                continue

            # Check if link already exists
            if AgentLink.objects.filter(
                session_id=session_id,
                agent_id=subagent.id,
            ).exists():
                mark_agent_link_done(session_id, subagent.id)
                continue

            # Get the subagent's prompt from cache or DB
            subagent_prompt = get_cached_agent_prompt(session_id, subagent.id)
            if not subagent_prompt:
                # Try to get from the subagent's first user message
                first_user_message = SessionItem.objects.filter(
                    session_id=subagent.id,
                    kind=ItemKind.USER_MESSAGE,
                ).first()
                if first_user_message is None:
                    continue
                try:
                    first_parsed = orjson.loads(first_user_message.content)
                except orjson.JSONDecodeError:
                    continue
                subagent_prompt = self.extract_user_message_text(first_parsed)
                if not subagent_prompt:
                    continue
                subagent_prompt = subagent_prompt.strip()
                cache_agent_prompt(session_id, subagent.id, subagent_prompt)

            # Check if the subagent's prompt matches any task prompt
            for tu_id, prompt, is_background in task_prompts:
                if prompt == subagent_prompt:
                    try:
                        _, created = AgentLink.objects.get_or_create(
                            session_id=session_id,
                            tool_use_line_num=item.line_num,
                            tool_use_id=tu_id,
                            defaults={
                                "agent_id": subagent.id,
                                "is_background": is_background,
                                "started_at": item.timestamp,
                            },
                        )
                        if created:
                            mark_agent_link_done(session_id, subagent.id)
                            updates.append(AgentLinkUpdate(
                                parent_session_id=session_id,
                                agent_id=subagent.id,
                                tool_use_id=tu_id,
                                tool_use_line_num=item.line_num,
                                is_background=is_background,
                                started_at=item.timestamp,
                            ))
                    except MultipleObjectsReturned:
                        pass
                    break

        return updates

    # ------------------------------------------------------------------
    # Batch orchestration — concrete in later steps
    # ------------------------------------------------------------------

    def extra_session_fields(self, session: Session) -> dict:  # noqa: ARG002
        """Provider hook: filesystem-derived session-level fields.

        Merged verbatim into the ``session_fields`` of the
        ``session_complete`` payload by :meth:`compute_session_metadata`,
        so the keys returned here are written by
        :meth:`apply_session_complete` like any other session field.

        Runs in the background-compute worker process (it may touch the
        filesystem). The default returns an empty dict — providers that
        derive nothing from disk inherit it unchanged. Implementations
        should emit a key ONLY when they want it written: an omitted key
        is left untouched on the row, which keeps monotonic flags (e.g.
        Claude Code's ``has_workflows``) from being reset on a recompute.
        """
        return {}

    @staticmethod
    def _plan_doc_roots(session: Session) -> list[str | None]:
        """Roots for plan-doc relativization/existence probing: the session's
        project directory first, then the ``worktree_of`` parent's (so a doc
        written in a worktree stays resolvable after the worktree is removed).
        Sync-only (lazy FK loads) — callable from the compute worker and the
        watcher's sync section, never from async code."""
        project = session.project
        if project is None:
            return []
        parent = project.worktree_of
        return [project.directory, parent.directory if parent else None]

    @staticmethod
    def _fold_plan_doc_events_into_ancestor(
        session: Session,
        timed_events: list[tuple[DocEditEvent, datetime | str | None]],
    ) -> str | None:
        """Fold a subagent's plan-doc events into its top-level ancestor's
        ``plan_paths`` (additive, entries tagged ``source='subagent'`` by the
        caller). The ancestor row is read fresh right before the write; its
        own writers preserve these entries via ``FOLDED_SOURCES``. Sync-only.
        Returns the ancestor's id when its list changed (the caller may need
        to broadcast it), ``None`` otherwise.
        """
        # Bounded parent walk — subagents are direct children of a main
        # session today, the loop is future-proofing against nesting.
        ancestor: Session | None = session
        for _ in range(5):
            if ancestor is None or ancestor.type == SessionType.SESSION:
                break
            if not ancestor.parent_session_id:
                ancestor = None
                break
            ancestor = Session.objects.filter(id=ancestor.parent_session_id).first()
        else:
            ancestor = None
        if ancestor is None or ancestor.type != SessionType.SESSION or ancestor.id == session.id:
            return None
        roots = BaseSessionCompute._plan_doc_roots(ancestor)
        entries, changed = apply_doc_edit_events(
            ancestor.plan_paths, timed_events,
            project_root=roots[0] if roots else None,
        )
        if not changed:
            return None
        refresh_entries_existence(entries, roots)
        ancestor.plan_paths = entries
        ancestor.save(update_fields=["plan_paths"])
        return ancestor.id

    def compute_session_metadata(self, session_id: str, result_queue, run_id: int) -> None:
        """
        Compute metadata for every item in a session and push the result on ``result_queue``.

        Runs in the multiprocessing worker. Does not touch the DB
        directly; the caller (:meth:`apply_session_complete`) consumes the
        queue and applies the changes.

        Generic algorithm — every parsing step is dispatched through a
        provider hook (``transform_inline``, ``analyze_content``,
        ``compute_item_metadata``, ``extract_item_timestamp``,
        ``compute_item_cost_and_usage``, ``extract_runtime_fields``,
        ``resolve_git_for_item``, ``extract_title_from_user_message``,
        ``compute_link_extra``, ``is_session_start_marker``,
        ``extract_custom_title``).

        Wraps the per-item loop with :meth:`begin_session_compute` /
        :meth:`end_session_compute` so providers can maintain per-session
        state used by their other hooks (e.g. Codex's exec_command map for
        write_stdin remapping).
        """
        from django.db import connection

        # Ensure this process/thread has its own database connection
        connection.close()

        try:
            session = Session.objects.get(id=session_id)
        except Session.DoesNotExist:
            logger.error(f"Session {session_id} not found for metadata computation")
            result_queue.put(orjson.dumps({
                'type': 'error',
                'provider': self.provider.value,
                'run_id': run_id,
                'session_id': session_id,
                'error': 'Session not found',
            }))
            return

        queryset = SessionItem.objects.filter(session=session).order_by('line_num')

        state = GroupState()
        items_to_update: list[SessionItem] = []
        all_item_updates: list[dict] = []
        all_tool_result_links: dict[tuple[str, int], dict] = {}
        all_agent_links: dict[tuple[str, str], dict] = {}
        content_overrides: list[dict] = []
        batch_size = 500

        def serialize_item(item: SessionItem) -> dict:
            return {
                'id': item.id,
                'display_level': item.display_level,
                'group_head': item.group_head,
                'group_tail': item.group_tail,
                'kind': item.kind,
                'message_id': item.message_id,
                'cost': str(item.cost) if item.cost is not None else None,
                'context_usage': item.context_usage,
                'timestamp': item.timestamp.isoformat() if item.timestamp else None,
                'git_directory': item.git_directory,
                'git_branch': item.git_branch,
            }

        def flush_items(items: list[SessionItem]) -> None:
            for item in items:
                serialized = serialize_item(item)
                if serialized != original_serialized.get(item.id):
                    all_item_updates.append(serialized)

        def serialize_tool_result_link(link: ToolResultLink) -> dict:
            return {
                'session_id': link.session_id,
                'tool_use_line_num': link.tool_use_line_num,
                'tool_result_line_num': link.tool_result_line_num,
                'tool_use_id': link.tool_use_id,
                'tool_name': link.tool_name,
                'tool_result_at': link.tool_result_at.isoformat() if link.tool_result_at else None,
                'extra': link.extra,
                'error': link.error,
            }

        def serialize_agent_link(link: AgentLink) -> dict:
            return {
                'session_id': link.session_id,
                'tool_use_line_num': link.tool_use_line_num,
                'tool_use_id': link.tool_use_id,
                'agent_id': link.agent_id,
                'is_background': link.is_background,
                'started_at': link.started_at.isoformat() if link.started_at else None,
            }

        tool_use_map: dict[str, ToolUseEntry] = {}
        task_tool_use_map: dict[str, tuple[int, bool, datetime]] = {}
        # Mirror the live path's guard (see sync_session_items_from_file:
        # ``initial_title_needs_set = session.title is None``). The
        # first-user-message title is only an INITIAL placeholder; it must never
        # overwrite a title that already exists — whether set by a manual rename,
        # an AI suggestion, or imported from a provider store (e.g. Codex's
        # thread name). Without this guard a recompute (compute-version bump)
        # reverts every session's title back to its first user message. Custom
        # titles carried in the JSONL (Claude Code) come through the separate
        # ``extract_custom_title`` branch below and are unaffected.
        initial_title_set = session.title is not None
        session_titles: dict[str, str] = {}
        user_message_count = 0
        affected_days: set[str] = set()
        seen_message_ids: set[str] = set()
        last_context_usage: int | None = None
        first_timestamp: datetime | None = None
        last_started_at: datetime | None = None
        last_updated_at: datetime | None = None
        first_cwd: str | None = None
        last_cwd: str | None = None
        last_cwd_git_branch: str | None = None
        last_model: str | None = None
        last_slug: str | None = None
        last_context_max: int | None = None
        last_resolved_git_directory: str | None = None
        last_resolved_git_branch: str | None = None
        # Latest task/todo/plan snapshot across the whole session (this full
        # recompute is authoritative — it resets stale state to {}).
        last_tasks_snapshot: dict | None = None
        # Plan-doc write/delete events across the whole session, paired with
        # their line timestamps (authoritative full rebuild — starts empty).
        # Also accumulated for subagents, whose events are folded into the
        # top-level ancestor's list at apply time (never their own row).
        plan_doc_events: list[tuple[DocEditEvent, datetime | None]] = []
        is_main_session = session.type == SessionType.SESSION
        # Goal lifecycle history, folded from every goal line across the whole
        # session (authoritative full rebuild — starts empty).
        goals: list[dict] = []
        found_compact_summary = False
        agent_tool_result_counts: dict[str, tuple[int, datetime | None]] = {}
        agent_stopped_list: list[dict] = []
        original_serialized: dict[int, dict] = {}

        # Load existing links for change detection
        original_tool_result_links: dict[tuple[str, int], dict] = {}
        original_tool_result_links_ids: dict[tuple[str, int], int] = {}
        for link in ToolResultLink.objects.filter(session_id=session_id):
            key = (link.tool_use_id, link.tool_result_line_num)
            original_tool_result_links[key] = serialize_tool_result_link(link)
            original_tool_result_links_ids[key] = link.id

        original_agent_links: dict[tuple[str, str], dict] = {}
        original_agent_links_ids: dict[tuple[str, str], int] = {}
        for link in AgentLink.objects.filter(session_id=session_id):
            key = (link.agent_id, link.tool_use_id)
            original_agent_links[key] = serialize_agent_link(link)
            original_agent_links_ids[key] = link.id

        # Provider hook: per-session setup (e.g. Codex initialises its
        # exec_command map used by remap_tool_result_id below).
        self.begin_session_compute(session_id)

        for item in queryset.iterator(chunk_size=batch_size):
            # Snapshot original state before any computation, for change detection
            original_serialized[item.id] = serialize_item(item)

            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
                parsed = {}

            # Provider rewrites + the generic context-tag strip, both applied
            # in place; see :meth:`transform_inline`. Returns the new content
            # when either fired, else ``None``.
            new_content = self.transform_inline(
                parsed, session_id=session_id, line_num=item.line_num,
            )
            if new_content is not None and new_content != item.content:
                item.content = new_content
                content_overrides.append({'id': item.id, 'content': new_content})

            # Single-pass content analysis (avoids redundant traversals)
            analysis = self.analyze_content(
                parsed, session_id=session_id, tool_use_map=tool_use_map
            )

            # Compute display_level and kind
            metadata = self.compute_item_metadata(parsed)
            item.display_level = metadata['display_level']
            item.kind = metadata['kind']

            # Track compact summary for session.compacted flag
            if item.kind == ItemKind.COMPACT_SUMMARY:
                found_compact_summary = True

            # Extract timestamp
            item.timestamp = self.extract_item_timestamp(parsed)
            if first_timestamp is None and item.timestamp is not None:
                first_timestamp = item.timestamp
                last_started_at = first_timestamp
                affected_days.add(first_timestamp.date().isoformat())
            if item.timestamp is not None:
                last_updated_at = item.timestamp
            if item.timestamp is not None and self.is_session_start_marker(parsed):
                last_started_at = item.timestamp

            # Refresh the running task/todo/plan snapshot (keep last non-None).
            tasks_snapshot = self.build_tasks_snapshot(
                parsed, line_num=item.line_num, timestamp=item.timestamp,
            )
            if tasks_snapshot is not None:
                last_tasks_snapshot = tasks_snapshot

            # Fold any goal-lifecycle transition into the running history.
            goal_event = self.extract_goal_event(parsed)
            if goal_event is not None:
                apply_goal_event(goals, goal_event, timestamp=item.timestamp)

            # Extract runtime environment fields
            runtime = self.extract_runtime_fields(parsed)
            if runtime.get('cwd'):
                if first_cwd is None:
                    first_cwd = runtime['cwd']
                last_cwd = runtime['cwd']
            if runtime.get('cwd_git_branch'):
                last_cwd_git_branch = runtime['cwd_git_branch']
            if runtime.get('model'):
                last_model = runtime['model']
            if runtime.get('slug'):
                last_slug = runtime['slug']
            if runtime.get('context_max') is not None:
                last_context_max = runtime['context_max']

            # Detect plan-doc writes/deletes. After the runtime block so
            # ``last_cwd`` already reflects a cwd carried by this very line.
            for doc_event in self.extract_doc_edit_events(parsed, cwd=last_cwd):
                plan_doc_events.append((doc_event, item.timestamp))

            # Compute cost and context usage with the running model state
            self.compute_item_cost_and_usage(
                item, parsed, seen_message_ids, last_model,
            )
            if item.context_usage is not None:
                last_context_usage = item.context_usage

            # Resolve git directory/branch from tool_use paths, anchored to the
            # session's cwd so unrelated repos merely touched by the session
            # are ignored (cwd is extracted above, so last_cwd is already set
            # when the session's JSONL carries one).
            if item.git_directory is not None:
                last_resolved_git_directory = item.git_directory
                last_resolved_git_branch = item.git_branch
            else:
                git_resolution = self.resolve_git_for_item(parsed, anchor_dir=last_cwd)
                if git_resolution is not None:
                    item.git_directory, item.git_branch = git_resolution
                    last_resolved_git_directory = item.git_directory
                    last_resolved_git_branch = item.git_branch

            # Handle title extraction
            if item.kind == ItemKind.USER_MESSAGE and not initial_title_set:
                title = self.extract_title_from_user_message(parsed)
                if title:
                    session_titles[session_id] = title
                    initial_title_set = True
            if item.kind == ItemKind.SYSTEM:
                custom = self.extract_custom_title(parsed)
                if custom is not None:
                    target_session_id, custom_title = custom
                    session_titles[target_session_id or session_id] = custom_title
            if item.kind == ItemKind.USER_MESSAGE:
                user_message_count += 1
            if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost):
                affected_days.add(item.timestamp.date().isoformat())

            # Use analysis fields instead of individual function calls
            for tu_id, tu_name in analysis.tool_use_entries.items():
                tool_use_map[tu_id] = ToolUseEntry(item.line_num, tu_name, parsed)
            for tu_id, is_background in analysis.task_tool_uses:
                task_tool_use_map[tu_id] = (item.line_num, is_background, item.timestamp)
            tool_result_ref = analysis.tool_result_id
            if tool_result_ref:
                # Provider hook: optionally substitute the tool_use_id to point
                # at a different tool_use than the naive parent (e.g. Codex
                # rebinds write_stdin function_call_outputs to the parent
                # exec_command). Default is identity.
                tool_result_ref = self.remap_tool_result_id(
                    parsed,
                    tool_result_ref,
                    session_id=session_id,
                    tool_use_map=tool_use_map,
                )
            if tool_result_ref and tool_result_ref in tool_use_map:
                tu_entry = tool_use_map[tool_result_ref]
                tu_line_num = tu_entry.line_num
                tu_name = tu_entry.tool_name
                extra = self.compute_link_extra(
                    parsed, tu_name, session_id=session_id,
                )
                error = analysis.tool_result_error
                # Same per-tool error refinement as the live path
                # (see ``compute_link_error_override`` docstring).
                error_override = self.compute_link_error_override(
                    parsed, tu_name, session_id=session_id,
                )
                if error_override is not None:
                    error = error_override
                new_key = (tool_result_ref, item.line_num)
                all_tool_result_links[new_key] = serialize_tool_result_link(ToolResultLink(
                    session_id=session_id,
                    tool_use_line_num=tu_line_num,
                    tool_result_line_num=item.line_num,
                    tool_use_id=tool_result_ref,
                    tool_name=tu_name,
                    tool_result_at=item.timestamp,
                    extra=extra,
                    error=error,
                ))
                # If the matched tool_use spawned an agent, count this result
                # against the corresponding subagent for stopped detection.
                if tool_result_ref in task_tool_use_map or any(
                    link['tool_use_id'] == tool_result_ref
                    for link in all_agent_links.values()
                ):
                    prev_count, _ = agent_tool_result_counts.get(tool_result_ref, (0, None))
                    agent_tool_result_counts[tool_result_ref] = (prev_count + 1, item.timestamp)
            if analysis.tool_result_agent_info:
                tu_id, agent_id, is_async = analysis.tool_result_agent_info
                if tu_id in task_tool_use_map:
                    line_num, is_background, started_at = task_tool_use_map[tu_id]
                    all_agent_links[(agent_id, tu_id)] = serialize_agent_link(AgentLink(
                        session_id=session_id,
                        tool_use_line_num=line_num,
                        tool_use_id=tu_id,
                        agent_id=agent_id,
                        is_background=is_background or is_async,
                        started_at=started_at,
                    ))
                    del task_tool_use_map[tu_id]

            # Prefix/suffix for group state machine
            has_prefix, has_suffix = False, False
            if (
                item.display_level == ItemDisplayLevel.ALWAYS
                and item.kind in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE)
            ):
                has_prefix, has_suffix = analysis.has_prefix, analysis.has_suffix
            info = state.process_item(
                line_num=item.line_num,
                display_level=item.display_level,
                has_prefix=has_prefix,
                has_suffix=has_suffix,
                item_ref=item,
            )
            item.group_head = info.group_head
            items_to_update.extend(info.closed_items)
            if item.display_level == ItemDisplayLevel.DEBUG_ONLY or item.display_level == ItemDisplayLevel.ALWAYS and not has_suffix:
                items_to_update.append(item)

            # Flush batches
            if len(items_to_update) >= batch_size:
                flush_items(items_to_update)
                items_to_update = []

        # Finalize pending groups
        finalized = state.finalize()
        items_to_update.extend(finalized)

        flush_items(items_to_update)

        # Provider hook: per-session teardown — mirror of begin_session_compute.
        self.end_session_compute(session_id)

        # Diff tool result links: create / update / delete
        trl_to_create: list[dict] = []
        trl_to_update: list[dict] = []
        for key, serialized in all_tool_result_links.items():
            original = original_tool_result_links.get(key)
            if original is None:
                trl_to_create.append(serialized)
            else:
                # Preserve previously-recorded error / extra if the re-compute
                # produced None — happens when the live path's
                # _user_terminated_tool_ids signal is no longer available after a
                # backend restart (the agent is gone so
                # _user_terminated_tool_reason returns None). The live-path value
                # is the authoritative one; a stale re-compute must not erase it.
                if serialized['error'] is None and original['error'] is not None:
                    serialized['error'] = original['error']
                if serialized['extra'] is None and original['extra'] is not None:
                    serialized['extra'] = original['extra']
                if serialized != original:
                    serialized['id'] = original_tool_result_links_ids[key]
                    trl_to_update.append(serialized)
        trl_to_delete: list[int] = [
            pk for key, pk in original_tool_result_links_ids.items() if key not in all_tool_result_links
        ]

        # Diff agent links: create / update / delete
        agent_links_to_create: list[dict] = []
        agent_links_to_update: list[dict] = []
        for key, serialized in all_agent_links.items():
            original = original_agent_links.get(key)
            if original is None:
                agent_links_to_create.append(serialized)
            elif serialized != original:
                serialized['id'] = original_agent_links_ids[key]
                agent_links_to_update.append(serialized)
        agent_links_to_delete: list[int] = [
            pk for key, pk in original_agent_links_ids.items() if key not in all_agent_links
        ]

        # Determine which agents have stopped
        for tu_id, (result_count, last_ts) in agent_tool_result_counts.items():
            if last_ts is None:
                continue
            for link in all_agent_links.values():
                if link['tool_use_id'] == tu_id:
                    required = 2 if link.get('is_background') else 1
                    if result_count >= required:
                        agent_stopped_list.append({
                            'agent_session_id': link['agent_id'],
                            'stopped_at': last_ts.isoformat(),
                        })
                    break

        project_directory = first_cwd if first_cwd and session.type == SessionType.SESSION else None

        if not last_resolved_git_directory and last_cwd:
            cwd_git = resolve_git_from_path(last_cwd)
            if cwd_git:
                last_resolved_git_directory, last_resolved_git_branch = cwd_git

        # Provider-specific filesystem-derived session fields merged into
        # ``session_fields`` below (e.g. Claude Code's ``has_workflows``). The
        # default hook returns ``{}``; providers emit a key only when they want
        # it written, so a recompute never clobbers a value they choose to omit.
        extra_session_fields = self.extra_session_fields(session)

        # Main session: rebuild the plan-doc list authoritatively from the
        # full replay, plus filesystem-derived events (Claude Code's native
        # plan file). Subagent: its own row keeps the authoritative [] and
        # the events ship in the message for apply_session_complete to fold
        # into the top-level ancestor's list.
        parent_plan_doc_events: list[dict] | None = None
        plan_paths: list[dict] = []
        if is_main_session:
            plan_doc_events.extend(self.extra_doc_edit_events(session, last_slug=last_slug))
            plan_doc_roots = self._plan_doc_roots(session)
            plan_paths, _ = apply_doc_edit_events(
                [], plan_doc_events,
                project_root=plan_doc_roots[0] if plan_doc_roots else None,
            )
            refresh_entries_existence(plan_paths, plan_doc_roots)
        elif plan_doc_events:
            parent_plan_doc_events = [
                {
                    'path': event.path,
                    'action': event.action,
                    'timestamp': timestamp.isoformat() if timestamp else None,
                }
                for event, timestamp in plan_doc_events
            ]

        result_queue.put(orjson.dumps({
            'type': 'session_complete',
            'provider': self.provider.value,
            'run_id': run_id,
            'session_id': session_id,
            'project_id': session.project_id,
            # Revision marker — the session's last_offset as this worker saw
            # it. apply_session_complete skips the apply if the row's
            # last_offset has since advanced (watcher live-computed newer
            # lines), so a stale worker result can't clobber fresher data.
            'observed_last_offset': session.last_offset,
            'item_updates': all_item_updates,
            'item_fields': [
                'display_level', 'group_head', 'group_tail', 'kind', 'message_id',
                'cost', 'context_usage', 'timestamp', 'git_directory', 'git_branch',
            ],
            'content_overrides': content_overrides,
            'tool_result_links_to_create': trl_to_create,
            'tool_result_links_to_update': trl_to_update,
            'tool_result_links_to_delete': trl_to_delete,
            'agent_links_to_create': agent_links_to_create,
            'agent_links_to_update': agent_links_to_update,
            'agent_links_to_delete': agent_links_to_delete,
            'session_fields': {
                'compute_version': self.compute_version,
                'user_message_count': user_message_count,
                'context_usage': last_context_usage,
                'cwd': last_cwd,
                'cwd_git_branch': last_cwd_git_branch,
                'git_directory': last_resolved_git_directory,
                'git_branch': last_resolved_git_branch,
                'model': last_model,
                'slug': last_slug,
                # Only emit ``context_max`` when the JSONL gave us a real
                # value (Codex's ``task_started.model_context_window``).
                # Providers that don't expose it (Claude Code) leave the
                # accumulator at ``None`` and we must NOT serialise the
                # key — otherwise the bulk update would clobber the
                # user-set value coming from the agent settings dialog.
                **({'context_max': last_context_max} if last_context_max is not None else {}),
                # Full recompute is authoritative for the whole file: reset to
                # {} when the session carries no task/plan state at all.
                'tasks': last_tasks_snapshot if last_tasks_snapshot is not None else {},
                # Authoritative too — [] when no plan-doc was ever touched.
                'plan_paths': plan_paths,
                # Authoritative for the whole file too — [] when no goal ever set.
                'goals': goals,
                'compacted': found_compact_summary,
                'created_at': first_timestamp.isoformat() if first_timestamp else None,
                'last_started_at': last_started_at.isoformat() if last_started_at else None,
                'last_updated_at': (
                    datetime.fromtimestamp(session.mtime, tz=UTC).isoformat()
                    if session.mtime
                    else (last_updated_at.isoformat() if last_updated_at else None)
                ),
                'last_stopped_at': (
                    datetime.fromtimestamp(session.mtime, tz=UTC).isoformat()
                    if session.mtime
                    else None
                ),
                # Provider-specific filesystem-derived fields (Claude Code:
                # ``has_workflows``). Empty for providers without the hook.
                **extra_session_fields,
            },
            'titles': session_titles,
            'project_directory': project_directory,
            'affected_days': sorted(affected_days) if affected_days else None,
            'agent_stopped': agent_stopped_list or None,
            # Subagent-detected plan-doc events, folded into the top-level
            # ancestor's plan_paths by apply_session_complete (None for main
            # sessions and event-less subagents).
            'parent_plan_doc_events': parent_plan_doc_events,
        }))

        connection.close()

    @staticmethod
    @transaction.atomic
    def apply_session_complete(msg: dict) -> ComputeApplyResult:
        """
        Apply a ``session_complete`` payload produced by :meth:`compute_session_metadata`.

        Pure DB plumbing — no provider hook involved. Performs item
        bulk_updates, link create/update/delete diffs, session field
        updates, cost recalculation, title persistence, and project
        metadata refresh. Returns an explicit apply outcome and, after a
        successful apply, the optional top-level ancestor whose ``plan_paths``
        changed.

        Wrapped in ``transaction.atomic`` so the dozen-ish SQL statements
        below run as a single transaction: one write-lock acquisition and
        one fsync per session instead of one per statement.
        """
        session_id = msg['session_id']

        # Revision guard. The worker computed this result against the session
        # as it was when it read the row (observed_last_offset). If the watcher
        # has since appended new JSONL lines and live-computed them, the row's
        # last_offset has advanced past what the worker saw — the worker's
        # result is stale. Applying it would overwrite the watcher's fresher
        # metadata and still bump compute_version to current, which would also
        # stop a later recompute pass from correcting it.
        #
        # The check is a conditional no-op UPDATE, not a SELECT, so it is the
        # transaction's first statement and takes the write lock up front. A
        # SELECT-then-write guard would open a read snapshot a concurrent
        # watcher commit could invalidate, turning the whole apply into a
        # SQLITE_BUSY_SNAPSHOT / "database is locked" failure instead of a
        # clean skip. 0 rows matched => last_offset advanced past the observed
        # value (or the row is gone) => skip. compute's session_fields never
        # include last_offset, so only the watcher advances it — a reliable
        # monotonic revision marker.
        observed_last_offset = msg.get('observed_last_offset')
        if observed_last_offset is not None:
            rows = Session.objects.filter(
                id=session_id, last_offset__lte=observed_last_offset,
            ).update(last_offset=F('last_offset'))
            if rows == 0:
                outcome = "superseded" if Session.objects.filter(id=session_id).exists() else "missing"
                logger.info(
                    "apply_session_complete: %s compute result for session %s "
                    "at observed last_offset %s",
                    outcome, session_id, observed_last_offset,
                )
                return ComputeApplyResult(outcome)
        elif not Session.objects.filter(id=session_id).exists():
            return ComputeApplyResult("missing")

        # 1. Apply item updates (only items that changed)
        item_updates = msg.get('item_updates', [])
        item_fields = msg.get('item_fields', [])
        if item_updates and item_fields:
            items = [
                SessionItem(id=upd['id'], **{
                    field: Decimal(value) if (value := upd.get(field)) is not None and field == 'cost' else value
                    for field in item_fields
                })
                for upd in item_updates
            ]
            SessionItem.objects.bulk_update(items, item_fields, batch_size=50)

        # 2. Apply content overrides (rare: provider-specific transformations applied at compute time)
        content_overrides = msg.get('content_overrides', [])
        if content_overrides:
            items = [
                SessionItem(id=ovr['id'], content=ovr['content'])
                for ovr in content_overrides
            ]
            SessionItem.objects.bulk_update(items, ['content'], batch_size=50)

        # 3. Sync tool result links (diff-based: create/update/delete)
        trl_to_create = msg.get('tool_result_links_to_create', [])
        if trl_to_create:
            links = [
                ToolResultLink(
                    session_id=d['session_id'],
                    tool_use_line_num=d['tool_use_line_num'],
                    tool_result_line_num=d['tool_result_line_num'],
                    tool_use_id=d['tool_use_id'],
                    tool_name=d['tool_name'],
                    tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                    extra=d.get('extra'),
                    error=d.get('error'),
                )
                for d in trl_to_create
            ]
            ToolResultLink.objects.bulk_create(links, ignore_conflicts=True, batch_size=50)

        trl_to_update = msg.get('tool_result_links_to_update', [])
        if trl_to_update:
            trl_update_fields = ['tool_use_line_num', 'tool_name', 'tool_result_at', 'extra', 'error']
            links = [
                ToolResultLink(
                    id=d['id'],
                    session_id=d['session_id'],
                    tool_use_line_num=d['tool_use_line_num'],
                    tool_result_line_num=d['tool_result_line_num'],
                    tool_use_id=d['tool_use_id'],
                    tool_name=d['tool_name'],
                    tool_result_at=datetime.fromisoformat(d['tool_result_at']) if d.get('tool_result_at') else None,
                    extra=d.get('extra'),
                    error=d.get('error'),
                )
                for d in trl_to_update
            ]
            ToolResultLink.objects.bulk_update(links, trl_update_fields, batch_size=50)

        trl_to_delete = msg.get('tool_result_links_to_delete', [])
        if trl_to_delete:
            ToolResultLink.objects.filter(id__in=trl_to_delete).delete()

        # 4. Sync agent links (diff-based: create/update/delete)
        agent_links_to_create = msg.get('agent_links_to_create', [])
        if agent_links_to_create:
            links = [
                AgentLink(
                    session_id=d['session_id'],
                    tool_use_line_num=d['tool_use_line_num'],
                    tool_use_id=d['tool_use_id'],
                    agent_id=d['agent_id'],
                    is_background=d['is_background'],
                    started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
                )
                for d in agent_links_to_create
            ]
            AgentLink.objects.bulk_create(links, ignore_conflicts=True, batch_size=50)

        agent_links_to_update = msg.get('agent_links_to_update', [])
        if agent_links_to_update:
            agent_link_fields = ['tool_use_line_num', 'is_background', 'started_at']
            links = [
                AgentLink(
                    id=d['id'],
                    session_id=d['session_id'],
                    tool_use_line_num=d['tool_use_line_num'],
                    tool_use_id=d['tool_use_id'],
                    agent_id=d['agent_id'],
                    is_background=d['is_background'],
                    started_at=datetime.fromisoformat(d['started_at']) if d.get('started_at') else None,
                )
                for d in agent_links_to_update
            ]
            AgentLink.objects.bulk_update(links, agent_link_fields, batch_size=50)

        agent_links_to_delete = msg.get('agent_links_to_delete', [])
        if agent_links_to_delete:
            AgentLink.objects.filter(id__in=agent_links_to_delete).delete()

        # 5. Update session fields (always includes compute_version)
        session_fields = msg.get('session_fields', {})
        if session_fields:
            for dt_field in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
                if dt_field in session_fields and session_fields[dt_field] is not None:
                    session_fields[dt_field] = datetime.fromisoformat(session_fields[dt_field])
            # The recompute folded ``goals`` from scratch, so a UI-set
            # ``dismissed`` flag (the one non-compute-owned key in that JSON)
            # would be dropped — re-port it from the stored row. Safe read:
            # this apply runs serialized through the DB writer under the shared
            # write lock (and inside ``transaction.atomic``), the same lock the
            # dismiss PATCH takes, so the stored value can't move under us.
            if session_fields.get('goals'):
                db_goals = Session.objects.filter(id=session_id).values_list('goals', flat=True).first()
                if db_goals:
                    preserve_dismissed_flags(db_goals, session_fields['goals'])
            # The plans watcher (native-plan latch) and subagent compute
            # passes write plan_paths entries this rebuild can't reproduce
            # from the session's own lines — and their writes don't advance
            # last_offset, so the revision guard above can't catch them. Fold
            # them back in before the authoritative overwrite (same
            # just-before-write re-read as ``goals`` right above).
            if 'plan_paths' in session_fields:
                db_plan_paths = Session.objects.filter(id=session_id).values_list('plan_paths', flat=True).first()
                session_fields['plan_paths'] = fold_concurrent_entries(
                    session_fields['plan_paths'], db_plan_paths, sources=FOLDED_SOURCES,
                )
            if 'compute_version' in session_fields:
                session_fields['search_version'] = None
            rows = Session.objects.filter(id=session_id).update(**session_fields)
            if rows == 0:
                logger.debug(f"apply_session_complete: session {session_id} not found for update")
                return ComputeApplyResult("missing")
            else:
                logger.debug(
                    f"apply_session_complete: session {session_id} updated"
                    f" (compute_version={session_fields.get('compute_version')})"
                )

        # Remap private snapshot anchors before advancing outside this atomic
        # metadata apply. The greatest matching line includes every canonical
        # row that shares the source timestamp.
        snapshot_anchor_key = "_codex_rollout_migration_anchor"
        shares = Share.objects.select_for_update().filter(session_id=session_id)
        for share in shares:
            options = share.options
            anchor_payload = options.get(snapshot_anchor_key)
            if options.get("mode") != "snapshot" or anchor_payload is None:
                continue

            frozen_at_line = 0
            timestamp_text = anchor_payload.get("timestamp") if isinstance(anchor_payload, dict) else None
            if isinstance(timestamp_text, str):
                try:
                    anchor_timestamp = datetime.fromisoformat(timestamp_text)
                    if anchor_timestamp.tzinfo is None:
                        anchor_timestamp = anchor_timestamp.replace(tzinfo=UTC)
                    matched_line = SessionItem.objects.filter(
                        session_id=session_id,
                        timestamp__lte=anchor_timestamp,
                    ).order_by("-line_num").values_list("line_num", flat=True).first()
                    if matched_line is not None:
                        frozen_at_line = matched_line
                except ValueError:
                    pass

            new_options = dict(options)
            new_options["frozen_at_line"] = frozen_at_line
            new_options.pop(snapshot_anchor_key, None)
            share.options = new_options
            share.save(update_fields=["options"])

        # 6. Recalculate session costs from SessionItem data (idempotent)
        session = Session.objects.get(id=session_id)
        session.recalculate_costs()
        session.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

        # 7. Recalculate parent session costs if subagent
        if session.parent_session_id:
            parent = Session.objects.get(id=session.parent_session_id)
            parent.recalculate_costs()
            parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

        # 7bis. Fold subagent-detected plan docs into the top-level ancestor
        folded_ancestor_id: str | None = None
        parent_plan_doc_events = msg.get('parent_plan_doc_events')
        if parent_plan_doc_events and session.parent_session_id:
            folded_ancestor_id = BaseSessionCompute._fold_plan_doc_events_into_ancestor(
                session,
                [
                    (DocEditEvent(d['path'], d['action'], 'subagent'), d.get('timestamp'))
                    for d in parent_plan_doc_events
                ],
            )

        # 8. Update session titles
        titles = msg.get('titles', {})
        for target_id, title in titles.items():
            Session.objects.filter(id=target_id).update(title=title)

        # 9. Update project directory
        project_id = msg.get('project_id')
        project_directory = msg.get('project_directory')
        if project_id and project_directory:
            ensure_project_directory(project_id, project_directory)

        # 10. Resolve project git_root if session has git info but project doesn't
        session_git_dir = session_fields.get('git_directory') if session_fields else None
        if session_git_dir and project_id and get_project_git_root(project_id) is None:
            # Pass project_directory explicitly. Step 9's ensure_project_directory
            # defers its directory-cache write to on_commit, so a bare
            # ensure_project_git_root(project_id) would fall back to the
            # pre-step-9 cached directory and resolve git_root from the wrong
            # path. When project_directory is None, step 9 did not run and the
            # cache fallback is reliable, so passing None is also correct.
            ensure_project_git_root(project_id, project_directory)

        # 11. Update last_stopped_at for subagents that finished naturally.
        # Same monotonic guard as check_agent_naturally_stopped: a batch
        # recompute of the parent must not re-freeze as "stopped" a resumable
        # subagent whose live sync already recorded newer activity.
        agent_stopped = msg.get('agent_stopped')
        if agent_stopped:
            for entry in agent_stopped:
                stopped_at = datetime.fromisoformat(entry['stopped_at'])
                Session.objects.filter(id=entry['agent_session_id']).exclude(
                    last_updated_at__gt=stopped_at,
                ).update(
                    last_stopped_at=stopped_at, last_updated_at=stopped_at,
                )

        # 12. Update project metadata (sessions_count, mtime, total_cost)
        if project_id:
            update_project_metadata(project_id)

        return ComputeApplyResult("applied", folded_ancestor_id)

    # ------------------------------------------------------------------
    # Watcher orchestration — concrete in later steps
    # ------------------------------------------------------------------

    @transaction.atomic
    def sync_session_items_from_file(
        self,
        session: Session,
        file_path: Path,
    ) -> tuple[
        list[int],
        list[int],
        list[AgentLinkUpdate],
        list[WorkflowLinkUpdate],
        list[ToolResultUpdate],
        list[AgentStoppedUpdate],
        bool,
    ]:
        """
        Synchronise new lines from ``file_path`` into ``session``.

        Reads from ``session.last_offset``, transforms / parses every new
        line, computes metadata, persists items, links, lifecycle
        timestamps, costs, and returns the broadcast payload tuple
        ``(new_line_nums, modified_line_nums, agent_link_updates,
        workflow_link_updates, tool_result_updates, agent_stopped_updates,
        found_compact_summary)``.

        ``found_compact_summary`` is ``True`` when this batch ingested at
        least one ``COMPACT_SUMMARY`` line. It is the live, append-only
        signal the Codex watcher relays to the agent manager so a live
        agent knows a compaction just landed (a manually-triggered
        ``/compact`` ends its ``ASSISTANT_TURN`` on it). Never fires during
        the background recompute — that path goes through
        :meth:`compute_session_metadata`, not this method.

        Generic algorithm — every parsing or provider-specific decision
        is delegated through hooks (``transform_inline``,
        ``transform_tool_result_with_cache``, ``compute_item_metadata``,
        ``extract_item_timestamp``, ``compute_item_cost_and_usage``,
        ``extract_runtime_fields``, ``is_session_start_marker``,
        ``extract_title_from_user_message``, ``extract_subagent_marker``,
        ``extract_user_message_text``, ``extract_custom_title``,
        ``apply_session_title``).
        """
        if not file_path.exists():
            return [], [], [], [], [], [], False

        stat = file_path.stat()
        file_mtime = stat.st_mtime

        # If mtime hasn't changed and no new data appended, nothing to do.
        # Check file size too: mtime has ~1s resolution, so two writes within the same second
        # share the same mtime. Without the size check, the second write would be silently skipped.
        if session.mtime == file_mtime and session.last_offset >= stat.st_size:
            return [], [], [], [], [], [], False

        # Read raw bytes, then decode leniently — see
        # read_session_items_from_file in sync_helpers.py for the full
        # rationale. The watcher's global try/except would catch a strict-decode
        # UnicodeDecodeError, but the session's offset would never advance, so a
        # durably-corrupt file would re-fail on every watcher event and block
        # all further live updates for that session. Replacing invalid bytes
        # with U+FFFD confines the damage to its own line; reading in binary
        # keeps last_offset an exact byte count.
        with open(file_path, "rb") as f:
            f.seek(session.last_offset)
            raw = f.read()
            # Capture file position immediately to release the file.
            new_offset = f.tell()

        try:
            new_content = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Invalid UTF-8 in %s — decoding with replacement", file_path)
            new_content = raw.decode("utf-8", errors="replace")

        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return [], [], [], [], [], [], False

        lines = [line for line in new_content.split("\n") if line.strip()]

        session.last_offset = new_offset
        session.mtime = file_mtime

        if not lines:
            session.save(update_fields=["last_offset", "mtime"])
            return [], [], [], [], [], [], False

        # Create SessionItem objects for bulk insert
        items_to_create: list[tuple[SessionItem, dict]] = []
        current_line_num = session.last_line

        # Track title updates (session_id -> title)
        session_title_updates: dict[str, str] = {}
        # Track if we've already set initial title for this session (from first user message ever)
        initial_title_needs_set = session.title is None

        # Track first timestamp in this batch (for session.created_at)
        first_timestamp: datetime | None = None

        # Track lifecycle timestamps for this batch
        last_started_at_update: datetime | None = None
        last_updated_at: datetime | None = None
        last_new_content_at: datetime | None = None
        # Latest subagent turn boundary seen in this batch (see
        # :meth:`subagent_turn_boundary`): ``True`` = went idle, ``False`` =
        # started working again, ``None`` = no boundary, leave the stored
        # ``last_stopped_at`` alone. ``last_turn_end_at`` carries the moment
        # of the idle one.
        subagent_idle: bool | None = None
        last_turn_end_at: datetime | None = None
        subagent_lifecycle_changed = False

        # Track last seen values for runtime environment fields
        first_cwd: str | None = None
        last_cwd: str | None = None
        last_cwd_git_branch: str | None = None
        last_model: str | None = None
        last_slug: str | None = None
        last_context_max: int | None = None
        # Latest task/todo/plan snapshot seen in this batch of new lines
        # (None = no task line here, keep the stored Session.tasks untouched).
        last_tasks_snapshot: dict | None = None
        # Plan-doc write/delete events carried by this batch (empty = leave
        # the stored Session.plan_paths untouched). For subagent files the
        # events are folded into the top-level ancestor's list after the
        # save (the subagent's own row keeps its default []).
        plan_doc_events: list[tuple[DocEditEvent, datetime | None]] = []
        is_main_session = session.type == SessionType.SESSION
        # Goal history folded incrementally onto the persisted list: start from
        # the stored state and apply only this batch's transitions.
        goals = copy.deepcopy(session.goals) if session.goals else []
        goals_changed = False

        # Updates to broadcast after processing
        agent_link_updates: list[AgentLinkUpdate] = []
        workflow_link_updates: list[WorkflowLinkUpdate] = []
        tool_result_updates: list[ToolResultUpdate] = []
        agent_stopped_updates: list[AgentStoppedUpdate] = []

        # Track if a compact_summary item was found in this batch
        found_compact_summary = False

        # For subagents: track if we need to create the link between the agent
        # and the parent session tool use
        subagent_needs_link = (
            session.type == SessionType.SUBAGENT
            and session.parent_session_id
            and not is_agent_link_done(session.parent_session_id, session.id)
        )

        # Load existing message_ids for deduplication of cost computation
        seen_message_ids: set[str] = set(
            SessionItem.objects.filter(
                session_id=session.id,
                message_id__isnull=False,
            ).values_list('message_id', flat=True)
        )

        # Track items already processed in this batch so ``transform_inline``
        # can resolve ``<twicc:insert-screenshot />`` tags against
        # tool_results that landed in the same JSONL batch (and are
        # therefore not yet committed to the DB). Each entry carries the
        # ``(line_num, timestamp, parsed_json)`` triple the walker needs.
        processed_items: list[tuple[int, datetime | None, dict]] = []

        for line in lines:
            line = line.strip()
            if not line:
                line = "{}"
            current_line_num += 1
            item = SessionItem(
                session=session,
                line_num=current_line_num,
                content=line,
            )
            try:
                parsed = orjson.loads(line)
            except orjson.JSONDecodeError:
                parsed = {}

            # Extract timestamp first so transform_inline can stash it in
            # processed_items (used by the screenshot substitution
            # helper). Provider hooks do not depend on the timestamp
            # being set on the item before they run.
            item.timestamp = self.extract_item_timestamp(parsed)
            if first_timestamp is None and item.timestamp is not None:
                first_timestamp = item.timestamp

            # Provider rewrites + the generic context-tag strip, both applied
            # in place; see :meth:`transform_inline`.
            new_content = self.transform_inline(
                parsed,
                session_id=session.id,
                line_num=current_line_num,
                in_memory_items=processed_items,
            )
            if new_content is not None:
                item.content = new_content

            # Provider-specific tool_result enrichment from out-of-band caches
            # (e.g. Claude Code injects originalFile from the PreToolUse hook).
            if self.is_tool_result_item(parsed):
                try:
                    enriched = self.transform_tool_result_with_cache(
                        parsed, session.id, current_line_num,
                    )
                    if enriched is not None:
                        item.content = enriched
                except Exception:
                    logger.exception(
                        "Failed to enrich tool_result with cached data "
                        "(session=%s, line=%d)",
                        session.id, current_line_num,
                    )

            # Make the parsed view of the current item visible to the
            # next iteration's transform_inline. Appended AFTER the
            # transform call so a tag can only resolve against items
            # *before* the one that wrote it.
            processed_items.append((current_line_num, item.timestamp, parsed))

            # Pre-compute display_level + kind (no group info yet)
            metadata = self.compute_item_metadata(parsed)
            item.display_level = metadata['display_level']
            item.kind = metadata['kind']

            # Track compact summary for session.compacted flag
            if item.kind == ItemKind.COMPACT_SUMMARY:
                found_compact_summary = True

            # Refresh the running task/todo/plan snapshot (keep last non-None).
            tasks_snapshot = self.build_tasks_snapshot(
                parsed, line_num=current_line_num, timestamp=item.timestamp,
            )
            if tasks_snapshot is not None:
                last_tasks_snapshot = tasks_snapshot

            # Fold any goal-lifecycle transition onto the running history.
            goal_event = self.extract_goal_event(parsed)
            if goal_event is not None and apply_goal_event(
                goals, goal_event, timestamp=item.timestamp
            ):
                goals_changed = True

            # Track lifecycle timestamps
            if item.timestamp is not None:
                last_updated_at = item.timestamp
            if item.timestamp is not None and item.kind == ItemKind.ASSISTANT_MESSAGE:
                last_new_content_at = item.timestamp
            if item.timestamp is not None and self.is_session_start_marker(parsed):
                last_started_at_update = item.timestamp
            if not is_main_session:
                boundary = self.subagent_turn_boundary(parsed)
                if boundary is not None:
                    subagent_idle = boundary
                    if boundary:
                        last_turn_end_at = item.timestamp or last_updated_at

            # Extract runtime environment fields (keep last non-null value)
            runtime = self.extract_runtime_fields(parsed)
            if runtime.get('cwd'):
                if first_cwd is None:
                    first_cwd = runtime['cwd']
                last_cwd = runtime['cwd']
            if runtime.get('cwd_git_branch'):
                last_cwd_git_branch = runtime['cwd_git_branch']
            if runtime.get('model'):
                last_model = runtime['model']
            if runtime.get('slug'):
                last_slug = runtime['slug']
            if runtime.get('context_max') is not None:
                last_context_max = runtime['context_max']

            # Detect plan-doc writes/deletes. After the runtime block so a
            # cwd carried by this very line is already in ``last_cwd``; the
            # live accumulator is batch-local (starts at None), so fall back
            # to the persisted cwd — analogous to the model fallback below.
            for doc_event in self.extract_doc_edit_events(parsed, cwd=last_cwd or session.cwd):
                plan_doc_events.append((doc_event, item.timestamp))

            # Compute cost and context usage with the running model state.
            # Live mode only tracks ``last_model`` within the current batch,
            # so we fall back to the persisted ``Session.model`` when this
            # batch hasn't yet observed a fresh ``turn_context`` (typical
            # for Codex billing items arriving mid-turn).
            self.compute_item_cost_and_usage(
                item, parsed, seen_message_ids, last_model or session.model,
            )

            items_to_create.append((item, parsed))

            # Handle title extraction
            if item.kind == ItemKind.USER_MESSAGE and initial_title_needs_set:
                title = self.extract_title_from_user_message(parsed)
                if title:
                    session_title_updates[session.id] = title
                    initial_title_needs_set = False

            # For subagents: create the link from the first line that
            # carries a subagent marker.
            if subagent_needs_link:
                agent_id = self.extract_subagent_marker(parsed)
                if agent_id:
                    prompt = get_cached_agent_prompt(session.parent_session_id, agent_id)
                    if not prompt:
                        # Try to read it from the subagent's first user message in DB
                        first_user_message = (
                            session.items.filter(kind=ItemKind.USER_MESSAGE).first()
                        )
                        if first_user_message is not None:
                            try:
                                first_parsed = orjson.loads(first_user_message.content)
                            except orjson.JSONDecodeError:
                                pass
                            else:
                                prompt = self.extract_user_message_text(first_parsed)
                        # Fallback: this batch carries the first user message itself
                        if not prompt and item.kind == ItemKind.USER_MESSAGE:
                            prompt = self.extract_user_message_text(parsed)

                        if prompt:
                            cache_agent_prompt(session.parent_session_id, agent_id, prompt)
                            agent_update = self.create_agent_link_from_subagent(
                                parent_session_id=session.parent_session_id,
                                agent_id=agent_id,
                                agent_prompt=prompt,
                            )
                            if agent_update:
                                agent_link_updates.append(agent_update)
                                subagent_needs_link = False

            if item.kind == ItemKind.SYSTEM:
                custom = self.extract_custom_title(parsed)
                if custom is not None:
                    target_session_id, custom_title = custom
                    session_title_updates[target_session_id or session.id] = custom_title

        # Bulk create all items
        items_only = [item for item, _ in items_to_create]
        SessionItem.objects.bulk_create(items_only, ignore_conflicts=True, batch_size=50)

        # Track line_nums of new and updated items
        new_line_nums: set[int] = {item.line_num for item in items_only}
        modified_line_nums: set[int] = set()

        # Second pass: compute group membership, tool_result links, and update cost/usage/timestamp fields
        for item, parsed in items_to_create:
            update_fields = {
                'message_id': item.message_id,
                'cost': item.cost,
                'context_usage': item.context_usage,
                'timestamp': item.timestamp,
            }

            # Resolve git directory/branch from tool_use paths for every
            # item, mirroring the bulk-compute path. This must happen
            # outside the COLLAPSIBLE/ALWAYS gate so DEBUG_ONLY items
            # carrying tool paths (e.g. Codex's event_msg.patch_apply_end)
            # also contribute to git resolution. Anchored to the session's
            # cwd (falling back to this batch's cwd on the first sync) so
            # unrelated repos merely touched by the session are ignored.
            git_resolution = self.resolve_git_for_item(
                parsed, anchor_dir=session.cwd or last_cwd, use_cache=False
            )
            if git_resolution is not None:
                item.git_directory, item.git_branch = git_resolution
                update_fields['git_directory'] = item.git_directory
                update_fields['git_branch'] = item.git_branch

            # Group membership for COLLAPSIBLE and ALWAYS items
            if item.display_level in (ItemDisplayLevel.COLLAPSIBLE, ItemDisplayLevel.ALWAYS):
                item_modified_lines = self.compute_item_metadata_live(session.id, item, parsed)
                modified_line_nums.update(item_modified_lines)
                update_fields['group_head'] = item.group_head
                update_fields['group_tail'] = item.group_tail

            # Update the item in DB with all computed fields
            SessionItem.objects.filter(
                session=session,
                line_num=item.line_num,
            ).update(**update_fields)

            # Tool result links (tool_result items are DEBUG_ONLY)
            if self.is_tool_result_item(parsed):
                # Create/upgrade the agent link BEFORE the naturally-stopped
                # check: on an async launch ack both fire on the same line,
                # and the check must see the link's final ``is_background``
                # (an ack upgrading a prompt-matched link would otherwise be
                # counted as the single result of a foreground agent and
                # stop it immediately).
                if update := self.create_agent_link_from_tool_result(session.id, item, parsed):
                    agent_link_updates.append(update)
                tool_result_update = self.create_tool_result_link_live(session.id, item, parsed)
                if tool_result_update:
                    tool_result_updates.append(tool_result_update)
                    if stopped := self.check_agent_naturally_stopped(session.id, tool_result_update):
                        agent_stopped_updates.append(stopped)
                if wf_update := self.create_workflow_link_from_tool_result(session.id, item, parsed):
                    workflow_link_updates.append(wf_update)

            # For parent sessions: check if this item contains agent-spawning tool_use(s)
            # and try to link them to existing subagents (race condition: subagent file
            # synced before the parent's tool_use line).
            if session.type == SessionType.SESSION and item.kind in (
                ItemKind.ASSISTANT_MESSAGE, ItemKind.CONTENT_ITEMS,
            ):
                agent_link_updates.extend(
                    self.create_agent_link_from_tool_use(session.id, item, parsed)
                )

        # Resolve project git_root if any item resolved git info but the project doesn't have one yet
        if any(item.git_directory for item, _ in items_to_create) and get_project_git_root(session.project_id) is None:
            ensure_project_git_root(session.project_id)

        # Apply title updates through the provider's hook (which can refuse the
        # update and re-write a correction in the underlying storage).
        for target_session_id, title in session_title_updates.items():
            applied = self.apply_session_title(target_session_id, title)
            if applied and target_session_id == session.id:
                session.title = title

        # Update session tracking fields
        session.last_line = current_line_num

        # Recompute user_message_count using the optimized index
        session.user_message_count = SessionItem.objects.filter(
            session=session,
            kind=ItemKind.USER_MESSAGE,
        ).count()

        # Update session cost and context usage from the new items
        # Find last context_usage among new items (most recent non-null value)
        for item, _ in reversed(items_to_create):
            if item.context_usage is not None:
                session.context_usage = item.context_usage
                break

        # Recalculate costs from DB (idempotent)
        session.recalculate_costs()

        # Update runtime environment fields if changed
        if last_cwd and last_cwd != session.cwd:
            # Update project directory only on first sync (when session.cwd was None)
            # The first cwd of a session is the project directory (where the agent CLI was launched)
            # Only for real sessions, not subagents (which may be launched from a different directory)
            if session.cwd is None and first_cwd and session.type == SessionType.SESSION:
                ensure_project_directory(session.project_id, first_cwd)
            session.cwd = last_cwd
        if last_cwd_git_branch and last_cwd_git_branch != session.cwd_git_branch:
            session.cwd_git_branch = last_cwd_git_branch
        if last_model and last_model != session.model:
            session.model = last_model
        if last_slug and last_slug != session.slug:
            session.slug = last_slug
        # Only override the persisted ``context_max`` when this batch
        # actually saw a JSONL value (Codex's task_started window). For
        # providers that don't expose it, ``last_context_max`` stays at
        # ``None`` and the user-set window survives untouched.
        if last_context_max is not None and last_context_max != session.context_max:
            session.context_max = last_context_max

        # Update resolved git directory/branch from the latest item that has one
        # (items are processed in order, so the last one wins)
        for item, _ in reversed(items_to_create):
            if item.git_directory:
                if item.git_directory != session.git_directory or item.git_branch != session.git_branch:
                    session.git_directory = item.git_directory
                    session.git_branch = item.git_branch
                break

        # Fallback: if no item provided git info, try resolving from the session's cwd.
        if not session.git_directory and session.cwd:
            cwd_git = resolve_git_from_path(session.cwd, use_cache=False)
            if cwd_git:
                session.git_directory, session.git_branch = cwd_git

        # Validate git state: verify git_directory still exists on disk and refresh branch.
        if session.git_directory:
            if os.path.isdir(session.git_directory):
                head_path = os.path.join(session.git_directory, '.git', 'HEAD')
                if not os.path.isfile(head_path):
                    git_file = os.path.join(session.git_directory, '.git')
                    if os.path.isfile(git_file):
                        try:
                            with open(git_file, 'r') as f:
                                content = f.read().strip()
                            if content.startswith('gitdir: '):
                                head_path = os.path.join(content[len('gitdir: '):], 'HEAD')
                        except OSError:
                            head_path = None
                    else:
                        head_path = None
                if head_path:
                    branch = read_head_branch(head_path)
                    if branch and branch != session.git_branch:
                        session.git_branch = branch
            else:
                # git_directory no longer exists: re-resolve through fallback chain
                resolved = None
                if session.cwd and os.path.isdir(session.cwd):
                    resolved = resolve_git_from_path(session.cwd, use_cache=False)
                if not resolved:
                    project_git_root = get_project_git_root(session.project_id)
                    if project_git_root and os.path.isdir(project_git_root):
                        head_path = os.path.join(project_git_root, '.git', 'HEAD')
                        branch = read_head_branch(head_path)
                        if branch:
                            resolved = (project_git_root, branch)
                if not resolved:
                    project_directory = get_project_directory(session.project_id)
                    if project_directory and os.path.isdir(project_directory):
                        resolved = resolve_git_from_path(project_directory, use_cache=False)
                if resolved:
                    session.git_directory, session.git_branch = resolved
                else:
                    session.git_directory = None
                    session.git_branch = None

        is_new_session = session.created_at is None and first_timestamp is not None
        if is_new_session:
            session.created_at = first_timestamp

        # Update lifecycle timestamps
        if last_started_at_update is not None:
            session.last_started_at = last_started_at_update
        elif is_new_session:
            session.last_started_at = first_timestamp
        if last_updated_at is not None:
            session.last_updated_at = last_updated_at
        if last_new_content_at is not None:
            session.last_new_content_at = last_new_content_at
        # A subagent has no process of its own, so nothing but its file says
        # whether it is still working. When the provider recognises a turn
        # boundary in it (:meth:`subagent_turn_boundary`), that is the
        # authoritative "running / idle" signal for every consumer of
        # ``last_stopped_at`` — the tab's process indicator, the parent's
        # spawn card. ``None`` (no boundary in this batch) leaves the stored
        # value untouched, so the parent-side rule keeps its say.
        if subagent_idle is not None:
            session.last_stopped_at = last_turn_end_at if subagent_idle else None
            subagent_lifecycle_changed = True

        # Mark session as compacted if a compact_summary item was found
        if found_compact_summary and not session.compacted:
            session.compacted = True

        # Recalculate activity counters for affected days
        affected_days = {
            item.timestamp.date()
            for item, _ in items_to_create
            if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost)
        }
        if is_new_session and session.type == SessionType.SESSION and first_timestamp:
            affected_days.add(first_timestamp.date())

        session_update_fields = [
            "last_offset", "last_line", "mtime", "user_message_count", "context_usage",
            "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch",
            "git_directory", "git_branch", "model", "slug", "created_at",
            "last_started_at", "last_updated_at", "last_new_content_at", "compacted",
        ]
        if subagent_lifecycle_changed:
            session_update_fields.append("last_stopped_at")
        # Persist a refreshed task snapshot only when this batch carried one, so
        # a batch with no task line leaves the stored Session.tasks intact.
        if last_tasks_snapshot is not None:
            session.tasks = last_tasks_snapshot
            session_update_fields.append("tasks")
        # Merge this batch's plan-doc events into the stored list (additive —
        # unlike the batch recompute, a live batch never resets entries it
        # didn't see). The plans watcher and subagent folds write plan_paths
        # concurrently: fold in their fresher entries from a just-before-write
        # re-read, like the goals re-read below. Subagent files fold into the
        # top-level ancestor after the save instead (see below).
        if plan_doc_events and is_main_session:
            plan_doc_roots = self._plan_doc_roots(session)
            new_plan_paths, plan_paths_changed = apply_doc_edit_events(
                session.plan_paths, plan_doc_events,
                project_root=plan_doc_roots[0] if plan_doc_roots else None,
            )
            if plan_paths_changed:
                refresh_entries_existence(new_plan_paths, plan_doc_roots)
                db_plan_paths = Session.objects.filter(id=session.id).values_list("plan_paths", flat=True).first()
                session.plan_paths = fold_concurrent_entries(
                    new_plan_paths, db_plan_paths, sources=FOLDED_SOURCES,
                )
                session_update_fields.append("plan_paths")
        # Persist the folded goal history only when this batch changed it. The
        # fold started from a copy of the row taken at batch start, so a
        # ``dismissed`` flag PATCHed by the user while this batch was being
        # processed would be silently dropped — re-port it from a fresh read
        # just before writing.
        if goals_changed:
            db_goals = Session.objects.filter(id=session.id).values_list("goals", flat=True).first()
            if db_goals:
                preserve_dismissed_flags(db_goals, goals)
            session.goals = goals
            session_update_fields.append("goals")
        session.save(update_fields=session_update_fields)

        # Recalculate activities after session.save (needs created_at in DB for session_count)
        from twicc.core.models import PeriodicActivity
        PeriodicActivity.recalculate_for_days(
            session.project_id, affected_days, provider=self.provider,
        )

        # If this is a subagent, propagate cost to parent session
        if session.type == SessionType.SUBAGENT and session.parent_session_id:
            try:
                parent = Session.objects.get(id=session.parent_session_id)
            except Session.DoesNotExist:
                pass
            else:
                parent.recalculate_costs()
                parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

        # Fold subagent-detected plan docs into the top-level ancestor's
        # plan_paths. The watcher's post-sync parent broadcast (a refreshed
        # session_updated) carries the updated list to the frontend.
        if plan_doc_events and not is_main_session:
            self._fold_plan_doc_events_into_ancestor(
                session,
                [(event._replace(source='subagent'), ts) for event, ts in plan_doc_events],
            )

        # Exclude new items from modified_line_nums
        return (
            sorted(new_line_nums),
            sorted(modified_line_nums - new_line_nums),
            agent_link_updates,
            workflow_link_updates,
            tool_result_updates,
            agent_stopped_updates,
            found_compact_summary,
        )
