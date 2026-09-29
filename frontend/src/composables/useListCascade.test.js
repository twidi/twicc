// Run with: node --test src/composables/useListCascade.test.js (from the frontend dir)
// Sidebar list cascade (visual refresh step 5c, docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §4.3):
// the composable's state, driven in an effect scope with fake timers and animation frames,
// the SessionList.vue wiring (file scan, §4.4), and the ArtifactBookmarkList.vue / data.js
// wiring (file scan, docs/plans/2026-09-29-accent-glow-design.md §17.4).
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

test('13c. isArriving: true while pending and playing, false when idle', () => {
    // Smooth reveals (sidebar-row-glide §10.7) wait for the arrival to end.
    const { cascade, env, effect } = setup()
    assert.equal(cascade.isArriving(), true, 'pending')
    env.flushFrame()
    env.flushFrame()
    assert.equal(cascade.isArriving(), true, 'playing')
    env.advance(listCascadeEndMs(2))
    assert.equal(cascade.isArriving(), false, 'idle')
    effect.stop()
    const empty = setup([])
    assert.equal(empty.cascade.isArriving(), false, 'idle before any row')
    empty.effect.stop()
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

test('15. ArtifactBookmarkList.vue wiring', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const sfc = readFileSync(join(here, '../components/artifacts/ArtifactBookmarkList.vue'), 'utf8')
    const script = sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
    const template = sfc.slice(sfc.indexOf('<template>'), sfc.indexOf('<style'))
    const flat = (text) => text.replace(/\s+/g, ' ')

    // The arrival waits for the bookmarks and the projects.
    assert.match(flat(script), /const listReady = computed\(\(\) => dataStore\.artifactBookmarksLoaded && dataStore\.projectsLoaded\)/)

    // The call: before the activeBookmarkId watcher, the unfiltered size gated on listReady,
    // the scope key naming the list the sidebar shows.
    const composableAt = script.indexOf('useListCascade(')
    const activeWatchAt = script.indexOf('watch(() => props.activeBookmarkId')
    assert.ok(composableAt > 0, 'useListCascade( is called')
    assert.ok(activeWatchAt > 0, 'the activeBookmarkId watcher')
    assert.ok(composableAt < activeWatchAt, 'the composable exists before the activeBookmarkId watcher')
    const call = flat(script.slice(composableAt, script.indexOf('})', composableAt)))
    assert.match(call, /items: list,/)
    assert.match(call, /getKey: \(b\) => b\.id,/)
    assert.match(call, /sourceSize: \(\) => \(listReady\.value \? scoped\.value\.length : 0\),/)
    assert.match(call, /scopeKey: \(\) => \(props\.showAllArtifacts \? 'all' : \(props\.effectiveProjectId \?\? ''\)\),/)
    assert.match(call, /getVisibleRange: visibleEntryRange,/)
    assert.match(script, /visibleIndexRange\(/, 'the range maths live in utils/listCascade.js')

    // One element per bookmark carries the cascade's class and style.
    const entry = template.match(/<div\b(?:[^>"]|"[^"]*")*class="bookmark-entry"(?:[^>"]|"[^"]*")*>/)
    assert.ok(entry, 'a .bookmark-entry element')
    assert.match(entry[0], /v-for="\(b, index\) in list"/)
    assert.match(entry[0], /:class="cascade\.itemClass\(b\)"/)
    assert.match(entry[0], /:style="cascade\.itemStyle\(b\)"/)

    // scrollRowIntoView selects the entries and returns the promise it scrolls in.
    const scrollFn = script.slice(script.indexOf('function scrollRowIntoView('), script.indexOf('\n}', script.indexOf('function scrollRowIntoView(')))
    assert.ok(scrollFn.includes("':scope > .bookmark-entry'"), 'it selects the entries')
    assert.match(scrollFn, /return nextTick\(/, 'it returns its nextTick promise')
    assert.ok(!script.includes('.bookmark-item-wrapper'), 'no .bookmark-item-wrapper query left')

    // Both reveal watchers hold the cascade on the open row.
    const activeWatch = script.slice(activeWatchAt, script.indexOf('})', activeWatchAt) + 30)
    assert.ok(activeWatch.includes('cascade.holdTarget('), 'the activeBookmarkId watcher holds')
    assert.match(activeWatch, /\{ immediate: true \}\)/)
    const readyWatchAt = script.indexOf('watch(() => listReady.value && scoped.value.some(isActive)')
    assert.ok(readyWatchAt > 0, 'a watcher on the open row joining the complete list')
    const readyWatch = script.slice(readyWatchAt, script.indexOf('})', readyWatchAt) + 30)
    assert.ok(readyWatch.includes('cascade.holdTarget('), 'it holds')
    assert.match(readyWatch, /\{ flush: 'post' \}\)/, "flush: 'post', not immediate")

    // Live entrances: one action, the subscription removed on unmount.
    const onAction = flat(script.slice(script.indexOf('dataStore.$onAction(')))
    assert.match(script, /const \w+ = dataStore\.\$onAction\(/, 'the subscription is kept')
    assert.match(onAction, /name === 'upsertArtifactBookmark'\) cascade\.noteLive\(args\[0\]\?\.id\)/)
    const stop = script.match(/const (\w+) = dataStore\.\$onAction\(/)[1]
    assert.match(flat(script), new RegExp(`onBeforeUnmount\\(\\(\\) => \\{[^}]*\\b${stop}\\(\\)`), 'removed on unmount')
})

test('16. data.js: projectsLoaded, and creation through upsertArtifactBookmark', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const store = readFileSync(join(here, '../stores/data.js'), 'utf8')
    assert.match(store, /\n {8}projectsLoaded: false,/, 'a state flag')
    const load = store.slice(store.indexOf('async loadHomeData()'), store.indexOf('\n        },', store.indexOf('async loadHomeData()')))
    const fin = load.slice(load.lastIndexOf('} finally {'))
    assert.ok(fin.includes('this.projectsLoaded = true'), "set in loadHomeData's finally")
    const create = store.slice(store.indexOf('async createArtifactBookmark('), store.indexOf('\n        },', store.indexOf('async createArtifactBookmark(')))
    assert.ok(create.includes('this.upsertArtifactBookmark(b)'), 'stored through upsertArtifactBookmark')
    assert.ok(!create.includes('this.artifactBookmarks[b.id] = b'), 'no direct assignment left')
})
