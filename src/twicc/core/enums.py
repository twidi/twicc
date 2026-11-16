"""
Enums for session items metadata.

These enums define the possible values for computed metadata fields
on SessionItem objects.
"""

from enum import IntEnum, StrEnum


class ItemDisplayLevel(IntEnum):
    """Display level for session items, determining visibility in different modes."""
    ALWAYS = 1       # Always shown in all modes
    COLLAPSIBLE = 2  # Shown in Normal, grouped in Simplified
    DEBUG_ONLY = 3   # Only shown in Debug mode


class ItemKind(StrEnum):
    """Kind/category of session items."""
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    CONTENT_ITEMS = "content_items"
    TOOL_USE = "tool_use"
    API_ERROR = "api_error"
    COMPACT_SUMMARY = "compact_summary"
    SYSTEM = "system"
    # ``reasoning`` lines from providers that expose model reasoning as a
    # standalone JSONL line (Codex ``response_item.reasoning`` with a
    # non-empty ``summary``). Falls through to ``COLLAPSIBLE`` display level,
    # so it joins tool_use et al. in the natural group_head/group_tail
    # machinery. A reasoning line whose ``summary`` is empty stays bucketed
    # as ``SYSTEM`` (the encrypted_content is opaque to us and useless to
    # render) and lands at ``DEBUG_ONLY``.
    REASONING = "reasoning"
    # Generated-image lines from providers that emit an image alongside its
    # prompt and an on-disk path (Codex ``event_msg.image_generation_end``).
    # Treated as standalone content rather than as a tool_use → tool_result
    # pair: the JSONL line itself already carries the base64 image, the
    # revised prompt and the saved path, so there's nothing left to wait
    # for. Lands at ``ALWAYS`` so the rendered image stays visible across
    # all display modes (Normal, Simplified, Debug).
    IMAGE = "image"


class Provider(StrEnum):
    """Backend provider that produced a session."""
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    GEMINI = "gemini"


class ShareKind(StrEnum):
    """What a share link targets."""
    SESSION = "session"
    ARTIFACT = "artifact"
