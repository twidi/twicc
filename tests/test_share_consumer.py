import asyncio

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings as django_settings
from django.utils import timezone as djtz

from twicc.core.models import Project, Session, SessionType, Share
from twicc.core.services.share_tokens import mint_token
from twicc.share.consumer import ShareConsumer


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def session(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-cons", directory="/tmp/cons")
    return Session.objects.create(
        id="sess-cons", project=project, provider="claude_code",
        file_path="sess-cons.jsonl", type=SessionType.SESSION, title="Cons",
        created_at=now, last_new_content_at=now, user_message_count=1, last_line=5,
        compute_version=django_settings.CLAUDE_CODE_COMPUTE_VERSION,
    )


def _share(session, **kw):
    return Share.objects.create(kind="session", token=mint_token(), session=session, **kw)


def _communicator(token):
    comm = WebsocketCommunicator(ShareConsumer.as_asgi(), f"/ws/share/{token}/")
    comm.scope["url_route"] = {"kwargs": {"token": token}}
    return comm


def test_live_share_accepts_and_filters_debug(session):
    # Share created in the sync test body (async ORM in the consumer is fine).
    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid,
            "items": [
                {"line_num": 1, "display_level": 3, "content": "{}", "kind": "system"},
                {"line_num": 2, "display_level": 1, "content": "{}", "kind": "user_message"},
            ],
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_items_added"
        # DEBUG_ONLY (level 3) filtered before it ever reaches the viewer's socket.
        assert [it["line_num"] for it in msg["items"]] == [2]
        await comm.disconnect()

    _run(scenario())


def test_snapshot_share_rejected(session):
    share = _share(session, options={"mode": "snapshot", "frozen_at_line": 3})

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert not connected  # snapshot shares never stream
        await comm.disconnect()

    _run(scenario())


def test_obsolete_root_share_rejected(session):
    session.compute_version -= 1
    session.save(update_fields=["compute_version"])
    share = _share(session, options={"mode": "live"})

    async def scenario():
        communicator = _communicator(share.token)
        connected, _ = await communicator.connect()
        assert not connected

    _run(scenario())


def test_revoked_share_rejected(session):
    share = _share(session, options={"mode": "live"}, revoked_at=djtz.now())

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert not connected
        await comm.disconnect()

    _run(scenario())


def test_tool_state_forwarded_with_visibility_check(session):
    from twicc.core.models import SessionItem, ToolResultLink

    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    sid = session.id
    # Visible tool_use (level 1) at line 2; hidden one (DEBUG_ONLY) at line 4.
    SessionItem.objects.create(session=session, line_num=2, content="{}", display_level=1, kind="assistant_message")
    SessionItem.objects.create(session=session, line_num=4, content="{}", display_level=3, kind="assistant_message")
    ToolResultLink.objects.create(session=session, tool_use_line_num=2, tool_result_line_num=3,
                                  tool_use_id="tu-vis", tool_result_at=djtz.now())
    ToolResultLink.objects.create(session=session, tool_use_line_num=4, tool_result_line_num=5,
                                  tool_use_id="tu-hid", tool_result_at=djtz.now(), error="hidden detail")

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": sid, "tool_use_id": "tu-vis",
            "result_count": 1, "completed_at": None, "extra": None, "error": None,
            "tool_result_line_nums": [3],
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_tool_state"
        assert msg["tool_use_id"] == "tu-vis"
        assert msg["result_count"] == 1
        # Over-ceiling tool_use: dropped (its error text must not leak).
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": sid, "tool_use_id": "tu-hid",
            "result_count": 1, "completed_at": None, "extra": None, "error": "hidden detail",
            "tool_result_line_nums": [5],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        # Another session's tool_state: dropped.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "tool_state", "session_id": "some-other-session", "tool_use_id": "tu-x",
            "result_count": 1, "completed_at": None, "extra": None, "error": None,
            "tool_result_line_nums": [1],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_share_updated_refreshes_open_socket_filters(session):
    # Review finding: an owner tightening a live share's options must apply to
    # already-connected viewers — the socket's connect-time ceiling is stale.
    share = _share(session, options={"mode": "live", "max_display_mode": "debug"})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        item = {"line_num": 1, "display_level": 3, "content": "{}", "kind": "system"}
        # Debug ceiling: level-3 forwarded.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid, "items": [item]}})
        assert (await comm.receive_json_from(timeout=2))["type"] == "share_items_added"
        # Owner tightens to normal → socket refreshes its filters + pushes meta.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "share_updated",
            "share": {"id": share.id, "status": "active",
                      "options": {"mode": "live", "max_display_mode": "normal"}},
        }})
        assert (await comm.receive_json_from(timeout=2))["type"] == "share_meta"
        # Level-3 traffic is now dropped by the refreshed ceiling.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": sid, "items": [item]}})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_process_state_forwarded_root_only(session):
    # Live "is thinking" indicator: the root session's process_state is forwarded
    # slim (state only); another session's is dropped.
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "process_state", "session_id": sid, "state": "assistant_turn",
            "label": "compacting", "tools": ["Bash"],  # owner-only detail, must be stripped
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg == {"type": "share_process_state", "state": "assistant_turn"}
        # Another session's process_state: dropped.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "process_state", "session_id": "some-other-session", "state": "assistant_turn"}})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_agent_link_forwarded_for_live_spawned_subagent(session):
    # A subagent spawned after page load must become openable: the consumer both
    # tracks it as a descendant AND pushes a viewer link so "View Agent" resolves.
    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id
    Session.objects.create(
        id="sub-1",
        project=session.project,
        provider=session.provider,
        type=SessionType.SUBAGENT,
        parent_session=session,
        compute_version=session.compute_version,
    )

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "agent_link_created", "parent_session_id": sid,
            "agent_session_id": "sub-1", "agent_slug": "explorer",
            "tool_use_id": "tu-agent", "tool_use_line_num": 3,
            "is_background": False, "started_at": None,
        }})
        msg = await comm.receive_json_from(timeout=2)
        assert msg["type"] == "share_agent_link"
        assert msg["link"] == {
            "owner_session_id": sid, "root_session_id": sid,
            "agent_id": "sub-1", "agent_slug": "explorer", "tool_use_id": "tu-agent",
            "tool_use_line_num": 3, "is_background": False, "started_at": None,
        }
        # The new subagent is now a tracked descendant: its items forward too.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": "sub-1",
            "items": [{"line_num": 1, "display_level": 1, "content": "{}", "kind": "assistant_message"}],
        }})
        items_msg = await comm.receive_json_from(timeout=2)
        assert items_msg["type"] == "share_items_added" and items_msg["session_id"] == "sub-1"
        await comm.disconnect()

    _run(scenario())


