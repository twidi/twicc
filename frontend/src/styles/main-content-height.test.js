import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../views/ProjectView.vue'), 'utf8')

// The project, workspace and artifacts pages have a card (.project-detail-content, .artifacts-browser-content)
// whose content scrolls inside it: the page itself must not. .main-content is the item of the split panel's
// grid that holds the card; its automatic minimum height (the height of its content) let the whole page
// grow to the content and scroll, sidebar included. It is not a scroll container (overflow: clip), so the
// automatic minimum applies: min-height: 0 gives it the height of the window.
test('1. .main-content may be shorter than its content (the card scrolls, not the page)', () => {
    const style = view.slice(view.indexOf('<style'))
    const m = style.match(/\n\.main-content \{([^}]*)\}/)
    assert.ok(m, 'the .main-content rule')
    assert.ok(/\n\s*min-height: 0;/.test(m[1]), 'min-height: 0')
    assert.ok(/\n\s*height: 100%;/.test(m[1]), 'height: 100% stays')
})

test('mobile sidebar drawer: no slide under reduced motion, it fades instead', () => {
    const block = view.match(/@media \(width < 640px\) \{\s*:root\.reduce-motion \.sidebar \{([\s\S]*?)\n\}\n/)
    assert.ok(block, 'a mobile block with the reduced-motion rules')
    const css = block[0]
    assert.match(css, /:root\.reduce-motion \.sidebar \{[^}]*opacity: 0;[^}]*visibility: hidden;[^}]*transition: opacity/)
    assert.match(css, /:root\.reduce-motion \.sidebar-toggle \{\s*transition: none;/)
    assert.match(css, /:checked\) \.sidebar \{[^}]*opacity: 1;[^}]*visibility: visible;/)
    assert.ok(!/translate|transform/.test(css), 'the reduced-motion rules move nothing')
    assert.ok(!/prefers-reduced-motion/.test(view), 'no prefers-reduced-motion media query: the class carries it')
})

// The project, workspace and all-projects page has the session view's shape: a header on the canvas,
// and a card (the tab bar) below it. The card is not the whole panel.
test('project page: header on the canvas, the card is the tab bar', () => {
    const panel = readFileSync(join(here, '../components/project/ProjectDetailPanel.vue'), 'utf8')
    assert.match(panel, /<TabBar[^>]*class="detail-tabs panel-card"/)
    assert.match(panel, /\.project-detail-panel > wa-divider \{\s*visibility: hidden;/)
    assert.match(panel, /\.project-detail-panel > \.detail-header\.compact-collapsed \{\s*border-bottom-color: transparent;/)
    assert.match(view, /<div v-show="!isArtifactsMode && !sessionId" class="project-detail-content">/)
    assert.match(view, /\.project-detail-content \{\s*overflow: clip;\s*overflow-clip-margin: var\(--panel-gap\);/)
})
