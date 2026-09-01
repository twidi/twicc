"""
ASGI application configuration with WebSocket routing.

Provides HTTP and WebSocket protocol routing, with the UpdatesConsumer
handling real-time updates on the /ws/ endpoint. Also handles agent-related
messages for sending messages to agent sessions (any provider).
"""

import asyncio
import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from blacknoise import BlackNoise
from packaging.version import InvalidVersion, Version
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.sessions import SessionMiddlewareStack
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.urls import path

from twicc.agent import AgentInfo, serialize_agent_info
from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    SESSION_FINGERPRINT_KEY,
    is_session_authenticated,
)
from twicc.agent.registry import get_agent_manager_registry
from twicc.agent_settings_presets import (
    RESERVED_PRESET_NAMES,
    read_agent_settings_presets,
    write_agent_settings_presets,
)
from twicc.core.enums import Provider
from twicc.core.services.session_creation import create_session_from_payload
from twicc.share.consumer import ShareConsumer
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler
from twicc.providers.codex.ws import CodexWSHandler
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.state import ProviderDisabledError, ensure_provider_running, is_provider_enabled
from twicc.providers.helpers import (
    AGENT_SETTINGS_HIDDEN_FROM_FRONTEND,
    AgentSettings,
    agent_settings_kwargs_from_frontend_payload,
    get_provider_helpers,
    get_provider_helpers_registry,
)
from twicc.external_notifications import notify_agent_event
from twicc.synced_settings import _settings_lock, prepare_settings_for_client, read_synced_settings, write_synced_settings
from twicc.workspaces import read_workspaces
from twicc.layouts import read_layouts
from twicc.message_snippets import read_message_snippets_config, write_message_snippets_config
from twicc.help_manifest import manifest_to_dict as help_manifest_to_dict
from twicc.seen_help import read_seen_help, write_seen_help
from twicc.seen_tips import read_seen_tips, write_seen_tips
from twicc.providers_status import acknowledge_incident, broadcast_providers_status, build_providers_status_message
from twicc.tips_manifest import manifest_to_dict
from twicc.terminal_config import read_terminal_config, write_terminal_config
from twicc.terminal import terminal_application

logger = logging.getLogger(__name__)

# WebSocket close code for authentication failure.
# 4000-4999 range is reserved for application use by the WebSocket spec.
WS_CLOSE_AUTH_FAILURE = 4001


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
def get_session_provider(session_id: str) -> str | None:
    """Return the provider string of a session if it exists, ``None`` otherwise.

    Used by the send_message handler to resolve which provider owns the
    session: an existing session's provider is authoritative; for a brand
    new session, the caller falls back to the ``provider`` field in the
    incoming WebSocket payload.
    """
    from twicc.core.models import Session

    return Session.objects.filter(id=session_id).values_list("provider", flat=True).first()


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
def get_session_and_project_display(
    session_id: str, project_id: str
) -> tuple[str | None, str | None, str | None]:
    """Get session title and project display names from the database.

    Uses select_related to fetch session + project (and the project's main
    repository, when the project is a git worktree) in a single query. Falls
    back to a separate Project query if the session doesn't exist.

    Returns:
        (session_title, project_display_name, parent_project_display_name) —
        any may be None if not found. The parent name is set only when the
        project is a git worktree (``worktree_of``).
    """
    from twicc.core.models import Project, Session

    session_title = None
    project = None

    try:
        session = Session.objects.select_related("project", "project__worktree_of").get(id=session_id)
        session_title = session.title
        project = session.project
    except Session.DoesNotExist:
        # Session not in DB yet (e.g. just created) — try project alone
        try:
            project = Project.objects.select_related("worktree_of").get(id=project_id)
        except Project.DoesNotExist:
            project = None

    project_name = _get_project_display_name(project) if project else None
    parent_name = (
        _get_project_display_name(project.worktree_of)
        if project and project.worktree_of_id
        else None
    )

    return session_title, project_name, parent_name


@sync_to_async
def get_bulk_session_and_project_display(
    process_infos: list[dict],
) -> dict[str, tuple[str | None, str | None, str | None]]:
    """Batch-fetch session titles and project display names for multiple processes.

    Args:
        process_infos: List of serialized process info dicts (with session_id and project_id).

    Returns:
        Dict mapping session_id → (session_title, project_display_name,
        parent_project_display_name). The parent name is set only when the
        project is a git worktree (``worktree_of``).
    """
    from twicc.core.models import Project, Session

    session_ids = [p["session_id"] for p in process_infos]
    project_ids = list({p["project_id"] for p in process_infos})

    # Batch fetch sessions with their projects (and each project's main repo,
    # when it is a worktree)
    sessions_by_id = {
        s.id: s
        for s in Session.objects.select_related("project", "project__worktree_of").filter(id__in=session_ids)
    }

    # Batch fetch projects (for sessions not yet in DB)
    projects_by_id = {
        p.id: p
        for p in Project.objects.select_related("worktree_of").filter(id__in=project_ids)
    }

    def _project_names(project) -> tuple[str | None, str | None]:
        """(display_name, parent_display_name) for a project, or (None, None)."""
        if not project:
            return None, None
        parent = _get_project_display_name(project.worktree_of) if project.worktree_of_id else None
        return _get_project_display_name(project), parent

    result = {}
    for p in process_infos:
        sid = p["session_id"]
        pid = p["project_id"]
        session = sessions_by_id.get(sid)
        if session:
            name, parent = _project_names(session.project)
            result[sid] = (session.title, name, parent)
        else:
            name, parent = _project_names(projects_by_id.get(pid))
            result[sid] = (None, name, parent)

    return result


async def broadcast_process_state(info: AgentInfo) -> None:
    """Broadcast a process state change to all connected clients.

    This is the callback registered with the agent manager to handle
    state change notifications.

    When transitioning out of assistant_turn (e.g. to user_turn or dead),
    we delay the broadcast by 1 second to allow the file watcher to sync
    the final assistant message to the database and broadcast it via WebSocket
    before the frontend learns that the turn ended. This prevents a brief flash
    of an intermediate assistant message in conversation display mode.
    """
    # if info.previous_state == AgentState.ASSISTANT_TURN and info.state != AgentState.ASSISTANT_TURN:
    #     await asyncio.sleep(1)

    # Hidden sessions never produce a process_state event. This is the
    # broadcast that drives toast / sound / browser notifications on the
    # frontend (via notifyProcessStateChange) and hydrates the
    # "active processes" cross-filter; a hidden session must produce
    # none of those even mid-flight.
    from twicc.core.models import Session
    session_flags = await sync_to_async(
        lambda: Session.objects.filter(pk=info.session_id)
        .values_list("hidden", "mute_on_user_turn")
        .first()
    )()
    is_hidden, mute_on_user_turn = session_flags or (False, False)
    if is_hidden:
        return

    channel_layer = get_channel_layer()
    message = serialize_agent_info(info)
    message["type"] = "process_state"
    message["mute_on_user_turn"] = mute_on_user_turn is True

    # Let the provider attach any state it owns (e.g. Claude Code's
    # ``active_crons``). Each provider routes its own keys via its helper.
    await get_provider_helpers(info.provider).enrich_agent_state(message, info.session_id)

    # Enrich with human-readable session title and project name
    # so the frontend can display notifications without needing
    # session data in its local store.
    session_title, project_name, project_parent_name = await get_session_and_project_display(
        info.session_id, info.project_id
    )
    if session_title is not None:
        message["session_title"] = session_title
    if project_name is not None:
        message["project_name"] = project_name
    if project_parent_name is not None:
        message["project_parent_name"] = project_parent_name

    # External notifications (Apprise) mirror the browser notifications this
    # broadcast drives in the frontend; the hook sits after the hidden-session
    # early-return so hidden sessions never notify. Fire-and-forget inside.
    notify_agent_event(
        info,
        session_title,
        project_name,
        project_parent_name,
        mute_on_user_turn is True,
    )

    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


# Version constants for changelog migration.
# These represent the versions before we introduced each tracking field,
# used to bootstrap the values for users upgrading from older releases.
VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN = "1.2.1"
VERSION_BEFORE_PREVIOUS_LAST_CHANGELOG_VERSION_SEEN = "1.3.0"


