"""Resumable Claude subagents: turn boundaries, orphan notifications, stale stops.

Recent CLIs make background agents resumable: a finished agent re-wakes when
its own background child completes or when the parent messages it, and the CLI
emits a fresh terminal ``<task-notification>`` at every stop. Three
consequences covered here:

- the subagent's own file carries the running/idle boundaries
  (``subagent_turn_boundary``): ``end_turn`` = idle, a CLI-injected wake-up
  (string content + ``origin.kind``) = working again;
- a stop that follows a self-wake has no triggering tool_use in the parent
  session, so its notification carries no ``<tool-use-id>`` — the launching
  tool_use is recovered from the ``agent-<task_id>.meta.json`` sidecar
  (orphan rewrite), else the entry classifies as SYSTEM via ``origin.kind``;
- the live stop step (``run_stop_step``) must not re-freeze as "stopped" a
  subagent whose own file already recorded newer activity (monotonic guard);
  it still reports the stop, unstamped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import orjson
import pytest

from twicc.core.agent_runs import RunStateExclude, run_stop_step
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AgentLink, Project, Session, ToolResultLink
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.compute_base import AgentStoppedUpdate

_T_SPAWN = datetime(2026, 1, 1, 11, 55, 0, tzinfo=UTC)
_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
_T2 = datetime(2026, 1, 1, 12, 10, 0, tzinfo=UTC)


def _orphan_notification_xml(task_id: str) -> str:
    return (
        '<task-notification>\n'
        f'<task-id>{task_id}</task-id>\n'
        '<output-file>/tmp/tasks/out.output</output-file>\n'
        '<status>completed</status>\n'
        '<summary>Agent "Runner" finished</summary>\n'
        '<result>All gates green.</result>\n'
        '<usage><subagent_tokens>42</subagent_tokens></usage>\n'
        '</task-notification>'
    )


class TestSubagentTurnBoundary:
    """Pure classification of a subagent's own lines (no DB)."""

    def setup_method(self):
        self.compute = get_compute()

    def test_end_turn_is_idle(self):
        parsed = {
            'type': 'assistant',
            'message': {'role': 'assistant', 'stop_reason': 'end_turn', 'content': []},
        }
        assert self.compute.subagent_turn_boundary(parsed) is True

    def test_tool_use_stop_reason_is_not_a_boundary(self):
        parsed = {
            'type': 'assistant',
            'message': {'role': 'assistant', 'stop_reason': 'tool_use', 'content': []},
        }
        assert self.compute.subagent_turn_boundary(parsed) is None

    def test_streaming_line_without_stop_reason_is_not_a_boundary(self):
        parsed = {
            'type': 'assistant',
            'message': {'role': 'assistant', 'stop_reason': None, 'content': []},
        }
        assert self.compute.subagent_turn_boundary(parsed) is None

    @pytest.mark.parametrize('kind', ['coordinator', 'task-notification'])
    def test_injected_wake_up_is_working_again(self, kind):
        parsed = {
            'type': 'user',
            'origin': {'kind': kind},
            'message': {'role': 'user', 'content': '[SYSTEM NOTIFICATION] child done'},
        }
        assert self.compute.subagent_turn_boundary(parsed) is False

    def test_initial_prompt_without_origin_is_not_a_boundary(self):
        parsed = {
            'type': 'user',
            'message': {'role': 'user', 'content': 'RUNTIME INPUTS ...'},
        }
        assert self.compute.subagent_turn_boundary(parsed) is None

    def test_tool_result_list_content_is_not_a_boundary(self):
        parsed = {
            'type': 'user',
            'message': {
                'role': 'user',
                'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_x', 'content': 'ok'}],
            },
        }
        assert self.compute.subagent_turn_boundary(parsed) is None

    def test_non_message_entries_are_ignored(self):
        assert self.compute.subagent_turn_boundary({'type': 'queue-operation'}) is None


@pytest.fixture
def parent_session(db):
    project = Project.objects.create(id='test-project-subagent-lifecycle')
    return Session.objects.create(
        id='parent-session-lifecycle',
        project=project,
        provider=Provider.CLAUDE_CODE,
        file_path='test-project-subagent-lifecycle/parent-session-lifecycle.jsonl',
    )


@pytest.fixture
def sidecar_dir(parent_session, provider_home):
    """Point the Claude home at tmp and return the parent's subagents/ dir."""
    subagents = (
        provider_home.claude / 'projects'
        / 'test-project-subagent-lifecycle' / 'parent-session-lifecycle' / 'subagents'
    )
    subagents.mkdir(parents=True)
    return subagents


