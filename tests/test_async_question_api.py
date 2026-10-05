"""Authenticated hydration and committed question updates for every browser."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.db import transaction
from django.urls import Resolver404, resolve

from twicc.auth.session_auth import bind_session
from twicc.core.enums import Provider
from twicc.core.models import AsyncQuestionState, Project, Session, SessionItem, SessionType
from twicc.core.services import async_questions as service
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.async_questions import QuestionFact

pytestmark = pytest.mark.django_db(transaction=True)


def question(item_id="q1", turn_id="t1"):
    return QuestionFact(
        f"question:{item_id}", "question", "2026-10-05T10:00:01Z", turn_id, item_id, 1,
        {"questions": [{"index": 0, "title": "Keep the menu?", "options": ["Yes", "No"]}]},
    )


def end(turn_id="t1"):
    return QuestionFact(f"end:{turn_id}", "turn_end", "2026-10-05T10:00:02Z", turn_id, None, 2, {})


def question_url(session):
    return f"/api/projects/{session.project_id}/sessions/{session.id}/async-questions/"


@pytest.fixture
def session():
    return Session.objects.create(
        id="api-session", project=Project.objects.create(id="api-project", directory="/tmp"),
        provider=Provider.CODEX, file_path="api.jsonl",
    )


@pytest.fixture
def ready_session(session):
    service.merge_question_facts(session.id, [question(), end()])
    return session


@pytest.fixture
def authenticated_client(client, settings):
    settings.TWICC_PASSWORD_HASH = "test-password-hash"
    settings.TWICC_DEV_LOCAL_BYPASS = False
    auth = client.session
    bind_session(auth, settings.TWICC_PASSWORD_HASH)
    auth.save()
    return client


@pytest.fixture
def layer(monkeypatch):
    layer = SimpleNamespace(group_send=AsyncMock())
    monkeypatch.setattr("twicc.providers.codex.question_snapshots.get_channel_layer", lambda: layer)
    monkeypatch.setattr("twicc.core.services.session_update.get_channel_layer", lambda: layer)
    return layer


def updates(layer):
    return [call.args[1]["data"] for call in layer.group_send.await_args_list
            if call.args[1]["data"]["type"] == "async_questions_updated"]


def run_with_writer(callback):
    from twicc.providers import db_writer

    async def run():
        db_writer.start_db_writer()
        try:
            return await callback()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(run())


def test_snapshot_requires_existing_authentication(client, session, settings):
    settings.TWICC_PASSWORD_HASH = "test-password-hash"
    settings.TWICC_DEV_LOCAL_BYPASS = False
    assert client.get(question_url(session)).status_code == 401


def test_authenticated_empty_snapshot_is_read_only(authenticated_client, session):
    result = authenticated_client.get(question_url(session))
    assert result.status_code == 200
    assert result.json() == {"revision": 0, "batches": [], "resolutions": {}, "widget_enabled": True}
    assert not AsyncQuestionState.objects.exists()


def test_snapshot_does_not_depend_on_loaded_message_range(authenticated_client, ready_session):
    assert not SessionItem.objects.exists()
    result = authenticated_client.get(question_url(ready_session) + "?after=1000&before=2000").json()
    assert [batch["item_id"] for batch in result["batches"]] == ["q1"]
    assert result == service.read_question_snapshot(ready_session.id)


def test_snapshot_reads_widget_and_revision_in_one_query(ready_session, django_assert_num_queries):
    with django_assert_num_queries(1):
        snapshot = service.read_question_snapshot(ready_session.id)
    assert snapshot["widget_enabled"] is True
    assert snapshot["revision"] > 0


def test_snapshot_contains_unresolved_batches_and_resolutions(authenticated_client, ready_session):
    service.merge_question_facts(ready_session.id, [question("q2", "t2")])
    service.dismiss_question_batch(ready_session.id, "q1", request_id="dismiss-1")
    result = authenticated_client.get(question_url(ready_session)).json()
    assert [(batch["item_id"], batch["status"]) for batch in result["batches"]] == [("q2", "collecting")]
    assert result["resolutions"] == {"q1": {"status": "dismissed", "request_id": "dismiss-1"}}


def test_disabled_widget_retains_question_state(authenticated_client, ready_session):
    ready_session.question_widget = False
    ready_session.save(update_fields=["question_widget"])
    result = authenticated_client.get(question_url(ready_session)).json()
    assert result["widget_enabled"] is False
    assert result["batches"][0]["item_id"] == "q1"


def test_other_provider_returns_disabled_empty_snapshot(authenticated_client, session):
    session.provider = Provider.CLAUDE_CODE
    session.save(update_fields=["provider"])
    assert authenticated_client.get(question_url(session)).json() == {
        "revision": 0, "batches": [], "resolutions": {}, "widget_enabled": False,
    }


@pytest.mark.parametrize("field,value", [("hidden", True), ("type", SessionType.SUBAGENT),
                                       ("parent_session_id", "parent")])
def test_snapshot_rejects_hidden_sessions_and_subagents(authenticated_client, session, field, value):
    if field == "parent_session_id":
        Session.objects.create(id="parent", project=session.project, provider=Provider.CODEX)
    setattr(session, field, value)
    session.save(update_fields=[field])
    assert authenticated_client.get(question_url(session)).status_code == 404


def test_snapshot_rejects_wrong_project_and_unknown_session(authenticated_client, session):
    assert authenticated_client.get(question_url(session).replace("api-project", "wrong-project")).status_code == 404
    assert authenticated_client.get(question_url(session).replace("api-session", "unknown")).status_code == 404


def test_snapshot_is_get_only(authenticated_client, session):
    assert authenticated_client.post(question_url(session)).status_code == 405


def test_snapshot_has_no_subagent_or_share_route(session):
    endpoint = resolve(question_url(session)).func
    assert resolve("/share/token/async-questions/").func is not endpoint
    with pytest.raises(Resolver404):
        resolve(f"/api/projects/{session.project_id}/sessions/parent/subagent/{session.id}/async-questions/")


def dismiss_ws(monkeypatch, session, *, item_id="q1", request_id="dismiss-1", manager=None):
    from twicc.asgi import WSConsumer

    manager = manager or CodexAgentManager()
    monkeypatch.setattr("twicc.providers.codex.ws.get_agent_manager_registry",
                        lambda: SimpleNamespace(get=lambda *_: manager))
    monkeypatch.setattr("twicc.providers.codex.ws.ensure_provider_running", lambda *_: None)
    consumer = WSConsumer()
    consumer.send_json = AsyncMock()

    async def run():
        await consumer.receive_json({"type": "codex_dismiss_async_question", "session_id": session.id,
                                     "item_id": item_id, "request_id": request_id})

    run_with_writer(run)
    return consumer


def test_ws_dismiss_uses_gated_manager_and_correlated_ack(monkeypatch, ready_session):
    snapshot = service.read_question_snapshot(ready_session.id)
    manager = SimpleNamespace(dismiss_async_question=AsyncMock(return_value=snapshot))
    consumer = dismiss_ws(monkeypatch, ready_session, manager=manager)
    manager.dismiss_async_question.assert_awaited_once_with(ready_session.id, "q1", request_id="dismiss-1")
    consumer.send_json.assert_awaited_once_with({"type": "async_question_dismissed", "session_id": ready_session.id,
                                               "item_id": "q1", "request_id": "dismiss-1", "snapshot": snapshot})


def test_ws_dismiss_preserves_transcript_and_never_starts_turn(monkeypatch, ready_session, layer):
    original = '{"type":"event_msg","payload":{"text":"Keep the menu?"}}'
    SessionItem.objects.create(session=ready_session, line_num=1, content=original)
    manager = CodexAgentManager()
    manager.send_to_session = AsyncMock()
    consumer = dismiss_ws(monkeypatch, ready_session, manager=manager)
    manager.send_to_session.assert_not_awaited()
    assert SessionItem.objects.get(session=ready_session).content == original
    expected = {"q1": {"status": "dismissed", "request_id": "dismiss-1"}}
    assert consumer.send_json.await_args.args[0]["snapshot"]["resolutions"] == expected
    assert updates(layer)[0]["snapshot"]["resolutions"] == expected
    assert len(updates(layer)) == 1


@pytest.mark.parametrize("resolution", ["dismissed", "sent"])
def test_ws_dismiss_resolved_known_batch_is_idempotent(monkeypatch, ready_session, layer, resolution):
    if resolution == "dismissed":
        service.dismiss_question_batch(ready_session.id, "q1", request_id="first")
    else:
        prepared = service.prepare_question_send(ready_session.id, "Yes", None, request_id="first",
                                                origin="human", at="2026-10-05T10:00:03Z")
        service.accept_question_send(ready_session.id, prepared.submission)
    prior = service.read_question_snapshot(ready_session.id)
    layer.group_send.reset_mock()
    consumer = dismiss_ws(monkeypatch, ready_session, request_id="second")
    frame = consumer.send_json.await_args.args[0]
    assert frame["type"] == "async_question_dismissed"
    assert frame["request_id"] == "second"
    assert frame["snapshot"] == prior
    layer.group_send.assert_not_awaited()


@pytest.mark.parametrize("item_id", ["missing", ["q1"], ""])
def test_ws_dismiss_errors_are_correlated(monkeypatch, ready_session, layer, item_id):
    consumer = dismiss_ws(monkeypatch, ready_session, item_id=item_id)
    frame = consumer.send_json.await_args.args[0]
    assert frame["type"] == "error"
    assert frame["code"] == ("async_questions_stale" if item_id == "missing" else "async_questions_invalid")
    assert frame["request_id"] == "dismiss-1"
    assert frame["session_id"] == ready_session.id
    layer.group_send.assert_not_awaited()


def test_dismiss_publication_waits_for_commit_and_discards_rollback(ready_session, layer):
    with transaction.atomic():
        service.dismiss_question_batch(ready_session.id, "q1", request_id="rollback")
        layer.group_send.assert_not_awaited()
        transaction.set_rollback(True)
    layer.group_send.assert_not_awaited()
    assert service.read_question_snapshot(ready_session.id)["batches"][0]["status"] == "ready"
    with transaction.atomic():
        expected = service.dismiss_question_batch(ready_session.id, "q1", request_id="committed")
        layer.group_send.assert_not_awaited()
    assert updates(layer)[0]["snapshot"] == expected


def test_hidden_session_dismiss_does_not_broadcast(ready_session, layer):
    ready_session.hidden = True
    ready_session.save(update_fields=["hidden"])
    service.dismiss_question_batch(ready_session.id, "q1", request_id="hidden")
    layer.group_send.assert_not_awaited()


def settings_update(monkeypatch, session, setting):
    from twicc.core.services import session_update

    monkeypatch.setattr(session_update, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(
        get=lambda *_: SimpleNamespace(get_agent_info=lambda *_: None)))
    return run_with_writer(lambda: session_update.update_session_settings_from_payload(
        {"session_id": session.id, "updates": {"question_widget": setting}}))


def test_widget_toggle_orders_older_hydration_response(authenticated_client, ready_session, monkeypatch, layer):
    old_hydration = authenticated_client.get(question_url(ready_session)).json()
    facts = deepcopy(AsyncQuestionState.objects.get(session=ready_session).state["facts"])
    assert settings_update(monkeypatch, ready_session, False).success
    changed = updates(layer)[0]["snapshot"]
    assert changed["revision"] > old_hydration["revision"]
    assert old_hydration["widget_enabled"] is True
    assert changed["widget_enabled"] is False
    assert changed == authenticated_client.get(question_url(ready_session)).json()
    assert AsyncQuestionState.objects.get(session=ready_session).state["facts"] == facts
    assert len(updates(layer)) == 1
    layer.group_send.reset_mock()
    assert settings_update(monkeypatch, ready_session, None).success
    enabled = updates(layer)[0]["snapshot"]
    assert enabled["widget_enabled"] is True
    assert enabled["revision"] > changed["revision"]
    assert len(updates(layer)) == 1


@pytest.mark.parametrize("existing,setting", [(None, True), (False, False), (True, None)])
def test_effectively_unchanged_widget_does_not_publish_or_bump_revision(session, monkeypatch, layer, existing, setting):
    session.question_widget = existing
    session.save(update_fields=["question_widget"])
    prior = service.read_question_snapshot(session.id)
    assert settings_update(monkeypatch, session, setting).success
    assert updates(layer) == []
    assert service.read_question_snapshot(session.id) == prior


def test_browser_settings_only_updates_publish_once(monkeypatch, ready_session, layer):
    from twicc import asgi

    monkeypatch.setattr(asgi, "ensure_provider_running", lambda *_: None)
    monkeypatch.setattr(asgi, "get_project_directory", AsyncMock(return_value="/tmp"))
    manager = SimpleNamespace(get_agent_info=lambda *_: None, send_to_session=AsyncMock())
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(get=lambda *_: manager))
    consumer = asgi.WSConsumer()
    consumer.send_json = AsyncMock()
    consumer.channel_layer = layer
    # Hidden frontend settings keep their existing reset-to-NULL behavior.
    ready_session.question_widget = False
    ready_session.save(update_fields=["question_widget"])
    before = service.read_question_snapshot(ready_session.id)
    run_with_writer(lambda: consumer._handle_send_message_admitted({
        "session_id": ready_session.id, "project_id": ready_session.project_id,
        "text": "", "request_id": "settings", "question_widget": False,
    }))
    assert len(updates(layer)) == 1
    changed = updates(layer)[0]["snapshot"]
    assert changed["widget_enabled"] is True
    assert changed["revision"] > before["revision"]
    assert changed["batches"] == before["batches"]
    manager.send_to_session.assert_not_awaited()


def test_widget_settings_transaction_discards_snapshot_after_rollback(ready_session, layer):
    from twicc.core.services.session_update import persist_session_settings

    prior = service.read_question_snapshot(ready_session.id)
    with transaction.atomic():
        persist_session_settings(ready_session.id, {"question_widget": False})
        layer.group_send.assert_not_awaited()
        transaction.set_rollback(True)
    assert service.read_question_snapshot(ready_session.id) == prior
    layer.group_send.assert_not_awaited()
    with transaction.atomic():
        persist_session_settings(ready_session.id, {"question_widget": False})
        layer.group_send.assert_not_awaited()
    assert len(updates(layer)) == 1


def test_other_provider_settings_do_not_create_question_state(session, monkeypatch, layer):
    session.provider = Provider.CLAUDE_CODE
    session.save(update_fields=["provider"])
    assert settings_update(monkeypatch, session, False).success
    assert updates(layer) == []
    assert not AsyncQuestionState.objects.exists()


def test_committed_dismissal_identity_exists_before_broadcast_and_ack(monkeypatch, ready_session):
    from twicc.asgi import WSConsumer

    observations = []

    async def publish(*args):
        from asgiref.sync import sync_to_async

        stored = await sync_to_async(lambda: AsyncQuestionState.objects.get(session=ready_session).state)()
        assert stored["facts"]["dismiss:identity"]["data"]["request_id"] == "identity"
        assert stored["batches"]["q1"]["resolved_request_id"] == "identity"
        observations.append("broadcast")

    async def acknowledge(frame):
        assert frame["type"] == "async_question_dismissed"
        assert frame["snapshot"]["resolutions"]["q1"]["request_id"] == frame["request_id"] == "identity"
        observations.append("ack")

    monkeypatch.setattr("twicc.providers.codex.question_snapshots.get_channel_layer",
                        lambda: SimpleNamespace(group_send=publish))
    monkeypatch.setattr("twicc.providers.codex.ws.ensure_provider_running", lambda *_: None)
    manager = CodexAgentManager()
    monkeypatch.setattr("twicc.providers.codex.ws.get_agent_manager_registry",
                        lambda: SimpleNamespace(get=lambda *_: manager))
    consumer = WSConsumer()
    consumer.send_json = acknowledge
    run_with_writer(lambda: consumer.receive_json({
        "type": "codex_dismiss_async_question", "session_id": ready_session.id,
        "item_id": "q1", "request_id": "identity",
    }))
    assert observations == ["broadcast", "ack"]
