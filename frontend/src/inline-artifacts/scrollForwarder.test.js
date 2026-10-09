import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canChainVertically, allowsVerticalPan, releaseVelocity, createInlineScrollForwarder } from './scrollForwarder.js'

const el = (props = {}, parent = null) => ({ nodeType: 1, parentElement: parent, scrollTop: 0, scrollHeight: 0,
    clientHeight: 0, style: {}, ...props })

/** A document whose html/body hold the viewport scroll, plus computed styles per element. */
function page({ rootScrollHeight = 500, viewport = 500, rootTop = 0, htmlStyle = {}, bodyStyle = {} } = {}) {
    const root = el({ scrollHeight: rootScrollHeight, clientHeight: viewport, scrollTop: rootTop, style: htmlStyle })
    const body = el({ style: bodyStyle }, root)
    const document = { documentElement: root, body, scrollingElement: root }
    const window = { getComputedStyle: element => ({ overflowY: 'visible', overscrollBehaviorY: 'auto', touchAction: 'auto',
        ...element.style }) }
    return { document, window, root, body }
}

test('a page that does not scroll chains both ways; room in the page consumes the gesture', () => {
    const p = page()
    assert.equal(canChainVertically(p.window, p.document, p.body, 1), true)
    assert.equal(canChainVertically(p.window, p.document, p.body, -1), true)
    const long = page({ rootScrollHeight: 2000 })
    assert.equal(canChainVertically(long.window, long.document, long.body, 1), false)
    assert.equal(canChainVertically(long.window, long.document, long.body, -1), true, 'already at the top')
    const bottom = page({ rootScrollHeight: 2000, rootTop: 1500 })
    assert.equal(canChainVertically(bottom.window, bottom.document, bottom.body, 1), true)
    assert.equal(canChainVertically(bottom.window, bottom.document, bottom.body, -1), false)
})

test('an inner scroller consumes until its edge, then chains; overscroll-behavior stops the chain', () => {
    const p = page()
    const list = el({ scrollHeight: 1000, clientHeight: 200, scrollTop: 100, style: { overflowY: 'auto' } }, p.body)
    const item = el({}, list)
    assert.equal(canChainVertically(p.window, p.document, item, 1), false)
    assert.equal(canChainVertically(p.window, p.document, item, -1), false)
    list.scrollTop = 800
    assert.equal(canChainVertically(p.window, p.document, item, 1), true)
    list.scrollTop = 0
    assert.equal(canChainVertically(p.window, p.document, item, -1), true)
    list.style.overscrollBehaviorY = 'contain'
    assert.equal(canChainVertically(p.window, p.document, item, -1), false)
    const hidden = el({ scrollHeight: 1000, clientHeight: 200, style: { overflowY: 'hidden' } }, p.body)
    assert.equal(canChainVertically(p.window, p.document, hidden, 1), true)
})

test('hidden viewport overflow (on html, or propagated from body) does not consume', () => {
    const fromHtml = page({ rootScrollHeight: 2000, htmlStyle: { overflowY: 'hidden' } })
    assert.equal(canChainVertically(fromHtml.window, fromHtml.document, fromHtml.body, 1), true)
    const fromBody = page({ rootScrollHeight: 2000, bodyStyle: { overflowY: 'hidden' } })
    assert.equal(canChainVertically(fromBody.window, fromBody.document, fromBody.body, 1), true)
})

test('touch-action that forbids vertical panning keeps the gesture in the artifact', () => {
    const p = page()
    const canvas = el({ style: { touchAction: 'none' } }, p.body)
    const slider = el({ style: { touchAction: 'pan-x' } }, p.body)
    const ok = el({ style: { touchAction: 'pan-y pinch-zoom' } }, p.body)
    assert.equal(allowsVerticalPan(p.window, canvas), false)
    assert.equal(allowsVerticalPan(p.window, el({}, canvas)), false)
    assert.equal(allowsVerticalPan(p.window, slider), false)
    assert.equal(allowsVerticalPan(p.window, ok), true)
    assert.equal(allowsVerticalPan(p.window, p.body), true)
})

test('release velocity uses only the last 100 ms and is zero after a pause', () => {
    const samples = [{ time: 0, delta: 50 }, { time: 920, delta: 10 }, { time: 940, delta: 20 }, { time: 960, delta: 20 }]
    assert.equal(releaseVelocity(samples, 1000), 40 / 40)
    assert.equal(releaseVelocity(samples, 2000), 0)
    assert.equal(releaseVelocity([{ time: 990, delta: 30 }], 1000), 0)
})

function forwarderFixture(pageOptions) {
    const p = page(pageOptions)
    const listeners = new Map()
    const win = { ...p.window, addEventListener: (type, fn, options) => listeners.set(type, { fn, options }),
        removeEventListener: type => listeners.delete(type) }
    const sent = []
    const forwarder = createInlineScrollForwarder({ document: p.document, window: win, inlineGeneration: 3,
        getHost: async () => ({ forwardInlineScroll: async report => sent.push(report) }) })
    const fire = (type, event) => {
        const full = { type, defaultPrevented: false, cancelable: true, timeStamp: 0, prevented: false,
            preventDefault() { this.prevented = true }, ...event }
        listeners.get(type).fn(full)
        return full
    }
    const settle = () => new Promise(resolve => setTimeout(resolve, 0))
    return { p, listeners, sent, forwarder, fire, settle }
}

test('a wheel nothing in the artifact consumes is relayed and kept from chaining elsewhere', async () => {
    const f = forwarderFixture()
    const event = f.fire('wheel', { target: f.p.body, deltaY: 100, deltaX: 0, deltaMode: 0 })
    await f.settle()
    assert.equal(event.prevented, true)
    assert.deepEqual(f.sent, [{ inlineGeneration: 3, kind: 'wheel', deltaY: 100, deltaMode: 0 }])
})

