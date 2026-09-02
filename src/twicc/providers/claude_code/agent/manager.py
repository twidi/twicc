"""
Claude Code agent manager: tracks and manages all active Claude Code agents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings

from twicc.agent import (
    DELIBERATE_STOP_REASONS,
    AgentInfo,
    AgentState,
    BaseAgent,
    BaseAgentManager,
    SendDeliveryError,
)
from twicc.core.enums import Provider
from twicc.providers.db_writer import run_under_db_write_lock

from .agent import ClaudeCodeAgent

if TYPE_CHECKING:
    from datetime import datetime

    from twicc.providers.claude_code.agent.hybrid.signals import HybridHookOutcome, HybridJsonlSignals
    from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# Deaths that must NOT trigger the runtime cron restart: every deliberate stop
# (a human asked for it — see :data:`DELIBERATE_STOP_REASONS`) plus ``shutdown``
# (TwiCC is going down; the boot-time restart takes over on the next start).
#
# Redundant with :meth:`ClaudeCodeHelpers.should_keep_dead_process_run`, which
# already drops the ProcessRun row for the deliberate reasons and so leaves
# ``agent.process_run`` at ``None`` here. Kept explicit anyway: the two rules
# answer the same question ("may this session come back?") and must stay
# aligned, whichever one is read first.
_NO_CRON_RESTART_REASONS = DELIBERATE_STOP_REASONS | {"shutdown"}


def _get_session_slug_sync(session_id: str) -> str | None:
    """Retrieve the slug stored on the Session model (synchronous).

    Args:
        session_id: The session identifier to look up

    Returns:
        The slug string, or None if not set
    """
    from twicc.core.models import Session

    try:
        return Session.objects.values_list('slug', flat=True).get(id=session_id)
    except Session.DoesNotExist:
        return None


async def get_session_slug(session_id: str) -> str | None:
    """Async wrapper around _get_session_slug_sync.

    Runs the DB query in a thread to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_get_session_slug_sync, session_id)


