"""The ``agent_interaction`` WebSocket messages of the live pass and the watcher.

The live pass returns the ``agent_interaction`` payloads of the interactions a
batch created or whose ``opens_run`` changed (tuple index 7), filtered by the
tree rule. The watcher sends them after the batch's ``agent_link_created``
messages, and follows each ``agent_link_created`` with the interactions
targeting or owned by its agent (the late tree rule). Design:
``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`` §5.1, §7.3
and §9.
"""
from __future__ import annotations

import pytest

from twicc.core.agent_runs import interaction_payloads, late_tree_rule_payloads
from twicc.core.models import AgentInteraction, AgentLink, Session
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher

from tests.test_agent_run_stop_step import (
    Watched,
    _claude_tree,
    claude_watch,
    foreground_spawn,
    live,
    prompt,
)
from tests.test_claude_agent_runs import (
    AGENT,
    OTHER,
    SHELL,
    ack,
    at,
    line,
    result,
    resumed_ack,
    send,
    spawn,
    task_stop,
    text_resumed_ack,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAYLOAD_KEYS = {
    "root_session_id", "owner_session_id", "agent_session_id", "tool_use_id", "tool_use_line_num", "kind",
    "opens_run", "started_at",
}


@pytest.fixture
def claude(transactional_db, provider_home):
    return _claude_tree(provider_home)


def payload(root, owner, tool_use_id, *, line_num, kind, opens_run, started_at, agent=AGENT):
    return {
        "root_session_id": root.id, "owner_session_id": owner.id, "agent_session_id": agent,
        "tool_use_id": tool_use_id, "tool_use_line_num": line_num, "kind": kind, "opens_run": opens_run,
        "started_at": started_at.isoformat(),
    }


def message(root, owner, tool_use_id, **kwargs):
    return {**payload(root, owner, tool_use_id, **kwargs), "type": "agent_interaction",
            "project_id": root.project_id}


def kinds(watched):
    return [call.args[1]["type"] for call in watched.messages.call_args_list]


# ---------------------------------------------------------------------------
# Live tuple index 7
# ---------------------------------------------------------------------------


def test_live_tuple_carries_created_and_flipped_interactions(claude):
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    created = live(claude, root, send("tool_send", AGENT, 10))
    assert created[7] == [payload(root, root, "tool_send", line_num=4, kind="message", opens_run=False,
                                  started_at=at(10))]
    assert set(created[7][0]) == PAYLOAD_KEYS
    # The first result flips opens_run: started_at moves to the ack time.
    flipped = live(claude, root, resumed_ack("tool_send", AGENT, 12))
    assert flipped[7] == [payload(root, root, "tool_send", line_num=4, kind="message", opens_run=True,
                                  started_at=at(12))]
    # Nothing changed on the interaction: nothing to send.
    assert live(claude, root, line("user", "more", 13))[7] == []


def test_live_tuple_skips_interactions_failing_the_tree_rule(claude):
    root, children, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    # A shell task id is never a linked agent.
    outcome = live(claude, root, task_stop("tool_shell", SHELL, 10))
    assert AgentInteraction.objects.filter(tool_use_id="tool_shell").exists()
    assert outcome[7] == []
    # A subagent's call to an agent with no link yet: no payload until the link exists.
    outcome = live(claude, children[OTHER], send("tool_sub", "a3333333333333333", 11))
    assert AgentInteraction.objects.filter(tool_use_id="tool_sub").exists()
    assert outcome[7] == []


def test_interaction_payloads_apply_the_owner_scope(claude):
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    live(claude, root, send("tool_send", AGENT, 10))
    # The same call copied under a session outside the tree (e.g. a CLI fork).
    foreign = Session.objects.create(id="foreign-root", project=root.project, provider=root.provider,
                                     file_path="foreign.jsonl")
    row = AgentInteraction.objects.get(tool_use_id="tool_send")
    AgentInteraction.objects.create(
        session=foreign, tool_use_line_num=row.tool_use_line_num, event_line_num=row.event_line_num,
        tool_use_id="tool_send", agent_id=AGENT, kind=row.kind, started_at=row.started_at,
    )
    assert [p["owner_session_id"] for p in interaction_payloads(
        root.id, [(foreign.id, "tool_send"), (root.id, "tool_send"), (root.id, "tool_send")],
    )] == [root.id]
    assert [p["owner_session_id"] for p in late_tree_rule_payloads(root.id, AGENT)] == [root.id]


# ---------------------------------------------------------------------------
# Watcher: agent_interaction on creation and on the opens_run change
# ---------------------------------------------------------------------------


def test_watcher_sends_agent_interaction_on_creation_and_flip(claude, monkeypatch):
    root, _, _ = claude
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    assert watched.sent("agent_interaction") == []

    claude_watch(claude, watched, root, send("tool_send", AGENT, 10), task_stop("tool_shell", SHELL, 10.5))
    assert watched.sent("agent_interaction") == [
        message(root, root, "tool_send", line_num=4, kind="message", opens_run=False, started_at=at(10)),
    ]

    claude_watch(claude, watched, root, resumed_ack("tool_send", AGENT, 12))
    assert watched.sent("agent_interaction") == [
        message(root, root, "tool_send", line_num=4, kind="message", opens_run=True, started_at=at(12)),
    ]
    # Sent before the stop step's run state (§7.3 order).
    sent = kinds(watched)
    assert sent.index("agent_interaction") < sent.index("agent_run_state")


# ---------------------------------------------------------------------------
# Watcher: the late tree rule
# ---------------------------------------------------------------------------


def test_late_tree_rule_resends_an_interaction_targeting_the_new_agent(claude, monkeypatch):
    """§9: a Claude interaction synced before its target's spawn link."""
    root, children, _ = claude
    caller, target = children[OTHER], children[AGENT]
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude, watched, root, prompt(0), foreground_spawn("tool_spawn", 1),
                 result("tool_spawn", "done", 2))
    claude_watch(claude, watched, caller, send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11))
    assert AgentInteraction.objects.get(tool_use_id="tool_sub").opens_run
    assert watched.sent("agent_interaction") == []  # no link for AGENT yet

    claude_watch(claude, watched, target, line("user", "work for tool_spawn", 1.5, agentId=AGENT))
    assert AgentLink.objects.get().agent_id == AGENT
    assert watched.sent("agent_interaction") == [
        message(root, caller, "tool_sub", line_num=1, kind="message", opens_run=True, started_at=at(11)),
    ]
    sent = kinds(watched)
    assert sent.index("agent_link_created") < sent.index("agent_interaction")


