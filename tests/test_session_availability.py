"""A session whose ``unavailable_reason`` is set is read nowhere and written nowhere.

One test per entry-point family. The rule depends only on the field being
non-null, so every test uses a reason no code knows: a new reason must need no
change anywhere. Metadata surfaces stay open, and are checked too.

Rule: ``twicc.core.session_availability``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from django.http import Http404
from django.test import AsyncClient
from django.utils import timezone
from typer.testing import CliRunner

from twicc import asgi
from twicc import search as search_mod
from twicc.agent.exceptions import SessionUnavailableError
from twicc.asgi import WSConsumer
from twicc.cli import app
from twicc.cli import sessions_wait_reply
from twicc.core.models import Project, Session, SessionType, Share
from twicc.core.session_availability import (
    SESSION_UNAVAILABLE,
    aensure_session_id_available,
    all_unavailable_session_ids,
    session_unavailable_reason,
    unavailable_reason_by_id,
)
from twicc.rpc.invoker import invoke

REASON = "some_reason_no_code_knows"
PROJECT_ID = "-tmp-availability"

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(sid, *, reason=None, parent=None, **kwargs):
    project, _ = Project.objects.get_or_create(id=PROJECT_ID, defaults={"directory": "/tmp/availability"})
    return Session.objects.create(
        id=sid, project=project, provider=kwargs.pop("provider", "claude_code"), file_path=f"{sid}.jsonl",
        type=SessionType.SUBAGENT if parent else SessionType.SESSION, parent_session=parent,
        created_at=timezone.now(), user_message_count=1, last_line=3, unavailable_reason=reason, **kwargs,
    )


@pytest.fixture
def sessions():
    blocked = make_session("blocked", reason=REASON)
    return SimpleNamespace(
        blocked=blocked,
        child=make_session("blocked-child", parent=blocked),
        open=make_session("open"),
    )


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_any_reason_blocks_and_a_subagent_follows_its_parent(sessions):
    assert session_unavailable_reason(sessions.blocked) == REASON
    assert session_unavailable_reason(sessions.child) == REASON
    assert session_unavailable_reason(sessions.open) is None
    assert unavailable_reason_by_id("blocked-child") == REASON
    assert unavailable_reason_by_id("open") is None
    assert unavailable_reason_by_id("unknown") is None
    assert all_unavailable_session_ids() == {"blocked", "blocked-child"}


def test_the_lookup_of_every_unavailable_session_uses_its_partial_index(sessions):
    """Every search runs it: a scan of the whole session table would cost each one."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from twicc.core.session_availability import all_unavailable_session_ids

    with CaptureQueriesContext(connection) as captured:
        all_unavailable_session_ids()
    # The first query finds the sessions with a reason; the second, their subagents.
    first_sql = captured.captured_queries[0]["sql"]
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN QUERY PLAN {first_sql}")
        plan = " ".join(str(row[-1]) for row in cursor.fetchall())

    assert "idx_session_unavailable" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_a_known_parent_spares_the_query(sessions, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert session_unavailable_reason(sessions.child, parent=sessions.blocked) == REASON
        assert session_unavailable_reason(sessions.blocked) == REASON


def test_an_available_top_level_session_is_answered_inline(sessions, monkeypatch):
    """The hot path (each live append, each items read) never reaches the executor."""
    from twicc.core import session_availability

    def no_hop(*args, **kwargs):
        raise AssertionError("went to the thread executor")

    monkeypatch.setattr("asgiref.sync.sync_to_async", no_hop)
    assert asyncio.run(session_availability.asession_unavailable_reason(sessions.open)) is None
    assert asyncio.run(session_availability.asession_unavailable_reason(sessions.blocked)) == REASON
    assert asyncio.run(session_availability.asession_unavailable_reason(
        sessions.child, parent=sessions.blocked)) == REASON


def test_the_write_guard_raises_a_send_error_naming_the_reason(sessions):
    with pytest.raises(SessionUnavailableError) as exc:
        asyncio.run(aensure_session_id_available("blocked"))
    assert exc.value.code == SESSION_UNAVAILABLE
    assert REASON in str(exc.value)
    asyncio.run(aensure_session_id_available("open"))
    asyncio.run(aensure_session_id_available("unknown"))


# ---------------------------------------------------------------------------
# REST reads
# ---------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


CONTENT_URLS = [
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/items/?range=0:5",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/items/metadata/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/items/1/tool-results/toolu_1/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/items/1/attachments/att_0123456789ab.png",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/user-messages/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/subagents/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/tool-states/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/workflows/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/workflow-links/",
    f"/api/projects/{PROJECT_ID}/sessions/{{sid}}/async-questions/",
    "/api/sessions/{sid}/plan/",
    "/api/sessions/{sid}/inline-artifacts/a1/index.html",
    f"/api/projects/{PROJECT_ID}/sessions/blocked/subagent/blocked-child/items/metadata/",
]


@pytest.mark.parametrize("url", CONTENT_URLS)
def test_content_endpoints_refuse_with_the_reason(sessions, client, url):
    response = asyncio.run(client.get(url.format(sid="blocked")))

    assert response.status_code == 409
    body = orjson.loads(response.content)
    assert body["code"] == SESSION_UNAVAILABLE
    assert body["unavailable_reason"] == REASON
    assert REASON in body["error"]
    # The session that carries the reason: the subagent follows its parent.
    assert body["unavailable_session_id"] == "blocked"


def test_a_subagent_with_its_own_reason_is_named_as_the_carrier(sessions, client):
    make_session("own-child", reason="own_reason", parent=sessions.open)
    url = f"/api/projects/{PROJECT_ID}/sessions/open/subagent/own-child/items/metadata/"
    body = orjson.loads(asyncio.run(client.get(url)).content)

    assert body["unavailable_reason"] == "own_reason"
    assert body["unavailable_session_id"] == "own-child"


def test_content_endpoints_still_serve_an_available_session(sessions, client):
    response = asyncio.run(client.get(f"/api/projects/{PROJECT_ID}/sessions/open/items/metadata/"))
    assert response.status_code == 200


def test_metadata_endpoints_stay_open_and_expose_the_reason(sessions, client):
    for url in ("/api/sessions/blocked/", f"/api/projects/{PROJECT_ID}/sessions/blocked/"):
        response = asyncio.run(client.get(url))
        assert response.status_code == 200
        assert orjson.loads(response.content)["unavailable_reason"] == REASON


def test_the_payload_drops_what_comes_from_the_content(sessions):
    from twicc.core.serializers import serialize_session

    Session.objects.filter(id__in=["blocked", "open"]).update(
        tasks={"items": [{"content": "secret"}]}, goals=[{"objective": "secret"}],
        plan_paths=["plan.md"], has_workflows=True,
    )
    blocked = serialize_session(Session.objects.get(id="blocked"))
    open_ = serialize_session(Session.objects.get(id="open"))

    assert blocked["unavailable_reason"] == REASON
    assert (blocked["tasks"], blocked["goals"], blocked["plan_paths"], blocked["inline_artifacts"]) == ({}, [], [], {})
    assert not any(blocked[flag] for flag in ("has_tasks", "has_goals", "has_plan", "has_workflows"))
    assert open_["tasks"] and open_["goals"] and open_["plan_paths"] and open_["has_workflows"]


# ---------------------------------------------------------------------------
# Writes: the send service (CLI, RPC, MCP, send-messages) and the WebSocket
# ---------------------------------------------------------------------------


def test_the_send_service_rejects_with_the_reason(sessions):
    from twicc.core.services.send_message import send_message_to_session_from_payload

    result = asyncio.run(send_message_to_session_from_payload({"session_id": "blocked", "text": "hi"}))

    assert result.success is False
    assert [e.code for e in result.errors] == [SESSION_UNAVAILABLE]
    assert REASON in result.errors[0].message


@pytest.fixture
def consumer(monkeypatch):
    manager = SimpleNamespace(send_to_session=AsyncMock(return_value=True), get_agent_info=lambda sid: None)
    monkeypatch.setattr("twicc.asgi.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: manager))
    instance = WSConsumer()
    instance.send_json = AsyncMock()
    instance.channel_layer = SimpleNamespace(group_send=AsyncMock())
    instance.manager = manager
    return instance


async def _drain():
    while asgi._DETACHED_TASKS:
        await asyncio.gather(*list(asgi._DETACHED_TASKS), return_exceptions=True)


def _frames(consumer):
    return [call.args[0] for call in consumer.send_json.call_args_list]


def test_the_websocket_send_refuses_before_any_side_effect(sessions, consumer):
    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "blocked", "project_id": PROJECT_ID,
            "text": "hi", "request_id": "req-1", "selected_model": "opus",
        })
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    assert (frame["type"], frame["code"], frame["request_id"]) == ("error", SESSION_UNAVAILABLE, "req-1")
    assert REASON in frame["message"]
    consumer.manager.send_to_session.assert_not_called()
    assert Session.objects.get(id="blocked").selected_model is None  # settings not persisted