class ClaudeCodeAgentManager(BaseAgentManager):
    """Manages all active Claude Code agents.

    Inherits the registry, lifecycle and timeout monitor from BaseAgentManager
    and adds Claude Code–specific behavior: settings hot-reload, cron lifecycle,
    title flushing, subagent propagation, and a separate cron-expiry monitor
    that runs alongside the timeout monitor.

    Typical usage:
        manager = ClaudeCodeAgentManager()

        # Send a message to an existing session
        await manager.send_to_session(session_id, project_id, cwd, "Hello")

        # Create a new session
        await manager.create_session(session_id, project_id, cwd, "Hello")

        # Get active agents
        agents = manager.get_active_agents()

        # Shutdown all
        await manager.shutdown()
    """

    provider: ClassVar[Provider] = Provider.CLAUDE_CODE

    # Cron expiry check cadence (seconds). Detects recurring crons auto-deleted
    # by the CLI after their 7-day window without forcing a tighter timeout loop.
    CRON_EXPIRY_MONITOR_INTERVAL = 60

    def __init__(self) -> None:
        super().__init__()
        self._cron_expiry_monitor_task: asyncio.Task[None] | None = None
        self._cron_restart_tasks: dict[str, asyncio.Task[None]] = {}  # session_id -> restart task
        # Pending content to send after a settings-triggered restart completes.
        # Stored when the user sends text + startup settings changes during USER_TURN:
        # the agent must be killed and restarted, and this content is sent after
        # cron restart (if any) or directly with the new agent.
        self._pending_after_restart: dict[str, dict] = {}  # session_id -> {text, images, documents}

    # ------------------------------------------------------------------
    # Public API (Claude Code–specific signatures)
    # ------------------------------------------------------------------

    async def send_to_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        cancel_cron_restart: bool = True,
    ) -> bool:
        """Send a message to an existing session, applying settings changes as needed.

        If no active agent exists for this session, a new one is created with
        the resume option to continue the existing conversation.

        Messages can be sent during USER_TURN (normal) or ASSISTANT_TURN
        (Claude Code reads them inline during work).

        Settings are classified into categories:
        - live (permission_mode): applied immediately in any state
        - idle (selected_model, context_max): applied during USER_TURN via set_model()
        - startup (effort, thinking_enabled, claude_in_chrome, fast_mode): require agent stop

        When startup settings change during USER_TURN, the agent is killed
        (reason="apply-settings") and restarted. During ASSISTANT_TURN, the
        changes are saved to DB and applied on the next USER_TURN transition.

        Returns:
            ``True`` when a message was synchronously accepted for delivery
            (so the WS layer can emit a delivery ack); ``False`` when no
            message was delivered now — settings-only update, or a
            restart-deferred send whose user_message line will confirm it.

        Raises:
            RuntimeError: If the agent cannot be started or message cannot be sent
        """
        from twicc.providers.helpers import AgentSettingCategory, get_provider_helpers

        provider_helpers = get_provider_helpers(Provider.CLAUDE_CODE)

        async with self._lock:
            # Cancel any running cron restart task — user is taking over this session.
            # Callers that ARE the cron restart task pass cancel_cron_restart=False
            # to avoid cancelling themselves.
            if cancel_cron_restart:
                self._cancel_cron_restart_task(session_id)

            has_content = bool(text) or bool(images) or bool(documents)

            if session_id in self._agents:
                agent = self._agents[session_id]

                if agent.state == AgentState.DEAD:
                    # Dead agent, clean it up and create a new one
                    logger.debug("Removing dead agent for session %s", session_id)
                    del self._agents[session_id]

                elif agent.state == AgentState.USER_TURN:
                    changes = provider_helpers.classify_agent_settings_changes(
                        agent.agent_settings, settings,
                        categories=self._settings_categories_for(agent),
                    )
                    has_startup_changes = bool(changes[AgentSettingCategory.STARTUP])

                    if has_startup_changes:
                        # Startup settings changed → must kill and restart
                        logger.info(
                            "Startup settings changed for session %s (%s), killing agent",
                            session_id, changes[AgentSettingCategory.STARTUP],
                        )
                        has_crons = await self._session_has_crons(agent)
                        will_restart = has_content or has_crons
                        if has_content:
                            self._pending_after_restart[session_id] = {
                                "text": text, "images": images, "documents": documents,
                            }
                        # _on_state_change(DEAD) fires once DEAD is reached:
                        # - if has_crons: launches cron restart task (which will
                        #   also send pending text after success)
                        # - cleans up agent from _agents
                        await agent.interrupt_or_kill(reason="apply-settings")
                        if will_restart:
                            # Broadcast "starting" so the frontend blocks interaction
                            await self._broadcast_agent_state(
                                session_id, project_id, AgentState.STARTING,
                            )
                        # If no crons and has content → start directly
                        if not has_crons and has_content:
                            pending = self._pending_after_restart.pop(session_id, None)
                            await self._start_agent(
                                session_id, project_id, cwd, pending["text"],
                                resume=True, settings=settings,
                                images=pending.get("images"),
                                documents=pending.get("documents"),
                            )
                        # If has_crons → cron restart task handles it.
                        # Delivery is deferred to after the restart, so this is
                        # not a synchronous ack — the message's user_message
                        # line (a fresh USER_TURN send) confirms it instead.
                        return False
                    else:
                        # Only live/idle changes → apply on the live agent
                        await agent.apply_live_settings(settings)
                        delivered = False
                        if has_content:
                            delivered = await agent.send(text, images=images, documents=documents)
                        return delivered

                elif agent.state == AgentState.ASSISTANT_TURN:
                    # During assistant_turn: apply live (permission) immediately,
                    # send text if any. Idle/startup changes are saved to DB by
                    # the caller and will be checked on next USER_TURN transition.
                    # Hybrid agents have no set_permission_mode (the mode is
                    # STARTUP there) — the change is picked up by
                    # _apply_pending_settings on the next USER_TURN.
                    if (
                        not getattr(agent, "is_hybrid", False)
                        and settings.permission_mode != agent.agent_settings.permission_mode
                    ):
                        await agent.set_permission_mode(settings.permission_mode)
                    delivered = False
                    if has_content:
                        delivered = await agent.send(text, images=images, documents=documents)
                    return delivered

                else:
                    # Agent starting - cannot send yet
                    raise SendDeliveryError(
                        f"Cannot send message: agent is in state {agent.state}",
                        code="agent_starting",
                    )

            # No live agent — a message is required to start one. Attachments
            # alone qualify: Claude Code accepts a user message made only of
            # image / document blocks.
            if not has_content:
                raise RuntimeError(
                    "Cannot start a new agent without a message"
                )

            # Create and start new agent with resume
            await self._start_agent(
                session_id, project_id, cwd, text, resume=True,
                settings=settings,
                images=images, documents=documents,
            )
            return True

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> str:
        """Create a new session with a client-provided session ID.

        Unlike send_to_session which handles existing sessions, this creates
        a brand new session. The session_id is passed to the Claude CLI via
        the --session-id flag.

        Returns the canonical session id. For Claude Code this is equal to
        the input ``session_id`` (the CLI accepts the client-supplied UUID
        via ``--session-id``).

        Raises:
            RuntimeError: If an agent already exists for this session_id
        """
        async with self._lock:
            if session_id in self._agents:
                agent = self._agents[session_id]
                if agent.state != AgentState.DEAD:
                    raise RuntimeError(
                        f"Session {session_id} already exists and is active"
                    )
                # Dead agent, clean it up
                logger.debug(
                    "Removing dead agent for session %s before creating new",
                    session_id,
                )
                del self._agents[session_id]

            # Create and start new agent without resume
            return await self._start_agent(
                session_id, project_id, cwd, text, resume=False,
                settings=settings,
                images=images, documents=documents,
            )

    async def discard_active_tool(self, session_id: str, tool_use_id: str) -> bool:
        """Discard an active-tool entry on the matching agent. Returns True
        when an entry was actually removed. Used by the JSONL watcher to clean
        up tools that never produced a PostToolUse/PostToolUseFailure (CLI-side
        validation rejections).
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return await agent.discard_active_tool(tool_use_id)

    async def stop_subagent(self, session_id: str, subagent_id: str) -> bool:
        """Stop a running subagent (Task) by its subagent ID.

        Verifies that the subagent belongs to the given session, then
        delegates to the running parent agent's ``stop_subagent``.

        Args:
            session_id: The parent session that spawned this subagent
            subagent_id: The subagent identifier (same as the SDK's task_id;
                this is the subagent ``Session`` row's primary key ``id``)

        Returns:
            True if the SDK stop call was issued, False if the subagent
            was not found or the parent agent is not active.
        """
        from twicc.core.models import Session

        # Verify the subagent exists and belongs to the given session
        exists = await asyncio.to_thread(
            lambda: Session.objects.filter(
                id=subagent_id, parent_session_id=session_id
            ).exists()
        )
        if not exists:
            logger.debug(
                "stop_subagent: no subagent %s found for session %s", subagent_id, session_id,
            )
            return False

        agent = self._agents.get(session_id)
        if not agent or agent.state == AgentState.DEAD:
            logger.debug("stop_subagent: no running agent for session %s", session_id)
            return False

        try:
            await agent.stop_subagent(subagent_id)
            return True
        except Exception as e:
            logger.warning(
                "stop_subagent: failed to stop subagent %s in session %s: %s",
                subagent_id, session_id, e,
            )
            return False

    # ------------------------------------------------------------------
    # Hybrid boot adoption
    # ------------------------------------------------------------------

    async def adopt_running_hybrid_sessions(self) -> None:
        """Adopt hybrid tmux sessions that survived a TwiCC restart.

        A clean shutdown now kills each hybrid tmux (best-effort, see
        ``HybridClaudeAgent.kill``), so a survivor here means the kill didn't
        land or TwiCC was hard-killed (SIGKILL/OOM) before reaching it. The
        dedicated hybrid socket (``HYBRID_TMUX_SOCKET_NAME``, per data dir —
        so this scan never sees, nor kills, another instance's hybrid CLIs)
        keeps such a claude running across the restart; we re-bind to it
        instead of losing the session. Called
        once at boot, after the stale-ProcessRun cleanup and BEFORE the
        hybrid-hooks watcher's boot scan, so a leftover PermissionRequest event
        of a still-pending prompt reaches its adopted agent.

        For each surviving tmux session: live pane + a ``hybrid=True``
        Session row with a project directory → adopt (register + attach,
        no relaunch, no paste). A dead pane or a row that is missing /
        not hybrid → the orphan tmux session is killed. A live claude
        whose project lost its directory is left untouched (never kill a
        live claude we merely cannot adopt).
        """
        from twicc.core.models import Session
        from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux
        from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
        from twicc.providers.helpers import AgentSettings, get_provider_helpers

        try:
            names = await asyncio.to_thread(hybrid_tmux.list_hybrid_sessions)
        except Exception:
            logger.exception("Hybrid boot adoption: tmux scan failed")
            return
        if not names:
            return

        provider_helpers = get_provider_helpers(Provider.CLAUDE_CODE)
        for name in names:
            # Session ids are UUIDs (no '.'/':' to sanitize), so the raw
            # suffix is the id itself.
            session_id = name[len(hybrid_tmux.HYBRID_SESSION_PREFIX):]
            try:
                pid, dead = await asyncio.to_thread(hybrid_tmux.pane_status, session_id)

                def _load(sid: str = session_id):
                    session = (
                        Session.objects.select_related("project")
                        .filter(id=sid)
                        .first()
                    )
                    if session is None or not session.hybrid:
                        return None
                    return (
                        session.project_id,
                        session.project.directory,
                        AgentSettings.from_session(session),
                    )

                row = await asyncio.to_thread(_load)
                if dead or pid is None or row is None:
                    logger.info(
                        "Killing orphan hybrid tmux session %s (pane_dead=%s, "
                        "known_hybrid_session=%s)", name, dead, row is not None,
                    )
                    await asyncio.to_thread(hybrid_tmux.kill_session, session_id)
                    continue
                project_id, directory, raw_settings = row
                if not directory:
                    logger.warning(
                        "Hybrid session %s survived but its project %s has no "
                        "directory — leaving the tmux session unadopted",
                        session_id, project_id,
                    )
                    continue
                settings = provider_helpers.enforce_agent_settings_consistency(
                    provider_helpers.resolve_agent_settings(raw_settings)
                )
                async with self._lock:
                    if session_id in self._agents:
                        continue
                    agent = HybridClaudeAgent(
                        session_id=session_id,
                        project_id=project_id,
                        cwd=directory,
                        agent_settings=settings,
                    )
                    await self._register_and_start(agent, "", resume=True, adopt=True)
                logger.info("Adopted surviving hybrid session %s", session_id)
            except Exception:
                logger.exception("Failed to adopt hybrid tmux session %s", name)

    # ------------------------------------------------------------------
    # Hybrid title application
    # ------------------------------------------------------------------

    async def rename_hybrid_if_live(self, session_id: str, title: str) -> bool:
        """Paste ``/rename <title>`` into a live hybrid TUI. Returns whether it was issued.

        ``False`` (no live hybrid agent, agent still STARTING — the booting
        TUI would swallow the paste — or paste failure) tells the caller to
        fall back to the direct JSONL write.
        """
        agent = self._agents.get(session_id)
        if (
            agent is None
            or not getattr(agent, "is_hybrid", False)
            or agent.state not in (AgentState.ASSISTANT_TURN, AgentState.USER_TURN)
        ):
            return False
        try:
            await agent.rename(title)
            logger.info("Pasted /rename into hybrid session %s", session_id)
            return True
        except Exception:
            logger.warning(
                "Hybrid /rename failed for session %s; falling back to the "
                "JSONL write", session_id, exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Hybrid signal routing (hooks drop watcher + JSONL bridge)
    # ------------------------------------------------------------------

    async def handle_hybrid_hook(
        self, session_id: str, event: str, payload: dict, nonce: str,
    ) -> HybridHookOutcome:
        """Route a hybrid hook event file to its live hybrid agent.

        Returns the file-ownership verdict for the hooks watcher:
        ``UNHANDLED`` for stale events (no live hybrid agent) and event
        names V1 does not inject (deleted, never retried); ``OWNED`` when
        the agent registered a pending request keyed on ``nonce`` (the drop
        file stays on disk until the pending resolves); ``HANDLED`` when
        the agent declined the registration (e.g. a stale drop re-fed after
        a restart, already answered in the TUI — deleted).
        """
        from twicc.providers.claude_code.agent.hybrid.signals import HybridHookOutcome

        agent = self._agents.get(session_id)
        if agent is None or not getattr(agent, "is_hybrid", False):
            return HybridHookOutcome.UNHANDLED
        if event == "PermissionRequest":
            registered = await agent.on_permission_request(payload, nonce)
            return HybridHookOutcome.OWNED if registered else HybridHookOutcome.HANDLED
        return HybridHookOutcome.UNHANDLED

    async def handle_hybrid_jsonl_signals(
        self, session_id: str, signals: HybridJsonlSignals,
    ) -> None:
        """Apply JSONL-derived state signals to a live hybrid agent.

        Order matters within a batch: turn starters first (user message,
        slash-command echo), then tool results (pending cleared), then turn
        enders (command ack, turn end) — so a batch carrying a whole fast
        turn settles on USER_TURN.
        """
        agent = self._agents.get(session_id)
        if agent is None or not getattr(agent, "is_hybrid", False):
            return
        if signals.user_message:
            await agent.on_jsonl_user_message()
        if signals.command_message:
            await agent.on_jsonl_command_message()
        if signals.tool_results:
            await agent.on_jsonl_progress()
        if signals.local_command_ack:
            await agent.on_jsonl_local_command_ack()
        if signals.turn_end:
            await agent.on_jsonl_turn_end()

    # ------------------------------------------------------------------
    # Factory hook (Claude Code agent construction)
    # ------------------------------------------------------------------

    @staticmethod
    def _settings_categories_for(agent: BaseAgent) -> dict | None:
        """Classification table override for hybrid agents (None = SDK default)."""
        if getattr(agent, "is_hybrid", False):
            from twicc.providers.claude_code.constants import HYBRID_AGENT_SETTINGS_CATEGORIES
            return HYBRID_AGENT_SETTINGS_CATEGORIES
        return None

    @staticmethod
    async def _session_is_hybrid(session_id: str) -> bool:
        """Hybrid flag for a session: DB row, else the pending-attributes buffer.

        Same dual lookup as the system-prompt addendum — a brand-new session
        has no row until the watcher ingests its first JSONL line.
        """
        from twicc.core.models import Session
        from twicc.pending_session_attributes import get_pending_session_attributes

        def _read() -> bool | None:
            return (
                Session.objects
                .filter(id=session_id)
                .values_list("hybrid", flat=True)
                .first()
            )

        hybrid = await asyncio.to_thread(_read)
        if hybrid is not None:
            return hybrid
        pending = get_pending_session_attributes(session_id)
        return bool(pending.hybrid) if pending else False

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        **kwargs: Any,
    ) -> BaseAgent:
        """Build the right agent flavor for the session (SDK or hybrid CLI).

        Claude Code's SDK accepts a client-supplied session id, so ``session_id``
        is the canonical id in both ``resume=True`` and ``resume=False`` cases.
        ``resume`` is therefore unused here (the resume vs new-session decision
        is handled by ``agent.start(..., resume=resume, ...)`` downstream).

        Every agent-construction path funnels through this factory
        (``_start_agent`` for sends/creates AND the cron restart flow), so the
        hybrid check covers them all. A hybrid session must NEVER be resumed
        through the SDK: the CLI-era messages would be invisible to it
        (one-way switch, see ``Session.hybrid``).
        """
        if await self._session_is_hybrid(session_id):
            # ``settings`` is the AgentSettings parameter here; reach Django
            # settings under an alias to honor the hybrid feature flag.
            from django.conf import settings as django_settings

            if not django_settings.CLAUDE_HYBRID_ENABLED:
                raise RuntimeError("Hybrid mode is disabled on this server")
            from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent

            return HybridClaudeAgent(
                session_id=session_id,
                project_id=project_id,
                cwd=cwd,
                agent_settings=settings,
            )
        return ClaudeCodeAgent(
            session_id=session_id,
            project_id=project_id,
            cwd=cwd,
            settings=settings,
            get_session_slug=get_session_slug,
            on_cron_created=self._on_cron_created,
            on_cron_deleted=self._on_cron_deleted,
        )

    # ------------------------------------------------------------------
    # Hooks (overrides of BaseAgentManager)
    # ------------------------------------------------------------------

    def _start_extra_monitors(self) -> None:
        """Launch the cron expiry monitor alongside the base timeout monitor."""
        if not settings.CRON_AUTO_RESTART:
            logger.debug("Cron expiry monitor skipped (TWICC_NO_CRON_RESTART is set)")
            return
        if self._cron_expiry_monitor_task is not None and not self._cron_expiry_monitor_task.done():
            return
        self._cron_expiry_monitor_task = asyncio.create_task(
            self._run_cron_expiry_monitor(),
            name="cron-expiry-monitor",
        )
        logger.debug("Started cron expiry monitor task")

    async def _stop_extra_monitors(self) -> None:
        """Cancel the cron expiry monitor."""
        if self._cron_expiry_monitor_task is not None:
            self._cron_expiry_monitor_task.cancel()
            try:
                await self._cron_expiry_monitor_task
            except asyncio.CancelledError:
                pass
            self._cron_expiry_monitor_task = None

    async def _pre_shutdown_extra(self) -> None:
        """Cancel runtime cron restart tasks before agents are interrupted.

        ProcessRuns stay in the DB so ``restart_all_session_crons`` can pick
        them up on the next TwiCC startup.
        """
        if self._cron_restart_tasks:
            logger.info("Cancelling %d cron restart task(s)", len(self._cron_restart_tasks))
            for session_id in list(self._cron_restart_tasks):
                self._cancel_cron_restart_task(session_id)

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Apply Claude Code-specific skips, then the shared per-state policy.

        Skips agents that have active crons (the CLI has scheduled work that
        would be lost if we kill the agent). The ``pending_requests`` skip
        lives in :meth:`BaseAgentManager._state_based_timeout` and is shared
        with every provider that calls into it.
        """
        # Don't timeout agents with active cron jobs.
        try:
            from twicc.core.models import SessionCron
            has_crons = await asyncio.to_thread(
                lambda sid=agent.session_id: SessionCron.has_active_for_session(sid, Provider.CLAUDE_CODE)
            )
            if has_crons:
                return None
        except Exception as e:
            logger.error(
                "Error checking active crons for session %s: %s",
                agent.session_id, e,
            )

        return self._state_based_timeout(agent, current_time)

    async def _update_session_stopped_at(self, agent: BaseAgent) -> None:
        """Override: also propagate ``last_stopped_at`` to subagents from this run.

        Replaces the base's plain ``Session.objects.update`` with a closure
        that, under a single DB write lock acquire, updates the agent's own
        ``last_stopped_at`` and then mirrors it onto every subagent that was
        started within the current run window (``last_started_at >=
        previous_cutoff``). The broadcasts run after the lock, one per
        updated session id. Every log emission goes through
        :func:`provider_log_context` so records carry the Claude Code
        provider tag (agent message loops do not set it themselves).
        """
        from django.utils import timezone

        from twicc.core.models import Session
        from twicc.logging_context import provider_log_context

        with provider_log_context(agent.provider):
            try:
                now = timezone.now()

                async def _persist_session_stopped_and_propagate() -> list[str]:
                    # Snapshot cutoff BEFORE updating so we can find subagents started in this run.
                    session = await asyncio.to_thread(Session.objects.filter(id=agent.session_id).first)
                    previous_cutoff = session.cutoff if session else None

                    await asyncio.to_thread(
                        lambda: Session.objects.filter(id=agent.session_id).update(
                            last_stopped_at=now, last_updated_at=now,
                        )
                    )

                    if session is None:
                        return []

                    subagent_filter = Session.objects.filter(parent_session_id=agent.session_id)
                    if previous_cutoff is not None:
                        subagent_filter = subagent_filter.filter(last_started_at__gte=previous_cutoff)
                    subagent_ids = await asyncio.to_thread(
                        lambda: list(subagent_filter.values_list('id', flat=True))
                    )
                    if subagent_ids:
                        await asyncio.to_thread(
                            lambda: Session.objects.filter(id__in=subagent_ids).update(
                                last_stopped_at=now, last_updated_at=now,
                            )
                        )
                    return subagent_ids

                subagent_ids = await run_under_db_write_lock(_persist_session_stopped_and_propagate)
                await self._broadcast_session_updated(agent.session_id)
                for subagent_id in subagent_ids:
                    await self._broadcast_session_updated(subagent_id)
            except Exception as e:
                logger.error(
                    "Error updating last_stopped_at for session %s: %s",
                    agent.session_id, e,
                )

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Handle state changes: titles, settings hot-reload, cron lifecycle.

        Concurrency model:
        This callback is invoked in two contexts:
        1. Synchronously from send_message() when start()/send() fails - the lock is
           already held by send_message(), so we must not try to acquire it again.
        2. Asynchronously from the message loop background task when Claude Code finishes
           or an error occurs - the lock is NOT held.

        For dead agent cleanup, we do NOT acquire the lock because:
        - In context 1, we'd deadlock (the lock is already held)
        - In context 2, the cleanup is effectively atomic in asyncio (no await between
          the check and delete operations, so no preemption)
        - The identity check (is agent) ensures we only delete the exact instance

        Flow: this override sandwiches :meth:`BaseAgentManager._on_state_change`
        between pre-broadcast cron-specific work (old run dedup) and
        post-broadcast / post-DEAD provider hooks (settings hot-reload, title
        flush, title protection rewrite, cron auto-restart). The base call
        handles persisting ``state`` + ``last_state_change_at`` on the
        :class:`ProcessRun` row, broadcasting the state change, running
        :meth:`_update_session_stopped_at` on DEAD (overridden above to
        propagate to subagents), invoking the helper's
        ``should_keep_dead_process_run`` to keep-or-delete the row, and
        cleaning up the agent from the registry. The auto-restart branch
        below reads ``agent.process_run is not None`` as the canonical
        "helper kept the row" signal — equivalent to "agent died with crons
        attached and is eligible for cron restart".
        """
        # Snapshot the state at entry. This is critical because _apply_pending_settings
        # may kill() the agent (changing agent.state to DEAD) while we're handling
        # a USER_TURN callback. Using the snapshot ensures each callback invocation only
        # runs the logic for the state it was called with.
        info = agent.get_info()
        state = info.state

        logger.debug(
            "State change callback triggered for session %s: state=%s",
            info.session_id, state.value,
        )

        # First USER_TURN: consolidate this session's ProcessRuns onto the current one.
        # Deleting a run cascades onto its crons, so the crons the resumed CLI re-armed
        # by itself are re-parented first — see reattach_crons_and_purge_old_runs.
        # Done BEFORE broadcasting so that _enrich_with_active_crons sees the correct cron count
        # (without this, crons from the old run + new run would both appear in the broadcast).
        # Uses _old_runs_purged flag because _first_user_turn_reached is already True at this point
        # (set in _run_message_loop before _notify_state_change is called).
        # Systematic for all agents — no-op when the session has no old process run.
        # Specific to Claude Code: ghost ProcessRuns only exist when a previous TwiCC instance
        # kept a row alive past death because it had crons attached, and the boot-time cron
        # restart then created a new ProcessRun for the relaunched session. The other provider
        # (Codex) has no cron concept and so can never accumulate ghost rows for the same id.
        if (
            state == AgentState.USER_TURN
            and not agent._old_runs_purged
            and agent.process_run is not None
        ):
            agent._old_runs_purged = True
            current_run_id = agent.process_run.pk
            try:
                from twicc.providers.claude_code.cron_restart import (
                    reattach_crons_and_purge_old_runs,
                )
                reattached, deleted_count = await run_under_db_write_lock(
                    lambda: asyncio.to_thread(
                        reattach_crons_and_purge_old_runs,
                        agent.session_id,
                        current_run_id,
                    )
                )
                if deleted_count:
                    logger.info(
                        "Purged %d old process run(s) for session %s (current: %s), "
                        "re-attached %d restored cron(s)",
                        deleted_count, agent.session_id, current_run_id, reattached,
                    )
            except Exception as e:
                logger.error("Error purging old process runs for session %s: %s", agent.session_id, e)

        # Base: persist ProcessRun state → broadcast → on DEAD: update_session_stopped_at
        # (overridden above) + helper-driven keep-or-delete + _cleanup_dead.
        await super()._on_state_change(agent)

        # --- Check for pending settings changes on USER_TURN transition ---
        # When settings are changed during ASSISTANT_TURN, they are saved to DB
        # but not applied to the agent. On each USER_TURN transition, we compare
        # DB settings with agent settings and apply/restart as needed.
        if state == AgentState.USER_TURN:
            try:
                await self._apply_pending_settings(agent)
            except Exception as e:
                logger.error("Error applying pending settings for session %s: %s", agent.session_id, e)

        # Pending-title flush on ASSISTANT_TURN is handled by
        # :meth:`BaseAgentManager._flush_pending_title` (provider-agnostic;
        # delegates the provider-specific write to
        # :meth:`ClaudeCodeHelpers.rename_session`, which appends the
        # custom-title JSONL entry and registers ``protect_title``).

        # Post-DEAD provider hooks: title protection rewrite + cron auto-restart.
        if state == AgentState.DEAD:
            from twicc.providers.claude_code.titles import clear_protected_title, get_protected_title, rename_session_in_jsonl

            # Re-write the user-set title at the end of the JSONL so the CLI's
            # tail-scan finds it on the next resume (survives server restarts).
            protected = get_protected_title(agent.session_id)
            if protected:
                try:
                    await asyncio.to_thread(rename_session_in_jsonl, agent.session_id, protected)
                except Exception as e:
                    logger.warning("Failed to re-write title on DEAD for session %s: %s", agent.session_id, e)

            clear_protected_title(agent.session_id)

            # Auto-restart crons for deaths nobody asked for.
            # ``agent.process_run is not None`` ⟹ the helper kept the row,
            # which for Claude Code means "this run still has active crons
            # to honour". Anything else (kept-then-deleted, deliberate stop,
            # early death before USER_TURN) clears the attribute via the
            # base's :meth:`_persist_process_run_transition`.
            if (
                agent.process_run is not None
                and agent.kill_reason not in _NO_CRON_RESTART_REASONS
                and settings.CRON_AUTO_RESTART
                and agent.session_id not in self._cron_restart_tasks
                and self._stop_event is not None
                and not self._stop_event.is_set()
            ):
                task = asyncio.create_task(
                    self._restart_crons_for_session(agent.session_id),
                    name=f"cron-restart-{agent.session_id}",
                )
                self._cron_restart_tasks[agent.session_id] = task
                logger.info(
                    "Launched runtime cron restart task for session %s (kill_reason=%s)",
                    agent.session_id, agent.kill_reason,
                )

    # ------------------------------------------------------------------
    # Cron expiry monitor
    # ------------------------------------------------------------------

    async def _run_cron_expiry_monitor(self) -> None:
        """Background task that detects recurring crons auto-deleted by the CLI after 7 days.

        Runs every 60 seconds. For each active agent, checks if any recurring crons
        have passed their computed expiry time (last_fire + jitter + margin).
        """
        logger.info("Cron expiry monitor started")

        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                await self._check_expired_crons()
            except Exception as e:
                logger.error("Error in cron expiry monitor: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.CRON_EXPIRY_MONITOR_INTERVAL,
                )
            except TimeoutError:
                pass

        logger.info("Cron expiry monitor stopped")

    async def _check_expired_crons(self) -> None:
        """Check all active agents for expired recurring crons.

        Only checks agents in USER_TURN state: this guarantees the last cron fire
        has completed (Claude is no longer working on the cron's prompt).

        When expired crons are found, they are deleted from the DB and a message is
        sent to Claude Code asking it to recreate them via CronCreate. The new crons will
        be persisted by the existing PostToolUse hooks.
        """
        from twicc.core.models import SessionCron
        from twicc.providers.claude_code.cron_restart import _build_renewal_message

        # Snapshot active agents (no lock needed — dict iteration is safe in asyncio)
        agents = list(self._agents.values())

        for agent in agents:
            if agent.state != AgentState.USER_TURN:
                continue
            try:
                expired = await asyncio.to_thread(agent.get_expired_recurring_crons)
                if not expired:
                    continue

                logger.info(
                    "Session %s has %d expired recurring cron(s): %s",
                    agent.session_id,
                    len(expired),
                    ", ".join(c.cron_id for c in expired),
                )

                # Build renewal message from expired crons data before deleting them.
                # Includes cron_id so Claude can delete the old CLI crons first.
                crons_data = [
                    {
                        "cron_id": c.cron_id,
                        "cron_expr": c.cron_expr,
                        "recurring": c.recurring,
                        "prompt": c.prompt,
                    }
                    for c in expired
                ]
                message = _build_renewal_message(crons_data)

                # Delete expired crons from DB (they're already dead in the CLI)
                expired_ids = [c.pk for c in expired]
                # ``ids=`` binds this iteration's list into the outer factory
                # rather than closing over the loop variable. The helper does
                # await the inner task to completion, but the binding keeps that
                # guarantee local to the call site. It must sit on the OUTER
                # lambda: the inner one is only built when the factory runs.
                await run_under_db_write_lock(
                    lambda ids=expired_ids: asyncio.to_thread(
                        lambda: SessionCron.objects.filter(pk__in=ids).delete()
                    )
                )
                logger.info(
                    "Deleted %d expired cron(s) from DB for session %s",
                    len(expired_ids), agent.session_id,
                )

                # Send message to Claude Code asking to recreate the crons
                try:
                    await agent.send(message)
                    logger.info(
                        "Sent cron renewal message to session %s for %d cron(s)",
                        agent.session_id, len(crons_data),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to send cron renewal message to session %s: %s",
                        agent.session_id, e,
                    )

            except Exception as e:
                logger.error(
                    "Error checking expired crons for session %s: %s",
                    agent.session_id, e,
                )

    # ------------------------------------------------------------------
    # Cron restart helpers
    # ------------------------------------------------------------------

    def _cancel_cron_restart_task(self, session_id: str) -> bool:
        """Cancel a running cron restart task for a session.

        Returns True if a task was cancelled, False if none existed.
        """
        task = self._cron_restart_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled cron restart task for session %s", session_id)
            return True
        return False

    async def _restart_crons_for_session(self, session_id: str) -> None:
        """Launch infinite cron restart loop for a session that died at runtime.

        Thin wrapper around restart_session_crons() that handles task lifecycle
        (CancelledError, cleanup from _cron_restart_tasks dict).

        After successful cron restart, sends any pending content that was queued
        during a settings-triggered restart (user sent text + startup settings changes).
        """
        from twicc.providers.claude_code.cron_restart import restart_session_crons

        RUNTIME_INITIAL_DELAY = 10  # seconds before first attempt (let API recover)

        try:
            await restart_session_crons(
                session_id,
                stop_event=self._stop_event,
                initial_delay=RUNTIME_INITIAL_DELAY,
            )
            # After successful cron restart, send pending content if any
            pending = self._pending_after_restart.pop(session_id, None)
            # Attachments alone are a message too — never gate on text only.
            if pending and (
                pending.get("text") or pending.get("images") or pending.get("documents")
            ):
                agent = self._agents.get(session_id)
                if agent and agent.state == AgentState.USER_TURN:
                    logger.info("Sending pending message after cron restart for session %s", session_id)
                    await agent.send(
                        pending["text"],
                        images=pending.get("images"),
                        documents=pending.get("documents"),
                    )
        except asyncio.CancelledError:
            logger.info("Cron restart task cancelled for session %s", session_id)
            raise
        except Exception as e:
            logger.error("Cron restart task failed for session %s: %s", session_id, e, exc_info=True)
        finally:
            self._cron_restart_tasks.pop(session_id, None)
            # Drop any queued pending content too: if we got here without
            # successfully sending it (cancel/error/no-USER_TURN), we cannot
            # send it later — keeping the entry around would just leak and
            # potentially overwrite a future legitimate queue.
            self._pending_after_restart.pop(session_id, None)

    # ------------------------------------------------------------------
    # Cron persistence callbacks (passed to ClaudeCodeAgent)
    # ------------------------------------------------------------------

    async def _on_cron_created(
        self,
        session_id: str,
        cron_id: str,
        cron_expr: str,
        recurring: bool,
        prompt: str,
        created_at: datetime,
        next_fire: datetime,
    ) -> None:
        """Persist a newly created cron job to the database and broadcast the update."""
        from twicc.core.enums import Provider
        from twicc.core.models import SessionCron

        # Associate cron with the current process run (if any)
        agent = self._agents.get(session_id)
        process_run = agent.process_run if agent else None

        await run_under_db_write_lock(
            lambda: asyncio.to_thread(
                lambda: SessionCron.objects.create(
                    provider=Provider.CLAUDE_CODE.value,
                    cron_id=cron_id,
                    session_id=session_id,
                    process_run=process_run,
                    cron_expr=cron_expr,
                    recurring=recurring,
                    prompt=prompt,
                    created_at=created_at,
                    next_fire=next_fire,
                )
            )
        )
        logger.info(
            "Persisted cron %s for session %s (process run: %s)",
            cron_id, session_id, process_run.pk if process_run else None,
        )
        await self._broadcast_agent_state(session_id)

    async def _on_cron_deleted(self, session_id: str, cron_id: str) -> None:
        """Delete a cron job from the database and broadcast the update."""
        from twicc.core.models import SessionCron

        deleted, _ = await run_under_db_write_lock(
            lambda: asyncio.to_thread(
                lambda: SessionCron.objects.filter(cron_id=cron_id).delete()
            )
        )
        if deleted:
            logger.info("Deleted cron %s for session %s", cron_id, session_id)
        else:
            logger.debug("Cron %s not found in DB for session %s (may already be deleted)", cron_id, session_id)
        await self._broadcast_agent_state(session_id)

    @staticmethod
    async def _session_has_crons(agent: ClaudeCodeAgent) -> bool:
        """Check if an agent has active crons (via its ProcessRun)."""
        if agent.process_run is None:
            return False
        return await asyncio.to_thread(lambda: agent.process_run.crons.exists())

    # ------------------------------------------------------------------
    # Synthetic state broadcast (for restart "starting" notifications)
    # ------------------------------------------------------------------

    async def _broadcast_agent_state(
        self,
        session_id: str,
        project_id: str | None = None,
        override_state: AgentState | None = None,
    ) -> None:
        """Trigger an agent state broadcast for a session.

        When override_state is provided, broadcasts a synthetic AgentInfo with
        that state (useful for broadcasting "starting" after a kill). Otherwise
        broadcasts the current agent state (used after cron changes).
        """
        if self._broadcast_callback is None:
            return
        try:
            if override_state is not None and project_id is not None:
                now = asyncio.get_event_loop().time()
                info = AgentInfo(
                    session_id=session_id,
                    project_id=project_id,
                    provider=ClaudeCodeAgent.provider,
                    state=override_state,
                    previous_state=None,
                    started_at=now,
                    state_changed_at=now,
                    last_activity=now,
                )
                await self._broadcast_callback(info)
            else:
                agent = self._agents.get(session_id)
                if agent:
                    await self._broadcast_callback(agent.get_info())
        except Exception as e:
            logger.error("Error broadcasting agent state for session %s: %s", session_id, e)

    # ------------------------------------------------------------------
    # Settings hot-reload
    # ------------------------------------------------------------------

    async def _apply_pending_settings(self, agent: ClaudeCodeAgent) -> None:
        """Check DB settings vs agent settings and apply/restart on USER_TURN.

        Called from _on_state_change when an agent transitions to USER_TURN.
        Compares the session's stored settings (potentially updated during
        ASSISTANT_TURN) with the agent's current settings.

        - Idle changes (model, context) → apply live via set_model()
        - Startup changes (effort, thinking, chrome) → kill + restart/cron restart
        - No changes → no-op
        """
        from twicc.core.models import Session
        from twicc.providers.helpers import AgentSettingCategory, AgentSettings, get_provider_helpers

        session = await asyncio.to_thread(
            Session.objects.filter(id=agent.session_id).first
        )
        if session is None:
            return

        provider_helpers = get_provider_helpers(Provider.CLAUDE_CODE)
        # Resolve effective settings (null → global synced default), then
        # enforce provider-specific capability rules
        requested_settings = provider_helpers.enforce_agent_settings_consistency(
            provider_helpers.resolve_agent_settings(
                AgentSettings.from_session(session),
            ),
        )

        changes = provider_helpers.classify_agent_settings_changes(
            agent.agent_settings, requested_settings,
            categories=self._settings_categories_for(agent),
        )
        if not any(changes.values()):
            return

        if changes[AgentSettingCategory.STARTUP]:
            # Startup settings changed → kill and let cron restart / pending handle it
            logger.info(
                "Pending startup settings for session %s (%s), killing agent on USER_TURN",
                agent.session_id, changes[AgentSettingCategory.STARTUP],
            )
            has_crons = await self._session_has_crons(agent)
            has_pending = agent.session_id in self._pending_after_restart
            will_restart = has_crons or has_pending
            await agent.interrupt_or_kill(reason="apply-settings")
            if will_restart:
                await self._broadcast_agent_state(
                    agent.session_id, agent.project_id, AgentState.STARTING,
                )
            # If no crons but has pending text → start directly
            if not has_crons and has_pending:
                pending = self._pending_after_restart.pop(agent.session_id)
                await self._start_agent(
                    agent.session_id, agent.project_id, agent.cwd,
                    pending["text"], resume=True, settings=requested_settings,
                    images=pending.get("images"), documents=pending.get("documents"),
                )
        else:
            # Only live/idle changes → apply via SDK methods
            logger.info(
                "Applying live/idle settings for session %s (live=%s, idle=%s) on USER_TURN",
                agent.session_id,
                changes[AgentSettingCategory.LIVE],
                changes[AgentSettingCategory.IDLE],
            )
            await agent.apply_live_settings(requested_settings)


def get_claude_code_agent_manager() -> ClaudeCodeAgentManager:
    """Return the Claude Code agent manager singleton from the registry.

    Thin convenience wrapper for Claude Code–specific call sites. The actual
    instance is owned by the global ``AgentManagerRegistry`` and shared with
    every provider-agnostic consumer.
    """
    # Lazy import to avoid an import cycle: the registry imports this module.
    from twicc.agent.registry import get_agent_manager_registry

    manager = get_agent_manager_registry().get(Provider.CLAUDE_CODE)
    return manager  # type: ignore[return-value]
