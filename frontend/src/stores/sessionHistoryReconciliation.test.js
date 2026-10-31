import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { toRaw } from 'vue'
import { hasContent, getParsedContent, clearParsedContent, setParsedContent } from '../utils/parsedContent.js'
import { DISPLAY_LEVEL, INITIAL_ITEMS_COUNT } from '../constants.js'
import { userMessageText, userMessageAttachmentCount } from '../providers/codex/canonical.js'
import { attachmentMatchKey, attachmentCountForMessage, matchableUserMessage } from '../utils/attachmentStrip.js'
import { sweepLoadedSessions } from '../composables/reconcileSweep.js'
import {
    applyUnavailableRefusal, captureRefusalTarget, getSessionUnavailableReason, noteUnavailableReasonUpdate,
} from '../utils/sessionAvailability.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const start = source.indexOf(`        ${name}(`)
    return start < 0 ? '' : source.slice(start, source.indexOf('        /**', start))
}
const matchStart = source.indexOf('function userMessageMatchKey(')
const matchEnd = source.indexOf('// Same key,', matchStart)
const itemHelpersStart = source.indexOf('/** A session item built from an items metadata row')
const itemHelpersEnd = source.indexOf('/**\n * Coalesce ascending', itemHelpersStart)
const optimisticStart = source.indexOf('function userMessageMatchesOptimistic(')
const optimisticEnd = source.indexOf('\n}', optimisticStart) + 2
const reconcileSource = readFileSync(new URL('../composables/useReconciliation.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export function useReconciliation', 'function useReconciliation')
const quiet = { group() {}, groupEnd() {}, log() {}, warn() {}, error() {} }
const payload = (text, timestamp) => ({ timestamp, type: 'event_msg', payload: { type: 'item_completed',
    item: { id: 'u', type: 'UserMessage', content: [{ type: 'text', text }] } } })
const transcript = [
    { line_num: 1, display_level: 0, kind: 'user_message', timestamp: '2026-10-08T06:23:50Z',
        group_head: null, group_tail: null, content: JSON.stringify(payload('Hello', '2026-10-08T06:23:50Z')) },
    { line_num: 2, display_level: 0, kind: 'assistant_message', timestamp: '2026-10-08T06:25:18Z',
        group_head: null, group_tail: null, content: JSON.stringify({ type: 'event_msg', payload: {
            type: 'item_completed', item: { id: 'a', type: 'AgentMessage', content: [{ type: 'Text', text: 'Answer' }] } } }) },
]
const metadata = transcript.map(({ content, ...item }) => item)
const copy = value => JSON.parse(JSON.stringify(value))
function fixture({ fetched = false, lastLine = 2, stallMs = 30000, logger = quiet } = {}) {
    const requests = []
    let respond = async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) })
    const deps = { hasContent, getParsedContent, clearParsedContent, setParsedContent, INITIAL_ITEMS_COUNT, DISPLAY_LEVEL, toRaw,
        isLaunchedEphemeral: s => !!s.ephemeral,
        itemsCoverageInFlight: new Function(`return ${source.match(/const itemsCoverageInFlight = (new \w+\(\))/)[1]}`)(),
        readJsonWithProgress: new Function(`return ${source.match(/async function readJsonWithProgress[\s\S]*?\n}\n/)[0]}`)(),
        describeFetchFailure: new Function(`return ${source.match(/function describeFetchFailure[\s\S]*?\n}\n/)[0]}`)(),
        sessionHistoryInFlight: new Map(), sessionHistoryControllers: new Map(), SESSION_HISTORY_STALL_TIMEOUT_MS: stallMs,
        lineNumsToRanges: lines => lines.map(line => [line, line]),
        apiFetch: async (url, options) => { requests.push(url); return respond(url, options) }, console: logger,
        getProviderHelpers: () => ({ extractUserMessageText: userMessageText, extractUserMessageAttachmentCount: userMessageAttachmentCount }),
        attachmentMatchKey, attachmentCountForMessage, matchableUserMessage,
        applyUnavailableRefusal, captureRefusalTarget, getSessionUnavailableReason,
    }
    const names = ['async loadSessionHistory', 'async ensureSessionItemsCoverage', 'async loadSessionItemsRanges',
        'async loadSessionMetadata', 'initSessionItemsFromMetadata', 'updateSessionItemsContent', 'clearOptimisticMessageIfMatched',
        'mergeSessionItemsMetadata']
    const actions = new Function(...Object.keys(deps), `${source.slice(matchStart, matchEnd)}\n${source.slice(optimisticStart, optimisticEnd)}\n${source.slice(itemHelpersStart, itemHelpersEnd)}
        return { ${names.map(action).join('')} }`)(...Object.values(deps))
    const useFixture = defineStore('session-history-reconciliation', {
        state: () => ({ sessions: { s: { id: 's', project_id: 'p', provider: 'codex', draft: false,
            last_line: lastLine, compute_version_up_to_date: true } }, sessionItems: { s: [] },
            localState: { sessions: { s: { itemsFetched: fetched } }, optimisticMessages: {} } }),
        actions: { ...actions, getSession(id) { return this.sessions[id] }, recomputeVisualItems() {},
            resolveInflightSends() {}, _retireStreamingBlocks() {}, auditInflightSends() {},
            loadAsyncQuestions: async () => {},
            addSessionItems(id, items) {
                for (const item of items) this.sessionItems[id][item.line_num - 1] = item
                this.clearOptimisticMessageIfMatched(id, items)
            },
            loadProjects: async () => new Set(), loadSessions: async () => new Set(),
            didSessionsFailToLoad: () => false, didSessionItemsFailToLoad() { return !!this.localState.sessions.s.itemsLoadingError },
            markNewTailItemsLive() {}, clearSyncBaseline() {}, refreshSessionRecord: async () => true,
            refreshAllLoadedToolStates: async () => {}, auditAllLoadedInflightSends() {},
        },
    })
    const store = useFixture(createPinia())
    const optimistic = { kind: 'user_message', _optimisticCreatedAtMs: Date.parse('2026-10-08T06:23:49Z') }
    setParsedContent(optimistic, payload('Hello'))
    store.localState.optimisticMessages.s = optimistic
    const reconciliation = new Function('useDataStore', 'sweepLoadedSessions', 'console',
        `${reconcileSource}; return useReconciliation()`)(() => store, sweepLoadedSessions, quiet)
    return { store, requests, reconciliation, respond(fn) { respond = fn } }
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

