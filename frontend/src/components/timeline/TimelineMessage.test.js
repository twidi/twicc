import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import { Message } from '../../../tests/helpers/timelineMessageComponent.js'

const textOf = node => `${node.type === '#comment' ? '' : node.text ?? ''}${node.children.map(textOf).join('')}`
const buttons = root => descendants(root, node => node.type === 'button')
const regions = root => descendants(root, node => node.props.role === 'region')
const articles = root => descendants(root, node => node.type === 'article')
async function mount(t, fields = {}) {
    const props = Vue.reactive({ eventId: 1, kind: 'assistant_message.sent', data: { text: '**Saved**\n\nFull text' }, ...fields })
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => Vue.h(Message, props) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    return { props, root }
}

test('collapsed snapshots show plain text and load complete Markdown only after expansion', async t => {
    const { root } = await mount(t)
    assert.match(textOf(root), /Saved Full text/)
    assert.equal(articles(root).length, 0)
    const [button] = buttons(root)
    assert.equal(button.props.type, 'button')
    assert.equal(button.props['aria-expanded'], 'false')
    assert.equal(textOf(button), 'Show message')
    button.props.onClick()
    await flush()
    assert.equal(button.props['aria-expanded'], 'true')
    assert.equal(textOf(button), 'Hide message')
    assert.equal(articles(root)[0].props['data-markdown-source'], '**Saved**\n\nFull text')
    const [region] = regions(root)
    assert.equal(region.props.id, button.props['aria-controls'])
    assert.equal(region.props['aria-labelledby'], button.props.id)
    button.props.onClick()
    await flush()
    assert.equal(button.props['aria-expanded'], 'false')
    assert.equal(articles(root).length, 0)
    assert.equal(regions(root).length, 0)
})

test('recycling an expanded component for another event resets its local expansion', async t => {
    const { props, root } = await mount(t)
    buttons(root)[0].props.onClick()
    await flush()
    props.eventId = 2
    props.data = { text: 'Second snapshot' }
    await flush()
    assert.equal(buttons(root)[0].props['aria-expanded'], 'false')
    assert.equal(articles(root).length, 0)
    assert.match(textOf(root), /Second snapshot/)
    buttons(root)[0].props.onClick()
    await flush()
    assert.equal(articles(root)[0].props['data-markdown-source'], 'Second snapshot')
})

test('short user messages expand, while unknown kinds and malformed text have no control', async t => {
    const { root } = await mount(t, { kind: 'user_message.sent', data: { text: 'Short' } })
    assert.equal(buttons(root).length, 1)
    for (const fields of [{ kind: 'future' }, { data: null }, { data: { text: 1 } }, { data: { text: '  ' } }]) {
        const { root: empty } = await mount(t, fields)
        assert.equal(buttons(empty).length, 0)
        assert.equal(articles(empty).length, 0)
    }
})

test('message renderer uses the existing async Markdown component without raw HTML', () => {
    const source = readFileSync(new URL('./TimelineMessage.vue', import.meta.url), 'utf8')
    assert.match(source, /defineAsyncComponent\(\(\) => import\('\.\.\/ui\/MarkdownContent\.vue'\)\)/)
    assert.doesNotMatch(source, /v-html/)
})