class TestOrphanNotificationRewrite:
    """Orphan ``<task-notification>`` (no <tool-use-id>) → sidecar lookup."""

    def _transform(self, parsed: dict, session_id: str) -> str | None:
        return get_compute()._transform_inline_provider(
            parsed, session_id=session_id, line_num=100,
        )

    def _user_entry(self, task_id: str) -> dict:
        return {
            'type': 'user',
            'sessionId': 'parent-session-lifecycle',
            'origin': {'kind': 'task-notification'},
            'message': {'role': 'user', 'content': _orphan_notification_xml(task_id)},
        }

    def test_user_message_orphan_resolves_via_sidecar(self, parent_session, sidecar_dir):
        task_id = 'a3de5a994208b0ed5'
        (sidecar_dir / f'agent-{task_id}.meta.json').write_bytes(
            orjson.dumps({'toolUseId': 'toolu_launch_42', 'agentType': 'general-purpose'})
        )
        parsed = self._user_entry(task_id)
        result = self._transform(parsed, parent_session.id)

        assert result is not None
        block = parsed['message']['content'][0]
        assert block['type'] == 'tool_result'
        assert block['tool_use_id'] == 'toolu_launch_42'
        assert block['content'] == 'All gates green.'
        assert 'is_error' not in block
        # agentId pairs the subagent card; isAsync must NOT be set (this is
        # never a launch ack — it must not flip a foreground link).
        assert parsed['toolUseResult'] == {'agentId': task_id}
        assert parsed['twiccOriginalContent'].startswith('<task-notification>')

    def test_orphan_with_failed_status_is_an_error_result(self, parent_session, sidecar_dir):
        task_id = 'afailedagent000000'
        (sidecar_dir / f'agent-{task_id}.meta.json').write_bytes(
            orjson.dumps({'toolUseId': 'toolu_launch_43'})
        )
        parsed = self._user_entry(task_id)
        parsed['message']['content'] = parsed['message']['content'].replace(
            '<status>completed</status>', '<status>killed</status>'
        )
        self._transform(parsed, parent_session.id)
        assert parsed['message']['content'][0]['is_error'] is True

    def test_orphan_without_sidecar_is_left_untouched(self, parent_session, sidecar_dir):
        parsed = self._user_entry('amissingsidecar000')
        assert self._transform(parsed, parent_session.id) is None
        assert isinstance(parsed['message']['content'], str)

    def test_notification_with_tool_use_id_keeps_the_existing_path(
        self, parent_session, sidecar_dir
    ):
        """A <tool-use-id> notification never consults the sidecar."""
        task_id = 'anormalagent000000'
        xml = _orphan_notification_xml(task_id).replace(
            f'<task-id>{task_id}</task-id>',
            f'<task-id>{task_id}</task-id>\n<tool-use-id>toolu_direct_7</tool-use-id>',
        )
        parsed = self._user_entry(task_id)
        parsed['message']['content'] = xml
        self._transform(parsed, parent_session.id)
        block = parsed['message']['content'][0]
        assert block['tool_use_id'] == 'toolu_direct_7'
        assert parsed['toolUseResult'] == {'agentId': task_id, 'isAsync': True}

    def test_attachment_orphan_resolves_via_sidecar(self, parent_session, sidecar_dir):
        task_id = 'aattachorphan00000'
        (sidecar_dir / f'agent-{task_id}.meta.json').write_bytes(
            orjson.dumps({'toolUseId': 'toolu_launch_44'})
        )
        parsed = {
            'type': 'attachment',
            'sessionId': 'parent-session-lifecycle',
            'attachment': {
                'type': 'queued_command',
                'commandMode': 'task-notification',
                'prompt': _orphan_notification_xml(task_id),
            },
        }
        result = self._transform(parsed, parent_session.id)

        assert result is not None
        assert parsed['type'] == 'user'
        block = parsed['message']['content'][0]
        assert block['tool_use_id'] == 'toolu_launch_44'
        assert parsed['toolUseResult'] == {'agentId': task_id}
        assert 'attachment' not in parsed
        assert 'twiccOriginalEntry' in parsed


