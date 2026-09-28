// Run with: node --test src/composables/usePopupMotion.test.js (from the frontend dir)
// Picker entrances (visual refresh step 5b, docs/plans/2026-09-28-overlay-motion-design.md §6, §14.6):
// the composable in an effect scope, with a fake panel, popup and environment whose
// functions check `this`.
import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'

import { usePopupMotion } from './usePopupMotion.js'

const DEFAULT_TOKENS = { '--motion-amount': '1', '--motion-ease-out': ' cubic-bezier(.22, 1, .36, 1)' }

class FakeAnimation {
    constructor(keyframes, options) {
        this.keyframes = keyframes
        this.options = options
        this.cancelled = false
        this.finished = new Promise((resolve, reject) => {
            this.resolve = resolve
            this.reject = reject
        })
    }

    cancel() {
        this.cancelled = true
        const error = new Error('AbortError')
        error.name = 'AbortError'
        this.reject(error)
    }
}

function makePanel() {
    const properties = new Map()
    const panel = {
        animations: [],
        style: {
            setProperty: (name, value) => properties.set(name, value),
            removeProperty: (name) => properties.delete(name),
            getPropertyValue: (name) => properties.get(name) ?? '',
        },
        animate(keyframes, options) {
            const animation = new FakeAnimation(keyframes, options)
            animation.inlineAtStart = Object.fromEntries(properties)
            panel.animations.push(animation)
            return animation
        },
    }
    return panel
}

function makePopup(attributes = {}) {
    return { getAttribute: (name) => attributes[name] ?? null }
}

/** requestAnimationFrame, cancelAnimationFrame and getComputedStyle throw when called unbound. */
function makeEnv(tokens = DEFAULT_TOKENS) {
    const env = { frames: new Map(), nextId: 1, cancelled: [] }
    env.requestAnimationFrame = function (fn) {
        if (this !== env) throw new TypeError('Illegal invocation')
        const id = env.nextId++
        env.frames.set(id, fn)
        return id
    }
    env.cancelAnimationFrame = function (id) {
        if (this !== env) throw new TypeError('Illegal invocation')
        env.cancelled.push(id)
        env.frames.delete(id)
    }
    env.getComputedStyle = function () {
        if (this !== env) throw new TypeError('Illegal invocation')
        return { getPropertyValue: (name) => tokens[name] ?? '' }
    }
    env.runFrames = () => {
        const frames = [...env.frames.values()]
        env.frames.clear()
        for (const fn of frames) fn(0)
    }
    return env
}

function setup({ attributes = { placement: 'top-start', 'data-current-placement': 'top-start' }, tokens } = {}) {
    const env = makeEnv(tokens)
    const isOpen = ref(false)
    const panel = ref(makePanel())
    const popup = ref(makePopup(attributes))
    const scope = effectScope()
    scope.run(() => usePopupMotion(isOpen, panel, popup, env))
    return { env, isOpen, panel: panel.value, popup, scope }
}

/** Open, let Vue flush (watcher, then the nextTick), then run the frame. */
async function openAndPlay(context) {
    context.isOpen.value = true
    await nextTick()
    await nextTick()
    context.env.runFrames()
}

const origin = (panel) => panel.style.getPropertyValue('transform-origin')

const REVEAL_FILTER = 'opacity(var(--twicc-reveal))'
const revealFilter = (panel) => panel.style.getPropertyValue('--twicc-reveal-filter')
const flush = () => new Promise((resolve) => setImmediate(resolve))

test('--twicc-reveal-filter (§14.6): set before the entrance, removed when it finishes; the origin stays', async () => {
    const context = setup()
    await openAndPlay(context)
    const [animation] = context.panel.animations
    assert.equal(animation.inlineAtStart['--twicc-reveal-filter'], REVEAL_FILTER, 'set before animate')
    assert.equal(revealFilter(context.panel), REVEAL_FILTER, 'held during the entrance')
    for (const name of ['--glass-bg', '--glass-sticky-bg', '--glass-tooltip-bg', '--glass-settle']) {
        assert.equal(animation.inlineAtStart[name], undefined, `no opaque-while-moving ${name}`)
    }
    animation.resolve(animation)
    await flush()
    assert.equal(revealFilter(context.panel), '', 'removed on finish')
    assert.equal(origin(context.panel), 'left bottom', 'transform-origin stays after finish')
    context.scope.stop()
})

test('--twicc-reveal-filter: removed on a close during the entrance and on dispose', async () => {
    const closed = setup()
    await openAndPlay(closed)
    closed.isOpen.value = false
    await nextTick()
    assert.equal(revealFilter(closed.panel), '', 'removed on cancel')
    closed.scope.stop()

    const disposed = setup()
    await openAndPlay(disposed)
    disposed.scope.stop()
    assert.equal(revealFilter(disposed.panel), '', 'removed on dispose')
    await flush()
})

