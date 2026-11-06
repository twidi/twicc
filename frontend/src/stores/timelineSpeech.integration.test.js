import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import { ALL_PROJECTS_ID } from '../constants.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'
import { setPcmStreamPlayerFactory } from '../utils/pcmStreamPlayer.js'
import { useSummariesStore } from './summaries.js'
import { useTimelineSpeechStore } from './timelineSpeech.js'
import { useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'

const CLICK = Date.parse('2026-11-04T10:30:00.000Z')
const OPENROUTER = {
    kind: 'openrouter', model: 'openai/gpt-4o-mini-tts', voice: 'alloy',
    response_format: 'mp3', speed: 1.2, instructions: 'Read clearly.',
}
const SCRIPT = {
    kind: 'script', command: 'say -o -', output: 'stream', audio_format: 'mp3',
    pcm_rate: null, pcm_channels: 1, max_chars: 240,
}
const sessions = {
    s: { title: 'First session', project_id: 'p', provider: 'codex', hidden: false, archived: false },
}
const at = second => new Date(CLICK + second * 1000).toISOString().replace('Z', '000Z')
const message = (id, second = id, project = 'p', session = 's') => ({
    id, kind: 'assistant_message.sent', occurred_at: at(second), project_id: project,
    session_id: session, provider: 'codex', data: { text: `Original **message ${id}**.\nSaved text.` },
})
const page = (events = [], extra = {}) => ({
    events, sessions, has_more: false, latest_id: Math.max(0, ...events.map(event => event.id)), ...extra,
})
const json = body => new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
})
const opened = id => json({ audio_id: id, content_type: 'audio/mpeg' })
const deferred = () => {
    let resolve
    const promise = new Promise(yes => { resolve = yes })
    return { promise, resolve }
}
// Flush promise continuations through an event-loop checkpoint, without a timing delay.
const flush = () => new Promise(resolve => setImmediate(resolve))
async function until(predicate) {
    for (let turn = 0; turn < 50; turn++) {
        if (predicate()) return
        await flush()
    }
    assert.fail('The expected network or player transition did not occur')
}

function setup(t, fields = OPENROUTER) {
    _resetSpeechForTests()
    setActivePinia(createPinia())
    const plays = [], opens = [], releases = [], endings = [], requests = [], replies = [], speechReplies = []
    const player = {
        prime() {}, stop() {},
        playUrl(url, handlers) { plays.push({ url, handlers }) },
        playBlob() { assert.fail('Desktop MP3 must use the existing media player') },
    }
    setSpeechPlayerFactory(() => player)
    setPcmStreamPlayerFactory(() => ({ prime() {}, stop() {} }))
    const tts = useTextToSpeechStore()
    tts.applyConfig({
        config: {
            active_id: 'voice', adapt: 'off', test_text: 'Saved test sentence.',
            configs: [{ id: 'voice', name: 'Saved voice', ...fields, validated: { ...fields } }],
        },
        key_present: fields.kind === 'openrouter',
    })
    tts._openSpeech = (body, signal) => {
        opens.push({ body, signal })
        return Promise.resolve(speechReplies.length ? speechReplies.shift() : opened(`audio-${opens.length}`))
    }
    tts._fetchAudio = () => assert.fail('Desktop MP3 must not download buffered audio')
    tts._fetchEnding = async id => { endings.push(id); return json({ state: 'complete', error: null }) }
    tts._releaseAudio = async id => { releases.push(id) }
    const summaries = useSummariesStore()
    summaries._fetch = async (_url, options) => json({ sources: JSON.parse(options.body).sources.map(() => ({ eligible: false })) })
    const store = useTimelineSpeechStore(), target = new EventTarget()
    let now = CLICK
    store._now = () => now
    store._fetchPage = params => {
        requests.push(params)
        assert.ok(replies.length, 'Every timeline request needs an explicit network response')
        return Promise.resolve(replies.shift())
    }
    // Fixed scope/name facts. Scope resolution, feed interpretation, queueing and ownership remain real.
    const projects = { p: { name: 'Project' }, q: { name: 'Other project' } }
    store.initialize({
        dataStore: {
            projectsLoaded: true, projects,
            getProject: id => projects[id], getSession: () => undefined,
            getProjectScopeIds: id => [id], getProjectDisplayName: id => projects[id]?.name ?? id, getMainRepoProjectId: id => id,
        },
        workspacesStore: { getWorkspaceById: () => undefined, getAllProjectIds: () => [] },
        connectionStatus: ref('OPEN'), eventTarget: target,
    })
    t.after(async () => {
        store.dispose()
        tts.stop()
        await flush()
        store.$dispose()
        tts.$dispose()
        _resetSpeechForTests()
    })
    const emit = latestId => {
        const event = new Event('twicc:timeline-changed')
        event.detail = { latestId }
        target.dispatchEvent(event)
    }
    return {
        store, tts, summaries, plays, opens, releases, endings, requests, replies, speechReplies, emit,
        setNow(value) { now = value },
        async enable(events = [], pageId = 'p') {
            replies.push(page(events))
            store.enable(pageId)
            await flush()
        },
        async live(events, extra = {}) {
            replies.push(page(events, extra))
            emit(extra.latest_id ?? Math.max(...events.map(event => event.id)))
            await flush()
        },
        async start(index = plays.length - 1) { plays[index].handlers.onPlaying(); await flush() },
        async end(index = plays.length - 1) { plays[index].handlers.onEnded(); await flush() },
    }
}

