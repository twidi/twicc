// Run with: node --test src/composables/useVirtualScroll.test.js (from the frontend dir)
// Reveal the open row only near an edge (visual refresh step 6a-bis §10,
// docs/plans/2026-09-29-sidebar-row-glide-design.md): the virtual scroller's `nearest`
// alignment, and scrollToKey's jump-settle-check loop for it, driven with a fake container;
// and the smooth reveal (§10.7): when it is smooth, its hold, its destination, its endings.
import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTick, ref, shallowRef } from 'vue'

import { useVirtualScroll } from './useVirtualScroll.js'

// Node has no animation frames: the scroll handler's frame runs on a timer.
globalThis.requestAnimationFrame ??= (callback) => setTimeout(callback, 0)
globalThis.cancelAnimationFrame ??= (handle) => clearTimeout(handle)

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const PADDING = 4
const CLIENT_HEIGHT = 400
const SETTLE_MS = 15
// The session list's compact margins: 5rem bands at a 15px root, the button's 3.125rem,
// shifted by the list's padding (revealMargins).
const MARGINS = { marginTop: 75 - PADDING, marginBottom: 75 + 46.875 + PADDING }
// From the top of 40 rows of 36px, row 30 (1080..1116) comes to the bottom band edge.
const FIRST_JUMP = 1116 - CLIENT_HEIGHT + MARGINS.marginBottom

/**
 * A fake scroll container: clientHeight, a scrollHeight computed from the current
 * positions plus the padding, a scrollTop the browser clamps to the real range (every
 * accepted write is recorded), and EventTarget-like listeners. Its scrollTo records the
 * call and moves only halfway (dispatching `scroll`): a smooth scroll in flight, that the
 * test completes with finishSmooth().
 */
function makeContainer() {
    const listeners = new Map()
    let top = 0
    const clamp = (value, el) => Math.max(0, Math.min(value, el.scrollHeight - el.clientHeight))
    const container = {
        clientHeight: CLIENT_HEIGHT,
        contentHeight: () => 0,
        writes: [],
        listenerLog: [],
        scrollToCalls: [],
        get scrollHeight() { return Math.max(this.clientHeight, this.contentHeight() + 2 * PADDING) },
        get scrollTop() { return top },
        set scrollTop(value) {
            top = clamp(value, this)
            this.writes.push(top)
        },
        scrollTo(options) {
            this.scrollToCalls.push({ ...options })
            top += (clamp(options.top, this) - top) / 2
            this.dispatchEvent({ type: 'scroll' })
        },
        /** Complete the last smooth scroll: at its target, then `scroll` and `scrollend`. */
        finishSmooth({ scrollend = true } = {}) {
            top = clamp(this.scrollToCalls.at(-1).top, this)
            this.dispatchEvent({ type: 'scroll' })
            if (scrollend) this.dispatchEvent({ type: 'scrollend' })
        },
        addEventListener(type, handler, options) {
            this.listenerLog.push(['add', type, handler, options])
            if (!listeners.has(type)) listeners.set(type, new Set())
            listeners.get(type).add(handler)
        },
        removeEventListener(type, handler, options) {
            this.listenerLog.push(['remove', type, handler, options])
            listeners.get(type)?.delete(handler)
        },
        dispatchEvent(event) {
            if (!event.target) event.target = container
            for (const handler of listeners.get(event.type) ?? []) handler(event)
            return true
        },
        listenerCount(type) { return listeners.get(type)?.size ?? 0 },
    }
    return container
}

/**
 * A scroller over `count` rows, wired like VirtualScroller: the container's scroll event
 * goes to handleScroll; `height` measures every row, `estimated` leaves them unmeasured.
 */
