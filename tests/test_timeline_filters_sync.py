"""The filter keys of the Timeline tab (frontend/src/utils/timeline/filters.js) match the backend definitions.

Filters design (docs/plans/2026-10-26-timeline-filters-design.md), sections 4.1 to 4.4: the keys the frontend sends
to ``GET /api/timeline/`` and the problem reasons are copied on both sides; this test pins that the copies agree.
"""

import subprocess
from pathlib import Path

import orjson

from twicc.core.enums import Provider
from twicc.core.services.timeline_filters import AUTHORS, FAMILIES, PROBLEM_REASONS, PROBLEM_WORKFLOW_STATUSES, VIEWS

JAVASCRIPT = """
import { AUTHORS, FAMILIES, PROVIDERS, VIEWS } from './frontend/src/utils/timeline/filters.js';
import { PROCESS_ENDED_REASONS, WORKFLOW_ENDED_STATUSES } from './frontend/src/utils/timeline/describeEvent.js';
const values = (entries) => entries.map((entry) => entry.value);
const problemReasons = Object.entries(PROCESS_ENDED_REASONS)
    .filter(([, [, severity]]) => severity === 'warning' || severity === 'danger')
    .map(([reason]) => reason);
const problemWorkflowStatuses = Object.entries(WORKFLOW_ENDED_STATUSES)
    .filter(([, [, severity]]) => severity === 'warning' || severity === 'danger')
    .map(([status]) => status);
console.log(JSON.stringify({
    views: values(VIEWS), families: values(FAMILIES), messageFamily: FAMILIES.find((entry) => entry.value === "messages"), authors: values(AUTHORS), providers: PROVIDERS, problemReasons, problemWorkflowStatuses,
}));
"""


def test_frontend_keys_match_the_backend():
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", JAVASCRIPT],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    frontend = orjson.loads(result.stdout)
    assert frontend["views"] == list(VIEWS)
    assert frontend["families"] == list(FAMILIES)
    assert frontend["messageFamily"] == {
        "value": "messages", "label": "Messages", "hint": "user messages, assistant finals",
    }
    assert frontend["authors"] == list(AUTHORS)
    assert frontend["providers"] == [provider.value for provider in Provider]
    assert sorted(frontend["problemReasons"]) == sorted(PROBLEM_REASONS)
    assert sorted(frontend["problemWorkflowStatuses"]) == sorted(PROBLEM_WORKFLOW_STATUSES)
