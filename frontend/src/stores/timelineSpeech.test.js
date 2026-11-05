import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { reactive, ref, watch } from 'vue'
import { useSummariesStore } from './summaries.js'
import { useTimelineSpeechStore } from './timelineSpeech.js'
import { useTimelineFiltersStore } from './timelineFilters.js'
import { useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'
import { setPcmStreamPlayerFactory } from '../utils/pcmStreamPlayer.js'
import { REFUSED } from '../utils/speechOutcome.js'

const CLICK = Date.parse('2026-11-04T10:30:00.000000Z')
const fields = { model: 'tts', voice: 'voice', response_format: 'mp3', speed: null, instructions: '' }
const at = second => `2026-11-04T10:30:${String(second).padStart(2, '0')}.000000Z`
const message = (id, second = id, project = 'p', session = 's') => ({
    id, kind: 'assistant_message.sent', occurred_at: at(second), project_id: project,
    session_id: session, data: { text: `Message ${id}` },
})
const page = (events = [], extra = {}) => ({
    events, sessions: { s: { title: 'Session', project_id: 'p', hidden: false } },
    has_more: false, latest_id: Math.max(0, ...events.map(event => event.id)), ...extra,
})
const deferred = () => {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
}
class ListenerTarget extends EventTarget {
    listeners = new Set()
    addEventListener(type, listener, options) {
        this.listeners.add({ type, listener })
        super.addEventListener(type, listener, options)
    }
    removeEventListener(type, listener, options) {
        for (const entry of this.listeners) {
            if (entry.type === type && entry.listener === listener) this.listeners.delete(entry)
        }
        super.removeEventListener(type, listener, options)
    }
}
const json = body => new Response(JSON.stringify(body), { status: 200 })
async function settle() {
    for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve))
}
let cleanups = []
afterEach(async () => {
    for (const cleanup of cleanups.splice(0)) cleanup()
    await settle()
    _resetSpeechForTests()
})
function setup() {
    _resetSpeechForTests()
    setActivePinia(createPinia())
    let handlers, now = CLICK, primeCount = 0
    const player = {
        prime() { primeCount++ }, stop() {},
        playUrl(_url, callbacks) { handlers = callbacks },
        playBlob(_blob, callbacks) { handlers = callbacks },
    }
    setSpeechPlayerFactory(() => player)
    setPcmStreamPlayerFactory(() => ({ prime() {}, stop() {} }))
    const tts = useTextToSpeechStore()
    tts.applyConfig({ config: { active_id: 'voice', configs: [{ id: 'voice', ...fields, validated: fields }] }, key_present: true })
    const opens = [], requests = [], replies = [], errors = []
    tts._openSpeech = async body => { opens.push(body); return json({ audio_id: `audio-${opens.length}`, content_type: 'audio/mpeg' }) }
    tts._sendConfig = () => {}
    tts._fetchEnding = async () => json({ state: 'complete' })
    tts._releaseAudio = async () => {}
    const data = reactive({
        projectsLoaded: true, projects: { p: { name: 'Project' }, q: { name: 'Other' } }, sessions: {},
        getProject(id) { return this.projects[id] },
        getSession(id) { return this.sessions[id] },
        getProjectScopeIds(id) { return this.projects[id] ? [id] : [] },
        getProjectDisplayName(id) { return this.projects[id]?.name ?? id },
    })
    const workspaces = reactive({
        workspaces: { w: { name: 'Workspace', ids: ['p'] } },
        getWorkspaceById(id) { return this.workspaces[id] },
        getAllProjectIds(id) { return this.workspaces[id]?.ids ?? [] },
    })
    const status = ref('OPEN'), target = new ListenerTarget(), store = useTimelineSpeechStore()
    const filters = useTimelineFiltersStore()
    const settings = reactive({ timelineSummaryEnabled: true, summaryModel: 'auto' })
    const summaries = useSummariesStore(), summaryCalls = [], summaryStreams = new Map(), summaryDeletes = []
    let eligible = false
    const descriptor = id => ({ source_key: `event-${id}`, source_hash: `hash-${id}`, policy_key: settings.summaryModel,
        session_id: 's', eligible, saved: null })
    const acquisitions = new Map()
    summaries._fetch = async (url, options = {}) => {
        const body = options.body && JSON.parse(options.body)
        if (options.method === 'DELETE') { summaryDeletes.push(url); return new Response(null, { status: 204 }) }
        if (url.endsWith('resolve/')) return json({ sources: body.sources.map(source => descriptor(source.event_id)) })
        if (url === '/api/summaries/') {
            const id = body.source.event_id
            summaryCalls.push(id)
            if (acquisitions.has(id)) await acquisitions.get(id).promise
            return json({ id: `summary-${id}`, descriptor: descriptor(id), state: 'running' })
        }
        const id = Number(url.match(/summary-(\d+)/)[1])
        return new Response(new ReadableStream({ start(controller) { summaryStreams.set(id, controller) } }))
    }
    store._now = () => now
    store._fetchPage = params => {
        requests.push(params)
        const reply = replies.shift() ?? page()
        return typeof reply === 'function' ? reply(params) : Promise.resolve(reply)
    }
    store._showError = error => errors.push(error)
    store.initialize({ dataStore: data, workspacesStore: workspaces, connectionStatus: status, eventTarget: target, settingsStore: settings })
    const emit = (name, latestId) => {
        const event = new Event(name)
        event.detail = { latestId }
        target.dispatchEvent(event)
    }
    cleanups.push(() => { store.dispose(); tts.stop(); store.$dispose(); tts.$dispose() })
    return {
        store, filters, tts, data, workspaces, status, target, opens, requests, replies, errors, emit,
        settings, summaries, summaryCalls, summaryDeletes, acquisitions,
        summariesEnabled() { eligible = true },
        async summary(id, state = 'complete', error = null) {
            summaryStreams.get(id).enqueue(new TextEncoder().encode(JSON.stringify({ revision: 1, state,
                text: `Summary ${id}`, error, saved_id: state === 'complete' ? id : null }) + '\n'))
            if (state !== 'running') summaryStreams.get(id).close()
            await settle()
        },
        now(value) { now = value }, get primes() { return primeCount },
        async enable(events = [], pageId = 'p', extra = {}) { replies.push(page(events, extra)); store.enable(pageId); await settle() },
        async live(events, extra = {}) { replies.push(page(events, extra)); emit('twicc:timeline-changed', Math.max(1, ...events.map(event => event.id), extra.latest_id ?? 0)); await settle() },
        async start() { handlers.onPlaying(); await settle() },
        async end() { handlers.onEnded(); await settle() },
        async refuse() { handlers.onError('refused'); await settle() },
    }
}