def test_agent_link_not_forwarded_when_subagents_disabled(session):
    share = _share(session, options={"mode": "live", "include_subagents": False})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "agent_link_created", "parent_session_id": sid,
            "agent_session_id": "sub-1", "tool_use_id": "tu-agent", "tool_use_line_num": 3,
        }})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_obsolete_descendant_items_are_not_forwarded(session):
    sub = Session.objects.create(
        id="sub-stale",
        project=session.project,
        provider=session.provider,
        type=SessionType.SUBAGENT,
        parent_session=session,
        compute_version=session.compute_version - 1,
    )
    share = _share(session, options={"mode": "live", "include_subagents": True})

    async def scenario():
        communicator = _communicator(share.token)
        connected, _ = await communicator.connect()
        assert connected
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added",
            "session_id": sub.id,
            "items": [{"line_num": 1, "display_level": 1, "content": "{}"}],
        }})
        assert await communicator.receive_nothing(timeout=0.5)
        await communicator.disconnect()

    _run(scenario())


def test_unrelated_session_items_not_forwarded(session):
    share = _share(session, options={"mode": "live", "max_display_mode": "normal"})

    async def scenario():
        comm = _communicator(share.token)
        connected, _ = await comm.connect()
        assert connected
        layer = get_channel_layer()
        # A different session's items must be dropped by the server-side filter.
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": "some-other-session",
            "items": [{"line_num": 1, "display_level": 1, "content": "{}", "kind": "user_message"}],
        }})
        assert await comm.receive_nothing(timeout=0.5)
        await comm.disconnect()

    _run(scenario())


