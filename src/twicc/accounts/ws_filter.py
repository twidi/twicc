"""Per-connection filter of ``/ws/`` frames for member accounts.

Every frame a ``WSConsumer`` sends — the connect-time snapshot as well as the
``updates`` group broadcasts — goes through ``WSConsumer.send_json``, which
hands it to :meth:`MemberFrameFilter.filter` when the connection belongs to a
member. That single choke point is the whole design: no broadcast site has to
know about accounts.

Deny by default, like the HTTP side: a frame type not classified below is
dropped for members, so a new broadcast type stays admin-only until someone
decides how it should be scoped.
"""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async

from twicc.accounts.project_access import ProjectAccess, filter_workspaces_payload, visible_workspace_ids
from twicc.accounts.scoping import strip_member_settings

logger = logging.getLogger(__name__)

# Instance-wide frames that carry nothing project-scoped (or nothing secret).
PASS_THROUGH_TYPES = frozenset({
    "server_version",
    "auth_failure",
    "accounts_changed",
    "agent_settings_presets_updated",
    "message_snippets_updated",
    "tts_config_updated",
    "layouts_updated",
    "provider_state_changed",
    "providers_status_updated",
    "timeline_changed",
    "activity_updated",
    "startup_progress",
    "update_available",
    "tips_manifest_pushed",
    "seen_tips_updated",
    "help_manifest_pushed",
    "seen_help_updated",
    "pong",
    "resync_required",
    # Direct replies to the member's own request (sent only on its socket).
    "error",
    "send_ack",
    "invalid_title",
    "ephemeral_result",
    "ephemeral_admission_failed",
})

# Frames scoped by a ``project_id`` key and/or a ``session_id`` key and/or an
# embedded ``session`` object. Dropped unless the project is viewable.
SCOPED_TYPES = frozenset({
    "session_items_added",
    "session_updated",
    "session_removed",
    "agent_run_state",
    "workflow_changed",
    "workflow_link_created",
    "agent_link_created",
    "process_state",
    "process_label",
    "process_tools",
    "process_background_work",
    "agent_stopped",
    "agent_interaction",
    "async_questions_updated",
    "async_question_dismissed",
    "artifacts_available",
    "artifact_files_changed",
    "title_suggested",
    "user_draft_updated",
    "upload_state",
    "statistics_updated",
    "summary_saved",
    "project_added",
    "project_updated",
    "artifact_bookmark_updated",
    "session_bound",
    "plan_available",
    "plan_gone",
    "plan_changed",
    "manual_compaction_done",
    "goal_command_done",
    "plan_command_done",
    "tool_state",
})

# Frame-type prefixes scoped the same way (streaming deltas, provider events).
SCOPED_PREFIXES = ("stream_block_", "claude_code:", "codex:")


class MemberFrameFilter:
    """Filter bound to one member connection (re-created on ``accounts_changed``)."""

    def __init__(self, principal, access: ProjectAccess):
        self.principal = principal
        self.access = access
        self._session_projects: dict[str, tuple[str | None, ...]] = {}

    # --- session → project resolution (cached per connection) -------------

    async def _session_visible(self, session_id) -> bool:
        if not isinstance(session_id, str) or not session_id:
            return False
        projects = self._session_projects.get(session_id)
        if projects is None:
            projects = await sync_to_async(_lookup_session_projects)(session_id)
            if projects:
                self._session_projects[session_id] = projects
        return any(self.access.can_view(pid) for pid in projects)

    def remember(self, session_id: str, project_id: str | None) -> None:
        """Seed the cache from a frame that carries both ids."""
        if session_id and project_id:
            known = self._session_projects.get(session_id, ())
            if project_id not in known:
                self._session_projects[session_id] = (*known, project_id)

    async def _scoped_visible(self, msg: dict) -> bool:
        session = msg.get("session")
        if isinstance(session, dict):
            self.remember(session.get("id"), session.get("project_id"))
            if self.access.can_view(session.get("project_id")):
                return True
            parent = session.get("parent_session_id")
            return bool(parent) and await self._session_visible(parent)
        project = msg.get("project")
        if isinstance(project, dict):
            return self.access.can_view(project.get("id"))
        project_id = msg.get("project_id")
        session_id = msg.get("session_id")
        if project_id is not None:
            if session_id:
                self.remember(session_id, project_id)
            if self.access.can_view(project_id):
                return True
            # Subagent rows may sit in another project than their visible parent.
            return bool(session_id) and await self._session_visible(session_id)
        if session_id is not None:
            return await self._session_visible(session_id)
        bookmark = msg.get("bookmark")
        if isinstance(bookmark, dict):
            return self.access.can_view(bookmark.get("project_id"))
        return False

    # --- the filter ----------------------------------------------------------

    async def filter(self, msg):
        """The frame to send to this member, possibly rewritten, or None to drop it."""
        if not isinstance(msg, dict):
            return None
        msg_type = msg.get("type", "")
        if msg_type in PASS_THROUGH_TYPES:
            return msg
        if msg_type in SCOPED_TYPES or msg_type.startswith(SCOPED_PREFIXES):
            return msg if await self._scoped_visible(msg) else None
        if msg_type == "synced_settings_updated":
            return {**msg, "settings": strip_member_settings(msg.get("settings"))}
        if msg_type == "workspaces_updated":
            visible = await sync_to_async(visible_workspace_ids)(self.principal, self.access)
            return {**msg, "workspaces": filter_workspaces_payload(msg.get("workspaces") or [], self.access, visible)}
        if msg_type == "active_processes":
            processes = [p for p in msg.get("processes") or [] if await self._process_visible(p)]
            return {**msg, "processes": processes, "ephemeral_starting": []}
        if msg_type in ("hidden_sessions", "sessions_bulk_archived"):
            ids = [sid for sid in msg.get("session_ids") or [] if await self._session_visible(sid)]
            return {**msg, "session_ids": ids}
        if msg_type == "artifact_bookmarks_updated":
            bookmarks = [b for b in msg.get("bookmarks") or [] if self.access.can_view(b.get("project_id"))]
            return {**msg, "bookmarks": bookmarks}
        if msg_type == "artifact_bookmark_removed":
            # Carries only an id: harmless, and the client ignores unknown ids.
            return msg
        logger.debug("Member frame dropped (unclassified type %r)", msg_type)
        return None

    async def _process_visible(self, proc) -> bool:
        if not isinstance(proc, dict):
            return False
        project_id = proc.get("project_id")
        if project_id is not None and self.access.can_view(project_id):
            return True
        return await self._session_visible(proc.get("session_id"))


def _lookup_session_projects(session_id: str) -> tuple[str | None, ...]:
    from twicc.core.models import Session

    row = (
        Session.objects.filter(pk=session_id)
        .values_list("project_id", "parent_session__project_id")
        .first()
    )
    if row is None:
        return ()
    return tuple(pid for pid in row if pid)