test('activation spans multiple pages without losing arrivals', async () => {
    const h = setup(), held = deferred()
    h.replies.push(page([message(6), message(5)], { has_more: true }), () => held.promise)
    h.store.enable('p')
    assert.equal(h.primes, 1)
    await settle()
    assert.equal(h.opens.length, 0)
    h.emit('twicc:timeline-changed', 8)
    h.replies.push(page([message(8)]), page([message(2), message(1)], { has_more: true }),
        page([message(0), { ...message(-1), occurred_at: '2026-11-04T10:29:59.999999Z' }], { has_more: true }))
    held.resolve(page([message(4), message(3)], { has_more: true }))
    await settle()
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 6)
    assert.equal(h.requests.length, 5)
    await h.live([message(9)])
    assert.equal(h.store.pendingCount, 7)
})

test('one second late is accepted but more than five seconds late is rejected', async () => {
    const h = setup()
    await h.enable([message(40)])
    await h.start()
    await h.live([message(41, 39), message(42, 35), message(43, 34)])
    assert.equal(h.store.pendingCount, 2)
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 42')
    await h.start()
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 41')
    await h.start()
    await h.live([message(44, 34)])
    assert.equal(h.store.pendingCount, 0)
})

test('tolerance is applied at admission not at dequeue', async () => {
    const h = setup()
    await h.enable([message(40)])
    await h.start()
    await h.live([message(41, 39)])
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    h.now(CLICK + 3_600_000)
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 41')
})

test('selected preparation is never reordered', async () => {
    const h = setup(), held = deferred()
    h.tts._openSpeech = body => { h.opens.push(body); return held.promise }
    await h.enable([message(40)])
    await h.live([message(41, 39)])
    assert.equal(h.opens.length, 1)
    assert.equal(h.opens[0].text, 'Message 40')
    assert.equal(h.store.pendingCount, 1)
    held.resolve(json({ audio_id: 'held', content_type: 'audio/mpeg' }))
    await settle()
    assert.equal(h.tts.status, 'loading')
})

