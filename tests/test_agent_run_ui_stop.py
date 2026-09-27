"""The Stop button (Claude): a successful stop is run evidence on the root, then a stop step.

``WSConsumer._handle_stop_subagent`` reads ``ended_at`` just before it sends
the stop request, and only when ``manager.stop_subagent`` succeeds, under the
DB write lock: ``record_ui_stop`` writes a ``ui`` ``AgentRunEnd`` on the root
(``status = "ui_stopped"``) and runs the stop step, then the handler sends the
outcome through ``broadcast_agent_run_outcome``, still under the lock, with
no ``_after_agents_stopped`` hook. Design:
``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`` §5.2,
§5.4 (rule 2), §5.5, §6.3 and §9.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from asgiref.sync import sync_to_async
from watchfiles import Change

from twicc import asgi
from twicc.core import agent_runs
from twicc.asgi import WSConsumer
from twicc.core.agent_runs import agent_run_states, serialize_run_state
from twicc.core.enums import Provider
from twicc.core.models import AgentRunEnd, AgentRunEndSource, Project, Session, SessionType
from twicc.providers import db_writer, sessions_watcher
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile

from tests.test_claude_agent_runs import (
    AGENT,
    ack,
    assert_batch_keeps_live_rows,
    at,
    line,
    notif_queue,
    notif_user,
    resumed_ack,
    send,
    spawn,
)

PROJECT = "ui-stop-project"


@pytest.fixture(autouse=True)
def db_write_lock(monkeypatch):
    """A running DB writer's lock state: a real lock, a stop event never set."""
    monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())


@pytest.fixture
def tree(transactional_db, provider_home):
    project = Project.objects.create(id=PROJECT)
    root = Session.objects.create(id="ui-stop-root", project=project, provider=Provider.CLAUDE_CODE,
                                  file_path=f"{PROJECT}/ui-stop-root.jsonl")
    Session.objects.create(id=AGENT, project=project, provider=Provider.CLAUDE_CODE, type=SessionType.SUBAGENT,
                           parent_session=root, file_path=f"{PROJECT}/ui-stop-root/subagents/agent-{AGENT}.jsonl")
    return root, provider_home.claude / "projects"


def append(tree, entries):
    root, home = tree
    path = home / root.file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for parsed in entries:
            handle.write(orjson.dumps(parsed) + b"\n")
    return path


def live(tree, *entries):
    """Append ``entries`` to the root file and run one live sync of it."""
    root, _ = tree
    path = append(tree, entries)
    ClaudeCodeSessionCompute().sync_session_items_from_file(Session.objects.get(id=root.id), path)


def running_spawn(tree):
    """Root lines: prompt, background spawn of AGENT, its ack — AGENT runs from 2."""
    live(tree, line("user", "go", 0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2))


def state(root):
    return agent_run_states(Session.objects.get(id=root.id), [AGENT])[AGENT]


def ui_rows():
    return list(AgentRunEnd.objects.filter(source=AgentRunEndSource.UI).values(
        "session_id", "line_num", "agent_id", "tool_use_id", "ended_at", "status"))


class Clock:
    """A fake ``timezone.now`` whose reads are counted."""

    def __init__(self, now):
        self.now_value = now
        self.reads = 0

    def now(self):
        self.reads += 1
        return self.now_value


