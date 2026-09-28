// Run with: node --test src/utils/detailsMotion.test.js (from the frontend dir)
// wa-details open/close motion (visual refresh step 4b,
// docs/plans/2026-09-27-details-motion-design.md): the pure helpers, the Web Awesome
// contract the module relies on, and gesture sequences driven through fakes.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

import WaDetails from '@awesome.me/webawesome/dist/components/details/details.js'
import {
    closeKeyframes,
    detailsMotionStateOf,
    followKeyframes,
    followStep,
    handleOpenChange,
    IDLE_TIMEOUT_MS,
    installDetailsMotion,
    openKeyframes,
    parseDurationMs,
    resetDetailsMotionForTests,
    startState,
} from './detailsMotion.js'

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

test('parseDurationMs', () => {
    assert.equal(parseDurationMs('380ms'), 380)
    assert.equal(parseDurationMs('0.38s'), 380)
    assert.equal(parseDurationMs(' 200ms '), 200)
    assert.equal(parseDurationMs('0s'), 0)
    assert.equal(parseDurationMs('0ms'), 0)
    assert.equal(parseDurationMs(''), 0)
    assert.equal(parseDurationMs(undefined), 0)
})

test('startState', () => {
    assert.deepEqual(startState({ kind: 'open', interrupting: false, renderedHeight: 900, renderedOpacity: 1 }), { height: 0, opacity: 0 })
    assert.deepEqual(
        startState({ kind: 'close', interrupting: false, renderedHeight: 240, renderedOpacity: 1, settledHeight: 240 }),
        { height: 240, opacity: 1 },
    )
    assert.deepEqual(startState({ kind: 'open', interrupting: true, renderedHeight: 120, renderedOpacity: 0.4 }), { height: 120, opacity: 0.4 })
    assert.deepEqual(
        startState({ kind: 'close', interrupting: true, renderedHeight: 60, renderedOpacity: 0.3, settledHeight: 60 }),
        { height: 60, opacity: 0.3 },
    )
})

test('keyframe builders, normal and reduced', () => {
    assert.deepEqual(openKeyframes({ height: 0, opacity: 0 }, 240, false), [{ height: '0px', opacity: 0 }, { height: '240px', opacity: 1 }])
    assert.deepEqual(openKeyframes({ height: 80, opacity: 0.5 }, 240, false), [{ height: '80px', opacity: 0.5 }, { height: '240px', opacity: 1 }])
    assert.deepEqual(openKeyframes({ height: 0, opacity: 0 }, 240, true), [{ opacity: 0 }, { opacity: 1 }])
    assert.deepEqual(openKeyframes({ height: 80, opacity: 0.5 }, 240, true), [{ opacity: 0.5 }, { opacity: 1 }])

    assert.deepEqual(followKeyframes(100, 300, 1), [{ height: '100px', opacity: 1 }, { height: '300px', opacity: 1 }])
    assert.deepEqual(followKeyframes(300, 120, 0.6), [{ height: '300px', opacity: 0.6 }, { height: '120px', opacity: 1 }])

    assert.deepEqual(closeKeyframes({ height: 240, opacity: 1 }, false), [{ height: '240px', opacity: 1 }, { height: '0px', opacity: 0 }])
    assert.deepEqual(closeKeyframes({ height: 0, opacity: 0 }, false), [{ height: '0px', opacity: 0 }, { height: '0px', opacity: 0 }])
    assert.deepEqual(closeKeyframes({ height: 240, opacity: 1 }, true), [{ height: '240px', opacity: 1 }, { height: '240px', opacity: 0 }])
    assert.deepEqual(closeKeyframes({ height: 90, opacity: 0.4 }, true), [{ height: '90px', opacity: 0.4 }, { height: '90px', opacity: 0 }])
})

test('followStep', () => {
    const base = { running: false, renderedHeight: 200, lastHeight: 200, target: 200, descendantAnimating: false, unpinned: false }
    assert.deepEqual(followStep({ ...base, descendantAnimating: true }), { action: 'unpin' })
    assert.deepEqual(followStep({ ...base, descendantAnimating: true, unpinned: true }), { action: 'none' })
    assert.deepEqual(followStep({ ...base, unpinned: true, target: 260 }), { action: 'set', to: 260 })
    assert.deepEqual(followStep({ ...base, running: true, renderedHeight: 120, target: 200.3 }), { action: 'none' })
    assert.deepEqual(followStep({ ...base, target: 200.4 }), { action: 'none' })
    assert.deepEqual(followStep({ ...base, renderedHeight: 150, target: 320 }), { action: 'animate', from: 200, to: 320 })
    assert.deepEqual(followStep({ ...base, running: true, renderedHeight: 150, target: 320 }), { action: 'animate', from: 150, to: 320 })
    assert.deepEqual(followStep({ ...base, target: 120 }), { action: 'animate', from: 200, to: 120 })
})

