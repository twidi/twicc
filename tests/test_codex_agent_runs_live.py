"""Codex agent-run signals in the live sync, and their parity with the batch (design §5.6, §6.2, §9).

The live hook ``apply_agent_run_signals`` and the live ``FINAL_ANSWER``
rebind read the DB rows below their line; the batch reads the loop's dicts.
The parity test replays every rollout of
:data:`tests.codex_agent_run_fixtures.ALL_FIXTURES` live (one line per sync,
then in one chunk) and through the batch recompute in a fresh DB, and
compares the stored rows. The other tests pin the §9 live cases.
"""

from __future__ import annotations

from pathlib import Path
from queue import Queue

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.agent_runs import agent_run_states
from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentRunEnd,
    AgentRunEndSource,
    Project,
    Session,
    SessionItem,
    SessionHistoryFact,
    SessionType,
    ToolResultLink,
)
from twicc.providers.codex.agent_runs import FileEvidence, FileRun
from twicc.providers.codex.compute import CodexSessionCompute, evidence_from_db
from twicc.providers.compute_base import LiveAgentSignals

from tests.codex_agent_run_fixtures import (
    AGENT_A,
    ALL_FIXTURES,
    PATH_A,
    PROJECT_ID,
    ROOT,
    CodexAgentRunFixture,
    Rollout,
    _fixture,
    at,
    fixture_completed_between_call_and_interacted,
    fixture_idle_followup_opens_run,
    fixture_interrupt_race,
    fixture_merged_followup,
    fixture_merged_then_real_followups,
    fixture_stop_result_before_event,
)
from tests.test_codex_agent_runs_batch import play as play_batch


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class RecordingCompute(CodexSessionCompute):
    """Records what each live hook call returned, per ``(session_id, line_num)``."""

    def __init__(self) -> None:
        super().__init__()
        self.live: dict[tuple[str, int], LiveAgentSignals] = {}

    def apply_agent_run_signals(self, session_id, item, parsed, **kwargs):
        signals = super().apply_agent_run_signals(session_id, item, parsed, **kwargs)
        self.live[(session_id, item.line_num)] = signals
        return signals


class LiveReplay:
    """Creates the sessions of a fixture and appends / syncs their rollout lines live."""

    def __init__(self, fixture: CodexAgentRunFixture, tmp_path: Path, compute: CodexSessionCompute | None = None):
        self.fixture = fixture
        self.compute = compute or RecordingCompute()
        project, _ = Project.objects.get_or_create(id=PROJECT_ID)
        self.sessions: dict[str, Session] = {}
        self.paths: dict[str, Path] = {}
        self.synced: dict[str, int] = {}
        root = None
        for fixture_session in fixture.sessions:
            path = tmp_path / f"{fixture_session.session_id}.jsonl"
            path.touch()
            session = Session.objects.create(
                id=fixture_session.session_id,
                project=project,
                provider=Provider.CODEX,
                file_path=str(path),
                type=SessionType.SUBAGENT if fixture_session.is_subagent else SessionType.SESSION,
                parent_session=root if fixture_session.is_subagent else None,
            )
            if root is None:
                root = session
            self.sessions[session.id] = session
            self.paths[session.id] = path
            self.synced[session.id] = 0

    def sync(self, session_id: str, upto: int | None = None) -> tuple:
        """Append the lines of ``session_id`` not synced yet (up to line ``upto``) and sync them in one call."""
        lines = self.fixture.session(session_id).lines
        end = len(lines) if upto is None else upto
        with self.paths[session_id].open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines[self.synced[session_id]:end])
        self.synced[session_id] = end
        return self.compute.sync_session_items_from_file(self.sessions[session_id], self.paths[session_id])

    def sync_to_mark(self, session_id: str, mark: str) -> tuple:
        return self.sync(session_id, self.fixture.line(session_id, mark))

    def run(self, *, per_line: bool) -> None:
        """Every session in fixture order (the root first), line by line or in one chunk."""
        for fixture_session in self.fixture.sessions:
            session_id = fixture_session.session_id
            if per_line:
                for line_num in range(1, len(fixture_session.lines) + 1):
                    self.sync(session_id, line_num)
            else:
                self.sync(session_id)


