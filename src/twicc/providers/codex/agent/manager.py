"""
Codex agent manager: tracks active Codex agents and creates new ones.

Codex settings are applied as per-turn overrides; Fast mode additionally uses
``thread/settings/update`` so Codex-owned continuations inherit its service tier.
The same RPC attaches canonical scratch/artifact roots after ``thread/start``.
Images are forwarded to the Codex SDK as ``ImageInput`` data URLs; documents
(PDF / TXT) have no Codex protocol equivalent and are silently dropped with a
warning. Approvals are routed through ``CodexAgent``'s sync ↔ async bridge
to the shared ``PendingRequest`` plumbing; the sandbox + approval policy come
from the user's ``permission_mode`` preset via :func:`resolve_codex_policy`
(see ``permission_modes.py``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from asgiref.sync import sync_to_async
from openai_codex.generated.v2_all import ApprovalsReviewer, SandboxMode

from twicc.agent import AgentState, BaseAgent, BaseAgentManager, SendDeliveryError
from twicc.agent.work_dirs import resolve_and_create_work_dirs
from twicc.context_injection import inject_context
from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context
from twicc.pending_session_attributes import get_pending_session_attributes
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from ..bin import make_codex_config
from ..migration_gate import gate_for, wake_migration_scheduler
from ..permission_modes import resolve_codex_policy, resolve_codex_turn_overrides
from ..sdk_wrappers import TwiccAsyncCodex, service_tier_from_fast_mode
from .agent import CodexAgent
from .hardcoded_commands import HardcodedCommand, parse_hardcoded_command
from .sdk_logger import attach_stderr_logging

logger = logging.getLogger(__name__)

# Delay before re-pushing a resumed session's title to Codex, giving the first
# turn's append (which re-derives ``threads.title`` from the first user message)
# time to land — pushing earlier would be overwritten. Matches the rename
# verify's delay.
_RESUME_TITLE_REPUSH_DELAY = 5.0


class CodexAgentManager(BaseAgentManager):
    """Manages all active Codex agents.

    Mirrors the public surface of :class:`ClaudeCodeAgentManager` (the
    methods ``asgi._handle_send_message`` calls into), applying supported
    live settings to the next turn and persisting Fast mode on the thread for
    Codex-owned continuations.

    Typical usage::

        manager = CodexAgentManager()

        # Send a message to an existing session
        await manager.send_to_session(session_id, project_id, cwd, "Hi", settings=...)

        # Create a new session (Codex mints its own canonical id)
        await manager.create_session(draft_id, project_id, cwd, "Hi", settings=...)
    """

    provider: ClassVar[Provider] = Provider.CODEX

    # ------------------------------------------------------------------
    # State-change hook — post-resume title re-assert (Codex-only)
    # ------------------------------------------------------------------

    async def _on_state_change(self, agent: BaseAgent) -> None:
        """Base lifecycle, plus a Codex-only post-resume title re-assert.

        The base skeleton handles every generic transition and the draft
        pending-title flush on ``ASSISTANT_TURN``. On top of that, a *resumed*
        Codex thread needs :meth:`_reassert_codex_title_after_resume` to defend
        its title against the app-server re-deriving ``threads.title`` from the
        first user message on the first turn (see that method and
        :mod:`twicc.providers.codex.titles`).
        """
        await super()._on_state_change(agent)
        state = agent.get_info().state
        if state == AgentState.DEAD:
            wake_migration_scheduler()
            return
        if state != AgentState.ASSISTANT_TURN:
            return
        # Only a (cold) resume can be clobbered: a brand-new session derives its
        # title from the first message for the first time, which is correct.
        if not getattr(agent, "_resumed", False):
            return
        # One-shot per agent run — only the first turn re-derives the title; a
        # later cold resume gets a fresh agent instance, re-arming this flag.
        if getattr(agent, "_codex_title_reasserted", False):
            return
        agent._codex_title_reasserted = True
        await self._reassert_codex_title_after_resume(agent)

    async def _reassert_codex_title_after_resume(self, agent: BaseAgent) -> None:
        """Schedule a post-delay re-push of ``Session.title`` to Codex.

        Codex (0.136 → main) re-derives ``threads.title`` from the first user
        message on every cold resume: ``ThreadMetadataSync`` resets its
        in-memory ``title_seen`` per process and carries no "custom title"
        flag, so a curated name set via ``thread/name/set`` is silently
        reverted on the first turn's append. TwiCC keeps its own
        ``Session.title`` (the live sync never overwrites a non-null title and
        the boot import skips Codex defaults), but the Codex side — and any
        other client or future recovery — would lose the name. So once the
        first turn has run, we push our title back.

        Skipped when a draft-title flush or another title task is already
        converging on this session (the rename path covers those).
        """
        from twicc.core.models import Session
        from twicc.pending_titles import get_pending_title

        session_id = agent.session_id
        if (
            get_pending_title(session_id)
            or session_id in self._pending_title_retry_tasks
            or session_id in self._pending_title_verify_tasks
        ):
            return

        title = await sync_to_async(
            lambda: Session.objects.filter(id=session_id)
            .values_list("title", flat=True)
            .first()
        )()
        if not title:
            return

        task = asyncio.create_task(
            self._repush_codex_title_after_delay(session_id, title),
            name=f"codex-resume-title-reassert-{session_id}",
        )
        self._pending_title_verify_tasks[session_id] = task

    async def _repush_codex_title_after_delay(self, session_id: str, title: str) -> None:
        """Sleep, then unconditionally re-push ``title`` as the Codex thread name.

        The first turn after a cold resume re-derives ``threads.title``
        deterministically, so we skip the read-back verify and push our title
        directly — one ``thread/name/set`` round-trip instead of the verify's
        read-then-maybe-write two. The delay lets that re-derivation land first;
        pushing earlier would be overwritten (``title_seen`` only latches once
        the first append has been observed).

        One-shot: self-cleans from ``_pending_title_verify_tasks`` and is
        cancelled if the agent dies before the delay elapses
        (:meth:`BaseAgentManager._cancel_pending_title_verify`).
        """
        with provider_log_context(self.provider):
            try:
                await asyncio.sleep(_RESUME_TITLE_REPUSH_DELAY)
                await get_provider_helpers(self.provider).rename_session(session_id, title)
                logger.info(
                    "Codex post-resume title re-push for session %s: %r",
                    session_id, title,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "Codex post-resume title re-push failed for session %s: %s",
                    session_id, e,
                )
            finally:
                self._pending_title_verify_tasks.pop(session_id, None)

    # ------------------------------------------------------------------
    # Public API (Codex-specific signatures)
    # ------------------------------------------------------------------

    @staticmethod
    def _warn_about_documents(session_id: str, documents: list[dict] | None) -> None:
        """Defensive log when the frontend ships ``documents`` to a Codex session.

        The frontend's :class:`CodexHelpers.getAttachmentSupport` declares
        ``documents: false`` and refuses them at the file picker / paste /
        drop layer, so reaching this point means either a UI bug or a
        custom WebSocket client. Either way, Codex has no protocol for
        PDF / TXT input, so we drop them and surface the discrepancy to
        the logs rather than failing the whole turn.
        """
        if documents:
            logger.warning(
                "Codex session %s received %d document attachment(s); "
                "Codex has no protocol for documents — dropping them silently.",
                session_id, len(documents),
            )

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
    ) -> bool:
        """Serialize every existing-session send with rollout migration."""

        async with gate_for(session_id):
            return await self._send_to_session_under_gate(
                session_id,
                project_id,
                cwd,
                text,
                settings,
                images=images,
                documents=documents,
            )

    async def _send_to_session_under_gate(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        text: str,
        settings: AgentSettings,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> bool:
        """Send a message to an existing session.

        Routes based on the live agent's state:

        - No agent (or DEAD agent): resume the canonical thread.
        - ``USER_TURN``: schedule a new turn via ``agent.send``.
        - ``ASSISTANT_TURN``: steer the active turn — refresh the agent
          settings bundle for the next ``_run_turn`` and forward to
          ``agent.send``, which routes to ``turn_handle.steer`` (or, when
          the agent is parked in the subagent hold with no active turn,
          breaks the hold and opens a real turn).
        - ``STARTING``: refuse — safety net only; in practice the state has
          flipped to ``ASSISTANT_TURN`` by the time ``_start_agent`` returns.

        ``images`` are forwarded to the SDK as ``ImageInput`` data URLs;
        ``documents`` are dropped with a warning (Codex protocol has no
        equivalent input block).

        Returns ``True`` when a message (or hardcoded command) was accepted
        for delivery, so the WS layer can emit a delivery ack; ``False`` for
        a settings-only update with nothing to send.
        """
        self._warn_about_documents(session_id, documents)

        # Hardcoded slash commands (e.g. ``/compact``) are captured here — the
        # single convergence point every send path funnels through (WS, CLI
        # ``send-message`` drop-file, …) — and routed to a direct SDK action
        # instead of becoming a turn. The App Server gives ``/`` no meaning
        # (slash commands live in the interactive TUI, which never runs here),
        # so this is collision-free; parsing is a cheap pure check run before
        # the lock.
        command = parse_hardcoded_command(text)

        async with self._lock:
            if command is not None:
                await self._dispatch_hardcoded_command(
                    session_id, project_id, cwd, command, settings,
                )
                # Command was dispatched (it may produce no user_message line);
                # ack it so its in-flight snapshot is dropped rather than later
                # mis-audited as undelivered.
                return True

            if session_id in self._agents:
                agent = self._agents[session_id]

                if agent.state == AgentState.DEAD:
                    logger.debug(
                        "Removing dead agent for session %s before resume",
                        session_id,
                    )
                    del self._agents[session_id]

                elif agent.state == AgentState.USER_TURN:
                    if not text and not images:
                        # Settings-only update with no turn to open. Refresh the
                        # bundle now; ``apply_agent_settings`` also persists a
                        # changed Fast tier for Codex-owned continuations.
                        await agent.apply_agent_settings(settings)
                        return False
                    # Refresh the bundle on the live agent so the upcoming turn picks
                    # up any field changed since creation. ``CodexAgent._run_turn``
                    # reads ``effort``, ``permission_mode``, ``selected_model`` and
                    # ``fast_mode`` off
                    # ``agent_settings`` on every ``thread.turn`` call, so changing
                    # the picker mid-session takes effect on the NEXT turn (this one).
                    old_settings = agent.agent_settings
                    logger.debug(
                        "Codex live settings update: session=%s "
                        "permission_mode=%r->%r effort=%r->%r "
                        "selected_model=%r->%r fast_mode=%r->%r",
                        session_id,
                        old_settings.permission_mode, settings.permission_mode,
                        old_settings.effort, settings.effort,
                        old_settings.selected_model, settings.selected_model,
                        old_settings.fast_mode, settings.fast_mode,
                    )
                    await agent.apply_agent_settings(settings)
                    return await agent.send(text, images=images)

                elif agent.state == AgentState.ASSISTANT_TURN:
                    if not text and not images:
                        # Settings-only update during an active turn. Refresh
                        # the bundle so the NEXT turn picks up the new picker
                        # values; nothing to steer.
                        await agent.apply_agent_settings(settings)
                        return False
                    # Steer: refresh the bundle (the active turn keeps the
                    # policy it was started with — ``turn/steer`` has no
                    # override knobs, the new values land on the next
                    # ``_run_turn``), then forward to ``agent.send`` which
                    # detects ``ASSISTANT_TURN`` and routes to
                    # ``turn_handle.steer`` instead of scheduling a new turn.
                    old_settings = agent.agent_settings
                    logger.debug(
                        "Codex live settings update during active turn (steer): "
                        "session=%s permission_mode=%r->%r effort=%r->%r "
                        "selected_model=%r->%r fast_mode=%r->%r",
                        session_id,
                        old_settings.permission_mode, settings.permission_mode,
                        old_settings.effort, settings.effort,
                        old_settings.selected_model, settings.selected_model,
                        old_settings.fast_mode, settings.fast_mode,
                    )
                    await agent.apply_agent_settings(settings)
                    return await agent.send(text, images=images)

                else:
                    raise SendDeliveryError(
                        f"Cannot send message: agent is in state {agent.state}",
                        code="agent_starting",
                    )

            # No live agent — text or at least one image is required to
            # spin one up.
            if not text and not images:
                raise RuntimeError("Cannot start a new agent without a message")

            await self._start_agent(
                session_id, project_id, cwd, text, resume=True,
                settings=settings, images=images,
            )
            return True

    async def _dispatch_hardcoded_command(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        command: HardcodedCommand,
        settings: AgentSettings,
    ) -> None:
        """Route a captured hardcoded command to its SDK action.

        Must be called while holding ``self._lock``.

        - Live, idle agent (``USER_TURN``): run the command on it directly.
        - Live in a ``/goal`` continuation (synthetic ``ASSISTANT_TURN``, see
          ``CodexAgent._goal_continuation_active``): let a ``/goal`` through —
          TwiCC isn't driving that turn and the goal RPCs work mid-continuation
          (set steers it in place per ``_set_goal``, clear stops it). ``/compact``
          there, and anything during a real working turn, stays refused.
        - Live but otherwise busy (real ``ASSISTANT_TURN`` / ``STARTING``):
          refuse — the ``RuntimeError`` surfaces to the caller (the WS handler
          turns it into an ``error`` frame).
        - No agent (or ``DEAD``): resume the thread WITHOUT scheduling a turn
          (``_start_agent`` with empty text + ``command=…``), then run it.
          Mirrors Claude Code, where ``/compact`` on a cold session wakes it.
        """
        logger.info(
            "Codex hardcoded command /%s for session %s (args=%r)",
            command.name, session_id, command.args,
        )
        agent = self._agents.get(session_id)

        if agent is not None and agent.state != AgentState.DEAD:
            # A ``/goal`` continuation parks the agent in a synthetic
            # ASSISTANT_TURN that TwiCC does NOT drive; the goal RPCs still work
            # there (set steers the running turn in place, clear stops it), so a
            # ``/goal`` is allowed. The subagent hold parks the agent the same
            # way with NO turn at all — the thread is as idle as in USER_TURN,
            # so every hardcoded command works there (a ``/compact`` during a
            # long verification must not be refused; its settle re-arms the
            # hold). Everything else while busy — a real working turn, or
            # ``/compact`` mid-goal-continuation — is still refused.
            busy = agent.state != AgentState.USER_TURN
            allowed_while_busy = (
                (command.name == "goal" and agent.in_goal_continuation())
                or agent.in_subagent_hold()
            )
            if busy and not allowed_while_busy:
                raise RuntimeError(
                    f"Cannot run /{command.name} while the assistant is working",
                )
            await agent.apply_agent_settings(settings)
            await agent.run_hardcoded_command(command)
            return

        # No live agent (or a dead one to replace): wake the thread in resume
        # mode with no initial turn and let ``agent.start`` run the command
        # once the (already-resumed) thread is live.
        if agent is not None:
            del self._agents[session_id]

        await self._start_agent(
            session_id, project_id, cwd, "", resume=True,
            settings=settings, command=command,
        )

    async def notify_compacted(self, session_id: str) -> None:
        """Relay a "session compacted" signal from the watcher to a live agent.

        Neutral relay: the watcher detected a ``COMPACT_SUMMARY`` line and
        tells us, without knowing whether the compaction was manual or
        automatic. We forward to the agent if one is live; the agent alone
        decides — via its ``_manual_compaction`` flag — whether to act (end a
        manual ``/compact``'s ``ASSISTANT_TURN``) or ignore it (an
        auto-compaction it did not trigger). No live agent → no-op.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return
        await agent.notify_compacted()

    async def notify_subagents_stopped(
        self, session_id: str, agent_ids: list[str],
    ) -> None:
        """Relay a "these subagents finished" signal from the watcher.

        Fired when ``check_agent_naturally_stopped`` stamps
        ``last_stopped_at`` on subagents of ``session_id`` — the only
        reliable end-of-child signal (nothing reaches the parent's SDK
        stream). The agent drops the ids from its live set and, when parked
        in the subagent hold with nothing left running, settles back to
        USER_TURN. No live agent → no-op.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return
        await agent.notify_subagents_stopped(agent_ids)

    def has_goal_continuation(self, session_id: str) -> bool:
        """Whether a live agent for ``session_id`` is parked in a ``/goal``
        continuation ASSISTANT_TURN.

        Cheap gate for the watcher: it only reads goal-status lines from the DB
        when this returns ``True`` (see ``CodexSessionsWatcher``).
        """
        agent = self._agents.get(session_id)
        return agent is not None and agent.in_goal_continuation()

    async def notify_goal_continuation_stopped(self, session_id: str) -> None:
        """Relay "the goal continuation ended" from the watcher to a live agent.

        Fired when persisted goal evidence with a non-``active`` status lands.
        Neutral relay — the agent decides, via its ``_goal_continuation_active``
        flag, whether this concludes a ``/goal`` it armed (act: end the
        synthetic ASSISTANT_TURN) or is unrelated (ignore). No live agent →
        no-op.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return
        await agent.notify_goal_continuation_stopped()

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
        """Create a brand-new Codex thread for the draft ``session_id``.

        Codex mints its own canonical thread id, so ``session_id`` here is
        the frontend-side draft. ``_create_agent`` builds the agent with the
        canonical id returned by ``thread_start``, and the base
        ``_start_agent`` broadcasts a ``session_bound`` event so the
        frontend can reconcile its local draft state.

        Returns the canonical session id minted by ``thread_start`` — this
        differs from the draft ``session_id`` parameter.

        Same image / document policy as ``send_to_session``: images go
        through; documents are warned-and-dropped.
        """
        self._warn_about_documents(session_id, documents)

        # A hardcoded command (e.g. ``/goal …``) as the FIRST message must be
        # captured here too: ``create_session`` is the new-session entry point
        # and never funnels through ``send_to_session`` (where existing-session
        # capture lives), so without this a ``/goal`` first message would reach
        # the model as ordinary turn text. Parsed before the lock (cheap pure
        # check), same as ``send_to_session``.
        command = parse_hardcoded_command(text)

        async with self._lock:
            # No "session already exists" guard: by construction the draft id
            # is fresh per attempt; even if the frontend reuses one, Codex
            # mints a new canonical id and the (now-orphan) draft-keyed entry
            # is harmless — it will be GCed by its own DEAD transition.
            if command is not None:
                # Mint the thread but run the command on it instead of opening
                # a turn (empty text + ``command=…``), so the ``/command`` text
                # never becomes a real ``user_message`` turn. Mirrors the
                # cold-wake branch of ``_dispatch_hardcoded_command`` — only the
                # resume flag differs (``False`` here: a brand-new thread).
                return await self._start_agent(
                    session_id, project_id, cwd, "", resume=False,
                    settings=settings, command=command,
                )
            return await self._start_agent(
                session_id, project_id, cwd, text, resume=False,
                settings=settings, images=images,
            )

    def get_user_terminated_tool_reason(
        self, session_id: str, item_id: str,
    ) -> str | None:
        """Return the recorded user-termination reason for ``(session_id, item_id)``.

        Called by :class:`twicc.providers.codex.compute.CodexSessionCompute`
        to surface tools the user ended out of band — an approval refusal
        (Deny / Cancel turn / empty permissions) or a turn interruption
        (:meth:`CodexAgent.soft_interrupt`) — as ``ToolResultLink.error``
        when the matching ``function_call_output`` arrives in the JSONL.

        Returns ``None`` if there is no live agent for the session (e.g.
        the agent died and was GC'd, or this is a background re-compute
        on a session from a previous backend run) or if the item_id was
        never user-terminated.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return None
        # ``CodexAgent`` owns ``_user_terminated_tool_ids`` — see the comment
        # on the map in ``CodexAgent.__init__``.
        reason = agent._user_terminated_tool_ids.get(item_id)
        if reason is not None:
            # Hit-only logging: a miss is the common case (every
            # function_call_output triggers a lookup, almost none are
            # user-terminated), so logging both branches would drown out the signal.
            logger.debug(
                "Codex user-terminated-tool lookup hit: session=%s itemId=%s reason=%r",
                session_id, item_id, reason,
            )
        return reason

    # ------------------------------------------------------------------
    # Factory hook
    # ------------------------------------------------------------------

    async def _create_agent(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        *,
        resume: bool,
        settings: AgentSettings,
        **kwargs: Any,
    ) -> CodexAgent:
        """Spin up the AsyncCodex client + thread, wrap them in a CodexAgent.

        For ``resume=True`` the input ``session_id`` is the canonical thread
        id (the frontend already knows it). For ``resume=False`` it's the
        frontend-side draft id — Codex doesn't accept it, so we let
        ``thread_start`` mint a fresh canonical id and use that.

        Sandbox, approval policy, and reviewer come from the user's preset (the
        ``permission_mode`` field on the bundle), translated by
        :func:`resolve_codex_policy`. The default preset is ``"auto"``
        (``workspace-write`` + ``on-request``) so a session without an
        explicit mode emits approvals on shell/network/filesystem
        operations that step outside the workspace; users opt into
        ``"yolo"`` to recover the pre-existing unrestricted bypass.

        The agent settings seed the thread and stay on the agent so each turn
        can apply the latest model, effort, permission mode, and service tier.
        Fast mode is also persisted through ``thread/settings/update`` for
        Codex-owned continuations that do not pass through TwiCC's turn path.
        """
        config = await make_codex_config(cwd=cwd)
        codex = TwiccAsyncCodex(config=config)
        # Debug-only: persist the app-server's stderr (RUST_LOG tracing) per
        # session. Must be attached before the first RPC lazily spawns the
        # subprocess and its drain thread — so it only knows the draft id here.
        # The returned callback re-homes the log onto the canonical thread id
        # once thread_start mints it (see below); None in production.
        rebind_stderr_log = attach_stderr_logging(session_id, codex)

        # ``thread_start`` / ``thread_resume`` lazy-init the transport via
        # ``_ensure_initialized`` on first call — no need to start it
        # explicitly. If anything between here and a successful return
        # raises, we close the transport so we don't leak the codex
        # subprocess. This includes the ``CodexAgent`` constructor below:
        # it monkey-patches the private SDK path
        # ``_codex._client._sync._approval_handler`` (see
        # ``CodexAgent.__init__``), so a future SDK rename would surface as
        # ``AttributeError`` here and must NOT leak the already-spawned
        # subprocess. Once the agent is returned, ownership transfers to
        # the caller (``BaseAgentManager._start_agent`` covers the rest of
        # the startup sequence via its own cleanup wrapper).
        try:
            # Trust clamp (security floor, trust design §13.4): in an untrusted
            # (or unknown-trust) project the thread never binds a no-guardrail
            # policy (``yolo``), whatever mode the bundle carries. The stored
            # Session value is left untouched — the frontend derives the forced
            # display itself.
            def _resolve_trust_clamp() -> tuple[bool, str | None]:
                from twicc.core.services.trust import (
                    clamp_permission_mode_for_untrusted,
                    project_is_untrusted,
                )

                untrusted = project_is_untrusted(project_id)
                mode = settings.permission_mode
                if untrusted:
                    mode = clamp_permission_mode_for_untrusted(Provider.CODEX, mode)
                return untrusted, mode

            untrusted, clamped_mode = await sync_to_async(_resolve_trust_clamp)()
            if clamped_mode != settings.permission_mode:
                settings = settings._replace(permission_mode=clamped_mode)
            service_tier = service_tier_from_fast_mode(settings.fast_mode)

            # Translate the user's preset (Session.permission_mode) into
            # the SDK policy. Unset / unknown modes fall on
            # ``permission_modes.DEFAULT_MODE`` (currently ``"auto"`` =
            # ``workspace-write`` + ``on-request``).
            sandbox, approval_policy, approvals_reviewer = resolve_codex_policy(
                settings.permission_mode,
            )
            # Per-thread config overrides. ``config`` on thread_start /
            # thread_resume reaches the server as a fresh ``ConfigToml`` patch
            # scoped to this thread, which is more reliable than ``-c`` CLI
            # overrides (those bind at app-server boot and can be ignored by
            # the per-thread request layer). We force ``detailed`` reasoning
            # summaries so the JSONL captures the model's thinking text —
            # needed for the TwiCC "thinking" stream support we're wiring
            # next. Every model in the catalog already has
            # ``supports_reasoning_summaries=true``; the only knob that
            # actually moves the needle is the summary verbosity itself.
            thread_config: dict[str, Any] = {
                "model_reasoning_summary": "detailed",
            }
            # TwiCC's own MCP server (/mcp). All tools available in every mode,
            # auto-approved (``default_tools_approval_mode="approve"`` short-
            # circuits the approval_policy) — it is a control plane, orthogonal
            # to the project's permission mode (MCP plan D9). The token is minted
            # against ``session_id`` (the draft id for a brand-new session); the
            # draft→canonical alias registered after thread_start makes it
            # resolve correctly. Gated on the TWICC_NO_MCP kill switch.
            from twicc.mcp import mcp_enabled

            if mcp_enabled():
                thread_config["mcp_servers"] = {"twicc": _twicc_mcp_server_config(session_id)}
                _apply_codex_mcp_context_mode(thread_config)
            # Offer request_user_input (the AskUserQuestion-equivalent) in every
            # collaboration mode, not just Plan — unless the session opted out
            # via ``question_widget=False``. Applied on both start and resume.
            _apply_request_user_input(
                thread_config, enabled=settings.question_widget is not False,
            )
            if approvals_reviewer is ApprovalsReviewer.auto_review:
                # Give the workspace sandbox direct network access. Do not turn
                # on Codex's managed network proxy here: without an administrator
                # requirements layer its allowlist misses are hard HTTP 403s, not
                # Guardian decisions. Guardian still reviews real sandbox
                # escalations, and TwiCC forwards its denials to the user.
                # Per-turn policy independently reasserts network_access.
                _apply_auto_review_network(thread_config)
            if resume:
                # The canonical id is already known, so establish the extra
                # workspace-write roots in the first resume request. This is
                # the thread-level counterpart to the per-turn sandbox policy:
                # Codex-owned continuations (notably /goal) do not pass through
                # TwiCC's turn/start path and therefore need the roots here.
                work_dirs = await resolve_and_create_work_dirs(session_id)
                _apply_codex_work_dirs(thread_config, work_dirs)
                # Model is sticky to the existing thread server-side — leave it
                # unset so the resumed thread keeps whatever model it was started
                # with. Sandbox / approval are re-asserted because the SDK
                # contract requires resume to be self-contained.
                thread = await codex.thread_resume_with_policy(
                    session_id,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                    approvals_reviewer=approvals_reviewer,
                    config=thread_config,
                    service_tier=service_tier,
                )
            else:
                # Resolve the user's selected_model alias (e.g. "gpt",
                # "gpt-5.4", "gpt-mini") to the SDK full name the Codex CLI
                # expects (e.g. "gpt-5.5", "gpt-5.4", "gpt-5.4-mini").
                # Falls back to ``None`` for an empty input — Codex CLI then
                # picks its own default.
                helpers = get_provider_helpers(Provider.CODEX)
                sdk_model = helpers.resolve_sdk_model(settings.selected_model)

                # TwiCC's addendum, passed as ``developer_instructions`` so
                # Codex injects it as a ``developer``-role message at the
                # head of the rollout. Read the frozen value composed by
                # ``core.services.session_creation`` and stashed in the
                # pending buffer keyed by the draft id. On resume we never
                # repass it — the original block already lives in the
                # rollout and would be replayed on every turn anyway.
                def _read_pending_addendum() -> str | None:
                    pending = get_pending_session_attributes(session_id)
                    return pending.system_prompt_addendum if pending else None

                developer_instructions = await sync_to_async(_read_pending_addendum)()

                thread = await codex.thread_start_with_policy(
                    model=sdk_model,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                    approvals_reviewer=approvals_reviewer,
                    config=thread_config,
                    developer_instructions=developer_instructions,
                    service_tier=service_tier,
                )

                # The canonical id only exists after thread/start, while the
                # work directories are deliberately scoped to that id. Create
                # them now. ``pending_id`` preserves access to the draft-keyed
                # orchestration metadata until the watcher creates the row.
                work_dirs = await resolve_and_create_work_dirs(
                    thread.id,
                    pending_id=session_id,
                )

                # A workspace-write thread needs the canonical scratch and
                # artifact paths in its persistent next-turn settings too:
                # Codex-owned continuations (notably /goal) do not pass through
                # TwiCC's turn/start override. Update the already-loaded thread
                # in place. An immediate thread/resume is invalid here because
                # Codex has not indexed the brand-new rollout yet ("no rollout
                # found"). Other sandbox modes do not carry writable roots.
                if sandbox is SandboxMode.workspace_write:
                    sandbox_policy, _, _ = resolve_codex_turn_overrides(
                        settings.permission_mode,
                        writable_roots=work_dirs,
                    )
                    await thread.update_settings_with_policy(
                        sandbox_policy=sandbox_policy,
                    )

                # Codex just minted the canonical thread id — the one piece of
                # context that could not go into the (already-frozen)
                # developer_instructions. Queue it so the first user message
                # carries a ``<twicc:context>`` block announcing the session's
                # own id; it is consumed once at the first turn and scrubbed
                # from the stored copy by ``compute_base``. Never on resume:
                # the original block already lives in the replayed rollout.
                inject_context(thread.id, session_id=thread.id)

                # The MCP token in thread_config was minted against the DRAFT
                # session_id; map it to the canonical id Codex just minted so
                # /mcp resolves the caller correctly (harmless no-op if equal).
                from twicc.mcp.identity import register_draft_alias

                register_draft_alias(session_id, thread.id)

            # Re-home the debug stderr log from the draft id (all we had before
            # thread_start) onto the canonical id, so it matches the JSONL
            # request log and everything else keyed by the real session id.
            # No-op on resume (ids already equal) and in production.
            if rebind_stderr_log is not None:
                rebind_stderr_log(thread.id)

            # On resume ``thread.id == session_id``; on new sessions Codex
            # picked its own canonical id and ``_start_agent`` will broadcast
            # the ``session_bound`` mapping (draft → canonical) for the
            # frontend.
            agent = CodexAgent(
                session_id=thread.id,
                project_id=project_id,
                cwd=cwd,
                settings=settings,
                codex=codex,
                thread=thread,
                untrusted=untrusted,
                work_dirs=work_dirs,
            )

            # Prime the environment-reconciliation baseline (best-effort; the
            # agent methods log-and-swallow on failure). On a fresh start, seed
            # it from the same primitives that built the addendum so the first
            # turn re-states nothing — and read the pending attributes from the
            # DRAFT ``session_id``, which differs from the canonical ``thread.id``
            # the agent now carries. On resume, reset it so the first turn
            # re-states the whole mutable Context block (the frozen
            # ``developer_instructions`` reflect launch-time state and are never
            # re-sent). See :mod:`twicc.context_injection`.
            if resume:
                agent._reset_context_baseline()
            else:
                await agent._seed_context_baseline(pending_id=session_id)
            return agent
        except Exception:
            try:
                await codex.close()
            except Exception:
                logger.debug(
                    "codex.close() failed while unwinding _create_agent",
                    exc_info=True,
                )
            raise

    # ------------------------------------------------------------------
    # Timeout policy
    # ------------------------------------------------------------------

    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Delegate to the shared per-state policy — no Codex-specific skips.

        The ``pending_requests`` skip (load-bearing whenever an approval is
        routed through the sync ↔ async user bridge in :class:`CodexAgent`)
        lives in
        :meth:`BaseAgentManager._state_based_timeout` and is shared with
        every provider that calls into it. No equivalent of Claude's
        ``SessionCron`` check because :class:`SessionCron` is Claude
        Code-specific.
        """
        return self._state_based_timeout(agent, current_time)


