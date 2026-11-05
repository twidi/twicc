"""Incremental summary parts reuse the existing speech splitting and ownership."""
import asyncio

import pytest

from twicc.tts import service as speech_service
from twicc.tts.openrouter import SpeechError


class Reader:
    def __init__(self):
        self.snapshot = {'revision': 1, 'state': 'running', 'text': '', 'saved_id': None}
        self.changed = asyncio.Queue()
        self.committed = False
        self.closed = False

    def send(self, text, state='running', revision=1):
        self.snapshot = {**self.snapshot, 'text': text, 'state': state, 'revision': revision}
        self.changed.put_nowait(self.snapshot)

    async def snapshots(self):
        yield self.snapshot
        while self.snapshot['state'] == 'running':
            yield await self.changed.get()

    def mark_audio_committed(self):
        self.committed = True

    async def aclose(self):
        self.closed = True


def test_prefix_waits_for_the_first_summary_part_and_commits_with_it():
    from twicc.summaries.speech import SummaryParts
    from twicc.tts.part_source import PrefixedParts
    async def scenario():
        reader = Reader()
        body = SummaryParts(reader, 30, 15)
        source = PrefixedParts('Project name.', body)
        pending = asyncio.create_task(source.next_part())
        await asyncio.sleep(0)
        assert not pending.done() and not reader.committed
        reader.send('First sentence. Remaining text.')
        assert await pending == 'Project name.\n\nFirst sentence.'
        assert reader.committed and source.split
        reader.send('First sentence. Remaining text. Last.', 'complete')
        assert await source.next_part() == 'Remaining text. Last.'
        assert await source.next_part() is None
        await source.aclose()
        assert reader.closed
    asyncio.run(scenario())


def test_revision_reset_replaces_provisional_text_and_whole_input_waits():
    from twicc.summaries.speech import SummaryParts
    async def scenario():
        reader = Reader()
        source = SummaryParts(reader, None, None)
        pending = asyncio.create_task(source.next_part())
        reader.send('Discarded provisional sentence.')
        await asyncio.sleep(0)
        assert not pending.done() and not reader.committed
        reader.send('Replacement sentence.', revision=2)
        await asyncio.sleep(0)
        assert not pending.done()
        reader.send('Replacement sentence. Finished.', 'complete', revision=2)
        assert await pending == 'Replacement sentence. Finished.'
        assert source.split is False
        assert await source.next_part() is None
        await source.aclose()
    asyncio.run(scenario())


def test_interruption_reads_usable_tail_then_reports_failure():
    from twicc.summaries.speech import SummaryParts
    async def scenario():
        reader = Reader()
        source = SummaryParts(reader, 60, 15)
        reader.send('First sentence. More text')
        assert await source.next_part() == 'First sentence.'
        reader.send('First sentence. More text ends. unfinished', 'failed')
        assert await source.next_part() == 'More text ends.'
        with pytest.raises(SpeechError):
            await source.next_part()
        await source.aclose()
    asyncio.run(scenario())


def test_failure_before_first_part_never_reads_failed_or_original_text():
    from twicc.summaries.speech import SummaryParts
    async def scenario():
        reader = Reader()
        reader.send('Provisional sentence.', 'failed')
        source = SummaryParts(reader, 60, 15)
        with pytest.raises(SpeechError):
            await source.next_part()
        assert not reader.committed
        await source.aclose()
    asyncio.run(scenario())


@pytest.fixture
def integrated(monkeypatch, transactional_db):
    from twicc.core.models import Project, Session
    from twicc.providers.text_calls import ENGINES
    from twicc.summaries import service
    from twicc.summaries.sources import SummarySource
    from twicc.tts import openrouter, streams, views
    from twicc.tts.audio_cache import speech_cache
    service._runs.clear()
    service._handles.clear()
    streams._reset_for_tests()
    speech_cache.clear()
    async def passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(service, 'run_under_db_write_lock', passthrough)
    session = Session.objects.create(id='speech-summary', project=Project.objects.create(id='speech-project'))
    source = SummarySource('message:speech', 'a' * 64, 'original ' * 200, 'original ' * 200,
                           session.id, 1, (1,), 1800, True, None, 'claude_code', None)
    monkeypatch.setattr(service, 'resolve_source', lambda _: source)
    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'auto'})
    monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
    monkeypatch.setattr(speech_service, 'openrouter_api_key', lambda: 'fake')
    monkeypatch.setattr(speech_service, '_stored_adapt_choice', lambda: 'openrouter')
    monkeypatch.setattr(speech_service, 'resolve_engine', lambda *_: pytest.fail('A summary invokes adaptation'))
    monkeypatch.setattr(speech_service, 'direct_text', lambda *_: pytest.fail('A summary invokes original cleanup'))
    parts = []
    async def pump_part(client, request, api_key, sink, index, **kwargs):
        parts.append(request.text)
        sink.begin_part()
        if index == 0:
            sink.set_headers('audio/mpeg', None)
        sink.append(b'audio')
        sink.end_part()
        return True
    monkeypatch.setattr(openrouter, '_pump_part', pump_part)
    yield service, views, streams, speech_cache, parts, source
    service._runs.clear()
    service._handles.clear()
    streams._reset_for_tests()
    speech_cache.clear()


