import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { hasContent, getParsedContent, clearParsedContent, setParsedContent } from '../utils/parsedContent.js'
import { INITIAL_ITEMS_COUNT } from '../constants.js'
import { userMessageText, userMessageAttachmentCount } from '../providers/codex/canonical.js'
import { attachmentMatchKey, attachmentCountForMessage, matchableUserMessage } from '../utils/attachmentStrip.js'
import { sweepLoadedSessions } from '../composables/reconcileSweep.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const start = source.indexOf(`        ${name}(`)
    return start < 0 ? '' : source.slice(start, source.indexOf('        /**', start))
}
const matchStart = source.indexOf('function userMessageMatchKey(')
const matchEnd = source.indexOf('// Same key,', matchStart)
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
function fixture({ fetched = false, lastLine = 2 } = {}) {
    const requests = []
    let respond = async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) })
    const deps = { hasContent, getParsedContent, clearParsedContent, setParsedContent, INITIAL_ITEMS_COUNT,
        isLaunchedEphemeral: s => !!s.ephemeral,
        itemsCoverageInFlight: new Function(`return ${source.match(/const itemsCoverageInFlight = (new \w+\(\))/)[1]}`)(),
        sessionHistoryInFlight: new Map(),
        lineNumsToRanges: lines => lines.map(line => [line, line]),
        apiFetch: async url => { requests.push(url); return respond(url) }, console: quiet,
        getProviderHelpers: () => ({ extractUserMessageText: userMessageText, extractUserMessageAttachmentCount: userMessageAttachmentCount }),
        attachmentMatchKey, attachmentCountForMessage, matchableUserMessage,
    }
    const names = ['async loadSessionHistory', 'async ensureSessionItemsCoverage', 'async loadSessionItemsRanges',
        'async loadSessionMetadata', 'initSessionItemsFromMetadata', 'updateSessionItemsContent', 'clearOptimisticMessageIfMatched']
    const actions = new Function(...Object.keys(deps), `${source.slice(matchStart, matchEnd)}\n${source.slice(optimisticStart, optimisticEnd)}
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

test('a failed first visit publishes the error and permits a retry', async () => {
    const { store, respond } = fixture()
    delete store.localState.sessions.s
    respond(async () => ({ ok: false, status: 503 }))
    assert.equal(await store.loadSessionHistory('p', 's', 2), false)
    assert.equal(store.localState.sessions.s.itemsLoading, false)
    assert.equal(store.localState.sessions.s.itemsFetched, false)
    assert.equal(store.localState.sessions.s.itemsLoadingError, true)
    respond(async url => ({ ok: true, json: async () => copy(url.includes('metadata') ? metadata : transcript) }))
    assert.equal(await store.loadSessionHistory('p', 's', 2), true)
    assert.equal(store.localState.sessions.s.itemsLoadingError, false)
    assert.equal(store.sessionItems.s[1].kind, 'assistant_message')
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
