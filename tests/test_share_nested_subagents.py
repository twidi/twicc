"""Nested share visibility and live queue completion at actual HTTP/WS boundaries."""
import asyncio

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import AsyncClient

from twicc.core.models import AgentLink, Session, SessionType, Share
from twicc.core.services.share_tokens import mint_token
from twicc.share.consumer import ShareConsumer
from tests.test_subagents_tree_endpoint import NOW, queue
from tests import test_subagents_tree_endpoint

tree = test_subagents_tree_endpoint.tree  # Register the shared pytest fixture.


def share_for(root, **options):
    return Share.objects.create(kind="session", session=root, token=mint_token(), options=options)


@pytest.mark.parametrize("frozen,visible", [(100, False), (119, True), (200, True)])
def test_snapshot_ownership_visibility_and_all_content_routes(tree, frozen, visible):
    root, launcher, child = tree
    share = share_for(root, mode="snapshot", frozen_at_line=frozen, include_subagents=True)
    client = AsyncClient()
    response = asyncio.run(client.get(f"/share/{share.token}/api/subagents/"))
    assert response.status_code == 200
    assert {row["agent_id"] for row in response.json()} == ({launcher.id, child.id} if visible else set())
    for endpoint in ("items/metadata/", "items/?range=1:100", "tool-states/", "items/1/tool-results/absent/"):
        response = asyncio.run(client.get(f"/share/{share.token}/api/subagent/{child.id}/{endpoint}"))
        assert response.status_code == (200 if visible else 404), endpoint


def test_include_off_hides_all_depths(tree):
    root, launcher, child = tree
    share = share_for(root, mode="live", include_subagents=False)
    client = AsyncClient()
    assert asyncio.run(client.get(f"/share/{share.token}/api/subagents/")).json() == []
    for target in (launcher, child):
        assert asyncio.run(client.get(
            f"/share/{share.token}/api/subagent/{target.id}/items/metadata/"
        )).status_code == 404


def test_snapshot_state_does_not_read_post_freeze_root_completion(tree):
    root, launcher, child = tree
    queue(root, child, line=150)
    share = share_for(root, mode="snapshot", frozen_at_line=120, include_subagents=True)
    rows = asyncio.run(AsyncClient().get(f"/share/{share.token}/api/subagents/")).json()
    assert next(row for row in rows if row["agent_id"] == child.id)["stopped_at"] is None


def test_deep_snapshot_and_unreachable_cycle(tree, settings):
    root, launcher, child = tree
    parent = child
    for i in range(40):
        node = Session.objects.create(id=f"deep-{i}", project=root.project, file_path=f"deep/{i}.jsonl",
            provider=root.provider, type=SessionType.SUBAGENT, parent_session=root,
            compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION)
        AgentLink.objects.create(session=parent, agent_id=node.id, tool_use_id=f"deep-tool-{i}",
            tool_use_line_num=1, started_at=NOW)
        parent = node
    share = share_for(root, mode="snapshot", frozen_at_line=120, include_subagents=True)
    client = AsyncClient()
    assert asyncio.run(client.get(
        f"/share/{share.token}/api/subagent/{parent.id}/items/metadata/"
    )).status_code == 200
    AgentLink.objects.filter(session=root).delete()
    AgentLink.objects.create(session=parent, agent_id=launcher.id, tool_use_id="cycle", tool_use_line_num=1)
    assert asyncio.run(client.get(f"/share/{share.token}/api/subagents/")).json() == []
    assert asyncio.run(client.get(
        f"/share/{share.token}/api/subagent/{parent.id}/items/metadata/"
    )).status_code == 404


def test_live_share_nested_link_and_queue_only_stop(tree):
    root, launcher, child = tree
    share = share_for(root, mode="live", include_subagents=True)

    async def scenario():
        comm = WebsocketCommunicator(ShareConsumer.as_asgi(), f"/ws/share/{share.token}/")
        comm.scope["url_route"] = {"kwargs": {"token": share.token}}
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        for event in (
            {"type": "agent_link_created", "parent_session_id": launcher.id, "root_session_id": root.id,
             "agent_session_id": child.id, "tool_use_id": "spawn-child", "is_background": True},
            {"type": "agent_stopped", "agent_session_id": child.id,
             "root_session_id": root.id, "stopped_at": NOW.isoformat()},
        ):
            await layer.group_send("updates", {"type": "broadcast", "data": event})
            message = await comm.receive_json_from(timeout=2)
            if event["type"] == "agent_link_created":
                assert message["link"]["owner_session_id"] == launcher.id
            else:
                assert message["type"] == "share_agent_stopped"
                assert message["stopped_at"] == NOW.isoformat()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "agent_stopped", "agent_session_id": "foreign", "root_session_id": "foreign-root",
            "stopped_at": NOW.isoformat(),
        }})
        assert await comm.receive_nothing(timeout=0.05)
        await comm.disconnect()

    asyncio.run(scenario())


def test_public_meta_carries_root_lifecycle(tree):
    root, launcher, child = tree
    root.last_started_at = root.last_stopped_at = NOW
    root.save(update_fields=["last_started_at", "last_stopped_at"])
    share = share_for(root, mode="live", include_subagents=True)
    meta = asyncio.run(AsyncClient().get(f"/share/{share.token}/api/meta/")).json()
    assert meta["last_started_at"] == meta["last_stopped_at"] == NOW.isoformat()


def test_snapshot_rejects_conflicting_ownership_and_reachable_cycle(tree):
    root, launcher, child = tree
    # A back edge gives the already visible launcher two owners.
    AgentLink.objects.create(session=child, agent_id=launcher.id, tool_use_id="back-edge", tool_use_line_num=2)
    share = share_for(root, mode="snapshot", frozen_at_line=120, include_subagents=True)
    client = AsyncClient()
    assert asyncio.run(client.get(f"/share/{share.token}/api/subagents/")).json() == []
    assert asyncio.run(client.get(
        f"/share/{share.token}/api/subagent/{child.id}/items/metadata/"
    )).status_code == 404


def test_live_share_transports_child_idle_and_wake_without_cost(tree):
    root, launcher, child = tree
    share = share_for(root, mode="live", include_subagents=True)

    async def scenario():
        comm = WebsocketCommunicator(ShareConsumer.as_asgi(), f"/ws/share/{share.token}/")
        comm.scope["url_route"] = {"kwargs": {"token": share.token}}
        assert (await comm.connect())[0]
        layer = get_channel_layer()
        for stopped in (NOW.isoformat(), None):
            await layer.group_send("updates", {"type": "broadcast", "data": {
                "type": "session_updated", "session": {"id": child.id,
                    "last_stopped_at": stopped, "total_cost": 123},
            }})
            assert await comm.receive_json_from(timeout=2) == {
                "type": "share_agent_idle", "agent_session_id": child.id,
                "agent_stopped_at": stopped, "root_session_id": root.id,
            }
        await comm.disconnect()

    asyncio.run(scenario())


def test_root_back_edge_never_exposes_root_as_subagent(tree):
    root, launcher, child = tree
    AgentLink.objects.create(session=launcher, agent_id=root.id, tool_use_id="root-back-edge", tool_use_line_num=2)
    for mode in ("live", "snapshot"):
        share = share_for(root, mode=mode, frozen_at_line=120, include_subagents=True)
        rows = asyncio.run(AsyncClient().get(f"/share/{share.token}/api/subagents/")).json()
        assert root.id not in {row["agent_id"] for row in rows}
