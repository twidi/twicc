import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const gutter = readFileSync(join(here, 'DockGutter.vue'), 'utf8')
const layout = readFileSync(join(here, 'SessionLayout.vue'), 'utf8')
const collapse = (s) => s.replace(/\s+/g, ' ')

// An edge can hold two docks (right-top and right-bottom, say): the overlay of that edge shows the tabs
// of both, and only ONE of them is the tab the route is on. Each dock keeps its own active tab, so both
// of their active chips read as "open": two chips pressed while the overlay shows a single tab. A chip is
// open only when its dock holds the tab the overlay is actually showing.
test('1. a chip is open only if its dock holds the tab the open overlay shows', () => {
    assert.ok(/openOverlayTabId:\s*\{\s*type:\s*String,\s*default:\s*null\s*\}/.test(gutter), 'prop openOverlayTabId')
    const fn = gutter.slice(gutter.indexOf('function isOpen(entry)'))
    const body = collapse(fn.slice(0, fn.indexOf('\n}')))
    assert.ok(body.includes("entry.item.action === 'overlay'"), 'an overlay chip')
    assert.ok(body.includes('props.openOverlayEdge === props.gutter.edge'), 'its edge is the open one')
    assert.ok(body.includes('entry.active'), 'the dock\'s active chip')
    assert.ok(body.includes('entry.item.tabs.some((t) => t.id === props.openOverlayTabId)'), 'the dock holds the tab the overlay shows')
})

test('2. SessionLayout hands the tab the overlay shows to every gutter', () => {
    assert.ok(collapse(layout).includes(':open-overlay-edge="openOverlayEdge" :open-overlay-tab-id="overlayActive"'), 'binding on DockGutter')
})
