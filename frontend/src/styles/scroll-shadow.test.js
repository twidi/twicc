import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { edgeState, boxMetrics, SCROLLING_PARTS, EDGE_TOLERANCE } from '../utils/scrollEdges.js'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

test('1. edgeState: at the top only more below, in the middle both, at the end only more above', () => {
    const box = { scrollHeight: 500, clientHeight: 200 }
    assert.deepEqual(edgeState({ ...box, scrollTop: 0 }), { top: false, bottom: true })
    assert.deepEqual(edgeState({ ...box, scrollTop: 100 }), { top: true, bottom: true })
    assert.deepEqual(edgeState({ ...box, scrollTop: 300 }), { top: true, bottom: false })
})

test('2. edgeState: nothing to scroll, no edge; a sub-pixel gap is not an edge', () => {
    assert.deepEqual(edgeState({ scrollTop: 0, scrollHeight: 200, clientHeight: 200 }), { top: false, bottom: false })
    assert.deepEqual(edgeState({ scrollTop: 0, scrollHeight: 200 + EDGE_TOLERANCE, clientHeight: 200 }), { top: false, bottom: false })
    assert.deepEqual(edgeState({ scrollTop: 298.6, scrollHeight: 500, clientHeight: 200 }), { top: true, bottom: false })
})

test('3. the Web Awesome parts that scroll: dialog and drawer body only', () => {
    assert.deepEqual(SCROLLING_PARTS, { 'wa-dialog': 'body', 'wa-drawer': 'body' })
})

const css = norm(read('scroll-shadow.css'))

test('3b. boxMetrics: a "normal" or missing gap is zero, side paddings pass through', () => {
    assert.deepEqual(boxMetrics({ rowGap: 'normal', paddingLeft: '16px', paddingRight: '16px' }), { gap: '0px', left: '16px', right: '16px', top: '0px', bottom: '0px' })
    assert.deepEqual(boxMetrics({ rowGap: '12px', paddingLeft: '0px', paddingRight: '8px', paddingTop: '24px', paddingBottom: '20px' }), { gap: '12px', left: '0px', right: '8px', top: '24px', bottom: '20px' })
    assert.deepEqual(boxMetrics({}), { gap: '0px', left: '0px', right: '0px', top: '0px', bottom: '0px' })
})

test('4. the edge numbers are registered inherited numbers (the pseudo-elements read them), the shadow is tinted with the accent', () => {
    for (const name of ['--scroll-edge-top', '--scroll-edge-bottom']) {
        assert.match(css, new RegExp(`@property ${name} \\{ syntax: '<number>'; inherits: true; initial-value: 0; \\}`))
    }
    assert.match(css, /--scroll-shadow: color-mix\(in oklab, var\(--wa-color-brand-40\) 30%, transparent\);/)
    // Dark: a lighter, stronger accent step, so it registers on a dark surface.
    assert.match(css, /\.wa-dark \{ --scroll-shadow: color-mix\(in oklab, var\(--wa-color-brand-70\) 40%, transparent\); \}/)
})

const TARGETS = ['wa-dialog::part(body)', 'wa-drawer::part(body)', '[data-scroll-shadow]']

test('5. the transition sits on the scroller that owns the numbers', () => {
    assert.match(css, /wa-dialog::part\(body\), wa-drawer::part\(body\), \[data-scroll-shadow\] \{ transition: --scroll-edge-top 0.2s ease, --scroll-edge-bottom 0.2s ease; \}/)
})

test('5a. two in-flow sticky pseudo-elements above the content, pulled back over their height and the gap', () => {
    const both = TARGETS.flatMap((t) => [`${t}::before`, `${t}::after`]).join(', ')
    assert.ok(css.includes(`${both} { content: ''; display: block; flex-shrink: 0; position: sticky; z-index: 2; height: var(--scroll-shadow-size); margin-inline: calc(-1 * var(--scroll-edge-pad-left, 0px)) calc(-1 * var(--scroll-edge-pad-right, 0px)); pointer-events: none; }`))
    const before = TARGETS.map((t) => `${t}::before`).join(', ')
    assert.ok(css.includes(`${before} { top: calc(-1 * var(--scroll-edge-pad-top, 0px)); margin-block-end: calc(-1 * (var(--scroll-shadow-size) + var(--scroll-edge-gap, 0px))); background: radial-gradient(ellipse 75% 100% at 50% 0, var(--scroll-shadow) 20%, transparent); opacity: var(--scroll-edge-top); }`))
    const after = TARGETS.map((t) => `${t}::after`).join(', ')
    assert.ok(css.includes(`${after} { bottom: calc(-1 * var(--scroll-edge-pad-bottom, 0px)); margin-block-start: calc(-1 * (var(--scroll-shadow-size) + var(--scroll-edge-gap, 0px))); background: radial-gradient(ellipse 75% 100% at 50% 100%, var(--scroll-shadow) 20%, transparent); opacity: var(--scroll-edge-bottom); }`))
})

test('5b. never a mask or a blur, never a glass surface: it would make a backdrop root and drop the glass layer', () => {
    assert.doesNotMatch(css, /mask|filter:|backdrop/)
    for (const glass of ['wa-dropdown', 'wa-select', 'wa-popover', 'wa-tooltip', '::part(dialog)']) {
        assert.ok(!css.replace(/\/\*[\s\S]*?\*\//g, '').includes(glass), glass)
    }
})

test('6. wired in main.js: stylesheet, install, directive', () => {
    const main = read('../main.js')
    assert.ok(main.includes("import './styles/scroll-shadow.css'"))
    assert.ok(main.includes('installScrollEdges()'))
    assert.ok(main.includes("app.directive('scroll-shadow', vScrollShadow)"))
})