def test_the_hybrid_switch_is_refused(sessions, consumer, settings):
    settings.CLAUDE_HYBRID_ENABLED = True

    async def go():
        await consumer._handle_set_session_hybrid({"session_id": "blocked"})
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    assert frame["code"] == SESSION_UNAVAILABLE
    assert Session.objects.get(id="blocked").hybrid is False


def test_no_title_is_suggested_from_the_content(sessions, consumer, monkeypatch):
    suggest = AsyncMock()
    monkeypatch.setattr("twicc.asgi.suggest_title", suggest)

    asyncio.run(consumer._handle_suggest_title({
        "sessionId": "blocked", "provider": "claude_code", "systemPrompt": "Title: {text}",
    }))

    [frame] = _frames(consumer)
    assert (frame["type"], frame["error"]) == ("title_suggested", SESSION_UNAVAILABLE)
    # No asked model: the session provider's model.
    assert (frame["requestedModel"], frame["titleModel"]) == ("haiku", None)
    suggest.assert_not_called()


@pytest.mark.parametrize("asked, key_set, expected", [
    ("openrouter", True, "openrouter"), ("openrouter", False, "haiku"),
    # "qwen", the stored value before the rename to "openrouter": unknown, so the default.
    ("qwen", True, "haiku"),
    ("luna", False, "luna"), ("haiku", True, "haiku"), ("provider", True, "haiku"), ("unknown", True, "haiku"),
])
def test_the_unavailable_title_frame_names_the_asked_model(sessions, consumer, monkeypatch, asked, key_set, expected):
    monkeypatch.setattr("twicc.asgi.suggest_title", AsyncMock())
    monkeypatch.setattr("twicc.core.services.title_suggestion.openrouter_key_set", lambda: key_set)

    asyncio.run(consumer._handle_suggest_title({
        "sessionId": "blocked", "provider": "claude_code", "systemPrompt": "Title: {text}",
        "titleSuggestionModel": asked,
    }))

    [frame] = _frames(consumer)
    assert frame["error"] == SESSION_UNAVAILABLE
    assert (frame["requestedModel"], frame["titleModel"]) == (expected, None)


