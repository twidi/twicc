"""Hybrid delivery of composer attachment content (spec §6.2, §6.6, §7.4, §7.5).

The hybrid agent pastes one text into the Claude CLI TUI: the raw user text, a blank line, then the
``<twicc:attachments>`` block in its hybrid variant, whose inline lines end with the ``@<path>`` of an
``att_<12 hex><ext>`` file written in the session's hybrid dir. A message starting with ``/`` or
``!`` (both turn the TUI input into a command) never carries attachments: the refusal is raised
synchronously, before anything is launched, scheduled, written or pasted. ``on_delivered`` runs only
after a successful first paste. The plan target of an existing session takes ``hybrid`` from the database
flag, which a switch to hybrid writes inside the session's send lane.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc import asgi
from twicc.agent import AgentState, SendDeliveryError, send_lanes
from twicc.core.services.attachments import planner
from twicc.core.services.attachments import target as target_module
from twicc.core.services.attachments.manifest import INLINE_LINE_HYBRID, build_manifest, parse_manifest
from twicc.core.services.attachments.types import (
    AttachmentContent,
    AttachmentManifest,
    AttachmentRef,
    ManifestEntry,
    NativePart,
    PlanTarget,
)
from twicc.providers.claude_code.agent.hybrid import agent as hybrid_module
from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
from twicc.providers.helpers import AgentSettings

SESSION_ID = "0a1b2c3d-0000-4000-8000-000000000001"
PNG = b"\x89PNG\r\n\x1a\nfirst"
PNG_2 = b"\x89PNG\r\n\x1a\nsecond"
PDF = b"%PDF-1.7 body"
NATIVE_TEXT = "# Notes\nline two\n"
ATT_RE = re.compile(r"att_[0-9a-f]{12}\.[a-z]+")


def _content(user_text: str = "user text") -> AttachmentContent:
    """image, PDF and text parts (an SDK-target plan), a video file, a second image."""
    entries = (
        ManifestEntry(1, "login@2x.png", "image", 1, 2, "inline", None),
        ManifestEntry(2, "spec.pdf", "PDF", 1, 1, "inline", None),
        ManifestEntry(3, "capture.mp4", "video", 1, 1, "file", "capture (1).mp4"),
        ManifestEntry(4, "notes <draft>.md", "text", 1, 1, "inline", None),
        ManifestEntry(5, "after.png", "image", 2, 2, "inline", None),
    )
    manifest = AttachmentManifest(SESSION_ID, Path(f"/data/artifacts/{SESSION_ID}/attachments"), entries)
    parts = (
        NativePart("image", "image/png", PNG),
        NativePart("PDF", "application/pdf", PDF),
        NativePart("text", "text/plain", NATIVE_TEXT),
        NativePart("image", "image/webp", PNG_2),
    )
    return AttachmentContent(parts, manifest, user_text)


def _file_only_content(user_text: str = "see file") -> AttachmentContent:
    entries = (ManifestEntry(1, "capture.mp4", "video", 1, 1, "file", "capture.mp4"),)
    manifest = AttachmentManifest(SESSION_ID, Path(f"/data/artifacts/{SESSION_ID}/attachments"), entries)
    return AttachmentContent((), manifest, user_text)


class FakeTmux:
    """Records pastes; the composer is ready unless told otherwise."""

    def __init__(self, *, ready: bool = True, fail_paste: bool = False) -> None:
        self.ready = ready
        self.fail_paste = fail_paste
        self.pastes: list[str] = []
        self.created: list[str] = []
        self.killed: list[str] = []

    def paste_text(self, session_id: str, text: str, *, submit: bool = True) -> None:
        if self.fail_paste:
            raise RuntimeError("tmux paste-buffer failed")
        self.pastes.append(text)

    def composer_ready(self, session_id: str) -> bool:
        return self.ready

    def capture_pane(self, session_id: str) -> str:
        return ""

    def create_session(self, session_id: str, cwd: str, argv: list[str]) -> None:
        self.created.append(session_id)

    def session_exists(self, session_id: str) -> bool:
        return False

    def kill_session(self, session_id: str) -> None:
        self.killed.append(session_id)

    def pane_status(self, session_id: str) -> tuple[int | None, bool]:
        return 4242, False


@pytest.fixture
def hybrid_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The session hybrid dir, under a data dir path without whitespace."""
    return _patch_hybrid_dir(tmp_path / "data", monkeypatch)


