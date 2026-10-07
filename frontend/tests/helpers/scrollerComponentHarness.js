import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { useVirtualScroll } from '../../src/composables/useVirtualScroll.js'
import { createRowVisibilityObserver } from '../../src/utils/rowVisibilityObserver.js'
import * as rangeUpdates from '../../src/utils/virtualScrollRangeUpdates.js'
import * as keys from '../../src/components/virtual-scroller/virtualScrollerKeys.js'

export function compileComponent(url, dependencies = {}) {
    const { descriptor } = parse(readFileSync(url, 'utf8'), { templateParseOptions: { isCustomElement: tag => tag.startsWith('wa-') } })
    let code = compileScript(descriptor, { id: 'loading-test', inlineTemplate: true, templateOptions: { compilerOptions: { isCustomElement: tag => tag.startsWith('wa-') } } }).content
    const modules = { vue: Vue, ...dependencies }
    code = code.replace(/import\s+([\s\S]*?)\s+from\s+['"]([^'"]+)['"];?/g, (_, names, path) => {
        if (!modules[path]) throw new Error(`Missing dependency ${path}`)
        if (names.startsWith('{')) return `const ${names.replace(/\bas\b/g, ':')} = modules[${JSON.stringify(path)}];`
        return `const ${names} = modules[${JSON.stringify(path)}];`
    }).replace(/export function /g, 'function ').replace('export default', 'return')
    return new Function('modules', code)(modules)
}

export function makeRenderer(height = 140, decorate = () => {}) {
    function node(type, text = '') {
        const element = { type, text, children: [], props: {}, clientHeight: height, scrollHeight: height, scrollTop: 0,
            addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], querySelector: () => null,
            getBoundingClientRect: () => ({ top: 0, bottom: height, height }),
        }
        decorate(element)
        return element
    }
    const renderer = Vue.createRenderer({
        createElement: node, createText: text => node('#text', text), createComment: text => node('#comment', text),
        insert(child, parent, anchor) {
            if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
            child.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
        },
        remove(child) { const list = child.parent?.children; if (list) list.splice(list.indexOf(child), 1); child.parent = null },
        setText(el, text) { el.text = text }, setElementText(el, text) { el.text = text },
        patchProp(el, key, previous, value) { el.props[key] = value },
        parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1],
    })
    return { renderer, root: node('root') }
}
export const row = { setup(_, { slots }) { return () => Vue.h('div', slots.default?.()) } }
export function virtualScroller(extra = {}) {
    return compileComponent(new URL('../../src/components/virtual-scroller/VirtualScroller.vue', import.meta.url), {
        '../../composables/useVirtualScroll': { useVirtualScroll },
        '../../utils/rowVisibilityObserver.js': { createRowVisibilityObserver },
        './VirtualScrollerItem.vue': row,
        './virtualScrollerKeys.js': keys,
        '../../utils/virtualScrollRangeUpdates.js': rangeUpdates,
        ...extra,
    })
}
export async function flush() { for (let i = 0; i < 12; i++) await Vue.nextTick() }
export function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
export function descendants(node, predicate) { return [node, ...node.children.flatMap(child => descendants(child, predicate))].filter(predicate) }
