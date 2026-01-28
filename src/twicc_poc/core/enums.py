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
    API_ERROR = "api_error"
