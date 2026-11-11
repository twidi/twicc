"""Summary ownership, incremental text, persistence, and engine failure boundaries."""
import asyncio
import hashlib

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from tests import test_timeline_messages

from twicc.core.models import MessageSummary, Project, Session
from twicc.summaries.sources import SummarySource

live_session = test_timeline_messages.live_session
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def service(monkeypatch):
    from twicc.summaries import service
    from twicc.providers.text_calls import ENGINES
    service._runs.clear()
    service._handles.clear()
    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'auto'})
    monkeypatch.setattr(service, 'resolve_engine', lambda *_: (ENGINES[0], None))
    monkeypatch.setattr(service, 'fallback_engine', lambda *_: None)
    async def passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(service, 'run_under_db_write_lock', passthrough)
    return service


@pytest.fixture
def source(service, monkeypatch):
    session = Session.objects.create(id='summary-session', project=Project.objects.create(id='summary-project'))
    value = SummarySource('message:identity', 'a' * 64, 'source ' * 200, 'source ' * 200,
                          session.id, 2, (2, 3), 1400, True, None, 'claude_code', None)
    monkeypatch.setattr(service, 'resolve_source', lambda _: value)
    return value


async def terminal(service, handle):
    async with asyncio.timeout(2):
        snapshots = [value async for value in service.summary_snapshots(handle.id)]
    return snapshots[-1]


@pytest.mark.parametrize('origin, expected', [('manual', 1), ('timeline', 0), ('unknown', 0)])
def test_telemetry_counts_new_manual_results_once(service, source, monkeypatch, origin, expected):
    from twicc.telemetry import activity
    counted = []
    monkeypatch.setattr(activity, 'collection_token', lambda: ('test', 'epoch'))
    monkeypatch.setattr(activity, 'record_activity', lambda metric, **kwargs: counted.append(metric))
    async def stream(*args):
        yield 'A new summary.'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        handle = await service.acquire_summary({}, usage_origin=origin)
        assert (await terminal(service, handle))['state'] == 'complete'
        cached = await service.acquire_summary({}, usage_origin='manual')
        assert cached.state == 'complete'
        assert counted == ['manual_summaries'] * expected
        await service.release_summary(handle.id)
        await service.release_summary(cached.id)
    asyncio.run(scenario())


def test_manual_join_does_not_relabel_automatic_generation(service, source, monkeypatch):
    from twicc.telemetry import activity
    counted = []
    monkeypatch.setattr(activity, 'collection_token', lambda: ('test', 'epoch'))
    monkeypatch.setattr(activity, 'record_activity', lambda *args, **kwargs: counted.append(args))
    async def scenario():
        gate = asyncio.Event()
        async def stream(*args):
            await gate.wait()
            yield 'Automatic summary.'
        monkeypatch.setattr(service, 'stream_text', stream)
        auto = await service.acquire_summary({}, usage_origin='timeline')
        manual = await service.acquire_summary({}, usage_origin='manual')
        gate.set()
        await terminal(service, manual)
        assert counted == []
        await service.release_summary(auto.id)
        await service.release_summary(manual.id)
    asyncio.run(scenario())


def test_two_handles_share_live_progress_and_release_independently(service, source, monkeypatch):
    async def scenario():
        gate, sent, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        async def stream(engine, prompt):
            calls.append(prompt)
            try:
                yield 'First sentence. '
                sent.set()
                await gate.wait()
                yield 'Last sentence.'
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', stream)
        first = await service.acquire_summary({})
        await sent.wait()
        second = await service.acquire_summary({})
        assert first.id != second.id
        reader = service.summary_snapshots(second.id)
        snapshot = await anext(reader)
        assert snapshot['state'] == 'running' and snapshot['text'] == 'First sentence. '
        assert len(calls) == 1 and not gate.is_set()
        await service.release_summary(first.id)
        assert not closed.is_set()
        gate.set()
        final = [item async for item in reader][-1]
        assert final['state'] == 'complete' and final['saved_id'] is not None
        assert final['result_hash'] == hashlib.sha256(final['text'].encode()).hexdigest()
        assert await sync_to_async(MessageSummary.objects.count)() == 1
        saved = await sync_to_async(service.load_saved_summary)(final['saved_id'])
        assert saved['result_hash'] == hashlib.sha256(saved['text'].encode()).hexdigest()
        await service.release_summary(second.id)
    asyncio.run(scenario())