test('installDetailsMotion replaces handleOpenChange once and fails loudly without it', () => {
    const original = function () {}
    class Fake {}
    Fake.prototype.handleOpenChange = original
    installDetailsMotion(Fake)
    assert.equal(Fake.prototype.handleOpenChange, handleOpenChange)
    installDetailsMotion(Fake)
    assert.equal(Fake.prototype.handleOpenChange, handleOpenChange)

    class Renamed {}
    assert.throws(() => installDetailsMotion(Renamed), /handleOpenChange/)
})

test('Web Awesome contract guard (re-read design §1.1 when this fails)', () => {
    const proto = WaDetails.prototype
    assert.equal(typeof proto.handleOpenChange, 'function')
    assert.equal(typeof proto.closeOthersWithSameName, 'function')
    for (const name of ['body', 'details', 'header']) {
        assert.equal(typeof Object.getOwnPropertyDescriptor(proto, name)?.get, 'function', `${name} accessor`)
    }
    assert.equal(WaDetails.elementProperties.get('isAnimating').state, true)

    const require = createRequire(import.meta.url)
    const root = dirname(require.resolve('@awesome.me/webawesome/package.json'))
    const watchChunk = readFileSync(join(root, 'dist/chunks/chunk.PZAN6FPN.js'), 'utf8')
    assert.ok(watchChunk.includes('this[decoratedFnName]('), 'the watch decorator calls the method by name')
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
    assert.equal(pkg.version, '3.3.1')
})

// ---------------------------------------------------------------------------
// Fakes for gesture sequences
// ---------------------------------------------------------------------------

const TOKENS = {
    '--motion-dur-2': '200ms',
    '--motion-dur-3': '380ms',
    '--motion-ease-out': 'cubic-bezier(.22, 1, .36, 1)',
    '--motion-ease-out-height': 'cubic-bezier(.25, .46, .45, .94)',
    '--motion-amount': '1',
}

const px = (value) => parseFloat(value)

class FakeAnimation {
    constructor(keyframes, options) {
        this.keyframes = keyframes
        this.options = options
        this.playState = 'running'
        this.progress = 0
        this.finished = new Promise((resolve, reject) => {
            this.resolveFinished = resolve
            this.rejectFinished = reject
        })
    }

    valueOf(property) {
        const first = this.keyframes[0][property]
        const last = this.keyframes[this.keyframes.length - 1][property]
        if (first === undefined || last === undefined) return undefined
        return px(first) + (px(last) - px(first)) * this.progress
    }

    cancel() {
        if (this.playState !== 'running') return
        this.playState = 'idle'
        const error = new Error('The animation was aborted')
        error.name = 'AbortError'
        this.rejectFinished(error)
    }

    finish() {
        assert.equal(this.playState, 'running', 'finishing an animation that is not running')
        this.playState = 'finished'
        this.progress = 1
        this.resolveFinished(this)
    }
}

function makeEnv() {
    const env = {
        tokens: { ...TOKENS },
        frames: [],
        nextFrameId: 1,
        timers: [],
        now: 0,
        observers: [],
        documentElement: { isDocumentElement: true },
    }
    env.document = { documentElement: env.documentElement }
    env.requestAnimationFrame = (callback) => {
        const id = env.nextFrameId++
        env.frames.push({ id, callback })
        return id
    }
    env.cancelAnimationFrame = (id) => {
        env.frames = env.frames.filter((f) => f.id !== id)
    }
    env.idles = []
    env.requestIdleCallback = (callback, options) => {
        env.idles.push({ callback, options })
    }
    env.setTimeout = (callback, delay) => {
        const id = env.nextFrameId++
        env.timers.push({ id, callback, at: env.now + delay })
        return id
    }
    env.clearTimeout = (id) => {
        env.timers = env.timers.filter((t) => t.id !== id)
    }
    env.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback
            this.targets = new Set()
            env.observers.push(this)
        }
        observe(target) { this.targets.add(target) }
        unobserve(target) { this.targets.delete(target) }
    }
    env.getComputedStyle = (element) => {
        if (element === env.documentElement) return { getPropertyValue: (name) => env.tokens[name] ?? '' }
        return {
            getPropertyValue: (name) => element.vars?.[name] ?? '',
            get opacity() { return String(element.computedOpacity) },
        }
    }
    env.setReduced = (reduced) => {
        env.tokens['--motion-amount'] = reduced ? '0' : '1'
    }
    return env
}

