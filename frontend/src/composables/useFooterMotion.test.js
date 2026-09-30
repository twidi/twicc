// Run with: node --test src/composables/useFooterMotion.test.js (from the frontend dir)
// Footer blocks motion (visual refresh step 7d, docs/plans/2026-09-30-footer-blocks-motion-design.md
// §4, §6): the controller, driven in an effect scope with a fake environment and fake elements.
// The DOM patch of a state change is a pre watcher registered after the block's own: it runs
// between the block's pre measure and its post measure, like the component render job.
import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, effectScope, nextTick, reactive, ref, shallowRef, watch } from 'vue'

import { createFooterMotion, createSwitchGate, FOOTER_MOTION_KEY, settleRender, useFooterBlockMotion } from './useFooterMotion.js'

// A timer fires only once the microtask queue is empty: every pending settle (§9), the async
// post watcher, footerEnter and the restore have run by then.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))
const flush = settle
// A reactive change: the watchers run at nextTick, their async tail by the flush.
const tick = async () => {
    await nextTick()
    await flush()
}

function deferred() {
    let resolve
    const promise = new Promise((r) => {
        resolve = r
    })
    return { promise, resolve }
}

// ── Fakes ────────────────────────────────────────────────────────────────────

function makeEnv({ reduced = false } = {}) {
    const env = { frames: [], observers: [], reduced }
    env.getComputedStyle = () => ({ getPropertyValue: () => '' })
    env.matchMedia = (query) => ({ matches: query === '(prefers-reduced-motion: reduce)' && env.reduced })
    env.requestAnimationFrame = (callback) => {
        env.frames.push(callback)
        return env.frames.length
    }
    env.runFrames = () => env.frames.splice(0).forEach((callback) => callback())
    env.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback
            this.targets = new Set()
            this.disconnected = false
            env.observers.push(this)
        }
        observe(target) { this.targets.add(target) }
        disconnect() { this.targets.clear(); this.disconnected = true }
    }
    return env
}

const kindOf = (keyframes) => {
    if (keyframes[0].position) return 'restore'
    if (keyframes[0].inset !== undefined) return 'inset'
    if (keyframes[0].height !== undefined) return 'height'
    return 'fade'
}

function makeAnimation(target, keyframes, options, log) {
    const handlers = {}
    const animation = { target, keyframes, options, kind: kindOf(keyframes), running: true }
    animation.finished = new Promise((resolve, reject) => Object.assign(handlers, { resolve, reject }))
    animation.finished.catch(() => {})
    animation.finish = () => {
        if (!animation.running) return
        animation.running = false
        handlers.resolve(animation)
    }
    animation.cancel = () => {
        log.push(['cancel', target.name, animation.kind])
        if (!animation.running) return
        animation.running = false
        handlers.reject(new Error('AbortError'))
    }
    animation.fail = () => {
        animation.running = false
        handlers.reject(new Error('interrupted'))
    }
    return animation
}

function makeElement(name, log, props = {}) {
    const el = {
        name,
        localName: 'div',
        shadowRoot: null,
        natural: 0,
        animatedPx: null,
        rect: null,
        visible: true,
        inert: false,
        classes: new Set(),
        attrs: new Map(),
        own: [],
        foreign: [],
        children: [],
        listeners: new Map(),
        container: null,
        clientTop: 0, clientHeight: 0, offsetHeight: 0,
        ...props,
    }
    let inlineHeight = ''
    el.style = {
        get height() { return inlineHeight },
        set height(value) {
            log.push(['style.height', name, value])
            inlineHeight = value
        },
    }
    el.getBoundingClientRect = () => {
        log.push(['measure', name])
        if (el.rect) return { ...el.rect, height: el.rect.bottom - el.rect.top }
        let height = el.natural
        if (inlineHeight) height = parseFloat(inlineHeight)
        else if (el.animatedPx !== null && el.own.some((a) => a.running && a.kind === 'height')) height = el.animatedPx
        return { top: 0, bottom: height, height }
    }
    el.getClientRects = () => (el.visible ? [{}] : [])
    el.closest = (selector) => (selector === '.session-items-list' ? el.container : null)
    el.animate = (keyframes, options) => {
        const animation = makeAnimation(el, keyframes, options, log)
        log.push(['animate', name, animation.kind])
        el.own.push(animation)
        return animation
    }
    el.getAnimations = () => [...el.own, ...el.foreign].filter((a) => a.running)
    el.setAttribute = (attr, value = '') => {
        log.push(['setAttribute', name, attr])
        el.attrs.set(attr, value)
    }
    el.removeAttribute = (attr) => {
        log.push(['removeAttribute', name, attr])
        el.attrs.delete(attr)
    }
    el.hasAttribute = (attr) => el.attrs.has(attr)
    el.classList = { contains: (c) => el.classes.has(c) }
    el.querySelector = (selector) => {
        const wanted = selector.replace(/^\./, '')
        const walk = (node) => {
            for (const child of node.children) {
                if (child.classes.has(wanted)) return child
                const found = walk(child)
                if (found) return found
            }
            return null
        }
        return walk(el)
    }
    // Only '*' is used (settleRender): every light-DOM descendant, never into a shadow root.
    el.querySelectorAll = (selector) => {
        assert.equal(selector, '*')
        const all = []
        const walk = (node) => node.children.forEach((child) => {
            all.push(child)
            walk(child)
        })
        walk(el)
        return all
    }
    el.addEventListener = (type, fn) => {
        if (!el.listeners.has(type)) el.listeners.set(type, new Set())
        el.listeners.get(type).add(fn)
    }
    el.removeEventListener = (type, fn) => el.listeners.get(type)?.delete(fn)
    el.dispatch = (type, target = el) => [...(el.listeners.get(type) ?? [])].forEach((fn) => fn({ type, target }))
    el.listenerCount = () => [...el.listeners.values()].reduce((n, set) => n + set.size, 0)
    return el
}

/**
 * A Web Awesome element: `updateComplete` (a getter, like Lit's) returns what `update()` returns,
 * a fresh thenable per read. Its resolution is where the real element changes the layout.
 */
function makeWa(name, log, update = () => Promise.resolve(true), props = {}) {
    const el = makeElement(name, log, { localName: 'wa-button', reads: 0, ...props })
    Object.defineProperty(el, 'updateComplete', {
        get() {
            el.reads += 1
            return update()
        },
    })
    return el
}