function setup({ count = 40, height = 36, estimated = null, buffer = undefined, unloadBuffer = undefined } = {}) {
    const items = ref(Array.from({ length: count }, (_, i) => ({ id: `s${i}` })))
    const container = makeContainer()
    // shallowRef: like a template ref to a DOM element, the value is the element itself (a
    // plain-object fake in a ref() would be a reactive proxy, never === event.target).
    const containerRef = shallowRef(container)
    // Outside a component, onUnmounted only warns: keep the output readable.
    const warn = console.warn
    console.warn = (...args) => { if (!String(args[0]).includes('onUnmounted')) warn(...args) }
    let vs
    try {
        vs = useVirtualScroll({ items, itemKey: (item) => item.id, minItemHeight: estimated ?? height, buffer, unloadBuffer, containerRef })
    } finally {
        console.warn = warn
    }
    container.contentHeight = () => vs.totalHeight.value
    container.addEventListener('scroll', vs.handleScroll)
    if (estimated === null) vs.batchUpdateItemHeights(new Map(items.value.map((item) => [item.id, height])))
    vs.syncScrollPosition()
    container.writes.length = 0
    return { vs, container, containerRef, items }
}

/** Set the scroll like a user would (no explicit call), then sync the scroller's state. */
function placeAt(vs, container, value) {
    container.scrollTop = value
    vs.syncScrollPosition()
    container.writes.length = 0
}

/** Change the heights of rows `from`..`to` (inclusive). */
function grow(vs, from, to, height) {
    const updates = new Map()
    for (let i = from; i <= to; i++) updates.set(`s${i}`, height)
    vs.batchUpdateItemHeights(updates)
}

/** The target that brings row `index` to the bottom band edge, clamped to the real range. */
function bottomTarget(vs, container, index, marginBottom = MARGINS.marginBottom) {
    const pos = vs.positions.value[index]
    const max = container.scrollHeight - container.clientHeight
    return Math.max(0, Math.min(pos.top + pos.height - container.clientHeight + marginBottom, max))
}

// ---------------------------------------------------------------------------
// scrollToIndex, align 'nearest'
// ---------------------------------------------------------------------------

test('scrollToIndex nearest: no write inside the zone', () => {
    const { vs, container } = setup()
    placeAt(vs, container, 360)
    // Row 15: 540..576, zone 431..635.
    vs.scrollToIndex(15, { align: 'nearest', ...MARGINS })
    assert.deepEqual(container.writes, [])
    assert.equal(vs.scrollTop.value, 360)
})

test('scrollToIndex nearest: the margins, the container\'s clientHeight, offset ignored', () => {
    const { vs, container } = setup()
    placeAt(vs, container, 360)
    // The content-box height a resize reports: ignored, clientHeight is the viewport.
    vs.updateViewportHeight(CLIENT_HEIGHT - 2 * PADDING)
    vs.scrollToIndex(10, { align: 'nearest', offset: 100, ...MARGINS })
    assert.deepEqual(container.writes, [360 - MARGINS.marginTop], 'row 10 (360): its top to the top band edge')
    vs.scrollToIndex(30, { align: 'nearest', offset: 100, ...MARGINS })
    assert.equal(container.writes[1], 1116 - CLIENT_HEIGHT + MARGINS.marginBottom, 'row 30: its bottom to the bottom band edge')
    assert.equal(vs.scrollTop.value, container.writes[1])
})

test('scrollToIndex nearest: clamped to the container\'s real scroll range', () => {
    const { vs, container } = setup()
    // positions end at 1440; the container adds 2 × 4px of padding: its max is 1048, not 1040.
    vs.scrollToIndex(39, { align: 'nearest', ...MARGINS })
    assert.deepEqual(container.writes, [1048])
    vs.scrollToIndex(1, { align: 'nearest', ...MARGINS })
    assert.deepEqual(container.writes, [1048, 0], 'the top clamp')
})

test('scrollToIndex nearest: margins default to 0 (plain nearest)', () => {
    const { vs, container } = setup()
    vs.scrollToIndex(10, { align: 'nearest' })
    assert.deepEqual(container.writes, [], 'row 10 (360..396) is fully visible')
    vs.scrollToIndex(11, { align: 'nearest' })
    assert.deepEqual(container.writes, [432 - CLIENT_HEIGHT])
})

// ---------------------------------------------------------------------------
// scrollToKey, align 'nearest'
// ---------------------------------------------------------------------------