def test_equal_temporary_texts_have_independent_runs_and_no_database_access(service, monkeypatch):
    async def scenario():
        gate, both_started = asyncio.Event(), asyncio.Event()
        calls = []
        async def stream(*args):
            calls.append(args)
            if len(calls) == 2:
                both_started.set()
            yield 'Summary'
            await gate.wait()
        monkeypatch.setattr(service, 'stream_text', stream)
        def forbidden(*args, **kwargs):
            raise AssertionError('Temporary source touches saved results')
        monkeypatch.setattr(MessageSummary.objects, 'filter', forbidden)
        monkeypatch.setattr(MessageSummary.objects, 'update_or_create', forbidden)
        a = await service.acquire_summary({'text': 'Selected text', 'purpose': 'selection'})
        b = await service.acquire_summary({'text': 'Selected text', 'purpose': 'selection'})
        await asyncio.wait_for(both_started.wait(), 1)
        assert len(calls) == 2
        gate.set()
        assert (await terminal(service, a))['saved_id'] is None
        assert (await terminal(service, b))['saved_id'] is None
        await service.release_summary(a.id)
        await service.release_summary(b.id)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['empty', 'failed', 'cut'])
def test_failed_generation_saves_nothing(service, source, monkeypatch, failure):
    from twicc.openrouter_text import OpenRouterTextError
    async def stream(*args):
        if failure != 'empty':
            yield 'Partial'
            raise OpenRouterTextError('cut') if failure == 'cut' else RuntimeError('private text')
        if False:
            yield ''
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        handle = await service.acquire_summary({})
        assert (await terminal(service, handle))['state'] == 'failed'
        assert await sync_to_async(MessageSummary.objects.count)() == 0
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_saved_reuse_regeneration_failure_policy_and_source_variants(service, source, monkeypatch):
    async def stream(*args):
        yield 'Successful summary.'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        first = await service.acquire_summary({})
        saved = (await terminal(service, first))['saved_id']
        await service.release_summary(first.id)
        service._runs.clear()  # Backend restart retains DB results.
        reused = await service.acquire_summary({}, start=False)
        assert (await terminal(service, reused))['saved_id'] == saved
        async def failure(*args):
            yield 'Failed replacement'
            raise RuntimeError()
        monkeypatch.setattr(service, 'stream_text', failure)
        failed = await service.acquire_summary({}, regenerate=True)
        assert (await terminal(service, failed))['state'] == 'failed'
        assert (await sync_to_async(service.load_saved_summary)(saved))['text'] == 'Successful summary.'
        monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'luna'})
        descriptor = (await sync_to_async(service.resolve_sources)([{}]))[0]
        assert descriptor['saved'] is None and descriptor['previous']['id'] == saved
        with pytest.raises(service.SummaryUnavailable):
            await service.acquire_summary({}, start=False)
        monkeypatch.setattr(service, 'resolve_source', lambda _: source._replace(source_hash='b' * 64))
        descriptor = (await sync_to_async(service.resolve_sources)([{}]))[0]
        assert descriptor['saved'] is None and descriptor['previous'] is None
        await service.release_summary(reused.id)
        await service.release_summary(failed.id)
    asyncio.run(scenario())