function makeWorld({ reduced = false, atBottom = true, pinEnabled = true, enabled = true } = {}) {
    const log = []
    const env = makeEnv({ reduced })
    const scroller = makeElement('scroller', log, { scrollHeight: 1000, clientHeight: 400, scrollTop: 600 })
    const flags = { enabled: ref(enabled), pinEnabled: ref(pinEnabled), atBottom: ref(atBottom) }
    const motion = createFooterMotion({
        env,
        enabled: flags.enabled,
        pinEnabled: flags.pinEnabled,
        getScrollEl: () => scroller,
        isAtBottom: () => flags.atBottom.value,
    })
    const container = makeElement('list', log, { rect: { top: 0, bottom: 500 }, clientTop: 0, clientHeight: 500, offsetHeight: 500 })
    return { log, env, scroller, motion, container, ...flags }
}

/**
 * A block attached in its own effect scope. `patch(state)` is the DOM patch: it runs after the
 * block's pre watcher and before its post watcher. Default patch: the wrapper takes the natural
 * height of the new shape, the block gets or loses `.maximized`.
 */
function mountBlock(world, { shape = 'a', maximized = false, heights = {}, withBlock = true, patch, beforeMeasure, onSettled } = {}) {
    const { log, motion, container } = world
    const wrapper = makeElement('wrapper', log, { natural: heights[shape] ?? 0 })
    const block = withBlock ? makeElement('block', log, { container }) : null
    if (block) {
        wrapper.children.push(block)
        block.rect = { top: 400, bottom: 480 }
        if (maximized) block.classes.add('maximized')
    }
    const state = reactive({ shape, maximized, unrelated: 0 })
    const shapeRef = computed(() => (state.unrelated >= 0 ? state.shape : state.shape))
    const maximizedRef = computed(() => state.maximized)
    const scope = effectScope()
    const api = scope.run(() => {
        const result = useFooterBlockMotion({
            // Template refs hold the raw element (Vue never proxies a DOM node): shallowRef.
            wrapperRef: shallowRef(wrapper),
            blockRef: shallowRef(block),
            shape: shapeRef,
            maximized: maximizedRef,
            beforeMeasure,
            onSettled,
            motion,
        })
        watch([shapeRef, maximizedRef], ([s, m]) => {
            if (patch) return patch({ shape: s, maximized: m, wrapper, block })
            if (heights[s] !== undefined) wrapper.natural = heights[s]
            if (block) {
                if (m) {
                    block.classes.add('maximized')
                    block.rect = { top: 0, bottom: 500 }
                } else {
                    block.classes.delete('maximized')
                    block.rect = { top: 400, bottom: 480 }
                }
            }
        }, { flush: 'pre' })
        return result
    })
    return { wrapper, block, state, scope, api }
}

const animated = (log) => log.filter((e) => e[0] === 'animate').map((e) => `${e[1]}:${e[2]}`)
const measures = (log, name) => log.filter((e) => e[0] === 'measure' && e[1] === name).length
const runningOn = (el) => el.own.filter((a) => a.running)
const finishAll = (...els) => els.forEach((el) => el.own.forEach((a) => a.finish()))

// ── Shape changes ────────────────────────────────────────────────────────────

test('one measure pair per shape change, none for an unrelated reactive change', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    state.unrelated += 1
    await tick()
    assert.equal(measures(world.log, 'wrapper'), 0, 'the computed shape did not change: no layout read')
    state.shape = 'b'
    await tick()
    assert.equal(measures(world.log, 'wrapper'), 2, 'pre + post')
    assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'])
    const [heightAnim, fadeAnim] = wrapper.own
    assert.deepEqual(heightAnim.keyframes, [{ height: '30px', overflowY: 'clip' }, { height: '80px', overflowY: 'clip' }])
    assert.deepEqual(heightAnim.options, { duration: 380, easing: 'cubic-bezier(.25, .46, .45, .94)' })
    assert.deepEqual(fadeAnim.keyframes, [{ opacity: 0 }, { opacity: 1 }])
    assert.deepEqual(fadeAnim.options, { duration: 200, easing: 'ease-in-out' })
    scope.stop()
})

test('a pure maximize: the pre watcher takes the old maximized from its arguments, plays the inset only', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, { heights: { a: 90 } })
    state.maximized = true
    assert.equal(state.maximized, true, 'maximized.value is already new when the pre watcher runs')
    await tick()
    assert.deepEqual(animated(world.log), ['block:inset'], 'no height animation, no fade')
    assert.deepEqual(block.own[0].keyframes, [{ inset: '400px 0 20px 0' }, { inset: '0px 0 0px 0' }])
    assert.equal(wrapper.own.length, 0)
    scope.stop()
})

test('maximize measures the offsets against closest(.session-items-list), padding box (a 1px border changes nothing)', async () => {
    const world = makeWorld()
    world.container.rect = { top: 99, bottom: 501 }
    Object.assign(world.container, { clientTop: 1, clientHeight: 400, offsetHeight: 402 })
    const { block, state, scope } = mountBlock(world)
    block.rect = { top: 400, bottom: 480 }
    state.maximized = true
    await tick()
    assert.deepEqual(block.own[0].keyframes[0], { inset: '300px 0 20px 0' })
    scope.stop()
})

test('a pure restore: attribute, then inline height, then the restore keyframes; both cleared on finish', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, { maximized: true, heights: { a: 90 } })
    wrapper.natural = 90
    state.maximized = false
    await tick()
    const setAt = world.log.findIndex((e) => e[0] === 'setAttribute' && e[2] === 'data-footer-restoring')
    const heightAt = world.log.findIndex((e) => e[0] === 'style.height' && e[2] === '90px')
    const playAt = world.log.findIndex((e) => e[0] === 'animate' && e[2] === 'restore')
    assert.ok(setAt >= 0 && heightAt > setAt && playAt > heightAt, 'attribute → inline height → play')
    assert.deepEqual(animated(world.log), ['block:restore'])
    const discrete = { position: 'absolute', zIndex: 2, height: 'auto', maxHeight: 'none' }
    assert.deepEqual(block.own[0].keyframes, [
        { inset: '0px 0 0px 0', ...discrete },
        { inset: '400px 0 20px 0', ...discrete },
    ])
    assert.ok(wrapper.hasAttribute('data-footer-restoring'))
    block.own[0].finish()
    await settle()
    assert.ok(!wrapper.hasAttribute('data-footer-restoring'), 'attribute removed on finish')
    assert.equal(wrapper.style.height, '', 'inline height cleared on finish')
    scope.stop()
})

test('a restore cancelled from outside clears the attribute and the inline height', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, { maximized: true })
    wrapper.natural = 90
    state.maximized = false
    await tick()
    block.own[0].cancel()
    await settle()
    assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
    assert.equal(wrapper.style.height, '')
    scope.stop()
})

