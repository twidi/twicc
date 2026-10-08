"""Canonical publication catalogs remain identical across compute paths."""

import base64
import queue
import subprocess
from pathlib import Path

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionType, Share
from twicc.core.serializers import serialize_session, serialize_share_public_meta
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.live_sync import LiveSyncLimits


TAG = '<twicc:inline-artifact id="preferences" src="inline-artifacts/preferences/index.html" />'
TEXT = '😀\r\n\r\n  ' + TAG + '\r\n'
RECORD = {
    'artifact_id': 'preferences', 'line_num': 1, 'text_block_index': 1,
    'tag_offset': 7, 'src': 'inline-artifacts/preferences/index.html',
    'title': 'preferences', 'height': 360,
}


def assistant(provider, text=TEXT, *, source_id='message-1'):
    if provider == Provider.CLAUDE_CODE:
        return {'type': 'assistant', 'uuid': source_id, 'timestamp': '2026-10-08T10:00:00Z',
                'message': {'id': 'msg-1', 'role': 'assistant', 'content': [
                    {'type': 'thinking', 'thinking': TAG}, {'type': 'text', 'text': text}]}}
    return {'type': 'event_msg', 'timestamp': '2026-10-08T10:00:00Z', 'payload': {
        'type': 'item_completed', 'thread_id': 's', 'turn_id': 't', 'item': {
            'type': 'AgentMessage', 'id': source_id, 'phase': 'commentary', 'content': [
                {'type': 'Image', 'url': 'x'}, {'type': 'Text', 'text': text}]}}}


def mirror(provider):
    if provider == Provider.CLAUDE_CODE:
        return {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': TAG}]}}
    return {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': TEXT}]}}


def capture(compute, session):
    results = queue.Queue()
    compute.compute_session_metadata(session.id, results, run_id=0)
    return orjson.loads(results.get_nowait())


def ingest(compute, session, path, records, *, append=False, slice_bytes=256):
    with path.open('ab' if append else 'wb') as stream:
        for record in records:
            stream.write(orjson.dumps(record) + b'\n')
    while True:
        result = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits(max_bytes=slice_bytes))
        if not result.has_more:
            break
    session.refresh_from_db()


@pytest.fixture(params=[Provider.CLAUDE_CODE, Provider.CODEX])
def case(request, db, tmp_path):
    provider = request.param
    session = Session.objects.create(id='s', file_path='s.jsonl', project=Project.objects.create(id='p'), provider=provider)
    compute = ClaudeCodeSessionCompute() if provider == Provider.CLAUDE_CODE else CodexSessionCompute()
    return provider, session, compute, tmp_path / 'rollout.jsonl'


def test_live_and_full_publish_identical_catalog(case):
    provider, session, compute, path = case
    ingest(compute, session, path, [assistant(provider), mirror(provider), assistant(provider, TAG, source_id='message-2')])
    expected = {'schema': 1, 'publications': [RECORD, {**RECORD, 'line_num': 3, 'tag_offset': 0}]}
    if provider == Provider.CLAUDE_CODE:
        expected['source_messages'] = {'message-1': 1, 'message-2': 3}
    assert session.inline_artifacts == expected
    rebuilt = capture(compute, session)
    assert rebuilt['session_fields']['inline_artifacts'] == expected
    session.inline_artifacts = {}
    session.save(update_fields=['inline_artifacts'])
    compute.apply_session_complete(rebuilt)
    session.refresh_from_db()
    assert session.inline_artifacts == expected
    assert serialize_session(session)['inline_artifacts'] == {'preferences': expected['publications'][1]}


def test_finalized_text_publishes_before_turn_end_and_invalid_tag_keeps_latest(case):
    provider, session, compute, path = case
    ingest(compute, session, path, [assistant(provider)])
    assert session.inline_artifacts['publications'] == [RECORD]
    ingest(compute, session, path, [assistant(provider, TAG.replace('index.html', 'index.js'), source_id='message-2')], append=True)
    assert serialize_session(session)['inline_artifacts'] == {'preferences': RECORD}
    # No files exist. Catalog validity depends only on finalized text.
    assert not (path.parent / 'inline-artifacts').exists()


def test_reingestion_and_recompute_keep_source_identity(case):
    provider, session, compute, path = case
    ingest(compute, session, path, [assistant(provider)])
    session.last_offset = 0
    session.last_line = 0
    session.save(update_fields=['last_offset', 'last_line'])
    compute.sync_session_slice(session.id, path, limits=LiveSyncLimits())
    compute.apply_session_complete(capture(compute, session))
    session.refresh_from_db()
    assert session.inline_artifacts['publications'] == [RECORD]


@pytest.mark.parametrize('copied_parent', [False, True])
def test_native_subagents_never_publish_and_recompute_clears_stale_catalog(case, copied_parent, monkeypatch):
    provider, session, compute, path = case
    session.type = SessionType.SUBAGENT
    if copied_parent:
        parent = Session.objects.create(id='parent', file_path='parent.jsonl', project=session.project, provider=provider)
        session.parent_session = parent
    session.save()

    def forbidden_hook(*_args):
        raise AssertionError('subagent extraction hook must never run')

    monkeypatch.setattr(compute, 'extract_inline_artifact_texts', forbidden_hook, raising=False)
    ingest(compute, session, path, [assistant(provider)])
    assert session.inline_artifacts == {}
    session.inline_artifacts = {'schema': 1, 'publications': [RECORD]}
    session.save(update_fields=['inline_artifacts'])
    compute.apply_session_complete(capture(compute, session))
    session.refresh_from_db()
    assert session.inline_artifacts == {}
    assert serialize_session(session)['inline_artifacts'] == {}


def test_nonassistant_and_unfinished_messages_never_publish(case):
    provider, session, compute, path = case
    if provider == Provider.CLAUDE_CODE:
        records = [mirror(provider), {'type': 'assistant', 'message': {'content': [
            {'type': 'thinking', 'thinking': TAG}, {'type': 'tool_use', 'id': 'tool-1',
             'name': 'Read', 'input': {'file_path': TAG}}]}},
            {'type': 'system', 'subtype': 'local_command', 'content': '<local-command-stdout>' + TAG + '</local-command-stdout>'},
            {'type': 'assistant', 'isApiErrorMessage': True, 'message': {'content': [{'type': 'text', 'text': TAG}]}}]
    else:
        records = [mirror(provider), {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': TAG}},
            {'type': 'event_msg', 'payload': {'type': 'item_started', 'item': {'type': 'AgentMessage', 'content': [{'type': 'Text', 'text': TAG}]}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'Reasoning', 'content': [{'type': 'Text', 'text': TAG}]}}},
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'UserMessage', 'content': [{'type': 'text', 'text': TAG}]}}}]
    ingest(compute, session, path, records)
    assert session.inline_artifacts == {}
    compute.apply_session_complete(capture(compute, session))
    session.refresh_from_db()
    assert session.inline_artifacts == {}