def test_late_tree_rule_resends_an_interaction_owned_by_the_new_agent(claude, monkeypatch):
    """§9: an interaction owned by an agent whose own spawn link arrives later."""
    root, children, _ = claude
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    # OTHER is spawned (link exists); AGENT has no link yet.
    claude_watch(claude, watched, root, prompt(0), spawn("tool_other", 1), ack("tool_other", OTHER, 2),
                 foreground_spawn("tool_spawn", 3), result("tool_spawn", "done", 4))
    assert list(AgentLink.objects.values_list("agent_id", flat=True)) == [OTHER]
    # AGENT's call to OTHER passes the tree rule (sent now), but the share
    # relay drops it while AGENT is unknown to a viewer.
    AgentInteraction.objects.create(
        session=children[AGENT], tool_use_line_num=7, event_line_num=7, tool_use_id="tool_own",
        agent_id=OTHER, kind="message", started_at=at(20),
    )
    claude_watch(claude, watched, children[AGENT], line("user", "work for tool_spawn", 3.5, agentId=AGENT))
    assert AgentLink.objects.filter(agent_id=AGENT).exists()
    assert watched.sent("agent_interaction") == [
        message(root, children[AGENT], "tool_own", line_num=7, kind="message", opens_run=False,
                started_at=at(20), agent=OTHER),
    ]
    sent = kinds(watched)
    assert sent.index("agent_link_created") < sent.index("agent_interaction")


def test_late_tree_rule_skips_the_batch_own_interactions(claude, monkeypatch):
    """An interaction created in the link's batch is sent once, as a batch update."""
    root, _, _ = claude
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                 send("tool_send", AGENT, 10))
    assert watched.sent("agent_interaction") == [
        message(root, root, "tool_send", line_num=4, kind="message", opens_run=False, started_at=at(10)),
    ]
    sent = kinds(watched)
    assert sent.index("agent_link_created") < sent.index("agent_interaction")