let env

/** Resolved microtasks (promise continuations) run. */
const flush = () => new Promise((resolve) => setImmediate(resolve))

/** Let the main thread go idle: run the queued idle callbacks, then the microtasks. */
async function idle() {
    const queued = env.idles
    env.idles = []
    for (const i of queued) i.callback()
    await flush()
}

/** Go idle (an opening waits for it), then run the queued animation frames (not the ones
    they queue), then the microtasks. */
async function frame() {
    await idle()
    const queued = env.frames
    env.frames = []
    for (const f of queued) f.callback(0)
    await flush()
}

async function tick(ms) {
    env.now += ms
    for (;;) {
        const due = env.timers.filter((t) => t.at <= env.now).sort((a, b) => a.at - b.at)[0]
        if (!due) break
        env.timers = env.timers.filter((t) => t !== due)
        due.callback()
    }
    await flush()
}

/** Report a content size change of these details to the shared ResizeObserver. */
function resize(...elements) {
    const observer = env.observers[0]
    const entries = elements.map((el) => el.slot).filter((slot) => observer?.targets.has(slot)).map((target) => ({ target }))
    if (entries.length) observer.callback(entries, observer)
}

const isObserved = (el) => !!env.observers[0]?.targets.has(el.slot)

function makeDetails({ contentHeight = 200, assigned = 1, name = 'details' } = {}) {
    const slot = {
        offsetHeight: contentHeight,
        assigned,
        assignedElements() { return Array.from({ length: this.assigned }, () => ({})) },
    }
    const body = {
        style: { height: '0', opacity: '', overflowX: '', overflowY: '' },
        vars: { '--show-duration': '200ms', '--hide-duration': '200ms' },
        animations: [],
        offsetParent: {},
        animate(keyframes, options) {
            const animation = new FakeAnimation(keyframes, options)
            this.animations.push(animation)
            return animation
        },
        running(property) {
            return [...this.animations].reverse().find((a) => a.playState === 'running' && a.valueOf(property) !== undefined)
        },
        get offsetHeight() {
            const animation = this.running('height')
            if (animation) return animation.valueOf('height')
            if (!this.style.height || this.style.height === 'auto') return slot.offsetHeight
            return px(this.style.height)
        },
        get computedOpacity() {
            const animation = this.running('opacity')
            if (animation) return animation.valueOf('opacity')
            return this.style.opacity === '' ? 1 : Number(this.style.opacity)
        },
    }
    const attributes = new Map()
    const el = {
        name,
        localName: 'wa-details',
        open: false,
        isAnimating: false,
        isConnected: true,
        details: { open: false },
        body,
        slot,
        shadowRoot: { querySelector: (selector) => (selector === '[part~="content"]' ? slot : null) },
        events: [],
        preventNext: new Set(),
        closedOthers: 0,
        parentElement: null,
        root: {},
        getRootNode() { return this.root },
        closeOthersWithSameName() { this.closedOthers++ },
        dispatchEvent(event) {
            if (this.preventNext.has(event.type)) {
                this.preventNext.delete(event.type)
                event.preventDefault()
            }
            this.events.push(event.type)
            return !event.defaultPrevented
        },
        setAttribute(key, value) { attributes.set(key, String(value)) },
        removeAttribute(key) { attributes.delete(key) },
        hasAttribute(key) { return attributes.has(key) },
        instant() {
            body.vars['--show-duration'] = '0ms'
            body.vars['--hide-duration'] = '0ms'
        },
        animated() {
            body.vars['--show-duration'] = '200ms'
            body.vars['--hide-duration'] = '200ms'
        },
        get lastAnimation() { return body.animations[body.animations.length - 1] },
        count(type) { return this.events.filter((t) => t === type).length },
    }
    return el
}

/** Nest `child` in the light DOM of `parent`. */
function nest(child, parent) {
    child.parentElement = { closest: (selector) => (selector === 'wa-details' ? parent : null) }
}

/** Nest `child` in the shadow root of a host element that is itself inside `parent`. */
function nestThroughShadowRoot(child, parent) {
    child.parentElement = { closest: () => null }
    child.root = { host: { closest: (selector) => (selector === 'wa-details' ? parent : null) } }
}

