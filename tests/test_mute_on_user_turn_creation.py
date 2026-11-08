import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.agent.base_manager import BaseAgentManager
from twicc.core.enums import Provider
from twicc.core.models import Project, SessionType
from twicc.core.services.session_creation import create_session_from_payload
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import (
    get_pending_session_attributes,
    pop_pending_session_attributes,
    set_pending_session_attributes,
)
from twicc.providers.helpers import AgentSettings
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


class _Manager(BaseAgentManager):
    provider = Provider.CODEX

    async def _create_agent(self, session_id, project_id, cwd, **kwargs):
        return SimpleNamespace(
            session_id="canonical-id",
            interrupt_or_kill=AsyncMock(),
        )


_MISSING = object()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("payload_value", "expected"),
    [
        pytest.param(True, True, id="literal-true"),
        pytest.param(_MISSING, False, id="missing"),
        pytest.param(False, False, id="literal-false"),
        pytest.param("true", False, id="string-true"),
        pytest.param(1, False, id="numeric-one"),
        pytest.param({"enabled": True}, False, id="truthy-object"),
        pytest.param([True], False, id="truthy-list"),
    ],
)
def test_creation_service_puts_only_literal_true_mute_in_the_pending_buffer(
    tmp_path, payload_value, expected
):
    project = Project.objects.create(
        id="-mute-create", directory=str(tmp_path)
    )
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft-id"))
    registry = SimpleNamespace(get=lambda provider: manager)
    payload = {
        "session_id": "draft-id",
        "project_id": project.id,
        "provider": Provider.CODEX.value,
        "text": "Work",
        "layout": {},
    }
    if payload_value is not _MISSING:
        payload["mute_on_user_turn"] = payload_value

    try:
        with patch(
            "twicc.core.services.session_creation.ensure_provider_running"
        ), patch(
            "twicc.agent.registry.get_agent_manager_registry",
            return_value=registry,
        ):
            result = asyncio.run(create_session_from_payload(payload))
        assert result.success is True
        assert get_pending_session_attributes("draft-id").mute_on_user_turn is expected
    finally:
        pop_pending_agent_settings("draft-id")
        pop_pending_session_attributes("draft-id")


@pytest.mark.django_db
def test_watcher_copies_mute_and_discovered_sessions_default_to_unmuted(tmp_path):
    project = Project.objects.create(
        id="-mute-watch", directory=str(tmp_path)
    )
    watcher = _Watcher()
    set_pending_session_attributes("created-id", mute_on_user_turn=True)
    created = watcher.create_session_sync(ParsedSessionFile(
        project.id, "created-id", SessionType.SESSION, "created.jsonl"
    ), project)
    discovered = watcher.create_session_sync(ParsedSessionFile(
        project.id, "discovered-id", SessionType.SESSION, "discovered.jsonl"
    ), project)

    assert created.mute_on_user_turn is True
    assert discovered.mute_on_user_turn is False


def test_canonical_id_rekey_preserves_mute_on_user_turn():
    manager = _Manager()
    manager.notify_session_bound = AsyncMock()
    manager._register_and_start = AsyncMock()
    set_pending_session_attributes("draft-id", mute_on_user_turn=True)

    try:
        canonical_id = asyncio.run(manager._start_agent(
            "draft-id",
            "-mute-create",
            "/tmp",
            "Work",
            False,
            settings=AgentSettings(),
        ))
        pending = get_pending_session_attributes("canonical-id")
        assert canonical_id == "canonical-id"
        assert pending.mute_on_user_turn is True
    finally:
        pop_pending_session_attributes("draft-id")
        pop_pending_session_attributes("canonical-id")
