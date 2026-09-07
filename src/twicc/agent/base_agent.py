"""
Base class for a running agent instance.

A ``BaseAgent`` represents one live instance of a coding-agent runtime
(Claude Code SDK, Codex, ...) for a single TwiCC session. Subclasses provide
the runtime-specific lifecycle (``start``/``send``/``interrupt_or_kill``); the
base owns the state machine, lifecycle timestamps, and process introspection
shared by every provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, ClassVar

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.agent.work_dir_autoapprove import all_targets_within_work_dirs
from twicc.agent.work_dirs import resolve_and_create_work_dirs
from twicc.context_injection import clear_context, reconcile, reset_baseline
from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context

from .states import AgentInfo, AgentState, PendingRequest, get_process_memory

if TYPE_CHECKING:
    from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# Async callback invoked when the agent transitions between states.
StateChangeCallback = Callable[["BaseAgent"], Coroutine[Any, Any, None]]


class BaseAgent:
    """Skeleton for a single agent instance.

    Subclasses must implement ``start``, ``send`` and ``interrupt_or_kill``.
    They typically also override ``get_info`` to add provider-specific fields
    on top of the base snapshot (via ``AgentInfo._replace``).

    Subclasses must also set the ``provider`` class attribute to the
    provider key registered in ``AgentManagerRegistry.PROVIDER_MANAGERS``.
    """

    # Provider key (e.g. ``Provider.CLAUDE_CODE``). Subclasses must override.
    provider: ClassVar[Provider]

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        agent_settings: AgentSettings,
        *,
        ephemeral: bool = False,
    ) -> None:
        # Fail fast if the subclass forgot to set its provider key. Without
        # this guard, the missing attribute would only surface deep inside
        # the first ``get_info()`` call as an opaque ``AttributeError``.
        if not getattr(type(self), "provider", None):
            raise TypeError(
                f"{type(self).__name__}.provider must be set to a "
                "Provider enum class attribute (e.g. Provider.CLAUDE_CODE)."
            )

        self.session_id = session_id
        self.project_id = project_id
        self.cwd = cwd
        self.ephemeral = ephemeral
        self.ephemeral_draft_id = session_id
        self.ephemeral_final_text: str = ""
        self.ephemeral_usage: dict = {}
        self.ephemeral_result_emitted = False
        self.ephemeral_soft_interrupted = False

        # Per-session agent settings as a single typed bundle. Mutate via
        # ``_replace`` so the assignment site is the only place a setting
        # changes (no scattered ``self.permission_mode = ...`` writes).
        self.agent_settings = agent_settings

        self.state: AgentState = AgentState.STARTING
        self.previous_state: AgentState | None = None
        self.started_at = time.time()
        self.state_changed_at = self.started_at
        self.last_activity = self.started_at
        # Epoch time the agent last stopped being blocked on a user-facing
        # pending request — i.e. when the last pending request cleared (0.0
        # until one ever resolves). The ASSISTANT_TURN timeout policy floors
        # both its inactivity and absolute baselines on this so the time the
        # user spent before validating a tool approval / question does not
        # count against the caps. Stamped in ``_await_pending_request``'s
        # ``finally`` when ``_pending_requests`` empties; consumed by
        # ``BaseAgentManager._state_based_timeout``.
        self.last_pending_resolved_at: float = 0.0
        self.error: str | None = None
        self.kill_reason: str | None = None

        # Cached ``Session.hidden`` for this agent, gating every live-update
        # broadcast (see ``_is_session_hidden``). ``None`` = not resolved yet.
        # Filled from the DB on first use and overwritten by ``set_hidden``
        # when the flag flips, so the streaming path never touches the DB.
        self._hidden: bool | None = None

        self._dead_event = asyncio.Event()
        # Set by ``_transition_to_dead`` after the DEAD state-change callback
        # finishes (success or exception). ``wait_for_dead`` blocks on this
        # event in addition to ``_dead_event`` so callers that need a
        # fully-stopped agent — most importantly ``BaseAgentManager.shutdown``,
        # which runs immediately before ``stop_db_writer()`` in the server
        # shutdown sequence — only proceed once any DB writes performed in
        # the DEAD callback under ``run_under_db_write_lock`` have committed.
        self._dead_callback_done_event = asyncio.Event()
        self._state_change_callback: StateChangeCallback | None = None

        # Set once a stop (``kill_agent``) has been requested, surfaced via
        # ``get_info().stopping`` so the front's "stopping" spinner survives a
        # WS reconnect / page refresh until the process actually dies. Never
        # reset: the agent only proceeds from here to DEAD. See ``mark_stopping``.
        self._stop_requested = False

        # Latched by ``request_force_kill`` when the user escalates a stop to a
        # hard kill. The graceful waits in every ``interrupt_or_kill`` race this
        # event and bail out immediately, going straight to the forced teardown
        # instead of waiting out the grace window. See ``_race_force``.
        self._force_kill: asyncio.Event = asyncio.Event()

        # Pending requests waiting on a user click (tool approval, ask user
        # question, …). Keyed by request_id (UUID). Provider subclasses populate
        # these via ``_await_pending_request``; the WS layer consumes them via
        # ``resolve_pending_request`` and the manager-level
        # ``BaseAgentManager.resolve_pending_request``.
        # ``_pending_futures`` is typed ``Any`` because each provider's SDK
        # returns its own decision type (Claude: PermissionResult{Allow,Deny};
        # Codex: raw dict). The caller is responsible for the cast.
        self._pending_requests: dict[str, PendingRequest] = {}
        self._pending_futures: dict[str, asyncio.Future[Any]] = {}

        # ProcessRun model row, populated by the manager once the agent is registered.
        self.process_run: Any = None

        # This session's system work dirs (its own artifacts/<id> + scratch/<id>,
        # plus the orchestration root's shared scratch when spawned), cached by
        # ``_resolve_and_create_work_dirs``. Used both to grant prompt-free
        # access at agent build and to auto-approve tool approvals that target
        # only these dirs (``_targets_only_work_dirs``). Empty until resolved
        # (Codex may narrow it to ``None`` only on direct construction paths —
        # its manager normally resolves the list before agent startup).
        self._work_dirs: list[str] = []

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @property
    def _logger(self) -> logging.Logger:
        """Suppress provider payload diagnostics for non-persistent runs."""
        if not getattr(self, "ephemeral", False):
            return logging.getLogger(type(self).__module__)
        suppressed = logging.getLogger("twicc.ephemeral.suppressed")
        suppressed.disabled = True
        return suppressed

    def _set_state(self, new_state: AgentState) -> None:
        """Transition into ``new_state`` and trigger the DEAD-event side-effect."""
        old_state = self.state
        self.previous_state = old_state
        self.state = new_state
        self.state_changed_at = time.time()
        if new_state == AgentState.DEAD:
            self._dead_event.set()
        with provider_log_context(self.provider):
            logger.debug(
                "State transition for session %s: %s -> %s",
                self.session_id, old_state.value, new_state.value,
            )

    async def _notify_state_change(self) -> None:
        """Dispatch the registered state-change callback, swallowing failures.

        Wrapped in :func:`provider_log_context` so every log emitted by the
        callback chain (typically ``BaseAgentManager._on_state_change`` and
        its descendants) carries the provider tag — agent message loops may
        run in tasks that were not created via the orchestrator's
        ``_create_task`` helper (e.g. tasks reached from a WS consumer),
        so the tag must be (re-)posted here to cover the whole notify path.
        """
        if self._state_change_callback is None:
            return
        with provider_log_context(self.provider):
            try:
                await self._state_change_callback(self)
            except Exception as e:
                self._logger.error(
                    "Error in state change callback for session %s: %s",
                    self.session_id, e, exc_info=True,
                )

    async def _transition_to_dead(self) -> None:
        """Atomically transition into DEAD: set state, notify, then signal done.

        Callers must set the agent-side attributes that reflect the final
        state (``kill_reason``, ``error``, ``last_activity``, provider-specific
        events like ``_first_turn_done_event``) BEFORE invoking this helper —
        those must be visible to the state-change callback. The helper owns
        the three steps that must not be split: ``_set_state(DEAD)``, the
        awaited ``_notify_state_change``, and the
        ``_dead_callback_done_event`` signal that releases ``wait_for_dead``.

        Keeping these three steps in one helper is what closes the shutdown
        race exposed by the agent-lifecycle wiring: ``BaseAgentManager.shutdown``
        gathers ``wait_for_dead`` on every agent before returning, so
        ``stop_db_writer`` cannot fire while a DEAD callback's
        ``run_under_db_write_lock`` acquire is still queued. The same
        guarantee protects every non-shutdown caller that polls
        ``wait_for_dead`` (cron restart, settings-triggered restart).

        Cancellation safety: the notify runs on a separate task we
        shield-loop on. Without the shield, an outer cancel (e.g.
        ``BaseAgentManager.shutdown`` cancelling a pending
        ``interrupt_or_kill`` Task on timeout, or ``kill()`` cancelling the
        message loop that is mid-``_transition_to_dead``) would propagate
        through ``await self._notify_state_change()`` BEFORE the callback's
        ``run_under_db_write_lock`` acquire actually applies its DB writes —
        and the ``finally`` would still fire the done event, unblocking
        ``wait_for_dead`` while the writes were never queued. The shield-loop
        holds the done-event signal back until the notify task is genuinely
        finished; any outer cancellation we caught is re-raised once the
        callback has run to completion.
        """
        # Drop any per-session context state this agent accumulated: the queued
        # injection it never consumed (died before composing its first user
        # message) and the reconciliation baseline. Generic across providers;
        # pure dict pops with no await, safe to run before the state-transition
        # dance below. See :mod:`twicc.context_injection`.
        clear_context(self.session_id)
        reset_baseline(self.session_id)
        self._set_state(AgentState.DEAD)
        notify_task = asyncio.create_task(
            self._notify_state_change(),
            name=f"dead-notify-{self.session_id}",
        )
        captured_outer_cancel: asyncio.CancelledError | None = None
        current = asyncio.current_task()
        # Watermark of pending outer cancellations observed so far.
        # ``Task.cancelling()`` is sticky (only ``uncancel()`` decrements
        # it), so a level check ``cancelling() > 0`` would keep matching
        # forever once any outer cancel arrived — including for a later
        # inner self-cancel propagating through the shield. The
        # watermark lets us identify a NEW outer cancel (count went up)
        # vs the same one we already saw (count unchanged).
        cancelling_watermark = current.cancelling() if current is not None else 0
        try:
            while not notify_task.done():
                try:
                    await asyncio.shield(notify_task)
                except asyncio.CancelledError as exc:
                    # ``asyncio.shield`` raises ``CancelledError`` for two
                    # distinct sources:
                    #   * OUR task was cancelled (outer cancel).
                    #   * ``notify_task`` itself was cancelled (inner
                    #     self-cancel — e.g. someone called
                    #     ``notify_task.cancel()`` directly, or the
                    #     callback awaited an already-cancelled future).
                    # Discriminate via two complementary signals:
                    #   (a) ``cancelling()`` advanced past the watermark —
                    #       a new outer cancel was injected at this await.
                    #   (b) ``notify_task`` is still NOT done — the shield
                    #       only raises ``CancelledError`` while the inner
                    #       is alive when the source is an outer cancel.
                    #       Catches the pre-entry-pending edge: caller had
                    #       ``cancel()`` requested but no await had
                    #       consumed it yet, so ``cancelling()`` started
                    #       above zero and didn't move when the delivery
                    #       finally happened at our first shield await.
                    # Either signal classifies the catch as outer; we keep
                    # the FIRST outer cancel so multi-outer-cancel doesn't
                    # overwrite the original message/cause/traceback.
                    # Inner cancellations surface via the drain below as
                    # the authoritative ones.
                    is_outer = False
                    if current is not None:
                        latest = current.cancelling()
                        if latest > cancelling_watermark:
                            cancelling_watermark = latest
                            is_outer = True
                    if not is_outer and not notify_task.done():
                        is_outer = True
                    if is_outer and captured_outer_cancel is None:
                        captured_outer_cancel = exc
                    continue
                except Exception:
                    # Inner raised — ``notify_task.done()`` is True so the
                    # next iteration exits the loop. The drain block below
                    # surfaces the exception under the precedence rule.
                    pass
        finally:
            # ``notify_task.done()`` is now True — either via the while
            # loop or because an eager task factory (Python 3.12+) ran
            # the coro synchronously at ``create_task`` and the very
            # first check saw the task already done. Signal
            # ``wait_for_dead()`` only after the inner is genuinely
            # terminal.
            self._dead_callback_done_event.set()
        # If we entered the helper with an outer cancel already pending
        # (``cancelling() > 0`` at entry) AND the eager task factory
        # short-circuited the shield-loop (``notify_task`` was done
        # before the first ``done()`` check), the pending outer cancel
        # was never delivered to an await — so neither the watermark
        # nor the ``notify_task.done()`` discriminator above could
        # capture it. Force a delivery via a zero-delay yield: asyncio
        # injects the pending ``CancelledError`` on the next await,
        # which is our ``sleep(0)``. Without this, the drain below
        # would propagate the inner cancellation (or return normally),
        # silently losing the outer cancel that should take precedence.
        if (
            captured_outer_cancel is None
            and current is not None
            and current.cancelling() > 0
        ):
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError as exc:
                captured_outer_cancel = exc
        # Drain ``notify_task``'s outcome. The shield-loop above exits as
        # soon as ``notify_task.done()`` is True, but two edge cases let
        # it exit without anyone reading the inner outcome:
        #   * Eager task factory (Python 3.12+): the coro may run to
        #     completion synchronously at ``create_task``, so the very
        #     first ``done()`` check passes and the loop body never
        #     awaits. Without this drain, both a synchronous exception
        #     AND a synchronous ``CancelledError`` from the inner would
        #     be silently swallowed.
        #   * Same-tick race: an outer cancel arriving on the same tick
        #     as inner completion exits the loop via ``done()`` without
        #     re-awaiting, missing the inner exception.
        # ``task.result()`` raises whatever the inner produced
        # (``CancelledError`` for a cancelled task, the actual exception
        # for a failed one) and returns the value otherwise; that's the
        # cleanest way to consume both kinds of outcome.
        # Precedence rule:
        #   * Outer cancellation always wins (the caller's tear-down
        #     contract is "this is being torn down"). Inner exceptions
        #     are logged for observability; an inner cancellation is
        #     suppressed silently (no signal in a redundant cancel).
        #   * Without an outer cancel, the inner result is propagated
        #     verbatim — including a synchronous ``CancelledError`` from
        #     the eager-task-factory path, which would otherwise be
        #     lost.
        try:
            notify_task.result()
        except asyncio.CancelledError:
            if captured_outer_cancel is None:
                # Inner cancelled with no outer cancel — propagate the
                # inner cancellation, preserving its message/cause via a
                # bare ``raise``.
                raise
            # Outer cancel takes precedence; inner cancel is redundant.
        except Exception as inner_exc:
            if captured_outer_cancel is not None:
                with provider_log_context(self.provider):
                    self._logger.error(
                        "DEAD callback for session %s raised while the "
                        "transition was being cancelled; suppressing inner: %s",
                        self.session_id, inner_exc, exc_info=inner_exc,
                    )
            else:
                raise
        if captured_outer_cancel is not None:
            raise captured_outer_cancel

    async def wait_for_dead(self, timeout: float = 30.0) -> bool:
        """Wait until the agent is fully torn down: DEAD state AND callback done.

        Returns ``True`` when both events fire within ``timeout``, ``False``
        on timeout. ``timeout`` covers the whole wait, not each stage. The
        two-stage wait is what makes the manager shutdown safe against the
        DB writer being stopped before a DEAD callback's lock-protected
        writes commit; see :meth:`_transition_to_dead` for the rationale.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            await asyncio.wait_for(self._dead_event.wait(), timeout)
        except TimeoutError:
            return False
        remaining = max(0.0, deadline - asyncio.get_event_loop().time())
        try:
            await asyncio.wait_for(
                self._dead_callback_done_event.wait(), remaining,
            )
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------------
    # Pending requests (shared by every provider)
    # ------------------------------------------------------------------

    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        """Active pending requests waiting for user response, oldest first."""
        return tuple(
            sorted(self._pending_requests.values(), key=lambda r: r.created_at)
        )

    async def _await_pending_request(self, request: PendingRequest) -> Any:
        """Register a pending request, broadcast, wait for resolution, return raw response.

        Provider subclasses construct the ``PendingRequest`` (which knows the
        provider-specific ``tool_name`` / ``tool_input`` / suggestions) and
        delegate the bookkeeping here. The Future's ``set_result`` is invoked
        by ``resolve_pending_request`` when the WS layer routes a user decision
        back, or via ``_cancel_all_pending_futures`` on kill.

        The return type is ``Any`` because each provider's wire decision is its
        own type — the caller in the subclass casts.
        """
        self._pending_requests[request.request_id] = request
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_futures[request.request_id] = future

        # Tell the frontend a new pending request is in flight.
        await self._notify_state_change()

        try:
            return await future
        finally:
            # Drop the entry whether we resolved or were cancelled.
            self._pending_requests.pop(request.request_id, None)
            self._pending_futures.pop(request.request_id, None)
            # The agent stops being blocked on the user the instant the LAST
            # pending request clears (parallel approvals resolve independently,
            # so only the final one marks "work resumes"). Stamp that instant
            # so the ASSISTANT_TURN timeout baselines can exclude the user-wait
            # — without it, the activity-blind 6h absolute cap fires on the very
            # next monitor tick after a long-delayed validation. See
            # ``BaseAgentManager._state_based_timeout``.
            if not self._pending_requests:
                self.last_pending_resolved_at = time.time()
            # If the agent has already transitioned to DEAD (typically because
            # ``interrupt_or_kill`` cancelled this pending future as part of
            # its cleanup), the DEAD state-change callback has already fired
            # — or is in flight — via ``_transition_to_dead``. Re-invoking
            # ``_notify_state_change`` here would run an untracked second
            # copy of the DEAD callback whose DB writes are NOT covered by
            # ``_dead_callback_done_event``, so ``BaseAgentManager.shutdown``'s
            # ``wait_for_dead`` drain would not block on them and
            # ``stop_db_writer`` could race the queued lock acquire. Skip
            # the cleanup broadcast in that case — the DEAD broadcast
            # already announced the final state to the frontend.
            if self.state != AgentState.DEAD:
                await self._notify_state_change()

    def _cancel_all_pending_futures(self) -> None:
        """Cancel every in-flight pending Future.

        Used by provider ``interrupt_or_kill`` paths to unwind awaiters cleanly.
        The awaiter's ``finally`` clause does the dict cleanup; we just signal
        the cancellation here. Safe to call multiple times.
        """
        for future in self._pending_futures.values():
            if not future.done():
                future.cancel()

    def resolve_pending_request(self, request_id: str, response: Any) -> bool:
        """Resolve a specific pending request with the user's response.

        Called by the manager when a WebSocket response arrives from the
        frontend. ``request_id`` disambiguates between concurrent pending
        requests on the same session (e.g. Claude's parallel Read + Glob).

        Returns ``True`` if the request was resolved, ``False`` if there was no
        matching in-flight Future (typically meaning the request was already
        resolved or the agent died in the meantime).
        """
        future = self._pending_futures.get(request_id)
        if future is None or future.done():
            with provider_log_context(self.provider):
                self._logger.warning(
                    "[session %s] resolve_pending_request: no in-flight Future "
                    "for request_id=%s (known=%s)",
                    self.session_id,
                    request_id,
                    list(self._pending_requests.keys()),
                )
            return False
        future.set_result(response)
        return True

    # ------------------------------------------------------------------
    # Process introspection
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        """Return the underlying subprocess PID if applicable.

        Subclasses backed by a subprocess override this. The default returns
        ``None`` so providers that don't run a subprocess (in-process SDKs)
        can leave it untouched.
        """
        return None

    def get_memory_rss(self) -> int | None:
        """Return RSS memory of the underlying subprocess if known."""
        try:
            pid = self.get_pid()
            if pid is None:
                return None
            return get_process_memory(pid)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_info(self) -> AgentInfo:
        """Build an immutable snapshot of the current agent state.

        Subclasses can override this to populate ``active_tools`` and
        ``last_started_tool_id`` by calling ``super()`` and ``_replace``-ing
        the result. ``pending_requests`` is always populated here.
        """
        # The subprocess no longer exists past DEAD — skip the memory lookup.
        memory_rss = None if self.state == AgentState.DEAD else self.get_memory_rss()
        return AgentInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            provider=self.provider,
            state=self.state,
            previous_state=self.previous_state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
            pending_requests=self.pending_requests,
            stopping=self._stop_requested,
            label=self.current_status_label(),
            extra={"ephemeral": True, "ephemeral_draft_id": self.ephemeral_draft_id} if getattr(self, "ephemeral", False) else {},
        )

    def current_status_label(self) -> str | None:
        """Return the status-line override a fresh client must show, or ``None``.

        Recomputed on every call from whatever the agent is waiting on right
        now, so a snapshot never announces a stale count — two subagents may
        have become one while the user was talking to the agent. Returns
        ``None`` whenever the agent's own live activity is the truth (it is
        working, or nothing holds it), which is the arbitration rule: the
        background reason only surfaces when there is nothing else to show.

        Default: no override. Providers with holdable turns override it, and
        broadcast the same value through :meth:`_broadcast_process_label` when
        it changes — one source, computed the same way on both paths.
        """
        return None

    def mark_stopping(self) -> bool:
        """Flag that a stop has been requested so ``get_info`` reports it.

        Returns ``True`` when newly set, so the caller (``kill_agent``) knows to
        broadcast the snapshot now — live clients update immediately, and the
        in-memory flag feeds the ``active_processes`` snapshot for any client
        that (re)connects while the kill is still in flight. Idempotent; a no-op
        once already set or once DEAD.
        """
        if self._stop_requested or self.state == AgentState.DEAD:
            return False
        self._stop_requested = True
        return True

    def request_force_kill(self) -> None:
        """Escalate a stop to a hard kill.

        Latches the force flag so the graceful waits in ``interrupt_or_kill``
        race it and bail straight to the forced process-tree teardown. The
        manager pairs this with an out-of-lock SIGKILL of the OS process, which
        is what lets a hard kill bypass the manager lock a soft stop may hold.
        Idempotent — a hard kill never de-escalates.
        """
        self._force_kill.set()

    async def _race_force(self, coro: Any, timeout: float) -> bool:
        """Await ``coro`` until it finishes, the timeout fires, or a force-kill
        is requested.

        Returns ``True`` iff ``coro`` finished first with a truthy result. On
        force-kill or timeout, ``coro``'s task is cancelled — so use this only
        for side-effect-free waits (e.g. :meth:`wait_for_dead`); a wait that
        owns external state (a turn task, a tmux loop) must race the
        ``_force_kill`` event itself and keep ownership of its own cleanup.
        """
        task = asyncio.ensure_future(coro)
        force = asyncio.ensure_future(self._force_kill.wait())
        try:
            done, _ = await asyncio.wait(
                {task, force}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done and not task.cancelled() and task.exception() is None:
                return bool(task.result())
            return False
        finally:
            for fut in (task, force):
                if not fut.done():
                    fut.cancel()
                    try:
                        await fut
                    except BaseException:
                        pass

    # ------------------------------------------------------------------
    # Environment context reconciliation (shared by every provider)
    # ------------------------------------------------------------------

    async def _reconcile_context(self) -> None:
        """Fold any drift in the dynamic Context block into the next user message.

        Recomputes the mutable Context snapshot (agent settings, workspaces,
        project name, interaction mode, hidden, annotations) from the DB and
        queues only what changed since the agent last saw it. A no-op until the
        session row exists. Providers call this at the single chokepoint every
        outgoing user message passes through (``_build_turn_input`` /
        ``_build_query_prompt``), right where the ``<twicc:context>`` fold runs.
        See :mod:`twicc.context_injection`.

        Best-effort: this runs in the hot path of composing every user message,
        so a failure is logged and swallowed rather than allowed to block the
        message — the worst case is the agent misses one environment update.
        """
        from twicc.agent.system_prompt import mutable_context_fields_for_session

        try:
            current = await mutable_context_fields_for_session(self.session_id)
            if current is None:
                return
            reconcile(self.session_id, current)
        except Exception:
            with provider_log_context(self.provider):
                self._logger.warning(
                    "Context reconciliation failed for session %s; "
                    "skipping this turn.",
                    self.session_id, exc_info=True,
                )

    async def _seed_context_baseline(self, *, pending_id: str | None = None) -> None:
        """Seed the reconciliation baseline at a FRESH start — no injection follows.

        Records the mutable Context snapshot that went into the addendum, built
        from the same primitives (the resolved agent settings, plus the pending
        ``hidden`` / ``annotations``), so the first :meth:`_reconcile_context`
        finds no delta. ``pending_id`` is where to read the pending attributes
        when it differs from this agent's canonical id — Codex mints its id
        inside ``thread_start`` while the pending attributes stay keyed by the
        draft id; it defaults to ``self.session_id`` (Claude Code, where the two
        coincide). A resume calls :meth:`_reset_context_baseline` instead.

        Best-effort: a failure only means the first reconcile re-states the whole
        (correct) snapshot instead of nothing, so it is logged and swallowed
        rather than allowed to abort agent startup.
        """
        from twicc.agent.system_prompt import seed_environment_baseline
        from twicc.pending_session_attributes import get_pending_session_attributes

        try:
            pending = get_pending_session_attributes(pending_id or self.session_id)
            hidden = bool(pending.hidden) if pending else False
            annotations = (pending.annotations if pending else None) or {}
            await sync_to_async(seed_environment_baseline)(
                baseline_session_id=self.session_id,
                provider=self.provider,
                project_id=self.project_id,
                agent_settings=self.agent_settings,
                hidden=hidden,
                annotations=annotations,
            )
        except Exception:
            with provider_log_context(self.provider):
                self._logger.warning(
                    "Failed to seed context baseline for session %s; the first "
                    "turn will re-state the full Context block instead.",
                    self.session_id, exc_info=True,
                )

    def _reset_context_baseline(self) -> None:
        """Forget the reconciliation baseline (resume) → next reconcile re-injects all."""
        reset_baseline(self.session_id)

    async def _resolve_and_create_work_dirs(
        self,
        *,
        pending_id: str | None = None,
    ) -> list[str]:
        """Pre-create this session's work dirs and return them as absolute paths.

        Always covers the session's own ``artifacts/<id>`` and ``scratch/<id>``.
        For a session inside an orchestration tree (``Session.spawn_root`` set),
        the root session's ``scratch/<root_id>`` is added too, so the whole
        subtree shares one scratch folder.

        These paths are granted to the agent for prompt-free access — via
        ``ClaudeAgentOptions.add_dirs`` (Claude Code) or the workspace-write
        sandbox ``writable_roots`` (Codex). Both need the directory to exist up
        front: Claude silently ignores a missing ``--add-dir`` target, and the
        Codex sandbox can write *under* a root but cannot create the root dir
        itself. Creation is ``mkdir -p`` (idempotent): a background cleanup may
        prune empty dirs later, and we recreate them here on the next
        start/resume.

        ``pending_id`` is forwarded for providers whose canonical id differs
        from the pending-session buffer key during startup (Codex draft ids).
        Resolution and validation live in
        :func:`twicc.agent.work_dirs.resolve_and_create_work_dirs` so a provider
        manager can establish the same grants before constructing an agent.
        """
        work_dirs = await resolve_and_create_work_dirs(
            self.session_id,
            pending_id=pending_id,
        )
        # Cache for the auto-approval path (``_targets_only_work_dirs``); the
        # caller also passes these to the provider's directory-scope grant.
        self._work_dirs = work_dirs
        return work_dirs

    def _targets_only_work_dirs(
        self, candidate_paths: list[str], fully_known: bool,
    ) -> bool:
        """True if an approval touching ``candidate_paths`` may be silently
        auto-approved because it targets only this session's system work dirs.

        ``fully_known`` is set by the provider's path extractor — ``False`` means
        the request's full footprint could not be enumerated, so we never
        auto-approve. Containment is checked against ``self._work_dirs`` (this
        session's own dirs), so another session's dirs are never in scope.

        Trust is NOT checked here: callers gate on a live untrusted read first
        (auto-approval is disabled in untrusted projects). See
        ``twicc.agent.work_dir_autoapprove`` for the containment rule.
        """
        if not fully_known:
            return False
        return all_targets_within_work_dirs(candidate_paths, self._work_dirs)

    def set_hidden(self, hidden: bool) -> None:
        """Record a ``Session.hidden`` flip pushed by the visibility service.

        ``hide_session`` / ``unhide_session`` are the only writers of the
        column on an existing row, and they call this through the manager
        registry the moment they flip it — before the ``session_removed``
        broadcast. So the cached value below is exact without the broadcast
        path ever reading the DB.
        """
        self._hidden = hidden

    async def _is_session_hidden(self) -> bool:
        """Whether this agent's session is hidden. Gates every live broadcast.

        Resolved once from the DB, then kept fresh by :meth:`set_hidden`. The
        cache is what makes this usable on the streaming path: a session emits
        one ``stream_block_delta`` per token, and a DB round-trip per token
        (through ``sync_to_async``) would be far too expensive.

        The row does not exist until the provider's watcher ingests the first
        JSONL line, so an unknown session reads as visible AND is not cached —
        otherwise a session created hidden would latch ``False`` forever, since
        no flip (and therefore no ``set_hidden``) ever follows a creation.
        Provider-agnostic — ``hidden`` is a cross-provider ``Session`` column —
        so it lives here and every provider's agent inherits it.
        """
        if getattr(self, "ephemeral", False):
            return False
        if self._hidden is not None:
            return self._hidden

        from twicc.core.models import Session
        known = await sync_to_async(
            lambda: Session.objects.filter(pk=self.session_id)
            .values_list("hidden", flat=True).first()
        )()
        if known is None:
            return False  # no row yet — stay uncached and ask again next time

        # Re-check after the await: a ``set_hidden`` push may have landed while
        # the read was in flight, and a push is authoritative over a snapshot
        # taken before it.
        if self._hidden is None:
            self._hidden = bool(known)
        return self._hidden

    # ------------------------------------------------------------------
    # Live-update broadcasts (WebSocket "updates" group)
    # ------------------------------------------------------------------

    async def _broadcast_stream_event(self, data: dict[str, Any]) -> None:
        """Broadcast a live-update event to all connected WebSocket clients.

        Pushes ``data`` through the ``"updates"`` channel group; the
        consumer's ``broadcast`` handler forwards it verbatim as a WS
        message. Provider-agnostic — every agent's live-update emitters
        (streaming blocks, process labels, …) funnel through here, so the
        WS envelope shape lives in exactly one place.

        Gated by :meth:`_is_session_hidden`, which makes the whole funnel obey
        the rule every other emitter already follows: a hidden session pushes
        nothing to the UI. Streaming is the reason the gate sits here rather
        than on each caller — it is by far the loudest emitter (one frame per
        token, from every live agent at once), and those frames share the one
        bounded per-client queue that also carries ``session_removed``. Left
        ungated, a session went on streaming after being hidden, competing for
        that queue with the very message announcing its removal.
        """
        if await self._is_session_hidden():
            return
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": data},
        )

    async def _broadcast_process_label(self, label: str) -> None:
        """Broadcast a transient label override for the process status display.

        Consumed by the frontend ``process_label`` handler: it overrides the
        working-status line (e.g. ``"compacting"``) until the next
        ``process_state`` transition recreates the process-state object and
        drops the override. The hidden-session guard lives in
        :meth:`_broadcast_stream_event` below, which every emitter funnels
        through.
        """
        await self._broadcast_stream_event({
            "type": "process_label",
            "session_id": self.session_id,
            "label": label,
        })

    # ------------------------------------------------------------------
    # Lifecycle (abstract)
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        **kwargs: Any,
    ) -> None:
        """Start the agent and process the first message.

        Implementations must:

        - Register ``on_state_change`` so ``_notify_state_change`` dispatches to it.
        - Drive the state machine through STARTING → ASSISTANT_TURN → USER_TURN
          (or → DEAD on failure).
        - Never raise: errors are reported via the DEAD state and ``error``.
        """
        raise NotImplementedError

    async def send(self, text: str, **kwargs: Any) -> bool:
        """Send a follow-up message to the running agent.

        Returns ``True`` when the message was accepted for delivery to the
        agent, ``False`` when a synchronous delivery error was swallowed and
        surfaced via the DEAD-state broadcast instead. Callers use the result
        to emit a positive delivery acknowledgement to the frontend.
        """
        raise NotImplementedError

    async def interrupt_or_kill(self, reason: str) -> None:
        """Stop the agent: try a graceful teardown, then force-kill.

        Every provider/mode honours the same shape — attempt a clean stop
        (interrupt / transport close / tmux kill), bounded by a timeout, then
        guarantee the OS process tree is gone via :meth:`_kill_system_process`
        (SIGTERM → wait → SIGKILL). The graceful timeout and what counts as
        "graceful" are provider-specific; the forced backstop is shared so a
        wedged CLI can never keep running after a stop.
        """
        raise NotImplementedError

    async def soft_interrupt(self) -> bool:
        """Interrupt the current turn WITHOUT killing the session.

        Aborts whatever the agent is doing right now (generation, a tool run)
        and drops back to ``USER_TURN`` with the process kept alive, ready for
        the next message — the polite counterpart to :meth:`interrupt_or_kill`,
        which tears the session down to DEAD.

        The base implementation is a no-op returning ``False`` so generic
        callers (the WS handler, :meth:`BaseAgentManager.interrupt_agent`) can
        invoke it without provider/mode-specific dispatch. Agent runtimes that
        can interrupt a turn in place override and return ``True``.
        """
        return False

    async def _kill_system_process(self, pid: int) -> None:
        """Force-kill an OS process and all its children (SIGTERM → SIGKILL).

        Shared forced-teardown backstop for every provider/mode: uses psutil to
        SIGTERM the whole tree (children first), waits up to 2s for a graceful
        exit, then SIGKILLs any survivor. No-op if ``pid`` is already gone.
        Runs in a thread executor to avoid any async context pollution that
        could cause cancel-scope leaks.

        Args:
            pid: Process ID to kill (the root of the tree to take down).
        """
        import psutil

        def _do_kill() -> None:
            """Synchronous kill logic, runs in a separate thread."""
            try:
                parent = psutil.Process(pid)
            except psutil.NoSuchProcess:
                logger.debug("Process %d already dead", pid)
                return

            # Get all children recursively BEFORE killing parent
            # (once parent is dead, children become orphans and harder to find)
            try:
                children = parent.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []

            all_procs = children + [parent]  # Kill children first, then parent
            logger.debug("Killing process %d and %d children", pid, len(children))

            # SIGTERM to all processes
            for proc in all_procs:
                try:
                    proc.terminate()  # SIGTERM
                    logger.debug("Sent SIGTERM to process %d", proc.pid)
                except psutil.NoSuchProcess:
                    pass

            # Wait for graceful termination (up to 2 seconds)
            gone, alive = psutil.wait_procs(all_procs, timeout=2)

            if gone:
                logger.debug("%d process(es) terminated gracefully", len(gone))

            # SIGKILL any survivors
            for proc in alive:
                try:
                    self._logger.warning(
                        "Process %d did not terminate after SIGTERM, sending SIGKILL",
                        proc.pid,
                    )
                    proc.kill()  # SIGKILL
                except psutil.NoSuchProcess:
                    pass

        # Run in thread executor for complete isolation from async context
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_kill)