// Losing bootstrap pagination or admitting the same event from live and before pages breaks this case.
test('slow activation preserves all click-time arrivals and live overlap', async t => {
    const h = setup(t), first = deferred()
    const arrivals = Array.from({ length: 55 }, (_, index) => message(index + 1))
    // Date order differs from ID order. Equal dates still use ascending IDs.
    arrivals[6] = message(7, 6)
    arrivals[7] = message(8, 9)
    arrivals[8] = message(9, 8)
    const overlap = message(901, 3), live = message(902, 56)
    h.replies.push(first.promise)
    h.store.enable('p')
    await until(() => h.requests.length === 1)
    h.emit(901)
    h.emit(902)
    await flush()
    assert.equal(h.opens.length, 0)
    // The first page's global ID is newer than eligible arrivals on older date pages.
    h.replies.push(page([live, overlap], { latest_id: 902 }),
        page([arrivals[4], arrivals[3], overlap, arrivals[2], arrivals[1], arrivals[0], message(0)], {
            has_more: true, latest_id: 902,
        }))
    first.resolve(page(arrivals.slice(5).reverse(), { has_more: true, latest_id: 900 }))
    await until(() => h.plays.length === 1)
    assert.deepEqual(h.requests.map(({ before, afterId }) => ({ before, afterId })), [
        { before: null, afterId: null },
        { before: null, afterId: 900 },
        { before: `${at(6)},6`, afterId: null },
    ])
    assert.equal(h.store.pendingCount, 56)
    const expectedIds = [1, 2, 3, 901, 4, 5, 6, 7, 9, 8,
        ...Array.from({ length: 46 }, (_, index) => index + 10), 902]
    for (const [index, id] of expectedIds.entries()) {
        await until(() => h.plays.length === index + 1)
        assert.equal(h.opens[index].body.text, message(id).data.text)
        await h.start(index)
        await h.end(index)
    }
    assert.equal(h.opens.length, 57)
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.tts.status, 'idle')
    assert.equal(new Set(h.opens.map(({ body }) => body.text)).size, 57)
    assert.equal(h.endings.length, 57)
    assert.equal(h.releases.length, 57)
    assert.ok(h.requests.every(request => request.query.include_muted === '1' && Object.keys(request.query).length === 1))
    assert.equal(h.replies.length, 0)
})

