import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import SESSION_LISTING_FIELDS, serialize_session, slim_session
from twicc.core.services.session_creation import create_session_from_payload
from twicc.pending_agent_settings import pop_pending_agent_settings
from twicc.pending_session_attributes import (
    get_pending_session_attributes,
    pop_pending_session_attributes,
    set_pending_session_attributes,
)
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


@pytest.fixture
def project(tmp_path):
    return Project.objects.create(id="-tree-detached-create", directory=str(tmp_path))


def test_serializer_exposes_tree_detached(project):
    session = Session.objects.create(
        id="s1", file_path="s1.jsonl", project=project, provider=Provider.CODEX, tree_detached=True,
    )
    assert serialize_session(session)["tree_detached"] is True


@pytest.mark.parametrize("value", [True, False])
def test_slim_session_keeps_tree_detached(project, value):
    assert "tree_detached" in SESSION_LISTING_FIELDS
    session = Session.objects.create(
        id="s2", file_path="s2.jsonl", project=project, provider=Provider.CODEX, tree_detached=value,
    )
    assert slim_session(serialize_session(session))["tree_detached"] is value


@pytest.mark.parametrize(
    ("payload_extra", "expected"),
    [({"tree_detached": True}, True), ({}, False), ({"tree_detached": "true"}, False)],
)
def test_creation_payload_sets_pending_attribute(project, payload_extra, expected):
    manager = SimpleNamespace(create_session=AsyncMock(return_value="draft-td"))
    registry = SimpleNamespace(get=lambda provider: manager)
    payload = {
        "session_id": "draft-td",
        "project_id": project.id,
        "provider": Provider.CODEX.value,
        "text": "Work",
        "layout": {},
        **payload_extra,
    }
    try:
        with patch("twicc.core.services.session_creation.ensure_provider_running"), patch(
            "twicc.agent.registry.get_agent_manager_registry", return_value=registry,
        ):
            result = asyncio.run(create_session_from_payload(payload))
        assert result.success is True
        assert get_pending_session_attributes("draft-td").tree_detached is expected
    finally:
        pop_pending_agent_settings("draft-td")
        pop_pending_session_attributes("draft-td")


def test_watcher_persists_pending_flag_on_the_row(project):
    set_pending_session_attributes("det", tree_detached=True)
    try:
        created = _Watcher().create_session_sync(
            ParsedSessionFile(project.id, "det", SessionType.SESSION, "det.jsonl"), project,
        )
    finally:
        pop_pending_session_attributes("det")
    assert created.tree_detached is True
    assert kinds("det") == ["session.created", "tree_detached.started"]
