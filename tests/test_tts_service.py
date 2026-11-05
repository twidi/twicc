"""Callable speech preparation keeps the REST pipeline and resource ownership."""
import asyncio
import hashlib
from contextlib import aclosing

import pytest

from twicc.summaries import service as summaries
from twicc.tts import adapt, streams
from twicc.tts.audio_cache import speech_cache
from twicc.tts.openrouter import SpeechError as UpstreamSpeechError
from twicc.tts.part_source import as_part_source
from twicc.tts.script_audio import OutputCheck

BODY = {'text': 'Hello there', 'model': 'openai/gpt-4o-mini-tts', 'voice': 'alloy', 'response_format': 'mp3'}
FIELDS = {'command': 'stored command', 'output': 'stream', 'audio_format': 'mp3',
          'pcm_rate': None, 'pcm_channels': 1, 'max_chars': None}


@pytest.fixture
def service(monkeypatch):
    from twicc.tts import service
    streams._reset_for_tests()
    speech_cache.clear()
    monkeypatch.setattr(service, 'openrouter_api_key', lambda: 'fake-key')
    monkeypatch.setattr(service, '_stored_adapt_choice', lambda: 'off')
    monkeypatch.setattr(service, 'schedule_cost_log', lambda *_: pytest.fail('Unexpected cost lookup'))
    monkeypatch.setattr(adapt, '_open_stream', lambda *_: pytest.fail('Unexpected model call'))
    yield service
    streams._reset_for_tests()
    speech_cache.clear()


def install_pump(service, monkeypatch):
    calls = []
    async def pump(request, api_key, sink, parts, *, paced):
        async with aclosing(as_part_source(request.text, parts)) as source:
            texts = []
            while (part := await source.next_part()) is not None:
                texts.append(part)
            calls.append((request, api_key, texts, paced))
            sink.set_headers('audio/mpeg', None)
            sink.append(b'audio')
            sink.finish()
    monkeypatch.setattr(service, 'pump_speech', pump)
    return calls


def test_ordinary_text_cache_announcement_and_buffering(service, monkeypatch):
    calls = install_pump(service, monkeypatch)
    async def scenario():
        body = {**BODY, 'cache': True, 'buffered': True, 'announcement': '  Project.  '}
        first = await service.open_speech(body)
        assert isinstance(first, tuple)
        assert first == service.OpenedSpeech(first.audio_id, 'audio/mpeg')
        assert streams.get_stream(first.audio_id).data == b'audio'
        second = await service.open_speech(body)
        assert second.content_type == first.content_type
        assert second.audio_id != first.audio_id
        assert streams.get_stream(second.audio_id).data == b'audio'
        assert len(calls) == 1
        assert calls[0][2:] == (['Project.\n\nHello there'], False)
        assert len(speech_cache._entries) == 1
    asyncio.run(scenario())


def test_configuration_test_keeps_telemetry_and_never_caches(service, monkeypatch):
    calls = install_pump(service, monkeypatch)
    records = []
    async def record(config_id, update):
        records.append((config_id, update))
    monkeypatch.setattr(service, '_record_last_test', record)
    async def scenario():
        for _ in range(2):
            opened = await service.open_speech({**BODY, 'config_id': 'config', 'cache': True})
            await streams.get_stream(opened.audio_id)._task
        assert len(calls) == len(records) == 2
        assert not speech_cache._entries
        config_id, record = records[0]
        assert config_id == 'config'
        assert record['chars'] == len(BODY['text'])
        assert record['parts'] == 1 and record['adapt_engine'] is None
    asyncio.run(scenario())


@pytest.mark.parametrize('audio_format', ['mp3', 'wav', 'pcm'])
def test_script_formats_use_stored_options_and_existing_audio_check(service, monkeypatch, audio_format):
    from tests.test_tts_script_audio import MP3_FRAME, _wav
    fields = {**FIELDS, 'audio_format': audio_format, 'pcm_rate': 8000 if audio_format == 'pcm' else None}
    monkeypatch.setattr(service, 'stored_script_config', lambda _: {**fields, 'delete_file': False})
    monkeypatch.setattr(service, 'script_config_validated', lambda _: True)
    calls = []
    raw = MP3_FRAME if audio_format == 'mp3' else _wav(b'\x01\x02' * 20) if audio_format == 'wav' else b'\x01\x02' * 20
    expected = raw if audio_format == 'mp3' else b'\x01\x02' * 20
    async def pump(request, sink, parts, *, paced, delete_file):
        calls.append((request, parts, paced, delete_file))
        check = OutputCheck(request.audio_format, request.pcm_rate, request.pcm_channels)
        audio = check.feed(raw) + check.end()
        sink.set_headers(check.content_type, None)
        sink.append(audio)
        sink.finish()
    monkeypatch.setattr(service, 'pump_script_speech', pump)
    async def scenario():
        opened = await service.open_speech({'kind': 'script', 'script_id': 'script', 'text': 'Hello', 'command': 'ignored'})
        assert opened.content_type == ('audio/mpeg' if audio_format == 'mp3' else 'audio/pcm;rate=8000;channels=1')
        assert bytes(streams.get_stream(opened.audio_id).data) == expected
        assert calls[0][0].command == FIELDS['command']
        assert calls[0][1:] == (['Hello'], True, False)
    asyncio.run(scenario())


def test_saved_summary_hash_mismatch_preserves_http_error(service, monkeypatch):
    saved_id = 17
    monkeypatch.setattr(summaries, 'load_saved_summary', lambda _: {'text': 'Updated summary', 'result_hash': 'a' * 64})
    async def scenario():
        with pytest.raises(service.SpeechError) as caught:
            await service.open_speech({k: v for k, v in {**BODY, 'saved_summary_id': saved_id,
                                      'summary_result_hash': 'b' * 64}.items() if k != 'text'})
        error = caught.value
        assert error.status == 409
        assert error.extra == {'summary_changed': True, 'saved_id': saved_id}
    asyncio.run(scenario())


