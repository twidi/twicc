// Run with: node --test src/utils/glideInk.test.js (from the frontend dir)
// Gliding indicators (visual refresh step 4c, docs/plans/2026-09-28-gliding-indicators-design.md):
// the pure helpers, then the controller driven through a fake environment.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { createGlideInk, nearBox, placementMode, relativeBox, sameBox, scaleRatio } from './glideInk.js'

const PROPS = ['--glide-x', '--glide-y', '--glide-w', '--glide-h']

/** Box equality with a 1e-9 tolerance (scaled boxes go through a division). */
function assertBox(actual, expected) {
    for (const key of ['x', 'y', 'w', 'h']) {
        assert.ok(Math.abs(actual[key] - expected[key]) < 1e-9, `${key}: ${actual[key]} != ${expected[key]}`)
    }
}

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height })

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

test('scaleRatio: rounding is not a scale', () => {
    assert.equal(scaleRatio(812.4, 812), 1)
    assert.equal(scaleRatio(31.5, 32), 1)
    assert.equal(scaleRatio(640, 800), 0.8)
    assert.equal(scaleRatio(640, 0), 1)
    assert.equal(scaleRatio(0, 800), 1)
})

test('relativeBox: plain, scrolled, with borders', () => {
    const container = rect(100, 50, 400, 300)
    const layout = { offsetWidth: 400, offsetHeight: 300 }
    assert.deepEqual(relativeBox(rect(110, 80, 200, 30), container, layout), { x: 10, y: 30, w: 200, h: 30 })
    assert.deepEqual(
        relativeBox(rect(110, 80, 200, 30), container, { ...layout, scrollTop: 120, scrollLeft: 7 }),
        { x: 17, y: 150, w: 200, h: 30 },
    )
    assert.deepEqual(
        relativeBox(rect(110, 80, 200, 30), container, { ...layout, clientTop: 2, clientLeft: 3 }),
        { x: 7, y: 28, w: 200, h: 30 },
    )
})

test('relativeBox: a scaled container gives the unscaled layout box', () => {
    const unscaled = relativeBox(rect(110, 80, 200, 30), rect(100, 50, 400, 300),
        { offsetWidth: 400, offsetHeight: 300, scrollTop: 10, clientTop: 1 })
    // The same container at scale 0.8 (visual rects shrunk around its origin).
    const scaled = relativeBox(rect(100 + 10 * 0.8, 50 + 30 * 0.8, 160, 24), rect(100, 50, 320, 240),
        { offsetWidth: 400, offsetHeight: 300, scrollTop: 10, clientTop: 1 })
    assertBox(scaled, unscaled)
})

test('relativeBox: no layout size → ratio 1; rounding → exact box', () => {
    assert.deepEqual(relativeBox(rect(10, 10, 50, 20), rect(0, 0, 320, 240)), { x: 10, y: 10, w: 50, h: 20 })
    assert.deepEqual(
        relativeBox(rect(12.4, 3.5, 100.2, 24.5), rect(2, 1, 812.4, 31.5), { offsetWidth: 812, offsetHeight: 32 }),
        { x: 12.4 - 2, y: 2.5, w: 100.2, h: 24.5 },
    )
})

test('relativeBox: one ratio for both axes, from the longer axis', () => {
    // 400 × 32 at scale 0.98: the short axis alone (31.36 vs 32) would read as rounding.
    const box = relativeBox(rect(9.8, 0.98, 98, 29.4), rect(0, 0, 392, 31.36), { offsetWidth: 400, offsetHeight: 32 })
    assertBox(box, { x: 10, y: 1, w: 100, h: 30 })
    // Taller than wide: the ratio comes from the height.
    const tall = relativeBox(rect(0, 49, 29.4, 98), rect(0, 0, 31.36, 392), { offsetWidth: 32, offsetHeight: 400 })
    assertBox(tall, { x: 0, y: 50, w: 30, h: 100 })
})