test('scrollToKey nearest: re-jumps when the row left the zone between two attempts', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    assert.deepEqual(container.writes, [FIRST_JUMP])
    await delay(3)
    // Rows between the first visible one (23) and the target measure taller: the anchor
    // correction keeps row 23 in place, the target drifts under the bottom band.
    grow(vs, 24, 29, 60)
    assert.equal(await done, true)
    const expected = bottomTarget(vs, container, 30)
    assert.deepEqual(container.writes, [FIRST_JUMP, expected])
    assert.equal(vs.scrollTop.value, expected)
})

test('scrollToKey nearest: stops at the clamp limit', async () => {
    const { vs, container } = setup()
    assert.equal(await vs.scrollToKey('s39', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS }), true)
    assert.deepEqual(container.writes, [1048], 'one jump: the end of the list is a success')
})

test('scrollToKey nearest: a row that does not fit lands with its bottom at the bottom edge', async () => {
    const { vs, container } = setup()
    grow(vs, 20, 20, 250)
    container.writes.length = 0
    assert.equal(await vs.scrollToKey('s20', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS }), true)
    const pos = vs.positions.value[20]
    assert.deepEqual(container.writes, [pos.top + 250 - CLIENT_HEIGHT + MARGINS.marginBottom], 'one jump')
    assert.ok(pos.top >= vs.scrollTop.value, 'its top stays in view')
    // Taller than the viewport minus the bottom band: its top.
    grow(vs, 21, 21, 320)
    container.writes.length = 0
    assert.equal(await vs.scrollToKey('s21', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS }), true)
    assert.deepEqual(container.writes, [vs.positions.value[21].top])
})

test('scrollToKey nearest: at 0, estimated heights that grow push the row out: the second attempt scrolls', async () => {
    const { vs, container } = setup({ estimated: 35 })
    // Row 5 on estimates: 175..210, inside the zone 71..278 at 0.
    const done = vs.scrollToKey('s5', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    assert.deepEqual(container.writes, [], 'no first jump')
    await delay(3)
    grow(vs, 0, 39, 60)
    assert.equal(await done, true)
    assert.deepEqual(container.writes, [360 - CLIENT_HEIGHT + MARGINS.marginBottom])
})

for (const [name, event] of [
    ['wheel', { type: 'wheel' }],
    ['pointerdown', { type: 'pointerdown' }],
    ['touchstart', { type: 'touchstart' }],
    ['touchmove', { type: 'touchmove' }],
    ['keydown PageDown', { type: 'keydown', key: 'PageDown' }],
    ['keydown Space', { type: 'keydown', key: ' ' }],
]) {
    test(`scrollToKey nearest: a ${name} on the container during the settle ends the loop`, async () => {
        const { vs, container } = setup()
        const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
        await delay(3)
        container.dispatchEvent({ ...event })
        grow(vs, 24, 29, 60)
        await done
        assert.deepEqual(container.writes, [FIRST_JUMP], 'no second jump')
    })
}

test('scrollToKey nearest: a keydown that scrolls nothing does not end the loop', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    container.dispatchEvent({ type: 'keydown', key: 'Enter' })
    grow(vs, 24, 29, 60)
    assert.equal(await done, true)
    assert.equal(container.writes.length, 2, 'the second attempt re-aligns')
})

test('scrollToKey nearest: a scrollToIndex from outside the loop during the settle ends it', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    vs.scrollToIndex(0, { align: 'start' })
    grow(vs, 24, 29, 60)
    await done
    assert.deepEqual(container.writes, [FIRST_JUMP, 0], 'no second jump')
})

test('scrollToKey nearest: a scrollToIndex nearest that does not write during the settle does not end it', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    // Row 27 (972..1008) sits inside the zone at FIRST_JUMP: no write, no explicit-scroll bump.
    vs.scrollToIndex(27, { align: 'nearest', ...MARGINS })
    assert.deepEqual(container.writes, [FIRST_JUMP])
    grow(vs, 24, 29, 60)
    assert.equal(await done, true)
    assert.deepEqual(container.writes, [FIRST_JUMP, bottomTarget(vs, container, 30)], 'the loop re-aligns the row')
})