def test_fallback_resets_revision_and_audio_commit_blocks_it(service, source, monkeypatch):
    from twicc.providers.text_calls import ENGINES, OPENROUTER_ENGINE
    monkeypatch.setattr(service, 'resolve_engine', lambda *_: (OPENROUTER_ENGINE, None))
    monkeypatch.setattr(service, 'fallback_engine', lambda *_: ENGINES[0])
    async def scenario(commit):
        gate, sent = asyncio.Event(), asyncio.Event()
        calls = []
        async def stream(engine, prompt):
            calls.append(engine)
            if engine == OPENROUTER_ENGINE:
                yield 'Provisional'
                sent.set()
                await gate.wait()
                raise RuntimeError()
            yield 'Replacement'
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({}, regenerate=True)
        reader = await service.acquire_summary_reader(handle.id)
        await sent.wait()
        assert (await anext(reader.snapshots()))['text'] == 'Provisional'
        if commit:
            reader.mark_audio_committed()
        gate.set()
        final = await terminal(service, handle)
        if commit:
            assert final['state'] == 'failed' and len(calls) == 1
        else:
            assert final['revision'] == 2 and final['text'] == 'Replacement'
            assert final['fallback_reason'] == 'failed' and len(calls) == 2
        await reader.aclose()
        await service.release_summary(handle.id)
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_internal_reader_owns_work_after_external_release_and_last_owner_closes(service, source, monkeypatch):
    async def scenario():
        closed, sent = asyncio.Event(), asyncio.Event()
        async def stream(*args):
            try:
                yield 'Partial'
                sent.set()
                await asyncio.Event().wait()
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({})
        reader = await service.acquire_summary_reader(handle.id)
        await sent.wait()
        await service.release_summary(handle.id)
        assert not closed.is_set()
        await reader.aclose()
        assert closed.is_set()
        assert await sync_to_async(MessageSummary.objects.count)() == 0
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_snapshot_session_purged_before_or_during_save_never_resurrects(service, source, monkeypatch):
    async def scenario():
        gate, sent = asyncio.Event(), asyncio.Event()
        async def stream(*args):
            yield 'Retained snapshot summary'
            sent.set()
            await gate.wait()
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({})
        await sent.wait()
        await sync_to_async(Session.objects.filter(pk=source.session_id).delete)()
        gate.set()
        result = await terminal(service, handle)
        assert result['state'] == 'complete' and result['saved_id'] is None
        assert await sync_to_async(MessageSummary.objects.count)() == 0
        assert await sync_to_async(Session.objects.count)() == 0
        await service.release_summary(handle.id)
        handle = await service.acquire_summary({})
        assert (await terminal(service, handle))['saved_id'] is None
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_expiry_releases_unclaimed_and_claimed_handles(service, source, monkeypatch):
    async def scenario():
        closed = asyncio.Event()
        async def stream(*args):
            try:
                yield 'Partial'
                await asyncio.Event().wait()
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', stream)
        monkeypatch.setattr(service, 'UNCLAIMED_TTL', .02)
        monkeypatch.setattr(service, 'SAFETY_TTL', .05)
        handle = await service.acquire_summary({})
        await asyncio.wait_for(closed.wait(), 1)
        with pytest.raises(service.SummaryGone):
            await anext(service.summary_snapshots(handle.id))
        closed.clear()
        handle = await service.acquire_summary({})
        reader = service.summary_snapshots(handle.id)
        await anext(reader)
        await asyncio.wait_for(closed.wait(), 1)
        assert handle.id not in service._handles
        await reader.aclose()
    asyncio.run(scenario())


def test_concurrent_acquisition_and_regeneration_save_once_and_broadcast_only_provenance(service, source, monkeypatch):
    async def scenario():
        gate, started = asyncio.Event(), asyncio.Event()
        calls, events = [], []
        async def stream(*args):
            calls.append(args)
            yield 'Visible summary'
            started.set()
            await gate.wait()
        class Layer:
            async def group_send(self, group, event):
                events.append((group, event))
        monkeypatch.setattr(service, 'get_channel_layer', Layer)
        monkeypatch.setattr(service, 'stream_text', stream)
        a, b = await asyncio.gather(service.acquire_summary({}, regenerate=True),
                                    service.acquire_summary({}, regenerate=True))
        await started.wait()
        assert a.id != b.id and len(calls) == 1
        descriptor = (await sync_to_async(service.resolve_sources)([{}]))[0]
        assert descriptor['running']
        gate.set()
        assert (await terminal(service, a))['saved_id'] == (await terminal(service, b))['saved_id']
        await a._run.task
        assert await sync_to_async(MessageSummary.objects.count)() == 1
        assert len(events) == 1
        group, event = events[0]
        assert group == 'updates' and event['type'] == 'broadcast'
        assert set(event['data']) == {'type', 'session_id', 'source_key', 'source_hash', 'policy_key', 'saved_id'}
        assert event['data']['type'] == 'summary_saved'
        await service.release_summary(a.id)
        await service.release_summary(b.id)
    asyncio.run(scenario())


