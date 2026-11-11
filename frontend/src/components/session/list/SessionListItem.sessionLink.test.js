import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createPinia, setActivePinia } from 'pinia'
import { useSessionSelectionStore } from '../../../stores/sessionSelection.js'

// Same vm harness as SessionListItem.touch.test.js, with a real EventTarget as
// `window` so the row's pointer listeners can be driven.
const source = readFileSync(new URL('./SessionListItem.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'SessionListItem.vue' })
const compiled = compileScript(descriptor, { id: 'session-link-test' }).content
    .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*;?/g, '')
    .replace('export default', 'const component =')

// Several fixtures can share one test: mock the timers once per test context.
const mockedTimers = new WeakSet()

function fixture(t, { target = 'open', props: extraProps = {} } = {}) {
    if (!mockedTimers.has(t)) {
        t.mock.timers.enable({ apis: ['setTimeout'] })
        mockedTimers.add(t)
    }
    setActivePinia(createPinia())
    const store = useSessionSelectionStore()
    const win = new EventTarget()
    const drags = []
    const events = []
    const targetRef = { value: target }
    const props = { session: { id: 'first', project_id: 'p', title: 'First' }, selectionEnabled: true, linkSource: true, ...extraProps }
    const context = {
        useI18n: () => ({}),
        computed: getter => ({ get value() { return getter() } }),
        watch() {}, inject: () => null, onBeforeUnmount() {},
        ref: value => ({ value }),
        useRoute: () => ({ params: { sessionId: target }, query: {} }),
        useRouter: () => ({ resolve: () => ({ href: '/session' }) }),
        useDataStore: () => ({}), useSettingsStore: () => ({}), useCodeCommentsStore: () => ({}),
        useSessionSelectionStore: () => store,
        useDragHover: () => ({ cancel() {} }),
        useSessionLinkTarget: () => ({ targetSessionId: targetRef }),
        isSessionLinkDragActive: () => false,
        startSessionLinkDrag: (session, pointer) => drags.push({ session, pointer }),
        buildSessionLinkMarkdown: session => `[${session.title}](/project/${session.project_id}/session/${session.id})`,
        getSessionDisplayName: session => session.title || session.id,
        window: win,
        setTimeout, clearTimeout,
    }
    for (const match of source.matchAll(/import ([\s\S]*?) from ['"][^'"]+['"]/g)) {
        for (const name of match[1].replace(/[{}]/g, '').split(',').map(value => value.trim()).filter(Boolean)) {
            if (!(name in context)) context[name] = () => {}
        }
    }
    const component = runInNewContext(`${compiled}; component`, context)
    const row = component.setup(props, { expose() {}, emit: (...args) => events.push(args) })
    return { row, store, win, drags, events, targetRef }
}

const touch = (x = 20, y = 20) => ({ touches: [{ clientX: x, clientY: y }], preventDefault() {} })
const pointer = (type, x, y, pointerType = 'touch') => Object.assign(new Event(type),
    { pointerId: 1, pointerType, clientX: x, clientY: y, button: 0 })

function longPress(f) {
    f.row.handleTouchStart(touch())
    f.mock.timers.tick(500)
}

test('a long press with an open chat arms the drag instead of entering multi-select', t => {
    const f = fixture(t); f.mock = t.mock
    longPress(f)
    assert.equal(f.row.linkArmed.value, true)
    assert.equal(f.store.active, false)
    f.row.handleTouchEnd(touch())
    assert.equal(f.store.active, true, 'a release without move enters multi-select')
    assert.deepEqual([...f.store.selectedIds], ['first'])
    assert.equal(f.row.linkArmed.value, false)
    assert.equal(f.drags.length, 0)
})

test('a move in any direction after the long press drags the session link', t => {
    for (const [x, y] of [[40, 20], [32, 34], [20, 45]]) {
        const f = fixture(t); f.mock = t.mock
        longPress(f)
        f.row.handleTouchMove(touch(26, 25))
        assert.equal(f.drags.length, 0, 'within 9 px it stays armed')
        f.row.handleTouchMove(touch(x, y))
        assert.equal(f.drags.length, 1)
        assert.deepEqual({ ...f.drags[0].session }, { id: 'first', projectId: 'p', title: 'First', markdown: '[First](/project/p/session/first)' })
        assert.equal(f.drags[0].pointer.pointerType, 'touch')
        f.row.handleTouchEnd(touch(x, y))
        assert.equal(f.store.active, false, 'the drag never enters multi-select')
    }
})

test('a scroll before the long press completes neither arms nor selects', t => {
    const f = fixture(t); f.mock = t.mock
    f.row.handleTouchStart(touch())
    f.row.handleTouchMove(touch(20, 40))
    t.mock.timers.tick(500)
    assert.equal(f.row.linkArmed.value, false)
    f.row.handleTouchEnd(touch(20, 40))
    assert.equal(f.store.active, false)
    assert.equal(f.drags.length, 0)
})

test('a second finger disarms the drag', t => {
    const f = fixture(t); f.mock = t.mock
    longPress(f)
    f.row.handleTouchMove({ touches: [{ clientX: 40, clientY: 20 }, { clientX: 90, clientY: 90 }], preventDefault() {} })
    assert.equal(f.row.linkArmed.value, false)
    assert.equal(f.drags.length, 0)
})

test('without an open chat, or for the open session itself, the long press selects as before', t => {
    for (const target of [null, 'first']) {
        const f = fixture(t, { target }); f.mock = t.mock
        longPress(f)
        assert.equal(f.row.linkArmed.value, false)
        assert.equal(f.store.active, true)
    }
})

test('a mouse drag starts after 5 px, only when a chat can receive the link', t => {
    const f = fixture(t)
    f.row.handlePointerDown(pointer('pointerdown', 10, 10, 'mouse'))
    f.win.dispatchEvent(pointer('pointermove', 13, 10, 'mouse'))
    assert.equal(f.drags.length, 0)
    f.win.dispatchEvent(pointer('pointermove', 15, 10, 'mouse'))
    assert.equal(f.drags.length, 1)
    assert.equal(f.drags[0].pointer.pointerType, 'mouse')
    const none = fixture(t, { target: null })
    none.row.handlePointerDown(pointer('pointerdown', 10, 10, 'mouse'))
    none.win.dispatchEvent(pointer('pointermove', 30, 10, 'mouse'))
    assert.equal(none.drags.length, 0)
})

test('no link drag in multi-select mode, for drafts, or outside the main sidebar list', t => {
    for (const setup of [
        f => f.store.enter(),
        { props: { session: { id: 'first', draft: true } } },
        { props: { linkSource: false } },
    ]) {
        const f = fixture(t, typeof setup === 'function' ? {} : setup)
        if (typeof setup === 'function') setup(f)
        f.row.handlePointerDown(pointer('pointerdown', 10, 10, 'mouse'))
        f.win.dispatchEvent(pointer('pointermove', 30, 10, 'mouse'))
        assert.equal(f.drags.length, 0)
        let prevented = false
        f.row.handleDragStart({ preventDefault() { prevented = true } })
        assert.equal(prevented, false, 'the native URL drag stays')
    }
})

test('the native drag of the row link gives way to the link drag', t => {
    const f = fixture(t)
    let prevented = false
    f.row.handleDragStart({ preventDefault() { prevented = true } })
    assert.equal(prevented, true)
})

test('the menu entry shows for a linkable main-list row when another chat can receive the link', t => {
    assert.equal(fixture(t).row.showInsertLinkEntry.value, true)
    const selecting = fixture(t)
    selecting.store.enter()
    assert.equal(selecting.row.showInsertLinkEntry.value, true, 'multi-select keeps the entry')
    for (const options of [{ target: null }, { target: 'first' }, { props: { linkSource: false } },
        { props: { session: { id: 'first', ephemeral: true } } }]) {
        assert.equal(fixture(t, options).row.showInsertLinkEntry.value, false)
    }
})

test('the menu entry emits once, after the dropdown is hidden', t => {
    const f = fixture(t)
    const dropdown = new EventTarget()
    const child = new EventTarget()
    f.row.handleMenuSelect({ detail: { item: { value: 'insert-link' } }, target: dropdown })
    assert.equal(f.events.length, 0)
    dropdown.dispatchEvent(Object.assign(new Event('wa-after-hide'), {}))
    assert.equal(f.events.length, 1)
    assert.equal(f.events[0][0], 'insert-link')
    assert.equal(f.events[0][1].id, 'first')
    t.mock.timers.tick(600)
    assert.equal(f.events.length, 1, 'the fallback timer does not emit again')
    child.dispatchEvent(new Event('wa-after-hide'))
    const late = fixture(t)
    late.row.handleMenuSelect({ detail: { item: { value: 'insert-link' } }, target: new EventTarget() })
    t.mock.timers.tick(600)
    assert.equal(late.events.length, 1, 'the fallback emits when no wa-after-hide arrives')
    const gone = fixture(t)
    gone.row.handleMenuSelect({ detail: { item: { value: 'insert-link' } }, target: new EventTarget() })
    gone.targetRef.value = null
    t.mock.timers.tick(600)
    assert.equal(gone.events.length, 0, 'no emit when the chat went away meanwhile')
})