test('display filters and navigation do not affect listening', async () => {
    const h = setup()
    await h.enable([], 'ws:w')
    assert.equal(h.store.activeLabel, 'Workspace')
    assert.deepEqual(h.requests[0], { projectIds: ['p'], query: { include_muted: '1' }, before: null, afterId: null })
    h.data.currentProjectId = 'q'
    h.data.filters = { session: 'other', kind: 'user_message.sent' }
    await h.live([message(1)])
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.activePageId, 'ws:w')
})

test('muted session messages are skipped by default using feed metadata', async () => {
    const h = setup()
    await h.enable([message(1)], 'p', { sessions: { s: { title: 'Muted', mute_on_user_turn: true } } })
    assert.equal(h.opens.length, 0)
    assert.equal(h.store.pendingCount, 0)
})

test('muted inclusion follows the listening timeline and does not replay skipped messages', async () => {
    const h = setup()
    h.filters.setIncludeMuted('other', true)
    await h.enable([message(1)], 'p', { sessions: { s: { title: 'Muted', mute_on_user_turn: true } } })
    assert.equal(h.opens.length, 0)
    h.filters.setIncludeMuted('p', true)
    await settle()
    assert.equal(h.opens.length, 0, 'enabling inclusion does not replay skipped messages')
    h.data.currentProjectId = 'other'
    await h.live([message(2)], { sessions: { s: { title: 'Muted', mute_on_user_turn: true } } })
    assert.equal(h.opens[0].text, 'Message 2')
})

test('disabling muted inclusion purges its queue while keeping manual audio and other messages', async () => {
    const h = setup()
    h.filters.setIncludeMuted('p', true)
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    await h.enable([message(1), message(2, 2, 'p', 'other')], 'p', {
        sessions: { s: { title: 'Muted', mute_on_user_turn: true }, other: { title: 'Other', mute_on_user_turn: false } },
    })
    assert.equal(h.store.pendingCount, 2)
    h.filters.setIncludeMuted('p', false)
    assert.equal(h.store.pendingCount, 1)
    assert.equal(h.tts.status, 'playing')
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 2')
})

test('muting a session removes queued messages without interrupting the current reading', async () => {
    const h = setup()
    await h.enable([message(1), message(2)])
    await h.start()
    h.data.sessions.s = { hidden: false, mute_on_user_turn: true }
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.tts.status, 'playing')
    await h.end()
    assert.equal(h.opens.length, 1)
    assert.equal(h.store.activePageId, 'p')
})

test('live mute metadata purges queued speech for sessions absent from the data store', async () => {
    const h = setup()
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    await h.enable([message(1)])
    await h.live([{ ...message(2), kind: 'muted.started', data: {} }], {
        sessions: { s: { title: 'Session', mute_on_user_turn: true } },
    })
    assert.equal(h.store.pendingCount, 0)
    await h.end()
    assert.equal(h.opens.length, 1)
})

test('muted inclusion does not block subsequent messages after unmute', async () => {
    const h = setup()
    await h.enable([message(1)], 'p', { sessions: { s: { title: 'Muted', mute_on_user_turn: true } } })
    await h.live([message(2)], { sessions: { s: { title: 'Session', mute_on_user_turn: false } } })
    assert.equal(h.opens.length, 1)
    assert.equal(h.opens[0].text, 'Message 2')
})

test('manual playback preempts only the current automatic message', async () => {
    const h = setup()
    await h.enable([message(3), message(2), message(1)])
    await h.start()
    const manual = h.tts.play('Manual')
    await settle()
    await h.start()
    await manual
    assert.equal(h.store.pendingCount, 2)
    assert.equal(h.opens.length, 2)
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 2')
    await h.start()
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 3')
})

test('context follows project and session identity', async () => {
    const h = setup()
    const events = [message(1), message(2), message(3, 3, 'p', 't'), message(4, 4, 'q', 't'), message(5, 5, 'p', 'u')]
    const sessions = { s: { title: 'Session' }, t: { title: 'Second' }, u: { title: 'Third' } }
    await h.enable(events, '__all__', { sessions })
    for (let i = 0; i < 4; i++) { await h.start(); await h.end() }
    assert.deepEqual(h.opens.map(body => body.announcement ?? ''), ['Project. Session.', '', 'Second.', 'Other.', 'Project. Third.'])
})

