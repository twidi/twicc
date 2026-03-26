"""
ASGI application configuration with WebSocket routing.

Provides HTTP and WebSocket protocol routing, with the UpdatesConsumer
handling real-time updates on the /ws/ endpoint. Also handles agent-related
messages for sending messages to Claude sessions.
"""

import asyncio
import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from blacknoise import BlackNoise
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.sessions import SessionMiddlewareStack
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.urls import path

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny, PermissionRuleValue, PermissionUpdate

from twicc.agent.manager import get_process_manager
from twicc.agent.states import ProcessInfo, ProcessState, serialize_process_info
from twicc.synced_settings import read_synced_settings, write_synced_settings
from twicc.usage_task import get_usage_message_for_connection
from twicc.terminal import terminal_application

logger = logging.getLogger(__name__)

# WebSocket close code for authentication failure.
# 4000-4999 range is reserved for application use by the WebSocket spec.
WS_CLOSE_AUTH_FAILURE = 4001


def _permission_update_from_dict(data: dict) -> PermissionUpdate:
    """Reconstruct a PermissionUpdate from its serialized dict form.

    The SDK's ``PermissionUpdate.to_dict()`` uses camelCase keys (e.g., ``toolName``,
    ``ruleContent``). This function reverses that conversion back to the dataclass
    with snake_case field names.

    Args:
        data: Dictionary as produced by ``PermissionUpdate.to_dict()``

    Returns:
        A ``PermissionUpdate`` instance ready to pass back to the SDK.
    """
    rules = None
    raw_rules = data.get("rules")
    if raw_rules is not None:
        rules = [
            PermissionRuleValue(
                tool_name=r["toolName"],
                # SDK bug workaround: PermissionUpdate.to_dict() serializes None as
                # "ruleContent": null, but Claude Code CLI's Zod schema rejects null
                # (expects string | undefined). Using "" instead of None avoids the error.
                rule_content=r.get("ruleContent") or "",
            )
            for r in raw_rules
        ]

    return PermissionUpdate(
        type=data["type"],
        rules=rules,
        behavior=data.get("behavior"),
        mode=data.get("mode"),
        directories=data.get("directories"),
        destination=data.get("destination"),
    )


@sync_to_async
def get_project_directory(project_id: str) -> str | None:
    """Get the directory for a project from the database.

    Returns None if project not found or has no directory set.
    """
    from twicc.core.models import Project

    try:
        project = Project.objects.get(id=project_id)
        return project.directory
    except Project.DoesNotExist:
        return None


@sync_to_async
def session_exists(session_id: str) -> bool:
    """Check if a session exists in the database.

    Returns True if the session exists, False otherwise.
    """
    from twicc.core.models import Session

    return Session.objects.filter(id=session_id).exists()


async def update_session_permission_mode(session_id: str, permission_mode: str) -> None:
    """Update the permission_mode for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(permission_mode=permission_mode).update
    )(permission_mode=permission_mode)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with permission_mode {permission_mode}")


async def update_session_selected_model(session_id: str, selected_model: str) -> None:
    """Update the selected_model for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(selected_model=selected_model).update
    )(selected_model=selected_model)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with model {selected_model}")


async def update_session_effort(session_id: str, effort: str) -> None:
    """Update the effort level for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(effort=effort).update
    )(effort=effort)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with effort {effort}")


async def update_session_thinking_enabled(session_id: str, thinking_enabled: bool) -> None:
    """Update the thinking_enabled flag for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(thinking_enabled=thinking_enabled).update
    )(thinking_enabled=thinking_enabled)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with thinking_enabled {thinking_enabled}")