def _resolve_changelog_versions() -> tuple[str, str, bool]:
    """Resolve changelog tracking versions and determine if forced changelog should be shown.

    Normalizes ``lastChangelogVersionSeen`` and ``previousLastChangelogVersionSeen`` in
    settings.json, handles migration from older installs, and detects upgrades.

    Returns:
        A tuple of (previous_last_changelog_version_seen, last_changelog_version_seen, show_forced).
        ``show_forced`` is True when the user should be presented with the changelog dialog.
    """
    with _settings_lock:
        all_settings = read_synced_settings()
        last = all_settings.get("lastChangelogVersionSeen")
        previous = all_settings.get("previousLastChangelogVersionSeen")

        # --- Step 1: Normalize / initialize the two variables ---

        if not all_settings:
            # No settings or empty → first install
            all_settings["lastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["previousLastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["_version"] = all_settings.get("_version", 0) + 1
            write_synced_settings(all_settings)
            return settings.APP_VERSION, settings.APP_VERSION, False

        if last is None and previous is None:
            # Settings exist but no changelog tracking → user was on ≤ 1.2.1
            last = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
            previous = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
        elif last is not None and previous is None:
            # last exists but no previous → user was on 1.3.0 (first version with lastChangelogVersionSeen)
            previous = VERSION_BEFORE_PREVIOUS_LAST_CHANGELOG_VERSION_SEEN
        elif previous is not None and last is None:
            # Bad manual edit → force last = previous
            last = previous

        # --- Step 2: Update previous based on upgrade detection ---

        if last == previous:
            # Historical / fresh-install case → no change to previous
            pass
        elif last == settings.APP_VERSION:
            # No upgrade → no change to previous
            pass
        else:
            # New upgrade: previous != last AND last != currentVersion
            previous = last

        # --- Persist (only if values actually changed) ---

        if last != all_settings.get("lastChangelogVersionSeen") or previous != all_settings.get("previousLastChangelogVersionSeen"):
            all_settings["lastChangelogVersionSeen"] = last
            all_settings["previousLastChangelogVersionSeen"] = previous
            all_settings["_version"] = all_settings.get("_version", 0) + 1
            write_synced_settings(all_settings)

    # --- Determine if forced show is needed ---

    show_forced = False
    try:
        if Version(settings.APP_VERSION) > Version(last):
            show_forced = True
    except InvalidVersion:
        pass

    return previous, last, show_forced


# Detached background tasks spawned from the WS consumer (e.g. agent stops, which
# hold the manager grace window for up to ~30s in ``interrupt_or_kill``). Awaiting
# such an operation inline in ``receive_json`` would freeze the consumer: Channels
# dispatches a connection's events serially (``await_many_dispatch``), so a blocked
# handler stops the consumer answering the heartbeat ``ping`` — the client then
# drops and reconnects the WS — and stops it flushing queued broadcasts until the
# operation returns. Running detached keeps the receive loop free.
#
# The set lives at module scope, not on the consumer, on purpose: a stop must run
# to completion (actually kill the agent) even if the spawning connection goes
# away first. asyncio keeps only a weak reference to a running task, so without a
# strong ref here the GC could destroy one mid-flight.
_DETACHED_TASKS: set[asyncio.Task] = set()


def _spawn_detached(coro, *, label: str) -> None:
    """Run ``coro`` detached from the consumer's serial receive loop.

    Keeps a strong reference until completion and logs any exception (a bare
    ``create_task`` would let both the reference and the error escape silently).
    """
    task = asyncio.create_task(coro)
    _DETACHED_TASKS.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _DETACHED_TASKS.discard(t)
        if not t.cancelled() and (exc := t.exception()) is not None:
            logger.error("Detached WS task %s failed", label, exc_info=exc)

    task.add_done_callback(_on_done)


class WSConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for broadcasting real-time updates.

    All connected clients join the "updates" group and receive broadcasts
    about project, session, session item changes, and process state changes.

    Handles incoming messages:
    - ping: heartbeat, responds with pong
    - send_message: send a message to an agent session

    Provider-specific inbound messages use a ``"<provider_key>:<action>"``
    type prefix (e.g. ``claude_code:pending_request_response``) and are
    routed to the matching handler in ``_provider_handlers``. Each
    connection instantiates its own handler instances, with this consumer
    passed in so handlers can call ``send_json``, access the channel
    layer, etc.
    """

    PROVIDER_HANDLERS: dict[Provider, type] = {
        Provider.CLAUDE_CODE: ClaudeCodeWSHandler,
        Provider.CODEX: CodexWSHandler,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._provider_handlers: dict[Provider, object] = {
            key: cls(self) for key, cls in self.PROVIDER_HANDLERS.items()
        }

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
        # Unprotected instance (no password): refuse non-local connections —
        # there's nothing to authenticate against. No-op when a password is
        # configured or the operator opted out (see twicc.auth.local_access).
        if scope_remote_access_blocked(self.scope):
            logger.warning("WebSocket connection rejected: remote access requires a password")
            await self.accept()
            await self.send_json({"type": "auth_failure"})
            await self.close(code=WS_CLOSE_AUTH_FAILURE)
            return

        # Check authentication if password protection is enabled.
        # A session bound to an older password hash (rotated since login)
        # is rejected the same way as an unauthenticated one.
        if settings.TWICC_PASSWORD_HASH:
            session = self.scope.get("session", {})
            # Session.get() triggers a synchronous DB load, so we must
            # wrap it with sync_to_async in this async consumer.
            session_auth, session_fp = await sync_to_async(
                lambda: (session.get(SESSION_AUTH_KEY), session.get(SESSION_FINGERPRINT_KEY))
            )()
            if not is_session_authenticated(session_auth, session_fp, settings.TWICC_PASSWORD_HASH):
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
            msg = {"type": "server_version", "version": settings.APP_VERSION}
            previous, last, show_forced = await sync_to_async(_resolve_changelog_versions)()
            msg["previous_last_changelog_version_seen"] = previous
            msg["last_changelog_version_seen"] = last
            if show_forced:
                msg["show_changelog_for_version"] = settings.APP_VERSION
            await self.send_json(msg)

        # Set up broadcast callback on every provider's agent manager
        # (idempotent, safe to call multiple times)
        registry = get_agent_manager_registry()
        registry.set_broadcast_callback(broadcast_process_state)

        # Hidden sessions. Read once for the two consumers below: filtering the
        # active-process list, and telling the client which rows to drop.
        hidden_session_ids: set[str] = set()
        if self._should_send("active_processes") or self._should_send("hidden_sessions"):
            from twicc.core.models import Session
            hidden_session_ids = await sync_to_async(
                lambda: set(Session.objects.filter(hidden=True).values_list("id", flat=True))
            )()

        # Send current active processes to the connecting client,
        # enriched with session titles, project names, and active crons.
        if self._should_send("active_processes"):
            processes = registry.get_active_agents()
            serialized = [serialize_agent_info(p) for p in processes if p.session_id not in hidden_session_ids]
            if serialized:
                display_info = await get_bulk_session_and_project_display(serialized)
                for proc in serialized:
                    session_title, project_name, project_parent_name = display_info.get(
                        proc["session_id"], (None, None, None)
                    )
                    if session_title is not None:
                        proc["session_title"] = session_title
                    if project_name is not None:
                        proc["project_name"] = project_name
                    if project_parent_name is not None:
                        proc["project_parent_name"] = project_parent_name
                    # Let the provider attach any state it owns
                    await get_provider_helpers(
                        Provider(proc["provider"])
                    ).enrich_agent_state(proc, proc["session_id"])
            await self.send_json(
                {
                    "type": "active_processes",
                    "processes": serialized,
                }
            )

        # Tell the client which sessions it must drop.
        #
        # Hiding a session emits one ``session_removed`` frame and nothing else
        # ever again: the archive broadcast, the process states and the watcher
        # all fall silent for a hidden session, and no REST listing returns one.
        # So a client that missed that single frame — a full WebSocket queue, a
        # dropped connection — keeps the row forever. Reconciliation cannot help
        # either: it merges what the API returns and never removes what it
        # omits, and "absent from a listing" means a dozen things besides
        # hidden (a subagent, a draft, a session with no user message yet, the
        # next page…).
        #
        # Stating the fact positively removes all of that reasoning: these are
        # hidden, drop them. Sent on every connect, so a reload or any
        # reconnection heals the row.
        if self._should_send("hidden_sessions"):
            await self.send_json(
                {
                    "type": "hidden_sessions",
                    "session_ids": sorted(hidden_session_ids),
                }
            )

        # Send synced settings to the connecting client
        if self._should_send("synced_settings_updated"):
            raw_settings = await sync_to_async(read_synced_settings)()
            clean_settings, version = prepare_settings_for_client(raw_settings)
            await self.send_json({"type": "synced_settings_updated", "settings": clean_settings, "version": version})

        # Send agent settings presets for every registered provider (cross-provider:
        # the on-disk format is identical, only the file path differs per provider).
        # Necessary on every WS connect — including reconnects after the initial
        # /api/bootstrap/ — so the client store stays in sync without re-fetching
        # the bootstrap payload.
        if self._should_send("agent_settings_presets_updated"):
            # The registry is not a dict — it exposes get/items/values, no keys().
            for provider, _ in get_provider_helpers_registry().items():  # noqa: PERF102
                presets = await sync_to_async(read_agent_settings_presets)(provider)
                await self.send_json({
                    "type": "agent_settings_presets_updated",
                    "provider": provider.value,
                    "config": presets,
                })

        if self._should_send("terminal_config_updated"):
            terminal_config = await sync_to_async(read_terminal_config)()
            await self.send_json({"type": "terminal_config_updated", "config": terminal_config})

        if self._should_send("message_snippets_updated"):
            message_snippets = await sync_to_async(read_message_snippets_config)()
            await self.send_json({"type": "message_snippets_updated", "config": message_snippets})

        if self._should_send("workspaces_updated"):
            workspaces = await sync_to_async(read_workspaces)()
            await self.send_json({"type": "workspaces_updated", "workspaces": workspaces.get("workspaces", [])})

        if self._should_send("layouts_updated"):
            layouts = await sync_to_async(read_layouts)()
            await self.send_json({"type": "layouts_updated", "layouts": layouts.get("layouts", [])})

        # Re-push the full artifact-bookmark set on every WS connect (including
        # reconnects) so the client store stays in sync without re-fetching, the
        # same way workspaces/settings/presets do above. Bookmarks are a small
        # global set, so a full snapshot is cheap and the client replaces its dict
        # wholesale — catching creations/edits/removals missed while disconnected
        # (the incremental artifact_bookmark_updated/_removed events cover the
        # connected case). Larger, per-project data (sessions/items) is handled by
        # client-side reconciliation instead.
        if self._should_send("artifact_bookmarks_updated"):
            from twicc.core.models import ArtifactBookmark
            from twicc.core.serializers import serialize_artifact_bookmark
            artifact_bookmarks = await sync_to_async(list)(ArtifactBookmark.objects.all())
            await self.send_json({
                "type": "artifact_bookmarks_updated",
                "bookmarks": [serialize_artifact_bookmark(b) for b in artifact_bookmarks],
            })

        if self._should_send("shares_updated"):
            from twicc.core.models import Share
            from twicc.core.serializers import serialize_share
            shares = await sync_to_async(list)(
                Share.objects.select_related(
                    "session", "artifact_bookmark", "created_by_session",
                ).all()
            )
            await self.send_json({
                "type": "shares_updated",
                "shares": [serialize_share(s) for s in shares],
            })

        if self._should_send("peers_updated"):
            from twicc.core.models import Peer
            from twicc.core.serializers import serialize_peer
            peers = await sync_to_async(list)(Peer.objects.all())
            await self.send_json({
                "type": "peers_updated",
                "peers": [serialize_peer(p) for p in peers],
            })

        if self._should_send("peer_messages_updated"):
            from twicc.core.models import PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
            from twicc.core.serializers import serialize_peer_message

            def _peer_messages_snapshot():
                # The serializer reads each message's local session titles: one
                # JOIN, not one query per row.
                rows = PeerMessage.objects.select_related(
                    "origin_session", "delivered_to_session", "reply_to_message",
                ).exclude(peer__state=PeerState.REVOKED)
                pending = list(rows.filter(
                    direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
                ))
                recent = list(rows.exclude(
                    direction=PeerMessageDirection.IN, status=PeerMessageStatus.PENDING,
                )[:50])
                return pending + recent

            peer_messages = await sync_to_async(_peer_messages_snapshot)()
            await self.send_json({
                "type": "peer_messages_updated",
                "messages": [serialize_peer_message(m) for m in peer_messages],
            })

        if self._should_send("tips_manifest_pushed"):
            await self.send_json({
                "type": "tips_manifest_pushed",
                "manifest": manifest_to_dict(),
            })

        if self._should_send("seen_tips_updated"):
            seen_tips = await sync_to_async(read_seen_tips)()
            await self.send_json({"type": "seen_tips_updated", "seen_tips": seen_tips})

        # Upstream status of every provider (current status, incident,
        # acknowledgment) — the whole file, every connect and reconnect. The
        # client derives the status toasts from it alone; disabled providers
        # are filtered on the client side, which knows the enabled set.
        if self._should_send("providers_status_updated"):
            await self.send_json(await sync_to_async(build_providers_status_message)())

        if self._should_send("help_manifest_pushed"):
            await self.send_json({
                "type": "help_manifest_pushed",
                "manifest": help_manifest_to_dict(),
            })

        if self._should_send("seen_help_updated"):
            seen_help = await sync_to_async(read_seen_help)()
            await self.send_json({"type": "seen_help_updated", "seen_help": seen_help})

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

        # Provider-specific on-connect messages (e.g. claude_code:auth_updated,
        # claude_code:settings_presets_updated).
        # Each handler yields fully-formed messages with their type already prefixed.
        # Disabled providers are skipped: their orchestrator isn't running, so any
        # state we'd push (auth, usage, statuspage) is either stale or about to
        # be computed inline (e.g. ``codex login status``) for nothing.
        for provider, handler in self._provider_handlers.items():
            if not is_provider_enabled(provider):
                continue
            async for msg in handler.get_connect_messages():
                if self._should_send(msg.get("type", "")):
                    await self.send_json(msg)

    async def disconnect(self, close_code):
        """Remove from the updates group on disconnect."""
        await self.channel_layer.group_discard("updates", self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Handle incoming messages from clients.

        Provider-specific message types use a ``"<provider_key>:<action>"``
        prefix (e.g. ``claude_code:pending_request_response``) and are
        routed to the matching provider handler.

        Supported message types:
        - ping: heartbeat, responds with pong
        - presence: human-presence heartbeat (gates away-only external notifications)
        - send_message: send a message to an agent session (creates new or resumes existing)
        - kill_process: kill a running agent process
        - stop_subagent: gracefully stop a running subagent (Task)
        - interrupt_session: interrupt the current turn, keeping the session alive
        - suggest_title: request a title suggestion for a session
        - update_synced_settings: update synced settings and broadcast to all clients
        - session_viewed: mark a session as viewed by the user (updates last_viewed_at)
        - mark_session_read_state: explicitly mark a session as read or unread
        - list_terminals: list active tmux terminal indices for a terminal context
        - kill_terminal: kill a secondary terminal's tmux session and broadcast
        - validate_usage_dump_path: validate a usage dump file path (write mode) and return result
        - validate_tmux_config_path: validate a tmux config file path and return result
        - get_telemetry_payload: return the last-sent telemetry payload, or a live preview
        - reset_telemetry_instance_id: rotate the anonymous telemetry instance id
        - changelog_seen: acknowledge that the user has seen the changelog for a version
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})

        elif msg_type == "presence":
            # Lightweight human-presence heartbeat (no reply). Gates "away-only"
            # external notifications so they aren't pushed while the user is at a
            # TwiCC client (see twicc.presence), and wakes the paused usage sync
            # loops so quota data refreshes promptly when the user comes back.
            from twicc.presence import touch
            from twicc.usage_task import note_activity
            touch(content.get("device_class"))
            note_activity()

        elif msg_type == "send_message":
            await self._handle_send_message(content)

        elif msg_type == "kill_process":
            await self._handle_kill_process(content)

        elif msg_type == "set_session_hybrid":
            await self._handle_set_session_hybrid(content)

        elif msg_type == "stop_subagent":
            await self._handle_stop_subagent(content)

        elif msg_type == "interrupt_session":
            await self._handle_interrupt_session(content)

        elif msg_type == "user_draft_updated":
            await self._handle_user_draft_updated(content)

        elif msg_type == "suggest_title":
            # Fire-and-forget: title generation involves an SDK call (Haiku) with
            # retries that can take many seconds. Running it as a background task
            # avoids blocking the consumer — so send_message (which typically
            # follows immediately) is processed without delay.
            asyncio.create_task(self._handle_suggest_title(content))

        elif msg_type == "update_synced_settings":
            await self._handle_update_synced_settings(content)

        elif msg_type == "update_workspaces":
            await self._handle_update_workspaces(content)

        elif msg_type == "update_layouts":
            await self._handle_update_layouts(content)

        elif msg_type == "update_terminal_config":
            await self._handle_update_terminal_config(content)

        elif msg_type == "update_message_snippets":
            await self._handle_update_message_snippets(content)

        elif msg_type == "update_agent_settings_presets":
            await self._handle_update_agent_settings_presets(content)

        elif msg_type == "update_seen_tips":
            await self._handle_update_seen_tips(content)

        elif msg_type == "acknowledge_provider_status":
            await self._handle_acknowledge_provider_status(content)

        elif msg_type == "update_seen_help":
            await self._handle_update_seen_help(content)

        elif msg_type == "session_viewed":
            await self._handle_session_viewed(content)

        elif msg_type == "mark_session_read_state":
            await self._handle_mark_session_read_state(content)

        elif msg_type == "list_terminals":
            await self._handle_list_terminals(content)

        elif msg_type == "kill_terminal":
            await self._handle_kill_terminal(content)

        elif msg_type == "rename_terminal":
            await self._handle_rename_terminal(content)

        elif msg_type == "set_terminal_autoattach":
            await self._handle_set_terminal_autoattach(content)

        elif msg_type == "validate_usage_dump_path":
            await self._handle_validate_usage_dump_path(content)

        elif msg_type == "validate_usage_file":
            await self._handle_validate_usage_file(content)

        elif msg_type == "validate_tmux_config_path":
            await self._handle_validate_tmux_config_path(content)

        elif msg_type == "get_telemetry_payload":
            await self._handle_get_telemetry_payload(content)

        elif msg_type == "reset_telemetry_instance_id":
            await self._handle_reset_telemetry_instance_id(content)

        elif msg_type == "changelog_seen":
            await self._handle_changelog_seen(content)

        elif isinstance(msg_type, str) and ":" in msg_type:
            await self._dispatch_provider_message(msg_type, content)

        else:
            logger.warning("Unknown WebSocket message type: %r", msg_type)

    async def _dispatch_provider_message(self, msg_type: str, content: dict) -> None:
        """Route a prefixed message ``"<provider_key>:<action>"`` to the matching handler."""
        provider_key, _, action = msg_type.partition(":")
        try:
            provider = Provider(provider_key)
        except ValueError:
            logger.warning("Unknown provider key %r in message type %r", provider_key, msg_type)
            return

        handler = self._provider_handlers.get(provider)
        if handler is None:
            logger.warning("No provider handler registered for %r", provider)
            return

        handled = await handler.dispatch(action, content)
        if not handled:
            logger.warning(
                "Provider %r did not handle action %r",
                provider, action,
            )

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
            "session_id": "sess-xxx",
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
        # Client-generated correlation id, echoed in every reply frame so the
        # frontend can match an error to the exact send it made (and run its
        # recovery flow: restore the draft, drop the optimistic message).
        request_id = content.get("request_id")

        async def send_error(message: str, *, code: str, **extra) -> None:
            frame: dict = {"type": "error", "code": code, "message": message, **extra}
            if request_id:
                frame["request_id"] = request_id
            if session_id:
                frame["session_id"] = session_id
            await self.send_json(frame)

        # Validate required fields (text is allowed to be empty for settings-only updates)
        if not session_id or not project_id:
            logger.warning(
                "send_message missing required fields: session_id=%s, project_id=%s",
                session_id,
                project_id,
            )
            await send_error(
                "send_message requires session_id and project_id",
                code="invalid_request",
            )
            return

        # Resolve the provider that owns this session: an existing session's
        # provider is authoritative; for a brand new session, the front must
        # tell us via the ``provider`` field in the payload.
        existing_provider = await get_session_provider(session_id)
        exists = existing_provider is not None
        if exists:
            try:
                provider = Provider(existing_provider)
            except ValueError:
                logger.error(
                    "send_message: session %s has unknown provider %r",
                    session_id, existing_provider,
                )
                await send_error(
                    f"Session {session_id} has an unknown provider",
                    code="unknown_provider",
                )
                return
        else:
            provider_value = content.get("provider")
            if not provider_value:
                logger.warning(
                    "send_message: missing 'provider' for new session %s",
                    session_id,
                )
                await send_error(
                    "send_message for a new session requires a 'provider' field",
                    code="invalid_request",
                )
                return
            try:
                provider = Provider(provider_value)
            except ValueError:
                logger.warning(
                    "send_message: unknown provider %r for new session %s",
                    provider_value, session_id,
                )
                await send_error(
                    f"Unknown provider: {provider_value!r}",
                    code="unknown_provider",
                )
                return

        try:
            ensure_provider_running(provider)
        except ProviderDisabledError as e:
            await send_error(str(e), code="provider_disabled", provider=e.provider.value)
            return

        helpers = get_provider_helpers(provider)

        # Validate title if provided
        if title is not None:
            title_result = helpers.validate_title(title)
            if title_result.error:
                logger.warning(
                    "send_message: invalid title for session %s: %s",
                    session_id,
                    title_result.error,
                )
                frame = {
                    "type": "invalid_title",
                    "session_id": session_id,
                    "title": title,
                    "error": title_result.error,
                }
                if request_id:
                    frame["request_id"] = request_id
                await self.send_json(frame)
                return
            title = title_result.title

        # Get project directory from database
        cwd = await get_project_directory(project_id)
        if not cwd:
            logger.warning(
                "send_message: project %s not found or has no directory", project_id
            )
            await send_error(
                f"Project {project_id} not found or has no directory configured",
                code="project_not_found",
            )
            return

        manager = get_agent_manager_registry().get(provider)
        # Per-session settings: ``None`` means "use global default", any
        # explicit value means "forced for this session". The bundle of
        # field names is the cross-provider :class:`AgentSettings`.
        # Fields listed in ``AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`` are dropped
        # here (defense in depth — the frontend does not know they exist).
        agent_settings = AgentSettings(**agent_settings_kwargs_from_frontend_payload(content))
        # Whether the backend accepted the message for delivery to the agent.
        # A positive ack is emitted after the try/except when True; failures
        # leave it False (and have already surfaced via an error frame).
        delivered = False
        try:
            if exists:
                # Save all session settings to DB in one query.
                # Values are null (use global default) or explicit (forced).
                from twicc.core.models import Session
                from twicc.core.serializers import serialize_session
                await run_under_db_write_lock(
                    lambda: Session.objects.filter(id=session_id).aupdate(
                        **agent_settings._asdict()
                    )
                )
                # Broadcast session update so all clients see the new settings
                session_obj = await sync_to_async(Session.objects.filter(id=session_id).first)()
                if session_obj and not session_obj.hidden:
                    await self.channel_layer.group_send(
                        "updates",
                        {
                            "type": "broadcast",
                            "data": {
                                "type": "session_updated",
                                "session": serialize_session(session_obj),
                            },
                        },
                    )

                # If no text/attachments and no process is running, we're done:
                # settings are saved to DB and broadcast, nothing to send.
                has_content = bool(text) or bool(images) or bool(documents)
                has_process = manager.get_agent_info(session_id) is not None
                if not has_content and not has_process:
                    return

                # Resolve effective values for the process manager (null →
                # global synced default, so the process gets concrete values)
                effective_agent_settings = helpers.resolve_agent_settings(agent_settings)

                # Auto-upgrade retired models + enforce provider capability rules
                # (single safety net — front should have corrected, but just in case)
                effective_agent_settings = helpers.enforce_agent_settings_consistency(effective_agent_settings)

                # Session exists: send message to it
                delivered = await manager.send_to_session(
                    session_id, project_id, cwd, text,
                    settings=effective_agent_settings,
                    images=images, documents=documents,
                )
            else:
                # New session: delegate to the shared service so the WS path
                # and the CLI watcher path stay byte-for-byte aligned.
                # Hidden agent-settings fields are stripped here so they
                # cannot enter the create-session payload from a frontend
                # source. The CLI path bypasses this filter on purpose.
                payload = {
                    "session_id": session_id,
                    "project_id": project_id,
                    "provider": content.get("provider"),
                    "text": content.get("text") or "",
                    "title": content.get("title"),
                    "images": content.get("images") or [],
                    "documents": content.get("documents") or [],
                    # Dockable-layout intention the web UI draft carries, frozen onto the new
                    # ``Session.layout`` (the user's customized draft layout — not the project/global
                    # default the service would otherwise resolve when this is absent).
                    "layout": content.get("layout"),
                    # Hybrid CLI mode for drafts. Honored ONLY because this is
                    # the trusted human path (web UI): allow_hybrid below is
                    # what lets the service read it — every other caller
                    # (drop-request files, CLI) keeps the default False.
                    "hybrid": bool(content.get("hybrid")),
                    **agent_settings_kwargs_from_frontend_payload(content),
                }

                result = await create_session_from_payload(payload, allow_hybrid=True)
                if not result.success:
                    # Translate the first error to the WS-specific error frame shape.
                    # The frontend already understands the error codes the service emits
                    # (provider_disabled, project_not_found, etc.).
                    first = result.errors[0]
                    await send_error(first.message, code=first.code)
                    return
                # Success: the new agent started with the first message as its
                # opening prompt. Mark it delivered so the ack below confirms
                # it; the usual broadcasts from the agent manager + watcher
                # still flow.
                delivered = True
        except RuntimeError as e:
            # Process busy or other expected delivery errors. SendDeliveryError
            # carries a specific code (agent_starting, hybrid_composer_busy, …);
            # plain RuntimeErrors fall back to the generic send_failed.
            logger.warning("send_message failed: %s", e)
            await send_error(str(e), code=getattr(e, "code", None) or "send_failed")
        except Exception as e:
            # Unexpected errors - log full traceback
            logger.exception("Unexpected error in send_message")
            await send_error(
                f"Failed to send message: {e}",
                code="send_failed",
            )

        # Positive delivery acknowledgement: the message reached the agent, so
        # the frontend can drop its in-flight send snapshot. This is the only
        # delivery confirmation for messages Claude Code accepts mid-turn —
        # those are folded into the running turn and never get their own
        # user_message line in the JSONL.
        if delivered and request_id:
            await self.send_json({
                "type": "send_ack",
                "request_id": request_id,
                "session_id": session_id,
            })

    async def _handle_set_session_hybrid(self, content: dict) -> None:
        """Switch an existing session to hybrid CLI mode (one-way).

        Human-only and UI-only: this WS handler is the single place the flag
        can be flipped on an existing session (drafts go through the
        ``send_message`` payload + ``allow_hybrid``). There is deliberately
        no payload field to set it back to False — a session resumed by the
        CLI can never go back to the SDK.
        """
        from twicc.core.models import Session, SessionType

        if not settings.CLAUDE_HYBRID_ENABLED:
            await self.send_json({
                "type": "error",
                "message": "Hybrid mode is disabled on this server",
            })
            return

        session_id = content.get("session_id")
        if not session_id:
            await self.send_json({
                "type": "error",
                "message": "set_session_hybrid requires session_id",
            })
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session is None:
            await self.send_json({
                "type": "error",
                "message": f"Session {session_id} not found",
            })
            return
        if session.hybrid:
            return  # Already hybrid — idempotent no-op.
        if (
            session.provider != Provider.CLAUDE_CODE.value
            or session.type != SessionType.SESSION
            or session.hidden
        ):
            await self.send_json({
                "type": "error",
                "message": "Hybrid mode is only available for visible top-level Claude Code sessions",
            })
            return

        # A live SDK agent cannot survive the switch (the CLI will own the session
        # from now on) — kill it first, then flip the flag. Detached: the kill
        # holds the manager grace window for up to ~30s, and the whole tail must
        # stay ordered (kill → DB → broadcast), so run it off the receive loop to
        # avoid freezing this consumer (no heartbeat → the WS would drop).
        _spawn_detached(
            self._run_switch_hybrid(session_id),
            label=f"switch_hybrid({session_id})",
        )

    async def _run_switch_hybrid(self, session_id: str) -> None:
        """Kill the SDK agent, mark the session hybrid, broadcast. Off the receive loop."""
        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        manager = get_agent_manager_registry().get(Provider.CLAUDE_CODE)
        await manager.kill_agent(session_id, reason="switch-hybrid")

        await run_under_db_write_lock(
            lambda: Session.objects.filter(id=session_id).aupdate(hybrid=True)
        )
        logger.info("Session %s switched to hybrid CLI mode", session_id)
        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session is not None:
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

    async def _handle_kill_process(self, content: dict) -> None:
        """Handle kill_process request from client.

        Expected content format:
        {
            "type": "kill_process",
            "session_id": "sess-xxx"
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

        provider_value = await get_session_provider(session_id)
        if provider_value is not None:
            try:
                provider = Provider(provider_value)
                ensure_provider_running(provider)
            except ProviderDisabledError as e:
                await self.send_json({
                    "type": "error",
                    "code": "provider_disabled",
                    "provider": e.provider.value,
                    "message": str(e),
                })
                return
            except ValueError:
                pass  # Unknown provider value — let the registry handle it

        # Detached: a soft stop holds the manager grace window for up to ~30s
        # (``interrupt_or_kill`` interrupts, waits, then force-kills). Awaiting it
        # here would freeze this consumer's serial receive loop for that whole
        # window — no heartbeat ``pong`` (the client drops + reconnects the WS),
        # no broadcasts flushed. Nothing here needs the result: the stop reports
        # itself via broadcasts (``stopping`` then the DEAD ``process_state``).
        # ``hard_kill_agent`` is fast but detached too, for uniformity.
        _spawn_detached(
            self._run_kill_process(session_id, force=bool(content.get("force"))),
            label=f"kill_process({session_id})",
        )

    async def _run_kill_process(self, session_id: str, *, force: bool) -> None:
        """Stop an agent off the receive loop. See ``_handle_kill_process``."""
        registry = get_agent_manager_registry()
        # ``force`` escalates to a hard kill: SIGKILL the process tree now,
        # bypassing the manager lock a soft stop may hold (no grace window).
        if force:
            killed = await registry.hard_kill_agent(session_id)
        else:
            killed = await registry.kill_agent(session_id, reason="manual")
        if not killed:
            # Process not found or not in killable state — not an error, just log.
            logger.debug(
                "kill_process: session %s not killed (not found or not active)",
                session_id,
            )

    async def _handle_stop_subagent(self, content: dict) -> None:
        """Handle stop_subagent request from client.

        Expected content format:
        {
            "type": "stop_subagent",
            "session_id": "sess-xxx",
            "subagent_id": "a6c7d21"
        }

        Routes the request to the manager that owns ``session_id`` (we
        have a process running, hence we know its provider) and asks
        it to gracefully stop the subagent.
        """
        session_id = content.get("session_id")
        subagent_id = content.get("subagent_id")

        if not session_id or not subagent_id:
            logger.warning("stop_subagent missing session_id or subagent_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "stop_subagent requires session_id and subagent_id",
                }
            )
            return

        provider_value = await get_session_provider(session_id)
        if provider_value is not None:
            try:
                provider = Provider(provider_value)
                ensure_provider_running(provider)
            except ProviderDisabledError as e:
                await self.send_json({
                    "type": "error",
                    "code": "provider_disabled",
                    "provider": e.provider.value,
                    "message": str(e),
                })
                return
            except ValueError:
                pass  # Unknown provider value — let the registry handle it

        manager = get_agent_manager_registry().find_manager_for_session(session_id)
        if manager is None:
            logger.debug(
                "stop_subagent: session %s has no active manager (not found or dead)",
                session_id,
            )
            return

        stopped = await manager.stop_subagent(session_id, subagent_id)
        if not stopped:
            logger.error(
                "stop_subagent: subagent %s in session %s not stopped (not found or parent process not active)",
                subagent_id,
                session_id,
            )

    async def _handle_interrupt_session(self, content: dict) -> None:
        """Handle interrupt_session request from client.

        Expected content format:
        {
            "type": "interrupt_session",
            "session_id": "sess-xxx"
        }

        Interrupts the session's current turn while keeping the process alive
        (back to USER_TURN), routed to the manager that owns ``session_id``.
        Only meaningful in ASSISTANT_TURN; a no-op otherwise or for runtimes
        that can't interrupt in place. The resulting state change is reported
        via the normal ``process_state`` broadcast.
        """
        session_id = content.get("session_id")

        if not session_id:
            logger.warning("interrupt_session missing session_id")
            await self.send_json(
                {
                    "type": "error",
                    "message": "interrupt_session requires session_id",
                }
            )
            return

        provider_value = await get_session_provider(session_id)
        if provider_value is not None:
            try:
                provider = Provider(provider_value)
                ensure_provider_running(provider)
            except ProviderDisabledError as e:
                await self.send_json({
                    "type": "error",
                    "code": "provider_disabled",
                    "provider": e.provider.value,
                    "message": str(e),
                })
                return
            except ValueError:
                pass  # Unknown provider value — let the registry handle it

        # Detached: the hybrid-Claude interrupt presses Escape and polls the TUI
        # composer for up to ~15s, so awaiting it on this serial receive loop
        # would stall the heartbeat pong (client drops + reconnects) and block
        # broadcasts. The SDK/Codex paths are fast, but we detach uniformly. The
        # outcome surfaces via the normal ``process_state`` broadcast (USER_TURN),
        # not a return value, so nothing here needs to await it.
        _spawn_detached(
            self._run_interrupt_session(session_id),
            label=f"interrupt_session({session_id})",
        )

    async def _run_interrupt_session(self, session_id: str) -> None:
        """Interrupt a session's turn off the receive loop. See ``_handle_interrupt_session``."""
        interrupted = await get_agent_manager_registry().interrupt_agent(session_id)
        if not interrupted:
            # Not found, not in ASSISTANT_TURN, or the runtime can't interrupt —
            # not an error, just log.
            logger.debug(
                "interrupt_session: session %s not interrupted (no active "
                "manager, not in ASSISTANT_TURN, or runtime can't interrupt)",
                session_id,
            )

    async def _handle_user_draft_updated(self, content: dict) -> None:
        """Handle user_draft_updated notification from client.

        Expected content format:
        {
            "type": "user_draft_updated",
            "session_id": "sess-xxx"
        }

        This is sent (debounced) when the user is actively preparing a message
        (typing text, adding images, etc.). It updates the process's last_activity
        timestamp to prevent auto-stop due to inactivity timeout.
        """
        session_id = content.get("session_id")

        if not session_id:
            # Silent ignore - this is a fire-and-forget notification
            return

        get_agent_manager_registry().touch_agent_activity(session_id)

    async def _handle_suggest_title(self, content: dict) -> None:
        """Handle title suggestion request.

        Expected content format:
        {
            "type": "suggest_title",
            "sessionId": "...",
            "provider": "claude_code" | "codex",
            "systemPrompt": "System prompt with {text} placeholder",
            "prompt": "optional prompt text for draft/new sessions"
        }

        Requires systemPrompt and provider from the frontend (no fallback).
        ``provider`` travels in the payload because draft sessions don't exist
        in the DB yet — the backend can't resolve them via ``Session.provider``.

        Modes:
        - prompt provided: Use prompt directly (draft/new session or regenerate)
        - sessionId only (existing session): Fetch first message from DB

        Always returns the prompt used for generation, so the frontend can
        regenerate.
        """
        session_id = content.get("sessionId")
        provider_key = content.get("provider")
        system_prompt = content.get("systemPrompt")
        prompt = content.get("prompt")

        if not session_id or not system_prompt or not provider_key:
            return
        if "{text}" not in system_prompt:
            return

        try:
            provider = Provider(provider_key)
        except ValueError:
            logger.warning("suggest_title: unknown provider %r", provider_key)
            return

        try:
            ensure_provider_running(provider)
        except ProviderDisabledError as e:
            await self.send_json({
                "type": "error",
                "code": "provider_disabled",
                "provider": e.provider.value,
                "message": str(e),
            })
            return

        helpers = get_provider_helpers(provider)

        if not prompt:
            prompt = await sync_to_async(helpers.get_first_user_message)(session_id)

        suggestion = None
        if prompt:
            suggestion = await helpers.generate_title(prompt, system_prompt)

        await self.send_json({
            "type": "title_suggested",
            "sessionId": session_id,
            "suggestion": suggestion,  # Can be None
            "sourcePrompt": prompt,    # Always included for regeneration
        })

    async def _handle_update_synced_settings(self, content: dict) -> None:
        """Handle update_synced_settings request from client.

        Thin WS wrapper over the shared ``update_synced_settings`` service
        (the single rich write path, also used by the drop-request CLI). The
        service performs the merge under the settings lock — optimistic
        concurrency, per-provider consistency, the ``disabledProviders`` safety
        rules (self-healing on live agents + transition guard), the
        ``defaultProvider`` rebind and the ``_version`` bump — then, on
        acceptance, applies the orchestrator transitions and broadcasts
        ``synced_settings_updated`` to every client.

        This wrapper adds the WS-specific result path. A rejected write first
        resyncs this client. A request with a correlation ID then receives one
        direct result after the accepted broadcast or rejected resync.
        """
        synced_settings = content.get("settings")
        if not isinstance(synced_settings, dict):
            return
        base_version = content.get("baseVersion")  # None for old clients
        request_id = content.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = None

        # The service broadcasts every accepted write. This wrapper sends a
        # rejected resync and one direct result for a correlated request.
        from twicc.core.services.settings_mutation import update_synced_settings

        result = await update_synced_settings(synced_settings, base_version=base_version)
        if result.status == "rejected":
            # Resync this client before its direct verdict. The request ID makes
            # the result independent of this frame order.
            await self.send_json({
                "type": "synced_settings_updated",
                "settings": result.clean,
                "version": result.version,
            })
        if request_id:
            await self.send_json({
                "type": "synced_settings_result",
                "request_id": request_id,
                "status": result.status,
                "settings": {key: result.clean.get(key) for key in synced_settings},
                "version": result.version,
                "errors": [error._asdict() for error in result.errors],
            })
            return
        if result.errors:
            # Keep the existing fallback for old clients without correlation.
            await self.send_json({
                "type": "error",
                "code": "invalid_synced_settings",
                "message": result.errors[0].message,
                "errors": [error._asdict() for error in result.errors],
            })

    async def _handle_validate_usage_dump_path(self, content: dict) -> None:
        """Validate a usage dump file path and return the result to the client.

        Intentionally provider-agnostic: this validates a *write* target (the
        directory exists and is writable). Unlike a *read* file — whose content
        must match a provider-specific shape and goes through
        :meth:`_handle_validate_usage_file` — a dump only writes the raw
        payload back, so the check has no provider-specific content to
        verify. Do not move this into a provider handler.
        """
        file_path = content.get("file_path", "")
        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({"type": "usage_dump_path_validated", "valid": False, "message": "No file path provided"})
            return

        from twicc.usage import validate_usage_dump_path

        valid, message = await sync_to_async(validate_usage_dump_path)(file_path.strip())
        await self.send_json({"type": "usage_dump_path_validated", "valid": valid, "message": message})

    async def _handle_validate_usage_file(self, content: dict) -> None:
        """Validate a usage *read* file path for a given provider and reply.

        Cross-provider envelope: file existence + JSON parsing happens
        in :func:`twicc.usage.validate_usage_file`, then format checking
        is delegated to the provider's
        :meth:`BaseProviderHelpers.validate_usage_file_payload`.
        """
        provider_value = content.get("provider")
        file_path = content.get("file_path", "")

        if not provider_value:
            await self.send_json({
                "type": "usage_file_validated",
                "provider": None,
                "valid": False,
                "message": "Missing provider",
            })
            return
        try:
            provider = Provider(provider_value)
        except ValueError:
            await self.send_json({
                "type": "usage_file_validated",
                "provider": provider_value,
                "valid": False,
                "message": f"Unknown provider: {provider_value!r}",
            })
            return

        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({
                "type": "usage_file_validated",
                "provider": provider.value,
                "valid": False,
                "message": "No file path provided",
            })
            return

        from twicc.usage import validate_usage_file

        valid, message = await sync_to_async(validate_usage_file)(provider, file_path.strip())
        await self.send_json({
            "type": "usage_file_validated",
            "provider": provider.value,
            "valid": valid,
            "message": message,
        })

    async def _handle_validate_tmux_config_path(self, content: dict) -> None:
        """Validate a tmux config file path and return the result to the client."""
        file_path = content.get("file_path", "")
        if not isinstance(file_path, str) or not file_path.strip():
            await self.send_json({"type": "tmux_config_path_validated", "valid": False, "message": "No file path provided"})
            return

        from twicc.terminal import validate_tmux_config_path

        valid, message = await sync_to_async(validate_tmux_config_path)(file_path.strip())
        await self.send_json({"type": "tmux_config_path_validated", "valid": valid, "message": message})

    async def _handle_get_telemetry_payload(self, content: dict) -> None:
        """Reply with the last-sent telemetry payload, or a live preview if none was sent yet."""

        def _read():
            from twicc.telemetry.snapshot import build_payload
            from twicc.telemetry.state import ensure_state

            state = ensure_state()
            if state.get("last_payload") is not None:
                return state["last_payload"], state.get("last_sent_at"), False
            return build_payload(state), state.get("last_sent_at"), True

        payload, sent_at, is_preview = await sync_to_async(_read)()
        await self.send_json({
            "type": "telemetry_payload",
            "payload": payload,
            "sent_at": sent_at,
            "preview": is_preview,
        })

    async def _handle_reset_telemetry_instance_id(self, content: dict) -> None:
        """Rotate the anonymous telemetry instance id and reply with the new one."""
        from twicc.telemetry.state import reset_instance_id

        new_id = await sync_to_async(reset_instance_id)()
        await self.send_json({"type": "telemetry_instance_id_reset", "instance_id": new_id})

    async def _handle_update_workspaces(self, content: dict) -> None:
        """Handle workspace definitions update from a client."""
        workspaces = content.get("workspaces")
        if not isinstance(workspaces, list):
            return

        from twicc.atomic_json import atomic_write_json, file_lock
        from twicc.paths import get_workspaces_path

        def _write():
            # Whole-blob overwrite under the same cross-process lock the atomic
            # read-modify-write ops take (via ``locked_json_file``), so a
            # concurrent ``auto_add_project_to_workspaces`` (watcher / views) or
            # a CLI workspace op doesn't read a stale snapshot, append a
            # project, and overwrite this update.
            with file_lock(get_workspaces_path()):
                atomic_write_json(get_workspaces_path(), {"workspaces": workspaces})

        await sync_to_async(_write)()

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {"type": "workspaces_updated", "workspaces": workspaces},
            },
        )

    async def _handle_update_layouts(self, content: dict) -> None:
        """Handle the named-layouts catalog update from a client (whole-blob).

        The catalog is small, user-global config; the frontend sends the full
        list and we overwrite ``layouts.json`` wholesale (under the cross-process
        file lock), then broadcast it back so every client converges.
        """
        layouts = content.get("layouts")
        if not isinstance(layouts, list):
            return

        from twicc.atomic_json import atomic_write_json, file_lock
        from twicc.paths import get_layouts_path

        def _write():
            with file_lock(get_layouts_path()):
                atomic_write_json(get_layouts_path(), {"layouts": layouts})

        await sync_to_async(_write)()

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {"type": "layouts_updated", "layouts": layouts},
            },
        )

    async def _handle_update_terminal_config(self, content: dict) -> None:
        """Handle terminal config update from client."""
        config = content.get("config")
        if not isinstance(config, dict):
            logger.warning("Invalid terminal config update: config is not a dict")
            return

        await sync_to_async(write_terminal_config)(config)

        # Broadcast to all connected clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_config_updated",
                    "config": config,
                },
            },
        )

    async def _handle_update_message_snippets(self, content: dict) -> None:
        """Handle message snippets config update from client."""
        config = content.get("config")
        if not isinstance(config, dict):
            logger.warning("Invalid message snippets update: config is not a dict")
            return

        await sync_to_async(write_message_snippets_config)(config)

        # Broadcast to all connected clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "message_snippets_updated",
                    "config": config,
                },
            },
        )

    async def _handle_update_agent_settings_presets(self, content: dict) -> None:
        """Persist a provider's agent settings presets and broadcast the change.

        Expected payload:
        ``{"type": "update_agent_settings_presets", "provider": "<key>", "config": {"presets": [...]}}``

        Cross-provider: the on-disk format is identical for every provider —
        the ``provider`` field selects the file. Mirrors the way
        ``usage_updated`` carries its provider in the payload.
        """
        provider_value = content.get("provider")
        config = content.get("config")
        if not provider_value or not isinstance(config, dict):
            logger.warning("Invalid update_agent_settings_presets: provider=%r, config=%r", provider_value, config)
            return
        try:
            provider = Provider(provider_value)
        except ValueError:
            logger.warning("update_agent_settings_presets: unknown provider %r", provider_value)
            return

        # Defense in depth: strip hidden agent-settings fields from each preset
        # so they cannot be persisted via a frontend-authored payload. The
        # frontend does not know these fields exist (filtered from bootstrap),
        # but a stale or misbehaving client could still emit them. Stripping
        # them after persistence would let the rebroadcast leak them.
        presets = config.get("presets")
        if isinstance(presets, list):
            # Reject presets whose name is in RESERVED_PRESET_NAMES — these
            # slots are owned by ``twicc info presets`` (e.g. ``__defaults__``)
            # and must not be shadowed by a stored preset, even from a
            # hand-edited payload.
            config["presets"] = [
                p for p in presets
                if not (isinstance(p, dict) and p.get("name") in RESERVED_PRESET_NAMES)
            ]
            for preset in config["presets"]:
                if isinstance(preset, dict):
                    for hidden in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND:
                        preset.pop(hidden, None)

        await sync_to_async(write_agent_settings_presets)(provider, config)

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "agent_settings_presets_updated",
                    "provider": provider.value,
                    "config": config,
                },
            },
        )

    async def _handle_update_seen_tips(self, content: dict) -> None:
        """Persist the seen-tips state and broadcast the change.

        Expected payload:
        ``{"type": "update_seen_tips", "seen_tips": {<key>: <iso_timestamp>, ...}}``

        Last-write-wins : no version / clock. Acceptable given the rarity
        of concurrent updates for this state.
        """
        state = content.get("seen_tips", {})
        if not isinstance(state, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in state.items()
        ):
            logger.warning("Invalid update_seen_tips payload, ignoring: %r", state)
            return

        await sync_to_async(write_seen_tips)(state)

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "seen_tips_updated",
                    "seen_tips": state,
                },
            },
        )

    async def _handle_acknowledge_provider_status(self, content: dict) -> None:
        """Record the user's dismissal of a provider-status toast and broadcast it.

        Expected payload::

            {"type": "acknowledge_provider_status",
             "provider": "<key>",
             "episode": {"started_at": "<iso>", "status": "<level> | operational"}}

        The write is refused silently — like the file module does — for an
        unknown provider, a malformed episode, or one that belongs to another
        incident than the current one; nothing is broadcast then. One
        acknowledgment settles the toast on every tab and device: they all
        receive the updated file and clear their copy.
        """
        state = await sync_to_async(acknowledge_incident)(content.get("provider"), content.get("episode"))
        if state is None:
            return
        await broadcast_providers_status(state)

    async def _handle_update_seen_help(self, content: dict) -> None:
        """Persist the seen-help state and broadcast the change.

        Mirrors :meth:`_handle_update_seen_tips`. Expected payload:
        ``{"type": "update_seen_help", "seen_help": {<key>: <iso_timestamp>, ...}}``
        Last-write-wins.
        """
        state = content.get("seen_help", {})
        if not isinstance(state, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in state.items()
        ):
            logger.warning("Invalid update_seen_help payload, ignoring: %r", state)
            return

        await sync_to_async(write_seen_help)(state)

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "seen_help_updated",
                    "seen_help": state,
                },
            },
        )

    async def _handle_session_viewed(self, content: dict) -> None:
        """Handle session_viewed notification from client.

        Expected content format:
        {
            "type": "session_viewed",
            "session_id": "sess-xxx",
            "viewed_at": "2026-04-13T20:53:05.123Z",  // client timestamp
            "reason": "deactivated"                     // why this was sent
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

        now = timezone.now()

        rows = await run_under_db_write_lock(
            lambda: Session.objects.filter(id=session_id).aupdate(last_viewed_at=now)
        )
        if not rows:
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session and not session.hidden:
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

    async def _handle_mark_session_read_state(self, content: dict) -> None:
        """Handle mark_session_read_state request from client.

        Expected content format:
        {
            "type": "mark_session_read_state",
            "session_id": "sess-xxx",
            "unread": true/false
        }

        Mark as read: sets last_viewed_at = now().
        Mark as unread: clears last_viewed_at. If last_new_content_at is null
        (old sessions without backfill), sets it to now() so the unread
        comparison works.
        """
        session_id = content.get("session_id")
        unread = content.get("unread")
        if not session_id or unread is None:
            return

        from django.utils import timezone

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        from django.db.models import Case, F, Value, When

        now = timezone.now()
        if unread:
            # Clear last_viewed_at; only set last_new_content_at if it was null
            rows = await run_under_db_write_lock(
                lambda: Session.objects.filter(id=session_id).aupdate(
                    last_viewed_at=None,
                    last_new_content_at=Case(
                        When(last_new_content_at__isnull=True, then=Value(now)),
                        default=F('last_new_content_at'),
                    ),
                )
            )
        else:
            rows = await run_under_db_write_lock(
                lambda: Session.objects.filter(id=session_id).aupdate(last_viewed_at=now)
            )
        if not rows:
            return

        session = await sync_to_async(Session.objects.filter(id=session_id).first)()
        if session and not session.hidden:
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

    async def _handle_changelog_seen(self, content: dict) -> None:
        """Persist that the user has seen the changelog for the given version."""
        version = content.get("version")
        if not version:
            return

        def _persist():
            from twicc.synced_settings import _settings_lock, read_synced_settings, write_synced_settings

            with _settings_lock:
                all_settings = read_synced_settings()
                all_settings["lastChangelogVersionSeen"] = version
                all_settings["_version"] = all_settings.get("_version", 0) + 1
                write_synced_settings(all_settings)

        await sync_to_async(_persist)()

    async def _handle_list_terminals(self, data):
        """Handle list_terminals request: return active tmux terminal indices (with labels) for a terminal context."""
        terminal_context = data.get("terminal_context")
        if not terminal_context:
            await self.send_json({"type": "error", "message": "Missing terminal_context"})
            return

        from twicc.terminal import list_tmux_terminals

        terminals = await asyncio.to_thread(list_tmux_terminals, terminal_context)

        await self.send_json({
            "type": "terminal_list",
            "terminal_context": terminal_context,
            "terminals": [t.index for t in terminals],
            "labels": {str(t.index): t.label for t in terminals if t.label},
            "autoAttach": {str(t.index): True for t in terminals if t.auto_attach},
        })

    async def _handle_kill_terminal(self, data):
        """Handle kill_terminal request: kill a secondary terminal's tmux session and broadcast."""
        terminal_context = data.get("terminal_context")
        terminal_index = data.get("terminal_index")
        if not terminal_context or terminal_index is None:
            await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
            return

        # Safety: never kill the main terminal via this handler
        if terminal_index == 0:
            await self.send_json({"type": "error", "message": "Cannot kill main terminal"})
            return

        from twicc.terminal import kill_tmux_terminal

        await asyncio.to_thread(kill_tmux_terminal, terminal_context, terminal_index)

        # Broadcast to all clients
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_killed",
                    "terminal_context": terminal_context,
                    "terminal_index": terminal_index,
                },
            },
        )

    async def _handle_rename_terminal(self, data):
        """Handle rename_terminal request: set a display label on a terminal's tmux session.

        For tmux terminals, the label is stored as a tmux user option and
        persists across reconnections. The rename is broadcast to all clients
        for cross-device sync.
        """
        terminal_context = data.get("terminal_context")
        terminal_index = data.get("terminal_index")
        label = data.get("label", "")
        if not terminal_context or terminal_index is None:
            await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
            return

        from twicc.terminal import TERMINAL_LABEL_MAX_LENGTH, set_tmux_terminal_label

        # Sanitize: trim and truncate
        label = label.strip()[:TERMINAL_LABEL_MAX_LENGTH]

        await asyncio.to_thread(set_tmux_terminal_label, terminal_context, terminal_index, label)

        # Broadcast to all clients (including the sender, for confirmation)
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_renamed",
                    "terminal_context": terminal_context,
                    "terminal_index": terminal_index,
                    "label": label,
                },
            },
        )

    async def _handle_set_terminal_autoattach(self, data):
        """Handle set_terminal_autoattach: set/clear the auto-attach-into-children flag.

        The flag is stored as a tmux user option (persists across reconnections) and
        broadcast to all clients for cross-device sync. tmux-only by construction.
        """
        terminal_context = data.get("terminal_context")
        terminal_index = data.get("terminal_index")
        enabled = bool(data.get("enabled"))
        if not terminal_context or terminal_index is None:
            await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
            return

        from twicc.terminal import set_tmux_terminal_autoattach

        await asyncio.to_thread(set_tmux_terminal_autoattach, terminal_context, terminal_index, enabled)

        # Broadcast to all clients (including the sender, for confirmation)
        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "terminal_autoattach_changed",
                    "terminal_context": terminal_context,
                    "terminal_index": terminal_index,
                    "enabled": enabled,
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
    # Terminal with session context
    path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
    # Terminal with project context only (no session)
    path("ws/terminal/<str:project_id>/<int:terminal_index>/", terminal_application),
    # Terminal with no project (global/workspace context)
    path("ws/terminal/<int:terminal_index>/", terminal_application),
    # Public live share stream (design §10, O4). Before ws/ so it isn't shadowed.
    path("ws/share/<str:token>/", ShareConsumer.as_asgi()),
    path("ws/", WSConsumer.as_asgi()),
]