def test_lookup_only_never_starts_after_running_lookup_becomes_stale(service, source, monkeypatch):
    async def scenario():
        sent = asyncio.Event()
        calls = []
        async def stream(*args):
            calls.append(args)
            yield 'Partial'
            sent.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({})
        await sent.wait()
        assert (await sync_to_async(service.resolve_sources)([{}]))[0]['running']
        await service.release_summary(handle.id)
        with pytest.raises(service.SummaryUnavailable):
            await service.acquire_summary({}, start=False)
        assert len(calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('first', [True, False])
def test_stalled_calls_fail_and_close_without_saving(service, source, monkeypatch, first):
    async def scenario():
        closed = asyncio.Event()
        async def stream(*args):
            try:
                if not first:
                    yield 'Partial'
                await asyncio.Event().wait()
            finally:
                closed.set()
            if False:
                yield ''
        monkeypatch.setattr(service, 'step_limits', lambda _: (.01, .01))
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({})
        final = await terminal(service, handle)
        assert final['state'] == 'failed' and final['error'] == 'stalled'
        assert closed.is_set()
        assert await sync_to_async(MessageSummary.objects.count)() == 0
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_requested_policy_and_source_are_frozen_while_generation_runs(service, source, monkeypatch):
    async def scenario():
        gate, started = asyncio.Event(), asyncio.Event()
        async def stream(engine, prompt):
            assert source.input_text in prompt
            yield 'Summary'
            started.set()
            await gate.wait()
        monkeypatch.setattr(service, 'stream_text', stream)
        handle = await service.acquire_summary({})
        policy = handle.descriptor['policy_key']
        await started.wait()
        monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'luna'})
        monkeypatch.setattr(service, 'resolve_source', lambda _: source._replace(source_hash='b' * 64))
        gate.set()
        final = await terminal(service, handle)
        row = await sync_to_async(service.load_saved_summary)(final['saved_id'])
        assert row['policy_key'] == policy and row['requested_model'] == 'auto'
        assert row['source_hash'] == source.source_hash
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_purge_at_save_boundary_and_write_race_remain_temporary(service, source, monkeypatch):
    from django.db import IntegrityError
    original = service._save_result
    async def stream(*args):
        yield 'Retained snapshot summary'
    monkeypatch.setattr(service, 'stream_text', stream)
    def purge_then_save(run):
        Session.objects.filter(pk=source.session_id).delete()
        return original(run)
    monkeypatch.setattr(service, '_save_result', purge_then_save)
    async def scenario():
        handle = await service.acquire_summary({})
        final = await terminal(service, handle)
        assert final['state'] == 'complete' and final['saved_id'] is None
        await service.release_summary(handle.id)
    asyncio.run(scenario())
    assert Session.objects.count() == MessageSummary.objects.count() == 0
    # Model a purge winning the write race after the initial existence check.
    Session.objects.create(id=source.session_id, project=Project.objects.get(pk='summary-project'))
    monkeypatch.setattr(service, '_save_result', original)
    def deleted_during_write(*args, **kwargs):
        raise IntegrityError('session FK disappeared')
    monkeypatch.setattr(MessageSummary.objects, 'update_or_create', deleted_during_write)
    real_exists = Session.objects.filter(pk=source.session_id).__class__.exists
    checks = []
    def exists(query):
        checks.append(True)
        return real_exists(query) if len(checks) == 1 else False
    monkeypatch.setattr(Session.objects.filter(pk=source.session_id).__class__, 'exists', exists)
    asyncio.run(scenario())
    assert MessageSummary.objects.count() == 0


def test_release_after_provider_completion_during_save_does_not_persist_cancelled_result(service, source, monkeypatch):
    import threading
    saved, proceed = threading.Event(), threading.Event()
    original = service._save_result
    def save_then_pause(run):
        value = original(run)
        saved.set()
        assert proceed.wait(2)
        return value
    monkeypatch.setattr(service, '_save_result', save_then_pause)
    async def stream(*args):
        yield 'Successful complete text'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        handle = await service.acquire_summary({})
        await asyncio.wait_for(asyncio.to_thread(saved.wait), 1)
        release = asyncio.create_task(service.release_summary(handle.id))
        await asyncio.sleep(0)
        proceed.set()
        await release
        state = handle._run.state
        count = await sync_to_async(MessageSummary.objects.count)()
        assert not (state == 'cancelled' and count), 'Cancelled result has persisted'
    asyncio.run(scenario())