test('manual audio and failed automatic speech reset context', async () => {
    const h = setup()
    await h.enable([message(1)])
    await h.start(); await h.end()
    h.tts._openSpeech = async () => new Response('failed', { status: 500 })
    await h.tts.play('Failed manual')
    h.tts._openSpeech = async body => { h.opens.push(body); return json({ audio_id: 'next', content_type: 'audio/mpeg' }) }
    await h.live([message(2)])
    assert.equal(h.opens.at(-1).announcement ?? '', '')
    await h.start()
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual; await h.end()
    await h.live([message(3)])
    assert.equal(h.opens.at(-1).announcement, 'Project. Session.')
    await h.start(); await h.end()
    h.tts._openSpeech = async () => new Response('failed', { status: 500 })
    await h.live([message(4)])
    assert.equal(h.errors.length, 1)
    h.tts._openSpeech = async body => { h.opens.push(body); return json({ audio_id: 'next', content_type: 'audio/mpeg' }) }
    await h.live([message(5)])
    assert.equal(h.opens.at(-1).announcement, 'Project. Session.')
})

test('scope replacement and disable invalidate old continuations', async () => {
    const h = setup(), held = deferred()
    h.replies.push(() => held.promise)
    h.store.enable('p'); await settle()
    await h.enable([message(2, 2, 'q')], 'q')
    held.resolve(page([message(1)])); await settle()
    assert.deepEqual(h.opens.map(body => body.text), ['Message 2'])
    const manual = h.tts.play('Manual'); await settle(); await h.start(); await manual
    h.store.disable()
    assert.equal(h.tts.status, 'playing')
    assert.equal(h.store.activePageId, null)
    assert.equal(h.store.pendingCount, 0)
    await h.end()
    assert.equal(h.opens.length, 2)
})

test('visibility backfill and joining projects do not replay history', async () => {
    const h = setup()
    await h.enable([], 'ws:w')
    h.now(CLICK + 20_000)
    h.replies.push(page([message(1, 1), message(2, 2, 'q'), message(3, 21, 'q', 'q-session')], { latest_id: 5 }))
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.requests.at(-1).afterId, 0)
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 1)
    h.replies.push(page([], { latest_id: 7, shown_sessions: ['t'] }), page([message(6, 22, 'p', 't')]))
    h.emit('twicc:timeline-changed', 7); await settle()
    assert.equal(h.store.pendingCount, 1)
    h.data.sessions.s = { hidden: true }; await settle()
    await h.live([message(8, 23)], { hidden_sessions: ['s'] })
    assert.equal(h.store.pendingCount, 1)
})

test('membership changes during bootstrap retain the click boundary', async () => {
    const h = setup(), held = deferred()
    h.replies.push(page([message(6, 6)], { has_more: true }), () => held.promise)
    h.store.enable('ws:w'); await settle()
    h.now(CLICK + 20_000)
    h.replies.push(page([message(6, 6), message(5, 5)], { has_more: true }), page([message(1), message(2, 2, 'q'), message(7, 21, 'q'), message(0)]))
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.requests[2].afterId, null)
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 3)
    held.resolve(page([message(4)])); await settle()
    assert.equal(h.store.pendingCount, 3)
})

test('shown-session barrier filters ordinary catch-up and bootstrap pages', async () => {
    const h = setup()
    const shown = { ...message(5, 5), kind: 'hidden.ended', data: {} }
    await h.enable([message(6), shown, message(4)])
    assert.equal(h.opens[0].text, 'Message 6')
    await h.live([message(7), message(3)], { latest_id: 8 })
    assert.equal(h.store.pendingCount, 1)
    h.store.disable()
    await h.enable([])
    await h.live([message(12, 12), { ...shown, id: 11 }, message(10, 10)], { latest_id: 12 })
    assert.equal(h.opens.at(-1).text, 'Message 12')
    assert.equal(h.store.pendingCount, 0)
})

test('first live message uses newly received session metadata', async () => {
    const h = setup()
    await h.enable([])
    await h.live([message(1, 1, 'p', 'new-session')], { sessions: { 'new-session': { title: 'Received title', hidden: false } } })
    assert.equal(h.opens[0].announcement, 'Project. Received title.')
})

test('scope replacement retains session titles for pending messages', async () => {
    const h = setup()
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    await h.enable([message(1)], 'ws:w')
    h.replies.push(page([], { sessions: {}, latest_id: 1 }))
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.requests.at(-1).afterId, 1)
    assert.equal(h.store.pendingCount, 1)
    assert.equal(h.opens.length, 1)
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 1')
    assert.equal(h.opens.at(-1).announcement, 'Project. Session.')
})

