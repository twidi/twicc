import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref } from 'vue'

const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('function resolveStability()')
const end = source.indexOf('/**\n * Convert an array', start)
const block = source.slice(start, end)

function setup() {
    const jumps = [], frames = []
    let revision = 0
    const scroller = {
        getScrollRevision: () => revision,
        scrollToBottom: () => { jumps.push('bottom'); revision++ },
        scrollToTop: () => { jumps.push('top'); revision++ },
    }
    const dependencies = { scrollerRef: ref(scroller), sessionActive: ref(true),
        isAutoScrollingToBottom: ref(false), isInitialScrolling: ref(false),
        STABILITY_DEBOUNCE_MS: 100, MAX_STABILITY_WAIT_MS: 1000,
        requestAnimationFrame: callback => frames.push(callback) }
    const api = new Function(...Object.keys(dependencies), `
        let edgeScrollOperation = null, onStabilizedCallback = null;
        let stabilityTimeoutId = null, stabilityMaxWaitId = null;
        let pendingScrollToBottom = null;
        ${block}
        return { scrollToEdgeUntilStable, resolveStability,
            userScroll: () => typeof onUserScroll === 'function' && onUserScroll() };
    `)(...Object.values(dependencies))
    return { api, jumps, frames, dependencies, changeIntent: () => revision++ }
}

for (const edge of ['top', 'bottom']) {
    test(`pending ${edge} positioning does not override a newer scroll intent`, async () => {
        const v = setup()
        const operation = v.api.scrollToEdgeUntilStable(edge)
        assert.deepEqual(v.jumps, [edge])
        v.changeIntent()
        v.api.resolveStability()
        await operation
        assert.deepEqual(v.jumps, [edge])
        assert.equal(v.dependencies.isAutoScrollingToBottom.value, false)
        assert.equal(v.dependencies.isInitialScrolling.value, false)
    })
}
test('unchanged bottom positioning still performs its final correction', async () => {
    const v = setup()
    const operation = v.api.scrollToEdgeUntilStable('bottom')
    v.api.resolveStability(); await operation
    assert.deepEqual(v.jumps, ['bottom', 'bottom'])
})
test('user input immediately releases pending bottom following', async () => {
    const v = setup()
    const operation = v.api.scrollToEdgeUntilStable('bottom')
    v.changeIntent(); v.api.userScroll()
    assert.equal(v.dependencies.isAutoScrollingToBottom.value, false)
    v.api.resolveStability(); await operation
    assert.deepEqual(v.jumps, ['bottom'])
})


test('opposite-edge navigation takes ownership from a pending bottom operation', async () => {
    const v = setup()
    const bottom = v.api.scrollToEdgeUntilStable('bottom')
    const top = v.api.scrollToEdgeUntilStable('top')
    v.api.resolveStability(); await bottom; await Promise.resolve()
    assert.deepEqual(v.jumps, ['bottom', 'top'])
    v.api.resolveStability(); await top
})
