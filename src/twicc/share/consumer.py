"""Dedicated, server-filtered WS consumer for live session shares (design §10,
O4). Joins the global ``updates`` group but forwards only this share's session
(and, when allowed, its subagents), re-filtered by the display ceiling / frozen
line, re-serialized through the public meta. The viewer never sees the firehose."""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from twicc.core.services.share_tokens import aresolve_share, password_fingerprint
from twicc.share.display import display_ceiling
from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

logger = logging.getLogger(__name__)

WS_CLOSE_SHARE_UNAVAILABLE = 4404


@sync_to_async
def _session_is_ready(session_id: str) -> bool:
    from twicc.core.models import Session
    from twicc.core.serializers import session_compute_ready

    session = Session.objects.filter(id=session_id).first()
    return session is not None and session_compute_ready(session)


class ShareConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token = self.scope["url_route"]["kwargs"]["token"]
        share = await aresolve_share(token)
        if share is None or not share.is_active() or share.kind != "session":
            await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return
        # Snapshot shares never stream.
        opts = share.options or {}
        if opts.get("mode") != "live":
            await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return
        if not await _session_is_ready(share.session_id):
            await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return
        # Per-link password grant (same fingerprint check as HTTP).
        if share.password_hash:
            session = self.scope.get("session")
            grants = await sync_to_async(lambda: session.get(SHARE_GRANTS_SESSION_KEY, {}))() if session else {}
            if grants.get(share.id) != password_fingerprint(share.password_hash):
                await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
                return

        self.share_id = share.id
        self.session_id = share.session_id
        self.max_display_mode = opts.get("max_display_mode", "normal")
        self.include_subagents = opts.get("include_subagents", True)
        self.ceiling = display_ceiling(self.max_display_mode)
        self.descendant_ids = await self._load_descendants()
        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()
        # Seed the current assistant state so a viewer landing mid-turn sees the
        # "is thinking" indicator immediately (process_state is edge-triggered).
        await self._send_current_assistant_state()

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard("updates", self.channel_name)
        except Exception:
            pass

    async def _send_current_assistant_state(self) -> None:
        """Push the session's live process state to a just-connected viewer, so the
        "is thinking" indicator shows even when the page is opened mid-assistant-turn
        (the broadcast below is edge-triggered and would otherwise miss the ongoing
        turn until it ends)."""
        from twicc.agent import serialize_agent_info
        from twicc.agent.registry import get_agent_manager_registry

        try:
            agents = get_agent_manager_registry().get_active_agents()
        except Exception:
            return  # no live registry (e.g. tests) — nothing to seed
        for info in agents:
            if info.session_id == self.session_id:
                state = serialize_agent_info(info).get("state")
                if state:
                    await self.send_json({"type": "share_process_state", "state": state})
                return

    async def _load_descendants(self) -> set[str]:
        if not self.include_subagents:
            return set()
        from twicc.core.models import Session

        ids = await sync_to_async(list)(
            Session.objects.filter(spawn_root_id=self.session_id).values_list("id", flat=True)
        )
        # Also cover direct children not stamped with spawn_root (belt + braces).
        child = await sync_to_async(list)(
            Session.objects.filter(parent_session_id=self.session_id).values_list("id", flat=True)
        )
        return set(ids) | set(child)

    def _visible(self, item: dict) -> bool:
        dl = item.get("display_level")
        if self.ceiling >= 3:
            return True
        return dl is not None and dl <= self.ceiling

    async def _tool_use_visible(self, session_id: str, tool_use_id: str) -> bool:
        """Whether the tool_use item backing a ``tool_state`` broadcast is under the
        ceiling (the broadcast itself carries no display_level — resolve via the
        link's ``tool_use_line_num``)."""
        if self.ceiling >= 3:
            return True
        from twicc.core.models import SessionItem, ToolResultLink

        line = await sync_to_async(
            lambda: ToolResultLink.objects.filter(session_id=session_id, tool_use_id=tool_use_id)
            .values_list("tool_use_line_num", flat=True).first()
        )()
        if line is None:
            return False
        dl = await sync_to_async(
            lambda: SessionItem.objects.filter(session_id=session_id, line_num=line)
            .values_list("display_level", flat=True).first()
        )()
        return dl is not None and dl <= self.ceiling

    async def broadcast(self, event):
        """Server-side filter: forward only this share's traffic."""
        data = event["data"]
        mtype = data.get("type")

        if mtype == "session_items_added":
            sid = data.get("session_id")
            is_root = sid == self.session_id
            is_sub = self.include_subagents and sid in self.descendant_ids
            if not (is_root or is_sub):
                return
            if not await _session_is_ready(sid):
                return
            items = [it for it in (data.get("items") or []) if self._visible(it)]
            if not items:
                return
            await self.send_json({
                "type": "share_items_added",
                "session_id": sid,
                "items": items,
            })
            return

        if mtype == "tool_state":
            sid = data.get("session_id")
            is_root = sid == self.session_id
            is_sub = self.include_subagents and sid in self.descendant_ids
            if not (is_root or is_sub):
                return
            if not await _session_is_ready(sid):
                return
            # A hidden (over-ceiling) tool_use must not leak its error/extra.
            if not await self._tool_use_visible(sid, data.get("tool_use_id")):
                return
            await self.send_json({**data, "type": "share_tool_state"})
            return

        if mtype == "process_state":
            # Root session only: drives the viewer's "<Provider> is thinking"
            # indicator. Carries the state alone — never the label/tools/crons the
            # owner UI shows.
            if data.get("session_id") != self.session_id:
                return
            if not await _session_is_ready(self.session_id):
                return
            await self.send_json({"type": "share_process_state", "state": data.get("state")})
            return

        if mtype == "agent_link_created" and self.include_subagents:
            if data.get("parent_session_id") == self.session_id:
                agent_id = data.get("agent_session_id")
                self.descendant_ids.add(agent_id)
                # Let the viewer open a subagent spawned after page load — its
                # "View Agent" button resolves through this link (seeded once at
                # load otherwise, so a live-spawned agent would be un-openable).
                await self.send_json({
                    "type": "share_agent_link",
                    "link": {
                        "agent_id": agent_id,
                        "agent_slug": data.get("agent_slug"),
                        "tool_use_id": data.get("tool_use_id"),
                        "tool_use_line_num": data.get("tool_use_line_num"),
                        "is_background": data.get("is_background"),
                        "started_at": data.get("started_at"),
                    },
                })
            return

        if mtype == "session_updated":
            session = data.get("session") or {}
            if session.get("id") != self.session_id:
                return
            meta = await self._public_meta()
            if meta is not None:
                await self.send_json({"type": "share_meta", "meta": meta})
            return

        if mtype in ("share_updated", "share_removed"):
            sid = data.get("share_id") or (data.get("share") or {}).get("id")
            if sid != self.share_id:
                return
            share = data.get("share") or {}
            status = share.get("status")
            if mtype == "share_removed" or status in ("revoked", "expired") \
                    or share.get("options", {}).get("mode") == "snapshot":
                # Revoked / expired / flipped to snapshot → close it out.
                await self.send_json({"type": "share_closed"})
                await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
                return
            # Still live: re-resolve the connect-time filters from the fresh options.
            # HTTP re-resolves per request, but this open socket would otherwise keep
            # forwarding under its stale ceiling / subagent set (an owner tightening
            # a share must apply to already-connected viewers immediately).
            opts = share.get("options") or {}
            self.max_display_mode = opts.get("max_display_mode", "normal")
            self.ceiling = display_ceiling(self.max_display_mode)
            self.include_subagents = opts.get("include_subagents", True)
            self.descendant_ids = await self._load_descendants()
            meta = await self._public_meta()
            if meta is not None:
                await self.send_json({"type": "share_meta", "meta": meta})
            return

    async def _public_meta(self):
        from twicc.core.models import Share
        from twicc.core.serializers import serialize_share_public_meta

        share = await sync_to_async(
            lambda: Share.objects.select_related("session").filter(id=self.share_id).first()
        )()
        if share is None or not share.is_active():
            return None
        return await sync_to_async(serialize_share_public_meta)(share)