class Handler:
    """``WSConsumer._handle_stop_subagent`` with a fake manager, clock and recorded broadcasts."""

    def __init__(self, monkeypatch, clock, stop=None, stopped=True):
        self.messages = AsyncMock()
        monkeypatch.setattr(sessions_watcher, "broadcast_message", self.messages)
        self.stopped_hook = AsyncMock()
        monkeypatch.setattr(BaseSessionsWatcher, "_after_agents_stopped", self.stopped_hook)
        monkeypatch.setattr(asgi, "timezone", SimpleNamespace(now=clock.now))
        monkeypatch.setattr(asgi, "ensure_provider_running", lambda provider: None)
        self.manager = SimpleNamespace(stop_subagent=AsyncMock(side_effect=stop, return_value=stopped))
        registry = SimpleNamespace(find_manager_for_session=lambda session_id: self.manager)
        monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: registry)
        self.consumer = WSConsumer()
        self.consumer.send_json = AsyncMock()
        self.consumer.channel_layer = None  # set by Channels on connect; broadcasts are recorded

    def stop(self, root, agent_id=AGENT):
        asyncio.run(self.call(root, agent_id))

    async def call(self, root, agent_id=AGENT):
        await self.consumer._handle_stop_subagent(
            {"type": "stop_subagent", "session_id": root.id, "subagent_id": agent_id})

    def sent(self, kind=None):
        return [call.args[1] for call in self.messages.call_args_list
                if kind is None or call.args[1]["type"] == kind]


def expected_run_state(root):
    return {**serialize_run_state(root.id, AGENT, state(root)), "type": "agent_run_state",
            "project_id": root.project_id}


# ---------------------------------------------------------------------------
# A successful stop is run evidence; a failed one writes nothing
# ---------------------------------------------------------------------------


def test_successful_stop_writes_a_ui_row_and_closes_the_runs_before_it(tree, monkeypatch):
    """No ``killed`` notification ever reaches a transcript: the ui row alone closes the run."""
    root, _ = tree
    running_spawn(tree)
    assert state(root).running
    handler = Handler(monkeypatch, Clock(at(10)))
    handler.stop(root)
    handler.manager.stop_subagent.assert_awaited_once_with(root.id, AGENT)
    assert ui_rows() == [{"session_id": root.id, "line_num": None, "agent_id": AGENT, "tool_use_id": "",
                          "ended_at": at(10), "status": "ui_stopped"}]
    current = state(root)
    assert not current.running and current.stopped_at == at(10)
    assert Session.objects.get(id=AGENT).last_stopped_at == at(10)
    assert handler.sent("agent_run_state") == [expected_run_state(root)]
    assert [message["session"]["id"] for message in handler.sent("session_updated")] == [AGENT]
    assert handler.sent("agent_stopped") == [{"type": "agent_stopped", "agent_session_id": AGENT,
                                              "stopped_at": at(10).isoformat(), "root_session_id": root.id}]
    handler.stopped_hook.assert_not_awaited()


def test_failed_stop_writes_nothing_and_broadcasts_nothing(tree, monkeypatch):
    root, _ = tree
    running_spawn(tree)
    handler = Handler(monkeypatch, Clock(at(10)), stopped=False)
    handler.stop(root)
    handler.manager.stop_subagent.assert_awaited_once_with(root.id, AGENT)
    assert not AgentRunEnd.objects.exists()
    assert handler.sent() == []
    assert state(root).running
    assert Session.objects.get(id=AGENT).last_stopped_at is None


def test_failed_recording_is_logged_and_rolled_back(tree, monkeypatch):
    """The stop step fails after the row is created: the row rolls back, the handler logs and returns."""
    root, _ = tree
    running_spawn(tree)

    def failing_step(*args, **kwargs):
        assert AgentRunEnd.objects.filter(source=AgentRunEndSource.UI).count() == 1  # the row was written
        raise RuntimeError("stop step failed")

    monkeypatch.setattr(agent_runs, "run_stop_step", failing_step)
    logger = MagicMock()
    monkeypatch.setattr(asgi, "logger", logger)
    handler = Handler(monkeypatch, Clock(at(10)))
    handler.stop(root)  # does not raise
    logger.exception.assert_called_once()
    assert not AgentRunEnd.objects.exists()
    assert handler.sent() == []
    assert state(root).running


# ---------------------------------------------------------------------------
# ended_at is read before the stop request
# ---------------------------------------------------------------------------


