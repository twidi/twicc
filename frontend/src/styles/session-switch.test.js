import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

test('1. a session switch fades like a tab change: the old image stays opaque, the new one fades in over 250ms', () => {
    const css = norm(read('styles/motion.css'))
    assert.ok(css.includes('html.twicc-vt-tab::view-transition-old(root), html.twicc-vt-session::view-transition-old(root), html.twicc-vt-overlay::view-transition-old(root) { animation: none; mix-blend-mode: normal; }'))
    assert.ok(css.includes('html.twicc-vt-tab::view-transition-new(root), html.twicc-vt-session::view-transition-new(root), html.twicc-vt-overlay::view-transition-new(root) { animation: twicc-vt-fade-in 250ms ease-in-out both; mix-blend-mode: normal; }'))
})

test('2. installed once on the router at startup, a draft swapped for its real session excluded', () => {
    const main = read('main.js')
    assert.ok(main.includes("import { installSessionSwitchTransition } from './utils/sessionSwitchTransition'"))
    assert.ok(main.includes("installSessionSwitchTransition(router, { isDraft: (id) => !!useDataStore().sessions[id]?.draft })"))
    assert.ok(main.indexOf('app.use(router)') < main.indexOf('installSessionSwitchTransition(router'))
})

test('3. the transition kind is the one the stylesheet names', () => {
    assert.ok(read('utils/sessionSwitchTransition.js').includes("{ kind: 'session', settle: true, updateTimeoutMs: 800 }"))
})
