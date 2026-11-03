"""New message events retain the exact sending request's origin."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tests import test_timeline_message_associations
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import LogicalTurn, ProcessRun, Session, TimelineEvent, TimelineKind
from twicc.core.services.timeline_filters import author_of, author_q
from twicc.core.services.timeline_messages import record_indexed_messages, recover_referenced_messages
from twicc.request_origin import RequestOrigin, SYSTEM_ORIGIN, UI_ORIGIN, origin_scope

bridge = test_timeline_message_associations.bridge
content_row_factory = test_timeline_message_associations.content_row_factory
index = test_timeline_message_associations.index


def prepare(bridge, provider):
    manager, agent = bridge
    agent.provider = provider
    Session.objects.filter(pk=agent.session_id).update(provider=provider.value)
    ProcessRun.objects.filter(pk=agent.process_run.pk).update(state="user_turn")
    agent.process_run.state = "user_turn"
    return manager, agent


def message_ref(provider, name):
    return NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else "codex_client", name)


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_live_inputs_use_each_request_origin_and_exact_identity(bridge, content_row_factory, provider):
    manager, agent = prepare(bridge, provider)
    entries = [
        (UI_ORIGIN, "you"),
        (RequestOrigin("user", "cli"), "you"),
        (RequestOrigin("agent", "cli", "sender"), "agent_cli_mcp"),
        (RequestOrigin("agent", "mcp_internal", "sender-mcp"), "agent_cli_mcp"),
        (RequestOrigin("external", "mcp_external", "connection", "Peer"), "external_mcp"),
        (SYSTEM_ORIGIN, "automatic"),
    ]

    async def admit():
        for number, (origin, _) in enumerate(entries, 1):
            token = str(uuid4())
            with origin_scope(origin):
                agent._completion_input_pending(token, staged=True)
            # Provider delivery can run in a task with a different request context.
            with origin_scope(UI_ORIGIN):
                agent._completion_input_pending(token)
                agent._timeline_reserve_input(token, message_ref(provider, f"input-{number}"))
                agent._completion_input_settled(token, "accepted")
        agent._completion_activity()
        agent._cancel_completion_timer()

    asyncio.run(admit())
    for line in range(1, len(entries) + 2):
        index(agent, content_row_factory, message_ref(provider, f"input-{line}"), line, "Identical text")
    record_indexed_messages(agent.session_id, new_line_nums=list(range(1, len(entries) + 2)),
                            timeline_since=timezone.now() - timedelta(seconds=10),
                            get_input_origins=lambda: manager.timeline_input_origins(agent.session_id))
    events = list(TimelineEvent.objects.filter(kind=TimelineKind.USER_MESSAGE_SENT).order_by("id"))
    assert len(events) == len(entries) + 1
    for event, (origin, author) in zip(events, entries, strict=False):
        assert (event.actor, event.via, event.actor_ref, event.actor_name) == tuple(origin)
        assert author_of(event.__dict__) == author
        assert TimelineEvent.objects.filter(pk=event.pk).filter(author_q(author)).exists()
    assert author_of(events[-1].__dict__) == "unknown", "matching text never proves the sender"


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_delayed_source_keeps_origin_after_turn_publication_and_journal_cleanup(bridge, content_row_factory, provider):
    manager, agent = prepare(bridge, provider)
    token = str(uuid4())
    ref = message_ref(provider, "late-input")
    origin = RequestOrigin("agent", "mcp_internal", "sending-session")

    async def close():
        with origin_scope(origin):
            agent._completion_input_pending(token)
            agent._timeline_reserve_input(token, ref)
        agent._completion_activity()
        agent._completion_input_settled(token, "accepted")
        await manager._persist_process_run_transition(agent, AgentState.ASSISTANT_TURN)
        snapshot = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, snapshot, state=AgentState.USER_TURN)
        agent._cancel_completion_timer()

    asyncio.run(close())
    assert manager.timeline_input_origins(agent.session_id) == []
    LogicalTurn.objects.all().delete()
    manager._agents.clear()
    index(agent, content_row_factory, ref, 1, "Late source")
    recover_referenced_messages(agent.session_id)
    event = TimelineEvent.objects.get(kind=TimelineKind.USER_MESSAGE_SENT)
    assert (event.actor, event.via, event.actor_ref, event.actor_name) == tuple(origin)
    assert author_of(event.__dict__) == "agent_cli_mcp"


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_existing_unknown_message_is_never_reclassified(bridge, content_row_factory, provider):
    manager, agent = prepare(bridge, provider)
    ref = message_ref(provider, "already-recorded")
    index(agent, content_row_factory, ref, 1, "Existing source")
    boundary = timezone.now() - timedelta(seconds=10)
    record_indexed_messages(agent.session_id, new_line_nums=[1], timeline_since=boundary)
    before = TimelineEvent.objects.values().get(kind=TimelineKind.USER_MESSAGE_SENT)

    async def admit():
        with origin_scope(UI_ORIGIN):
            token = str(uuid4())
            agent._completion_input_pending(token)
            agent._timeline_reserve_input(token, ref)
        agent._cancel_completion_timer()

    asyncio.run(admit())
    record_indexed_messages(agent.session_id, new_line_nums=[1], timeline_since=boundary,
                            get_input_origins=lambda: manager.timeline_input_origins(agent.session_id))
    after = TimelineEvent.objects.values().get(kind=TimelineKind.USER_MESSAGE_SENT)
    assert after == before


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
@pytest.mark.parametrize("mismatch", ["source_floor", "storage_incarnation", "history_epoch"])
def test_another_source_scope_cannot_attribute_a_message(bridge, content_row_factory, provider, mismatch):
    manager, agent = prepare(bridge, provider)
    ref = message_ref(provider, "scoped")
    index(agent, content_row_factory, ref, 1, "Same native ID")
    proof = {"ref": ref._asdict(), "origin": UI_ORIGIN._asdict(), "source_floor": 0,
             "storage_incarnation": None, "history_epoch": None}
    proof[mismatch] = {"source_floor": 1, "storage_incarnation": "another-source", "history_epoch": 999}[mismatch]
    record_indexed_messages(agent.session_id, new_line_nums=[1], timeline_since=timezone.now() - timedelta(seconds=10),
                            get_input_origins=lambda: [proof])
    event = TimelineEvent.objects.get(kind=TimelineKind.USER_MESSAGE_SENT)
    assert author_of(event.__dict__) == "unknown"
