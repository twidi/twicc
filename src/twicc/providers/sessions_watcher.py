"""
Provider-agnostic file watcher for JSONL session files.

Each provider stores session files under a native directory layout below its
own ``projects_dir`` (e.g. Claude Code uses ``<claude home>/projects/``, resolved
from ``twicc.provider_homes``). A provider-specific subclass of
:class:`BaseSessionsWatcher` plugs that layout in by overriding
:meth:`BaseSessionsWatcher.parse_session_file` and setting the ``projects_dir``
instance attribute in its ``__init__``. The base class owns the watchfiles loop,
ORM updates, WebSocket broadcasts, full-text search indexing, and the
projects-dir polling phase — all generic across providers.

The watcher does not maintain a registry: each provider's orchestrator
instantiates and starts its own subclass directly (typically via a
provider-local ``get_watcher()`` singleton helper). The compute object
returned by :meth:`BaseSessionsWatcher.get_compute` carries both
``provider`` and ``compute_version``, so the watcher does not need to inject
those separately.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from watchfiles import Change, awatch

from twicc import search
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
    session_compute_ready,
)
from twicc.logging_context import current_provider
from twicc.projects import (
    ensure_worktree_link,
    load_project_directories,
    load_project_git_roots,
    register_project,
    update_project_metadata as _update_project_metadata_sync,
)
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.workspaces import auto_add_project_to_workspaces

if TYPE_CHECKING:
    from twicc.providers.compute_base import BaseSessionCompute, ToolResultUpdate

logger = logging.getLogger(__name__)


# Polling intervals (seconds) for the "waiting for projects dir" phase.
PROJECTS_DIR_POLL_INTERVAL = 30
PROJECTS_DIR_POLL_INTERVAL_FAST = 5
# How long fast-polling stays engaged after a request.
FAST_POLL_DURATION = 30


class ParsedSessionFile:
    """Identity of a session file as recognized by a provider.

    Built by :meth:`BaseSessionsWatcher.parse_session_file`, which may
    inspect both the path and (optionally) the file content. Codex for
    instance reads the first JSONL line to recover ``project_id`` from
    the session's ``cwd``, since the file path itself does not encode it.

    ``title`` is an optional initial title supplied by the provider's
    parse step (e.g. read from Codex's state DB) — used only when the
    session is created for the first time, ignored on later events.
    """
    __slots__ = (
        'compute_ready_on_create',
        'file_path',
        'parent_session_id',
        'project_id',
        'session_id',
        'title',
        'type',
    )

    def __init__(
        self,
        project_id: str,
        session_id: str,
        type: SessionType,
        file_path: str,
        parent_session_id: str | None = None,
        title: str | None = None,
        compute_ready_on_create: bool = True,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.type = type
        self.parent_session_id = parent_session_id
        # Provider-relative path (relative to the watcher's ``projects_dir``).
        self.file_path = file_path
        self.title = title
        self.compute_ready_on_create = compute_ready_on_create


class IndexingRequest(NamedTuple):
    """Side-effect payload returned by :meth:`BaseSessionsWatcher.sync_and_broadcast`.

    Describes a Tantivy full-text indexing pass the caller should run AFTER
    releasing the DB write lock. Tantivy I/O is unrelated to the SQLite DB
    we serialise with the lock, so holding the lock across the indexing
    call would block every other DB writer for nothing (and the index
    pass on a long session can take several hundred ms — `reindex_session`
    rewrites the whole session). Returning the request lets the watcher
    main loop schedule the work outside the lock.
    """
    session_id: str
    new_line_nums: list[int]
    title_changed: bool


# ---- @sync_to_async helpers (stateless, no provider coupling) ----

@sync_to_async
def update_project_metadata(project: Project) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    _update_project_metadata_sync(project.id)


@sync_to_async
def get_project_by_id(project_id: str) -> Project | None:
    """Get a project by ID, or None if not found."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


@sync_to_async
def get_session_by_id(session_id: str) -> Session | None:
    """Get a session by ID, or None if not found."""
    try:
        return Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        return None


@sync_to_async
def check_file_has_content_async(file_path: Path) -> bool:
    """Check if a JSONL file has any valid lines (async wrapper).

    Decoded with ``errors="replace"`` so invalid UTF-8 can't raise
    ``UnicodeDecodeError`` (a ``ValueError``, not an ``OSError``); mirrors
    :func:`twicc.sync_helpers.check_file_has_content`.
    """
    if not file_path.exists():
        return False

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                return True
    return False


@sync_to_async
def get_session_items(session: Session, line_nums: list[int]) -> list[dict]:
    """Get full session items (with content) by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).order_by("line_num")
    return [serialize_session_item(item) for item in items]


@sync_to_async
def get_items_metadata(session: Session, line_nums: list[int]) -> list[dict]:
    """Get metadata (without content) for specific items by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).defer('content').order_by("line_num")
    return [serialize_session_item_metadata(item) for item in items]


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


