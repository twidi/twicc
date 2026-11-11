import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createPinia, setActivePinia } from 'pinia'
import { useSessionSelectionStore } from '../../../stores/sessionSelection.js'

const source = readFileSync(new URL('./SessionListItem.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'SessionListItem.vue' })
const compiled = compileScript(descriptor, { id: 'touch-test' }).content
    .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*;?/g, '')
    .replace('export default', 'const component =')

function fixture(t, overrides = {}) {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    setActivePinia(createPinia())
    const store = useSessionSelectionStore()
    const events = []
    const cleanup = []
    const watchers = []
    const props = { session: { id: 'first' }, selectionEnabled: true, ...overrides }
    const context = {
        useI18n: () => ({}),
        computed: getter => ({ get value() { return getter() } }),
        watch: (getter, callback) => watchers.push(callback), inject: () => null,
        onBeforeUnmount: callback => cleanup.push(callback),
        useRoute: () => ({ params: { sessionId: 'open' }, query: {} }),
        useRouter: () => ({ resolve: () => ({ href: '/session' }) }),
        useDataStore: () => ({}), useSettingsStore: () => ({}), useCodeCommentsStore: () => ({}),
        useSessionSelectionStore: () => store,
        useDragHover: () => ({ cancel() {} }),
        setTimeout, clearTimeout,
    }
    for (const match of source.matchAll(/import ([\s\S]*?) from ['"][^'"]+['"]/g)) {
        for (const name of match[1].replace(/[{}]/g, '').split(',').map(value => value.trim()).filter(Boolean)) {
            if (!(name in context)) context[name] = () => {}
        }
    }
    const component = runInNewContext(`${compiled}; component`, context)
    const row = component.setup(props, { expose() {}, emit: (...args) => events.push(args) })
    return { row, store, events, cleanup, watchers, props }
}

function touch(x = 20, y = 20, count = 1) {
    return { touches: Array.from({ length: count }, () => ({ clientX: x, clientY: y })),
        prevented: false, preventDefault() { this.prevented = true } }
}
function click(extra = {}) {
    return { button: 0, prevented: false, preventDefault() { this.prevented = true }, ...extra }
}

test('a stationary long press selects the touched session without navigation or a second toggle', t => {
    const { row, store, events } = fixture(t, { active: true })
    assert.equal(typeof row.handleTouchStart, 'function', 'rows support touch selection')
    row.handleTouchStart(touch())
    t.mock.timers.tick(499)
    assert.equal(store.active, false)
    t.mock.timers.tick(1)
    assert.equal(store.active, true)
    assert.deepEqual([...store.selectedIds], ['first'])
    const end = touch()
    row.handleTouchEnd(end)
    assert.equal(end.prevented, true)
    row.handleClick(click())
    assert.deepEqual([...store.selectedIds], ['first'])
    assert.deepEqual(events, [])
})

test('a short touch toggles selection only while selection mode is active', t => {
    const { row, store, events } = fixture(t)
    row.handleTouchStart(touch())
    row.handleTouchEnd(touch())
    row.handleClick(click())
    assert.equal(events[0][0], 'select')
    store.enter()
    for (const expected of [['first'], []]) {
        row.handleTouchStart(touch())
        const end = touch()
        row.handleTouchEnd(end)
        assert.equal(end.prevented, true)
        row.handleClick(click())
        assert.deepEqual([...store.selectedIds], expected)
    }
    assert.equal(events.length, 1)
})

test('scrolling, cancellation and multiple fingers never start or toggle selection', t => {
    const { row, store } = fixture(t)
    for (const cancel of [() => row.handleTouchMove(touch(20, 40)),
        () => row.handleTouchCancel(), () => row.handleTouchStart(touch(20, 20, 2))]) {
        row.handleTouchStart(touch())
        cancel()
        t.mock.timers.tick(600)
        row.handleTouchEnd(touch())
        assert.equal(store.active, false)
        store.enter()
        row.handleTouchStart(touch())
        cancel()
        row.handleTouchEnd(touch())
        assert.deepEqual([...store.selectedIds], [])
        store.exit()
    }
})

test('small finger movement keeps the long press armed', t => {
    const { row, store } = fixture(t)
    row.handleTouchStart(touch())
    row.handleTouchMove(touch(22, 23))
    t.mock.timers.tick(500)
    assert.deepEqual([...store.selectedIds], ['first'])
})

test('preview rows keep native touch navigation', t => {
    const { row, store, events } = fixture(t, { selectionEnabled: false })
    store.enter()
    row.handleTouchStart(touch())
    t.mock.timers.tick(600)
    const end = touch()
    row.handleTouchEnd(end)
    assert.equal(end.prevented, false)
    assert.deepEqual([...store.selectedIds], [])
    row.handleClick(click())
    assert.equal(events[0][0], 'select')
})

test('recycling or unmounting a row cancels its pending long press', t => {
    const { row, store, props, watchers, cleanup } = fixture(t)
    row.handleTouchStart(touch())
    props.session = { id: 'second' }
    for (const callback of watchers) callback()
    t.mock.timers.tick(600)
    assert.equal(store.active, false)
    row.handleTouchStart(touch())
    for (const callback of cleanup) callback()
    t.mock.timers.tick(600)
    assert.equal(store.active, false)
})

test('touch context menus are blocked while mouse context menus remain native', t => {
    const { row } = fixture(t)
    const mouse = click()
    row.handleContextMenu(mouse)
    assert.equal(mouse.prevented, false)
    row.handleTouchStart(touch())
    const held = click()
    row.handleContextMenu(held)
    assert.equal(held.prevented, true)
    t.mock.timers.tick(500)
    row.handleTouchEnd(touch())
    const laterMouse = click({ pointerType: 'mouse' })
    row.handleContextMenu(laterMouse)
    assert.equal(laterMouse.prevented, false)
})

test('the row link wires touch handlers and allows touchend cancellation', () => {
    const root = descriptor.template.ast.children.find(node => node.type === 1)
    const row = root.children.find(node => node.type === 1 && node.tag === 'wa-button')
    for (const [event, handler] of Object.entries({ touchstart: 'handleTouchStart', touchmove: 'handleTouchMove',
        touchend: 'handleTouchEnd', touchcancel: 'handleTouchCancel', contextmenu: 'handleContextMenu' })) {
        const binding = row.props.find(prop => prop.type === 7 && prop.name === 'on' && prop.arg?.content === event)
        assert.equal(binding?.exp?.content, handler)
        if (event === 'touchend') assert.ok(!binding.modifiers.some(modifier => modifier.content === 'passive'))
    }
})

test('plain mouse clicks keep opening sessions in selection mode', t => {
    const { row, store, events } = fixture(t)
    store.enter()
    row.handleClick(click())
    assert.equal(events[0][0], 'select')
    assert.deepEqual([...store.selectedIds], [])
    row.handleClick(click({ ctrlKey: true }))
    assert.equal(events[1][0], 'selection-click')
})