test('scrollToKey nearest: a scrollToIndex nearest that writes during the settle ends it', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    // Row 10 (360..396) is above the zone at FIRST_JUMP: its top to the top band edge.
    vs.scrollToIndex(10, { align: 'nearest', ...MARGINS })
    assert.deepEqual(container.writes, [FIRST_JUMP, 360 - MARGINS.marginTop])
    grow(vs, 24, 29, 60)
    assert.equal(await done, false)
    assert.deepEqual(container.writes, [FIRST_JUMP, 360 - MARGINS.marginTop], 'no second jump')
})

test('scrollToKey nearest: a reorder during the settle checks the row at its new index', async () => {
    const { vs, container, items } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    // s35 moves to the head: s30 moves one row down (1116..1152), out of the zone; the row
    // now at its old index (s29) sits where s30 was aligned.
    const list = items.value.slice()
    const [moved] = list.splice(35, 1)
    items.value = [moved, ...list]
    assert.equal(await done, true)
    assert.deepEqual(container.writes, [FIRST_JUMP, FIRST_JUMP + 36], 'the loop re-aligns s30')
    assert.equal(vs.scrollTop.value, bottomTarget(vs, container, 31))
})

test('scrollToKey nearest: a newer reveal that does not scroll ends the older loop', async () => {
    const { vs, container } = setup()
    const first = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    await delay(3)
    // Row 27 (972..1008) sits inside the zone at FIRST_JUMP (912.875..1116): no write.
    const second = vs.scrollToKey('s27', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    assert.deepEqual(container.writes, [FIRST_JUMP])
    // Rows 28 and 29 grow: row 30 leaves the zone, row 27 does not move.
    grow(vs, 28, 29, 60)
    assert.equal(await second, true)
    await first
    assert.deepEqual(container.writes, [FIRST_JUMP], 'no write after the second call')
})

test('scrollToKey nearest: isCurrent turning false during the settle ends the loop', async () => {
    const { vs, container } = setup()
    let current = true
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS, isCurrent: () => current })
    await delay(3)
    current = false
    grow(vs, 24, 29, 60)
    await done
    assert.deepEqual(container.writes, [FIRST_JUMP], 'no second jump')
})

test('scrollToKey nearest: a scroll event and an anchor correction do not end the loop', async () => {
    const { vs, container } = setup()
    const done = vs.scrollToKey('s30', { align: 'nearest', settleMs: SETTLE_MS, ...MARGINS })
    // The write's own scroll event, queued after the programmatic flag is reset.
    container.dispatchEvent({ type: 'scroll' })
    await delay(3)
    // A row above the first visible one grows: the anchor correction writes (flag off).
    grow(vs, 0, 0, 46)
    assert.deepEqual(container.writes, [FIRST_JUMP, FIRST_JUMP + 10], 'the anchor correction wrote')
    container.dispatchEvent({ type: 'scroll' })
    grow(vs, 24, 29, 60)
    assert.equal(await done, true)
    const expected = bottomTarget(vs, container, 30)
    assert.deepEqual(container.writes, [FIRST_JUMP, FIRST_JUMP + 10, expected], 'the second attempt re-aligns the row')
})

test('scrollToKey with another alignment keeps the overlap check', async () => {
    const { vs, container } = setup()
    assert.equal(await vs.scrollToKey('s30', { align: 'center', settleMs: SETTLE_MS }), true)
    assert.equal(container.writes.length, 1)
})

// ---------------------------------------------------------------------------
// The user-input listeners
// ---------------------------------------------------------------------------

