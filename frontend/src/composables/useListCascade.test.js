// Run with: node --test src/composables/useListCascade.test.js (from the frontend dir)
// Session-list cascade (visual refresh step 5c, docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §4.3):
// the composable's state, driven in an effect scope with fake timers and animation frames,
// and the SessionList.vue wiring (file scan, §4.4).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { effectScope, nextTick, ref, watch } from 'vue'

import { listCascadeEndMs } from '../utils/listCascade.js'
import { useListCascade } from './useListCascade.js'

/** Fake timers on a manual clock, and a manual animation-frame queue. */
function makeEnv() {
    const env = { now: 0, timers: new Map(), frames: new Map(), nextId: 1 }
    env.setTimeout = (fn, ms) => {
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.now + ms })
        return id
    }
    env.clearTimeout = (id) => { env.timers.delete(id) }
    env.advance = (ms) => {
        const target = env.now + ms
        for (;;) {
            const due = [...env.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            env.timers.delete(due[0])
            env.now = due[1].at
            due[1].fn()
        }
        env.now = target
    }
    env.requestAnimationFrame = (fn) => {
        const id = env.nextId++
        env.frames.set(id, fn)
        return id
    }
    env.cancelAnimationFrame = (id) => { env.frames.delete(id) }
    /** Run the frames queued so far (a frame requested by one of them waits for the next call). */
    env.flushFrame = () => {
        const due = [...env.frames.entries()]
        env.frames.clear()
        for (const [, fn] of due) fn()
    }
    return env
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve))

function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

function setup(ids = ['a', 'b', 'c', 'd', 'e'], { source = null, range = { start: 0, end: 3 }, env = makeEnv() } = {}) {
    const items = ref(ids.map((id) => ({ id })))
    const size = ref(source ?? ids.length)
    const scope = ref('p1')
    const visible = { value: range }
    const effect = effectScope()
    const cascade = effect.run(() => useListCascade({
        items,
        getKey: (s) => s.id,
        sourceSize: () => size.value,
        scopeKey: () => scope.value,
        getVisibleRange: () => visible.value,
        env,
    }))
    const setItems = (next, nextSize = next.length) => {
        items.value = next.map((id) => ({ id }))
        size.value = nextSize
    }
    const cls = (id) => cascade.itemClass({ id })
    const style = (id) => cascade.itemStyle({ id })
    return { items, size, scope, visible, effect, cascade, env, setItems, cls, style }
}

/** Setup, then run the arrival's two frames and the whole cascade. */
function setupIdle(ids = ['a', 'b', 'c'], options = {}) {
    const ctx = setup(ids, options)
    ctx.env.flushFrame()
    ctx.env.flushFrame()
    ctx.env.advance(listCascadeEndMs(20))
    return ctx
}

test('6. a filled list at setup: arriving, then the rows on screen cascade, then no class', () => {
    const { cls, style, env, effect } = setup()
    for (const id of ['a', 'b', 'c', 'd', 'e']) assert.equal(cls(id), 'list-arriving', id)
    assert.equal(style('a'), null)
    env.flushFrame()
    assert.equal(cls('a'), 'list-arriving', 'still pending after one frame')
    env.flushFrame()
    for (const [id, index] of [['a', 0], ['b', 1], ['c', 2]]) {
        assert.equal(cls(id), 'list-entering', id)
        assert.deepEqual(style(id), { '--list-enter-index': index })
    }
    for (const id of ['d', 'e']) {
        assert.equal(cls(id), null, `${id} is off screen`)
        assert.equal(style(id), null)
    }
    env.advance(listCascadeEndMs(2) - 1)
    assert.equal(cls('c'), 'list-entering')
    env.advance(1)
    for (const id of ['a', 'b', 'c', 'd', 'e']) assert.equal(cls(id), null, id)
    effect.stop()
})

