import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, 'tags.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: every tag is a glow chip.
test('1. a tag is a flat tint of its variant colour over the opaque surface, with a coloured edge and halo', () => {
    const body = norm(css.match(/\nwa-tag \{([^}]*)\}/)[1])
    assert.match(body, /--tag-c: var\(--wa-color-brand-60\);/)
    assert.match(body, /background-color: color-mix\(in oklab, var\(--tag-c\) 13%, var\(--surface-solid\)\);/)
    assert.match(body, /border-color: color-mix\(in oklab, var\(--tag-c\) 50%, transparent\);/)
    assert.match(body, /box-shadow: 0 0 8px -2px color-mix\(in oklab, var\(--tag-c\) 55%, transparent\);/)
    assert.doesNotMatch(css, /gradient/, 'flat: no gradient')
})

test('2. each variant sets the colour; neutral leans toward the accent', () => {
    for (const v of ['success', 'warning', 'danger']) {
        assert.match(css, new RegExp(`wa-tag\\[variant='${v}'\\] \\{ --tag-c: var\\(--wa-color-${v}-60\\); \\}`))
    }
    assert.match(norm(css), /wa-tag\[variant='neutral'\] \{ --tag-c: color-mix\(in oklab, var\(--wa-color-neutral-60\) 55%, var\(--wa-color-brand-60\)\); \}/)
})

test('3. dark has its own tint and text', () => {
    assert.match(norm(css), /\.wa-dark wa-tag \{ background-color: color-mix\(in oklab, var\(--tag-c\) 20%, var\(--surface-solid\)\);/)
    assert.match(norm(css), /\.wa-dark wa-tag \{[^}]*color: color-mix\(in oklab, var\(--tag-c\) 55%, white\);/)
})

test('4. no wa-tag sets an appearance any more, and the stylesheet is loaded', () => {
    const walk = (d) => readdirSync(d).flatMap((f) => {
        const p = join(d, f)
        return statSync(p).isDirectory() ? walk(p) : p.endsWith('.vue') ? [p] : []
    })
    for (const f of walk(join(here, '..'))) {
        assert.doesNotMatch(readFileSync(f, 'utf8'), /<wa-tag[^>]*\sappearance=/, f)
    }
    assert.match(readFileSync(join(here, '..', 'main.js'), 'utf8'), /import '\.\/styles\/tags\.css'/)
})
