"""TwiCC wrappers for the Codex SDK that preserve fine-grained approval/sandbox.

The public ``openai_codex`` SDK exposes ``AsyncCodex.thread_start``,
``AsyncCodex.thread_resume`` and ``AsyncThread.turn`` that only accept the
coarse :class:`openai_codex.ApprovalMode` enum (``deny_all`` /
``auto_review``) for permission control. TwiCC needs the full granularity
of :class:`AskForApproval` so the user-facing presets map exactly to their
intended wire combinations. The wrappers also carry ``approvals_reviewer``
explicitly: ordinary interactive modes route requests to TwiCC's user bridge,
while ``auto_review`` routes eligible requests to Codex's reviewer agent.

These subclasses add ``*_with_policy`` methods that bypass the high-level
mapping and build the typed JSON-RPC params directly. The returned
runtime objects (``AsyncThread`` subclass, ``AsyncTurnHandle``,
streaming, errors) are otherwise the upstream SDK ones — only the
parameter-build step is replaced.

PRIVATE SDK API: the implementation reaches into
``openai_codex._inputs._normalize_run_input`` / ``_to_wire_input`` and
into ``AsyncCodex._ensure_initialized`` / ``AsyncCodex._client``. The
goal helpers and ``inject_user_message`` additionally call
``AsyncCodex._client.request`` with raw ``thread/goal/*`` /
``thread/inject_items`` / ``thread/settings/update`` method strings — these
app-server RPCs have no generated SDK wrapper (only their notifications are
generated). See the memory ``reference_codex_sdk_update_procedure.md`` for the
upgrade checklist (these attribute paths must hold).
"""

from __future__ import annotations

from typing import Any

from openai_codex import AsyncCodex, AsyncThread, AsyncTurnHandle, RunInput
from openai_codex._inputs import _normalize_run_input, _to_wire_input
from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    CollaborationMode,
    ReasoningEffort,
    SandboxMode,
    SandboxPolicy,
    ThreadApproveGuardianDeniedActionResponse,
    ThreadGoal,
    ThreadResumeParams,
    ThreadSetNameResponse,
    ThreadStartParams,
    TurnStartParams,
)
from pydantic import BaseModel, ConfigDict


CODEX_FAST_SERVICE_TIER = "priority"
CODEX_STANDARD_SERVICE_TIER = "default"


def service_tier_from_fast_mode(fast_mode: bool | None) -> str | None:
    """Translate TwiCC's provider-neutral Fast switch to Codex's service tier."""
    if fast_mode is None:
        return None
    return CODEX_FAST_SERVICE_TIER if fast_mode else CODEX_STANDARD_SERVICE_TIER


class _ThreadGoalEnvelope(BaseModel):
    """``thread/goal/{get,set}`` response — the thread's goal, or ``None``."""

    model_config = ConfigDict(populate_by_name=True)
    goal: ThreadGoal | None = None


class _ThreadGoalClearResponse(BaseModel):
    """``thread/goal/clear`` response — whether a goal was actually removed."""

    model_config = ConfigDict(populate_by_name=True)
    cleared: bool = False


class _ThreadInjectItemsResponse(BaseModel):
    """``thread/inject_items`` response — an empty envelope."""

    model_config = ConfigDict(populate_by_name=True)


class _ThreadSettingsUpdateResponse(BaseModel):
    """``thread/settings/update`` response — an empty envelope."""

    model_config = ConfigDict(populate_by_name=True)