def _twicc_mcp_server_config(session_id: str) -> dict:
    """Per-thread TwiCC MCP server entry (TOML-shaped, json_to_toml-merged).

    Every tool is exposed in every mode; ``default_tools_approval_mode="approve"``
    makes them auto-approve without a per-call prompt, independent of the
    session's approval_policy (Codex's ``AppToolApproval::Approve`` short-circuits
    the whole approval check). TwiCC MCP is a control plane, orthogonal to the
    project's permission mode (MCP plan D9).
    """
    from twicc.mcp import mcp_base_url
    from twicc.mcp.identity import mint_session_token

    return {
        "url": mcp_base_url(),
        "http_headers": {"Authorization": f"Bearer {mint_session_token(session_id)}"},
        "default_tools_approval_mode": "approve",
        "tool_timeout_sec": 600,
        "startup_timeout_sec": 30,
    }


def _apply_codex_mcp_context_mode(thread_config: dict) -> None:
    """Force MCP tool-schema deferral on Codex unless the constant disables it."""
    from twicc.mcp import TWICC_MCP_CODEX_DEFER

    if not TWICC_MCP_CODEX_DEFER:
        return  # eager: Codex loads all schemas into context
    features = thread_config.setdefault("features", {})
    features["tool_search_always_defer_mcp_tools"] = True
    # Enabling an under-development feature otherwise prints an unstable-features
    # warning on every thread start.
    thread_config["suppress_unstable_features_warning"] = True


