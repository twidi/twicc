// Settings content crossfade (visual refresh step 7f,
// docs/plans/2026-09-30-settings-crossfade-design.md §7). Same approach as
// question-options-motion.test.js: the component is read as text, each swap rule is pinned
// whole (selector + ordered declaration list) and the template facts are checked on the
// @vue/compiler-sfc AST. The component is also compiled, so a markup or scoped-style slip
// that Vue rejects fails here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { compileStyle, compileTemplate, parse } from '@vue/compiler-sfc'

const here = dirname(fileURLToPath(import.meta.url))
const FILE = '../components/app/SettingsPopover.vue'
const sfc = readFileSync(join(here, FILE), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Split on commas at parenthesis depth 0 (selector lists). */
function splitTopLevel(text) {
    const parts = []
    let depth = 0
    let start = 0
    for (let i = 0; i < text.length; i++) {
        if (text[i] === '(') depth++
        else if (text[i] === ')') depth--
        else if (text[i] === ',' && depth === 0) {
            parts.push(text.slice(start, i))
            start = i + 1
        }
    }
    parts.push(text.slice(start))
    return parts.map(collapse).filter(Boolean)
}

/** Every rule of a stylesheet, nesting-aware, in source order:
 *  { head, selectors, list, decls, ancestors, order }. */
function rulesOf(css, ancestors = [], out = []) {
    let i = 0
    let own = ''
    while (i < css.length) {
        const open = css.indexOf('{', i)
        if (open === -1) break
        const rawHead = css.slice(i, open)
        const cut = Math.max(rawHead.lastIndexOf(';'), rawHead.lastIndexOf('}'))
        own += rawHead.slice(0, cut + 1)
        const head = collapse(rawHead.slice(cut + 1))
        let depth = 1
        let j = open + 1
        while (j < css.length && depth > 0) {
            if (css[j] === '{') depth++
            else if (css[j] === '}') depth--
            j++
        }
        const entry = { head, selectors: head.startsWith('@') ? [] : splitTopLevel(head), ancestors, decls: {}, list: [], order: out.length }
        out.push(entry)
        entry.list = declarationList(rulesOf(css.slice(open + 1, j - 1), [...ancestors, head], out))
        entry.decls = Object.fromEntries(entry.list)
        i = j
    }
    return own + css.slice(i)
}

/** The declarations of a rule body as [property, value] pairs, in source order. */
function declarationList(body) {
    const out = []
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) out.push([match[1], collapse(match[2])])
    }
    return out
}

