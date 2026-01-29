"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""

from django.conf import settings


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "archived": project.archived,
    }


def serialize_session(session):
    """
    Serialize a Session model to a dictionary.

    Includes compute_version_up_to_date boolean to indicate if the session's
    metadata has been computed with the current version of rules.
    """
    return {
        "id": session.id,
        "project_id": session.project_id,
        "last_line": session.last_line,
        "mtime": session.mtime,
        "archived": session.archived,
        "title": session.title,  # Session title (from first user message or custom-title)
        "message_count": session.message_count,  # Number of user/assistant messages
        # Boolean indicating if session metadata is up-to-date
        "compute_version_up_to_date": session.compute_version == settings.CURRENT_COMPUTE_VERSION,
        # Cost and context usage fields
        "context_usage": session.context_usage,  # Current context usage in tokens
        "total_cost": float(session.total_cost) if session.total_cost else None,  # Total cost in USD
    }


def serialize_session_item(item):
    """
    Serialize a SessionItem model to a dictionary with full content.

    Used by:
    - GET /api/.../items/?range=... endpoint
    - WebSocket item_created messages
    """
    return {
        "line_num": item.line_num,
        "content": item.content,
        # Display metadata fields
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
    }


def serialize_session_item_metadata(item):
    """
    Serialize a SessionItem model to a dictionary WITHOUT content.

    Used by:
    - GET /api/.../items/metadata/ endpoint

    This is a lightweight serialization for loading all item metadata
    without the potentially large content field.
    """
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
        # NO content field - that's the whole point
    }