test('the input listeners follow the container, passive, same handler for add and remove', async () => {
    const { container, containerRef } = setup()
    const types = ['wheel', 'touchstart', 'touchmove', 'pointerdown', 'pointercancel', 'keydown']
    for (const type of types) assert.equal(container.listenerCount(type), 1, type)
    const other = makeContainer()
    containerRef.value = other
    for (const type of types) {
        assert.equal(container.listenerCount(type), 0, `${type} removed from the old container`)
        assert.equal(other.listenerCount(type), 1, `${type} on the new one`)
        const [added] = container.listenerLog.filter(([op, t]) => op === 'add' && t === type)
        const [removed] = container.listenerLog.filter(([op, t]) => op === 'remove' && t === type)
        assert.equal(removed[2], added[2], `${type}: the same handler`)
        assert.deepEqual(added[3], { passive: true })
        assert.equal(removed[3], added[3], `${type}: the same options`)
    }
    containerRef.value = null
    await nextTick()
    for (const type of types) assert.equal(other.listenerCount(type), 0, `${type} removed on null`)
})

// ---------------------------------------------------------------------------
// The smooth reveal (§10.7)
// ---------------------------------------------------------------------------

// From 360, row 18 (648..684) sits under the zone (431..634.125): a 49.875px move down.
const NEAR_FROM = 360
const NEAR_TARGET = 684 - CLIENT_HEIGHT + MARGINS.marginBottom
const NEAR = { align: 'nearest', allowSmooth: true, ...MARGINS }
// Past the RAF-throttled scroll handler (a timer in Node).
const afterFrame = () => delay(5)

/** Stub prefers-reduced-motion for the duration of `fn`. */
async function withReducedMotion(fn) {
    const previous = globalThis.matchMedia
    globalThis.matchMedia = (query) => ({ matches: query === '(prefers-reduced-motion: reduce)' })
    try {
        await fn()
    } finally {
        if (previous === undefined) delete globalThis.matchMedia
        else globalThis.matchMedia = previous
    }
}

/** A scroller at NEAR_FROM, its render range up to date. */
async function nearSetup(options) {
    const ctx = setup(options)
    placeAt(ctx.vs, ctx.container, NEAR_FROM)
    await nextTick()
    return ctx
}

/** A smooth reveal of row 18 from NEAR_FROM, now halfway (scrollTop.value synced). */
async function smoothHalfway(options) {
    const ctx = await nearSetup(options)
    const done = ctx.vs.scrollToIndex(18, NEAR)
    assert.ok(done instanceof Promise, 'a smooth reveal returns its done')
    assert.deepEqual(ctx.container.scrollToCalls, [{ top: NEAR_TARGET, behavior: 'smooth' }])
    await afterFrame()
    assert.equal(ctx.vs.scrollTop.value, NEAR_FROM + (NEAR_TARGET - NEAR_FROM) / 2)
    return { ...ctx, done }
}

/** Whether `done` resolves at once (an ending), not at the 600ms timeout. */
const endsAtOnce = (done) => Promise.race([done.then(() => true), delay(50).then(() => false)])

/** True when the anchor correction writes for a row above the viewport growing (no hold). */
function anchorCorrects(vs, container) {
    const before = container.writes.length
    grow(vs, 0, 0, (vs.getItemHeight('s0') ?? 36) + 10)
    return container.writes.length > before
}

/** True when a nearest reveal of row `index` does something (a write or a scroll). */
function revealWrites(vs, container, index = 18) {
    const before = container.writes.length + container.scrollToCalls.length
    vs.scrollToIndex(index, NEAR)
    return container.writes.length + container.scrollToCalls.length > before
}

test('smooth: a near nearest reveal scrolls smoothly, no instant write', async () => {
    const { container } = await smoothHalfway()
    assert.deepEqual(container.writes, [])
})

test('smooth: a far reveal is instant and returns null', async () => {
    const { vs, container } = setup()
    assert.equal(vs.scrollToIndex(30, NEAR), null)
    assert.deepEqual(container.writes, [FIRST_JUMP])
    assert.deepEqual(container.scrollToCalls, [])
})

test('smooth: reduced motion → instant', async () => {
    await withReducedMotion(async () => {
        const { vs, container } = await nearSetup()
        assert.equal(vs.scrollToIndex(18, NEAR), null)
        assert.deepEqual(container.writes, [NEAR_TARGET])
        assert.deepEqual(container.scrollToCalls, [])
    })
})

