import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p) => readFileSync(join(here, p), 'utf8')
const css = read('session-state-icons.css').replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the "Arch."/"Draft" text tags became icons, one look everywhere.
test('1. archived is the header icon in its yellow, draft a warning pen', () => {
    assert.match(norm(css), /\.session-state-icon--archived \{ color: var\(--wa-color-yellow-80\); \}/)
    assert.match(norm(css), /\.session-state-icon--draft \{ color: var\(--wa-color-warning-60\); \}/)
})

test('2. no text tag for archived or draft remains in the sidebar row, the switcher or the search results', () => {
    for (const f of ['../components/session/list/SessionListItem.vue', '../components/app/SessionSwitcher.vue', '../components/app/SearchOverlay.vue']) {
        const src = read(f)
        assert.doesNotMatch(src, /<wa-tag[^>]*>\s*(Archived|Arch\.|Draft)\s*<\/wa-tag>/, f)
        assert.match(src, /session-state-icon--archived/, f)
    }
})

test('3. the session header marks a draft and a stale session with icons, and tags neither archived nor stale', () => {
    const src = read('../components/session/detail/SessionHeader.vue')
    assert.doesNotMatch(src, /<wa-tag[^>]*>\s*(Archived|Arch\.|Draft|Stale)\s*<\/wa-tag>/)
    assert.doesNotMatch(src, /session-title-tags/)
    assert.match(src, /name="file-pen"[\s\S]{0,80}session-state-icon--draft/)
    assert.match(src, /name="link-slash"[\s\S]{0,120}session-state-icon--stale/)
})

test('3b. stale is the link-slash icon in the sidebar, the switcher, the header and the read-only callout, in the warning colour', () => {
    assert.match(norm(css), /\.session-state-icon--stale \{ color: var\(--wa-color-warning-60\); \}/)
    for (const f of ['../components/session/list/SessionListItem.vue', '../components/app/SessionSwitcher.vue', '../components/session/detail/SessionHeader.vue', '../components/session/detail/SessionItemsList.vue']) {
        assert.match(read(f), /name="link-slash"[\s\S]{0,160}session-state-icon--stale/, f)
    }
})

test('4. the stylesheet is loaded by the app', () => {
    assert.match(read('../main.js'), /import '\.\/styles\/session-state-icons\.css'/)
})

test('5. one ephemeral colour, used by the sidebar, the header and the composer toggle', () => {
    assert.match(norm(css), /--session-ephemeral-color: var\(--wa-color-success-60\);/)
    assert.match(norm(css), /\.session-state-icon--ephemeral \{ color: var\(--session-ephemeral-color\); \}/)
    for (const f of ['../components/session/list/SessionListItem.vue', '../components/session/detail/SessionHeader.vue', '../components/message/MessageInput.vue']) {
        assert.match(read(f), /session-state-icon--ephemeral/, f)
    }
    assert.doesNotMatch(read('../components/session/detail/SessionHeader.vue'), />Ephemeral<\/wa-tag>/)
})
