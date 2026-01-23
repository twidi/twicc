"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "archived": project.archived,
    }


def serialize_session(session):
    """Serialize a Session model to a dictionary."""
    return {
        "id": session.id,
        "project_id": session.project_id,
        "last_line": session.last_line,
        "mtime": session.mtime,
        "archived": session.archived,
    }


def serialize_session_item(item):
    """Serialize a SessionItem model to a dictionary with line_num and content."""
    return {
        "line_num": item.line_num,
        "content": item.content,
    }