test('scope replacement uses the latest session title for pending messages', async () => {
    const h = setup()
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    await h.enable([message(1)], 'ws:w')
    h.replies.push(page([], { sessions: { s: { title: 'Updated title', project_id: 'p', hidden: false } }, latest_id: 1 }))
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.store.pendingCount, 1)
    assert.equal(h.opens.length, 1)
    await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 1')
    assert.equal(h.opens.at(-1).announcement, 'Project. Updated title.')
})

test('disconnect retains pending readings until successful catch-up', async () => {
    const h = setup(), held = deferred()
    await h.enable([message(2), message(1)])
    await h.start()
    h.status.value = 'CLOSED'; await h.end()
    assert.equal(h.opens.length, 1)
    h.replies.push(() => held.promise)
    h.status.value = 'OPEN'; h.emit('twicc:ws-reconnected'); await settle()
    assert.equal(h.opens.length, 1)
    held.reject(new Error('offline')); await settle()
    assert.equal(h.opens.length, 1)
    await h.live([message(3)])
    assert.equal(h.opens.at(-1).text, 'Message 2')
    assert.equal(h.store.pendingCount, 1)
})

test('bootstrap load failure retries on next invalidation', async () => {
    const h = setup()
    h.replies.push(() => Promise.reject(new Error('first failed')))
    h.store.enable('p'); await settle()
    h.replies.push(page([message(2)], { has_more: true }), () => Promise.reject(new Error('before failed')))
    h.emit('twicc:timeline-changed', 2); await settle()
    assert.equal(h.opens.length, 0)
    h.replies.push(page([message(1), message(0)]), page([], { latest_id: 3 }))
    h.emit('twicc:timeline-changed', 3); await settle()
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 1)
})

test('scope invalidation availability loss and truncation disable listening', async () => {
    const h = setup()
    await h.enable([message(2), message(1)], 'ws:w')
    h.workspaces.workspaces.w.ids = []; await settle()
    assert.equal(h.store.activePageId, null)
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.requests.some(params => params.projectIds.length === 0), false)
    await h.enable([message(3)])
    h.tts.deactivateConfig(); await settle()
    assert.equal(h.store.activePageId, null)
    h.tts.activateConfig('voice')
    await h.enable([message(4)])
    const count = h.requests.length
    await h.live([], { truncated: true, latest_id: 50 })
    assert.equal(h.store.activePageId, null)
    assert.equal(h.requests.length, count + 1)
    assert.equal(h.errors.length, 1)
    assert.match(h.errors[0], /catch-up/i)
})

test('stopping the current reading keeps listening and reads the next message', async () => {
    const h = setup()
    await h.enable([message(2), message(1)])
    await h.start()
    assert.equal(h.opens.at(-1).text, 'Message 1')
    h.tts.stop(); await settle()
    assert.equal(h.store.activePageId, 'p')
    assert.equal(h.opens.at(-1).text, 'Message 2')
    assert.equal(h.errors.length, 0)
})

test('stopping a manual reading keeps listening', async () => {
    const h = setup()
    await h.enable([message(4), message(3)])
    const manual = h.tts.play('Manual'); await settle(); await h.start(); await manual
    h.tts.stop(); await settle()
    assert.equal(h.store.activePageId, 'p')
    assert.equal(h.opens.at(-1).text, 'Message 4')
})

test('disabling or enabling another page stops the current reading and the listening', async () => {
    const h = setup()
    await h.enable([message(2), message(1)])
    await h.start()
    h.store.disable(); await settle()
    assert.equal(h.store.activePageId, null)
    assert.equal(h.tts.status, 'idle')
    assert.equal(h.opens.length, 1)
})

test('reload pagehide logout and disposal cannot restart speech', async () => {
    for (const operation of ['pagehide', 'disable', 'dispose']) {
        const h = setup(), held = deferred()
        h.replies.push(() => held.promise)
        h.store.enable('p'); await settle()
        if (operation === 'pagehide') h.target.dispatchEvent(new Event('pagehide'))
        else h.store[operation]()
        held.resolve(page([message(1)])); await settle()
        h.emit('twicc:timeline-changed', 20); h.emit('twicc:ws-reconnected'); await settle()
        assert.equal(h.store.activePageId, null)
        assert.equal(h.opens.length, 0)
        assert.equal(h.requests.length, 1)
    }
})

test('browser playback refusal disables instead of draining the queue', async () => {
    const h = setup()
    h.tts._openSpeech = async () => new Response(JSON.stringify({ error: REFUSED }), { status: 500 })
    await h.enable([message(2), message(1)])
    assert.equal(h.store.activePageId, null)
    assert.equal(h.errors.length, 1)
})


