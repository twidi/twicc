// Run with: node --test src/styles/reduce-effects.test.js (from the frontend dir)
// "Reduce effects" (utils/reducedMotion.js) drops the costly filters. Every `filter: drop-shadow(...)`
// of the app has a `:root.reduce-effects` rule that removes it.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(srcDir, rel), 'utf8')
const collapse = (text) => text.replace(/\s+/g, ' ')

const RULES = [
    ['styles/glow.css', ":root.reduce-effects :where(wa-progress-ring:is(.context-usage-ring, .onode-context-ring))::part(base) { filter: none; }"],
    ['components/ui/TabBar.vue', ':root.reduce-effects .tab-bar::part(tabs)::after { filter: none; }'],
    ['components/session/layout/SessionLayout.vue', ':root.reduce-effects .layout-tab-drag-ghost { filter: none; box-shadow: 0 5px 12px rgba(0, 0, 0, 0.25); }'],
    ['components/activity/ContributionSparklines.vue', ':root.reduce-effects .sparkline-line { filter: none; }'],
    ['styles/callouts.css', ":root.reduce-effects wa-callout > wa-icon[slot='icon'] { filter: none; }"],
]

test('each drop-shadow glow has a "Reduce effects" rule that removes it', () => {
    for (const [file, rule] of RULES) assert.ok(collapse(read(file)).includes(rule), `${file}: ${rule}`)
})

test('no other file draws a drop-shadow filter', () => {
    const withShadow = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(css|vue)$/.test(e.name))
        .map((e) => relative(srcDir, join(e.parentPath ?? e.path, e.name)))
        .filter((file) => /filter:\s*drop-shadow\(/.test(read(file)))
    assert.deepEqual(withShadow.sort(), RULES.map(([file]) => file).sort(), 'a new drop-shadow needs its :root.reduce-effects rule (and an entry here)')
})