def _apply_request_user_input(thread_config: dict, *, enabled: bool) -> None:
    """Expose or remove Codex's ``request_user_input`` tool for this thread.

    The tool handler is added by default (``tools.experimental_request_user_input``
    resolves to on when unset), but in the Default (non-Plan) collaboration mode —
    which is where TwiCC's Codex sessions run — Codex only offers it when the
    ``default_mode_request_user_input`` feature is enabled. Force it so the
    AskUserQuestion-equivalent works in every mode, not just Plan.

    ``enabled=False`` is the ``question_widget=False`` path: the agent must ask
    its questions as plain text. Turning the feature off already hides the tool
    in the Default mode; we also drop the handler itself so no collaboration
    mode can bring it back. Without this the agent would call a widget nobody
    can answer — the reply travels on the WS approval channel
    (``toolRequestUserInput``), which a scripted or hidden session has no access
    to, and the turn would block forever.

    ``tools.experimental_request_user_input`` is a TABLE (``{enabled = bool}``),
    not a bool: unlike the neighbouring ``tools.web_search``, it has no untagged
    bool form, and a bare ``false`` makes Codex reject the whole config patch
    ("expected struct ExperimentalRequestUserInput"). Same shape for the sibling
    ``tools.update_plan``.

    Both keys live in the per-thread ``config`` patch, read at ``thread_start``
    and ``thread_resume`` only: ``thread/settings/update`` cannot carry them, so
    a change on a live session lands at the next process start. That is why
    ``question_widget`` is a STARTUP setting for Codex.

    The feature is stage ``UnderDevelopment``, so silence the per-start
    unstable-features warning (same knob ``_apply_codex_mcp_context_mode`` sets).
    """
    features = thread_config.setdefault("features", {})
    features["default_mode_request_user_input"] = enabled
    thread_config["suppress_unstable_features_warning"] = True
    if not enabled:
        tools = thread_config.setdefault("tools", {})
        tools["experimental_request_user_input"] = {"enabled": False}