test('a live request begun before reconnection cannot reopen the drain gate', async () => {
    const h = setup(), oldLive = deferred(), catchUp = deferred()
    await h.enable([message(2), message(1)])
    await h.start()
    h.replies.push(() => oldLive.promise)
    h.emit('twicc:timeline-changed', 3); await settle()
    h.status.value = 'CLOSED'; await h.end()
    h.replies.push(() => catchUp.promise)
    h.status.value = 'OPEN'; await settle()
    oldLive.resolve(page([message(3)])); await settle()
    assert.equal(h.opens.length, 1)
    catchUp.resolve(page([], { latest_id: 3 })); await settle()
    assert.equal(h.opens.at(-1).text, 'Message 2')
})

test('owned admission preserves a message when manual playback wins the final busy guard', async () => {
    const h = setup()
    let intercepted = false
    const unsubscribe = h.tts.$onAction(({ name, args }) => {
        if (name !== 'play' || !args[1]?.owner || intercepted) return
        intercepted = true
        void h.tts.play('Manual wins')
    })
    cleanups.push(unsubscribe)
    await h.enable([message(1)])
    assert.equal(h.opens.length, 1)
    assert.equal(h.opens[0].text, 'Manual wins')
    assert.equal(h.store.pendingCount, 1)
    await h.start(); await h.end()
    assert.equal(h.opens.at(-1).text, 'Message 1')
})

test('automatic ending holds selection while the player briefly reports idle', async () => {
    const h = setup(), ending = deferred()
    h.tts._fetchEnding = () => ending.promise
    await h.enable([message(2), message(1)])
    await h.start(); await h.end()
    assert.equal(h.tts.status, 'playing')
    assert.equal(h.opens.length, 1)
    const idleCounts = []
    cleanups.push(watch(() => h.tts.status, status => {
        if (status === 'idle') idleCounts.push(h.opens.length)
    }, { flush: 'sync' }))
    await h.live([message(3)])
    assert.equal(h.opens.length, 1)
    ending.resolve(json({ state: 'complete' })); await settle()
    assert.deepEqual(idleCounts, [1])
    assert.equal(h.opens.at(-1).text, 'Message 2')
})


test('initialization is idempotent and disposal removes all owned listeners', async () => {
    const h = setup()
    await h.enable([])
    assert.equal(h.target.listeners.size, 4)
    h.store.initialize({ dataStore: h.data, workspacesStore: h.workspaces, connectionStatus: h.status, eventTarget: h.target })
    assert.equal(h.target.listeners.size, 4)
    h.store.dispose()
    assert.equal(h.target.listeners.size, 0)
    h.status.value = 'CLOSED'; h.status.value = 'OPEN'
    h.store.enable('p'); await settle()
    assert.equal(h.store.activePageId, null)
    assert.equal(h.requests.length, 1)
})

test('completed empty bootstrap seeds replacement catch-up from zero', async () => {
    const h = setup()
    await h.enable([], 'ws:w', { latest_id: null })
    h.now(CLICK + 20_000)
    h.replies.push(params => Promise.resolve(params.afterId === 0
        ? page([message(3), message(2), message(1)])
        : page([message(3), message(2)], { has_more: true })))
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.requests[1].afterId, 0)
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 2)
    assert.equal(h.requests.length, 2)
})

test('unfinished bootstrap replacement retains the reconnect catch-up obligation', async () => {
    const h = setup(), original = deferred(), catchUp = deferred(), before = deferred()
    cleanups.push(() => { original.resolve(page()); catchUp.resolve(page()); before.resolve(page()) })
    h.replies.push(() => original.promise)
    h.store.enable('ws:w'); await settle()
    h.status.value = 'CLOSED'; h.status.value = 'OPEN'
    const followUp = params => params.afterId === null ? before.promise : catchUp.promise
    h.replies.push(page([message(3)], { has_more: true }), followUp, followUp)
    h.workspaces.workspaces.w.ids = ['p', 'q']; await settle()
    assert.equal(h.store.activePageId, 'ws:w')
    assert.equal(h.opens.length, 0)
    assert.equal(h.requests.some(params => params.afterId === 3), true)
    original.resolve(page([message(2)]))
    before.resolve(page([message(1), message(0)])); await settle()
    assert.equal(h.opens.length, 0)
    catchUp.resolve(page([message(4)])); await settle()
    assert.equal(h.opens[0].text, 'Message 1')
    assert.equal(h.store.pendingCount, 2)
})


