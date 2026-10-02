import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

// Retouches: one "Shared with TwiCC" footer for every public share page (session, document, HTML artifact):
// the same height as the session page's header, the full robot logo, the logo and the whole sentence one link.
const footer = read('share-session/ShareFooter.vue')

test('1. one link around the logo (32px: the full robot) and the whole sentence', () => {
    assert.ok(footer.includes('<BrandLogo :size="32" /> <span>Shared with <u>TwiCC</u></span>'))
    assert.ok(footer.includes('<a class="share-footer-link" href="https://github.com/twidi/twicc" target="_blank" rel="noopener noreferrer">'))
    assert.ok(footer.includes('<slot />'))
})

test('2. a bar as tall as the header, the page surface behind it only when asked', () => {
    const css = norm(footer.slice(footer.indexOf('<style')))
    assert.ok(css.includes('.share-footer { flex: 0 0 auto; display: flex; align-items: center; justify-content: center; min-height: 3rem; position: relative; }'))
    assert.ok(css.includes('.share-footer--solid { background: var(--wa-color-surface-default); }'))
    assert.ok(css.includes('font-size: var(--wa-font-size-m)'))
    assert.ok(read('share-session/ShareSessionApp.vue').includes('--share-bar-height: 3rem'))
})

test('3. the three share pages use it: glass on the session page, solid on the document and the artifact pages', () => {
    assert.ok(read('share-session/ShareSessionApp.vue').includes('<ShareFooter class="glass-sticky" />'))
    assert.ok(read('share-session/ShareDocApp.vue').includes('<ShareFooter solid>'))
    assert.ok(read('share-session/ShareDocApp.vue').includes('<ShareThemeToggle />'))
    assert.ok(read('artifact-shell/ArtifactShellApp.vue').includes(`<ShareFooter v-if="mode === 'share'" solid />`))
    assert.ok(!read('artifact-shell/ArtifactShellApp.vue').includes('.share-footer {'), 'no restated footer style')
})
