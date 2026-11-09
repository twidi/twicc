"""Tests for the spawned-session topology builder."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.cli._annotation_filters import AnnotationFilter, AnnotationOp
from twicc.cli.topology import TOPOLOGY_SESSION_FIELDS, build_topology
from twicc.core.models import ProcessRun, Project, Session, SessionCron, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-twicc-topology",
        directory="/tmp/twicc-topology",
    )


def make_session(
    project,
    session_id: str,
    *,
    title: str,
    spawned_by=None,
    minutes: int = 0,
    **overrides,
):
    now = timezone.now() + timedelta(minutes=minutes)
    values = {
        "id": session_id,
        "project": project,
        "provider": "codex",
        "file_path": f"{session_id}.jsonl",
        "type": SessionType.SESSION,
        "title": title,
        "created_at": now,
        "last_new_content_at": now,
        "user_message_count": 1,
        "spawned_by": spawned_by,
    }
    values.update(overrides)
    return Session.objects.create(**values)


def test_build_topology_returns_rooted_tree_from_middle_node(project):
    root = make_session(project, "A", title="Root", minutes=0)
    root.total_cost = Decimal("1.000000")
    root.spawn_root = root
    root.annotations = {"role": "coordinator"}
    root.save(update_fields=["total_cost", "spawn_root", "annotations"])
    b = make_session(
        project,
        "B",
        title="Backend",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
        total_cost=Decimal("2.000000"),
    )
    c = make_session(
        project,
        "C",
        title="Frontend",
        spawned_by=root,
        spawn_root=root,
        minutes=2,
    )
    make_session(
        project,
        "D",
        title="Tests",
        spawned_by=b,
        spawn_root=root,
        minutes=3,
        total_cost=Decimal("0.500000"),
    )
    make_session(
        project,
        "E",
        title="Hidden",
        spawned_by=root,
        spawn_root=root,
        minutes=4,
        hidden=True,
        archived=True,
    )

    data = build_topology(c, include_processes=False)

    assert data["seed_session_id"] == "C"
    assert data["root_session_id"] == "A"
    assert data["path_to_seed"] == ["A", "C"]
    assert data["total_cost"] == 3.5
    assert data["node_count"] == 5
    assert data["processes"] == {
        "requested": False,
        "available": False,
        "reason": "not_requested",
    }
    assert list(data.keys()) == [
        "seed_session_id",
        "root_session_id",
        "path_to_seed",
        "cycle_detected",
        "total_cost",
        "node_count",
        "processes",
        "tree",
        "nodes",
    ]
    assert data["tree"] == {
        "id": "A",
        "children": [
            {"id": "B", "children": [{"id": "D", "children": []}]},
            {"id": "C", "children": []},
            {"id": "E", "children": []},
        ],
    }
    nodes = {node["id"]: node for node in data["nodes"]}
    assert list(nodes) == ["A", "B", "D", "C", "E"]
    assert nodes["A"]["session"]["id"] == "A"
    assert nodes["A"]["session"]["context_usage"] is None
    assert nodes["A"]["session"]["spawn_root"] == "A"
    assert nodes["A"]["session"]["annotations"] == {"role": "coordinator"}
    assert nodes["B"]["session"]["spawn_root"] == "A"
    assert nodes["A"]["direct_child_count"] == 3
    assert nodes["A"]["descendant_count"] == 4
    assert nodes["A"]["subtree_total_cost"] == 3.5
    assert nodes["B"]["direct_child_count"] == 1
    assert nodes["B"]["descendant_count"] == 1
    assert nodes["B"]["subtree_total_cost"] == 2.5
    assert nodes["C"]["subtree_total_cost"] is None
    # Slim default: every node carries exactly the expected field set plus
    # the synthetic ``directory`` — and nothing else (no hidden/archived/etc).
    expected_session_keys = set(TOPOLOGY_SESSION_FIELDS) | {"directory"}
    for node in nodes.values():
        assert set(node["session"].keys()) == expected_session_keys


def test_build_topology_full_sessions_emits_full_session_payload(project):
    root = make_session(project, "A", title="Root", cwd="/tmp/twicc")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(
        project,
        "B",
        title="Hidden worker",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
        hidden=True,
        archived=True,
    )

    data = build_topology(root, include_processes=False, full_sessions=True)
    nodes = {node["id"]: node for node in data["nodes"]}

    # The full payload exposes the model fields that the slim shape hides.
    assert nodes["A"]["session"]["cwd"] == "/tmp/twicc"
    assert "directory" not in nodes["A"]["session"]
    assert nodes["B"]["session"]["hidden"] is True
    assert nodes["B"]["session"]["archived"] is True


def test_build_topology_slim_default_resolves_directory(project):
    root = make_session(project, "A", title="Root", cwd="/tmp/twicc")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(
        project,
        "B",
        title="Git worker",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
        cwd="/tmp/twicc/sub",
        git_directory="/tmp/twicc/git",
    )

    data = build_topology(root, include_processes=False)
    nodes = {node["id"]: node for node in data["nodes"]}

    # ``directory`` prefers ``git_directory``, falls back to ``cwd``.
    assert nodes["A"]["session"]["directory"] == "/tmp/twicc"
    assert nodes["B"]["session"]["directory"] == "/tmp/twicc/git"
    # The raw fields are NOT in the slim payload.
    assert "cwd" not in nodes["A"]["session"]
    assert "git_directory" not in nodes["B"]["session"]


def test_build_topology_adds_compact_process_state(project):
    root = make_session(project, "A", title="Root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    child = make_session(
        project,
        "B",
        title="Worker",
        spawned_by=root,
        spawn_root=root,
        minutes=1,
    )
    now = timezone.now()
    ProcessRun.objects.create(
        provider="codex",
        session_id=child.id,
        started_at=now,
        state=AgentState.ASSISTANT_TURN.value,
        last_state_change_at=now,
        twicc_pid=1234,
        agent_pid=5678,
        awaiting_user_input=True,
    )

    data = build_topology(root, include_processes=True, twicc_pid=1234)
    nodes = {node["id"]: node for node in data["nodes"]}

    assert data["processes"] == {
        "requested": True,
        "available": True,
        "reason": None,
    }
    assert nodes["A"]["process"]["state"] == "dead"
    assert nodes["B"]["process"]["state"] == "awaiting_user_input"
    assert nodes["B"]["process"]["pid"] == 5678


def test_build_topology_marks_processes_unavailable_without_live_twicc(project, monkeypatch):
    root = make_session(project, "A", title="Root")
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)

    data = build_topology(root, include_processes=True)

    assert data["processes"] == {
        "requested": True,
        "available": False,
        "reason": "twicc_not_running",
    }
    assert data["nodes"][0]["process"] is None


def test_topology_no_annotation_filter_omits_matches_field(project):
    root = make_session(project, "R", title="root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(project, "C", title="child", spawned_by=root, spawn_root=root)

    data = build_topology(root, include_processes=False)
    for node in data["nodes"]:
        assert "matches_annotations" not in node


def test_topology_annotation_filter_sets_flag_per_node(project):
    root = make_session(project, "R", title="root", annotations={"role": "coord"})
    root.spawn_root = root
    root.save(update_fields=["spawn_root", "annotations"])
    make_session(
        project, "I", title="impl",
        spawned_by=root, spawn_root=root,
        annotations={"role": "implementer"},
    )
    make_session(
        project, "Rv", title="rev",
        spawned_by=root, spawn_root=root,
        annotations={"role": "reviewer"},
    )

    data = build_topology(
        root,
        include_processes=False,
        annotation_filters=[AnnotationFilter(("role",), AnnotationOp.EQ, "implementer")],
    )
    by_id = {node["id"]: node for node in data["nodes"]}
    assert by_id["R"]["matches_annotations"] is False
    assert by_id["I"]["matches_annotations"] is True
    assert by_id["Rv"]["matches_annotations"] is False


def test_topology_annotation_filter_no_match_still_returns_full_tree(project):
    root = make_session(project, "R", title="root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(project, "C", title="child", spawned_by=root, spawn_root=root)

    data = build_topology(
        root,
        include_processes=False,
        annotation_filters=[AnnotationFilter(("role",), AnnotationOp.EQ, "nobody")],
    )
    ids = {node["id"] for node in data["nodes"]}
    assert ids == {"R", "C"}  # tree is preserved
    assert all(node["matches_annotations"] is False for node in data["nodes"])


def test_topology_root_with_null_spawn_root_matches_its_own_annotations(project):
    """Regression: a session that has never spawned a child has spawn_root_id=NULL.
    The annotation match query must include it via Q(pk=root.id) — otherwise
    matches_annotations is always False even when annotations match.
    """
    root = make_session(
        project, "STANDALONE", title="standalone",
        annotations={"role": "implementer"},
    )
    # Intentionally do NOT set root.spawn_root = root — leave spawn_root_id NULL.

    data = build_topology(
        root,
        include_processes=False,
        annotation_filters=[AnnotationFilter(("role",), AnnotationOp.EQ, "implementer")],
    )
    by_id = {node["id"]: node for node in data["nodes"]}
    assert by_id["STANDALONE"]["matches_annotations"] is True


def make_recurring_cron(cron_id: str, session_id: str, *, created_at):
    return SessionCron.objects.create(
        provider="claude_code",
        cron_id=cron_id,
        session_id=session_id,
        cron_expr="*/5 * * * *",
        recurring=True,
        prompt="tick",
        created_at=created_at,
        next_fire=created_at + timedelta(minutes=5),
    )


def test_topology_node_reports_active_cron_count(project):
    root = make_session(project, "A", title="Root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    make_session(project, "B", title="Worker", spawned_by=root, spawn_root=root, minutes=1)
    make_recurring_cron("cron-a", "A", created_at=timezone.now())

    # The count does not depend on the process data: both modes report it.
    for include_processes, twicc_pid in ((False, None), (True, 1234)):
        data = build_topology(root, include_processes=include_processes, twicc_pid=twicc_pid)
        nodes = {node["id"]: node for node in data["nodes"]}

        assert nodes["A"]["active_cron_count"] == 1
        assert nodes["B"]["active_cron_count"] == 0


def test_topology_cron_count_ignores_expired(project):
    root = make_session(project, "A", title="Root")
    make_recurring_cron(
        "cron-expired",
        "A",
        created_at=timezone.now() - SessionCron.CLAUDE_RECURRING_MAX_AGE - timedelta(hours=1),
    )

    data = build_topology(root, include_processes=False)

    assert data["nodes"][0]["active_cron_count"] == 0
