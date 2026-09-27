"""The ``/subagents/`` snapshot: per-agent run state and interactions (design §7.2, §9 "Snapshot").

Rows are hand-built through the ORM, with the helpers of the run-model tests;
``agent_run_states`` is covered there, these tests check what the snapshot
serves from it.
"""

import asyncio

import pytest
from django.test import AsyncClient

from twicc.core.agent_runs import agent_run_states, serialize_runs
from twicc.core.models import AgentInteractionKind, SessionItem, Share
from twicc.core.services.share_tokens import mint_token
from twicc.core.session_queries import build_subagents_state, serialize_agent_links
from twicc.share.display import visible_call_lines
from tests import test_agent_run_states, test_subagents_tree_endpoint
from tests.test_agent_run_states import (
    ROOT_ID,
    child,
    interaction,
    item,
    link,
    make_session,
    result,
    t,
    ui_stop,
)

# Register the shared pytest fixtures.
project = test_agent_run_states.project
root = test_agent_run_states.root
codex_root = test_agent_run_states.codex_root
tree = test_subagents_tree_endpoint.tree


def entries(root, **kwargs):
    return {entry["agent_id"]: entry for entry in build_subagents_state(root, **kwargs)}


def listed(entry):
    return [(row["owner_session_id"], row["tool_use_id"], row["opens_run"]) for row in entry["interactions"]]


# --- Per-agent state and interactions ----------------------------------------


def test_entry_carries_the_agents_run_state_and_interactions(root):
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, started_at=t(0))
    result(root, "tu-spawn", line=2, at=t(1), use_line=1)
    interaction(root, "a1", "tu-msg", line=5, event_line=6, opens_run=True, started_at=t(2))
    result(root, "tu-msg", line=6, at=t(2), use_line=5)

    entry = entries(root)["a1"]

    assert entry["running"] is True
    assert entry["run_started_at"] == t(2).isoformat()
    assert entry["run_background"] is True
    # The agent's stop time (its closed spawn run), no longer the link's queue time.
    assert entry["stopped_at"] == t(1).isoformat()
    assert entry["runs"] == serialize_runs(agent_run_states(root, ["a1"])["a1"])
    assert [run["tool_use_id"] for run in entry["runs"]] == ["tu-spawn", "tu-msg"]
    assert entry["interactions"] == [{
        "owner_session_id": ROOT_ID,
        "tool_use_id": "tu-msg",
        "tool_use_line_num": 5,
        "kind": AgentInteractionKind.MESSAGE,
        "opens_run": True,
        "started_at": t(2).isoformat(),
    }]
    assert "trust_agent_stopped" not in entry


def test_interaction_owned_by_a_subagent_is_listed_on_its_target(root):
    launcher = child(root, "a1")
    child(root, "a2")
    link(root, "a1", "tu-spawn-1", line=1, started_at=t(0))
    link(launcher, "a2", "tu-spawn-2", line=3, background=True, started_at=t(1))
    result(launcher, "tu-spawn-2", line=4, at=t(1), use_line=3)
    interaction(launcher, "a2", "tu-stop", kind=AgentInteractionKind.STOP, line=8, started_at=t(3))
    result(launcher, "tu-stop", line=9, at=t(4), use_line=8)

    rows = entries(root)

    assert listed(rows["a2"]) == [("a1", "tu-stop", False)]
    assert rows["a2"]["running"] is False
    assert rows["a2"]["stopped_at"] == t(4).isoformat()
    assert rows["a1"]["interactions"] == []


def test_interaction_owned_by_another_roots_session_is_not_listed(root, project):
    """Tree rule owner scope, snapshot half (§9): a copy under another root never shows."""
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, background=True, started_at=t(0))
    other_root = make_session(project, "other-root")
    other_sub = make_session(project, "other-sub", parent=other_root)
    interaction(other_root, "a1", "tu-fork-msg", line=4, opens_run=True, started_at=t(2))
    interaction(other_sub, "a1", "tu-fork-stop", kind=AgentInteractionKind.STOP, line=5, started_at=t(3))
    result(other_sub, "tu-fork-stop", line=6, at=t(3), use_line=5)
    interaction(root, "a1", "tu-own", line=7, started_at=t(4))

    entry = entries(root)["a1"]

    assert listed(entry) == [(ROOT_ID, "tu-own", False)]
    assert entry["running"] is True


