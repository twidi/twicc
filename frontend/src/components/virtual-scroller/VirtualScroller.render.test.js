import test from 'node:test'
import assert from 'node:assert/strict'
import { h, reactive, ref, onUpdated } from 'vue'
import { virtualScroller, makeRenderer, flush, descendants } from '../../../tests/helpers/scrollerComponentHarness.js'

async function fixture(t, { memo = true } = {}) {
    const { renderer, root } = makeRenderer()
    const items = ref([reactive({ id: 1, text: 'one' }), reactive({ id: 2, text: 'two' })])
    const label = ref('initial'), indexLabels = ref(false), animation = reactive({}), updates = []
    const wrapper = { props: ['itemKey'], setup(props, { slots }) {
        onUpdated(() => updates.push(props.itemKey))
        return () => h('div', { 'data-key': props.itemKey }, slots.default?.())
    } }
    const component = virtualScroller({ './VirtualScrollerItem.vue': wrapper })
    const app = renderer.createApp({ setup: () => () => h(component, {
        items: items.value, itemKey: row => row.id,
        ...(memo ? { itemMemo: () => [label.value, indexLabels.value] } : {}),
        itemClass: row => animation[row.id] ? 'entering' : '',
        itemStyle: row => animation[row.id] ? { opacity: 0.5 } : null,
        itemMinHeight: row => animation[row.id] ? 80 : null,
    }, { default: ({ item, index }) => h('span', `${label.value}:${item.text}${indexLabels.value ? `:${index}` : ''}`) }) })
    app.mount(root); t.after(() => app.unmount()); await flush(); updates.length = 0
    return { items, label, indexLabels, animation, updates, text: () => descendants(root, n => n.type === 'span').map(n => n.text) }
}

test('replacing one row does not update unchanged wrappers', async t => {
    const f = await fixture(t)
    f.items.value = [f.items.value[0], reactive({ id: 2, text: 'changed' })]
    await flush()
    assert.deepEqual(f.updates, [2])
    assert.deepEqual(f.text(), ['initial:one', 'initial:changed'])
})

test('same-object streaming content still updates its wrapper', async t => {
    const f = await fixture(t)
    f.items.value[1].text = 'streamed'
    await flush()
    assert.deepEqual(f.updates, [2])
    assert.deepEqual(f.text(), ['initial:one', 'initial:streamed'])
})

test('index changes and declared slot dependencies update rows', async t => {
    const f = await fixture(t)
    f.indexLabels.value = true
    await flush()
    f.items.value.reverse()
    await flush()
    assert.deepEqual(f.text(), ['initial:two:0', 'initial:one:1'])
    f.label.value = 'changed'
    await flush()
    assert.deepEqual(f.text(), ['changed:two:0', 'changed:one:1'])
})

test('animation and height changes update only the affected wrapper', async t => {
    const f = await fixture(t)
    f.animation[2] = true
    await flush()
    assert.deepEqual(f.updates, [2])
})

test('callers without memo dependencies retain normal slot updates', async t => {
    const f = await fixture(t, { memo: false })
    f.label.value = 'changed'
    await flush()
    assert.deepEqual(f.text(), ['changed:one', 'changed:two'])
})
