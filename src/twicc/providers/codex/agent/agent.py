"""
Codex agent: wraps a single AsyncCodex thread for one TwiCC session.

Minimal v1 implementation. Streaming partial output to the frontend is left
out: the watcher picks up the JSONL file the Codex CLI writes and pushes it
through the regular session_item path, so the UI catches up at end-of-turn
granularity. Approvals: the agent installs a sync ↔ async bridge on the SDK's private
``_client._sync._approval_handler`` slot and routes the 5 Codex approval
method families (commandExecution, fileChange, permissions, MCP
elicitations, requestUserInput) through the shared
``BaseAgent._await_pending_request`` plumbing. Whether approvals actually
reach that bridge depends on the resolved ``permission_mode`` for the session:
the default ``auto`` mode routes them to the user, ``auto_review`` routes them
to Codex's reviewer agent, and ``yolo`` keeps command approvals dormant.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from openai_codex import (
    AsyncTurnHandle,
    ImageInput,
    InputItem,
    TextInput,
    TransportClosedError,
)
from openai_codex.generated.v2_all import (
    CodexErrorInfoValue,
    CollaborationMode,
    ErrorNotification,
    GuardianApprovalReviewStatus,
    HttpConnectionFailedCodexErrorInfo,
    ItemGuardianApprovalReviewCompletedNotification,
    ModeKind,
    ReasoningEffort,
    ResponseStreamConnectionFailedCodexErrorInfo,
    Settings as CollaborationModeSettings,  # the SDK name is too generic here
    ThreadGoalStatus,
)

from asgiref.sync import sync_to_async

from twicc.agent import AgentState, BaseAgent, PendingRequest, SendDeliveryError, StateChangeCallback
from twicc.context_injection import apply_pending_context
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from ..permission_modes import resolve_codex_turn_overrides
from ..provider_errors import CodexProviderError, build_provider_error_marker
from ..sdk_wrappers import TwiccAsyncCodex, TwiccAsyncThread, service_tier_from_fast_mode
from ..streaming_registry import get_streamed_item_registry
from .approvals import (
    ELICITATION_METHOD,
    REQUEST_USER_INPUT_METHOD,
    approve_mcp_tool_call_response,
    auto_approve_response_for,
    default_response_for,
    extract_codex_approval_paths,
    is_approval_method,
    is_mcp_tool_call_approval,
    make_pending_request,
)
from .goal_continuation import GoalContinuation
from .hardcoded_commands import HardcodedCommand
from .original_files_cache import (
    MAX_FILE_SIZE as _ORIGINAL_FILE_MAX_SIZE,
    cache_original_files,
    clear_session as clear_original_files_for_session,
)
from .sdk_logger import log_approval_request, log_approval_response, log_stream_event

logger = logging.getLogger(__name__)

# Pattern for HTTP 401/403 in a Codex terminal error message. Codex upstream
# does not map ``CodexErr::UnexpectedStatus(401)`` to ``CodexErrorInfo::Unauthorized``
# — it falls through to ``Other`` and the only auth signal left is the
# formatted message (``"unexpected status 401 Unauthorized: ..."``). Used by
# ``CodexAgent._is_unauthorized_error`` as the third detection path.
_AUTH_STATUS_IN_MESSAGE = re.compile(r"\bstatus\s+40[13]\b", re.IGNORECASE)

# Safety net for a manually-triggered ``/compact``: ``thread.compact()`` is
# fire-and-forget (it returns a start ack, not a completion) and the SDK
# completion notification is lost outside a turn. The compaction's synthetic
# ASSISTANT_TURN normally ends when the watcher ingests the ``compacted``
# JSONL line; if that line never lands (server-side failure), this timeout
# forces the agent back to USER_TURN so it can't stay stuck "compacting".
# Generous on purpose: overshooting only drops the label a little early — the
# compaction, if still running, finishes server-side and its summary appears.
COMPACTION_SAFETY_TIMEOUT_S = 300

# A manual approval only injects Codex's exact-action authorization marker; it
# does not itself start a model cycle. Steer the active turn with this truthful
# representation of the user's click, or open a continuation turn if the
# original turn finished while the approval card was waiting.
_AUTO_REVIEW_RETRY_PROMPT = "Retry the exact action I just approved."

# Fixed user message sent when the user accepts the post-plan "implement"
# prompt — the exact text the official Codex TUI submits for "Yes, implement
# this plan" (codex-rs/tui/src/chatwidget/plan_implementation.rs).
_PLAN_IMPLEMENTATION_MESSAGE = "Implement the plan."

_GUARDIAN_ACTION_TYPES_TO_CORE = {
    "command": "command",
    "execve": "execve",
    "applyPatch": "apply_patch",
    "networkAccess": "network_access",
    "mcpToolCall": "mcp_tool_call",
    "requestPermissions": "request_permissions",
}


def _guardian_action_to_core(
    payload: ItemGuardianApprovalReviewCompletedNotification,
) -> tuple[dict, dict] | None:
    """Return one Guardian action in core-RPC and browser-display shapes."""
    display_action = payload.action.model_dump(mode="json", by_alias=True)
    action = payload.action.model_dump(mode="json", by_alias=False)
    action_type = action.get("type")
    core_action_type = _GUARDIAN_ACTION_TYPES_TO_CORE.get(action_type)
    if core_action_type is None:
        logger.error(
            "Unsupported Codex Guardian action type %r for review %s",
            action_type, payload.review_id,
        )
        return None
    action["type"] = core_action_type
    if action.get("source") == "unifiedExec":
        action["source"] = "unified_exec"
    return action, display_action


def _guardian_denial_context(
    payload: ItemGuardianApprovalReviewCompletedNotification,
) -> tuple[dict, dict] | None:
    """Return the native approval event and browser-safe display details.

    The app-server notification uses its public camelCase action schema, while
    ``thread/approveGuardianDeniedAction`` accepts a serialized core
    ``GuardianAssessmentEvent`` with snake_case variants. Codex's own TUI makes
    the same conversion before implementing ``/approve``.
    """
    review = payload.review
    if review.status is not GuardianApprovalReviewStatus.denied:
        return None

    action_pair = _guardian_action_to_core(payload)
    if action_pair is None:
        return None
    action, display_action = action_pair

    event = {
        "id": payload.review_id,
        "turn_id": payload.turn_id,
        "started_at_ms": payload.started_at_ms,
        "completed_at_ms": payload.completed_at_ms,
        "status": "denied",
        "risk_level": review.risk_level.value if review.risk_level is not None else None,
        "user_authorization": (
            review.user_authorization.value
            if review.user_authorization is not None
            else None
        ),
        "rationale": review.rationale,
        "decision_source": payload.decision_source.root,
        "action": action,
    }
    if payload.target_item_id is not None:
        event["target_item_id"] = payload.target_item_id

    display = {
        "action": display_action,
        "rationale": review.rationale,
        "riskLevel": review.risk_level.value if review.risk_level is not None else None,
        "userAuthorization": (
            review.user_authorization.value
            if review.user_authorization is not None
            else None
        ),
    }
    return event, display


def _capture_original_files_for_apply_patch(inner: Any, session_id: str) -> None:
    """Read each file targeted by an ``apply_patch`` and store the contents.

    Called from ``CodexAgent._handle_stream_event`` when a
    ``FileChangeThreadItem`` is announced via ``item/started`` — that
    notification fires before the Codex CLI subprocess applies the patch,
    so the on-disk content here is the pre-patch original. Sync read (no
    ``await``) is intentional: in ``yolo`` mode (``approval_policy="never"``)
    the patch is applied a few ms later, and yielding to the event loop
    risks losing that window. See ``original_files_cache.py`` for the
    full rationale.

    Files that don't exist on disk (``add`` kind) are silently skipped —
    nothing to capture. Files larger than ``MAX_FILE_SIZE`` are skipped
    too, matching Claude's per-file limit.
    """
    item_id = getattr(inner, "id", None)
    if not isinstance(item_id, str) or not item_id:
        return

    changes = getattr(inner, "changes", None) or ()
    captured: dict[str, str] = {}
    for change in changes:
        path_str = getattr(change, "path", None)
        if not isinstance(path_str, str) or not path_str:
            continue
        try:
            path = Path(path_str)
            if not path.is_file():
                # New file (add), rename destination, or any other case
                # where there's no pre-patch content on disk.
                continue
            if path.stat().st_size > _ORIGINAL_FILE_MAX_SIZE:
                continue
            content = path.read_text(encoding="utf-8")
        except Exception:
            logger.debug(
                "Failed to capture original file %s for call %s",
                path_str, item_id, exc_info=True,
            )
            continue
        captured[path_str] = content

    cache_original_files(session_id, item_id, captured)


def _agent_message_item(payload: Any) -> Any | None:
    """Unwrap a ``ThreadItem`` payload to its ``AgentMessageThreadItem`` inner.

    ``ItemStarted`` / ``ItemCompleted`` notifications carry the freshly
    minted (or finalized) ``ThreadItem`` under ``payload.item``. That
    ``ThreadItem`` is a Pydantic ``RootModel`` whose actual variant lives
    on ``.root`` — ``item.type`` is *not* a passthrough, it returns
    ``None``. We need the real inner instance to read ``type``/``id``.

    Returns the unwrapped instance only when it's an ``agentMessage``;
    any other type (reasoning, command_execution, plan, …) flows through
    the JSONL → watcher path and isn't streamed live in this iteration.
    """
    item = getattr(payload, "item", None)
    if item is None:
        return None
    inner = getattr(item, "root", item)
    if getattr(inner, "type", None) != "agentMessage":
        return None
    return inner


# ``ThreadItem`` variants of the multi-agent v2 collaboration protocol
# that reach the *parent's* stream: ``subAgentActivity`` announces a
# spawn / interaction / interruption of one child, ``collabAgentToolCall``
# wraps the parent's own collaboration tool calls (only ``wait`` shows up
# in practice — ``spawn_agent`` surfaces as a ``subAgentActivity``).
_SUB_AGENT_ACTIVITY_ITEM_TYPE = "subAgentActivity"
_COLLAB_AGENT_TOOL_CALL_ITEM_TYPE = "collabAgentToolCall"
_COLLAB_WAIT_TOOL = "wait"
_SUB_AGENT_STARTED_KIND = "started"
_SUB_AGENT_INTERRUPTED_KIND = "interrupted"
_SUB_AGENT_COMPLETED_KIND = "completed"


def _enum_value(value: Any) -> Any:
    """Return ``value.value`` for an SDK enum, ``value`` otherwise."""
    return getattr(value, "value", value)


def _is_collab_wait_call(inner: Any) -> bool:
    """Whether this thread item is the parent blocking on ``wait_agent``."""
    if getattr(inner, "type", None) != _COLLAB_AGENT_TOOL_CALL_ITEM_TYPE:
        return False
    return _enum_value(getattr(inner, "tool", None)) == _COLLAB_WAIT_TOOL


def _stopped_subagent_ids(session_ids: list[str]) -> list[str]:
    """Return, among ``session_ids``, those whose subagent session has finished.

    A spawned subagent's completion never reaches the parent's SDK stream
    (Codex emits no item for the ``FINAL_ANSWER`` it hands back), so the
    live view of "which children are still running" comes from the
    watcher instead: it stamps ``Session.last_stopped_at`` when the
    subagent's completion pairs with its ``spawn_agent``
    (``check_agent_naturally_stopped``). Blocking ORM call — wrap in
    ``sync_to_async``.
    """
    from twicc.core.models import Session

    return list(
        Session.objects.filter(
            id__in=session_ids, last_stopped_at__isnull=False,
        ).values_list("id", flat=True)
    )


class CodexAgent(BaseAgent):
    """Codex SDK agent wrapping one ``AsyncCodex`` / ``AsyncThread`` pair.

    State machine:

    - ``STARTING`` → ``ASSISTANT_TURN``: ``start(text)`` flips the state and
      schedules a background task that runs the first turn. We don't await
      the turn inside ``start`` because ``_register_and_start`` calls it
      under the manager's lock, and a turn can run for minutes.
    - ``ASSISTANT_TURN`` → ``USER_TURN``: the turn task finishes its run.
    - ``USER_TURN`` → ``ASSISTANT_TURN``: ``send(text)`` schedules a new turn.
    - ``ASSISTANT_TURN`` → ``ASSISTANT_TURN``: ``send(text)`` steers the
      active turn — the input is injected via ``turn/steer`` and consumed
      by Codex at the next turn cycle. No state transition.
    - any → ``DEAD``: ``interrupt_or_kill`` first attempts a clean
      ``turn/interrupt`` via :class:`AsyncTurnHandle` (when a turn is in
      flight), then closes the transport. The in-flight turn task lands in
      ``DEAD`` via :class:`TransportClosedError` either way.
    """

    provider: ClassVar[Provider] = Provider.CODEX

    # Bounded patience for an interrupted turn to unwind on its own — the
    # app-server emits ``turn/completed`` and finalizes its rollout — before we
    # tear the transport down. Mirrors Claude Code's ``wait_for_dead`` window so
    # every provider gives a turn the same grace to finalize on a stop.
    GRACEFUL_TURN_END_TIMEOUT: ClassVar[float] = 30.0

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        settings: AgentSettings,
        codex: TwiccAsyncCodex,
        thread: TwiccAsyncThread,
        untrusted: bool = False,
        work_dirs: list[str] | None = None,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings=settings)
        self._codex = codex
        self._thread = thread
        # Effective trust of the project, resolved once by the manager at
        # thread start/resume (trust design §13.4). While True, the per-turn
        # policy overrides are clamped to the untrusted-allowed set — which
        # also catches live settings updates that would escalate the mode.
        self._untrusted = untrusted
        # Tracked so ``interrupt_or_kill`` can fire ``turn/interrupt`` on the
        # active turn instead of yanking the whole transport.
        self._current_turn: AsyncTurnHandle | None = None
        # Set in ``_run_turn`` once the ``thread.turn`` RPC roundtrip has
        # returned and ``_current_turn`` is published; cleared when the turn
        # unwinds. ``send()`` awaits this when steering so a fast second
        # message doesn't race the turn handshake and find ``_current_turn``
        # still ``None`` despite ``state == ASSISTANT_TURN``.
        self._current_turn_ready: asyncio.Event = asyncio.Event()
        self._turn_task: asyncio.Task[None] | None = None
        # Manual-compaction tracking. ``compact()`` flips the agent into a
        # synthetic ASSISTANT_TURN and sets this flag; when the ``compacted``
        # JSONL line lands, the watcher → manager → ``notify_compacted`` path
        # uses the flag to tell a manual ``/compact`` (act: end the turn) from
        # an auto-compaction (ignore). ``_compaction_timeout_task`` is the
        # safety net that ends the turn if that line never arrives.
        self._manual_compaction: bool = False
        self._compaction_timeout_task: asyncio.Task[None] | None = None
        # The goal route observes physical turns before goal/set returns. Its
        # consumer owns streaming and completion; the rollout watcher is only
        # a fallback for agents without an attached route.
        self._goal_continuation_active: bool = False
        self._goal_monitor: GoalContinuation | None = None
        # Plan-item tracking. Codex delivers a Plan collaboration-mode final
        # answer as a ``plan`` turn item; seeing its ``item/completed`` on the
        # stream sets this flag, and ``_run_turn`` consumes it at turn end to
        # raise the "Implement this plan?" pending request (mirroring the
        # official TUI's ``saw_plan_item_this_turn`` trigger). Reset at every
        # turn start.
        self._plan_item_this_turn: bool = False
        # ``reasoning`` items can fan out into several summary parts (each
        # with its own ``summaryIndex``). The SDK fires one
        # ``summaryPartAdded`` per part but a single ``item/completed`` for
        # the whole item, so we remember which indices we already started
        # streaming and emit a matching ``stream_block_stop`` + ``end`` for
        # each at completion time. Keyed by Codex ``item_id``.
        self._reasoning_summary_indices: dict[str, set[int]] = {}
        # Side-table for ``item/started`` payloads, indexed by ``itemId``.
        # Used to inject the diff into ``fileChange`` PendingRequests (the
        # approval payload itself doesn't carry it — see spec §1.1.b).
        # Populated on ``item/started``, popped on ``item/completed``,
        # cleared on ``interrupt_or_kill``.
        self._items_by_id: dict[str, dict] = {}
        # Map of itemId → human-readable reason for tools the user ended
        # out of band: an approval refusal (Deny, Cancel turn, empty
        # permissions grant) recorded by ``_record_decision_outcome``, or a
        # turn interruption recorded by ``soft_interrupt``. Codex's
        # ``function_call_output`` JSONL line has no ``is_error`` flag —
        # only an output string like "exec_command failed for ...
        # Rejected(...)" or "aborted by user after X.Xs" — and no
        # ``Process exited`` trailer, so the Codex compute path
        # (``CodexSessionCompute.extract_tool_result_info`` and
        # ``compute_link_extra``) consults this side-table both to mark the
        # resulting ``ToolResultLink`` as errored and to flag
        # ``extra.is_terminated`` so the spinner stops. See spec §1.1 +
        # PR2c plan. Lifetime: agent lifetime. Cleared by
        # ``interrupt_or_kill`` (with the rest of the side-tables) or by
        # re-creating the agent on a fresh session.
        self._user_terminated_tool_ids: dict[str, str] = {}
        # Subagents this run spawned through the multi-agent v2
        # collaboration tools, ``agent_thread_id -> agent_path``. Fed by
        # the ``subAgentActivity`` items Codex routes on the *parent's*
        # stream (``started`` adds, ``interrupted``/``completed`` remove)
        # and pruned against the watcher's view of which ones already
        # finished (see :func:`_stopped_subagent_ids`) — in practice the
        # SDK stream carries no per-agent completion item, so the watcher
        # is the reliable end-of-child source.
        #
        # Two consumers:
        # - the "waiting for N subagents" label while the parent blocks
        #   inside a ``wait_agent`` call (the turn stays open on its own
        #   there, only the *why* is missing);
        # - the subagent hold: when a turn ends with entries still live
        #   (the model called ``spawn_agent`` without ``wait_agent``),
        #   ``_run_turn`` keeps ASSISTANT_TURN instead of settling idle —
        #   the Codex mirror of Claude Code's background-agents hold.
        self._live_subagents: dict[str, str] = {}
        # True while the "waiting for N subagents" process label is the
        # one on screen. Set when a ``wait`` collaboration call starts,
        # cleared when it completes. A ``process_state`` broadcast (turn
        # end) rebuilds the frontend's process object and drops the label
        # on its own, so no extra clean-up is needed there.
        self._subagent_wait_label_active = False
        # True while a turn ended held in ASSISTANT_TURN because spawned
        # subagents are still running (see :meth:`_try_arm_subagent_hold`).
        # There is no active turn during the hold: a user message breaks it
        # by opening a real turn (see :meth:`send`), and the watcher's
        # end-of-child signal (:meth:`notify_subagents_stopped`) releases
        # it once the last child finishes. A new real turn always clears
        # the flag at its top and re-decides at its tail.
        self._subagent_hold_active = False

        # Set when a user approves an Auto-review denial after Codex has already
        # closed the originating turn. ``_run_turn`` consumes it by immediately
        # opening a continuation whose only input asks Codex to retry the exact
        # action covered by the native approval marker.
        self._auto_review_retry_after_turn = False
        self._auto_review_retry_action: dict | None = None

        # This session's work dirs (own artifacts/scratch + the orchestration
        # root's shared scratch), normally resolved + pre-created by the manager
        # once the canonical thread id is final. Reused as workspace-write
        # ``writable_roots`` on every turn; ``None`` only for direct/test
        # construction paths that still resolve them lazily in ``start``.
        self._work_dirs: list[str] | None = work_dirs

        # Captured lazily in ``start()`` — that's the first place we're
        # guaranteed to be inside a running asyncio loop. The SDK's worker
        # threads dispatch approval callbacks back to this loop via
        # ``asyncio.run_coroutine_threadsafe``.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Capture the SDK's *default* sync approval handler BEFORE we
        # monkey-patch our own in. The default auto-accepts the 2 methods
        # it recognises and returns ``{}`` for others (see vendored
        # ``openai_codex/client.py``). We delegate to it for
        # server requests we don't own (item/tool/call,
        # account/chatgptAuthTokens/refresh) — see spec §1.6, §7-Q9.
        # PRIVATE SDK API — see memory ``reference_codex_sdk_update_procedure.md``
        # for the upgrade checklist (this attribute path must hold).
        self._sdk_default_approval_handler = (
            self._codex._client._sync._approval_handler
        )
        # Replace the SDK's stub with our bridge. Must happen here, BEFORE
        # any ``thread_start`` / ``thread_resume`` runs (Codex could ship
        # the first approval immediately).
        self._codex._client._sync._approval_handler = self._sync_approval_handler

    @staticmethod
    def _sdk_effort(effort: str | None) -> ReasoningEffort | None:
        """Map our wire effort string to the SDK enum, ``None`` for unset/unknown.

        Unknown values fall through to ``None`` so Codex CLI picks its own
        default rather than crashing the turn — the dropdown only ever
        produces validated values today, this is a defensive guard.

        It is logged at ``error`` level, not ``warning``: reaching it means our
        effort catalogue and the SDK enum have drifted apart, and the user
        silently gets the CLI's default instead of the level they picked. The
        running CLI is the source of truth — ``model/list`` reports each model's
        ``supportedReasoningEfforts``.
        """
        if not effort:
            return None
        try:
            return ReasoningEffort(effort)
        except ValueError:
            logger.error(
                "Unknown Codex effort %r: not in the SDK ReasoningEffort enum, so the CLI will "
                "silently apply its own default. The effort catalogue and the vendored SDK have "
                "drifted — reconcile against the CLI's model/list supportedReasoningEfforts.",
                effort,
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool,
        *,
        images: list[dict] | None = None,
        command: HardcodedCommand | None = None,
        **kwargs: Any,
    ) -> None:
        """Wire the state-change callback and schedule the first turn.

        ``images`` is the WS attachment payload (Claude-shaped image blocks)
        forwarded by the manager. ``documents`` is intentionally absent —
        Codex has no protocol for them, the manager drops them upstream
        with a warning.

        ``command`` is set on the "run a hardcoded command without opening a
        turn" paths: the manager passes empty text + ``command=…`` either to
        wake a cold EXISTING session (``resume=True``, the thread is resumed in
        ``_create_agent``) or to seed a BRAND-NEW session whose first message
        is a command (``resume=False``, the thread is freshly started) — e.g. a
        ``/goal`` typed as the very first message. Either way the thread is
        live by the time we get here; the agent comes up idle and runs the
        command instead of scheduling a turn. See :meth:`run_hardcoded_command`.
        """
        self._state_change_callback = on_state_change
        # Whether this run is a (cold) resume of an existing thread rather than a
        # brand-new session. Read by ``CodexAgentManager._on_state_change`` to
        # decide whether to re-assert our title after the first turn — Codex
        # re-derives ``threads.title`` from the first user message on resume
        # (see that manager hook and :mod:`twicc.providers.codex.titles`).
        self._resumed = resume

        # First place we're guaranteed to be inside a running loop. Captured
        # so the SDK's worker threads can resume our coroutines back here
        # via ``asyncio.run_coroutine_threadsafe`` (see ``_sync_approval_handler``).
        self._loop = asyncio.get_running_loop()

        # The manager normally resolves these before binding the thread-level
        # workspace-write config. Keep a lazy fallback for direct construction
        # paths, but never redo the filesystem/DB work during ordinary startup.
        if self._work_dirs is None:
            self._work_dirs = await self._resolve_and_create_work_dirs()

        if command is not None:
            # Run a hardcoded command (e.g. ``/compact`` on a cold session, or
            # ``/goal`` as a brand-new session's first message) with no initial
            # turn to schedule: the thread is already live (resumed or freshly
            # started in ``_create_agent``). The command drives its own state
            # (``compact`` flips to a synthetic ASSISTANT_TURN then back to
            # USER_TURN; ``/goal <objective>`` settles to ASSISTANT_TURN for the
            # Codex goal continuation, ``/goal clear`` to USER_TURN; bare
            # ``/plan`` settles to USER_TURN while ``/plan <prompt>`` schedules
            # a real turn itself). The agent is still STARTING here; the
            # command's first ``_set_state`` moves it forward.
            await self.run_hardcoded_command(command)
            return

        # Flip to ASSISTANT_TURN immediately so the UI gates the input as
        # "working" — the actual turn runs in the background task below.
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text, images)
        return True

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Schedule a new turn, or steer the active one.

        Returns ``True`` once the input is accepted (turn scheduled, or steer
        landed on the active turn); raises on failure. Codex never silently
        drops a send, so there is no ``False`` return.

        - ``USER_TURN``: schedule a fresh turn (the normal flow).
        - ``ASSISTANT_TURN`` in the subagent hold (no active turn): break the
          hold and schedule a fresh turn — there is nothing to steer.
        - ``ASSISTANT_TURN`` otherwise: steer — push the input into the active
          ``TurnHandle`` so Codex picks it up at the next turn cycle, without
          interrupting tool execution or reasoning. The SDK's ``turn_steer``
          serializes behind the async ``_transport_lock`` held by the
          concurrent ``stream()`` loop, so the steer lands at the next
          notification boundary (sub-second during streaming, seconds during
          a silent tool execution).
        - ``DEAD``: refuse.

        ``turn/steer`` carries only the input — model / effort / sandbox /
        approval overrides are NOT applied to the active turn. Settings
        changed during ``ASSISTANT_TURN`` are refreshed on the agent by the
        manager and pick up on the NEXT ``_run_turn``.
        """
        if self.state == AgentState.DEAD:
            raise SendDeliveryError("Cannot send message: agent is dead", code="agent_dead")

        if self.state == AgentState.ASSISTANT_TURN:
            monitor = getattr(self, "_goal_monitor", None)
            if monitor is not None:
                turn_input = await self._build_turn_input(text, images)
                try:
                    await monitor.steer(turn_input)
                except TransportClosedError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"Steer failed: {e}") from e
                self.last_activity = time.time()
                return True

            if self._subagent_hold_active and self._current_turn is None:
                # Parked in the subagent hold: ASSISTANT_TURN with no active
                # turn to steer (steering would just time out on the
                # handshake wait below). The user message breaks the hold
                # and opens a real turn — which re-arms the hold at its own
                # end if children are still running. Clear the label
                # explicitly: the state doesn't change, so no
                # ``process_state`` broadcast would drop it for us.
                self._subagent_hold_active = False
                await self._broadcast_process_label("")
                self.last_activity = time.time()
                self._schedule_turn(text, images)
                return True

            # ``_run_turn`` publishes ``_current_turn`` only after the
            # ``thread.turn`` RPC returns. A fast steer could race that
            # window; bound the wait so a broken handshake doesn't hang
            # the WS request.
            try:
                await asyncio.wait_for(
                    self._current_turn_ready.wait(),
                    timeout=5.0,
                )
            except TimeoutError as e:
                raise RuntimeError(
                    "Cannot steer: turn handshake did not complete in time",
                ) from e

            turn_handle = self._current_turn
            if turn_handle is None:
                # Ready was set then cleared between wait and read — turn
                # ended. Caller can retry on the next state-change event.
                raise RuntimeError(
                    "Cannot steer: turn ended before steer could be issued",
                )

            turn_input = await self._build_turn_input(text, images)
            try:
                await turn_handle.steer(turn_input)
            except TransportClosedError:
                raise
            except Exception as e:
                logger.warning(
                    "Codex steer failed for session %s: %s",
                    self.session_id, e,
                )
                raise RuntimeError(f"Steer failed: {e}") from e

            self.last_activity = time.time()
            return True

        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

        self._schedule_turn(text, images)

    def _schedule_turn(self, text: str, images: list[dict] | None) -> None:
        """Spawn the background task that drives one turn end-to-end."""
        self._turn_task = asyncio.create_task(
            self._run_turn(text, images),
            name=f"codex-turn-{self.session_id}",
        )

    async def _build_turn_input(
        self,
        text: str,
        images: list[dict] | None,
    ) -> list[InputItem]:
        """Convert the WS attachment payload to a Codex SDK ``Input`` list.

        Each WS image block is the Claude-shaped::

            {"type": "image",
             "source": {"type": "base64", "media_type": "image/...", "data": "..."}}

        Codex CLI's Rust core accepts ``ImageInput.url`` as either an
        http(s) URL or a base64 data URL — and even converts
        ``LocalImageInput(path)`` to the latter internally at request
        serialization time. We therefore re-pack the base64 + media_type
        pair into a single ``data:`` URL and let the SDK forward it
        verbatim. Blocks whose ``source.type`` is not ``"base64"`` are
        skipped defensively (the WS contract guarantees base64 today).

        Order: images first, then the text — mirrors Claude Code's
        content-block ordering so the two providers feel consistent when
        the user attaches references before phrasing the prompt.

        Reconciles the dynamic Context block and folds any queued
        ``<twicc:context>`` block into ``text`` first: this is the single point
        every outgoing Codex user message passes through — a normal turn
        (``_run_turn``) and a steer (``send`` during an assistant turn) — so a
        pending injection, or a settings/environment change the reconcile picks
        up, lands on whichever message goes out next. One-shot and generic; a
        no-op when nothing changed. ``compute_base`` scrubs the block from the
        stored copy. See :mod:`twicc.context_injection`.
        """
        await self._reconcile_context()
        text = apply_pending_context(self.session_id, text)
        items: list[InputItem] = []
        for block in images or ():
            source = block.get("source") or {}
            if source.get("type") != "base64":
                continue
            media_type = source.get("media_type") or "image/png"
            data = source.get("data") or ""
            if not data:
                continue
            items.append(ImageInput(url=f"data:{media_type};base64,{data}"))
        # Images alone are a valid turn input (the frontend allows sending
        # attachments with an empty composer on an existing session); an empty
        # ``TextInput`` would only add a blank item to the rollout. Keep it as
        # the fallback though: callers upstream refuse a message with neither
        # text nor attachments, so an empty list here would mean every image
        # block was skipped — send the (empty) text rather than nothing.
        if text or not items:
            items.append(TextInput(text))
        return items

    async def _run_turn(self, text: str, images: list[dict] | None) -> None:
        """Open one turn, wait for it to complete, transition to USER_TURN.

        Errors raised by the SDK (transport closed, RPC errors, ...) are
        funnelled through ``_handle_error`` and surface as a ``DEAD`` state
        with an ``error`` message. ``TransportClosedError`` is treated as a
        clean shutdown when ``kill_reason`` is already set (i.e. the manager
        killed us on purpose) — no error toast in that case.
        """
        # A real TwiCC-driven turn supersedes any parked ``/goal`` continuation
        # or subagent hold: from here ``_run_turn`` owns the state, so drop the
        # flags (the watcher signals must not flip us out of this turn). The
        # hold re-decides at this turn's own end.
        self._goal_continuation_active = False
        self._subagent_hold_active = False
        # Each turn decides anew whether it delivered a plan (see
        # ``_prompt_plan_implementation``).
        self._plan_item_this_turn = False
        # ``Thread.turn`` expects an ``Input`` (TextInput/ImageInput/...) — only
        # ``Thread.run`` accepts a bare str via internal normalization. We don't
        # use ``run`` because it consumes the turn stream and hides the
        # ``TurnHandle`` we need for clean ``interrupt`` later on.
        #
        # ``effort``, ``permission_mode``, ``selected_model`` and ``fast_mode`` are all
        # read off ``agent_settings`` per turn so live updates via
        # ``send_to_session`` (which refreshes the bundle just before
        # calling ``send``) take effect on the next turn. ``effort=None``
        # / ``model=None`` lets Codex CLI use its own default. The SDK
        # accepts ``approval_policy``, ``approvals_reviewer``, ``sandbox_policy``,
        # ``effort``, ``model`` and ``service_tier`` as per-turn overrides on
        # ``thread.turn`` — they're
        # forwarded as ``TurnStartParams`` on top of the values bound at
        # ``thread_start``, so the current turn keeps its policy but the
        # next one picks up the new picker value.
        effort = self._sdk_effort(self.agent_settings.effort)
        turn_mode = self.agent_settings.permission_mode
        if self._untrusted:
            # Security floor (trust design §13.4): live settings updates refresh
            # the bundle between turns, so re-clamp at every turn — an untrusted
            # project never escalates past the untrusted-allowed set.
            from twicc.core.services.trust import clamp_permission_mode_for_untrusted

            turn_mode = await sync_to_async(clamp_permission_mode_for_untrusted)(
                Provider.CODEX, turn_mode,
            )
        # Grant the agent prompt-free writes to its own artifacts/scratch (and
        # the orchestration root's shared scratch) via the workspace-write
        # sandbox's writable_roots. The list is resolved + pre-created once in
        # ``start()`` (cached on ``self._work_dirs``) and re-sent on every turn:
        # each turn's sandbox_policy replaces the previous one, so omitting it
        # would wipe the roots. No-op for read-only/strict (no writes) and yolo
        # (writes everywhere) — those sandbox types don't carry the field.
        sandbox_policy, approval_policy, approvals_reviewer = resolve_codex_turn_overrides(
            turn_mode, writable_roots=self._work_dirs,
        )
        sdk_model = get_provider_helpers(Provider.CODEX).resolve_sdk_model(
            self.agent_settings.selected_model,
        )
        service_tier = service_tier_from_fast_mode(self.agent_settings.fast_mode)
        turn_input = await self._build_turn_input(text, images)
        try:
            turn_handle = await self._thread.turn_with_policy(
                turn_input,
                model=sdk_model,
                effort=effort,
                service_tier=service_tier,
                approval_policy=approval_policy,
                approvals_reviewer=approvals_reviewer,
                sandbox_policy=sandbox_policy,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return

        self._current_turn = turn_handle
        self._current_turn_ready.set()

        # Consume the turn's notification stream ourselves (instead of the
        # blackbox ``turn_handle.run()``) so we can:
        #   - Broadcast ``stream_block_*`` WS events that paint the live
        #     assistant text in the frontend before the JSONL line lands.
        #   - Push each completed ``agentMessage`` item_id onto the FIFO
        #     registry so the watcher can stamp the matching SessionItem
        #     with ``stream_uuid`` and the frontend can retire the synthetic
        #     placeholder. (See ``streaming_registry.py`` for the why.)
        try:
            stream = turn_handle.stream()
            try:
                async for event in stream:
                    await self._handle_stream_event(event)
            finally:
                await stream.aclose()
        except TransportClosedError:
            # Manager closed the transport — expected during interrupt_or_kill
            # or shutdown. _handle_error already ran (or will run); avoid a
            # second transition if we're already DEAD.
            if self.state != AgentState.DEAD:
                self.last_activity = time.time()
                await self._transition_to_dead()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Turn run failed: {e}", exc=e)
            return
        finally:
            self._current_turn = None
            self._current_turn_ready.clear()

        # Skip the USER_TURN transition if an in-stream branch already
        # moved us to DEAD (e.g. terminal ``error`` notification). The
        # stream loop above can exit cleanly after that — via a final
        # ``turn/completed`` arriving before ``self._codex.close()``
        # propagates — and we don't want to overwrite the terminal state.
        if self.state == AgentState.DEAD:
            return

        if self._auto_review_retry_after_turn:
            self._auto_review_retry_after_turn = False
            self._auto_review_retry_action = None
            logger.info(
                "Starting Codex continuation after manual Auto-review approval "
                "for session %s",
                self.session_id,
            )
            await self._run_turn(_AUTO_REVIEW_RETRY_PROMPT, None)
            return

        if self._plan_item_this_turn:
            # The turn delivered a Plan-mode final answer: ask what to do next
            # instead of settling idle. The prompt owns the state from here
            # (ASSISTANT_TURN while pending, then either the implement
            # continuation turn or USER_TURN).
            self._plan_item_this_turn = False
            await self._prompt_plan_implementation()
            return

        # The turn is over, so any in-turn ``wait_agent`` is too — drop its
        # label flag locally (an interrupted wait may never see its
        # ``item/completed``); from here the hold owns the label if needed.
        self._subagent_wait_label_active = False

        # Turn completed normally. If subagents this session spawned are
        # still running (``spawn_agent`` without ``wait_agent`` — Codex ends
        # the turn as soon as the parent stops talking, children or not),
        # hold ASSISTANT_TURN instead of settling idle: this keeps every
        # USER_TURN consumer quiet — "finished working" notifications, the
        # green check, the idle auto-stop — until a turn ends with nothing
        # left running. The Codex mirror of Claude Code's background hold.
        if await self._try_arm_subagent_hold():
            return

        # Turn completed normally → ready for the next user input.
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def apply_agent_settings(self, settings: AgentSettings) -> None:
        """Refresh the bundle and persist a changed Fast tier for continuations.

        Ordinary turns receive every setting as a per-turn override. Codex-owned
        continuations such as goals do not pass through that path, so a Fast mode
        change is also written to the loaded thread without starting a turn.
        """
        previous_tier = service_tier_from_fast_mode(self.agent_settings.fast_mode)
        next_tier = service_tier_from_fast_mode(settings.fast_mode)
        if next_tier is not None and next_tier != previous_tier:
            await self._thread.update_settings_with_policy(service_tier=next_tier)
        self.agent_settings = settings

    # ------------------------------------------------------------------
    # Hardcoded slash commands (captured by the manager — see
    # ``hardcoded_commands.py``)
    # ------------------------------------------------------------------

    async def run_hardcoded_command(self, command: HardcodedCommand) -> None:
        """Execute a hardcoded slash command captured by the manager.

        Tiny dispatch table — adding a command is a branch here plus its
        action method. ``parse_hardcoded_command`` only ever yields a name in
        ``KNOWN_COMMANDS``, so the trailing ``else`` is a defensive guard
        against parser/dispatch drift, not a user-reachable path.
        """
        if command.name == "compact":
            await self.compact()
        elif command.name == "goal":
            await self.run_goal_command(command.args)
        elif command.name == "plan":
            await self.run_plan_command(command.args)
        else:
            raise RuntimeError(
                f"No handler for hardcoded command {command.name!r}",
            )

    def _note_sub_agent_activity(self, inner: Any) -> None:
        """Update the live-subagent set from one ``subAgentActivity`` item.

        ``started`` is the spawn (the only kind whose ``event_id`` is a
        ``spawn_agent`` call), ``interrupted``/``completed`` end the child
        (``completed`` is defensive — the runtimes observed so far never
        route it on the parent's stream, the watcher signal covers the
        gap), and ``interacted`` is just a message passing through — it
        must not change the set. Idempotent: the SDK emits the same item
        on ``item/started`` and ``item/completed``.
        """
        thread_id = getattr(inner, "agent_thread_id", None)
        if not isinstance(thread_id, str) or not thread_id:
            return
        kind = _enum_value(getattr(inner, "kind", None))
        if kind == _SUB_AGENT_STARTED_KIND:
            agent_path = getattr(inner, "agent_path", None)
            self._live_subagents[thread_id] = agent_path if isinstance(agent_path, str) else ""
        elif kind in (_SUB_AGENT_INTERRUPTED_KIND, _SUB_AGENT_COMPLETED_KIND):
            self._live_subagents.pop(thread_id, None)

    async def _refresh_subagent_wait_label(self) -> None:
        """Say the parent blocks on ``wait_agent``, with a count when there is one.

        Codex holds the turn open on its own here (the call blocks inside
        the turn), so the state is already ``ASSISTANT_TURN`` — what is
        missing is *why*, since Codex streams no tool activity and the
        frontend would otherwise show a bare "thinking" for the whole
        wait. Same channel and wording as Claude Code's
        ``_refresh_waiting_label``.

        The count is the spawns seen on this run's stream minus the ones
        the watcher already saw finish, so a sequence of one-agent waits
        reads "1 subagent" each time instead of accumulating. With nothing
        live the label drops the count and reads a bare "waiting" — a
        stale count would be worse than none, and both ways to get there
        are honest as "waiting": every child already finished, or the wait
        was issued with no child at all (the model reaching for
        ``wait_agent`` as a sleep, which nothing can wake before its own
        ``timeout_ms``).
        """
        await self._prune_finished_subagents()
        self._subagent_wait_label_active = True
        # Same composition as the snapshot path — one source, one wording.
        label = self.current_status_label()
        if label is not None:
            await self._broadcast_process_label(label)

    def current_status_label(self) -> str | None:
        # Recomputed, never stored: the count is read off the live set at the
        # moment a client asks. A manual /compact owns the line while it runs
        # (its own synthetic turn), otherwise the label only exists while the
        # agent blocks on ``wait_agent`` or is parked in the subagent hold —
        # any other moment, what Codex is doing speaks for itself.
        if self._manual_compaction:
            return "compacting"
        if not (self._subagent_wait_label_active or self._subagent_hold_active):
            return None
        count = len(self._live_subagents)
        if not count:
            return "waiting"
        return f"waiting for {count} subagent{'s' if count > 1 else ''}"

    async def _clear_subagent_wait_label(self) -> None:
        """Drop the waiting label if it is currently shown (no-op otherwise)."""
        if not self._subagent_wait_label_active:
            return
        self._subagent_wait_label_active = False
        await self._broadcast_process_label("")

    async def _prune_finished_subagents(self) -> None:
        """Forget the children the watcher already saw finish.

        One indexed query per ``wait`` call — the only moment the count
        is read. Failures are swallowed: an over-count in a status label
        is not worth breaking a turn over.
        """
        if not self._live_subagents:
            return
        try:
            stopped = await sync_to_async(_stopped_subagent_ids)(list(self._live_subagents))
        except Exception:
            logger.warning(
                "Codex: failed to prune finished subagents for session %s",
                self.session_id, exc_info=True,
            )
            return
        for session_id in stopped:
            self._live_subagents.pop(session_id, None)

    async def _try_arm_subagent_hold(self) -> bool:
        """Hold ASSISTANT_TURN at an idle boundary when spawned subagents still run.

        Called wherever the agent would otherwise settle to USER_TURN (turn
        end, command settles, end-of-compaction). Prunes the live set against
        the watcher's view first, so a stale entry can never hold a turn open
        for a child that already finished. Returns ``True`` when the hold was
        armed (state + label broadcast done — the caller must NOT settle to
        USER_TURN), ``False`` when nothing is running (the flag is dropped and
        the caller settles normally).

        Order matters on arming: the ``process_state`` broadcast goes out
        first (the frontend rebuilds its process object on it, wiping any
        label), then the label overrides on top.
        """
        await self._prune_finished_subagents()
        if not self._live_subagents:
            self._subagent_hold_active = False
            return False
        self._subagent_hold_active = True
        logger.info(
            "Codex session %s: idle boundary with %d subagent(s) still running "
            "— holding ASSISTANT_TURN (%s)",
            self.session_id,
            len(self._live_subagents),
            ", ".join(
                f"{tid} ({path})" if path else tid
                for tid, path in self._live_subagents.items()
            ),
        )
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()
        label = self.current_status_label()
        if label is not None:
            await self._broadcast_process_label(label)
        return True

    def in_subagent_hold(self) -> bool:
        """Whether the agent is parked in the subagent hold (no active turn).

        Read by the manager to let hardcoded commands (``/compact``, …)
        through during the hold — from the SDK's perspective the thread is
        as idle as in USER_TURN, only TwiCC's state says otherwise.
        """
        return self._subagent_hold_active

    async def notify_subagents_stopped(self, agent_ids: list[str]) -> None:
        """Relay from the watcher: these spawned subagents have finished.

        The end-of-child signal never reaches the parent's SDK stream (the
        ``FINAL_ANSWER`` lands only in the parent's rollout), so the watcher
        forwards it here when ``check_agent_naturally_stopped`` stamps
        ``last_stopped_at``. Drops the ids from the live set, then:

        - a real turn is running → it owns the state; just refresh the
          in-turn ``wait_agent`` label's count if one is shown;
        - parked in the hold with children left → refresh the label count;
        - parked in the hold with nothing left → release: settle USER_TURN.
        """
        changed = False
        for agent_id in agent_ids:
            if self._live_subagents.pop(agent_id, None) is not None:
                changed = True
        if not changed or self.state == AgentState.DEAD:
            return

        if self._current_turn is not None:
            if self._subagent_wait_label_active:
                label = self.current_status_label()
                if label is not None:
                    await self._broadcast_process_label(label)
            return

        if self._manual_compaction or self._goal_continuation_active:
            # A manual ``/compact`` or a ``/goal`` continuation owns the
            # state right now (both park ASSISTANT_TURN without a
            # ``_current_turn``). Its own settle — ``notify_compacted`` /
            # ``notify_goal_continuation_stopped`` — re-runs the hold
            # decision and will find the pruned set.
            return

        if not self._subagent_hold_active:
            return

        if self._live_subagents:
            label = self.current_status_label()
            if label is not None:
                await self._broadcast_process_label(label)
            return

        logger.info(
            "Codex session %s: last held subagent finished — back to USER_TURN",
            self.session_id,
        )
        self._subagent_hold_active = False
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def compact(self) -> None:
        """Kick off a server-side context compaction on the live thread.

        Fire-and-forget at the SDK layer: ``thread.compact()`` issues the
        ``thread/compact/start`` RPC and returns an empty start ack — never a
        completion. So we model the compaction as a synthetic turn: set the
        ``_manual_compaction`` flag, flip to ``ASSISTANT_TURN`` (the input
        gates), then fire the RPC. The synthetic turn ends in
        :meth:`notify_compacted` when the watcher ingests the ``compacted``
        JSONL line, or in :meth:`_compaction_safety_timeout` if it never lands.

        The ``"compacting"`` status label goes out via the shared
        ``_broadcast_process_label`` channel (same mechanism as Claude Code),
        right after the ``ASSISTANT_TURN`` ``process_state``. Order matters:
        the ``process_state`` is broadcast first (the frontend rebuilds its
        process-state object on it, which would wipe a label set earlier),
        then the label overrides on top — and a placeholder already on screen
        only re-renders the new label thanks to ``workingStatusKey`` (see
        ``recomputeVisualItems``), since the stabilizer ignores ``_parsedContent``.
        """
        logger.info(
            "Codex /compact: starting manual compaction for session %s", self.session_id,
        )
        self._manual_compaction = True
        self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()
        await self._broadcast_process_label("compacting")
        # Give the manual /compact a persistent transcript line. The compaction
        # RPC writes only the ``compacted`` summary (the divider), never a "the
        # user asked to compact" line, so without this the command survives only
        # as the transient optimistic bubble (retired on completion). Inject it
        # BEFORE the RPC so it lands ahead of the summary on disk; best-effort —
        # a failed injection must never block the compaction itself. The compute
        # relabels the injected line into a real user_message (see the Codex
        # compute's ``_injected_command_text``).
        try:
            await self._thread.inject_user_message("/compact")
        except Exception:
            logger.warning(
                "Codex /compact: failed to inject the transcript line for session %s",
                self.session_id, exc_info=True,
            )
        try:
            await self._thread.compact()
        except Exception as e:
            # The RPC failed to even start — unwind the synthetic turn through
            # the same path as a normal completion (back to USER_TURN AND
            # retire the optimistic /compact bubble), then surface as
            # RuntimeError so the WS handler turns it into a clean ``error``
            # frame (its except clause only catches RuntimeError). Never
            # swallowed. The timeout task isn't armed yet, so there's nothing
            # to cancel.
            logger.warning(
                "Codex compact failed to start for session %s: %s",
                self.session_id, e,
            )
            await self.notify_compacted()
            raise RuntimeError(f"Compaction failed: {e}") from e
        # Arm the safety net: end the synthetic turn after a generous delay if
        # the ``compacted`` line never lands (server-side failure).
        self._compaction_timeout_task = asyncio.create_task(
            self._compaction_safety_timeout(),
            name=f"codex-compact-timeout-{self.session_id}",
        )

    async def notify_compacted(self) -> None:
        """End a manual compaction's synthetic turn (called when ``compacted`` lands).

        Routed here from the watcher via the manager. Neutral entry point: it
        is the agent that knows, via ``_manual_compaction``, whether this
        ``compacted`` line concludes a ``/compact`` it triggered (→ leave
        ``ASSISTANT_TURN`` and tell the frontend to drop the optimistic
        ``/compact`` bubble) or an auto-compaction it never owned (→ no-op).
        Also a no-op once ``DEAD`` (a tear-down may race the watcher).
        """
        if not self._manual_compaction or self.state == AgentState.DEAD:
            return
        self._manual_compaction = False
        self._cancel_compaction_timeout()
        logger.info(
            "Codex /compact: compaction finished for session %s — back to USER_TURN",
            self.session_id,
        )
        # A ``/compact`` issued during the subagent hold settles back INTO the
        # hold, not to USER_TURN — the children it was waiting on are still
        # running.
        if not await self._try_arm_subagent_hold():
            self._set_state(AgentState.USER_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()
        # Dedicated signal so the frontend retires the optimistic ``/compact``
        # bubble (no real user_message JSONL line is ever produced for it).
        await self._broadcast_stream_event({
            "type": "manual_compaction_done",
            "session_id": self.session_id,
        })

    async def _compaction_safety_timeout(self) -> None:
        """Force-end a manual compaction if its ``compacted`` line never lands.

        See :data:`COMPACTION_SAFETY_TIMEOUT_S`. A clean conclusion via
        :meth:`notify_compacted` cancels this task; if it fires anyway, the
        flag is still set and we end the synthetic turn ourselves.
        """
        try:
            await asyncio.sleep(COMPACTION_SAFETY_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._manual_compaction:
            logger.warning(
                "Codex /compact: no 'compacted' line after %ds for session %s — "
                "forcing USER_TURN",
                COMPACTION_SAFETY_TIMEOUT_S, self.session_id,
            )
            await self.notify_compacted()

    def _cancel_compaction_timeout(self) -> None:
        """Cancel the pending compaction safety-timeout task, if any.

        No-op if the canceller is the timeout task itself (it just fired and
        is calling back into ``notify_compacted``) — cancelling our own task
        mid-run would raise ``CancelledError`` into this very coroutine.
        """
        task = self._compaction_timeout_task
        self._compaction_timeout_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    # ------------------------------------------------------------------
    # ``/goal`` — set / clear the thread's Codex goal
    # ------------------------------------------------------------------

    async def run_goal_command(self, args: str) -> None:
        """Keep goal completion from settling in the middle of a goal command."""
        monitor = self._goal_monitor
        if monitor is not None:
            monitor.command_done.clear()
        try:
            await self._apply_goal_command(args)
        finally:
            if monitor is not None:
                monitor.command_done.set()
            if self._goal_monitor is not None:
                self._goal_monitor.command_done.set()

    async def _apply_goal_command(self, args: str) -> None:
        """Apply a user ``/goal`` command captured by the manager.

        Two forms, mirroring the Codex CLI surface TwiCC ports:

        - ``/goal clear`` (exact, case-insensitive) — delete the thread's goal.
        - ``/goal <objective>`` — create or update the goal's objective.

        A bare ``/goal`` (no argument) is a usage error: TwiCC's goal surface
        is set/clear only — there is no status view to fall back on (a full
        view is deliberately out of scope here). The CLI's ``/goal clear the
        backlog`` ambiguity does not arise: only the exact token ``clear``
        clears; ``clear <more text>`` is an objective.

        The goal RPCs themselves are instant, but their *effect* on the session
        state differs:

        - ``/goal clear`` prevents future continuation turns. An existing
          turn keeps running until its completion notification arrives.
        - Failure leaves an existing continuation usable; otherwise idle.
        - ``/goal <objective>`` → ``ASSISTANT_TURN``: setting an active goal
          makes Codex autonomously run a "continuation" turn to pursue it (the
          app-server starts AND drives it; TwiCC does not). Marking the session
          ASSISTANT_TURN lets the frontend show it working and stream the
          continuation's messages live. The SDK goal route tracks its physical
          turns and settles after both the goal and the last turn have ended.

        Either way the optimistic ``/goal`` bubble is dropped (see
        :meth:`_settle_after_command`). On failure the agent is left usable and
        the error surfaces as a ``RuntimeError`` for a clean, retry-able frame.
        """
        try:
            objective = args.strip()
            if not objective:
                raise RuntimeError("Usage: /goal <objective>  or  /goal clear")
            if objective.lower() == "clear":
                await self._thread.goal_clear()
                # Clearing prevents future continuations but does not end the active turn.
                settle_state = (
                    AgentState.ASSISTANT_TURN if self._goal_monitor is not None else AgentState.USER_TURN
                )
                self._goal_continuation_active = self._goal_monitor is not None
                # ``/goal clear`` writes nothing to the rollout (the clear RPC
                # only emits a wire-only notification), so — unlike a set, whose
                # transcript line comes free from Codex's goal-context injection
                # — inject a synthetic ``/goal clear`` user message ourselves so
                # the command shows in the transcript. Best-effort: the clear
                # already succeeded; a failed injection just loses the line. The
                # compute relabels the injected line as a user_message.
                try:
                    await self._thread.inject_user_message("/goal clear")
                except Exception as e:
                    logger.warning(
                        "Codex /goal clear: failed to inject transcript line "
                        "for session %s: %s",
                        self.session_id, e,
                    )
            else:
                await self._set_goal(objective)
                # The monitor follows runtime-owned turns without starting
                # them. Keep the session working across continuation boundaries.
                self._goal_continuation_active = True
                settle_state = AgentState.ASSISTANT_TURN
        except Exception as e:
            logger.warning(
                "Codex /goal failed for session %s: %s", self.session_id, e,
            )
            self._goal_continuation_active = self._goal_monitor is not None
            await self._settle_after_command(
                AgentState.ASSISTANT_TURN if self._goal_monitor is not None else AgentState.USER_TURN,
                "goal_command_done",
            )
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"/goal failed: {e}") from e
        if self._goal_monitor is not None and (self._turn_task is None or self._turn_task.done()):
            self._turn_task = asyncio.create_task(self._run_goal_continuation(self._goal_monitor))
        await self._settle_after_command(settle_state, "goal_command_done")

    async def _run_goal_continuation(self, monitor: GoalContinuation) -> None:
        """Stream physical goal turns without starting or interrupting them."""
        stream = monitor.stream()
        try:
            async for event in stream:
                if event.method == "turn/started":
                    self._current_turn = AsyncTurnHandle(self._codex, self._thread.id, event.payload.turn.id)
                    self._current_turn_ready.set()
                elif event.method == "turn/completed":
                    if self._current_turn is not None and self._current_turn.id == event.payload.turn.id:
                        self._current_turn = None
                        self._current_turn_ready.clear()
                await self._handle_stream_event(event)
                if self.state == AgentState.DEAD:
                    return
        except TransportClosedError:
            if self.state != AgentState.DEAD:
                self.last_activity = time.time()
                await self._transition_to_dead()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.state != AgentState.DEAD:
                await self._handle_error(f"Goal continuation failed: {e}", exc=e)
            return
        finally:
            await stream.aclose()
            monitor.close()
            if self._goal_monitor is monitor:
                self._goal_monitor = None
                self._current_turn = None
                self._current_turn_ready.clear()
        await self.notify_goal_continuation_stopped()

    async def _set_goal(self, objective: str) -> None:
        """Create or update the thread's goal from ``/goal <objective>``.

        Reproduces the Codex CLI's replacement semantics on top of the raw
        app-server ``set`` (which only ever edits in place): read the current
        goal; if one exists and is NOT ``active``, clear it first so a brand
        new goal is created; an ``active`` goal is edited in place (objective
        only — its status, budget and counters are preserved). No goal at all
        → ``set`` creates one (``active``, no budget).
        """
        existing = await self._thread.goal_get()
        if existing is not None and existing.status is not ThreadGoalStatus.active:
            await self._thread.goal_clear()
        new_monitor = self._goal_monitor is None
        if new_monitor:
            self._goal_monitor = GoalContinuation(self._codex, self._thread.id)
            self._goal_monitor.command_done.clear()
        try:
            await self._thread.goal_set(objective)
        except BaseException:
            if new_monitor:
                self._goal_monitor.close()
                self._goal_monitor = None
            raise

    async def _settle_after_command(self, state: AgentState, done_event: str) -> None:
        """Move to ``state`` and tell the frontend a hardcoded command is done.

        Shared tail of the ``/goal`` and ``/plan`` actions. ``state`` is
        ``ASSISTANT_TURN`` when the command left something running (a Codex
        goal continuation) or ``USER_TURN`` when it settled idle (goal clear,
        bare ``/plan``, any failure). The transition also clears any optimistic
        STARTING placeholder from a cold-woken session. Then emit
        ``done_event`` (``goal_command_done`` / ``plan_command_done``) so the
        frontend retires the optimistic command bubble — the command opens no
        turn, so no ``user_message`` JSONL line is guaranteed to do it (the
        injected transcript markers are best-effort). No-op once ``DEAD`` (a
        teardown may race this). A USER_TURN settle lands back in the
        subagent hold instead when spawned children are still running (e.g.
        a ``/goal clear`` issued during the hold).
        """
        if self.state == AgentState.DEAD:
            return
        if state != AgentState.USER_TURN or not await self._try_arm_subagent_hold():
            self._set_state(state)
            self.last_activity = time.time()
            await self._notify_state_change()
        await self._broadcast_stream_event({
            "type": done_event,
            "session_id": self.session_id,
        })

    def in_goal_continuation(self) -> bool:
        """Whether the agent is parked in a ``/goal`` continuation ASSISTANT_TURN.

        Read by the manager (``has_goal_continuation``) so the watcher only
        pays for a goal-status DB read when it could actually matter.
        """
        return self._goal_continuation_active

    async def notify_goal_continuation_stopped(self) -> None:
        """Settle a ``/goal`` continuation back to USER_TURN (goal left ``active``).

        Routed from the watcher via the manager when persisted goal evidence
        carries a non-``active`` status (a ``thread_goal_updated`` event or a
        successful Goal-tool result). Mirrors
        :meth:`notify_compacted`'s flag guard: act only on a continuation WE
        armed via ``/goal``, never once ``DEAD``, and never while we're driving
        a real turn ourselves (then ``_run_turn`` owns the state — its own
        ``thread_goal_updated`` lines, e.g. an agent ``update_goal`` mid-turn,
        must not yank us out).
        """
        if not self._goal_continuation_active or self.state == AgentState.DEAD:
            return
        if self._current_turn is not None or self._goal_monitor is not None:
            # The wire stream owns completion while a monitor is attached.
            return
        logger.info(
            "Codex /goal: continuation ended for session %s — back to USER_TURN",
            self.session_id,
        )
        self._goal_continuation_active = False
        # The continuation may have spawned subagents that outlive it: an
        # idle boundary like any other — hold instead of settling idle.
        if await self._try_arm_subagent_hold():
            return
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # ``/plan`` — switch the thread into Plan collaboration mode
    # ------------------------------------------------------------------

    def _build_collaboration_mode(self, mode: ModeKind) -> CollaborationMode:
        """Build the wire collaboration-mode object for this session.

        ``settings.model`` is required by the wire shape — use the session's
        resolved SDK model, the same value every ``_run_turn`` re-passes as a
        per-turn override anyway. ``reasoning_effort`` deliberately stays
        ``null`` so Codex's own mode preset / config decides (the ordinary
        turn effort is NOT the Plan-mode effort), and
        ``developer_instructions: null`` means "use the built-in mode
        instructions".

        Raises ``RuntimeError`` when no model resolves: the bundle reaching a
        live agent is resolved to concrete defaults, so that is settings
        drift, not a user-reachable path — but the wire object cannot omit
        ``model``.
        """
        sdk_model = get_provider_helpers(Provider.CODEX).resolve_sdk_model(
            self.agent_settings.selected_model,
        )
        if not sdk_model:
            raise RuntimeError("No model resolved for this session")
        return CollaborationMode(
            mode=mode,
            settings=CollaborationModeSettings(
                model=sdk_model,
                reasoning_effort=None,
                developer_instructions=None,
            ),
        )

    async def run_plan_command(self, args: str) -> None:
        """Apply a user ``/plan`` command captured by the manager.

        Switches the thread into Codex's Plan *collaboration mode* — a sticky
        per-thread App Server setting applied to subsequent turns — via
        ``thread/settings/update``. Orthogonal to TwiCC's ``permission_mode``
        (the sandbox/approval axis, untouched here) and to the ``update_plan``
        task tool (plan *progress*, not a mode). Enter-only by design: a
        second bare ``/plan`` re-asserts Plan mode instead of toggling back to
        Default — toggle parity needs a reliable current-mode source first
        (cold resumes and backend restarts lose process-local state; another
        client may have switched the thread), a deliberate follow-up.

        Two forms, mirroring the official Codex clients:

        - ``/plan`` — enter Plan mode, run nothing. A durable ``/plan`` user
          line is injected (best-effort, same mechanism as ``/compact``) so
          the switch survives in the transcript, then the agent settles to
          ``USER_TURN``.
        - ``/plan <prompt>`` — enter Plan mode, then run ``<prompt>`` as a
          normal turn; the literal ``/plan`` prefix never reaches the model.
          The turn writes the real ``user_message`` line itself (which also
          retires the optimistic bubble), so no marker is injected.

        Payload choices (see the 2026-07-16 hand-off) live in
        :meth:`_build_collaboration_mode`.

        On failure the agent settles back to ``USER_TURN`` (never left stuck
        in STARTING/ASSISTANT_TURN from a cold wake) and the error surfaces as
        a ``RuntimeError`` for a clean, retry-able frame.
        """
        try:
            await self._thread.update_settings_with_policy(
                collaboration_mode=self._build_collaboration_mode(ModeKind.plan),
            )
        except Exception as e:
            logger.warning(
                "Codex /plan failed for session %s: %s", self.session_id, e,
            )
            await self._settle_after_command(AgentState.USER_TURN, "plan_command_done")
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"/plan failed: {e}") from e

        logger.info(
            "Codex /plan: session %s switched to Plan collaboration mode",
            self.session_id,
        )

        if args:
            # Inline prompt: the sticky mode is set, now run the prompt as an
            # ordinary turn (same transition dance as ``send`` on an idle
            # agent — this also moves a cold-woken agent out of STARTING).
            self._set_state(AgentState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()
            self._schedule_turn(args, None)
            return

        # Bare ``/plan``: give the mode switch a persistent transcript line.
        # The settings RPC writes nothing to the rollout, so without this the
        # command survives only as the transient optimistic bubble. Best-effort
        # — the mode switch already succeeded; a failed injection just loses
        # the line. The compute relabels the injected line into a real
        # user_message (see the Codex compute's ``_injected_command_text``).
        try:
            await self._thread.inject_user_message("/plan")
        except Exception:
            logger.warning(
                "Codex /plan: failed to inject the transcript line for session %s",
                self.session_id, exc_info=True,
            )
        await self._settle_after_command(AgentState.USER_TURN, "plan_command_done")

    async def _prompt_plan_implementation(self) -> None:
        """Post-plan prompt: ask whether to implement the plan just delivered.

        Mirrors the official TUI (``plan_implementation.rs`` +
        ``turn_runtime.rs`` ``maybe_prompt_plan_implementation``): when a turn
        produced a ``plan`` item, ask before settling idle. Runs through the
        standard :class:`PendingRequest` plumbing, so the surface behaves
        exactly like an approval — the agent stays in ASSISTANT_TURN, the
        composer is gated, the answering button shows the sending state, and
        Stop cancels the wait like any other pending request.

        Decisions (validated by the WS layer, ``_build_codex_response``):

        - ``implement`` → switch the thread's collaboration mode back to
          Default (the session's own settings stay untouched — model and
          permission mode remain whatever the user picked), then run the
          TUI's fixed "Implement the plan." message as a normal turn.
        - ``newSession`` → agent-side identical to ``stay``: the frontend
          creates the fresh session seeded with the plan itself (the TUI's
          "clear context and implement", reframed — this session keeps its
          Plan mode and full history).
        - ``stay`` (also the safe default) → settle to ``USER_TURN``; Plan
          mode stays active for further planning.
        """
        request = PendingRequest(
            request_id=f"planImplementation-{uuid.uuid4()}",
            request_type="ask_user_question",
            tool_name="planImplementation",
            tool_input={},
            created_at=time.time(),
        )
        logger.info(
            "Codex plan prompt: asking whether to implement the plan for session %s",
            self.session_id,
        )
        response = await self._await_pending_request(request)

        decision = response.get("decision") if isinstance(response, dict) else None
        if decision == "implement":
            try:
                await self._thread.update_settings_with_policy(
                    collaboration_mode=self._build_collaboration_mode(ModeKind.default),
                )
            except Exception:
                # Do NOT run the implement turn while still in Plan mode —
                # mutating actions are blocked there, the turn would just
                # produce another plan. Settle idle; the user can retry.
                logger.warning(
                    "Codex plan prompt: failed to switch session %s back to "
                    "Default mode — not starting the implement turn",
                    self.session_id, exc_info=True,
                )
                if not await self._try_arm_subagent_hold():
                    self._set_state(AgentState.USER_TURN)
                    self.last_activity = time.time()
                    await self._notify_state_change()
                return
            logger.info(
                "Codex plan prompt: session %s back to Default mode — "
                "starting the implement turn",
                self.session_id,
            )
            await self._run_turn(_PLAN_IMPLEMENTATION_MESSAGE, None)
            return

        # ``stay`` / ``newSession`` (or a malformed response resolved to the
        # safe default): remain in Plan mode, hand control back to the user
        # — unless subagents spawned by earlier turns are still running, in
        # which case the hold takes over as at any idle boundary.
        if await self._try_arm_subagent_hold():
            return
        self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def soft_interrupt(self) -> bool:
        """Interrupt the current turn but keep the thread alive (→ USER_TURN).

        Fires ``turn/interrupt`` on the active turn WITHOUT closing the
        transport. The running ``_run_turn`` consumes the rest of the turn
        stream, which ends on an interrupted ``turn/completed`` (not an
        ``error``, not ``TransportClosedError``), so ``_run_turn`` falls
        through to its normal ``USER_TURN`` transition — thread alive, ready
        for the next message. Contrast with :meth:`interrupt_or_kill`, which
        fires the same interrupt then closes the transport (→ DEAD).

        Any tool in flight when we interrupt gets aborted by Codex with an
        "aborted by user after X.Xs" ``function_call_output`` that carries
        no ``is_error`` flag and no ``Process exited`` trailer — the same
        shape an approval Cancel produces. Unlike a full stop, a soft
        interrupt keeps the session alive, so the frontend's lifecycle
        ``isStaleToolUse`` gate never fires; without an explicit signal the
        tool's spinner would spin forever. So we mark every in-flight tool
        in :attr:`_user_terminated_tool_ids` (exactly like the cancel branch
        of :meth:`_record_decision_outcome`) for the Codex compute to turn
        into ``ToolResultLink.error`` + ``extra.is_terminated``.

        Returns ``True`` if an interrupt was issued, ``False`` if no turn is
        active (best-effort, e.g. the turn just ended).
        """
        turn_handle = self._current_turn
        if turn_handle is None:
            return False
        # Mark in-flight tools BEFORE firing the interrupt: the "aborted by
        # user" outputs may be written and picked up by the watcher during
        # the interrupt round-trip's await, so the termination signal must
        # already be in place. Rolled back below if the interrupt fails.
        marked = self._mark_inflight_tools_user_terminated("User interrupted the turn")
        try:
            await turn_handle.interrupt()
        except Exception as e:
            # Interrupt failed → the turn keeps running and those tools may
            # still complete normally; undo the marks so a later genuine
            # completion isn't mislabelled as interrupted.
            for item_id in marked:
                self._user_terminated_tool_ids.pop(item_id, None)
            logger.warning(
                "Codex soft interrupt failed for session %s: %s", self.session_id, e,
            )
            return False
        logger.info(
            "Soft-interrupting Codex session %s (marked %d in-flight tool(s))",
            self.session_id, len(marked),
        )
        return True

    def _mark_inflight_tools_user_terminated(self, reason: str) -> list[str]:
        """Mark every in-flight cancellable tool as user-terminated.

        Records each in-flight item of a :attr:`_CANCELLABLE_ITEM_TYPES`
        type (held in ``_items_by_id`` between ``item/started`` and
        ``item/completed``) into :attr:`_user_terminated_tool_ids` with
        ``reason``. The Codex compute then surfaces it as the tool result's
        ``error`` + ``extra.is_terminated`` when the matching "aborted by
        user" ``function_call_output`` lands — stopping the orphaned spinner
        the same way an approval Cancel does. Returns the marked item ids
        (used by the caller to roll back on failure).
        """
        marked: list[str] = []
        for item_id, payload in self._items_by_id.items():
            if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
                self._user_terminated_tool_ids[item_id] = reason
                marked.append(item_id)
                logger.debug(
                    "Codex interrupt: marking in-flight tool session=%s "
                    "itemId=%r type=%r",
                    self.session_id, item_id, payload.get("type"),
                )
        return marked

    async def interrupt_or_kill(self, reason: str) -> None:
        """Stop the agent: interrupt the turn, let it unwind, then close.

        Fires ``turn/interrupt`` on the active turn, waits (bounded) for it to
        finalize on its own — the analog of Claude Code's ``wait_for_dead`` —
        then closes the transport and force-kills if needed. Always lands in
        ``DEAD``. Safe to call multiple times.
        """
        if self.state == AgentState.DEAD:
            return

        self.kill_reason = reason
        # Capture the CLI subprocess pid up front: ``codex.close()`` clears the
        # SDK's ``_proc`` handle, so ``get_pid`` returns None afterwards.
        pid = self.get_pid()

        # A manual /compact may be mid-flight — drop its safety-timeout task
        # and flag so a late firing can't touch a dying agent. Same for the
        # subagent hold: a late watcher relay must find nothing to settle,
        # and no snapshot of the dying agent should carry a waiting label.
        self._manual_compaction = False
        self._cancel_compaction_timeout()
        self._subagent_hold_active = False

        # Cancel any in-flight approval BEFORE closing the transport.
        # Cascade per pending approval:
        #   future.cancel() → ``_await_pending_request`` raises CancelledError
        #                  → its ``finally`` clears the dict + broadcasts
        #                  → ``run_coroutine_threadsafe`` re-raises in the
        #                    SDK worker thread
        #                  → our ``_sync_approval_handler`` catches it and
        #                    returns ``default_response_for(method)``
        #                  → worker writes the wire response, releases
        #                    ``_transport_lock``
        # Now ``codex.close()`` can acquire the lock and tear down cleanly.
        # See spec §2.4 + §5.1.
        self._cancel_all_pending_futures()  # inherited from BaseAgent (PR1)

        # Clean turn cancellation when a turn is in flight. We don't gate this
        # on AgentState.ASSISTANT_TURN: depending on race timing, the turn
        # task may have just transitioned to USER_TURN but the next ``send``
        # could re-arm a turn before we observe DEAD. Issuing interrupt is a
        # best-effort no-op if no turn is active server-side.
        turn_handle = self._current_turn
        if turn_handle is not None and not self._force_kill.is_set():
            try:
                await turn_handle.interrupt()
            except Exception as e:
                logger.debug(
                    "turn_handle.interrupt() failed for session %s: %s — "
                    "falling back to transport close",
                    self.session_id, e,
                )

        # Let the interrupted turn unwind on its own so the app-server finalizes
        # its rollout before we tear the transport down: ``_turn_task`` consumes
        # the stream and ends on ``turn/completed`` (interrupted status), the
        # clean analog of Claude Code's post-interrupt ``wait_for_dead``. Bounded
        # and we race the force-kill event so a hard kill bails immediately. We
        # don't cancel ``_turn_task`` here (we close + cancel below if it
        # overruns). Skipped on shutdown and on a force-kill, where speed wins.
        if (
            reason != "shutdown"
            and not self._force_kill.is_set()
            and self._turn_task is not None
            and not self._turn_task.done()
        ):
            force_wait = asyncio.ensure_future(self._force_kill.wait())
            try:
                done, _ = await asyncio.wait(
                    {self._turn_task, force_wait},
                    timeout=self.GRACEFUL_TURN_END_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._turn_task not in done:
                    logger.debug(
                        "Turn didn't unwind (timeout/forced) for session %s — "
                        "closing transport",
                        self.session_id,
                    )
            finally:
                if not force_wait.done():
                    force_wait.cancel()
                    try:
                        await force_wait
                    except asyncio.CancelledError:
                        pass

        # Close the codex transport — the turn task lands in DEAD via
        # TransportClosedError. Idempotent on the SDK side. Bounded so a wedged
        # close can't hang the stop forever; the forced backstop below then
        # takes over. ``close_ok`` gates that backstop: we only touch the pid
        # when the clean teardown did NOT happen, since a cleanly-closed
        # transport may already have freed the pid (avoids PID reuse).
        close_ok = False
        try:
            await asyncio.wait_for(self._codex.close(), timeout=5.0)
            close_ok = True
        except TimeoutError:
            logger.warning(
                "codex.close() timed out for session %s — forcing process kill",
                self.session_id,
            )
        except Exception as e:
            logger.warning(
                "codex.close() failed for session %s: %s", self.session_id, e,
            )

        # Cancel the turn task if it hasn't unwound from
        # TransportClosedError yet (e.g. it was awaiting something other
        # than ``turn_handle.run``).
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "Turn task raised on cancellation for session %s",
                    self.session_id, exc_info=True,
                )

        # A goal task cancelled before its first instruction has no finally
        # block to release its registered route.
        if getattr(self, "_goal_monitor", None) is not None:
            self._goal_monitor.close()
            self._goal_monitor = None
        self._goal_continuation_active = False

        # Drop any item_ids buffered for this session. The agent is going
        # away, so the watcher will never get matching JSONL lines for
        # whatever was streamed and not yet completed (or whatever was
        # completed in the SDK after we tore the transport down). Keeping
        # them would corrupt the FIFO for the next agent on the same id.
        get_streamed_item_registry().clear_session(self.session_id)
        # Drop the side-table — no more turns will read it on this agent.
        self._items_by_id.clear()
        self._user_terminated_tool_ids.clear()
        # Drop any captured pre-patch contents that won't be consumed
        # (the matching ``patch_apply_end`` won't be emitted on a torn-down
        # transport). The TTL would clean them up eventually; this just
        # makes the boundary explicit.
        clear_original_files_for_session(self.session_id)

        # Forced backstop: if the transport didn't tear down cleanly, the Rust
        # ``codex app-server`` subprocess may still be alive — SIGTERM → SIGKILL
        # its process tree so a stop always kills (uniform across providers).
        if not close_ok and pid is not None:
            await self._kill_system_process(pid)

        if self.state != AgentState.DEAD:
            self.last_activity = time.time()
            await self._transition_to_dead()

    async def _handle_error(
        self, error_message: str, exc: Exception | None = None,
    ) -> None:
        """Surface a runtime error as a clean DEAD transition."""
        logger.error(
            "Codex agent for session %s died: %s",
            self.session_id, error_message,
            exc_info=exc,
        )
        self.error = error_message
        self.kill_reason = "error"
        try:
            await self._codex.close()
        except Exception:
            # Already broken — don't pile more errors on top.
            logger.debug(
                "codex.close() during error handling failed for session %s",
                self.session_id, exc_info=True,
            )
        get_streamed_item_registry().clear_session(self.session_id)
        self.last_activity = time.time()
        await self._transition_to_dead()

    # ------------------------------------------------------------------
    # Process introspection
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        """Get the PID of the underlying Codex CLI subprocess.

        ``AsyncCodex._client._sync._proc`` is the :class:`subprocess.Popen`
        wrapping the bundled Rust ``codex app-server`` binary. ``None``
        before ``codex.start()`` runs and again once ``close()`` clears it.

        PRIVATE SDK API — see memory ``reference_codex_sdk_update_procedure.md``
        for the upgrade checklist (this attribute path must hold).
        """
        try:
            client = getattr(self._codex, "_client", None)
            if client is None:
                return None
            sync_client = getattr(client, "_sync", None)
            if sync_client is None:
                return None
            proc = getattr(sync_client, "_proc", None)
            if proc is None:
                return None
            return getattr(proc, "pid", None)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Stream event handling
    # ------------------------------------------------------------------

    @staticmethod
    def _is_unauthorized_error(payload: ErrorNotification) -> bool:
        """Return ``True`` when this terminal error looks like an auth failure.

        Three paths can surface an auth failure depending on where Codex
        catches the 401/403:

        1. ``CodexErrorInfoValue.unauthorized`` — Codex mapped
           ``CodexErr::RefreshTokenFailed`` (token refresh attempted and
           failed permanently). Only reachable when TwiCC handles the
           ``account/chatgptAuthTokens/refresh`` server request — not
           wired today, so this path doesn't fire in practice.
        2. ``HttpConnectionFailedCodexErrorInfo`` /
           ``ResponseStreamConnectionFailedCodexErrorInfo`` with
           ``http_status_code in {401, 403}`` — Codex classified the
           network error and exposes the status directly.
        3. ``"status 40[13]"`` in the message — covers the common
           ``CodexErr::UnexpectedStatus(401)`` case (e.g. session resume
           on an expired token). Codex upstream has no dedicated mapping
           for ``UnexpectedStatus`` in ``to_codex_protocol_error``, so it
           falls through to ``Other`` and the HTTP status is only visible
           in the formatted message (``"unexpected status {status}: ..."``).

        A false positive flips the topbar red briefly — the next
        ``codex login status`` poll (≤30s) rectifies it. A false negative
        means the user keeps seeing a green topbar while every request
        fails, which is worse, so we prefer being a touch aggressive.
        """
        info = payload.error.codex_error_info
        if info is not None:
            root = info.root
            if root is CodexErrorInfoValue.unauthorized:
                return True
            if isinstance(root, HttpConnectionFailedCodexErrorInfo):
                if root.http_connection_failed.http_status_code in (401, 403):
                    return True
            elif isinstance(root, ResponseStreamConnectionFailedCodexErrorInfo):
                if root.response_stream_connection_failed.http_status_code in (401, 403):
                    return True
        return bool(_AUTH_STATUS_IN_MESSAGE.search(payload.error.message))

    async def _handle_stream_event(self, event: Any) -> None:
        """Translate one Codex SDK stream notification into TwiCC's WS protocol.

        We handle two item kinds today; everything else flows through the
        JSONL → watcher path. The mapping to the Claude-shared
        ``stream_block_*`` wire format is:

        - ``item/started`` on an ``agentMessage``
            → ``stream_block_start`` (``block_type="text"``, ``block_index=0``,
              ``message_id`` = Codex ``item_id``).
        - ``item/agentMessage/delta`` → ``stream_block_delta``.
        - ``item/completed`` on an ``agentMessage``
            → ``stream_block_stop`` + ``stream_block_end`` (``uuid`` =
              ``item_id``). Pushes the ``item_id`` onto the FIFO registry
              so the watcher can stamp the matching SessionItem.

        - ``item/reasoning/summaryPartAdded``
            → ``stream_block_start`` on the first summary part we see for
              this item (``block_type="thinking"``, ``block_index=0``).
              Subsequent summary parts emit a ``stream_block_delta`` with
              text ``"\\n\\n"`` instead, so the streaming view shows the
              same single concatenated reasoning card the JSONL line will
              render once flushed (the post-flush ``Reasoning.vue`` joins
              every ``summary_text`` with ``\\n\\n``). We deliberately
              ignore ``item/started`` on reasoning items because OpenAI
              sometimes returns an empty summary — we only want to paint
              a card when there's actual text to display, which is what
              ``summaryPartAdded`` signals.
        - ``item/reasoning/summaryTextDelta`` → ``stream_block_delta``
              (always on ``block_index=0`` — the summary_index of the
              specific part is hidden from the wire so the frontend sees
              one continuous block).
        - ``item/completed`` on a ``reasoning`` with non-empty summary
            → ``stream_block_stop`` + ``stream_block_end`` on
              ``block_index=0``, then a single registry push (the JSONL
              persists the whole reasoning as a single line, so a single
              pop on the watcher side will pair them).
        """
        # Mirror the raw SDK notification into the per-session debug log
        # before any local processing. No-op when TWICC_DEBUG is unset.
        log_stream_event(self.session_id, event)

        # Refresh last_activity on every stream event so the
        # ASSISTANT_TURN inactivity timeout only fires on a truly silent
        # SDK (mirrors ClaudeCodeAgent._run_message_loop, where each
        # message coming out of the SDK touches last_activity).
        self.last_activity = time.time()

        method = event.method
        payload = event.payload

        # Subagent traffic filter: Codex routes every notification from
        # a spawned subagent through the parent's SDK transport (single
        # Rust process, single notification stream). Each notification
        # carries its origin ``thread_id`` — for subagent items it
        # differs from ``self.session_id``. We must drop those events
        # here for two reasons:
        #
        #   1. ``stream_block_*`` broadcasts go out tagged with
        #      ``self.session_id``, so painting the subagent's text
        #      into the parent conversation would surface content the
        #      user already sees through the spawn_agent tool card +
        #      the dedicated subagent tab.
        #   2. The :class:`StreamedItemRegistry` FIFO is keyed by
        #      ``self.session_id``. A subagent push would consume the
        #      slot meant for the parent's next ``agent_message``,
        #      leaving the streaming placeholder stuck — the
        #      ``stream_uuid`` stamped on the parent's SessionItem ends
        #      up being a subagent item id the frontend never painted a
        #      placeholder for, so retirement-by-uuid never matches.
        #
        # Notifications without a ``thread_id`` (none today, but keep
        # the guard defensive against future SDK additions) flow
        # through unchanged.
        payload_thread_id = getattr(payload, "thread_id", None)
        if payload_thread_id is not None and payload_thread_id != self.session_id:
            return

        if (
            method == "item/autoApprovalReview/completed"
            and isinstance(payload, ItemGuardianApprovalReviewCompletedNotification)
        ):
            if payload.review.status is GuardianApprovalReviewStatus.approved:
                action_pair = _guardian_action_to_core(payload)
                if (
                    action_pair is not None
                    and action_pair[0] == self._auto_review_retry_action
                ):
                    # The exact action approved by the user was retried before
                    # this turn ended. Do not launch the fallback continuation.
                    self._auto_review_retry_after_turn = False
                    self._auto_review_retry_action = None
            await self._handle_auto_review_completed(payload)
            return

        if method == "error" and isinstance(payload, ErrorNotification):
            # ``EventMsg::Error`` upstream → ``will_retry: false`` and a
            # ``turn/completed`` with ``status: failed`` follows on the same
            # stream. ``EventMsg::StreamError`` → ``will_retry: true`` and is
            # a transient SSE retry the SDK handles on its own — don't kill
            # the agent or flip auth state on those.
            if payload.will_retry:
                return

            is_auth_error = self._is_unauthorized_error(payload)
            if is_auth_error:
                logger.error(
                    "Codex auth error for session %s: %s",
                    self.session_id, payload.error.message,
                )
            else:
                logger.error(
                    "Codex terminal error for session %s: %s (codex_error_info=%r)",
                    self.session_id,
                    payload.error.message,
                    payload.error.codex_error_info,
                )

                # Codex exposes the terminal error only on the live app-server
                # stream; unlike Claude Code it writes no error item to the
                # rollout. Persist a private no-turn item before closing the
                # transport so the watcher can rewrite it into a durable
                # ``api_error`` transcript row with one-click recovery. The RPC
                # is local and normally immediate, but error teardown must not
                # hang if the app-server is already unhealthy.
                error_info = payload.error.codex_error_info
                error_type = (
                    error_info.model_dump(mode="json", by_alias=True)
                    if error_info is not None
                    else None
                )
                marker = build_provider_error_marker(CodexProviderError(
                    turn_id=payload.turn_id,
                    message=payload.error.message,
                    error_type=error_type,
                ))
                try:
                    await asyncio.wait_for(
                        self._thread.inject_user_message(marker),
                        timeout=5,
                    )
                except Exception:
                    logger.warning(
                        "Could not persist terminal Codex error for session %s",
                        self.session_id,
                        exc_info=True,
                    )

            # Mirror Claude's order of ops on ``authentication_failed``:
            # cancel pending futures → set DEAD → notify → (auth only:
            # flip global auth state) → close transport. The flip happens
            # after the DEAD broadcast so the frontend's topbar reflects
            # the new auth state without waiting for the next
            # ``codex login status`` poll (up to 30s away).
            self._cancel_all_pending_futures()
            self.error = payload.error.message
            self.kill_reason = "auth_required" if is_auth_error else "error"
            self.last_activity = time.time()
            await self._transition_to_dead()

            if is_auth_error:
                from twicc.providers.codex.auth import mark_unauthenticated_and_broadcast
                await mark_unauthenticated_and_broadcast()

            # Drop side-tables tied to this session so a future agent on
            # the same id starts clean (same cleanup as ``interrupt_or_kill``).
            get_streamed_item_registry().clear_session(self.session_id)
            self._items_by_id.clear()
            self._user_terminated_tool_ids.clear()
            clear_original_files_for_session(self.session_id)

            # Tear the transport down. The stream loop in ``_run_turn``
            # will observe ``TransportClosedError`` next and exit; the
            # DEAD guard there (and at the post-loop tail) prevents the
            # state from being overwritten by USER_TURN.
            try:
                await self._codex.close()
            except Exception:
                logger.debug(
                    "codex.close() after error notification failed for session %s",
                    self.session_id, exc_info=True,
                )
            return

        if method == "item/started":
            # Capture the raw inner payload first so any ``itemId`` is indexed,
            # regardless of item kind. ``fileChange`` approvals later in the
            # turn read this side-table to grab the diff.
            item = getattr(payload, "item", None)
            if item is not None:
                inner = getattr(item, "root", item)
                item_id = getattr(inner, "id", None)
                if item_id:
                    self._items_by_id[item_id] = inner.model_dump(
                        mode="json", by_alias=True,
                    )
                # ``fileChange`` items announce an upcoming ``apply_patch``.
                # Read the pre-patch contents synchronously so the watcher
                # can splice them into the persisted ``patch_apply_end``
                # for full-file diffs on the frontend. See
                # :func:`_capture_original_files_for_apply_patch` for the
                # timing rationale.
                if getattr(inner, "type", None) == "fileChange":
                    _capture_original_files_for_apply_patch(inner, self.session_id)
                # Multi-agent v2 bookkeeping: ``subAgentActivity`` tracks
                # which children are alive, a starting ``wait``
                # collaboration call turns that into the status label.
                if getattr(inner, "type", None) == _SUB_AGENT_ACTIVITY_ITEM_TYPE:
                    self._note_sub_agent_activity(inner)
                elif _is_collab_wait_call(inner):
                    await self._refresh_subagent_wait_label()

            # Existing agent-message streaming logic — only this kind paints
            # a live ``stream_block_start`` event today; other kinds flow
            # through the JSONL → watcher path.
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_start",
                "session_id": self.session_id,
                "message_id": agent_msg.id,
                "block_index": 0,
                "block_type": "text",
            })
            return

        if method == "item/agentMessage/delta":
            item_id = getattr(payload, "item_id", None)
            delta = getattr(payload, "delta", None)
            if not item_id or delta is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_delta",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "text",
                "text": delta,
            })
            return

        if method == "item/reasoning/summaryPartAdded":
            item_id = getattr(payload, "item_id", None)
            summary_index = getattr(payload, "summary_index", None)
            if not item_id or summary_index is None:
                return
            indices = self._reasoning_summary_indices.setdefault(item_id, set())
            if summary_index in indices:
                # Already started; the SDK shouldn't fire ``summaryPartAdded``
                # twice for the same (item_id, summary_index), but the guard
                # keeps us idempotent if it ever did.
                return
            first_part = not indices
            indices.add(summary_index)
            if first_part:
                await self._broadcast_stream_event({
                    "type": "stream_block_start",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                })
            else:
                # Subsequent summary part — paint a paragraph separator into
                # the same block instead of opening a new one, so the user
                # sees the same single Reasoning card the JSONL will render.
                await self._broadcast_stream_event({
                    "type": "stream_block_delta",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                    "text": "\n\n",
                })
            return

        if method == "item/reasoning/summaryTextDelta":
            item_id = getattr(payload, "item_id", None)
            delta = getattr(payload, "delta", None)
            if not item_id or delta is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_delta",
                "session_id": self.session_id,
                "message_id": item_id,
                "block_index": 0,
                "block_type": "thinking",
                "text": delta,
            })
            return

        if method == "item/completed":
            item = getattr(payload, "item", None)
            if item is None:
                return
            inner = getattr(item, "root", item)
            item_type = getattr(inner, "type", None)
            # The side-table entry is no longer needed once the item is
            # finalized (see ``_items_by_id`` in __init__). Pop is
            # idempotent — items we never saw started don't show up here.
            item_id_for_cleanup = getattr(inner, "id", None)
            if item_id_for_cleanup:
                self._items_by_id.pop(item_id_for_cleanup, None)

            # Multi-agent v2: the ``wait`` collaboration call returned, so
            # the parent is working again — drop the waiting label. The
            # matching ``subAgentActivity`` completion carries the same
            # payload as its ``item/started``, and the bookkeeping is
            # idempotent, so re-running it here is harmless.
            if item_type == _SUB_AGENT_ACTIVITY_ITEM_TYPE:
                self._note_sub_agent_activity(inner)
                return
            if _is_collab_wait_call(inner):
                await self._clear_subagent_wait_label()
                return

            if item_type == "plan":
                # A Plan collaboration-mode turn just delivered its final
                # answer (streamed via ``item/plan/delta``, persisted as the
                # ``<proposed_plan>`` response_item the compute relabels).
                # Arm the post-turn "Implement this plan?" prompt — consumed
                # by ``_run_turn`` once the turn completes.
                self._plan_item_this_turn = True
                return

            if item_type == "agentMessage":
                item_id = inner.id
                await self._broadcast_stream_event({
                    "type": "stream_block_stop",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "text",
                })
                await self._broadcast_stream_event({
                    "type": "stream_block_end",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "text",
                    "uuid": item_id,
                })
                # Hand the item_id off to the watcher so it can stamp the
                # matching SessionItem when the JSONL line lands.
                get_streamed_item_registry().push(self.session_id, item_id)
                return

            if item_type == "reasoning":
                item_id = inner.id
                # If we never received a ``summaryPartAdded`` for this item
                # the set is empty (or absent) — typical when OpenAI didn't
                # produce a summary at all, in which case the JSONL line
                # carries ``summary: []`` and the watcher classifies it as
                # SYSTEM. No SessionItem to retire, no push needed.
                indices = self._reasoning_summary_indices.pop(item_id, set())
                if not indices:
                    return
                # Single block per reasoning item (block_index=0) regardless
                # of how many summary parts we saw — see ``summaryPartAdded``
                # above for the rationale.
                await self._broadcast_stream_event({
                    "type": "stream_block_stop",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                })
                await self._broadcast_stream_event({
                    "type": "stream_block_end",
                    "session_id": self.session_id,
                    "message_id": item_id,
                    "block_index": 0,
                    "block_type": "thinking",
                    "uuid": item_id,
                })
                # One JSONL line per reasoning item, so a single registry
                # push regardless of how many summary parts streamed.
                get_streamed_item_registry().push(self.session_id, item_id)
                return

    async def _handle_auto_review_completed(
        self,
        payload: ItemGuardianApprovalReviewCompletedNotification,
    ) -> None:
        """Escalate a Guardian denial to TwiCC's ordinary approval UI.

        Approved and non-terminal reviews need no intervention. For a denial,
        hold the exact native event on this coroutine's stack while the client
        sees only display details. An approval invokes Codex's native override,
        then steers the still-active turn to retry. If the turn ended server-side
        while the user was deciding, ``_run_turn`` opens one continuation after
        it drains the queued completion event.
        """
        context = _guardian_denial_context(payload)
        if context is None:
            return
        event, display = context
        if event["action"] == self._auto_review_retry_action:
            # A retry did happen, but Guardian denied it again. The new card is
            # now authoritative; do not also launch the older fallback turn.
            self._auto_review_retry_after_turn = False
            self._auto_review_retry_action = None
        request = PendingRequest(
            request_id=f"auto-review:{payload.review_id}",
            request_type="tool_approval",
            tool_name="autoReviewDenial",
            tool_input=display,
            created_at=time.time(),
            permission_suggestions=None,
        )
        response = await self._await_pending_request(request)
        if response.get("decision") != "accept":
            logger.info(
                "User kept Codex Auto-review denial %s for session %s",
                payload.review_id, self.session_id,
            )
            return

        await self._thread.approve_guardian_denied_action(event)
        logger.info(
            "User manually approved Codex Auto-review denial %s for session %s",
            payload.review_id, self.session_id,
        )

        # Keep a one-shot continuation armed even when steering succeeds: the
        # app-server may accept a late steer just as the turn is closing. A
        # matching approved-review notification clears it; otherwise the turn
        # tail consumes it exactly once.
        self._auto_review_retry_after_turn = True
        self._auto_review_retry_action = event["action"]

        turn_handle = self._current_turn
        if turn_handle is not None:
            try:
                await turn_handle.steer([TextInput(_AUTO_REVIEW_RETRY_PROMPT)])
                return
            except Exception as exc:
                # The server keeps running while TwiCC waits for the click; in
                # the common slow-response case its turn is already complete,
                # even though our stream loop has not consumed that notification
                # yet. Continue below rather than treating this expected race as
                # a provider failure.
                logger.info(
                    "Could not steer completed Codex turn after Auto-review "
                    "approval for session %s (%s); scheduling a continuation",
                    self.session_id, exc,
                )

    # ------------------------------------------------------------------
    # Approval handlers (sync ↔ async bridge)
    # ------------------------------------------------------------------

    def _sync_approval_handler(self, method: str, params: dict | None) -> dict:
        """Logging wrapper around the actual approval bridge.

        Records the inbound SDK request + the response we hand back, then
        delegates to :meth:`_sync_approval_handler_impl` for the real
        bridge logic. Both ``log_*`` calls are no-ops when TWICC_DEBUG is
        unset, so production keeps the original code path overhead.

        Wrapping at this layer (rather than instrumenting each return
        inside the impl) guarantees we capture every response — including
        the safe defaults returned on cancellation, missing event loop,
        bridge crash, etc. Those failure-mode responses are exactly what
        debug logs need to surface.
        """
        log_approval_request(self.session_id, method, params)
        response = self._sync_approval_handler_impl(method, params)
        log_approval_response(self.session_id, method, response)
        return response

    def _sync_approval_handler_impl(self, method: str, params: dict | None) -> dict:
        """Called by the SDK from a worker thread (via ``asyncio.to_thread``).

        Bridges the SDK's blocking expectation (``Callable -> dict``) to our
        async ``_await_pending_request``. Approvals we don't own (dynamic
        tool calls, OAuth refresh) delegate to the captured SDK default. Cancellation —
        typically from ``_cancel_all_pending_futures()`` on kill — is
        converted into a safe wire default so the SDK's read loop doesn't
        hang.

        See spec §2.4 + §5.1 for the full call chain.
        """
        if not is_approval_method(method):
            # Defensive fallback: log + delegate. The SDK default returns
            # ``{}`` for unknown methods which might break Codex; for the 2
            # approval methods it knows it returns ``{"decision": "accept"}``,
            # which is safer than crashing the read loop. PR2a does not
            # naturally exercise this path — the warning is here to flag
            # an unsupported server request the day it shows up.
            logger.warning(
                "Unhandled Codex server request method=%r (delegating to SDK default)",
                method,
            )
            return self._sdk_default_approval_handler(method, params)

        if self._loop is None or self._loop.is_closed():
            # Approval before ``start()`` ran, or after the loop was torn
            # down. Either way we can't bridge to async; return a safe
            # wire default so the SDK doesn't hang.
            logger.error(
                "Codex approval received before loop init or after close: method=%r",
                method,
            )
            return default_response_for(method)

        coro = self._async_approval_handler(method, params)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result()
        except (asyncio.CancelledError, concurrent.futures.CancelledError):
            # Pending future was cancelled (kill, transport teardown). The
            # awaiter's ``finally`` already dropped the entry; we just have
            # to give the SDK something to send back to Codex so the JSON-RPC
            # response is well-formed and the read loop unblocks.
            #
            # We catch BOTH classes because the asyncio coroutine raises
            # ``asyncio.CancelledError`` (a BaseException subclass since
            # Python 3.8) but ``run_coroutine_threadsafe(...).result()``
            # repackages it as ``concurrent.futures.CancelledError`` (an
            # Exception subclass) on the worker-thread side.
            return default_response_for(method)
        except Exception as exc:
            # Any other failure of the bridge — log loudly and fall back to
            # a safe default. Re-raising would leak the exception into the
            # SDK's worker thread which would then crash the entire read
            # loop.
            logger.error(
                "Codex approval bridge failed for method=%r: %s",
                method, exc, exc_info=True,
            )
            return default_response_for(method)

    async def _async_approval_handler(
        self, method: str, params: dict | None,
    ) -> dict:
        """Main-loop side of the bridge.

        Build a ``PendingRequest`` (enriched with the streamed item payload
        for ``fileChange``), broadcast it via ``_await_pending_request``,
        and return the dict the frontend sent back through
        ``manager.resolve_pending_request``.

        The WS layer is responsible for shape-validating the response into
        a Codex-compliant dict (``CodexWSHandler._build_codex_response``)
        — at this point we just pass it through.
        """
        item_id_for_log = params.get("itemId") if params else None
        logger.debug(
            "Codex approval request: session=%s method=%s itemId=%s",
            self.session_id, method, item_id_for_log,
        )
        enriched_params = self._enrich_params_with_item_payload(method, params)
        # yolo auto-approves MCP tool CALLS client-side. yolo's Granular approval
        # policy forwards the tool-call approval to us so genuine elicitations
        # get through (a plain ``never`` auto-approved the call but auto-declined
        # elicitations — see permission_modes._YOLO_APPROVAL); yolo's contract is
        # still "no prompt to run a tool". Genuine elicitations
        # (elicitationForm/Url) are NOT caught here — they reach the user. Gated
        # on the trust-clamped mode so an untrusted project (yolo clamped away)
        # never silently auto-approves.
        if (
            is_mcp_tool_call_approval(method, enriched_params)
            and await self._effective_permission_mode() == "yolo"
        ):
            logger.info(
                "Auto-approving Codex MCP tool-call for session %s (yolo — no "
                "prompt for tool execution)", self.session_id,
            )
            return approve_mcp_tool_call_response()
        if await self._should_auto_approve_work_dir(method, enriched_params):
            logger.info(
                "Auto-approving Codex %s for session %s — targets only system "
                "work dirs", method, self.session_id,
            )
            return auto_approve_response_for(method)
        request = make_pending_request(method, enriched_params)
        response = await self._await_pending_request(request)
        # Record refusals in _user_terminated_tool_ids so the Codex compute
        # can surface them as ToolResultLink.error when the matching
        # function_call_output lands in the JSONL.
        self._record_decision_outcome(method, params, response)
        return response

    def _enrich_params_with_item_payload(
        self, method: str, params: dict | None,
    ) -> dict | None:
        """For ``fileChange``, attach the streamed item payload (the diff).

        Other methods pass through unchanged. We do this BEFORE constructing
        the PendingRequest so ``tool_input`` carries the join data (under
        ``_item_payload``) and the frontend doesn't have to do a side fetch.

        The underscore prefix on ``_item_payload`` signals it's a synthetic
        side-band field, not from the Codex schema.
        """
        if method != "item/fileChange/requestApproval":
            return params
        if not params:
            return params
        item_id = params.get("itemId")
        if not item_id:
            return params
        payload = self._items_by_id.get(item_id)
        if payload is None:
            return params
        return {**params, "_item_payload": payload}

    async def _effective_permission_mode(self) -> str | None:
        """The session's permission mode after the untrusted trust clamp.

        Mirrors :meth:`_run_turn`: an untrusted project re-clamps to the
        untrusted-allowed set (``yolo`` stripped), so a caller
        gating on a permissive mode (e.g. auto-approving MCP tool calls in
        ``yolo``) sees the SAME mode the turn's Codex policy was built from —
        never the raw, pre-clamp bundle value that would bypass the trust floor.
        """
        mode = self.agent_settings.permission_mode
        if self._untrusted:
            from twicc.core.services.trust import clamp_permission_mode_for_untrusted

            mode = await sync_to_async(clamp_permission_mode_for_untrusted)(
                Provider.CODEX, mode,
            )
        return mode

    async def _should_auto_approve_work_dir(
        self, method: str, enriched_params: dict | None,
    ) -> bool:
        """True if this approval targets ONLY the session's system work dirs.

        Mirrors the Claude path: extract the request's full footprint, require
        it fully enumerable and entirely inside ``self._work_dirs``, then gate
        on a live untrusted read (auto-approval is off in untrusted projects).
        Containment is checked first so the trust DB read only happens for a
        request already proven in-scope.
        """
        paths, fully_known = extract_codex_approval_paths(method, enriched_params)
        if not self._targets_only_work_dirs(paths, fully_known):
            return False
        from twicc.core.services.trust import project_is_untrusted

        return not await sync_to_async(project_is_untrusted)(self.project_id)

    # Item types from ``_items_by_id`` that produce a ``function_call_output``
    # in the JSONL (and therefore can be matched by ``_user_terminated_tool_ids``).
    # We keep this set tight to avoid marking dead entries on cancel turn —
    # the lookup is harmless if we over-include, but the explicit list
    # documents which kinds we expect to surface as ``ToolResultLink``.
    # The SDK item-types stream as camelCase per ``model_dump(by_alias=True)``;
    # values here match what ``_items_by_id`` will hold.
    _CANCELLABLE_ITEM_TYPES: ClassVar[frozenset[str]] = frozenset({
        "commandExecution",
        "fileChange",
    })

    def _record_decision_outcome(
        self,
        method: str,
        params: dict | None,
        response: dict,
    ) -> None:
        """If the user refused the request, mark the matching itemId(s).

        Called from ``_async_approval_handler`` immediately after
        ``_await_pending_request`` returns. Three refusal shapes:

        - ``commandExecution`` / ``fileChange`` with ``decision == "decline"``:
          mark just the current itemId.
        - ``commandExecution`` / ``fileChange`` with ``decision == "cancel"``:
          mark the current itemId AND every in-flight item in
          ``_items_by_id`` whose type is in :attr:`_CANCELLABLE_ITEM_TYPES`
          (Codex will abort the whole turn — each in-flight tool gets
          an "aborted by user" output line).
        - ``permissions`` with empty granted profile:
          mark just the current itemId.

        ``ELICITATION_METHOD`` / ``REQUEST_USER_INPUT_METHOD`` are a no-op —
        see the early return below.

        ``response`` is the dict the frontend sent through
        ``resolve_pending_request``; ``params`` are the original Codex
        request params that contain ``itemId``. No-op if either is missing
        an itemId we can route from.
        """
        if method in (ELICITATION_METHOD, REQUEST_USER_INPUT_METHOD):
            # No side-table marking: an MCP tool call the user declines or
            # cancels surfaces in the JSONL as a ``mcp_tool_call_end`` with a
            # ``{"Err": …}`` result, which the compute already converts into
            # an errored ToolResultLink (``_mcp_tool_call_end_error``) — the
            # spinner stops on its own. Generic elicitations / user-input
            # forms have no tool item at all.
            return
        if not params:
            return
        item_id = params.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            return

        if method == "item/permissions/requestApproval":
            granted = response.get("permissions")
            if not granted:
                # Empty granted profile = user refused permissions.
                self._user_terminated_tool_ids[item_id] = "User refused permissions"
                logger.debug(
                    "Codex decision recorded: session=%s itemId=%s "
                    "outcome=permissions_denied reason=%r",
                    self.session_id, item_id, "User refused permissions",
                )
            else:
                logger.debug(
                    "Codex decision recorded: session=%s itemId=%s "
                    "outcome=permissions_granted (no marking)",
                    self.session_id, item_id,
                )
            return

        # command / file
        decision = response.get("decision")
        if decision == "decline":
            self._user_terminated_tool_ids[item_id] = "User denied this action"
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=decline reason=%r",
                self.session_id, item_id, "User denied this action",
            )
            return
        if decision == "cancel":
            self._user_terminated_tool_ids[item_id] = "User cancelled this turn"
            # Also mark every other in-flight function-call item. The user
            # asked for "tous les tools qui n'ont pas été terminés doivent
            # être marqués" — we iterate _items_by_id which holds every
            # item that emitted item/started but not item/completed yet.
            siblings_marked: list[str] = []
            for other_id, payload in self._items_by_id.items():
                if other_id == item_id:
                    continue
                if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
                    self._user_terminated_tool_ids[other_id] = "User cancelled this turn"
                    siblings_marked.append(other_id)
                    logger.debug(
                        "Codex cancel: marking sibling session=%s itemId=%r type=%r",
                        self.session_id, other_id, payload.get("type"),
                    )
            logger.debug(
                "Codex decision recorded: session=%s itemId=%s "
                "outcome=cancel reason=%r siblings_marked=%s",
                self.session_id, item_id,
                "User cancelled this turn", siblings_marked,
            )
            return
        # Anything else (notably "approve" on command/file) is a pass-through
        # with no map entry — trace it so the smoke-test grep shows the
        # full approve/deny picture for each itemId.
        logger.debug(
            "Codex decision recorded: session=%s itemId=%s "
            "outcome=%s (no marking)",
            self.session_id, item_id, decision,
        )