def _patch_hybrid_dir(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def fake_hybrid_dir(session_id: str) -> Path:
        path = data_dir / "hybrid" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(hybrid_module, "get_session_hybrid_dir", fake_hybrid_dir)
    return data_dir / "hybrid" / SESSION_ID


@pytest.fixture
def tmux(monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
    fake = FakeTmux()
    for name in (
        "paste_text", "composer_ready", "capture_pane", "create_session",
        "session_exists", "kill_session", "pane_status",
    ):
        monkeypatch.setattr(hybrid_tmux, name, getattr(fake, name))
    return fake


def _agent() -> HybridClaudeAgent:
    agent = HybridClaudeAgent(SESSION_ID, "project-id", "/tmp", AgentSettings(permission_mode="default"))
    agent._state_change_callback = AsyncMock()
    return agent


@pytest.fixture
def no_fold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hybrid never folds ``<twicc:context>``: any call to the fold fails the test."""
    from twicc import context_injection

    def forbidden(*args, **kwargs):
        raise AssertionError("hybrid must not fold the context")

    monkeypatch.setattr(context_injection, "apply_pending_context", forbidden)
    monkeypatch.setattr(context_injection, "apply_goal_instruction", forbidden)


def _att_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("att_*")) if directory.exists() else []


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def test_send_pastes_user_text_then_the_hybrid_manifest(hybrid_dir, tmux, no_fold):
    content = _content("user text")
    delivered = asyncio.run(_agent().send("user text", content=content))

    assert delivered is True
    assert len(tmux.pastes) == 1
    paste = tmux.pastes[0]
    assert paste.startswith("user text\n\n<twicc:attachments>\n")
    assert "inline): @" in paste

    block = paste[len("user text\n\n"):]
    parsed = parse_manifest(block)
    assert parsed is not None
    assert parsed.hybrid is True
    assert parsed.entries == tuple(
        entry._replace(name=entry.artifact_name) if entry.mode == "file" else entry
        for entry in content.manifest.entries
    )
    lines = block.split("\n")
    assert lines[2] == INLINE_LINE_HYBRID
    assert lines[3] == f"file = /data/artifacts/{SESSION_ID}/attachments/"
    assert lines[4].startswith("1. login&#64;2x.png (image 1 of 2, inline): @")
    assert lines[5].startswith("2. spec.pdf (PDF 1 of 1, inline): @")
    assert lines[6] == "3. capture (1).mp4 (video 1 of 1, file)"
    assert lines[7].startswith("4. notes &lt;draft&gt;.md (text 1 of 1, inline): @")
    assert lines[8].startswith("5. after.png (image 2 of 2, inline): @")

    paths = [Path(path) if path else None for path in parsed.hybrid_paths]
    assert paths[2] is None
    inline_paths = [path for path in paths if path is not None]
    assert [path.parent for path in inline_paths] == [hybrid_dir] * 4
    assert all(ATT_RE.fullmatch(path.name) for path in inline_paths)
    assert [path.suffix for path in inline_paths] == [".png", ".pdf", ".txt", ".webp"]
    assert inline_paths[0].read_bytes() == PNG
    assert inline_paths[1].read_bytes() == PDF
    materialized_text_file = inline_paths[2]
    assert materialized_text_file.read_text() == NATIVE_TEXT
    assert inline_paths[3].read_bytes() == PNG_2
    assert _att_files(hybrid_dir) == sorted(inline_paths)
    assert block == build_manifest(content.manifest, hybrid_paths=parsed.hybrid_paths)


def test_without_user_text_the_paste_is_the_block_alone(hybrid_dir, tmux, no_fold):
    content = _content("")
    asyncio.run(_agent().send("", content=content))

    paste = tmux.pastes[0]
    assert paste.startswith("<twicc:attachments>\n")
    assert paste.endswith("\n</twicc:attachments>")
    assert parse_manifest(paste).hybrid is True


def test_the_raw_user_text_is_pasted_unchanged(hybrid_dir, tmux, no_fold):
    raw = "  keep <twicc:context>x</twicc:context> as is  "
    asyncio.run(_agent().send(raw, content=_content(raw)))

    assert tmux.pastes[0].startswith(raw + "\n\n<twicc:attachments>\n")


def test_a_file_only_manifest_carries_no_reference(hybrid_dir, tmux, no_fold):
    asyncio.run(_agent().send("see file", content=_file_only_content()))

    assert tmux.pastes == [(
        "see file\n\n<twicc:attachments>\n"
        "Files the user attached to this message, in the order they attached them.\n"
        f"file = /data/artifacts/{SESSION_ID}/attachments/\n"
        "1. capture.mp4 (video 1 of 1, file)\n"
        "</twicc:attachments>"
    )]
    assert _att_files(hybrid_dir) == []


def test_mismatched_native_parts_fail_before_any_write(hybrid_dir, tmux):
    content = _content()._replace(native_parts=(NativePart("image", "image/png", PNG),))
    with pytest.raises(ValueError):
        asyncio.run(_agent().send("user text", content=content))
    assert tmux.pastes == []
    assert _att_files(hybrid_dir) == []


def test_legacy_send_keeps_its_mention_prefix(hybrid_dir, tmux):
    """The legacy path (and its known text bug, spec D18) is untouched."""
    import base64

    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}}
    asyncio.run(_agent().send("legacy", images=[image]))

    [path] = _att_files(hybrid_dir)
    assert tmux.pastes == [f"@{path}\nlegacy"]
    assert path.read_bytes() == PNG


def test_legacy_slash_text_without_content_is_still_pasted(hybrid_dir, tmux):
    asyncio.run(_agent().send("/model sonnet"))
    assert tmux.pastes == ["/model sonnet"]


# ----------------------------------------------------------------------
# Command refusal (synchronous)
# ----------------------------------------------------------------------


COMMANDS = ["/model sonnet", "   /compact", "\n\t/foo bar", "!ls -la", "  !git status"]


@pytest.mark.parametrize("text", COMMANDS)
def test_send_refuses_a_command_with_content_before_writing_or_pasting(hybrid_dir, tmux, text):
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(_agent().send(text, content=_content(text)))

    assert caught.value.code == "attachments_with_command"
    assert tmux.pastes == []
    assert _att_files(hybrid_dir) == []


@pytest.mark.parametrize("text", COMMANDS)
def test_start_refuses_a_command_with_content_before_launching(hybrid_dir, tmux, monkeypatch, text):
    agent = _agent()
    launched = MagicMock()
    monkeypatch.setattr(agent, "_resolve_and_create_work_dirs", launched)

    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(agent.start(text, AsyncMock(), resume=True, content=_content(text), on_delivered=MagicMock()))

    assert caught.value.code == "attachments_with_command"
    assert agent._first_paste_task is None
    assert agent._liveness_task is None
    assert tmux.created == []
    assert tmux.pastes == []
    launched.assert_not_called()
    assert _att_files(hybrid_dir) == []


@pytest.mark.parametrize("text", ["/model", "!ls", "  /compact"])
def test_planner_refuses_hybrid_commands_before_loading(text):
    target = PlanTarget("claude_code", True, False, "opus", False, "first_party")
    missing = AttachmentRef("b1", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(planner.AttachmentPlanError) as caught:
        planner.plan_attachments((missing,), target, text=text)
    assert caught.value.code == "attachments_with_command"


def test_hybrid_command_detection():
    assert planner.is_hybrid_command("/model") is True
    assert planner.is_hybrid_command(" \n!ls") is True
    assert planner.is_hybrid_command("see a/b and !this") is False
    assert planner.is_hybrid_command("") is False


def test_sdk_bang_text_with_attachments_is_not_a_command():
    target = PlanTarget("claude_code", False, False, "opus", False, "first_party")
    assert planner._is_refused_command(target, "!important") is False


# ----------------------------------------------------------------------
# Data dir with whitespace
# ----------------------------------------------------------------------


def test_send_refuses_inline_content_when_the_hybrid_dir_has_whitespace(tmp_path, tmux, monkeypatch):
    hybrid_dir = _patch_hybrid_dir(tmp_path / "data dir", monkeypatch)

    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(_agent().send("user text", content=_content()))

    assert caught.value.code == "attachment_commit_failed"
    assert "whitespace" in str(caught.value)
    assert tmux.pastes == []
    assert _att_files(hybrid_dir) == []


def test_start_refuses_inline_content_when_the_hybrid_dir_has_whitespace(tmp_path, tmux, monkeypatch):
    _patch_hybrid_dir(tmp_path / "data dir", monkeypatch)
    agent = _agent()

    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(agent.start("user text", AsyncMock(), resume=True, content=_content()))

    assert caught.value.code == "attachment_commit_failed"
    assert agent._first_paste_task is None
    assert tmux.created == []


def test_a_file_only_manifest_is_accepted_with_a_whitespace_hybrid_dir(tmp_path, tmux, monkeypatch):
    _patch_hybrid_dir(tmp_path / "data dir", monkeypatch)
    asyncio.run(_agent().send("see file", content=_file_only_content()))
    assert len(tmux.pastes) == 1


# ----------------------------------------------------------------------
# Start, first paste and on_delivered
# ----------------------------------------------------------------------


@pytest.fixture
def launchable(hybrid_dir, tmux, monkeypatch):
    """Stub the launch preamble of ``start`` (trust, addendum, title, argv, watcher, liveness)."""
    from twicc.core.services import trust
    from twicc.providers.claude_code import sessions_watcher

    monkeypatch.setattr(trust, "project_is_untrusted", lambda project_id: False)
    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: SimpleNamespace(request_fast_poll=lambda: None))
    monkeypatch.setattr(hybrid_module, "write_addendum_file", lambda session_id, addendum: None)
    monkeypatch.setattr(hybrid_module, "build_argv", lambda **kwargs: ["claude"])
    monkeypatch.setattr(HybridClaudeAgent, "_read_system_prompt_addendum", lambda self: None)
    monkeypatch.setattr(HybridClaudeAgent, "_resolve_temp_title", AsyncMock(return_value="title"))
    monkeypatch.setattr(HybridClaudeAgent, "_resolve_and_create_work_dirs", AsyncMock(return_value=[]))
    monkeypatch.setattr(HybridClaudeAgent, "_start_liveness_monitor", lambda self: None)
    monkeypatch.setattr(HybridClaudeAgent, "READY_POLL_INTERVAL", 0.0)
    return tmux


def test_start_pastes_the_manifest_then_calls_on_delivered_once(launchable, hybrid_dir, no_fold):
    calls: list[str] = []

    async def run() -> HybridClaudeAgent:
        agent = _agent()
        await agent.start(
            "user text", AsyncMock(), resume=True, content=_content(), on_delivered=lambda: calls.append("x"),
        )
        assert launchable.created == [SESSION_ID]
        await agent._first_paste_task
        return agent

    agent = asyncio.run(run())
    first_paste_callback_calls = len(calls)
    assert first_paste_callback_calls == 1
    assert len(launchable.pastes) == 1
    assert launchable.pastes[0].startswith("user text\n\n<twicc:attachments>\n")
    assert "inline): @" in launchable.pastes[0]
    assert len(_att_files(hybrid_dir)) == 4
    assert agent.state == AgentState.ASSISTANT_TURN


def test_start_with_legacy_text_keeps_its_paste(launchable):
    async def run() -> None:
        agent = _agent()
        await agent.start("plain", AsyncMock(), resume=True)
        await agent._first_paste_task

    asyncio.run(run())
    assert launchable.pastes == ["plain"]


def test_on_delivered_is_not_called_when_the_paste_fails(launchable, hybrid_dir):
    launchable.fail_paste = True
    callback = MagicMock()

    async def run() -> HybridClaudeAgent:
        agent = _agent()
        await agent.start("user text", AsyncMock(), resume=True, content=_content(), on_delivered=callback)
        await agent._first_paste_task
        return agent

    agent = asyncio.run(run())
    callback.assert_not_called()
    assert agent.state == AgentState.DEAD
    assert agent.kill_reason == "startup-failed"


def test_on_delivered_is_not_called_after_a_ready_timeout(launchable, monkeypatch):
    launchable.ready = False
    monkeypatch.setattr(HybridClaudeAgent, "READY_TIMEOUT", 0.0)
    callback = MagicMock()

    async def run() -> None:
        agent = _agent()
        await agent.start("user text", AsyncMock(), resume=True, content=_content(), on_delivered=callback)
        await agent._first_paste_task

    asyncio.run(run())
    assert len(launchable.pastes) == 1  # pasted anyway, delivery unconfirmed
    callback.assert_not_called()


def test_on_delivered_is_not_called_when_the_first_paste_is_cancelled(launchable, monkeypatch):
    launchable.ready = False
    monkeypatch.setattr(HybridClaudeAgent, "READY_POLL_INTERVAL", 0.01)
    callback = MagicMock()

    async def run() -> None:
        agent = _agent()
        await agent.start("user text", AsyncMock(), resume=True, content=_content(), on_delivered=callback)
        await asyncio.sleep(0.05)
        agent._first_paste_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await agent._first_paste_task

    asyncio.run(run())
    assert launchable.pastes == []
    callback.assert_not_called()


def test_on_delivered_is_not_called_on_adoption(tmux, monkeypatch):
    monkeypatch.setattr(HybridClaudeAgent, "_start_liveness_monitor", lambda self: None)
    monkeypatch.setattr(hybrid_tmux, "read_process_environ", lambda pid: None)
    callback = MagicMock()

    async def run() -> HybridClaudeAgent:
        agent = _agent()
        await agent.start("", AsyncMock(), resume=True, adopt=True, on_delivered=callback)
        return agent

    agent = asyncio.run(run())
    assert agent._first_paste_task is None
    assert tmux.pastes == []
    callback.assert_not_called()


def test_a_failing_on_delivered_does_not_kill_the_agent(launchable):
    def explode() -> None:
        raise RuntimeError("release failed")

    async def run() -> HybridClaudeAgent:
        agent = _agent()
        await agent.start("user text", AsyncMock(), resume=True, content=_content(), on_delivered=explode)
        await agent._first_paste_task
        return agent

    agent = asyncio.run(run())
    assert agent.state == AgentState.ASSISTANT_TURN


def test_killing_a_never_launched_agent_skips_the_graceful_exit(tmux, monkeypatch):
    """A synchronous start refusal is torn down at once, not after a 30 s ``/exit`` wait."""
    agent = _agent()
    graceful = AsyncMock(side_effect=AssertionError("no CLI to exit"))
    monkeypatch.setattr(agent, "_graceful_cli_exit", graceful)
    monkeypatch.setattr(hybrid_tmux, "pane_status", lambda session_id: (None, False))

    asyncio.run(agent.kill("startup-failed"))

    graceful.assert_not_awaited()
    assert agent.state == AgentState.DEAD


# ----------------------------------------------------------------------
# Switch to hybrid (asgi) and the plan target of an existing session
# ----------------------------------------------------------------------


def test_asgi_uses_the_shared_plan_target_resolver():
    assert asgi.resolve_existing_session_plan_target is target_module.resolve_existing_session_plan_target


def _switch_consumer(monkeypatch):
    from twicc.core.models import SessionType

    session = SimpleNamespace(hybrid=False, provider="claude_code", type=SessionType.SESSION, hidden=False)
    monkeypatch.setattr(asgi.settings, "CLAUDE_HYBRID_ENABLED", True, raising=False)
    monkeypatch.setattr(asgi, "sync_to_async", lambda fn: AsyncMock(return_value=session))
    lane_refs: list[tuple[str, int]] = []

    async def kill_agent(session_id, reason):
        lane_refs.append(("kill", send_lanes._reference_count(session_id)))

    async def write(apply):
        lane_refs.append(("write", send_lanes._reference_count(SESSION_ID)))

    manager = SimpleNamespace(kill_agent=AsyncMock(side_effect=kill_agent))
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(get=lambda provider: manager))
    monkeypatch.setattr(asgi, "run_under_db_write_lock", write)
    from twicc.core import serializers

    monkeypatch.setattr(serializers, "serialize_session", lambda session: {"id": SESSION_ID})
    consumer = asgi.WSConsumer()
    consumer.send_json = AsyncMock()
    consumer.channel_layer = SimpleNamespace(group_send=AsyncMock())
    spawned: list = []
    monkeypatch.setattr(asgi, "_spawn_detached", lambda coro, *, label: spawned.append(coro))
    return consumer, spawned, manager, lane_refs


def test_the_switch_kills_then_writes_the_flag_inside_the_send_lane(monkeypatch):
    send_lanes._reset_for_tests()
    consumer, spawned, manager, lane_refs = _switch_consumer(monkeypatch)

    asyncio.run(consumer._handle_set_session_hybrid({"session_id": SESSION_ID}))
    [coro] = spawned
    asyncio.run(coro)

    manager.kill_agent.assert_awaited_once_with(SESSION_ID, reason="switch-hybrid")
    assert lane_refs == [("kill", 1), ("write", 1)]
    assert send_lanes._live_keys() == set()


def test_session_hybrid_is_the_database_flag(monkeypatch):
    monkeypatch.setattr(target_module, "_read_session_hybrid", AsyncMock(return_value=True))
    assert asyncio.run(target_module.resolve_session_hybrid(SESSION_ID)) is True

    monkeypatch.setattr(target_module, "_read_session_hybrid", AsyncMock(return_value=False))
    assert asyncio.run(target_module.resolve_session_hybrid(SESSION_ID)) is False


def test_existing_session_target_takes_hybrid_from_the_database_flag(monkeypatch):
    resolve = AsyncMock(return_value="target")
    monkeypatch.setattr(target_module, "resolve_plan_target", resolve)
    monkeypatch.setattr(target_module, "_read_session_hybrid", AsyncMock(return_value=True))
    settings = AgentSettings(selected_model="opus")
    live = SimpleNamespace(agent_settings=settings)

    result = asyncio.run(target_module.resolve_existing_session_plan_target(
        session_id=SESSION_ID, provider="claude_code", effective_settings=settings,
        directory="/project", ephemeral=False, live_agent=live,
    ))

    assert result == "target"
    resolve.assert_awaited_once_with(
        provider="claude_code", effective_settings=settings, directory="/project",
        hybrid=True, ephemeral=False, live_agent=live,
    )
