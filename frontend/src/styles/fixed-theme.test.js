// Guard of the fixed theme and accent (visual refresh, docs/plans/2026-09-28-fixed-theme-accent-design.md).
// The Web Awesome theme is always `default` and the accent always `cyan`: no code, CSS branch or
// comment may bring back another theme, palette or accent, nor the user choice of them.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const srcDir = join(here, '..')

/** Each pattern names a removed theme, palette, accent, or the machinery of the removed choice.
 *  `watheme`/`wabrand` are case-insensitive (getWaTheme, setWaBrand, onWaThemeChange...); the
 *  hyphen of `wa-theme-default` keeps it out of them. */
const FORBIDDEN = [
    /\bshoelace\b/i,
    /(?<!web )awesome theme/i,
    /wa-theme-(?!default)/,
    /wa-palette-(?!default)/,
    /wa-brand-(?!cyan)/,
    'dataset.theme',
    'data-theme',
    'THEME_TO_PALETTE',
    /watheme/i,
    /wabrand/i,
    'WA_THEME',
    'WA_BRAND',
    'themes/awesome.css',
    'themes/shoelace.css',
]

const matches = (pattern, text) => (typeof pattern === 'string' ? text.includes(pattern) : pattern.test(text))

/** Legitimate lines that must stay allowed. */
const NEGATIVE_CONTROLS = [
    'depth.css (--depth-3) and the Web Awesome theme tokens.',
    '// CodeMirror search panel overrides (Web Awesome themed)',
    '— Web Awesome themed overrides',
    '@awesome.me/webawesome',
    'Font Awesome',
    'wa-theme-default',
    'wa-brand-cyan',
]

function* walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name)
        if (entry.isDirectory()) yield* walk(path)
        else if (!entry.name.endsWith('.test.js')) yield path
    }
}

test('no pattern matches a legitimate line', () => {
    for (const line of NEGATIVE_CONTROLS) {
        const hits = FORBIDDEN.filter(pattern => matches(pattern, line)).map(String)
        assert.deepEqual(hits, [], `"${line}" is legitimate`)
    }
})

test('no source file refers to another theme, palette or accent, or to their choice', () => {
    const offenders = []
    for (const path of walk(srcDir)) {
        const lines = readFileSync(path, 'utf8').split('\n')
        lines.forEach((line, index) => {
            for (const pattern of FORBIDDEN) {
                if (matches(pattern, line)) {
                    offenders.push(`${relative(srcDir, path)}:${index + 1} ${String(pattern)}: ${line.trim()}`)
                }
            }
        })
    }
    assert.deepEqual(offenders, [])
})

test('utils/theme.js applies the three fixed classes', () => {
    const theme = readFileSync(join(srcDir, 'utils/theme.js'), 'utf8')
    for (const cls of ['wa-theme-default', 'wa-palette-default', 'wa-brand-cyan']) {
        assert.ok(theme.includes(`'${cls}'`), `utils/theme.js applies ${cls}`)
    }
})