def test_saved_summary_keeps_exact_body_and_bypasses_adaptation(service, monkeypatch):
    text = 'Frozen saved summary.'
    result_hash = hashlib.sha256(text.encode()).hexdigest()
    monkeypatch.setattr(summaries, 'load_saved_summary', lambda _: {'id': 17, 'text': text, 'result_hash': result_hash})
    monkeypatch.setattr(service, '_stored_adapt_choice', lambda: pytest.fail('Summary invokes adaptation'))
    calls = install_pump(service, monkeypatch)
    async def scenario():
        opened = await service.open_speech({k: v for k, v in {**BODY, 'saved_summary_id': 17,
                                          'summary_result_hash': result_hash}.items() if k != 'text'})
        assert opened.content_type == 'audio/mpeg'
        assert calls[0][0].text == text and calls[0][2] == [text]
        assert streams.get_stream(opened.audio_id).label['adapted'] == 'summary'
    asyncio.run(scenario())


def test_running_summary_reader_is_independent_and_closes_on_completion(service, monkeypatch):
    calls = install_pump(service, monkeypatch)
    async def scenario():
        source = summaries.SummarySource(None, 'a' * 64, '', '', None, None, (), 0, True, None, None, 'selection')
        run = summaries._SummaryRun(source, 'auto', 'policy')
        run.text = 'Running summary'
        page = summaries.SummaryHandle(run, {})
        retained = asyncio.Event()
        acquire = summaries.acquire_summary_reader
        async def retain(handle_id):
            reader = await acquire(handle_id)
            retained.set()
            return reader
        monkeypatch.setattr(summaries, 'acquire_summary_reader', retain)
        opening = asyncio.create_task(service.open_speech({k: v for k, v in {**BODY, 'summary_id': page.id}.items() if k != 'text'}))
        await asyncio.wait_for(retained.wait(), 1)
        await summaries.release_summary(page.id)
        assert run.state == 'running' and len(run.owners) == 1
        assert not opening.done()
        run.state, run.text = 'complete', 'Running summary completed.'
        run.wake()
        opened = await asyncio.wait_for(opening, 1)
        assert opened.content_type == 'audio/mpeg'
        assert calls[0][2] == [run.text]
        assert run.committed
        assert not run.owners
    asyncio.run(scenario())


@pytest.mark.parametrize('later_fragment', [False, True])
def test_summary_cancellation_closes_reader_and_pump(service, monkeypatch, later_fragment):
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        source = summaries.SummarySource(None, 'a' * 64, '', '', None, None, (), 0, True, None, None, 'selection')
        run = summaries._SummaryRun(source, 'auto', 'policy')
        page = summaries.SummaryHandle(run, {})
        async def pump(request, api_key, sink, parts, *, paced):
            async with aclosing(parts):
                try:
                    if later_fragment:
                        sink.set_headers('audio/mpeg', None)
                        sink.append(b'first fragment')
                    entered.set()
                    await parts.next_part()
                finally:
                    closed.set()
        monkeypatch.setattr(service, 'pump_speech', pump)
        opening = asyncio.create_task(service.open_speech({k: v for k, v in {**BODY, 'summary_id': page.id}.items() if k != 'text'}))
        await asyncio.wait_for(entered.wait(), 1)
        await summaries.release_summary(page.id)
        assert len(run.owners) == 1
        if later_fragment:
            opened = await opening
            streams.release_stream(opened.audio_id)
        else:
            opening.cancel()
            with pytest.raises(asyncio.CancelledError):
                await opening
        await asyncio.wait_for(closed.wait(), 1)
        assert not run.owners
        assert not streams._streams
    asyncio.run(scenario())


def test_cancellation_during_adaptation_closes_source_and_stream(service, monkeypatch):
    from twicc.providers.text_calls import ENGINES
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(service, '_stored_adapt_choice', lambda: 'haiku')
        monkeypatch.setattr(service, 'direct_text', lambda *_: None)
        monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
        async def deltas(*_):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield ''
            finally:
                closed.set()
        monkeypatch.setattr(adapt, '_open_stream', deltas)
        install_pump(service, monkeypatch)
        opening = asyncio.create_task(service.open_speech(BODY))
        await asyncio.wait_for(entered.wait(), 1)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        await asyncio.wait_for(closed.wait(), 1)
        assert not streams._streams
    asyncio.run(scenario())


@pytest.mark.parametrize('body,status,message', [([], 400, 'The body must be a JSON object'),
                         ({**BODY, 'origin': 'invalid'}, 400, "'origin' must be one of:"),
                         ({**BODY, 'announcement': 1}, 400, "'announcement' must be a string")])
def test_validation_errors_are_typed(service, body, status, message):
    async def scenario():
        with pytest.raises(service.SpeechError) as caught:
            await service.open_speech(body)
        assert caught.value.status == status
        assert caught.value.message.startswith(message)
        assert caught.value.extra == {}
    asyncio.run(scenario())


def test_provider_401_is_dependency_failure(service, monkeypatch):
    async def pump(*args, **kwargs):
        raise UpstreamSpeechError('Provider rejected request', upstream_status=401)
    monkeypatch.setattr(service, 'pump_speech', pump)
    async def scenario():
        with pytest.raises(service.SpeechError) as caught:
            await service.open_speech(BODY)
        assert caught.value.status == 424
        assert caught.value.extra == {'upstream_status': 401}
        assert not streams._streams
    asyncio.run(scenario())
