"""The spawned-session ancestors carried by the live process payloads.

The Orchestration tab's activity indicator counts the live processes under the
current session. The frontend cannot rebuild that sub-hierarchy from the rows
it holds (unloaded projects, later pages, hidden or dead intermediates), so the
backend states each process's chain of ancestors, from its parent up to the
spawn root.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.agent.states import AgentInfo, AgentState
from twicc.asgi import WSConsumer, broadcast_process_state
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.core.spawn_ancestors import clear_spawn_ancestors_cache, get_spawn_ancestors
from twicc.pending_session_attributes import (
    pop_pending_session_attributes,
    set_pending_session_attributes,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_spawn_ancestors_cache()
    yield
    clear_spawn_ancestors_cache()


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-spawn", directory="/tmp/spawn")


def _session(project, sid, *, spawned_by=None, hidden=False):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider=Provider.CODEX.value,
        file_path=f"{sid}.jsonl", type=SessionType.SESSION, title=sid,
        created_at=now, last_new_content_at=now, user_message_count=1,
        hidden=hidden, spawned_by_id=spawned_by,
    )


def _tree(project):
    """root -> manager -> worker, root -> sibling."""
    _session(project, "root")
    _session(project, "manager", spawned_by="root")
    _session(project, "worker", spawned_by="manager")
    _session(project, "sibling", spawned_by="root")


def _info(session_id, state=AgentState.ASSISTANT_TURN):
    return AgentInfo(
        session_id=session_id,
        project_id="-tmp-spawn",
        provider=Provider.CODEX,
        state=state,
        previous_state=AgentState.STARTING,
        started_at=1.0,
        state_changed_at=2.0,
        last_activity=2.0,
    )


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_the_chain_runs_from_the_parent_up_to_the_root(project):
    _tree(project)

    assert get_spawn_ancestors("worker") == ("manager", "root")
    assert get_spawn_ancestors("manager") == ("root",)
    assert get_spawn_ancestors("sibling") == ("root",)
    assert get_spawn_ancestors("root") == ()


def test_a_chain_read_from_rows_is_cached(project):
    _tree(project)
    assert get_spawn_ancestors("worker") == ("manager", "root")

    # spawned_by is immutable: the rows are not read again.
    Session.objects.filter(id__in=["worker", "manager"]).delete()
    assert get_spawn_ancestors("worker") == ("manager", "root")


def test_a_session_without_a_row_reads_its_parent_from_the_pending_attributes(project):
    _tree(project)
    set_pending_session_attributes("new-worker", spawned_by_id="manager", spawn_root_id="root")
    try:
        assert get_spawn_ancestors("new-worker") == ("manager", "root")
    finally:
        pop_pending_session_attributes("new-worker")

    # Not cached: the pending entry is gone and there is still no row.
    assert get_spawn_ancestors("new-worker") == ()


def test_a_hidden_intermediate_stays_in_the_chain(project):
    """Hidden only keeps a session's OWN activity out of the indicator."""
    _session(project, "root")
    _session(project, "hidden-manager", spawned_by="root", hidden=True)
    _session(project, "visible-worker", spawned_by="hidden-manager")

    assert get_spawn_ancestors("visible-worker") == ("hidden-manager", "root")


def test_a_cycle_ends_the_chain(project):
    _session(project, "a")
    _session(project, "b", spawned_by="a")
    Session.objects.filter(id="a").update(spawned_by_id="b")

    assert get_spawn_ancestors("b") == ("a",)


# ---------------------------------------------------------------------------
# The payloads
# ---------------------------------------------------------------------------


def _broadcast(info):
    layer = SimpleNamespace(group_send=AsyncMock())
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    with patch("twicc.asgi.get_channel_layer", return_value=layer), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch(
                "twicc.asgi.get_session_and_project_display",
                new=AsyncMock(return_value=(None, None, None)),
            ), \
            patch("twicc.asgi.notify_agent_event"):
        asyncio.run(broadcast_process_state(info))
    return layer.group_send.await_args.args[1]["data"]


def test_process_state_carries_the_chain(project):
    _tree(project)

    assert _broadcast(_info("worker"))["spawn_ancestors"] == ["manager", "root"]


def test_process_state_of_an_unspawned_session_has_no_chain(project):
    _tree(project)

    assert "spawn_ancestors" not in _broadcast(_info("root"))


def _communicator():
    comm = WebsocketCommunicator(WSConsumer.as_asgi(), "/ws/?subscribe=active_processes")
    comm.scope["client"] = ("127.0.0.1", 43210)
    return comm


async def _collect(comm, wanted, limit=40):
    for _ in range(limit):
        try:
            msg = await comm.receive_json_from(timeout=2)
        except Exception:
            return None
        if msg.get("type") == wanted:
            return msg
    return None


async def _close(comm):
    try:
        await comm.disconnect()
    except (Exception, asyncio.CancelledError):
        pass


def test_active_processes_carries_the_chain_and_drops_pending_hidden(project):
    _tree(project)
    infos = [_info("root"), _info("worker"), _info("pending-hidden"), _info("pending-visible")]
    registry = Mock()
    registry.get_active_agents.return_value = infos
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())
    set_pending_session_attributes("pending-hidden", hidden=True, spawned_by_id="worker")
    set_pending_session_attributes("pending-visible", spawned_by_id="worker")

    async def scenario():
        comm = _communicator()
        await comm.connect()
        msg = await _collect(comm, "active_processes")
        await _close(comm)
        return msg

    try:
        with patch("twicc.asgi.get_agent_manager_registry", return_value=registry), \
                patch("twicc.asgi.get_provider_helpers", return_value=helpers):
            msg = asyncio.run(scenario())
    finally:
        pop_pending_session_attributes("pending-hidden")
        pop_pending_session_attributes("pending-visible")

    assert msg is not None
    by_id = {p["session_id"]: p for p in msg["processes"]}
    assert set(by_id) == {"root", "worker", "pending-visible"}
    assert "spawn_ancestors" not in by_id["root"]
    assert by_id["worker"]["spawn_ancestors"] == ["manager", "root"]
    assert by_id["pending-visible"]["spawn_ancestors"] == ["worker", "manager", "root"]


def test_active_processes_drops_a_session_hidden_during_the_snapshot(project):
    """The snapshot awaits the display/enrichment lookups between its first and
    final passes; a session hidden in between must not ride along."""
    from asgiref.sync import sync_to_async

    _tree(project)
    registry = Mock()
    registry.get_active_agents.return_value = [_info("root"), _info("worker")]
    helpers = SimpleNamespace(enrich_agent_state=AsyncMock())

    async def display_then_hide(serialized):
        await sync_to_async(lambda: Session.objects.filter(id="worker").update(hidden=True))()
        return {}

    async def scenario():
        comm = _communicator()
        await comm.connect()
        msg = await _collect(comm, "active_processes")
        await _close(comm)
        return msg

    with patch("twicc.asgi.get_agent_manager_registry", return_value=registry), \
            patch("twicc.asgi.get_provider_helpers", return_value=helpers), \
            patch("twicc.asgi.get_bulk_session_and_project_display", new=display_then_hide):
        msg = asyncio.run(scenario())

    assert msg is not None
    assert [p["session_id"] for p in msg["processes"]] == ["root"]
