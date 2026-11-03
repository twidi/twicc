import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import { Message } from '../../../tests/helpers/timelineMessageComponent.js'

const textOf = node => `${node.type === '#comment' ? '' : node.text ?? ''}${node.children.map(textOf).join('')}`
const byClass = (root, name) => descendants(root, node => String(node.props.class ?? '').split(' ').includes(name))
const regions = root => descendants(root, node => node.props.role === 'region')
const articles = root => descendants(root, node => node.type === 'article')
const resizeContexts = new WeakMap()
async function mount(t, fields = {}, layout = { width: 300, textWidth: 150 }) {
    let observers = resizeContexts.get(t)
    if (!observers) {
        observers = []
        resizeContexts.set(t, observers)
        const previous = globalThis.ResizeObserver
        globalThis.ResizeObserver = class {
            constructor(callback) { this.callback = callback; observers.push(this) }
            observe(target) { this.target = target }
            disconnect() { this.target = null }
            resize() { this.callback([{ target: this.target }]) }
        }
        t.after(() => {
            if (previous === undefined) delete globalThis.ResizeObserver
            else globalThis.ResizeObserver = previous
        })
    }
    const props = Vue.reactive({ eventId: 1, kind: 'assistant_message.sent', data: { text: '**Saved**\n\nFull text' }, ...fields })
    const toggles = []
    const { renderer, root } = makeRenderer(140, node => {
        Object.defineProperties(node, {
            clientWidth: { get: () => layout.width },
            scrollWidth: { get: () => Math.max(layout.width, layout.textWidth) },
        })
    })
    const app = renderer.createApp({ render: () => Vue.h(Message, { ...props, onToggle: () => toggles.push(true) }) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    return { props, root, layout, observers, toggles }
}

test('a multiline preview opens with its chevron and closes at the end of the complete Markdown', async t => {
    const { root, toggles, observers } = await mount(t)
    assert.equal(textOf(byClass(root, 'timeline-message-excerpt')[0]), 'Saved…')
    assert.equal(articles(root).length, 0)
    const [chevron] = byClass(root, 'timeline-message-chevron')
    assert.equal(chevron.props.type, 'button')
    assert.equal(chevron.props['aria-expanded'], 'false')
    assert.equal(chevron.props['aria-label'], 'Open message')
    assert.equal(descendants(chevron, node => node.type === 'wa-icon')[0].props.name, 'chevron-down')
    chevron.props.onClick()
    await flush()
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
    assert.equal(byClass(root, 'timeline-message-excerpt').length, 0)
    assert.ok(observers.every(observer => observer.target === null), 'removed previews release their resize observer')
    assert.equal(articles(root)[0].props['data-markdown-source'], '**Saved**\n\nFull text')
    assert.equal(regions(root)[0].props.id, chevron.props['aria-controls'])
    const [close] = byClass(root, 'timeline-message-close')
    assert.equal(textOf(close), 'Close message')
    assert.ok(descendants(regions(root)[0], node => node === close).length, 'close follows the rendered message inside its region')
    close.props.onClick()
    await flush()
    assert.equal(articles(root).length, 0)
    assert.equal(regions(root).length, 0)
    assert.equal(byClass(root, 'timeline-message-chevron').length, 1)
    assert.equal(toggles.length, 2)
})

test('the chevron follows actual line overflow when the available width changes', async t => {
    const { root, layout, observers } = await mount(t, { data: { text: 'A single text line' } })
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
    layout.width = 100
    observers.at(-1).resize()
    await flush()
    assert.equal(byClass(root, 'timeline-message-chevron').length, 1)
    layout.width = 500
    observers.at(-1).resize()
    await flush()
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
})

test('recycling an expanded component resets its expansion and preview measurement', async t => {
    const { props, root } = await mount(t)
    byClass(root, 'timeline-message-chevron')[0].props.onClick()
    await flush()
    props.eventId = 2
    props.data = { text: 'Second snapshot' }
    await flush()
    assert.equal(byClass(root, 'timeline-message-close').length, 0)
    assert.equal(articles(root).length, 0)
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
    assert.match(textOf(root), /Second snapshot/)
})

test('short messages have no chevron; controlled expansion still shows their complete Markdown', async t => {
    const { props, root } = await mount(t, { kind: 'user_message.sent', data: { text: '**Short**' }, expanded: false })
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
    props.expanded = true
    await flush()
    assert.equal(articles(root)[0].props['data-markdown-source'], '**Short**')
    assert.equal(textOf(byClass(root, 'timeline-message-close')[0]), 'Close message')
})

test('unknown kinds and malformed text have no message controls', async t => {
    for (const fields of [{ kind: 'future' }, { data: null }, { data: { text: 1 } }, { data: { text: '  ' } }]) {
        const { root } = await mount(t, fields)
        assert.equal(descendants(root, node => node.type === 'button').length, 0)
        assert.equal(articles(root).length, 0)
    }
})

test('message renderer keeps the existing async Markdown boundary without raw HTML', () => {
    const source = readFileSync(new URL('./TimelineMessage.vue', import.meta.url), 'utf8')
    assert.match(source, /defineAsyncComponent\(\(\) => import\('\.\.\/ui\/MarkdownContent\.vue'\)\)/)
    assert.doesNotMatch(source, /v-html/)
})
