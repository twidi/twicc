import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()
const sfc = read('share-session/SharedSubagentView.vue')
const css = norm(sfc.slice(sfc.indexOf('<style>')))

// Retouches: on the public share page a sub-agent opens from the right, over 90% of the width, on the
// page's own background.
test('1. the panel takes 90% of the width, on the canvas fixed to the viewport (the page\'s background), the head transparent', () => {
    const panel = css.match(/\.subagent-panel \{([^}]*)\}/)[1]
    for (const decl of ['position: absolute;', 'top: 0;', 'right: 0;', 'bottom: 0;', 'width: 90%;', 'background: var(--canvas-background);', 'background-attachment: fixed;']) {
        assert.ok(panel.includes(decl), decl)
    }
    assert.doesNotMatch(panel, /surface-default|min\(52rem/)
    assert.doesNotMatch(css.match(/\.subagent-head \{([^}]*)\}/)[1], /background/)
})

test('2. it slides in from and out to the right edge (following --motion-amount), and the veil fades with it', () => {
    assert.ok(css.includes("@property --glass-veil-opacity { syntax: '<number>'; inherits: true; initial-value: 1; }"))
    assert.ok(css.includes('.subagent-drawer { transition: --glass-veil-opacity 300ms ease-in-out; }'))
    assert.ok(css.includes('.subagent-drawer-enter-from, .subagent-drawer-leave-to { --glass-veil-opacity: 0; }'))
    assert.ok(css.includes('.subagent-drawer-enter-from .subagent-panel, .subagent-drawer-leave-to .subagent-panel { translate: calc(100% * var(--motion-amount, 1)) 0; }'))
    assert.ok(css.includes('.subagent-drawer-enter-active .subagent-panel { transition: translate 320ms var(--motion-ease-out, ease-out); }'))
    assert.ok(css.includes('.subagent-drawer-leave-active .subagent-panel { transition: translate 240ms ease-in; }'))
})

test('3. the page wraps the drawer in the transition it names', () => {
    const app = read('share-session/ShareSessionApp.vue')
    assert.ok(norm(app).includes('<Transition name="subagent-drawer"> <SharedSubagentView v-if="ready && subagentStack.length"'))
})