def test_public_metadata_has_only_safe_support_flag(case):
    provider, session, compute, path = case
    ingest(compute, session, path, [assistant(provider)])
    share = Share.objects.create(kind='session', token='a' * 64, session=session,
                                 inline_artifact_exports={'owner_path': '/private/location'})
    public = serialize_share_public_meta(share)
    assert public['inline_artifacts_supported'] is True
    assert 'inline_artifacts' not in public
    assert 'inline_artifact_exports' not in public
    assert '/private/location' not in orjson.dumps(public).decode()
    session.type = SessionType.SUBAGENT
    assert serialize_share_public_meta(share)['inline_artifacts_supported'] is False


@pytest.mark.parametrize('session_type', [SessionType.SESSION, SessionType.SUBAGENT])
@pytest.mark.parametrize('public', [False, True])
def test_serialized_identity_reaches_actual_inline_context_and_adapter(case, session_type, public):
    """Feed real wire payloads into the owning Vue context and JavaScript adapter."""
    provider, session, compute, path = case
    session.type = session_type
    session.save(update_fields=['type'])
    ingest(compute, session, path, [assistant(provider)])
    record = orjson.loads(SessionItem.objects.get(session=session, line_num=1).content)
    share = Share.objects.create(kind='session', token='c' * 64, session=session)
    wire = serialize_share_public_meta(share) if public else serialize_session(session)
    javascript = '''
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { computed, ref, shallowRef, watch, unref, effectScope } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { useFramePoolStore } from './src/stores/framePool.js';
import { makeOwnerInlineAdapter } from './src/inline-artifacts/ownerAdapter.js';
import { createInlineArtifactRuntime } from './src/inline-artifacts/runtime.js';
import { INLINE_ARTIFACT_CONTEXT } from './src/inline-artifacts/context.js';
import { assistantTextBlocks } from './src/providers/codex/canonical.js';
import { createInlineTextContext, displayInlineText, inlineArtifactPlacement } from './src/inline-artifacts/rendering.js';
import { splitMarkdownBlocks } from './src/utils/markdown.js';
const { wire, record, provider, expectedType, public: isPublic } = JSON.parse(fs.readFileSync(0, 'utf8'));
function excerpt(path, start, end, dependencies, result) {
    const source = fs.readFileSync(path, 'utf8');
    const left = source.indexOf(start), right = source.indexOf(end, left);
    assert.ok(left >= 0 && right > left, path);
    return new Function(...Object.keys(dependencies), source.slice(left, right) + '\\n' + result)(...Object.values(dependencies));
}
if (isPublic) {
    let seeded;
    excerpt('src/share-session/ShareSessionApp.vue', 'store.setSession({', '// Show-timestamps:',
        { store: { setSession: value => { seeded = value; } }, meta: wire }, '');
    assert.equal(seeded.type, expectedType);
    assert.equal(wire.inline_artifacts_supported, expectedType === 'session');
} else {
    const scope = effectScope();
    scope.run(() => {
        setActivePinia(createPinia());
        let provided;
        const store = { getSession: id => id === wire.id ? wire : null };
        const dependencies = { computed, ref, shallowRef, watch, unref, INLINE_ARTIFACT_CONTEXT,
            session: computed(() => store.getSession(wire.id)), sessionId: ref(wire.id), isActive: ref(true),
            store, useFramePoolStore, makeOwnerInlineAdapter, createInlineArtifactRuntime,
            apiFetch: () => { throw new Error('No probe before a visible placement'); },
            provide: (key, value) => { assert.equal(key, INLINE_ARTIFACT_CONTEXT); provided = value; },
            onBeforeUnmount: () => {},
        };
        const runtime = excerpt('src/views/SessionView.vue', '// Freeze the source identity',
            '// ─── Artifacts tab', dependencies, 'return inlineRuntime.value;');
        assert.equal(Boolean(runtime), expectedType === 'session');
        // Native sessions without parent metadata must still remain excluded.
        assert.equal(wire.type, expectedType);
        const props = { sessionId: wire.id, parentSessionId: null, kind: 'assistant_message',
            lineNum: 1, content: record, syntheticKind: null };
        const listContext = excerpt('src/components/session/detail/SessionItemsList.vue',
            'const inheritedInlineContext =', 'watch([inlineContext,', {
                props, computed, unref, INLINE_ARTIFACT_CONTEXT, session: dependencies.session,
                inject: () => provided, provide: () => {},
            }, 'return inlineContext;');
        const itemContext = excerpt('src/components/session/detail/SessionItem.vue',
            'const providedInlineContext =', '// Whether this item', {
                props, computed, unref, INLINE_ARTIFACT_CONTEXT, dataStore: store, inject: () => listContext,
            }, 'return inlineContext.value;');
        assert.equal(Boolean(itemContext), expectedType === 'session');
        if (runtime) {
            const adapter = makeOwnerInlineAdapter({ sessionId: wire.id, store, api: dependencies.apiFetch });
            assert.equal(adapter.manifest().descriptors.length, 1);
            const blocks = provider === 'codex' ? assistantTextBlocks(record) : record.message.content.flatMap((b, i) =>
                b.type === 'text' ? [{ textBlockIndex: i, text: b.text }] : []);
            const context = createInlineTextContext(itemContext, blocks);
            const display = displayInlineText(blocks.map(b => b.text).join(''), context);
            const widgets = splitMarkdownBlocks(display.source, { inlineArtifacts: true, ...display.inlineContext })
                .blocks.filter(block => block.type === 'inline-artifact');
            assert.equal(widgets.length, 1);
            assert.equal(inlineArtifactPlacement(context, widgets[0].span, runtime).status, 'ready');
            assert.equal(adapter.documentUrl(adapter.manifest().descriptors[0]),
                '/api/sessions/s/inline-artifacts/preferences/index.html');
            runtime.dispose();
        } else {
            assert.equal(unref(provided), null);
            assert.equal(makeOwnerInlineAdapter({ sessionId: wire.id, store, api: dependencies.apiFetch })
                .manifest().descriptors.length, 0);
        }
    });
    scope.stop();
}
'''
    result = subprocess.run(
        ['node', '--input-type=module', '--eval', javascript], cwd=Path(__file__).resolve().parents[1] / 'frontend',
        input=orjson.dumps({'wire': wire, 'record': record, 'provider': provider.value,
                           'expectedType': session_type, 'public': public}),
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b''


def test_migration_defaults_are_empty_and_independent(db):
    project = Project.objects.create(id='defaults')
    first = Session.objects.create(id='first', file_path='first.jsonl', project=project)
    second = Session.objects.create(id='second', file_path='second.jsonl', project=project)
    share = Share.objects.create(kind='session', token='b' * 64, session=first)
    first.refresh_from_db()
    second.refresh_from_db()
    share.refresh_from_db()
    assert first.inline_artifacts == second.inline_artifacts == share.inline_artifact_exports == {}
    first.inline_artifacts['schema'] = 1
    assert second.inline_artifacts == {}


def test_screenshot_normalization_offsets_use_persisted_text(case):
    provider, session, compute, path = case
    ingest(compute, session, path, [assistant(provider, '<twicc:insert-screenshot />\r\n\r\n' + TAG)])
    publication = session.inline_artifacts['publications'][0]
    # Screenshot normalization changes source length; Codex coalesces blocks.
    assert publication == {**RECORD, 'text_block_index': 1 if provider == Provider.CLAUDE_CODE else 0,
                           'tag_offset': 31}
    rebuilt = capture(compute, session)
    assert rebuilt['session_fields']['inline_artifacts'] == session.inline_artifacts


@pytest.mark.parametrize('slice_bytes', [256, 4096])
def test_persisted_screenshot_publication_matches_actual_frontend_source(case, monkeypatch, tmp_path, slice_bytes):
    """Cross the provider rewrite, DB, catalog, and actual JS renderer boundary."""
    from twicc import paths

    provider, session, compute, path = case
    data_dir = tmp_path / 'isolated-data'
    monkeypatch.setattr(paths, 'get_data_dir', lambda: data_dir)
    image = b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR'
    encoded = base64.b64encode(image).decode()
    timestamp = '2026-10-08T10:00:00Z'
    if provider == Provider.CLAUDE_CODE:
        tool_result = {'type': 'user', 'timestamp': timestamp, 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'capture', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': encoded}}]}]}}
    else:
        tool_result = {'type': 'response_item', 'timestamp': timestamp, 'payload': {
            'type': 'function_call_output', 'call_id': 'capture', 'output': [
                {'type': 'input_image', 'image_url': 'data:image/png;base64,' + encoded}]}}
    source = ' \r\n😀\r\n\r\n<twicc:insert-screenshot title="Capture" />\r\n\r\n' + TAG + '\r\n '
    frozen = 'Existing user-owned addendum. No inline instructions.'
    session.system_prompt_addendum = frozen
    session.save(update_fields=['system_prompt_addendum'])
    ingest(compute, session, path, [tool_result, assistant(provider, source)], slice_bytes=slice_bytes)
    persisted = orjson.loads(SessionItem.objects.get(session=session, line_num=2).content)
    index = 1 if provider == Provider.CLAUDE_CODE else 0
    content = persisted['message']['content'] if provider == Provider.CLAUDE_CODE else persisted['payload']['item']['content']
    normalized = content[index]['text']
    filename = '2026-10-08-10-00-00-capture-off0.png'
    expected_text = ' \r\n😀\r\n\r\n![Capture](/artifacts/s/' + filename + ')\r\n\r\n' + TAG + '\r\n '
    assert normalized == expected_text
    assert (data_dir / 'artifacts' / session.id / filename).read_bytes() == image
    publication = session.inline_artifacts['publications'][0]
    assert publication == {**RECORD, 'line_num': 2, 'text_block_index': index,
                           'tag_offset': expected_text.index(TAG)}

    # Consume the DB value, not a hand-written frontend normalization fixture.
    javascript = '''
import fs from 'node:fs';
import assert from 'node:assert/strict';
import { assistantTextBlocks } from './frontend/src/providers/codex/canonical.js';
import { createInlineTextContext, displayInlineText } from './frontend/src/inline-artifacts/rendering.js';
import { splitMarkdownBlocks } from './frontend/src/utils/markdown.js';
import { publicationKey } from './frontend/src/inline-artifacts/publications.js';
const { record, provider, publication } = JSON.parse(fs.readFileSync(0, 'utf8'));
const textBlocks = provider === 'codex' ? assistantTextBlocks(record) : record.message.content.flatMap((b, i) =>
    b.type === 'text' ? [{textBlockIndex: i, text: b.text}] : []);
const context = createInlineTextContext({sessionId: 's', lineNum: 2, finalized: true, publicationAllowed: true}, textBlocks);
const display = displayInlineText(textBlocks.map(b => b.text).join(''), context);
const blocks = splitMarkdownBlocks(display.source, {inlineArtifacts: true, ...display.inlineContext}).blocks;
const widgets = blocks.filter(b => b.type === 'inline-artifact');
assert.equal(widgets.length, 1);
const span = widgets[0].span;
assert.equal(span.textBlockIndex, publication.text_block_index);
assert.equal(span.tag_offset, publication.tag_offset);
assert.equal(span.descriptor.artifact_id, publication.artifact_id);
const actual = {line_num: 2, text_block_index: span.textBlockIndex, tag_offset: span.tag_offset};
assert.equal(publicationKey('s', actual), publicationKey('s', publication));
'''
    result = subprocess.run(
        ['node', '--input-type=module', '--eval', javascript],
        cwd=Path(__file__).resolve().parents[1],
        input=orjson.dumps({'record': persisted, 'provider': provider.value, 'publication': publication}),
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b''
    compute.apply_session_complete(capture(compute, session))
    session.refresh_from_db()
    assert session.inline_artifacts['publications'] == [publication]
    assert session.system_prompt_addendum == frozen
    assert orjson.loads(SessionItem.objects.get(session=session, line_num=2).content) == persisted


def test_claude_string_text_uses_block_zero(db, tmp_path):
    session = Session.objects.create(id='hybrid', file_path='hybrid.jsonl',
                                    project=Project.objects.create(id='hybrid-p'), provider=Provider.CLAUDE_CODE)
    compute = ClaudeCodeSessionCompute()
    ingest(compute, session, tmp_path / 'hybrid.jsonl', [
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': TAG}},
        {'type': 'assistant', 'message': {}},
    ])
    assert session.inline_artifacts == {'schema': 1, 'publications': [
        {**RECORD, 'text_block_index': 0, 'tag_offset': 0}]}
    assert capture(compute, session)['session_fields']['inline_artifacts'] == session.inline_artifacts


def test_codex_normalized_plan_preserves_publication_source(db, tmp_path):
    session = Session.objects.create(id='plan', file_path='plan.jsonl',
                                    project=Project.objects.create(id='plan-p'), provider=Provider.CODEX)
    compute = CodexSessionCompute()
    source = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
        'content': [{'type': 'output_text', 'text': '<proposed_plan>\r\n\r\n' + TAG + '\r\n\r\n</proposed_plan>'}]}}
    ingest(compute, session, tmp_path / 'plan.jsonl', [source])
    assert session.inline_artifacts == {'schema': 1, 'publications': [
        {**RECORD, 'text_block_index': 0, 'tag_offset': 19}]}
    assert capture(compute, session)['session_fields']['inline_artifacts'] == session.inline_artifacts


def test_generated_migration_initializes_existing_rows(tmp_path, django_db_blocker):
    """Apply the generated operations to existing rows in a disposable DB."""
    from importlib import import_module

    from django.db import connections, models
    from django.db.migrations.state import ModelState, ProjectState
    from twicc.db.backends.sqlite3.base import DatabaseWrapper

    alias = 'inline_catalog_migration'
    config = {**connections['default'].settings_dict, 'NAME': str(tmp_path / 'migration.sqlite3')}
    connection = DatabaseWrapper(config, alias)
    setattr(connections._connections, alias, connection)
    state = ProjectState()
    for name in ('Session', 'Share'):
        state.add_model(ModelState('core', name, [('id', models.CharField(primary_key=True, max_length=64))]))
    migration = import_module('twicc.core.migrations.0154_inline_artifact_catalogs').Migration('0154', 'core')
    try:
        with django_db_blocker.unblock():
            with connection.schema_editor() as editor:
                for name in ('Session', 'Share'):
                    model = state.apps.get_model('core', name)
                    editor.create_model(model)
                    model.objects.using(alias).create(id='existing')
            with connection.schema_editor() as editor:
                state = migration.apply(state, editor)
            session = state.apps.get_model('core', 'Session').objects.using(alias).get(id='existing')
            share = state.apps.get_model('core', 'Share').objects.using(alias).get(id='existing')
            assert session.inline_artifacts == share.inline_artifact_exports == {}
    finally:
        connection.close()
        delattr(connections._connections, alias)


@pytest.mark.parametrize('restart', [False, True])
def test_claude_compaction_replay_does_not_move_publication(db, tmp_path, restart):
    session = Session.objects.create(id='replay', file_path='replay.jsonl',
                                    project=Project.objects.create(id='replay-p'), provider=Provider.CLAUDE_CODE)
    compute = ClaudeCodeSessionCompute()
    source = assistant(Provider.CLAUDE_CODE)
    path = tmp_path / 'replay.jsonl'
    ingest(compute, session, path, [source])
    original = session.inline_artifacts
    if restart:
        compute = ClaudeCodeSessionCompute()
    ingest(compute, session, path, [source], append=True)
    assert session.inline_artifacts == original
    assert serialize_session(session)['inline_artifacts'] == {'preferences': RECORD}
    assert capture(compute, session)['session_fields']['inline_artifacts'] == original


@pytest.mark.parametrize('restart', [False, True])
def test_claude_old_replay_cannot_supersede_real_correction(db, tmp_path, restart):
    session = Session.objects.create(id='correction', file_path='correction.jsonl',
                                    project=Project.objects.create(id='correction-p'), provider=Provider.CLAUDE_CODE)
    compute = ClaudeCodeSessionCompute()
    source = assistant(Provider.CLAUDE_CODE)
    correction = assistant(Provider.CLAUDE_CODE, TAG.replace('index.html', 'corrected.html'), source_id='message-2')
    path = tmp_path / 'correction.jsonl'
    ingest(compute, session, path, [source, correction])
    corrected = session.inline_artifacts
    if restart:
        compute = ClaudeCodeSessionCompute()
    ingest(compute, session, path, [source], append=True)
    assert session.inline_artifacts == corrected
    assert serialize_session(session)['inline_artifacts'] == {'preferences': {
        **RECORD, 'line_num': 2, 'tag_offset': 0, 'src': 'inline-artifacts/preferences/corrected.html'}}
    assert capture(compute, session)['session_fields']['inline_artifacts'] == corrected


def test_claude_distinct_uuids_with_same_api_message_id_publish_independently(db, tmp_path):
    session = Session.objects.create(id='blocks', file_path='blocks.jsonl',
                                    project=Project.objects.create(id='blocks-p'), provider=Provider.CLAUDE_CODE)
    first = assistant(Provider.CLAUDE_CODE)
    second = assistant(Provider.CLAUDE_CODE, source_id='message-2')
    compute = ClaudeCodeSessionCompute()
    ingest(compute, session, tmp_path / 'blocks.jsonl', [first, second])
    assert session.inline_artifacts['publications'] == [RECORD, {**RECORD, 'line_num': 2}]
    assert session.inline_artifacts['source_messages'] == {'message-1': 1, 'message-2': 2}
    assert capture(compute, session)['session_fields']['inline_artifacts'] == session.inline_artifacts
    wire = serialize_session(session)['inline_artifacts']['preferences']
    assert set(wire) == {'artifact_id', 'line_num', 'text_block_index', 'tag_offset', 'src', 'title', 'height'}


def test_claude_same_slice_replay_keeps_first_source_occurrence(db, tmp_path):
    session = Session.objects.create(id='same-slice', file_path='same-slice.jsonl',
                                    project=Project.objects.create(id='same-slice-p'), provider=Provider.CLAUDE_CODE)
    compute = ClaudeCodeSessionCompute()
    source = assistant(Provider.CLAUDE_CODE)
    correction = assistant(Provider.CLAUDE_CODE, TAG, source_id='message-2')
    ingest(compute, session, tmp_path / 'same-slice.jsonl', [source, correction, source], slice_bytes=4096)
    expected = {'schema': 1, 'publications': [RECORD, {**RECORD, 'line_num': 2, 'tag_offset': 0}],
                'source_messages': {'message-1': 1, 'message-2': 2}}
    assert session.inline_artifacts == expected
    assert capture(compute, session)['session_fields']['inline_artifacts'] == expected
