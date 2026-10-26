"""Timeline labels use current metadata without a query per event or session."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_timeline_api import make_event, make_instance_event
from twicc.core.models import Project, Session
from twicc.pending_titles import pop_pending_title, set_pending_title
from twicc.rpc.invoker import invoke

pytestmark = pytest.mark.django_db


@pytest.fixture
def history():
    project = Project.objects.create(id="-labels", name="Timeline labels", directory="/tmp/labels")
    for index in range(20):
        session = Session.objects.create(
            id=f"labels-{index}", project=project, provider="codex", file_path=f"{index}.jsonl",
            title=f"Session {index}",
        )
        make_event(session_id=session.id, project=project, at=index)
    return project


def timeline(*arguments):
    response = invoke(["timeline", *arguments])
    assert response.exit_code == 0, response.error
    return response.result


def project_queries(captured):
    return [query["sql"] for query in captured if 'FROM "core_project"' in query["sql"]]


def test_global_page_batches_project_and_session_labels(history):
    with CaptureQueriesContext(connection) as captured:
        body = timeline()
    assert len(body["items"]) == 20
    for row in body["items"]:
        assert row["session_title"] == f"Session {row['session_id'].removeprefix('labels-')}"
        assert row["project_name"] == "Timeline labels"
        assert row["project_directory"] == "/tmp/labels"
    assert body["sessions"]["labels-0"]["project_name"] == "Timeline labels"
    assert body["sessions"]["labels-0"]["project_directory"] == "/tmp/labels"
    assert len(project_queries(captured)) == 1
    session_queries = [q for q in captured if q["sql"].startswith('SELECT "core_session".')]
    assert len(session_queries) == 1


def test_global_page_batches_distinct_projects(history):
    other = Project.objects.create(id="-other-labels", directory="/tmp/other-labels")
    Session.objects.filter(pk="labels-19").update(project=other)
    make_event(session_id="labels-19", project=other, at=50)
    with CaptureQueriesContext(connection) as captured:
        body = timeline()
    assert body["items"][0]["project_name"] is None
    assert body["items"][0]["project_directory"] == "/tmp/other-labels"
    assert body["items"][1]["project_directory"] == "/tmp/labels"
    assert body["sessions"]["labels-19"]["project_directory"] == "/tmp/other-labels"
    assert len(project_queries(captured)) == 1


def test_project_scope_reuses_its_loaded_project(history):
    with CaptureQueriesContext(connection) as captured:
        body = timeline(f"--project={history.id}")
    assert body["items"][0]["project_name"] == "Timeline labels"
    # One scope lookup and one worktree expansion; no metadata re-read.
    assert len(project_queries(captured)) == 2


def test_project_scope_loads_worktree_labels_once(history):
    worktree = Project.objects.create(id="-labels-wt", directory="/tmp/labels-wt", worktree_of=history)
    Session.objects.create(id="wt", project=worktree, provider="codex", file_path="wt.jsonl")
    make_event(session_id="wt", project=worktree, at=50)
    with CaptureQueriesContext(connection) as captured:
        body = timeline(f"--project={history.id}")
    assert body["items"][0]["project_directory"] == "/tmp/labels-wt"
    assert body["items"][1]["project_name"] == "Timeline labels"
    assert len(project_queries(captured)) == 3


def test_exact_session_scope_reuses_session_metadata_and_pending_title(history):
    set_pending_title("labels-0", "Pending title")
    try:
        with CaptureQueriesContext(connection) as captured:
            body = timeline("--session=labels-0")
        assert body["items"][0]["session_title"] == "Pending title"
        assert body["sessions"]["labels-0"]["title"] == "Pending title"
        session_queries = [q for q in captured if q["sql"].startswith('SELECT "core_session".')]
        assert len(session_queries) == 1
    finally:
        pop_pending_title("labels-0")


def test_workspace_name_reuses_the_scope_definition(history, monkeypatch):
    reads = []

    def read_workspaces():
        reads.append(True)
        return {"workspaces": [{"id": "labels-ws", "name": "My workspace", "projectIds": [history.id]}]}

    monkeypatch.setattr("twicc.workspaces.read_workspaces", read_workspaces)
    body = timeline("--workspace=labels-ws")
    assert body["workspace"] == {"id": "labels-ws", "name": "My workspace"}
    assert len(reads) == 1


def test_empty_workspace_still_has_a_name(monkeypatch):
    monkeypatch.setattr("twicc.workspaces.read_workspaces", lambda: {
        "workspaces": [{"id": "empty", "name": "Empty workspace", "projectIds": []}],
    })
    with CaptureQueriesContext(connection) as captured:
        body = timeline("--workspace=empty")
    assert body["items"] == []
    assert body["workspace"] == {"id": "empty", "name": "Empty workspace"}
    assert len(captured) == 0


def test_missing_session_and_instance_labels_are_null(history):
    make_event(session_id="purged", project=history, at=50)
    make_instance_event(at=60)
    body = timeline("--limit=2")
    instance, orphan = body["items"]
    assert instance["session_title"] is instance["project_name"] is instance["project_directory"] is None
    assert orphan["session_title"] is None
    assert orphan["project_directory"] == "/tmp/labels"
    assert body["sessions"] == {}


def test_no_metadata_queries_for_instance_only_page():
    make_instance_event()
    with CaptureQueriesContext(connection) as captured:
        body = timeline()
    assert body["items"][0]["project_directory"] is None
    assert project_queries(captured) == []
