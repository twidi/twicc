import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, createRenderer, h, nextTick, ref, shallowRef, unref, watch } from 'vue'

import { useVirtualScroll } from '../../../composables/useVirtualScroll.js'

const source = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const start = source.indexOf('// Watch for deferred resume completion:')
const end = source.indexOf('/**\n * Handle VirtualScroller becoming visible', start)
assert.ok(start >= 0 && end > start, 'parent resume source is available')
const parentResume = new Function('watch', 'nextTick', 'unref', 'scrollerRef', 'visualItems', 'scrollToBottomUntilStable', `
    let itemCountAtDeactivation = null
    let wasNearBottomAtDeactivation = false
    let savedScrollAnchor = null
    ${source.slice(start, end)}
    return {
        handlePostResume,
        save(count, nearBottom) {
            itemCountAtDeactivation = count
            wasNearBottomAtDeactivation = nearBottom
            savedScrollAnchor = { index: 5, key: 'row-5', offset: 0 }
        },
        saved: () => ({ itemCountAtDeactivation, wasNearBottomAtDeactivation, savedScrollAnchor }),
    }
`)

const renderer = createRenderer({
    createComment: () => ({}), createElement: () => ({ children: [] }), createText: () => ({}),
    insert(node, parent) { node.parent = parent; parent.children.push(node) },
    remove(node) { const children = node.parent?.children; if (children) children.splice(children.indexOf(node), 1) },
    setElementText() {}, setText() {}, patchProp() {}, parentNode: node => node.parent, nextSibling: () => null,
})

function mountResume(t) {
    const items = shallowRef(Array.from({ length: 10 }, (_, index) => ({ id: `row-${index}` })))
    const container = {
        clientHeight: 200, scrollTop: 200, domHeight: 400,
        get scrollHeight() { return this.domHeight },
        addEventListener() {}, removeEventListener() {},
    }
    const containerRef = shallowRef(container)
    let child, parent, bottomAttempts = 0
    const Child = {
        setup(_, { expose }) {
            child = useVirtualScroll({ items, itemKey: item => item.id, minItemHeight: 40, containerRef })
            expose({ suspended: child.suspended, scrollToBottom: child.scrollToBottom })
            return () => null
        },
    }
    const Parent = {
        setup() {
            const scrollerRef = ref(null)
            parent = parentResume(watch, nextTick, unref, scrollerRef, computed(() => items.value), () => {
                bottomAttempts++
                scrollerRef.value.scrollToBottom()
            })
            return () => h(Child, { ref: scrollerRef })
        },
    }
    const app = renderer.createApp(Parent)
    app.mount({ children: [] })
    t.after(() => app.unmount())
    child.syncScrollPosition()
    return { child, parent, items, container, bottomAttempts: () => bottomAttempts }
}

test('hidden reactivation keeps the bottom override until the exposed scroller actually resumes', async (t) => {
    const view = mountResume(t)
    view.parent.save(10, true)
    view.child.suspend()
    view.container.clientHeight = 0
    view.items.value = [...view.items.value, { id: 'row-10' }]
    view.container.domHeight = 440
    await nextTick()
    view.child.resume()
    assert.equal(view.child.suspended.value, true)

    view.parent.handlePostResume()
    assert.equal(view.bottomAttempts(), 0)
    assert.equal(view.parent.saved().itemCountAtDeactivation, 10)
    assert.ok(view.parent.saved().savedScrollAnchor)

    view.container.clientHeight = 200
    view.child.resume()
    await nextTick()
    await nextTick()
    assert.equal(view.child.suspended.value, false)
    assert.equal(view.bottomAttempts(), 1)
    assert.equal(view.container.scrollTop, 240)
    assert.deepEqual(view.parent.saved(), {
        itemCountAtDeactivation: null, wasNearBottomAtDeactivation: false, savedScrollAnchor: null,
    })

    view.parent.handlePostResume()
    await nextTick()
    assert.equal(view.bottomAttempts(), 1, 'a second activation cannot replay the consumed override')
})

test('reactivation without a new row clears saved state without a bottom override', async (t) => {
    const view = mountResume(t)
    view.parent.save(10, true)
    view.child.suspend()
    await nextTick()
    view.container.clientHeight = 0
    view.child.resume()
    view.parent.handlePostResume()
    assert.equal(view.parent.saved().itemCountAtDeactivation, 10)

    view.container.clientHeight = 200
    view.child.resume()
    await nextTick()
    await nextTick()
    assert.equal(view.bottomAttempts(), 0)
    assert.equal(view.parent.saved().itemCountAtDeactivation, null)
})
