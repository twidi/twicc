"""Update path of the ``tree_detached`` flag: service, drop request, REST PATCH."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_update import (
    apply_session_tree_detached_change,
    update_session_tree_detached_from_payload,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()

    monkeypatch.setattr("twicc.core.services.session_update.run_under_db_write_lock", _passthrough)


def _make_session(*, hidden=False, tree_detached=False):
    project, _ = Project.objects.get_or_create(id="-td-update", defaults={"directory": "/tmp/td-update"})
    return Session.objects.create(
        id="td-update-session", project=project, provider=Provider.CODEX.value,
        type=SessionType.SESSION, hidden=hidden, tree_detached=tree_detached,
    )


def _kinds(session):
    return list(
        TimelineEvent.objects.filter(session_id=session.id, kind__startswith="tree_detached.")
        .order_by("id").values_list("kind", flat=True)
    )


def test_change_saves_and_records_started():
    session = _make_session()
    asyncio.run(apply_session_tree_detached_change(session, True))
    session.refresh_from_db()
    assert session.tree_detached is True
    assert _kinds(session) == ["tree_detached.started"]


def test_change_back_records_ended():
    session = _make_session(tree_detached=True)
    asyncio.run(apply_session_tree_detached_change(session, False))
    session.refresh_from_db()
    assert session.tree_detached is False
    assert _kinds(session) == ["tree_detached.ended"]


def test_noop_change_records_nothing():
    session = _make_session()
    asyncio.run(apply_session_tree_detached_change(session, False))
    assert _kinds(session) == []
    session = Session.objects.get(id=session.id)
    session.tree_detached = True
    session.save(update_fields=["tree_detached"])
    asyncio.run(apply_session_tree_detached_change(session, True))
    assert _kinds(session) == []


def test_payload_requires_boolean():
    session = _make_session()
    missing = asyncio.run(update_session_tree_detached_from_payload({"session_id": session.id}))
    assert not missing.success
    assert (missing.errors[0].field, missing.errors[0].code) == ("tree_detached", "missing")
    invalid = asyncio.run(update_session_tree_detached_from_payload(
        {"session_id": session.id, "tree_detached": "true"}))
    assert not invalid.success
    assert invalid.errors[0].code == "invalid_tree_detached"
    no_id = asyncio.run(update_session_tree_detached_from_payload({"tree_detached": True}))
    assert not no_id.success
    assert (no_id.errors[0].field, no_id.errors[0].code) == ("session_id", "missing")
    session.refresh_from_db()
    assert session.tree_detached is False


def test_unknown_session_is_rejected():
    result = asyncio.run(update_session_tree_detached_from_payload(
        {"session_id": "nope", "tree_detached": True}))
    assert not result.success
    assert (result.errors[0].field, result.errors[0].code) == ("session_id", "session_not_found")


def _run_payload(session):
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.core.services.session_update.get_channel_layer", return_value=layer), \
            patch("twicc.core.services.session_update.ensure_provider_running"):
        result = asyncio.run(update_session_tree_detached_from_payload(
            {"session_id": session.id, "tree_detached": True}))
    assert result.success
    return layer


def test_broadcast_skipped_for_hidden_session():
    session = _make_session(hidden=True)
    layer = _run_payload(session)
    assert layer.group_send.await_count == 0
    session.refresh_from_db()
    assert session.tree_detached is True


def test_broadcast_sent_for_visible_session():
    session = _make_session()
    layer = _run_payload(session)
    data = layer.group_send.await_args.args[1]["data"]
    assert data["type"] == "session_updated"
    assert data["session"]["tree_detached"] is True


def _patch(session, body):
    return asyncio.run(AsyncClient().patch(
        f"/api/projects/{session.project_id}/sessions/{session.id}/",
        data=orjson.dumps(body), content_type="application/json",
    ))


def test_rest_patch_accepts_boolean():
    session = _make_session()
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.views.get_channel_layer", return_value=layer):
        response = _patch(session, {"tree_detached": True})
    assert response.status_code == 200
    assert response.json()["tree_detached"] is True
    session.refresh_from_db()
    assert session.tree_detached is True
    assert _kinds(session) == ["tree_detached.started"]
    assert layer.group_send.await_args.args[1]["data"]["session"]["tree_detached"] is True


def test_rest_patch_rejects_non_boolean_with_400():
    session = _make_session()
    response = _patch(session, {"tree_detached": "true"})
    assert response.status_code == 400
    assert response.json() == {
        "error": "tree_detached must be a boolean",
        "ui_message": {
            "key": "backend.request.boolean_required",
            "params": {"field": "tree_detached"},
        },
    }
    session.refresh_from_db()
    assert session.tree_detached is False