def test_the_codex_question_dismissal_is_refused(monkeypatch):
    from twicc.providers.codex.ws import CodexWSHandler

    make_session("codex-blocked", reason=REASON, provider="codex")
    manager = SimpleNamespace(dismiss_async_question=AsyncMock())
    monkeypatch.setattr("twicc.providers.codex.ws.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: manager))
    handler = CodexWSHandler(SimpleNamespace(send_json=AsyncMock()))

    asyncio.run(handler._handle_dismiss_async_question(
        {"session_id": "codex-blocked", "item_id": "i1", "request_id": "r1"},
    ))

    frame = handler.consumer.send_json.call_args.args[0]
    assert frame["code"] == SESSION_UNAVAILABLE
    manager.dismiss_async_question.assert_not_called()


def test_no_agent_resumes_the_session(sessions):
    """The managers' last guard, under every resume (parked restarts included)."""
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    with pytest.raises(SessionUnavailableError):
        asyncio.run(manager._start_agent("blocked", PROJECT_ID, "/tmp", "hi", True, settings=AgentSettings()))
    assert manager._agents == {}


def _idle_claude_manager(session_id, agent_settings):
    """A real manager holding one idle fake agent, its broadcasts recorded."""
    from twicc.agent.states import AgentState
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager

    manager = ClaudeCodeAgentManager()
    agent = SimpleNamespace(
        session_id=session_id, project_id=PROJECT_ID, cwd="/tmp", state=AgentState.USER_TURN,
        agent_settings=agent_settings, background_shell_count=lambda: 0, is_hybrid=False,
        interrupt_or_kill=AsyncMock(), apply_live_settings=AsyncMock(), send=AsyncMock(),
    )
    manager._agents[session_id] = agent
    manager._broadcast_agent_state = AsyncMock()
    manager._session_has_crons = AsyncMock(return_value=False)
    return manager, agent


def _other_effort(settings):
    return settings._replace(effort="low" if settings.effort != "low" else "high")


def test_a_startup_change_send_is_refused_before_the_kill(sessions):
    from twicc.providers.helpers import AgentSettings

    requested = AgentSettings(effort="high")
    manager, agent = _idle_claude_manager("blocked", _other_effort(requested))

    with pytest.raises(SessionUnavailableError):
        asyncio.run(manager.send_to_session("blocked", PROJECT_ID, "/tmp", "hi", requested))

    agent.interrupt_or_kill.assert_not_called()
    manager._broadcast_agent_state.assert_not_called()  # no STARTING left behind
    assert manager._pending_after_restart == {}
    assert manager._agents["blocked"] is agent


def test_a_deferred_startup_restart_is_refused_before_the_kill(sessions):
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    helpers = get_provider_helpers("claude_code")
    requested = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(sessions.blocked)),
    )
    manager, agent = _idle_claude_manager("blocked", _other_effort(requested))
    manager._pending_after_restart["blocked"] = {"text": "parked", "images": None, "documents": None}

    asyncio.run(manager._apply_pending_settings(agent))

    agent.interrupt_or_kill.assert_not_called()
    manager._broadcast_agent_state.assert_not_called()  # no STARTING left behind
    assert manager._pending_after_restart == {}


