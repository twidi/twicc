import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const sfc = readFileSync(join(here, '..', 'components/session/detail/SessionItem.vue'), 'utf8')
const style = sfc.slice(sfc.indexOf('<style')).replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the user's message is a lifted bubble (a strong accent tint over the
// plain surface, a firm accent border, a floating shadow), so it stands out among the cards without
// recolouring its content.
test('1. the bubble is a pale-centred, edge-tinted card with a firm accent border and a floating shadow (the base, and dark)', () => {
    const m = style.match(/\.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)
    assert.ok(m, 'the user card rule')
    const body = norm(m[1])
    assert.match(body, /border-radius: 18px 18px 4px 18px;/)
    assert.match(body, /border-width: 1\.5px;/)
    assert.match(body, /border-color: color-mix\(in oklab, var\(--wa-color-brand-60\) 72%, transparent\);/)
    assert.match(body, /radial-gradient\(120% 140% at 50% 45%/)
    assert.match(body, /--user-card-solid: var\(--surface-solid, var\(--wa-color-surface-default\)\);/)
})

test('2. dark keeps a strong accent tint in a vertical gradient, a firmer border and a stronger shadow', () => {
    const m = style.match(/\.wa-dark \.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)
    assert.ok(m, 'the dark rule')
    const body = norm(m[1])
    assert.match(body, /border-color: color-mix\(in oklab, var\(--wa-color-brand-60\) 70%, transparent\);/)
    assert.match(body, /color-mix\(in oklab, var\(--wa-color-brand-60\) 38%, var\(--user-card-solid\)\), color-mix\(in oklab, var\(--wa-color-brand-60\) 26%/)
})

test('3. the old tint tokens are gone', () => {
    assert.doesNotMatch(style, /--user-card-base-color|--md-tint/)
    const tokens = readFileSync(join(here, 'transcript-tokens.css'), 'utf8')
    assert.doesNotMatch(tokens, /--user-card-base-color/)
})

test('4. a user message has room below it, and more above it unless it is the first item', () => {
    const m = style.match(/\.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)[1]
    assert.match(norm(m), /margin: calc\(var\(--card-spacing\) - var\(--main-shadow-size\)\) var\(--card-spacing\) calc\(var\(--card-spacing\) \* 2\) auto;/)
    assert.match(norm(style), /\.session-items \.virtual-scroller-item \+ \.virtual-scroller-item \.session-item\[data-kind="user_message"\] \{ margin-top: calc\(var\(--card-spacing\) \* 2\.25 - var\(--main-shadow-size\)\); \}/)
})

// Light only: a solid lit bubble with white text.
test('7. in light the user card is a solid accent gradient with white text', () => {
    const m = style.match(/html:not\(\.wa-dark\) \.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)
    assert.ok(m, 'the light rule')
    const body = norm(m[1])
    assert.match(body, /color: #fff;/)
    assert.match(body, /linear-gradient\(180deg, oklch\(from var\(--wa-color-brand-60\) calc\(l \+ 0\.005\) c h\), oklch\(from var\(--wa-color-brand-60\) calc\(l - 0\.06\) c h\)\)/)
    assert.match(norm(style), /html:not\(\.wa-dark\) \.session-items \.session-item\[data-kind="user_message"\] \.markdown-body \{ color: #fff; \}/)
})

test('9. in light, links, inline code, rules and tables sitting on the bubble read in white, but not inside the light cards', () => {
    const n = norm(style)
    const P = 'html:not\\(\\.wa-dark\\) \\.session-items \\.session-item\\[data-kind="user_message"\\] \\.markdown-body'
    const N = ':not\\(blockquote \\*, \\.md-container \\*, \\.code-tools \\*, pre \\*\\)'
    assert.match(n, new RegExp(P + ' a' + N + ' \\{ color: #fff; text-decoration: underline;'))
    assert.match(n, new RegExp(P + ' code' + N + ' \\{ background: oklch\\(1 0 0 / 0\\.2\\); color: #fff; \\}'))
    assert.match(n, new RegExp(P + ' hr' + N + ' \\{ height: 1px; border: 0; background: oklch\\(1 0 0 / 0\\.45\\); \\}'))
    assert.match(n, new RegExp(P + ' table' + N + ' tr \\{ background: transparent; \\}'))
})

test('10. a user message has a minimum width, capped by the maximum the items share', () => {
    const base = norm(style.match(/\.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)[1])
    assert.match(base, /min-width: min\(12rem, calc\(var\(--max-card-width\) - var\(--card-spacing\) \* 2\)\);/)
    assert.match(style, /max-width: calc\(var\(--max-card-width\) - var\(--card-spacing\) \* 2\);/)
})