test('smooth: allowSmooth false (the default) → instant', async () => {
    const { vs, container } = await nearSetup()
    assert.equal(vs.scrollToIndex(18, { ...NEAR, allowSmooth: false }), null)
    assert.deepEqual(container.writes, [NEAR_TARGET])
    assert.deepEqual(container.scrollToCalls, [])
    assert.equal(vs.scrollToIndex(19, { align: 'nearest', behavior: 'smooth', ...MARGINS }), null, 'behavior ignored')
    assert.equal(container.writes.length, 2)
    assert.deepEqual(container.scrollToCalls, [])
})

test('smooth: a target more than buffer away but within clientHeight → instant', async () => {
    // Row 16 (576..612) from 0: 337.875px down, over a 300px buffer, under the 400px viewport.
    const target = 612 - CLIENT_HEIGHT + MARGINS.marginBottom
    {
        const { vs, container } = setup({ buffer: 300, unloadBuffer: 300 })
        assert.equal(vs.scrollToIndex(16, NEAR), null)
        assert.deepEqual(container.writes, [target])
    }
    {
        const { vs, container } = setup()
        assert.ok(vs.scrollToIndex(16, NEAR) instanceof Promise, 'smooth with the default 500px buffer')
        assert.deepEqual(container.scrollToCalls, [{ top: target, behavior: 'smooth' }])
    }
})

// Upward, buffer 300 at 800: renderRange.start = row 13 (468..504). Row 20 (720..756) sits
// above the zone (871..1074.125): target 649, the upward interval [row 9 (324..360, it
// straddles 649 - 300 = 349), row 13).
async function upwardSetup(measured) {
    const ctx = setup({ estimated: 36, buffer: 300, unloadBuffer: 300 })
    for (const [from, to] of measured) grow(ctx.vs, from, to, 36)
    placeAt(ctx.vs, ctx.container, 800)
    await nextTick()
    assert.equal(ctx.vs.renderRange.value.start, 13)
    return ctx
}

test('smooth: upward with unmeasured items in the upward interval → instant', async () => {
    const { vs, container } = await upwardSetup([[13, 39]])
    assert.equal(vs.scrollToIndex(20, NEAR), null)
    assert.deepEqual(container.writes, [720 - MARGINS.marginTop])
})

test('smooth: upward with the upward interval measured → smooth', async () => {
    // Row 8 (288..324) lies above the interval: unmeasured, it does not count.
    const { vs, container } = await upwardSetup([[9, 39]])
    assert.ok(vs.scrollToIndex(20, NEAR) instanceof Promise)
    assert.deepEqual(container.scrollToCalls, [{ top: 720 - MARGINS.marginTop, behavior: 'smooth' }])
})

test('smooth: upward, an unmeasured item straddling target - buffer → instant', async () => {
    const { vs, container } = await upwardSetup([[0, 8], [10, 39]])
    assert.equal(vs.scrollToIndex(20, NEAR), null)
    assert.deepEqual(container.scrollToCalls, [])
})

test('smooth: downward with unmeasured items past renderRange.end → smooth', async () => {
    const { vs, container } = setup({ estimated: 36, buffer: 300, unloadBuffer: 300 })
    placeAt(vs, container, NEAR_FROM)
    await nextTick()
    assert.ok(vs.renderRange.value.end < 40)
    assert.ok(vs.scrollToIndex(18, NEAR) instanceof Promise)
    assert.deepEqual(container.scrollToCalls, [{ top: NEAR_TARGET, behavior: 'smooth' }])
})

test('smooth: rows above the target grow mid-scroll → no write before scrollend, the re-jump after it and the settle', async () => {
    const { vs, container } = await nearSetup()
    const result = vs.scrollToKey('s18', { ...NEAR, settleMs: SETTLE_MS })
    await afterFrame()
    grow(vs, 12, 17, 60)
    await delay(SETTLE_MS * 3)
    assert.deepEqual(container.writes, [], 'the hold: no anchor correction')
    assert.equal(container.scrollToCalls.length, 1, 'no re-jump before the end')
    container.finishSmooth()
    await delay(SETTLE_MS / 3)
    assert.equal(container.scrollToCalls.length, 1, 'the settle first')
    await delay(SETTLE_MS * 2)
    assert.equal(container.scrollToCalls.length, 2, 'the re-jump')
    assert.equal(container.scrollToCalls[1].top, bottomTarget(vs, container, 18))
    container.finishSmooth()
    assert.equal(await result, true)
    assert.deepEqual(container.writes, [])
})