test('reconnect loads an unfetched focused history and replaces its optimistic message with dated items', async () => {
    const { store, reconciliation } = fixture()
    await reconciliation.onReconnected('p', 's', true)
    assert.equal(store.sessionItems.s.length, 2)
    assert.equal(store.sessionItems.s[0].timestamp, '2026-10-08T06:23:50Z')
    assert.equal(getParsedContent(store.sessionItems.s[1]).payload.item.content[0].text, 'Answer')
    assert.equal(store.localState.optimisticMessages.s, undefined)
    assert.equal(store.localState.sessions.s.itemsFetched, true)
})

test('the first visit loads history without a pre-existing local session record', async () => {
    const { store } = fixture()
    delete store.localState.sessions.s
    assert.equal(await store.loadSessionHistory('p', 's', 2), true)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
    assert.equal(store.localState.optimisticMessages.s, undefined)
})

test('a history load recomputes the visual items once, after metadata and content are both in place', async () => {
    const { store } = fixture()
    const seen = []
    store.recomputeVisualItems = id => seen.push(store.sessionItems[id].map(item => [item.line_num, hasContent(item)]))
    assert.equal(await store.loadSessionHistory('p', 's', 2), true)
    assert.deepEqual(seen, [[[1, true], [2, true]]])
})

test('a history load fills the lines the metadata leaves out with DEBUG_ONLY stubs, up to the last line', async () => {
    const { store, respond } = fixture({ lastLine: 4 })
    respond(async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? [metadata[0]] : transcript) }))
    assert.equal(await store.loadSessionHistory('p', 's', 4), true)
    const items = store.sessionItems.s
    assert.deepEqual(items.map(item => item.line_num), [1, 2, 3, 4])
    assert.equal(items[1].kind, 'assistant_message', 'the content window fills a stub with its real metadata')
    assert.ok(hasContent(items[1]))
    for (const stub of items.slice(2)) {
        assert.equal(stub.display_level, DISPLAY_LEVEL.DEBUG_ONLY)
        assert.equal(hasContent(stub), false)
    }
})

