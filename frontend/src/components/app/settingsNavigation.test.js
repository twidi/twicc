import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./SettingsPopover.vue', import.meta.url), 'utf8')

test('orders settings from general controls through advanced peer settings', () => {
    const expected = [
        "id: 'general'",
        "id: 'notifications'",
        "id: 'providers'",
        '...providerSections.value.filter(s => s.enabled)',
        "id: 'sessions'",
        "id: 'layouts'",
        "id: 'title'",
        "id: 'editor'",
        "id: 'terminal'",
        "id: 'sharing'",
        "id: 'usage'",
        "id: 'peers'",
    ]

    let previous = -1
    for (const marker of expected) {
        const index = source.indexOf(marker)
        assert.ok(index > previous, `${marker} must follow the preceding navigation entry`)
        previous = index
    }
})

test('shows a semantic divider before the auxiliary entries', () => {
    assert.match(source, /<wa-divider class="settings-nav-divider"><\/wa-divider>/)
    assert.doesNotMatch(source, /shortcuts-nav-divider/)
    assert.doesNotMatch(source, /hasUtilitySections/)
})

// A touch device can have a keyboard plugged in at any moment, so the cheat
// sheet is never gated on the touch flag — neither in the template nor in CSS.
test('keeps the Shortcuts entry reachable on touch devices', () => {
    assert.doesNotMatch(source, /\.shortcuts-nav-item\s*\{\s*display:\s*none/)
    const shortcutsButton = source.match(/<button[^>]*shortcuts-nav-item[^>]*>/)
    assert.ok(shortcutsButton, 'the Shortcuts nav entry must exist')
    assert.doesNotMatch(shortcutsButton[0], /v-if/)
})
