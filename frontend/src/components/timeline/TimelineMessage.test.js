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
    const props = Vue.reactive({ eventId: 1, kind: 'assistant_message.sent', data: { text: '**Saved**\n\nFull text' }, expanded: false, ...fields })
    const toggles = [], moreChanges = []
    const { renderer, root } = makeRenderer(140, node => {
        Object.defineProperties(node, {
            clientWidth: { get: () => layout.width },
            scrollWidth: { get: () => Math.max(layout.width, layout.textWidth) },
        })
    })
    const app = renderer.createApp({ render: () => Vue.h(Message, { ...props, 'onUpdate:expanded': value => { props.expanded = value }, onToggle: () => toggles.push(true), onMoreChange: value => moreChanges.push(value) }) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    return { props, root, layout, observers, toggles, moreChanges }
}

test('a multiline preview reports more content and closes at the end of the complete Markdown', async t => {
    const { props, root, toggles, moreChanges } = await mount(t, { contentId: 'saved-message' })
    assert.equal(textOf(byClass(root, 'timeline-message-excerpt')[0]), 'Saved…')
    assert.equal(moreChanges.at(-1), true)
    assert.equal(byClass(root, 'timeline-message-chevron').length, 0)
    assert.equal(articles(root).length, 0)
    props.expanded = true
    await flush()
    assert.equal(byClass(root, 'timeline-message-preview')[0].props['aria-hidden'], 'true')
    assert.equal(moreChanges.at(-1), true)
    assert.equal(articles(root)[0].props['data-markdown-source'], '**Saved**\n\nFull text')
    assert.equal(regions(root)[0].props.id, 'saved-message')
    const [close] = byClass(root, 'timeline-message-close')
    assert.equal(textOf(close), 'Close message')
    assert.ok(descendants(regions(root)[0], node => node === close).length, 'close follows the complete text')
    close.props.onClick()
    await flush()
    assert.equal(props.expanded, false)
    assert.equal(articles(root).length, 0)
    assert.equal(regions(root).length, 0)
    assert.equal(byClass(root, 'timeline-message-preview')[0].props['aria-hidden'], undefined)
    assert.equal(toggles.length, 1)
})

test('reported overflow follows available width, including while the message is expanded', async t => {
    const { props, layout, observers, moreChanges } = await mount(t, { data: { text: 'A single text line' } })
    assert.equal(moreChanges.at(-1), false)
    layout.width = 100
    observers.at(-1).resize()
    await flush()
    assert.equal(moreChanges.at(-1), true)
    props.expanded = true
    await flush()
    assert.equal(moreChanges.at(-1), true, 'opening keeps the overflow indicator')
    layout.width = 500
    observers.at(-1).resize()
    await flush()
    assert.equal(moreChanges.at(-1), false, 'a line that now fits removes the indicator while open')
})

test('recycling resets expansion and reports whether the new message has more content', async t => {
    const { props, root, moreChanges } = await mount(t)
    props.expanded = true
    await flush()
    props.eventId = 2
    props.data = { text: 'Second snapshot' }
    await flush()
    assert.equal(props.expanded, false)
    assert.equal(byClass(root, 'timeline-message-close').length, 0)
    assert.equal(articles(root).length, 0)
    assert.equal(moreChanges.at(-1), false)
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