test('placementMode: every row of the table', () => {
    const a = { id: 'a' }
    const b = { id: 'b' }
    const shown = { ready: true, active: a, resetKey: null }
    assert.equal(placementMode(shown, { visible: false, active: b, resetKey: null }), 'hide')
    assert.equal(placementMode(shown, { visible: true, active: null, resetKey: null }), 'hide')
    assert.equal(placementMode({ ready: false, active: null, resetKey: null }, { visible: true, active: a, resetKey: null }), 'snap')
    assert.equal(placementMode(shown, { visible: true, active: a, resetKey: null }), 'snap')
    assert.equal(placementMode({ ...shown, resetKey: 'q' }, { visible: true, active: a, resetKey: 'qq' }), 'snap')
    assert.equal(placementMode(shown, { visible: true, active: b, resetKey: null }), 'glide')
    // A reset key change with a new active element snaps.
    assert.equal(placementMode({ ...shown, resetKey: 'q' }, { visible: true, active: b, resetKey: 'qq' }), 'snap')
    // Object.is: NaN equals NaN, objects compare by identity.
    assert.equal(placementMode({ ...shown, resetKey: NaN }, { visible: true, active: b, resetKey: NaN }), 'glide')
    const list = []
    assert.equal(placementMode({ ...shown, resetKey: list }, { visible: true, active: b, resetKey: list }), 'glide')
    assert.equal(placementMode({ ...shown, resetKey: list }, { visible: true, active: b, resetKey: [] }), 'snap')
    assert.equal(placementMode({ ...shown, resetKey: 0 }, { visible: true, active: b, resetKey: -0 }), 'snap')
})

test('sameBox', () => {
    const box = { x: 1, y: 24, w: 100, h: 30 }
    assert.equal(sameBox(box, { ...box }), true)
    assert.equal(sameBox(box, { ...box, y: 24.000000000000004 }), true)
    assert.equal(sameBox(box, { ...box, w: 100.5 }), false)
    assert.equal(sameBox(box, null), false)
    assert.equal(sameBox(null, box), false)
    assert.equal(sameBox(null, null), true)
})

test('nearBox: every value within the tolerance', () => {
    const box = { x: 1, y: 24, w: 100, h: 30 }
    assert.equal(nearBox(box, { ...box }, 0.05), true)
    assert.equal(nearBox(box, { ...box, y: 24.016 }, 0.05), true)
    assert.equal(nearBox(box, { ...box, x: 0.96, h: 30.04 }, 0.05), true)
    assert.equal(nearBox(box, { ...box, y: 24.1 }, 0.05), false)
    assert.equal(nearBox(box, { ...box, w: 99.9 }, 0.05), false)
    assert.equal(nearBox(box, null, 0.05), false)
    assert.equal(nearBox(null, box, 0.05), false)
    assert.equal(nearBox(null, null, 0.05), true)
})

test('createGlideInk reads globalThis when no env is given', () => {
    const source = readFileSync(new URL('./glideInk.js', import.meta.url), 'utf8')
    assert.match(source, /env = globalThis/)
})

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

function makeEnv() {
    const env = {
        log: [],
        timers: [],
        nextId: 1,
        frames: [],
        observers: [],
        dur1: '120ms',
    }
    env.setTimeout = (callback, delay) => {
        const id = env.nextId++
        env.timers.push({ id, callback, delay })
        return id
    }
    env.clearTimeout = (id) => {
        env.timers = env.timers.filter((t) => t.id !== id)
    }
    env.requestAnimationFrame = (callback) => {
        const id = env.nextId++
        env.frames.push({ id, callback })
        return id
    }
    env.cancelAnimationFrame = (id) => {
        env.frames = env.frames.filter((f) => f.id !== id)
    }
    env.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback
            this.targets = new Set()
            this.disconnected = false
            env.observers.push(this)
        }
        observe(target) { this.targets.add(target) }
        unobserve(target) { this.targets.delete(target) }
        disconnect() { this.targets.clear(); this.disconnected = true }
    }
    env.getComputedStyle = (element, pseudo) => ({
        getPropertyValue(name) {
            env.log.push(['style', element.name, pseudo ?? null, name])
            return name === '--motion-dur-1' ? env.dur1 : ''
        },
    })
    return env
}