test('smooth: without a scrollend, the check runs after 600ms', async () => {
    const { vs, container } = await nearSetup()
    let settled = false
    const result = vs.scrollToKey('s18', { ...NEAR, settleMs: SETTLE_MS }).then((value) => { settled = true; return value })
    await afterFrame()
    container.finishSmooth({ scrollend: false })
    await delay(500)
    assert.equal(settled, false, 'still waiting for the end')
    await delay(150)
    assert.equal(settled, true)
    assert.equal(await result, true)
    assert.equal(container.scrollToCalls.length, 1)
})

test('smooth: an early scrollend away from the target is ignored; an at-target one ends it before 600ms', async () => {
    const { vs, container } = await nearSetup()
    let settled = false
    const started = Date.now()
    const result = vs.scrollToKey('s18', { ...NEAR, settleMs: SETTLE_MS }).then((value) => { settled = true; return value })
    await afterFrame()
    container.dispatchEvent({ type: 'scrollend' })
    await delay(SETTLE_MS * 3)
    assert.equal(settled, false, 'the early scrollend is ignored')
    container.finishSmooth()
    assert.equal(await result, true)
    assert.ok(Date.now() - started < 400, 'before the 600ms timeout')
})

test('smooth: a child pointerdown, then a second nearest call at halfway → no write, destination and hold kept', async () => {
    const { vs, container } = await smoothHalfway()
    container.dispatchEvent({ type: 'pointerdown', target: {} })
    // Row 18 is outside the zone at halfway, inside at the destination.
    assert.equal(vs.scrollToIndex(18, NEAR), null)
    assert.deepEqual(container.writes, [])
    assert.equal(container.scrollToCalls.length, 1)
    assert.equal(anchorCorrects(vs, container), false, 'the hold is kept')
})

test('smooth: a prevented ArrowDown keydown → destination and hold kept', async () => {
    const { vs, container } = await smoothHalfway()
    container.dispatchEvent({ type: 'keydown', key: 'ArrowDown', defaultPrevented: true })
    assert.equal(revealWrites(vs, container), false, 'the destination is kept')
    assert.equal(anchorCorrects(vs, container), false, 'the hold is kept')
})

test('smooth: a touch pointercancel ends it (hold released); a touchmove alone does not', async () => {
    {
        const { vs, container } = await smoothHalfway()
        container.dispatchEvent({ type: 'touchmove' })
        container.dispatchEvent({ type: 'pointercancel', pointerType: 'mouse' })
        assert.equal(anchorCorrects(vs, container), false, 'nothing ended')
    }
    {
        const { vs, container, done } = await smoothHalfway()
        container.dispatchEvent({ type: 'pointercancel', pointerType: 'touch' })
        assert.equal(await endsAtOnce(done), true)
        assert.equal(anchorCorrects(vs, container), true, 'the hold is released')
    }
})

test('smooth: the hold lasts until the end, past 500ms', async () => {
    const { vs, container, done } = await smoothHalfway()
    await delay(520)
    assert.equal(anchorCorrects(vs, container), false, 'no anchor correction mid-scroll')
    container.finishSmooth()
    assert.equal(await endsAtOnce(done), true, 'the at-target scrollend')
    await afterFrame()
    assert.equal(anchorCorrects(vs, container), true, 'released at the end')
})

