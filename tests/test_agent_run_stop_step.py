"""The stop step: one per live batch, from run evidence to stamps and broadcasts (both providers).

``run_stop_step`` compares the run states of the batch's affected agents
without the batch's evidence (``exclude``) and with it: a run closed by the
batch stamps its agent's ``last_stopped_at`` (monotonic guard) and returns an
``AgentStoppedUpdate``; the watcher's ``broadcast_agent_run_outcome`` sends
``agent_run_state``, then ``session_updated`` / ``agent_stopped`` for stamped
updates, and the watcher fires ``_after_agents_stopped`` with the tree root id.
Design: ``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md``
§5.4 (``exclude``, late tree rule), §6.3, §6.4 and §9.
"""
from __future__ import annotations

import asyncio
import shutil
from unittest.mock import AsyncMock

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from watchfiles import Change

from twicc.core.agent_runs import (
    RunStateExclude,
    agent_run_states,
    run_stop_step,
    serialize_run_state,
)
from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentLink,
    AgentRunEnd,
    Project,
    Session,
    SessionType,
    ToolResultLink,
)
from twicc.providers import compute_base, sessions_watcher
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
from twicc.providers.compute_base import (
    AgentRunSignalsCollector,
    AgentStoppedUpdate,
    BaseSessionCompute,
    ToolResultUpdate,
)
from twicc.providers.sessions_watcher import ParsedSessionFile, broadcast_agent_run_outcome

from tests.codex_agent_run_fixtures import (
    AGENT_A,
    PATH_A,
    ROOT,
    Rollout,
    _fixture,
    fixture_child_turn_ends,
    fixture_t20_sequence,
)
from tests.codex_agent_run_fixtures import at as codex_at
from tests.test_claude_agent_runs import (
    AGENT,
    OTHER,
    SHELL,
    ack,
    at,
    calls,
    interrupt,
    line,
    notif_user,
    result,
    send,
    spawn,
    stop_ok,
    task_stop,
    text_resumed_ack,
)
from tests.test_codex_agent_runs_live import LiveReplay

pytestmark = pytest.mark.django_db

PROJECT = "stop-step-project"


# ---------------------------------------------------------------------------
# Claude harness
# ---------------------------------------------------------------------------