test('a history load keeps each metadata row at its own line around a left-out DEBUG_ONLY line', async () => {
    const { store, respond } = fixture({ lastLine: 4 })
    const third = { line_num: 3, display_level: DISPLAY_LEVEL.ALWAYS, kind: 'assistant_message',
        group_head: null, group_tail: null, timestamp: '2026-10-08T06:30:00Z' }
    // No content window: only the metadata places the rows.
    respond(async url => ({ ok: true, json: async () => url.includes('metadata') ? [copy(metadata[0]), third] : [] }))
    assert.equal(await store.loadSessionHistory('p', 's', 4), true)
    const items = store.sessionItems.s
    assert.deepEqual(items.map(item => [item.line_num, item.display_level, item.kind]), [
        [1, metadata[0].display_level, 'user_message'],
        [2, DISPLAY_LEVEL.DEBUG_ONLY, null],
        [3, DISPLAY_LEVEL.ALWAYS, 'assistant_message'],
        [4, DISPLAY_LEVEL.DEBUG_ONLY, null],
    ])
    assert.equal(items[2].timestamp, third.timestamp)
})

test('coverage turns the bare lines the metadata leaves out into DEBUG_ONLY stubs, then stops fetching', async () => {
    const { store, requests, respond } = fixture({ fetched: true, lastLine: 105 })
    const row = line => ({ line_num: line, display_level: DISPLAY_LEVEL.ALWAYS, kind: 'assistant_message',
        group_head: null, group_tail: null, timestamp: null })
    // Lines 2-10 are bare (a live item extended the array over them).
    store.sessionItems.s = Array.from({ length: 105 }, (_, i) =>
        i >= 1 && i <= 9 ? { line_num: i + 1 } : { ...row(i + 1), content: '{}' })
    respond(async url => ({ ok: true, json: async () => url.includes('metadata') ? [row(1), row(5)] : [] }))
    assert.equal(await store.ensureSessionItemsCoverage('s'), true)
    assert.deepEqual(store.sessionItems.s.slice(0, 10).map(item => item.display_level),
        [1, 3, 3, 3, 1, 3, 3, 3, 3, 3])
    requests.length = 0
    assert.equal(await store.ensureSessionItemsCoverage('s'), true)
    assert.deepEqual(requests, [], 'stubs are not bare: nothing left to heal')
})

test('a failed first visit publishes the error and permits a retry', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    respond(async () => ({ ok: false, status: 503 }))
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
    assert.equal(store.localState.sessions.s.itemsFetched, false)
    assert.equal(store.localState.sessions.s.itemsLoadingError, true)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'Server error (503)')
    respond(async () => { throw new TypeError('Failed to fetch') })
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'Network unreachable')
    respond(async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) }))
    assert.equal(await store.loadSessionHistory('p', 's', 2), true)
    assert.equal(store.localState.sessions.s.itemsLoadingError, false)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, null)
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
})

test('a failed metadata request publishes its real cause instead of a generic message', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    const items = async () => ({ ok: true, json: async () => copy(transcript) })
    respond(async url => url.includes('metadata') ? { ok: false, status: 502 } : items())
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'Server error (502)')
    respond(async url => {
        if (!url.includes('metadata')) return items()
        throw new DOMException('The operation timed out.', 'TimeoutError')
    })
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'No data received for 30 seconds')
    respond(async url => {
        if (!url.includes('metadata')) return items()
        throw new DOMException('The operation was aborted.', 'AbortError')
    })
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'No data received for 30 seconds')
})

