// Home motion (visual refresh step 7b, docs/plans/2026-09-30-home-motion-design.md §7). Same
// approach as stats-motion.test.js: the stylesheets and components are read as text, each
// rule of the spec is pinned whole (selector + ordered declaration list, roadmap §6l.2) and
// the template and script facts the rules rely on are checked. The card-side reveal selector
// is also compiled with @vue/compiler-sfc: a source pin cannot catch a selector Vue rewrites.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { compileStyle } from '@vue/compiler-sfc'

import { HOME_CARD_DURATION_MS, HOME_CARD_STAGGER_MS, HOME_SPARKLINE_REVEAL_MS } from '../utils/homeCardCascade.js'

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
const REDUCED = '@media (prefers-reduced-motion: reduce)'
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

const SPARKLINE = '../components/activity/ActivitySparkline.vue'
const WORKSPACE_CARD = '../components/workspace/WorkspaceCard.vue'
const PROJECT_CARD = '../components/project/ProjectCard.vue'
const HOME_VIEW = '../views/HomeView.vue'
const DETAIL_HEADER = '../components/project/ProjectDetailHeader.vue'

const CARD_ENTERING = '.home-card-entering .activity-sparkline'
const SVG_REVEAL = '.activity-sparkline.activity-sparkline--reveal'

test('1. motion.css: the home card cascade (§3.2), timings of utils/homeCardCascade.js', () => {
    const all = parseAll(read('motion.css'))
    assertKeyframes(all, 'home-card-in', [
        [['from'], 'opacity: 0; translate: 0 calc(0.375rem * var(--motion-amount));'],
    ])
    assertPinned(rule(all, ['.home-card-entering'], topLevel), `
        animation: home-card-in 420ms var(--motion-ease-out) backwards;
        animation-delay: calc(var(--home-card-index, 0) * 60ms);
    `)
    assert.equal(HOME_CARD_DURATION_MS, 420)
    assert.equal(HOME_CARD_STAGGER_MS, 60)
})

test('2. ActivitySparkline: the reveal, both keyframes, the reduced-motion fade (§4.1)', () => {
    const all = parseAll(styleOf(read(SPARKLINE)))

    assertPinned(rule(all, ['.activity-sparkline'], topLevel), 'display: block;')

    const reveal = rule(all, [CARD_ENTERING, SVG_REVEAL], topLevel)
    assertPinned(reveal, `
        animation: activity-sparkline-reveal 800ms var(--motion-ease-out) backwards;
        animation-delay: calc(var(--home-card-index, 0) * 60ms);
    `)
    assert.equal(HOME_SPARKLINE_REVEAL_MS, 800)
    assertKeyframes(all, 'activity-sparkline-reveal', [
        [['from'], 'clip-path: inset(0 100% 0 0);'],
        [['to'], 'clip-path: inset(0 0 0 0);'],
    ])

    const reduced = block(all, REDUCED)
    assert.deepEqual(reduced.inside.map((r) => r.selectors), [[CARD_ENTERING, SVG_REVEAL]], 'the block holds one rule')
    assertPinned(reduced.inside[0], `
        animation: activity-sparkline-fade 300ms ease-in-out backwards;
        animation-delay: calc(var(--home-card-index, 0) * 60ms);
    `)
    assert.ok(reduced.block.order > reveal.order, 'the reduced-motion block comes after the reveal rule')
    assertKeyframes(all, 'activity-sparkline-fade', [[['from'], 'opacity: 0;']])
})

test('3. ActivitySparkline: the card-side selector survives scoping (§4.1)', () => {
    const id = 'data-v-7b7b7b7b'
    const { code, errors } = compileStyle({ source: styleOf(read(SPARKLINE)), filename: 'ActivitySparkline.vue', id, scoped: true })
    assert.deepEqual(errors, [], 'no compile errors')
    const compiled = parseAll(code)
    const reveals = compiled.filter((r) => r.selectors.includes(`${SVG_REVEAL}[${id}]`))
    assert.equal(reveals.length, 2, 'two compiled reveal rules (base, reduced motion)')
    for (const r of reveals) {
        assert.ok(r.selectors.includes(`${CARD_ENTERING}[${id}]`), `"${r.head}" keeps the card ancestor unscoped`)
    }
})