def test_generation_task_cancelled_before_first_step_wakes_readers(service, source):
    async def scenario():
        handle = await service.acquire_summary({})
        handle._run.task.cancel()
        final = await terminal(service, handle)
        assert final['state'] == 'cancelled'
        assert await sync_to_async(MessageSummary.objects.count)() == 0
        await service.release_summary(handle.id)
    asyncio.run(scenario())


def test_successful_regeneration_replaces_body_hash_and_retained_purge_clears_saved_id(service, source, monkeypatch):
    answers = iter(['First result', 'Changed result'])
    async def stream(*args):
        yield next(answers)
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        first = await service.acquire_summary({})
        saved_id = (await terminal(service, first))['saved_id']
        before = await sync_to_async(service.load_saved_summary)(saved_id)
        second = await service.acquire_summary({}, regenerate=True)
        assert (await terminal(service, second))['saved_id'] == saved_id
        after = await sync_to_async(service.load_saved_summary)(saved_id)
        assert before['result_hash'] != after['result_hash'] and after['text'] == 'Changed result'
        assert await sync_to_async(MessageSummary.objects.count)() == 1
        await sync_to_async(Session.objects.filter(pk=source.session_id).delete)()
        retained = await service.acquire_summary({}, start=False)
        final = await terminal(service, retained)
        assert final['state'] == 'complete' and final['text'] == 'Changed result'
        assert final['saved_id'] is None
        await service.release_summary(first.id)
        await service.release_summary(second.id)
        await service.release_summary(retained.id)
    asyncio.run(scenario())


def test_large_openrouter_choice_routes_native_timeline_source_intact_to_enabled_agent(service, live_session, monkeypatch):
    from twicc.core.enums import Provider
    from twicc.providers import text_calls
    from twicc.summaries.sources import resolve_source

    text = 'First native source. ' + 'é' * 60_001 + '\nLast native source.'
    live_session.append(live_session.message('large-summary', text))
    event = live_session.events()[0]
    monkeypatch.setattr(service, 'resolve_source', resolve_source)
    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'openrouter'})
    monkeypatch.setattr(service, 'resolve_engine', text_calls.resolve_engine)
    monkeypatch.setattr(text_calls, 'openrouter_key_set', lambda: True)
    monkeypatch.setattr(text_calls, 'is_provider_running', lambda provider: provider == Provider.CODEX)
    calls = []

    async def stream(engine, prompt):
        calls.append((engine, prompt))
        yield 'A complete timeline-only summary.'

    monkeypatch.setattr(service, 'stream_text', stream)

    async def scenario():
        handle = await service.acquire_summary({'event_id': event.id})
        final = await terminal(service, handle)
        assert final['state'] == 'complete' and final['saved_id'] is not None
        assert len(calls) == 1 and calls[0][0].name == 'luna'
        assert calls[0][1] == service.summary_prompt(text)
        saved = await sync_to_async(service.load_saved_summary)(final['saved_id'])
        assert saved['text'] == 'A complete timeline-only summary.'
        assert saved['requested_model'] == 'openrouter'
        assert saved['source_hash'] == resolve_source_hash
        await service.release_summary(handle.id)

    resolve_source_hash = resolve_source({'event_id': event.id}).source_hash
    asyncio.run(scenario())


def test_descriptors_expose_openrouter_source_limit_without_disabling_saved_access(service, source, monkeypatch):
    from twicc.providers.text_calls import OPENROUTER_ADAPT_MAX_CHARS
    from twicc.summaries.sources import _resolved

    monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'openrouter'})
    short = _resolved(key=source.source_key, text='é' * OPENROUTER_ADAPT_MAX_CHARS, session_id=source.session_id)
    long = _resolved(key=source.source_key, text='é' * (OPENROUTER_ADAPT_MAX_CHARS + 1), session_id=source.session_id)
    monkeypatch.setattr(service, 'resolve_source', lambda request: short if request['text'] == 'short' else long)
    _, policy = service._policy()
    MessageSummary.objects.create(session_id=source.session_id, source_key=long.source_key,
        source_hash=long.source_hash, policy_key=policy, requested_model='openrouter', engine='luna', text='Saved summary', completed_at=timezone.now())
    descriptors = service.resolve_sources([{'text': 'short'}, {'text': 'long'}])
    assert descriptors[0]['openrouter_eligible'] is True
    assert descriptors[1]['openrouter_eligible'] is False
    assert descriptors[1]['eligible'] is True and descriptors[1]['saved']['id']
    assert service._runs == {} and service._handles == {}


