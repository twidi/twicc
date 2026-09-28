// Run with: node --test src/composables/useGlideInk.test.js (from the frontend dir)
// Gliding indicators (visual refresh step 4c, docs/plans/2026-09-28-gliding-indicators-design.md):
// the composable's controller lifecycle, driven in an effect scope with a fake environment.
import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'

import { useGlideInk } from './useGlideInk.js'

function makeEnv() {
    const env = { observers: [], timers: [], frames: [], nextId: 1 }
    env.setTimeout = () => env.nextId++
    env.clearTimeout = () => {}
    env.requestAnimationFrame = () => env.nextId++
    env.cancelAnimationFrame = () => {}
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
    env.flushes = []
    env.getComputedStyle = (element, pseudo) => {
        env.flushes.push([element, pseudo])
        return { getPropertyValue: () => '' }
    }
    return env
}

function makeElement(name, top = 0) {
    return {
        name,
        top,
        isConnected: true,
        scrollLeft: 0, scrollTop: 0, clientLeft: 0, clientTop: 0,
        offsetWidth: 200, offsetHeight: 100,
        attrs: new Set(),
        props: new Map(),
        style: {
            setProperty(prop, value) { this.owner.props.set(prop, value) },
            removeProperty(prop) { this.owner.props.delete(prop) },
        },
        getBoundingClientRect() { return { left: 0, top: this.top, width: 200, height: this.name.startsWith('row') ? 20 : 100 } },
        setAttribute(attr) { this.attrs.add(attr) },
        removeAttribute(attr) { this.attrs.delete(attr) },
    }
}
const element = (name, top) => {
    const el = makeElement(name, top)
    el.style.owner = el
    return el
}

function setup(options = {}) {
    const env = makeEnv()
    const container = ref(null)
    const ink = ref(element('ink'))
    const rows = [element('row0', 0), element('row1', 20), element('row2', 40)]
    const index = ref(0)
    const scope = effectScope()
    scope.run(() => useGlideInk({
        container,
        flushTarget: ink,
        getActive: () => rows[index.value],
        getItems: () => rows,
        sources: [index, ...(options.extraSources ?? [])],
        env,
        flushPseudo: options.flushPseudo,
        resetKey: options.resetKey,
    }))
    return { env, container, ink, rows, index, scope }
}

test('the controller is created when the container ref gets an element', async () => {
    const { env, container, scope } = setup()
    assert.equal(env.observers.length, 0, 'no element: no controller')
    const list = element('list')
    container.value = list
    await nextTick()
    assert.equal(env.observers.length, 1, 'env reaches the controller')
    assert.ok(list.attrs.has('data-glide-ready'), 'the first placement ran')
    assert.equal(list.props.get('--glide-y'), '0px')
    scope.stop()
})

test('recreated when the element changes, destroyed when it becomes null and when the scope stops', async () => {
    const { env, container, scope } = setup()
    const first = element('list')
    container.value = first
    await nextTick()
    const second = element('list2')
    container.value = second
    await nextTick()
    assert.equal(env.observers.length, 2)
    assert.ok(env.observers[0].disconnected, 'old controller destroyed')
    assert.equal(first.props.size, 0)
    assert.ok(second.attrs.has('data-glide-ready'))

    container.value = null
    await nextTick()
    assert.ok(env.observers[1].disconnected)
    assert.equal(second.attrs.size, 0)

    const third = element('list3')
    container.value = third
    await nextTick()
    scope.stop()
    assert.ok(env.observers[2].disconnected, 'scope stop destroys')
    assert.equal(third.attrs.size, 0)
})

test('a sources change runs update() after the flush', async () => {
    const { container, index, scope } = setup()
    const list = element('list')
    container.value = list
    await nextTick()
    index.value = 1
    assert.equal(list.props.get('--glide-y'), '0px', 'not before the flush')
    await nextTick()
    assert.equal(list.props.get('--glide-y'), '20px')
    scope.stop()
})

test('the container ref already set at creation: placed at once', () => {
    const env = makeEnv()
    const list = element('list')
    const scope = effectScope()
    scope.run(() => useGlideInk({
        container: ref(list),
        flushTarget: ref(element('ink')),
        getActive: () => null,
        sources: [],
        env,
    }))
    assert.equal(env.observers.length, 1)
    scope.stop()
})

test('flushTarget and flushPseudo reach the controller: the snap flushes the ink', async () => {
    const { env, container, ink, scope } = setup({ flushPseudo: '::after' })
    container.value = element('list')
    await nextTick()
    assert.equal(env.flushes.length, 1, 'the first placement snaps')
    assert.equal(env.flushes[0][0], ink.value, 'the ink is flushed, not the container')
    assert.equal(env.flushes[0][1], '::after')
    scope.stop()
})

test('resetKey reaches the controller: a new key snaps, the same key glides', async () => {
    const key = ref('a')
    const { env, container, index, scope } = setup({ extraSources: [key], resetKey: () => key.value })
    const list = element('list')
    container.value = list
    await nextTick()
    assert.equal(env.flushes.length, 1)

    index.value = 1
    await nextTick()
    assert.equal(list.props.get('--glide-y'), '20px')
    assert.equal(env.flushes.length, 1, 'same key, new row: a glide (no flush)')

    index.value = 2
    key.value = 'b'
    await nextTick()
    assert.equal(list.props.get('--glide-y'), '40px')
    assert.equal(env.flushes.length, 2, 'new key: a snap (flush)')
    scope.stop()
})