test('4. ActivitySparkline: the reveal prop and its class (§4.2)', () => {
    const sfc = read(SPARKLINE)
    assert.match(scriptOf(sfc), /reveal:\s*\{\s*type:\s*Boolean,\s*default:\s*false,?\s*\}/, 'a Boolean `reveal` prop, false by default')
    const svgs = openingTags(templateOf(sfc), 'svg').map(attrsOf)
    assert.equal(svgs.length, 1, 'one <svg')
    assert.equal(svgs[0].class, 'activity-sparkline')
    assert.equal(svgs[0][':class'], "{ 'activity-sparkline--reveal': reveal }")
})

/** The card rules of §5, shared by both cards. */
function assertCardRules(file, name, { disabled }) {
    const sfc = read(file)
    const all = parseAll(styleOf(sfc))
    const card = `.${name}`

    assertPinned(rule(all, [card], topLevel), `
        cursor: pointer;
        transition:
            translate var(--motion-dur-2) var(--motion-ease-spring),
            box-shadow var(--motion-dur-2) var(--motion-ease),
            border-color var(--motion-dur-2) var(--motion-ease);
    `)
    assertPinned(rule(all, ['&::part(body)'], (r) => r.ancestors.length === 1 && r.ancestors[0] === card), 'position: relative;')
    assertPinned(rule(all, [`${card}.home-card-entering`], topLevel), `
        transition:
            box-shadow var(--motion-dur-2) var(--motion-ease),
            border-color var(--motion-dur-2) var(--motion-ease);
    `)

    const hover = block(all, HOVER)
    const expected = [[`${card}:hover`]]
    if (disabled) expected.push([`${card}.disabled:hover`])
    assert.deepEqual(hover.inside.map((r) => r.selectors), expected, `${name}: the hover block's rules`)
    assertPinned(hover.inside[0], `
        translate: 0 calc(-0.125rem * var(--motion-amount));
        border-color: color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 55%, transparent);
        box-shadow: var(--depth-2), 0 0.5rem 1.75rem -0.75rem color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 70%, transparent);
    `)
    if (disabled) {
        assertPinned(hover.inside[1], `
            translate: none;
            border-color: var(--wa-color-surface-border);
            box-shadow: var(--wa-shadow-s);
        `)
        assertPinned(rule(all, [`${card}.disabled`], topLevel), 'opacity: 0.5; cursor: not-allowed;')
    }
    const hovers = all.filter((r) => r.selectors.some((s) => s.startsWith(card) && s.includes(':hover')))
    assert.equal(hovers.length, expected.length, `${name}: no hover rule outside the block`)

    const cards = openingTags(templateOf(sfc), 'wa-card').map(attrsOf)
    assert.equal(cards.length, 1, `${name}: one <wa-card`)
    assert.equal(cards[0].class, name)
    assert.equal(cards[0].ref, 'cardRef')
    return { attrs: cards[0], script: scriptOf(sfc) }
}

test('5. WorkspaceCard: lift and glow in the workspace colour, entrance (§3.1, §5)', () => {
    const { attrs, script } = assertCardRules(WORKSPACE_CARD, 'workspace-card', { disabled: true })
    assert.equal(attrs[':style'], "{ '--card-glow-color': workspace.color || null }")
    assert.match(script, /const cardRef = ref\(null\)/)
    assert.match(script, /useHomeCardEntrance\(cardRef\)/)
})

test('6. ProjectCard: lift and glow in the dot colour, entrance (§3.1, §5)', () => {
    const { attrs, script } = assertCardRules(PROJECT_CARD, 'project-card', { disabled: false })
    assert.equal(attrs[':style'], "{ '--card-glow-color': dotColor || null }")
    assert.match(script, /const cardRef = ref\(null\)/)
    assert.match(script, /useHomeCardEntrance\(cardRef\)/)
    assert.match(script, /useProjectMark\(computed\(\(\) => props\.project\.id\)\)/)
})

test('7. HomeView: the coordinator and the header reveal (§3.1, §4.2)', () => {
    const sfc = read(HOME_VIEW)
    assert.match(scriptOf(sfc), /^\s*provideHomeCardCascade\(\)\s*$/m, 'provideHomeCardCascade() called')
    const sparklines = openingTags(templateOf(sfc), 'ActivitySparkline').map(attrsOf)
    assert.equal(sparklines.length, 1, 'one header sparkline')
    assert.ok('reveal' in sparklines[0], 'the header sparkline has reveal')
})

test('8. ProjectDetailHeader: the header reveal (§4.2)', () => {
    const sparklines = openingTags(templateOf(read(DETAIL_HEADER)), 'ActivitySparkline').map(attrsOf)
    assert.equal(sparklines.length, 1, 'one header sparkline')
    assert.ok('reveal' in sparklines[0], 'the header sparkline has reveal')
})