def rows(fixture: CodexAgentRunFixture) -> dict[str, list]:
    """The rows the live and batch paths must agree on, for every session of ``fixture``."""
    ids = [fixture_session.session_id for fixture_session in fixture.sessions]
    return {
        "interactions": sorted(
            AgentInteraction.objects.filter(session_id__in=ids).values_list(
                "session_id", "tool_use_id", "tool_use_line_num", "event_line_num", "agent_id", "kind",
                "opens_run", "started_at",
            ),
            key=repr,
        ),
        "run_ends": sorted(
            AgentRunEnd.objects.filter(session_id__in=ids, source=AgentRunEndSource.TRANSCRIPT).values_list(
                "session_id", "line_num", "tool_use_id", "agent_id", "status", "ended_at",
            ),
            key=repr,
        ),
        "results": sorted(
            ToolResultLink.objects.filter(session_id__in=ids).values_list(
                "session_id", "tool_use_id", "tool_result_line_num",
            ),
            key=repr,
        ),
    }


def interaction(tool_use_id: str, session_id: str = ROOT) -> AgentInteraction:
    return AgentInteraction.objects.get(session_id=session_id, tool_use_id=tool_use_id)


def result_lines(tool_use_id: str, session_id: str = ROOT) -> list[int]:
    return sorted(ToolResultLink.objects.filter(session_id=session_id, tool_use_id=tool_use_id).values_list(
        "tool_result_line_num", flat=True,
    ))


def run_ends(session_id: str = ROOT) -> list[tuple]:
    return list(
        AgentRunEnd.objects.filter(session_id=session_id).order_by("line_num", "tool_use_id").values_list(
            "line_num", "tool_use_id", "agent_id", "status", "ended_at",
        )
    )


def state(agent_id: str = AGENT_A):
    return agent_run_states(Session.objects.get(id=ROOT), [agent_id])[agent_id]


def ack_line(fixture: CodexAgentRunFixture, spawn_mark: str = "spawn") -> int:
    """A v2 spawn is three lines: call, ``started``, ack."""
    return fixture.line(ROOT, spawn_mark) + 2


# ---------------------------------------------------------------------------
# Batch / live parity (§9)
# ---------------------------------------------------------------------------


# (interactions, transcript run ends) each fixture writes, so no fixture
# passes the parity check with empty lists. ``v1_notification`` is the only
# one with none: multi-agent v1 has no agent-run signal (a rebind only).
EXPECTED_ROW_COUNTS: dict[str, tuple[int, int]] = {
    "child_turn_ends": (1, 3),
    "completed_between_call_and_interacted": (1, 4),
    "control_calls": (2, 0),
    "duplicate_interacted": (1, 1),
    "flat_timestamp_child": (0, 1),
    "followup_after_interrupt": (2, 1),
    "forked_child": (0, 1),
    "idle_followup_opens_run": (1, 2),
    "interrupt_race": (1, 1),
    "long_gap_with_followup": (1, 2),
    "merged_followup": (1, 1),
    "merged_then_real_followups": (3, 3),
    "non_fork_child_with_history_field": (0, 1),
    "owner_abort_after_signal": (2, 2),
    "owner_abort_other_error": (2, 2),
    "owner_abort_other_reason": (2, 2),
    "owner_abort_repeated_turn_id": (0, 1),
    "owner_abort_turn_aborted": (2, 4),
    "owner_abort_unknown_turn": (2, 2),
    "owner_abort_usage_limit": (2, 4),
    "pre_completed_rollout": (1, 0),
    "reaudit_limitation": (1, 2),
    "same_time_ack_and_final_answer": (1, 0),
    "stop_in_completed_final_gap": (2, 1),
    "started_without_spawn_call": (1, 0),
    "stop_result_before_event": (1, 1),
    "subagent_owner_usage_limit": (0, 2),
    "t20_sequence": (2, 3),
    "v1_notification": (0, 0),
}


def test_expected_row_counts_cover_every_fixture():
    assert set(EXPECTED_ROW_COUNTS) == set(ALL_FIXTURES)


@pytest.mark.parametrize("per_line", [True, False], ids=["per_line", "one_chunk"])
@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_batch_live_parity(db, tmp_path, name, per_line):
    fixture = ALL_FIXTURES[name]()
    LiveReplay(fixture, tmp_path).run(per_line=per_line)
    live = rows(fixture)
    # Batch extraction must match both live chunking modes, including
    # normalized private-source records already written by live transforms.
    for session in Session.objects.filter(project_id=PROJECT_ID):
        queue = Queue()
        CodexSessionCompute().compute_session_metadata(session.id, queue, 1)
        messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
        batch_facts = next(msg for msg in messages if msg['type'] == 'session_complete')['history_facts']
        live_facts = list(SessionHistoryFact.objects.filter(session=session)
                          .order_by('line_num', 'kind', 'key').values('line_num', 'kind', 'key', 'data'))
        assert sorted(batch_facts, key=lambda fact: (fact['line_num'], fact['kind'], fact['key'])) == live_facts
    # The replay really synced: every fixture pairs at least one spawn ack,
    # and writes its own number of agent-run rows.
    assert live["results"]
    assert (len(live["interactions"]), len(live["run_ends"])) == EXPECTED_ROW_COUNTS[name]

    # A fresh DB for the batch: drop the live sessions and every row they own.
    Session.objects.filter(project_id=PROJECT_ID).delete()
    assert rows(fixture) == {"interactions": [], "run_ends": [], "results": []}
    play_batch(fixture)
    batch = rows(fixture)

    assert live == batch


