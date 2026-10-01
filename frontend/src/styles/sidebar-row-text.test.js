import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, 'sidebar-rows.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

// Visual refresh retouches: a sidebar row is a plain neutral wa-button, whose text is Web Awesome's
// "on quiet" colour: neutral-60 in dark, a grey at 67% lightness that reads faded. In dark the rows
// take the normal text colour (96%). One rule of the shared sheet: the session and the artifacts
// lists both follow. Zero specificity: the open row keeps its brand colour (rules above it win).
test('1. in dark, the sidebar rows read in the normal text colour', () => {
    const m = css.match(/:where\(\.wa-dark \.sidebar-row\)::part\(base\)\s*\{([^}]*)\}/)
    assert.ok(m, 'the dark row text rule')
    assert.equal(m[1].replace(/\s+/g, ' ').trim(), 'color: color-mix(in oklab, var(--wa-color-text-normal) 40%, var(--wa-color-neutral-60));')
})