function makeElement(env, name, box = rect(0, 0, 0, 0), layout = {}) {
    const el = {
        name,
        rect: box,
        isConnected: true,
        scrollLeft: 0,
        scrollTop: 0,
        clientLeft: 0,
        clientTop: 0,
        offsetWidth: Math.round(box.width),
        offsetHeight: Math.round(box.height),
        ...layout,
        attrs: new Set(),
        props: new Map(),
        getBoundingClientRect() { return { ...this.rect } },
        setAttribute(attr) { env.log.push(['setAttribute', name, attr]); this.attrs.add(attr) },
        removeAttribute(attr) { env.log.push(['removeAttribute', name, attr]); this.attrs.delete(attr) },
        hasAttribute(attr) { return this.attrs.has(attr) },
    }
    el.style = {
        setProperty(prop, value) { env.log.push(['set', name, prop, value]); el.props.set(prop, value) },
        removeProperty(prop) { env.log.push(['remove', name, prop]); el.props.delete(prop) },
    }
    return el
}

/** A container 400 × 300 at (100, 50), its ink, three rows of 30px. */
function makeList(env = makeEnv(), { getActiveOffset } = {}) {
    const container = makeElement(env, 'container', rect(100, 50, 400, 300))
    const ink = makeElement(env, 'ink')
    const rows = [0, 1, 2].map((i) => makeElement(env, `row${i}`, rect(100, 50 + i * 30, 400, 30)))
    const site = { env, container, ink, rows, active: rows[0], resetKey: null, items: rows }
    site.controller = createGlideInk({
        container,
        flushTarget: ink,
        getActive: () => site.active,
        getItems: () => site.items,
        getResetKey: () => site.resetKey,
        ...(getActiveOffset ? { getActiveOffset } : {}),
        env,
    })
    return site
}

const values = (el) => PROPS.map((p) => el.props.get(p))
const fired = (env, ...targets) => {
    const observer = env.observers[0]
    observer.callback(targets.map((target) => ({ target })), observer)
}
const flushes = (env) => env.log.filter((e) => e[0] === 'style' && e[3] === 'translate')
const runTimers = (env) => {
    const due = env.timers
    env.timers = []
    for (const t of due) t.callback()
}
/** Run the queued animation frames with this timestamp (not the ones they queue). */
function frame(env, timestamp = 0) {
    const queued = env.frames
    env.frames = []
    for (const f of queued) f.callback(timestamp)
}

/** The snap sequence on the container: instant, the four writes, ready, flush, instant removed. */
function assertSnapSequence(log, expected, pseudo = null, flushName = 'ink') {
    const relevant = log.filter((e) => e[0] !== 'style' || e[3] === 'translate')
    assert.deepEqual(relevant, [
        ['setAttribute', 'container', 'data-glide-instant'],
        ...PROPS.map((p, i) => ['set', 'container', p, `${expected[i]}px`]),
        ['setAttribute', 'container', 'data-glide-ready'],
        ['style', flushName, pseudo, 'translate'],
        ['removeAttribute', 'container', 'data-glide-instant'],
    ])
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

test('creation writes 0px, then runs the first update itself: a snap', () => {
    const { env, container } = makeList()
    const zero = env.log.slice(0, 4)
    assert.deepEqual(zero, PROPS.map((p) => ['set', 'container', p, '0px']))
    assertSnapSequence(env.log.slice(4), [0, 0, 400, 30])
    assert.ok(container.attrs.has('data-glide-ready'))
    assert.ok(!container.attrs.has('data-glide-instant'))
})

test('flushPseudo and a separate target are passed through', () => {
    const env = makeEnv()
    const host = makeElement(env, 'container')
    const tabs = makeElement(env, 'tabs', rect(0, 0, 300, 32))
    const tab = makeElement(env, 'tab', rect(40, 0, 80, 32))
    createGlideInk({ container: tabs, target: host, flushTarget: tabs, flushPseudo: '::after', getActive: () => tab, env })
    assertSnapSequence(env.log.slice(4), [40, 0, 80, 32], '::after', 'tabs')
    assert.equal(tabs.props.size, 0, 'the container itself gets nothing')
})

test('a new active element glides: writes only, no instant, no flush', () => {
    const site = makeList()
    const { env, container, rows } = site
    env.log = []
    site.active = rows[2]
    site.controller.update()
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), PROPS.map((p, i) => ['set', 'container', p, `${[0, 60, 400, 30][i]}px`]))
    assert.equal(flushes(env).length, 0)
    assert.ok(container.attrs.has('data-glide-ready'))
})