# ---------------------------------------------------------------------------
# §9 live cases
# ---------------------------------------------------------------------------


def test_completed_between_call_and_interacted_live(db, tmp_path):
    """``01a0796d-72bd…`` line 210: the ``completed`` goes to the previous run; the follow-up stays open."""
    fx = fixture_completed_between_call_and_interacted()
    replay = LiveReplay(fx, tmp_path)
    replay.sync_to_mark(ROOT, "final_0")
    replay.sync_to_mark(AGENT_A, "end_1")

    row = interaction("c_fu")
    assert row.opens_run is True and row.started_at == at(12)
    assert (row.tool_use_line_num, row.event_line_num) == (fx.line(ROOT, "fu"), fx.line(ROOT, "fu_event"))
    assert run_ends() == [(fx.line(ROOT, "completed_0"), "c_spawn", AGENT_A, "completed", at(11))]
    assert result_lines("c_spawn") == [ack_line(fx), fx.line(ROOT, "final_0")]
    current = state()
    assert current.running and current.run_started_at == at(12)

    replay.sync(ROOT)
    replay.sync(AGENT_A)
    assert run_ends()[-1] == (fx.line(ROOT, "completed_1"), "c_fu", AGENT_A, "completed", at(20))
    assert result_lines("c_fu") == [fx.line(ROOT, "fu_output"), fx.line(ROOT, "final_1")]
    assert not state().running


def test_interrupt_race_live(db, tmp_path):
    """Interrupt call → ``completed`` → ``interrupted`` → result → ``FINAL_ANSWER``: both on the spawn."""
    fx = fixture_interrupt_race()
    LiveReplay(fx, tmp_path).run(per_line=True)
    live = rows(fx)
    assert run_ends() == [(fx.line(ROOT, "completed"), "c_spawn", AGENT_A, "completed", at(11))]
    assert result_lines("c_spawn") == [ack_line(fx), fx.line(ROOT, "final")]
    assert result_lines("c_stop") == [fx.line(ROOT, "stop_output")]
    assert interaction("c_stop").kind == "stop"
    assert not state().running

    play_batch(fx)
    assert rows(fx) == live


def test_opens_run_live_ignores_a_root_restart(db, tmp_path):
    """A root restart between the spawn and a follow-up does not change ``opens_run`` (no cutoff input)."""
    fx = fixture_merged_followup()
    replay = LiveReplay(fx, tmp_path)
    replay.sync(ROOT, fx.line(ROOT, "fu") - 1)
    # The root restarts after the spawn: the running state now cuts the spawn run.
    root = replay.sessions[ROOT]
    root.last_started_at = at(4)
    root.save(update_fields=["last_started_at"])
    assert not state().running
    replay.sync(ROOT)
    live_opens_run = interaction("c_fu").opens_run

    play_batch(fx)
    assert live_opens_run is False
    assert interaction("c_fu").opens_run is live_opens_run


def test_agents_resumed_only_for_run_opening_resumes(db, tmp_path):
    """``agents_resumed`` names the resumed agent for a run-opening follow-up only."""
    fx = fixture_merged_then_real_followups()
    replay = LiveReplay(fx, tmp_path)
    returned = replay.sync(ROOT)
    compute = replay.compute
    signals = {line: compute.live[(ROOT, line)] for line in range(1, len(fx.session(ROOT).lines) + 1)}

    merged = signals[fx.line(ROOT, "fu0_event")]
    assert merged.changed_interactions == ((ROOT, "c_fu0"),)
    assert merged.run_interactions == () and merged.agents_resumed == ()
    for mark_name, call_id in (("fu1_event", "c_fu1"), ("fu2_event", "c_fu2")):
        opened = signals[fx.line(ROOT, mark_name)]
        assert opened.changed_interactions == ((ROOT, call_id),)
        assert opened.run_interactions == ((ROOT, call_id),)
        assert opened.agents_resumed == ((AGENT_A, PATH_A),)
        assert opened.affected_agent_ids == (AGENT_A,)
    resumed_lines = [line for line, s in signals.items() if s.agents_resumed]
    assert resumed_lines == [fx.line(ROOT, "fu1_event"), fx.line(ROOT, "fu2_event")]
    # The sync returns them as its last element, for ``_after_agents_resumed``.
    assert returned[-1] == [(AGENT_A, PATH_A), (AGENT_A, PATH_A)]


