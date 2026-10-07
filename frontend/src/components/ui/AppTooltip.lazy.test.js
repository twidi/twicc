import test from 'node:test'
import assert from 'node:assert/strict'
import { h, markRaw } from 'vue'
import { compileComponent, makeRenderer, flush, descendants } from '../../../tests/helpers/scrollerComponentHarness.js'
import { serializeTooltipTransitions } from '../../utils/tooltipTransitions.js'

const Tooltip = compileComponent(new URL('./AppTooltip.vue', import.meta.url), {
    '../../stores/settings': { useSettingsStore: () => ({ isTouchDevice: false }) },
    '../../utils/tooltipTransitions.js': { serializeTooltipTransitions },
})
const Duration = compileComponent(new URL('./ProcessDuration.vue', import.meta.url), {
    '../../utils/date': { formatDuration: value => String(value) },
})

async function fixture(t, lazy = true) {
    const saved = { document: globalThis.document, setInterval, clearInterval }
    const timers = new Map(); let next = 0
    globalThis.document = new EventTarget()
    globalThis.setInterval = callback => { timers.set(++next, callback); return next }
    globalThis.clearInterval = id => timers.delete(id)
    const view = makeRenderer(140, element => {
        markRaw(element)
        const listeners = new Map()
        element.addEventListener = (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback) }
        element.removeEventListener = (name, callback) => listeners.get(name)?.delete(callback)
        element.emit = (name, target = element) => { for (const callback of listeners.get(name) || []) callback({ target }) }
        element.open = false; element.trigger = 'manual'; element.anchor = {}
        element.isConnected = true; element.eventController = new AbortController()
        element.handleOpenChange = async () => element.emit(element.open ? 'wa-show' : 'wa-hide')
    })
    const app = view.renderer.createApp({ render: () => h(Tooltip, { force: true, interactive: true, lazy }, {
        default: () => h(Duration, { stateChangedAt: 100 }),
    }) })
    app.mount(view.root); await flush()
    t.after(() => { app.unmount(); Object.assign(globalThis, saved); assert.equal(timers.size, 0, 'unmount clears duration timers') })
    const element = descendants(view.root, node => node.type === 'wa-tooltip')[0]
    return { element, timers, view }
}

test('closed lazy tooltips mount no duration timer; show mounts and completed hide removes it', async t => {
    const f = await fixture(t)
    assert.equal(f.timers.size, 0)
    f.element.show(); await flush(); assert.equal(f.timers.size, 1)
    f.element.hide(); await flush(); assert.equal(f.timers.size, 1, 'retain content during hide animation')
    f.element.emit('wa-after-hide'); await flush(); assert.equal(f.timers.size, 0)
    f.element.show(); await flush(); assert.equal(f.timers.size, 1)
})

test('nested events and a late hide from a prior opening cannot drop reopened content', async t => {
    const f = await fixture(t)
    f.element.emit('wa-show', {}); await flush(); assert.equal(f.timers.size, 0)
    f.element.show(); await flush(); f.element.hide(); f.element.show()
    f.element.emit('wa-after-hide'); await flush(); assert.equal(f.timers.size, 1)
    f.element.emit('wa-after-hide', {}); await flush(); assert.equal(f.timers.size, 1)
})

test('ordinary tooltips retain their existing eager slot behavior', async t => {
    const f = await fixture(t, false)
    assert.equal(f.timers.size, 1)
    f.element.show(); f.element.hide(); f.element.emit('wa-after-hide')
    await flush(); assert.equal(f.timers.size, 1)
})