test('the same active element with a new rect snaps', () => {
    const site = makeList()
    const { env, rows } = site
    env.log = []
    rows[0].rect = rect(100, 55, 400, 40)
    site.controller.update()
    assertSnapSequence(env.log, [0, 5, 400, 40])
})

test('a reset key change with a new active element snaps', () => {
    const site = makeList()
    const { env, rows } = site
    env.log = []
    site.active = rows[1]
    site.resetKey = 'query'
    site.controller.update()
    assertSnapSequence(env.log, [0, 30, 400, 30])
})

test('a reset key change on the same element at the same box writes nothing; the next move glides', () => {
    const site = makeList()
    const { env, container, rows } = site
    env.log = []
    site.resetKey = 'query'
    site.controller.update()
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), [], 'nothing written')
    // The key was still recorded: another element under that key glides.
    site.active = rows[1]
    site.controller.update()
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), PROPS.map((p, i) => ['set', 'container', p, `${[0, 30, 400, 30][i]}px`]))
    assert.equal(flushes(env).length, 0, 'a glide: no flush')
    assert.ok(!env.log.some((e) => e[2] === 'data-glide-instant'))
    assert.ok(container.attrs.has('data-glide-ready'))
})

test('getActiveOffset is subtracted from the measured box; without it, the box is unchanged', () => {
    const offset = { x: 0, y: 6 }
    const site = makeList(makeEnv(), { getActiveOffset: () => offset })
    // rows[0] is at y 0 in the container: measured 6px low, it is written 6px higher.
    assert.deepEqual(values(site.container), ['0px', '-6px', '400px', '30px'])
    const calls = []
    const other = makeList(makeEnv(), { getActiveOffset: (active) => { calls.push(active); return { x: 2, y: 0 } } })
    assert.deepEqual(values(other.container), ['-2px', '0px', '400px', '30px'])
    assert.deepEqual(calls, [other.rows[0]], 'called with the active element')

    const plain = makeList()
    assert.deepEqual(values(plain.container), ['0px', '0px', '400px', '30px'])
})

test('the same element re-measured within 0.05px writes nothing; 0.1px away still snaps', () => {
    const site = makeList()
    const { env, rows } = site
    env.log = []
    rows[0].rect = rect(100, 50.016, 400, 30)
    site.controller.update()
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), [], 'no instant, no property write')
    assert.equal(flushes(env).length, 0)

    rows[0].rect = rect(100, 50.1, 400, 30)
    site.controller.update()
    assertSnapSequence(env.log, [0, 50.1 - 50, 400, 30])
})

test('no creep: successive sub-0.05px measures compare against the written box', () => {
    const site = makeList()
    const { env, rows } = site
    const at = (dy) => {
        env.log = []
        rows[0].rect = rect(100, 50 + dy, 400, 30)
        site.controller.update()
        return env.log.filter((e) => e[0] !== 'style')
    }
    assert.deepEqual(at(0.03), [], '+0.03 from the written 0: nothing')
    const second = at(0.06)
    assert.ok(second.some((e) => e[0] === 'setAttribute' && e[2] === 'data-glide-instant'), '+0.06 from the written 0: a snap')
    assert.deepEqual(second.filter((e) => e[0] === 'set').map((e) => e[2]), PROPS)
    assert.deepEqual(at(0.09), [], '+0.03 from the written 0.06: nothing')
})