// A response whose body arrives in chunks `delayMs` apart (a slow but live download).
function slowBody(value, chunks, delayMs) {
    const bytes = new TextEncoder().encode(JSON.stringify(value))
    const size = Math.ceil(bytes.length / chunks)
    let offset = 0
    return { ok: true, body: new ReadableStream({
        async pull(controller) {
            await new Promise(resolve => setTimeout(resolve, delayMs))
            if (offset >= bytes.length) return controller.close()
            controller.enqueue(bytes.slice(offset, offset + size))
            offset += size
        },
    }) }
}

test('a slow download that keeps receiving data outlives the stall timeout', async () => {
    const { store, respond } = fixture({ stallMs: 60 })
    delete store.localState.sessions.s
    respond(async url => slowBody(url.includes('metadata') ? metadata : transcript, 6, 25))
    const started = Date.now()
    assert.equal(await store.loadSessionHistory('p', 's', 2), true)
    assert.ok(Date.now() - started > 60, 'the load lasted longer than the stall timeout')
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
})

test('a connection that stops sending data ends in an error after the stall timeout', async () => {
    const { store, respond } = fixture({ stallMs: 30 })
    delete store.localState.sessions.s
    respond((url, options) => url.includes('metadata')
        ? Promise.resolve({ ok: true, json: async () => copy(metadata) })
        : new Promise((resolve, reject) => options.signal.addEventListener('abort',
            () => reject(options.signal.reason))))
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoadingError, true)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'No data received for 30 seconds')
})

test('unloading a first visit still prevents its pending snapshot from restoring history', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    const pending = deferred()
    respond(async url => {
        await pending.promise
        return { ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) }
    })
    const opening = store.loadSessionHistory('p', 's', 2)
    await Promise.resolve()
    store.localState.sessions.s.itemsFetched = false
    delete store.sessionItems.s
    pending.resolve()
    assert.equal(await opening, false)
    assert.equal(store.sessionItems.s, undefined)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
})

test('concurrent coverage waits for the same failing request instead of reporting premature success', async () => {
    const { store, respond, requests } = fixture({ fetched: true })
    const pending = deferred()
    respond(async () => { await pending.promise; return { ok: false, status: 503 } })
    const first = store.ensureSessionItemsCoverage('s')
    let settled = false
    const second = store.ensureSessionItemsCoverage('s').then(result => { settled = true; return result })
    try {
        for (let i = 0; i < 10; i++) await Promise.resolve()
        assert.equal(settled, false)
        assert.equal(requests.length, 1)
    } finally { pending.resolve() }
    assert.deepEqual(await Promise.all([first, second]), [false, false])
})

test('coverage checks fresh last_line after a coalesced pass settles', async () => {
    const { store, respond, requests } = fixture({ fetched: true, lastLine: 1 })
    const pending = deferred()
    respond(async () => {
        const first = requests.length === 1
        if (first) await pending.promise
        return { ok: true, json: async () => copy(first ? transcript.slice(0, 1) : transcript.slice(1)) }
    })
    const first = store.ensureSessionItemsCoverage('s')
    // Advance only after the first pass has captured its original bound.
    for (let i = 0; i < 10; i++) await Promise.resolve()
    assert.equal(requests.length, 1)
    store.sessions.s.last_line = 2
    const second = store.ensureSessionItemsCoverage('s')
    pending.resolve()
    assert.deepEqual(await Promise.all([first, second]), [true, true])
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
})

test('conversation opening and reconnect coverage share one pending initial history load', async () => {
    const { store, respond, requests } = fixture()
    const pending = deferred()
    respond(async url => {
        await pending.promise
        return { ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) }
    })
    const opening = store.loadSessionHistory('p', 's', 2)
    let settled = false
    const recovery = store.ensureSessionItemsCoverage('s', { loadUnfetched: true })
        .then(result => { settled = true; return result })
    try {
        for (let i = 0; i < 10; i++) await Promise.resolve()
        assert.equal(requests.length, 2)
        assert.equal(settled, false)
        assert.equal(store.localState.sessions.s.itemsLoading, true)
    } finally { pending.resolve() }
    assert.deepEqual(await Promise.all([opening, recovery]), [true, true])
    assert.equal(requests.length, 2)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
    assert.equal(store.localState.optimisticMessages.s, undefined)
})