def _claude_tree(provider_home):
    project = Project.objects.create(id=PROJECT)
    root = Session.objects.create(id="stop-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path=f"{PROJECT}/stop-root.jsonl")
    children = {
        agent_id: Session.objects.create(
            id=agent_id, project=project, provider=Provider.CLAUDE_CODE, type=SessionType.SUBAGENT,
            parent_session=root, file_path=f"{PROJECT}/stop-root/subagents/agent-{agent_id}.jsonl")
        for agent_id in (AGENT, OTHER)
    }
    return root, children, provider_home.claude / "projects"


@pytest.fixture
def claude(db, provider_home):
    return _claude_tree(provider_home)


@pytest.fixture
def claude_tx(transactional_db, provider_home):
    return _claude_tree(provider_home)


def append(session, home, entries):
    path = home / session.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for parsed in entries:
            handle.write(orjson.dumps(parsed) + b"\n")
    return path


def live(tree, session, *entries, compute=None):
    """Append ``entries`` to ``session``'s file and run one live sync; return the live tuple."""
    _, _, home = tree
    path = append(session, home, entries)
    return (compute or ClaudeCodeSessionCompute()).sync_session_items_from_file(
        Session.objects.get(id=session.id), path,
    )


def prompt(seconds):
    return line("user", "go", seconds)


def foreground_spawn(tool, seconds):
    return calls(seconds, (tool, "Agent", {"prompt": f"work for {tool}"}))


def session_start(seconds):
    return {"type": "progress", "timestamp": at(seconds).isoformat(), "data": {"hookEvent": "SessionStart"}}


def stopped_state(root, agent_id):
    return agent_run_states(Session.objects.get(id=root.id), [agent_id])[agent_id]


def last_stopped_at(agent_id):
    return Session.objects.get(id=agent_id).last_stopped_at


class Watched:
    """A watcher whose broadcasts and stop hook are recorded (``broadcast_message`` patched)."""

    def __init__(self, watcher, monkeypatch):
        self.watcher = watcher
        self.messages = AsyncMock()
        monkeypatch.setattr(sessions_watcher, "broadcast_message", self.messages)
        self.stopped_hook = AsyncMock()
        monkeypatch.setattr(watcher, "_after_agents_stopped", self.stopped_hook)

    def sync(self, session, path):
        session = Session.objects.get(id=session.id)
        parsed = ParsedSessionFile(session.project_id, session.id, session.type, file_path=session.file_path,
                                   parent_session_id=session.parent_session_id)
        self.messages.reset_mock()
        self.stopped_hook.reset_mock()
        asyncio.run(self.watcher.sync_and_broadcast(path, parsed, Change.modified, None))

    def sent(self, kind):
        return [call.args[1] for call in self.messages.call_args_list if call.args[1]["type"] == kind]

    def session_updates(self, session_id):
        return [message for message in self.sent("session_updated") if message["session"]["id"] == session_id]


def claude_watch(tree, watched, session, *entries):
    _, _, home = tree
    watched.sync(session, append(session, home, entries))


def expected_run_state(root, agent_id):
    return {**serialize_run_state(root.id, agent_id, stopped_state(root, agent_id)),
            "type": "agent_run_state", "project_id": root.project_id}


# ---------------------------------------------------------------------------
# One batch opens and closes a run
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_run_opened_and_closed_in_one_batch_stops_and_fires_the_hook(claude_tx, monkeypatch):
    root, _, _ = claude_tx
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude_tx, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                 notif_user(AGENT, "tool_spawn", 3))
    assert watched.sent("agent_run_state") == [expected_run_state(root, AGENT)]
    assert watched.sent("agent_stopped") == [{"type": "agent_stopped", "agent_session_id": AGENT,
                                              "stopped_at": at(3).isoformat(), "root_session_id": root.id}]
    assert len(watched.session_updates(AGENT)) == 1
    watched.stopped_hook.assert_awaited_once_with(root.id, [AGENT])
    assert last_stopped_at(AGENT) == at(3)


def test_run_opened_and_closed_in_one_batch_returns_the_update(claude):
    root, _, _ = claude
    outcome = live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                   notif_user(AGENT, "tool_spawn", 3))
    assert outcome[5] == [AgentStoppedUpdate(AGENT, at(3), True)]
    assert [payload["agent_session_id"] for payload in outcome[8]] == [AGENT]


# ---------------------------------------------------------------------------
# Two closing pieces of one run in one batch close it once
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_claude_notification_line_closes_once(claude_tx, monkeypatch):
    """The user-form notification is both the second result and an ``AgentRunEnd``."""
    root, _, _ = claude_tx
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude_tx, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    assert watched.sent("agent_stopped") == []
    claude_watch(claude_tx, watched, root, notif_user(AGENT, "tool_spawn", 3))
    assert ToolResultLink.objects.filter(session=root, tool_use_id="tool_spawn").count() == 2
    assert AgentRunEnd.objects.filter(agent_id=AGENT, tool_use_id="tool_spawn").count() == 1
    stops = watched.sent("agent_stopped")
    assert len(stops) == 1
    assert stops[0]["stopped_at"] == at(3).isoformat() == stopped_state(root, AGENT).stopped_at.isoformat()
    watched.stopped_hook.assert_awaited_once_with(root.id, [AGENT])


def _codex_two_pieces():
    root = Rollout(ROOT)
    root.meta(0)
    root.task_started(1, "t1")
    root.spawn(2, "c_spawn", AGENT_A, PATH_A)
    root.task_complete(3, "t1")
    root.task_started(10, "t2")
    root.completed(11, AGENT_A, PATH_A, "a1", mark="completed")
    root.final_answer(12, PATH_A, mark="final")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    return _fixture("two_pieces", root, child)


