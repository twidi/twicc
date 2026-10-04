"""Codex delivery of composer attachment content (spec §7.1, §7.5).

Existing sessions commit the plan at entry of ``send_to_session``: before the rollout-migration
gate, the manager ``_lock`` and the hardcoded-command dispatch. A new session prepares before any
lock and finishes inside ``_create_agent``, right after ``thread_start`` and the work-dir creation,
for the canonical id Codex minted, and before ``session_bound``. The turn input carries the
ordered data-URL ``ImageInput`` items, then the manifest ``TextInput``, then the folded user text.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai_codex import ImageInput, TextInput

from twicc.agent import AgentState, SendDeliveryError, send_lanes
from twicc.core.services.attachments import committer
from twicc.core.services.attachments.manifest import build_manifest
from twicc.core.services.attachments.types import (
    AttachmentContent,
    AttachmentManifest,
    AttachmentPlan,
    AttachmentRef,
    ManifestEntry,
    NativePart,
    PlannedEntry,
    PlanTarget,
    PreparedAttachments,
    StagedEntry,
)
from twicc.providers.codex.agent import agent as agent_module
from twicc.providers.codex.agent import manager as manager_module
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.helpers import AgentSettings

from tests.test_composer_attachment_commit import (  # noqa: F401 - fixture
    attachments_dir,
    precopies,
    promoted_marker,
    root,
    stage,
)

PNG = b"\x89PNG\r\n\x1a\nfirst"
JPEG = b"\xff\xd8\xff\xe0second"
TARGET = PlanTarget("codex", False, False, "gpt-terra", False, "first_party")


def _content(user_text: str = "hello") -> AttachmentContent:
    """image, video file, image: two native parts and one file entry."""
    entries = (
        ManifestEntry(1, "login.png", "image", 1, 2, "inline", None),
        ManifestEntry(2, "capture.mp4", "video", 1, 1, "file", "capture.mp4"),
        ManifestEntry(3, "after.jpg", "image", 2, 2, "inline", None),
    )
    manifest = AttachmentManifest("thread-id", Path("/data/artifacts/thread-id/attachments"), entries)
    parts = (NativePart("image", "image/png", PNG), NativePart("image", "image/jpeg", JPEG))
    return AttachmentContent(parts, manifest, user_text)


def _file_only_content(user_text: str = "") -> AttachmentContent:
    entries = (ManifestEntry(1, "spec.pdf", "PDF", 1, 1, "file", "spec.pdf"),)
    manifest = AttachmentManifest("thread-id", Path("/data/artifacts/thread-id/attachments"), entries)
    return AttachmentContent((), manifest, user_text)


# ----------------------------------------------------------------------
# Turn input
# ----------------------------------------------------------------------


def _make_agent(monkeypatch: pytest.MonkeyPatch, events: list | None = None, *, pending: str = "") -> CodexAgent:
    """A real agent whose fold records its calls; *pending* prefixes the folded text."""
    events = events if events is not None else []
    monkeypatch.setattr(
        agent_module, "get_provider_helpers",
        lambda provider: SimpleNamespace(resolve_sdk_model=lambda selected: "gpt-terra"),
    )
    agent = CodexAgent(
        "thread-id", "project-id", "/tmp",
        AgentSettings(selected_model="gpt-terra", effort="high", permission_mode="auto"),
        MagicMock(), AsyncMock(), work_dirs=[],
    )

    async def reconcile() -> None:
        events.append(("reconcile",))

    def apply_pending(session_id: str, text: str) -> str:
        events.append(("pending", text))
        return pending + text

    def no_goal(*args, **kwargs):
        raise AssertionError("Codex has no goal instruction")

    monkeypatch.setattr(agent, "_reconcile_context", reconcile)
    monkeypatch.setattr(agent_module, "apply_pending_context", apply_pending)
    monkeypatch.setattr("twicc.context_injection.apply_goal_instruction", no_goal)
    return agent


def _items(agent: CodexAgent, text: str, content: AttachmentContent | None, images=None) -> list:
    return asyncio.run(agent._build_turn_input(text, images, content=content))


def test_turn_input_orders_images_then_manifest_then_user_text(monkeypatch):
    events: list = []
    content = _content("hello")
    items = _items(_make_agent(monkeypatch, events), "hello", content)

    assert [type(item).__name__ for item in items] == ["ImageInput", "ImageInput", "TextInput", "TextInput"]
    assert items[0].url == f"data:image/png;base64,{base64.b64encode(PNG).decode()}"
    assert items[1].url == f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode()}"
    assert items[2].text == build_manifest(content.manifest)
    assert items[3].text == "hello"
    # Only the user-text part is folded, once, after the reconcile.
    assert events == [("reconcile",), ("pending", "hello")]


def test_turn_input_never_uses_local_image_input(monkeypatch):
    items = _items(_make_agent(monkeypatch), "", _content(""))
    assert all(isinstance(item, ImageInput | TextInput) for item in items)
    assert all(item.url.startswith("data:") for item in items if isinstance(item, ImageInput))


def test_fold_runs_only_on_the_user_text_part(monkeypatch):
    agent = _make_agent(monkeypatch, pending="<twicc:context>x</twicc:context>\n")
    content = _content("look")
    items = _items(agent, "look", content)
    assert items[-1].text == "<twicc:context>x</twicc:context>\nlook"
    assert items[-2].text == build_manifest(content.manifest)


def test_file_only_message_omits_an_empty_user_text_part(monkeypatch):
    content = _file_only_content("")
    items = _items(_make_agent(monkeypatch), "", content)
    assert [type(item).__name__ for item in items] == ["TextInput"]
    assert items[0].text == build_manifest(content.manifest)


def test_file_only_message_keeps_a_pending_context_as_the_last_part(monkeypatch):
    agent = _make_agent(monkeypatch, pending="<twicc:context>x</twicc:context>")
    items = _items(agent, "", _file_only_content(""))
    assert [type(item).__name__ for item in items] == ["TextInput", "TextInput"]
    assert items[-1].text == "<twicc:context>x</twicc:context>"


def test_content_replaces_the_legacy_images(monkeypatch):
    legacy = [{"type": "image", "source": {"type": "base64", "media_type": "image/gif", "data": "R0lG"}}]
    items = _items(_make_agent(monkeypatch), "hi", _content("hi"), images=legacy)
    assert not any(isinstance(item, ImageInput) and item.url.startswith("data:image/gif") for item in items)


def test_a_non_image_native_part_fails_before_the_fold(monkeypatch):
    events: list = []
    entries = (ManifestEntry(1, "spec.pdf", "PDF", 1, 1, "inline", None),)
    content = AttachmentContent(
        (NativePart("PDF", "application/pdf", b"%PDF"),), AttachmentManifest("thread-id", None, entries), "x",
    )
    with pytest.raises(ValueError):
        _items(_make_agent(monkeypatch, events), "x", content)
    assert events == []


def test_legacy_turn_input_is_unchanged_without_content(monkeypatch):
    legacy = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}]
    items = _items(_make_agent(monkeypatch), "hi", None, images=legacy)
    assert [type(item).__name__ for item in items] == ["ImageInput", "TextInput"]
    assert items[0].url == "data:image/png;base64,AA=="


# ----------------------------------------------------------------------
# Agent start / send
# ----------------------------------------------------------------------


def _schedule_recorder(agent: CodexAgent) -> list:
    scheduled: list = []

    def schedule(text, images, **kwargs):
        scheduled.append((text, images, kwargs))

    agent._schedule_turn = schedule
    agent._notify_state_change = AsyncMock()
    return scheduled


def test_start_consumes_the_initial_content_once_and_a_later_steer_carries_only_its_text(monkeypatch):
    agent = _make_agent(monkeypatch)
    scheduled = _schedule_recorder(agent)
    content = _content("hello")
    agent._initial_content = content

    legacy = [{"type": "image", "source": {"type": "base64", "media_type": "image/gif", "data": "R0lG"}}]
    asyncio.run(agent.start("hello", AsyncMock(), resume=False, images=legacy))

    assert agent._initial_content is None
    assert scheduled == [("hello", None, {"content": content})]

    # A later steer of the same turn: its own new text only, no replayed content.
    agent.state = AgentState.ASSISTANT_TURN
    agent._current_turn_ready.set()
    agent._current_turn = MagicMock(steer=AsyncMock())
    assert asyncio.run(agent.send("more")) is True
    (steered,) = agent._current_turn.steer.await_args.args
    assert [type(item).__name__ for item in steered] == ["TextInput"]
    assert steered[0].text == "more"


def test_start_uses_a_forwarded_content_for_a_resume(monkeypatch):
    agent = _make_agent(monkeypatch)
    scheduled = _schedule_recorder(agent)
    content = _file_only_content("")
    asyncio.run(agent.start("", AsyncMock(), resume=True, content=content))
    assert scheduled == [("", None, {"content": content})]


def test_legacy_start_keeps_its_exact_schedule_call(monkeypatch):
    agent = _make_agent(monkeypatch)
    scheduled = _schedule_recorder(agent)
    asyncio.run(agent.start("hello", AsyncMock(), resume=False))
    assert scheduled == [("hello", None, {})]


def test_send_in_user_turn_schedules_the_content(monkeypatch):
    agent = _make_agent(monkeypatch)
    scheduled = _schedule_recorder(agent)
    agent.state = AgentState.USER_TURN
    content = _file_only_content("")
    assert asyncio.run(agent.send("", content=content)) is True
    assert scheduled == [("", None, {"content": content})]


def test_mid_turn_steer_carries_the_content(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent.state = AgentState.ASSISTANT_TURN
    agent._current_turn_ready.set()
    agent._current_turn = MagicMock(steer=AsyncMock())
    content = _content("")
    assert asyncio.run(agent.send("", content=content)) is True
    (steered,) = agent._current_turn.steer.await_args.args
    assert [type(item).__name__ for item in steered] == ["ImageInput", "ImageInput", "TextInput"]


def test_goal_monitor_steer_carries_the_content(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent.state = AgentState.ASSISTANT_TURN
    agent._goal_monitor = MagicMock(steer=AsyncMock())
    assert asyncio.run(agent.send("", content=_file_only_content(""))) is True
    (steered,) = agent._goal_monitor.steer.await_args.args
    assert [type(item).__name__ for item in steered] == ["TextInput"]


def test_hold_break_schedules_the_content(monkeypatch):
    agent = _make_agent(monkeypatch)
    scheduled = _schedule_recorder(agent)
    agent._broadcast_process_label = AsyncMock()
    agent.state = AgentState.ASSISTANT_TURN
    agent._subagent_hold_active = True
    content = _file_only_content("")
    assert asyncio.run(agent.send("", content=content)) is True
    assert scheduled == [("", None, {"content": content})]


def test_scheduled_turn_forwards_the_content_to_run_turn(monkeypatch):
    agent = _make_agent(monkeypatch)
    agent._run_turn = AsyncMock()
    content = _file_only_content("")

    async def run() -> None:
        agent._schedule_turn("", None, content=content)
        await agent._turn_task

    asyncio.run(run())
    agent._run_turn.assert_awaited_once_with("", None, content=content)


def test_run_turn_opens_the_turn_with_the_ordered_content(monkeypatch):
    agent = _make_agent(monkeypatch)
    opened: list = []

    async def turn_with_policy(turn_input, **kwargs):
        opened.append(turn_input)
        raise RuntimeError("stop here")

    agent._thread = SimpleNamespace(turn_with_policy=turn_with_policy)
    agent._handle_error = AsyncMock()
    asyncio.run(agent._run_turn("hello", None, content=_content("hello")))
    assert [type(item).__name__ for item in opened[0]] == ["ImageInput", "ImageInput", "TextInput", "TextInput"]


# ----------------------------------------------------------------------
# Manager: existing session
# ----------------------------------------------------------------------


def _plan(kind: str = "PDF", name: str = "spec.pdf") -> AttachmentPlan:
    ref = AttachmentRef("bucket", "id")
    source = StagedEntry(ref, name, 5, Path(f"/staged/{name}"), None)
    return AttachmentPlan(TARGET, (PlannedEntry(ref, 1, name, kind, 1, 1, "file", source, None),))


class RecordingLock:
    """Stands in for the manager ``_lock`` and records when it is taken."""

    def __init__(self, events: list) -> None:
        self.events = events

    async def __aenter__(self):
        self.events.append("manager_lock")
        return self

    async def __aexit__(self, *exc):
        self.events.append("manager_unlock")
        return False


class FakeLiveAgent:
    def __init__(self, events: list, state=AgentState.USER_TURN) -> None:
        self.events = events
        self.state = state
        self.agent_settings = AgentSettings()
        self.sent: list = []

    async def apply_agent_settings(self, settings) -> None:
        self.events.append("apply_agent_settings")

    async def send(self, text, **kwargs) -> bool:
        self.events.append("send")
        self.sent.append((text, kwargs))
        return True


@pytest.fixture
def commit_spy(monkeypatch):
    """Fake prepare / finish / discard that record their order and return a known content."""
    events: list = []
    prepared = PreparedAttachments(_plan(), ())
    calls = SimpleNamespace(events=events, prepared=prepared, finish_error=None, discarded=0)

    def prepare(plan, *, session_id):
        events.append(("prepare", session_id))
        return prepared

    def finish(prepared_arg, *, session_id, text):
        events.append(("finish", session_id, text))
        if calls.finish_error is not None:
            raise calls.finish_error
        return _file_only_content(text)._replace(
            manifest=_file_only_content(text).manifest._replace(owner=session_id),
        )

    def discard(prepared_arg):
        assert prepared_arg is prepared
        calls.discarded += 1

    monkeypatch.setattr(committer, "prepare_attachments", prepare)
    monkeypatch.setattr(committer, "finish_attachments", finish)
    monkeypatch.setattr(committer, "discard_prepared", discard)

    @contextlib.asynccontextmanager
    async def gate(session_id):
        events.append(("gate", session_id))
        yield

    monkeypatch.setattr(manager_module, "gate_for", gate)

    pending = MagicMock(side_effect=AssertionError("pending context consumed by the manager"))
    monkeypatch.setattr("twicc.context_injection.apply_pending_context", pending)
    monkeypatch.setattr(agent_module, "apply_pending_context", pending)
    return calls


def _manager(events: list) -> CodexAgentManager:
    manager = CodexAgentManager()
    manager._lock = RecordingLock(events)
    return manager


def _send(manager, text="", plan=None, **kwargs):
    return asyncio.run(manager.send_to_session(
        "thread-id", "project-id", "/project", text, AgentSettings(),
        attachment_plan=_plan() if plan is None else plan, **kwargs,
    ))


def test_existing_session_commits_before_the_gate_and_the_lock(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(events)

    assert _send(manager, "look") is True
    assert events[:4] == [
        ("prepare", "thread-id"), ("finish", "thread-id", "look"), ("gate", "thread-id"), "manager_lock",
    ]
    text, kwargs = agent.sent[0]
    assert text == "look"
    assert kwargs["content"].user_text == "look"
    assert kwargs["content"].manifest.owner == "thread-id"
    assert commit_spy.discarded == 1


def test_file_only_follow_up_in_user_turn_sends_the_content(commit_spy):
    manager = _manager(commit_spy.events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(commit_spy.events)
    assert _send(manager, "") is True
    assert agent.sent[0][1]["content"] is not None


def test_file_only_mid_turn_steer_sends_the_content(commit_spy):
    manager = _manager(commit_spy.events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(commit_spy.events, state=AgentState.ASSISTANT_TURN)
    assert _send(manager, "") is True
    assert agent.sent[0][1]["content"] is not None


def test_file_only_message_resumes_a_cold_session_with_the_content(commit_spy, monkeypatch):
    events = commit_spy.events
    manager = _manager(events)
    start = AsyncMock(side_effect=lambda *a, **k: events.append("start_agent"))
    monkeypatch.setattr(manager, "_start_agent", start)

    assert _send(manager, "") is True
    assert events.index(("finish", "thread-id", "")) < events.index(("gate", "thread-id"))
    assert events.index("manager_lock") < events.index("start_agent")
    assert start.await_args.kwargs["content"].user_text == ""
    assert start.await_args.kwargs["resume"] is True


def test_settings_only_update_without_a_plan_sends_nothing(commit_spy):
    manager = _manager(commit_spy.events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(commit_spy.events)
    assert asyncio.run(manager.send_to_session(
        "thread-id", "project-id", "/project", "", AgentSettings(),
    )) is False
    assert agent.sent == []


def test_empty_plan_keeps_the_legacy_send_call(commit_spy):
    manager = _manager(commit_spy.events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(commit_spy.events)
    _send(manager, "hi", plan=AttachmentPlan(TARGET, ()))
    assert not any(isinstance(event, tuple) and event[0] == "prepare" for event in commit_spy.events)
    assert agent.sent == [("hi", {"images": None})]


def test_existing_session_commit_error_reaches_neither_the_gate_nor_the_agent(commit_spy):
    events = commit_spy.events
    commit_spy.finish_error = SendDeliveryError("disk", code="attachment_commit_failed")
    manager = _manager(events)
    agent = manager._agents["thread-id"] = FakeLiveAgent(events)

    with pytest.raises(SendDeliveryError) as caught:
        _send(manager, "look")
    assert caught.value.code == "attachment_commit_failed"
    assert ("gate", "thread-id") not in events
    assert "manager_lock" not in events
    assert agent.sent == []
    assert commit_spy.discarded == 1


def test_hardcoded_command_with_attachments_is_refused_before_any_commit(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    manager._agents["thread-id"] = FakeLiveAgent(events)
    with pytest.raises(SendDeliveryError) as caught:
        _send(manager, "/compact")
    assert caught.value.code == "attachments_with_command"
    assert events == []


def test_new_session_hardcoded_command_with_attachments_is_refused_before_any_commit(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(manager.create_session(
            "draft-cmd", "project-id", "/project", "/goal ship it", AgentSettings(), attachment_plan=_plan(),
        ))
    assert caught.value.code == "attachments_with_command"
    assert events == []


def test_document_warning_only_on_legacy_documents(commit_spy, monkeypatch):
    warnings: list[str] = []
    # Recorded on the module logger itself: other suites may reconfigure logging propagation.
    monkeypatch.setattr(manager_module.logger, "warning", lambda message, *args: warnings.append(message % args))
    manager = _manager(commit_spy.events)
    manager._agents["thread-id"] = FakeLiveAgent(commit_spy.events)

    _send(manager, "pdf as a file", plan=_plan("PDF", "spec.pdf"))
    assert warnings == []

    asyncio.run(manager.send_to_session(
        "thread-id", "project-id", "/project", "legacy", AgentSettings(),
        documents=[{"type": "document"}],
    ))
    assert len(warnings) == 1
    assert "1 document attachment(s)" in warnings[0]


def test_ephemeral_readonly_refusal_happens_before_any_commit(commit_spy):
    events = commit_spy.events
    manager = _manager(events)

    def refuse(*args):
        raise SendDeliveryError("read only", code="ephemeral_readonly")

    manager._check_ephemeral_readonly = refuse
    with pytest.raises(SendDeliveryError):
        _send(manager, "x")
    assert events == []


# ----------------------------------------------------------------------
# Manager: new session (canonical binding)
# ----------------------------------------------------------------------


class _FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.id = thread_id

    async def update_settings_with_policy(self, **kwargs) -> None:
        pass


class _FakeCodex:
    def __init__(self, events: list, thread_id: str, *, start_error: BaseException | None = None) -> None:
        self.events = events
        self.thread_id = thread_id
        self.start_error = start_error
        self.closed = False
        self.start_calls: list[dict] = []

    async def thread_start_with_policy(self, **kwargs):
        self.events.append("thread_start")
        self.start_calls.append(deepcopy(kwargs))
        if self.start_error is not None:
            raise self.start_error
        return _FakeThread(self.thread_id)

    async def close(self) -> None:
        self.events.append("close")
        self.closed = True


class _FakeNewAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.session_id = kwargs["session_id"]
        self.ephemeral = kwargs.get("ephemeral", False)
        self._initial_content = None
        self.killed: list[str] = []

    async def _seed_context_baseline(self, *, pending_id: str) -> None:
        pass

    def _reset_context_baseline(self) -> None:
        pass

    async def interrupt_or_kill(self, reason: str) -> None:
        self.killed.append(reason)


def _install_creation_fakes(monkeypatch, events: list, thread_id: str, *, start_error=None) -> _FakeCodex:
    codex = _FakeCodex(events, thread_id, start_error=start_error)

    async def fake_make_codex_config(*, cwd):
        return SimpleNamespace(config_overrides=())

    async def fake_work_dirs(session_id, *, pending_id=None):
        events.append(("work_dirs", session_id))
        return []

    monkeypatch.setattr(manager_module, "make_codex_config", fake_make_codex_config)
    monkeypatch.setattr(manager_module, "TwiccAsyncCodex", lambda *, config: codex)
    monkeypatch.setattr(manager_module, "attach_stderr_logging", lambda *args: None)
    monkeypatch.setattr(manager_module, "resolve_and_create_work_dirs", fake_work_dirs)
    monkeypatch.setattr(manager_module, "CodexAgent", _FakeNewAgent)
    monkeypatch.setattr(manager_module, "inject_context", lambda *args, **kwargs: None)

    from twicc import mcp
    from twicc.core.services import trust
    from twicc.mcp import identity

    monkeypatch.setattr(mcp, "mcp_enabled", lambda: False)
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project_id: False)
    monkeypatch.setattr(identity, "register_draft_alias", lambda *args: None)
    return codex


@pytest.fixture
def lanes():
    send_lanes._reset_for_tests()
    yield
    send_lanes._reset_for_tests()


def _creation_manager(monkeypatch, events: list, *, register_error=None) -> tuple[CodexAgentManager, list]:
    manager = _manager(events)
    started: list = []

    async def notify(*, draft_session_id, session_id):
        events.append(("notify_session_bound", draft_session_id, session_id))
        # Task 9's lane alias is already in place when the binding is exposed.
        events.append(("lane_aliased", send_lanes._lanes.get(session_id) is send_lanes._lanes.get(draft_session_id)))

    async def register_and_start(agent, text, resume, **start_kwargs):
        events.append("register_and_start")
        started.append((agent, text, resume, start_kwargs, agent._initial_content))
        if register_error is not None:
            raise register_error

    monkeypatch.setattr(manager, "notify_session_bound", notify)
    monkeypatch.setattr(manager, "_register_and_start", register_and_start)
    return manager, started


def _create(manager, draft_id: str, text: str, plan: AttachmentPlan) -> str:
    async def run() -> str:
        async with send_lanes.send_lane(draft_id):
            return await manager.create_session(
                draft_id, "project-id", "/project", text, AgentSettings(permission_mode="yolo"),
                attachment_plan=plan,
            )

    return asyncio.run(run())


def test_new_session_prepares_before_the_lock_and_finishes_before_binding(commit_spy, monkeypatch, lanes):
    events = commit_spy.events
    _install_creation_fakes(monkeypatch, events, "canonical-1")
    manager, started = _creation_manager(monkeypatch, events)

    assert _create(manager, "draft-1", "original text", _plan()) == "canonical-1"

    assert events.index(("prepare", None)) < events.index("manager_lock")
    assert events.index("thread_start") < events.index(("work_dirs", "canonical-1"))
    assert events.index(("work_dirs", "canonical-1")) < events.index(("finish", "canonical-1", "original text"))
    assert events.index(("finish", "canonical-1", "original text")) < events.index(
        ("notify_session_bound", "draft-1", "canonical-1"),
    )
    assert ("lane_aliased", True) in events
    (agent, text, resume, start_kwargs, initial) = started[0]
    assert agent.session_id == "canonical-1"
    assert initial.user_text == "original text"
    assert initial.manifest.owner == "canonical-1"
    # Preparation and text travel as factory kwargs, never as agent.start kwargs.
    assert "content" not in start_kwargs
    assert "prepared_attachments" not in start_kwargs
    assert "initial_text" not in start_kwargs
    assert commit_spy.discarded == 1


def test_new_session_finish_error_closes_the_client_and_never_binds(commit_spy, monkeypatch, lanes):
    events = commit_spy.events
    commit_spy.finish_error = SendDeliveryError("disk", code="attachment_commit_failed")
    codex = _install_creation_fakes(monkeypatch, events, "canonical-2")
    manager, started = _creation_manager(monkeypatch, events)

    with pytest.raises(SendDeliveryError) as caught:
        _create(manager, "draft-2", "hello", _plan())
    assert caught.value.code == "attachment_commit_failed"
    closed_client_on_commit_failure = codex.closed
    assert closed_client_on_commit_failure is True
    assert not any(isinstance(event, tuple) and event[0] == "notify_session_bound" for event in events)
    assert started == []
    assert commit_spy.discarded == 1


def test_new_session_startup_error_before_finish_discards_the_precopies(commit_spy, monkeypatch, lanes):
    events = commit_spy.events
    _install_creation_fakes(monkeypatch, events, "canonical-3", start_error=RuntimeError("no thread"))
    manager, _ = _creation_manager(monkeypatch, events)

    with pytest.raises(RuntimeError):
        _create(manager, "draft-3", "hello", _plan())
    assert not any(isinstance(event, tuple) and event[0] == "finish" for event in events)
    assert commit_spy.discarded == 1


def test_new_session_without_a_plan_keeps_the_legacy_factory_call(monkeypatch, lanes):
    events: list = []
    _install_creation_fakes(monkeypatch, events, "canonical-4")
    manager, started = _creation_manager(monkeypatch, events)
    factory_kwargs: list = []
    original = manager._create_agent

    async def spy(*args, **kwargs):
        factory_kwargs.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "_create_agent", spy)
    assert _create(manager, "draft-4", "hello", AttachmentPlan(TARGET, ())) == "canonical-4"
    assert set(factory_kwargs[0]) == {"resume", "settings"}
    assert started[0][4] is None


@pytest.mark.usefixtures("lanes")
def test_retry_onto_another_canonical_id_copies_an_independent_artifact(root, monkeypatch):  # noqa: F811
    from tests.test_composer_attachment_commit import plan_of

    ref, entry, _source = stage(root, name="notes.txt", content=b"notes")

    # First attempt: the attachment is promoted into thread-A, then the startup fails.
    events: list = []
    _install_creation_fakes(monkeypatch, events, "thread-A")
    manager, _ = _creation_manager(monkeypatch, events, register_error=RuntimeError("startup failed"))
    with pytest.raises(RuntimeError):
        _create(manager, "draft-retry", "hello", plan_of((ref, "text", None)))
    first_file = attachments_dir("thread-A") / "notes.txt"
    assert first_file.read_bytes() == b"notes"
    assert promoted_marker(entry)["session_id"] == "thread-A"

    # Retry of the same draft: Codex mints thread-B; the tombstone is copied, never moved.
    events = []
    _install_creation_fakes(monkeypatch, events, "thread-B")
    manager, started = _creation_manager(monkeypatch, events)
    retry_plan = plan_of((ref, "text", None))
    assert retry_plan.entries[0].source.promoted is not None
    assert _create(manager, "draft-retry", "hello", retry_plan) == "thread-B"

    second_file = attachments_dir("thread-B") / "notes.txt"
    assert second_file.read_bytes() == b"notes"
    assert first_file.read_bytes() == b"notes"
    assert os.stat(second_file).st_ino != os.stat(first_file).st_ino
    marker = promoted_marker(entry)
    assert marker["session_id"] == "thread-B"
    assert marker["final_path"] == str(Path(os.path.realpath(second_file)))
    initial = started[0][4]
    assert initial.manifest.owner == "thread-B"
    assert initial.manifest.entries[0].artifact_name == "notes.txt"
    assert precopies() == []
