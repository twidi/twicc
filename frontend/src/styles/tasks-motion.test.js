// Tasks tab motion (visual refresh step 7c, docs/plans/2026-09-30-tasks-motion-design.md §8).
// Same approach as home-motion.test.js: the components are read as text, each rule of the
// spec is pinned whole (selector + ordered declaration list, roadmap §6l.2) and the template
// and script facts the rules rely on are checked. TaskPane's scoped :deep() selectors are
// also compiled with @vue/compiler-sfc: a source pin cannot catch a selector Vue rewrites.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { compileStyle } from '@vue/compiler-sfc'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Split on commas at parenthesis depth 0 (selector lists, value lists). */
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
 *  { head, selectors, decls, list, order, ancestors } (list = the [property, value] pairs in
 *  source order; ancestors = the enclosing heads). */
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

/** Every rule, at-rules included (@property, @keyframes, @media, @supports). */
function parseAll(css) {
    const out = []
    rulesOf(stripComments(css), [], out)
    return out
}

/** A rule pinned whole: its ordered declaration list equals the spec block's exactly. */
function assertPinned(r, specBody, label = r.head) {
    assert.deepEqual(r.list, declarationList(stripComments(specBody)), `${label}: declaration list`)
}

const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
const templateOf = (sfc) => sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')
const scriptOf = (sfc) => [...sfc.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n')

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const topLevel = (r) => r.ancestors.length === 0
// Reduced motion is a class on <html> (utils/reducedMotion.js): top-level rules under this prefix.
const RM = ':root.reduce-motion'
const HOVER = '@media (hover: hover)'
const inBlock = (head) => (r) => r.ancestors.length === 1 && r.ancestors[0] === head
const inKeyframes = (name) => inBlock(`@keyframes ${name}`)

/** A top-level @keyframes block pinned frame by frame. */
function assertKeyframes(all, name, frames) {
    const blocks = all.filter((r) => r.head === `@keyframes ${name}` && topLevel(r))
    assert.equal(blocks.length, 1, `one top-level @keyframes ${name}`)
    const actual = all.filter(inKeyframes(name))
    assert.deepEqual(actual.map((r) => r.selectors), frames.map(([selectors]) => selectors), `${name}: frames`)
    frames.forEach(([selectors, body], k) => assertPinned(actual[k], body, `${name} ${selectors.join(', ')}`))
}

/** The one top-level block `head` (an at-rule), and the rules directly inside it, in order. */
function block(all, head) {
    const found = all.filter((r) => r.head === head && topLevel(r))
    assert.equal(found.length, 1, `one top-level ${head}`)
    return { block: found[0], inside: all.filter(inBlock(head)) }
}

/** The attributes of an opening tag, as written: { name: value } (valueless → ''). */
function attrsOf(tag) {
    const inner = tag.replace(/^<[\w-]+/, '').replace(/\/?>$/, '')
    const out = {}
    for (const m of inner.matchAll(/([:@\w.-]+)(?:="([^"]*)")?/g)) out[m[1]] = m[2] ?? ''
    return out
}

/** Every opening tag `<name ...>` of a template, in source order. */
const openingTags = (template, name) => [...template.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g'))].map((m) => m[0])

const TODO = '../components/session/detail/items/TodoContent.vue'
const PANE = '../components/tasks/TaskPane.vue'
const DEEP = '.task-scroll :deep(.todo-item-completed .todo-item-strike)'
const DEEP_TEXT = '.task-scroll :deep(.todo-item-ticking .todo-item-text)'
const DEEP_STRIKE = '.task-scroll :deep(.todo-item-ticking .todo-item-strike)'

test('1. TodoContent: the pop rule carries the rank delay (§4)', () => {
    const all = parseAll(styleOf(read(TODO)))
    assertPinned(rule(all, ['.todo-item-icon--pop'], topLevel), `
        animation: todo-check-pop 420ms var(--motion-ease-spring) both;
        animation-delay: calc(var(--tick-rank, 0) * 150ms);
    `)
})

test('2. TodoContent: ticking class, --tick-rank binding, inner strike span (§4)', () => {
    const sfc = read(TODO)
    const template = templateOf(sfc)
    const li = openingTags(template, 'li').map(attrsOf)
    assert.equal(li.length, 1, 'one <li')
    assert.equal(li[0][':class'], "[`todo-item-${todo.status}`, { 'todo-item-ticking': popping.has(i) }]")
    assert.equal(li[0][':style'], "{ '--tick-rank': popping.get(i) ?? null }")
    assert.ok(
        collapse(template).includes('<span class="todo-item-text"><span class="todo-item-strike">{{ getDetail(todo) }}</span></span>'),
        'the strike span sits inside the text span',
    )
    const script = scriptOf(sfc)
    assert.match(script, /const popping = ref\(new Map\(\)\)/, 'popping is a Map')
    assert.match(script, /\/\/ Indices[\s\S]*?ranks/, 'the comment describes indices and ranks')
    assert.ok(!script.includes('popping.value.add('), 'no Set add left')
})

test('3. TaskPane: strike, colour and keyframes rules (§5)', () => {
    const all = parseAll(styleOf(read(PANE)))
    assertPinned(rule(all, [DEEP], topLevel), `
        background-image: linear-gradient(currentColor, currentColor);
        background-repeat: no-repeat;
        background-position: 0 62%;
        background-size: 100% 1px;
    `)
    assertPinned(rule(all, [DEEP_TEXT], topLevel), `
        transition: color var(--motion-dur-3) var(--motion-ease);
        transition-delay: calc(var(--tick-rank, 0) * 150ms);
    `)
    assertPinned(rule(all, [DEEP_STRIKE], topLevel), `
        animation: task-strike-draw 300ms var(--motion-ease-out) backwards;
        animation-delay: calc(var(--tick-rank, 0) * 150ms);
    `)
    assertKeyframes(all, 'task-strike-draw', [[['from'], 'background-size: 0% 1px;']])
})

test('4. TaskPane: progress line rules and keyframes (§6)', () => {
    const all = parseAll(styleOf(read(PANE)))
    assertPinned(rule(all, ['.task-progress'], topLevel), `
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-2xs);
        padding: var(--wa-space-s) var(--wa-space-s) 0;
    `)
    assertPinned(rule(all, ['.task-progress-label'], topLevel), `
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-text-quiet);
    `)
    assertPinned(rule(all, ['.task-progress-track'], topLevel), `
        height: 6px;
        border-radius: var(--wa-border-radius-pill);
        background: var(--progress-track);
    `)
    assertPinned(rule(all, ['.task-progress-fill'], topLevel), `
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, oklch(from var(--wa-color-success-60) calc(l + 0.08) c h), var(--wa-color-success-60));
        box-shadow: 0 0 0.25rem color-mix(in oklab, var(--wa-color-success-60) 30%, transparent);
        transition: width 600ms var(--motion-ease-out);
        animation: task-progress-fill 600ms var(--motion-ease-out) backwards;
    `)
    assertKeyframes(all, 'task-progress-fill', [[['from'], 'width: 0;']])
})

test('5. TaskPane: the reduced-motion block comes after the strike and progress rules (§7)', () => {
    const all = parseAll(styleOf(read(PANE)))
    const strike = rule(all, [`${RM} ${DEEP_STRIKE}`], topLevel)
    const fill = rule(all, [`${RM} .task-progress-fill`], topLevel)
    assertPinned(strike, 'animation: none;')
    assertPinned(fill, `
        transition: none;
        animation: none;
    `)
    for (const [selector, reducedRule] of [[DEEP_STRIKE, strike], ['.task-progress-fill', fill]]) {
        const base = rule(all, [selector], topLevel)
        assert.ok(reducedRule.order > base.order, `the reduced-motion rule comes after ${selector}`)
    }
})

test('6. TaskPane: template shape, progress header, computed (§6)', () => {
    const sfc = read(PANE)
    const template = templateOf(sfc)
    const flat = collapse(template)
    assert.ok(flat.includes('<div v-if="!tasks" class="task-state">'), 'empty state keeps v-if')
    assert.equal(openingTags(template, 'template').map(attrsOf).filter((a) => 'v-else' in a).length, 1, 'a <template v-else>')
    assert.ok(!openingTags(template, 'div').some((tag) => /\bv-else\b/.test(tag)), 'no div v-else left')
    const elseAt = flat.indexOf('<template v-else>')
    const headerAt = flat.indexOf('v-if="progress.total > 0"')
    const scrollAt = flat.indexOf('class="task-scroll"')
    assert.ok(elseAt >= 0 && headerAt > elseAt && scrollAt > headerAt, 'header v-if sits inside the template, before the scroll')
    assert.ok(flat.includes('<div v-if="progress.total > 0" class="task-progress">'), 'the header div')
    const label = flat.match(/<span class="task-progress-label">([^<]*)<\/span>/)
    assert.equal(label[1], '{{ progress.done }} of {{ progress.total }} done')
    const track = attrsOf(openingTags(template, 'div').find((t) => t.includes('task-progress-track')))
    assert.equal(track.role, 'progressbar')
    assert.equal(track[':aria-valuemin'], '0')
    assert.equal(track[':aria-valuemax'], 'progress.total')
    assert.equal(track[':aria-valuenow'], 'progress.done')
    assert.equal(track['aria-label'], 'Tasks done')
    const fill = attrsOf(openingTags(template, 'div').find((t) => t.includes('task-progress-fill')))
    assert.equal(fill[':style'], "{ width: progress.percent + '%' }")
    const todoTag = attrsOf(openingTags(template, 'TodoContent')[0])
    assert.equal(todoTag[':animate'], 'active')
    assert.match(scriptOf(sfc), /const progress = computed\(\(\) => countTasks\(tasks\.value\?\.items\)\)/)
})

test('7. TaskPane: the :deep() strike rules survive scoping, keyframes are renamed (§8)', () => {
    const id = 'data-v-7c7c7c7c'
    const { code, errors } = compileStyle({ source: styleOf(read(PANE)), filename: 'TaskPane.vue', id, scoped: true })
    assert.deepEqual(errors, [], 'no compile errors')
    const compiled = parseAll(code)
    const strike = `.task-scroll[${id}] .todo-item-completed .todo-item-strike`
    assert.equal(compiled.filter((r) => r.selectors.includes(strike)).length, 1, 'completed strike rule compiled')
    for (const tail of ['.todo-item-text', '.todo-item-strike']) {
        const selector = `.task-scroll[${id}] .todo-item-ticking ${tail}`
        assert.ok(compiled.some((r) => r.selectors.includes(selector)), `${selector} compiled`)
    }
    for (const name of ['task-strike-draw', 'task-progress-fill']) {
        const renamed = `${name}-${id.replace('data-v-', '')}`
        assert.ok(compiled.some((r) => r.head === `@keyframes ${renamed}`), `@keyframes ${renamed} exists`)
        assert.ok(compiled.some((r) => (r.decls.animation ?? '').includes(renamed)), `an animation references ${renamed}`)
    }
})