test('open: after the frame, the entrance plays from the current placement', async () => {
    const cases = { 'top-start': 'left bottom', 'bottom-end': 'right top', bottom: 'center top', top: 'center bottom' }
    for (const [placement, expected] of Object.entries(cases)) {
        const context = setup({ attributes: { placement: 'top-start', 'data-current-placement': placement } })
        context.isOpen.value = true
        await nextTick()
        await nextTick()
        assert.equal(context.panel.animations.length, 0, 'nothing before the frame')
        assert.equal(context.env.frames.size, 1, 'one frame requested')
        context.env.runFrames()
        assert.equal(context.panel.animations.length, 1, placement)
        assert.equal(origin(context.panel), expected, placement)
        context.scope.stop()
    }
})

test('the entrance: --twicc-reveal and scale (no opacity), 180ms, the trimmed --motion-ease-out', async () => {
    const context = setup()
    await openAndPlay(context)
    const [animation] = context.panel.animations
    assert.deepEqual(animation.keyframes, [{ '--twicc-reveal': 0, scale: 1 - 0.06 }, { '--twicc-reveal': 1, scale: 1 }])
    assert.deepEqual(animation.options, { duration: 180, easing: 'cubic-bezier(.22, 1, .36, 1)' })
    context.scope.stop()
})

test('close before the frame: the frame is cancelled, nothing animates', async () => {
    const context = setup()
    context.isOpen.value = true
    await nextTick()
    await nextTick()
    const [frameId] = context.env.frames.keys()
    context.isOpen.value = false
    await nextTick()
    assert.deepEqual(context.env.cancelled, [frameId])
    context.env.runFrames()
    assert.equal(context.panel.animations.length, 0)
    context.scope.stop()
})

test('close before the nextTick: no frame is ever requested', async () => {
    const context = setup()
    context.isOpen.value = true
    await nextTick()
    context.isOpen.value = false
    await nextTick()
    await nextTick()
    context.env.runFrames()
    assert.equal(context.panel.animations.length, 0)
    context.scope.stop()
})

test('close during the entrance: the animation is cancelled, the inline origin removed', async () => {
    const context = setup()
    await openAndPlay(context)
    const [animation] = context.panel.animations
    assert.equal(origin(context.panel), 'left bottom')
    context.isOpen.value = false
    await nextTick()
    assert.equal(animation.cancelled, true)
    assert.equal(origin(context.panel), '')
    // The rejected `finished` raises nothing (an unhandled rejection would fail the run).
    await new Promise((resolve) => setImmediate(resolve))
    context.scope.stop()
})

test('scope dispose: same as close', async () => {
    const pending = setup()
    pending.isOpen.value = true
    await nextTick()
    await nextTick()
    const [frameId] = pending.env.frames.keys()
    pending.scope.stop()
    assert.deepEqual(pending.env.cancelled, [frameId])

    const running = setup()
    await openAndPlay(running)
    running.scope.stop()
    assert.equal(running.panel.animations[0].cancelled, true)
    assert.equal(origin(running.panel), '')
    await new Promise((resolve) => setImmediate(resolve))
})

test('no data-current-placement: the placement attribute; neither: center bottom', async () => {
    const fromAttribute = setup({ attributes: { placement: 'bottom-end' } })
    await openAndPlay(fromAttribute)
    assert.equal(origin(fromAttribute.panel), 'right top')
    fromAttribute.scope.stop()

    const none = setup({ attributes: {} })
    await openAndPlay(none)
    assert.equal(origin(none.panel), 'center bottom')
    none.scope.stop()
})

test('a missing or non-numeric --motion-amount counts as 1', async () => {
    for (const amount of [undefined, 'none']) {
        const tokens = { '--motion-ease-out': 'ease' }
        if (amount !== undefined) tokens['--motion-amount'] = amount
        const context = setup({ tokens })
        await openAndPlay(context)
        assert.equal(context.panel.animations[0].keyframes[0].scale, 1 - 0.06, String(amount))
        context.scope.stop()
    }
})

test('--motion-amount 0: the reveal only (scale 1); an empty --motion-ease-out falls back to ease-out', async () => {
    const context = setup({ tokens: { '--motion-amount': ' 0', '--motion-ease-out': '   ' } })
    await openAndPlay(context)
    const [animation] = context.panel.animations
    assert.deepEqual(animation.keyframes, [{ '--twicc-reveal': 0, scale: 1 }, { '--twicc-reveal': 1, scale: 1 }])
    assert.equal(animation.options.easing, 'ease-out')
    context.scope.stop()
})

test('a reopen replays the entrance', async () => {
    const context = setup()
    await openAndPlay(context)
    context.isOpen.value = false
    await nextTick()
    await openAndPlay(context)
    assert.equal(context.panel.animations.length, 2)
    assert.equal(context.panel.animations[0].cancelled, true)
    assert.equal(context.panel.animations[1].cancelled, false)
    context.scope.stop()
})
