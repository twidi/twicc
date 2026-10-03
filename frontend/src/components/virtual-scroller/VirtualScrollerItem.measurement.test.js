import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, flush } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as keys from './virtualScrollerKeys.js'
import * as publicationKeys from '../../composables/streamPublicationKeys.js'

const Item = compileComponent(new URL('./VirtualScrollerItem.vue', import.meta.url), {
    './virtualScrollerKeys.js': keys,
    '../../composables/streamPublicationKeys.js': publicationKeys,
})

function mount(t, { height = 50, explicitFloor = null, count = 1, cached = null } = {}) {
    const { renderer, root } = makeRenderer()
    const releases = [], estimatedHeight = Vue.ref(height), cachedHeight = Vue.ref(cached)
    let cacheReads = 0
    const Child = { setup() {
        const context = Vue.inject(publicationKeys.STREAMING_ROW_CONTEXT)
        releases.push(context.reserveInitialHeight?.())
        return () => Vue.h('span')
    } }
    const app = renderer.createApp({ setup() {
        Vue.provide(keys.RESIZE_OBSERVER_KEY, { register() {}, unregister() {}, getItemHeight() { cacheReads++; return cachedHeight.value } })
        return () => Vue.h(Item, { itemKey: 1, estimatedHeight: estimatedHeight.value, minHeight: explicitFloor },
            { default: () => Array.from({ length: count }, () => Vue.h(Child)) })
    } })
    app.mount(root); t.after(() => app.unmount())
    return { row: root.children[0], releases, estimatedHeight, cachedHeight, cacheReads: () => cacheReads }
}

test('an unpublished row retains its estimated height and removes the floor after publication', async t => {
    const v = mount(t); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '50px' })
    v.releases[0](); await flush()
    assert.equal(v.row.props.style?.minHeight, undefined)
})
test('remounted Markdown retains the cached row height until all publications finish', async t => {
    const v = mount(t, { height: 320, explicitFloor: 200, count: 2 }); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '320px' })
    v.releases[0](); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '320px' })
    v.estimatedHeight.value = 340; await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '340px' })
    v.releases[1](); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '200px' })
    v.releases[1](); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '200px' })
})


test('only an unpublished row subscribes to its measured height', async t => {
    const v = mount(t, { height: 50, cached: 640 }); await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '640px' })
    v.cachedHeight.value = 680; await flush()
    assert.deepEqual(v.row.props.style, { minHeight: '680px' })
    v.releases[0](); await flush()
    const reads = v.cacheReads()
    v.cachedHeight.value = 700; await flush()
    assert.equal(v.cacheReads(), reads, 'finished Markdown must not create a new per-frame cache subscription')
    assert.equal(v.row.props.style?.minHeight, undefined)
})


test('the first DOM insertion reserves cached geometry before children can publish readiness', async t => {
    const v = mount(t, { height: 50, cached: 640, count: 0 })
    assert.deepEqual(v.row.props.style, { minHeight: '640px' }, 'synchronous child layout must not collapse the scroll range')
    await flush()
    assert.equal(v.row.props.style?.minHeight, undefined)
})