function open(el) {
    el.open = true
    return handleOpenChange.call(el)
}

function close(el) {
    el.open = false
    return handleOpenChange.call(el)
}

/** A settled, followed card: a user open whose opening motion has ended. */
async function openSettled(el) {
    open(el)
    await frame()
    el.lastAnimation.finish()
    await flush()
    await frame()
}

const LOADING = 'data-motion-loading'

test.beforeEach(() => {
    env = makeEnv()
    resetDetailsMotionForTests(env)
})

// ---------------------------------------------------------------------------
// Gesture sequences
// ---------------------------------------------------------------------------

test('user open: followed and pinned, one wa-after-show; the fold clears the inline overflow', async () => {
    const el = makeDetails({ contentHeight: 244 })
    open(el)
    assert.deepEqual(el.events, ['wa-show'])
    assert.equal(el.details.open, true)
    assert.equal(el.closedOthers, 1)
    assert.equal(el.isAnimating, true)
    assert.equal(el.body.style.overflowY, 'clip')
    assert.equal(el.body.style.overflowX, 'visible', 'a toolbar placed beside the block stays visible')
    assert.equal(el.body.style.height, '0px', 'the start state is held until the frame')
    assert.equal(el.body.style.opacity, '0')
    assert.equal(el.body.animations.length, 0)

    await frame()
    const animation = el.lastAnimation
    assert.deepEqual(animation.keyframes, [{ height: '0px', opacity: 0 }, { height: '244px', opacity: 1 }])
    assert.deepEqual(animation.options, { duration: 380, easing: TOKENS['--motion-ease-out-height'] })
    assert.equal(el.body.style.height, '244px')
    assert.equal(el.body.style.opacity, '')
    assert.equal(detailsMotionStateOf(el).followed, true)
    assert.ok(isObserved(el))

    animation.finish()
    await flush()
    assert.deepEqual(el.events, ['wa-show', 'wa-after-show'])
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.height, '244px')
    assert.equal(el.body.style.overflowY, 'clip')

    close(el)
    assert.deepEqual(el.events.slice(2), ['wa-hide'])
    assert.equal(el.isAnimating, true)
    assert.equal(el.body.style.height, 'auto')
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '244px', opacity: 1 }, { height: '0px', opacity: 0 }])
    assert.deepEqual(el.lastAnimation.options, { duration: 380, easing: TOKENS['--motion-ease-out-height'] })
    assert.equal(el.details.open, true, 'the content stays visible during the fold')
    assert.equal(detailsMotionStateOf(el).followed, false)
    assert.ok(!isObserved(el))

    el.lastAnimation.finish()
    await flush()
    assert.equal(el.details.open, false)
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.height, 'auto')
    assert.equal(el.body.style.overflowY, '')
    assert.deepEqual(el.events, ['wa-show', 'wa-after-show', 'wa-hide', 'wa-after-hide'])
})

test('an opening waits for an idle main thread (capped) before its frame', async () => {
    const el = makeDetails({ contentHeight: 244 })
    open(el)
    assert.equal(env.idles.length, 1)
    assert.deepEqual(env.idles[0].options, { timeout: IDLE_TIMEOUT_MS })
    assert.equal(IDLE_TIMEOUT_MS, 250)
    // A frame before the idle callback measures nothing: the start state stays held.
    const queued = env.frames
    env.frames = []
    for (const f of queued) f.callback(0)
    await flush()
    assert.equal(el.body.animations.length, 0)
    assert.equal(el.body.style.height, '0px')

    await frame()
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '0px', opacity: 0 }, { height: '244px', opacity: 1 }])
})

test('without requestIdleCallback, the opening falls back to a zero timeout', async () => {
    delete env.requestIdleCallback
    const el = makeDetails({ contentHeight: 244 })
    open(el)
    assert.equal(env.timers.length, 1)
    assert.equal(env.timers[0].at, env.now)
    await tick(0)
    await frame()
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '0px', opacity: 0 }, { height: '244px', opacity: 1 }])
})

