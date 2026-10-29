import test from 'node:test'
import assert from 'node:assert/strict'
import { h, markRaw, ref } from 'vue'
import { compileComponent, makeRenderer, flush, descendants } from '../../../tests/helpers/scrollerComponentHarness.js'
import { serializeTooltipTransitions } from '../../utils/tooltipTransitions.js'

const Tooltip = compileComponent(new URL('./AppTooltip.vue', import.meta.url), {
    '../../stores/settings': { useSettingsStore: () => ({ isTouchDevice: false }) },
    '../../utils/tooltipTransitions.js': { serializeTooltipTransitions },
})

const tick = () => new Promise(resolve => setTimeout(resolve, 5))

/** An anchor element: its hover and focus state are set by the test. */
function anchor(id) {
    return { id, hovered: true, focused: false, matches(selector) {
        return selector === ':hover' ? this.hovered : selector === ':focus-within' ? this.focused : false
    } }
}

function fixture(t, tooltips) {
    const savedDocument = globalThis.document
    const listeners = new Map()
    globalThis.document = {
        addEventListener: (name, callback) => listeners.set(name, callback),
        removeEventListener: (name, callback) => { if (listeners.get(name) === callback) listeners.delete(name) },
    }
    const anchors = new Map(tooltips.flatMap(tip => [[tip.for, anchor(tip.for)]]))
    const view = makeRenderer(20, element => {
        if (element.type !== 'wa-tooltip') return
        markRaw(element)
        element.addEventListener = () => {}
        element.removeEventListener = () => {}
        element.isConnected = true; element.eventController = new AbortController()
        element.handleOpenChange = async () => {}
        element.updateComplete = Promise.resolve()
        element.showDelay = 0
        element.open = false // AppTooltip's transition serializer wraps show(), which sets it
        element.hasTrigger = trigger => trigger === 'hover' || trigger === 'focus'
        Object.defineProperty(element, 'anchor', { get: () => anchors.get(element.props.for) ?? null })
    })
    const visible = ref(true)
    const app = view.renderer.createApp({ render: () => visible.value
        ? h('div', tooltips.map(tip => h(Tooltip, { deferred: tip.deferred ?? true, for: tip.for }, { default: () => 'text' })))
        : null })
    app.mount(view.root)
    t.after(() => { app.unmount(); globalThis.document = savedDocument })
    const fire = (type, path) => listeners.get(type)?.({ type, composedPath: () => path })
    const elements = id => descendants(view.root, node => node.type === 'wa-tooltip' && node.props.for === id)
    const element = id => elements(id)[0]
    return { anchors, fire, element, elements, listeners, visible }
}

test('a deferred tooltip mounts no wa-tooltip until its anchor is hovered', async t => {
    const f = fixture(t, [{ for: 'mtime-s1' }])
    await flush()
    assert.equal(f.element('mtime-s1'), undefined)
    f.fire('mouseover', [{ id: 'elsewhere' }])
    await flush()
    assert.equal(f.element('mtime-s1'), undefined, 'a hover elsewhere mounts nothing')
})

test('a hover on a child of the anchor mounts the tooltip and shows it after the delay', async t => {
    const f = fixture(t, [{ for: 'mtime-s1' }])
    await flush()
    f.fire('mouseover', [{ id: '' }, f.anchors.get('mtime-s1')])
    await flush()
    const el = f.element('mtime-s1')
    assert.ok(el, 'the wa-tooltip is mounted')
    await tick()
    assert.equal(el.open, true, 'the first hover is replayed')
})

test('no show when the pointer left the anchor before the delay ends', async t => {
    const f = fixture(t, [{ for: 'mtime-s1' }])
    await flush()
    f.fire('mouseover', [f.anchors.get('mtime-s1')])
    f.anchors.get('mtime-s1').hovered = false
    await flush(); await tick()
    assert.equal(f.element('mtime-s1').open, false)
})

test('a focus mounts the tooltip and shows it at once', async t => {
    const f = fixture(t, [{ for: 'menu-s1' }])
    await flush()
    const target = f.anchors.get('menu-s1')
    target.hovered = false; target.focused = true
    f.fire('focusin', [target])
    await flush()
    assert.equal(f.element('menu-s1').open, true)
})

test('nested anchors mount every tooltip on the path, once each', async t => {
    const f = fixture(t, [{ for: 'mtime-s1' }, { for: 'button-s1' }])
    await flush()
    const path = [f.anchors.get('mtime-s1'), f.anchors.get('button-s1')]
    f.fire('mouseover', path)
    f.fire('mouseover', path)
    await flush(); await tick()
    for (const id of ['mtime-s1', 'button-s1']) {
        assert.equal(f.element(id).open, true, id)
        assert.equal(f.elements(id).length, 1, `${id} mounts once`)
    }
})

test('the document listeners go away with the last waiting tooltip', async t => {
    const f = fixture(t, [{ for: 'mtime-s1' }, { for: 'cost-s1' }])
    await flush()
    assert.deepEqual([...f.listeners.keys()].sort(), ['focusin', 'mouseover'])
    f.fire('mouseover', [f.anchors.get('mtime-s1')])
    await flush()
    assert.equal(f.listeners.size, 2, 'cost-s1 still waits')
    f.visible.value = false
    await flush()
    assert.equal(f.listeners.size, 0, 'unmount unregisters')
})

test('a tooltip without deferred still mounts at once', async t => {
    const f = fixture(t, [{ for: 'title-s1', deferred: false }])
    await flush()
    assert.ok(f.element('title-s1'))
    assert.equal(f.listeners.size, 0)
})
