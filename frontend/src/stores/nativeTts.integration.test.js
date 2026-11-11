import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import { useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'
import { useTimelineSpeechStore } from './timelineSpeech.js'
import { useTimelineFiltersStore } from './timelineFilters.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'
import { setPcmStreamPlayerFactory } from '../utils/pcmStreamPlayer.js'
import { createNativeTtsClient } from '../utils/nativeTtsClient.js'
import * as uiMessages from '../i18n/uiMessages.js'
import { configureTelemetry, setTelemetryEpoch } from '../utils/telemetryClient.js'
beforeEach(() => { setActivePinia(createPinia()); _resetSpeechForTests(); setSpeechPlayerFactory(() => assert.fail('native cannot create HTMLAudio')); setPcmStreamPlayerFactory(() => assert.fail('native cannot create AudioContext')) })
test('displayed native errors are consumed before reload, including after a successful new reading', async () => {
    const audioError = 'Audio playback was interrupted.'
    const stopped = 'Speech stopped. Start playback or turn on timeline reading again.'
    let current = { version: 1, revision: 1, instanceId: 'instance', sessionStatus: 'error', playback: { status: 'idle' },
        listening: null, pendingCount: 0, error: { code: 'session_lost', message: stopped },
        results: [{ operationId: 'automatic:old:1', automatic: true, readingId: 'old', generation: 1,
            start: { ok: true }, ended: { ok: false, error: audioError } }] }
    const calls = [], errors = [], options = { dataStore: {}, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() }
    let finishToast
    async function loadPage() {
        setActivePinia(createPinia())
        const client = createNativeTtsClient({
            appClient: { async request(command, args) {
                calls.push(command)
                if (command === 'getState') return { capabilities: { nativeTts: 1 } }
                if (command === 'tts.acknowledge') {
                    assert.equal(args.revision, current.revision)
                    current = { ...current, revision: current.revision + 1, results: [], error: null }
                }
                return { accepted: true, snapshot: current }
            } },
            document: { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} },
            fetchImpl: async () => ({ ok: true, json: async () => ({ protocol: 1, available: true }) }),
            timers: { setTimeout() { return 1 }, clearTimeout() {} },
        })
        const tts = useTextToSpeechStore()
        await tts.attachNative(client)
        const timeline = useTimelineSpeechStore()
        timeline._showError = async message => {
            errors.push(message)
            if (finishToast === true) await new Promise(resolve => { finishToast = resolve })
        }
        timeline.initialize(options)
        return () => { timeline.dispose(); tts.detachNative() }
    }
    finishToast = true
    const close = await loadPage()
    assert.equal(calls.includes('tts.acknowledge'), false, 'native must retain an error until its toast is displayed')
    finishToast(); finishToast = null
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [audioError, stopped])
    assert.equal(calls.includes('tts.acknowledge'), true)
    assert.equal(current.error, null)
    assert.deepEqual(current.results, [])
    close()
    const closeReload = await loadPage()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [audioError, stopped], 'reload cannot show a consumed error')
    closeReload()
    current = { ...current, revision: current.revision + 1, sessionStatus: 'stopped',
        results: [{ operationId: 'new-success', start: { ok: true }, ended: { ok: true } }] }
    const closeSuccess = await loadPage()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [audioError, stopped])
    assert.deepEqual(current.results, [], 'successful terminal results are consumed too')
    assert.equal(calls.includes('tts.manual'), false, 'attaching and consuming errors never starts audio')
    closeSuccess()
    current = { ...current, revision: current.revision + 1, error: { code: 'session_lost', message: stopped } }
    const closeNewError = await loadPage()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [audioError, stopped, stopped], 'a new failure can use the same message as a consumed failure')
    closeNewError()
})
async function fixture(snapshot = {}) {
    const calls = []; let observer
    const client = { attach: async () => true, state: () => ({ revision: 1, playback: { status: 'idle' }, listening: null, ...snapshot }), subscribe(fn) { observer = fn; return () => {} }, manual(request) { calls.push(['manual', request]); return Promise.resolve({ ok: true, ended: Promise.resolve({ ok: true }) }) }, listen(a) { calls.push(['listen', a]); return Promise.resolve({ accepted: true }) }, setMuted(v) { calls.push(['muted', v]) }, disable() { calls.push(['disable']) }, skip(id, operationId) { calls.push(['skip', id, operationId]) }, stopAll() { calls.push(['stopAll']) }, detach() { calls.push(['detach']) } }
    const tts = useTextToSpeechStore(); tts.applyConfig({ config: { active_id: 'voice', configs: [{ id: 'voice', model: 'model', voice: 'voice', response_format: 'mp3', validated: { model: 'model', voice: 'voice', response_format: 'mp3', speed: null, instructions: '' }, speed: null, instructions: '' }] }, key_present: true })
    await tts.attachNative(client); tts._openSpeech = () => assert.fail('native cannot open Web speech')
    return { tts, calls, client, publish(s, refresh = false) { observer(s, refresh) } }
}
test('a repeated failure received during acknowledgement produces one toast until its pending batch is consumed', async () => {
    const message = 'The active instance was closed. Start speech again to continue.'
    const failed = { revision: 2, playback: { status: 'idle' }, listening: null,
        results: [{ operationId: 'automatic:first:1', automatic: true, readingId: 'first', generation: 1,
            start: { ok: true }, ended: { ok: false, error: message } }],
        error: { code: 'unauthorized', message, reading_id: 'first', generation: 1 } }
    const { tts, client, publish } = await fixture(failed)
    const timeline = useTimelineSpeechStore(), errors = [], acknowledgements = []
    let complete
    client.acknowledge = revision => {
        acknowledgements.push(revision)
        if (revision === 2) return new Promise(resolve => { complete = () => {
            publish({ ...failed, revision: 4, results: [], error: { code: 'unauthorized', message } })
            resolve({ accepted: true })
        } })
        publish({ ...failed, revision: revision + 1, results: [], error: null })
        return Promise.resolve({ accepted: true })
    }
    timeline._showError = text => errors.push(text)
    timeline.initialize({ dataStore: {}, workspacesStore: {}, eventTarget: new EventTarget() })
    await new Promise(resolve => setImmediate(resolve))
    // A second close changes the error's ownership while its first toast is being acknowledged.
    publish({ ...failed, revision: 3, error: { code: 'unauthorized', message } })
    complete()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [message])
    assert.deepEqual(acknowledgements, [2, 4])
    assert.equal(tts.nativeSnapshot.error, null)
    // No permanent deduplication history: a later failure after consumption remains visible.
    publish({ ...failed, revision: 6, results: [], error: { code: 'unauthorized', message } })
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, [message, message])
    timeline.dispose(); tts.detachNative()
})
test('hidden native errors wait for visibility, while successful and cancelled readings stay silent', async () => {
    const failed = { revision: 1, playback: { status: 'idle' }, listening: null,
        results: ['failed', 'also-failed'].map(readingId => ({ operationId: `automatic:${readingId}:1`, automatic: true, readingId, generation: 1,
            start: { ok: false }, ended: { ok: false, error: 'Generation failed' } })),
        error: { reading_id: 'failed', generation: 1, message: 'Generation failed' } }
    const { tts, client, publish } = await fixture(failed)
    const document = new EventTarget(); document.visibilityState = 'hidden'
    const eventTarget = new EventTarget(); eventTarget.document = document
    const errors = [], consumed = []
    client.acknowledge = async revision => {
        consumed.push(revision)
        publish({ ...failed, revision: revision + 1, results: [], error: null })
    }
    const timeline = useTimelineSpeechStore()
    timeline._showError = message => errors.push(message)
    timeline.initialize({ dataStore: {}, workspacesStore: {}, eventTarget })
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, []); assert.deepEqual(consumed, [])
    document.visibilityState = 'visible'; document.dispatchEvent(new Event('visibilitychange'))
    assert.deepEqual(errors, [], 'visibility alone cannot present a potentially expired cached failure')
    publish(failed, true)
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, ['Generation failed'], 'matching pending failures produce one useful notice')
    assert.deepEqual(consumed, [1])
    publish({ ...failed, revision: 3, error: null, results: [
        { operationId: 'done', start: { ok: true }, ended: { ok: true } },
        { operationId: 'skip', automatic: true, readingId: 'skip', generation: 2, start: { ok: true }, ended: { ok: false, aborted: true } },
    ] })
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(consumed, [1, 3])
    assert.deepEqual(errors, ['Generation failed'])
    document.visibilityState = 'hidden'
    publish({ ...failed, revision: 5 })
    document.visibilityState = 'visible'; document.dispatchEvent(new Event('visibilitychange'))
    publish({ ...failed, revision: 6, error: null, results: [] }, true)
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(errors, ['Generation failed'], 'a fresh state discards an error that expires during sleep')
    timeline.dispose(); tts.detachNative()
})
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
test('paused native timeline stays enabled, permits manual speech, and can be disabled from the page', async () => {
    const listening = { activationId: 'paused', pageId: 'project-one', scope: { kind: 'project', id: 'project-one', label: 'One' }, includeMuted: false, paused: true }
    const { tts, calls, publish } = await fixture({ listening })
    const timeline = useTimelineSpeechStore()
    timeline.initialize({ dataStore: {}, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    assert.equal(timeline.activePageId, 'project-one')
    assert.equal(timeline.paused, true)
    await tts.play('Manual while paused')
    assert.equal(calls.at(-1)[0], 'manual')
    assert.equal(timeline.paused, true)
    publish({ revision: 2, playback: { status: 'idle' }, listening: { ...listening, paused: false, activationId: 'resumed' } })
    assert.equal(timeline.paused, false)
    publish({ revision: 3, playback: { status: 'idle' }, listening })
    timeline.disable()
    assert.deepEqual(calls.at(-1), ['disable'])
    publish({ revision: 4, playback: { status: 'idle' }, listening: null })
    assert.equal(timeline.activePageId, null)
    assert.equal(timeline.paused, false)
    timeline.dispose(); tts.detachNative()
})
test('native automatic activation sends real worktree scope and six-fraction UTC boundary without a Web coordinator', async () => {
    configureTelemetry({ send() {}, enabled: () => true })
    setTelemetryEpoch('a'.repeat(64))
    const { tts, calls } = await fixture()
    const timeline = useTimelineSpeechStore()
    timeline._now = () => Date.parse('2026-11-05T12:00:00Z')
    timeline.initialize({ dataStore: { projectsLoaded: true, getProject: id => ({ id, worktree_of: 'parent' }), getProjectScopeIds: id => [id], getProjectDisplayName: () => 'Worktree', getMainRepoProjectId: id => id }, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    await timeline.enable('worktree')
    assert.deepEqual(calls[0], ['listen', { pageId: 'worktree', scope: { kind: 'worktree', id: 'worktree', label: 'Worktree' }, includeMuted: false, boundary: '2026-11-05T12:00:00.000000Z', telemetryEpoch: 'a'.repeat(64) }])
    timeline.dispose(); tts.detachNative()
    configureTelemetry({})
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
    await new Promise(resolve => setImmediate(resolve))
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
        '../../../composables/useToast': { toast }, '../../../i18n/uiMessages.js': uiMessages, '../../ui/MarkdownContent.vue': { setup: () => () => Vue.h('markdown') },
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

test('real next-poll adapter reports retained automatic failure once and keeps manual errors with their controls', async () => {
    const automatic = { operationId: 'automatic:first:1', automatic: true, readingId: 'first', generation: 1,
        start: { ok: false, error: 'Automatic preparation failed' }, ended: { ok: false, error: 'Automatic preparation failed' } }
    let current = { version: 1, revision: 2, instanceId: 'instance', playback: { readingId: 'first', status: 'loading' },
        listening: { activationId: 'a', pageId: 'p', includeMuted: false }, error: null, results: [] }
    const scheduled = new Map(); let sequence = 0
    const client = createNativeTtsClient({
        appClient: { async request(command, args) {
            if (command === 'tts.acknowledge') {
                assert.equal(args.revision <= current.revision, true)
                current = { ...current, revision: current.revision + 1, results: [], error: null }
            }
            return command === 'getState' ? { capabilities: { nativeTts: 1 } } : { accepted: true, snapshot: current }
        } },
        document: { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} },
        fetchImpl: async () => ({ ok: true, json: async () => ({ protocol: 1, available: true }) }),
        timers: { setTimeout(fn, delay) { assert.equal(delay, 1000); scheduled.set(++sequence, fn); return sequence }, clearTimeout(id) { scheduled.delete(id) } },
    })
    async function poll() {
        const [id, fn] = scheduled.entries().next().value; scheduled.delete(id); await fn()
        await new Promise(resolve => setImmediate(resolve))
    }
    const { tts } = await fixture()
    await tts.attachNative(client)
    const timeline = useTimelineSpeechStore(), errors = []
    timeline._showError = message => errors.push(message)
    const options = { dataStore: {}, workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() }
    timeline.initialize(options)
    assert.deepEqual(errors, [])
    current = { ...current, revision: 3, playback: { readingId: 'second', source: 'message', text: 'Second', status: 'loading' }, results: [automatic] }
    await poll()
    assert.equal(tts.nativeSnapshot.playback.text, 'Second')
    assert.deepEqual(errors, ['Automatic preparation failed'], 'the next one-second poll sees the retained failure after global error clears')
    // The acknowledged failure is gone before the next reading starts.
    assert.deepEqual(current.results, [])
    current = { ...current, revision: current.revision + 1 }
    await poll()
    assert.deepEqual(errors, ['Automatic preparation failed'], 'raw global error cannot report the same terminal twice')
    const pending = client.manual({ kind: 'text', text: 'Manual' })
    const manual = { ...automatic, operationId: pending.operationId, automatic: false, readingId: 'manual', generation: 2,
        start: { ok: false, error: 'Manual preparation failed' }, ended: { ok: false, error: 'Manual preparation failed' } }
    current = { ...current, revision: current.revision + 1, results: [manual],
        error: { code: 'failed', message: 'Manual preparation failed', reading_id: 'manual', generation: 2 } }
    await poll()
    assert.deepEqual(await pending, { ok: false, error: 'Manual preparation failed' })
    assert.deepEqual(errors, ['Automatic preparation failed'], 'raw manual ownership is recovered from its terminal result')
    timeline.dispose(); timeline.initialize(options)
    assert.deepEqual(errors, ['Automatic preparation failed'], 'a new mirror cannot replay consumed failures')
    current = { ...current, revision: current.revision + 1, error: null }
    await poll()
    assert.equal(errors.length, 1)
    timeline.dispose(); tts.detachNative()
})


test('native listening waits for battery advice without an error and adopts the confirmed service state', async () => {
    const { tts, client, publish } = await fixture()
    const timeline = useTimelineSpeechStore(), errors = []
    timeline._showError = message => errors.push(message)
    timeline.initialize({ dataStore: { projectsLoaded: true, getProject: id => ({ id }),
        getProjectScopeIds: id => [id], getProjectDisplayName: () => 'Project', getMainRepoProjectId: id => id },
    workspacesStore: {}, connectionStatus: ref('OPEN'), eventTarget: new EventTarget() })
    client.listen = async () => ({ accepted: false, error: { code: 'confirmation_required', message: 'Choose how to continue.' } })
    await timeline.enable('p')
    assert.deepEqual(errors, [])
    assert.equal(timeline.activePageId, null, 'the dialog does not claim listening is active')
    publish({ revision: 2, playback: { status: 'idle' }, listening: {
        activationId: 'confirmed', pageId: 'p', includeMuted: false, scope: { kind: 'project', id: 'p', label: 'Project' },
    } })
    assert.equal(timeline.activePageId, 'p')
    client.listen = async () => ({ accepted: false, error: { code: 'notifications_blocked', message: 'Enable notifications' } })
    await timeline.enable('p')
    assert.deepEqual(errors, ['Enable notifications'])
    timeline.dispose(); tts.detachNative()
})
