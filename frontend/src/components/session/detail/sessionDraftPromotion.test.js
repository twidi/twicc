import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive, computed, ref, watch, nextTick, effectScope } from 'vue'
import { applySessionItemsAdded } from '../../../composables/wsSessionItems.js'

const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('watch([() => props.sessionId, session')
const end = source.indexOf('}, { immediate: true })', start) + '}, { immediate: true })'.length
const computeStart = source.indexOf('async function onComputeCompleted()')
const computeEnd = source.indexOf('watch(\n', computeStart)

test('a session opened empty keeps both turns through live item deliveries', async () => {
    const row = reactive({ id: 's', draft: false, last_line: 0, compute_version_up_to_date: true })
    const props = reactive({ sessionId: 's', projectId: 'p', parentSessionId: 'parent' })
    const scope = effectScope(), history = [], loads = []
    let fetched = false
    const store = {
        getSession: () => row, areSessionItemsFetched: () => fetched,
        fetchToolStates: async () => {}, markItemsLive() {},
        addSessionItems: (_, items) => history.push(...items),
    }
    const deps = { props, session: computed(() => row), watch, isLaunchedEphemeral: () => false, store,
        beginRevealFlow: () => () => {}, sessionActive: ref(false), scrollerRef: ref(null),
        loadSessionData: async line => {
            fetched = true
            loads.push(line)
            history.push({ line_num: 10, kind: 'user_message' })
        },
    }
    try {
        scope.run(() => new Function(...Object.keys(deps), source.slice(start, end))(...Object.values(deps)))
        await nextTick()
        row.last_line = 10
        await nextTick()
        await nextTick()
        for (const [line, kind] of [[28, 'assistant_message'], [39, 'user_message'], [40, 'assistant_message']]) {
            row.last_line = line
            await nextTick()
            applySessionItemsAdded(store, { session_id: 's', items: [{ line_num: line, kind }] })
        }
        assert.deepEqual(history.map(item => item.line_num), [10, 28, 39, 40])
        assert.deepEqual(loads, [10], 'the second turn must not reload the first turn')
    } finally { scope.stop() }
})

test('first lines start history loading after a ready session opens empty', async () => {
    const row = reactive({ id: 's', draft: false, last_line: 0, compute_version_up_to_date: true })
    const props = reactive({ sessionId: 's', projectId: 'p', parentSessionId: 'parent' })
    const scope = effectScope(), loads = []
    let fetched = false
    const deps = { props, session: computed(() => row), watch, isLaunchedEphemeral: () => false,
        store: { areSessionItemsFetched: () => fetched, fetchToolStates: async () => {} },
        beginRevealFlow: () => () => {}, loadSessionData: async line => { fetched = true; loads.push(line) },
        sessionActive: ref(false), scrollerRef: ref(null),
    }
    try {
        scope.run(() => new Function(...Object.keys(deps), source.slice(start, end))(...Object.values(deps)))
        await nextTick()
        row.last_line = 2
        await nextTick()
        await nextTick()
        assert.deepEqual(loads, [2])
        row.last_line = 20
        await nextTick()
        assert.deepEqual(loads, [2], 'later messages must not reload history')
    } finally { scope.stop() }
})

test('promoting a stable draft record loads its real history without waiting for another session object', async () => {
    const row = reactive({ id: 's', draft: true, last_line: 4 })
    const props = reactive({ sessionId: 's', projectId: 'p', parentSessionId: 'parent' })
    const scope = effectScope(), loads = []
    const deps = { props, session: computed(() => row), watch, isLaunchedEphemeral: () => false,
        store: { areSessionItemsFetched: () => false, fetchToolStates: async () => {} },
        beginRevealFlow: () => () => {}, loadSessionData: async line => loads.push(line),
        sessionActive: ref(false), scrollerRef: ref(null),
    }
    try {
        scope.run(() => new Function(...Object.keys(deps), source.slice(start, end))(...Object.values(deps)))
        await nextTick()
        assert.deepEqual(loads, [])
        row.draft = false
        await nextTick()
        await nextTick()
        assert.deepEqual(loads, [4])
    } finally { scope.stop() }
})

test('draft promotion and compute readiness share one history load', async () => {
    for (const ready of [undefined, false]) {
        const row = reactive({ id: 's', draft: true, last_line: 4, compute_version_up_to_date: ready })
        const props = reactive({ sessionId: 's', projectId: 'p', parentSessionId: 'parent' })
        const scope = effectScope(), loads = []
        let fetched = false
        const deps = { props, session: computed(() => row), watch, isLaunchedEphemeral: () => false,
            store: { areSessionItemsFetched: () => fetched, fetchToolStates: async () => {} },
            beginRevealFlow: () => () => {}, loadSessionData: async line => { fetched = true; loads.push(line) },
            sessionActive: ref(false), scrollerRef: ref(null),
        }
        try {
            const code = source.slice(start, end) + '\n' + source.slice(computeStart, computeEnd)
            scope.run(() => new Function(...Object.keys(deps), code)(...Object.values(deps)))
            await nextTick()
            row.draft = false
            row.compute_version_up_to_date = true
            await nextTick()
            await nextTick()
            assert.deepEqual(loads, [4])
            row.total_cost = 10
            row.last_line = 5
            await nextTick()
            assert.deepEqual(loads, [4], 'ordinary activity does not reload history')
            row.compute_version_up_to_date = false
            await nextTick()
            row.compute_version_up_to_date = true
            await nextTick()
            await nextTick()
            assert.deepEqual(loads, [4, 5], 'a real later recompute still refreshes history')
        } finally { scope.stop() }
    }
})
