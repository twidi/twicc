"""Browser presentation metadata must preserve Codex's English contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc.agent import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.goal_continuation import GoalContinuation
from twicc.providers.codex.agent.hardcoded_commands import HardcodedCommand
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.compute import (
    _code_mode_output_error,
    _exit_code_error_from_output,
    _mcp_tool_call_end_error,
    _patch_apply_error,
    _status_error_text,
)
from twicc.providers.codex.ws import CodexWSHandler


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_invalid_dismissal_publishes_metadata_without_changing_error_contract():
    consumer = SimpleNamespace(send_json=AsyncMock())
    await CodexWSHandler(consumer)._handle_dismiss_async_question({})
    payload = consumer.send_json.call_args.args[0]
    assert payload['message'] == 'Invalid async question dismissal'
    assert payload['code'] == 'async_questions_invalid'
    assert payload['ui_message'] == {'key': 'backend.codex.asyncQuestionDismissalInvalid', 'params': {}}


@pytest.mark.anyio
async def test_busy_command_refusal_retains_runtime_error_and_command_parameter():
    manager = CodexAgentManager()
    manager._agents['s'] = SimpleNamespace(
        state=AgentState.ASSISTANT_TURN,
        in_goal_continuation=lambda: False,
        in_subagent_hold=lambda: False,
    )
    with pytest.raises(RuntimeError) as caught:
        await manager._dispatch_hardcoded_command('s', 'p', '/tmp', HardcodedCommand('compact', ''), None)
    assert str(caught.value) == 'Cannot run /compact while the assistant is working'
    assert caught.value.ui_message == {
        'key': 'backend.codex.commandWhileWorking', 'params': {'command': 'compact'},
    }


def test_status_metadata_tracks_current_child_count_and_compaction():
    agent = object.__new__(CodexAgent)
    agent._manual_compaction = False
    agent._subagent_wait_label_active = True
    agent._subagent_hold_active = False
    agent._live_subagents = {'a': '/root/a', 'b': '/root/b'}
    assert agent.current_status_label() == 'waiting for 2 subagents'
    assert agent.current_status_label_ui_message() == {
        'key': 'backend.codex.waitingForSubagents', 'params': {'count': 2}, 'count': 2,
    }
    agent._live_subagents.clear()
    assert agent.current_status_label_ui_message()['key'] == 'backend.codex.waiting'
    agent._manual_compaction = True
    assert agent.current_status_label_ui_message()['key'] == 'backend.codex.compacting'
    agent._manual_compaction = False
    agent._subagent_wait_label_active = False
    assert agent.current_status_label_ui_message() is None


@pytest.mark.anyio
async def test_closed_goal_route_refuses_steer_with_localizable_reason():
    route = object.__new__(GoalContinuation)
    route.closed = True
    with pytest.raises(RuntimeError) as caught:
        await route.steer([])
    assert str(caught.value) == 'Cannot steer: goal continuation has ended'
    assert caught.value.ui_message['key'] == 'backend.codex.goalContinuationEnded'


@pytest.mark.parametrize(('factory', 'english', 'key', 'params'), [
    (lambda: _patch_apply_error({'type': 'FileChange', 'status': 'declined'}),
     'Patch declined', 'backend.codex.patchDeclined', {}),
    (lambda: _patch_apply_error({'type': 'FileChange', 'status': 'failed'}),
     'Patch failed', 'backend.codex.patchFailed', {}),
    (lambda: _mcp_tool_call_end_error({'type': 'McpToolCall', 'error': {}}),
     'Tool error', 'backend.codex.toolError', {}),
    (lambda: _status_error_text('shutdown'),
     'Subagent shut down', 'backend.codex.subagentShutdown', {}),
    (lambda: _status_error_text('not_found'),
     'Subagent not found', 'backend.codex.subagentNotFound', {}),
    (lambda: _exit_code_error_from_output('Process exited with code 7'),
     'Exit code 7', 'backend.codex.exitCode', {'code': 7}),
    (lambda: _code_mode_output_error('Script failed\nWall time 0.1 seconds\nOutput:\nraw'),
     'Script failed', 'backend.codex.scriptFailed', {}),
])
def test_generated_tool_error_carries_presentation_without_changing_text(factory, english, key, params):
    error = factory()
    assert error == english
    assert error.ui_message == {'key': key, 'params': params}


def test_upstream_tool_diagnostics_remain_verbatim_without_presentation_metadata():
    for error in (
        _patch_apply_error({'type': 'FileChange', 'status': 'failed', 'stderr': 'upstream IO error'}),
        _mcp_tool_call_end_error({'type': 'McpToolCall', 'error': {'message': 'upstream MCP error'}}),
        _status_error_text({'errored': 'upstream agent error'}),
    ):
        assert error.startswith('upstream ')
        assert not hasattr(error, 'ui_message')


@pytest.mark.parametrize('response', [{}, {'batch_ids': ['missing'], 'answers': []}])
def test_invalid_async_answer_metadata_survives_manager_error_conversion(response):
    from twicc.agent import SendDeliveryError
    from twicc.providers.codex.async_questions import validate_question_answers

    with pytest.raises(ValueError) as caught:
        validate_question_answers({}, response)
    error = SendDeliveryError(str(caught.value), code=str(caught.value),
                              ui_message=getattr(caught.value, 'ui_message', None))
    assert error.code in {'async_questions_invalid', 'async_questions_stale'}
    assert error.ui_message is not None
    assert error.ui_message['key'] in {'backend.codex.asyncQuestionsInvalid', 'backend.codex.asyncQuestionsStale'}


def test_rollout_migration_progress_describes_done_and_set_aside_counts():
    from twicc.providers.codex.background_compute import CodexComputeCoordinator

    coordinator = object.__new__(CodexComputeCoordinator)
    coordinator._legacy_ids = {'a', 'b', 'c'}
    coordinator.initial_ids = {'a', 'b', 'c'}
    coordinator.initial_ready = {'a'}
    coordinator._set_aside = {'b'}
    detail = coordinator._detail()
    assert detail == 'Migrating Codex legacy sessions (1 / 3, 1 set aside)'
    assert detail.ui_message == {
        'key': 'backend.codex.migrationProgressSetAside',
        'params': {'done': 1, 'total': 3, 'setAside': 1},
    }
    coordinator._set_aside.clear()
    assert coordinator._detail().ui_message == {
        'key': 'backend.codex.migrationProgress', 'params': {'done': 1, 'total': 3},
    }
    coordinator._legacy_ids.clear()
    assert coordinator._detail() is None


@pytest.mark.anyio
@pytest.mark.parametrize(('entries', 'parts', 'key'), [
    ([SimpleNamespace(mode='inline')], [], 'backend.codex.attachmentNativePartsMismatch'),
    ([SimpleNamespace(mode='inline')], [SimpleNamespace(kind='pdf')], 'backend.codex.attachmentNativeUnsupported'),
])
async def test_attachment_shape_errors_have_source_metadata(entries, parts, key):
    from twicc.providers.codex.provider_errors import CodexUiRuntimeError

    content = SimpleNamespace(manifest=SimpleNamespace(entries=entries), native_parts=parts)
    with pytest.raises(ValueError) as caught:
        await object.__new__(CodexAgent)._build_attachment_turn_input(content)
    error = CodexUiRuntimeError(f'Failed to open turn: {caught.value}',
                                'backend.codex.openTurnFailed', {'diagnostic': str(caught.value)}, cause=caught.value)
    assert error.ui_message['key'] == key


def test_unsupported_runtime_platform_has_source_metadata(monkeypatch):
    from twicc.providers.codex import runtime

    monkeypatch.setattr(runtime.platform, 'system', lambda: 'TestOS')
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'test-arch')
    with pytest.raises(runtime.CodexRuntimeUnsupportedPlatform) as caught:
        runtime._platform_tag()
    assert str(caught.value).startswith("No Codex binary for system='TestOS' machine='test-arch'.")
    assert caught.value.ui_message['key'] == 'backend.codex.runtimeUnsupportedPlatform'
    assert caught.value.ui_message['params']['system'] == 'TestOS'
