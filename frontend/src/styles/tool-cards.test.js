import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const css = read('tool-cards.css').replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the collapsible rows of the chat wear the quote card, in the brand colour.
test('1. a tool card is the opaque gradient card in the brand colour, colours only', () => {
    assert.match(norm(css), /wa-details\.item-details \{ --tool-c: var\(--wa-color-brand-60\); \}/)
    const m = norm(css.match(/\nwa-details\.item-details::part\(base\) \{([^}]*)\}/)[1])
    assert.match(m, /border-color: color-mix\(in oklab, var\(--tool-c\) 38%, transparent\);/)
    assert.match(m, /background-color: transparent;/)
    assert.match(m, /linear-gradient\(100deg, color-mix\(in oklab, var\(--tool-c\) 17%, var\(--surface-solid\)\), color-mix\(in oklab, var\(--tool-c\) 2%, var\(--surface-solid\)\)\)/)
    assert.doesNotMatch(m, /box-shadow|border-radius|border-width/, 'the shape, shadow and joining stay with SessionItem.vue')
    assert.doesNotMatch(css, /linear-gradient[^;]*transparent\)[^;]*\);/, 'opaque')
})

test('2. dark has its own gradient', () => {
    assert.match(norm(css), /\.wa-dark wa-details\.item-details::part\(base\) \{ background-image: linear-gradient\(100deg, color-mix\(in oklab, var\(--tool-c\) 26%, var\(--surface-solid\)\), color-mix\(in oklab, var\(--tool-c\) 6%/)
})

test('3. the app imports it', () => {
    assert.match(read('../main.js'), /import '\.\/styles\/tool-cards\.css'/)
})

test('4. the Result disclosure inside a tool card takes the card edge colour', () => {
    assert.match(norm(css), /wa-details\.tool-result::part\(base\) \{ border-color: color-mix\(in oklab, var\(--tool-c\) 38%, transparent\); \}/)
})