test('a restore interrupting a running maximize starts from the captured offsets, not {0, 0}', async () => {
    const world = makeWorld()
    const { block, state, scope } = mountBlock(world, { heights: { a: 90 } })
    state.maximized = true
    await tick()
    assert.equal(runningOn(block)[0].kind, 'inset')
    // Mid-maximize: the block is drawn part-way up (the rect includes the running animation).
    state.maximized = false
    block.rect = { top: 200, bottom: 480 }
    await tick()
    const restore = block.own.find((a) => a.kind === 'restore')
    assert.deepEqual(restore.keyframes[0].inset, '200px 0 20px 0')
    assert.equal(block.own.find((a) => a.kind === 'inset').running, false, 'cancelAll cancelled the inset on the block')
    assert.deepEqual(runningOn(block).map((a) => a.kind), ['restore'])
    scope.stop()
})

test('an interrupted change reads prev with the running animation and next after cancelAll', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    state.shape = 'b'
    await tick()
    wrapper.animatedPx = 55
    state.shape = 'a'
    await tick()
    const cancelAt = world.log.findIndex((e) => e[0] === 'cancel' && e[2] === 'height')
    const lastMeasure = world.log.findLastIndex((e) => e[0] === 'measure' && e[1] === 'wrapper')
    assert.ok(cancelAt >= 0 && lastMeasure > cancelAt, 'the post measure comes after the cancel')
    const replay = wrapper.own.filter((a) => a.running && a.kind === 'height')
    assert.equal(replay.length, 1)
    assert.deepEqual(replay[0].keyframes, [{ height: '55px', overflowY: 'clip' }, { height: '30px', overflowY: 'clip' }])
    scope.stop()
})

test('a change during a restore reads the natural next and leaves no attribute nor inline height, synchronously', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, {
        maximized: true,
        patch: ({ shape, maximized, wrapper: w, block: b }) => {
            if (maximized) b.classes.add('maximized')
            else b.classes.delete('maximized')
            w.natural = shape === 'collapsed' ? 40 : 90
        },
    })
    wrapper.natural = 90
    state.maximized = false
    await tick()
    assert.equal(wrapper.style.height, '90px')
    assert.equal(runningOn(block).length, 1, 'the restore runs on the block')
    state.shape = 'collapsed'
    await nextTick()
    // No await for the cancelled animation's `finished` (nor for the settle): the cleanup
    // already happened, in the synchronous head of the post watcher.
    assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
    assert.equal(wrapper.style.height, '')
    assert.equal(runningOn(block).length, 0, 'cancelAll cancelled the restore on the block')
    await flush()
    const heightAnim = wrapper.own.find((a) => a.running && a.kind === 'height')
    assert.deepEqual(heightAnim.keyframes.map((k) => k.height), ['90px', '40px'], 'next is the natural 40, not the pinned 90')
    scope.stop()
})

test('combined change table: the four rows', async (t) => {
    await t.test('false → true, shape changed: maximize only', async () => {
        const world = makeWorld()
        const { state, scope } = mountBlock(world, { heights: { a: 40, b: 90 } })
        state.shape = 'b'
        state.maximized = true
        await tick()
        assert.deepEqual(animated(world.log), ['block:inset'])
        scope.stop()
    })
    await t.test('true → false, shape unchanged: restore', async () => {
        const world = makeWorld()
        const { wrapper, state, scope } = mountBlock(world, { maximized: true })
        wrapper.natural = 90
        state.maximized = false
        await tick()
        assert.deepEqual(animated(world.log), ['block:restore'])
        scope.stop()
    })
    await t.test('true → false, shape changed: a fade on the block only', async () => {
        const world = makeWorld()
        const { wrapper, block, state, scope } = mountBlock(world, { maximized: true, heights: { a: 90, b: 40 } })
        state.shape = 'b'
        state.maximized = false
        await tick()
        assert.deepEqual(animated(world.log), ['block:fade'], 'the fade targets the block, never the wrapper')
        assert.equal(wrapper.own.length, 0)
        assert.deepEqual(block.own[0].keyframes, [{ opacity: 0 }, { opacity: 1 }])
        assert.ok(!wrapper.hasAttribute('data-footer-restoring'), 'no restore')
        scope.stop()
    })
    await t.test('false → false, shape changed: height and fade on the wrapper', async () => {
        const world = makeWorld()
        const { state, scope } = mountBlock(world, { heights: { a: 90, b: 40 } })
        state.shape = 'b'
        await tick()
        assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'])
        scope.stop()
    })
})

test('beforeMeasure runs after cancelAll and before the post measure', async () => {
    const world = makeWorld()
    const { state, scope } = mountBlock(world, {
        withBlock: false,
        heights: { open: 120, collapsed: 40 },
        shape: 'open',
        beforeMeasure: () => world.log.push(['beforeMeasure']),
    })
    state.shape = 'collapsed'
    await tick()
    state.shape = 'open'
    await tick()
    const at = world.log.findLastIndex((e) => e[0] === 'beforeMeasure')
    const cancelAt = world.log.findLastIndex((e) => e[0] === 'cancel')
    const measureAt = world.log.findLastIndex((e) => e[0] === 'measure' && e[1] === 'wrapper')
    assert.ok(cancelAt >= 0 && at > cancelAt && measureAt > at, 'cancel → beforeMeasure → measure')
    scope.stop()
})

