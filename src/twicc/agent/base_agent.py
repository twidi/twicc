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
from dataclasses import replace
import logging
import time
from uuid import uuid4
from collections.abc import Callable, Collection, Coroutine, Iterable, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.agent.completion import CompletionController
from twicc.agent.completion_types import CompletionCandidate, CompletionInput, CompletionSnapshot, NativeMessageRef
from twicc.agent.auto_deny import AUTO_DENY_CHECK_INTERVAL_SECONDS, AUTO_DENY_DELAY_SECONDS
from twicc.agent.shell_notice import OwnerFacts, ShellLookup, ShellNoticeState, ShellResolution
from twicc.agent.stop_origin import StopOriginMixin
from twicc.agent.work_dir_autoapprove import all_targets_within_work_dirs
from twicc.agent.work_dirs import resolve_and_create_work_dirs
from twicc.context_injection import clear_context, reconcile, reset_baseline
from twicc.core.enums import Provider
from twicc.logging_context import provider_log_context
from twicc.request_origin import RequestOrigin, capture_origin, system_marker

from .states import AgentInfo, AgentState, PendingRequest, get_process_memory

if TYPE_CHECKING:
    from twicc.providers.helpers import AgentSettings

logger = logging.getLogger(__name__)

# Async callback invoked when the agent transitions between states.
StateChangeCallback = Callable[["BaseAgent"], Coroutine[Any, Any, None]]
BackgroundWorkCallback = Callable[["BaseAgent", "dict | None"], Coroutine[Any, Any, None]]