def test_openrouter_source_limit_uses_cleaned_input_not_normalized_content(service, monkeypatch):
    from twicc.providers.text_calls import OPENROUTER_ADAPT_MAX_CHARS

    # Whitespace contributes to routing input even when the visible content count is small.
    text = 'Visible.' + ' ' * OPENROUTER_ADAPT_MAX_CHARS
    descriptor = service.resolve_sources([{'text': text, 'purpose': 'selection'}])[0]
    assert descriptor['content_chars'] == 8
    assert descriptor['openrouter_eligible'] is False


def test_summary_prompt_asks_a_third_of_the_source_within_bounds(service):
    assert service.summary_target_words('word ' * 30) == service.SUMMARY_MIN_TARGET_WORDS
    assert service.summary_target_words('word ' * 150) == 50
    assert service.summary_target_words('word ' * 3000) == service.SUMMARY_MAX_WORDS
    assert service.summary_target_words('é' * 1200) == 66, 'a text without spaces counts one word per six characters'
    prompt = service.summary_prompt('Text with {braces} inside')
    assert 'aim for 15 words' in prompt and 'Text with {braces} inside' in prompt


def test_summary_prompt_shares_the_speech_rules_of_the_adaptation(service):
    from twicc.speech_rules import SPEECH_IDENTIFIER_RULES, SPEECH_NEVER_SAID
    from twicc.tts.adapt import ADAPT_PROMPT

    prompt = service.summary_prompt('Commit 9e04b7a')
    assert SPEECH_NEVER_SAID in prompt and SPEECH_IDENTIFIER_RULES in prompt
    assert SPEECH_NEVER_SAID in ADAPT_PROMPT and SPEECH_IDENTIFIER_RULES in ADAPT_PROMPT
    assert 'fixed in commit 9e04b7a' in prompt


def speech_reference(handle, source=None, **changes):
    if source is None:
        source = {'session_id': handle.descriptor['session_id'], 'line_num': handle.descriptor['first_line']}
    return {'source': source, **{key: handle.descriptor[key] for key in ('source_key', 'source_hash', 'policy_key')},
            'handle_id': handle.id, **changes}


def test_retain_summary_for_speech_owns_running_handle_after_page_release(service, source, monkeypatch):
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        async def stream(*_):
            try:
                entered.set()
                yield 'Partial summary'
                await asyncio.Event().wait()
            finally:
                closed.set()
        monkeypatch.setattr(service, 'stream_text', stream)
        page = await service.acquire_summary({})
        await entered.wait()
        reader = await service.retain_summary_for_speech(speech_reference(page))
        assert isinstance(reader, service.SummaryReader)
        assert reader.id != page.id
        await service.release_summary(page.id)
        assert not closed.is_set()
        await reader.aclose()
        assert closed.is_set()
        assert not service._handles
    asyncio.run(scenario())


def test_retain_summary_for_speech_reconstructs_released_handle(service, source, monkeypatch):
    calls = []
    async def stream(*_):
        calls.append(True)
        yield 'Independent summary'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        page = await service.acquire_summary({})
        reference = speech_reference(page)
        await service.release_summary(page.id)
        reader = await service.retain_summary_for_speech(reference)
        snapshots = [item async for item in reader.snapshots()]
        assert snapshots[-1]['state'] == 'complete'
        assert snapshots[-1]['text'] == 'Independent summary'
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['source_key', 'source_hash', 'policy_key'])
@pytest.mark.parametrize('live', [False, True])
def test_retain_summary_for_speech_rejects_descriptor_mismatch(service, source, monkeypatch, field, live):
    async def stream(*_):
        yield 'Partial'
        await asyncio.Event().wait()
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        page = await service.acquire_summary({})
        reference = speech_reference(page, **{field: 'different'})
        if not live:
            await service.release_summary(page.id)
        before = set(service._handles)
        with pytest.raises(service.SummaryUnavailable, match='changed'):
            await service.retain_summary_for_speech(reference)
        assert set(service._handles) == before
        await service.release_summary(page.id)
    asyncio.run(scenario())