def test_ended_at_is_read_before_the_stop_request(tree, monkeypatch):
    """A resume whose ack lands during the stop round trip starts after ``ended_at``: it stays open.

    Its later ``killed`` notification (the kill stopped it) closes it (rule 4).
    """
    root, _ = tree
    live(tree, line("user", "go", 0), spawn("tool_spawn", 1), ack("tool_spawn", AGENT, 2),
         notif_user(AGENT, "tool_spawn", 3))
    assert not state(root).running
    clock = Clock(at(10))

    async def stop_round_trip(session_id, agent_id):
        assert clock.reads == 1  # ended_at already read
        # The resume's call precedes ended_at, its ack lands during the round trip.
        await sync_to_async(live)(tree, send("tool_send", AGENT, 9), resumed_ack("tool_send", AGENT, 11))
        clock.now_value = at(12)
        return True

    handler = Handler(monkeypatch, clock, stop=stop_round_trip)
    handler.stop(root)
    assert [row["ended_at"] for row in ui_rows()] == [at(10)]
    current = state(root)
    assert current.running and current.run_started_at == at(11)
    assert handler.sent("agent_run_state") == [expected_run_state(root)]
    assert handler.sent("agent_run_state")[0]["running"] is True
    assert handler.sent("agent_stopped") == []

    live(tree, notif_queue(AGENT, "tool_send", 13, status="killed"))
    current = state(root)
    assert not current.running and current.stopped_at == at(13)


def test_ui_stop_at_the_run_start_closes_it(tree, monkeypatch):
    """A ui row has no line: an ``ended_at`` equal to the run's start closes it (rule 2, no tie-break)."""
    root, _ = tree
    running_spawn(tree)
    started_at = state(root).run_started_at
    Handler(monkeypatch, Clock(started_at)).stop(root)
    assert not state(root).running


# ---------------------------------------------------------------------------
# Broadcast order: the handler sends under the lock
# ---------------------------------------------------------------------------


def test_watcher_batch_started_during_the_handler_broadcasts_after_it(tree, monkeypatch):
    """A resume batch that starts while the handler holds the lock sends its state after the handler's."""
    root, _ = tree
    running_spawn(tree)
    handler = Handler(monkeypatch, Clock(at(10)))
    path = append(tree, [send("tool_send", AGENT, 11), resumed_ack("tool_send", AGENT, 12)])
    watcher = ClaudeCodeSessionsWatcher()
    session = Session.objects.get(id=root.id)
    parsed = ParsedSessionFile(session.project_id, session.id, session.type, file_path=session.file_path,
                               parent_session_id=None)

    queued = asyncio.Event()
    watcher_tasks = []

    async def watcher_batch():
        # The watcher's own entry (``_process_parsed_session_change``): the
        # whole sync and its broadcasts under the DB write lock.
        queued.set()  # the next await queues on the lock the handler holds
        await run_under_db_write_lock(lambda: watcher.sync_and_broadcast(path, parsed, Change.modified, None))

    order = []

    async def record(channel_layer, message):
        order.append((message["type"], message.get("running")))
        if not watcher_tasks:
            watcher_tasks.append(asyncio.create_task(watcher_batch()))
            await queued.wait()
            # Give the batch time to run: it must stay queued behind the handler.
            await asyncio.wait(watcher_tasks, timeout=0.1)

    handler.messages.side_effect = record

    async def scenario():
        await handler.call(root)
        await watcher_tasks[0]

    asyncio.run(scenario())
    run_states = [entry for entry in order if entry[0] == "agent_run_state"]
    assert run_states == [("agent_run_state", False), ("agent_run_state", True)]
    handler_stop = order.index(("agent_stopped", None))
    assert handler_stop < order.index(("agent_run_state", True))
    assert state(root).running
    handler.stopped_hook.assert_not_awaited()


# ---------------------------------------------------------------------------
# A root recompute keeps the ui row
# ---------------------------------------------------------------------------


def test_root_recompute_keeps_the_ui_row(tree, monkeypatch):
    root, _ = tree
    running_spawn(tree)
    Handler(monkeypatch, Clock(at(10))).stop(root)
    rows = ui_rows()
    assert len(rows) == 1
    assert_batch_keeps_live_rows(Session.objects.get(id=root.id))
    assert ui_rows() == rows
    assert not state(root).running