// Publishing idle between manual replacements lets automatic B steal the player from M or N.
test('manual replacement and explicit stop preserve queue ownership', async t => {
    const h = setup(t), a = message(1), b = message(2), c = message(3)
    await h.enable([b, a])
    await h.start()
    const automaticOwner = h.tts.playback.owner
    const staleA = h.plays[0].handlers
    assert.equal(typeof automaticOwner, 'symbol')
    assert.equal(h.store.pendingCount, 1)
    const manualM = h.tts.play('Manual M')
    await until(() => h.plays.length === 2)
    await h.start()
    const resultM = await manualM
    const staleM = h.plays[1].handlers
    const manualN = h.tts.play('Manual N', { origin: 'rendered' })
    await until(() => h.plays.length === 3)
    assert.deepEqual(await resultM.ended, { ok: false, aborted: true })
    await h.start()
    const resultN = await manualN
    staleA.onPlaying()
    staleA.onEnded()
    staleM.onEnded()
    await flush()
    assert.deepEqual(h.opens.map(({ body }) => body.text), [a.data.text, 'Manual M', 'Manual N'])
    assert.equal(h.tts.playback.owner, null)
    assert.equal(h.store.activePageId, 'p')
    await h.end()
    assert.deepEqual(await resultN.ended, { ok: true })
    await until(() => h.plays.length === 4)
    assert.equal(h.opens[3].body.text, b.data.text)
    assert.equal(h.tts.playback.owner, automaticOwner)
    await h.start()
    await h.live([c])
    assert.equal(h.store.pendingCount, 1)
    const staleB = h.plays[3].handlers
    h.tts.stop() // SidebarRail's public Stop reading action: skips B, keeps the queue.
    assert.equal(h.store.activePageId, 'p')
    staleB.onEnded()
    await until(() => h.opens.length === 5)
    assert.deepEqual(h.opens.map(({ body }) => body.text), [a.data.text, 'Manual M', 'Manual N', b.data.text, c.data.text])
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.tts.playback.owner, automaticOwner)
})

// Old feed/open completions must not start audio or populate a later activation.
test('disable and immediate reenable defeat stale audio and fetch completions', async t => {
    const h = setup(t), oldSpeech = deferred(), oldFetch = deferred(), newFetch = deferred()
    h.speechReplies.push(oldSpeech.promise)
    await h.enable([message(1)])
    assert.equal(h.tts.status, 'loading')
    const obsoleteSignal = h.opens[0].signal
    h.replies.push(oldFetch.promise)
    h.emit(2)
    await until(() => h.requests.length === 2)
    h.store.disable()
    h.setNow(CLICK + 10000)
    h.replies.push(newFetch.promise)
    h.store.enable('p')
    await until(() => h.requests.length === 3)
    assert.equal(obsoleteSignal.aborted, true)
    // This event even postdates the NEW click: only activation identity can reject its stale response.
    oldFetch.resolve(page([message(2, 12)]))
    oldSpeech.resolve(opened('obsolete-audio'))
    await until(() => h.releases.includes('obsolete-audio'))
    assert.equal(h.plays.length, 0)
    assert.equal(h.store.pendingCount, 0)
    newFetch.resolve(page([message(3, 11), message(4, 10), message(5, 9)]))
    await until(() => h.plays.length === 1)
    await h.start()
    const staleAutomatic = h.plays[0].handlers
    const manual = h.tts.play('Manual survives disable')
    await until(() => h.plays.length === 2)
    await h.start()
    const manualResult = await manual
    await h.live([message(6, 13)])
    assert.equal(h.store.pendingCount, 1)
    h.store.disable()
    h.setNow(CLICK + 20000)
    h.replies.push(page([message(7, 21), message(6, 13)]))
    h.store.enable('p')
    await until(() => h.store.pendingCount === 1)
    staleAutomatic.onPlaying()
    staleAutomatic.onEnded()
    await flush()
    assert.equal(h.tts.playback.text, 'Manual survives disable')
    assert.equal(h.tts.playback.owner, null)
    assert.equal(h.tts.status, 'playing')
    assert.deepEqual(h.releases, ['obsolete-audio', 'audio-2'])
    assert.equal(h.opens.length, 3)
    await h.end()
    assert.deepEqual(await manualResult.ended, { ok: true })
    await until(() => h.plays.length === 3)
    assert.equal(h.opens[3].body.text, message(7).data.text)
    await h.start()
    await h.end()
    assert.deepEqual(h.opens.map(({ body }) => body.text), [
        message(1).data.text, message(3).data.text, 'Manual survives disable', message(7).data.text,
    ])
    assert.deepEqual(h.releases, ['obsolete-audio', 'audio-2', 'audio-3', 'audio-4'])
    assert.equal(h.store.pendingCount, 0)
    assert.equal(h.replies.length, 0)
})