test('two speculative summaries prepare during playback without audio or order changes', async () => {
    const h = setup(); h.summariesEnabled()
    await h.enable([message(1)])
    assert.equal(h.tts.status, 'loading')
    assert.equal(h.opens[0].summary_id, 'summary-1')
    await h.start()
    await h.live([message(4), message(3), message(2)])
    assert.deepEqual(h.summaryCalls, [1, 2, 3])
    assert.equal(h.opens.length, 1)
    await h.summary(3)
    assert.deepEqual(h.summaryCalls, [1, 2, 3, 4])
    await h.summary(2)
    await h.end()
    assert.equal(h.opens[1].summary_id, 'summary-2')
    assert.equal(h.opens[1].announcement, undefined)
    await h.start(); await h.end()
    assert.equal(h.opens[2].summary_id, 'summary-3')
})

test('delayed summary acquisition reserves selected audio and manual speech keeps priority', async () => {
    const h = setup(), held = deferred(); h.summariesEnabled(); h.acquisitions.set(1, held)
    await h.enable([message(1), message(2)])
    assert.equal(h.tts.status, 'loading')
    assert.equal(h.tts.playback.source, 'summary')
    assert.equal(h.opens.length, 0)
    const manual = h.tts.play('Manual')
    await settle(); await h.start(); await manual
    held.resolve(); await settle()
    assert.deepEqual(h.opens.map(body => body.text), ['Manual'])
    await h.end()
    assert.equal(h.opens[1].summary_id, 'summary-2')
})

test('switch off releases speculative owners and keeps selected summary through completion', async () => {
    const h = setup(); h.summariesEnabled()
    await h.enable([message(1), message(2)])
    const selected = h.tts.playback.identity
    h.settings.timelineSummaryEnabled = false
    await settle()
    assert.deepEqual(h.tts.playback.identity, selected)
    assert.ok(h.summaryDeletes.includes('/api/summaries/summary-2/'))
    assert.ok(!h.summaryDeletes.includes('/api/summaries/summary-1/'))
    await h.start(); await h.end()
    assert.equal(h.opens[1].text, 'Message 2')
})

test('switch on and policy invalidation refresh pending descriptors at selection', async () => {
    const h = setup(); h.summariesEnabled(); h.settings.timelineSummaryEnabled = false
    await h.enable([message(1), message(2)])
    await h.start()
    h.settings.timelineSummaryEnabled = true
    await settle()
    assert.deepEqual(h.summaryCalls, [2])
    h.settings.summaryModel = 'luna'
    h.summaries.invalidate({ type: 'synced_settings_updated', settings: { summaryModel: 'luna' } })
    await settle()
    assert.equal(h.summaries.descriptorFor({ event_id: 2 }).policy_key, 'luna')
    await h.end()
    assert.equal(h.opens[1].summary_id, 'summary-2')
})

test('failed summary skips and reports without disabling a working voice or reading original', async () => {
    const h = setup(); h.summariesEnabled()
    await h.enable([message(1), message(2), message(3)])
    await h.start(); await h.summary(2, 'failed', 'Summary failed')
    await h.end()
    assert.equal(h.opens[1].summary_id, 'summary-3')
    assert.deepEqual(h.errors, ['Summary failed'])
    assert.equal(h.store.activePageId, 'p')
    assert.equal(h.tts.available, true)
    assert.ok(h.opens.every(body => body.text === undefined))
})

test('included mute keeps preparation and hide releases only the timeline owner', async () => {
    const h = setup(); h.summariesEnabled(); h.filters.setIncludeMuted('p', true)
    await h.enable([message(1), message(2)])
    await h.start()
    const joined = h.summaries.request({ event_id: 2 })
    await joined.ready
    h.data.sessions.s = { hidden: false, mute_on_user_turn: true }
    await settle()
    assert.equal(h.store.pendingCount, 1)
    assert.equal(h.summaryDeletes.length, 0)
    h.data.sessions.s.hidden = true
    await settle()
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.summaryDeletes.length, 0)
    h.store.disable()
    await h.summary(2)
    assert.equal(joined.entry.state, 'complete')
    joined.release()
})

test('stop or switch invalidates delayed handles before they can start obsolete audio', async () => {
    for (const switchScope of [false, true]) {
        const h = setup(), held = deferred(); h.summariesEnabled(); h.acquisitions.set(1, held)
        await h.enable([message(1), message(2)])
        if (switchScope) await h.enable([], 'q')
        else h.store.disable()
        held.resolve(); await settle()
        assert.equal(h.opens.length, 0)
        assert.ok(h.summaryDeletes.includes('/api/summaries/summary-1/'))
    }
})


