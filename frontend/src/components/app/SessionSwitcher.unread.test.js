import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const sfc = readFileSync(new URL('./SessionSwitcher.vue', import.meta.url), 'utf8')
const template = sfc.match(/<template>([\s\S]*)<\/template>/)[1]
const style = sfc.match(/<style scoped>([\s\S]*?)<\/style>/)[1]

test('unread row with a process renders ProcessIndicator with unread', () => {
    const match = template.match(/<ProcessIndicator\s+v-(?:else-)?if="row\.state\.kind === 'unread' && row\.state\.processState"[\s\S]*?\/>/)
    assert.ok(match, 'ProcessIndicator rendered for unread + processState')
    assert.match(match[0], /\bunread\b(?!-)/)
    assert.match(match[0], /:state="row\.state\.processState\.state"/)
})

test('the hand-drawn eye remains only for unread without processState', () => {
    assert.match(template, /<wa-icon\s+v-else-if="row\.state\.kind === 'unread'"\s+name="eye" class="switcher-unread"/)
    assert.equal((template.match(/name="eye"/g) || []).length, 1)
})

test('the switcher indicator keeps the brand eye colour', () => {
    assert.match(style, /--process-unread-color:\s*var\(--wa-color-brand-60\)/)
})
