"""A switch to hybrid CLI mode runs on the session's send lane (composer attachments spec §6.2).

The WS ``set_session_hybrid`` handler kills the live SDK agent, then writes ``Session.hybrid``.
Both steps hold ``send_lane(session_id)``: a send that arrives during the switch (the composer
sends ``set_session_hybrid`` then ``send_message`` back to back, a CLI drop send can arrive at any
time) waits for the flag. Its plan target and the agent the manager builds both read the same
flag, so a plan made for the hybrid CLI is never delivered by an SDK agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc import asgi
from twicc.agent import ephemeral, send_lanes
from twicc.core.services import send_message as send_message_module
from twicc.core.services.attachments import lifecycle, planner
from twicc.core.services.attachments import target as plan_target
from twicc.core.services.attachments.types import (
    AttachmentPlan,
    AttachmentRef,
    PlannedEntry,
    PlanTarget,
    StagedEntry,
)
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager

SESSION_ID = "7a1b2c3d-0000-4000-8000-0000000000aa"
PROJECT_ID = "hybrid-switch-lane-project"
REF = AttachmentRef("draft-bucket", "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01")


@pytest.fixture(autouse=True)
def _clean_state():
    ephemeral.clear()
    send_lanes._reset_for_tests()
    yield
    ephemeral.clear()
    send_lanes._reset_for_tests()
    asgi._DETACHED_TASKS.clear()


@pytest.fixture
def session_row(transactional_db, tmp_path, settings):
    from twicc.core.models import Project, Session

    settings.CLAUDE_HYBRID_ENABLED = True
    project = Project.objects.create(id=PROJECT_ID, directory=str(tmp_path))
    Session.objects.create(
        id=SESSION_ID, project=project, provider="claude_code", file_path=f"{SESSION_ID}.jsonl",
        created_at="2026-10-07T10:00:00Z", user_message_count=1,
    )
    return tmp_path


class SwitchManager:
    """The registry's Claude manager: a gated kill, and the real agent factory on send."""

    def __init__(self) -> None:
        self.real = ClaudeCodeAgentManager()
        self.kill_gate = asyncio.Event()
        self.kill_error: Exception | None = None
        self.killed: list[str] = []
        self.sent = asyncio.Event()
        self.deliveries: list[tuple[type, bool]] = []

    async def kill_agent(self, session_id: str, reason: str = "manual") -> bool:
        # The real kill holds the manager grace window: the gate keeps the switch inside it.
        await self.kill_gate.wait()
        self.killed.append(reason)
        if self.kill_error is not None:
            raise self.kill_error
        return False

    def get_agent_info(self, session_id):
        return None

    def get_live_agent(self, session_id):
        return None

    async def send_to_session(self, session_id, project_id, cwd, text, *, settings, attachment_plan=None, **kwargs):
        # No live agent: the manager builds one through its factory, which reads the database flag.
        agent = await self.real._create_agent(session_id, project_id, cwd, resume=True, settings=settings)
        self.deliveries.append((type(agent), attachment_plan.target.hybrid))
        self.sent.set()
        return True