// Context is audible, not merely selected; names must stay outside the original message and voice fields.
test('announcements follow audible context through backend request payloads', async t => {
    for (const fields of [OPENROUTER, SCRIPT]) {
        await t.test(fields.kind, async t => {
            const h = setup(t, fields)
            const savedConfig = JSON.parse(JSON.stringify(h.tts._configPayload()))
            await h.enable([], ALL_PROJECTS_ID)
            const a = message(1), same = message(2), other = message(3, 3, 'p', 't')
            const resumed = message(4, 4, 'p', 't'), moved = message(5, 5, 'q', 't')
            await h.live([moved, resumed, other, same, a], { sessions: {
                ...sessions,
                t: { title: 'Second session', project_id: 'p', provider: 'codex', hidden: false, archived: false },
            } })
            assert.equal(h.tts.playback.text, a.data.text)
            assert.equal(h.tts.statusForText(a.data.text), 'loading')
            await h.start()
            await h.end()
            await h.start()
            await h.end()
            await h.start()
            const manual = h.tts.play('Selected original text', { origin: 'rendered' })
            await until(() => h.plays.length === 4)
            await h.start()
            const manualResult = await manual
            await h.end()
            assert.deepEqual(await manualResult.ended, { ok: true })
            await h.start()
            await h.end()
            await h.start()
            await h.end()
            const base = { ...fields, cache: true, ...(fields.kind === 'script' ? { script_id: 'voice' } : {}) }
            assert.deepEqual(h.opens.map(({ body }) => body), [
                { text: a.data.text, ...base, announcement: 'Project. First session.' },
                { text: same.data.text, ...base },
                { text: other.data.text, ...base, announcement: 'Second session.' },
                { text: 'Selected original text', ...base, origin: 'rendered' },
                { text: resumed.data.text, ...base, announcement: 'Project. Second session.' },
                { text: moved.data.text, ...base, announcement: 'Other project.' },
            ])
            assert.deepEqual(h.tts._configPayload(), savedConfig)
            assert.equal(h.tts.status, 'idle')
            assert.equal(h.store.pendingCount, 0)
            assert.equal(h.releases.length, 6)
        })
    }
})


// Real feed, summary subscriptions, player ownership and request bodies share one activation.
test('streamed summary speech failure reports once and continues without replaying the source', async t => {
    const h = setup(t), wires = new Map(), deletes = [], errors = []
    const descriptor = id => ({ source_key: `native-${id}`, source_hash: `hash-${id}`, policy_key: 'auto',
        session_id: 's', eligible: true, saved: null })
    h.store._showError = error => errors.push(error)
    h.summaries._fetch = async (url, options = {}) => {
        if (options.method === 'DELETE') { deletes.push(url); return new Response(null, { status: 204 }) }
        const body = options.body && JSON.parse(options.body)
        if (url.endsWith('resolve/')) return json({ sources: body.sources.map(source => descriptor(source.event_id)) })
        if (url === '/api/summaries/') return json({ id: `summary-${body.source.event_id}`,
            descriptor: descriptor(body.source.event_id), state: 'running' })
        return new Response(new ReadableStream({ start(controller) { wires.set(url, controller) } }))
    }
    await h.enable([message(1), message(2)])
    await until(() => h.plays.length === 1 && wires.size === 2)
    assert.equal(h.opens[0].body.summary_id, 'summary-1')
    assert.equal(h.opens[0].body.text, undefined)
    assert.equal(h.opens[0].body.announcement, 'Project. First session.')
    await h.start()
    wires.get('/api/summaries/summary-1/').enqueue(new TextEncoder().encode(JSON.stringify({
        state: 'failed', revision: 1, text: 'Usable first sentence.', error: 'The summary stopped before the end', saved_id: null,
    }) + '\n'))
    wires.get('/api/summaries/summary-1/').close()
    h.tts._fetchEnding = async id => json(id === 'audio-1'
        ? { state: 'failed', error: 'The summary stopped before the end' } : { state: 'complete' })
    await h.end()
    await until(() => h.plays.length === 2)
    assert.deepEqual(h.opens.map(({ body }) => body.summary_id), ['summary-1', 'summary-2'])
    assert.equal(h.opens[1].body.announcement, 'Project. First session.')
    assert.ok(h.opens.every(({ body }) => !('text' in body)))
    assert.deepEqual(errors, ['The summary stopped before the end'])
    assert.equal(h.store.activePageId, 'p')
    await until(() => deletes.includes('/api/summaries/summary-1/'))
    assert.equal(Object.values(h.summaries.entries).find(entry => entry.descriptor.source_key === 'native-1').saved_id, null)
})
