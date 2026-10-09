import { test } from 'node:test'
import assert from 'node:assert/strict'
import postcss from 'postcss'
import { flattenTheme } from './flattenTheme.js'

const run = (css, options) => postcss([flattenTheme(options)]).process(css, { from: '/x/artifact-theme/theme.css' }).css

// These tests catch dark values that never switch, side effects on page styles, and
// leftovers of selectors or layers an artifact document cannot match.
test('light selectors become :root and .wa-dark becomes a prefers-color-scheme block, in source order', () => {
    const out = run(`
        @layer wa-theme, wa-color;
        @layer wa-theme {
            :where(:root), .wa-theme-default, .wa-dark .wa-invert { --surface: white; color-scheme: light; }
            .wa-dark, .wa-invert { --surface: black; color-scheme: dark; }
        }
        .wa-brand-cyan { --brand: cyan; }
        :root { --tint: 217; }
    `)
    assert.equal(out.replace(/\s+/g, ' ').trim(), [
        ':root { --surface: white }',
        '@media (prefers-color-scheme: dark) { :root { --surface: black } }',
        ':root { --brand: cyan }',
        ':root { --tint: 217 }',
    ].join(' '))
})

test('regular properties, other selectors and other at-rules are dropped', () => {
    const out = run(`
        body { font-variant-numeric: tabular-nums; --body-only: 1; }
        wa-divider { --color: red; }
        .wa-brand-red { --brand: red; }
        :root { color: black; }
        @media (max-width: 40rem) { :root { --gap: 2px; } }
        @property --x { syntax: '<length>'; inherits: false; initial-value: 0; }
    `)
    assert.equal(out.trim(), '')
})

test('a rule matching both schemes is emitted for each', () => {
    const out = run(':where(:root), .wa-dark { --font: system-ui; }').replace(/\s+/g, ' ').trim()
    assert.equal(out, ':root { --font: system-ui } @media (prefers-color-scheme: dark) { :root { --font: system-ui } }')
})

test('files outside the match are left untouched', () => {
    const css = '.wa-dark { --surface: black; } body { margin: 0; }'
    assert.equal(run(css, { match: file => file.endsWith('/kit.css') }), css)
})
