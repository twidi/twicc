"""Claude SDK delivery of composer attachment content (spec §7.1, §7.5).

The prompt carries the ordered native blocks, then the manifest text block, then the folded user
text LAST. The manager commits the plan (prepare + finish) before its ``_lock`` on every path, owns
the pre-copies in a ``try``/``finally``, threads the structured content to the agent (live send,
resume, new session, parked restarts), and lets a commit error propagate as ``SendDeliveryError``.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.agent import AgentState, SendDeliveryError
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
from twicc.providers.claude_code.agent import agent as agent_module
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings

PNG = b"\x89PNG\r\n\x1a\nfirst"
PNG_2 = b"\x89PNG\r\n\x1a\nsecond"
PDF = b"%PDF-1.7 body"
LONG_NAME = "r" * 230 + ".pdf"


def _content(user_text: str = "hello") -> AttachmentContent:
    """image, PDF (long name), video file, image: three native parts and one file entry."""
    entries = (
        ManifestEntry(1, "login.png", "image", 1, 2, "inline", None),
        ManifestEntry(2, LONG_NAME, "PDF", 1, 1, "inline", None),
        ManifestEntry(3, "capture.mp4", "video", 1, 1, "file", "capture.mp4"),
        ManifestEntry(4, "after.png", "image", 2, 2, "inline", None),
    )
    manifest = AttachmentManifest("session-id", Path("/data/artifacts/session-id/attachments"), entries)
    parts = (
        NativePart("image", "image/png", PNG),
        NativePart("PDF", "application/pdf", PDF),
        NativePart("image", "image/webp", PNG_2),
    )
    return AttachmentContent(parts, manifest, user_text)


def _text_content(user_text: str = "") -> AttachmentContent:
    entries = (ManifestEntry(1, "notes@home.md", "text", 1, 1, "inline", None),)
    manifest = AttachmentManifest("session-id", None, entries)
    return AttachmentContent((NativePart("text", "text/plain", "# Notes\nbody"),), manifest, user_text)


# ----------------------------------------------------------------------
# Prompt building
# ----------------------------------------------------------------------


def _make_agent(monkeypatch: pytest.MonkeyPatch, events: list | None = None, *, pending="", goal="") -> ClaudeCodeAgent:
    """An SDK agent whose fold records its calls; *pending* / *goal* decorate the folded text."""
    events = events if events is not None else []
    agent = ClaudeCodeAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="opus", permission_mode="bypassPermissions"),
        AsyncMock(return_value=None),
        AsyncMock(),
        AsyncMock(),
    )

    async def reconcile() -> None:
        events.append(("reconcile",))

    def apply_pending(session_id: str, text: str) -> str:
        events.append(("pending", text))
        return pending + text

    def apply_goal(text: str) -> str:
        events.append(("goal", text))
        return text + goal if text else text

    monkeypatch.setattr(agent, "_reconcile_context", reconcile)
    monkeypatch.setattr(agent_module, "apply_pending_context", apply_pending)
    monkeypatch.setattr(agent_module, "apply_goal_instruction", apply_goal)
    return agent


async def _blocks(stream) -> list[dict]:
    message = await anext(stream)
    return message["message"]["content"]


def _prompt(agent: ClaudeCodeAgent, text: str, content: AttachmentContent | None, **legacy) -> list[dict]:
    async def run() -> list[dict]:
        stream = await agent._build_query_prompt(
            text, legacy.get("images"), legacy.get("documents"), content=content,
        )
        return await _blocks(stream)

    return asyncio.run(run())


def test_prompt_orders_native_blocks_then_manifest_then_user_text(monkeypatch):
    content = _content("hello")
    prompt = _prompt(_make_agent(monkeypatch), "hello", content)

    assert [part["type"] for part in prompt] == ["image", "document", "image", "text", "text"]
    assert prompt[-1]["text"] == "hello"
    assert prompt[-2]["text"].startswith("<twicc:attachments>\n")
    assert prompt[-2]["text"] == build_manifest(content.manifest)

    first, document, second = prompt[:3]
    assert first["source"] == {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}
    assert second["source"] == {
        "type": "base64", "media_type": "image/webp", "data": base64.b64encode(PNG_2).decode(),
    }
    assert "title" not in first
    assert document["source"] == {
        "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(PDF).decode(),
    }
    assert document["title"] == LONG_NAME[:200]
    assert len(document["title"]) == 200


def test_prompt_sends_a_text_native_entry_as_a_text_document(monkeypatch):
    prompt = _prompt(_make_agent(monkeypatch), "read it", _text_content("read it"))
    assert [part["type"] for part in prompt] == ["document", "text", "text"]
    assert prompt[0] == {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "# Notes\nbody"},
        "title": "notes@home.md",
    }
    assert prompt[-1]["text"] == "read it"


def test_fold_runs_only_on_the_user_text_part(monkeypatch):
    events: list = []
    agent = _make_agent(monkeypatch, events, pending="<twicc:context>x</twicc:context>\n", goal="\nGOAL")
    content = _content("/goal ship it")
    prompt = _prompt(agent, "/goal ship it", content)

    folded = "<twicc:context>x</twicc:context>\n/goal ship it\nGOAL"
    assert events == [
        ("reconcile",),
        ("pending", "/goal ship it"),
        ("goal", "<twicc:context>x</twicc:context>\n/goal ship it"),
    ]
    assert prompt[-1]["text"] == folded
    # The manifest block is never folded.
    assert prompt[-2]["text"] == build_manifest(content.manifest)


def test_file_only_message_omits_an_empty_user_text_part(monkeypatch):
    content = _content("")
    prompt = _prompt(_make_agent(monkeypatch), "", content)
    assert [part["type"] for part in prompt] == ["image", "document", "image", "text"]
    assert prompt[-1]["text"] == build_manifest(content.manifest)


def test_file_only_message_keeps_a_pending_context_as_the_last_part(monkeypatch):
    agent = _make_agent(monkeypatch, pending="<twicc:context>x</twicc:context>")
    prompt = _prompt(agent, "", _text_content(""))
    assert [part["type"] for part in prompt] == ["document", "text", "text"]
    assert prompt[-1]["text"] == "<twicc:context>x</twicc:context>"


def test_slash_command_keeps_its_final_user_text_unchanged(monkeypatch):
    prompt = _prompt(_make_agent(monkeypatch), "/compact focus on tests", _content("/compact focus on tests"))
    assert prompt[-1] == {"type": "text", "text": "/compact focus on tests"}
    assert prompt[-2]["text"].startswith("<twicc:attachments>\n")


def test_legacy_prompt_is_unchanged_without_content(monkeypatch):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
    assert _prompt(_make_agent(monkeypatch), "hi", None, images=[image]) == [image, {"type": "text", "text": "hi"}]


# ----------------------------------------------------------------------
# Agent start / send
# ----------------------------------------------------------------------


def test_send_hands_the_structured_content_to_the_sdk(monkeypatch):
    agent = _make_agent(monkeypatch)
    queried: list = []

    async def query(stream) -> None:
        queried.append(await _blocks(stream))

    agent._client = SimpleNamespace(query=query)
    agent.state = AgentState.USER_TURN
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()

    assert asyncio.run(agent.send("", content=_text_content(""))) is True
    assert [part["type"] for part in queried[0]] == ["document", "text"]


@pytest.mark.django_db(transaction=True)
def test_start_uses_the_content_and_never_invokes_on_delivered(monkeypatch):
    from twicc.core.services import trust
    from twicc.providers.claude_code import sessions_watcher

    queried: list = []

    async def query(stream) -> None:
        queried.append(await _blocks(stream))

    fake = SimpleNamespace(connect=AsyncMock(), query=query)
    monkeypatch.setattr(agent_module, "ClaudeSDKClient", lambda *, options: fake)
    monkeypatch.setattr(agent_module, "patch_client_for_logging", lambda *args: None)
    monkeypatch.setattr(agent_module, "attach_elicitation_handler", lambda *args: None)
    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: SimpleNamespace(request_fast_poll=lambda: None))
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project: False)
    agent = ClaudeCodeAgent(
        "id", "project", "/project", AgentSettings(permission_mode="default"),
        AsyncMock(), AsyncMock(), AsyncMock(), ephemeral=True,
    )
    agent._seed_context_baseline = AsyncMock()
    agent._reconcile_context = AsyncMock()
    agent._run_message_loop = AsyncMock()
    monkeypatch.setattr(agent_module, "apply_pending_context", lambda session_id, text: text)
    on_delivered = MagicMock()

    async def run() -> None:
        await agent.start("hello", AsyncMock(), resume=False, content=_content("hello"), on_delivered=on_delivered)
        if agent._message_loop_task:
            await agent._message_loop_task

    asyncio.run(run())
    assert agent.error is None
    assert [part["type"] for part in queried[0]] == ["image", "document", "image", "text", "text"]
    on_delivered.assert_not_called()


# ----------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------


TARGET = PlanTarget("claude_code", False, False, "opus", False, "first_party")


def _plan() -> AttachmentPlan:
    ref = AttachmentRef("bucket", "id")
    source = StagedEntry(ref, "notes.txt", 5, Path("/staged/notes.txt"), None)
    return AttachmentPlan(TARGET, (PlannedEntry(ref, 1, "notes.txt", "text", 1, 1, "file", source, None),))


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


class FakeAgent:
    is_hybrid = False

    def __init__(self, events: list, state=AgentState.USER_TURN, settings=None) -> None:
        self.events = events
        self.state = state
        self.agent_settings = settings or AgentSettings()
        self.process_run = None
        self.session_id = "session-id"
        self.project_id = "project-id"
        self.cwd = "/project"
        self.sent: list = []

    def background_shell_count(self) -> int:
        return 0

    async def apply_live_settings(self, settings) -> None:
        self.events.append("apply_live_settings")

    async def send(self, text, **kwargs) -> bool:
        self.events.append("send")
        self.sent.append((text, kwargs))
        return True

    async def interrupt_or_kill(self, reason: str) -> None:
        self.events.append(("kill", reason))
        self.state = AgentState.DEAD


@pytest.fixture
def commit_spy(monkeypatch):
    """Fake prepare / finish / discard that record their order and return a known content."""
    events: list = []
    content = _text_content("")
    prepared = PreparedAttachments(_plan(), ())
    calls = SimpleNamespace(events=events, content=content, prepared=prepared, finish_error=None, discarded=0)

    def prepare(plan, *, session_id):
        events.append(("prepare", session_id))
        return prepared

    def finish(prepared_arg, *, session_id, text):
        events.append(("finish", session_id, text))
        if calls.finish_error is not None:
            raise calls.finish_error
        return content._replace(user_text=text)

    def discard(prepared_arg):
        calls.discarded += 1

    monkeypatch.setattr(committer, "prepare_attachments", prepare)
    monkeypatch.setattr(committer, "finish_attachments", finish)
    monkeypatch.setattr(committer, "discard_prepared", discard)

    pending = MagicMock(side_effect=AssertionError("pending context consumed by the manager"))
    monkeypatch.setattr("twicc.context_injection.apply_pending_context", pending)
    monkeypatch.setattr(agent_module, "apply_pending_context", pending)
    return calls


def _manager(events: list) -> ClaudeCodeAgentManager:
    manager = ClaudeCodeAgentManager()
    manager._lock = RecordingLock(events)
    manager._check_ephemeral_readonly = lambda *args: None
    return manager


def _send(manager, text="", settings=None, **kwargs):
    return asyncio.run(manager.send_to_session(
        "session-id", "project-id", "/project", text, settings or AgentSettings(),
        attachment_plan=_plan(), **kwargs,
    ))


def test_existing_session_commits_before_the_lock_and_sends_the_content(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events)

    assert _send(manager, "look") is True
    assert events[:3] == [("prepare", "session-id"), ("finish", "session-id", "look"), "manager_lock"]
    text, kwargs = agent.sent[0]
    assert text == "look"
    assert kwargs["content"].user_text == "look"
    assert commit_spy.discarded >= 1


def test_file_only_follow_up_counts_the_content_as_content(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events, state=AgentState.ASSISTANT_TURN)

    assert _send(manager, "") is True
    assert agent.sent[0][1]["content"] is not None


def test_resumed_session_commits_before_the_lock_and_starts_with_the_content(commit_spy, monkeypatch):
    events = commit_spy.events
    manager = _manager(events)
    start = AsyncMock(side_effect=lambda *a, **k: events.append("start_agent"))
    monkeypatch.setattr(manager, "_start_agent", start)

    assert _send(manager, "") is True
    assert events.index(("finish", "session-id", "")) < events.index("manager_lock") < events.index("start_agent")
    assert start.await_args.kwargs["content"] is not None
    assert start.await_args.kwargs["resume"] is True


def test_new_session_commits_with_the_draft_id_before_the_lock(commit_spy, monkeypatch):
    events = commit_spy.events
    manager = _manager(events)
    start = AsyncMock(side_effect=lambda *a, **k: events.append("start_agent") or "session-id")
    monkeypatch.setattr(manager, "_start_agent", start)

    canonical = asyncio.run(manager.create_session(
        "session-id", "project-id", "/project", "hello", AgentSettings(), attachment_plan=_plan(),
    ))
    assert canonical == "session-id"
    assert events[:3] == [("prepare", "session-id"), ("finish", "session-id", "hello"), "manager_lock"]
    assert start.await_args.kwargs["content"].user_text == "hello"
    assert start.await_args.kwargs["resume"] is False


def test_commit_error_propagates_and_nothing_is_sent(commit_spy, monkeypatch):
    events = commit_spy.events
    commit_spy.finish_error = SendDeliveryError("disk", code="attachment_commit_failed")
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events)

    with pytest.raises(SendDeliveryError) as caught:
        _send(manager, "look")
    assert caught.value.code == "attachment_commit_failed"
    assert "manager_lock" not in events
    assert agent.sent == []
    assert commit_spy.discarded >= 1


def test_new_session_commit_error_never_starts_an_agent(commit_spy, monkeypatch):
    commit_spy.finish_error = SendDeliveryError("gone", code="attachment_missing")
    manager = _manager(commit_spy.events)
    start = AsyncMock()
    monkeypatch.setattr(manager, "_start_agent", start)
    with pytest.raises(SendDeliveryError):
        asyncio.run(manager.create_session(
            "session-id", "project-id", "/project", "hello", AgentSettings(), attachment_plan=_plan(),
        ))
    start.assert_not_awaited()


def test_prepare_cancellation_discards_the_late_precopies(monkeypatch):
    """Cancelled while prepare runs in its thread: its pre-copies are discarded once it ends."""
    import threading

    release = threading.Event()
    prepared = PreparedAttachments(_plan(), ())
    discarded: list = []

    def prepare(plan, *, session_id):
        release.wait(5)
        return prepared

    monkeypatch.setattr(committer, "prepare_attachments", prepare)
    monkeypatch.setattr(committer, "discard_prepared", discarded.append)
    manager = _manager([])

    async def run() -> None:
        task = asyncio.create_task(manager._commit_attachment_plan(_plan(), session_id="s", text=""))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(100):
            if discarded:
                break
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert discarded == [prepared]


def test_empty_plan_is_no_content(commit_spy, monkeypatch):
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events)
    asyncio.run(manager.send_to_session(
        "session-id", "project-id", "/project", "hi", AgentSettings(),
        attachment_plan=AttachmentPlan(TARGET, ()),
    ))
    assert not any(isinstance(event, tuple) and event[0] == "prepare" for event in events)
    assert agent.sent == [("hi", {"images": None, "documents": None})]


def test_settings_only_update_without_content_sends_nothing(commit_spy):
    """Legacy gate unchanged: no text, no attachments, no plan → no send."""
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events)
    assert asyncio.run(manager.send_to_session("session-id", "project-id", "/project", "", AgentSettings())) is False
    assert agent.sent == []


def test_background_shell_branch_sends_the_content(commit_spy):
    events = commit_spy.events
    manager = _manager(events)
    agent = manager._agents["session-id"] = FakeAgent(events)
    agent.background_shell_count = lambda: 1
    assert _send(manager, "", settings=AgentSettings(effort="max")) is True
    assert agent.sent[0][1]["content"] is not None


def test_startup_change_without_crons_restarts_with_the_content(commit_spy, monkeypatch):
    events = commit_spy.events
    manager = _manager(events)
    manager._agents["session-id"] = FakeAgent(events)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(manager, "_start_agent", start)

    assert _send(manager, "", settings=AgentSettings(effort="max")) is False
    assert start.await_args.kwargs["content"] is not None
    assert "session-id" not in manager._pending_after_restart


def test_startup_change_with_crons_parks_the_content_for_the_cron_restart(commit_spy, monkeypatch):
    from twicc.providers.claude_code import cron_restart

    events = commit_spy.events
    manager = _manager(events)
    manager._agents["session-id"] = FakeAgent(events)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=True))

    assert _send(manager, "", settings=AgentSettings(effort="max")) is False
    parked = manager._pending_after_restart["session-id"]
    assert parked["content"].user_text == ""
    assert parked["text"] == ""

    restarted = FakeAgent(events)
    manager._agents["session-id"] = restarted
    monkeypatch.setattr(cron_restart, "restart_session_crons", AsyncMock())
    asyncio.run(manager._restart_crons_for_session("session-id"))
    assert restarted.sent[0][1]["content"] is parked["content"]


def test_cron_restart_keeps_the_legacy_parked_shape(monkeypatch):
    from twicc.providers.claude_code import cron_restart

    manager = _manager([])
    restarted = manager._agents["session-id"] = FakeAgent([])
    manager._pending_after_restart["session-id"] = {"text": "hi", "images": None, "documents": None}
    monkeypatch.setattr(cron_restart, "restart_session_crons", AsyncMock())
    asyncio.run(manager._restart_crons_for_session("session-id"))
    assert restarted.sent == [("hi", {"images": None, "documents": None})]


def test_pending_settings_restart_starts_with_the_parked_content(monkeypatch):
    from twicc.core import models
    from twicc.providers.helpers import get_provider_helpers

    manager = _manager([])
    agent = FakeAgent([])
    content = _text_content("")
    manager._pending_after_restart["session-id"] = {"text": "", "images": None, "documents": None, "content": content}
    requested = AgentSettings(effort="max")
    monkeypatch.setattr(
        models.Session, "objects",
        SimpleNamespace(filter=lambda **kw: SimpleNamespace(first=lambda: SimpleNamespace())),
    )
    monkeypatch.setattr(AgentSettings, "from_session", staticmethod(lambda session: requested))
    helpers = get_provider_helpers("claude_code")
    monkeypatch.setattr(helpers, "resolve_agent_settings", lambda settings: settings)
    monkeypatch.setattr(helpers, "enforce_agent_settings_consistency", lambda settings: settings)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(manager, "_start_agent", start)

    asyncio.run(manager._apply_pending_settings(agent))
    assert start.await_args.kwargs["content"] is content


def test_pending_settings_restart_keeps_the_legacy_parked_shape(monkeypatch):
    from twicc.core import models
    from twicc.providers.helpers import get_provider_helpers

    manager = _manager([])
    agent = FakeAgent([])
    manager._pending_after_restart["session-id"] = {"text": "hi", "images": None, "documents": None}
    monkeypatch.setattr(
        models.Session, "objects",
        SimpleNamespace(filter=lambda **kw: SimpleNamespace(first=lambda: SimpleNamespace())),
    )
    monkeypatch.setattr(AgentSettings, "from_session", staticmethod(lambda session: AgentSettings(effort="max")))
    helpers = get_provider_helpers("claude_code")
    monkeypatch.setattr(helpers, "resolve_agent_settings", lambda settings: settings)
    monkeypatch.setattr(helpers, "enforce_agent_settings_consistency", lambda settings: settings)
    monkeypatch.setattr(manager, "_session_has_crons", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(manager, "_start_agent", start)

    asyncio.run(manager._apply_pending_settings(agent))
    assert "content" not in start.await_args.kwargs
    assert start.await_args.kwargs["images"] is None


def test_ephemeral_readonly_refusal_happens_before_any_commit(commit_spy):
    events = commit_spy.events
    manager = _manager(events)

    def refuse(*args):
        raise SendDeliveryError("read only", code="ephemeral_readonly")

    manager._check_ephemeral_readonly = refuse
    with pytest.raises(SendDeliveryError):
        _send(manager, "x")
    assert events == []