def test_retain_saved_summary_uses_exact_result_and_rejects_replacement(service, source, monkeypatch):
    async def stream(*_):
        yield 'Frozen saved body.'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        page = await service.acquire_summary({})
        final = await terminal(service, page)
        saved_id, result_hash = final['saved_id'], final['result_hash']
        await service.release_summary(page.id)
        reader = await service.retain_summary_for_speech({'saved_id': saved_id, 'result_hash': result_hash})
        assert reader.snapshot['text'] == 'Frozen saved body.'
        assert reader.snapshot['saved_id'] == saved_id
        await sync_to_async(MessageSummary.objects.filter(pk=saved_id).update)(text='Replacement body.')
        assert reader.snapshot['text'] == 'Frozen saved body.'
        with pytest.raises(service.SummaryChanged) as caught:
            await service.retain_summary_for_speech({'saved_id': saved_id, 'result_hash': result_hash})
        assert caught.value.saved_id == saved_id
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())


def test_retain_summary_for_speech_rejects_handle_without_reconstructible_source(service):
    async def scenario():
        with pytest.raises(ValueError):
            await service.retain_summary_for_speech({'handle_id': 'page-only'})
    asyncio.run(scenario())


async def complete_temporary_reference(service, monkeypatch, hint):
    async def stream(*_):
        yield 'Summary of source A.'
    monkeypatch.setattr(service, 'stream_text', stream)
    source = {'text': 'Temporary source A.', 'purpose': 'selection'}
    page = await service.acquire_summary(source)
    await terminal(service, page)
    reference = speech_reference(page, source)
    if hint == 'none':
        reference.pop('handle_id')
    elif hint == 'released':
        await service.release_summary(page.id)
    return page, reference


@pytest.mark.parametrize('hint', ['none', 'live', 'released'])
@pytest.mark.parametrize('source', [{}, {'text': 'Missing purpose'}, {'text': '', 'purpose': 'selection'},
    {'text': 'Source', 'purpose': 'unsupported'}, {'session_id': 'session', 'line_num': True},
    {'session_id': '', 'line_num': 1}, {'event_id': 0}, {'event_id': 1, 'text': 'Unexpected'}])
def test_retain_summary_rejects_malformed_real_source_with_any_hint(service, monkeypatch, hint, source):
    async def scenario():
        page, reference = await complete_temporary_reference(service, monkeypatch, hint)
        reference['source'] = source
        reader = None
        try:
            with pytest.raises(ValueError):
                reader = await service.retain_summary_for_speech(reference)
        finally:
            if reader is not None:
                await reader.aclose()
            await service.release_summary(page.id)
        assert not service._handles
    asyncio.run(scenario())


@pytest.mark.parametrize('hint', ['none', 'live', 'released'])
def test_retain_summary_rejects_changed_temporary_text_with_any_hint(service, monkeypatch, hint):
    async def scenario():
        page, reference = await complete_temporary_reference(service, monkeypatch, hint)
        reference['source'] = {'text': 'Different temporary source B.', 'purpose': 'selection'}
        reader = None
        try:
            with pytest.raises(service.SummaryUnavailable, match='changed'):
                reader = await service.retain_summary_for_speech(reference)
        finally:
            if reader is not None:
                await reader.aclose()
            await service.release_summary(page.id)
        assert not service._handles
    asyncio.run(scenario())


@pytest.mark.parametrize('hint', ['none', 'live', 'released'])
def test_retain_summary_accepts_real_temporary_source_with_any_hint(service, monkeypatch, hint):
    async def scenario():
        page, reference = await complete_temporary_reference(service, monkeypatch, hint)
        reader = await service.retain_summary_for_speech(reference)
        assert (await terminal(service, reader))['text'] == 'Summary of source A.'
        assert reader.id != page.id
        if hint == 'live':
            assert reader._run is page._run
        await service.release_summary(page.id)
        assert reader.snapshot['state'] == 'complete'
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())


