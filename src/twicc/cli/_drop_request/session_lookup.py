"""Local DB lookup of an existing session.

Shared by ``twicc send-message`` / ``twicc send-messages`` and
``twicc update-session`` / ``twicc update-sessions``. The CLI side
does this pre-check before dropping the request so the user gets an immediate,
locally diagnosable error for the common cases (session id unknown, session
stale because its JSONL was deleted, project without directory, subagent).
The watcher-side service re-validates the same conditions in case the DB
state changed between this lookup and the actual operation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from twicc.providers.helpers import AgentSettings


class SessionLookupError(Exception):
    """Raised when the local pre-check for an existing session fails.

    ``code`` is the same vocabulary the backend services use
    (``session_not_found``, ``session_stale``, ``project_no_directory``,
    ``unknown_provider``, ``is_subagent``) so the output layer can render a
    uniform message regardless of where the failure was detected.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ResolvedSession(NamedTuple):
    session_id: str
    provider: str  # value (e.g. "claude_code"); validated against the Provider enum
    project_id: str
    directory: str
    hidden: bool
    spawned_by_id: str | None
    """Id of the session that spawned this one (``None`` for a root session).

    Used to compute the spawn-tree relation in the sender header of
    ``send-message`` / ``send-messages``.
    """
    current_settings: AgentSettings
    """The session row's AgentSettings at lookup time (all fields, None = use synced default).

    Populated via :meth:`AgentSettings.from_session`. Callers that need to
    validate what the effective settings *would be after* a partial update
    should overlay their changes on this baseline, then call
    ``resolve_agent_settings`` + ``enforce_agent_settings_consistency`` before
    any constraint check. This avoids an extra DB round-trip.
    """


def lookup_session(session_id: str) -> ResolvedSession:
    """Read the session row and validate it can be the target of a send or update.

    Raises :class:`SessionLookupError` on any failed precondition; otherwise
    returns a :class:`ResolvedSession` with the fields the caller needs to
    keep going (notably ``provider``, and ``current_settings`` to build the
    effective post-update settings for constraint validation).
    """
    from twicc.core.enums import Provider
    from twicc.core.models import Session, SessionType
    from twicc.providers.helpers import AgentSettings

    session = (
        Session.objects.select_related("project").filter(id=session_id).first()
    )
    if session is None:
        raise SessionLookupError(
            "session_not_found",
            f"Session {session_id!r} not found",
        )
    if session.type == SessionType.SUBAGENT:
        raise SessionLookupError(
            "is_subagent",
            f"Session {session_id!r} is a subagent; subagents cannot be "
            "messaged or updated directly. Target the parent session instead.",
        )
    if session.stale:
        raise SessionLookupError(
            "session_stale",
            f"Session {session_id!r} is stale "
            "(its JSONL file no longer exists on disk)",
        )
    project = session.project
    if project is None or not project.directory:
        raise SessionLookupError(
            "project_no_directory",
            f"Session {session_id!r} has no project directory configured",
        )
    try:
        Provider(session.provider)
    except ValueError:
        raise SessionLookupError(
            "unknown_provider",
            f"Session {session_id!r} has unknown provider {session.provider!r}",
        )
    return ResolvedSession(
        session_id=session.id,
        provider=session.provider,
        project_id=session.project_id,
        directory=project.directory,
        hidden=bool(session.hidden),
        spawned_by_id=session.spawned_by_id,
        current_settings=AgentSettings.from_session(session),
    )
