import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

// Visual refresh, retouches: the whole Web Awesome neutral scale (light and dark steps) leans to the
// accent hue, so every flat grey background of the app (anything on --wa-color-surface-lowered,
// --wa-color-neutral-fill-*, ...) follows without being touched one by one.
test('1. the neutral steps are tinted: one :root rule, accent hue, original lightness', () => {
    const css = strip(read('neutral-tint.css'))
    const block = css.match(/:root\s*\{([\s\S]*?)\}/)
    assert.ok(block, 'one :root block')
    const decls = block[1].split(';').map((d) => d.trim().replace(/\s+/g, ' ')).filter(Boolean)
    assert.deepEqual(decls, [
        '--neutral-tint-hue: 217',
        '--wa-color-neutral-95: oklch(96.07% 0.022 var(--neutral-tint-hue))',
        '--wa-color-neutral-90: oklch(92.23% 0.03 var(--neutral-tint-hue))',
        '--wa-color-neutral-80: oklch(83.64% 0.038 var(--neutral-tint-hue))',
        '--wa-color-neutral-70: oklch(75.18% 0.042 var(--neutral-tint-hue))',
        '--wa-color-neutral-60: oklch(66.86% 0.044 var(--neutral-tint-hue))',
        '--wa-color-neutral-50: oklch(56.42% 0.045 var(--neutral-tint-hue))',
        '--wa-color-neutral-40: oklch(46.28% 0.045 var(--neutral-tint-hue))',
        '--wa-color-neutral-30: oklch(39.36% 0.045 var(--neutral-tint-hue))',
        '--wa-color-neutral-20: oklch(31.97% 0.043 var(--neutral-tint-hue))',
        '--wa-color-neutral-10: oklch(23.28% 0.037 var(--neutral-tint-hue))',
        '--wa-color-neutral-05: oklch(18.34% 0.03 var(--neutral-tint-hue))',
        // Dividers and the empty track of progress bars and rings: the same greys with less chroma.
        '--divider-color: oklch(from var(--wa-color-surface-border) l calc(c * 0.5) h)',
        '--progress-track: oklch(from var(--wa-color-neutral-fill-normal) l calc(c * 0.4) h)',
        // The pressed state of every button (Web Awesome mixes its fill with this): a tinted
        // near-black instead of pure black, so the press is not a flat grey.
        '--wa-color-mix-active: oklch(14% 0.05 var(--neutral-tint-hue)) 20%',
    ])
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
        '--progress-track: oklch(from var(--wa-color-neutral-border-normal) l calc(c * 0.8) h); --wa-color-surface-lowered: var(--wa-color-neutral-10); --wa-color-mix-active: oklch(8% 0.03 var(--neutral-tint-hue)) 16%;',
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

test('4. dividers and progress tracks read the softer tokens, in every place that draws them', () => {
    const css = strip(read('neutral-tint.css'))
    assert.match(css, /wa-divider \{\s*--color: var\(--divider-color\);\s*\}/)
    assert.match(css, /wa-progress-bar,\s*wa-progress-ring \{\s*--track-color: var\(--progress-track\);\s*\}/)
    for (const f of ['../views/ProjectView.vue', '../components/tasks/TaskPane.vue', '../components/files/UploadStrip.vue']) {
        const s = strip(read(f))
        assert.ok(s.includes('var(--progress-track)'), `${f}: reads the track token`)
    }
})

// Retouches: the fields are tinted with the accent, not a pure white or a near-black.
test('field fill: a faint accent tint over the page surface (a little stronger in dark), read by Web Awesome and by the glass', () => {
    const css = strip(read('neutral-tint.css')).replace(/\s+/g, ' ')
    assert.ok(css.includes(':root { --field-bg: color-mix(in oklab, var(--wa-color-brand-60) 5%, var(--wa-color-surface-default)); --wa-form-control-background-color: var(--field-bg); }'))
    assert.ok(css.includes('.wa-dark { --field-bg: color-mix(in oklab, var(--wa-color-brand-60) 11%, var(--wa-color-surface-default)); }'))
    const glass = strip(read('glass.css')).replace(/\s+/g, ' ')
    assert.ok(glass.includes('--glass-field-bg: color-mix(in oklab, var(--field-bg, var(--wa-color-surface-default)) 70%, transparent);'))
})