@pytest.fixture
def switch(monkeypatch, session_row):
    manager = SwitchManager()
    released: list = []

    async def release_refs(refs):
        released.append(tuple(refs))

    async def resolve_plan_target(**kwargs):
        return PlanTarget("claude_code", kwargs["hybrid"], False, "opus", False, "first_party")

    def plan_attachments(refs, target, *, text):
        source = StagedEntry(REF, "notes.md", 5, Path("/staged/notes.md"), None)
        return AttachmentPlan(target, (PlannedEntry(REF, 1, "notes.md", "text", 1, 1, "file", source, None),))

    helpers = SimpleNamespace(
        resolve_agent_settings=lambda s: s,
        enforce_agent_settings_consistency=lambda s: s,
        validate_title=lambda t: SimpleNamespace(error=None, title=t),
    )
    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    monkeypatch.setattr(plan_target, "resolve_plan_target", resolve_plan_target)
    monkeypatch.setattr(planner, "plan_attachments", plan_attachments)
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(get=lambda provider: manager))
    monkeypatch.setattr(asgi, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(asgi, "get_provider_helpers", lambda provider: helpers)

    consumer = asgi.WSConsumer()
    consumer.frames = []

    async def send_json(frame, close=False):
        consumer.frames.append(frame)

    consumer.send_json = send_json
    consumer.channel_layer = SimpleNamespace(group_send=AsyncMock())
    return SimpleNamespace(consumer=consumer, manager=manager, released=released)


async def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


async def _drain() -> None:
    while asgi._DETACHED_TASKS:
        await asyncio.gather(*list(asgi._DETACHED_TASKS), return_exceptions=True)
    pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _run(scenario) -> None:
    """Run *scenario* with the DB writer the switch and the settings write go through."""
    from twicc.providers import db_writer

    async def run() -> None:
        db_writer.start_db_writer()
        try:
            await scenario()
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(run())


def _session_hybrid() -> bool:
    from twicc.core.models import Session

    return Session.objects.get(id=SESSION_ID).hybrid


def test_a_send_right_after_the_switch_is_planned_and_delivered_by_the_hybrid_cli(switch):
    """The composer order: the switch frame, then the send frame, with no live agent."""
    consumer, manager = switch.consumer, switch.manager
    frame = {
        "type": "send_message", "session_id": SESSION_ID, "project_id": PROJECT_ID, "text": "look",
        "request_id": "req-1", "attachments": [REF._asdict()],
    }

    async def scenario() -> None:
        await consumer._handle_set_session_hybrid({"session_id": SESSION_ID})
        await consumer._handle_send_message(frame)
        # The switch holds the lane inside its kill; the send queues behind it. Without the
        # lane, the send would reach the manager here, before the flag is written.
        await _wait_until(lambda: send_lanes._reference_count(SESSION_ID) == 2 or manager.sent.is_set())
        assert not manager.sent.is_set()
        manager.kill_gate.set()
        await _drain()

    _run(scenario)

    assert manager.killed == ["switch-hybrid"]
    assert manager.deliveries == [(HybridClaudeAgent, True)]
    assert _session_hybrid() is True
    assert [f["type"] for f in consumer.frames] == ["send_ack"]
    assert switch.released == [(REF,)]
    assert send_lanes._live_keys() == set()


def test_a_drop_send_during_the_switch_waits_for_the_flag(switch, monkeypatch):
    """A CLI/MCP send arrives while the switch kills the agent: it runs after the flag write."""
    consumer, manager = switch.consumer, switch.manager
    seen: list[bool] = []

    async def send_to_session_from_payload(payload, *, release_refs_on_outcome=False):
        from asgiref.sync import sync_to_async

        seen.append(await sync_to_async(_session_hybrid)())
        return send_message_module.SendMessageResult(True, SESSION_ID, "claude_code", PROJECT_ID, None)

    monkeypatch.setattr(send_message_module, "send_message_to_session_from_payload", send_to_session_from_payload)

    async def scenario() -> None:
        await consumer._handle_set_session_hybrid({"session_id": SESSION_ID})
        await asyncio.sleep(0)  # the switch task takes the lane
        drop = asyncio.create_task(
            send_message_module.send_message_from_drop_payload({"session_id": SESSION_ID, "text": "hi"}),
        )
        await _wait_until(lambda: send_lanes._reference_count(SESSION_ID) == 2 or bool(seen))
        assert seen == []
        manager.kill_gate.set()
        await drop
        await _drain()

    _run(scenario)

    assert seen == [True]
    assert send_lanes._live_keys() == set()


def test_a_failed_switch_releases_the_lane_and_leaves_the_session_sdk(switch):
    consumer, manager = switch.consumer, switch.manager
    manager.kill_error = RuntimeError("kill failed")

    async def scenario() -> None:
        await consumer._handle_set_session_hybrid({"session_id": SESSION_ID})
        manager.kill_gate.set()
        await _drain()

    _run(scenario)

    assert manager.killed == ["switch-hybrid"]
    assert _session_hybrid() is False
    assert send_lanes._live_keys() == set()


def test_a_switch_cancelled_while_it_waits_for_the_lane_leaves_no_lane(switch):
    consumer = switch.consumer

    async def scenario() -> None:
        async with send_lanes.send_lane(SESSION_ID):  # a send ahead of the switch
            await consumer._handle_set_session_hybrid({"session_id": SESSION_ID})
            await asyncio.sleep(0)
            assert send_lanes._reference_count(SESSION_ID) == 2
            [task] = [t for t in asgi._DETACHED_TASKS if not t.done()]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _drain()

    _run(scenario)

    assert switch.manager.killed == []
    assert _session_hybrid() is False
    assert send_lanes._live_keys() == set()