const styleOf = (text) => [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
const allRules = () => {
    const out = []
    rulesOf(stripComments(styleOf(sfc)), [], out)
    return out
}

/** The template's AST root nodes. */
const templateRoots = () => parse(sfc).descriptor.template.ast.children

/** A node's attributes and directives as written: { class: value, ':key': exp, ... }. */
function attrsOf(node) {
    const out = {}
    for (const p of node.props) {
        if (p.type === 6) out[p.name] = p.value?.content ?? ''
        else out[p.rawName] = p.exp?.content ?? ''
    }
    return out
}

/** Every element node of a subtree, depth first. */
function elements(nodes, out = []) {
    for (const node of nodes) {
        if (node.type !== 1) continue
        out.push(node)
        elements(node.children ?? [], out)
    }
    return out
}
const hasClass = (node, name) => (attrsOf(node).class ?? '').split(/\s+/).includes(name)
const elementChildren = (node) => node.children.filter((c) => c.type === 1)
const oneByClass = (name) => {
    const found = elements(templateRoots()).filter((n) => hasClass(n, name))
    assert.equal(found.length, 1, `one .${name}`)
    return found[0]
}

const script = () => parse(sfc).descriptor.scriptSetup.content

/** The whole text of a top-level `function name(...) { ... }` of the script. */
function fn(name) {
    const text = script()
    const at = text.indexOf(`\nfunction ${name}(`)
    assert.ok(at >= 0, `function ${name} exists`)
    const end = text.indexOf('\n}\n', at)
    return text.slice(at + 1, end + 2)
}

// The four swap rules of §4.2, in source order.
const SPEC = [
    ['.settings-swap-leave-active', 'transition: opacity var(--motion-dur-1) ease-in-out;'],
    ['.settings-swap-leave-to', 'opacity: 0;'],
    ['.settings-swap-enter-active', `
        transition:
            opacity var(--motion-dur-2) ease-in-out,
            translate var(--motion-dur-3) var(--motion-ease-out);`],
    ['.settings-swap-enter-from', `
        opacity: 0;
        translate: 0 calc(0.75rem * var(--motion-amount));`],
]

test('1. one keyed wrapper in one Transition inside .settings-sections, no appear (§4.1)', () => {
    const sections = oneByClass('settings-sections')
    const children = elementChildren(sections)
    assert.equal(children.length, 1, '.settings-sections holds the Transition only')
    const [transition] = children
    assert.equal(transition.tag, 'Transition')
    assert.deepEqual(attrsOf(transition), {
        name: 'settings-swap',
        ':mode': "isNarrow ? undefined : 'out-in'",
        ':css': '!isNarrow',
        '@before-leave': 'onSectionLeaving',
        '@enter': 'onSectionEnter',
        '@after-leave': 'onSectionLeft',
    }, 'the Transition bindings (no appear)')
    assert.deepEqual(transition.children.map((c) => c.type), [1], 'the wrapper is the only child (no comment, no text)')
    const [wrapper] = transition.children
    assert.equal(wrapper.tag, 'div')
    assert.deepEqual(attrsOf(wrapper), { ':key': 'activeSection', class: 'settings-swap' })
    assert.ok(elementChildren(wrapper).length > 10, 'the sections sit inside the wrapper')
    assert.equal(elements(templateRoots()).filter((n) => n.tag === 'Transition').length, 1, 'one Transition in the template')

    const text = script()
    assert.match(text, /^import \{ useMediaQuery \} from '@vueuse\/core'$/m)
    assert.match(text, /^const isNarrow = useMediaQuery\('\(width < 640px\)'\)$/m)
    assert.ok(stripComments(styleOf(sfc)).includes('@media (width < 640px)'), 'same breakpoint as the slide rules')
})

test('2. the four swap rules pinned whole, in order; no @media, no literal ms (§4.2, §5)', () => {
    const all = allRules()
    const swap = all.filter((r) => r.head.includes('settings-swap'))
    assert.deepEqual(swap.map((r) => [r.ancestors.length, r.head]), SPEC.map(([selector]) => [0, selector]), 'the swap rules, top level, in order')
    for (let k = 1; k < swap.length; k++) assert.equal(swap[k].order, swap[0].order + k, 'the swap rules are consecutive')
    for (const [selector, body] of SPEC) {
        const r = swap.find((x) => x.head === selector)
        assert.deepEqual(r.list, declarationList(body), `${selector}: declaration list`)
        for (const [, value] of r.list) assert.ok(!/\d\s*ms\b/.test(value), `${selector}: no literal ms`)
    }
    for (const media of all.filter((r) => r.head.startsWith('@media'))) {
        const inside = all.filter((r) => r.ancestors.includes(media.head) && r.head.includes('settings-swap'))
        assert.deepEqual(inside, [], `${media.head}: no settings-swap rule`)
    }
    const from = swap.find((x) => x.head === '.settings-swap-enter-from')
    assert.match(from.decls.translate, /var\(--motion-amount\)/)
    for (const r of swap) {
        for (const [property] of r.list) assert.ok(!['transform', 'rotate', 'scale'].includes(property), `${r.head}: ${property}`)
    }
})

test('3. scroll back to the top after the exit; the exit is inert (§4.1, §4.3)', () => {
    const detail = oneByClass('settings-detail')
    assert.equal(attrsOf(detail).ref, 'detailRef')
    assert.match(script(), /^const detailRef = ref\(null\)$/m)
    assert.match(fn('onSectionLeft'), /if \(detailRef\.value\) detailRef\.value\.scrollTop = 0/)
    const leaving = fn('onSectionLeaving')
    assert.match(leaving, /^function onSectionLeaving\(el\) \{/)
    assert.match(leaving, /\bel\.inert = true\b/)
})

test('4. focus after a swap: the afterSwap slot (§4.4)', () => {
    assert.match(script(), /^let afterSwap = null$/m)
    const enter = fn('onSectionEnter')
    const params = enter.match(/^function onSectionEnter\(([^)]*)\)/)[1]
    assert.ok(!params.includes(','), 'at most one parameter: a second one makes Vue wait for done()')
    const clear = enter.indexOf('afterSwap = null')
    const run = enter.indexOf('nextTick(run)')
    assert.ok(clear >= 0 && run >= 0 && clear < run, 'the slot is cleared before its callback is run')
    assert.ok(enter.indexOf('const run = afterSwap') < clear, 'the callback is read before the clear')

    const go = fn('goToPublicBaseUrl')
    assert.match(go, /const swaps = activeSection\.value !== 'general'/)
    assert.match(go, /if \(swaps\) afterSwap = focusInput/)
    assert.match(go, /else nextTick\(focusInput\)/)
    assert.ok(go.indexOf('const swaps') < go.indexOf("selectSection('general')"), 'the swap is detected before the section changes')

    assert.match(fn('onPopoverShow'), /\n {4}afterSwap = null\n/, 'onPopoverShow clears the slot')
})

test('5. the component still compiles: template and styles', () => {
    const { descriptor, errors } = parse(sfc, { filename: 'SettingsPopover.vue' })
    assert.deepEqual(errors, [], 'parse')
    const id = 'data-v-7f7f7f7f'
    const template = compileTemplate({ source: descriptor.template.content, filename: 'SettingsPopover.vue', id })
    assert.deepEqual(template.errors, [], 'template')
    for (const style of descriptor.styles) {
        const compiled = compileStyle({ source: style.content, filename: 'SettingsPopover.vue', id, scoped: style.scoped })
        assert.deepEqual(compiled.errors, [], 'style')
    }
})