@sync_to_async
def mark_session_search_version_current(session_id: str) -> None:
    """Mark ``Session.search_version = CURRENT_SEARCH_VERSION`` for the row.

    Called from the watcher AFTER a successful Tantivy indexing pass
    (under a separate short DB write lock acquire). An ``asyncio``
    cancellation between the indexing call and this mark skips it,
    so the startup search-indexing sweep can retry the session on
    the next boot — matching the previous behaviour where mark and
    indexing were both inside the same lock and a cancel skipped both.
    """
    from django.conf import settings
    Session.objects.filter(id=session_id).update(
        search_version=settings.CURRENT_SEARCH_VERSION,
    )


@sync_to_async
def refresh_project(project: Project) -> Project:
    """Refresh project from database."""
    project.refresh_from_db()
    return project


async def broadcast_message(channel_layer, message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


class BaseSessionsWatcher:
    """Provider-agnostic file watcher for JSONL session files.

    Subclasses set :attr:`projects_dir` and implement :meth:`parse_session_file`
    and :meth:`get_compute`. The base orchestrates everything else:
    incremental sync via the compute object, broadcast of session/project
    updates, full-text search indexing, and the projects-dir polling phase
    used while the directory does not yet exist.

    :meth:`request_fast_poll` and :meth:`stop_watcher` use per-instance
    events so multiple providers can run watchers in parallel without
    stepping on each other.
    """

    # Set by each subclass's ``__init__`` from ``twicc.provider_homes`` — an
    # instance attribute resolved at instantiation, never a class-level constant
    # evaluated at import (the provider home is configurable per instance).
    projects_dir: Path

    def __init__(self) -> None:
        self._stop_event: asyncio.Event | None = None
        self._boost_event: asyncio.Event | None = None
        self._fast_poll_until: float = 0.0

    # ------------------------------------------------------------------
    # Provider extension surface — overridden by each subclass
    # ------------------------------------------------------------------

    async def parse_session_file(self, path: Path) -> ParsedSessionFile | None:
        """Identify a JSONL file as a provider-recognized session.

        Returns ``None`` if ``path`` does not match any known layout for
        this provider (the watcher silently skips such paths). The
        returned ``file_path`` must be the path relative to
        :attr:`projects_dir`.

        Implementations may need to read the file (e.g. Codex extracts
        ``project_id`` from the first JSONL line); they should offload
        any blocking I/O via :func:`asyncio.to_thread`.
        """
        raise NotImplementedError

    def get_compute(self) -> BaseSessionCompute:
        """Return the provider's compute singleton.

        Used for:

        - new-line ingestion via
          :meth:`~twicc.providers.compute_base.BaseSessionCompute.sync_session_items_from_file`,
        - reading provider metadata (:attr:`provider`,
          :attr:`compute_version`) when creating fresh ``Session`` rows
          and when looking up the matching helpers for search indexing.
        """
        raise NotImplementedError

    async def _fetch_initial_title(self, parsed: ParsedSessionFile) -> str | None:
        """Optional hook : fetch an initial title for a newly-discovered session.

        Called by :meth:`sync_and_broadcast` exactly once per new session
        (never on subsequent JSONL modify events). Subclasses that have
        an out-of-band title source (e.g. the Codex state DB) override
        this. Default returns ``None`` — no out-of-band title.
        """
        return None

    @asynccontextmanager
    async def session_change_context(
        self, parsed: ParsedSessionFile,
    ) -> AsyncIterator[None]:
        """Wrap one complete parsed-file callback with provider coordination."""

        yield

    async def _after_tool_result_broadcast(self, update: ToolResultUpdate) -> None:
        """Hook fired right after a ``tool_state`` broadcast.

        Default implementation is a no-op. Claude Code overrides this to
        clean up the agent manager's ``_active_tools`` registry when a
        ``tool_result`` appears in JSONL but no PostToolUse hook ever
        fired (e.g. CLI-side validation rejection).
        """
        return

    async def _after_compaction_synced(self, session_id: str) -> None:
        """Hook fired once per live batch that ingested a COMPACT_SUMMARY line.

        Default implementation is a no-op. Codex overrides this to notify
        its agent manager that a compaction landed, so a live agent that
        triggered a manual ``/compact`` can leave its ``ASSISTANT_TURN``.
        Fires only on the live incremental-sync path (never on the
        background recompute), so the signal is never replayed.
        """
        return

    async def _after_agents_stopped(
        self, session_id: str, stopped_agent_ids: list[str],
    ) -> None:
        """Hook fired when subagents of ``session_id`` naturally finished.

        ``stopped_agent_ids`` are the subagent session ids whose
        ``last_stopped_at`` was just stamped by
        ``check_agent_naturally_stopped`` while syncing the parent's file.
        Default implementation is a no-op. Codex overrides this to release
        a live parent parked in the subagent hold — a spawned subagent's
        completion never reaches the parent's SDK stream, so this watcher
        signal is the only release channel. Live incremental-sync path
        only; implementations must never block the ingest path on agent
        locks (fire-and-forget a task instead).
        """
        return

    async def _after_new_lines_synced(
        self,
        session: Session,
        new_line_nums: list[int],
        tool_result_updates: list[ToolResultUpdate],
    ) -> None:
        """Hook fired once per live batch that ingested fresh JSONL lines.

        Default implementation is a no-op. Claude Code overrides this to
        derive hybrid-session state signals (user message → assistant turn,
        ``turn_duration`` → user turn, tool results → pending cleared) from
        the just-computed items. Live incremental-sync path only, like
        :meth:`_after_compaction_synced`. Implementations must never block
        the ingest path on agent locks (fire-and-forget a task instead).
        """
        return

    async def maybe_handle_special_change(
        self,
        path: Path,
        change_type: Change,
        channel_layer,
    ) -> bool:
        """Hook for provider-specific path patterns that aren't ``.jsonl`` files.

        Called for every change before the default ``.jsonl`` handling.
        Returning ``True`` consumes the event (default loop ``continue``);
        returning ``False`` lets the base watcher proceed with its normal
        dispatch (skip non-jsonl, parse session file, sync, broadcast).

        Default implementation is a no-op that returns ``False``. Claude
        Code overrides this to detect direct children of
        :attr:`projects_dir` as project directories and broadcast their
        creation/deletion as ``project_*`` events.
        """
        return False

    # ------------------------------------------------------------------
    # Per-instance event accessors
    # ------------------------------------------------------------------

    def get_stop_event(self) -> asyncio.Event:
        """Get or create the stop event for this watcher instance."""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    def get_boost_event(self) -> asyncio.Event:
        """Get or create the boost event used to wake the polling loop."""
        if self._boost_event is None:
            self._boost_event = asyncio.Event()
        return self._boost_event

    def request_fast_poll(self, duration: float = FAST_POLL_DURATION) -> None:
        """
        Shorten the projects-dir poll interval for ``duration`` seconds.

        Called when something likely to create the watcher's
        :attr:`projects_dir` is about to happen (e.g. starting a Claude
        Code SDK session). Cheap no-op when the watcher is already past
        the polling phase — the boost event is consumed only by that
        phase's wait loop.
        """
        deadline = time.monotonic() + duration
        self._fast_poll_until = max(self._fast_poll_until, deadline)
        self.get_boost_event().set()

    def stop_watcher(self) -> None:
        """Signal this watcher instance to stop."""
        if self._stop_event is not None:
            self._stop_event.set()
        # Wake the polling loop if it's currently sleeping.
        if self._boost_event is not None:
            self._boost_event.set()

    # ------------------------------------------------------------------
    # Session creation (uses provider/compute_version from the compute)
    # ------------------------------------------------------------------

    def create_session_sync(
        self,
        parsed: ParsedSessionFile,
        project: Project,
        parent_session: Session | None = None,
        agent_settings: AgentSettings | None = None,
    ) -> Session:
        """Create a session or subagent in the database (sync).

        Wrap with :func:`sync_to_async` at the call site. ``provider`` and
        ``compute_version`` are read from the compute singleton, so the
        owning provider stays in one place.
        """
        from twicc.pending_session_attributes import pop_pending_session_attributes

        compute = self.get_compute()
        if parsed.type == SessionType.SUBAGENT:
            if parent_session is None:
                raise ValueError("parent_session is required for subagents")
            return Session.objects.create(
                id=parsed.session_id,
                project=project,
                provider=compute.provider,
                file_path=parsed.file_path,
                type=SessionType.SUBAGENT,
                parent_session=parent_session,
                compute_version=(
                    compute.compute_version if parsed.compute_ready_on_create else None
                ),
            )
        kwargs: dict = {
            'id': parsed.session_id,
            'project': project,
            'provider': compute.provider,
            'file_path': parsed.file_path,
            'compute_version': (
                compute.compute_version if parsed.compute_ready_on_create else None
            ),
        }
        if parsed.title:
            kwargs["title"] = parsed.title
        if agent_settings is not None:
            for field, value in agent_settings._asdict().items():
                if value is not None:
                    kwargs[field] = value
        pending = pop_pending_session_attributes(parsed.session_id)
        if pending is not None:
            kwargs["hidden"] = pending.hidden
            kwargs["mute_on_user_turn"] = pending.mute_on_user_turn
            if pending.spawned_by_id is not None:
                kwargs["spawned_by_id"] = pending.spawned_by_id
            if pending.spawn_root_id is not None:
                kwargs["spawn_root_id"] = pending.spawn_root_id
            if pending.annotations:
                kwargs["annotations"] = pending.annotations
            if pending.system_prompt_addendum is not None:
                kwargs["system_prompt_addendum"] = pending.system_prompt_addendum
            if pending.hybrid:
                kwargs["hybrid"] = True
            if pending.layout:
                kwargs["layout"] = pending.layout
        return Session.objects.create(**kwargs)

    # ------------------------------------------------------------------
    # Full-text search indexing
    # ------------------------------------------------------------------

    async def _index_new_items_for_search(
        self, session: Session, line_nums: list[int]
    ) -> None:
        """Index new session items for full-text search.

        Only indexes ``user_message`` and ``assistant_message`` items.
        Errors are caught and logged to never crash the watcher.
        """
        try:
            if not search.is_initialized():
                return

            items = await sync_to_async(
                lambda: list(
                    SessionItem.objects.filter(
                        session=session,
                        line_num__in=line_nums,
                        kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
                    )
                )
            )()

            helpers = get_provider_helpers(self.get_compute().provider)
            indexed_count = 0
            for item in items:
                text = helpers.extract_indexable_text(item)
                if text:
                    await asyncio.to_thread(
                        search.index_document,
                        session.id,
                        session.project_id,
                        item.line_num,
                        text,
                        "user" if item.kind == ItemKind.USER_MESSAGE else "assistant",
                        item.timestamp,
                        session.archived,
                        hidden=session.hidden,
                        spawned_by_id=session.spawned_by_id,
                        spawn_root_id=session.spawn_root_id,
                    )
                    indexed_count += 1

            if indexed_count > 0:
                await asyncio.to_thread(search.commit)
        except Exception:
            logger.exception(
                "Error indexing session items for search (session=%s)", session.id
            )

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    async def sync_and_broadcast(
        self,
        path: Path,
        parsed: ParsedSessionFile,
        change_type: Change,
        channel_layer,
    ) -> IndexingRequest | None:
        """
        Handle a session or subagent file change.

        Synchronizes with the database and broadcasts updates via WebSocket.
        Empty files (0 lines) are ignored and not created in the database.

        Returns an :class:`IndexingRequest` when the caller should run a
        Tantivy indexing pass for this event AFTER releasing the DB write
        lock — Tantivy I/O is unrelated to the SQLite DB the lock protects.
        Returns ``None`` when no indexing is needed (subagent, no new items,
        or no user messages yet).

        Callers MUST resolve ``parsed.title`` for brand-new sessions
        (out-of-band title fetches like Codex's state DB read) BEFORE
        entering the lock and calling this method — see
        :meth:`start_watcher`. The method itself does NOT call
        :meth:`_fetch_initial_title`.
        """
        compute = self.get_compute()
        is_subagent = parsed.type == SessionType.SUBAGENT
        indexing_request: IndexingRequest | None = None

        # For subagents, verify parent session exists
        parent_session: Session | None = None
        if is_subagent:
            parent_session = await get_session_by_id(parsed.parent_session_id)
            if parent_session is None:
                # Parent session not yet synced, skip for now
                logger.debug(
                    f"Skipping subagent {parsed.session_id}: "
                    f"parent session {parsed.parent_session_id} not found"
                )
                return

        if change_type == Change.deleted:
            # File deleted - mark as stale
            session = await get_session_by_id(parsed.session_id)
            if session and not session.stale:
                session.stale = True
                await sync_to_async(session.save)(update_fields=["stale"])
                if not session.hidden:
                    await broadcast_message(channel_layer, {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    })
                # Update project metadata (includes total_cost which changes for subagents too)
                project = await get_project_by_id(parsed.project_id)
                if project:
                    await update_project_metadata(project)
                    project = await refresh_project(project)
                    await broadcast_message(channel_layer, {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    })
            return

        # Check if session already exists in DB
        session = await get_session_by_id(parsed.session_id)

        # For new sessions, check that the file has content BEFORE creating
        # the Project row, so an empty-file event doesn't leave a project
        # (and later a session) row with nothing behind it.
        if session is None:
            has_content = await check_file_has_content_async(path)
            if not has_content:
                return

        # Ensure project exists. ``register_project`` broadcasts
        # ``project_added`` on creation. Auto-add is deferred until after
        # ``sync_session_items_from_file`` has resolved the directory from
        # the JSONL body — see the explicit call at the end of this method.
        project, project_created = await register_project(parsed.project_id)

        # The directory may not be resolvable on THIS event: recent Claude
        # CLI session files open with header lines (``last-prompt``, ``mode``,
        # ``permission-mode``) that carry no ``cwd`` — the first cwd-bearing
        # line lands on a later event. Track the directory-less state so the
        # workspace auto-add at the end of this method retries on every event
        # until the directory is known, not just on the creation event.
        project_had_no_directory = not project.directory

        # Track whether this session was just created via TwiCC (had pending settings).
        # Used below to broadcast an early session_updated even before the user message
        # appears in the JSONL, so the frontend can drop the draft flag immediately.
        pending_agent_settings: AgentSettings | None = None

        if session is None:
            # ``parsed.title`` was resolved by the caller before entering
            # the lock (see ``start_watcher``) — out-of-band title fetches
            # (e.g. Codex SDK state DB read) MUST stay outside the lock.
            # Whatever the caller left here is used as the initial title.

            # Create session (regular or subagent)
            # Pop any pending settings set by the WS handler for new sessions
            from twicc.pending_agent_settings import pop_pending_agent_settings

            pending_agent_settings = pop_pending_agent_settings(parsed.session_id)
            session = await sync_to_async(self.create_session_sync)(
                parsed, project, parent_session, pending_agent_settings,
            )

        old_title = session.title
        new_line_nums, modified_line_nums, agent_link_updates, workflow_link_updates, tool_result_updates, agent_stopped_updates, found_compact_summary = await sync_to_async(
            compute.sync_session_items_from_file
        )(session, path)
        title_changed = session.title != old_title

        # Live-only signal: a freshly-ingested COMPACT_SUMMARY line means a
        # compaction just landed for this session. Hand it to the provider
        # hook (Codex relays it to the agent manager so a manually-triggered
        # ``/compact`` can end the agent's ASSISTANT_TURN). This is the live
        # path exclusively — the background recompute goes through
        # ``compute_session_metadata``, never here — so the signal can never
        # be replayed on a metadata recompute.
        if found_compact_summary:
            await self._after_compaction_synced(session.id)

        if new_line_nums:
            # New JSONL lines were just appended → this session is producing
            # output even if its agent's SDK message loop has gone quiet (e.g.
            # a background Bash the CLI keeps draining into the file while the
            # agent sits in USER_TURN). Reset the idle-timeout countdown so the
            # timeout monitor (see ``BaseAgentManager._state_based_timeout``)
            # doesn't auto-kill the agent — and its still-running background
            # work — mid-flight. A subagent JSONL has no live agent of its own
            # in the manager, so the activity belongs to its parent (whose
            # process owns the subagent's work); for a top-level session it's
            # the session itself — never both. ``touch_agent_activity`` is a
            # no-op unless a live agent in USER_TURN/ASSISTANT_TURN owns the id,
            # so this is free for the overwhelmingly common non-live event.
            # Local import: crosses the providers→agent layer; keeping the
            # registry's manager stack out of this module's import graph.
            from twicc.agent.registry import get_agent_manager_registry

            activity_session_id = (
                parsed.parent_session_id if is_subagent else parsed.session_id
            )
            get_agent_manager_registry().touch_agent_activity(activity_session_id)

            # Provider hook for batch-level signals derived from the fresh
            # lines (hybrid state bridge). Subagent lines never carry the
            # parent's turn markers, so only top-level files feed it.
            if not is_subagent:
                await self._after_new_lines_synced(
                    session, list(new_line_nums), list(tool_result_updates),
                )

            # Refresh session to get computed values
            session = await refresh_session(session)

            # Only broadcast if session has user messages — empty sessions (e.g. just
            # system/metadata lines) stay silent in DB until a user message arrives.
            # Exception: TwiCC-initiated sessions (identified by having had pending
            # settings) get an early session_updated so the frontend drops the draft
            # flag immediately, without waiting for the user message to appear in JSONL.
            #
            # Subagents bypass the gate entirely: the "wait for a user message"
            # rule exists to keep *listed* sessions out of the UI until they
            # carry something a human wrote, and a subagent is never listed —
            # it is only ever reached through its parent's tool card. Some
            # never own a user message at all (Codex multi-agent v2 hands the
            # task over as an encrypted inter-agent message), so gating them
            # froze their open tab on whatever the first fetch returned: no
            # live items, and no ``last_stopped_at`` update to retire the
            # "running" indicator.
            broadcast_session = (
                session.user_message_count > 0 or is_subagent or pending_agent_settings is not None
            )
            if not session.hidden and broadcast_session:
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(session),
                })

            if session.user_message_count > 0 or is_subagent:
                if not session.hidden:
                    # Broadcast new items (with updated metadata of pre-existing items if any)
                    new_items = await get_session_items(session, new_line_nums)
                    # Per-provider wire-only enrichment (e.g. Codex stamps
                    # ``stream_uuid`` so the frontend can retire its streaming
                    # placeholder). Default helper implementation is a no-op
                    # so this stays generic.
                    get_provider_helpers(self.get_compute().provider).enrich_live_items_payload(
                        session.id, new_items,
                    )
                    if new_items:
                        message = {
                            "type": "session_items_added",
                            "session_id": parsed.session_id,
                            "project_id": parsed.project_id,
                            "parent_session_id": parsed.parent_session_id,
                            "items": new_items,
                        }
                        if modified_line_nums:
                            updated_metadata = await get_items_metadata(session, modified_line_nums)
                            if updated_metadata:
                                message["updated_metadata"] = updated_metadata
                        await broadcast_message(channel_layer, message)

                # For subagents, broadcast parent session update (costs have changed)
                if is_subagent and parent_session:
                    parent_session = await refresh_session(parent_session)
                    if not parent_session.hidden:
                        await broadcast_message(channel_layer, {
                            "type": "session_updated",
                            "session": serialize_session(parent_session),
                        })

                # Update project metadata (includes total_cost which changes for subagents too)
                await update_project_metadata(project)
                project = await refresh_project(project)
                await broadcast_message(channel_layer, {
                    "type": "project_updated",
                    "project": serialize_project(project),
                })

                # Broadcast agent link state changes (subagent linked).
                # ``agent_slug`` carries the spawned subagent's nickname
                # (Codex's ``agent_nickname`` persisted as
                # ``Session.slug``) so the frontend can label the tool
                # card / agent tab without separately hydrating the
                # subagent Session row. Stays ``None`` when the subagent
                # file hasn't been parsed yet (the AgentLink and the
                # subagent Session are created by independent watcher
                # passes — race expected); the next ``subagents_state``
                # fetch re-resolves it.
                if agent_link_updates:
                    slugs_by_id = await sync_to_async(
                        lambda ids: dict(
                            Session.objects.filter(id__in=ids).values_list("id", "slug")
                        )
                    )([update.agent_id for update in agent_link_updates])
                    for update in agent_link_updates:
                        await broadcast_message(channel_layer, {
                            "type": "agent_link_created",
                            "parent_session_id": update.parent_session_id,
                            "agent_session_id": update.agent_id,
                            "agent_slug": slugs_by_id.get(update.agent_id),
                            "tool_use_id": update.tool_use_id,
                            "tool_use_line_num": update.tool_use_line_num,
                            "is_background": update.is_background,
                            "started_at": update.started_at.isoformat() if update.started_at else None,
                            "project_id": parsed.project_id,
                        })

                # Broadcast workflow tool-link state changes (a Workflow tool_use
                # paired with its run via toolUseResult.runId). Powers the in-chat
                # "View Workflow" button, mirroring agent_link_created.
                for update in workflow_link_updates:
                    await broadcast_message(channel_layer, {
                        "type": "workflow_link_created",
                        "session_id": update.session_id,
                        "tool_use_id": update.tool_use_id,
                        "run_id": update.run_id,
                        "project_id": parsed.project_id,
                    })

                # Broadcast tool result state changes
                for update in tool_result_updates:
                    await broadcast_message(channel_layer, {
                        "type": "tool_state",
                        "session_id": update.session_id,
                        "tool_use_id": update.tool_use_id,
                        "result_count": update.result_count,
                        "completed_at": update.completed_at.isoformat() if update.completed_at else None,
                        "extra": update.extra,
                        "error": update.error,
                        "tool_result_line_nums": list(update.tool_result_line_nums),
                    })
                    await self._after_tool_result_broadcast(update)

                # Broadcast session_updated for subagents that naturally finished
                for stopped in agent_stopped_updates:
                    stopped_session = await get_session_by_id(stopped.agent_session_id)
                    if stopped_session and not stopped_session.hidden:
                        await broadcast_message(channel_layer, {
                            "type": "session_updated",
                            "session": serialize_session(stopped_session),
                        })
                if agent_stopped_updates:
                    await self._after_agents_stopped(
                        session.id,
                        [u.agent_session_id for u in agent_stopped_updates],
                    )

                # Full-text search indexing (sessions only, not subagents):
                # build the indexing request — the actual Tantivy I/O AND
                # the ``search_version`` mark run OUTSIDE this lock, after
                # the caller has dispatched the indexing pass and observed
                # its outcome. Tantivy writes touch the search-index
                # directory, NOT the SQLite DB the lock protects, so
                # holding it across the indexing call would block every
                # other DB writer for nothing (a full ``reindex_session``
                # on a long session can take several hundred ms).
                #
                # The mark is moved out of this lock (and out of this
                # method entirely) so that a cancellation between the
                # mark and the Tantivy call cannot leave the row marked
                # current while Tantivy never ran — which would prevent
                # the startup sweep from retrying on the next boot.
                if not is_subagent:
                    indexing_request = IndexingRequest(
                        session_id=session.id,
                        new_line_nums=list(new_line_nums),
                        title_changed=title_changed,
                    )

        elif session.stale:
            # File reappeared - unstale
            session.stale = False
            await sync_to_async(session.save)(update_fields=["stale"])
            if not session.hidden:
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(session),
                })

        # Auto-add the project to workspaces whose patterns match its
        # directory. Deferred to here (rather than into ``register_project``
        # above) because the directory is only resolved by
        # ``sync_session_items_from_file`` — at creation time the watcher
        # only knows ``project_id``, not the cwd. Gated on the directory
        # having been unknown BEFORE this sync (not on ``project_created``):
        # the event that creates the project often syncs only cwd-less header
        # lines, so the directory is resolved by a LATER event — that event
        # must run the auto-add, or the project never joins its workspaces.
        # Idempotent, and a no-op refresh+check for the (rare) directory-less
        # project until the cwd shows up; projects with a known directory
        # skip it entirely.
        if project_created or project_had_no_directory:
            project = await refresh_project(project)
            if project.directory:
                await auto_add_project_to_workspaces(project.id, project.directory)
                await ensure_worktree_link(project.id, project.directory)

        return indexing_request

    async def _process_parsed_session_change(
        self,
        path: Path,
        parsed: ParsedSessionFile,
        change_type: Change,
        channel_layer,
    ) -> None:
        """Run one complete callback after a provider identifies its session."""

        # Out-of-band initial-title fetch runs outside the DB write lock but
        # inside the provider's complete callback context.
        if (
            change_type != Change.deleted
            and parsed.type == SessionType.SESSION
            and parsed.title is None
            and await get_session_by_id(parsed.session_id) is None
            and await check_file_has_content_async(path)
        ):
            parsed.title = await self._fetch_initial_title(parsed)

        indexing = await run_under_db_write_lock(
            lambda: self.sync_and_broadcast(path, parsed, change_type, channel_layer)
        )

        # Tantivy work stays outside the DB write lock. The provider callback
        # context remains active so migration cannot replace history midway.
        if indexing is None:
            return

        indexed_session = await get_session_by_id(indexing.session_id)
        if indexed_session is None:
            return
        if not session_compute_ready(indexed_session):
            if search.is_initialized():
                await asyncio.to_thread(search.delete_session_documents, indexing.session_id)
                await asyncio.to_thread(search.commit)
            return

        try:
            if indexing.title_changed:
                await asyncio.to_thread(search.reindex_session, indexing.session_id)
            else:
                await self._index_new_items_for_search(
                    indexed_session, indexing.new_line_nums,
                )
        except Exception:
            logger.exception(
                "Error indexing session for search (session=%s, title_changed=%s)",
                indexing.session_id,
                indexing.title_changed,
            )

        try:
            await run_under_db_write_lock(
                lambda: mark_session_search_version_current(indexing.session_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Error marking session %s as search-indexed",
                indexing.session_id,
            )

    # ------------------------------------------------------------------
    # Polling phase + entry point
    # ------------------------------------------------------------------

    async def _wait_for_projects_dir(self) -> bool:
        """
        Poll until :attr:`projects_dir` exists or shutdown is requested.

        The interval is normally :data:`PROJECTS_DIR_POLL_INTERVAL` seconds,
        but drops to :data:`PROJECTS_DIR_POLL_INTERVAL_FAST` while a
        fast-poll request is active (typically right after a session start
        signal from the owning provider).

        Returns True if the directory appeared, False if shutdown was signaled.
        """
        stop_event = self.get_stop_event()
        boost_event = self.get_boost_event()
        while not self.projects_dir.exists():
            boost_event.clear()
            fast = time.monotonic() < self._fast_poll_until
            timeout = PROJECTS_DIR_POLL_INTERVAL_FAST if fast else PROJECTS_DIR_POLL_INTERVAL

            waiters = [
                asyncio.create_task(stop_event.wait()),
                asyncio.create_task(boost_event.wait()),
            ]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
            finally:
                for w in waiters:
                    w.cancel()
                for w in waiters:
                    try:
                        await w
                    except (asyncio.CancelledError, Exception):
                        pass

            if stop_event.is_set():
                return False
        return True

    async def start_watcher(self) -> None:
        """
        Start the file watcher for this provider's :attr:`projects_dir`.

        Monitors all changes recursively and dispatches to appropriate handlers.
        If the projects directory doesn't exist yet, polls until it appears
        (e.g. user hasn't used the provider yet). The poll interval drops from
        :data:`PROJECTS_DIR_POLL_INTERVAL` to
        :data:`PROJECTS_DIR_POLL_INTERVAL_FAST` after :meth:`request_fast_poll`
        is called — typically right before a session-start that's about to
        create the directory.
        """
        # Tag every log emitted from this watcher task with the provider —
        # the orchestrator's ``_create_task`` already sets the same tag on
        # the task's context, but doing it here too keeps a direct call
        # (e.g. a future helper that does not go through the orchestrator)
        # from leaking the watcher's logs into the global tag. The ContextVar
        # set is scoped to the asyncio.Task that runs this coroutine, so
        # it cannot leak into sibling tasks.
        current_provider.set(self.get_compute().provider.value)

        channel_layer = get_channel_layer()
        projects_dir = self.projects_dir
        stop_event = self.get_stop_event()
        # Reset the stop event so a hot-restart (provider toggled off then
        # back on) doesn't see the leftover ``set()`` from the previous
        # ``stop_watcher()`` call and exit on the first ``is_set()`` check.
        stop_event.clear()

        catch_up = False
        if not projects_dir.exists():
            logger.info(
                "Projects directory does not exist yet: %s — waiting for it to appear",
                projects_dir,
            )
            appeared = await self._wait_for_projects_dir()
            if not appeared:
                logger.info("Watcher stopped while waiting for projects directory")
                return
            logger.info("Projects directory appeared: %s", projects_dir)
            catch_up = True

        # Load project caches at startup
        await sync_to_async(load_project_directories)()
        await sync_to_async(load_project_git_roots)()

        logger.info(f"Starting file watcher on: {projects_dir}")

        if catch_up:
            await self._catch_up_existing_files(channel_layer)

        async for changes in awatch(projects_dir, stop_event=stop_event):
            for change_type, path_str in changes:
                await self._process_change(change_type, path_str, channel_layer)

    async def _catch_up_existing_files(self, channel_layer) -> None:
        """Process every session file already present under :attr:`projects_dir`.

        Only when the directory appeared *after* the watcher started waiting
        for it (a brand-new provider home — e.g. a relocated ``CLAUDE_CONFIG_DIR``
        / ``CODEX_HOME`` — or a first install): the provider creates the
        directory and writes the first session in one go, so by the time
        ``awatch`` is armed that file already exists and would only be seen at
        the next boot's initial sync (or at its next append: ``sync_and_broadcast``
        reads from ``last_offset``, so a later ``modified`` heals a missed
        ``added``). Each file goes through the regular ``added`` path.
        """
        projects_dir = self.projects_dir
        existing = await asyncio.to_thread(
            lambda: sorted(path for path in projects_dir.rglob("*.jsonl") if path.is_file())
        )
        if not existing:
            return
        logger.info("Catching up %d existing session file(s) under %s", len(existing), projects_dir)
        for path in existing:
            await self._process_change(Change.added, str(path), channel_layer)

    async def _process_change(self, change_type: Change, path_str: str, channel_layer) -> None:
        """Handle one filesystem change (the body of the watch loop; errors are logged, never raised)."""
        try:
            path = Path(path_str)

            # Provider-specific path patterns (e.g. project directory
            # creation/deletion for Claude Code). No-op for providers
            # that only care about jsonl files (e.g. Codex). Wrapped
            # under the DB write lock unconditionally — cheap when
            # the handler is a no-op, and a safety net so any future
            # override that writes is automatically serialised with
            # every other DB writer.
            handled = await run_under_db_write_lock(
                lambda p=path, ct=change_type, cl=channel_layer:
                    self.maybe_handle_special_change(p, ct, cl)
            )
            if handled:
                return

            # Skip non-jsonl files
            if not path_str.endswith(".jsonl"):
                return

            # Parse path to determine type (session or subagent).
            # Read-only (FS only, no DB) — runs outside the lock.
            parsed = await self.parse_session_file(path)
            if parsed is None:
                # Invalid path — silently skip
                return

            # Keep the provider context across title lookup, DB sync,
            # indexing, the search-version mark, and post-sync hooks.
            async with self.session_change_context(parsed):
                await self._process_parsed_session_change(
                    path, parsed, change_type, channel_layer,
                )
        except Exception:
            logger.exception("Error processing watcher change %s on %s", change_type, path_str)