def test_a_late_refusal_after_starting_broadcasts_dead(sessions):
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings

    manager, _agent = _idle_claude_manager("blocked", AgentSettings())

    with pytest.raises(SessionUnavailableError):
        asyncio.run(manager._restart_after_kill("blocked", PROJECT_ID, "/tmp", "hi", settings=AgentSettings()))

    manager._broadcast_agent_state.assert_awaited_once_with("blocked", PROJECT_ID, AgentState.DEAD)


def _start_failure():
    from twicc.agent.exceptions import AgentStartError
    return AgentStartError("Claude Code did not start", reason="working_directory_missing")


def test_inline_restart_reports_and_clears_starting(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings

    manager, _agent = _idle_claude_manager("open", AgentSettings())
    manager._start_agent = AsyncMock(side_effect=_start_failure())
    with pytest.raises(AgentStartError):
        asyncio.run(manager._restart_after_kill(
            "open", PROJECT_ID, "/tmp", "hi", settings=AgentSettings(), report_start_failure=True,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with("open", PROJECT_ID, AgentState.DEAD)


def test_automatic_parked_restart_notifies_and_does_not_raise(sessions):
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    helpers = get_provider_helpers("claude_code")
    requested = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(sessions.open)),
    )
    manager, agent = _idle_claude_manager("open", _other_effort(requested))
    manager._pending_after_restart["open"] = {"text": "parked", "images": None, "documents": None}
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)

    asyncio.run(manager._apply_pending_settings(agent))

    manager._broadcast_agent_state.assert_any_await(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


def test_automatic_parked_restart_failure_finishes_the_deferred_handoff(sessions):
    from twicc.providers.helpers import AgentSettings, get_provider_helpers

    helpers = get_provider_helpers("claude_code")
    requested = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(sessions.open)),
    )
    manager, agent = _idle_claude_manager("open", _other_effort(requested))
    manager._pending_after_restart["open"] = {
        "text": "parked", "images": None, "documents": None, "completion_token": "t",
    }
    manager._start_agent = AsyncMock(side_effect=_start_failure())

    async def scenario():
        handoff = asyncio.get_running_loop().create_future()
        manager._deferred_completion_receipts["t"] = handoff
        await manager._apply_pending_settings(agent)
        return handoff

    handoff = asyncio.run(scenario())

    assert handoff.done()
    assert handoff.result() is None