test('a follow waits for an idle main thread before its frame; a stale wait does nothing', async () => {
    const el = makeDetails({ contentHeight: 200 })
    await openSettled(el)
    const before = el.body.animations.length
    el.slot.offsetHeight = 320
    resize(el)
    assert.equal(env.idles.length, 1, 'the follow waits for idle')
    assert.equal(env.frames.length, 0, 'no frame before idle')
    await idle()
    assert.equal(env.frames.length, 1)
    await frame()
    assert.equal(el.body.animations.length, before + 1)

    // A wait superseded by a close then a reopen resolves into nothing.
    el.slot.offsetHeight = 400
    resize(el)
    assert.equal(env.idles.length, 1)
    const staleIdle = env.idles.shift()
    close(el)
    open(el)
    staleIdle.callback()
    await flush()
    assert.equal(env.frames.length, 0, 'the stale follow wait requests no frame')
})

test('instant open and close: at once, not followed, no inline overflow', async () => {
    const el = makeDetails()
    el.instant()
    open(el)
    assert.deepEqual(el.events, ['wa-show', 'wa-after-show'])
    assert.equal(el.body.style.height, 'auto')
    assert.equal(el.body.style.overflowY, '')
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.animations.length, 0)
    assert.equal(detailsMotionStateOf(el).followed, false)
    assert.ok(!isObserved(el))

    close(el)
    assert.deepEqual(el.events.slice(2), ['wa-hide', 'wa-after-hide'])
    assert.equal(el.details.open, false)
    assert.equal(el.body.style.height, 'auto')
    assert.equal(el.body.style.overflowY, '')
    assert.equal(el.body.animations.length, 0)
})

test('prevented wa-show: closed again, no inline overflow', async () => {
    const el = makeDetails()
    el.preventNext.add('wa-show')
    open(el)
    assert.equal(el.open, false)
    assert.equal(el.details.open, false)
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.overflowY, '')
    assert.deepEqual(el.events, ['wa-show'])
    await frame()
    assert.equal(el.body.animations.length, 0)
})

test('prevented wa-hide on a card neither followed nor opening: stays open, no inline overflow', async () => {
    const el = makeDetails()
    el.instant()
    open(el)
    el.animated()
    el.preventNext.add('wa-hide')
    close(el)
    assert.equal(el.open, true)
    assert.equal(el.details.open, true)
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.overflowY, '')
    assert.equal(el.body.animations.length, 0)
})

test('content arriving during the opening re-targets it; one wa-after-show after the replacement ends', async () => {
    const el = makeDetails({ contentHeight: 20 })
    open(el)
    await frame()
    const opening = el.lastAnimation
    opening.progress = 0.5 // 10px, opacity 0.5
    el.slot.offsetHeight = 300
    resize(el)
    await frame()
    const follow = el.lastAnimation
    assert.notEqual(follow, opening)
    assert.equal(opening.playState, 'idle')
    assert.deepEqual(follow.keyframes, [{ height: '10px', opacity: 0.5 }, { height: '300px', opacity: 1 }])
    assert.deepEqual(follow.options, { duration: 200, easing: TOKENS['--motion-ease-out-height'] })
    assert.equal(el.body.style.height, '300px')
    assert.equal(el.count('wa-after-show'), 0)
    assert.equal(el.isAnimating, true)

    follow.finish()
    await flush()
    assert.equal(el.count('wa-after-show'), 1)
    assert.equal(el.isAnimating, false)
})

test('reduced motion switched on while followed: the next change is set at once', async () => {
    const el = makeDetails()
    await openSettled(el)
    const before = el.body.animations.length
    env.setReduced(true)
    el.slot.offsetHeight = 320
    resize(el)
    await frame()
    assert.equal(el.body.animations.length, before)
    assert.equal(el.body.style.height, '320px')
    assert.equal(detailsMotionStateOf(el).lastHeight, 320)
})

test('content shrinking on a settled followed card animates down', async () => {
    const el = makeDetails({ contentHeight: 300 })
    await openSettled(el)
    el.slot.offsetHeight = 120
    resize(el)
    await frame()
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '300px', opacity: 1 }, { height: '120px', opacity: 1 }])
    assert.equal(el.body.style.height, '120px')
    assert.equal(el.isAnimating, false, 'a follow of a settled card is not an opening')
})

test('the initial observer notification changes nothing', async () => {
    const el = makeDetails()
    await openSettled(el)
    const before = el.body.animations.length
    resize(el)
    await frame()
    assert.equal(el.body.animations.length, before)
})

test('close during the opening folds from the rendered height and opacity', async () => {
    const el = makeDetails({ contentHeight: 200 })
    open(el)
    await frame()
    el.lastAnimation.progress = 0.5
    close(el)
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '100px', opacity: 0.5 }, { height: '0px', opacity: 0 }])
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.details.open, false)
    assert.equal(el.count('wa-after-hide'), 1)
    assert.equal(el.count('wa-after-show'), 0)
    assert.equal(el.isAnimating, false)
})

