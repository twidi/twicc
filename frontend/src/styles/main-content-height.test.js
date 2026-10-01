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