def test_send_message_resumes_nothing(db, tmp_path):
    fx = ALL_FIXTURES["control_calls"]()
    replay = LiveReplay(fx, tmp_path)
    replay.sync(ROOT)
    message = replay.compute.live[(ROOT, fx.line(ROOT, "msg_event"))]
    assert message.changed_interactions == ((ROOT, "c_msg"),)
    assert message.agents_resumed == () and message.run_interactions == ()
    assert all(not s.agents_resumed for s in replay.compute.live.values())


def test_stop_row_with_an_earlier_result_is_a_stop_record(db, tmp_path):
    """A Codex stop's result, synced in an earlier batch than its ``interrupted`` line → a stop record."""
    fx = fixture_stop_result_before_event()
    replay = LiveReplay(fx, tmp_path)
    replay.sync_to_mark(ROOT, "stop_output")
    assert not AgentInteraction.objects.filter(tool_use_id="c_stop").exists()
    replay.sync(ROOT)

    at_event = replay.compute.live[(ROOT, fx.line(ROOT, "stop_event"))]
    assert at_event.changed_interactions == ((ROOT, "c_stop"),)
    assert at_event.stop_records == ((ROOT, "c_stop"),)
    assert at_event.affected_agent_ids == (AGENT_A,)
    assert all(s.stop_records == () for key, s in replay.compute.live.items()
               if key != (ROOT, fx.line(ROOT, "stop_event")))


def test_stop_row_before_its_result_is_no_stop_record(db, tmp_path):
    fx = fixture_interrupt_race()
    replay = LiveReplay(fx, tmp_path)
    replay.sync(ROOT)
    at_event = replay.compute.live[(ROOT, fx.line(ROOT, "stop_event"))]
    assert at_event.changed_interactions == ((ROOT, "c_stop"),)
    assert at_event.stop_records == ()


def test_live_run_ends_are_described(db, tmp_path):
    fx = fixture_idle_followup_opens_run()
    replay = LiveReplay(fx, tmp_path)
    replay.sync(ROOT)
    completed = replay.compute.live[(ROOT, fx.line(ROOT, "completed_1"))]
    end = AgentRunEnd.objects.get(session_id=ROOT, line_num=fx.line(ROOT, "completed_1"))
    assert completed.run_end_ids == (end.id,)
    assert completed.affected_agent_ids == (AGENT_A,)
    assert (end.tool_use_id, end.status) == ("c_spawn", "completed")


def test_live_duplicate_event_line_writes_nothing_new(db, tmp_path):
    fx = ALL_FIXTURES["duplicate_interacted"]()
    replay = LiveReplay(fx, tmp_path)
    replay.sync(ROOT)
    assert replay.compute.live[(ROOT, fx.line(ROOT, "fu_duplicate"))] == LiveAgentSignals()
    assert interaction("c_fu").event_line_num == fx.line(ROOT, "fu_event")


# ---------------------------------------------------------------------------
# DB evidence (the live twin of ``evidence_from_batch_state``)
# ---------------------------------------------------------------------------


def test_evidence_from_db_reads_rows_below_the_line(db, tmp_path):
    fx = fixture_stop_result_before_event()
    LiveReplay(fx, tmp_path).run(per_line=False)
    spawn_line = fx.line(ROOT, "spawn")
    ack = ack_line(fx)
    final = fx.line(ROOT, "final")

    full = evidence_from_db(ROOT, AGENT_A, final + 1)
    assert full == FileEvidence(
        runs=(FileRun("c_spawn", spawn_line, None, "spawn"),),
        stops=((fx.line(ROOT, "stop_event"), fx.line(ROOT, "stop_output")),),
        completed_lines={"c_spawn": (fx.line(ROOT, "completed"),)},
        aborted_lines={},
        results={"c_spawn": ((ack, at(2.2)), (final, at(14)))},
    )
    # Below the stop's event line: no stop, no completed; the FINAL_ANSWER is not read yet.
    early = evidence_from_db(ROOT, AGENT_A, fx.line(ROOT, "stop_event"))
    assert early.stops == ()
    assert early.completed_lines == {"c_spawn": (fx.line(ROOT, "completed"),)}
    assert early.results == {"c_spawn": ((ack, at(2.2)),)}
    # Another agent's rows are never read.
    assert evidence_from_db(ROOT, "someone-else", final + 1) == FileEvidence((), (), {}, {}, {})