test('reduced motion: height and inset snap, the fade stays', async (t) => {
    await t.test('shape change', async () => {
        const world = makeWorld({ reduced: true })
        const { state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
        state.shape = 'b'
        await tick()
        assert.deepEqual(animated(world.log), ['wrapper:fade'])
        scope.stop()
    })
    await t.test('maximize and restore fade the block', async () => {
        const world = makeWorld({ reduced: true })
        const { wrapper, state, scope } = mountBlock(world)
        state.maximized = true
        await tick()
        state.maximized = false
        await tick()
        assert.deepEqual(animated(world.log), ['block:fade', 'block:fade'])
        assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
        scope.stop()
    })
    await t.test('enter and leave', async () => {
        const world = makeWorld({ reduced: true })
        const el = makeElement('entering', world.log, { natural: 60 })
        world.motion.footerEnter(el, () => {})
        await flush()
        world.motion.footerLeave(el, () => {})
        assert.deepEqual(animated(world.log), ['entering:fade', 'entering:fade'])
    })
})

test('enabled false: nothing plays, footerLeave and footerEnter call done() at once', async () => {
    const world = makeWorld({ enabled: false })
    const { state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    state.shape = 'b'
    await tick()
    state.maximized = true
    await tick()
    const el = makeElement('leaving', world.log, { natural: 60 })
    let done = 0
    world.motion.footerLeave(el, () => done++)
    world.motion.footerEnter(makeElement('entering', world.log, { natural: 60 }), () => done++)
    assert.equal(done, 2, 'done() synchronously')
    assert.deepEqual(animated(world.log), [])
    scope.stop()
})

test('a hidden wrapper (display: none ancestor) plays nothing', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    wrapper.visible = false
    state.shape = 'b'
    await tick()
    let done = 0
    const hidden = makeElement('entering', world.log, { natural: 60, visible: false })
    world.motion.footerEnter(hidden, () => done++)
    assert.equal(done, 1)
    assert.deepEqual(animated(world.log), [])
    // Maximize, then restore, of a hidden block: no inset, no restore, no attribute.
    const maxi = mountBlock(world, { heights: { a: 90 } })
    maxi.wrapper.visible = false
    maxi.state.maximized = true
    await tick()
    assert.deepEqual(animated(world.log), [], 'a hidden maximize plays nothing')
    maxi.state.maximized = false
    await tick()
    assert.deepEqual(animated(world.log), [], 'a hidden restore plays nothing')
    assert.ok(!maxi.wrapper.hasAttribute('data-footer-restoring'), 'a hidden restore sets no attribute')
    assert.equal(maxi.wrapper.style.height, '')
    // Leave of a hidden block, normal and with a maximized descendant: done() at once.
    const leaving = makeElement('leaving', world.log, { natural: 60, visible: false })
    world.motion.footerLeave(leaving, () => done++)
    assert.equal(done, 2, 'a hidden leave calls done() at once')
    const leavingMaxi = makeElement('leavingMaxi', world.log, { natural: 0.8, visible: false })
    const form = makeElement('form', world.log, { natural: 500 })
    form.classes.add('maximized')
    leavingMaxi.children.push(form)
    world.motion.footerLeave(leavingMaxi, () => done++)
    assert.equal(done, 3, 'a hidden maximized leave calls done() at once')
    assert.deepEqual(animated(world.log), [])
    scope.stop()
    maxi.scope.stop()
})

test('cancelAll leaves a foreign animation on the wrapper alone', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    const foreign = makeAnimation(wrapper, [{ opacity: 1 }, { opacity: 1 }], {}, world.log)
    wrapper.foreign.push(foreign)
    state.shape = 'b'
    await tick()
    state.shape = 'a'
    await tick()
    assert.ok(foreign.running, 'a CSS animation or transition is never cancelled')
    scope.stop()
})

// ── Per-block animating and onSettled ───────────────────────────────────────

test('animating is per block; onSettled runs once, after the count is decremented', async () => {
    const world = makeWorld()
    const calls = []
    const { wrapper, state, scope, api } = mountBlock(world, {
        heights: { a: 30, b: 80 },
        onSettled: () => calls.push(api.animating.value),
    })
    const other = mountBlock(world, { heights: { a: 10, b: 20 } })
    assert.equal(api.animating.value, false)
    state.shape = 'b'
    other.state.shape = 'b'
    await tick()
    assert.equal(api.animating.value, true)
    assert.equal(world.motion.animating.value, true)
    wrapper.own[0].finish()
    await settle()
    assert.deepEqual(calls, [], 'one of two animations settled: not yet')
    wrapper.own[1].fail()
    await settle()
    assert.deepEqual(calls, [false], 'called once, animating already false')
    assert.equal(world.motion.animating.value, true, 'the other block still runs')
    finishAll(other.wrapper)
    await settle()
    assert.equal(world.motion.animating.value, false)
    // Cancel then replay: onSettled waits for the replay.
    state.shape = 'a'
    await tick()
    state.shape = 'b'
    await tick()
    await settle()
    assert.deepEqual(calls, [false], 'the cancelled pair does not settle the block: the replay runs')
    finishAll(wrapper)
    await settle()
    assert.deepEqual(calls, [false, false])
    scope.stop()
    other.scope.stop()
})

test('a block attached to a wrapper already entering starts with animating true', async () => {
    const world = makeWorld()
    const wrapper = makeElement('wrapper', world.log, { natural: 64 })
    world.motion.footerEnter(wrapper, () => {})
    await flush()
    const scope = effectScope()
    const api = scope.run(() => useFooterBlockMotion({
        wrapperRef: shallowRef(wrapper),
        shape: computed(() => 'a'),
        motion: world.motion,
    }))
    assert.equal(api.animating.value, true, 'the running enter counts at attach')
    finishAll(wrapper)
    await settle()
    assert.equal(api.animating.value, false)
    scope.stop()
})

test('no controller: the block does nothing and animating stays false', async () => {
    const wrapper = { getBoundingClientRect() { throw new Error('measured') } }
    const shape = ref('a')
    const scope = effectScope()
    const api = scope.run(() => useFooterBlockMotion({ wrapperRef: ref(wrapper), shape: computed(() => shape.value), motion: null }))
    shape.value = 'b'
    await tick()
    assert.equal(api.animating.value, false)
    assert.equal(typeof FOOTER_MOTION_KEY, 'symbol')
    scope.stop()
})

// ── Mount and unmount ────────────────────────────────────────────────────────

test('footerEnter plays height 0 → natural and the fade; done after BOTH finish', async () => {
    const world = makeWorld()
    const el = makeElement('entering', world.log, { natural: 64 })
    let done = 0
    world.motion.footerEnter(el, () => done++)
    await flush()
    assert.deepEqual(animated(world.log), ['entering:height', 'entering:fade'])
    assert.deepEqual(el.own[0].keyframes, [{ height: '0px', overflowY: 'clip' }, { height: '64px', overflowY: 'clip' }])
    assert.deepEqual(el.own[1].keyframes, [{ opacity: 0 }, { opacity: 1 }])
    el.own[1].finish()
    await settle()
    assert.equal(done, 0, 'the fade alone is not enough')
    el.own[0].finish()
    await settle()
    assert.equal(done, 1)
})

test('a rejected finished still calls done()', async () => {
    const world = makeWorld()
    const el = makeElement('entering', world.log, { natural: 64 })
    let done = 0
    world.motion.footerEnter(el, () => done++)
    await flush()
    assert.equal(el.own.length, 2)
    el.own.forEach((a) => a.fail())
    await settle()
    assert.equal(done, 1)
    const leaving = makeElement('leaving', world.log, { natural: 64 })
    world.motion.footerLeave(leaving, () => done++)
    leaving.own.forEach((a) => a.fail())
    await settle()
    assert.equal(done, 2)
})

test('footerLeave: inert, data-footer-leaving, height current → 0 and fade 1 → 0, done after both', async () => {
    const world = makeWorld()
    const el = makeElement('leaving', world.log, { natural: 70 })
    let done = 0
    world.motion.footerLeave(el, () => done++)
    assert.equal(el.inert, true)
    assert.ok(el.hasAttribute('data-footer-leaving'))
    assert.deepEqual(el.own.map((a) => a.keyframes), [
        [{ height: '70px', overflowY: 'clip' }, { height: '0px', overflowY: 'clip' }],
        [{ opacity: 1 }, { opacity: 0 }],
    ])
    el.own[0].finish()
    await settle()
    assert.equal(done, 0)
    el.own[1].finish()
    await settle()
    assert.equal(done, 1)
})

