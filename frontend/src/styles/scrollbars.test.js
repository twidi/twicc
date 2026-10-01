import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh, retouches: thin scrollbars in the app's colours with a visible track (it tells
// that a zone scrolls). WebKit pseudo-elements where they exist, the standard properties
// elsewhere: in Chrome a standard property would switch the pseudo-elements off, so the two
// branches exclude each other.
const css = norm(strip(read('scrollbars.css')))

test('1. three tokens: thumb, hovered thumb, a visible (non-transparent) track', () => {
    const root = css.match(/:root \{([^}]*)\}/)[1]
    assert.match(root, /--scrollbar-thumb: color-mix\(in srgb, color-mix\(in srgb, var\(--wa-color-neutral-50\) 45%, var\(--wa-color-brand-fill-loud\)\) 32%, transparent\);/)
    assert.match(root, /--scrollbar-thumb-hover: color-mix\(in srgb, var\(--wa-color-brand-fill-loud\) 75%, transparent\);/)
    assert.match(root, /--scrollbar-track: color-mix\(in srgb, var\(--wa-color-neutral-50\) 9%, transparent\);/)
})

test('2. the WebKit branch: 8px bar, rounded thumb inset in the track, accent on hover', () => {
    const branch = css.match(/@supports selector\(::-webkit-scrollbar\) \{(.*?)\} @supports not/)[1]
    assert.match(branch, /\*::-webkit-scrollbar[^{]*\{ width: 8px; height: 8px; \}/)
    assert.match(branch, /\*::-webkit-scrollbar-track[^{]*\{ background: var\(--scrollbar-track\); \}/)
    assert.match(branch, /\*::-webkit-scrollbar-thumb[^{]*\{ background-color: var\(--scrollbar-thumb\); background-clip: padding-box; border: 2px solid transparent; border-radius: 999px; \}/)
    assert.match(branch, /\*::-webkit-scrollbar-thumb:hover[^{]*\{ background-color: var\(--scrollbar-thumb-hover\); \}/)
})

test('3. the standard branch only exists where WebKit pseudo-elements do not', () => {
    const branch = css.match(/@supports not selector\(::-webkit-scrollbar\) \{(.*)\}$/)[1]
    assert.match(branch, /\* \{ scrollbar-width: thin; scrollbar-color: var\(--scrollbar-thumb\) var\(--scrollbar-track\); \}/)
    // No standard property outside that branch (Chrome would drop the WebKit styling).
    const outside = css.replace(branch, '')
    assert.doesNotMatch(outside, /scrollbar-(width|color):/)
})

test('4. the scrolling Web Awesome parts are covered in both branches', () => {
    for (const part of ['wa-dialog::part(body)', 'wa-drawer::part(body)', 'wa-select::part(listbox)', 'wa-textarea::part(textarea)']) {
        assert.ok(css.includes(`${part}::-webkit-scrollbar {`) || css.includes(`${part}::-webkit-scrollbar,`), `${part} webkit`)
        assert.match(css, new RegExp(`${part.replace(/[()]/g, '\\$&')}[,{ ]`), part)
    }
})

test('5. imported once in main.js', () => {
    assert.equal(read('../main.js').split("import './styles/scrollbars.css'").length - 1, 1)
})