class TwiccAsyncThread(AsyncThread):
    """``AsyncThread`` with ``turn_with_policy`` for fine-grained per-turn overrides."""

    initial_model: str | None = None

    async def turn_with_policy(
        self,
        input: RunInput,
        *,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        effort: ReasoningEffort | None = None,
        model: str | None = None,
        service_tier: str | None = None,
    ) -> AsyncTurnHandle:
        """Start a turn with fine-grained approval/sandbox overrides.

        Bypasses :class:`openai_codex.ApprovalMode` so the per-turn
        override carries the same preset granularity as the start /
        resume call. ``approvals_reviewer`` is explicit so switching permission
        mode can also switch between TwiCC's user bridge and Codex Auto-review.
        """
        await self._codex._ensure_initialized()
        wire_input = _to_wire_input(_normalize_run_input(input))
        params = TurnStartParams(
            thread_id=self.id,
            input=wire_input,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            effort=effort,
            model=model,
            sandbox_policy=sandbox_policy,
            service_tier=service_tier,
        )
        turn = await self._codex._client.turn_start(
            self.id, wire_input, params=params,
        )
        return AsyncTurnHandle(self._codex, self.id, turn.turn.id)

    # ------------------------------------------------------------------
    # Goal RPCs (``thread/goal/{get,set,clear}``)
    #
    # Not part of the generated SDK surface — only the goal *notifications*
    # are generated. TwiCC drives the app-server RPCs directly through the
    # private client, mirroring how the SDK's own ``compact()`` reaches
    # ``thread/compact/start``. These are thin single-RPC helpers; the
    # set-vs-clear-vs-replace orchestration lives on ``CodexAgent``.
    # ------------------------------------------------------------------

    async def goal_get(self) -> ThreadGoal | None:
        """Read this thread's goal via ``thread/goal/get`` (``None`` when unset)."""
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/get",
            {"threadId": self.id},
            response_model=_ThreadGoalEnvelope,
        )
        return response.goal

    async def approve_guardian_denied_action(self, event: dict[str, Any]) -> None:
        """Record one exact Guardian-denied action as manually approved.

        Codex injects a developer authorization marker into the thread; when
        the action is retried, Auto-review can match that exact payload without
        treating similar actions as authorized. The app-server exposes this as
        a raw RPC but the Python SDK does not currently wrap it.
        """
        await self._codex._ensure_initialized()
        await self._codex._client.request(
            "thread/approveGuardianDeniedAction",
            {"threadId": self.id, "event": event},
            response_model=ThreadApproveGuardianDeniedActionResponse,
        )

    async def update_settings_with_policy(
        self,
        *,
        sandbox_policy: SandboxPolicy | None = None,
        collaboration_mode: CollaborationMode | None = None,
        service_tier: str | None = None,
    ) -> None:
        """Update this loaded thread's next-turn settings without resuming it.

        The app-server's ``thread/settings/update`` RPC changes the loaded
        thread in place, without starting a turn or adding transcript items.
        Three next-turn settings go through it today; each is sent only when
        passed, so callers stay independent:

        - ``sandbox_policy`` — a new Codex thread gets its canonical id only
          after ``thread/start``, while TwiCC's scratch/artifact roots are
          scoped to that id. Calling ``thread/resume`` immediately is invalid
          before Codex has indexed the fresh rollout; this RPC is the intended
          hot path for binding the id-scoped writable roots.
        - ``collaboration_mode`` — switch the thread's collaboration mode
          (e.g. ``/plan`` → Plan) for subsequent turns. Serialized WITHOUT
          ``exclude_none``: ``settings.developer_instructions: null`` means
          "use Codex's built-in instructions for this mode" and must stay an
          explicit JSON ``null`` on the wire — dropping the key would change
          the protocol meaning. Same for ``reasoning_effort: null`` (let the
          mode preset / config decide).
        - ``service_tier`` — switch Standard/Fast routing for subsequent turns
          and Codex-owned continuations without creating a transcript item.
        """
        if sandbox_policy is None and collaboration_mode is None and service_tier is None:
            raise ValueError("update_settings_with_policy: nothing to update")
        params: dict[str, Any] = {"threadId": self.id}
        if sandbox_policy is not None:
            params["sandboxPolicy"] = sandbox_policy.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        if collaboration_mode is not None:
            params["collaborationMode"] = collaboration_mode.model_dump(
                by_alias=True,
                exclude_none=False,
                mode="json",
            )
        if service_tier is not None:
            params["serviceTier"] = service_tier
        await self._codex._ensure_initialized()
        await self._codex._client.request(
            "thread/settings/update",
            params,
            response_model=_ThreadSettingsUpdateResponse,
        )

    async def goal_set(self, objective: str) -> ThreadGoal | None:
        """Create or edit this thread's goal via ``thread/goal/set``.

        Sends only ``objective`` (+ ``threadId``): on a thread with no goal
        this creates one (``status=active``, no budget, counters at 0); on an
        existing goal it edits the objective in place, keeping status, budget
        and counters. Budget and status are deliberately never sent — TwiCC's
        ``/goal`` surface is objective-only.
        """
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/set",
            {"threadId": self.id, "objective": objective},
            response_model=_ThreadGoalEnvelope,
        )
        return response.goal

    async def goal_clear(self) -> bool:
        """Delete this thread's goal via ``thread/goal/clear``.

        Returns the server's ``cleared`` flag (``False`` when there was no
        goal to remove).
        """
        await self._codex._ensure_initialized()
        response = await self._codex._client.request(
            "thread/goal/clear",
            {"threadId": self.id},
            response_model=_ThreadGoalClearResponse,
        )
        return response.cleared

    async def inject_user_message(self, text: str) -> None:
        """Append a synthetic user message to the thread via ``thread/inject_items``.

        Records a ``message`` (role=user) item in the thread's rollout AND
        model-visible history WITHOUT starting or steering a turn (Codex
        ``inject_no_new_turn`` + ``flush_rollout``), so it is safe even while a
        turn runs. TwiCC uses this for two durable transcript gaps:

        - ``/goal clear`` and ``/compact`` write no "the user asked" rollout
          line, so their injected item is relabelled as a real ``user_message``;
        - terminal provider errors exist only on the live app-server stream,
          so their private marker is rewritten into an ``api_error`` item.

        See ``CodexSessionCompute._transform_inline_provider`` for both
        rewrites. Like the goal RPCs, ``thread/inject_items`` has no generated
        SDK wrapper.
        """
        await self._codex._ensure_initialized()
        await self._codex._client.request(
            "thread/inject_items",
            {
                "threadId": self.id,
                "items": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    }
                ],
            },
            response_model=_ThreadInjectItemsResponse,
        )