test('the 0.05px tolerance is for the same element only: another element that close glides', () => {
    const site = makeList()
    const { env, container, rows } = site
    env.log = []
    rows[1].rect = rect(100, 50.03, 400, 30)
    site.active = rows[1]
    site.controller.update()
    assert.deepEqual(env.log.filter((e) => e[0] === 'set').map((e) => e[2]), PROPS, 'the box is written')
    assert.equal(flushes(env).length, 0, 'a glide: no flush')
    assert.ok(!env.log.some((e) => e[2] === 'data-glide-instant'))
    assert.ok(container.attrs.has('data-glide-ready'))
})

test('the active element is recorded even when nothing is written', () => {
    const site = makeList()
    const { env, rows } = site
    // Another element at the very same box: a glide with nothing to write.
    rows[1].rect = rect(100, 50, 400, 30)
    site.active = rows[1]
    site.controller.update()
    // That element moves: the same element with a new box snaps (it was recorded).
    env.log = []
    rows[1].rect = rect(100, 55, 400, 30)
    site.controller.update()
    assertSnapSequence(env.log, [0, 5, 400, 30])
})

test('no active element: hide at once, collapse after --motion-dur-1, then snap again', () => {
    const site = makeList()
    const { env, container, rows } = site
    env.log = []
    site.active = null
    site.controller.update()
    assert.ok(!container.attrs.has('data-glide-ready'))
    assert.deepEqual(values(container), ['0px', '0px', '400px', '30px'], 'the box stays for the fade')
    assert.equal(env.timers.length, 1)
    assert.equal(env.timers[0].delay, 120)
    runTimers(env)
    assert.deepEqual(values(container), ['0px', '0px', '0px', '0px'])

    // Back on the same row, at the same box: a hide forgot the box, so this snaps too.
    env.log = []
    site.active = rows[0]
    site.controller.update()
    assertSnapSequence(env.log, [0, 0, 400, 30])
})

test('the collapse delay is read from the target', () => {
    const env = makeEnv()
    env.dur1 = '0.2s'
    const site = makeList(env)
    site.active = null
    site.controller.update()
    assert.equal(env.timers[0].delay, 200)
    assert.ok(env.log.some((e) => e[0] === 'style' && e[1] === 'container' && e[3] === '--motion-dur-1'))
})

test('a placement before the collapse cancels it', () => {
    const site = makeList()
    const { env, container, rows } = site
    site.active = null
    site.controller.update()
    site.active = rows[2]
    site.controller.update()
    assert.equal(env.timers.length, 0)
    runTimers(env)
    assert.deepEqual(values(container), ['0px', '60px', '400px', '30px'])
})

test('a zero-size container hides and collapses at once; a zero-size active element hides', () => {
    const site = makeList()
    const { env, container } = site
    container.rect = rect(100, 50, 0, 0)
    site.controller.update()
    assert.ok(!container.attrs.has('data-glide-ready'))
    assert.equal(env.timers.length, 0)
    assert.deepEqual(values(container), ['0px', '0px', '0px', '0px'])

    const other = makeList()
    other.rows[1].rect = rect(100, 80, 0, 0)
    other.active = other.rows[1]
    other.controller.update()
    assert.ok(!other.container.attrs.has('data-glide-ready'))
    assert.equal(other.env.timers.length, 1, 'visible container: the collapse waits for the fade')

    const detached = makeList()
    detached.container.isConnected = false
    detached.controller.update()
    assert.ok(!detached.container.attrs.has('data-glide-ready'))
})

test('created hidden: the container is observed, a resize places the ink', () => {
    const env = makeEnv()
    const container = makeElement(env, 'container', rect(0, 0, 0, 0))
    const ink = makeElement(env, 'ink')
    const row = makeElement(env, 'row', rect(0, 0, 0, 0))
    createGlideInk({ container, flushTarget: ink, getActive: () => row, env })
    assert.ok(env.observers[0].targets.has(container))
    assert.ok(!container.attrs.has('data-glide-ready'))

    container.rect = rect(10, 20, 300, 200)
    row.rect = rect(10, 20, 300, 30)
    env.log = []
    fired(env, container)
    assertSnapSequence(env.log, [0, 0, 300, 30])
})

