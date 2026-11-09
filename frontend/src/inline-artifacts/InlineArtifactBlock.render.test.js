import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { useI18n } from 'vue-i18n'
import * as Vue from 'vue'
import { createAppI18n } from '../i18n/index.js'
import { INLINE_ARTIFACT_CONTEXT } from './context.js'
import { focusInlineConversation } from './geometry.js'

function compileBlock() {
    const { descriptor } = parse(readFileSync(new URL('./InlineArtifactBlock.vue', import.meta.url), 'utf8'))
    const compiled = compileScript(descriptor, { id: 'inline-block-test', inlineTemplate: true }).content
    const script = compiled.replace(/import \{([^}]+)\} from ['"]vue['"]/g,
        (_, names) => `const {${names.replace(/ as /g, ': ')}} = Vue`)
        .replace(/^import .*$/gm, '').replace('export default', 'return')
    return new Function('Vue', 'useI18n', 'INLINE_ARTIFACT_CONTEXT', 'focusInlineConversation', script)(
        Vue, useI18n, INLINE_ARTIFACT_CONTEXT, focusInlineConversation)
}

function fixture(t) {
    const observers = []
    globalThis.IntersectionObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this) }
        observe() {}
        disconnect() {}
    }
    t.after(() => { delete globalThis.IntersectionObserver })
    const element = type => ({ type, props: {}, children: [], parent: null, style: {} })
    const renderer = Vue.createRenderer({ createElement: element,
        createText: text => ({ type: '#text', text }), createComment: text => ({ type: '#comment', text }),
        setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text },
        patchProp: (node, key, old, value) => { node.props[key] = value },
        insert(node, parent, anchor) {
            node.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
        },
        remove(node) { node.parent?.children.splice(node.parent.children.indexOf(node), 1); node.parent = null },
        parentNode: node => node.parent,
        nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null })
    const entry = Vue.shallowReactive({ present: true, inlineHeight: 360, loadState: 'idle',
        descriptor: { publicationKey: 'publication-a', title: 'A' } })
    const visibleCalls = []
    let attachment = null
    const runtime = { entries: Vue.shallowReactive(new Map([['a', entry]])),
        geometry: { schedule() {} },
        attach(key, publicationKey, value) { attachment = value; return () => {} },
        setVisible(key, visible) { visibleCalls.push(visible) } }
    const Block = compileBlock()
    const app = renderer.createApp({ render: () => Vue.h(Block, {
        artifactKey: 'a', publicationKey: 'publication-a', clipEl: Vue.markRaw(element('scroller')) }) })
    app.use(createAppI18n())
    app.provide(INLINE_ARTIFACT_CONTEXT, { runtime })
    app.mount(element('root'))
    t.after(() => app.unmount())
    return { deliver: entries => observers[0].callback(entries), visibleCalls, isVisible: () => attachment.isVisible() }
}

test('a delivery holding several reports uses the most recent intersection state', t => {
    const f = fixture(t)
    f.deliver([{ isIntersecting: false }, { isIntersecting: true }])
    assert.equal(f.isVisible(), true)
    assert.equal(f.visibleCalls.at(-1), true)

    f.deliver([{ isIntersecting: true }, { isIntersecting: false }])
    assert.equal(f.isVisible(), false)
    assert.equal(f.visibleCalls.at(-1), false)
})

test('a delivery holding one report uses that report', t => {
    const f = fixture(t)
    f.deliver([{ isIntersecting: true }])
    assert.equal(f.isVisible(), true)
    assert.equal(f.visibleCalls.at(-1), true)
})
