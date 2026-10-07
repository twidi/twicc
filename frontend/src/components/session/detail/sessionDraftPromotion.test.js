import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive, computed, ref, watch, nextTick, effectScope } from 'vue'

const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('watch([() => props.sessionId, session')
const end = source.indexOf('}, { immediate: true })', start) + '}, { immediate: true })'.length
const computeStart = source.indexOf('async function onComputeCompleted()')
const computeEnd = source.indexOf('watch(\n', computeStart)

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
