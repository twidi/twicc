import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { effectScope, shallowRef } from 'vue'

import { useMarkdownToolbar } from './useMarkdownToolbar.js'

/** Minimal document: records the listeners the composable adds and removes. */
function makeDocument() {
    const listeners = new Map()
    return {
        listeners,
        addEventListener(type, fn) { listeners.set(type, [...(listeners.get(type) ?? []), fn]) },
        removeEventListener(type, fn) { listeners.set(type, (listeners.get(type) ?? []).filter(f => f !== fn)) },
        count() { return [...listeners.values()].reduce((n, fns) => n + fns.length, 0) },
        dispatch(type, event) { for (const fn of listeners.get(type) ?? []) fn(event) },
    }
}

/** One toolbar mounted in its own effect scope, as one message would be. */
function mount(document) {
    const toolbarEl = { ownerDocument: document }
    const toggleEl = { focused: 0, focus() { this.focused++ } }
    const scope = effectScope()
    const api = scope.run(() => useMarkdownToolbar(shallowRef(toolbarEl), shallowRef(toggleEl)))
    return { ...api, scope, toolbarEl, toggleEl }
}

const eventInside = (toolbarEl) => ({ composedPath: () => [toolbarEl] })
const eventOutside = () => ({ composedPath: () => [{}] })
const escapeEvent = () => ({ key: 'Escape', defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, stopPropagation() {} })

test('toggle opens then closes the toolbar', () => {
    const t = mount(makeDocument())
    assert.equal(t.opened.value, false)
    t.toggle()
    assert.equal(t.opened.value, true)
    t.toggle()
    assert.equal(t.opened.value, false)
    t.scope.stop()
})

test('opening a toolbar closes the previously open one', () => {
    const document = makeDocument()
    const a = mount(document)
    const b = mount(document)
    a.toggle()
    b.toggle()
    assert.equal(a.opened.value, false)
    assert.equal(b.opened.value, true)
    a.scope.stop()
    b.scope.stop()
})

test('a pointer or focus event outside closes, one inside keeps it open', () => {
    for (const type of ['pointerdown', 'focusin']) {
        const document = makeDocument()
        const t = mount(document)
        t.toggle()
        document.dispatch(type, eventInside(t.toolbarEl))
        assert.equal(t.opened.value, true, `${type} inside`)
        document.dispatch(type, eventOutside())
        assert.equal(t.opened.value, false, `${type} outside`)
        t.scope.stop()
    }
})

test('Escape closes the toolbar and returns focus to the toggle', () => {
    const document = makeDocument()
    const t = mount(document)
    t.toggle()
    const event = escapeEvent()
    document.dispatch('keydown', event)
    assert.equal(t.opened.value, false)
    assert.equal(event.defaultPrevented, true)
    assert.equal(t.toggleEl.focused, 1)
    t.scope.stop()
})

test('other keys leave the toolbar open', () => {
    const document = makeDocument()
    const t = mount(document)
    t.toggle()
    document.dispatch('keydown', { key: 'a', preventDefault() {}, stopPropagation() {} })
    assert.equal(t.opened.value, true)
    t.scope.stop()
})

test('document listeners exist only while the toolbar is open', () => {
    const document = makeDocument()
    const t = mount(document)
    assert.equal(document.count(), 0)
    t.toggle()
    assert.equal(document.count(), 3)
    t.toggle()
    assert.equal(document.count(), 0)
    t.scope.stop()
})

test('disposing the scope closes the toolbar and releases its listeners', () => {
    const document = makeDocument()
    const t = mount(document)
    t.toggle()
    t.scope.stop()
    assert.equal(t.opened.value, false)
    assert.equal(document.count(), 0)
})

test('close() is a no-op when the toolbar is already closed', () => {
    const document = makeDocument()
    const a = mount(document)
    const b = mount(document)
    b.toggle()
    a.close()
    assert.equal(b.opened.value, true)
    a.scope.stop()
    b.scope.stop()
})

test('the toolbar CSS keeps an open or focused toolbar visible while the message is hovered', async () => {
    const source = await readFile(new URL('../components/ui/MarkdownContent.vue', import.meta.url), 'utf8')
    const hoverRule = source.indexOf('.markdown-content-wrapper:hover .markdown-toolbar')
    const openRule = source.indexOf('.markdown-content-wrapper .markdown-toolbar[data-open="true"]')
    const focusRule = source.indexOf('.markdown-content-wrapper .markdown-toolbar:focus-within')
    assert.match(source, /\.markdown-toolbar\s*\{[^}]*opacity:\s*0;/s)
    assert.match(source, /\.markdown-content-wrapper:hover \.markdown-toolbar\s*\{[^}]*opacity:\s*0\.3;/s)
    // Later rules win the cascade: open and focus must come after hover.
    assert.ok(hoverRule >= 0 && hoverRule < openRule && openRule < focusRule)
    assert.match(source, /\.markdown-toolbar\[data-open="false"\] wa-button:not\(\.markdown-toolbar-toggle\)\s*\{[^}]*display:\s*none/s)
})
