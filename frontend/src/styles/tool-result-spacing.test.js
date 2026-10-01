import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const sfc = readFileSync(join(here, '..', 'components/session/detail/items/ToolUseContent.vue'), 'utf8')
const style = sfc.slice(sfc.indexOf('<style'))

// Visual refresh retouches: a Result card has the same margin below its content as on its sides.
// The details' content part already ends with the card spacing; the content wrapper adds none.
test('the result content has no bottom padding of its own', () => {
    const m = style.match(/\.tool-result-content \{([^}]*)\}/)
    assert.ok(m)
    assert.equal(m[1].replace(/\s+/g, ' ').trim(), 'padding: var(--wa-space-xs) 0 0;')
})
