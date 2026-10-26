"""Public timeline scopes and filters compose before event pagination."""

from types import SimpleNamespace

import orjson
import pytest
from django.utils import timezone
from typer.testing import CliRunner

from tests.test_timeline_api import make_event, make_instance_event
from twicc.cli import app
from twicc.cli._drop_request.whoami import forced_session_id
from twicc.core.models import ProcessRun, Project, Session
from twicc.rpc.invoker import invoke

pytestmark = pytest.mark.django_db


@pytest.fixture
def history(monkeypatch):
    main = Project.objects.create(id="-tl-main", directory="/tmp/tl-main")
    worktree = Project.objects.create(id="-tl-wt", directory="/tmp/tl-wt", worktree_of=main)
    archived = Project.objects.create(id="-tl-arch", directory="/tmp/tl-arch", worktree_of=main, archived=True)
    other = Project.objects.create(id="-tl-other", directory="/tmp/tl-other")
    sessions, events = {}, {}
    for index, (sid, project, fields) in enumerate([
        ("A", main, {"annotations": {"team": "backend", "ready": True, "count": 2}}),
        ("B", worktree, {"spawned_by_id": "A", "spawn_root_id": "A", "annotations": {"team": "backend"}}),
        ("C", worktree, {"spawned_by_id": "B", "spawn_root_id": "A"}),
        ("D", other, {"spawned_by_id": "A", "spawn_root_id": "A"}),
        ("H", main, {"spawned_by_id": "A", "spawn_root_id": "A", "hidden": True}),
        ("Z", archived, {"archived": True}),
        ("N", main, {"type": "subagent", "parent_session_id": "A"}),
    ]):
        sessions[sid] = Session.objects.create(
            id=sid, project=project, provider="codex", file_path=f"{sid}.jsonl", **fields,
        )
        events[sid] = make_event(session_id=sid, project=project, at=index)
    instance = make_instance_event(at=20)
    monkeypatch.setattr("twicc.workspaces.read_workspaces", lambda: {"workspaces": [
        {"id": "ws", "projectIds": [main.id, worktree.id]}, {"id": "empty", "projectIds": []},
        {"id": "worktree-ws", "projectIds": [worktree.id]},
    ]})
    monkeypatch.setattr("twicc.cli._process_state.resolve_listing_twicc_pid", lambda: 4242)
    return SimpleNamespace(main=main, worktree=worktree, other=other, sessions=sessions, events=events, instance=instance)


def result(*args):
    response = invoke(["timeline", *args])
    assert response.exit_code == 0, response.error
    return response.result


def targets(*args):
    return {row["session_id"] for row in result(*args)["items"]}


def test_default_page_size_and_cursor_shape():
    assert result()["pagination"] == {"limit": 20, "has_more": False, "next_cursor": None}


def test_project_worktree_and_workspace_expansion(history):
    assert targets("--project=-tl-main") == {"A", "B", "C", "Z", "N", None}
    assert targets("--project=-tl-wt") == {"B", "C", None}
    assert targets("--workspace=ws") == {"A", "B", "C", "Z", "N", None}
    assert targets("--workspace=worktree-ws") == {"B", "C", None}


def test_unknown_explicit_scope_errors_but_known_empty_scope_succeeds(history):
    for flag in ("--project=unknown", "--workspace=unknown", "--session=unknown"):
        assert invoke(["timeline", flag]).exit_code != 0
    assert targets("--workspace=empty") == set()
    Session.objects.create(id="empty-session", project=history.main, provider="codex", file_path="empty.jsonl")
    assert targets("--session=empty-session") == set()


def test_exact_session_scope_includes_no_descendants_or_instance_events(history):
    assert targets("--session=A") == {"A"}
    assert targets("--session=N") == {"N"}


def test_scope_and_session_filters_intersect(history):
    assert targets("--project=-tl-wt", "--spawn-tree=C") == {"B", "C"}
    assert targets("--session=A", "--annotation=team=backend") == {"A"}
    assert targets("--session=A", "--annotation=team=frontend") == set()
    assert targets("--session=A", "--state=assistant_turn") == set()


@pytest.mark.parametrize("flags, expected", [
    (["--spawn-tree=A"], {"A", "B", "C", "D"}),
    (["--spawn-tree=B"], {"A", "B", "C", "D"}),
    (["--spawn-tree=C"], {"A", "B", "C", "D"}),
    (["--spawn-tree=Z"], {"Z"}),
    (["--descendants=A"], {"B", "C", "D"}),
    (["--descendants=B"], {"C"}),
    (["--spawned-by=A"], {"B", "D"}),
    (["--siblings=B"], {"D"}),
    (["--siblings=A"], set()),
    (["--spawn-tree=unknown"], set()),
    (["--descendants=unknown"], set()),
    (["--siblings=unknown"], set()),
    (["--spawned-by=unknown"], set()),
])
def test_all_filiation_filters_reuse_existing_membership(history, flags, expected):
    assert targets(*flags) == expected


