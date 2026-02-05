"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""

from functools import lru_cache

from django.conf import settings

from twicc.core.pricing import extract_model_info


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "directory": project.directory,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "stale": project.stale,
        "name": project.name,
        "color": project.color,
        "total_cost": float(project.total_cost) if project.total_cost else None,
    }


def serialize_session(session):
    """
    Serialize a Session model to a dictionary.

    Includes compute_version_up_to_date boolean to indicate if the session's
    metadata has been computed with the current version of rules.

    Works for both regular sessions and subagents. For subagents,
    parent_session_id will be set; for regular sessions it will be None.

    For the title field, pending titles take priority over the database value.
    This ensures that when a session is created with a custom title, the title
    is immediately visible even before it's written to the JSONL file.
    """
    from twicc.titles import get_pending_title

    # Use pending title if available, otherwise use the stored title
    title = get_pending_title(session.id) or session.title

    return {
        "id": session.id,
        "project_id": session.project_id,
        "parent_session_id": session.parent_session_id,  # None for regular sessions, set for subagents
        "last_line": session.last_line,
        "mtime": session.mtime,
        "stale": session.stale,
        "title": title,  # Session title (from pending, first user message, or custom-title)
        "message_count": session.message_count,  # Number of user/assistant messages
        # Boolean indicating if session metadata is up-to-date
        "compute_version_up_to_date": session.compute_version == settings.CURRENT_COMPUTE_VERSION,
        # Cost and context usage fields
        "context_usage": session.context_usage,  # Current context usage in tokens
        "self_cost": float(session.self_cost) if session.self_cost else None,  # Own items cost in USD
        "subagents_cost": float(session.subagents_cost) if session.subagents_cost else None,  # Sum of subagents cost
        "total_cost": float(session.total_cost) if session.total_cost else None,  # Total cost in USD
        # Runtime environment fields
        "cwd": session.cwd,  # Current working directory
        "git_branch": session.git_branch,  # Git branch name
        "model": _serialize_model(session.model),  # Model info object
        # User-controlled fields
        "archived": session.archived,  # Whether the session is archived
    }


@lru_cache(maxsize=32)
def _serialize_model(model: str | None) -> dict | None:
    """Serialize model info as structured object with raw, family, version."""
    if not model:
        return None

    info = extract_model_info(model)
    if not info:
        return {"raw": model, "family": None, "version": None}

    return {
        "raw": model,
        "family": info.family,  # e.g., "opus"
        "version": info.version,  # e.g., "4.5"
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