test('footerLeave of a maximized block fades the descendant only, even with a 0.8px wrapper', async () => {
    const world = makeWorld()
    const el = makeElement('leaving', world.log, { natural: 0.8 })
    const form = makeElement('form', world.log, { natural: 500 })
    form.classes.add('maximized')
    el.children.push(form)
    let done = 0
    world.motion.footerLeave(el, () => done++)
    assert.deepEqual(animated(world.log), ['form:fade'], 'never the wrapper')
    assert.deepEqual(form.own[0].keyframes, [{ opacity: 1 }, { opacity: 0 }])
    await settle()
    assert.equal(done, 0, 'done waits for the fade')
    form.own[0].finish()
    await settle()
    assert.equal(done, 1)
})

test('footerLeave during an enter: capturePin, then the measure (animated height), then cancelAll; state survives scope disposal', async () => {
    const world = makeWorld()
    const { wrapper, scope } = mountBlock(world, { withBlock: false, heights: { a: 30, b: 80 } })
    wrapper.natural = 30
    world.motion.footerEnter(wrapper, () => {})
    await flush()
    wrapper.animatedPx = 22
    scope.stop()
    const captureAt = world.log.length
    world.motion.footerLeave(wrapper, () => {})
    const tail = world.log.slice(captureAt)
    const measureAt = tail.findIndex((e) => e[0] === 'measure' && e[1] === 'wrapper')
    const cancelAt = tail.findIndex((e) => e[0] === 'cancel')
    assert.ok(measureAt >= 0 && cancelAt > measureAt, 'measure before cancelAll')
    const leave = wrapper.own.filter((a) => a.running && a.kind === 'height')
    assert.deepEqual(leave[0].keyframes[0].height, '22px', 'starts from the animated height')
    assert.equal(wrapper.own.filter((a) => a.running).length, 2, 'the enter animations were cancelled')
})

test('footerLeave during a restore leaves neither attribute nor inline height (scope disposed first)', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, { maximized: true })
    wrapper.natural = 90
    state.maximized = false
    await tick()
    assert.ok(wrapper.hasAttribute('data-footer-restoring'))
    assert.equal(runningOn(block).length, 1, 'the restore runs on the block')
    scope.stop()
    world.motion.footerLeave(wrapper, () => {})
    assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
    assert.equal(wrapper.style.height, '')
    assert.equal(runningOn(block).length, 0, 'cancelAll cancelled the restore on the block')
    const leave = wrapper.own.find((a) => a.running && a.kind === 'height')
    assert.equal(leave.keyframes[0].height, '90px', 'the measured start, taken before the cleanup')
})

test('footerLeave calls capturePin before its measure', () => {
    const world = makeWorld()
    const el = makeElement('leaving', world.log, { natural: 70 })
    const scrollerMeasure = world.scroller
    let captured = null
    Object.defineProperty(scrollerMeasure, 'scrollTop', {
        get() {
            captured ??= measures(world.log, 'leaving')
            return 600
        },
        set() {},
        configurable: true,
    })
    world.motion.footerLeave(el, () => {})
    assert.equal(captured, 0, 'the scroller was read before the leaving element was measured')
})

test('vFooterEnter plays the enter at mounted, after the render settles; nothing when disabled', async () => {
    const world = makeWorld()
    const el = makeElement('banner', world.log, { natural: 33.6 })
    el.children.push(makeWa('button', world.log, () => Promise.resolve().then(() => {
        el.natural = 46
    })))
    world.motion.vFooterEnter.mounted(el)
    await flush()
    assert.deepEqual(animated(world.log), ['banner:height', 'banner:fade'])
    assert.equal(el.own[0].keyframes[1].height, '46px', 'the settled natural height')
    world.enabled.value = false
    const other = makeElement('banner2', world.log, { natural: 50 })
    world.motion.vFooterEnter.mounted(other)
    await flush()
    assert.equal(other.own.length, 0)
})

// ── Chat pin ─────────────────────────────────────────────────────────────────

test('the pin connects once for two overlapping animations, disconnects after both end', async () => {
    const world = makeWorld()
    world.motion.capturePin()
    const a = makeElement('a', world.log, { natural: 50 })
    const b = makeElement('b', world.log, { natural: 50 })
    world.motion.footerEnter(a, () => {})
    world.motion.footerEnter(b, () => {})
    await flush()
    assert.equal(world.env.observers.length, 1)
    assert.ok(world.env.observers[0].targets.has(world.scroller))
    finishAll(a)
    await settle()
    assert.equal(world.env.observers[0].disconnected, false)
    finishAll(b)
    await settle()
    assert.equal(world.env.observers[0].disconnected, true)
    assert.equal(world.scroller.listenerCount(), 0, 'gesture listeners removed with the observer')
})

test('the pin keeps the gap read before the change, not a snap to the bottom', async () => {
    const world = makeWorld()
    world.scroller.scrollTop = 500 // 100px above the bottom (inside the 150px zone)
    const { state, scope } = mountBlock(world, {
        patch: ({ shape, wrapper }) => {
            wrapper.natural = shape === 'b' ? 300 : 30
            world.scroller.scrollTop = 330 // the browser clamps once the footer grows
        },
    })
    state.shape = 'b'
    await tick()
    const observer = world.env.observers[0]
    world.scroller.clientHeight = 130
    observer.callback()
    assert.equal(world.scroller.scrollTop, 1000 - 130 - 100, 'the pre-change gap, not the clamped one')
    // cancelAll followed by a replay does not re-capture.
    world.env.runFrames()
    world.scroller.scrollTop = 0
    state.shape = 'a'
    await tick()
    assert.ok(world.log.some((e) => e[0] === 'cancel'), 'the first change was cancelled and replayed')
    assert.equal(world.env.observers.length, 1, 'still the same observer')
    observer.callback()
    assert.equal(world.scroller.scrollTop, 1000 - 130 - 100)
    scope.stop()
})

test('a user gesture disconnects the pin: wheel, touchstart, keydown, pointerdown on the scroller', async () => {
    for (const type of ['wheel', 'touchstart', 'keydown', 'pointerdown']) {
        const world = makeWorld()
        world.motion.capturePin()
        const el = makeElement('a', world.log, { natural: 50 })
        world.motion.footerEnter(el, () => {})
        await flush()
        world.scroller.dispatch(type)
        assert.equal(world.env.observers[0].disconnected, true, type)
        assert.equal(world.scroller.listenerCount(), 0, `${type}: listeners removed`)
    }
})