def _apply_codex_work_dirs(thread_config: dict, work_dirs: list[str]) -> None:
    """Bind TwiCC's pre-created work dirs to Codex workspace-write threads.

    The nested config is retained even when the current sandbox mode is
    read-only or danger-full-access. It is harmless there and ensures a later
    workspace-write continuation sees the same roots. Per-turn policy still
    reasserts the list independently.
    """
    workspace_write = thread_config.setdefault("sandbox_workspace_write", {})
    workspace_write["writable_roots"] = work_dirs


def _apply_auto_review_network(thread_config: dict) -> None:
    """Give ``auto_review`` workspace commands direct sandboxed network access.

    Workspace-write blocks network at the sandbox boundary by default, which
    appears to commands as an un-actionable DNS failure. The managed network
    proxy is deliberately not enabled: Codex only wires its allowlist misses
    into Guardian when an administrator-provided network requirements layer is
    active. In an ordinary local setup, enabling the feature alone converts
    every unknown host into an opaque HTTP 403 instead.
    """
    workspace_write = thread_config.setdefault("sandbox_workspace_write", {})
    workspace_write["network_access"] = True


def get_codex_agent_manager() -> CodexAgentManager:
    """Return the Codex agent manager singleton from the registry.

    Convenience wrapper for Codex-specific call sites (orchestrator
    shutdown). The actual instance is owned by the global
    :class:`AgentManagerRegistry`.
    """
    # Lazy import to avoid an import cycle: the registry imports this module.
    from twicc.agent.registry import get_agent_manager_registry

    manager = get_agent_manager_registry().get(Provider.CODEX)
    return manager  # type: ignore[return-value]