def test_evidence_from_db_stop_uses_the_first_non_error_result(db):
    project = Project.objects.create(id=PROJECT_ID)
    root = Session.objects.create(id=ROOT, project=project, provider=Provider.CODEX)
    AgentInteraction.objects.create(session=root, tool_use_id="c_stop", tool_use_line_num=3, event_line_num=5,
                                    agent_id=AGENT_A, kind="stop", started_at=at(5))
    for line, error in ((4, "boom"), (6, None), (7, None)):
        ToolResultLink.objects.create(session=root, tool_use_id="c_stop", tool_use_line_num=3,
                                      tool_result_line_num=line, tool_result_at=at(line), error=error)
    assert evidence_from_db(ROOT, AGENT_A, 100).stops == ((5, 6),)
    # A result line at or after ``before_line`` is not read.
    assert evidence_from_db(ROOT, AGENT_A, 6).stops == ((5, None),)


# ---------------------------------------------------------------------------
# Query budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("owner_is_subagent", [False, True], ids=["root_owner", "subagent_owner"])
def test_control_event_on_the_owner_or_root_runs_no_query(db, django_assert_num_queries, owner_is_subagent):
    """The free owner/root target check comes before any lookup (as batch)."""
    project = Project.objects.create(id=PROJECT_ID)
    root = Session.objects.create(id=ROOT, project=project, provider=Provider.CODEX, file_path="root.jsonl")
    owner = root
    if owner_is_subagent:
        owner = Session.objects.create(id=AGENT_A, project=project, provider=Provider.CODEX, file_path="a.jsonl",
                                       type=SessionType.SUBAGENT, parent_session=root)
    rollout = Rollout(owner.id)
    rollout.activity(5, "interacted", "c_self", ROOT, "/root")
    parsed = orjson.loads(rollout.lines[0])
    item = SessionItem(session=owner, line_num=7, content=rollout.lines[0], timestamp=at(5))
    compute = CodexSessionCompute()
    with django_assert_num_queries(0):
        assert compute.apply_agent_run_signals(owner.id, item, parsed) == LiveAgentSignals()


class QueryCountingCompute(CodexSessionCompute):
    """Counts the queries of the live hook on one line."""

    def __init__(self, line_num: int) -> None:
        super().__init__()
        self.line_num = line_num
        self.count: int | None = None

    def apply_agent_run_signals(self, session_id, item, parsed, **kwargs):
        if item.line_num != self.line_num:
            return super().apply_agent_run_signals(session_id, item, parsed, **kwargs)
        with CaptureQueriesContext(connection) as ctx:
            signals = super().apply_agent_run_signals(session_id, item, parsed, **kwargs)
        self.count = len(ctx.captured_queries)
        return signals


def _abort_with_idle_agents(agent_count: int) -> CodexAgentRunFixture:
    """``agent_count`` agents spawned (and followed up) in turn t1, one spawn in t2, then t2 aborts."""
    root = Rollout(ROOT)
    root.meta(0)
    root.task_started(1, "t1")
    for index in range(agent_count):
        agent_id, path = f"019f0000-0000-7000-8000-{index:012d}", f"/root/task_{index}"
        root.spawn(2 + index, f"c_spawn_{index}", agent_id, path)
        root.followup(2.5 + index, f"c_fu_{index}", agent_id, path)
    root.task_complete(100, "t1")
    root.task_started(200, "t2")
    root.spawn(201, "c_b", AGENT_A, PATH_A)
    root.turn_aborted(300, "t2", mark="abort")
    return _fixture(f"abort_with_{agent_count}_idle_agents", root)


def test_owner_abort_query_budget_does_not_grow_with_the_agents(db, tmp_path):
    counts = {}
    for agent_count in (5, 40):
        fx = _abort_with_idle_agents(agent_count)
        compute = QueryCountingCompute(fx.line(ROOT, "abort"))
        (tmp_path / str(agent_count)).mkdir()
        LiveReplay(fx, tmp_path / str(agent_count), compute).run(per_line=False)
        assert run_ends() == [(fx.line(ROOT, "abort"), "c_b", AGENT_A, "owner_turn_aborted", at(300))]
        counts[agent_count] = compute.count
        Session.objects.filter(project_id=PROJECT_ID).delete()
    assert counts[5] is not None
    assert counts[5] == counts[40]
