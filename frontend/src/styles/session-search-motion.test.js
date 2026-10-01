import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')
const norm = (s) => s.replace(/\s+/g, ' ').trim()

// Visual refresh retouches: the in-session search bar (Ctrl+F) slides down from the top edge of the
// chat when it opens and back up when it closes. What is above the chat is never drawn: the chat's
// container (.session-items-list) clips at its top edge (overflow: hidden), so a half-way bar shows
// only its lower half. The movement is multiplied by --motion-amount (reduced motion keeps the fade).
test('1. the bar is wrapped in a Transition and its slide is pinned', () => {
    const list = strip(read('../components/session/detail/SessionItemsList.vue'))
    assert.ok(
        /<Transition name="session-search">\s*<SessionSearchBar\s+v-if="showSessionSearch"/.test(list),
        'SessionSearchBar sits in <Transition name="session-search">',
    )
    // The clip that makes "only the part inside the chat" true: the container hides what leaves it.
    const container = list.slice(list.indexOf('<style')).match(/\n\.session-items-list \{([^}]*)\}/)
    assert.ok(container && /overflow: hidden;/.test(container[1]), 'the chat container clips its overflow')

    const bar = strip(read('../components/session/list/SessionSearchBar.vue'))
    const style = bar.slice(bar.indexOf('<style'))
    const rule = (selectors) => {
        const m = style.match(new RegExp(`${selectors.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`))
        assert.ok(m, selectors)
        return norm(m[1])
    }
    assert.equal(
        rule('.session-search-enter-active'),
        'transition: translate var(--motion-dur-3) var(--motion-ease-out), opacity var(--motion-dur-2) ease-in-out;',
    )
    assert.equal(
        rule('.session-search-leave-active'),
        'transition: translate var(--motion-dur-2) var(--motion-ease), opacity var(--motion-dur-1) ease-in-out;',
    )
    assert.equal(
        rule('.session-search-enter-from,\n.session-search-leave-to'),
        'opacity: 0; translate: -50% calc(-100% * var(--motion-amount));',
    )
    assert.ok(!/\b\d+ms\b/.test(style.slice(style.indexOf('.session-search-enter-active'))), 'no literal duration')
})
