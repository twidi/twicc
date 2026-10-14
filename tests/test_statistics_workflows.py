from decimal import Decimal
import pytest
import orjson
from twicc.core.models import Workflow
from tests.test_statistics_activity import read, root

pytestmark = pytest.mark.django_db(transaction=True)


def test_workflow_contract_empty():
    session = root('workflow-root')
    workflow = Workflow.objects.create(session=session, run_id='run', raw_json='{}')
    assert read('prepare_workflow_cost', workflow.pk)[0] == (Decimal(0), {})


def test_nested_workflow_phases_and_repeated_entries():
    from twicc.core.models import AgentLink, Session
    owner = root('owner')
    for sid, total in (('run:a', '9'), ('run:b', '5')):
        Session.objects.create(id=sid, project=owner.project, provider=owner.provider,
                               file_path=f'/{sid}', type='subagent', parent_session=owner,
                               total_cost=Decimal(total))
    AgentLink.objects.create(session_id='run:a', agent_id='run:b', tool_use_line_num=1, tool_use_id='b')
    envelope = {'workflowProgress': [
        {'type': 'workflow_agent', 'agentId': agent, 'phaseIndex': phase}
        for agent, phase in (('a', 1), ('b', 2), ('b', 3), ('a', 1))]}
    workflow = Workflow.objects.create(session=owner, run_id='run', raw_json=orjson.dumps(envelope).decode())
    assert read('prepare_workflow_cost', workflow.pk)[0] == (Decimal(9), {'1': 4.0, '2': 5.0})


def test_orphan_and_unlisted_descendant_keep_existing_phase_semantics():
    from twicc.core.models import Session
    owner = root('owner')
    for sid, total in (('run:a', '7'), ('run:orphan', '2'), ('run:null', None)):
        Session.objects.create(id=sid, project=owner.project, provider=owner.provider,
                               file_path=f'/{sid}', type='subagent', parent_session=owner, total_cost=total)
    # The unlisted nested contribution is already part of a's total_cost.
    Session.objects.create(id='native-child', project=owner.project, provider=owner.provider,
                           file_path='/native', type='subagent', parent_session=owner, total_cost=3)
    envelope = {'workflowProgress': [{'type': 'workflow_agent', 'agentId': 'a', 'phaseIndex': 1}]}
    workflow = Workflow.objects.create(session=owner, run_id='run', raw_json=orjson.dumps(envelope).decode())
    assert read('prepare_workflow_cost', workflow.pk)[0] == (Decimal(9), {'1': 7.0})
