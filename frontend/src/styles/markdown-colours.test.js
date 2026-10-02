import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const sfc = readFileSync(join(here, '..', 'components/ui/MarkdownContent.vue'), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()
const css = norm(sfc.slice(sfc.indexOf('<style')))

// Retouches: links and tables of the markdown renders wear the app's colours, not GitHub's blue and greys.
test('1. links take the theme link colour (the accent: darker in light, lighter in dark)', () => {
    assert.ok(css.includes('.markdown-body a { color: var(--wa-color-text-link); }'))
})

test('2. tables: transparent rows, a faint accent tint on even rows (a little stronger in dark), a stronger header, divider-coloured rules', () => {
    // (the rule may carry other declarations, e.g. a minimum cell width: only the colour is pinned)
    assert.ok(css.includes('.markdown-body table th, .markdown-body table td { border-color: var(--divider-color, var(--wa-color-surface-border));'))
    assert.ok(css.includes('.markdown-body table tr { background-color: transparent; border-top-color: var(--divider-color, var(--wa-color-surface-border)); }'))
    assert.ok(css.includes('.markdown-body table tr:nth-child(2n) { background-color: color-mix(in oklab, var(--wa-color-brand-60) 6%, transparent); }'))
    assert.ok(css.includes('.markdown-body table th { background-color: color-mix(in oklab, var(--wa-color-brand-60) 12%, transparent); }'))
    assert.ok(css.includes('.wa-dark .markdown-body table tr:nth-child(2n) { background-color: color-mix(in oklab, var(--wa-color-brand-60) 10%, transparent); }'))
    assert.ok(css.includes('.wa-dark .markdown-body table th { background-color: color-mix(in oklab, var(--wa-color-brand-60) 18%, transparent); }'))
})

test('3. no GitHub colour left in these rules (the blue and the greys come from github-markdown-css only)', () => {
    const block = css.slice(css.indexOf('.markdown-body a { color'), css.indexOf('.wa-dark .markdown-body table th'))
    assert.doesNotMatch(block, /#[0-9a-f]{3,8}|fgColor|bgColor|borderColor/i)
})
