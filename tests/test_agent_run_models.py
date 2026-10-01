from django.db import IntegrityError, connection
from django.utils import timezone as djtz
import pytest

from twicc.core.models import (
    AgentInteraction,
    AgentInteractionKind,
    AgentRunEnd,
    AgentRunEndSource,
    Project,
    Session,
    SessionType,
)


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-agent-runs", directory="/tmp/agent-runs")


@pytest.fixture
def root(project):
    now = djtz.now()
    return Session.objects.create(
        id="root-1", project=project, provider="claude_code",
        file_path="root-1.jsonl", type=SessionType.SESSION, title="Root",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=10,
    )


def test_agent_interaction_unique_session_tool_use_id(root):
    AgentInteraction.objects.create(
        session=root, tool_use_line_num=1, event_line_num=1,
        tool_use_id="tu-1", agent_id="agent-1", kind=AgentInteractionKind.MESSAGE,
    )
    with pytest.raises(IntegrityError):
        AgentInteraction.objects.create(
            session=root, tool_use_line_num=2, event_line_num=2,
            tool_use_id="tu-1", agent_id="agent-1", kind=AgentInteractionKind.STOP,
        )


def test_agent_run_end_unique_transcript_session_line_num_tool_use_id(root):
    AgentRunEnd.objects.create(
        session=root, line_num=5, source=AgentRunEndSource.TRANSCRIPT,
        agent_id="agent-1", tool_use_id="tu-1", status="completed",
    )
    with pytest.raises(IntegrityError):
        AgentRunEnd.objects.create(
            session=root, line_num=5, source=AgentRunEndSource.TRANSCRIPT,
            agent_id="agent-1", tool_use_id="tu-1", status="failed",
        )


def test_agent_run_end_ui_rows_do_not_collide(root):
    # ui rows all have line_num=None, tool_use_id="" — the conditional
    # unique constraint (source=transcript) must not reject them.
    AgentRunEnd.objects.create(
        session=root, line_num=None, source=AgentRunEndSource.UI,
        agent_id="agent-1", tool_use_id="", status="ui_stopped",
    )
    AgentRunEnd.objects.create(
        session=root, line_num=None, source=AgentRunEndSource.UI,
        agent_id="agent-1", tool_use_id="", status="ui_stopped",
    )
    assert AgentRunEnd.objects.filter(agent_id="agent-1", source=AgentRunEndSource.UI).count() == 2


def _index_names(table):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return set(constraints)


def test_new_indexes_exist(transactional_db):
    agent_interaction_indexes = _index_names("core_agentinteraction")
    assert "idx_agent_interaction_agent" in agent_interaction_indexes

    agent_run_end_indexes = _index_names("core_agentrunend")
    assert "idx_agent_run_end_agent_tool" in agent_run_end_indexes

    agent_link_indexes = _index_names("core_agentlink")
    assert "idx_agent_link_agent" in agent_link_indexes

    tool_result_link_indexes = _index_names("core_toolresultlink")
    assert "idx_tool_result_link_by_tool" in tool_result_link_indexes