def speech_request(**fields):
    import orjson
    from django.test import AsyncRequestFactory
    data = {'model': 'openai/gpt-4o-mini-tts', 'response_format': 'mp3', 'cache': True,
            'buffered': True, **fields}
    return AsyncRequestFactory().post('/api/tts/speech/', orjson.dumps(data), content_type='application/json')


def test_real_pump_starts_before_summary_completion_and_last_audio_release_cancels_producer(integrated, monkeypatch):
    import orjson
    service, views, streams, cache, parts, _ = integrated
    async def scenario():
        sent, closed, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        async def generate(*args):
            calls.append(args)
            try:
                yield 'First sentence. ' * 20
                sent.set()
                await gate.wait()
                yield 'Finished.'
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', generate)
        handle = await service.acquire_summary({})
        await sent.wait()
        response = await asyncio.wait_for(views.tts_speech(speech_request(summary_id=handle.id)), 2)
        assert response.status_code == 200
        audio_id = orjson.loads(response.content)['audio_id']
        audio = streams.get_stream(audio_id)
        assert parts and not gate.is_set() and handle.state == 'running'
        assert len(calls) == 1 and len(service._handles) == 2
        await service.release_summary(handle.id)
        assert not closed.is_set() and len(service._handles) == 1
        streams.release_stream(audio_id)
        await asyncio.gather(audio._task, return_exceptions=True)
        assert closed.is_set() and not service._handles
        assert cache.total_bytes == 0
    asyncio.run(scenario())


def test_audio_release_preserves_visual_owner_and_failure_never_caches_partial(integrated, monkeypatch):
    import orjson
    service, views, streams, cache, parts, _ = integrated
    async def scenario():
        sent, gate, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def generate(*args):
            try:
                yield 'First sentence. ' * 20
                sent.set()
                await gate.wait()
                yield 'Last sentence. unfinished'
                raise RuntimeError('Interrupted')
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', generate)
        handle = await service.acquire_summary({})
        await sent.wait()
        response = await views.tts_speech(speech_request(summary_id=handle.id))
        audio = streams.get_stream(orjson.loads(response.content)['audio_id'])
        streams.release_stream(audio.id)
        await asyncio.gather(audio._task, return_exceptions=True)
        assert not closed.is_set() and len(service._handles) == 1
        gate.set()
        final = [snap async for snap in service.summary_snapshots(handle.id)][-1]
        assert final['state'] == 'failed' and final['saved_id'] is None
        assert cache.total_bytes == 0
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_complete_live_and_saved_audio_share_exact_cache_but_temporary_summaries_do_not(integrated, monkeypatch):
    import orjson
    from asgiref.sync import sync_to_async
    service, views, streams, cache, parts, _ = integrated
    async def generate(*args):
        yield 'Exact summary body.'
    monkeypatch.setattr(service, 'stream_text', generate)
    async def scenario():
        handle = await service.acquire_summary({})
        response = await views.tts_speech(speech_request(summary_id=handle.id, announcement='Project.'))
        audio = streams.get_stream(orjson.loads(response.content)['audio_id'])
        await audio._task
        assert cache.total_bytes > 0
        final = [snap async for snap in service.summary_snapshots(handle.id)][-1]
        saved = await sync_to_async(service.load_saved_summary)(final['saved_id'])
        count = len(parts)
        repeat = await views.tts_speech(speech_request(saved_summary_id=saved['id'], summary_result_hash=saved['result_hash'], announcement='Project.'))
        assert repeat.status_code == 200 and len(parts) == count
        different = await views.tts_speech(speech_request(saved_summary_id=saved['id'], summary_result_hash=saved['result_hash'], announcement='Other project.'))
        await streams.get_stream(orjson.loads(different.content)['audio_id'])._task
        assert len(parts) > count
        await service.release_summary(handle.id)
        monkeypatch.setattr(service, 'resolve_source', lambda _: integrated[-1]._replace(source_key=None))
        temporary = await service.acquire_summary({})
        count = len(parts)
        selection = await views.tts_speech(speech_request(summary_id=temporary.id, announcement='Project.'))
        await streams.get_stream(orjson.loads(selection.content)['audio_id'])._task
        assert len(parts) > count, 'Temporary selection cannot reuse saved-summary audio'
        await service.release_summary(temporary.id)
    asyncio.run(scenario())