test('manual summary reserves before acquisition independently of automatic switch', async () => {
    const h = setup(), held = deferred(); h.settings.timelineSummaryEnabled = false
    await h.enable([message(1), message(2)])
    await h.start()
    const manual = h.tts.playSummary(held.promise, { identity: { selectionId: 1, variant: 'summary' } })
    await settle()
    assert.equal(h.tts.status, 'loading')
    assert.equal(h.tts.playback.owner, null)
    await h.live([message(3)])
    assert.equal(h.opens.length, 1)
    held.resolve({ id: 'manual-summary' }); await settle()
    assert.equal(h.opens[1].summary_id, 'manual-summary')
    await h.start(); await manual; await h.end()
    assert.equal(h.opens[2].text, 'Message 2')
})

test('preparation does not lock the next date or advance the audible watermark', async () => {
    const h = setup(); h.summariesEnabled()
    const manual = h.tts.play('Manual'); await settle(); await h.start(); await manual
    await h.enable([message(40), message(41)])
    assert.deepEqual(h.summaryCalls, [40, 41])
    await h.live([message(2), message(3)], { latest_id: 42 })
    assert.equal(h.store.pendingCount, 4, 'speculation cannot reject earlier arrivals through a watermark')
    await h.end()
    assert.equal(h.opens[1].summary_id, 'summary-2', 'selected acquisition bypasses both speculative slots')
    assert.equal(h.opens[1].announcement, 'Project. Session.')
})

test('summary open failure skips and actual browser refusal still disables listening', async () => {
    const h = setup(); h.summariesEnabled()
    h.tts._openSpeech = async body => {
        h.opens.push(body)
        return body.summary_id === 'summary-1' ? new Response(JSON.stringify({ error: 'Summary unavailable' }), { status: 424 })
            : json({ audio_id: 'good', content_type: 'audio/mpeg' })
    }
    await h.enable([message(1), message(2)])
    assert.equal(h.opens[1].summary_id, 'summary-2')
    assert.equal(h.store.activePageId, 'p')
    assert.deepEqual(h.errors, ['Summary unavailable'])
    h.tts._fetchEnding = async () => json({ state: 'complete' })
    await h.refuse()
    assert.equal(h.store.activePageId, null)
    assert.equal(h.errors.at(-1), REFUSED)
})


test('failed handle acquisition reports its summary error and continues without an original', async () => {
    const h = setup(), held = deferred(); h.summariesEnabled(); h.acquisitions.set(1, held)
    await h.enable([message(1), message(2)])
    held.reject(new Error('Summary producer unavailable'))
    await settle()
    assert.deepEqual(h.errors, ['Summary producer unavailable'])
    assert.equal(h.opens.length, 1)
    assert.equal(h.opens[0].summary_id, 'summary-2')
    assert.equal(h.store.activePageId, 'p')
})

test('scope removal releases otherwise unowned preparation and never stops selected speech', async () => {
    const h = setup(); h.summariesEnabled()
    await h.enable([message(1), message(2, 2, 'q')], 'ws:w', {})
    // Add q to the current workspace, then admit a fresh q message.
    h.workspaces.workspaces.w.ids.push('q')
    await settle()
    await h.live([message(3, 3, 'q')])
    assert.ok(h.summaryCalls.includes(3))
    h.workspaces.workspaces.w.ids = ['p']
    await settle()
    assert.ok(h.summaryDeletes.includes('/api/summaries/summary-3/'))
    assert.equal(h.tts.playback.identity.sourceKey, 'event-1')
})

test('shared native policy fixtures preserve Web boundary and deterministic ordering', async () => {
    const { readFileSync } = await import('node:fs')
    const cases = JSON.parse(readFileSync(new URL('../../../tests/fixtures/native_tts_timeline_cases.json', import.meta.url)))
    for (const fixture of cases) {
        const h = setup()
        const events = fixture.events.map(item => ({ ...message(item.id), occurred_at:
            new Date(CLICK + item.second * 1000).toISOString().replace('Z', '000Z') }))
        await h.enable(events)
        const played = []
        for (let i = 0; i < fixture.expected.length; i++) {
            played.push(Number(h.opens.at(-1).text.replace('Message ', '')))
            await h.start(); await h.end()
        }
        assert.deepEqual(played, fixture.expected, fixture.name)
        h.store.disable()
    }
})