test('7. arrival at the first fill; the search filter never arrives', async () => {
    {
        const { cls, env, setItems, effect } = setup([])
        assert.equal(cls('a'), null)
        assert.equal(env.frames.size, 0)
        setItems(['a', 'b'])
        await nextTick()
        assert.equal(cls('a'), 'list-arriving')
        env.flushFrame()
        env.flushFrame()
        assert.equal(cls('a'), 'list-entering')
        effect.stop()
    }
    {
        // The unfiltered list has rows, the filtered one none: the start plans nothing.
        const { cls, env, setItems, effect } = setup([], { source: 3 })
        assert.equal(cls('a'), 'list-arriving')
        env.flushFrame()
        env.flushFrame()
        setItems(['a', 'b'], 3)
        await nextTick()
        assert.equal(cls('a'), null)
        assert.equal(cls('b'), null)
        assert.equal(env.frames.size, 0)
        effect.stop()
    }
})

test('7b. the arrival runs pre-flush: a post-flush watcher already sees the class', async () => {
    // A post-flush class would show the row for one frame, then blink (5a lesson, §4.3).
    const env = makeEnv()
    const items = ref([])
    const seen = []
    const effect = effectScope()
    const cascade = effect.run(() => {
        // Registered first: a post-flush composable would run after this recorder.
        watch(items, () => { seen.push(cascade.itemClass({ id: 'a' })) }, { flush: 'post' })
        return useListCascade({
            items,
            getKey: (s) => s.id,
            sourceSize: () => items.value.length,
            scopeKey: () => 'p1',
            getVisibleRange: () => ({ start: 0, end: 2 }),
            env,
        })
    })
    items.value = [{ id: 'a' }, { id: 'b' }]
    await nextTick()
    assert.deepEqual(seen, ['list-arriving'])
    effect.stop()
})

test('8. holdTarget: inside the range, outside it, never settling, rejected, absent, outside pending', async () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']
    {
        const { cls, cascade, env, effect } = setup(ids)
        cascade.holdTarget('b', new Promise(() => {}))
        env.flushFrame()
        env.flushFrame()
        assert.equal(cls('a'), 'list-entering', 'inside the range: no wait')
        effect.stop()
    }
    {
        const { cls, style, cascade, env, visible, effect } = setup(ids)
        const target = deferred()
        cascade.holdTarget('h', target.promise)
        env.flushFrame()
        env.flushFrame()
        assert.equal(cls('a'), 'list-arriving', 'waits for the scroll')
        assert.equal(env.frames.size, 0)
        visible.value = { start: 5, end: 9 }
        target.resolve(true)
        await flushMicrotasks()
        assert.equal(env.frames.size, 1, 'one more frame')
        assert.equal(cls('a'), 'list-arriving')
        env.flushFrame()
        assert.equal(cls('a'), null)
        assert.equal(cls('h'), 'list-entering')
        assert.deepEqual(style('h'), { '--list-enter-index': 2 }, 'the second range is used')
        assert.equal(env.timers.size, 1, 'the cap timer is cancelled; only the end timer is left')
        effect.stop()
    }
    {
        const { cls, cascade, env, effect } = setup(ids)
        cascade.holdTarget('h', new Promise(() => {}))
        env.flushFrame()
        env.flushFrame()
        env.advance(299)
        await flushMicrotasks()
        assert.equal(env.frames.size, 0)
        env.advance(1)
        await flushMicrotasks()
        assert.equal(env.frames.size, 1, 'the cap releases the wait')
        env.flushFrame()
        assert.equal(cls('a'), 'list-entering')
        effect.stop()
    }
    {
        const { cls, cascade, env, effect } = setup(ids)
        const target = deferred()
        cascade.holdTarget('h', target.promise)
        env.flushFrame()
        env.flushFrame()
        target.reject(new Error('gone'))
        await flushMicrotasks()
        env.flushFrame()
        assert.equal(cls('a'), 'list-entering', 'a rejection releases too')
        effect.stop()
    }
    {
        const { cls, cascade, env, effect } = setup(ids)
        cascade.holdTarget('zz', new Promise(() => {}))
        env.flushFrame()
        env.flushFrame()
        assert.equal(cls('a'), 'list-entering', 'a held key absent from the list is no hold')
        effect.stop()
    }
    {
        const { cls, cascade, env, effect } = setupIdle()
        cascade.holdTarget('a', Promise.resolve())
        await flushMicrotasks()
        assert.equal(env.frames.size, 0)
        assert.equal(env.timers.size, 0)
        assert.equal(cls('a'), null)
        effect.stop()
    }
})

