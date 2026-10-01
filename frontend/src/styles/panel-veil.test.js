import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()
const block = (css, selector) => {
    const m = css.match(new RegExp(`(?:^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([\\s\\S]*?)\\}`))
    assert.ok(m, `block ${selector}`)
    return m[1].split(';').map((d) => d.trim().replace(/\s+/g, ' ')).filter(Boolean)
}

// Visual refresh retouches: the floating cards are a faint veil over the canvas (white in light,
// black in dark) instead of a flat surface, so the canvas gradient shows through, attenuated.
// Everything inside a card that painted `surface-default` paints nothing: the token is transparent
// there. The few places that use it as a colour (a check mark, a ring, a drag ghost) read an
// opaque alias instead.
test('1. the cards are a veil over the canvas, the token is transparent inside them', () => {
    const css = strip(read('surfaces.css'))
    const card = block(css, '.panel-card')
    assert.ok(card.includes('--wa-color-surface-default: transparent'), 'the token is transparent inside a card')
    assert.ok(card.includes('background: var(--panel-veil)'), 'the card paints the veil')
    assert.ok(!card.some((d) => d.startsWith('background:') && d.includes('--wa-color-surface-default')), 'no flat surface left')
    const root = css.slice(0, css.indexOf('.wa-dark {'))
    assert.ok(root.includes('--panel-veil: color-mix(in oklab, white 30%, transparent);'), 'light veil: white 30%, a faint lightening')
    assert.ok(css.slice(css.indexOf('.wa-dark {')).includes('--panel-veil: color-mix(in oklab, black 30%, transparent);'), 'dark veil: black 30%, a faint darkening')
    assert.ok(root.includes('--surface-solid: var(--wa-color-surface-default);'), 'the opaque alias is declared on :root')
})

