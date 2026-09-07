"""
Base agent manager for TwiCC.

Owns the registry of running agents, the timeout monitor, and the database
lifecycle bookkeeping (``ProcessRun`` rows, ``Session`` start/stop timestamps).
Provider-specific managers subclass this and plug in their factory and
optional hooks (state-change extras, timeout policy, extra monitors).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar

from asgiref.sync import sync_to_async

from twicc.logging_context import provider_log_context
from twicc.providers.db_writer import run_under_db_write_lock

from .base_agent import BaseAgent
from . import ephemeral as ephemeral_runs
from .states import AgentInfo, AgentState

if TYPE_CHECKING:
    from twicc.core.enums import Provider
    from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# Async callback used to push agent state to clients (typically over WebSocket).
BroadcastCallback = Callable[[AgentInfo], Coroutine[Any, Any, None]]


class BaseAgentManager:
    """Provider-agnostic manager for a fleet of agents.

    Subclasses must override ``_create_agent`` to build their provider's
    agent type and typically wrap it in a higher-level method
    (``send_to_session``, ``create_session``, ...) whose signature depends on
    the provider's settings shape.

    Subclasses must also set the ``provider`` class attribute to the matching
    :class:`twicc.core.enums.Provider` enum member — the manager's external
    entry points and background task bodies use it via
    :func:`provider_log_context` to tag every log record with the owning
    provider.
    """

    # Provider key (e.g. ``Provider.CLAUDE_CODE``). Subclasses must override.
    provider: ClassVar[Provider]

    # Interval (seconds) of the timeout monitor loop. Frequent enough to
    # catch short startup timeouts accurately without excessive churn.
    TIMEOUT_MONITOR_INTERVAL = 30

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._ephemeral_cleanup_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._broadcast_callback: BroadcastCallback | None = None
        self._timeout_monitor_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        # Background retry tasks for pending title flushes (one per session).
        # Each task keeps re-trying both steps of :meth:`_try_flush_pending_title`
        # (DB mirror + provider rename_session) until the session row exists
        # in our DB *and* the provider has accepted the rename. See
        # :meth:`_flush_pending_title`.
        self._pending_title_retry_tasks: dict[str, asyncio.Task[None]] = {}
        # One-shot post-flush verify tasks (one per session). After a
        # successful flush, a delayed task re-reads the title from the
        # provider's store and re-sets it if a background overwrite has
        # happened in the meantime (Codex's app-server can re-flush the
        # threads row from a derived name). Default no-op for providers
        # whose helper :meth:`BaseProviderHelpers.verify_session_title`
        # is not overridden — Claude Code is already covered by its
        # in-memory ``protect_title`` machinery.
        self._pending_title_verify_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Public API — generic for every provider
    # ------------------------------------------------------------------

    def is_ephemeral_id(self, session_id: str) -> bool:
        return ephemeral_runs.is_known(session_id)

    def _check_ephemeral_readonly(self, session_id: str, ephemeral_admission=None) -> None:
        ephemeral_runs.check_readonly(session_id, ephemeral_admission)

    def set_broadcast_callback(self, callback: BroadcastCallback) -> None:
        """Register the callback used to broadcast agent state changes."""
        self._broadcast_callback = callback

    def get_active_agents(self) -> list[AgentInfo]:
        """Return snapshots of all non-dead agents."""
        return [
            agent.get_info()
            for agent in self._agents.values()
            if agent.state != AgentState.DEAD
        ]

    def get_agent_info(self, session_id: str) -> AgentInfo | None:
        """Return a snapshot for a single agent, or ``None`` if unknown."""
        agent = self._agents.get(session_id)
        if agent is None:
            return None
        return agent.get_info()

    def touch_agent_activity(self, session_id: str) -> bool:
        """Refresh ``last_activity`` so the idle-timeout countdown resets.

        Useful when the user is preparing the next message (typing, attaching
        files): we don't want to auto-stop the agent during that pause.

        Returns ``True`` if the agent was found and updated.
        """
        with provider_log_context(self.provider):
            agent = self._agents.get(session_id)
            if agent is None:
                return False
            if agent.state not in (AgentState.USER_TURN, AgentState.ASSISTANT_TURN):
                return False
            agent.last_activity = time.time()
            logger.debug(
                "Touched last_activity for session %s (state=%s)",
                session_id, agent.state.value,
            )
            return True

    def set_session_hidden(self, session_id: str, hidden: bool) -> bool:
        """Push a ``Session.hidden`` flip into the live agent, if there is one.

        Keeps the agent's broadcast gate exact without making it poll the DB
        (see ``BaseAgent._is_session_hidden``). Returns ``True`` if an agent
        owned the id. No state filter: a hidden session must go quiet in every
        state, including mid-ASSISTANT_TURN, which is precisely when it is
        loudest.
        """
        with provider_log_context(self.provider):
            agent = self._agents.get(session_id)
            if agent is None:
                return False
            agent.set_hidden(hidden)
            logger.debug(
                "Pushed hidden=%s to the live agent for session %s",
                hidden, session_id,
            )
            return True

    async def kill_agent(self, session_id: str, reason: str = "manual") -> bool:
        """Stop one agent. Returns ``True`` if a kill was actually issued."""
        with provider_log_context(self.provider):
            async with self._lock:
                agent = self._agents.get(session_id)
                if agent is None:
                    logger.debug("kill_agent: session %s not found", session_id)
                    return False
                if agent.state == AgentState.DEAD:
                    logger.debug("kill_agent: session %s already dead", session_id)
                    return False
                logger.info(
                    "Stopping agent for session %s (reason: %s)", session_id, reason,
                )
                # Surface the in-flight stop before the (possibly long) kill:
                # ``interrupt_or_kill`` can block for up to ~30s (Claude Code
                # normal), so push the flag now for live clients, and keep it in
                # the agent's snapshot for any client that (re)connects meanwhile.
                # Safe under ``_lock``: ``_broadcast_info`` / ``_on_state_change``
                # never reacquire it.
                if agent.mark_stopping():
                    await self._broadcast_info(agent.get_info())
                await agent.interrupt_or_kill(reason=reason)
                return True

    async def hard_kill_agent(self, session_id: str, reason: str = "force") -> bool:
        """Hard-kill one agent: SIGKILL the OS process tree NOW, then finalize.

        A soft stop holds ``self._lock`` for up to ~30s (its grace window), so a
        hard kill must NOT wait on the lock. We read the pid (an atomic dict
        read), latch the force flag, and SIGKILL the process tree *out-of-lock*.
        That kills the process immediately AND unblocks any in-flight soft stop:
        its graceful wait races ``_force_kill`` and bails to its forced teardown.
        We then call :meth:`kill_agent` to finalize — it either no-ops (the soft
        stop already reached DEAD and freed the lock) or takes the now-free lock
        and runs the forced teardown (``interrupt_or_kill`` sees ``_force_kill``
        set and skips the grace window).
        """
        with provider_log_context(self.provider):
            agent = self._agents.get(session_id)
            if agent is None or agent.state == AgentState.DEAD:
                return False
            logger.info(
                "Hard-killing agent for session %s (reason: %s)", session_id, reason,
            )
            agent.kill_reason = reason
            agent.request_force_kill()
            if agent.mark_stopping():
                await self._broadcast_info(agent.get_info())
            pid = agent.get_pid()
            if pid is not None:
                await agent._kill_system_process(pid)
        # Finalize via the normal path (lock now free / freeing). No-ops if the
        # soft stop already transitioned the agent to DEAD.
        return await self.kill_agent(session_id, reason=reason)

    async def stop_subagent(self, session_id: str, subagent_id: str) -> bool:
        """Stop a running subagent (Task) within ``session_id``.

        Subagents are a provider-specific concept (e.g. Claude Code's
        Task tool spawns a subagent within a parent session). The base
        implementation is a no-op returning ``False`` so generic
        consumers (e.g. the WS handler) can call this without
        provider-specific dispatch. Providers that support subagents
        override.
        """
        return False

    async def interrupt_agent(self, session_id: str) -> bool:
        """Interrupt an agent's current turn WITHOUT killing the session.

        Sends the polite interrupt (see :meth:`BaseAgent.soft_interrupt`):
        aborts the in-flight turn and drops the agent back to ``USER_TURN``
        with its process kept alive, ready for the next message. Only
        meaningful while the agent is actually working (``ASSISTANT_TURN``) —
        a no-op in any other state. Generic across providers: agent runtimes
        that can't interrupt a turn in place inherit ``BaseAgent``'s ``False``
        no-op (Codex, hybrid Claude), so the request is silently dropped.

        Unlike :meth:`kill_agent` this deliberately does NOT take ``self._lock``:
        the soft interrupt is a fast, non-mutating control request, while a
        concurrent soft stop holds that lock for up to ~30s. Blocking an
        interrupt behind a teardown would be pointless — the kill wins anyway
        (the message loop checks its ``_interrupting`` flag before the
        soft-interrupt one).

        Returns ``True`` if an interrupt was issued, ``False`` if the session
        is unknown, not in ``ASSISTANT_TURN``, or its runtime can't interrupt.
        """
        with provider_log_context(self.provider):
            agent = self._agents.get(session_id)
            if agent is None:
                logger.debug("interrupt_agent: session %s not found", session_id)
                return False
            if agent.state != AgentState.ASSISTANT_TURN:
                logger.debug(
                    "interrupt_agent: session %s not in ASSISTANT_TURN (state=%s)",
                    session_id, agent.state.value,
                )
                return False
            return await agent.soft_interrupt()

    async def resolve_pending_request(
        self,
        session_id: str,
        request_id: str,
        response: Any,
    ) -> bool:
        """Resolve a specific pending request on an agent.

        Routes the user's response to the correct agent (by ``session_id``)
        and the correct in-flight Future on that agent (by ``request_id``).
        ``response`` is provider-specific; the caller (typically the WS
        handler) is responsible for shaping it correctly for the SDK that
        will receive it.

        Returns ``True`` if the request was resolved, ``False`` if no agent
        or no matching pending request was found.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return agent.resolve_pending_request(request_id, response)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Stop all agents and cancel monitors. Best-effort, time-bounded.

        After this call returns, the manager is reset to a state equivalent
        to a freshly constructed one (modulo provider-specific extras): the
        timeout monitor and stop event are cleared, and the agent registry
        is emptied. A subsequent ``_register_and_start`` would lazily restart
        the timeout monitor.
        """
        with provider_log_context(self.provider):
            if self._stop_event is not None:
                self._stop_event.set()

            if self._timeout_monitor_task is not None:
                self._timeout_monitor_task.cancel()
                try:
                    await self._timeout_monitor_task
                except asyncio.CancelledError:
                    pass
                self._timeout_monitor_task = None

            # Drop any background pending-title work — fire-and-forget cancel,
            # we don't await them. The DB row already holds the user's title,
            # so the worst case is a missing custom-title entry in the provider's
            # own store (the watcher's next resync may or may not recover it,
            # but the user-visible title is preserved).
            self._cancel_all_pending_title_retries()
            self._cancel_all_pending_title_verifies()

            await self._stop_extra_monitors()
            await self._pre_shutdown_extra()

            async with self._lock:
                if self._agents:
                    logger.info("Shutting down %d active agent(s)", len(self._agents))

                    agents_snapshot = list(self._agents.values())
                    shutdown_tasks = [
                        asyncio.create_task(
                            agent.interrupt_or_kill(reason="shutdown"),
                            name=f"shutdown-{agent.session_id}",
                        )
                        for agent in agents_snapshot
                    ]
                    if shutdown_tasks:
                        _, pending = await asyncio.wait(shutdown_tasks, timeout=timeout)
                        for task in pending:
                            task.cancel()

                    # Belt-and-suspenders: an ``interrupt_or_kill`` override is
                    # free to return as soon as the agent has reached the DEAD
                    # state, but the DEAD state-change callback may still be in
                    # flight afterwards — and that callback is where the
                    # lifecycle DB writes happen, under ``run_under_db_write_lock``.
                    # ``wait_for_dead`` blocks on both ``_dead_event`` and
                    # ``_dead_callback_done_event``, so this gather guarantees
                    # every callback's lock-protected writes have committed
                    # before we let the caller proceed to ``stop_db_writer()``.
                    wait_results = await asyncio.gather(
                        *[agent.wait_for_dead(timeout=timeout) for agent in agents_snapshot],
                        return_exceptions=True,
                    )
                    # Surface anything that timed out or raised — the caller
                    # (``run_server``) is about to call ``stop_db_writer()``,
                    # and a False/exception here means a DEAD callback's
                    # lock-protected writes may not have committed. We can't
                    # block forever (the user pressed Ctrl-C), but a log line
                    # is what makes the silent drop debuggable after the fact.
                    for agent, result in zip(agents_snapshot, wait_results):
                        if isinstance(result, BaseException):
                            logger.error(
                                "Waiting for DEAD callback of session %s raised during shutdown: %s",
                                agent.session_id, result, exc_info=result,
                            )
                        elif result is False:
                            logger.warning(
                                "DEAD callback for session %s did not finish within %.1fs "
                                "during shutdown — its lifecycle DB writes may have been dropped",
                                agent.session_id, timeout,
                            )

                    self._agents.clear()
                    logger.info("All agents shut down")

            # Kill tasks acquire the manager lock: drain only after releasing it.
            tasks = list(self._ephemeral_cleanup_tasks)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            ephemeral_runs.clear(self.provider.value)

            # Clear the stop event last so a future `_ensure_timeout_monitor_running`
            # creates a fresh one tied to the new lifecycle.
            self._stop_event = None

    # ------------------------------------------------------------------
    # Lifecycle helpers (called by subclasses)
    # ------------------------------------------------------------------

    async def _start_agent(
        self, session_id: str, project_id: str, cwd: str, text: str, resume: bool,
        *, settings: AgentSettings, ephemeral: bool = False, ephemeral_admission=None,
        **start_kwargs: Any,
    ) -> str:
        self._check_ephemeral_readonly(session_id, ephemeral_admission)
        if not resume and ephemeral_admission is None:
            ephemeral_admission = ephemeral_runs.reserve(session_id, self.provider.value, project_id, ephemeral=ephemeral)
        ids = [session_id]
        succeeded = False
        try:
            canonical_id = await self._start_agent_with_admission(
                session_id, project_id, cwd, text, resume, settings=settings,
                ephemeral=ephemeral, ephemeral_admission=ephemeral_admission,
                ephemeral_ids=ids, **start_kwargs,
            )
            succeeded = True
            return canonical_id
        finally:
            if ephemeral:
                ephemeral_runs.drain_buffers(*ids)
            await ephemeral_runs.finish(ephemeral_admission, failed=not succeeded)

    async def _start_agent_with_admission(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        resume: bool,
        *,
        settings: AgentSettings,
        ephemeral: bool = False,
        ephemeral_admission=None,
        ephemeral_ids: list[str],
        **start_kwargs: Any,
    ) -> str:
        """Build a provider agent, bind it to its canonical id, register and start.

        Common entry point for both new sessions (``resume=False``, the
        ``session_id`` argument is the frontend-side draft) and resumes
        (``resume=True``, ``session_id`` is the canonical id already known
        to the frontend).

        For brand-new sessions, the provider's ``_create_agent`` is expected
        to return an agent whose ``session_id`` is the canonical id. If the
        provider accepts a client-supplied id (Claude Code) the two are
        equal; if it mints its own (Codex) they differ — in either case,
        ``notify_session_bound`` tells the frontend which canonical id was
        bound to its local draft so it can reconcile its state.

        Provider-specific factory kwargs go through ``settings`` (universal)
        and ``_create_agent`` overrides. Provider-specific start kwargs
        (e.g. ``images``/``documents`` for Claude Code) are forwarded through
        ``start_kwargs`` to ``_register_and_start`` and ultimately to
        ``agent.start``.

        Must be called while holding ``self._lock``.
        """
        with provider_log_context(self.provider):
            label = "session" if resume else "draft session"
            logger.debug(
                "Creating agent for %s %s, project %s",
                label, session_id, project_id,
            )
            agent = await self._create_agent(
                session_id, project_id, cwd, resume=resume, settings=settings,
                **({"ephemeral": True} if ephemeral else {}),
            )

            # Once ``_create_agent`` returns, the agent owns external resources
            # (SDK client / subprocess). If any of the post-creation steps below
            # raise — pending re-keying, WS broadcast, DB writes in
            # ``_register_and_start``, ``agent.start`` itself — nothing else will
            # release them: the agent is at most in ``_agents`` but its state is
            # still ``STARTING``, so the DEAD-driven ``_cleanup_dead`` path never
            # fires. Tear it down explicitly via the provider's own
            # ``interrupt_or_kill`` (which is required to be safe on a not-yet-
            # started agent — see the docstring of ``_create_agent``).
            try:
                if ephemeral_admission is not None:
                    ephemeral_ids.append(agent.session_id)
                    ephemeral_runs.bind(ephemeral_admission, agent.session_id)
                if ephemeral:
                    agent.ephemeral_draft_id = session_id
                    from twicc.pending_titles import pop_pending_title
                    pop_pending_title(session_id)
                    pop_pending_title(agent.session_id)
                # Brand-new sessions: tell the frontend which canonical id is bound
                # to its local draft, so it can reconcile (redirect or discard).
                # On resume the frontend already knows the canonical id — skip it.
                if not resume:
                    # When the provider mints its own canonical id (Codex), the WS
                    # handler stored the pending agent settings under the draft id we
                    # received. Re-key them under the canonical id so the watcher
                    # pops them when it creates the Session row from the JSONL —
                    # otherwise selected_model / effort / ... stay NULL until the
                    # next user-initiated settings update. No-op when ids match
                    # (Claude Code): the existing pending entry is already under the
                    # canonical key.
                    if session_id != agent.session_id:
                        from twicc.pending_agent_settings import (
                            pop_pending_agent_settings,
                            set_pending_agent_settings,
                        )

                        pending = pop_pending_agent_settings(session_id)
                        if pending is not None:
                            set_pending_agent_settings(agent.session_id, pending)

                        # Same rationale for pending_titles: the WS handler stored it under
                        # the draft id we received; re-key under the canonical id so the
                        # Codex manager's ASSISTANT_TURN flush actually finds it. No-op for
                        # Claude Code where draft id == canonical id.
                        from twicc.pending_titles import (
                            pop_pending_title,
                            set_pending_title,
                        )

                        pending_title = pop_pending_title(session_id)
                        if pending_title is not None:
                            set_pending_title(agent.session_id, pending_title)

                        # Same rationale for pending_session_attributes (hidden,
                        # mute-on-user-turn, spawned links, system-prompt addendum,
                        # hybrid, dockable layout): the create-session service stashed
                        # them under the draft id, but the watcher pops them when it
                        # creates the row keyed on the canonical id Codex writes into
                        # the JSONL. Re-key here so the pop finds them — every field
                        # must be forwarded, or it silently reverts to its default (the
                        # draft's customized ``layout`` would drop to single pane).
                        from twicc.pending_session_attributes import (
                            pop_pending_session_attributes,
                            set_pending_session_attributes,
                        )

                        pending_attrs = pop_pending_session_attributes(session_id)
                        if pending_attrs is not None:
                            set_pending_session_attributes(
                                agent.session_id,
                                hidden=pending_attrs.hidden,
                                mute_on_user_turn=pending_attrs.mute_on_user_turn,
                                spawned_by_id=pending_attrs.spawned_by_id,
                                spawn_root_id=pending_attrs.spawn_root_id,
                                annotations=pending_attrs.annotations,
                                system_prompt_addendum=pending_attrs.system_prompt_addendum,
                                hybrid=pending_attrs.hybrid,
                                layout=pending_attrs.layout,
                                ephemeral=pending_attrs.ephemeral,
                            )

                    await self.notify_session_bound(
                        draft_session_id=session_id,
                        session_id=agent.session_id,
                    )

                await self._register_and_start(agent, text, resume=resume, **start_kwargs)
                return agent.session_id
            except BaseException:
                try:
                    await agent.interrupt_or_kill(reason="startup-failed")
                except Exception as cleanup_error:
                    if ephemeral:
                        logger.error("Ephemeral startup cleanup failed (%s)", type(cleanup_error).__name__)
                    else:
                        logger.exception(
                            "Cleanup interrupt_or_kill failed for session %s after start-up error",
                            agent.session_id,
                        )
                if self._agents.get(agent.session_id) is agent:
                    self._agents.pop(agent.session_id, None)
                    ephemeral_runs.agent_ended(agent.session_id)
                raise

    async def _register_and_start(
        self,
        agent: BaseAgent,
        text: str,
        resume: bool,
        **start_kwargs: Any,
    ) -> None:
        """Register an agent, persist its ``ProcessRun``, broadcast STARTING, start.

        Must be called while holding ``self._lock``. Failures inside
        ``agent.start`` should be reported via the DEAD state, not raised.

        By the time this method runs, ``agent.session_id`` is the canonical
        provider-side id (the draft → canonical resolution, if any, happens
        earlier in ``_start_agent``). Everything below operates on that id.
        """
        from django.utils import timezone

        from twicc.core.models import ProcessRun, Session

        session_id = agent.session_id
        self._agents[session_id] = agent
        ephemeral_runs.mark_registered(session_id)

        now = timezone.now()

        # Persist both DB writes (ProcessRun create + Session start-timestamps
        # update) under a single DB write lock acquire. The broadcasts that
        # follow stay outside the lock — they target the in-process Channels
        # layer and have no DB dependency. ``session_id`` is a plain
        # CharField on ProcessRun, so the create works even when no Session
        # row exists yet (the watcher creates the Session when the JSONL
        # file appears). The row is created with the model's default
        # ``state=STARTING`` and ``last_state_change_at=now``;
        # :meth:`_on_state_change` keeps both columns in sync from there.
        # ``twicc_pid`` always carries our own PID (stable for the lifetime
        # of this TwiCC process); ``agent_pid`` is whatever the provider can
        # surface right now via :meth:`BaseAgent.get_pid` — typically
        # ``None`` because ``agent.start()`` (the call that actually spawns
        # the subprocess) runs after this block.
        twicc_pid = os.getpid()
        agent_pid = agent.get_pid()

        async def _persist_run_and_start_timestamps() -> None:
            pr = await asyncio.to_thread(
                lambda: ProcessRun.objects.create(
                    provider=agent.provider.value,
                    session_id=session_id,
                    started_at=now,
                    last_state_change_at=now,
                    twicc_pid=twicc_pid,
                    agent_pid=agent_pid,
                )
            )
            agent.process_run = pr
            await asyncio.to_thread(
                lambda: Session.objects.filter(id=session_id).update(
                    last_started_at=now, last_updated_at=now,
                )
            )

        if not getattr(agent, "ephemeral", False):
            await run_under_db_write_lock(_persist_run_and_start_timestamps)
            await self._broadcast_session_updated(session_id)

        # Initial STARTING broadcast (state was set to STARTING in __init__).
        await self._on_state_change(agent)

        await agent.start(text, self._on_state_change, resume=resume, **start_kwargs)

        self._ensure_timeout_monitor_running()

    # ------------------------------------------------------------------
    # State-change skeleton
    # ------------------------------------------------------------------

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Default skeleton: persist ProcessRun state → broadcast → DB lifecycle → registry cleanup.

        Subclasses typically override this to insert provider-specific work
        before/after broadcast (titles, settings hot-reload, ...) while still
        delegating the generic bits to the helpers below.

        The ProcessRun transition is persisted **before** the broadcast: clients
        that consult the DB right after receiving a state-change message
        observe a row already in the new state. On ``DEAD``, the row is
        either UPDATEd (when the provider helper says to keep it — Claude
        Code with crons attached) or DELETEd (default for every other case);
        ``agent.process_run`` is cleared on delete so override post-DEAD
        logic can detect "row was kept" via ``agent.process_run is not None``.

        On ``ASSISTANT_TURN``, :meth:`_flush_pending_title` runs to push any
        title the CLI / WS stored for a draft session into the DB and the
        provider's own store. Provider-agnostic — both Claude Code and Codex
        share the same draft-title bridge (:mod:`twicc.pending_titles`).
        """
        info = agent.get_info()
        if not getattr(agent, "ephemeral", False):
            await self._persist_process_run_transition(agent, info.state)
        await self._broadcast_info(info)
        if info.state == AgentState.DEAD:
            # Cancel any background pending-title work, if any: once the
            # agent is gone there's nothing left to converge on the provider
            # side; ``Session.title`` already holds the value (or never will
            # if the flush never succeeded — in which case the title was
            # already lost regardless of the verify).
            self._cancel_pending_title_retry(agent.session_id)
            self._cancel_pending_title_verify(agent.session_id)
            if not getattr(agent, "ephemeral", False):
                await self._update_session_stopped_at(agent)
            self._cleanup_dead(agent)
        elif info.state == AgentState.ASSISTANT_TURN:
            # Wake the paused usage sync loops: an agent starting real work may
            # want fresh quota data (e.g. an orchestrator checking usage) even
            # with no human present. No-op when the loops aren't paused.
            from twicc.usage_task import note_activity
            note_activity()
            if not getattr(agent, "ephemeral", False):
                await self._flush_pending_title(agent)
        if getattr(agent, "ephemeral", False):
            await self._complete_ephemeral(agent, info.state)

    async def _complete_ephemeral(self, agent: BaseAgent, state: AgentState) -> None:
        if agent.ephemeral_result_emitted or state not in (AgentState.USER_TURN, AgentState.DEAD):
            return
        if state == AgentState.DEAD and agent.kill_reason == "shutdown":
            return
        from datetime import datetime, UTC
        from channels.layers import get_channel_layer
        from .states import DELIBERATE_STOP_REASONS

        agent.ephemeral_result_emitted = True
        if state == AgentState.USER_TURN:
            status = "stopped" if agent.ephemeral_soft_interrupted else "done"
        else:
            status = "stopped" if agent.kill_reason in DELIBERATE_STOP_REASONS else "error"
        frame = {
            "type": "ephemeral_result", "session_id": agent.session_id,
            "project_id": agent.project_id, "provider": agent.provider.value,
            "status": status, "text": agent.ephemeral_final_text or "",
            "error": (agent.error or agent.kill_reason) if status == "error" else None,
            "cost_usd": agent.ephemeral_usage.get("cost_usd"),
            "duration_ms": agent.ephemeral_usage.get("duration_ms"),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        try:
            layer = get_channel_layer()
            if layer is not None:
                await layer.group_send("updates", {"type": "broadcast", "data": frame})
        finally:
            if state == AgentState.USER_TURN:
                task = asyncio.create_task(self.kill_agent(agent.session_id, reason="ephemeral-done"))
                self._ephemeral_cleanup_tasks.add(task)
                task.add_done_callback(self._ephemeral_cleanup_done)

    def _ephemeral_cleanup_done(self, task: asyncio.Task) -> None:
        self._ephemeral_cleanup_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Ephemeral agent cleanup failed (%s)", type(task.exception()).__name__)

    async def _flush_pending_title(self, agent: BaseAgent) -> None:
        """Persist any pending session title once the agent reaches ASSISTANT_TURN.

        Draft sessions (CLI ``--title`` or WS ``send_message`` with a custom
        title) stash the chosen title in :mod:`twicc.pending_titles` because
        the provider's backing store does not exist yet at draft time. The
        first ``ASSISTANT_TURN`` is the contractual moment to drain it: the
        agent has signalled first activity, the WS clients have already seen
        ``serialize_session`` resolve the pending title for the frontend, and
        we now want the title to live in two stores at rest.

        The actual two-step flush is :meth:`_try_flush_pending_title` — see
        its docstring for what each step does and why both can fail. This
        method only orchestrates: inline attempt, then a background retry
        loop on failure, then a one-shot post-success verify task.

        Idempotency: if a background retry is already in flight for this
        session, the method is a no-op (the running loop owns the pop).
        """
        from twicc.pending_titles import get_pending_title

        pending = get_pending_title(agent.session_id)
        if not pending:
            return

        session_id = agent.session_id

        # A background loop is already converging both stores — don't fire
        # a parallel attempt.
        if session_id in self._pending_title_retry_tasks:
            return

        if await self._try_flush_pending_title(agent, pending):
            await self._on_flush_success(agent, pending)
            return

        # Inline attempt failed — schedule background retry.
        task = asyncio.create_task(
            self._retry_flush_pending_title(agent, pending),
            name=f"pending-title-retry-{session_id}",
        )
        self._pending_title_retry_tasks[session_id] = task

    async def _try_flush_pending_title(self, agent: BaseAgent, pending: str) -> bool:
        """Single attempt at the two-step pending title flush.

        Returns ``True`` only when **both** steps succeed:

        1. ``Session.title`` in the DB — what
           :func:`twicc.core.serializers.serialize_session` falls back to once
           the pending entry is gone, and what ``twicc session <id>`` reads
           on the CLI. The ``.update()`` return value is checked: ``0`` rows
           updated means the Session row does not exist yet (typical race for
           Codex, whose ASSISTANT_TURN fires before the DropRequestsWatcher
           has created the row) and is reported as failure.
        2. The provider's own store via
           :meth:`BaseProviderHelpers.rename_session` — for Claude Code, a
           ``custom-title`` JSONL entry plus :func:`protect_title` against
           CLI re-appends; for Codex, a ``thread/name/set`` call to the app
           server. May fail transiently: Claude Code's JSONL may not yet
           exist (SDK emits ASSISTANT_TURN before writing the file), or the
           Codex app server may be momentarily unreachable.

        Idempotent — re-tries of this method either succeed cleanly (UPDATE
        with the same value is a no-op for the DB; the provider rename APIs
        accept repeated calls). ``protect_title`` (Claude Code) is registered
        on every call thanks to the ``finally`` inside
        :meth:`ClaudeCodeHelpers.rename_session`, so even failing attempts
        leave the in-memory protection in place.
        """
        from twicc.core.models import Session
        from twicc.providers.helpers import get_provider_helpers

        session_id = agent.session_id

        try:
            async def _persist_session_title() -> int:
                return await sync_to_async(
                    Session.objects.filter(id=session_id).update
                )(title=pending)
            rows_updated = await run_under_db_write_lock(_persist_session_title)
        except Exception as e:
            logger.warning(
                "Pending title flush — DB update raised for session %s: %s",
                session_id, e,
            )
            return False

        if rows_updated == 0:
            logger.debug(
                "Pending title flush — DB UPDATE matched 0 rows for session %s "
                "(row not yet created — retrying)",
                session_id,
            )
            return False

        try:
            helpers = get_provider_helpers(agent.provider)
            await helpers.rename_session(session_id, pending)
        except Exception as e:
            logger.warning(
                "Pending title flush — provider rename_session failed for session %s: %s",
                session_id, e,
            )
            return False

        return True

    async def _on_flush_success(self, agent: BaseAgent, pending: str) -> None:
        """Common post-success hook: pop the pending entry and schedule a verify task.

        Pops the in-memory pending bridge so subsequent ``serialize_session``
        calls fall through to the DB row (now correct). Then schedules a
        delayed :meth:`_verify_pending_title_after_delay` task to guard
        against silent background overwrites — useful for providers whose
        process can re-flush its own row after our explicit set (Codex).
        For providers that don't need it (Claude Code: ``protect_title``
        covers the equivalent), the helper's ``verify_session_title`` is a
        no-op and the task exits cheaply.
        """
        from twicc.pending_titles import pop_pending_title

        session_id = agent.session_id
        pop_pending_title(session_id)

        # Replace any in-flight verify task so the new delay window starts
        # from this success (in particular when a retry succeeded after an
        # earlier inline-success verify was scheduled).
        self._cancel_pending_title_verify(session_id)
        task = asyncio.create_task(
            self._verify_pending_title_after_delay(agent.provider, session_id, pending),
            name=f"pending-title-verify-{session_id}",
        )
        self._pending_title_verify_tasks[session_id] = task

    async def _retry_flush_pending_title(self, agent: BaseAgent, pending: str) -> None:
        """Background retry of :meth:`_try_flush_pending_title` with backoff.

        Sleeps ``backoff`` seconds, attempts the full flush, repeats on
        failure with ``backoff = min(backoff * 2, 30)``. Calls
        :meth:`_on_flush_success` on success and exits. On cancellation
        (DEAD / shutdown), exits without popping — by then the agent is
        gone and there is nothing left to converge to.

        Retry attempts are logged at DEBUG to avoid spamming a long-running
        loop; success is logged at INFO so it shows up in normal logs.

        Self-clean-up: removes itself from ``_pending_title_retry_tasks`` on
        every exit path via a ``finally``. The :meth:`_cancel_pending_title_retry`
        helper also pops eagerly so the dict never references a cancelled task.
        """
        with provider_log_context(self.provider):
            backoff = 1.0
            max_backoff = 30.0
            # The inline attempt in _flush_pending_title was attempt #1.
            attempt = 1

            try:
                while True:
                    await asyncio.sleep(backoff)
                    attempt += 1
                    if await self._try_flush_pending_title(agent, pending):
                        await self._on_flush_success(agent, pending)
                        logger.info(
                            "Pending title flush retry succeeded for session %s on attempt %d",
                            agent.session_id, attempt,
                        )
                        return
                    backoff = min(backoff * 2, max_backoff)
            finally:
                self._pending_title_retry_tasks.pop(agent.session_id, None)

    async def _verify_pending_title_after_delay(
        self, provider: Provider, session_id: str, expected_title: str,
    ) -> None:
        """Sleep, then verify the title is still the user's value on the provider side.

        One-shot, fire-and-forget. After the delay, delegates to
        :meth:`BaseProviderHelpers.verify_session_title`, which reads back
        and re-sets the title if a background overwrite has occurred.

        The delay (5 seconds) gives the provider's binary time to finish any
        flush of its own state that runs in parallel with our explicit
        rename — most notably Codex, whose app-server can re-flush the
        ``threads.title`` column from an in-memory value derived from
        ``first_user_message`` shortly after our ``thread/name/set``.
        """
        from twicc.providers.helpers import get_provider_helpers

        with provider_log_context(self.provider):
            delay = 5.0
            try:
                await asyncio.sleep(delay)
                helpers = get_provider_helpers(provider)
                await helpers.verify_session_title(session_id, expected_title)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "Pending title verify failed for session %s: %s",
                    session_id, e,
                )
            finally:
                self._pending_title_verify_tasks.pop(session_id, None)

    def _cancel_pending_title_retry(self, session_id: str) -> None:
        """Cancel a single session's background pending-title retry, if any."""
        task = self._pending_title_retry_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_pending_title_retries(self) -> None:
        """Cancel every running pending-title retry. Used on manager shutdown."""
        if not self._pending_title_retry_tasks:
            return
        logger.info(
            "Cancelling %d pending-title retry task(s)",
            len(self._pending_title_retry_tasks),
        )
        for session_id in list(self._pending_title_retry_tasks):
            self._cancel_pending_title_retry(session_id)

    def _cancel_pending_title_verify(self, session_id: str) -> None:
        """Cancel a single session's delayed pending-title verify, if any."""
        task = self._pending_title_verify_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_all_pending_title_verifies(self) -> None:
        """Cancel every running pending-title verify. Used on manager shutdown."""
        if not self._pending_title_verify_tasks:
            return
        logger.info(
            "Cancelling %d pending-title verify task(s)",
            len(self._pending_title_verify_tasks),
        )
        for session_id in list(self._pending_title_verify_tasks):
            self._cancel_pending_title_verify(session_id)

    async def _broadcast_info(self, info: AgentInfo) -> None:
        """Push an ``AgentInfo`` snapshot through the broadcast callback."""
        if self._broadcast_callback is None:
            return
        try:
            await self._broadcast_callback(info)
        except Exception as e:
            logger.error("Error broadcasting state change: %s", type(e).__name__ if info.extra and info.extra.get("ephemeral") else e)

    async def _persist_process_run_transition(
        self, agent: BaseAgent, state: AgentState,
    ) -> None:
        """Mirror a runtime state transition onto the agent's ProcessRun row.

        For non-``DEAD`` transitions: UPDATE ``state``,
        ``last_state_change_at`` and ``agent_pid``. For ``DEAD``: consult
        the provider helper — if it returns ``True``, UPDATE the row to
        ``state=DEAD`` (same triplet); otherwise DELETE the row (and clear
        ``agent.process_run`` so override-level post-DEAD logic can branch
        on whether the row was kept).

        ``agent_pid`` is refreshed on every UPDATE because
        :meth:`BaseAgent.get_pid` returns ``None`` until the provider has
        actually spawned its subprocess — which only happens inside
        ``agent.start()``, after ``ProcessRun.objects.create`` has already
        run. The first transition out of ``STARTING`` is therefore the
        earliest moment we can persist a usable value. On ``DEAD`` the
        subprocess is gone again, so ``get_pid()`` flips back to ``None``
        and the column reflects that.

        ``awaiting_user_input`` mirrors ``bool(agent.pending_requests)``,
        forced to ``False`` on ``DEAD`` (a dead agent is not awaiting
        anything, even if its pending-requests dict still has entries that
        will be drained in the cancellation cleanup). The column is the
        persistable view of "blocked on user click", since the runtime
        ``state`` stays in ``ASSISTANT_TURN`` while the SDK's
        ``can_use_tool`` callback blocks. This persist site fires both on
        explicit state transitions and on pending-request add/remove,
        because :meth:`BaseAgent._await_pending_request` invokes
        ``_notify_state_change`` at both moments.

        The helper read + write are grouped under a single
        ``run_under_db_write_lock`` acquire so no other writer can race
        between the keep/delete decision and the DB mutation (mirrors the
        pattern previously used in Claude Code's ``_on_state_change``).
        Every log emission goes through :func:`provider_log_context` so
        records carry the agent's provider tag — agent message loops do
        not set the context themselves, so wrapping it here is the
        canonical attribution point. No-op when the agent has no
        process_run attached (early failure before ``_register_and_start``
        completed).
        """
        if agent.process_run is None:
            return

        from django.utils import timezone

        from twicc.core.models import ProcessRun
        from twicc.logging_context import provider_log_context
        from twicc.providers.helpers import get_provider_helpers

        now = timezone.now()
        pr_pk = agent.process_run.pk
        state_value = state.value
        agent_pid = agent.get_pid()
        # ``awaiting_user_input`` is the persistable counterpart of
        # ``agent.pending_requests``. Force ``False`` on DEAD: the pending
        # requests dict may still hold entries at the moment the DEAD
        # callback runs (cleanup happens in the ``finally`` of
        # ``_await_pending_request`` AFTER the future is cancelled), but a
        # dead agent is not actually awaiting anything — the column reflects
        # the operational truth.
        awaiting = False if state == AgentState.DEAD else bool(agent.pending_requests)

        with provider_log_context(agent.provider):
            if state != AgentState.DEAD:
                async def _persist_update() -> None:
                    await asyncio.to_thread(
                        lambda: ProcessRun.objects.filter(pk=pr_pk).update(
                            state=state_value,
                            last_state_change_at=now,
                            agent_pid=agent_pid,
                            awaiting_user_input=awaiting,
                        )
                    )
                    if agent.process_run is not None:
                        agent.process_run.state = state_value
                        agent.process_run.last_state_change_at = now
                        agent.process_run.agent_pid = agent_pid
                        agent.process_run.awaiting_user_input = awaiting

                try:
                    await run_under_db_write_lock(_persist_update)
                except Exception as e:
                    logger.error(
                        "Error persisting state %s on process run %s for session %s: %s",
                        state_value, pr_pk, agent.session_id, e,
                    )
                return

            # DEAD: helper decides keep vs delete. Helper is sync (typically
            # a DB read); wrapped in ``to_thread`` so the event loop stays free.
            helper = get_provider_helpers(agent.provider)

            async def _settle_dead() -> None:
                keep = await asyncio.to_thread(
                    lambda: helper.should_keep_dead_process_run(
                        agent.process_run, agent=agent,
                    )
                )
                if keep:
                    await asyncio.to_thread(
                        lambda: ProcessRun.objects.filter(pk=pr_pk).update(
                            state=state_value,
                            last_state_change_at=now,
                            agent_pid=agent_pid,
                            awaiting_user_input=awaiting,
                        )
                    )
                    if agent.process_run is not None:
                        agent.process_run.state = state_value
                        agent.process_run.last_state_change_at = now
                        agent.process_run.agent_pid = agent_pid
                        agent.process_run.awaiting_user_input = awaiting
                else:
                    await asyncio.to_thread(lambda: agent.process_run.delete())
                    agent.process_run = None
                    logger.info(
                        "Deleted process run %s for session %s on death",
                        pr_pk, agent.session_id,
                    )

            try:
                await run_under_db_write_lock(_settle_dead)
            except Exception as e:
                logger.error(
                    "Error settling DEAD process run %s for session %s: %s",
                    pr_pk, agent.session_id, e,
                )

    async def _update_session_stopped_at(self, agent: BaseAgent) -> None:
        """Update ``Session.last_stopped_at`` and broadcast ``session_updated``."""
        from django.utils import timezone

        from twicc.core.models import Session

        try:
            now = timezone.now()

            async def _persist_stopped() -> None:
                await asyncio.to_thread(
                    lambda: Session.objects.filter(id=agent.session_id).update(
                        last_stopped_at=now, last_updated_at=now,
                    )
                )

            await run_under_db_write_lock(_persist_stopped)
            await self._broadcast_session_updated(agent.session_id)
        except Exception as e:
            logger.error(
                "Error updating last_stopped_at for session %s: %s",
                agent.session_id, e,
            )

    def _cleanup_dead(self, agent: BaseAgent) -> None:
        """Remove a dead agent from the registry (identity-checked)."""
        if (
            agent.session_id in self._agents
            and self._agents[agent.session_id] is agent
        ):
            logger.debug(
                "Cleaning up dead agent for session %s", agent.session_id,
            )
            del self._agents[agent.session_id]
            ephemeral_runs.agent_ended(agent.session_id)

    async def _broadcast_session_updated(self, session_id: str) -> None:
        """Push a ``session_updated`` message via WebSocket.

        No-op when the ``Session`` row does not exist yet. That's the
        expected state for brand-new sessions between ``_register_and_start``
        and the moment the watcher inserts the row from the first JSONL
        line — the watcher then broadcasts its own ``session_updated`` so
        nothing is lost. Resume / DEAD call sites observe an existing
        row and broadcast normally.
        """
        from channels.layers import get_channel_layer

        from twicc.core.models import Session
        from twicc.core.serializers import serialize_session

        try:
            session = await asyncio.to_thread(
                lambda: Session.objects.filter(id=session_id).first()
            )
            if session is None:
                return
            if session.hidden:
                return
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
        except Exception as e:
            logger.error(
                "Error broadcasting session_updated for %s: %s", session_id, e,
            )

    async def notify_session_bound(
        self, draft_session_id: str, session_id: str,
    ) -> None:
        """Broadcast the canonical session id bound to a local draft.

        The frontend mints a ``draft_session_id`` (UUID) when the user starts a
        new conversation and uses it locally — store key, URL, IndexedDB draft.
        Providers that accept a client-supplied id (Claude Code) reuse it as
        the canonical id; providers that mint their own (Codex) return a
        different id. This broadcast tells the frontend which canonical id is
        now bound to the draft so it can reconcile its local state: redirect
        ``/sessions/{draft_session_id}`` to ``/sessions/{session_id}`` if the
        user is still on the draft, or just discard the local draft otherwise.

        Called once per new session, as early as the provider can confirm the
        canonical id. Not called on resume (the frontend already knows the id).
        When ``draft_session_id == session_id`` the frontend treats it as a
        no-op (the existing ``session_updated`` path upgrades the draft in
        place).
        """
        from channels.layers import get_channel_layer

        with provider_log_context(self.provider):
            channel_layer = get_channel_layer()
            try:
                await channel_layer.group_send(
                    "updates",
                    {
                        "type": "broadcast",
                        "data": {
                            "type": "session_bound",
                            "draft_session_id": draft_session_id,
                            "session_id": session_id,
                        },
                    },
                )
            except Exception as e:
                logger.error(
                    "Error broadcasting session_bound for draft=%s, session=%s: %s",
                    draft_session_id, session_id, e,
                )

    # ------------------------------------------------------------------
    # Timeout monitor
    # ------------------------------------------------------------------

    def _ensure_timeout_monitor_running(self) -> None:
        """Start the timeout monitor (and provider-extra monitors) if idle."""
        if self._timeout_monitor_task is not None and not self._timeout_monitor_task.done():
            return
        self._stop_event = asyncio.Event()
        self._timeout_monitor_task = asyncio.create_task(
            self._run_timeout_monitor(),
            name="agent-timeout-monitor",
        )
        logger.debug("Started agent timeout monitor")

        self._start_extra_monitors()

    async def _run_timeout_monitor(self) -> None:
        """Periodically check every active agent against its timeout policy.

        Wrapped in :func:`provider_log_context` so every log emitted by the
        monitor (and by the kill paths it triggers via
        :meth:`check_and_stop_timed_out_agents`) carries the provider tag —
        the task may be created from a caller whose context already taps it,
        but the wrap makes the attribution self-contained regardless.
        """
        with provider_log_context(self.provider):
            logger.info("Agent timeout monitor started")
            while self._stop_event is not None and not self._stop_event.is_set():
                try:
                    killed = await self.check_and_stop_timed_out_agents()
                    if killed:
                        logger.info(
                            "Auto-stopped %d timed out agent(s): %s",
                            len(killed), ", ".join(killed),
                        )
                except Exception as e:
                    logger.error(
                        "Error in agent timeout monitor: %s", e, exc_info=True,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.TIMEOUT_MONITOR_INTERVAL,
                    )
                except TimeoutError:
                    pass
            logger.info("Agent timeout monitor stopped")

    async def check_and_stop_timed_out_agents(self) -> list[str]:
        """Kill every agent whose ``_check_agent_timeout`` returns a decision."""
        current_time = time.time()
        killed: list[str] = []

        # Snapshot to avoid mutation during iteration (kill_agent acquires the lock).
        for session_id, agent in list(self._agents.items()):
            decision = await self._check_agent_timeout(agent, current_time)
            if decision is None:
                continue
            reason, elapsed, timeout = decision

            logger.info(
                "Auto-stopping agent %s: state=%s, elapsed=%.1fs, timeout=%ds",
                session_id, agent.state.value, elapsed, timeout,
            )
            if await self.kill_agent(session_id, reason=reason):
                killed.append(session_id)

        return killed

    def _state_based_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Shared per-state timeout policy reused by every provider.

        Returns ``(reason, elapsed_seconds, timeout_seconds)`` if ``agent``
        has exceeded the timeout for its current state, ``None`` otherwise.
        Providers call this from their own ``_check_agent_timeout`` after
        applying provider-specific skips (e.g. Claude Code's active cron
        checks). The ``pending_requests`` skip is built in here so every
        provider gets it for free. Reason strings are part of the wire
        contract with the frontend — see ``useWebSocket.js``
        ``kill_reason`` toasts.

        Per-state policy:

        - ``STARTING``: ``PROCESS_TIMEOUT_STARTING`` (default 60s) — stuck startup.
        - ``USER_TURN``: ``PROCESS_TIMEOUT_USER_TURN`` (default 30min) — idle.
        - ``ASSISTANT_TURN``: ``PROCESS_TIMEOUT_ASSISTANT_TURN`` (default 3h)
          for inactivity, plus an absolute
          ``PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE`` (default 10h) safety cap.
        """
        from django.conf import settings

        # Never time out an agent waiting on a user click. The countdown
        # resumes once the pending request resolves and last_activity is
        # touched again.
        if agent.pending_requests:
            return None

        if agent.state == AgentState.STARTING:
            timeout = getattr(settings, "PROCESS_TIMEOUT_STARTING", 60)
            elapsed = current_time - agent.state_changed_at
            if elapsed > timeout:
                return ("timeout_starting", elapsed, timeout)
            return None

        if agent.state == AgentState.USER_TURN:
            timeout = getattr(settings, "PROCESS_TIMEOUT_USER_TURN", 30 * 60)
            elapsed = current_time - agent.last_activity
            if elapsed > timeout:
                return ("timeout_user_turn", elapsed, timeout)
            return None

        if agent.state == AgentState.ASSISTANT_TURN:
            inactivity_timeout = getattr(
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN", 3 * 60 * 60,
            )
            absolute_timeout = getattr(
                settings, "PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE", 10 * 60 * 60,
            )

            # Floor both baselines on ``last_pending_resolved_at`` so the time
            # the agent spent blocked on a user-facing pending request is
            # excluded from both budgets: the caps restart from the moment the
            # agent actually resumed work, not the turn's start. The
            # ``pending_requests`` skip above covers the in-progress wait; this
            # covers the moment right after resolution, where the
            # activity-blind absolute cap would otherwise fire immediately for
            # a request validated after a long absence. Auto-resets per turn —
            # a fresh ASSISTANT_TURN has a newer ``state_changed_at`` than any
            # stale ``last_pending_resolved_at`` (0.0 until one ever resolves),
            # so ``max`` reverts to the real turn start.
            inactivity_elapsed = current_time - max(agent.last_activity, agent.last_pending_resolved_at)
            absolute_elapsed = current_time - max(agent.state_changed_at, agent.last_pending_resolved_at)

            # Absolute takes precedence for the reason.
            if absolute_elapsed > absolute_timeout:
                return ("timeout_assistant_turn_absolute", absolute_elapsed, absolute_timeout)
            if inactivity_elapsed > inactivity_timeout:
                return ("timeout_assistant_turn", inactivity_elapsed, inactivity_timeout)
            return None

        return None

    # ------------------------------------------------------------------
    # Hooks — override in subclasses
    # ------------------------------------------------------------------

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> BaseAgent:
        """Factory hook: build a provider-specific agent instance.

        For providers that accept a client-supplied id (Claude Code), build
        the agent with ``session_id`` as-is — that becomes the canonical id.
        For providers that mint their own id (Codex), ignore ``session_id``
        when ``resume`` is ``False`` and obtain the canonical id from the
        provider (e.g. via ``thread/start``) before constructing the agent.
        When ``resume`` is ``True``, ``session_id`` is the canonical id
        already known to the frontend in every case.

        The returned agent must carry the canonical id in ``agent.session_id``.

        Cleanup invariant: the returned agent must accept
        ``interrupt_or_kill`` immediately, even before ``start`` has been
        called. ``_start_agent`` calls it as the cleanup mechanism when the
        post-creation startup sequence (re-keying, broadcast, DB writes,
        ``agent.start``) raises — the agent owns external resources by then
        and nothing else will release them. Implementations that allocate
        resources inside ``__init__`` (or between ``__init__`` and the
        return statement) must also clean them up locally if the
        construction itself raises, since ``_start_agent`` only sees the
        agent once it has been returned.
        """
        raise NotImplementedError

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Decide whether ``agent`` has exceeded a state-specific timeout.

        Returns ``(reason, elapsed_seconds, timeout_seconds)`` if the agent
        should be killed, ``None`` otherwise. The default returns ``None`` —
        no timeouts are enforced unless the subclass opts in. Most providers
        will apply their own skips first and then delegate to
        ``_state_based_timeout`` for the shared per-state policy.
        """
        return None

    def _start_extra_monitors(self) -> None:
        """Override to launch extra background monitors."""
        return

    async def _stop_extra_monitors(self) -> None:
        """Override to cancel extra background monitors."""
        return

    async def _pre_shutdown_extra(self) -> None:
        """Override to perform cleanup before agents are interrupted."""
        return