@pytest.mark.django_db(transaction=True)
def test_codex_completed_and_final_answer_close_once(tmp_path):
    fixture = _codex_two_pieces()
    replay = LiveReplay(fixture, tmp_path)
    assert replay.sync(ROOT, fixture.line(ROOT, "completed") - 1)[5] == []
    outcome = replay.sync(ROOT)
    root = Session.objects.get(id=ROOT)
    # One stamp at the earliest evidence: the ``completed`` time, not the FINAL_ANSWER's.
    assert outcome[5] == [AgentStoppedUpdate(AGENT_A, codex_at(11), True)]
    assert stopped_state(root, AGENT_A).stopped_at == codex_at(11)
    assert last_stopped_at(AGENT_A) == codex_at(11)

    messages = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sessions_watcher, "broadcast_message", messages)
        asyncio.run(broadcast_agent_run_outcome(
            None, root_session_id=ROOT, project_id=root.project_id,
            run_state_payloads=outcome[8], stopped_updates=outcome[5],
        ))
    sent = [call.args[1] for call in messages.call_args_list]
    assert [message["type"] for message in sent] == ["agent_run_state", "session_updated", "agent_stopped"]
    assert sent[2]["stopped_at"] == codex_at(11).isoformat()


# ---------------------------------------------------------------------------
# Evidence reaching an already-closed run
# ---------------------------------------------------------------------------


def test_late_final_answer_after_rule_five_closes_nothing(tmp_path):
    """The t20 case: the child's turn end closes the spawn; its late FINAL_ANSWER re-stamps nothing."""
    fixture = fixture_t20_sequence()
    replay = LiveReplay(fixture, tmp_path)
    replay.sync(ROOT, fixture.line(ROOT, "final_0") - 1)
    child_outcome = replay.sync(AGENT_A)
    assert child_outcome[5] == [AgentStoppedUpdate(AGENT_A, codex_at(50), True)]

    outcome = replay.sync(ROOT, fixture.line(ROOT, "final_0"))
    assert ToolResultLink.objects.filter(session_id=ROOT, tool_use_id="c_spawn").count() == 2
    assert outcome[5] == []
    assert [(payload["agent_session_id"], payload["running"]) for payload in outcome[8]] == [(AGENT_A, False)]
    assert last_stopped_at(AGENT_A) == codex_at(50)
    assert stopped_state(Session.objects.get(id=ROOT), AGENT_A).stopped_at == codex_at(50)


@pytest.mark.django_db(transaction=True)
def test_evidence_on_a_closed_run_sends_state_only(claude_tx, monkeypatch):
    """A compaction-style copy of the closing notification: no stamp, no agent_stopped, no hook."""
    root, _, _ = claude_tx
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude_tx, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                 notif_user(AGENT, "tool_spawn", 3))
    Session.objects.filter(id=AGENT).update(last_stopped_at=None)
    claude_watch(claude_tx, watched, root, notif_user(AGENT, "tool_spawn", 9))
    assert AgentRunEnd.objects.filter(agent_id=AGENT, tool_use_id="tool_spawn").count() == 2
    assert watched.sent("agent_run_state") == [expected_run_state(root, AGENT)]
    assert watched.sent("agent_stopped") == []
    assert watched.session_updates(AGENT) == []
    watched.stopped_hook.assert_not_awaited()
    assert last_stopped_at(AGENT) is None


# ---------------------------------------------------------------------------
# Late tree rule
# ---------------------------------------------------------------------------


