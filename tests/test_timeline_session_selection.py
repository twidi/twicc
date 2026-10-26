"""Timeline eligibility policies must not change existing session listings."""

from types import SimpleNamespace

import pytest
from django.utils import timezone

from twicc.cli.sessions import build_filtered_queryset
from twicc.core.models import ProcessRun, Project, Session


@pytest.fixture
def tree(db):
    project = Project.objects.create(id="-timeline-selection", directory="/tmp/timeline-selection")

    def create(sid, **fields):
        return Session.objects.create(
            id=sid, project=project, provider="codex", file_path=f"{sid}.jsonl",
            created_at=timezone.now(), user_message_count=1, **fields,
        )

    root = create("root")
    visible = create("visible", spawned_by=root, spawn_root=root, annotations={"team": "backend", "ready": True})
    hidden = create("hidden", spawned_by=root, spawn_root=root, hidden=True)
    archived = create("archived", spawned_by=root, spawn_root=root, archived=True)
    native = create("native", type="subagent", parent_session=root)
    unindexed = create("unindexed")
    Session.objects.filter(pk=unindexed.pk).update(created_at=None, user_message_count=0)
    return SimpleNamespace(root=root, visible=visible, hidden=hidden, archived=archived, native=native)


def selected(**kwargs):
    qs, _ = build_filtered_queryset(**kwargs)
    return set(qs.values_list("id", flat=True))


def test_timeline_policy_keeps_hidden_children_out(tree):
    assert selected(
        spawn_tree="root", archived=True, require_indexed=False, filiation_includes_hidden=False,
    ) == {"root", "visible", "archived"}


def test_default_policy_preserves_existing_sessions_selection(tree):
    assert selected(spawn_tree="root") == {"root", "visible", "hidden"}


def test_timeline_policy_can_select_unindexed_and_native_sessions(tree):
    assert selected(root_only=False, require_indexed=False, archived=True) == {
        "root", "visible", "archived", "native", "unindexed",
    }


@pytest.mark.parametrize("visibility, expected", [
    ({"include_hidden": True}, {"root", "visible", "hidden", "archived"}),
    ({"only_hidden": True}, {"hidden"}),
])
def test_timeline_policy_visibility_flags(tree, visibility, expected):
    assert selected(
        spawn_tree="root", archived=True, require_indexed=False, filiation_includes_hidden=False, **visibility,
    ) == expected


def test_timeline_policy_reuses_state_and_annotations(tree, monkeypatch):
    monkeypatch.setattr("twicc.cli._process_state.resolve_listing_twicc_pid", lambda: 4242)
    for sid, state in (("visible", "user_turn"), ("root", "assistant_turn")):
        ProcessRun.objects.create(
            session_id=sid, provider="codex", twicc_pid=4242, state=state,
            started_at=timezone.now(), last_state_change_at=timezone.now(),
        )
    policy = {"root_only": False, "require_indexed": False, "filiation_includes_hidden": False}
    assert selected(annotation=["team=backend", "ready=true"], active=True, **policy) == {"visible"}
    assert selected(state=["assistant_turn", "user_turn"], **policy) == {"root", "visible"}
    assert selected(annotation=["team=backend", "ready=false"], **policy) == set()