test('close after the idle wait, before the frame: folds from the held start', async () => {
    const el = makeDetails({ contentHeight: 200 })
    open(el)
    await idle()
    assert.equal(env.frames.length, 1, 'the opening now waits for its frame')
    close(el)
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '0px', opacity: 0 }, { height: '0px', opacity: 0 }])
    await frame() // the superseded open's frame: it must touch nothing
    assert.equal(el.body.animations.length, 1)
    assert.equal(el.body.style.height, 'auto')
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.count('wa-after-show'), 0)
    assert.equal(el.count('wa-after-hide'), 1)
})

test('close during the idle wait folds from the held start state', async () => {
    const el = makeDetails({ contentHeight: 200 })
    open(el)
    close(el)
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '0px', opacity: 0 }, { height: '0px', opacity: 0 }])
    await idle()
    assert.equal(env.frames.length, 0, 'the superseded open requests no frame')
    await frame() // the superseded open's frame: it must touch nothing
    assert.equal(el.body.animations.length, 1)
    assert.equal(el.body.style.height, 'auto')
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.count('wa-after-hide'), 1)
    assert.equal(el.count('wa-after-show'), 0)
    assert.equal(el.details.open, false)
})

test('reopen during the fold grows back from where it is and ends followed', async () => {
    const el = makeDetails({ contentHeight: 200 })
    await openSettled(el)
    close(el)
    const fold = el.lastAnimation
    fold.progress = 0.25 // 150px, opacity 0.75
    open(el)
    assert.equal(fold.playState, 'idle')
    assert.equal(el.details.open, true)
    await frame()
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '150px', opacity: 0.75 }, { height: '200px', opacity: 1 }])
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.count('wa-after-hide'), 0)
    assert.equal(el.count('wa-after-show'), 2)
    assert.equal(el.details.open, true)
    assert.equal(detailsMotionStateOf(el).followed, true)
    assert.equal(el.body.style.height, '200px')
})

test('open, close, open, close: ends closed with one wa-after-hide', async () => {
    const el = makeDetails()
    open(el)
    await frame()
    close(el)
    open(el)
    await frame()
    close(el)
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.details.open, false)
    assert.equal(el.count('wa-after-hide'), 1)
    assert.equal(el.count('wa-after-show'), 0)
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.overflowY, '')
})

test('prevented wa-hide during an opening: pinned open, one wa-after-show', async () => {
    const el = makeDetails({ contentHeight: 200 })
    open(el)
    await frame()
    const opening = el.lastAnimation
    opening.progress = 0.5
    el.preventNext.add('wa-hide')
    close(el)
    assert.equal(el.open, true)
    assert.equal(el.details.open, true)
    assert.equal(el.isAnimating, false)
    assert.equal(el.body.style.height, '200px')
    assert.equal(el.body.style.overflowY, 'clip')
    assert.equal(detailsMotionStateOf(el).followed, true)
    assert.equal(detailsMotionStateOf(el).lastHeight, 200)
    assert.ok(isObserved(el))
    await flush()
    assert.equal(el.count('wa-after-show'), 1)
})

test('nested: an inner animation unpins a followed outer; the outer re-pins after it ends', async () => {
    const outer = makeDetails({ contentHeight: 300 })
    const inner = makeDetails({ contentHeight: 100 })
    nest(inner, outer)
    await openSettled(outer)
    assert.equal(outer.body.style.height, '300px')

    open(inner)
    await frame()
    const innerOpening = inner.lastAnimation
    assert.equal(outer.body.style.height, '', 'unpinned in the same task')
    assert.equal(detailsMotionStateOf(outer).unpinned, true)

    // The outer grows with the inner motion: its follow frames leave it auto.
    outer.slot.offsetHeight = 360
    resize(outer)
    await frame()
    assert.equal(outer.body.style.height, '')
    assert.equal(outer.body.animations.length, 1)

    // The end of the inner animation schedules the outer's frame without a size report.
    outer.slot.offsetHeight = 400
    innerOpening.finish()
    await flush()
    await frame()
    assert.equal(outer.body.style.height, '400px')
    assert.equal(detailsMotionStateOf(outer).unpinned, false)
    assert.equal(detailsMotionStateOf(outer).lastHeight, 400)
    assert.equal(outer.body.animations.length, 1, 're-pinned without an animation')
})

