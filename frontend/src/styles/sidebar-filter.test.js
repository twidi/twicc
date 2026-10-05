import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, 'sidebar-rows.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()
const rule = (selector) => {
    const m = css.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\:]/g, '\\$&')}\\s*\\{([^}]*)\\}`))
    assert.ok(m, selector)
    return norm(m[1])
}

// Visual refresh retouches: the filter field of the sidebar header (sessions and artifacts, which share
// .sidebar-header-row) uses brand border and text colour tokens, like the adjacent options dropdown.
// Both schemes follow the tokens.
test('1. the sidebar filter field wears the brand border and text colours of the header buttons', () => {
    assert.equal(rule(':where(.sidebar-header-row .session-search)::part(base)'), 'border-color: var(--wa-color-brand-border-loud);')
    assert.equal(rule(':where(.sidebar-header-row .session-search)::part(input)'), 'color: var(--wa-color-brand-on-quiet);')
    assert.equal(
        rule(':where(.sidebar-header-row .session-search)::part(input)::placeholder'),
        'color: color-mix(in oklab, var(--wa-color-brand-on-quiet) 65%, transparent);',
    )
    assert.equal(rule(':where(.sidebar-header-row .session-search) wa-icon'), 'color: var(--wa-color-brand-on-quiet);')
})
