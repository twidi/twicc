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
test('1. the user card is a pale-centred, edge-tinted bubble with a firm accent border and a floating shadow', () => {
    const m = style.match(/\.session-items \.session-item\[data-kind="user_message"\] \{([^}]*)\}/)
    assert.ok(m, 'the user card rule')
    const body = norm(m[1])
    assert.match(body, /border-width: 1\.5px;/)
    assert.match(body, /border-color: color-mix\(in oklab, var\(--wa-color-brand-60\) 72%, transparent\);/)
    assert.match(body, /radial-gradient\(120% 140% at 50% 45%, color-mix\(in oklab, var\(--wa-color-brand-60\) 4%, var\(--user-card-solid\)\) 40%, color-mix\(in oklab, var\(--wa-color-brand-60\) 15%, var\(--user-card-solid\)\)\)/)
    assert.match(body, /box-shadow: 0 8px 22px -8px color-mix\(in oklab, var\(--wa-color-brand-60\) 45%, transparent\), inset 0 0 18px -6px color-mix\(in oklab, var\(--wa-color-brand-60\) 18%, transparent\);/)
    assert.match(body, /--user-card-solid: var\(--surface-solid, var\(--wa-color-surface-default\)\);/)
})

test('2. dark has a stronger tint and border', () => {
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