test('9. a scope change mid-pending: the old start stops at every step, the new list is pending', async () => {
    {
        const { cls, env, scope, setItems, effect } = setup()
        env.flushFrame()
        scope.value = 'p2'
        setItems(['x', 'y'])
        await nextTick()
        assert.equal(cls('x'), 'list-arriving')
        assert.equal(env.frames.size, 1, 'the old second frame is cancelled; the new first frame is queued')
        env.flushFrame()
        env.flushFrame()
        assert.equal(cls('x'), 'list-entering')
        assert.equal(cls('a'), null)
        effect.stop()
    }
    {
        const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
        const { cls, style, cascade, env, scope, setItems, effect } = setup(ids)
        const old = deferred()
        cascade.holdTarget('h', old.promise)
        env.flushFrame()
        env.flushFrame()
        scope.value = 'p2'
        setItems(['x', 'y', 'h'])
        await nextTick()
        assert.equal(cls('x'), 'list-arriving')
        old.resolve()
        await flushMicrotasks()
        assert.equal(env.frames.size, 1, 'the old wait requests no frame')
        env.flushFrame()
        env.flushFrame()
        assert.deepEqual(style('x'), { '--list-enter-index': 0 })
        assert.deepEqual(style('h'), { '--list-enter-index': 2 }, 'the plan is the new arrival\'s')
        assert.equal(cls('y'), 'list-entering')
        effect.stop()
    }
})

test('10. after the cascade: a noted live id new in the list enters alone, once', async () => {
    const { cls, style, cascade, env, setItems, effect } = setupIdle()
    cascade.noteLive('n')
    setItems(['n', 'a', 'b', 'c'])
    await nextTick()
    assert.equal(cls('n'), 'list-entering')
    assert.deepEqual(style('n'), { '--list-enter-index': 0 })
    assert.equal(cls('a'), null)
    env.advance(419)
    assert.equal(cls('n'), 'list-entering')
    env.advance(1)
    assert.equal(cls('n'), null)

    cascade.noteLive('a')
    setItems(['n', 'a', 'b', 'c', 'p'])
    await nextTick()
    assert.equal(cls('a'), null, 'noted but already present')
    assert.equal(cls('p'), null, 'new but not noted (next page)')

    cascade.noteLive('q')
    setItems(['n', 'a', 'b', 'c', 'p', 'r'])
    await nextTick()
    setItems(['n', 'a', 'b', 'c', 'p', 'r', 'q'])
    await nextTick()
    assert.equal(cls('q'), null, 'the run cleared the noted ids')

    cascade.noteLive('s')
    cascade.noteLive('t')
    setItems(['s', 't', 'n', 'a', 'b', 'c', 'p', 'r', 'q'])
    await nextTick()
    assert.deepEqual(style('s'), { '--list-enter-index': 0 })
    assert.deepEqual(style('t'), { '--list-enter-index': 0 }, 'several live ids all use index 0')
    effect.stop()
})

test('11. a live id during pending gets no live entry: the plan decides', async () => {
    const { cls, cascade, env, visible, setItems, effect } = setup(['a', 'b', 'c'])
    cascade.noteLive('n')
    setItems(['n', 'a', 'b', 'c'])
    await nextTick()
    assert.equal(cls('n'), 'list-arriving')
    visible.value = { start: 1, end: 3 }
    env.flushFrame()
    env.flushFrame()
    assert.equal(cls('n'), null, 'outside the plan, no live entry')
    assert.equal(cls('a'), 'list-entering')
    effect.stop()
})