def test_serialize_agent_links_without_run_states_reads_not_running(root):
    child(root, "a1")
    row = link(root, "a1", "tu-spawn", started_at=t(0))

    [entry] = serialize_agent_links([row])

    assert entry["running"] is False
    assert entry["runs"] == []
    assert entry["interactions"] == []
    assert entry["stopped_at"] is None


# --- Frozen filter -------------------------------------------------------------


def test_frozen_interactions_follow_the_link_filter(root):
    launcher = child(root, "a1")
    child(root, "a2")
    late = child(root, "a3")
    link(root, "a1", "tu-spawn-1", line=1, background=True, started_at=t(0))
    link(root, "a2", "tu-spawn-2", line=2, background=True, started_at=t(0))
    link(root, "a3", "tu-spawn-3", line=30, background=True, started_at=t(9))
    interaction(root, "a2", "tu-before", line=5, started_at=t(1))
    interaction(root, "a2", "tu-after", line=20, started_at=t(5))
    interaction(launcher, "a2", "tu-by-visible", line=3, started_at=t(2))
    interaction(late, "a2", "tu-by-late", line=2, opens_run=True, started_at=t(10))

    rows = entries(root, frozen_at_line=12)

    assert set(rows) == {"a1", "a2"}
    assert sorted(listed(rows["a2"])) == [("a1", "tu-by-visible", False), (ROOT_ID, "tu-before", False)]
    assert rows["a1"]["interactions"] == []
    # Not frozen: every interaction of the tree is listed.
    assert len(entries(root)["a2"]["interactions"]) == 4


def test_frozen_root_send_message_decided_after_the_freeze_is_not_a_run(root):
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, started_at=t(0))
    result(root, "tu-spawn", line=2, at=t(1), use_line=1)
    interaction(root, "a1", "tu-msg", line=10, event_line=14, opens_run=True, started_at=t(3))
    result(root, "tu-msg", line=14, at=t(3), use_line=10)

    frozen = entries(root, frozen_at_line=12)["a1"]

    assert listed(frozen) == [(ROOT_ID, "tu-msg", False)]
    assert frozen["running"] is False  # stopped, as at the freeze
    assert [run["tool_use_id"] for run in frozen["runs"]] == ["tu-spawn"]
    later = entries(root, frozen_at_line=15)["a1"]
    assert listed(later) == [(ROOT_ID, "tu-msg", True)]
    assert later["running"] is True


def test_frozen_codex_followup_whose_interacted_line_is_after_the_freeze_is_not_a_run(codex_root):
    child(codex_root, "a1")
    link(codex_root, "a1", "tu-spawn", line=1, started_at=t(0))
    result(codex_root, "tu-spawn", line=2, at=t(1), use_line=1)
    interaction(codex_root, "a1", "tu-followup", line=10, event_line=13, opens_run=True, started_at=t(3))

    assert listed(entries(codex_root, frozen_at_line=12)["a1"]) == [("codex-root", "tu-followup", False)]
    assert entries(codex_root, frozen_at_line=12)["a1"]["running"] is False
    assert listed(entries(codex_root, frozen_at_line=13)["a1"]) == [("codex-root", "tu-followup", True)]
    assert entries(codex_root, frozen_at_line=13)["a1"]["running"] is True


@pytest.mark.parametrize("ui_at,item_line,running", [
    (t(4), 5, False),   # before the freeze time: the Stop button stopped it
    (t(6), 5, True),    # after the freeze time: not yet stopped at the freeze
    (t(1), 20, True),   # no timestamp at or before the frozen line: every ui row is dropped
])
def test_frozen_root_ui_row_against_the_freeze_time(root, ui_at, item_line, running):
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, background=True, started_at=t(0))
    result(root, "tu-spawn", line=2, at=t(0), use_line=1)
    item(root, item_line, t(5))
    ui_stop(root, "a1", ui_at)

    assert entries(root, frozen_at_line=10)["a1"]["running"] is running
    assert entries(root)["a1"]["running"] is False  # live: every ui row counts