test('nested, outer still opening: its loop ends when a nested card cancels its animation', async () => {
    const outer = makeDetails({ contentHeight: 300 })
    const inner = makeDetails({ contentHeight: 100 })
    nest(inner, outer)
    open(outer)
    await frame()
    const outerOpening = outer.lastAnimation
    open(inner)
    await frame()
    assert.equal(outerOpening.playState, 'idle')
    assert.equal(outer.body.style.height, '')
    assert.equal(outer.body.style.opacity, '')
    assert.equal(outer.isAnimating, false)
    assert.equal(outer.count('wa-after-show'), 1)
    inner.lastAnimation.finish()
    await flush()
    await frame()
    assert.equal(outer.count('wa-after-show'), 1)
    assert.equal(outer.body.style.height, '300px')
})

test('nested, three levels: a non-followed middle card is skipped, and crossed', async () => {
    const grand = makeDetails({ contentHeight: 500 })
    const middle = makeDetails({ contentHeight: 300 })
    const child = makeDetails({ contentHeight: 100 })
    nest(middle, grand)
    nestThroughShadowRoot(child, middle)
    await openSettled(grand)
    middle.instant()
    open(middle)
    assert.equal(detailsMotionStateOf(middle).followed, false)

    open(child)
    await frame()
    assert.equal(detailsMotionStateOf(grand).unpinned, true)
    assert.equal(grand.body.style.height, '')
    assert.equal(middle.body.style.height, 'auto', 'the restored middle card is left alone')

    grand.slot.offsetHeight = 520
    resize(grand)
    await frame()
    assert.equal(grand.body.style.height, '', 'descendantAnimating through the middle card')
    assert.equal(grand.body.animations.length, 1)

    child.lastAnimation.finish()
    await flush()
    await frame()
    assert.equal(grand.body.style.height, '520px')
})

test('close reads reduced motion live', async () => {
    const instantCard = makeDetails({ contentHeight: 180 })
    instantCard.instant()
    open(instantCard)
    instantCard.animated()
    env.setReduced(true)
    close(instantCard)
    assert.deepEqual(instantCard.lastAnimation.keyframes, [{ height: '180px', opacity: 1 }, { height: '180px', opacity: 0 }])
    assert.deepEqual(instantCard.lastAnimation.options, { duration: 200, easing: TOKENS['--motion-ease-out-height'] })

    env.setReduced(false)
    const followed = makeDetails({ contentHeight: 220 })
    await openSettled(followed)
    env.setReduced(true)
    close(followed)
    assert.deepEqual(followed.lastAnimation.keyframes, [{ height: '220px', opacity: 1 }, { height: '220px', opacity: 0 }])
})

test('nested re-target: the outer stays unpinned until the replacement animation ends', async () => {
    const outer = makeDetails({ contentHeight: 300 })
    const inner = makeDetails({ contentHeight: 50 })
    nest(inner, outer)
    await openSettled(outer)

    open(inner)
    await frame()
    const a1 = inner.lastAnimation
    inner.slot.offsetHeight = 150
    resize(inner)
    await frame()
    const a2 = inner.lastAnimation
    assert.notEqual(a1, a2)
    assert.equal(a1.playState, 'idle')
    await frame() // the frame scheduled by A1's cancellation
    assert.equal(detailsMotionStateOf(outer).unpinned, true)
    assert.equal(outer.body.style.height, '')

    outer.slot.offsetHeight = 400
    a2.finish()
    await flush()
    await frame()
    assert.equal(outer.body.style.height, '400px')
    assert.equal(detailsMotionStateOf(outer).unpinned, false)
})

test('nested: a close interrupting an inner opening keeps the outer unpinned until the fold ends', async () => {
    const outer = makeDetails({ contentHeight: 300 })
    const inner = makeDetails({ contentHeight: 80 })
    nest(inner, outer)
    await openSettled(outer)

    open(inner)
    await frame()
    close(inner)
    const fold = inner.lastAnimation
    await frame()
    assert.equal(detailsMotionStateOf(outer).unpinned, true)
    assert.equal(outer.body.style.height, '')

    outer.slot.offsetHeight = 260
    fold.finish()
    await flush()
    await frame()
    assert.equal(outer.body.style.height, '260px')
})

test('loading line: set after 150ms on an empty body, removed when content appears', async () => {
    const el = makeDetails({ assigned: 0, contentHeight: 0 })
    open(el)
    await frame()
    await tick(149)
    assert.equal(el.hasAttribute(LOADING), false)
    await tick(1)
    assert.equal(el.hasAttribute(LOADING), true)

    el.slot.assigned = 1
    el.slot.offsetHeight = 200
    resize(el)
    await frame()
    assert.equal(el.hasAttribute(LOADING), false)
    await tick(10000)
    assert.equal(el.hasAttribute(LOADING), false)
})