def test_visibility_and_archived_defaults(history):
    assert "H" not in targets()
    assert "H" in targets("--include-hidden")
    assert targets("--only-hidden") == {"H"}
    assert "H" in targets("--spawn-tree=A", "--include-hidden")
    assert targets("--spawn-tree=A", "--only-hidden") == {"H"}
    assert "Z" in targets()
    assert targets("--include-archived") == targets()
    assert targets("--session=H") == set()
    assert targets("--session=H", "--include-hidden") == {"H"}


@pytest.mark.parametrize("expression, expected", [
    ("team=backend", {"A", "B"}), ("team!=backend", {"C", "D", "Z", "N"}),
    ("team:exists", {"A", "B"}), ("team:not-exists", {"C", "D", "Z", "N"}),
    ("team:in:frontend,backend", {"A", "B"}), ("ready=true", {"A"}), ("count=2", {"A"}),
])
def test_annotations_use_existing_typed_operators(history, expression, expected):
    assert targets(f"--annotation={expression}") == expected
    assert targets("--annotation=team=backend", "--annotation=ready=true") == {"A"}


def test_process_filters_use_current_state(history, monkeypatch):
    for sid, state, waiting in (
        ("A", "starting", False), ("B", "assistant_turn", False),
        ("C", "assistant_turn", True), ("D", "user_turn", False),
    ):
        ProcessRun.objects.create(
            session_id=sid, provider="codex", twicc_pid=4242, state=state, awaiting_user_input=waiting,
            started_at=timezone.now(), last_state_change_at=timezone.now(),
        )
    ProcessRun.objects.create(
        session_id="Z", provider="codex", twicc_pid=9999, state="assistant_turn",
        started_at=timezone.now(), last_state_change_at=timezone.now(),
    )
    assert targets("--state=starting") == {"A"}
    assert targets("--state=assistant_turn") == {"B"}
    assert targets("--state=awaiting_user_input") == {"C"}
    assert targets("--state=user_turn") == {"D"}
    assert targets("--state=dead") == {"Z", "N"}
    assert targets("--active") == {"A", "B", "C", "D"}
    assert targets("--state=starting", "--state=user_turn") == {"A", "D"}
    monkeypatch.setattr("twicc.cli._process_state.resolve_listing_twicc_pid", lambda: None)
    assert targets("--active") == set()
    assert targets("--state=dead") == {"A", "B", "C", "D", "Z", "N"}


@pytest.mark.parametrize("flags", [
    ["--session=A"], ["--annotation=team=backend"], ["--spawn-tree=A"], ["--descendants=A"],
    ["--siblings=B"], ["--spawned-by=A"], ["--provider=codex"], ["--state=dead"], ["--active"],
    ["--only-hidden"],
    [f"--state={s}" for s in ("starting", "assistant_turn", "awaiting_user_input", "user_turn", "dead")],
])
def test_instance_policy_depends_on_supplied_filters(history, flags):
    assert None not in targets(*flags)
    assert None in targets()


@pytest.mark.parametrize("flags", [
    ["--project=/tmp/tl-main", "--workspace=empty"], ["--session=A", "--workspace=empty"],
    ["--spawn-tree=A", "--descendants=A"], ["--include-hidden", "--only-hidden"],
    ["--state=dead", "--active"], ["--provider=invalid"], ["--state=invalid"],
    ["--annotation=invalid"], ["--limit=0"], ["--limit=-1"], ["--limit=101"],
    ["--before=invalid"], ["--offset=1"], ["--after-id=1"], ["--paginated"],
    ["--spawn-tree=parent"], ["--siblings=parent"],
])
def test_invalid_arguments_fail_before_empty_scope_shortcuts(history, flags):
    response = invoke(["timeline", "--workspace=empty", *flags])
    assert response.exit_code != 0


def test_local_session_keywords_resolve_or_fail_closed(history, monkeypatch):
    token = forced_session_id.set("B")
    try:
        assert targets("--session=self") == {"B"}
        assert targets("--session=parent") == {"A"}
        assert targets("--descendants=self") == {"C"}
    finally:
        forced_session_id.reset(token)
    monkeypatch.setattr("twicc.cli._drop_request.whoami._walk_ppids", lambda: iter(()))
    assert invoke(["timeline", "--session=self"]).exit_code != 0
    token = forced_session_id.set("A")
    try:
        assert invoke(["timeline", "--session=parent"]).exit_code != 0
    finally:
        forced_session_id.reset(token)


def test_cli_stdout_and_help(history):
    runner = CliRunner()
    response = runner.invoke(app, ["timeline", "--session=A"])
    assert response.exit_code == 0
    assert orjson.loads(response.stdout)["items"][0]["session_id"] == "A"
    help_result = runner.invoke(app, ["timeline", "--help"])
    assert help_result.exit_code == 0
    assert "--before" in help_result.stdout


def test_existing_absolute_project_path(tmp_path):
    from twicc.paths import path_to_project_id

    project = Project.objects.create(id=path_to_project_id(str(tmp_path)), directory=str(tmp_path))
    Session.objects.create(id="path-session", project=project, provider="codex", file_path="path.jsonl")
    make_event(session_id="path-session", project=project)
    assert targets(f"--project={tmp_path}") == {"path-session"}
