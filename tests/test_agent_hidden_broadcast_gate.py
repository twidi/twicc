"""A hidden session must emit no live update — streaming included.

Every other emitter already obeys this (``process_state`` in ``twicc.asgi``,
the watcher in ``sessions_watcher``, the archive broadcast in
``session_update``). The agent's own funnel, ``BaseAgent._broadcast_stream_event``,
did not: it carries ``stream_block_*`` (one frame per streamed token, from every
live agent at once) plus ``process_label`` and the command-done signals.

Those frames share the single bounded per-client queue that also carries
``session_removed``, so an ungated stream can crowd out the very message that
tells the browser the session is gone.

The gate reads a cached flag rather than the DB, because the streaming path
cannot afford a query per token. These tests pin both halves: the gate itself,
and the push that keeps the cache exact.
"""

import asyncio
from unittest.mock import patch

import pytest

from twicc.agent.base_agent import BaseAgent
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.providers.helpers import AgentSettings


class _Agent(BaseAgent):
    """Minimal concrete agent — the funnel and the gate live on the base class."""

    provider = Provider.CODEX

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _make_agent(session_id="s-1"):
    return _Agent(
        session_id=session_id,
        project_id="-p",
        cwd="/tmp",
        agent_settings=AgentSettings(),
    )


class _Layer:
    """Channel layer stub recording what reached the group."""

    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))


def _run_broadcast(agent, data):
    layer = _Layer()
    with patch("twicc.agent.base_agent.get_channel_layer", return_value=layer):
        asyncio.run(agent._broadcast_stream_event(data))
    return layer.sent


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_visible_session_streams():
    agent = _make_agent()
    agent.set_hidden(False)

    sent = _run_broadcast(agent, {"type": "stream_block_delta", "text": "hi"})

    assert len(sent) == 1
    assert sent[0][0] == "updates"
    assert sent[0][1]["data"]["type"] == "stream_block_delta"


def test_hidden_session_streams_nothing():
    agent = _make_agent()
    agent.set_hidden(True)

    assert _run_broadcast(agent, {"type": "stream_block_delta", "text": "hi"}) == []


def test_gate_covers_every_emitter_on_the_funnel():
    """process_label and the command-done signals ride the same funnel."""
    agent = _make_agent()
    agent.set_hidden(True)

    layer = _Layer()
    with patch("twicc.agent.base_agent.get_channel_layer", return_value=layer):
        asyncio.run(agent._broadcast_process_label("compacting"))
        asyncio.run(agent._broadcast_stream_event({"type": "manual_compaction_done"}))
        asyncio.run(agent._broadcast_stream_event({"type": "stream_block_start"}))

    assert layer.sent == []


def test_mid_stream_flip_stops_the_next_frame():
    """The flag flips while the agent is streaming — the incident's exact shape."""
    agent = _make_agent()
    agent.set_hidden(False)

    before = _run_broadcast(agent, {"type": "stream_block_delta", "text": "a"})
    agent.set_hidden(True)
    after = _run_broadcast(agent, {"type": "stream_block_delta", "text": "b"})

    assert len(before) == 1
    assert after == []


# ---------------------------------------------------------------------------
# The cache — resolution and staleness
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_flag_resolves_from_the_db_once_then_stops_querying():
    project = Project.objects.create(id="-p", directory="/tmp")
    Session.objects.create(
        id="s-db", project=project, provider=Provider.CODEX.value,
        type=SessionType.SESSION, hidden=True,
    )
    agent = _make_agent("s-db")

    async def scenario():
        first = await agent._is_session_hidden()
        second = await agent._is_session_hidden()
        return first, second

    first, second = asyncio.run(scenario())
    assert (first, second) == (True, True)

    # Cached: deleting the row cannot change the answer any more.
    Session.objects.filter(id="s-db").delete()
    assert asyncio.run(agent._is_session_hidden()) is True


@pytest.mark.django_db(transaction=True)
def test_missing_row_reads_as_visible_and_is_not_cached():
    """The row only appears when the watcher ingests the first JSONL line.

    Caching that transient "unknown" would latch a session created hidden as
    visible forever — no flip follows a creation, so no push would fix it.
    """
    agent = _make_agent("s-late")

    assert asyncio.run(agent._is_session_hidden()) is False
    assert agent._hidden is None, "an unknown session must stay unresolved"

    project = Project.objects.create(id="-p", directory="/tmp")
    Session.objects.create(
        id="s-late", project=project, provider=Provider.CODEX.value,
        type=SessionType.SESSION, hidden=True,
    )

    assert asyncio.run(agent._is_session_hidden()) is True


@pytest.mark.django_db(transaction=True)
def test_a_push_wins_over_a_stale_db_read():
    """set_hidden landing during the read must not be overwritten by it."""
    project = Project.objects.create(id="-p", directory="/tmp")
    Session.objects.create(
        id="s-race", project=project, provider=Provider.CODEX.value,
        type=SessionType.SESSION, hidden=False,
    )
    agent = _make_agent("s-race")

    # Simulate the push arriving while the DB read is in flight.
    agent.set_hidden(True)

    assert asyncio.run(agent._is_session_hidden()) is True
    assert agent._hidden is True


# ---------------------------------------------------------------------------
# The push — service to agent, end to end
# ---------------------------------------------------------------------------


def test_manager_pushes_the_flag_into_its_agent():
    """The registry hop the visibility service relies on."""
    from twicc.agent.registry import AgentManagerRegistry

    agent = _make_agent("s-mgr")

    class _Manager:
        provider = Provider.CODEX

        def set_session_hidden(self, session_id, hidden):
            if session_id != agent.session_id:
                return False
            agent.set_hidden(hidden)
            return True

    registry = AgentManagerRegistry()
    with patch.object(registry, "find_manager_for_session", return_value=_Manager()):
        assert registry.set_session_hidden("s-mgr", True) is True

    assert agent._hidden is True


@pytest.mark.django_db(transaction=True)
def test_hide_session_mutes_the_live_agent_before_the_recompute():
    """End to end: the flip reaches the agent, and early enough to matter.

    The recompute step reindexes the whole session and can take seconds. A
    streaming agent muted only afterwards would keep competing with the
    ``session_removed`` frame for the same bounded queue.
    """
    from twicc.core.services import session_visibility

    project = Project.objects.create(id="-p", directory="/tmp")
    session = Session.objects.create(
        id="s-e2e", project=project, provider=Provider.CODEX.value,
        type=SessionType.SESSION, hidden=False, permission_mode="yolo",
        question_widget=False,
    )

    calls = []

    def _push(session_id, hidden):
        calls.append(("push", session_id, hidden))

    async def _removed(session_id):
        calls.append(("session_removed", session_id))

    async def _project(project_id):
        calls.append(("project_updated", project_id))

    with patch.object(session_visibility, "_push_hidden_to_live_agent", _push), \
            patch.object(session_visibility, "_broadcast_session_removed", _removed), \
            patch.object(session_visibility, "_broadcast_project_updated", _project):
        async def hide_with_writer():
            from twicc.providers import db_writer
            db_writer.start_db_writer()
            try:
                return await session_visibility.hide_session(session)
            finally:
                await db_writer.stop_db_writer()
        result = asyncio.run(hide_with_writer())

    assert result.success, result.errors
    session.refresh_from_db()
    assert session.hidden is True

    # The mute lands first — before the recompute, and before session_removed
    # is queued behind whatever the agent was still streaming.
    assert calls == [
        ("push", "s-e2e", True),
        ("session_removed", "s-e2e"),
        ("project_updated", "-p"),
    ]