def test_password_change_keeps_open_socket_streaming(session, monkeypatch):
    """§14 Update shape / §8: replacing a password never cuts an open live WS —
    the socket receives share_updated, then still streams later session items."""
    from twicc.auth.hashers import hash_password
    from twicc.core.services import share_mutation
    from twicc.core.services.share_tokens import password_fingerprint
    from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough)

    share = _share(session, options={"mode": "live"},
                   password_hash=hash_password("old-pw"))

    async def scenario():
        comm = _communicator(share.token)
        comm.scope["session"] = {
            SHARE_GRANTS_SESSION_KEY: {share.id: password_fingerprint(share.password_hash)}}
        connected, _ = await comm.connect()
        assert connected
        old_hash = share.password_hash
        result = await share_mutation.patch_share(share, {"password": "new-pw"})
        assert result.success
        assert share.password_hash != old_hash          # grants invalidated
        msg = await comm.receive_json_from(timeout=2)   # broadcast reached the socket
        assert msg["type"] == "share_meta"
        # Prove the separate item-streaming branch still works after the
        # password update. Receiving share_meta alone does not prove this.
        item = {
            "line_num": 6, "display_level": 1, "content": "{}",
            "kind": "assistant_message",
        }
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": session.id,
            "items": [item],
        }})
        streamed = await comm.receive_json_from(timeout=2)
        assert streamed == {
            "type": "share_items_added", "session_id": session.id,
            "items": [item],
        }
        await comm.disconnect()

    _run(scenario())


def test_password_change_gates_new_connects_on_new_fingerprint(session):
    """§14 Update shape / §8: after a password change, a connect carrying the
    OLD grant fingerprint is refused; one carrying the new fingerprint passes."""
    from twicc.auth.hashers import hash_password
    from twicc.core.services.share_tokens import password_fingerprint
    from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

    share = _share(session, options={"mode": "live"},
                   password_hash=hash_password("old-pw"))
    old_fp = password_fingerprint(share.password_hash)
    share.password_hash = hash_password("new-pw")
    share.save(update_fields=["password_hash"])
    new_fp = password_fingerprint(share.password_hash)

    async def scenario():
        stale = _communicator(share.token)
        stale.scope["session"] = {SHARE_GRANTS_SESSION_KEY: {share.id: old_fp}}
        connected, _ = await stale.connect()
        assert not connected                            # old grant is dead
        fresh = _communicator(share.token)
        fresh.scope["session"] = {SHARE_GRANTS_SESSION_KEY: {share.id: new_fp}}
        connected2, _ = await fresh.connect()
        assert connected2                               # new password's grant works
        await fresh.disconnect()

    _run(scenario())


# ---------------------------------------------------------------------------
# Subagent runs: share_agent_run_state / share_agent_interaction (design §7.3)
# ---------------------------------------------------------------------------

RUN_STATE_KEYS = {
    "type", "root_session_id", "agent_session_id", "running", "run_started_at", "run_background", "runs",
}
INTERACTION_KEYS = {
    "type", "root_session_id", "owner_session_id", "agent_session_id", "tool_use_id", "tool_use_line_num",
    "kind", "opens_run", "started_at",
}


def _subagent(session, agent_id):
    return Session.objects.create(
        id=agent_id, project=session.project, provider=session.provider, type=SessionType.SUBAGENT,
        parent_session=session, file_path=f"{agent_id}.jsonl", compute_version=session.compute_version,
    )


def _run_state(root_id, agent_id="sub-a"):
    return {
        "type": "agent_run_state", "root_session_id": root_id, "agent_session_id": agent_id,
        "running": True, "run_started_at": "2026-09-27T10:00:00+00:00", "run_background": True,
        "runs": [{"owner_session_id": root_id, "tool_use_id": "tu-spawn", "started_at": "2026-09-27T10:00:00+00:00",
                  "open": True, "closed_at": None}],
        "project_id": "-tmp-cons",
    }


def _interaction(root_id, owner_id, agent_id="sub-a", line_num=2, tool_use_id="tu-send"):
    return {
        "type": "agent_interaction", "root_session_id": root_id, "owner_session_id": owner_id,
        "agent_session_id": agent_id, "tool_use_id": tool_use_id, "tool_use_line_num": line_num,
        "kind": "message", "opens_run": True, "started_at": "2026-09-27T10:00:05+00:00",
        "project_id": "-tmp-cons",
    }


