import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { computed, reactive } from 'vue'
import { i18n, setLocale, loadLocaleMessages } from '../../i18n/index.js'
import { createSessionEventHistory, createSessionEventHistoryState } from './history.js'
import { createSessionEventHistoryActions } from './historyActions.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const body = (events = [], has_more = false) => ({ events, sessions: {}, latest_id: 1, has_more })
const event = { id: 1, session_id: 's', kind: 'title.changed', occurred_at: '2026-11-01T12:00:00Z', data: {} }
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
afterEach(() => setLocale('en'))

function assertLocalizedCachedError(state, key) {
    assert.ok(state.error instanceof Error)
    assert.equal(state.error.uiMessage.key, key)
    const message = computed(() => state.error.message)
    const english = message.value
    assert.equal(english, i18n.global.t(key))
    setLocale('pt-BR')
    assert.equal(message.value, i18n.global.t(key))
    assert.notEqual(message.value, english)
    assert.equal(state.status, 'error')
}

for (const failure of ['empty page', 'repeated cursor']) {
    test(`cached pagination error changes locale after ${failure} without fetching again`, async () => {
        const state = reactive(createSessionEventHistoryState())
        let requests = 0
        const controller = createSessionEventHistory({ sessionId: 's', state, fetchPage: async () => {
            requests++
            return body(failure === 'empty page' ? [] : [event], true)
        } })
        controller.start()
        await controller.idle()
        const before = requests
        assertLocalizedCachedError(state, 'session.events.errors.pagination')
        assert.equal(requests, before)
        assert.equal(state.snapshot, null)
        controller.dispose()
    })
}

test('cached request timeout changes locale without another history request', async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    let requests = 0
    const actions = createSessionEventHistoryActions({ eventTarget: () => null, fetchPage: ({ signal }) => {
        requests++
        return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }))
    } })
    const store = reactive({ ...actions, sessions: { s: {} },
        localState: { sessionEventHistory: {}, sessionEventSegments: {}, sessionEventRetired: {} },
        recomputeVisualItems() {},
    })
    const controller = store.ensureSessionEventHistory('s')
    await tick()
    t.mock.timers.tick(30_000)
    await controller.idle()
    assertLocalizedCachedError(store.localState.sessionEventHistory.s, 'session.events.errors.timeout')
    assert.equal(requests, 1)
    store.clearSessionEventHistory('s')
})

test('cached external error remains literal across locale changes', async () => {
    const external = new Error('Provider detail: raw @value {data} | reason')
    const state = reactive(createSessionEventHistoryState())
    const controller = createSessionEventHistory({ sessionId: 's', state, fetchPage: async () => { throw external } })
    controller.start()
    await controller.idle()
    const message = computed(() => state.error.message)
    setLocale('pt-BR')
    assert.strictEqual(state.error, external)
    assert.equal(message.value, 'Provider detail: raw @value {data} | reason')
    assert.equal(state.error.uiMessage, undefined)
    controller.dispose()
})