test('a failed initial load remains retryable during focused reconciliation', async () => {
    const { store, respond, requests, reconciliation } = fixture()
    respond(async url => ({ ok: requests.length > 2, status: 503,
        json: async () => copy(url.includes('metadata') ? metadata : transcript) }))
    await reconciliation.onReconnected('p', 's', true)
    assert.equal(requests.length, 4)
    assert.equal(store.localState.sessions.s.itemsFetched, true)
    assert.equal(store.localState.sessions.s.itemsLoadingError, false)
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
})

test('ordinary coverage preserves lazy sessions and empty drafts', async () => {
    const { store, requests } = fixture()
    assert.equal(await store.ensureSessionItemsCoverage('s'), true)
    store.sessions.s.draft = true
    assert.equal(await store.ensureSessionItemsCoverage('s', { loadUnfetched: true }), true)
    store.sessions.s.draft = false
    store.sessions.s.last_line = 0
    assert.equal(await store.ensureSessionItemsCoverage('s', { loadUnfetched: true }), true)
    assert.equal(requests.length, 0)
    assert.equal(store.localState.sessions.s.itemsFetched, false)
})

test('a first-load snapshot does not discard live content received while REST is pending', async () => {
    const { store, respond } = fixture()
    const pending = deferred()
    respond(async url => {
        await pending.promise
        return { ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript.slice(0, 1)) }
    })
    const opening = store.loadSessionHistory('p', 's', 1)
    await Promise.resolve()
    store.addSessionItems('s', copy(transcript.slice(1)))
    pending.resolve()
    assert.equal(await opening, true)
    assert.equal(getParsedContent(store.sessionItems.s[1]).payload.item.content[0].text, 'Answer')
})

test('a forced load supersedes a stalled one, so a retry always runs', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    respond((url, options) => new Promise((resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new Error('aborted')))
    }))
    const stalled = store.loadSessionHistory('p', 's', 2)
    await Promise.resolve()
    respond(async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) }))
    assert.equal(await store.loadSessionHistory('p', 's', 2, null, { force: true }), true)
    assert.equal(await stalled, false)
    assert.equal(store.localState.sessions.s.itemsLoadingError, false)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
})

// The backend's refusal of a content read (``arefuse_unavailable``).
const refusal = (code = 'session_unavailable', carrier = 's') => new Response(JSON.stringify({
    error: 'Session is unavailable', code, unavailable_reason: 'history:claude-rewind', unavailable_session_id: carrier }),
    { status: 409 })
function recordingConsole() {
    const errors = []
    return { errors, logger: { ...quiet, error: (...args) => errors.push(args) } }
}

test('a history load refused as unavailable stores the reason, logs nothing and publishes no error', async () => {
    const { errors, logger } = recordingConsole()
    const { store, requests, respond } = fixture({ logger })
    delete store.localState.sessions.s
    respond(async () => refusal())
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.sessions.s.unavailable_reason, 'history:claude-rewind')
    assert.deepEqual(errors, [])
    const local = store.localState.sessions.s
    assert.equal(local.itemsLoading, false)
    assert.equal(local.itemsFetched, false)
    assert.ok(!local.itemsLoadingError, 'the unavailable view shows, not the error panel')
    requests.length = 0
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.deepEqual(requests, [], 'a known reason stops the load before any request')
})

test('reconnect coverage of a session refused as unavailable is not a failure', async () => {
    const { errors, logger } = recordingConsole()
    const { store, requests, respond } = fixture({ logger })
    respond(async () => refusal())
    assert.equal(await store.ensureSessionItemsCoverage('s', { loadUnfetched: true }), true)
    assert.equal(store.sessions.s.unavailable_reason, 'history:claude-rewind')
    assert.deepEqual(errors, [])
    requests.length = 0
    assert.equal(await store.ensureSessionItemsCoverage('s', { loadUnfetched: true }), true)
    assert.equal(await store.loadSessionItemsRanges('p', 's', [[1, 2]]), false)
    assert.deepEqual(requests, [], 'a known reason stops every read before any request')
})