def test_late_tree_rule_closes_the_run_once(claude):
    """A SendMessage run ended in the DB; its target's spawn link arrives later, spawn already closed."""
    root, children, _ = claude
    caller, target = children[OTHER], children[AGENT]
    assert live(claude, root, prompt(0), foreground_spawn("tool_spawn", 1), result("tool_spawn", "done", 2),
                notif_user(AGENT, "tool_sub", 20))[5] == []
    assert live(claude, caller, send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11))[5] == []
    assert AgentInteraction.objects.get(session=caller, tool_use_id="tool_sub").opens_run
    assert not AgentLink.objects.exists()

    outcome = live(claude, target, line("user", "work for tool_spawn", 1.5, agentId=AGENT))
    assert AgentLink.objects.get().agent_id == AGENT
    state = stopped_state(root, AGENT)
    assert [run.closed_at for run in state.runs] == [at(2), at(20)]
    assert outcome[5] == [AgentStoppedUpdate(AGENT, at(20), True)]
    assert last_stopped_at(AGENT) == at(20)

    assert live(claude, target, line("user", "more", 21))[5] == []
    assert last_stopped_at(AGENT) == at(20)


# ---------------------------------------------------------------------------
# Guard refusal and null stop time
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_guard_refusal_still_sends_state_and_fires_the_hook(claude_tx, monkeypatch):
    root, _, _ = claude_tx
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude_tx, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    Session.objects.filter(id=AGENT).update(last_updated_at=at(100))
    claude_watch(claude_tx, watched, root, notif_user(AGENT, "tool_spawn", 3))
    assert watched.sent("agent_run_state") == [expected_run_state(root, AGENT)]
    assert watched.sent("agent_stopped") == []
    assert watched.session_updates(AGENT) == []
    watched.stopped_hook.assert_awaited_once_with(root.id, [AGENT])
    assert last_stopped_at(AGENT) is None


@pytest.mark.django_db(transaction=True)
def test_null_stop_time_skips_the_stamp_but_fires_the_hook(claude_tx, monkeypatch):
    root, _, _ = claude_tx
    watched = Watched(ClaudeCodeSessionsWatcher(), monkeypatch)
    claude_watch(claude_tx, watched, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    untimed = notif_user(AGENT, "tool_spawn", 3)
    del untimed["timestamp"]
    claude_watch(claude_tx, watched, root, untimed)
    state = stopped_state(root, AGENT)
    assert not state.running and [run.closed_at for run in state.runs] == [None]
    assert watched.sent("agent_run_state") == [expected_run_state(root, AGENT)]
    assert watched.sent("agent_stopped") == []
    assert watched.session_updates(AGENT) == []
    watched.stopped_hook.assert_awaited_once_with(root.id, [AGENT])
    assert last_stopped_at(AGENT) is None


def test_null_stop_time_returns_an_unstamped_update(claude):
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))
    untimed = notif_user(AGENT, "tool_spawn", 3)
    del untimed["timestamp"]
    assert live(claude, root, untimed)[5] == [AgentStoppedUpdate(AGENT, None, False)]


def test_stopped_update_has_no_stamped_default():
    # The broadcast reads ``stopped_at`` of every stamped update: a default of
    # True would let a caller stamp an update whose time is None.
    with pytest.raises(TypeError):
        AgentStoppedUpdate(AGENT, None)


# ---------------------------------------------------------------------------
# ``exclude`` sets
# ---------------------------------------------------------------------------


def test_codex_stop_row_created_after_its_result_closes(tmp_path):
    root = Rollout(ROOT)
    root.meta(0)
    root.task_started(1, "t1")
    root.spawn(2, "c_spawn", AGENT_A, PATH_A)
    root.call(10, "interrupt_agent", "c_stop", {"target": PATH_A}, mark="stop")
    root.output(11, "c_stop", "{}", mark="stop_output")
    root.activity(13, "interrupted", "c_stop", AGENT_A, PATH_A, mark="stop_event")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    fixture = _fixture("stop_row_after_result", root, child)
    replay = LiveReplay(fixture, tmp_path)
    assert replay.sync(ROOT, fixture.line(ROOT, "stop_output"))[5] == []
    assert not AgentInteraction.objects.exists()
    outcome = replay.sync(ROOT)
    assert AgentInteraction.objects.get(tool_use_id="c_stop").kind == "stop"
    assert outcome[5] == [AgentStoppedUpdate(AGENT_A, codex_at(11), True)]
    assert last_stopped_at(AGENT_A) == codex_at(11)


