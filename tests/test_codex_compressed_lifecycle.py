"""Durable history survives compression, restart, restoration, append and deletion."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from watchfiles import Change

from tests.test_codex_compressed_watcher import SID, compress, settle, source
from tests.test_codex_compressed_initial_sync import apply, produce
from twicc.core.models import Project, Session, SessionItem
from twicc.providers import db_writer, sessions_watcher as base
from twicc.providers.codex import sessions_watcher as module
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher


def records(count=1):
    values = [{'type': 'session_meta', 'payload': {
        'id': SID, 'cwd': '/tmp/p', 'history_mode': 'paginated',
    }}]
    values.extend({'type': 'event_msg', 'payload': {
        'type': 'item_completed', 'thread_id': SID, 'turn_id': f'turn-{number}',
        'item': {'type': 'UserMessage', 'id': f'user-{number}',
                 'content': [{'type': 'text', 'text': f'Work {number}', 'text_elements': []}]},
    }} for number in range(count))
    return b''.join(orjson.dumps(value) + b'\n' for value in values)


@pytest.mark.django_db(transaction=True)
def test_full_representation_lifecycle_preserves_durable_history(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    relative = str(path.relative_to(watcher.projects_dir))
    Session.objects.create(id=SID, project=Project.objects.create(id='p'), provider='codex',
                           file_path=relative, title='Keep', pinned='2026-11-08T00:00:00Z',
                           compute_version=watcher.get_compute().compute_version)
    channel = SimpleNamespace(group_send=AsyncMock())
    monkeypatch.setattr(base, 'get_channel_layer', lambda: channel)
    monkeypatch.setattr(db_writer, 'get_channel_layer', lambda: channel)
    monkeypatch.setattr(module, 'request_rebuild', lambda *_: pytest.fail('representation triggered rebuild'))

    async def snapshot():
        row = await Session.objects.aget(pk=SID)
        ids = [item.id async for item in SessionItem.objects.filter(session_id=SID).order_by('line_num')]
        return row, ids

    async def run():
        nonlocal watcher
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            original, item_ids = await snapshot()
            assert original.last_offset == len(records())
            assert original.last_line == 2
            stable = (original.history_epoch, original.storage_incarnation, original.compute_version,
                      original.search_version, original.content_state_epoch, original.plaintext_verified_epoch)
            watcher.stop_watcher()
            await watcher._drain_changes()

            compressed = compress(path)
            messages, stats = await asyncio.to_thread(produce)
            await asyncio.to_thread(apply, messages)
            assert stats['inventory_complete'] == 1
            row, ids = await snapshot()
            assert row.file_path == relative + '.zst'
            assert (row.last_offset, row.last_line) == (len(records()), 2)
            assert ids == item_ids
            assert (row.history_epoch, row.storage_incarnation, row.compute_version,
                    row.search_version, row.content_state_epoch, row.plaintext_verified_epoch) == stable
            assert not row.stale and row.unavailable_reason is None

            watcher = CodexSessionsWatcher()
            await watcher.process_path(compressed)
            await settle(watcher)
            generation = watcher._queue.source_generation(path)
            path.write_bytes(records())
            watcher._enqueue(path, Change.added)
            await settle(watcher)
            row, ids = await snapshot()
            assert row.file_path == relative
            assert ids == item_ids
            assert (row.history_epoch, row.storage_incarnation, row.compute_version,
                    row.search_version, row.content_state_epoch, row.plaintext_verified_epoch) == stable
            assert watcher._queue.source_generation(path) is generation

            with path.open('ab') as stream:
                stream.write(records(2)[len(records()):])
            watcher._enqueue(path, Change.modified)
            await settle(watcher)
            row, ids = await snapshot()
            assert (row.last_offset, row.last_line) == (len(records(2)), 3)
            assert ids[:2] == item_ids and len(ids) == 3
            assert row.history_epoch == original.history_epoch
            assert row.storage_incarnation == original.storage_incarnation
            assert row.title == 'Keep' and row.pinned == '2026-11-08T00:00:00Z'
            assert not row.stale and row.unavailable_reason is None

            # Removing the unused sibling does not publish a stale session.
            channel.group_send.reset_mock()
            compressed.unlink()
            watcher._enqueue(compressed, Change.deleted)
            await settle(watcher)
            assert not (await Session.objects.aget(pk=SID)).stale
            assert all(not call.args[1]['data'].get('session', {}).get('stale')
                       for call in channel.group_send.await_args_list)
            path.unlink()
            watcher._enqueue(path, Change.deleted)
            await settle(watcher)
            row, final_ids = await snapshot()
            assert row.stale and row.unavailable_reason is None
            assert final_ids == ids
            updates = [call.args[1]['data']['session'] for call in channel.group_send.await_args_list
                       if call.args[1]['data']['type'] == 'session_updated']
            assert updates and updates[-1]['stale']
            assert updates[-1]['unavailable_reason'] is None

            # An unchanged returning representation emits recovered availability.
            path.write_bytes(records(2))
            compressed = compress(path)
            watcher._enqueue(compressed, Change.added)
            await settle(watcher)
            row, final_ids = await snapshot()
            assert not row.stale and row.unavailable_reason is None
            assert row.file_path == relative + '.zst'
            assert final_ids == ids
            updates = [call.args[1]['data']['session'] for call in channel.group_send.await_args_list
                       if call.args[1]['data']['type'] == 'session_updated']
            assert not updates[-1]['stale'] and updates[-1]['unavailable_reason'] is None
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()

    asyncio.run(run())