class BaseAgent(StopOriginMixin):
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
        self.ephemeral_started_monotonic = time.monotonic() if ephemeral else None
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
        self.error_ui_message: dict | None = None
        # Property from StopOriginMixin: the reason and the origin of the request that asked for the stop
        # are assigned together (design 2026-10-14, section 7).
        self.kill_reason = None

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

        # Background-work publication (see ``_schedule_background_work_refresh``).
        # The callback is the manager's persistence hook, attached at
        # registration. ``_published_background_work`` is the last snapshot
        # the front and the ``ProcessRun`` row were given, so a refresh that
        # changes nothing costs nothing.
        self._background_work_callback: BackgroundWorkCallback | None = None
        self._published_background_work: dict | None = None
        self._background_work_refresh_task: asyncio.Task[None] | None = None
        self._background_work_dirty = False
        self._background_work_broadcast_failures = 0

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
        # Auto-deny checks of armed pending requests, keyed by request_id: the
        # next scheduled wall-clock check (see ``_check_auto_deny``). Empty when
        # nothing is armed. Cancelled in ``_await_pending_request``'s finally.
        self._auto_deny_timers: dict[str, asyncio.TimerHandle] = {}
        # Ids of the requests TwiCC auto-denied, until the timeline records the end of the wait
        # (``timeline_hooks.record_process_changes``). Lets the ``awaiting_input.ended`` event say why.
        self.auto_denied_request_ids: set[str] = set()

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
        self._init_shell_notice_state()

        # Providers opt in by recording completion facts. Until then, existing
        # state transitions and one-shot ephemeral results remain unchanged.
        self.completion_controller = CompletionController(run_id=str(uuid4()))
        self._completion_enabled = False
        self._completion_publication_lock = asyncio.Lock()
        self._completion_publication_task: asyncio.Task | None = None
        self._completion_timer_task: asyncio.Task | None = None
        self._completion_timer_candidate: CompletionCandidate | None = None
        self._completion_tasks: set[asyncio.Task] = set()
        self._completion_persist_task: asyncio.Task | None = None
        self._completion_pending_snapshots: dict[str, CompletionSnapshot] = {}
        self._completion_last_queued: CompletionSnapshot | None = None
        self._completion_persist_callback: Callable | None = None
        self._completion_confirm_callback: Callable | None = None
        self._completion_end_callback: Callable | None = None
        self._completion_retry_callback: Callable | None = None
        self._completion_pending_publications: dict[str, CompletionSnapshot] = {}
        self._completion_retry_deadline: float | None = None
        self._completion_openings: dict[str, Any] = {}
        self._completion_opened_ids: set[str] = set()
        # Keep committed boundaries only until their pending publication drains.
        self._completion_published_ids: set[str] = set()
        self._completion_inputs: dict[str, CompletionInput] = {}
        self._completion_input_callback: Callable | None = None
        self._completion_input_receipts: dict[str, asyncio.Future] = {}
        self._completion_durable_inputs: dict[str, CompletionInput] = {}
        self._completion_source_floor = 0
        self._completion_storage_incarnation: str | None = None
        self._completion_history_epoch: int | None = None
        self._completion_committed_snapshot: CompletionSnapshot | None = None
        # Exact identities only. Text remains in source storage and timeline snapshots.
        self._timeline_message_refs: dict[str, list[NativeMessageRef]] = {}
        self._timeline_start_refs: dict[str, NativeMessageRef | None] = {}
        self._timeline_input_refs: dict[str, NativeMessageRef] = {}
        self._timeline_input_origins: dict[str, RequestOrigin] = {}
        self._state_revision = 0

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _timeline_observe_message(self, ref: NativeMessageRef, *, role: str) -> None:
        """Retain one confirmed root identity on its actual logical interval."""
        if not ref.native_id or ref.kind == "unresolved" or not self.completion_controller.is_open:
            return
        turn_id = self.completion_controller.snapshot().turn_id
        refs = self._timeline_message_refs.setdefault(turn_id, [])
        if ref not in refs:
            refs.append(ref)
        if role == "user" and self.completion_controller.snapshot().first_ref == ref:
            if self._timeline_start_refs.get(turn_id) is None:
                self._timeline_start_refs[turn_id] = ref

    def _timeline_reserve_input(self, token: str, ref: NativeMessageRef) -> None:
        """Reserve the exact outgoing identity; source indexing alone confirms its text."""
        self._timeline_input_refs[token] = ref
        self._timeline_bind_inputs()

    def _timeline_bind_inputs(self) -> None:
        for token, ref in tuple(self._timeline_input_refs.items()):
            item = self._completion_inputs.get(token)
            if item is None or item.turn_id is None or item.status == "rejected":
                continue
            refs = self._timeline_message_refs.setdefault(item.turn_id, [])
            if ref not in refs:
                refs.append(ref)
            self._timeline_start_refs.setdefault(item.turn_id, ref)

    def _timeline_origins_for_refs(self, refs: list) -> list:
        """Copy sending origins for exact input identities, never for adjacent messages."""
        wanted = [ref._asdict() if isinstance(ref, NativeMessageRef) else ref for ref in refs]
        result = []
        for token, ref in tuple(self._timeline_input_refs.items()):
            origin = self._timeline_input_origins.get(token)
            item = self._completion_inputs.get(token)
            if origin is None or item is None or item.status == "rejected" or ref._asdict() not in wanted:
                continue
            opening = self._completion_openings.get(item.turn_id)
            result.append({"ref": ref._asdict(), "origin": origin._asdict(),
                           "source_floor": item.admission_cursor,
                           "storage_incarnation": opening[2] if opening else self._completion_storage_incarnation,
                           "history_epoch": opening[3] if opening else self._completion_history_epoch})
        return result

    def _timeline_refs_for_turn(self, snapshot: CompletionSnapshot, *, start: bool = False) -> list:
        """Copy the selected interval before writer admission can yield."""
        if start:
            ref = self._timeline_start_refs.get(snapshot.turn_id)
            return [ref._asdict()] if ref else []
        refs = dict.fromkeys(self._timeline_message_refs.get(snapshot.turn_id, ()))
        return [ref._asdict() for ref in refs if ref.native_id and ref.kind != "unresolved"]

    def _timeline_process_refs(self, state: AgentState) -> list:
        snapshot = self.completion_controller.snapshot()
        if snapshot.ordinal and (self.completion_controller.is_open or state != AgentState.ASSISTANT_TURN):
            return self._timeline_refs_for_turn(snapshot, start=state == AgentState.ASSISTANT_TURN)
        if state == AgentState.ASSISTANT_TURN:
            for token, ref in tuple(self._timeline_input_refs.items()):
                item = self._completion_inputs.get(token)
                if item is not None and item.turn_id is None and item.status != "rejected":
                    return [ref._asdict()]
        return []

    def _timeline_forget_turn(self, turn_id: str) -> None:
        self._timeline_message_refs.pop(turn_id, None)
        self._timeline_start_refs.pop(turn_id, None)
        for token in tuple(self._timeline_input_refs):
            item = self._completion_inputs.get(token)
            if item is not None and item.turn_id == turn_id:
                self._timeline_input_refs.pop(token, None)
                self._timeline_input_origins.pop(token, None)

    def _completion_activity(self, *, first_ref: NativeMessageRef | None = None) -> None:
        """Record actual root work before the provider yields control."""
        from django.utils import timezone

        if self.state == AgentState.DEAD or self._stop_requested:
            return
        self._completion_enabled = True
        previous = self.completion_controller.snapshot()
        self.completion_controller.activity(asyncio.get_running_loop().time(), first_ref=first_ref)
        snapshot = self.completion_controller.snapshot()
        if snapshot.turn_id != previous.turn_id:
            # Capture only observations made before work. Never infer the floor
            # from indexing that may have advanced during an awaited DB write.
            self._completion_openings[snapshot.turn_id] = (
                timezone.now(), self._completion_source_floor,
                self._completion_storage_incarnation, self._completion_history_epoch,
            )
        for token, (turn_id, cursor, status) in tuple(self._completion_inputs.items()):
            if turn_id is None and status != "rejected":
                self._completion_inputs[token] = CompletionInput(snapshot.turn_id, cursor, status)
        self._timeline_bind_inputs()
        self._timeline_start_refs.setdefault(snapshot.turn_id, None)
        if self.state != AgentState.ASSISTANT_TURN:
            self._set_state(AgentState.ASSISTANT_TURN)
        self._completion_refresh()

    def _completion_provider_ended(
        self, *, final_ref: NativeMessageRef | None = None, timeline_verified: bool = True,
    ) -> None:
        if final_ref is not None and timeline_verified:
            self._timeline_observe_message(final_ref, role="assistant")
        self.completion_controller.provider_ended(asyncio.get_running_loop().time(), final_ref=final_ref)
        self._completion_refresh()

    def _completion_holds_changed(self, holds: frozenset[str]) -> None:
        self.completion_controller.set_holds(holds, asyncio.get_running_loop().time())
        self._completion_refresh()

    def _completion_awaiting_changed(self, awaiting: bool) -> None:
        self.completion_controller.set_awaiting(awaiting, asyncio.get_running_loop().time())
        self._completion_refresh()

    def _completion_input_pending(self, token: str, *, staged: bool = False) -> None:
        self._timeline_input_origins.setdefault(token, capture_origin())
        # Manager staging blocks confirmation before setup. Only actual provider
        # delivery associates the receipt with this agent's interval.
        if token not in self._completion_input_receipts:
            self._completion_input_receipts[token] = asyncio.get_running_loop().create_future()
        if self._completion_input_callback is not None:
            self._completion_input_callback(token, self._completion_input_receipts[token])
        if not staged and token not in self._completion_inputs:
            snapshot = self.completion_controller.snapshot()
            turn_id = snapshot.turn_id if self.completion_controller.is_open else None
            self._completion_inputs[token] = CompletionInput(turn_id, self._completion_source_floor, "pending")
        self.completion_controller.input_pending(token, asyncio.get_running_loop().time())
        self._completion_refresh()

    def _completion_input_settled(self, token: str, status: str) -> None:
        item = self._completion_inputs.get(token)
        if item is not None and item.status in ("accepted", "rejected") and status != item.status:
            return
        if status == "rejected":
            self._timeline_input_origins.pop(token, None)
        receipt = self._completion_input_receipts.get(token)
        if status in ("rejected", "uncertain") and receipt is not None and not receipt.done():
            receipt.set_result(False)
        if token in self._completion_inputs:
            turn_id, cursor, _ = self._completion_inputs[token]
            self._completion_inputs[token] = CompletionInput(turn_id, cursor, status)
            # A status change must persist even when root facts are unchanged.
            self._completion_last_queued = None
        self.completion_controller.input_settled(token, status, asyncio.get_running_loop().time())
        self._completion_refresh()

    async def wait_for_completion_input(self, token: str) -> CompletionInput | None:
        """Observe native acceptance only after its durable association commits."""
        receipt = self._completion_input_receipts.get(token)
        if receipt is None:
            return None
        return await asyncio.shield(receipt) or None

    def _completion_inputs_committed(self) -> None:
        for token, item in self._completion_durable_inputs.items():
            receipt = self._completion_input_receipts.get(token)
            if receipt is not None and not receipt.done() and item.status == "accepted":
                receipt.set_result(item)

    def _completion_terminate(self, outcome: str) -> CompletionSnapshot | None:
        snapshot = self.completion_controller.terminate(outcome, asyncio.get_running_loop().time())
        self._cancel_completion_timer()
        if snapshot is not None:
            self._completion_refresh()
        return snapshot

    def _completion_own_task(self, coroutine: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name=f"completion-{self.session_id}")
        self._completion_tasks.add(task)

        def done(finished):
            self._completion_tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                self._logger.error("Completion publication failed for %s", self.session_id,
                                   exc_info=finished.exception())

        task.add_done_callback(done)
        return task

    def _cancel_completion_timer(self, *, preserve_admitted: bool = False) -> None:
        timer = self._completion_timer_task
        if (preserve_admitted and timer is not None and timer is self._completion_publication_task
                and timer is not asyncio.current_task()):
            # An admitted transaction owns this task until its writer drains.
            # A later candidate interrupt must not cancel an older publication.
            return
        self._completion_timer_task = None
        self._completion_timer_candidate = None
        self._completion_retry_deadline = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _completion_refresh(self) -> None:
        snapshot = self.completion_controller.snapshot()
        if (snapshot.ordinal and self._completion_persist_callback is not None
                and snapshot.turn_id not in self._completion_pending_publications
                and snapshot != self._completion_last_queued):
            self._completion_pending_snapshots[snapshot.turn_id] = snapshot
            self._completion_last_queued = snapshot
        if self._completion_pending_publications:
            self._completion_schedule_retry(0.0)
            return
        if self._completion_pending_snapshots and self._completion_persist_task is None:
            self._completion_persist_task = self._completion_own_task(self._completion_drain_snapshots())
        candidate = self.completion_controller.candidate()
        if candidate == self._completion_timer_candidate:
            return
        self._cancel_completion_timer()
        if candidate is not None:
            self._completion_timer_candidate = candidate
            self._completion_timer_task = asyncio.create_task(
                self._completion_wait(candidate), name=f"completion-timer-{self.session_id}",
            )

    def _completion_schedule_retry(self, delay: float = 1.0) -> None:
        """Keep a claimed closure owned until its transactional publication succeeds."""
        if (not self._completion_pending_publications or self._completion_retry_callback is None
                or self.state == AgentState.DEAD or self._stop_requested):
            return
        deadline = asyncio.get_running_loop().time() + delay
        if (self._completion_timer_task is not None and self._completion_retry_deadline is not None
                and self._completion_retry_deadline <= deadline):
            return
        self._cancel_completion_timer()
        self._completion_retry_deadline = deadline
        snapshot = next(iter(self._completion_pending_publications.values()))
        self._completion_timer_task = asyncio.create_task(
            self._completion_retry_wait(snapshot, delay), name=f"completion-retry-{self.session_id}",
        )

    async def _completion_retry_wait(self, snapshot: CompletionSnapshot, delay: float) -> None:
        cancelled = False
        try:
            await asyncio.sleep(delay)
            await self._completion_retry_callback(self, snapshot)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if self._completion_timer_task is asyncio.current_task():
                self._completion_timer_task = None
                self._completion_retry_deadline = None
                if not cancelled:
                    if self._completion_pending_publications:
                        self._completion_schedule_retry()
                    else:
                        self._completion_refresh()

    async def _completion_drain_snapshots(self) -> None:
        """Keep one writer admission in flight and the latest snapshot per interval.

        Dict insertion order preserves closed intervals before later openings.
        Streaming bursts update facts immediately without queuing per-token tasks.
        """
        try:
            while self._completion_pending_snapshots:
                if self._completion_pending_publications:
                    break
                turn_id = next(iter(self._completion_pending_snapshots))
                snapshot = self._completion_pending_snapshots.pop(turn_id)
                await self._completion_persist_callback(self, snapshot)
        finally:
            self._completion_persist_task = None

    async def _completion_wait(self, candidate: CompletionCandidate) -> None:
        cancelled = False
        try:
            await asyncio.sleep(max(0.0, candidate.deadline - asyncio.get_running_loop().time()))
            if self._completion_confirm_callback is not None:
                await self._completion_confirm_callback(self, candidate)
            elif self.ephemeral:
                snapshot = self.completion_controller.claim_due(candidate, asyncio.get_running_loop().time())
                if snapshot is not None:
                    self._set_state(AgentState.USER_TURN)
                    await self._notify_state_change()
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            self._logger.exception("Completion confirmation failed for %s", self.session_id)
        finally:
            if self._completion_timer_task is asyncio.current_task():
                self._completion_timer_task = None
                self._completion_timer_candidate = None
                self._completion_retry_deadline = None
                if not cancelled and self._completion_pending_publications:
                    self._completion_schedule_retry()

    async def _completion_interrupt_candidate(self) -> bool:
        """End a quiet candidate immediately, without an SDK control request."""
        if self.completion_controller.candidate() is None:
            return False
        snapshot = self.completion_controller.terminate("ended", asyncio.get_running_loop().time())
        self._cancel_completion_timer(preserve_admitted=True)
        if snapshot is None:
            return False
        self.ephemeral_soft_interrupted = self.ephemeral
        if self._completion_end_callback is not None:
            return await self._completion_end_callback(self, snapshot)
        self._set_state(AgentState.USER_TURN)
        await self._notify_state_change()
        return True

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
        if new_state == AgentState.DEAD:
            for token, receipt in self._completion_input_receipts.items():
                item = self._completion_inputs.get(token)
                if not receipt.done() and (item is None or item[0] is None or item[2] != "accepted"):
                    receipt.set_result(False)
        if new_state == AgentState.DEAD and self._completion_enabled:
            self._completion_terminate("provider_error" if self.error else "ended")
        self._state_revision += 1
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
                # Everything this callback chain starts or stops (cron restart, pending-settings
                # restart) is automatic: it runs under the system marker, so it neither reads the
                # origin of whoever triggered this transition (often a stale context) nor falls to the
                # default. Deferred writers inside the chain use an origin captured earlier.
                with system_marker():
                    if self.state == AgentState.DEAD and self._completion_tasks:
                        # _transition_to_dead shields this notify task. Drain
                        # journal work here, after DEAD is already observable.
                        await asyncio.gather(*tuple(self._completion_tasks))
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

    async def _await_pending_request(
        self, request: PendingRequest, *, auto_deny_response: Any = None,
    ) -> Any:
        """Register a pending request, broadcast, wait for resolution, return raw response.

        Provider subclasses construct the ``PendingRequest`` (which knows the
        provider-specific ``tool_name`` / ``tool_input`` / suggestions) and
        delegate the bookkeeping here. The Future's ``set_result`` is invoked
        by ``resolve_pending_request`` when the WS layer routes a user decision
        back, or via ``_cancel_all_pending_futures`` on kill.

        ``auto_deny_response`` arms the auto-deny (``twicc.agent.auto_deny``):
        the request gets ``auto_deny_at = created_at + AUTO_DENY_DELAY_SECONDS``
        and the Future resolves with this provider-specific deny value when
        nobody answers by then. ``None`` (the default) leaves it unarmed.

        The return type is ``Any`` because each provider's wire decision is its
        own type — the caller in the subclass casts.
        """
        if auto_deny_response is not None:
            request = replace(request, auto_deny_at=request.created_at + AUTO_DENY_DELAY_SECONDS)
        self._pending_requests[request.request_id] = request
        self._completion_awaiting_changed(True)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_futures[request.request_id] = future

        # Tell the frontend a new pending request is in flight.
        await self._notify_state_change()

        try:
            # Started inside the ``try`` so that its ``finally`` always cancels
            # the scheduled check, whatever ends the wait.
            if auto_deny_response is not None:
                self._check_auto_deny(request, future, auto_deny_response)
            return await future
        finally:
            # Stop the auto-deny checks whatever ended the wait: an answer,
            # the auto-deny itself, or a cancellation (kill / interrupt).
            timer = self._auto_deny_timers.pop(request.request_id, None)
            if timer is not None:
                timer.cancel()
            # Drop the entry whether we resolved or were cancelled.
            self._pending_requests.pop(request.request_id, None)
            self._completion_awaiting_changed(bool(self._pending_requests))
            self._pending_futures.pop(request.request_id, None)
            # The agent stops being blocked on the user the instant the LAST
            # pending request clears (parallel approvals resolve independently,
            # so only the final one marks "work resumes"). Stamp that instant
            # so the ASSISTANT_TURN inactivity baseline can exclude the
            # user-wait — without it, a request validated after a long absence
            # is already over budget on the very next monitor tick, before the
            # resumed agent emits anything. See
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

    def _check_auto_deny(
        self, request: PendingRequest, future: asyncio.Future[Any], response: Any,
    ) -> None:
        """Deny ``request`` once its wall-clock deadline has passed, else check again later.

        The deadline is wall-clock (``time.time()``), the value the frontend
        counts down. ``loop.call_later`` runs on the monotonic clock, which does
        not advance during a machine suspend, so one long delay could fire long
        after the deadline: the check re-runs at most
        ``AUTO_DENY_CHECK_INTERVAL_SECONDS`` apart instead. A Future already
        done (answered, cancelled) ends the checks without touching it.
        """
        self._auto_deny_timers.pop(request.request_id, None)
        if future.done():
            return
        remaining = request.auto_deny_at - time.time()
        if remaining <= 0:
            with provider_log_context(self.provider):
                self._logger.info(
                    "[session %s] Auto-denying unanswered %s request %s",
                    self.session_id, request.tool_name, request.request_id,
                )
            self.auto_denied_request_ids.add(request.request_id)
            future.set_result(response)
            return
        self._auto_deny_timers[request.request_id] = asyncio.get_running_loop().call_later(
            min(AUTO_DENY_CHECK_INTERVAL_SECONDS, remaining),
            self._check_auto_deny, request, future, response,
        )

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
            error_ui_message=getattr(self, "error_ui_message", None),
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
            pending_requests=self.pending_requests,
            stopping=self._stop_requested,
            label=self.current_status_label(),
            label_ui_message=self.current_status_label_ui_message(),
            background_work_in_progress=self.background_work_snapshot(),
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

    def current_status_label_ui_message(self) -> dict | None:
        """Return browser presentation metadata for the current status label."""
        return None

    # ------------------------------------------------------------------
    # Background work
    # ------------------------------------------------------------------

    # Delay between a background-work change and its publication. Codex
    # announces every shell command — a one-second ``ls`` included — with a
    # start/end pair a few milliseconds apart; coalescing them keeps those
    # from costing a broadcast and a DB write each.
    BACKGROUND_WORK_DEBOUNCE_SECONDS: ClassVar[float] = 0.5
    # Retries of a failed ``process_background_work`` broadcast, one per
    # debounce period.
    BACKGROUND_WORK_BROADCAST_RETRIES: ClassVar[int] = 3

    def ended_workflow_task_ids(self) -> frozenset[str]:
        """Provider hook: the task ids of the workflow runs this agent saw end.

        The timeline reads it when the process ends: every open run dies with the
        process except these, whose own notification is still on its way through the
        watcher (``timeline_workflows.close_open_workflows``). Default: none seen
        (Codex has no workflows, the hybrid CLI streams no task events).
        """
        return frozenset()

    def current_background_work(self) -> dict | None:
        """Provider hook: what still runs behind this agent right now.

        Return :func:`~twicc.agent.states.build_background_work`'s snapshot,
        recomputed from the provider's live bookkeeping on every call (never a
        stored copy, same rule as :meth:`current_status_label`). Default:
        nothing.

        Providers call :meth:`_schedule_background_work_refresh` whenever that
        bookkeeping changes, so the front and the ``ProcessRun`` row follow.
        """
        return None

    def background_work_snapshot(self) -> dict | None:
        """The background work to report, or ``None``.

        :meth:`current_background_work`, filtered: a DEAD agent runs nothing
        (its children died with its process), and an ephemeral run reports
        nothing — it has no ``ProcessRun`` row, no visible session, and must
        not escape the idle auto-stop through a leaked shell.
        """
        if self.state == AgentState.DEAD or getattr(self, "ephemeral", False):
            return None
        return self.current_background_work()

    def background_shell_count(self) -> int:
        """Number of background shells still running (``0`` when none)."""
        snapshot = self.background_work_snapshot()
        return snapshot["shells"] if snapshot else 0

    def note_background_work_published(self, snapshot: dict | None) -> None:
        """Record that ``snapshot`` reached the front and the ``ProcessRun`` row.

        Called by the manager after a state transition persisted and broadcast
        the snapshot through the regular path, so the next refresh compares
        against what consumers actually hold.
        """
        self._published_background_work = snapshot

    def _schedule_background_work_refresh(self) -> None:
        """Publish the background-work snapshot soon, if it changed.

        Sync on purpose, so bookkeeping sites (often sync helpers) can call it
        freely. Changes landing while a refresh is pending or in flight are
        folded into it: the refresh loops until no change arrived during its
        own publication. A no-op for an ephemeral run, which never reports
        any (see :meth:`background_work_snapshot`).
        """
        if getattr(self, "ephemeral", False):
            return
        self._background_work_dirty = True
        task = self._background_work_refresh_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (sync unit tests driving bookkeeping directly).
            # Nothing can be published without one; the next state change
            # carries the snapshot anyway.
            return
        self._background_work_refresh_task = loop.create_task(
            self._run_background_work_refresh(),
            name=f"background-work-refresh-{self.session_id}",
        )

    async def _run_background_work_refresh(self) -> None:
        while self._background_work_dirty:
            await asyncio.sleep(self.BACKGROUND_WORK_DEBOUNCE_SECONDS)
            self._background_work_dirty = False
            await self._publish_background_work()

    async def _publish_background_work(self) -> None:
        """Push the current snapshot to the front and the ``ProcessRun`` row.

        A dedicated ``process_background_work`` message rather than a
        ``process_state`` re-broadcast: the front rebuilds its whole process
        object on ``process_state`` (dropping the live tool list), which a
        shell starting mid-turn must not do. Snapshots (``process_state``,
        ``active_processes``) still carry the value through ``get_info``.
        """
        snapshot = self.background_work_snapshot()
        previous = self._published_background_work
        if snapshot == previous:
            return
        self._published_background_work = snapshot
        try:
            await self._broadcast_stream_event({
                "type": "process_background_work",
                "session_id": self.session_id,
                "background_work_in_progress": snapshot,
            })
        except Exception as e:
            # Forget the publication and retry it a few times (the row is
            # still written below). Past the limit, the next change or state
            # transition carries the value anyway.
            self._published_background_work = previous
            self._background_work_broadcast_failures += 1
            self._logger.error(
                "Error broadcasting background work for session %s (attempt %d): %s",
                self.session_id, self._background_work_broadcast_failures, e, exc_info=True,
            )
            if self._background_work_broadcast_failures <= self.BACKGROUND_WORK_BROADCAST_RETRIES:
                self._schedule_background_work_refresh()
        else:
            self._background_work_broadcast_failures = 0
        if self._background_work_callback is None:
            return
        try:
            await self._background_work_callback(self, snapshot)
        except Exception as e:
            self._logger.error(
                "Error persisting background work for session %s: %s",
                self.session_id, e, exc_info=True,
            )

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
        self._completion_terminate("ended")
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
    # Background shell notice (docs/plans/2026-09-27-background-shell-notice-design.md)
    # ------------------------------------------------------------------

    def _init_shell_notice_state(self) -> None:
        """Episode state of the notice (spec §5.3). Separate so test stubs built
        with ``__new__`` can call it."""
        self._shell_notice_idle_since: float | None = None
        self._shell_notice_notified: set[str] = set()
        # Same, for the 30-minute reminder stage.
        self._shell_notice_notified_reminder: set[str] = set()
        self._shell_notice_resolutions: dict[str, ShellResolution] = {}
        self._shell_notice_task: asyncio.Task[None] | None = None

    def shell_notice_state(self) -> ShellNoticeState | None:
        """Provider hook: idle flag and backgrounded shells, owners merged.

        ``None`` skips the agent (no shell tracking, DEAD, ephemeral).
        """
        return None

    def shell_notice_lookups(self, keys: Collection[str], now: float) -> list[ShellLookup]:
        """Provider hook: the shells among ``keys`` that need the database part."""
        return []

    def _shell_notice_live_keys(self) -> set[str]:
        """Provider hook: keys of the live shell records."""
        return set()

    def _note_main_turn_opening(self) -> None:
        """A main turn opens, whatever its source (spec §3.1): restart the delay."""
        self._shell_notice_idle_since = None

    def _note_external_send(self) -> None:
        """An external send reached the agent (spec §3.2): new notice episode."""
        self._shell_notice_notified.clear()
        self._shell_notice_notified_reminder.clear()

    def _drop_shell_notice_key(self, key: str) -> None:
        """Forget a shell that ended (spec §5.3)."""
        self._shell_notice_notified.discard(key)
        self._shell_notice_notified_reminder.discard(key)
        self._shell_notice_resolutions.pop(key, None)

    def mark_shells_noticed(self, keys: Iterable[str], *, reminder: bool = False) -> None:
        """Record a sent notice, only for shells still alive (spec §7 step 5)."""
        live = self._shell_notice_live_keys()
        target = self._shell_notice_notified_reminder if reminder else self._shell_notice_notified
        target.update(key for key in keys if key in live)

    def store_shell_resolutions(self, facts: Sequence[OwnerFacts], now: float) -> None:
        """Store the database part's facts, only for shells still alive (spec §5.5)."""
        live = self._shell_notice_live_keys()
        for fact in facts:
            if fact.key not in live:
                continue
            previous = self._shell_notice_resolutions.get(fact.key)
            self._shell_notice_resolutions[fact.key] = ShellResolution(
                owner_id=fact.owner_id,
                tool_name=fact.tool_name,
                spawner_id=fact.spawner_id,
                title=fact.title,
                known=fact.known,
                running=fact.running,
                stopped_at=fact.stopped_at,
                first_attempt_at=previous.first_attempt_at if previous else now,
            )

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
        JSONL line. Until then the creation-time flag comes from the pending
        session attributes (a session created hidden is hidden from its first
        frame), and the answer is NOT cached — otherwise a session would latch
        that transient read forever, since no flip (and therefore no
        ``set_hidden``) ever follows a creation.
        Provider-agnostic — ``hidden`` is a cross-provider ``Session`` column —
        so it lives here and every provider's agent inherits it.
        """
        if getattr(self, "ephemeral", False):
            return False
        if self._hidden is not None:
            return self._hidden

        from twicc.pending_session_attributes import read_session_visibility_flags
        flags = await sync_to_async(read_session_visibility_flags)(self.session_id)
        if not flags.from_row:
            return flags.hidden  # no row yet — stay uncached and ask again next time

        # Re-check after the await: a ``set_hidden`` push may have landed while
        # the read was in flight, and a push is authoritative over a snapshot
        # taken before it.
        if self._hidden is None:
            self._hidden = flags.hidden
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

    async def _broadcast_process_label(self, label: str, ui_message: dict | None = None) -> None:
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
            **({"label_ui_message": ui_message} if ui_message is not None else {}),
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

        Providers that deliver composer attachments accept an optional
        keyword ``content: AttachmentContent | None``: the ordered native
        parts, the structured manifest and the raw user text, already
        committed by the manager. ``content.user_text`` is the only part the
        context fold may touch.
        """
        raise NotImplementedError

    async def send(self, text: str, **kwargs: Any) -> bool:
        """Send a follow-up message to the running agent.

        Returns ``True`` when the message was accepted for delivery to the
        agent, ``False`` when a synchronous delivery error was swallowed and
        surfaced via the DEAD-state broadcast instead. Callers use the result
        to emit a positive delivery acknowledgement to the frontend.

        Accepts the same optional ``content`` keyword as :meth:`start`.
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