test('an unchanged box writes nothing and does not flush', () => {
    const site = makeList()
    const { env } = site
    env.log = []
    site.controller.update()
    fired(env, site.container)
    assert.deepEqual(env.log, [])
})

test('the observer follows the active element and the items', () => {
    const site = makeList()
    const { env, container, rows } = site
    const observer = env.observers[0]
    assert.deepEqual([...observer.targets].sort((a, b) => a.name.localeCompare(b.name)).map((e) => e.name),
        ['container', 'row0', 'row1', 'row2'])

    // A separate active element (not an item) is followed.
    const extra = makeElement(env, 'extra', rect(100, 200, 400, 30))
    site.active = extra
    site.controller.update()
    assert.ok(observer.targets.has(extra))
    site.active = rows[1]
    site.controller.update()
    assert.ok(!observer.targets.has(extra), 'old active element unobserved')

    // An item dropped from the list is unobserved.
    site.items = [rows[0], rows[1]]
    site.controller.update()
    assert.ok(!observer.targets.has(rows[2]))
    assert.ok(observer.targets.has(container))

    // A fired resize calls update(): the moved active element snaps.
    rows[1].rect = rect(100, 90, 400, 30)
    env.log = []
    fired(env, rows[1])
    assertSnapSequence(env.log, [0, 40, 400, 30])
})

test('an item above the active one resized: the active element snaps to its new place', () => {
    const site = makeList()
    const { env, rows } = site
    site.active = rows[2]
    site.controller.update()
    rows[1].rect = rect(100, 80, 400, 50)
    rows[2].rect = rect(100, 130, 400, 30)
    env.log = []
    fired(env, rows[1])
    assertSnapSequence(env.log, [0, 80, 400, 30])
})

test('destroy: timer, frame and observer cleared, attributes and properties removed', () => {
    const site = makeList()
    const { env, container } = site
    site.active = null
    site.controller.update()
    assert.equal(env.timers.length, 1)
    site.controller.destroy()
    assert.equal(env.timers.length, 0)
    assert.ok(env.observers[0].disconnected)
    assert.equal(container.attrs.size, 0)
    assert.equal(container.props.size, 0)
})

// ---------------------------------------------------------------------------
// Settle after a scale
// ---------------------------------------------------------------------------

/** A list whose container opens at scale 0.8 (layout 800 × 400). */
function makeScaledList() {
    const env = makeEnv()
    const container = makeElement(env, 'container', rect(0, 0, 640, 320), { offsetWidth: 800, offsetHeight: 400 })
    const ink = makeElement(env, 'ink')
    const rows = [0, 1].map((i) => makeElement(env, `row${i}`, rect(0, i * 24, 640, 24)))
    const site = { env, container, ink, rows, active: rows[0] }
    site.controller = createGlideInk({ container, flushTarget: ink, getActive: () => site.active, env })
    return site
}

test('settle: one loop per scale, frames only read', () => {
    const site = makeScaledList()
    const { env, container, rows } = site
    assert.equal(env.frames.length, 1, 'the first measure starts a loop')
    assertBox({ x: 0, y: 0, w: +container.props.get('--glide-w').slice(0, -2), h: +container.props.get('--glide-h').slice(0, -2) },
        { x: 0, y: 0, w: 800, h: 30 })
    site.active = rows[1]
    site.controller.update()
    assert.equal(env.frames.length, 1, 'a second measure does not start another')

    env.log = []
    container.rect = rect(0, 0, 700, 350)
    frame(env, 0)
    container.rect = rect(0, 0, 799.6, 399.8)
    frame(env, 16)
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), [], 'frames write nothing')
    assert.equal(env.frames.length, 1, 'within 0.5px but still moving: the loop goes on')

    // The final scale: equal to the previous frame's rect → stop, one settle update.
    container.rect = rect(0, 0, 799.6, 399.8)
    rows[1].rect = rect(0, 30 * 799.6 / 800, 799.6, 30 * 799.6 / 800)
    frame(env, 32)
    assert.equal(env.frames.length, 0, 'stopped')
    const writes = env.log.filter((e) => e[0] !== 'style')
    assert.deepEqual(writes.map((e) => e.slice(0, 3)), PROPS.map((p) => ['set', 'container', p]), 'written like a glide')
    assert.equal(flushes(env).length, 0)
})

