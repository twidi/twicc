"""Cooperative known-ID recovery outside the live writer and change consumer."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import TYPE_CHECKING, NamedTuple

import orjson
from channels.db import database_sync_to_async
from django.db import transaction
from django.db.models import Max

from twicc.core.content_storage.readers import effective_current, load_current
from twicc.core.models import Session, SessionItem

if TYPE_CHECKING:
    from twicc.providers.compute_base import AgentLinkUpdate


class LauncherLookupRequest(NamedTuple):
    launcher_session_id: str
    agent_id: str
    tool_use_id: str


class _Cursor(NamedTuple):
    after_line: int = 0
    before_line: int | None = None
    epoch: int | None = None


class _Page(NamedTuple):
    cursor: _Cursor
    selected: tuple[int, bytes] | None
    exhausted: bool


def _compute(provider):
    if provider == 'codex':
        from twicc.providers.codex.compute import get_compute
    else:
        from twicc.providers.claude_code.compute import get_compute
    return get_compute()


def _read_page(request: LauncherLookupRequest, cursor: _Cursor) -> _Page:
    """Read at most 128 records on the default read pool, with no writer lease."""
    owner = Session.objects.filter(id=request.launcher_session_id).first()
    if owner is None or (cursor.epoch is not None and owner.history_epoch != cursor.epoch):
        return _Page(cursor, None, True)
    if cursor.before_line is None:
        last = owner.items.aggregate(last=Max('line_num'))['last'] or 0
        cursor = _Cursor(0, last + 1, owner.history_epoch)
    rows = list(SessionItem.objects.filter(
        session_id=owner.id, line_num__gt=cursor.after_line, line_num__lt=cursor.before_line,
    ).order_by('line_num').defer('content')[:128])
    load_current(rows)
    compute = _compute(owner.provider)
    for item in rows:
        source = effective_current(item)
        try:
            parsed = orjson.loads(source)
        except orjson.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and any(
            tool == request.tool_use_id for tool, _prompt, _background in compute.extract_task_tool_use_prompts(parsed)
        ):
            return _Page(cursor, (item.line_num, sha256(source.encode()).digest()), True)
    next_cursor = cursor._replace(after_line=rows[-1].line_num) if rows else cursor
    return _Page(next_cursor, None, len(rows) < 128)


@transaction.atomic
def _publish(request, page):
    """Recheck membership, epoch, selected bytes, and link identity under the writer."""
    owner = Session.objects.filter(id=request.launcher_session_id).first()
    child = Session.objects.filter(id=request.agent_id).first()
    if (owner is None or child is None or owner.history_epoch != page.cursor.epoch
            or child.parent_session_id != (owner.parent_session_id or owner.id)):
        return ()
    line, digest = page.selected
    item = SessionItem.objects.filter(session_id=owner.id, line_num=line).defer('content').first()
    if item is None:
        return ()
    load_current([item])
    if sha256(effective_current(item).encode()).digest() != digest:
        return ()
    update = _compute(owner.provider)._agent_link_from_selected_item(owner, child, request.tool_use_id, item)
    return (update,) if update else ()


async def _drain_operation(awaitable):
    """Retain submitted work until completion, including repeated cancellation."""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        # Retrieve errors before propagating cancellation; no owned work survives.
        task.result()
        raise asyncio.CancelledError
    return task.result()


class LauncherLookupResolver:
    """Keep only request cursors; rotate unfinished requests after every page."""

    def __init__(self, *, on_resolved: Callable[[tuple[AgentLinkUpdate, ...]], Awaitable[None]]):
        self.on_resolved = on_resolved
        self._cursors = {}
        self._ready = deque()
        self._wake = asyncio.Event()

    def enqueue(self, requests) -> None:
        for request in requests:
            if request not in self._cursors:
                self._cursors[request] = _Cursor()
                self._ready.append(request)
        self._wake.set()

    async def run(self, stop_event) -> None:
        from twicc.providers.compute_executor import run_compute_sync
        from twicc.providers.db_writer import run_under_storage_write_lock
        from twicc.providers.write_coordinator import WritePriority

        stop = asyncio.create_task(stop_event.wait())
        try:
            while not stop_event.is_set():
                if not self._ready:
                    self._wake.clear()
                    wake = asyncio.create_task(self._wake.wait())
                    try:
                        await asyncio.wait((wake, stop), return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        wake.cancel()
                        await asyncio.gather(wake, return_exceptions=True)
                    continue
                request = self._ready.popleft()
                page = await _drain_operation(database_sync_to_async(
                    _read_page, thread_sensitive=False,
                )(request, self._cursors[request]))
                if stop_event.is_set():
                    break
                if page.selected:
                    updates = await _drain_operation(run_under_storage_write_lock(
                        lambda request=request, page=page: run_compute_sync(_publish, request, page), priority=WritePriority.BACKGROUND,
                    ))
                    if updates:
                        await self.on_resolved(updates)
                if page.exhausted:
                    self._cursors.pop(request, None)
                else:
                    self._cursors[request] = page.cursor
                    self._ready.append(request)
                await asyncio.sleep(0)
        finally:
            stop.cancel()
            await asyncio.gather(stop, return_exceptions=True)
            self._cursors.clear()
            self._ready.clear()
