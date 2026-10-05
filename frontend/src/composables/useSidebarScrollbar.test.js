import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, shallowRef } from 'vue'
import { useSidebarScrollbar } from './useSidebarScrollbar.js'

function setup() {
    const observers = []
    const env = { getComputedStyle: () => ({ paddingLeft: '7.5px', paddingRight: '0px' }), ResizeObserver: class {
        constructor(callback) { this.callback = callback; observers.push(this) }
        observe(element) { this.element = element }
        disconnect() { this.disconnected = true }
    } }
    const container = shallowRef(null)
    const scope = effectScope()
    scope.run(() => useSidebarScrollbar(container, 'sessions', env))
    return { container, scope, observers }
}

function element(width, clientWidth) {
    const properties = new Map()
    return {
        offsetWidth: width, clientWidth,
        closest: () => null,
        style: {
            getPropertyValue: key => properties.get(key) ?? '',
            setProperty: (key, value) => properties.set(key, value),
            removeProperty: key => properties.delete(key),
        },
    }
}
const measured = el => el.style.getPropertyValue('--sidebar-scrollbar-width')

test('measures native widths and updates when a scrollbar appears or disappears', async () => {
    const { container, scope, observers } = setup()
    const el = element(240, 240)
    container.value = el
    await nextTick()
    assert.equal(measured(el), '0px')
    el.clientWidth = 232
    observers[0].callback()
    assert.equal(measured(el), '8px')
    el.clientWidth = 234
    observers[0].callback()
    assert.equal(measured(el), '6px')
    el.clientWidth = 240
    observers[0].callback()
    assert.equal(measured(el), '0px')
    scope.stop()
})

test('recreated scrollers and scope disposal remove observers and measurements', async () => {
    const { container, scope, observers } = setup()
    const previous = element(240, 232)
    container.value = previous
    await nextTick()
    const current = element(200, 194)
    container.value = current
    await nextTick()
    assert.equal(observers[0].disconnected, true)
    assert.equal(measured(previous), '')
    assert.equal(measured(current), '6px')
    container.value = null
    await nextTick()
    assert.equal(observers[1].disconnected, true)
    assert.equal(measured(current), '')
    container.value = element(200, 200)
    await nextTick()
    scope.stop()
    assert.equal(observers[2].disconnected, true)
    assert.equal(measured(container.value), '')
})

test('publishes platform width to the project wrapper and retains it while the list is hidden', async () => {
    const { container, scope, observers } = setup()
    const wrapper = element(300, 300)
    const el = element(240, 234)
    el.closest = () => wrapper
    container.value = el
    await nextTick()
    assert.equal(wrapper.style.getPropertyValue('--sidebar-sessions-scrollbar-width'), '6px')
    el.offsetWidth = 0
    el.clientWidth = 0
    observers[0].callback()
    assert.equal(measured(el), '6px')
    assert.equal(wrapper.style.getPropertyValue('--sidebar-sessions-scrollbar-width'), '6px')
    el.offsetWidth = 240
    el.clientWidth = 240
    observers[0].callback()
    assert.equal(wrapper.style.getPropertyValue('--sidebar-sessions-scrollbar-width'), '0px')
    scope.stop()
    assert.equal(wrapper.style.getPropertyValue('--sidebar-sessions-scrollbar-width'), '')
})


test('observer dimensions preserve fractional scrollbar widths after zoom or resize', async () => {
    const { container, scope, observers } = setup()
    const el = element(241, 233)
    container.value = el
    await nextTick()
    assert.equal(measured(el), '8px')
    observers[0].callback([{ target: el,
        borderBoxSize: [{ inlineSize: 240.5 }], contentBoxSize: [{ inlineSize: 225.5 }],
    }])
    assert.equal(measured(el), '7.5px')
    observers[0].callback([{ target: el,
        borderBoxSize: [{ inlineSize: 240.5 }], contentBoxSize: [{ inlineSize: 233 }],
    }])
    assert.equal(measured(el), '0px')
    scope.stop()
})
