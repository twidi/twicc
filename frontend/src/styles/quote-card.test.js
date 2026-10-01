import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()
const css = strip(read('quote-card.css'))

// Visual refresh retouches: everything that quotes is a rounded card with a thicker accent left
// border; code blocks are the same card in the neutral colour. One stylesheet for all of it.
test('1. a quote is a rounded opaque gradient card whose left border is the accent, with a faint glow', () => {
    const m = css.match(/\n\.quote-card,\s*\.markdown-body blockquote,\s*\.markdown-body \.md-container \{([^}]*)\}/)
    assert.ok(m, 'the card rule')
    const body = norm(m[1])
    assert.match(body, /border: 1px solid color-mix\(in oklab, var\(--quote-c\) 24%, transparent\);/)
    assert.match(body, /border-inline-start: 1px solid var\(--quote-c\);/)
    assert.match(body, /border-radius: 0\.75rem;/)
    assert.match(body, /linear-gradient\(100deg, color-mix\(in oklab, var\(--quote-c\) 13%, var\(--quote-solid\)\), color-mix\(in oklab, var\(--quote-c\) 3%, var\(--quote-solid\)\)\)/)
    assert.match(body, /box-shadow: -2px 0 6px -4px/)
    assert.doesNotMatch(css, /::before/, 'no separate rail: the border is the rail')
    assert.doesNotMatch(css, /linear-gradient[^;]*transparent\)[^;]*\);/, 'no transparent stop in the card gradients')
})

test('2. nested quotes alternate a flat lighter card and a stronger one, by border and fill, not by the surface token', () => {
    assert.match(css, /\.markdown-body blockquote blockquote \{[^}]*background-image: none;/)
    assert.match(css, /\.markdown-body blockquote blockquote blockquote,\s*\.markdown-body blockquote blockquote blockquote blockquote blockquote \{[^}]*border-inline-start-color: var\(--quote-c\);/)
    // The surface token is transparent inside cards: it only appears as the fallback of --quote-solid.
    assert.equal(css.match(/--wa-color-surface-default/g).length, 1)
    assert.match(css, /--quote-solid: var\(--surface-solid, var\(--wa-color-surface-default\)\);/)
})

test('3. the container keeps a plain hairline; its label is a plain accent title', () => {
    assert.match(norm(css), /\.markdown-body \.md-container \{ margin: 1em 0; padding: 0\.75em 1em 1em;[^}]*border: 1px solid color-mix\(in oklab, var\(--quote-c\) 34%, transparent\);/)
    // The label is a plain title, not a chip: no border, background or shadow of its own.
    const label = norm(css.match(/\n\.markdown-body \.md-container-label \{([^}]*)\}/)[1])
    assert.doesNotMatch(label, /border|background|box-shadow|padding/)
    assert.match(label, /color: color-mix\(in oklab, var\(--quote-c\) 60%, var\(--wa-color-text-normal\)\);/)
})

test('4. a code block is the same card in the neutral colour, with its tokens left clear', () => {
    const body = norm(css.match(/\n\.markdown-body pre,\s*\.jhv-pre,\s*\.jhv-markdown \.markdown-body \{([^}]*)\}/)[1])
    assert.match(body, /--quote-c: color-mix\(in oklab, var\(--wa-color-neutral-60\) 55%, var\(--wa-color-brand-60\)\);/)
    assert.match(body, /background-color: transparent !important;/)
    assert.match(css, /\.markdown-body pre\.shiki span \{\s*background-color: transparent !important;/)
})

test('5. the renderer inlines the stylesheet first, and the quote boxes of the app use the class', () => {
    const md = read('../components/ui/MarkdownContent.vue')
    assert.match(md, /<style>\n(?:\/\*[\s\S]*?\*\/\n)?@import '\.\.\/\.\.\/styles\/quote-card\.css';/)
    for (const [f, cls] of [['../components/peer/PeerInboxRow.vue', 'pir__message quote-card'], ['../components/peer/PeerMessageReviewDialog.vue', 'pr-quote quote-card'], ['../components/session/detail/TextSelectionComment.vue', 'tsc-quote quote-card']]) {
        const s = read(f)
        assert.ok(s.includes(`class="${cls}"`), `${f}: ${cls}`)
        assert.doesNotMatch(strip(s), /border-inline-start: 2px solid var\(--wa-color-brand-fill-loud\)/, `${f}: no copy of the old recipe`)
    }
    assert.match(read('../main.js'), /import '\.\/styles\/quote-card\.css'/)
})

test('6. the generic JSON view values are the same neutral card, with no card inside the card', () => {
    assert.match(css, /\.jhv-markdown \.markdown-body pre \{\s*border: 0;\s*border-radius: 0;\s*box-shadow: none;\s*background-image: none;/)
    const jhv = strip(read('../components/json/JsonHumanView.vue'))
    assert.doesNotMatch(jhv, /\.jhv-pre, \.jhv-markdown :deep\(\.markdown-body\) \{[^}]*(background|border-radius)/)
})

test('7. a JSON view value that is only a code block has no padding of its own around the block', () => {
    const jhv = strip(read('../components/json/JsonHumanView.vue'))
    assert.match(norm(jhv), /\.jhv-markdown :deep\(\.markdown-body:has\(> \.markdown-block:only-child > \.code-tools:only-child\)\) \{ padding: 0; \}/)
})

test('8. a `::` line and a container title share the same semibold weight', () => {
    const line = norm(css.match(/\n\.markdown-body \.md-line \{([^}]*)\}/)[1])
    const label = norm(css.match(/\n\.markdown-body \.md-container-label \{([^}]*)\}/)[1])
    assert.match(line, /font-weight: var\(--wa-font-weight-semibold\);/)
    assert.match(label, /font-weight: var\(--wa-font-weight-semibold\);/)
})

test('9. the quote of a container keeps its own margins: room below it before the comment', () => {
    assert.match(css, /\.markdown-body \.md-container > blockquote \{\s*margin: 0\.2em 0 1em;/)
    // A rule that matched this quote with a higher specificity used to shrink the margin to 0.2em.
    assert.doesNotMatch(css, /\.md-container blockquote:not\(/)
})
