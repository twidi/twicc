"""Shared workflow response derivation from source envelopes and published totals."""
import orjson

from twicc.core.models import Session
from twicc.providers.claude_code.workflow_synthesis import apply_orphan_status


def prepare_workflow_envelope(run, session_cutoff=None):
    """Return a fresh response without changing the persisted source envelope.

    Call on a database worker for async requests. Agent costs come only from
    published Session totals, including NULL or absent sessions. Source costs
    can predate statistics publication and must not survive as a fallback.
    """
    try:
        raw = orjson.loads(run.raw_json)
    except orjson.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        return raw
    apply_orphan_status(raw, run.updated_at, session_cutoff)
    agents = [entry for entry in raw.get('workflowProgress', [])
              if isinstance(entry, dict) and entry.get('type') == 'workflow_agent' and entry.get('agentId')]
    ids = {f'{run.run_id}:{entry["agentId"]}' for entry in agents}
    costs = dict(Session.objects.filter(pk__in=ids).values_list('pk', 'total_cost'))
    for entry in agents:
        cost = costs.get(f'{run.run_id}:{entry["agentId"]}')
        entry['cost'] = float(cost) if cost is not None else None
    return raw