def _relayed(data):
    """The flat share message an app payload becomes: its keys minus ``project_id``."""
    kind = {"agent_run_state": "share_agent_run_state", "agent_interaction": "share_agent_interaction"}
    return {**{key: value for key, value in data.items() if key != "project_id"}, "type": kind[data["type"]]}


async def _send(data):
    await get_channel_layer().group_send("updates", {"type": "broadcast", "data": data})


def test_agent_run_state_relayed_flat_for_its_root_only(session):
    share = _share(session, options={"mode": "live", "include_subagents": True})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        await _send(_run_state("other-root"))
        assert await comm.receive_nothing(timeout=0.1)
        await _send(_run_state(sid))
        msg = await comm.receive_json_from(timeout=2)
        assert set(msg) == RUN_STATE_KEYS
        assert msg == _relayed(_run_state(sid))
        await comm.disconnect()

    _run(scenario())


def test_agent_run_state_not_relayed_when_subagents_disabled(session):
    share = _share(session, options={"mode": "live", "include_subagents": False})
    sid = session.id

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        await _send(_run_state(sid))
        await _send(_interaction(sid, sid))
        assert await comm.receive_nothing(timeout=0.2)
        await comm.disconnect()

    _run(scenario())


def test_agent_interaction_relay_filters(session):
    from twicc.core.models import SessionItem

    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id
    _subagent(session, "sub-a")
    owner = _subagent(session, "sub-b")
    SessionItem.objects.create(session=session, line_num=2, content="{}", display_level=1, kind="assistant_message")
    SessionItem.objects.create(session=session, line_num=4, content="{}", display_level=3, kind="assistant_message")
    SessionItem.objects.create(session=owner, line_num=6, content="{}", display_level=2, kind="assistant_message")
    SessionItem.objects.create(session=owner, line_num=8, content="{}", display_level=3, kind="assistant_message")

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        for dropped in (
            _interaction("other-root", sid),                    # another root
            _interaction(sid, sid, agent_id="unknown-agent"),   # target not a descendant
            _interaction(sid, "unknown-owner"),                 # owner neither the root nor a descendant
            _interaction(sid, sid, line_num=4),                 # call over the display ceiling
            _interaction(sid, sid, line_num=3),                 # call item not synced: not visible
            _interaction(sid, "sub-b", line_num=8),             # subagent call over the ceiling
        ):
            await _send(dropped)
        assert await comm.receive_nothing(timeout=0.2)
        for kept in (_interaction(sid, sid), _interaction(sid, "sub-b", line_num=6, tool_use_id="tu-sub")):
            await _send(kept)
            msg = await comm.receive_json_from(timeout=2)
            assert set(msg) == INTERACTION_KEYS
            assert msg == _relayed(kept)
        await comm.disconnect()

    _run(scenario())


def test_agent_interaction_relay_debug_ceiling_skips_the_item_check(session):
    share = _share(session, options={"mode": "live", "max_display_mode": "debug", "include_subagents": True})
    sid = session.id
    _subagent(session, "sub-a")

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        await _send(_interaction(sid, sid, line_num=99))
        assert await comm.receive_json_from(timeout=2) == _relayed(_interaction(sid, sid, line_num=99))
        await comm.disconnect()

    _run(scenario())


@pytest.mark.parametrize("new_agent_is", ["target", "owner"])
def test_late_interaction_passes_after_agent_link_created(session, new_agent_is):
    """§9: the re-send after ``agent_link_created`` passes the descendant filter.

    The viewer connects before the new agent has a ``Session`` row or a link,
    so the agent is not a descendant; its interaction is dropped until the
    ``agent_link_created`` relay adds it.
    """
    from asgiref.sync import sync_to_async

    from twicc.core.models import SessionItem

    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id
    known = _subagent(session, "sub-known")
    SessionItem.objects.create(session=known, line_num=2, content="{}", display_level=1, kind="assistant_message")
    if new_agent_is == "target":
        late = _interaction(sid, "sub-known", agent_id="sub-new")
    else:
        late = _interaction(sid, "sub-new", agent_id="sub-known", line_num=5)

    def new_agent_rows():
        new = _subagent(session, "sub-new")
        SessionItem.objects.create(session=new, line_num=5, content="{}", display_level=1, kind="assistant_message")

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        await sync_to_async(new_agent_rows)()
        await _send(late)
        assert await comm.receive_nothing(timeout=0.2)
        await _send({"type": "agent_link_created", "parent_session_id": sid, "root_session_id": sid,
                     "agent_session_id": "sub-new", "tool_use_id": "tu-spawn-new", "tool_use_line_num": 9,
                     "is_background": True, "started_at": None, "project_id": "-tmp-cons"})
        assert (await comm.receive_json_from(timeout=2))["type"] == "share_agent_link"
        await _send(late)
        assert await comm.receive_json_from(timeout=2) == _relayed(late)
        await comm.disconnect()

    _run(scenario())