def test_claude_task_stop_result_is_a_new_stop_record(claude):
    """The TaskStop row predates its result: the result's batch detects the flip."""
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
         task_stop("tool_stop", AGENT, 5))
    outcome = live(claude, root, stop_ok("tool_stop", AGENT, 6))
    assert outcome[5] == [AgentStoppedUpdate(AGENT, at(6), True)]


def test_stop_record_needs_all_its_results_in_the_batch(claude):
    """A stop's second non-error result in a later batch is not a new stop record: no re-stamp."""
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
         task_stop("tool_stop", AGENT, 5))
    assert live(claude, root, stop_ok("tool_stop", AGENT, 6))[5] == [AgentStoppedUpdate(AGENT, at(6), True)]
    Session.objects.filter(id=AGENT).update(last_stopped_at=at(8), last_updated_at=at(8))
    outcome = live(claude, root, stop_ok("tool_stop", AGENT, 7))
    assert ToolResultLink.objects.filter(session=root, tool_use_id="tool_stop", error__isnull=True).count() == 2
    assert outcome[5] == []
    assert last_stopped_at(AGENT) == at(8)


def test_subagent_send_message_flip_closes_an_already_ended_run_once(claude):
    root, children, _ = claude
    caller = children[OTHER]
    live(claude, root, prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
         notif_user(AGENT, "tool_spawn", 3))
    assert last_stopped_at(AGENT) == at(3)
    # The run's end reaches the root before the caller's first result.
    assert live(claude, root, notif_user(AGENT, "tool_sub", 20))[5] == []
    outcome = live(claude, caller, send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11))
    assert AgentInteraction.objects.get(session=caller, tool_use_id="tool_sub").opens_run
    assert outcome[5] == [AgentStoppedUpdate(AGENT, at(20), True)]
    assert last_stopped_at(AGENT) == at(20)
    assert live(claude, caller, line("user", "next", 30))[5] == []


# ---------------------------------------------------------------------------
# Rule 3 (root restart)
# ---------------------------------------------------------------------------


def test_rule_three_only_close_stamps_at_the_cutoff(claude):
    root, _, _ = claude
    live(claude, root, prompt(0), spawn("tool_spawn", 1))
    live(claude, root, session_start(5))
    outcome = live(claude, root, ack("tool_spawn", AGENT, 6))
    assert AgentLink.objects.get().started_at == at(1)
    assert outcome[5] == [AgentStoppedUpdate(AGENT, at(5), True)]
    assert last_stopped_at(AGENT) == at(5)
    # A later restart alone stamps nothing live.
    assert live(claude, root, session_start(10))[5] == []
    assert last_stopped_at(AGENT) == at(5)


# ---------------------------------------------------------------------------
# Unknown agents
# ---------------------------------------------------------------------------


def test_no_run_state_for_shell_or_monitor_ends(claude):
    root, _, _ = claude
    monitor = "m3cd4ef5a"
    outcome = live(claude, root, prompt(0),
                   calls(1, ("tool_bash", "Bash", {"command": "sleep 1", "run_in_background": True}),
                         ("tool_monitor", "Monitor", {"command": "tail -f log"})),
                   notif_user(SHELL, "tool_bash", 2), notif_user(monitor, "tool_monitor", 3))
    assert {end.agent_id for end in AgentRunEnd.objects.all()} == {SHELL, monitor}
    assert outcome[8] == []
    assert outcome[5] == []