@pytest.mark.parametrize('hint', ['none', 'live', 'released'])
@pytest.mark.parametrize('kind', ['chat', 'event'])
def test_retain_summary_accepts_real_message_sources_with_any_hint(service, monkeypatch, live_session, hint, kind):
    f = live_session
    f.append(f.message('answer', 'Message source. ' * 100))
    source = {'session_id': f.session.id, 'line_num': 1} if kind == 'chat' else {'event_id': f.events()[0].id}
    async def stream(*_):
        yield 'Summary of message.'
    monkeypatch.setattr(service, 'stream_text', stream)
    async def scenario():
        page = await service.acquire_summary(source)
        await terminal(service, page)
        reference = speech_reference(page, source)
        if hint == 'none':
            reference.pop('handle_id')
        elif hint == 'released':
            await service.release_summary(page.id)
        if hint == 'live':
            monkeypatch.setattr(service, 'resolve_source', lambda _: pytest.fail('Live reader rereads mutable source'))
            monkeypatch.setattr(service, '_policy', lambda: pytest.fail('Live reader resolves current policy'))
        reader = await service.retain_summary_for_speech(reference)
        assert reader.snapshot['text'] == 'Summary of message.'
        assert reader.id != page.id
        await service.release_summary(page.id)
        assert reader.snapshot['state'] == 'complete'
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())


def test_retain_live_summary_preserves_frozen_policy_after_settings_change(service, monkeypatch):
    async def scenario():
        page, reference = await complete_temporary_reference(service, monkeypatch, 'live')
        frozen_policy = page.descriptor['policy_key']
        monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'luna'})
        assert service._policy()[1] != frozen_policy
        reader = await service.retain_summary_for_speech(reference)
        assert reader._run is page._run
        assert reader._run.policy == frozen_policy
        await service.release_summary(page.id)
        assert reader.snapshot['text'] == 'Summary of source A.'
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())


def test_retain_summary_validation_allows_unrelated_event_loop_callback(service, monkeypatch):
    import threading

    async def scenario():
        page, reference = await complete_temporary_reference(service, monkeypatch, 'live')
        loop = asyncio.get_running_loop()
        callback_ran = threading.Event()
        original_validate = service.validate_source
        def controlled_validate(source):
            loop.call_soon_threadsafe(callback_ran.set)
            assert callback_ran.wait(2), 'Source validation blocks the unrelated event-loop callback'
            return original_validate(source)
        monkeypatch.setattr(service, 'validate_source', controlled_validate)
        reader = None
        try:
            reader = await service.retain_summary_for_speech(reference)
            assert callback_ran.is_set()
            assert reader._run is page._run
            assert reader.id != page.id
        finally:
            if reader is not None:
                await reader.aclose()
            await service.release_summary(page.id)
        assert not service._handles
    asyncio.run(scenario())

def test_descriptorless_temporary_reader_freezes_model_and_releases_independently(service, monkeypatch):
    async def scenario():
        gate, entered = asyncio.Event(), asyncio.Event()
        choices = []
        from twicc.providers.text_calls import ENGINES
        def engine(requested, *_):
            choices.append(requested)
            return ENGINES[0], None
        async def stream(*_):
            entered.set()
            await gate.wait()
            yield 'Independent summary.'
        monkeypatch.setattr(service, 'resolve_engine', engine)
        monkeypatch.setattr(service, 'stream_text', stream)
        monkeypatch.setattr(service, 'read_synced_settings', lambda: {'summaryModel': 'luna'})
        reference = {'source': {'text': 'Captured selection', 'purpose': 'selection'}, 'requested_model': 'haiku'}
        reader = await service.retain_summary_for_speech(reference)
        await entered.wait()
        assert choices == ['haiku']
        assert reader._run.source.canonical_text == 'Captured selection'
        assert reader._run.requested == 'haiku'
        assert reader._run.policy == service._policy_for('haiku')[1]
        gate.set()
        assert (await terminal(service, reader))['text'] == 'Independent summary.'
        await reader.aclose()
        assert not service._handles
    asyncio.run(scenario())

@pytest.mark.parametrize('reference', [
    {'source': {'event_id': 1}, 'requested_model': 'auto'},
    {'source': {'text': 'Text', 'purpose': 'invalid'}, 'requested_model': 'auto'},
    {'source': {'text': 'Text', 'purpose': 'selection'}, 'requested_model': 'invalid'},
    {'source': {'text': 'Text', 'purpose': 'selection'}, 'requested_model': 'auto', 'source_hash': 'forged'},
])
def test_descriptorless_reader_rejects_non_temporary_or_injected_policy(service, reference):
    async def scenario():
        with pytest.raises(ValueError):
            await service.retain_summary_for_speech(reference)
        assert not service._handles
    asyncio.run(scenario())