def test_script_without_limit_waits_while_visual_summary_streams(integrated, monkeypatch):
    import orjson
    from twicc.tts.part_source import as_part_source
    service, views, streams, cache, parts, _ = integrated
    fields = {'command': 'fake', 'output': 'stream', 'audio_format': 'mp3', 'pcm_rate': None,
              'pcm_channels': 1, 'max_chars': None}
    monkeypatch.setattr(speech_service, 'stored_script_config', lambda _: {**fields, 'delete_file': False})
    monkeypatch.setattr(speech_service, 'script_config_validated', lambda _: True)
    async def pump(request, sink, source, **kwargs):
        source = as_part_source(request.text, source)
        try:
            text = await source.next_part()
            parts.append(text)
            sink.set_headers('audio/mpeg', None)
            sink.append(b'audio')
            assert await source.next_part() is None
            sink.finish()
        finally:
            await source.aclose()
    monkeypatch.setattr(speech_service, 'pump_script_speech', pump)
    async def scenario():
        sent, gate = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            yield 'Visible sentence. ' * 20
            sent.set()
            await gate.wait()
            yield 'Completed.'
        monkeypatch.setattr(service, 'stream_text', generate)
        handle = await service.acquire_summary({})
        await sent.wait()
        pending = asyncio.create_task(views.tts_speech(speech_request(summary_id=handle.id, kind='script', script_id='config')))
        visual = service.summary_snapshots(handle.id)
        snapshot = await anext(visual)
        assert snapshot['text'].startswith('Visible sentence.') and snapshot['state'] == 'running'
        await asyncio.sleep(0.05)
        assert not pending.done() and parts == []
        gate.set()
        response = await asyncio.wait_for(pending, 2)
        assert response.status_code == 200
        await streams.get_stream(orjson.loads(response.content)['audio_id'])._task
        assert parts == ['Visible sentence. ' * 20 + 'Completed.']
        await visual.aclose()
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_audio_pump_cancelled_before_first_step_releases_reader(integrated, monkeypatch):
    service, views, streams, cache, parts, _ = integrated
    original_start = speech_service.start_stream
    async def scenario():
        sent, gate = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            yield 'Pending sentence.'
            sent.set()
            await gate.wait()
        monkeypatch.setattr(service, 'stream_text', generate)
        handle = await service.acquire_summary({})
        await sent.wait()
        def cancelled_start(*args, **kwargs):
            audio = original_start(*args, **kwargs)
            audio._task.cancel()
            return audio
        monkeypatch.setattr(speech_service, 'start_stream', cancelled_start)
        response = await asyncio.wait_for(views.tts_speech(speech_request(summary_id=handle.id)), 2)
        assert response.status_code == 424
        await asyncio.sleep(0)
        assert len(service._handles) == 1, 'Pre-start audio cancellation must not leak its independent reader'
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_committed_pump_reports_summary_failure_without_fallback_or_complete_cache(integrated, monkeypatch):
    import orjson
    service, views, streams, cache, parts, _ = integrated
    monkeypatch.setattr(service, 'fallback_engine', lambda *_: pytest.fail('Committed audio cannot restart'))
    async def scenario():
        sent, gate = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            yield 'First sentence. ' * 20
            sent.set()
            await gate.wait()
            yield 'Last usable sentence. unfinished'
            raise RuntimeError('Interrupted')
        monkeypatch.setattr(service, 'stream_text', generate)
        handle = await service.acquire_summary({})
        await sent.wait()
        response = await views.tts_speech(speech_request(summary_id=handle.id))
        audio = streams.get_stream(orjson.loads(response.content)['audio_id'])
        assert response.status_code == 200 and parts
        gate.set()
        await asyncio.wait_for(audio._task, 2)
        assert audio.state == 'failed' and cache.total_bytes == 0
        assert parts[-1].endswith('Last usable sentence.')
        assert not any(part.endswith('unfinished') for part in parts)
        await service.release_summary(handle.id)
    asyncio.run(scenario())