test('disposing the controller scope disconnects a connected pin', async () => {
    const scope = effectScope()
    const world = scope.run(() => makeWorld())
    world.motion.capturePin()
    world.motion.footerEnter(makeElement('a', world.log, { natural: 50 }), () => {})
    await flush()
    assert.equal(world.env.observers.length, 1)
    assert.equal(world.env.observers[0].disconnected, false)
    assert.ok(world.scroller.listenerCount() > 0)
    scope.stop()
    assert.equal(world.env.observers[0].disconnected, true)
    assert.equal(world.scroller.listenerCount(), 0, 'gesture listeners removed')
})

test('a pointerdown on a child of the scroller does not disconnect the pin', async () => {
    const world = makeWorld()
    world.motion.capturePin()
    world.motion.footerEnter(makeElement('a', world.log, { natural: 50 }), () => {})
    await flush()
    assert.equal(world.env.observers.length, 1)
    world.scroller.dispatch('pointerdown', { name: 'a child' })
    assert.equal(world.env.observers[0].disconnected, false)
})

test('no pin when the chat is not at the bottom, or when pinEnabled is false', async () => {
    for (const flags of [{ atBottom: false }, { pinEnabled: false }]) {
        const world = makeWorld(flags)
        world.motion.capturePin()
        world.motion.footerEnter(makeElement('a', world.log, { natural: 50 }), () => {})
        await flush()
        assert.equal(animated(world.log).length, 2, 'the enter played')
        assert.equal(world.env.observers.length, 0)
    }
})

test('the capture slot keeps the first capture of a batch and is cleared by the next frame', async () => {
    const world = makeWorld()
    world.scroller.scrollTop = 580
    world.motion.capturePin()
    world.scroller.scrollTop = 500
    world.motion.capturePin()
    world.motion.footerEnter(makeElement('a', world.log, { natural: 50 }), () => {})
    await flush()
    world.env.observers[0].callback()
    assert.equal(world.scroller.scrollTop, 1000 - 400 - 20, 'the first capture (gap 20) wins')

    const later = makeWorld()
    later.motion.capturePin()
    later.env.runFrames()
    later.motion.footerEnter(makeElement('a', later.log, { natural: 50 }), () => {})
    await flush()
    assert.equal(animated(later.log).length, 2, 'the enter played')
    assert.equal(later.env.observers.length, 0, 'the frame cleared the slot')
})

test('a capture then footerEnter connects the pin with the captured gap', async () => {
    const world = makeWorld()
    world.scroller.scrollTop = 560
    world.motion.capturePin()
    world.motion.footerEnter(makeElement('a', world.log, { natural: 50 }), () => {})
    await flush()
    world.scroller.scrollHeight = 1200
    world.env.observers[0].callback()
    assert.equal(world.scroller.scrollTop, 1200 - 400 - 40)
})

// ── Render settle (§9) ───────────────────────────────────────────────────────

// Resolves to 'timer' when a timer fires first: settleRender must end in microtasks only.
const raceTimer = (promise) => Promise.race([promise.then(() => 'settled'), flush().then(() => 'timer')])

test('settleRender awaits top-level and shadow-nested wa-* elements, in microtasks only', async () => {
    const log = []
    const rendered = []
    const root = makeElement('root', log)
    const top = makeWa('top', log, () => Promise.resolve().then(() => rendered.push('top')))
    const inner = makeWa('inner', log, () => Promise.resolve().then(() => rendered.push('inner')))
    const host = makeElement('host', log)
    host.shadowRoot = { querySelectorAll: (selector) => (selector === '*' ? [inner] : []) }
    root.children.push(top, host)
    assert.equal(await raceTimer(settleRender(root, makeEnv())), 'settled', 'no timer, no frame')
    assert.deepEqual(rendered.sort(), ['inner', 'top'])
})

test('settleRender ignores non-wa- elements and swallows a rejecting thenable', async () => {
    const log = []
    const root = makeElement('root', log)
    // Not a wa- element: never awaited (it would never resolve).
    const plain = makeElement('plain', log, { updateComplete: new Promise(() => {}) })
    const failing = makeWa('failing', log, () => ({ then: (resolve, reject) => reject(new Error('render failed')) }))
    const good = makeWa('good', log)
    root.children.push(plain, failing, good)
    assert.equal(await raceTimer(settleRender(root, makeEnv())), 'settled')
    assert.equal(failing.reads, 1)
    assert.equal(good.reads, 1)
})

test('settleRender repeats for wa-* elements created by a render, at most 4 rounds', async () => {
    const log = []
    // Every render creates a nested wa- element: an endless chain, cut after 4 rounds.
    const endless = makeElement('root', log)
    const spawned = []
    const spawn = (parent) => {
        const el = makeWa(`wa${spawned.length}`, log, () => Promise.resolve().then(() => spawn(el)))
        spawned.push(el)
        parent.children.push(el)
    }
    spawn(endless)
    assert.equal(await raceTimer(settleRender(endless, makeEnv())), 'settled')
    assert.deepEqual(spawned.map((el) => el.reads), [1, 1, 1, 1, 0], 'four rounds, each element awaited once')
    // One nested element: two rounds, the third finds nothing new and stops.
    const once = makeElement('root', log)
    const child = makeWa('child', log)
    const parent = makeWa('parent', log, () => Promise.resolve().then(() => {
        if (!parent.children.length) parent.children.push(child)
    }))
    once.children.push(parent)
    await settleRender(once, makeEnv())
    assert.equal(parent.reads, 1, 'an element already awaited is not awaited again')
    assert.equal(child.reads, 1)
})

test('the post watcher measures nextPx after the Web Awesome elements have rendered', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 120, b: 33.6 } })
    // The bar's wa-button renders in a microtask: 33.6px before, 46px after (Firefox 156 probe).
    wrapper.children.push(makeWa('button', world.log, () => Promise.resolve().then(() => {
        if (state.shape === 'b') wrapper.natural = 46
    })))
    state.shape = 'b'
    await tick()
    const height = wrapper.own.find((a) => a.kind === 'height')
    assert.deepEqual(height.keyframes.map((k) => k.height), ['120px', '46px'], 'the settled height, not 33.6')
    scope.stop()
})

test('footerEnter measures the natural height after the render settles', async () => {
    const world = makeWorld()
    const el = makeElement('entering', world.log, { natural: 33.6 })
    el.children.push(makeWa('button', world.log, () => Promise.resolve().then(() => {
        el.natural = 46
    })))
    let done = 0
    world.motion.footerEnter(el, () => done++)
    await flush()
    assert.deepEqual(el.own[0].keyframes.map((k) => k.height), ['0px', '46px'])
    finishAll(el)
    await flush()
    assert.equal(done, 1)
})