# Django ASGI application for HTTP requests
django_asgi_app = get_asgi_application()


async def http_router(scope, receive, send):
    """Route ``/mcp`` to the raw-ASGI MCP endpoint; everything else to Django.

    The MCP endpoint is intentionally mounted *in front of* Django (no
    middleware, no urls.py, no SPA catch-all): it speaks its own streamable-HTTP
    protocol with its own token auth.
    """
    path = scope.get("path", "")
    if path == "/mcp" or path.startswith("/mcp/"):
        from twicc.mcp.endpoint import handle_mcp

        await handle_mcp(scope, receive, send)
        return
    await django_asgi_app(scope, receive, send)


# Protocol router for HTTP and WebSocket
# SessionMiddlewareStack reads the session cookie from the WebSocket
# HTTP upgrade request, making session data available in the consumer's scope.
application = ProtocolTypeRouter(
    {
        "http": http_router,
        "websocket": SessionMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)

# Serve static files via BlackNoise at the ASGI level.
application = BlackNoise(application, immutable_file_test=lambda *_: True)
application.add(settings.FRONTEND_DIST_DIR, "/static")

# Common public-origin gate (peer-origin-routing design §9-§11): routes the
# External, Share, and Peer addresses. /share/ is served ONLY on the configured
# Share hostname; /peer/ ONLY on the configured Peer authority; a dedicated Peer
# authority serves nothing else. The gate reads the active settings cache, so
# an Apply in Settings takes effect on the next request with no restart. Wrapped
# ABOVE BlackNoise so rejected authorities never reach the /static/ mount.
from twicc.origin_gate import PublicOriginGate, ShareOnlyApp  # noqa: E402

application = PublicOriginGate(application, ShareOnlyApp(application))
