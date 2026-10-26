"""Run the published timeline examples against an isolated event history."""

import re
import shlex
from pathlib import Path

import pytest

from tests.test_timeline_api import make_event
from twicc.core.models import Project, Session
from twicc.rpc.invoker import invoke

SKILL = Path(__file__).resolve().parents[1] / "src/twicc/agent/plugin/twicc/skills/twicc-timeline/SKILL.md"


@pytest.mark.django_db
def test_published_timeline_examples_select_events_and_advance_cursor(monkeypatch):
    project = Project.objects.create(id="-demo-project", directory="/tmp/demo-project")
    root = Session.objects.create(id="demo-root", project=project, provider="codex", file_path="root.jsonl")
    Session.objects.create(
        id="demo-child", project=project, provider="codex", file_path="child.jsonl",
        hidden=True, spawned_by=root, spawn_root=root, annotations={"team": "backend"},
    )
    events = [make_event(session_id="demo-child", project=project, at=i) for i in range(3)]
    monkeypatch.setattr("twicc.workspaces.read_workspaces", lambda: {
        "workspaces": [{"id": "demo-workspace", "projectIds": [project.id]}],
    })
    monkeypatch.setattr("twicc.cli._process_state.resolve_listing_twicc_pid", lambda: None)
    examples = re.findall(r"^\$TWICC timeline .+$", SKILL.read_text(), flags=re.MULTILINE)
    assert examples, "The skill must provide executable usage examples."
    cursor = None
    for example in examples:
        arguments = example.replace("SESSION_ID", "demo-child").replace("MIDDLE_ID", "demo-child")
        arguments = arguments.replace("PROJECT_ID", "-demo-project").replace("WORKSPACE_ID", "demo-workspace")
        if "CURSOR" in arguments:
            assert cursor is not None, "A next-page example needs an earlier page."
            arguments = arguments.replace("CURSOR", cursor)
        response = invoke(shlex.split(arguments)[1:])
        assert response.exit_code == 0, (example, response.error)
        body = response.result
        if "--before" in arguments:
            assert [e["id"] for e in body["items"]] == [events[1].id]
        elif "--limit 1" in arguments:
            assert [e["id"] for e in body["items"]] == [events[2].id]
            cursor = body["pagination"]["next_cursor"]
        elif "--include-hidden" in arguments:
            assert len(body["items"]) == 3
        else:
            assert body["items"] == []