def test_relay_seeded_with_links_whose_agent_has_no_session_row(session):
    """§9 "Share relay seeding": a link synced before its agent's ``Session`` row."""
    from twicc.core.models import AgentLink, SessionItem

    share = _share(session, options={"mode": "live", "max_display_mode": "normal", "include_subagents": True})
    sid = session.id
    AgentLink.objects.create(session=session, agent_id="sub-rowless", tool_use_id="tu-spawn",
                             tool_use_line_num=1, started_at=djtz.now())
    SessionItem.objects.create(session=session, line_num=2, content="{}", display_level=1, kind="assistant_message")
    assert not Session.objects.filter(id="sub-rowless").exists()
    interaction = _interaction(sid, sid, agent_id="sub-rowless")

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        await _send(interaction)
        assert await comm.receive_json_from(timeout=2) == _relayed(interaction)
        await comm.disconnect()

    _run(scenario())


def test_inline_event_uses_fresh_sanitized_manifest(session):
    from asgiref.sync import sync_to_async
    share = _share(session, options={'mode': 'live'})
    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        layer = get_channel_layer()
        await layer.group_send('updates', {'type': 'broadcast', 'data': {
            'type': 'share_inline_artifacts', 'share_id': share.id,
            'manifest': {'private_path': '/secret', 'copy_id': 'secret'}, 'paths': ['/secret'],
        }})
        result = await comm.receive_json_from(timeout=2)
        assert result == {'type': 'share_inline_artifacts', 'share_id': share.id,
                          'manifest': {'enabled': True, 'revision': 1, 'artifacts': []}}
        await sync_to_async(Share.objects.filter(id=share.id).update)(options={'mode': 'live',
                                                                           'include_inline_artifacts': False})
        await layer.group_send('updates', {'type': 'broadcast', 'data': {
            'type': 'share_inline_artifacts', 'share_id': share.id}})
        result = await comm.receive_json_from(timeout=2)
        assert result['manifest']['enabled'] is False
        await sync_to_async(Share.objects.filter(id=share.id).update)(revoked_at=djtz.now())
        await layer.group_send('updates', {'type': 'broadcast', 'data': {
            'type': 'share_inline_artifacts', 'share_id': share.id}})
        assert await comm.receive_nothing(timeout=.1)
        await comm.disconnect()
    _run(scenario())


def test_active_live_to_snapshot_sends_metadata_without_revoking_viewer(session):
    from asgiref.sync import sync_to_async

    share = _share(session, options={'mode': 'live', 'include_inline_artifacts': True})

    async def scenario():
        comm = _communicator(share.token)
        assert (await comm.connect())[0]
        share.options = {'mode': 'snapshot', 'frozen_at_line': 5, 'include_inline_artifacts': True}
        await sync_to_async(share.save)(update_fields=['options'])
        await get_channel_layer().group_send('updates', {'type': 'broadcast', 'data': {
            'type': 'share_updated', 'share': {'id': share.id, 'status': 'active', 'options': share.options}}})
        event = await comm.receive_json_from(timeout=1)
        assert event['type'] == 'share_meta', 'active snapshot adoption is not revocation'
        assert event['meta']['mode'] == 'snapshot'
        assert event['meta']['last_line'] == 5
        output = await comm.receive_output(timeout=1)
        assert output['type'] == 'websocket.close'
        assert output['code'] == 1000
        await comm.disconnect()

    _run(scenario())
