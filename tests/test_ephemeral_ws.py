"""The WS admission exists before its first asynchronous lookup."""

import asyncio
from unittest.mock import AsyncMock, patch

from twicc.agent import ephemeral
from twicc.asgi import WSConsumer


def test_ws_reserves_before_lookup_and_settles_failed_validation():
    async def scenario():
        ephemeral.clear()
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()

        async def lookup(sid):
            assert ephemeral.pending_snapshot()[0]["draft_session_id"] == "draft"

        with (
            patch("twicc.asgi.get_session_provider", side_effect=lookup),
            patch("twicc.asgi.get_project_directory", new=AsyncMock(return_value=None)),
            patch("twicc.asgi.ensure_provider_running"),
        ):
            await consumer._handle_send_message(
                {"session_id": "draft", "project_id": "p", "provider": "claude_code", "ephemeral": True, "text": "go"}
            )
        assert ephemeral.pending_snapshot() == []
        assert ephemeral.is_known("draft")
        ephemeral.clear()

    asyncio.run(scenario())


def test_ws_existing_persistent_row_is_untouched_and_unreserved():
    async def scenario():
        ephemeral.clear()
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        with (
            patch("twicc.asgi.get_session_provider", new=AsyncMock(return_value="claude_code")),
            patch("twicc.asgi.get_project_directory", new=AsyncMock()) as project_lookup,
        ):
            await consumer._handle_send_message(
                {
                    "session_id": "persistent",
                    "project_id": "p",
                    "provider": "claude_code",
                    "ephemeral": True,
                    "text": "go",
                }
            )
        assert consumer.send_json.call_args.args[0]["code"] == "ephemeral_existing_session"
        project_lookup.assert_not_called()
        assert not ephemeral.is_known("persistent")
        ephemeral.clear()

    asyncio.run(scenario())


import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from channels.testing import WebsocketCommunicator
from twicc.agent.states import AgentInfo, AgentState
from twicc.core.enums import Provider


@pytest.mark.django_db(transaction=True)
def test_snapshot_refreshes_agents_and_includes_unbound_admission():
    async def scenario():
        ephemeral.clear()
        old = AgentInfo("old", "p", Provider.CODEX, AgentState.ASSISTANT_TURN, None, 1, 1, 1)
        new = old._replace(session_id="new", extra={"ephemeral": True, "ephemeral_draft_id": "draft"})
        agents = [old]
        registry = SimpleNamespace(get_active_agents=lambda: list(agents), set_broadcast_callback=Mock())

        async def enrich(records):
            agents.append(new)
            ephemeral.reserve("unbound", "codex", "p")
            return {}

        async def enrich_provider(record, sid):
            record["active_crons"] = [{"id": "cron"}]

        helper = SimpleNamespace(enrich_agent_state=AsyncMock(side_effect=enrich_provider))
        comm = WebsocketCommunicator(WSConsumer.as_asgi(), "/ws/?subscribe=active_processes")
        comm.scope["client"] = ("127.0.0.1", 43210)
        with (
            patch("twicc.asgi.get_agent_manager_registry", return_value=registry),
            patch("twicc.asgi.get_bulk_session_and_project_display", side_effect=enrich),
            patch("twicc.asgi.get_provider_helpers", return_value=helper),
        ):
            connected, _ = await comm.connect()
            assert connected
            frame = await comm.receive_json_from()
            assert {p["session_id"] for p in frame["processes"]} == {"old", "new"}
            assert frame["processes"][0]["active_crons"] == [{"id": "cron"}]
            assert frame["ephemeral_starting"] == [
                {"draft_session_id": "unbound", "provider": "codex", "project_id": "p"}
            ]
            await comm.disconnect()
        ephemeral.clear()

    asyncio.run(scenario())


def test_ws_normal_followup_does_not_conflict_with_registered_normal_claim():
    async def scenario():
        ephemeral.clear()
        admission = ephemeral.reserve("normal", "claude_code", "p", ephemeral=False)
        ephemeral.mark_registered("normal")
        ephemeral.settle(admission)
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        consumer._handle_send_message_admitted = AsyncMock(return_value=True)
        await consumer._handle_send_message(
            {"session_id": "normal", "project_id": "p", "provider": "claude_code", "text": "followup"}
        )
        consumer._handle_send_message_admitted.assert_awaited_once()
        assert consumer._handle_send_message_admitted.call_args.kwargs["ephemeral_admission"] is None
        consumer.send_json.assert_not_called()
        assert ephemeral.is_active_normal("normal")
        ephemeral.clear()

    asyncio.run(scenario())
