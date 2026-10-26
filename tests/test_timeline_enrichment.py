"""Timeline labels use current metadata without a query per event or session."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.test_timeline_api import make_event, make_instance_event
from twicc.core.models import ProcessRun, Project, Session
from twicc.core.serializers import serialize_project
from twicc.pending_titles import pop_pending_title, set_pending_title
from twicc.rpc.invoker import invoke

pytestmark = pytest.mark.django_db


@pytest.fixture
def history(monkeypatch):
    monkeypatch.setattr("twicc.projects._project_directories", {})
    monkeypatch.setattr("twicc.cli._process_state.resolve_listing_twicc_pid", lambda: 4242)
    project = Project.objects.create(id="-labels", name="Timeline labels", directory="/tmp/labels")
    for index in range(20):
        session = Session.objects.create(
            id=f"labels-{index}", project=project, provider="codex", file_path=f"{index}.jsonl",
            title=f"Session {index}", created_at=timezone.now(), user_message_count=1,
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
        assert {"session_title", "project_name", "project_directory"}.isdisjoint(row)
    assert body["sessions"]["labels-0"]["title"] == "Session 0"
    assert body["sessions"]["labels-0"]["project_directory"] == "/tmp/labels"
    assert body["projects"] == {history.id: serialize_project(history)}
    assert len(project_queries(captured)) == 1
    session_queries = [q for q in captured if q["sql"].startswith('SELECT "core_session".')]
    assert len(session_queries) == 1
    process_queries = [q for q in captured if 'FROM "core_processrun"' in q["sql"]]
    assert len(process_queries) == 1


def test_global_page_batches_distinct_projects(history):
    other = Project.objects.create(id="-other-labels", directory="/tmp/other-labels")
    Session.objects.create(id="other", project=other, provider="codex", file_path="other.jsonl")
    make_event(session_id="other", project=other, at=50)
    with CaptureQueriesContext(connection) as captured:
        body = timeline()
    assert body["projects"][other.id]["name"] is None
    assert body["projects"][other.id]["directory"] == "/tmp/other-labels"
    assert body["projects"][history.id]["directory"] == "/tmp/labels"
    assert body["sessions"]["other"]["project_directory"] == "/tmp/other-labels"
    assert len(project_queries(captured)) == 1


def test_project_scope_reuses_its_loaded_project(history):
    with CaptureQueriesContext(connection) as captured:
        body = timeline(f"--project={history.id}")
    assert body["projects"][history.id]["name"] == "Timeline labels"
    # One scope lookup and one worktree expansion; no metadata re-read.
    assert len(project_queries(captured)) == 2


def test_project_scope_loads_worktree_labels_once(history):
    worktree = Project.objects.create(id="-labels-wt", directory="/tmp/labels-wt", worktree_of=history)
    Session.objects.create(id="wt", project=worktree, provider="codex", file_path="wt.jsonl")
    make_event(session_id="wt", project=worktree, at=50)
    with CaptureQueriesContext(connection) as captured:
        body = timeline(f"--project={history.id}")
    assert body["projects"][worktree.id]["directory"] == "/tmp/labels-wt"
    assert body["projects"][history.id]["name"] == "Timeline labels"
    assert len(project_queries(captured)) == 3


def test_exact_session_scope_reuses_session_metadata_and_pending_title(history):
    set_pending_title("labels-0", "Pending title")
    try:
        with CaptureQueriesContext(connection) as captured:
            body = timeline("--session=labels-0")
        assert body["sessions"]["labels-0"]["title"] == "Pending title"
        session_queries = [q for q in captured if q["sql"].startswith('SELECT "core_session".')]
        assert len(session_queries) == 1
    finally:
        pop_pending_title("labels-0")


def test_workspace_name_reuses_the_scope_definition(history, monkeypatch):
    reads = []

    def read_workspaces():
        reads.append(True)
        return {"workspaces": [{
            "id": "labels-ws", "name": "My workspace", "projectIds": [history.id],
            "color": "#112233", "archived": False, "autoAddPatterns": ["/tmp/*"],
        }]}

    monkeypatch.setattr("twicc.workspaces.read_workspaces", read_workspaces)
    body = timeline("--workspace=labels-ws")
    assert body["workspace"] == {
        "id": "labels-ws", "name": "My workspace", "projectIds": [history.id],
        "color": "#112233", "archived": False, "autoAddPatterns": ["/tmp/*"],
    }
    assert len(reads) == 1


def test_empty_workspace_still_has_a_name(monkeypatch):
    monkeypatch.setattr("twicc.workspaces.read_workspaces", lambda: {
        "workspaces": [{"id": "empty", "name": "Empty workspace", "projectIds": []}],
    })
    with CaptureQueriesContext(connection) as captured:
        body = timeline("--workspace=empty")
    assert body["items"] == []
    assert body["workspace"] == {"id": "empty", "name": "Empty workspace", "projectIds": []}
    assert body["sessions"] == body["projects"] == {}
    assert len(captured) == 0


def test_orphan_events_keep_their_project_and_instance_events_have_no_references(history):
    make_event(session_id="purged", project=history, at=50)
    make_instance_event(at=60)
    body = timeline("--limit=2")
    instance, orphan = body["items"]
    assert instance["session_id"] is instance["project_id"] is None
    assert orphan["session_id"] == "purged"
    assert body["projects"][orphan["project_id"]]["directory"] == "/tmp/labels"
    assert body["sessions"] == {}


def test_no_metadata_queries_for_instance_only_page():
    make_instance_event()
    with CaptureQueriesContext(connection) as captured:
        body = timeline()
    assert body["sessions"] == body["projects"] == {}
    assert project_queries(captured) == []


@pytest.mark.parametrize("full", [False, True])
def test_sessions_match_the_standard_listing_in_both_modes(history, full):
    Session.objects.filter(pk="labels-0").update(tasks={"items": [{"status": "pending", "content": "Test"}]})
    flags = ["--full"] if full else []
    body = timeline(*flags)
    listed = invoke(["sessions", *flags])
    assert listed.exit_code == 0, listed.error
    assert body["sessions"] == {row["id"]: row for row in listed.result["items"]}
    first = body["sessions"]["labels-0"]
    assert first["has_tasks"] is True
    assert ("tasks" in first) is full
    assert ("layout" in first) is full
    assert ("pid" in first["process"]) is full
    assert body["projects"] == {history.id: serialize_project(history)}


def test_process_state_filter_reuses_loaded_process_rows(history):
    ProcessRun.objects.create(
        session_id="labels-0", provider="codex", twicc_pid=4242, state="assistant_turn",
        started_at=timezone.now(), last_state_change_at=timezone.now(),
    )
    with CaptureQueriesContext(connection) as captured:
        body = timeline("--state=assistant_turn")
    assert body["sessions"]["labels-0"]["process"]["state"] == "assistant_turn"
    assert len([q for q in captured if 'FROM "core_processrun"' in q["sql"]]) == 1


def test_native_subagent_uses_standard_process_and_settings(history):
    Session.objects.create(
        id="native", project=history, provider="codex", file_path="native.jsonl",
        type="subagent", parent_session_id="labels-0", effort="high",
    )
    make_event(session_id="native", project=history, at=50)
    body = timeline("--session=native", "--full")
    assert body["sessions"]["native"]["process"] is None
    assert body["sessions"]["native"]["effort"] == "high"


def test_references_only_describe_the_returned_page(history):
    other = Project.objects.create(id="-lookahead", directory="/tmp/lookahead")
    Session.objects.create(id="lookahead", project=other, provider="codex", file_path="lookahead.jsonl")
    make_event(session_id="lookahead", project=other, at=50)
    body = timeline("--limit=1")
    assert set(body["sessions"]) == {"lookahead"}
    assert set(body["projects"]) == {other.id}