test('smooth: a wheel ends it; a height change above the viewport then corrects the anchor', async () => {
    const { vs, container, done } = await smoothHalfway()
    container.dispatchEvent({ type: 'wheel' })
    assert.equal(await endsAtOnce(done), true)
    placeAt(vs, container, NEAR_FROM)
    assert.equal(anchorCorrects(vs, container), true)
    // Row 17 (622..658 after the growth) sits in the zone at the destination, not here.
    assert.equal(revealWrites(vs, container, 17), true, 'the destination is cleared')
})

test('smooth: an instant write (setScrollTop) during it clears the destination and the hold', async () => {
    const { vs, container } = await smoothHalfway()
    vs.setScrollTop(200)
    assert.deepEqual(container.writes, [200])
    assert.equal(anchorCorrects(vs, container), true, 'the hold is released')
    // Row 17 (622..658 after the growth) sits in the zone at the destination, not at 210.
    assert.equal(revealWrites(vs, container, 17), true, 'the destination is cleared')
})

for (const [name, end] of [
    ['a pointerdown on the container itself', ({ container }) => container.dispatchEvent({ type: 'pointerdown' })],
    ['a non-prevented Space keydown', ({ container }) => container.dispatchEvent({ type: 'keydown', key: ' ', defaultPrevented: false })],
    ['scrollToIndex start', ({ vs }) => vs.scrollToIndex(5, { align: 'start' })],
    ['scrollToTop', ({ vs }) => vs.scrollToTop()],
    ['scrollToBottom', ({ vs }) => vs.scrollToBottom()],
    ['scrollToAnchor', ({ vs }) => vs.scrollToAnchor({ index: 5, key: 's5', offset: 0 })],
    ['writeAnchor (a resume)', ({ vs }) => { vs.suspend(); vs.resume() }],
]) {
    test(`smooth: ${name} ends it, the destination cleared and the hold released`, async () => {
        const ctx = await smoothHalfway()
        const { vs, container, done } = ctx
        end(ctx)
        assert.equal(await endsAtOnce(done), true, 'done resolved by the ending')
        placeAt(vs, container, NEAR_FROM)
        assert.equal(anchorCorrects(vs, container), true, 'the hold is released')
        // Row 0 grew by 10: row 17 (622..658) sits in the zone at the destination
        // (480.875..684), not at 370 (441..644.125). Were the destination kept, no write.
        assert.equal(revealWrites(vs, container, 17), true, 'the destination is cleared')
    })
}

test('smooth: a second smooth reveal during the first ends the first; the older end never clears the newer hold', async () => {
    const { vs, container, done } = await smoothHalfway()
    // Row 22 (792..828): target 553.875 from the destination 409.875, a 144px smooth move.
    assert.ok(vs.scrollToIndex(22, NEAR) instanceof Promise)
    assert.equal(await endsAtOnce(done), true, 'the first ended by the second')
    await delay(1)
    assert.equal(revealWrites(vs, container, 22), false, 'the newer destination kept')
    assert.equal(anchorCorrects(vs, container), false, 'the newer hold kept after the older end')
})

test('smooth: a second no-write reveal during it, heights change after the scrollend → corrected after the end', async () => {
    const { vs, container } = await nearSetup()
    const first = vs.scrollToKey('s18', { ...NEAR, settleMs: SETTLE_MS })
    await afterFrame()
    // Row 17 (612..648) sits in the zone at the destination (480.875..684): no write.
    const second = vs.scrollToKey('s17', { ...NEAR, settleMs: SETTLE_MS })
    assert.equal(container.scrollToCalls.length, 1)
    assert.deepEqual(container.writes, [])
    await delay(SETTLE_MS * 3)
    assert.equal(container.scrollToCalls.length, 1, 'the second waits for the end')
    container.finishSmooth()
    await delay(1)
    // Rows between the first visible one (11) and row 17 grow: the anchor keeps row 11,
    // row 17 drifts under the zone.
    grow(vs, 12, 16, 60)
    await delay(SETTLE_MS * 3)
    assert.equal(container.scrollToCalls.length, 2, 'the second reveal re-aligns its row')
    assert.equal(container.scrollToCalls[1].top, bottomTarget(vs, container, 17))
    container.finishSmooth()
    assert.equal(await second, true)
    assert.equal(await first, false)
})