# --- Share display ceiling -----------------------------------------------------


def test_visible_call_lines_applies_the_ceiling(root):
    SessionItem.objects.create(session=root, line_num=1, content="{}", display_level=1)
    SessionItem.objects.create(session=root, line_num=2, content="{}", display_level=3)
    SessionItem.objects.create(session=root, line_num=3, content="{}", display_level=None)
    pairs = [(ROOT_ID, 1), (ROOT_ID, 2), (ROOT_ID, 3), (ROOT_ID, 4)]

    assert visible_call_lines(pairs, 2) == {(ROOT_ID, 1)}
    assert visible_call_lines(pairs, 3) == set(pairs)


def test_display_ceiling_drops_interactions_whose_call_item_is_above_it(root):
    child(root, "a1")
    sibling = child(root, "a2")
    link(root, "a1", "tu-spawn", line=1, background=True, started_at=t(0))
    interaction(root, "a1", "tu-shown", line=5, started_at=t(1))
    interaction(root, "a1", "tu-debug", line=6, started_at=t(2))
    interaction(sibling, "a1", "tu-sub-debug", line=7, started_at=t(3))
    SessionItem.objects.create(session=root, line_num=5, content="{}", display_level=1)
    SessionItem.objects.create(session=root, line_num=6, content="{}", display_level=3)
    SessionItem.objects.create(session=sibling, line_num=7, content="{}", display_level=3)

    assert listed(entries(root, display_ceiling=2)["a1"]) == [(ROOT_ID, "tu-shown", False)]
    assert len(entries(root, display_ceiling=3)["a1"]["interactions"]) == 3
    assert len(entries(root)["a1"]["interactions"]) == 3


def test_frozen_snapshot_applies_the_display_ceiling_too(root):
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, background=True, started_at=t(0))
    for tool, line, level in (("tu-shown", 5, 1), ("tu-debug", 6, 3), ("tu-after", 20, 1)):
        interaction(root, "a1", tool, line=line, started_at=t(1))
        SessionItem.objects.create(session=root, line_num=line, content="{}", display_level=level)

    # The freeze drops tu-after, the ceiling drops tu-debug.
    assert listed(entries(root, frozen_at_line=12, display_ceiling=2)["a1"]) == [(ROOT_ID, "tu-shown", False)]
    assert {row[1] for row in listed(entries(root, frozen_at_line=12, display_ceiling=3)["a1"])} == {
        "tu-shown", "tu-debug",
    }
    assert {row[1] for row in listed(entries(root, display_ceiling=2)["a1"])} == {"tu-shown", "tu-after"}


@pytest.mark.parametrize("mode,expected", [("normal", {"tu-shown"}), ("debug", {"tu-shown", "tu-debug"})])
def test_share_snapshot_drops_interactions_above_the_display_ceiling(tree, mode, expected):
    root, launcher, child_session = tree
    for tool, line, level in (("tu-shown", 130, 1), ("tu-debug", 131, 3)):
        interaction(root, child_session.id, tool, line=line, started_at=t(1))
        SessionItem.objects.create(session=root, line_num=line, content="{}", display_level=level)
    share = Share.objects.create(kind="session", session=root, token=mint_token(),
                                 options={"mode": "live", "include_subagents": True, "max_display_mode": mode})

    response = asyncio.run(AsyncClient().get(f"/share/{share.token}/api/subagents/"))

    assert response.status_code == 200
    row = next(row for row in response.json() if row["agent_id"] == child_session.id)
    assert {entry["tool_use_id"] for entry in row["interactions"]} == expected


def test_owner_api_lists_every_interaction(tree):
    root, launcher, child_session = tree
    interaction(root, child_session.id, "tu-debug", line=131, started_at=t(1))
    SessionItem.objects.create(session=root, line_num=131, content="{}", display_level=3)

    response = asyncio.run(AsyncClient().get(f"/api/projects/{root.project_id}/sessions/{root.id}/subagents/"))

    row = next(row for row in response.json() if row["agent_id"] == child_session.id)
    assert [entry["tool_use_id"] for entry in row["interactions"]] == ["tu-debug"]
