"""Codex thread-level configuration bound at thread_start / thread_resume."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from twicc.providers.codex.agent import manager as manager_module
from twicc.providers.codex.agent.manager import (
    CodexAgentManager,
    _apply_auto_review_network,
    _apply_codex_work_dirs,
    _apply_request_user_input,
    _apply_update_plan,
)
from twicc.providers.helpers import AgentSettings


class _FakeCodex:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []
        self.started_thread: _FakeThread | None = None

    async def thread_start_with_policy(self, **kwargs):
        self.start_calls.append(deepcopy(kwargs))
        self.started_thread = _FakeThread("canonical-id")
        return self.started_thread

    async def thread_resume_with_policy(self, thread_id, **kwargs):
        self.resume_calls.append((thread_id, deepcopy(kwargs)))
        return SimpleNamespace(id=thread_id)

    async def close(self) -> None:
        pass


class _FakeAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.seeded_pending_id: str | None = None
        self.context_reset = False

    async def _seed_context_baseline(self, *, pending_id: str) -> None:
        self.seeded_pending_id = pending_id

    def _reset_context_baseline(self) -> None:
        self.context_reset = True


class _FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.id = thread_id
        self.settings_updates: list[dict] = []

    async def update_settings_with_policy(self, **kwargs) -> None:
        self.settings_updates.append(deepcopy(kwargs))


def _install_factory_fakes(monkeypatch, work_dirs: list[str]):
    codex = _FakeCodex()
    resolved: list[tuple[str, str | None]] = []

    async def fake_make_codex_config(*, cwd):
        return {"cwd": cwd}

    async def fake_resolve(session_id, *, pending_id=None):
        resolved.append((session_id, pending_id))
        return work_dirs

    monkeypatch.setattr(manager_module, "make_codex_config", fake_make_codex_config)
    monkeypatch.setattr(manager_module, "TwiccAsyncCodex", lambda *, config: codex)
    monkeypatch.setattr(manager_module, "attach_stderr_logging", lambda *args: None)
    monkeypatch.setattr(manager_module, "resolve_and_create_work_dirs", fake_resolve)
    monkeypatch.setattr(manager_module, "CodexAgent", _FakeAgent)
    monkeypatch.setattr(manager_module, "inject_context", lambda *args, **kwargs: None)

    from twicc import mcp
    from twicc.core.services import trust
    from twicc.mcp import identity

    monkeypatch.setattr(mcp, "mcp_enabled", lambda: False)
    monkeypatch.setattr(trust, "project_is_untrusted", lambda project_id: False)
    monkeypatch.setattr(identity, "register_draft_alias", lambda *args: None)
    return codex, resolved


def test_new_thread_updates_canonical_work_dirs_without_resume(monkeypatch) -> None:
    roots = ["/data/artifacts/canonical-id", "/data/scratch/canonical-id"]
    codex, resolved = _install_factory_fakes(monkeypatch, roots)

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "draft-id",
            "project-id",
            "/project",
            resume=False,
            settings=AgentSettings(permission_mode="auto_review"),
        )

    agent = asyncio.run(scenario())

    assert resolved == [("canonical-id", "draft-id")]
    assert len(codex.start_calls) == 1
    start_config = codex.start_calls[0]["config"]
    assert start_config["sandbox_workspace_write"]["network_access"] is True
    assert "network_proxy" not in start_config["features"]
    assert codex.resume_calls == []
    assert codex.started_thread is not None
    assert len(codex.started_thread.settings_updates) == 1
    sandbox_policy = codex.started_thread.settings_updates[0]["sandbox_policy"]
    assert sandbox_policy.model_dump(mode="json", by_alias=True)["writableRoots"] == roots
    assert sandbox_policy.root.network_access is True
    assert agent.kwargs["work_dirs"] == roots
    assert agent.seeded_pending_id == "draft-id"


def test_new_fast_thread_forwards_priority_service_tier(monkeypatch) -> None:
    codex, _ = _install_factory_fakes(monkeypatch, [])

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "draft-id",
            "project-id",
            "/project",
            resume=False,
            settings=AgentSettings(permission_mode="yolo", fast_mode=True),
        )

    asyncio.run(scenario())

    assert codex.start_calls[0]["service_tier"] == "priority"


def test_new_yolo_thread_does_not_resume_before_first_turn(monkeypatch) -> None:
    roots = ["/data/artifacts/canonical-id", "/data/scratch/canonical-id"]
    codex, resolved = _install_factory_fakes(monkeypatch, roots)

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "draft-id",
            "project-id",
            "/project",
            resume=False,
            settings=AgentSettings(permission_mode="yolo"),
        )

    agent = asyncio.run(scenario())

    assert resolved == [("canonical-id", "draft-id")]
    assert len(codex.start_calls) == 1
    assert codex.resume_calls == []
    assert codex.started_thread is not None
    assert codex.started_thread.settings_updates == []
    assert agent.kwargs["work_dirs"] == roots


def test_existing_thread_gets_work_dirs_in_first_resume(monkeypatch) -> None:
    roots = ["/data/artifacts/thread-id", "/data/scratch/thread-id"]
    codex, resolved = _install_factory_fakes(monkeypatch, roots)

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "thread-id",
            "project-id",
            "/project",
            resume=True,
            settings=AgentSettings(permission_mode="auto_review"),
        )

    agent = asyncio.run(scenario())

    assert resolved == [("thread-id", None)]
    assert codex.start_calls == []
    assert len(codex.resume_calls) == 1
    _, resume = codex.resume_calls[0]
    assert resume["config"]["sandbox_workspace_write"]["writable_roots"] == roots
    assert "network_proxy" not in resume["config"]["features"]
    assert agent.kwargs["work_dirs"] == roots
    assert agent.context_reset is True


def test_resumed_standard_thread_forwards_default_service_tier(monkeypatch) -> None:
    codex, _ = _install_factory_fakes(monkeypatch, [])

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "thread-id",
            "project-id",
            "/project",
            resume=True,
            settings=AgentSettings(permission_mode="yolo", fast_mode=False),
        )

    asyncio.run(scenario())

    assert codex.resume_calls[0][1]["service_tier"] == "default"


def test_work_dir_config_preserves_existing_workspace_settings() -> None:
    config = {"sandbox_workspace_write": {"network_access": True}}
    _apply_codex_work_dirs(config, ["/scratch/session"])

    assert config["sandbox_workspace_write"] == {
        "network_access": True,
        "writable_roots": ["/scratch/session"],
    }


def test_auto_review_network_preserves_existing_features_and_roots() -> None:
    config = {
        "features": {"default_mode_request_user_input": True},
        "sandbox_workspace_write": {"writable_roots": ["/scratch/session"]},
    }
    _apply_auto_review_network(config)

    assert config["features"] == {"default_mode_request_user_input": True}
    assert config["sandbox_workspace_write"] == {
        "network_access": True,
        "writable_roots": ["/scratch/session"],
    }
    assert "suppress_unstable_features_warning" not in config


def test_request_user_input_enabled_forces_the_default_mode_feature() -> None:
    config: dict = {}
    _apply_request_user_input(config, enabled=True)

    assert config["features"]["default_mode_request_user_input"] is True
    assert config["suppress_unstable_features_warning"] is True
    assert "tools" not in config


def test_update_plan_is_forced_on_for_every_thread() -> None:
    """Codex 0.151 made ``update_plan`` opt-in; the Tasks tab needs it."""
    config: dict = {"tools": {"experimental_request_user_input": {"enabled": False}}}
    _apply_update_plan(config)

    assert config["tools"]["update_plan"] == {"enabled": True}
    # Sibling entries of the ``tools`` table are left alone.
    assert config["tools"]["experimental_request_user_input"] == {"enabled": False}


def test_request_user_input_disabled_drops_the_tool_and_the_feature() -> None:
    config: dict = {}
    _apply_request_user_input(config, enabled=False)

    assert config["features"]["default_mode_request_user_input"] is False
    assert config["tools"]["experimental_request_user_input"] == {"enabled": False}


def test_new_thread_without_question_widget_disables_request_user_input(monkeypatch) -> None:
    codex, _ = _install_factory_fakes(monkeypatch, [])

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "draft-id",
            "project-id",
            "/project",
            resume=False,
            settings=AgentSettings(permission_mode="yolo", question_widget=False),
        )

    asyncio.run(scenario())

    config = codex.start_calls[0]["config"]
    assert config["features"]["default_mode_request_user_input"] is False
    assert config["tools"]["experimental_request_user_input"] == {"enabled": False}


def test_resumed_thread_keeps_request_user_input_when_widget_unset(monkeypatch) -> None:
    codex, _ = _install_factory_fakes(monkeypatch, [])

    async def scenario():
        manager = CodexAgentManager()
        return await manager._create_agent(
            "thread-id",
            "project-id",
            "/project",
            resume=True,
            settings=AgentSettings(permission_mode="yolo"),
        )

    asyncio.run(scenario())

    config = codex.resume_calls[0][1]["config"]
    assert config["features"]["default_mode_request_user_input"] is True
    # ``update_plan`` is always forced on; ``request_user_input`` stays untouched.
    assert config["tools"] == {"update_plan": {"enabled": True}}
