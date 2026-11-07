import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_session


def _run(coro):
    return asyncio.run(coro)


def _make_session(*, muted=False):
    project = Project.objects.create(id="-mute-api", directory="/tmp/mute-api")
    return Session.objects.create(
        id="mute-api-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        mute_on_user_turn=muted,
    )


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    """The global DB writer only starts at app boot; run write factories directly."""
    async def _passthrough(coro_factory):
        return await coro_factory()

    monkeypatch.setattr(
        "twicc.core.services.session_update.run_under_db_write_lock",
        _passthrough,
    )


@pytest.mark.django_db
def test_session_defaults_to_unmuted_and_serializes_the_flag():
    session = _make_session()
    assert session.mute_on_user_turn is False
    assert serialize_session(session)["mute_on_user_turn"] is False


@pytest.mark.django_db(transaction=True)
def test_patch_rejects_a_non_boolean_mute_value():
    session = _make_session()
    response = _run(AsyncClient().patch(
        f"/api/projects/{session.project_id}/sessions/{session.id}/",
        data=orjson.dumps({"mute_on_user_turn": "true"}),
        content_type="application/json",
    ))
    assert response.status_code == 400
    assert response.json() == {
        "error": "mute_on_user_turn must be a boolean",
        "ui_message": {
            "key": "backend.request.boolean_required",
            "params": {"field": "mute_on_user_turn"},
        },
    }


@pytest.mark.django_db(transaction=True)
def test_patch_persists_and_broadcasts_mute_on_user_turn():
    session = _make_session()
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.views.get_channel_layer", return_value=layer):
        response = _run(AsyncClient().patch(
            f"/api/projects/{session.project_id}/sessions/{session.id}/",
            data=orjson.dumps({"mute_on_user_turn": True}),
            content_type="application/json",
        ))

    assert response.status_code == 200
    assert response.json()["mute_on_user_turn"] is True
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    payload = layer.group_send.await_args.args[1]["data"]
    assert payload["type"] == "session_updated"
    assert payload["session"]["mute_on_user_turn"] is True