test('the restore measures target and H after the render settles', async () => {
    const world = makeWorld()
    const { wrapper, block, state, scope } = mountBlock(world, {
        maximized: true,
        patch: ({ maximized, wrapper: w, block: b }) => {
            if (maximized) b.classes.add('maximized')
            else b.classes.delete('maximized')
            w.natural = 60
            b.rect = { top: 420, bottom: 480 }
        },
    })
    // The render lands once the wrapper is restoring (after the post watcher's own settle).
    wrapper.children.push(makeWa('button', world.log, () => Promise.resolve().then(() => {
        if (!wrapper.hasAttribute('data-footer-restoring')) return
        wrapper.natural = 90
        block.rect = { top: 390, bottom: 480 }
    })))
    state.maximized = false
    await tick()
    assert.equal(wrapper.style.height, '90px', 'H after the settle')
    const restore = block.own.find((a) => a.kind === 'restore')
    assert.equal(restore.keyframes[1].inset, '390px 0 20px 0', 'target after the settle')
    scope.stop()
})

test('a change or a leave landing while the restore waits for the render: the restore never plays', async (t) => {
    const setup = () => {
        const world = makeWorld()
        const mounted = mountBlock(world, {
            maximized: true,
            patch: ({ shape, maximized, wrapper: w, block: b }) => {
                if (maximized) b.classes.add('maximized')
                else b.classes.delete('maximized')
                w.natural = shape === 'collapsed' ? 40 : 90
            },
        })
        mounted.wrapper.natural = 90
        const render = deferred()
        mounted.wrapper.children.push(makeWa('button', world.log, () => (
            mounted.wrapper.hasAttribute('data-footer-restoring') ? render.promise : Promise.resolve()
        )))
        return { world, render, ...mounted }
    }
    await t.test('a shape change', async () => {
        const { world, render, wrapper, state, scope } = setup()
        state.maximized = false
        await tick()
        assert.ok(wrapper.hasAttribute('data-footer-restoring'), 'the restore waits, attribute set')
        state.shape = 'collapsed'
        await tick()
        render.resolve()
        await flush()
        assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'the newer change only')
        assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
        assert.equal(wrapper.style.height, '')
        scope.stop()
    })
    await t.test('a leave', async () => {
        const { world, render, wrapper, state, scope } = setup()
        state.maximized = false
        await tick()
        scope.stop()
        world.motion.footerLeave(wrapper, () => {})
        render.resolve()
        await flush()
        assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'the leave only')
        assert.ok(!wrapper.hasAttribute('data-footer-restoring'))
        assert.equal(wrapper.style.height, '')
    })
})

test('a stale generation plays nothing: the newer change owns the block', async () => {
    const world = makeWorld()
    // Three heights: the stale run (30 → 50 once resumed) would have a real delta to play.
    const { wrapper, state, scope, api } = mountBlock(world, { heights: { a: 30, b: 80, c: 50 } })
    const first = deferred()
    let reads = 0
    wrapper.children.push(makeWa('button', world.log, () => (reads++ === 0 ? first.promise : Promise.resolve())))
    state.shape = 'b'
    await tick()
    assert.deepEqual(animated(world.log), [], 'the first run waits for the render')
    state.shape = 'c'
    await tick()
    assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'the newer run plays')
    // From the pending run's prevPx (§9.1): the screen never showed the 80px.
    assert.deepEqual(wrapper.own[0].keyframes.map((k) => k.height), ['30px', '50px'])
    first.resolve()
    await flush()
    assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'the stale run plays nothing')
    finishAll(wrapper)
    await flush()
    assert.equal(api.animating.value, false)
    assert.equal(world.motion.animating.value, false, 'the counters balance')
    scope.stop()
})

test('a change starting while the previous run waits for the render animates from the FIRST prevPx (§9.1)', async (t) => {
    // The first run (a → b) waits for a slow render; the second change (b → c) lands meanwhile.
    // No frame was painted: the screen still shows 30px although the DOM already measures 80.
    const setup = () => {
        const world = makeWorld()
        const mounted = mountBlock(world, { heights: { a: 30, b: 80, c: 50 } })
        const first = deferred()
        let reads = 0
        mounted.wrapper.children.push(makeWa('button', world.log, () => (reads++ === 0 ? first.promise : Promise.resolve())))
        return { world, first, ...mounted }
    }
    const heights = (animation) => animation.keyframes.map((k) => k.height)
    const lastHeight = (wrapper) => wrapper.own.filter((a) => a.kind === 'height').at(-1)

    await t.test('the second change starts from the first prevPx; cleared once it played', async () => {
        const { world, first, wrapper, state, scope } = setup()
        state.shape = 'b'
        await tick()
        assert.deepEqual(animated(world.log), [], 'the first run waits for the render')
        const measured = measures(world.log, 'wrapper')
        state.shape = 'c'
        await tick()
        assert.equal(measures(world.log, 'wrapper'), measured + 1, 'the post measure only: the pre run used the stored prevPx')
        assert.deepEqual(heights(lastHeight(wrapper)), ['30px', '50px'], 'from the FIRST prevPx, not the DOM 80px')
        // The stale first run resumes: it plays nothing and does not clear a newer value.
        first.resolve()
        await flush()
        assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'])
        finishAll(wrapper)
        await flush()
        // Cleared: the next change measures again.
        state.shape = 'a'
        await tick()
        assert.deepEqual(heights(lastHeight(wrapper)), ['50px', '30px'])
        scope.stop()
    })

    await t.test('a run that bails out (disabled) clears it', async () => {
        const { world, first, wrapper, state, scope } = setup()
        state.shape = 'b'
        await tick()
        world.enabled.value = false
        state.shape = 'c'
        await tick()
        world.enabled.value = true
        assert.deepEqual(animated(world.log), [], 'nothing played')
        // The first run still waits: only the bail-out itself can have cleared the value.
        state.shape = 'a'
        await tick()
        assert.deepEqual(heights(lastHeight(wrapper)), ['50px', '30px'], 'measured, not the stale 30px')
        first.resolve()
        await flush()
        assert.equal(wrapper.own.length, 2, 'the stale first run plays nothing')
        scope.stop()
    })

    await t.test('a stale run resuming does not clear the value of a newer pending run', async () => {
        const world = makeWorld()
        const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80, c: 50, d: 70 } })
        const renders = [deferred(), deferred()]
        let reads = 0
        wrapper.children.push(makeWa('button', world.log, () => (renders[reads++]?.promise ?? Promise.resolve())))
        state.shape = 'b'
        await tick()
        state.shape = 'c'
        await tick()
        // The first run resumes, stale, while the second still waits.
        renders[0].resolve()
        await flush()
        assert.deepEqual(animated(world.log), [])
        state.shape = 'd'
        await tick()
        assert.deepEqual(heights(lastHeight(wrapper)), ['30px', '70px'], 'still from the first prevPx: nothing was painted')
        renders[1].resolve()
        await flush()
        assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'the stale second run plays nothing')
        scope.stop()
    })
})

