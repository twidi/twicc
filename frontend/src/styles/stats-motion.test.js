// Project stats motion (visual refresh step 7a, docs/plans/2026-09-30-stats-motion-design.md
// §8). Same approach as glow.test.js: the stylesheets and components are read as text, each
// rule of the spec is pinned whole (selector + ordered declaration list, roadmap §6l.2) and
// the template facts the rules rely on are checked.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

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

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const topLevel = (r) => r.ancestors.length === 0
// Reduced motion is a class on <html> (utils/reducedMotion.js): a top-level rule under this prefix.
const RM = ':root.reduce-motion'
const inKeyframes = (name) => (r) => r.ancestors.length === 1 && r.ancestors[0] === `@keyframes ${name}`

/** A top-level @keyframes block pinned frame by frame. */
function assertKeyframes(all, name, frames) {
    const blocks = all.filter((r) => r.head === `@keyframes ${name}` && topLevel(r))
    assert.equal(blocks.length, 1, `one top-level @keyframes ${name}`)
    const actual = all.filter(inKeyframes(name))
    assert.deepEqual(actual.map((r) => r.selectors), frames.map(([selectors]) => selectors), `${name}: frames`)
    frames.forEach(([selectors, body], k) => assertPinned(actual[k], body, `${name} ${selectors.join(', ')}`))
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

const ANIMATED_NUMBER = '../components/ui/AnimatedNumber.vue'
const DASHBOARD = '../components/activity/ActivityDashboard.vue'
const GRAPH = '../components/activity/ContributionGraph.vue'
const SPARKLINES = '../components/activity/ContributionSparklines.vue'

test('1. motion.css registers --heat-week-delay (§5.2)', () => {
    const all = parseAll(read('motion.css'))
    const found = all.filter((r) => r.head === '@property --heat-week-delay')
    assert.equal(found.length, 1, 'one @property --heat-week-delay')
    assert.ok(topLevel(found[0]), 'top-level')
    assertPinned(found[0], `syntax: '<time>'; inherits: true; initial-value: 0s;`)
})

test('2. ContributionGraph: the heatmap wave (§5.2)', () => {
    const all = parseAll(styleOf(read(GRAPH)))
    const SUPPORTS = '@supports (animation-delay: calc(sibling-index() * 1ms))'

    const cell = rule(all, ['.contribution-graph :deep(.vch__day__square)'], topLevel)
    assertPinned(cell, 'animation: heatmap-cell-in 360ms ease-in-out backwards;')

    const supports = all.filter((r) => r.head === SUPPORTS)
    assert.equal(supports.length, 1, 'one @supports block')
    assert.ok(topLevel(supports[0]), 'the @supports block is top-level')
    const inSupports = all.filter((r) => r.ancestors.length === 1 && r.ancestors[0] === SUPPORTS)
    assert.deepEqual(inSupports.map((r) => r.selectors), [
        ['.contribution-graph :deep(.vch__month__wrapper)'],
        ['.contribution-graph :deep(.vch__day__square)'],
    ], 'the @supports block holds exactly the two rules, in order')
    assertPinned(inSupports[0], '--heat-week-delay: calc(sibling-index() * 14ms);')
    assertPinned(inSupports[1], 'animation-delay: calc(var(--heat-week-delay) + sibling-index() * 10ms);')
    assert.ok(cell.order < supports[0].order, 'the @supports block comes after the cell rule')

    assertKeyframes(all, 'heatmap-cell-in', [[['from'], 'opacity: 0;']])
})

test('3. ContributionGraph: always horizontal (§5.1)', () => {
    const sfc = read(GRAPH)
    for (const word of ['vertical', 'isVertical', 'useElementSize', 'graphContainer']) {
        assert.ok(!sfc.includes(word), `no "${word}" left`)
    }
})

test('4. ContributionSparklines: the reveal, the reduced-motion fade, area and halo rules (§6, §7)', () => {
    const all = parseAll(styleOf(read(SPARKLINES)))

    const svg = rule(all, ['.contribution-sparkline'], topLevel)
    assertPinned(svg, `
        display: block;
        width: 100%;
        height: 150px;
        overflow: visible;
        animation: sparkline-reveal 1100ms var(--motion-ease-out) backwards;
    `)
    assertKeyframes(all, 'sparkline-reveal', [
        [['from'], 'clip-path: inset(-1rem 100% -1rem -1rem);'],
        [['to'], 'clip-path: inset(-1rem -1rem -1rem -1rem);'],
    ])

    const reduced = all.filter((r) => r.head.startsWith(RM))
    assert.deepEqual(reduced.map((r) => r.selectors), [[`${RM} .contribution-sparkline`]], 'one reduced-motion rule')
    assert.ok(topLevel(reduced[0]), 'the reduced-motion rule is top-level')
    assertPinned(reduced[0], 'animation: sparkline-fade 300ms ease-in-out backwards;')
    assert.ok(reduced[0].order > svg.order, 'the reduced-motion rule comes after the .contribution-sparkline rule')
    assertKeyframes(all, 'sparkline-fade', [[['from'], 'opacity: 0;']])

    assertPinned(rule(all, ['.sparkline-area'], topLevel), 'stroke: none;')
    assertPinned(rule(all, ['.sparkline-line'], topLevel),
        'filter: drop-shadow(0 0 0.1875rem color-mix(in oklab, var(--curve-color) 60%, transparent));')
})

test('5. ContributionSparklines: gradient area in Separate mode, halo on every line (§6.2, §6.3)', () => {
    const sfc = read(SPARKLINES)
    const template = templateOf(sfc)

    assert.ok(!sfc.includes('gradientId') && !sfc.includes('maskId'), 'no gradientId / maskId left')

    const gradients = openingTags(template, 'linearGradient')
    assert.equal(gradients.length, 1, 'exactly one <linearGradient')
    assert.deepEqual(attrsOf(gradients[0]), {
        ':id': '`${uid}-${curve.key}-area`',
        x1: '0', x2: '0', y1: '1', y2: '0',
    })
    const gradientBody = template.match(/<linearGradient\b[\s\S]*?<\/linearGradient>/)[0]
    const stops = openingTags(gradientBody, 'stop').map(attrsOf)
    assert.deepEqual(stops, [
        { offset: '0', ':stop-color': 'colorVars(curve.colorPrefix).stroke', 'stop-opacity': '0.35' },
        { offset: '1', ':stop-color': 'colorVars(curve.colorPrefix).stroke', 'stop-opacity': '0' },
    ])

    const polygons = openingTags(template, 'polygon')
    assert.equal(polygons.length, 1, 'exactly one <polygon')
    assert.deepEqual(attrsOf(polygons[0]), {
        class: 'sparkline-area',
        ':transform': '`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`',
        ':points': 'areaPoints(curve.points, MIN_Y)',
        ':fill': '`url(#${uid}-${curve.key}-area)`',
    })

    // Combined mode renders no <defs> and no area: both live in the Separate branch only.
    const separateAt = template.indexOf('<template v-else>')
    assert.ok(separateAt > 0, 'the Separate branch exists')
    const combined = template.slice(0, separateAt)
    assert.ok(!combined.includes('<defs') && !combined.includes('<polygon'), 'no <defs> nor area in Combined mode')
    assert.ok(template.indexOf('<defs') > separateAt, 'the <defs> is in the Separate branch')
    assert.ok(template.indexOf('<polygon') < template.lastIndexOf('<polyline'), 'the area comes before the Separate polyline')

    const polylines = openingTags(template, 'polyline').map(attrsOf)
    assert.equal(polylines.length, 2, 'two polylines (Combined, Separate)')
    for (const attrs of polylines) {
        assert.equal(attrs.class, 'sparkline-line')
        assert.equal(attrs[':style'], "{ '--curve-color': colorVars(curve.colorPrefix).stroke }")
    }
})

test('6. AnimatedNumber: the fade that starts the count (§4.2)', () => {
    const sfc = read(ANIMATED_NUMBER)
    const all = parseAll(styleOf(sfc))
    assertPinned(rule(all, ['.animated-number'], topLevel),
        'animation: animated-number-in var(--motion-dur-2) ease-in-out backwards;')
    assertKeyframes(all, 'animated-number-in', [[['from'], 'opacity: 0;']])

    const template = templateOf(sfc).replace(/<!--[\s\S]*?-->/g, '').replace(/<\/?template>/g, '').trim()
    const root = template.match(/^<[\w-]+\b[^>]*>/)
    assert.ok(root, 'a root element')
    const attrs = attrsOf(root[0])
    assert.ok(root[0].startsWith('<span'), 'the root is a span')
    assert.equal(attrs.class, 'animated-number')
    assert.ok('@animationstart' in attrs, '@animationstart on the root')
})

test('7. ActivityDashboard: the counted numbers and their text gradient (§4.3, §4.4)', () => {
    const sfc = read(DASHBOARD)
    const all = parseAll(styleOf(sfc))
    assertPinned(rule(all, ['.stat-number'], topLevel), `
        background: linear-gradient(180deg, var(--wa-color-text-normal),
            color-mix(in oklab, var(--wa-color-text-normal) 70%, var(--wa-color-brand-60)));
        background-clip: text;
        color: transparent;
    `)
    assertPinned(rule(all, ['.stat-number :deep(.cost-icon)'], topLevel), 'color: var(--wa-color-text-normal);')

    const template = templateOf(sfc)
    const numbers = openingTags(template, 'AnimatedNumber').map(attrsOf)
    assert.deepEqual(numbers, [
        { ':value': 'period.mainValue', format: 'integer', class: 'wa-heading-2xl stat-number' },
        { ':value': 'period.sub1Value', format: 'average', class: 'wa-heading-l stat-number' },
        { ':value': 'period.sub1Value', format: 'cost', class: 'wa-heading-l stat-number' },
        { ':value': 'period.sub2Value', format: 'cost', class: 'wa-heading-l stat-number' },
    ])
    for (const gone of [
        '{{ period.mainValue }}',
        'wa-heading-l">{{ formatAvg(period.sub1Value)',
        '<CostDisplay :cost="period.sub1Value" class="wa-heading-l"',
        '<CostDisplay :cost="period.sub2Value" class="wa-heading-l"',
    ]) {
        assert.ok(!template.includes(gone), `no "${gone}" left`)
    }
})
