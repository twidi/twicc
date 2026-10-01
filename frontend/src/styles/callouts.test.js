import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, 'callouts.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: every callout wears the lit gradient with a halo on its icon.
test('1. a callout is an opaque gradient of its variant colour over the surface, with a coloured edge and shadow', () => {
    const m = css.match(/\nwa-callout \{([^}]*)\}/)
    assert.ok(m, 'the base rule')
    const body = norm(m[1])
    assert.match(body, /--callout-c: var\(--wa-color-brand-60\);/)
    assert.match(body, /background-color: transparent;/)
    assert.match(body, /linear-gradient\(100deg, color-mix\(in oklab, var\(--callout-c\) 24%, var\(--surface-solid\)\), color-mix\(in oklab, var\(--callout-c\) 5%, var\(--surface-solid\)\)\)/)
    assert.match(body, /border-color: color-mix\(in oklab, var\(--callout-c\) 45%, transparent\);/)
    assert.match(body, /box-shadow:/)
})

test('1b. the gradient has no transparency, in light or in dark', () => {
    assert.doesNotMatch(css, /linear-gradient[^;]*transparent/)
})

test('2. each variant sets the colour', () => {
    for (const v of ['success', 'warning', 'danger']) {
        assert.match(css, new RegExp(`wa-callout\\[variant='${v}'\\] \\{ --callout-c: var\\(--wa-color-${v}-60\\); \\}`))
    }
})

test('2b. neutral is the neutral grey leaning toward the accent', () => {
    assert.match(norm(css), /wa-callout\[variant='neutral'\] \{ --callout-c: color-mix\(in oklab, var\(--wa-color-neutral-60\) 55%, var\(--wa-color-brand-60\)\); \}/)
})

test('3. the icon has a halo; dark gives a stronger gradient and the plain variant colour', () => {
    assert.match(norm(css), /wa-callout > wa-icon\[slot='icon'\] \{[^}]*filter: drop-shadow\(0 0 5px/)
    assert.match(norm(css), /\.wa-dark wa-callout \{ background-image: linear-gradient\(100deg, color-mix\(in oklab, var\(--callout-c\) 30%/)
    assert.match(norm(css), /\.wa-dark wa-callout > wa-icon\[slot='icon'\] \{ color: var\(--callout-c\); \}/)
})

test('4. no wa-callout sets an appearance any more, and the stylesheet is loaded', () => {
    const walk = (d) => readdirSync(d).flatMap((f) => {
        const p = join(d, f)
        return statSync(p).isDirectory() ? walk(p) : p.endsWith('.vue') ? [p] : []
    })
    for (const f of walk(join(here, '..'))) {
        assert.doesNotMatch(readFileSync(f, 'utf8'), /<wa-callout[^>]*\sappearance=/, f)
    }
    assert.match(readFileSync(join(here, '..', 'main.js'), 'utf8'), /import '\.\/styles\/callouts\.css'/)
})