test('a restore interrupting a running maximize keeps the pin: one observer, never disconnected', async () => {
    const world = makeWorld()
    const { block, state, scope } = mountBlock(world, { heights: { a: 90 } })
    state.maximized = true
    await tick()
    assert.equal(world.env.observers.length, 1, 'the maximize connected the pin')
    // Frames pass mid-maximize: the capture slot is cleared.
    world.env.runFrames()
    state.maximized = false
    block.rect = { top: 200, bottom: 480 }
    await tick()
    assert.deepEqual(runningOn(block).map((a) => a.kind), ['restore'])
    assert.equal(world.env.observers.length, 1, 'still one observer')
    assert.equal(world.env.observers[0].disconnected, false, 'never disconnected between the maximize and the restore')
    finishAll(block)
    await settle()
    assert.equal(world.env.observers[0].disconnected, true, 'disconnected once the restore ends')
    scope.stop()
})

test('a change made while enabled is false still cancels the running animations first', async () => {
    const world = makeWorld()
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    state.shape = 'b'
    await tick()
    assert.equal(runningOn(wrapper).length, 2, 'a → b animates')
    world.enabled.value = false
    state.shape = 'a'
    await tick()
    assert.equal(runningOn(wrapper).length, 0, 'nothing runs on the wrapper')
    assert.deepEqual(animated(world.log), ['wrapper:height', 'wrapper:fade'], 'nothing new played')
    scope.stop()
})

test('vFooterEnter.mounted returns the promise of footerEnter', async () => {
    const world = makeWorld()
    const el = makeElement('banner', world.log, { natural: 40 })
    const result = world.motion.vFooterEnter.mounted(el)
    assert.ok(result instanceof Promise, 'a throw is not an unhandled rejection')
    await result
    assert.deepEqual(animated(world.log), ['banner:height', 'banner:fade'])
})

test('a change that waits for the render keeps the block counted: no onSettled, no pin drop, until the replay ends', async () => {
    const world = makeWorld()
    const calls = []
    const { wrapper, state, scope, api } = mountBlock(world, {
        heights: { a: 30, b: 80, c: 50 },
        onSettled: () => calls.push(api.animating.value),
    })
    world.motion.capturePin()
    state.shape = 'b'
    await tick()
    assert.equal(world.env.observers.length, 1)
    // The next change cancels the running pair, then waits for a slow render; a third change
    // arrives meanwhile and makes it stale. It goes to a third height: it starts from the
    // pending run's 80px (§9.1), so back to 'b' would have nothing to play.
    const slow = deferred()
    let reads = 0
    wrapper.children.push(makeWa('button', world.log, () => (reads++ === 0 ? slow.promise : Promise.resolve())))
    state.shape = 'a'
    await tick()
    assert.deepEqual(calls, [], 'the cancelled pair settled, the pending run still holds the block')
    assert.equal(api.animating.value, true)
    assert.equal(world.env.observers[0].disconnected, false, 'the pin stays connected')
    state.shape = 'c'
    await tick()
    assert.deepEqual(wrapper.own.filter((a) => a.running && a.kind === 'height').map((a) => a.keyframes.map((k) => k.height)), [['80px', '50px']])
    slow.resolve()
    await flush()
    assert.deepEqual(calls, [])
    assert.equal(world.env.observers.length, 1, 'never re-captured')
    finishAll(wrapper)
    await flush()
    assert.deepEqual(calls, [false], 'once, when the last animation settles')
    assert.equal(world.motion.animating.value, false)
    assert.equal(world.env.observers[0].disconnected, true)
    scope.stop()
})

test('a leave arriving before a pending enter has rendered cancels the enter', async () => {
    const world = makeWorld()
    const el = makeElement('entering', world.log, { natural: 64 })
    const render = deferred()
    el.children.push(makeWa('button', world.log, () => render.promise))
    let entered = 0
    let left = 0
    world.motion.footerEnter(el, () => entered++)
    await flush()
    assert.deepEqual(animated(world.log), [])
    world.motion.footerLeave(el, () => left++)
    render.resolve()
    await flush()
    assert.deepEqual(el.own.map((a) => a.keyframes[0]), [{ height: '64px', overflowY: 'clip' }, { opacity: 1 }], 'the leave only')
    assert.equal(entered, 1, 'the stale enter still hands its done back to the Transition')
    finishAll(el)
    await flush()
    assert.equal(left, 1)
    assert.equal(world.motion.animating.value, false)
})

test('an element hidden once the render settles plays nothing', async () => {
    const world = makeWorld()
    // Shape change.
    const { wrapper, state, scope } = mountBlock(world, { heights: { a: 30, b: 80 } })
    wrapper.children.push(makeWa('button', world.log, () => Promise.resolve().then(() => {
        wrapper.visible = false
    })))
    state.shape = 'b'
    await tick()
    // Enter.
    const el = makeElement('entering', world.log, { natural: 64 })
    el.children.push(makeWa('button2', world.log, () => Promise.resolve().then(() => {
        el.visible = false
    })))
    let done = 0
    world.motion.footerEnter(el, () => done++)
    await flush()
    assert.equal(done, 1)
    // Restore.
    const maxi = mountBlock(world, { maximized: true })
    maxi.wrapper.natural = 90
    maxi.wrapper.children.push(makeWa('button3', world.log, () => Promise.resolve().then(() => {
        if (maxi.wrapper.hasAttribute('data-footer-restoring')) maxi.wrapper.visible = false
    })))
    maxi.state.maximized = false
    await tick()
    assert.deepEqual(animated(world.log), [])
    assert.ok(!maxi.wrapper.hasAttribute('data-footer-restoring'))
    assert.equal(maxi.wrapper.style.height, '')
    assert.equal(world.motion.animating.value, false)
    scope.stop()
    maxi.scope.stop()
})

// ── Session-switch gate ──────────────────────────────────────────────────────

test('createSwitchGate: arm() closes, releases after nextTick then a requestAnimationFrame, never earlier', async () => {
    const env = makeEnv()
    const gate = createSwitchGate({ env })
    gate.arm()
    assert.equal(gate.open.value, false)
    assert.equal(env.frames.length, 0, 'the frame is requested after nextTick')
    await nextTick()
    assert.equal(gate.open.value, false)
    assert.equal(env.frames.length, 1)
    env.runFrames()
    assert.equal(gate.open.value, true)
})

test('createSwitchGate: a second arm() before the release keeps the gate closed until its own frame', async () => {
    const env = makeEnv()
    const gate = createSwitchGate({ env })
    gate.arm()
    await nextTick()
    gate.arm()
    env.runFrames()
    assert.equal(gate.open.value, false, 'the first release is stale')
    await nextTick()
    env.runFrames()
    assert.equal(gate.open.value, true)
})