test('loading line: not set when the body has content', async () => {
    const el = makeDetails({ assigned: 1 })
    open(el)
    await frame()
    await tick(200)
    assert.equal(el.hasAttribute(LOADING), false)
})

test('loading line: removed after 10s', async () => {
    const el = makeDetails({ assigned: 0, contentHeight: 0 })
    open(el)
    await frame()
    await tick(150)
    assert.equal(el.hasAttribute(LOADING), true)
    await tick(9999)
    assert.equal(el.hasAttribute(LOADING), true)
    await tick(1)
    assert.equal(el.hasAttribute(LOADING), false)
})

test('loading line: never set after a token change', async () => {
    const el = makeDetails({ assigned: 0, contentHeight: 0 })
    open(el)
    await frame()
    close(el)
    el.open = true // a stale timer must not trust the host state alone
    await tick(200)
    assert.equal(el.hasAttribute(LOADING), false)
})

test('disconnect while followed: following stops, the pending open loop completes', async () => {
    const el = makeDetails()
    open(el)
    await frame()
    const state = detailsMotionStateOf(el)
    el.isConnected = false
    resize(el)
    await frame()
    assert.equal(state.followed, false)
    assert.ok(!isObserved(el))
    assert.equal(el.body.style.overflowY, '', 'the clip is cleared with the pin')
    assert.equal(detailsMotionStateOf(el), state, 'the state entry remains')
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.isAnimating, false)
    assert.equal(el.count('wa-after-show'), 1)
})

test('close while unpinned by a nested card, reopen: the next change animates (unpinned was reset)', async () => {
    const outer = makeDetails({ contentHeight: 300 })
    const inner = makeDetails({ contentHeight: 100 })
    nest(inner, outer)
    await openSettled(outer)
    open(inner)
    await frame()
    assert.equal(detailsMotionStateOf(outer).unpinned, true)

    close(outer)
    assert.equal(detailsMotionStateOf(outer).unpinned, false)
    inner.lastAnimation.finish()
    open(outer)
    await frame()
    outer.lastAnimation.finish()
    await flush()
    const before = outer.body.animations.length
    outer.slot.offsetHeight = 360
    resize(outer)
    await frame()
    assert.equal(outer.body.animations.length, before + 1)
    assert.deepEqual(outer.lastAnimation.keyframes, [{ height: '300px', opacity: 1 }, { height: '360px', opacity: 1 }])
})

test('content without layout while followed: following stops, height auto', async () => {
    const el = makeDetails()
    await openSettled(el)
    el.slot.offsetHeight = 0
    el.body.offsetParent = null
    resize(el)
    await frame()
    assert.equal(detailsMotionStateOf(el).followed, false)
    assert.ok(!isObserved(el))
    assert.ok(['', 'auto'].includes(el.body.style.height))
    assert.equal(el.body.style.overflowY, '', 'an unfollowed card is not clipped')
})

test('reduced motion: opacity-only open, observed but not followed, held height on close', async () => {
    env.setReduced(true)
    const el = makeDetails({ assigned: 0, contentHeight: 0 })
    open(el)
    await frame()
    assert.equal(el.body.style.height, 'auto')
    assert.deepEqual(el.lastAnimation.keyframes, [{ opacity: 0 }, { opacity: 1 }])
    assert.deepEqual(el.lastAnimation.options, { duration: 200, easing: TOKENS['--motion-ease-out-height'] })
    assert.equal(detailsMotionStateOf(el).followed, false)
    assert.ok(isObserved(el))

    await tick(150)
    assert.equal(el.hasAttribute(LOADING), true)
    el.slot.assigned = 1
    el.slot.offsetHeight = 240
    resize(el)
    await frame()
    assert.equal(el.hasAttribute(LOADING), false)
    assert.equal(el.body.style.height, 'auto')
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.count('wa-after-show'), 1)

    close(el)
    assert.deepEqual(el.lastAnimation.keyframes, [{ height: '240px', opacity: 1 }, { height: '240px', opacity: 0 }])
    assert.equal(el.details.open, true)
    el.lastAnimation.finish()
    await flush()
    assert.equal(el.details.open, false)
    assert.equal(el.count('wa-after-hide'), 1)
})