class TwiccAsyncCodex(AsyncCodex):
    """``AsyncCodex`` whose ``*_with_policy`` methods return :class:`TwiccAsyncThread`."""

    async def thread_set_name(
        self,
        thread_id: str,
        name: str,
    ) -> ThreadSetNameResponse:
        """Rename a persisted thread without loading its rollout.

        The high-level SDK exposes ``set_name`` only on ``AsyncThread``, which
        tempts callers to run an unnecessary ``thread/resume`` just to obtain
        that object. Its typed client already exposes the underlying
        ``thread/name/set`` RPC, and the app-server applies it directly to the
        metadata store, so forward to that method instead.
        """
        await self._ensure_initialized()
        return await self._client.thread_set_name(thread_id, name)

    async def thread_start_with_policy(
        self,
        *,
        sandbox: SandboxMode | None = None,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        cwd: str | None = None,
        config: dict[str, Any] | None = None,
        model: str | None = None,
        ephemeral: bool | None = None,
        developer_instructions: str | None = None,
        service_tier: str | None = None,
    ) -> TwiccAsyncThread:
        """Start a new thread with fine-grained approval/sandbox.

        ``developer_instructions`` lands in the thread rollout as a
        ``developer``-role message and is replayed on every subsequent
        turn, including after ``thread_resume`` (the Codex protocol keeps
        the original block when resume does not re-pass the field).
        """
        await self._ensure_initialized()
        params = ThreadStartParams(
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            config=config,
            cwd=cwd,
            developer_instructions=developer_instructions,
            ephemeral=ephemeral,
            model=model,
            sandbox=sandbox,
            service_tier=service_tier,
        )
        started = await self._client.thread_start(params)
        thread = TwiccAsyncThread(self, started.thread.id)
        thread.initial_model = getattr(started, "model", None)
        return thread

    async def thread_resume_with_policy(
        self,
        thread_id: str,
        *,
        sandbox: SandboxMode | None = None,
        approval_policy: AskForApproval | None = None,
        approvals_reviewer: ApprovalsReviewer | None = None,
        cwd: str | None = None,
        config: dict[str, Any] | None = None,
        model: str | None = None,
        service_tier: str | None = None,
    ) -> TwiccAsyncThread:
        """Resume an existing thread with fine-grained approval/sandbox.

        The resumed thread's model is sticky server-side; leave ``model``
        unset to keep whichever model the thread was started with.
        """
        await self._ensure_initialized()
        params = ThreadResumeParams(
            thread_id=thread_id,
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            config=config,
            cwd=cwd,
            model=model,
            sandbox=sandbox,
            service_tier=service_tier,
        )
        resumed = await self._client.thread_resume(thread_id, params)
        return TwiccAsyncThread(self, resumed.thread.id)