async def update_session_claude_in_chrome(session_id: str, claude_in_chrome: bool) -> None:
    """Update the claude_in_chrome flag for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(claude_in_chrome=claude_in_chrome).update
    )(claude_in_chrome=claude_in_chrome)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with claude_in_chrome {claude_in_chrome}")


async def update_session_context_max(session_id: str, context_max: int) -> None:
    """Update the context_max for an existing session and broadcast the change.

    Skips the DB update and broadcast if the value is already the same.
    """
    from twicc.core.models import Session
    from twicc.core.serializers import serialize_session

    rows = await sync_to_async(
        Session.objects.filter(id=session_id).exclude(context_max=context_max).update
    )(context_max=context_max)
    if not rows:
        return

    session = await sync_to_async(Session.objects.filter(id=session_id).first)()
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )
    logger.info(f"Session {session_id} updated with context_max {context_max}")


def _get_project_display_name(project) -> str:
    """Compute a human-readable display name for a project.

    Mirrors the frontend logic in getProjectDisplayName (stores/data.js):
    1. User-defined name (project.name) takes priority
    2. Last component of the directory path
    3. Last component of the project ID (after dashes)
    """
    if project.name:
        return project.name
    if project.directory:
        parts = project.directory.rstrip("/").split("/")
        return parts[-1] if parts[-1] else project.directory
    parts = project.id.split("-")
    return parts[-1] if parts[-1] else project.id


@sync_to_async
def get_session_and_project_display(session_id: str, project_id: str) -> tuple[str | None, str | None]:
    """Get session title and project display name from the database.

    Uses select_related to fetch session + project in a single query.
    Falls back to a separate Project query if the session doesn't exist.

    Returns:
        (session_title, project_display_name) — either may be None if not found.
    """
    from twicc.core.models import Project, Session

    session_title = None
    project_name = None

    try:
        session = Session.objects.select_related("project").get(id=session_id)
        session_title = session.title
        project_name = _get_project_display_name(session.project)
    except Session.DoesNotExist:
        # Session not in DB yet (e.g. just created) — try project alone
        try:
            project = Project.objects.get(id=project_id)
            project_name = _get_project_display_name(project)
        except Project.DoesNotExist:
            pass

    return session_title, project_name


@sync_to_async
def get_bulk_session_and_project_display(
    process_infos: list[dict],
) -> dict[str, tuple[str | None, str | None]]:
    """Batch-fetch session titles and project display names for multiple processes.

    Args:
        process_infos: List of serialized process info dicts (with session_id and project_id).

    Returns:
        Dict mapping session_id → (session_title, project_display_name).
    """
    from twicc.core.models import Project, Session

    session_ids = [p["session_id"] for p in process_infos]
    project_ids = list({p["project_id"] for p in process_infos})

    # Batch fetch sessions with their projects
    sessions_by_id = {
        s.id: s
        for s in Session.objects.select_related("project").filter(id__in=session_ids)
    }

    # Batch fetch projects (for sessions not yet in DB)
    projects_by_id = {
        p.id: p
        for p in Project.objects.filter(id__in=project_ids)
    }

    result = {}
    for p in process_infos:
        sid = p["session_id"]
        pid = p["project_id"]
        session = sessions_by_id.get(sid)
        if session:
            result[sid] = (session.title, _get_project_display_name(session.project))
        else:
            project = projects_by_id.get(pid)
            result[sid] = (None, _get_project_display_name(project) if project else None)

    return result


async def _enrich_with_active_crons(message: dict, session_id: str) -> None:
    """Enrich a serialized process state dict with active crons from the database."""
    from twicc.core.models import SessionCron

    crons = await sync_to_async(
        lambda: [c.serialize() for c in SessionCron.active_for_session(session_id)]
    )()
    if crons:
        message["active_crons"] = crons


async def broadcast_process_state(info: ProcessInfo) -> None:
    """Broadcast a process state change to all connected clients.

    This is the callback registered with ProcessManager to handle
    state change notifications.

    When transitioning out of assistant_turn (e.g. to user_turn or dead),
    we delay the broadcast by 1 second to allow the file watcher to sync
    the final assistant message to the database and broadcast it via WebSocket
    before the frontend learns that the turn ended. This prevents a brief flash
    of an intermediate assistant message in conversation display mode.
    """
    if info.previous_state == ProcessState.ASSISTANT_TURN and info.state != ProcessState.ASSISTANT_TURN:
        await asyncio.sleep(1)

    channel_layer = get_channel_layer()
    message = serialize_process_info(info)
    message["type"] = "process_state"

    # Enrich with active crons from the database
    await _enrich_with_active_crons(message, info.session_id)

    # Enrich with human-readable session title and project name
    # so the frontend can display notifications without needing
    # session data in its local store.
    session_title, project_name = await get_session_and_project_display(
        info.session_id, info.project_id
    )
    if session_title is not None:
        message["session_title"] = session_title
    if project_name is not None:
        message["project_name"] = project_name

    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


class UpdatesConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for broadcasting real-time updates.

    All connected clients join the "updates" group and receive broadcasts
    about project, session, session item changes, and process state changes.

    Handles incoming messages:
    - ping: heartbeat, responds with pong
    - send_message: send a message to a Claude session
    """

    async def connect(self):
        """Accept connection, add to updates group, and send active processes.

        If password protection is enabled, rejects unauthenticated WebSocket
        connections. The session is populated by SessionMiddlewareStack from
        the browser's session cookie (sent during the HTTP upgrade handshake).

        Supports an optional ``subscribe`` query parameter to filter outgoing
        messages by type. Example: ``?subscribe=process_state,active_processes``
        When set, only messages whose ``type`` matches the list are sent.
        When absent, all messages are sent (backward compatible).
        """
        # Check authentication if password protection is enabled
        if settings.TWICC_PASSWORD_HASH:
            session = self.scope.get("session", {})
            # Session.get() triggers a synchronous DB load, so we must
            # wrap it with sync_to_async in this async consumer.
            is_authenticated = await sync_to_async(session.get)("authenticated")
            if not is_authenticated:
                logger.warning("WebSocket connection rejected: not authenticated")
                # Accept first so we can send a message and a close code.
                # Closing before accept causes the close code to be lost
                # (the WebSocket handshake is never completed).
                await self.accept()
                # Send an auth_failure message as a fallback: some proxies
                # (notably Vite's dev proxy via node-http-proxy) may strip
                # the WebSocket close code, delivering 1006 instead of 4001.
                # The client handles both the message and the close code.
                await self.send_json({"type": "auth_failure"})
                await self.close(code=WS_CLOSE_AUTH_FAILURE)
                return

        # Parse optional subscribe filter from query string
        query_string = self.scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        subscribe_values = params.get("subscribe")
        if subscribe_values:
            # parse_qs returns a list of values; split each on comma and flatten
            self._subscribe_filter: set[str] | None = {
                t for value in subscribe_values for t in value.split(",") if t
            }
        else:
            self._subscribe_filter = None

        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()

        # Send server version to the client (used for auto-reload on version change)
        if self._should_send("server_version"):
            await self.send_json({"type": "server_version", "version": settings.APP_VERSION})

        # Set up broadcast callback on ProcessManager (idempotent, safe to call multiple times)
        manager = get_process_manager()
        manager.set_broadcast_callback(broadcast_process_state)

        # Send current active processes to the connecting client,
        # enriched with session titles, project names, and active crons.
        if self._should_send("active_processes"):
            processes = manager.get_active_processes()
            serialized = [serialize_process_info(p) for p in processes]
            if serialized:
                display_info = await get_bulk_session_and_project_display(serialized)
                for proc in serialized:
                    session_title, project_name = display_info.get(proc["session_id"], (None, None))
                    if session_title is not None:
                        proc["session_title"] = session_title
                    if project_name is not None:
                        proc["project_name"] = project_name
                    # Enrich with active crons from DB
                    await _enrich_with_active_crons(proc, proc["session_id"])
            await self.send_json(
                {
                    "type": "active_processes",
                    "processes": serialized,
                }
            )

        # Send latest usage snapshot to the connecting client
        if self._should_send("usage_updated"):
            usage_message = await get_usage_message_for_connection()
            await self.send_json(usage_message)

        # Send synced settings to the connecting client
        if self._should_send("synced_settings_updated"):
            synced_settings = await sync_to_async(read_synced_settings)()
            await self.send_json({"type": "synced_settings_updated", "settings": synced_settings})

        # Send current startup progress (if any phase is still active)
        if self._should_send("startup_progress"):
            from twicc.startup_progress import get_startup_progress
            for progress_msg in get_startup_progress():
                await self.send_json(progress_msg)

        # Send update available notification if a newer version is known
        if self._should_send("update_available"):
            from twicc.version_check_task import get_update_available_message
            update_msg = get_update_available_message()
            if update_msg:
                await self.send_json(update_msg)

        # Send Claude Code status if currently not operational
        if self._should_send("claude_status"):
            from twicc.statuspage_task import get_statuspage_message_for_connection
            status_msg = get_statuspage_message_for_connection()
            if status_msg:
                await self.send_json(status_msg)

    async def disconnect(self, close_code):
        """Remove from the updates group on disconnect."""
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.

        Supported message types:
        - ping: heartbeat, responds with pong
        - send_message: send a message to a Claude session (creates new or resumes existing)
        - kill_process: kill a running Claude process
        - pending_request_response: respond to a pending tool approval or clarifying question
        - suggest_title: request a title suggestion for a session
        - update_synced_settings: update synced settings and broadcast to all clients
        - session_viewed: mark a session as viewed by the user (updates last_viewed_at)
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})

        elif msg_type == "send_message":
            await self._handle_send_message(content)

        elif msg_type == "kill_process":
            await self._handle_kill_process(content)

        elif msg_type == "user_draft_updated":
            await self._handle_user_draft_updated(content)

        elif msg_type == "pending_request_response":
            await self._handle_pending_request_response(content)

        elif msg_type == "suggest_title":
            await self._handle_suggest_title(content)

        elif msg_type == "update_synced_settings":
            await self._handle_update_synced_settings(content)

        elif msg_type == "session_viewed":
            await self._handle_session_viewed(content)

    async def send_json(self, content, close=False):
        try:
            await super().send_json(content, close=close)
        except Exception as exc:
            logger.exception("Error sending JSON message: %s", exc)

    async def _handle_send_message(self, content: dict) -> None:
        """Handle send_message request from client.

        Expected content format:
        {
            "type": "send_message",
            "session_id": "claude-conv-xxx",
            "project_id": "proj-xyz",
            "text": "The message text",       // May be empty for settings-only updates
            "title": "Optional session title",  // Only for new sessions
            "images": [...],  // Optional: array of SDK ImageBlockParam objects
            "documents": [...]  // Optional: array of SDK DocumentBlockParam objects
        }

        This handles both new sessions and existing sessions:
        - If session exists in database: resume the session
        - If session doesn't exist: create a new session with the provided session_id

        The optional title field is only used for new sessions (drafts becoming real).
        If provided, it will be stored as a pending title and written to JSONL
        when the process becomes safe.

        The optional images and documents fields contain attachments in SDK format.

        Text may be empty for settings-only updates (changing model or permission mode
        on a live process). In that case, the SDK methods are called but no query is sent.
        """
        session_id = content.get("session_id")
        project_id = content.get("project_id")
        text = content.get("text", "")  # May be empty for settings-only updates
        title = content.get("title")  # Optional, only for new sessions
        images = content.get("images")  # Optional: SDK ImageBlockParam list
        documents = content.get("documents")  # Optional: SDK DocumentBlockParam list
        permission_mode = content.get("permission_mode", "default")
        selected_model = content.get("selected_model")  # Optional: SDK model shorthand
        effort = content.get("effort")  # Optional: SDK effort level
        thinking_enabled = content.get("thinking_enabled")  # Optional: bool
        claude_in_chrome = content.get("claude_in_chrome", False)  # bool, defaults to False
        context_max = content.get("context_max", 200_000)  # int: 200_000 (default) or 1_000_000

        # Validate required fields (text is allowed to be empty for settings-only updates)
        if not session_id or not project_id:
            logger.warning(
                "send_message missing required fields: session_id=%s, project_id=%s",
                session_id,
                project_id,
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": "send_message requires session_id and project_id",
                }
            )
            return

        # Validate title if provided
        if title is not None:
            from twicc.titles import validate_title

            validated_title, title_error = validate_title(title)
            if title_error:
                logger.warning(
                    "send_message: invalid title for session %s: %s",
                    session_id,
                    title_error,
                )
                await self.send_json(
                    {
                        "type": "invalid_title",
                        "session_id": session_id,
                        "title": title,
                        "error": title_error,
                    }
                )
                return
            title = validated_title

        # Get project directory from database
        cwd = await get_project_directory(project_id)
        if not cwd:
            logger.warning(
                "send_message: project %s not found or has no directory", project_id
            )
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Project {project_id} not found or has no directory configured",
                }
            )
            return

        # Check if session exists to determine whether to create new or resume
        exists = await session_exists(session_id)

        manager = get_process_manager()
        try:
            if exists:
                # Update permission_mode, selected_model, effort, thinking in DB for existing sessions
                await update_session_permission_mode(session_id, permission_mode)
                if selected_model:
                    await update_session_selected_model(session_id, selected_model)
                if effort:
                    await update_session_effort(session_id, effort)
                if thinking_enabled is not None:
                    await update_session_thinking_enabled(session_id, thinking_enabled)
                await update_session_claude_in_chrome(session_id, claude_in_chrome)
                await update_session_context_max(session_id, context_max)
                # Session exists: send message to it
                await manager.send_to_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    selected_model=selected_model,
                    effort=effort, thinking_enabled=thinking_enabled,
                    claude_in_chrome=claude_in_chrome,
                    context_max=context_max,
                    images=images, documents=documents
                )
            else:
                # New session requires text (settings-only update makes no sense here)
                if not text:
                    await self.send_json(
                        {
                            "type": "error",
                            "message": "Text is required to create a new session",
                        }
                    )
                    return

                # Session doesn't exist: create new with client-provided ID
                # Store title as pending if provided (will be written when process is safe)
                if title:
                    from twicc.titles import set_pending_title

                    set_pending_title(session_id, title)

                # Store session settings as pending (will be applied when watcher creates the session row)
                from twicc.pending_settings import set_pending

                pending_kwargs = {"permission_mode": permission_mode}
                if selected_model:
                    pending_kwargs["selected_model"] = selected_model
                if effort:
                    pending_kwargs["effort"] = effort
                if thinking_enabled is not None:
                    pending_kwargs["thinking_enabled"] = thinking_enabled
                pending_kwargs["claude_in_chrome"] = claude_in_chrome
                pending_kwargs["context_max"] = context_max
                set_pending(session_id, **pending_kwargs)

                await manager.create_session(
                    session_id, project_id, cwd, text,
                    permission_mode=permission_mode,
                    selected_model=selected_model,
                    effort=effort, thinking_enabled=thinking_enabled,
                    claude_in_chrome=claude_in_chrome,
                    context_max=context_max,
                    images=images, documents=documents
                )
        except RuntimeError as e:
            # Process busy or other expected errors
            logger.warning("send_message failed: %s", e)
            await self.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception as e:
            # Unexpected errors - log full traceback
            logger.exception("Unexpected error in send_message")
            await self.send_json(
                {
                    "type": "error",
                    "message": f"Failed to send message: {e}",
                }
            )

    async def _handle_kill_process(self, content: dict) -> None:
        """Handle kill_process request from client.

        Expected content format:
        {
            "type": "kill_process",
            "session_id": "claude-conv-xxx"
        }

        Only processes in STARTING or ASSISTANT_TURN state can be killed.
        The state change to DEAD will be broadcast via process_state message.
        """
        session_id = content.get("session_id")

        if not session_id:
            logger.warning("kill_process missing session_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "kill_process requires session_id",
                }
            )
            return

        manager = get_process_manager()
        killed = await manager.kill_process(session_id, reason="manual")

        if not killed:
            # Process not found or not in killable state - not an error, just log
            logger.debug(
                "kill_process: session %s not killed (not found or not active)",
                session_id,
            )

    async def _handle_user_draft_updated(self, content: dict) -> None:
        """Handle user_draft_updated notification from client.

        Expected content format:
        {
            "type": "user_draft_updated",
            "session_id": "claude-conv-xxx"
        }

        This is sent (debounced) when the user is actively preparing a message
        (typing text, adding images, etc.). It updates the process's last_activity
        timestamp to prevent auto-stop due to inactivity timeout.
        """
        session_id = content.get("session_id")

        if not session_id:
            # Silent ignore - this is a fire-and-forget notification
            return

        manager = get_process_manager()
        manager.touch_process_activity(session_id)

    async def _handle_pending_request_response(self, content: dict) -> None:
        """Handle a pending request response from the user.

        Routes the user's decision (tool approval or clarifying question answer)
        to the correct process via the ProcessManager.

        Expected content for tool approval:
        {
            "type": "pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "tool_approval",
            "decision": "allow" | "deny",
            "message": "optional reason for deny",
            "updated_input": { ... }  // optional, for approve with modifications
            "updated_permissions": [ ... ]  // optional, checked permission suggestions
        }

        Expected content for ask_user_question:
        {
            "type": "pending_request_response",
            "session_id": "...",
            "request_id": "...",
            "request_type": "ask_user_question",
            "answers": {
                "question text": "selected label or free text",
                ...
            }
        }
        """
        session_id = content.get("session_id")
        request_type = content.get("request_type")

        if not session_id or not request_type:
            logger.warning(
                "pending_request_response missing required fields: session_id=%s, request_type=%s",
                session_id,
                request_type,
            )
            return

        manager = get_process_manager()

        if request_type == "tool_approval":
            decision = content.get("decision")
            if decision == "allow":
                updated_input = content.get("updated_input")

                # Reconstruct accepted permission suggestions (if any) from the frontend
                updated_permissions = None
                raw_permissions = content.get("updated_permissions")
                if raw_permissions:
                    updated_permissions = [_permission_update_from_dict(p) for p in raw_permissions]

                response = PermissionResultAllow(
                    updated_input=updated_input,
                    updated_permissions=updated_permissions,
                )
                logger.debug("Tool approval allowed for session %s with responses=%s", session_id, response)
            else:
                message = content.get("message", "User denied this action")
                response = PermissionResultDeny(message=message)

        elif request_type == "ask_user_question":
            answers = content.get("answers", {})
            # Retrieve original questions from the process's pending request
            process_info = manager.get_process_info(session_id)
            if process_info is None or process_info.pending_request is None:
                logger.warning(
                    "pending_request_response: no pending request for session %s",
                    session_id,
                )
                return
            original_input = process_info.pending_request.tool_input
            response = PermissionResultAllow(
                updated_input={
                    "questions": original_input.get("questions", []),
                    "answers": answers,
                }
            )

        else:
            logger.warning(
                "pending_request_response: unknown request_type %r",
                request_type,
            )
            return

        # Persist setMode suggestions in DB so future resumes use the correct mode
        if request_type == "tool_approval" and content.get("decision") == "allow":
            raw_permissions = content.get("updated_permissions")
            if raw_permissions:
                for perm in raw_permissions:
                    if perm.get("type") == "setMode" and perm.get("mode"):
                        await update_session_permission_mode(session_id, perm["mode"])
                        logger.info(
                            "Permission mode updated to %r for session %s (from setMode suggestion)",
                            perm["mode"],
                            session_id,
                        )
                        break  # Only one setMode should be applied

        resolved = await manager.resolve_pending_request(session_id, response)
        if not resolved:
            logger.warning(
                "pending_request_response: failed to resolve for session %s "
                "(no pending request or already resolved)",
                session_id,
            )

    async def _handle_suggest_title(self, content: dict) -> None:
        """Handle title suggestion request.

        Expected content format:
        {
            "type": "suggest_title",
            "sessionId": "claude-conv-xxx",
            "systemPrompt": "System prompt with {text} placeholder",
            "prompt": "optional prompt text for draft/new sessions"
        }

        Requires systemPrompt from frontend (no fallback).

        Modes:
        - prompt provided: Use prompt directly (draft/new session or regenerate)
        - sessionId only: Fetch first message from DB (existing session)

        Always returns the prompt used for generation, so frontend can regenerate.
        """
        from twicc.title_suggest import (
            generate_title,
            get_first_user_message,
        )

        session_id = content.get("sessionId")
        system_prompt = content.get("systemPrompt")
        prompt = content.get("prompt")

        # Require both sessionId and systemPrompt
        if not session_id or not system_prompt:
            return

        # Validate systemPrompt contains {text} placeholder
        if "{text}" not in system_prompt:
            return

        # Get prompt: use provided or fetch from DB
        if not prompt:
            prompt = await get_first_user_message(session_id)

        # Generate suggestion if we have a prompt
        suggestion = None
        if prompt:
            suggestion = await generate_title(prompt, system_prompt)

        # Send result back to client (always include prompt for regeneration)
        await self.send_json({
            "type": "title_suggested",
            "sessionId": session_id,
            "suggestion": suggestion,  # Can be None
            "sourcePrompt": prompt,    # Always included for regeneration
        })

    async def _handle_update_synced_settings(self, content: dict) -> None:
        """Handle update_synced_settings request from client.

        Writes the received settings to settings.json and broadcasts
        the updated settings to all connected clients.
        """
        synced_settings = content.get("settings")
        if not isinstance(synced_settings, dict):
            return

        await sync_to_async(write_synced_settings)(synced_settings)

        # Broadcast to all clients (including the sender — harmless, same values)
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {"type": "synced_settings_updated", "settings": synced_settings},
            },
        )

    async def _handle_session_viewed(self, content: dict) -> None:
        """Handle session_viewed notification from client.

        Expected content format:
        {
            "type": "session_viewed",
            "session_id": "claude-conv-xxx"
        }

        Updates the session's last_viewed_at timestamp and broadcasts the change
        to all connected clients (for multi-device sync).
        """
        session_id = content.get("session_id")
        if not session_id:
            return

        from django.utils import timezone

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        rows = await sync_to_async(
            Session.objects.filter(id=session_id).update
        )(last_viewed_at=timezone.now())
        if not rows:
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session:
            await self.channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )

    def _should_send(self, msg_type: str) -> bool:
        """Check if a message type should be sent to this client.

        Returns True if no subscribe filter is set (all messages pass)
        or if the given type is in the filter.
        """
        return not self._subscribe_filter or msg_type in self._subscribe_filter

    async def broadcast(self, event):
        """Handle broadcast events by sending data to the client.

        If the client connected with a ``subscribe`` filter, only messages
        whose type is in the filter are forwarded. Otherwise all messages
        are sent (default behavior for the TwiCC web UI).
        """
        data = event["data"]
        if not self._should_send(data.get("type", "")):
            return
        await self.send_json(data)


websocket_urlpatterns = [
    path("ws/terminal/<str:session_id>/", terminal_application),
    path("ws/", UpdatesConsumer.as_asgi()),
]

# Django ASGI application for HTTP requests
django_asgi_app = get_asgi_application()

# Protocol router for HTTP and WebSocket
# SessionMiddlewareStack reads the session cookie from the WebSocket
# HTTP upgrade request, making session data available in the consumer's scope.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": SessionMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)

# Serve static files via BlackNoise at the ASGI level.
application = BlackNoise(application, immutable_file_test=lambda *_: True)
application.add(settings.FRONTEND_DIST_DIR, "/static")
