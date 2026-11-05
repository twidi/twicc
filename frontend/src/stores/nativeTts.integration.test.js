import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import { useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'
import { useTimelineSpeechStore } from './timelineSpeech.js'
import { useTimelineFiltersStore } from './timelineFilters.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'
import { setPcmStreamPlayerFactory } from '../utils/pcmStreamPlayer.js'
beforeEach(() => { setActivePinia(createPinia()); _resetSpeechForTests(); setSpeechPlayerFactory(() => assert.fail('native cannot create HTMLAudio')); setPcmStreamPlayerFactory(() => assert.fail('native cannot create AudioContext')) })
async function fixture(snapshot = {}) {
    const calls = []; let observer
    const client = { attach: async () => true, state: () => ({ revision: 1, playback: { status: 'idle' }, listening: null, ...snapshot }), subscribe(fn) { observer = fn; return () => {} }, manual(request) { calls.push(['manual', request]); return Promise.resolve({ ok: true, ended: Promise.resolve({ ok: true }) }) }, listen(a) { calls.push(['listen', a]); return Promise.resolve({ accepted: true }) }, setMuted(v) { calls.push(['muted', v]) }, disable() { calls.push(['disable']) }, skip(id, operationId) { calls.push(['skip', id, operationId]) }, stopAll() { calls.push(['stopAll']) }, detach() { calls.push(['detach']) } }
    const tts = useTextToSpeechStore(); tts.applyConfig({ config: { active_id: 'voice', configs: [{ id: 'voice', model: 'model', voice: 'voice', response_format: 'mp3', validated: { model: 'model', voice: 'voice', response_format: 'mp3', speed: null, instructions: '' }, speed: null, instructions: '' }] }, key_present: true })
    await tts.attachNative(client); tts._openSpeech = () => assert.fail('native cannot open Web speech')
    return { tts, calls, client, publish(s) { observer(s) } }
}
test('native_summary_survives_page_release_before_socket_ready', async () => {
    const { tts, calls, client } = await fixture()
    let webReferenceCalled = false, socketReady
    client.manual = request => {
        calls.push(['manual', request])
        return new Promise(resolve => { socketReady = () => resolve({ ok: true, ended: Promise.resolve({ ok: true }) }) })
    }
    const frozenDescriptor = { source: { text: 'Captured selection', purpose: 'selection' }, source_key: null, source_hash: 'hash', policy_key: 'policy' }
    const start = tts.playSummary(() => { webReferenceCalled = true; throw new Error('Page handle released') }, {
        identity: { sourceKey: null, sourceHash: 'hash', variant: 'summary' }, nativeRequest: frozenDescriptor,
    })
    assert.equal(calls.length, 1, 'native reserves before deferred Web acquisition or socket readiness')
    // Releasing the real temporary page owner clears its source object. Native keeps its independent snapshot.
    frozenDescriptor.source.text = ''; frozenDescriptor.source_hash = 'changed'
    assert.equal(calls[0][1].summary.source.text, 'Captured selection')
    assert.equal(calls[0][1].summary.source_hash, 'hash')
    assert.equal(webReferenceCalled, false)
    socketReady()
    const result = await start
    assert.deepEqual(await result.ended, { ok: true })
})
test('all manual entry points preserve their speech settings and use native', async () => {
    const { tts, calls } = await fixture()
    await tts.play('message'); await tts.play('選'.repeat(18000), { origin: 'rendered' }); await tts.playWith('test', { model: 'model', voice: 'voice', response_format: 'mp3' }, { configId: 'voice' }); await tts.playWith('script', { kind: 'script', command: 'say', output: 'stream', audio_format: 'wav' }, { configId: 'script' })
    assert.deepEqual(calls.map(c => c[1].source), ['message', 'selection', 'test', 'test']); assert.equal(calls[1][1].speech.origin, 'rendered'); assert.equal(calls[1][1].text.length, 18000); assert.equal(calls[3][1].speech.script_id, 'script')
    const outcome = await tts.playSummary(Promise.resolve({ saved_id: 7, result_hash: 'a'.repeat(64) }), { identity: { sourceKey: 's', sourceHash: 'h', variant: 'summary' }, nativeRequest: { saved_id: 7, result_hash: 'a'.repeat(64) } }); assert.equal(outcome.ok, true)
})
test('reload restores listening filter for its exact page before watcher; navigation and disposal only detach', async () => {
    const listening = { activationId: 'a', pageId: 'project-one', includeMuted: true, scope: { kind: 'project', id: 'project-one', label: 'One' } }
    const { tts, calls, publish } = await fixture({ listening, pendingCount: 2 })
    const timeline = useTimelineSpeechStore(), filters = useTimelineFiltersStore()
    timeline.initialize({ dataStore: {}, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    assert.equal(timeline.activePageId, 'project-one'); assert.equal(filters.filtersFor('project-one').includeMuted, true); assert.equal(timeline.pendingCount, 2); assert.deepEqual(calls, [])
    filters.setIncludeMuted('other-route', true); assert.deepEqual(calls, [])
    filters.setIncludeMuted('project-one', false); assert.deepEqual(calls, [['muted', false]])
    tts.playback = { status: 'playing', source: 'message', readingId: 'manual', owner: null }; timeline.disable(); assert.deepEqual(calls.at(-1), ['disable']); assert.equal(tts.status, 'playing')
    publish({ revision: 4, playback: { status: 'idle' }, listening: null, sessionStatus: 'error', error: { message: 'Lost' } }); assert.equal(timeline.activePageId, null)
    timeline.dispose(); tts.detachNative(); assert.equal(calls.some(c => c[0] === 'stopAll'), false)
})
test('rejected native activation returns idle and cannot invoke Web playback', async () => {
    const { tts, client } = await fixture()
    client.manual = () => Promise.resolve({ ok: false, error: 'Allow notifications' })
    assert.deepEqual(await tts.play('message'), { ok: false, error: 'Allow notifications' })
    assert.equal(tts.status, 'idle')
})
test('native automatic activation sends real worktree scope and six-fraction UTC boundary without a Web coordinator', async () => {
    const { tts, calls } = await fixture()
    const timeline = useTimelineSpeechStore()
    timeline._now = () => Date.parse('2026-11-05T12:00:00Z')
    timeline.initialize({ dataStore: { projectsLoaded: true, getProject: id => ({ id, worktree_of: 'parent' }), getProjectScopeIds: id => [id], getProjectDisplayName: () => 'Worktree' }, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    await timeline.enable('worktree')
    assert.deepEqual(calls[0], ['listen', { pageId: 'worktree', scope: { kind: 'worktree', id: 'worktree', label: 'Worktree' }, includeMuted: false, boundary: '2026-11-05T12:00:00.000000Z' }])
    timeline.dispose(); tts.detachNative()
})
test('actual logout Stop all reaches native while capability negotiation still runs', async () => {
    const { tts, calls } = await fixture()
    tts.nativeMode = false; tts.nativeNegotiating = true
    await tts.stopAll(); assert.deepEqual(calls.at(-1), ['stopAll'])
})
test('a detached native mirror cannot fall back to Web audio', async () => {
    const { tts } = await fixture(); tts.detachNative()
    const result = await tts.play('message')
    assert.equal(result.ok, false); assert.equal(typeof result.error, 'string')
})
test('actual logout and authorization loss stop native before leaving authenticated work', async t => {
    const { useAuthStore } = await import('./auth.js')
    const { calls } = await fixture(), auth = useAuthStore()
    const original = globalThis.fetch
    t.after(() => { globalThis.fetch = original })
    globalThis.fetch = async url => { assert.deepEqual(calls.at(-1), ['stopAll']); assert.equal(url, '/api/auth/logout/'); return { ok: true } }
    auth.authenticated = true
    await auth.logout(); assert.equal(auth.authenticated, false)
    await auth.handleUnauthorized(); assert.equal(calls.filter(c => c[0] === 'stopAll').length, 2)
})

test('automatic preparation errors report once while manual operation errors stay with their controls', async () => {
    const { tts, client, publish } = await fixture({ listening: { activationId: 'a', pageId: 'p', includeMuted: false } })
    const timeline = useTimelineSpeechStore(), errors = []
    client.handlesOperation = id => id === 'op'
    timeline._showError = message => errors.push(message)
    timeline.initialize({ dataStore: {}, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    const error = { code: 'failed', message: 'Preparation failed', reading_id: 'automatic', generation: 1, automatic: true }
    publish({ revision: 3, playback: { status: 'idle' }, listening: { activationId: 'a', pageId: 'p', includeMuted: false }, error })
    publish({ revision: 4, playback: { status: 'idle' }, listening: { activationId: 'a', pageId: 'p', includeMuted: false }, error })
    assert.deepEqual(errors, ['Preparation failed'])
    publish({ revision: 5, playback: { status: 'idle' }, error: { ...error, reading_id: 'manual', automatic: false, operation_id: 'op' } })
    assert.deepEqual(errors, ['Preparation failed'])
    publish({ revision: 6, playback: { status: 'idle' }, error: { ...error, reading_id: 'vanished-control', automatic: false, operation_id: 'old-page' } })
    assert.deepEqual(errors, ['Preparation failed', 'Preparation failed'])
    timeline.dispose(); tts.detachNative()
})

test('real selection summary reserves native with no descriptor before page acquisition, settings change, and page release', async t => {
    const Vue = await import('vue')
    const { useSummariesStore } = await import('./summaries.js')
    const temporaryGeneration = await import('../composables/useTemporarySummaryGeneration.js')
    const { compileComponent, descendants, makeRenderer, flush } = await import('../../tests/helpers/scrollerComponentHarness.js')
    const { tts, calls, client, publish } = await fixture()
    const summaries = useSummariesStore(), errors = []
    const settings = Vue.reactive({ summaryModel: 'haiku', enabledProviders: ['claude_code'] })
    summaries._fetch = (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Released', 'AbortError'))))
    let complete
    client.manual = request => { calls.push(['manual', request]); return Object.assign(new Promise(resolve => { complete = resolve }), { operationId: 'selection-operation' }) }
    const toast = { error: error => errors.push(error), success() {} }
    const Button = compileComponent(new URL('../components/ui/TextToSpeechButton.vue', import.meta.url), {
        '../../stores/textToSpeech': { useTextToSpeechStore: () => tts }, '../../composables/useToast': { toast },
    })
    const Body = compileComponent(new URL('../components/session/detail/SelectionSummaryBody.vue', import.meta.url), {
        '../../../stores/summaries': { useSummariesStore: () => summaries }, '../../../stores/settings': { useSettingsStore: () => settings },
        '../../../stores/textToSpeech': { useTextToSpeechStore: () => tts }, '../../../composables/useTemporarySummaryGeneration': temporaryGeneration,
        '../../../composables/useToast': { toast }, '../../ui/MarkdownContent.vue': { setup: () => () => Vue.h('markdown') },
        '../../ui/SummaryGenerationLinks.vue': { setup: () => () => Vue.h('links') }, '../../ui/TextToSpeechButton.vue': Button,
    })
    const { renderer, root } = makeRenderer(), text = 'Captured source selection. '.repeat(30)
    const app = renderer.createApp({ render: () => Vue.h(Body, { text }) }); app.mount(root)
    t.after(() => { if (root.children.length) app.unmount() })
    await flush()
    assert.equal(summaries.temporary.entry.state, 'running'); assert.equal(summaries.temporary.entry.descriptor, null)
    const button = descendants(root, node => node.type === 'wa-button' && node.props['aria-label'] === 'Read aloud')[0]
    assert.ok(button)
    const click = button.props.onClick()
    assert.equal(calls.length, 1, 'native reserves before descriptor acquisition')
    assert.deepEqual(calls[0][1].summary, { source: { text, purpose: 'selection' }, requested_model: 'haiku' })
    assert.equal(calls[0][1].identity, null)
    settings.summaryModel = 'luna'
    publish({ revision: 2, playback: { readingId: 'reading', operationId: 'selection-operation', source: 'summary', status: 'loading', identity: null } })
    await flush(); assert.equal(button.props['aria-label'], 'Cancel the request')
    app.unmount()
    assert.equal(summaries.temporary, null); assert.equal(calls[0][1].summary.source.text, text)
    assert.equal(calls[0][1].summary.requested_model, 'haiku')
    assert.equal(calls.some(call => call[0] === 'skip' || call[0] === 'stopAll'), false)
    complete({ ok: true, ended: Promise.resolve({ ok: true }) }); await click
    assert.deepEqual(errors, [])
})

test('descriptorless selection replacement keeps only its current provisional Stop identity', async () => {
    const { tts, client, calls } = await fixture()
    let sequence = 0
    const completions = []
    client.manual = request => {
        calls.push(['manual', request])
        return Object.assign(new Promise(resolve => completions.push(resolve)), { operationId: `operation-${++sequence}` })
    }
    const source = { source: { text: 'Captured', purpose: 'selection' }, requested_model: 'auto' }
    const first = tts.playSummary(() => assert.fail('no Web reference'), { identity: { selectionId: 'first', variant: 'summary' }, nativeRequest: source })
    const second = tts.playSummary(() => assert.fail('no Web reference'), { identity: { selectionId: 'second', variant: 'summary' }, nativeRequest: source })
    assert.equal(tts.statusForSummary({ selectionId: 'first', variant: 'summary' }), 'idle')
    assert.equal(tts.statusForSummary({ selectionId: 'second', variant: 'summary' }), 'loading')
    tts.stop()
    assert.deepEqual(calls.at(-1), ['skip', undefined, 'operation-2'])
    completions.forEach(resolve => resolve({ ok: false, aborted: true }))
    assert.deepEqual(await first, { ok: false, aborted: true }); assert.deepEqual(await second, { ok: false, aborted: true })
})