test('a range read refused as unavailable stores the reason and logs nothing', async () => {
    const { errors, logger } = recordingConsole()
    const { store, respond } = fixture({ fetched: true, logger })
    respond(async () => refusal())
    assert.equal(await store.loadSessionItemsRanges('p', 's', [[1, 2]]), false)
    assert.equal(store.sessions.s.unavailable_reason, 'history:claude-rewind')
    assert.deepEqual(errors, [])
})

test('a 409 with another code still fails the history load as before', async () => {
    const { errors, logger } = recordingConsole()
    const { store, respond } = fixture({ logger })
    delete store.localState.sessions.s
    respond(async () => refusal('something_else'))
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.sessions.s.unavailable_reason, undefined)
    assert.equal(store.localState.sessions.s.itemsLoadingError, true)
    assert.equal(store.localState.sessions.s.itemsLoadingErrorReason, 'Server error (409)')
    assert.ok(errors.some(([message]) => message === 'Failed to load session metadata:'))
    assert.ok(errors.some(([message]) => message === 'Failed to load session history:'))
})

test('a refused subagent history puts the reason on the row the backend names', async () => {
    for (const [carrier, blocked, spared] of [['root', 'root', 's'], ['s', 's', 'root']]) {
        const { errors, logger } = recordingConsole()
        const { store, respond } = fixture({ logger })
        store.sessions.root = { id: 'root', project_id: 'p', unavailable_reason: null }
        respond(async () => refusal('session_unavailable', carrier))
        assert.equal(await store.loadSessionHistory('p', 's', 2, 'root'), false)
        assert.equal(store.sessions[blocked].unavailable_reason, 'history:claude-rewind')
        assert.equal(store.sessions[spared].unavailable_reason ?? null, null, `${spared} stays available`)
        assert.ok(!store.localState.sessions.s.itemsLoadingError)
        assert.deepEqual(errors, [])
    }
})

test('a reason that lands during the read keeps the row value and still shows the unavailable view', async () => {
    const { errors, logger } = recordingConsole()
    const { store, respond } = fixture({ logger })
    delete store.localState.sessions.s
    const gate = deferred()
    respond(async () => { await gate.promise; return refusal() })
    const pending = store.loadSessionHistory('p', 's', 2)
    await Promise.resolve()
    // The session_updated carrying the reason arrives before the 409 body is read.
    store.sessions.s.unavailable_reason = 'rollout_missing'
    noteUnavailableReasonUpdate(store.sessions.s)
    gate.resolve()
    assert.equal(await pending, false)
    assert.equal(store.sessions.s.unavailable_reason, 'rollout_missing')
    assert.ok(!store.localState.sessions.s.itemsLoadingError)
    assert.deepEqual(errors, [])
})

test('a reason update that lands during the read wins: the late refusal is a plain failure', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    const gate = deferred()
    respond(async () => { await gate.promise; return refusal() })
    const pending = store.loadSessionHistory('p', 's', 2)
    await Promise.resolve()
    // The session_updated that cleared the reason arrives before the 409 body is read.
    noteUnavailableReasonUpdate(store.sessions.s)
    gate.resolve()
    assert.equal(await pending, false)
    assert.equal(store.sessions.s.unavailable_reason, undefined)
    assert.equal(store.localState.sessions.s.itemsLoadingError, true)
})

test('a forced retry of a session known unavailable leaves the load in flight alone', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    const gate = deferred()
    const signals = []
    respond(async (url, options) => { signals.push(options?.signal); await gate.promise; return refusal() })
    const inFlight = store.loadSessionHistory('p', 's', 2)
    await new Promise(resolve => setTimeout(resolve, 0))
    store.sessions.s.unavailable_reason = 'rollout_missing'
    assert.equal(await store.loadSessionHistory('p', 's', 2, null, { force: true }), false)
    assert.ok(signals.length && signals.every(signal => !signal.aborted), 'no abort of the load in flight')
    gate.resolve()
    assert.equal(await inFlight, false)
    const local = store.localState.sessions.s
    assert.equal(local.itemsLoading, false)
    assert.equal(local.itemsFetched, false)
    assert.ok(!local.itemsLoadingError)
})