# ---------------------------------------------------------------------------
# Hook routing (watcher side)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_codex_child_rule_five_fires_the_hook_with_the_root_id(tmp_path, monkeypatch):
    fixture = fixture_child_turn_ends()
    replay = LiveReplay(fixture, tmp_path)
    replay.sync(ROOT, fixture.line(ROOT, "completed") - 1)
    child_lines = fixture.session(AGENT_A).lines[:fixture.line(AGENT_A, "end_1")]
    path = replay.paths[AGENT_A]
    path.write_text("".join(f"{line_}\n" for line_ in child_lines))
    watched = Watched(CodexSessionsWatcher(), monkeypatch)
    watched.sync(replay.sessions[AGENT_A], path)
    assert AgentRunEnd.objects.filter(session_id=AGENT_A, status="turn_complete").count() == 1
    watched.stopped_hook.assert_awaited_once_with(ROOT, [AGENT_A])
    assert [stop["agent_session_id"] for stop in watched.sent("agent_stopped")] == [AGENT_A]
    assert [state["root_session_id"] for state in watched.sent("agent_run_state")] == [ROOT]


# ---------------------------------------------------------------------------
# Query budget
# ---------------------------------------------------------------------------


def _budget_tree(prefix, agents, filler_results):
    project = Project.objects.get_or_create(id=PROJECT)[0]
    root = Session.objects.create(id=f"{prefix}-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path=f"{PROJECT}/{prefix}-root.jsonl", last_started_at=at(0))
    agent_ids = [f"{prefix}-agent-{index}" for index in range(agents)]
    Session.objects.bulk_create([
        Session(id=agent_id, project=project, provider=Provider.CLAUDE_CODE, type=SessionType.SUBAGENT,
                parent_session=root, file_path=f"{PROJECT}/{prefix}/{agent_id}.jsonl")
        for agent_id in agent_ids
    ])
    AgentLink.objects.bulk_create([
        AgentLink(session=root, tool_use_line_num=index + 1, tool_use_id=f"spawn-{index}", agent_id=agent_id,
                  is_background=True, started_at=at(1))
        for index, agent_id in enumerate(agent_ids)
    ])
    rows = [ToolResultLink(session=root, tool_use_line_num=index + 1, tool_result_line_num=10_000 + index,
                           tool_use_id=f"spawn-{index}", tool_name="Agent", tool_result_at=at(2))
            for index in range(agents)]
    rows += [ToolResultLink(session=root, tool_use_line_num=1, tool_result_line_num=100_000 + index,
                            tool_use_id=f"filler-{index}", tool_name="Bash", tool_result_at=at(3))
             for index in range(filler_results)]
    ToolResultLink.objects.bulk_create(rows, batch_size=2000)
    closing = ToolResultLink.objects.create(session=root, tool_use_line_num=1, tool_result_line_num=999_999,
                                            tool_use_id="spawn-0", tool_name="Agent", tool_result_at=at(4))
    return root, agent_ids[0], RunStateExclude(tool_result_link_ids=frozenset({closing.id}))


def test_stop_step_query_budget_does_not_grow_with_the_tree(django_assert_num_queries):
    small_root, small_agent, small_exclude = _budget_tree("small", 5, 0)
    with CaptureQueriesContext(connection) as small:
        small_outcome = run_stop_step(small_root.id, [small_agent], small_exclude)
    assert small_outcome.stopped == [AgentStoppedUpdate(small_agent, at(4), True)]

    big_root, big_agent, big_exclude = _budget_tree("big", 200, 15_000)
    with django_assert_num_queries(len(small.captured_queries)):
        big_outcome = run_stop_step(big_root.id, [big_agent], big_exclude)
    assert big_outcome.stopped == [AgentStoppedUpdate(big_agent, at(4), True)]


def _live_collection(root, exclude):
    """The end-of-batch collection plus the step, for a batch whose one result closes ``spawn-0``."""
    (link_id,) = exclude.tool_result_link_ids
    update = ToolResultUpdate(session_id=root.id, tool_use_id="spawn-0", result_count=2, completed_at=at(4),
                              link_id=link_id)
    return BaseSessionCompute._run_live_stop_step(root, [], [update], AgentRunSignalsCollector())


def test_live_collection_query_budget_does_not_grow_with_the_tree(django_assert_num_queries):
    small_root, small_agent, small_exclude = _budget_tree("small", 5, 0)
    with CaptureQueriesContext(connection) as small:
        small_outcome = _live_collection(small_root, small_exclude)
    assert small_outcome.stopped == [AgentStoppedUpdate(small_agent, at(4), True)]

    big_root, big_agent, big_exclude = _budget_tree("big", 200, 15_000)
    with django_assert_num_queries(len(small.captured_queries)):
        big_outcome = _live_collection(big_root, big_exclude)
    assert big_outcome.stopped == [AgentStoppedUpdate(big_agent, at(4), True)]


# ---------------------------------------------------------------------------
# Initial-sync order
# ---------------------------------------------------------------------------


def _reset_db_and_caches(provider_home, monkeypatch):
    shutil.rmtree(provider_home.claude / "projects" / PROJECT, ignore_errors=True)
    for model in (AgentInteraction, AgentRunEnd, ToolResultLink, AgentLink):
        model.objects.all().delete()
    Session.objects.all().delete()
    Project.objects.all().delete()
    monkeypatch.setattr(compute_base, "AGENTS_LINKS_DONE_CACHE", set())
    monkeypatch.setattr(compute_base, "AGENTS_PROMPT_CACHE", {})


def _all_states(root_id, agent_ids):
    root = Session.objects.get(id=root_id)
    return {agent_id: serialize_run_state(root_id, agent_id, state)
            for agent_id, state in agent_run_states(root, agent_ids).items()}


def _claude_order_run(provider_home, reverse):
    tree = _claude_tree(provider_home)
    root, children, _ = tree
    compute = ClaudeCodeSessionCompute()
    steps = [
        (root, [prompt(0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
                spawn("tool_other", 3), ack("tool_other", OTHER, 4),
                notif_user(AGENT, "tool_spawn", 5), notif_user(AGENT, "tool_sub", 12),
                task_stop("tool_stop", OTHER, 13), stop_ok("tool_stop", OTHER, 14)]),
        (children[OTHER], [line("user", "work for tool_other", 3.5, agentId=OTHER),
                           send("tool_sub", AGENT, 10), text_resumed_ack("tool_sub", AGENT, 11)]),
        (children[AGENT], [line("user", "work for tool_spawn", 1.5, agentId=AGENT),
                           line("user", "The coordinator sent a message: go on", 11.2,
                                origin={"kind": "coordinator"}),
                           interrupt(11.5)]),
    ]
    for session, entries in (reversed(steps) if reverse else steps):
        live(tree, session, *entries, compute=compute)
    return _all_states(root.id, [AGENT, OTHER])


def _codex_order_run(tmp_path, reverse):
    tmp_path.mkdir()
    fixture = fixture_child_turn_ends()
    replay = LiveReplay(fixture, tmp_path)
    order = [session.session_id for session in fixture.sessions]
    for session_id in (reversed(order) if reverse else order):
        replay.sync(session_id)
    return _all_states(ROOT, [AGENT_A])


def test_initial_sync_order_gives_the_same_states(provider_home, tmp_path, monkeypatch):
    _reset_db_and_caches(provider_home, monkeypatch)
    claude_natural = _claude_order_run(provider_home, reverse=False)
    _reset_db_and_caches(provider_home, monkeypatch)
    claude_reverse = _claude_order_run(provider_home, reverse=True)
    assert claude_natural == claude_reverse
    assert all(state["runs"] for state in claude_natural.values())

    _reset_db_and_caches(provider_home, monkeypatch)
    codex_natural = _codex_order_run(tmp_path / "natural", reverse=False)
    _reset_db_and_caches(provider_home, monkeypatch)
    codex_reverse = _codex_order_run(tmp_path / "reverse", reverse=True)
    assert codex_natural == codex_reverse
    assert codex_natural[AGENT_A]["runs"]