test('12. no visible range: every row shows at once', () => {
    const { cls, env, effect } = setup(['a', 'b'], { range: null })
    env.flushFrame()
    env.flushFrame()
    assert.equal(cls('a'), null)
    assert.equal(cls('b'), null)
    assert.equal(env.timers.size, 0)
    effect.stop()
})

test('13. dispose cancels timers and frames; a held promise settling later requests no frame', async () => {
    {
        const { env, effect } = setup()
        env.flushFrame()
        effect.stop()
        assert.equal(env.frames.size, 0)
        assert.equal(env.timers.size, 0)
    }
    {
        const { cascade, env, effect } = setup(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'])
        const target = deferred()
        cascade.holdTarget('h', target.promise)
        env.flushFrame()
        env.flushFrame()
        effect.stop()
        target.resolve()
        await flushMicrotasks()
        assert.equal(env.frames.size, 0)
        assert.equal(env.timers.size, 0)
    }
    {
        const { cascade, env, setItems, effect } = setup(['a', 'b', 'c'])
        env.flushFrame()
        env.flushFrame()
        cascade.noteLive('n')
        setItems(['n', 'a', 'b', 'c'])
        await nextTick()
        assert.equal(env.timers.size, 2, 'the end timer and the live timer')
        effect.stop()
        assert.equal(env.timers.size, 0)
    }
})

test('13b. dropLive: before the run, and mid-animation', async () => {
    const { cls, cascade, env, setItems, effect } = setupIdle()
    cascade.noteLive('n')
    cascade.dropLive('n')
    setItems(['n', 'a', 'b', 'c'])
    await nextTick()
    assert.equal(cls('n'), null)

    cascade.noteLive('m')
    setItems(['m', 'n', 'a', 'b', 'c'])
    await nextTick()
    assert.equal(cls('m'), 'list-entering')
    env.advance(100)
    cascade.dropLive('m')
    assert.equal(cls('m'), null)
    assert.equal(env.timers.size, 0, 'its timer is cancelled')
    effect.stop()
})

test('14. SessionList.vue wiring', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const sfc = readFileSync(join(here, '../components/session/list/SessionList.vue'), 'utf8')
    const script = sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
    const template = sfc.slice(sfc.indexOf('<template>'))

    const composableAt = script.indexOf('useListCascade(')
    const firstSessionIdWatch = script.indexOf('watch(() => props.sessionId')
    assert.ok(composableAt > 0, 'useListCascade( is called')
    assert.ok(firstSessionIdWatch > 0)
    assert.ok(composableAt < firstSessionIdWatch, 'the composable exists before the sessionId watchers')

    const scroller = template.match(/<VirtualScroller\b(?:[^>"]|"[^"]*")*>/)[0]
    assert.match(scroller, /:item-class="cascade\.itemClass"/)
    assert.match(scroller, /:item-style="cascade\.itemStyle"/)

    // The scrolling watcher (flush: 'post', immediate) holds the cascade's target.
    const watchers = script.split('watch(() => props.sessionId').slice(1)
    const scrolling = watchers.find((body) => body.includes("flush: 'post'"))
    assert.ok(scrolling, 'the scrolling sessionId watcher')
    assert.ok(scrolling.slice(0, scrolling.indexOf("flush: 'post'")).includes('cascade.holdTarget('), 'it calls cascade.holdTarget(')

    const onAction = script.slice(script.indexOf('store.$onAction('))
    assert.ok(script.includes('store.$onAction('), 'a $onAction subscription')
    const handler = onAction.slice(0, onAction.indexOf('\n})') + 3)
    assert.match(handler, /'addSession'[\s\S]*?cascade\.noteLive\(/)
    assert.match(handler, /'createDraftSession'[\s\S]*?after\(/)
    const bind = handler.slice(handler.indexOf("'bindDraftSession'"))
    assert.ok(handler.includes("'bindDraftSession'"))
    assert.match(bind, /draftId !== sessionId/)
    assert.match(bind, /store\.sessions\[draftId\]/)
    assert.ok(bind.includes('cascade.dropLive('))
})