test('settle: a settle update of the same element writes without instant or flush', () => {
    const site = makeScaledList()
    const { env, container, rows } = site
    // Scale ends: layout size reached, then stable; the row ends 0.3px taller than measured.
    container.rect = rect(0, 0, 800, 400)
    rows[0].rect = rect(0, 0, 800, 30.3)
    env.log = []
    frame(env, 0)
    assert.equal(env.frames.length, 1, 'frame 1 differs from the starting measure: goes on')
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), [], 'frames write nothing')
    frame(env, 16)
    assert.equal(env.frames.length, 0, 'frame 2 equals frame 1 at the layout size: stops (before the fallback)')
    assert.deepEqual(env.log.filter((e) => e[0] !== 'style'), PROPS.map((p, i) => ['set', 'container', p, `${[0, 0, 800, 30.3][i]}px`]),
        'written like a glide: no instant')
    assert.equal(flushes(env).length, 0, 'no flush')
})

test('settle: a settle update while still scaled starts no new loop', () => {
    const site = makeScaledList()
    const { env } = site
    // Stays scaled and stable: fallback stop at the 3rd frame.
    frame(env, 0)
    assert.equal(env.frames.length, 1, 'frame 1 equal to the starting measure: goes on')
    frame(env, 16)
    assert.equal(env.frames.length, 1, 'frame 2: goes on')
    frame(env, 32)
    assert.equal(env.frames.length, 0, 'frame 3: the fallback stops it, and the settle update starts no loop')
})

test('settle: the cap needs both 60 frames and 500ms', () => {
    const run = (step, count) => {
        const site = makeScaledList()
        const { env, container } = site
        for (let i = 0; i < count; i++) {
            container.rect = rect(0, 0, 640 + (i % 2 ? 1 : 2), 320)
            frame(env, 1000 + i * step)
        }
        return env
    }
    // Frame n has elapsed (n - 1) × step: frame 1 counts as frame 1 and as elapsed 0.
    assert.equal(run(2.8, 60).frames.length, 1, '60 frames in 165ms: goes on')
    assert.equal(run(100, 59).frames.length, 1, '5.8s in 59 frames: goes on')
    assert.equal(run(8.4, 60).frames.length, 1, '60 frames, 495.6ms: goes on')
    assert.equal(run(8.5, 60).frames.length, 0, '60 frames, 501.5ms: stops')
    assert.equal(run(2.8, 180).frames.length, 0, '180 frames, 501.2ms: stops')
})

test('settle: an unscaled container (rounding) starts no loop', () => {
    const env = makeEnv()
    const container = makeElement(env, 'container', rect(0, 0, 812.4, 31.5), { offsetWidth: 812, offsetHeight: 32 })
    const ink = makeElement(env, 'ink')
    const row = makeElement(env, 'row', rect(10, 0, 80, 31.5))
    createGlideInk({ container, flushTarget: ink, getActive: () => row, env })
    assert.equal(env.frames.length, 0)
})

test('settle: hide and destroy cancel the frame; a new active element still glides', () => {
    const site = makeScaledList()
    site.active = null
    site.controller.update()
    assert.equal(site.env.frames.length, 0, 'hide cancels')

    const other = makeScaledList()
    other.controller.destroy()
    assert.equal(other.env.frames.length, 0, 'destroy cancels')

    const third = makeScaledList()
    const { env, rows } = third
    frame(env, 0)
    env.log = []
    third.active = rows[1]
    third.controller.update()
    assert.ok(!env.log.some((e) => e[2] === 'data-glide-instant'))
    assert.equal(flushes(env).length, 0)
    assert.equal(env.log.filter((e) => e[0] === 'set').length, 4)
    assert.equal(env.frames.length, 1, 'the loop keeps running')
})