test('wheels the artifact handles or that are not plain vertical scrolls are left alone', async () => {
    const f = forwarderFixture({ rootScrollHeight: 2000 })
    f.fire('wheel', { target: f.p.body, deltaY: 100, deltaX: 0, deltaMode: 0 }) // page has room
    const idle = forwarderFixture()
    idle.fire('wheel', { target: idle.p.body, deltaY: 100, deltaX: 0, deltaMode: 0, defaultPrevented: true })
    idle.fire('wheel', { target: idle.p.body, deltaY: 100, deltaX: 0, deltaMode: 0, ctrlKey: true })
    idle.fire('wheel', { target: idle.p.body, deltaY: 10, deltaX: 80, deltaMode: 0 })
    idle.fire('wheel', { target: idle.p.body, deltaY: 0, deltaX: 0, deltaMode: 0 })
    await Promise.all([f.settle(), idle.settle()])
    assert.deepEqual(f.sent, [])
    assert.deepEqual(idle.sent, [])
})

test('a vertical pan on a non-scrolling artifact is relayed as moves and a fling', async () => {
    const f = forwarderFixture()
    const touch = y => ({ touches: [{ clientX: 10, clientY: y }] })
    f.fire('touchstart', { target: f.p.body, ...touch(300) })
    const tiny = f.fire('touchmove', { ...touch(298), timeStamp: 10 })
    assert.equal(tiny.prevented, false, 'a tap-sized jitter is not a scroll')
    f.fire('touchmove', { ...touch(280), timeStamp: 20 }) // leaves the slop: the decision point
    f.fire('touchmove', { ...touch(260), timeStamp: 40 })
    const last = f.fire('touchmove', { ...touch(240), timeStamp: 60 })
    assert.equal(last.prevented, true)
    f.fire('touchend', { touches: [], timeStamp: 70 })
    await f.settle()
    assert.deepEqual(f.sent.map(({ kind, deltaY }) => [kind, deltaY]), [
        ['touchstart', undefined], ['touchmove', 20], ['touchmove', 20], ['touchend', undefined] ])
    assert.equal(f.sent.at(-1).velocity, 1) // 40px over 40ms
})

test('taps, horizontal pans, scrollable pages and multi-touch stay native', async () => {
    const touch = (x, y) => ({ touches: [{ clientX: x, clientY: y }] })
    const tap = forwarderFixture()
    tap.fire('touchstart', { target: tap.p.body, ...touch(5, 5) })
    tap.fire('touchmove', { ...touch(6, 7) })
    tap.fire('touchend', { touches: [], timeStamp: 10 })
    const horizontal = forwarderFixture()
    horizontal.fire('touchstart', { target: horizontal.p.body, ...touch(5, 5) })
    const move = horizontal.fire('touchmove', { ...touch(60, 12) })
    horizontal.fire('touchend', { touches: [], timeStamp: 10 })
    assert.equal(move.prevented, false)
    const scrollable = forwarderFixture({ rootScrollHeight: 2000 })
    scrollable.fire('touchstart', { target: scrollable.p.body, ...touch(5, 100) })
    const down = scrollable.fire('touchmove', { ...touch(5, 40) }) // content would scroll down: it has room
    scrollable.fire('touchend', { touches: [], timeStamp: 10 })
    assert.equal(down.prevented, false)
    const pinch = forwarderFixture()
    pinch.fire('touchstart', { target: pinch.p.body, touches: [{ clientX: 1, clientY: 1 }, { clientX: 9, clientY: 9 }] })
    const pinched = pinch.fire('touchmove', { touches: [{ clientX: 1, clientY: 1 }, { clientX: 9, clientY: 60 }] })
    assert.equal(pinched.prevented, false)
    await Promise.all([tap.settle(), horizontal.settle(), scrollable.settle(), pinch.settle()])
    assert.deepEqual(tap.sent.map(r => r.kind), ['touchstart'])
    assert.deepEqual(horizontal.sent.map(r => r.kind), ['touchstart'])
    assert.deepEqual(scrollable.sent.map(r => r.kind), ['touchstart'])
    assert.deepEqual(pinch.sent, [])
})

test('the relay decision holds for the whole gesture, and a cancel stops without a fling', async () => {
    const f = forwarderFixture({ rootScrollHeight: 900, viewport: 500 })
    const touch = y => ({ touches: [{ clientX: 10, clientY: y }] })
    // Finger moving down scrolls content up: the page is at the top, so the chat takes it.
    f.fire('touchstart', { target: f.p.body, ...touch(100) })
    f.fire('touchmove', { ...touch(130), timeStamp: 10 })
    f.p.root.scrollTop = 200 // would now have room, but the gesture is already relayed
    f.fire('touchmove', { ...touch(160), timeStamp: 20 })
    f.fire('touchcancel', { touches: [], timeStamp: 30 })
    await f.settle()
    assert.deepEqual(f.sent.map(r => r.kind), ['touchstart', 'touchmove', 'touchend'])
    assert.equal(f.sent.at(-1).velocity, 0)
})

test('without a reload generation nothing is installed; dispose removes every listener', () => {
    const none = createInlineScrollForwarder({ document: {}, window: {}, getHost: () => {}, inlineGeneration: null })
    none.dispose()
    const f = forwarderFixture()
    assert.equal(f.listeners.size, 5)
    assert.equal(f.listeners.get('wheel').options.passive, false)
    assert.equal(f.listeners.get('touchmove').options.passive, false)
    f.forwarder.dispose()
    assert.equal(f.listeners.size, 0)
})
