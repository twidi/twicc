"""Fine-grained Codex SDK wrappers preserve the approval reviewer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai_codex import TextInput
from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    CollaborationMode,
    ModeKind,
    SandboxMode,
    SandboxPolicy,
    Settings,
    ThreadGoalClearParams,
    ThreadGoalSetParams,
    WorkspaceWriteSandboxPolicy,
)

from twicc.providers.codex.sdk_wrappers import (
    TwiccAsyncCodex,
    TwiccAsyncThread,
    service_tier_from_fast_mode,
)


def _mock_codex() -> TwiccAsyncCodex:
    codex = TwiccAsyncCodex()
    codex._ensure_initialized = AsyncMock()
    codex._client = AsyncMock()
    return codex


def test_thread_start_forwards_auto_review() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_start.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )

        await codex.thread_start_with_policy(
            sandbox=SandboxMode.workspace_write,
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.auto_review,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_start.await_args.args[0]
    assert params.approvals_reviewer is ApprovalsReviewer.auto_review


def test_fast_mode_service_tier_mapping() -> None:
    assert service_tier_from_fast_mode(True) == "priority"
    assert service_tier_from_fast_mode(False) == "default"
    assert service_tier_from_fast_mode(None) is None


def test_thread_start_forwards_service_tier() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_start.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )
        await codex.thread_start_with_policy(service_tier="priority")
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_start.await_args.args[0]
    assert params.service_tier == "priority"


def test_thread_resume_forwards_user_reviewer() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_resume.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )

        await codex.thread_resume_with_policy(
            "thread-id",
            sandbox=SandboxMode.workspace_write,
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.user,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_resume.await_args.args[1]
    assert params.approvals_reviewer is ApprovalsReviewer.user


def test_thread_resume_forwards_standard_service_tier() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.thread_resume.return_value = SimpleNamespace(
            thread=SimpleNamespace(id="thread-id"),
        )
        await codex.thread_resume_with_policy("thread-id", service_tier="default")
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.thread_resume.await_args.args[1]
    assert params.service_tier == "default"


def test_thread_set_name_does_not_resume_rollout() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        await codex.thread_set_name("thread-id", "New title")
        return codex

    codex = asyncio.run(scenario())
    codex._client.thread_set_name.assert_awaited_once_with(
        "thread-id",
        "New title",
    )
    codex._client.thread_resume.assert_not_awaited()


def test_turn_forwards_auto_review() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.turn_start.return_value = SimpleNamespace(
            turn=SimpleNamespace(id="turn-id"),
        )
        thread = TwiccAsyncThread(codex, "thread-id")

        await thread.turn_with_policy(
            [TextInput("test")],
            approval_policy=AskForApproval("on-request"),
            approvals_reviewer=ApprovalsReviewer.auto_review,
        )
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.turn_start.await_args.kwargs["params"]
    assert params.approvals_reviewer is ApprovalsReviewer.auto_review


def test_turn_forwards_fast_service_tier() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.turn_start.return_value = SimpleNamespace(
            turn=SimpleNamespace(id="turn-id"),
        )
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.turn_with_policy([TextInput("test")], service_tier="priority")
        return codex

    codex = asyncio.run(scenario())
    params = codex._client.turn_start.await_args.kwargs["params"]
    assert params.service_tier == "priority"


def test_approve_guardian_denied_action_uses_native_rpc() -> None:
    event = {
        "id": "review-1",
        "turn_id": "turn-id",
        "started_at_ms": 1,
        "completed_at_ms": 2,
        "status": "denied",
        "action": {
            "type": "network_access",
            "target": "https://example.com",
            "host": "example.com",
            "protocol": "https",
            "port": 443,
        },
    }

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.approve_guardian_denied_action(event)
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/approveGuardianDeniedAction",
        {"threadId": "thread-id", "event": event},
    )


def test_goal_set_sends_user_origin_with_objective() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.request.return_value = SimpleNamespace(goal=None)
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.goal_set("ship it")
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/goal/set",
        {"threadId": "thread-id", "objective": "ship it", "origin": "user"},
    )
    # The wire payload must be valid for the generated request model.
    params = ThreadGoalSetParams.model_validate(call.args[1])
    assert params.origin.value == "user"


def test_goal_clear_sends_user_origin() -> None:
    async def scenario() -> tuple[TwiccAsyncCodex, bool]:
        codex = _mock_codex()
        codex._client.request.return_value = SimpleNamespace(cleared=True)
        thread = TwiccAsyncThread(codex, "thread-id")
        return codex, await thread.goal_clear()

    codex, cleared = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert cleared is True
    assert call.args[:2] == (
        "thread/goal/clear",
        {"threadId": "thread-id", "origin": "user"},
    )
    params = ThreadGoalClearParams.model_validate(call.args[1])
    assert params.origin.value == "user"


def test_goal_get_sends_no_origin() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        codex._client.request.return_value = SimpleNamespace(goal=None)
        await TwiccAsyncThread(codex, "thread-id").goal_get()
        return codex

    codex = asyncio.run(scenario())
    assert codex._client.request.await_args.args[:2] == ("thread/goal/get", {"threadId": "thread-id"})


def test_thread_settings_update_uses_native_rpc() -> None:
    sandbox_policy = SandboxPolicy(root=WorkspaceWriteSandboxPolicy(
        type="workspaceWrite",
        network_access=True,
        writable_roots=["/data/artifacts/thread-id", "/data/scratch/thread-id"],
    ))

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.update_settings_with_policy(sandbox_policy=sandbox_policy)
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/settings/update",
        {
            "threadId": "thread-id",
            "sandboxPolicy": {
                "excludeSlashTmp": False,
                "excludeTmpdirEnvVar": False,
                "networkAccess": True,
                "type": "workspaceWrite",
                "writableRoots": [
                    "/data/artifacts/thread-id",
                    "/data/scratch/thread-id",
                ],
            },
        },
    )


def test_thread_settings_update_service_tier_uses_native_rpc() -> None:
    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.update_settings_with_policy(service_tier="priority")
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/settings/update",
        {"threadId": "thread-id", "serviceTier": "priority"},
    )


def test_thread_settings_update_collaboration_mode_payload() -> None:
    """The Plan-mode payload keeps ``developer_instructions`` as explicit null.

    ``settings.developer_instructions: null`` means "use Codex's built-in
    instructions for this collaboration mode" — an ``exclude_none`` dump would
    drop the key and change the protocol meaning. Same for
    ``reasoning_effort: null`` (the mode preset / config decides).
    """
    collaboration_mode = CollaborationMode(
        mode=ModeKind.plan,
        settings=Settings(
            model="gpt-5.6",
            reasoning_effort=None,
            developer_instructions=None,
        ),
    )

    async def scenario() -> TwiccAsyncCodex:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        await thread.update_settings_with_policy(
            collaboration_mode=collaboration_mode,
        )
        return codex

    codex = asyncio.run(scenario())
    call = codex._client.request.await_args
    assert call.args[:2] == (
        "thread/settings/update",
        {
            "threadId": "thread-id",
            "collaborationMode": {
                "mode": "plan",
                "settings": {
                    "model": "gpt-5.6",
                    "reasoning_effort": None,
                    "developer_instructions": None,
                },
            },
        },
    )


def test_thread_settings_update_requires_a_setting() -> None:
    async def scenario() -> None:
        codex = _mock_codex()
        thread = TwiccAsyncThread(codex, "thread-id")
        with pytest.raises(ValueError):
            await thread.update_settings_with_policy()

    asyncio.run(scenario())


def test_turn_start_forwards_native_client_message_id():
    async def run():
        codex = _mock_codex()
        codex._client.turn_start.return_value = SimpleNamespace(turn=SimpleNamespace(id="turn"))
        codex._client._subscribe_turn_notifications = Mock()
        thread = TwiccAsyncThread(codex, "thread")
        await thread.turn_with_policy([TextInput("hello")], client_user_message_id="submission-1")
        return codex._client.turn_start.await_args.kwargs["params"]
    assert asyncio.run(run()).client_user_message_id == "submission-1"


def test_native_steer_wrapper_uses_generated_params():
    from twicc.providers.codex import sdk_wrappers
    async def run():
        codex = _mock_codex()
        handle = SimpleNamespace(_codex=codex, thread_id="thread", id="turn")
        await sdk_wrappers.steer_with_message_id(handle, [TextInput("hello")], client_user_message_id="submission-1")
        return codex._client.request.await_args
    call = asyncio.run(run())
    assert call.args[0] == "turn/steer"
    assert call.args[1]["clientUserMessageId"] == "submission-1"
    assert call.args[1]["expectedTurnId"] == "turn"


def test_goal_steer_forwards_native_client_message_id():
    from twicc.providers.codex.agent.goal_continuation import GoalContinuation
    async def run():
        codex = _mock_codex()
        route = GoalContinuation.__new__(GoalContinuation)
        route.codex = codex
        route.thread_id = "thread"
        route.closed = False
        route.state = SimpleNamespace(current_turn=lambda: "goal-turn")
        await route.steer([TextInput("hello")], client_user_message_id="submission-1")
        return codex._client.request.await_args
    call = asyncio.run(run())
    assert call.args[0] == "turn/steer"
    assert call.args[1]["clientUserMessageId"] == "submission-1"
    assert call.args[1]["expectedTurnId"] == "goal-turn"