class TestTaskNotificationOriginClassification:
    """Un-rewritten notification entries classify as SYSTEM, not USER_MESSAGE."""

    def setup_method(self):
        self.compute = get_compute()

    def test_orphan_notification_string_is_system(self):
        parsed = {
            'type': 'user',
            'origin': {'kind': 'task-notification'},
            'message': {'role': 'user', 'content': _orphan_notification_xml('a1')},
        }
        assert self.compute.compute_item_kind(parsed) == ItemKind.SYSTEM

    def test_subagent_wake_notification_is_system(self):
        parsed = {
            'type': 'user',
            'origin': {'kind': 'task-notification'},
            'message': {
                'role': 'user',
                'content': '[SYSTEM NOTIFICATION - NOT USER INPUT]\nchild finished',
            },
        }
        assert self.compute.compute_item_kind(parsed) == ItemKind.SYSTEM

    def test_coordinator_message_stays_a_user_message(self):
        parsed = {
            'type': 'user',
            'origin': {'kind': 'coordinator'},
            'message': {'role': 'user', 'content': 'The coordinator sent a message: go on'},
        }
        assert self.compute.compute_item_kind(parsed) == ItemKind.USER_MESSAGE

    def test_rewritten_notification_stays_content_items(self):
        """After the rewrite the content is a tool_result list — origin is moot."""
        parsed = {
            'type': 'user',
            'origin': {'kind': 'task-notification'},
            'message': {
                'role': 'user',
                'content': [{'type': 'tool_result', 'tool_use_id': 't', 'content': 'ok'}],
            },
        }
        assert self.compute.compute_item_kind(parsed) == ItemKind.CONTENT_ITEMS


class TestStopStepMonotonicGuard:
    """A stale parent-side stop must not overwrite newer subagent activity (``run_stop_step``)."""

    @pytest.fixture
    def agent_session(self, parent_session):
        AgentLink.objects.create(
            session=parent_session,
            tool_use_line_num=10,
            tool_use_id='toolu_guard_1',
            agent_id='agent-guard',
            is_background=True,
            started_at=_T_SPAWN,
        )
        return Session.objects.create(
            id='agent-guard',
            project=parent_session.project,
            provider=Provider.CLAUDE_CODE,
            file_path=(
                'test-project-subagent-lifecycle/parent-session-lifecycle/'
                'subagents/agent-guard.jsonl'
            ),
        )

    def _stop_step(self, parent_session, stop_at):
        """The background spawn's ack, then its closing result at ``stop_at`` written by this batch."""
        ToolResultLink.objects.create(
            session=parent_session, tool_use_line_num=10, tool_result_line_num=11,
            tool_use_id='toolu_guard_1', tool_name='Agent', tool_result_at=_T_SPAWN,
        )
        closing = ToolResultLink.objects.create(
            session=parent_session, tool_use_line_num=10, tool_result_line_num=12,
            tool_use_id='toolu_guard_1', tool_name='Agent', tool_result_at=stop_at,
        )
        return run_stop_step(
            parent_session.id, ['agent-guard'], RunStateExclude(tool_result_link_ids=frozenset({closing.id})),
        ).stopped

    def test_stop_newer_than_agent_activity_stamps(self, parent_session, agent_session):
        Session.objects.filter(id=agent_session.id).update(last_updated_at=_T0)
        stopped = self._stop_step(parent_session, _T1)
        assert stopped == [AgentStoppedUpdate('agent-guard', _T1, stamped=True)]
        agent_session.refresh_from_db()
        assert agent_session.last_stopped_at == _T1

    def test_stale_stop_after_a_wake_is_not_stamped(self, parent_session, agent_session):
        # The subagent's own sync recorded newer activity (it re-woke and
        # works again): the older notification must not re-freeze it. The
        # update is still returned, unstamped, so the stop hook fires.
        Session.objects.filter(id=agent_session.id).update(
            last_updated_at=_T2, last_stopped_at=None,
        )
        stopped = self._stop_step(parent_session, _T1)
        assert stopped == [AgentStoppedUpdate('agent-guard', _T1, stamped=False)]
        agent_session.refresh_from_db()
        assert agent_session.last_stopped_at is None

    def test_stop_equal_to_agent_activity_stamps(self, parent_session, agent_session):
        # Equality must stamp: the historical flow can write both with the
        # same second-resolution timestamp.
        Session.objects.filter(id=agent_session.id).update(last_updated_at=_T1)
        stopped = self._stop_step(parent_session, _T1)
        assert stopped == [AgentStoppedUpdate('agent-guard', _T1, stamped=True)]