test('2. the colour uses of the token read the opaque alias (never transparent)', () => {
    const FILES = {
        '../components/message/AgentSettingsMatrix.vue': 3,
        '../components/peer/PeerInboxBadge.vue': 1,
        '../components/session/layout/SessionLayout.vue': 3,
        '../components/message/PendingRequestForm.vue': 1,
    }
    for (const [file, expected] of Object.entries(FILES)) {
        const src = read(file)
        assert.equal((src.match(/var\(--surface-solid(?:, #fff)?\)/g) ?? []).length >= expected, true, `${file}: ${expected} uses of --surface-solid`)
    }
    // The gradient-border fill of the question form needs an opaque fill; its plain background does not.
    const form = read('../components/message/PendingRequestForm.vue')
    assert.ok(form.includes('linear-gradient(var(--surface-solid), var(--surface-solid)) padding-box'), 'gradient border fill is opaque')
    assert.ok(/\.pending-request-form\s*\{[^}]*background: var\(--wa-color-surface-default\)/.test(form), 'its own background follows the card (transparent inside it)')
})

// A card floats above the others in three places (the layout overlay, the two headers' overflow
// panels): they must not show what is behind them. Their background is the page colour without
// its gradient (--canvas-color), with the same veil as the cards: opaque, lightened or darkened
// by the same step. Declared once on :root, so the veil resolves on <html> (light or dark).
test('3. floating cards are opaque: the canvas colour under the same veil', () => {
    const css = strip(read('surfaces.css'))
    const root = css.slice(0, css.indexOf('.wa-dark {'))
    assert.ok(
        root.includes('--panel-solid: linear-gradient(var(--panel-veil), var(--panel-veil)), linear-gradient(var(--canvas-flat), var(--canvas-flat)), var(--canvas-color);'),
        '--panel-solid: the veil over the flat colour over an opaque base (the auras are translucent colours)',
    )
    // The flat colour a card LOOKS like. Light: the canvas colour itself (the auras barely reach the
    // middle). Dark: the auras cover most of the page, so the canvas colour alone is far too dark;
    // measured against the cards' average (20,33,46): 30% of the start aura and 40% of the end aura
    // mixed in, over the opaque canvas colour (21,34,46).
    assert.ok(root.includes('--canvas-flat: var(--canvas-color);'), 'light: the canvas colour')
    assert.ok(
        css.slice(css.indexOf('.wa-dark {')).includes('--canvas-flat: color-mix(in oklab, var(--canvas-aura-start) 30%, color-mix(in oklab, var(--canvas-aura-end) 40%, var(--canvas-color)));'),
        'dark: the canvas colour with a share of both auras',
    )
    // The same in light and in dark: no dark exception (the floating cards are the page colour under the
    // veil, like the cards; only the layout overlay's backdrop is adjusted in dark, test 4).
    assert.ok(!css.slice(css.indexOf('.wa-dark {')).includes('--panel-solid'), 'no dark override of --panel-solid')
    const overlay = strip(read('../components/session/layout/LayoutOverlay.vue'))
    assert.ok(/\.layout-overlay\s*\{[^}]*background: var\(--panel-solid\);/.test(overlay), 'the layout overlay is opaque')
    for (const file of ['../components/session/detail/SessionHeader.vue', '../components/project/ProjectDetailHeader.vue']) {
        const src = strip(read(file))
        assert.ok(/background: var\(--panel-solid\);\s*(?:\/\*[^*]*\*\/\s*)?box-shadow: var\(--panel-overlay-shadow\)/.test(src.replace(/\/\*[\s\S]*?\*\//g, '')), `${file}: the overflow panel is opaque`)
    }
})

// In dark the backdrop behind the layout overlay was black 20% over an almost black page: no visible
// effect. Black 70% dims what is behind, so the overlay's edge reads by contrast, as in light. The
// overlay itself keeps the page colour under the veil (test 3).
test('4. the layout overlay backdrop dims in dark: black 70%', () => {
    const src = strip(read('../components/session/layout/LayoutOverlay.vue'))
    const style = src.slice(src.indexOf('<style'))
    assert.ok(/\.overlay-backdrop\s*\{[^}]*background: rgba\(0, 0, 0, 0\.2\);/.test(style), 'light keeps black 20%')
    // Plain `.wa-dark .overlay-backdrop`: Vue compiles `:global(.wa-dark) .overlay-backdrop` into a bare
    // `.wa-dark { ... }` (the backdrop's selector is lost), which painted <html> instead of the backdrop.
    assert.ok(!style.includes(':global('), 'no :global() form: it drops the backdrop selector')
    const dark = style.match(/\.wa-dark \.overlay-backdrop\s*\{([^}]*)\}/)
    assert.ok(dark, 'a dark rule')
    const decls = dark[1].split(';').map((d) => d.trim().replace(/\s+/g, ' ')).filter(Boolean)
    assert.deepEqual(decls, ['background: rgba(0, 0, 0, 0.7)'])
})

// Inside a card the page surface token is transparent: whatever used it as an OPAQUE backing, to cover
// what is under it (a fullscreen layer, an absolute overlay, a sticky bar), would show that content
// through. They paint --panel-solid, the flat colour of a card, opaque.
test('5. opaque layers inside a card paint --panel-solid, not the transparent token', () => {
    const LAYERS = [
        ['../components/git/GitPanel.vue', '.gitlog-overlay'],
        ['../components/browser/BrowserPane.vue', '.browser-pane--fullscreen'],
        ['../components/files/FilePane.vue', '.file-pane-preview--fullscreen'],
        ['../components/files/FileTreePanel.vue', '.file-tree-panel--mobile > .file-tree-panel-content'],
        ['../components/session/list/SessionSearchBar.vue', '.session-search-bar'],
        ['../components/activity/ContributionGraphs.vue', '.provider-filter'],
    ]
    for (const [file, selector] of LAYERS) {
        const src = strip(read(file))
        const style = src.slice(src.indexOf('<style'))
        const at = style.indexOf(`${selector} {`)
        assert.ok(at >= 0, `${file}: ${selector}`)
        const body = style.slice(at, style.indexOf('}', at))
        assert.ok(/background(?:-color)?: var\(--panel-solid\);/.test(body), `${file} ${selector}: paints --panel-solid`)
        assert.ok(!/background(?:-color)?: var\(--wa-color-surface-default/.test(body), `${file} ${selector}: no transparent token`)
    }
})

// The session search bar floats at the top of the chat, on the card's own colour: a neutral hairline and
// no shadow made it vanish. It reuses what the floating surfaces have: their shadow and the glass border.
test('6. the session search bar is set off: the floating shadow and the glass border', () => {
    const src = strip(read('../components/session/list/SessionSearchBar.vue'))
    const style = src.slice(src.indexOf('<style'))
    const at = style.indexOf('.session-search-bar {')
    const body = style.slice(at, style.indexOf('}', at))
    assert.ok(body.includes('border: var(--divider-size) solid var(--glass-border);'), 'glass border')
    assert.ok(body.includes('border-top: 0;'), 'hangs from the top edge: no top border')
    assert.ok(body.includes('box-shadow: var(--panel-overlay-shadow);'), 'the floating shadow')
})

// Inside a card the page surface token is transparent. Web Awesome's own components that read it as a
// COLOUR, not as a background, would break there: the switch's thumb vanished (a bare track), the
// slider's thumb ring and markers, the colour picker's ring, the ring around a button's badge, the
// button-style radio's fill. They get the opaque value back, on themselves only.
test('7. the Web Awesome controls that read the surface as a colour get it back, opaque', () => {
    const css = strip(read('surfaces.css'))
    const m = css.match(/:where\(\.panel-card\) :is\(([^)]*)\)\s*\{([^}]*)\}/)
    assert.ok(m, 'the restoring rule')
    assert.deepEqual(m[1].split(',').map((s) => s.trim()), ['wa-switch', 'wa-slider', 'wa-radio', 'wa-color-picker', 'wa-button'])
    assert.equal(norm(m[2]), '--wa-color-surface-default: var(--surface-solid);')
    // After the card rule that makes the token transparent.
    assert.ok(css.indexOf(m[0]) > css.indexOf('--wa-color-surface-default: transparent'), 'after the transparent declaration')
})
