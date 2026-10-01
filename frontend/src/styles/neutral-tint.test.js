import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

// Visual refresh, retouches: the light greys of the Web Awesome neutral scale lean to the accent
// hue (cyan), so every flat grey background of the app (anything on --wa-color-surface-lowered,
// --wa-color-neutral-fill-*, ...) follows without being touched one by one.
test('1. the light neutral steps are tinted: one :root rule, accent hue, original lightness', () => {
    const css = strip(read('neutral-tint.css'))
    const block = css.match(/:root\s*\{([\s\S]*?)\}/)
    assert.ok(block, 'one :root block')
    const decls = block[1].split(';').map((d) => d.trim().replace(/\s+/g, ' ')).filter(Boolean)
    assert.deepEqual(decls, [
        '--neutral-tint-hue: 217',
        '--wa-color-neutral-95: oklch(96.07% 0.011 var(--neutral-tint-hue))',
        '--wa-color-neutral-90: oklch(92.23% 0.015 var(--neutral-tint-hue))',
        '--wa-color-neutral-80: oklch(83.64% 0.019 var(--neutral-tint-hue))',
        '--wa-color-neutral-70: oklch(75.18% 0.021 var(--neutral-tint-hue))',
        '--wa-color-neutral-60: oklch(66.86% 0.022 var(--neutral-tint-hue))',
        // The pressed state of every button (Web Awesome mixes its fill with this): a tinted
        // near-black instead of pure black, so the press is not a flat grey.
        '--wa-color-mix-active: oklch(14% 0.05 var(--neutral-tint-hue)) 20%',
    ])
    // The dark steps (50 to 05) are untouched: already tinted blue by the theme.
    assert.ok(!/neutral-(50|40|30|20|10|05)\b[^;]*:/.test(css.replace(/--wa-color-surface-lowered:[^;]*;/, '')), 'the dark steps stay as Web Awesome has them')
})

// The dark "lowered" surface was a near-black (surface-default mixed 20% with black); it now takes
// the colour of the neutral callouts' fill (neutral-fill-quiet in dark = neutral-10), a touch lighter
// than the page. The consumers keep the token: the extra keys bar, the upload strip, the kbd hint...
test('1b. in the dark scheme --wa-color-surface-lowered is neutral-10', () => {
    const css = strip(read('neutral-tint.css'))
    const dark = css.match(/\.wa-dark\s*\{([\s\S]*?)\}/)
    assert.ok(dark, 'one .wa-dark block')
    assert.equal(
        dark[1].replace(/\s+/g, ' ').trim(),
        '--wa-color-surface-lowered: var(--wa-color-neutral-10); --wa-color-mix-active: oklch(8% 0.03 var(--neutral-tint-hue)) 16%;',
    )
    const bar = strip(read('../components/terminal/TerminalExtraKeysBar.vue'))
    assert.ok(bar.includes('background: var(--wa-color-surface-lowered);'), 'the keys bar keeps the token')
})

test('2. the light canvas the user tuned keeps the original gray-95, whatever the neutral scale does', () => {
    const surfaces = strip(read('surfaces.css'))
    assert.ok(surfaces.includes('--canvas-color: color-mix(in oklab, var(--wa-color-brand-95) 30%, var(--wa-color-gray-95));'))
    assert.ok(!surfaces.includes('var(--wa-color-neutral-95)'), 'no neutral-95 left in the canvas')
})

test('3. the SPA imports it after the Web Awesome theme', () => {
    const main = read('../main.js')
    const theme = main.indexOf("themes/default.css'")
    const tint = main.indexOf("./styles/neutral-tint.css")
    assert.ok(theme !== -1 && tint > theme, 'imported after the theme')
})
