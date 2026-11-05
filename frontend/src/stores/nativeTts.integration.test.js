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
    const client = { attach: async () => true, state: () => ({ revision: 1, playback: { status: 'idle' }, listening: null, ...snapshot }), subscribe(fn) { observer = fn; return () => {} }, manual(request) { calls.push(['manual', request]); return Promise.resolve({ ok: true, ended: Promise.resolve({ ok: true }) }) }, listen(a) { calls.push(['listen', a]); return Promise.resolve({ accepted: true }) }, setMuted(v) { calls.push(['muted', v]) }, disable() { calls.push(['disable']) }, skip(id) { calls.push(['skip', id]) }, stopAll() { calls.push(['stopAll']) }, detach() { calls.push(['detach']) } }
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