def test_inline_restart_failure_rejects_the_staged_receipt(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.providers.helpers import AgentSettings

    requested = AgentSettings(effort="high")
    manager, agent = _idle_claude_manager("open", _other_effort(requested))
    agent._completion_input_pending = MagicMock()
    agent._completion_input_settled = MagicMock()
    manager._start_agent = AsyncMock(side_effect=_start_failure())

    with pytest.raises(AgentStartError):
        asyncio.run(manager.send_to_session(
            "open", PROJECT_ID, "/tmp", "hi", requested, completion_token="t", completion_cursor=0,
        ))

    agent._completion_input_settled.assert_called_once_with("t", "rejected")


def test_cron_send_notifies_then_raises(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._broadcast_agent_state = AsyncMock()
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)
    with pytest.raises(AgentStartError):
        asyncio.run(manager.send_to_session(
            "open", PROJECT_ID, "/tmp", "hi", AgentSettings(), cancel_cron_restart=False,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


@pytest.mark.parametrize("cancel, expected", [(True, True), (False, False)])
def test_cold_send_report_flag_follows_the_cron_marker(sessions, cancel, expected):
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._start_agent = AsyncMock(return_value="open")
    asyncio.run(manager.send_to_session("open", PROJECT_ID, "/tmp", "hi", AgentSettings(), cancel_cron_restart=cancel))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected


@pytest.mark.parametrize("cancel, expected", [(True, True), (False, False)])
def test_inline_restart_forwards_the_report_flag(sessions, cancel, expected):
    from twicc.providers.helpers import AgentSettings

    requested = AgentSettings(effort="high")
    manager, _agent = _idle_claude_manager("open", _other_effort(requested))
    manager._start_agent = AsyncMock(return_value="open")
    asyncio.run(manager.send_to_session("open", PROJECT_ID, "/tmp", "hi", requested, cancel_cron_restart=cancel))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected


def test_restart_without_a_waiting_caller_notifies_then_raises(sessions):
    from twicc.agent.exceptions import AgentStartError
    from twicc.agent.states import AgentState
    from twicc.providers.helpers import AgentSettings

    manager, _agent = _idle_claude_manager("open", AgentSettings())
    failure = _start_failure()
    manager._start_agent = AsyncMock(side_effect=failure)
    with pytest.raises(AgentStartError):
        asyncio.run(manager._restart_after_kill(
            "open", PROJECT_ID, "/tmp", "hi", settings=AgentSettings(), report_start_failure=False,
        ))
    manager._broadcast_agent_state.assert_awaited_once_with(
        "open", PROJECT_ID, AgentState.DEAD, error=str(failure), error_ui_message=failure.ui_message, kill_reason="error",
    )


@pytest.mark.parametrize("ephemeral, expected", [(False, True), (True, False)])
def test_create_report_flag_follows_ephemeral(sessions, ephemeral, expected):
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.helpers import AgentSettings

    manager = ClaudeCodeAgentManager()
    manager._start_agent = AsyncMock(return_value="draft")
    asyncio.run(manager.create_session("draft", PROJECT_ID, "/tmp", "hi", AgentSettings(), ephemeral=ephemeral))
    assert manager._start_agent.await_args.kwargs["report_start_failure"] is expected


def test_the_hybrid_terminal_is_refused(sessions, settings, monkeypatch):
    from twicc.terminal import terminal_application

    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr("twicc.terminal.scope_remote_access_blocked", lambda scope: False)
    scope = {"type": "websocket", "url_route": {"kwargs": {}}, "query_string": b"name=h:blocked"}
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run(terminal_application(scope, receive, send))

    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.send", "websocket.send", "websocket.close"]
    assert REASON in sent[1]["text"]
    assert orjson.loads(sent[2]["text"]) == {"type": "pty_exited"}


def test_a_cron_restart_does_not_resume_the_session(sessions, monkeypatch):
    from twicc.providers.claude_code import cron_restart

    monkeypatch.setattr(cron_restart, "_split_restorable_crons", lambda sid: SimpleNamespace(has_any=True))

    assert cron_restart._collect_restart_data("blocked") is None


# ---------------------------------------------------------------------------
# Pending questions (read and answer)
# ---------------------------------------------------------------------------


def _run_question_service(coro_factory):
    registry = MagicMock()
    registry.get_agent_info.return_value = None
    with patch("twicc.agent.registry.get_agent_manager_registry", return_value=registry), \
         patch("twicc.core.services.session_update.ensure_provider_running"):
        return asyncio.run(coro_factory())


@pytest.mark.parametrize("payload", [
    {"session_id": "blocked"},
    {"session_id": "blocked", "action": "cancel"},
])
def test_pending_questions_are_refused(sessions, payload):
    from twicc.core.services.pending_question import (
        answer_pending_question_from_payload,
        read_pending_requests_from_payload,
    )

    service = answer_pending_question_from_payload if "action" in payload else read_pending_requests_from_payload
    result = _run_question_service(lambda: service(payload))

    assert result.success is False
    assert [e.code for e in result.errors] == [SESSION_UNAVAILABLE]


# ---------------------------------------------------------------------------
# CLI readers (and the RPC and MCP tools generated from them)
# ---------------------------------------------------------------------------


@pytest.fixture
def no_backend(monkeypatch):
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)


@pytest.mark.parametrize("argv", [
    ("content",), ("messages",), ("agents",), ("plan",), ("workflows",), ("workflow", "wf_1"), ("wait-reply",),
])
@pytest.mark.parametrize("sid", ["blocked", "blocked-child"])
def test_cli_readers_exit_3_with_the_reason(sessions, no_backend, argv, sid):
    result = CliRunner().invoke(app, ["session", sid, *argv])

    assert result.exit_code == 3, result.output
    assert REASON in result.output
    # The RPC route and the MCP tool run the same command in-process.
    through_rpc = invoke(["session", sid, *argv])
    assert through_rpc.exit_code == 3
    assert REASON in through_rpc.error


def test_cli_row_stays_readable(sessions, no_backend):
    result = CliRunner().invoke(app, ["session", "blocked"])

    assert result.exit_code == 0, result.output
    assert orjson.loads(result.stdout)["unavailable_reason"] == REASON


def test_sessions_wait_reply_reports_it_per_id(sessions, capsysbinary, monkeypatch):
    seen = {}

    def fake(cursors, **kwargs):
        seen.update(cursors)
        return {sid: {"outcome": "replied"} for sid in cursors}

    monkeypatch.setattr("twicc.cli._wait_reply.wait_for_replies", fake)

    sessions_wait_reply.main(["blocked", "open"], timeout=5.0)
    payload = orjson.loads(capsysbinary.readouterr().out)

    assert list(seen) == ["open"]
    assert payload["results"]["blocked"]["outcome"] == SESSION_UNAVAILABLE
    assert payload["results"]["blocked"]["unavailable_reason"] == REASON
    assert payload["summary"]["total"] == 2


# ---------------------------------------------------------------------------
# Search: filtered at query time, the index keeps the documents
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed(sessions, tmp_path, monkeypatch):
    from twicc import paths

    monkeypatch.setattr(paths, "get_search_dir", lambda: tmp_path / "search-index")
    search_mod.init_search_index()
    for session in (sessions.blocked, sessions.child, sessions.open):
        search_mod.index_document(
            session_id=session.id, project_id=PROJECT_ID, line_num=1, body="needle here",
            from_role="user", timestamp=timezone.now(), archived=False,
        )
    search_mod.commit()
    try:
        yield
    finally:
        search_mod.shutdown_search_index()


def _ui_search_ids(client):
    response = asyncio.run(client.get("/api/search/?q=needle"))
    assert response.status_code == 200
    return {r["session_id"] for r in orjson.loads(response.content)["results"]}


def test_search_leaves_out_unavailable_sessions(indexed, client):
    assert _ui_search_ids(client) == {"open"}
    raw = search_mod.raw_search("needle", to_json=False, include_hidden=True)
    assert [hit["session_id"] for hit in raw["hits"]] == ["open"]
    assert raw["total_hits"] == 1

    # The documents stay: the hits come back with the history.
    Session.objects.filter(id="blocked").update(unavailable_reason=None)
    assert _ui_search_ids(client) == {"open", "blocked", "blocked-child"}
    raw = search_mod.raw_search("needle", to_json=False, include_hidden=True)
    assert {hit["session_id"] for hit in raw["hits"]} == {"open", "blocked", "blocked-child"}


# ---------------------------------------------------------------------------
# Shares: no new link, an existing one serves nothing
# ---------------------------------------------------------------------------


def test_no_share_is_created(sessions):
    from twicc.core.services.share_mutation import create_share

    result = asyncio.run(create_share("session", session=sessions.blocked))

    assert result.success is False
    assert [e.code for e in result.errors] == [SESSION_UNAVAILABLE]
    assert not Share.objects.exists()


def test_a_snapshot_share_is_not_refrozen(sessions):
    from twicc.core.services.share_mutation import propagate_share

    share = Share.objects.create(
        id="sh2", token="tok-2", kind="session", session=sessions.blocked,
        options={"mode": "snapshot", "frozen_at_line": 1},
    )

    result = asyncio.run(propagate_share(share))

    assert [e.code for e in result.errors] == [SESSION_UNAVAILABLE]
    assert Share.objects.get(id="sh2").options["frozen_at_line"] == 1


def test_a_refused_refreeze_keeps_the_exports_in_flight(sessions):
    from twicc.core.services.share_mutation import propagate_share
    from twicc.inline_artifacts import share_exports

    share = Share.objects.create(
        id="sh3", token="tok-3", kind="session", session=sessions.blocked,
        options={"mode": "snapshot", "frozen_at_line": 1},
    )
    before = share_exports._generations.get(share_exports._root_key(share.id), 0)

    asyncio.run(propagate_share(share))

    assert share_exports._generations.get(share_exports._root_key(share.id), 0) == before


def test_new_options_on_a_session_share_are_refused(sessions, monkeypatch):
    from twicc.core.services.share_mutation import patch_share

    async def passthrough(factory):
        return await factory()

    monkeypatch.setattr("twicc.core.services.share_mutation.run_under_db_write_lock", passthrough)

    share = Share.objects.create(id="sh4", token="tok-4", kind="session", session=sessions.blocked,
                                 options={"mode": "live"}, label="old")

    result = asyncio.run(patch_share(share, {"options": {"mode": "snapshot"}, "label": "new"}))

    assert [e.code for e in result.errors] == [SESSION_UNAVAILABLE]
    stored = Share.objects.get(id="sh4")
    assert (stored.options, stored.label) == ({"mode": "live"}, "old")
    # The rest of a link stays manageable.
    assert asyncio.run(patch_share(share, {"label": "renamed"})).success
    assert Share.objects.get(id="sh4").label == "renamed"


def test_an_existing_share_serves_nothing(sessions):
    from twicc.share.consumer import _session_is_ready
    from twicc.share.resolver import resolve_or_404

    Share.objects.create(id="sh1", token="tok-1", kind="session", session=sessions.blocked, options={"mode": "live"})
    request = SimpleNamespace(session=SimpleNamespace())

    with pytest.raises(Http404):
        asyncio.run(resolve_or_404(request, "tok-1"))
    assert asyncio.run(_session_is_ready("blocked")) is False


# ---------------------------------------------------------------------------
# Automatic processing and external subscriptions
# ---------------------------------------------------------------------------


def test_automatic_titles_skip_the_session(sessions):
    from twicc.core.services.title_automation import _is_eligible

    Session.objects.filter(id__in=["blocked", "open"]).update(title_origin="auto")
    assert _is_eligible(Session.objects.get(id="blocked"), closing=False) is False
    assert _is_eligible(Session.objects.get(id="open"), closing=False) is True


def test_mcp_event_subscriptions_are_refused(sessions):
    from mcp.shared.exceptions import MCPError

    from twicc.mcp.events.methods import refuse_unavailable_session

    with pytest.raises(MCPError) as exc:
        refuse_unavailable_session(sessions.blocked)
    assert exc.value.error.data["reason"] == SESSION_UNAVAILABLE
    refuse_unavailable_session(sessions.open)
    refuse_unavailable_session(None)


def _registry_with(monkeypatch, manager):
    monkeypatch.setattr("twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda p: manager))
    monkeypatch.setattr("twicc.core.services.send_message.ensure_provider_running", lambda provider: None)
    monkeypatch.setattr("twicc.core.services.session_creation.ensure_provider_running", lambda provider: None)


def test_the_send_service_returns_the_code_and_the_reason(sessions, monkeypatch):
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.services.send_message import send_message_to_session_from_payload

    failure = _start_failure()
    manager = MagicMock(spec=BaseAgentManager)
    manager.send_to_session = AsyncMock(side_effect=failure)
    manager.get_live_agent = lambda sid: None
    _registry_with(monkeypatch, manager)

    result = asyncio.run(send_message_to_session_from_payload({"session_id": "open", "text": "hi"}))

    assert result.success is False
    assert (result.errors[0].code, result.errors[0].reason) == ("agent_start_failed", "working_directory_missing")
    assert result.errors[0].message == str(failure)


def test_the_create_service_returns_the_code_and_the_reason(sessions, monkeypatch):
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.services.session_creation import create_session_from_payload

    manager = MagicMock(spec=BaseAgentManager)
    manager.create_session = AsyncMock(side_effect=_start_failure())
    _registry_with(monkeypatch, manager)

    result = asyncio.run(create_session_from_payload({
        "session_id": "new-session", "project_id": PROJECT_ID, "provider": "claude_code", "text": "hi",
    }))

    assert result.success is False
    assert (result.errors[0].code, result.errors[0].reason) == ("agent_start_failed", "working_directory_missing")


@pytest.fixture
def db_write_passthrough(monkeypatch):
    async def passthrough(operation, **kwargs):
        return await operation()

    monkeypatch.setattr("twicc.asgi.run_under_db_write_lock", passthrough)


def test_the_websocket_new_session_frame_has_the_reason(sessions, consumer, monkeypatch, db_write_passthrough):
    from twicc.core.services.session_creation import SessionCreationError, SessionCreationResult

    error = SessionCreationError("session", "agent_start_failed", "did not start", "working_directory_missing")
    monkeypatch.setattr("twicc.asgi.create_session_from_payload", AsyncMock(
        return_value=SessionCreationResult(False, None, None, None, [error]),
    ))

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "new-draft", "project_id": PROJECT_ID,
            "provider": "claude_code", "text": "hi", "request_id": "req-1",
        })
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    assert (frame["code"], frame["reason"]) == ("agent_start_failed", "working_directory_missing")


def test_the_websocket_frame_has_the_reason_and_no_ack(sessions, consumer, db_write_passthrough):
    failure = _start_failure()
    consumer.manager.send_to_session = AsyncMock(side_effect=failure)

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "open", "project_id": PROJECT_ID,
            "text": "hi", "request_id": "req-1", "selected_model": "opus",
        })
        await _drain()

    with patch("twicc.asgi.logger") as logger:
        asyncio.run(go())

    [frame] = _frames(consumer)
    assert (frame["type"], frame["code"], frame["reason"], frame["request_id"]) == (
        "error", "agent_start_failed", "working_directory_missing", "req-1",
    )
    consumer.manager.send_to_session.assert_awaited_once()
    logger.warning.assert_not_called()


def test_the_websocket_frame_of_another_error_has_no_reason(sessions, consumer, db_write_passthrough):
    consumer.manager.send_to_session = AsyncMock(side_effect=SessionUnavailableError(
        "gone", session_id="open", reason="some_reason",
    ))

    async def go():
        await consumer._handle_send_message({
            "type": "send_message", "session_id": "open", "project_id": PROJECT_ID,
            "text": "hi", "request_id": "req-1", "selected_model": "opus",
        })
        await _drain()

    asyncio.run(go())

    [frame] = _frames(consumer)
    consumer.manager.send_to_session.assert_awaited_once()
    assert frame["code"] == SESSION_UNAVAILABLE
    assert "reason" not in frame
